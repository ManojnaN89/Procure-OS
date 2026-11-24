"""Payment runs: what is due, one live run per item, the approver the rule names, the code, and the bank file (2.2, 2.7).

A run is planned by Finance. The items are taken under a transaction lock, so two runs at once never take the same
invoice or advance. The approval rule for a `payment_run` of the run's total names the people who decide it, in order;
each of them needs the one-time code the setting asks for, and none of them may be the requester, the invoice's
approver, the service's confirmer or the advance's requester of an item they would pay (segregation of duties). An
item in that conflict is left out with the reason and a task. Every check runs again at each approval step and at the
file: a run may shrink, never grow.
"""

import csv
import hashlib
import io
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.approvals import rules as approval_rules
from p2p.approvals.workflow import name_of, org_code_of
from p2p.formulas import payment as rules
from p2p.formulas.calendar import business_today
from p2p.formulas.money import format_inr
from p2p.formulas.words import count
from p2p.invoices import payment
from p2p.invoices.service import InvoiceError
from p2p.platform import audit, numbering, otp, proposals, reference, settings, tasks
from p2p.platform.sod import Actor, user

D = Decimal
IST = ZoneInfo("Asia/Kolkata")
OPEN_RUN = ("proposed", "approved", "file_generated")
ADVANCE_OPEN = ("requested", "approved", "in_run", "paid", "recovering")
FILE_HEADER = ["payment_no", "beneficiary", "account_no", "ifsc", "amount", "mode", "reference"]
PAYER_WORDS = "segregation of duties: {who} approved this run"


def _has_column(db: Session, table: str, column: str) -> bool:
    return bool(
        db.execute(
            text("select 1 from information_schema.columns where table_name = :t and column_name = :c"),
            {"t": table, "c": column},
        ).first()
    )


def _bad_date_words(db: Session, day: date) -> str:
    holiday = db.execute(
        text("select name from ref_holiday where holiday_date = :d and kind = 'closed' order by scope limit 1"),
        {"d": day},
    ).scalar()
    return f"the bank is closed on {day:%d %b %Y}" + (f" ({holiday})" if holiday else "")


def check_run_date(db: Session, run_date: date) -> None:
    """A run is dated on a working day of the payment calendar, today or later (12.2.5)."""
    today = business_today()
    if run_date < today:
        raise InvoiceError("a run cannot be dated in the past")
    calendar = reference.calendar_for(db)
    if not calendar.is_working_day(run_date):
        raise InvoiceError(_bad_date_words(db, run_date))


# ---- what may be paid -------------------------------------------------------------------------


def _bank_ok(db: Session, supplier: str) -> tuple[bool, str | None]:
    verified = db.execute(
        text("select 1 from supplier_bank_account where supplier_code = :c and verified limit 1"), {"c": supplier}
    ).first()
    if not verified:
        return False, "bank account not verified"
    waiting = db.execute(
        text(
            "select 1 from proposal where action = 'supplier.set_bank' and status = 'pending' "
            "and payload->>'supplier' = :c limit 1"
        ),
        {"c": supplier},
    ).first()
    if waiting:
        return False, "bank change waiting for approval"
    return True, None


def invoice_problem(
    db: Session,
    invoice_no: str,
    run_date: date,
    window_days: int,
    *,
    ignore_window: bool = False,
    ignore_run: str | None = None,
) -> str | None:
    """Why an invoice may not be paid in a run on this date, or None when it may (12.2.2)."""
    inv = db.execute(text("select * from invoice where no = :n"), {"n": invoice_no}).mappings().first()
    if inv is None or inv["type"] != "invoice":
        return "not an invoice"
    if inv["status"] != "approved":
        return f"invoice {inv['status']}"
    if inv["payment_block"] != "none":
        return f"blocked: {inv['block_reason'] or inv['payment_block']}"
    if inv["booked_at"] is None:
        return "not booked"
    if inv["in_run"] is not None and inv["in_run"] != ignore_run:
        return f"in run {inv['in_run']}"
    due = inv["pay_by"] or inv["due_date"]
    if due is None:
        return "no due date"
    if not ignore_window and due > run_date + timedelta(days=window_days):
        return "not due yet"
    if db.execute(
        text("select 1 from tds_entry where invoice_no = :n and status = 'proposed' limit 1"), {"n": invoice_no}
    ).first():
        return "TDS to confirm"
    ok, why = _bank_ok(db, str(inv["supplier_code"]))
    if not ok:
        return why
    pending_advance = db.execute(
        text(
            "select r.po_no from advance_request r join invoice_line il on il.po_no = r.po_no "
            "where il.invoice_no = :n and r.status in ('approved', 'in_run') limit 1"
        ),
        {"n": invoice_no},
    ).scalar()
    if pending_advance:
        return f"advance on {pending_advance} not settled"
    return None


def advance_problem(db: Session, request_id: int, *, allow_in_run: bool = False) -> str | None:
    req = (
        db.execute(
            text(
                "select r.*, po.status as po_status from advance_request r join po on po.no = r.po_no where r.id = :i"
            ),
            {"i": request_id},
        )
        .mappings()
        .first()
    )
    if req is None:
        return "advance request does not exist"
    if req["status"] != "approved" and not (allow_in_run and req["status"] == "in_run"):
        return f"advance {req['status']}"
    if req["po_status"] == "cancelled":
        return "order cancelled"
    ok, why = _bank_ok(
        db, str(db.execute(text("select supplier_code from po where no = :p"), {"p": req["po_no"]}).scalar())
    )
    if not ok:
        return why
    return None


