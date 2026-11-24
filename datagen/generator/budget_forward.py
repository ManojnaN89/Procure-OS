"""Budget and forward buying (block 3: tasks 3.7 and 13.5): the master rows they add, the scenario files the evals use,
and what each scenario must show, worked out here from the raw files on their own (never from the backend).

Master rows (written by company_profile.py):
- budget rows of the next financial year (`budgets.next_year`: a rise in percent, each cost centre's row rounded to the
  nearest `round_to` rupees, a location row the sum of its cost centres' rounded rows, else its own row rounded);
- master/budget_period_lock.csv: the months already closed (`budgets.locks`), each locked by the finance person on the
  `day` of the next month at `time` (the next working day when that is not one);
- master/forward_buy_rule.csv: the company row (the same values as the migration's version 1) and the item and category
  rows of `forward_buy.rules`.

Scenario files (outside master/, never loaded by a reset) and truth:
- forward_buy/<scenario>/benchmark.csv and rule.csv: the benchmark an eval enters and verifies and the rule versions it
  adds;
- truth/truth_budget.csv: every budget group a scenario checks (BG0, BG1 by step, YE1, FB6);
- truth/truth_forward_buy.csv: every forward-buy scenario, one row per month it buys for (one row when it buys none).

The rules, as the block 3 spec says them:
- used = held by requests (lines open or sourcing on requests submitted or approved, at qty x estimate) + held by orders
  (open order lines at (qty - short closed) x price less what is actual on the line) + spent (actuals of the financial
  year up to the month); the request checked counts in none of them;
- a stocked line is actual when accepted at receipt, at the order price; freight on an invoice is actual when paid;
- forward buying: trend = least squares over the monthly averages of verified delivered in-region benchmarks of the 12
  complete months before; price now = the latest counting benchmark (fresh, verified, delivered, in region); dip above the
  rule's minimum; months ahead of the anchor month up to the cover limit and below the shelf life, each kept only while
  its net saving per unit is above zero; the quantity is the need of those months less the cover, cut by storage and
  budget, rounded down to whole purchase units, the cut taken from the latest months.
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal, getcontext
from pathlib import Path
from typing import Any

from common import Calendar
from master_files import months, read, write_csv

getcontext().prec = 28
ZERO = Decimal(0)
CENT = Decimal("0.01")
FOUR = Decimal("0.0001")
LOCK = ["period", "locked_by", "locked_at", "reason"]
RULE = [
    "category_code", "item_code", "location_code", "version", "effective_from", "effective_to", "enabled", "min_gap_pct",
    "min_trend_months", "max_cover_months", "holding_cost_pct_month", "min_net_saving_inr", "shelf_life_months",
    "storage_max_qty", "note",
]  # fmt: skip
FIELDS = RULE[6:14]
# the company row of the forward-buy rule, version 1 (the migration seeds the same values)
COMPANY_RULE = {
    "enabled": "true", "min_gap_pct": "5.0000", "min_trend_months": "6", "max_cover_months": "3",
    "holding_cost_pct_month": "1.5000", "min_net_saving_inr": "10000.00", "shelf_life_months": "", "storage_max_qty": "",
}  # fmt: skip
COMPANY_NOTE = "Recommended default, needs validation (Q27)"
BENCHMARK = [
    "item_code", "price", "uom", "basis", "effective_date", "region_code", "source_kind", "source_name", "source_ref",
    "entered_by", "verified_by",
]  # fmt: skip
TRUTH_BUDGET = [
    "scenario", "step", "as_of", "request_of", "gl_code", "level", "holder", "budget", "held_by_requests",
    "held_by_orders", "spent", "committed", "requested", "used_pct", "status",
]  # fmt: skip
TRUTH_FB = [
    "scenario", "company", "item_code", "location_code", "as_of", "price_now", "trend_now", "slope", "dip_pct", "avg_use",
    "cover", "month", "expected_price", "net_per_unit", "month_qty", "qty", "value", "gross_saving", "holding_cost",
    "net_saving", "recommended", "reason", "limit", "rule",
]  # fmt: skip


def _q4(v: Decimal) -> Decimal:
    return v.quantize(FOUR, rounding=ROUND_HALF_UP)


def _m2(v: Decimal) -> Decimal:
    return v.quantize(CENT, rounding=ROUND_HALF_UP)


def _month(d: date) -> str:
    return f"{d.year}-{d.month:02d}"


def _shift(month: str, n: int) -> str:
    y, m = int(month[:4]), int(month[5:]) - 1 + n
    return f"{y + m // 12}-{m % 12 + 1:02d}"


def _diff(a: str, b: str) -> int:
    """Months from b to a."""
    return (int(a[:4]) - int(b[:4])) * 12 + int(a[5:]) - int(b[5:])


def _on(d: dict[Any, Any], default: date | None = None) -> date:
    """A step's date (YAML 1.1 reads a bare `on` key as true)."""
    raw = d.get("on", d.get(True))
    if raw is None:
        assert default is not None
        return default
    return date.fromisoformat(str(raw))


