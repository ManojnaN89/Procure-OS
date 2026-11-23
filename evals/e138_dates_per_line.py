"""E138: delivery dates per line (task 11b.3 line-level dates, DF-27, spec 8.5, 8.6.1, K4), written without the
implementation.

Independence: written from the block 8 spec (sections 5 8.5-8.6, 8, 9 P and K4) and the dataset only. The oracle
(`block8.Oracle8`) writes each refusal from the spec's rule (before the business date, not a working day on PLT-CKN's
own calendar: Mon-Sat with 24 Nov and 25 Dec 2026 closed, more than `supplier_date_days` = 3 calendar days after the
date ordered) and walks the delivery reminders per line on that calendar with the data dictionary's settings (2
working days before; 1 after, then every 3, at most 3).

Given a fresh Apex reset on Mon 23 Nov 2026, P approved and sent (L1); the contact asks for a code:
- line 2 on Fri 11 Dec: "line 2: 11 Dec 2026 is 4 days after the date ordered (07 Dec 2026), more than the 3 accepted:
  ask for a change instead"; line 3 on Sun 13 Dec: "line 3: 13 Dec 2026 is not a working day at Plant, Chakan (Pune)";
  line 1 on 20 Nov: "line 1: 20 Nov 2026 is in the past"; each 409, nothing written, the code still open.
- line 1 as ordered (7 Dec), line 2 Wed 9 Dec, line 3 as ordered (14 Dec): promised 7, 9, 14 Dec line by line; three
  `po_commitment` rows of version 1 (source ack_link); the buyer is told line 2 is later than ordered.
- with nothing arriving, the reminders from 24 Nov to 17 Dec: before 4 Dec (line 1), 7 Dec (line 2), 11 Dec (line 3);
  overdue for 7 Dec on 8, 11, 15 Dec; for 9 Dec on 10, 14, 17 Dec; for 14 Dec on 15 Dec.
- the buyer's Record acknowledgement on a second P: no note 422; version 2 409; line 2 on 11 Dec 422 with the same
  words; then 7, 8, 14 Dec with a note: 200 {status, version 1, warnings}, `ack_via` app with the note, commitments
  source ack_app.
Ends with a tenant check.
Mutations: one date written to every line; the company Mon-Fri calendar (line 3's reminder on Thu 10 Dec).
"""

from datetime import date, timedelta
from typing import Any

import block1 as b
import block4 as k
import block8 as e
import env
from block1 import js
from lib import Outcome, capture_mail

ID = "E138"
TITLE = "Delivery dates per line: each line's own date, checked on the delivery place's calendar, reminded per line"
REQUIREMENT = (
    "Task 11b.3, DF-27 (block 8 spec 8.5, 8.6.1, K4): the accept form takes one date per line; past, non-working "
    "(ship-to calendar) and too-late dates refused in the spec's words with nothing written and the code still open; "
    "accepted dates line by line with a commitment each; the buyer's Record acknowledgement takes a date per line and a "
    "required note; delivery reminders per line on the PLT-CKN calendar = the oracle; tenant check"
)
PREFIX = "e138"
WALK = (e.TODAY + timedelta(days=1), date(2026, 12, 17))
ORDERED = {1: e.DEC7, 2: e.DEC7, 3: e.DEC14}
NOTE = "Confirmed on the phone by Mr Patil of Deccan Castings"


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
        oracle = e.Oracle8(apex)
        m = k.Mutations()
        _rule, who = oracle.approver(oracle.p()["total"], frozenset({apex.buyer()}))
        po, l1 = _k4(check, apex, oracle, m, who, notes)
        po2, l2 = _record(check, apex, oracle, who, notes)
        if po:
            _reminders(out, check, apex, oracle, m, po)
        m.report(check, out, ["one_date_every_line", "company_calendar"])
        e.pin(e.TODAY)
        e.tenant_check8(check, apex, bw, [po, po2], [l1, l2], start)
    finally:
        mail.send_email = saved  # type: ignore[assignment]
    out.metrics.update({"checks": check.done, "failed": len(check.failed)})
    out.notes += sorted(set(notes)) + [
        ("A line the supplier leaves alone is sent with its prefilled date (the date on the order), as a browser sends "
         "the form; the spec's 'blank' is read that way."),
        ("Delivery reminders are run day by day with orders/delivery_reminders.run (as E42 runs them); a reminder's day "
         "is the business day of the run that wrote it. Kinds are read as 'before' and 'overdue' by their words."),
        ("Spec gap: how the buyer is told that line 2 is later is not worded; the eval looks for 'line 2' in the "
         "buyer's notifications (medium)."),
    ]
    return out


