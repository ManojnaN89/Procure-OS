"""The budget check on a request: for each GL account on it, is there enough budget left, both where the line is
charged (its cost centre, else its department's cost centres at its location) and at its location as a whole?

A line with a cost centre is charged to that cost centre. A line without one (a planning request does not know who will
use the goods) is charged to the cost centres of the request's department at its delivery location, and to all the cost
centres there when the department has none. A location may also have its own budget (budget rows with a location and no
cost centre); then every line delivered there, or charged to a cost centre there, is checked against it as well.

The budget counts from the start of the financial year (1 April) to the month of the check; the commitments are the
other requests of the year that are waiting for approval, approved, or already ordered. A check warns at
`budget_warn_pct` of the budget used (default 90) and is over above `budget_block_pct` (default 100); what an over budget
does follows `budget_policy` or the budget rows (warn or block). The check runs at submit, at each approval step, and
again when the order is made, on its final prices.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.formulas.budget import BLOCK_PCT, WARN_PCT, BudgetCheck, check_budget
from p2p.formulas.fy import financial_year, fy_bounds
from p2p.platform import settings

COMMITTED_STATUSES = ("submitted", "approved", "closed")
CENTS = Decimal("0.01")

# the cost centres a line is charged to (aliases: l the line, r its request, cc a cost centre)
_CHARGED_TO = """(cc.code = l.cost_centre_code or (l.cost_centre_code is null and cc.active
    and cc.location_code = coalesce(l.delivery_location, r.ship_to_location)
    and (cc.department_code = r.department_code or not exists (select 1 from cost_centre d where d.active
        and d.location_code = cc.location_code and d.department_code = r.department_code))))"""
# the location a line spends at: its cost centre's, else where it is delivered
_LOCATION_OF = (
    "coalesce((select c.location_code from cost_centre c where c.code = l.cost_centre_code), "
    "l.delivery_location, r.ship_to_location)"
)
_COMMITTED = (
    "select coalesce(sum(l.qty * coalesce(l.est_price, 0)), 0) from request_line l join request r on r.no = l.request_no "
    "where r.no <> :n and r.status = any(:st) and l.status <> 'cancelled' and l.gl_code = :g "
    "and r.created_at::date between :f and :t and "
)
LEVELS = {
    "cost_centre": "cost centre",
    "department": "department",
    "cost_centres": "all cost centres",
    "location": "location",
}


@dataclass(frozen=True)
class GroupCheck:
    gl_code: str
    cost_centres: tuple[str, ...]
    check: BudgetCheck
    policy: str  # warn or block
    level: str = "cost_centre"  # see LEVELS
    location: str | None = None
    department: str | None = None
    period: str = ""  # the months the budget counts, in words
    warn_pct: Decimal = WARN_PCT

    @property
    def holder(self) -> str:
        """Whose budget it is, in words."""
        if self.level == "location":
            return f"location {self.location}"
        if self.level == "department":
            return f"department {self.department} at {self.location}"
        if self.level == "cost_centres":
            return f"cost centres at {self.location}"
        return f"cost centre {', '.join(self.cost_centres)}"

    def as_json(self) -> dict[str, Any]:
        used = self.check.used_pct
        return {
            "gl_code": self.gl_code,
            "cost_centres": list(self.cost_centres),
            "level": self.level,
            "location": self.location,
            "department": self.department,
            "holder": self.holder,
            "period": self.period,
            "status": self.check.status,
            "budget": None if self.check.budget is None else str(self.check.budget.quantize(CENTS)),
            "committed": str(self.check.committed.quantize(CENTS)),
            "requested": str(self.check.requested.quantize(CENTS)),
            "over_by": str(self.check.over_by),
            "used_pct": None if used is None else str(used.quantize(CENTS)),
            "warn_pct": str(self.warn_pct),
            "block_pct": str(self.check.block_pct),
            "policy": self.policy,
        }


def _policy_setting(db: Session) -> str:
    return "block" if settings.value(db, "budget_policy", "warn") == "block" else "warn"


def period_words(today: date) -> str:
    """'Apr 2026 to Nov 2026 (FY 2026-27)': the months a check made today counts."""
    start, _ = fy_bounds(today)
    return f"{start:%b %Y} to {today:%b %Y} (FY {financial_year(today)})"


def _lines(db: Session, request_no: str, final_prices: bool) -> list[dict[str, Any]]:
    """The request's lines with a GL account: value, where each is charged and where it spends."""
    price = (
        "coalesce((select pl.unit_price from po_line pl where pl.request_no = l.request_no "
        "and pl.request_line_no = l.line_no and pl.status <> 'cancelled' limit 1), l.est_price, 0)"
        if final_prices
        else "coalesce(l.est_price, 0)"
    )
    sql = (
        f"select l.gl_code, l.cost_centre_code, r.department_code, {_LOCATION_OF} as location, l.qty * {price} as value, "  # noqa: S608 - fixed fragments only
        f"array(select cc.code from cost_centre cc where {_CHARGED_TO} order by cc.code) as scope, "
        "exists (select 1 from cost_centre d where d.active and d.department_code = r.department_code "
        "and d.location_code = coalesce(l.delivery_location, r.ship_to_location)) as department_has_one "
        "from request_line l join request r on r.no = l.request_no "
        "where l.request_no = :n and l.status <> 'cancelled' and l.gl_code is not null"
    )
    return [dict(r) for r in db.execute(text(sql), {"n": request_no}).mappings()]