def due_invoices(db: Session, run_date: date, window_days: int | None = None) -> list[dict[str, Any]]:
    """Approved invoices a run on this date would pay: due by the run date plus the window (their pay-by day), or
    whose early-payment discount would be lost by waiting. Small suppliers first."""
    window = window_days if window_days is not None else int(settings.value(db, "payment_run_window_days", "7"))
    take = settings.value(db, "payment_take_discounts", "true") == "true"
    rows = db.execute(
        text(
            "select i.no, i.supplier_code, i.due_date, i.pay_by, i.total, "
            "(s.msme_category in ('micro', 'small')) as msme, coalesce(t.discount_pct, 0) as discount_pct, "
            "(select min(g.accepted_on + make_interval(days => :noag))::date from invoice_line il "
            " join gr_line gl on gl.po_no = il.po_no and gl.po_line_no = il.po_line_no "
            " join goods_receipt g on g.no = gl.gr_no where il.invoice_no = i.no) as msme_deadline "
            "from invoice i join supplier s on s.code = i.supplier_code "
            "left join payment_terms t on t.code = s.payment_terms_code "
            "where i.type = 'invoice' and i.status = 'approved' and i.payment_block = 'none' "
            "and i.booked_at is not null and i.in_run is null "
            "and coalesce(i.pay_by, i.due_date) <= :d + make_interval(days => :w + 120) "
            "order by (s.msme_category in ('micro', 'small')) desc, i.due_date, i.no"
        ),
        {"d": run_date, "w": window, "noag": int(settings.value(db, "msme_no_agreement_days", "15"))},
    ).mappings()
    out: list[dict[str, Any]] = []
    for r in rows:
        row = dict(r)
        due = row["pay_by"] or row["due_date"]
        if due <= run_date + timedelta(days=window):
            if invoice_problem(db, row["no"], run_date, window) is None:
                out.append(row)
            continue
        if take and D(row["discount_pct"]) > 0:
            p = payment.payable(db, row["no"], run_date)
            if p.discount > 0 and p.window_end is not None and p.window_end <= run_date + timedelta(days=window):
                if invoice_problem(db, row["no"], run_date, window, ignore_window=True) is None:
                    out.append(row)
    return out


def held_back(db: Session, run_date: date, window_days: int | None = None) -> list[dict[str, Any]]:
    """The invoices that are approved and near their date but held back, with the reason in plain words."""
    window = window_days if window_days is not None else int(settings.value(db, "payment_run_window_days", "7"))
    rows = db.execute(
        text(
            "select i.no, i.supplier_code, i.due_date, i.pay_by, i.total from invoice i "
            "join supplier s on s.code = i.supplier_code where i.type = 'invoice' "
            "and i.status not in ('cancelled', 'paid', 'received') "
            "and coalesce(i.pay_by, i.due_date) <= :d + make_interval(days => :w) "
            "order by i.due_date, i.no"
        ),
        {"d": run_date, "w": window},
    ).mappings()
    out = []
    for r in rows:
        why = invoice_problem(db, r["no"], run_date, window)
        if why and why not in ("not due yet", "not an invoice"):
            out.append({**dict(r), "reason": why})
    return out


def due_advances(db: Session, po_nos: list[str] | None = None) -> list[dict[str, Any]]:
    """Approved advance requests to pay in the next run (12.6.3). An advance is paid only once a person approved it."""
    rows = db.execute(
        text(
            "select r.id, r.po_no, r.pct, r.gross, r.taxable, po.supplier_code, po.total as order_total, "
            "po.taxable as order_taxable from advance_request r join po on po.no = r.po_no "
            "where r.status = 'approved' order by r.id"
        )
    ).mappings()
    out = []
    for r in rows:
        if po_nos and r["po_no"] not in po_nos:
            continue
        if advance_problem(db, int(r["id"])) is None:
            out.append(dict(r))
    return out


# ---- who pays ----------------------------------------------------------------------------------


def _item_people(db: Session, *, invoice_no: str | None = None, request_id: int | None = None) -> dict[str, set[str]]:
    """Who already has a duty on an item: its requests' requesters and on-behalf people, the service's confirmer, the
    invoice's approver, and the advance's requester."""
    out: dict[str, set[str]] = {"requester": set(), "service": set(), "invoice": set(), "advance": set()}
    if invoice_no:
        for r in db.execute(
            text(
                "select q.requester, q.on_behalf_of from invoice_line il "
                "join po_line pl on pl.po_no = il.po_no and pl.line_no = il.po_line_no "
                "left join request q on q.no = pl.request_no where il.invoice_no = :n"
            ),
            {"n": invoice_no},
        ):
            out["requester"].update(str(c) for c in r if c)
        out["service"].update(
            str(r[0])
            for r in db.execute(
                text(
                    "select distinct e.accepted_by from service_entry e join invoice_line il "
                    "on il.po_no = e.po_no and il.po_line_no = e.po_line_no "
                    "where il.invoice_no = :n and e.accepted_by is not null"
                ),
                {"n": invoice_no},
            )
        )
        # NEEDS block 10: invoice.approved_by (0170) is who released its block or approved the bill, stored as a user id
        if _has_column(db, "invoice", "approved_by"):
            code = db.execute(text("select approved_by from invoice where no = :n"), {"n": invoice_no}).scalar()
            if code:
                text_code = str(code)
                out["invoice"].add(org_code_of(db, int(text_code)) or text_code if text_code.isdigit() else text_code)
    if request_id is not None:
        code = db.execute(text("select requested_by from advance_request where id = :i"), {"i": request_id}).scalar()
        if code:
            out["advance"].add(str(code))
    return out


def _why_left_out(db: Session, people: dict[str, set[str]], payer: str) -> str | None:
    name = name_of(db, payer)
    if payer in people["requester"]:
        return f"{name} asked for it and approves this run"
    if payer in people["service"]:
        return f"{name} confirmed the service and approves this run"
    if payer in people["invoice"]:
        return f"{name} approved the invoice and approves this run"
    if payer in people["advance"]:
        return f"{name} asked for the advance and approves this run"
    return None


def _run_payers(db: Session, run_no: str) -> set[str]:
    return {
        str(r[0])
        for r in db.execute(
            text(
                "select approver from payment_run_approval where run_no = :r union "
                "select delegate from payment_run_approval where run_no = :r and delegate is not null"
            ),
            {"r": run_no},
        )
    }


# ---- planning a run ---------------------------------------------------------------------------


def _closed_words(total: Decimal) -> str:
    return f"no approval rule names who approves a payment run of {format_inr(total)}"


