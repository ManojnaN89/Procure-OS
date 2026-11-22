"""E16: the controls added at the end of Phase 1, each against an independent oracle:
supplier documents, budget check, contract ceilings, and reminders to silent suppliers."""

import random
from datetime import date, timedelta
from decimal import Decimal
from fractions import Fraction

import ai_harness as h
import lib
import pipeline
from ai_cases import Item, items_for_cases
from lib import D, Outcome, rows
from sqlalchemy import text

ID = "E16"
TITLE = "Documents, budget, contract ceilings and reminders do what the plan says"
REQUIREMENT = (
    "Plan, phase 1 controls: a supplier needs good documents, a request must fit its budget, a contract has a "
    "ceiling, and a silent supplier is reminded a limited number of times"
)

TODAY = lib.TODAY  # Friday 6 November 2026


# ---- A. supplier documents --------------------------------------------------------------------


def required_types(sup: dict) -> list[str]:
    need = ["pan"]  # no bank proof before payment (owner decision 9, 22 Nov 2026)
    if sup["gst_reg_type"] == "regular":
        need.append("gst_cert")
    if sup["msme_category"] in ("micro", "small", "medium"):
        need.append("udyam")
    return need


def documents(ctx, world, out: Outcome) -> None:
    from p2p.sourcing import documents as platform_docs
    from p2p.sourcing import service

    db = world.db
    suppliers = [s for s in rows("master", "supplier") if s["status"] == "active"]
    rng = random.Random(16)
    # the dataset as delivered: every active supplier has what it needs, so nobody is wrongly excluded
    wrongly_blocked = [s["code"] for s in suppliers if not platform_docs.check_supplier(db, s["code"], TODAY).ok]
    if wrongly_blocked:
        out.find(
            "high",
            f"{len(wrongly_blocked)} supplier(s) with complete documents are blocked, e.g. {wrongly_blocked[:3]}",
        )
    # now spoil documents in known ways and compare with what the plan says must happen
    kinds = ["missing", "unverified", "expired yesterday", "expires today", "expired but renewed"]
    chosen = rng.sample(suppliers, min(len(suppliers), 25 if ctx.quick else 50))
    wrong: list[str] = []
    should_be_blocked: set[str] = set()
    for n, sup in enumerate(chosen):
        kind = kinds[n % len(kinds)]
        doc = rng.choice(required_types(sup))
        code = sup["code"]
        if kind == "missing":
            db.execute(
                text("delete from supplier_document where supplier_code = :c and doc_type = :t"),
                {"c": code, "t": doc},
            )
            expected_ok = False
        elif kind == "unverified":
            db.execute(
                text("update supplier_document set verified = false where supplier_code = :c and doc_type = :t"),
                {"c": code, "t": doc},
            )
            expected_ok = False
        elif kind == "expired yesterday":
            db.execute(
                text("update supplier_document set expires_on = :d where supplier_code = :c and doc_type = :t"),
                {"c": code, "t": doc, "d": TODAY - timedelta(days=1)},
            )
            expected_ok = False
        elif kind == "expires today":
            db.execute(
                text("update supplier_document set expires_on = :d where supplier_code = :c and doc_type = :t"),
                {"c": code, "t": doc, "d": TODAY},
            )
            expected_ok = True  # still good on its last day
        else:
            db.execute(
                text("update supplier_document set expires_on = :d where supplier_code = :c and doc_type = :t"),
                {"c": code, "t": doc, "d": TODAY - timedelta(days=30)},
            )
            db.execute(
                text(
                    "insert into supplier_document (supplier_code, doc_type, number, issued_on, expires_on, verified) "
                    "values (:c, :t, 'NEW', :i, :e, true)"
                ),
                {"c": code, "t": doc, "i": TODAY - timedelta(days=5), "e": TODAY + timedelta(days=365)},
            )
            expected_ok = True
        db.commit()
        got = platform_docs.check_supplier(db, code, TODAY).ok
        if got != expected_ok:
            wrong.append(f"{code} {doc} {kind}: platform ok={got}, expected {expected_ok}")
        if not expected_ok:
            should_be_blocked.add(code)
    # optional documents never block
    optional_blocked = 0
    for sup in suppliers[:20]:
        db.execute(
            text(
                "update supplier_document set expires_on = :d where supplier_code = :c and doc_type in ('iso', 'licence', 'insurance', 'lower_tds_cert', 'msme_agreement')"
            ),
            {"c": sup["code"], "d": TODAY - timedelta(days=400)},
        )
    db.commit()
    for sup in suppliers[:20]:
        if sup["code"] in should_be_blocked:
            continue
        optional_blocked += not platform_docs.check_supplier(db, sup["code"], TODAY).ok
    if optional_blocked:
        out.find("high", f"{optional_blocked} supplier(s) were blocked by an expired optional document")
    # none of the blocked suppliers is ever asked to quote
    asked: set[str] = set()
    for cat in {r["category_code"] for r in rows("master", "supplier_category")}:
        invitees, _ = service.eligible_suppliers(db, cat, [], TODAY)
        asked |= {i.code for i in invitees}
    leaked = sorted(asked & should_be_blocked)
    if wrong:
        out.find("high", f"{len(wrong)} document verdict(s) differ from the plan; first: {wrong[0]}")
    if leaked:
        out.find("high", f"suppliers with bad documents are still asked to quote: {leaked[:3]}")
    out.metrics["documents: suppliers checked"] = len(suppliers)
    out.metrics["documents: spoiled cases"] = f"{len(chosen)} ({len(chosen) - len(wrong)} right)"
    out.metrics["documents: bad suppliers asked to quote"] = len(leaked)


