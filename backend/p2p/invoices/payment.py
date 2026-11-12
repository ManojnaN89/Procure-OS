"""From an approved invoice to money paid: booking (due date and tax at source), advances, payment runs, the bank file,
and closing the case.

An invoice is booked when it becomes approved for payment (matched, or released by a person): its due date follows the
payment terms and the small-supplier rule, what is left of an advance paid on its order is taken off, and the tax to hold
back is worked out. A payment run groups the advances that are due and the invoices that are due (small suppliers first),
nets credit notes, advances and tax, takes the early-payment discount where it is earned, and goes to the owner for
approval (rule 5: never automatic). The approved run produces a bank file; when the bank has paid, the invoices are
marked paid, the cases they belong to are closed, and the people who asked for the goods are told.
"""

import csv
import io
import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.formulas import payment as rules
from p2p.invoices.service import InvoiceError
from p2p.platform import audit, emails, notifications, numbering, proposals
from p2p.platform.sod import Actor, user

log = logging.getLogger(__name__)
D = Decimal
RTGS_FROM = D("200000")  # a payment above this goes by RTGS, below it by NEFT
OPEN_RUN = ("proposed", "approved", "file_generated")
ADVANCE_STATUSES = ("acknowledged", "part_received")  # an order is advanced once the supplier has confirmed it


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
    """Tax at source on goods (194Q) applies to goods. Services have other sections, which are not built yet."""
    sql = (
        "select count(*) from invoice_line il join po_line pl on pl.po_no = il.po_no and pl.line_no = il.po_line_no "
        "join item i on i.code = pl.item_code where il.invoice_no = :k and i.kind = 'service'"
        if lines_of == "invoice"
        else "select count(*) from po_line pl join item i on i.code = pl.item_code where pl.po_no = :k and i.kind = 'service'"
    )
    return int(db.execute(text(sql), {"k": key}).scalar_one()) == 0


def tds_rule(db: Session, on: date) -> rules.TdsRule:
    """Section 194Q as the tax table has it on a date (the latest row that has started and not ended). The rate, the
    threshold and which Act's wording is used are data, so a change in the law is a new row, and an old decision can be
    worked out again against the row it used. Without a row the statutory figures in formulas/payment.py apply."""
    row = db.execute(
        text(
            "select act, rate_pct, rate_no_pan_pct, threshold_annual from ref_tds_section where section = '194Q' "
            "and effective_from <= :d and (effective_to is null or effective_to >= :d) "
            "and rate_pct is not null and rate_no_pan_pct is not null and threshold_annual is not null "
            "order by effective_from desc, id desc limit 1"
        ),
        {"d": on},
    ).first()
    if row is None:
        return rules.default_tds_rule(on)
    label = rules.SECTION_LABEL["194Q"] if str(row[0]) == "2025" else "194Q"
    return rules.TdsRule(label, D(row[1]), D(row[2]), D(row[3]))


def _cumulative(db: Session, supplier: str, year: date, *, excluding: str | None = None) -> Decimal:
    """What has been bought from the supplier in the financial year on which tax at source was worked out: the taxable
    value of its booked invoices (less what advances already counted) and of its advances."""
    a, b = _fy_start(year), _fy_end(year)
    invoices = db.execute(
        text(
            "select coalesce(sum(i.taxable - i.advance_taxable_used), 0) from invoice i join tds_entry t on t.invoice_no = i.no "
            "where i.supplier_code = :s and i.no <> coalesce(:n, '') and i.status <> 'cancelled' "
            "and t.booked_on >= :a and t.booked_on <= :b"
        ),
        {"s": supplier, "n": excluding, "a": a, "b": b},
    ).scalar_one()
    advances = db.execute(
        text(
            "select coalesce(sum(v.taxable), 0) from advance v join payment p on p.no = v.payment_no "
            "join tds_entry t on t.payment_no = p.no where p.supplier_code = :s and t.booked_on >= :a and t.booked_on <= :b"
        ),
        {"s": supplier, "a": a, "b": b},
    ).scalar_one()
    return D(invoices) + D(advances)


