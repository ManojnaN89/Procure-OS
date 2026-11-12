"""Company setup of the buying rules: the buying route, the supplier selection rule, the touchless order rule and the
items bought from one supplier only.

Each rule is versioned rows. A change is a new version from a date (today or later), made by the rule's own service,
which closes the version in force and writes the audit row; the setup history gets a row too. An old version is never
edited, so every decision keeps pointing at the version it used. For the Admin (permission `admin.rules`), like the
approval chains; every change needs a reason.
"""

from datetime import date
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.formulas import selection as sel
from p2p.formulas.calendar import business_today
from p2p.formulas.money import round_money
from p2p.orders import selection_rule, touchless
from p2p.platform import routing
from p2p.platform.permissions import require
from p2p.platform.rows import rows as _rows
from p2p.platform.setup_api import need_reason, record_history
from p2p.platform.sod import user as user_actor
from p2p.sourcing import route

router = APIRouter(prefix="/api/setup", tags=["setup"])
Db = Annotated[Session, Depends(routing.tenant_session)]
RulesAdmin = Annotated[routing.TenantContext, Depends(require("admin.rules"))]
D = Decimal

# The thresholds of the buying route in the order the screen shows them: label and kind (rupees, percent or days).
ROUTE_FIELDS: dict[str, tuple[str, str]] = {
    "two_quotes_from": ("A quotation needs two quotes from", "inr"),
    "three_quotes_above": ("... and three quotes above", "inr"),
    "reorder_below": ("A safe reorder only under", "inr"),
    "reorder_within_days": ("... when the item was last bought within", "days"),
    "reorder_on_time_pct": ("... from a supplier on time at least", "pct"),
    "reorder_price_pct": ("... at a price within this of the last", "pct"),
    "agree_above": ("A single-source buy needs the purchase manager above", "inr"),
    "window_days": ("Values of the same item are added up over", "days"),
}
METHOD_NAMES = {"total_cost": "Lowest total cost", "weighted": "Weighted score"}
TIE_BREAK_NAMES = {"delivery": "Earlier delivery", "vendor_score": "Higher vendor score"}


def _422(message: str) -> HTTPException:
    return HTTPException(422, message)


def _names(db: Session) -> dict[str, str]:
    """'user:5' to the person's name, for the history."""
    return {f"user:{r[0]}": str(r[1]) for r in db.execute(text("select user_id, display_name from user_profile"))}


def _who(names: dict[str, str], by: Any) -> str:
    if by is None or str(by).startswith("system"):
        return "Set up with the company"
    return names.get(str(by), str(by))


def _version(r: dict[str, Any], today: date, names: dict[str, str]) -> dict[str, Any]:
    """The columns every rule version has, in words the screen shows."""
    start, end = r["effective_from"], r["effective_to"]
    return {
        "version": r["version"],
        "effective_from": start.isoformat(),
        "effective_to": end.isoformat() if end else None,
        "in_force": start <= today and (end is None or end >= today),
        "never_used": end is not None and end < start,  # replaced before the day it was to start
        "note": r["note"],
        "by": _who(names, r["created_by"]),
        "at": r["created_at"].isoformat() if r["created_at"] else None,
    }


def _check_start(db: Session, table: str, effective_from: date, where: str = "true", **params: Any) -> None:
    """A new version starts today or later, and not before the latest version: versions stay in date order."""
    if effective_from < business_today():
        raise _422("a new version starts today or later: decisions already made keep the version they were made under")
    latest = db.execute(text(f"select max(effective_from) from {table} where {where}"), params).scalar()  # noqa: S608 - fixed names
    if latest is not None and effective_from < latest:
        raise _422(f"the latest version starts on {latest:%d %b %Y}; a new one cannot start before it")


def _plain(values: dict[str, Any]) -> dict[str, str | None]:
    return {k: None if v is None else format(D(v).normalize(), "f") for k, v in values.items()}


# ---- the buying route ----------------------------------------------------------------------------


@router.get("/buying-route")
def get_buying_route(db: Db, ctx: RulesAdmin) -> dict[str, Any]:
    today, names = business_today(), _names(db)
    versions = []
    for r in _rows(db, "select * from buying_route_rule order by category_code nulls first, version desc"):
        v = _version(r, today, names)
        v["category"] = r["category_code"]
        v["values"] = _plain({k: r[k] for k in ROUTE_FIELDS})
        versions.append(v)
    return {
        "fields": [{"key": k, "label": label, "kind": kind} for k, (label, kind) in ROUTE_FIELDS.items()],
        "versions": versions,
        "categories": _rows(db, "select code, name from item_category order by code"),
    }


