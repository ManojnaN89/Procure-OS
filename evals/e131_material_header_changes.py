"""E131: a material header change is approved again (Q19, spec 7.5), written without the implementation.

Independence: written from the block 7 spec (sections 4, 5.5 R11-R13, 5.6 R18-R20, 7 P4 and P5, 8, 9.2) and the
dataset only. The oracle (`block7.Oracle7`) works out GST on the place of supply (Brightwave's SUP-001 is in state 29:
OFF-BLR 29 splits CGST and SGST, OFF-HYD 36 is IGST), the buyer's GSTIN of the place (`location.csv`), the material
reasons from the last approved version (block 4's Q19 rule in its order), and the band's person with makers skipped
(`approval_rule.csv`: an automatic band moves up, a maker is passed over).

P4 on a fresh Brightwave reset (Mon 23 Nov 2026): the employee asks for 2 LP-01 at OFF-BLR; approved (REQ-06); sourced
on CNT-0001 at 62,000.00: version 1 taxable 1,24,000.00, CGST and SGST 11,160.00, total 1,46,320.00, PO-02 for the CFO
(U09); she approves, the buyer sends (L1), the supplier accepts. The buyer moves the delivery place to OFF-HYD:
- version 2 has CGST 0, SGST 0, IGST 22,320.00, total 1,46,320.00 and `buyer_gstin` 36BRTCB0000T1ZC; material
  ["delivery location changed"]; status `submitted` with an open step for U09 carrying version 2;
  `acknowledged_version` empty; L1 refused "has changed since"; U09 approves version 2, the buyer sends, the new link
  opens and accepts, and the version 2 PDF shows IGST and the Hyderabad office.
P5 on a fresh Apex reset: the purchase manager gets the Buyer role as a second job (two administrators, as E54).
K(RM-05, 10, 14 Dec 2026) is touchless: approved by `rule:PO-01` and sent by the system (1,800.00 + 45.00 + 45.00 =
1,890.00). The purchase manager changes the payment terms NET30 -> ADV30N30:
- version 2, same total; material ["payment terms changed"]; `touchless` false, its checks show `touchless.revision`
  and the advance test failed; PO-01 has no approver and U08 is a maker, so the step is U09 (PO-03) with version 2; the
  system's link refused; after U09 approves and the buyer sends, the new link works.
Ends with tenant checks both ways.
Mutations: a location change carried without approval; the reviser not counted as a maker.
"""

from typing import Any

import block1 as b
import block4 as k
import block7 as t
import env
from lib import Outcome, capture_mail

ID = "E131"
TITLE = "A material header change (delivery place, payment terms) is approved again; the reviser is a maker"
REQUIREMENT = (
    "Q19, Rev s5 (block 7 spec R12, R13, R18-R20, P4, P5): moving the delivery place re-taxes on the new place of supply "
    "and sets the place's GSTIN; a delivery place or payment terms change is material, goes back to the band's person "
    "with the new version (the reviser skipped as a maker), voids the acknowledgement and old links, and ends touchless; "
    "after approval and sending a new link works; tenant checks"
)
PREFIX = "e131"


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
        m = k.Mutations()
        t.start(bw)
        start = bw.mark()
        bw_po, bw_tokens = _p4(out, check, bw, m)
        t.start(apex)
        ap_po, ap_tokens = _p5(out, check, apex, m)
        m.report(check, out, ["location_carried", "reviser_not_maker"])
        t.compare_truth(check, bw, out.metrics.pop("_truth_p4", {}))
        t.compare_truth(check, apex, out.metrics.pop("_truth_p5", {}))
        t.tenant_check7(check, bw, apex, [bw_po], bw_tokens, start)
        t.tenant_check7(check, apex, bw, [ap_po], ap_tokens, start)
    finally:
        mail.send_email = saved  # type: ignore[assignment]
    out.metrics.update({"checks": check.done, "failed": len(check.failed)})
    out.notes += [
        ("Touchless checks are read from po.touchless_checks (a list of {test, passed, detail}); the advance test's "
         "name is not given by the spec, so a failed check whose name or detail says 'advance' is accepted."),
        ("The new respond link after re-approval is the one in the mail of the second sending; its supplier_link row "
         "is read for po_version."),
        "The location's name is looked for in the PDF as written in location.csv, or the code OFF-HYD.",
    ]
    return out


