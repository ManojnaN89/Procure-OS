"""Standard prices as versions (R5, 13.2, block 0b OP-19).

Version 1 of every item comes from the item master. A new version is proposed by Finance and approved by another
holder of `price.standard`; on approval the version in force the day before ends then, as a contract price version does
(D48). The estimate chain reads the version in force on its own date, so a change never rewrites an old estimate.
"""

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.formulas.money import format_inr, round_money
from p2p.platform import audit, proposals
from p2p.platform.sod import Actor, user

ACTION = "price.standard"


class StandardPriceError(Exception):
    def __init__(self, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.status = status


def in_force(db: Session, item_code: str, on: date) -> Decimal | None:
    """The version whose range holds the date; the item master's single price when it has no versions yet."""
    found = db.execute(
        text(
            "select price from item_standard_price where item_code = :i and status = 'active' "
            "and effective_from <= :d and (effective_to is null or effective_to >= :d) "
            "order by effective_from desc, version desc limit 1"
        ),
        {"i": item_code, "d": on},
    ).scalar()
    if found is not None:
        return Decimal(found)
    return db.execute(text("select standard_price from item where code = :i"), {"i": item_code}).scalar()


def versions(db: Session, item_code: str) -> list[dict[str, Any]]:
    rows = db.execute(
        text(
            "select item_code, price, version, effective_from, effective_to, status, reason, created_by, created_at, "
            "approved_by, approved_at from item_standard_price where item_code = :i order by version"
        ),
        {"i": item_code},
    ).mappings()
    return [dict(r) for r in rows]


def _first_open_month(db: Session, today: date) -> date:
    """The first day of the first month that is not locked (a new price never starts in a closed month)."""
    from p2p.approvals import budget_periods  # noqa: PLC0415 - the budget locks are block 3's

    held = set(budget_periods.locks(db))
    y, m = today.year, today.month
    for _ in range(24):
        period = f"{y}-{m:02d}"
        if period not in held:
            return date(y, m, 1)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    raise StandardPriceError("there is no open month to start a standard price in", 409)


def propose(
    db: Session,
    user_id: int,
    item_code: str,
    price: Decimal | str,
    effective_from: date,
    reason: str | None,
    today: date,
) -> int:
    """Check a new version and propose it; another holder of `price.standard` decides it (R5.2)."""
    why = (reason or "").strip()
    if not why:
        raise StandardPriceError("a reason is needed for a new standard price")
    if db.execute(text("select 1 from item where code = :c"), {"c": item_code}).first() is None:
        raise StandardPriceError(f"item {item_code} does not exist", 404)
    try:
        amount = round_money(Decimal(str(price)))
    except Exception:  # noqa: BLE001 - an unreadable amount is the same refusal
        raise StandardPriceError("the price is a number of rupees") from None
    if amount < 0:
        raise StandardPriceError("the price must not be negative")
    earliest = _first_open_month(db, today)
    if effective_from < earliest:
        raise StandardPriceError(f"a new price starts on or after {earliest:%d %b %Y}, the first open month", 409)
    version = int(
        db.execute(
            text("select coalesce(max(version), 0) + 1 from item_standard_price where item_code = :i"),
            {"i": item_code},
        ).scalar_one()
    )
    pid = proposals.propose(
        db,
        user(user_id),
        action=ACTION,
        summary=f"Standard price of {item_code}: {format_inr(amount)} from {effective_from:%d %b %Y}",
        payload={
            "item_code": item_code,
            "price": str(amount),
            "effective_from": effective_from.isoformat(),
            "version": version,
            "reason": why,
        },
        required_permission=ACTION,
    )
    db.execute(
        text(
            "insert into item_standard_price (item_code, price, version, effective_from, status, reason, proposal_id, "
            "created_by) values (:i, :p, :v, :d, 'proposed', :why, :pid, :by)"
        ),
        {"i": item_code, "p": amount, "v": version, "d": effective_from, "why": why, "pid": pid,
         "by": str(user_id)},
    )  # fmt: skip
    audit.record(db, user(user_id), "price.standard_proposed", "item", item_code,
                 after={"price": str(amount), "from": effective_from.isoformat(), "version": version,
                        "proposal": pid}, reason=why)  # fmt: skip
    db.commit()
    return pid


@proposals.executor(ACTION)
def _approved(db: Session, payload: dict[str, Any], actor: Actor) -> dict[str, Any]:
    item_code = str(payload["item_code"])
    start = date.fromisoformat(str(payload["effective_from"]))
    row = db.execute(
        text(
            "select id, version from item_standard_price where item_code = :i and version = :v and status = 'proposed' "
            "for update"
        ),
        {"i": item_code, "v": int(payload["version"])},
    ).first()
    if row is None:
        raise StandardPriceError("the proposed version no longer waits for a decision", 409)
    before = start - timedelta(days=1)
    db.execute(
        text(
            "update item_standard_price set effective_to = :d where item_code = :i and status = 'active' "
            "and effective_from <= :d and (effective_to is null or effective_to > :d)"
        ),
        {"d": before, "i": item_code},
    )
    db.execute(
        text("update item_standard_price set status = 'active', approved_by = :b, approved_at = now() where id = :i"),
        {"b": str(actor.id), "i": row[0]},
    )
    audit.record(db, actor, "price.standard", "item", item_code,
                 after={"price": payload["price"], "from": payload["effective_from"], "version": row[1]})  # fmt: skip
    return {"item": item_code, "version": row[1], "from": payload["effective_from"], "price": payload["price"]}


@proposals.closer(ACTION)
def _not_approved(db: Session, proposal_id: int, payload: dict[str, Any], actor: Actor) -> None:
    """A refused version is kept as rejected; it never becomes active (block 9's closer shape)."""
    db.execute(
        text(
            "update item_standard_price set status = 'rejected' where item_code = :i and version = :v "
            "and status = 'proposed'"
        ),
        {"i": payload.get("item_code"), "v": int(payload.get("version") or 0)},
    )
    db.commit()
