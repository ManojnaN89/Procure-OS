"""E141: chasing unacknowledged orders per version (Rev l.119 "POs not acknowledged", AG02 templated reminders, spec
8.8.1-8.8.6, K7, K7b), written without the implementation.

Independence: written from the block 8 spec (sections 5 8.8, 6, 8, 9 K7, K7b) and the dataset only. The oracle
(`block8.Oracle8.chasers`) counts on PLT-CKN's own calendar (Mon-Sat, 24 Nov and 25 Dec 2026 closed): due
`supplier_ack_due_days` (2) working days after the version was sent; the first chaser on the first working day after
that; then every `supplier_chase_days` (3) working days; at most `supplier_chase_max` (3) per version; the overdue task
on the day the next would be due.

Given a fresh Apex reset, two administrators set `supplier.remind` to automatic; P1 and P2 approved by U08 and sent on
Thu 26 Nov (due Sat 28 Nov), never acknowledged; the daily job each day from 27 Nov to 15 Dec at 09:45:
- P1 (K7): chasers Mon 30 Nov, Thu 3 Dec, Mon 7 Dec, each to sales@ with a new version 1 respond link, the fixed words
  "We sent you purchase order <P1> (version 1) on 26 Nov 2026 and have not received your acceptance. Please accept it,
  or tell us what you can do, here (the link works once and asks for a code we email to you):" and version 1's stored
  PDF; none on Sun 29 Nov; a second run on 30 Nov adds nothing; on Thu 10 Dec one task `po_ack_overdue` for U03,
  chain [U08], SLA 18, "Deccan Castings Pvt Ltd has not acknowledged <P1> version 1 after 3 reminders", audit
  `po.ack_overdue`; once; completed by the system when P1 is revised on 11 Dec; the exceptions row "<P1> version 1 has
  not been acknowledged by Deccan Castings Pvt Ltd" from 29 Nov (not on 28 Nov).
- P2 (K7b): the same three chasers; on Tue 8 Dec buyer@ cuts line 3 to 140 (version 2 sent 8 Dec, due Thu 10 Dec): no
  task for version 1 on 10 Dec; version 2's first chaser on Fri 11 Dec, its second on Tue 15 Dec (not Mon 14 Dec).
- nothing else automatic: neither order cancelled, no other order made.
Ends with a tenant check.
Mutations: the count across versions; calendar days between chasers.
"""

from datetime import date, timedelta
from typing import Any

import block1 as b
import block4 as k
import block6 as s
import block8 as e
import env
from lib import Outcome, capture_mail

ID = "E141"
TITLE = "Unacknowledged orders are chased per version on the delivery place's working days, then handed to the buyer"
REQUIREMENT = (
    "Rev l.119, AG02 (block 8 spec 8.8, K7, K7b): chasers on PLT-CKN working days from the version's sending, every 3 "
    "working days, at most 3 per version, each with a fresh link, the fixed words and the version's PDF; after the "
    "last, one po_ack_overdue task for the buyer (chain the purchase manager) completed by the system; a new version "
    "starts again; the exceptions row from the due day; nothing else automatic; tenant check"
)
PREFIX = "e141"
WALK = (e.NOV27, e.DEC15)
CHASE = "have not received your acceptance"


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
        e.data_gaps(check, apex, chase=True)
        for key in ("supplier_chase_days", "supplier_chase_max"):
            check(apex.org(key) == "3", f"set-up: org_setting {key} is {apex.org(key)!r}; migration 0150 adds 3 "
                  "(section 7)", "medium")  # fmt: skip
        asked, done = apex.automation("supplier.remind", "auto")
        check(asked.status_code == 202 and done is not None and done.status_code == 200, f"set-up: supplier.remind could "
              f"not be set to automatic ({asked.status_code} {getattr(done, 'status_code', None)})", "medium")  # fmt: skip
        orders, tokens = _k7(out, check, apex, notes)
        e.pin(e.TODAY)
        e.tenant_check8(check, apex, bw, orders, tokens, start)
    finally:
        mail.send_email = saved  # type: ignore[assignment]
    out.metrics.update({"checks": check.done, "failed": len(check.failed)})
    out.notes += sorted(set(notes)) + [
        ("The job `supplier_chase` is enqueued at 09:45 IST of each business day and run the way the worker claims it; "
         "a chaser's day is the business day of the run that wrote it (its timestamps are the machine's clock)."),
        ("A chaser is seen as a supplier_chase row (its po_version), a mail to the contact with the spec's words and a "
         "new respond link; the version is read from the row, else from the link."),
        "The exceptions screen is read after POST /api/alerts/run (as the buyer) on the business day.",
    ]
    return out


