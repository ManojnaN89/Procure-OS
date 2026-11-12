"""Company setup: who the people are and what they may do, who approves what, the company's policies and tolerances, and
how much the platform may do without a person. All of it is data an administrator changes on screen.

Every change needs a reason and writes two things: an audit row and a row in the setup history (who, when, old value,
new value, why). A decision made earlier stays explainable, because the value in force at any time can be read back from
the history. Admin is a setup role: it can change this but holds no business permission, and it cannot give itself one.
"""

import json
import re
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.agents import autonomy
from p2p.formulas.calendar import business_today
from p2p.platform import audit, routing, tenants, users
from p2p.platform.permissions import require
from p2p.platform.rows import rows as _rows
from p2p.platform.sod import Actor
from p2p.platform.sod import user as user_actor
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
    "supplier.documents": "Add a supplier's documents",
    "supplier.verify_docs": "Verify a supplier's documents against the originals",
    "contract.manage": "Store contracts and their price lists",
    "contract.activate": "Activate a contract",
    "grn.create": "Receive and inspect goods",
    "stock.view": "See stock",
    "stock.issue": "Issue stock for use",
    "stock.adjust": "Correct stock after a count",
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
    "admin.users": "Manage people and roles",
    "admin.rules": "Manage approval chains and approvers",
    "admin.settings": "Manage policies and automation",
    "audit.view": "See the audit log",
}
ADMIN_ONLY = {"admin.users", "admin.rules", "admin.settings"}
ADMIN_REQUIRED = ADMIN_ONLY | {"audit.view"}  # without these nobody could run the setup any more
APPROVER_SOURCES = ("requester_manager", "department_head", "department_head_of", "designation")


