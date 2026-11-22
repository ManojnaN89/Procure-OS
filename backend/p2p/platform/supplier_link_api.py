"""The page behind a supplier's link: answer an order (accept, decline, ask for a change) or record a dispatch.

No sign-in: the signed link is the key (orders/supplier_links.py). Opening the page changes nothing; the answer is a
separate button and the link works once. A change or a decline goes to the buyer as a proposed change; nothing on the
order moves until the buyer accepts it (orders/confirmation.py). A dispatch is stored against the order for the store
(orders/dispatch_notes.py). A link that was altered, has expired, was used, or is for an order that has moved on says so
and does nothing. Wrong links are counted per address, so guessing is stopped.
"""

from datetime import date
from decimal import Decimal, InvalidOperation
from html import escape
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import text
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from p2p.formulas.calendar import business_today
from p2p.formulas.money import format_inr, format_qty
from p2p.orders import changes, confirmation, dispatch_notes, supplier_links
from p2p.platform import routing
from p2p.platform.link_pages import Refused, bad_link, guard, open_company, page, refuse

router = APIRouter(tags=["supplier links"])


def _link(token: str, request: Request) -> supplier_links.Link:
    key = guard(request, "supplier-link")
    try:
        link = supplier_links.read(token)
    except supplier_links.LinkError as exc:
        raise bad_link(key, str(exc)) from None
    open_company(link.tenant)
    return link


def _order(db: Session, link: supplier_links.Link) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    try:
        supplier_links.check(db, link)
    except supplier_links.LinkError as exc:
        raise Refused(409, str(exc)) from None
    po = dict(
        db.execute(
            text(
                "select p.no, p.version, p.total, coalesce(c.short_name, c.legal_name) as company from po p "
                "cross join (select short_name, legal_name from company limit 1) c where p.no = :n"
            ),
            {"n": link.po_no},
        )
        .mappings()
        .one()
    )
    left = dispatch_notes.to_ship(db, link.po_no)
    lines = [
        {**dict(r), "left": left.get(int(r["line_no"]), {}).get("left", Decimal(0))}
        for r in db.execute(
            text(
                "select line_no, description, item_code, qty, uom, unit_price, need_by, promised_date from po_line "
                "where po_no = :n and status in ('open', 'part_received') order by line_no"
            ),
            {"n": link.po_no},
        ).mappings()
    ]
    return po, lines


def _day(d: date | None) -> str:
    return f"{d:%d %b %Y}" if d else ""


def _respond_form(token: str, po: dict[str, Any], lines: list[dict[str, Any]], note: str = "") -> str:
    rows = "".join(
        f"<tr><td>{escape(ln['description'])}</td><td>{escape(format_qty(ln['qty']))} {escape(ln['uom'] or '')}</td>"
        f"<td>{escape(format_inr(ln['unit_price']))}</td><td>{escape(_day(ln['promised_date'] or ln['need_by']))}</td></tr>"
        for ln in lines
    )
    change_rows = "".join(
        f"<tr><td>{escape(ln['description'])}</td>"
        f"<td><input type=date name=date_{ln['line_no']} aria-label='New date for line {ln['line_no']}'></td>"
        f"<td><input name=qty_{ln['line_no']} inputmode=decimal aria-label='New quantity for line {ln['line_no']}'></td>"
        f"<td><input name=price_{ln['line_no']} inputmode=decimal aria-label='New price for line {ln['line_no']}'></td></tr>"
        for ln in lines
    )
    action = f"/supplier/{escape(token)}"
    return (
        (f"<p class=note>{escape(note)}</p>" if note else "")
        + f"<p>{escape(po['company'])} placed order {escape(po['no'])} with you"
        + (f" (version {po['version']})" if po["version"] > 1 else "")
        + f", {escape(format_inr(po['total']))} in all with GST.</p>"
        f"<table><tr><th>Item</th><th>Quantity</th><th>Price</th><th>Needed by</th></tr>{rows}</table>"
        f"<h2>Accept the order</h2><form method=post action='{action}'>"
        "<label for=promised>The date you will deliver (optional)</label><input type=date id=promised name=promised>"
        "<label for=note>A note for the buyer (optional)</label><input id=note name=note maxlength=300>"
        "<p><button class=ok name=action value=accept>Accept the order</button></p></form>"
        f"<h2>Ask for a change</h2><form method=post action='{action}'>"
        "<p class=muted>Fill in only what you need changed. The buyer accepts or rejects each change; the order stands "
        "as it is until then.</p>"
        f"<table><tr><th>Item</th><th>New date</th><th>New quantity</th><th>New price</th></tr>{change_rows}</table>"
        "<label for=creason>Why (the buyer sees it)</label><textarea id=creason name=reason rows=2 maxlength=500></textarea>"
        "<p><button class=back name=action value=change>Send the change to the buyer</button></p></form>"
        f"<h2>Decline the order</h2><form method=post action='{action}'>"
        "<label for=dreason>Why you cannot supply it</label><textarea id=dreason name=reason rows=2 maxlength=500 required></textarea>"
        "<p><button class=no name=action value=decline>Decline the order</button></p></form>"
        "<p class=muted>This link works once. You can also reply to the order email.</p>"
    )


