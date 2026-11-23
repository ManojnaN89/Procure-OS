"""E134: an order keeps the contract price version it was bought at (D48 "POs keep the version they used", spec 7.8),
written without the implementation.

Independence: written from the block 7 spec (sections 4, 5.5 R15, 5.9 R26-R28, 7 P8, 8, 9.2, OP-6 a) and the dataset
only. The oracle (`block7.Oracle7.contract_price`) reads `price_record.csv` itself: CNT-0001 RM-01 version 1 at 650.00
(1 Apr 2026 to 31 Mar 2027, region MH) and the draft version 2 at 672.00 from 1 Jan 2027, in force only once activated,
when version 1 then ends on 31 Dec 2026. GST 18% split on PLT-CKN (state 27, SUP-001 in 27); the band and its person
from `approval_rule.csv`; the material reasons from the last approved version (block 4's Q19 rule).

P8 on a fresh Apex reset, Mon 23 Nov 2026: K(RM-01, 200, need-by 15 Jan 2027): version 1 at 650.00 with the
`price_record_id` of CNT-0001 RM-01 version 1, taxable 1,30,000.00, CGST and SGST 11,700.00, total 1,53,400.00, PO-02
(U08); the API shows the line's contract {CNT-0001, version 1, ...}; U08 approves, the buyer sends; the version 1 PDF
says "Contract CNT-0001 version 1".
- When the plant head sends and the director activates CNT-0001 version 2: the order line is untouched (650.00, the
  same `price_record_id`), version 1 of RM-01 now ends 31 Dec 2026, and re-pricing on 23 Nov is 409 "line 1 is already
  at the contract price in force (CNT-0001 version 1)" with nothing written.
- On Mon 4 Jan 2027 re-pricing makes version 2 at 672.00 on the version 2 record: taxable 1,34,400.00, CGST and SGST
  12,096.00, total 1,58,592.00; material ["line 1: price up", "the total went up"]; a step for U08 with version 2; the
  comparison 1 -> 2 shows the contract version 1 -> 2 and the price 650.00 -> 672.00; approved and sent, its PDF says
  "Contract CNT-0001 version 2".
- A new K(RM-01, 200) on 4 Jan is ordered at 672.00 on version 2, total 1,58,592.00 (R28, the other side of the start).
Ends with a tenant check.
Mutations: activation re-prices open orders; a re-price that keeps the old `price_record_id`.
"""

from datetime import date
from typing import Any

import block1 as b
import block4 as k
import block7 as t
import env
from block1 import js
from lib import Outcome, capture_mail

ID = "E134"
TITLE = "An order keeps the contract price version it used; re-pricing moves it to the version in force, approved again"
REQUIREMENT = (
    "D48, OP-6 (block 7 spec R15, R26-R28, P8): the line keeps the price record it was bought at when a later contract "
    "version is activated; re-pricing takes the version in force on the business date (refused when already there), a "
    "rise is material and approved again; the version shows on the API, the PDF and the comparison; new orders use the "
    "version in force on their date; figures = the oracle and the truth file; tenant check"
)
PREFIX = "e134"
NEW_NEED = date(2027, 1, 29)


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
        orders, tokens = _p8(out, check, apex)
        t.pin(t.TODAY)
        t.tenant_check7(check, apex, bw, orders, tokens, start)
    finally:
        mail.send_email = saved  # type: ignore[assignment]
    out.metrics.update({"checks": check.done, "failed": len(check.failed)})
    out.notes += [
        ("The line's contract on the API is read from lines[].contract {code, version, price_record_id, valid_from, "
         "valid_to} (section 8); the snapshot's contract version from po_version.snapshot lines[0] (contract_version or "
         "price_record_id)."),
        ("The comparison's contract field is accepted as contract_version, contract, price_record_version or "
         "price_record_id (any one showing 1 -> 2 or the two record ids)."),
        ("The new order on 4 Jan has need-by 29 Jan 2027 (the spec gives none); its request is approved through its "
         "chain like any K."),
    ]
    return out


def _line_contract(check: b.Checker, co: t.Co7, no: str, version: int, prid: Any, label: str) -> None:
    detail = co.detail(no, co.buyer())
    line = next((x for x in detail.get("lines") or [] if str(x.get("line_no", 1)) == "1"), {})
    c = line.get("contract") if isinstance(line.get("contract"), dict) else {}
    check(c.get("code") == t.CONTRACT and str(c.get("version")) == str(version) and str(c.get("price_record_id")) == str(prid)
          and bool(c.get("valid_from")), f"{label}: GET /api/purchase-orders/{no} line 1 contract {c or line.get('contract')}; "
          f"expected {t.CONTRACT} version {version}, record {prid}, with its dates (R26)", "medium")  # fmt: skip


