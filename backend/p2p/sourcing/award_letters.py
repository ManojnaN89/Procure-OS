"""The letters of an approved award (task 12.2, D45): one award letter per awarded supplier, regret notes to the rest.

Queued by the system in the approval's transaction (OP-9: never before the approval) and sent once by the outbox. The
award letter is a notice of selection, not an order and not a commitment to buy; it carries the draft rate contract (a
PDF in the order's layout) and a signed link to accept or decline it (masters/contract_acceptance.py). A regret note,
when the buyer asked for them, goes to each invited supplier that quoted and won nothing: it never says a price, a
quantity, a value or another supplier's name. Fixed words (sourcing/templates.py); no model writes them.
"""

from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.formulas.money import format_inr, format_qty
from p2p.masters import contract_acceptance
from p2p.orders.document import Pdf, supplier_address
from p2p.platform import audit, email_layout, outbox
from p2p.sourcing import templates

ACTOR = audit.SYSTEM
PDF = "application/pdf"


def _contact(db: Session, supplier: str) -> tuple[str | None, str | None]:
    """The supplier's primary contact: the address the letter (and later the code) goes to."""
    row = db.execute(
        text(
            "select name, email from supplier_contact where supplier_code = :c and email is not null "
            "order by is_primary desc nulls last, id limit 1"
        ),
        {"c": supplier},
    ).first()
    return (row[0], row[1]) if row else (None, None)


def _names(db: Session) -> tuple[str, dict[str, str]]:
    company = db.execute(text("select coalesce(short_name, legal_name) from company limit 1")).scalar()
    suppliers = {
        str(r[0]): str(r[1]) for r in db.execute(text("select code, coalesce(trade_name, legal_name) from supplier"))
    }
    return str(company or "Our company"), suppliers


def contract_pdf(db: Session, code: str) -> bytes:
    """The draft rate contract as a document: the parties, the prices, the period, the ceiling and the terms."""
    c = contract_acceptance.terms(db, code)
    doc = Pdf(db)
    doc.title(f"RATE CONTRACT {code} (draft)", f"Valid {c['valid_from']:%d %b %Y} to {c['valid_to']:%d %b %Y}")
    doc.parties(
        ("Supplier", [str(c["supplier_name"]), supplier_address(db, str(c["supplier_code"]))]),
        ("Deliver to", [str(p) for p in dict.fromkeys(x["place"] for x in c["prices"]) if p]),
    )
    rows: list[list[Any]] = [["Item", "Unit", "Price (Rs)", "Lead days"]]
    for p in c["prices"]:
        rows.append([doc.p(f"{p['name']} ({p['item_code']})", doc.small), p["uom"] or "", f"{p['price']:.2f}",
                     str(p["lead_days"] or "")])  # fmt: skip
    doc.lines(rows, [100, 20, 30, 30], (2, 3))
    doc.add(
        doc.p(f"Ceiling before tax: {format_inr(c['max_value'])}" if c["max_value"] else "No ceiling"),
        doc.p(f"Payment terms: {c['terms_name'] or c['payment_terms_code'] or 'as agreed'}"),
        doc.p(f"Delivery terms: {c['delivery_terms'] or 'delivered to the place above'}"),
    )
    doc.space(3)
    doc.add(
        doc.p(
            "This contract is in force only once you accept its terms and a second person at "
            f"{c['company']} activates it. Orders are placed during the period as the goods are needed; prices are "
            "before GST.",
            doc.small,
        )
    )
    return doc.build(f"Rate contract {code}")


def _queue(db: Session, decision: dict[str, Any], supplier: str, kind: str, message_id: str, code: str | None) -> None:
    db.execute(
        text(
            "insert into award_letter (decision_id, supplier_code, kind, message_id, contract_code) "
            "values (:d, :s, :k, :m, :c)"
        ),
        {"d": decision["id"], "s": supplier, "k": kind, "m": message_id, "c": code},
    )
    audit.record(db, ACTOR, "award.letter_queued", "rfq", decision["rfq_no"],
                 after={"decision": decision["id"], "supplier": supplier, "kind": kind, "contract": code})  # fmt: skip


