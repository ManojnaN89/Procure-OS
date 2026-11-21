"""A change in the ERP's demand, applied (rule Q21): the ERP sends a row it sent before with other values, or cancelled.

`classify` says what a re-sent row changes (or why it is left out). An `Applier` writes the change and its effects on
the one material request line of each requirement it touches:

- less needed (or cancelled): what the line holds beyond the new need is taken first off purchase request lines not yet
  with the buyer, then off the stock set aside; what is still over, up to what the buyer already has on an order or a
  quotation request, becomes a case for the buyer, and beyond that it can only be stock issued: a case for the store;
- more needed, or needed earlier: the store check runs again for that item and place once the import is done (a
  submitted request never grows); request lines not yet with the buyer take the earlier date, and an order promised
  later becomes a case for the buyer;
- needed later: only the dates move.

The stock released goes at once to the open lines that need it (`reservation.stock_rose`), earliest need-by first. A
header change of a production order moves its lines' dates or cancels its lines; its quantity alone changes no line (the
ERP explodes the bill of materials). The manual Cancel on the Material requests screen uses the same rules.
"""

import json
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.formulas import demand_change as rules
from p2p.formulas.money import format_qty, round_money
from p2p.platform import audit
from p2p.platform.sod import Actor
from p2p.requests import demand_cases, reservation
from p2p.requests.combining import LIVE_REQUEST
from p2p.requests.demand_files import DemandFile, Reference, status_of

Place = tuple[str, str]
ZERO = Decimal(0)
NEXT_STEP = {
    "po": "Revise or cancel the order, or keep the goods for stock; then close the case",
    "rfq": "Reduce or cancel the RFQ line, or keep the goods for stock; then close the case",
    "request": "Reduce or cancel the request line before it is ordered, or keep it for stock; then close the case",
    "material_request": "Return the extra to stock or keep it for production; then close the case",
}


def _state(rec: dict[str, Any]) -> str:
    """As the ERP sees a stored row: cancelled, or open (an issued line is still live demand)."""
    return "cancelled" if rec.get("status") == "cancelled" else "open"


def _show(value: Any) -> str:
    if isinstance(value, Decimal):
        return format_qty(value)
    if isinstance(value, date):
        return value.isoformat()
    return "(blank)" if value is None else str(value)


@dataclass
class Change:
    """One re-sent row with what it changes; the effects are filled in as it is applied."""

    kind: str
    doc_no: str
    line_key: str | None
    item_code: str | None
    location_code: str | None
    what: list[str]
    before: dict[str, Any]
    after: dict[str, Any]
    label: str  # the row in words: MO-1, MO-1 RM-08, MRP-2027-01 line 3
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = field(default_factory=list)  # (stored, sent)
    requirement_id: int | None = None
    id: int | None = None
    import_id: int | None = None
    mr_no: str | None = None
    released: dict[str, Decimal] = field(default_factory=dict)
    requests_cut: list[dict[str, Any]] = field(default_factory=list)
    requests_cancelled: list[str] = field(default_factory=list)
    need_by_moved: list[dict[str, Any]] = field(default_factory=list)
    cases: list[dict[str, Any]] = field(default_factory=list)
    store_check: bool = False
    applied_at: str | None = None

    def effects(self) -> dict[str, Any]:
        return {
            "released": {k: format_qty(v) for k, v in self.released.items()},
            "requests_cut": self.requests_cut,
            "requests_cancelled": self.requests_cancelled,
            "need_by_moved": self.need_by_moved,
            "cases": self.cases,
            "store_check": self.store_check,
        }

    def view(self) -> dict[str, Any]:
        """As the import answer and /api/demand/changes show it."""
        return {
            "id": self.id, "import_id": self.import_id, "kind": self.kind, "doc_no": self.doc_no,
            "line_key": self.line_key, "item_code": self.item_code, "location_code": self.location_code,
            "what": self.what, "before": self.before, "after": self.after, **self.effects(),
            "applied_at": self.applied_at,
        }  # fmt: skip

    def words(self) -> str:
        """What happened, for a case title: "MO-1 cancelled in the ERP", "MO-1 cut to 20"."""
        if "cancelled" in self.what:
            return f"{self.label} cancelled " + ("on Material requests" if self.kind == "manual" else "in the ERP")
        if "qty_down" in self.what:
            return f"{self.doc_no} cut to {self.after.get('qty')}"  # the spec's title: "MO-1 cut to 20: ..."
        if "earlier" in self.what:
            return f"{self.label} needed by {date.fromisoformat(self.after['need_by']):%d %b} in the ERP"
        return f"{self.label} changed in the ERP"


