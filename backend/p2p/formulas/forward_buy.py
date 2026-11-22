"""Forward buying (task 13.5): when an item's market price is low against its trend, how much more to buy now and what
it saves after the cost of holding the stock. Pure arithmetic on numbers the service reads; never a model.

- Trend: least squares over monthly price points (x = months before the evaluation month, -12 .. -1):
  b = S(x - xbar)(y - ybar) / S(x - xbar)^2, a = ybar - b xbar; trend now T = a (4 places), expected price in month x
  E(x) = a + b x (4 places).
- Dip = (T - P) / T x 100 (4 places); it must be above the rule's minimum (exactly at it is not enough).
- Need in a month = max(what open material requirements still need then, average use). Cover = usable stock plus open
  supply less the need from the evaluation month to the anchor month, never below nothing.
- Holding per unit and month h = P x holding % a month / 100 (4 places). Month m ahead of the anchor month nets
  E(x0 + m) - P - m h per unit; a month that nets nothing or less is left out. Months run to the cover limit and stay
  below the shelf life.
- The quantity is the need of those months after the cover is used up from the first one, cut to the storage room left
  (storage less cover) and to the budget left at the estimate, then rounded down to whole purchase units; every cut comes
  off the latest months. Money: gross, holding and net in paise half up on the totals; value = purchase units x estimate.
- Recommended only when something is bought and the net saving is above the rule's minimum (exactly at it is not).
"""

from dataclasses import dataclass, field, replace
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal, localcontext

ZERO = Decimal(0)
FOUR = Decimal("0.0001")
PAISE = Decimal("0.01")
LIMITS = ("max_cover_months", "holding", "shelf_life", "storage", "budget", "need")


def q4(v: Decimal) -> Decimal:
    return v.quantize(FOUR, rounding=ROUND_HALF_UP)


