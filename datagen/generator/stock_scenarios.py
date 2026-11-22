"""Held stock and the stock check scenarios of block 2 (tasks 4.1, 6.1, 6.2, 6.5, 6.7): `held:` and `stock_scenarios:`
of a company profile, and what each scenario must do, worked out here from the raw files on their own (never from the
backend).

Files written:
- history/stock_held.csv: stock held back at the cutoff (quarantine or blocked), loaded by `import stock` after the
  movements as receipts of that quality, one hold each;
- truth/stock_scenarios.json: every scenario with its steps spelled out from the demo reset, each on a pinned business
  date by a person, the states worth checking labelled, and what the step must show besides the tables (cases, refusals);
- truth/truth_stock_check.csv: every material request line at each labelled step (need, issued, set aside, timely, late,
  at risk, short, what live requests still bring, the causes);
- truth/truth_stock_position.csv: every stocked item-location at each labelled step (usable, quarantine, blocked, set
  aside, safety stock, available);
- truth/truth_stock_requests.csv: the purchase requests a labelled step made, changed or cancelled.

The rules, as the plan and the block 2 spec say them:
- free stock for setting aside = usable on hand - set aside - safety stock; held stock (quarantine, blocked) is never set
  aside, issued or counted;
- each part still to come for a line has a date: on an order, its promised date, else the order line's need-by; on a
  request or quotation not yet ordered, the request line's need-by (that of the material request line); a date already
  passed with the goods not in counts as today. The latest arrival is the line's need-by moved back the receiving buffer
  in working days on the location's calendar. Timely = dated on or before it; late = the rest;
- lacking = need - issued - set aside; at risk = min(late, max(lacking - timely, 0)); short = max(lacking - timely - late,
  0). Free stock goes first to short + at risk, earliest need-by first, then takes over cover on requests not yet with
  the buyer (cutting them, newest first), as before;
- the store check buys only what is short; checks started by the system (the job, events) never buy again for a line
  whose last request was rejected, and leave what they make as drafts (setting store_check_submit off);
- one late-supply case per order and material request, for the buyer (escalated to the buyer's manager), due after
  late_supply_case_days working days; it resolves itself once nothing on it is at risk;
- estimates: the contract price, else the item's standard price (the history's orders are not loaded at a reset,
  so nothing was paid yet);
- restock (min-max) counts on order for stock only the stock part of open requests and orders.
"""

import json
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from common import Calendar
from demand_scenarios import Plant, _q
from master_files import read, write_csv

ZERO = Decimal(0)
CENT = Decimal("0.01")
LIVE = ("draft", "submitted", "approved", "returned")
CASE_DAYS = 2
HELD = ["id", "item_code", "location_code", "date", "quality", "qty", "reason", "reference"]
CHECK = [
    "scenario", "step", "mr_order_no", "item_code", "location_code", "need", "issued", "reserved", "timely", "late",
    "at_risk", "short", "open_request_qty", "causes",
]  # fmt: skip
POSITION = [
    "scenario", "step", "item_code", "location_code", "usable", "quarantine", "blocked", "reserved", "safety_stock",
    "available",
]  # fmt: skip
REQUESTS = ["scenario", "step", "kind", "order_no", "lines", "value", "priority", "approver_code", "change"]
Place = tuple[str, str]


def _money(value: Decimal) -> str:
    return f"{value.quantize(CENT)}"


# ---- the files ------------------------------------------------------------------------------------------