def check_request(db: Session, request_no: str, today: date, *, final_prices: bool = False) -> list[GroupCheck]:
    """One result per GL account and cost-centre scope on the request, and one per GL account and location that has a
    budget of its own (lines without a GL account cannot be checked and are left out).

    With `final_prices` a line that has been ordered is valued at the order's price instead of the estimate: that is the
    check an order makes on its final amount."""
    start, fy_end = fy_bounds(today)
    months = {"a": start.strftime("%Y-%m"), "b": today.strftime("%Y-%m")}
    warn_pct = settings.number(db, "budget_warn_pct", str(WARN_PCT))
    block_pct = settings.number(db, "budget_block_pct", str(BLOCK_PCT))
    period = period_words(today)
    common = {"n": request_no, "st": list(COMMITTED_STATUSES), "f": start, "t": fy_end}
    setting_blocks = _policy_setting(db) == "block"

    charged: dict[tuple[str, tuple[str, ...], str, str | None, str | None], Decimal] = {}
    at_location: dict[tuple[str, str], Decimal] = {}
    for ln in _lines(db, request_no, final_prices):
        level = (
            "cost_centre" if ln["cost_centre_code"] else "department" if ln["department_has_one"] else "cost_centres"
        )
        key = (ln["gl_code"], tuple(ln["scope"]), level, ln["department_code"] if level == "department" else None,
               ln["location"])  # fmt: skip
        charged[key] = charged.get(key, Decimal(0)) + Decimal(ln["value"])
        if ln["location"]:
            at_location[(ln["gl_code"], ln["location"])] = at_location.get(
                (ln["gl_code"], ln["location"]), Decimal(0)
            ) + Decimal(ln["value"])

    out: list[GroupCheck] = []
    for (gl, scope, level, department, location), requested in sorted(charged.items(), key=lambda kv: str(kv[0])):
        if not scope:
            out.append(GroupCheck(gl, (), check_budget(None, Decimal(0), requested), "warn", level, location))
            continue
        held = (
            db.execute(
                text(
                    "select sum(amount) as total, bool_or(policy = 'block') as blocks, count(*) as n from budget "
                    "where gl_code = :g and cost_centre_code = any(:cc) and period between :a and :b"
                ),
                {"g": gl, "cc": list(scope), **months},
            )
            .mappings()
            .one()
        )
        committed = db.execute(
            text(_COMMITTED + "exists (select 1 from cost_centre cc where cc.code = any(:cc) and " + _CHARGED_TO + ")"),  # noqa: S608 - fixed fragments only
            {**common, "g": gl, "cc": list(scope)},
        ).scalar_one()
        check = check_budget(
            Decimal(held["total"]) if held["n"] else None,
            Decimal(committed),
            requested,
            warn_pct=warn_pct,
            block_pct=block_pct,
        )
        policy = "block" if held["blocks"] or setting_blocks else "warn"
        out.append(GroupCheck(gl, scope, check, policy, level, location, department, period, warn_pct))

    for (gl, location), requested in sorted(at_location.items()):
        held = (
            db.execute(
                text(
                    "select sum(amount) as total, bool_or(policy = 'block') as blocks, count(*) as n from budget "
                    "where gl_code = :g and location_code = :l and cost_centre_code is null "
                    "and period between :a and :b"
                ),
                {"g": gl, "l": location, **months},
            )
            .mappings()
            .one()
        )
        if not held["n"]:
            continue  # the location has no budget of its own for this account
        committed = db.execute(text(_COMMITTED + f"{_LOCATION_OF} = :l"), {**common, "g": gl, "l": location})
        check = check_budget(
            Decimal(held["total"]),
            Decimal(committed.scalar_one()),
            requested,
            warn_pct=warn_pct,
            block_pct=block_pct,
        )
        policy = "block" if held["blocks"] or setting_blocks else "warn"
        out.append(GroupCheck(gl, (), check, policy, "location", location, None, period, warn_pct))
    return out


