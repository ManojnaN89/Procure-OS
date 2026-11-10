"""The page behind the link in an approval email: look at the request, then approve or reject.

No sign-in: the signed link is the key (approvals/email_links.py). Opening the page changes nothing; the decision is a
separate button. A page that is not for this person any more, or a link that was altered or has expired, says so and does
nothing. Wrong links are counted per address, so guessing is stopped.
"""

from html import escape
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from p2p.approvals import email_links
from p2p.platform import ratelimit, routing

router = APIRouter(tags=["approval links"])
WINDOW = 600.0
MAX_BAD = 30

_STYLE = (
    "body{font-family:system-ui,sans-serif;background:#10151c;color:#e8edf3;margin:0;padding:24px}"
    "main{max-width:560px;margin:0 auto}h1{font-size:20px}table{width:100%;border-collapse:collapse;margin:12px 0}"
    "td,th{padding:6px 8px;border-bottom:1px solid #2a3340;text-align:left;font-size:14px}"
    "textarea{width:100%;box-sizing:border-box;background:#18202b;color:inherit;border:1px solid #2a3340;border-radius:6px;padding:8px}"
    "button{padding:9px 16px;border-radius:6px;border:0;font-size:14px;cursor:pointer;margin-right:8px}"
    ".ok{background:#2f9e6e;color:#fff}.no{background:#b4443c;color:#fff}.muted{color:#93a0b1;font-size:13px}"
)
_HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Robots-Tag": "noindex"}


def _page(title: str, body: str, status: int = 200) -> HTMLResponse:
    html = (
        f"<!doctype html><html lang=en><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>{escape(title)}</title><style>{_STYLE}</style><main><h1>{escape(title)}</h1>{body}</main></html>"
    )
    return HTMLResponse(html, status_code=status, headers=_HEADERS)


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


@router.get("/approve/{token}", response_class=HTMLResponse)
def show(token: str, request: Request) -> HTMLResponse:
    try:
        key = _guard(request)
        link = _link(token, key)
        with Session(routing.engine_for(link.tenant)) as db:
            try:
                data = email_links.summary(db, link)
            except email_links.LinkError as exc:
                raise _Refused(409, str(exc)) from None
    except _Refused as error:
        return _refuse(error)
    rows = "".join(
        f"<tr><td>{escape(r['item'])}</td><td>{escape(r['qty'])} {escape(r['uom'])}</td></tr>" for r in data["lines"]
    )
    body = (
        f"<p>From {escape(data['requester'])}. Total <b>{escape(data['total'])}</b>. "
        f"<span class=muted>Rule {escape(data['rule'])}</span></p>"
        + (f"<p>{escape(data['why'])}</p>" if data["why"] else "")
        + f"<table><tr><th>Item</th><th>Quantity</th></tr>{rows}</table>"
        f"<form method=post><p class=muted>A reason is needed to reject, and welcome when you approve.</p>"
        f"<textarea name=reason rows=3 maxlength=500></textarea><p>"
        f"<button class=ok name=action value=approve>Approve</button>"
        f"<button class=no name=action value=reject>Reject</button></p></form>"
        f"<p class=muted>This link works once. To change a quantity, open the request in the app.</p>"
    )
    return _page(f"Request {data['no']}", body)


@router.post("/approve/{token}", response_class=HTMLResponse)
def decide(
    token: str, request: Request, action: Annotated[str, Form()], reason: Annotated[str, Form()] = ""
) -> HTMLResponse:
    try:
        key = _guard(request)
        link = _link(token, key)
        with Session(routing.engine_for(link.tenant)) as db:
            try:
                status = email_links.decide(db, link, action, reason.strip()[:500] or None)
            except email_links.LinkError as exc:
                raise _Refused(409, str(exc)) from None
    except _Refused as error:
        return _refuse(error)
    done = {
        "approved": "Approved. The buyer takes it from here.",
        "submitted": "Approved. It goes on to the next approver.",
        "rejected": "Rejected. The requester has been told.",
    }.get(status, f"Recorded: {status}.")
    return _page(f"Request {link.request_no}", f"<p>{escape(done)}</p><p class=muted>You can close this page.</p>")
