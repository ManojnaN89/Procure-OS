"""Sourcing: how an approved request is bought, line by line.

Each line takes the first buying route (D37) that applies: a contract price in force with money left, a single-source
item, an emergency, a safe reorder from the known supplier, and otherwise a request for quotation (RFQ) sent to the
suppliers approved for its category. The three direct routes make an order without quotes; single source above the
threshold and every emergency wait for the purchase manager to agree. A buyer may tighten a route at once (send the
line to quotation); loosening it is a proposal. A deterministic service: the choices come from the price records, the
orders and receipts, the supplier master and the versioned route rule, never from a model.

Sending an RFQ puts a message in front of outside companies, so it goes through the autonomy gate: unless an
administrator has set 'rfq.send' to automatic, the RFQ is prepared and a proposal waits for a buyer to approve it.
"""

import json
import smtplib
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.agents import autonomy
from p2p.formulas.buying_route import Check, is_tighter, quotes_needed
from p2p.formulas.contracts import ceiling_breach
from p2p.formulas.money import format_inr, format_qty
from p2p.formulas.sourcing import PriceOption, pick_contract_price
from p2p.orders import contracts as contract_usage
from p2p.platform import audit, emails, numbering, proposals, reference, worker
from p2p.platform.sod import Actor
from p2p.sourcing import documents, quote_sheet, route, templates
from p2p.suppliers import scoring

D = Decimal
DECISION_TYPE = "rfq.send"
ROUTE_PERMISSION = "sourcing.route_approve"  # the purchase manager's: agree to direct buys and looser routes
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
    method: str  # contract, direct, rfq or manual
    reason: str
    supplier_code: str | None = None
    contract_code: str | None = None
    price: Decimal | None = None
    rfq_no: str | None = None
    # the buying route (D37) and what it was based on
    route: str | None = None
    route_rule: str | None = None
    checks: tuple[Check, ...] = ()
    line_value: Decimal | None = None
    window_value: Decimal | None = None
    min_quotes: int = 0
    needs_agreement: bool = False  # a direct buy the purchase manager must agree to
    proposal_id: int | None = None


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


def _sourcing_mode(db: Session) -> str:
    """'compare' (the default: contract price, else a request for quotation) or 'best_vendor' (see _best_vendor)."""
    mode = _app_setting(db, "sourcing_mode", "compare")
    return mode if mode in ("compare", "best_vendor") else "compare"


def _best_vendor(db: Session, line: dict[str, Any], as_of: date, base: dict[str, Any]) -> LineOutcome | None:
    """In best-vendor mode a line whose item has prices on file goes straight to the best-scored supplier, without a quotation
    request: price against the others on offer, delivery and quality from the supplier's own record. The supplier must be
    one that could be asked (not sanctioned, has an email address, good documents) and a contract's ceiling must have room.
    Returns None when nobody qualifies, and the line is then sourced the usual way."""
    qty = Decimal(line["qty"])
    rows = db.execute(
        text(
            "select p.supplier_code, p.contract_code, p.price, coalesce(p.min_qty, 0) as min_qty "
            "from price_record p join supplier s on s.code = p.supplier_code left join contract c on c.code = p.contract_code "
            "where p.item_code = :i and p.uom is not distinct from :u and p.valid_from <= :d "
            "and (p.valid_to is null or p.valid_to >= :d) and s.status = 'active' "
            "and (p.contract_code is null or (c.status = 'active' and c.valid_from <= :d and (c.valid_to is null or c.valid_to >= :d)))"
        ),
        {"i": line["item_code"], "u": line["uom"], "d": as_of},
    ).mappings()
    per_supplier: dict[str, dict[str, Any]] = {}
    for r in rows:
        if r["min_qty"] > qty:
            continue  # the quantity does not reach this price break
        held = per_supplier.get(r["supplier_code"])
        if held is None or (r["min_qty"], -r["price"]) > (held["min_qty"], -held["price"]):
            per_supplier[r["supplier_code"]] = dict(r)
    sanctioned = _sanctioned_names(db)
    candidates: list[tuple[str, Decimal]] = []
    for code, opt in per_supplier.items():
        who = db.execute(
            text(
                "select legal_name, trade_name, (select c.email from supplier_contact c where c.supplier_code = s.code "
                "and c.email is not null limit 1) as email from supplier s where s.code = :c"
            ),
            {"c": code},
        ).one()
        if {str(who[0]).lower(), str(who[1] or "").lower()} & sanctioned or not who[2]:
            continue
        if not documents.check_supplier(db, code, as_of).ok:
            continue
        if opt["contract_code"]:
            use = contract_usage.usage(db, opt["contract_code"])
            if ceiling_breach(use.max_value, use.used, qty * D(opt["price"])) > 0 and use.max_value is not None:
                continue
        candidates.append((code, D(opt["price"])))
    ranked = scoring.rank(db, candidates, as_of)
    if not ranked:
        return None
    top = ranked[0]
    pick = per_supplier[top.supplier_code]
    return LineOutcome(
        **base,
        method="contract",
        supplier_code=top.supplier_code,
        contract_code=pick["contract_code"],
        price=top.price,
        reason=(
            f"Best vendor {_supplier_name(db, top.supplier_code)} of {len(ranked)} priced: score {top.total} "
            f"(price {top.price_score:.2f}, delivery {top.delivery:.2f}, quality {top.quality:.2f}), "
            f"{format_inr(top.price)} per {line['uom']}"
            + (f" on contract {pick['contract_code']}" if pick["contract_code"] else "")
        ),
    )