def _pdf_says(check: b.Checker, co: t.Co7, no: str, v: int, words: list[str], label: str) -> None:
    r = co.version_pdf(no, v)
    text_ = t.pdf_text(r.content if r.status_code == 200 else None)
    for w in words:
        check(t.norm(w) in t.norm(text_), f"{label}: versions/{v}/pdf ({r.status_code}) lacks '{w}' (R8): "
              f"{text_[:300]!r}", "medium")  # fmt: skip


def _p8(out: Outcome, check: b.Checker, co: t.Co7) -> tuple[list[str], list[str | None]]:
    oracle = t.Oracle7(co)
    m = k.Mutations()
    want1 = oracle.contract_price(t.CASTING, t.TODAY)
    check(want1 == (t.D("650.00"), 1), f"P8: the oracle's price in force on 23 Nov {want1} (precondition)", "medium")
    po = co.k_order(check, t.CASTING, 200, t.P8_NEED, "P8")
    if not po:
        return [], []
    prid1, prid2 = co.price_id(t.CASTING, 1), co.price_id(t.CASTING, 2)
    check(prid1 is not None and prid2 is not None, f"P8: price records of {t.CONTRACT} RM-01 v1 {prid1} / v2 {prid2} "
          "(precondition: the dataset's draft version 2 is loaded)")  # fmt: skip
    terms = str(co.po_row(po).get("payment_terms_code") or "")
    v1 = oracle.version(1, t.CASTING, 200, "650.00", t.P8_NEED, "SUP-001", t.LOC, terms=terms, price_record_id=prid1,
                        contract_version=1)  # fmt: skip
    v2 = oracle.version(2, t.CASTING, 200, "672.00", t.P8_NEED, "SUP-001", t.LOC, terms=terms, price_record_id=prid2,
                        contract_version=2)  # fmt: skip
    rule, who = oracle.approver(v1.amounts["total"], frozenset({co.buyer()}))
    rule2, who2 = oracle.approver(v2.amounts["total"], frozenset({co.buyer()}))
    t.self_check(check, "P8v1", t.figures(v1, rule, who))
    t.self_check(check, "P8v2", t.figures(v2, rule2, who2))
    t.amounts_ok(check, co, po, v1, "P8 v1")
    ln = co.po_line1(po)
    check(ln.get("price_record_id") == prid1, f"P8 v1: price_record_id {ln.get('price_record_id')}; oracle {prid1} "
          f"({t.CONTRACT} RM-01 version 1)")  # fmt: skip
    pend = co.open_steps(po)
    check(len(pend) == 1 and co.code_of(pend[0].get("approver")) == who, f"P8 v1: steps "
          f"{[co.code_of(s.get('approver')) for s in pend]}; oracle {rule} {who}")  # fmt: skip
    _line_contract(check, co, po, 1, prid1, "P8 v1")
    snap1 = co.po_versions(po).get(1, {}).get("snapshot") or {}
    sl = (snap1.get("lines") or [{}])[0] if isinstance(snap1, dict) else {}
    check(str(sl.get("contract_version")) == "1" or sl.get("price_record_id") == prid1, f"P8 v1: the version 1 snapshot's "
          f"line {sl}; expected the contract version (R2, R26)", "medium")  # fmt: skip
    k.approve_ok(check, co, who, po, 1, "P8 approve version 1")
    l1, _m1, _ = co.send_v(check, po, "P8 send")
    _pdf_says(check, co, po, 1, [f"Contract {t.CONTRACT} version 1"], "P8 v1")

    # the contract's version 2 is activated: the order keeps version 1
    co.activate_v2(check, "P8 activation")
    ln = co.po_line1(po)
    kept = (t.D(ln.get("unit_price")), ln.get("price_record_id"))
    m.case("activation_reprices", "P8 after activating version 2", kept, (t.D("650.00"), prid1), (t.D("672.00"), prid2))
    check(kept == (t.D("650.00"), prid1) and int(co.po_row(po).get("version") or 0) == 1,
          f"P8: after activation the line is {kept}, version {co.po_row(po).get('version')}; expected 650.00 on "
          f"{prid1}, version 1 (R27)")  # fmt: skip
    end = co.one("select valid_to from price_record where id = :i", i=prid1) if prid1 else None
    check(str(end) == "2026-12-31", f"P8: RM-01 version 1 now ends {end}; expected 2026-12-31 (D48)", "medium")
    check(oracle.contract_price(t.CASTING, t.TODAY, activated=True) == (t.D("650.00"), 1), "P8: the oracle's price in "
          "force on 23 Nov after activation is not version 1 (precondition)", "medium")  # fmt: skip
    before = co.footprint(po)
    r = co.revise7(po, lines=[{"line_no": 1, "reprice": True}], version=1)
    t.refusal(check, r, 409, f"already at the contract price in force ({t.CONTRACT} version 1)", "P8 re-price on 23 Nov")
    t.unchanged(check, co, po, before, "P8 re-price on 23 Nov")

    # 4 Jan 2027: re-price to version 2
    t.pin(t.JAN4)
    want = oracle.contract_price(t.CASTING, t.JAN4, activated=True)
    check(want == (t.D("672.00"), 2), f"P8: the oracle's price in force on 4 Jan {want} (precondition)", "medium")
    reasons = oracle.reasons(v1, v2)
    r = co.revise7(po, lines=[{"line_no": 1, "reprice": True}], version=1, reason="the contract's January price")
    row, ln = co.po_row(po), co.po_line1(po)
    check(r.status_code == 200 and int(row.get("version") or 0) == 2, f"P8: re-pricing on 4 Jan answered "
          f"{r.status_code} {r.text[:200]}; version {row.get('version')}")  # fmt: skip
    m.case("reprice_keeps_prid", "P8 re-price on 4 Jan", ln.get("price_record_id"), prid2, prid1)
    check(ln.get("price_record_id") == prid2, f"P8 v2: price_record_id {ln.get('price_record_id')}; oracle {prid2} "
          "(version 2's record, R15)")  # fmt: skip
    t.amounts_ok(check, co, po, v2, "P8 v2")
    check(row.get("status") == "submitted", f"P8 v2: status {row.get('status')}; a price rise goes for approval again")
    k.check_reasons(check, "P8 v2", co.revisions(po).get(2, {}).get("material"), reasons, tuple(reasons))
    changes = k.as_json(co.revisions(po).get(2, {}).get("changes")) or []
    check(any(isinstance(e, dict) and isinstance(e.get("contract_version"), dict)
              and str(e["contract_version"].get("from")) == "1" and str(e["contract_version"].get("to")) == "2"
              for e in changes), f"P8 v2: po_revision changes {changes}; expected a contract_version 1 -> 2 entry (R17)",
          "medium")  # fmt: skip
    pend = co.open_steps(po)
    check(len(pend) == 1 and co.code_of(pend[0].get("approver")) == who2 and pend[0].get("version") == 2,
          f"P8 v2: open steps {[(co.code_of(s.get('approver')), s.get('version')) for s in pend]}; oracle {rule2} "
          f"{who2} with version 2")  # fmt: skip
    r = co.compare(po, 1, 2)
    got = t.diff_set(js(r) if r.status_code == 200 else {})
    price = got.get(("line", 1, "unit_price"))
    contract = got.get(("line", 1, "contract_version")) or got.get(("line", 1, "price_record_id"))
    check(r.status_code == 200 and price is not None and (t.value_of(price[0]), t.value_of(price[1]))
          == (t.value_of("650.00"), t.value_of("672.00")), f"P8: compare 1 -> 2 ({r.status_code}) price {price}; expected "
          "650.00 -> 672.00")  # fmt: skip
    check(contract is not None and {str(t.value_of(contract[0])), str(t.value_of(contract[1]))} in
          ({"1", "2"}, {str(t.value_of(prid1)), str(t.value_of(prid2))}),
          f"P8: compare 1 -> 2 contract version {contract}; expected 1 -> 2 (R26)")  # fmt: skip
    k.approve_ok(check, co, who2, po, 2, "P8 approve version 2")
    l2, _m2, _ = co.send_v(check, po, "P8 send version 2")
    _pdf_says(check, co, po, 2, [f"Contract {t.CONTRACT} version 2", f"This version: {t.long_date(t.JAN4)}"], "P8 v2")
    _line_contract(check, co, po, 2, prid2, "P8 v2")

    # a new order on 4 Jan takes version 2
    po2 = co.k_order(check, t.CASTING, 200, NEW_NEED, "P8 new order on 4 Jan")
    if po2:
        n1 = oracle.version(1, t.CASTING, 200, "672.00", NEW_NEED, "SUP-001", t.LOC)
        t.amounts_ok(check, co, po2, n1, "P8 new order on 4 Jan")
        check(co.po_line1(po2).get("price_record_id") == prid2, f"P8: the 4 Jan order's line is on record "
              f"{co.po_line1(po2).get('price_record_id')}; oracle {prid2} (R28)")  # fmt: skip
    t.pin(t.TODAY)
    t.compare_truth(check, co, {("P8", 1): t.truth_row(v1, rule, who, None), ("P8", 2): t.truth_row(v2, rule2, who2, True)})
    m.report(check, out, ["activation_reprices", "reprice_keeps_prid"])
    out.metrics["P8"] = {"order": po, "records v1/v2": f"{prid1}/{prid2}", "v2 total": str(v2.amounts["total"]),
                         "reasons": reasons, "new order": po2}  # fmt: skip
    return [po, po2], [l1, l2]
