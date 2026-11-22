"""Shared pieces of the block 2 evals (E68-E74): stock quality, time-phased cover (Q17), safety stock (C4), the store
check as a job and after events, replenishment's on-order count (DF-25) and the planning capability. Written from the
block 2 spec, the plan and the dataset only, never from the implementation.

Two halves, as in `block1`:

- **The oracle** (`Stock`, built on `block1.Plant`): the store check of the spec in plain code over the raw files. Usable
  stock is the replayed history (`stock_opening.csv` + `stock_movement.csv`, untouched by block 2), held stock comes from
  `history/stock_held.csv`, safety stock from `master/item_location.csv`. Free stock = usable - set aside - safety
  stock. Each purchase cover carries its supply date (the request line's need-by, or the purchase order's promise once
  one is recorded); a date already passed counts as today; the latest arrival is the need-by moved back
  `receiving_buffer_days` working days on the location's own calendar (from `location.csv` and `data/reference`).
  Timely, late, at risk and short follow spec 4.1; the store check sets free stock aside for short + at risk and buys
  only the short part (OP-1). `Wrong` switches single rules to a deliberately wrong version (the mutation checks).
- **The platform** (`Co`, built on `block1.Company`): a demo company made by `demo reset`, its people signed in with their
  own logins, the API the spec names (`/api/inventory...`, `/api/material-requests...`, `/api/supply-risks...`,
  `/api/setup/...`, `/api/admin/proposals/...`), the job worker run in this process at a chosen time, and read-only views
  of the tables the spec names where the API is silent.

Where the spec leaves a shape open (a list wrapped in an object or not, a quantity as text or a number, a person as id,
code or name), the readers accept every reasonable shape and say so in a comment.
"""

import contextlib
import json
import re
from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import block1 as b
import lib
from block1 import IST, D, Z, dec, fq, js

QUALITIES = ("usable", "quarantine", "blocked")
CAUSES = (
    "covered",
    "on_order",
    "late_supply",
    "short",
    "no_stock",
    "held_for_earlier",
    "safety_stock",
    "held_quality",
)
PLANNING_TOOLS = {
    "material_request",
    "supply_for_line",
    "stock_position",
    "set_aside_elsewhere",
    "demand_freshness",
    "open_cases",
}
JOB = "store_check"
JOB_AT = time(5, 30)  # India time (spec 6.1)


# ======================================================================================================================
# The oracle
# ======================================================================================================================


@dataclass(frozen=True)
class Wrong:
    """Rules of block 2 switched to a wrong version, one at a time (the mutations the spec's eval table names)."""

    blocked_usable: bool = False  # blocked stock counted as on hand (E68)
    quarantine_usable: bool = False  # quarantine set aside before the decision (E68)
    date_blind: bool = False  # every supply counts whatever its date (E69, DF-10)
    buy_late: bool = False  # the late part is bought again (E69, AT-09)
    calendar_buffer: bool = False  # the receiving buffer counted in calendar days (E69)
    ignore_safety: bool = False  # safety stock ignored in reservation (E70, DF-20)
    no_event: bool = False  # no store check after a short-close or a cancel (E71)
    rebuy_rejected: bool = False  # the job re-raises a rejected request (E71, OP-16)
    count_all_po: bool = False  # open PO lines bought for production counted as restock on order (E72, DF-25)


RIGHT2 = Wrong()


def back(cal: b.Calendar, d: date, n: int) -> date:
    """The day n working days before d on this calendar; 0 = d itself, whatever day it is (spec 4.1)."""
    while n > 0:
        d -= timedelta(days=1)
        if cal.working(d):
            n -= 1
    return d


def calendars(dataset: Path) -> dict[str, b.Calendar]:
    """Each location's calendar from `location.csv` (working days, holiday scope) and `data/reference`."""
    out = {}
    for loc in b.read_csv(dataset / "master" / "location.csv"):
        out[loc["code"]] = b.Calendar(
            frozenset(b.open_days(loc.get("working_days", ""))), frozenset(b.holidays(loc.get("holiday_scope", "")))
        )
    return out


def held_lots(dataset: Path) -> list[dict[str, str]]:
    return b.read_csv(dataset / "history" / "stock_held.csv")


def safety_of(dataset: Path) -> dict[tuple[str, str], Decimal]:
    first = b.read_csv(dataset / "master" / "location.csv")[0]["code"]
    return {
        (r["item_code"], r.get("location_code") or first): D(r.get("safety_stock"))
        for r in b.read_csv(dataset / "master" / "item_location.csv")
    }


