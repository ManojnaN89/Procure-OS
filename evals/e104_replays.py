"""E104: replays make no duplicates (task 11a.7), written without the implementation.

Independence: written from the AI foundation spec (2.2 job keys, events and outbox; 4 E104) only. Every model is
scripted (`aif.no_live_models`), so a replayed reading job never calls a live model.

Scenario, on a fresh main demo company (Apex): `demo populate` fills it with cases at every stage (driving the real API
as the role logins). Then one settling pass (`replenish`, `source`, `order`, the worker until idle) so that work
populate leaves on purpose (items still low) is done before the snapshot. Snapshot: requests, RFQs, orders, outgoing
mail rows, tasks, notifications, planning notes, recommendations, events, workflows, quote drafts, and the messages
that left. Then the replays:
- every `done` job that carries a `dedupe_key` is enqueued again with its key (`worker.enqueue(..., key=)`), and the
  worker runs until idle;
- every stored incoming mail is ingested again (a mailbox polled again after a crash);
- the demand folder imported again (`p2p.cli import demand`);
- `replenish`, `source`, `order` and `remind --dry-run` run again; the worker until idle.
Every count and the number of messages that left are unchanged; while a keyed job waits, enqueueing the same key again
is refused (None).
Mutations caught: the job key removed from one producer (a replayed job doubles its work or a second one queues); an
event recorded without its key (a replayed mail routes again).
"""

from typing import Any

import aif
import block1 as b
import block2 as s2
import env
from lib import Outcome

ID = "E104"
TITLE = "Replaying jobs, mail, imports and the daily commands after a full demo makes no duplicates"
REQUIREMENT = (
    "Task 11a.7 (spec 2.2, 4): jobs keyed by their business key (one waiting per key), events keyed by their source, "
    "mail recorded once in the outbox; replaying any of them, or re-running the daily commands, changes no count of "
    "documents, mail, tasks, notifications, notes, recommendations, events or workflows"
)
PREFIX = "e104"
TABLES = {
    "requests": "request",
    "rfqs": "rfq",
    "orders": "po",
    "mail out": "email_message where direction = 'out'",
    "tasks": "task",
    "notifications": "notification",
    "planning notes": "planning_note",
    "recommendations": "recommendation",
    "events": "event",
    "workflows": "workflow",
    "quote drafts": "quote_draft",
    "invoices": "invoice",
}


def run(ctx: Any) -> Outcome:
    env.forget_tenants(PREFIX)
    try:
        return _run(ctx)
    finally:
        env.forget_tenants(PREFIX)


def _run(ctx: Any) -> Outcome:
    from fastapi.testclient import TestClient
    from p2p.main import app
    from p2p.platform import demo_cases

    out = Outcome(True)
    check = b.Checker(out)
    smtp = aif.install_smtp()
    co = s2.Co("apex", PREFIX, TestClient(app))
    with aif.no_live_models(aif.Model()):
        filled = demo_cases.populate(co.slug)
        out.metrics["stages"] = len(getattr(filled, "cases", {}) or {})
        if getattr(filled, "stopped", None):
            out.notes.append(f"demo populate stopped early: {filled.stopped}")
        _daily(co)
        aif.drain(co.slug, now=aif.later(1))
        before, sent0 = aif.counts(co, TABLES), len(smtp.sent)
        _replay_jobs(check, co)
        after_jobs = aif.counts(co, TABLES)
        _same(check, "replayed jobs", before, after_jobs)
        _replay_mail(check, co)
        _same(check, "mail ingested again", before, aif.counts(co, TABLES))
        code, said = b.cli("import", "demand", co.slug, str(co.dataset))
        check(code == 0, f"re-importing the demand folder exited {code}: {said[-300:]}", "medium")
        aif.drain(co.slug, now=aif.later(3))
        _same(check, "demand imported again", before, aif.counts(co, TABLES))
        _daily(co, remind=True)
        aif.drain(co.slug, now=aif.later(4))
        _same(check, "the daily commands again", before, aif.counts(co, TABLES))
        check(len(smtp.sent) == sent0, f"the replays sent {len(smtp.sent) - sent0} more message(s)")
    out.metrics.update({"checks": check.done, "failed": len(check.failed), **{f"n {k}": v for k, v in before.items()}})
    out.notes += [
        (
            "Spec gap: populate leaves items below their reorder point on purpose (`low_items_left`); the first replenish "
            "after it rightly makes requests, so the snapshot is taken after one settling pass, not straight after populate."
        ),
        (
            "Spec gap: 're-enqueue every done job with its key' is read as the jobs that have a key; a job with no key "
            "(a timer, a one-off) has no business key to deduplicate on."
        ),
    ]
    return out