def _use_advances(db: Session, inv: dict[str, Any]) -> tuple[Decimal, Decimal]:
    """Take what is left of the advances paid on the invoice's orders (before the invoice arrived) off the invoice."""
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
                    "select * from advance where po_no = :p and paid_on is not null and paid_on <= :d "
                    "and gross > used_gross order by paid_on, payment_no for update"
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
    """Work out the due date, take off any advance, and work out the tax to hold back, once, when an invoice is approved."""
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
    tds_amount = D(0)
    if _setting(db, "tds_applicable_on_goods", "true") == "true" and _goods_only(db, "invoice", no):
        tds = rules.tds_on_goods(
            _cumulative(db, inv["supplier_code"], inv["received_on"], excluding=no),
            D(inv["taxable"]) - used_t,
            inv["received_on"],
            pan_inoperative=sup["pan_status"] == "inoperative",
            rule=tds_rule(db, inv["received_on"]),
        )
        db.execute(
            text(
                "insert into tds_entry (invoice_no, section, base, rate, amount, booked_on, quarter) "
                "values (:i, :s, :b, :r, :a, :d, :q)"
            ),
            {
                "i": no, "s": tds.section, "b": tds.base, "r": tds.rate, "a": tds.amount, "d": inv["received_on"],
                "q": rules.quarter_label(inv["received_on"]),
            },
        )  # fmt: skip
        tds_amount = tds.amount
    db.execute(
        text(
            "update invoice set due_date = :d, tds_amount = :t, advance_used = :g, advance_taxable_used = :x, "
            "booked_at = now() where no = :n"
        ),
        {"d": due, "t": tds_amount, "g": used_g, "x": used_t, "n": no},
    )


def _fy_start(d: date) -> date:
    return date(d.year if d.month >= 4 else d.year - 1, 4, 1)


def _fy_end(d: date) -> date:
    return date(_fy_start(d).year + 1, 3, 31)


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

    @property
    def net(self) -> Decimal:
        return max(self.total - self.tds - self.credit - self.advance - self.discount, D(0))


def _bank_rates(db: Session) -> list[tuple[date, Decimal]]:
    return [(r[0], D(r[1])) for r in db.execute(text("select effective_from, rate_pct from ref_rbi_bank_rate"))]


def payable(db: Session, no: str, on: date) -> Payable:
    """What is paid for an invoice on a date: its total, less the tax held back, credit notes against it, the advance
    already paid on its order and the early-payment discount if it is earned; plus interest if it is paid to a micro or
    small supplier after the due date (the interest is paid on top, as its own amount)."""
    inv = db.execute(text("select * from invoice where no = :n"), {"n": no}).mappings().one()
    credits = db.execute(
        text(
            "select c.no, c.total from invoice c where c.type = 'credit_note' and c.ref_invoice_no = :n and c.status = 'approved' "
            "and not exists (select 1 from payment_allocation a where a.invoice_no = c.no) order by c.no"
        ),
        {"n": no},
    ).all()
    sup = db.execute(
        text("select msme_category, payment_terms_code from supplier where code = :c"), {"c": inv["supplier_code"]}
    ).first()
    interest = D(0)
    rates = _bank_rates(db)
    if sup and sup[0] in rules.MSME_CATEGORIES and inv["due_date"] and rates:
        interest = rules.msme_interest(D(inv["total"]), inv["due_date"], on, rates)
    discount, window_end = D(0), None
    terms = _terms(db, sup[1] if sup else None)
    if terms and terms["discount_pct"] and _setting(db, "payment_take_discounts", "true") == "true":
        baseline = inv["invoice_date"] if terms["baseline"] == "invoice_date" else accepted_on(db, dict(inv))
        days = int(terms["discount_days"] or 0)
        pct = D(terms["discount_pct"])
        discount = rules.early_discount(D(inv["taxable"]) - D(inv["advance_taxable_used"]), pct, days, baseline, on)
        window_end = rules.discount_window_end(baseline, days) if pct > 0 and days > 0 else None
    return Payable(
        no, D(inv["total"]), D(inv["tds_amount"]), sum((D(c[1]) for c in credits), D(0)), interest, inv["due_date"],
        [c[0] for c in credits], D(inv["advance_used"]), discount, window_end,
    )  # fmt: skip


# ---- payment runs -----------------------------------------------------------------------------


