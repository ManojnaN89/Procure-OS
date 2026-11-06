"""Purchase orders: one per supplier, made from the lines of an approved request that are ready to be bought.

A line is ready when it was sourced on a contract, or its quotation was awarded. The price is the contract price or
the awarded net price, never an estimate; the GST rate is the item's (contract) or the quote's (award); the tax split
follows the supplier's state, the place of supply and the supplier's registration. A price well above the estimate
holds the order until a person accepts the variance (rule 5), then it goes for approval by the rule table.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.formulas.calendar import business_today
from p2p.formulas.gst import Registration
from p2p.formulas.money import format_inr
from p2p.formulas.orders import (
    LineAmounts,
    charge_rate,
    current_gst_rate,
    line_amounts,
    order_totals,
    variance_pct,
)
from p2p.orders import workflow
from p2p.platform import audit, numbering, proposals
from p2p.platform.sod import Actor

SERVICE = Actor("service", "ordering")


@dataclass
class ReadyLine:
    request_no: str
    request_line_no: int
    item_code: str | None
    name: str
    qty: Decimal
    uom: str | None
    base_qty: Decimal | None
    est_price: Decimal | None
    need_by: date | None
    gl_code: str | None
    category: str | None
    supplier_code: str
    unit_price: Decimal
    gst_rate: Decimal
    source: str  # contract or award
    source_ref: str  # the contract code or the quote number
    quote_no: str | None = None


@dataclass
class OrderResult:
    request_no: str
    pos: list[str] = field(default_factory=list)
    held: list[str] = field(default_factory=list)  # POs waiting for a price variance to be accepted
    approved: list[str] = field(default_factory=list)  # POs approved on the spot (the rule says so)
    waiting: list[str] = field(default_factory=list)  # POs waiting for approvers
    skipped: list[str] = field(default_factory=list)  # why some lines could not be ordered yet


def _org_setting(db: Session, key: str, default: str) -> str:
    value = db.execute(text("select value from org_setting where key = :k"), {"k": key}).scalar()
    return str(value) if value else default


def ready_lines(db: Session, request_no: str, as_of: date) -> tuple[list[ReadyLine], list[str]]:
    """The lines of the request that can be ordered now, and a note for each that cannot yet."""
    rows = db.execute(
        text(
            "select rl.line_no, rl.item_code, coalesce(i.name, rl.free_text) as name, rl.qty, rl.uom, rl.base_qty, "
            "rl.est_price, rl.need_by, rl.gl_code, coalesce(nullif(rl.category_code, ''), i.category_code) as category, "
            "d.method, d.supplier_code as d_supplier, d.contract_code, d.price as d_price, d.rfq_no, "
            "i.gst_rate_before, i.gst_rate_after, i.gst_rate_change_date "
            "from request_line rl join sourcing_decision d on d.request_no = rl.request_no and d.line_no = rl.line_no "
            "left join item i on i.code = rl.item_code "
            "where rl.request_no = :n and rl.status = 'sourcing' and d.method in ('contract', 'rfq') "
            "and not exists (select 1 from po_line p where p.request_no = rl.request_no "
            "and p.request_line_no = rl.line_no and p.status <> 'cancelled') order by rl.line_no"
        ),
        {"n": request_no},
    ).mappings()
    ready: list[ReadyLine] = []
    notes: list[str] = []
    for r in rows:
        common: dict[str, Any] = {
            "request_no": request_no,
            "request_line_no": r["line_no"],
            "item_code": r["item_code"],
            "name": r["name"],
            "qty": r["qty"],
            "uom": r["uom"],
            "base_qty": r["base_qty"],
            "est_price": r["est_price"],
            "need_by": r["need_by"],
            "gl_code": r["gl_code"],
            "category": r["category"],
        }
        if r["method"] == "contract":
            rate = current_gst_rate(r["gst_rate_before"], r["gst_rate_after"], r["gst_rate_change_date"], as_of)
            if rate is None:
                notes.append(
                    f"line {r['line_no']} ({r['name']}): the item has no GST rate, so it cannot be ordered yet"
                )
                continue
            ready.append(
                ReadyLine(
                    **common,
                    supplier_code=r["d_supplier"],
                    unit_price=r["d_price"],
                    gst_rate=rate,
                    source="contract",
                    source_ref=r["contract_code"],
                )
            )
            continue
        won = db.execute(
            text(
                "select a.supplier_code, a.price, a.quote_no, ql.gst_rate from award a "
                "join rfq_line l on l.rfq_no = a.rfq_no and l.line_no = a.rfq_line_no "
                "join quote_line ql on ql.quote_no = a.quote_no and ql.rfq_line_no = a.rfq_line_no "
                "where a.rfq_no = :r and l.request_no = :n and l.request_line_no = :l"
            ),
            {"r": r["rfq_no"], "n": request_no, "l": r["line_no"]},
        ).first()
        if won is None:
            continue  # not awarded yet: the buyer has not decided
        ready.append(
            ReadyLine(
                **common,
                supplier_code=won[0],
                unit_price=won[1],
                gst_rate=won[3],
                source="award",
                source_ref=won[2],
                quote_no=won[2],
            )
        )
    return ready, notes


def order_request(db: Session, tenant: str, request_no: str, actor: Actor = SERVICE, *, as_of: date) -> OrderResult:
    """Make a purchase order for each supplier of the request's ready lines."""
    db.execute(text("select pg_advisory_xact_lock(hashtext('ordering'))"))
    req = db.execute(text("select * from request where no = :n for update"), {"n": request_no}).mappings().first()
    result = OrderResult(request_no)
    if req is None:
        db.rollback()
        return result
    lines, notes = ready_lines(db, request_no, as_of)
    result.skipped.extend(notes)
    by_supplier: dict[str, list[ReadyLine]] = {}
    for line in lines:
        by_supplier.setdefault(line.supplier_code, []).append(line)
    created: list[tuple[str, bool]] = []
    for supplier, group in sorted(by_supplier.items()):
        outcome = _create_po(db, tenant, dict(req), supplier, group, as_of, actor, result)
        if outcome:
            created.append(outcome)
    db.commit()
    for po_no, held in created:
        if held:
            continue
        status = workflow.submit(db, tenant, po_no, actor, today=as_of)
        (result.approved if status == "approved" else result.waiting).append(po_no)
    return result


