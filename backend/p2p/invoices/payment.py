"""Booking an approved invoice and what is payable on it (2.1, 12.5, 12.8).

An invoice is booked when it becomes approved for payment: its due date follows the payment terms and the small-supplier
rule (DF-30), the working day a run must pay it by comes from the payment calendar, what is left of a settled advance on
its order is taken off, and the tax to hold back is proposed per section (invoices/tds.py). What is paid is the total less
the tax confirmed, the notes against it, the advance and the early-payment discount, plus interest to a small supplier
when it is paid late.

The run, settlement, advance and note services live beside this module (invoices/runs.py, settlement.py, advances.py,
notes.py).
"""

import logging
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.formulas import payment as rules
from p2p.platform import audit, notifications, reference
from p2p.platform.sod import user

log = logging.getLogger(__name__)
D = Decimal


def _setting(db: Session, key: str, default: str) -> str:
    for table in ("org_setting", "app_setting"):
        value = db.execute(text(f"select value from {table} where key = :k"), {"k": key}).scalar()  # noqa: S608
        if value not in (None, ""):
            return str(value)
    return default


def msme_limits(db: Session) -> rules.MsmeLimits:
    """The MSMED Act day limits as the company has them (settings), else the statute's figures."""
    default = rules.MsmeLimits()
    return rules.MsmeLimits(
        int(_setting(db, "msme_max_days", str(default.max_days))),
        int(_setting(db, "msme_no_agreement_days", str(default.no_agreement_days))),
    )


