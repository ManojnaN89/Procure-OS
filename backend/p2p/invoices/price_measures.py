"""One row per price measure and event (R6, 13.2, 13.3, D47, DF-29).

The arithmetic is `formulas/ppv.py`; this service reads the documents and keeps each row with its reference (the
standard version, the contract price, the benchmark or the estimate), so a row can be worked out again. Unique per
measure, part and source line: a second run adds nothing.
"""

import json
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.formulas import ppv as rules
from p2p.formulas.calendar import IST, business_today
from p2p.formulas.price_gap import gap_pct
from p2p.masters import standard_prices
from p2p.platform import audit, routing, settings, worker
from p2p.platform.sod import Actor

LABELS = {
    "ppv": "Accounting PPV",
    "contract_variance": "Contract variance",
    "market_gap": "Market gap",
    "saving_potential": "Saving, potential",
    "saving_realised": "Saving, realised",
}


def _period(on: date) -> str:
    return f"{on.year}-{on.month:02d}"


def _unit(qty: Decimal, taxable: Decimal) -> Decimal | None:
    return None if qty == 0 else rules.unit(qty, taxable)


def _version(db: Session, item: str, on: date) -> int | None:
    return db.execute(
        text(
            "select version from item_standard_price where item_code = :i and status = 'active' "
            "and effective_from <= :d and (effective_to is null or effective_to >= :d) "
            "order by effective_from desc, version desc limit 1"
        ),
        {"i": item, "d": on},
    ).scalar()


def _keep(db: Session, **row: Any) -> bool:
    """Insert once; False when this measure, part and source line is already kept. Returns whether it was added."""
    return (
        db.execute(
            text(
                "insert into price_measure (measure, part, source_type, source_ref, source_line, item_code, "
                "supplier_code, po_no, po_line_no, on_date, period, qty, unit_price, reference_price, reference, amount, "
                "pct, basis) values (:measure, :part, :source_type, :source_ref, :source_line, :item_code, :supplier_code, "
                ":po_no, :po_line_no, :on_date, :period, :qty, :unit_price, :reference_price, :reference, :amount, :pct, "
                "cast(:basis as jsonb)) on conflict (measure, part, source_type, source_ref, source_line) do nothing "
                "returning id"
            ),
            {**row, "basis": json.dumps(row.get("basis") or {}, default=str)},
        ).scalar()
        is not None
    )


def _invoice_lines(db: Session, through: date) -> list[dict[str, Any]]:
    rows = db.execute(
        text(
            "select i.no, i.type, i.supplier_code, i.received_on, coalesce(i.booked_at::date, i.received_on) as on_date, "
            "l.line_no, l.po_no, l.po_line_no, l.qty, l.taxable, pl.item_code, pl.uom, pl.unit_price as po_price, "
            "pl.source, pl.request_no, pl.request_line_no, pl.price_record_id, pr.price as contract_price, "
            "pr.contract_code, rl.est_price "
            "from invoice i join invoice_line l on l.invoice_no = i.no "
            "left join po_line pl on pl.po_no = l.po_no and pl.line_no = l.po_line_no "
            "left join price_record pr on pr.id = pl.price_record_id "
            "left join request_line rl on rl.request_no = pl.request_no and rl.line_no = pl.request_line_no "
            "where i.status in ('approved', 'part_paid', 'paid') "
            "and coalesce(i.booked_at::date, i.received_on) <= :d "
            "and i.type in ('invoice', 'credit_note', 'debit_note') order by i.no, l.line_no"
        ),
        {"d": through},
    ).mappings()
    return [dict(r) for r in rows]


def _receipt_lines(db: Session, through: date) -> list[dict[str, Any]]:
    return [
        dict(r)
        for r in db.execute(
            text(
                "select g.no as gr_no, g.accepted_on as on_date, l.po_line_no, l.qty_accepted as qty, "
                "pl.item_code, pl.uom, pl.unit_price, pl.po_no, po.supplier_code "
                "from gr_line l join goods_receipt g on g.no = l.gr_no join po_line pl on pl.po_no = l.po_no "
                "and pl.line_no = l.po_line_no join po on po.no = l.po_no "
                "where g.accepted_on is not null and g.accepted_on <= :d and l.qty_accepted > 0 "
                "order by g.no, l.po_line_no"
            ),
            {"d": through},
        ).mappings()
    ]


