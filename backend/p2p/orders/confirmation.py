"""The supplier's answer to an order (plan step 8): accept, decline or ask for a change, and the buyer's decision.

A supplier answers from the link in the order email (orders/supplier_links.py) or by replying to it; a reply is read by the
supplier-mail agent (agents/supplier_comms.py), where a model only classifies and copies. Accepting is the acknowledgement
(orders/dispatch.py). Every other answer becomes a proposed change, old value to new, that a buyer accepts or rejects;
nothing on the order moves before that:

* an accepted date, quantity or price makes a new version of the order (orders/changes.py) and tells the requester;
* a price rise above 'supplier_price_pct' is not the buyer's to accept: accepting it makes a proposal for the person the
  price variance rule names (the purchase manager), and only that person's approval changes the order;
* an accepted decline cancels the order and gives its lines back to the buyer to source again.

The supplier is told the outcome; a rejected change comes with a fresh link to answer the order as it stands.
"""

import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.approvals import rules as approval_rules
from p2p.approvals.workflow import name_of, org_code_of
from p2p.formulas import supplier_changes as judge
from p2p.formulas.calendar import business_today
from p2p.formulas.money import format_inr, format_qty
from p2p.orders import changes, dispatch, supplier_links
from p2p.platform import audit, notifications, permissions, proposals, tasks
from p2p.platform.sod import Actor, user

log = logging.getLogger(__name__)
KINDS = ("date", "qty", "price", "decline")
ANSWERABLE = ("sent", "acknowledged", "part_received")
PRICE_ACTION = "po.supplier_price"


class ConfirmationError(Exception):
    pass


@dataclass(frozen=True)
class Proposed:
    """One change the supplier asks for: a new delivery date, quantity or unit price on a line, or a decline."""

    kind: str
    line_no: int | None = None
    new_value: str | None = None


def supplier_actor(code: str) -> Actor:
    return Actor("service", f"supplier-link:{code}")


def _setting_tolerance(db: Session, key: str, default: str) -> Decimal:
    value = db.execute(
        text("select value from tolerance where key = :k and category_code = 'ANY' limit 1"), {"k": key}
    ).scalar()
    return Decimal(str(value)) if value not in (None, "") else Decimal(default)


def price_tolerance(db: Session) -> Decimal:
    return _setting_tolerance(db, "supplier_price_pct", "2")


def _buyer_login(db: Session, tenant: str, po: dict[str, Any]) -> int:
    """The order's buyer, else the first person with the Buyer role."""
    login = notifications.login_of(db, tenant, po["buyer"])
    if login:
        return login
    found = db.execute(text("select user_id from user_profile where role = 'Buyer' order by user_id limit 1")).scalar()
    return int(found or db.execute(text("select min(user_id) from user_profile")).scalar() or 0)


def _order(db: Session, po_no: str, *, lock: bool = False) -> dict[str, Any]:
    row = (
        db.execute(
            text("select * from po where no = :n for update" if lock else "select * from po where no = :n"),
            {"n": po_no},
        )
        .mappings()
        .first()
    )
    if row is None:
        raise ConfirmationError(f"{po_no} does not exist")
    return dict(row)


def _old_value(kind: str, line: dict[str, Any]) -> str:
    if kind == "date":
        when = line["promised_date"] or line["need_by"]
        return when.isoformat() if when else ""
    if kind == "qty":
        return str(Decimal(line["qty"]).normalize())
    return str(Decimal(line["unit_price"]).normalize())


def _parse(kind: str, value: str | None, line_no: int | None) -> str:
    """The new value in its stored form; refused if it is not a date, or a number above zero."""
    where = f"line {line_no}: " if line_no else ""
    if not value or not str(value).strip():
        raise ConfirmationError(f"{where}a new {'date' if kind == 'date' else kind} is needed")
    if kind == "date":
        try:
            return date.fromisoformat(str(value).strip()).isoformat()
        except ValueError:
            raise ConfirmationError(f"{where}the date must be like 2026-12-06") from None
    try:
        number = Decimal(str(value).strip().replace(",", ""))
    except InvalidOperation:
        raise ConfirmationError(f"{where}the {kind} must be a number") from None
    if not number.is_finite() or number <= 0:
        raise ConfirmationError(f"{where}the {'quantity' if kind == 'qty' else 'price'} must be more than zero")
    return str(number.normalize())