def _dispatch_form(token: str, po: dict[str, Any], lines: list[dict[str, Any]], note: str = "") -> str:
    rows = "".join(
        f"<tr><td>{escape(ln['description'])}</td><td>{escape(format_qty(ln['left']))} {escape(ln['uom'] or '')}</td>"
        f"<td><input name=ship_{ln['line_no']} inputmode=decimal aria-label='Quantity shipped on line {ln['line_no']}'></td></tr>"
        for ln in lines
        if ln["left"] > 0
    )
    today = business_today().isoformat()
    return (
        (f"<p class=note>{escape(note)}</p>" if note else "")
        + f"<p>Record what you have shipped against order {escape(po['no'])}, so that {escape(po['company'])}'s store "
        "expects it.</p>"
        f"<form method=post action='/supplier/{escape(token)}'>"
        f"<table><tr><th>Item</th><th>Still to ship</th><th>Shipped now</th></tr>{rows}</table>"
        f"<label for=shipped_on>Date shipped</label><input type=date id=shipped_on name=shipped_on value={today} required>"
        "<label for=expected_on>Expected to arrive (optional)</label><input type=date id=expected_on name=expected_on>"
        "<label for=vehicle>Vehicle number</label><input id=vehicle name=vehicle maxlength=60>"
        "<label for=lr_no>LR or AWB number</label><input id=lr_no name=lr_no maxlength=60>"
        "<label for=invoice_no>Your invoice number (optional)</label><input id=invoice_no name=invoice_no maxlength=60>"
        "<p><button class=ok name=action value=dispatch>Record the dispatch</button></p></form>"
        "<p class=muted>This link works once. If you ship part now, you get a link for the rest.</p>"
    )


def _form(token: str, link: supplier_links.Link, note: str = "", status: int = 200) -> HTMLResponse:
    with Session(routing.engine_for(link.tenant)) as db:
        po, lines = _order(db, link)
    if link.purpose == "dispatch":
        return page(f"Dispatch against order {po['no']}", _dispatch_form(token, po, lines, note), status)
    return page(f"Purchase order {po['no']}", _respond_form(token, po, lines, note), status)


@router.get("/supplier/{token}", response_class=HTMLResponse)
def show(token: str, request: Request) -> HTMLResponse:
    try:
        return _form(token, _link(token, request))
    except Refused as error:
        return refuse(error)


def _date(value: str | None, what: str) -> date | None:
    if not value or not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        raise confirmation.ConfirmationError(f"{what} must be a date") from None


def _quantity(value: str) -> Decimal:
    try:
        return Decimal(value.strip().replace(",", ""))
    except InvalidOperation:
        raise confirmation.ConfirmationError(f"'{value}' is not a quantity") from None


def _asked(form: dict[str, str]) -> list[confirmation.Proposed]:
    asked: list[confirmation.Proposed] = []
    for name, value in sorted(form.items()):
        kind, _, line = name.partition("_")
        if kind in ("date", "qty", "price") and line.isdigit() and value.strip():
            asked.append(confirmation.Proposed(kind, int(line), value.strip()))
    return asked


