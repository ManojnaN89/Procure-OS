"""E136: a forwarded link decides nothing (task 1.3 second half, D53, spec 8.1-8.2, K1, K2), written without the
implementation.

Independence: written from the block 8 spec (sections 5 8.1-8.2, 8, 9 P, Q, K1, K2) and the dataset only. The oracle
is P's amounts and approver in exact fractions (`block8.Oracle8`, section 9's figures as a self-check), the registered
contacts of `supplier_contact.csv` (the code goes only to the address the link's email went to, the primary
sales@deccancastings.example, masked s****@deccancastings.example) and the `otp` words the spec quotes.

Given a fresh Apex reset on Mon 23 Nov 2026 with SUP-001's second contact orders@deccancastings.example (spec 3.1),
P (RM-02 100 / RM-03 200 need-by 7 Dec, RM-05 150 need-by 14 Dec on CNT-0001: taxable 1,19,000.00, CGST and SGST
6,095.00, total 1,31,190.00, PO-02, U08) approved and sent to sales@ (link L1), and Q (SP-01 20 to SUP-006) sent:
- L1's page (opened by anyone) shows the order, s****@deccancastings.example and Email me a code; opening it changes
  nothing.
- accept with no code, accept with 123456 before any code, decline and ask for a change with no code: 409, "this needs
  a one-time code", nothing written (status sent, acknowledged_version empty, the link unused, no change, no audit).
- Email me a code: "A code was sent to s****@deccancastings.example. It works once and expires in 10 minutes.";
  exactly one code mail, to sales@ (none to orders@ or anyone else); a second ask within 30 s refused, no mail.
- a guessed code: "wrong code; 4 tries left"; three more; the fifth: "wrong code 5 times"; then the right code refused;
  `otp.wrong` audited with actor contact:sales@...; nothing written.
- a new code (the eval moves `created_at` back), `expires_at` moved back 11 minutes: "the code has expired; ask for a
  new one", `otp.expired`; nothing written.
- a fresh code typed on Q's link: refused (a code works only on its own link), Q unchanged; the same code on L1:
  `acknowledged_version` 1, `ack_via` link, `ack_address` sales@, the code `used`, audit `po.acknowledge` with version,
  via, by and code; L1 again: 409 "already been used".
Ends with a tenant check.
Mutations: the code skipped on decline; the code sent to every contact.
"""

from typing import Any

import block1 as b
import block4 as k
import block8 as e
import env
from lib import Outcome, capture_mail

ID = "E136"
TITLE = "A forwarded link decides nothing: accept, change and decline need a code mailed to the registered contact"
REQUIREMENT = (
    "Task 1.3, D53 (block 8 spec 8.1-8.2, K1, K2): the supplier's answer needs the link and a one-time code from "
    "Email me a code, sent only to the address the link's email went to (masked on the page); no answer without the "
    "right code, nothing written by a refusal; 5 tries then locked, 10 minutes, 30 s between codes, a code per link; "
    "the accepted answer records the version, the address and the code; tenant check"
)
PREFIX = "e136"
NEEDS_CODE = "this needs a one-time code"


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
        e.data_gaps(check, apex, contacts=True)
        apex.ensure_second_contact(notes)
        orders, tokens = _k1_k2(out, check, apex, notes)
        e.tenant_check8(check, apex, bw, orders, tokens, start)
    finally:
        mail.send_email = saved  # type: ignore[assignment]
    out.metrics.update({"checks": check.done, "failed": len(check.failed)})
    out.notes += notes + [
        "Q is made on 23 Nov (the spec sends it on 26 Nov; here it only needs a link of its own).",
        ("Spec gap: the words after 'wrong code 5 times:' are not given; the eval checks the start. 'n tries left' is "
         "checked for 4 (the spec's words) and the count for the others."),
        ("The one-time code is read from the captured mail to the contact (a mail with 'code', six digits and no "
         "link: an order mail carries a pincode)."),
        "Refusal statuses: section 8 gives 409 for a missing or wrong code (form again); any of them passes as 409.",
    ]
    return out


def _nothing(check: b.Checker, co: e.Co8, po: str, before: tuple[Any, ...], label: str) -> None:
    e.unchanged(check, co, po, before, label)
    e.not_acked(check, co, po, label)