# ---- B. budget --------------------------------------------------------------------------------


def budget(ctx, world, out: Outcome) -> None:
    from p2p.approvals import budget as platform_budget

    db = world.db
    lines = rows("master", "budget")
    pairs = sorted({(r["cost_centre_code"], r["gl_code"]) for r in lines if not r["project_code"] and r["cost_centre_code"]})
    rng = random.Random(17)
    first, last = "2026-04", TODAY.strftime("%Y-%m")
    location = rows("master", "location")[0]["code"]
    n_cases = 30 if ctx.quick else 80
    bad: list[str] = []
    over_seen = ok_seen = 0
    for i in range(n_cases):
        cc, gl = rng.choice(pairs)
        ytd = sum(
            (
                D(r["amount"])
                for r in lines
                if r["cost_centre_code"] == cc and r["gl_code"] == gl and first <= r["period"] <= last
            ),
            Decimal(0),
        )
        committed = rng.choice([Decimal(0), (ytd / 4).quantize(Decimal("0.01"))])
        delta = rng.choice([Decimal("-0.01"), Decimal("0"), Decimal("0.01"), Decimal("1000")])
        ask = (ytd - committed + delta).quantize(Decimal("0.01"))
        if ask <= 0:
            continue
        db.execute(text("delete from request where no like 'EVB-%'"))
        if committed > 0:
            db.execute(
                text(
                    "insert into request (no, ship_to_location, type, channel, status, total_value) values ('EVB-C', :l, 'goods', 'form', 'approved', :v)"
                ),
                {"l": location, "v": committed},
            )
            db.execute(
                text(
                    "insert into request_line (request_no, line_no, free_text, qty, est_price, cost_centre_code, gl_code, status) values ('EVB-C', 1, 'x', 1, :v, :c, :g, 'open')"
                ),
                {"v": committed, "c": cc, "g": gl},
            )
        db.execute(
            text(
                "insert into request (no, ship_to_location, type, channel, status, total_value) values ('EVB-N', :l, 'goods', 'form', 'draft', :v)"
            ),
            {"l": location, "v": ask},
        )
        db.execute(
            text(
                "insert into request_line (request_no, line_no, free_text, qty, est_price, cost_centre_code, gl_code, status) values ('EVB-N', 1, 'x', 1, :v, :c, :g, 'open')"
            ),
            {"v": ask, "c": cc, "g": gl},
        )
        db.commit()
        # the cost centre's own check (the location's budget is checked beside it, see E38)
        got = next(c for c in platform_budget.check_request(db, "EVB-N", TODAY) if c.level == "cost_centre")
        want_over = ask > ytd - committed
        want_by = (ask - (ytd - committed)) if want_over else Decimal("0.00")
        over_seen += want_over
        ok_seen += not want_over
        if (got.check.status == "over") != want_over or got.check.over_by != want_by or got.check.budget != ytd:
            bad.append(
                f"{cc}/{gl} ytd {ytd} committed {committed} ask {ask}: platform {got.check.status} over_by {got.check.over_by} budget {got.check.budget}"
            )
    db.execute(text("delete from request where no like 'EVB-%'"))
    db.commit()
    out.metrics["budget: cases (over / within)"] = f"{over_seen + ok_seen} ({over_seen} / {ok_seen})"
    out.metrics["budget: disagreements"] = len(bad)
    if bad:
        out.find("high", f"{len(bad)} budget verdict(s) differ from the oracle; first: {bad[0]}")
    if over_seen == 0 or ok_seen == 0:
        out.find("medium", "the budget cases did not cover both over and within budget")