def _take(
    db: Session, run_no: str, due: list[dict[str, Any]], advances: list[dict[str, Any]]
) -> tuple[list[str], list[int]]:
    """Take what no other live run holds. A row that does not update is not taken (12.2.1)."""
    invoices: list[str] = []
    for row in due:
        taken = db.execute(
            text("update invoice set in_run = :r where no = :n and in_run is null returning no"),
            {"r": run_no, "n": row["no"]},
        ).first()
        if taken:
            invoices.append(str(row["no"]))
    taken_advances: list[int] = []
    for row in advances:
        got = db.execute(
            text("update advance_request set status = 'in_run' where id = :i and status = 'approved' returning id"),
            {"i": row["id"]},
        ).first()
        if got:
            taken_advances.append(int(row["id"]))
    return invoices, taken_advances


def _bank_account(db: Session, supplier: str) -> int | None:
    return db.execute(
        text(
            "select id from supplier_bank_account where supplier_code = :c and verified "
            "order by verified_on desc nulls last, id desc limit 1"
        ),
        {"c": supplier},
    ).scalar()


def _mode(net: Decimal, rtgs_from: Decimal) -> str:
    return "rtgs" if net > rtgs_from else "neft"


def _advance_preview(db: Session, request: dict[str, Any], run_date: date) -> Decimal:
    """The tax that will be held back from the advance when it is paid (no row is written yet, DF-17)."""
    from p2p.invoices import tds  # noqa: PLC0415 - tds builds on this module

    rows = tds.preview_advance(db, str(request["po_no"]), D(request["taxable"]), run_date)
    return sum((r.amount for r in rows), D(0))


def _insert_advance_payment(
    db: Session, request: dict[str, Any], run_no: str, run_date: date, rtgs_from: Decimal
) -> tuple[str, Decimal]:
    gross, taxable = D(request["gross"]), D(request["taxable"])
    hold = _advance_preview(db, request, run_date)
    net = gross - hold
    pay_no = numbering.next_number(db, "payment", run_date)
    db.execute(
        text(
            "insert into payment (no, run_no, type, supplier_code, po_no, amount, tds_amount, msme_interest, mode, "
            "reference, status, bank_account_id) values (:n, :r, 'advance', :s, :p, :a, :t, 0, :m, :p, 'planned', :b)"
        ),
        {
            "n": pay_no, "r": run_no, "s": request["supplier_code"], "p": request["po_no"], "a": net,
            "t": hold, "m": _mode(net, rtgs_from), "b": _bank_account(db, str(request["supplier_code"])),
        },
    )  # fmt: skip
    db.execute(
        text("insert into advance (payment_no, po_no, gross, taxable, request_id) values (:n, :p, :g, :t, :r)"),
        {"n": pay_no, "p": request["po_no"], "g": gross, "t": taxable, "r": request["id"]},
    )
    db.execute(
        text("update advance_request set payment_no = :p, status = 'in_run' where id = :i"),
        {"p": pay_no, "i": request["id"]},
    )
    return pay_no, net


def _insert_supplier_payment(
    db: Session, supplier: str, invoices: list[str], run_no: str, run_date: date, rtgs_from: Decimal
) -> tuple[str, Decimal] | None:
    """One payment for a supplier: its invoices, the notes against them (oldest first, never more than is due)."""
    cash = D(0)
    tds_total = D(0)
    interest = D(0)
    parts: list[dict[str, Any]] = []
    for no in invoices:
        p = payment.payable(db, no, run_date)
        line = {
            "no": no, "total": p.total, "tds": p.tds, "advance": p.advance, "discount": p.discount,
            "interest": p.interest, "cash": max(p.total - p.tds - p.advance - p.discount, D(0)),
        }  # fmt: skip
        parts.append(line)
        cash += max(p.total - p.tds - p.advance - p.discount, D(0))
        tds_total += p.tds
        interest += p.interest
    if not parts:
        return None
    notes = payment._approved_notes(db, supplier, set(invoices))  # noqa: SLF001 - one company, one rule for notes
    used, left = rules.offset(cash, notes)
    amount = left + interest
    pay_no = numbering.next_number(db, "payment", run_date)
    db.execute(
        text(
            "insert into payment (no, run_no, type, supplier_code, amount, tds_amount, msme_interest, mode, reference, "
            "status, bank_account_id) values (:n, :r, 'invoice', :s, :a, :t, :i, :m, :ref, 'planned', :b)"
        ),
        {
            "n": pay_no, "r": run_no, "s": supplier, "a": amount, "t": tds_total, "i": interest,
            "m": _mode(amount, rtgs_from), "ref": ", ".join(invoices)[:200], "b": _bank_account(db, supplier),
        },
    )  # fmt: skip
    for part in parts:
        db.execute(
            text("insert into payment_allocation (payment_no, invoice_no, amount, discount) values (:p, :i, :a, :d)"),
            {"p": pay_no, "i": part["no"], "a": part["total"] - part["tds"] - part["advance"], "d": part["discount"]},
        )
    for note_no, used_amount in used:
        db.execute(
            text("insert into payment_allocation (payment_no, invoice_no, amount) values (:p, :i, :a)"),
            {"p": pay_no, "i": note_no, "a": -used_amount},
        )
    return pay_no, amount


