"""E133: a respond link per version, end to end (task 0.2 "new respond link per version", spec 7.7), written without
the implementation.

Independence: written from the block 7 spec (sections 5.8 R23-R25, 7 P7, 8, 9.2) and the dataset only. The oracle is
which version each mailing carries (the eval's own count of versions), the refusal words the spec fixes, and sha256 of
the stored version 2 document against what the supplier's page serves.

P7 on a fresh Apex reset (Mon 23 Nov 2026): Q(120 kg, 905.00) approved by U08 and sent (L1, `po_version` 1). The buyer
moves the need-by 5 days (material: version 2 waits for approval).
- L1's page and its accept are 409 "order <no> has changed since this link was sent: please use the latest email",
  never "is submitted"; nothing is written.
- U08 approves `{"version": 2}`, the buyer sends: the mail "Purchase order <no> (version 2)" carries L2, whose
  `supplier_link` row has `po_version` 2; `/supplier/L2/pdf` is the stored version 2 document (same sha256 as
  `versions/2/pdf` and as the mail's attachment); L1 is still refused.
- L2 accept: `acknowledged_version` 2, `ack_via` link; L2 again: 409 (used).
- The buyer sets the delivery terms (minor): version 3, L3 mailed (`po_version` 3); L3 accept: `acknowledged_version` 3.
- A token with one character changed: "not valid", nothing written.
Ends with a tenant check (a link forged for Brightwave opens nothing, its PDF neither).
Mutations: the status checked before the version (old links read "is submitted"); a link issued for the old version.
"""

from typing import Any

import block1 as b
import block4 as k
import block7 as t
import env
from lib import Outcome, capture_mail

ID = "E133"
TITLE = "Every mailing of a version carries its own respond link; older links say the order changed"
REQUIREMENT = (
    "Task 0.2 (block 7 spec R23-R25, P7): an older version's respond link is refused with 'has changed since this link "
    "was sent' whatever the order's status; each mailing issues a link for the version mailed; the supplier's page "
    "serves that version's stored PDF; a used link is refused; acknowledgements land on versions 2 and 3; a tampered "
    "token is not valid; tenant check"
)
PREFIX = "e133"
CHANGED = "has changed since this link was sent"


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
        po, tokens = _p7(out, check, apex)
        t.tenant_check7(check, apex, bw, [po], tokens, start)
    finally:
        mail.send_email = saved  # type: ignore[assignment]
    out.metrics.update({"checks": check.done, "failed": len(check.failed)})
    out.notes += [
        ("The subject 'Purchase order <no> (version 2)' is checked as medium: R24 says the existing mail text is kept, "
         "and section 7 P7 names the subject."),
        ("A tampered token changes the last character of the signature; 'not valid' is looked for in the page, any "
         "refusal status passes."),
        ("L2 tried again: any 409 passes; the word 'used' (or 'already') is checked as low, the spec gives no sentence."),
    ]
    return out


def _old_refused(check: b.Checker, co: t.Co7, no: str, token: str | None, label: str) -> bool:
    """The old link's page and accept: 409 with the spec's words, never the internal status; nothing written."""
    if not check(token is not None, f"{label}: no old link to try"):
        return False
    before = co.footprint(no)
    page = co.supplier_page(token or "")
    t.refusal(check, page, 409, CHANGED, f"{label}: GET the old link", "high")
    check(t.norm(f"order {no}") in t.norm(page.text) and "please use the latest email" in t.norm(page.text),
          f"{label}: the old link's page lacks 'order {no} ... please use the latest email': {page.text[:200]}",
          "medium")  # fmt: skip
    check("is submitted" not in t.norm(page.text), f"{label}: the old link's page shows the internal status: "
          f"{page.text[:200]}")  # fmt: skip
    posted = co.accept(token)
    t.refusal(check, posted, 409, CHANGED, f"{label}: POST accept on the old link", "high")
    t.unchanged(check, co, no, before, f"{label}: the old link")
    return CHANGED in t.norm(page.text)