def stored_view(f: DemandFile, rec: dict[str, Any], count: Decimal | None = None) -> dict[str, Any]:
    """A stored or sent record as a change shows it (a joiner row by its number of joiners)."""
    out: dict[str, Any] = {"qty": format_qty(count if count is not None else rec["qty"]), "status": _state(rec)}
    for col in ("need_by", "unit", "basis", "location_code", "customer", "period"):
        if rec.get(col) is not None:
            out[col] = _show(rec[col])
    if f.kind == "demand_plan":
        out["status"] = rec.get("status") or "(blank)"
    return out


def _joiners(old: dict[str, Any], new: dict[str, Any], row: dict[str, Any]) -> Decimal:
    """How many joiners a stored joiner record was for: its quantity over the kit's quantity per joiner."""
    per = Decimal(new["qty"]) / Decimal(row["count"])
    return (Decimal(old["qty"]) / per).quantize(Decimal(1)) if per else ZERO


def classify(
    f: DemandFile, row: dict[str, Any], pairs: list[tuple[dict[str, Any], dict[str, Any]]], ref: Reference
) -> tuple[Change | None, str | None]:
    """What a row whose key was loaded before changes: (the change, None), (None, why it is left out), or (None, None)
    when it is the same as loaded (or cancelled again)."""
    if not f.changeable:
        diffs = [f"{c} {_show(o[c])} -> {_show(n[c])}" for o, n in pairs for c in f.compared if o[c] != n[c]]
        return (None, "already loaded; changed: " + "; ".join(diffs)) if diffs else (None, None)
    old, new = pairs[0]
    cancel = status_of(row) == "cancelled"
    if all(_state(o) == "cancelled" for o, _ in pairs):
        return (None, None) if cancel else (None, f.reopen_reason(row))
    fixed = [f"{c} {_show(o[c])} -> {_show(n[c])}" for o, n in pairs[:1] for c in f.fixed if o[c] != n[c]]
    if fixed:
        return None, f.fixed_reason(row, fixed)
    what = ["cancelled"] if cancel else _what(f, pairs)
    if not what:
        return None, None
    doc, line = f.doc(row)
    joiner = f.kind == "joiner_plan"
    change = Change(
        kind=f.kind, doc_no=doc, line_key=line,
        item_code=new.get("item_code") if len(pairs) == 1 else None, location_code=new.get("location_code"),
        what=what, before=stored_view(f, old, _joiners(old, new, row) if joiner else None),
        after=stored_view(f, new, row["count"] if joiner else None), label=f.label(row), pairs=pairs,
        requirement_id=old.get("id") if f.table == "material_requirement" and len(pairs) == 1 else None,
    )  # fmt: skip
    if f.kind == "production_order":  # the lines read after this header see it as it now is
        head = ref.orders[new["order_no"]]
        head.status, head.need_by = new["status"], new["need_by"]
    return change, None


def _what(f: DemandFile, pairs: list[tuple[dict[str, Any], dict[str, Any]]]) -> list[str]:
    old, new = pairs[0]
    if f.table == "product_demand":
        return ["reference"] if any(old[c] != new[c] for c in f.compared) else []
    what: list[str] = []
    if f.kind == "production_order":
        if old["qty"] != new["qty"]:
            what.append("header_qty")
    else:
        qty = rules.qty_change(
            sum((Decimal(o["base_qty"]) for o, _ in pairs), ZERO), sum((Decimal(n["base_qty"]) for _, n in pairs), ZERO)
        )
        what += [qty] if qty else []
        what += ["unit"] if old.get("unit") != new.get("unit") else []
        what += ["basis"] if old.get("basis", "gross") != new.get("basis", "gross") else []
    moved = rules.date_change(old["need_by"], new["need_by"]) if old.get("need_by") and new.get("need_by") else None
    return what + ([moved] if moved else [])


# ---- applying ---------------------------------------------------------------------------------