def fy_start(d: date) -> str:
    return f"{d.year if d.month >= 4 else d.year - 1}-04"


# ---- master rows -------------------------------------------------------------------------------------------


def budget_rows(profile: Any) -> list[dict[str, object]]:
    """Every budget row: the plan's months as given, the next year's months risen and rounded."""
    b = profile["budgets"]
    nxt = b.get("next_year") or {}
    start = nxt.get("from", "9999-12")
    rise = Decimal(str(nxt.get("rise_pct", "0")))
    step = Decimal(str(nxt.get("round_to", "1")))
    cc_location = {c[0]: profile["location"]["code"] for c in profile["cost_centres"]}

    def risen(amount: str) -> Decimal:
        value = Decimal(amount) * (100 + rise) / 100
        return ((value / step).quantize(Decimal(1), rounding=ROUND_HALF_UP) * step).quantize(CENT)

    rows: list[dict[str, object]] = []

    def put(cc: str, loc: str, gl: str, period: str, amount: Decimal | str) -> None:
        rows.append({
            "cost_centre_code": cc, "gl_code": gl, "project_code": "", "period": period,
            "amount": f"{Decimal(amount):.2f}", "version": "1", "policy": b["policy"], "location_code": loc,
        })  # fmt: skip

    for period in months(*b["periods"]):
        later = period >= start
        for cc, plan in b["per_month"].items():
            for gl, amount in plan["gl"].items():
                put(cc, "", gl, period, risen(amount) if later else amount)
        for loc, plan in b["per_location"].items():
            for gl, amount in plan["gl"].items():
                if not later:
                    put("", loc, gl, period, amount)
                    continue
                own = [
                    risen(p["gl"][gl]) for cc, p in b["per_month"].items() if cc_location[cc] == loc and gl in p["gl"]
                ]
                put("", loc, gl, period, sum(own, ZERO) if own else risen(amount))
    return rows


def lock_rows(profile: Any) -> list[dict[str, object]]:
    """The months closed before the demo starts, oldest first."""
    locks = profile["budgets"].get("locks")
    if not locks:
        return []
    cal, loc = Calendar(profile.places), profile["location"]["code"]
    hh, mm = (int(x) for x in str(locks.get("time", "18:00")).split(":"))
    out: list[dict[str, object]] = []
    for period in months(profile["budgets"]["periods"][0], locks["through"]):
        nxt = _shift(period, 1)
        day = cal.next_working(date(int(nxt[:4]), int(nxt[5:]), int(locks.get("day", 10))), loc)
        at = datetime(day.year, day.month, day.day, hh, mm)
        out.append({"period": period, "locked_by": locks["by"], "locked_at": f"{at.isoformat()}+05:30",
                    "reason": locks["reason"]})  # fmt: skip
    return out


def _rule_row(r: dict[str, Any], version: int, start: str, note: str) -> dict[str, object]:
    row: dict[str, object] = {
        "category_code": r.get("category") or "", "item_code": r.get("item") or "",
        "location_code": r.get("location") or "", "version": str(r.get("version", version)),
        "effective_from": str(r.get("effective_from", start)), "effective_to": "", "note": r.get("note") or note,
    }  # fmt: skip
    for f in FIELDS:
        v = r.get(f)
        row[f] = "" if v is None else (str(v).lower() if isinstance(v, bool) else str(v))
    return row


