"""Sourcing: how an approved request is bought, line by line.

A line whose item has a contract price in force is bought on that contract. Every other line goes into a request for
quotation (RFQ) sent to the suppliers approved for its category. A deterministic service: the choices come from the
price records, the supplier master and the settings, never from a model.

Sending an RFQ puts a message in front of outside companies, so it goes through the autonomy gate: unless an
administrator has set 'rfq.send' to automatic, the RFQ is prepared and a proposal waits for a buyer to approve it.
"""

import smtplib
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.agents import autonomy
from p2p.formulas.money import format_inr, format_qty
from p2p.formulas.sourcing import PriceOption, pick_contract_price
from p2p.platform import audit, emails, numbering, proposals, reference, worker
from p2p.platform.sod import Actor

DECISION_TYPE = "rfq.send"
SERVICE = Actor("service", "sourcing")


class SourcingError(Exception):
    pass


@dataclass
class LineOutcome:
    line_no: int
    item_code: str | None
    name: str
    qty: Decimal
    uom: str | None
    method: str  # contract, rfq or manual
    reason: str
    supplier_code: str | None = None
    contract_code: str | None = None
    price: Decimal | None = None
    rfq_no: str | None = None


@dataclass
class SourcingResult:
    request_no: str
    lines: list[LineOutcome] = field(default_factory=list)
    rfqs: list[str] = field(default_factory=list)
    sent: int = 0  # emails sent now
    proposals: list[int] = field(default_factory=list)  # RFQs waiting for a buyer to approve sending
    notes: list[str] = field(default_factory=list)

    def count(self, method: str) -> int:
        return sum(1 for line in self.lines if line.method == method)


@dataclass(frozen=True)
class Invitee:
    code: str
    name: str
    email: str
    contact: str | None
    history: int  # price records this supplier already has for the items asked about


# ---- settings ---------------------------------------------------------------------------------


def _org_setting(db: Session, key: str, default: str) -> str:
    value = db.execute(text("select value from org_setting where key = :k"), {"k": key}).scalar()
    return str(value) if value else default


def _app_setting(db: Session, key: str, default: str) -> str:
    value = db.execute(text("select value from app_setting where key = :k"), {"k": key}).scalar()
    return str(value) if value else default


# ---- contract or quotation --------------------------------------------------------------------


def contract_options(db: Session, item_code: str, uom: str | None, as_of: date) -> list[PriceOption]:
    """Contract prices for the item in force on the date: the price record, its contract and the supplier all valid.

    A price record without a contract is only a price list, not a commitment, so it does not count here.
    """
    rows = db.execute(
        text(
            "select pr.supplier_code, pr.contract_code, pr.price, coalesce(pr.min_qty, 0) as min_qty, pr.lead_days, pr.valid_to "
            "from price_record pr join contract c on c.code = pr.contract_code join supplier s on s.code = pr.supplier_code "
            "where pr.item_code = :item and pr.uom is not distinct from :uom "
            "and pr.valid_from <= :d and (pr.valid_to is null or pr.valid_to >= :d) "
            "and c.status = 'active' and c.valid_from <= :d and (c.valid_to is null or c.valid_to >= :d) "
            "and s.status = 'active'"
        ),
        {"item": item_code, "uom": uom, "d": as_of},
    ).all()
    return [PriceOption(r[0], r[1], r[2], r[3], r[4], r[5]) for r in rows]


def _supplier_name(db: Session, code: str) -> str:
    row = db.execute(text("select coalesce(trade_name, legal_name) from supplier where code = :c"), {"c": code}).first()
    return str(row[0]) if row else code


