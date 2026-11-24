"""The role home, the control tower, the KPIs and the alerts, for the Procure-OS screens.

The home shows each person only what needs their action, from the roles they hold. Everything here reads existing tables:
nothing is written except by running the alert engine.
"""

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.agents import autonomy
from p2p.approvals.workflow import name_of, org_code_of
from p2p.formulas.calendar import business_today
from p2p.formulas.kpis import BUCKETS, working_days_between
from p2p.formulas.money import format_inr
from p2p.formulas.words import count
from p2p.invoices import payment
from p2p.platform import alerts, control_tower, exceptions, reference, routing
from p2p.platform.permissions import can_anywhere
from p2p.platform.rows import rows as _rows
from p2p.requests import replenishment

router = APIRouter(prefix="/api", tags=["workspace"])
Db = Annotated[Session, Depends(routing.tenant_session)]
Ctx = Annotated[routing.TenantContext, Depends(routing.tenant_context)]
SHOWN = 8  # rows listed per section; the count is always the whole number


def _section(key: str, title: str, link: str, rows: list[dict[str, Any]], total: int | None = None) -> dict[str, Any]:
    return {
        "key": key,
        "title": title,
        "link": link,
        "count": total if total is not None else len(rows),
        "rows": rows[:SHOWN],
    }


def _row(id_: str, label: str, link: str, sub: str = "") -> dict[str, Any]:
    return {"id": id_, "label": label, "sub": sub, "link": link}


# ---- the sections each role sees --------------------------------------------------------------


def _requester(db: Session, org: str | None) -> list[dict[str, Any]]:
    if org is None:
        return []
    out = []
    for key, title, status in (
        ("drafts", "My drafts", "draft"),
        ("returned", "Returned to me for changes", "returned"),
    ):
        rows = _rows(
            db,
            "select no, total_value from request where requester = :o and status = :s order by created_at desc",
            o=org,
            s=status,
        )
        out.append(
            _section(
                key,
                title,
                "#/requisitions",
                [_row(r["no"], r["no"], f"#/requisitions/{r['no']}", format_inr(r["total_value"])) for r in rows],
            )
        )
    return out


def _approver(db: Session, user_id: int, org: str | None) -> list[dict[str, Any]]:
    out = []
    if org is not None:
        rows = _rows(
            db,
            "select r.no, r.total_value from approval_step s join request r on r.no = s.request_no where s.status = 'pending' "
            "and s.round = r.approval_round and r.status = 'submitted' and (s.approver = :o or s.delegate = :o) order by r.submitted_at nulls last, r.no",
            o=org,
        )
        out.append(
            _section(
                "requests",
                "Requests waiting for my approval",
                "#/requisitions",
                [_row(r["no"], r["no"], f"#/requisitions/{r['no']}", format_inr(r["total_value"])) for r in rows],
            )
        )
        rows = _rows(
            db,
            "select p.no, p.total from po_approval_step s join po p on p.no = s.po_no where s.status = 'pending' and s.round = p.approval_round "
            "and p.status = 'submitted' and (s.approver = :o or s.delegate = :o) order by p.created_at, p.no",
            o=org,
        )
        out.append(
            _section(
                "orders",
                "Orders waiting for my approval",
                "#/purchase-orders",
                [_row(r["no"], r["no"], f"#/purchase-orders/{r['no']}", format_inr(r["total"])) for r in rows],
            )
        )
    # decisions that need a person with the right permission, but never the person who asked
    waiting = _rows(
        db,
        "select p.id, p.action, p.summary, p.proposed_by_id, p.proposed_by_type from proposal p where p.status = 'pending' and exists "
        "(select 1 from user_role ur join role_permission rp on rp.role = ur.role where ur.user_id = :u and rp.action = p.required_permission) "
        "order by p.id",
        u=user_id,
    )
    titles = {
        "payment_run.approve": ("payment_runs", "Payment runs to approve"),
        "invoice.release_block": ("releases", "Blocked invoices to release"),
        "supplier.set_status": ("suppliers", "Supplier changes to approve"),
        "po.accept_variance": ("variances", "Orders held for a price or ceiling"),
        "rfq.send": ("rfq_sends", "RFQs waiting to be sent"),
    }
    grouped: dict[str, list[dict[str, Any]]] = {}
    for p in waiting:
        if p["proposed_by_type"] == "user" and p["proposed_by_id"] == str(user_id):
            continue
        if p["action"] == "invoice.release_block":
            entered = db.execute(
                text(
                    "select entered_by from invoice where no = (select payload->>'invoice' from proposal where id = :i)"
                ),
                {"i": p["id"]},
            ).scalar()
            if entered == str(user_id):
                continue
        grouped.setdefault(p["action"], []).append(p)
    for action, (key, title) in titles.items():
        if action in grouped:
            where = "#/rfqs" if action == "rfq.send" else "/admin/approvals"  # RFQs are confirmed from their list (D42)
            rows = [_row(str(p["id"]), p["summary"][:90], where) for p in grouped[action]]
            out.append(_section(key, title, where, rows))
    return out + _awards_and_contracts(db, user_id, org)