def _k4(check: b.Checker, co: e.Co8, oracle: e.Oracle8, m: k.Mutations, who: str,
        notes: list[str]) -> tuple[str, str | None]:  # fmt: skip
    po = co.p_order(check, "P", who, notes)
    if not po:
        return "", None
    l1, _m1 = co.send8(check, po, "P send")
    r, code, _mails = co.ask_code(l1)
    check(r.status_code == 200 and code is not None, f"K4: Email me a code answered {r.status_code}")
    before = co.fp(po)
    for n, promised in ((2, e.DEC11), (3, date(2026, 12, 13)), (1, date(2026, 11, 20))):
        words = oracle.date_refusal(n, ORDERED[n], promised, e.TODAY)
        check(words is not None, f"K4: the oracle accepts line {n} on {promised} (precondition)", "medium")
        r = co.answer(l1, "accept", code or "000000", **co.date_fields({**ORDERED, n: promised}))
        e.refused(check, r, 409, words, f"K4 line {n} on {e.long_date(promised)}", "high")
        e.unchanged(check, co, po, before, f"K4 line {n} on {e.long_date(promised)}")
    opened = [x for x in co.codes(f"{po}%") if x.get("status") not in ("used", "locked", "expired")]
    check(len(opened) == 1, f"K4: after the refused dates the codes are {[(x.get('id'), x.get('status')) for x in co.codes(f'{po}%')]}"
          "; the spec says the code stays open (8.5.1)")  # fmt: skip

    # the accepted dates, line by line
    accepted = {1: e.DEC7, 2: e.DEC9, 3: e.DEC14}
    check(all(oracle.date_refusal(n, ORDERED[n], d, e.TODAY) is None for n, d in accepted.items()),
          "K4: the oracle refuses the accepted dates (precondition)", "medium")  # fmt: skip
    cm = co.commit_mark()
    r = co.answer(l1, "accept", code or "000000", note="K4: eval", **co.date_fields(accepted))
    check(r.status_code == 200, f"K4: accepting 7, 9 and 14 Dec answered {r.status_code} {e.page(r)[:200]}")
    e.ack_ok(check, co, po, 1, "link", "K4", address=e.SALES, code=True)
    got = co.promised(po)
    m.case("one_date_every_line", "K4 accepted 7, 9, 14 Dec", got, accepted, {1: e.DEC9, 2: e.DEC9, 3: e.DEC9})
    check(got == accepted, f"K4: promised dates {got}; the supplier gave {accepted} (spec 8.5.2, DF-27)")
    rows = co.commitments(po, cm)
    seen = sorted((int(x.get("po_line_no") or 0), b.day(x.get("promised_date")), x.get("po_version"), x.get("source"))
                  for x in rows)  # fmt: skip
    check(sorted((n, d) for n, d, _v, _s in seen) == sorted(accepted.items()),
          f"K4: po_commitment rows {seen}; expected one per line with its date", "high")  # fmt: skip
    check(all(v == 1 and s == "ack_link" for _n, _d, v, s in seen) and len(seen) == 3,
          f"K4: po_commitment rows {seen}; expected po_version 1 and source ack_link (section 7)", "medium")  # fmt: skip
    _status, listing = co.versions_api(po)
    lv1 = e.t.version_row(listing, 1)
    ack_lines = k.as_json(lv1.get("ack_lines"))
    lines = {str(x): e.iso(y) for x, y in ack_lines.items()} if isinstance(ack_lines, dict) else {}
    check(lines == {str(n): d.isoformat() for n, d in accepted.items()}, f"K4: /versions row 1 ack_lines "
          f"{lv1.get('ack_lines', 'missing')}; expected 7, 9, 14 Dec (section 8)", "medium")  # fmt: skip
    detail = co.detail(po, co.buyer())
    dl = {int(x.get("line_no") or 0): (e.iso(x.get("due")), x.get("due_source")) for x in detail.get("lines") or []
          if isinstance(x, dict)}  # fmt: skip
    check(dl == {n: (d.isoformat(), "promised") for n, d in accepted.items()}, f"K4: GET /api/purchase-orders/{po} "
          f"lines due/due_source {dl}; expected each line's promised date (section 8)", "medium")  # fmt: skip
    told = co.call(co.buyer(), "GET", "/api/notifications")
    check("line 2" in e.page(told) and po.lower() in e.page(told), f"K4: the buyer's notifications do not mention line "
          f"2 of {po} being later than ordered (8.5.2): {e.page(told)[:200]}", "medium")  # fmt: skip
    return po, l1


