"""Changing a supplier's status: activating, blocking, reactivating, or putting it back under review.

Each of these is a sensitive action (rule 5), so a request only creates a proposal. A person with the permission
approves it (never the person who asked), and the executor makes the change, writes the status history and the audit
row. A supplier cannot be activated while a required document is missing, unverified or expired; the check is made
when the proposal is made and again when it is approved, because the documents may have changed in between.
"""

from datetime import date
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.platform import audit, proposals
from p2p.platform.sod import Actor
from p2p.sourcing import documents

STATUSES = ("draft", "active", "blocked", "prospect")
ALLOWED = {  # from -> to
    "draft": {"active"},
    "active": {"blocked", "draft"},  # blocked: stop using the supplier; draft: put it back under review
    "blocked": {"active"},
    "prospect": {"active", "blocked"},  # a prospect is activated once its identity is checked (D-03 R5, R11)
}
WORDS = {"active": "Activate", "blocked": "Block", "draft": "Put under review"}


class SupplierStatusError(Exception):
    pass


def _supplier(db: Session, code: str) -> tuple[str, str]:
    row = db.execute(
        text("select status, coalesce(trade_name, legal_name) from supplier where code = :c for update"), {"c": code}
    ).first()
    if row is None:
        raise SupplierStatusError(f"supplier {code} does not exist")
    return str(row[0]), str(row[1])


def pending_change(db: Session, code: str) -> dict[str, Any] | None:
    row = (
        db.execute(
            text(
                "select id, summary from proposal where action = 'supplier.set_status' and status = 'pending' "
                "and payload->>'supplier' = :c order by id desc limit 1"
            ),
            {"c": code},
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


def _check_move(db: Session, code: str, current: str, to: str, today: date) -> None:
    if to not in ALLOWED.get(current, set()):
        raise SupplierStatusError(f"a supplier that is {current} cannot be made {to}")
    if to == "active" and current == "prospect":
        checked = db.execute(text("select identity_checked_at from supplier where code = :c"), {"c": code}).scalar()
        if checked is None:
            raise SupplierStatusError("cannot be activated: its identity has not been checked by an independent person")
    if to == "active":
        check = documents.check_supplier(db, code, today)
        if not check.ok:
            raise SupplierStatusError("cannot be activated: " + "; ".join(check.blocking))


def request_change(
    db: Session, code: str, to: str, actor: Actor, reason: str, *, today: date, tenant: str | None = None
) -> int:
    """Make the proposal. Nothing changes until a person approves it."""
    reason = reason.strip()
    if not reason:
        raise SupplierStatusError("a reason is needed")
    current, name = _supplier(db, code)
    _check_move(db, code, current, to, today)
    if pending_change(db, code):
        raise SupplierStatusError("a change for this supplier is already waiting for approval")
    pid = proposals.propose(
        db,
        actor,
        action="supplier.set_status",
        summary=f"{WORDS[to]}: {name} ({code}) is {current}. Reason: {reason}",
        payload={"supplier": code, "to": to, "reason": reason, **({"tenant": tenant} if tenant else {})},
        required_permission="supplier.activate",
        risk="medium",
        document="supplier_activation",
    )
    return pid


@proposals.executor("supplier.set_status")
def _apply(db: Session, payload: dict[str, Any], actor: Actor) -> dict[str, Any]:
    from p2p.formulas.calendar import business_today

    code, to, reason = str(payload["supplier"]), str(payload["to"]), str(payload.get("reason") or "")
    current, _ = _supplier(db, code)
    _check_move(db, code, current, to, business_today())
    db.execute(text("update supplier set status = :s where code = :c"), {"s": to, "c": code})
    db.execute(
        text(
            "insert into supplier_status_history (supplier_code, at, from_status, to_status, reason, by_user) "
            "values (:c, now(), :f, :t, :r, :u)"
        ),
        {"c": code, "f": current, "t": to, "r": reason, "u": actor.id},
    )
    audit.record(
        db,
        actor,
        "supplier.status_change",
        "supplier",
        code,
        before={"status": current},
        after={"status": to},
        reason=reason,
    )
    db.commit()
    if to == "active" and payload.get("tenant"):
        _order_waiting_awards(db, str(payload["tenant"]), code)
    return {"supplier": code, "status": to}


def _order_waiting_awards(db: Session, tenant: str, code: str) -> None:
    """A prospect awarded on condition is now active: the order run makes the orders its awards were waiting for (R5)."""
    from p2p.platform import worker  # noqa: PLC0415 - the worker loads the masters

    for (request_no,) in db.execute(
        text(
            "select distinct r.request_no from award a join rfq r on r.no = a.rfq_no "
            "where a.supplier_code = :c and a.conditional and a.lapsed_at is null and a.status = 'final'"
        ),
        {"c": code},
    ):
        worker.enqueue(tenant, "order_request", {"request_no": request_no})