def _p7(out: Outcome, check: b.Checker, co: t.Co7) -> tuple[str, list[str | None]]:
    oracle = t.Oracle7(co)
    m = k.Mutations()
    po = co.q_order(check, "P7")
    if not po:
        return "", []
    supplier = str(co.po_row(po).get("supplier_code"))
    v1 = oracle.version(1, t.WIRE, 120, "905.00", t.NEED_BY, supplier, t.LOC)
    v2 = oracle.version(2, t.WIRE, 120, "905.00", t.DEC19, supplier, t.LOC)
    rule, who = oracle.approver(v1.amounts["total"], frozenset({co.buyer()}))
    t.self_check(check, "P1v1", t.figures(v1, rule, who))
    want = oracle.reasons(v1, v2)
    check(bool(want), "P7: the oracle finds +5 days not material (precondition)", "medium")
    k.approve_ok(check, co, who, po, 1, "P7 approve version 1")
    l1, _m1, lm = co.send_v(check, po, "P7 send")
    check(t.link_versions(co, po, lm) == [1], f"P7: L1's versions {t.link_versions(co, po, lm)}; expected [1]", "medium")

    # +5 days: material, version 2 waits for approval; L1 says the order changed
    r = co.revise7(po, lines=[{"line_no": 1, "need_by": t.DEC19.isoformat()}], version=1)
    row = co.po_row(po)
    check(r.status_code == 200 and int(row.get("version") or 0) == 2 and row.get("status") == "submitted",
          f"P7: +5 days answered {r.status_code} {r.text[:150]}; version {row.get('version')}, status "
          f"{row.get('status')}; expected 2, submitted")  # fmt: skip
    said = _old_refused(check, co, po, l1, "P7 L1 while version 2 waits")
    m.case("status_before_version", "P7 L1 while version 2 waits for approval", said, True, False)

    # approved and sent: L2 for version 2, the supplier's PDF is the stored one
    k.approve_ok(check, co, who, po, 2, "P7 approve version 2")
    lm = co.link_mark()
    since = co.mark()
    r = co.send(po)
    check(r.status_code == 200, f"P7: sending version 2 answered {r.status_code} {r.text[:200]}")
    mail2 = co.last_order_mail(po, since)
    l2 = co.token_in(mail2) if mail2 else None
    check(l2 is not None and l2 != l1, f"P7: the version 2 mail carries no new link ({l2})")
    check(t.norm(f"Purchase order {po} (version 2)") in t.norm(mail2.get("subject")), f"P7: the version 2 mail's "
          f"subject {mail2.get('subject')!r}; the spec says 'Purchase order {po} (version 2)'", "medium")  # fmt: skip
    versions = t.link_versions(co, po, lm)
    m.case("link_old_version", "P7 the link mailed with version 2", versions, [2], [1])
    check(versions == [2], f"P7: links issued with the version 2 mail carry versions {versions}; expected [2] (R24)")
    stored = co.version_pdf(po, 2)
    page_pdf = co.supplier_pdf(l2 or "")
    attached = t.attached_pdfs(mail2)
    s_stored = t.sha(stored.content) if stored.status_code == 200 and stored.content[:5] == b"%PDF-" else None
    s_page = t.sha(page_pdf.content) if page_pdf.status_code == 200 and page_pdf.content[:5] == b"%PDF-" else None
    check(s_page is not None and s_page == s_stored, f"P7: /supplier/L2/pdf answered {page_pdf.status_code} sha256 "
          f"{s_page}; the stored version 2 is {s_stored} ({stored.status_code}) (R25)")  # fmt: skip
    check(len(attached) == 1 and t.sha(attached[0]) == s_page, "P7: the version 2 mail's PDF differs from the "
          "supplier page's", "medium")  # fmt: skip
    page = co.supplier_page(l2 or "")
    check(page.status_code == 200 and "/pdf" in page.text, f"P7: L2's page ({page.status_code}) offers no link to the "
          "version's PDF (R25 'This version of the order (PDF)')", "medium")  # fmt: skip
    _old_refused(check, co, po, l1, "P7 L1 after version 2 was sent")
    r = co.supplier_pdf(l1 or "")
    check(r.status_code == 409 and r.content[:5] != b"%PDF-", f"P7: /supplier/L1/pdf answered {r.status_code}; the "
          "spec says 409 as the link check", "medium")  # fmt: skip

    # L2 accepted once
    r = co.accept(l2)
    row = co.po_row(po)
    ver2 = co.po_versions(po).get(2, {})
    check(r.status_code == 200 and row.get("acknowledged_version") == 2 and ver2.get("ack_via") == "link",
          f"P7: L2 accept answered {r.status_code}; acknowledged_version {row.get('acknowledged_version')}, ack_via "
          f"{ver2.get('ack_via')}; expected 2, link")  # fmt: skip
    before = co.footprint(po)
    r = co.accept(l2)
    check(r.status_code == 409, f"P7: L2 accepted again answered {r.status_code}; the spec says 409 (used)")
    check(any(w in t.norm(r.text) for w in ("used", "already")), f"P7: L2 again does not say it was used: "
          f"{r.text[:200]}", "low")  # fmt: skip
    t.unchanged(check, co, po, before, "P7 L2 again")

    # a minor change: version 3 and its own link
    lm = co.link_mark()
    since = co.mark()
    r = co.revise7(po, header={"delivery_terms": "ex_works"}, version=2, reason="supplier collects from its works")
    mail3 = co.last_order_mail(po, since)
    l3 = co.token_in(mail3) if mail3 else None
    check(r.status_code == 200 and int(co.po_row(po).get("version") or 0) == 3 and co.po_row(po).get("status") == "sent",
          f"P7: delivery terms answered {r.status_code} {r.text[:150]}; version {co.po_row(po).get('version')}, "
          f"status {co.po_row(po).get('status')}; expected 3, sent")  # fmt: skip
    check(l3 is not None and t.link_versions(co, po, lm) == [3], f"P7: version 3 mailed with link {l3}, versions "
          f"{t.link_versions(co, po, lm)}; expected one for version 3")  # fmt: skip
    r = co.supplier_page(l2 or "")
    check(r.status_code == 409, f"P7: L2 (used, version 2) after version 3 answered {r.status_code}", "medium")
    r = co.accept(l3)
    row = co.po_row(po)
    check(r.status_code == 200 and row.get("acknowledged_version") == 3, f"P7: L3 accept answered {r.status_code}; "
          f"acknowledged_version {row.get('acknowledged_version')}; expected 3")  # fmt: skip
    rows = co.po_versions(po)
    check([rows.get(v, {}).get("ack_via") for v in (2, 3)] == ["link", "link"] and rows.get(1, {}).get("acknowledged_at")
          is None, f"P7: acknowledgements by version {[(v, rows.get(v, {}).get('ack_via')) for v in (1, 2, 3)]}; "
          "expected none on 1 (never accepted), link on 2 and 3", "medium")  # fmt: skip

    # a tampered token
    if l3:
        bad = l3[:-1] + ("A" if l3[-1] != "A" else "B")
        before = co.footprint(po)
        r = co.supplier_page(bad)
        check(r.status_code in t.REFUSED and "not valid" in t.norm(r.text), f"P7: a token with one character changed "
              f"answered {r.status_code} {r.text[:150]}; expected 'not valid'", "medium")  # fmt: skip
        co.accept(bad)
        t.unchanged(check, co, po, before, "P7 tampered token")
    m.report(check, out, ["status_before_version", "link_old_version"])
    out.metrics["P7"] = {"order": po, "L1 refusal words seen": said, "links of version 2": versions}
    return po, [l1, l2, l3]