def _record(check: b.Checker, co: e.Co8, oracle: e.Oracle8, who: str, notes: list[str]) -> tuple[str, str | None]:
    """The buyer's Record acknowledgement (spec 8.5.4): version, a date per line, a required note."""
    po = co.p_order(check, "P2", who, notes)
    if not po:
        return "", None
    tok, _m = co.send8(check, po, "P2 send")
    before = co.fp(po)
    r = co.record_ack(po, 1, ORDERED, None)
    e.refused(check, r, 422, None, "K4 record with no note")
    r = co.record_ack(po, 2, ORDERED, NOTE)
    e.refused(check, r, 409, None, "K4 record of version 2 on version 1")
    words = oracle.date_refusal(2, e.DEC7, e.DEC11, e.TODAY)
    r = co.record_ack(po, 1, {**ORDERED, 2: e.DEC11}, NOTE)
    e.refused(check, r, 422, words, "K4 record with line 2 on 11 Dec")
    e.unchanged(check, co, po, before, "K4 refused records")
    cm = co.commit_mark()
    dates = {1: e.DEC7, 2: e.DEC8, 3: e.DEC14}
    r = co.record_ack(po, 1, dates, NOTE)
    body = js(r) if r.status_code == 200 else {}
    check(r.status_code == 200 and body.get("version") == 1 and "warnings" in body, f"K4: recording 7, 8, 14 Dec with a "
          f"note answered {r.status_code} {r.text[:200]}; expected 200 {{status, version 1, warnings}}")  # fmt: skip
    check("line 2" in e.blob(body.get("warnings")), f"K4: the record's warnings {body.get('warnings')} do not name line 2 "
          "(a day later than ordered, 8.5.2)", "low")  # fmt: skip
    row = e.ack_ok(check, co, po, 1, "app", "K4 record")
    check(row.get("ack_note") == NOTE, f"K4: po_version 1 ack_note {row.get('ack_note', 'missing')}; expected the "
          "buyer's note (8.5.4)", "medium")  # fmt: skip
    check(co.promised(po) == dates, f"K4: after the record the promised dates are {co.promised(po)}; expected {dates}")
    src = sorted((int(x.get("po_line_no") or 0), x.get("source")) for x in co.commitments(po, cm))
    check(src == [(1, "ack_app"), (2, "ack_app"), (3, "ack_app")], f"K4: the record's po_commitment rows {src}; "
          "expected one per line, source ack_app", "medium")  # fmt: skip
    return po, tok


def _kind(x: Any) -> str:
    s_ = str(x or "").lower()
    return "before" if "before" in s_ else "overdue"


def _reminders(out: Outcome, check: b.Checker, co: e.Co8, oracle: e.Oracle8, m: k.Mutations, po: str) -> None:
    """The reminder walk with nothing arriving, 24 Nov to 17 Dec, against the oracle on PLT-CKN's calendar."""
    seen: set[tuple[date, date, str]] = set()
    numbers: dict[tuple[date, date, str], Any] = {}
    errors = set()
    day = WALK[0]
    while day <= WALK[1]:
        known = {int(x["id"]) for x in co.qd("select id from delivery_reminder where po_no = :n", n=po)}
        err = co.delivery_reminders(day)
        if err:
            errors.add(err)
        for x in co.qd("select * from delivery_reminder where po_no = :n order by id", n=po):
            if int(x["id"]) not in known:
                key = (day, b.day(x.get("promised_date")) or date.min, _kind(x.get("kind")))
                seen.add(key)
                numbers[key] = x.get("number")
        day += timedelta(days=1)
    check(not errors, f"K4: the delivery reminder run failed: {sorted(errors)[:2]}")
    want = {(d, due, kind) for due in (e.DEC7, e.DEC9, e.DEC14) for d, kind, _n in oracle.reminders(due)
            if WALK[0] <= d <= WALK[1]}  # fmt: skip
    wrong = {(d, due, kind) for due in (e.DEC7, e.DEC9, e.DEC14) for d, kind, _n in oracle.reminders(due, oracle.company_cal())
             if WALK[0] <= d <= WALK[1]}  # fmt: skip
    line3 = sorted(d for d, due, kind in seen if due == e.DEC14 and kind == "before")
    m.case("company_calendar", "K4 line 3's reminder before 14 Dec", line3, [e.DEC11], [e.DEC10])
    check(seen == want, f"K4: reminders (day, due, kind) {sorted(seen)}; oracle {sorted(want)}; missing "
          f"{sorted(want - seen)}, extra {sorted(seen - want)} (spec 8.6.1: per line on the PLT-CKN calendar)")  # fmt: skip
    nums = {key: n for key, n in numbers.items() if key in want}
    want_n = {(d, due, kind): n for due in (e.DEC7, e.DEC9, e.DEC14) for d, kind, n in oracle.reminders(due)}
    off = [f"{key}: {n} (oracle {want_n.get(key)})" for key, n in nums.items() if str(n) != str(want_n.get(key))]
    check(not off, f"K4: reminder numbers {off}", "low")
    plan = {"before": [str(d) for d, due, kind in sorted(want) if kind == "before"],
            "overdue 9 Dec": [str(d) for d, due, kind in sorted(want) if kind == "overdue" and due == e.DEC9]}  # fmt: skip
    e.self_check(check, "K4", {"before_line1": plan["before"][0] if plan["before"] else None,
                               "before_line2": plan["before"][1] if len(plan["before"]) > 1 else None,
                               "before_line3": plan["before"][2] if len(plan["before"]) > 2 else None,
                               **{f"overdue_line2_{i}": d for i, d in enumerate(plan["overdue 9 Dec"], 1)}})  # fmt: skip
    e.compare_truth(check, co, "K4", {"line1_promised": e.DEC7, "line2_promised": e.DEC9, "line3_promised": e.DEC14,
                                      **{f"reminder_{i}": d for i, (d, _due, _k) in enumerate(sorted(want), 1)}})  # fmt: skip
    out.metrics["K4"] = {"order": po, "reminders seen": len(seen), "oracle": len(want), "Mon-Fri would be": len(wrong),
                         "line 3 before": [str(x) for x in line3]}  # fmt: skip