def forward_rule_rows(profile: Any) -> list[dict[str, object]]:
    """The company row (version 1, as the migration seeds it) and the profile's own rows."""
    if "forward_buy" not in profile.data:
        return []
    company = {"category_code": "", "item_code": "", "location_code": "", "version": "1",
               "effective_from": "2000-01-01", "effective_to": "", **COMPANY_RULE, "note": COMPANY_NOTE}  # fmt: skip
    own = [_rule_row(r, 1, "2025-04-01", "") for r in profile["forward_buy"].get("rules") or []]
    return [company, *own]


# ---- reading the dataset -----------------------------------------------------------------------------------


@dataclass
class Line:
    order: str
    item: str
    qty: Decimal
    price: Decimal
    gl: str
    need_by: date


class Dataset:
    """What the scenarios read from the files already written."""

    def __init__(self, out: Path) -> None:
        m = out / "master"
        self.out = out
        self.items = {r["code"]: r for r in read(m / "item.csv")}
        self.categories = {r["code"]: r for r in read(m / "category.csv")}
        self.locations = {r["code"]: r for r in read(m / "location.csv")}
        self.budget = read(m / "budget.csv")
        self.cost_centres = read(m / "cost_centre.csv")
        self.benchmarks = read(m / "market_benchmark.csv") if (m / "market_benchmark.csv").exists() else []
        self.rules = read(m / "forward_buy_rule.csv") if (m / "forward_buy_rule.csv").exists() else []
        self.contracts = {r["code"]: r for r in read(m / "contract.csv")}
        self.prices = read(m / "price_record.csv")
        self.conversions = {(r["item_code"], r["from_uom"], r["to_uom"]): Decimal(r["factor"])
                            for r in read(m / "uom_conversion.csv")}  # fmt: skip
        self.movements = read(out / "history" / "stock_movement.csv")
        t = out / "truth"
        self.positions = read(t / "truth_stock_position.csv") if (t / "truth_stock_position.csv").exists() else []
        self.requests = read(t / "truth_stock_requests.csv") if (t / "truth_stock_requests.csv").exists() else []
        self.needs: dict[str, dict[str, date]] = {}  # order -> item -> need-by
        self.requirements: list[tuple[str, str, Decimal, date]] = []  # item, location, qty, need-by
        inputs = out / "inputs"
        orders = {r["order_no"]: r for r in read(inputs / "production_order.csv")} if (inputs / "production_order.csv").exists() else {}
        if (inputs / "production_order_line.csv").exists():
            for r in read(inputs / "production_order_line.csv"):
                need = date.fromisoformat(r["need_by"])
                self.needs.setdefault(r["order_no"], {})[r["item_code"]] = need
                self.requirements.append((r["item_code"], orders[r["order_no"]]["location_code"], Decimal(r["qty"]), need))
        if (inputs / "mrp_result.csv").exists():
            for r in read(inputs / "mrp_result.csv"):
                need = date.fromisoformat(r["need_by"])
                self.needs.setdefault(f"{r['plan_no']}/{r['location_code']}", {})[r["item_code"]] = need
                self.requirements.append((r["item_code"], r["location_code"], Decimal(r["qty"]), need))

    def gl(self, item: str) -> str:
        it = self.items[item]
        return it["default_gl"] or self.categories[it["category_code"]]["default_gl"]

    def contract_price(self, item: str, location: str, on: date) -> Decimal | None:
        region = self.locations[location]["region_code"]
        for p in self.prices:
            c = self.contracts.get(p["contract_code"] or "")
            if p["item_code"] != item or c is None or p["status"] != "active" or c["status"] != "active":
                continue
            if not (p["valid_from"] <= on.isoformat() <= (p["valid_to"] or "9999") and
                    c["valid_from"] <= on.isoformat() <= (c["valid_to"] or "9999")):  # fmt: skip
                continue
            if (p["location_code"] and p["location_code"] != location) or (p["region_code"] and p["region_code"] != region):
                continue
            return Decimal(p["price"])
        return None

    def estimate(self, item: str, location: str, on: date) -> Decimal:
        found = self.contract_price(item, location, on)
        return found if found is not None else Decimal(self.items[item]["standard_price"])

    def lines(self, scenario: str, step: str, location: str, on: date) -> list[Line]:
        """The request lines a stock scenario's step left (their orders, items, quantities, estimates and accounts)."""
        out = []
        for r in self.requests:
            if r["scenario"] != scenario or r["step"] != step:
                continue
            for part in r["lines"].split(";"):
                item, qty = part.split(":")
                need = self.needs.get(r["order_no"], {}).get(item, on)
                out.append(Line(r["order_no"], item, Decimal(qty), self.estimate(item, location, on), self.gl(item), need))
        return out

    def usable(self, scenario: str, step: str, item: str, location: str) -> Decimal:
        for r in self.positions:
            if (r["scenario"], r["step"], r["item_code"], r["location_code"]) == (scenario, step, item, location):
                return Decimal(r["usable"])
        return ZERO

    def budget_of(self, gl: str, holders: list[str], location: str | None, a: str, b: str) -> Decimal | None:
        rows = [
            r for r in self.budget
            if r["gl_code"] == gl and a <= r["period"] <= b
            and ((location is None and r["cost_centre_code"] in holders) or
                 (location is not None and not r["cost_centre_code"] and r["location_code"] == location))
        ]  # fmt: skip
        return sum((Decimal(r["amount"]) for r in rows), ZERO) if rows else None

    def department_scope(self, department: str, location: str) -> list[str]:
        own = [c["code"] for c in self.cost_centres if c["department_code"] == department and c["location_code"] == location]
        return sorted(own or [c["code"] for c in self.cost_centres if c["location_code"] == location])


