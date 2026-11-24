"""The exceptions screen: one list of everything that is stuck and needs a person, so nothing waits silently.

Each row says what is stuck, why in plain words, who must act, what they do next, and for how many working days it has
waited. The rows come from the services that already know each case (invoice blocks, goods received without an order,
services to confirm, proposals, approvals past their reminder, the buyer's supplier alerts, the cases a change in
the ERP's demand left for the buyer or the store, and supply promised too late for a need); nothing is written here.
A person sees the rows their roles may see, and the ones where they are the person named; the administrator sees them all,
read only.
"""

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from functools import partial
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.approvals.workflow import name_of, org_code_of
from p2p.formulas import matching
from p2p.formulas.calendar import IST, BusinessCalendar
from p2p.formulas.kpis import working_days_between
from p2p.invoices.service import asked_or_confirmed
from p2p.orders import document as po_document
from p2p.orders.workflow import NO_APPROVER
from p2p.platform import alerts, permissions
from p2p.platform.rows import rows as _rows
from p2p.receiving import goods, services, unordered
from p2p.requests import demand_cases, supply_cases

KINDS: dict[str, str] = {
    "invoice": "Invoices blocked",
    "unordered": "Goods received without an order",
    "service": "Services not confirmed",
    "approval": "Approvals past their reminder",
    "proposal": "Decisions waiting",
    "supplier": "Suppliers to chase",
    "order": "Orders nobody may approve",
    "demand": "Demand changes to act on",
    "late_supply": "Supply promised too late",
    "ai_handover": "AI work handed to a person",
    "rfq_no_response": "Quotation requests closed short",
    "quote_sender_check": "Supplier replies not read",
    "quote_expired": "Quotes expired before the order",
    "path_case": "Contract above the market: path A or B",
    "po_ack_overdue": "Orders not acknowledged after the reminders",
    "supplier_ack_confirm": "Acknowledgements to record",
    "supplier_sender_check": "Supplier order emails not read",
    "receipt_excess": "Goods above an order",
    "return_outcome": "Returns to decide",
    "inspection": "Receipts to inspect",
    "return_draft": "Returns to dispatch",
}
SOURCING_NEXT: dict[str, tuple[str, str]] = {  # task kind (block 6): the permission that acts, next step
    "rfq_no_response": (
        "rfq.create",
        "Extend the closing date, select from the quotes received with a reason, or change the lines' route",
    ),
    "quote_sender_check": (
        "rfq.create",
        "Ask the supplier to send from its registered address, or register the contact through supplier records",
    ),
    "quote_expired": ("rfq.create", "Ask for a renewed price, or select another valid quote with a reason"),
    "path_case": ("rfq.create", "Choose path A (buy on the contract, with a reason) or B (ask for quotes)"),
}
ORDER_NEXT: dict[str, tuple[str, str]] = {  # task kind (block 8): the permission that acts, next step
    "po_ack_overdue": (
        "po.send",
        "Call the supplier, record its acknowledgement with a note, send the order again, or cancel it",
    ),
    "supplier_ack_confirm": (
        "po.send",
        "Read the supplier's email and record its acknowledgement of that version, or ask it to accept from the link",
    ),
    "supplier_sender_check": (
        "po.send",
        "Ask the supplier to write from its registered address, or register the contact through supplier records",
    ),
}

AP = ("AP", "invoice.match", "Ask the supplier for a corrected invoice")
RECEIPT_NEXT: dict[str, tuple[str, str]] = {  # task kind (block 9): the permission that acts, next step
    "receipt_excess": ("po.create", "Accept them by revising the order, or return them"),
    "return_outcome": ("po.create", "Agree with the supplier whether the goods will be replaced"),
}
INVOICE_NEXT: dict[str, tuple[str, str, str]] = {  # the block reason: owner, the permission that acts, next step
    matching.PRICE: (
        "AP",
        "invoice.match",
        "Ask the supplier for a credit note or a corrected invoice, or ask for a release",
    ),
    matching.QTY: (
        "AP",
        "invoice.match",
        "Match again once the goods are received, or ask the supplier for a credit note",
    ),
    matching.IRN: AP,
    matching.GSTIN: AP,
    matching.TAX: AP,
    matching.ARITH: AP,
}
RELEASE = (
    "Finance or an approver (not who entered the invoice, asked for it or confirmed it)",
    "invoice.approve",
    "Approve or reject the release on Approvals",
)
OWN_ROWS = {unordered.ACTION, "invoice.release_block"}  # proposals shown on their own document's row
BUYER_ALERTS: dict[str, tuple[str, str]] = {  # alert rule: the permission that acts, next step
    "rfq_no_quotes": ("rfq.create", "Remind the suppliers, or ask more suppliers to quote"),
    "po_not_acknowledged": ("po.send", "Ask the supplier to acknowledge the order"),
    "late_delivery": ("po.create", "Chase the supplier for a delivery date"),
}


