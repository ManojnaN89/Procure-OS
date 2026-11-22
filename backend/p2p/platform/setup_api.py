"""Company setup: who the people are and what they may do, who approves what, the company's policies and tolerances, and
how much the platform may do without a person. All of it is data an administrator changes on screen.

Every change needs a reason and writes two things: an audit row and a row in the setup history (who, when, old value,
new value, why). A decision made earlier stays explainable, because the value in force at any time can be read back from
the history. Admin is a setup role: it can change this but holds no business permission, and it cannot give itself one.

A change to a control (roles, permissions, limits, who approves, approval rules, policies, tolerances, automation, the
kill switch going off, whether a person may log in, a person's manager) is proposed by one administrator and applied
when another approves it (D-22, `setting_changes`): the endpoint checks it and answers 202 with the proposal. Stopping
automation and adding a person who is not an administrator apply at once.
"""

import json
import re
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.agents import autonomy, planning
from p2p.approvals.rules import chain_roles
from p2p.formulas.calendar import business_today
from p2p.invoices import tolerances as tolerance_versions
from p2p.platform import audit, routing, setting_changes, tenants, users
from p2p.platform.permissions import require
from p2p.platform.rows import rows as _rows
from p2p.platform.setting_changes import Change
from p2p.platform.sod import Actor
from p2p.platform.sod import user as user_actor
from p2p.requests import jobs as request_jobs
from p2p.requests import material
from p2p.sourcing import templates

router = APIRouter(prefix="/api/setup", tags=["setup"])
Db = Annotated[Session, Depends(routing.tenant_session)]
Rows = list[dict[str, Any]]
D = Decimal

PeopleAdmin = Annotated[routing.TenantContext, Depends(require("admin.users"))]
RulesAdmin = Annotated[routing.TenantContext, Depends(require("admin.rules"))]
SettingsAdmin = Annotated[routing.TenantContext, Depends(require("admin.settings"))]
AuditViewer = Annotated[routing.TenantContext, Depends(require("audit.view"))]

# ---- what can be set --------------------------------------------------------------------------

PERMISSIONS: dict[str, str] = {
    "request.create": "Raise a purchase request",
    "request.view_own": "See the requests they raised",
    "request.cancel_own": "Cancel their own requests",
    "request.submit": "Submit requests for approval",
    "request.view": "See every request",
    "request.approve": "Approve requests",
    "rfq.create": "Prepare requests for quotation",
    "rfq.send": "Send requests for quotation",
    "po.create": "Create purchase orders",
    "po.send": "Send purchase orders to suppliers",
    "po.approve": "Approve purchase orders",
    "supplier.view": "See suppliers",
    "supplier.propose": "Ask for a supplier to be activated, blocked or reviewed",
    "supplier.activate": "Approve a supplier's status change",
    "sourcing.route_approve": "Agree to a direct buy or a looser buying route",
    "supplier.documents": "Add a supplier's documents",
    "supplier.verify_docs": "Verify a supplier's documents against the originals",
    "supplier.verify_bank": "Verify a supplier's bank account against its proof",
    "contract.manage": "Store contracts and their price lists",
    "contract.activate": "Activate a contract",
    "benchmark.enter": "Enter or upload market benchmarks",
    "benchmark.verify": "Verify or reject market benchmarks others entered",
    "price.view": "See an item's prices side by side",
    "grn.create": "Receive and inspect goods",
    "grn.unordered_approve": "Approve goods received without an order",
    "stock.view": "See stock",
    "stock.issue": "Issue stock for use",
    "stock.adjust": "Correct stock after a count",
    "stock.quality": "Hold stock in quarantine or blocked, and decide on held stock",
    "replenishment.run": "Run automatic replenishment",
    "demand.import": "Load production orders and other demand from the ERP",
    "material.manage": "Run the store check, issue and cancel material requests",
    "invoice.view": "See invoices",
    "invoice.book": "Enter invoices",
    "invoice.match": "Work on blocked invoices",
    "invoice.approve": "Approve the release of a blocked invoice",
    "payment.view": "See payments",
    "payment.propose": "Propose payment runs",
    "payment.approve": "Approve payment runs",
    "budget.view": "See budgets",
    "budget.manage": "Lock budget periods, close the year, adjust budgets",
    "admin.users": "Manage people and roles",
    "admin.rules": "Manage approval chains and approvers",
    "admin.settings": "Manage policies and automation",
    "audit.view": "See the audit log",
    "company.cutover": "Load open documents at cutover",
}
ADMIN_ONLY = {"admin.users", "admin.rules", "admin.settings"}
ADMIN_REQUIRED = ADMIN_ONLY | {"audit.view"}  # without these nobody could run the setup any more
APPROVER_SOURCES = ("requester_manager", "department_head", "department_head_of", "designation", "location_head")