def _compatible(db: Session, item: str | None, uom: str | None) -> bool:
    """A quantity in another unit than the item's own has no measure unless a conversion is on file (R6 boundary)."""
    if not item:
        return False
    base = db.execute(text("select base_uom from item where code = :c"), {"c": item}).scalar()
    if not uom or not base or uom == base:
        return True
    return bool(
        db.execute(
            text(
                "select 1 from uom_conversion where item_code = :i and "
                "((from_uom = :u and to_uom = :b) or (from_uom = :b and to_uom = :u))"
            ),
            {"i": item, "u": uom, "b": base},
        ).first()
    )


def record(db: Session, through_date: date, actor: Actor | None = None, *, commit: bool = True) -> dict[str, Any]:
    """Every measure that can be worked out up to the date; a second run adds nothing (R6).

    `commit=False` leaves the rows and their audit in the caller's transaction (the month close, R7.3)."""
    actor = actor or audit.SYSTEM
    mode = settings.value(db, "ppv_recognition", "invoice")
    added = {name: 0 for name in LABELS}
    skipped: list[str] = []
    # NEEDS block 9: a receipt reversal or a counted return reverses that receipt's PPV with a negative row (R6.1).
    # Block 9 calls this service after its own commit; its reversal/return rows are read here at merge.
    for r in _receipt_lines(db, through_date):
        if mode not in ("receipt", "both"):
            continue
        item, qty = r["item_code"], Decimal(r["qty"])
        if not _compatible(db, item, r["uom"]):
            skipped.append(f"no standard for {item}: quantity in {r['uom']}")
            continue
        standard = standard_prices.in_force(db, item, r["on_date"])
        if standard is None:
            skipped.append(f"no standard for {item}")
            continue
        actual = Decimal(r["unit_price"])
        added["ppv"] += _keep(
            db, measure="ppv", part="receipt", source_type="gr_line", source_ref=str(r["gr_no"]),
            source_line=int(r["po_line_no"]), item_code=item, supplier_code=r["supplier_code"], po_no=r["po_no"],
            po_line_no=int(r["po_line_no"]), on_date=r["on_date"], period=_period(r["on_date"]), qty=qty,
            unit_price=actual, reference_price=standard, reference=f"standard v{_version(db, item, r['on_date'])}",
            amount=rules.ppv(qty, actual, standard), pct=None,
            basis={"recognition": "receipt", "standard_version": _version(db, item, r["on_date"])},
        )  # fmt: skip
    for r in _invoice_lines(db, through_date):
        qty = Decimal(r["qty"])
        unit = _unit(qty, Decimal(r["taxable"]))
        if unit is None:
            continue
        on = r["on_date"]
        item = r["item_code"]
        if r["type"] != "invoice" and item:
            # a credit or debit note reverses its share with a negative row (R6.1)
            standard = standard_prices.in_force(db, item, on)
            if standard is not None and mode in ("invoice", "both"):
                sign = -1 if r["type"] == "credit_note" else 1
                amount = sign * rules.ppv(abs(qty), unit, standard)
                added["ppv"] += _keep(
                    db, measure="ppv", part="invoice", source_type="invoice_line", source_ref=str(r["no"]),
                    source_line=int(r["line_no"]), item_code=item, supplier_code=r["supplier_code"],
                    po_no=r["po_no"], po_line_no=r["po_line_no"], on_date=on, period=_period(on),
                    qty=qty * sign, unit_price=unit, reference_price=standard,
                    reference=f"standard v{_version(db, item, on)}", amount=amount, pct=None,
                    basis={"recognition": "invoice", "reversal": r["type"]},
                )  # fmt: skip
            continue
        if not item:
            continue
        if mode in ("invoice", "both"):
            standard = standard_prices.in_force(db, item, on)
            if standard is None:
                skipped.append(f"no standard for {item}")
            elif not _compatible(db, item, r["uom"]):
                skipped.append(f"no standard for {item}: quantity in {r['uom']}")
            else:
                amount = (
                    rules.ppv(qty, unit, standard)
                    if mode == "invoice"
                    else rules.true_up(qty, unit, Decimal(r["po_price"]))
                )
                part = "invoice" if mode == "invoice" else "true_up"
                added["ppv"] += _keep(
                    db, measure="ppv", part=part, source_type="invoice_line", source_ref=str(r["no"]),
                    source_line=int(r["line_no"]), item_code=item, supplier_code=r["supplier_code"],
                    po_no=r["po_no"], po_line_no=r["po_line_no"], on_date=on, period=_period(on), qty=qty,
                    unit_price=unit, reference_price=standard if mode == "invoice" else Decimal(r["po_price"]),
                    reference=f"standard v{_version(db, item, on)}", amount=amount, pct=None,
                    basis={"recognition": mode, "standard_version": _version(db, item, on)},
                )  # fmt: skip
        if r["contract_price"] is not None and r["contract_code"]:
            contract = Decimal(r["contract_price"])
            added["contract_variance"] += _keep(
                db, measure="contract_variance", part="invoice", source_type="invoice_line",
                source_ref=str(r["no"]), source_line=int(r["line_no"]), item_code=item,
                supplier_code=r["supplier_code"], po_no=r["po_no"], po_line_no=r["po_line_no"], on_date=on,
                period=_period(on), qty=qty, unit_price=unit, reference_price=contract,
                reference=f"contract {r['contract_code']} price record {r['price_record_id']}",
                amount=rules.contract_variance(qty, unit, contract), pct=None,
                basis={"price_record_id": r["price_record_id"], "contract": r["contract_code"]},
            )  # fmt: skip
        if r["source"] == "award" and r["est_price"] is not None:
            baseline = Decimal(r["est_price"])
            added["saving_realised"] += _keep(
                db, measure="saving_realised", part="invoice", source_type="invoice_line",
                source_ref=str(r["no"]), source_line=int(r["line_no"]), item_code=item,
                supplier_code=r["supplier_code"], po_no=r["po_no"], po_line_no=r["po_line_no"], on_date=on,
                period=_period(on), qty=qty, unit_price=unit, reference_price=baseline,
                reference=f"estimate of {r['request_no']}/{r['request_line_no']}",
                amount=rules.saving(qty, unit, baseline), pct=None, basis={"baseline": "request estimate"},
            )  # fmt: skip
    _potential(db, through_date, added)
    audit.record(
        db, actor, "price_measure.run", "price_measure", str(through_date),
        after={"through": through_date.isoformat(), "added": added, "no_standard": skipped[:50],
               "recognition": mode},
    )  # fmt: skip
    if commit:
        db.commit()
    return {"through": through_date.isoformat(), "added": added, "no_standard": skipped}