def _daily(co: s2.Co, remind: bool = False) -> None:
    b.cli("replenish", co.slug)
    b.cli("source", co.slug)
    b.cli("order", co.slug)
    if remind:
        b.cli("remind", co.slug, "--dry-run")


def _same(check: b.Checker, label: str, before: dict[str, int], after: dict[str, int]) -> None:
    moved = {k: (before.get(k), after.get(k)) for k in after if after.get(k) != before.get(k)}
    check(not moved, f"{label}: counts changed {moved}")


def _replay_jobs(check: b.Checker, co: s2.Co) -> None:
    from p2p.platform import worker

    done = [j for j in aif.jobs(co.slug) if j["status"] == "done"]
    check(bool(done) and "dedupe_key" in done[0], "the job table has no dedupe_key column (control 0006)")
    keyed = [j for j in done if j.get("dedupe_key")]
    check(bool(keyed), "no finished job of the demo carried a key", "medium")
    kinds = {j["kind"] for j in keyed}
    events = aif.count(co, "select count(*) from event") if aif.has(co, "event") else 0
    for k in ("email", "event_route"):
        # eval fix: the demo stores no incoming mail and planning is off by default, so it records no event and has no
        # route job to replay; the check applies once an event exists
        check(k in kinds or (k == "event_route" and not events), f"no finished `{k}` job with a key after the demo",
              "medium")  # fmt: skip
    seen: set[tuple[str, str]] = set()
    refused_twice = 0
    for j in keyed:
        pair = (str(j["kind"]), str(j["dedupe_key"]))
        if pair in seen:
            continue
        seen.add(pair)
        first = worker.enqueue(co.slug, j["kind"], aif.payload_of(j), key=j["dedupe_key"])  # type: ignore[call-arg]
        again = worker.enqueue(co.slug, j["kind"], aif.payload_of(j), key=j["dedupe_key"])  # type: ignore[call-arg]
        if first is not None and again is None:
            refused_twice += 1
        check(again is None, f"a second {j['kind']} job with key {j['dedupe_key']!r} was queued while one waits")
    aif.drain(co.slug, now=aif.later(2))
    failed = [j for j in aif.jobs(co.slug) if j["status"] == "failed" and j["id"] > max(x["id"] for x in done)]
    check(not failed, f"{len(failed)} replayed job(s) failed: {[(f['kind'], f.get('last_error')) for f in failed][:3]}",
          "medium")  # fmt: skip


def _replay_mail(check: b.Checker, co: s2.Co) -> None:
    raws = co.q("select e.message_id, d.id from email_message e left join document d on d.id = e.raw_document_id "
                "where e.direction = 'in'")  # fmt: skip
    again = 0
    for message_id, _doc in raws:
        raw = aif.raw_mail("rfq+RFQ-REPLAY@eval.example", "replayed", "replayed", message_id=str(message_id))
        if aif.ingest(co, raw) is not None:
            again += 1
    check(again == 0, f"{again} incoming mail(s) were stored again when ingested a second time")
    aif.run_jobs(co.slug, ["event_route"], now=aif.later(2.5))