# Only settings that some code reads are offered here: a setting that nothing reads would be a control that does nothing.
# key -> (where it is stored, kind, label, help, default, low, high, choices)
POLICIES: dict[str, tuple[str, str, str, str, str, float | None, float | None, tuple[str, ...]]] = {
    "identity_check_methods": (
        "org",
        "list",
        "Identity check methods for a prospect",
        "How a person other than the buyer checks a new supplier's identity before its quote counts (D-03)",
        "pan_and_gstin_online,gst_certificate_seen,call_back_registered_number",
        None,
        None,
        (),
    ),
    "prospect_qualification_days": (
        "org",
        "int",
        "Days a prospect has to qualify (working days)",
        "A conditional award lapses when the supplier is not activated within this many working days",
        "10",
        1,
        90,
        (),
    ),
    "public_mail_domains": (
        "org",
        "list",
        "Public mail domains",
        "Email domains anyone can use; two suppliers sharing one of these are not flagged as related",
        "gmail.com,yahoo.com,yahoo.co.in,outlook.com,hotmail.com,rediffmail.com,icloud.com",
        None,
        None,
        (),
    ),
    "budget_policy": (
        "org",
        "choice",
        "Over-budget requests",
        "What happens when a request is over its budget",
        "warn",
        None,
        None,
        ("warn", "block"),
    ),
    "auto_approve_requires_budget": (
        "org",
        "bool",
        "Auto-approval needs budget",
        "A request that is over budget never approves itself",
        "true",
        None,
        None,
        (),
    ),
    "budget_warn_pct": (
        "org",
        "decimal",
        "Budget warning at (% used)",
        "A request that brings a budget to this share used or more says so to the approver",
        "90",
        0,
        100,
        (),
    ),
    "budget_block_pct": (
        "org",
        "decimal",
        "Over budget above (% used)",
        "A request that brings a budget above this share used is over budget (blocked or warned, as set above)",
        "100",
        50,
        200,
        (),
    ),
    "combine_window_days": (
        "org",
        "int",
        "Combine planned lines needed within (days)",
        "Shortfall and restock lines for the same item and delivery place, needed within this many days of each other, "
        "are bought on one requisition line while it is a draft (0: only the same day)",
        "7",
        0,
        60,
        (),
    ),
    "default_rfq_response_days": (
        "org",
        "int",
        "Days suppliers get to quote",
        "Working days from sending a request for quotation",
        "5",
        1,
        60,
        (),
    ),
    "rfq_reminder_after_days": (
        "org",
        "int",
        "Remind a silent supplier after",
        "Working days; 0 switches reminders off",
        "2",
        0,
        30,
        (),
    ),
    "rfq_max_reminders": (
        "app",
        "int",
        "Most reminders to one supplier",
        "Reminders stop after this many",
        "2",
        0,
        10,
        (),
    ),
    "demand_change_case_days": (
        "app",
        "int",
        "Days to act on a demand change",
        "Working days the buyer or the store has to act on a case a change in the ERP's demand left (then it escalates)",
        "2",
        1,
        30,
        (),
    ),
    "rfq_supplier_count": (
        "app",
        "int",
        "Suppliers asked to quote",
        "How many suppliers a quotation request goes to",
        "3",
        1,
        20,
        (),
    ),
    "min_quotes_default": (
        "org",
        "int",
        "Quotes wanted",
        "The fewest quotes that can be awarded without a reason",
        "2",
        1,
        10,
        (),
    ),
    "rfq_email_template": (
        "org",
        "text",
        "Wording of the quotation request email",
        "Fields filled in for each supplier: "
        + ", ".join(f"{{{k}}} ({v})" for k, v in templates.FIELDS.items())
        + ". {items} and {reference} must be there.",
        templates.DEFAULT_RFQ_TEMPLATE,
        None,
        None,
        (),
    ),
    "low_price_flag_pct": (
        "org",
        "int",
        "Price that looks too good (percent below)",
        "A quote this far below the other quotes or the last price paid needs a question to the supplier first (0 switches it off)",
        "40",
        0,
        95,
        (),
    ),
    "score_weight_price": (
        "org",
        "int",
        "Weight of price in the vendor score",
        "Shares of price, delivery and quality are taken in proportion to these three weights (quotes are compared by the versioned supplier selection rule)",
        "40",
        0,
        100,
        (),
    ),
    "score_weight_delivery": ("org", "int", "Weight of delivery record", "", "40", 0, 100, ()),
    "score_weight_quality": ("org", "int", "Weight of quality record", "", "20", 0, 100, ()),
    "supplier_ack_due_days": (
        "org",
        "int",
        "Order acknowledgement due after",
        "Working days after sending before an order is overdue",
        "2",
        0,
        30,
        (),
    ),
    "supplier_date_days": (
        "org",
        "int",
        "Late delivery date accepted",
        "Days a supplier's promised date may be later than needed",
        "3",
        0,
        60,
        (),
    ),
    "delivery_reminder_before_days": (
        "org",
        "int",
        "Remind a supplier before delivery (working days)",
        "One email this many working days before the promised date",
        "2",
        0,
        30,
        (),
    ),
    "delivery_overdue_after_days": (
        "org",
        "int",
        "Remind about a missed delivery after (working days)",
        "The first reminder when nothing has arrived by the promised date",
        "1",
        0,
        30,
        (),
    ),
    "delivery_overdue_every_days": (
        "org",
        "int",
        "Then remind every (working days)",
        "Until the goods arrive or the maximum is reached",
        "3",
        1,
        30,
        (),
    ),
    "delivery_overdue_max": ("org", "int", "Reminders about a missed delivery, at most", "", "3", 0, 10, ()),
    "manual_mark_paid": (
        "org",
        "bool",
        "Payments recorded by hand",
        "Off until settlement evidence is built: a run is settled from the bank statement. Demo companies only",
        "false",
        None,
        None,
        (),
    ),
    "po_send_requires_confirmation": (
        "org",
        "bool",
        "A person sends orders",
        "Off: approved orders go to the supplier by themselves",
        "true",
        None,
        None,
        (),
    ),
    "price_band_pct": (
        "org",
        "decimal",
        "Price above estimate held at (%)",
        "An order priced more than this above the estimate is held",
        "15",
        0,
        100,
        (),
    ),
    "approval_reminder_hours": ("org", "int", "Remind an order approver after (hours)", "", "24", 1, 720, ()),
    "approval_reminder_days": (
        "org",
        "decimal",
        "Remind a request approver after (working days)",
        "One reminder by email, counted on the business calendar",
        "1",
        0.5,
        30,
        (),
    ),
    "approval_escalation_days": (
        "org",
        "decimal",
        "Escalate a request approval after (working days)",
        "To the approver's manager, else the next approver",
        "2",
        0.5,
        60,
        (),
    ),
    "approval_otp_above": (
        "org",
        "decimal",
        "One-time code to approve an order above (Rs)",
        "Approving a purchase order above this value needs a code sent to the approver. Every decision from an email "
        "link needs a code whatever the value.",
        "500000.00",
        0,
        999999999,
        (),
    ),
    "supplier_document_warn_days": ("org", "int", "Warn before a document expires (days)", "", "30", 0, 365, ()),
    "bank_verification_methods": (
        "org",
        "list",
        "Ways a supplier bank account may be verified",
        "Comma-separated, for example cancelled_cheque, bank_letter, penny_drop",
        "cancelled_cheque,bank_letter,penny_drop",
        None,
        None,
        (),
    ),
    "payment_run_window_days": (
        "org",
        "int",
        "Payment run window (days)",
        "Invoices due within this many days of the run date go in the run",
        "7",
        0,
        60,
        (),
    ),
    "msme_max_days": (
        "org",
        "int",
        "Small supplier: most days agreed in writing",
        "MSMED Act section 15: at most 45 days from acceptance",
        "45",
        1,
        45,
        (),
    ),
    "msme_no_agreement_days": (
        "org",
        "int",
        "Small supplier: days without a written agreement",
        "MSMED Act section 15: 15 days from acceptance",
        "15",
        1,
        45,
        (),
    ),
    "msme_warn_days": (
        "org",
        "int",
        "Small supplier: warn before the deadline (days)",
        "Invoices reaching the deadline within this many days are listed",
        "7",
        0,
        45,
        (),
    ),
    "tds_applicable_on_goods": (
        "org",
        "bool",
        "Tax at source on goods (194Q)",
        "Applies when the company's turnover is above the limit",
        "true",
        None,
        None,
        (),
    ),
    "duplicate_window_days": ("org", "int", "Duplicate request window (days)", "", "14", 0, 90, ()),
    "request_mailbox": (
        "org",
        "email",
        "Requests mailbox",
        "Mail sent to this address from a colleague is read as a purchase request (leave empty for none)",
        "",
        None,
        None,
        (),
    ),
    "invoice_receipt_mailbox": (
        "org",
        "email",
        "Invoices mailbox",
        "Invoices that suppliers send to this address are read into drafts for AP (leave empty for none)",
        "",
        None,
        None,
        (),
    ),
    "auto_replenishment": (
        "app",
        "bool",
        "Automatic replenishment",
        "Create requests every morning for stock at its reorder point",
        "false",
        None,
        None,
        (),
    ),
    "receiving_buffer_days": (
        "app",
        "int",
        "Receiving buffer (working days)",
        "Supply counts for a need only when it is due this many working days before the need-by date (rule Q17)",
        "0",
        0,
        10,
        (),
    ),
    "late_supply_case_days": (
        "app",
        "int",
        "Days to act on supply promised too late (working days)",
        "A buyer's case for an order promised after the need is due this many working days after it opens",
        "2",
        1,
        10,
        (),
    ),
    "auto_store_check": (
        "app",
        "bool",
        "Daily store check",
        "Set stock aside and raise shortfall requests every morning at 05:30, before replenishment",
        "true",
        None,
        None,
        (),
    ),
    "store_check_submit": (
        "app",
        "bool",
        "Send automatic shortfall requests for approval",
        "Shortfall requests made by the daily store check or after an event go into approval at once; when off they "
        "wait as drafts for the requester or planner to submit",
        "false",
        None,
        None,
        (),
    ),
}
TOLERANCES: dict[str, tuple[str, str, float | None, float | None]] = {
    "price_pct": ("Invoice price above the order price (%)", "decimal", 0, 100),
    "price_abs": ("Or by this many rupees on the line", "decimal", 0, None),
    "qty_pct": ("Invoice quantity above what was received (%)", "decimal", 0, 100),
    "receipt_over_pct": ("Goods received above the quantity ordered (%)", "decimal", 0, 100),
    "small_diff_abs": ("Small invoice difference accepted (rupees)", "decimal", 0, None),
    "supplier_change_pct": ("Supplier's quantity change noted by the agent (%)", "decimal", 0, 100),
    "supplier_price_pct": ("Supplier's price rise a buyer may accept (%)", "decimal", 0, 100),
    "supplier_date_days": ("Supplier's later delivery date accepted (days)", "int", 0, 365),
}

# decision type -> (label, what it does, how its level is stored)
_HELP = {
    "rfq.send": "Emails the suppliers chosen for a line",
    "rfq.award": "Awards a quotation when there is a clear winner",
    "quote.read": "Turns a clean reading of a supplier's reply into a quote",
    "po.send": "Emails the order to the supplier",
    "po.issue": "Issues and sends a contract or safe-reorder order within the buyer's tier and the touchless limit, "
    "with no buyer",
    "invoice.read": "Enters a clean reading of a supplier's invoice without AP",
    "supplier.change": "Moves a promised date within the accepted days; quantities are only noted, prices always go to "
    "a buyer",
    "supplier.remind": "Reminds a supplier that has not acknowledged an order, or still owes part of a delivery",
    "planning.explain": "Writes a plain-words explanation of a material request's shortages from the engine's figures "
    "(Act within the limits turns it on; it never changes stock, requests or orders)",
}
DECISIONS: dict[str, tuple[str, str]] = {dt: (autonomy.LABELS[dt], help_) for dt, help_ in _HELP.items()}
LOCKED_LABELS = {
    dt: autonomy.LABELS[dt] for dt in ("payment_run.approve", "supplier.set_status", "invoice.release_block")
}
LEVELS = ("off", "propose", "auto")


def _422(message: str) -> HTTPException:
    return HTTPException(422, message)


def need_reason(reason: str | None) -> str:
    if not (reason or "").strip():
        raise _422("a reason is needed")
    return str(reason).strip()


def _log(db: Session, actor: Actor, area: str, key: str, old: Any, new: Any, reason: str) -> None:
    """The setup history and the audit log say the same thing: who changed what, from what, to what, and why."""
    record_history(db, actor, area, key, old, new, reason)
    audit.record(db, actor, f"setup.{area}", area, key, before=old, after=new, reason=reason)


def record_history(db: Session, actor: Actor, area: str, key: str, old: Any, new: Any, reason: str) -> None:
    """The setup history row alone, for a change whose service writes its own audit row. A change applied on a second
    administrator's approval names them too."""
    db.execute(
        text(
            "insert into setup_change (by_user, area, key, old_value, new_value, reason, approved_by) "
            "values (:u, :a, :k, cast(:o as jsonb), cast(:n as jsonb), :r, :ap)"
        ),
        {
            "u": actor.id,
            "ap": setting_changes.APPROVED_BY.get(),
            "a": area,
            "k": key,
            "o": json.dumps(old, default=str),
            "n": json.dumps(new, default=str),
            "r": reason,
        },
    )