# ---- C. contract ceilings ---------------------------------------------------------------------


def ceilings(ctx, out: Outcome) -> None:
    from p2p.formulas.contracts import ceiling_breach

    rng = random.Random(18)
    bad = 0
    n = 600 if ctx.quick else 3000
    for _ in range(n):
        ceiling = Decimal(rng.randint(1, 10**9)) / 100
        used = Decimal(rng.randint(0, 10**9)) / 100
        new = Decimal(rng.randint(0, 10**8)) / 100
        want = max(Fraction(used) + Fraction(new) - Fraction(ceiling), Fraction(0))  # exact, in paise
        got = ceiling_breach(ceiling, used, new)
        if Fraction(got) != want:
            bad += 1
        if (got > 0) != (used + new > ceiling):
            bad += 1
    edge = [
        ceiling_breach(D("100"), D("40"), D("60")) == 0,
        ceiling_breach(D("100"), D("40"), D("60.01")) == D("0.01"),
        ceiling_breach(None, D("1"), Decimal(10**9)) == 0,
    ]
    out.metrics["ceilings: random cases"] = n
    out.metrics["ceilings: disagreements"] = bad + (3 - sum(edge))
    if bad or not all(edge):
        out.find("high", f"contract ceiling arithmetic is wrong in {bad + (3 - sum(edge))} case(s)")


def ceiling_flow(ctx, out: Outcome) -> None:
    """Replay every order of the whole flow in time order: a contract may go over its ceiling only on an order that was held."""
    from p2p.orders import contracts as usage

    state = pipeline.get_state(ctx)
    db = state.world.db
    contracts = {r["code"]: D(r["max_value"]) if r["max_value"] else None for r in rows("master", "contract")}
    lines = db.execute(
        text(
            "select po.no, po.created_at, po.hold_reason, l.source_ref, l.taxable from po_line l join po on po.no = l.po_no "
            "where l.source = 'contract' and po.status <> 'cancelled' order by po.created_at, po.no"
        )
    ).all()
    running: dict[str, Decimal] = {}
    over_without_hold: list[str] = []
    per_po: dict[str, dict[str, Decimal]] = {}
    for po_no, _, hold, ref, taxable in lines:
        per_po.setdefault(po_no, {}).setdefault(ref, Decimal(0))
        per_po[po_no][ref] += Decimal(taxable)
        per_po[po_no]["_hold"] = hold or ""  # type: ignore[assignment]
    seen: set[str] = set()
    for po_no, _, _, _, _ in lines:
        if po_no in seen:
            continue
        seen.add(po_no)
        parts = per_po[po_no]
        for ref, amount in parts.items():
            if ref == "_hold":
                continue
            running[ref] = running.get(ref, Decimal(0)) + amount
            ceiling = contracts.get(ref)
            if ceiling is not None and running[ref] > ceiling and "ceiling" not in str(parts["_hold"]):
                over_without_hold.append(f"{po_no} took {ref} to {running[ref]} of {ceiling} without a hold")
    # the platform's own usage figure equals the replay
    mismatches = [c for c, v in running.items() if usage.usage(db, c).used != v]
    out.metrics["ceilings: contract orders replayed"] = len(seen)
    out.metrics["ceilings: usage figures that differ from the replay"] = len(mismatches)
    if over_without_hold:
        out.find("high", f"a contract went over its ceiling without a hold: {over_without_hold[0]}")
    if mismatches:
        out.find(
            "high", f"the usage of {len(mismatches)} contract(s) differs from an independent sum: {mismatches[:3]}"
        )


# ---- D. reminders -----------------------------------------------------------------------------


