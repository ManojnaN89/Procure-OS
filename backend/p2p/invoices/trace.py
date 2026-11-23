"""The order trace: from any one document, the whole chain it belongs to, end to end, with dates, quantities, amounts and
who acted. Read only; it follows the links the tables already keep.

Production order -> material request -> purchase request (material_request_buy) -> quotation request (rfq_line) ->
purchase order (po_line.request_no, po.rfq_no) -> goods receipt / service entry -> invoice (invoice_line, credit notes by
ref_invoice_no) -> payment (payment_allocation, advances by po_no).
"""

from collections.abc import Iterable
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.formulas.words import count

D = Decimal
# what a number can be, and where to look for it (the first table that has it wins)
KINDS = (
    ("production_order", "select order_no from production_order where order_no = :n limit 1"),
    ("material_request", "select no from material_request where no = :n"),
    ("request", "select no from request where no = :n"),
    ("rfq", "select no from rfq where no = :n"),
    ("po", "select no from po where no = :n"),
    ("goods_receipt", "select no from goods_receipt where no = :n"),
    ("service_entry", "select no from service_entry where no = :n"),
    ("invoice", "select no from invoice where no = :n"),
    ("payment", "select no from payment where no = :n"),
)
STAGES = (
    ("production_order", "Production orders"),
    ("material_request", "Material requests"),
    ("request", "Purchase requests"),
    ("rfq", "Quotation requests"),
    ("po", "Purchase orders"),
    ("goods_receipt", "Goods receipts"),
    ("service_entry", "Service entries"),
    ("invoice", "Invoices"),
    ("payment", "Payments"),
)


class People:
    """Names for who acted: an organisation person's code (U03) or a login's number."""

    def __init__(self, db: Session) -> None:
        self.names = {str(r[0]): str(r[1]) for r in db.execute(text("select code, name from org_user"))}
        self.names.update(
            {str(r[0]): str(r[1]) for r in db.execute(text("select user_id, display_name from user_profile"))}
        )

    def __call__(self, who: Any) -> str | None:
        return None if who in (None, "") else self.names.get(str(who), str(who))


def kind_of(db: Session, no: str) -> str | None:
    for kind, sql in KINDS:
        if db.execute(text(sql), {"n": no}).first():
            return kind
    return None


def _set(db: Session, sql: str, **params: Any) -> set[str]:
    return {str(r[0]) for r in db.execute(text(sql), params) if r[0] is not None}


def _seeds(db: Session, kind: str, no: str) -> tuple[set[str], set[str]]:
    """The purchase requests and orders the document leads to directly."""
    if kind == "production_order":
        mrs = _set(db, "select no from material_request where order_no = :n", n=no)
        return _requests_of_mrs(db, mrs), set()
    if kind == "material_request":
        return _requests_of_mrs(db, {no}), set()
    if kind == "request":
        return {no}, set()
    if kind == "rfq":
        requests = _set(db, "select request_no from rfq_line where rfq_no = :n union select request_no from rfq where no = :n", n=no)  # fmt: skip
        return requests, _set(db, "select no from po where rfq_no = :n", n=no)
    if kind == "po":
        return set(), {no}
    if kind == "goods_receipt":
        return set(), _set(db, "select po_no from goods_receipt where no = :n", n=no)
    if kind == "service_entry":
        return set(), _set(db, "select po_no from service_entry where no = :n", n=no)
    if kind == "invoice":
        return set(), _pos_of_invoices(db, {no} | _set(db, "select ref_invoice_no from invoice where no = :n", n=no))
    invoices = _set(db, "select invoice_no from payment_allocation where payment_no = :n", n=no)
    return set(), _pos_of_invoices(db, invoices) | _set(db, "select po_no from payment where no = :n", n=no)


def _requests_of_mrs(db: Session, mrs: set[str]) -> set[str]:
    return _set(
        db,
        "select b.request_no from material_request_buy b join material_request_line l on l.id = b.mr_line_id "
        "where l.mr_no = any(:m)",
        m=list(mrs),
    )