def _answer(token: str, link: supplier_links.Link, form: dict[str, str]) -> HTMLResponse:
    action = form.get("action", "")
    with Session(routing.engine_for(link.tenant)) as db:
        if action == "accept" and link.purpose == "respond":
            dispatch_token, warnings = confirmation.accept_by_link(
                db, link.tenant, link, _date(form.get("promised"), "the date you will deliver"), form.get("note")
            )
            late = f"<p class=note>Noted: {escape('; '.join(warnings))}.</p>" if warnings else ""
            return page(
                f"Order {link.po_no} accepted",
                f"<p>Thank you. The buyer has been told.</p>{late}<p>When you ship, record the dispatch here (we have "
                f"emailed you this link too): <a href='/supplier/{escape(dispatch_token)}'>record a dispatch</a>.</p>",
            )
        if action in ("change", "decline") and link.purpose == "respond":
            asked = [confirmation.Proposed("decline")] if action == "decline" else _asked(form)
            if not asked:
                raise confirmation.ConfirmationError("fill in at least one new date, quantity or price")
            confirmation.answer_by_link(db, link.tenant, link, asked, form.get("reason"))
            said = "that you cannot supply the order" if action == "decline" else "the change you asked for"
            return page(
                f"Order {link.po_no}",
                f"<p>Thank you. The buyer has {escape(said)} and will reply by email. Until then the order stands as "
                "it is.</p><p class=muted>You can close this page.</p>",
            )
        if action == "dispatch" and link.purpose == "dispatch":
            return _record_dispatch(db, link, form)
    raise confirmation.ConfirmationError("this link cannot do that")


def _record_dispatch(db: Session, link: supplier_links.Link, form: dict[str, str]) -> HTMLResponse:
    quantities = {
        int(name.split("_", 1)[1]): _quantity(value)
        for name, value in form.items()
        if name.startswith("ship_") and name.split("_", 1)[1].isdigit() and value.strip()
    }
    shipped_on = _date(form.get("shipped_on"), "the date shipped") or business_today()
    done = dispatch_notes.record(
        db, link.tenant, link.po_no, quantities, shipped_on=shipped_on, actor=confirmation.supplier_actor(link.supplier_code),
        source="link", expected_on=_date(form.get("expected_on"), "the date expected"), vehicle=form.get("vehicle"),
        lr_no=form.get("lr_no"), invoice_no=form.get("invoice_no"), link=link,
    )  # fmt: skip
    rest = ""
    if done.partial:
        token = supplier_links.issue(db, link.tenant, link.po_no, "dispatch")
        db.commit()
        po = dict(db.execute(text("select * from po where no = :n"), {"n": link.po_no}).mappings().one())
        changes.tell_supplier(
            db, po, f"Purchase order {link.po_no}: the rest to ship",
            f"Thank you: dispatch recorded against order {link.po_no}. When you ship the rest, record it here:\n"
            f"{supplier_links.url(token)}",
        )  # fmt: skip
        rest = f"<p>Part of the order is still to ship. Record it later here (also emailed): <a href='/supplier/{escape(token)}'>the next dispatch</a>.</p>"
    return page(f"Dispatch recorded: {link.po_no}", f"<p>Thank you. Our store now expects it.</p>{rest}")


@router.post("/supplier/{token}", response_class=HTMLResponse)
async def answer(token: str, request: Request) -> HTMLResponse:
    form = {k: str(v)[:500] for k, v in (await request.form()).items() if isinstance(v, str)}

    def work() -> HTMLResponse:
        try:
            link = _link(token, request)
            try:
                return _answer(token, link, form)
            except supplier_links.LinkError as exc:
                raise Refused(409, str(exc)) from None
            except (confirmation.ConfirmationError, dispatch_notes.DispatchNoteError) as exc:
                return _form(token, link, str(exc), 409)
        except Refused as error:
            return refuse(error)

    return await run_in_threadpool(work)