def _decide(db: Session, router: route.Router, line: dict[str, Any], emergency: bool, as_of: date) -> LineOutcome:
    """The buying route of the line (D37): contract, single source, emergency, safe reorder, else quotation."""
    base = {
        "line_no": line["line_no"],
        "item_code": line["item_code"],
        "name": line["name"],
        "qty": line["qty"],
        "uom": line["uom"],
    }
    if line["item_code"] and _sourcing_mode(db) == "best_vendor":
        chosen = _best_vendor(db, line, as_of, base)
        if chosen is not None:
            chosen.route = "best_vendor"
            return chosen
    if line["item_code"]:
        on_contract = _on_contract(db, line, as_of, base)
    else:
        on_contract = LineOutcome(
            **base, method="rfq", reason="A free-text line has no item, so no contract can cover it"
        )
    found = router.decide(line, on_contract.price, on_contract.reason, emergency)
    choice = found.choice
    outcome = (
        on_contract if choice.route == "contract" else LineOutcome(**base, method=_METHOD[choice.route], reason="")
    )
    outcome.route, outcome.route_rule, outcome.checks = choice.route, found.rule.version, choice.checks
    outcome.line_value, outcome.window_value, outcome.min_quotes = (
        found.line_value,
        found.window_value,
        choice.min_quotes,
    )
    outcome.needs_agreement = choice.needs_agreement
    if choice.route != "contract":
        outcome.reason = choice.reason
    if outcome.method == "direct":
        outcome.supplier_code, outcome.price = found.supplier_code, found.price
        if found.supplier_code is None or found.price is None:  # nobody known to buy from, or no price to buy at
            outcome.method = "manual"
            outcome.reason = (
                f"{choice.reason}; no supplier and price on file to buy from directly: a buyer has to source it"
            )
        else:
            outcome.reason = f"{choice.reason}; {_supplier_name(db, found.supplier_code)} at {format_inr(found.price)}"
    return outcome


_METHOD = {"single_source": "direct", "emergency": "direct", "reorder": "direct", "rfq": "rfq"}


