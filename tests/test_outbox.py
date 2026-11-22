"""The email outbox and job keys (task 11a.1, DF-23, DF-28): a mail is recorded with the work it is about, sent after
the commit, never lost with a rollback and never sent twice."""

import threading
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.platform import mail as mail_module
from p2p.platform import notifications, outbox, routing, tenants, users, worker


class FakeMail:
    """Stands in for the mail server: counts what it sends and can be told to be down."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.down = False
        self.lock = threading.Lock()

    def send_email(self, to: str, subject: str, body: str, **kw: Any) -> str:
        if self.down:
            raise OSError("connection refused")
        with self.lock:
            self.sent.append({"to": to, "subject": subject, "body": body, **kw})
        return str(kw.get("message_id"))


@pytest.fixture
def box(monkeypatch: pytest.MonkeyPatch) -> FakeMail:
    fake = FakeMail()
    monkeypatch.setattr(mail_module, "send_email", fake.send_email)
    return fake


@pytest.fixture
def db(control_db: str) -> Iterator[Session]:
    slug = "t" + uuid.uuid4().hex[:8]
    tenants.create_tenant(slug, load_reference=False)
    with Session(routing.engine_for(slug)) as session:
        session.info["slug"] = slug
        yield session
    routing.clear_engine_cache()


def _jobs(slug: str, kind: str) -> list[dict[str, Any]]:
    with tenants.control_engine().connect() as c:
        return [
            dict(r)
            for r in c.execute(
                text(
                    "select id, payload, status, dedupe_key, created_at from job where tenant_slug = :t and kind = :k"
                ),
                {"t": slug, "k": kind},
            ).mappings()
        ]


def _row(db: Session, message_id: str) -> Any:
    db.rollback()
    return db.execute(
        text(
            "select status, sealed, body_text, attempts, last_error, sent_at from email_message where message_id = :m"
        ),
        {"m": message_id},
    ).first()


def _queue(db: Session, **kw: Any) -> str:
    return outbox.queue_email(db, to="s@supplier.example", subject="Hello", body="Plain part", **kw)


def test_a_mail_whose_work_is_rolled_back_is_never_sent(db: Session, box: FakeMail) -> None:
    m = _queue(db)
    db.rollback()

    assert box.sent == [] and _row(db, m) is None
    (job,) = _jobs(db.info["slug"], "email")
    old = worker.JobContext(
        job["id"], db.info["slug"], job["payload"], datetime.now(UTC), job["created_at"] - timedelta(hours=1)
    )
    outbox._email_job(old)  # an old job whose row never came: nothing to send, and it ends quietly
    young = worker.JobContext(job["id"], db.info["slug"], job["payload"], datetime.now(UTC), datetime.now(UTC))
    with pytest.raises(RuntimeError, match="not committed"):
        outbox._email_job(young)  # a young one waits for its sender's commit
    assert box.sent == []


def test_a_committed_mail_leaves_once_with_the_secret_only_in_the_mail(db: Session, box: FakeMail) -> None:
    m = _queue(db, secret="\nhttps://app.example/link/SECRET123")
    db.commit()
    assert box.sent == []  # the commit sends nothing by itself
    outbox.send_now(db.info["slug"], [m])  # the caller sends it right after its commit

    assert [x["body"] for x in box.sent] == ["Plain part\nhttps://app.example/link/SECRET123"]
    status, sealed, body_text, attempts, _, sent_at = _row(db, m)
    assert (status, sealed, body_text, attempts) == ("sent", None, "Plain part", 1) and sent_at is not None
    (job,) = _jobs(db.info["slug"], "email")
    assert job["payload"] == {"message_id": m} and job["dedupe_key"] == m
    assert outbox.deliver(db, m) == "sent" and len(box.sent) == 1  # a second delivery sends nothing


def test_the_job_sends_a_mail_when_nobody_sent_it_after_the_commit(db: Session, box: FakeMail) -> None:
    m = _queue(db)
    db.commit()
    assert box.sent == []

    worker.run_once(tenant=db.info["slug"])

    assert len(box.sent) == 1 and _row(db, m)[0] == "sent"
    worker.run_once(tenant=db.info["slug"])
    assert len(box.sent) == 1


def test_a_mail_server_that_is_down_leaves_the_mail_queued_and_it_goes_once_later(db: Session, box: FakeMail) -> None:
    box.down = True
    m = _queue(db)
    db.commit()
    outbox.send_now(db.info["slug"], [m])  # never raises: the job retries
    status, sealed, _, attempts, error, _ = _row(db, m)
    assert (status, attempts) == ("queued", 1) and sealed is not None and "refused" in error

    box.down = False
    assert outbox.deliver(db, m) == "sent"
    assert outbox.deliver(db, m) == "sent" and len(box.sent) == 1
    assert _row(db, m)[1] is None


def test_two_senders_at_once_send_one_mail(db: Session, box: FakeMail) -> None:
    m = _queue(db)
    db.commit()
    slug = db.info["slug"]

    def send() -> None:
        with Session(routing.engine_for(slug)) as s:
            try:
                outbox.deliver(s, m)
            except Exception:  # noqa: BLE001, S110 - only the count matters
                pass

    threads = [threading.Thread(target=send) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(box.sent) == 1


def test_a_notification_link_is_in_the_mail_only_never_in_the_app_the_row_or_the_job(
    db: Session, box: FakeMail
) -> None:
    slug = db.info["slug"]
    uid = users.create_user(slug, f"n{uuid.uuid4().hex[:6]}@example.com", "correct horse battery", "Nia", "Buyer")
    notifications.notify(db, slug, uid, "task", "New task", "Due today.", email_extra="\nhttps://x.example/t/SECRET9")

    assert box.sent == []  # the worker sends notifications, as before
    worker.run_once(tenant=slug)
    assert len(box.sent) == 1 and "SECRET9" in box.sent[0]["body"] and "SECRET9" in box.sent[0]["html"]
    db.rollback()
    assert db.execute(text("select count(*) from email_message where body_text like '%SECRET9%'")).scalar_one() == 0
    assert db.execute(text("select count(*) from notification where body like '%SECRET9%'")).scalar_one() == 0
    with tenants.control_engine().connect() as c:
        assert c.execute(text("select count(*) from job where payload::text like '%SECRET9%'")).scalar_one() == 0


def test_the_sweep_queues_old_mail_again_and_fails_mail_unsent_after_a_day(db: Session, box: FakeMail) -> None:
    slug = db.info["slug"]
    users.create_user(slug, f"a{uuid.uuid4().hex[:6]}@example.com", "correct horse battery", "Ada", "Admin")
    late, stuck = _queue(db), _queue(db)
    db.execute(
        text("update email_message set queued_at = now() - interval '20 minutes' where message_id = :m"), {"m": late}
    )
    db.execute(
        text("update email_message set queued_at = now() - interval '25 hours' where message_id = :m"), {"m": stuck}
    )
    db.commit()
    with tenants.control_engine().begin() as c:
        c.execute(text("update job set status = 'failed' where tenant_slug = :t and kind = 'email'"), {"t": slug})

    got = outbox.sweep(db, slug, datetime.now(UTC))

    assert got == {"retried": 1, "failed": 1}
    waiting = [j["payload"]["message_id"] for j in _jobs(slug, "email") if j["status"] == "queued"]
    assert late in waiting and stuck not in waiting  # (the task's own notification mail waits too)
    assert _row(db, stuck)[:2] == ("failed", None)
    assert db.execute(text("select count(*) from task where kind = 'email_failed'")).scalar_one() == 1


# ---- job keys ----------------------------------------------------------------------------------------------------


def test_twenty_callers_at_once_queue_one_job_per_key_and_the_key_is_free_again_once_done(control_db: str) -> None:
    slug = "k" + uuid.uuid4().hex[:8]
    ids: list[int | None] = []

    def add() -> None:
        ids.append(worker.enqueue(slug, "noop", {"x": 1}, key="the-key"))

    threads = [threading.Thread(target=add) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    made = [i for i in ids if i is not None]
    assert len(made) == 1
    with tenants.control_engine().begin() as c:
        c.execute(text("update job set status = 'done' where id = :i"), {"i": made[0]})
    assert worker.enqueue(slug, "noop", {"x": 1}, key="the-key") is not None


def test_enqueue_unique_keeps_one_waiting_job_per_payload(control_db: str) -> None:
    slug = "k" + uuid.uuid4().hex[:8]
    now = datetime.now(UTC)
    assert worker.enqueue_unique(slug, "poll", {"mr": "A"}, now, same_payload=True) is not None
    assert worker.enqueue_unique(slug, "poll", {"mr": "A"}, now, same_payload=True) is None
    assert worker.enqueue_unique(slug, "poll", {"mr": "B"}, now, same_payload=True) is not None
    first = worker.enqueue_unique(slug, "daily", {}, now)
    assert first is not None and worker.enqueue_unique(slug, "daily", {}, now) is None
    assert worker.enqueue_unique(slug, "daily", {}, now, exclude_id=first) is not None  # the job reschedules itself