# Only settings that some code reads are offered here: a setting that nothing reads would be a control that does nothing.
# key -> (where it is stored, kind, label, help, default, low, high, choices)
POLICIES: dict[str, tuple[str, str, str, str, str, float | None, float | None, tuple[str, ...]]] = {
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
    "comparison_mode": (
        "org",
        "choice",
        "How quotes are compared",
        "Price: the cheapest landed cost that complies. Weighted: landed cost, delivery record and quality record, by the weights below",
        "price",
        None,
        None,
        ("price", "weighted"),
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
        "Weight of price in a weighted comparison",
        "Shares of price, delivery and quality are taken in proportion to these three weights",
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
    "approval_reminder_hours": ("org", "int", "Remind an approver after (hours)", "", "24", 1, 720, ()),
    "supplier_document_warn_days": ("org", "int", "Warn before a document expires (days)", "", "30", 0, 365, ()),
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
}
TOLERANCES: dict[str, tuple[str, str, float | None, float | None]] = {
    "price_pct": ("Invoice price above the order price (%)", "decimal", 0, 100),
    "price_abs": ("Or by this many rupees on the line", "decimal", 0, None),
    "qty_pct": ("Invoice quantity above what was received (%)", "decimal", 0, 100),
    "receipt_over_pct": ("Goods received above the quantity ordered (%)", "decimal", 0, 100),
    "small_diff_abs": ("Small invoice difference accepted (rupees)", "decimal", 0, None),
    "supplier_change_pct": ("Supplier's quantity or price change accepted (%)", "decimal", 0, 100),
}

# decision type -> (label, what it does, how its level is stored)
DECISIONS: dict[str, tuple[str, str]] = {
    "rfq.send": ("Send a request for quotation", "Emails the suppliers chosen for a line"),
    "rfq.award": ("Award to the recommended supplier", "Awards a quotation when there is a clear winner"),
    "quote.read": ("Accept a quote read from an email", "Turns a clean reading of a supplier's reply into a quote"),
    "po.send": ("Send an approved purchase order", "Emails the order to the supplier"),
    "invoice.read": (
        "Enter an invoice read from a document",
        "Enters a clean reading of a supplier's invoice without AP",
    ),
    "supplier.change": (
        "Apply a small change a supplier asks for",
        "Moves a promised date within the accepted days; quantities are only noted, prices always go to a buyer",
    ),
    "supplier.remind": (
        "Chase a supplier by email",
        "Reminds a supplier that has not acknowledged an order, or still owes part of a delivery",
    ),
}
LOCKED_LABELS = {
    "payment_run.approve": "Approve a payment run",
    "supplier.set_status": "Activate or block a supplier",
    "invoice.release_block": "Release a payment block on an invoice",
}
LEVELS = ("off", "propose", "auto")


def _422(message: str) -> HTTPException:
    return HTTPException(422, message)


def _need_reason(reason: str | None) -> str:
    if not (reason or "").strip():
        raise _422("a reason is needed")
    return str(reason).strip()


def _log(db: Session, actor: Actor, area: str, key: str, old: Any, new: Any, reason: str) -> None:
    """The setup history and the audit log say the same thing: who changed what, from what, to what, and why."""
    db.execute(
        text(
            "insert into setup_change (by_user, area, key, old_value, new_value, reason) "
            "values (:u, :a, :k, cast(:o as jsonb), cast(:n as jsonb), :r)"
        ),
        {
            "u": actor.id,
            "a": area,
            "k": key,
            "o": json.dumps(old, default=str),
            "n": json.dumps(new, default=str),
            "r": reason,
        },
    )
    audit.record(db, actor, f"setup.{area}", area, key, before=old, after=new, reason=reason)


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


@router.post("/people", status_code=201)
def add_person(body: NewPerson, db: Db, ctx: PeopleAdmin) -> dict[str, int]:
    reason = _need_reason(body.reason)
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


@router.post("/people/{user_id}/active")
def set_active(user_id: int, body: ActiveBody, db: Db, ctx: PeopleAdmin) -> dict[str, Any]:
    reason = _need_reason(body.reason)
    if user_id == ctx.user_id and not body.active:
        raise HTTPException(409, "you cannot deactivate yourself")
    with Session(tenants.control_engine()) as s:
        row = s.execute(text("select active from app_user where id = :i"), {"i": user_id}).first()
        if (
            row is None
            or db.execute(text("select 1 from user_profile where user_id = :i"), {"i": user_id}).first() is None
        ):
            raise HTTPException(404, "no such person")
        s.execute(text("update app_user set active = :a where id = :i"), {"a": body.active, "i": user_id})
        s.commit()
    _log(db, user_actor(ctx.user_id), "person", str(user_id), {"active": bool(row[0])}, {"active": body.active}, reason)
    db.commit()
    return {"user_id": user_id, "active": body.active}


class RoleGrant(BaseModel):
    role: str
    location: str | None = None
    department: str | None = None
    reason: str | None = None


@router.post("/people/{user_id}/roles", status_code=201)
def give_role(user_id: int, body: RoleGrant, db: Db, ctx: PeopleAdmin) -> dict[str, str]:
    reason = _need_reason(body.reason)
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
    db.execute(
        text("insert into user_role (user_id, role, scope_location, scope_department) values (:i, :r, :l, :d)"),
        {"i": user_id, "r": body.role, "l": body.location, "d": body.department},
    )
    _log(
        db,
        user_actor(ctx.user_id),
        "role_grant",
        str(user_id),
        None,
        {"role": body.role, "location": body.location, "department": body.department},
        reason,
    )
    db.commit()
    return {"status": "granted"}


@router.delete("/people/{user_id}/roles/{row_id}")
def take_role(user_id: int, row_id: int, db: Db, ctx: PeopleAdmin, reason: str = "") -> dict[str, str]:
    reason = _need_reason(reason)
    row = db.execute(
        text("select role from user_role where id = :r and user_id = :u"), {"r": row_id, "u": user_id}
    ).first()
    if row is None:
        raise HTTPException(404, "no such role")
    if row[0] == "Admin":
        others = db.execute(
            text("select count(distinct user_id) from user_role where role = 'Admin' and id <> :r"), {"r": row_id}
        ).scalar_one()
        if user_id == ctx.user_id or others == 0:
            raise HTTPException(409, "the last administrator, or you yourself, cannot lose the Admin role")
    db.execute(text("delete from user_role where id = :r"), {"r": row_id})
    _log(db, user_actor(ctx.user_id), "role_revoke", str(user_id), {"role": row[0]}, None, reason)
    db.commit()
    return {"status": "removed"}


class LimitBody(BaseModel):
    document: str
    max_value: Decimal
    category: str | None = None
    location: str | None = None
    reason: str | None = None


@router.put("/people/{user_id}/limits")
def put_limit(user_id: int, body: LimitBody, db: Db, ctx: PeopleAdmin) -> dict[str, str]:
    reason = _need_reason(body.reason)
    if body.document not in ("request", "po", "invoice", "payment_run"):
        raise _422("document must be request, po, invoice or payment_run")
    if body.max_value < 0:
        raise _422("a limit cannot be negative")
    if db.execute(text("select 1 from user_profile where user_id = :i"), {"i": user_id}).first() is None:
        raise HTTPException(404, "no such person")
    where = "user_id = :u and document = :d and category_code is not distinct from :c and location_code is not distinct from :l"
    params = {"u": user_id, "d": body.document, "c": body.category, "l": body.location}
    old = db.execute(text(f"select max_value from user_limit where {where}"), params).scalar()  # noqa: S608 - fixed text
    db.execute(text(f"delete from user_limit where {where}"), params)  # noqa: S608
    db.execute(
        text(
            "insert into user_limit (user_id, document, category_code, location_code, max_value) values (:u, :d, :c, :l, :m)"
        ),
        {**params, "m": body.max_value},
    )
    _log(
        db,
        user_actor(ctx.user_id),
        "limit",
        f"{user_id}:{body.document}",
        {"max_value": old},
        {"max_value": body.max_value},
        reason,
    )
    db.commit()
    return {"status": "set"}


@router.delete("/people/{user_id}/limits/{limit_id}")
def drop_limit(user_id: int, limit_id: int, db: Db, ctx: PeopleAdmin, reason: str = "") -> dict[str, str]:
    reason = _need_reason(reason)
    row = db.execute(
        text("select document, max_value from user_limit where id = :i and user_id = :u"), {"i": limit_id, "u": user_id}
    ).first()
    if row is None:
        raise HTTPException(404, "no such limit")
    db.execute(text("delete from user_limit where id = :i"), {"i": limit_id})
    _log(db, user_actor(ctx.user_id), "limit", f"{user_id}:{row[0]}", {"max_value": row[1]}, None, reason)
    db.commit()
    return {"status": "removed"}


class ManagerBody(BaseModel):
    manager_code: str | None = None
    reason: str | None = None


@router.put("/people/{user_id}/manager")
def put_manager(user_id: int, body: ManagerBody, db: Db, ctx: PeopleAdmin) -> dict[str, Any]:
    reason = _need_reason(body.reason)
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
    db.execute(text("update org_user set manager_code = :m where code = :c"), {"m": body.manager_code, "c": me[0]})
    _log(
        db,
        user_actor(ctx.user_id),
        "manager",
        me[0],
        {"manager_code": me[1]},
        {"manager_code": body.manager_code},
        reason,
    )
    db.commit()
    return {"person": me[0], "manager_code": body.manager_code}


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


@router.put("/roles/{role}")
def put_role(role: str, body: RolePermissions, db: Db, ctx: PeopleAdmin) -> dict[str, Any]:
    reason = _need_reason(body.reason)
    if role not in users.ROLES:
        raise HTTPException(404, "no such role")
    wanted = set(body.permissions)
    unknown = sorted(wanted - set(PERMISSIONS))
    if unknown:
        raise _422(f"unknown permission(s): {', '.join(unknown)}")
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
    db.execute(text("delete from role_permission where role = :r"), {"r": role})
    for action in sorted(wanted):
        db.execute(text("insert into role_permission (role, action) values (:r, :a)"), {"r": role, "a": action})
    _log(db, user_actor(ctx.user_id), "role_permission", role, sorted(old), sorted(wanted), reason)
    db.commit()
    return {"role": role, "added": sorted(wanted - old), "removed": sorted(old - wanted)}


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
        part.strip()
        for (chain,) in db.execute(
            text("select approver_chain from approval_rule where effective_to is null or effective_to >= current_date")
        )
        for part in str(chain).split(">")
    }
    out = []
    for r in roles:
        if r["source"] == "requester_manager":
            who = "The requester's own manager"
        elif r["source"] == "department_head":
            who = "The head of the request's department"
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