class RouteVersion(BaseModel):
    values: dict[str, Decimal]
    category: str | None = None  # None: the company's rule; a category's row names only the thresholds it tightens
    effective_from: date
    reason: str | None = None


@router.post("/buying-route", status_code=201)
def new_buying_route(body: RouteVersion, db: Db, ctx: RulesAdmin) -> dict[str, Any]:
    reason = need_reason(body.reason)
    category = body.category or None
    if category and db.execute(text("select 1 from item_category where code = :c"), {"c": category}).first() is None:
        raise _422(f"there is no item category {category}")
    _check_start(db, "buying_route_rule", body.effective_from, "category_code is not distinct from :c", c=category)
    actor = user_actor(ctx.user_id)
    old = route.in_force_row(db, category, body.effective_from)
    before = _plain({k: old[k] for k in ROUTE_FIELDS}) if old else None
    record_history(db, actor, "buying_route_rule", category or "company", before, _plain(dict(body.values)), reason)
    try:
        version = route.save_rule(
            db, actor, dict(body.values), category=category, effective_from=body.effective_from, note=reason
        )
    except route.RouteError as exc:
        db.rollback()
        raise _422(str(exc)) from None
    return {"version": version}


# ---- the supplier selection rule -------------------------------------------------------------------


@router.get("/supplier-selection")
def get_supplier_selection(db: Db, ctx: RulesAdmin) -> dict[str, Any]:
    today, names = business_today(), _names(db)
    versions = []
    for r in _rows(db, "select * from supplier_selection_rule order by version desc"):
        v = _version(r, today, names)
        v.update(
            method=r["method"],
            weights=_plain(
                {
                    "price": r["weight_price"],
                    "delivery": r["weight_delivery"],
                    "quality": r["weight_quality"],
                    "payment_terms": r["weight_payment_terms"],
                }
            ),
            tie_break=[t for t in str(r["tie_break"]).split(",") if t],
            input_credit=bool(r["input_credit"]),
        )
        versions.append(v)
    return {"versions": versions, "methods": METHOD_NAMES, "tie_breaks": TIE_BREAK_NAMES}


class SelectionVersion(BaseModel):
    method: str
    weights: dict[str, Decimal]
    tie_break: list[str]
    input_credit: bool
    effective_from: date
    reason: str | None = None


@router.post("/supplier-selection", status_code=201)
def new_supplier_selection(body: SelectionVersion, db: Db, ctx: RulesAdmin) -> dict[str, Any]:
    reason = need_reason(body.reason)
    _check_start(db, "supplier_selection_rule", body.effective_from)
    try:
        weights = sel.Weights(*(body.weights[k] for k in ("price", "delivery", "quality", "payment_terms")))
    except KeyError:
        raise _422("give the four weights: price, delivery, quality and payment terms") from None
    if weights.total != 100:
        raise _422(f"the weights must add up to 100; they add up to {format(weights.total.normalize(), 'f')}")
    actor = user_actor(ctx.user_id)
    old = selection_rule.rule_for(db, body.effective_from)
    new = {**body.model_dump(exclude={"reason", "effective_from"}), "weights": _plain(dict(body.weights))}
    before = {
        "method": old.method,
        "weights": _plain(
            {
                "price": old.weights.price,
                "delivery": old.weights.delivery,
                "quality": old.weights.quality,
                "payment_terms": old.weights.payment_terms,
            }
        ),
        "tie_break": list(old.tie_break),
        "input_credit": old.input_credit,
    }
    record_history(db, actor, "supplier_selection_rule", "company", before, new, reason)
    try:
        version = selection_rule.save_rule(
            db,
            actor,
            effective_from=body.effective_from,
            method=body.method,
            weights=weights,
            tie_break=tuple(body.tie_break),
            input_credit=body.input_credit,
            note=reason,
        )
    except selection_rule.SelectionRuleError as exc:
        db.rollback()
        raise _422(str(exc)) from None
    return {"version": version}


# ---- the touchless order rule ----------------------------------------------------------------------