def money(v: Decimal) -> Decimal:
    return v.quantize(PAISE, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class Rule:
    label: str  # "forward_buy v1 + RM-08@PLT-CKN v2": every row used
    enabled: bool
    min_gap_pct: Decimal
    min_trend_months: int
    max_cover_months: int
    holding_cost_pct_month: Decimal
    min_net_saving_inr: Decimal
    shelf_life_months: int | None = None
    storage_max_qty: Decimal | None = None


@dataclass(frozen=True)
class Inputs:
    price_now: Decimal  # P, per stock unit
    points: list[tuple[int, Decimal]]  # (x, monthly average per stock unit), x = -12 .. -1
    avg_use: Decimal  # A, stock units a month
    needs: dict[int, Decimal]  # month offset from the evaluation month (0, 1, ...) -> what requirements still need then
    usable: Decimal  # usable stock on hand
    supply: Decimal  # live request lines and open order balances, stock units
    anchor_offset: int  # x0: the anchor month less the evaluation month (0 without an anchor)
    est_price: Decimal  # per purchase unit
    unit_factor: Decimal = Decimal(1)  # stock units in one purchase unit (pack)
    budget_left: Decimal | None = None  # the least room left in the anchor request's budgets for the item's account
    last_paid: Decimal | None = None  # per stock unit, paid in the last 365 days


@dataclass
class Month:
    m: int  # months after the anchor month
    x: int  # months after the evaluation month
    expected: Decimal
    net_per_unit: Decimal
    need: Decimal
    qty: Decimal = ZERO  # stock units


@dataclass
class Result:
    recommended: bool
    reason: str  # '' when recommended, else not_below_trend, not_below_last_paid, no_trend, no_need, saving_too_small
    price_now: Decimal
    trend_now: Decimal | None = None
    slope: Decimal | None = None
    dip_pct: Decimal | None = None
    avg_use: Decimal | None = None
    cover: Decimal | None = None
    holding_per_month: Decimal | None = None
    months: list[Month] = field(default_factory=list)
    qty_base: Decimal = ZERO
    units: Decimal = ZERO  # purchase units
    value: Decimal = Decimal("0.00")
    gross_saving: Decimal = Decimal("0.00")
    holding_cost: Decimal = Decimal("0.00")
    net_saving: Decimal = Decimal("0.00")
    limit: str | None = None


def fit(points: list[tuple[int, Decimal]]) -> tuple[Decimal, Decimal]:
    """Least squares line through the points: (a, b), unrounded."""
    n = Decimal(len(points))
    xbar = sum((Decimal(x) for x, _ in points), ZERO) / n
    ybar = sum((y for _, y in points), ZERO) / n
    sxx = sum(((Decimal(x) - xbar) ** 2 for x, _ in points), ZERO)
    b = sum(((Decimal(x) - xbar) * (y - ybar) for x, y in points), ZERO) / sxx
    return ybar - b * xbar, b


def monthly_average(prices: list[Decimal]) -> Decimal:
    return q4(sum(prices, ZERO) / len(prices))


def average_use(issued_in_12_months: Decimal) -> Decimal:
    return q4(issued_in_12_months / 12)


def holding(price: Decimal, pct_month: Decimal) -> Decimal:
    return q4(price * pct_month / 100)


def _money_of(months: list[Month], price: Decimal, h: Decimal) -> tuple[Decimal, Decimal, Decimal]:
    gross = money(sum((c.qty * (c.expected - price) for c in months), ZERO))
    held = money(sum((c.qty * c.m * h for c in months), ZERO))
    net = money(sum((c.qty * c.net_per_unit for c in months), ZERO))
    return gross, held, net


def cut_to(months: list[Month], qty_base: Decimal) -> list[Month]:
    """The months with their quantities cut, latest first, so they add up to `qty_base`; months left empty go."""
    out = [replace(c) for c in months]
    over = sum((c.qty for c in out), ZERO) - qty_base
    for c in reversed(out):
        take = min(over, c.qty)
        c.qty -= take
        over -= take
    return [c for c in out if c.qty > 0]


def evaluate(rule: Rule, i: Inputs) -> Result:
    """The recommendation, or why there is none (the caller has checked the item may be bought forward at all)."""
    with localcontext() as ctx:
        ctx.prec = 28
        return _evaluate(rule, i)


def _evaluate(rule: Rule, i: Inputs) -> Result:
    p = i.price_now
    if len(i.points) < rule.min_trend_months:
        return Result(False, "no_trend", p)
    a, b = fit(i.points)
    trend = q4(a)
    dip = q4((trend - p) / trend * 100)
    out = Result(False, "", p, trend, q4(b), dip)
    if dip <= rule.min_gap_pct:
        out.reason = "not_below_trend"
        return out
    if i.last_paid is not None and p >= i.last_paid:
        out.reason = "not_below_last_paid"
        return out

    def need(offset: int) -> Decimal:
        return max(i.needs.get(offset, ZERO), i.avg_use)

    x0 = i.anchor_offset
    cover = max(ZERO, i.usable + i.supply - sum((need(k) for k in range(x0 + 1)), ZERO))
    h = holding(p, rule.holding_cost_pct_month)
    out.avg_use, out.cover, out.holding_per_month = i.avg_use, cover, h

    horizon, limit = rule.max_cover_months, "max_cover_months"
    if rule.shelf_life_months is not None and rule.shelf_life_months - 1 < horizon:
        horizon, limit = max(0, rule.shelf_life_months - 1), "shelf_life"
    months: list[Month] = []
    for m in range(1, horizon + 1):
        expected = q4(a + b * (x0 + m))
        net = expected - p - m * h
        if net <= 0:
            limit = "holding"
            continue
        months.append(Month(m, x0 + m, expected, net, need(x0 + m)))
    left = cover
    for c in months:
        used = min(left, c.need)
        c.qty, left = c.need - used, left - used
    total = sum((c.qty for c in months), ZERO)
    if limit == "max_cover_months" and cover > 0 and total < sum((c.need for c in months), ZERO):
        limit = "need"
    cap = total
    if rule.storage_max_qty is not None and max(ZERO, rule.storage_max_qty - cover) < cap:
        cap, limit = max(ZERO, rule.storage_max_qty - cover), "storage"
    if i.budget_left is not None:
        room = max(ZERO, i.budget_left) / i.est_price * i.unit_factor
        if room < cap:
            cap, limit = room, "budget"
    units = (cap / i.unit_factor).quantize(Decimal(1), rounding=ROUND_DOWN)
    qty_base = units * i.unit_factor
    months = cut_to(months, qty_base)
    gross, held, net = _money_of(months, p, h)
    out.months, out.qty_base, out.units = months, qty_base, units
    out.value = money(units * i.est_price)
    out.gross_saving, out.holding_cost, out.net_saving, out.limit = gross, held, net, limit
    if units <= 0:
        out.reason = "no_need"
    elif net <= rule.min_net_saving_inr:
        out.reason = "saving_too_small"
    else:
        out.recommended = True
    return out


def rework(result: Result, units: Decimal, unit_factor: Decimal, est_price: Decimal) -> Result:
    """The same recommendation for fewer purchase units (the buyer accepts less): cut from the latest months."""
    h = result.holding_per_month or ZERO
    months = cut_to(result.months, units * unit_factor)
    gross, held, net = _money_of(months, result.price_now, h)
    return replace(
        result, months=months, qty_base=units * unit_factor, units=units, value=money(units * est_price),
        gross_saving=gross, holding_cost=held, net_saving=net,
    )  # fmt: skip