class Stock(b.Plant):
    """The store as block 2 says it should be: quality, safety stock and supply dates on top of block 1's store."""

    def __init__(
        self,
        dataset: Path,
        wrong: Wrong = RIGHT2,
        rank: dict[str, int] | None = None,
        today: date = lib.TODAY,
        buffer: int = 0,
    ) -> None:
        super().__init__(dataset, b.RIGHT, rank)
        self.wrong = wrong
        self.today = today
        self.buffer = buffer
        self.cals = calendars(dataset)
        self.safety = safety_of(dataset)
        self.held: dict[tuple[str, str, str], Decimal] = defaultdict(Decimal)
        for r in held_lots(dataset):
            self.held[(r["item_code"], r.get("location_code") or self.first_loc, r["quality"])] += D(r["qty"])
        self.rejected: set[str] = set()  # material requests whose latest purchase request was rejected (OP-16)

    # ---- positions ----
    def usable(self, item: str, loc: str) -> Decimal:
        return self.on_hand.get((item, loc), Z)

    def reserved_at(self, item: str, loc: str, but: b.Line | None = None) -> Decimal:
        return sum((ln.reserved for ln in self.lines if ln.item == item and ln.loc == loc and ln is not but), Z)

    def safety_at(self, item: str, loc: str) -> Decimal:
        return Z if self.wrong.ignore_safety else self.safety.get((item, loc), Z)

    def free(self, item: str, loc: str) -> Decimal:
        extra = Z
        if self.wrong.blocked_usable:
            extra += self.held[(item, loc, "blocked")]
        if self.wrong.quarantine_usable:
            extra += self.held[(item, loc, "quarantine")]
        return self.usable(item, loc) + extra - self.reserved_at(item, loc) - self.safety_at(item, loc)

    def position(self, item: str, loc: str) -> dict[str, Decimal]:
        """usable, quarantine, blocked, reserved, safety_stock, available (= usable - reserved - safety; may be < 0)."""
        reserved = self.reserved_at(item, loc)
        safety = self.safety.get((item, loc), Z)
        usable = self.usable(item, loc)
        return {
            "usable": usable,
            "quarantine": self.held[(item, loc, "quarantine")],
            "blocked": self.held[(item, loc, "blocked")],
            "reserved": reserved,
            "safety_stock": safety,
            "available": usable - reserved - safety,
        }

    # ---- supply dates (spec 4.1) ----
    def latest(self, ln: b.Line) -> date:
        if self.wrong.calendar_buffer:
            return ln.need_by - timedelta(days=self.buffer)
        return back(self.cals.get(ln.loc, b.WRONG_CALENDAR), ln.need_by, self.buffer)

    def phase(self, ln: b.Line) -> dict[str, Any]:
        latest = self.latest(ln)
        timely = late = Z
        for c in ln.covers:
            if c.qty <= 0:
                continue
            when = max(c.need_by or ln.need_by, self.today)
            if self.wrong.date_blind or when <= latest:
                timely += c.qty
            else:
                late += c.qty
        lacking = max(ln.need - ln.issued - ln.reserved, Z)
        at_risk = min(late, max(lacking - timely, Z))
        short = max(lacking - timely - late, Z)
        return {"timely": timely, "late": late, "at_risk": at_risk, "short": short, "lacking": lacking,
                "latest": latest}  # fmt: skip

    def causes(self, ln: b.Line) -> set[str]:
        """Spec 8.2, per line."""
        p = self.phase(ln)
        lacking = p["lacking"]
        usable = self.usable(ln.item, ln.loc)
        before = usable - self.reserved_at(ln.item, ln.loc)
        out = set()
        if ln.issued + ln.reserved >= ln.need:
            out.add("covered")
        if p["timely"] > 0:
            out.add("on_order")
        if p["at_risk"] > 0:
            out.add("late_supply")
        if p["short"] > 0:
            out.add("short")
        if lacking > 0 and usable == 0:
            out.add("no_stock")
        if lacking > 0 and self.reserved_at(ln.item, ln.loc, but=ln) > 0:
            out.add("held_for_earlier")
        if lacking > 0 and before > 0 and before - self.safety.get((ln.item, ln.loc), Z) <= 0:
            out.add("safety_stock")
        if lacking > 0 and (self.held[(ln.item, ln.loc, "quarantine")] + self.held[(ln.item, ln.loc, "blocked")]) > 0:
            out.add("held_quality")
        return out

    # ---- the store check and reservation (spec 4.2, 5.1) ----
    def want(self, ln: b.Line) -> Decimal:
        """What free stock goes to: short + at risk (the date-blind rule: only what nothing covers)."""
        p = self.phase(ln)
        if self.wrong.date_blind:
            return p["short"]
        return p["short"] + p["at_risk"]

    def store_check(
        self, places: set[tuple[str, str]] | None = None, tag: str | None = None, automatic: bool = False
    ) -> dict:
        if tag is None:
            self.tag += 1
            tag = f"new{self.tag}"
        made: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
        for ln in self.ordered():
            if places is not None and (ln.item, ln.loc) not in places:
                continue
            if ln.basis == "gross":
                take = min(self.want(ln), max(self.free(ln.item, ln.loc), Z))
                if take > 0:
                    ln.reserved += take
            p = self.phase(ln)
            buy = p["short"] + (p["at_risk"] if self.wrong.buy_late else Z)
            if buy <= 0:
                continue
            if automatic and ln.mr in self.rejected and not self.wrong.rebuy_rejected:
                continue  # OP-16: a rejection is a person's decision; only the button buys again
            ln.covers.append(b.Cover(tag, buy, False, ln.need_by))
            made[(ln.mr, ln.item)] += buy
        return dict(made)

    def stock_rose(self, places: set[tuple[str, str]]) -> None:
        for second in (False, True):
            for ln in self.ordered():
                if (ln.item, ln.loc) not in places or ln.basis == "net":
                    continue
                free = self.free(ln.item, ln.loc)
                if free <= 0:
                    continue
                if not second:
                    ln.reserved += min(self.want(ln), free)
                else:
                    ln.reserved += self.cut(ln, min(ln.open_cover(), free))

    # ---- what happens in the store and with the supplier ----
    def line(self, mr: str, item: str) -> b.Line:
        return next(ln for ln in self.lines if ln.mr == mr and ln.item == item and ln.status == "open")

    def promise(self, mr: str, item: str, when: date) -> None:
        """The supplier's promise on the order that buys this line (its covers with the buyer)."""
        for c in self.line(mr, item).covers:
            if c.buyer:
                c.need_by = when

    def arrive(self, mr: str, item: str, qty: Decimal) -> None:
        """Goods of the order that buys this line arrive: off its cover, into stock, set aside for it, the rest handed on."""
        ln = self.line(mr, item)
        left = qty
        for c in ln.covers:
            if c.buyer and left > 0:
                d = min(c.qty, left)
                c.qty -= d
                left -= d
        self.on_hand[(ln.item, ln.loc)] = self.usable(ln.item, ln.loc) + qty
        ln.reserved += min(qty, max(ln.need - ln.issued - ln.reserved, Z), max(self.free(ln.item, ln.loc), Z))
        self.stock_rose({(ln.item, ln.loc)})

    def drop_order(self, mr: str, item: str, event: bool = True) -> dict:
        """What is still to come on the order goes (short-close, or a cancel): the line's buyer cover drops; the event
        then runs the evaluation and the store check for that item-location."""
        ln = self.line(mr, item)
        for c in ln.covers:
            if c.buyer:
                c.qty = Z
        if event and not self.wrong.no_event:
            return self.store_check({(ln.item, ln.loc)}, automatic=True)
        return {}

    def reject(self, mr: str) -> dict:
        """The material request's purchase request rejected: its cover drops, the event check buys nothing again."""
        self.drop_covers(mr)
        self.rejected.add(mr)
        places = {(ln.item, ln.loc) for ln in self.lines if ln.mr == mr}
        return self.store_check(places, automatic=True)

    def hold(self, item: str, loc: str, qty: Decimal, quality: str) -> dict[str, Decimal]:
        """Usable stock held; what was set aside beyond usable is given back latest need-by first; then the store check
        for that place (OP-12). Returns what each material request gave back."""
        self.on_hand[(item, loc)] = self.usable(item, loc) - qty
        self.held[(item, loc, quality)] += qty
        over = self.reserved_at(item, loc) - self.usable(item, loc)
        given: dict[str, Decimal] = defaultdict(Decimal)
        for ln in reversed(self.ordered()):
            if over <= 0:
                break
            if (ln.item, ln.loc) != (item, loc) or ln.reserved <= 0:
                continue
            d = min(ln.reserved, over)
            ln.reserved -= d
            over -= d
            given[ln.mr] += d
        self.store_check({(item, loc)})
        return dict(given)

    def decide(self, item: str, loc: str, quality: str, to_usable: Decimal, to_blocked: Decimal) -> None:
        self.held[(item, loc, quality)] -= to_usable + to_blocked
        self.held[(item, loc, "blocked")] += to_blocked
        if to_usable > 0:
            self.on_hand[(item, loc)] = self.usable(item, loc) + to_usable
            self.stock_rose({(item, loc)})

    def issue_by_hand(self, item: str, loc: str, qty: Decimal) -> None:
        self.on_hand[(item, loc)] = self.usable(item, loc) - qty

    def manual_limit(self, item: str, loc: str) -> Decimal:
        """A manual issue takes at most usable - set aside - safety stock (spec 5.1.2)."""
        return self.usable(item, loc) - self.reserved_at(item, loc) - self.safety_at(item, loc)

    def override(self, mr: str, item: str, qty: Decimal) -> Decimal:
        """The approved safety-stock override for a material line: set aside up to qty, bounded by what the line lacks
        and by usable - set aside, and its open cover cut by as much (spec 5.1.5)."""
        ln = self.line(mr, item)
        lacking = max(ln.need - ln.issued - ln.reserved, Z)
        take = max(min(qty, lacking, self.usable(item, ln.loc) - self.reserved_at(item, ln.loc)), Z)
        ln.reserved += take
        self.cut(ln, take)
        return take

    # ---- what the oracle says ----
    def observe2(self) -> dict[tuple[str, str], dict[str, Any]]:
        got = self.observe()
        for ln in self.lines:
            if ln.status != "open":
                continue
            o = got[(ln.mr, ln.item)]
            p = self.phase(ln)
            for k in ("timely", "late", "at_risk", "short"):
                o[k] = o.get(k, Z) + p[k]
            o["latest"] = p["latest"]
            o["causes"] = sorted(self.causes(ln))
        return got

    def value(self, mr: str, request: str) -> Decimal:
        return self.request_value(mr, request)


