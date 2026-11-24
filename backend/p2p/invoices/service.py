"""Supplier invoices: taken in, matched against the order and the goods received, blocked or approved for payment.

An invoice is recorded as the supplier sent it. What is wrong with it is not a reason to refuse it but to block it:
the match finds the problem and puts a payment block on the invoice. A copy of an invoice already on file is kept as a
cancelled record that points at the original. A blocked invoice is released only by a person other than the one who
entered it, through a proposal (rule 5); nothing here releases a block by itself.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.formulas import matching as rules
from p2p.formulas.fy import financial_year
from p2p.formulas.gst import Registration, gst_split
from p2p.formulas.money import format_inr
from p2p.formulas.orders import line_amounts
from p2p.formulas.words import count
from p2p.invoices import tolerances
from p2p.platform import audit, numbering
from p2p.platform.sod import Actor, user
from p2p.receiving.kinds import service_lines

BOOKED = ("approved", "part_paid", "paid")  # the invoice counts against what was received
D = Decimal


class InvoiceError(Exception):
    pass


@dataclass
class InvLine:
    po_no: str
    po_line_no: int
    qty: Decimal
    unit_price: Decimal
    discount: Decimal = D(0)
    taxable: Decimal | None = None  # what the invoice says; worked out from quantity and price when not given
    gst_rate: Decimal | None = None
    cgst: Decimal | None = None
    sgst: Decimal | None = None
    igst: Decimal | None = None
    description: str | None = None
    hsn_sac: str | None = None
    uom: str | None = None


@dataclass
class InvCharge:
    type: str
    amount: Decimal
    gst_rate: Decimal = D(0)
    sac: str | None = None


@dataclass
class InvoiceIn:
    supplier_code: str
    supplier_invoice_no: str
    invoice_date: date
    lines: list[InvLine]
    charges: list[InvCharge] = field(default_factory=list)
    type: str = "invoice"
    supplier_gstin: str | None = None
    buyer_gstin: str | None = None
    place_of_supply: str | None = None
    irn: str | None = None
    ack_no: str | None = None
    ack_date: date | None = None
    eway_bill_no: str | None = None
    reverse_charge: bool = False
    channel: str = "manual"
    received_at: datetime | None = None
    taxable: Decimal | None = None
    cgst: Decimal | None = None
    sgst: Decimal | None = None
    igst: Decimal | None = None
    cess: Decimal = D(0)
    round_off: Decimal | None = None
    total: Decimal | None = None
    ref_invoice_no: str | None = None
    document_ref: str | None = None
    no: str | None = (
        None  # an invoice of the old system loaded at cutover keeps its number; else the platform numbers it
    )


@dataclass
class Entered:
    no: str
    status: str  # approved, blocked or cancelled (a duplicate)
    result: str | None  # matched, within_tolerance, blocked
    reasons: list[str] = field(default_factory=list)
    duplicate_of: str | None = None


def _tolerances(db: Session, inv: Any) -> tolerances.ToleranceSet:
    """The version the invoice is judged under: the one it was first matched under (a re-match gives the same result),
    else the version in force on the day it was received."""
    if inv["tolerance_version"] is not None:
        return tolerances.of_version(db, int(inv["tolerance_version"]))
    return tolerances.in_force(db, inv["received_on"])


def _po_line(db: Session, supplier: str, po_no: str, line_no: int) -> dict[str, Any]:
    row = (
        db.execute(
            text(
                "select l.*, po.supplier_code, po.status as po_status, po.buyer_gstin, po.ship_to_location, i.category_code "
                "from po_line l join po on po.no = l.po_no left join item i on i.code = l.item_code "
                "where l.po_no = :p and l.line_no = :l"
            ),
            {"p": po_no, "l": line_no},
        )
        .mappings()
        .first()
    )
    if row is None:
        raise InvoiceError(f"{po_no} line {line_no} does not exist")
    if row["supplier_code"] != supplier:
        raise InvoiceError(f"{po_no} is an order of {row['supplier_code']}, not of {supplier}")
    if row["po_status"] in ("draft", "submitted", "cancelled"):
        raise InvoiceError(f"{po_no} is {row['po_status']}: nothing can be invoiced against it")
    return dict(row)


def _supplier_state(sup: dict[str, Any]) -> str | None:
    return sup["state_code"] or (str(sup["gstin"])[:2] if sup["gstin"] else None)


def enter_invoice(
    db: Session, data: InvoiceIn, user_id: int, *, as_of: date | None = None, commit: bool = True
) -> Entered:
    """Record an invoice or credit note, then match it. Returns where it stands. Without `commit` the caller commits
    (the cutover import writes all its documents in one transaction)."""
    found = db.execute(text("select * from supplier where code = :c"), {"c": data.supplier_code}).mappings().first()
    if found is None:
        raise InvoiceError(f"supplier {data.supplier_code} does not exist")
    sup = dict(found)
    if data.type not in ("invoice", "credit_note", "debit_note"):
        raise InvoiceError("the type must be invoice, credit_note or debit_note")
    if not data.supplier_invoice_no.strip():
        raise InvoiceError("the supplier's invoice number is needed")
    if data.type == "invoice" and not data.lines:
        raise InvoiceError("an invoice needs at least one line")
    for ln in data.lines:
        if ln.qty <= 0 or ln.unit_price < 0 or ln.discount < 0:
            raise InvoiceError(
                f"{ln.po_no} line {ln.po_line_no}: the quantity must be more than zero and the price not negative"
            )
    po_lines = [_po_line(db, data.supplier_code, ln.po_no, ln.po_line_no) for ln in data.lines]
    if data.type != "invoice" and data.ref_invoice_no:
        ref = db.execute(text("select supplier_code from invoice where no = :n"), {"n": data.ref_invoice_no}).first()
        if ref is None or ref[0] != data.supplier_code:
            raise InvoiceError(f"{data.ref_invoice_no} is not an invoice of {data.supplier_code}")

    received_at = data.received_at or datetime.now(UTC)
    received_on = as_of or received_at.date()
    fy = financial_year(data.invoice_date)
    key = rules.normalize_invoice_no(data.supplier_invoice_no)
    twin = db.execute(
        text(
            "select no from invoice where supplier_code = :s and fy = :f and dedupe_key = :k and type = :t "
            "and status <> 'cancelled' order by created_at, no limit 1"
        ),
        {"s": data.supplier_code, "f": fy, "k": key, "t": data.type},
    ).scalar()

    buyer = data.buyer_gstin or (po_lines[0]["buyer_gstin"] if po_lines else None)
    place = data.place_of_supply or (str(buyer)[:2] if buyer else None)
    registration = Registration(sup["gst_reg_type"] or "regular")
    state = _supplier_state(sup)
    amounts = []
    for ln, pl in zip(data.lines, po_lines, strict=True):
        rate = ln.gst_rate if ln.gst_rate is not None else D(pl["gst_rate"])
        taxable = ln.taxable if ln.taxable is not None else (ln.qty * (ln.unit_price - ln.discount)).quantize(D("0.01"))
        if None in (ln.cgst, ln.sgst, ln.igst):
            if state and place:
                calc = line_amounts(ln.qty, ln.unit_price - ln.discount, rate, state, place, registration)
                tax = (calc.cgst, calc.sgst, calc.igst)
            else:
                tax = (D(0), D(0), D(0))
        else:
            tax = (ln.cgst or D(0), ln.sgst or D(0), ln.igst or D(0))
        amounts.append((rate, taxable, *tax))
    charge_tax: list[tuple[D, D, D]] = []
    for ch in data.charges:
        if state and place:
            calc = line_amounts(1, ch.amount, ch.gst_rate, state, place, registration)
            charge_tax.append((calc.cgst, calc.sgst, calc.igst))
        else:
            charge_tax.append((D(0), D(0), D(0)))
    taxable_total = sum((a[1] for a in amounts), D(0)) + sum((c.amount for c in data.charges), D(0))
    cgst = (
        data.cgst
        if data.cgst is not None
        else sum((a[2] for a in amounts), D(0)) + sum((c[0] for c in charge_tax), D(0))
    )
    sgst = (
        data.sgst
        if data.sgst is not None
        else sum((a[3] for a in amounts), D(0)) + sum((c[1] for c in charge_tax), D(0))
    )
    igst = (
        data.igst
        if data.igst is not None
        else sum((a[4] for a in amounts), D(0)) + sum((c[2] for c in charge_tax), D(0))
    )
    header_taxable = data.taxable if data.taxable is not None else taxable_total
    raw_total = header_taxable + cgst + sgst + igst + data.cess
    total = data.total if data.total is not None else raw_total.quantize(D("1"))
    # rounding to the rupee is all the rounding there is: a total that is further off than that is a problem to find
    round_off = data.round_off if data.round_off is not None else raw_total.quantize(D("1")) - raw_total

    no = data.no or numbering.next_number(db, "invoice_booking", received_on)
    db.execute(
        text(
            "insert into invoice (no, type, supplier_code, supplier_gstin, buyer_gstin, place_of_supply, supplier_invoice_no, "
            "dedupe_key, invoice_date, fy, irn, ack_no, ack_date, eway_bill_no, reverse_charge, received_at, received_on, channel, "
            "taxable, cgst, sgst, igst, cess, round_off, total, ref_invoice_no, document_ref, entered_by, status) values "
            "(:no, :t, :s, :sg, :bg, :pos, :sin, :k, :d, :fy, :irn, :ack, :ackd, :ew, :rc, :ra, :ro, :ch, :tx, :cg, :sgs, :ig, :ce, :rnd, :tot, :ref, :doc, :by, 'received')"
        ),
        {
            "no": no, "t": data.type, "s": data.supplier_code, "sg": data.supplier_gstin, "bg": buyer, "pos": place,
            "sin": data.supplier_invoice_no.strip(), "k": key, "d": data.invoice_date, "fy": fy, "irn": data.irn or None,
            "ack": data.ack_no, "ackd": data.ack_date, "ew": data.eway_bill_no, "rc": data.reverse_charge, "ra": received_at,
            "ro": received_on, "ch": data.channel, "tx": header_taxable, "cg": cgst, "sgs": sgst, "ig": igst, "ce": data.cess,
            "rnd": round_off, "tot": total, "ref": data.ref_invoice_no, "doc": data.document_ref, "by": str(user_id),
        },
    )  # fmt: skip
    for n, (ln, pl, am) in enumerate(zip(data.lines, po_lines, amounts, strict=True), start=1):
        db.execute(
            text(
                "insert into invoice_line (invoice_no, line_no, po_no, po_line_no, description, hsn_sac, qty, uom, unit_price, discount, "
                "taxable, gst_rate, cgst, sgst, igst) values (:i, :n, :p, :l, :d, :h, :q, :u, :pr, :di, :tx, :r, :c, :s, :g)"
            ),
            {
                "i": no, "n": n, "p": ln.po_no, "l": ln.po_line_no, "d": ln.description or pl["description"], "h": ln.hsn_sac,
                "q": ln.qty, "u": ln.uom or pl["uom"], "pr": ln.unit_price, "di": ln.discount, "tx": am[1], "r": am[0],
                "c": am[2], "s": am[3], "g": am[4],
            },
        )  # fmt: skip
    for ch in data.charges:
        db.execute(
            text("insert into invoice_charge (invoice_no, type, amount, gst_rate, sac) values (:i, :t, :a, :r, :s)"),
            {"i": no, "t": ch.type, "a": ch.amount, "r": ch.gst_rate, "s": ch.sac},
        )
    actor = user(user_id)
    audit.record(
        db, actor, "invoice.enter", "invoice", no,
        after={"supplier": data.supplier_code, "number": data.supplier_invoice_no, "total": total, "type": data.type, "channel": data.channel},
    )  # fmt: skip

    if twin:
        db.execute(
            text(
                "update invoice set status = 'cancelled', payment_block = 'duplicate', duplicate_of = :o, "
                "block_reason = :why where no = :n"
            ),
            {"o": twin, "why": f"The same invoice number is already on file as {twin}", "n": no},
        )
        audit.record(db, actor, "invoice.duplicate", "invoice", no, after={"duplicate_of": twin})
        if commit:
            db.commit()
        return Entered(no, "cancelled", None, ["duplicate"], twin)
    if data.type != "invoice":
        db.execute(text("update invoice set status = 'approved' where no = :n"), {"n": no})
        if commit:
            db.commit()
        return Entered(no, "approved", None)
    return match_invoice(db, no, actor, commit=commit)


def lock_lines(db: Session, keys: Iterable[tuple[str, int]]) -> None:
    """Lock the order lines a match, a release or a booking touches, one po at a time and in line order (OP-8).

    Every service that changes what may be invoiced takes the same locks, so two matches, a match and a receipt, or
    a match and a release on one line run one after the other.
    """
    by_po: dict[str, set[int]] = {}
    for po_no, line_no in keys:
        by_po.setdefault(po_no, set()).add(line_no)
    for po_no in sorted(by_po):
        db.execute(
            text("select 1 from po_line where po_no = :p and line_no = any(:ls) order by line_no for update"),
            {"p": po_no, "ls": sorted(by_po[po_no])},
        )


def _available(db: Session, po_no: str, line_no: int, received: Decimal, excluding: str) -> Decimal:
    """What may still be invoiced on an order line: accepted at receipt, less what other booked invoices already took."""
    taken = db.execute(
        text(
            "select coalesce(sum(l.qty), 0) from invoice_line l join invoice i on i.no = l.invoice_no "
            "where l.po_no = :p and l.po_line_no = :l and i.no <> :x and i.type = 'invoice' and i.status = any(:st)"
        ),
        {"p": po_no, "l": line_no, "x": excluding, "st": list(BOOKED)},
    ).scalar_one()
    return received - Decimal(taken)


def expected_gst(
    *,
    order_rate: Decimal,
    taxable: Decimal,
    registration: str | None,
    supplier_state: str | None,
    place_state: str | None,
    reverse_charge: bool,
) -> rules.TaxParts:
    """The tax the order expects on a line, from its rate and the two states (the invoice's taxable value)."""
    if not supplier_state or not place_state:
        return rules.TaxParts()
    split = gst_split(
        taxable, order_rate, supplier_state, place_state, Registration(registration or "regular"),
        reverse_charge=reverse_charge,
    )  # fmt: skip
    return rules.TaxParts(split.cgst, split.sgst, split.igst)


def _line_gst(
    ln: Mapping[Any, Any], inv: Any, sup: Mapping[Any, Any], tol: rules.Tolerance
) -> tuple[list[str], rules.TaxParts]:
    """The order's expected tax for one invoice line, and what is wrong with the line's rate and tax."""
    order_rate = Decimal(ln["order_gst_rate"] or 0)
    taxable = Decimal(ln["taxable"])
    supplier_state = sup["state_code"] or (str(ln["order_supplier_gstin"])[:2] if ln["order_supplier_gstin"] else None)
    place = str(inv["place_of_supply"] or ln["order_buyer_gstin"] or "")[:2] or None
    expected = expected_gst(
        order_rate=order_rate, taxable=taxable, registration=sup["gst_reg_type"], supplier_state=supplier_state,
        place_state=place, reverse_charge=bool(inv["reverse_charge"]),
    )  # fmt: skip
    charged = rules.TaxParts(Decimal(ln["cgst"]), Decimal(ln["sgst"]), Decimal(ln["igst"]))
    messages = rules.judge_gst(
        order_rate, Decimal(ln["gst_rate"]), expected, charged, tol.tax_line_abs,
        compare_amounts=not bool(inv["reverse_charge"]),
    )  # fmt: skip
    return [f"line {ln['line_no']}: {m}" for m in messages], expected


def gst_view(db: Session, inv: Any, ln: dict[str, Any]) -> dict[str, Any]:
    """The GST comparison the invoice screen shows for a line: the two rates and the expected and charged tax."""
    sup = (
        db.execute(text("select gst_reg_type, state_code from supplier where code = :c"), {"c": inv["supplier_code"]})
        .mappings()
        .one()
    )
    po = (
        db.execute(text("select supplier_gstin, buyer_gstin from po where no = :p"), {"p": ln["po_no"]})
        .mappings()
        .one()
    )
    order_rate = Decimal(ln["order_gst_rate"] or 0)
    supplier_state = sup["state_code"] or (str(po["supplier_gstin"])[:2] if po["supplier_gstin"] else None)
    place = str(inv["place_of_supply"] or po["buyer_gstin"] or "")[:2] or None
    expected = expected_gst(
        order_rate=order_rate, taxable=Decimal(ln["taxable"]), registration=sup["gst_reg_type"],
        supplier_state=supplier_state, place_state=place, reverse_charge=bool(inv["reverse_charge"]),
    )  # fmt: skip
    return {
        "order_rate": str(order_rate),
        "invoice_rate": str(Decimal(ln["gst_rate"])),
        "expected": {"cgst": str(expected.cgst), "sgst": str(expected.sgst), "igst": str(expected.igst)},
        "charged": {
            "cgst": str(Decimal(ln["cgst"])),
            "sgst": str(Decimal(ln["sgst"])),
            "igst": str(Decimal(ln["igst"])),
        },
    }


def match_invoice(
    db: Session,
    no: str,
    actor: Actor,
    *,
    commit: bool = True,
    reason: str | None = None,
) -> Entered:
    """Compare the invoice with the order and the goods received; block it or approve it for payment.

    The invoice row is locked first, then its order lines in `(po_no, line_no)` order, and only then is what may still
    be invoiced worked out, so nothing can over-invoice a line by running at the same time.
    """
    inv = db.execute(text("select * from invoice where no = :n for update"), {"n": no}).mappings().one()
    lock_lines(
        db,
        [
            (r[0], r[1])
            for r in db.execute(text("select po_no, po_line_no from invoice_line where invoice_no = :n"), {"n": no})
        ],
    )
    sup = db.execute(text("select * from supplier where code = :c"), {"c": inv["supplier_code"]}).mappings().one()
    lines = [
        dict(r)
        for r in db.execute(
            text(
                "select l.*, pl.unit_price as order_price, pl.received_qty, pl.item_code, pl.gst_rate as order_gst_rate, "
                "i.category_code, po.supplier_gstin as order_supplier_gstin, po.buyer_gstin as order_buyer_gstin "
                "from invoice_line l join po_line pl on pl.po_no = l.po_no and pl.line_no = l.po_line_no "
                "join po on po.no = l.po_no left join item i on i.code = pl.item_code "
                "where l.invoice_no = :n order by l.line_no"
            ),
            {"n": no},
        ).mappings()
    ]
    order = (
        db.execute(text("select buyer_gstin, supplier_gstin from po where no = :p"), {"p": lines[0]["po_no"]}).first()
        if lines
        else None
    )
    buyer_on_order = order[0] if order else None
    valid_gstins = {
        g
        for (g,) in db.execute(
            text("select gstin from supplier_address where supplier_code = :c and gstin is not null"),
            {"c": inv["supplier_code"]},
        )
    } | {g for g in (sup["gstin"], order[1] if order else None) if g}
    tax = Decimal(inv["cgst"]) + Decimal(inv["sgst"]) + Decimal(inv["igst"])
    header = rules.Header(
        sup["gst_reg_type"], sup["e_invoice_applicable"] in (True, "true"), inv["type"], inv["irn"], tax,
        inv["supplier_gstin"], frozenset(valid_gstins), inv["buyer_gstin"], buyer_on_order,
    )  # fmt: skip
    tol_set = _tolerances(db, inv)
    tol_rows = tol_set.rows
    problem = rules.header_problem(header)
    verdicts: list[rules.LineVerdict] = []
    gst_details: list[str | None] = []
    running: dict[tuple[str, int], Decimal] = {}
    services = {po_no: service_lines(db, po_no) for po_no in {ln["po_no"] for ln in lines}}
    for ln in lines:
        key = (ln["po_no"], ln["po_line_no"])
        available = _available(db, ln["po_no"], ln["po_line_no"], Decimal(ln["received_qty"]), no) - running.get(
            key, D(0)
        )
        tol = rules.tolerance_for(tol_rows, ln["category_code"])
        verdict = rules.judge_line(
            Decimal(ln["order_price"]),
            Decimal(ln["unit_price"]) - Decimal(ln["discount"]),
            Decimal(ln["qty"]),
            available,
            tol,
            service=ln["po_line_no"] in services[ln["po_no"]],
        )
        # The tax head (IGST against CGST and SGST) is block 10's header check; when it blocks, the amounts are not
        # compared again per line. A composition supplier stays its header reason. (Block 10 adds the header check.)
        # NEEDS block 10: `header_problem` returning `tax_head` (its 10.7.3); until then a line's amount reason stands.
        gst_messages: list[str] = []
        if problem not in (rules.TAX, rules.TAX_HEAD):
            gst_messages, _ = _line_gst(ln, inv, sup, tol)
        if gst_messages:
            verdict = rules.LineVerdict(
                rules.BLOCKED,
                verdict.reason or rules.GST,
                verdict.price_var,
                verdict.qty_var,
                (*verdict.reasons, rules.GST),
                verdict.variance_value,
            )
        verdicts.append(verdict)
        gst_details.append("; ".join(gst_messages) or None)
        running[key] = running.get(key, D(0)) + Decimal(ln["qty"])
    result, reason_code = rules.overall(verdicts)
    if any(d for d in gst_details):
        result, reason_code = rules.BLOCKED, reason_code or rules.GST
    arithmetic = rules.check_arithmetic(
        [rules.LineSums(Decimal(x["qty"]), Decimal(x["unit_price"]), Decimal(x["discount"]), Decimal(x["taxable"])) for x in lines],
        [Decimal(r[0]) for r in db.execute(text("select amount from invoice_charge where invoice_no = :n"), {"n": no})],
        Decimal(inv["taxable"]), tax, Decimal(inv["round_off"]), Decimal(inv["total"]),
    )  # fmt: skip
    if problem:
        result, reason_code = rules.BLOCKED, problem
    elif result != rules.BLOCKED and arithmetic.problems:
        result, reason_code = rules.BLOCKED, rules.ARITH

    invoice_reasons: list[str] = [problem] if problem else []
    for v in verdicts:
        for r in v.reasons:
            if r not in invoice_reasons:
                invoice_reasons.append(r)
    if not problem and arithmetic.problems and rules.ARITH not in invoice_reasons:
        invoice_reasons.insert(0, rules.ARITH)

    db.execute(text("delete from match_result where invoice_no = :n and resolved_at is null"), {"n": no})
    for ln, v, detail in zip(lines, verdicts, gst_details, strict=True):
        db.execute(
            text(
                "insert into match_result (invoice_no, invoice_line_no, result, reason_codes, reasons, price_var, qty_var, "
                "variance_value, detail, rules, tolerance_version) values (:n, :l, :r, :c, :rs, :p, :q, :vv, :d, 2, :tv)"
            ),
            {
                "n": no,
                "l": ln["line_no"],
                "r": v.result,
                "c": v.reason,
                "rs": list(v.reasons),
                "p": v.price_var,
                "q": v.qty_var,
                "vv": v.variance_value,
                "d": detail,
                "tv": tol_set.version,
            },  # fmt: skip
        )
    if problem or arithmetic.problems:
        detail = problem and f"{problem}: see the invoice and the order" or "; ".join(arithmetic.problems)
        db.execute(
            text(
                "insert into match_result (invoice_no, result, reason_codes, reasons, detail, rules, tolerance_version) "
                "values (:n, 'blocked', :c, :rs, :d, 2, :tv)"
            ),
            {"n": no, "c": problem or rules.ARITH, "rs": [problem or rules.ARITH], "d": detail, "tv": tol_set.version},
        )
    if result == rules.BLOCKED:
        kind = "tax" if any(rules.BLOCK_KIND.get(r) == "tax" for r in invoice_reasons) else "match"
        why = _explain(reason_code, lines, verdicts, gst_details, arithmetic.problems, problem)
        db.execute(
            text(
                "update invoice set status = 'blocked', payment_block = :k, block_reason = :w, match_rules = 2 "
                "where no = :n"
            ),
            {"k": kind, "w": why, "n": no},
        )
        status = "blocked"
    else:
        db.execute(
            text(
                "update invoice set status = 'approved', payment_block = 'none', block_reason = null, match_rules = 2 "
                "where no = :n"
            ),
            {"n": no},
        )
        from p2p.invoices import payment  # noqa: PLC0415 - payment builds on this module

        payment.book_invoice(db, no)
        status = "approved"
    db.execute(text("update invoice set tolerance_version = :v where no = :n"), {"v": tol_set.version, "n": no})
    audit.record(
        db,
        actor,
        "invoice.match",
        "invoice",
        no,
        after={
            "result": result, "reason": reason_code, "reasons": invoice_reasons, "status": status,
            "tolerance_version": tol_set.version, "rules": 2,
        },
        reason=reason,
        rule_version=f"{tol_set.label}; match rules 2",
    )  # fmt: skip
    _refresh_invoiced(db, [(x["po_no"], x["po_line_no"]) for x in lines])
    if commit:
        db.commit()
    return Entered(no, status, result, invoice_reasons)


def _explain(
    reason: str | None,
    lines: list[dict[str, Any]],
    verdicts: list[rules.LineVerdict],
    gst_details: list[str | None],
    arithmetic: list[str],
    header: str | None = None,
) -> str:
    """One sentence per reason, in the order the match records them."""
    sentences: list[str] = []
    if header:
        if header == rules.TAX:
            sentences.append("A supplier under the composition scheme or without GST registration cannot charge GST")
        elif header == rules.IRN:
            sentences.append("The supplier must issue an e-invoice and this one carries no IRN")
        elif header in (rules.GSTIN,):
            sentences.append(
                "The GSTIN on the invoice does not match the supplier master or the order's delivery registration"
            )
        elif header == rules.TAX_HEAD:
            sentences.append("The tax heads on the invoice do not match the supply: see the invoice and the order")
        else:
            sentences.append(str(header))
    price_lines = [(ln["line_no"], v) for ln, v in zip(lines, verdicts, strict=True) if rules.PRICE in v.reasons]
    if price_lines:
        biggest = max(v.price_var for _, v in price_lines)
        sentences.append(
            f"Price above the order price on {count(len(price_lines), 'line')}, by up to {format_inr(biggest)} per unit"
        )
    qty = [(ln["line_no"], v.qty_var) for ln, v in zip(lines, verdicts, strict=True) if rules.QTY in v.reasons]
    if qty:
        sentences.append(
            "Quantity invoiced is more than was received and accepted: " + ", ".join(f"line {n} by {q}" for n, q in qty)
        )
    service = [(ln["line_no"], v.qty_var) for ln, v in zip(lines, verdicts, strict=True) if rules.SERVICE in v.reasons]
    if service:
        sentences.append(
            "Service not confirmed: the requester has not confirmed the service invoiced on "
            + ", ".join(f"line {n} ({q} more than confirmed)" for n, q in service)
        )
    gst = [d for d in gst_details if d]
    if gst:
        sentences.append("GST differs from the order: " + "; ".join(gst))
    if arithmetic:
        sentences.extend(arithmetic)
    return "; ".join(sentences) or "The invoice does not add up"


def _refresh_invoiced(db: Session, keys: list[tuple[str, int]]) -> None:
    for po_no, line_no in set(keys):
        total = db.execute(
            text(
                "select coalesce(sum(l.qty), 0) from invoice_line l join invoice i on i.no = l.invoice_no "
                "where l.po_no = :p and l.po_line_no = :l and i.type = 'invoice' and i.status = any(:st)"
            ),
            {"p": po_no, "l": line_no, "st": list(BOOKED)},
        ).scalar_one()
        db.execute(
            text("update po_line set invoiced_qty = :q where po_no = :p and line_no = :l"),
            {"q": total, "p": po_no, "l": line_no},
        )


def cancel_invoice(db: Session, no: str, reason: str, user_id: int) -> None:
    """An invoice that will not be paid (the supplier is sending a corrected one, or it was wrong)."""
    if not reason.strip():
        raise InvoiceError("a reason is needed")
    inv = db.execute(text("select status, type from invoice where no = :n for update"), {"n": no}).first()
    if inv is None:
        raise InvoiceError(f"{no} does not exist")
    if inv[0] not in ("blocked", "received"):
        raise InvoiceError(f"{no} is {inv[0]}; only a blocked invoice can be cancelled")
    db.execute(
        text("update invoice set status = 'cancelled', cancel_reason = :r where no = :n"),
        {"r": reason.strip(), "n": no},
    )
    audit.record(
        db,
        user(user_id),
        "invoice.cancel",
        "invoice",
        no,
        before={"status": inv[0]},
        after={"status": "cancelled"},
        reason=reason,
    )
    db.commit()


def request_release(db: Session, no: str, reason: str, user_id: int, *, tenant: str | None = None) -> int:
    """Ask for a blocked invoice to be paid anyway. The release rules live in `invoices/release.py` (block 11)."""
    from p2p.invoices import release  # noqa: PLC0415 - the release builds on this module

    return release.request_release(db, no, reason, user_id, tenant=tenant)


def asked_or_confirmed(db: Session, no: str, user_id: int) -> bool:
    """Whether this login is the requester of a line on the invoice, or confirmed a service on it."""
    from p2p.approvals.workflow import org_code_of  # noqa: PLC0415 - approvals pulls in more than invoices needs

    me = org_code_of(db, user_id)
    if me is None:
        return False
    return bool(
        db.execute(
            text(
                "select 1 from invoice_line il join po_line pl on pl.po_no = il.po_no and pl.line_no = il.po_line_no "
                "left join request r on r.no = pl.request_no where il.invoice_no = :n and (:me in (r.requester, r.on_behalf_of) "
                "or exists (select 1 from service_entry e where e.po_no = il.po_no and e.po_line_no = il.po_line_no "
                "and e.accepted_by = :me)) limit 1"
            ),
            {"n": no, "me": me},
        ).first()
    )


READ_PERMISSIONS = ("invoice.view", "invoice.book", "invoice.match", "invoice.approve")


def can_read(db: Session, user_id: int, no: str | None = None) -> bool:
    """Who may see an invoice (11.10): an invoice permission, or a bill's own authoriser and step approvers.

    Block 10 adds the bill readers (its authoriser and the people its approval names); until then an invoice is read
    by the invoice permissions alone.
    """
    from p2p.platform import permissions  # noqa: PLC0415 - permissions does not build on invoices

    granted = set(permissions.granted(db, user_id))
    if granted & set(READ_PERMISSIONS):
        return True
    # NEEDS block 10: a bill's own authoriser and step approvers may read it too (10.11)
    return False


def rematch_invoice(db: Session, no: str, user_id: int) -> Entered:
    """Run the match again on a blocked invoice (the goods may have been received, or the order corrected, since).

    It applies the same rules; it does not override them. Only a release by another person pays a blocked invoice.
    """
    inv = db.execute(text("select status, type from invoice where no = :n"), {"n": no}).first()
    if inv is None:
        raise InvoiceError(f"{no} does not exist")
    if inv[0] != "blocked" or inv[1] != "invoice":
        raise InvoiceError(f"{no} is {inv[0]}; only a blocked invoice is matched again")
    return match_invoice(db, no, user(user_id))


from p2p.invoices import release as _release  # noqa: E402, F401 - registers the release executor and guard
