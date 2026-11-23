"""E113: a period RFQ without a request (task 12.1, D40 route B), written without the implementation.

Independence: written from the block 5 spec (sections 5.1, 5.2, 8.3 P1 and P6, 9) and the dataset only. The suggested
quantity is the oracle's (`block5.Oracle5`: the 12 full months of orders before the creation month from the dataset's
history, pro rata to the period, against the open requirements of the period, rounded up); invitees and `min_quotes`
from the supplier files and the plan's D37 bands; the period sentence from the spec's fixed words.

P1 on a fresh Apex reset, 23 Nov 2026: the suggestion for RM-08 (Jan-Mar 2027) = the oracle (ordered 3,009.5 kg in Nov
2025-Oct 2026, 753 for 3 months); refusals with nothing written (start today, 13 months, end before start, mixed
categories, no lines, quantity 0, unknown item or place); P1 made: kind period, no request, one demand line 900 KGS with
the suggestion, every line points to a demand line and no request line (a row with both, or neither, refused by the
database), invited SUP-003, SUP-005, SUP-001, min_quotes 3, a pending rfq.send proposal and no mail; an overlapping
period RFQ for RM-08 at the plant 422 naming P1 (a later, separate quarter is accepted); the buyer confirms: three mails
with the period sentence, the plant's name and no request number; no request or order made. P6 on 1 Feb 2027: the
expiry review of CNT-0001 prefills RM-01 to RM-05 at PLT-CKN for 1 Apr 2027 to 31 Mar 2028 with the oracle's
quantities; closing it 'rfq_started' without an RFQ is refused; creating with `review_task_id` closes it with the RFQ
number; a second RM-01 RFQ overlapping is 422. Truth file rows; tenant check.
Mutations: a line with both links accepted; the overlap check missing.
"""

import json
from datetime import date
from decimal import Decimal
from typing import Any

import block1 as b
import block4 as k
import block5 as f
import env
import lib
from block1 import D, js
from lib import Outcome
from sqlalchemy import text

ID = "E113"
TITLE = "A period RFQ asks for a period's prices without a request"
REQUIREMENT = (
    "Task 12.1, D40 route B (block 5 spec 5.1, 5.2, P1, P6): kind period, no request; each line one period demand line, "
    "never both or neither; the suggested quantity of 5.2; invitees and min_quotes as operational; refusals of 5.1 "
    "rule 2 with nothing written; the fixed period sentence in the mail; no request, order or budget; a strategic "
    "review prefills and is closed rfq_started with the RFQ; truth file; tenant check"
)
PREFIX = "e113"
CASTINGS = ["RM-01", "RM-02", "RM-03", "RM-04", "RM-05"]
P6_DAY = date(2027, 2, 1)
P6_FROM, P6_TO = date(2027, 4, 1), date(2028, 3, 31)


def run(ctx: Any) -> Outcome:
    env.forget_tenants(PREFIX)
    try:
        return _run(ctx)
    finally:
        f.pin(lib.TODAY)
        env.forget_tenants(PREFIX)


def _run(ctx: Any) -> Outcome:
    from fastapi.testclient import TestClient
    from p2p.main import app
    from p2p.platform import mail

    out = Outcome(True)
    check = b.Checker(out)
    saved = mail.send_email
    box = lib.capture_mail()
    try:
        client = TestClient(app)
        bw = f.Co5("brightwave", PREFIX, client, box)
        apex = f.Co5("apex", PREFIX, client, box)
        f.start(apex)
        start = apex.mark()
        oracle = f.Oracle5(apex)
        m = k.Mutations()
        truth: list[dict[str, Any]] = []
        suggested = _suggest(out, check, apex, oracle)
        _refusals(check, apex)
        rfq = _p1(out, check, apex, oracle, suggested)
        truth.append({"scenario": "P1", "line": "1", "item": f.WIRE, "location": f.LOC, "qty": Decimal(900),
                      "suggested_qty": suggested, "period_from": f.P_FROM.isoformat(), "period_to": f.P_TO.isoformat()})  # fmt: skip
        if rfq:
            _line_rule(check, apex, m, rfq)
            _overlap(check, apex, m, rfq)
            _sent(check, apex, rfq)
        p6 = _p6(out, check, apex, oracle, m, truth)
        out.metrics["truth rows compared"] = f.compare_truth(check, apex, truth)
        m.report(check, out, ["both_links", "no_overlap_check"])
        f.tenant_check(check, apex, bw, [rfq, p6], [], start)
    finally:
        mail.send_email = saved  # type: ignore[assignment]
    out.metrics.update({"checks": check.done, "failed": len(check.failed)})
    out.notes += [
        (
            "Open need is read from the platform's open material_requirement rows (loaded by the reset from the ERP files, "
            "before block 5 runs); the order history from the dataset's own history files. Units: every item here is "
            "bought and stored in the same unit."
        ),
        (
            "min_quotes uses the plan's D37 bands (2 from Rs 25,000, 3 above Rs 2,00,000) and the category's own figure; the "
            "versioned buying_route_rule row is not a dataset file."
        ),
        "The words of a refusal are checked as low, the 422 and nothing written as high.",
    ]
    return out


