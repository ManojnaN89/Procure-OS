"""Prospects on RFQs (D-03, task 4.4): inviting a registered prospect, and the qualification deadline of a conditional
award.

A buyer invites a prospect of the RFQ's category (R1). On an RFQ already out, the buyer's invitation is the confirmation
and the request goes to the new invitee only; on a draft it goes with the RFQ. A conditional award waits
`prospect_qualification_days` working days for the supplier's activation. When the time is up, or its activation is
rejected, the award lapses, the RFQ's buyer gets a task, and nothing is awarded to anyone else automatically (R10).
"""

from datetime import date, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.formulas.calendar import IST
from p2p.masters import prospects
from p2p.platform import audit, notifications, reference, tasks
from p2p.platform.sod import Actor, user
from p2p.sourcing import service

LAPSE_KIND = "prospect_lapsed"
SYSTEM = Actor("service", "prospect_deadline")


def invite(db: Session, rfq_no: str, supplier: str, user_id: int) -> dict[str, Any]:
    rfq = db.execute(text("select status, category_code from rfq where no = :n for update"), {"n": rfq_no}).first()
    if rfq is None:
        raise prospects.ProspectError(f"{rfq_no} does not exist", 404)
    if rfq[0] not in ("draft", "sent"):
        raise prospects.ProspectError(f"{rfq_no} is {rfq[0]}: no one more can be invited", 409)
    status = db.execute(text("select status from supplier where code = :c"), {"c": supplier}).scalar()
    if status is None:
        raise prospects.ProspectError(f"supplier {supplier} does not exist", 404)
    if status != "prospect":
        raise prospects.ProspectError(f"{supplier} is {status}, not a prospect")
    asked = db.execute(
        text("select 1 from supplier_category where supplier_code = :s and category_code = :c"),
        {"s": supplier, "c": rfq[1]},
    ).first()
    if asked is None:
        raise prospects.ProspectError(f"{supplier} is not registered for category {rfq[1]}")
    if db.execute(
        text("select 1 from rfq_supplier where rfq_no = :r and supplier_code = :s"), {"r": rfq_no, "s": supplier}
    ).first():
        raise prospects.ProspectError(f"{supplier} is already invited to {rfq_no}", 409)
    email = db.execute(
        text(
            "select email from supplier_contact where supplier_code = :c and email is not null "
            "order by is_primary desc nulls last, id limit 1"
        ),
        {"c": supplier},
    ).scalar()
    if not email:
        raise prospects.ProspectError(f"{supplier} has no email address on file")
    me = prospects.person(db, user_id)
    db.execute(
        text("insert into rfq_supplier (rfq_no, supplier_code, email, invited_by) values (:r, :s, :e, :by)"),
        {"r": rfq_no, "s": supplier, "e": email, "by": me},
    )
    db.execute(
        text("update supplier set invited_by = coalesce(invited_by, :by) where code = :c"), {"by": me, "c": supplier}
    )
    audit.record(db, user(user_id), "rfq.prospect_invite", "rfq", rfq_no, after={"supplier": supplier, "email": email})
    db.commit()
    sent = service.send_rfq(db, rfq_no, user(user_id)) if rfq[0] == "sent" else 0
    waiting = db.execute(
        text("select 1 from rfq q join proposal p on p.id = q.proposal_id where q.no = :n and p.status = 'pending'"),
        {"n": rfq_no},
    ).first()
    if rfq[0] == "draft" and waiting is None:  # an RFQ that nobody approved could be asked: now it can be sent (D42)
        service.confirm_send(db, rfq_no, user(user_id))
        sent = int(db.execute(text("select status = 'sent' from rfq where no = :n"), {"n": rfq_no}).scalar() or 0)
    return {"rfq_no": rfq_no, "supplier_code": supplier, "email": email, "invited_by": me, "sent": sent > 0}