_LINE = """
    select l.id, l.mr_no, l.line_no, l.item_code, l.location_code, l.uom, l.need_qty, l.reserved_qty, l.issued_qty,
           l.need_by, l.status, m.order_no, m.status as mr_status, c.requested_qty
    from material_request_line l join material_request m on m.no = l.mr_no
    join material_request_cover c on c.id = l.id
    where l.requirement_id = :r
"""

_OPEN_COVER = """
    select coalesce(sum(b.qty), 0) from material_request_buy b
    join request_line rl on rl.request_no = b.request_no and rl.line_no = b.request_line_no
    join request r on r.no = b.request_no
    where b.mr_line_id = :l and rl.status = 'open' and r.status = any(:live)
"""

# what the buyer already has for a line: request lines with a route decided or on an order, newest first
_BUYER_LINKS = """
    select b.qty, b.request_no, b.request_line_no, rl.qty as rq, rl.base_qty as rbase, rl.est_price, rl.category_code
    from material_request_buy b
    join request_line rl on rl.request_no = b.request_no and rl.line_no = b.request_line_no
    join request r on r.no = b.request_no
    where b.mr_line_id = :l and b.qty > 0 and rl.status in ('sourcing', 'ordered')
      and r.status not in ('cancelled', 'rejected')
    order by b.id desc
"""


@dataclass(frozen=True)
class Doc:
    """The purchase document that holds a request line for the buyer: its order, else its RFQ, else the request."""

    doc_type: str
    doc_no: str
    owners: list[str | None]
    price: Decimal  # per stock unit
    promised: date | None = None
    need_by: date | None = None


def _per_base(price: Any, qty: Any, base: Any) -> Decimal:
    if price is None:
        return ZERO
    return Decimal(price) * Decimal(qty) / Decimal(base or qty)


def document(db: Session, link: dict[str, Any]) -> Doc:
    category_buyer = db.execute(
        text("select buyer_user from item_category where code = :c"), {"c": link["category_code"]}
    ).scalar()
    po = db.execute(
        text(
            "select pl.po_no, pl.unit_price, pl.qty, pl.base_qty, pl.promised_date, pl.need_by, p.buyer from po_line pl "
            "join po p on p.no = pl.po_no where pl.request_no = :r and pl.request_line_no = :l "
            "and pl.status <> 'cancelled' and p.status <> 'cancelled' order by p.created_at desc limit 1"
        ),
        {"r": link["request_no"], "l": link["request_line_no"]},
    ).first()
    if po is not None:
        return Doc(
            "po", po.po_no, [po.buyer, category_buyer], _per_base(po.unit_price, po.qty, po.base_qty),
            po.promised_date, po.need_by,
        )  # fmt: skip
    estimate = _per_base(link["est_price"], link["rq"], link["rbase"])
    rfq = db.execute(
        text(
            "select r.no, r.buyer from rfq_line l join rfq r on r.no = l.rfq_no where l.request_no = :r "
            "and l.request_line_no = :l and r.status <> 'cancelled' order by r.no desc limit 1"
        ),
        {"r": link["request_no"], "l": link["request_line_no"]},
    ).first()
    if rfq is not None:
        return Doc("rfq", rfq.no, [rfq.buyer, category_buyer], estimate)
    return Doc("request", link["request_no"], [category_buyer], estimate)


