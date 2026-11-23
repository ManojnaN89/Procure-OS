"""E128: an acknowledgement is bound to the version the supplier accepted (task 0.2, DF-07, AT-08), written without the
implementation.

Independence: written from the block 7 spec (sections 5.2 R3-R5, 7 P1, 8, 9.2) and the dataset only. The oracle
(`block7.Oracle7`) works out GST on the delivery place (SUP-005 and PLT-CKN both in state 27), the band and its person
from `approval_rule.csv` and the profile; section 7's figures and `truth/truth_po_versions.csv` are checked against it.

Given a fresh Apex reset on Mon 23 Nov 2026, Q(120 kg, 905.00) gives version 1: taxable 1,08,600.00, CGST and SGST
9,774.00, total 1,28,148.00, PO-02 (the purchase manager U08). U08 approves `{"version": 1}`, the buyer sends (link L1,
`po_version` 1), the supplier accepts from L1:
- then `po.acknowledged_version` is 1 and the `po_version` row 1 records `acknowledged_at`, `acknowledged_by` and
  `ack_via` link; audit `po.acknowledge` carries version 1 and via link.
When the buyer cuts the quantity to 100 (not material: the approval of version 1 carries):
- then version 2 is taxable 90,500.00, CGST and SGST 8,145.00, total 1,06,790.00; status `sent`; `approved_version` 1;
  `acknowledged_version` and `acknowledged_at` empty; row 1 keeps its acknowledgement, row 2 has none; a new link L2
  (`po_version` 2) is mailed.
- the buyer acknowledging `{"version": 1}` in the app is 409 "... has changed since that version: it is now version 2
  ..." with no audit row and nothing written; L1 is refused (used); L2 accepted makes `acknowledged_version` 2 (via link).
- a cut to 90 (version 3) acknowledged in the app with `{"version": 3}` records version 3 via app, and the response
  carries the version.
Ends with a tenant check (Brightwave gets 404 for the order, its versions, comparison and documents; a link forged for
it opens nothing).
Mutations: a revision that keeps `acknowledged_version`; an app acknowledgement that ignores the version.
"""

from typing import Any

import block1 as b
import block4 as k
import block7 as t
import env
from block1 import js
from lib import Outcome, capture_mail

ID = "E128"
TITLE = "An acknowledgement is bound to the version the supplier accepted; a revision voids it"
REQUIREMENT = (
    "Task 0.2, DF-07, AT-08 (block 7 spec R3-R5, P1): po.acknowledged_version and the po_version row record the "
    "version, how and when; a revision clears it while the older row keeps its own; an app acknowledgement of a stale "
    "version is 409 with nothing written; a used link is refused; the new link acknowledges the new version; amounts = "
    "the oracle and the truth file; tenant check"
)
PREFIX = "e128"


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
        po, tokens = _p1(out, check, apex)
        t.tenant_check7(check, apex, bw, [po], tokens, start)
    finally:
        mail.send_email = saved  # type: ignore[assignment]
    out.metrics.update({"checks": check.done, "failed": len(check.failed)})
    out.notes += [
        ("Spec gap: the acknowledge body's other fields are not named for block 7; the eval sends block 2's "
         "promised_date and note with the new `version`."),
        ("L1 is both used and of an older version when tried again; the spec says '409 used', so any 409 passes and "
         "the words are not checked."),
        ("`acknowledged_by` is accepted as 'kind:id', a user id, code, email or name; for a link it is only checked to "
         "be filled in."),
    ]
    return out


def _ack_row_ok(check: b.Checker, co: t.Co7, no: str, v: int, via: str, label: str) -> None:
    row = co.po_versions(no).get(v, {})
    check(bool(row) and row.get("acknowledged_at") is not None and row.get("ack_via") == via
          and bool(row.get("acknowledged_by")),
          f"{label}: po_version row {v} acknowledged_at {row.get('acknowledged_at')}, by {row.get('acknowledged_by')}, "
          f"via {row.get('ack_via')}; expected filled in, via {via}")  # fmt: skip


def _ack_audit_ok(check: b.Checker, co: t.Co7, no: str, mark: int, v: int, via: str, label: str) -> None:
    rows = co.audits("po.acknowledge", no, mark)
    check(len(rows) == 1 and int(rows[0]["after"].get("version") or 0) == v and rows[0]["after"].get("via") == via,
          f"{label}: po.acknowledge audit rows {[r['after'] for r in rows]}; expected one with version {v} and via "
          f"{via}", "medium")  # fmt: skip


