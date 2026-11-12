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
from p2p.platform import audit, notifications, worker

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


def _button(href: str, label: str, colour: str) -> str:
    return (
        f'<a href="{escape(href)}" style="display:inline-block;padding:10px 18px;margin:0 8px 8px 0;border-radius:6px;'
        f'background:{colour};color:#ffffff;text-decoration:none;font-weight:600">{escape(label)}</a>'
    )


def compose(db: Session, tenant: str, no: str, round_no: int, step: int, user_id: int, kind: str = "new") -> Mail:
    from p2p.approvals import workflow  # noqa: PLC0415 - workflow sends this email

    f = email_links.facts(db, no, round_no, step, workflow.org_code_of(db, user_id))
    try:
        base = email_links.url(tenant, no, round_no, step, user_id)
        links = {a: f"{base}?do={a}" for a in email_links.ACTIONS}
    except email_links.LinkError:  # no signing secret: decide in the app
        base, links = "", {}
    code_note = (
        "Approving needs a one-time code (the request is above the limit); the page emails it to you."
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
    rows = "".join(
        f"<tr><td>{escape(ln['item'])}</td><td>{escape(ln['qty'])} {escape(ln['uom'])}</td>"
        f"<td style='text-align:right'>{escape(ln['price'])}</td><td style='text-align:right'>{escape(ln['amount'])}</td></tr>"
        for ln in f["lines"]
    )
    buttons = (
        _button(links["approve"], "Approve", "#2f9e6e")
        + _button(links["reject"], "Reject", "#b4443c")
        + _button(links["return"], "Send back for changes", "#5b6b7f")
        if links
        else "<p>Open the request in the app to decide.</p>"
    )
    html = (
        "<div style='font-family:system-ui,sans-serif;font-size:14px;color:#1d2733;max-width:620px'>"
        f"<p>{escape(OPENING[kind])}</p><h2 style='font-size:18px'>Request {escape(no)}</h2>"
        f"<p>{escape(f['summary'])}</p>"
        + (f"<p><b>Reason given:</b> {escape(f['justification'])}</p>" if f["justification"] else "")
        + "<table style='border-collapse:collapse;width:100%' cellpadding='6' border='1' bordercolor='#d5dbe3'>"
        "<tr><th align='left'>Item</th><th align='left'>Quantity</th><th align='right'>Price</th><th align='right'>Amount</th></tr>"
        f"{rows}</table><p><b>Estimated total:</b> {escape(f['total_inr'])}</p>"
        f"<p><b>Budget:</b> {escape(f['budget'])}</p><p><b>Why you:</b> {escape(f['why'])}</p>"
        + (f"<p><b>{escape(code_note)}</b></p>" if code_note else "")
        + f"<p>{buttons}</p><p style='color:#6b7785;font-size:12px'>Each button works once and expires in "
        f"{email_links.EXPIRES_DAYS} days. Opening a link decides nothing.</p></div>"
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
