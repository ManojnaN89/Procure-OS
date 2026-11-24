"""Tax at source on an invoice or an advance: proposed by the platform, confirmed by the tax owner (2.5, D-16).

Each invoice line takes its section from its item category, else from the supplier's default, else none. The lines of
one section make one row: the taxable value still to pay after an advance taken off, the row of the tax table in force
on the booking date, and the amount from `formulas/payment.tds_for` (never typed and never a model's figure). A row is
confirmed by the system when it is zero and its section came from the item category; every other row waits for a holder
of `tds.confirm`. An invoice with a proposed row is not payable.

The year's cumulative for a supplier is kept per financial year and section: the booked invoices' rows, what the old
system bought (cutover), and the tax on advances only once those advances are settled (DF-17).
"""

import json
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.formulas import payment as rules
from p2p.formulas.calendar import business_today
from p2p.formulas.money import format_inr
from p2p.formulas.words import count
from p2p.platform import audit, settings
from p2p.platform.sod import Actor, user

D = Decimal
TWO = Decimal("0.01")
CONFIRMED = "confirmed"
PROPOSED = "proposed"
CANCELLED = "cancelled"


class TdsError(Exception):
    pass


@dataclass(frozen=True)
class LineSection:
    line_no: int
    taxable: Decimal
    section: str | None
    from_category: bool


@dataclass(frozen=True)
class Row:
    section_code: str
    label: str
    base: Decimal
    rate: Decimal
    amount: Decimal
    ref_id: int | None


# ---- which section a line takes ----------------------------------------------------------------


def _has_column(db: Session, table: str, column: str) -> bool:
    return bool(
        db.execute(
            text("select 1 from information_schema.columns where table_name = :t and column_name = :c"),
            {"t": table, "c": column},
        ).first()
    )


def _lines(db: Session, invoice_no: str) -> list[dict[str, Any]]:
    """The invoice's lines with the section their item category names and the supplier's default."""
    # NEEDS block 10: invoice_line.category_code and gl_code for a bill line without an order (0170)
    category = "il.category_code" if _has_column(db, "invoice_line", "category_code") else "NULL::text"
    rows = db.execute(
        text(  # noqa: S608 - the only inserted text is one of two fixed column names
            "select il.line_no, il.taxable, "  # noqa: S608 - fixed column name below
            f"{category} as line_category, coalesce(ic.default_tds_section, ic2.default_tds_section) as category_tds, "
            "coalesce(ic.code, ic2.code) as category_code, s.tds_section_default as supplier_tds_default, "
            "coalesce(ic.kind, ic2.kind) as category_kind, i.kind as item_kind "
            "from invoice_line il join invoice v on v.no = il.invoice_no "
            "join supplier s on s.code = v.supplier_code "
            "left join po_line pl on pl.po_no = il.po_no and pl.line_no = il.po_line_no "
            "left join item i on i.code = pl.item_code "
            "left join item_category ic on ic.code = i.category_code "
            f"left join item_category ic2 on ic2.code = {category} "  # noqa: S608 - the same fixed column
            "where il.invoice_no = :n order by il.line_no"
        ),
        {"n": invoice_no},
    ).mappings()
    return [dict(r) for r in rows]


def line_sections(db: Session, invoice_no: str) -> list[LineSection]:
    """The section of each line, in line order: the item category's, else the supplier's default, else 194Q for goods
    and none for a service (a person then picks the section)."""
    out: list[LineSection] = []
    for r in _lines(db, invoice_no):
        code = r["category_tds"] or r["supplier_tds_default"]
        service = str(r["category_kind"] or "") == "service" or str(r["item_kind"] or "") == "service"
        if not code and not service:
            code = "194Q"  # goods bought from a seller: section 194Q is the statutory default
        out.append(
            LineSection(int(r["line_no"]), D(r["taxable"]), str(code) if code else None, bool(r["category_tds"]))
        )
    return out


