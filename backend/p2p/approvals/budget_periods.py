"""Budget months: locking a month, closing the financial year, and budget adjustments (task 3.7, D-09).

A month is open, locked or future (not ended yet). Finance (`budget.manage`) locks a month once it has ended and every
earlier month of its financial year is locked; a lock is never undone in Phase 1. A locked month is frozen: no actual is
booked in it (a late one goes to the next open month, `budget_ledger`), and no budget row for it is added or changed (a
masters import leaves it out, an adjustment is refused).

The year end (from 1 April) locks the months of the year not locked yet and keeps what was still open: request and order
lines carried into the new year (they count against it, as the budget check counts open commitments whatever their
date). Budget not used lapses: the check reads only the new year's rows.

An adjustment (a supplement, a return, or a transfer from one budget to another) is a proposal; another holder of
`budget.manage` approves it, and only then is it written as budget rows of kind `adjustment`.
"""

import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.approvals.workflow import org_code_of
from p2p.formulas import budget as rules
from p2p.formulas.fy import financial_year
from p2p.formulas.money import format_inr, round_money
from p2p.platform import audit, permissions, proposals, settings
from p2p.platform.sod import Actor, user

ADJUST = "budget.adjust"
KINDS = ("supplement", "return", "transfer")
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


class PeriodError(Exception):
    def __init__(self, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.status = status


def month_words(period: str) -> str:
    """'Nov 2026'."""
    return f"{MONTHS[int(period[5:]) - 1]} {period[:4]}"


def _month(d: date) -> str:
    return f"{d.year}-{d.month:02d}"


def locks(db: Session) -> dict[str, dict[str, Any]]:
    return {
        str(r["period"]): dict(r)
        for r in db.execute(text("select period, locked_by, locked_at, reason from budget_period_lock")).mappings()
    }


def is_locked(db: Session, period: str) -> bool:
    return db.execute(text("select 1 from budget_period_lock where period = :p"), {"p": period}).first() is not None


def _closed(db: Session, fy: str) -> bool:
    return db.execute(text("select 1 from budget_year_end where fy = :f"), {"f": fy}).first() is not None


def _who(db: Session, user_id: int) -> str:
    return org_code_of(db, user_id) or f"user:{user_id}"


def periods(db: Session, fy: str, today: date, user_id: int | None = None) -> dict[str, Any]:
    """The twelve months of a year with their state, and what the caller may do next."""
    if not rules.valid_fy(fy):
        raise PeriodError(f"'{fy}' is not a financial year like 2026-27")
    held = locks(db)
    now = _month(today)
    out = []
    for p in rules.fy_periods(fy):
        lock = held.get(p)
        status = "locked" if lock else "future" if p >= now else "open"
        out.append(
            {
                "period": p,
                "label": month_words(p),
                "status": status,
                "locked_by": lock["locked_by"] if lock else None,
                "locked_at": lock["locked_at"].isoformat() if lock else None,
                "reason": lock["reason"] if lock else None,
            }
        )
    manage = user_id is not None and permissions.can_anywhere(db, user_id, "budget.manage")
    next_open = next((r["period"] for r in out if r["status"] == "open"), None)
    can_lock = next_open if manage and next_open and rules.lockable(next_open, today, set(held)) is None else None
    closed = _closed(db, fy)
    ended = today >= date(int(fy[:4]) + 1, 4, 1)
    return {
        "fy": fy,
        "periods": out,
        "year_closed": closed,
        "can_lock": can_lock,
        "can_close": bool(manage and ended and not closed),
    }


def lock(db: Session, period: str, user_id: int, reason: str, today: date) -> dict[str, Any]:
    """Lock an ended month (every earlier month of its year first). The caller has `budget.manage`."""
    if not rules.valid_period(period):
        raise PeriodError(f"'{period}' is not a month like 2026-11")
    why = (reason or "").strip()
    if not why:
        raise PeriodError("a reason is needed to lock a month")
    refused = rules.lockable(period, today, set(locks(db)))
    if refused == "not ended":
        raise PeriodError(f"{period} has not ended")
    if refused:
        raise PeriodError(f"{period} cannot be locked: {refused}", 409)
    who = _who(db, user_id)
    at = db.execute(
        text("insert into budget_period_lock (period, locked_by, reason) values (:p, :b, :r) returning locked_at"),
        {"p": period, "b": who, "r": why},
    ).scalar_one()
    audit.record(db, user(user_id), "budget.period_lock", "budget_period", period, after={"locked_by": who}, reason=why)
    db.commit()
    return {"period": period, "status": "locked", "locked_by": who, "locked_at": at.isoformat()}


def carried(db: Session) -> list[dict[str, Any]]:
    """What requests and orders still hold, per account, cost centre and location: the open commitments."""
    rows = db.execute(
        text(
            "select gl_code, cost_centre_code, location_code, "
            "coalesce(sum(amount) filter (where part = 'request'), 0) as req, "
            "coalesce(sum(amount) filter (where part = 'order'), 0) as ord "
            "from budget_use where part in ('request', 'order') group by 1, 2, 3 "
            "having sum(amount) <> 0 order by 1, 2 nulls first, 3"
        )
    ).all()
    return [
        {
            "gl_code": r[0],
            "cost_centre_code": r[1],
            "location_code": r[2],
            "held_by_requests": str(round_money(r[3])),
            "held_by_orders": str(round_money(r[4])),
        }
        for r in rows
    ]


def close_year(db: Session, fy: str, user_id: int, note: str | None, today: date) -> dict[str, Any]:
    """The year end: lock the year's months not yet locked and keep the open commitments carried into the next year."""
    if not rules.valid_fy(fy):
        raise PeriodError(f"'{fy}' is not a financial year like 2026-27")
    if _closed(db, fy):
        raise PeriodError(f"FY {fy} is already closed", 409)
    if today < date(int(fy[:4]) + 1, 4, 1):
        raise PeriodError(f"FY {fy} has not ended: it can be closed from 1 April {int(fy[:4]) + 1}")
    who = _who(db, user_id)
    held = set(locks(db))
    newly = [p for p in rules.fy_periods(fy) if p not in held]
    for p in newly:
        db.execute(
            text("insert into budget_period_lock (period, locked_by, reason) values (:p, :b, :r)"),
            {"p": p, "b": who, "r": f"Year end FY {fy}"},
        )
    keep = carried(db)
    req = sum((Decimal(r["held_by_requests"]) for r in keep), Decimal(0))
    ord_ = sum((Decimal(r["held_by_orders"]) for r in keep), Decimal(0))
    totals = {"held_by_requests": str(req), "held_by_orders": str(ord_), "carried": str(req + ord_)}
    at = db.execute(
        text(
            "insert into budget_year_end (fy, closed_by, note, carried, totals) values (:f, :b, :n, "
            "cast(:c as jsonb), cast(:t as jsonb)) returning closed_at"
        ),
        {"f": fy, "b": who, "n": (note or "").strip() or None, "c": json.dumps(keep), "t": json.dumps(totals)},
    ).scalar_one()
    audit.record(
        db, user(user_id), "budget.year_close", "budget_year", fy,
        after={"locked": newly, "carried_total": totals["carried"], "groups": len(keep)}, reason=note,
    )  # fmt: skip
    db.commit()
    return {
        "fy": fy,
        "closed_at": at.isoformat(),
        "closed_by": who,
        "locked": newly,
        "carried": keep,
        "carried_total": totals["carried"],
        "totals": totals,
    }


def year(db: Session, fy: str) -> dict[str, Any] | None:
    row = (
        db.execute(
            text("select fy, closed_by, closed_at, note, carried, totals from budget_year_end where fy = :f"), {"f": fy}
        )
        .mappings()
        .first()
    )
    if row is None:
        return None
    return {
        "fy": row["fy"],
        "closed_by": row["closed_by"],
        "closed_at": row["closed_at"].isoformat(),
        "note": row["note"],
        "carried": row["carried"],
        "carried_total": row["totals"]["carried"],
        "totals": row["totals"],
    }


# ---- adjustments -----------------------------------------------------------------------------------------------


@dataclass
class Holder:
    gl_code: str
    cost_centre_code: str | None
    location_code: str | None
    period: str

    def words(self) -> str:
        who = f"cost centre {self.cost_centre_code}" if self.cost_centre_code else f"location {self.location_code}"
        return f"{who} account {self.gl_code} for {month_words(self.period)}"


def _holder(db: Session, raw: dict[str, Any], default: Holder | None = None) -> Holder:
    gl = raw.get("gl_code") or (default.gl_code if default else None)
    period = raw.get("period") or (default.period if default else None)
    cc, loc = raw.get("cost_centre_code") or None, raw.get("location_code") or None
    if not gl or not period:
        raise PeriodError("an adjustment names an account and a month")
    if bool(cc) == bool(loc):
        raise PeriodError("an adjustment is for a cost centre or for a location, not both and not neither")
    if not rules.valid_period(str(period)):
        raise PeriodError(f"'{period}' is not a month like 2026-11")
    if db.execute(text("select 1 from gl_account where code = :g"), {"g": gl}).first() is None:
        raise PeriodError(f"account {gl} is not known")
    if cc and db.execute(text("select 1 from cost_centre where code = :c"), {"c": cc}).first() is None:
        raise PeriodError(f"cost centre {cc} is not known")
    if loc and db.execute(text("select 1 from location where code = :l"), {"l": loc}).first() is None:
        raise PeriodError(f"location {loc} is not known")
    h = Holder(str(gl), cc, loc, str(period))
    if is_locked(db, h.period):
        raise PeriodError(f"{h.period} is locked")
    if _closed(db, rules.fy_of(h.period)):
        raise PeriodError(f"FY {rules.fy_of(h.period)} is closed")
    return h


def _rows_of(kind: str, amount: Decimal, frm: Holder, to: Holder | None) -> list[tuple[Holder, Decimal]]:
    if kind == "supplement":
        return [(frm, amount)]
    if kind == "return":
        return [(frm, -amount)]
    assert to is not None
    return [(frm, -amount), (to, amount)]


def propose_adjustment(db: Session, user_id: int, body: dict[str, Any]) -> int:
    """Check an adjustment and propose it; another holder of budget.manage decides it. Returns the proposal id."""
    kind = str(body.get("kind") or "")
    if kind not in KINDS:
        raise PeriodError("the kind is supplement, return or transfer")
    try:
        amount = round_money(Decimal(str(body.get("amount"))))
    except Exception:  # noqa: BLE001 - any unreadable amount is the same refusal
        raise PeriodError("the amount is a number of rupees") from None
    if amount <= 0:
        raise PeriodError("the amount must be above zero")
    why = str(body.get("reason") or "").strip()
    if not why:
        raise PeriodError("a reason is needed for a budget adjustment")
    frm = _holder(db, body)
    to = _holder(db, body.get("to") or {}, frm) if kind == "transfer" else None
    if to is not None and to == frm:
        raise PeriodError("a transfer goes to another budget")
    words = {
        "supplement": f"Add {format_inr(amount)} to {frm.words()}",
        "return": f"Take {format_inr(amount)} off {frm.words()}",
        "transfer": f"Move {format_inr(amount)} from {frm.words()} to {to.words() if to else ''}",
    }[kind]
    payload = {
        "kind": kind,
        "amount": str(amount),
        "reason": why,
        "from": frm.__dict__,
        "to": to.__dict__ if to else None,
    }
    pid = proposals.propose(
        db, user(user_id), action=ADJUST, summary=words, payload=payload, required_permission="budget.manage",
        risk="medium",
    )  # fmt: skip
    audit.record(db, user(user_id), "budget.adjust_proposed", "proposal", pid, after=payload, reason=why)
    db.commit()
    return pid


@proposals.executor(ADJUST)
def _adjust(db: Session, payload: dict[str, Any], actor: Actor) -> dict[str, Any]:
    """Approved: write the budget rows (checked again: a month may have been locked since)."""
    running = proposals.RUNNING.get()
    pid = running[0] if running else None
    frm = _holder(db, payload["from"])
    to = _holder(db, payload["to"]) if payload.get("to") else None
    amount = Decimal(payload["amount"])
    policy = "block" if settings.value(db, "budget_policy", "warn") == "block" else "warn"
    written = []
    for h, value in _rows_of(payload["kind"], amount, frm, to):
        row_policy = db.execute(
            text(
                "select policy from budget where gl_code = :g and cost_centre_code is not distinct from :c "
                "and location_code is not distinct from :l order by period desc limit 1"
            ),
            {"g": h.gl_code, "c": h.cost_centre_code, "l": h.location_code},
        ).scalar()
        db.execute(
            text(
                "insert into budget (cost_centre_code, gl_code, period, amount, version, policy, location_code, kind, "
                "reason, proposal_id) values (:c, :g, :p, :a, 1, :pol, :l, 'adjustment', :r, :pid)"
            ),
            {
                "c": h.cost_centre_code, "g": h.gl_code, "p": h.period, "a": value, "pol": row_policy or policy,
                "l": h.location_code, "r": payload["reason"], "pid": pid,
            },
        )  # fmt: skip
        written.append({**h.__dict__, "amount": str(value)})
    audit.record(db, actor, "budget.adjust", "proposal", pid, after={"rows": written}, reason=payload["reason"])
    return {"rows": written}


def fy_today(today: date) -> str:
    return financial_year(today)