def copy_state(src: Stock, wrong: Wrong) -> Stock:
    """The same store and demand with one rule switched (lines, covers, stock, held lots and dates copied)."""
    import copy

    dup = copy.deepcopy(src)
    dup.wrong = wrong
    return dup


# ======================================================================================================================
# The platform
# ======================================================================================================================


class Co(b.Company):
    """A demo company with the block 2 calls."""

    # ---- people ----
    def role(self, name: str) -> str:
        return self.holders(name)[0]

    def designation(self, words: str) -> str:
        """The person whose designation holds these words (Plant Head, Director, Chief Financial Officer)."""
        return next(c for c, u in sorted(self.people.items()) if words.lower() in (u.get("designation") or "").lower())

    def manager_chain(self, code: str) -> list[str]:
        out, seen = [], {code}
        cur = self.people[code].get("manager_code", "")
        while cur and cur not in seen:
            out.append(cur)
            seen.add(cur)
            cur = self.people.get(cur, {}).get("manager_code", "")
        return out

    # ---- files ----
    def file(self, rel: str) -> Path | None:
        """A dataset file: the reset's own copy, else `data/datasets/<name>-v1/` (spec 9.2)."""
        for base in (self.dataset, lib.ROOT / "data" / "datasets" / f"{self.name}-v1"):
            if (base / rel).exists():
                return base / rel
        return None

    def truth2(self, name: str, scenario: str) -> list[dict[str, str]]:
        path = self.file(f"truth/{name}.csv")
        rows = b.read_csv(path) if path else []
        return [
            r for r in rows if r.get("scenario", "") == scenario or r.get("scenario", "").startswith(scenario + "-")
        ]

    def scenarios(self) -> list[dict[str, Any]]:
        path = self.file("truth/stock_scenarios.json")
        if path is None:
            return []
        try:
            found = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            return []
        return found if isinstance(found, list) else list(found.get("scenarios") or [])

    def scenario2(self, name: str) -> dict[str, Any] | None:
        return next((s for s in self.scenarios() if str(s.get("name", "")).split(" ")[0] == name), None)

    # ---- reading ----
    def api_lines(self, by: str | None = None) -> dict[tuple[str, str], dict[str, Any]]:
        """The Material requests screen's lines by (order label, item); an order's last request wins."""
        out = {}
        for order, card in b.mr_cards(self, by or self.role("store")).items():
            for ln in card.get("lines") or []:
                out[(order, str(ln.get("item_code")))] = {**ln, "_card": card}
        return out

    def observe2(self) -> dict[tuple[str, str], dict[str, Any]]:
        """Block 1's table view plus the screen's time-phased fields (spec 4.5)."""
        got = self.observe()
        api = self.api_lines()
        for k, o in got.items():
            ln = api.get(k, {})
            for f, key in (
                ("timely", "timely_qty"),
                ("late", "late_qty"),
                ("at_risk", "at_risk_qty"),
                ("short", "short"),
            ):
                o[f] = dec(ln.get(key))
            o["latest"] = b.day(ln.get("latest_arrival"))
            o["causes"] = sorted(str(c) for c in ln.get("causes") or []) if ln.get("causes") is not None else None
            o["last_rejected"] = ln.get("last_rejected")
        return got

    def inventory(self, by: str | None = None) -> dict[tuple[str, str], dict[str, Any]]:
        r = self.call(by or self.role("store"), "GET", "/api/inventory")
        return {(str(x.get("item_code")), str(x.get("location_code"))): x for x in js(r).get("lines") or []}

    def quality_levels(self) -> dict[tuple[str, str, str], Decimal]:
        if "stock_quality_level" not in self.tables():
            return {}
        out: dict[tuple[str, str, str], Decimal] = defaultdict(Decimal)
        for item, loc, quality, qty in self.q("select item_code, location_code, quality, qty from stock_quality_level"):
            out[(item, loc, quality)] += D(qty)
        return dict(out)

    def open_hold_sums(self) -> dict[tuple[str, str, str], Decimal]:
        out: dict[tuple[str, str, str], Decimal] = defaultdict(Decimal)
        for item, loc, quality, left in self.q(
            "select item_code, location_code, quality, left_qty from stock_hold where closed_at is null"
        ):
            out[(item, loc, quality)] += D(left)
        return {k: v for k, v in out.items() if v}

    def holds(self, by: str | None = None, **params: Any) -> tuple[int, list[dict[str, Any]]]:
        qs = "&".join(f"{k}={v}" for k, v in params.items() if v is not None)
        r = self.call(by or self.role("store"), "GET", "/api/inventory/holds" + (f"?{qs}" if qs else ""))
        body = js(r)
        rows = body.get("holds") if isinstance(body, dict) else body  # the spec wraps the list in "holds"
        return r.status_code, [x for x in rows or [] if isinstance(x, dict)]

    def hold_of(self, item: str, quality: str, loc: str | None = None) -> dict[str, Any] | None:
        _, rows = self.holds()
        mine = [h for h in rows if h.get("item_code") == item and h.get("quality") == quality
                and (loc is None or h.get("location_code") == loc)]  # fmt: skip
        return mine[0] if mine else None

    def hold(self, by: str, item: str, loc: str, qty: Any, quality: str, reason: str, reference: str = "") -> Any:
        body = {"item_code": item, "location": loc, "qty": str(qty), "quality": quality, "reason": reason,
                "reference": reference}  # fmt: skip
        return self.call(by, "POST", "/api/inventory/holds", body)

    def decide(self, by: str, hold_id: Any, to_usable: Any, to_blocked: Any, reason: str) -> Any:
        body = {"to_usable": str(to_usable), "to_blocked": str(to_blocked), "reason": reason}
        return self.call(by, "POST", f"/api/inventory/holds/{hold_id}/decide", body)

    def risks(self) -> list[dict[str, Any]]:
        if "supply_risk" not in self.tables():
            return []
        found = self.q(
            "select t.id, t.kind, t.title, t.assignee, t.status, t.due_at, t.created_at, t.escalation_chain::text, "
            "s.po_no, s.mr_no, s.location_code, s.lines::text, s.buffer_days, s.outcome, s.closed_at "
            "from supply_risk s join task t on t.id = s.task_id order by t.id"
        )
        keys = ("task_id", "kind", "title", "assignee", "status", "due_at", "created_at", "chain", "po_no", "mr_no",
                "location_code", "lines", "buffer_days", "outcome", "closed_at")  # fmt: skip
        out = []
        for r in found:
            c = dict(zip(keys, r, strict=True))
            c["chain"] = json.loads(c["chain"] or "[]")
            c["lines"] = json.loads(c["lines"] or "[]")
            out.append(c)
        return out

    def supply_risks_api(self, by: str, status: str | None = "open") -> tuple[int, list[dict[str, Any]]]:
        r = self.call(by, "GET", "/api/supply-risks" + (f"?status={status}" if status else ""))
        return r.status_code, [
            x for x in js(r).get("cases") or [] if isinstance(x, dict)
        ] if r.status_code == 200 else []

    def exceptions_of(self, by: str, kind: str, mine: bool = False) -> list[dict[str, Any]]:
        r = self.call(by, "GET", f"/api/exceptions?kind={kind}" + ("&mine=true" if mine else ""))
        return [x for x in (js(r).get("rows") or []) if isinstance(x, dict)] if r.status_code == 200 else []

    def requests_now(self) -> set[str]:
        return {n for (n,) in self.q("select no from request")}

    def request_lines(self, no: str) -> dict[str, Decimal]:
        got: dict[str, Decimal] = defaultdict(Decimal)
        for item, qty in self.q(
            "select item_code, qty from request_line where request_no = :n and status <> 'cancelled'", n=no
        ):
            got[item] += D(qty)
        return dict(got)

    def request_head(self, no: str) -> dict[str, Any]:
        r = self.q("select status, total_value, priority, ship_to_location from request where no = :n", n=no)
        if not r:
            return {}
        return {"status": r[0][0], "total": D(r[0][1]), "priority": r[0][2], "location": r[0][3]}

    def approver_of(self, no: str) -> str | None:
        """Who the request waits for now (the pending approval step), as a person code."""
        found = self.one(
            "select approver from approval_step where request_no = :n and status = 'pending' order by round desc, "
            "step limit 1",
            n=no,
        )
        return self.code_of(found) if found else None

    def mr_requests(self, label: str) -> set[str]:
        no = dict((lbl, n) for lbl, n, _ in self.mr_labels()).get(label)
        return {
            r[0]
            for r in self.q(
                "select distinct b.request_no from material_request_buy b join material_request_line l on "
                "l.id = b.mr_line_id where l.mr_no = :m",
                m=no,
            )
        }

    # ---- purchase orders ----
    def po_of(self, label: str, item: str) -> tuple[str, int] | None:
        no = dict((lbl, n) for lbl, n, _ in self.mr_labels()).get(label)
        found = self.q(
            "select pl.po_no, pl.line_no from po_line pl join po p on p.no = pl.po_no join material_request_buy b on "
            "b.request_no = pl.request_no and b.request_line_no = pl.request_line_no join material_request_line l on "
            "l.id = b.mr_line_id where l.mr_no = :m and l.item_code = :i and p.status <> 'cancelled' "
            "order by pl.po_no desc",
            m=no,
            i=item,
        )
        return (str(found[0][0]), int(found[0][1])) if found else None

    def po_status(self, no: str) -> str:
        return str(self.one("select status from po where no = :n", n=no) or "")

    def advance_po(self, no: str) -> str:
        """An order made by `p2p.cli order`: approved through its tier by whoever may, then sent by the buyer."""
        approvers = self.holders("approver")
        buyer = self.role("buyer")
        for _ in range(8):
            status = self.po_status(no)
            if status in ("sent", "acknowledged", "closed", "cancelled"):
                break
            if status == "approved":
                self.call(buyer, "POST", f"/api/purchase-orders/{no}/send", {})
                continue
            moved = False
            for code in approvers:
                if self.call(code, "POST", f"/api/purchase-orders/{no}/approve", {}).status_code == 200:
                    moved = True
                    break
            if not moved:
                break
        return self.po_status(no)

    def acknowledge(self, no: str, promised: date, on: date) -> Any:
        with b.on_day(on):
            return self.call(self.role("buyer"), "POST", f"/api/purchase-orders/{no}/acknowledge",
                             {"promised_date": promised.isoformat(), "note": "eval"})  # fmt: skip

    def receive(self, no: str, line_no: int, qty: Any, on: date, rejected: Any = None) -> Any:
        line: dict[str, Any] = {"line_no": line_no, "qty_received": str(qty)}
        if rejected is not None:
            line.update(qty_rejected=str(rejected), reject_reason="eval: damaged at the gate")
        with b.on_day(on):
            return self.call(self.role("store"), "POST", f"/api/purchase-orders/{no}/receive",
                             {"lines": [line], "challan": f"DC-EVAL-{no[-5:]}-{qty}"})  # fmt: skip

    # ---- changes that wait for a second administrator (D-22) ----
    def d22(self, path: str, body: dict[str, Any], method: str = "PUT") -> tuple[Any, Any]:
        """One administrator asks, the other approves. Returns (the ask, the approval) responses."""
        maker, checker = self.holders("admin")[0], self.holders("admin")[1]
        asked = self.call(maker, method, path, {**body, "reason": body.get("reason") or "eval: block 2 setting"})
        pid = js(asked).get("proposal_id")
        if asked.status_code != 202 or pid is None:
            return asked, None
        return asked, self.call(checker, "POST", f"/api/admin/proposals/{pid}/approve", {})

    def policy(self, key: str, value: str) -> tuple[Any, Any]:
        return self.d22(f"/api/setup/policies/{key}", {"value": value})

    def planning(self, item: str, loc: str, **values: Any) -> tuple[Any, Any]:
        body = {"item_code": item, "location": loc, **{k: str(v) for k, v in values.items()}}
        return self.d22("/api/setup/item-planning", body, method="POST")

    def automation(self, decision: str, level: str) -> tuple[Any, Any]:
        return self.d22(f"/api/setup/automation/{decision}", {"level": level})

    def setting(self, key: str) -> str | None:
        v = self.one("select value from app_setting where key = :k", k=key)
        return None if v is None else str(v)

    # ---- the job ----
    def run_job(self, day: date, kind: str = JOB) -> int:
        """The daily job at 05:30 India time on this business day, run by the worker in this process."""
        from p2p.platform import worker

        worker.load_handlers()
        handler = worker.HANDLERS.get(kind)
        if handler is None:
            return 0
        at = datetime.combine(day, JOB_AT, tzinfo=IST)
        self.db.commit()
        # the job already queued for this morning runs; else one is queued now. Only this job runs (other kinds due at
        # the same time, like replenishment, would change what a "second run changes nothing" check compares)
        due = [j for j in self.queued_jobs(kind) if j[2] == "queued" and j[1] <= at + timedelta(minutes=1)]
        job_id = due[0][0] if due else worker.enqueue(self.slug, kind, {}, at)
        with b.on_day(day):
            handler(worker.JobContext(job_id, self.slug, {}, at + timedelta(minutes=1)))
            worker._finish(job_id, at + timedelta(minutes=1), None)  # noqa: SLF001 - the worker's own bookkeeping
        self.db.rollback()
        return 1

    def queued_jobs(self, kind: str = JOB) -> list[tuple[int, datetime, str]]:
        from sqlalchemy import text

        from p2p.platform import tenants

        with tenants.control_engine().connect() as c:
            rows = c.execute(
                text("select id, run_at, status from job where tenant_slug = :t and kind = :k order by id"),
                {"t": self.slug, "k": kind},
            ).all()
        return [(int(r[0]), r[1], str(r[2])) for r in rows]

    # ---- a snapshot for "a second run changes nothing" ----
    def snapshot2(self) -> dict[str, Any]:
        snap = self.snapshot()
        tables = self.tables()
        snap["holds"] = sorted(tuple(str(x) for x in r) for r in self.q(
            "select id, quality, qty, left_qty, closed_at from stock_hold")) if "stock_hold" in tables else []  # fmt: skip
        snap["risks"] = sorted(tuple(str(x) for x in r) for r in self.q(
            "select task_id, lines::text, outcome from supply_risk")) if "supply_risk" in tables else []  # fmt: skip
        snap["movements"] = int(self.one("select count(*) from stock_movement") or 0)
        snap["reserve_audit"] = self.audit_count("material_request.reserve")
        return snap