def _decide(db: Session, line: dict[str, Any], as_of: date) -> LineOutcome:
    base = {
        "line_no": line["line_no"],
        "item_code": line["item_code"],
        "name": line["name"],
        "qty": line["qty"],
        "uom": line["uom"],
    }
    if not line["item_code"]:
        return LineOutcome(**base, method="rfq", reason="A free-text line has no item, so no contract can cover it")
    options = contract_options(db, line["item_code"], line["uom"], as_of)
    pick = pick_contract_price(options, line["qty"])
    if pick is None:
        why = (
            f"No contract price applies to a quantity of {format_qty(line['qty'])} (the first price break is higher)"
            if options
            else "No contract price in force for this item"
        )
        return LineOutcome(**base, method="rfq", reason=why)
    until = f", in force until {pick.valid_to:%d %b %Y}" if pick.valid_to else ""
    return LineOutcome(
        **base,
        method="contract",
        supplier_code=pick.supplier_code,
        contract_code=pick.contract_code,
        price=pick.price,
        reason=(
            f"Contract {pick.contract_code} with {_supplier_name(db, pick.supplier_code)}: "
            f"{format_inr(pick.price)} per {line['uom']}{until}"
        ),
    )


# ---- who to ask -------------------------------------------------------------------------------


def eligible_suppliers(
    db: Session, category: str, item_codes: list[str], as_of: date
) -> tuple[list[Invitee], list[str]]:
    """Active suppliers approved for the category that have an email address and are not on the sanctions list.

    Those that already have prices for these items come first, then the rest by code, so the choice is repeatable.
    Returns the suppliers and a note for each one left out.
    """
    sanctioned = _sanctioned_names(db)
    rows = db.execute(
        text(
            "select s.code, s.legal_name, coalesce(s.trade_name, s.legal_name) as name, s.trade_name, "
            "(select c.email from supplier_contact c where c.supplier_code = s.code and c.email is not null "
            "   order by c.is_primary desc nulls last, c.id limit 1) as email, "
            "(select c.name from supplier_contact c where c.supplier_code = s.code and c.email is not null "
            "   order by c.is_primary desc nulls last, c.id limit 1) as contact, "
            "(select count(*) from price_record p where p.supplier_code = s.code and p.item_code = any(:items)) as history "
            "from supplier s join supplier_category sc on sc.supplier_code = s.code and sc.category_code = :cat "
            "and (sc.approved_from is null or sc.approved_from <= :d) "
            "where s.status = 'active' order by history desc, s.code"
        ),
        {"cat": category, "items": item_codes, "d": as_of},
    ).mappings()
    chosen: list[Invitee] = []
    notes: list[str] = []
    for r in rows:
        if {str(r["legal_name"]).lower(), str(r["trade_name"] or "").lower()} & sanctioned:
            notes.append(f"{r['code']} left out: matches the sanctions list")
        elif not r["email"]:
            notes.append(f"{r['code']} left out: no email address on file")
        else:
            chosen.append(Invitee(r["code"], r["name"], r["email"], r["contact"], int(r["history"])))
    return chosen, notes


def _sanctioned_names(db: Session) -> set[str]:
    names: set[str] = set()
    for name, aliases in db.execute(text("select name, aliases from sanction_entry")):
        names.add(str(name).lower())
        names.update(a.strip().lower() for a in str(aliases or "").split(";") if a.strip())
    return names


# ---- the run ----------------------------------------------------------------------------------