def _suggest(out: Outcome, check: b.Checker, co: f.Co5, oracle: f.Oracle5) -> Decimal:
    ordered = oracle.ordered_12m(f.WIRE, f.LOC, f.TODAY)
    need = f.open_need(co, f.WIRE, f.LOC, f.P_FROM, f.P_TO)
    months = f.months_in(f.P_FROM, f.P_TO)
    want = oracle.suggested(need, ordered, months)
    check(ordered == Decimal("3009.5") and want == Decimal(753), f"oracle self-check: RM-08 ordered {ordered} kg in the "
          f"12 months, suggestion {want}; the dataset's history gives 3,009.5 and 753", "low")  # fmt: skip
    q = (f"item_code={f.WIRE}&location_code={f.LOC}&period_from={f.P_FROM.isoformat()}"
         f"&period_to={f.P_TO.isoformat()}")  # fmt: skip
    r = co.call(co.login("buyer"), "GET", f"/api/rfqs/period/suggest?{q}")
    body = js(r)
    got = {
        x: b.dec(body.get(x))
        for x in ("suggested_qty", "ordered_12m", "open_need", "months")
    }
    check(r.status_code == 200 and got == {"suggested_qty": want, "ordered_12m": ordered, "open_need": need,
                                           "months": Decimal(months)},
          f"suggest RM-08 Jan-Mar 2027: {r.status_code} {got}; oracle suggested {want}, ordered {ordered}, open need "
          f"{need}, months {months}")  # fmt: skip
    words = str(body.get("suggestion") or "")
    check(b.fq(ordered) in words and "KGS" in words, f"the suggestion's words do not give the 12 months' order "
          f"({b.fq(ordered)} KGS): {words!r}", "low")  # fmt: skip
    bad = (
        (
            q.replace(f"period_from={f.P_FROM.isoformat()}", "period_from=2026-11-23"),
            "starting today",
        ),
        (
            q.replace(f"period_to={f.P_TO.isoformat()}", "period_to=2026-12-31"),
            "ending before it starts",
        ),
    )
    for qq, label in bad:
        r = co.call(co.login("buyer"), "GET", f"/api/rfqs/period/suggest?{qq}")
        check(
            r.status_code == 422,
            f"suggest for a period {label} answered {r.status_code}; expected 422",
            "medium",
        )
    out.metrics["RM-08 suggestion (ordered 12m, open need, suggested)"] = (
        f"{ordered}, {need}, {want}"
    )
    return want


