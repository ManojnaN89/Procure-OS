"""The email that asks a person to decide a request: the summary, the lines, the estimated total, the budget, why it came
to them, and approve / reject / return buttons that are signed links working once (approvals/email_links.py).

A deterministic template: no AI step summarises requests, so no model writes or decides any of it. The same email goes out
when a step opens, as the one reminder, and when the step is escalated; only the opening line changes. The links are put in
the email only (the job that sends it), never in the app's notifications.
"""

from dataclasses import dataclass
from html import escape

from sqlalchemy.orm import Session

from p2p.approvals import email_links
from p2p.platform import audit, email_layout, notifications, worker

OPENING = {
    "new": "A request is waiting for your approval.",
    "reminder": "Reminder: this request is still waiting for your decision.",
    "escalated": "This request has waited too long for a decision, so it has come to you as well.",
}
SUBJECT = {
    "new": "Approve request {no} ({total})",
    "reminder": "Reminder: request {no} ({total}) is waiting for you",
    "escalated": "Escalated to you: request {no} ({total})",
}


@dataclass(frozen=True)
class Mail:
    subject: str
    text: str
    html: str


def compose(db: Session, tenant: str, no: str, round_no: int, step: int, user_id: int, kind: str = "new") -> Mail:
    from p2p.approvals import workflow  # noqa: PLC0415 - workflow sends this email

    f = email_links.facts(db, no, round_no, step, workflow.org_code_of(db, user_id))
    try:
        base = email_links.url(tenant, no, round_no, step, user_id)
        links = {a: f"{base}?do={a}" for a in email_links.ACTIONS}
    except email_links.LinkError:  # no signing secret: decide in the app
        base, links = "", {}
    code_note = (
        "Deciding from this email needs a one-time code: the page emails it to you. "
        "Or open the request in the app and sign in."
        if f["otp"]
        else ""
    )
    lines_text = "\n".join(
        f"  - {ln['item']}: {ln['qty']} {ln['uom']}" + (f" at {ln['price']} = {ln['amount']}" if ln["price"] else "")
        for ln in f["lines"]
    )
    text_parts = [
        OPENING[kind],
        "",
        f"Request {no}: {f['summary']}",
        *([f"Reason given: {f['justification']}"] if f["justification"] else []),
        "",
        "Lines:",
        lines_text,
        f"Estimated total: {f['total_inr']}",
        f"Budget: {f['budget']}",
        "",
        f"Why you: {f['why']}",
        *([code_note] if code_note else []),
        f"Open the request in the app: {f['app_url']}",
        "",
    ]
    if links:
        text_parts += [
            f"Open the request: {base}",
            f"Approve: {links['approve']}",
            f"Reject (asks for a reason): {links['reject']}",
            f"Send back for changes (asks for a reason): {links['return']}",
            "",
            f"Each link works once and expires in {email_links.EXPIRES_DAYS} days. Opening a link decides nothing.",
        ]
    else:
        text_parts.append("Open the request in the app to decide.")
    cell = f"padding:8px 10px;border-bottom:1px solid {email_layout.BORDER}"
    head = f"{cell};background:#FAFBFC;color:{email_layout.MUTED};font-size:12px;font-weight:600"
    rows = "".join(
        f"<tr><td style='{cell}'>{escape(ln['item'])}</td><td style='{cell}'>{escape(ln['qty'])} {escape(ln['uom'])}</td>"
        f"<td style='{cell};text-align:right'>{escape(ln['price'])}</td>"
        f"<td style='{cell};text-align:right'>{escape(ln['amount'])}</td></tr>"
        for ln in f["lines"]
    )
    buttons = (
        email_layout.button(links["approve"], "Approve", "success")
        + email_layout.button(links["reject"], "Reject", "danger")
        + email_layout.button(links["return"], "Send back for changes", "neutral")
        if links
        else "<p>Open the request in the app to decide.</p>"
    )
    fact = "<tr><td style='padding:3px 16px 3px 0;color:{m};vertical-align:top;white-space:nowrap'>{k}</td><td style='padding:3px 0'>{v}</td></tr>"
    facts = "".join(
        fact.format(m=email_layout.MUTED, k=escape(k), v=v)
        for k, v in (
            ("Estimated total", f"<strong>{escape(f['total_inr'])}</strong>"),
            ("Budget", escape(f["budget"])),
            ("Why you", escape(f["why"])),
        )
    )
    inner = (
        f"<p style='margin:0 0 12px'>{escape(OPENING[kind])}</p>"
        f"<p style='margin:0 0 12px'>{escape(f['summary'])}</p>"
        + (
            f"<p style='margin:0 0 12px'><b>Reason given:</b> {escape(f['justification'])}</p>"
            if f["justification"]
            else ""
        )
        + "<table role='presentation' style='border-collapse:collapse;width:100%;margin:0 0 16px;font-size:13px'>"
        f"<tr><th align='left' style='{head}'>Item</th><th align='left' style='{head}'>Quantity</th>"
        f"<th align='right' style='{head}'>Price</th><th align='right' style='{head}'>Amount</th></tr>{rows}</table>"
        f"<table role='presentation' style='border-collapse:collapse;margin:0 0 16px'>{facts}</table>"
        + (f"<p style='margin:0 0 12px'><b>{escape(code_note)}</b></p>" if code_note else "")
        + f"<p style='margin:8px 0 8px'>{buttons}</p>"
        + f"<p style='margin:0 0 12px'><a href='{escape(f['app_url'])}'>Open the request in the app</a></p>"
    )
    html = email_layout.wrap(
        f"Request {no}",
        inner,
        footer=f"Each button works once and expires in {email_links.EXPIRES_DAYS} days. Opening a link decides nothing.",
    )
    subject = SUBJECT[kind].format(no=no, total=f["total_inr"])
    return Mail(subject, "\n".join(text_parts).strip(), html)


def send(db: Session, tenant: str, no: str, round_no: int, step: int, user_id: int, kind: str = "new") -> bool:
    """Queue the email to the person deciding the step and record that it went (not the links). False if they have no
    email address."""
    address = notifications.email_of(user_id)
    if not address:
        return False
    m = compose(db, tenant, no, round_no, step, user_id, kind)
    worker.enqueue(tenant, "send_email", {"to": address, "subject": m.subject, "body": m.text, "html": m.html})
    audit.record(
        db, audit.SYSTEM, "request.approval_email", "request", no,
        after={"to": address, "round": round_no, "step": step, "kind": kind},
    )  # fmt: skip
    db.commit()
    return True