def _awards_and_contracts(db: Session, user_id: int, org: str | None) -> list[dict[str, Any]]:
    """Block 5: the period awards waiting for this person by name, and the contracts from awards that the supplier has
    accepted and this person may activate (not its maker, not its award's approver)."""
    out = []
    if org is not None:
        rows = _rows(
            db,
            "select rfq_no, value_total from award_decision where status = 'waiting' and (approver = :o or delegate = :o) "
            "order by proposed_at",
            o=org,
        )
        out.append(
            _section(
                "awards",
                "Awards waiting for you",
                "#/rfqs",
                [_row(r["rfq_no"], r["rfq_no"], f"#/rfqs/{r['rfq_no']}", format_inr(r["value_total"])) for r in rows],
            )
        )
    rows = _rows(
        db,
        "select c.code, c.max_value from contract c left join award_decision d on d.id = c.award_decision_id "
        "where c.status = 'draft' and c.supplier_accepted_at is not null and c.created_by is distinct from :u "
        "and d.decided_by is distinct from :u and exists (select 1 from user_role ur join role_permission rp "
        "on rp.role = ur.role where ur.user_id = cast(:u as int) and rp.action = 'contract.activate') order by c.code",
        u=str(user_id),
    )
    out.append(
        _section(
            "contracts_to_activate",
            "Contracts to activate",
            "#/contracts",
            [_row(r["code"], r["code"], f"#/contracts/{r['code']}", format_inr(r["max_value"] or 0)) for r in rows],
        )
    )
    return out