@dataclass(frozen=True)
class Stuck:
    kind: str
    ref: str
    link: str
    reason: str
    owner: str
    next_step: str
    since: date
    amount: Decimal | None = None
    act: str | None = None  # the permission of who acts; None when only the named people act
    people: frozenset[str] = frozenset()  # the people (org codes) named to act
    see: tuple[str, ...] = ()  # permissions that let someone see the row (besides the people named)
    not_by: frozenset[str] = frozenset()  # logins that may not act on it (segregation of duties)
    check: Callable[[int], bool] | None = None  # a further test of whether a login may act
    case: dict[str, Any] | None = None  # a case the row's screen closes (a demand change): its task and outcomes


def _day(ts: Any) -> date:
    return ts.astimezone(IST).date() if isinstance(ts, datetime) else ts


def _not_asked_by(db: Session, invoice_no: str, user_id: int) -> bool:
    return not asked_or_confirmed(db, invoice_no, user_id)


def _not_received_by(db: Session, received_by: str | None, user_id: int) -> bool:
    return org_code_of(db, user_id) != received_by


# ---- the sources ------------------------------------------------------------------------------


def _invoices(db: Session) -> Iterator[Stuck]:
    rows = _rows(
        db,
        "select i.no, i.supplier_invoice_no, i.total, i.received_on, i.block_reason, i.entered_by, "
        "(select m.reason_codes from match_result m where m.invoice_no = i.no and m.result = 'blocked' "
        "and m.resolved_at is null order by m.invoice_line_no nulls first limit 1) as code, "
        "(select p.id from proposal p where p.action = 'invoice.release_block' and p.status = 'pending' "
        "and p.payload->>'invoice' = i.no limit 1) as release "
        "from invoice i where i.type = 'invoice' and i.status = 'blocked' and i.payment_block <> 'duplicate'",
    )
    for r in rows:
        no, see = r["no"], ("invoice.view", "invoice.match", "invoice.approve")
        common = {"kind": "invoice", "ref": no, "link": f"#/invoices/{no}", "since": r["received_on"]}
        reason = r["block_reason"] or "Blocked by the match"
        amount = Decimal(r["total"])
        if r["release"] is not None:
            owner, act, step = RELEASE
            yield Stuck(
                **common, reason=reason, owner=owner, next_step=step, amount=amount, act=act, see=see,
                not_by=frozenset({str(r["entered_by"])}), check=partial(_not_asked_by, db, no),
            )  # fmt: skip
        elif r["code"] == matching.SERVICE:
            people = frozenset(
                str(c)
                for p in _rows(
                    db,
                    "select distinct q.requester, q.on_behalf_of from invoice_line il join po_line pl on pl.po_no = il.po_no "
                    "and pl.line_no = il.po_line_no join request q on q.no = pl.request_no where il.invoice_no = :n",
                    n=no,
                )
                for c in (p["requester"], p["on_behalf_of"])
                if c
            )
            names = " or ".join(name_of(db, c) for c in sorted(people)) or "the requester"
            yield Stuck(
                **common, reason=reason, owner=f"Requester: {names}", amount=amount, people=people, see=see,
                next_step="Confirm the service that was done (Confirm services); the invoice is then matched again",
            )  # fmt: skip
        else:
            owner, act, step = INVOICE_NEXT.get(r["code"] or "", AP)
            yield Stuck(**common, reason=reason, owner=owner, next_step=step, amount=amount, act=act, see=see)


