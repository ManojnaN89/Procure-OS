"""E59: price types are kept apart; only verified, fresh, in-region benchmarks count (task 13.1, D41, D52), written without
the implementation.

Independence: written from the block 0b spec (sections 7 and 6.3 rule 2) and the dataset only. The oracle reads
`market_benchmark.csv`, `price_record.csv`, `contract.csv`, `item.csv` (standard price), `history/po.csv` and
`po_line.csv` (last price paid, months with orders), `uom_conversion.csv`, `location.csv` and `price_threshold_rule.csv`.
The dataset's order history is planted into the company under its own numbers first, so the platform's "last price paid"
and the oracle's come from the same orders. On 20 Nov 2026 a benchmark counts at a location when it is verified, its
effective date is at most `evidence_max_age_days` (45) days back, its region is empty or the location's region, and its
unit is the item's or has a conversion; otherwise the reason is "unverified", "stale: N days old", "other region" or
"unit not convertible".

Cases: the price view of RM-01 at Chakan and Sanand and of LP-01 at Bengaluru equals the oracle section by section, and no
benchmark price appears under another type's label; every dataset benchmark's status and age; the reasons, with 45 days
fresh and 46 stale; the enterer and a buyer refused verification, an approver verifies, twice is refused, a verified
record cannot be edited and a correction replaces it; an upload with three bad rows lists exactly those and loads the rest
unverified, a second upload loads nothing, a changed price is listed and not applied; halving every benchmark price
changes no estimate, buying route or touchless decision; another company reads none of it. Two broken rules (an
unverified benchmark counted; a benchmark used as the estimate) must be told apart by the cases.
"""

import csv
import io
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction
from typing import Any

import env
import lib
from block0b import Checker, Company, dec, dicts, js, listed, on_day, pick, section
from lib import D, Outcome
from sqlalchemy import text

ID = "E59"
TITLE = (
    "Price types are kept apart; only verified, fresh, in-region, convertible benchmarks count, and never as a price"
)
REQUIREMENT = (
    "Task 13.1, D41, D52: contract price, quote, last price paid, price history, standard price and benchmark shown in "
    "their own sections; a benchmark carries source, date, region, unit and status, counts only when verified, fresh, "
    "in region and convertible, is verified by someone other than who entered it, is never edited once verified, and "
    "is never read by an estimate, route, order or touchless check; uploads list bad rows and skip repeats"
)

DAY = date(2026, 11, 20)  # the spec's day for the benchmark statuses
# spec 7.4 names the listing's content ("records with status, who, age in days"), not its key
LIST_KEYS = ("benchmarks", "records", "items", "rows")


def n_changed(answer: Any) -> int:
    """`changed` of an upload: a count, or the rows listed as "already loaded; changed" (spec 7.3 rule 2 lists them)."""
    got = pick(answer, "changed", default=0)
    return len(got) if isinstance(got, list) else int(got or 0)


P4 = Decimal("0.0001")


# ---- the oracle -------------------------------------------------------------------------------------------------------