def source_request(
    db: Session, tenant: str, request_no: str, actor: Actor = SERVICE, *, as_of: date, dispatch: bool = True
) -> SourcingResult:
    """Decide how every open line of an approved request is bought, make the RFQs and send them (or propose sending)."""
    db.execute(text("select pg_advisory_xact_lock(hashtext('sourcing'))"))
    found = db.execute(text("select * from request where no = :n for update"), {"n": request_no}).mappings().first()
    if found is None:
        raise SourcingError(f"request {request_no} does not exist")
    req: dict[str, Any] = dict(found)
    if req["status"] != "approved":
        raise SourcingError(f"request {request_no} is {req['status']}; only an approved request can be sourced")
    lines = [
        dict(r)
        for r in db.execute(
            text(
                "select rl.line_no, rl.item_code, coalesce(i.name, rl.free_text) as name, rl.qty, rl.uom, rl.need_by, "
                "coalesce(nullif(rl.category_code, ''), i.category_code) as category from request_line rl "
                "left join item i on i.code = rl.item_code where rl.request_no = :n and rl.status = 'open' "
                "and not exists (select 1 from sourcing_decision d where d.request_no = rl.request_no and d.line_no = rl.line_no) "
                "order by rl.line_no"
            ),
            {"n": request_no},
        ).mappings()
    ]
    result = SourcingResult(request_no)
    if not lines:
        db.rollback()
        return result

    quote_lines: dict[str | None, list[tuple[dict[str, Any], LineOutcome]]] = {}
    for line in lines:
        outcome = _decide(db, line, as_of)
        result.lines.append(outcome)
        if outcome.method == "rfq":
            quote_lines.setdefault(line["category"], []).append((line, outcome))

    for category, group in quote_lines.items():
        _quotation(db, req, category, group, as_of, actor, result)

    for outcome in result.lines:
        db.execute(
            text(
                "insert into sourcing_decision (request_no, line_no, method, supplier_code, contract_code, price, uom, "
                "rfq_no, reason) values (:n, :l, :m, :s, :c, :p, :u, :r, :why)"
            ),
            {
                "n": request_no,
                "l": outcome.line_no,
                "m": outcome.method,
                "s": outcome.supplier_code,
                "c": outcome.contract_code,
                "p": outcome.price,
                "u": outcome.uom,
                "r": outcome.rfq_no,
                "why": outcome.reason,
            },
        )
        if outcome.method != "manual":  # a line nobody can buy yet stays open for a buyer to deal with
            db.execute(
                text("update request_line set status = 'sourcing' where request_no = :n and line_no = :l"),
                {"n": request_no, "l": outcome.line_no},
            )
    audit.record(
        db,
        actor,
        "sourcing.decide",
        "request",
        request_no,
        after={
            "contract": result.count("contract"),
            "rfq": result.count("rfq"),
            "manual": result.count("manual"),
            "rfqs": result.rfqs,
        },
    )
    db.commit()

    if result.count("contract"):
        worker.enqueue(tenant, "order_request", {"request_no": request_no})  # contract lines can be ordered at once
    if dispatch:
        for rfq_no in result.rfqs:
            _dispatch(db, rfq_no, Decimal(req["total_value"]), actor, result)
    return result


def _quotation(
    db: Session,
    req: dict[str, Any],
    category: str | None,
    group: list[tuple[dict[str, Any], LineOutcome]],
    as_of: date,
    actor: Actor,
    result: SourcingResult,
) -> None:
    """Make one RFQ for the lines of one category, or mark the lines for a buyer when nobody can be asked."""

    def give_up(reason: str) -> None:
        for _, outcome in group:
            outcome.method, outcome.reason = "manual", reason
        result.notes.append(reason)

    if category is None:
        return give_up("No category to find suppliers for: a buyer has to source these lines")
    cat = db.execute(
        text("select name, min_quotes, buyer_user from item_category where code = :c"), {"c": category}
    ).first()
    min_quotes = int(cat[1]) if cat and cat[1] else int(_org_setting(db, "min_quotes_default", "2"))
    wanted = max(int(_app_setting(db, "rfq_supplier_count", "3")), min_quotes)
    items = [line["item_code"] for line, _ in group if line["item_code"]]
    suppliers, left_out = eligible_suppliers(db, category, items, as_of)
    result.notes.extend(left_out)
    if not suppliers:
        return give_up(f"No active supplier approved for category {category} with an email address")
    invited = suppliers[:wanted]

    rfq_no = numbering.next_number(db, "rfq", as_of)
    due = reference.calendar_for(db).add_working_days(as_of, int(_org_setting(db, "default_rfq_response_days", "5")))
    note = None
    if len(invited) < min_quotes:
        note = f"Only {len(invited)} supplier(s) are approved for {category}; at least {min_quotes} quotes are wanted"
        result.notes.append(note)
    db.execute(
        text(
            "insert into rfq (no, buyer, request_no, category_code, due_date, status, note) "
            "values (:no, :buyer, :req, :cat, :due, 'draft', :note)"
        ),
        {"no": rfq_no, "buyer": cat[2] if cat else None, "req": req["no"], "cat": category, "due": due, "note": note},
    )
    for n, (line, outcome) in enumerate(group, start=1):
        db.execute(
            text(
                "insert into rfq_line (rfq_no, line_no, request_no, request_line_no, item_code, description, qty, uom, need_by) "
                "values (:r, :n, :req, :rl, :item, :d, :q, :u, :need)"
            ),
            {
                "r": rfq_no,
                "n": n,
                "req": req["no"],
                "rl": line["line_no"],
                "item": line["item_code"],
                "d": line["name"],
                "q": line["qty"],
                "u": line["uom"],
                "need": line["need_by"],
            },
        )
        outcome.rfq_no = rfq_no
        outcome.reason = f"{outcome.reason}; quotes asked from {len(invited)} suppliers in {rfq_no}"
    for s in invited:
        db.execute(
            text("insert into rfq_supplier (rfq_no, supplier_code, email) values (:r, :s, :e)"),
            {"r": rfq_no, "s": s.code, "e": s.email},
        )
    audit.record(
        db,
        actor,
        "rfq.create",
        "rfq",
        rfq_no,
        after={
            "request": req["no"],
            "category": category,
            "lines": len(group),
            "suppliers": [s.code for s in invited],
            "due_date": due,
        },
    )
    result.rfqs.append(rfq_no)