def _refusals(check: b.Checker, co: f.Co5) -> None:
    buyer = co.login("buyer")
    cases = [
        ("starts today", f.LOC, f.TODAY, f.P_TO, [(f.WIRE, 900)], "after today"),
        ("13 months", f.LOC, f.P_FROM, date(2028, 1, 31), [(f.WIRE, 900)], "12 months"),
        ("ends before it starts", f.LOC, f.P_TO, f.P_FROM, [(f.WIRE, 900)], ""),
        (
            "mixed categories",
            f.LOC,
            f.P_FROM,
            f.P_TO,
            [(f.WIRE, 900), ("PK-01", 500)],
            "one period rfq per category",
        ),
        ("no lines", f.LOC, f.P_FROM, f.P_TO, [], ""),
        ("quantity 0", f.LOC, f.P_FROM, f.P_TO, [(f.WIRE, 0)], ""),
        ("unknown item", f.LOC, f.P_FROM, f.P_TO, [("XX-99", 10)], ""),
        ("unknown place", "PLT-NONE", f.P_FROM, f.P_TO, [(f.WIRE, 900)], ""),
    ]
    for label, loc, p_from, p_to, lines, words in cases:
        before = (co.count("rfq"), co.count("period_demand_line"), co.count("proposal"))
        r = co.period_rfq(buyer, loc, p_from, p_to, lines)
        after = (co.count("rfq"), co.count("period_demand_line"), co.count("proposal"))
        check(r.status_code == 422 and after == before, f"period RFQ {label}: {r.status_code} {r.text[:200]}; rows "
              f"{before} -> {after}; the spec says 422 and nothing written")  # fmt: skip
        if words:
            check(
                words in r.text.lower(),
                f"period RFQ {label}: the refusal does not say '{words}': {r.text[:200]}",
                "low",
            )
    r = co.period_rfq(co.login("production"), f.LOC, f.P_FROM, f.P_TO, [(f.WIRE, 900)])
    check(
        r.status_code == 403,
        f"period RFQ by production@ (no rfq.create) answered {r.status_code}; expected 403",
    )


def _p1(
    out: Outcome, check: b.Checker, co: f.Co5, oracle: f.Oracle5, suggested: Decimal
) -> str:
    before = (co.count("request"), co.count("po"), co.budget_state())
    since = co.mark()
    r = co.period_rfq(co.login("buyer"), f.LOC, f.P_FROM, f.P_TO, [(f.WIRE, 900)])
    rfq = f.created(check, co, r, "P1")
    if not rfq:
        return ""
    body = js(r)
    row, lines, demand = co.rfq(rfq), co.rfq_lines(rfq), co.demand_lines(rfq)
    value = Decimal(900) * oracle.estimate(f.WIRE, f.LOC, f.TODAY)
    mq = oracle.min_quotes("RAW", value)
    invitees = oracle.invitees("RAW", [f.WIRE], f.LOC, f.TODAY, mq)
    check(set(invitees) == f.SPEC_INVITED["P1"] and mq == f.SPEC_MIN_QUOTES["P1"], f"oracle self-check P1: invitees "
          f"{invitees}, min_quotes {mq}; the spec says {f.SPEC_INVITED['P1']}, 3", "medium")  # fmt: skip
    check(body.get("status") == "draft" and bool(body.get("proposal_id")), f"P1 answer: {body}; the spec says status "
          "draft with a proposal_id", "medium")  # fmt: skip
    check(row.get("kind") == "period" and row.get("request_no") is None and row.get("period_from") == f.P_FROM
          and row.get("period_to") == f.P_TO and row.get("delivery_location") == f.LOC and row.get("status") == "draft",
          f"P1 {rfq}: kind {row.get('kind')}, request {row.get('request_no')}, period {row.get('period_from')} to "
          f"{row.get('period_to')}, location {row.get('delivery_location')}, status {row.get('status')}")  # fmt: skip
    check(len(lines) == 1 and lines[0].get("period_line_id") is not None and lines[0].get("request_no") is None
          and lines[0].get("request_line_no") is None and lines[0].get("need_by") == f.P_FROM,
          f"P1 {rfq} lines: {[(x.get('request_no'), x.get('period_line_id'), x.get('need_by')) for x in lines]}; "
          "expected one line pointing to a demand line only, need-by 1 Jan 2027")  # fmt: skip
    d = demand[0] if demand else {}
    check(len(demand) == 1 and D(d.get("qty")) == 900 and d.get("uom") == "KGS" and d.get("item_code") == f.WIRE
          and d.get("location_code") == f.LOC and d.get("status") == "on_rfq",
          f"P1 demand line: {d}; expected RM-08 900 KGS at PLT-CKN, on_rfq")  # fmt: skip
    check(D(d.get("suggested_qty")) == suggested and bool(d.get("suggestion")),
          f"P1 demand line suggested {d.get('suggested_qty')} ({d.get('suggestion')!r}); oracle {suggested}")  # fmt: skip
    api_lines = body.get("lines") or []
    check(bool(api_lines) and D(api_lines[0].get("suggested_qty")) == suggested and D(api_lines[0].get("qty")) == 900,
          f"P1 answer lines {api_lines}; oracle qty 900, suggested {suggested}", "medium")  # fmt: skip
    got = sorted(str(s["supplier_code"]) for s in co.invited(rfq))
    check(
        got == sorted(invitees),
        f"P1 invited {got}; oracle {sorted(invitees)} (prices on file first, then by code)",
    )
    check(
        sorted(str(x) for x in body.get("invited") or []) == sorted(invitees),
        f"P1 answer invited {body.get('invited')}",
        "medium",
    )
    check(int(row.get("min_quotes") or 0) == mq and int(body.get("min_quotes") or 0) == mq,
          f"P1 min_quotes {row.get('min_quotes')} (answer {body.get('min_quotes')}); oracle {mq} for Rs {value}")  # fmt: skip
    prop = co.proposal(row.get("proposal_id"))
    check(prop.get("action") == "rfq.send" and prop.get("status") == "pending", f"P1 proposal {prop.get('action')} "
          f"{prop.get('status')}; expected a pending rfq.send (D42)")  # fmt: skip
    check(not [x for x in co.mails(since) if rfq in str(x.get("subject", ""))], f"P1 {rfq}: mail left before the buyer "
          "confirmed")  # fmt: skip
    audits = [a for a in co.since("rfq.create", 0) if a["entity_id"] == rfq]
    check(bool(audits) and "period" in json.dumps(audits[-1]["after"], default=str), f"P1 {rfq}: rfq.create audit "
          f"{[a['after'] for a in audits]}; expected one with kind period", "medium")  # fmt: skip
    after = (co.count("request"), co.count("po"), co.budget_state())
    check(after == before, "P1: a request, an order or a budget figure changed when the period RFQ was made (spec 5.1 "
          "rule 8)")  # fmt: skip
    out.metrics["P1"] = (
        f"{rfq}: invited {got}, min_quotes {row.get('min_quotes')}, suggested {d.get('suggested_qty')}"
    )
    return rfq


