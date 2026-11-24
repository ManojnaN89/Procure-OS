"""Goods above the order: the buyer accepts them by revising the order, or returns them (C3, task 4.2, 9.3).

Accepting is a material change of the order (Q19), so it is a proposal (`receipt.excess_accept`) that the person the
`po` approval rows name for the new total approves, never a maker of the order. The approval carries: the executor makes
the new version in one transaction with the goods moving from quarantine to usable, the order line and its budget, and
the supplier getting the new version with a respond link. A rejected proposal sends the excess back to waiting with a
fresh task for the buyer. Returning it makes a draft return from the excess hold (receiving/returns.py).
"""

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.approvals import budget
from p2p.approvals.workflow import ApprovalError, org_code_of
from p2p.formulas.calendar import business_today
from p2p.formulas.money import format_inr, format_qty
from p2p.formulas.orders import LineAmounts, line_amounts, order_totals
from p2p.orders import changes, workflow
from p2p.orders import service as ordering
from p2p.platform import audit, outbox, proposals, tasks
from p2p.platform.sod import Actor, user
from p2p.receiving import goods, quality, returns

ACTION = "receipt.excess_accept"
REVERSE = "receipt.reverse"


class ExcessError(Exception):
    """The excess makes no sense for this decision: `status` is the HTTP answer (404, 409, 422)."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def _row(db: Session, excess_id: int) -> dict[str, Any]:
    row = db.execute(text("select * from receipt_excess where id = :i for update"), {"i": excess_id}).mappings().first()
    if row is None:
        raise ExcessError(404, f"there is no excess {excess_id}")
    return dict(row)


def listing(db: Session, *, open_only: bool = False) -> list[dict[str, Any]]:
    """Goods above the order, newest first; `open_only` keeps those a person still has to decide."""
    return [
        dict(r)
        for r in db.execute(
            text(
                "select e.*, l.description, l.item_code, l.uom, p.version, coalesce(s.trade_name, s.legal_name) as supplier "
                "from receipt_excess e join po_line l on l.po_no = e.po_no and l.line_no = e.po_line_no "
                "join po p on p.no = e.po_no join supplier s on s.code = p.supplier_code "
                "where (not :o or e.status = any(:open)) order by e.created_at desc, e.id desc limit 500"
            ),
            {"o": open_only, "open": ["waiting", "proposed", "returning"]},
        ).mappings()
    ]


def _new_qty(db: Session, row: dict[str, Any]) -> Decimal:
    line = db.execute(
        text("select received_qty from po_line where po_no = :p and line_no = :l"),
        {"p": row["po_no"], "l": row["po_line_no"]},
    ).scalar()
    return Decimal(line or 0) + Decimal(row["qty"])


def _named(db: Session, po: dict[str, Any], total: Decimal, today: date, also: frozenset[str]) -> tuple[Any, list[str]]:
    try:
        rule, steps = workflow.people_for(db, po, total, today, also)
    except ApprovalError:
        return None, []
    return rule, [str(s.approver) for s in steps]


def _new_total(db: Session, po: dict[str, Any], row: dict[str, Any], amounts: LineAmounts) -> Decimal:
    """The order's total with this one line at its new amount: every other line and charge as it stands."""
    others = [
        LineAmounts(r[0], r[1], r[2], r[3])
        for r in db.execute(
            text(
                "select taxable, cgst, sgst, igst from po_line where po_no = :n and status <> 'cancelled' "
                "and line_no <> :l"
            ),
            {"n": po["no"], "l": row["po_line_no"]},
        )
    ] + [
        LineAmounts(r[0], r[1], r[2], r[3])
        for r in db.execute(text("select amount, cgst, sgst, igst from po_charge where po_no = :n"), {"n": po["no"]})
    ]
    return order_totals([*others, amounts]).total