def _on_contract(db: Session, line: dict[str, Any], as_of: date, base: dict[str, Any]) -> LineOutcome:
    """The contract the line can be bought on (method contract), or why not (method rfq)."""
    options = contract_options(db, line["item_code"], line["uom"], as_of)
    blocked = {
        code: docs.blocking
        for code in sorted({o.supplier_code for o in options})
        if not (docs := documents.check_supplier(db, code, as_of)).ok
    }
    options = [o for o in options if o.supplier_code not in blocked]
    pick = pick_contract_price(options, line["qty"])
    if pick is None:
        if options:
            why = f"No contract price applies to a quantity of {format_qty(line['qty'])} (the first price break is higher)"
        elif blocked:
            why = "The contract supplier cannot be used: " + "; ".join(
                f"{code}: {', '.join(problems)}" for code, problems in blocked.items()
            )
        else:
            why = "No contract price in force for this item"
        return LineOutcome(**base, method="rfq", reason=why)
    use = contract_usage.usage(db, pick.contract_code)
    breach = ceiling_breach(use.max_value, use.used, Decimal(line["qty"]) * pick.price)
    if breach > 0 and use.max_value is not None:
        return LineOutcome(
            **base,
            method="rfq",
            reason=(
                f"Contract {pick.contract_code} has {format_inr(use.remaining or 0)} left of its ceiling of "
                f"{format_inr(use.max_value)}, not enough for this line"
            ),
        )
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
        elif not (docs := documents.check_supplier(db, r["code"], as_of)).ok:
            notes.append(f"{r['code']} left out: " + "; ".join(docs.blocking))
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
                "rl.est_price, i.standard_price, coalesce(nullif(rl.category_code, ''), i.category_code) as category "
                "from request_line rl left join item i on i.code = rl.item_code where rl.request_no = :n "
                "and rl.status = 'open' and not exists (select 1 from sourcing_decision d "
                "where d.request_no = rl.request_no and d.line_no = rl.line_no) order by rl.line_no"
            ),
            {"n": request_no},
        ).mappings()
    ]
    result = SourcingResult(request_no)
    if not lines:
        db.rollback()
        return result

    router = route.Router(db, as_of)
    quote_lines: dict[str | None, list[tuple[dict[str, Any], LineOutcome]]] = {}
    for line in lines:
        outcome = _decide(db, router, line, req["priority"] == "emergency", as_of)
        result.lines.append(outcome)
        if outcome.method == "rfq":
            quote_lines.setdefault(line["category"], []).append((line, outcome))

    for category, group in quote_lines.items():
        _quotation(db, req, category, group, as_of, actor, result)

    for outcome in result.lines:
        _save_decision(db, request_no, outcome, as_of)
    audit.record(
        db,
        actor,
        "sourcing.decide",
        "request",
        request_no,
        after={
            "contract": result.count("contract"),
            "direct": result.count("direct"),
            "rfq": result.count("rfq"),
            "manual": result.count("manual"),
            "rfqs": result.rfqs,
            "routes": {str(o.line_no): {"route": o.route, "rule": o.route_rule} for o in result.lines},
        },
    )
    db.commit()

    for outcome in result.lines:
        if outcome.method == "direct" and outcome.needs_agreement:
            result.proposals.append(_propose_direct(db, tenant, request_no, outcome))
    if result.count("contract") or any(o.method == "direct" and not o.needs_agreement for o in result.lines):
        worker.enqueue(tenant, "order_request", {"request_no": request_no})  # these lines can be ordered at once
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
    min_quotes = max(min_quotes, *(o.min_quotes for _, o in group))  # the route's value bands can ask for more
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
            "insert into rfq (no, buyer, request_no, category_code, due_date, status, note, min_quotes) "
            "values (:no, :buyer, :req, :cat, :due, 'draft', :note, :mq)"
        ),
        {
            "no": rfq_no,
            "buyer": cat[2] if cat else None,
            "req": req["no"],
            "cat": category,
            "due": due,
            "note": note,
            "mq": min_quotes,
        },
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
        outcome.rfq_no, outcome.min_quotes = rfq_no, min_quotes
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