# ---- budget scenarios ---------------------------------------------------------------------------------------


@dataclass
class OrderLine:
    order: str
    item: str
    qty: Decimal
    price: Decimal
    gl: str
    actual: Decimal = ZERO
    short: Decimal = ZERO
    open: bool = True

    @property
    def held(self) -> Decimal:
        if not self.open:
            return ZERO
        # block 13 (R2.7): a short-closed balance stays committed until the order closes
        return max(ZERO, _m2(self.qty * self.price) - self.actual)


class Budgets:
    """One company's requests, orders and actuals as a budget scenario's steps move them."""

    def __init__(self, ds: Dataset, profile: Any, lines: list[Line], department: str, location: str) -> None:
        self.ds, self.p = ds, profile
        self.department, self.location = department, location
        self.lines = list(lines)  # live request lines (not ordered)
        self.orders: list[OrderLine] = []
        self.spent: list[tuple[str, str, Decimal]] = []  # gl, period, amount

    def order(self, of: str, prices: dict[str, str]) -> None:
        for item, price in prices.items():
            ln = next(x for x in self.lines if x.order == of and x.item == item)
            self.lines.remove(ln)
            self.orders.append(OrderLine(of, item, ln.qty, Decimal(price), ln.gl))

    def _line(self, of: str, item: str) -> OrderLine:
        return next(x for x in self.orders if x.order == of and x.item == item)

    def accept(self, of: str, item: str, qty: str, on: date, rejected: str = "0") -> None:
        ln = self._line(of, item)
        amount = _m2(Decimal(qty) * ln.price)
        ln.actual += amount
        self.spent.append((ln.gl, _month(on), amount))
        del rejected  # a rejected quantity is never actual

    def close_balance(self, of: str, item: str, qty: str) -> None:
        self._line(of, item).short += Decimal(qty)

    def pay_freight(self, of: str, item: str, amount: str, on: date) -> None:
        self.spent.append((self._line(of, item).gl, _month(on), Decimal(amount)))

    def check(self, request_of: str, on: date, fy_from: str | None = None) -> list[dict[str, str]]:
        """The groups of a request's check: its department's cost centres at the location, then the location."""
        mine = [x for x in self.lines if x.order == request_of]
        if not mine:
            mine = [Line(o.order, o.item, o.qty, o.price, o.gl, on) for o in self.orders if o.order == request_of]
        first, last = fy_from or fy_start(on), _month(on)
        scope = self.ds.department_scope(self.department, self.location)
        out = []
        for gl in sorted({x.gl for x in mine}):
            requested = sum((_m2(x.qty * x.price) for x in mine if x.gl == gl), ZERO)
            by_requests = sum((_m2(x.qty * x.price) for x in self.lines if x.gl == gl and x.order != request_of), ZERO)
            by_orders = sum((o.held for o in self.orders if o.gl == gl and o.order != request_of), ZERO)
            spent = sum((a for g, p, a in self.spent if g == gl and first <= p <= last), ZERO)
            for level, holder, budget in (
                ("department", f"department {self.department} at {self.location}", self.ds.budget_of(gl, scope, None, first, last)),
                ("location", f"location {self.location}", self.ds.budget_of(gl, [], self.location, first, last)),
            ):  # fmt: skip
                if budget is None:
                    continue
                committed = by_requests + by_orders + spent
                used = _m2((committed + requested) * 100 / budget)
                status = "over" if requested > budget - committed else "warn" if used >= 90 else "ok"
                out.append({
                    "request_of": request_of, "gl_code": gl, "level": level, "holder": holder,
                    "budget": f"{budget:.2f}", "held_by_requests": f"{by_requests:.2f}", "held_by_orders": f"{by_orders:.2f}",
                    "spent": f"{spent:.2f}", "committed": f"{committed:.2f}", "requested": f"{requested:.2f}",
                    "used_pct": f"{used:.2f}", "status": status,
                })  # fmt: skip
        return out


