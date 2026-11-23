"""The page behind a supplier's link: answer an order (accept, decline, ask for a change) or record a dispatch.

No sign-in: the signed link is the key (orders/supplier_links.py), and an answer also needs the one-time code we email
to the address the link was sent to (D53), so a forwarded link decides nothing. Opening the page or asking for a code
changes nothing on the order; the answer is a separate button and the link works once. Accepting gives a delivery date
per line. A link of an older version says which version is current and can have it emailed to the supplier's contact. The page offers the document of the version the link was mailed with, exactly
as it was stored (orders/versions.py). A change or a decline goes to the buyer as a proposed change; nothing on the
order moves until the buyer accepts it (orders/confirmation.py). A dispatch is stored against the order for the store
(orders/dispatch_notes.py). A link that was altered, has expired, was used, or is for an order that has moved on says so
and does nothing. Wrong links are counted per address, so guessing is stopped.
"""

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from html import escape
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse
from sqlalchemy import text
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from p2p.formulas.calendar import IST, business_today
from p2p.formulas.money import format_inr, format_qty
from p2p.orders import changes, confirmation, dispatch_notes, supplier_links, versions
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


class Stale(Exception):
    """The link is of an older version: the page says which version is current (OP-9)."""

    def __init__(self, message: str, facts: dict[str, Any]) -> None:
        super().__init__(message)
        self.message, self.facts = message, facts


def _order(db: Session, link: supplier_links.Link) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    try:
        row = supplier_links.check(db, link)
    except supplier_links.StaleLink as exc:
        raise Stale(str(exc), supplier_links.stale_facts(db, link, exc.link_version)) from None
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
    po["masked"] = supplier_links.mask(row.get("sent_to"))
    return po, lines


def _day(d: date | None) -> str:
    return f"{d:%d %b %Y}" if d else ""


def _code_box(token: str, masked: str) -> str:
    """The code field of an answer, with the button that emails a code (as on the approval page)."""
    return (
        f"<p class=note>Answering needs the one-time code we email to {escape(masked)}. "
        f"<button class=plain formaction='/supplier/{escape(token)}/code' formmethod=post formnovalidate>"
        "Email me a code</button></p><label>One-time code<input name=code inputmode=numeric "
        "autocomplete=one-time-code maxlength=6 placeholder='Six-digit code'></label>"
    )


def _respond_form(token: str, po: dict[str, Any], lines: list[dict[str, Any]], note: str = "") -> str:
    rows = "".join(
        f"<tr><td>{escape(ln['description'])}</td><td>{escape(format_qty(ln['qty']))} {escape(ln['uom'] or '')}</td>"
        f"<td>{escape(format_inr(ln['unit_price']))}</td><td>{escape(_day(ln['promised_date'] or ln['need_by']))}</td></tr>"
        for ln in lines
    )
    date_rows = "".join(
        f"<tr><td>{ln['line_no']}. {escape(ln['description'])}</td>"
        f"<td><input type=date name=date_{ln['line_no']} value='{(ln['promised_date'] or ln['need_by'] or '')}' "
        f"aria-label='Delivery date for line {ln['line_no']}'></td></tr>"
        for ln in lines
    )
    code = _code_box(token, po["masked"])
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
        f"<p><a href='/supplier/{escape(token)}/pdf'>This version of the order (PDF)</a></p>"
        f"<table><tr><th>Item</th><th>Quantity</th><th>Price</th><th>Needed by</th></tr>{rows}</table>"
        f"<h2>Accept the order</h2><form method=post action='{action}'>"
        "<p class=muted>The delivery date of each line, as ordered. A date a few days later can be given here; for a "
        "later one, ask for a change.</p>"
        f"<table><tr><th>Line</th><th>Delivery date</th></tr>{date_rows}</table>"
        "<label for=note>A note for the buyer (optional)</label><input id=note name=note maxlength=300>"
        f"{code}<p><button class=ok name=action value=accept>Accept the order</button></p></form>"
        f"<h2>Ask for a change</h2><form method=post action='{action}'>"
        "<p class=muted>Fill in only what you need changed. The buyer accepts or rejects each change; the order stands "
        "as it is until then.</p>"
        f"<table><tr><th>Item</th><th>New date</th><th>New quantity</th><th>New price</th></tr>{change_rows}</table>"
        "<label for=creason>Why (the buyer sees it)</label><textarea id=creason name=reason rows=2 maxlength=500></textarea>"
        f"{code}<p><button class=back name=action value=change>Send the change to the buyer</button></p></form>"
        f"<h2>Decline the order</h2><form method=post action='{action}'>"
        "<label for=dreason>Why you cannot supply it</label><textarea id=dreason name=reason rows=2 maxlength=500 required></textarea>"
        f"{code}<p><button class=no name=action value=decline>Decline the order</button></p></form>"
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