def _unordered(db: Session) -> Iterator[Stuck]:
    see = ("grn.create", unordered.APPROVE, "po.create")
    for r in unordered.listing(db, open_only=True):
        no, state, po = r["no"], r["status"], r["po_no"]
        reason = f"Goods received without an order from {r['supplier']} ({r['item_name']}): {r['reason']}"
        common = {"kind": "unordered", "ref": no, "link": "#/receive-without-order", "since": r["received_on"]}
        if state == "pending":
            yield Stuck(
                **common, reason=reason, owner="Purchase manager", act=unordered.APPROVE, see=see,
                next_step="Approve the goods (the buyer then orders them) or reject them (they go back)",
                not_by=frozenset({str(r["posted_by"])}),
            )  # fmt: skip
        elif state == "approved":
            yield Stuck(
                **common, reason=reason + " (approved)", owner="Buyer (not who received the goods)", act="po.create",
                see=see, next_step="Raise the order after the fact at the price agreed with the supplier",
                check=partial(_not_received_by, db, r["received_by"]),
            )  # fmt: skip
        elif state == "rejected":
            yield Stuck(
                **common, reason=reason + " (rejected)", owner="Store", act="grn.create", see=see,
                next_step="Send the goods back to the supplier and record the return",
            )  # fmt: skip
        elif r["po_status"] in goods.RECEIVABLE:
            yield Stuck(
                **common, reason=reason + f" (ordered on {po})", owner="Store", act="grn.create", see=see,
                next_step=f"Post the goods receipt on {po}",
            )  # fmt: skip
        else:
            yield Stuck(
                **common, reason=reason + f" (ordered on {po}, {r['po_status']})", owner="Buyer", act="po.send",
                see=see, next_step=f"Get {po} approved and sent; the store then receives the goods on it",
            )  # fmt: skip


def _receipts(db: Session) -> Iterator[Stuck]:
    """Receipts waiting for an inspection by someone other than the receiver (SEP-16), and draft returns to dispatch."""
    for r in _rows(
        db,
        "select g.no, g.po_no, g.location_code, g.received_by, (g.gate_entry_at at time zone 'Asia/Kolkata')::date as day "
        "from goods_receipt g where g.inspection_status = 'pending' and g.status = 'posted' order by g.no",
    ):
        name = po_document.delivery_place(db, str(r["location_code"]))[0]
        yield Stuck(
            "inspection", r["no"], f"#/receiving/{r['no']}",
            f"Receipt {r['no']} of {r['po_no']} at {name} has waited for its inspection since {r['day']:%d %b %Y}",
            "Quality", "Record what the inspection accepted and rejected", r["day"],
            act="grn.inspect", see=("grn.inspect", "grn.create"), check=partial(_not_received_by, db, r["received_by"]),
        )  # fmt: skip
    for r in _rows(
        db, "select no, supplier_code, reason, created_at from supplier_return where status = 'draft' order by no"
    ):
        supplier = db.execute(
            text("select coalesce(trade_name, legal_name) from supplier where code = :c"), {"c": r["supplier_code"]}
        ).scalar()
        yield Stuck(
            "return_draft", r["no"], f"#/returns/{r['no']}",
            f"Return {r['no']} to {supplier} is a draft: {r['reason']}", "Store",
            "Give the challan and the vehicle and dispatch it", _day(r["created_at"]), act="grn.create",
            see=("grn.create", "po.create"),
        )  # fmt: skip


def _services(db: Session, today: date) -> Iterator[Stuck]:
    for r in services.open_lines(db):
        if r["need_by"] is None or r["need_by"] >= today:
            continue
        people = frozenset(str(c) for c in (r["requester"], r["on_behalf_of"]) if c)
        names = " or ".join(name_of(db, c) for c in sorted(people)) or "the requester"
        yield Stuck(
            "service", r["po_no"], f"#/purchase-orders/{r['po_no']}",
            f"Service from {r['supplier']} ({r['description']}, line {r['line_no']}) was due by "
            f"{r['need_by']:%d %b %Y} and is not confirmed",
            f"Requester: {names}", "Confirm how much of the service was done (Confirm services)", r["need_by"],
            amount=(Decimal(r["left"]) * Decimal(r["unit_price"])).quantize(Decimal("0.01")),
            people=people, see=("po.create",),
        )  # fmt: skip


def _approvals(db: Session) -> Iterator[Stuck]:
    for r in _rows(
        db,
        "select r.no, r.total_value, s.approver, s.delegate, s.opened_at, s.reminded_at from approval_step s "
        "join request r on r.no = s.request_no where s.status = 'pending' and s.round = r.approval_round "
        "and r.status = 'submitted' and s.reminded_at is not null",
    ):
        who = str(r["delegate"] or r["approver"])
        yield Stuck(
            "approval", r["no"], f"#/requisitions/{r['no']}",
            f"Waiting for approval; reminded on {_day(r['reminded_at']):%d %b %Y} and still not decided",
            f"Approver: {name_of(db, who)}", "Approve, reject or return the request", _day(r["opened_at"]),
            amount=Decimal(r["total_value"]), people=frozenset({who}), see=("request.approve",),
        )  # fmt: skip