def _sent_on(co: e.Co8, check: b.Checker, po: str, label: str) -> str | None:
    tok, _mail = co.send8(check, po, label)
    return tok


def _k7(out: Outcome, check: b.Checker, co: e.Co8, notes: list[str]) -> tuple[list[str], list[str | None]]:
    oracle = e.Oracle8(co)
    m = k.Mutations()
    _rule, who = oracle.approver(oracle.p()["total"], frozenset({co.buyer()}))
    p1 = co.p_order(check, "K7 P1", who, notes)
    p2 = co.p_order(check, "K7b P2", who, notes)
    if not (p1 and p2):
        return [p1, p2], []
    e.pin(e.NOV26)
    tokens = [_sent_on(co, check, p1, "K7 send P1"), _sent_on(co, check, p2, "K7b send P2")]
    for po in (p1, p2):
        if co.place(po, 1, "sent_at", e.NOV26):
            notes.append("version 1's sent_at was put on 26 Nov")
    pos0 = int(co.one("select count(*) from po") or 0)
    sup = oracle.supplier_name(e.SUP)

    # the oracle: chaser days per order and version, the overdue task
    c1, task1 = oracle.chasers(e.NOV26)
    p2v2, _t = oracle.chasers(e.DEC8)
    p1v2, _t = oracle.chasers(e.DEC11)
    want = {p1: {(d, 1) for d in c1} | {(d, 2) for d in p1v2 if d <= WALK[1]},
            p2: {(d, 1) for d in c1 if d < e.DEC8} | {(d, 2) for d in p2v2 if d <= WALK[1]}}  # fmt: skip
    e.self_check(check, "K7", {"ack_due": oracle.ack_due(e.NOV26).isoformat(), "chaser_1": c1[0].isoformat(),
                               "chaser_2": c1[1].isoformat(), "chaser_3": c1[2].isoformat(), "overdue_task": task1.isoformat()})  # fmt: skip
    e.self_check(check, "K7b", {"v2_due": oracle.ack_due(e.DEC8).isoformat(), "v2_chaser_1": p2v2[0].isoformat()})

    seen: dict[str, set[tuple[date, int]]] = {p1: set(), p2: set()}
    tasks_seen: dict[date, list[dict[str, Any]]] = {}
    errors: set[str] = set()
    words_bad: list[str] = []
    day = WALK[0]
    while day <= WALK[1]:
        marks = (co.mark(), co.link_mark(), int(co.one("select coalesce(max(id), 0) from supplier_chase") or 0), co.task_mark())
        errors |= {x for x in co.chase(day) if x}
        found = _found(co, [p1, p2], marks)
        for po in (p1, p2):
            seen[po] |= {(day, v) for v in found[po]["versions"]}
            words_bad += _words(co, oracle, po, day, found[po])
        tasks_seen[day] = [x for x in co.new_tasks("po_ack_overdue", marks[3])]
        if day == e.NOV30:  # a second run the same day adds nothing
            marks = (co.mark(), co.link_mark(), int(co.one("select coalesce(max(id), 0) from supplier_chase") or 0), co.task_mark())
            co.chase(day)
            again = _found(co, [p1, p2], marks)
            check(not any(again[po]["versions"] or again[po]["mails"] for po in (p1, p2)), f"K7: a second run on 30 Nov "
                  f"chased again: {[(po, again[po]['versions'], len(again[po]['mails'])) for po in (p1, p2)]}")  # fmt: skip
        if day in (date(2026, 11, 28), date(2026, 11, 29)):
            _exceptions_row(check, co, p1, sup, day, oracle.ack_due(e.NOV26))
        if day == e.DEC8:
            r = co.revise8(p2, 3, qty=140, version=1, reason="plan cut")
            check(r.status_code == 200 and int(co.po_row(p2).get("version") or 0) == 2, f"K7b: the cut on 8 Dec answered "
                  f"{r.status_code} {r.text[:150]}")  # fmt: skip
            for col in ("made_at", "sent_at"):
                co.place(p2, 2, col, e.DEC8)
        if day == task1:
            _task(check, co, p1, p2, sup, tasks_seen[day], who)
        if day == e.DEC11:
            check(not tasks_seen[day], f"K7: the run on 11 Dec made {len(tasks_seen[day])} more po_ack_overdue task(s) "
                  "(once per version)")  # fmt: skip
            r = co.revise8(p1, 3, qty=140, version=1, reason="plan cut")
            for col in ("made_at", "sent_at"):
                co.place(p1, 2, col, e.DEC11)
            ts = [x for x in co.tasks("po_ack_overdue") if p1 in e.blob(x).upper()]
            check(bool(ts) and all(x.get("status") not in ("open", "pending") for x in ts), f"K7: after P1 was revised "
                  f"its po_ack_overdue task is {[x.get('status') for x in ts]}; the system completes it (8.8.4)", "medium")  # fmt: skip
        day += timedelta(days=1)

    check(not errors, f"K7: the chaser job failed: {sorted(errors)[:2]}")
    for po, label in ((p1, "K7 P1"), (p2, "K7b P2")):
        check(seen[po] == want[po], f"{label}: chasers (day, version) {sorted(seen[po])}; oracle {sorted(want[po])}; "
              f"missing {sorted(want[po] - seen[po])}, extra {sorted(seen[po] - want[po])}")  # fmt: skip
    check(not words_bad, f"K7: chasers whose words, link or PDF differ from spec 8.8.3: {words_bad[:3]}", "medium")
    v2_days = sorted(d for d, v in seen[p2] if v == 2)
    right = sorted(d for d, v in want[p2] if v == 2)
    m.case("count_across_versions", "K7b version 2's chasers after three on version 1", v2_days, right, [])
    wrong_days = [d for d in oracle.chasers(e.DEC8, calendar_days=True)[0] if d <= WALK[1]]
    m.case("calendar_days", "K7b version 2's chasers", v2_days, right, wrong_days)
    for po in (p1, p2):
        check(co.po_row(po).get("status") not in ("cancelled", "closed"), f"K7: {po} is {co.po_row(po).get('status')}; "
              "nothing is automatic beyond chasers and the task (8.8.6)")  # fmt: skip
    check(int(co.one("select count(*) from po") or 0) == pos0, "K7: an order was made while chasing (8.8.6: no new "
          "supplier)")  # fmt: skip
    # spec 9 K7: "exceptions row from 29 Nov", the day after the acknowledgement fell due
    e.compare_truth(check, co, "K7", {"sent": e.NOV26, "ack_due": oracle.ack_due(e.NOV26),
                                      "overdue_from": oracle.ack_due(e.NOV26) + timedelta(days=1),
                                      **{f"chaser_{i}": d for i, d in enumerate(c1, 1)}, "overdue_task": task1})  # fmt: skip
    m.report(check, out, ["count_across_versions", "calendar_days"])
    out.metrics["K7"] = {"P1": p1, "P2": p2, "P1 chasers": [str(d) for d, _v in sorted(seen[p1])],
                         "P2 chasers": [f"{d} v{v}" for d, v in sorted(seen[p2])], "task day": str(task1)}  # fmt: skip
    return [p1, p2], tokens