def _line_rule(check: b.Checker, co: f.Co5, m: k.Mutations, rfq: str) -> None:
    """Rev s3: an RFQ line points to a request line or a period demand line, never both, never neither."""
    pdl = co.demand_lines(rfq)
    req = co.qd(
        "select request_no, line_no from request_line order by request_no, line_no limit 1"
    )
    if not pdl:
        check(False, "line rule: no demand line to try a forbidden row with", "medium")
        return
    # a fresh demo company has no request yet; rfq_line.request_no has no foreign key, so any request reference makes
    # the "both" row, and only the one-source check can refuse it
    both = (req[0]["request_no"], req[0]["line_no"]) if req else ("REQ-EVAL-LINE-RULE", 1)
    rows = {
        "both": (both[0], both[1], pdl[0]["id"]),
        "neither": (None, None, None),
    }
    for label, (rno, rline, pid) in rows.items():
        refused = False
        try:
            with lib.owner_session(co.slug) as db:
                db.execute(text(
                    "insert into rfq_line (rfq_no, line_no, request_no, request_line_no, period_line_id, item_code, "
                    "description, qty, uom, need_by) values (:r, 99, :rn, :rl, :p, :i, 'eval', 1, 'KGS', :nb)"
                ), {"r": rfq, "rn": rno, "rl": rline, "p": pid, "i": f.WIRE, "nb": f.P_FROM})  # fmt: skip
                db.rollback()
        except Exception:  # noqa: BLE001 - the database refusing the row is the point
            refused = True
        if label == "both":
            m.case(
                "both_links",
                "an rfq_line with a request line and a demand line",
                refused,
                True,
                False,
            )
        check(
            refused,
            f"line rule: an rfq_line with {label} links was accepted by the database (Rev s3)",
        )


