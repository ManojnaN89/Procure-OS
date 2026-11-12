"""Notifications: an in-app row for the bell and list, and an email sent through the job worker."""

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from p2p.platform import mail, tenants, users, worker
from p2p.platform.control_models import AppUser


def login_of(db: Session, tenant: str, org_code: str | None) -> int | None:
    """The login of a person in the organisation, found by the email on their record."""
    if not org_code:
        return None
    email = db.execute(text("select email from org_user where code = :c and active"), {"c": org_code}).scalar()
    return users.user_id_by_email(tenant, str(email)) if email else None


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
    email_extra: str = "",
) -> int:
    """Add a notification for a user. The email goes out through a job, so a mail outage never blocks the caller.
    `email_extra` is added to the email only, never kept in the app (for a link that works as a key)."""
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
        worker.enqueue(
            tenant, "send_email", {"to": address, "subject": title, "body": ((body or title) + email_extra).strip()}
        )
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
    html = ctx.payload.get("html")
    if html:
        mail.send_email(ctx.payload["to"], ctx.payload["subject"], ctx.payload["body"], html=html)
    else:
        mail.send_email(ctx.payload["to"], ctx.payload["subject"], ctx.payload["body"])
