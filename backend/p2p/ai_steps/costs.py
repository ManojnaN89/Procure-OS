"""Model prices and the company's monthly AI budget (task 11a.6, DF-32).

Prices are data: `data/reference/model_prices.csv` (one row per provider, model and date it took effect, with the plan,
where the price was read and when), plus any rows in MODEL_PRICES in .env. A call's cost is worked out here in Decimal
(US dollars, 6 places, as `model_call.cost_usd`); it is bookkeeping, never a business figure.

The cap is the policy `ai_monthly_cost_cap_usd` (empty: no cap; changed by two administrators). Before each model call
the gateway asks `check_cap`: the month's spend so far plus the call's worst case (its token estimate at the output
price) must stay within the cap, and a model with no known price is refused while a cap is set. The administrators are
told once a month when the spend passes `ai_cost_warn_pct` of the cap.
"""

import csv
import json
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.ai_steps.adapters import BadRequest
from p2p.config import get_settings

log = logging.getLogger(__name__)
CAP_KEY = "ai_monthly_cost_cap_usd"
WARN_KEY = "ai_cost_warn_pct"
DEFAULT_WARN_PCT = 80
PLACES = Decimal("0.000001")
MILLION = Decimal(1_000_000)


class CostCapReached(BadRequest):
    """The month's AI budget would be passed. A BadRequest: the gateway tries the next model of the route (a free one
    passes), and every caller already falls back on a model error."""


@dataclass(frozen=True)
class Price:
    provider: str
    model: str
    effective_from: date
    usd_in_per_mtok: Decimal
    usd_out_per_mtok: Decimal
    plan: str = ""
    source: str = ""


def _file() -> Path:
    from p2p.platform.reference import reference_dir  # noqa: PLC0415 - the reference folder can be configured

    return reference_dir() / "model_prices.csv"


def _row(r: dict[str, str]) -> Price:
    return Price(
        str(r["provider"]), str(r["model"]), date.fromisoformat(str(r["effective_from"])),
        Decimal(str(r["usd_in_per_mtok"])), Decimal(str(r["usd_out_per_mtok"])), str(r.get("plan") or ""),
        str(r.get("source") or ""),
    )  # fmt: skip


EXTRA: list[Price] = []  # rows a test adds without touching the file (applied last)


def prices() -> list[Price]:
    """Every price row: the file's, then MODEL_PRICES's (a row for the same model and date replaces the file's)."""
    rows: dict[tuple[str, str, date], Price] = {}
    path = _file()
    if path.exists():
        with path.open(encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                p = _row(r)
                rows[(p.provider, p.model, p.effective_from)] = p
    raw = get_settings().model_prices
    for r in json.loads(raw) if raw else []:
        p = _row(r)
        rows[(p.provider, p.model, p.effective_from)] = p
    for p in EXTRA:
        rows[(p.provider, p.model, p.effective_from)] = p
    return list(rows.values())


def price(provider: str, model: str, at: date) -> Price | None:
    """The price in force on the day: the latest row that took effect on or before it."""
    found = [p for p in prices() if p.provider == provider and p.model == model and p.effective_from <= at]
    return max(found, key=lambda p: p.effective_from) if found else None


def cost(p: Price | None, tokens_in: int, tokens_out: int) -> Decimal:
    """US dollars for one call, to 6 places (0 for a model with no price)."""
    if p is None:
        return Decimal(0).quantize(PLACES)
    raw = (Decimal(tokens_in) * p.usd_in_per_mtok + Decimal(tokens_out) * p.usd_out_per_mtok) / MILLION
    return raw.quantize(PLACES, rounding=ROUND_HALF_UP)


def month_bounds(at: datetime) -> tuple[datetime, datetime]:
    at = at.astimezone(UTC)
    start = datetime(at.year, at.month, 1, tzinfo=UTC)
    end = datetime(at.year + (at.month == 12), at.month % 12 + 1, 1, tzinfo=UTC)
    return start, end


def month_spend(db: Session, at: datetime | None = None) -> Decimal:
    """What this company's model calls cost in the calendar month (UTC) of `at`. Cache hits cost nothing."""
    start, end = month_bounds(at or datetime.now(UTC))
    got = db.execute(
        text(
            "select coalesce(sum(cost_usd), 0) from model_call where at >= :s and at < :e and outcome in ('ok', 'invalid')"
        ),
        {"s": start, "e": end},
    ).scalar_one()
    return Decimal(got)


def _setting(db: Session, key: str) -> str | None:
    value = db.execute(text("select value from org_setting where key = :k"), {"k": key}).scalar()
    return None if value is None or not str(value).strip() else str(value).strip()


def cap(db: Session) -> Decimal | None:
    value = _setting(db, CAP_KEY)
    return None if value is None else Decimal(value)


def warn_pct(db: Session) -> int:
    value = _setting(db, WARN_KEY)
    return int(value) if value is not None else DEFAULT_WARN_PCT


def check_cap(db: Session, provider: str, model: str, estimated_tokens: int, *, at: datetime | None = None) -> None:
    """Raise CostCapReached when this call could take the month past the cap. No cap: nothing to check."""
    limit = cap(db)
    if limit is None:
        return
    at = at or datetime.now(UTC)
    p = price(provider, model, at.date())
    if p is None:
        raise CostCapReached(f"no price is known for {provider}/{model}, and a monthly AI budget is set")
    spent = month_spend(db, at)
    worst = cost(
        Price(p.provider, p.model, p.effective_from, p.usd_out_per_mtok, p.usd_out_per_mtok), estimated_tokens, 0
    )
    if spent >= limit or spent + worst > limit:
        raise CostCapReached(f"this month's AI budget of ${limit} is used up (spent ${spent})")


def warn_if_passed(db: Session, at: datetime | None = None) -> bool:
    """After a call that cost money: tell the administrators once a month when the spend passes the warning share.
    Returns True when the warning went now. Commits."""
    from p2p.platform import events, notifications, tenants  # noqa: PLC0415 - the platform imports the gateway

    limit = cap(db)
    if limit is None or limit <= 0:
        return False
    at = at or datetime.now(UTC)
    spent, pct = month_spend(db, at), warn_pct(db)
    if spent * 100 < limit * pct:
        return False
    month = f"{at.astimezone(UTC):%Y-%m}"
    if not events.record(db, "ai_cost.warn", month, source="gateway", payload={"spent": str(spent)}).new:
        db.commit()
        return False
    tenant = tenants.slug_of(str(db.get_bind().engine.url.database or "")) or ""
    admins = db.execute(
        text(
            "select distinct ur.user_id from user_role ur join role_permission rp on rp.role = ur.role "
            "where rp.action = 'admin.settings' order by ur.user_id"
        )
    ).scalars()
    for uid in list(admins):
        notifications.notify(
            db, tenant, int(uid), "ai_cost", f"AI spend this month is at {pct}% of its budget",
            f"${spent} of ${limit} used in {month}. Model calls stop when the budget is used up.",
            link="#/automation",
        )  # fmt: skip
    db.commit()
    return True