def propose_run(
    db: Session,
    run_date: date,
    user_id: int,
    *,
    window_days: int | None = None,
    include_advances: bool = True,
    advances_only: bool = False,
    po_nos: list[str] | None = None,
    tenant: str | None = None,
) -> dict[str, Any]:
    """Group what is due into a run and send it for approval by the people the rule names. Nothing is paid yet."""
    check_run_date(db, run_date)
    window = window_days if window_days is not None else int(settings.value(db, "payment_run_window_days", "7"))
    db.execute(text("select pg_advisory_xact_lock(hashtext('payment_run'))"))
    due = [] if advances_only else due_invoices(db, run_date, window)
    advances = due_advances(db, po_nos) if (include_advances or advances_only) else []
    if not due and not advances:
        raise InvoiceError("nothing is due: no approved invoice or advance falls in this window")
    run_no = numbering.next_number(db, "payment_run", run_date)
    account = db.execute(
        text(
            "select code from company_bank_account where purpose is null or purpose = 'payments' order by code limit 1"
        )
    ).scalar()
    db.execute(
        text("insert into payment_run (no, run_date, company_bank_account, proposed_by, status) values (:n, :d, :a, :u, 'proposed')"),
        {"n": run_no, "d": run_date, "a": account, "u": str(user_id)},
    )  # fmt: skip
    invoices, request_ids = _take(db, run_no, due, advances)
    by_id = {int(a["id"]): a for a in advances}
    rtgs_from = settings.number(db, "rtgs_from", "200000.00")
    total = D(0)
    advance_pay: dict[int, str] = {}
    for request_id in request_ids:
        pay_no, amount = _insert_advance_payment(db, by_id[request_id], run_no, run_date, rtgs_from)
        advance_pay[request_id] = pay_no
        total += amount
    by_supplier: dict[str, list[str]] = {}
    for row in due:
        if row["no"] in invoices:
            by_supplier.setdefault(str(row["supplier_code"]), []).append(str(row["no"]))
    for supplier, numbers in by_supplier.items():
        made = _insert_supplier_payment(db, supplier, numbers, run_no, run_date, rtgs_from)
        if made:
            total += made[1]
    proposer = org_code_of(db, user_id)
    try:
        rule = approval_rules.find_rule(db, "payment_run", total, run_date)
        steps = approval_rules.resolve_chain(
            db, rule.roles, requester=proposer, department=None, on_date=run_date, sources=rule.sources
        )
    except approval_rules.ApprovalRuleError as exc:
        raise InvoiceError(_closed_words(total) if "no approval rule" in str(exc) else str(exc)) from None
    left_out = _leave_out_conflicts(db, run_no, steps, user_id)
    total = D(
        db.execute(text("select coalesce(sum(amount), 0) from payment where run_no = :r"), {"r": run_no}).scalar_one()
    )
    if not db.execute(text("select 1 from payment where run_no = :r limit 1"), {"r": run_no}).first():
        raise InvoiceError("nothing is due: every item waits for someone else to approve it")
    for n, step in enumerate(steps, start=1):
        db.execute(
            text(
                "insert into payment_run_approval (run_no, step, role, approver, delegate) "
                "values (:r, :s, :role, :a, :d)"
            ),
            {"r": run_no, "s": n, "role": step.role, "a": step.approver, "d": step.delegate},
        )
    expires = datetime.combine(run_date + timedelta(days=1), time(0, 0), tzinfo=IST).astimezone(UTC)
    what = payment_what(db, run_no)
    saving = D(
        db.execute(
            text(
                "select coalesce(sum(a.discount), 0) from payment_allocation a join payment p on p.no = a.payment_no "
                "where p.run_no = :r"
            ),
            {"r": run_no},
        ).scalar_one()
    )
    pid = proposals.propose(
        db,
        user(user_id),
        action="payment_run.approve",
        summary=f"Pay {what}, {format_inr(total)} in all, on {run_date:%d %b %Y}"
        + (f", saving {format_inr(saving)} in early-payment discount" if saving else "")
        + (f". Left out: {len(left_out)}" if left_out else ""),
        payload={"run_no": run_no},
        required_permission="payment.approve",
        risk="high",
        document="payment_run",
        value=total,
        expires_at=expires,
    )
    db.execute(
        text("update payment_run set total = :t, proposal_id = :p, rule_code = :rc, rule_version = :rv where no = :n"),
        {"t": total, "p": pid, "rc": rule.code, "rv": rule.version, "n": run_no},
    )
    if left_out:
        _left_out_task(db, tenant, run_no, user_id, left_out)
    audit.record(
        db, user(user_id), "payment_run.propose", "payment_run", run_no,
        after={
            "total": total, "rule": f"{rule.code} v{rule.version}",
            "approvers": [s.approver for s in steps], "left_out": left_out,
            "invoices": len(invoices), "advances": len(request_ids),
        },
    )  # fmt: skip
    db.commit()
    return {"run": run_no, "status": "proposed", "total": total, "left_out": left_out}


def payment_what(db: Session, run_no: str) -> str:
    invoices = db.execute(
        text(
            "select count(distinct a.invoice_no) from payment_allocation a join payment p on p.no = a.payment_no "
            "join invoice i on i.no = a.invoice_no where p.run_no = :r and i.type = 'invoice' and a.amount > 0"
        ),
        {"r": run_no},
    ).scalar_one()
    suppliers = db.execute(
        text("select count(distinct supplier_code) from payment where run_no = :r"), {"r": run_no}
    ).scalar_one()
    advances = db.execute(
        text("select count(*) from payment where run_no = :r and type = 'advance'"), {"r": run_no}
    ).scalar_one()
    what = f"{count(int(invoices), 'invoice')} to {count(int(suppliers), 'supplier')}" if invoices else ""
    if advances:
        what = (what + " and " if what else "") + count(int(advances), "advance")
    return what or "nothing"


def _leave_out_conflicts(
    db: Session, run_no: str, steps: list[approval_rules.Step], user_id: int
) -> list[dict[str, str]]:
    """Items whose requester, confirmer, invoice approver or advance requester is one of this run's payers (12.1.4)."""
    payers = {s.approver for s in steps} | {s.delegate for s in steps if s.delegate}
    out: list[dict[str, str]] = []
    for p in db.execute(
        text("select no, type, po_no from payment where run_no = :r order by no"), {"r": run_no}
    ).mappings():
        people: dict[str, set[str]] = {"requester": set(), "service": set(), "invoice": set(), "advance": set()}
        if p["type"] == "advance":
            request_id = db.execute(
                text("select request_id from advance where payment_no = :p"), {"p": p["no"]}
            ).scalar()
            if request_id is not None:
                people = _item_people(db, request_id=int(request_id))
        else:
            invoice_nos = [
                str(r[0])
                for r in db.execute(
                    text(
                        "select a.invoice_no from payment_allocation a join invoice i on i.no = a.invoice_no "
                        "where a.payment_no = :p and a.amount > 0 and i.type = 'invoice'"
                    ),
                    {"p": p["no"]},
                )
            ]
            for no in invoice_nos:
                got = _item_people(db, invoice_no=no)
                for key in people:
                    people[key] |= got[key]
        why = next(
            (w for payer in sorted(payers) if (w := _why_left_out(db, people, payer))),
            None,
        )
        if why:
            item = str(p["po_no"] or p["no"])
            _remove_payment(db, run_no, str(p["no"]), why, user(user_id))
            out.append({"item": item, "reason": why})
    return out