def _submit(db: Session, ctx: routing.TenantContext, kind: str, args: dict[str, Any], reason: str) -> dict[str, Any]:
    return setting_changes.submit(db, user_actor(ctx.user_id), kind, args, reason, tenant=ctx.slug)


def _args(body: BaseModel) -> dict[str, Any]:
    """A request body as the proposal keeps it: plain JSON, without the reason (kept beside it)."""
    return body.model_dump(mode="json", exclude={"reason"})


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


# ---- people and roles -------------------------------------------------------------------------


def _accounts(ids: list[int]) -> dict[int, dict[str, Any]]:
    """Email and whether the login works: these live in the control database, shared by all companies."""
    if not ids:
        return {}
    with Session(tenants.control_engine()) as s:
        rows = s.execute(text("select id, email, active from app_user where id = any(:i)"), {"i": ids})
        return {r[0]: {"email": r[1], "active": bool(r[2])} for r in rows}


@router.get("/people")
def list_people(db: Db, ctx: PeopleAdmin) -> dict[str, Any]:
    profiles = _rows(db, "select user_id, display_name, role from user_profile order by user_id")
    accounts = _accounts([p["user_id"] for p in profiles])
    roles = _rows(db, "select id, user_id, role, scope_location, scope_department from user_role order by id")
    limits = _rows(
        db, "select id, user_id, document, category_code, location_code, max_value from user_limit order by id"
    )
    org = {
        r["email"].lower(): r
        for r in _rows(
            db,
            "select code, name, designation, department_code, manager_code, email from org_user where email is not null",
        )
    }
    people = []
    for p in profiles:
        acc = accounts.get(p["user_id"], {"email": None, "active": False})
        person = org.get((acc["email"] or "").lower())
        people.append(
            {
                "user_id": p["user_id"],
                "name": p["display_name"],
                "email": acc["email"],
                "active": acc["active"],
                "roles": [
                    {
                        "id": r["id"],
                        "role": r["role"],
                        "location": r["scope_location"],
                        "department": r["scope_department"],
                    }
                    for r in roles
                    if r["user_id"] == p["user_id"]
                ],
                "limits": [
                    {
                        "id": x["id"],
                        "document": x["document"],
                        "category": x["category_code"],
                        "location": x["location_code"],
                        "max_value": str(x["max_value"]),
                    }
                    for x in limits
                    if x["user_id"] == p["user_id"]
                ],
                "person": {k: person[k] for k in ("code", "name", "designation", "department_code", "manager_code")}
                if person
                else None,
            }
        )
    return {
        "people": people,
        "roles": list(users.ROLES),
        "org": _rows(
            db,
            "select code, name, designation, department_code from org_user where coalesce(active, true) order by code",
        ),
        "departments": _rows(db, "select code, name, head_user from department order by code"),
        "documents": ["request", "po", "invoice", "payment_run"],
    }


class NewPerson(BaseModel):
    email: str
    password: str
    display_name: str
    role: str = "Requester"
    reason: str = "New starter"


ADMIN_NEEDS_A_SECOND = "add the person with another role, then give the Admin role: another administrator approves it"


@router.post("/people", status_code=201)
def add_person(body: NewPerson, db: Db, ctx: PeopleAdmin) -> dict[str, int]:
    reason = need_reason(body.reason)
    if body.role == "Admin":
        raise HTTPException(403, ADMIN_NEEDS_A_SECOND)
    try:
        uid = users.create_user(
            ctx.slug, body.email, body.password, body.display_name, body.role, actor=user_actor(ctx.user_id)
        )
    except ValueError as exc:
        raise _422(str(exc)) from None
    _log(
        db,
        user_actor(ctx.user_id),
        "person",
        str(uid),
        None,
        {"email": body.email.strip().lower(), "role": body.role},
        reason,
    )
    db.commit()
    return {"user_id": uid}


class ActiveBody(BaseModel):
    active: bool
    reason: str | None = None


@router.post("/people/{user_id}/active", status_code=202)
def set_active(user_id: int, body: ActiveBody, db: Db, ctx: PeopleAdmin) -> dict[str, Any]:
    reason = need_reason(body.reason)
    if user_id == ctx.user_id and not body.active:
        raise HTTPException(409, "you cannot deactivate yourself")
    return _submit(db, ctx, "person_active", {"user_id": user_id, **_args(body)}, reason)


@setting_changes.handler("person_active", "admin.users")
def _person_active(db: Session, actor: Actor, args: dict[str, Any], reason: str, apply: bool) -> Change:
    """The login itself lives in the control database: it is written only when the change applies."""
    body, user_id = ActiveBody.model_validate(args), int(args["user_id"])
    was = _accounts([user_id]).get(user_id)
    if was is None or db.execute(text("select 1 from user_profile where user_id = :i"), {"i": user_id}).first() is None:
        raise HTTPException(404, "no such person")
    if was["active"] == body.active:
        raise HTTPException(409, f"they are already {'active' if body.active else 'inactive'}")
    before, after = {"active": was["active"]}, {"active": body.active}
    change = Change("person", str(user_id), before, after, {"user_id": user_id, "active": body.active}, person=user_id)
    if apply:
        with Session(tenants.control_engine()) as s:
            s.execute(text("update app_user set active = :a where id = :i"), {"a": body.active, "i": user_id})
            s.commit()
        _log(db, actor, "person", str(user_id), before, after, reason)
        db.commit()
    return change


class RoleGrant(BaseModel):
    role: str
    location: str | None = None
    department: str | None = None
    reason: str | None = None


def _not_yourself(user_id: int, ctx: routing.TenantContext, what: str) -> None:
    """Nobody widens their own powers: another administrator does it (DF-15)."""
    if user_id == ctx.user_id:
        raise HTTPException(403, f"you cannot {what}; another administrator has to")


@router.post("/people/{user_id}/roles", status_code=202)
def give_role(user_id: int, body: RoleGrant, db: Db, ctx: PeopleAdmin) -> dict[str, Any]:
    reason = need_reason(body.reason)
    _not_yourself(user_id, ctx, "give yourself a role")
    return _submit(db, ctx, "role_grant", {"user_id": user_id, **_args(body)}, reason)


@setting_changes.handler("role_grant", "admin.users")
def _role_grant(db: Session, actor: Actor, args: dict[str, Any], reason: str, apply: bool) -> Change:
    body, user_id = RoleGrant.model_validate(args), int(args["user_id"])
    if body.role not in users.ROLES:
        raise _422(f"role must be one of {', '.join(users.ROLES)}")
    if db.execute(text("select 1 from user_profile where user_id = :i"), {"i": user_id}).first() is None:
        raise HTTPException(404, "no such person")
    same = db.execute(
        text(
            "select 1 from user_role where user_id = :i and role = :r and scope_location is not distinct from :l "
            "and scope_department is not distinct from :d"
        ),
        {"i": user_id, "r": body.role, "l": body.location, "d": body.department},
    ).first()
    if same:
        raise HTTPException(409, "they already have this role")
    new = {"role": body.role, "location": body.location, "department": body.department}
    change = Change("role_grant", str(user_id), None, new, {"status": "granted"}, person=user_id)
    if apply:
        db.execute(
            text("insert into user_role (user_id, role, scope_location, scope_department) values (:i, :r, :l, :d)"),
            {"i": user_id, "r": body.role, "l": body.location, "d": body.department},
        )
        _log(db, actor, "role_grant", str(user_id), None, new, reason)
        db.commit()
    return change


@router.delete("/people/{user_id}/roles/{row_id}", status_code=202)
def take_role(user_id: int, row_id: int, db: Db, ctx: PeopleAdmin, reason: str = "") -> dict[str, Any]:
    reason = need_reason(reason)
    return _submit(db, ctx, "role_revoke", {"user_id": user_id, "row_id": row_id}, reason)


@setting_changes.handler("role_revoke", "admin.users")
def _role_revoke(db: Session, actor: Actor, args: dict[str, Any], reason: str, apply: bool) -> Change:
    user_id, row_id = int(args["user_id"]), int(args["row_id"])
    row = db.execute(
        text("select role from user_role where id = :r and user_id = :u"), {"r": row_id, "u": user_id}
    ).first()
    if row is None:
        raise HTTPException(404, "no such role")
    if row[0] == "Admin":
        others = db.execute(
            text("select count(distinct user_id) from user_role where role = 'Admin' and id <> :r"), {"r": row_id}
        ).scalar_one()
        if str(user_id) == actor.id or others == 0:
            raise HTTPException(409, "the last administrator, or you yourself, cannot lose the Admin role")
    change = Change("role_revoke", str(user_id), {"role": row[0]}, None, {"status": "removed"})
    if apply:
        db.execute(text("delete from user_role where id = :r"), {"r": row_id})
        _log(db, actor, "role_revoke", str(user_id), {"role": row[0]}, None, reason)
        db.commit()
    return change