def _checks(row: dict[str, Any]) -> list[dict[str, Any]]:
    return [c for c in k.as_json(row.get("touchless_checks")) or [] if isinstance(c, dict)]


def _name(c: dict[str, Any]) -> str:
    return str(c.get("test") or c.get("code") or c.get("name") or "")


def _reapproved(check: b.Checker, co: t.Co7, no: str, want_reasons: list[str], approver: str, label: str) -> None:
    row = co.po_row(no)
    check(row.get("status") == "submitted" and int(row.get("version") or 0) == 2,
          f"{label}: status {row.get('status')}, version {row.get('version')}; expected submitted, 2 (R18)")  # fmt: skip
    k.check_reasons(check, label, co.revisions(no).get(2, {}).get("material"), want_reasons, tuple(want_reasons))
    check(co.revisions(no).get(2, {}).get("reapproval") is True, f"{label}: po_revision 2 reapproval "
          f"{co.revisions(no).get(2, {}).get('reapproval')}", "medium")  # fmt: skip
    pend = co.open_steps(no)
    check(len(pend) == 1 and co.code_of(pend[0].get("approver")) == approver and pend[0].get("version") == 2,
          f"{label}: open steps {[(co.code_of(s.get('approver')), s.get('version')) for s in pend]}; expected "
          f"{approver} with version 2")  # fmt: skip
    check("acknowledged_version" in row and row.get("acknowledged_version") is None and row.get("acknowledged_at") is None,
          f"{label}: acknowledged_version {row.get('acknowledged_version', 'missing')}, acknowledged_at "
          f"{row.get('acknowledged_at')}; expected both empty (R5)")  # fmt: skip
    check(row.get("approved_version") == 1, f"{label}: approved_version {row.get('approved_version')}; expected 1 until "
          "the new approval", "medium")  # fmt: skip
    check(row.get("touchless") is not True, f"{label}: the re-approved order is touchless (R20)")


def _old_link(check: b.Checker, co: t.Co7, no: str, token: str | None, label: str) -> None:
    if not check(token is not None, f"{label}: no old link to try"):
        return
    before = co.footprint(no)
    r = co.supplier_page(token or "")
    t.refusal(check, r, 409, "has changed since this link was sent", f"{label}: the old link's page")
    r = co.accept(token)
    check(r.status_code in (409, 410), f"{label}: accepting on the old link answered {r.status_code}")
    t.unchanged(check, co, no, before, f"{label}: the old link")


def _resend_works(check: b.Checker, co: t.Co7, no: str, approver: str, label: str) -> str | None:
    k.approve_ok(check, co, approver, no, 2, f"{label} approve version 2")
    token, _mail, lm = co.send_v(check, no, f"{label} send version 2")
    check(t.link_versions(co, no, lm) == [2], f"{label}: the new link's versions {t.link_versions(co, no, lm)}; "
          "expected [2]")  # fmt: skip
    r = co.supplier_page(token or "")
    check(r.status_code == 200, f"{label}: the new link's page answered {r.status_code} {r.text[:150]}")
    r = co.accept(token)
    row = co.po_row(no)
    check(r.status_code == 200 and row.get("acknowledged_version") == 2, f"{label}: accepting on the new link answered "
          f"{r.status_code}; acknowledged_version {row.get('acknowledged_version')}")  # fmt: skip
    return token