def _left_out_task(db: Session, tenant: str | None, run_no: str, user_id: int, rows: list[dict[str, str]]) -> None:
    if not tenant:
        return
    tasks.create_task(
        db,
        tenant,
        kind="payment_left_out",
        title=f"{run_no}: {count(len(rows), 'item')} left out of the run",
        assignee=user_id,
        sla_hours=24,
        payload={"run": run_no, "items": rows},
        actor=user(user_id),
    )


# ---- ending a run -----------------------------------------------------------------------------


def _release_item(db: Session, run_no: str, pay_no: str) -> None:
    for invoice_no in db.execute(
        text("select invoice_no from payment_allocation where payment_no = :p"), {"p": pay_no}
    ).scalars():
        db.execute(
            text("update invoice set in_run = null where no = :n and in_run = :r"), {"n": invoice_no, "r": run_no}
        )


def _remove_payment(db: Session, run_no: str, pay_no: str, why: str, actor: Actor) -> list[str]:
    """Take one payment out of a run: its items are due again and the run keeps only what is left (12.2.4)."""
    pay = (
        db.execute(text("select type, po_no from payment where no = :p and run_no = :r"), {"p": pay_no, "r": run_no})
        .mappings()
        .first()
    )
    if pay is None:
        return []
    removed: list[str] = []
    for invoice_no in db.execute(
        text("select invoice_no from payment_allocation where payment_no = :p"), {"p": pay_no}
    ).scalars():
        removed.append(str(invoice_no))
    if pay["type"] == "advance":
        request_id = db.execute(text("select request_id from advance where payment_no = :p"), {"p": pay_no}).scalar()
        db.execute(text("delete from advance where payment_no = :p"), {"p": pay_no})
        db.execute(text("delete from tds_entry where payment_no = :p"), {"p": pay_no})
        db.execute(
            text("update advance_request set status = 'approved', payment_no = null where id = :i"), {"i": request_id}
        )
        removed = [str(pay["po_no"] or pay_no)]
    db.execute(text("delete from payment_allocation where payment_no = :p"), {"p": pay_no})
    db.execute(text("delete from payment where no = :p"), {"p": pay_no})
    _release_item(db, run_no, pay_no)
    for item in removed:
        audit.record(db, actor, "payment_run.item_removed", "payment_run", run_no, after={"item": item, "reason": why})
    _after_shrink(db, run_no, actor)
    return removed


def _after_shrink(db: Session, run_no: str, actor: Actor) -> None:
    left = D(
        db.execute(text("select coalesce(sum(amount), 0) from payment where run_no = :r"), {"r": run_no}).scalar_one()
    )
    run = (
        db.execute(text("select status, proposal_id, file_text from payment_run where no = :r"), {"r": run_no})
        .mappings()
        .one()
    )
    if not db.execute(text("select 1 from payment where run_no = :r limit 1"), {"r": run_no}).first():
        who = int(actor.id) if actor.kind == "user" and str(actor.id).isdigit() else None
        cancel(db, run_no, who, "nothing is left to pay", _system=True)
        return
    db.execute(
        text(
            "update payment_run set total = :t, status = case when status = 'file_generated' then 'approved' else status end, "
            "file_text = case when status = 'file_generated' then null else file_text end, "
            "file_hash = case when status = 'file_generated' then null else file_hash end where no = :r"
        ),
        {"t": left, "r": run_no},
    )
    if run["status"] == "proposed" and run["proposal_id"] is not None:
        db.execute(
            text("update proposal set summary = summary || :x where id = :p and status = 'pending'"),
            {"x": f". Changed: now {format_inr(left)}", "p": run["proposal_id"]},
        )


def cancel(db: Session, run_no: str, user_id: int | None, reason: str, *, _system: bool = False) -> None:
    """End a run before its file: its payments are cancelled, its invoices and advances are due again (12.2.3)."""
    run = db.execute(text("select * from payment_run where no = :r for update"), {"r": run_no}).mappings().first()
    if run is None:
        raise InvoiceError(f"{run_no} does not exist")
    if run["status"] not in ("proposed", "approved"):
        raise InvoiceError(f"{run_no} is {run['status']}; only a run before its file can be cancelled")
    pays = [str(r[0]) for r in db.execute(text("select no from payment where run_no = :r"), {"r": run_no})]
    for pay_no in pays:
        pay = db.execute(text("select type, po_no from payment where no = :p"), {"p": pay_no}).mappings().first()
        if pay is not None and pay["type"] == "advance":
            request_id = db.execute(
                text("select request_id from advance where payment_no = :p"), {"p": pay_no}
            ).scalar()
            db.execute(text("delete from advance where payment_no = :p"), {"p": pay_no})
            db.execute(text("delete from tds_entry where payment_no = :p"), {"p": pay_no})
            db.execute(
                text("update advance_request set status = 'approved', payment_no = null where id = :i"),
                {"i": request_id},
            )
        _release_item(db, run_no, pay_no)
        db.execute(text("delete from payment_allocation where payment_no = :p"), {"p": pay_no})
    db.execute(
        text("update payment set status = 'cancelled', cancel_reason = :w where run_no = :r"),
        {"w": reason, "r": run_no},
    )
    db.execute(
        text(
            "update payment_run set status = 'cancelled', cancelled_at = now(), cancel_reason = :w, total = 0, "
            "file_text = null, file_hash = null where no = :r"
        ),
        {"w": reason, "r": run_no},
    )
    if run["proposal_id"] is not None:
        db.execute(
            text(
                "update proposal set status = 'expired', decided_at = now(), decision_note = :w "
                "where id = :p and status = 'pending'"
            ),
            {"w": reason, "p": run["proposal_id"]},
        )
        audit.record(
            db,
            user(user_id) if user_id else audit.SYSTEM,
            "proposal.expired",
            "proposal",
            run["proposal_id"],
            reason=reason,
        )
    audit.record(
        db, user(user_id) if user_id else audit.SYSTEM, "payment_run.cancel", "payment_run", run_no,
        before={"status": run["status"]}, after={"status": "cancelled", "cancel_reason": reason}, reason=reason,
    )  # fmt: skip