def budget_truth(profile: Any, ds: Dataset) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    base = profile["budget_scenarios"]
    location, department = base["location"], base["department"]
    for sc in base["scenarios"]:
        on = _on(sc)
        world = Budgets(ds, profile, ds.lines(base["stock_base"], "base", location, on), department, location)
        for step in sc["steps"]:
            on = _on(step, on)
            for of, prices in (step.get("order") or {}).items():
                world.order(of, prices)
            for of, items in (step.get("accept") or {}).items():
                for item, qty in items.items():
                    world.accept(of, item, str(qty), on)
            for of, items in (step.get("close_balance") or {}).items():
                for item, qty in items.items():
                    world.close_balance(of, item, str(qty))
            for of, items in (step.get("pay_freight") or {}).items():
                for item, amount in items.items():
                    world.pay_freight(of, item, str(amount), on)
            for extra in step.get("add_request") or []:
                world.lines.append(Line(extra["order"], extra["item"], Decimal(str(extra["qty"])),
                                        Decimal(str(extra["price"])), ds.gl(extra["item"]), on))  # fmt: skip
            for request_of in step.get("check") or []:
                for g in world.check(request_of, on, step.get("fy_from")):
                    rows.append({"scenario": sc["name"], "step": step["label"], "as_of": on.isoformat(), **g})
    return rows


# ---- forward buying -----------------------------------------------------------------------------------------


@dataclass
class Rule:
    label: str
    values: dict[str, Any]


def rule_in_force(rows: list[dict[str, str]], item: dict[str, str], location: str, on: date) -> Rule:
    """The company row with each narrower row in force laid over it (category, item, item at the location)."""

    def best(cat: str, it: str, loc: str) -> dict[str, str] | None:
        found = [r for r in rows if (r["category_code"], r["item_code"], r["location_code"]) == (cat, it, loc)
                 and r["effective_from"] <= on.isoformat() and (not r["effective_to"] or r["effective_to"] >= on.isoformat())]  # fmt: skip
        return max(found, key=lambda r: int(r["version"])) if found else None

    scopes = [("", "", "", "forward_buy"), (item["category_code"], "", "", item["category_code"]),
              ("", item["code"], "", item["code"]), ("", item["code"], location, f"{item['code']}@{location}")]  # fmt: skip
    values: dict[str, Any] = {}
    label: list[str] = []
    for cat, it, loc, name in scopes:
        r = best(cat, it, loc)
        if r is None:
            continue
        label.append(f"{name} v{r['version']}")
        for f in FIELDS:
            if r[f] != "":
                values[f] = r[f]
    return Rule(" + ".join(label), values)


@dataclass
class State:
    """Where an item stands at a location when it is evaluated."""

    on: date
    usable: Decimal
    supply: Decimal  # live request lines and open order balances, in the stock unit
    anchor: Line | None
    budget_left: Decimal | None  # the least room left in the anchor request's groups for the item's account