def _overlap(check: b.Checker, co: f.Co5, m: k.Mutations, rfq: str) -> None:
    before = co.count("rfq")
    r = co.period_rfq(
        co.login("buyer"), f.LOC, date(2027, 3, 1), date(2027, 5, 31), [(f.WIRE, 300)]
    )
    m.case(
        "no_overlap_check",
        "RM-08 at the plant for Mar-May 2027 beside P1",
        r.status_code,
        422,
        201,
    )
    check(r.status_code == 422 and co.count("rfq") == before, f"an overlapping period RFQ for RM-08 at {f.LOC} answered "
          f"{r.status_code} {r.text[:200]}; the spec says 422 naming {rfq}")  # fmt: skip
    check(
        rfq in r.text and "RM-08" in r.text,
        f"the overlap refusal does not name {rfq} and RM-08: {r.text[:200]}",
        "low",
    )
    r = co.period_rfq(
        co.login("buyer"), f.LOC, date(2027, 4, 1), date(2027, 6, 30), [(f.WIRE, 300)]
    )
    later = js(r).get("rfq_no")
    check(r.status_code in (200, 201) and bool(later), f"a separate later quarter for RM-08 (Apr-Jun 2027) answered "
          f"{r.status_code} {r.text[:200]}; expected accepted (no day shared)", "medium")  # fmt: skip
    if later:  # out of the way: cancelled, which also checks the cancel path (spec 5.1 rule 10)
        since = co.mark()
        c = co.call(
            co.login("buyer"),
            "POST",
            f"/api/rfqs/{later}/cancel",
            {"reason": "eval: not needed"},
        )
        row = co.rfq(str(later))
        check(c.status_code == 200 and row.get("status") == "cancelled"
              and co.proposal(row.get("proposal_id")).get("status") != "pending"
              and all(x.get("status") == "cancelled" for x in co.demand_lines(str(later)))
              and not [x for x in co.mails(since) if str(later) in str(x.get("subject", ""))],
              f"cancelling {later}: {c.status_code} {c.text[:150]}, status {row.get('status')}; expected cancelled, its "
              "proposal withdrawn, demand lines cancelled, no mail", "medium")  # fmt: skip
        r = co.call(co.login("buyer"), "POST", f"/api/rfqs/{rfq}/cancel", {})
        check(
            r.status_code == 422,
            f"cancel without a reason answered {r.status_code}; expected 422",
            "low",
        )


def _sent(check: b.Checker, co: f.Co5, rfq: str) -> None:
    since = co.mark()
    check(
        co.confirm(co.login("buyer"), rfq),
        f"P1: the buyer could not confirm sending {rfq}",
    )
    row = co.rfq(rfq)
    check(row.get("status") == "sent" and row.get("confirmed_via") in ("proposal", "bulk")
          and f.same_person(co, row.get("confirmed_by"), co.login("buyer")),
          f"P1 after the buyer confirmed: status {row.get('status')}, by {row.get('confirmed_by')}, via "
          f"{row.get('confirmed_via')}")  # fmt: skip
    sentence = f.PERIOD_SENTENCE.format(f=f.long_date(f.P_FROM), t=f.long_date(f.P_TO))
    place = str(co.one("select name from location where code = :c", c=f.LOC) or "")
    for s in co.invited(rfq):
        got = f.mails_to(co, str(s.get("email") or ""), since, rfq)
        if not check(
            len(got) == 1,
            f"P1: {len(got)} mail(s) to {s.get('supplier_code')}, expected 1",
        ):
            continue
        body = str(got[0].get("body") or "")
        check(f.norm(sentence) in f.norm(body), f"P1 mail to {s.get('supplier_code')} has not the period sentence "
              f"'{sentence}': {body[:400]}")  # fmt: skip
        check(place in body and "REQ-" not in body and "None" not in body,
              f"P1 mail to {s.get('supplier_code')}: place '{place}' {place in body}, a request number or None in it: "
              f"{body[:300]}", "medium")  # fmt: skip


def _review(co: f.Co5, contract: str) -> dict[str, Any]:
    r = co.call(co.login("buyer"), "GET", "/api/reviews")
    for x in js(r) if isinstance(js(r), list) else []:
        flat = json.dumps(x, default=str)
        if contract in flat and "expiry" in flat:
            return dict(x)
    return {}


