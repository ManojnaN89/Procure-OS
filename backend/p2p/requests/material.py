"""Material requests: what a production order needs from the store, the stock set aside for it, the issue at production
start, and the shortfall that becomes a purchase request.

A material request is made for each imported production order (each joiner plan of a department, and each MRP plan per
location), one line per component. Lines loaded later join its open material request, or start a new one when it is
already issued or cancelled. The store check sets stock aside for the open lines (requests/reservation.py: earliest
need-by first, only free stock, never both stocked and bought for), as happens by itself whenever stock goes up. What a
line still lacks, and no live purchase request already asks for, becomes one draft purchase request per material
request, priced at the estimate (requests/estimates.py) and sent into approval. At production start the store issues
what is set aside. Cancelling a material request follows the ERP's cancel (requests/demand_changes.py): purchase
requests not yet with the buyer are cut, the buyer gets a case for what is already ordered or out for quotation, and the
stock released is set aside at once for the next lines that need it.

The store check and issues hold the same per item-location lock as every stock posting, and the store check runs one at
a time per company, so two people pressing at once cannot set aside the same stock twice.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.formulas import material as rules
from p2p.formulas.calendar import business_today
from p2p.formulas.money import format_qty, round_money
from p2p.formulas.replenishment import purchase_quantity
from p2p.formulas.words import count
from p2p.platform import audit, numbering, tenants
from p2p.platform.sod import Actor, user
from p2p.receiving import stock
from p2p.requests import combining, demand_changes, drafts, estimates, reservation

QTY = Decimal("0.0001")


class MaterialError(Exception):
    """The action makes no sense (no such request, nothing set aside, already closed)."""


# ---- making material requests from the imported production orders --------------------------------


def make_requests(db: Session, actor: Actor = audit.SYSTEM, *, today: date | None = None) -> list[str]:
    """A material request for every open production order (joiner plan of a department, MRP plan at a location) whose
    lines have none yet; lines loaded later are added to its open material request, or make a new one when it is issued
    or cancelled. An MRP plan's request is numbered `<plan>/<location>` and has no product. Returns the numbers made or
    added to. Commits."""
    today = today or business_today()
    rows = db.execute(
        text(
            "select r.id, r.item_code, r.unit, r.base_qty, r.need_by, r.location_code, r.source_kind, r.basis, "
            "r.department_code, p.id as order_id, "
            "case when r.source_kind = 'mrp' then r.source_doc_no || '/' || r.location_code else r.source_doc_no end "
            "as order_no, "
            "case when r.source_kind = 'mrp' then null else coalesce(p.product_code, split_part(r.line_key, '/', 1)) end "
            "as product_code, coalesce(p.need_by, r.need_by) as order_need_by, i.base_uom "
            "from material_requirement r left join production_order p on p.id = r.production_order_id "
            "join item i on i.code = r.item_code "
            "where r.status = 'open' and coalesce(p.status, 'open') = 'open' "
            "and not exists (select 1 from material_request_line l where l.requirement_id = r.id) "
            "order by coalesce(p.need_by, r.need_by), order_no, r.id"
        )
    ).mappings()
    by_order: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for r in rows:
        by_order.setdefault((r["source_kind"], r["order_no"]), []).append(dict(r))
    touched: list[str] = []
    for (kind, order_no), lines in by_order.items():
        head = lines[0]
        existing = db.execute(
            text(
                "select no, status from material_request where source_kind = :k and order_no = :o "
                "order by created_at desc"
            ),
            {"k": kind, "o": order_no},
        ).first()
        if existing is not None and existing.status != "open":
            existing = None  # issued or cancelled: the new lines get a request of their own
        if existing is None:
            no = numbering.next_number(db, "material_request", today, location_code=head["location_code"])
            db.execute(
                text(
                    "insert into material_request (no, production_order_id, order_no, product_code, location_code, "
                    "need_by, created_by, source_kind, department_code) "
                    "values (:no, :o, :ord, :prod, :loc, :need, :by, :kind, :dep)"
                ),
                {
                    "no": no,
                    "o": head["order_id"],
                    "ord": order_no,
                    "prod": head["product_code"],
                    "loc": head["location_code"],
                    "need": head["order_need_by"],
                    "by": actor.identity,
                    "kind": kind,
                    "dep": head["department_code"],
                },
            )
        else:
            no = existing.no
        last = db.execute(
            text("select coalesce(max(line_no), 0) from material_request_line where mr_no = :n"), {"n": no}
        ).scalar_one()
        for n, ln in enumerate(lines, start=int(last) + 1):
            db.execute(
                text(
                    "insert into material_request_line (mr_no, line_no, requirement_id, item_code, location_code, uom, "
                    "need_qty, need_by) values (:no, :n, :req, :item, :loc, :uom, :qty, :need)"
                ),
                {
                    "no": no,
                    "n": n,
                    "req": ln["id"],
                    "item": ln["item_code"],
                    "loc": ln["location_code"],
                    "uom": ln["base_uom"],
                    "qty": ln["base_qty"],
                    "need": ln["need_by"],
                },
            )
        audit.record(
            db,
            actor,
            "material_request.create" if existing is None else "material_request.add_lines",
            "material_request",
            no,
            after={
                "order": head["order_no"],
                "lines": [
                    f"{ln['item_code']} {format_qty(ln['base_qty'])}" + (" net" if ln["basis"] == "net" else "")
                    for ln in lines
                ],
                **(
                    {"basis": {ln["item_code"]: "net" for ln in lines if ln["basis"] == "net"}}
                    if any(ln["basis"] == "net" for ln in lines)
                    else {}
                ),
            },
        )
        touched.append(no)
    db.commit()
    return touched


# ---- the store check -----------------------------------------------------------------------------


@dataclass
class LineView:
    id: int
    line_no: int
    item_code: str
    uom: str
    need: Decimal
    reserved: Decimal
    issued: Decimal
    requested: Decimal
    reserved_now: Decimal = Decimal(0)  # set aside by this check

    @property
    def short(self) -> Decimal:
        """Still lacking after what is issued and set aside (some of it may already be requested)."""
        return max(self.need - self.issued - self.reserved, Decimal(0))


@dataclass
class Checked:
    mr_no: str
    order_no: str
    lines: list[LineView] = field(default_factory=list)
    request: drafts.DraftRequest | None = None  # the purchase request made for what is short
    unpriced: list[str] = field(default_factory=list)  # items short with no price to estimate them with
    combined: dict[str, str] = field(default_factory=dict)  # item -> the draft request whose line it joined
    joined: dict[int, Decimal] = field(default_factory=dict)  # material request line -> quantity bought on such a line
    department: str | None = None  # the request is raised for it (a joiner's department), else the planning one


@dataclass
class CheckResult:
    as_of: date
    requests: list[Checked] = field(default_factory=list)

    @property
    def purchase_requests(self) -> list[drafts.DraftRequest]:
        return [c.request for c in self.requests if c.request is not None]


def _shortfall_request(
    db: Session, checked: Checked, short: list[tuple[dict[str, Any], Decimal]], as_of: date, department: str | None
) -> list[tuple[int, Decimal]]:
    """The draft purchase request for what a material request lacks (in memory), and per line the material request
    line it buys for and how much of it. Items with no price at all are left out and listed."""
    info = drafts.item_info(db, [ln["item_code"] for ln, _ in short])
    lines: list[drafts.DraftLine] = []
    links: list[tuple[int, Decimal]] = []
    for ln, qty_short in short:
        item = info[ln["item_code"]]
        unit, factor = drafts.purchase_unit(item)
        qty = purchase_quantity(qty_short, base_per_purchase_unit=factor, moq=Decimal(item["moq"] or 0))
        estimate = estimates.for_item(
            db, ln["item_code"], unit, qty, as_of, item["standard_price"], ln["location_code"]
        )
        if estimate is None:
            checked.unpriced.append(ln["item_code"])
            continue
        price = estimate.price
        lines.append(
            drafts.DraftLine(
                item_code=ln["item_code"],
                item_name=item["name"],
                qty=qty,
                uom=unit,
                base_qty=qty * factor,
                est_price=price,
                value=round_money(qty * price),
                need_by=ln["need_by"],
                gl_code=str(item["gl"]) if item["gl"] is not None else None,
                reason=(
                    f"Short for {ln['order_no']} ({ln['mr_no']}): needs {format_qty(ln['need_qty'])} {ln['uom']}, "
                    f"{format_qty(ln['need_qty'] - qty_short)} covered"
                ),
                category_code=item["category_code"],
                est_source=estimate.source,
            )
        )
        links.append((ln["id"], qty_short))
    if lines:
        categories = {line.category_code for line in lines}
        checked.request = drafts.DraftRequest(
            location_code=short[0][0]["location_code"],
            category_code=categories.pop() if len(categories) == 1 else None,
            priority="normal",
            lines=lines,
            department=department,
        )
        drafts.route(db, checked.request, as_of)
    return links


def _combine(
    db: Session, actor: Actor, checked: Checked, links: list[tuple[int, Decimal]], window: int, as_of: date
) -> list[tuple[int, Decimal]]:
    """Lines of the new request that an open draft line can take (same item and place, needed within the window) join
    it; the rest stay on the new request, which goes when nothing is left on it. Returns the links of the lines kept."""
    req = checked.request
    if req is None:
        return links
    kept: list[tuple[drafts.DraftLine, tuple[int, Decimal]]] = []
    for line, link in zip(req.lines, links, strict=True):
        target = combining.open_line(db, line.item_code, req.location_code, line.need_by, window, req.department)
        if target is None:
            kept.append((line, link))
            continue
        note = f"{checked.order_no} ({checked.mr_no}) short {format_qty(link[1])}"
        combining.join(db, actor, target, need_by=line.need_by, note=note, link=link)
        checked.combined[line.item_code] = target["request_no"]
        checked.joined[link[0]] = link[1]
    if len(kept) != len(req.lines):
        req.lines = [line for line, _ in kept]
        if req.lines:
            categories = {line.category_code for line in req.lines}
            req.category_code = categories.pop() if len(categories) == 1 else None
            drafts.route(db, req, as_of)
        else:
            checked.request = None
    return [link for _, link in kept]


def store_check(
    db: Session,
    actor: Actor = audit.SYSTEM,
    *,
    tenant: str | None = None,
    location: str | None = None,
    today: date | None = None,
    submit: bool = True,
    places: list[reservation.Place] | None = None,
) -> CheckResult:
    """Set free stock aside for every open material request line, earliest need-by first, and ask to buy what is still
    short. With submit (and the company), each new purchase request is sent into approval. With `places`, only the open
    lines of those item-locations are checked (after a change in the ERP's demand)."""
    as_of = today or business_today()
    db.execute(text("select pg_advisory_xact_lock(hashtext('store_check'))"))  # one check at a time per company
    wanted = set(places) if places is not None else None
    rows = [
        r
        for r in reservation.open_lines(db, location=location, items=sorted({i for i, _ in wanted}) if wanted else None)
        if wanted is None or (r["item_code"], r["location_code"]) in wanted
    ]
    result = CheckResult(as_of)
    if not rows:
        db.commit()
        return result
    taken, cuts = reservation.reserve(db, actor, rows, reservation.lock(db, rows))
    by_request: dict[str, Checked] = {}
    short: dict[str, list[tuple[dict[str, Any], Decimal]]] = {}
    for r in rows:
        checked = by_request.setdefault(r["mr_no"], Checked(r["mr_no"], r["order_no"], department=r["department_code"]))
        checked.lines.append(
            LineView(
                r["id"], r["line_no"], r["item_code"], r["uom"], r["need_qty"], r["reserved_qty"], r["issued_qty"],
                r["requested_qty"], taken[r["id"]],
            )
        )  # fmt: skip
        lacking = rules.still_short(r["need_qty"], r["issued_qty"], r["reserved_qty"], r["requested_qty"])
        if lacking:
            short.setdefault(r["mr_no"], []).append((r, lacking))
    department = drafts.planning_department(db)
    window = combining.window_days(db)
    written: dict[str, drafts.DraftRequest] = {}
    grown: set[str] = set()  # requests written in this check that later lines were combined into
    for mr_no, checked in by_request.items():
        dept = checked.department or department
        links = _shortfall_request(db, checked, short[mr_no], as_of, dept) if mr_no in short else []
        links = _combine(db, actor, checked, links, window, as_of)
        grown |= set(checked.combined.values()) & set(written)
        if checked.request is not None:
            no = drafts.write(
                db,
                actor,
                checked.request,
                as_of=as_of,
                source_ref=mr_no,
                justification=f"Short for {checked.order_no} ({mr_no}): {count(len(checked.request.lines), 'item')} not in stock",
                audit_extra={"material_request": mr_no},
            )
            for n, (line_id, qty) in enumerate(links, start=1):
                db.execute(
                    text(
                        "insert into material_request_buy (mr_line_id, request_no, request_line_no, qty) "
                        "values (:l, :r, :n, :q)"
                    ),
                    {"l": line_id, "r": no, "n": n, "q": qty},
                )
            written[no] = checked.request
        added = dict(links) | checked.joined
        for line in checked.lines:
            line.requested += added.get(line.id, Decimal(0))
        audit.record(
            db,
            actor,
            "material_request.check",
            "material_request",
            mr_no,
            after={
                "reserved_now": {ln.item_code: str(ln.reserved_now) for ln in checked.lines if ln.reserved_now},
                "short": {ln.item_code: str(ln.short) for ln in checked.lines if ln.short},
                "purchase_request": checked.request.no if checked.request else None,
                "combined_into": checked.combined,
                "unpriced": checked.unpriced,
            },
        )
        result.requests.append(checked)
    for no in grown:
        drafts.refresh(db, written[no], as_of)
    db.commit()
    reservation.settle(db, tenant, cuts)
    if submit and tenant:
        drafts.submit(db, tenant, result.purchase_requests, actor, as_of)
    return result


# ---- issue at production start, and cancelling ---------------------------------------------------


def _head(db: Session, mr_no: str) -> dict[str, Any]:
    row = (
        db.execute(
            text("select no, status, order_no, production_order_id from material_request where no = :n for update"),
            {"n": mr_no},
        )
        .mappings()
        .first()
    )
    if row is None:
        db.rollback()
        raise MaterialError(f"there is no material request {mr_no}")
    if row["status"] != "open":
        db.rollback()
        raise MaterialError(f"material request {mr_no} is {row['status']}")
    return dict(row)


def _open_lines(db: Session, mr_no: str) -> list[dict[str, Any]]:
    rows = db.execute(
        text(
            "select id, line_no, item_code, location_code, need_qty, reserved_qty, issued_qty, requirement_id "
            "from material_request_line where mr_no = :n and status = 'open' order by line_no for update"
        ),
        {"n": mr_no},
    ).mappings()
    return [dict(r) for r in rows]


def _issue_line(db: Session, actor: Actor, mr_no: str, ln: dict[str, Any], qty: Decimal, today: date) -> None:
    """One line: its reservation goes down by what is issued, the stock leaves the shelf. The caller commits."""
    if qty <= 0:
        raise MaterialError(f"line {ln['line_no']}: the quantity must be more than zero")
    if qty > ln["reserved_qty"]:
        raise MaterialError(
            f"line {ln['line_no']} ({ln['item_code']}): only {format_qty(ln['reserved_qty'])} is set aside, "
            f"so {format_qty(qty)} cannot be issued; run the store check when more stock is in"
        )
    done = ln["issued_qty"] + qty == ln["need_qty"]
    db.execute(
        text(
            "update material_request_line set reserved_qty = reserved_qty - :q, issued_qty = issued_qty + :q, "
            "status = :s where id = :i"
        ),
        {"q": qty, "s": "issued" if done else "open", "i": ln["id"]},
    )
    if done:
        db.execute(text("update material_requirement set status = 'issued' where id = :r"), {"r": ln["requirement_id"]})
    stock.issue_movement(db, actor, ln["item_code"], ln["location_code"], qty, reference=mr_no, today=today)


def issue(
    db: Session,
    user_id: int,
    mr_no: str,
    quantities: dict[int, Decimal] | None = None,
    *,
    today: date | None = None,
) -> dict[str, Decimal]:
    """At production start: issue what is set aside for the request (all of it, or the given quantity per line number,
    each no more than is set aside). The reservation goes with the stock. All or nothing. Returns item -> issued."""
    today = today or business_today()
    _head(db, mr_no)
    lines = _open_lines(db, mr_no)
    unknown = sorted(set(quantities or {}) - {ln["line_no"] for ln in lines})
    if unknown:
        db.rollback()
        raise MaterialError(f"{mr_no} has no open line {', '.join(map(str, unknown))}")
    actor = user(user_id)
    issued: dict[str, Decimal] = {}
    try:
        for ln in lines:
            if quantities is not None and ln["line_no"] not in quantities:
                continue
            qty = Decimal(quantities[ln["line_no"]] if quantities is not None else ln["reserved_qty"]).quantize(QTY)
            if quantities is None and qty == 0:
                continue  # nothing set aside for this line yet
            _issue_line(db, actor, mr_no, ln, qty, today)
            issued[ln["item_code"]] = qty
        if not issued:
            raise MaterialError(f"nothing is set aside for {mr_no} yet; run the store check first")
    except (MaterialError, stock.StockError):
        db.rollback()
        raise
    left = db.execute(
        text("select count(*) from material_request_line where mr_no = :n and status = 'open'"), {"n": mr_no}
    ).scalar_one()
    if left == 0:
        db.execute(text("update material_request set status = 'issued' where no = :n"), {"n": mr_no})
    audit.record(
        db,
        actor,
        "material_request.issue",
        "material_request",
        mr_no,
        after={"issued": {k: str(v) for k, v in issued.items()}, "status": "issued" if left == 0 else "open"},
    )
    db.commit()
    return issued


def cancel(db: Session, user_id: int, mr_no: str, reason: str, *, tenant: str | None = None) -> dict[str, Any]:
    """The order is called off here rather than in the ERP: the same rules as the ERP's cancel (rule Q21). Its lines
    release their stock, purchase requests not yet with the buyer are cut (one left with nothing is cancelled), the buyer
    gets a case for what is already ordered or out for quotation, and the store one for what was already issued. The
    stock released is set aside at once for the open lines that need it. Returns what it did."""
    why = (reason or "").strip()
    if not why:
        raise MaterialError("say why the material request is cancelled")
    head = _head(db, mr_no)
    actor = user(user_id)
    tenant = tenant or tenants.slug_of(str(db.get_bind().engine.url.database or ""))
    reqs = [
        dict(r)
        for r in db.execute(
            text(
                "select q.* from material_requirement q join material_request_line l on l.requirement_id = q.id "
                "where l.mr_no = :n and l.status <> 'cancelled' order by l.line_no"
            ),
            {"n": mr_no},
        ).mappings()
    ]
    import_id = db.execute(
        text(
            "insert into demand_import (source, files, loaded, already_loaded, rejected, changed, imported_by) "
            "values (:s, '{}', 0, 0, 0, 1, :by) returning id"
        ),
        {"s": f"{mr_no} cancelled on Material requests", "by": actor.identity},
    ).scalar_one()
    applier = demand_changes.Applier(db, tenant, actor, import_id)
    change = demand_changes.Change(
        kind="manual", doc_no=head["order_no"], line_key=None, item_code=None, location_code=None, what=["cancelled"],
        before={"status": "open"}, after={"status": "cancelled", "reason": why}, label=mr_no,
        pairs=[(r, {**r, "status": "cancelled"}) for r in reqs],
    )  # fmt: skip
    applier.apply(change)
    db.execute(
        text("update material_request set status = 'cancelled', cancel_reason = :why where no = :n"),
        {"why": why, "n": mr_no},
    )
    db.execute(
        text("update production_order set status = 'cancelled' where id = :o"), {"o": head["production_order_id"]}
    )
    db.commit()
    applier.finish()
    left = sorted({ln["request_no"] for c in change.cases for ln in c.get("lines", []) if ln.get("request_no")})
    audit.record(
        db, actor, "material_request.cancel", "material_request", mr_no, before={"status": "open"},
        after={
            "status": "cancelled", "released": {k: format_qty(v) for k, v in change.released.items()},
            "purchase_requests_reduced": sorted({c["request_no"] for c in change.requests_cut}),
            "purchase_requests_cancelled": change.requests_cancelled, "purchase_requests_left": left,
            "cases": [c["task_id"] for c in change.cases],
        },
        reason=why,
    )  # fmt: skip
    db.commit()
    reservation.stock_rose(db, tenant, sorted(applier.released), why=f"{head['order_no']} ({mr_no}) was cancelled")
    return {
        "requests_cut": change.requests_cut, "requests_cancelled": change.requests_cancelled, "cases": change.cases,
        "purchase_requests_left": left,
    }  # fmt: skip


# ---- for the screen -------------------------------------------------------------------------------


def listing(db: Session, *, status: str | None = None) -> list[dict[str, Any]]:
    """Material requests, earliest need-by first, each with its lines and the purchase requests buying for them."""
    heads = [
        dict(h)
        for h in db.execute(
            text(
                "select no, order_no, product_code, location_code, need_by, status, created_at, cancel_reason, "
                "source_kind from material_request where (cast(:s as text) is null or status = :s) order by need_by, no"
            ),
            {"s": status},
        ).mappings()
    ]
    if not heads:
        return heads
    nos = [h["no"] for h in heads]
    buys: dict[int, list[str]] = {}
    for line_id, request_no in db.execute(
        text(
            "select b.mr_line_id, b.request_no from material_request_buy b "
            "join material_request_line l on l.id = b.mr_line_id where l.mr_no = any(:n) order by b.id"
        ),
        {"n": nos},
    ):
        buys.setdefault(line_id, []).append(request_no)
    by_no: dict[str, list[dict[str, Any]]] = {}
    for ln in db.execute(
        text(
            "select c.id, c.mr_no, c.line_no, c.item_code, i.name as item_name, c.uom, c.need_qty, c.reserved_qty, "
            "c.issued_qty, c.requested_qty, c.need_by, c.status, q.basis from material_request_cover c "
            "join item i on i.code = c.item_code join material_request_line l on l.id = c.id "
            "join material_requirement q on q.id = l.requirement_id where c.mr_no = any(:n) order by c.mr_no, c.line_no"
        ),
        {"n": nos},
    ).mappings():
        row = dict(ln)
        row["short"] = max(row["need_qty"] - row["issued_qty"] - row["reserved_qty"], Decimal(0))
        row["requests"] = sorted(set(buys.get(row.pop("id"), [])))
        by_no.setdefault(row.pop("mr_no"), []).append(row)
    changes: dict[str, list[dict[str, Any]]] = {}
    for c in db.execute(
        text("select id, mr_no, applied_at, what, before, after from demand_change where mr_no = any(:n) order by id"),
        {"n": nos},
    ).mappings():
        changes.setdefault(c["mr_no"], []).append(
            {"id": c["id"], "at": c["applied_at"].isoformat(), "what": list(c["what"]), "before": c["before"],
             "after": c["after"]}
        )  # fmt: skip
    for h in heads:
        h["lines"] = by_no.get(h["no"], [])
        h["changes"] = changes.get(h["no"], [])
    return heads
