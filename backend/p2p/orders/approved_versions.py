"""The approved versions of an order (task 0.2, DF-07): what each approval covered, kept when the order becomes approved.

The last approved version is the baseline a revision is measured from (formulas/po_versions.py); the first one gives the
base price of the cumulative price-rise rule (DF-08). Rows are written only here, by the approval that made them, in the
caller's transaction.
"""

import json
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.approvals import rules
from p2p.formulas.calendar import business_today
from p2p.formulas.po_versions import Charge, LineState, Snapshot


def snapshot(db: Session, po_no: str) -> Snapshot:
    """The order as it stands now: header, lines (with their contract price version) and charges. The one reader of an
    order's state, for approved versions and for every version (orders/versions.py)."""
    po = (
        db.execute(
            text(
                "select version, supplier_code, ship_to_location, payment_terms_code, total, rule_code, buyer_gstin, "
                "delivery_terms, contract_code, taxable, cgst, sgst, igst from po where no = :n"
            ),
            {"n": po_no},
        )
        .mappings()
        .one()
    )
    lines = tuple(
        LineState(
            int(r["line_no"]),
            r["item_code"],
            Decimal(r["qty"]),
            Decimal(r["unit_price"]),
            r["need_by"],
            r["description"],
            r["uom"],
            r["gst_rate"],
            r["taxable"],
            r["cgst"],
            r["sgst"],
            r["igst"],
            r["price_record_id"],
            r["contract_version"],
            r["status"],
        )  # fmt: skip
        for r in db.execute(
            text(
                "select l.line_no, l.item_code, l.qty, l.unit_price, l.need_by, l.description, l.uom, l.gst_rate, "
                "l.taxable, l.cgst, l.sgst, l.igst, l.price_record_id, pr.version as contract_version, l.status "
                "from po_line l left join price_record pr on pr.id = l.price_record_id "
                "where l.po_no = :n and l.status <> 'cancelled' order by l.line_no"
            ),
            {"n": po_no},
        ).mappings()
    )
    charges = tuple(
        Charge(str(r[0]), r[1], r[2], r[3], r[4], r[5])
        for r in db.execute(
            text("select type, amount, gst_rate, cgst, sgst, igst from po_charge where po_no = :n order by id"),
            {"n": po_no},
        )
    )
    return Snapshot(
        int(po["version"]),
        str(po["supplier_code"]),
        po["ship_to_location"],
        po["payment_terms_code"],
        Decimal(po["total"]),
        None,
        lines,
        po["rule_code"],
        po["buyer_gstin"],
        po["delivery_terms"],
        po["contract_code"],
        po["taxable"],
        po["cgst"],
        po["sgst"],
        po["igst"],
        charges,
    )


def band_max(db: Session, rule: rules.ApprovalRule, today: date | None = None) -> Decimal | None:
    """The top of the band that approved the order; None for the top band (no order rule above it)."""
    try:
        rules.find_rule(db, "po", rule.max_value + Decimal("0.01"), today or business_today())
    except rules.ApprovalRuleError:
        return None
    return Decimal(rule.max_value)


def band_of(db: Session, rule_code: str | None, rule_version: int | None) -> Decimal | None:
    """The band top of a stored rule code and version (for an approval by people, read from the order)."""
    row = db.execute(
        text(
            "select r.max_value from approval_rule r where r.document = 'po' and r.rule_code = :c and r.version = :v "
            "and exists (select 1 from approval_rule u where u.document = 'po' and u.min_value > r.max_value) limit 1"
        ),
        {"c": rule_code, "v": rule_version},
    ).first()
    return None if row is None else Decimal(row[0])


def record(
    db: Session,
    po_no: str,
    *,
    approved_by: str,
    rule_code: str | None,
    rule_version: int | None,
    band: Decimal | None,
) -> int:
    """Keep the version being approved and what it says; set `po.approved_version`. The caller commits. Returns the
    version."""
    now = snapshot(db, po_no)
    db.execute(
        text(
            "insert into po_approved_version (po_no, version, approved_by, rule_code, rule_version, band_max, total, "
            "snapshot) values (:p, :v, :by, :rc, :rv, :b, :t, cast(:s as jsonb)) "
            "on conflict (po_no, version) do update set approved_at = now(), approved_by = excluded.approved_by, "
            "rule_code = excluded.rule_code, rule_version = excluded.rule_version, band_max = excluded.band_max, "
            "total = excluded.total, snapshot = excluded.snapshot"
        ),
        {
            "p": po_no,
            "v": now.version,
            "by": approved_by,
            "rc": rule_code,
            "rv": rule_version,
            "b": band,
            "t": now.total,
            "s": json.dumps(now.as_json()),
        },
    )
    db.execute(text("update po set approved_version = :v where no = :n"), {"v": now.version, "n": po_no})
    return now.version


_SELECT = "select version, total, band_max, rule_code, snapshot from po_approved_version where po_no = :n "
_LAST = text(_SELECT + "order by version desc limit 1")
_FIRST = text(_SELECT + "order by version limit 1")


def _read(db: Session, po_no: str, query: Any) -> Snapshot | None:
    row = db.execute(query, {"n": po_no}).mappings().first()
    if row is None:
        return None
    return Snapshot.from_json(
        dict(row["snapshot"]),
        version=int(row["version"]),
        total=Decimal(row["total"]),
        band_max=None if row["band_max"] is None else Decimal(row["band_max"]),
        rule_code=row["rule_code"],
    )


def baseline(db: Session, po_no: str) -> Snapshot | None:
    """The last approved version: what a revision is measured from. None if the order was never approved."""
    return _read(db, po_no, _LAST)


def base_price(db: Session, po_no: str, line_no: int) -> Decimal | None:
    """A line's unit price in the first approved version: the base of the cumulative price-rise rule (DF-08)."""
    first = _read(db, po_no, _FIRST)
    if first is None:
        return None
    return next((ln.unit_price for ln in first.lines if ln.line_no == line_no), None)
