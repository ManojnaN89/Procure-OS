"""Twelve months of a demo company's buying, day by day, from its profile: material requests and stock, requisitions and
approvals, the buying route, RFQs and awards, purchase orders, supplier confirmations, deliveries, inspection and
service entries (the buying half is in history_buying.py). Invoices, payments, the cutoff and the answer keys are in
history_money.py and history_close.py; the checks in history_check.py.

The rules (decisions D36 and D37): stock first, a requisition only for what is short; approval by the value bands of
the company's rules; the buying route in order (valid contract with money left, single source, emergency, safe reorder,
else an RFQ with two quotes up to the threshold and three above, the value counted per item over 30 days). The history
ends at the stock the profile gives as opening stock, so the company's demo starts where its history stops.

The generator is an independent oracle: it works out tax, totals and dates itself and never imports the platform.
"""

from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from common import Calendar, D, Numbering, daterange, iso, money, months_between, q2, q3, qty_s, sub_rng, ts, dt_at
from history_buying import ZERO, Buying, Need
from history_plan import MaterialRequest, PlanMaker
from master_files import read, tolerance_on


WEEKDAYS = {"Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3, "Fri": 4, "Sat": 5, "Sun": 6}


class Infeasible(Exception):
    """This run of the history does not end at the profile's stock; the next attempt draws again."""


class History(Buying):
    """One run of a company's history. `run()` fills `tables` (rows by file name, each with its time in `_t`)."""

    def __init__(self, profile: Any, out: Path, attempt: int = 0) -> None:
        self.p, self.h = profile, profile["history"]
        self.start, self.cutoff = date.fromisoformat(self.h["start"]), date.fromisoformat(self.h["cutoff"])
        seed = f"{profile.name}:{self.h['seed']}:{attempt}"
        self.R = sub_rng(seed, "operations")
        m = {p.stem: read(p) for p in (out / "master").glob("*.csv")}
        self.m = m
        self.loc = m["location"][0]["code"]
        self.state = m["location"][0]["state_code"]
        self.cal = Calendar(m["location"])
        self.num = Numbering(m["number_series"], {self.loc: m["location"][0]["loc_short"]})
        self.items = {r["code"]: r for r in m["item"]}
        self.sup = {r["code"]: r for r in m["supplier"]}
        self.cats = {r["code"]: r for r in m["category"]}
        self.plan_of = {r["item_code"]: r for r in m["item_location"]}
        self.target = {r["item_code"]: D(r["qty"]) for r in m["stock_opening"]}  # the stock at the end of history
        self.setting = {r["key"]: r["value"] for r in m["setting"]}
        self.people = self.h["people"]
        self.route = self.h["route"]
        self.behaviour = self.h["suppliers"]
        self.buying_days = {WEEKDAYS[d] for d in self.h["buying_days"]}
        self.tables: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.drafts: list[dict[str, Any]] = []  # receipts and service entries an invoice will follow
        self.on_hand: dict[str, Decimal] = defaultdict(Decimal)
        self.pipeline: dict[str, Decimal] = defaultdict(Decimal)  # requisitioned or ordered, not yet accepted
        self.claimed: dict[str, Decimal] = defaultdict(Decimal)  # promised to material requests, not yet issued
        self.waiting: list[Need] = []
        self.accept: dict[date, list[tuple[str, Decimal, Decimal, str, str]]] = defaultdict(list)  # stock to take in
        self.issues: list[tuple[MaterialRequest, int, str, Decimal]] = []
        self.deliveries: dict[str, list[tuple[date, bool]]] = defaultdict(list)  # supplier: (arrived, on time)
        self.bought: dict[str, list[tuple[date, str, Decimal]]] = defaultdict(list)  # item: (ordered, supplier, price)
        self.requested: dict[str, list[tuple[date, Decimal]]] = defaultdict(list)  # item: (raised, value)
        self.contract_used: dict[str, Decimal] = defaultdict(Decimal)
        self.cost: dict[str, Decimal] = {}  # the last price an item was received at
        self.mr_lines: dict[tuple[str, int], dict[str, Any]] = {}
        self.approved_at: dict[str, datetime] = {}
        self.cases: list[dict[str, Any]] = []
        self.planned = PlanMaker(profile, self.cal, self.loc, sub_rng(seed, "plan")).make()
        # planned materials are bought until buy_by; the last request before it buys for the closing stock and for
        # the requests still to come, which are then served from stock
        self.buy_by = date.fromisoformat(self.h["planned_buy_by"])
        self.last_mr: dict[str, str] = {}
        self.later_needs: dict[str, Decimal] = defaultdict(Decimal)
        for mr in self.planned.material_requests:
            for item, qty in mr.lines:
                if mr.raised <= self.buy_by:
                    self.last_mr[item] = mr.no
                else:
                    self.later_needs[item] += qty
        self.protected = set(self.h.get("closed_at_cutoff", [])) | set(self.last_mr)
        self.last_day = self.cal.prev_working(self.cutoff, self.loc)
        self.clip_day = self.cal.add_working(self.last_day, -3, self.loc)
        self.prices = self._end_prices()
        self.contracts = [c for c in m["contract"] if c["status"] == "active"]
        self.contract_price = {
            (r["contract_code"], r["item_code"]): D(r["price"])
            for r in m["price_record"]
            if r["contract_code"] and r["status"] == "active"  # a draft version is not in force until it is activated
        }
        self.lead = {(r["supplier_code"], r["item_code"]): int(r["lead_days"]) for r in m["price_record"]}
        count = self.h["planted"].get("receipt_without_po", 0)  # every other breakdown, from the second
        self.without_po = {e[0] for e in self.planned.emergencies[1::2][:count]}

    # ---- small helpers ----------------------------------------------------------------------------

    def add(self, table: str, row: dict[str, Any], when: datetime) -> dict[str, Any]:
        row["_t"] = when
        self.tables[table].append(row)
        return row

    def case(self, kind: str, documents: str, cause: str, expected: str, on: date) -> None:
        self.cases.append({"type": kind, "documents": documents, "cause": cause, "expected_result": expected,
                           "planted_on": iso(on), "_t": dt_at(on, 18)})  # fmt: skip

    def working(self, d: date) -> date:
        return self.cal.next_working(d, self.loc)

    def plus(self, d: date, days: int) -> date:
        return self.cal.add_working(d, days, self.loc)

    def later(self, when: datetime, hours: float) -> datetime:
        """Working hours later, in a 09:00 to 18:00 working day."""
        d, hour = when.date(), max(when.hour + when.minute / 60, 9.0)
        if not self.cal.is_working(d, self.loc) or hour >= 18:
            d, hour = self.plus(d, 1) if self.cal.is_working(d, self.loc) else self.working(d), 9.0
        hour = round((hour + hours) * 4) / 4
        while hour > 18:
            hour -= 9
            d = self.plus(d, 1)
        return dt_at(d, hour)

    def tolerance(self, day: date, key: str, category: str | None = None) -> tuple[int, Decimal]:
        """A tolerance in force on a day, and its version."""
        version, values = tolerance_on(self.m["tolerance"], self.m["tolerance_version"], day.isoformat(), category)
        return version, values[key]

    def rate_on(self, item: dict[str, str], d: date) -> Decimal:
        change = item["gst_rate_change_date"]
        return D(item["gst_rate_after"] if not change or d.isoformat() >= change else item["gst_rate_before"])

    def cost_centre(self, item: str, department: str | None = None) -> str:
        if department:
            for c in self.m["cost_centre"]:
                if c["department_code"] == department:
                    return c["code"]
        return str(self.h["cost_centre"][self.items[item]["category_code"]])

    def department_of(self, cc: str) -> str:
        return next(c["department_code"] for c in self.m["cost_centre"] if c["code"] == cc)

    # ---- prices -----------------------------------------------------------------------------------

    def _end_prices(self) -> dict[tuple[str, str], Decimal]:
        """Each supplier's price for each item at the end of history: the price records, and the quoters' shares."""
        trading = {code for code, s in self.sup.items() if s["status"] == "active"}  # never a prospect or a blocked one
        prices = {
            (r["supplier_code"], r["item_code"]): D(r["price"])
            for r in self.m["price_record"]
            if r["supplier_code"] in trading
        }
        for item, quoters in self.h.get("quoters", {}).items():
            for supplier, share in quoters.items():
                if supplier in trading:
                    prices.setdefault((supplier, item), q2(D(self.items[item]["standard_price"]) * D(share)))
        return prices

    def pool(self, item: str) -> list[str]:
        return sorted(s for s, i in self.prices if i == item)

    def list_price(self, supplier: str, item: str, d: date) -> Decimal:
        quarters = months_between(d, self.cutoff) // 3
        rise = D(self.h["price_rise_pct_per_quarter"]) * quarters / 100
        return q2(self.prices[(supplier, item)] * (1 - rise))

    def contract_for(self, item: str, d: date, qty: Decimal) -> dict[str, str] | None:
        """A contract valid on the day that prices the item and has the money left for this quantity."""
        for c in self.contracts:
            price = self.contract_price.get((c["code"], item))
            if price and c["valid_from"] <= d.isoformat() <= c["valid_to"]:
                if self.contract_used[c["code"]] + q2(price * qty) <= D(c["max_value"]):
                    return c
        return None

    # ---- the day loop -----------------------------------------------------------------------------

    def run(self) -> "History":
        self.opening = self._opening()
        for item, qty in self.opening.items():
            self.on_hand[item] = qty
        mrs = defaultdict(list)
        for mr in self.planned.material_requests:
            mrs[mr.raised].append(mr)
        by_day: dict[date, list[tuple[str, tuple[Any, ...]]]] = defaultdict(list)
        for day, item, qty, share in self.planned.services:
            by_day[day].append(("service", (item, qty, share)))
        for day, item, qty in self.planned.splits:
            by_day[self.working(day)].append(("split", (item, qty)))
        for day, item, qty in self.planned.emergencies:
            by_day[day].append(("emergency", (item, qty)))
        for day in daterange(self.start, self.cutoff):
            if not self.cal.is_working(day, self.loc):
                continue
            for item, qty, cost, ref, use in self.accept.pop(day, []):
                self.cost[item] = cost
                self.move(day, item, "receipt", qty, cost, "", ref)
                self.pipeline[item] -= qty
                if use:  # bought for a breakdown: issued as it arrives
                    self.move(day, item, "issue", qty, cost, self.cost_centre(item), use)
            self.review(day)
            for kind, args in by_day.get(day, []):
                getattr(self, kind)(day, *args)
            for mr in mrs.get(day, []):
                self.material_request(mr)
            self.issue_planned(day)
            self.consume(day)
            if day == self.last_day:
                self.land(day)
            if day.weekday() in self.buying_days:
                self.buy(day)
        return self

    def _opening(self) -> dict[str, Decimal]:
        """The stock on the first day: a little for planned materials, a full shelf for reorder-point items."""
        out = {}
        for item in self.target:
            plan = self.plan_of[item]
            if plan["reorder_policy"] == "minmax":
                out[item] = D(self.R.randint(int(D(plan["reorder_point"])) + 1, int(D(plan["max_qty"]))))
            else:
                needs = [q for mr in self.planned.material_requests for i, q in mr.lines if i == item]
                out[item] = q3(D(round(self.R.uniform(0, 0.8) * float(needs[0])))) if needs else self.target[item]
        return out

    def move(self, day: date, item: str, kind: str, qty: Decimal, cost: Decimal, cc: str, ref: str) -> None:
        self.on_hand[item] += qty if kind == "receipt" else -qty
        if self.on_hand[item] < 0:
            raise Infeasible(f"{item} below zero on {day}")
        self.add("stock_movement", {"id": "", "item_code": item, "location_code": self.loc, "date": iso(day), "type": kind,
                                    "qty": qty_s(qty), "unit_cost": money(cost), "cost_centre_code": cc, "ref_doc": ref},
                 dt_at(day, 8 if kind == "receipt" else 12))  # fmt: skip

    # ---- demand -----------------------------------------------------------------------------------

    def review(self, day: date) -> None:
        """The morning stock check: an item at or below its reorder point is ordered up to its maximum."""
        for item, plan in self.plan_of.items():
            if plan["reorder_policy"] != "minmax":
                continue
            position = self.on_hand[item] + self.pipeline[item]
            if position <= D(plan["reorder_point"]):
                qty = D(plan["max_qty"]) - position
                self.requisition(
                    dt_at(day, 9.5), [(item, qty)], "Stock at or below its reorder point", channel="planning"
                )

    def consume(self, day: date) -> None:
        landing = day > self.cutoff - timedelta(days=15)
        for item, cfg in self.h.get("consumption", {}).items():
            if not self.R.bern(cfg["chance"]):
                continue
            qty = min(D(self.R.randint(*cfg["qty"])), self.on_hand[item])
            if landing:
                qty = min(qty, max(self.on_hand[item] - self.target[item], ZERO))
            if qty > 0:
                self.move(day, item, "issue", qty, self.last_cost(item), self.cost_centre(item), "")

    def land(self, day: date) -> None:
        """The last working day: what is left of the reorder-point items beyond the closing stock goes to the floor."""
        for item, plan in self.plan_of.items():
            extra = self.on_hand[item] - self.target[item]
            if extra < 0 or (extra > 0 and plan["reorder_policy"] != "minmax"):
                raise Infeasible(f"{item} ends at {self.on_hand[item]}, not {self.target[item]}")
            if extra > 0:
                self.move(day, item, "issue", extra, self.last_cost(item), self.cost_centre(item), "month-end issue")

    def last_cost(self, item: str) -> Decimal:
        return self.cost.get(item) or D(self.items[item]["standard_price"])

    def material_request(self, mr: MaterialRequest) -> None:
        """Stock first: reserve what is there (on hand and coming, less what earlier requests hold), ask for the rest."""
        short_lines, keep = [], []
        for n, (item, need) in enumerate(mr.lines, 1):
            stocked = self.items[item]["stocked"] == "true"
            free = self.on_hand[item] + self.pipeline[item] - self.claimed[item] if stocked else ZERO
            reserved = min(need, max(free, ZERO))
            short = need - reserved
            row = {"mr_no": mr.no, "line_no": n, "item_code": item, "qty": qty_s(need), "unit": self.items[item]["base_uom"],
                   "on_hand": qty_s(self.on_hand[item]) if stocked else "0.000",
                   "on_order": qty_s(self.pipeline[item]) if stocked else "0.000",
                   "held_for_earlier": qty_s(self.claimed[item]) if stocked else "0.000",
                   "reserved": qty_s(reserved), "short": qty_s(short), "request_no": "", "request_line_no": "",
                   "issued_on": "", "status": "open"}  # fmt: skip
            self.mr_lines[(mr.no, n)] = row
            if stocked:
                self.claimed[item] += need
                self.issues.append((mr, n, item, need))
            if short > 0:
                if stocked and mr.raised > self.buy_by:
                    raise Infeasible(f"{item} short after the last day to buy it")
                short_lines.append((item, short))
                keep.append(row)
            if self.last_mr.get(item) == mr.no and stocked:
                top_up = self.target[item] + self.later_needs[item] - (free + short - need)
                if top_up < 0:
                    raise Infeasible(f"{item} would end above its closing stock")
                if top_up > 0:
                    when = dt_at(mr.raised, 11)
                    self.requisition(when, [(item, top_up)], "Stock for the orders to come", department=mr.department)
        if short_lines:
            when = dt_at(mr.raised, 10)
            req = self.requisition(when, short_lines, f"Short for {mr.source}", source=mr.no,
                                   department=mr.department, need_by=mr.need_by, joiner="/" in mr.source)  # fmt: skip
            for n, row in enumerate(keep, 1):
                row["request_no"], row["request_line_no"] = req, n
        self.tables["material_request"].append(
            {"mr_no": mr.no, "source": mr.source, "department": mr.department, "requested_by": self.people["requester"],
             "raised_on": iso(mr.raised), "need_by": iso(mr.need_by), "status": "issued", "_t": dt_at(mr.raised, 10)}
        )  # fmt: skip

    def issue_planned(self, day: date) -> None:
        """Material requests take their stock on or after the day they need it, in order, once all of it is there."""
        waiting, done_items = [], set()
        for mr, n, item, need in sorted(self.issues, key=lambda x: (x[0].need_by, x[0].raised, x[0].no)):
            if day >= mr.need_by and item not in done_items and self.on_hand[item] >= need:
                cc = self.cost_centre(item, mr.department if "/" in mr.source else None)
                self.move(day, item, "issue", need, self.last_cost(item), cc, mr.no)
                self.claimed[item] -= need
                self.mr_lines[(mr.no, n)].update(issued_on=iso(day), status="issued")
            else:
                done_items.add(item)  # a later request waits for the earlier one
                waiting.append((mr, n, item, need))
        self.issues = waiting

    def service(self, day: date, item: str, qty: Decimal, share: Decimal) -> None:
        self.requisition(dt_at(day, 10), [(item, qty)], f"{self.items[item]['name']} for {day:%B %Y}", share=share)

    def split(self, day: date, item: str, qty: Decimal) -> None:
        first = self.requisition(dt_at(day, 10), [(item, qty)], "Stock for the coming orders")
        second = self.requisition(dt_at(self.plus(day, 1), 10.5), [(item, qty)], "Stock for the coming orders")
        limit = D(self.route["two_quotes_up_to"])
        self.case("split_order", f"{first};{second}",
                  f"One need cut into two requisitions a day apart, each under the Rs {limit:,.0f} RFQ threshold",
                  "Flagged: the item's value over 30 days is above the threshold, so the RFQ asks for three quotes", day)  # fmt: skip

    def emergency(self, day: date, item: str, qty: Decimal) -> None:
        """A breakdown: bought at once from the usual supplier and issued as it arrives."""
        without_po = day in self.without_po
        req = self.requisition(dt_at(day, 11 if without_po else 9.25), [(item, qty)], "Breakdown: needed today",
                               priority="urgent")  # fmt: skip
        need = next(n for n in self.waiting if n.request_no == req)
        self.waiting.remove(need)
        supplier = min(self.pool(item), key=lambda s: self.list_price(s, item, day))
        when = max(need.approved, dt_at(day, 15 if without_po else 10.5))
        self.order(when, supplier, [need], "emergency", gate=dt_at(day, 9.5) if without_po else None)
        if without_po:
            grn, po = self.tables["goods_receipt"][-1]["no"], self.tables["po"][-1]["no"]
            self.case("receipt_without_po", f"{grn};{po}", "Goods taken in for a breakdown before any order existed",
                      "The receipt is matched to the order raised afterwards; flagged as a receipt without an order", day)  # fmt: skip

    # ---- requisitions and approval ----------------------------------------------------------------

    def requisition(self, when: datetime, lines: list[tuple[str, Decimal]], why: str, *, source: str = "",
                    department: str | None = None, need_by: date | None = None, joiner: bool = False,
                    priority: str = "normal", share: Decimal = Decimal(1), channel: str = "form") -> str:  # fmt: skip
        no = self.num.next("request", when.date(), self.loc)
        first = self.items[lines[0][0]]
        cc = self.cost_centre(first["code"], department if joiner else None)
        need_by = need_by or self.plus(when.date(), max(int(first["default_lead_days"]) + 3, 5))
        total = ZERO
        kinds = {self.items[i]["kind"] for i, _ in lines}
        for n, (item, qty) in enumerate(lines, 1):
            it = self.items[item]
            est = q2(D(it["standard_price"]) * share)
            line_cc = self.cost_centre(item, department if joiner else None)
            total += q2(est * qty)
            self.add("request_line", {"request_no": no, "line_no": n, "item_code": item, "free_text": "", "attributes": "{}",
                                      "qty": qty_s(qty), "uom": it["base_uom"], "est_price": money(est), "need_by": iso(need_by),
                                      "delivery_location": self.loc, "cost_centre_code": line_cc, "gl_code": it["default_gl"],
                                      "category_code": it["category_code"], "project_code": "", "status": "open"}, when)  # fmt: skip
            self.requested[item].append((when.date(), q2(est * qty)))
            if it["stocked"] == "true":
                self.pipeline[item] += qty
        dept = self.department_of(cc)
        self.add("request", {"no": no, "requester": self.people["requester"], "on_behalf_of": "", "department_code": dept,
                             "ship_to_location": self.loc, "type": "service" if kinds == {"service"} else "goods",
                             "priority": priority, "project_code": "", "created_at": ts(when), "need_by": iso(need_by),
                             "channel": channel, "source_ref": source, "justification": why, "preferred_supplier": "",
                             "status": "approved", "total_value": money(total)}, when)  # fmt: skip
        cats = {self.items[i]["category_code"] for i, _ in lines}
        approved = self.approve("request", no, total, when, dept, next(iter(cats)) if len(cats) == 1 else None,
                                fast=priority == "urgent")  # fmt: skip
        self.approved_at[no] = approved
        for n, (item, qty) in enumerate(lines, 1):
            est = q2(D(self.items[item]["standard_price"]) * share)
            line_cc = self.cost_centre(item, department if joiner else None)
            self.waiting.append(Need(no, n, item, qty, q2(est * qty), approved, need_by, priority, line_cc, share))
        return no

    def rule(self, document: str, value: Decimal, category: str | None) -> dict[str, str]:
        rules = [r for r in self.m["approval_rule"] if r["document"] == document
                 and D(r["min_value"]) <= value <= D(r["max_value"])
                 and not r["approver_chain"].startswith("+") and r["location_code"] == "ANY"]  # fmt: skip
        mine = [r for r in rules if r["category_code"] == category]
        return (mine or [r for r in rules if r["category_code"] == "ANY"])[0]

    def chain(self, rule: dict[str, str], department: str) -> list[str]:
        """The people a chain of roles names, the same person twice in a row once, never the requester."""
        roles = {r: (s, v) for r, s, v in self.p.approver_roles}
        people: list[str] = []
        for role in rule["approver_chain"].split(">"):
            if role == "auto":
                continue
            source, value = roles[role]
            if source == "designation":
                person = next(u["code"] for u in self.m["user"] if u["designation"] == value)
            elif source == "location_head":
                person = self.m["location"][0]["head_user"]
            else:
                person = next(d["head_user"] for d in self.m["department"] if d["code"] == department)
            if person != self.people["requester"] and (not people or people[-1] != person):
                people.append(person)
        return people

    def approve(self, document: str, no: str, value: Decimal, when: datetime, department: str,
                category: str | None = None, fast: bool = False) -> datetime:  # fmt: skip
        """Approval events by the value bands; a slow approver is reminded after the reminder hours."""
        rule = self.rule(document, value, category)
        people = self.chain(rule, department)
        if not people:
            self.add("approval_event", {"document": document, "document_no": no, "step": 1, "approver": "SYSTEM",
                                        "on_behalf_of": "", "rule_code": rule["rule_code"], "action": "auto_approved",
                                        "at": ts(when), "comment": "Within auto-approval limit"}, when)  # fmt: skip
            return when
        remind = float(self.setting.get("approval_reminder_hours", "24")) * 9 / 24
        at = when
        for step, person in enumerate(people, 1):
            slow = not fast and self.R.bern(0.15)
            hours = (
                self.R.uniform(0.25, 1)
                if fast
                else self.R.uniform(remind + 2, remind * 2)
                if slow
                else self.R.uniform(0.5, remind - 1)
            )
            if slow:
                reminded = self.later(at, remind)
                self.add("approval_event", {"document": document, "document_no": no, "step": step, "approver": person,
                                            "on_behalf_of": "", "rule_code": rule["rule_code"], "action": "reminded",
                                            "at": ts(reminded), "comment": "Reminder sent"}, reminded)  # fmt: skip
            at = self.later(at, hours)
            self.add("approval_event", {"document": document, "document_no": no, "step": step, "approver": person,
                                        "on_behalf_of": "", "rule_code": rule["rule_code"], "action": "approved",
                                        "at": ts(at), "comment": ""}, at)  # fmt: skip
        return at


def simulate(profile: Any, out: Path, attempts: int = 60) -> History:
    """The first run of the history, attempt by attempt, that ends exactly at the profile's stock."""
    for attempt in range(attempts):
        try:
            return History(profile, out, attempt).run()
        except Infeasible:
            continue
    raise RuntimeError(f"no history of {profile.name} ends at its opening stock in {attempts} attempts")
