"""Shared pieces of the block 1 evals (E60-E65): demand intake, MRP results and changed ERP orders. Written from the block 1
spec, the plan and the dataset only, never from the implementation.

Two halves:

- **The oracle** (`Plant`): the store check and the change rules of the spec (Q21, sections 3 and 4) in plain code over
  the raw dataset files: stock replayed from `stock_opening.csv` and `stock_movement.csv` per item and location, the
  demand from `inputs/*.csv` (production order lines, MRP results, joiner plan times kit), the change files from
  `changes/<scenario>/`. `Rules` switches single rules to a deliberately wrong version, so each eval can show that its
  cases tell the wrong rule from the right one (the mutation checks).
- **The platform** (`Company`): a demo company made by `demo reset` in a tenant of its own, its people signed in with
  their own logins, the public API (`/api/demand/...`, `/api/material-requests...`, `/api/exceptions`), the CLI run in
  this process, and read-only views of the tables the spec names where the API is silent.

The equal need-by tie (Brightwave's two joiner requests, both 1 December) is broken the way the spec says ("made
first"): by the order the platform numbered the material requests, the one fact the oracle takes from the platform.
"""

import contextlib
import csv
import io
import json
import re
import runpy
import sys
import tempfile
from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import env
import lib
from sqlalchemy import text

Z = Decimal(0)
Q4 = Decimal("0.0001")
CENT = Decimal("0.01")
IST = ZoneInfo("Asia/Kolkata")
KINDS = (
    "production_order",
    "production_order_line",
    "mrp_result",
    "sales_order",
    "demand_plan",
    "joiner_kit",
    "joiner_plan",
)
LIVE_REQUEST = ("draft", "submitted", "approved", "returned")  # a live request: its open lines are not with the buyer
WITH_BUYER = ("sourcing", "ordered")
AUDITED = ("demand.change", "request.line_reduce", "request.cancel", "material_request.change", "task.create")


# ---- small readers ----------------------------------------------------------------------------------------------------


def js(r: Any) -> Any:
    try:
        return r.json()
    except ValueError:
        return {}


def dec(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value)) if value not in (None, "") else None
    except ArithmeticError:
        return None


def D(value: Any) -> Decimal:
    return dec(value) or Z


def fq(value: Decimal) -> str:
    """A quantity as people read it: 40, 36.5 (the spec's `format_qty`)."""
    s = f"{Decimal(value).quantize(Q4):f}"
    return s.rstrip("0").rstrip(".") if "." in s else s