def evaluate(ds: Dataset, item_code: str, location: str, st: State, rules: list[dict[str, str]],
             extra: list[dict[str, str]]) -> dict[str, Any]:  # fmt: skip
    """The forward-buy engine, worked out from the files (one result; months when it buys)."""
    item = ds.items[item_code]
    rule = rule_in_force(rules, item, location, st.on)
    v = rule.values
    out: dict[str, Any] = {"rule": rule.label, "months": [], "recommended": False}
    if item["stocked"] != "true" or item["capex"] == "true" or str(v.get("enabled", "true")).lower() != "true":
        return {**out, "reason": "not_eligible"}
    if ds.contract_price(item_code, location, st.on) is not None:
        return {**out, "reason": "on_contract"}
    base = item["base_uom"]
    region = ds.locations[location]["region_code"]

    def in_base(b: dict[str, str]) -> Decimal | None:
        if b["uom"] == base:
            return Decimal(b["price"])
        f = ds.conversions.get((item_code, base, b["uom"]))
        if f is None and (back := ds.conversions.get((item_code, b["uom"], base))):
            f = 1 / back
        return None if f is None else _q4(Decimal(b["price"]) * f)

    usable = [b for b in ds.benchmarks + extra if b["item_code"] == item_code and b["status"] == "verified"
              and b["basis"] == "delivered" and b["region_code"] in ("", region) and in_base(b) is not None]  # fmt: skip
    now = _month(st.on)
    fresh = [b for b in usable if 0 <= (st.on - date.fromisoformat(b["effective_date"])).days <= 45]
    if not fresh:
        return {**out, "reason": "no_fresh_benchmark"}
    latest = max(fresh, key=lambda b: (b["effective_date"], in_base(b)))
    price = in_base(latest)
    assert price is not None
    points: list[tuple[int, Decimal]] = []
    for k in range(-12, 0):
        month = _shift(now, k)
        got = [in_base(b) for b in usable if b["effective_date"][:7] == month]
        prices = [p for p in got if p is not None]
        if prices:
            points.append((k, _q4(sum(prices, ZERO) / len(prices))))
    if len(points) < int(v["min_trend_months"]):
        return {**out, "reason": "no_trend", "price_now": price}
    n = Decimal(len(points))
    xbar = sum((Decimal(x) for x, _ in points), ZERO) / n
    ybar = sum((y for _, y in points), ZERO) / n
    slope = sum(((x - xbar) * (y - ybar) for x, y in points), ZERO) / sum(((x - xbar) ** 2 for x, _ in points), ZERO)
    a = ybar - slope * xbar
    trend = _q4(a)
    dip = _q4((trend - price) / trend * 100)
    out.update(price_now=price, trend_now=trend, slope=_q4(slope), dip_pct=dip)
    if dip <= Decimal(v["min_gap_pct"]):
        return {**out, "reason": "not_below_trend"}
    first, last = _shift(now, -12), _shift(now, -1)
    issued = sum((Decimal(m["qty"]) for m in ds.movements if m["item_code"] == item_code and m["location_code"] == location
                  and m["type"] == "issue" and first <= m["date"][:7] <= last), ZERO)  # fmt: skip
    avg = _q4(issued / 12)
    m0 = _month(st.anchor.need_by) if st.anchor else now

    def need(month: str) -> Decimal:
        r = sum((q for i, loc, q, d in ds.requirements if i == item_code and loc == location and _month(d) == month), ZERO)
        return max(r, avg)

    through = sum((need(_shift(now, k)) for k in range(_diff(m0, now) + 1)), ZERO)
    cover = max(ZERO, st.usable + st.supply - through)
    out.update(avg_use=avg, cover=cover)
    hold = _q4(price * Decimal(v["holding_cost_pct_month"]) / 100)
    horizon = int(v["max_cover_months"])
    limit = "max_cover_months"
    if v.get("shelf_life_months"):
        if int(v["shelf_life_months"]) - 1 < horizon:
            horizon, limit = max(0, int(v["shelf_life_months"]) - 1), "shelf_life"
    x0 = _diff(m0, now)
    chosen: list[dict[str, Any]] = []
    for m in range(1, horizon + 1):
        expected = _q4(a + slope * (x0 + m))
        net = expected - price - m * hold
        if net <= 0:
            limit = "holding"
            continue
        chosen.append({"month": _shift(m0, m), "m": m, "expected": expected, "net": net, "need": need(_shift(m0, m))})
    left = cover
    for c in chosen:
        used = min(left, c["need"])
        c["qty"], left = c["need"] - used, left - used
    total = sum((c["qty"] for c in chosen), ZERO)
    if limit == "max_cover_months" and cover > 0 and total < sum((c["need"] for c in chosen), ZERO):
        limit = "need"
    cap = total
    if v.get("storage_max_qty"):
        room = max(ZERO, Decimal(v["storage_max_qty"]) - cover)
        if room < cap:
            cap, limit = room, "storage"
    est = st.anchor.price if st.anchor else ds.estimate(item_code, location, st.on)
    if st.budget_left is not None:
        room = max(ZERO, st.budget_left / est)
        if room < cap:
            cap, limit = room, "budget"
    pack = Decimal(item["pack_size"] or 1)
    unit = item["purchase_uom"] or base
    factor = Decimal(1) if unit == base else ds.conversions.get((item_code, unit, base), pack)
    units = (cap / factor).quantize(Decimal(1), rounding=ROUND_DOWN)
    qty = units * factor
    cut = total - qty
    for c in reversed(chosen):
        take = min(cut, c["qty"])
        c["qty"] -= take
        cut -= take
    chosen = [c for c in chosen if c["qty"] > 0]
    gross = _m2(sum((c["qty"] * (c["expected"] - price) for c in chosen), ZERO))
    holding = _m2(sum((c["qty"] * c["m"] * hold for c in chosen), ZERO))
    net = _m2(sum((c["qty"] * c["net"] for c in chosen), ZERO))
    value = _m2(units * est)
    out.update(months=chosen, qty=qty, value=value, gross=gross, holding=holding, net=net, limit=limit, est=est)
    if qty <= 0:
        return {**out, "reason": "no_need"}
    if net <= Decimal(v["min_net_saving_inr"]):
        return {**out, "reason": "saving_too_small"}
    return {**out, "reason": "", "recommended": True}