def _p6(
    out: Outcome,
    check: b.Checker,
    co: f.Co5,
    oracle: f.Oracle5,
    m: k.Mutations,
    truth: list[dict[str, Any]],
) -> str:
    from p2p.platform import routing
    from p2p.sourcing import price_review
    from sqlalchemy.orm import Session

    buyer = co.login("buyer")
    with f.pinned(P6_DAY):
        with Session(routing.engine_for(co.slug)) as db:
            price_review.run(db, co.slug, P6_DAY)
            db.commit()
        review = _review(co, "CNT-0001")
        task = review.get("task_id")
        if not check(
            task is not None,
            "P6: no open expiry review of CNT-0001 on 1 Feb 2027 (60 days before 31 Mar 2027)",
        ):
            return ""
        r = co.call(buyer, "GET", f"/api/reviews/{task}/period-rfq")
        pre = js(r)
        want = {}
        months = f.months_in(P6_FROM, P6_TO)
        for item in CASTINGS:
            want[item] = oracle.suggested(f.open_need(co, item, f.LOC, P6_FROM, P6_TO), oracle.ordered_12m(item, f.LOC, P6_DAY),
                                          months)  # fmt: skip
        got = {
            str(x.get("item_code")): b.dec(x.get("suggested_qty"))
            for x in pre.get("lines") or []
        }
        check(
            r.status_code == 200 and got == want,
            f"P6 prefill lines {got}; oracle {want} (12 months of orders)",
        )
        check([str(x) for x in pre.get("locations") or []] == [f.LOC] and str(pre.get("period_from")) == P6_FROM.isoformat()
              and str(pre.get("period_to")) == P6_TO.isoformat(),
              f"P6 prefill places {pre.get('locations')}, period {pre.get('period_from')} to {pre.get('period_to')}; "
              "expected [PLT-CKN] (region MH), 1 Apr 2027 to 31 Mar 2028")  # fmt: skip
        r = co.call(
            buyer,
            "POST",
            f"/api/reviews/{task}/close",
            {"outcome": "rfq_started", "reason": "eval: no RFQ"},
        )
        check(r.status_code == 422, f"P6: closing the review rfq_started without an RFQ answered {r.status_code}; "
              "expected 422 'start the period RFQ from the review'")  # fmt: skip
        check(
            "start the period rfq from the review" in r.text.lower(),
            f"P6: the refusal's words: {r.text[:200]}",
            "low",
        )
        lines = [(i, q if q > 0 else Decimal(100)) for i, q in want.items()]
        r = co.period_rfq(
            buyer,
            f.LOC,
            P6_FROM,
            P6_TO,
            lines,
            review_task_id=task,
            note="eval: CNT-0001 renewal",
        )
        rfq = f.created(check, co, r, "P6")
        status, payload = None, {}
        found = co.qd("select status, payload from task where id = :i", i=task)
        if found:
            status, payload = found[0]["status"], k.as_json(found[0]["payload"]) or {}
        check(bool(rfq) and status == "done" and payload.get("outcome") == "rfq_started" and payload.get("rfq_no") == rfq,
              f"P6: review {task} is {status} with outcome {payload.get('outcome')} and rfq {payload.get('rfq_no')}; "
              f"expected done, rfq_started, {rfq}")  # fmt: skip
        check(
            co.rfq(rfq).get("review_task_id") == task if rfq else False,
            f"P6: {rfq} does not keep its review {task}",
            "medium",
        )
        before = co.count("rfq")
        r = co.period_rfq(
            buyer, f.LOC, date(2027, 6, 1), date(2027, 8, 31), [("RM-01", 100)]
        )
        m.case(
            "no_overlap_check",
            "RM-01 at the plant beside the renewal RFQ",
            r.status_code,
            422,
            201,
        )
        check(r.status_code == 422 and co.count("rfq") == before, f"P6: a second RM-01 RFQ overlapping answered "
              f"{r.status_code} {r.text[:200]}; expected 422")  # fmt: skip
    for n, (item, q) in enumerate(want.items(), 1):
        truth.append({"scenario": "P6", "line": str(n), "item": item, "location": f.LOC, "suggested_qty": q,
                      "period_from": P6_FROM.isoformat(), "period_to": P6_TO.isoformat()})  # fmt: skip
    out.metrics["P6"] = (
        f"review {task} -> {rfq}; suggested { {i: str(q) for i, q in want.items()} }"
    )
    return rfq
