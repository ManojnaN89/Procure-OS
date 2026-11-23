"""E132: a change with no material reason carries the approval; a touchless order a person changes is no longer
touchless (Q19 "anything else", Rev s5, spec 7.6), written without the implementation.

Independence: written from the block 7 spec (sections 4, 5.6 R18, 5.7 R21-R22, 7 P6, 8, 9.2, OP-2 a interim: a
delivery-terms change is not material) and the dataset only. The oracle (`block7.Oracle7`) gives the amounts and the
material reasons from the last approved version (none for delivery terms), the band of the touchless order
(`approval_rule.csv`: PO-01 automatic to 1,00,000.00) and the R22 note in the spec's words with the buyer's name from
`user.csv` and the issue date.

P6 (a) on a fresh Apex reset (Mon 23 Nov 2026): Q(120 kg, 905.00) approved by U08, sent (L1), accepted. The buyer sets
the delivery terms to "ex_works":
- version 2, not material: `approved_version` 1, no new approval step, the history says "approval of version 1
  carries", `acknowledged_version` empty, status `sent`; version 2 stored and mailed with a new link L2 (`po_version`
  2) that accepts; its PDF reads "Delivery terms: ex_works"; total still 1,28,148.00.
P6 (b): K(RM-05, 10) is touchless, approved by `rule:PO-01` and sent by the system. The buyer sets the delivery terms to
"ex_works":
- version 2 with no approval step at all; `po_approved_version` only version 1 by `rule:PO-01`; `touchless` false with
  the note "Changed by <buyer> after it was issued on 23 Nov 2026: no longer touchless (Rev s5). The approval by rule
  PO-01 of version 1 carries: nothing material changed."; audit `po.touchless_lost`; mailed again with a new link.
Ends with a tenant check.
Mutations: delivery terms treated as material; the touchless label kept after a person's change.
"""

from typing import Any

import block1 as b
import block4 as k
import block7 as t
import env
from block1 import js
from lib import Outcome, capture_mail

ID = "E132"
TITLE = "A minor change carries the approval and needs only a new acknowledgement; touchless is lost"
REQUIREMENT = (
    "Q19, Rev s5, OP-2 interim, OP-16 (block 7 spec R21, R22, P6): a delivery-terms change is a new version with the "
    "approval carried, no new round, the acknowledgement voided and the version stored and mailed with a new link; on a "
    "touchless order it also sets touchless false with the R22 note and audit po.touchless_lost; tenant check"
)
PREFIX = "e132"
TERMS = "ex_works"


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
        po_a, tok_a = _p6a(out, check, apex, m)
        po_b, tok_b = _p6b(out, check, apex, m)
        m.report(check, out, ["delivery_terms_material", "touchless_label_kept"])
        t.tenant_check7(check, apex, bw, [po_a, po_b], tok_a + tok_b, start)
    finally:
        mail.send_email = saved  # type: ignore[assignment]
    out.metrics.update({"checks": check.done, "failed": len(check.failed)})
    out.notes += [
        ("OP-2 is NEEDS OWNER; the eval uses the interim (a): a delivery-terms change is not material. If the owner "
         "picks (b), P6 (a)'s checks of status and approval change."),
        ("'History says approval of version 1 carries' is looked for in the /controls history entry of version 2 or "
         "the po_revision row; /versions approval 'carried' is checked too."),
        "The R22 note is compared word for word after folding case and spacing, with the buyer's name from user.csv.",
    ]
    return out