class LimitBody(BaseModel):
    document: str
    max_value: Decimal
    category: str | None = None
    location: str | None = None
    reason: str | None = None


@router.put("/people/{user_id}/limits", status_code=202)
def put_limit(user_id: int, body: LimitBody, db: Db, ctx: PeopleAdmin) -> dict[str, Any]:
    reason = need_reason(body.reason)
    _not_yourself(user_id, ctx, "set your own approval limit")
    return _submit(db, ctx, "limit_set", {"user_id": user_id, **_args(body)}, reason)


@setting_changes.handler("limit_set", "admin.users")
def _limit_set(db: Session, actor: Actor, args: dict[str, Any], reason: str, apply: bool) -> Change:
    body, user_id = LimitBody.model_validate(args), int(args["user_id"])
    if body.document not in ("request", "po", "invoice", "payment_run"):
        raise _422("the document must be a request, a purchase order, an invoice or a payment run")
    if body.max_value < 0:
        raise _422("a limit cannot be negative")
    if db.execute(text("select 1 from user_profile where user_id = :i"), {"i": user_id}).first() is None:
        raise HTTPException(404, "no such person")
    where = "user_id = :u and document = :d and category_code is not distinct from :c and location_code is not distinct from :l"
    params = {"u": user_id, "d": body.document, "c": body.category, "l": body.location}
    old = db.execute(text(f"select max_value from user_limit where {where}"), params).scalar()  # noqa: S608 - fixed text
    key, before, after = f"{user_id}:{body.document}", {"max_value": old}, {"max_value": body.max_value}
    change = Change("limit", key, before, after, {"status": "set"}, person=user_id)
    if apply:
        db.execute(text(f"delete from user_limit where {where}"), params)  # noqa: S608
        db.execute(
            text(
                "insert into user_limit (user_id, document, category_code, location_code, max_value) "
                "values (:u, :d, :c, :l, :m)"
            ),
            {**params, "m": body.max_value},
        )
        _log(db, actor, "limit", key, before, after, reason)
        db.commit()
    return change


@router.delete("/people/{user_id}/limits/{limit_id}", status_code=202)
def drop_limit(user_id: int, limit_id: int, db: Db, ctx: PeopleAdmin, reason: str = "") -> dict[str, Any]:
    reason = need_reason(reason)
    return _submit(db, ctx, "limit_drop", {"user_id": user_id, "limit_id": limit_id}, reason)


@setting_changes.handler("limit_drop", "admin.users")
def _limit_drop(db: Session, actor: Actor, args: dict[str, Any], reason: str, apply: bool) -> Change:
    user_id, limit_id = int(args["user_id"]), int(args["limit_id"])
    row = db.execute(
        text("select document, max_value from user_limit where id = :i and user_id = :u"), {"i": limit_id, "u": user_id}
    ).first()
    if row is None:
        raise HTTPException(404, "no such limit")
    change = Change("limit", f"{user_id}:{row[0]}", {"max_value": row[1]}, None, {"status": "removed"})
    if apply:
        db.execute(text("delete from user_limit where id = :i"), {"i": limit_id})
        _log(db, actor, "limit", change.key, change.old, None, reason)
        db.commit()
    return change


class ManagerBody(BaseModel):
    manager_code: str | None = None
    reason: str | None = None


@router.put("/people/{user_id}/manager", status_code=202)
def put_manager(user_id: int, body: ManagerBody, db: Db, ctx: PeopleAdmin) -> dict[str, Any]:
    reason = need_reason(body.reason)
    return _submit(db, ctx, "manager", {"user_id": user_id, **_args(body)}, reason)


@setting_changes.handler("manager", "admin.users")
def _manager(db: Session, actor: Actor, args: dict[str, Any], reason: str, apply: bool) -> Change:
    body, user_id = ManagerBody.model_validate(args), int(args["user_id"])
    email = (_accounts([user_id]).get(user_id) or {}).get("email")
    me = db.execute(
        text("select code, manager_code from org_user where lower(email) = :e"), {"e": (email or "").lower()}
    ).first()
    if me is None:
        raise HTTPException(409, "this login is not linked to a person in the organisation (the addresses must match)")
    if body.manager_code:
        if db.execute(text("select 1 from org_user where code = :c"), {"c": body.manager_code}).first() is None:
            raise _422(f"there is no person {body.manager_code}")
        seen: set[str] = {me[0]}
        nxt: str | None = body.manager_code
        while nxt:  # nobody may end up their own boss
            if nxt in seen:
                raise _422("that would make a person their own manager")
            seen.add(nxt)
            up = db.execute(text("select manager_code from org_user where code = :c"), {"c": nxt}).scalar()
            nxt = str(up) if up else None
    if me[1] == body.manager_code:
        raise HTTPException(409, "that is already their manager")
    before, after = {"manager_code": me[1]}, {"manager_code": body.manager_code}
    change = Change("manager", me[0], before, after, {"person": me[0], **after}, person=user_id)
    if apply:
        db.execute(text("update org_user set manager_code = :m where code = :c"), {"m": body.manager_code, "c": me[0]})
        _log(db, actor, "manager", me[0], before, after, reason)
        db.commit()
    return change


# ---- what each role may do --------------------------------------------------------------------


@router.get("/roles")
def list_roles(db: Db, ctx: PeopleAdmin) -> dict[str, Any]:
    granted: dict[str, list[str]] = {r: [] for r in users.ROLES}
    for role, action in db.execute(text("select role, action from role_permission order by role, action")):
        granted.setdefault(role, []).append(action)
    known = {**PERMISSIONS, **{a: a for actions in granted.values() for a in actions if a not in PERMISSIONS}}
    return {
        "roles": [{"role": r, "permissions": granted.get(r, [])} for r in users.ROLES],
        "permissions": [
            {"action": a, "label": label, "admin_only": a in ADMIN_ONLY} for a, label in sorted(known.items())
        ],
    }


class RolePermissions(BaseModel):
    permissions: list[str]
    reason: str | None = None


@router.put("/roles/{role}", status_code=202)
def put_role(role: str, body: RolePermissions, db: Db, ctx: PeopleAdmin) -> dict[str, Any]:
    reason = need_reason(body.reason)
    return _submit(db, ctx, "role_permission", {"role": role, **_args(body)}, reason)


@setting_changes.handler("role_permission", "admin.users")
def _role_permission(db: Session, actor: Actor, args: dict[str, Any], reason: str, apply: bool) -> Change:
    body, role = RolePermissions.model_validate(args), str(args["role"])
    if role not in users.ROLES:
        raise HTTPException(404, "no such role")
    wanted = set(body.permissions)
    unknown = sorted(wanted - set(PERMISSIONS))
    if unknown:
        raise _422(f"unknown {'permission' if len(unknown) == 1 else 'permissions'}: {', '.join(unknown)}")
    if role == "Admin":
        if not ADMIN_REQUIRED <= wanted:
            raise _422(f"Admin must keep {', '.join(sorted(ADMIN_REQUIRED))}, or nobody could run the setup")
        business = sorted(
            a for a in wanted if not a.startswith(("admin.", "audit.")) and not a.endswith(".view")
        )  # seeing is allowed
        if business:
            raise _422(f"Admin is a setup role and cannot take part in the purchase flow: {', '.join(business)}")
    elif wanted & ADMIN_ONLY:
        raise _422("the setup permissions belong to the Admin role only")
    old = {r[0] for r in db.execute(text("select action from role_permission where role = :r"), {"r": role})}
    result = {"role": role, "added": sorted(wanted - old), "removed": sorted(old - wanted)}
    change = Change("role_permission", role, sorted(old), sorted(wanted), result)
    if apply:
        db.execute(text("delete from role_permission where role = :r"), {"r": role})
        for action in sorted(wanted):
            db.execute(text("insert into role_permission (role, action) values (:r, :a)"), {"r": role, "a": action})
        _log(db, actor, "role_permission", role, sorted(old), sorted(wanted), reason)
        db.commit()
    return change


# ---- who approves: the people behind the roles in an approval chain ------------------------------


@router.get("/approvers")
def list_approvers(db: Db, ctx: RulesAdmin) -> dict[str, Any]:
    roles = _rows(db, "select role, source, value from approver_role order by role")
    people = _rows(
        db, "select code, name, designation, department_code from org_user where coalesce(active, true) order by code"
    )
    heads = {d["code"]: d["head_user"] for d in _rows(db, "select code, head_user from department")}
    by_code = {p["code"]: p for p in people}
    used = {
        role
        for (chain,) in db.execute(
            text("select approver_chain from approval_rule where effective_to is null or effective_to >= current_date")
        )
        for role in chain_roles(str(chain))[1]
    }
    out = []
    for r in roles:
        if r["source"] == "requester_manager":
            who = "The requester's own manager"
        elif r["source"] == "department_head":
            who = "The head of the request's department"
        elif r["source"] == "location_head":
            who = "The head of the request's delivery location"
        elif r["source"] == "department_head_of":
            head = by_code.get(heads.get(r["value"]) or "")
            who = f"The head of {r['value']}: {head['name'] if head else 'nobody set'}"
        else:
            named = [p["name"] for p in people if p["designation"] == r["value"]]
            who = f"Whoever is {r['value']}: {', '.join(named) or 'nobody'}"
        out.append({**r, "resolves_to": who, "in_use": r["role"] in used})
    return {
        "roles": out,
        "sources": list(APPROVER_SOURCES),
        "departments": _rows(db, "select code, name, head_user from department order by code"),
        "designations": sorted({p["designation"] for p in people if p["designation"]}),
        "people": people,
    }