def _award_letter(
    db: Session, tenant: str, rfq: dict[str, Any], decision: dict[str, Any], supplier: str, code: str
) -> str | None:
    contact, email = _contact(db, supplier)
    if not email:
        return None
    company, names = _names(db)
    where = db.execute(text("select name from location where code = :c"), {"c": rfq["delivery_location"]}).scalar()
    parts = db.execute(
        text(
            "select l.description, l.item_code, a.qty, a.price, l.uom from award a join rfq_line l on l.rfq_no = a.rfq_no "
            "and l.line_no = a.rfq_line_no where a.decision_id = :d and a.supplier_code = :s order by a.rfq_line_no"
        ),
        {"d": decision["id"], "s": supplier},
    ).all()
    lines = "\n".join(
        f"  - {d} ({i}): {format_qty(q)} {u or ''} at {format_inr(p)} per {u or 'unit'}" for d, i, q, p, u in parts
    )
    fields = {
        "contact": contact or "Sir or Madam", "company": company, "supplier": names.get(supplier, supplier),
        "reference": rfq["no"], "lines": lines, "period_from": f"{rfq['period_from']:%d %b %Y}",
        "period_to": f"{rfq['period_to']:%d %b %Y}", "location": str(where or rfq["delivery_location"]), "contract": code,
    }  # fmt: skip
    head = templates.render(templates.AWARD_LETTER, fields)
    end = templates.render(templates.AWARD_LETTER_END, fields)
    link = contract_acceptance.url(contract_acceptance.issue_link(db, tenant, code, email))
    subject = f"Award notice for {rfq['no']}: rate contract {code}"
    message_id = outbox.queue_email(
        db, to=email, subject=subject, body=head, secret=link + end, record=head + "(the link in the email)" + end,
        html=email_layout.text_email(subject, head + link + end), linked_type="award", linked_ref=str(rfq["no"]),
        attachments=[(f"{code}.pdf", PDF, contract_pdf(db, code))],
    )  # fmt: skip
    _queue(db, decision, supplier, "award", message_id, code)
    return message_id


def _regret(db: Session, rfq: dict[str, Any], decision: dict[str, Any], supplier: str, quote: str) -> str | None:
    contact, email = _contact(db, supplier)
    if not email:
        return None
    company, _ = _names(db)
    body = templates.render(
        templates.REGRET_NOTE,
        {"contact": contact or "Sir or Madam", "company": company, "quote": quote, "reference": str(rfq["no"])},
    )
    subject = f"Your quotation for {rfq['no']}"
    message_id = outbox.queue_email(
        db, to=email, subject=subject, body=body, html=email_layout.text_email(subject, body), linked_type="award",
        linked_ref=str(rfq["no"]),
    )  # fmt: skip
    _queue(db, decision, supplier, "regret", message_id, None)
    return message_id


def queue(db: Session, tenant: str, rfq: dict[str, Any], decision: dict[str, Any], made: dict[str, str]) -> list[str]:
    """Queue the decision's letters (in the caller's transaction): award letters to the awarded suppliers with their
    contracts, regret notes to those that quoted and won nothing when asked. Returns the queued Message-IDs."""
    queued = [m for supplier, code in made.items() if (m := _award_letter(db, tenant, rfq, decision, supplier, code))]
    if not decision["regret_notes"]:
        return queued
    others = db.execute(
        text(
            "select q.supplier_code, max(q.no) filter (where q.revision = (select max(x.revision) from quote x "
            "where x.rfq_no = q.rfq_no and x.supplier_code = q.supplier_code)) from quote q join rfq_supplier s "
            "on s.rfq_no = q.rfq_no and s.supplier_code = q.supplier_code where q.rfq_no = :n "
            "and q.supplier_code <> all(:won) group by q.supplier_code order by q.supplier_code"
        ),
        {"n": rfq["no"], "won": list(made)},
    ).all()
    return queued + [m for supplier, quote in others if (m := _regret(db, rfq, decision, str(supplier), str(quote)))]