def check_order(db: Session, po_no: str, today: date) -> list[GroupCheck]:
    """The budget check again, on the order's final prices: the request's other lines count at their estimates."""
    request_no = db.execute(text("select request_no from po where no = :n"), {"n": po_no}).scalar()
    return check_request(db, str(request_no), today, final_prices=True) if request_no else []


def blocked(checks: list[GroupCheck]) -> bool:
    """Over a budget whose policy blocks."""
    return any(c.check.status == "over" and c.policy == "block" for c in checks)


def summary(checks: list[GroupCheck]) -> str:
    """One plain sentence for the decision reason: which budget (whose, which account, which months) was hit."""
    if not checks or all(c.check.status == "unchecked" for c in checks):
        return "No budget was checked: no budget is set for these lines."
    over = [c for c in checks if c.check.status == "over"]
    if over:
        parts = "; ".join(f"GL {c.gl_code} by {c.check.over_by:,.2f} ({c.holder}, {c.period})" for c in over)
        return f"Over budget: {parts}."
    near = [c for c in checks if c.check.status == "warn"]
    if near:
        parts = "; ".join(f"GL {c.gl_code} {c.check.used_pct or 0:.2f}% used ({c.holder}, {c.period})" for c in near)
        return f"Within budget, but nearly used up: {parts}."
    return "Within budget."


def _money(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01")))


def variance(db: Session, today: date) -> dict[str, Any]:
    """Budget against what is committed and what was spent, from the start of the financial year to the month of `today`.

    One row per cost centre and GL account that has a budget: the budget for those months, what requests already hold
    (waiting, approved or ordered: the same count the budget check makes), what was spent (paid invoices, by the period of
    the invoice) and what is left to commit. Request lines with no cost centre are held against all the cost centres of their
    location, so they are listed apart rather than guessed onto one."""
    start, end = fy_bounds(today)
    first, last = start.strftime("%Y-%m"), today.strftime("%Y-%m")
    held = {
        (r[0], r[1]): Decimal(r[2])
        for r in db.execute(
            text(
                "select l.cost_centre_code, l.gl_code, sum(l.qty * coalesce(l.est_price, 0)) from request_line l "
                "join request r on r.no = l.request_no where r.status = any(:st) and l.status <> 'cancelled' "
                "and l.gl_code is not null and l.cost_centre_code is not null and r.created_at::date between :f and :t group by 1, 2"
            ),
            {"st": list(COMMITTED_STATUSES), "f": start, "t": end},
        )
    }
    spent = {
        (r[0], r[1]): Decimal(r[2])
        for r in db.execute(
            text(
                "select cost_centre_code, gl_code, sum(amount) from budget_actual where period between :a and :b group by 1, 2"
            ),
            {"a": first, "b": last},
        )
    }
    rows: list[dict[str, Any]] = []
    for cc, gl, name, amount, blocks in db.execute(
        text(
            "select b.cost_centre_code, b.gl_code, c.name, sum(b.amount), bool_or(b.policy = 'block') from budget b "
            "left join cost_centre c on c.code = b.cost_centre_code where b.period between :a and :b "
            "and b.cost_centre_code is not null group by 1, 2, 3 order by 1, 2"
        ),
        {"a": first, "b": last},
    ):
        budget_, committed, used = Decimal(amount), held.get((cc, gl), Decimal(0)), spent.get((cc, gl), Decimal(0))
        rows.append(
            {
                "cost_centre": cc,
                "cost_centre_name": name,
                "gl_code": gl,
                "budget": _money(budget_),
                "committed": _money(committed),
                "spent": _money(used),
                "left": _money(budget_ - committed),
                "over": committed > budget_,
                "policy": "block" if blocks else "warn",
            }
        )
    unassigned = [
        {"gl_code": r[0], "location": r[1], "committed": _money(Decimal(r[2]))}
        for r in db.execute(
            text(
                "select l.gl_code, coalesce(l.delivery_location, r.ship_to_location), sum(l.qty * coalesce(l.est_price, 0)) "
                "from request_line l join request r on r.no = l.request_no where r.status = any(:st) and l.status <> 'cancelled' "
                "and l.gl_code is not null and l.cost_centre_code is null and r.created_at::date between :f and :t group by 1, 2 order by 1, 2"
            ),
            {"st": list(COMMITTED_STATUSES), "f": start, "t": end},
        )
    ]
    return {"from": first, "to": last, "rows": rows, "unassigned": unassigned}