class ApproverBody(BaseModel):
    source: str
    value: str | None = None
    reason: str | None = None


@router.put("/approvers/{role}", status_code=202)
def put_approver(role: str, body: ApproverBody, db: Db, ctx: RulesAdmin) -> dict[str, Any]:
    reason = need_reason(body.reason)
    return _submit(db, ctx, "approver_role", {"role": role, **_args(body)}, reason)


@setting_changes.handler("approver_role", "admin.rules")
def _approver_role(db: Session, actor: Actor, args: dict[str, Any], reason: str, apply: bool) -> Change:
    body, role = ApproverBody.model_validate(args), str(args["role"])
    if not role.replace("_", "").isalnum() or role != role.lower() or role == "auto":
        raise _422("a role name is lower case letters, digits and underscores, and cannot be 'auto'")
    if body.source not in APPROVER_SOURCES:
        raise _422("choose who stands behind the role from the list")
    value = (body.value or "").strip() or None
    if body.source in ("requester_manager", "department_head", "location_head"):
        value = None
    elif not value:
        raise _422("this kind of role needs a value (a department code, or a designation)")
    elif (
        body.source == "department_head_of"
        and db.execute(text("select 1 from department where code = :c"), {"c": value}).first() is None
    ):
        raise _422(f"there is no department {value}")
    elif (
        body.source == "designation"
        and db.execute(
            text("select 1 from org_user where designation = :d and coalesce(active, true)"), {"d": value}
        ).first()
        is None
    ):
        raise _422(f"nobody holds the designation '{value}'")
    old = db.execute(text("select source, value from approver_role where role = :r"), {"r": role}).first()
    before = {"source": old[0], "value": old[1]} if old else None
    after = {"source": body.source, "value": value}
    change = Change("approver_role", role, before, after, {"role": role, **after})
    if apply:
        db.execute(
            text(
                "insert into approver_role (role, source, value) values (:r, :s, :v) "
                "on conflict (role) do update set source = :s, value = :v"
            ),
            {"r": role, "s": body.source, "v": value},
        )
        _log(db, actor, "approver_role", role, before, after, reason)
        db.commit()
    return change


@router.delete("/approvers/{role}", status_code=202)
def drop_approver(role: str, db: Db, ctx: RulesAdmin, reason: str = "") -> dict[str, Any]:
    reason = need_reason(reason)
    return _submit(db, ctx, "approver_role_drop", {"role": role}, reason)


@setting_changes.handler("approver_role_drop", "admin.rules")
def _approver_role_drop(db: Session, actor: Actor, args: dict[str, Any], reason: str, apply: bool) -> Change:
    role = str(args["role"])
    old = db.execute(text("select source, value from approver_role where role = :r"), {"r": role}).first()
    if old is None:
        raise HTTPException(404, "no such role")
    for (chain,) in db.execute(
        text("select approver_chain from approval_rule where effective_to is null or effective_to >= current_date")
    ):
        if role in chain_roles(str(chain))[1]:
            raise HTTPException(409, "an approval chain still uses this role")
    change = Change("approver_role", role, {"source": old[0], "value": old[1]}, None, {"status": "removed"})
    if apply:
        db.execute(text("delete from approver_role where role = :r"), {"r": role})
        _log(db, actor, "approver_role", role, change.old, None, reason)
        db.commit()
    return change


class HeadBody(BaseModel):
    person_code: str
    reason: str | None = None


@router.put("/departments/{code}/head", status_code=202)
def put_head(code: str, body: HeadBody, db: Db, ctx: RulesAdmin) -> dict[str, Any]:
    reason = need_reason(reason=body.reason)
    return _submit(db, ctx, "department_head", {"code": code, **_args(body)}, reason)


@setting_changes.handler("department_head", "admin.rules")
def _department_head(db: Session, actor: Actor, args: dict[str, Any], reason: str, apply: bool) -> Change:
    body, code = HeadBody.model_validate(args), str(args["code"])
    old = db.execute(text("select head_user from department where code = :c"), {"c": code}).first()
    if old is None:
        raise HTTPException(404, "no such department")
    if (
        db.execute(
            text("select 1 from org_user where code = :c and coalesce(active, true)"), {"c": body.person_code}
        ).first()
        is None
    ):
        raise _422(f"there is no active person {body.person_code}")
    before, after = {"head_user": old[0]}, {"head_user": body.person_code}
    change = Change("department_head", code, before, after, {"department": code, "head": body.person_code})
    if apply:
        db.execute(text("update department set head_user = :p where code = :c"), {"p": body.person_code, "c": code})
        _log(db, actor, "department_head", code, before, after, reason)
        db.commit()
    return change


# ---- approval chains: the rule table that says who approves what, by value --------------------

CENT = D("0.01")


def _chain(db: Session, chain: str) -> str:
    """The chain as stored: 'auto', roles joined by '>', or '+' and roles for a row that adds approvers."""
    adds, roles = chain_roles(chain)
    parts = list(roles)
    if not parts:
        if adds or chain.strip() != "auto":
            raise _422("a chain needs at least one step, or 'auto'")
        return "auto"
    known = {r[0] for r in db.execute(text("select role from approver_role"))}
    missing = [p for p in parts if p not in known]
    if missing:
        raise _422(f"these roles are not defined under Approvers: {', '.join(missing)}")
    if len(set(parts)) != len(parts):
        raise _422("a role appears twice in the chain")
    return ("+" if adds else "") + ">".join(parts)


def coverage_problems(db: Session, document: str, on: date) -> list[str]:
    """For each scope of a document, the value bands in force on a date must follow one another with no gap and no overlap.
    Rows that add an approver ('+role') cover only the band they name, so they are not part of this check."""
    rows = _rows(
        db,
        "select rule_code, min_value, max_value, category_code, department_code, coalesce(location_code, 'ANY') as "
        "location_code, spend_type from approval_rule where document = :d and effective_from <= :o "
        "and (effective_to is null or effective_to >= :o) and approver_chain not like '+%' order by min_value",
        d=document,
        o=on,
    )
    scopes: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
    for r in rows:
        scopes.setdefault((r["category_code"], r["department_code"], r["location_code"], r["spend_type"]), []).append(r)
    problems = []
    for scope, bands in scopes.items():
        label = "/".join(scope)
        if bands[0]["min_value"] > CENT:
            problems.append(f"{document} {label}: nothing covers values below {bands[0]['min_value']}")
        for a, b in zip(bands, bands[1:], strict=False):
            if b["min_value"] <= a["max_value"]:
                problems.append(f"{document} {label}: {a['rule_code']} and {b['rule_code']} overlap")
            elif b["min_value"] > a["max_value"] + CENT:
                problems.append(
                    f"{document} {label}: nothing covers {a['max_value'] + CENT} to {b['min_value'] - CENT}"
                )
    return problems


def _rule_json(r: dict[str, Any], today: date) -> dict[str, Any]:
    return {
        "id": r["id"],
        "rule_code": r["rule_code"],
        "document": r["document"],
        "min_value": str(r["min_value"]),
        "max_value": str(r["max_value"]),
        "category": r["category_code"],
        "department": r["department_code"],
        "location": r.get("location_code") or "ANY",
        "spend_type": r["spend_type"],
        "adds": chain_roles(str(r["approver_chain"]))[0],
        "chain": list(chain_roles(str(r["approver_chain"]))[1]) or ["auto"],
        "version": r["version"],
        "effective_from": _iso(r["effective_from"]),
        "effective_to": _iso(r["effective_to"]),
        "in_force": r["effective_from"] <= today and (r["effective_to"] is None or r["effective_to"] >= today),
    }


@router.get("/approval-rules")
def list_approval_rules(db: Db, ctx: RulesAdmin) -> dict[str, Any]:
    today = business_today()
    rules = _rows(db, "select * from approval_rule order by document, rule_code, version")
    documents = sorted({r["document"] for r in rules})
    return {
        "rules": [_rule_json(r, today) for r in rules],
        "problems": [p for d in documents for p in coverage_problems(db, d, today)],
        "roles": [r[0] for r in db.execute(text("select role from approver_role order by role"))],
        "documents": documents,
    }


class RuleVersion(BaseModel):
    rule_code: str | None = None
    document: str
    min_value: Decimal
    max_value: Decimal
    category_code: str = "ANY"
    department_code: str = "ANY"
    location_code: str = "ANY"
    spend_type: str = "ANY"
    approver_chain: str
    effective_from: date
    reason: str | None = None