# ======================================================================================================================
# Comparisons
# ======================================================================================================================

PHASED = ("need", "reserved", "issued", "timely", "late", "at_risk", "short")


def differ2(
    want: dict[tuple[str, str], dict[str, Any]],
    got: dict[tuple[str, str], dict[str, Any]],
    fields: Iterable[str] = PHASED,
    only: Iterable[tuple[str, str]] | None = None,
) -> list[str]:
    keys = sorted(set(want) & set(got)) if only is None else sorted(only)
    wrong = []
    for k in keys:
        a, g = want.get(k), got.get(k)
        if a is None or g is None:
            wrong.append(f"{k[0]} {k[1]}: {'missing on the platform' if g is None else 'not expected'}")
            continue
        if a.get("status") == "cancelled":
            continue
        bad = []
        for f in fields:
            if f == "causes":
                if g.get("causes") is None or sorted(g["causes"]) != sorted(a.get("causes") or []):
                    bad.append(f"causes {g.get('causes')} (expected {a.get('causes')})")
                continue
            if g.get(f) is None and f in ("timely", "late", "at_risk"):
                bad.append(f"{f} missing")
                continue
            if D(a.get(f)) != D(g.get(f)):
                bad.append(f"{f} {fq(D(g.get(f)))} (expected {fq(D(a.get(f)))})")
        if bad:
            wrong.append(f"{k[0]} {k[1]}: " + ", ".join(bad))
    missing = sorted(set(want) - set(got)) if only is None else []
    wrong += [f"{k[0]} {k[1]}: missing on the platform" for k in missing if want[k].get("status") != "cancelled"]
    return wrong