def _proposals(db: Session) -> Iterator[Stuck]:
    holders: dict[str, str] = {}
    for p in _rows(db, "select * from proposal where status = 'pending' order by id"):
        if p["action"] in OWN_ROWS:
            continue
        need = p["required_permission"]
        if need not in holders:
            roles = [r[0] for r in db.execute(text("select role from role_permission where action = :a order by role"), {"a": need})]  # fmt: skip
            holders[need] = " or ".join(roles) or f"someone allowed {need}"
        yield Stuck(
            "proposal", f"Proposal {p['id']}", "/admin/approvals", p["summary"], holders[need],
            "Approve or reject it on Approvals", _day(p["created_at"]),
            amount=None if p["value"] is None else Decimal(p["value"]), act=need, see=(need,),
            not_by=frozenset({str(p["proposed_by_id"])}) if p["proposed_by_type"] == "user" else frozenset(),
        )  # fmt: skip


def _orders(db: Session) -> Iterator[Stuck]:
    """Orders every approver of which made them (C1): only a change to the approval rules lets them go on."""
    for r in _rows(db, "select no, total, hold_reason, created_at from po where status = 'draft' and hold_reason like :h", h=f"{NO_APPROVER}%"):  # fmt: skip
        yield Stuck(
            "order", r["no"], f"#/purchase-orders/{r['no']}", str(r["hold_reason"]), "Admin (the approval rules)",
            "Name an approver who did not make the order, then submit it again", _day(r["created_at"]),
            amount=Decimal(r["total"]), act="admin.rules", see=("admin.rules", "po.create"),
        )  # fmt: skip


def _suppliers(db: Session, today: date, calendar: BusinessCalendar) -> Iterator[Stuck]:
    ctx = alerts.Context(db, today, calendar)
    for rule, (act, step) in BUYER_ALERTS.items():
        for f in alerts.RULES[rule](ctx):
            yield Stuck(
                "supplier", f.subject_id, f.link, f"{f.title}. {f.detail}", "Buyer", step, f.since or today,
                act=act, see=(act,),
            )  # fmt: skip


def _demand(db: Session) -> Iterator[Stuck]:
    """Cases a change in the ERP's demand left: the buyer revises an order, the store returns what was issued too many."""
    for c in demand_cases.open_cases(db):
        kind = c["owner_kind"]
        code = org_code_of(db, int(c["assignee"]))
        act = demand_cases.ACTS[kind]
        yield Stuck(
            "demand", c["doc_no"], demand_cases.link(c["doc_type"], c["doc_no"]), str(c["title"]),
            f"{'Buyer' if kind == 'buyer' else 'Store'}: {name_of(db, code)}", str((c["payload"] or {}).get("next_step", "")),
            demand_cases.since(c["created_at"]), amount=None if c["amount"] is None else Decimal(c["amount"]),
            act=act, people=frozenset({code}) if code else frozenset(), see=(act,),
            case={
                "task_id": c["task_id"], "owner_kind": kind, "outcomes": list(demand_cases.OUTCOMES[kind]),
                "note_needed": list(demand_cases.NOTE_NEEDED),
            },
        )  # fmt: skip


def _late_supply(db: Session) -> Iterator[Stuck]:
    """Orders that bring what a material request needs after it needs it (rule Q17): the buyer expedites or buys
    elsewhere."""
    for c in supply_cases.listing(db, status="open"):
        code = org_code_of(db, int(c["assignee"]))
        yield Stuck(
            "late_supply", c["po_no"], f"#/purchase-orders/{c['po_no']}", str(c["title"]), f"Buyer: {name_of(db, code)}",
            str((c["payload"] or {}).get("next_step", "")), demand_cases.since(c["created_at"]),
            amount=supply_cases.amount(db, c), act=supply_cases.ACTS, people=frozenset({code}) if code else frozenset(),
            see=(supply_cases.ACTS, "stock.view"),
            case={
                "task_id": c["task_id"], "owner_kind": "buyer", "outcomes": list(supply_cases.OUTCOMES),
                "note_needed": list(supply_cases.NOTE_NEEDED), "close_url": f"/api/supply-risks/{c['task_id']}/close",
            },
        )  # fmt: skip


NEXT_BY_KIND = {  # what the person does with work an AI capability handed over (11a.5)
    "intake_email": "Read the email and raise the request by hand",
    "read_supplier_reply": "Read the supplier's reply and enter the quote on the RFQ",
    "supplier_message": "Read the supplier's email and act on the order",
    "read_invoice_email": "Read the invoice email and enter the invoice by hand",
}


AI_ACTS = {  # who does a switched-off capability's work by hand: the permission of its owners
    "intake": "rfq.create",
    "planning": "stock.view",
    "sourcing": "rfq.create",
    "execution": "po.create",
    "invoice_ap": "invoice.match",
}