# ---- sending ----------------------------------------------------------------------------------


def _dispatch(db: Session, rfq_no: str, value: Decimal, actor: Actor, result: SourcingResult) -> None:
    gate = autonomy.gate(db, DECISION_TYPE, value=value, confidence=1.0)  # nothing here is a model's guess
    if gate.allowed:
        result.sent += send_rfq(db, rfq_no, actor)
        autonomy.record_outcome(db, DECISION_TYPE, auto=True, ref=rfq_no)
        return
    count = db.execute(text("select count(*) from rfq_supplier where rfq_no = :r"), {"r": rfq_no}).scalar_one()
    pid = proposals.propose(
        db,
        SERVICE,
        action="rfq.send",
        summary=f"Send {rfq_no} to {count} suppliers ({gate.reason})",
        payload={"rfq_no": rfq_no},
        required_permission="rfq.send",
        risk="low",
        document="rfq",
    )
    db.execute(text("update rfq set proposal_id = :p where no = :r"), {"p": pid, "r": rfq_no})
    db.commit()
    result.proposals.append(pid)


def _body(rfq: dict[str, Any], lines: list[dict[str, Any]], contact: str | None, company: str, deliver_to: str) -> str:
    items = "\n".join(
        f"{n:>3}. {ln['description']}"
        + (f" ({ln['item_code']})" if ln["item_code"] else "")
        + f" - {format_qty(ln['qty'])} {ln['uom'] or ''}".rstrip()
        + (f" - needed by {ln['need_by']:%d %b %Y}" if ln["need_by"] else "")
        for n, ln in enumerate(lines, start=1)
    )
    return (
        f"Dear {contact or 'Sir or Madam'},\n\n"
        f"{company} invites you to quote for the items below.\n\n"
        f"Reference: {rfq['no']}\n"
        f"Please reply by: {rfq['due_date']:%d %b %Y}\n"
        f"Deliver to: {deliver_to}\n\n"
        f"{items}\n\n"
        "Please reply to this email, keeping the subject line, with for each item: the unit price, the GST rate, "
        "the delivery time in days, how long your price is valid, and your payment terms. "
        "Attach your quotation if you have one.\n\n"
        f"Regards,\nProcurement, {company}\n"
    )