def due_invoices(db: Session, run_date: date, window_days: int | None = None) -> list[dict[str, Any]]:
    """Approved invoices to pay in a run: those due up to the run date plus the window, and those whose early-payment
    discount would be lost if they waited for the next run. Not already in a run; small suppliers first."""
    window = window_days if window_days is not None else int(_setting(db, "payment_run_window_days", "7"))
    take = _setting(db, "payment_take_discounts", "true") == "true"
    rows = (
        db.execute(
            text(
                "select i.no, i.supplier_code, i.due_date, i.total, (s.msme_category in ('micro', 'small')) as msme, "
                "coalesce(t.discount_pct, 0) as discount_pct "
                "from invoice i join supplier s on s.code = i.supplier_code "
                "left join payment_terms t on t.code = s.payment_terms_code "
                "where i.type = 'invoice' and i.status = 'approved' and i.payment_block = 'none' and i.due_date is not null "
                "and i.due_date <= :d + make_interval(days => :w + 120) and not exists ("
                "  select 1 from payment_allocation a join payment p on p.no = a.payment_no join payment_run r on r.no = p.run_no "
                "  where a.invoice_no = i.no and r.status = any(:open)) "
                "order by (s.msme_category in ('micro', 'small')) desc, i.due_date, i.no"
            ),
            {"d": run_date, "w": window, "open": list(OPEN_RUN)},
        )
        .mappings()
        .all()
    )
    out: list[dict[str, Any]] = []
    for r in rows:
        row = dict(r)
        row["discount"] = D(0)
        if row["due_date"] <= run_date + timedelta(days=window):
            out.append(row)
        elif take and D(row["discount_pct"]) > 0:
            p = payable(db, row["no"], run_date)
            if p.discount > 0 and p.window_end is not None and p.window_end <= run_date + timedelta(days=window):
                row["discount"] = p.discount  # paid in this run it earns the discount; in the next it would not
                out.append(row)
    return out


def due_advances(db: Session, po_nos: list[str] | None = None) -> list[dict[str, Any]]:
    """Orders whose payment terms ask for an advance that has not been proposed yet. An order is advanced once the supplier
    has acknowledged it; naming the orders makes any sent order eligible."""
    rows = db.execute(
        text(
            "select po.no, po.supplier_code, po.total, po.taxable, t.advance_pct from po "
            "join payment_terms t on t.code = po.payment_terms_code "
            "where t.advance_pct > 0 and not exists (select 1 from advance a where a.po_no = po.no) "
            "and (case when cast(:p as text[]) is null then po.status = any(:st) "
            "else po.no = any(:p) and po.status not in ('draft', 'submitted', 'approved', 'cancelled') end) "
            "order by po.no"
        ),
        {"p": po_nos, "st": list(ADVANCE_STATUSES)},
    ).mappings()
    return [dict(r) for r in rows]


def _advance_payment(db: Session, adv: dict[str, Any], run_no: str, run_date: date) -> Decimal:
    """One payment of type advance for an order, with the tax at source held back from it. Returns what is paid."""
    sup = db.execute(text("select pan_status from supplier where code = :c"), {"c": adv["supplier_code"]}).first()
    gross, taxable = rules.advance_amounts(D(adv["total"]), D(adv["taxable"]), D(adv["advance_pct"]))
    tds_amount = D(0)
    tds: rules.Tds | None = None
    pay_no = numbering.next_number(db, "payment", run_date)
    if _setting(db, "tds_applicable_on_goods", "true") == "true" and _goods_only(db, "po", adv["no"]):
        tds = rules.tds_on_goods(
            _cumulative(db, adv["supplier_code"], run_date),
            taxable,
            run_date,
            pan_inoperative=bool(sup and sup[0] == "inoperative"),
            rule=tds_rule(db, run_date),
        )
        tds_amount = tds.amount
    net = gross - tds_amount
    db.execute(
        text(
            "insert into payment (no, run_no, type, supplier_code, po_no, amount, tds_amount, msme_interest, mode, reference) "
            "values (:n, :r, 'advance', :s, :p, :a, :t, 0, :m, :p)"
        ),
        {"n": pay_no, "r": run_no, "s": adv["supplier_code"], "p": adv["no"], "a": net, "t": tds_amount, "m": "rtgs" if net > RTGS_FROM else "neft"},
    )  # fmt: skip
    db.execute(
        text("insert into advance (payment_no, po_no, gross, taxable) values (:n, :p, :g, :t)"),
        {"n": pay_no, "p": adv["no"], "g": gross, "t": taxable},
    )
    if tds is not None:
        db.execute(
            text(
                "insert into tds_entry (payment_no, section, base, rate, amount, booked_on, quarter) "
                "values (:p, :s, :b, :r, :a, :d, :q)"
            ),
            {
                "p": pay_no, "s": tds.section, "b": tds.base, "r": tds.rate, "a": tds.amount, "d": run_date,
                "q": rules.quarter_label(run_date),
            },
        )  # fmt: skip
    return net


