"""The page behind a supplier's contract link (task 12.3, D53): read the rate contract, ask for a code, accept or decline.

No sign-in: the signed link is the key (masters/contract_acceptance.py), and every decision also needs the one-time code
sent to the address the award letter went to, so a forwarded link decides nothing. Opening the page changes nothing.
A link that was altered, has expired or was used says so; wrong links are counted per address, so guessing is stopped.
A company in cutover or suspended takes no action through a link.
"""

from html import escape
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from p2p.formulas.money import format_inr
from p2p.masters import contract_acceptance as acceptance
from p2p.platform import otp, routing
from p2p.platform.link_pages import Refused, bad_link, guard, open_company, page, refuse

router = APIRouter(tags=["contract links"])


def _link(token: str, request: Request) -> acceptance.Link:
    key = guard(request, "contract-link")
    try:
        link = acceptance.read(token)
    except acceptance.LinkError as exc:
        raise bad_link(key, str(exc)) from None
    open_company(link.tenant)
    return link


def _form(token: str, c: dict[str, Any], note: str = "", done: str = "") -> str:
    rows = "".join(
        f"<tr><td>{escape(str(p['name'] or p['item_code']))}</td><td>{escape(str(p['uom'] or ''))}</td>"
        f"<td>{escape(str(p['lead_days'] or ''))}</td><td>{escape(format_inr(p['price']))}</td></tr>"
        for p in c["prices"]
    )
    action = f"/contract/{escape(token)}"
    places = ", ".join(dict.fromkeys(str(p["place"]) for p in c["prices"] if p["place"]))
    ceiling = format_inr(c["max_value"]) + " before tax" if c["max_value"] is not None else "none"
    return (
        (f"<p class=note>{escape(note)}</p>" if note else "")
        + (f"<p class=done>{escape(done)}</p>" if done else "")
        + f"<p>{escape(str(c['company']))} offers {escape(str(c['supplier_name']))} rate contract "
        f"{escape(str(c['code']))}. It is a notice of selection, not an order: orders follow during the period.</p>"
        "<table class=facts>"
        f"<tr><td>Period</td><td>{c['valid_from']:%d %b %Y} to {c['valid_to']:%d %b %Y}</td></tr>"
        f"<tr><td>Delivered to</td><td>{escape(places or 'as agreed')}</td></tr>"
        f"<tr><td>Ceiling</td><td>{escape(ceiling)}</td></tr>"
        f"<tr><td>Payment terms</td><td>{escape(str(c['terms_name'] or c['payment_terms_code'] or 'as agreed'))}</td></tr>"
        "</table>"
        f"<table><tr><th>Item</th><th>Unit</th><th>Lead days</th><th>Price</th></tr>{rows}</table>"
        f"<h2>1. Get a code</h2><form method=post action='{action}/code'><p class=muted>We email a one-time code to the "
        "address this offer was sent to. It works once and expires in 10 minutes.</p>"
        "<p><button class=plain>Email me a code</button></p></form>"
        f"<h2>2. Accept or decline</h2><form method=post action='{action}'>"
        "<label for=code>The code from the email</label><input id=code name=code inputmode=numeric maxlength=6 required>"
        "<label for=reason>If you decline: why (the buyer sees it)</label>"
        "<textarea id=reason name=reason rows=2 maxlength=500></textarea>"
        "<p><button class=ok name=action value=accept>Accept the contract</button>"
        "<button class=no name=action value=decline>Decline</button></p></form>"
        "<p class=muted>This link works once. You can also reply to the award email.</p>"
    )


def _show(token: str, link: acceptance.Link, note: str = "", done: str = "", status: int = 200) -> HTMLResponse:
    with Session(routing.engine_for(link.tenant)) as db:
        try:
            acceptance.check(db, link)
        except acceptance.LinkError as exc:
            raise Refused(409, str(exc)) from None
        c = acceptance.terms(db, link.contract)
    return page(f"Rate contract {link.contract}", _form(token, c, note, done), status)


@router.get("/contract/{token}", response_class=HTMLResponse)
def show(token: str, request: Request) -> HTMLResponse:
    try:
        return _show(token, _link(token, request))
    except Refused as error:
        return refuse(error)


@router.post("/contract/{token}/code", response_class=HTMLResponse)
def send_code(token: str, request: Request) -> HTMLResponse:
    try:
        link = _link(token, request)
        with Session(routing.engine_for(link.tenant)) as db:
            try:
                address = acceptance.send_code(db, link)
            except acceptance.LinkError as exc:
                raise Refused(409, str(exc)) from None
            except otp.CodeError as exc:
                return _show(token, link, str(exc), status=409)
        return _show(token, link, done=f"A code was sent to {address}.")
    except Refused as error:
        return refuse(error)


@router.post("/contract/{token}", response_class=HTMLResponse)
async def answer(token: str, request: Request) -> HTMLResponse:
    form = {k: str(v)[:500] for k, v in (await request.form()).items() if isinstance(v, str)}

    def work() -> HTMLResponse:
        try:
            link = _link(token, request)
            action = form.get("action", "")
            with Session(routing.engine_for(link.tenant)) as db:
                try:
                    if action == "accept":
                        acceptance.accept(db, link.tenant, link, form.get("code"))
                        words = "Thank you: you accepted the contract. It is in force once the company activates it."
                    elif action == "decline":
                        acceptance.decline(db, link.tenant, link, form.get("code"), form.get("reason"))
                        words = "Thank you: the buyer has been told that you decline the contract."
                    else:
                        raise acceptance.LinkError("choose accept or decline")
                except otp.CodeError as exc:
                    db.rollback()
                    return _show(token, link, str(exc), status=409)
                except acceptance.LinkError as exc:
                    db.rollback()
                    if "decline" in str(exc) or "choose" in str(exc):
                        return _show(token, link, str(exc), status=409)
                    raise Refused(409, str(exc)) from None
            return page(f"Contract {link.contract}", f"<p class=done>{escape(words)}</p>")
        except Refused as error:
            return refuse(error)

    return await run_in_threadpool(work)