def _p1(out: Outcome, check: b.Checker, co: t.Co7) -> tuple[str, list[str | None]]:
    oracle = t.Oracle7(co)
    m = k.Mutations()
    po = co.q_order(check, "P1")
    if not po:
        return "", []
    supplier = str(co.po_row(po).get("supplier_code"))
    v1 = oracle.version(1, t.WIRE, 120, "905.00", t.NEED_BY, supplier, t.LOC)
    rule, who = oracle.approver(v1.amounts["total"], frozenset({co.buyer()}))
    t.self_check(check, "P1v1", t.figures(v1, rule, who))
    t.amounts_ok(check, co, po, v1, "P1 v1")
    check(bool(co.po_versions(po).get(1)), f"P1: {po} has no po_version row for version 1 (R1: written with the order)")
    k.approve_ok(check, co, who, po, 1, "P1 approve version 1")
    l1, _mail, lm = co.send_v(check, po, "P1 send")
    check(t.link_versions(co, po, lm) == [1], f"P1: links issued by the send carry versions {t.link_versions(co, po, lm)}; "
          "expected [1]", "medium")  # fmt: skip

    # the supplier accepts version 1 from L1
    mark = co.audit_mark()
    r = co.accept(l1)
    row = co.po_row(po)
    check(r.status_code == 200 and row.get("status") == "acknowledged", f"P1: L1 accept answered {r.status_code}; "
          f"order {row.get('status')}")  # fmt: skip
    check(row.get("acknowledged_version") == 1, f"P1: acknowledged_version {row.get('acknowledged_version')} after L1; "
          "expected 1 (R3)")  # fmt: skip
    _ack_row_ok(check, co, po, 1, "link", "P1 L1")
    _ack_audit_ok(check, co, po, mark, 1, "link", "P1 L1")

    # the buyer cuts to 100: version 2, not material, the approval carries, the acknowledgement is voided
    v2 = oracle.version(2, t.WIRE, 100, "905.00", t.NEED_BY, supplier, t.LOC)
    t.self_check(check, "P1v2", t.figures(v2))
    want = oracle.reasons(v1, v2)
    check(want == [], f"P1: the oracle finds {want} for the cut to 100 (precondition: not material)", "medium")
    lm = co.link_mark()
    since = co.mark()
    r = co.revise7(po, lines=[{"line_no": 1, "qty": "100"}], version=1)
    row = co.po_row(po)
    check(r.status_code == 200 and int(row.get("version") or 0) == 2, f"P1: the cut to 100 answered {r.status_code} "
          f"{r.text[:200]}; version {row.get('version')}")  # fmt: skip
    t.amounts_ok(check, co, po, v2, "P1 v2")
    m.case("revision_keeps_ack", "P1 cut to 100 after L1", row.get("acknowledged_version", "missing"), None, 1)
    check(row.get("status") == "sent" and row.get("approved_version") == 1 and row.get("acknowledged_version") is None
          and row.get("acknowledged_at") is None,
          f"P1 v2: status {row.get('status')}, approved_version {row.get('approved_version')}, acknowledged_version "
          f"{row.get('acknowledged_version')}, acknowledged_at {row.get('acknowledged_at')}; expected sent, 1, empty, "
          "empty (R5, R21)")  # fmt: skip
    rows = co.po_versions(po)
    check(rows.get(1, {}).get("acknowledged_at") is not None and rows.get(1, {}).get("ack_via") == "link",
          f"P1 v2: row 1 lost its acknowledgement: {rows.get(1, {}).get('acknowledged_at')} "
          f"{rows.get(1, {}).get('ack_via')} (R5: history kept)")  # fmt: skip
    check(bool(rows.get(2)) and rows[2].get("acknowledged_at") is None, f"P1 v2: row 2 {rows.get(2)}; expected a row "
          "with no acknowledgement")  # fmt: skip
    detail = co.detail(po, co.buyer())
    check("acknowledged_version" in detail and detail.get("acknowledged_version") is None,
          f"P1 v2: GET /api/purchase-orders/{po} acknowledged_version {detail.get('acknowledged_version', 'missing')}",
          "medium")  # fmt: skip
    status, listing = co.versions_api(po)
    lv1, lv2 = t.version_row(listing, 1), t.version_row(listing, 2)
    check(status == 200 and lv1.get("ack_via") == "link" and lv1.get("acknowledged_at") is not None
          and lv2.get("acknowledged_at") is None and [int(x.get("version") or 0) for x in listing][:2] == [2, 1],
          f"P1 v2: /versions answered {status} {[(x.get('version'), x.get('ack_via'), x.get('acknowledged_at')) for x in listing]}; "
          "expected newest first, v1 acknowledged by link, v2 not", "medium")  # fmt: skip
    check(lv2.get("approval") == "carried", f"P1 v2: /versions approval of v2 {lv2.get('approval')!r}; expected "
          "'carried'", "medium")  # fmt: skip
    mail2 = co.last_order_mail(po, since)
    l2 = co.token_in(mail2) if mail2 else None
    check(l2 is not None and l2 != l1, f"P1 v2: no new respond link mailed (L2 {l2})")
    check(t.link_versions(co, po, lm) == [2], f"P1 v2: links issued carry versions {t.link_versions(co, po, lm)}; expected "
          "[2]", "medium")  # fmt: skip

    # the buyer acknowledges the stale version 1 in the app: 409, nothing written
    before = co.footprint(po)
    mark = co.audit_mark()
    r = co.ack_app(po, 1)
    stale = r.status_code
    m.case("app_ack_ignores_version", "P1 app acknowledgement of version 1 on version 2", stale, 409, 200)
    t.refusal(check, r, 409, "has changed since that version", "P1 app acknowledgement {'version': 1} on v2")
    check("version 2" in r.text.lower(), f"P1: the refusal does not name version 2: {r.text[:200]}", "medium")
    check(not co.audits("po.acknowledge", po, mark), "P1: the refused acknowledgement wrote a po.acknowledge audit row")
    t.unchanged(check, co, po, before, "P1 stale app acknowledgement")

    # L1 again: refused (used); L2: version 2 acknowledged
    before = co.footprint(po)
    r = co.accept(l1)
    check(r.status_code == 409, f"P1: L1 accepted again answered {r.status_code}; the spec says 409 (used)")
    t.unchanged(check, co, po, before, "P1 L1 again")
    mark = co.audit_mark()
    r = co.accept(l2)
    row = co.po_row(po)
    check(r.status_code == 200 and row.get("acknowledged_version") == 2 and row.get("status") == "acknowledged",
          f"P1: L2 accept answered {r.status_code}; acknowledged_version {row.get('acknowledged_version')}, status "
          f"{row.get('status')}; expected 2, acknowledged")  # fmt: skip
    _ack_row_ok(check, co, po, 2, "link", "P1 L2")
    _ack_audit_ok(check, co, po, mark, 2, "link", "P1 L2")

    # version 3 acknowledged by the buyer in the app with the version on screen (R4)
    v3 = oracle.version(3, t.WIRE, 90, "905.00", t.NEED_BY, supplier, t.LOC)
    r = co.revise7(po, lines=[{"line_no": 1, "qty": "90"}], version=2)
    check(r.status_code == 200 and int(co.po_row(po).get("version") or 0) == 3, f"P1: the cut to 90 answered "
          f"{r.status_code} {r.text[:200]}", "medium")  # fmt: skip
    t.amounts_ok(check, co, po, v3, "P1 v3", "medium")
    mark = co.audit_mark()
    r = co.ack_app(po, 3)
    row = co.po_row(po)
    check(r.status_code == 200 and row.get("acknowledged_version") == 3, f"P1: the app acknowledgement of version 3 "
          f"answered {r.status_code} {r.text[:200]}; acknowledged_version {row.get('acknowledged_version')}")  # fmt: skip
    check(r.status_code == 200 and js(r).get("version") == 3, f"P1: the acknowledge response {str(js(r))[:200]} lacks "
          "version 3 (section 8)", "medium")  # fmt: skip
    _ack_row_ok(check, co, po, 3, "app", "P1 app v3")
    _ack_audit_ok(check, co, po, mark, 3, "app", "P1 app v3")
    rows = co.po_versions(po)
    check(all(rows.get(v, {}).get("acknowledged_at") is not None for v in (1, 2, 3)),
          f"P1: the older rows lost their acknowledgements: {[(v, rows.get(v, {}).get('ack_via')) for v in (1, 2, 3)]}",
          "medium")  # fmt: skip

    t.compare_truth(check, co, {("P1", 1): t.truth_row(v1, rule, who), ("P1", 2): t.truth_row(v2, material=False)})
    m.report(check, out, ["revision_keeps_ack", "app_ack_ignores_version"])
    out.metrics["P1"] = {"order": po, "v1 total": str(v1.amounts["total"]), "v2 total": str(v2.amounts["total"]),
                         "approver": f"{rule} {who}", "stale app ack": stale}  # fmt: skip
    return po, [l1, l2]