def propose_run(
    db: Session,
    run_date: date,
    user_id: int,
    *,
    window_days: int | None = None,
    include_advances: bool = True,
    advances_only: bool = False,
    po_nos: list[str] | None = None,
) -> str:
    """Group what is due into a payment run and send it for approval. Nothing is paid until the owner approves."""
    due = [] if advances_only else due_invoices(db, run_date, window_days)
    advances = due_advances(db, po_nos) if (include_advances or advances_only) else []
    if not due and not advances:
        raise InvoiceError("no approved invoice is due in this window")
    by_supplier: dict[str, list[Payable]] = {}
    for row in due:
        by_supplier.setdefault(row["supplier_code"], []).append(payable(db, row["no"], run_date))
    run_no = numbering.next_number(db, "payment_run", run_date)
    account = db.execute(
        text(
            "select code from company_bank_account where purpose is null or purpose = 'payments' order by code limit 1"
        )
    ).scalar()
    db.execute(
        text(
            "insert into payment_run (no, run_date, company_bank_account, proposed_by, status) values (:n, :d, :a, :u, 'proposed')"
        ),
        {"n": run_no, "d": run_date, "a": account, "u": str(user_id)},
    )
    total = D(0)
    for adv in advances:  # advances first: the supplier is waiting for them before it ships
        total += _advance_payment(db, adv, run_no, run_date)
    saving = D(0)
    for supplier, items in by_supplier.items():
        net = sum((p.net for p in items), D(0))
        interest = sum((p.interest for p in items), D(0))
        pay_no = numbering.next_number(db, "payment", run_date)
        db.execute(
            text(
                "insert into payment (no, run_no, type, supplier_code, amount, tds_amount, msme_interest, mode, reference) "
                "values (:n, :r, 'invoice', :s, :a, :t, :i, :m, :ref)"
            ),
            {
                "n": pay_no, "r": run_no, "s": supplier, "a": net + interest,
                "t": sum((p.tds for p in items), D(0)), "i": interest,
                "m": "rtgs" if net > RTGS_FROM else "neft", "ref": ", ".join(p.invoice_no for p in items)[:200],
            },
        )  # fmt: skip
        for p in items:
            db.execute(
                text(
                    "insert into payment_allocation (payment_no, invoice_no, amount, discount) values (:p, :i, :a, :d)"
                ),
                {"p": pay_no, "i": p.invoice_no, "a": p.total - p.tds - p.advance, "d": p.discount},
            )
            saving += p.discount
            for credit_no in p.credit_notes:
                credit = D(db.execute(text("select total from invoice where no = :n"), {"n": credit_no}).scalar_one())
                db.execute(
                    text("insert into payment_allocation (payment_no, invoice_no, amount) values (:p, :i, :a)"),
                    {"p": pay_no, "i": credit_no, "a": -credit},
                )
        total += net + interest
    what = f"{len(due)} invoice(s) to {len(by_supplier)} supplier(s)" if due else ""
    if advances:
        what = (what + " and " if what else "") + f"{len(advances)} advance(s)"
    pid = proposals.propose(
        db,
        user(user_id),
        action="payment_run.approve",
        summary=f"Pay {what}, {total:,.2f} in all, on {run_date:%d %b %Y}"
        + (f", saving {saving:,.2f} in early-payment discount" if saving else ""),
        payload={"run_no": run_no},
        required_permission="payment.approve",
        risk="high",
    )
    db.execute(
        text("update payment_run set total = :t, proposal_id = :p where no = :n"), {"t": total, "p": pid, "n": run_no}
    )
    audit.record(
        db,
        user(user_id),
        "payment_run.propose",
        "payment_run",
        run_no,
        after={"invoices": len(due), "advances": len(advances), "total": total, "discount": saving},
    )
    db.commit()
    return run_no


@proposals.executor("payment_run.approve")
def _approve_run(db: Session, payload: dict[str, Any], actor: Actor) -> dict[str, Any]:
    no = str(payload["run_no"])
    run = db.execute(text("select status, proposed_by from payment_run where no = :n for update"), {"n": no}).first()
    if run is None or run[0] != "proposed":
        raise InvoiceError(f"{no} is not waiting for approval")
    if run[1] == actor.id:
        raise InvoiceError("segregation of duties: the person who proposed the run cannot approve it")
    db.execute(
        text("update payment_run set status = 'approved', approved_by = :u, approved_at = now() where no = :n"),
        {"u": actor.id, "n": no},
    )
    audit.record(
        db, actor, "payment_run.approve", "payment_run", no, before={"status": "proposed"}, after={"status": "approved"}
    )
    db.commit()
    return {"run": no, "status": "approved"}