@router.put("/approvers/{role}")
def put_approver(role: str, body: ApproverBody, db: Db, ctx: RulesAdmin) -> dict[str, Any]:
    reason = _need_reason(body.reason)
    if not role.replace("_", "").isalnum() or role != role.lower() or role == "auto":
        raise _422("a role name is lower case letters, digits and underscores, and cannot be 'auto'")
    if body.source not in APPROVER_SOURCES:
        raise _422(f"source must be one of {', '.join(APPROVER_SOURCES)}")
    value = (body.value or "").strip() or None
    if body.source in ("requester_manager", "department_head"):
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
    db.execute(
        text(
            "insert into approver_role (role, source, value) values (:r, :s, :v) on conflict (role) do update set source = :s, value = :v"
        ),
        {"r": role, "s": body.source, "v": value},
    )
    _log(
        db,
        user_actor(ctx.user_id),
        "approver_role",
        role,
        {"source": old[0], "value": old[1]} if old else None,
        {"source": body.source, "value": value},
        reason,
    )
    db.commit()
    return {"role": role, "source": body.source, "value": value}


@router.delete("/approvers/{role}")
def drop_approver(role: str, db: Db, ctx: RulesAdmin, reason: str = "") -> dict[str, str]:
    reason = _need_reason(reason)
    old = db.execute(text("select source, value from approver_role where role = :r"), {"r": role}).first()
    if old is None:
        raise HTTPException(404, "no such role")
    for (chain,) in db.execute(
        text("select approver_chain from approval_rule where effective_to is null or effective_to >= current_date")
    ):
        if role in [p.strip() for p in str(chain).split(">")]:
            raise HTTPException(409, "an approval chain still uses this role")
    db.execute(text("delete from approver_role where role = :r"), {"r": role})
    _log(db, user_actor(ctx.user_id), "approver_role", role, {"source": old[0], "value": old[1]}, None, reason)
    db.commit()
    return {"status": "removed"}