def advance_taxable_split(lines: list[LineSection], advance_taxable: Decimal) -> list[Decimal]:
    """The advance's taxable part taken off each line, in proportion to the line's taxable value (half up; the last
    line takes what rounding left over)."""
    total = sum((ln.taxable for ln in lines), D(0))
    if advance_taxable <= 0 or total <= 0:
        return [D(0) for _ in lines]
    used = min(advance_taxable, total)
    shares: list[Decimal] = []
    given = D(0)
    for n, ln in enumerate(lines):
        if n == len(lines) - 1:
            share = used - given
        else:
            share = (used * ln.taxable / total).quantize(TWO, rounding=ROUND_HALF_UP)
            given += share
        shares.append(min(share, ln.taxable))
    return shares


# ---- the tax table row in force ---------------------------------------------------------------


def section_row(db: Session, code: str, on: date, *, pan: str | None = None) -> rules.TdsSection:
    """The row of the tax table for this section on the booking date, the one whose payee type fits the PAN."""
    rows = [
        dict(r)
        for r in db.execute(
            text(
                "select id, act, section, payee_type, rate_pct, rate_no_pan_pct, threshold_single, threshold_annual, "
                "base_rule from ref_tds_section where section = :s and rate_pct is not null "
                "and effective_from <= :d and (effective_to is null or effective_to >= :d) "
                "order by effective_from desc, id desc"
            ),
            {"s": code, "d": on},
        ).mappings()
    ]
    kind = rules.payee_type(pan)
    fits = [
        r
        for r in rows
        if code != "194C"
        or (kind == "individual_huf" and "individual" in str(r["payee_type"]).lower())
        or (kind == "others" and "others" in str(r["payee_type"]).lower())
    ]
    if not fits:
        return rules.default_section(code, on)
    row = fits[0]
    base_rule = "entire amount" if code != "194Q" else "excess over threshold"
    return rules.TdsSection(
        int(row["id"]),
        code,
        rules.section_label(code, on),
        D(row["rate_pct"]),
        D(row["rate_no_pan_pct"]),
        None if row["threshold_single"] is None else D(row["threshold_single"]),
        None if row["threshold_annual"] is None else D(row["threshold_annual"]),
        base_rule,
        str(row["payee_type"] or "ANY"),
    )


def _supplier(db: Session, code: str) -> dict[str, Any]:
    row = db.execute(text("select * from supplier where code = :c"), {"c": code}).mappings().first()
    if row is None:
        raise TdsError(f"supplier {code} does not exist")
    return dict(row)


def _section_base(db: Session, invoice_no: str, code: str) -> Decimal:
    """The invoice's taxable value under one section, after the advance's taxable part is spread over its lines."""
    lines = line_sections(db, invoice_no)
    advance = D(
        db.execute(text("select advance_taxable_used from invoice where no = :n"), {"n": invoice_no}).scalar_one()
    )
    if not lines:  # a planted or cutover row kept without its lines: its whole taxable value belongs to the section
        taxable = D(db.execute(text("select taxable from invoice where no = :n"), {"n": invoice_no}).scalar_one())
        return max(taxable - advance, D(0))
    shares = advance_taxable_split(lines, advance)
    return sum((ln.taxable - sh for ln, sh in zip(lines, shares, strict=True) if ln.section == code), D(0))


def cumulative(db: Session, supplier: str, code: str, on: date, *, excluding: str | None = None) -> Decimal:
    """What was booked with the supplier this financial year under one section: booked invoices' rows (proposed or
    confirmed), what the old system bought, and the tax on advances once they are settled."""
    start = date(on.year if on.month >= 4 else on.year - 1, 4, 1)
    end = date(start.year + 1, 3, 31)
    fy = f"{start.year}-{(start.year + 1) % 100:02d}"
    old = db.execute(
        text("select taxable from cutover_supplier_ytd where supplier_code = :s and fy = :f and section = :c"),
        {"s": supplier, "f": fy, "c": code},
    ).scalar()
    invoice_nos = [
        str(r[0])
        for r in db.execute(
            text(
                "select distinct i.no from invoice i join tds_entry t on t.invoice_no = i.no "
                "and t.section_code = :c and t.status <> 'cancelled' "
                "where i.supplier_code = :s and i.type = 'invoice' and i.no <> coalesce(:n, '') "
                "and i.status not in ('cancelled', 'blocked', 'received') "
                "and t.booked_on >= :a and t.booked_on <= :b and (not :old or i.channel <> 'cutover')"
            ),
            {"s": supplier, "c": code, "n": excluding, "a": start, "b": end, "old": old is not None},
        )
    ]
    booked = sum((_section_base(db, no, code) for no in invoice_nos), D(0))
    advances = db.execute(
        text(
            "select coalesce(sum(case when :c = '194Q' then v.taxable else t.base end), 0) "
            "from tds_entry t join payment p on p.no = t.payment_no join advance v on v.payment_no = p.no "
            "where p.supplier_code = :s and t.section_code = :c and t.booked_on >= :a and t.booked_on <= :b "
            "and p.status = 'settled' and p.paid_on is not null"
        ),
        {"s": supplier, "c": code, "a": start, "b": end},
    ).scalar_one()
    return D(booked) + D(advances) + D(old or 0)