def recheck(db: Session, run_no: str, actor: Actor) -> list[dict[str, str]]:
    """At every approval step and at the file: take out every item that is no longer payable (12.2.4)."""
    removed: list[dict[str, str]] = []
    run = db.execute(text("select run_date from payment_run where no = :r"), {"r": run_no}).mappings().first()
    if run is None:
        raise InvoiceError(f"{run_no} does not exist")
    window = int(settings.value(db, "payment_run_window_days", "7"))
    for p in list(
        db.execute(text("select no, type from payment where run_no = :r order by no"), {"r": run_no}).mappings()
    ):
        if p["type"] == "advance":
            request_id = db.execute(
                text("select request_id from advance where payment_no = :p"), {"p": p["no"]}
            ).scalar()
            why = (
                advance_problem(db, int(request_id), allow_in_run=True)
                if request_id is not None
                else "advance request gone"
            )
            if why:
                _remove_payment(db, run_no, str(p["no"]), why, actor)
                removed.append({"item": str(p["no"]), "reason": why})
            continue
        gone: list[tuple[str, str]] = []
        for invoice_no in [
            str(r[0])
            for r in db.execute(
                text(
                    "select a.invoice_no from payment_allocation a join invoice i on i.no = a.invoice_no "
                    "where a.payment_no = :p and a.amount > 0 and i.type = 'invoice'"
                ),
                {"p": p["no"]},
            )
        ]:
            why = invoice_problem(db, invoice_no, run["run_date"], window, ignore_window=True, ignore_run=run_no)
            if why:
                gone.append((invoice_no, why))
        if gone:
            for invoice_no, why in gone:
                db.execute(
                    text("delete from payment_allocation where payment_no = :p and invoice_no = :i"),
                    {"p": p["no"], "i": invoice_no},
                )
                db.execute(
                    text("update invoice set in_run = null where no = :n and in_run = :r"),
                    {"n": invoice_no, "r": run_no},
                )
                audit.record(
                    db, actor, "payment_run.item_removed", "payment_run", run_no,
                    after={"item": invoice_no, "reason": why},
                )  # fmt: skip
                removed.append({"item": invoice_no, "reason": why})
            _rebuild_payment(db, run_no, str(p["no"]), actor)
    _after_shrink(db, run_no, actor)
    return removed


def _rebuild_payment(db: Session, run_no: str, pay_no: str, actor: Actor) -> None:
    pay = db.execute(text("select * from payment where no = :p"), {"p": pay_no}).mappings().first()
    if pay is None:
        return
    invoices = [
        str(r[0])
        for r in db.execute(
            text(
                "select a.invoice_no from payment_allocation a join invoice i on i.no = a.invoice_no "
                "where a.payment_no = :p and a.amount > 0 and i.type = 'invoice' order by a.invoice_no"
            ),
            {"p": pay_no},
        )
    ]
    if not invoices:
        _remove_payment(db, run_no, pay_no, "nothing was left to pay", actor)
        return
    run = db.execute(text("select run_date from payment_run where no = :r"), {"r": run_no}).mappings().one()
    db.execute(text("delete from payment_allocation where payment_no = :p"), {"p": pay_no})
    cash = D(0)
    tds_total = D(0)
    interest = D(0)
    for no in invoices:
        p = payment.payable(db, no, run["run_date"])
        cash_part = max(p.total - p.tds - p.advance - p.discount, D(0))
        db.execute(
            text("insert into payment_allocation (payment_no, invoice_no, amount, discount) values (:p, :i, :a, :d)"),
            {"p": pay_no, "i": no, "a": p.total - p.tds - p.advance, "d": p.discount},
        )
        cash += cash_part
        tds_total += p.tds
        interest += p.interest
    notes = payment._approved_notes(db, str(pay["supplier_code"]), set(invoices))  # noqa: SLF001
    used, left = rules.offset(cash, notes)
    for note_no, amount in used:
        db.execute(
            text("insert into payment_allocation (payment_no, invoice_no, amount) values (:p, :i, :a)"),
            {"p": pay_no, "i": note_no, "a": -amount},
        )
    amount = left + interest
    if amount < 0:
        amount = D(0)
    db.execute(
        text(
            "update payment set amount = :a, tds_amount = :t, msme_interest = :i, mode = :m, reference = :r "
            "where no = :p"
        ),
        {
            "a": amount, "t": tds_total, "i": interest, "r": ", ".join(invoices)[:200],
            "m": _mode(amount, settings.number(db, "rtgs_from", "200000.00")), "p": pay_no,
        },
    )  # fmt: skip


def take_out_supplier(db: Session, supplier: str, actor: Actor, why: str) -> list[str]:
    """A supplier's bank account changed: its payments leave the runs they wait in, and a made file is voided."""
    runs = [
        str(r[0])
        for r in db.execute(
            text(
                "select distinct run_no from payment p join payment_run r on r.no = p.run_no "
                "where p.supplier_code = :s and r.status = any(:open) order by run_no"
            ),
            {"s": supplier, "open": list(OPEN_RUN)},
        )
    ]
    for run_no in runs:
        for pay_no in [
            str(r[0])
            for r in db.execute(
                text("select no from payment where run_no = :r and supplier_code = :s"), {"r": run_no, "s": supplier}
            )
        ]:
            _remove_payment(db, run_no, pay_no, why, actor)
    audit.record(db, actor, "payment_run.supplier_removed", "supplier", supplier, after={"runs": runs}, reason=why)
    return runs