class Applier:
    """Applies the changes of one import (or one manual cancel) in one transaction, then, once it is committed, settles
    the cut requests and opens the cases. `released` and `to_check` say where the stock released goes and where the store
    check runs again."""

    def __init__(self, db: Session, tenant: str | None, actor: Actor, import_id: int) -> None:
        self.db, self.tenant, self.actor, self.import_id = db, tenant, actor, import_id
        self.released: set[Place] = set()
        self.to_check: set[Place] = set()
        self.cuts: list[reservation.Cut] = []
        self.pending: dict[tuple[Any, ...], demand_cases.Pending] = {}
        self.changes: list[Change] = []

    # -- one change -------------------------------------------------------------------------------

    def apply(self, change: Change) -> None:
        """Write the change and do what it does. The caller commits, then calls `finish`."""
        change.import_id = self.import_id
        change.id = int(
            self.db.execute(
                text(
                    "insert into demand_change (import_id, kind, doc_no, line_key, requirement_id, what, before, after) "
                    "values (:i, :k, :d, :l, :r, :w, cast(:b as jsonb), cast(:a as jsonb)) returning id"
                ),
                {
                    "i": self.import_id, "k": change.kind, "d": change.doc_no, "l": change.line_key,
                    "r": change.requirement_id, "w": change.what, "b": _json(change.before), "a": _json(change.after),
                },
            ).scalar_one()
        )  # fmt: skip
        self.changes.append(change)
        if change.kind in ("sales_order", "demand_plan"):
            self._reference(change)
        elif change.kind == "production_order":
            self._header(change)
        else:
            for old, new in change.pairs:
                self.requirement(change, old, new)
        audit.record(
            self.db, self.actor, "demand.change", "demand_change", change.id,
            before=change.before, after={**change.after, "what": change.what, "doc_no": change.doc_no},
            reason=f"{change.label}: {', '.join(change.what)}",
        )  # fmt: skip

    def _reference(self, change: Change) -> None:
        """A sales order or demand plan row is kept as sent; it changes no material."""
        old, new = change.pairs[0]
        self.db.execute(
            text(
                "update product_demand set customer = :customer, qty = :qty, need_by = :need_by, period = :period, "
                "status = :status where id = :id"
            ),
            {**{k: new.get(k) for k in ("customer", "qty", "need_by", "period", "status")}, "id": old["id"]},
        )

    def _header(self, change: Change) -> None:
        old, new = change.pairs[0]
        self.db.execute(
            text("update production_order set qty = :q, need_by = :n, status = :s where id = :i"),
            {"q": new["qty"], "n": new["need_by"], "s": new["status"], "i": old["id"]},
        )
        if new["status"] == "cancelled":
            reqs = self._requirements("production_order_id = :o and status <> 'cancelled'", o=old["id"])
            for r in reqs:
                self.requirement(change, r, {**r, "status": "cancelled"})
        elif old["need_by"] != new["need_by"]:  # lines with the order's own date move with it
            reqs = self._requirements(
                "production_order_id = :o and status <> 'cancelled' and need_by = :d", o=old["id"], d=old["need_by"]
            )
            for r in reqs:
                self.requirement(change, r, {**r, "need_by": new["need_by"]})

    def _requirements(self, where: str, **params: Any) -> list[dict[str, Any]]:
        sql = f"select * from material_requirement where {where} order by id"  # noqa: S608 - fixed clauses
        return [dict(r) for r in self.db.execute(text(sql), params).mappings()]

    def requirement(self, change: Change, old: dict[str, Any], new: dict[str, Any]) -> None:
        """One requirement and its material request line, as 4.2 of the plan says."""
        db = self.db
        cancel = new.get("status") == "cancelled"
        line = db.execute(text(_LINE + " for update of l"), {"r": old["id"]}).mappings().first()
        if line is None:  # no material request yet: the requirement alone changes
            self._store_requirement(old["id"], new, "cancelled" if cancel else "open")
            return
        ln = dict(line)
        change.mr_no = change.mr_no or ln["mr_no"]
        place = (ln["item_code"], ln["location_code"])
        need = ZERO if cancel else Decimal(new["base_qty"])
        reserved, issued, status = Decimal(ln["reserved_qty"]), Decimal(ln["issued_qty"]), ln["status"]
        before = {"need": format_qty(ln["need_qty"]), "reserved": format_qty(reserved), "status": status}
        if cancel or need < ln["need_qty"]:
            open_cover = Decimal(
                db.execute(text(_OPEN_COVER), {"l": ln["id"], "live": list(LIVE_REQUEST)}).scalar_one()
            )
            buyer_cover = max(Decimal(ln["requested_qty"]) - open_cover, ZERO)
            ex = rules.excess(need, issued, reserved, open_cover, buyer_cover)
            if ex.cut:
                self._cut(change, ln, ex.cut)
            if ex.release:
                reserved -= ex.release
                change.released[ln["item_code"]] = change.released.get(ln["item_code"], ZERO) + ex.release
                self.released.add(place)
            if ex.buyer:
                self._buyer_cases(change, ln, ex.buyer)
            if ex.store:
                self._store_case(change, ln, ex.store)
            status = "cancelled" if cancel else "issued" if issued >= need else "open"
        elif need > ln["need_qty"]:
            status = "open"
            self._check(change, place)
        need_by = ln["need_by"] if cancel else new["need_by"]
        if not cancel and need_by < ln["need_by"]:
            self._earlier(change, ln, need_by)
            self._check(change, place)
        db.execute(
            text(
                "update material_request_line set need_qty = :n, reserved_qty = :r, status = :s, need_by = :d "
                "where id = :i"
            ),
            {"n": need if need > 0 else ln["need_qty"], "r": reserved, "s": status, "d": need_by, "i": ln["id"]},
        )
        self._store_requirement(old["id"], new, "cancelled" if cancel else "issued" if status == "issued" else "open")
        head = self._head(ln["mr_no"], change)
        audit.record(
            db, audit.SYSTEM, "material_request.change", "material_request", ln["mr_no"],
            before={"line": ln["line_no"], "item": ln["item_code"], **before},
            after={
                "line": ln["line_no"], "item": ln["item_code"], "need": format_qty(need if need > 0 else ln["need_qty"]),
                "reserved": format_qty(reserved), "status": status, "need_by": need_by.isoformat(), "request": head,
            },
            reason=change.words(),
        )  # fmt: skip

    def _store_requirement(self, req_id: int, new: dict[str, Any], status: str) -> None:
        self.db.execute(
            text(
                "update material_requirement set qty = :q, unit = :u, base_qty = :b, need_by = :d, "
                "basis = coalesce(:basis, basis), status = :s where id = :i"
            ),
            {
                "q": new["qty"], "u": new["unit"], "b": new["base_qty"], "d": new["need_by"], "basis": new.get("basis"),
                "s": status, "i": req_id,
            },
        )  # fmt: skip

    def _head(self, mr_no: str, change: Change) -> str:
        """The material request after its line changed: its need-by is its earliest live line's; with no line left open
        it is issued (some line was issued) or cancelled; a line open again opens it again."""
        db = self.db
        counts = db.execute(
            text(
                "select count(*) filter (where status = 'open'), count(*) filter (where status = 'issued'), "
                "min(need_by) filter (where status <> 'cancelled'), (select status from material_request where no = :n) "
                "from material_request_line where mr_no = :n"
            ),
            {"n": mr_no},
        ).one()
        open_lines, issued_lines, need_by, status = counts
        new = status
        if open_lines == 0 and status == "open":
            new = "issued" if issued_lines else "cancelled"
        elif open_lines and status == "issued":
            new = "open"
        db.execute(
            text(
                "update material_request set status = :s, need_by = coalesce(:d, need_by), "
                "cancel_reason = case when :s = 'cancelled' and status <> 'cancelled' then :why else cancel_reason end "
                "where no = :n"
            ),
            {"s": new, "d": need_by, "why": change.words(), "n": mr_no},
        )
        return str(new)

    def _check(self, change: Change, place: Place) -> None:
        self.to_check.add(place)
        change.store_check = True

    def _cut(self, change: Change, ln: dict[str, Any], qty: Decimal) -> None:
        cuts = reservation._cut_cover(self.db, audit.SYSTEM, ln, qty, why=change.words())  # noqa: SLF001 - the one cut
        self.cuts += cuts
        change.requests_cut += [
            {"request_no": c.request_no, "line_no": c.line_no, "qty_before": format_qty(c.qty_before),
             "qty_after": format_qty(c.qty_after)}
            for c in cuts
        ]  # fmt: skip

    def _earlier(self, change: Change, ln: dict[str, Any], need_by: date) -> None:
        """Request lines not yet with the buyer take the earlier date; an order promised later is a case for the buyer."""
        db = self.db
        for request_no, line_no, was in db.execute(
            text(
                "select distinct rl.request_no, rl.line_no, rl.need_by from material_request_buy b "
                "join request_line rl on rl.request_no = b.request_no and rl.line_no = b.request_line_no "
                "join request r on r.no = b.request_no where b.mr_line_id = :l and b.qty > 0 and rl.status = 'open' "
                "and r.status = any(:live) and rl.need_by > :d order by 1, 2"
            ),
            {"l": ln["id"], "live": list(LIVE_REQUEST), "d": need_by},
        ).all():
            db.execute(
                text("update request_line set need_by = :d where request_no = :n and line_no = :l"),
                {"d": need_by, "n": request_no, "l": line_no},
            )
            db.execute(
                text(
                    "update request set need_by = (select min(need_by) from request_line where request_no = :n "
                    "and status <> 'cancelled') where no = :n"
                ),
                {"n": request_no},
            )
            audit.record(
                db, audit.SYSTEM, "request.line_need_by", "request", request_no,
                before={"line": line_no, "need_by": was.isoformat()}, after={"line": line_no, "need_by": need_by.isoformat()},
                reason=change.words(),
            )  # fmt: skip
            change.need_by_moved.append(
                {"request_no": request_no, "line_no": line_no, "from": was.isoformat(), "to": need_by.isoformat()}
            )
        for link in self._links(ln):
            doc = document(db, link)
            if doc.doc_type == "po" and rules.late_for(doc.promised, doc.need_by, need_by):
                self._add_case(change, ln, "buyer", doc, Decimal(link["qty"]), link, late=need_by)

    def _links(self, ln: dict[str, Any]) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.execute(text(_BUYER_LINKS), {"l": ln["id"]}).mappings()]

    def _buyer_cases(self, change: Change, ln: dict[str, Any], qty: Decimal) -> None:
        """The excess on cover the buyer has, newest first, one case per document."""
        left = qty
        for link in self._links(ln):
            if left <= 0:
                break
            take = min(Decimal(link["qty"]), left)
            left -= take
            self._add_case(change, ln, "buyer", document(self.db, link), take, link)

    def _store_case(self, change: Change, ln: dict[str, Any], qty: Decimal) -> None:
        doc = Doc("material_request", ln["mr_no"], [], ZERO)
        self._add_case(change, ln, "store", doc, qty, None)

    def _add_case(
        self,
        change: Change,
        ln: dict[str, Any],
        owner_kind: str,
        doc: Doc,
        qty: Decimal,
        link: dict[str, Any] | None,
        late: date | None = None,
    ) -> None:
        assert change.id is not None
        key = (change.id, owner_kind, doc.doc_type, doc.doc_no, late)
        case = self.pending.get(key)
        if case is None:
            step = (
                f"Ask the supplier to deliver by {late:%d %b}; then close the case" if late else NEXT_STEP[doc.doc_type]
            )
            case = demand_cases.Pending(
                change.id, owner_kind, doc.doc_type, doc.doc_no, ln["location_code"], change.words(), step,
                owners=doc.owners, amount=None if owner_kind == "store" else ZERO, late=late,
            )  # fmt: skip
            self.pending[key] = case
        case.excess[ln["item_code"]] = case.excess.get(ln["item_code"], ZERO) + qty
        case.lines.append(
            {
                "mr_no": ln["mr_no"], "mr_line_no": ln["line_no"], "item_code": ln["item_code"], "qty": format_qty(qty),
                "request_no": link["request_no"] if link else None, "request_line_no": link["request_line_no"] if link else None,
            }
        )  # fmt: skip
        if case.amount is not None:
            case.amount = round_money(case.amount + qty * doc.price)

    # -- after the commit ---------------------------------------------------------------------------

    def finish(self) -> None:
        """Requests cut to nothing are cancelled and requesters told; each case is opened. Commits."""
        cancelled = set(reservation.settle(self.db, self.tenant, self.cuts, why="The ERP's demand changed"))
        by_id = {c.id: c for c in self.changes}
        for change in self.changes:
            change.requests_cancelled = sorted({c["request_no"] for c in change.requests_cut} & cancelled)
        if self.pending:
            names: dict[str, str] = {str(r[0]): str(r[1]) for r in self.db.execute(text("select code, name from item"))}
            for case in self.pending.values():
                case.title = _title(case, names)
                if self.tenant:
                    by_id[case.change_id].cases.append(demand_cases.open_case(self.db, self.tenant, case))
        for change in self.changes:
            row = self.db.execute(
                text(
                    "update demand_change set effects = cast(:e as jsonb), mr_no = :m where id = :i returning applied_at"
                ),
                {"e": _json(change.effects()), "m": change.mr_no, "i": change.id},
            ).scalar_one()
            change.applied_at = row.isoformat()
        self.db.commit()


def _title(case: demand_cases.Pending, names: dict[str, str]) -> str:
    items = ", ".join(f"{format_qty(q)} {names.get(i, i)}" for i, q in case.excess.items())
    late = case.late
    if case.owner_kind == "store":
        return f"{case.title}: {items} issued too many"
    if late is not None:
        return f"{case.title}: {case.doc_no} brings {items} after {late:%d %b}"
    return f"{case.title}: {case.doc_no} buys {items} no longer needed"


def _json(value: Any) -> str:
    return json.dumps(value, default=str)