def _minor_common(check: b.Checker, co: t.Co7, no: str, since: int, lm: int, label: str) -> str | None:
    """Version 2 of a minor change after sending: approval carried, no new round, the acknowledgement voided, stored and
    mailed with a new link for version 2."""
    row = co.po_row(no)
    check(int(row.get("version") or 0) == 2 and row.get("delivery_terms") == TERMS, f"{label}: version "
          f"{row.get('version')}, delivery_terms {row.get('delivery_terms')}; expected 2, {TERMS}")  # fmt: skip
    check(row.get("status") == "sent" and row.get("approved_version") == 1 and "acknowledged_version" in row
          and row.get("acknowledged_version") is None and row.get("acknowledged_at") is None,
          f"{label}: status {row.get('status')}, approved_version {row.get('approved_version')}, acknowledged_version "
          f"{row.get('acknowledged_version', 'missing')}, acknowledged_at {row.get('acknowledged_at')}; expected sent, "
          "1, empty, empty (R21)")  # fmt: skip
    rev = co.revisions(no).get(2, {})
    check(bool(rev) and not k.as_list(rev.get("material")) and rev.get("reapproval") is not True,
          f"{label}: po_revision 2 material {rev.get('material')}, reapproval {rev.get('reapproval')}; expected none "
          "(OP-2 interim)")  # fmt: skip
    check(any(e.get("header") == "delivery_terms" for e in k.as_json(rev.get("changes")) or [] if isinstance(e, dict)),
          f"{label}: po_revision 2 changes {rev.get('changes')}; expected a header entry for delivery_terms (R17)",
          "medium")  # fmt: skip
    check(not co.open_steps(no), f"{label}: open approval steps {co.open_steps(no)}; a minor change needs no new round")
    status, listing = co.versions_api(no)
    check(t.version_row(listing, 2).get("approval") == "carried", f"{label}: /versions approval of version 2 "
          f"{t.version_row(listing, 2).get('approval')!r} ({status}); expected 'carried'", "medium")  # fmt: skip
    row2 = co.po_versions(no).get(2, {})
    check(bool(row2.get("document_id")) and row2.get("sent_at") is not None, f"{label}: version 2 row document "
          f"{row2.get('document_id')}, sent_at {row2.get('sent_at')}; expected stored and sent (R6, R21)")  # fmt: skip
    mail = co.last_order_mail(no, since)
    token = co.token_in(mail) if mail else None
    check(token is not None and t.link_versions(co, no, lm) == [2], f"{label}: the version 2 mail's link {token}, link "
          f"versions {t.link_versions(co, no, lm)}; expected one new link for version 2 (R24)")  # fmt: skip
    pdfs = t.attached_pdfs(mail)
    r = co.version_pdf(no, 2)
    check(len(pdfs) == 1 and r.status_code == 200 and t.sha(r.content) == t.sha(pdfs[0]),
          f"{label}: the version 2 mail's PDF ({len(pdfs)}) and versions/2/pdf ({r.status_code}) differ", "medium")  # fmt: skip
    text_ = t.pdf_text(r.content if r.status_code == 200 else (pdfs[0] if pdfs else None))
    check(t.norm(f"Delivery terms: {TERMS}") in t.norm(text_), f"{label}: the version 2 PDF lacks 'Delivery terms: "
          f"{TERMS}': {text_[:300]!r}", "medium")  # fmt: skip
    return token


def _p6a(out: Outcome, check: b.Checker, co: t.Co7, m: k.Mutations) -> tuple[str, list[str | None]]:
    oracle = t.Oracle7(co)
    po = co.q_order(check, "P6a")
    if not po:
        return "", []
    supplier = str(co.po_row(po).get("supplier_code"))
    v1 = oracle.version(1, t.WIRE, 120, "905.00", t.NEED_BY, supplier, t.LOC)
    rule, who = oracle.approver(v1.amounts["total"], frozenset({co.buyer()}))
    t.self_check(check, "P1v1", t.figures(v1, rule, who))
    k.approve_ok(check, co, who, po, 1, "P6a approve version 1")
    l1, _m1, _ = co.send_v(check, po, "P6a send")
    r = co.accept(l1)
    check(co.po_row(po).get("status") == "acknowledged", f"P6a: L1 accept answered {r.status_code} (precondition)", "medium")
    steps0 = len(co.steps(po))
    since, lm = co.mark(), co.link_mark()
    r = co.revise7(po, header={"delivery_terms": TERMS}, version=1, reason="supplier collects from its works")
    check(r.status_code == 200, f"P6a: setting delivery terms {TERMS} answered {r.status_code} {r.text[:200]}")
    m.case("delivery_terms_material", "P6a delivery terms after acknowledgement", co.po_row(po).get("status"), "sent",
           "submitted")  # fmt: skip
    check(r.status_code != 200 or js(r).get("header_changes") not in (None, [], {}), f"P6a: the revise response lacks "
          f"header_changes: {r.text[:200]}", "medium")  # fmt: skip
    l2 = _minor_common(check, co, po, since, lm, "P6a v2")
    check(len(co.steps(po)) == steps0, f"P6a: approval steps {steps0} -> {len(co.steps(po))}; no new round")
    t.amounts_ok(check, co, po, v1, "P6a v2 (total unchanged)")
    hist = co.history(po, co.buyer()).get(2, {})
    words = "approval of version 1 carries"
    check(t.norm(words) in t.norm(json_text(hist)) or t.norm(words) in t.norm(json_text(co.revisions(po).get(2, {}))),
          f"P6a: the history of version 2 does not say '{words}': {json_text(hist)[:300]}", "medium")  # fmt: skip
    r = co.accept(l2)
    check(r.status_code == 200 and co.po_row(po).get("acknowledged_version") == 2, f"P6a: L2 accept answered "
          f"{r.status_code}; acknowledged_version {co.po_row(po).get('acknowledged_version')}")  # fmt: skip
    out.metrics["P6a"] = {"order": po, "status after": co.po_row(po).get("status"), "total": str(co.po_row(po).get("total"))}
    return po, [l1, l2]