def _create_po(
    db: Session,
    tenant: str,
    req: dict[str, Any],
    supplier_code: str,
    lines: list[ReadyLine],
    as_of: date,
    actor: Actor,
    result: OrderResult,
) -> tuple[str, bool] | None:
    sup = (
        db.execute(
            text("select code, gstin, state_code, gst_reg_type, payment_terms_code from supplier where code = :c"),
            {"c": supplier_code},
        )
        .mappings()
        .one()
    )
    loc = (
        db.execute(text("select code, gstin, state_code from location where code = :c"), {"c": req["ship_to_location"]})
        .mappings()
        .one()
    )
    buyer_gstin = (
        loc["gstin"]
        or db.execute(text("select gstin from gst_registration where is_default order by gstin limit 1")).scalar()
    )
    place_of_supply = (str(buyer_gstin)[:2] if buyer_gstin else None) or loc["state_code"]
    supplier_state = sup["state_code"] or (str(sup["gstin"])[:2] if sup["gstin"] else None)
    if not supplier_state or not place_of_supply:
        result.skipped.append(
            f"{supplier_code}: the state of the supplier or of the delivery place is unknown, so the GST cannot be split"
        )
        return None
    registration = Registration(sup["gst_reg_type"] or "regular")

    amounts: list[LineAmounts] = [
        line_amounts(ln.qty, ln.unit_price, ln.gst_rate, supplier_state, place_of_supply, registration) for ln in lines
    ]
    charge_parts: list[tuple[str, Decimal, Decimal, LineAmounts]] = []
    rate = charge_rate([ln.gst_rate for ln in lines])
    for quote_no in sorted({ln.quote_no for ln in lines if ln.quote_no}):
        q = db.execute(text("select freight, packing_forwarding from quote where no = :n"), {"n": quote_no}).one()
        for kind, amount in (("freight", q[0]), ("packing_forwarding", q[1])):
            if amount and amount > 0:
                charge_parts.append(
                    (kind, amount, rate, line_amounts(1, amount, rate, supplier_state, place_of_supply, registration))
                )
    totals = order_totals([*amounts, *[c[3] for c in charge_parts]])

    contracts = {ln.source_ref for ln in lines if ln.source == "contract"}
    contract_code = (
        next(iter(contracts)) if len(contracts) == 1 and all(ln.source == "contract" for ln in lines) else None
    )
    terms = _terms(db, lines, contract_code, sup["payment_terms_code"])
    rfqs = {
        r[0]
        for ln in lines
        if ln.quote_no
        for r in db.execute(text("select rfq_no from quote where no = :n"), {"n": ln.quote_no})
    }
    buyer = db.execute(text("select buyer_user from item_category where code = :c"), {"c": lines[0].category}).scalar()
    po_no = numbering.next_number(db, "po", as_of, location_code=req["ship_to_location"])

    band = Decimal(_org_setting(db, "price_band_pct", "15"))
    over: list[str] = []
    for ln in lines:
        v = variance_pct(ln.unit_price, ln.est_price) if ln.est_price else None
        if v is not None and v > band:
            over.append(f"{ln.name}: {format_inr(ln.unit_price)} is {v}% above the estimate {format_inr(ln.est_price)}")
    hold = ("Price above the estimate by more than " + f"{band}%: " + "; ".join(over)) if over else None

    db.execute(
        text(
            "insert into po (no, po_type, supplier_code, supplier_gstin, buyer_gstin, ship_to_location, buyer, request_no, "
            "rfq_no, contract_code, payment_terms_code, delivery_terms, taxable, cgst, sgst, igst, total, status, hold_reason) "
            "values (:no, :type, :sup, :sg, :bg, :loc, :buyer, :req, :rfq, :cc, :pt, :dt, :tx, :cg, :sg2, :ig, :tot, 'draft', :hold)"
        ),
        {
            "no": po_no,
            "type": "contract_release" if contract_code else "standard",
            "sup": supplier_code,
            "sg": sup["gstin"],
            "bg": buyer_gstin,
            "loc": req["ship_to_location"],
            "buyer": buyer,
            "req": req["no"],
            "rfq": next(iter(rfqs)) if len(rfqs) == 1 else None,
            "cc": contract_code,
            "pt": terms[0],
            "dt": terms[1],
            "tx": totals.taxable,
            "cg": totals.cgst,
            "sg2": totals.sgst,
            "ig": totals.igst,
            "tot": totals.total,
            "hold": hold,
        },
    )
    for n, (ln, amt) in enumerate(zip(lines, amounts, strict=True), start=1):
        db.execute(
            text(
                "insert into po_line (po_no, line_no, request_no, request_line_no, item_code, description, qty, uom, base_qty, "
                "unit_price, est_price, gst_rate, taxable, cgst, sgst, igst, need_by, gl_code, source, source_ref) values "
                "(:po, :n, :req, :rl, :item, :d, :q, :u, :bq, :p, :est, :g, :tx, :cg, :sg, :ig, :need, :gl, :src, :ref)"
            ),
            {
                "po": po_no,
                "n": n,
                "req": ln.request_no,
                "rl": ln.request_line_no,
                "item": ln.item_code,
                "d": ln.name,
                "q": ln.qty,
                "u": ln.uom,
                "bq": ln.base_qty,
                "p": ln.unit_price,
                "est": ln.est_price,
                "g": ln.gst_rate,
                "tx": amt.taxable,
                "cg": amt.cgst,
                "sg": amt.sgst,
                "ig": amt.igst,
                "need": ln.need_by,
                "gl": ln.gl_code,
                "src": ln.source,
                "ref": ln.source_ref,
            },
        )
        db.execute(
            text("update request_line set status = 'ordered' where request_no = :n and line_no = :l"),
            {"n": ln.request_no, "l": ln.request_line_no},
        )
    for kind, amount, charge_rate_pct, amt in charge_parts:
        db.execute(
            text(
                "insert into po_charge (po_no, type, amount, gst_rate, cgst, sgst, igst) values (:po, :t, :a, :g, :cg, :sg, :ig)"
            ),
            {"po": po_no, "t": kind, "a": amount, "g": charge_rate_pct, "cg": amt.cgst, "sg": amt.sgst, "ig": amt.igst},
        )
    open_left = db.execute(
        text("select count(*) from request_line where request_no = :n and status in ('open', 'sourcing')"),
        {"n": req["no"]},
    ).scalar_one()
    if open_left == 0:
        db.execute(text("update request set status = 'closed' where no = :n"), {"n": req["no"]})
    audit.record(
        db,
        actor,
        "po.create",
        "po",
        po_no,
        after={
            "request": req["no"],
            "supplier": supplier_code,
            "lines": len(lines),
            "taxable": totals.taxable,
            "tax": totals.cgst + totals.sgst + totals.igst,
            "total": totals.total,
            "place_of_supply": place_of_supply,
            "supplier_state": supplier_state,
            "held": bool(hold),
        },
    )
    result.pos.append(po_no)
    if hold:
        pid = proposals.propose(
            db,
            SERVICE,
            action="po.accept_variance",
            summary=f"Accept a price above the estimate on {po_no}: {hold}",
            payload={"po_no": po_no, "tenant": tenant},
            required_permission="po.approve",
            risk="medium",
        )
        db.execute(text("update po set hold_proposal_id = :p where no = :n"), {"p": pid, "n": po_no})
        result.held.append(po_no)
        return po_no, True
    return po_no, False