# ---- the bank file, the payment, the closing --------------------------------------------------

FILE_HEADER = ["payment_no", "beneficiary", "account_no", "ifsc", "amount", "mode", "reference"]


def generate_file(db: Session, no: str, user_id: int) -> str:
    """The bank instruction for an approved run: one line per payment, exactly the approved amounts."""
    run = db.execute(text("select * from payment_run where no = :n for update"), {"n": no}).mappings().first()
    if run is None:
        raise InvoiceError(f"{no} does not exist")
    if run["status"] != "approved":
        raise InvoiceError(f"{no} is {run['status']}; a file is made only for an approved run")
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(FILE_HEADER)
    for p in db.execute(text("select * from payment where run_no = :n order by no"), {"n": no}).mappings():
        if D(p["amount"]) <= 0:
            continue  # an invoice fully covered by an advance settles on paper: there is nothing to send the bank
        bank = db.execute(
            text(
                "select b.account_no, b.ifsc, coalesce(s.trade_name, s.legal_name) from supplier_bank_account b "
                "join supplier s on s.code = b.supplier_code where b.supplier_code = :c and coalesce(b.verified, false) "
                "order by b.verified_on desc nulls last limit 1"
            ),
            {"c": p["supplier_code"]},
        ).first()
        if bank is None:
            raise InvoiceError(f"{p['supplier_code']} has no verified bank account, so it cannot be paid")
        writer.writerow([p["no"], bank[2], bank[0], bank[1], p["amount"], p["mode"].upper(), p["reference"]])
    text_ = out.getvalue()
    db.execute(
        text("update payment_run set status = 'file_generated', file_text = :f where no = :n"), {"f": text_, "n": no}
    )
    audit.record(db, user(user_id), "payment_run.file", "payment_run", no, after={"lines": text_.count("\n") - 1})
    db.commit()
    return text_


def mark_paid(
    db: Session,
    no: str,
    user_id: int,
    *,
    paid_on: date,
    utrs: dict[str, str] | None = None,
    tenant: str | None = None,
) -> list[str]:
    """The bank has paid the run: record the bank references, settle the invoices, close what is finished, and tell the
    people who asked for the goods (when the company's slug is given, so the notification can go out)."""
    run = db.execute(text("select * from payment_run where no = :n for update"), {"n": no}).mappings().first()
    if run is None:
        raise InvoiceError(f"{no} does not exist")
    if run["status"] != "file_generated":
        raise InvoiceError(f"{no} is {run['status']}; only a run whose bank file was made can be marked paid")
    payments = [
        dict(r) for r in db.execute(text("select * from payment where run_no = :n order by no"), {"n": no}).mappings()
    ]
    closed: list[str] = []
    for n, p in enumerate(payments, start=1):
        utr = (utrs or {}).get(p["no"]) or f"UTR{paid_on:%y%m%d}{no[-4:]}{n:04d}"
        db.execute(
            text("update payment set utr = :u, paid_on = :d where no = :p"), {"u": utr, "d": paid_on, "p": p["no"]}
        )
        if p["type"] == "advance":
            db.execute(text("update advance set paid_on = :d where payment_no = :p"), {"d": paid_on, "p": p["no"]})
            db.execute(  # the tax at source on an advance is booked on the day it is paid
                text("update tds_entry set booked_on = :d, quarter = :q where payment_no = :p"),
                {"d": paid_on, "q": rules.quarter_label(paid_on), "p": p["no"]},
            )
        for a in db.execute(
            text("select invoice_no, discount from payment_allocation where payment_no = :p"), {"p": p["no"]}
        ).all():
            db.execute(
                text(
                    "update invoice set status = 'paid', paid_on = :d, discount_taken = :x where no = :i and status = 'approved'"
                ),
                {"d": paid_on, "i": a[0], "x": a[1]},
            )
            _budget_actual(db, a[0])
            closed += close_case(db, a[0], user_id)
        p["utr"] = utr
    db.execute(text("update payment_run set status = 'paid' where no = :n"), {"n": no})
    audit.record(
        db, user(user_id), "payment_run.paid", "payment_run", no, after={"paid_on": paid_on, "payments": len(payments)}
    )
    db.commit()  # the payment stands whatever happens to the emails
    for p in payments:
        _remit(db, p, paid_on, p["utr"])
    for po_no in dict.fromkeys(closed):
        _notify_closed(db, tenant, po_no)
    return closed