def money(value: Decimal) -> Decimal:
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def day(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def csv_bytes(header: list[str], data: Iterable[list[str]]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    w.writerows(data)
    return buf.getvalue().encode()


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as f:
        return [{k: (v or "").strip() for k, v in r.items() if k} for r in csv.DictReader(f)]


@dataclass
class Checker:
    """Counts checks; a failed one becomes a finding."""

    out: Any
    done: int = 0
    failed: list[str] = field(default_factory=list)

    def __call__(self, ok: bool, message: str, severity: str = "high") -> bool:
        self.done += 1
        if not ok:
            self.failed.append(message)
            self.out.find(severity, message)
        return bool(ok)


# ---- the business calendar (from the dataset's location and data/reference, never from the platform) -----------------

DAYS = {"Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3, "Fri": 4, "Sat": 5, "Sun": 6}


def open_days(spec: str) -> set[int]:
    first, _, last = (spec or "Mon-Fri").partition("-")
    return set(range(DAYS[first], DAYS[last or first] + 1))


def holidays(scope: str) -> set[date]:
    """Closed days for a holiday scope like 'central;27': the central closed holidays and the states' lists."""
    parts = {p.strip() for p in (scope or "central").split(";") if p.strip()}
    ref = lib.ROOT / "data" / "reference"
    closed: set[date] = set()
    if "central" in parts:
        for path in sorted(ref.glob("holidays_*central*.csv")):
            closed |= {date.fromisoformat(r["date"]) for r in read_csv(path) if r.get("type", "closed") == "closed"}
    for path in sorted(ref.glob("holidays_state_*.csv")):
        closed |= {date.fromisoformat(r["date"]) for r in read_csv(path) if r["state_code"] in parts}
    return closed


@dataclass(frozen=True)
class Calendar:
    days: frozenset[int]
    closed: frozenset[date]

    def working(self, d: date) -> bool:
        return d.weekday() in self.days and d not in self.closed

    def add(self, start: date, n: int) -> date:
        d = start
        while n > 0:
            d += timedelta(days=1)
            if self.working(d):
                n -= 1
        return d


WRONG_CALENDAR = Calendar(frozenset(range(5)), frozenset())  # Mon-Fri and no holidays: the mutation of the due date


def due_ok(cal: Calendar, created: datetime, due: datetime, n: int, anchors: Iterable[date] = ()) -> bool:
    """`due` lies n working days after creation on this calendar. The spec gives days, not the calendar's hours, so the
    date is compared: n working days after the creation date, or n - 1 when the task was made on a working day before
    the working day began (the hours of that day count). Another business date in force at creation (the evals pin one)
    is accepted as the start too."""
    local_c, local_d = created.astimezone(IST), due.astimezone(IST)
    starts = {local_c.date(), *anchors}
    allowed = {cal.add(s, n) for s in starts}
    if cal.working(local_c.date()) and local_c.hour < 10:
        allowed.add(cal.add(local_c.date(), n - 1))
    return local_d.date() in allowed


# ---- the CLI in this process (so the evals' databases and pinned day apply) ----------------------------------------


def cli(*args: str) -> tuple[int, str]:
    saved = sys.argv
    sys.argv = ["p2p.cli", *args]
    buf = io.StringIO()
    code = 0
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            runpy.run_module("p2p.cli", run_name="__main__", alter_sys=True)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    finally:
        sys.argv = saved
    return code, buf.getvalue()


@contextlib.contextmanager
def on_day(d: date) -> Iterator[None]:
    from p2p.formulas.calendar import pin_today

    pin_today(d)
    try:
        yield
    finally:
        pin_today(lib.TODAY)


# ======================================================================================================================
# The oracle
# ======================================================================================================================


@dataclass(frozen=True)
class Rules:
    """The spec's rules, each switchable to a wrong version for a mutation check."""

    release_first: bool = False  # a cut releases the reservation before cutting the open purchase request
    earlier_steals: bool = False  # an order moved earlier takes stock set aside for a later one
    skip_mrp: bool = False  # MRP results never become material requests (DF-18)
    net_as_gross: bool = False  # a net MRP line sets free stock aside like a gross one
    case_for_open: bool = False  # a request not yet with the buyer gets a case instead of a cut
    keep_sole_pr: bool = False  # a cancel leaves the purchase requests made only for the order (the old rule)
    take_held: bool = False  # an increase takes stock set aside for a later order
    grow_first: bool = False  # an increase grows the submitted purchase request
    unscoped_check: bool = False  # the store check after an increase looks at every item, not only the changed ones
    no_store_case: bool = False  # issued more than the new need raises no case
    no_reopen: bool = False  # an issued line stays issued when the need goes up
    skip_closed: bool = False  # new lines of a cancelled or issued material request are skipped (DF-19)
    ignore_cancel: bool = False  # a cancelled row is loaded as live demand (a moved start date doubles the demand)


RIGHT = Rules()  # the spec's rules


@dataclass
class Cover:
    request: str  # 'first' (the store check of the base state) or 'new<n>' (a later check)
    qty: Decimal
    buyer: bool = False  # the request line is with the buyer (sourcing or ordered)
    need_by: date | None = None


@dataclass
class Line:
    key: tuple[str, ...]
    mr: str
    item: str
    loc: str
    need: Decimal
    need_by: date
    basis: str
    seq: int
    issued: Decimal = Z
    reserved: Decimal = Z
    covers: list[Cover] = field(default_factory=list)
    status: str = "open"

    def open_cover(self) -> Decimal:
        return sum((c.qty for c in self.covers if not c.buyer), Z)

    def buyer_cover(self) -> Decimal:
        return sum((c.qty for c in self.covers if c.buyer), Z)

    def requested(self) -> Decimal:
        return sum((c.qty for c in self.covers), Z)

    def short(self) -> Decimal:
        return self.need - self.issued - self.reserved - self.requested()


@dataclass
class Case:
    owner: str  # buyer or store
    mr: str
    item: str
    qty: Decimal
    doc: str  # the ERP document whose change raised it


@dataclass
class Effects:
    refused: list[tuple[str, int, str]] = field(default_factory=list)  # kind, file line, words the reason must hold
    changes: list[dict[str, Any]] = field(default_factory=list)  # kind, doc, item, what
    warnings: list[str] = field(default_factory=list)
    already: int = 0
    loaded: int = 0
    released: dict[str, Decimal] = field(default_factory=lambda: defaultdict(Decimal))
    cut: dict[tuple[str, str], Decimal] = field(default_factory=lambda: defaultdict(Decimal))  # (mr, item)
    cases: list[Case] = field(default_factory=list)
    moved: list[tuple[str, str, date, date]] = field(default_factory=list)
    new: dict[tuple[str, str], Decimal] = field(default_factory=lambda: defaultdict(Decimal))
    released_places: set[tuple[str, str]] = field(default_factory=set)
    up_places: set[tuple[str, str]] = field(default_factory=set)
    resort: set[tuple[str, str]] = field(default_factory=set)

    def case_qty(self, owner: str) -> dict[str, Decimal]:
        got: dict[str, Decimal] = defaultdict(Decimal)
        for c in self.cases:
            if c.owner == owner:
                got[c.item] += c.qty
        return dict(got)


def norm_status(value: str) -> str:
    return (value or "open").strip().lower() or "open"


def on_hand_at_cutoff(dataset: Path) -> dict[tuple[str, str], Decimal]:
    """Opening stock plus the movement history from its date, per item and location (as E37 does, per location)."""
    stock: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    since: dict[tuple[str, str], str] = {}
    first = read_csv(dataset / "master" / "location.csv")[0]["code"]
    for r in read_csv(dataset / "master" / "stock_opening.csv"):
        k = (r["item_code"], r.get("location_code") or first)
        stock[k] += D(r["qty"])
        since[k] = r["as_of"]
    for m in read_csv(dataset / "history" / "stock_movement.csv"):
        k = (m["item_code"], m.get("location_code") or first)
        if m["date"] < since.get(k, ""):
            continue
        sign = 1 if m["type"] in ("receipt", "transfer_in", "adjust") else -1
        stock[k] += sign * D(m["qty"])
    return dict(stock)


class Plant:
    """The store and its demand as the spec says they should be, from the raw files."""

    def __init__(self, dataset: Path, rules: Rules = RIGHT, rank: dict[str, int] | None = None) -> None:
        self.dataset = dataset
        self.rules = rules
        self.rank_override = rank or {}
        self.items = {r["code"]: r for r in read_csv(dataset / "master" / "item.csv")}
        self.conv = {
            (r["item_code"], r["from_uom"]): (r["to_uom"], D(r["factor"]))
            for r in read_csv(dataset / "master" / "uom_conversion.csv")
        }
        self.first_loc = read_csv(dataset / "master" / "location.csv")[0]["code"]
        self.on_hand = on_hand_at_cutoff(dataset)
        self.lines: list[Line] = []
        self.by_key: dict[tuple[str, ...], Line] = {}
        self.mrs: dict[str, dict[str, Any]] = {}  # label -> base, status, rank
        self.headers: dict[str, dict[str, Any]] = {}
        self.mrp: dict[tuple[str, str], dict[str, Any]] = {}
        self.joiners: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        self.kits: dict[str, list[tuple[str, Decimal]]] = defaultdict(list)
        self.seq = 0
        self.tag = 0
        self.cut_log: list[tuple[str, str, str, Decimal]] = []  # mr, item, request tag, qty cut
        self._load()

    # ---- basics ----
    def price(self, item: str) -> Decimal:
        """The estimate: contract, else last price paid, else standard price; on both demo datasets these are the same
        number for every material (company.yaml: contract and price-list prices equal the items' prices), and the spec's
        request values (Rs 72,930 and so on) are worked out from it."""
        return D(self.items[item]["standard_price"])

    def base_qty(self, item: str, qty: Decimal, unit: str) -> Decimal:
        base = self.items.get(item, {}).get("base_uom", "")
        if not unit or unit == base:
            return qty.quantize(Q4)
        to, factor = self.conv.get((item, unit), (base, Decimal(1)))
        return (qty * factor).quantize(Q4)

    def loc_of(self, value: str) -> str:
        return value or self.first_loc  # decision 20 Nov: a row without a location goes to the first location

    def rank(self, mr: str) -> int:
        return self.rank_override.get(mr, self.mrs[mr]["rank"])

    def free(self, item: str, loc: str) -> Decimal:
        held = sum((ln.reserved for ln in self.lines if ln.item == item and ln.loc == loc), Z)
        return self.on_hand.get((item, loc), Z) - held

    def ordered(self) -> list[Line]:
        return sorted(
            (ln for ln in self.lines if ln.status == "open"), key=lambda ln: (ln.need_by, self.rank(ln.mr), ln.seq)
        )

    def lines_of(self, base: str) -> list[Line]:
        return [ln for ln in self.lines if self.mrs[ln.mr]["base"] == base]

    def _mr_for(self, base: str) -> str | None:
        mine = [m for m, v in self.mrs.items() if v["base"] == base]
        if mine and self.mrs[mine[-1]]["status"] == "open":
            return mine[-1]
        if mine and self.rules.skip_closed:
            return None
        label = base if not mine else f"{base}#{len(mine) + 1}"
        self.mrs[label] = {"base": base, "status": "open", "rank": 1000 + len(self.mrs)}
        return label

    def add(
        self, key: tuple[str, ...], base: str, item: str, need: Decimal, need_by: date, loc: str, basis: str
    ) -> None:
        mr = self._mr_for(base)
        if mr is None:
            return
        self.seq += 1
        ln = Line(key, mr, item, loc, need, need_by, basis, self.seq)
        self.lines.append(ln)
        self.by_key[key] = ln

    def settle(self, mr: str) -> None:
        mine = [ln for ln in self.lines if ln.mr == mr]
        if mine and not any(ln.status == "open" for ln in mine):
            self.mrs[mr]["status"] = "cancelled" if any(ln.status == "cancelled" for ln in mine) else "issued"
        elif any(ln.status == "open" for ln in mine):
            self.mrs[mr]["status"] = "open"

    # ---- the base demand ----
    def _load(self) -> None:
        inputs = self.dataset / "inputs"
        for r in read_csv(inputs / "production_order.csv"):
            self.headers[r["order_no"]] = {
                "product": r["product_code"],
                "qty": D(r["qty"]),
                "need_by": date.fromisoformat(r["need_by"]),
                "loc": self.loc_of(r.get("location_code", "")),
                "status": norm_status(r.get("status", "")),
            }
        for r in read_csv(inputs / "production_order_line.csv"):
            h = self.headers[r["order_no"]]
            self.add(
                ("mo", r["order_no"], r["item_code"]),
                r["order_no"],
                r["item_code"],
                self.base_qty(r["item_code"], D(r["qty"]), r.get("unit", "")),
                date.fromisoformat(r.get("need_by") or h["need_by"].isoformat()),
                h["loc"],
                "gross",
            )
        for r in read_csv(inputs / "mrp_result.csv"):
            loc = self.loc_of(r.get("location_code", ""))
            self.mrp[(r["plan_no"], r["line_no"])] = {"item": r["item_code"], "loc": loc, "status": "open"}
            if self.rules.skip_mrp:
                continue
            self.add(
                ("mrp", r["plan_no"], r["line_no"]),
                f"{r['plan_no']}/{loc}",
                r["item_code"],
                self.base_qty(r["item_code"], D(r["qty"]), r.get("unit", "")),
                date.fromisoformat(r["need_by"]),
                loc,
                (r.get("basis") or "gross").lower(),
            )
        for r in read_csv(inputs / "joiner_kit.csv"):
            self.kits[r["kit"]].append((r["item_code"], D(r["qty"])))
        for r in read_csv(inputs / "joiner_plan.csv"):
            self._new_joiner(r)

    def _new_joiner(self, r: dict[str, str]) -> None:
        key = (r["plan_no"], r["department"], r["kit"], r["start_date"])
        loc = self.loc_of(r.get("location_code", ""))
        self.joiners[key] = {"count": D(r["count"]), "loc": loc, "status": "open"}
        for item, per in self.kits[r["kit"]]:
            self.add(
                ("jp", *key, item),
                f"{r['plan_no']}/{r['department']}",
                item,
                (per * D(r["count"])).quantize(Q4),
                date.fromisoformat(r["start_date"]),
                loc,
                "gross",
            )

    # ---- the store check, reservation and cuts ----
    def store_check(self, places: set[tuple[str, str]] | None = None, tag: str | None = None) -> dict:
        """Free stock set aside earliest need-by first; what is still short goes on a new purchase request."""
        if tag is None:
            self.tag += 1
            tag = f"new{self.tag}"
        made: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
        for ln in self.ordered():
            if places is not None and not self.rules.unscoped_check and (ln.item, ln.loc) not in places:
                continue
            short = ln.short()
            if short <= 0:
                continue
            if ln.basis == "gross" or self.rules.net_as_gross:
                take = min(short, max(self.free(ln.item, ln.loc), Z))
                ln.reserved += take
                short -= take
                if short > 0 and self.rules.take_held:
                    short -= self._steal(ln, short)
            if short <= 0:
                continue
            first = next((c for c in ln.covers if c.request == "first" and not c.buyer), None)
            if self.rules.grow_first and first is not None:
                first.qty += short
            else:
                ln.covers.append(Cover(tag, short, False, ln.need_by))
            made[(ln.mr, ln.item)] += short
        return dict(made)

    def _steal(self, ln: Line, short: Decimal) -> Decimal:
        got = Z
        later = [o for o in self.ordered() if o.item == ln.item and o.loc == ln.loc and o.need_by > ln.need_by]
        for o in reversed(later):
            take = min(o.reserved, short - got)
            o.reserved -= take
            got += take
        ln.reserved += got
        return got

    def cut(self, ln: Line, qty: Decimal) -> Decimal:
        """Cut open cover, newest link first."""
        left = qty
        for c in reversed(ln.covers):
            if c.buyer or left <= 0:
                continue
            d = min(c.qty, left)
            c.qty -= d
            left -= d
            if d:
                self.cut_log.append((ln.mr, ln.item, c.request, d))
        return qty - left

    def stock_rose(self, places: set[tuple[str, str]]) -> None:
        """Released stock goes at once to the open lines that need it, earliest need-by first: uncovered need, then cover
        on requests not yet with the buyer (cutting them). Net lines take nothing."""
        for second in (False, True):
            for ln in self.ordered():
                if (ln.item, ln.loc) not in places or (ln.basis == "net" and not self.rules.net_as_gross):
                    continue
                free = self.free(ln.item, ln.loc)
                if free <= 0:
                    continue
                if not second:
                    ln.reserved += min(max(ln.short(), Z), free)
                else:
                    ln.reserved += self.cut(ln, min(ln.open_cover(), free))

    def issue(self, mr: str, item: str | None = None, qty: Decimal | None = None) -> None:
        for ln in self.lines:
            if ln.mr != mr or ln.status != "open" or (item and ln.item != item):
                continue
            q = ln.reserved if qty is None else qty
            ln.issued += q
            ln.reserved -= q
            self.on_hand[(ln.item, ln.loc)] = self.on_hand.get((ln.item, ln.loc), Z) - q
            if ln.issued >= ln.need:
                ln.status = "issued"
        self.settle(mr)

    def count(self, item: str, loc: str, counted: Decimal) -> None:
        self.on_hand[(item, loc)] = counted
        self.stock_rose({(item, loc)})

    def drop_covers(self, mr: str, request: str = "first") -> None:
        """A request rejected: its cover no longer counts."""
        for ln in self.lines:
            if ln.mr == mr:
                ln.covers = [c for c in ln.covers if c.request != request]

    def mark_buyer(self, with_buyer: dict[tuple[str, str], bool]) -> None:
        """Which cover is with the buyer after a setup (sourcing), as the platform's request lines say."""
        for ln in self.lines:
            flag = with_buyer.get((ln.mr, ln.item))
            if flag is not None:
                for c in ln.covers:
                    c.buyer = flag

    # ---- changes (spec 4.2) ----
    def change_need(self, ln: Line, n: Decimal, eff: Effects, doc: str, cancel: bool = False) -> None:
        r = self.rules
        excess = ln.issued + ln.reserved + ln.requested() - n
        if excess > 0:
            if r.keep_sole_pr and cancel:
                d = min(excess, ln.reserved)
                self._release(ln, d, eff)
                excess = Z
            for step in ("release", "cut") if r.release_first else ("cut", "release"):
                if excess <= 0:
                    break
                if step == "cut" and not r.case_for_open:
                    d = self.cut(ln, min(excess, ln.open_cover()))
                    eff.cut[(ln.mr, ln.item)] += d
                    excess -= d
                elif step == "release":
                    d = min(excess, ln.reserved)
                    self._release(ln, d, eff)
                    excess -= d
            cover = ln.buyer_cover() + (ln.open_cover() if r.case_for_open else Z)
            d = min(excess, cover)
            if d > 0:
                eff.cases.append(Case("buyer", ln.mr, ln.item, d, doc))
                excess -= d
            if excess > 0 and not r.no_store_case:
                eff.cases.append(Case("store", ln.mr, ln.item, excess, doc))
        ln.need = n
        if cancel:
            ln.status = "cancelled"
        elif ln.issued > n or (ln.issued and ln.issued >= n):
            ln.status = "issued"
        self.settle(ln.mr)

    def _release(self, ln: Line, qty: Decimal, eff: Effects) -> None:
        if qty <= 0:
            return
        ln.reserved -= qty
        eff.released[ln.item] += qty
        eff.released_places.add((ln.item, ln.loc))

    def raise_need(self, ln: Line, n: Decimal, eff: Effects) -> None:
        ln.need = n
        if ln.status == "issued" and not self.rules.no_reopen:
            ln.status = "open"
        self.settle(ln.mr)
        eff.up_places.add((ln.item, ln.loc))

    def move(self, ln: Line, new: date, eff: Effects) -> str:
        old = ln.need_by
        ln.need_by = new
        if new < old:
            for c in ln.covers:
                if not c.buyer and c.need_by and c.need_by > new:
                    eff.moved.append((ln.mr, ln.item, c.need_by, new))
                    c.need_by = new
            eff.up_places.add((ln.item, ln.loc))
            if self.rules.earlier_steals:
                eff.resort.add((ln.item, ln.loc))
            return "earlier"
        return "later"

    def _qty_and_date(self, ln: Line, n: Decimal, nb: date | None, eff: Effects, doc: str) -> set[str]:
        what: set[str] = set()
        if n < ln.need:
            what.add("qty_down")
            self.change_need(ln, n, eff, doc)
        elif n > ln.need:
            what.add("qty_up")
            self.raise_need(ln, n, eff)
        if nb and nb != ln.need_by:
            what.add(self.move(ln, nb, eff))
        return what

    def _cancel(self, ln: Line, eff: Effects, doc: str) -> None:
        if ln.status == "cancelled":
            return
        if self.rules.ignore_cancel:
            return
        self.change_need(ln, Z, eff, doc, cancel=True)

    def apply(self, files: dict[str, list[dict[str, str]]]) -> Effects:
        """One import: files in KINDS order, rows in file order, then new requests, released stock handed on, and the
        store check for the item-locations that went up or earlier (spec 4.3)."""
        eff = Effects()
        line_orders = {r["order_no"] for r in files.get("production_order_line", [])}
        for kind in KINDS:
            for i, r in enumerate(files.get(kind, []), start=2):
                handler = getattr(self, f"_row_{kind}", None)
                if handler is None:
                    eff.already += 1
                    continue
                handler(r, i, eff, line_orders)
        for mr in list(self.mrs):
            self.settle(mr)
        self.stock_rose(eff.released_places)
        for place in eff.resort:
            held = [ln for ln in self.ordered() if (ln.item, ln.loc) == place and ln.basis == "gross"]
            for ln in held:
                ln.reserved = Z
            for ln in held:
                ln.reserved = min(max(ln.need - ln.issued, Z), max(self.free(*place), Z))
        if eff.up_places:
            for k, v in self.store_check(eff.up_places).items():
                eff.new[k] += v
        return eff

    def _change(self, eff: Effects, kind: str, doc: str, item: str | None, what: set[str]) -> None:
        if what:
            eff.changes.append({"kind": kind, "doc": doc, "item": item, "what": what})
        else:
            eff.already += 1

    def _row_production_order(self, r: dict[str, str], line: int, eff: Effects, line_orders: set[str]) -> None:
        o, st = r["order_no"], norm_status(r.get("status", ""))
        h = self.headers.get(o)
        if h is None:
            if st == "cancelled":
                eff.refused.append(("production_order", line, f"nothing to cancel: {o} was never loaded"))
            else:
                eff.loaded += 1
                self.headers[o] = {
                    "product": r["product_code"],
                    "qty": D(r["qty"]),
                    "need_by": date.fromisoformat(r["need_by"]),
                    "loc": self.loc_of(r.get("location_code", "")),
                    "status": "open",
                }
            return
        if h["status"] == "cancelled":
            if st == "cancelled":
                eff.already += 1
            else:
                eff.refused.append(
                    ("production_order", line, "a cancelled order is not reopened; send a new order number")
                )
            return
        if r["product_code"] != h["product"] or self.loc_of(r.get("location_code", "")) != h["loc"]:
            eff.refused.append(("production_order", line, f"cancel {o} and send a new order"))
            return
        what: set[str] = set()
        if st == "cancelled" and not self.rules.ignore_cancel:
            for ln in self.lines_of(o):
                self._cancel(ln, eff, o)
            h["status"] = "cancelled"
            what.add("cancelled")
        else:
            qty, nb = D(r["qty"]), date.fromisoformat(r["need_by"])
            if qty != h["qty"]:
                what.add("header_qty")
                if o not in line_orders:
                    eff.warnings.append(
                        f"{o}: quantity {fq(h['qty'])} -> {fq(qty)}; its components were not in this import, "
                        "so its material request is unchanged"
                    )
                h["qty"] = qty
            if nb != h["need_by"]:
                what.add("earlier" if nb < h["need_by"] else "later")
                for ln in self.lines_of(o):
                    if ln.status == "open" and ln.need_by == h["need_by"]:
                        self.move(ln, nb, eff)
                h["need_by"] = nb
        self._change(eff, "production_order", o, None, what)

    def _row_production_order_line(self, r: dict[str, str], line: int, eff: Effects, _: set[str]) -> None:
        o, item, st = r["order_no"], r["item_code"], norm_status(r.get("status", ""))
        h = self.headers.get(o)
        if h and h["status"] == "cancelled":
            eff.refused.append(("production_order_line", line, f"{o} is cancelled"))
            return
        n = self.base_qty(item, D(r["qty"]), r.get("unit", ""))
        nb = date.fromisoformat(r["need_by"]) if r.get("need_by") else None
        ln = self.by_key.get(("mo", o, item))
        if ln is None:
            if st == "cancelled" or h is None:
                eff.refused.append(("production_order_line", line, "nothing to cancel" if h else o))
            else:
                eff.loaded += 1
                self.add(("mo", o, item), o, item, n, nb or h["need_by"], h["loc"], "gross")
            return
        if ln.status == "cancelled":
            eff.already += 1
            return
        if st == "cancelled":
            self._cancel(ln, eff, o)
            self._change(eff, "production_order_line", o, item, {"cancelled"})
            return
        self._change(eff, "production_order_line", o, item, self._qty_and_date(ln, n, nb, eff, o))

    def _row_mrp_result(self, r: dict[str, str], line: int, eff: Effects, _: set[str]) -> None:
        key = (r["plan_no"], r["line_no"])
        st, loc = norm_status(r.get("status", "")), self.loc_of(r.get("location_code", ""))
        basis = (r.get("basis") or "gross").lower()
        old = self.mrp.get(key)
        n = self.base_qty(r["item_code"], D(r["qty"]), r.get("unit", ""))
        nb = date.fromisoformat(r["need_by"])
        if old is None:
            if st == "cancelled":
                eff.refused.append(("mrp_result", line, "nothing to cancel"))
                return
            eff.loaded += 1
            self.mrp[key] = {"item": r["item_code"], "loc": loc, "status": "open"}
            if not self.rules.skip_mrp:
                self.add(("mrp", *key), f"{key[0]}/{loc}", r["item_code"], n, nb, loc, basis)
            return
        if r["item_code"] != old["item"] or loc != old["loc"]:
            eff.refused.append(("mrp_result", line, "cancel the line and send a new line number"))
            return
        ln = self.by_key.get(("mrp", *key))
        if ln is None:  # MRP skipped (the mutation)
            eff.already += 1
            return
        if ln.status == "cancelled":
            eff.already += 1
            return
        if st == "cancelled":
            self._cancel(ln, eff, key[0])
            self._change(eff, "mrp_result", key[0], ln.item, {"cancelled"})
            return
        what = set()
        if basis != ln.basis:
            what.add("basis")
            ln.basis = basis
        what |= self._qty_and_date(ln, n, nb, eff, key[0])
        self._change(eff, "mrp_result", key[0], ln.item, what)

    def _row_joiner_plan(self, r: dict[str, str], line: int, eff: Effects, _: set[str]) -> None:
        key = (r["plan_no"], r["department"], r["kit"], r["start_date"])
        st, loc = norm_status(r.get("status", "")), self.loc_of(r.get("location_code", ""))
        old = self.joiners.get(key)
        doc = f"{r['plan_no']}/{r['department']}"
        if old is None:
            if st == "cancelled" and not self.rules.ignore_cancel:
                eff.refused.append(("joiner_plan", line, "nothing to cancel"))
                return
            eff.loaded += 1
            self._new_joiner(r)
            return
        if loc != old["loc"]:
            eff.refused.append(("joiner_plan", line, "location"))
            return
        mine = [self.by_key[("jp", *key, item)] for item, _ in self.kits[r["kit"]] if ("jp", *key, item) in self.by_key]
        if old["status"] == "cancelled":
            eff.already += 1
            return
        if st == "cancelled":
            if self.rules.ignore_cancel:
                eff.already += 1
                return
            for ln in mine:
                self._cancel(ln, eff, doc)
            old["status"] = "cancelled"
            self._change(eff, "joiner_plan", doc, None, {"cancelled"})
            return
        what: set[str] = set()
        count = D(r["count"])
        per = dict(self.kits[r["kit"]])
        for ln in mine:
            what |= self._qty_and_date(ln, (per[ln.item] * count).quantize(Q4), None, eff, doc)
        old["count"] = count
        self._change(eff, "joiner_plan", doc, None, what)

    # ---- what the oracle says ----
    def observe(self) -> dict[tuple[str, str], dict[str, Any]]:
        got: dict[tuple[str, str], dict[str, Any]] = {}
        for ln in self.lines:
            k = (ln.mr, ln.item)
            o = got.setdefault(
                k, {"need": Z, "reserved": Z, "issued": Z, "requested": Z, "open": Z, "buyer": Z, "status": ln.status}
            )
            o["need"] += ln.need
            o["reserved"] += ln.reserved
            o["issued"] += ln.issued
            o["requested"] += ln.requested()
            o["open"] += ln.open_cover()
            o["buyer"] += ln.buyer_cover()
            o["need_by"] = ln.need_by
            o["first"] = o.get("first", Z) + sum((c.qty for c in ln.covers if c.request == "first"), Z)
            o["new"] = o.get("new", Z) + sum((c.qty for c in ln.covers if c.request != "first"), Z)
        return got

    def request_value(self, mr: str, request: str = "first") -> Decimal:
        return money(
            sum(
                (
                    c.qty * self.price(ln.item)
                    for ln in self.lines
                    if ln.mr == mr
                    for c in ln.covers
                    if c.request == request
                ),
                Z,
            )
        )

    def new_value(self, mr: str) -> Decimal:
        return money(
            sum(
                (
                    c.qty * self.price(ln.item)
                    for ln in self.lines
                    if ln.mr == mr
                    for c in ln.covers
                    if c.request != "first"
                ),
                Z,
            )
        )


def differ(
    want: dict[tuple[str, str], dict[str, Any]],
    got: dict[tuple[str, str], dict[str, Any]],
    fields: Iterable[str] = ("need", "reserved", "issued", "requested", "open"),
    only: Iterable[tuple[str, str]] | None = None,
) -> list[str]:
    """Lines where two views disagree, as readable text."""
    keys = sorted(set(want) | set(got)) if only is None else sorted(only)
    wrong = []
    for k in keys:
        a, b = want.get(k), got.get(k)
        if a is None or b is None:
            wrong.append(f"{k[0]} {k[1]}: {'missing on the platform' if b is None else 'not expected'}")
            continue
        # a cancelled line: the cover with the buyer stays on its documents (cases), so only need, reserved and issued
        # are the line's own; what is still requested for it is read from the cases and the requests instead
        use = [f for f in fields if not (a.get("status") == "cancelled" and f in ("requested", "open", "buyer"))]
        bad = [f"{f} {fq(b[f])} (expected {fq(a[f])})" for f in use if D(a.get(f)) != D(b.get(f))]
        if a.get("status") == "cancelled" and b.get("status") != "cancelled":
            bad.append(f"status {b.get('status')} (expected cancelled)")
        if bad:
            wrong.append(f"{k[0]} {k[1]}: " + ", ".join(bad))
    return wrong


def separated(
    right: dict,
    wrong: dict,
    # "first" and "new" (what the first and the new requests buy) tell a grown submitted request from a new one; without
    # them that mutation could never be told apart (eval bug, review of 21 Nov)
    fields: Iterable[str] = ("need", "reserved", "requested", "open", "status", "first", "new"),
) -> bool:
    """Some observed value tells the wrong rule from the right one."""
    fields = tuple(fields)
    if set(right) != set(wrong):
        return True
    return any(
        (str(right[k].get(f)) if f == "status" else D(right[k].get(f)))
        != (str(wrong[k].get(f)) if f == "status" else D(wrong[k].get(f)))
        for k in right
        for f in fields
    )


# ======================================================================================================================
# The platform
# ======================================================================================================================


class Company:
    """A demo company (`demo reset`) in a tenant of its own; its people act through the API with their own logins."""

    def __init__(self, profile: str, prefix: str, client: Any) -> None:
        self.name = profile
        self.prefix = prefix
        self.client = client
        self.slug = ""
        self.db: Any = None
        self.resets = 0
        self.first_requests: set[str] = set()
        self.reset()

    def reset(self) -> None:
        """A fresh demo company (the previous one of this object dropped: demo logins are unique per control database)."""
        from p2p.platform import demo, routing, tenants
        from sqlalchemy.orm import Session

        if self.db is not None:
            self.db.close()
            routing.engine_for(self.slug).dispose()  # no pooled connection may hold the database being dropped
            tenants.drop_tenant(self.slug)
        folder = Path(tempfile.mkdtemp(prefix=f"{self.prefix}_{self.name}_"))
        self.dataset = folder / "dataset"
        self.slug = env.slug(self.prefix)
        demo.reset_company(demo.company(self.name), self.dataset, credentials=folder / "credentials", slug=self.slug)
        self.db = Session(routing.engine_for(self.slug))
        self.password = demo.DEV_PASSWORD
        self.passwords: dict[str, str] = {}  # the logins the reset wrote ("email password # ..."), else the dev one
        creds = folder / "credentials"
        for line in creds.read_text(encoding="utf-8").splitlines() if creds.is_file() else []:
            found = re.match(r"^(\S+@\S+)\s+(\S+)\s+#", line)
            if found:
                self.passwords[found.group(1)] = found.group(2)
        self.people = {u["code"]: u for u in self.rows("master", "user")}
        self.roles: dict[str, set[str]] = defaultdict(set)
        for r in self.rows("master", "user_role"):
            self.roles[r["user_code"]].add(r["role"])
        self._headers: dict[str, dict[str, str]] = {}
        self._ids: dict[str, int] = {}
        self.first_requests = set()
        self.resets += 1

    # the company's own dataset files (the oracles read these)
    def rows(self, folder: str, name: str) -> list[dict[str, str]]:
        return read_csv(self.dataset / folder / f"{name}.csv")

    def holders(self, role: str) -> list[str]:
        return sorted(c for c, held in self.roles.items() if role in held)

    def location(self) -> dict[str, str]:
        return self.rows("master", "location")[0]

    def calendar(self) -> Calendar:
        loc = self.location()
        return Calendar(
            frozenset(open_days(loc.get("working_days", ""))), frozenset(holidays(loc.get("holiday_scope", "")))
        )

    def scenario(self, label: str) -> Path | None:
        """The change scenario folder `changes/<label>-...` (or exactly `<label>`)."""
        base = self.dataset / "changes"
        if not base.is_dir():
            return None
        found = sorted(p for p in base.iterdir() if p.is_dir() and (p.name == label or p.name.startswith(label + "-")))
        return found[0] if found else None

    def truth(self, label: str) -> list[dict[str, str]]:
        return [
            r
            for r in self.rows("truth", "truth_demand_change")
            if r.get("scenario", "") == label or r.get("scenario", "").startswith(label + "-")
        ]

    # people and calls
    def headers(self, code: str) -> dict[str, str]:
        if code not in self._headers:
            email = self.people[code]["email"]
            r = self.client.post(
                "/api/auth/login", json={"email": email, "password": self.passwords.get(email, self.password)}
            )
            self._headers[code] = {"Authorization": "Bearer " + str(js(r).get("access_token", ""))}
        return self._headers[code]

    def call(self, code: str, method: str, path: str, body: Any = None, **kw: Any) -> Any:
        self.db.rollback()
        r = self.client.request(method, path, json=body, headers=self.headers(code), **kw)
        self.db.rollback()
        return r

    def user_id(self, code: str) -> int:
        if code not in self._ids:
            from p2p.platform import tenants

            with tenants.control_engine().connect() as c:
                found = c.execute(
                    text(
                        "select u.id from app_user u join tenant t on t.id = u.tenant_id "
                        "where t.slug = :s and lower(u.email) = lower(:e)"
                    ),
                    {"s": self.slug, "e": self.people[code]["email"]},
                ).scalar()
            self._ids[code] = int(found or 0)
        return self._ids[code]

    def code_of(self, ref: Any) -> str | None:
        """A person named by user id, code, email or name."""
        for code, u in self.people.items():
            if str(ref) in (code, u["email"], u["name"]) or (str(ref).isdigit() and int(ref) == self.user_id(code)):
                return code
        return None

    def q(self, sql: str, **p: Any) -> list[Any]:
        self.db.rollback()
        found = list(self.db.execute(text(sql), p).all())
        self.db.rollback()
        return found

    def one(self, sql: str, **p: Any) -> Any:
        found = self.q(sql, **p)
        return found[0][0] if found else None

    def columns(self, table: str) -> set[str]:
        return {
            r[0] for r in self.q("select column_name from information_schema.columns where table_name = :t", t=table)
        }

    def audit_count(self, action: str) -> int:
        # a task.create counts only for a demand case: a request made by the store check after an increase opens its
        # own approval task (spec 4.2 "up ... with submit"), which is not a case (review of 21 Nov, eval bug)
        if action == "task.create":
            sql = "select count(*) from audit_event where action = :a and after->>'kind' = 'demand_change'"
            return int(self.one(sql, a=action) or 0)
        return int(self.one("select count(*) from audit_event where action = :a", a=action) or 0)

    def audit_rows(self, action: str, after_id: int = 0) -> list[Any]:
        return self.q(
            "select id, actor_type, actor_id, entity_id, after::text from audit_event where action = :a and id > :i "
            "order by id",
            a=action,
            i=after_id,
        )

    def last_audit_id(self) -> int:
        return int(self.one("select coalesce(max(id), 0) from audit_event") or 0)

    def work(self, rounds: int = 80) -> None:
        from p2p.platform import worker

        self.db.commit()
        for _ in range(rounds):
            if not worker.run_once():
                break
        self.db.rollback()

    # ---- the flow, as people do it ----
    def store_check(self) -> Any:
        r = self.call(self.holders("store")[0], "POST", "/api/material-requests/check", {})
        return r

    def base_state(self) -> Any:
        """The base state of every scenario: demo reset, then Run store check (A0 / B0). Remembers the requests made."""
        r = self.store_check()
        self.first_requests = {n for (n,) in self.q("select distinct request_no from material_request_buy")}
        return r

    def approve(self, no: str) -> str:
        """A request approved through its chain, each step by whoever may approve it."""
        approvers = self.holders("approver")
        status = ""
        for _ in range(6):
            status = str(self.one("select status from request where no = :n", n=no) or "")
            if status != "submitted":
                break
            for code in approvers:
                if self.call(code, "POST", f"/api/requisitions/{no}/approve", {}).status_code == 200:
                    break
        return str(self.one("select status from request where no = :n", n=no) or "")

    def source(self) -> None:
        """Sourcing and ordering as the spec's setup names them (`p2p.cli source`, the worker, `p2p.cli order`)."""
        cli("source", self.slug)
        self.work()
        cli("order", self.slug)
        self.work()

    def upload(self, by: str, kind: str, body: bytes, dry_run: bool = False) -> tuple[int, dict[str, Any]]:
        path = f"/api/demand/import/{kind}" + ("?dry_run=true" if dry_run else "")
        r = self.call(by, "POST", path, files={"file": (f"{kind}.csv", body, "text/csv")})
        return r.status_code, (js(r) if r.status_code == 200 else {"text": r.text[:300]})

    def import_files(
        self, files: dict[str, bytes], dry_run: bool = False, by: str | None = None
    ) -> list[dict[str, Any]]:
        """Upload the files one by one in the order the spec names (KINDS), as the store."""
        who = by or self.holders("store")[0]
        out = []
        for kind in KINDS:
            if kind in files:
                status, body = self.upload(who, kind, files[kind], dry_run)
                out.append({"kind": kind, "status": status, **body})
        return out

    # ---- what the platform holds ----
    def mr_labels(self) -> list[tuple[str, str, str]]:
        """(label, material request no, status) in the order made; a second request of one order is '<order>#2'."""
        seen: dict[str, int] = defaultdict(int)
        out = []
        for no, order, status in self.q("select no, order_no, status from material_request order by created_at, no"):
            seen[order] += 1
            out.append((order if seen[order] == 1 else f"{order}#{seen[order]}", no, status))
        return out

    def observe(self) -> dict[tuple[str, str], dict[str, Any]]:
        label_of = {no: label for label, no, _ in self.mr_labels()}
        mr_status = {no: st for _, no, st in self.mr_labels()}
        lines = self.q(
            "select l.id, l.mr_no, l.item_code, l.need_qty, l.reserved_qty, l.issued_qty, l.status, l.need_by "
            "from material_request_line l order by l.mr_no, l.line_no"
        )
        cover: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
        for mr, item, requested in self.q("select mr_no, item_code, requested_qty from material_request_cover"):
            cover[(mr, item)] += D(requested)
        buys: dict[int, list[tuple[str, Decimal, bool, bool]]] = defaultdict(list)
        for mr_line, req, qty, line_status, req_status in self.q(
            "select b.mr_line_id, b.request_no, b.qty, rl.status, r.status from material_request_buy b "
            "join request_line rl on rl.request_no = b.request_no and rl.line_no = b.request_line_no "
            "join request r on r.no = b.request_no"
        ):
            live = req_status not in ("cancelled", "rejected") and line_status != "cancelled"
            buys[mr_line].append((req, D(qty), live and line_status == "open" and req_status in LIVE_REQUEST,
                                  live and line_status in WITH_BUYER))  # fmt: skip
        got: dict[tuple[str, str], dict[str, Any]] = {}
        for lid, mr, item, need, reserved, issued, status, need_by in lines:
            k = (label_of.get(mr, mr), item)
            b = buys.get(lid, [])
            o = got.setdefault(
                k,
                {"need": Z, "reserved": Z, "issued": Z, "requested": Z, "open": Z, "buyer": Z, "first": Z, "new": Z},
            )
            o.update(status=status, need_by=need_by, mr_no=mr, mr_status=mr_status.get(mr), line_id=lid)
            # a cancelled line needs nothing; the platform keeps its last need on record because need_qty must stay
            # above zero (migration 0045), so it is read as 0 (spec silent on the stored value; review of 21 Nov)
            o["need"] += Z if status == "cancelled" else D(need)
            o["reserved"] += D(reserved)
            o["issued"] += D(issued)
            o["requested"] += cover.pop((mr, item), Z)
            o["open"] += sum((q for _, q, op, _ in b if op), Z)
            o["buyer"] += sum((q for _, q, _, by in b if by), Z)
            o["first"] += sum((q for r, q, op, by in b if (op or by) and r in self.first_requests), Z)
            o["new"] += sum((q for r, q, op, by in b if (op or by) and r not in self.first_requests), Z)
        return got

    def cover_view_ok(self) -> list[str]:
        """The view's requested quantity against the request lines that buy for each line (nothing has arrived)."""
        wrong = []
        for lid, item, viewed, linked in self.q(
            "select c.mr_no, c.item_code, c.requested_qty, coalesce(sum(case when r.status not in ('cancelled', "
            "'rejected') and rl.status <> 'cancelled' then b.qty else 0 end), 0) from material_request_cover c "
            "join material_request_line l on l.mr_no = c.mr_no and l.item_code = c.item_code "
            "left join material_request_buy b on b.mr_line_id = l.id "
            "left join request_line rl on rl.request_no = b.request_no and rl.line_no = b.request_line_no "
            "left join request r on r.no = b.request_no group by c.mr_no, c.item_code, c.requested_qty"
        ):
            if D(viewed) != D(linked):
                wrong.append(f"line {lid} {item}: view {fq(D(viewed))}, request lines {fq(D(linked))}")
        return wrong

    def with_buyer(self) -> dict[tuple[str, str], bool]:
        got = self.observe()
        return {k: v["buyer"] > 0 and v["open"] == 0 for k, v in got.items()}

    def request_of(self, label: str, first: bool = True) -> list[str]:
        no = dict((lbl, n) for lbl, n, _ in self.mr_labels()).get(label)
        found = [
            r[0]
            for r in self.q(
                "select distinct b.request_no from material_request_buy b join material_request_line l on l.id = b.mr_line_id "
                "where l.mr_no = :m",
                m=no,
            )
        ]
        return sorted(r for r in found if (r in self.first_requests) == first)

    def request(self, no: str) -> dict[str, Any]:
        r = self.q("select status, total_value, need_by from request where no = :n", n=no)
        return {"status": r[0][0], "total": D(r[0][1]), "need_by": r[0][2]} if r else {}

    def snapshot(self) -> dict[str, Any]:
        """What a second import or a dry run must leave as it was."""
        return {
            "lines": sorted(
                tuple(str(x) for x in r)
                for r in self.q(
                    "select mr_no, item_code, need_qty, reserved_qty, issued_qty, status, need_by from material_request_line"
                )
            ),
            "mrs": sorted(tuple(str(x) for x in r) for r in self.q("select no, status, need_by from material_request")),
            "requests": sorted(tuple(str(x) for x in r) for r in self.q("select no, status, total_value from request")),
            "request_lines": sorted(
                tuple(str(x) for x in r)
                for r in self.q("select request_no, line_no, qty, status, need_by from request_line")
            ),
            "requirements": sorted(
                tuple(str(x) for x in r)
                for r in self.q("select id, qty, base_qty, need_by, status from material_requirement")
            ),
            "tasks": int(self.one("select count(*) from task") or 0),
            "changes": int(self.one("select count(*) from demand_change") or 0)
            if "demand_change" in self.tables()
            else -1,
            "audit": {
                a: self.audit_count(a) for a in (*AUDITED, "material_request.create", "material_request.add_lines")
            },
        }

    def tables(self) -> set[str]:
        return {r[0] for r in self.q("select table_name from information_schema.tables where table_schema = 'public'")}

    def cases(self) -> list[dict[str, Any]]:
        if "demand_change_case" not in self.tables():
            return []
        found = self.q(
            "select t.id, t.kind, t.title, t.assignee, t.status, t.due_at, t.created_at, t.escalation_chain::text, "
            "c.owner_kind, c.doc_type, c.doc_no, c.excess::text, c.amount, c.outcome, c.change_id, c.lines::text "
            "from demand_change_case c join task t on t.id = c.task_id order by t.id"
        )
        keys = (
            "task_id", "kind", "title", "assignee", "status", "due_at", "created_at", "chain",
            "owner_kind", "doc_type", "doc_no", "excess", "amount", "outcome", "change_id", "lines",
        )  # fmt: skip
        out = []
        for r in found:
            c = dict(zip(keys, r, strict=True))
            c["excess"] = {k: D(v) for k, v in (json.loads(c["excess"] or "{}") or {}).items()}
            c["chain"] = json.loads(c["chain"] or "[]")
            out.append(c)
        return out

    def changes(self, by: str, **params: Any) -> tuple[int, list[dict[str, Any]]]:
        qs = "&".join(f"{k}={v}" for k, v in params.items() if v is not None)
        r = self.call(by, "GET", "/api/demand/changes" + (f"?{qs}" if qs else ""))
        return r.status_code, list(js(r).get("changes") or []) if r.status_code == 200 else []

    def exceptions(self, by: str, mine: bool = False) -> list[dict[str, Any]]:
        r = self.call(by, "GET", "/api/exceptions?kind=demand" + ("&mine=true" if mine else ""))
        return [x for x in (js(r).get("rows") or []) if isinstance(x, dict)] if r.status_code == 200 else []

    def doc_of(self, request_no: str, line_no: int) -> tuple[str, str]:
        """The purchase document holding a request line: its order, else its quotation request, else the request."""
        po = self.one(
            "select pl.po_no from po_line pl join po p on p.no = pl.po_no where pl.request_no = :r "
            "and pl.request_line_no = :l and p.status <> 'cancelled' order by pl.po_no desc limit 1",
            r=request_no,
            l=line_no,
        )
        if po:
            return "po", str(po)
        rfq = self.one(
            "select rfq_no from rfq_line where request_no = :r and request_line_no = :l order by rfq_no desc limit 1",
            r=request_no,
            l=line_no,
        )
        return ("rfq", str(rfq)) if rfq else ("request", request_no)

    def buyer_docs(self, label: str) -> dict[str, tuple[str, str, Decimal]]:
        """Item -> (doc type, doc no, unit price) for the lines with the buyer that buy for a material request."""
        no = dict((lbl, n) for lbl, n, _ in self.mr_labels()).get(label)
        out = {}
        for item, req, line, est in self.q(
            "select l.item_code, b.request_no, b.request_line_no, rl.est_price from material_request_buy b "
            "join material_request_line l on l.id = b.mr_line_id join request_line rl on rl.request_no = b.request_no "
            "and rl.line_no = b.request_line_no where l.mr_no = :m and rl.status in ('sourcing', 'ordered') and b.qty > 0",
            m=no,
        ):
            kind, doc = self.doc_of(req, line)
            price = D(est)
            if kind == "po":
                price = D(
                    self.one(
                        "select unit_price from po_line where po_no = :p and request_no = :r and request_line_no = :l",
                        p=doc,
                        r=req,
                        l=line,
                    )
                )
            out[item] = (kind, doc, price)
        return out


def summary_of(responses: list[dict[str, Any]]) -> dict[str, Any]:
    """The sum of several import answers: loaded, changed, already loaded, left out, changes, warnings."""
    agg: dict[str, Any] = {
        "loaded": 0,
        "changed": 0,
        "already_loaded": 0,
        "rejected": [],
        "changes": [],
        "warnings": [],
    }
    for r in responses:
        for k in ("loaded", "changed", "already_loaded"):
            agg[k] += int(r.get(k) or 0)
        agg["rejected"] += [dict(x, kind=r["kind"]) for x in r.get("rejected") or []]
        agg["changes"] += list(r.get("changes") or [])
        agg["warnings"] += [str(w) for w in r.get("warnings") or []]
    agg["statuses"] = [r["status"] for r in responses]
    return agg


def released_by(changes: list[dict[str, Any]]) -> dict[str, Decimal]:
    got: dict[str, Decimal] = defaultdict(Decimal)
    for c in changes:
        for item, q in (c.get("released") or {}).items():
            got[item] += D(q)
    return {k: v for k, v in got.items() if v}


def what_of(changes: list[dict[str, Any]]) -> dict[tuple[str, str | None], set[str]]:
    """(document, item or None) -> the change kinds the platform reported."""
    got: dict[tuple[str, str | None], set[str]] = defaultdict(set)
    for c in changes:
        got[(str(c.get("doc_no")), c.get("item_code"))] |= set(c.get("what") or [])
    return got


def files_of(folder: Path) -> tuple[dict[str, bytes], dict[str, list[dict[str, str]]]]:
    raw = {k: (folder / f"{k}.csv").read_bytes() for k in KINDS if (folder / f"{k}.csv").exists()}
    parsed = {k: read_csv(folder / f"{k}.csv") for k in raw}
    return raw, parsed


def compare_truth(
    check: Checker, co: Company, label: str, before: dict, afters: list[dict], eff: Effects, missing: str = "medium"
) -> int:
    """The dataset's answer key (truth_demand_change.csv) against the oracle, row by row. `afters` are the oracle's
    states the key may describe (right after the import, or after the next store check where the spec names one).
    Rows of the same material request and item (a moved start date) are matched in the order the requests were made."""
    rows = co.truth(label)
    if not check(bool(rows), f"{label}: truth_demand_change.csv has no rows for this scenario", missing):
        return 0
    buyer: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    store: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    for c in eff.cases:
        (buyer if c.owner == "buyer" else store)[(c.mr, c.item)] += c.qty
    seen: dict[tuple[str, str], int] = defaultdict(int)
    wrong = []
    for r in rows:
        base = (r["mr_order_no"], r["item_code"])
        seen[base] += 1
        k = base if seen[base] == 1 else (f"{base[0]}#{seen[base]}", base[1])
        b = before.get(k, {})
        for col in r:
            if col in ("scenario", "mr_order_no", "item_code", "location_code") or dec(r[col]) is None:
                continue
            options: set[Decimal] = set()
            for a in afters:
                a = a.get(k, {})
                options |= {
                    "need_before": {b.get("need", Z)},
                    "need_after": {a.get("need", Z)},
                    "reserved_before": {b.get("reserved", Z)},
                    "reserved_after": {a.get("reserved", Z)},
                    "open_request_before": {b.get("open", Z), b.get("requested", Z)},  # read either way: the key's
                    "open_request_after": {a.get("open", Z), a.get("requested", Z)},  # word may count all cover
                    "buyer_case_qty": {buyer.get(k, Z)},
                    "store_case_qty": {store.get(k, Z)},
                    "new_request_qty": {eff.new.get(k, Z), D(a.get("new")) - D(b.get("new"))},
                }.get(col, set())
            if options and D(r[col]) not in options:
                wrong.append(f"{k[0]} {k[1]} {col}: truth {r[col]}, oracle {sorted(fq(o) for o in options)}")
    check(not wrong, f"{label}: the answer key disagrees with the rules for {len(wrong)} value(s); first: {wrong[:3]}",
          "medium")  # fmt: skip
    return len(rows)


def isolation(check: Checker, mine: Company, other: Company, docs: set[str], other_before: dict[str, Any]) -> None:
    """The other company sees none of this company's changes, cannot close its cases, and nothing of its own moved."""
    by = other.holders("store")[0]
    status, seen = other.changes(by)
    check(status == 200, f"{other.name}: GET /api/demand/changes answered {status}")
    leaked = [c for c in seen if str(c.get("doc_no")) in docs]
    check(not leaked, f"{other.name} lists {len(leaked)} change(s) of {mine.name}: {leaked[:2]}")
    for d in sorted(docs)[:3]:
        _, rows = other.changes(by, doc_no=d)
        check(not rows, f"{other.name} finds {mine.name}'s {d} in its changes")
    theirs = {c["task_id"] for c in other.cases()}
    for c in mine.cases()[:3]:
        if c["task_id"] in theirs:
            continue  # the same number is one of its own cases: a 404 would not show anything
        for code in (other.holders("buyer")[0], other.holders("store")[0]):
            r = other.call(code, "POST", f"/api/demand/cases/{c['task_id']}/close", {"outcome": "reduced", "note": "x"})
            check(r.status_code == 404, f"{other.name}'s {code} closing {mine.name}'s case {c['task_id']} answered "
                  f"{r.status_code}")  # fmt: skip
    after = other.snapshot()
    check(after == other_before, f"{mine.name}'s import changed {other.name}'s data: "
          f"{[k for k in after if after[k] != other_before.get(k)]}")  # fmt: skip


def owner_name(co: Company, owner_kind: str) -> str:
    """The case owner the spec names: the buyer of the document (the demo companies have one buyer), or the first
    holder of material.manage (the store)."""
    return co.holders("buyer" if owner_kind == "buyer" else "store")[0]


def check_case_task(check: Checker, co: Company, c: dict[str, Any], anchors: Iterable[date] = ()) -> str:
    """Owner, escalation and due date of one case. Returns which calendar the due date fits ('right', 'both')."""
    owner = owner_name(co, c["owner_kind"])
    check(co.code_of(c["assignee"]) == owner, f"case {c['task_id']} ({c['doc_no']}) is assigned to "
          f"{co.code_of(c['assignee']) or c['assignee']}, expected {co.people[owner]['name']}")  # fmt: skip
    manager = co.people[owner].get("manager_code", "")
    first = c["chain"][0] if c["chain"] else None
    if isinstance(first, dict):
        first = first.get("user_id") or first.get("id") or first.get("code") or first.get("email")
    check(bool(manager) and co.code_of(first) == manager, f"case {c['task_id']} escalates to {first}, expected "
          f"{co.people.get(manager, {}).get('name')} (the owner's manager)")  # fmt: skip
    check(c["kind"] == "demand_change", f"case {c['task_id']} task kind {c['kind']}, expected demand_change")
    cal = co.calendar()
    days = int(
        D(next((r["value"] for r in co.rows("master", "setting") if r["key"] == "demand_change_case_days"), "2"))
    )
    right = due_ok(cal, c["created_at"], c["due_at"], days, anchors)
    wrong = due_ok(WRONG_CALENDAR, c["created_at"], c["due_at"], days, anchors)
    check(
        right,
        f"case {c['task_id']} made {c['created_at']} is due {c['due_at']}, not {days} working days later on "
        f"the company calendar ({co.location().get('working_days')}, holidays {co.location().get('holiday_scope')})",
    )
    return "both" if right and wrong else "right" if right else "wrong"


# ======================================================================================================================
# The spec's own numbers (sections 3.2, 6.3, 6.4): the oracle must reproduce them from the files, or the eval says so
# ======================================================================================================================

MRP_CKN = "MRP-2027-01/PLT-CKN"
MRP_SND = "MRP-2027-01/PLT-SND"
ADM, ENG = "JP-2026-12/ADM", "JP-2026-12/ENG"
FG01 = ("RM-01", "RM-03", "RM-05", "RM-06", "RM-07", "RM-08", "RM-09", "RM-10", "RM-11", "PK-01")
FG02 = ("RM-02", "RM-04", "RM-05", "RM-06", "RM-07", "RM-08", "RM-09", "RM-10", "RM-11", "PK-01")
PER_FG01 = dict(zip(FG01, ("1", "1", "1", "2", "1", "1.8", "1", "1", "0.2", "1"), strict=True))
PER_FG02 = dict(zip(FG02, ("1", "1", "1", "2", "1", "1.1", "1", "1", "0.15", "1"), strict=True))
UNIT = {"RM-08": "KGS", "RM-11": "LTR"}


def _t(spec: str) -> dict[str, tuple[Decimal, Decimal]]:
    """'RM-01 0/50, RM-03 50/0' -> {item: (set aside, on a purchase request)}."""
    out = {}
    for part in spec.split(","):
        item, _, pair = part.strip().partition(" ")
        a, _, b = pair.partition("/")
        out[item] = (Decimal(a), Decimal(b))
    return out


SPEC_STOCK = {
    "apex": {"RM-01": "0", "RM-02": "0", "RM-03": "60", "RM-04": "0", "RM-05": "60", "RM-06": "40", "RM-07": "60",
             "RM-08": "50", "RM-09": "60", "RM-10": "200", "RM-11": "30", "PK-01": "20"},
    "brightwave": {"LP-01": "3", "LP-02": "1", "MN-01": "2", "DK-01": "2", "HS-01": "5", "KB-01": "6", "SW-01": "0"},
}  # fmt: skip
SPEC_A0 = {
    "MO-1": _t("RM-01 0/50, RM-03 50/0, RM-05 50/0, RM-06 40/60, RM-07 50/0, RM-08 50/40, RM-09 50/0, RM-10 50/0, "
               "RM-11 10/0, PK-01 20/30"),
    "MO-2": _t("RM-02 0/100, RM-04 0/100, RM-05 10/90, RM-06 0/200, RM-07 10/90, RM-08 0/110, RM-09 10/90, RM-10 100/0, "
               "RM-11 15/0, PK-01 0/100"),
    MRP_CKN: _t("RM-01 0/120, RM-03 10/110, RM-05 0/120, RM-06 0/240, RM-07 0/120, RM-08 0/216, RM-09 0/120, RM-10 50/70, "
                "RM-11 5/19, PK-01 0/120"),
}  # fmt: skip
SPEC_A0_VALUES = {"MO-1": Decimal("72930"), "MO-2": Decimal("209700"), MRP_CKN: Decimal("369490")}
SPEC_B0 = {
    ADM: _t("LP-01 2/0, MN-01 2/0, DK-01 2/0, HS-01 2/0, KB-01 2/0, SW-01 0/2"),
    ENG: _t("LP-02 1/3, MN-01 0/4, DK-01 0/4, HS-01 3/1, KB-01 4/0, SW-01 0/4"),
}
SPEC_B0_VALUES = {ADM: Decimal("16800"), ENG: Decimal("440200")}


def _lines(order: str, per: dict[str, str], pumps: int, need_by: str) -> list[dict[str, str]]:
    return [
        {"order_no": order, "item_code": i, "qty": fq(Decimal(q) * pumps), "unit": UNIT.get(i, "NOS"), "need_by": need_by,
         "status": ""}
        for i, q in per.items()
    ]  # fmt: skip


def _po(order: str, product: str, qty: int, need_by: str, reason: str, loc: str = "PLT-CKN", status: str = "") -> dict:
    return {"order_no": order, "product_code": product, "qty": str(qty), "need_by": need_by, "reason": reason,
            "location_code": loc, "status": status}  # fmt: skip


def _mrp(first: int, loc: str, pumps: int, basis: str, need_by: str) -> list[dict[str, str]]:
    return [
        {"plan_no": "MRP-2027-01", "line_no": str(first + n), "item_code": i, "qty": fq(Decimal(q) * pumps),
         "unit": UNIT.get(i, "NOS"), "need_by": need_by, "location_code": loc, "basis": basis, "status": ""}
        for n, (i, q) in enumerate(PER_FG01.items())
    ]  # fmt: skip


def _jp(dept: str, kit: str, count: int, start: str, status: str = "") -> dict[str, str]:
    return {"plan_no": "JP-2026-12", "department": dept, "kit": kit, "count": str(count), "start_date": start,
            "location_code": "OFF-BLR", "status": status}  # fmt: skip


SPEC_FILES: dict[str, dict[str, list[dict[str, str]]]] = {
    "A1": {"production_order": [_po("MO-2", "FG-02", 60, "2026-12-15", "DP-2026-12")],
           "production_order_line": _lines("MO-2", PER_FG02, 60, "2026-12-15")},
    "A2": {"production_order": [_po("MO-1", "FG-01", 50, "2026-12-05", "SO-1001", status="cancelled")]},
    "A3": {"production_order": [_po("MO-2", "FG-02", 100, "2026-12-03", "DP-2026-12")],
           "production_order_line": _lines("MO-2", PER_FG02, 100, "2026-12-03")},
    "A4": {"production_order": [_po("MO-1", "FG-01", 60, "2026-12-05", "SO-1001")],
           "production_order_line": _lines("MO-1", PER_FG01, 60, "2026-12-05")},
    "A5": {"production_order": [_po("MO-1", "FG-01", 20, "2026-12-05", "SO-1001")],
           "production_order_line": _lines("MO-1", PER_FG01, 20, "2026-12-05")},
    "M2": {"mrp_result": _mrp(1, "PLT-CKN", 100, "gross", "2027-01-10") + _mrp(11, "PLT-SND", 20, "net", "2027-01-15")},
    "R1": {"production_order": [
        _po("MO-1", "FG-01", 50, "2026-12-05", "SO-1001", loc="PLT-SND"),
        _po("MO-2", "FG-01", 100, "2026-12-15", "DP-2026-12"),
        _po("MO-9", "FG-01", 10, "2026-12-20", "", status="cancelled"),
        _po("MO-2", "FG-02", 100, "2026-12-18", "DP-2026-12")]},
    "B1": {"joiner_plan": [_jp("ADM", "standard", 1, "2026-12-01")]},
    "B2": {"joiner_plan": [_jp("ENG", "developer", 4, "2026-12-01", "cancelled")]},
    "B3": {"joiner_plan": [_jp("ENG", "developer", 5, "2026-12-01")]},
    "B4": {"joiner_plan": [_jp("ADM", "standard", 2, "2026-12-01", "cancelled"), _jp("ADM", "standard", 2, "2026-11-30")]},
}  # fmt: skip
SPEC_FILES["A5b"] = SPEC_FILES["A2"]


def same_rows(got: dict[str, list[dict[str, str]]], want: dict[str, list[dict[str, str]]]) -> list[str]:
    """A scenario's change files against the spec's rows: the same kinds and rows, quantities compared as numbers,
    rows matched by their ERP key (the order of rows inside a file is the generator's)."""

    def key(kind: str, r: dict[str, str]) -> tuple[str, ...]:
        return tuple(r.get(k, "") for k in ("order_no", "item_code", "plan_no", "line_no", "department", "start_date",
                                              "location_code", "product_code", "status"))  # fmt: skip

    wrong = []
    for kind in sorted(set(got) | set(want)):
        a = {key(kind, r): r for r in want.get(kind, [])}
        b = {key(kind, r): r for r in got.get(kind, [])}
        if set(a) != set(b):
            wrong.append(f"{kind}: rows {sorted(set(a) ^ set(b))[:3]} differ")
            continue
        for k, r in a.items():
            for col in ("qty", "count", "need_by", "basis", "unit"):
                if col in r and col in b[k] and (dec(r[col]) != dec(b[k][col]) if col in ("qty", "count")
                                                 else (r[col] or "").lower() != (b[k][col] or "").lower()):  # fmt: skip
                    if col == "basis" and not r[col] and (b[k][col] or "gross").lower() == "gross":
                        continue
                    wrong.append(f"{kind} {k[:2]} {col}: {b[k][col]} (spec {r[col]})")
    return wrong


# ======================================================================================================================
# Checks shared by the evals
# ======================================================================================================================


def start(co: Company, check: Checker, rules: Rules = RIGHT) -> tuple[Plant, dict[str, int]]:
    """The base state on the platform (Run store check) and in the oracle; returns the oracle and the order the
    platform made the material requests in (the tie-break the spec calls "made first")."""
    r = co.base_state()
    check(r.status_code == 200, f"{co.name}: Run store check answered {r.status_code}: {r.text[:200]}")
    rank = {label: i for i, (label, _, _) in enumerate(co.mr_labels())}
    p = Plant(co.dataset, rules, rank)
    p.store_check(tag="first")
    return p, rank


def agree(
    check: Checker,
    label: str,
    plant: Plant,
    co: Company,
    fields: Iterable[str] = ("need", "reserved", "issued", "requested", "open"),
    only: Iterable[tuple[str, str]] | None = None,
) -> bool:
    wrong = differ(plant.observe(), co.observe(), fields, only)
    return check(
        not wrong, f"{label}: {len(wrong)} material request line(s) differ from the oracle; first: {wrong[:3]}"
    )


def check_stock(check: Checker, co: Company, plant: Plant) -> None:
    """The shelf the oracle starts from is the shelf the platform has, and the spec's own figures (section 3.2/6.4)."""
    got = {(i, loc): D(q) for i, loc, q in co.q("select item_code, location_code, on_hand from stock_level")}
    wrong = [f"{k}: {fq(got.get(k, Z))} vs replayed {fq(v)}" for k, v in plant.on_hand.items() if got.get(k, Z) != v]
    check(not wrong, f"{co.name}: stock loaded differs from the replayed history for {wrong[:3]}")
    spec = {i: Decimal(q) for i, q in SPEC_STOCK[co.name].items()}
    mine = {i: plant.on_hand.get((i, plant.first_loc), Z) for i in spec}
    check(mine == spec, f"{co.name}: the replayed stock {mine} is not the spec's {spec} (oracle self-check)", "medium")


def check_base(check: Checker, co: Company, plant: Plant, spec: dict, values: dict[str, Decimal]) -> None:
    """The base state (A0 or B0): oracle against the spec's table, platform against the oracle, request values."""
    obs = plant.observe()
    mine = {mr: {i: (v["reserved"], v["requested"]) for (m, i), v in obs.items() if m == mr} for mr in spec}
    off = {mr: {i: tuple(map(fq, v)) for i, v in t.items() if spec[mr].get(i) != v} for mr, t in mine.items()}
    check(
        mine == spec, f"{co.name}: the oracle's base state is not the spec's table (oracle self-check): {off}", "medium"
    )
    agree(check, f"{co.name} base state", plant, co)
    for mr, value in values.items():
        mine_value = plant.request_value(mr)
        check(
            mine_value == value, f"{mr}: oracle request value {mine_value}, spec {value} (oracle self-check)", "medium"
        )
        reqs = co.request_of(mr)
        got = sum((co.request(n).get("total", Z) for n in reqs), Z)
        check(
            len(reqs) == 1 and money(got) == mine_value,
            f"{mr}: purchase request(s) {reqs} worth {got}, expected one worth {mine_value}",
        )
    over = co.q(
        "select l.item_code, l.location_code from material_request_line l left join stock_level s on s.item_code = "
        "l.item_code and s.location_code = l.location_code group by l.item_code, l.location_code, s.on_hand "
        "having sum(l.reserved_qty) > coalesce(s.on_hand, 0)"
    )
    check(not over, f"{co.name}: more set aside than on hand for {over}")
    bad = co.cover_view_ok()
    check(not bad, f"{co.name}: material_request_cover disagrees with the request lines: {bad[:3]}", "medium")


def mutation(check: Checker, out: Any, name: str, right: dict, wrong: dict, extra: bool = False) -> bool:
    """A deliberately wrong rule must be told apart from the right one by some case (that the platform matches the
    right one is checked by the comparisons themselves)."""
    told = separated(right, wrong) or extra
    check(told, f"mutation '{name}': no case of this eval tells it from the right rule", "medium")
    out.metrics.setdefault("mutations caught", []).append(name if told else f"{name} (NOT separated)")
    return told


def import_change(co: Company, check: Checker, label: str, raw: dict[str, bytes], dry_run: bool = False) -> dict:
    answers = co.import_files(raw, dry_run=dry_run)
    for a in answers:
        check(
            a["status"] == 200,
            f"{label}: importing {a['kind']}{' (dry run)' if dry_run else ''} answered {a['status']}: "
            f"{str(a.get('text', ''))[:200]}",
        )
    return summary_of(answers)


def scenario_files(co: Company, check: Checker, label: str) -> tuple[dict[str, bytes], dict[str, list[dict[str, str]]]]:
    folder = co.scenario(label)
    if folder is None:
        check(False, f"{co.name}: the dataset has no changes/{label}-... folder (spec 6.2)")
        return {}, {}
    raw, parsed = files_of(folder)
    wrong = same_rows(parsed, SPEC_FILES.get(label, {}))
    check(not wrong, f"{label}: the change files differ from the spec's rows: {wrong[:3]}", "medium")
    return raw, parsed


def new_cases(co: Company, before: set[int]) -> list[dict[str, Any]]:
    return [c for c in co.cases() if c["task_id"] not in before]


def check_import(
    check: Checker,
    co: Company,
    label: str,
    plant: Plant,
    eff: Effects,
    summary: dict[str, Any],
    audit_before: dict[str, int],
    tasks_before: set[int],
    exact_count: bool = True,
) -> list[dict[str, Any]]:
    """An applied import against the oracle's effects: counts, refusals and their reasons, what changed, what was
    released, the cases raised and the audit rows. Returns the new cases."""
    n_changes = len(eff.changes)
    if exact_count:
        check(summary["changed"] == n_changes, f"{label}: changed {summary['changed']}, expected {n_changes}")
    else:
        check(summary["changed"] >= 1, f"{label}: changed {summary['changed']}, expected at least the header")
    check(
        len(summary["changes"]) >= summary["changed"],
        f"{label}: 'changed' {summary['changed']} rows but only {len(summary['changes'])} change records",
    )
    got_refused = {(r["kind"], int(r.get("line") or 0)): str(r.get("reason", "")) for r in summary["rejected"]}
    want_refused = {(k, line): words for k, line, words in eff.refused}
    for k, words in want_refused.items():
        check(
            words.lower() in got_refused.get(k, "").lower(),
            f"{label}: {k[0]} line {k[1]} should be left out with '{words}', got {got_refused.get(k)!r}",
        )
    extra = sorted(set(got_refused) - set(want_refused))
    check(not extra, f"{label}: rows left out that the rules apply: {[(k, got_refused[k]) for k in extra][:3]}")
    by_item: dict[str, set[str]] = defaultdict(set)
    headers: set[str] = set()
    for (_, item), what in what_of(summary["changes"]).items():
        (by_item[item] if item else headers).update(what)
    union = headers | {w for s in by_item.values() for w in s}
    for c in eff.changes:
        want = set(c["what"])
        if c["item"]:
            got = by_item.get(c["item"], set())
            check(want <= got, f"{label}: {c['doc']} {c['item']} reported as {sorted(got)}, expected {sorted(want)}")
        else:
            alt = want | ({"qty_down", "qty_up"} if "header_qty" in want else set())
            check(
                bool(alt & union),
                f"{label}: the change of {c['doc']} reported as {sorted(union)}, expected {sorted(want)}",
            )
    want_rel = {k: v for k, v in eff.released.items() if v}
    got_rel = released_by(summary["changes"])
    check(
        got_rel == want_rel,
        f"{label}: released { {k: fq(v) for k, v in got_rel.items()} }, expected { {k: fq(v) for k, v in want_rel.items()} }",
    )
    for w in eff.warnings:
        check(
            any(w.lower() in x.lower() for x in summary["warnings"]),
            f"{label}: warning '{w}' missing; got {summary['warnings']}",
        )
    made = new_cases(co, tasks_before)
    for owner in ("buyer", "store"):
        got_q: dict[str, Decimal] = defaultdict(Decimal)
        for c in made:
            if c["owner_kind"] == owner:
                for item, q in c["excess"].items():
                    got_q[item] += q
        want_q = eff.case_qty(owner)
        check(
            dict(got_q) == want_q,
            f"{label}: {owner} cases for { {k: fq(v) for k, v in got_q.items()} }, expected "
            f"{ {k: fq(v) for k, v in want_q.items()} }",
        )
    listed = {c.get("task_id") for ch in summary["changes"] for c in (ch.get("cases") or [])}
    check(
        {c["task_id"] for c in made} <= listed,
        f"{label}: cases {[c['task_id'] for c in made]} are not all listed in the import's changes",
        "medium",
    )
    if any(v for v in eff.cut.values()):
        check(
            any(ch.get("requests_cut") for ch in summary["changes"]),
            f"{label}: open purchase requests were cut but no change lists requests_cut",
            "medium",
        )
    delta = {a: co.audit_count(a) - audit_before.get(a, 0) for a in AUDITED}
    check(
        delta["demand.change"] in (summary["changed"], len(summary["changes"])) and delta["demand.change"] > 0,
        f"{label}: {delta['demand.change']} demand.change audit rows for {summary['changed']} changed row(s) and "
        f"{len(summary['changes'])} change record(s)",
    )
    cut_lines = {k for k, v in eff.cut.items() if v}
    if cut_lines:
        check(
            delta["request.line_reduce"] >= len(cut_lines),
            f"{label}: {delta['request.line_reduce']} request.line_reduce rows for {len(cut_lines)} line(s) cut",
        )
    if want_rel:
        check(delta["material_request.change"] >= 1, f"{label}: stock was released but no material_request.change row")
    check(
        delta["task.create"] == len(made), f"{label}: {delta['task.create']} task.create rows for {len(made)} case(s)"
    )
    return made


def audit_counts(co: Company) -> dict[str, int]:
    return {a: co.audit_count(a) for a in AUDITED}


def task_ids(co: Company) -> set[int]:
    return {c["task_id"] for c in co.cases()}


def read_csv_bytes(body: bytes) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(body.decode("utf-8-sig"))))


def check_again(check: Checker, co: Company, label: str, raw: dict[str, bytes], eff: Effects) -> None:
    """The same files a second time: nothing changes (spec 4.5, first bullet)."""
    before = co.snapshot()
    s = import_change(co, check, f"{label} again", raw)
    rows = sum(len(read_csv_bytes(b)) for b in raw.values())
    left = rows - len(eff.refused)
    check(
        s["changed"] == 0 and s["loaded"] == 0 and s["already_loaded"] == left,
        f"{label}: a second import answered loaded {s['loaded']}, changed {s['changed']}, already "
        f"{s['already_loaded']} (expected 0, 0, {left})",
    )
    after = co.snapshot()
    moved = [k for k in after if after[k] != before[k]]
    check(not moved, f"{label}: a second import changed {moved}")


def mr_cards(co: Company, by: str) -> dict[str, dict[str, Any]]:
    """The Material requests screen's requests by order number (the last one made for an order)."""
    found: dict[str, dict[str, Any]] = {}

    def walk(obj: Any) -> None:
        if isinstance(obj, dict):
            if "order_no" in obj and ("lines" in obj or "source_kind" in obj):
                found[str(obj["order_no"])] = obj
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for v in obj:
                walk(v)

    walk(js(co.call(by, "GET", "/api/material-requests")))
    return found


def lines_buying(co: Company, label: str, item: str | None = None) -> list[dict[str, Any]]:
    """The request lines that buy for a material request (or one of its items): number, line, qty, status, need-by."""
    no = dict((lbl, n) for lbl, n, _ in co.mr_labels()).get(label)
    found = co.q(
        "select l.item_code, rl.request_no, rl.line_no, rl.qty, rl.status, rl.need_by, b.qty from material_request_buy b "
        "join material_request_line l on l.id = b.mr_line_id join request_line rl on rl.request_no = b.request_no "
        "and rl.line_no = b.request_line_no where l.mr_no = :m order by rl.request_no, rl.line_no",
        m=no,
    )
    keys = ("item", "request_no", "line_no", "qty", "status", "need_by", "buys")
    return [dict(zip(keys, r, strict=True)) for r in found if item is None or r[0] == item]


def check_dry_run(check: Checker, co: Company, label: str, raw: dict[str, bytes]) -> int:
    """A dry run lists the changes (what, before, after) with empty effects and writes nothing (spec 5.2, OP-20)."""
    before = co.snapshot()
    imports = int(co.one("select count(*) from demand_import") or 0)
    answers = co.import_files(raw, dry_run=True)
    s = summary_of(answers)
    check(all(a["status"] == 200 and a.get("dry_run") is True for a in answers), f"{label}: a dry run answered "
          f"{[(a['status'], a.get('dry_run')) for a in answers]}")  # fmt: skip
    check(bool(s["changes"]), f"{label}: a dry run listed no changes")
    for c in s["changes"]:
        shape = all(k in c for k in ("what", "before", "after"))
        empty = (
            not c.get("released")
            and not c.get("requests_cut")
            and not c.get("cases")
            and not c.get("requests_cancelled")
        )
        check(shape and empty, f"{label}: dry-run change {c.get('doc_no')} {c.get('item_code')} has shape {shape} and "
              f"effects {({k: c.get(k) for k in ('released', 'requests_cut', 'cases')})}")  # fmt: skip
    after = co.snapshot()
    check(after == before, f"{label}: a dry run changed {[k for k in after if after[k] != before[k]]}")
    check(int(co.one("select count(*) from demand_import") or 0) == imports, f"{label}: a dry run stored an import")
    return len(s["changes"])
