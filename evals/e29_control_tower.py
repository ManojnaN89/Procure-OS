"""E29: the control tower, the key figures, the alerts and the role homes, recomputed without the platform's own code.

The company is built from the plant dataset's history (orders, receipts, requests, RFQs, quotes, awards, invoices entered
through the platform's intake and match, the paid ones marked paid on the dates of the dataset's payments). Every figure is
then worked out again here in plain Python from the raw rows: ageing is counted by walking the calendar one day at a time,
medians and percentiles come from sorted lists, savings and on-time delivery come straight from the dataset's CSV files.
The alert rules are re-derived the same way; the engine must raise exactly those, once, to exactly the roles named, and
resolve them when the cause goes. The role homes must show each role only its own kind of work."""

import itertools
import random
import statistics
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import lib
from lib import D, Outcome, rows
from replay import insert, replay
from sqlalchemy import text

ID = "E29"
# Block 13 (spec 10.1): savings are potential and realised from the price measures, never the first quote.
TITLE = "The control tower, the key figures and the alerts agree with an independent recount"
REQUIREMENT = (
    "Plan P1-21/P1-23: every role sees what needs it and nothing else; the control tower shows every case by stage, location "
    "and age in working days; the key figures reconcile with the raw data; alerts are raised once, routed by role, and "
    "resolved when their cause goes away"
)

IST = timezone(timedelta(hours=5, minutes=30))
TODAY = lib.TODAY
HOLIDAYS = {date(2026, 10, 2), date(2026, 10, 20), date(2026, 11, 2)}
STAGES = [
    "request_approval", "sourcing", "rfq_out", "award", "po_approval", "po_sent", "awaiting_receipt",
    "awaiting_invoice", "invoice_blocked", "awaiting_payment",
]  # fmt: skip
BUCKETS = ["0-2", "3-7", "8-14", "15+"]
CENT = Decimal("0.01")


# ---- the recount tools (written without the platform's formulas) --------------------------------------------


def walk_days(start: date, end: date, holidays: set[date]) -> int:
    """Working days after `start`, up to and including `end`, found by stepping through the calendar."""
    n, d = 0, start
    while d < end:
        d += timedelta(days=1)
        if d.weekday() < 5 and d not in holidays:
            n += 1
    return n


def bucket_of(n: int) -> str:
    return "0-2" if n <= 2 else "3-7" if n <= 7 else "8-14" if n <= 14 else "15+"


def ist_date(v: Any) -> date:
    return v.astimezone(IST).date() if isinstance(v, datetime) else v


def at(days_back: int, hour: int = 11) -> datetime:
    return datetime.combine(TODAY - timedelta(days=days_back), time(hour), tzinfo=IST)


def q(db: Any, sql: str, **params: Any) -> list[dict[str, Any]]:
    return [dict(r) for r in db.execute(text(sql), params).mappings()]


def pct1(part: int, whole: int) -> str | None:
    return None if not whole else str((Decimal(part) * 100 / Decimal(whole)).quantize(Decimal("0.1"), ROUND_HALF_UP))


def one_dp(v: float | Decimal) -> str:
    return str(Decimal(str(v)).quantize(Decimal("0.1"), ROUND_HALF_UP))


# ---- the company ------------------------------------------------------------------------------------------