def propose(
    db: Session,
    tenant: str,
    po_no: str,
    asked: list[Proposed],
    *,
    source: str,
    reason: str | None,
    actor: Actor,
    email_id: int | None = None,
    task_id: int | None = None,
) -> list[int]:
    """Record what the supplier asks for, for the buyer. Nothing on the order changes. Returns the change ids.

    A task for the buyer is made unless the caller already made one (`task_id`, the supplier-mail agent's)."""
    po = _order(db, po_no, lock=True)
    if po["status"] not in ANSWERABLE:
        db.rollback()
        raise ConfirmationError(
            f"{po_no} is {po['status']}: a change can be asked for only while it is with the supplier"
        )
    if not asked:
        db.rollback()
        raise ConfirmationError("nothing was asked for")
    lines = {
        r["line_no"]: dict(r)
        for r in db.execute(
            text("select * from po_line where po_no = :n and status in ('open', 'part_received')"), {"n": po_no}
        ).mappings()
    }
    rows: list[dict[str, Any]] = []
    tolerance = price_tolerance(db)
    for item in asked:
        if item.kind not in KINDS:
            db.rollback()
            raise ConfirmationError(f"'{item.kind}' is not a change that can be asked for")
        if item.kind == "decline":
            rows.append({"kind": "decline", "line": None, "old": po["status"], "new": "declined", "within": None})
            continue
        line = lines.get(item.line_no or -1)
        if line is None:
            db.rollback()
            raise ConfirmationError(f"line {item.line_no} is not an open line of {po_no}")
        try:
            new = _parse(item.kind, item.new_value, item.line_no)
        except ConfirmationError:
            db.rollback()
            raise
        old = _old_value(item.kind, line)
        if new == old:
            db.rollback()
            raise ConfirmationError(f"line {item.line_no}: the {item.kind} asked for is the one on the order")
        within = judge.judge_price(Decimal(old), Decimal(new), tolerance).ok if item.kind == "price" else None
        rows.append({"kind": item.kind, "line": item.line_no, "old": old, "new": new, "within": within})
    if task_id is None:
        what = "cannot supply" if any(r["kind"] == "decline" for r in rows) else "asks for a change to"
        task_id = tasks.create_task(
            db,
            tenant,
            kind="supplier_change",
            title=f"{_supplier_name(db, po['supplier_code'])} {what} {po_no}: accept or reject"[:200],
            assignee=_buyer_login(db, tenant, po),
            sla_hours=24,
            payload={"po_no": po_no, "source": source},
            actor=actor,
        )
    ids: list[int] = []
    for r in rows:
        ids.append(
            int(
                db.execute(
                    text(
                        "insert into po_change (po_no, po_line_no, kind, old_value, new_value, reason, source, email_id, "
                        "within_tolerance, task_id) values (:p, :l, :k, :o, :n, :r, :s, :e, :w, :t) returning id"
                    ),
                    {
                        "p": po_no, "l": r["line"], "k": r["kind"], "o": r["old"], "n": r["new"],
                        "r": (reason or "").strip()[:500] or None, "s": source, "e": email_id, "w": r["within"], "t": task_id,
                    },
                ).scalar_one()
            )
        )  # fmt: skip
    audit.record(
        db, actor, "po.change_proposed", "po", po_no,
        after={"changes": [{"id": i, **r} for i, r in zip(ids, rows, strict=True)], "source": source}, reason=reason,
    )  # fmt: skip
    db.commit()
    return ids


def _supplier_name(db: Session, code: str) -> str:
    return str(
        db.execute(text("select coalesce(trade_name, legal_name) from supplier where code = :c"), {"c": code}).scalar()
        or code
    )


# ---- the supplier's answer from the link -------------------------------------------------------------


