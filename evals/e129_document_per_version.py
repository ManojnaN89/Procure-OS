"""E129: a stored document per issued version (task 0.2, spec 7.2), written without the implementation.

Independence: written from the block 7 spec (sections 5.3 R6-R8, 7 P2, 8, 9.2) and the dataset only. The oracle is
the bytes themselves: sha256 of the PDF attached to each order mail (caught in this process) against sha256 of what the
reprint routes return; the amounts in the text from `block7.Oracle7` (GST on the delivery place), printed the Indian way.

Given a fresh Apex reset on Mon 23 Nov 2026, P1 to version 2: Q(120 kg, 905.00) approved by U08, sent (mail M1, link
L1), accepted, cut to 100 (not material: mail M2 with L2).
- Then the `po_version` rows 1 and 2 each have a `document_id`; sha256 of `GET .../versions/1/pdf` equals M1's attached
  PDF and of `.../versions/2/pdf` equals M2's; the `document` rows hold the same sha256 (`doc_type` po_version, linked to
  the order); `/versions` gives `document {id, sha256}`; audit `po.version_issued` names the version and sha256; version
  2's text has "version 2", "This version: 23 Nov 2026" and "1,06,790.00".
When the owner changes `po_terms_template` (the existing two-administrator setting change):
- then both stored PDFs come back unchanged byte for byte and without the new terms; `/pdf` of the issued version 2 is
  the stored one.
When the buyer moves the need-by to 19 Dec 2026 (material, 5 days from the approved 14 Dec):
- then version 3 has no document; `versions/3/pdf` is 404 "version 3 was not issued"; `/pdf` renders live with the
  title ending "DRAFT: not issued" (and the new terms); after U08 approves version 3 and the buyer sends (mail M3),
  version 3 is stored and equals M3's attachment.
Ends with a tenant check.
Mutations: a reprint that renders again; a document stored for an unissued version.
"""

from typing import Any

import block1 as b
import block4 as k
import block7 as t
import env
from lib import Outcome, capture_mail

ID = "E129"
TITLE = "Every issued version keeps the exact document mailed; reprints return it byte for byte"
REQUIREMENT = (
    "Task 0.2 (block 7 spec R6-R8, P2): a version mailed to the supplier stores its PDF once and the mail attaches that "
    "file; reprints return the stored bytes (sha256 = the attachment) whatever changed since; a version never mailed has "
    "no document (404) and /pdf renders it as 'DRAFT: not issued'; the text names the version, its date and the total; "
    "tenant check"
)
PREFIX = "e129"
NEW_TERMS = "E129 terms of 23 Nov 2026: changed after the order was issued"


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
        po, tokens = _p2(out, check, apex)
        t.tenant_check7(check, apex, bw, [po], tokens, start)
    finally:
        mail.send_email = saved  # type: ignore[assignment]
    out.metrics.update({"checks": check.done, "failed": len(check.failed)})
    out.notes += [
        ("The attachment is read from the captured mail call (seen shape: a list of (name, type, bytes)); the spec's "
         "'one stored file attached by id' (OP-11) is checked through the bytes, not the outbox row."),
        ("Spec gap: whether the terms template is printed on the PDF is not said; the draft of version 3 is checked to "
         "show the new terms (medium), which is what makes the unchanged reprints a real test."),
        "The filename `<no>-v<v>.pdf` and the content type are checked as low.",
        ("P2's 'existing two-person setting change': `po_terms_template` is a policy two administrators change through "
         "`/api/setup/policies` (owner's decision after the first run); the eval still falls back to writing it as the "
         "owner, with a low finding, if the API refuses it."),
    ]
    return out


def _set_terms(check: b.Checker, co: t.Co7) -> bool:
    """The terms template changed by two administrators when the policies API takes the key; otherwise written as the
    owner (the capability matrix says the template has no admin screen yet), and the refusal is a low finding: what
    E129 tests is the stored documents, not how the template is changed."""
    import aif

    asked, done = co.policy("po_terms_template", NEW_TERMS)
    if not (asked.status_code == 202 and done is not None and done.status_code == 200):
        check(False, f"P2: the policies API does not take po_terms_template ({asked.status_code} {asked.text[:120]}); "
              "the template was written as the owner", "low")  # fmt: skip
        aif.set_org(co, "po_terms_template", NEW_TERMS)
    # the settings API keeps a text setting with one trailing newline (as rfq_email_template): the same terms
    return str(co.org("po_terms_template") or "").rstrip("\n") == NEW_TERMS