class Prices:
    def __init__(self, c: Company) -> None:
        self.c = c
        self.region = c.region_of()
        self.items = {r["code"]: r for r in c.rows("master", "item")}
        self.conv = {(r["item_code"], r["from_uom"]) for r in c.rows("master", "uom_conversion")} | {
            (r["item_code"], r["to_uom"]) for r in c.rows("master", "uom_conversion")
        }
        self.contracts = {r["code"]: r for r in c.rows("master", "contract")}
        self.prices = c.rows("master", "price_record")
        self.bench = c.rows("master", "market_benchmark")
        rule = next((r for r in c.rows("master", "price_threshold_rule") if not r.get("category_code")), {})
        self.max_age = int(rule.get("evidence_max_age_days") or 45)
        self.po = {r["no"]: r for r in c.rows("history", "po")}
        self.lines = c.rows("history", "po_line")

    def uom(self, item: str) -> str:
        it = self.items[item]
        return it.get("purchase_uom") or it.get("uom") or it.get("unit") or "NOS"

    def reason(self, b: dict[str, str], location: str, day: date, *, count_unverified: bool = False) -> str:
        eff = date.fromisoformat(b["effective_date"])
        if b.get("status") != "verified" and not count_unverified:
            return "unverified"
        if (day - eff).days > self.max_age or eff > day:
            return f"stale: {(day - eff).days} days old"
        if b.get("region_code") and b["region_code"] != self.region.get(location):
            return "other region"
        if b["uom"] != self.uom(b["item_code"]) and (b["item_code"], b["uom"]) not in self.conv:
            return "unit not convertible"
        return "counts"

    def contract(self, item: str, location: str, day: date) -> set[tuple[str, Decimal]]:
        found = set()
        for p in self.prices:
            if p["item_code"] != item or not p["contract_code"] or (p.get("status") or "active") != "active":
                continue
            k = self.contracts.get(p["contract_code"], {})
            scoped = (
                (not p.get("location_code") and not p.get("region_code"))
                or p.get("location_code") == location
                or (p.get("region_code") and p["region_code"] == self.region.get(location))
            )
            if (
                date.fromisoformat(p["valid_from"]) <= day <= date.fromisoformat(p["valid_to"])
                and k.get("status") == "active"
                and scoped
            ):
                found.add((p["supplier_code"], D(p["price"])))
        return found

    def last_paid(self, item: str, day: date) -> Decimal | None:
        mine = [ln for ln in self.lines if ln["item_code"] == item and self.po[ln["po_no"]]["status"] != "cancelled"
                and self.po[ln["po_no"]]["created_at"][:10] <= day.isoformat()]  # fmt: skip
        if not mine:
            return None
        return D(max(mine, key=lambda ln: (self.po[ln["po_no"]]["created_at"], ln["po_no"]))["unit_price"])

    def months(self, item: str, day: date) -> set[str]:
        start = (day - timedelta(days=365)).isoformat()
        return {self.po[ln["po_no"]]["created_at"][:7] for ln in self.lines if ln["item_code"] == item
                and self.po[ln["po_no"]]["status"] != "cancelled" and start <= self.po[ln["po_no"]]["created_at"][:10] <= day.isoformat()}  # fmt: skip

    def market_gap(self, item: str, location: str, day: date) -> tuple[Decimal, Decimal] | None:
        counted = [
            D(b["price"]) for b in self.bench if b["item_code"] == item and self.reason(b, location, day) == "counts"
        ]
        prices = self.contract(item, location, day)
        if not counted or not prices:
            return None
        cp = min(p for _, p in prices)
        rs = Fraction(cp) - Fraction(min(counted))
        pct = rs / Fraction(cp) * 100
        return Decimal(rs.numerator) / Decimal(rs.denominator), (
            Decimal(pct.numerator) / Decimal(pct.denominator)
        ).quantize(P4, rounding=ROUND_HALF_UP)


def prices_in(rows: list[dict[str, Any]] | None) -> set[Decimal]:
    return {
        d for r in rows or [] for k in ("price", "unit_price", "value", "amount") if (d := dec(r.get(k))) is not None
    }


def standard_rows(payload: Any) -> list[dict[str, Any]] | None:
    """The standard price section. Spec silent on the JSON shape: a list or dict section, or a plain value under a key
    naming the standard price."""
    rows = section(payload, "standard")
    if rows is None and isinstance(payload, dict):
        rows = [{"price": v} for k, v in payload.items() if "standard" in k.lower() and not isinstance(v, (dict, list))]
    return rows or None


def gap_rows(payload: Any) -> list[dict[str, Any]]:
    """The market gap figures. Spec silent on the JSON shape: a section of its own, or a gap shown with each contract
    price (any dict under a key naming the gap)."""
    rows = list(section(payload, "gap") or [])
    for d in dicts(payload):
        for k, v in d.items():
            if "gap" in k.lower() and isinstance(v, dict):
                rows.append(v)
    return rows


def plant_history(c: Company) -> None:
    """The dataset's orders, under their own numbers with an H- prefix, so 'last price paid' has orders to read."""
    from replay import insert

    for r in c.rows("history", "po"):
        insert(c.db, "po", {**r, "no": "H-" + r["no"]})
    for r in c.rows("history", "po_line"):
        insert(c.db, "po_line", {**r, "po_no": "H-" + r["po_no"]},
               {"received_qty": 0, "invoiced_qty": 0, "source": "award", "request_no": "H-" + r["po_no"],
                "request_line_no": int(r["line_no"]), "taxable": (D(r["qty"]) * D(r["unit_price"])).quantize(D("0.01"))})  # fmt: skip
    c.db.commit()