def propose_accept(
    db: Session, tenant: str, excess_id: int, user_id: int, version: int, reason: str, *, as_of: date
) -> dict[str, Any]:
    """Ask the band's person to accept the excess by revising the order to the quantity counted plus it. Commits."""
    why = (reason or "").strip()
    if not why:
        raise ExcessError(422, "say why the goods above the order are accepted")
    row = _row(db, excess_id)
    if row["status"] != "waiting":
        db.rollback()
        raise ExcessError(409, f"excess {excess_id} is {row['status']}, not waiting for a decision")
    po = dict(db.execute(text("select * from po where no = :n for update"), {"n": row["po_no"]}).mappings().one())
    if po["status"] in ("closed", "cancelled"):
        db.rollback()
        raise ExcessError(409, "the order changed: return the goods or propose again")
    if int(version) != int(po["version"]):
        db.rollback()
        raise ExcessError(409, "the order changed: return the goods or propose again")
    today = as_of or business_today()
    new_qty = _new_qty(db, row)
    line = db.execute(
        text("select unit_price, gst_rate from po_line where po_no = :p and line_no = :l"),
        {"p": row["po_no"], "l": row["po_line_no"]},
    ).one()
    supplier = db.execute(
        text("select state_code, gstin, gst_reg_type from supplier where code = :c"), {"c": po["supplier_code"]}
    ).one()
    supplier_state = supplier[0] or (str(supplier[1])[:2] if supplier[1] else None)
    _, place = ordering.place_of_supply(db, po["ship_to_location"])
    if not supplier_state or not place:
        db.rollback()
        raise ExcessError(409, "the state of the supplier or of the place is unknown, so the GST cannot be worked out")
    amounts = line_amounts(new_qty, Decimal(line[0]), line[1], supplier_state, place)
    new_total = _new_total(db, po, row, amounts)
    proposer = org_code_of(db, user_id)
    also = frozenset({proposer}) if proposer else frozenset()
    rule, named = _named(db, po, new_total, today, also)
    if not named:
        db.rollback()
        raise ExcessError(
            409, "nobody who did not make the order can approve the new total: a change to the order rules is needed"
        )
    checks = budget.check_order(db, po["no"], today)
    budget_words = budget.summary(checks)
    pid = proposals.propose(
        db,
        Actor("user", str(user_id), proposer),
        action=ACTION,
        summary=(
            f"Accept {format_qty(row['qty'])} {row['uom'] or ''} of {_description(db, row)} above {row['po_no']} "
            f"on {row['gr_no']}: revise line {row['po_line_no']} to {format_qty(new_qty)} "
            f"({format_inr(new_total)}, quantity up). {budget_words}"
        ).strip(),
        payload={
            "excess": excess_id,
            "po_no": row["po_no"],
            "line": row["po_line_no"],
            "version": int(po["version"]),
            "qty": str(row["qty"]),
            "new_qty": str(new_qty),
            "new_total": str(new_total),
            "approver": named[0],
            "rule_code": rule.code,
            "rule_version": rule.version,
            "band": None if rule.max_value is None else str(rule.max_value),
        },
        required_permission="po.approve",
        risk="medium",
        value=new_total,
    )
    db.execute(
        text("update receipt_excess set status = 'proposed', proposal_id = :p, reason = :why where id = :i"),
        {"p": pid, "why": why, "i": excess_id},
    )
    if row["task_id"]:
        tasks.complete_task(db, int(row["task_id"]), user_id)
    audit.record(
        db, Actor("user", str(user_id), proposer), "receipt.excess_propose", "receipt_excess", excess_id,
        after={"proposal": pid, "new_qty": str(new_qty), "new_total": str(new_total), "approver": named[0],
               "rule": f"{rule.code}:v{rule.version}"},
        reason=why,
    )  # fmt: skip
    db.commit()
    return {"proposal_id": pid, "approver": named[0], "new_qty": str(new_qty), "new_total": str(new_total)}


def _ordered(db: Session, row: dict[str, Any]) -> Decimal:
    return Decimal(
        db.execute(
            text("select qty from po_line where po_no = :p and line_no = :l"),
            {"p": row["po_no"], "l": row["po_line_no"]},
        ).scalar_one()
    )


def _description(db: Session, row: dict[str, Any]) -> str:
    value = db.execute(
        text("select description from po_line where po_no = :p and line_no = :l"),
        {"p": row["po_no"], "l": row["po_line_no"]},
    ).scalar()
    return str(value or "an item")


def _allowed(db: Session, payload: dict[str, Any], today: date) -> tuple[dict[str, Any], list[str]]:
    po = dict(db.execute(text("select * from po where no = :n"), {"n": payload["po_no"]}).mappings().one())
    rule, named = _named(db, po, Decimal(payload["new_total"]), today, frozenset())
    return po, named


@proposals.guard(ACTION)
def _guard(db: Session, payload: dict[str, Any], actor: Actor) -> None:
    """Only the person the po band names for the new total, and never a maker of the order (C1)."""
    code = org_code_of(db, int(actor.id)) if str(actor.id).isdigit() else None
    if not code:
        raise ExcessError(403, "the acceptance is approved by a person of the company")
    po, named = _allowed(db, payload, business_today())
    workflow.refuse_maker(db, po, code)
    if code not in named:
        raise ExcessError(
            403, f"{po['no']} would total {format_inr(payload['new_total'])}: its approval band names "
            f"{', '.join(named) or 'nobody'} for that total"
        )  # fmt: skip


