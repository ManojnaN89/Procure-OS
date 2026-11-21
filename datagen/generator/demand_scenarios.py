"""Changes the ERP sends after the base demand (decision Q21): the files of each scenario in `demand_changes:` of a
company profile, and what each must do, worked out here from the raw files on their own (never from the backend).

Each scenario starts from the base state: the demand in inputs/ loaded and one store check run on the stock at the cutoff
(the opening stock plus the movement history). Its `setup` steps run, then its files are imported, then its `after`
steps. The truth (`truth/truth_demand_change.csv`) has one row per material request and item touched, before (after the
setup) and after:

- the stock set aside, earliest need-by first, only free stock; a shortfall goes on a purchase request;
- a line needing less (or cancelled) gives up first what is on requests not yet with the buyer, then its stock; what is
  still over is a case for the buyer (up to what the buyer has) and the rest, issued stock, a case for the store;
- stock released goes at once to the open lines at the place, earliest need-by first: first to what nothing covers, then
  in place of what requests not yet with the buyer bring;
- a line needing more or earlier is checked again: what is still short goes on a new request;
- a net MRP line takes no free stock: it buys what it needs.

Setup and after steps: `approve <order>` with `source` (that order's requests go to the buyer), `issue <order> <item>
<qty>`, `count <location> <item> <qty>`, `open_store <location>`, `store_check`.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from history_plan import production_lines
from master_files import read, write_csv

ZERO = Decimal(0)
TEMPLATES = {
    "production_order": ["order_no", "product_code", "qty", "need_by", "reason", "location_code", "status"],
    "production_order_line": ["order_no", "item_code", "qty", "unit", "need_by", "status"],
    "mrp_result": ["plan_no", "line_no", "item_code", "qty", "unit", "need_by", "location_code", "basis", "status"],
    "joiner_plan": ["plan_no", "department", "kit", "count", "start_date", "location_code", "status"],
}
TRUTH = [
    "scenario", "mr_order_no", "item_code", "location_code", "need_before", "need_after", "reserved_before",
    "reserved_after", "open_request_before", "open_request_after", "buyer_case_qty", "store_case_qty", "new_request_qty",
]  # fmt: skip


def _q(value: Decimal) -> str:
    text = f"{value:f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


# ---- the files of a scenario ------------------------------------------------------------------------


def scenario_files(scenario: dict[str, Any], inputs: dict[str, list[dict[str, str]]]) -> dict[str, list[dict[str, str]]]:
    """The files the ERP sends in a scenario, in our template's columns."""
    files: dict[str, list[dict[str, str]]] = {}
    for kind in ("production_order", "joiner_plan"):
        if kind in scenario:
            files[kind] = [dict(zip(TEMPLATES[kind], row, strict=True)) for row in scenario[kind]]
    if scenario.get("lines_for"):
        orders = [o for o in files["production_order"] if o["order_no"] in scenario["lines_for"]]
        files["production_order_line"] = [
            {**ln, "status": ""} for ln in production_lines(inputs["bom"], orders)
        ]
    if scenario.get("mrp"):
        bom: dict[str, list[dict[str, str]]] = defaultdict(list)
        for b in inputs["bom"]:
            bom[b["product_code"]].append(b)
        rows = []
        for run in scenario["mrp"]:
            for n, b in enumerate(bom[run["product"]], start=int(run["first_line"])):
                rows.append(
                    {
                        "plan_no": run["plan_no"], "line_no": str(n), "item_code": b["item_code"],
                        "qty": f"{(Decimal(b['qty']) * Decimal(run['qty'])).quantize(Decimal('0.001'))}",
                        "unit": b["unit"], "need_by": run["need_by"], "location_code": run["location_code"],
                        "basis": run["basis"], "status": "",
                    }
                )  # fmt: skip
        files["mrp_result"] = rows
    return files