# ---- proposing and confirming -----------------------------------------------------------------


def _rows_for(db: Session, invoice_no: str, *, on: date) -> tuple[list[Row], list[dict[str, Any]]]:
    """The rows this invoice's lines make, and the facts each was worked out from."""
    inv = db.execute(text("select * from invoice where no = :n"), {"n": invoice_no}).mappings().one()
    supplier = _supplier(db, inv["supplier_code"])
    lines = line_sections(db, invoice_no)
    shares = advance_taxable_split(lines, D(inv["advance_taxable_used"]))
    by_section: dict[str, tuple[Decimal, bool]] = {}
    for ln, share in zip(lines, shares, strict=True):
        if ln.section is None:
            continue
        base, from_category = by_section.get(ln.section, (D(0), True))
        by_section[ln.section] = (base + ln.taxable - share, from_category and ln.from_category)
    out: list[Row] = []
    facts: list[dict[str, Any]] = []
    for code, (base, from_category) in by_section.items():
        if code == "194Q" and settings.value(db, "tds_applicable_on_goods", "true") != "true":
            continue
        rule = section_row(db, code, on, pan=supplier["pan"])
        before = cumulative(db, inv["supplier_code"], code, on, excluding=invoice_no)
        single = _order_value(db, invoice_no)
        lower = D(supplier["lower_tds_rate"]) if supplier["lower_tds_rate"] else None
        valid_to = supplier["lower_tds_valid_to"]
        limit = D(supplier["lower_tds_limit"]) if supplier["lower_tds_limit"] else None
        usable = lower is not None and valid_to is not None and valid_to >= on and limit is not None
        tds = rules.tds_for(
            rule,
            base,
            before,
            single,
            pan_inoperative=str(supplier["pan_status"] or "") == "inoperative",
            lower_rate=lower if usable else None,
            lower_limit=limit if usable else None,
        )
        out.append(Row(code, tds.section, tds.base, tds.rate, tds.amount, rule.id))
        facts.append(
            {
                "section_code": code,
                "from_category": from_category,
                "cumulative_before": str(before),
                "cumulative_after": str(before + base),
                "single": str(single),
                "threshold": None if rule.threshold_annual is None else str(rule.threshold_annual),
                "words": _words(code, supplier, before, base, tds, rule),
            }
        )
    return out, facts


def _order_value(db: Session, invoice_no: str) -> Decimal:
    """The order (its current version) behind the invoice's lines: the single-sum threshold is judged on it."""
    value = db.execute(
        text(
            "select max((v.snapshot->>'total')::numeric) from invoice_line il join po_version v on v.po_no = il.po_no "
            "and v.version = (select version from po where no = il.po_no) where il.invoice_no = :n"
        ),
        {"n": invoice_no},
    ).scalar()
    if value is not None:
        return D(value)
    total = db.execute(
        text("select max(po.total) from invoice_line il join po on po.no = il.po_no where il.invoice_no = :n"),
        {"n": invoice_no},
    ).scalar()
    return D(total or 0)


def _po_value(db: Session, po_no: str) -> Decimal:
    value = db.execute(text("select total from po where no = :p"), {"p": po_no}).scalar()
    return D(value or 0)


