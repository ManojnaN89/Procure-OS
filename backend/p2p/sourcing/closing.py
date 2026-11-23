"""The closing of a request for quotation (task 12.6, block 6): extensions and the no-response case.

An RFQ closes at the end of its due date, India time (OP-1); a first quote received later is refused (orders/quotes.py).
A buyer may move the closing date to a later working day with a reason, as often as needed (OP-5): every invited
supplier that has not declined is told in fixed words. The daily reminder job also looks at every RFQ that closed
without a selection: when fewer quotes count than the RFQ wants (OP-7), the RFQ's buyer gets one case per closing
(a `rfq_no_response` task, escalating to the purchase manager). Nothing else is automatic (AT-10): no mail to a
supplier, no extension, no award, no new invitation. The case is completed when the RFQ is extended, fully awarded,
its award is proposed (a period RFQ) or it is cancelled.
"""

from datetime import date
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.formulas.words import count
from p2p.platform import audit, emails, notifications, reference, tasks, users
from p2p.platform.sod import Actor

ACTOR = Actor("service", "rfq-closing")
NO_RESPONSE = "rfq_no_response"
NO_RESPONSE_SLA_HOURS = 18  # two working days
NEXT = "Extend the closing date, select from the quotes received with a reason, or change the lines' route"


class ClosingError(Exception):
    def __init__(self, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.status = status


def buyer_of(db: Session, tenant: str, rfq_no: str) -> int:
    """The login of the RFQ's buyer, else the first buyer of the company."""
    code = db.execute(text("select buyer from rfq where no = :n"), {"n": rfq_no}).scalar()
    found = notifications.login_of(db, tenant, str(code)) if code else None
    return found if found is not None else users.first_with_role(db, "Buyer")


def _purchase_manager(db: Session, tenant: str) -> list[int]:
    for (code,) in db.execute(
        text("select code from org_user where active and lower(designation) = 'purchase manager' order by code")
    ).all():
        uid = notifications.login_of(db, tenant, str(code))
        if uid is not None:
            return [uid]
    return []


# ---- extending ------------------------------------------------------------------------------------------------------


def extend(db: Session, rfq_no: str, user_id: int, new_date: date, reason: str, *, today: date) -> dict[str, Any]:
    """Move the closing date of an RFQ that is out to a later working day, and tell the suppliers. Commits."""
    if not (reason or "").strip():
        raise ClosingError("a reason is needed to extend the closing date")
    rfq = db.execute(
        text("select status, due_date, delivery_location from rfq where no = :n for update"), {"n": rfq_no}
    ).first()
    if rfq is None:
        raise ClosingError(f"{rfq_no} does not exist", 404)
    if rfq[0] != "sent":
        raise ClosingError(f"{rfq_no} is {rfq[0]}; only an RFQ that is out with the suppliers can be extended", 409)
    if new_date <= today:
        raise ClosingError(f"the new closing date must be after today ({today:%d %b %Y})")
    if not reference.calendar_for_location(db, rfq[2]).is_working_day(new_date):
        raise ClosingError(f"{new_date:%d %b %Y} is not a working day")
    if new_date <= rfq[1]:
        raise ClosingError(f"the new closing date must be after the current one ({rfq[1]:%d %b %Y})")
    db.execute(text("update rfq set due_date = :d where no = :n"), {"d": new_date, "n": rfq_no})
    mailed = _tell_suppliers(db, rfq_no, new_date)
    actor = Actor("user", str(user_id))
    audit.record(
        db, actor, "rfq.extend", "rfq", rfq_no, before={"due_date": rfq[1]},
        after={"from": rfq[1], "to": new_date, "due_date": new_date, "mailed": mailed}, reason=reason.strip(),
    )  # fmt: skip
    emails.commit_and_send(db)
    settle(db, rfq_no, "the closing date was extended")
    return {"due_date": new_date.isoformat(), "mailed": mailed}


def _tell_suppliers(db: Session, rfq_no: str, new_date: date) -> int:
    from p2p.sourcing import service, templates  # noqa: PLC0415 - the service imports this module's neighbours

    company = service.company_name(db)
    rows = db.execute(
        text(
            "select rs.email, (select c.name from supplier_contact c where c.supplier_code = rs.supplier_code "
            "and lower(c.email) = lower(rs.email) limit 1) from rfq_supplier rs where rs.rfq_no = :n "
            "and rs.invited_at is not null and not rs.declined and rs.email is not null order by rs.supplier_code"
        ),
        {"n": rfq_no},
    ).all()
    subject = f"Closing date extended: request for quotation {rfq_no}"
    for address, contact in rows:
        body = templates.render(
            templates.EXTENSION,
            {"contact": contact or "Sir or Madam", "reference": rfq_no, "due_date": f"{new_date:%d %b %Y}",
             "company": company},
        )  # fmt: skip
        emails.send_linked_email(db, str(address), subject, body, linked_type="rfq", linked_ref=rfq_no)
    return len(rows)


# ---- the no-response case -------------------------------------------------------------------------------------------


def settle(db: Session, rfq_no: str, why: str) -> int:
    """Complete the RFQ's open no-response cases (extended, awarded, proposed or cancelled). Commits."""
    ids = [
        int(r[0])
        for r in db.execute(
            text("select id from task where kind = :k and status in ('open', 'overdue') and payload->>'rfq_no' = :n"),
            {"k": NO_RESPONSE, "n": rfq_no},
        )
    ]
    for task_id in ids:
        audit.record(db, ACTOR, "rfq.no_response_settled", "task", task_id, reason=why)
        tasks.complete_task(db, task_id, None)
    return len(ids)


def _closed_without_selection(db: Session, as_of: date) -> list[dict[str, Any]]:
    return [
        dict(r)
        for r in db.execute(
            text(
                "select r.no, r.due_date from rfq r where r.status = 'sent' and r.due_date < :d and not exists ("
                "select 1 from award a where a.rfq_no = r.no and a.lapsed_at is null "
                "and a.status in ('final', 'approved', 'proposed')) order by r.no"
            ),
            {"d": as_of},
        ).mappings()
    ]


def run(db: Session, tenant: str, as_of: date) -> list[str]:
    """Every RFQ out with the suppliers that closed before `as_of` without a selection: its ranking is written, and a
    case opens when too few quotes count (once per closing). Returns the RFQs a case was opened for."""
    from p2p.orders import quotes  # noqa: PLC0415 - orders build on sourcing
    from p2p.sourcing import notes  # noqa: PLC0415

    for (rfq_no,) in db.execute(
        text(
            "select distinct t.payload->>'rfq_no' from task t join rfq r on r.no = t.payload->>'rfq_no' "
            "where t.kind = :k and t.status in ('open', 'overdue') and r.status <> 'sent'"
        ),
        {"k": NO_RESPONSE},
    ).all():
        settle(db, str(rfq_no), "the RFQ is no longer out with the suppliers")
    opened: list[str] = []
    for rfq in _closed_without_selection(db, as_of):
        rfq_no, due = str(rfq["no"]), rfq["due_date"]
        notes.on_quote(db, rfq_no)
        comp = quotes.comparison(db, rfq_no, as_of)
        if comp.counting >= comp.min_quotes:
            continue
        if db.execute(
            text("select 1 from task where kind = :k and payload->>'rfq_no' = :n and payload->>'due_date' = :d"),
            {"k": NO_RESPONSE, "n": rfq_no, "d": due.isoformat()},
        ).first():
            continue  # once per closing
        _open_case(db, tenant, rfq_no, due, comp)
        opened.append(rfq_no)
    return opened


def _open_case(db: Session, tenant: str, rfq_no: str, due: date, comp: Any) -> int:
    invited = db.execute(
        text("select supplier_code, declined from rfq_supplier where rfq_no = :n order by supplier_code"), {"n": rfq_no}
    ).all()
    answered = sorted(s["supplier_code"] for s in comp.suppliers)
    declined = sorted(str(r[0]) for r in invited if r[1])
    silent = sorted(str(r[0]) for r in invited if not r[1] and str(r[0]) not in answered)
    k = comp.counting
    title = (
        f"{rfq_no} closed on {due:%d %b %Y} with no quotes"
        if k == 0
        else f"{rfq_no} closed on {due:%d %b %Y} with {k} of {comp.min_quotes} quotes wanted"
    )
    payload = {
        "rfq_no": rfq_no, "due_date": due.isoformat(), "answered": answered, "silent": silent, "declined": declined,
        "counting": k, "min_quotes": comp.min_quotes, "next": NEXT, "link": f"#/rfqs/{rfq_no}",
    }  # fmt: skip
    audit.record(
        db, ACTOR, "rfq.no_response", "rfq", rfq_no, after=payload,
        reason=f"{count(k, 'quote')} counted at closing; {comp.min_quotes} wanted",
    )  # fmt: skip
    db.commit()
    return tasks.create_task(
        db, tenant, kind=NO_RESPONSE, title=title, assignee=buyer_of(db, tenant, rfq_no),
        sla_hours=NO_RESPONSE_SLA_HOURS, escalation_chain=_purchase_manager(db, tenant), payload=payload,
        actor=ACTOR, email=False,
    )  # fmt: skip
