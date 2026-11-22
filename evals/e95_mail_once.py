"""E95: mail is never lost or doubled (task 11a.1, DF-23), written without the implementation.

Independence: written from the AI foundation spec (2.1 tenant 0100, 2.2 outbox, OP-3, OP-4) only. The mail server is
`aif.Smtp` (every message counted by its Message-ID); the worker's jobs are run in this process at chosen times.

Scenarios, on the main demo company (Apex):
1. Orders approved and sent through the API: each sent order's mail is one row (`status 'sent'`, `sealed` null) and
   exactly one message with the row's Message-ID; every message that left has its row (nothing sent unrecorded).
2. `outbox.queue_email`, then the caller rolls back: no row, no mail; the `email` job retries while young and ends
   `done` once older than the 10-minute grace time, still with no mail.
3. Queued and committed, `send_now` skipped: nothing leaves at commit; the worker sends it once; `deliver` again is a
   no-op.
4. The mail server down: `send_now` swallows the error, the row stays `queued` with attempts and the last error through
   three worker rounds; the server back, the next round sends it once and empties `sealed`.
5. `outbox.deliver` from two threads at once on one row (the server slow): one message.
6. A notification with a secret link (`email_extra`) and the request approval mails: the link is in the message, not
   in `email_message.body_text`, not in any job's payload; an `email` job's payload is only the Message-ID; no new
   `send_email` job is made (OP-5: the old kind only drains).
Mutations caught: SMTP before the row (scenario 2 mails a rolled-back message; scenario 1 finds a message without a row);
commit inside the sender (scenario 2: a row survives the caller's rollback); body in the job payload (scenario 6); no
row lock in `deliver` (scenario 5 sends twice).
"""

import threading
from typing import Any

import aif
import block1 as b
import block2 as s2
import env
from lib import Outcome

ID = "E95"
TITLE = "Mail is recorded before it is sent, and leaves exactly once (outbox)"
REQUIREMENT = (
    "Task 11a.1, DF-23 (spec 2.2): every outgoing mail is an `email_message` row queued in the caller's transaction "
    "(secret parts sealed), sent by one locked sender after commit or by the `email` job; a rollback sends nothing; an "
    "SMTP outage leaves it queued and it is sent once later; two senders never double it; no body or link in a job"
)
PREFIX = "e95"
LINK_PART = "EVALSECRETLINK7Q3Z"


def run(ctx: Any) -> Outcome:
    env.forget_tenants(PREFIX)
    try:
        return _run(ctx)
    finally:
        env.forget_tenants(PREFIX)


def _run(ctx: Any) -> Outcome:
    from fastapi.testclient import TestClient
    from p2p.main import app

    out = Outcome(True)
    check = b.Checker(out)
    smtp = aif.install_smtp()
    co = s2.Co("apex", PREFIX, TestClient(app))
    with aif.no_live_models(aif.Model()):
        send_jobs0 = len(aif.jobs(co.slug, ["send_email"]))
        _orders(check, co, smtp)
        _rollback(check, co, smtp)
        _worker_sends(check, co, smtp)
        _outage(check, co, smtp)
        _two_senders(check, co, smtp)
        _secrets(check, co, smtp)
        made = len(aif.jobs(co.slug, ["send_email"])) - send_jobs0
        check(
            made == 0, f"{made} new job(s) of the old kind send_email were made (OP-5: only old ones drain)", "medium"
        )
    out.metrics.update({"checks": check.done, "failed": len(check.failed), "messages": len(smtp.sent)})
    out.notes += [
        (
            "Spec gap: how old the `email` job must be before an absent row counts as rolled back is measured from the job "
            "(10 minutes, spec 2.2); the eval runs the job 11 minutes after it was made."
        ),
        "Spec gap: the 24-hour `failed` state and its `email_failed` task (OP-4) need a clock 24 hours on; not run here.",
        "The mail server is the evals' fake (as every eval); 'Mailpit' in the spec reads as 'the messages that left'.",
    ]
    return out


def _row(co: s2.Co, m: str) -> dict[str, Any]:
    found = aif.rows(co, "select * from email_message where message_id = :m", m=m)
    return found[0] if found else {}


def _email_jobs(co: s2.Co, m: str) -> list[dict[str, Any]]:
    return [j for j in aif.jobs(co.slug, ["email"]) if aif.payload_of(j).get("message_id") == m]


def _queue(db: Any, to: str, subject: str, body: str = "eval body", **kw: Any) -> str:
    from p2p.platform import outbox

    return str(outbox.queue_email(db, to=to, subject=subject, body=body, **kw))