def _buyer(db: Session) -> list[dict[str, Any]]:
    out = []
    rows = _rows(
        db,
        "select r.no, r.total_value from request r where r.status = 'approved' and exists (select 1 from request_line l where l.request_no = r.no "
        "and l.status = 'open' and not exists (select 1 from sourcing_decision d where d.request_no = l.request_no and d.line_no = l.line_no)) order by r.decided_at nulls last",
    )
    out.append(
        _section(
            "to_source",
            "Approved requests to source",
            "#/requisitions",
            [_row(r["no"], r["no"], f"#/requisitions/{r['no']}", format_inr(r["total_value"])) for r in rows],
        )
    )
    for key, title, quoted in (
        ("rfq_wait", "RFQs waiting for quotes", False),
        ("rfq_award", "RFQs with quotes to compare and award", True),
    ):
        rows = _rows(
            db,
            "select f.no from rfq f where f.status = 'sent' and "  # noqa: S608 - fixed fragments only
            + ("exists" if quoted else "not exists")
            + " (select 1 from quote q where q.rfq_no = f.no) order by f.sent_at",
        )
        out.append(_section(key, title, "#/rfqs", [_row(r["no"], r["no"], f"#/rfqs/{r['no']}") for r in rows]))
    rows = _rows(db, "select no, total from po where status = 'approved' and hold_reason is null order by approved_at")
    out.append(
        _section(
            "to_send",
            "Approved orders to send to the supplier",
            "#/purchase-orders",
            [_row(r["no"], r["no"], f"#/purchase-orders/{r['no']}", format_inr(r["total"])) for r in rows],
        )
    )
    rows = _rows(db, "select no, total from po where status = 'sent' order by sent_at")
    out.append(
        _section(
            "to_acknowledge",
            "Sent orders waiting for the supplier's acknowledgement",
            "#/purchase-orders",
            [_row(r["no"], r["no"], f"#/purchase-orders/{r['no']}", format_inr(r["total"])) for r in rows],
        )
    )
    rows = _rows(
        db,
        "select d.id, d.rfq_no, d.status from quote_draft d where d.status in ('pending', 'needs_person') order by d.id",
    )
    out.append(
        _section(
            "drafts",
            "Supplier replies to check",
            "#/rfqs",
            [
                _row(str(r["id"]), f"{r['rfq_no']}: {r['status'].replace('_', ' ')}", f"#/rfqs/{r['rfq_no']}")
                for r in rows
            ],
        )
    )
    # what a supplier wrote about an order or a quotation request that the agent left for a person
    rows = _rows(
        db,
        "select m.id, m.linked_type, m.linked_ref, e.subject from supplier_message m join email_message e on e.id = m.email_id "
        "left join task t on t.id = m.task_id where m.status in ('needs_person', 'task') "
        "and (m.task_id is null or t.status in ('open', 'overdue')) order by m.id desc limit 50",
    )
    out.append(
        _section(
            "supplier_messages",
            "Supplier messages to read",
            "#/purchase-orders",
            [
                _row(
                    str(r["id"]),
                    f"{r['linked_ref']}: {r['subject'] or 'no subject'}"[:90],
                    f"#/rfqs/{r['linked_ref']}"
                    if r["linked_type"] == "rfq"
                    else f"#/purchase-orders/{r['linked_ref']}",
                )
                for r in rows
            ],
        )
    )
    return out


def _store(db: Session) -> list[dict[str, Any]]:
    rows = _rows(
        db,
        "select p.no, coalesce(s.trade_name, s.legal_name) as supplier from po p join supplier s on s.code = p.supplier_code "
        "where p.status in ('sent', 'acknowledged', 'part_received') and exists (select 1 from po_line l where l.po_no = p.no and l.status in ('open', 'part_received')) order by p.sent_at nulls last",
    )
    out = [
        _section(
            "to_receive",
            "Orders with goods to come",
            "#/receiving",
            [_row(r["no"], r["no"], "#/receiving", r["supplier"]) for r in rows],
        )
    ]
    rows = _rows(db, "select no, po_no from goods_receipt where inspection_status = 'pending' order by created_at")
    out.append(
        _section(
            "inspections",
            "Receipts waiting for inspection",
            "#/receiving",
            [_row(r["no"], r["no"], f"#/receiving/{r['no']}", r["po_no"]) for r in rows],
        )
    )
    return out


def _ap(db: Session) -> list[dict[str, Any]]:
    rows = _rows(
        db,
        "select l.po_no, count(*) as lines from po_line l join po on po.no = l.po_no where l.received_qty > l.invoiced_qty and l.status <> 'cancelled' "
        "and po.status not in ('draft', 'submitted', 'cancelled') "
        "and not exists (select 1 from invoice_line il join invoice i on i.no = il.invoice_no where il.po_no = po.no "
        "and i.type = 'invoice' and i.status = 'blocked' and i.payment_block <> 'duplicate') "
        "group by l.po_no order by l.po_no",
    )
    out = [
        _section(
            "to_invoice",
            "Goods received, waiting for an invoice",
            "#/invoices/new",
            [_row(r["po_no"], r["po_no"], "#/invoices/new", count(r["lines"], "line")) for r in rows],
        )
    ]
    rows = _rows(
        db,
        "select no, supplier_invoice_no, block_reason from invoice where status = 'blocked' and payment_block <> 'duplicate' and type = 'invoice' order by received_on",
    )
    out.append(
        _section(
            "blocked",
            "Blocked invoices to work",
            "#/invoices",
            [
                _row(r["no"], r["supplier_invoice_no"], f"#/invoices/{r['no']}", (r["block_reason"] or "")[:80])
                for r in rows
            ],
        )
    )
    return out


