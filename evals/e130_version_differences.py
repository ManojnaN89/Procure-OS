"""E130: the differences between two versions of an order (task 0.2, spec 7.3), written without the implementation.

Independence: written from the block 7 spec (sections 5.4 R9-R10, 7 P3 and P4, 8, 9.2) and the dataset only. The oracle
works out each version (`block7.Oracle7.version`: quantity, price, need-by, delivery place, the buyer's GSTIN of the
place, GST split on the place of supply) and its own differences (`block7.diff`: every field whose value differs,
money as Decimal, dates as dates); the platform's `/versions/compare` must list exactly those, with the same before and
after, and nothing else.

Given a fresh Apex reset on Mon 23 Nov 2026, P2 to version 3: Q(120 kg, 905.00) approved by U08, sent, accepted; cut to
100 (version 2, the approval carries); need-by moved to 19 Dec 2026 (version 3, material).
- compare 1 -> 2: line 1 qty 120 -> 100, taxable 1,08,600.00 -> 90,500.00, CGST and SGST 9,774.00 -> 8,145.00; header
  taxable, CGST, SGST and total 1,28,148.00 -> 1,06,790.00; nothing else.
- compare 2 -> 3: line 1 need-by 14 Dec -> 19 Dec 2026 only (not measured from the approved version 1).
- compare 3 -> 3: 422; from a word: 422; version 9 (no record): 404.
Given a fresh Brightwave reset, P4: 2 LP-01 at OFF-BLR on CNT-0001 at 62,000.00, approved by the CFO, sent, accepted;
the buyer moves the delivery place to OFF-HYD:
- compare 1 -> 2: header ship-to OFF-BLR -> OFF-HYD, buyer GSTIN 29BRTCB0000T1Z7 -> 36BRTCB0000T1ZC, CGST and SGST
  11,160.00 -> 0.00, IGST 0.00 -> 22,320.00 (taxable and total unchanged, so absent); line 1 CGST, SGST, IGST likewise.
Ends with tenant checks both ways.
Mutations: the comparison measured from the previous approved version instead of the one asked; unchanged fields
listed.
"""

from typing import Any

import block1 as b
import block4 as k
import block7 as t
import env
from block1 import js
from lib import Outcome, capture_mail

ID = "E130"
TITLE = "Comparing two versions lists exactly the fields that changed, before and after"
REQUIREMENT = (
    "Task 0.2 (block 7 spec R9-R10, P3, P4 compare): /versions/compare gives every changed header and line field with "
    "its before and after = the oracle's own diff of the two versions, never an unchanged field and never measured "
    "from another version; 422 for a = b or not a number; 404 for a version with no record; tenant checks"
)
PREFIX = "e130"


def run(ctx: Any) -> Outcome:
    env.forget_tenants(PREFIX)
    try:
        return _run(ctx)
    finally:
        t.pin(t.TODAY)
        env.forget_tenants(PREFIX)


def _run(ctx: Any) -> Outcome:
    from fastapi.testclient import TestClient

    from p2p.main import app
    from p2p.platform import mail

    out = Outcome(True)
    check = b.Checker(out)
    saved = mail.send_email
    box = capture_mail()
    try:
        client = TestClient(app)
        bw = t.Co7("brightwave", PREFIX, client, box)
        apex = t.Co7("apex", PREFIX, client, box)
        t.start(apex)
        start = apex.mark()
        m = k.Mutations()
        po = _p3(out, check, apex, m)
        bw_po = _p4(out, check, bw, m)
        m.report(check, out, ["diff_against_approved", "unchanged_listed"])
        t.tenant_check7(check, apex, bw, [po], [], start)
        t.tenant_check7(check, bw, apex, [bw_po], [], start)
    finally:
        mail.send_email = saved  # type: ignore[assignment]
    out.metrics.update({"checks": check.done, "failed": len(check.failed)})
    out.notes += [
        ("Field names are matched after folding the likely spellings (quantity/qty, ship_to/ship_to_location, "
         "payment_terms/payment_terms_code, price/unit_price); values by meaning (650 = 650.00, a date by its day)."),
        ("A line's fields are read as a list of {field, before, after} or a mapping field -> {before, after}; section "
         "8 names the first."),
        ("'A version with no record' cannot be made on a fresh reset (every order is made after the migration); "
         "version 9 of a three-version order has no record either, which is the same refusal."),
    ]
    return out


def _compare(check: b.Checker, co: t.Co7, no: str, a: int, b_: int, want: dict, label: str) -> dict:
    r = co.compare(no, a, b_)
    body = js(r) if r.status_code == 200 else {}
    check(r.status_code == 200, f"{label}: compare {a} -> {b_} answered {r.status_code} {r.text[:200]}")
    got = t.diff_set(body)
    missing, extra, wrong = t.diff_matches(got, want)
    check(r.status_code != 200 or (not missing and not wrong), f"{label}: compare {a} -> {b_} lacks {missing} or has "
          f"wrong values {wrong}; platform {sorted(got)}")  # fmt: skip
    check(r.status_code != 200 or not extra, f"{label}: compare {a} -> {b_} lists fields that did not change: {extra}")
    if r.status_code == 200:
        check(str(body.get("from")) == str(a) and str(body.get("to")) == str(b_), f"{label}: the body's from/to "
              f"{body.get('from')}/{body.get('to')}", "low")  # fmt: skip
        lines = [x for x in k.as_json(body.get("lines")) or [] if isinstance(x, dict)]
        check(all(str(x.get("change") or "changed") == "changed" for x in lines), f"{label}: line changes "
              f"{[x.get('change') for x in lines]}; expected 'changed'", "medium")  # fmt: skip
    return got