def accept_by_link(
    db: Session, tenant: str, link: supplier_links.Link, promised: date | None, note: str | None
) -> tuple[str, list[str]]:
    """The supplier accepts the order from its link. Returns a link to record the dispatch (also emailed to the supplier)
    and the warnings of the acknowledgement (a promised date later than needed)."""
    supplier_links.check(db, link, lock=True)
    supplier_links.use(db, link, "accept")
    try:
        warnings = dispatch.acknowledge(
            db,
            link.po_no,
            supplier_actor(link.supplier_code),
            promised=promised,
            note=(note or "").strip()[:500] or "accepted by link",
        )
    except dispatch.DispatchError as exc:
        db.rollback()
        raise ConfirmationError(str(exc)) from None
    token = supplier_links.issue(db, tenant, link.po_no, "dispatch")
    db.commit()
    po = _order(db, link.po_no)
    changes.tell_supplier(
        db, po, f"Purchase order {link.po_no}: thank you for accepting",
        f"Thank you for accepting order {link.po_no}. When you ship, please record what you sent (quantities, vehicle or "
        f"LR/AWB number) here, so that our store expects it:\n{supplier_links.url(token)}",
    )  # fmt: skip
    _tell_buyer(db, tenant, link.po_no, f"{link.po_no} was accepted by the supplier", "; ".join(warnings))
    return token, warnings


def answer_by_link(
    db: Session, tenant: str, link: supplier_links.Link, asked: list[Proposed], reason: str | None
) -> list[int]:
    """The supplier declines or asks for changes from its link: proposed changes for the buyer."""
    supplier_links.check(db, link, lock=True)
    supplier_links.use(db, link, "decline" if any(a.kind == "decline" for a in asked) else "change")
    if any(a.kind == "decline" for a in asked) and not (reason or "").strip():
        db.rollback()
        raise ConfirmationError("please say why the order cannot be supplied")
    return propose(
        db, tenant, link.po_no, asked, source="link", reason=reason, actor=supplier_actor(link.supplier_code)
    )


def _tell_buyer(db: Session, tenant: str, po_no: str, title: str, body: str) -> None:
    po = _order(db, po_no)
    notifications.notify(
        db, tenant, _buyer_login(db, tenant, po), "order", title, body, link=f"#/purchase-orders/{po_no}", email=False
    )


# ---- the buyer's decision ----------------------------------------------------------------------------


def _change(db: Session, change_id: int) -> dict[str, Any]:
    row = db.execute(text("select * from po_change where id = :i for update"), {"i": change_id}).mappings().first()
    if row is None:
        raise ConfirmationError(f"change {change_id} does not exist")
    return dict(row)


def _describe(ch: dict[str, Any], db: Session) -> str:
    if ch["kind"] == "decline":
        return "the supplier cannot supply the order"
    line = f"line {ch['po_line_no']}"
    if ch["kind"] == "date":
        return f"{line}: delivery date {_day(ch['old_value'])} to {_day(ch['new_value'])}"
    if ch["kind"] == "qty":
        return f"{line}: quantity {format_qty(Decimal(ch['old_value']))} to {format_qty(Decimal(ch['new_value']))}"
    return f"{line}: price {format_inr(Decimal(ch['old_value']))} to {format_inr(Decimal(ch['new_value']))}"


def _day(value: str | None) -> str:
    return f"{date.fromisoformat(value):%d %b %Y}" if value else "none"


def decide(
    db: Session, tenant: str, change_id: int, user_id: int, *, accept: bool, note: str | None = None
) -> dict[str, Any]:
    """A buyer accepts or rejects a proposed change; for a price above the tolerance, the person the price variance rule
    names (the purchase manager) does, through the proposal the buyer's acceptance made."""
    ch = _change(db, change_id)
    if ch["status"] == "waiting_approval":
        db.rollback()
        return _decide_price(db, tenant, ch, user_id, accept=accept, note=note)
    if ch["status"] != "proposed":
        db.rollback()
        raise ConfirmationError(f"this change is already {ch['status'].replace('_', ' ')}")
    if not permissions.can_anywhere(db, user_id, "po.create"):
        db.rollback()
        raise permissions.PermissionDenied("only a buyer decides a supplier's change")
    if not accept:
        why = (note or "").strip()
        if not why:
            db.rollback()
            raise ConfirmationError("a reason is needed to reject a change")
        return _reject(db, tenant, ch, user_id, why)
    if ch["kind"] == "price" and not ch["within_tolerance"]:
        return _ask_for_price_approval(db, tenant, ch, user_id, note)
    if ch["kind"] == "decline":
        return _accept_decline(db, tenant, ch, user_id, note)
    return _apply(db, tenant, ch, user_id, note)