def _finance(db: Session, today: date) -> list[dict[str, Any]]:
    due = payment.due_invoices(db, today)
    out = [
        _section(
            "due",
            "Invoices due: plan a payment run",
            "#/payments",
            [_row(d["no"], d["no"], "#/payments", f"due {d['due_date']:%d %b}") for d in due],
        )
    ]
    for key, title, status in (
        ("approve_wait", "Runs waiting for the owner's approval", "proposed"),
        ("to_file", "Approved runs: make the bank file", "approved"),
        ("to_pay", "Runs sent to the bank: record the payment", "file_generated"),
    ):
        rows = _rows(db, "select no, total from payment_run where status = :s order by run_date", s=status)
        out.append(
            _section(
                key,
                title,
                "#/payments",
                [_row(r["no"], r["no"], f"#/payments/{r['no']}", format_inr(r["total"])) for r in rows],
            )
        )
    return out


def _restock(db: Session, today: date) -> dict[str, Any]:
    """Stock at its reorder point with no request or order yet: what "Check stock levels" would raise, shown up front."""
    drafts, skipped = replenishment.plan(db, today)
    rows = [
        _row(
            f"{d.location_code}-{ln.item_code}",
            f"{ln.item_name}",
            "#/requisitions",
            f"{ln.reason} · order {ln.qty:,.0f} {ln.uom}",
        )
        for d in drafts
        for ln in d.lines
    ]
    return _section("restock", "Stock to reorder, nothing requested yet", "#/requisitions", rows)


def _admin(db: Session, today: date) -> list[dict[str, Any]]:
    """Everything in the flow at once, read only: every approval that is waiting and on whom, then each stage in order."""
    out = [_restock(db, today)]
    rows = _rows(
        db,
        "select r.no, r.total_value, s.approver, s.delegate from approval_step s join request r on r.no = s.request_no "
        "where s.status = 'pending' and s.round = r.approval_round and r.status = 'submitted' order by r.submitted_at nulls last, r.no",
    )
    out.append(
        _section(
            "requests",
            "Requests waiting for approval",
            "#/requisitions",
            [
                _row(
                    r["no"],
                    r["no"],
                    f"#/requisitions/{r['no']}",
                    f"{format_inr(r['total_value'])} · waits for {name_of(db, r['delegate'] or r['approver'])}",
                )
                for r in rows
            ],
        )
    )
    rows = _rows(
        db,
        "select p.no, p.total, s.approver, s.delegate from po_approval_step s join po p on p.no = s.po_no where s.status = 'pending' "
        "and s.round = p.approval_round and p.status = 'submitted' order by p.created_at, p.no",
    )
    out.append(
        _section(
            "orders",
            "Orders waiting for approval",
            "#/purchase-orders",
            [
                _row(
                    r["no"],
                    r["no"],
                    f"#/purchase-orders/{r['no']}",
                    f"{format_inr(r['total'])} · waits for {name_of(db, r['delegate'] or r['approver'])}",
                )
                for r in rows
            ],
        )
    )
    titles = {
        "rfq.send": ("rfq_sends", "RFQs waiting to be approved and sent"),
        "po.accept_variance": ("variances", "Orders held for a price or ceiling"),
        "invoice.release_block": ("releases", "Blocked invoices waiting for release"),
        "payment_run.approve": ("payment_runs", "Payment runs waiting for approval"),
        "supplier.set_status": ("suppliers", "Supplier changes waiting for approval"),
    }
    grouped: dict[str, list[dict[str, Any]]] = {}
    for p in _rows(
        db, "select id, action, summary, required_permission from proposal where status = 'pending' order by id"
    ):
        grouped.setdefault(p["action"], []).append(p)
    for action, (key, title) in titles.items():
        found = grouped.get(action, [])
        out.append(
            _section(
                key,
                title,
                "/admin/approvals",
                [
                    _row(
                        str(p["id"]),
                        p["summary"][:90],
                        "/admin/approvals",
                        f"for a person who may: {autonomy.label(p['required_permission']).lower()}",
                    )
                    for p in found
                ],
            )
        )
    out += _buyer(db)
    out += _store(db)
    out += _ap(db)
    out += _finance(db, today)
    return out


