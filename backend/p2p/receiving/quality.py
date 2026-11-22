"""Quality of stock (task 4.1): usable, quarantine (waiting for a quality decision) and blocked (judged bad, waiting for
return or scrap). Held stock (quarantine and blocked) is never set aside, issued or counted: `on_hand` everywhere is
usable stock.

Stock comes into quarantine when goods that need inspection are received (they wait there for the inspection, not yet
received for the order), and when a store person holds usable stock (damage, expiry, a suspect lot). Holding stock that
is set aside gives back the reservations of the lines needed latest first, then the store check runs for that place.
A decision moves quarantine stock to usable and/or blocked, or blocked stock back to usable, always with a reason;
usable stock gained is set aside at once for the open material requests. Blocked stock leaves only by a return or
scrap (block 9). Each change is a pair of `requalify` movements (minus the old quality, plus the new one) with the same
reference: the receipt for an inspection, `HOLD-<id>` otherwise. Every posting takes the per item-location lock.
"""

import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.approvals.workflow import org_code_of
from p2p.formulas.calendar import business_today
from p2p.formulas.stock import QTY
from p2p.platform import audit
from p2p.platform.sod import user
from p2p.receiving import stock
from p2p.requests import reservation

QUALITIES = ("quarantine", "blocked")
ZERO = Decimal(0)