def lapse(db: Session, today: date) -> list[str]:
    """The daily job: a run not filed on its date is cancelled (18:30 IST), with its items due again (OP-19)."""
    runs = [
        str(r[0])
        for r in db.execute(
            text("select no from payment_run where status in ('proposed', 'approved') and run_date <= :d order by no"),
            {"d": today},
        )
    ]
    for run_no in runs:
        cancel(db, run_no, None, "not filed on its date", _system=True)
        audit.record(
            db, audit.SYSTEM, "payment_run.lapse", "payment_run", run_no, after={"status": "cancelled"},
            reason="not filed on its date",
        )  # fmt: skip
    db.commit()
    return runs


# ---- approval, with the code ------------------------------------------------------------------


def _current_step(db: Session, run_no: str) -> dict[str, Any] | None:
    row = (
        db.execute(
            text("select * from payment_run_approval where run_no = :r and approved_by is null order by step limit 1"),
            {"r": run_no},
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


def _is_step_person(db: Session, step: dict[str, Any], user_id: int) -> bool:
    me = org_code_of(db, user_id)
    if me is None:
        return False
    return me in {step["approver"], step["delegate"]}


def _approver_name(db: Session, step: dict[str, Any], on: date) -> str:
    who = step["approver"]
    delegate = step["delegate"] or approval_rules.deputy_on(db, str(who), on)
    return name_of(db, str(delegate or who))


def send_code(db: Session, run_no: str, user_id: int) -> str:
    """Email the code for the current step to the person acting on it (12.1.3)."""
    run = db.execute(text("select * from payment_run where no = :r"), {"r": run_no}).mappings().first()
    if run is None:
        raise InvoiceError(f"{run_no} does not exist")
    if run["status"] != "proposed":
        raise InvoiceError(f"{run_no} is {run['status']}, not waiting for approval")
    step = _current_step(db, run_no)
    if step is None:
        raise InvoiceError(f"{run_no} has no step waiting for a decision")
    if not _is_step_person(db, step, user_id):
        raise InvoiceError(f"this run waits for {_approver_name(db, step, run['run_date'])}")
    ref = _code_ref(run_no, int(step["step"]), D(run["total"]))
    try:
        address = otp.OneTimeCodes("payment_run").issue(
            db, ref, user_id, subject=f"Your code to approve payment run {run_no}"
        )
    except otp.CodeError as exc:
        raise InvoiceError(str(exc)) from None
    masked = address[:2] + "***@" + address.split("@")[-1] if "@" in address else address
    return masked


def _code_ref(run_no: str, step: int, total: Decimal) -> str:
    return f"{run_no}:{step}:{int((total * 100).to_integral_value())}"


def approve_step(db: Session, run_no: str, user_id: int, code: str | None, note: str | None = None) -> dict[str, Any]:
    """The person the rule names for the current step approves it, with the code when the setting asks for one."""
    run = db.execute(text("select * from payment_run where no = :r for update"), {"r": run_no}).mappings().first()
    if run is None:
        raise InvoiceError(f"{run_no} does not exist")
    if run["status"] != "proposed":
        raise InvoiceError(f"{run_no} is {run['status']}, not waiting for approval")
    if run["run_date"] < business_today():
        raise InvoiceError("a run cannot be approved after its date; plan it again")
    step = _current_step(db, run_no)
    if step is None:
        raise InvoiceError(f"{run_no} has no step waiting for a decision")
    if not _is_step_person(db, step, user_id):
        raise InvoiceError(f"this run waits for {_approver_name(db, step, run['run_date'])}")
    recheck(db, run_no, user(user_id))
    run = db.execute(text("select * from payment_run where no = :r"), {"r": run_no}).mappings().one()
    if run["status"] == "cancelled":
        raise InvoiceError(f"{run_no} is cancelled: nothing was left to pay")
    _sod_at_approval(db, run_no)
    code_id = None
    if otp.needed(db, D(run["total"]), "payment_run_otp_above"):
        ref = _code_ref(run_no, int(step["step"]), D(run["total"]))
        try:
            otp.OneTimeCodes("payment_run").verify(db, ref, user_id, code)
        except otp.CodeError as exc:
            raise InvoiceError(str(exc)) from None
        code_id = db.execute(
            text(
                "select max(id) from one_time_code where purpose = 'payment_run' and ref = :r and user_id = :u "
                "and status = 'used'"
            ),
            {"r": ref, "u": user_id},
        ).scalar()
    db.execute(
        text(
            "update payment_run_approval set approved_by = :u, approved_at = now(), code_id = :c "
            "where run_no = :r and step = :s"
        ),
        {"u": org_code_of(db, user_id), "c": code_id, "r": run_no, "s": step["step"]},
    )
    audit.record(
        db, user(user_id), "payment_run.step_approved", "payment_run", run_no,
        after={"step": step["step"], "role": step["role"], "code": code_id is not None}, reason=note,
    )  # fmt: skip
    nxt = _current_step(db, run_no)
    if nxt is not None:
        db.commit()
        return {"status": "proposed", "step": step["step"], "next": nxt["approver"]}
    db.execute(
        text("update payment_run set status = 'approved', approved_by = :u, approved_at = now() where no = :r"),
        {"u": org_code_of(db, user_id), "r": run_no},
    )
    if run["proposal_id"] is not None:
        db.execute(
            text(
                "update proposal set status = 'approved', decided_by = :u, decided_at = now(), decision_note = :n "
                "where id = :p and status = 'pending'"
            ),
            {"u": user_id, "n": note, "p": run["proposal_id"]},
        )
        audit.record(db, user(user_id), "proposal.approved", "proposal", run["proposal_id"], reason=note)
    audit.record(
        db, user(user_id), "payment_run.approve", "payment_run", run_no,
        before={"status": "proposed"}, after={"status": "approved"}, reason=note,
    )  # fmt: skip
    db.commit()
    return {"status": "approved", "step": step["step"], "next": None}


def _sod_at_approval(db: Session, run_no: str) -> None:
    """The facts may have changed: no item may be paid by someone who asked for or approved it (12.1.4)."""
    payers = _run_payers(db, run_no)
    bad: list[str] = []
    for p in db.execute(text("select no, type, po_no from payment where run_no = :r"), {"r": run_no}).mappings():
        people: dict[str, set[str]] = {"requester": set(), "service": set(), "invoice": set(), "advance": set()}
        if p["type"] == "advance":
            request_id = db.execute(
                text("select request_id from advance where payment_no = :p"), {"p": p["no"]}
            ).scalar()
            if request_id is not None:
                people = _item_people(db, request_id=int(request_id))
        else:
            for row in db.execute(
                text(
                    "select a.invoice_no from payment_allocation a join invoice i on i.no = a.invoice_no "
                    "where a.payment_no = :p and a.amount > 0 and i.type = 'invoice'"
                ),
                {"p": p["no"]},
            ):
                got = _item_people(db, invoice_no=str(row[0]))
                for key in people:
                    people[key] |= got[key]
        why = next((w for payer in sorted(payers) if (w := _why_left_out(db, people, payer))), None)
        if why:
            bad.append(f"{p['po_no'] or p['no']}: {why}")
    if bad:
        raise InvoiceError(
            "segregation of duties: " + "; ".join(bad[:5]) + (f" and {len(bad) - 5} more" if len(bad) > 5 else "")
        )


def reject(db: Session, run_no: str, user_id: int, reason: str) -> None:
    """A step person rejects the run, with a reason: the run is cancelled and its items are due again."""
    if not reason.strip():
        raise InvoiceError("a reason is required to reject a run")
    run = db.execute(text("select * from payment_run where no = :r for update"), {"r": run_no}).mappings().first()
    if run is None:
        raise InvoiceError(f"{run_no} does not exist")
    if run["status"] != "proposed":
        raise InvoiceError(f"{run_no} is {run['status']}, not waiting for approval")
    step = _current_step(db, run_no)
    if step is None or not _is_step_person(db, step, user_id):
        who = _approver_name(db, step, run["run_date"]) if step else "nobody"
        raise InvoiceError(f"this run waits for {who}")
    cancel(db, run_no, user_id, reason)
    if run["proposal_id"] is not None:
        db.execute(
            text(
                "update proposal set status = 'rejected', decided_by = :u, decided_at = now(), decision_note = :w "
                "where id = :p"
            ),
            {"u": user_id, "w": reason, "p": run["proposal_id"]},
        )
        audit.record(db, user(user_id), "proposal.rejected", "proposal", run["proposal_id"], reason=reason)
    audit.record(db, user(user_id), "payment_run.reject", "payment_run", run_no, reason=reason)
    db.commit()


# ---- the bank file ---------------------------------------------------------------------------


def generate_file(db: Session, run_no: str, user_id: int) -> str:
    """The bank instruction for an approved run, after every item is checked again: exactly what is left."""
    run = db.execute(text("select * from payment_run where no = :n for update"), {"n": run_no}).mappings().first()
    if run is None:
        raise InvoiceError(f"{run_no} does not exist")
    if run["status"] != "approved":
        raise InvoiceError(f"{run_no} is {run['status']}; a file is made only for an approved run")
    if business_today() > run["run_date"]:
        raise InvoiceError("a run cannot be filed after its date")
    recheck(db, run_no, user(user_id))
    run = db.execute(text("select * from payment_run where no = :n"), {"n": run_no}).mappings().one()
    if run["status"] == "cancelled":
        raise InvoiceError(f"{run_no} is cancelled: nothing was left to pay")
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(FILE_HEADER)
    zero: list[str] = []
    for p in db.execute(text("select * from payment where run_no = :n order by no"), {"n": run_no}).mappings():
        if D(p["amount"]) <= 0:
            zero.append(str(p["no"]))
            continue
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
        text("update payment_run set status = 'file_generated', file_text = :f, file_hash = :h where no = :n"),
        {"f": text_, "h": hashlib.sha256(text_.encode()).hexdigest(), "n": run_no},
    )
    db.execute(text("update payment set status = 'in_file' where run_no = :n and amount > 0"), {"n": run_no})
    from p2p.invoices import settlement  # noqa: PLC0415 - settlement builds on this module

    for pay_no in zero:
        settlement.settle_zero(db, pay_no, user(user_id))
    audit.record(
        db, user(user_id), "payment_run.file", "payment_run", run_no,
        after={"lines": text_.count("\n") - 1, "hash": hashlib.sha256(text_.encode()).hexdigest()[:12]},
    )  # fmt: skip
    db.commit()
    return text_


def run_page(db: Session, run_no: str) -> dict[str, Any]:
    """The run's steps and whether the caller may decide the current one."""
    run = db.execute(text("select * from payment_run where no = :n"), {"n": run_no}).mappings().first()
    if run is None:
        raise InvoiceError(f"{run_no} does not exist")
    steps = [
        {
            "step": r["step"], "role": r["role"], "approver": r["approver"], "delegate": r["delegate"],
            "name": name_of(db, str(r["delegate"] or r["approver"])), "approved_at": r["approved_at"],
        }
        for r in db.execute(
            text("select * from payment_run_approval where run_no = :n order by step"), {"n": run_no}
        ).mappings()
    ]  # fmt: skip
    return {"steps": steps, "current": _current_step(db, run_no)}


# NEEDS block 9: platform/proposals.closer(action) is block 9's hook, run when a proposal ends rejected or expired.
# Block 12 registers the payment run here so that a proposal ended outside the run page still cancels its run.
_closer = getattr(proposals, "closer", None)


if _closer is not None:  # pragma: no cover - the hook arrives with block 9

    @_closer("payment_run.approve")  # type: ignore[untyped-decorator]
    def _end_run(db: Session, proposal_id: int, payload: dict[str, Any], actor: Actor) -> None:
        run_no = str(payload.get("run_no"))
        row = db.execute(text("select status from payment_run where no = :r"), {"r": run_no}).first()
        if row is not None and row[0] in ("proposed", "approved"):
            cancel(db, run_no, None, f"the proposal ended: {actor.kind}", _system=True)
            db.commit()