def _budget_actual(db: Session, invoice_no: str) -> None:
    """What was spent, by cost centre and GL account, so the budget can show actuals."""
    inv = db.execute(
        text("select received_on, type, discount_taken from invoice where no = :n"), {"n": invoice_no}
    ).first()
    if inv is None or inv[1] != "invoice":
        return
    spent: list[tuple[Any, Any, Decimal]] = [
        (r[0], r[1], D(r[2]))
        for r in db.execute(
            text(
                "select rl.cost_centre_code as cc, coalesce(rl.gl_code, pl.gl_code) as gl_code, sum(il.taxable) as amount "
                "from invoice_line il join po_line pl on pl.po_no = il.po_no and pl.line_no = il.po_line_no "
                "left join request_line rl on rl.request_no = pl.request_no and rl.line_no = pl.request_line_no "
                "where il.invoice_no = :n group by 1, 2"
            ),
            {"n": invoice_no},
        ).all()
    ]
    charges = D(
        db.execute(
            text("select coalesce(sum(amount), 0) from invoice_charge where invoice_no = :n"), {"n": invoice_no}
        ).scalar_one()
    )
    if spent and (charges or inv[2]):  # freight and packing are spent too; a discount earned is spent less
        spent[0] = (spent[0][0], spent[0][1], spent[0][2] + charges - D(inv[2]))
    for cc, gl, amount in spent:
        db.execute(
            text(
                "insert into budget_actual (invoice_no, cost_centre_code, gl_code, period, amount) values (:i, :c, :g, :p, :a)"
            ),
            {"i": invoice_no, "c": cc, "g": gl, "p": inv[0].strftime("%Y-%m"), "a": amount},
        )


def close_case(db: Session, invoice_no: str, user_id: int) -> list[str]:
    """When an order line has been received, invoiced and paid in full its line is closed, and the order and request with it."""
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
    """The login of a person in the organisation, found by the email on their record."""
    return notifications.login_of(db, tenant, org_code)


def _notify_closed(db: Session, tenant: str | None, po_no: str) -> None:
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


def _remit(db: Session, payment: dict[str, Any], paid_on: date, utr: str) -> None:
    """Tell the supplier what was paid and for which invoices."""
    contact = db.execute(
        text(
            "select email, name from supplier_contact where supplier_code = :c and email is not null "
            "order by is_primary desc nulls last, id limit 1"
        ),
        {"c": payment["supplier_code"]},
    ).first()
    if contact is None:
        return
    rows = db.execute(
        text(
            "select i.supplier_invoice_no, i.type, a.amount, a.discount from payment_allocation a "
            "join invoice i on i.no = a.invoice_no where a.payment_no = :p order by i.no"
        ),
        {"p": payment["no"]},
    ).all()
    company = db.execute(text("select coalesce(short_name, legal_name) from company limit 1")).scalar() or "Our company"
    if payment["type"] == "advance":
        lines = f"  Advance against order {payment['po_no']}"
    else:
        lines = "\n".join(
            f"  {r[0]} ({r[1].replace('_', ' ')}): Rs {r[2]:,.2f}"
            + (f", less early-payment discount Rs {r[3]:,.2f}" if D(r[3]) else "")
            for r in rows
        )
    body = (
        f"Dear {contact[1] or 'Sir or Madam'},\n\n{company} has paid Rs {payment['amount']:,.2f} on {paid_on:%d %b %Y}.\n"
        f"Bank reference: {utr}\n\nThis covers:\n{lines}\n"
        + (f"\nTax deducted at source: Rs {payment['tds_amount']:,.2f}\n" if D(payment["tds_amount"]) else "")
        + f"\nRegards,\nAccounts, {company}\n"
    )
    try:
        emails.send_linked_email(
            db, contact[0], f"Payment advice {payment['no']}", body, linked_type="invoice", linked_ref=payment["no"]
        )
        db.execute(
            text("update payment set remittance_sent_at = :n where no = :p"),
            {"n": datetime.now(UTC), "p": payment["no"]},
        )
        db.commit()
    except OSError:
        db.rollback()  # the payment stands; the advice can be sent again