def _potential(db: Session, through: date, added: dict[str, int]) -> None:
    """Saving, potential at each final or approved award, against the estimate it was compared with (DF-29)."""
    for r in db.execute(
        text(
            "select a.rfq_no, a.rfq_line_no, a.supplier_code, a.qty, a.price, a.awarded_on, "
            "f.request_no, f.request_line_no, f.item_code, rl.est_price "
            "from award a join rfq_line f on f.rfq_no = a.rfq_no and f.line_no = a.rfq_line_no "
            "left join request_line rl on rl.request_no = f.request_no and rl.line_no = f.request_line_no "
            "where a.status in ('final', 'approved') and coalesce(a.awarded_on, current_date) <= :d order by a.id"
        ),
        {"d": through},
    ).mappings():
        if r["est_price"] is None:
            continue
        qty, price, baseline = Decimal(r["qty"]), Decimal(r["price"]), Decimal(r["est_price"])
        on = r["awarded_on"] or through
        added["saving_potential"] += _keep(
            db, measure="saving_potential", part="award", source_type="award",
            source_ref=f"{r['rfq_no']}/{r['rfq_line_no']}/{r['supplier_code']}", source_line=0,
            item_code=r["item_code"], supplier_code=r["supplier_code"], po_no=None, po_line_no=None,
            on_date=on, period=_period(on), qty=qty, unit_price=price, reference_price=baseline,
            reference=f"estimate of {r['request_no']}/{r['request_line_no']}",
            amount=rules.saving(qty, price, baseline), pct=None,
            basis={"baseline": "request estimate", "rfq": r["rfq_no"]},
        )  # fmt: skip