def _bill_tasks(db: Session, user_id: int) -> list[dict[str, Any]]:
    """The bills of block 10 whose authorisation or approval is this login's to give (their own tasks)."""
    out = []
    for kind, key, title in (
        ("invoice_authorise", "bills_to_authorise", "Bills to authorise"),
        ("invoice_approve", "bills_to_approve", "Bills to approve"),
    ):
        rows = _rows(
            db,
            "select id, title, payload from task where kind = :k and assignee = :u and status in ('open', 'overdue') "
            "order by due_at",
            k=kind,
            u=user_id,
        )
        out.append(
            _section(
                key,
                title,
                "#/invoices",
                [
                    _row(
                        str(r["id"]),
                        r["title"][:90],
                        f"#/invoices/{(r['payload'] or {}).get('invoice_no', '')}",
                    )
                    for r in rows
                ],
            )
        )
    return out


def _flow(db: Session, today: date, restock_count: int) -> list[dict[str, Any]]:
    """The steps of the purchase flow in order with how many cases sit in each, for the administrator's strip."""
    stages = control_tower.tower(db, today, reference.calendar_for(db))["stages"]
    closed = db.execute(text("select count(*) from po where status = 'closed'")).scalar() or 0
    steps = [{"key": "restock", "title": "Stock to reorder", "count": restock_count, "link": "#/requisitions"}]
    unsent = (
        db.execute(text("select count(*) from proposal where status = 'pending' and action = 'rfq.send'")).scalar() or 0
    )
    for s in stages:
        steps.append({"key": s["stage"], "title": s["title"], "count": s["count"], "link": "#/control-tower"})
        if s["stage"] == "sourcing":  # the quotation request is made, and waits for approval before it is sent
            steps.append(
                {"key": "rfq_unsent", "title": "RFQ waiting to be sent", "count": int(unsent), "link": "#/rfqs"}
            )
    steps.append({"key": "closed", "title": "Paid and closed", "count": int(closed), "link": "#/payments"})
    return steps


# ---- the endpoints ----------------------------------------------------------------------------


def _roles(db: Session, user_id: int) -> list[str]:
    return [
        r[0]
        for r in db.execute(text("select distinct role from user_role where user_id = :u order by 1"), {"u": user_id})
    ]


@router.get("/workspace")
def workspace(db: Db, ctx: Ctx) -> dict[str, Any]:
    """What needs this person's action, by role, and the open alerts for their roles. Sections with nothing in them are left out."""
    roles = _roles(db, ctx.user_id)
    org = org_code_of(db, ctx.user_id)
    today = business_today()
    sections: list[dict[str, Any]] = []
    sections += _bill_tasks(db, ctx.user_id)
    if "Requester" in roles:
        sections += _requester(db, org)
    if "Approver" in roles:
        sections += _approver(db, ctx.user_id, org)
    if "Admin" in roles and not {"Approver", "Buyer", "Finance"} & set(roles):
        sections += _admin(db, today)
    if "Buyer" in roles:
        sections.append(_restock(db, today))
        sections += _buyer(db)
        sections += _approver_proposals_only(db, ctx.user_id, "Buyer", roles)
    if "Store" in roles:
        sections += _store(db)
    if "AP" in roles:
        sections += _ap(db)
    if "Finance" in roles:
        sections += _finance(db, today)
        sections += _approver_proposals_only(db, ctx.user_id, "Finance", roles)
    seen: set[str] = set()
    unique = []
    for s in sections:
        if s["key"] in seen:
            continue
        seen.add(s["key"])
        unique.append(s)
    shown = [s for s in unique if s["count"] > 0]
    mine = alerts.open_alerts(db, roles)
    flow = (
        _flow(db, today, next((x["count"] for x in unique if x["key"] == "restock"), 0)) if "Admin" in roles else None
    )
    return {
        "flow": flow,
        "roles": roles,
        "as_of": today.isoformat(),
        "sections": shown,
        "total": sum(s["count"] for s in shown),
        "alerts": [
            {
                "id": a["id"],
                "severity": a["severity"],
                "title": a["title"],
                "detail": a["detail"],
                "link": a["link"],
                "rule": a["rule"],
                "since": a["opened_at"].isoformat(),
            }
            for a in mine
        ],
    }