def _finish(db: Session, ch: dict[str, Any], status: str, user_id: int, note: str | None, version: int | None) -> None:
    db.execute(
        text(
            "update po_change set status = :s, decided_by = :u, decided_at = now(), decision_note = :n, "
            "version_made = :v where id = :i"
        ),
        {"s": status, "u": str(user_id), "n": note, "v": version, "i": ch["id"]},
    )
    db.commit()
    if ch["task_id"]:
        left = db.execute(
            text("select count(*) from po_change where task_id = :t and status in ('proposed', 'waiting_approval')"),
            {"t": ch["task_id"]},
        ).scalar_one()
        if not left:
            tasks.complete_task(db, int(ch["task_id"]), user_id)


def _reject(db: Session, tenant: str, ch: dict[str, Any], user_id: int, why: str) -> dict[str, Any]:
    po = _order(db, ch["po_no"])
    audit.record(
        db, user(user_id), "po.change_rejected", "po", ch["po_no"],
        after={"change": ch["id"], "kind": ch["kind"], "from": ch["old_value"], "to": ch["new_value"]}, reason=why,
    )  # fmt: skip
    _finish(db, ch, "rejected", user_id, why, None)
    again = ""
    if po["status"] == "sent":  # the supplier has not accepted yet: a fresh link to answer the order as it stands
        token = supplier_links.issue(db, tenant, ch["po_no"], "respond")
        db.commit()
        again = (
            f"\n\nPlease accept the order as it stands, or tell us what you can do, here:\n{supplier_links.url(token)}"
        )
    told = changes.tell_supplier(
        db, po, f"Purchase order {ch['po_no']}: change not accepted",
        f"We cannot accept the change you asked for on order {ch['po_no']} ({_describe(ch, db)}). Reason: {why}\n"
        f"The order stands as placed.{again}",
    )  # fmt: skip
    return {"status": "rejected", "supplier_told": told}


def _apply(db: Session, tenant: str, ch: dict[str, Any], user_id: int, note: str | None) -> dict[str, Any]:
    """Make the new version of the order, then tell the requester and the buyer."""
    line = int(ch["po_line_no"])
    new = ch["new_value"]
    if ch["kind"] == "date":
        change = changes.LineChange(line, need_by=date.fromisoformat(new), promised_date=date.fromisoformat(new))
    elif ch["kind"] == "qty":
        change = changes.LineChange(line, qty=Decimal(new))
    else:
        change = changes.LineChange(line, unit_price=Decimal(new))
    what = _describe(ch, db)
    try:
        made = changes.revise(
            db, tenant, ch["po_no"], user_id, f"The supplier asked for it: {what}" + (f". {note}" if note else ""),
            [change], approved_change=ch["kind"] == "price",
        )  # fmt: skip
    except changes.ChangeError as exc:
        db.rollback()
        raise ConfirmationError(str(exc)) from None
    audit.record(
        db, user(user_id), "po.change_accepted", "po", ch["po_no"],
        after={"change": ch["id"], "kind": ch["kind"], "from": ch["old_value"], "to": new, "version": made["version"]},
        reason=note,
    )  # fmt: skip
    _finish(db, _change(db, int(ch["id"])), "accepted", user_id, note, int(made["version"]))
    po = _order(db, ch["po_no"])
    if (
        po["status"] == "sent"
    ):  # the supplier asked for the change instead of accepting: agreeing to it settles the order
        dispatch.acknowledge(db, ch["po_no"], user(user_id), promised=None, note=f"accepted with the change: {what}")
    changes.tell_people(
        db, tenant, ch["po_no"], f"Order {ch['po_no']} changed (version {made['version']})",
        f"The supplier asked for a change and it was accepted: {what}.", buyer=po["buyer"],
    )  # fmt: skip
    return {"status": "accepted", "version": made["version"], "supplier_told": made["supplier_told"]}