def write_scenarios(profile_data: dict[str, Any], out: Path) -> dict[str, int]:
    """changes/<scenario>/<kind>.csv for every scenario, and the truth of them all. Reads the dataset already written."""
    scenarios = profile_data.get("demand_changes") or []
    if not scenarios:
        return {}
    inputs = {p.stem: read(p) for p in sorted((out / "inputs").glob("*.csv"))}
    counts: dict[str, int] = {}
    truth: list[dict[str, str]] = []
    for sc in scenarios:
        files = scenario_files(sc, inputs)
        for kind, rows in files.items():
            write_csv(out / "changes" / sc["name"] / f"{kind}.csv", TEMPLATES[kind], list(rows))
        counts[sc["name"]] = sum(len(r) for r in files.values())
        if not sc.get("refused"):
            truth += Plant(out, inputs).run(sc, files)
    write_csv(out / "truth" / "truth_demand_change.csv", TRUTH, list(truth))
    return counts


# ---- the oracle ---------------------------------------------------------------------------------------


@dataclass
class Line:
    mr: int  # which material request (the order of making)
    order: str
    item: str
    place: str
    need: Decimal
    need_by: str
    net: bool = False
    reserved: Decimal = ZERO
    issued: Decimal = ZERO
    open_req: Decimal = ZERO  # on requests not yet with the buyer
    buyer_req: Decimal = ZERO  # on requests the buyer has
    status: str = "open"
    buyer_case: Decimal = ZERO
    store_case: Decimal = ZERO
    new_request: Decimal = ZERO

    @property
    def short(self) -> Decimal:
        return max(self.need - self.issued - self.reserved - self.open_req - self.buyer_req, ZERO)