def _found(co: e.Co8, orders: list[str], marks: tuple[int, int, int, int]) -> dict[str, dict[str, Any]]:
    """What one run did per order: the versions chased (supplier_chase rows of kind ack, else new respond links), the
    chaser mails and the new links."""
    mail_mark, link_mark, chase_mark, _task = marks
    out: dict[str, dict[str, Any]] = {}
    for po in orders:
        rows = co.qd("select * from supplier_chase where po_no = :n and id > :i order by id", n=po, i=chase_mark)
        links = co.respond_links(po, link_mark)
        mails = [x for x in co.mails_to(e.SALES, mail_mark, po) if CHASE in e.norm(x.get("body"))]
        versions = [int(x.get("po_version") or 0) for x in rows if str(x.get("kind") or "ack") == "ack"]
        if not versions and mails:
            versions = [int(x.get("po_version") or 0) for x in links]
        out[po] = {"versions": versions, "mails": mails, "links": links, "rows": rows}
    return out


def _words(co: e.Co8, oracle: e.Oracle8, po: str, day: date, found: dict[str, Any]) -> list[str]:
    bad = []
    for v in found["versions"]:
        sent = e.ist_day(co.po_versions(po).get(v, {}).get("sent_at")) or day
        text_ = e.chaser_text(po, v, sent)
        mail = next((x for x in found["mails"] if e.norm(text_) in e.norm(x.get("body"))), None)
        tok = co.token_in(mail) if mail else None
        link_ok = any(int(x.get("po_version") or 0) == v and str(x.get("sent_to") or "").lower() == e.SALES
                      for x in found["links"])  # fmt: skip
        pdf = co.version_pdf(po, v)
        stored = e.t.sha(pdf.content) if pdf.status_code == 200 and pdf.content[:5] == b"%PDF-" else None
        attached = [e.t.sha(x) for x in e.t.attached_pdfs(mail)] if mail else []
        if not (mail and tok and link_ok and stored and attached == [stored]):
            bad.append(f"{po} v{v} on {day}: mail {bool(mail)}, link {tok is not None} (row {link_ok}), PDF "
                       f"{attached == [stored] and stored is not None}")  # fmt: skip
    return bad