def agree2(check: b.Checker, label: str, s: Stock, co: Co, fields: Iterable[str] = PHASED,
           only: Iterable[tuple[str, str]] | None = None) -> bool:  # fmt: skip
    wrong = differ2(s.observe2(), co.observe2(), fields, only)
    return check(
        not wrong, f"{label}: {len(wrong)} material request line(s) differ from the oracle; first: {wrong[:3]}"
    )


def told(right: dict, wrong: dict, fields: Iterable[str] = ("reserved", "requested", "timely", "late", "at_risk",
                                                               "short", "first", "new")) -> bool:  # fmt: skip
    fields = tuple(fields)
    if set(right) != set(wrong):
        return True
    return any(D(right[k].get(f)) != D(wrong[k].get(f)) for k in right for f in fields)


def mutation(check: b.Checker, out: Any, name: str, separated: bool) -> bool:
    check(separated, f"mutation '{name}': no case of this eval tells it from the right rule", "medium")
    out.metrics.setdefault("mutations caught", []).append(name if separated else f"{name} (NOT separated)")
    return separated


def positions_of(co: Co, places: Iterable[tuple[str, str]]) -> dict[tuple[str, str], dict[str, Decimal | None]]:
    """The inventory screen's figures per place: on hand (usable), quarantine, blocked, reserved, safety, available."""
    inv = co.inventory()
    out = {}
    for k in places:
        x = inv.get(k, {})
        out[k] = {
            "usable": dec(x.get("on_hand")) or Z,
            "quarantine": dec(x.get("quarantine")) or Z,
            "blocked": dec(x.get("blocked")) or Z,
            "reserved": dec(x.get("reserved")) or Z,
            "safety_stock": dec(x.get("safety_stock")) or Z,
            "available": dec(x.get("available")),
        }
    return out