def _save_decision(db: Session, request_no: str, outcome: LineOutcome, decided_on: date) -> None:
    """Write the line's decision with what it was based on; the line moves to sourcing unless a buyer has to act."""
    db.execute(
        text(
            "insert into sourcing_decision (request_no, line_no, method, supplier_code, contract_code, price, uom, rfq_no, "
            "reason, route, route_rule, checks, line_value, window_value, min_quotes, decided_on, needs_agreement) "
            "values (:n, :l, :m, :s, :c, :p, :u, :r, :why, :route, :rule, cast(:checks as jsonb), :lv, :wv, :mq, :on, :agree)"
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
            "route": outcome.route,
            "rule": outcome.route_rule,
            "checks": json.dumps([c.as_json() for c in outcome.checks]),
            "lv": outcome.line_value,
            "wv": outcome.window_value,
            "mq": outcome.min_quotes or None,
            "on": decided_on,
            "agree": outcome.needs_agreement,
        },
    )
    db.execute(
        text("update request_line set status = :s where request_no = :n and line_no = :l"),
        {"s": "open" if outcome.method == "manual" else "sourcing", "n": request_no, "l": outcome.line_no},
    )


def _propose_direct(db: Session, tenant: str, request_no: str, outcome: LineOutcome) -> int:
    """A direct buy the purchase manager has to agree to before the order is made (rule 5)."""
    pid = proposals.propose(
        db,
        SERVICE,
        action="sourcing.agree_direct",
        summary=f"Agree to buy line {outcome.line_no} of {request_no} ({outcome.name}) directly: {outcome.reason}",
        payload={"tenant": tenant, "request_no": request_no, "line_no": outcome.line_no},
        required_permission=ROUTE_PERMISSION,
        value=outcome.line_value,
    )
    db.execute(
        text("update sourcing_decision set proposal_id = :p where request_no = :n and line_no = :l"),
        {"p": pid, "n": request_no, "l": outcome.line_no},
    )
    db.commit()
    return pid


@proposals.executor("sourcing.agree_direct")
def _direct_agreed(db: Session, payload: dict[str, Any], actor: Actor) -> dict[str, Any]:
    request_no, line_no = str(payload["request_no"]), int(payload["line_no"])
    audit.record(db, actor, "sourcing.direct_agreed", "request", f"{request_no}/{line_no}")
    db.commit()
    worker.enqueue(str(payload["tenant"]), "order_request", {"request_no": request_no})
    return {"request_no": request_no, "line_no": line_no}


# ---- a buyer changes the route ------------------------------------------------------------------


def _decision(db: Session, request_no: str, line_no: int) -> dict[str, Any]:
    row = (
        db.execute(
            text(
                "select d.*, rl.item_code, coalesce(i.name, rl.free_text) as name, rl.qty, rl.need_by, "
                "coalesce(nullif(rl.category_code, ''), i.category_code) as category, "
                "exists (select 1 from po_line p where p.request_no = d.request_no and p.request_line_no = d.line_no "
                "and p.status <> 'cancelled') as ordered from sourcing_decision d join request_line rl "
                "on rl.request_no = d.request_no and rl.line_no = d.line_no left join item i on i.code = rl.item_code "
                "where d.request_no = :n and d.line_no = :l for update of d"
            ),
            {"n": request_no, "l": line_no},
        )
        .mappings()
        .first()
    )
    if row is None:
        raise SourcingError(f"line {line_no} of {request_no} has no sourcing decision to change")
    if row["ordered"]:
        raise SourcingError(f"line {line_no} of {request_no} is already on an order")
    return dict(row)


def _replacement(old: dict[str, Any], method: str, reason: str) -> LineOutcome:
    """A new decision for the line that keeps what the route was based on (the value still counts in its window)."""
    return LineOutcome(
        line_no=old["line_no"],
        item_code=old["item_code"],
        name=old["name"],
        qty=old["qty"],
        uom=old["uom"],
        method=method,
        reason=reason,
        route_rule=old["route_rule"],
        checks=tuple(Check(c["test"], c["passed"], c["detail"]) for c in old["checks"] or []),
        line_value=old["line_value"],
        window_value=old["window_value"],
    )


