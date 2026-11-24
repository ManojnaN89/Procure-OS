"""Shared pieces of the block 3 evals (E85-E93): the budget engine (commitments by the document in force, stock budget at
receipt D-10, period locks and year end D-09, adjustments) and the forward-buy recommendation (task 13.5). Written from
the block 3 spec, the plan and the dataset only, never from the implementation.

Two halves, as in `block1` and `block2`:

- **The oracles**, plain code over the raw dataset files:
  - `Budgets` reads `master/budget.csv`, `cost_centre.csv` and `budget_period_lock.csv`: the budget of a GL account for a
    holder (a department's cost centres at a location, or a location) from April to the check month, the financial year
    and its months, which months are locked. The request values come from block 2's own store-check oracle
    (`block2.Stock`): requested quantity x the estimate (contract, else standard; the same number on both datasets).
  - `evaluate` is the forward-buy engine of spec 5.2 in `Decimal`: trend points from `master/market_benchmark.csv` plus
    the scenario's own benchmark, a least-squares line, the dip, the average use from `history/stock_movement.csv`, the
    cover from the replayed stock and the open supply of A0, the months ahead, the limits, the money. `rule_for` merges
    the rule rows (company v1 of spec 5.3, `master/forward_buy_rule.csv`, the scenario's `rule.csv`). `Wrong` switches a
    rule to a deliberately wrong version (the mutation checks).
- **The platform** (`Co3`, built on `block2.Co`): a demo company made by `demo reset`, its people signed in with their own
  logins, the API the spec names (`/api/budgets/...`, `/api/forward-buys/...`), the CLI the spec names run in this
  process, and read-only views of the tables the spec names where the API is silent.

Where the spec leaves a shape open (a list wrapped in an object or not, a number as text or a number, a person as id,
code or name, a path the spec does not name), the readers accept every reasonable shape and say so in a comment.
"""

import contextlib
import re
import shutil
import tempfile
from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal, localcontext
from pathlib import Path
from typing import Any

import block1 as b
import block2 as s2
import lib
from block1 import D, Z, dec, js

CKN, SND, BLR = "PLT-CKN", "PLT-SND", "OFF-BLR"
MRP = b.MRP_CKN  # "MRP-2027-01/PLT-CKN"
AS_OF = date(2026, 11, 23)  # every block 3 scenario starts on this Monday (spec 3.3, 3.4)
RECEIPT_DAY = date(2026, 11, 25)  # BG1's receipts
LATE_INVOICE_DAY = date(2026, 11, 28)  # PL1's non-stock invoice
LOCK_DAY = date(2026, 12, 7)  # PL1
YEAR_END_DAY = date(2027, 4, 5)  # YE1
Q4 = Decimal("0.0001")
CENT = Decimal("0.01")
HUNDRED = Decimal(100)


# ---- numbers and months --------------------------------------------------------------------------------------------


def r4(x: Decimal) -> Decimal:
    return Decimal(x).quantize(Q4, rounding=ROUND_HALF_UP)


def r2(x: Decimal) -> Decimal:
    return Decimal(x).quantize(CENT, rounding=ROUND_HALF_UP)