class HeadBody(BaseModel):
    person_code: str
    reason: str | None = None


@router.put("/departments/{code}/head")
def put_head(code: str, body: HeadBody, db: Db, ctx: RulesAdmin) -> dict[str, str]:
    reason = _need_reason(reason=body.reason)
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
    db.execute(text("update department set head_user = :p where code = :c"), {"p": body.person_code, "c": code})
    _log(
        db,
        user_actor(ctx.user_id),
        "department_head",
        code,
        {"head_user": old[0]},
        {"head_user": body.person_code},
        reason,
    )
    db.commit()
    return {"department": code, "head": body.person_code}


# ---- approval chains: the rule table that says who approves what, by value --------------------

CENT = D("0.01")


def _chain(db: Session, chain: str) -> list[str]:
    parts = [p.strip() for p in chain.split(">") if p.strip()]
    if not parts:
        raise _422("a chain needs at least one step, or 'auto'")
    if parts == ["auto"]:
        return parts
    known = {r[0] for r in db.execute(text("select role from approver_role"))}
    missing = [p for p in parts if p not in known]
    if missing:
        raise _422(f"these roles are not defined under Approvers: {', '.join(missing)}")
    if len(set(parts)) != len(parts):
        raise _422("a role appears twice in the chain")
    return parts


def coverage_problems(db: Session, document: str, on: date) -> list[str]:
    """For each scope of a document, the value bands in force on a date must follow one another with no gap and no overlap."""
    rows = _rows(
        db,
        "select rule_code, min_value, max_value, category_code, department_code, spend_type from approval_rule "
        "where document = :d and effective_from <= :o and (effective_to is null or effective_to >= :o) order by min_value",
        d=document,
        o=on,
    )
    scopes: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for r in rows:
        scopes.setdefault((r["category_code"], r["department_code"], r["spend_type"]), []).append(r)
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
        "spend_type": r["spend_type"],
        "chain": [p.strip() for p in str(r["approver_chain"]).split(">")],
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
    spend_type: str = "ANY"
    approver_chain: str
    effective_from: date
    reason: str | None = None