def _exceptions_row(check: b.Checker, co: e.Co8, po: str, sup: str, day: date, due: date) -> None:
    co.alerts_run()
    rows = co.exception_rows()
    words = f"{po} version 1 has not been acknowledged by {sup}"
    hit = [x for x in rows if e.norm(words) in e.norm(e.blob(x))]
    if day <= due:
        check(not hit, f"K7: on {e.long_date(day)} (due {e.long_date(due)}) the exceptions screen already says "
              f"'{words}'", "medium")  # fmt: skip
        return
    check(bool(hit), f"K7: on {e.long_date(day)} the exceptions screen has no row '{words}' (8.8.5)")
    detail = "Sent on 26 Nov 2026; 0 reminders."
    check(bool(hit) and e.norm(detail) in e.norm(e.blob(hit[0])), f"K7: the row lacks '{detail}': "
          f"{e.blob(hit[0])[:250] if hit else None}", "medium")  # fmt: skip


def _task(check: b.Checker, co: e.Co8, p1: str, p2: str, sup: str, new: list[dict[str, Any]], who: str) -> None:
    mine = [x for x in new if p1 in e.blob(x).upper()]
    other = [x for x in new if p2 in e.blob(x).upper()]
    check(len(mine) == 1, f"K7: on 10 Dec {len(mine)} po_ack_overdue task(s) for {p1}; expected one (8.8.4)")
    check(not other, f"K7b: a po_ack_overdue task for {p2} on 10 Dec although version 1 was replaced on 8 Dec")
    if not mine:
        return
    x = mine[0]
    title = f"{sup} has not acknowledged {p1} version 1 after 3 reminders"
    chain = s.chain_codes(co, x.get("escalation_chain"))
    check(e.norm(title) in e.norm(x.get("title")) and s.person_is(co, x.get("assignee"), co.buyer())
          and chain[:1] == [who], f"K7: the task {x.get('title')!r} for {co.code_of(x.get('assignee'))}, chain {chain}; "
          f"expected '{title}' for {co.buyer()}, chain [{who}]")  # fmt: skip
    check(float(x.get("sla_hours") or 0) == 18, f"K7: the task's SLA {x.get('sla_hours')}; the spec says 18 working "
          "hours", "medium")  # fmt: skip
    p = x.get("payload") or {}
    check(str(p.get("po_no")) == p1 and str(p.get("version")) == "1" and e.iso(p.get("sent_on")) == "2026-11-26"
          and str(p.get("chasers")) == "3" and "call the supplier" in str(p.get("next") or "").lower(),
          f"K7: the task's payload {p}; expected po_no, version 1, sent_on 26 Nov, chasers 3, next 'Call the supplier, "
          "...'", "medium")  # fmt: skip
    check(bool(co.audits("po.ack_overdue", p1)) or bool(co.since("po.ack_overdue", 0)), "K7: no po.ack_overdue audit",
          "medium")  # fmt: skip