def _terms(
    db: Session, lines: list[ReadyLine], contract_code: str | None, supplier_terms: str | None
) -> tuple[str | None, str | None]:
    """Payment and delivery terms: the contract's, else the quote's, else the supplier's own."""
    if contract_code:
        row = db.execute(
            text("select payment_terms_code, delivery_terms from contract where code = :c"), {"c": contract_code}
        ).first()
        if row:
            return row[0] or supplier_terms, row[1]
    for ln in lines:
        if ln.quote_no:
            row = db.execute(
                text("select payment_terms_code, delivery_terms from quote where no = :n"), {"n": ln.quote_no}
            ).first()
            if row and (row[0] or row[1]):
                return row[0] or supplier_terms, row[1]
    return supplier_terms, None


@proposals.executor("po.accept_variance")
def _accept_variance(db: Session, payload: dict[str, Any], actor: Actor) -> dict[str, Any]:
    """A person accepted the price above the estimate: the hold is lifted and the order goes for approval."""
    po_no, tenant = str(payload["po_no"]), str(payload["tenant"])
    db.execute(
        text("update po set hold_reason = null, hold_proposal_id = null where no = :n and status = 'draft'"),
        {"n": po_no},
    )
    audit.record(db, actor, "po.variance_accepted", "po", po_no)
    db.commit()

    return {"status": workflow.submit(db, tenant, po_no, actor, today=business_today())}


def sweep(db: Session, tenant: str, *, as_of: date, actor: Actor = SERVICE) -> list[OrderResult]:
    """Order every request that has lines ready to buy (a retry, and the safety net for jobs that were lost)."""
    numbers = [
        r[0]
        for r in db.execute(
            text(
                "select distinct rl.request_no from request_line rl where rl.status = 'sourcing' "
                "and not exists (select 1 from po_line p where p.request_no = rl.request_no "
                "and p.request_line_no = rl.line_no and p.status <> 'cancelled') order by 1"
            )
        )
    ]
    results = [order_request(db, tenant, no, actor, as_of=as_of) for no in numbers]
    return [r for r in results if r.pos or r.skipped]