def prospects_for(db: Session, rfq_no: str) -> list[dict[str, Any]]:
    """The prospects of the RFQ's category not yet invited, for the 'Invite a prospect' choice."""
    return [
        dict(r)
        for r in db.execute(
            text(
                "select s.code, coalesce(s.trade_name, s.legal_name) as name, s.identity_checked_at is not null as checked "
                "from supplier s join supplier_category c on c.supplier_code = s.code join rfq r on r.no = :n "
                "and r.category_code = c.category_code where s.status = 'prospect' and not exists (select 1 from "
                "rfq_supplier x where x.rfq_no = r.no and x.supplier_code = s.code) order by s.code"
            ),
            {"n": rfq_no},
        ).mappings()
    ]


def _deadline_days(db: Session) -> int:
    value = db.execute(text("select value from org_setting where key = 'prospect_qualification_days'")).scalar()
    return int(value) if value else 10


def deadline(db: Session, awarded_on: date) -> date:
    """The day the conditional award lapses when the supplier is still not active."""
    return reference.calendar_for(db).add_working_days(awarded_on, _deadline_days(db))


def _rejected_since(db: Session, supplier: str, since: datetime) -> bool:
    return (
        db.execute(
            text(
                "select 1 from proposal where action = 'supplier.set_status' and status = 'rejected' "
                "and payload->>'supplier' = :s and payload->>'to' = 'active' and decided_at >= :t limit 1"
            ),
            {"s": supplier, "t": since},
        ).first()
        is not None
    )


def lapse_due(db: Session, tenant: str, today: date) -> list[str]:
    """Mark conditional awards lapsed whose supplier was not activated in time (or whose activation was rejected);
    tell the RFQ's buyer with a task. Nothing is awarded to anyone else. Returns the RFQ lines that lapsed."""
    lapsed: list[str] = []
    rows = db.execute(
        text(
            "select a.rfq_no, a.rfq_line_no, a.supplier_code, a.at, a.awarded_on, r.buyer, s.status from award a "
            "join rfq r on r.no = a.rfq_no join supplier s on s.code = a.supplier_code "
            "where a.conditional and a.lapsed_at is null and a.status = 'final' and s.status <> 'active' "
            "and not exists (select 1 from po_line p where p.source_ref = a.quote_no and p.status <> 'cancelled') "
            "order by a.rfq_no, a.rfq_line_no"
        )
    ).mappings().all()  # fmt: skip
    for a in rows:
        awarded_on = a["awarded_on"] or a["at"].astimezone(IST).date()  # the business date it was made on
        due = deadline(db, awarded_on)
        rejected = _rejected_since(db, str(a["supplier_code"]), a["at"])
        if today < due and not rejected:
            continue
        why = (
            "its activation was rejected"
            if rejected
            else f"not activated within {_deadline_days(db)} working days (by {due:%d %b %Y})"
        )
        db.execute(
            text(
                "update award set lapsed_at = now(), lapse_reason = :w where rfq_no = :r and rfq_line_no = :l "
                "and supplier_code = :s"
            ),
            {"w": why, "r": a["rfq_no"], "l": a["rfq_line_no"], "s": a["supplier_code"]},
        )
        db.execute(text("update rfq set status = 'sent' where no = :n and status = 'awarded'"), {"n": a["rfq_no"]})
        audit.record(
            db, SYSTEM, "award.lapsed", "rfq", a["rfq_no"],
            after={"line": a["rfq_line_no"], "supplier": a["supplier_code"]}, reason=why,
        )  # fmt: skip
        db.commit()
        buyer = notifications.login_of(db, tenant, a["buyer"]) if a["buyer"] else None
        if buyer is None:
            buyer = int(
                db.execute(
                    text("select user_id from user_profile where role = 'Buyer' order by user_id limit 1")
                ).scalar()
                or 0
            )
        tasks.create_task(
            db, tenant, kind=LAPSE_KIND,
            title=f"{a['rfq_no']} line {a['rfq_line_no']}: the conditional award to {a['supplier_code']} lapsed",
            assignee=buyer, sla_hours=48,
            payload={"rfq": a["rfq_no"], "line": a["rfq_line_no"], "supplier": a["supplier_code"], "why": why,
                     "next": "Review the valid quotes left, record a reason and select again; nothing was awarded "
                     "automatically"},
            actor=SYSTEM, email=False,
        )  # fmt: skip
        lapsed.append(f"{a['rfq_no']}/{a['rfq_line_no']}")
    return lapsed