def prepare(world: Any) -> dict[str, Any]:
    db = world.db
    done = replay(world)
    for d in sorted(HOLIDAYS):
        db.execute(
            text("insert into ref_holiday (holiday_date, name, kind, scope) values (:d, 'e29', 'closed', 'central')"),
            {"d": d},
        )
    first_request: dict[str, str] = {}
    for line in rows("history", "rfq_line"):
        first_request.setdefault(line["rfq_no"], line["request_no"])
    for name in ("request", "request_line", "rfq", "rfq_line", "quote", "quote_line", "award"):
        for r in rows("history", name):
            insert(db, name, r, {"request_no": first_request.get(r["no"])} if name == "rfq" else None)
        db.commit()
    # which request an order answers, and when each request was decided (from the dataset's approval events)
    answered: dict[str, str] = {}
    for line in rows("history", "po_line"):
        answered.setdefault(line["po_no"], line["request_no"])  # (an order's lines answer one request)
    for po_no, request_no in answered.items():
        db.execute(text("update po set request_no = :r where no = :p"), {"r": request_no, "p": po_no})
    decided: dict[str, str] = {}
    for e in rows("history", "approval_event"):
        if (
            e["document"] == "request"
            and e["action"] in ("approved", "auto_approved")
            and e["at"] > decided.get(e["document_no"], "")
        ):
            decided[e["document_no"]] = e["at"]
    for no, when in decided.items():
        db.execute(
            text("update request set decided_at = :t where no = :n"), {"t": datetime.fromisoformat(when), "n": no}
        )
    # contract releases are bought on a contract; the other lines were awarded
    db.execute(
        text(
            "update po_line set source = 'contract' where po_no in (select no from po where contract_code is not null)"
        )
    )
    # invoices the dataset shows as paid are marked paid on the dataset's payment dates
    paid_on = {p["no"]: p["paid_on"] for p in rows("history", "payment")}
    by_invoice: dict[str, str] = {}
    for a in rows("history", "payment_allocation"):
        by_invoice[a["invoice_no"]] = max(by_invoice.get(a["invoice_no"], ""), paid_on[a["payment_no"]])
    marked = 0
    for ds_no, platform_no in done.platform_no.items():
        if ds_no in by_invoice and done.entered[ds_no].status == "approved":  # type: ignore[attr-defined]
            db.execute(
                text("update invoice set status = 'paid', paid_on = :d where no = :n"),
                {"d": date.fromisoformat(by_invoice[ds_no]), "n": platform_no},
            )
            marked += 1
    db.commit()
    # a few cases in the early stages, which the dataset (all history) has none of
    requests = sorted(r["no"] for r in rows("history", "request"))
    for k, no in zip((0, 1, 3, 5, 9, 13, 21), requests[:7], strict=False):
        db.execute(
            text("update request set status = 'submitted', submitted_at = :t where no = :n"), {"t": at(k), "n": no}
        )
    for k, no in zip((1, 4, 8, 16), requests[10:14], strict=False):
        db.execute(text("update request set status = 'approved', decided_at = :t where no = :n"), {"t": at(k), "n": no})
        db.execute(text("update request_line set status = 'open' where request_no = :n"), {"n": no})
    quoted = sorted({r["rfq_no"] for r in rows("history", "quote")})
    for no in quoted[:4]:
        db.execute(text("update rfq set status = 'sent' where no = :n"), {"n": no})
    # contracts: one ends in 5 days, one in 30 (the edge of the warning), one in 31 (outside it)
    for code, days in zip(sorted(c["code"] for c in rows("master", "contract"))[:3], (5, 30, 31), strict=False):
        db.execute(
            text("update contract set status = 'active', valid_to = :d where code = :c"),
            {"d": TODAY + timedelta(days=days), "c": code},
        )
    template = rows("history", "rfq")[0]
    for i, (k, req) in enumerate(zip((1, 6, 12), requests[20:23], strict=False)):
        insert(
            db, "rfq",
            {**template, "no": f"RFQ-E29-{i}", "status": "sent", "created_at": at(k).isoformat(), "sent_at": at(k).isoformat(), "due_date": (TODAY - timedelta(days=2)).isoformat()},
            {"request_no": req},
        )  # fmt: skip
    # people who touched some requests, so that the "no person touched it" figure has something to find
    from p2p.platform import audit
    from p2p.platform.sod import Actor

    with_orders = [
        r["no"] for r in q(db, "select distinct request_no as no from po where request_no is not null order by 1")
    ]
    touched = set(with_orders[0:3])
    for no in with_orders[0:3]:
        audit.record(db, Actor("user", "1"), "x", "request", no)
    po_of = q(db, "select no, request_no from po where request_no = :r order by no", r=with_orders[3])
    audit.record(db, Actor("user", "1"), "x", "po", po_of[0]["no"])
    touched.add(with_orders[3])
    db.commit()
    return {"replay": done, "marked_paid": marked, "touched": touched}


# ---- the control tower, recounted ---------------------------------------------------------------------------


