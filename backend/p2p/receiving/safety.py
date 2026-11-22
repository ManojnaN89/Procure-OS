"""Using safety stock (control C4): only with the manager's override.

Safety stock (`item_location.safety_stock`) is never set aside for a material request nor issued by hand. A store person
who must use it asks, with a reason, either for an issue by hand (`issue`: item, place, quantity) or for a material
request line (`material`: request, line, quantity). That is a proposal (rule 5): someone in the asker's manager chain
(`org_user.manager_code`, upwards) who may approve requests decides it; never the asker. On approval the issue is posted,
or the stock is set aside for the line (up to what it still lacks, and what is usable and not set aside) and what open
requests not yet with the buyer bring for it is cut by the part that was not short or at risk. A proposal not decided
within seven days expires.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.approvals.workflow import name_of, org_code_of
from p2p.formulas import material as rules
from p2p.formulas.calendar import business_today
from p2p.formulas.money import format_qty
from p2p.formulas.stock import QTY
from p2p.platform import audit, notifications, permissions, proposals, tenants
from p2p.platform.sod import Actor, user
from p2p.receiving import stock
from p2p.requests import reservation, supply_dates

ACTION = "stock.safety_override"
APPROVE = "request.approve"
EXPIRES_AFTER = timedelta(days=7)
ZERO = Decimal(0)


class OverrideError(Exception):
    """The request to use safety stock makes no sense: `status` is the HTTP answer (404, 409, 422)."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Asked:
    proposal_id: int
    approver_hint: str


def _qty(value: Any) -> Decimal:
    try:
        qty = Decimal(str(value)).quantize(QTY)
    except (InvalidOperation, ValueError):
        raise OverrideError(422, "qty must be a number") from None
    if not qty.is_finite() or qty <= 0:
        raise OverrideError(422, "the quantity must be more than zero")
    return qty


def chain(db: Session, code: str | None) -> list[str]:
    """The asker's managers, nearest first (org codes)."""
    out: list[str] = []
    while code:
        code = db.execute(text("select manager_code from org_user where code = :c"), {"c": code}).scalar() or None
        if not code or code in out:
            break
        out.append(str(code))
    return out


def _first_approver(db: Session, tenant: str, asker: str | None) -> str | None:
    for code in chain(db, asker):
        login = notifications.login_of(db, tenant, code)
        if login and permissions.can_anywhere(db, login, APPROVE):
            return code
    return None


def _material_line(db: Session, mr_no: str, line_no: int) -> dict[str, Any]:
    row = (
        db.execute(
            text(
                "select l.id, l.mr_no, l.line_no, l.item_code, l.location_code, l.uom, l.need_qty, l.reserved_qty, "
                "l.issued_qty, l.need_by, l.status, l.requested_qty, m.order_no, m.status as mr_status "
                "from material_request_cover l join material_request m on m.no = l.mr_no "
                "where l.mr_no = :n and l.line_no = :l"
            ),
            {"n": mr_no, "l": line_no},
        )
        .mappings()
        .first()
    )
    if row is None:
        raise OverrideError(404, f"there is no line {line_no} on material request {mr_no}")
    return dict(row)


def _open_cover(db: Session, line_id: int) -> Decimal:
    return Decimal(
        db.execute(
            text(
                "select coalesce(sum(b.qty), 0) from material_request_buy b join request_line rl "
                "on rl.request_no = b.request_no and rl.line_no = b.request_line_no join request r on r.no = b.request_no "
                "where b.mr_line_id = :l and rl.status = 'open' and r.status = any(:live)"
            ),
            {"l": line_id, "live": ["draft", "submitted", "approved", "returned"]},
        ).scalar_one()
    )