def json_text(x: Any) -> str:
    import json

    return json.dumps(x, default=str)


def _p6b(out: Outcome, check: b.Checker, co: t.Co7, m: k.Mutations) -> tuple[str, list[str | None]]:
    oracle = t.Oracle7(co)
    since0 = co.mark()
    po = co.k_order(check, t.SHAFT, 10, t.NEED_BY, "P6b")
    if not po:
        return "", []
    row = co.po_row(po)
    v1 = oracle.version(1, t.SHAFT, 10, "180.00", t.NEED_BY, "SUP-001", t.LOC, terms="NET30")
    band = oracle.band(v1.amounts["total"])["rule_code"]
    t.self_check(check, "P5v1", {**t.figures(v1), "rule": band})
    check(row.get("touchless") is True and row.get("status") in ("sent", "acknowledged"), f"P6b: precondition: "
          f"touchless {row.get('touchless')}, status {row.get('status')}; expected a touchless order sent")  # fmt: skip
    mail1 = co.last_order_mail(po, since0)
    l1 = co.token_in(mail1) if mail1 else None
    mark = co.audit_mark()
    since, lm = co.mark(), co.link_mark()
    r = co.revise7(po, header={"delivery_terms": TERMS}, version=1, reason="supplier collects from its works")
    row = co.po_row(po)
    check(r.status_code == 200, f"P6b: setting delivery terms {TERMS} answered {r.status_code} {r.text[:200]}")
    m.case("touchless_label_kept", "P6b delivery terms on a touchless order", row.get("touchless"), False, True)
    check(row.get("touchless") is False, f"P6b: touchless {row.get('touchless')} after the buyer's change; Rev s5 says "
          "no longer touchless (R22)")  # fmt: skip
    buyer_name = co.name_of(co.buyer())
    note = (f"Changed by {buyer_name} after it was issued on {t.long_date(t.TODAY)}: no longer touchless (Rev s5). The "
            f"approval by rule {band} of version 1 carries: nothing material changed.")  # fmt: skip
    check(t.norm(row.get("touchless_note")) == t.norm(note), f"P6b: touchless_note {row.get('touchless_note')!r}; the "
          f"spec says {note!r}", "medium")  # fmt: skip
    check(bool(co.audits("po.touchless_lost", po, mark)), "P6b: no po.touchless_lost audit row", "medium")
    check(not co.steps(po), f"P6b: approval steps {[(s.get('approver'), s.get('status')) for s in co.steps(po)]}; a "
          "minor change of a rule-approved order makes none")  # fmt: skip
    snaps = co.approved_versions(po)
    check([(int(x.get("version") or 0), str(x.get("approved_by"))) for x in snaps] == [(1, f"rule:{band}")],
          f"P6b: po_approved_version {[(x.get('version'), x.get('approved_by')) for x in snaps]}; expected only "
          f"(1, rule:{band})")  # fmt: skip
    l2 = _minor_common(check, co, po, since, lm, "P6b v2")
    t.amounts_ok(check, co, po, v1, "P6b v2 (total unchanged)", "medium")
    out.metrics["P6b"] = {"order": po, "touchless": row.get("touchless"), "note": row.get("touchless_note")}
    return po, [l1, l2]