def working_days_after(start: date, n: int) -> date:
    """n working days (Monday to Friday) after a date, counted by hand."""
    day, left = start, n
    while left:
        day += timedelta(days=1)
        left -= day.weekday() < 5
    return day


def reminders(ctx, world, out: Outcome) -> None:
    from p2p.sourcing import reminders as platform_reminders
    from datetime import UTC, datetime

    db = world.db
    box = lib.capture_mail()
    pool = items_for_cases()
    it = Item(1, pool[0].code, pool[0].name, pool[0].qty, pool[0].est, pool[0].gst)
    rfq, supplier, email = h.setup_rfq(world, "rem", [it])
    cats = sorted(
        {
            r["supplier_code"]
            for r in rows("master", "supplier_category")
            if r["category_code"] == lib.busiest_category()
        }
        - {supplier}
    )[:3]
    contacts = {c["supplier_code"]: c["email"] for c in rows("master", "supplier_contact") if c["email"]}
    silent = [supplier, cats[0]]
    replied, declined = cats[1], cats[2]
    for code in (cats[0], replied, declined):
        db.execute(
            text(
                "insert into rfq_supplier (rfq_no, supplier_code, email, invited_at) values (:r, :s, :e, now()) on conflict do nothing"
            ),
            {"r": rfq, "s": code, "e": contacts.get(code, f"{code.lower()}@eval.example")},
        )
    sent_day, due = TODAY, TODAY + timedelta(days=10)
    db.execute(text("update rfq set due_date = :d where no = :r"), {"d": due, "r": rfq})
    db.execute(
        text("update rfq_supplier set invited_at = :t where rfq_no = :r"),
        {"t": datetime(2026, 11, 6, 4, 30, tzinfo=UTC), "r": rfq},
    )
    db.execute(
        text("update rfq_supplier set declined = true where rfq_no = :r and supplier_code = :s"),
        {"r": rfq, "s": declined},
    )
    db.commit()
    h.deliver(world, rfq, contacts.get(replied, f"{replied.lower()}@eval.example"), "We will send our rates tomorrow.")

    after_days, max_reminders = 2, 2
    expected: list[date] = []
    last = sent_day
    for _ in range(max_reminders):
        nxt = working_days_after(last, after_days)
        if nxt > due:
            break
        expected.append(nxt)
        last = nxt
    got_days: dict[str, list[date]] = {s: [] for s in silent}
    stray: list[str] = []
    for offset in range(0, 16):
        day = sent_day + timedelta(days=offset)
        before = len(box.sent)
        done = platform_reminders.run(db, as_of=day, now=datetime(day.year, day.month, day.day, 4, 30, tzinfo=UTC))
        for r, s in done.sent:
            if s in got_days:
                got_days[s].append(day)
            else:
                stray.append(f"{s} reminded on {day}")
        if len(box.sent) - before != len(done.sent):
            stray.append(f"{day}: {len(done.sent)} reminders recorded but {len(box.sent) - before} emails sent")
    wrong = {s: d for s, d in got_days.items() if d != expected}
    out.metrics["reminders: expected days"] = ", ".join(f"{d:%a %d %b}" for d in expected)
    out.metrics["reminders: silent suppliers on schedule"] = f"{len(silent) - len(wrong)} of {len(silent)}"
    out.metrics["reminders: reminded though replied or declined"] = len([s for s in stray if "recorded" not in s])
    if wrong:
        out.find("high", f"reminders came on the wrong days: expected {expected}, got {wrong}")
    if stray:
        out.find("high", f"reminders went to suppliers that replied or declined, or were miscounted: {stray[:2]}")
    mails = [m for m in box.sent if m["subject"].startswith("Reminder")]
    if any(not m["reply_to"].startswith(f"rfq+{rfq}@") for m in mails):
        out.find("high", "a reminder does not carry the RFQ's reply address, so an answer would not be linked")


# ---- the eval ---------------------------------------------------------------------------------


def run(ctx) -> Outcome:
    out = Outcome(True)
    world = lib.build_world("e16", with_stock=False)
    documents(ctx, world, out)
    budget(ctx, world, out)
    ceilings(ctx, out)
    ceiling_flow(ctx, out)
    reminders(ctx, lib.build_world("e16r", with_stock=False), out)
    return out