@proposals.executor(ACTION)
def _accept(db: Session, payload: dict[str, Any], actor: Actor) -> dict[str, Any]:
    """The approver's yes: the order's new version with the excess counted, its stock to usable and its budget, in one
    transaction. The supplier is mailed after the commit."""
    today = business_today()
    row = _row(db, int(payload["excess"]))
    if row["status"] != "proposed":
        raise ExcessError(409, f"excess {row['id']} is {row['status']}: nothing to accept")
    po = dict(db.execute(text("select * from po where no = :n for update"), {"n": row["po_no"]}).mappings().one())
    if int(po["version"]) != int(payload["version"]):
        raise ExcessError(409, "the order changed: return the goods or propose again")
    if po["status"] in ("closed", "cancelled"):
        raise ExcessError(409, "the order changed: return the goods or propose again")
    code = org_code_of(db, int(actor.id)) if str(actor.id).isdigit() else None
    if not code:
        raise ExcessError(403, "the acceptance is approved by a person of the company")
    rule, named = _allowed(db, payload, today)
    workflow.refuse_maker(db, po, code)
    if code not in named:
        raise ExcessError(403, f"the approval band for {format_inr(payload['new_total'])} does not name you")
    checks = budget.check_order(db, po["no"], today)
    if budget.blocked(checks):
        raise ExcessError(409, budget.summary(checks))
    reason = f"Goods above {po['no']} accepted: {format_qty(payload['qty'])} on {row['gr_no']}"
    made = changes.revise(
        db, None, po["no"], int(actor.id) if str(actor.id).isdigit() else 0, reason, [],
        excess_id=int(row["id"]), approved_by=actor, rule_code=payload["rule_code"],
        rule_version=payload["rule_version"], band=Decimal(payload["band"]) if payload.get("band") else None,
    )  # fmt: skip
    line = dict(
        db.execute(
            text("select * from po_line where po_no = :p and line_no = :l"),
            {"p": row["po_no"], "l": row["po_line_no"]},
        )
        .mappings()
        .one()
    )
    if row["hold_id"]:
        quality.release_excess(db, int(row["hold_id"]), code, today)
    goods.count_accepted(db, row["gr_no"], po, line, Decimal(payload["qty"]), today, in_stock=True, actor=actor)
    goods.refresh_order(db, po["no"])
    db.execute(
        text(
            "update receipt_excess set status = 'accepted', version_made = :v, decided_by = :u, decided_at = now(), "
            "reason = :why where id = :i"
        ),
        {"v": made["version"], "u": code, "why": reason, "i": row["id"]},
    )
    audit.record(
        db, user(int(actor.id)), "receipt.excess_accept", "receipt_excess", int(row["id"]),
        after={"po": po["no"], "new_qty": payload["new_qty"], "new_total": made["total"],
               "version": made["version"], "stock": "quarantine to usable"}, reason=reason,
    )  # fmt: skip
    db.commit()
    tenant = outbox.tenant_of(db)
    goods.reserve_arrived(db, tenant, row["gr_no"])
    changes.mail_version(db, tenant, made["po"], made["version"], reason)
    return {"excess": row["id"], "version": made["version"], "new_total": made["total"]}


@proposals.closer(ACTION)
def _rejected(db: Session, proposal_id: int, payload: dict[str, Any], actor: Actor) -> None:
    """The approver refused: the excess waits again, with a new task saying to return it."""
    row = (
        db.execute(text("select * from receipt_excess where id = :i for update"), {"i": int(payload["excess"])})
        .mappings()
        .first()
    )
    if row is None or row["status"] != "proposed":
        return
    db.execute(text("update receipt_excess set status = 'waiting', proposal_id = null where id = :i"), {"i": row["id"]})
    tenant = outbox.tenant_of(db)
    po = dict(db.execute(text("select * from po where no = :n"), {"n": row["po_no"]}).mappings().one())
    line = dict(
        db.execute(
            text("select * from po_line where po_no = :p and line_no = :l"),
            {"p": row["po_no"], "l": row["po_line_no"]},
        )
        .mappings()
        .one()
    )
    task_id = goods.excess_task(
        db, tenant, row["gr_no"], po, line, int(row["id"]), Decimal(row["qty"]), row["uom"], line["description"],
        po["ship_to_location"],
    )  # fmt: skip
    if task_id is not None:
        db.execute(text("update receipt_excess set task_id = :t where id = :i"), {"t": task_id, "i": row["id"]})


def send_back(db: Session, tenant: str, excess_id: int, user_id: int, reason: str, *, as_of: date) -> dict[str, Any]:
    """Return the whole excess: a draft return from its quarantine hold. Commits."""
    why = (reason or "").strip()
    if not why:
        raise ExcessError(422, "say why the goods go back")
    row = _row(db, excess_id)
    if row["status"] != "waiting":
        db.rollback()
        raise ExcessError(409, f"excess {excess_id} is {row['status']}, not waiting for a decision")
    if not row["hold_id"]:
        db.rollback()
        raise ExcessError(409, "the excess is not stocked, so there is nothing to return: accept it on the order")
    made = returns.create(
        db, tenant, user_id, why, [returns.ReturnLineIn(int(row["hold_id"]), Decimal(row["qty"]))], as_of=as_of
    )
    db.execute(
        text(
            "update receipt_excess set status = 'returning', return_no = :r, decided_by = :u, decided_at = now(), "
            "reason = :why where id = :i"
        ),
        {"r": made.no, "u": org_code_of(db, user_id), "why": why, "i": excess_id},
    )
    if row["task_id"]:
        from p2p.platform import tasks  # noqa: PLC0415 - returning it finishes the buyer's task

        tasks.complete_task(db, int(row["task_id"]), user_id)
    audit.record(
        db, Actor("user", str(user_id), org_code_of(db, user_id)), "receipt.excess_return", "receipt_excess", excess_id,
        after={"return": made.no}, reason=why,
    )  # fmt: skip
    db.commit()
    return {"return_no": made.no}