@router.get("/touchless")
def get_touchless(db: Db, ctx: RulesAdmin) -> dict[str, Any]:
    today, names = business_today(), _names(db)
    versions = []
    for r in _rows(db, "select * from touchless_rule order by version desc"):
        v = _version(r, today, names)
        v.update(_plain({"value_limit": r["value_limit"], "price_pct": r["price_pct"]}))
        versions.append(v)
    top = touchless.buyer_tier_top(db, today)
    return {"versions": versions, "buyer_tier_max": None if top is None else str(round_money(top))}


class TouchlessVersion(BaseModel):
    value_limit: Decimal | None = None
    price_pct: Decimal
    effective_from: date
    reason: str | None = None


@router.post("/touchless", status_code=201)
def new_touchless(body: TouchlessVersion, db: Db, ctx: RulesAdmin) -> dict[str, Any]:
    reason = need_reason(body.reason)
    _check_start(db, "touchless_rule", body.effective_from)
    actor = user_actor(ctx.user_id)
    old = touchless.rule_for(db, body.effective_from)
    record_history(
        db,
        actor,
        "touchless_rule",
        "company",
        _plain({"value_limit": old.value_limit, "price_pct": old.price_pct}),
        _plain({"value_limit": body.value_limit, "price_pct": body.price_pct}),
        reason,
    )
    try:
        version = touchless.save_rule(
            db,
            actor,
            effective_from=body.effective_from,
            value_limit=body.value_limit,
            price_pct=body.price_pct,
            note=reason,
        )
    except touchless.TouchlessRuleError as exc:
        db.rollback()
        raise _422(str(exc)) from None
    return {"version": version}


# ---- items bought from one supplier only ---------------------------------------------------------


@router.get("/single-source")
def get_single_source(db: Db, ctx: RulesAdmin) -> dict[str, Any]:
    names = _names(db)
    marks = _rows(
        db,
        "select m.item_code, i.name as item_name, m.supplier_code, coalesce(s.trade_name, s.legal_name) as supplier_name, "
        "m.reason, m.marked_by, m.marked_at from item_single_source m left join item i on i.code = m.item_code "
        "left join supplier s on s.code = m.supplier_code order by m.item_code",
    )
    for m in marks:
        m["marked_by"] = _who(names, m["marked_by"])
        m["marked_at"] = m["marked_at"].isoformat()
    return {
        "marks": marks,
        "items": _rows(db, "select code, name from item where coalesce(active, true) order by code"),
        "suppliers": _rows(
            db,
            "select code, coalesce(trade_name, legal_name) as name from supplier where status = 'active' order by code",
        ),
    }


class SingleSource(BaseModel):
    supplier_code: str
    reason: str | None = None


@router.put("/single-source/{item_code}")
def put_single_source(item_code: str, body: SingleSource, db: Db, ctx: RulesAdmin) -> dict[str, str]:
    reason = need_reason(body.reason)
    if db.execute(text("select 1 from item where code = :c"), {"c": item_code}).first() is None:
        raise HTTPException(404, f"there is no item {item_code}")
    status = db.execute(text("select status from supplier where code = :c"), {"c": body.supplier_code}).scalar()
    if status is None:
        raise _422(f"there is no supplier {body.supplier_code}")
    if status != "active":
        raise _422(f"{body.supplier_code} is {status}: only an active supplier can be the single source")
    actor = user_actor(ctx.user_id)
    old = db.execute(
        text("select supplier_code from item_single_source where item_code = :i"), {"i": item_code}
    ).scalar()
    record_history(db, actor, "item_single_source", item_code, old, body.supplier_code, reason)
    route.mark_single_source(db, actor, item_code, body.supplier_code, reason)
    return {"item_code": item_code, "supplier_code": body.supplier_code}


@router.delete("/single-source/{item_code}")
def drop_single_source(item_code: str, db: Db, ctx: RulesAdmin, reason: str = "") -> dict[str, str]:
    reason = need_reason(reason)
    old = db.execute(
        text("select supplier_code from item_single_source where item_code = :i"), {"i": item_code}
    ).scalar()
    if old is None:
        raise HTTPException(404, f"{item_code} is not marked as single source")
    actor = user_actor(ctx.user_id)
    record_history(db, actor, "item_single_source", item_code, old, None, reason)
    route.mark_single_source(db, actor, item_code, None, reason)
    return {"item_code": item_code}