def _p4(out: Outcome, check: b.Checker, co: t.Co7, m: k.Mutations) -> tuple[str, list[str | None]]:
    oracle = t.Oracle7(co)
    po = co.k_order(check, t.LAPTOP, 2, t.NEED_BY, "P4", loc=t.BLR, by=co.login("employee"))
    if not po:
        return "", []
    terms = str(co.po_row(po).get("payment_terms_code") or "")
    v1 = oracle.version(1, t.LAPTOP, 2, "62000.00", t.NEED_BY, "SUP-001", t.BLR, terms=terms)
    v2 = oracle.version(2, t.LAPTOP, 2, "62000.00", t.NEED_BY, "SUP-001", t.HYD, terms=terms)
    rule, who = oracle.approver(v1.amounts["total"], frozenset({co.buyer()}))
    t.self_check(check, "P4v1", t.figures(v1, rule, who))
    t.self_check(check, "P4v2", t.figures(v2))
    t.amounts_ok(check, co, po, v1, "P4 v1")
    check(co.po_row(po).get("buyer_gstin") == v1.buyer_gstin, f"P4 v1: buyer_gstin {co.po_row(po).get('buyer_gstin')}; "
          f"oracle {v1.buyer_gstin}", "medium")  # fmt: skip
    pend = co.open_steps(po)
    check(len(pend) == 1 and co.code_of(pend[0].get("approver")) == who and str(co.po_row(po).get("rule_code")) == rule,
          f"P4 v1: steps {[co.code_of(s.get('approver')) for s in pend]}, band {co.po_row(po).get('rule_code')}; "
          f"oracle {rule} {who}")  # fmt: skip
    k.approve_ok(check, co, who, po, 1, "P4 approve version 1")
    l1, _m1, _ = co.send_v(check, po, "P4 send")
    co.accept(l1)
    want = oracle.reasons(v1, v2)

    r = co.revise7(po, header={"ship_to_location": t.HYD}, version=1, reason="the laptops go to the Hyderabad team")
    row, ln = co.po_row(po), co.po_line1(po)
    check(r.status_code == 200, f"P4: moving the delivery to {t.HYD} answered {r.status_code} {r.text[:200]}")
    m.case("location_carried", "P4 delivery place moved after acknowledgement", row.get("status"), "submitted", "sent")
    check(row.get("ship_to_location") == t.HYD and row.get("buyer_gstin") == v2.buyer_gstin,
          f"P4 v2: ship_to {row.get('ship_to_location')}, buyer_gstin {row.get('buyer_gstin')}; oracle {t.HYD}, "
          f"{v2.buyer_gstin} (R12)")  # fmt: skip
    t.amounts_ok(check, co, po, v2, "P4 v2")
    check(t.D(ln.get("igst")) == v2.amounts["igst"] and t.D(ln.get("cgst")) == 0 and t.D(ln.get("sgst")) == 0,
          f"P4 v2: line 1 CGST {ln.get('cgst')}, SGST {ln.get('sgst')}, IGST {ln.get('igst')}; oracle 0, 0, "
          f"{v2.amounts['igst']}")  # fmt: skip
    _reapproved(check, co, po, want, who, "P4 v2")
    _old_link(check, co, po, l1, "P4 v2")
    detail = co.call(co.buyer(), "GET", f"/api/purchase-orders/{po}/controls")
    body = t.js(detail) if detail.status_code == 200 else {}
    places = [x.get("code") for x in t.as_rows((body.get("options") or {}).get("locations") if isinstance(body.get(
        "options"), dict) else [])]  # fmt: skip
    check(set(places) >= {t.BLR, t.HYD}, f"P4: /controls options.locations {places}; expected both offices (section 8)",
          "medium")  # fmt: skip
    l2 = _resend_works(check, co, po, who, "P4")
    data = co.version_pdf(po, 2)
    text_ = t.pdf_text(data.content if data.status_code == 200 else None)
    hyd = oracle.locations[t.HYD]["name"]
    check("igst" in t.norm(text_) and (t.HYD.lower() in t.norm(text_) or t.norm(hyd) in t.norm(text_)),
          f"P4: the version 2 PDF ({data.status_code}) does not show IGST and the Hyderabad office: {text_[:300]!r}",
          "medium")  # fmt: skip
    check(t.inr(v2.amounts["igst"]) in text_, f"P4: the version 2 PDF lacks IGST {t.inr(v2.amounts['igst'])}", "medium")
    out.metrics["P4"] = {"order": po, "v2 igst": str(v2.amounts["igst"]), "reasons": want, "approver": who}
    out.metrics["_truth_p4"] = {("P4", 1): t.truth_row(v1, rule, who), ("P4", 2): t.truth_row(v2, rule, who, True)}
    return po, [l1, l2]