def _stale_page(token: str, error: Stale, note: str = "", status: int = 409) -> HTMLResponse:
    """An older version's link (8.3): the version now, when it was made and emailed and to whom, what changed, and a
    button that emails the current version to the supplier's contact. No code is asked for here."""
    f = error.facts
    sent = (
        f"Version {f['version']} was emailed to {f['sent_to']} on {_day(_on(f['sent_on']))}: please use that email."
        if f["sent_on"] and f["sent_to"]
        else f"Version {f['version']} has not been emailed yet; we will send it when it is ready."
    )
    revised = f" (revised on {_day(_on(f['revised_on']))})" if f["revised_on"] else ""
    changed = _changed(f["changed"])
    button = (
        f"<form method=post action='/supplier/{escape(token)}/current'><p><button class=plain>Email me the current "
        "version</button></p></form>"
        if f["can_resend"]
        else ""
    )
    body = (
        (f"<p class=note>{escape(note)}</p>" if note else "") + f"<p>{escape(error.message)}.</p>"
        f"<p>Order {escape(f['po_no'])} is now version {f['version']}{escape(revised)}. This link was for version "
        f"{f['link_version']} and no longer works. {escape(sent)}</p>"
        + (f"<h2>What changed</h2><ul>{changed}</ul>" if changed else "")
        + button
    )
    return page("This link cannot be used", body, status)


def _on(value: datetime | None) -> date | None:
    return value.astimezone(IST).date() if value is not None else None


def _changed(diff: dict[str, Any] | None) -> str:
    if not diff:
        return ""
    items = [f"{h['label']}: {h['before']} to {h['after']}" for h in diff["header"]]
    for ln in diff["lines"]:
        words = ", ".join(f"{x['label'].lower()} {x['before']} to {x['after']}" for x in ln["fields"])
        items.append(
            f"line {ln['line_no']}: {words}" if ln["change"] == "changed" else f"line {ln['line_no']} {ln['change']}"
        )
    return "".join(f"<li>{escape(str(i))}</li>" for i in items)


def _form(token: str, link: supplier_links.Link, note: str = "", status: int = 200) -> HTMLResponse:
    with Session(routing.engine_for(link.tenant)) as db:
        try:
            po, lines = _order(db, link)
        except Stale as error:
            return _stale_page(token, error)
    if link.purpose == "dispatch":
        return page(f"Dispatch against order {po['no']}", _dispatch_form(token, po, lines, note), status)
    return page(f"Purchase order {po['no']}", _respond_form(token, po, lines, note), status)


@router.get("/supplier/{token}", response_class=HTMLResponse)
def show(token: str, request: Request) -> HTMLResponse:
    try:
        return _form(token, _link(token, request))
    except Refused as error:
        return refuse(error)