def tower_oracle(db: Any, today: date) -> dict[str, list[dict[str, Any]]]:
    items: dict[str, list[dict[str, Any]]] = {k: [] for k in STAGES}

    def add(stage: str, id_: str, loc: Any, value: Any, since: date) -> None:
        items[stage].append({"id": id_, "loc": loc, "value": D(value), "since": since})

    reqs = {r["no"]: r for r in q(db, "select * from request")}
    decisions = {(r["request_no"], r["line_no"]) for r in q(db, "select request_no, line_no from sourcing_decision")}
    unsourced = {
        l["request_no"]
        for l in q(db, "select request_no, line_no, status from request_line")
        if l["status"] == "open" and (l["request_no"], l["line_no"]) not in decisions
    }
    for r in reqs.values():
        if r["status"] == "submitted":
            add(
                "request_approval",
                r["no"],
                r["ship_to_location"],
                r["total_value"],
                ist_date(r["submitted_at"] or r["created_at"]),
            )
        if r["status"] == "approved" and r["no"] in unsourced:
            add(
                "sourcing",
                r["no"],
                r["ship_to_location"],
                r["total_value"],
                ist_date(r["decided_at"] or r["created_at"]),
            )
    quotes: dict[str, list[datetime]] = defaultdict(list)
    for r in q(db, "select rfq_no, received_at from quote"):
        quotes[r["rfq_no"]].append(r["received_at"])
    for f in q(db, "select no, request_no, status, created_at, sent_at from rfq"):
        if f["status"] != "sent" or f["request_no"] not in reqs:
            continue
        rq = reqs[f["request_no"]]
        got = quotes.get(f["no"])
        add(
            "award" if got else "rfq_out",
            f["no"],
            rq["ship_to_location"],
            rq["total_value"],
            ist_date(min(got) if got else (f["sent_at"] or f["created_at"])),
        )
    lines: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for l in q(db, "select * from po_line"):
        lines[l["po_no"]].append(l)
    gates: dict[str, list[datetime]] = defaultdict(list)
    accepted: dict[str, list[date]] = defaultdict(list)
    for g in q(db, "select po_no, gate_entry_at, accepted_on from goods_receipt"):
        gates[g["po_no"]].append(g["gate_entry_at"])
        if g["accepted_on"]:
            accepted[g["po_no"]].append(g["accepted_on"])
    blocked_pos = {
        r["po_no"]
        for r in q(
            db,
            "select l.po_no from invoice_line l join invoice i on i.no = l.invoice_no where i.type = 'invoice' and i.status = 'blocked' and i.payment_block <> 'duplicate'",
        )
    }
    for p in q(db, "select * from po"):
        no, st = p["no"], p["status"]
        if st in ("draft", "submitted"):
            add("po_approval", no, p["ship_to_location"], p["total"], ist_date(p["created_at"]))
        if st in ("approved", "sent"):
            add(
                "po_sent",
                no,
                p["ship_to_location"],
                p["total"],
                ist_date(p["sent_at"] or p["approved_at"] or p["created_at"]),
            )
        if st in ("acknowledged", "part_received"):
            since = max(gates[no]) if gates[no] else (p["acknowledged_at"] or p["sent_at"] or p["created_at"])
            value = sum(
                (
                    max(l["qty"] - l["received_qty"], 0) * l["unit_price"]
                    for l in lines[no]
                    if l["status"] in ("open", "part_received")
                ),
                D(0),
            )
            add("awaiting_receipt", no, p["ship_to_location"], value, ist_date(since))
        if st not in ("draft", "submitted", "cancelled") and no not in blocked_pos:
            owed = [l for l in lines[no] if l["status"] != "cancelled" and l["received_qty"] > l["invoiced_qty"]]
            if owed:
                value = sum(((l["received_qty"] - l["invoiced_qty"]) * l["unit_price"] for l in owed), D(0))
                add(
                    "awaiting_invoice",
                    no,
                    p["ship_to_location"],
                    value,
                    max(accepted[no]) if accepted[no] else ist_date(p["created_at"]),
                )
    location_of = {p["no"]: p["ship_to_location"] for p in q(db, "select no, ship_to_location from po")}
    first_line: dict[str, str] = {}
    for l in q(db, "select invoice_no, po_no from invoice_line order by invoice_no, line_no"):
        first_line.setdefault(l["invoice_no"], l["po_no"])
    for i in q(db, "select no, type, status, payment_block, total, received_on, booked_at from invoice"):
        if i["type"] != "invoice":
            continue
        loc = location_of.get(first_line.get(i["no"], ""))
        if i["status"] == "blocked" and i["payment_block"] != "duplicate":
            add("invoice_blocked", i["no"], loc, i["total"], i["received_on"])
        if i["status"] == "approved":
            add(
                "awaiting_payment",
                i["no"],
                loc,
                i["total"],
                ist_date(i["booked_at"]) if i["booked_at"] else i["received_on"],
            )
    return items


def compare_tower(db: Any, today: date, holidays: set[date], location: str | None, wrong: list[str]) -> dict[str, int]:
    from p2p.formulas.calendar import BusinessCalendar
    from p2p.platform import control_tower

    cal = BusinessCalendar.of(holidays)
    got = control_tower.tower(db, today, cal, location)
    want = tower_oracle(db, today)
    counts: dict[str, int] = {}
    if [s["stage"] for s in got["stages"]] != STAGES:
        wrong.append(f"stages are {[s['stage'] for s in got['stages']]}")
    for stage in got["stages"]:
        key = stage["stage"]
        mine = [i for i in want.get(key, []) if location in (None, "") or i["loc"] == location]
        ages = [(i, walk_days(i["since"], today, holidays)) for i in mine]
        buckets = dict.fromkeys(BUCKETS, 0)
        by_loc: dict[str, list[Decimal]] = defaultdict(list)
        for i, n in ages:
            buckets[bucket_of(n)] += 1
            by_loc[i["loc"] or "-"].append(i["value"])
        expect = {
            "count": len(mine),
            "value": str(sum((i["value"] for i in mine), D(0)).quantize(CENT)),
            "buckets": buckets,
            "oldest_days": max((n for _, n in ages), default=0),
            "by_location": {
                k: {"count": len(v), "value": str(sum(v, D(0)).quantize(CENT))} for k, v in sorted(by_loc.items())
            },
        }
        actual = {k: stage[k] for k in expect}
        if actual != expect:
            wrong.append(f"{key} [{location or 'all'}]: expected {expect}, got {actual}")
        counts[key] = len(mine)
        for bucket in BUCKETS:  # the drill-down lists exactly the cases the counts claim
            ids = sorted(i["id"] for i, n in ages if bucket_of(n) == bucket)
            shown = sorted(r["id"] for r in control_tower.drill(db, key, today, cal, bucket, location))
            if ids != shown:
                wrong.append(f"{key} drill {bucket} [{location or 'all'}]: expected {ids[:4]}.., got {shown[:4]}..")
    return counts


# ---- the key figures, recounted -----------------------------------------------------------------------------