@router.post("/approval-rules", status_code=201)
def new_rule_version(body: RuleVersion, db: Db, ctx: RulesAdmin) -> dict[str, Any]:
    """A change to who approves is a new version from a date. Past decisions keep the version they used."""
    reason = _need_reason(body.reason)
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
            "approver_chain, version, effective_from) values (:c, :d, :lo, :hi, :cat, :dep, :sp, :ch, :v, :f) returning id"
        ),
        {
            "c": code, "d": body.document, "lo": body.min_value, "hi": body.max_value, "cat": body.category_code,
            "dep": body.department_code, "sp": body.spend_type, "ch": ">".join(chain), "v": version, "f": body.effective_from,
        },
    ).scalar_one()  # fmt: skip
    problems = coverage_problems(db, body.document, body.effective_from)
    if problems:
        db.rollback()
        raise _422("that would leave the rule table with a gap or an overlap: " + "; ".join(problems))
    new = _rule_json(_rows(db, "select * from approval_rule where id = :i", i=new_id)[0], today)
    _log(db, user_actor(ctx.user_id), "approval_rule", f"{code}:v{version}", old, new, reason)
    db.commit()
    return {"rule_code": code, "version": version}


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
    rows = _rows(db, "select key, value, category_code from tolerance order by key, category_code")
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
        "categories": ["ANY", *[r[0] for r in db.execute(text("select code from item_category order by code"))]],
    }


class PolicyBody(BaseModel):
    value: str
    reason: str | None = None


@router.put("/policies/{key}")
def put_policy(key: str, body: PolicyBody, db: Db, ctx: SettingsAdmin) -> dict[str, Any]:
    reason = _need_reason(body.reason)
    spec = POLICIES.get(key)
    if spec is None:
        raise HTTPException(404, f"there is no policy '{key}'")
    store, kind, label, _, default, low, high, choices = spec
    value = _validate(kind, body.value, low, high, choices, label)
    if key in MAILBOXES and value:
        other = next(k for k in MAILBOXES if k != key)
        if _stored(db, "org", other) == value:
            raise _422(f"{label}: that address already receives {POLICIES[other][2].lower()}; use a different one")
    return _write_policy(db, user_actor(ctx.user_id), key, store, value, default, reason)


def _write_policy(
    db: Session, actor: Actor, key: str, store: str, value: str, default: str, reason: str
) -> dict[str, Any]:
    old = _stored(db, store, key)
    if (old if old is not None else default) == value and old is not None:
        raise HTTPException(409, "that is already the value")
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
    _log(db, actor, "policy", key, {"value": old if old is not None else default}, {"value": value}, reason)
    db.commit()
    return {"key": key, "value": value}


class ToleranceBody(BaseModel):
    category: str = "ANY"
    value: str
    reason: str | None = None


@router.put("/tolerances/{key}")
def put_tolerance(key: str, body: ToleranceBody, db: Db, ctx: SettingsAdmin) -> dict[str, Any]:
    reason = _need_reason(body.reason)
    spec = TOLERANCES.get(key)
    if spec is None:
        raise HTTPException(404, f"there is no tolerance '{key}'")
    label, kind, low, high = spec
    value = _validate(kind, body.value, low, high, (), label)
    if (
        body.category != "ANY"
        and db.execute(text("select 1 from item_category where code = :c"), {"c": body.category}).first() is None
    ):
        raise _422(f"there is no category {body.category}")
    old = db.execute(
        text("select value from tolerance where key = :k and category_code = :c"), {"k": key, "c": body.category}
    ).scalar()
    if old is None:
        db.execute(
            text("insert into tolerance (key, value, category_code) values (:k, :v, :c)"),
            {"k": key, "v": value, "c": body.category},
        )
    else:
        db.execute(
            text("update tolerance set value = :v where key = :k and category_code = :c"),
            {"k": key, "v": value, "c": body.category},
        )
    _log(db, user_actor(ctx.user_id), "tolerance", f"{key}:{body.category}", {"value": old}, {"value": value}, reason)
    db.commit()
    return {"key": key, "category": body.category, "value": value}


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