def write_stock(profile_data: dict[str, Any], out: Path) -> dict[str, int]:
    """history/stock_held.csv, then the scenarios and their truth. Reads the dataset already written."""
    held = [dict(zip(HELD, [str(v) for v in row], strict=True)) for row in profile_data.get("held") or []]
    write_csv(out / "history" / "stock_held.csv", HELD, list(held))
    scenarios = profile_data.get("stock_scenarios") or []
    if not scenarios:
        return {"held": len(held)}
    by_name = {s["name"]: s for s in scenarios}
    company = profile_data["tenant"]
    spelled: list[dict[str, Any]] = []
    truth: dict[str, list[dict[str, str]]] = {"check": [], "position": [], "requests": []}
    for sc in scenarios:
        steps = _expand(sc, by_name)
        world = World(profile_data, out)
        for step in steps:
            if step.get("label") is not None and not world.primed:
                world.seen = {r.no: r.shown() for r in world.reqs}  # only what the scenario's own steps do counts
                world.primed = True
            world.do(step)
            if step.get("label") is not None:
                world.record(sc["name"], step, truth)
        spelled.append(
            {"name": sc["name"], "company": company, "title": sc["title"], "base": sc.get("base"), "steps": steps}
        )
    (out / "truth").mkdir(parents=True, exist_ok=True)
    (out / "truth" / "stock_scenarios.json").write_text(
        json.dumps(spelled, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    write_csv(out / "truth" / "truth_stock_check.csv", CHECK, list(truth["check"]))
    write_csv(out / "truth" / "truth_stock_position.csv", POSITION, list(truth["position"]))
    write_csv(out / "truth" / "truth_stock_requests.csv", REQUESTS, list(truth["requests"]))
    return {"held": len(held), "scenarios": len(scenarios), **{k: len(v) for k, v in truth.items()}}


def _step(raw: dict[str, Any], *, own: bool) -> dict[str, Any]:
    return {
        "do": raw["do"],
        "who": raw.get("who"),
        "on": str(raw["on"] if "on" in raw else raw[True]),  # YAML 1.1 reads a bare `on` key as true
        "args": {k: str(v) if not isinstance(v, bool) else v for k, v in (raw.get("args") or {}).items()},
        "label": raw.get("label") if own else None,
    }


def _expand(sc: dict[str, Any], by_name: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """The scenario's steps from the demo reset: its base's (unlabelled), then its own."""
    base = sc.get("base")
    if base is None:
        before: list[dict[str, Any]] = []
    elif base == "reset":
        first = next(s for s in by_name.values() if s.get("base") is None)
        before = [_step(first["steps"][0], own=False)]
    else:
        before = [{**s, "label": None, "expect": None} for s in _expand(by_name[base], by_name)]
        for s in before:
            s.pop("expect", None)
    return before + [_step(s, own=True) for s in sc["steps"]]


# ---- the model ---------------------------------------------------------------------------------------------


@dataclass
class ReqLine:
    item: str
    qty: Decimal  # what it buys (the item is bought in its stock unit)
    price: Decimal
    mr: "MLine | None" = None  # the material request line it buys for (a shortfall), else stock (a restock)
    state: str = "open"  # open (not yet with the buyer), buyer (out for quotation), po (on an order)
    supplier: str | None = None  # the order's supplier
    promised: date | None = None
    received: Decimal = ZERO
    closed: Decimal = ZERO  # short-closed on the order


@dataclass
class Req:
    no: int
    kind: str  # shortfall or restock
    order: str
    location: str
    department: str
    priority: str
    status: str
    lines: list[ReqLine]
    approver: str

    @property
    def live(self) -> bool:
        return self.status in LIVE

    @property
    def value(self) -> Decimal:
        return sum(((ln.qty * ln.price).quantize(CENT) for ln in self.lines if ln.qty > 0), ZERO)

    def shown(self) -> tuple[str, str, str]:
        lines = ";".join(f"{ln.item}:{_q(ln.qty)}" for ln in self.lines if ln.qty > 0)
        return lines, _money(self.value), self.status


@dataclass
class MLine:
    mr: int
    order: str
    item: str
    place: str
    need: Decimal
    need_by: date
    reserved: Decimal = ZERO
    issued: Decimal = ZERO
    status: str = "open"
    buys: list[ReqLine] = field(default_factory=list)
    req_of: dict[int, Req] = field(default_factory=dict)  # id(request line) -> its request
    last_rejected: bool = False

    @property
    def lacking(self) -> Decimal:
        return max(self.need - self.issued - self.reserved, ZERO)


def _left(ln: ReqLine, req: Req) -> Decimal:
    """What a request line still brings: nothing once its request is dead."""
    if not req.live:
        return ZERO
    return max(ln.qty - ln.received - ln.closed, ZERO)


class World:
    """One demo company as the steps move it."""

    def __init__(self, profile: dict[str, Any], out: Path) -> None:
        self.p, self.out = profile, out
        locations = [profile["location"], *(profile.get("locations") or [])]
        self.cal = Calendar(locations)
        self.home = profile["location"]["code"]
        self.items = {it["code"]: it for it in profile["items"]}
        self.categories = {c["code"]: c for c in profile["categories"]}
        self.people = {u["code"]: u for u in profile["people"]}
        self.buyer = next(u["code"] for u in profile["people"] if "buyer" in u["roles"])
        self.today = date(2026, 11, 23)
        self.buffer = 0
        self.reqs: list[Req] = []
        self.lines: list[MLine] = []
        self.usable: dict[Place, Decimal] = {}
        self.held: dict[tuple[str, str, str], Decimal] = {}  # (item, place, quality)
        self.holds: dict[str, dict[str, Any]] = {}
        self.plan: dict[Place, dict[str, Any]] = {}
        self.cases: dict[tuple[int, str, str], dict[str, Any]] = {}  # (request, supplier, order) -> open case
        self.resolved: list[dict[str, Any]] = []
        self.pending: dict[str, Any] | None = None
        self.expect: dict[str, Any] = {}
        self.seen: dict[int, tuple[str, str, str]] = {}  # request -> what was last recorded of it
        self.primed = False

    # -- reference data ---------------------------------------------------------------------------------

    def contract(self, item: str, place: str) -> tuple[str, Decimal] | None:
        regions = {r["code"]: set(r["locations"]) for r in self.p.get("regions") or []}
        for c in self.p.get("contracts") or []:
            if item not in c["prices"] or not (c["valid_from"] <= self.today.isoformat() <= c["valid_to"]):
                continue
            if c.get("region") and place not in regions.get(c["region"], set()):
                continue
            return c["supplier"], Decimal(c["prices"][item])
        return None

    def price(self, item: str, place: str) -> Decimal:
        found = self.contract(item, place)
        if found:
            return found[1]
        return Decimal(self.items[item]["price"])  # no order of the history is loaded, so no last price paid yet

    def approver(self, value: Decimal, items: list[str], department: str, place: str) -> str:
        """The chain the approval rules give a request of this value (person codes joined by '>', or auto)."""
        rows = [r.split(",") for r in self.p["approval_rules"].strip().splitlines()]
        head, body = rows[0], [dict(zip(rows[0], r, strict=True)) for r in rows[1:]]
        del head
        cats = {self.items[i]["category"] for i in items}
        category = cats.pop() if len(cats) == 1 else None
        capex = any(self.items[i].get("capex") for i in items)

        def fits(r: dict[str, str]) -> bool:
            return (
                r["document"] == "request"
                and Decimal(r["min_value"]) <= value <= Decimal(r["max_value"])
                and r["category_code"] in ("ANY", category)
                and r["department_code"] in ("ANY", department)
                and r["location_code"] in ("ANY", place)
                and r["spend_type"] in ("ANY", "capex" if capex else "ANY")
            )

        found = [r for r in body if fits(r)]
        main = sorted(
            (r for r in found if not r["approver_chain"].startswith("+")),
            key=lambda r: (r["category_code"] == "ANY", r["spend_type"] == "ANY"),
        )
        chain = main[0]["approver_chain"].split(">") if main else []
        chain += [r["approver_chain"][1:] for r in found if r["approver_chain"].startswith("+")]
        if chain == ["auto"]:
            return "auto"
        return ">".join(self.person(role, department) for role in chain if role != "auto")

    def person(self, role: str, department: str) -> str:
        source, value = next((s, v) for r, s, v in self.p["approver_roles"] if r == role)
        if source == "location_head":
            return str(self.p["location"]["head"])
        if source == "department_head":
            return str(next(d[2] for d in self.p["departments"] if d[0] == department))
        return str(next(u["code"] for u in self.p["people"] if u["designation"] == value))

    # -- stock -------------------------------------------------------------------------------------------

    def reserved(self, place: Place) -> Decimal:
        return sum((ln.reserved for ln in self.lines if (ln.item, ln.place) == place and ln.status == "open"), ZERO)

    def safety(self, place: Place) -> Decimal:
        return Decimal(self.plan.get(place, {}).get("safety_stock") or 0)

    def free(self, place: Place) -> Decimal:
        return self.usable.get(place, ZERO) - self.reserved(place) - self.safety(place)

    # -- cover by date (Q17) -----------------------------------------------------------------------------

    def latest(self, ln: MLine) -> date:
        return self.cal.add_working(ln.need_by, -self.buffer, ln.place) if self.buffer else ln.need_by

    def parts(self, ln: MLine) -> list[tuple[ReqLine, Decimal, date]]:
        out = []
        for b in ln.buys:
            left = _left(b, ln.req_of[id(b)])
            if left <= 0:
                continue
            when = b.promised or ln.need_by
            out.append((b, left, max(when, self.today)))
        return out

    def cover(self, ln: MLine) -> dict[str, Decimal]:
        latest = self.latest(ln)
        parts = self.parts(ln)
        timely = sum((q for _, q, d in parts if d <= latest), ZERO)
        late = sum((q for _, q, d in parts if d > latest), ZERO)
        lacking = ln.lacking
        return {
            "timely": timely,
            "late": late,
            "at_risk": min(late, max(lacking - timely, ZERO)),
            "short": max(lacking - timely - late, ZERO),
            "requested": timely + late,
            "open": sum((q for b, q, _ in parts if b.state == "open"), ZERO),
        }

    def ordered(self, place: Place) -> list[MLine]:
        return sorted(
            (ln for ln in self.lines if (ln.item, ln.place) == place and ln.status == "open"),
            key=lambda ln: (ln.need_by, ln.mr),
        )

    def give(self, places: set[Place]) -> None:
        """Free stock to the open lines, earliest need-by first: first to what is short or at risk, then in place of
        cover on requests not yet with the buyer (cutting them)."""
        for place in sorted(places):
            lines = self.ordered(place)
            left = max(self.free(place), ZERO)
            for ln in lines:
                take = min(max(ln.lacking - self.cover(ln)["timely"], ZERO), left)
                ln.reserved += take
                left -= take
            for ln in lines:
                c = self.cover(ln)
                still_short = max(ln.lacking - c["requested"], ZERO)
                over = min(max(min(c["open"], ln.lacking - still_short), ZERO), left)
                if over:
                    ln.reserved += over
                    left -= over
                    self.cut(ln, over)

    def cut(self, ln: MLine, qty: Decimal) -> None:
        """Take qty off the open request lines buying for a line, newest first."""
        for b in reversed(ln.buys):
            req = ln.req_of[id(b)]
            if qty <= 0:
                break
            if b.state != "open" or not req.live or b.qty <= 0:
                continue
            take = min(b.qty, qty)
            b.qty -= take
            qty -= take
            if all(x.qty <= 0 for x in req.lines):
                req.status = "cancelled"

    # -- the store check ----------------------------------------------------------------------------------

    def every_place(self) -> set[Place]:
        return {(ln.item, ln.place) for ln in self.lines if ln.status == "open"}

    def check(self, places: set[Place] | None, *, automatic: bool, submit: bool) -> None:
        places = self.every_place() if places is None else places & self.every_place()
        self.give(places)
        by_mr: dict[int, list[tuple[MLine, Decimal]]] = {}
        for ln in sorted(self.lines, key=lambda x: (x.need_by, x.mr)):
            if ln.status != "open" or (ln.item, ln.place) not in places:
                continue
            short = self.cover(ln)["short"]
            if short > 0 and not (automatic and ln.last_rejected):
                by_mr.setdefault(ln.mr, []).append((ln, short))
        for _, wanted in by_mr.items():
            first = wanted[0][0]
            lines = [ReqLine(ln.item, qty, self.price(ln.item, ln.place), mr=ln) for ln, qty in wanted]
            dept = self.department_of(first)
            req = self.new_req("shortfall", first.order, first.place, dept, "normal", lines, submit=submit)
            for ln, b in zip([w[0] for w in wanted], lines, strict=True):
                ln.buys.append(b)
                ln.req_of[id(b)] = req
                ln.last_rejected = False

    def department_of(self, ln: MLine) -> str:
        if "/" in ln.order and not ln.order.startswith("MRP"):
            return ln.order.split("/")[1]
        return str(self.p.get("replenishment_department") or "")

    def new_req(
        self, kind: str, order: str, place: str, dept: str, priority: str, lines: list[ReqLine], *, submit: bool
    ) -> Req:
        req = Req(len(self.reqs) + 1, kind, order, place, dept, priority, "draft", lines, "")
        req.approver = self.approver(req.value, [ln.item for ln in lines], dept, place)
        if submit:
            req.status = "approved" if req.approver == "auto" else "submitted"
        self.reqs.append(req)
        return req

    # -- restock (min-max), counting on order only the stock part -----------------------------------------

    def restock(self) -> None:
        groups: dict[tuple[str, str], list[ReqLine]] = {}
        for place, plan in sorted(self.plan.items()):
            if plan["reorder_policy"] != "minmax":
                continue
            item, loc = place
            on_hand = self.usable.get(place, ZERO) - self.reserved(place)
            on_order, stock_part = ZERO, False
            for req in self.reqs:
                for b in req.lines:
                    if b.item != item or req.location != loc or b.qty <= 0:
                        continue
                    if b.state in ("open", "buyer") and req.live:
                        on_order += b.qty
                    elif b.state == "po" and req.live and b.received + b.closed < b.qty:
                        on_order += b.qty - b.received
                    else:
                        continue
                    stock_part = stock_part or b.mr is None
            earmarked = sum(
                (max(min(self.cover(ln)["requested"], ln.lacking), ZERO) for ln in self.ordered(place)), ZERO
            )
            free_on_order = on_order - min(earmarked, on_order)
            status = _status(on_hand, free_on_order, plan)
            if status not in ("stockout", "below_safety", "reorder") or stock_part:
                continue
            qty = max(Decimal(plan["max_qty"]) - (on_hand + free_on_order), ZERO)
            if qty <= 0:
                continue
            priority = "urgent" if status in ("stockout", "below_safety") else "normal"
            groups.setdefault((self.items[item]["category"], priority), []).append(
                ReqLine(item, qty, self.price(item, loc))
            )
        for (_, priority), lines in sorted(groups.items()):
            dept = str(self.p.get("replenishment_department") or "")
            self.new_req("restock", "", self.home, dept, priority, lines, submit=True)

    # -- late-supply cases ----------------------------------------------------------------------------------

    def evaluate(self) -> None:
        found: dict[tuple[int, str, str], list[dict[str, str]]] = {}
        for ln in self.lines:
            if ln.status != "open":
                continue
            c = self.cover(ln)
            if c["at_risk"] <= 0:
                continue
            latest = self.latest(ln)
            for b, qty, when in self.parts(ln):
                if b.state != "po" or when <= latest:
                    continue
                req = ln.req_of[id(b)]
                key = (req.no, str(b.supplier), ln.order)
                found.setdefault(key, []).append(
                    {"item_code": ln.item, "at_risk": _q(min(c["at_risk"], qty)),
                     "promised": (b.promised or ln.need_by).isoformat(), "latest_arrival": latest.isoformat()}
                )  # fmt: skip
        for key, lines in found.items():
            case = self.cases.get(key)
            if case is None:
                owner = self.buyer
                place = next(ln.place for ln in self.lines if ln.order == key[2])
                case = {
                    "supplier": key[1], "mr_order_no": key[2], "owner": owner,
                    "escalate_to": self.people[owner]["manager"] or None,
                    "opened_on": self.today.isoformat(),
                    "due": self.cal.add_working(self.today, CASE_DAYS, place).isoformat(),
                    "buffer_days": self.buffer,
                }  # fmt: skip
                self.cases[key] = case
            case["lines"] = lines
        for key in [k for k in self.cases if k not in found]:
            case = self.cases.pop(key)
            self.resolved.append({**case, "outcome": "resolved", "closed_on": self.today.isoformat()})

    # -- the steps ----------------------------------------------------------------------------------------------

    def do(self, step: dict[str, Any]) -> None:
        self.today = date.fromisoformat(step["on"])
        self.expect = {}
        getattr(self, "step_" + step["do"])(step["args"])
        self.evaluate()
        if step.get("label") is not None:
            extra = self.expectation()
            if extra:
                step["expect"] = extra

    def expectation(self) -> dict[str, Any]:
        out = dict(self.expect)
        if self.cases or self.resolved:
            out["open_cases"] = [dict(c) for c in self.cases.values()]
            out["resolved_cases"] = list(self.resolved)
        return out

    def step_reset(self, args: dict[str, Any]) -> None:
        inputs = {p.stem: read(p) for p in sorted((self.out / "inputs").glob("*.csv"))}
        plant = Plant(self.out, inputs)
        plant.heads = {o["order_no"]: o for o in inputs.get("production_order", [])}
        plant.by_key = {}
        plant.make(plant.requirements(inputs))
        self.lines = [MLine(x.mr, x.order, x.item, x.place, x.need, date.fromisoformat(x.need_by)) for x in plant.lines]
        self.usable = {k: v for k, v in plant.stock().items()}
        for r in read(self.out / "master" / "item_location.csv"):
            self.plan[(r["item_code"], r["location_code"])] = dict(r)
        for r in read(self.out / "history" / "stock_held.csv"):
            key = (r["item_code"], r["location_code"], r["quality"])
            self.held[key] = self.held.get(key, ZERO) + Decimal(r["qty"])
            self.holds[r["id"]] = {"item": r["item_code"], "place": r["location_code"], "quality": r["quality"],
                                   "left": Decimal(r["qty"])}  # fmt: skip

    def step_store_check(self, args: dict[str, Any]) -> None:
        button = args.get("button") is True
        self.check(None, automatic=not button, submit=button and args.get("submit") is not False)

    def step_job(self, args: dict[str, Any]) -> None:
        self.check(None, automatic=True, submit=False)

    def _req_for(self, order: str) -> Req:
        return [r for r in self.reqs if r.order == order and r.kind == "shortfall" and r.live][-1]

    def step_approve_request(self, args: dict[str, Any]) -> None:
        self._req_for(args["mr_order_no"]).status = "approved"

    def step_reject_request(self, args: dict[str, Any]) -> None:
        req = self._req_for(args["mr_order_no"])
        req.status = "rejected"
        places = set()
        for b in req.lines:
            if b.mr is not None:
                b.mr.last_rejected = True
                places.add((b.mr.item, b.mr.place))
        self.expect["last_rejected"] = sorted(b.item for b in req.lines if b.mr is not None and b.qty > 0)
        self.check(places, automatic=True, submit=False)

    def step_source(self, args: dict[str, Any]) -> None:
        for req in self.reqs:
            if req.status != "approved":
                continue
            for b in req.lines:
                if b.state != "open" or b.qty <= 0:
                    continue
                found = self.contract(b.item, req.location)
                b.state, b.supplier = ("po", found[0]) if found else ("buyer", None)

    def step_issue_po(self, args: dict[str, Any]) -> None:
        """The buyer issues the contract order and sends it: nothing the stock check counts changes."""

    def _po_line(self, item: str) -> tuple[Req, ReqLine]:
        for req in reversed(self.reqs):
            for b in req.lines:
                if b.item == item and b.state == "po" and b.qty > 0:
                    return req, b
        raise KeyError(item)

    def step_acknowledge(self, args: dict[str, Any]) -> None:
        req, line = self._po_line(args["item_code"])
        promised = date.fromisoformat(args["promised"])
        places = set()
        for b in req.lines:
            if b.state == "po" and b.supplier == line.supplier:
                b.promised = promised
                if b.mr is not None:
                    places.add((b.mr.item, b.mr.place))
        self.check(places, automatic=True, submit=False)

    def step_receive(self, args: dict[str, Any]) -> None:
        req, line = self._po_line(args["item_code"])
        qty = Decimal(args["qty"])
        line.received += qty
        place = (line.item, req.location)
        self.usable[place] = self.usable.get(place, ZERO) + qty
        self.give({place})

    def step_close_balance(self, args: dict[str, Any]) -> None:
        req, line = self._po_line(args["item_code"])
        line.closed = max(line.qty - line.received, ZERO)
        self.check({(line.item, req.location)}, automatic=True, submit=False)

    def step_count(self, args: dict[str, Any]) -> None:
        place = (args["item_code"], args["location"])
        self.usable[place] = Decimal(args["counted"])
        self.give({place})

    def step_decide_hold(self, args: dict[str, Any]) -> None:
        hold = self.holds[args["hold_id"]]
        to_usable, to_blocked = Decimal(args["to_usable"]), Decimal(args.get("to_blocked") or 0)
        hold["left"] -= to_usable + to_blocked
        key = (hold["item"], hold["place"])
        self.held[(*key, hold["quality"])] -= to_usable + to_blocked
        self.held[(*key, "blocked")] = self.held.get((*key, "blocked"), ZERO) + to_blocked
        self.usable[key] = self.usable.get(key, ZERO) + to_usable
        if to_usable:
            self.give({key})

    def step_hold(self, args: dict[str, Any]) -> None:
        place = (args["item_code"], args["location"])
        qty = Decimal(args["qty"])
        unreserved = self.usable.get(place, ZERO) - self.reserved(place)
        self.usable[place] -= qty
        key = (*place, args["quality"])
        self.held[key] = self.held.get(key, ZERO) + qty
        release = max(qty - max(unreserved, ZERO), ZERO)
        released = {}
        for ln in reversed(self.ordered(place)):
            if release <= 0:
                break
            give_back = min(ln.reserved, release)
            ln.reserved -= give_back
            release -= give_back
            if give_back:
                released[ln.order] = _q(give_back)
        self.expect["released"] = released
        if released:
            self.check({place}, automatic=True, submit=False)

    def step_set_setting(self, args: dict[str, Any]) -> None:
        if args["key"] == "receiving_buffer_days":
            self.buffer = int(args["value"])
            self.check(None, automatic=True, submit=False)

    def step_set_item_planning(self, args: dict[str, Any]) -> None:
        place = (args["item_code"], args["location"])
        plan = self.plan.setdefault(place, {"reorder_policy": "none"})
        for k in ("safety_stock", "reorder_policy", "reorder_point", "max_qty"):
            if k in args:
                plan[k] = args[k]

    def step_issue(self, args: dict[str, Any]) -> None:
        place = (args["item_code"], args["location"])
        qty = Decimal(args["qty"])
        available = self.free(place)
        if qty > available:
            self.expect["refused"] = {
                "status": 409, "needs_override": True, "available": _q(max(available, ZERO)),
                "safety_stock": _q(self.safety(place)),
            }  # fmt: skip
            return
        self.usable[place] -= qty

    def step_safety_override(self, args: dict[str, Any]) -> None:
        self.pending = dict(args)
        approver = self.people[self.people[self._who_store()]["manager"]]
        self.expect["approver"] = approver["code"]

    def _who_store(self) -> str:
        return next(u["code"] for u in self.p["people"] if "store" in u["roles"])

    def step_approve_proposal(self, args: dict[str, Any]) -> None:
        asked, self.pending = self.pending, None
        assert asked is not None
        qty = Decimal(asked["qty"])
        if asked["kind"] == "issue":
            self.usable[(asked["item_code"], asked["location"])] -= qty
            return
        ln = next(x for x in self.lines if x.order == asked["mr_order_no"] and x.item == asked["item_code"])
        c = self.cover(ln)
        own = c["short"] + c["at_risk"]
        room = max(self.usable.get((ln.item, ln.place), ZERO) - self.reserved((ln.item, ln.place)), ZERO)
        take = min(qty, own + c["open"], room)
        ln.reserved += take
        self.cut(ln, min(max(take - own, ZERO), c["open"]))
        self.expect["reserved"] = _q(take)

    def step_replenish(self, args: dict[str, Any]) -> None:
        self.restock()

    # -- what is recorded at a labelled step ---------------------------------------------------------------------

    def causes(self, ln: MLine, c: dict[str, Decimal]) -> list[str]:
        place = (ln.item, ln.place)
        out = []
        if ln.issued + ln.reserved >= ln.need:
            out.append("covered")
        if c["timely"] > 0:
            out.append("on_order")
        if c["at_risk"] > 0:
            out.append("late_supply")
        if c["short"] > 0:
            out.append("short")
        if ln.lacking > 0:
            usable = self.usable.get(place, ZERO)
            others = sum((x.reserved for x in self.ordered(place) if x is not ln), ZERO)
            before_safety = usable - self.reserved(place)
            if usable <= 0:
                out.append("no_stock")
            if others > 0:
                out.append("held_for_earlier")
            if before_safety > 0 and before_safety - self.safety(place) <= 0:
                out.append("safety_stock")
            if self.held.get((*place, "quarantine"), ZERO) + self.held.get((*place, "blocked"), ZERO) > 0:
                out.append("held_quality")
        return sorted(out)

    def record(self, name: str, step: dict[str, Any], truth: dict[str, list[dict[str, str]]]) -> None:
        label = step["label"]
        for ln in sorted(self.lines, key=lambda x: (x.mr, x.order, x.item)):
            if ln.status == "cancelled":
                continue
            c = self.cover(ln)
            truth["check"].append(
                {
                    "scenario": name, "step": label, "mr_order_no": ln.order, "item_code": ln.item,
                    "location_code": ln.place, "need": _q(ln.need), "issued": _q(ln.issued), "reserved": _q(ln.reserved),
                    "timely": _q(c["timely"]), "late": _q(c["late"]), "at_risk": _q(c["at_risk"]),
                    "short": _q(c["short"]), "open_request_qty": _q(c["requested"]), "causes": ";".join(self.causes(ln, c)),
                }
            )  # fmt: skip
        for place in sorted(self.plan):
            truth["position"].append(
                {
                    "scenario": name, "step": label, "item_code": place[0], "location_code": place[1],
                    "usable": _q(self.usable.get(place, ZERO)),
                    "quarantine": _q(self.held.get((*place, "quarantine"), ZERO)),
                    "blocked": _q(self.held.get((*place, "blocked"), ZERO)), "reserved": _q(self.reserved(place)),
                    "safety_stock": _q(self.safety(place)), "available": _q(self.free(place)),
                }
            )  # fmt: skip
        for req in self.reqs:
            now = req.shown()
            was = self.seen.get(req.no)
            if was == now:
                continue
            self.seen[req.no] = now
            change = "new" if was is None else "cancelled" if req.status == "cancelled" else "changed"
            if was is not None and was[:2] == now[:2]:
                continue  # only its status moved (approved, ordered): not a change of what it buys
            truth["requests"].append(
                {
                    "scenario": name, "step": label, "kind": req.kind, "order_no": req.order, "lines": now[0],
                    "value": now[1], "priority": req.priority, "approver_code": req.approver, "change": change,
                }
            )  # fmt: skip


def _status(on_hand: Decimal, on_order: Decimal, plan: dict[str, Any]) -> str:
    """As the platform's stock status: physical stock against safety stock, the position against the reorder point."""
    point = Decimal(plan["reorder_point"]) if plan.get("reorder_point") else None
    safety = Decimal(plan["safety_stock"]) if plan.get("safety_stock") else None
    top = Decimal(plan["max_qty"]) if plan.get("max_qty") else None
    if on_hand < 0:
        return "negative"
    if on_hand == 0:
        return "stockout"
    if safety is not None and on_hand < safety:
        return "below_safety"
    if point is not None and on_hand <= point:
        return "covered" if on_hand + on_order > point else "reorder"
    if top is not None and on_hand > top:
        return "over_max"
    return "ok"