def month(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def shift(period: str, n: int) -> str:
    y, m = int(period[:4]), int(period[5:7])
    k = y * 12 + (m - 1) + n
    return f"{k // 12:04d}-{k % 12 + 1:02d}"


def months_between(a: str, b_: str) -> int:
    """b_ minus a, in months."""
    return (int(b_[:4]) * 12 + int(b_[5:7])) - (int(a[:4]) * 12 + int(a[5:7]))


def fy_of(period: str) -> str:
    """2027-03 -> 2026-27; 2027-04 -> 2027-28 (April to March)."""
    y, m = int(period[:4]), int(period[5:7])
    start = y if m >= 4 else y - 1
    return f"{start}-{str(start + 1)[2:]}"


def fy_periods(fy: str) -> list[str]:
    start = int(fy[:4])
    return [shift(f"{start}-04", i) for i in range(12)]


def pct_used(committed: Decimal, requested: Decimal, budget: Decimal | None) -> Decimal | None:
    if not budget:
        return None
    return r2((committed + requested) * HUNDRED / budget)


def status_of(budget: Decimal | None, committed: Decimal, requested: Decimal, warn: Decimal, block: Decimal) -> str:
    """The test of `formulas/budget.check_budget`, unchanged by block 3 (spec 4.4), as the D36 decision states it."""
    if budget is None:
        return "unchecked"
    if requested > max(budget * block / HUNDRED - committed, Z):
        return "over"
    if requested > 0 and (committed + requested) * HUNDRED >= budget * warn:
        return "warn"
    return "ok"


# ======================================================================================================================
# The budget oracle
# ======================================================================================================================


@dataclass
class Group:
    """One group of a budget check as the spec's GroupCheck reads it (spec 4.5): money as Decimal."""

    gl_code: str
    level: str  # department, cost_centre, cost_centres or location
    budget: Decimal | None
    held_by_requests: Decimal = Z
    held_by_orders: Decimal = Z
    spent: Decimal = Z
    requested: Decimal = Z
    status: str = ""
    used_pct: Decimal | None = None

    @property
    def committed(self) -> Decimal:
        return self.held_by_requests + self.held_by_orders + self.spent

    def settle(self, warn: Decimal, block: Decimal) -> "Group":
        self.used_pct = pct_used(self.committed, self.requested, self.budget)
        self.status = status_of(self.budget, self.committed, self.requested, warn, block)
        return self

    def as_dict(self) -> dict[str, Any]:
        return {
            "budget": self.budget,
            "held_by_requests": self.held_by_requests,
            "held_by_orders": self.held_by_orders,
            "spent": self.spent,
            "committed": self.committed,
            "requested": self.requested,
            "used_pct": self.used_pct,
            "status": self.status,
        }


class Budgets:
    """The budget files of a dataset, read without the platform."""

    def __init__(self, dataset: Path) -> None:
        self.rows = b.read_csv(dataset / "master" / "budget.csv")
        self.cost_centres = {r["code"]: r for r in b.read_csv(dataset / "master" / "cost_centre.csv")}
        self.locks = b.read_csv(dataset / "master" / "budget_period_lock.csv")
        settings = {r["key"]: r["value"] for r in b.read_csv(dataset / "master" / "setting.csv")}
        self.warn = D(settings.get("budget_warn_pct") or "90")
        self.block = D(settings.get("budget_block_pct") or "100")
        latest: dict[tuple[str, str, str, str], tuple[int, Decimal]] = {}
        for r in self.rows:  # the latest version of each (cost centre, GL, location, month)
            k = (r.get("cost_centre_code", ""), r["gl_code"], r.get("location_code", ""), r["period"])
            v = int(r.get("version") or 1)
            if k not in latest or v >= latest[k][0]:
                latest[k] = (v, D(r["amount"]))
        self.amounts = {k: v[1] for k, v in latest.items()}
        self.extra: dict[tuple[str, str, str, str], Decimal] = defaultdict(Decimal)  # adjustments the eval made

    def scope(self, department: str, location: str) -> list[str]:
        """A department's cost centres at a location (the `department` level of the existing check)."""
        return sorted(
            c for c, r in self.cost_centres.items() if r["department_code"] == department and r["location_code"] == location
        )

    def total(
        self, gl: str, upto: str, *, cost_centres: Iterable[str] = (), location: str | None = None
    ) -> Decimal | None:
        """The budget from April of `upto`'s financial year to `upto` (spec 4.1: the FY of the check)."""
        months = [p for p in fy_periods(fy_of(upto)) if p <= upto]
        ccs = set(cost_centres)
        found = False
        total = Z
        for (cc, g, loc, p), amount in list(self.amounts.items()) + list(self.extra.items()):
            if g != gl or p not in months:
                continue
            if ccs and cc in ccs or not ccs and location and not cc and loc == location:
                total += amount
                found = True
        return total if found else None

    def locked(self) -> list[str]:
        return sorted(r["period"] for r in self.locks)

    def group(self, gl: str, level: str, upto: str, holder: Any, **parts: Decimal) -> Group:
        if level == "location":
            budget = self.total(gl, upto, location=str(holder))
        else:
            budget = self.total(gl, upto, cost_centres=holder)
        return Group(gl, level, budget, **parts).settle(self.warn, self.block)


def item_rows(dataset: Path) -> dict[str, dict[str, str]]:
    return {r["code"]: r for r in b.read_csv(dataset / "master" / "item.csv")}


def a0_values(stock: s2.Stock, mr: str, *, ordered: Iterable[str] = ()) -> dict[str, Decimal]:
    """The estimate of the A0 request of one material request per GL account: requested quantity x the estimate, paise
    half up per line (spec 4.1). `ordered` items are left out (their lines are ordered: the order counts instead)."""
    items = stock.items
    skip = set(ordered)
    out: dict[str, Decimal] = defaultdict(Decimal)
    for (m, item), v in stock.observe().items():
        if m != mr or item in skip or not v.get("requested"):
            continue
        out[items[item]["default_gl"]] += r2(D(v["requested"]) * stock.price(item))
    return dict(out)


def a0_qty(stock: s2.Stock, mr: str, item: str) -> Decimal:
    return D(stock.observe().get((mr, item), {}).get("requested"))


# ======================================================================================================================
# The forward-buy oracle
# ======================================================================================================================

RULE_FIELDS = (
    "enabled",
    "min_gap_pct",
    "min_trend_months",
    "max_cover_months",
    "holding_cost_pct_month",
    "min_net_saving_inr",
    "shelf_life_months",
    "storage_max_qty",
)
COMPANY_V1 = {  # spec 5.3: the migration's company default, version 1
    "enabled": "true",
    "min_gap_pct": "5.0000",
    "min_trend_months": "6",
    "max_cover_months": "3",
    "holding_cost_pct_month": "1.5000",
    "min_net_saving_inr": "10000.00",
    "shelf_life_months": "",
    "storage_max_qty": "",
}


@dataclass(frozen=True)
class Rule:
    enabled: bool
    min_gap_pct: Decimal
    min_trend_months: int
    max_cover_months: int
    holding_cost_pct_month: Decimal
    min_net_saving_inr: Decimal
    shelf_life_months: int | None
    storage_max_qty: Decimal | None
    label: str = ""


def rule_rows(dataset: Path, scenario_rules: Iterable[dict[str, str]] = ()) -> list[dict[str, str]]:
    """Company v1 (spec 5.3), the dataset's `master/forward_buy_rule.csv` and the rule versions an eval added."""
    base = {"version": "1", "category_code": "", "item_code": "", "location_code": "", "effective_from": "2000-01-01",
            "effective_to": "", **COMPANY_V1}  # fmt: skip
    found = [base] + b.read_csv(dataset / "master" / "forward_buy_rule.csv") + [dict(r) for r in scenario_rules]
    company_versions = [r for r in found[1:] if not (r.get("category_code") or r.get("item_code"))]
    if any(str(r.get("version", "")) == "1" for r in company_versions):
        found = found[1:]  # the dataset writes the company row itself
    return found


def rule_for(rows: list[dict[str, str]], item: str, category: str, location: str, on: date) -> Rule:
    """Each field from the narrowest row in force that sets it: item + location, item, category, company (spec 5.3)."""

    def in_force(r: dict[str, str]) -> bool:
        start = date.fromisoformat(r.get("effective_from") or "2000-01-01")
        end = r.get("effective_to") or ""
        return start <= on and (not end or date.fromisoformat(end) >= on)

    scopes = [
        ("item_loc", lambda r: r.get("item_code") == item and r.get("location_code") == location),
        ("item", lambda r: r.get("item_code") == item and not r.get("location_code")),
        ("category", lambda r: r.get("category_code") == category and not r.get("item_code")),
        ("company", lambda r: not r.get("category_code") and not r.get("item_code")),
    ]
    chosen: list[dict[str, str]] = []
    for _, match in scopes:
        mine = [r for r in rows if match(r) and in_force(r)]
        if mine:
            chosen.append(max(mine, key=lambda r: (int(r.get("version") or 1), r.get("effective_from", ""))))
    values: dict[str, str] = {}
    for f in RULE_FIELDS:
        values[f] = next((str(r[f]) for r in chosen if str(r.get(f, "")).strip() != ""), "")
    labels = []
    for r in reversed(chosen):
        scope = r.get("item_code") or r.get("category_code") or "forward_buy"
        if r.get("location_code"):
            scope += "@" + r["location_code"]
        labels.append(f"{scope} v{r.get('version') or 1}")
    return Rule(
        enabled=str(values["enabled"]).lower() not in ("false", "0", "no"),
        min_gap_pct=D(values["min_gap_pct"]),
        min_trend_months=int(D(values["min_trend_months"])),
        max_cover_months=int(D(values["max_cover_months"])),
        holding_cost_pct_month=D(values["holding_cost_pct_month"]),
        min_net_saving_inr=D(values["min_net_saving_inr"]),
        shelf_life_months=int(D(values["shelf_life_months"])) if values["shelf_life_months"] else None,
        storage_max_qty=D(values["storage_max_qty"]) if values["storage_max_qty"] else None,
        label=" + ".join(labels),
    )


@dataclass(frozen=True)
class Bench:
    item: str
    price: Decimal
    uom: str
    basis: str
    effective: date
    region: str
    status: str
    source_name: str = ""
    source_ref: str = ""


def bench_rows(rows: Iterable[dict[str, str]], default_status: str = "verified") -> list[Bench]:
    out = []
    for r in rows:
        out.append(
            Bench(
                r["item_code"],
                D(r["price"]),
                r.get("uom", ""),
                (r.get("basis") or "delivered").lower(),
                date.fromisoformat(r["effective_date"]),
                r.get("region_code", ""),
                (r.get("status") or default_status).lower(),
                r.get("source_name", ""),
                r.get("source_ref", ""),
            )
        )
    return out


@dataclass(frozen=True)
class Wrong:
    """Rules of 5.2 switched to a wrong version, one at a time (the mutations of E90 and E92)."""

    no_holding: bool = False  # holding cost ignored
    cover_unbounded: bool = False  # cover months beyond max_cover_months
    falling_ok: bool = False  # a dip below a falling trend still counts (the trend's sign ignored: a straight average)
    ignore_contract: bool = False  # a contract item can be recommended


RIGHT3 = Wrong()


@dataclass
class Facts:
    """What the engine reads for one item and location, all in the item's base unit (spec 5.2)."""

    item: str
    location: str
    region: str
    as_of: date
    base_uom: str
    purchase_factor: Decimal  # base units per purchase unit
    pack: Decimal
    benches: list[Bench]
    conv: dict[str, Decimal]  # other unit -> base units per that unit
    issues: dict[str, Decimal]  # month -> issued base quantity at the location
    on_hand: Decimal
    open_supply: Decimal
    requirements: dict[str, Decimal]  # month -> base quantity still needed by open material requirements
    anchor_need_by: date | None
    est_price: Decimal
    budget_left: Decimal | None = None
    stocked: bool = True
    capex: bool = False
    on_contract: bool = False
    last_paid: Decimal | None = None
    max_age_days: int = 45


@dataclass
class Result:
    recommended: bool = False
    reason: str = ""
    price_now: Decimal | None = None
    trend_now: Decimal | None = None
    slope: Decimal | None = None
    trend_points: int = 0
    dip_pct: Decimal | None = None
    avg_use: Decimal | None = None
    cover: Decimal | None = None
    months: list[dict[str, Any]] = field(default_factory=list)
    qty: Decimal = Z
    value: Decimal = Z
    gross_saving: Decimal = Z
    holding_cost: Decimal = Z
    net_saving: Decimal = Z
    limit: str = ""
    bench: Bench | None = None
    holding_per_unit: Decimal = Z

    def number_fields(self) -> dict[str, Any]:
        return {
            "price_now": self.price_now,
            "trend_now": self.trend_now,
            "slope": self.slope,
            "dip_pct": self.dip_pct,
            "avg_use": self.avg_use,
            "cover": self.cover,
            "qty": self.qty,
            "value": self.value,
            "gross_saving": self.gross_saving,
            "holding_cost": self.holding_cost,
            "net_saving": self.net_saving,
        }


def in_base(f: Facts, bench: Bench) -> Decimal | None:
    if bench.uom in ("", f.base_uom):
        return bench.price
    factor = f.conv.get(bench.uom)
    return None if not factor else bench.price / factor


def counts_here(f: Facts, bench: Bench) -> bool:
    return (
        bench.item == f.item
        and bench.status == "verified"
        and bench.basis == "delivered"
        and (not bench.region or bench.region == f.region)
        and in_base(f, bench) is not None
    )


def trend(f: Facts, eval_month: str) -> list[tuple[int, Decimal]]:
    """One point per month for the 12 complete months before the evaluation month: the month's average, 4 places half up."""
    by_month: dict[str, list[Decimal]] = defaultdict(list)
    for bench in f.benches:
        p = month(bench.effective)
        if counts_here(f, bench) and shift(eval_month, -12) <= p < eval_month:
            by_month[p].append(in_base(f, bench) or Z)
    return sorted((months_between(eval_month, p), r4(sum(v, Z) / len(v))) for p, v in by_month.items())


def fit(points: list[tuple[int, Decimal]]) -> tuple[Decimal, Decimal]:
    n = Decimal(len(points))
    xbar = sum((Decimal(x) for x, _ in points), Z) / n
    ybar = sum((y for _, y in points), Z) / n
    sxy = sum(((Decimal(x) - xbar) * (y - ybar) for x, y in points), Z)
    sxx = sum(((Decimal(x) - xbar) ** 2 for x, _ in points), Z)
    slope = sxy / sxx if sxx else Z
    return ybar - slope * xbar, slope


def price_now(f: Facts) -> Bench | None:
    """The counting benchmark (D52: verified, fresh, delivered, in region, convertible) with the latest date; ties the
    highest price."""
    fresh = [
        x for x in f.benches if counts_here(f, x) and x.effective <= f.as_of and (f.as_of - x.effective).days <= f.max_age_days
    ]
    if not fresh:
        return None
    return max(fresh, key=lambda x: (x.effective, in_base(f, x) or Z))


def evaluate(f: Facts, rule: Rule, wrong: Wrong = RIGHT3) -> Result:  # spec 5.2 step by step
    with localcontext() as ctx:
        ctx.prec = 28
        out = Result()
        if not f.stocked or f.capex or not rule.enabled:
            out.reason = "not_eligible"
            return out
        if f.on_contract and not wrong.ignore_contract:
            out.reason = "on_contract"
            return out
        eval_month = month(f.as_of)
        bench = price_now(f)
        points = trend(f, eval_month)
        out.trend_points = len(points)
        if bench is None:
            out.reason = "no_fresh_benchmark"
            return out
        out.bench = bench
        p = r4(in_base(f, bench) or Z)
        out.price_now = p
        if len(points) < rule.min_trend_months:
            out.reason = "no_trend"
            return out
        a, slope = fit(points)
        if wrong.falling_ok:
            slope = Z
            a = sum((y for _, y in points), Z) / len(points)
        out.trend_now, out.slope = r4(a), r4(slope)
        t = r4(a)
        out.dip_pct = r4((t - p) / t * HUNDRED)
        if out.dip_pct <= rule.min_gap_pct:
            out.reason = "not_below_trend"
            return out
        if f.last_paid is not None and p >= f.last_paid:
            out.reason = "not_below_last_paid"
            return out
        used = sum((q for m, q in f.issues.items() if shift(eval_month, -12) <= m < eval_month), Z)
        avg = r4(used / 12)
        out.avg_use = avg
        m0 = month(f.anchor_need_by) if f.anchor_need_by else eval_month
        x0 = months_between(eval_month, m0)

        def need(period: str) -> Decimal:
            return max(f.requirements.get(period, Z), avg)

        through = sum((need(shift(eval_month, i)) for i in range(x0 + 1)), Z)
        cover = max(Z, f.on_hand + f.open_supply - through)
        out.cover = r4(cover)
        h = Z if wrong.no_holding else r4(p * rule.holding_cost_pct_month / HUNDRED)
        out.holding_per_unit = h
        horizon = 60 if wrong.cover_unbounded else rule.max_cover_months
        limit = "max_cover_months"
        if rule.shelf_life_months is not None and rule.shelf_life_months - 1 < horizon:
            horizon = max(rule.shelf_life_months - 1, 0)
            limit = "shelf_life"
        # the need of months 1..H after the cover is used up from month 1 on
        left = cover
        months: list[dict[str, Any]] = []
        for m in range(1, horizon + 1):
            period = shift(m0, m)
            d = need(period)
            take = min(left, d)
            left -= take
            e = r4(a + slope * (x0 + m))
            net = e - p - m * h
            if net <= 0:
                limit = "holding"
                continue
            months.append({"month": period, "m": m, "expected_price": e, "net_per_unit": net, "qty": d - take})
        total = sum((x["qty"] for x in months), Z)
        caps: list[tuple[Decimal, str]] = []
        if rule.storage_max_qty is not None:
            caps.append((max(rule.storage_max_qty - cover, Z), "storage"))
        if f.budget_left is not None and f.est_price > 0:
            caps.append((max(f.budget_left / f.est_price * f.purchase_factor, Z), "budget"))
        x = total
        for cap, name in sorted(caps, key=lambda c: c[0]):
            if cap < x:
                x, limit = cap, name
                break
        # whole purchase units times the pack, cut from the latest months
        unit = f.purchase_factor * f.pack
        x = (x / unit).to_integral_value(rounding=ROUND_DOWN) * unit if unit > 0 else x
        cut = total - x
        for row in reversed(months):
            take = min(row["qty"], cut)
            row["qty"] -= take
            cut -= take
        months = [r for r in months if r["qty"] > 0]
        out.months = [{**r, "qty": r4(r["qty"]), "net_per_unit": r4(r["net_per_unit"])} for r in months]
        out.qty = r4(x)
        out.limit = limit
        out.gross_saving = r2(sum((r["qty"] * (r["expected_price"] - p) for r in months), Z))
        out.holding_cost = r2(sum((r["qty"] * r["m"] * h for r in months), Z))
        out.net_saving = r2(sum((r["qty"] * r["net_per_unit"] for r in months), Z))
        out.value = r2(x / f.purchase_factor * f.est_price) if f.purchase_factor else Z
        if x <= 0:
            out.reason = "no_need"
        elif out.net_saving > rule.min_net_saving_inr:
            out.recommended = True
            out.reason = ""
        else:
            out.reason = "saving_too_small"
        return out


def facts_for(
    dataset: Path,
    stock: s2.Stock | None,
    item: str,
    location: str,
    as_of: date,
    *,
    extra_benches: Iterable[Bench] = (),
    anchor_need_by: date | None = None,
    budget_left: Decimal | None = None,
) -> Facts:
    """The engine's inputs from the raw files and block 2's oracle of A0 (open supply = A0's requests, spec 5.2)."""
    items = item_rows(dataset)
    it = items[item]
    loc = next(r for r in b.read_csv(dataset / "master" / "location.csv") if r["code"] == location)
    conv: dict[str, Decimal] = {}
    purchase_factor = Decimal(1)
    for r in b.read_csv(dataset / "master" / "uom_conversion.csv"):
        if r["item_code"] != item:
            continue
        if r["to_uom"] == it["base_uom"]:
            conv[r["from_uom"]] = D(r["factor"])
        elif r["from_uom"] == it["base_uom"] and D(r["factor"]):
            conv[r["to_uom"]] = 1 / D(r["factor"])
    if it.get("purchase_uom") and it["purchase_uom"] != it["base_uom"]:
        purchase_factor = conv.get(it["purchase_uom"], Decimal(1))
    issues: dict[str, Decimal] = defaultdict(Decimal)
    for r in b.read_csv(dataset / "history" / "stock_movement.csv"):
        if r["item_code"] == item and (r.get("location_code") or location) == location and r["type"] == "issue":
            issues[r["date"][:7]] += abs(D(r["qty"]))
    on_hand = Z
    supply = Z
    requirements: dict[str, Decimal] = defaultdict(Decimal)
    if stock is not None:
        on_hand = stock.usable(item, location)
        for ln in stock.lines:
            if ln.item != item or ln.loc != location or ln.status != "open":
                continue
            requirements[month(ln.need_by)] += max(ln.need - ln.issued, Z)
            supply += ln.requested()  # the live request lines bought for it (A0 has no purchase order yet)
    else:
        on_hand = b.on_hand_at_cutoff(dataset).get((item, location), Z)
    rules_ = b.read_csv(dataset / "master" / "price_threshold_rule.csv")
    company = [r for r in rules_ if not r.get("category_code")]
    max_age = int(D(company[-1]["evidence_max_age_days"])) if company and company[-1].get("evidence_max_age_days") else 45
    return Facts(
        item=item,
        location=location,
        region=loc.get("region_code", ""),
        as_of=as_of,
        base_uom=it["base_uom"],
        purchase_factor=purchase_factor,
        pack=D(it.get("pack_size") or "1") or Decimal(1),
        benches=bench_rows(b.read_csv(dataset / "master" / "market_benchmark.csv")) + list(extra_benches),
        conv=conv,
        issues=dict(issues),
        on_hand=on_hand,
        open_supply=supply,
        requirements=dict(requirements),
        anchor_need_by=anchor_need_by,
        est_price=D(it["standard_price"]),
        budget_left=budget_left,
        stocked=str(it.get("stocked", "")).lower() == "true" and it.get("kind", "goods") == "goods",
        capex=str(it.get("capex", "")).lower() == "true",
        on_contract=on_contract(dataset, item, location, as_of),
        max_age_days=max_age,
    )


def on_contract(dataset: Path, item: str, location: str, on: date) -> bool:
    """A contract price in force for the item at the location on the date: an active price record with a contract,
    its contract active, in scope for the location (its own location, its region, or everywhere). A price record without a
    contract is a price list, not a contract (sourcing's rule, D44)."""
    contracts = {r["code"]: r for r in b.read_csv(dataset / "master" / "contract.csv")}
    loc = next((r for r in b.read_csv(dataset / "master" / "location.csv") if r["code"] == location), {})
    for r in b.read_csv(dataset / "master" / "price_record.csv"):
        c = contracts.get(r.get("contract_code", ""))
        if r["item_code"] != item or c is None or r.get("status", "active") != "active" or c.get("status") != "active":
            continue
        if not (r["valid_from"] <= on.isoformat() <= (r.get("valid_to") or "9999")):
            continue
        if not (c["valid_from"] <= on.isoformat() <= (c.get("valid_to") or "9999")):
            continue
        if r.get("location_code") and r["location_code"] != location:
            continue
        if r.get("region_code") and r["region_code"] != loc.get("region_code"):
            continue
        return True
    return False


# ======================================================================================================================
# Reading the platform's answers
# ======================================================================================================================


def listed(body: Any, *keys: str) -> list[dict[str, Any]]:
    """A list answered bare or wrapped in an object under one of these keys (the spec names the key; both accepted)."""
    if isinstance(body, list):
        return [x for x in body if isinstance(x, dict)]
    if isinstance(body, dict):
        for k in keys:
            if isinstance(body.get(k), list):
                return [x for x in body[k] if isinstance(x, dict)]
    return []


def groups_of(body: Any) -> dict[tuple[str, str], dict[str, Any]]:
    """The groups of a budget check by (GL account, level)."""
    return {(str(g.get("gl_code")), str(g.get("level"))): g for g in listed(body, "groups")}


GROUP_FIELDS = ("budget", "held_by_requests", "held_by_orders", "spent", "committed", "requested", "used_pct", "status")


def group_diff(got: dict[str, Any] | None, want: dict[str, Any], fields: Iterable[str] = GROUP_FIELDS) -> list[str]:
    if got is None:
        return ["the group is missing"]
    wrong = []
    for f in fields:
        w, g = want.get(f), got.get(f)
        if f == "status":
            if w not in (None, "") and str(g) != str(w):
                wrong.append(f"{f} {g}, expected {w}")
        elif dec(g) != (Decimal(w) if w is not None else None):
            wrong.append(f"{f} {g}, expected {w}")
    return wrong


def truth_group(row: dict[str, str]) -> dict[str, Any]:
    out: dict[str, Any] = {f: dec(row.get(f)) for f in GROUP_FIELDS if f != "status"}
    out["status"] = row.get("status", "")
    return out


FB_FIELDS = ("price_now", "trend_now", "slope", "dip_pct", "avg_use", "cover", "qty", "value", "gross_saving",
             "holding_cost", "net_saving")  # fmt: skip
EMPTY_WHEN_NONE = ("qty", "value", "gross_saving", "holding_cost", "net_saving", "cover", "avg_use")


def fb_body(body: Any) -> dict[str, Any]:
    """A record or a preview: the engine's fields at the top or under `result` (the spec does not say which for the
    preview; both accepted)."""
    if isinstance(body, dict) and isinstance(body.get("result"), dict) and "qty" not in body:
        return {**body["result"], **{k: v for k, v in body.items() if k != "result"}}
    return body if isinstance(body, dict) else {}


def fb_diff(got: dict[str, Any], want: Result, *, numbers: bool = True, months: bool = True) -> list[str]:
    wrong = []
    reason = got.get("reason") or got.get("lapse_reason") or ""
    rec = got.get("recommended")
    if rec is None and got.get("status"):
        rec = got.get("status") in ("open", "accepted", "approved")
    if rec is not None and bool(rec) != want.recommended:
        wrong.append(f"recommended {rec}, expected {want.recommended}")
    if not want.recommended and want.reason and want.reason not in str(reason):
        wrong.append(f"reason {reason!r}, expected {want.reason}")
    if not numbers:
        return wrong
    for f, w in want.number_fields().items():
        if w is None or (not want.recommended and f in EMPTY_WHEN_NONE and not w):
            continue
        if dec(got.get(f)) != w:
            wrong.append(f"{f} {got.get(f)}, expected {w}")
    if want.recommended and want.limit and str(got.get("limit") or got.get("limit_kind") or "") != want.limit:
        wrong.append(f"limit {got.get('limit') or got.get('limit_kind')}, expected {want.limit}")
    if months and want.months:
        gm = [m for m in got.get("months") or [] if isinstance(m, dict)]
        mine = [(m["month"], m["expected_price"], m["net_per_unit"], m["qty"]) for m in want.months]
        theirs = [(str(m.get("month"))[:7], dec(m.get("expected_price")), dec(m.get("net_per_unit")), dec(m.get("qty")))
                  for m in gm]  # fmt: skip
        if theirs != mine:
            wrong.append(f"months {theirs}, expected {mine}")
    return wrong


def truth_fb(rows: list[dict[str, str]]) -> dict[str, Any]:
    """One scenario of `truth_forward_buy.csv` (one row per included month, scenario columns repeated)."""
    if not rows:
        return {}
    first = rows[0]
    out: dict[str, Any] = {f: dec(first.get(f)) for f in FB_FIELDS}
    out["recommended"] = str(first.get("recommended", "")).lower() in ("true", "yes", "1")
    out["reason"] = first.get("reason", "")
    out["limit"] = first.get("limit", "")
    out["months"] = [
        (r.get("month", "")[:7], dec(r.get("expected_price")), dec(r.get("net_per_unit")), dec(r.get("month_qty")))
        for r in rows
        if r.get("month")
    ]
    return out


def oracle_vs_truth(want: Result, truth: dict[str, Any]) -> list[str]:
    """The oracle against the generator's truth (the spec's numbers): a difference is a spec gap or an oracle bug."""
    if not truth:
        return ["no truth rows"]
    wrong = []
    if truth["recommended"] != want.recommended:
        wrong.append(f"recommended {truth['recommended']} in the truth, {want.recommended} by the spec's rules")
    if truth["reason"] and truth["reason"] != want.reason:
        wrong.append(f"reason {truth['reason']} in the truth, {want.reason} by the spec's rules")
    for f, w in want.number_fields().items():
        t = truth.get(f)
        if t is not None and w is not None and t != w:
            wrong.append(f"{f} {t} in the truth, {w} by the spec's rules")
    mine = [(m["month"], m["expected_price"], m["net_per_unit"], m["qty"]) for m in want.months]
    if truth["months"] and truth["months"] != mine:
        wrong.append(f"months {truth['months']} in the truth, {mine} by the spec's rules")
    return wrong


# ---- Indian money and the explanation (spec 5.5) --------------------------------------------------------------------


def inr(x: Decimal) -> str:
    """Rs amounts as the spec writes them: 6,86,320.00."""
    s = f"{r2(x):.2f}"
    whole, frac = s.split(".")
    neg = whole.startswith("-")
    whole = whole.lstrip("-")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        whole = ",".join([*parts, tail])
    return ("-" if neg else "") + whole + "." + frac


def number_tokens(text_: str) -> list[Decimal]:
    out = []
    for tok in re.findall(r"\d[\d,]*(?:\.\d+)?", text_):
        with contextlib.suppress(ArithmeticError):
            out.append(Decimal(tok.replace(",", "")))
    return out


def record_numbers(record: Any, extra: Iterable[Any] = ()) -> set[Decimal]:
    """Every number the record holds (recursively, dates split into their parts), as written and rounded to 0, 2 and 4
    places."""
    found: set[Decimal] = set()

    def add(v: Any) -> None:
        if isinstance(v, dict):
            for x in v.values():
                add(x)
        elif isinstance(v, list | tuple | set):
            for x in v:
                add(x)
        elif isinstance(v, bool) or v is None:
            return
        else:
            for t in number_tokens(str(v)):
                found.update({t, r2(t), r4(t), t.to_integral_value(rounding=ROUND_HALF_UP)})

    add(record)
    add(list(extra))
    return {x.normalize() for x in found}


def invented(text_: str, allowed: set[Decimal]) -> list[str]:
    return sorted({str(t) for t in number_tokens(text_) if t.normalize() not in allowed})


# ======================================================================================================================
# The platform
# ======================================================================================================================

def typed(key: str, value: Any) -> Any:
    """A rule field as its column's type, for the owner's insert."""
    if key in ("version", "min_trend_months", "max_cover_months", "shelf_life_months"):
        return int(D(value))
    if key in ("min_gap_pct", "holding_cost_pct_month", "min_net_saving_inr", "storage_max_qty"):
        return D(value)
    if key in ("effective_from", "effective_to"):
        return date.fromisoformat(str(value)[:10])
    if key == "enabled":
        return str(value).lower() not in ("false", "0", "no")
    return value


RULE_PATHS = ("/api/setup/forward-buy", "/api/setup/forward-buying", "/api/setup/forward-buy-rule",
              "/api/setup/forward-buy-rules")  # fmt: skip


class Co3(s2.Co):
    """A demo company with the block 3 calls."""

    # ---- people (spec 3.3, 3.4: U03 buyer, U05 finance, U08 purchase manager, U09 director on Apex) ----
    def buyer(self) -> str:
        return self.role("buyer")

    def finance(self) -> str:
        return self.role("finance")

    def ap(self) -> str:
        return self.role("ap")

    def store(self) -> str:
        return self.role("store")

    def pm(self) -> str:
        """Who the `forward_buy` rule names: the purchase manager on Apex, the CFO on Brightwave (spec 3.2 step 4)."""
        return self.designation("Purchase Manager" if self.name == "apex" else "Chief Financial")

    def director(self) -> str:
        """The top approver: the Director on Apex, the CFO on Brightwave (it has no director)."""
        try:
            return self.designation("Director")
        except StopIteration:
            return self.designation("Chief Financial")

    def relogin(self, code: str) -> None:
        self._headers.pop(code, None)

    # ---- files ----
    def scenario_dir(self, kind: str, label: str) -> Path | None:
        """`<kind>/<label>` (or `<label>-...`) in the reset's dataset (spec 3.2 step 5)."""
        base = self.dataset / kind
        if not base.is_dir():
            return None
        found = sorted(p for p in base.iterdir() if p.is_dir() and (p.name == label or p.name.startswith(label + "-")))
        return found[0] if found else None

    def scenario_rows(self, kind: str, label: str, name: str) -> list[dict[str, str]]:
        folder = self.scenario_dir(kind, label)
        return b.read_csv(folder / f"{name}.csv") if folder else []

    def truth3(self, name: str, scenario: str) -> list[dict[str, str]]:
        path = self.file(f"truth/{name}.csv")
        rows = b.read_csv(path) if path else []
        return [r for r in rows if r.get("scenario", "") == scenario or r.get("scenario", "").startswith(scenario + "-")]

    # ---- the budget API (spec 4.6) ----
    def check_of(self, no: str, by: str | None = None, final_prices: bool = False) -> tuple[int, dict[str, Any]]:
        path = f"/api/budgets/check/{no}" + ("?final_prices=true" if final_prices else "")
        r = self.call(by or self.finance(), "GET", path)
        return r.status_code, js(r) if r.status_code == 200 else {"text": r.text[:300]}

    def groups(self, no: str, by: str | None = None) -> dict[tuple[str, str], dict[str, Any]]:
        _, body = self.check_of(no, by)
        return groups_of(body)

    def variance(self, by: str | None = None) -> dict[str, Any]:
        r = self.call(by or self.finance(), "GET", "/api/budgets/variance")
        return js(r) if r.status_code == 200 else {}

    def periods(self, by: str | None = None, fy: str | None = None) -> tuple[int, dict[str, Any]]:
        r = self.call(by or self.finance(), "GET", "/api/budgets/periods" + (f"?fy={fy}" if fy else ""))
        return r.status_code, js(r)

    def lock(self, period: str, by: str | None = None, reason: str | None = "Month-end close") -> Any:
        body = {} if reason is None else {"reason": reason}
        return self.call(by or self.finance(), "POST", f"/api/budgets/periods/{period}/lock", body)

    def close_year(self, fy: str, by: str | None = None, note: str = "eval: year end") -> Any:
        return self.call(by or self.finance(), "POST", f"/api/budgets/years/{fy}/close", {"note": note})

    def adjust(self, body: dict[str, Any], by: str | None = None) -> Any:
        return self.call(by or self.finance(), "POST", "/api/budgets/adjustments", body)

    def actuals(self) -> list[dict[str, Any]]:
        cols = self.columns("budget_actual")
        want = [c for c in ("id", "invoice_no", "source", "gr_no", "po_no", "po_line_no", "request_no", "request_line_no",
                            "cost_centre_code", "gl_code", "location_code", "period", "event_period", "amount")
                if c in cols]  # fmt: skip
        return [dict(zip(want, r, strict=True)) for r in self.q(f"select {', '.join(want)} from budget_actual order by id")]

    def lock_rows(self) -> dict[str, Any]:
        if "budget_period_lock" not in self.tables():
            return {}
        return {str(r[0]): r for r in self.q("select period, locked_by, locked_at, reason from budget_period_lock")}

    # ---- the forward-buy API (spec 5.6) ----
    def fb_list(self, by: str | None = None, **params: Any) -> tuple[int, list[dict[str, Any]]]:
        r = self.call(by or self.buyer(), "GET", "/api/forward-buys",
                      params={k: v for k, v in params.items() if v is not None})  # fmt: skip
        return r.status_code, listed(js(r), "forward_buys")

    def fb_get(self, fid: Any, by: str | None = None) -> tuple[int, dict[str, Any]]:
        r = self.call(by or self.buyer(), "GET", f"/api/forward-buys/{fid}")
        return r.status_code, fb_body(js(r)) if r.status_code == 200 else {}

    def preview(self, item: str, location: str, by: str | None = None, request_no: str | None = None,
                line_no: int | None = None) -> tuple[int, dict[str, Any]]:  # fmt: skip
        params = {"item": item, "location": location, "request_no": request_no, "line_no": line_no}
        r = self.call(by or self.buyer(), "GET", "/api/forward-buys/preview",
                      params={k: v for k, v in params.items() if v is not None})  # fmt: skip
        return r.status_code, fb_body(js(r)) if r.status_code == 200 else {"text": r.text[:300]}

    def fb_act(self, fid: Any, action: str, by: str, body: dict[str, Any]) -> Any:
        return self.call(by, "POST", f"/api/forward-buys/{fid}/{action}", body)

    def fb_rows(self, item: str, location: str) -> list[dict[str, Any]]:
        if "forward_buy" not in self.tables():
            return []
        cols = ("id", "status", "request_no", "line_no", "qty", "net_saving", "superseded_by", "lapse_reason",
                "proposal_id", "new_request_no", "accepted_qty", "explanation", "explanation_source", "rule_label")  # fmt: skip
        have = [c for c in cols if c in self.columns("forward_buy")]
        found = self.q(f"select {', '.join(have)} from forward_buy where item_code = :i and location_code = :l order by id",
                       i=item, l=location)  # fmt: skip
        return [dict(zip(have, r, strict=True)) for r in found]

    def live(self, item: str, location: str) -> list[dict[str, Any]]:
        return [r for r in self.fb_rows(item, location) if r.get("status") in ("open", "accepted")]

    def evaluations(self, item: str | None = None) -> list[tuple[Any, ...]]:
        found = self.q("select id, version, input::text, output::text from rule_evaluation where key = 'forward_buy' "
                       "order by id")  # fmt: skip
        return [r for r in found if item is None or f'"{item}"' in str(r[2])]

    def fb_cli(self, *args: str, on: date = AS_OF) -> tuple[int, str]:
        """`p2p.cli forward-buy` on the business day (23 November unless said), then the queued jobs."""
        with b.on_day(on):
            code, said = b.cli("forward-buy", self.slug, *args)
            self.work()
        return code, said

    def line_of(self, no: str, item: str) -> int | None:
        found = self.one("select line_no from request_line where request_no = :n and item_code = :i order by line_no",
                         n=no, i=item)  # fmt: skip
        return int(found) if found is not None else None

    # ---- benchmarks (0b API) and rule versions ----
    def add_bench(self, row: dict[str, str], by: str | None = None, verifier: str | None = None) -> int | None:
        """Entered by the buyer, verified by the purchase manager (spec 3.4), then the queued jobs run."""
        keys = ("item_code", "price", "uom", "basis", "effective_date", "region_code", "source_kind", "source_name",
                "source_ref", "note")  # fmt: skip
        body = {k: row.get(k) or None for k in keys}
        body["basis"] = body["basis"] or "delivered"
        body["source_kind"] = body["source_kind"] or "market_survey"
        r = self.call(by or self.buyer(), "POST", "/api/benchmarks", body)
        bid = js(r).get("id")
        if r.status_code not in (200, 201) or not bid:
            return None
        v = self.call(verifier or self.pm(), "POST", f"/api/benchmarks/{bid}/verify", {"note": "eval: block 3"})
        self.work()
        return int(bid) if v.status_code == 200 else None

    def add_rule(self, row: dict[str, str]) -> str:
        """A rule version through the admin app (maker and checker) at the touchless rule's pattern. The spec does not name
        the path (5.7: "a Forward buying section like the touchless rule"): the likely ones are tried; when none answers,
        the version is written as the company's owner so the engine can still be tested (the eval says so in a note)."""
        body = {k: v for k, v in row.items() if v not in ("", None) and k not in ("id", "entered_by", "entered_at")}
        body.setdefault("effective_from", AS_OF.isoformat())
        for path in RULE_PATHS:
            asked, done = self.d22(path, body, method="POST")
            if asked.status_code == 202 and done is not None and done.status_code == 200:
                return "api"
            if asked.status_code not in (404, 405):
                break
        cols = self.columns("forward_buy_rule")
        data = {k: typed(k, v) for k, v in body.items() if k in cols and k != "reason"}
        if not data:
            return ""
        from sqlalchemy import text

        with lib.owner_session(self.slug) as s:
            names = ", ".join(data)
            s.execute(text(f"insert into forward_buy_rule ({names}) values ({', '.join(':' + k for k in data)})"), data)
            s.commit()
        return "owner"

    # ---- buying and paying (the existing API, as people do it) ----
    def ordered_lines(self, no: str) -> dict[str, dict[str, Any]]:
        """Live purchase order lines of a request, by item."""
        found = self.q(
            "select pl.item_code, pl.po_no, pl.line_no, pl.qty, pl.unit_price, pl.status, p.status, pl.short_closed_qty, "
            "pl.received_qty from po_line pl join po p on p.no = pl.po_no where pl.request_no = :n "
            "and p.status not in ('cancelled', 'rejected') and pl.status <> 'cancelled' order by pl.po_no, pl.line_no",
            n=no,
        )
        keys = ("item", "po_no", "line_no", "qty", "price", "line_status", "po_status", "short_closed", "received")
        return {str(r[0]): dict(zip(keys, r, strict=True)) for r in found}

    def issue_po(self, no: str) -> str:
        """An order taken to sent: the buyer issues (its own tier), else whoever may approve, then the buyer sends."""
        people = [self.buyer(), *self.holders("approver")]
        for _ in range(8):
            status = self.po_status(no)
            if status not in ("draft", "pending_approval", "submitted", "approved", "waiting"):
                break
            if status == "approved":
                self.call(self.buyer(), "POST", f"/api/purchase-orders/{no}/send", {})
                if self.po_status(no) == "approved":
                    break
                continue
            if not any(self.call(c, "POST", f"/api/purchase-orders/{no}/approve", {}).status_code == 200 for c in people):
                break
        return self.po_status(no)

    def direct(self, no: str, line: int, supplier: str, price: str, reason: str) -> Any:
        """The buyer loosens a line to a direct buy; the purchase manager agrees (`sourcing.agree_direct`)."""
        r = self.call(self.buyer(), "POST", f"/api/requisitions/{no}/lines/{line}/route",
                      {"route": "direct", "supplier_code": supplier, "price": price, "reason": reason})  # fmt: skip
        pid = js(r).get("proposal_id")
        if pid:
            self.call(self.pm(), "POST", f"/api/admin/proposals/{pid}/approve", {})
        self.work()
        return r

    def invoice(self, po: str, lines: list[tuple[int, Decimal, Decimal]], on: date, number: str,
                charges: list[dict[str, str]] | None = None) -> tuple[str | None, dict[str, Any]]:  # fmt: skip
        supplier = str(self.one("select supplier_code from po where no = :n", n=po))
        body: dict[str, Any] = {
            "supplier_code": supplier, "number": number, "date": on.isoformat(),
            "lines": [{"po_no": po, "po_line_no": ln, "qty": str(q), "unit_price": str(p)} for ln, q, p in lines],
            "charges": charges or [],
        }  # fmt: skip
        sup = next((r for r in self.rows("master", "supplier") if r["code"] == supplier), {})
        body["supplier_gstin"] = sup.get("gstin") or None
        body["buyer_gstin"] = self.one("select buyer_gstin from po where no = :n", n=po)
        if sup.get("e_invoice_applicable") == "true":
            body["irn"] = (number.replace("-", "") * 64)[:64]
        with b.on_day(on):
            r = self.call(self.ap(), "POST", "/api/invoices", body)
        got = js(r) if isinstance(js(r), dict) else {}
        no = str(got["invoice"]) if r.status_code == 200 and got.get("invoice") else None
        return no, {"status_code": r.status_code, **got}

    def pay(self, on: date) -> str:
        """A payment run of what is due, proposed by finance, approved by the director on its page, filed and settled by
        the bank's statement that day (block 12)."""
        from sqlalchemy import text

        with b.on_day(on):
            r = self.call(self.finance(), "POST", "/api/payment-runs", {"run_date": on.isoformat(), "window_days": 120})
            run = js(r).get("run")
            if not run:
                return f"no run: {r.status_code} {r.text[:200]}"
            with lib.owner_session(self.slug) as owned:  # the evals have no mailbox for the one-time code
                owned.execute(
                    text("update org_setting set value = '999999999.00' where key = 'payment_run_otp_above'")
                )
                owned.commit()
            try:
                self.call(self.director(), "POST", f"/api/payment-runs/{run}/approve", {"code": None})
            finally:
                with lib.owner_session(self.slug) as owned:
                    owned.execute(text("update org_setting set value = '0.00' where key = 'payment_run_otp_above'"))
                    owned.commit()
            self.call(self.finance(), "POST", f"/api/payment-runs/{run}/file", {})
            pays = self.q("select no, amount from payment where run_no = :r order by no", r=run)
            rows = ["date,utr,narration,debit,credit"]
            for n, (pay_no, amount) in enumerate(pays, start=1):
                rows.append(f"{on.isoformat()},EVAL{on:%y%m%d}{n:04d},NEFT {pay_no} EVAL,{amount},0.00")
            done = self.call(
                self.finance(), "POST", "/api/payment-runs/statement", {"csv": "\n".join(rows) + "\n"}
            )
            self.work()
        return str(run) if done.status_code == 200 else f"{run}: {done.status_code} {done.text[:200]}"

    def service_order(self, check: b.Checker, label: str, on: date) -> tuple[str, str, int] | None:
        """SV-01 (test bench calibration, GL 5240) for CC-MNT, bought directly from SUP-006 at its standard price, the order
        sent and the service confirmed by the person who asked (spec E86). The request form takes no cost centre, so the
        line is put on CC-MNT as the company's owner before it is submitted (setup, not a rule under test)."""
        from sqlalchemy import text

        asker = next(c for c, u in sorted(self.people.items()) if "production" in (u.get("designation") or "").lower())
        price = D(next(r for r in self.rows("master", "item") if r["code"] == "SV-01")["standard_price"])
        with b.on_day(on):
            r = self.call(asker, "POST", "/api/requisitions", {"lines": [{"item_code": "SV-01", "qty": "1"}],
                          "need_by": (on + timedelta(days=14)).isoformat(), "location": CKN,
                          "reason": "eval: test bench calibration"})  # fmt: skip
            no = js(r).get("id")
            if not check(r.status_code == 200 and bool(no), f"{label}: the SV-01 request was refused: {r.status_code} "
                         f"{r.text[:200]}", "medium"):  # fmt: skip
                return None
            with lib.owner_session(self.slug) as s:
                s.execute(text("update request_line set cost_centre_code = 'CC-MNT' where request_no = :n"), {"n": no})
                s.commit()
            self.call(asker, "POST", f"/api/requisitions/{no}/submit", {})
            self.approve(str(no))
            b.cli("source", self.slug, "--request", str(no))
            self.work()
            self.direct(str(no), 1, "SUP-006", str(price), "eval: the calibration firm we always use")
            b.cli("order", self.slug, "--request", str(no))
            self.work()
            line = self.ordered_lines(str(no)).get("SV-01")
            if not check(line is not None, f"{label}: no order was made for the SV-01 request {no} (setup)", "medium"):
                return None
            assert line is not None
            self.issue_po(str(line["po_no"]))
            c = self.call(asker, "POST", "/api/services/confirm",
                          {"po_no": line["po_no"], "line_no": int(line["line_no"]), "basis": "pct", "value": "100"})  # fmt: skip
            check(c.status_code == 200, f"{label}: confirming the service answered {c.status_code} {c.text[:200]}",
                  "medium")  # fmt: skip
        return str(no), str(line["po_no"]), int(line["line_no"])


def start3(co: Co3, check: b.Checker) -> s2.Stock:
    """A0 / B0 on 23 November 2026: demo reset, then Run store check (spec 3.3), on the platform and in block 2's oracle."""
    with b.on_day(AS_OF):
        r = co.base_state()
        co.work()
    check(r.status_code == 200, f"{co.name}: Run store check answered {r.status_code}: {r.text[:200]}")
    rank = {label: i for i, (label, _, _) in enumerate(co.mr_labels())}
    st = s2.Stock(co.dataset, s2.RIGHT2, rank, today=AS_OF)
    st.store_check(tag="first")
    s2.base_ok(check, co, st)
    return st


def request_no(co: Co3, label: str) -> str:
    found = co.request_of(label)
    return found[0] if found else ""


@contextlib.contextmanager
def day(d: date) -> Iterator[None]:
    with b.on_day(d):
        yield


def copy_dataset(co: Co3, drop: Iterable[str] = ()) -> Path:
    folder = Path(tempfile.mkdtemp(prefix="b3_")) / "dataset"
    shutil.copytree(co.dataset, folder)
    for rel in drop:
        (folder / rel).unlink(missing_ok=True)
    return folder


def tenant_check3(check: b.Checker, mine: Co3, other: Co3) -> None:
    """Spec 9: another company reads none of this company's budget periods, year ends, proposals or forward buys."""
    fin = other.finance()
    status, body = other.periods(fin)
    theirs = {str(p.get("period")): p for p in listed(body, "periods")}
    other_locks = other.lock_rows()
    leaked = [p for p in mine.lock_rows() if p not in other_locks and theirs.get(p, {}).get("status") == "locked"]
    check(status == 200 and not leaked, f"{other.name} sees {mine.name}'s locks {leaked} (answered {status})")
    tables = mine.tables()
    for (fid,) in mine.q("select id from forward_buy order by id")[:3] if "forward_buy" in tables else []:
        if "forward_buy" in other.tables() and other.q("select 1 from forward_buy where id = :i", i=fid):
            continue
        code, _ = other.fb_get(fid)
        check(code == 404, f"{other.name} reading {mine.name}'s forward buy {fid} answered {code}")
    for (pid,) in mine.q("select id from proposal where action like 'budget%' or action like 'forward_buy%'")[:3]:
        if other.q("select 1 from proposal where id = :i", i=pid):
            continue
        r = other.call(other.director(), "POST", f"/api/admin/proposals/{pid}/approve", {})
        check(r.status_code in (403, 404), f"{other.name} approving {mine.name}'s proposal {pid} answered {r.status_code}")
    for no in sorted(mine.requests_now())[:2]:
        if no in other.requests_now():
            continue
        code, _ = other.check_of(no, fin)
        check(code == 404, f"{other.name} checking {mine.name}'s request {no} against the budget answered {code}")


def with_wrong3(**flags: bool) -> Wrong:
    return replace(RIGHT3, **flags)


# ======================================================================================================================
# Scenario steps shared by several evals
# ======================================================================================================================

BG1_PRICES = {"RM-01": Decimal(650), "RM-06": Decimal(38), "RM-08": Decimal(880)}  # spec 3.3 precondition


def expect_groups(
    budgets: Budgets, gl: str, upto: str, department: str, *, hreq: Decimal, requested: Decimal,
    hord: Decimal = Z, spent: Decimal = Z, location: str = CKN,
) -> dict[tuple[str, str], Group]:  # fmt: skip
    """The department group and the location group of one GL account (spec 3.3: both carry the same parts here, since
    every request of A0 is the department's at the one plant)."""
    parts = {"held_by_requests": hreq, "held_by_orders": hord, "spent": spent, "requested": requested}
    return {
        (gl, "department"): budgets.group(gl, "department", upto, budgets.scope(department, location), **parts),
        (gl, "location"): budgets.group(gl, "location", upto, location, **parts),
    }


def compare_groups(
    check: b.Checker, label: str, got: dict[tuple[str, str], dict[str, Any]], want: dict[tuple[str, str], Group],
    fields: Iterable[str] = GROUP_FIELDS,
) -> None:  # fmt: skip
    for key, g in want.items():
        wrong = group_diff(got.get(key), g.as_dict(), fields)
        check(not wrong, f"{label}: budget {key[0]} ({key[1]}): {'; '.join(wrong)}")


def compare_truth(
    check: b.Checker, label: str, co: Co3, scenario: str, as_of: date, request_of: str,
    got: dict[tuple[str, str], dict[str, Any]], want: dict[tuple[str, str], Group], step: str | None = None,
) -> int:  # fmt: skip
    """`truth/truth_budget.csv` rows of this scenario, day and request: the platform must equal them, and the oracle must
    too (a difference there is a spec gap or an oracle bug, medium). The truth file also names the step (a column spec
    3.5 does not list): several BG1 steps share 25 Nov, so a caller that knows its step picks only that step's rows."""
    rows = [r for r in co.truth3("truth_budget", scenario)
            if r.get("as_of", "")[:10] == as_of.isoformat() and r.get("request_of") == request_of
            and (step is None or r.get("step") in (None, "", step))]  # fmt: skip
    for r in rows:
        key = (r.get("gl_code", ""), r.get("level", ""))
        t = truth_group(r)
        wrong = group_diff(got.get(key), t)
        check(not wrong, f"{label}: truth_budget {scenario} {request_of} {key}: {'; '.join(wrong)}")
        if key in want:
            mine = group_diff({k: (str(v) if v is not None else None) for k, v in want[key].as_dict().items()}, t)
            check(not mine, f"{label}: truth_budget {scenario} {key} differs from the oracle: {'; '.join(mine)}",
                  "medium")  # fmt: skip
    return len(rows)


def department_of(co: Co3, no: str) -> str:
    return str(co.one("select department_code from request where no = :n", n=no) or "")


def bg1(co: Co3, check: b.Checker, st: s2.Stock) -> dict[str, dict[str, Any]]:
    """BG1's setup (spec 3.3): the plant head approves MO-1's request; sourcing runs; the buyer changes the RM-08 line to
    a direct buy from SUP-005 at Rs 880 ("Spot price agreed by phone"), the purchase manager agrees; orders are made.
    Returns MO-1's live order lines by item; the precondition (RM-01 50 at 650, RM-06 60 at 38, RM-08 40 at 880 on live
    orders) is checked as setup (medium)."""
    mo1 = request_no(co, "MO-1")
    with day(AS_OF):
        status = co.approve(mo1)
        check(status == "approved", f"BG1 setup: MO-1's request {mo1} is {status} after the approvals", "medium")
        b.cli("source", co.slug, "--request", mo1)
        co.work()
        line = co.line_of(mo1, "RM-08")
        if line is not None:
            r = co.direct(mo1, line, "SUP-005", "880.0000", "Spot price agreed by phone")
            check(r.status_code in (200, 201, 202), f"BG1 setup: the direct route for RM-08 answered {r.status_code} "
                  f"{r.text[:200]}", "medium")  # fmt: skip
        # Spec gap (owner): spec 3.3 puts RM-06 on a SUP-002 contract; the dataset has none, so sourcing sends bearings
        # to quotation. To reach the spec's precondition (RM-06 60 at 38 on a live order) the buyer buys them directly
        # from SUP-002 at Rs 38, agreed like the wire. What is checked is unchanged.
        bearings = co.line_of(mo1, "RM-06")
        if bearings is not None and not co.one(
            "select 1 from sourcing_decision where request_no = :n and line_no = :l and method = 'contract'",
            n=mo1, l=bearings,
        ):  # fmt: skip
            r = co.direct(mo1, bearings, "SUP-002", "38.0000", "Usual bearing supplier at the standard price")
            check(r.status_code in (200, 201, 202), f"BG1 setup: the direct route for RM-06 answered {r.status_code} "
                  f"{r.text[:200]}", "medium")  # fmt: skip
        b.cli("order", co.slug, "--request", mo1)
        co.work()
    lines = co.ordered_lines(mo1)
    for item, price in BG1_PRICES.items():
        got = lines.get(item)
        want_qty = a0_qty(st, "MO-1", item)
        check(got is not None and D(got["qty"]) == want_qty and D(got["price"]) == price,
              f"BG1 precondition: {item} {want_qty} at {price} on a live order, found {got}", "medium")  # fmt: skip
    return lines


def open_value(line: dict[str, Any], actual: Decimal = Z) -> Decimal:
    """Spec 4.1 held by orders for one line: max(0, round((qty - short closed) x price, 2) - actual on the line)."""
    return max(Z, r2((D(line["qty"]) - D(line.get("short_closed"))) * D(line["price"])) - actual)


def gl_of_items(dataset: Path) -> dict[str, str]:
    return {code: r["default_gl"] for code, r in item_rows(dataset).items()}


def held_by(lines: dict[str, dict[str, Any]], gls: dict[str, str], gl: str,
            actual: dict[str, Decimal] | None = None) -> Decimal:  # fmt: skip
    got = actual or {}
    return sum((open_value(ln, got.get(item, Z)) for item, ln in lines.items() if gls.get(item) == gl), Z)


# ======================================================================================================================
# Forward-buy scenarios (spec 3.4): the scenario files, the oracle's state, the platform's record
# ======================================================================================================================

FB1_BENCH = {"item_code": "RM-08", "price": "828.0000", "uom": "KGS", "basis": "delivered", "effective_date": "2026-11-16",
             "region_code": "MH", "source_kind": "index",
             "source_name": "Enamelled copper wire reference rate (mid-month), Pune bulletin",
             "source_ref": "Pune wire bulletin 2026-11 (mid-month)", "note": "synthetic, eval"}  # fmt: skip
SPEC_BENCH = {
    "FB1": FB1_BENCH,
    "FB4": {**FB1_BENCH, "price": "870.0000"},
    "FB7": {"item_code": "OS-03", "price": "790.0000", "uom": "KGS", "basis": "delivered", "effective_date": "2026-11-16",
            "region_code": "KA", "source_kind": "market_survey", "source_name": "Coffee traders survey",
            "source_ref": "Bengaluru coffee survey 2026-11", "note": "synthetic, eval"},
}  # fmt: skip
SPEC_RULES = {  # spec 3.4, as rule rows; the scenario's own rule.csv wins when present
    "FB2": [{"item_code": "RM-08", "location_code": CKN, "max_cover_months": "12", "storage_max_qty": "2500"}],
    "FB3": [{"item_code": "RM-08", "location_code": CKN, "storage_max_qty": "600"}],
    "FB5": [{**COMPANY_V1, "min_net_saving_inr": "44982.36"}],
}


def scenario_bench(co: Co3, check: b.Checker, label: str) -> dict[str, str]:
    rows = co.scenario_rows("forward_buy", label, "benchmark")
    check(bool(rows), f"{co.name}: forward_buy/{label}/benchmark.csv is missing (spec 3.2 step 5); the spec's values "
          "are used", "medium")  # fmt: skip
    return rows[0] if rows else dict(SPEC_BENCH[label])


def scenario_rules(co: Co3, check: b.Checker, label: str) -> list[dict[str, str]]:
    rows = co.scenario_rows("forward_buy", label, "rule")
    check(bool(rows), f"{co.name}: forward_buy/{label}/rule.csv is missing (spec 3.2 step 5); the spec's values are "
          "used", "medium")  # fmt: skip
    return rows or [dict(r) for r in SPEC_RULES[label]]


def scope_key(r: dict[str, str]) -> tuple[str, str, str]:
    return (r.get("category_code") or "", r.get("item_code") or "", r.get("location_code") or "")


class FbState:
    """The oracle's view of one company while an eval adds benchmarks and rule versions to it."""

    def __init__(self, co: Co3, stock: s2.Stock | None) -> None:
        self.co = co
        self.stock = stock
        self.rules = rule_rows(co.dataset)
        self.benches: list[Bench] = []
        self.budgets = Budgets(co.dataset)
        self.items = item_rows(co.dataset)
        self.modes: list[str] = []

    def add_rule(self, row: dict[str, str]) -> str:
        """A new version for the row's scope: the next number after the versions the company already has there."""
        mine = {k: v for k, v in row.items() if v not in (None,)}
        same = [int(r.get("version") or 1) for r in self.rules if scope_key(r) == scope_key(mine)]
        mine["version"] = str(max(same, default=0) + 1)
        mine["effective_from"] = AS_OF.isoformat()
        mine.pop("effective_to", None)
        with b.on_day(AS_OF):
            mode = self.co.add_rule(mine)
        self.rules.append(mine)
        self.modes.append(mode)
        return mode

    def add_bench(self, row: dict[str, str]) -> int | None:
        with b.on_day(AS_OF):
            bid = self.co.add_bench(row)
        if bid is not None:
            self.benches += bench_rows([{**row, "status": "verified"}])
        return bid

    def anchor(self, item: str, location: str) -> date | None:
        """The live line with the latest need-by (spec 5.1); A0's lines are the oracle's."""
        if self.stock is None:
            return None
        dates = [ln.need_by for ln in self.stock.lines if ln.item == item and ln.loc == location and ln.requested() > 0]
        return max(dates) if dates else None

    def anchor_label(self, item: str, location: str) -> str | None:
        when = self.anchor(item, location)
        if self.stock is None or when is None:
            return None
        return max((ln.mr for ln in self.stock.lines if ln.item == item and ln.loc == location and ln.need_by == when),
                   default=None)  # fmt: skip

    def budget_left(self, item: str, location: str) -> Decimal | None:
        """The least (available - requested) of the anchor request's groups for the line's GL account, both levels (spec
        5.2; read as the line's own account, see E90's note)."""
        label = self.anchor_label(item, location)
        if label is None or self.stock is None:
            return None
        gl = self.items[item]["default_gl"]
        others = [m for m in {ln.mr for ln in self.stock.lines} if m != label]
        hreq = sum((a0_values(self.stock, m).get(gl, Z) for m in others), Z)
        req = a0_values(self.stock, label).get(gl, Z)
        dept = "PRD" if self.co.name == "apex" else ""
        groups = expect_groups(self.budgets, gl, month(AS_OF), dept, hreq=hreq, requested=req, location=location)
        lefts = [g.budget * self.budgets.block / HUNDRED - g.committed - g.requested for g in groups.values()
                 if g.budget is not None]  # fmt: skip
        return min(lefts) if lefts else None

    def want(self, item: str, location: str, *, anchored: bool = True, wrong: Wrong = RIGHT3) -> Result:
        f = facts_for(self.co.dataset, self.stock, item, location, AS_OF, extra_benches=self.benches,
                      anchor_need_by=self.anchor(item, location) if anchored else None,
                      budget_left=self.budget_left(item, location) if anchored else None)  # fmt: skip
        rule = rule_for(self.rules, item, self.items[item]["category_code"], location, AS_OF)
        return evaluate(f, rule, wrong)


def record_of(co: Co3, check: b.Checker, label: str, item: str, location: str) -> dict[str, Any] | None:
    """The one live record for the item and location, read through the API (spec 5.6)."""
    live = co.live(item, location)
    check(len(live) <= 1, f"{label}: {len(live)} live forward-buy records for {item}@{location} (one at most)")
    if not live:
        return None
    code, rec = co.fb_get(live[-1]["id"])
    check(code == 200, f"{label}: GET /api/forward-buys/{live[-1]['id']} answered {code}")
    return {**rec, "_id": live[-1]["id"]} if rec else None


def check_record(check: b.Checker, label: str, co: Co3, state: FbState, item: str, location: str,
                 truth: str | None = None, request_label: str | None = None) -> dict[str, Any] | None:  # fmt: skip
    """The live record (or its absence) against the oracle, and the oracle against the truth file."""
    want = state.want(item, location)
    rec = record_of(co, check, label, item, location)
    if want.recommended:
        if check(rec is not None, f"{label}: no live record for {item}@{location}; the oracle recommends {want.qty} "
                 f"(net {want.net_saving})"):  # fmt: skip
            assert rec is not None
            wrong = fb_diff(rec, want)
            check(not wrong, f"{label}: the record differs from the oracle: {'; '.join(wrong)}")
            if request_label:
                no = request_no(co, request_label)
                check(rec.get("request_no") == no and int(rec.get("line_no") or 0) == (co.line_of(no, item) or -1),
                      f"{label}: anchored on {rec.get('request_no')}/{rec.get('line_no')}, expected {no} line "
                      f"{co.line_of(no, item)}")  # fmt: skip
            for f in ("qty", "price_now", "trend_now"):
                check(re.fullmatch(r"-?\d+\.\d{4}", str(rec.get(f))) is not None,
                      f"{label}: {f} {rec.get(f)!r} is not a string with 4 places (spec 5.6)", "low")  # fmt: skip
            for f in ("net_saving", "value"):
                check(re.fullmatch(r"-?\d+\.\d{2}", str(rec.get(f))) is not None,
                      f"{label}: {f} {rec.get(f)!r} is not a string with 2 places (spec 5.6)", "low")  # fmt: skip
    else:
        check(rec is None, f"{label}: a live record {rec and rec.get('_id')} for {item}@{location}; the oracle says "
              f"{want.reason}")  # fmt: skip
    if truth:
        rows = co.truth3("truth_forward_buy", truth)
        wrong = oracle_vs_truth(want, truth_fb(rows))
        check(not wrong, f"{label}: truth_forward_buy {truth} against the spec's rules: {'; '.join(wrong)}", "medium")
    return rec


def check_preview(check: b.Checker, label: str, co: Co3, state: FbState, item: str, location: str,
                  anchored: bool = False) -> dict[str, Any]:  # fmt: skip
    """A preview writes nothing and says what the engine says (spec 5.6)."""
    before = (len(co.fb_rows(item, location)), len(co.evaluations()), co.last_audit_id())
    req = line = None
    if anchored:
        label_ = state.anchor_label(item, location)
        req = request_no(co, label_) if label_ else None
        line = co.line_of(req, item) if req else None
    with b.on_day(AS_OF):
        code, body = co.preview(item, location, request_no=req, line_no=line)
    check(code == 200, f"{label}: preview {item}@{location} answered {code}: {str(body)[:200]}")
    want = state.want(item, location, anchored=anchored)
    wrong = fb_diff(body, want, months=want.recommended)
    check(not wrong, f"{label}: preview {item}@{location}: {'; '.join(wrong)}")
    after = (len(co.fb_rows(item, location)), len(co.evaluations()), co.last_audit_id())
    check(after == before, f"{label}: the preview wrote something (records, evaluations, audit) {before} -> {after}")
    return body