def ask(db: Session, tenant: str, user_id: int, payload: dict[str, Any]) -> Asked:
    """The store person asks to use safety stock. Checked now; nothing changes until the manager approves."""
    kind = str(payload.get("kind") or "")
    reason = str(payload.get("reason") or "").strip()
    if kind not in ("issue", "material"):
        raise OverrideError(422, "kind must be issue or material")
    if not reason:
        raise OverrideError(422, "say why the safety stock is needed")
    qty = _qty(payload.get("qty"))
    if kind == "issue":
        item, location = str(payload.get("item_code") or ""), str(payload.get("location") or "")
        args: dict[str, Any] = {"kind": kind, "item_code": item, "location": location, "qty": str(qty),
                                "reference": str(payload.get("reference") or "").strip() or None}  # fmt: skip
    else:
        mr_no = str(payload.get("mr_no") or "")
        try:
            line_no = int(payload.get("line_no") or 0)
        except (TypeError, ValueError):
            raise OverrideError(422, "line_no must be a number") from None
        ln = _material_line(db, mr_no, line_no)
        if ln["status"] != "open" or ln["mr_status"] != "open":
            raise OverrideError(409, f"line {line_no} of {mr_no} is not open")
        item, location = ln["item_code"], ln["location_code"]
        args = {"kind": kind, "mr_no": mr_no, "line_no": line_no, "qty": str(qty)}
    if db.execute(text("select 1 from item where code = :c"), {"c": item}).first() is None:
        raise OverrideError(404, f"there is no item {item}")
    if (
        db.execute(text("select 1 from location where code = :c and is_stock_location"), {"c": location}).first()
        is None
    ):
        raise OverrideError(404, f"{location} is not a stock location")
    _check(db, args, qty)
    asker = org_code_of(db, user_id)
    hint = _first_approver(db, tenant, asker)
    if hint is None:
        raise OverrideError(409, "nobody above you may approve using safety stock: ask an administrator")
    what = (
        f"issue {format_qty(qty)} {item} at {location}"
        if kind == "issue"
        else f"set aside {format_qty(qty)} {item} for {args['mr_no']} line {args['line_no']}"
    )
    pid = proposals.propose(
        db, user(user_id), action=ACTION, summary=f"Use safety stock: {what}. {reason}",
        payload={**args, "reason": reason, "asker": asker}, required_permission=APPROVE, risk="medium",
        document=None, expires_at=datetime.now(UTC) + EXPIRES_AFTER,
    )  # fmt: skip
    login = notifications.login_of(db, tenant, hint)
    if login:
        notifications.notify(
            db, tenant, login, "proposal", "Safety stock needed", f"{name_of(db, asker)} asks to {what}: {reason}",
            link="#/approvals", email=False,
        )  # fmt: skip
    return Asked(pid, name_of(db, hint))


def _position(db: Session, item: str, location: str) -> tuple[Decimal, Decimal, Decimal]:
    """Usable on hand, set aside, safety stock."""
    return stock._on_hand(db, item, location), stock.reserved(db, item, location), stock.safety(db, item, location)  # noqa: SLF001


def _check(db: Session, args: dict[str, Any], qty: Decimal) -> Decimal:
    """Is the safety stock really needed, and is the quantity within it? Returns what the line may take (material)."""
    if args["kind"] == "issue":
        usable, held, safety = _position(db, args["item_code"], args["location"])
        available = rules.free_stock(usable, held, safety)
        if qty <= available:
            raise OverrideError(409, f"{format_qty(qty)} is available without the safety stock: issue it")
        from_safety = qty - max(available, ZERO)
        if from_safety > min(safety, max(usable - held, ZERO)):
            raise OverrideError(
                422, f"{format_qty(qty)} takes {format_qty(from_safety)} from a safety stock of {format_qty(safety)}"
                f" with {format_qty(max(usable - held, ZERO))} free of material requests"
            )  # fmt: skip
        return qty
    ln = _material_line(db, str(args["mr_no"]), int(args["line_no"]))
    usable, held, safety = _position(db, ln["item_code"], ln["location_code"])
    lacking = max(Decimal(ln["need_qty"]) - Decimal(ln["issued_qty"]) - Decimal(ln["reserved_qty"]), ZERO)
    if lacking <= 0:
        raise OverrideError(409, f"line {ln['line_no']} of {ln['mr_no']} lacks nothing")
    if rules.free_stock(usable, held, safety) > 0:
        raise OverrideError(409, "stock is free without the safety stock: run the store check")
    if qty > lacking:
        raise OverrideError(422, f"the line lacks only {format_qty(lacking)}")
    if qty > min(safety, max(usable - held, ZERO)):
        raise OverrideError(422, f"{format_qty(qty)} is more than the safety stock there holds for it")
    return qty