def _words(
    code: str, supplier: dict[str, Any], before: Decimal, base: Decimal, tds: rules.Tds, rule: rules.TdsSection
) -> str:
    name = supplier["trade_name"] or supplier["legal_name"]
    after = before + base
    threshold = rule.threshold_annual if rule.threshold_annual is not None else rule.threshold_single
    head = f"{code}: {name} this year {format_inr(after)}"
    if rule.base_rule == "entire amount":
        limit = (
            f"above {format_inr(threshold)}"
            if rule.threshold_annual is not None
            else f"each order above {format_inr(rule.threshold_single)}"
        )
        return (
            f"{head} is under its threshold: nothing to hold back"
            if tds.amount == 0
            else f"{head} is {limit}: {tds.rate.normalize():f}% of {format_inr(tds.base)}"
        )
    return (
        f"{head} is above {format_inr(threshold)}: {tds.rate.normalize():f}% of {format_inr(tds.base)}"
        if tds.base > 0
        else f"{head} is not above {format_inr(threshold)}: nothing to hold back"
    )


def propose(db: Session, invoice_no: str, *, on: date | None = None) -> list[Row]:
    """Work out the tax at source on an invoice just booked: one row per section, with the row of the table it used."""
    inv = db.execute(text("select * from invoice where no = :n for update"), {"n": invoice_no}).mappings().one()
    if inv["type"] != "invoice":
        return []
    on = on or inv["received_on"]
    db.execute(
        text(
            "update tds_entry set status = 'cancelled', change_reason = 'worked out again' "
            "where invoice_no = :n and status = 'proposed'"
        ),
        {"n": invoice_no},
    )
    rows, facts = _rows_for(db, invoice_no, on=on)
    auto_zero = settings.value(db, "tds_auto_confirm_zero", "true") == "true"
    now_confirmed = D(0)
    for row, fact in zip(rows, facts, strict=True):
        by_rule = row.amount == 0 and fact["from_category"] and auto_zero
        status = CONFIRMED if by_rule else PROPOSED
        db.execute(
            text(
                "insert into tds_entry (invoice_no, section, section_code, ref_tds_section_id, base, rate, amount, "
                "booked_on, quarter, status, proposed, confirmed_by, confirmed_at) values "
                "(:i, :s, :c, :r, :b, :rt, :a, :d, :q, :st, cast(:p as jsonb), cast(:by as text), "
                "case when cast(:by as text) is null then null else now() end) on conflict (invoice_no, section_code) "
                "where invoice_no is not null and status <> 'cancelled' do update set section = excluded.section, "
                "base = excluded.base, "
                "rate = excluded.rate, amount = excluded.amount, status = excluded.status, "
                "proposed = excluded.proposed, confirmed_by = excluded.confirmed_by, "
                "confirmed_at = excluded.confirmed_at"
            ),
            {
                "i": invoice_no, "s": row.label, "c": row.section_code, "r": row.ref_id, "b": row.base,
                "rt": row.rate, "a": row.amount, "d": on, "q": rules.quarter_label(on), "st": status,
                "p": json.dumps(fact, default=str), "by": "rule" if by_rule else None,
            },
        )  # fmt: skip
        if status == CONFIRMED:
            now_confirmed += row.amount
    db.execute(text("update invoice set tds_amount = :t where no = :n"), {"t": now_confirmed, "n": invoice_no})
    audit.record(
        db, audit.SYSTEM, "tds.propose", "invoice", invoice_no,
        after={"rows": [{"section": r.section_code, "base": r.base, "rate": r.rate, "amount": r.amount} for r in rows]},
    )  # fmt: skip
    return rows


