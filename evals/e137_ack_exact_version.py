"""E137: the supplier acknowledges the exact version (task 11b.3 version-aware acknowledgements, DF-07, spec 8.3,
8.8.7, K3), written without the implementation.

Independence: written from the block 8 spec (sections 4, 5 8.3 and 8.8.7, 8, 9 P and K3) and the dataset only. The
oracle (`block8.Oracle8`) works out P and its version 2 in exact fractions, the acknowledgement due days on PLT-CKN's
own calendar (Mon-Sat, 24 Nov 2026 closed) and which versions supplier scoring counts; section 9's figures and
`truth/truth_supplier_confirmation.csv` are checked against it.

Given a fresh Apex reset, P approved by U08 and sent on Mon 23 Nov (L1, due Thu 26 Nov):
- on Wed 25 Nov buyer@ cuts line 3 to 140 NOS, reason "plan cut": version 2 is taxable 1,17,200.00, CGST and SGST
  6,050.00, total 1,29,300.00 (line 3 25,200.00), not material (approval carried), and L2 is mailed to sales@.
- L1 shows "Order <P> is now version 2 (revised on 25 Nov 2026). This link was for version 1 and no longer works.
  Version 2 was emailed to s****@deccancastings.example on 25 Nov 2026: please use that email." and What changed; no
  code can be asked on it and accepting it writes nothing; Email me the current version mails a new version 2 link to
  sales@ (never shown on the page, audit `po.link_resend`); a second press within 10 minutes is 429.
- on Thu 26 Nov L2 with a code: `acknowledged_version` 2, the `po_version` 2 row via link with the address, link and
  code and its stored document; `/versions` shows the address, "with a code" and the line dates.
- supplier scoring of SUP-001 (as of Fri 27 Nov): version 1 (replaced on 25 Nov before its due day, unanswered) left out,
  version 2 (due Fri 27 Nov, acknowledged 26 Nov) counted on time: orders +1, acks +1, quick acks +1; after a version 3
  sent on 27 Nov (due Mon 30 Nov) the same counts stand.
Ends with a tenant check.
Mutations: the old link accepted for the new version; scoring from `po.acknowledged_at` (cleared by version 3).
"""

from typing import Any

import block1 as b
import block4 as k
import block8 as e
import env
from lib import Outcome, capture_mail

ID = "E137"
TITLE = "The supplier acknowledges the exact version; an older link says which version is current"
REQUIREMENT = (
    "Task 11b.3, DF-07 (block 8 spec 8.3, 8.8.7, K3): the acknowledgement lands on the version's po_version row with "
    "the address, link, code and stored document; an older version's link shows the spec's words and re-emails the "
    "current version (never on screen, once per 10 minutes) and accepts nothing; version 2 figures = the oracle and "
    "the truth file; supplier scoring counts version 2 on time and leaves version 1 out; tenant check"
)
PREFIX = "e137"


def run(ctx: Any) -> Outcome:
    env.forget_tenants(PREFIX)
    try:
        return _run(ctx)
    finally:
        e.pin(e.TODAY)
        env.forget_tenants(PREFIX)


def _run(ctx: Any) -> Outcome:
    from fastapi.testclient import TestClient

    from p2p.main import app
    from p2p.platform import mail

    out = Outcome(True)
    check = b.Checker(out)
    saved = mail.send_email
    box = capture_mail()
    notes: list[str] = []
    try:
        client = TestClient(app)
        bw = e.Co8("brightwave", PREFIX, client, box)
        apex = e.Co8("apex", PREFIX, client, box)
        e.start(apex)
        start = apex.mark()
        po, tokens = _k3(out, check, apex, notes)
        e.pin(e.TODAY)
        e.tenant_check8(check, apex, bw, [po], tokens, start)
    finally:
        mail.send_email = saved  # type: ignore[assignment]
    out.metrics.update({"checks": check.done, "failed": len(check.failed)})
    out.notes += sorted(set(notes)) + [
        ("A pinned business date is a date, not a clock: where the platform stamped the machine's clock on a version's "
         "made_at, sent_at or acknowledged_at, the eval puts the stamp on the scenario's day as the owner (10:00 IST)."),
        ("Supplier scoring is read from suppliers/scoring.score_all (as E23 reads it); the counts are compared as the "
         "change from before P was made, so the history orders of SUP-001 do not matter."),
        ("Spec gap: 'What changed' is checked by its heading and the new quantity 140 only; the block 7 comparison "
         "decides the rest."),
    ]
    return out