# ---- a price above the tolerance: the purchase manager ----------------------------------------------


def _price_approver(db: Session, value: Decimal) -> approval_rules.Step:
    rule = approval_rules.find_rule(db, "price_variance", value, business_today())
    steps = approval_rules.resolve_chain(db, rule.roles, requester=None, department=None, on_date=business_today())
    if not steps:
        raise ConfirmationError("the price variance rule names nobody to accept a price rise")
    return steps[0]


def _ask_for_price_approval(
    db: Session, tenant: str, ch: dict[str, Any], user_id: int, note: str | None
) -> dict[str, Any]:
    qty = db.execute(
        text("select qty from po_line where po_no = :p and line_no = :l"), {"p": ch["po_no"], "l": ch["po_line_no"]}
    ).scalar_one()
    rise = ((Decimal(ch["new_value"]) - Decimal(ch["old_value"])) * Decimal(qty)).quantize(Decimal("0.01"))
    try:
        step = _price_approver(db, rise)
    except approval_rules.ApprovalRuleError as exc:
        db.rollback()
        raise ConfirmationError(str(exc)) from None
    what = _describe(ch, db)
    pid = proposals.propose(
        db,
        user(user_id),
        action=PRICE_ACTION,
        summary=f"Accept the supplier's price rise on {ch['po_no']}, {what} ({format_inr(rise)} more before tax)",
        payload={"tenant": tenant, "change_id": int(ch["id"]), "approver": step.approver, "delegate": step.delegate},
        required_permission="po.approve",
        value=rise,
    )
    db.execute(
        text("update po_change set status = 'waiting_approval', proposal_id = :p where id = :i"),
        {"p": pid, "i": ch["id"]},
    )
    audit.record(
        db, user(user_id), "po.change_referred", "po", ch["po_no"],
        after={"change": ch["id"], "proposal": pid, "to": step.approver, "rise": rise}, reason=note,
    )  # fmt: skip
    db.commit()
    who = notifications.login_of(db, tenant, step.delegate or step.approver)
    if who:
        tasks.create_task(
            db, tenant, kind="supplier_price_change",
            title=f"Accept or reject a price rise on {ch['po_no']}: {what}"[:200],
            assignee=who, sla_hours=24, payload={"po_no": ch["po_no"], "change_id": int(ch["id"]), "proposal_id": pid},
            actor=user(user_id),
        )  # fmt: skip
    return {"status": "waiting_approval", "proposal_id": pid, "approver": name_of(db, step.approver)}


def _named(db: Session, payload: dict[str, Any], user_id: int) -> None:
    if org_code_of(db, user_id) not in (payload.get("approver"), payload.get("delegate")):
        raise permissions.PermissionDenied(
            f"this price rise is for {name_of(db, str(payload.get('approver')))} to accept or reject"
        )


def _decide_price(
    db: Session, tenant: str, ch: dict[str, Any], user_id: int, *, accept: bool, note: str | None
) -> dict[str, Any]:
    pid = int(ch["proposal_id"])
    payload = db.execute(text("select payload from proposal where id = :i"), {"i": pid}).scalar_one()
    _named(db, dict(payload), user_id)
    try:
        if accept:
            proposals.approve(db, pid, user_id, note=note)
            outcome = str(db.execute(text("select status from po_change where id = :i"), {"i": ch["id"]}).scalar_one())
            if outcome != "accepted":
                result = db.execute(text("select result from proposal where id = :i"), {"i": pid}).scalar()
                raise ConfirmationError(f"the price could not be changed: {(result or {}).get('error', 'see the log')}")
            return {"status": "accepted"}
        why = (note or "").strip()
        if not why:
            raise ConfirmationError("a reason is needed to reject a change")
        proposals.reject(db, pid, user_id, why)
    except proposals.ProposalError as exc:
        db.rollback()
        raise ConfirmationError(str(exc)) from None
    return _reject(db, tenant, _change(db, int(ch["id"])), user_id, why)