def check_positions(check: b.Checker, label: str, co: Co, s: Stock, places: Iterable[tuple[str, str]]) -> None:
    places = list(places)
    got = positions_of(co, places)
    wrong = []
    for k in places:
        want = s.position(*k)
        for f, v in want.items():
            if got[k].get(f) is None or D(got[k][f]) != v:
                wrong.append(f"{k[0]}@{k[1]} {f} {got[k].get(f)} (expected {fq(v)})")
    check(not wrong, f"{label}: inventory figures differ from the oracle: {wrong[:4]}")
    levels = co.quality_levels()
    if levels or "stock_quality_level" in co.tables():
        bad = [f"{k} {q}: {fq(levels.get((*k, q), Z))} vs {fq(s.position(*k)[q if q != 'usable' else 'usable'])}"
               for k in places for q in QUALITIES if levels.get((*k, q), Z) != s.position(*k)[q]]  # fmt: skip
        check(not bad, f"{label}: stock_quality_level differs from the oracle: {bad[:4]}")
    else:
        check(False, f"{label}: the view stock_quality_level does not exist (spec 3.2)", "medium")


def check_ledger(check: b.Checker, label: str, co: Co) -> None:
    """Spec 3.2's invariant: per item-location and quality, open holds' left_qty = stock_quality_level."""
    if "stock_hold" not in co.tables():
        check(False, f"{label}: the table stock_hold does not exist (spec 3.2)")
        return
    holds = co.open_hold_sums()
    levels = {k: v for k, v in co.quality_levels().items() if k[2] != "usable" and v}
    check(holds == levels, f"{label}: open holds {holds} do not equal the held stock in stock_quality_level {levels}")


