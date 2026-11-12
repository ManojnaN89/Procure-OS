"""Draft purchase requests made by the platform (replenishment, the shortfall of a material request): the request in
memory, what the approval rules will do with it, writing it with its audit row, and sending it into approval.

The callers decide what to buy and at what estimate; this module only writes and routes, so every automatic request is
written the same way.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.approvals import rules as approval_rules
from p2p.approvals import workflow
from p2p.formulas.money import round_money
from p2p.platform import audit, numbering
from p2p.platform.sod import Actor


@dataclass
class DraftLine:
    item_code: str
    item_name: str
    qty: Decimal
    uom: str
    base_qty: Decimal
    est_price: Decimal
    value: Decimal
    need_by: date
    gl_code: str | None
    reason: str
    stock_status: str = ""
    category_code: str | None = None  # when the lines of one request differ (else the request's)
    stock_qty: Decimal = Decimal(0)  # stock unit: what the line buys for stock (the rest buys for material requests)


@dataclass
class DraftRequest:
    location_code: str
    category_code: str | None  # None when the lines are of several categories
    priority: str
    lines: list[DraftLine]
    no: str | None = None  # a dry run does not take a number
    department: str | None = None
    route_kind: str = ""  # auto (approves itself), approval (needs people) or blocked (the rules cannot route it)
    route: str = ""  # in words
    outcome: str | None = None  # after submitting: approved or submitted
    submit_error: str | None = None

    @property
    def total_value(self) -> Decimal:
        return sum((line.value for line in self.lines), Decimal(0))


# ---- the items: price, unit, account ----------------------------------------------------------

_ITEMS = """
    select i.code, i.name, i.active, i.stocked, i.moq, i.purchase_uom, i.base_uom, i.pack_size, i.standard_price,
           i.default_lead_days, i.category_code, coalesce(i.default_gl, c.default_gl) as gl,
           (select u.factor from uom_conversion u
             where u.item_code = i.code and u.from_uom = i.purchase_uom and u.to_uom = i.base_uom) as factor
    from item i join item_category c on c.code = i.category_code
    where i.code = any(:codes)
"""


def item_info(db: Session, codes: list[str]) -> dict[str, dict[str, Any]]:
    """What a request line needs to know about each item: units, minimum order, standard price, lead time, account."""
    return {r["code"]: dict(r) for r in db.execute(text(_ITEMS), {"codes": sorted(set(codes))}).mappings()}


def purchase_unit(item: dict[str, Any]) -> tuple[str, Decimal]:
    """The unit the item is bought in and how many stock units one of it holds."""
    unit = item["purchase_uom"] or item["base_uom"]
    factor = item["factor"] or (Decimal(item["pack_size"] or 1) if unit != item["base_uom"] else Decimal(1))
    return unit, Decimal(factor)


def planning_department(db: Session) -> str | None:
    """The department a planning request is raised for (a company setting): its head is the nearest manager."""
    value = db.execute(text("select value from app_setting where key = 'replenishment_department'")).scalar()
    return str(value) if value else None


# ---- routing, writing and submitting ----------------------------------------------------------


def route(db: Session, req: DraftRequest, as_of: date) -> None:
    """Say, without writing anything, what the approval rules will do with this request."""
    try:
        rule = approval_rules.find_rule(
            db,
            "request",
            req.total_value,
            as_of,
            spend_type="opex",
            category=req.category_code,
            department=req.department,
            location=req.location_code,
        )
        steps = approval_rules.resolve_chain(
            db, rule.roles, requester=None, department=req.department, on_date=as_of, location=req.location_code
        )
    except approval_rules.ApprovalRuleError as exc:
        req.route_kind, req.route = "blocked", str(exc)
        return
    if rule.is_automatic:
        req.route_kind, req.route = "auto", f"Approved automatically ({rule.code} v{rule.version})"
    else:
        who = " then ".join(workflow.name_of(db, st.approver) for st in steps)
        req.route_kind, req.route = "approval", f"Approval by {who} ({rule.code} v{rule.version})"


def write(
    db: Session,
    actor: Actor,
    req: DraftRequest,
    *,
    as_of: date,
    source_ref: str,
    justification: str,
    audit_extra: dict[str, Any] | None = None,
) -> str:
    """Number the request and write it with its lines as a draft (no requester: the platform raised it). The caller
    commits. Returns the number."""
    req.no = numbering.next_number(db, "request", as_of, location_code=req.location_code)
    db.execute(
        text(
            "insert into request (no, ship_to_location, type, priority, need_by, channel, source_ref, justification, "
            "status, total_value, department_code) values (:no, :loc, 'goods', :prio, :need, 'planning', :src, :why, "
            "'draft', :total, :dep)"
        ),
        {
            "no": req.no,
            "loc": req.location_code,
            "prio": req.priority,
            "need": min(line.need_by for line in req.lines),
            "src": source_ref,
            "why": justification,
            "total": req.total_value,
            "dep": req.department,
        },
    )
    for n, line in enumerate(req.lines, start=1):
        db.execute(
            text(
                "insert into request_line (request_no, line_no, item_code, qty, uom, base_qty, est_price, need_by, "
                "delivery_location, gl_code, category_code, reason, status, stock_qty) values (:no, :n, :item, :qty, :uom, "
                ":base, :price, :need, :loc, :gl, :cat, :why, 'open', :stock)"
            ),
            {
                "no": req.no,
                "n": n,
                "item": line.item_code,
                "qty": line.qty,
                "uom": line.uom,
                "base": line.base_qty,
                "price": line.est_price,
                "need": line.need_by,
                "loc": req.location_code,
                "gl": line.gl_code,
                "cat": line.category_code or req.category_code,
                "why": line.reason,
                "stock": line.stock_qty,
            },
        )
    audit.record(
        db,
        actor,
        "request.create",
        "request",
        req.no,
        after={
            "status": "draft",
            "channel": "planning",
            "location": req.location_code,
            "category": req.category_code,
            "priority": req.priority,
            "lines": len(req.lines),
            "total_value": str(req.total_value),
            **(audit_extra or {}),
        },
    )
    return req.no


def refresh(db: Session, req: DraftRequest, as_of: date) -> None:
    """After lines were combined into a written request: its lines as they now stand, and the route for its new value."""
    rows = db.execute(
        text(
            "select qty, base_qty, need_by from request_line where request_no = :n and status <> 'cancelled' "
            "order by line_no"
        ),
        {"n": req.no},
    ).all()
    for line, row in zip(req.lines, rows, strict=True):
        line.qty, line.base_qty, line.need_by = row.qty, row.base_qty, row.need_by
        line.value = round_money(line.qty * line.est_price)
    route(db, req, as_of)


def submit(db: Session, tenant: str, requests: list[DraftRequest], actor: Actor, as_of: date) -> None:
    """Send written drafts through approval. One the rules cannot route, or the budget blocks, stays a draft with the
    reason on it."""
    for req in requests:
        assert req.no is not None
        try:
            req.outcome = workflow.submit(db, tenant, req.no, actor, today=as_of).status
        except (approval_rules.ApprovalRuleError, workflow.ApprovalError) as exc:
            db.rollback()
            req.submit_error = str(exc)