@proposals.executor(PRICE_ACTION)
def _price_accepted(db: Session, payload: dict[str, Any], actor: Actor) -> dict[str, Any]:
    """The purchase manager accepted the price rise: the new version is made as that person."""
    user_id = int(actor.id)
    _named(db, payload, user_id)
    ch = _change(db, int(payload["change_id"]))
    if ch["status"] != "waiting_approval":
        raise ConfirmationError(f"this change is already {ch['status']}")
    return _apply(db, str(payload["tenant"]), ch, user_id, "accepted by the purchase manager")


# ---- a decline: back to the buyer to source again ------------------------------------------------------


def _accept_decline(db: Session, tenant: str, ch: dict[str, Any], user_id: int, note: str | None) -> dict[str, Any]:
    po_no = ch["po_no"]
    supplier = _supplier_name(db, _order(db, po_no)["supplier_code"])
    why = f"{supplier} cannot supply it" + (f": {ch['reason']}" if ch["reason"] else "")
    lines = db.execute(
        text("select request_no, request_line_no from po_line where po_no = :n and status <> 'cancelled'"), {"n": po_no}
    ).all()
    try:
        changes.cancel(db, tenant, po_no, user_id, why, reorder=True)
    except changes.ChangeError as exc:
        db.rollback()
        raise ConfirmationError(str(exc)) from None
    for (
        request_no,
        line_no,
    ) in lines:  # the buyer sources the line again: nothing orders it from the same supplier by itself
        db.execute(
            text(
                "update sourcing_decision set method = 'manual', route = null, rfq_no = null, needs_agreement = false, "
                "reason = :r where request_no = :n and line_no = :l"
            ),
            {"r": f"{why} ({po_no} was cancelled): a buyer sources it again", "n": request_no, "l": line_no},
        )
        db.execute(
            text("update request_line set status = 'open' where request_no = :n and line_no = :l"),
            {"n": request_no, "l": line_no},
        )
    db.execute(
        text("update po_change set status = 'withdrawn' where po_no = :p and status = 'proposed' and id <> :i"),
        {"p": po_no, "i": ch["id"]},
    )
    audit.record(
        db, user(user_id), "po.change_accepted", "po", po_no,
        after={"change": ch["id"], "kind": "decline", "lines_to_source": [f"{n}/{ln}" for n, ln in lines]}, reason=note,
    )  # fmt: skip
    _finish(db, _change(db, int(ch["id"])), "accepted", user_id, note, None)
    tasks.create_task(
        db, tenant, kind="source_again", title=f"Source again: {why} ({po_no})"[:200],
        assignee=user_id, sla_hours=24, payload={"po_no": po_no, "lines": [[n, ln] for n, ln in lines]}, actor=user(user_id),
    )  # fmt: skip
    return {"status": "accepted", "cancelled": po_no, "to_source": [f"{n} line {ln}" for n, ln in lines]}


# ---- what the order page shows -----------------------------------------------------------------------


def list_changes(db: Session, po_no: str, user_id: int) -> list[dict[str, Any]]:
    may_decide = permissions.can_anywhere(db, user_id, "po.create")
    me = org_code_of(db, user_id)
    out: list[dict[str, Any]] = []
    for r in db.execute(
        text(
            "select c.*, p.payload from po_change c left join proposal p on p.id = c.proposal_id "
            "where c.po_no = :n order by c.id desc"
        ),
        {"n": po_no},
    ).mappings():
        named = (r["payload"] or {}).get("approver"), (r["payload"] or {}).get("delegate")
        out.append(
            {
                "id": r["id"],
                "kind": r["kind"],
                "line_no": r["po_line_no"],
                "old_value": r["old_value"],
                "new_value": r["new_value"],
                "what": _describe(dict(r), db),
                "reason": r["reason"],
                "source": r["source"],
                "status": r["status"],
                "within_tolerance": r["within_tolerance"],
                "waiting_for": name_of(db, named[0]) if r["status"] == "waiting_approval" else None,
                "decision_note": r["decision_note"],
                "version_made": r["version_made"],
                "at": r["created_at"].isoformat(),
                "can_decide": (r["status"] == "proposed" and may_decide)
                or (r["status"] == "waiting_approval" and me is not None and me in named),
            }
        )
    return out