def _ai_handover(db: Session) -> Iterator[Stuck]:
    """Work a switched-off AI capability gave to a person: an email to read, a workflow or a recommendation to finish."""
    rows = _rows(
        db,
        "select id, title, assignee, payload, created_at from task where kind = 'ai_handover' "
        "and status in ('open', 'overdue') order by id",
    )
    for r in rows:
        p, code = r["payload"] or {}, org_code_of(db, int(r["assignee"]))
        yield Stuck(
            "ai_handover", str(p.get("ref") or r["id"]), str(p.get("link") or "#/exceptions"), str(r["title"]),
            name_of(db, code) if code else "Not assigned",
            NEXT_BY_KIND.get(str(p.get("kind")), "Finish the work by hand, then close the task"), _day(r["created_at"]),
            act=AI_ACTS.get(str(p.get("capability"))), people=frozenset({code}) if code else frozenset(),
            see=("admin.settings", *((AI_ACTS[str(p.get("capability"))],) if str(p.get("capability")) in AI_ACTS else ())),
        )  # fmt: skip


def _sourcing_cases(db: Session, nexts: dict[str, tuple[str, str]] = SOURCING_NEXT) -> Iterator[Stuck]:
    """The sourcing cases of block 6 (an RFQ closed short, a reply not read, a quote expired before its order, a
    contract line held for path A or B) and the order cases of block 8 (an order not acknowledged after the reminders,
    an acknowledgement by email to record, an order email not read). Each is an open task of its buyer."""
    rows = _rows(
        db,
        "select id, kind, title, assignee, payload, created_at from task where kind = any(:k) "
        "and status in ('open', 'overdue') order by id",
        k=list(nexts),
    )
    for r in rows:
        p, code = r["payload"] or {}, org_code_of(db, int(r["assignee"]))
        act, step = nexts[str(r["kind"])]
        ref = str(
            p.get("rfq_no")
            or p.get("po_no")
            or (f"{p.get('request_no')}/{p.get('line_no')}" if p.get("request_no") else r["id"])
        )
        yield Stuck(
            str(r["kind"]), ref, str(p.get("link") or "#/exceptions"), str(r["title"]),
            name_of(db, code) if code else "Not assigned", step, _day(r["created_at"]), act=act,
            people=frozenset({code}) if code else frozenset(), see=(act,),
        )  # fmt: skip


def collect(db: Session, today: date, calendar: BusinessCalendar) -> list[Stuck]:
    """Every stuck case in the company, whoever must act."""
    return [
        *_invoices(db),
        *_receipts(db),
        *_unordered(db),
        *_services(db, today),
        *_approvals(db),
        *_proposals(db),
        *_suppliers(db, today, calendar),
        *_orders(db),
        *_demand(db),
        *_late_supply(db),
        *_ai_handover(db),
        *_sourcing_cases(db),
        *_sourcing_cases(db, ORDER_NEXT),
        *_sourcing_cases(db, RECEIPT_NEXT),
    ]


# ---- what one person sees ---------------------------------------------------------------------


def _may_act(s: Stuck, user_id: int, me: str | None, granted: set[str]) -> bool:
    if me is not None and me in s.people:
        return True
    if s.act is None or s.act not in granted or str(user_id) in s.not_by:
        return False
    return s.check is None or s.check(user_id)


def for_user(
    db: Session,
    user_id: int,
    today: date,
    calendar: BusinessCalendar,
    *,
    kind: str | None = None,
    mine: bool = False,
) -> list[dict[str, Any]]:
    """The rows this login may see (one kind, or only those they must act on, when asked), oldest first."""
    granted = set(permissions.granted(db, user_id))
    me = org_code_of(db, user_id)
    out: list[dict[str, Any]] = []
    overview = "audit.view" in granted  # the administrator sees every row, read only, as on the control tower
    for s in collect(db, today, calendar):
        if kind and s.kind != kind:
            continue
        if not (overview or (me is not None and me in s.people) or granted & set(s.see)):
            continue
        acts = _may_act(s, user_id, me, granted)
        if mine and not acts:
            continue
        out.append(
            {
                "kind": s.kind, "kind_title": KINDS[s.kind], "ref": s.ref, "link": s.link, "reason": s.reason,
                "owner": s.owner, "next_step": s.next_step, "since": s.since.isoformat(),
                "age_days": working_days_between(s.since, today, calendar),
                "amount": None if s.amount is None else str(s.amount), "mine": acts, "case": s.case,
            }
        )  # fmt: skip
    out.sort(key=lambda r: (-r["age_days"], r["since"], r["kind"], r["ref"]))
    return out