@router.post("/approval-rules", status_code=202)
def new_rule_version(body: RuleVersion, db: Db, ctx: RulesAdmin) -> dict[str, Any]:
    """A change to who approves is a new version from a date. Past decisions keep the version they used."""
    reason = need_reason(body.reason)
    return _submit(db, ctx, "approval_rule", _args(body), reason)


@setting_changes.handler("approval_rule", "admin.rules")
def _approval_rule(db: Session, actor: Actor, args: dict[str, Any], reason: str, apply: bool) -> Change:
    """Written to see that the table stays whole; a check alone runs in a savepoint that is undone (`setting_changes`)."""
    body = RuleVersion.model_validate(args)
    today = business_today()
    if body.effective_from < today:
        raise _422("a new version starts today or later: decisions already made keep the version they were made under")
    if body.min_value < 0 or body.max_value < body.min_value:
        raise _422("the band must run from zero or more up to a value that is not smaller")
    chain = _chain(db, body.approver_chain)
    code = body.rule_code
    old: dict[str, Any] | None = None
    if code:
        latest = db.execute(text("select max(version) from approval_rule where rule_code = :c"), {"c": code}).scalar()
        if latest is None:
            raise HTTPException(404, f"there is no rule {code}")
        version = int(latest) + 1
        old_row = (
            db.execute(
                text(
                    "select * from approval_rule where rule_code = :c and effective_from < :f and (effective_to is null or effective_to >= :f)"
                ),
                {"c": code, "f": body.effective_from},
            )
            .mappings()
            .first()
        )
        if old_row is not None:
            old = _rule_json(dict(old_row), today)
            db.execute(
                text("update approval_rule set effective_to = :t where id = :i"),
                {"t": body.effective_from - timedelta(days=1), "i": old_row["id"]},
            )
    else:
        prefix = {
            "request": "REQ",
            "po": "PO",
            "po_amendment": "POA",
            "price_variance": "VAR",
            "payment_run": "PAY",
            "supplier_activation": "SUP",
        }.get(body.document, body.document[:3].upper())
        taken = [
            r[0]
            for r in db.execute(
                text("select rule_code from approval_rule where rule_code like :p"), {"p": f"{prefix}-%"}
            )
        ]
        number = max((int(c.split("-")[1]) for c in taken if c.split("-")[1].isdigit()), default=0) + 1
        code, version = f"{prefix}-{number:02d}", 1
    new_id = db.execute(
        text(
            "insert into approval_rule (rule_code, document, min_value, max_value, category_code, department_code, spend_type, "
            "location_code, approver_chain, version, effective_from) values (:c, :d, :lo, :hi, :cat, :dep, :sp, :loc, :ch, "
            ":v, :f) returning id"
        ),
        {
            "c": code, "d": body.document, "lo": body.min_value, "hi": body.max_value, "cat": body.category_code,
            "dep": body.department_code, "sp": body.spend_type, "loc": body.location_code or "ANY", "ch": chain, "v": version,
            "f": body.effective_from,
        },
    ).scalar_one()  # fmt: skip
    problems = coverage_problems(db, body.document, body.effective_from)
    if problems:
        raise _422("that would leave the rule table with a gap or an overlap: " + "; ".join(problems))
    new = _rule_json(_rows(db, "select * from approval_rule where id = :i", i=new_id)[0], today)
    change = Change("approval_rule", f"{code}:v{version}", old, new, {"rule_code": code, "version": version})
    if not apply:
        return change
    _log(db, actor, "approval_rule", change.key, old, new, reason)
    db.commit()
    return change


# ---- policies and tolerances ------------------------------------------------------------------


def _validate(
    kind: str, value: str, low: float | None, high: float | None, choices: tuple[str, ...], label: str
) -> str:
    value = str(value).strip()
    if kind == "bool":
        if value not in ("true", "false"):
            raise _422(f"{label}: must be true or false")
    elif kind == "choice":
        if value not in choices:
            raise _422(f"{label}: must be one of {', '.join(choices)}")
    elif kind == "text":
        value = value.replace("\r\n", "\n").strip("\n") + "\n" if value.strip() else ""
        if value and (bad := templates.problems(value)):
            raise _422(f"{label}: " + "; ".join(bad))
    elif kind == "list":
        words = [w.strip().lower() for w in value.split(",") if w.strip()]
        if not words or not all(LIST_WORD.fullmatch(w) for w in words):
            raise _422(f"{label}: a comma-separated list of words like cancelled_cheque, bank_letter")
        value = ",".join(dict.fromkeys(words))
    elif kind == "email":
        value = value.lower()
        if value and (len(value) > 254 or not EMAIL.fullmatch(value)):
            raise _422(f"{label}: must be an email address like requests@yourcompany.example, or empty")
    else:
        try:
            number = D(value)
        except InvalidOperation:
            raise _422(f"{label}: must be a number") from None
        if not number.is_finite():
            raise _422(f"{label}: must be a number")
        if kind == "int" and number != number.to_integral_value():
            raise _422(f"{label}: must be a whole number")
        if low is not None and number < D(str(low)):
            raise _422(f"{label}: cannot be below {low:g}")
        if high is not None and number > D(str(high)):
            raise _422(f"{label}: cannot be above {high:g}")
        value = str(int(number)) if kind == "int" else format(number.normalize(), "f")
    return value


LIST_WORD = re.compile(r"[a-z][a-z0-9_]{0,39}")
EMAIL = re.compile(r"[a-z0-9._%+-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)+")
MAILBOXES = (
    "request_mailbox",
    "invoice_receipt_mailbox",
)  # two mailboxes cannot be one address: the mail would go to both


def _stored(db: Session, store: str, key: str) -> str | None:
    table = "org_setting" if store == "org" else "app_setting"
    value = db.execute(text(f"select value from {table} where key = :k"), {"k": key}).scalar()  # noqa: S608 - fixed table names
    return None if value is None else str(value)


def _last_change(db: Session, area: str, key: str) -> dict[str, Any] | None:
    r = db.execute(
        text("select at, by_user, reason from setup_change where area = :a and key = :k order by id desc limit 1"),
        {"a": area, "k": key},
    ).first()
    return {"at": _iso(r[0]), "by": r[1], "reason": r[2]} if r else None


@router.get("/policies")
def list_policies(db: Db, ctx: SettingsAdmin) -> dict[str, Any]:
    settings = []
    for key, (store, kind, label, help_, default, low, high, choices) in POLICIES.items():
        value = _stored(db, store, key)
        settings.append(
            {
                "key": key, "label": label, "help": help_, "kind": kind, "default": default,
                "value": value if value is not None else default, "set": value is not None,
                "min": low, "max": high, "choices": list(choices), "last_change": _last_change(db, "policy", key),
            }
        )  # fmt: skip
    today = business_today()
    current = tolerance_versions.in_force(db, today)
    rows = current.rows
    names = {f"user:{r[0]}": str(r[1]) for r in db.execute(text("select user_id, display_name from user_profile"))}
    tolerances = [
        {
            "key": key, "label": label, "min": low, "max": high,
            "values": [{"category": r["category_code"], "value": r["value"], "last_change": _last_change(db, "tolerance", f"{key}:{r['category_code']}")} for r in rows if r["key"] == key],
        }
        for key, (label, _, low, high) in TOLERANCES.items()
    ]  # fmt: skip
    return {
        "settings": settings,
        "tolerances": tolerances,
        "in_force": current.version,
        "versions": [_tolerance_version(v, today, names) for v in tolerance_versions.versions(db)],
        "categories": ["ANY", *[r[0] for r in db.execute(text("select code from item_category order by code"))]],
    }


@router.get("/tolerances")
def list_tolerances(db: Db, ctx: SettingsAdmin) -> dict[str, Any]:
    """The tolerance versions: the one in force, the history and the values of each (task 3.2)."""
    full = list_policies(db, ctx)
    return {k: full[k] for k in ("tolerances", "in_force", "versions", "categories")}


class PolicyBody(BaseModel):
    value: str
    reason: str | None = None


@router.put("/policies/{key}", status_code=202)
def put_policy(key: str, body: PolicyBody, db: Db, ctx: SettingsAdmin) -> dict[str, Any]:
    reason = need_reason(body.reason)
    return _submit(db, ctx, "policy", {"key": key, **_args(body)}, reason)


@setting_changes.handler("policy", "admin.settings")
def _policy(db: Session, actor: Actor, args: dict[str, Any], reason: str, apply: bool) -> Change:
    body, key = PolicyBody.model_validate(args), str(args["key"])
    spec = POLICIES.get(key)
    if spec is None:
        raise HTTPException(404, f"there is no policy '{key}'")
    store, kind, label, _, default, low, high, choices = spec
    value = _validate(kind, body.value, low, high, choices, label)
    if key in MAILBOXES and value:
        other = next(k for k in MAILBOXES if k != key)
        if _stored(db, "org", other) == value:
            raise _422(f"{label}: that address already receives {POLICIES[other][2].lower()}; use a different one")
    change = _write_policy(db, actor, key, store, value, default, reason, apply)
    if apply:
        _after_policy(db, key, value)
    return change


