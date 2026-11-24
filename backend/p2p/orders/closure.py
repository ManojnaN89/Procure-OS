"""Two-track closure (R2, D-12): an order closes only when supply is complete and the money is settled.

The service reads receipts, invoices, notes, advances, payments, returns, excesses and claims, and asks
`formulas/closure.py` whether anything is left. Every order closure in the product goes through `check`; a note, a
failed or reversed payment, or a counted return that names a closed order goes through `reopen` first.

Some of the rows this reads belong to blocks 9 and 12 (returns, excesses, claims, payment states). Until their
migrations are on the branch the reads are skipped, never guessed.

# NEEDS block 9: after a receipt reversal, a return dispatch or outcome, or an excess accept/return, block 9 calls
# `closure.check` (H9-1); a `short_closed` line refuses a `replace` outcome.
# NEEDS block 12: `invoices/settlement.py` calls `closure.check` on settlement and `closure.reopen` on a reversal, in
# place of `payment.close_case` (H12-5); a failed or reversed payment is read from `payment.status`.
"""

import logging
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.formulas import closure as rules
from p2p.formulas.calendar import IST
from p2p.platform import audit, routing, worker
from p2p.platform.sod import Actor

log = logging.getLogger(__name__)
JOB = "order_closure"
RUN_AT = time(6, 30)


def _has_table(db: Session, name: str) -> bool:
    return db.execute(text("select to_regclass(:n)"), {"n": f"public.{name}"}).scalar() is not None


def _has_column(db: Session, table: str, column: str) -> bool:
    return bool(
        db.execute(
            text(
                "select 1 from information_schema.columns where table_schema = 'public' "
                "and table_name = :t and column_name = :c"
            ),
            {"t": table, "c": column},
        ).first()
    )


def _order(db: Session, po_no: str) -> dict[str, Any]:
    row = db.execute(text("select * from po where no = :n"), {"n": po_no}).mappings().first()
    if row is None:
        raise ValueError(f"{po_no} does not exist")
    return dict(row)


def _line_states(db: Session, po_no: str) -> list[rules.LineState]:
    claims: dict[int, Decimal] = {}
    if _has_table(db, "recovery_claim"):
        try:
            claims = {
                int(r[0]): Decimal(r[1])
                for r in db.execute(
                    text(
                        "select po_line_no, coalesce(sum(qty), 0) from recovery_claim "
                        "where po_no = :p and status <> 'cancelled' group by po_line_no"
                    ),
                    {"p": po_no},
                )
            }
        except Exception:  # noqa: BLE001 - block 9's shape; its owner connects this at merge
            db.rollback()
    out = []
    for r in db.execute(
        text(
            "select line_no, qty, received_qty, invoiced_qty, coalesce(short_closed_qty, 0) as short_closed_qty, status "
            "from po_line where po_no = :p order by line_no"
        ),
        {"p": po_no},
    ).mappings():
        out.append(
            rules.LineState(
                int(r["line_no"]),
                Decimal(r["qty"]),
                Decimal(r["received_qty"]),
                Decimal(r["invoiced_qty"]),
                str(r["status"]),
                claims.get(int(r["line_no"]), Decimal(0)),
            )
        )
    return out


def _open_invoices(db: Session, po_no: str) -> list[rules.OpenInvoice]:
    rows = db.execute(
        text(
            "select distinct i.no, i.status from invoice i join invoice_line l on l.invoice_no = i.no "
            "where l.po_no = :p and i.type = 'invoice' and i.status not in ('paid', 'cancelled') order by i.no"
        ),
        {"p": po_no},
    ).all()
    return [rules.OpenInvoice(str(r[0]), str(r[1])) for r in rows]


def _failed_payments(db: Session, po_no: str) -> list[rules.FailedPayment]:
    # NEEDS block 12: payment.status and its failed/reversed values (Q18). A recorded payment is not settled either.
    if not _has_column(db, "payment", "status"):
        return []
    rows = db.execute(
        text(
            "select distinct p.no, coalesce(p.paid_on, r.run_date) from payment p "
            "join payment_run r on r.no = p.run_no "
            "join payment_allocation a on a.payment_no = p.no join invoice_line l on l.invoice_no = a.invoice_no "
            "where l.po_no = :p and p.status in ('failed', 'reversed') order by p.no"
        ),
        {"p": po_no},
    ).all()
    return [rules.FailedPayment(str(r[0]), r[1]) for r in rows if r[1] is not None]


def _open_advances(db: Session, po_no: str) -> list[rules.OpenAdvance]:
    rows = db.execute(
        text(
            "select payment_no, gross - coalesce(used_gross, 0) from advance "
            "where po_no = :p and paid_on is not null and gross - coalesce(used_gross, 0) > 0 order by payment_no"
        ),
        {"p": po_no},
    ).all()
    return [rules.OpenAdvance(str(r[0]), Decimal(r[1])) for r in rows]