def _approver_proposals_only(db: Session, user_id: int, role: str, roles: list[str]) -> list[dict[str, Any]]:
    """A role that is not an approver may still hold the right to approve some proposals (for example a buyer sends RFQs)."""
    if "Approver" in roles:
        return []
    return [
        s
        for s in _approver(db, user_id, None)
        if s["key"] in {"rfq_sends", "payment_runs", "releases", "suppliers", "variances"}
    ]


@router.get("/control-tower")
def control_tower_summary(db: Db, ctx: Ctx, location: str | None = None) -> dict[str, Any]:
    return control_tower.tower(db, business_today(), reference.calendar_for(db), location)


@router.get("/control-tower/{stage}")
def control_tower_stage(
    db: Db, ctx: Ctx, stage: str, bucket: str | None = None, location: str | None = None
) -> dict[str, Any]:
    if stage not in {k for k, _ in control_tower.STAGES}:
        raise HTTPException(404, "Not found")
    if bucket and bucket not in BUCKETS:
        raise HTTPException(422, f"bucket must be one of {', '.join(BUCKETS)}")
    title = dict(control_tower.STAGES)[stage]
    rows = control_tower.drill(db, stage, business_today(), reference.calendar_for(db), bucket, location)
    return {"stage": stage, "title": title, "rows": rows}


@router.get("/exceptions")
def exception_list(db: Db, ctx: Ctx, kind: str | None = None, mine: bool = False) -> dict[str, Any]:
    """Everything stuck that needs a person, as far as this login may see it, oldest first; one kind, or only their own."""
    if kind and kind not in exceptions.KINDS:
        raise HTTPException(422, f"kind must be one of {', '.join(exceptions.KINDS)}")
    today = business_today()
    rows = exceptions.for_user(db, ctx.user_id, today, reference.calendar_for(db), kind=kind, mine=mine)
    return {
        "as_of": today.isoformat(),
        "kinds": [{"key": k, "title": t} for k, t in exceptions.KINDS.items()],
        "rows": rows,
    }


@router.get("/kpis")
def kpis(db: Db, ctx: Ctx) -> dict[str, Any]:
    return control_tower.kpis(db, reference.calendar_for(db))


@router.get("/alerts")
def my_alerts(db: Db, ctx: Ctx) -> list[dict[str, Any]]:
    roles = _roles(db, ctx.user_id)
    return [
        {
            "id": a["id"],
            "rule": a["rule"],
            "severity": a["severity"],
            "title": a["title"],
            "detail": a["detail"],
            "link": a["link"],
            "since": a["opened_at"].isoformat(),
            "times_opened": a["times_opened"],
        }
        for a in alerts.open_alerts(db, roles)
    ]


@router.post("/alerts/run")
def run_alerts(db: Db, ctx: Ctx) -> dict[str, Any]:
    """Run the alert rules now (an administrator or a buyer). The worker also runs them every morning."""
    if not (can_anywhere(db, ctx.user_id, "admin.settings") or can_anywhere(db, ctx.user_id, "rfq.send")):
        raise HTTPException(403, "Not allowed: running the alert rules")
    done = alerts.run(db, ctx.slug)
    alerts.schedule(ctx.slug)  # the daily run keeps itself going once it has started
    return {"opened": done.opened, "resolved": done.resolved, "open": done.open_now, "failed_rules": done.failed_rules}


_ = working_days_between