def _orders(check: b.Checker, co: s2.Co, smtp: aif.Smtp) -> None:
    aif.to_orders(co)
    pos = [str(r[0]) for r in co.q("select no from po where status <> 'cancelled' order by no")]
    check(bool(pos), "set-up: no purchase order after sourcing and ordering", "medium")
    for no in pos:
        co.advance_po(no)
    sent = [no for no in pos if co.po_status(no) in ("sent", "acknowledged", "closed")]
    check(bool(sent), f"no order reached 'sent' (statuses {[co.po_status(n) for n in pos]})", "medium")
    for no in sent:
        mails = aif.rows(co, "select * from email_message where direction = 'out' and linked_type = 'po' and "
                             "linked_id = :n", n=no)  # fmt: skip
        if not check(bool(mails), f"order {no} is sent but has no outgoing email row"):
            continue
        first = mails[0]
        m = str(first["message_id"])
        check(first.get("status") == "sent", f"order {no}: its mail row is {first.get('status')!r}, expected 'sent'")
        check(first.get("sealed") is None, f"order {no}: its mail row keeps the sealed body after sending")
        check(smtp.count(m) == 1, f"order {no}: {smtp.count(m)} message(s) left with Message-ID {m}, expected 1")
    known = {str(r[0]) for r in co.q("select message_id from email_message where direction = 'out'")}
    unrecorded = [m for m in smtp.sent if m["message_id"] not in known and "code" not in str(m["subject"]).lower()]
    check(not unrecorded, f"{len(unrecorded)} message(s) left with no email_message row (SMTP before the row?), "
          f"e.g. {[m['subject'] for m in unrecorded[:3]]}")  # fmt: skip


def _rollback(check: b.Checker, co: s2.Co, smtp: aif.Smtp) -> None:
    db = aif.new_session(co)
    try:
        m = _queue(db, "rollback@eval.example", "E95 rolled back")
        db.rollback()
    finally:
        db.close()
    check(not _row(co, m), "a queued mail whose caller rolled back still has its email_message row")
    jobs = _email_jobs(co, m)
    check(len(jobs) == 1, f"queue_email made {len(jobs)} `email` job(s) for its message, expected 1 (made before the "
          "caller commits)")  # fmt: skip
    aif.run_jobs(co.slug, ["email"], now=aif.later(0.1))
    young = _email_jobs(co, m)
    check(bool(young) and young[0]["status"] == "queued", f"the young job of a rolled-back mail ended "
          f"{[j['status'] for j in young]}, expected queued for a retry (the caller may not have committed yet)",
          "medium")  # fmt: skip
    aif.run_jobs(co.slug, ["email"], now=aif.later(11))
    old = _email_jobs(co, m)
    check(bool(old) and old[0]["status"] == "done", f"after the grace time the job of a rolled-back mail is "
          f"{[(j['status'], j.get('last_error')) for j in old]}, expected done")  # fmt: skip
    check(smtp.count(m) == 0, f"a rolled-back mail was sent {smtp.count(m)} time(s)")


def _worker_sends(check: b.Checker, co: s2.Co, smtp: aif.Smtp) -> None:
    from p2p.platform import outbox

    db = aif.new_session(co)
    try:
        m = _queue(db, "worker@eval.example", "E95 sent by the worker")
        db.commit()
    finally:
        db.close()
    check(smtp.count(m) == 0, "a queued mail left at commit, before send_now or the job")
    check(_row(co, m).get("status") == "queued", f"a committed mail is {_row(co, m).get('status')!r}, expected queued")
    aif.run_jobs(co.slug, ["email"], now=aif.later(0.2))
    check(smtp.count(m) == 1, f"the worker sent a committed mail {smtp.count(m)} time(s), expected 1")
    row = _row(co, m)
    check(row.get("status") == "sent" and row.get("sealed") is None and row.get("sent_at") is not None,
          f"after sending: status {row.get('status')!r}, sealed kept {row.get('sealed') is not None}")  # fmt: skip
    db = aif.new_session(co)
    try:
        again = outbox.deliver(db, m)
    finally:
        db.close()
    check(smtp.count(m) == 1, f"deliver on a sent row sent it again ({again!r})")
    check(str(again) == "sent", f"deliver on a sent row answered {again!r}, expected 'sent'", "low")


