"""What a demo company plans to need over its history, before any buying: production orders from sales orders and the
demand plan, joiner plans, requests for replacements, services by month, split orders and breakdowns.

Everything here comes from the profile and the seed alone, never from stock or suppliers, so the simulation in
history.py can be run again on the same plan.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from common import Calendar, Rng, add_months, month_start

QTY = Decimal("0.001")


def production_lines(bom: list[dict[str, str]], orders: list[dict[str, str]]) -> list[dict[str, str]]:
    """One line per material of each production order: the quantity per product times the products to make."""
    per_product: dict[str, list[dict[str, str]]] = defaultdict(list)
    for b in bom:
        per_product[b["product_code"]].append(b)
    return [
        {
            "order_no": o["order_no"],
            "item_code": b["item_code"],
            "qty": f"{(Decimal(b['qty']) * Decimal(o['qty'])).quantize(QTY)}",
            "unit": b["unit"],
            "need_by": o["need_by"],
        }
        for o in orders
        for b in per_product[o["product_code"]]
    ]


@dataclass
class MaterialRequest:
    """Materials asked for from the store for one purpose: stock first, a requisition for what is short."""

    no: str
    source: str  # the production order, joiner plan or person it is for
    department: str
    raised: date
    need_by: date
    lines: list[tuple[str, Decimal]]  # item, quantity


@dataclass
class Plan:
    material_requests: list[MaterialRequest] = field(default_factory=list)
    services: list[tuple[date, str, Decimal, Decimal]] = field(default_factory=list)  # day, item, qty, price share
    splits: list[tuple[date, str, Decimal]] = field(default_factory=list)
    emergencies: list[tuple[date, str, Decimal]] = field(default_factory=list)
    files: dict[str, list[dict[str, str]]] = field(default_factory=dict)  # inputs/history/*.csv


def _months(start: date, cutoff: date) -> list[date]:
    out, m = [], month_start(start)
    while m <= cutoff:
        out.append(m)
        m = add_months(m, 1)
    return out


def _monday(month: date, n: int) -> date:
    d = month + timedelta(days=(0 - month.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


class PlanMaker:
    """Turns the history settings of a profile into a Plan."""

    def __init__(self, profile: Any, cal: Calendar, loc: str, rng: Rng) -> None:
        self.p, self.h, self.cal, self.loc, self.R = profile, profile["history"], cal, loc, rng
        self.start, self.cutoff = date.fromisoformat(self.h["start"]), date.fromisoformat(self.h["cutoff"])
        self.plan = Plan()

    def make(self) -> Plan:
        if "production" in self.h:
            self.production()
        if "joiners" in self.h:
            self.joiners()
        if "peripherals" in self.h:
            self.peripherals()
        self.service_months()
        for s in self.h.get("split_orders", []):
            self.plan.splits.append((date.fromisoformat(s["day"]), s["item"], Decimal(s["qty"])))
        self.breakdowns()
        self.plan.material_requests.sort(key=lambda m: (m.raised, m.no))
        return self.plan

    def _work(self, d: date) -> date:
        return self.cal.next_working(d, self.loc)

    def _mr(self, source: str, department: str, raised: date, need_by: date, lines: list[tuple[str, Decimal]]) -> None:
        if raised < self.start:
            return
        month = sum(
            1 for m in self.plan.material_requests if (m.raised.year, m.raised.month) == (raised.year, raised.month)
        )
        no = f"MR-{raised:%y%m}-{month + 1:02d}"
        self.plan.material_requests.append(MaterialRequest(no, source, department, raised, need_by, lines))

    def production(self) -> None:
        cfg, inputs = self.h["production"], self.p["inputs"]
        bom = [dict(zip(inputs["bom"]["columns"], (str(v) for v in r), strict=True)) for r in inputs["bom"]["rows"]]
        orders, sales, plans = [], [], []
        last = date.fromisoformat(cfg["last_start"])
        for month in _months(self.start, self.cutoff):
            for product, (lo, hi) in cfg["orders_per_month"].items():
                planned = 0
                for _ in range(self.R.randint(lo, hi)):
                    start = self._work(month + timedelta(days=self.R.randint(2, 26)))
                    if start > last or start.month != month.month:
                        continue
                    qty = self.R.randint(*cfg["qty"][product]) // 5 * 5
                    no = f"MO-{start:%y%m}-{len(orders) + 1:03d}"
                    if self.R.bern(cfg["sales_share"]):
                        reason = f"SO-{2000 + len(sales) + 1}"
                        need = self._work(start + timedelta(days=self.R.randint(5, 10)))
                        sales.append([reason, self.R.choice(cfg["customers"]), product, str(qty), need.isoformat()])
                    else:
                        reason = f"DP-{month:%Y-%m}"
                        planned += qty
                    orders.append({"order_no": no, "product_code": product, "qty": str(qty),
                                   "need_by": start.isoformat(), "reason": reason})  # fmt: skip
                if planned:
                    plans.append([f"DP-{month:%Y-%m}", product, str(planned), f"{month:%Y-%m}", "approved"])
        orders.sort(key=lambda o: o["need_by"])
        lines = production_lines(bom, orders)
        for o in orders:
            start = date.fromisoformat(o["need_by"])
            raised = self.cal.add_working(start, -self.R.randint(*cfg["release_days"]), self.loc)
            mine = [(ln["item_code"], Decimal(ln["qty"])) for ln in lines if ln["order_no"] == o["order_no"]]
            self._mr(o["order_no"], "PRD", raised, start, mine)
            o["raised"] = raised.isoformat()
        kept = {m.source for m in self.plan.material_requests}
        orders = [o for o in orders if o["order_no"] in kept]
        self.plan.files["sales_order"] = _table(self.p, "sales_order", sales, "delivered")
        self.plan.files["demand_plan"] = [
            dict(zip(["plan_no", "product_code", "qty", "period", "status"], r, strict=True)) for r in plans
        ]
        self.plan.files["production_order"] = [{**{k: o[k] for k in ("order_no", "product_code", "qty", "need_by", "reason")},
                                                "status": "completed"} for o in orders]  # fmt: skip
        self.plan.files["production_order_line"] = [
            {**ln, "status": "issued"} for ln in lines if ln["order_no"] in {o["order_no"] for o in orders}
        ]

    def joiners(self) -> None:
        cfg, inputs = self.h["joiners"], self.p["inputs"]
        kits: dict[str, list[tuple[str, Decimal]]] = defaultdict(list)
        for kit, item, qty in inputs["joiner_kit"]["rows"]:
            kits[kit].append((item, Decimal(qty)))
        last, rows = date.fromisoformat(cfg["last_start"]), []
        for month in _months(self.start, self.cutoff):
            plan_no = f"JP-{month:%Y-%m}"
            for dept, (lo, hi) in cfg["per_month"].items():
                count = self.R.randint(lo, hi)
                intakes = cfg["intakes"]
                shares = [count // len(intakes) + (1 if i < count % len(intakes) else 0) for i in range(len(intakes))]
                for n, share in zip(intakes, shares, strict=True):
                    start = self._work(_monday(month, n))
                    if not share or start > last:
                        continue
                    kit = cfg["kit"][dept]
                    rows.append({"plan_no": plan_no, "department": dept, "kit": kit, "count": str(share),
                                 "start_date": start.isoformat(), "status": "joined"})  # fmt: skip
                    raised = self.cal.add_working(start, -self.R.randint(*cfg["notice_days"]), self.loc)
                    self._mr(f"{plan_no}/{dept}", dept, raised, start, [(i, q * share) for i, q in kits[kit]])
        kept = {m.source for m in self.plan.material_requests}
        self.plan.files["joiner_plan"] = [r for r in rows if f"{r['plan_no']}/{r['department']}" in kept]

    def peripherals(self) -> None:
        dept = self.p["replenishment_department"]
        for month in _months(self.start, self.cutoff):
            for item, (lo, hi) in self.h["peripherals"].items():
                for _ in range(self.R.randint(lo, hi)):
                    raised = self._work(month + timedelta(days=self.R.randint(0, 27)))
                    if raised.month == month.month and raised <= self.cutoff - timedelta(days=21):
                        need = self.cal.add_working(raised, 3, self.loc)
                        self._mr("replacement", dept, raised, need, [(item, Decimal(1))])

    def service_months(self) -> None:
        for s in self.h.get("services", []):
            for month in _months(self.start, self.cutoff):
                if s["months"] != "all" and f"{month:%Y-%m}" not in s["months"]:
                    continue
                share = Decimal(str(round(self.R.uniform(*s["usage"]), 2))) if "usage" in s else Decimal(1)
                day = self._work(month + timedelta(days=s.get("day", 1) - 1))
                self.plan.services.append((day, s["item"], Decimal(str(s.get("qty", 1))), share))
        self.plan.services.sort()

    def breakdowns(self) -> None:
        cfg = self.h.get("emergency")
        if not cfg:
            return
        span = (self.cutoff - self.start).days - 30
        for k in range(cfg["count"]):
            day = self._work(self.start + timedelta(days=15 + span * k // cfg["count"] + self.R.randint(0, 20)))
            self.plan.emergencies.append((day, self.R.choice(cfg["items"]), Decimal(self.R.randint(*cfg["qty"]))))


def _table(profile: Any, name: str, rows: list[list[str]], status: str) -> list[dict[str, str]]:
    columns = profile["inputs"][name]["columns"]
    return [{**dict(zip(columns, r, strict=True)), "status": status} for r in rows]