# ---- the eval -------------------------------------------------------------------------------------------------------


def run(ctx: lib.Ctx) -> Outcome:
    env.forget_tenants("e59")
    try:
        return _run(ctx)
    finally:
        env.forget_tenants("e59")


def _run(ctx: lib.Ctx) -> Outcome:  # noqa: C901 - one scenario, step by step
    from fastapi.testclient import TestClient

    from p2p.main import app

    out = Outcome(True)
    check = Checker(out)
    lib.capture_mail()
    client = TestClient(app)
    c = Company("apex", "e59", client)
    o = Prices(c)
    caught = {"an unverified benchmark counted": False, "a benchmark used as the estimate": False}
    if not check(bool(o.bench), "the Apex dataset has no market_benchmark.csv rows"):
        return out
    plant_history(c)

    def view(co: Company, item: str, location: str) -> Any:
        r = co.call("U03", "GET", f"/api/items/{item}/prices", params={"location": location})
        check(
            r.status_code == 200,
            f"{co.name}: the price view of {item} at {location} answered {r.status_code} {r.text[:120]}",
            "medium",
        )
        return js(r)

    def bench_reasons(payload: Any) -> dict[tuple[str, str, str], str]:
        """(source, effective date, region) -> the row as shown."""
        found = {}
        for r in section(payload, "benchmark", "market_price") or []:
            key = (
                str(pick(r, "source_name", "source", default="")),
                str(pick(r, "effective_date", "date", default="")),
                str(pick(r, "region_code", "region", default="") or ""),
            )
            found[key] = str(r).lower()
        return found

    def reason_ok(want: str, shown: str) -> bool:
        if want == "counts":
            return not any(w in shown for w in ("unverified", "stale", "other region", "not convertible"))
        if want.startswith("stale"):
            return "stale" in shown and want.split()[1] in shown
        return want in shown

    # ---- 1. the price view, section by section ----------------------------------------------------------------------
    with on_day(DAY):
        for item, loc in (("RM-01", "PLT-CKN"), ("RM-01", "PLT-SND"), ("RM-08", "PLT-CKN"), ("PK-01", "PLT-CKN")):
            payload = view(c, item, loc)
            contract = {
                (str(pick(r, "supplier_code", "supplier")), dec(pick(r, "price", "unit_price")))
                for r in section(payload, "contract") or []
            }
            check(
                contract == o.contract(item, loc, DAY),
                f"{item} at {loc}: contract section {sorted(contract)}, oracle {sorted(o.contract(item, loc, DAY))}",
            )
            std = prices_in(standard_rows(payload))
            check(D(o.items[item]["standard_price"]) in std,
                  f"{item} at {loc}: standard price section {std}, item.csv {o.items[item]['standard_price']}", "medium")  # fmt: skip
            if loc == "PLT-CKN":
                last = prices_in(section(payload, "last"))
                want_last = o.last_paid(item, DAY)
                check(
                    want_last is None and not last or want_last in last,
                    f"{item}: last price paid section {last}, oracle {want_last}",
                    "medium",
                )
                months = {
                    m
                    for r in section(payload, "history") or []
                    for v in r.values()
                    for m in [str(v)[:7]]
                    if len(str(v)) >= 7 and str(v)[4] == "-"
                }
                check(
                    months == o.months(item, DAY),
                    f"{item}: price history months {sorted(months)}, oracle {sorted(o.months(item, DAY))}",
                    "medium",
                )
            own = [b for b in o.bench if b["item_code"] == item]
            others = (
                prices_in(section(payload, "contract"))
                | prices_in(section(payload, "last"))
                | prices_in(standard_rows(payload))
            )
            leaked = {D(b["price"]) for b in own} & others - {p for _, p in o.contract(item, loc, DAY)} - {
                D(o.items[item]["standard_price"])
            } - {o.last_paid(item, DAY)}
            check(
                not leaked,
                f"{item} at {loc}: benchmark prices {sorted(leaked)} appear under another price type's label",
            )
            shown = bench_reasons(payload)
            wrong = []
            for b in own:
                want = o.reason(b, loc, DAY)
                got = shown.get((b["source_name"], b["effective_date"], b.get("region_code") or ""))
                if got is None or not reason_ok(want, got):
                    wrong.append(
                        f"{b['effective_date']} {b.get('region_code') or 'national'} {b['status']}: {want}, shown {(got or 'missing')[:80]}"
                    )
            out.metrics[f"{item} at {loc}: benchmark reasons off the oracle"] = f"{len(wrong)} of {len(own)}"
            for w in wrong[:4]:
                out.find("high", f"{item} at {loc} on {DAY}: {w}")
            want_gap = o.market_gap(item, loc, DAY)
            if want_gap:
                gaps = {d for x in dicts(gap_rows(payload)) for v in x.values() if (d := dec(v)) is not None}
                check(
                    want_gap[1] in gaps and want_gap[0] in gaps,
                    f"{item} at {loc}: market gap shown {sorted(gaps)}, oracle Rs {want_gap[0]} / {want_gap[1]}%",
                    "medium",
                )
                check(
                    "evidence" in str(gap_rows(payload)).lower() or "evidence" in str(payload).lower(),
                    f"{item}: the market gap is not labelled evidence only",
                    "low",
                )
        rm08 = [b for b in o.bench if b["item_code"] == "RM-08"]
        counts08 = [b for b in rm08 if o.reason(b, "PLT-CKN", DAY) == "counts"]
        mutant08 = [b for b in rm08 if o.reason(b, "PLT-CKN", DAY, count_unverified=True) == "counts"]
        shown08 = bench_reasons(view(c, "RM-08", "PLT-CKN"))
        platform_counts = [k for k, v in shown08.items() if reason_ok("counts", v)]
        out.metrics["RM-08 counting benchmarks on 20 Nov (oracle / unverified-counted / platform)"] = (
            f"{len(counts08)} / {len(mutant08)} / {len(platform_counts)}"
        )
        check(
            not counts08 and len(mutant08) >= 1,
            "the dataset should leave RM-08 with no counting benchmark until October is verified",
            "medium",
        )
        caught["an unverified benchmark counted"] = len(mutant08) != len(counts08) and len(platform_counts) == len(
            counts08
        )

    # every dataset benchmark: status and age on 20 Nov
    with on_day(DAY):
        listed_ = listed(js(c.call("U03", "GET", "/api/benchmarks")), *LIST_KEYS)
        by_key = {
            (
                pick(r, "item_code", "item"),
                str(pick(r, "source_name", "source")),
                str(pick(r, "effective_date")),
                str(pick(r, "region_code", "region") or ""),
            ): r
            for r in listed_
        }
        bad = []
        for b in o.bench:
            r = by_key.get((b["item_code"], b["source_name"], b["effective_date"], b.get("region_code") or ""))
            age = (DAY - date.fromisoformat(b["effective_date"])).days
            if r is None or pick(r, "status") != b["status"] or int(pick(r, "age_days", "age", default=-1)) != age:
                bad.append(
                    f"{b['item_code']} {b['effective_date']}: listed {r and (pick(r, 'status'), pick(r, 'age_days', 'age'))}, file {b['status']} {age} days"
                )
        out.metrics["dataset benchmarks listed right (status, age)"] = f"{len(o.bench) - len(bad)} of {len(o.bench)}"
        for w in bad[:5]:
            out.find("high", w)

    # ---- 2. 45 days fresh, 46 stale; verification by someone else ------------------------------------------------------
    def enter(by: str, item: str, price: str, eff: date, source: str, region: str = "", **extra: Any) -> Any:
        body = {"item_code": item, "price": price, "uom": o.uom(item), "basis": "delivered", "effective_date": eff.isoformat(),
                "region_code": region or None, "source_kind": "price_list", "source_name": source, "source_ref": "eval",
                "note": "synthetic, E59", **extra}  # fmt: skip
        return c.call(by, "POST", "/api/benchmarks", body)

    ids: dict[str, int] = {}
    for label, eff in (("45", DAY - timedelta(days=45)), ("46", DAY - timedelta(days=46))):
        r = enter("U03", "RM-06", "36.00", eff, f"E59 bearing list {label}")
        ids[label] = int(pick(js(r), "id", default=0) or 0)
        check(
            r.status_code in (200, 201) and pick(js(r), "status") == "unverified",
            f"entering a benchmark: {r.status_code} {r.text[:150]}",
        )
        c.call("U07", "POST", f"/api/benchmarks/{ids[label]}/verify", {"note": "E59"})
    with on_day(DAY):
        shown = bench_reasons(view(c, "RM-06", "PLT-CKN"))
        r45 = shown.get(("E59 bearing list 45", (DAY - timedelta(days=45)).isoformat(), ""), "")
        r46 = shown.get(("E59 bearing list 46", (DAY - timedelta(days=46)).isoformat(), ""), "")
    check(reason_ok("counts", r45) and bool(r45), f"a verified benchmark 45 days old does not count: {r45[:120]}")
    check(
        reason_ok("stale: 46 days old", r46),
        f"a verified benchmark 46 days old is not shown 'stale: 46 days old': {r46[:120]}",
    )

    r = enter("U08", "RM-06", "35.50", lib.TODAY, "E59 approver's own entry")
    mine = int(pick(js(r), "id", default=0) or 0)
    tries = {
        "the approver who entered it": c.call(
            "U08", "POST", f"/api/benchmarks/{mine}/verify", {"note": "mine"}
        ).status_code,
        "a buyer": c.call("U03", "POST", f"/api/benchmarks/{mine}/verify", {"note": "x"}).status_code,
        "reject with no reason": c.call("U07", "POST", f"/api/benchmarks/{mine}/reject", {"reason": ""}).status_code,
        "another approver": c.call(
            "U07", "POST", f"/api/benchmarks/{mine}/verify", {"note": "E59 checked the list"}
        ).status_code,
        "verified twice": c.call("U09", "POST", f"/api/benchmarks/{mine}/verify", {"note": "again"}).status_code,
        "reject a verified one": c.call(
            "U09", "POST", f"/api/benchmarks/{mine}/reject", {"reason": "late"}
        ).status_code,
    }
    out.metrics["verification"] = tries
    want = {
        "the approver who entered it": 403,
        "a buyer": 403,
        "reject with no reason": 422,
        "another approver": 200,
        "verified twice": 409,
        "reject a verified one": 409,
    }
    check(tries == want, f"verification answers {tries}, expected {want}")
    edits = {m: c.call("U03", m, f"/api/benchmarks/{mine}", {"price": "1.00"}).status_code for m in ("PUT", "PATCH")}
    check(
        all(s in (405, 409) for s in edits.values()),
        f"editing a verified benchmark answered {edits} (409, or no edit route at all)",
    )
    if any(s == 405 for s in edits.values()):
        out.notes.append(f"a verified benchmark has no edit route ({edits}); the spec's 409 has no endpoint named")
    r = enter("U03", "RM-06", "35.75", lib.TODAY, "E59 approver's own entry corrected", replaces=mine)
    new = int(pick(js(r), "id", default=0) or 0)
    c.call("U07", "POST", f"/api/benchmarks/{new}/verify", {"note": "E59 correction"})
    old = next(
        (
            x
            for x in listed(js(c.call("U03", "GET", "/api/benchmarks", params={"item": "RM-06"})), *LIST_KEYS)
            if pick(x, "id") == mine
        ),
        {},
    )
    check(
        pick(old, "status") == "rejected" and f"#{new}" in str(old),
        f"the corrected benchmark {mine} is {pick(old, 'status')} ({str(old)[:150]}); expected rejected 'replaced by #{new}'",
    )
    trail = {a: len(c.audit(a)) for a in ("benchmark.add", "benchmark.verify", "benchmark.reject")}
    check(all(trail.values()), f"benchmark audit rows: {trail}", "medium")

    # ---- 3. an upload: three bad rows listed, the rest loaded unverified, a repeat loads nothing ------------------------
    tpl = c.call("U03", "GET", "/api/benchmarks/template")
    header = _header(tpl)
    good = [("RM-09", "82.00", "2026-11-02", "E59 sheet capacitor"), ("RM-10", "57.00", "2026-11-02", "E59 sheet terminal box"),
            ("RM-11", "335.00", "2026-11-02", "E59 sheet paint"), ("SP-01", "198.00", "2026-11-02", "E59 sheet belt")]  # fmt: skip
    bad = [("ZZ-99", "10.00", "2026-11-02", "E59 sheet unknown"), ("SP-02", "0", "2026-11-02", "E59 sheet zero"),
           ("SP-03", "170.00", "2026-12-31", "E59 sheet future")]  # fmt: skip
    rows_ = [good[0], good[1], bad[0], good[2], bad[1], good[3], bad[2]]
    bad_at = [3, 5, 7]
    if check(
        bool(header),
        f"the benchmark template has no header the eval can read: {tpl.status_code} {tpl.headers.get('content-type')}",
        "medium",
    ):

        def upload(rows: list[tuple[str, str, str, str]]) -> Any:
            body = _sheet(header, [(i, p, d, s, o.uom(i) if i in o.items else "NOS") for i, p, d, s in rows])
            return c.call("U03", "POST", "/api/benchmarks/upload", files={"file": ("benchmarks.csv", body, "text/csv")})

        before = c.one("select count(*) from market_benchmark")
        first = js(upload(rows_))
        lines = sorted(int(pick(x, "line", default=0)) for x in pick(first, "rejected", default=[]))
        out.metrics["upload 1 (loaded, skipped, changed, rejected lines)"] = (
            f"{pick(first, 'loaded')}, {pick(first, 'skipped')}, {pick(first, 'changed')}, {lines}"
        )
        check(
            pick(first, "loaded") == 4 and lines in (bad_at, [n + 1 for n in bad_at]),
            f"upload with three bad rows: {first}",
        )
        check(
            all(pick(x, "reason") for x in pick(first, "rejected", default=[])),
            "a rejected row has no reason",
            "medium",
        )
        check(
            c.one("select count(*) from market_benchmark") - before == 4, "the upload did not add exactly four records"
        )
        check(
            c.one(
                "select count(*) from market_benchmark where source_name like 'E59 sheet%' and status <> 'unverified'"
            )
            == 0,
            "an uploaded row is not unverified",
        )
        second = js(upload(rows_))
        out.metrics["upload 2"] = f"{pick(second, 'loaded')}, {pick(second, 'skipped')}"
        check(pick(second, "loaded") == 0 and pick(second, "skipped") == 4, f"the same sheet again: {second}")
        changed = js(upload([("RM-09", "83.00", "2026-11-02", "E59 sheet capacitor")]))
        price_now = c.one("select price from market_benchmark where source_name = 'E59 sheet capacitor'")
        check(
            n_changed(changed) == 1 and pick(changed, "loaded") == 0 and D(price_now) == D("82.00"),
            f"a changed price: {changed}, stored {price_now}",
        )
        check(bool(c.audit("benchmark.upload")), "benchmark.upload not audited", "medium")

    # ---- 4. evidence only: halving every benchmark price changes no estimate, route or touchless decision ---------------
    def snapshot() -> dict[str, tuple[Any, ...]]:
        found = {}
        for item in ("RM-01", "RM-08", "SP-01"):
            no = c.request("U01", item, "3", "PLT-CKN")
            c.work()
            ln = c.line("U03", no)
            po = (ln.get("source") or {}).get("po_no")
            touchless = js(c.call("U06", "GET", f"/api/purchase-orders/{po}")).get("touchless") if po else None
            route = c.one("select route from sourcing_decision where request_no = :n", n=no)
            found[item] = (dec(ln.get("est_price")), ln.get("est_source"), route, touchless)
        return found

    first_run = snapshot()
    c.db.execute(
        text("update market_benchmark set price = round(price / 2, 4)")
    )  # the spec's own test: change every benchmark price
    c.db.commit()
    second_run = snapshot()
    out.metrics["estimate, source, route, touchless before / after halving every benchmark"] = {
        k: f"{first_run[k]} / {second_run[k]}" for k in first_run
    }
    check(
        first_run == second_run,
        f"changing benchmark prices changed what the platform decides: {first_run} -> {second_run}",
    )
    est08 = first_run["RM-08"][0]
    want08 = o.last_paid("RM-08", lib.TODAY)
    bench08 = {D(b["price"]) for b in o.bench if b["item_code"] == "RM-08"}
    check(
        est08 is not None and est08 not in bench08,
        f"RM-08's estimate {est08} is one of its benchmark prices {sorted(bench08)}",
    )
    check(
        est08 == want08,
        f"RM-08's estimate {est08} ({first_run['RM-08'][1]}), oracle: the last price paid {want08}",
        "medium",
    )
    caught["a benchmark used as the estimate"] = (
        est08 is not None and est08 not in bench08 and first_run["RM-08"][1] != "benchmark"
    )

    # ---- 5. Brightwave: the laptop boundary on its own price view; it reads nothing of Apex's ---------------------------
    bw = Company("brightwave", "e59", client)
    bo = Prices(bw)
    with on_day(DAY):
        payload = view(bw, "LP-01", "OFF-BLR")
        contract = {
            (str(pick(r, "supplier_code", "supplier")), dec(pick(r, "price", "unit_price")))
            for r in section(payload, "contract") or []
        }
        check(
            contract == bo.contract("LP-01", "OFF-BLR", DAY),
            f"LP-01 contract section {contract}, oracle {bo.contract('LP-01', 'OFF-BLR', DAY)}",
        )
        want_gap = bo.market_gap("LP-01", "OFF-BLR", lib.TODAY)
        out.metrics["LP-01 market gap (oracle on 6 Nov)"] = str(want_gap)
        seen = str(js(bw.call("U03", "GET", "/api/benchmarks")))
    check("RM-0" not in seen and "PK-01" not in seen, "Brightwave's buyer lists Apex benchmarks")
    check(
        bw.call("U03", "GET", "/api/items/RM-01/prices", params={"location": "OFF-BLR"}).status_code == 404,
        "Brightwave reads Apex's item RM-01",
    )
    own_verified = [
        b for b in bo.bench + o.bench if b.get("verified_by") and b.get("verified_by") == b.get("entered_by")
    ]
    check(not own_verified, f"the dataset has benchmarks verified by their enterer: {len(own_verified)}", "medium")

    out.metrics["mutations caught"] = caught
    for name, ok in caught.items():
        check(ok, f"mutation '{name}' is not told apart by these cases", "medium")
    out.notes.append(
        "Not checked: the price-history values per month and price lists (rule 5 names no aggregate and no section for them)"
    )
    out.metrics["checks"] = f"{check.done - len(check.failed)} of {check.done} passed"
    return out