def rebuild(db: Session, invoice_no: str, actor: Actor, reason: str) -> None:
    """The invoice changed (an advance was settled and taken off it): work the tax out again. A row whose section and
    amount are unchanged stays confirmed; a changed one goes back to the tax owner."""
    before = {
        str(r["section_code"]): r
        for r in db.execute(
            text(
                "select section_code, base, amount, status, confirmed_by from tds_entry "
                "where invoice_no = :n and status <> 'cancelled'"
            ),
            {"n": invoice_no},
        ).mappings()
    }
    propose(db, invoice_no)
    for r in db.execute(
        text(
            "select id, section_code, base, amount, status from tds_entry where invoice_no = :n and status = 'proposed'"
        ),
        {"n": invoice_no},
    ).mappings():
        old = before.get(str(r["section_code"]))
        if (
            old is not None
            and old["status"] == "confirmed"
            and D(old["base"]) == D(r["base"])
            and D(old["amount"]) == D(r["amount"])
        ):
            db.execute(
                text("update tds_entry set status = 'confirmed', confirmed_by = :b where id = :i"),
                {"b": old["confirmed_by"], "i": r["id"]},
            )
    confirmed = db.execute(
        text("select coalesce(sum(amount), 0) from tds_entry where invoice_no = :n and status = 'confirmed'"),
        {"n": invoice_no},
    ).scalar_one()
    db.execute(text("update invoice set tds_amount = :t where no = :n"), {"t": confirmed, "n": invoice_no})
    audit.record(db, actor, "tds.propose", "invoice", invoice_no, reason=reason)


def confirm(
    db: Session, ids: list[int], user_id: int, *, section_code: str | None = None, reason: str | None = None
) -> list[dict[str, Any]]:
    """The tax owner confirms rows (or changes a row's section first, with a reason): the amount is worked out again
    from the tax table, never typed."""
    if not ids:
        raise TdsError("say which rows to confirm")
    if section_code is not None and not (reason or "").strip():
        raise TdsError("a reason is needed to change the section")
    out: list[dict[str, Any]] = []
    for row_id in ids:
        row = db.execute(text("select * from tds_entry where id = :i for update"), {"i": row_id}).mappings().first()
        if row is None:
            raise TdsError(f"tds row {row_id} does not exist")
        if row["status"] != PROPOSED:
            raise TdsError(f"tds row {row_id} is {row['status']}, not waiting for confirmation")
        if section_code is not None:
            _change(db, dict(row), section_code, user_id, str(reason or ""))
            row = db.execute(text("select * from tds_entry where id = :i"), {"i": row_id}).mappings().one()
        db.execute(
            text("update tds_entry set status = 'confirmed', confirmed_by = :u, confirmed_at = now() where id = :i"),
            {"u": str(user_id), "i": row_id},
        )
        audit.record(db, user(user_id), "tds.confirm", "tds_entry", str(row_id), after={
            "invoice": row["invoice_no"], "section": row["section_code"], "base": row["base"], "amount": row["amount"],
        })  # fmt: skip
        if row["invoice_no"]:
            _refresh_invoice(db, str(row["invoice_no"]))
        out.append(dict(row) | {"status": CONFIRMED})
    return out


def _change(db: Session, row: dict[str, Any], section_code: str, user_id: int, reason: str) -> None:
    """Put a row under another section (or none) and work its amount out again."""
    invoice_no = str(row["invoice_no"])
    if not section_code:  # none: the tax owner says this line has no section
        db.execute(
            text("update tds_entry set status = 'cancelled', change_reason = :r where id = :i"),
            {"r": reason, "i": row["id"]},
        )
        audit.record(db, user(user_id), "tds.change", "tds_entry", str(row["id"]),
                     before={"section": row["section_code"]}, after={"section": None}, reason=reason)  # fmt: skip
        _refresh_invoice(db, invoice_no)
        return
    inv = db.execute(text("select * from invoice where no = :n"), {"n": invoice_no}).mappings().one()
    supplier = _supplier(db, inv["supplier_code"])
    on = inv["received_on"]
    rule = section_row(db, section_code, on, pan=supplier["pan"])
    lines = line_sections(db, invoice_no)
    shares = advance_taxable_split(lines, D(inv["advance_taxable_used"]))
    base = sum((ln.taxable - sh for ln, sh in zip(lines, shares, strict=True)), D(0))
    before = cumulative(db, inv["supplier_code"], section_code, on, excluding=invoice_no)
    tds = rules.tds_for(
        rule, base, before, _order_value(db, invoice_no),
        pan_inoperative=str(supplier["pan_status"] or "") == "inoperative",
    )  # fmt: skip
    fact = {"words": _words(section_code, supplier, before, base, tds, rule), "changed": True}
    db.execute(
        text("update tds_entry set section = :s, section_code = :c, ref_tds_section_id = :r, base = :b, rate = :rt, "
             "amount = :a, proposed = cast(:p as jsonb), change_reason = :why, confirmed_by = null, "
             "confirmed_at = null where id = :i"),
        {"s": tds.section, "c": section_code, "r": rule.id, "b": tds.base, "rt": tds.rate, "a": tds.amount,
         "p": json.dumps(fact, default=str), "why": reason, "i": row["id"]},
    )  # fmt: skip
    audit.record(db, user(user_id), "tds.change", "tds_entry", str(row["id"]),
                 before={"section": row["section_code"]}, after={"section": section_code, "amount": tds.amount},
                 reason=reason)  # fmt: skip
    _refresh_invoice(db, invoice_no)