def _p5(out: Outcome, check: b.Checker, co: t.Co7, m: k.Mutations) -> tuple[str, list[str | None]]:
    oracle = t.Oracle7(co)
    pm = co.login("purchasemanager")
    co.grant(check, pm, "Buyer", "P5 second job")
    since = co.mark()
    lm = co.link_mark()
    po = co.k_order(check, t.SHAFT, 10, t.NEED_BY, "P5")
    if not po:
        return "", []
    row = co.po_row(po)
    v1 = oracle.version(1, t.SHAFT, 10, "180.00", t.NEED_BY, "SUP-001", t.LOC, terms="NET30")
    v2 = oracle.version(2, t.SHAFT, 10, "180.00", t.NEED_BY, "SUP-001", t.LOC, terms="ADV30N30")
    band = oracle.band(v1.amounts["total"])["rule_code"]
    rule2, who2 = oracle.approver(v2.amounts["total"], frozenset({pm}))
    t.self_check(check, "P5v1", {**t.figures(v1), "rule": band})
    t.self_check(check, "P5v2", {**t.figures(v2), "approver": who2})
    t.amounts_ok(check, co, po, v1, "P5 v1")
    snaps = co.approved_versions(po)
    check(row.get("touchless") is True and row.get("status") in ("sent", "acknowledged") and row.get("payment_terms_code")
          == "NET30" and [str(x.get("approved_by")) for x in snaps] == [f"rule:{band}"],
          f"P5: precondition: touchless {row.get('touchless')}, status {row.get('status')}, terms "
          f"{row.get('payment_terms_code')}, approvals {[x.get('approved_by') for x in snaps]}; expected a touchless "
          f"order sent by the system on NET30, approved by rule:{band}")  # fmt: skip
    mail1 = co.last_order_mail(po, since)
    l1 = co.token_in(mail1) if mail1 else None
    check(l1 is not None and t.link_versions(co, po, lm) == [1], f"P5: the system's order mail link {l1}, versions "
          f"{t.link_versions(co, po, lm)}", "medium")  # fmt: skip
    want = oracle.reasons(v1, v2)

    r = co.revise7(po, by=pm, header={"payment_terms_code": "ADV30N30"}, version=1, reason="supplier asks an advance")
    row = co.po_row(po)
    check(r.status_code == 200 and row.get("payment_terms_code") == "ADV30N30", f"P5: the purchase manager's change "
          f"to ADV30N30 answered {r.status_code} {r.text[:200]}; terms {row.get('payment_terms_code')}")  # fmt: skip
    t.amounts_ok(check, co, po, v2, "P5 v2", "medium")
    pend = co.open_steps(po)
    step = co.code_of(pend[0].get("approver")) if len(pend) == 1 else None
    m.case("reviser_not_maker", "P5 the purchase manager changes the terms", step, who2, "U08")
    _reapproved(check, co, po, want, who2, "P5 v2")
    check(rule2 == "PO-03" and who2 == "U09", f"P5: the oracle's approver {rule2} {who2}; the spec says PO-03 U09", "medium")
    checks = _checks(row)
    rev = [c for c in checks if _name(c) == "touchless.revision"]
    adv = [c for c in checks if "advance" in (_name(c) + " " + str(c.get("detail") or "")).lower()]
    check(row.get("touchless") is False and len(rev) == 1 and rev[0].get("passed") is False
          and any(c.get("passed") is False for c in adv),
          f"P5 v2: touchless {row.get('touchless')}, checks {[(_name(c), c.get('passed')) for c in checks]}; expected "
          "false with touchless.revision and the advance test failed (R20)")  # fmt: skip
    _old_link(check, co, po, l1, "P5 v2")
    l2 = _resend_works(check, co, po, who2, "P5")
    out.metrics["P5"] = {"order": po, "total": str(v2.amounts["total"]), "reasons": want, "step": step,
                         "oracle": f"{rule2} {who2}"}  # fmt: skip
    out.metrics["_truth_p5"] = {("P5", 1): t.truth_row(v1, band, ""), ("P5", 2): t.truth_row(v2, rule2, who2, True)}
    return po, [l1, l2]