@router.put("/automation/{decision_type}")
def put_automation(decision_type: str, body: AutomationBody, db: Db, ctx: SettingsAdmin) -> dict[str, Any]:
    reason = _need_reason(body.reason)
    actor = user_actor(ctx.user_id)
    if decision_type in autonomy.LOCKED:
        raise _422(f"'{decision_type}' is always decided by a person and can never be set to act alone")
    if decision_type not in DECISIONS:
        raise HTTPException(404, f"there is no decision type '{decision_type}'")
    if body.level not in LEVELS:
        raise _422(f"the level must be one of {', '.join(LEVELS)}")
    if body.value_cap is not None and body.value_cap < 0:
        raise _422("a cap cannot be negative")
    if not D(0) <= body.min_confidence <= D(1):
        raise _422("the confidence threshold is between 0 and 1")
    if not D(0) <= body.max_wrong_rate <= D(1):
        raise _422("the wrong-decision rate is between 0 and 1")
    if decision_type == "po.send":
        if body.level == "off":
            raise _422("orders have to be sent by someone: choose 'propose' (a person sends) or 'auto'")
        return _write_policy(
            db,
            actor,
            "po_send_requires_confirmation",
            "org",
            "true" if body.level == "propose" else "false",
            "true",
            reason,
        )
    old = db.execute(
        text("select level, value_cap, min_confidence, max_wrong_rate from autonomy_setting where decision_type = :d"),
        {"d": decision_type},
    ).first()
    autonomy.configure(
        db,
        actor,
        decision_type,
        level=body.level,
        value_cap=body.value_cap,
        min_confidence=body.min_confidence,
        max_wrong_rate=body.max_wrong_rate,
    )
    _log(
        db, actor, "automation", decision_type,
        {"level": old[0], "value_cap": old[1], "min_confidence": old[2], "max_wrong_rate": old[3]} if old else None,
        {"level": body.level, "value_cap": body.value_cap, "min_confidence": body.min_confidence, "max_wrong_rate": body.max_wrong_rate},
        reason,
    )  # fmt: skip
    db.commit()
    return {"decision_type": decision_type, "level": body.level}


class KillSwitch(BaseModel):
    on: bool
    reason: str | None = None


@router.put("/automation-kill-switch")
def put_kill_switch(body: KillSwitch, db: Db, ctx: SettingsAdmin) -> dict[str, bool]:
    reason = _need_reason(body.reason)
    actor = user_actor(ctx.user_id)
    old = bool(db.execute(text("select kill_switch from autonomy_global where id = 1")).scalar_one())
    autonomy.set_kill_switch(db, actor, body.on, reason)
    _log(db, actor, "automation", "kill_switch", {"on": old}, {"on": body.on}, reason)
    db.commit()
    return {"kill_switch": body.on}


# ---- the history ------------------------------------------------------------------------------


@router.get("/changes")
def list_changes(db: Db, ctx: AuditViewer, area: str | None = None, key: str | None = None, limit: int = 100) -> Rows:
    rows = _rows(
        db,
        "select id, at, by_user, area, key, old_value, new_value, reason from setup_change "
        "where (cast(:a as text) is null or area = :a) and (cast(:k as text) is null or key = :k) order by id desc limit :n",
        a=area,
        k=key,
        n=max(1, min(limit, 500)),
    )
    names = {p["user_id"]: p["display_name"] for p in _rows(db, "select user_id, display_name from user_profile")}
    for r in rows:
        r["at"] = _iso(r["at"])
        r["by_name"] = names.get(int(r["by_user"])) if str(r["by_user"]).isdigit() else r["by_user"]
    return rows