def _pos_of_invoices(db: Session, invoices: set[str]) -> set[str]:
    return _set(db, "select po_no from invoice_line where invoice_no = any(:i)", i=list(invoices))


def chain(db: Session, no: str) -> dict[str, Any] | None:
    """The chain the document is part of, stage by stage, or None when no document has this number."""
    no = no.strip()
    kind = kind_of(db, no)
    if kind is None:
        return None
    requests, pos = _seeds(db, kind, no)
    requests |= _set(db, "select request_no from po_line where po_no = any(:p)", p=list(pos))
    pos |= _set(db, "select po_no from po_line where request_no = any(:r)", r=list(requests))
    who = People(db)
    mrs = _set(
        db,
        "select l.mr_no from material_request_buy b join material_request_line l on l.id = b.mr_line_id "
        "where b.request_no = any(:r)",
        r=list(requests),
    )
    if kind == "material_request":
        mrs.add(no)
    if kind == "production_order":
        mrs |= _set(db, "select no from material_request where order_no = :n", n=no)
    invoices = _set(db, "select invoice_no from invoice_line where po_no = any(:p)", p=list(pos))
    invoices |= _set(db, "select no from invoice where ref_invoice_no = any(:i)", i=list(invoices))
    items = {
        "production_order": _production_orders(db, mrs, no if kind == "production_order" else None),
        "material_request": _material_requests(db, mrs, who),
        "request": _requests(db, requests, who),
        "rfq": _rfqs(db, requests, pos, who),
        "po": _orders(db, pos, who),
        "goods_receipt": _receipts(db, pos, who),
        "service_entry": _service_entries(db, pos, who),
        "invoice": _invoices(db, invoices, who),
        "payment": _payments(db, invoices, pos, who),
    }
    return {
        "document": no,
        "kind": kind,
        "stages": [{"stage": k, "title": t, "items": items[k]} for k, t in STAGES if items[k]],
    }


def _rows(db: Session, sql: str, **params: Any) -> list[dict[str, Any]]:
    return [dict(r) for r in db.execute(text(sql), params).mappings()]


def _item(no: Any, date: Any, status: Any, *, qty: Any = None, amount: Any = None, who: Any = None, detail: str = "") -> dict[str, Any]:  # fmt: skip
    return {"no": no, "date": date, "status": status, "qty": qty, "amount": amount, "who": who, "detail": detail}


def _approvals(db: Session, table: str, column: str, nos: Iterable[str], who: People) -> dict[str, str]:
    out: dict[str, list[str]] = {}
    for r in _rows(
        db,
        f"select {column} as no, role, coalesce(acted_by, approver) as actor, status, acted_at from {table} "  # noqa: S608
        f"where {column} = any(:n) and status in ('approved', 'rejected', 'returned') order by acted_at nulls last, id",
        n=list(nos),
    ):
        when = f" {r['acted_at']:%d %b %Y}" if r["acted_at"] else ""
        out.setdefault(r["no"], []).append(f"{r['status']} by {who(r['actor'])} ({r['role']}){when}")
    return {k: "; ".join(v) for k, v in out.items()}


def _production_orders(db: Session, mrs: set[str], order_no: str | None) -> list[dict[str, Any]]:
    return [
        _item(
            r["order_no"], r["need_by"], r["status"], qty=r["qty"], detail=f"{r['product_code']}, needed by this date"
        )
        for r in _rows(
            db,
            "select distinct p.order_no, p.need_by, p.status, p.qty, p.product_code from production_order p "
            "where p.order_no = :o or p.id in (select production_order_id from material_request where no = any(:m)) "
            "order by p.order_no",
            o=order_no,
            m=list(mrs),
        )
    ]