def _terms(db: Session, code: str | None) -> dict[str, Any] | None:
    row = (
        db.execute(
            text("select days, baseline, advance_pct, discount_pct, discount_days from payment_terms where code = :c"),
            {"c": code},
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


# ---- booking ----------------------------------------------------------------------------------


def accepted_on(db: Session, invoice: dict[str, Any]) -> date:
    """The day the goods were accepted (or the service confirmed): the latest acceptance on the order lines before the
    invoice arrived."""
    day = db.execute(
        text(
            "select max(d) from (select g.accepted_on as d, l.po_no, l.po_line_no from goods_receipt g "
            "join gr_line l on l.gr_no = g.no union all select accepted_on, po_no, po_line_no from service_entry) a "
            "join invoice_line i on i.po_no = a.po_no and i.po_line_no = a.po_line_no "
            "where i.invoice_no = :n and a.d is not null and a.d <= :d"
        ),
        {"n": invoice["no"], "d": invoice["received_on"]},
    ).scalar()
    return day or invoice["received_on"]  # type: ignore[no-any-return]


def _goods_only(db: Session, lines_of: str, key: str) -> bool:
    """Whether every line is goods (no service). Kept for block 10's bill path, which block 12 replaces with TDS per
    line (12.5.1); it no longer decides the tax this module books.

    # NEEDS block 10: remove this call from the bill path when its TDS is worked out per line by invoices/tds.py
    """
    sql = (
        "select count(*) from invoice_line il join po_line pl on pl.po_no = il.po_no and pl.line_no = il.po_line_no "
        "join item i on i.code = pl.item_code where il.invoice_no = :k and i.kind = 'service'"
        if lines_of == "invoice"
        else "select count(*) from po_line pl join item i on i.code = pl.item_code where pl.po_no = :k and i.kind = 'service'"
    )
    return int(db.execute(text(sql), {"k": key}).scalar_one()) == 0


def _use_advances(db: Session, inv: dict[str, Any]) -> tuple[Decimal, Decimal]:
    """Take what is left of the advances **settled** on the invoice's orders (before the invoice arrived) off it."""
    used_g = used_t = D(0)
    pos = [
        r[0]
        for r in db.execute(
            text("select distinct po_no from invoice_line where invoice_no = :n order by 1"), {"n": inv["no"]}
        )
    ]
    for po_no in pos:
        for adv in (
            db.execute(
                text(
                    "select a.* from advance a join payment p on p.no = a.payment_no where a.po_no = :p "
                    "and p.status = 'settled' and a.settled_on is not null and a.settled_on <= :d "
                    "and a.gross > a.used_gross order by a.settled_on, a.payment_no for update"
                ),
                {"p": po_no, "d": inv["received_on"]},
            )
            .mappings()
            .all()
        ):
            g, t = rules.advance_use(
                D(adv["gross"]) - D(adv["used_gross"]),
                D(adv["taxable"]) - D(adv["used_taxable"]),
                D(inv["total"]) - used_g,
                D(inv["taxable"]) - used_t,
            )
            if g <= 0:
                continue
            db.execute(
                text(
                    "update advance set used_gross = used_gross + :g, used_taxable = used_taxable + :t where payment_no = :p"
                ),
                {"g": g, "t": t, "p": adv["payment_no"]},
            )
            db.execute(
                text(
                    "insert into advance_adjustment (advance_payment_no, invoice_no, amount, taxable) values (:p, :i, :g, :t) "
                    "on conflict (advance_payment_no, invoice_no) do update set amount = advance_adjustment.amount + :g, "
                    "taxable = advance_adjustment.taxable + :t"
                ),
                {"p": adv["payment_no"], "i": inv["no"], "g": g, "t": t},
            )
            used_g += g
            used_t += t
    return used_g, used_t


def book_invoice(db: Session, no: str) -> None:
    """Work out the due date and pay-by day, take off any settled advance, and propose the tax to hold back, once."""
    inv = db.execute(text("select * from invoice where no = :n for update"), {"n": no}).mappings().one()
    if inv["type"] != "invoice" or inv["booked_at"] is not None:
        return
    sup = db.execute(text("select * from supplier where code = :c"), {"c": inv["supplier_code"]}).mappings().one()
    terms = _terms(db, sup["payment_terms_code"])
    due = rules.due_date(
        baseline=terms["baseline"] if terms else "invoice_date",
        days=int(terms["days"]) if terms else 30,
        invoice_date=inv["invoice_date"],
        accepted_on=accepted_on(db, dict(inv)),
        msme_category=sup["msme_category"],
        msme_agreement=bool(sup["msme_agreement"]),
        agreed_days=sup["agreed_payment_days"],
        limits=msme_limits(db),
    )
    used_g, used_t = _use_advances(db, dict(inv))
    db.execute(
        text(
            "update invoice set due_date = :d, pay_by = :p, advance_used = :g, advance_taxable_used = :x, "
            "booked_at = now() where no = :n"
        ),
        {
            "d": due,
            "p": rules.pay_by(due, reference.calendar_for(db)),
            "g": used_g,
            "x": used_t,
            "n": no,
        },
    )
    from p2p.invoices import tds  # noqa: PLC0415 - tds builds on this module

    tds.propose(db, no)


# ---- what is payable --------------------------------------------------------------------------


@dataclass
class Payable:
    invoice_no: str
    total: Decimal
    tds: Decimal
    credit: Decimal
    interest: Decimal
    due: date | None
    credit_notes: list[str] = field(default_factory=list)
    advance: Decimal = D(0)  # what was already paid in advance on the order
    discount: Decimal = D(0)  # the cash discount for paying early
    window_end: date | None = None  # the last day the discount is earned
    msme: bool = False

    @property
    def net(self) -> Decimal:
        return max(self.total - self.tds - self.credit - self.advance - self.discount, D(0))


def bank_rates(db: Session) -> list[tuple[date, Decimal]]:
    return [(r[0], D(r[1])) for r in db.execute(text("select effective_from, rate_pct from ref_rbi_bank_rate"))]


def _approved_notes(db: Session, supplier: str, linked_to: set[str]) -> list[tuple[str, Decimal]]:
    """The supplier's approved notes still to use: those linked to an invoice in the payment first, then the rest,
    oldest first. Each with what is left of it."""
    rows = db.execute(
        text(
            "select no, total - offset_used as left, case when ref_invoice_no = any(:linked) then 0 else 1 end as later, "
            "coalesce(ref_invoice_no, ''), no from invoice where supplier_code = :s and type in ('credit_note', 'debit_note') "
            "and status = 'approved' and total - offset_used > 0 order by later, invoice_date, no"
        ),
        {"s": supplier, "linked": list(linked_to) or [""]},
    ).all()
    return [(str(r[0]), D(r[1])) for r in rows]


def payable(db: Session, no: str, on: date) -> Payable:
    """What is paid for an invoice on a date: its total less the confirmed tax, the notes used against it, the advance
    already settled on its order and the early-payment discount if it is earned; plus interest when a micro or small
    supplier is paid after the due date (paid on top, as its own amount)."""
    inv = db.execute(text("select * from invoice where no = :n"), {"n": no}).mappings().one()
    sup = (
        db.execute(
            text("select msme_category, payment_terms_code, pan_status from supplier where code = :c"),
            {"c": inv["supplier_code"]},
        )
        .mappings()
        .one()
    )
    interest = D(0)
    rates = bank_rates(db)
    if sup["msme_category"] in rules.MSME_CATEGORIES and inv["due_date"] and rates:
        interest = rules.msme_interest(D(inv["total"]), inv["due_date"], on, rates)
    discount, window_end = D(0), None
    terms = _terms(db, sup["payment_terms_code"])
    if terms and terms["discount_pct"] and _setting(db, "payment_take_discounts", "true") == "true":
        baseline = inv["invoice_date"] if terms["baseline"] == "invoice_date" else accepted_on(db, dict(inv))
        days = int(terms["discount_days"] or 0)
        pct = D(terms["discount_pct"])
        discount = rules.early_discount(D(inv["taxable"]) - D(inv["advance_taxable_used"]), pct, days, baseline, on)
        window_end = rules.discount_window_end(baseline, days) if pct > 0 and days > 0 else None
    # the notes an earlier run already used on this invoice are behind offset_used; only the rest can be used now
    notes = _approved_notes(db, inv["supplier_code"], {no})
    cash = max(D(inv["total"]) - D(inv["tds_amount"]) - D(inv["advance_used"]) - discount, D(0))
    used, left = rules.offset(cash, notes)
    credit = sum((amount for _, amount in used), D(0))
    return Payable(
        no, D(inv["total"]), D(inv["tds_amount"]), credit, interest, inv["due_date"],
        [ref for ref, _ in used], D(inv["advance_used"]), discount, window_end,
        sup["msme_category"] in rules.MSME_CATEGORIES,
    )  # fmt: skip


# ---- closing a paid case ----------------------------------------------------------------------


def close_case(db: Session, invoice_no: str, user_id: int) -> list[str]:
    """When an order line has been received, invoiced and paid in full its line is closed, and the order and request with it.

    # NEEDS block 13: orders/closure.check replaces this call (and this function) on settlement
    """
    closed: list[str] = []
    for po_no, line_no in db.execute(
        text("select distinct po_no, po_line_no from invoice_line where invoice_no = :n"), {"n": invoice_no}
    ).all():
        line = db.execute(
            text(
                "select qty, received_qty, invoiced_qty, status from po_line where po_no = :p and line_no = :l for update"
            ),
            {"p": po_no, "l": line_no},
        ).first()
        if line is None or line[3] in ("closed", "cancelled"):
            continue
        unpaid = db.execute(
            text(
                "select count(*) from invoice_line l join invoice i on i.no = l.invoice_no "
                "where l.po_no = :p and l.po_line_no = :l and i.type = 'invoice' and i.status in ('approved', 'blocked', 'received')"
            ),
            {"p": po_no, "l": line_no},
        ).scalar_one()
        if D(line[1]) >= D(line[0]) and D(line[2]) >= D(line[1]) and unpaid == 0:
            db.execute(
                text("update po_line set status = 'closed' where po_no = :p and line_no = :l"),
                {"p": po_no, "l": line_no},
            )
        else:
            continue
        open_lines = db.execute(
            text("select count(*) from po_line where po_no = :p and status not in ('closed', 'cancelled')"),
            {"p": po_no},
        ).scalar_one()
        if open_lines == 0:
            db.execute(text("update po set status = 'closed' where no = :p"), {"p": po_no})
            closed.append(po_no)
            audit.record(db, user(user_id), "po.close", "po", po_no, after={"status": "closed"})
    return closed


def _login_of(db: Session, tenant: str, org_code: str | None) -> int | None:
    return notifications.login_of(db, tenant, org_code)


def notify_closed(db: Session, tenant: str | None, po_no: str) -> None:
    """Tell the person who asked for the goods, and the buyer, that the order is paid and closed: once each per order."""
    if not tenant:
        return
    try:
        people = [
            r[0]
            for r in db.execute(
                text(
                    "select distinct r.requester from po_line l join request r on r.no = l.request_no "
                    "where l.po_no = :p and r.requester is not null"
                ),
                {"p": po_no},
            )
        ]
        buyer = db.execute(text("select buyer from po where no = :p"), {"p": po_no}).scalar()
        supplier = db.execute(
            text(
                "select coalesce(s.trade_name, s.legal_name) from po join supplier s on s.code = po.supplier_code where po.no = :p"
            ),
            {"p": po_no},
        ).scalar()
        told: set[int] = set()
        for code in [*people, buyer]:
            uid = _login_of(db, tenant, code)
            if uid is None or uid in told:
                continue
            told.add(uid)
            notifications.notify(
                db,
                tenant,
                uid,
                "order_paid",
                f"Order {po_no} is paid and closed",
                f"{supplier or 'The supplier'} has been paid for order {po_no}. Nothing more is expected on it.",
                link=f"#/purchase-orders/{po_no}",
                email=False,
            )
    except Exception:  # noqa: BLE001 - a notification must never undo or hide a payment
        db.rollback()
        log.warning("could not notify about the closing of %s", po_no, exc_info=True)