def kpi_oracle(db: Any, world_info: dict[str, Any], holidays: set[date]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    live_po = {p["no"] for p in q(db, "select no from po where status not in ('draft', 'submitted', 'cancelled')")}
    # spend under management
    managed = contract = total = D(0)
    for l in q(db, "select po_no, status, source, taxable from po_line"):
        if l["po_no"] in live_po and l["status"] != "cancelled":
            total += l["taxable"]
            managed += l["taxable"] if l["source"] in ("contract", "award") else 0
            contract += l["taxable"] if l["source"] == "contract" else 0
    out["spend"] = (pct1(int(managed * 100), int(total * 100)), pct1(int(contract * 100), int(total * 100)), str(total))
    # savings, from the price measures' truth (block 13, DF-29): potential and realised are never one sum
    measures = rows("truth", "truth_price_measures")
    potential = sum(
        (D(r["amount"]) for r in measures if r["measure"] == "saving_potential"), D(0)
    ).quantize(CENT)
    realised = sum(
        (D(r["amount"]) for r in measures if r["measure"] == "saving_realised"), D(0)
    ).quantize(CENT)
    awards = len({r["source_ref"] for r in measures if r["measure"] == "saving_potential"})
    out["savings"] = {"potential": str(potential), "realised": str(realised), "awards": awards}
    # on-time delivery, from the dataset files
    po_status = {p["no"]: p["status"] for p in rows("history", "po")}
    gate = {g["no"]: ist_date(datetime.fromisoformat(g["gate_entry_at"])) for g in rows("history", "goods_receipt")}
    first_in: dict[tuple[str, str], date] = {}
    for g in rows("history", "gr_line"):
        if D(g["qty_received"]) > 0:
            key = (g["po_no"], g["po_line_no"])
            first_in[key] = min(first_in.get(key, date.max), gate[g["gr_no"]])
    judged = on_time = 0
    for l in rows("history", "po_line"):
        if po_status[l["po_no"]] in ("draft", "submitted", "cancelled"):
            continue
        promised = l["promised_date"] or l["need_by"]
        got = first_in.get((l["po_no"], l["line_no"]))
        if got and promised:
            judged += 1
            on_time += got <= date.fromisoformat(promised)
    out["on_time"] = (judged, pct1(on_time, judged))
    # invoices matched first time: from the match results, and the dataset's own answer key
    results = defaultdict(set)
    for m in q(db, "select invoice_no, result from match_result"):
        results[m["invoice_no"]].add(m["result"])
    seen = [
        i
        for i in q(db, "select no, type, payment_block from invoice")
        if i["type"] == "invoice" and i["payment_block"] != "duplicate" and results.get(i["no"])
    ]
    out["first_pass"] = (len(seen), pct1(sum(1 for i in seen if "blocked" not in results[i["no"]]), len(seen)))
    done = world_info["replay"]
    expected_ok = total_judged = 0
    for ds_no, platform_no in done.platform_no.items():
        want = done.truth.get(ds_no)
        if want is None or done.by_no[ds_no]["type"] != "invoice" or done.by_no[ds_no]["payment_block"] == "duplicate":
            continue
        if platform_no in results:
            total_judged += 1
            expected_ok += want["result"] != "blocked"
    out["first_pass_truth"] = pct1(expected_ok, total_judged)
    # small suppliers paid on time
    msme = {s["code"] for s in q(db, "select code from supplier where msme_category in ('micro', 'small')")}
    paid = [
        i
        for i in q(db, "select supplier_code, status, paid_on, due_date, type from invoice")
        if i["type"] == "invoice"
        and i["status"] == "paid"
        and i["paid_on"]
        and i["due_date"]
        and i["supplier_code"] in msme
    ]
    out["msme"] = (len(paid), pct1(sum(1 for i in paid if i["paid_on"] <= i["due_date"]), len(paid)))
    # duplicates stopped: invoices blocked or set aside as a copy; requests that looked like a repeat and were given up
    # with nobody going ahead on a reason
    copies = [
        i
        for i in q(db, "select status, payment_block, total from invoice")
        if i["payment_block"] == "duplicate" and i["status"] in ("blocked", "cancelled")
    ]
    flagged = {c["request_no"] for c in q(db, "select request_no from request_check where kind = 'duplicate'")}
    gone_ahead = {
        c["request_no"]
        for c in q(db, "select request_no, override_reason from request_check where kind = 'duplicate'")
        if c["override_reason"]
    }
    given_up = [
        r
        for r in q(db, "select no, status, total_value from request")
        if r["no"] in flagged - gone_ahead and r["status"] in ("cancelled", "rejected")
    ]
    out["duplicates"] = {
        "requests": {"count": len(given_up), "value": str(sum((r["total_value"] for r in given_up), D(0)))},
        "invoices": {"count": len(copies), "value": str(sum((i["total"] for i in copies), D(0)))},
    }
    out["duplicates_in_dataset"] = sum(1 for r in rows("history", "invoice") if r["payment_block"] == "duplicate")
    # touched by nobody
    with_order = {
        p["request_no"]
        for p in q(db, "select request_no from po where status <> 'cancelled' and request_no is not null")
    }
    out["touchless"] = (len(with_order), pct1(len(with_order - world_info["touched"]), len(with_order)))
    # cycle times, request to paid, by stage
    pos_of: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for p in q(db, "select * from po where status <> 'cancelled' and request_no is not null"):
        pos_of[p["request_no"]].append(p)
    invoices_of_po: dict[str, set[str]] = defaultdict(set)
    for l in q(db, "select invoice_no, po_no from invoice_line"):
        invoices_of_po[l["po_no"]].add(l["invoice_no"])
    invoices = {i["no"]: i for i in q(db, "select * from invoice")}
    first_gate: dict[str, datetime] = {}
    for g in q(db, "select po_no, gate_entry_at from goods_receipt"):
        first_gate[g["po_no"]] = min(first_gate.get(g["po_no"], g["gate_entry_at"]), g["gate_entry_at"])
    stage_days: dict[str, list[int]] = defaultdict(list)
    reqs = {r["no"]: r for r in q(db, "select no, created_at, decided_at from request")}
    for no, pos in pos_of.items():
        if no not in reqs:
            continue
        mine = {
            n
            for p in pos
            for n in invoices_of_po[p["no"]]
            if invoices[n]["type"] == "invoice" and invoices[n]["status"] != "cancelled"
        }
        if not mine or any(invoices[n]["status"] != "paid" for n in mine):
            continue
        r = reqs[no]
        marks = {
            "created": ist_date(r["created_at"]), "approved": ist_date(r["decided_at"]) if r["decided_at"] else None,
            "po_created": min(ist_date(p["created_at"]) for p in pos),
            "po_approved": ist_date(max(p["approved_at"] for p in pos if p["approved_at"])) if any(p["approved_at"] for p in pos) else None,
            "sent": ist_date(min(p["sent_at"] for p in pos if p["sent_at"])) if any(p["sent_at"] for p in pos) else None,
            "received": ist_date(min(first_gate[p["no"]] for p in pos if p["no"] in first_gate)) if any(p["no"] in first_gate for p in pos) else None,
            "invoiced": ist_date(min(invoices[n]["received_at"] for n in mine)), "paid": max(invoices[n]["paid_on"] for n in mine),
        }  # fmt: skip
        for name, a, b in (
            ("approval", "created", "approved"), ("ordering", "approved", "po_created"), ("po_approval", "po_created", "po_approved"),
            ("dispatch", "po_approved", "sent"), ("delivery", "sent", "received"), ("invoicing", "received", "invoiced"),
            ("payment", "invoiced", "paid"), ("total", "created", "paid"),
        ):  # fmt: skip
            if marks[a] and marks[b]:
                stage_days[name].append(walk_days(marks[a], marks[b], holidays))
    out["cycle"] = {
        name: {
            "cases": len(v),
            "median": one_dp(statistics.median(v)) if v else None,
            "p90": one_dp(sorted(v)[(9 * len(v) + 9) // 10 - 1]) if v else None,
        }
        for name in ("approval", "ordering", "po_approval", "dispatch", "delivery", "invoicing", "payment", "total")
        for v in [stage_days[name]]
    }
    return out


def compare_kpis(got: dict[str, Any], want: dict[str, Any], wrong: list[str]) -> None:
    def check(name: str, actual: Any, expected: Any) -> None:
        if actual != expected:
            wrong.append(f"{name}: expected {expected}, got {actual}")

    s = got["spend_under_management"]
    check("spend under management", (s["pct"], s["contract_pct"], s["total"]), want["spend"])
    check("savings", got["savings"], want["savings"])
    check("on-time delivery", (got["on_time_delivery"]["lines"], got["on_time_delivery"]["pct"]), want["on_time"])
    check("first-pass match", (got["first_pass_match"]["invoices"], got["first_pass_match"]["pct"]), want["first_pass"])
    check(
        "small suppliers paid on time",
        (got["msme_on_time_payment"]["invoices"], got["msme_on_time_payment"]["pct"]),
        want["msme"],
    )
    check("touchless", (got["touchless"]["requests"], got["touchless"]["pct"]), want["touchless"])
    d = got["duplicates_avoided"]
    check("duplicates avoided", {"requests": d["requests"], "invoices": d["invoices"]}, want["duplicates"])
    check(
        "duplicate invoices stopped against the dataset's planted copies",
        d["invoices"]["count"],
        want["duplicates_in_dataset"],
    )
    for name, w in want["cycle"].items():
        g = got["cycle_time"][name]
        check(f"cycle time {name}", {"cases": g["cases"], "median": g["median"], "p90": g["p90"]}, w)


# ---- the alerts, recounted ----------------------------------------------------------------------------------


def alert_oracle(db: Any, today: date, holidays: set[date]) -> dict[str, set[str]]:
    want: dict[str, set[str]] = defaultdict(set)
    for r in q(db, "select no, submitted_at, created_at from request where status = 'submitted'"):
        if walk_days(ist_date(r["submitted_at"] or r["created_at"]), today, holidays) > 3:
            want["stale_requisition"].add(f"stale_requisition:{r['no']}")
    have_quote = {r["rfq_no"] for r in q(db, "select rfq_no from quote")}
    for r in q(db, "select no, due_date from rfq where status = 'sent'"):
        if r["due_date"] < today and r["no"] not in have_quote:
            want["rfq_no_quotes"].add(f"rfq_no_quotes:{r['no']}")
    open_status = {p["no"]: p["status"] for p in q(db, "select no, status from po")}
    for l in q(db, "select po_no, promised_date, status, qty, received_qty from po_line"):
        if (
            l["promised_date"]
            and l["promised_date"] < today
            and l["status"] in ("open", "part_received")
            and l["received_qty"] < l["qty"]
            and open_status[l["po_no"]] in ("sent", "acknowledged", "part_received")
        ):
            want["late_delivery"].add(f"late_delivery:{l['po_no']}")
    for i in q(db, "select no, type, status, payment_block, received_on, due_date from invoice"):
        if i["type"] != "invoice":
            continue
        if (
            i["status"] == "blocked"
            and i["payment_block"] != "duplicate"
            and walk_days(i["received_on"], today, holidays) > 3
        ):
            want["invoice_blocked"].add(f"invoice_blocked:{i['no']}")
        if i["status"] == "approved" and i["due_date"] and i["due_date"] <= today + timedelta(days=3):
            want["invoice_due"].add(f"invoice_due:{i['no']}")
    for c in q(db, "select code, valid_to from contract where status = 'active'"):
        if c["valid_to"] and today <= c["valid_to"] <= today + timedelta(days=30):
            want["contract_expiring"].add(f"contract_expiring:{c['code']}")
    return want


def run_alerts(world: Any, holidays: set[date], out: Outcome, wrong: list[str]) -> dict[str, Any]:
    from p2p.platform import alerts

    db = world.db
    first = alerts.run(db, world.slug, today=TODAY)
    if first.failed_rules:
        wrong.append(f"alert rules failed: {first.failed_rules}")
    rows_ = q(db, "select * from alert where status = 'open'")
    keys_by_rule: dict[str, set[str]] = defaultdict(set)
    for r in rows_:
        keys_by_rule[r["rule"]].add(r["key"])
    want = alert_oracle(db, TODAY, holidays)
    for rule, keys in want.items():
        if keys_by_rule[rule] != keys:
            wrong.append(
                f"alert {rule}: expected {sorted(keys)[:5]} ({len(keys)}), raised {sorted(keys_by_rule[rule])[:5]} ({len(keys_by_rule[rule])})"
            )
    for rule in ("stale_requisition", "rfq_no_quotes", "invoice_blocked"):
        if not want[rule]:
            out.notes.append(
                f"nothing to raise for {rule} in this company, so its rule is checked only against an empty list"
            )
    sent = {r["po_no"] for r in q(db, "select p.no as po_no from po p where p.status = 'sent'")}
    for key in keys_by_rule["po_not_acknowledged"]:
        if key.split(":", 1)[1] not in sent:
            wrong.append(f"{key} is raised for an order that is not waiting for an acknowledgement")
    # raised once: a second run changes nothing and sends nothing
    before = q(db, "select count(*) as n from notification where related_type = 'alert'")[0]["n"]
    again = alerts.run(db, world.slug, today=TODAY)
    after = q(db, "select count(*) as n from notification where related_type = 'alert'")[0]["n"]
    if again.opened or again.resolved or after != before:
        wrong.append(
            f"a second run raised {len(again.opened)}, resolved {len(again.resolved)} and sent {after - before} more notifications"
        )
    # routed: each person with a role in the alert has exactly one notification for it, nobody else has any
    role_of = {uid: role for role, uid in world.logins.items()}
    routed_wrong = 0
    for a in rows_:
        wanted_roles = ROLES_FOR[a["rule"]]  # who should hear about it: written here, not read back from the alert
        if set(a["roles"].split(",")) != wanted_roles:
            wrong.append(f"{a['rule']} is addressed to {a['roles']}, expected {sorted(wanted_roles)}")
            break
    for a in rows_:
        wanted_roles = ROLES_FOR[a["rule"]]
        for uid, role in role_of.items():
            n = q(
                db,
                "select count(*) as n from notification where related_type = 'alert' and related_id = :a and user_id = :u",
                a=str(a["id"]),
                u=uid,
            )[0]["n"]
            if n != (1 if role in wanted_roles else 0):
                routed_wrong += 1
    if routed_wrong:
        wrong.append(f"{routed_wrong} notification(s) went to the wrong people or were sent twice")
    return {"raised": len(rows_), "by_rule": {k: len(v) for k, v in sorted(keys_by_rule.items())}, "want": want}


def resolve_and_return(world: Any, want: dict[str, set[str]], wrong: list[str]) -> None:
    """Take away the cause of every blocked-invoice alert: they resolve with their notifications; put it back: raised again."""
    from p2p.platform import alerts

    db = world.db
    keys = sorted(want["invoice_blocked"])
    if not keys:
        wrong.append("no blocked invoice alert to resolve: the company has no old blocked invoices")
        return
    numbers = [k.split(":", 1)[1] for k in keys]
    db.execute(text("update invoice set status = 'cancelled' where no = any(:n)"), {"n": numbers})
    db.commit()
    gone = alerts.run(db, world.slug, today=TODAY)
    if sorted(k for k in gone.resolved if k.startswith("invoice_blocked")) != keys:
        wrong.append(f"after the cause went, resolved {gone.resolved[:4]}.., expected {keys[:4]}..")
    open_left = q(
        db,
        "select count(*) as n from notification n join alert a on a.id::text = n.related_id where n.related_type = 'alert' and a.key = any(:k) and not n.resolved",
        k=keys,
    )[0]["n"]
    if open_left:
        wrong.append(f"{open_left} notification(s) of resolved alerts are still open")
    db.execute(text("update invoice set status = 'blocked' where no = any(:n)"), {"n": numbers})
    db.commit()
    back = alerts.run(db, world.slug, today=TODAY)
    if sorted(k for k in back.opened if k.startswith("invoice_blocked")) != keys:
        wrong.append(f"when the cause came back, raised {back.opened[:4]}.., expected {keys[:4]}..")
    times = q(db, "select min(times_opened) as lo from alert where key = any(:k)", k=keys)[0]["lo"]
    if times != 2:
        wrong.append(f"a returning alert should count as opened twice, not {times}")


# ---- the role homes -----------------------------------------------------------------------------------------

ROLES_FOR = {
    "stale_requisition": {"Approver"}, "rfq_no_quotes": {"Buyer"}, "po_not_acknowledged": {"Buyer"}, "late_delivery": {"Buyer"},
    "invoice_blocked": {"AP"}, "invoice_due": {"Finance"}, "contract_expiring": {"Buyer"}, "contract_ceiling": {"Buyer"},
    "supplier_documents": {"Buyer"}, "budget_used": {"Approver", "Finance"},
}  # fmt: skip
PROPOSALS = {"rfq_sends", "payment_runs", "releases", "suppliers", "variances"}
ALLOWED = {
    "Admin": set(),  # replaced below: the administrator sees every section, read only
    "Requester": {"drafts", "returned"},
    "Buyer": {"to_source", "rfq_wait", "rfq_award", "to_send", "to_acknowledge", "drafts"} | PROPOSALS,
    "Approver": {"requests", "orders"} | PROPOSALS,
    "Store": {"to_receive", "inspections"},
    "AP": {"to_invoice", "blocked"},
    "Finance": {"due", "approve_wait", "to_file", "to_pay"} | PROPOSALS,
}

ALLOWED["Admin"] = set().union(*[v for k, v in ALLOWED.items() if k != "Admin"]) | {"restock"}


def check_homes(world: Any, tower_counts: dict[str, int], wrong: list[str]) -> dict[str, Any]:
    from fastapi.testclient import TestClient
    from p2p.formulas.calendar import pin_today
    from p2p.main import app

    client = TestClient(app)
    pin_today(TODAY)
    seen: dict[str, Any] = {}
    try:
        for role in lib.ROLES:
            body = client.get("/api/workspace", headers=world.headers(role)).json()
            keys = {s["key"] for s in body["sections"]}
            seen[role] = sorted(keys)
            if not keys <= ALLOWED[role]:
                wrong.append(f"{role} is shown work that is not theirs: {sorted(keys - ALLOWED[role])}")
            if any(
                not s["rows"] or s["count"] < len(s["rows"]) or any(not r["link"] for r in s["rows"])
                for s in body["sections"]
            ):
                wrong.append(f"{role}: a section is empty, miscounted or has a row without a link")
            for a in body["alerts"]:
                mine = q(world.db, "select roles from alert where id = :i", i=a["id"])[0]["roles"].split(",")
                if role not in mine:
                    wrong.append(f"{role} is shown an alert addressed to {mine}")
            if role == "AP":
                sec = {s["key"]: s for s in body["sections"]}
                if sec.get("blocked", {}).get("count", 0) != tower_counts["invoice_blocked"]:
                    wrong.append(
                        f"AP sees {sec.get('blocked', {}).get('count', 0)} blocked invoices, the tower has {tower_counts['invoice_blocked']}"
                    )
                if sec.get("to_invoice", {}).get("count", 0) != tower_counts["awaiting_invoice"]:
                    wrong.append(
                        f"AP sees {sec.get('to_invoice', {}).get('count', 0)} orders to invoice, the tower has {tower_counts['awaiting_invoice']}"
                    )
            if role == "Buyer":
                sec = {s["key"]: s for s in body["sections"]}
                if sec.get("to_source", {}).get("count", 0) != tower_counts["sourcing"]:
                    wrong.append(
                        f"the buyer sees {sec.get('to_source', {}).get('count', 0)} requests to source, the tower has {tower_counts['sourcing']}"
                    )
                for key, stage in (("rfq_wait", "rfq_out"), ("rfq_award", "award")):
                    if sec.get(key, {}).get("count", 0) != tower_counts[stage]:
                        wrong.append(
                            f"the buyer sees {sec.get(key, {}).get('count', 0)} in '{key}', the tower has {tower_counts[stage]} in '{stage}'"
                        )
            if role in ("Admin", "Requester") and body["alerts"]:
                wrong.append(f"{role} is shown alerts that are not for that role")
        for path in ("/api/workspace", "/api/control-tower", "/api/control-tower/po_sent", "/api/kpis", "/api/alerts"):
            if client.get(path).status_code != 401:
                wrong.append(f"{path} is open without a login")
        api = client.get("/api/control-tower", headers=world.headers("Buyer")).json()
        if {s["stage"]: s["count"] for s in api["stages"]} != tower_counts:
            wrong.append("the control tower endpoint disagrees with the recount")
        k = client.get("/api/kpis", headers=world.headers("Finance")).json()
        seen["kpi_keys"] = sorted(k)
    finally:
        pin_today(TODAY)
    return seen


# ---- the formulas, against brute force ----------------------------------------------------------------------


def check_formulas(wrong: list[str]) -> int:
    from p2p.formulas import kpis as k
    from p2p.formulas.calendar import BusinessCalendar

    rng = random.Random(29)
    cal = BusinessCalendar.of(HOLIDAYS)
    checks = 0
    base = date(2026, 9, 1)
    pairs = [
        (base + timedelta(days=rng.randrange(0, 90)), base + timedelta(days=rng.randrange(0, 90))) for _ in range(600)
    ]
    pairs += [
        (a, a + timedelta(days=n)) for a, n in itertools.product([date(2026, 10, 1), date(2026, 10, 3)], range(0, 25))
    ]
    for a, b in pairs:
        checks += 1
        if k.working_days_between(a, b, cal) != walk_days(a, b, HOLIDAYS):
            wrong.append(
                f"working days {a}..{b}: {k.working_days_between(a, b, cal)}, counted {walk_days(a, b, HOLIDAYS)}"
            )
            break
    for n in range(0, 80):
        checks += 1
        if k.bucket_of(n) != bucket_of(n):
            wrong.append(f"bucket of {n} days: {k.bucket_of(n)}, expected {bucket_of(n)}")
    for _ in range(300):
        values = [rng.randrange(0, 40) for _ in range(rng.randrange(1, 40))]
        checks += 1
        if str(k.median(values)) != one_dp(statistics.median(values)) or str(k.percentile_90(values)) != one_dp(
            sorted(values)[(9 * len(values) + 9) // 10 - 1]
        ):
            wrong.append(f"median/p90 of {values}: {k.median(values)}/{k.percentile_90(values)}")
            break
    return checks


def run(ctx) -> Outcome:
    out = Outcome(True)
    wrong: list[str] = []
    formula_checks = check_formulas(wrong)

    world = lib.build_world("e29", with_stock=False)
    info = prepare(world)
    db = world.db

    tower_counts: dict[str, int] = {}
    for location in [None] + [r["code"] for r in q(db, "select code from location order by code")]:
        counts = compare_tower(db, TODAY, HOLIDAYS, location, wrong)
        if location is None:
            tower_counts = counts
    out.metrics["control tower: cases by stage"] = tower_counts

    from p2p.formulas.calendar import BusinessCalendar
    from p2p.platform import control_tower

    got = control_tower.kpis(db, BusinessCalendar.of(HOLIDAYS))
    want = kpi_oracle(db, info, HOLIDAYS)
    compare_kpis(got, want, wrong)
    out.metrics["key figures"] = {
        "spend under management % / on contract %": want["spend"][:2],
        "savings": want["savings"],
        "on-time delivery (lines, %)": want["on_time"],
        "invoices matched first time (n, %)": want["first_pass"],
        "  … by the dataset's answer key %": want["first_pass_truth"],
        "small suppliers paid on time (n, %)": want["msme"],
        "touchless (requests, %)": want["touchless"],
        "duplicates stopped (requests, invoices)": want["duplicates"],
        "cycle time, request to paid, working days": want["cycle"]["total"],
        "paid invoices set up from the dataset": info["marked_paid"],
    }
    if want["first_pass"][1] and want["first_pass_truth"]:
        gap = abs(Decimal(want["first_pass"][1]) - Decimal(want["first_pass_truth"]))
        out.metrics["first-pass gap to the dataset's answer key, points"] = str(gap)
        if gap > 3:
            out.find(
                "medium",
                f"the first-pass match rate is {want['first_pass'][1]}% but the dataset's answer key says {want['first_pass_truth']}%",
            )
    if want["cycle"]["total"]["cases"] == 0:
        out.find("medium", "no request reached 'paid' in this company, so the cycle times were not exercised")

    ran = run_alerts(world, HOLIDAYS, out, wrong)
    out.metrics["alerts raised, by rule"] = ran["by_rule"]
    resolve_and_return(world, ran["want"], wrong)
    out.metrics["role homes: sections shown"] = check_homes(world, tower_counts, wrong)
    out.metrics["formula checks against brute force"] = formula_checks
    for rule in (
        "stale_requisition",
        "rfq_no_quotes",
        "invoice_blocked",
        "late_delivery",
        "invoice_due",
        "contract_expiring",
    ):
        out.metrics.setdefault("alert rules compared with the recount", []).append(f"{rule}: {len(ran['want'][rule])}")

    if wrong:
        out.find("high", f"{len(wrong)} difference(s) from the independent recount; first: {wrong[0]}")
        out.notes.append("differences: " + " | ".join(w[:240] for w in wrong[:10]))
    return out