def _after_policy(db: Session, key: str, value: str) -> None:
    """Settings the store check reads take effect at once: the daily job is queued, or the check runs again."""
    tenant = tenants.slug_of(str(db.get_bind().engine.url.database or ""))
    if tenant is None:
        return
    if key == "auto_store_check" and value == "true":
        request_jobs.schedule_check(tenant)
    elif key == "receiving_buffer_days":
        material.check_after(db, tenant, material.all_places(db), why="the receiving buffer changed")


def _write_policy(
    db: Session, actor: Actor, key: str, store: str, value: str, default: str, reason: str, apply: bool
) -> Change:
    old = _stored(db, store, key)
    if (old if old is not None else default) == value and old is not None:
        raise HTTPException(409, "that is already the value")
    before, after = {"value": old if old is not None else default}, {"value": value}
    change = Change("policy", key, before, after, {"key": key, "value": value})
    if not apply:
        return change
    if store == "org":
        db.execute(
            text("insert into org_setting (key, value) values (:k, :v) on conflict (key) do update set value = :v"),
            {"k": key, "v": value},
        )
    else:
        db.execute(
            text(
                "insert into app_setting (key, value) values (:k, :v) on conflict (key) do update set value = :v, updated_at = now()"
            ),
            {"k": key, "v": value},
        )
    _log(db, actor, "policy", key, before, after, reason)
    db.commit()
    return change


def _tolerance_version(v: dict[str, Any], today: date, names: dict[str, str]) -> dict[str, Any]:
    start, end, by = v["effective_from"], v["effective_to"], v["created_by"]
    return {
        "version": v["version"],
        "effective_from": start.isoformat(),
        "effective_to": end.isoformat() if end else None,
        "in_force": start <= today and (end is None or end >= today),
        "never_used": end is not None and end < start,
        "note": v["note"],
        "by": "Set up with the company" if by is None or str(by).startswith("system") else names.get(str(by), by),
        "at": v["created_at"].isoformat() if v["created_at"] else None,
        "values": v["values"],
    }


class ToleranceBody(BaseModel):
    category: str = "ANY"
    value: str
    reason: str | None = None
    effective_from: date | None = None  # default: today


class ToleranceValue(BaseModel):
    key: str
    category: str = "ANY"
    value: str


class ToleranceSet(BaseModel):
    """Several tolerance values that apply together, in one version, when another administrator approves (owner
    decision 10, 22 Nov 2026)."""

    values: list[ToleranceValue]
    reason: str | None = None
    effective_from: date | None = None  # default: today


@router.put("/tolerances", status_code=202)
def put_tolerances(body: ToleranceSet, db: Db, ctx: SettingsAdmin) -> dict[str, Any]:
    reason = need_reason(body.reason)
    return _submit(db, ctx, "tolerance", _args(body), reason)


@router.put("/tolerances/{key}", status_code=202)
def put_tolerance(key: str, body: ToleranceBody, db: Db, ctx: SettingsAdmin) -> dict[str, Any]:
    reason = need_reason(body.reason)
    one = ToleranceSet(
        values=[ToleranceValue(key=key, category=body.category, value=body.value)], effective_from=body.effective_from
    )
    return _submit(db, ctx, "tolerance", _args(one), reason)


def _tolerance_changes(db: Session, values: list[ToleranceValue]) -> dict[tuple[str, str], str]:
    if not values:
        raise _422("give at least one tolerance value")
    changes: dict[tuple[str, str], str] = {}
    for v in values:
        spec = TOLERANCES.get(v.key)
        if spec is None:
            raise HTTPException(404, f"there is no tolerance '{v.key}'")
        label, kind, low, high = spec
        if (
            v.category != "ANY"
            and db.execute(text("select 1 from item_category where code = :c"), {"c": v.category}).first() is None
        ):
            raise _422(f"there is no category {v.category}")
        if (v.key, v.category) in changes:
            raise _422(f"{label} for {v.category} is given twice")
        changes[(v.key, v.category)] = _validate(kind, v.value, low, high, (), label)
    return changes


@setting_changes.handler("tolerance", "admin.settings")
def _tolerance(db: Session, actor: Actor, args: dict[str, Any], reason: str, apply: bool) -> Change:
    if "values" not in args:  # a proposal made before tolerance changes were proposed as a set
        args = {"values": [{k: args[k] for k in ("key", "category", "value") if k in args}], **args}
    body = ToleranceSet.model_validate(args)
    changes = _tolerance_changes(db, body.values)
    today = business_today()
    start = body.effective_from or today
    try:
        current = tolerance_versions.in_force(db, max(start, today))
        was = {(r["key"], r["category_code"]): r["value"] for r in current.rows}
        version = tolerance_versions.save_changes(
            db, actor, changes, effective_from=start, today=today, note=reason, apply=apply
        )
    except tolerance_versions.ToleranceError as exc:
        raise _422(str(exc)) from None
    names = {f"{k}:{c}": (was.get((k, c)), value) for (k, c), value in changes.items()}
    common = {"version": version, "effective_from": start.isoformat()}
    if len(changes) == 1:
        ((key, category), value), (name, (old, _)) = next(iter(changes.items())), next(iter(names.items()))
        change = Change("tolerance", name, {"value": old}, {"value": value},
                        {"key": key, "category": category, "value": value, **common})  # fmt: skip
    else:
        change = Change(
            "tolerance",
            ", ".join(names),
            {n: {"value": o} for n, (o, _) in names.items()},
            {n: {"value": v} for n, (_, v) in names.items()},
            {"values": [{"key": k, "category": c, "value": v} for (k, c), v in changes.items()], **common},
        )
    if not apply:
        return change
    for name, (old, value) in names.items():  # one history row per value, so each tolerance shows its last change
        _log(db, actor, "tolerance", name, {"value": old}, {"value": value}, reason)
    db.commit()
    return change


def policy_value_on(db: Session, key: str, at: datetime) -> str | None:
    """What a policy was on a given moment, read back from the setup history (None if it had not been changed by then)."""
    row = db.execute(
        text(
            "select new_value->>'value' from setup_change where area = 'policy' and key = :k and at <= :t order by id desc limit 1"
        ),
        {"k": key, "t": at},
    ).first()
    if row is not None:
        return str(row[0])
    first = db.execute(
        text("select old_value->>'value' from setup_change where area = 'policy' and key = :k order by id limit 1"),
        {"k": key},
    ).first()
    return None if first is None else str(first[0])


# ---- automation: how much the platform may do without a person --------------------------------


def _track_record(db: Session, decision_type: str) -> dict[str, int]:
    n, wrong = db.execute(
        text(
            "select count(*), count(*) filter (where wrong) from autonomy_outcome "
            "where decision_type = :d and auto and at >= now() - interval '30 days'"
        ),
        {"d": decision_type},
    ).one()
    return {"automatic": int(n), "wrong": int(wrong)}


@router.get("/automation")
def list_automation(db: Db, ctx: SettingsAdmin) -> dict[str, Any]:
    kill = bool(db.execute(text("select kill_switch from autonomy_global where id = 1")).scalar_one())
    rows = []
    for dt, (label, help_) in DECISIONS.items():
        if dt == "po.send":
            confirm = (_stored(db, "org", "po_send_requires_confirmation") or "true") != "false"
            rows.append(
                {
                    "decision_type": dt, "label": label, "help": help_, "locked": False,
                    "level": "propose" if confirm else "auto", "levels": ["propose", "auto"],
                    "value_cap": None, "min_confidence": None, "max_wrong_rate": None, "record": _track_record(db, dt),
                    "note": "Level 'propose' means a buyer sends each order; 'auto' sends it as soon as it is approved.",
                }
            )  # fmt: skip
            continue
        s = db.execute(
            text(
                "select level, value_cap, min_confidence, max_wrong_rate from autonomy_setting where decision_type = :d"
            ),
            {"d": dt},
        ).first()
        rows.append(
            {
                "decision_type": dt, "label": label, "help": help_, "locked": False,
                "level": s[0] if s else "propose", "levels": list(LEVELS),
                "value_cap": str(s[1]) if s and s[1] is not None else None,
                "min_confidence": str(s[2]) if s else "0.900", "max_wrong_rate": str(s[3]) if s else "0.0200",
                "record": _track_record(db, dt), "note": None,
            }
        )  # fmt: skip
    for dt, label in LOCKED_LABELS.items():
        rows.append(
            {
                "decision_type": dt, "label": label, "help": "", "locked": True, "level": "propose", "levels": ["propose"],
                "value_cap": None, "min_confidence": None, "max_wrong_rate": None, "record": _track_record(db, dt),
                "note": "Always decided by a person. It can never be set to act alone.",
            }
        )  # fmt: skip
    return {"kill_switch": kill, "decisions": rows, "last_kill_switch": _last_change(db, "automation", "kill_switch")}


class AutomationBody(BaseModel):
    level: str
    value_cap: Decimal | None = None
    min_confidence: Decimal = D("0.9")
    max_wrong_rate: Decimal = D("0.02")
    reason: str | None = None