@router.post("/supplier/{token}/code", response_class=HTMLResponse)
def send_code(token: str, request: Request) -> HTMLResponse:
    """Email a one-time code to the address the link was sent to; the form again to type it in. Nothing else changes."""
    try:
        link = _link(token, request)
        with Session(routing.engine_for(link.tenant)) as db:
            try:
                address = supplier_links.send_code(db, link)
            except supplier_links.StaleLink:
                return _form(token, link)
            except supplier_links.LinkError as exc:
                if str(exc).startswith("this link") or str(exc).startswith("order "):
                    raise Refused(409, str(exc)) from None
                return _form(token, link, str(exc), 409)
        masked = supplier_links.mask(address)
        return _form(token, link, f"A code was sent to {masked}. It works once and expires in 10 minutes.")
    except Refused as error:
        return refuse(error)


@router.post("/supplier/{token}/current", response_class=HTMLResponse)
def email_current(token: str, request: Request) -> HTMLResponse:
    """From an older version's link: email the current version, with a new link, to the supplier's order contact."""
    try:
        link = _link(token, request)
        with Session(routing.engine_for(link.tenant)) as db:
            try:
                masked, version = supplier_links.resend_current(db, link.tenant, link)
            except supplier_links.LinkError as exc:
                status = 429 if "a moment ago" in str(exc) else 409
                raise Refused(status, str(exc)) from None
        return page("The current version is on its way", f"<p class=done>We have emailed version {version} to "
                    f"{escape(masked)}.</p><p class=muted>You can close this page.</p>")  # fmt: skip
    except Refused as error:
        return refuse(error)


@router.get("/supplier/{token}/pdf", response_model=None)
def version_document(token: str, request: Request) -> Response | HTMLResponse:
    """The document of the version this link was mailed with, as stored, while the link may still be used (OP-21)."""
    try:
        link = _link(token, request)
        with Session(routing.engine_for(link.tenant)) as db:
            try:
                row = supplier_links.check(db, link)
            except supplier_links.LinkError as exc:
                raise Refused(409, str(exc)) from None
            data = versions.stored_pdf(db, link.tenant, link.po_no, int(row["po_version"]))
        if data is None:
            raise Refused(404, f"version {row['po_version']} of order {link.po_no} was not issued as a document")
        return Response(
            data,
            media_type="application/pdf",
            headers={"Content-Disposition": f'inline; filename="{link.po_no}-v{row["po_version"]}.pdf"'},
        )
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


def _line_dates(form: dict[str, str]) -> dict[int, date | None]:
    """The delivery date given for each line of the accept form (blank: the line's date as ordered)."""
    return {
        int(name.split("_", 1)[1]): _date(value, f"the delivery date of line {name.split('_', 1)[1]}")
        for name, value in form.items()
        if name.startswith("date_") and name.split("_", 1)[1].isdigit()
    }


def _answer(token: str, link: supplier_links.Link, form: dict[str, str]) -> HTMLResponse:
    action = form.get("action", "")
    code = form.get("code", "").strip()[:12] or None
    with Session(routing.engine_for(link.tenant)) as db:
        if action == "accept" and link.purpose == "respond":
            dispatch_token, warnings = confirmation.accept_by_link(
                db, link.tenant, link, _line_dates(form), form.get("note"), code
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
            confirmation.answer_by_link(db, link.tenant, link, asked, form.get("reason"), code)
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


def _refusal_of_code(message: str) -> bool:
    return "code" in message and not message.startswith("this link")


@router.post("/supplier/{token}", response_class=HTMLResponse)
async def answer(token: str, request: Request) -> HTMLResponse:
    form = {k: str(v)[:500] for k, v in (await request.form()).items() if isinstance(v, str)}

    def work() -> HTMLResponse:
        try:
            link = _link(token, request)
            try:
                return _answer(token, link, form)
            except supplier_links.StaleLink:
                return _form(token, link)
            except supplier_links.LinkError as exc:
                if _refusal_of_code(str(exc)):  # the form again, to ask for a code or type it again
                    return _form(token, link, str(exc), 409)
                raise Refused(409, str(exc)) from None
            except (confirmation.ConfirmationError, dispatch_notes.DispatchNoteError) as exc:
                return _form(token, link, str(exc), 409)
        except Refused as error:
            return refuse(error)

    return await run_in_threadpool(work)