@proposals.guard(ACTION)
def _manager(db: Session, payload: dict[str, Any], approver: Actor) -> None:
    """Only someone in the asker's manager chain who may approve requests; never the asker (segregation of duties)."""
    code = org_code_of(db, int(approver.id)) if approver.kind == "user" else None
    asker = payload.get("asker")
    if code is None or code == asker:
        raise PermissionError("the person who asked cannot approve using safety stock")
    if code not in chain(db, asker):
        raise PermissionError("only a manager of the person who asked may approve using safety stock")


@proposals.executor(ACTION)
def _execute(db: Session, payload: dict[str, Any], approver: Actor) -> dict[str, Any]:
    running = proposals.RUNNING.get()
    pid = running[0] if running else None
    asker = running[1] if running else approver
    qty = Decimal(str(payload["qty"]))
    reason = str(payload.get("reason") or "")
    audit.record(
        db, approver, ACTION, "proposal", pid,
        after={"asker": payload.get("asker"), "approver": org_code_of(db, int(approver.id)), "kind": payload["kind"],
               "qty": str(qty)},
        reason=reason,
    )  # fmt: skip
    if payload["kind"] == "issue":
        movement = stock.issue_movement(
            db, asker, str(payload["item_code"]), str(payload["location"]), qty, reference=payload.get("reference"),
            today=business_today(), override_proposal=pid,
        )  # fmt: skip
        db.commit()
        return {"movement": movement}
    return _set_aside(db, approver, payload, qty, reason)


def _set_aside(db: Session, actor: Actor, payload: dict[str, Any], qty: Decimal, reason: str) -> dict[str, Any]:
    """Set safety stock aside for a material request line and cut the open cover it replaces."""
    ln = _material_line(db, str(payload["mr_no"]), int(payload["line_no"]))
    place = (ln["item_code"], ln["location_code"])
    db.execute(text("select pg_advisory_xact_lock(hashtext('store_check'))"))
    db.execute(text("select pg_advisory_xact_lock(hashtext(:k))"), {"k": f"stock:{place[0]}:{place[1]}"})
    db.execute(text("select id from material_request_line where id = :i for update"), {"i": ln["id"]})
    ln = _material_line(db, str(payload["mr_no"]), int(payload["line_no"]))
    usable, held, _ = _position(db, *place)
    dated = supply_dates.cover_by_date(db, [ln])[ln["id"]]
    c = rules.cover(ln["need_qty"], ln["issued_qty"], ln["reserved_qty"], dated.timely, dated.late)
    take = max(min(qty, c.lacking, usable - held), ZERO)
    if ln["status"] != "open" or take <= 0:
        db.rollback()
        return {"reserved": "0", "requests_cut": []}
    db.execute(
        text("update material_request_line set reserved_qty = reserved_qty + :q where id = :i"),
        {"q": take, "i": ln["id"]},
    )
    cut_qty = min(max(take - c.short - c.at_risk, ZERO), _open_cover(db, ln["id"]))
    cuts = reservation._cut_cover(db, actor, ln, cut_qty, why=f"safety stock set aside: {reason}") if cut_qty else []  # noqa: SLF001
    audit.record(
        db, actor, "material_request.reserve", "material_request", ln["mr_no"],
        after={"reserved_now": {ln["item_code"]: str(take)}, "from_safety": str(take),
               "requests_cut": [f"{c.request_no}/{c.line_no}" for c in cuts]},
        reason=reason,
    )  # fmt: skip
    db.commit()
    tenant = tenants.slug_of(str(db.get_bind().engine.url.database or ""))
    reservation.settle(db, tenant, cuts, why="Safety stock now covers what it was raised for")
    return {"reserved": format_qty(take), "requests_cut": [f"{c.request_no}/{c.line_no}" for c in cuts]}
