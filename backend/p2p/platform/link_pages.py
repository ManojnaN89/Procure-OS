"""The plain pages behind signed links that need no login (an approver's decision, a supplier's answer to an order).

One look for all of them, headers that keep the page out of caches, search engines and referrers, and a count of wrong
links per address so that guessing is stopped.
"""

from html import escape

from fastapi import Request
from fastapi.responses import HTMLResponse

from p2p.platform import ratelimit

WINDOW = 600.0
MAX_BAD = 30

STYLE = (
    "body{font-family:system-ui,sans-serif;background:#10151c;color:#e8edf3;margin:0;padding:24px}"
    "main{max-width:640px;margin:0 auto}h1{font-size:20px}h2{font-size:16px;margin-top:22px}"
    "table{width:100%;border-collapse:collapse;margin:12px 0}"
    "td,th{padding:6px 8px;border-bottom:1px solid #2a3340;text-align:left;font-size:14px}"
    "textarea,input{width:100%;box-sizing:border-box;background:#18202b;color:inherit;border:1px solid #2a3340;"
    "border-radius:6px;padding:8px;font-size:14px}"
    "button{padding:9px 16px;border-radius:6px;border:0;font-size:14px;cursor:pointer;margin:0 8px 8px 0}"
    ".ok{background:#2f9e6e;color:#fff}.no{background:#b4443c;color:#fff}.back{background:#5b6b7f;color:#fff}"
    ".plain{background:#2a3340;color:#e8edf3}.muted{color:#93a0b1;font-size:13px}.note{color:#f0c36a}"
    "label{display:block;font-size:13px;color:#93a0b1;margin:8px 0 4px}"
)
HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Robots-Tag": "noindex"}


class Refused(Exception):
    def __init__(self, status: int, message: str) -> None:
        self.status, self.message = status, message


def page(title: str, body: str, status: int = 200) -> HTMLResponse:
    html = (
        f"<!doctype html><html lang=en><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>{escape(title)}</title><style>{STYLE}</style><main><h1>{escape(title)}</h1>{body}</main></html>"
    )
    return HTMLResponse(html, status_code=status, headers=HEADERS)


def refuse(error: Refused) -> HTMLResponse:
    return page("This link cannot be used", f"<p>{escape(error.message)}</p>", error.status)


def guard(request: Request, kind: str) -> str:
    """The key wrong links from this address are counted under; refused once there were too many."""
    key = f"{kind}-bad:{request.client.host if request.client else 'unknown'}"
    if ratelimit.count(key, WINDOW) >= MAX_BAD:
        raise Refused(429, "Too many wrong links from this address; try again in a little while.")
    return key


def bad_link(key: str, message: str) -> Refused:
    """Count a wrong link against the address and say why it cannot be used."""
    ratelimit.add(key)
    return Refused(400, message)
