"""E97: events are recorded once (D39 rule 1, task 11a.1), written without the implementation.

Independence: written from the AI foundation spec (2.1 tenant 0101, 2.2 "Events", 2.3 planning) only. The model is
scripted (`aif.no_live_models`): every gateway in this process answers from it, so no live model is called.

Scenarios, on the main demo company (Apex):
1. A supplier's reply to a request for quotation, in a mailbox that hands it out every time: polled twice (`emails.poll`
   and the `mailbox_poll` job with the mailbox swapped) and the same raw message ingested once more. Then the worker's
   `event_route` and `read_supplier_reply` jobs run. One `email_message`, one `event` (`email.received`, key = the
   Message-ID, source `mailbox`), one `event.record` audit row, one `read_supplier_reply` job (its key `email:<id>`), one
   quote draft.
2. Planning switched on by two administrators; Run store check twice with nothing changed in between, the routing jobs
   run after each: the second run records no new `material_request.figures_changed` event and queues no new job; one open
   `planning.explain` workflow per request; after the explanation jobs run, one `planning_note` per request and figures.
3. 20 threads call `events.record` with one type and key at once: one row, exactly one caller told `new`.
Mutations caught: the unique `(type, key)` removed (scenario 3 makes several rows); a dedupe by payload with
select-then-insert (scenario 3 and the repeated poll).
"""

import threading
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import ai_harness
import aif
import block1 as b
import block2 as s2
import env
import lib
from ai_cases import Item
from lib import Outcome

ID = "E97"
TITLE = "Events are recorded once by their source key; repeats route nothing new"
REQUIREMENT = (
    "D39 rule 1 (spec 2.2): `event` unique on (type, key); `events.record` returns the existing row with new false; an "
    "incoming mail is one `email.received` event keyed by its Message-ID whose router queues the same reading job "
    "once (key email:<id>); a store check with unchanged figures records and queues nothing new"
)
PREFIX = "e97"
REPLY_ID = "<e97-reply-0001@supplier-eval.example>"


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
    aif.install_smtp()
    co = s2.Co("apex", PREFIX, TestClient(app))
    model = aif.Model()
    with aif.no_live_models(model):
        _reply(check, co, model)
        _store_checks(check, co)
        _race(check, co)
    out.metrics.update({"checks": check.done, "failed": len(check.failed), "model calls": model.calls})
    out.notes += [
        (
            "Spec gap: the event's `payload` and `occurred_at` for an incoming mail are not given (the Date header or the "
            "time it was read); the eval checks type, key and source only."
        ),
        (
            "Spec gap: whether the planning router opens a workflow for a request with nothing short or at risk is not "
            "said; the eval counts workflows per request that has an event."
        ),
    ]
    return out


def _reply(check: b.Checker, co: s2.Co, model: aif.Model) -> None:
    from p2p.platform import emails

    category = lib.busiest_category()
    item = next(r for r in co.rows("master", "item") if r["category_code"] == category)
    world = SimpleNamespace(db=co.db, slug=co.slug)
    rfq, _supplier, email = ai_harness.setup_rfq(world, "E97", [Item(1, item["code"], item["name"], Decimal(10),
                                                                    Decimal(item["standard_price"] or 100),
                                                                    Decimal(18))],
                                                   rfq_no="RFQ-E97-0001")  # fmt: skip
    # (an RFQ number the mailbox links a reply to by its plus address: the harness's EV-Q-... is not one, so the reply
    # would be stored unlinked and no reading job is due; eval fix, the checks are unchanged)
    model.default = {"intent": "quote", "lines": [{"item": item["code"], "unit_price": "101.50", "discount_pct": None,
                     "gst_rate": "18", "delivery_days": 7, "price_includes_gst": False}],
                     "valid_until": "2026-12-31", "confidence": 0.95}  # fmt: skip
    raw = aif.raw_mail(emails.plus_address("rfq", rfq), f"Re: Request for quotation {rfq}",
                       f"Our rate for {item['name']} is Rs 101.50 per unit, GST 18%, delivery 7 days.",
                       sender=email, message_id=REPLY_ID)  # fmt: skip
    box = aif.Box([raw])
    db = aif.new_session(co)
    try:
        emails.poll(db, co.slug, box)
        emails.poll(db, co.slug, box)
        db.commit()
    finally:
        db.close()
    saved = emails.configured_mailbox
    emails.configured_mailbox = lambda: box  # type: ignore[assignment]  # the poll job reads this mailbox
    try:
        from p2p.platform import worker

        worker.enqueue(co.slug, "mailbox_poll", {"every": 3600})
        aif.run_jobs(co.slug, ["mailbox_poll"], now=aif.later(0.1))
    finally:
        emails.configured_mailbox = saved
    again = aif.ingest(co, raw)
    check(again is None, f"ingesting the same raw message again stored it ({again})")
    stored = aif.rows(co, "select id from email_message where message_id = :m", m=REPLY_ID)
    check(len(stored) == 1, f"{len(stored)} email_message row(s) for one reply, expected 1")
    if not stored:
        return
    email_id = int(stored[0]["id"])
    events = aif.rows(co, "select * from event where type = 'email.received' and key = :k", k=REPLY_ID)
    check(len(events) == 1, f"{len(events)} email.received event(s) keyed by the Message-ID, expected 1")
    if events:
        check(events[0].get("source") == "mailbox", f"the event's source is {events[0].get('source')!r}", "medium")
    recorded = [
        a for a in aif.audit_after(co, "event.record") if REPLY_ID in str(a.get("entity_id")) + str(a.get("after"))
    ]
    check(len(recorded) == 1, f"{len(recorded)} event.record audit row(s) for the reply, expected 1 (only when new)",
          "medium")  # fmt: skip
    aif.run_jobs(co.slug, ["event_route"], now=aif.later(0.2))
    aif.run_jobs(co.slug, ["event_route"], now=aif.later(0.3))
    reads = [
        j for j in aif.jobs(co.slug, ["read_supplier_reply"]) if int(aif.payload_of(j).get("email_id") or 0) == email_id
    ]
    check(len(reads) == 1, f"{len(reads)} read_supplier_reply job(s) for one reply, expected 1")
    if reads and "dedupe_key" in reads[0]:
        check(reads[0]["dedupe_key"] == f"email:{email_id}", f"the reading job's key is {reads[0]['dedupe_key']!r}, "
              f"expected email:{email_id}", "medium")  # fmt: skip
    events = aif.rows(co, "select * from event where type = 'email.received' and key = :k", k=REPLY_ID)
    check(bool(events) and events[0].get("status") == "routed", f"the reply's event is "
          f"{[e.get('status') for e in events]}, expected routed")  # fmt: skip
    aif.run_jobs(co.slug, ["read_supplier_reply"], now=aif.later(0.4))
    drafts = aif.count(co, "select count(*) from quote_draft where email_id = :e", e=email_id)
    check(drafts == 1, f"{drafts} quote draft(s) for one reply, expected 1")
    route_jobs = [j for j in aif.jobs(co.slug, ["event_route"]) if events and
                  int(aif.payload_of(j).get("event_id") or 0) == int(events[0]["id"])]  # fmt: skip
    check(len(route_jobs) == 1, f"{len(route_jobs)} event_route job(s) for one event, expected 1")