class QualityError(Exception):
    """The hold or decision makes no sense: `status` is the HTTP answer (404, 409, 422)."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class Held:
    hold_id: int
    released: dict[str, dict[str, Decimal]] = field(default_factory=dict)
    store_check: bool = False


@dataclass
class Decided:
    hold_id: int
    left_qty: Decimal
    blocked_hold_id: int | None = None
    reserved_now: dict[str, dict[str, Decimal]] = field(default_factory=dict)


def _requalify(
    db: Session, item: str, location: str, qty: Decimal, frm: str, to: str, ref: str, on: date, cost: Decimal | None
) -> None:
    """Move `qty` from one quality to another: a pair of movements with the same reference."""
    for sign, quality in ((-1, frm), (1, to)):
        db.execute(
            text(
                "insert into stock_movement (id, item_code, location_code, date, type, qty, unit_cost, ref_doc, source, "
                "quality) values (:id, :i, :l, :d, 'requalify', :q, :c, :r, 'platform', :k)"
            ),
            {
                "id": f"SM-RQ-{ref}-{quality[:1].upper()}-{uuid.uuid4().hex[:8]}",
                "i": item, "l": location, "d": on, "q": sign * qty, "c": cost if quality == "usable" else None,
                "r": ref, "k": quality,
            },
        )  # fmt: skip


def _new_hold(
    db: Session, item: str, location: str, quality: str, qty: Decimal, reason: str, reference: str | None, source: str,
    by: str, *, gr: tuple[str, int] | None = None, parent: int | None = None,
) -> int:  # fmt: skip
    return int(
        db.execute(
            text(
                "insert into stock_hold (item_code, location_code, quality, qty, left_qty, reason, reference, source, "
                "gr_no, gr_line_no, parent_id, held_by) values (:i, :l, :k, :q, :q, :why, :ref, :s, :g, :gl, :p, :by) "
                "returning id"
            ),
            {
                "i": item, "l": location, "k": quality, "q": qty, "why": reason, "ref": reference, "s": source,
                "g": gr[0] if gr else None, "gl": gr[1] if gr else None, "p": parent, "by": by,
            },
        ).scalar_one()
    )  # fmt: skip


def _who(db: Session, user_id: int) -> str:
    return org_code_of(db, user_id) or f"user:{user_id}"


def hold(
    db: Session,
    tenant: str | None,
    user_id: int,
    item: str,
    location: str,
    qty: Decimal,
    quality: str,
    reason: str,
    reference: str | None = None,
    *,
    today: date | None = None,
) -> Held:
    """A store person holds usable stock as quarantine or blocked. What it takes from stock set aside is given back from
    the lines needed latest first, and the store check runs for the place. Commits."""
    today = today or business_today()
    why = (reason or "").strip()
    if quality not in QUALITIES:
        raise QualityError(422, "the quality must be quarantine or blocked")
    qty = Decimal(qty).quantize(QTY)
    if qty <= 0:
        raise QualityError(422, "the quantity must be more than zero")
    if not why:
        raise QualityError(422, "say why the stock is held")
    try:
        stock._check_place(db, item, location)  # noqa: SLF001 - the one check and lock of a stock place
    except stock.StockError as exc:
        db.rollback()
        raise QualityError(404, str(exc)) from None
    have = stock._on_hand(db, item, location)  # noqa: SLF001
    if qty > have:
        db.rollback()
        raise QualityError(
            409, f"only {have.normalize():f} usable is on the shelf, so {qty.normalize():f} cannot be held"
        )
    actor = user(user_id)
    hold_id = _new_hold(
        db, item, location, quality, qty, why, (reference or "").strip() or None, "manual", _who(db, user_id)
    )
    _requalify(db, item, location, qty, "usable", quality, f"HOLD-{hold_id}", today, None)
    over = qty - (have - stock.reserved(db, item, location))
    released = (
        reservation.release_latest(
            db, actor, item, location, over, f"{format(qty.normalize(), 'f')} held as {quality}: {why}"
        )
        if over > 0
        else {}
    )
    audit.record(
        db, actor, "stock.hold", "stock_hold", hold_id,
        after={"item": item, "location": location, "qty": str(qty), "quality": quality, "reference": reference,
               "released": {mr: {i: str(q) for i, q in v.items()} for mr, v in released.items()}},
        reason=why,
    )  # fmt: skip
    db.commit()
    result = Held(hold_id, released)
    if released:
        from p2p.requests import material  # noqa: PLC0415 - the store check buys what the hold took away

        material.check_after(db, tenant, [(item, location)], why=f"hold {hold_id}")
        result.store_check = True
    return result


def _locked_hold(db: Session, hold_id: int) -> dict[str, Any]:
    row = db.execute(text("select * from stock_hold where id = :i for update"), {"i": hold_id}).mappings().first()
    if row is None:
        db.rollback()
        raise QualityError(404, f"there is no hold {hold_id}")
    return dict(row)


def _cost(db: Session, item: str, location: str, gr_no: str | None) -> Decimal | None:
    """The cost of the stock coming back to usable: its receipt's, else the item's last."""
    if gr_no:
        found = db.execute(
            text(
                "select unit_cost from stock_movement where ref_doc = :g and item_code = :i and unit_cost is not null"
            ),
            {"g": gr_no, "i": item},
        ).scalar()
        if found is not None:
            return Decimal(found)
    return stock._last_cost(db, item, location)  # noqa: SLF001


def _apply(
    db: Session, h: dict[str, Any], to_usable: Decimal, to_blocked: Decimal, reason: str, by: str, ref: str, on: date
) -> int | None:
    """Move what was decided out of a hold. Returns the blocked hold made from a quarantine one."""
    cost = _cost(db, h["item_code"], h["location_code"], h["gr_no"])
    if to_usable:
        _requalify(db, h["item_code"], h["location_code"], to_usable, h["quality"], "usable", ref, on, cost)
    blocked = None
    if to_blocked:
        _requalify(db, h["item_code"], h["location_code"], to_blocked, "quarantine", "blocked", ref, on, None)
        blocked = _new_hold(
            db, h["item_code"], h["location_code"], "blocked", to_blocked, reason, h["reference"], "decision", by,
            gr=(h["gr_no"], h["gr_line_no"]) if h["gr_no"] else None, parent=int(h["id"]),
        )  # fmt: skip
    left = Decimal(h["left_qty"]) - to_usable - to_blocked
    db.execute(
        text(
            "update stock_hold set left_qty = :l, closed_at = case when :l = 0 then now() else closed_at end "
            "where id = :i"
        ),
        {"l": left, "i": h["id"]},
    )
    return blocked


def decide(
    db: Session,
    tenant: str | None,
    user_id: int,
    hold_id: int,
    to_usable: Decimal,
    to_blocked: Decimal,
    reason: str,
    *,
    today: date | None = None,
) -> Decided:
    """The quality decision on a hold: quarantine to usable and/or blocked, blocked back to usable. Usable stock gained
    is set aside at once for the open material requests. Commits."""
    today = today or business_today()
    why = (reason or "").strip()
    to_usable, to_blocked = Decimal(to_usable).quantize(QTY), Decimal(to_blocked).quantize(QTY)
    h = _locked_hold(db, hold_id)
    if h["closed_at"] is not None or h["left_qty"] <= 0:
        db.rollback()
        raise QualityError(409, f"hold {hold_id} is already closed")
    if h["source"] == "receipt":
        db.rollback()
        raise QualityError(409, f"hold {hold_id} waits for the inspection of {h['gr_no']}: record it on Receiving")
    if not why:
        db.rollback()
        raise QualityError(422, "say why")
    if to_usable < 0 or to_blocked < 0 or to_usable + to_blocked == 0:
        db.rollback()
        raise QualityError(422, "say how much goes to usable or to blocked (more than zero)")
    if to_blocked and h["quality"] == "blocked":
        db.rollback()
        raise QualityError(422, "the stock is already blocked")
    if to_usable + to_blocked > h["left_qty"]:
        db.rollback()
        raise QualityError(409, f"only {Decimal(h['left_qty']).normalize():f} is left on hold {hold_id}")
    db.execute(
        text("select pg_advisory_xact_lock(hashtext(:k))"), {"k": f"stock:{h['item_code']}:{h['location_code']}"}
    )
    actor = user(user_id)
    blocked = _apply(db, h, to_usable, to_blocked, why, _who(db, user_id), f"HOLD-{hold_id}", today)
    left = Decimal(h["left_qty"]) - to_usable - to_blocked
    audit.record(
        db, actor, "stock.hold_decide", "stock_hold", hold_id,
        before={"quality": h["quality"], "left_qty": str(h["left_qty"])},
        after={"to_usable": str(to_usable), "to_blocked": str(to_blocked), "left_qty": str(left),
               "blocked_hold": blocked},
        reason=why,
    )  # fmt: skip
    db.commit()
    reserved: dict[str, dict[str, Decimal]] = {}
    if to_usable:
        place = (h["item_code"], h["location_code"])
        reserved = reservation.stock_rose(db, tenant, [place], why=f"Hold {hold_id} released to usable: {why}")
    return Decided(hold_id, left, blocked, reserved)


def holds(db: Session, *, item: str | None = None, location: str | None = None) -> list[dict[str, Any]]:
    """The open holds, oldest first."""
    rows = db.execute(
        text(
            "select h.id, h.item_code, i.name as item_name, h.location_code, h.quality, h.qty, h.left_qty, h.reason, "
            "h.reference, h.source, h.gr_no, h.held_by, h.held_at from stock_hold h join item i on i.code = h.item_code "
            "where h.closed_at is null and (cast(:i as text) is null or h.item_code = :i) "
            "and (cast(:l as text) is null or h.location_code = :l) order by h.held_at, h.id"
        ),
        {"i": item, "l": location},
    ).mappings()
    return [dict(r) for r in rows]


# ---- goods that need inspection ----------------------------------------------------------------------


def receive_into_quarantine(
    db: Session,
    gr_no: str,
    line_no: int,
    item: str,
    location: str,
    qty: Decimal,
    cost: Decimal | None,
    by: str,
    on: date,
) -> int:
    """Goods that need inspection go into stock as quarantine at posting, waiting for the inspection. The caller commits."""
    db.execute(text("select pg_advisory_xact_lock(hashtext(:k))"), {"k": f"stock:{item}:{location}"})
    db.execute(
        text(
            "insert into stock_movement (id, item_code, location_code, date, type, qty, unit_cost, ref_doc, source, "
            "quality) values (:id, :i, :l, :d, 'receipt', :q, :c, :r, 'platform', 'quarantine')"
        ),
        {"id": f"SM-{gr_no}-{line_no}-Q", "i": item, "l": location, "d": on, "q": qty, "c": cost, "r": gr_no},
    )
    return _new_hold(
        db, item, location, "quarantine", qty, f"Waiting for the inspection of {gr_no}", gr_no, "receipt", by,
        gr=(gr_no, line_no),
    )  # fmt: skip


def inspect_hold(
    db: Session, gr_no: str, line_no: int, accepted: Decimal, rejected: Decimal, reason: str | None, by: str, on: date
) -> int | None:
    """The inspection decides a lot in quarantine: accepted to usable, rejected to blocked with the reason. The caller
    commits. Returns the blocked hold, if any."""
    row = (
        db.execute(
            text(
                "select * from stock_hold where source = 'receipt' and gr_no = :g and gr_line_no = :l "
                "and closed_at is null for update"
            ),
            {"g": gr_no, "l": line_no},
        )
        .mappings()
        .first()
    )
    if row is None:
        return None
    db.execute(
        text("select pg_advisory_xact_lock(hashtext(:k))"), {"k": f"stock:{row['item_code']}:{row['location_code']}"}
    )
    why = (reason or "").strip() or f"Rejected at the inspection of {gr_no}"
    return _apply(db, dict(row), accepted, rejected, why, by, gr_no, on)