# ---- the upload sheet, written to the template's own header ---------------------------------------------------------


def _header(r: Any) -> list[str]:
    kind = r.headers.get("content-type", "")
    if r.status_code != 200:
        return []
    if "sheet" in kind or "excel" in kind or r.content[:2] == b"PK":
        try:
            from openpyxl import load_workbook

            ws = load_workbook(io.BytesIO(r.content), read_only=True).active
            return [str(x) for x in next(ws.iter_rows(max_row=1, values_only=True)) if x]
        except Exception:  # noqa: BLE001 - an unreadable template is reported by the caller
            return []
    first = r.content.decode("utf-8-sig").splitlines()
    return next(csv.reader(first[:1]), []) if first else []


def _sheet(header: list[str], rows: list[tuple[str, str, str, str, str]]) -> bytes:
    """Our rows under the template's columns, matched by the words in each column name."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    for item, price, day, source, uom in rows:
        line = []
        for col in header:
            h = col.lower()
            if "item" in h:
                line.append(item)
            elif "price" in h:
                line.append(price)
            elif "uom" in h or "unit" in h:
                line.append(uom)
            elif "basis" in h:
                line.append("delivered")
            elif "date" in h or "effective" in h:
                line.append(day)
            elif "region" in h:
                line.append("")
            elif "kind" in h:
                line.append("price_list")
            elif "ref" in h:
                line.append("eval sheet")
            elif "source" in h or "name" in h:
                line.append(source)
            elif "note" in h:
                line.append("synthetic, E59")
            else:
                line.append("")
        w.writerow(line)
    return buf.getvalue().encode("utf-8")
