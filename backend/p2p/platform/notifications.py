"""Notifications: an in-app row for the bell and list, and an email sent through the job worker."""

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from p2p.platform import mail, tenants, worker
from p2p.platform.control_models import AppUser


def email_of(user_id: int) -> str | None:
    engine = tenants.control_engine()
    with Session(engine) as s:
        return s.scalar(select(AppUser.email).where(AppUser.id == user_id))


def notify(
    db: Session,
    tenant: str,
    user_id: int,
    kind: str,
    title: str,
    body: str = "",
    *,
    link: str | None = None,
    email: bool = True,
) -> int:
    """Add a notification for a user. The email goes out through a job, so a mail outage never blocks the caller."""
    nid = int(
        db.execute(
            text(
                "insert into notification (user_id, kind, title, body, link) values (:u, :k, :t, :b, :l) returning id"
            ),
            {"u": user_id, "k": kind, "t": title, "b": body, "l": link},
        ).scalar_one()
    )
    db.commit()
    if email and (address := email_of(user_id)):
        worker.enqueue(tenant, "send_email", {"to": address, "subject": title, "body": body or title})
    return nid


def unread(db: Session, user_id: int) -> list[tuple[int, str, str]]:
    rows = db.execute(
        text("select id, kind, title from notification where user_id = :u and read_at is null order by id desc"),
        {"u": user_id},
    )
    return [(int(r[0]), str(r[1]), str(r[2])) for r in rows]


def mark_read(db: Session, user_id: int, notification_id: int) -> None:
    db.execute(
        text("update notification set read_at = now() where id = :i and user_id = :u and read_at is null"),
        {"i": notification_id, "u": user_id},
    )
    db.commit()


@worker.register("send_email")
def _send_email_job(ctx: worker.JobContext) -> None:
    mail.send_email(ctx.payload["to"], ctx.payload["subject"], ctx.payload["body"])