def _refresh_invoice(db: Session, invoice_no: str) -> None:
    confirmed = db.execute(
        text("select coalesce(sum(amount), 0) from tds_entry where invoice_no = :n and status = 'confirmed'"),
        {"n": invoice_no},
    ).scalar_one()
    db.execute(text("update invoice set tds_amount = :t where no = :n"), {"t": confirmed, "n": invoice_no})


# ---- advances: tax when the advance is paid ----------------------------------------------------


def advance_sections(db: Session, po_no: str) -> list[LineSection]:
    """The sections of an order's lines (the advance's taxable part follows them)."""
    rows = db.execute(
        text(
            "select pl.line_no, pl.qty * pl.unit_price as taxable, "
            "coalesce(ic.default_tds_section, s.tds_section_default) as code, ic.code as category_code, "
            "ic.kind as category_kind, i.kind as item_kind "
            "from po_line pl join po on po.no = pl.po_no join supplier s on s.code = po.supplier_code "
            "left join item i on i.code = pl.item_code left join item_category ic on ic.code = i.category_code "
            "where pl.po_no = :p order by pl.line_no"
        ),
        {"p": po_no},
    ).mappings()
    out = []
    for r in rows:
        code = r["code"]
        service = str(r["category_kind"] or "") == "service" or str(r["item_kind"] or "") == "service"
        if not code and not service:
            code = "194Q"
        out.append(
            LineSection(int(r["line_no"]), D(r["taxable"]), str(code) if code else None, bool(r["category_code"]))
        )
    return out


def propose_advance(db: Session, payment_no: str) -> list[Row]:
    """Write the advance's tax rows when it is settled (or recorded): booked on its value date, on its taxable
    part. No row exists before then (DF-17)."""
    pay = (
        db.execute(
            text("select p.*, a.taxable, a.po_no from payment p join advance a on a.payment_no = p.no where p.no = :n"),
            {"n": payment_no},
        )
        .mappings()
        .first()
    )
    if pay is None:
        return []
    on = pay["value_date"] or pay["paid_on"] or business_today()
    out = _advance_rows(db, str(pay["supplier_code"]), str(pay["po_no"]), D(pay["taxable"]), on)
    total = D(0)
    for row in out:
        db.execute(
            text(
                "insert into tds_entry (payment_no, section, section_code, ref_tds_section_id, base, rate, amount, "
                "booked_on, quarter, status, proposed, confirmed_by, confirmed_at) values "
                "(:p, :s, :c, :r, :b, :rt, :a, :d, :q, 'confirmed', cast(:j as jsonb), 'rule', now())"
            ),
            {"p": payment_no, "s": row.label, "c": row.section_code, "r": row.ref_id, "b": row.base,
             "rt": row.rate, "a": row.amount, "d": on, "q": rules.quarter_label(on),
             "j": json.dumps({"advance": True})},
        )  # fmt: skip
        total += row.amount
    if total != D(pay["tds_amount"]):
        db.execute(text("update payment set tds_amount = :t where no = :n"), {"t": total, "n": payment_no})
        db.execute(
            text("update payment set amount = amount + :d where no = :n"),
            {"d": D(pay["tds_amount"]) - total, "n": payment_no},
        )
    audit.record(db, audit.SYSTEM, "tds.propose", "payment", payment_no,
                 after={"advance": True, "rows": [r.section_code for r in out], "tds": total})  # fmt: skip
    return out


