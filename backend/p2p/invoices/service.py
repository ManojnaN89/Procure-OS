"""Supplier invoices: taken in, matched against the order and the goods received, blocked or approved for payment.

An invoice is recorded as the supplier sent it. What is wrong with it is not a reason to refuse it but to block it:
the match finds the problem and puts a payment block on the invoice. A copy of an invoice already on file is kept as a
cancelled record that points at the original. A blocked invoice is released only by a person other than the one who
entered it, through a proposal (rule 5); nothing here releases a block by itself.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from p2p.formulas import matching as rules
from p2p.formulas import non_po as non_po_rules
from p2p.formulas.fy import financial_year
from p2p.formulas.gst import Registration
from p2p.formulas.gstin import is_valid_gstin
from p2p.formulas.money import format_inr
from p2p.formulas.orders import line_amounts
from p2p.formulas.words import count
from p2p.invoices import tolerances
from p2p.platform import audit, numbering, proposals
from p2p.platform.sod import Actor, user
from p2p.receiving.kinds import service_lines

BOOKED = ("approved", "part_paid", "paid")  # the invoice counts against what was received
D = Decimal


class InvoiceError(Exception):
    pass


class InvoiceInputError(InvoiceError):
    """What was typed cannot be an invoice: refused 422, where a state or rule refuses 409."""


@dataclass
class InvLine:
    po_no: str | None
    po_line_no: int | None
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
    kind: str = "po"  # po or non_po (a bill without a purchase order)
    category_code: str | None = None
    cost_centre_code: str | None = None
    gl_code: str | None = None
    documents: list[dict[str, Any]] = field(default_factory=list)  # {"id": int, "kind": "copy"|"supporting"}
    not_duplicate_reason: str | None = None
    no: str | None = (
        None  # an invoice of the old system loaded at cutover keeps its number; else the platform numbers it
    )


@dataclass
class Entered:
    no: str
    status: str  # approved, blocked, cancelled (a duplicate), awaiting_authorisation
    result: str | None  # matched, within_tolerance, blocked
    reasons: list[str] = field(default_factory=list)
    duplicate_of: str | None = None
    kind: str = "po"
    authoriser: str | None = None


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


def _supplier_state(sup: Mapping[str, Any]) -> str | None:
    return sup["state_code"] or (str(sup["gstin"])[:2] if sup["gstin"] else None)


def _state_names(db: Session) -> dict[str, str]:
    return {str(r[0]): str(r[1]) for r in db.execute(text("select code, name from ref_gst_state"))}


def _bill(db: Session, data: InvoiceIn, sup: dict[str, Any], received_on: date) -> dict[str, Any]:
    """Check a bill without an order against its rule (10.1) and say where it is charged. Raises before anything is
    written; the total is checked again after the amounts are worked out."""
    category = data.category_code
    if not category:
        raise InvoiceInputError("a bill without an order needs the category it is for")
    from p2p.invoices import non_po as bill_service  # noqa: PLC0415 - the bill's service builds on this module

    rule = bill_service.rule_in_force(db, category, received_on)
    cc = (
        db.execute(
            text(
                "select c.*, l.gstin as location_gstin, d.head_user from cost_centre c "
                "left join location l on l.code = c.location_code left join department d on d.code = c.department_code "
                "where c.code = :c"
            ),
            {"c": data.cost_centre_code},
        )
        .mappings()
        .first()
    )
    gl_code = data.gl_code or (rule.gl_code if rule else None)
    gl = (
        db.execute(
            text("select code, type, is_capex, is_tax_account from gl_account where code::text = cast(:c as text)"),
            {"c": gl_code},
        ).first()
        if gl_code and str(gl_code).isdigit()
        else None
    )
    approved = bool(
        db.execute(
            text("select 1 from supplier_category where supplier_code = :s and category_code = :c"),
            {"s": data.supplier_code, "c": category},
        ).first()
    )
    gl_ok = gl is not None and str(gl[1]) == "expense" and not gl[2] and not gl[3]
    problems = non_po_rules.rule_problems(
        rule=rule,
        category_name=(str(rule.category_name) if rule else None),
        total=D("0"),
        supplier_name=str(sup["legal_name"]),
        supplier_status=str(sup["status"]),
        supplier_approved=approved,
        cost_centre_ok=cc is not None and cc["active"] in (True, "true"),
        gl_code=gl_code,
        gl_ok=gl_ok,
        has_copy=any(d.get("kind") == "copy" for d in data.documents),
        has_supporting=any(d.get("kind") == "supporting" for d in data.documents),
    )
    if problems:
        first = problems[0]
        raise (InvoiceInputError(first.message) if first.status == 422 else InvoiceError(first.message))
    # every document must be on file, of a kind this knows
    ids = [int(d["id"]) for d in data.documents if d.get("id") is not None]
    found = (
        {int(r[0]) for r in db.execute(text("select id from document where id = any(:i)"), {"i": ids})}
        if ids
        else set()
    )
    if len(found) != len(set(ids)) or len(ids) != len(data.documents):
        raise InvoiceInputError("one of the documents attached to the bill is not on file")
    for d in data.documents:
        if d.get("kind") not in ("copy", "supporting"):
            raise InvoiceInputError("a document is attached as the copy or as a supporting document")
    if cc is None:
        raise InvoiceError("a bill without an order needs an active cost centre")
    return {
        "rule": rule,
        "cc": dict(cc),
        "gl_code": gl_code,
        "problems": problems,
        "location_gstin": cc["location_gstin"],
    }


def _check_bill_line_rate(ln: InvLine, rate: Decimal) -> None:
    if rate not in non_po_rules.GST_SLABS:
        raise InvoiceInputError(f"{rate}% is not a GST rate: use one of 0, 0.25, 3, 5, 12, 18 or 28")


def _live(db: Session, supplier: str, fy: str, type_: str, key: str) -> str | None:
    return db.execute(
        text(
            "select no from invoice where supplier_code = :s and fy = :f and dedupe_key = :k and type = :t "
            "and status <> 'cancelled' order by created_at, no limit 1"
        ),
        {"s": supplier, "f": fy, "k": key, "t": type_},
    ).scalar()


def _twin_irn(db: Session, irn: str | None, excluding: str) -> tuple[str, str, date, Decimal] | None:
    if not irn:
        return None
    row = db.execute(
        text(
            "select no, supplier_invoice_no, invoice_date, total from invoice where upper(irn) = upper(:i) "
            "and status <> 'cancelled' and no <> :x order by created_at limit 1"
        ),
        {"i": irn, "x": excluding},
    ).first()
    return (str(row[0]), str(row[1]), row[2], D(row[3])) if row else None


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
    if data.kind not in ("po", "non_po"):
        raise InvoiceInputError("the kind must be po or non_po")
    if not data.supplier_invoice_no.strip():
        raise InvoiceError("the supplier's invoice number is needed")
    if data.type == "invoice" and not data.lines:
        raise InvoiceError("an invoice needs at least one line")
    if data.type == "debit_note" and not data.ref_invoice_no:
        raise InvoiceInputError("a debit note must say which invoice it is for")
    bill = data.kind == "non_po"
    if bill and data.type != "invoice":
        raise InvoiceInputError("a note cannot be a bill without an order")
    for ln in data.lines:
        if ln.qty <= 0 or ln.unit_price < 0 or ln.discount < 0:
            where = f"{ln.po_no} line {ln.po_line_no}" if ln.po_no else "a bill line"
            raise InvoiceError(f"{where}: the quantity must be more than zero and the price not negative")
        if bill and (ln.po_no or ln.po_line_no):
            raise InvoiceInputError("a line of a bill cannot name an order line: a bill is all one kind")
        if not bill and (not ln.po_no or ln.po_line_no is None):
            raise InvoiceInputError("an invoice line needs its order number and line number")
    received_at = data.received_at or datetime.now(UTC)
    received_on = as_of or received_at.date()
    context = _bill(db, data, sup, received_on) if bill else None
    po_lines = (
        []
        if bill
        else [_po_line(db, data.supplier_code, str(ln.po_no or ""), int(ln.po_line_no or 0)) for ln in data.lines]
    )
    if data.type != "invoice" and data.ref_invoice_no:
        ref = db.execute(text("select supplier_code from invoice where no = :n"), {"n": data.ref_invoice_no}).first()
        if ref is None or ref[0] != data.supplier_code:
            raise InvoiceError(f"{data.ref_invoice_no} is not an invoice of {data.supplier_code}")
    if data.supplier_gstin and not is_valid_gstin(data.supplier_gstin):
        raise InvoiceInputError(f"{data.supplier_gstin} is not a valid GSTIN")
    supplier_gstin = (data.supplier_gstin or sup["gstin"]) if (bill or data.type != "invoice") else data.supplier_gstin
    if supplier_gstin and not is_valid_gstin(str(supplier_gstin)):
        raise InvoiceInputError(f"{supplier_gstin} is not a valid GSTIN")
    if data.buyer_gstin and not is_valid_gstin(data.buyer_gstin):
        raise InvoiceInputError(f"{data.buyer_gstin} is not a valid GSTIN")

    fy = financial_year(data.invoice_date)
    key = rules.normalize_invoice_no(data.supplier_invoice_no)
    db.execute(
        text("select pg_advisory_xact_lock(hashtext(:k))"),
        {"k": f"invoice|{data.supplier_code}|{fy}|{data.type}|{key}"},
    )
    twin = _live(db, data.supplier_code, fy, data.type, key)
    irn_twin = _twin_irn(db, data.irn, "") if not twin else None

    buyer = data.buyer_gstin or (
        context["location_gstin"] if context else (po_lines[0]["buyer_gstin"] if po_lines else None)
    )
    place = data.place_of_supply or (str(buyer)[:2] if buyer else None)
    registration = Registration(sup["gst_reg_type"] or "regular")
    state = _supplier_state(sup)
    amounts = []
    for n, ln in enumerate(data.lines):
        pl = po_lines[n] if po_lines else None
        rate = ln.gst_rate if ln.gst_rate is not None else (D(pl["gst_rate"]) if pl else D(0))
        if bill:
            _check_bill_line_rate(ln, rate)
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

    if bill and context is not None:
        rule = context["rule"]
        if rule is not None and total > rule.max_value:
            raise InvoiceError(
                f"a {rule.category_name.lower()} bill without an order may be at most "
                f"{non_po_rules.money(rule.max_value)}; this one is {non_po_rules.money(total)}: raise a purchase order"
            )
        earlier = db.execute(
            text(
                "select no, supplier_invoice_no, invoice_date, total from invoice where supplier_code = :s "
                "and category_code = :c and kind = 'non_po' and status <> 'cancelled' and total = :t "
                "and dedupe_key <> :k and abs(invoice_date - :d) <= :w order by invoice_date desc, no limit 1"
            ),
            {
                "s": data.supplier_code,
                "c": data.category_code,
                "t": total,
                "k": key,
                "d": data.invoice_date,
                "w": int(
                    db.execute(
                        text("select value from org_setting where key = 'non_po_duplicate_window_days'")
                    ).scalar()
                    or 7
                ),
            },
        ).first()
        if earlier is not None and not twin and not irn_twin and not (data.not_duplicate_reason or "").strip():
            raise InvoiceError(
                f"this may be the same bill as {earlier[0]} ({earlier[1]}, {earlier[2]:%d %b %Y}, "
                f"{non_po_rules.money(D(earlier[3]))}): enter it with a reason if it is not"
            )

    no = data.no or numbering.next_number(db, "invoice_booking", received_on)
    copy_of, reason = (twin, "number") if twin else ((irn_twin[0], "irn") if irn_twin else (None, None))
    params = {
        "no": no, "t": data.type, "s": data.supplier_code, "sg": supplier_gstin, "bg": buyer, "pos": place,
        "sin": data.supplier_invoice_no.strip(), "k": key, "d": data.invoice_date, "fy": fy, "irn": data.irn or None,
        "ack": data.ack_no, "ackd": data.ack_date, "ew": data.eway_bill_no, "rc": data.reverse_charge, "ra": received_at,
        "ro": received_on, "ch": data.channel, "tx": header_taxable, "cg": cgst, "sgs": sgst, "ig": igst, "ce": data.cess,
        "rnd": round_off, "tot": total, "ref": data.ref_invoice_no, "doc": data.document_ref, "by": str(user_id),
        "kind": data.kind, "cat": data.category_code if bill else None,
        "cc": data.cost_centre_code if bill else None, "rule": context["rule"].id if bill and context else None,
        "status": "cancelled" if copy_of else "received",
        "block": "duplicate" if copy_of else "none",
        "dupo": copy_of,
        "why": (
            f"The same invoice number is already on file as {copy_of}"
            if reason == "number"
            else f"The same IRN is already on file as {copy_of}"
            if reason == "irn"
            else None
        ),
        "suspect": earlier[0] if bill and not copy_of and earlier is not None else None,
        "ndr": data.not_duplicate_reason if bill and earlier is not None and not copy_of else None,
    }  # fmt: skip
    try:
        with db.begin_nested():
            db.execute(
                text(
                    "insert into invoice (no, type, kind, supplier_code, supplier_gstin, buyer_gstin, place_of_supply, "
                    "supplier_invoice_no, dedupe_key, invoice_date, fy, irn, ack_no, ack_date, eway_bill_no, reverse_charge, "
                    "received_at, received_on, channel, taxable, cgst, sgst, igst, cess, round_off, total, ref_invoice_no, "
                    "document_ref, entered_by, status, payment_block, duplicate_of, block_reason, category_code, "
                    "cost_centre_code, non_po_rule_id, suspect_of, not_duplicate_reason) values "
                    "(:no, :t, :kind, :s, :sg, :bg, :pos, :sin, :k, :d, :fy, :irn, :ack, :ackd, :ew, :rc, :ra, :ro, :ch, "
                    ":tx, :cg, :sgs, :ig, :ce, :rnd, :tot, :ref, :doc, :by, :status, :block, :dupo, :why, :cat, :cc, :rule, "
                    ":suspect, :ndr)"
                ),
                params,
            )
    except IntegrityError:
        live = _live(db, data.supplier_code, fy, data.type, key) or (_twin_irn(db, data.irn, "") or ("another",))[0]
        raise InvoiceError(
            f"{data.supplier_invoice_no} was entered at the same moment as {live}: look at it before entering again"
        ) from None
    for n, (ln, am) in enumerate(zip(data.lines, amounts, strict=True), start=1):
        pl = po_lines[n - 1] if po_lines else None
        db.execute(
            text(
                "insert into invoice_line (invoice_no, line_no, po_no, po_line_no, description, hsn_sac, qty, uom, unit_price, "
                "discount, taxable, gst_rate, cgst, sgst, igst, category_code, cost_centre_code, gl_code, location_code) "
                "values (:i, :n, :p, :l, :d, :h, :q, :u, :pr, :di, :tx, :r, :c, :s, :g, :cat, :cc, :gl, :loc)"
            ),
            {
                "i": no, "n": n, "p": ln.po_no, "l": ln.po_line_no,
                "d": ln.description or (pl["description"] if pl else None), "h": ln.hsn_sac,
                "q": ln.qty, "u": ln.uom or (pl["uom"] if pl else None), "pr": ln.unit_price, "di": ln.discount,
                "tx": am[1], "r": am[0], "c": am[2], "s": am[3], "g": am[4],
                "cat": data.category_code if bill else None, "cc": data.cost_centre_code if bill else None,
                "gl": context["gl_code"] if bill and context else None,
                "loc": context["cc"]["location_code"] if bill and context else None,
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
        after={
            "supplier": data.supplier_code, "number": data.supplier_invoice_no, "total": total, "type": data.type,
            "channel": data.channel, "kind": data.kind, "category": data.category_code,
            "cost_centre": data.cost_centre_code,
        },
    )  # fmt: skip
    if bill and data.documents:
        for d in data.documents:
            db.execute(
                text(
                    "insert into invoice_document_link (invoice_no, document_id, kind, added_by) "
                    "values (:i, :d, :k, :b) on conflict do nothing"
                ),
                {"i": no, "d": int(d["id"]), "k": str(d["kind"]), "b": str(user_id)},
            )
        audit.record(
            db, actor, "invoice.evidence", "invoice", no,
            after={"documents": [{"id": int(d["id"]), "kind": d["kind"]} for d in data.documents]},
        )  # fmt: skip

    if copy_of:
        audit.record(
            db, actor, "invoice.duplicate", "invoice", no,
            after={"duplicate_of": copy_of, "by": reason},
        )  # fmt: skip
        if commit:
            db.commit()
        return Entered(no, "cancelled", None, ["duplicate"], copy_of, data.kind)
    if bill and earlier is not None and params["suspect"]:
        audit.record(
            db, actor, "invoice.not_duplicate", "invoice", no,
            after={"suspect_of": params["suspect"], "reason": data.not_duplicate_reason},
        )  # fmt: skip
    if data.type != "invoice":
        problem, words = _note_header(
            db, data, sup, buyer, place, cgst, sgst, igst,
            po_lines[0]["buyer_gstin"] if po_lines else None, supplier_gstin,
        )  # fmt: skip
        if problem:
            db.execute(
                text("update invoice set status = 'blocked', payment_block = 'tax', block_reason = :w where no = :n"),
                {"w": words, "n": no},
            )
            audit.record(
                db, actor, "invoice.match", "invoice", no,
                after={"result": "blocked", "reason": problem, "status": "blocked"},
            )  # fmt: skip
            if commit:
                db.commit()
            return Entered(no, "blocked", "blocked", [problem], kind=data.kind)
        db.execute(
            text("update invoice set status = 'approved', approved_by = :b where no = :n"), {"b": str(user_id), "n": no}
        )
        if commit:
            db.commit()
        return Entered(no, "approved", None, kind=data.kind)
    if bill:
        return _match_bill(db, no, actor, commit=commit)
    return match_invoice(db, no, actor, commit=commit)


def _note_words(problem: str | None) -> str | None:
    return {
        rules.TAX: "A supplier under the composition scheme or without GST registration cannot charge GST",
        rules.IRN: "The supplier must issue an e-invoice and this one carries no IRN",
        rules.GSTIN: "The GSTIN on the note does not match the supplier master or our registration",
    }.get(problem or "", problem)


def _note_header(
    db: Session,
    data: InvoiceIn,
    sup: dict[str, Any],
    buyer: str | None,
    place: str | None,
    cgst: Decimal,
    sgst: Decimal,
    igst: Decimal,
    order_buyer: str | None = None,
    supplier_gstin: str | None = None,
) -> tuple[str | None, str | None]:
    """The header checks of a credit or debit note (10.7.5): composition, GSTINs and the tax head."""
    valid = {
        g
        for (g,) in db.execute(
            text("select gstin from supplier_address where supplier_code = :c and gstin is not null"),
            {"c": data.supplier_code},
        )
    } | {g for g in (sup["gstin"],) if g}
    header = rules.Header(
        sup["gst_reg_type"], sup["e_invoice_applicable"] in (True, "true"), data.type, data.irn,
        cgst + sgst + igst, supplier_gstin, frozenset(valid), buyer, order_buyer,
        _supplier_state(sup), place, cgst, sgst, igst,
    )  # fmt: skip
    problem = rules.header_problem(header)
    if problem == rules.TAX_HEAD:
        return problem, rules.header_words(header, _state_names(db))
    return problem, _note_words(problem)


def _match_bill(db: Session, no: str, actor: Actor, *, commit: bool = True) -> Entered:
    """The header and arithmetic checks of a bill without an order; a clean one goes to its authoriser."""
    inv = db.execute(text("select * from invoice where no = :n for update"), {"n": no}).mappings().one()
    sup = db.execute(text("select * from supplier where code = :c"), {"c": inv["supplier_code"]}).mappings().one()
    lines = [
        dict(r)
        for r in db.execute(
            text("select * from invoice_line where invoice_no = :n order by line_no"), {"n": no}
        ).mappings()
    ]
    valid = {
        g
        for (g,) in db.execute(
            text("select gstin from supplier_address where supplier_code = :c and gstin is not null"),
            {"c": inv["supplier_code"]},
        )
    } | {g for g in (sup["gstin"],) if g}
    cc_gstin = db.execute(
        text("select l.gstin from cost_centre c join location l on l.code = c.location_code where c.code = :c"),
        {"c": inv["cost_centre_code"]},
    ).scalar()
    tax = D(inv["cgst"]) + D(inv["sgst"]) + D(inv["igst"])
    header = rules.Header(
        sup["gst_reg_type"], sup["e_invoice_applicable"] in (True, "true"), inv["type"], inv["irn"], tax,
        inv["supplier_gstin"], frozenset(valid), inv["buyer_gstin"], cc_gstin,
        _supplier_state(dict(sup)), inv["place_of_supply"], D(inv["cgst"]), D(inv["sgst"]), D(inv["igst"]),
    )  # fmt: skip
    problem = rules.header_problem(header)
    arithmetic = rules.check_arithmetic(
        [rules.LineSums(D(x["qty"]), D(x["unit_price"]), D(x["discount"]), D(x["taxable"])) for x in lines],
        [D(r[0]) for r in db.execute(text("select amount from invoice_charge where invoice_no = :n"), {"n": no})],
        D(inv["taxable"]), tax, D(inv["round_off"]), D(inv["total"]),
    )  # fmt: skip
    reason = problem or (rules.ARITH if arithmetic.problems else None)
    if reason:
        words = (
            rules.header_words(header, _state_names(db))
            if reason == rules.TAX_HEAD
            else _explain(reason, lines, [], arithmetic.problems)
        )
        db.execute(
            text("update invoice set status = 'blocked', payment_block = :k, block_reason = :w where no = :n"),
            {"k": rules.BLOCK_KIND.get(reason, "tax"), "w": words, "n": no},
        )
        audit.record(
            db, actor, "invoice.match", "invoice", no,
            after={"result": "blocked", "reason": reason, "status": "blocked", "detail": words},
        )  # fmt: skip
        if commit:
            db.commit()
        return Entered(no, "blocked", "blocked", [reason], kind="non_po")
    db.execute(
        text(
            "update invoice set status = 'awaiting_authorisation', payment_block = 'none', block_reason = null where no = :n"
        ),
        {"n": no},
    )
    audit.record(
        db, actor, "invoice.match", "invoice", no,
        after={"result": "matched", "reason": None, "status": "awaiting_authorisation"},
    )  # fmt: skip
    from p2p.invoices import non_po  # noqa: PLC0415 - the bill's own service builds on this module

    db.commit()
    authoriser = non_po.to_authorise(db, _tenant_of(db), no)
    return Entered(no, "awaiting_authorisation", "matched", kind="non_po", authoriser=authoriser)


def _tenant_of(db: Session) -> str:
    from p2p.platform import tenants  # noqa: PLC0415 - only the bill's emails need the tenant

    slug = db.info.get("slug")
    return str(slug) if slug else (tenants.slug_of(str(db.get_bind().engine.url.database or "")) or "")


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


def match_invoice(db: Session, no: str, actor: Actor, *, commit: bool = True) -> Entered:
    """Compare the invoice with the order and the goods received; block it or approve it for payment."""
    inv = db.execute(text("select * from invoice where no = :n for update"), {"n": no}).mappings().one()
    sup = db.execute(text("select * from supplier where code = :c"), {"c": inv["supplier_code"]}).mappings().one()
    lines = [
        dict(r)
        for r in db.execute(
            text(
                "select l.*, pl.unit_price as order_price, pl.received_qty, pl.item_code, i.category_code "
                "from invoice_line l join po_line pl on pl.po_no = l.po_no and pl.line_no = l.po_line_no "
                "left join item i on i.code = pl.item_code where l.invoice_no = :n order by l.line_no"
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
        _supplier_state(dict(sup)), (str(inv["buyer_gstin"])[:2] if inv["buyer_gstin"] else None),
        Decimal(inv["cgst"]), Decimal(inv["sgst"]), Decimal(inv["igst"]),
    )  # fmt: skip
    tol_set = _tolerances(db, inv)
    tol_rows = tol_set.rows
    problem = rules.header_problem(header)
    verdicts: list[rules.LineVerdict] = []
    running: dict[tuple[str, int], Decimal] = {}
    services = {po_no: service_lines(db, po_no) for po_no in {ln["po_no"] for ln in lines}}
    for ln in lines:
        key = (ln["po_no"], ln["po_line_no"])
        available = _available(db, ln["po_no"], ln["po_line_no"], Decimal(ln["received_qty"]), no) - running.get(
            key, D(0)
        )
        tol = rules.tolerance_for(tol_rows, ln["category_code"])
        verdicts.append(
            rules.judge_line(
                Decimal(ln["order_price"]),
                Decimal(ln["unit_price"]) - Decimal(ln["discount"]),
                Decimal(ln["qty"]),
                available,
                tol,
                service=ln["po_line_no"] in services[ln["po_no"]],
            )
        )
        running[key] = running.get(key, D(0)) + Decimal(ln["qty"])
    result, reason = rules.overall(verdicts)
    arithmetic = rules.check_arithmetic(
        [rules.LineSums(Decimal(x["qty"]), Decimal(x["unit_price"]), Decimal(x["discount"]), Decimal(x["taxable"])) for x in lines],
        [Decimal(r[0]) for r in db.execute(text("select amount from invoice_charge where invoice_no = :n"), {"n": no})],
        Decimal(inv["taxable"]), tax, Decimal(inv["round_off"]), Decimal(inv["total"]),
    )  # fmt: skip
    if problem:
        result, reason = rules.BLOCKED, problem
    elif result != rules.BLOCKED and arithmetic.problems:
        result, reason = rules.BLOCKED, rules.ARITH

    db.execute(text("delete from match_result where invoice_no = :n and resolved_at is null"), {"n": no})
    for ln, v in zip(lines, verdicts, strict=True):
        db.execute(
            text(
                "insert into match_result (invoice_no, invoice_line_no, result, reason_codes, price_var, qty_var, "
                "tolerance_version) values (:n, :l, :r, :c, :p, :q, :tv)"
            ),
            {
                "n": no,
                "l": ln["line_no"],
                "r": v.result,
                "c": v.reason,
                "p": v.price_var,
                "q": v.qty_var,
                "tv": tol_set.version,
            },  # fmt: skip
        )
    if problem or arithmetic.problems:
        detail = problem and f"{problem}: see the invoice and the order" or "; ".join(arithmetic.problems)
        db.execute(
            text(
                "insert into match_result (invoice_no, result, reason_codes, detail, tolerance_version) "
                "values (:n, 'blocked', :c, :d, :tv)"
            ),
            {"n": no, "c": problem or rules.ARITH, "d": detail, "tv": tol_set.version},
        )
    if result == rules.BLOCKED:
        kind = rules.BLOCK_KIND[reason or rules.PRICE]
        why = (
            rules.header_words(header, _state_names(db))
            if reason == rules.TAX_HEAD
            else _explain(reason, lines, verdicts, arithmetic.problems)
        )
        db.execute(
            text("update invoice set status = 'blocked', payment_block = :k, block_reason = :w where no = :n"),
            {"k": kind, "w": why, "n": no},
        )
        status = "blocked"
    else:
        db.execute(
            text(
                "update invoice set status = 'approved', payment_block = 'none', block_reason = null, "
                "approved_by = coalesce(entered_by, approved_by) where no = :n"
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
        after={"result": result, "reason": reason, "status": status, "tolerance_version": tol_set.version},
        rule_version=tol_set.label,
    )
    _refresh_invoiced(db, [(x["po_no"], x["po_line_no"]) for x in lines])
    if commit:
        db.commit()
    return Entered(no, status, result, [reason] if reason else [])


def _explain(
    reason: str | None, lines: list[dict[str, Any]], verdicts: list[rules.LineVerdict], arithmetic: list[str]
) -> str:
    if reason == rules.PRICE:
        v = next((x for x in verdicts if x.reason == rules.PRICE), None)
        return (
            f"Price above the order price on {count(sum(1 for x in verdicts if x.reason == rules.PRICE), 'line')}, by up to {format_inr(max((x.price_var for x in verdicts), default=D(0)))} per unit"
            if v
            else "Price above the order price"
        )
    if reason == rules.QTY:
        over = [(ln["line_no"], v.qty_var) for ln, v in zip(lines, verdicts, strict=True) if v.reason == rules.QTY]
        return "Quantity invoiced is more than was received and accepted: " + ", ".join(
            f"line {n} by {q}" for n, q in over
        )
    if reason == rules.SERVICE:
        over = [(ln["line_no"], v.qty_var) for ln, v in zip(lines, verdicts, strict=True) if v.reason == rules.SERVICE]
        return "Service not confirmed: the requester has not confirmed the service invoiced on " + ", ".join(
            f"line {n} ({q} more than confirmed)" for n, q in over
        )
    if reason == rules.IRN:
        return "The supplier must issue an e-invoice and this one carries no IRN"
    if reason == rules.GSTIN:
        return "The GSTIN on the invoice does not match the supplier master or the order's delivery registration"
    if reason == rules.TAX:
        return "A supplier under the composition scheme or without GST registration cannot charge GST"
    return "; ".join(arithmetic) or "The invoice does not add up"


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
    inv = db.execute(text("select status, type, kind from invoice where no = :n for update"), {"n": no}).first()
    if inv is None:
        raise InvoiceError(f"{no} does not exist")
    if inv[0] not in ("blocked", "received", "awaiting_authorisation", "awaiting_approval"):
        raise InvoiceError(f"{no} is {inv[0]}; only an invoice still being worked can be cancelled")
    db.execute(
        text("update invoice set status = 'cancelled', cancel_reason = :r where no = :n"),
        {"r": reason.strip(), "n": no},
    )
    if inv[2] == "non_po":
        from p2p.platform import tasks  # noqa: PLC0415 - only a bill has these tasks

        db.execute(
            text(
                "update invoice_approval_step set status = 'cancelled' where invoice_no = :n "
                "and status in ('waiting', 'pending')"
            ),
            {"n": no},
        )
        for task_id in db.execute(
            text(
                "select id from task where payload->>'invoice_no' = :n "
                "and kind in ('invoice_authorise', 'invoice_approve') and status in ('open', 'overdue')"
            ),
            {"n": no},
        ).scalars():
            tasks.cancel_task(db, int(task_id), user_id, reason="the bill was cancelled")
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


def request_release(db: Session, no: str, reason: str, user_id: int) -> int:
    """Ask for a blocked invoice to be paid anyway. A person with the right to approve it decides (rule 5)."""
    if not reason.strip():
        raise InvoiceError("a reason is needed")
    inv = db.execute(
        text("select status, total, payment_block, block_reason, supplier_code, kind from invoice where no = :n"),
        {"n": no},
    ).first()
    if inv is None:
        raise InvoiceError(f"{no} does not exist")
    if inv[5] == "non_po":
        raise InvoiceError(
            "a bill without an order is never released by a proposal: correct it and match it again, or cancel it"
        )
    if inv[0] != "blocked":
        raise InvoiceError(f"{no} is {inv[0]}, not blocked")
    if inv[2] == "duplicate":
        raise InvoiceError("a duplicate is never paid")
    pending = db.execute(
        text(
            "select 1 from proposal where action = 'invoice.release_block' and status = 'pending' and payload->>'invoice' = :n"
        ),
        {"n": no},
    ).first()
    if pending:
        raise InvoiceError("a release for this invoice is already waiting for approval")
    return proposals.propose(
        db,
        user(user_id),
        action="invoice.release_block",
        summary=f"Pay {no} despite the block ({inv[3]}). Reason: {reason.strip()}",
        payload={"invoice": no, "reason": reason.strip()},
        required_permission="invoice.approve",
        risk="medium",
    )


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


@proposals.executor("invoice.release_block")
def _release(db: Session, payload: dict[str, Any], actor: Actor) -> dict[str, Any]:
    no, reason = str(payload["invoice"]), str(payload.get("reason") or "")
    inv = db.execute(text("select status, entered_by from invoice where no = :n for update"), {"n": no}).first()
    if inv is None or inv[0] != "blocked":
        raise InvoiceError(f"{no} is no longer blocked")
    if inv[1] == actor.id:
        raise InvoiceError("segregation of duties: the person who entered the invoice cannot release its block")
    if actor.kind == "user" and asked_or_confirmed(db, no, int(actor.id)):
        raise InvoiceError(
            "segregation of duties: the person who asked for or confirmed what is invoiced cannot also approve the invoice"
        )
    db.execute(
        text("update invoice set status = 'approved', payment_block = 'none', approved_by = :b where no = :n"),
        {"b": str(actor.id) if actor.kind == "user" else actor.identity, "n": no},
    )
    from p2p.invoices import payment  # noqa: PLC0415 - payment builds on this module

    payment.book_invoice(db, no)
    db.execute(
        text(
            "update match_result set resolved_by = :u, resolved_at = now(), resolution = :r "
            "where invoice_no = :n and result = 'blocked' and resolved_at is null"
        ),
        {"u": actor.id, "r": reason, "n": no},
    )
    keys = [
        (r[0], r[1])
        for r in db.execute(text("select po_no, po_line_no from invoice_line where invoice_no = :n"), {"n": no})
    ]
    _refresh_invoiced(db, keys)
    audit.record(
        db,
        actor,
        "invoice.release_block",
        "invoice",
        no,
        before={"status": "blocked"},
        after={"status": "approved"},
        reason=reason,
    )
    db.commit()
    return {"invoice": no, "status": "approved"}


def rematch_invoice(db: Session, no: str, user_id: int) -> Entered:
    """Run the match again on a blocked invoice (the goods may have been received, or the order corrected, since).

    It applies the same rules; it does not override them. Only a release by another person pays a blocked invoice.
    """
    inv = db.execute(text("select status, type, kind from invoice where no = :n"), {"n": no}).first()
    if inv is None:
        raise InvoiceError(f"{no} does not exist")
    if inv[1] != "invoice":
        raise InvoiceError(f"{no} is a {str(inv[1]).replace('_', ' ')}; only an invoice is matched")
    if inv[0] != "blocked":
        raise InvoiceError(f"{no} is {inv[0]}; only a blocked invoice is matched again")
    if inv[2] == "non_po":
        return _match_bill(db, no, user(user_id))
    return match_invoice(db, no, user(user_id))