def _material_requests(db: Session, mrs: set[str], who: People) -> list[dict[str, Any]]:
    return [
        _item(r["no"], r["created_at"], r["status"], qty=r["qty"], who=who(r["created_by"]),
              detail=f"for {r['order_no']}, {count(r['lines'], 'line')}")
        for r in _rows(
            db,
            "select m.no, m.created_at, m.status, m.created_by, m.order_no, count(l.id) as lines, sum(l.need_qty) as qty "
            "from material_request m left join material_request_line l on l.mr_no = m.no where m.no = any(:m) "
            "group by m.no order by m.no",
            m=list(mrs),
        )
    ]  # fmt: skip


def _requests(db: Session, requests: set[str], who: People) -> list[dict[str, Any]]:
    approved = _approvals(db, "approval_step", "request_no", requests, who)
    return [
        _item(r["no"], r["created_at"], r["status"], qty=r["qty"], amount=r["total_value"], who=who(r["requester"]),
              detail=approved.get(r["no"], ""))
        for r in _rows(
            db,
            "select r.no, r.created_at, r.status, r.total_value, r.requester, sum(l.qty) as qty from request r "
            "left join request_line l on l.request_no = r.no where r.no = any(:r) group by r.no order by r.no",
            r=list(requests),
        )
    ]  # fmt: skip


def _rfqs(db: Session, requests: set[str], pos: set[str], who: People) -> list[dict[str, Any]]:
    out = []
    for r in _rows(
        db,
        "select q.no, coalesce(q.sent_at, q.created_at) as at, q.status, q.buyer, "
        "(select count(*) from rfq_supplier s where s.rfq_no = q.no) as asked, "
        "(select count(distinct supplier_code) from quote where rfq_no = q.no) as quotes, "
        "(select sum(qty) from rfq_line where rfq_no = q.no) as qty from rfq q where q.no in "
        "(select rfq_no from rfq_line where request_no = any(:r) union select rfq_no from po where no = any(:p)) "
        "order by q.no",
        r=list(requests),
        p=list(pos),
    ):
        awards = _rows(
            db,
            "select distinct supplier_code, by_user from award where rfq_no = :n and status in ('final', 'approved') "
            "order by supplier_code",
            n=r["no"],
        )
        won = f"; awarded to {', '.join(a['supplier_code'] for a in awards)}" if awards else ""
        out.append(
            _item(r["no"], r["at"], r["status"], qty=r["qty"], who=who(awards[0]["by_user"] if awards else r["buyer"]),
                  detail=f"{count(r['asked'], 'supplier')} asked, {r['quotes']} quoted{won}")
        )  # fmt: skip
    return out


def _orders(db: Session, pos: set[str], who: People) -> list[dict[str, Any]]:
    """Each order with what is still open on it: quantity still to receive, and received but not yet invoiced."""
    approved = _approvals(db, "po_approval_step", "po_no", pos, who)
    out = []
    for r in _rows(
        db,
        "select po.no, po.created_at, po.approved_at, po.status, po.total, po.buyer, po.version, po.touchless, "
        "coalesce(s.trade_name, s.legal_name) as supplier, sum(l.qty) as qty, "
        "sum(case when l.status = 'cancelled' then 0 else greatest(l.qty - l.received_qty - l.short_closed_qty, 0) end) as to_receive, "
        "sum(greatest(l.received_qty - l.invoiced_qty, 0)) as to_invoice "
        "from po join supplier s on s.code = po.supplier_code join po_line l on l.po_no = po.no where po.no = any(:p) "
        "group by po.no, s.trade_name, s.legal_name order by po.no",
        p=list(pos),
    ):
        parts = [f"{r['supplier']}, version {r['version']}", approved.get(r["no"], "")]
        parts.append("issued without a buyer (touchless)" if r["touchless"] else "")
        parts.append(f"{r['to_receive']:f} still to receive" if r["to_receive"] else "")
        parts.append(f"{r['to_invoice']:f} received, not invoiced" if r["to_invoice"] else "")
        out.append(
            {**_item(r["no"], r["approved_at"] or r["created_at"], r["status"], qty=r["qty"], amount=r["total"],
                     who=who(r["buyer"]), detail="; ".join(x for x in parts if x)),
             "to_receive": r["to_receive"], "to_invoice": r["to_invoice"]}
        )  # fmt: skip
    return out