@router.put("/automation/{decision_type}", status_code=202)
def put_automation(decision_type: str, body: AutomationBody, db: Db, ctx: SettingsAdmin) -> dict[str, Any]:
    reason = need_reason(body.reason)
    return _submit(db, ctx, "automation", {"decision_type": decision_type, **_args(body)}, reason)


@setting_changes.handler("automation", "admin.settings")
def _automation(db: Session, actor: Actor, args: dict[str, Any], reason: str, apply: bool) -> Change:
    body, decision_type = AutomationBody.model_validate(args), str(args["decision_type"])
    if decision_type in autonomy.LOCKED:
        raise _422(
            f"{autonomy.LABELS.get(decision_type, decision_type)} is always decided by a person and can never be set to act alone"
        )
    if decision_type not in DECISIONS:
        raise HTTPException(404, f"there is no decision type '{decision_type}'")
    if body.level not in LEVELS:
        raise _422("the level must be Off, Recommend or Act within the limits")
    if body.value_cap is not None and body.value_cap < 0:
        raise _422("a cap cannot be negative")
    if not D(0) <= body.min_confidence <= D(1):
        raise _422("the confidence threshold is between 0 and 1")
    if not D(0) <= body.max_wrong_rate <= D(1):
        raise _422("the wrong-decision rate is between 0 and 1")
    if decision_type == "po.send":
        if body.level == "off":
            raise _422(
                "orders have to be sent by someone: choose Recommend (a person sends each one) or Act within the limits"
            )
        return _write_policy(
            db,
            actor,
            "po_send_requires_confirmation",
            "org",
            "true" if body.level == "propose" else "false",
            "true",
            reason,
            apply,
        )
    old = db.execute(
        text("select level, value_cap, min_confidence, max_wrong_rate from autonomy_setting where decision_type = :d"),
        {"d": decision_type},
    ).first()
    before = {"level": old[0], "value_cap": old[1], "min_confidence": old[2], "max_wrong_rate": old[3]} if old else None
    after = {
        "level": body.level,
        "value_cap": body.value_cap,
        "min_confidence": body.min_confidence,
        "max_wrong_rate": body.max_wrong_rate,
    }
    change = Change("automation", decision_type, before, after, {"decision_type": decision_type, "level": body.level})
    if not apply:
        return change
    autonomy.configure(
        db,
        actor,
        decision_type,
        level=body.level,
        value_cap=body.value_cap,
        min_confidence=body.min_confidence,
        max_wrong_rate=body.max_wrong_rate,
    )
    _log(db, actor, "automation", decision_type, before, after, reason)
    db.commit()
    if decision_type == "planning.explain" and body.level != "auto":  # switched off: nothing queued is left behind
        planning.drop_queued(tenants.slug_of(str(db.get_bind().engine.url.database or "")) or "")
    return change


# ---- planning values of an item at a place (safety stock, reorder point, maximum) ---------------------


class ItemPlanningBody(BaseModel):
    item_code: str
    location: str
    safety_stock: Decimal | None = None
    reorder_policy: str | None = None
    reorder_point: Decimal | None = None
    max_qty: Decimal | None = None
    reason: str | None = None


PLANNING_KEYS = ("safety_stock", "reorder_policy", "reorder_point", "max_qty")


@router.post("/item-planning", status_code=202)
def post_item_planning(payload: dict[str, Any], db: Db, ctx: SettingsAdmin) -> dict[str, Any]:
    """Change an item's planning values at a place: waits for a second administrator (C4 is a control, D-22). Only the
    keys given change."""
    reason = need_reason(payload.get("reason"))
    args = {k: payload[k] for k in ("item_code", "location", *PLANNING_KEYS) if k in payload}
    return _submit(db, ctx, "item_planning", args, reason)


@setting_changes.handler("item_planning", "admin.settings")
def _item_planning(db: Session, actor: Actor, args: dict[str, Any], reason: str, apply: bool) -> Change:
    body = ItemPlanningBody.model_validate(args)
    if db.execute(text("select 1 from item where code = :c"), {"c": body.item_code}).first() is None:
        raise HTTPException(404, f"there is no item {body.item_code}")
    if db.execute(text("select 1 from location where code = :c"), {"c": body.location}).first() is None:
        raise HTTPException(404, f"there is no place {body.location}")
    given = {k: args[k] for k in PLANNING_KEYS if k in args}
    if not given:
        raise _422("say what changes: safety_stock, reorder_policy, reorder_point or max_qty")
    if body.reorder_policy is not None and body.reorder_policy not in ("none", "minmax", "fixed"):
        raise _422("reorder_policy is none, minmax or fixed")
    old_row = (
        db.execute(
            text(
                "select safety_stock, reorder_policy, reorder_point, max_qty from item_location "
                "where item_code = :i and location_code = :l"
            ),
            {"i": body.item_code, "l": body.location},
        )
        .mappings()
        .first()
    )
    old = {k: (old_row[k] if old_row else None) for k in PLANNING_KEYS}
    new = {**old, **{k: getattr(body, k) for k in given}}
    for k in ("safety_stock", "reorder_point", "max_qty"):
        if new[k] is not None and D(new[k]) < 0:
            raise _422(f"{k} cannot be negative")
    if new["max_qty"] is not None and new["reorder_point"] is not None and D(new["max_qty"]) < D(new["reorder_point"]):
        raise _422("the maximum cannot be below the reorder point")
    key = f"{body.item_code}@{body.location}"
    before, after = {k: old[k] for k in given}, {k: new[k] for k in given}
    change = Change(
        "item_planning", key, before, after, {"item_code": body.item_code, "location": body.location, **after}
    )
    if not apply:
        return change
    db.execute(
        text(
            "insert into item_location (item_code, location_code, safety_stock, reorder_policy, reorder_point, max_qty) "
            "values (:i, :l, :s, :p, :r, :m) on conflict (item_code, location_code) do update set "
            "safety_stock = :s, reorder_policy = :p, reorder_point = :r, max_qty = :m"
        ),
        {"i": body.item_code, "l": body.location, "s": new["safety_stock"], "p": new["reorder_policy"] or "none",
         "r": new["reorder_point"], "m": new["max_qty"]},
    )  # fmt: skip
    _log(db, actor, "item_planning", key, before, after, reason)
    db.commit()
    return change


class KillSwitch(BaseModel):
    on: bool
    reason: str | None = None


@router.put("/automation-kill-switch")
def put_kill_switch(body: KillSwitch, db: Db, ctx: SettingsAdmin, response: Response) -> dict[str, Any]:
    """Stopping automation is immediate, for safety. Starting it again waits for a second administrator."""
    reason = need_reason(body.reason)
    if not body.on:
        response.status_code = 202
        return _submit(db, ctx, "kill_switch_off", {}, reason)
    _kill_switch(db, user_actor(ctx.user_id), True, reason, apply=True)
    return {"kill_switch": True}


@setting_changes.handler("kill_switch_off", "admin.settings")
def _kill_switch_off(db: Session, actor: Actor, args: dict[str, Any], reason: str, apply: bool) -> Change:
    return _kill_switch(db, actor, False, reason, apply)


def _kill_switch(db: Session, actor: Actor, on: bool, reason: str, apply: bool) -> Change:
    old = bool(db.execute(text("select kill_switch from autonomy_global where id = 1")).scalar_one())
    if not on and not old:
        raise HTTPException(409, "the kill switch is already off")
    change = Change("automation", "kill_switch", {"on": old}, {"on": on}, {"kill_switch": on})
    if apply:
        autonomy.set_kill_switch(db, actor, on, reason)
        _log(db, actor, "automation", "kill_switch", {"on": old}, {"on": on}, reason)
        db.commit()
    return change


# ---- changes waiting for a second administrator ---------------------------------------------------


@router.get("/pending")
def list_pending(db: Db, ctx: AuditViewer) -> list[dict[str, Any]]:
    """Every change waiting for approval, old value (as it is now), new value, reason, who proposed it. `mine` marks the
    ones the caller proposed (they cannot approve those)."""
    return [{**c, "mine": c["by"] == ctx.user_id} for c in setting_changes.pending(db)]


# ---- the history ------------------------------------------------------------------------------


def _name(names: dict[Any, Any], user: str | None) -> str | None:
    """A person's name for a login id as the history stores it (the system and agents are kept as they are)."""
    if user is None:
        return None
    return names.get(int(user)) if user.isdigit() else user


@router.get("/changes")
def list_changes(db: Db, ctx: AuditViewer, area: str | None = None, key: str | None = None, limit: int = 100) -> Rows:
    rows = _rows(
        db,
        "select id, at, by_user, approved_by, area, key, old_value, new_value, reason from setup_change "
        "where (cast(:a as text) is null or area = :a) and (cast(:k as text) is null or key = :k) order by id desc limit :n",
        a=area,
        k=key,
        n=max(1, min(limit, 500)),
    )
    names = {p["user_id"]: p["display_name"] for p in _rows(db, "select user_id, display_name from user_profile")}
    for r in rows:
        r["at"] = _iso(r["at"])
        r["by_name"] = _name(names, r["by_user"])
        r["approved_by_name"] = _name(names, r["approved_by"])
    return rows