def _p3(out: Outcome, check: b.Checker, co: t.Co7, m: k.Mutations) -> str:
    oracle = t.Oracle7(co)
    po = co.q_order(check, "P3")
    if not po:
        return ""
    supplier = str(co.po_row(po).get("supplier_code"))
    terms = str(co.po_row(po).get("payment_terms_code") or "")
    v1 = oracle.version(1, t.WIRE, 120, "905.00", t.NEED_BY, supplier, t.LOC, terms=terms)
    v2 = oracle.version(2, t.WIRE, 100, "905.00", t.NEED_BY, supplier, t.LOC, terms=terms)
    v3 = oracle.version(3, t.WIRE, 100, "905.00", t.DEC19, supplier, t.LOC, terms=terms)
    rule, who = oracle.approver(v1.amounts["total"], frozenset({co.buyer()}))
    t.self_check(check, "P1v1", t.figures(v1, rule, who))
    t.self_check(check, "P1v2", t.figures(v2))
    k.approve_ok(check, co, who, po, 1, "P3 approve version 1")
    l1, _m1, _ = co.send_v(check, po, "P3 send")
    co.accept(l1)
    r = co.revise7(po, lines=[{"line_no": 1, "qty": "100"}], version=1)
    check(r.status_code == 200 and int(co.po_row(po).get("version") or 0) == 2, f"P3: the cut to 100 answered "
          f"{r.status_code} {r.text[:200]}")  # fmt: skip
    r = co.revise7(po, lines=[{"line_no": 1, "need_by": t.DEC19.isoformat()}], version=2)
    check(r.status_code == 200 and int(co.po_row(po).get("version") or 0) == 3, f"P3: need-by 19 Dec answered "
          f"{r.status_code} {r.text[:200]}")  # fmt: skip
    t.amounts_ok(check, co, po, v3, "P3 v3", "medium")

    want12 = t.diff(v1, v2)
    got12 = _compare(check, co, po, 1, 2, want12, "P3")
    everything = {**{("header", 0, x): (v, v) for x, v in v1.header().items()},
                  **{("line", 1, x): (v, v) for x, v in v1.line().items() if v is not None}, **want12}  # fmt: skip
    m.case("unchanged_listed", "P3 compare 1 -> 2", set(got12), set(want12), set(everything))
    want23 = t.diff(v2, v3)
    got23 = _compare(check, co, po, 2, 3, want23, "P3")
    wrong23 = t.diff(v1, v3)  # measured from the approved version 1 (the approval of 1 carried to 2)
    m.case("diff_against_approved", "P3 compare 2 -> 3", set(got23), set(want23), set(wrong23))
    check(int(co.po_row(po).get("approved_version") or 0) == 1, f"P3: approved_version "
          f"{co.po_row(po).get('approved_version')}; expected 1 (the cut carried, 19 Dec waits)", "medium")  # fmt: skip

    for a, b_, code, words in ((3, 3, 422, None), ("one", 2, 422, None), (1, 9, 404, "version 9 of")):
        r = co.compare(po, a, b_)
        t.refusal(check, r, code, words, f"P3 compare {a} -> {b_}")
    out.metrics["P3"] = {"order": po, "1 -> 2": sorted(str(x) for x in got12), "2 -> 3": sorted(str(x) for x in got23)}
    return po


def _p4(out: Outcome, check: b.Checker, co: t.Co7, m: k.Mutations) -> str:
    """P4 to version 2 (the delivery place moved to Hyderabad), then compare 1 -> 2."""
    t.start(co)
    oracle = t.Oracle7(co)
    po = co.k_order(check, t.LAPTOP, 2, t.NEED_BY, "P4", loc=t.BLR, by=co.login("employee"))
    if not po:
        return ""
    terms = str(co.po_row(po).get("payment_terms_code") or "")
    v1 = oracle.version(1, t.LAPTOP, 2, "62000.00", t.NEED_BY, "SUP-001", t.BLR, terms=terms)
    v2 = oracle.version(2, t.LAPTOP, 2, "62000.00", t.NEED_BY, "SUP-001", t.HYD, terms=terms)
    rule, who = oracle.approver(v1.amounts["total"], frozenset({co.buyer()}))
    t.self_check(check, "P4v1", t.figures(v1, rule, who))
    t.self_check(check, "P4v2", t.figures(v2))
    t.amounts_ok(check, co, po, v1, "P4 v1", "medium")
    k.approve_ok(check, co, who, po, 1, "P4 approve version 1")
    l1, _m1, _ = co.send_v(check, po, "P4 send")
    co.accept(l1)
    r = co.revise7(po, header={"ship_to_location": t.HYD}, version=1)
    check(r.status_code == 200 and int(co.po_row(po).get("version") or 0) == 2, f"P4: moving the delivery to {t.HYD} "
          f"answered {r.status_code} {r.text[:200]}")  # fmt: skip
    want = t.diff(v1, v2)
    got = _compare(check, co, po, 1, 2, want, "P4")
    everything = {**{("header", 0, x): (v, v) for x, v in v1.header().items()}, **want}
    m.case("unchanged_listed", "P4 compare 1 -> 2", set(got), set(want), set(everything))
    out.metrics["P4"] = {"order": po, "1 -> 2": sorted(str(x) for x in got)}
    return po