def change_route(
    db: Session,
    tenant: str,
    request_no: str,
    line_no: int,
    new_route: str,
    actor: Actor,
    *,
    reason: str,
    as_of: date,
    supplier_code: str | None = None,
    price: Decimal | None = None,
    dispatch: bool = True,
) -> dict[str, Any]:
    """A buyer changes the route of a line not yet ordered. Tightening (to quotation) happens at once; loosening (a
    direct buy from a named supplier at a price) becomes a proposal for the purchase manager."""
    if not reason.strip():
        raise SourcingError("a reason is needed to change the route")
    old = _decision(db, request_no, line_no)
    was = str(old["route"] or old["method"])
    if new_route == "rfq":
        if not is_tighter(was, new_route):
            raise SourcingError(f"line {line_no} of {request_no} already goes to quotation")
        return _tighten(db, tenant, old, actor, reason, as_of, dispatch)
    if new_route != "direct":
        raise SourcingError(
            "a route can be changed to 'rfq' (tighter) or to 'direct' (looser, the purchase manager agrees)"
        )
    if not supplier_code or price is None or price <= 0:
        raise SourcingError("a direct buy needs a supplier and a price")
    if old["rfq_no"] and _rfq_status(db, old["rfq_no"]) != "draft":
        raise SourcingError(f"{old['rfq_no']} has gone to the suppliers: award it or cancel it instead")
    problem = route.Router(db, as_of).supplier_problem(supplier_code)
    if problem:
        raise SourcingError(f"cannot buy from {problem}")
    pid = proposals.propose(
        db,
        actor,
        action="sourcing.loosen_route",
        summary=(
            f"Buy line {line_no} of {request_no} ({old['name']}) directly from {_supplier_name(db, supplier_code)} at "
            f"{format_inr(price)} instead of by {was}: {reason}"
        ),
        payload={
            "tenant": tenant,
            "request_no": request_no,
            "line_no": line_no,
            "supplier_code": supplier_code,
            "price": str(price),
            "was": was,
            "reason": reason,
        },
        required_permission=ROUTE_PERMISSION,
        value=old["line_value"],
    )
    audit.record(
        db,
        actor,
        "sourcing.route_change_proposed",
        "request",
        f"{request_no}/{line_no}",
        before={"route": was},
        after={"route": "direct", "supplier": supplier_code, "price": price, "proposal": pid},
        reason=reason,
    )
    db.commit()
    return {"status": "proposed", "proposal_id": pid}


def _rfq_status(db: Session, rfq_no: str) -> str:
    return str(db.execute(text("select status from rfq where no = :n"), {"n": rfq_no}).scalar_one())


def _tighten(
    db: Session, tenant: str, old: dict[str, Any], actor: Actor, reason: str, as_of: date, dispatch: bool
) -> dict[str, Any]:
    request_no, was = str(old["request_no"]), str(old["route"] or old["method"])
    if old["proposal_id"]:
        proposals.withdraw(db, int(old["proposal_id"]), None, "the buyer sent the line to quotation instead")
        old = _decision(db, request_no, int(old["line_no"]))
    req = dict(db.execute(text("select * from request where no = :n"), {"n": request_no}).mappings().one())
    outcome = _replacement(old, "rfq", f"Buyer sent the line to quotation instead of {was}: {reason}")
    outcome.route = "rfq"
    if old["route_rule"]:
        outcome.min_quotes = quotes_needed(old["window_value"], route.rule_for(db, old["category"], as_of))
    db.execute(text("delete from sourcing_decision where id = :i"), {"i": old["id"]})
    result = SourcingResult(request_no)
    _quotation(db, req, old["category"], [(old, outcome)], as_of, actor, result)
    _save_decision(db, request_no, outcome, old["decided_on"] or as_of)
    audit.record(
        db,
        actor,
        "sourcing.route_change",
        "request",
        f"{request_no}/{old['line_no']}",
        before={"route": was, "method": old["method"], "supplier": old["supplier_code"]},
        after={"route": "rfq", "method": outcome.method, "rfq": outcome.rfq_no, "min_quotes": outcome.min_quotes},
        reason=reason,
    )
    db.commit()
    if dispatch:
        for rfq_no in result.rfqs:
            _dispatch(db, rfq_no, Decimal(req["total_value"]), actor, result)
    return {"status": "changed", "method": outcome.method, "rfq_no": outcome.rfq_no, "proposals": result.proposals}