def _truth_rows(name: str, company: str, item: str, location: str, on: date, r: dict[str, Any]) -> list[dict[str, str]]:
    def s(key: str, places: str = "4") -> str:
        v = r.get(key)
        return "" if v is None else f"{Decimal(v):.{places}f}"

    head = {
        "scenario": name, "company": company, "item_code": item, "location_code": location, "as_of": on.isoformat(),
        "price_now": s("price_now"), "trend_now": s("trend_now"), "slope": s("slope"), "dip_pct": s("dip_pct"),
        "avg_use": s("avg_use"), "cover": s("cover"), "qty": s("qty"), "value": s("value", "2"),
        "gross_saving": s("gross", "2"), "holding_cost": s("holding", "2"), "net_saving": s("net", "2"),
        "recommended": str(r["recommended"]).lower(), "reason": r["reason"], "limit": r.get("limit", ""), "rule": r["rule"],
    }  # fmt: skip
    months_ = r["months"] or [None]
    return [
        {**head, "month": c["month"] if c else "", "expected_price": f"{c['expected']:.4f}" if c else "",
         "net_per_unit": f"{_q4(c['net']):.4f}" if c else "", "month_qty": f"{c['qty']:.4f}" if c else ""}  # fmt: skip
        for c in months_
    ]


def forward_truth(profile: Any, ds: Dataset, out: Path) -> list[dict[str, str]]:
    """Each scenario's files and its result. The state is the stock scenario's base step (or the reset)."""
    fb = profile["forward_buy"]
    rows: list[dict[str, str]] = []
    company = profile["tenant"]
    who = fb.get("entered_by", "U03"), fb.get("verified_by", "U08")
    for sc in fb.get("scenarios") or []:
        on = _on(sc)
        item, location = sc["item"], sc["location"]
        folder = out / "forward_buy" / sc["name"]
        extra: list[dict[str, str]] = []
        if sc.get("benchmark"):
            b = sc["benchmark"]
            row = {"item_code": item, "price": f"{Decimal(str(b['price'])):.4f}", "uom": b["uom"],
                   "basis": b.get("basis", "delivered"), "effective_date": str(b["effective_date"]),
                   "region_code": b.get("region", ""), "source_kind": b["source_kind"], "source_name": b["source_name"],
                   "source_ref": b.get("source_ref", ""), "entered_by": who[0], "verified_by": who[1]}  # fmt: skip
            write_csv(folder / "benchmark.csv", BENCHMARK, [row])
            extra.append({**row, "status": "verified"})
        added = [_rule_row(r, int(r["version"]), _on(sc).isoformat(), r.get("note", f"Scenario {sc['name']}"))
                 for r in sc.get("rules") or []]  # fmt: skip
        if added:
            write_csv(folder / "rule.csv", RULE, list(added))
        rules = list(ds.rules) + [{k: str(v) for k, v in r.items()} for r in added]  # the highest version in force wins
        stock = sc.get("stock", {"scenario": fb["stock_base"], "step": "base"})
        lines = [ln for ln in ds.lines(stock["scenario"], stock["step"], location, on)] if stock.get("requests", True) else []
        mine = [ln for ln in lines if ln.item == item]
        anchor = None if sc.get("preview") or not mine else max(mine, key=lambda ln: (ln.need_by, ln.order))
        left = None
        if anchor is not None:
            world = Budgets(ds, profile, lines, profile["budget_scenarios"]["department"], location)
            groups = [g for g in world.check(anchor.order, on) if g["gl_code"] == anchor.gl]
            if groups:
                left = min(Decimal(g["budget"]) - Decimal(g["committed"]) - Decimal(g["requested"]) for g in groups)
        st = State(on, ds.usable(stock["scenario"], stock["step"], item, location),
                   sum((ln.qty for ln in mine), ZERO), anchor, left)  # fmt: skip
        result = evaluate(ds, item, location, st, rules, extra)
        rows += _truth_rows(sc["name"], company, item, location, on, result)
    return rows