def _stored(check: b.Checker, co: t.Co7, no: str, v: int, label: str) -> bytes | None:
    """GET versions/{v}/pdf: the bytes when it is a PDF, else None (with a finding)."""
    r = co.version_pdf(no, v)
    ok = r.status_code == 200 and r.content[:5] == b"%PDF-"
    check(ok, f"{label}: GET versions/{v}/pdf answered {r.status_code} {r.text[:150] if not ok else ''}")
    if ok:
        check(r.headers.get("content-type", "").startswith("application/pdf")
              and f"{no}-v{v}.pdf" in r.headers.get("content-disposition", ""),
              f"{label}: versions/{v}/pdf type {r.headers.get('content-type')}, disposition "
              f"{r.headers.get('content-disposition')}; the spec says application/pdf named {no}-v{v}.pdf", "low")  # fmt: skip
    return r.content if ok else None


def _issued_ok(check: b.Checker, co: t.Co7, no: str, v: int, mail: dict[str, Any], mark: int, label: str) -> str | None:
    """The version's stored document = the PDF attached to its mail; the rows and the audit agree."""
    pdfs = t.attached_pdfs(mail)
    check(len(pdfs) == 1, f"{label}: the order mail carries {len(pdfs)} PDF attachment(s); expected one")
    sent = t.sha(pdfs[0]) if pdfs else None
    data = _stored(check, co, no, v, label)
    got = t.sha(data)
    check(got is not None and got == sent, f"{label}: sha256 of versions/{v}/pdf {got} differs from the mailed attachment "
          f"{sent} (R6, R7)")  # fmt: skip
    row = co.po_versions(no).get(v, {})
    doc = co.document(row.get("document_id"))
    check(bool(row.get("document_id")) and row.get("sent_at") is not None, f"{label}: po_version row {v} document_id "
          f"{row.get('document_id')}, sent_at {row.get('sent_at')}; expected both (R6)")  # fmt: skip
    check(bool(doc) and doc.get("sha256") == sent and doc.get("doc_type") == "po_version"
          and doc.get("linked_type") == "po" and doc.get("linked_id") == no,
          f"{label}: document row {({x: doc.get(x) for x in ('sha256', 'doc_type', 'linked_type', 'linked_id')})}; "
          f"expected sha256 {sent}, po_version, po, {no}", "medium")  # fmt: skip
    status, listing = co.versions_api(no)
    entry = t.version_row(listing, v)
    document = entry.get("document") if isinstance(entry.get("document"), dict) else {}
    check(status == 200 and document.get("sha256") == sent and str(document.get("id")) == str(row.get("document_id")),
          f"{label}: /versions entry {v} document {document}; expected the stored id and sha256", "medium")  # fmt: skip
    audits = [a for a in co.audits("po.version_issued", no, mark) if int(a["after"].get("version") or 0) == v]
    check(len(audits) == 1 and sent is not None and sent in str(audits[0]["after"]),
          f"{label}: po.version_issued audit rows for version {v}: {[a['after'] for a in audits]}; expected one with "
          "the sha256", "medium")  # fmt: skip
    return got