@proposals.executor("sourcing.loosen_route")
def _loosen(db: Session, payload: dict[str, Any], actor: Actor) -> dict[str, Any]:
    """The purchase manager agreed: the line is bought directly from the named supplier at the price."""
    request_no, line_no = str(payload["request_no"]), int(payload["line_no"])
    old = _decision(db, request_no, line_no)
    if old["rfq_no"]:
        _take_off_rfq(db, str(old["rfq_no"]), request_no, line_no, actor)
    if old["proposal_id"]:
        proposals.withdraw(db, int(old["proposal_id"]), None, "the purchase manager agreed to a direct buy instead")
        old = _decision(db, request_no, line_no)
    supplier, price = str(payload["supplier_code"]), Decimal(str(payload["price"]))
    outcome = _replacement(
        old,
        "direct",
        f"Buyer chose a direct buy from {_supplier_name(db, supplier)} at {format_inr(price)} instead of "
        f"{payload['was']}: {payload['reason']}; the purchase manager agreed",
    )
    outcome.route, outcome.supplier_code, outcome.price = "direct", supplier, price
    db.execute(text("delete from sourcing_decision where id = :i"), {"i": old["id"]})
    _save_decision(db, request_no, outcome, old["decided_on"])
    audit.record(
        db,
        actor,
        "sourcing.route_change",
        "request",
        f"{request_no}/{line_no}",
        before={"route": payload["was"], "method": old["method"], "rfq": old["rfq_no"]},
        after={"route": "direct", "supplier": supplier, "price": price},
        reason=str(payload["reason"]),
    )
    db.commit()
    worker.enqueue(str(payload["tenant"]), "order_request", {"request_no": request_no})
    return {"request_no": request_no, "line_no": line_no, "supplier": supplier}


def _take_off_rfq(db: Session, rfq_no: str, request_no: str, line_no: int, actor: Actor) -> None:
    """Remove a line from an RFQ that has not gone out; an RFQ left empty is cancelled with its sending proposal."""
    rfq = db.execute(text("select status, proposal_id from rfq where no = :n for update"), {"n": rfq_no}).one()
    if rfq[0] != "draft":
        raise SourcingError(f"{rfq_no} has gone to the suppliers: award it or cancel it instead")
    db.execute(text("update sourcing_decision set rfq_no = null where rfq_no = :r and request_no = :n and line_no = :l"),
               {"r": rfq_no, "n": request_no, "l": line_no})  # fmt: skip
    db.execute(
        text("delete from rfq_line where rfq_no = :r and request_no = :n and request_line_no = :l"),
        {"r": rfq_no, "n": request_no, "l": line_no},
    )
    if db.execute(text("select count(*) from rfq_line where rfq_no = :r"), {"r": rfq_no}).scalar_one() == 0:
        db.execute(text("update rfq set status = 'cancelled' where no = :r"), {"r": rfq_no})
        audit.record(db, actor, "rfq.cancel", "rfq", rfq_no, reason="its only line is bought directly")
        if rfq[1]:
            db.commit()
            proposals.withdraw(db, int(rfq[1]), None, f"{rfq_no} was cancelled")


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
        summary=f"Send {rfq_no} to {count} supplier{'' if count == 1 else 's'}: {gate.reason}",
        payload={"rfq_no": rfq_no},
        required_permission="rfq.send",
        risk="low",
        document="rfq",
    )
    db.execute(text("update rfq set proposal_id = :p where no = :r"), {"p": pid, "r": rfq_no})
    db.commit()
    result.proposals.append(pid)