def preview_advance(db: Session, po_no: str, taxable: Decimal, on: date) -> list[Row]:
    """What the tax on an advance would be when it is paid: shown on the run, no row written yet (DF-17)."""
    supplier = db.execute(text("select supplier_code from po where no = :p"), {"p": po_no}).scalar()
    if supplier is None:
        return []
    return _advance_rows(db, str(supplier), po_no, taxable, on)


def _advance_rows(db: Session, supplier_code: str, po_no: str, taxable: Decimal, on: date) -> list[Row]:
    supplier = _supplier(db, supplier_code)
    lines = advance_sections(db, po_no)
    shares = advance_taxable_split(lines, taxable)
    by_section: dict[str, Decimal] = {}
    for ln, sh in zip(lines, shares, strict=True):
        if ln.section is not None:
            by_section[ln.section] = by_section.get(ln.section, D(0)) + ln.taxable - sh
    out: list[Row] = []
    for code, base in by_section.items():
        if code == "194Q" and settings.value(db, "tds_applicable_on_goods", "true") != "true":
            continue
        rule = section_row(db, code, on, pan=supplier["pan"])
        before = cumulative(db, supplier_code, code, on)
        tds = rules.tds_for(
            rule, base, before, _po_value(db, po_no),
            pan_inoperative=str(supplier["pan_status"] or "") == "inoperative",
        )  # fmt: skip
        out.append(Row(code, tds.section, tds.base, tds.rate, tds.amount, rule.id))
    return out


def remove_advance(db: Session, payment_no: str) -> None:
    """The advance's payment failed or was removed: its tax row goes with it."""
    db.execute(text("delete from tds_entry where payment_no = :p"), {"p": payment_no})


# ---- reads -------------------------------------------------------------------------------------


def pending(db: Session) -> list[dict[str, Any]]:
    """The rows waiting for the tax owner."""
    rows = db.execute(
        text(
            "select t.id, t.invoice_no, t.section, t.section_code, t.base, t.rate, t.amount, t.proposed, "
            "i.supplier_code, coalesce(s.trade_name, s.legal_name) as supplier, i.supplier_invoice_no "
            "from tds_entry t join invoice i on i.no = t.invoice_no join supplier s on s.code = i.supplier_code "
            "where t.status = 'proposed' order by t.booked_on, t.id"
        )
    ).mappings()
    return [
        {
            "id": r["id"],
            "invoice": r["invoice_no"],
            "supplier": r["supplier"],
            "supplier_code": r["supplier_code"],
            "section": r["section"],
            "section_code": r["section_code"],
            "base": str(r["base"]),
            "rate": str(r["rate"]),
            "amount": str(r["amount"]),
            "words": (r["proposed"] or {}).get("words", ""),
            "invoice_no": r["supplier_invoice_no"],
        }
        for r in rows
    ]


def status_of(db: Session, invoice_no: str) -> str | None:
    """'proposed' when the invoice has a row waiting for the tax owner, else None."""
    return db.execute(
        text("select status from tds_entry where invoice_no = :n and status = 'proposed' limit 1"),
        {"n": invoice_no},
    ).scalar()


def summary(db: Session, invoice_no: str) -> list[dict[str, Any]]:
    rows = db.execute(
        text(
            "select section, section_code, status, base, rate, amount, confirmed_by, change_reason, proposed "
            "from tds_entry where invoice_no = :n order by section_code"
        ),
        {"n": invoice_no},
    ).mappings()
    return [
        {
            "section": r["section"],
            "section_code": r["section_code"],
            "status": r["status"],
            "base": str(r["base"]),
            "rate": str(r["rate"]),
            "amount": str(r["amount"]),
            "confirmed_by": r["confirmed_by"],
            "reason": r["change_reason"],
            "words": (r["proposed"] or {}).get("words", ""),
        }
        for r in rows
    ]


def count_waiting(db: Session) -> int:
    return int(db.execute(text("select count(*) from tds_entry where status = 'proposed'")).scalar_one())


def describe(db: Session, ids: list[int]) -> str:
    return count(len(ids), "tax row")