@dataclass
class Plant:
    """The material requests of one company as the rules move them."""

    out: Path
    inputs: dict[str, list[dict[str, str]]]
    lines: list[Line] = field(default_factory=list)
    free: dict[tuple[str, str], Decimal] = field(default_factory=dict)
    mrs: list[tuple[str, str]] = field(default_factory=list)  # (order, status) per material request

    def stock(self) -> dict[tuple[str, str], Decimal]:
        held: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
        since: dict[tuple[str, str], str] = {}
        for r in read(self.out / "master" / "stock_opening.csv"):
            held[(r["item_code"], r["location_code"])] += Decimal(r["qty"])
            since[(r["item_code"], r["location_code"])] = r["as_of"]
        for m in read(self.out / "history" / "stock_movement.csv"):
            key = (m["item_code"], m["location_code"])
            if m["date"] < since.get(key, ""):
                continue
            sign = 1 if m["type"] in ("receipt", "transfer_in", "adjust") else -1
            held[key] += sign * Decimal(m["qty"])
        return dict(held)

    # -- demand as records -------------------------------------------------------------------------

    def requirements(self, files: dict[str, list[dict[str, str]]]) -> list[dict[str, Any]]:
        """One record per material of each row: (order, key, item, place, qty, need_by, net, status)."""
        out: list[dict[str, Any]] = []
        heads = {o["order_no"]: o for o in files.get("production_order", [])}
        for ln in files.get("production_order_line", []):
            head = heads.get(ln["order_no"]) or self.heads[ln["order_no"]]
            out.append(
                {"order": ln["order_no"], "key": (ln["order_no"], ln["item_code"]), "item": ln["item_code"],
                 "place": head["location_code"], "qty": Decimal(ln["qty"]), "need_by": ln["need_by"] or head["need_by"],
                 "net": False, "cancel": ln.get("status") == "cancelled"}
            )  # fmt: skip
        for r in files.get("mrp_result", []):
            out.append(
                {"order": f"{r['plan_no']}/{r['location_code']}", "key": (r["plan_no"], r["line_no"]),
                 "item": r["item_code"], "place": r["location_code"], "qty": Decimal(r["qty"]),
                 "need_by": r["need_by"], "net": r.get("basis") == "net", "cancel": r.get("status") == "cancelled"}
            )  # fmt: skip
        kits: dict[str, list[dict[str, str]]] = defaultdict(list)
        for k in self.inputs.get("joiner_kit", []):
            kits[k["kit"]].append(k)
        for r in files.get("joiner_plan", []):
            for k in kits[r["kit"]]:
                out.append(
                    {"order": f"{r['plan_no']}/{r['department']}", "key": (r["plan_no"], r["department"], r["kit"],
                     r["start_date"], k["item_code"]), "item": k["item_code"], "place": r["location_code"],
                     "qty": Decimal(k["qty"]) * Decimal(r["count"]), "need_by": r["start_date"], "net": False,
                     "cancel": r.get("status") == "cancelled"}
                )  # fmt: skip
        return out

    def base(self) -> None:
        """The base state: the inputs loaded, one material request per order, the store check run."""
        self.heads = {o["order_no"]: o for o in self.inputs.get("production_order", [])}
        self.by_key: dict[tuple[str, ...], Line] = {}
        self.free = self.stock()
        recs = self.requirements(self.inputs)
        self.make(recs)
        self.check(None)

    def make(self, recs: list[dict[str, Any]]) -> None:
        """New records join their order's open material request, else make a new one; earliest need-by first."""
        new = [r for r in recs if r["key"] not in self.by_key and not r["cancel"]]
        orders: dict[str, list[dict[str, Any]]] = {}
        for r in sorted(new, key=lambda r: (self.heads.get(r["order"], r)["need_by"], r["order"])):
            orders.setdefault(r["order"], []).append(r)
        for order, rs in orders.items():
            mr = next((i for i, (o, s) in enumerate(self.mrs) if o == order and s == "open"), None)
            if mr is None:
                self.mrs.append((order, "open"))
                mr = len(self.mrs) - 1
            for r in rs:
                line = Line(mr, order, r["item"], r["place"], r["qty"], r["need_by"], r["net"])
                self.lines.append(line)
                self.by_key[r["key"]] = line

    def ordered(self, place: tuple[str, str]) -> list[Line]:
        return sorted(
            (ln for ln in self.lines if (ln.item, ln.place) == place and ln.status == "open"),
            key=lambda ln: (ln.need_by, ln.mr),
        )

    def give(self, places: set[tuple[str, str]]) -> None:
        """Free stock to the open lines: what nothing covers first, then in place of requests not with the buyer."""
        for place in sorted(places):
            lines = [ln for ln in self.ordered(place) if not ln.net]
            for ln in lines:
                take = min(ln.short, max(self.free.get(place, ZERO), ZERO))
                ln.reserved += take
                self.free[place] = self.free.get(place, ZERO) - take
            for ln in lines:
                take = min(ln.open_req, max(self.free.get(place, ZERO), ZERO))
                ln.reserved += take
                ln.open_req -= take
                self.free[place] = self.free.get(place, ZERO) - take

    def check(self, places: set[tuple[str, str]] | None) -> None:
        """The store check: stock set aside, what is still short bought on a new request."""
        every = {(ln.item, ln.place) for ln in self.lines if ln.status == "open"}
        places = every if places is None else places & every
        self.give(places)
        for ln in self.lines:
            if ln.status == "open" and (ln.item, ln.place) in places and ln.short:
                ln.new_request += ln.short
                ln.open_req += ln.short

    # -- a scenario ---------------------------------------------------------------------------------

    def step(self, words: str) -> None:
        verb, *args = words.split()
        if verb == "approve":
            for ln in self.lines:
                if ln.order == args[0]:
                    ln.buyer_req, ln.open_req = ln.buyer_req + ln.open_req, ZERO
        elif verb == "issue":
            ln = next(x for x in self.lines if x.order == args[0] and x.item == args[1] and x.status == "open")
            qty = Decimal(args[2])
            ln.reserved -= qty
            ln.issued += qty  # the stock leaves the shelf with its reservation: free stock is the same
        elif verb == "count":
            place = (args[1], args[0])
            self.free[place] = self.free.get(place, ZERO) + Decimal(args[2])
        elif verb == "store_check":
            self.check(None)
        # "source" and "open_store" change nothing the oracle counts

    def apply(self, rec: dict[str, Any], released: set[tuple[str, str]], again: set[tuple[str, str]]) -> None:
        ln = self.by_key[rec["key"]]
        if ln.status == "cancelled":
            return
        place = (ln.item, ln.place)
        need = ZERO if rec["cancel"] else rec["qty"]
        if rec["cancel"] or need < ln.need:
            left = max(ln.issued + ln.reserved + ln.open_req + ln.buyer_req - need, ZERO)
            cut = min(left, ln.open_req)
            ln.open_req -= cut
            left -= cut
            release = min(left, ln.reserved)
            ln.reserved -= release
            self.free[place] = self.free.get(place, ZERO) + release
            left -= release
            if release:
                released.add(place)
            buyer = min(left, ln.buyer_req)
            ln.buyer_case += buyer
            ln.store_case += left - buyer
            ln.status = "cancelled" if rec["cancel"] else "issued" if ln.issued >= need else "open"
        elif need > ln.need:
            ln.status = "open"
            again.add(place)
        if not rec["cancel"]:
            if rec["need_by"] < ln.need_by:
                again.add(place)
            ln.need, ln.need_by = need, rec["need_by"]

    def run(self, sc: dict[str, Any], files: dict[str, list[dict[str, str]]]) -> list[dict[str, str]]:
        self.base()
        for words in sc.get("setup", []):
            self.step(words)
        before = {id(ln): (ln.need, ln.reserved, ln.open_req) for ln in self.lines}
        dates = {id(ln): ln.need_by for ln in self.lines}
        for ln in self.lines:
            ln.new_request = ZERO
        released: set[tuple[str, str]] = set()
        again: set[tuple[str, str]] = set()
        for o in files.get("production_order", []):
            if o["status"] == "cancelled":
                for key, ln in self.by_key.items():
                    if key[0] == o["order_no"] and ln.order == o["order_no"]:
                        self.apply({"key": key, "cancel": True, "qty": ZERO, "need_by": ln.need_by}, released, again)
            elif o["need_by"] != self.heads[o["order_no"]]["need_by"]:
                old = self.heads[o["order_no"]]["need_by"]
                for key, ln in self.by_key.items():
                    if ln.order == o["order_no"] and ln.need_by == old:
                        self.apply({"key": key, "cancel": False, "qty": ln.need, "need_by": o["need_by"]}, released, again)
            self.heads[o["order_no"]] = o
        recs = self.requirements({k: v for k, v in files.items() if k != "production_order"})
        for rec in recs:
            if rec["key"] in self.by_key:
                self.apply(rec, released, again)
        for i, (order, _) in enumerate(self.mrs):
            if all(ln.status != "open" for ln in self.lines if ln.mr == i):
                self.mrs[i] = (order, "closed")
        self.make([r for r in recs if r["key"] not in self.by_key])
        self.give(released)
        if again:
            self.check(again)
        for words in sc.get("after", []):
            self.step(words)
        return self.rows(sc["name"], before, dates)

    def rows(
        self, name: str, before: dict[int, tuple[Decimal, Decimal, Decimal]], dates: dict[int, str]
    ) -> list[dict[str, str]]:
        """One row per line touched: a quantity or a date moved, a case or a new request."""
        out = []
        for ln in self.lines:
            need0, res0, open0 = before.get(id(ln), (ZERO, ZERO, ZERO))
            after = (ln.need if ln.status != "cancelled" else ZERO, ln.reserved, ln.open_req)
            moved = dates.get(id(ln), ln.need_by) != ln.need_by
            if (need0, res0, open0) == after and not (moved or ln.buyer_case or ln.store_case or ln.new_request):
                continue
            out.append(
                {
                    "scenario": name, "mr_order_no": ln.order, "item_code": ln.item, "location_code": ln.place,
                    "need_before": _q(need0), "need_after": _q(after[0]), "reserved_before": _q(res0),
                    "reserved_after": _q(ln.reserved), "open_request_before": _q(open0),
                    "open_request_after": _q(ln.open_req), "buyer_case_qty": _q(ln.buyer_case),
                    "store_case_qty": _q(ln.store_case), "new_request_qty": _q(ln.new_request),
                }
            )  # fmt: skip
        return out
