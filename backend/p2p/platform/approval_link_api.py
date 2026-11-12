"""The page behind the links in an approval email: look at the request, then approve, reject or send it back.

No sign-in: the signed link is the key (approvals/email_links.py). Opening the page changes nothing; the decision is a
separate button. The email's buttons open the same page for one decision (`?do=approve|reject|return`); rejecting or
sending back asks for a reason there. Approving above the code limit asks for a one-time code, emailed on request. A page
that is not for this person any more, or a link that was altered or has expired, says so and does nothing. Wrong links are
counted per address, so guessing is stopped.
"""

from html import escape
from typing import Annotated, Any

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from p2p.approvals import email_links
from p2p.platform import public_page, ratelimit, routing

router = APIRouter(tags=["approval links"])
WINDOW = 600.0
MAX_BAD = 30

_BUTTONS = {
    "approve": "<button class=ok name=action value=approve>Approve</button>",
    "reject": "<button class=no name=action value=reject>Reject</button>",
    "return": "<button class=back name=action value=return>Send back for changes</button>",
}


_page = public_page.page


def _address(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _guard(request: Request) -> str:
    key = f"approve-link-bad:{_address(request)}"
    if ratelimit.count(key, WINDOW) >= MAX_BAD:
        raise _Refused(429, "Too many wrong links from this address; try again in a little while.")
    return key


class _Refused(Exception):
    def __init__(self, status: int, message: str) -> None:
        self.status, self.message = status, message


def _refuse(error: _Refused) -> HTMLResponse:
    return _page("This link cannot be used", f"<p>{escape(error.message)}</p>", error.status)


def _link(token: str, key: str) -> email_links.Link:
    try:
        return email_links.read(token)
    except email_links.LinkError as exc:
        ratelimit.add(key)
        raise _Refused(400, str(exc)) from None


def _facts(token: str, request: Request) -> tuple[email_links.Link, dict[str, Any]]:
    link = _link(token, _guard(request))
    with Session(routing.engine_for(link.tenant)) as db:
        try:
            return link, email_links.summary(db, link)
        except email_links.LinkError as exc:
            raise _Refused(409, str(exc)) from None


def _form(token: str, data: dict[str, Any], do: str | None, note: str = "") -> HTMLResponse:
    rows = "".join(
        f"<tr><td>{escape(r['item'])}</td><td>{escape(r['qty'])} {escape(r['uom'])}</td><td>{escape(r['amount'])}</td></tr>"
        for r in data["lines"]
    )
    actions = [do] if do in email_links.ACTIONS else list(email_links.ACTIONS)
    needs_reason = "approve" not in actions
    code = (
        "<p class=note>Approving needs the one-time code we email you (it is above the limit). "
        f"<button class=plain formaction='/approve/{escape(token)}/code' formmethod=post formnovalidate>"
        "Email me a code</button></p><input name=code inputmode=numeric autocomplete=one-time-code maxlength=6 "
        "placeholder='Six-digit code'>"
        if data["otp"] and "approve" in actions
        else ""
    )
    prompt = (
        "Say why (the requester sees it)."
        if needs_reason
        else "A reason is needed to reject or send back, and welcome when you approve."
    )
    body = (
        f"<p>{escape(data['summary'])}</p>"
        + (f"<p>{escape(data['justification'])}</p>" if data["justification"] else "")
        + f"<table><tr><th>Item</th><th>Quantity</th><th>Amount</th></tr>{rows}</table>"
        f"<table class=facts><tr><td>Estimated total</td><td><b>{escape(data['total_inr'])}</b></td></tr>"
        f"<tr><td>Budget</td><td>{escape(data['budget'])}</td></tr>"
        f"<tr><td>Why you</td><td>{escape(data['why'])}</td></tr></table>"
        + (f"<p class=note>{escape(note)}</p>" if note else "")
        + f"<form method=post action='/approve/{escape(token)}'>{code}"
        f"<label for=reason>Reason</label><p class=muted>{escape(prompt)}</p>"
        f"<textarea id=reason name=reason rows=3 maxlength=500{' required' if needs_reason else ''}></textarea><p>"
        + "".join(_BUTTONS[a] for a in actions)
        + "</p></form><p class=muted>This link works once. To change a quantity, open the request in the app.</p>"
    )
    return _page(f"Request {data['no']}", body)


@router.get("/approve/{token}", response_class=HTMLResponse)
def show(token: str, request: Request, do: str | None = None) -> HTMLResponse:
    try:
        _, data = _facts(token, request)
    except _Refused as error:
        return _refuse(error)
    return _form(token, data, do)


@router.post("/approve/{token}/code", response_class=HTMLResponse)
def send_code(token: str, request: Request) -> HTMLResponse:
    """Email the approver a one-time code, then show the approval form again to type it in."""
    try:
        link, data = _facts(token, request)
        with Session(routing.engine_for(link.tenant)) as db:
            try:
                address = email_links.send_code(db, link)
            except email_links.LinkError as exc:
                return _form(token, data, "approve", str(exc))
    except _Refused as error:
        return _refuse(error)
    return _form(token, data, "approve", f"A code was sent to {address}. It works once and expires in 10 minutes.")


@router.post("/approve/{token}", response_class=HTMLResponse)
def decide(
    token: str,
    request: Request,
    action: Annotated[str, Form()],
    reason: Annotated[str, Form()] = "",
    code: Annotated[str, Form()] = "",
) -> HTMLResponse:
    try:
        key = _guard(request)
        link = _link(token, key)
        with Session(routing.engine_for(link.tenant)) as db:
            try:
                status = email_links.decide(db, link, action, reason.strip()[:500] or None, code.strip()[:12] or None)
            except email_links.LinkError as exc:
                raise _Refused(409, str(exc)) from None
    except _Refused as error:
        return _refuse(error)
    done = {
        "approved": "Approved. The buyer takes it from here.",
        "submitted": "Approved. It goes on to the next approver.",
        "rejected": "Rejected. The requester has been told.",
        "returned": "Sent back for changes. The requester has been told.",
    }.get(status, f"Recorded: {status}.")
    return _page(
        f"Request {link.request_no}", f"<p class=done>{escape(done)}</p><p class=muted>You can close this page.</p>"
    )