def market_gap(db: Session, period_end: date, actor: Actor | None = None, *, commit: bool = True) -> dict[str, int]:
    """At the month's end, one evidence row per active contract price with a counting benchmark (PV3, OP-16)."""
    actor = actor or audit.SYSTEM
    added = 0
    for r in db.execute(
        text(
            "select pr.id, pr.supplier_code, pr.item_code, pr.contract_code, pr.price, pr.uom, pr.valid_from, "
            "pr.valid_to, min(b.price) as market, count(*) as sources from price_record pr "
            "join market_benchmark b on b.item_code = pr.item_code and b.status = 'verified' "
            "and b.effective_date <= :d and (coalesce(pr.region_code, '') = '' or b.region_code = pr.region_code) "
            "where pr.contract_code <> '' and pr.status = 'active' and pr.valid_from <= :d "
            "and (pr.valid_to is null or pr.valid_to >= :d) group by pr.id order by pr.id"
        ),
        {"d": period_end},
    ).mappings():
        contract, market = Decimal(r["price"]), Decimal(r["market"])
        if contract <= 0 or market <= 0:
            continue
        gap = (contract - market).quantize(Decimal("0.0001"))
        if _keep(
            db,
            measure="market_gap",
            part="month_end",
            source_type="price_record",
            source_ref=str(r["id"]),
            source_line=0,
            item_code=r["item_code"],
            supplier_code=r["supplier_code"],
            po_no=None,
            po_line_no=None,
            on_date=period_end,
            period=_period(period_end),
            qty=Decimal(0),
            unit_price=contract,
            reference_price=market,
            reference=f"lowest of {r['sources']} benchmarks",
            amount=Decimal("0.00"),
            pct=gap_pct(contract, market),
            basis={"contract": r["contract_code"], "gap_per_unit": str(gap), "sources": r["sources"]},
        ):
            added += 1
    audit.record(db, actor, "price_measure.run", "price_measure", f"market_gap {period_end}",
                 after={"measure": "market_gap", "period": _period(period_end), "added": added})  # fmt: skip
    if commit:
        db.commit()
    return {"market_gap": added}


def measures(db: Session, *, measure: str | None = None, on_from: date | None = None, on_to: date | None = None,
             item: str | None = None, supplier: str | None = None) -> dict[str, Any]:  # fmt: skip
    """Each measure in its own list with its own total; never one sum (R6.3)."""
    out: dict[str, Any] = {}
    for name, label in LABELS.items():
        if measure and measure != name:
            continue
        rows = [
            dict(r)
            for r in db.execute(
                text(
                    "select measure, part, source_type, source_ref, source_line, item_code, supplier_code, po_no, "
                    "po_line_no, on_date, period, qty, unit_price, reference_price, reference, amount, pct, basis "
                    "from price_measure where measure = :m "
                    "and (cast(:f as date) is null or on_date >= :f) and (cast(:t as date) is null or on_date <= :t) "
                    "and (cast(:i as text) is null or item_code = :i) "
                    "and (cast(:s as text) is null or supplier_code = :s) order by on_date, id"
                ),
                {"m": name, "f": on_from, "t": on_to, "i": item, "s": supplier},
            ).mappings()
        ]
        out[name] = {
            "label": label,
            "rows": rows,
            "total": str(sum((Decimal(r["amount"]) for r in rows), Decimal("0.00"))),
        }
    return out


def kpis(db: Session) -> dict[str, Any]:
    """The control tower's savings: potential and realised from the rows, and how many awards (DF-29)."""
    potential = Decimal(
        db.execute(
            text("select coalesce(sum(amount), 0) from price_measure where measure = 'saving_potential'")
        ).scalar_one()
    ).quantize(Decimal("0.01"))
    realised = Decimal(
        db.execute(
            text("select coalesce(sum(amount), 0) from price_measure where measure = 'saving_realised'")
        ).scalar_one()
    ).quantize(Decimal("0.01"))
    awards = int(
        db.execute(
            text("select count(distinct source_ref) from price_measure where measure = 'saving_potential'")
        ).scalar_one()
    )
    return {"potential": str(potential), "realised": str(realised), "awards": awards}


# ---- the daily job (06:45 IST) --------------------------------------------------------------------

JOB = "price_measures"
RUN_AT = time(6, 45)


def next_run(now: datetime) -> datetime:
    day = now.astimezone(IST).date()
    at = datetime.combine(day, RUN_AT, tzinfo=IST)
    if at <= now:
        at += timedelta(days=1)
    return at.astimezone(UTC)


def schedule(tenant: str, run_at: datetime | None = None) -> int | None:
    return worker.enqueue_unique(tenant, JOB, {}, run_at or datetime.now(UTC))


@worker.register(JOB)
def _daily(ctx: worker.JobContext) -> None:
    from sqlalchemy.orm import Session  # noqa: PLC0415 - this module is imported by the API too

    with Session(routing.engine_for(ctx.tenant)) as db:
        record(db, business_today())
        market_gap(db, _month_end(business_today()))
    worker.enqueue_unique(ctx.tenant, JOB, {}, next_run(ctx.now), exclude_id=ctx.id)


def _month_end(on: date) -> date:
    return (date(on.year + 1, 1, 1) if on.month == 12 else date(on.year, on.month + 1, 1)) - timedelta(days=1)
