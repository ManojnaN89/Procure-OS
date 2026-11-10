"""The budget check on a request: for each GL account on it, is there enough budget left?

A line with a cost centre is held against that cost centre. A line without one (stock replenishment does not know
who will use the goods) is held against all the cost centres at its delivery location. The budget counts from the
start of the financial year to the month of the check; the commitments are the other requests of the year that are
waiting for approval, approved, or already ordered.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.formulas.budget import BudgetCheck, check_budget
from p2p.formulas.fy import fy_bounds

COMMITTED_STATUSES = ("submitted", "approved", "closed")


@dataclass(frozen=True)
class GroupCheck:
    gl_code: str
    cost_centres: tuple[str, ...]
    check: BudgetCheck
    policy: str  # warn or block

    def as_json(self) -> dict[str, Any]:
        return {
            "gl_code": self.gl_code,
            "cost_centres": list(self.cost_centres),
            "status": self.check.status,
            "budget": None if self.check.budget is None else str(self.check.budget.quantize(Decimal("0.01"))),
            "committed": str(self.check.committed.quantize(Decimal("0.01"))),
            "requested": str(self.check.requested.quantize(Decimal("0.01"))),
            "over_by": str(self.check.over_by),
            "policy": self.policy,
        }


def _policy_setting(db: Session) -> str:
    value = db.execute(text("select value from org_setting where key = 'budget_policy'")).scalar()
    return "block" if value == "block" else "warn"


def check_request(db: Session, request_no: str, today: date, *, final_prices: bool = False) -> list[GroupCheck]:
    """One result per GL account on the request (lines without a GL account cannot be checked and are left out).

    With `final_prices` a line that has been ordered is valued at the order's price instead of the estimate: that is the
    check an order makes on its final amount."""
    price = (
        "coalesce((select pl.unit_price from po_line pl where pl.request_no = l.request_no "
        "and pl.request_line_no = l.line_no and pl.status <> 'cancelled' limit 1), l.est_price, 0)"
        if final_prices
        else "coalesce(l.est_price, 0)"
    )
    lines = (
        db.execute(
            text(
                "select l.gl_code, l.cost_centre_code, coalesce(l.delivery_location, r.ship_to_location) as location, "  # noqa: S608 - the only variable part is one of two fixed fragments
                f"l.qty * {price} as value from request_line l join request r on r.no = l.request_no "
                "where l.request_no = :n and l.status <> 'cancelled' and l.gl_code is not null"
            ),
            {"n": request_no},
        )
        .mappings()
        .all()
    )
    groups: dict[tuple[str, str | None, str | None], Decimal] = {}
    for ln in lines:
        key = (ln["gl_code"], ln["cost_centre_code"], None if ln["cost_centre_code"] else ln["location"])
        groups[key] = groups.get(key, Decimal(0)) + Decimal(ln["value"])
    start, _ = fy_bounds(today)
    first_period, last_period = start.strftime("%Y-%m"), today.strftime("%Y-%m")
    fy_end = fy_bounds(today)[1]
    out: list[GroupCheck] = []
    for (gl, cc, location), requested in sorted(
        groups.items(), key=lambda kv: (kv[0][0], kv[0][1] or "", kv[0][2] or "")
    ):
        scope = (
            [cc]
            if cc
            else [
                r[0]
                for r in db.execute(
                    text("select code from cost_centre where location_code = :l and active order by code"),
                    {"l": location},
                )
            ]
        )
        if not scope:
            out.append(GroupCheck(gl, (), check_budget(None, Decimal(0), requested), "warn"))
            continue
        rows = (
            db.execute(
                text(
                    "select sum(amount) as total, bool_or(policy = 'block') as blocks, count(*) as n from budget "
                    "where gl_code = :g and cost_centre_code = any(:cc) and period between :a and :b"
                ),
                {"g": gl, "cc": scope, "a": first_period, "b": last_period},
            )
            .mappings()
            .one()
        )
        budget = Decimal(rows["total"]) if rows["n"] else None
        committed = Decimal(
            db.execute(
                text(
                    "select coalesce(sum(l.qty * coalesce(l.est_price, 0)), 0) from request_line l "
                    "join request r on r.no = l.request_no "
                    "where r.no <> :n and r.status = any(:st) and l.status <> 'cancelled' and l.gl_code = :g "
                    "and r.created_at::date between :f and :t "
                    "and (l.cost_centre_code = any(:cc) or (l.cost_centre_code is null and "
                    "coalesce(l.delivery_location, r.ship_to_location) in "
                    "(select location_code from cost_centre where code = any(:cc))))"
                ),
                {"n": request_no, "st": list(COMMITTED_STATUSES), "g": gl, "cc": scope, "f": start, "t": fy_end},
            ).scalar_one()
        )
        policy = "block" if rows["blocks"] or _policy_setting(db) == "block" else "warn"
        out.append(GroupCheck(gl, tuple(scope), check_budget(budget, committed, requested), policy))
    return out


def check_order(db: Session, po_no: str, today: date) -> list[GroupCheck]:
    """The budget check again, on the order's final prices: the request's other lines count at their estimates."""
    request_no = db.execute(text("select request_no from po where no = :n"), {"n": po_no}).scalar()
    return check_request(db, str(request_no), today, final_prices=True) if request_no else []


def summary(checks: list[GroupCheck]) -> str:
    """One plain sentence for the decision reason."""
    if not checks or all(c.check.status == "unchecked" for c in checks):
        return "No budget was checked: no budget is set for these lines."
    over = [c for c in checks if c.check.status == "over"]
    if over:
        parts = ", ".join(f"GL {c.gl_code} by {c.check.over_by:,.2f}" for c in over)
        return f"Over budget: {parts}."
    return "Within budget."