def _p2(out: Outcome, check: b.Checker, co: t.Co7) -> tuple[str, list[str | None]]:
    oracle = t.Oracle7(co)
    m = k.Mutations()
    po = co.q_order(check, "P2")
    if not po:
        return "", []
    supplier = str(co.po_row(po).get("supplier_code"))
    v1 = oracle.version(1, t.WIRE, 120, "905.00", t.NEED_BY, supplier, t.LOC)
    v2 = oracle.version(2, t.WIRE, 100, "905.00", t.NEED_BY, supplier, t.LOC)
    rule, who = oracle.approver(v1.amounts["total"], frozenset({co.buyer()}))
    t.self_check(check, "P1v1", t.figures(v1, rule, who))
    t.self_check(check, "P1v2", t.figures(v2))
    k.approve_ok(check, co, who, po, 1, "P2 approve version 1")
    mark = co.audit_mark()
    l1, m1, _ = co.send_v(check, po, "P2 send")
    sha1 = _issued_ok(check, co, po, 1, m1, mark, "P2 v1")
    r = co.accept(l1)
    check(r.status_code == 200, f"P2: L1 accept answered {r.status_code}", "medium")
    since = co.mark()
    mark = co.audit_mark()
    r = co.revise7(po, lines=[{"line_no": 1, "qty": "100"}], version=1)
    check(r.status_code == 200 and int(co.po_row(po).get("version") or 0) == 2, f"P2: the cut to 100 answered "
          f"{r.status_code} {r.text[:200]}")  # fmt: skip
    t.amounts_ok(check, co, po, v2, "P2 v2", "medium")
    m2 = co.last_order_mail(po, since)
    l2 = co.token_in(m2) if m2 else None
    check(bool(m2), "P2: no mail of version 2 to the supplier (R6: a minor revision is issued)")
    sha2 = _issued_ok(check, co, po, 2, m2, mark, "P2 v2")
    text1 = t.pdf_text(_stored(check, co, po, 1, "P2 v1 text"))
    text2 = t.pdf_text(_stored(check, co, po, 2, "P2 v2 text"))
    check(t.inr(v1.amounts["total"]) in text1, f"P2: version 1's PDF lacks the total {t.inr(v1.amounts['total'])}: "
          f"{text1[:300]}", "medium")  # fmt: skip
    for words in ("version 2", t.inr(v2.amounts["total"])):
        check(t.norm(words) in t.norm(text2), f"P2: version 2's PDF lacks '{words}': {text2[:400]}")
    for words in (f"PURCHASE ORDER {po}, version 2", f"This version: {t.long_date(t.TODAY)}"):
        check(t.norm(words) in t.norm(text2), f"P2: version 2's PDF lacks '{words}' (R8): {text2[:400]}", "medium")

    # the owner changes the terms template: the stored documents stay as they were
    ok = _set_terms(check, co)
    check(ok, "P2: po_terms_template is not the new text after the change (precondition)", "medium")
    after1 = t.sha(_stored(check, co, po, 1, "P2 v1 after the terms change"))
    after2_data = _stored(check, co, po, 2, "P2 v2 after the terms change")
    after2 = t.sha(after2_data)
    same = after1 == sha1 and after2 == sha2 and sha1 is not None and sha2 is not None
    m.case("reprint_renders_again", "P2 reprints after the terms change", same, True, False)
    check(same, f"P2: after the terms change versions 1 and 2 are {after1} / {after2}; stored and mailed {sha1} / "
          f"{sha2} (R7: never rendered again)")  # fmt: skip
    check(t.norm(NEW_TERMS) not in t.norm(t.pdf_text(after2_data)), "P2: the reprint of version 2 shows the new terms")
    r = co.pdf(po)
    check(r.status_code == 200 and t.sha(r.content) == sha2, f"P2: /pdf of the issued version 2 answered {r.status_code} "
          f"sha256 {t.sha(r.content) if r.status_code == 200 else None}; expected the stored {sha2} (section 8)",
          "medium")  # fmt: skip

    # a material change: version 3 waits for approval and has no document
    want = oracle.reasons(v1, oracle.version(3, t.WIRE, 100, "905.00", t.DEC19, supplier, t.LOC))
    check(bool(want), "P2: the oracle finds the 19 Dec date not material (precondition)", "medium")
    r = co.revise7(po, lines=[{"line_no": 1, "need_by": t.DEC19.isoformat()}], version=2)
    row = co.po_row(po)
    check(r.status_code == 200 and int(row.get("version") or 0) == 3 and row.get("status") == "submitted",
          f"P2: need-by 19 Dec answered {r.status_code} {r.text[:200]}; version {row.get('version')}, status "
          f"{row.get('status')}; expected 3, submitted")  # fmt: skip
    rows = co.po_versions(po)
    doc3 = rows[3].get("document_id", "missing") if 3 in rows else "no row"
    r3 = co.version_pdf(po, 3)
    m.case("document_for_unissued", "P2 version 3 before sending", (r3.status_code, doc3), (404, None), (200, 1))
    check(3 in rows and doc3 is None, f"P2: version 3's row {rows.get(3, {}).get('document_id', 'no row')}; expected a "
          "row with no document (R6: never mailed)")  # fmt: skip
    t.refusal(check, r3, 404, "version 3 was not issued", "P2 versions/3/pdf before sending")
    r = co.pdf(po)
    draft = t.pdf_text(r.content if r.status_code == 200 else None)
    check(r.status_code == 200 and "draft: not issued" in t.norm(draft), f"P2: /pdf of version 3 answered "
          f"{r.status_code}; text {draft[:300]!r}; expected the title ending 'DRAFT: not issued' (R8, OP-12)")  # fmt: skip
    check(t.norm(NEW_TERMS) in t.norm(draft), "P2: the live draft of version 3 does not show the new terms, so the "
          "terms change cannot be told from a stored copy (precondition of the reprint test)", "medium")  # fmt: skip

    # approved and sent: version 3 is stored and equals its attachment
    k.approve_ok(check, co, who, po, 3, "P2 approve version 3")
    mark = co.audit_mark()
    l3, m3, _ = co.send_v(check, po, "P2 send version 3")
    _issued_ok(check, co, po, 3, m3, mark, "P2 v3")
    check(t.sha(_stored(check, co, po, 1, "P2 v1 at the end")) == sha1, "P2: version 1's document changed after version 3")
    m.report(check, out, ["reprint_renders_again", "document_for_unissued"])
    out.metrics["P2"] = {"order": po, "sha256 v1": sha1, "sha256 v2": sha2, "v2 total": str(v2.amounts["total"]),
                         "versions/3/pdf before sending": r3.status_code}  # fmt: skip
    return po, [l1, l2, l3]