def _receipts(db: Session, pos: set[str], who: People) -> list[dict[str, Any]]:
    return [
        _item(r["no"], r["accepted_on"] or r["gate_entry_at"], r["inspection_status"], qty=r["accepted"],
              amount=r["value"], who=who(r["received_by"]),
              detail=f"for {r['po_no']}; {r['received']} received, {r['rejected']} rejected")
        for r in _rows(
            db,
            "select g.no, g.po_no, g.accepted_on, g.gate_entry_at, g.inspection_status, g.received_by, "
            "sum(l.qty_received) as received, sum(coalesce(l.qty_accepted, 0)) as accepted, "
            "sum(coalesce(l.qty_rejected, 0)) as rejected, "
            "sum(round(coalesce(l.qty_accepted, 0) * pl.unit_price, 2)) as value from goods_receipt g "
            "join gr_line l on l.gr_no = g.no join po_line pl on pl.po_no = l.po_no and pl.line_no = l.po_line_no "
            "where g.po_no = any(:p) group by g.no order by g.gate_entry_at, g.no",
            p=list(pos),
        )
    ]  # fmt: skip


def _service_entries(db: Session, pos: set[str], who: People) -> list[dict[str, Any]]:
    return [
        _item(r["no"], r["accepted_on"], "confirmed", qty=r["qty_accepted"], amount=r["amount_accepted"],
              who=who(r["accepted_by"]), detail=f"for {r['po_no']} line {r['po_line_no']}, by {r['via']}")
        for r in _rows(
            db,
            "select no, po_no, po_line_no, accepted_on, qty_accepted, amount_accepted, accepted_by, via "
            "from service_entry where po_no = any(:p) order by accepted_on, no",
            p=list(pos),
        )
    ]  # fmt: skip


def _invoices(db: Session, invoices: set[str], who: People) -> list[dict[str, Any]]:
    out = []
    for r in _rows(
        db,
        "select i.no, i.type, i.supplier_invoice_no, i.invoice_date, i.status, i.total, i.entered_by, i.due_date, "
        "i.block_reason, (select sum(qty) from invoice_line where invoice_no = i.no) as qty from invoice i "
        "where i.no = any(:i) order by i.invoice_date, i.no",
        i=list(invoices),
    ):
        parts = [f"{r['type'].replace('_', ' ')} {r['supplier_invoice_no']}"]
        if r["due_date"]:
            parts.append(f"due {r['due_date']:%d %b %Y}")
        if r["block_reason"]:
            parts.append(str(r["block_reason"]))
        out.append(
            _item(r["no"], r["invoice_date"], r["status"], qty=r["qty"], amount=r["total"], who=who(r["entered_by"]),
                  detail="; ".join(parts))
        )  # fmt: skip
    return out


def _payments(db: Session, invoices: set[str], pos: set[str], who: People) -> list[dict[str, Any]]:
    return [
        _item(r["no"], r["paid_on"] or r["run_date"], "paid" if r["paid_on"] else r["run_status"], amount=r["amount"],
              who=who(r["approved_by"]),
              detail="; ".join(x for x in (f"{r['type']} in {r['run_no']}", f"bank reference {r['utr']}" if r["utr"] else "") if x))
        for r in _rows(
            db,
            "select p.no, p.type, p.run_no, p.amount, p.utr, p.paid_on, r.run_date, r.status as run_status, r.approved_by "
            "from payment p join payment_run r on r.no = p.run_no where p.no in "
            "(select payment_no from payment_allocation where invoice_no = any(:i)) or p.po_no = any(:p) order by p.no",
            i=list(invoices),
            p=list(pos),
        )
    ]  # fmt: skip