def send_rfq(db: Session, rfq_no: str, actor: Actor = SERVICE) -> int:
    """Email the RFQ to every invited supplier it has not reached yet. Returns how many emails went out now.

    A supplier whose email fails is recorded and retried the next time; the others are not held up.
    """
    rfq = db.execute(text("select * from rfq where no = :n for update"), {"n": rfq_no}).mappings().first()
    if rfq is None:
        raise SourcingError(f"{rfq_no} does not exist")
    if rfq["status"] not in ("draft", "sent"):
        raise SourcingError(f"{rfq_no} is {rfq['status']} and cannot be sent")
    lines = [
        dict(r)
        for r in db.execute(text("select * from rfq_line where rfq_no = :n order by line_no"), {"n": rfq_no}).mappings()
    ]
    company = db.execute(text("select coalesce(short_name, legal_name) from company limit 1")).scalar() or "Our company"
    category = db.execute(text("select name from item_category where code = :c"), {"c": rfq["category_code"]}).scalar()
    deliver_to = (
        db.execute(
            text("select l.name from request r join location l on l.code = r.ship_to_location where r.no = :n"),
            {"n": rfq["request_no"]},
        ).scalar()
        or "as agreed"
    )
    pending = db.execute(
        text(
            "select rs.supplier_code, rs.email, (select c.name from supplier_contact c where c.supplier_code = rs.supplier_code "
            "and c.email = rs.email limit 1) as contact from rfq_supplier rs where rs.rfq_no = :n and rs.invited_at is null "
            "order by rs.supplier_code"
        ),
        {"n": rfq_no},
    ).all()
    subject = f"Request for quotation {rfq_no}" + (f": {category}" if category else "")
    sent = failed = 0
    for code, email, contact in pending:
        try:
            emails.send_linked_email(
                db,
                email,
                subject,
                _body(dict(rfq), lines, contact, str(company), str(deliver_to)),
                linked_type="rfq",
                linked_ref=rfq_no,
            )
        except (OSError, smtplib.SMTPException) as exc:
            db.rollback()
            db.execute(
                text("update rfq_supplier set send_error = :e where rfq_no = :n and supplier_code = :s"),
                {"e": str(exc)[:300], "n": rfq_no, "s": code},
            )
            db.commit()
            failed += 1
            continue
        db.execute(
            text(
                "update rfq_supplier set invited_at = now(), send_error = null where rfq_no = :n and supplier_code = :s"
            ),
            {"n": rfq_no, "s": code},
        )
        db.commit()
        sent += 1
    if sent:
        db.execute(
            text("update rfq set status = 'sent', sent_at = coalesce(sent_at, now()) where no = :n"), {"n": rfq_no}
        )
    audit.record(db, actor, "rfq.send", "rfq", rfq_no, after={"sent": sent, "failed": failed})
    db.commit()
    return sent


@proposals.executor("rfq.send")
def _send_when_approved(db: Session, payload: dict[str, Any], actor: Actor) -> dict[str, Any]:
    return {"sent": send_rfq(db, str(payload["rfq_no"]), actor)}


def sweep(
    db: Session, tenant: str, *, as_of: date, actor: Actor = SERVICE, dispatch: bool = True
) -> list[SourcingResult]:
    """Source every approved request that still has lines without a decision (a retry, and the safety net for jobs lost)."""
    numbers = [
        r[0]
        for r in db.execute(
            text(
                "select distinct r.no from request r join request_line rl on rl.request_no = r.no "
                "where r.status = 'approved' and rl.status = 'open' and not exists "
                "(select 1 from sourcing_decision d where d.request_no = rl.request_no and d.line_no = rl.line_no) "
                "order by r.no"
            )
        )
    ]
    return [source_request(db, tenant, no, actor, as_of=as_of, dispatch=dispatch) for no in numbers]


def summary(result: SourcingResult) -> dict[str, Any]:
    return {
        "request": result.request_no,
        "contract": result.count("contract"),
        "rfq": result.count("rfq"),
        "manual": result.count("manual"),
        "rfqs": result.rfqs,
        "emails_sent": result.sent,
        "proposals": result.proposals,
        "notes": result.notes,
    }
