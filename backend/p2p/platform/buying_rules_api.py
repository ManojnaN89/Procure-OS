"""Company setup of the buying rules: the buying route, the supplier selection rule, the touchless order rule and the
items bought from one supplier only.

Each rule is versioned rows. A change is a new version from a date (today or later), made by the rule's own service,
which closes the version in force and writes the audit row; the setup history gets a row too. An old version is never
edited, so every decision keeps pointing at the version it used. For the Admin (permission `admin.rules`), like the
approval chains; every change needs a reason, and it applies only when a second administrator approves it (D-22,
`setting_changes`): the endpoint checks it and answers 202 with the proposal.
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
from p2p.platform import routing, setting_changes
from p2p.platform.permissions import require
from p2p.platform.rows import rows as _rows
from p2p.platform.setting_changes import Change
from p2p.platform.setup_api import need_reason, record_history
from p2p.platform.sod import Actor
from p2p.platform.sod import user as user_actor
from p2p.sourcing import price_review, route

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


def _submit(db: Session, ctx: routing.TenantContext, kind: str, args: dict[str, Any], reason: str) -> dict[str, Any]:
    return setting_changes.submit(db, user_actor(ctx.user_id), kind, args, reason, tenant=ctx.slug)


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


@router.post("/buying-route", status_code=202)
def new_buying_route(body: RouteVersion, db: Db, ctx: RulesAdmin) -> dict[str, Any]:
    reason = need_reason(body.reason)
    return _submit(db, ctx, "buying_route", body.model_dump(mode="json", exclude={"reason"}), reason)


@setting_changes.handler("buying_route", "admin.rules")
def _buying_route(db: Session, actor: Actor, args: dict[str, Any], reason: str, apply: bool) -> Change:
    body = RouteVersion.model_validate(args)
    category = body.category or None
    if category and db.execute(text("select 1 from item_category where code = :c"), {"c": category}).first() is None:
        raise _422(f"there is no item category {category}")
    _check_start(db, "buying_route_rule", body.effective_from, "category_code is not distinct from :c", c=category)
    old = route.in_force_row(db, category, body.effective_from)
    before = _plain({k: old[k] for k in ROUTE_FIELDS}) if old else None
    after = _plain(dict(body.values))
    key = category or "company"
    try:
        if apply:
            record_history(db, actor, "buying_route_rule", key, before, after, reason)
        version = route.save_rule(
            db,
            actor,
            dict(body.values),
            category=category,
            effective_from=body.effective_from,
            note=reason,
            apply=apply,
        )
    except route.RouteError as exc:
        raise _422(str(exc)) from None
    return Change("buying_route_rule", key, before, after, {"version": version})


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


@router.post("/supplier-selection", status_code=202)
def new_supplier_selection(body: SelectionVersion, db: Db, ctx: RulesAdmin) -> dict[str, Any]:
    reason = need_reason(body.reason)
    return _submit(db, ctx, "supplier_selection", body.model_dump(mode="json", exclude={"reason"}), reason)


@setting_changes.handler("supplier_selection", "admin.rules")
def _supplier_selection(db: Session, actor: Actor, args: dict[str, Any], reason: str, apply: bool) -> Change:
    body = SelectionVersion.model_validate(args)
    _check_start(db, "supplier_selection_rule", body.effective_from)
    try:
        weights = sel.Weights(*(body.weights[k] for k in ("price", "delivery", "quality", "payment_terms")))
    except KeyError:
        raise _422("give the four weights: price, delivery, quality and payment terms") from None
    if weights.total != 100:
        raise _422(f"the weights must add up to 100; they add up to {format(weights.total.normalize(), 'f')}")
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
    try:
        if apply:
            record_history(db, actor, "supplier_selection_rule", "company", before, new, reason)
        version = selection_rule.save_rule(
            db,
            actor,
            effective_from=body.effective_from,
            method=body.method,
            weights=weights,
            tie_break=tuple(body.tie_break),
            input_credit=body.input_credit,
            note=reason,
            apply=apply,
        )
    except selection_rule.SelectionRuleError as exc:
        raise _422(str(exc)) from None
    return Change("supplier_selection_rule", "company", before, new, {"version": version})


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


@router.post("/touchless", status_code=202)
def new_touchless(body: TouchlessVersion, db: Db, ctx: RulesAdmin) -> dict[str, Any]:
    reason = need_reason(body.reason)
    return _submit(db, ctx, "touchless", body.model_dump(mode="json", exclude={"reason"}), reason)


@setting_changes.handler("touchless", "admin.rules")
def _touchless(db: Session, actor: Actor, args: dict[str, Any], reason: str, apply: bool) -> Change:
    body = TouchlessVersion.model_validate(args)
    _check_start(db, "touchless_rule", body.effective_from)
    old = touchless.rule_for(db, body.effective_from)
    before = _plain({"value_limit": old.value_limit, "price_pct": old.price_pct})
    after = _plain({"value_limit": body.value_limit, "price_pct": body.price_pct})
    try:
        if apply:
            record_history(db, actor, "touchless_rule", "company", before, after, reason)
        version = touchless.save_rule(
            db,
            actor,
            effective_from=body.effective_from,
            value_limit=body.value_limit,
            price_pct=body.price_pct,
            note=reason,
            apply=apply,
        )
    except touchless.TouchlessRuleError as exc:
        raise _422(str(exc)) from None
    return Change("touchless_rule", "company", before, after, {"version": version})


# ---- the forward-buy rule (13.5) -------------------------------------------------------------------

FORWARD_FIELDS: dict[str, tuple[str, str]] = {
    "enabled": ("Forward buying is on", "bool"),
    "min_gap_pct": ("The market price is below its trend by more than", "pct"),
    "min_trend_months": ("Months of benchmarks the trend needs at least", "number"),
    "max_cover_months": ("Months ahead it may buy for at most", "number"),
    "holding_cost_pct_month": ("Holding the stock costs a month", "pct"),
    "min_net_saving_inr": ("The net saving is more than", "inr"),
    "shelf_life_months": ("The item keeps for (months)", "number"),
    "storage_max_qty": ("Stock may reach at most (stock units)", "number"),
}


@router.get("/forward-buy")
def get_forward_buy(db: Db, ctx: RulesAdmin) -> dict[str, Any]:
    today, names = business_today(), _names(db)
    versions = []
    for r in _rows(
        db,
        "select * from forward_buy_rule order by category_code nulls first, item_code nulls first, location_code nulls first, version desc",
    ):
        v = _version({**r, "created_by": r["entered_by"], "created_at": r["entered_at"]}, today, names)
        v["scope"] = {"category": r["category_code"], "item": r["item_code"], "location": r["location_code"]}
        v["values"] = {
            k: (None if r[k] is None else str(r[k]).lower() if k == "enabled" else format(D(r[k]).normalize(), "f"))
            for k in FORWARD_FIELDS
        }
        versions.append(v)
    return {
        "fields": [{"key": k, "label": label, "kind": kind} for k, (label, kind) in FORWARD_FIELDS.items()],
        "versions": versions,
    }


class ForwardVersion(BaseModel):
    category_code: str | None = None
    item_code: str | None = None
    location_code: str | None = None
    enabled: bool | None = None
    min_gap_pct: Decimal | None = None
    min_trend_months: int | None = None
    max_cover_months: int | None = None
    holding_cost_pct_month: Decimal | None = None
    min_net_saving_inr: Decimal | None = None
    shelf_life_months: int | None = None
    storage_max_qty: Decimal | None = None
    effective_from: date
    reason: str | None = None


@router.post("/forward-buy", status_code=202)
def new_forward_buy(body: ForwardVersion, db: Db, ctx: RulesAdmin) -> dict[str, Any]:
    reason = need_reason(body.reason)
    return _submit(db, ctx, "forward_buy", body.model_dump(mode="json", exclude={"reason"}), reason)


@setting_changes.handler("forward_buy", "admin.rules")
def _forward_buy(db: Session, actor: Actor, args: dict[str, Any], reason: str, apply: bool) -> Change:
    from p2p.requests import forward_buy  # noqa: PLC0415 - forward buying reads budgets and sourcing

    body = ForwardVersion.model_validate(args)
    scope = {"c": body.category_code, "i": body.item_code, "l": body.location_code}
    _check_start(
        db, "forward_buy_rule", body.effective_from,
        "category_code is not distinct from :c and item_code is not distinct from :i and location_code is not distinct from :l",
        **scope,
    )  # fmt: skip
    values = body.model_dump(exclude={"effective_from", "reason"})
    old = db.execute(
        text(
            "select * from forward_buy_rule where category_code is not distinct from :c and item_code is not distinct "
            "from :i and location_code is not distinct from :l order by version desc limit 1"
        ),
        scope,
    ).mappings().first()  # fmt: skip
    label = body.item_code and f"{body.item_code}{'@' + body.location_code if body.location_code else ''}"
    before = {k: None if old is None or old[k] is None else str(old[k]) for k in FORWARD_FIELDS}
    after = {k: None if values[k] is None else str(values[k]) for k in FORWARD_FIELDS}
    try:
        if apply:
            record_history(
                db, actor, "forward_buy_rule", label or body.category_code or "company", before, after, reason
            )
        version = forward_buy.save_rule(db, actor, values, effective_from=body.effective_from, note=reason, apply=apply)
    except forward_buy.ForwardBuyError as exc:
        raise _422(str(exc)) from None
    return Change("forward_buy_rule", label or body.category_code or "company", before, after, {"version": version})


# ---- the price threshold rule (12.5) -----------------------------------------------------------------

THRESHOLD_FIELDS: dict[str, tuple[str, str]] = {
    "gap_pct": ("Contract above the market by more than", "pct"),
    "gap_inr_per_unit": ("... and by more than this per unit", "inr"),
    "min_saving_inr": ("... and a saving at the relevant quantity above", "inr"),
    "evidence_max_age_days": ("A benchmark counts when no older than", "days"),
    "min_sources": ("... from at least this many sources", "count"),
    "expiry_review_days": ("A contract this near its end opens a review", "days"),
}
THRESHOLD_BANNER = "Default values need validation with the company (O2)"


@router.get("/price-thresholds")
def get_price_thresholds(db: Db, ctx: RulesAdmin) -> dict[str, Any]:
    today, names = business_today(), _names(db)
    versions = []
    for r in _rows(db, "select * from price_threshold_rule order by category_code nulls first, version desc"):
        v = _version(r, today, names)
        v["category"] = r["category_code"]
        v["values"] = _plain({k: r[k] for k in THRESHOLD_FIELDS})
        versions.append(v)
    return {
        "fields": [{"key": k, "label": label, "kind": kind} for k, (label, kind) in THRESHOLD_FIELDS.items()],
        "versions": versions,
        "categories": _rows(db, "select code, name from item_category order by code"),
        "banner": THRESHOLD_BANNER,
    }


class ThresholdVersion(BaseModel):
    values: dict[str, Decimal]
    category: str | None = None
    effective_from: date
    reason: str | None = None


@router.put("/price-thresholds", status_code=202)
def new_price_thresholds(body: ThresholdVersion, db: Db, ctx: RulesAdmin) -> dict[str, Any]:
    reason = need_reason(body.reason)
    return _submit(db, ctx, "price_threshold", body.model_dump(mode="json", exclude={"reason"}), reason)


@setting_changes.handler("price_threshold", "admin.rules")
def _price_threshold(db: Session, actor: Actor, args: dict[str, Any], reason: str, apply: bool) -> Change:
    body = ThresholdVersion.model_validate(args)
    category = body.category or None
    if category and db.execute(text("select 1 from item_category where code = :c"), {"c": category}).first() is None:
        raise _422(f"there is no item category {category}")
    _check_start(db, "price_threshold_rule", body.effective_from, "category_code is not distinct from :c", c=category)
    old = price_review.in_force_row(db, category, body.effective_from)
    before = _plain({k: old[k] for k in THRESHOLD_FIELDS}) if old else None
    after = _plain(dict(body.values))
    key = category or "company"
    try:
        if apply:
            record_history(db, actor, "price_threshold_rule", key, before, after, reason)
        version = price_review.save_rule(
            db, actor, dict(body.values), category=category, effective_from=body.effective_from, note=reason,
            apply=apply,
        )  # fmt: skip
    except price_review.ReviewError as exc:
        raise _422(str(exc)) from None
    return Change("price_threshold_rule", key, before, after, {"version": version})


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


@router.put("/single-source/{item_code}", status_code=202)
def put_single_source(item_code: str, body: SingleSource, db: Db, ctx: RulesAdmin) -> dict[str, Any]:
    reason = need_reason(body.reason)
    return _submit(db, ctx, "single_source", {"item_code": item_code, "supplier_code": body.supplier_code}, reason)


@router.delete("/single-source/{item_code}", status_code=202)
def drop_single_source(item_code: str, db: Db, ctx: RulesAdmin, reason: str = "") -> dict[str, Any]:
    reason = need_reason(reason)
    return _submit(db, ctx, "single_source", {"item_code": item_code, "supplier_code": None}, reason)


@setting_changes.handler("single_source", "admin.rules")
def _single_source(db: Session, actor: Actor, args: dict[str, Any], reason: str, apply: bool) -> Change:
    """Mark an item as bought from one supplier only, or (supplier None) clear the mark."""
    item_code, supplier = str(args["item_code"]), args.get("supplier_code")
    old = db.execute(
        text("select supplier_code from item_single_source where item_code = :i"), {"i": item_code}
    ).scalar()
    if supplier is None:
        if old is None:
            raise HTTPException(404, f"{item_code} is not marked as single source")
    else:
        if db.execute(text("select 1 from item where code = :c"), {"c": item_code}).first() is None:
            raise HTTPException(404, f"there is no item {item_code}")
        status = db.execute(text("select status from supplier where code = :c"), {"c": supplier}).scalar()
        if status is None:
            raise _422(f"there is no supplier {supplier}")
        if status != "active":
            raise _422(f"{supplier} is {status}: only an active supplier can be the single source")
    change = Change("item_single_source", item_code, old, supplier, {"item_code": item_code, "supplier_code": supplier})
    if apply:
        record_history(db, actor, change.area, item_code, old, supplier, reason)
        route.mark_single_source(db, actor, item_code, None if supplier is None else str(supplier), reason)
    return change