def truth_check(
    check: b.Checker,
    co: Co,
    scenario: str,
    states: list[dict[tuple[str, str], dict[str, Any]]],
    oracle: list[dict[tuple[str, str], dict[str, Any]]] | None = None,
    missing: str = "medium",
    items: set[str] | None = None,
    upto: str | None = None,
) -> int:
    """`truth_stock_check.csv` rows of a scenario against the platform's states the eval saw. The `step` column's values
    are the generator's own (the spec does not name them), so each step's rows must equal one of the states seen, and
    the last step's rows the last state. The oracle's states, when given, are checked against the key too (a
    disagreement there is a spec or generator gap, medium)."""
    rows = co.truth2("truth_stock_check", scenario)
    if not check(bool(rows), f"{scenario}: truth_stock_check.csv has no rows for this scenario (spec 9.4)", missing):
        return 0
    if items is not None:  # the eval ran other scenarios on the same company: only this scenario's items compare
        rows = [r for r in rows if r.get("item_code") in items]
    steps: dict[str, list[dict[str, str]]] = {}
    for r in rows:
        steps.setdefault(r.get("step", ""), []).append(r)

    def fits(group: list[dict[str, str]], state: dict[tuple[str, str], dict[str, Any]]) -> list[str]:
        bad = []
        for r in group:
            k = (r["mr_order_no"], r["item_code"])
            if k not in state:
                alt = (f"{r['mr_order_no']}/{r.get('location_code', '')}", r["item_code"])
                k = alt if alt in state else k
            g = state.get(k)
            if g is None:
                bad.append(f"{k} not on the platform")
                continue
            for col, f in (("need", "need"), ("issued", "issued"), ("reserved", "reserved"), ("timely", "timely"),
                           ("late", "late"), ("at_risk", "at_risk"), ("short", "short")):  # fmt: skip
                if dec(r.get(col)) is not None and D(g.get(f)) != D(r[col]):
                    bad.append(f"{k} {col} {fq(D(g.get(f)))} (key {r[col]})")
            if dec(r.get("open_request_qty")) is not None and D(r["open_request_qty"]) not in {
                D(g.get("open")),
                D(g.get("requested")),
            }:
                bad.append(f"{k} open_request_qty {fq(D(g.get('requested')))} (key {r['open_request_qty']})")
            if r.get("causes") is not None and g.get("causes") is not None:
                want = sorted(c for c in r["causes"].split(";") if c)
                if sorted(g["causes"]) != want:
                    bad.append(f"{k} causes {g['causes']} (key {want})")
        return bad

    names = list(steps)
    if upto in names:  # the eval stopped at this step of the scenario: later steps of the key were not run
        names = names[: names.index(upto) + 1]
    if len(states) == 1:  # only the final state was observed: the earlier steps of the key cannot be compared with it
        names = names[-1:]
    for i, step in enumerate(names):
        group = steps[step]
        candidates = states[-1:] if i == len(names) - 1 else states
        results = [fits(group, st) for st in candidates]
        best = min(results, key=len) if results else ["no state seen"]
        check(not best or any(not r for r in results), f"{scenario} step {step}: the platform disagrees with "
              f"truth_stock_check.csv in {len(best)} value(s); first: {best[:3]}")  # fmt: skip
        if oracle:
            oresults = [fits(group, st) for st in (oracle[-1:] if i == len(names) - 1 else oracle)]
            ob = min(oresults, key=len) if oresults else []
            check(any(not r for r in oresults), f"{scenario} step {step}: the oracle disagrees with the answer key in "
                  f"{len(ob)} value(s) (spec or generator gap); first: {ob[:3]}", "medium")  # fmt: skip
    return len(rows)


def truth_positions(check: b.Checker, co: Co, scenario: str, s: Stock, items: set[str] | None = None) -> None:
    rows = [r for r in co.truth2("truth_stock_position", scenario) if items is None or r.get("item_code") in items]
    if not check(bool(rows), f"{scenario}: truth_stock_position.csv has no rows (spec 9.4)", "medium"):
        return
    last = rows[-1].get("step", "")
    group = [r for r in rows if r.get("step", "") == last]
    places = {(r["item_code"], r["location_code"]) for r in group}
    got = positions_of(co, places)
    bad, obad = [], []
    for r in group:
        k = (r["item_code"], r["location_code"])
        mine = s.position(*k)
        for col in ("usable", "quarantine", "blocked", "reserved", "safety_stock", "available"):
            if dec(r.get(col)) is None:
                continue
            if got[k].get(col) is None or D(got[k][col]) != D(r[col]):
                bad.append(f"{k} {col} {got[k].get(col)} (key {r[col]})")
            if mine[col] != D(r[col]):
                obad.append(f"{k} {col} oracle {fq(mine[col])} (key {r[col]})")
    check(not bad, f"{scenario}: the inventory disagrees with truth_stock_position.csv: {bad[:4]}")
    check(not obad, f"{scenario}: the oracle disagrees with truth_stock_position.csv (spec or generator gap): "
          f"{obad[:4]}", "medium")  # fmt: skip


def parse_lines(spec: str) -> dict[str, Decimal]:
    out: dict[str, Decimal] = defaultdict(Decimal)
    for part in (spec or "").split(";"):
        item, _, qty = part.strip().partition(":")
        if item:
            out[item.strip()] += D(qty)
    return dict(out)


def truth_requests(check: b.Checker, co: Co, scenario: str, new: Iterable[str]) -> None:
    """`truth_stock_requests.csv` (last step) against the requests the scenario made: same lines and value; the
    approver when the request waits for one."""
    rows = co.truth2("truth_stock_requests", scenario)
    if not rows:
        return  # a scenario that makes no request may have no rows
    last = rows[-1].get("step", "")
    made = {n: (co.request_lines(n), co.request_head(n)) for n in new}
    for r in (x for x in rows if x.get("step", "") == last):
        want = parse_lines(r.get("lines", ""))
        hit = [
            n for n, (lines, head) in made.items() if lines == want and b.money(head["total"]) == b.money(D(r["value"]))
        ]
        check(bool(hit), f"{scenario}: truth_stock_requests.csv row {r.get('kind')} {r.get('order_no')} "
              f"{r.get('lines')} Rs {r.get('value')} has no matching request among {[(n, m[0]) for n, m in made.items()]}")  # fmt: skip
        if hit and r.get("priority"):
            check(made[hit[0]][1].get("priority") == r["priority"], f"{scenario}: request {hit[0]} priority "
                  f"{made[hit[0]][1].get('priority')}, key {r['priority']}", "medium")  # fmt: skip
        if hit and r.get("approver_code") and made[hit[0]][1].get("status") == "submitted":
            check(co.approver_of(hit[0]) == r["approver_code"], f"{scenario}: request {hit[0]} waits for "
                  f"{co.approver_of(hit[0])}, key {r['approver_code']}", "medium")  # fmt: skip