def _open_notes(db: Session, po_no: str) -> list[rules.OpenNote]:
    rows = db.execute(
        text(
            "select distinct i.type, i.no from invoice i join invoice_line l on l.invoice_no = i.no "
            "where l.po_no = :p and i.type in ('credit_note', 'debit_note') "
            "and i.status not in ('paid', 'cancelled') order by i.no"
        ),
        {"p": po_no},
    ).all()
    return [rules.OpenNote(str(r[0]), str(r[1])) for r in rows]


def _open_returns(db: Session, po_no: str) -> list[rules.OpenReturn]:
    # NEEDS block 9: supplier_return is block 9's table. Its header carries no order; the order line is on
    # supplier_return_line (`return_no`, `po_no`, `po_line_no`, `outcome`).
    if not _has_table(db, "supplier_return"):
        return []
    rows = db.execute(
        text(
            "select distinct r.no from supplier_return r join supplier_return_line l on l.return_no = r.no "
            "where l.po_no = :p and (r.status = 'draft' or (r.status = 'dispatched' and l.outcome is null)) "
            "order by r.no"
        ),
        {"p": po_no},
    ).all()
    return [rules.OpenReturn(str(r[0])) for r in rows]


def _open_excesses(db: Session, po_no: str) -> list[rules.OpenExcess]:
    # NEEDS block 9: receipt_excess waiting, proposed or returning.
    if not _has_table(db, "receipt_excess"):
        return []
    rows = db.execute(
        text(
            "select qty, gr_no from receipt_excess where po_no = :p "
            "and status in ('waiting', 'proposed', 'returning') order by gr_no"
        ),
        {"p": po_no},
    ).all()
    return [rules.OpenExcess(Decimal(r[0]), str(r[1])) for r in rows]


def _open_claims(db: Session, po_no: str) -> list[rules.OpenClaim]:
    # NEEDS block 9: recovery_claim and its open rows are block 9's; block 12 adds the debit note and moves a claim to
    # `settled` (and a cancelled one stays cancelled), so only those two statuses are not in this blocker.
    if not _has_table(db, "recovery_claim"):
        return []
    rows = db.execute(
        text("select no from recovery_claim where po_no = :p and status = 'open' order by no"),
        {"p": po_no},
    ).all()
    return [rules.OpenClaim(str(r[0])) for r in rows]


def _waiting_short_close(db: Session, po_no: str) -> str | None:
    return db.execute(
        text("select approver from po_short_close where po_no = :p and status = 'waiting'"), {"p": po_no}
    ).scalar()


def _short_closes(db: Session, po_no: str) -> list[dict[str, Any]]:
    return [
        dict(r)
        for r in db.execute(
            text(
                "select id, lines, value, risk, flags, rule_code, rule_version, approver, reason, status, "
                "asked_by, asked_at, decided_by, decided_at, note from po_short_close where po_no = :p order by id"
            ),
            {"p": po_no},
        ).mappings()
    ]


def state(db: Session, po_no: str) -> dict[str, Any]:
    """Supply done, money settled and every blocker, in the words people read (R2.1)."""
    lines = _line_states(db, po_no)
    supply_done = rules.supply_done(lines)
    supplies = db.execute(
        text(
            "select max(g.accepted_on) from goods_receipt g join po_line l on l.po_no = g.po_no "
            "where g.po_no = :p and g.accepted_on is not null"
        ),
        {"p": po_no},
    ).scalar()
    if supply_done and supplies is None:
        supplies = db.execute(
            text("select max(accepted_on) from service_entry where po_no = :p and accepted_on is not null"),
            {"p": po_no},
        ).scalar()
    if supply_done and supplies is None:
        supplies = db.execute(
            text(
                "select max(greatest(asked_at, coalesce(decided_at, asked_at))) from po_short_close "
                "where po_no = :p and status = 'closed'"
            ),
            {"p": po_no},
        ).scalar()
    found = rules.blockers(
        lines,
        invoices=_open_invoices(db, po_no),
        failed_payments=_failed_payments(db, po_no),
        advances=_open_advances(db, po_no),
        notes=_open_notes(db, po_no),
        returns=_open_returns(db, po_no),
        excesses=_open_excesses(db, po_no),
        claims=_open_claims(db, po_no),
        short_close_approver=_waiting_short_close(db, po_no),
    )
    return {
        "supply_done": supply_done,
        "supply_done_on": supplies.isoformat() if supplies else None,
        "settled": supply_done and not found,
        "blockers": [{"code": b.code, "words": b.words} for b in found],
        "short_closes": _short_closes(db, po_no),
    }