def item_lines(lines: list[dict[str, Any]]) -> str:
    return "\n".join(
        f"{n:>3}. {ln['description']}"
        + (f" ({ln['item_code']})" if ln["item_code"] else "")
        + f" - {format_qty(ln['qty'])} {ln['uom'] or ''}".rstrip()
        + (f" - needed by {ln['need_by']:%d %b %Y}" if ln["need_by"] else "")
        for n, ln in enumerate(lines, start=1)
    )


def _company(db: Session) -> str:
    return str(
        db.execute(text("select coalesce(short_name, legal_name) from company limit 1")).scalar() or "Our company"
    )


def _deliver_to(db: Session, request_no: str) -> str:
    return str(
        db.execute(
            text("select l.name from request r join location l on l.code = r.ship_to_location where r.no = :n"),
            {"n": request_no},
        ).scalar()
        or "as agreed"
    )


def question_reply_draft(db: Session, rfq_no: str, contact: str | None, question: str) -> str:
    """A reply a buyer can start from when a supplier asks about an RFQ: the question, a promise of an answer and the
    facts of the request (reply-by date, delivery place, items). It is a draft; nothing is sent."""
    rfq = db.execute(text("select * from rfq where no = :n"), {"n": rfq_no}).mappings().one()
    lines = [
        dict(r)
        for r in db.execute(text("select * from rfq_line where rfq_no = :n order by line_no"), {"n": rfq_no}).mappings()
    ]
    return templates.render(
        templates.QUESTION_REPLY,
        {
            "contact": contact or "Sir or Madam",
            "company": _company(db),
            "reference": rfq_no,
            "due_date": f"{rfq['due_date']:%d %b %Y}",
            "deliver_to": _deliver_to(db, str(rfq["request_no"])),
            "items": item_lines(lines),
            "question": " ".join(question.split()),
        },
    )


def rfq_template(db: Session) -> str:
    """The company's own wording for the request, or the standard one when it has none (or the saved text is no good)."""
    saved = _org_setting(db, "rfq_email_template", "")
    return saved if saved.strip() and not templates.problems(saved) else templates.DEFAULT_RFQ_TEMPLATE


def _body(
    template: str, rfq: dict[str, Any], lines: list[dict[str, Any]], contact: str | None, company: str, deliver_to: str
) -> str:
    return templates.render(
        template,
        {
            "contact": contact or "Sir or Madam",
            "company": company,
            "reference": str(rfq["no"]),
            "due_date": f"{rfq['due_date']:%d %b %Y}",
            "deliver_to": deliver_to,
            "items": item_lines(lines),
        },
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
    company = _company(db)
    category = db.execute(text("select name from item_category where code = :c"), {"c": rfq["category_code"]}).scalar()
    deliver_to = _deliver_to(db, str(rfq["request_no"]))
    pending = db.execute(
        text(
            "select rs.supplier_code, rs.email, (select c.name from supplier_contact c where c.supplier_code = rs.supplier_code "
            "and c.email = rs.email limit 1) as contact from rfq_supplier rs where rs.rfq_no = :n and rs.invited_at is null "
            "order by rs.supplier_code"
        ),
        {"n": rfq_no},
    ).all()
    subject = f"Request for quotation {rfq_no}" + (f": {category}" if category else "")
    template = rfq_template(db)
    sheet = quote_sheet.build(dict(rfq), lines, str(company))
    sent = failed = 0
    for code, email, contact in pending:
        try:
            emails.send_linked_email(
                db,
                email,
                subject,
                _body(template, dict(rfq), lines, contact, str(company), str(deliver_to)),
                linked_type="rfq",
                linked_ref=rfq_no,
                attachments=[(f"{rfq_no}-quotation-form.xlsx", quote_sheet.CONTENT_TYPE, sheet)],
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
        "direct": result.count("direct"),
        "rfq": result.count("rfq"),
        "manual": result.count("manual"),
        "rfqs": result.rfqs,
        "emails_sent": result.sent,
        "proposals": result.proposals,
        "notes": result.notes,
    }