def scenario_known(check: b.Checker, co: Co, name: str) -> dict[str, Any] | None:
    found = co.scenario2(name)
    check(found is not None, f"{co.name}: truth/stock_scenarios.json has no scenario '{name}' (spec 9.2)", "medium")
    return found


def tenant_check(check: b.Checker, mine: Co, other: Co, before: dict[str, Any]) -> None:
    """Spec 13: another company lists none of this company's holds, risks or notes and cannot act on them (404)."""
    by = other.role("store")
    status, holds = other.holds(by)
    check(status == 200, f"{other.name}: GET /api/inventory/holds answered {status}")
    theirs = {h.get("id") for h in holds}
    for h in mine.q("select id from stock_hold where closed_at is null")[:3] if "stock_hold" in mine.tables() else []:
        if h[0] in theirs or h[0] in {x[0] for x in other.q("select id from stock_hold")}:
            continue
        r = other.decide(by, h[0], "1", "0", "eval: another company")
        check(r.status_code == 404, f"{other.name} deciding {mine.name}'s hold {h[0]} answered {r.status_code}")
    own_tasks = {c["task_id"] for c in other.risks()} | {r[0] for r in other.q("select id from task")}
    for c in mine.risks()[:2]:
        if c["task_id"] in own_tasks:
            continue
        r = other.call(other.role("buyer"), "POST", f"/api/supply-risks/{c['task_id']}/close",
                       {"outcome": "expedited", "note": "x"})  # fmt: skip
        check(r.status_code == 404, f"{other.name} closing {mine.name}'s supply risk {c['task_id']} answered "
              f"{r.status_code}")  # fmt: skip
    _, listed = other.supply_risks_api(other.role("buyer"), None)
    leaked = [x for x in listed if x.get("po_no") in {c["po_no"] for c in mine.risks()}
              and x.get("task_id") not in own_tasks]  # fmt: skip
    check(not leaked, f"{other.name} lists {len(leaked)} supply risk(s) of {mine.name}")
    mine_mrs = [no for _, no, _ in mine.mr_labels()][:2]
    other_mrs = {no for _, no, _ in other.mr_labels()}
    for no in mine_mrs:
        if no in other_mrs:
            continue
        r = other.call(by, "POST", f"/api/material-requests/{no}/explain", {})
        check(r.status_code == 404, f"{other.name} asking to explain {mine.name}'s {no} answered {r.status_code}")
    after = other.snapshot2()
    moved = [k for k in after if after[k] != before.get(k)]
    check(not moved, f"{mine.name}'s scenario changed {other.name}'s data: {moved}")


def second_run(check: b.Checker, co: Co, label: str, again: Any) -> None:
    """A second run with nothing changed changes nothing (spec 6.5.5)."""
    before = co.snapshot2()
    again()
    after = co.snapshot2()
    moved = [k for k in after if after[k] != before[k]]
    check(not moved, f"{label}: a second run changed {moved}")


@contextlib.contextmanager
def day(d: date) -> Iterator[None]:
    with b.on_day(d):
        yield


def start2(co: Co, check: b.Checker, wrong: Wrong = RIGHT2) -> tuple[Stock, dict[str, int]]:
    """The base state A0 / B0 (demo reset, then Run store check) on the platform and in the oracle."""
    r = co.base_state()
    check(r.status_code == 200, f"{co.name}: Run store check answered {r.status_code}: {r.text[:200]}")
    rank = {label: i for i, (label, _, _) in enumerate(co.mr_labels())}
    s = Stock(co.dataset, wrong, rank)
    s.store_check(tag="first")
    return s, rank


def base_ok(check: b.Checker, co: Co, s: Stock) -> None:
    """A0 / B0 as block 1's table: held lots and spare safety stock change none of it (spec 9.2)."""
    spec = b.SPEC_A0 if co.name == "apex" else b.SPEC_B0
    values = b.SPEC_A0_VALUES if co.name == "apex" else b.SPEC_B0_VALUES
    obs = s.observe()
    mine = {mr: {i: (v["reserved"], v["requested"]) for (m, i), v in obs.items() if m == mr} for mr in spec}
    check(mine == spec, f"{co.name}: the oracle's base state is not block 1's table (oracle self-check)", "medium")
    b.agree(check, f"{co.name} base state", s, co)
    for mr, value in values.items():
        got = sum((co.request(n).get("total", Z) for n in co.request_of(mr)), Z)
        check(b.money(got) == value, f"{co.name} {mr}: purchase request worth {got}, expected {value} (block 1 table)")


def held_ok(check: b.Checker, co: Co) -> list[dict[str, str]]:
    """The dataset's held lots (spec 9.2) exactly as written there."""
    lots = held_lots(co.dataset)
    want = {
        "apex": {("H-APX-01", "RM-07", "quarantine", "20"), ("H-APX-02", "RM-06", "blocked", "8")},
        "brightwave": {("H-BWT-01", "MN-01", "quarantine", "2"), ("H-BWT-02", "LP-01", "blocked", "1")},
    }[co.name]
    got = {(r.get("id", ""), r.get("item_code", ""), r.get("quality", ""), fq(D(r.get("qty")))) for r in lots}
    check(got == want, f"{co.name}: history/stock_held.csv holds {sorted(got)}, the spec {sorted(want)} (spec 9.2)",
          "medium")  # fmt: skip
    return lots


def at(d: date, hour: int = 10) -> datetime:
    return datetime.combine(d, time(hour, 0), tzinfo=IST)


def numbers_in(text_: str) -> set[str]:
    return set(re.findall(r"(?<![\w.])\d+(?:\.\d+)?(?![\w.])", text_))


def with_wrong(s: Stock, **flags: bool) -> Wrong:
    return replace(s.wrong, **flags)