def write_block3(profile: Any, out: Path) -> dict[str, int]:
    """The scenario files and both truth files. Reads the dataset already written (stock truth included)."""
    ds = Dataset(out)
    budget = budget_truth(profile, ds) if profile.data.get("budget_scenarios") else []
    forward = forward_truth(profile, ds, out) if (profile.data.get("forward_buy") or {}).get("scenarios") else []
    write_csv(out / "truth" / "truth_budget.csv", TRUTH_BUDGET, list(budget))
    write_csv(out / "truth" / "truth_forward_buy.csv", TRUTH_FB, list(forward))
    return {"truth_budget": len(budget), "truth_forward_buy": len(forward)}


def block3_problems(out: Path, profile: Any | None) -> list[str]:
    """Locks in order and before the cutoff month, next year's budget there, enough verified benchmarks for every
    forward-buy scenario's item."""
    problems: list[str] = []
    m = out / "master"
    locks = read(m / "budget_period_lock.csv") if (m / "budget_period_lock.csv").exists() else []
    periods = [r["period"] for r in locks]
    if periods != sorted(periods) or len(set(periods)) != len(periods):
        problems.append("budget_period_lock: periods are not in order or repeat")
    for r in locks:
        if r["locked_at"][:7] <= r["period"]:
            problems.append(f"budget_period_lock {r['period']}: locked before the month ended")
        if r["period"] >= "2026-11":
            problems.append(f"budget_period_lock {r['period']}: the demo month cannot be locked already")
    if profile is not None and (profile["budgets"].get("next_year") or {}).get("from"):
        start = profile["budgets"]["next_year"]["from"]
        nxt = [r for r in read(m / "budget.csv") if r["period"] >= start]
        if not nxt:
            problems.append(f"budget: no rows from {start}")
    if profile is not None and (profile.data.get("forward_buy") or {}).get("scenarios"):
        bench = read(m / "market_benchmark.csv")
        for sc in profile["forward_buy"]["scenarios"]:
            got = {b["effective_date"][:7] for b in bench if b["item_code"] == sc["item"] and b["status"] == "verified"}
            if len(got) < 6:
                problems.append(f"forward buy {sc['name']}: {sc['item']} has {len(got)} verified monthly benchmarks, not 6")
    return problems