def check(
    db: Session,
    po_no: str,
    actor: Actor | None = None,
    *,
    tenant: str | None = None,
    as_of: date | None = None,
    commit: bool = True,
) -> dict[str, Any]:
    """Close the order when both tracks are done; a second call changes nothing (R2.4).

    `commit=False` lets a caller that is already in a transaction (the bank settlement) close the order in its own
    transaction; it then sends the notice itself.
    """
    actor = actor or audit.SYSTEM
    found = db.execute(text("select * from po where no = :n for update"), {"n": po_no}).mappings().first()
    if found is None:
        raise ValueError(f"{po_no} does not exist")
    po = dict(found)
    if po["status"] == "cancelled":
        if commit:
            db.rollback()
        return {"po_no": po_no, "status": "cancelled", "closed": False}
    before = state(db, po_no)
    if po["status"] == "closed":
        if commit:
            db.rollback()
        return {"po_no": po_no, "status": "closed", "closed": False, **before}
    if not (before["supply_done"] and before["settled"]):
        if commit:
            db.rollback()
        return {"po_no": po_no, "status": po["status"], "closed": False, **before}
    settled_on = as_of or datetime.now(UTC).date()
    db.execute(text("update po set status = 'closed', closed_at = now() where no = :n"), {"n": po_no})
    db.execute(text("update po_line set status = 'closed' where po_no = :n and status <> 'cancelled'"), {"n": po_no})
    audit.record(
        db,
        actor,
        "po.close",
        "po",
        po_no,
        before={"status": po["status"]},
        after={
            "status": "closed",
            "supply_done_on": before["supply_done_on"],
            "settled_on": settled_on.isoformat(),
        },
    )
    if not commit:
        return {"po_no": po_no, "status": "closed", "closed": True, **before}
    db.commit()
    tenant = tenant or db.info.get("slug")
    if tenant:
        from p2p.invoices.payment import notify_closed  # noqa: PLC0415 - the notice is written once per person

        notify_closed(db, tenant, po_no)
    return {"po_no": po_no, "status": "closed", "closed": True, **before}


def reopen(
    db: Session, po_no: str, cause: str, actor: Actor | None = None, *, tenant: str | None = None
) -> dict[str, Any]:
    """A note, failed or reversed payment, or a counted return after closure puts the order back to received (R2.6)."""
    actor = actor or audit.SYSTEM
    po = db.execute(text("select status from po where no = :n for update"), {"n": po_no}).mappings().first()
    if po is None:
        raise ValueError(f"{po_no} does not exist")
    if po["status"] != "closed":
        db.rollback()
        return {"po_no": po_no, "reopened": False}
    rows = db.execute(
        text(
            "select line_no, qty, received_qty, coalesce(short_closed_qty, 0) as short_closed_qty, status "
            "from po_line where po_no = :n and status <> 'cancelled'"
        ),
        {"n": po_no},
    ).mappings()
    back = "received"
    for ln in rows:
        if ln["status"] != "closed":
            if ln["status"] in ("open", "part_received"):
                back = "part_received"
            continue
        if Decimal(ln["short_closed_qty"]) > 0:
            fresh = "short_closed"
        elif Decimal(ln["received_qty"]) <= 0:
            fresh = "open"
        elif Decimal(ln["received_qty"]) >= Decimal(ln["qty"]):
            fresh = "received"
        else:
            fresh = "part_received"
        db.execute(
            text("update po_line set status = :s where po_no = :n and line_no = :l"),
            {"s": fresh, "n": po_no, "l": ln["line_no"]},
        )
        if fresh in ("open", "part_received"):
            back = "part_received"
    db.execute(text("update po set status = :s, closed_at = null where no = :n"), {"s": back, "n": po_no})
    audit.record(db, actor, "po.reopen", "po", po_no, before={"status": "closed"}, after={"status": back}, reason=cause)
    db.commit()
    return {"po_no": po_no, "reopened": True, "status": back}


# ---- the daily catch-up job --------------------------------------------------------------------


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
    """Check every order that supply is finished with but money may not be: closure that a missed call left open."""
    with Session(routing.engine_for(ctx.tenant)) as db:
        for (no,) in db.execute(
            text("select no from po where status in ('received', 'part_received') order by no")
        ).all():
            try:
                check(db, str(no), audit.SYSTEM, tenant=ctx.tenant)
            except Exception:  # noqa: BLE001 - one bad order never stops the others
                db.rollback()
                log.warning("could not check the closure of %s", no, exc_info=True)
    worker.enqueue_unique(ctx.tenant, JOB, {}, next_run(ctx.now), exclude_id=ctx.id)