def _counts_delta(now: dict[str, Any], base: dict[str, Any]) -> dict[str, Any]:
    return {x: (int(now[x]) - int(base[x])) if now.get(x) is not None and base.get(x) is not None else None
            for x in ("orders", "acks", "quick_acks")}  # fmt: skip


def _k3(out: Outcome, check: b.Checker, co: e.Co8, notes: list[str]) -> tuple[str, list[str | None]]:
    oracle = e.Oracle8(co)
    m = k.Mutations()
    try:
        base = co.ack_counts(e.SUP, e.NOV27)
    except Exception as exc:  # noqa: BLE001 - scoring not reachable: a finding, the rest still runs
        base = {}
        check(False, f"K3: supplier scoring could not be read: {type(exc).__name__}: {str(exc)[:150]}", "medium")
    v1, v2, v3 = oracle.p(), oracle.p(140), oracle.p(130)
    rule, who = oracle.approver(v1["total"], frozenset({co.buyer()}))
    due1, due2 = oracle.ack_due(e.TODAY), oracle.ack_due(e.NOV25)
    e.self_check(check, "P", {**oracle.figures(v1), "rule": rule, "approver": who})
    e.self_check(check, "K3", {**oracle.figures(v2, "v2_"), "v1_due": due1.isoformat(), "v2_due": due2.isoformat()})
    po = co.p_order(check, "P", who, notes)
    if not po:
        return "", []
    e.amounts_ok(check, co, po, v1, "P v1")
    l1, _m1 = co.send8(check, po, "P send")
    l1_id = (co.respond_links(po) or [{}])[-1].get("id")
    if co.place(po, 1, "sent_at", e.TODAY):
        notes.append("version 1's sent_at was put on 23 Nov")

    # Wed 25 Nov: line 3 cut to 140, the approval carries, L2 mailed
    e.pin(e.NOV25)
    lm, since = co.link_mark(), co.mark()
    r = co.revise8(po, 3, qty=140, version=1, reason="plan cut")
    row = co.po_row(po)
    check(r.status_code == 200 and int(row.get("version") or 0) == 2 and row.get("status") == "sent"
          and row.get("approved_version") == 1, f"K3: cutting line 3 to 140 answered {r.status_code} {r.text[:150]}; "
          f"version {row.get('version')}, status {row.get('status')}, approved_version {row.get('approved_version')}; "
          "expected 2, sent, 1 (not material)")  # fmt: skip
    e.amounts_ok(check, co, po, v2, "K3 v2")
    mail2 = co.last_order_mail(po, since)
    l2 = co.token_in(mail2) if mail2 else None
    new = co.respond_links(po, lm)
    check(l2 is not None and [int(x.get("po_version") or 0) for x in new] == [2] and str(mail2.get("to", "")).lower()
          == e.SALES, f"K3: version 2 mailed to {mail2.get('to')} with link {l2}, links {[x.get('po_version') for x in new]}"
          f"; expected one version 2 link to {e.SALES}")  # fmt: skip
    check(bool(new) and str(new[-1].get("sent_to") or "").lower() == e.SALES, f"K3: L2's sent_to "
          f"{new[-1].get('sent_to', 'missing') if new else None}; expected {e.SALES} (8.2.3)", "medium")  # fmt: skip
    for col in ("made_at", "sent_at"):
        if co.place(po, 2, col, e.NOV25):
            notes.append(f"version 2's {col} was put on 25 Nov")

    # L1: the stale page, no code, nothing accepted
    before = co.fp(po)
    pg = co.supplier_page(l1 or "")
    words = e.stale_text(po, 2, e.NOV25, 1, e.SALES, e.NOV25)
    e.refused(check, pg, 409, words, "K3 L1's page after version 2", "high")
    check(e.says(pg, "what changed") and "140" in e.page(pg), f"K3: L1's page lacks 'What changed' with line 3's 140: "
          f"{e.page(pg)[:300]}", "medium")  # fmt: skip
    check(e.SALES not in e.page(pg), "K3: the stale page shows the full address (spec 8.3.2: masked)", "medium")
    r, code, mails = co.ask_code(l1)
    check(r.status_code == 409 and not mails and not co.codes(f"{po}:{l1_id}"), f"K3: Email me a code on L1 answered "
          f"{r.status_code} with {len(mails)} code mail(s); no code is asked on a stale link (OP-9)")  # fmt: skip
    r = co.answer(l1, "accept", code or "123456", **co.date_fields({1: e.DEC7, 2: e.DEC7, 3: e.DEC14}))
    m.case("old_link_accepted", "K3 L1 accepted after version 2", (r.status_code, co.po_row(po).get("acknowledged_version")),
           (409, None), (200, 2))  # fmt: skip
    e.refused(check, r, 409, None, "K3 L1 accepted after version 2")
    e.unchanged(check, co, po, before, "K3 L1 after version 2")

    # Email me the current version: mailed to the contact, never on the page; once per 10 minutes
    lm, since, mark = co.link_mark(), co.mark(), co.audit_mark()
    r = co.link_post(f"/supplier/{l1}/current")
    co.work()
    sent = [x for x in co.mails_to(e.SALES, since) if "/supplier/" in str(x.get("body") or "")]
    l3 = co.token_in(sent[-1]) if sent else None
    new = co.respond_links(po, lm)
    check(r.status_code == 200 and e.says(r, f"We have emailed version 2 to {e.masked(e.SALES)}."),
          f"K3: Email me the current version answered {r.status_code}: {e.page(r)[:200]}")  # fmt: skip
    check(len(sent) == 1 and [int(x.get("po_version") or 0) for x in new] == [2] and l3 is not None,
          f"K3: the re-email sent {len(sent)} mail(s) to {e.SALES}, links {[x.get('po_version') for x in new]}; expected "
          "one with a new version 2 link")  # fmt: skip
    check(l3 is None or l3 not in r.text, "K3: the new link is shown on the page (spec 8.3.2: mailed only, OP-9)")
    resend = co.since("po.link_resend", mark)
    check(bool(resend) and any("from_link" in a["after"] for a in resend), f"K3: po.link_resend audits "
          f"{[a['after'] for a in resend]}; expected one with from_link (8.3.2)", "medium")  # fmt: skip
    since = co.mark()
    r = co.link_post(f"/supplier/{l1}/current")
    co.work()
    again = [x for x in co.mails_to(e.SALES, since) if "/supplier/" in str(x.get("body") or "")]
    check(r.status_code == 429 and not again, f"K3: a second Email me the current version within 10 minutes answered "
          f"{r.status_code} and sent {len(again)} mail(s); the spec says 429 and none")  # fmt: skip

    # Thu 26 Nov: L2 with a code acknowledges version 2
    e.pin(e.NOV26)
    mark = co.audit_mark()
    r = co.accept_code(check, l2, {1: e.DEC7, 2: e.DEC7, 3: e.DEC14}, "K3 L2")
    check(r.status_code == 200, f"K3: L2 accepted with its code answered {r.status_code} {e.page(r)[:200]}")
    row2 = e.ack_ok(check, co, po, 2, "link", "K3 L2", address=e.SALES, code=True)
    check(row2.get("document_id") is not None, f"K3: po_version 2 document_id {row2.get('document_id')}; the "
          "acknowledgement points at the stored document accepted (8.3.1, B7-5)")  # fmt: skip
    check(co.po_versions(po).get(1, {}).get("acknowledged_at") is None, "K3: version 1 shows an acknowledgement it "
          "never had")  # fmt: skip
    acks = co.audits("po.acknowledge", po, mark)
    check(len(acks) == 1 and int(acks[0]["after"].get("version") or 0) == 2 and acks[0]["after"].get("via") == "link",
          f"K3: po.acknowledge audits {[a['after'] for a in acks]}; expected one, version 2, via link", "medium")  # fmt: skip
    status, listing = co.versions_api(po)
    lv2 = e.t.version_row(listing, 2)
    lines = {str(x): e.iso(y) for x, y in (k.as_json(lv2.get("ack_lines")) or {}).items()} if isinstance(
        k.as_json(lv2.get("ack_lines")), dict) else {}  # fmt: skip
    check(status == 200 and str(lv2.get("ack_address") or "").lower() == e.SALES and lv2.get("ack_with_code") is True
          and lines == {"1": "2026-12-07", "2": "2026-12-07", "3": "2026-12-14"},
          f"K3: /versions row 2 ack_address {lv2.get('ack_address', 'missing')}, ack_with_code "
          f"{lv2.get('ack_with_code', 'missing')}, ack_lines {lv2.get('ack_lines', 'missing')}; expected {e.SALES}, true, "
          "7, 7, 14 Dec (section 8)", "medium")  # fmt: skip
    if co.place(po, 2, "acknowledged_at", e.NOV26):
        notes.append("version 2's acknowledged_at was put on 26 Nov")

    # supplier scoring as of Fri 27 Nov: version 1 left out, version 2 on time
    want = oracle.ack_score([(e.TODAY, None), (e.NOV25, e.NOV26)], e.NOV27)
    check(want == (1, 1), f"K3: the oracle counts {want} for versions 1 and 2 (precondition (1, 1))", "medium")
    got = _counts_delta(_safe_counts(check, co), base)
    check(got == {"orders": 1, "acks": want[0], "quick_acks": want[1]}, f"K3: supplier scoring of {e.SUP} changed by "
          f"{got}; the oracle: orders +1, acks +{want[0]}, quick acks +{want[1]} (v1 left out, v2 on time)")  # fmt: skip

    # Fri 27 Nov: version 3 (not due yet) must not hide version 2's answer
    e.pin(e.NOV27)
    r = co.revise8(po, 3, qty=130, version=2, reason="plan cut again")
    check(r.status_code == 200 and int(co.po_row(po).get("version") or 0) == 3, f"K3: cutting line 3 to 130 answered "
          f"{r.status_code} {r.text[:150]}", "medium")  # fmt: skip
    e.amounts_ok(check, co, po, v3, "K3 v3", "medium")
    for col in ("made_at", "sent_at"):
        co.place(po, 3, col, e.NOV27)
    want3 = oracle.ack_score([(e.TODAY, None), (e.NOV25, e.NOV26), (e.NOV27, None)], e.NOV27)
    got3 = _counts_delta(_safe_counts(check, co), base)
    m.case("scoring_from_po_acknowledged_at", "K3 scoring after version 3 (po.acknowledged_at cleared)",
           got3.get("acks"), want3[0], 0)  # fmt: skip
    check(got3 == {"orders": 1, "acks": want3[0], "quick_acks": want3[1]}, f"K3: after version 3 supplier scoring of "
          f"{e.SUP} changed by {got3}; the oracle: orders +1, acks +{want3[0]}, quick acks +{want3[1]} (8.8.7: counted "
          "per sent version from po_version, not po.acknowledged_at)")  # fmt: skip

    mine = {**oracle.figures(v1), "rule": rule, "approver": who}
    e.compare_truth(check, co, "P", mine)
    e.compare_truth(check, co, "K3", {**oracle.figures(v2, "v2_"), "v1_due": due1, "v2_due": due2,
                                      "v1_sent": e.TODAY, "v2_sent": e.NOV25, "v2_acknowledged": e.NOV26})  # fmt: skip
    m.report(check, out, ["old_link_accepted", "scoring_from_po_acknowledged_at"])
    out.metrics["K3"] = {"order": po, "v2 total": str(v2["total"]), "v1 due": str(due1), "v2 due": str(due2),
                         "scoring change": got, "after v3": got3}  # fmt: skip
    return po, [l1, l2, l3]


def _safe_counts(check: b.Checker, co: e.Co8) -> dict[str, Any]:
    try:
        return co.ack_counts(e.SUP, e.NOV27)
    except Exception as exc:  # noqa: BLE001
        check(False, f"K3: supplier scoring could not be read: {type(exc).__name__}: {str(exc)[:150]}", "medium")
        return {}