def _outage(check: b.Checker, co: s2.Co, smtp: aif.Smtp) -> None:
    from p2p.platform import outbox

    smtp.down = True
    db = aif.new_session(co)
    try:
        m = _queue(db, "outage@eval.example", "E95 while the server is down")
        db.commit()
    finally:
        db.close()
    try:
        outbox.send_now(co.slug, [m])
        swallowed = True
    except Exception:  # noqa: BLE001 - send_now must swallow it (spec 2.2)
        swallowed = False
    check(swallowed, "send_now raised while the mail server was down (the job retries; the caller must not fail)")
    attempts = []
    for minutes in (20, 60, 120):
        aif.run_jobs(co.slug, ["email"], now=aif.later(minutes))
        row = _row(co, m)
        attempts.append(int(row.get("attempts") or 0))
        check(row.get("status") == "queued" and row.get("last_error"), f"server down, round at +{minutes} min: row "
              f"{row.get('status')!r}, last_error {row.get('last_error')!r}, expected queued with the error")  # fmt: skip
    check(attempts == sorted(attempts) and attempts[-1] >= 3, f"SMTP attempts recorded {attempts}, expected rising to "
          "at least 3")  # fmt: skip
    check(smtp.count(m) == 0, "a message counted as sent while the server was down")
    smtp.down = False
    aif.run_jobs(co.slug, ["email"], now=aif.later(240))
    row = _row(co, m)
    if smtp.count(m) == 0:  # the job gave up after its attempts: the sweep (every 15 minutes) queues it again
        aif.run_jobs(co.slug, ["email_sweep", "email"], now=aif.later(300))
        aif.run_jobs(co.slug, ["email"], now=aif.later(310))
        row = _row(co, m)
    check(smtp.count(m) == 1, f"after the server came back the mail left {smtp.count(m)} time(s), expected 1")
    check(row.get("status") == "sent" and row.get("sealed") is None, f"after the outage: {row.get('status')!r}, "
          f"sealed kept {row.get('sealed') is not None}")  # fmt: skip


def _two_senders(check: b.Checker, co: s2.Co, smtp: aif.Smtp) -> None:
    from p2p.platform import outbox

    db = aif.new_session(co)
    try:
        m = _queue(db, "race@eval.example", "E95 two senders")
        db.commit()
    finally:
        db.close()
    smtp.slow = 0.8
    gate = threading.Barrier(2)
    answers: list[Any] = [None, None]

    def go(i: int) -> None:
        s = aif.new_session(co)
        try:
            gate.wait(timeout=30)
            answers[i] = outbox.deliver(s, m)
        except Exception as exc:  # noqa: BLE001 - a refused second sender is fine; a second message is not
            answers[i] = f"{type(exc).__name__}"
        finally:
            s.close()

    threads = [threading.Thread(target=go, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    smtp.slow = 0.0
    check(smtp.count(m) == 1, f"two deliver calls at once sent the mail {smtp.count(m)} time(s) ({answers}); the row "
          "lock must stop the second")  # fmt: skip


def _secrets(check: b.Checker, co: s2.Co, smtp: aif.Smtp) -> None:
    from p2p.platform import notifications

    who = co.role("buyer")
    uid = co.user_id(who)
    db = aif.new_session(co)
    try:
        notifications.notify(db, co.slug, uid, "eval", "E95 a link that works as a key", "Please look at this.",
                             email_extra=f"\nOpen: https://eval.example/l/{LINK_PART}")  # fmt: skip
        db.commit()
    finally:
        db.close()
    aif.run_jobs(co.slug, ["email"], now=aif.later(1))
    mine = [m for m in smtp.to(co.people[who]["email"]) if "E95 a link" in str(m["subject"])]
    check(len(mine) == 1 and LINK_PART in smtp.everything(mine[0]), f"the notification left {len(mine)} time(s) "
          "with the secret link in it, expected once")  # fmt: skip
    secrets = [LINK_PART]
    for m in smtp.sent:  # the request approval mails carry signed links (approve, reject, return)
        for line in str(m.get("body") or "").splitlines():
            if line.startswith("Approve: ") and "/" in line:
                secrets.append(line.split("Approve: ", 1)[1].split("?")[0].rstrip("/").rsplit("/", 1)[-1])
    check(len(secrets) > 1, "no request approval mail with signed links was seen; the link check ran on the "
          "notification only", "low")  # fmt: skip
    bodies = [str(r[0] or "") + str(r[1] or "") for r in co.q("select body_text, sealed from email_message")]
    payloads = [str(j.get("payload")) for j in aif.jobs(co.slug)]
    for s in secrets:
        if len(s) < 12:
            continue
        check(not any(s in x for x in bodies), f"a secret link part {s[:8]}... is stored in email_message in clear")
        check(not any(s in p for p in payloads), f"a secret link part {s[:8]}... is in a job's payload")
    wide = [j["id"] for j in aif.jobs(co.slug, ["email"]) if set(aif.payload_of(j)) != {"message_id"}]
    check(not wide, f"{len(wide)} `email` job(s) carry more than the Message-ID in their payload")
    no_key = [j["id"] for j in aif.jobs(co.slug, ["email"]) if "dedupe_key" in j and not j.get("dedupe_key")]
    check(not no_key, f"{len(no_key)} `email` job(s) have no dedupe_key (spec: key = the Message-ID)", "medium")