def _k1_k2(out: Outcome, check: b.Checker, co: e.Co8, notes: list[str]) -> tuple[list[str], list[str | None]]:
    oracle = e.Oracle8(co)
    m = k.Mutations()
    want = oracle.p()
    rule, who = oracle.approver(want["total"], frozenset({co.buyer()}))
    e.self_check(check, "P", {**oracle.figures(want), "rule": rule, "approver": who})
    po = co.p_order(check, "P", who, notes)
    if not po:
        return [], []
    e.amounts_ok(check, co, po, want, "P v1", "medium")
    l1, mail1 = co.send8(check, po, "P send")
    check(str(mail1.get("to", "")).lower() == e.SALES, f"P: the order mail went to {mail1.get('to')}; the primary "
          f"contact is {e.SALES}", "medium")  # fmt: skip
    links = co.respond_links(po)
    check(bool(links) and str(links[-1].get("sent_to") or "").lower() == e.SALES, f"P: supplier_link.sent_to "
          f"{[x.get('sent_to', 'missing') for x in links]}; expected {e.SALES} (spec 8.2.3)", "medium")  # fmt: skip
    link_id = links[-1]["id"] if links else 0
    ref = f"{po}:{link_id}"
    q, lq, _mq = co.q_order(check, "Q")

    # K1: the page, then answers with no code
    before = co.fp(po)
    pg = co.supplier_page(l1 or "")
    check(pg.status_code == 200 and e.says(pg, e.masked(e.SALES)) and e.says(pg, "email me a code") and e.says(pg, po),
          f"K1: L1's page answered {pg.status_code}; it should show {po}, {e.masked(e.SALES)} and 'Email me a code': "
          f"{e.page(pg)[:300]}")  # fmt: skip
    check(e.SALES not in e.page(pg), "K1: the page shows the contact's full address (spec 8.2.2: masked)", "medium")
    e.unchanged(check, co, po, before, "K1 opening the page")
    mark = co.audit_mark()
    for label, action, code, fields in (
        ("K1 accept with no code", "accept", None, co.date_fields({n: nb for n, _i, _q, nb in e.P_LINES})),
        ("K1 accept with 123456 before any code", "accept", "123456", co.date_fields({n: nb for n, _i, _q, nb in e.P_LINES})),
        ("K1 change with no code", "change", None, {"date_1": e.DEC8.isoformat(), "reason": "die repair"}),
    ):  # fmt: skip
        r = co.answer(l1, action, code, **fields)
        e.refused(check, r, 409, NEEDS_CODE if code is None else None, label)
        _nothing(check, co, po, before, label)
    r = co.answer(l1, "decline", None, reason="we cannot make it")
    m.case("code_skipped_on_decline", "K1 decline with no code", (r.status_code, co.po_row(po).get("status")),
           (409, "sent"), (200, "cancelled"))  # fmt: skip
    e.refused(check, r, 409, NEEDS_CODE, "K1 decline with no code")
    check(co.po_row(po).get("status") == "sent", f"K1: a decline with no code left {po} {co.po_row(po).get('status')}")
    _nothing(check, co, po, before, "K1 decline with no code")
    check(not co.audits("po.acknowledge", po, mark), "K1: a refused answer wrote a po.acknowledge audit row")

    # Email me a code: one mail, to the address the link's email went to
    r, code1, mails = co.ask_code(l1)
    to = sorted(str(x.get("to", "")).lower() for x in mails)
    m.case("code_to_every_contact", "K1 Email me a code with two contacts on file", to, [e.SALES],
           sorted([e.SALES, e.ORDERS]))  # fmt: skip
    check(r.status_code == 200 and to == [e.SALES] and code1 is not None, f"K1: Email me a code answered {r.status_code}"
          f"; code mails to {to}; the spec says exactly one, to {e.SALES}")  # fmt: skip
    check(e.says(r, f"A code was sent to {e.masked(e.SALES)}. It works once and expires in 10 minutes."),
          f"K1: the page after asking does not say the spec's words: {e.page(r)[:300]}", "medium")  # fmt: skip
    rows = co.codes(f"{po}%")
    check(len(rows) == 1 and rows[0].get("ref") == ref and str(rows[0].get("address") or "").lower() == e.SALES,
          f"K1: one_time_code rows {[(x.get('ref'), x.get('address'), x.get('status')) for x in rows]}; expected one, "
          f"purpose po_answer, ref {ref}, address {e.SALES} (spec 8.1.1)")  # fmt: skip
    e.unchanged(check, co, po, before, "K1 asking for a code")
    again, _code_again, mails_again = co.ask_code(l1)
    check(again.status_code == 409 and not mails_again, f"K1: a second Email me a code within 30 s answered "
          f"{again.status_code} with {len(mails_again)} mail(s); the spec says 409 and none (8.1.5)", "medium")  # fmt: skip

    # K2: wrong tries and the lock
    mark = co.audit_mark()
    wrong = "000000" if code1 != "000000" else "111111"
    r = co.answer(l1, "accept", wrong, **co.date_fields({n: nb for n, _i, _q, nb in e.P_LINES}))
    e.refused(check, r, 409, "wrong code; 4 tries left", "K2 the guessed code")
    left = []
    for _ in range(3):
        r = co.answer(l1, "accept", wrong, **co.date_fields({n: nb for n, _i, _q, nb in e.P_LINES}))
        left.append(r.status_code)
    check(left == [409, 409, 409], f"K2: wrong tries 2-4 answered {left}")
    r = co.answer(l1, "accept", wrong, **co.date_fields({n: nb for n, _i, _q, nb in e.P_LINES}))
    e.refused(check, r, 409, "wrong code 5 times", "K2 the fifth wrong code")
    r = co.answer(l1, "accept", code1, **co.date_fields({n: nb for n, _i, _q, nb in e.P_LINES}))
    e.refused(check, r, 409, None, "K2 the right code after the lock")
    _nothing(check, co, po, before, "K2 wrong tries and the lock")
    wrongs = co.since("otp.wrong", mark)
    actors = [f"{w.get('actor_type')}:{w.get('actor_id')}".lower() for w in wrongs]  # 'contact' as type or in the id
    check(len(wrongs) >= 4 and all("contact" in x and e.SALES in x for x in actors),
          f"K2: otp.wrong audits {[(w.get('actor_type'), w.get('actor_id')) for w in wrongs]}; expected one per wrong "
          f"try with actor contact:{e.SALES} (spec 8.1.2)", "medium")  # fmt: skip

    # a new code, then expired
    co.age_codes(f"{po}%", 1)
    r, code2, mails = co.ask_code(l1)
    check(r.status_code == 200 and code2 is not None, f"K2: a new code after the lock (a minute later) answered "
          f"{r.status_code} with {len(mails)} code mail(s)")  # fmt: skip
    co.age_codes(f"{po}%", 11, expire=True)
    mark = co.audit_mark()
    r = co.answer(l1, "accept", code2 or "000000", **co.date_fields({n: nb for n, _i, _q, nb in e.P_LINES}))
    e.refused(check, r, 409, "the code has expired; ask for a new one", "K2 the expired code")
    check(bool(co.since("otp.expired", mark)), "K2: the expired code wrote no otp.expired audit (spec 8.1.2)", "medium")
    _nothing(check, co, po, before, "K2 the expired code")

    # a code works only on its own link: typed on Q's link, then on L1
    co.age_codes(f"{po}%", 1)
    r, code3, _ = co.ask_code(l1)
    check(r.status_code == 200 and code3 is not None, f"K2: a fresh code answered {r.status_code}")
    if q and lq:
        q_before = co.fp(q)
        r = co.answer(lq, "accept", code3 or "000000", **co.date_fields({1: e.Q_NEED}))
        e.refused(check, r, 409, None, "K2 P's code typed on Q's link")
        e.unchanged(check, co, q, q_before, "K2 P's code on Q's link")
        e.not_acked(check, co, q, "K2 P's code on Q's link")
    else:
        check(False, "K2: Q has no respond link to try P's code on (precondition)", "medium")
    mark = co.audit_mark()
    r = co.answer(l1, "accept", code3 or "000000", **co.date_fields({n: nb for n, _i, _q, nb in e.P_LINES}))
    check(r.status_code == 200, f"K1: the contact's real code answered {r.status_code} {e.page(r)[:200]}")
    e.ack_ok(check, co, po, 1, "link", "K1 the real code", address=e.SALES, code=True)
    link = next((x for x in co.links(po) if x["id"] == link_id), {})
    check(link.get("used_at") is not None, f"K1: L1's used_at {link.get('used_at')} after the accepted answer (8.1.3)")
    used = [x for x in co.codes(f"{po}%") if x.get("status") == "used"]
    check(len(used) == 1, f"K1: codes of L1 {[(x.get('id'), x.get('status')) for x in co.codes(f'{po}%')]}; expected "
          "exactly the last one used (8.1.3)", "medium")  # fmt: skip
    acks = co.audits("po.acknowledge", po, mark)
    a = acks[0]["after"] if acks else {}
    check(len(acks) == 1 and int(a.get("version") or 0) == 1 and a.get("via") == "link" and bool(a.get("by"))
          and a.get("code") not in (None, "", False), f"K1: po.acknowledge audits {[x['after'] for x in acks]}; expected "
          "one with version 1, via link, by and code (spec 8.3.1)", "medium")  # fmt: skip
    before = co.fp(po)
    r = co.supplier_page(l1 or "")
    e.refused(check, r, 409, "already been used", "K1 L1 opened again")
    r = co.answer(l1, "accept", code3 or "000000", **co.date_fields({n: nb for n, _i, _q, nb in e.P_LINES}))
    e.refused(check, r, 409, None, "K1 L1 accepted again")
    e.unchanged(check, co, po, before, "K1 L1 again")
    m.report(check, out, ["code_skipped_on_decline", "code_to_every_contact"])
    out.metrics["P"] = {"order": po, "total": str(want["total"]), "approver": f"{rule} {who}", "masked": e.masked(e.SALES),
                        "code mails": to, "Q": q}  # fmt: skip
    return [po, q], [l1, lq]