def _figures_events(co: s2.Co) -> list[dict[str, Any]]:
    return aif.rows(co, "select * from event where type = 'material_request.figures_changed' order by id")


def _store_checks(check: b.Checker, co: s2.Co) -> None:
    aif.planning_on(co, check)
    r = co.base_state()
    check(r.status_code == 200, f"Run store check answered {r.status_code}")
    aif.at_risk(co, check)  # eval set-up: a request with a line at risk (see aif.at_risk)
    aif.run_jobs(co.slug, ["event_route"], now=aif.later(0.5))
    first = _figures_events(co)
    jobs1 = aif.jobs(co.slug, ["planning_explain"])
    check(bool(first), "a store check with planning on recorded no material_request.figures_changed event", "medium")
    for e in first:
        mr, _, digest = str(e["key"]).partition(":")
        check(bool(mr) and bool(digest), f"event key {e['key']!r} is not <mr_no>:<snapshot_hash>", "medium")
        check(e.get("source") == "store_check", f"figures event source {e.get('source')!r}", "low")
    r = co.store_check()
    check(r.status_code == 200, f"the second Run store check answered {r.status_code}")
    aif.run_jobs(co.slug, ["event_route"], now=aif.later(0.6))
    second = _figures_events(co)
    check(len(second) == len(first), f"a store check with nothing changed recorded {len(second) - len(first)} new "
          "figures event(s)")  # fmt: skip
    jobs2 = aif.jobs(co.slug, ["planning_explain"])
    check(len(jobs2) == len(jobs1), f"a store check with nothing changed queued {len(jobs2) - len(jobs1)} new "
          "explanation job(s)")  # fmt: skip
    mrs = {str(e["key"]).split(":")[0] for e in second}
    for mr in sorted(mrs):
        open_ = aif.count(co, "select count(*) from workflow where kind = 'planning.explain' and business_key = :k "
                              "and status in ('open', 'waiting')", k=mr)  # fmt: skip
        check(open_ <= 1, f"{mr}: {open_} open planning.explain workflows, expected at most one")
    aif.run_jobs(co.slug, ["planning_explain"], now=aif.later(1))
    dup = co.q(
        "select mr_no, snapshot_hash, count(*) from planning_note group by mr_no, snapshot_hash having count(*) > 1"
    )
    check(not dup, f"planning notes twice for the same figures: {dup[:3]}")
    notes = aif.count(co, "select count(*) from planning_note")
    check(notes >= 1 or not jobs2, "the explanation jobs ran and wrote no planning_note", "medium")
    co.store_check()
    aif.run_jobs(co.slug, ["event_route", "planning_explain"], now=aif.later(2))
    check(aif.count(co, "select count(*) from planning_note") == notes, "a third store check with nothing changed "
          "wrote another planning note")  # fmt: skip


def _race(check: b.Checker, co: s2.Co) -> None:
    from p2p.platform import events

    gate = threading.Barrier(20)
    answers: list[Any] = [None] * 20

    def go(i: int) -> None:
        s = aif.new_session(co)
        try:
            gate.wait(timeout=60)
            got = events.record(s, "eval.race", "E97-KEY-1", source="eval", payload={"thread": i})
            s.commit()
            answers[i] = bool(getattr(got, "new", None))
        except Exception as exc:  # noqa: BLE001 - a refused insert is a crash for the caller: counted as a failure
            s.rollback()
            answers[i] = type(exc).__name__
        finally:
            s.close()

    threads = [threading.Thread(target=go, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    n = aif.count(co, "select count(*) from event where type = 'eval.race' and key = 'E97-KEY-1'")
    check(n == 1, f"20 callers recording one event at once made {n} rows")
    news = sum(1 for a in answers if a is True)
    crashed = [a for a in answers if not isinstance(a, bool)]
    check(news == 1, f"{news} of 20 callers were told the event was new, expected exactly 1")
    check(not crashed, f"{len(crashed)} caller(s) got an error instead of the existing event: {crashed[:3]}")
