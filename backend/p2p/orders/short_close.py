"""Short-close by risk (R1, D-13, DF-26, AT-14): the buyer asks, the rule decides who may close, the need goes back.

A close below the automatic limit with nothing outstanding is the buyer's own decision. Anything else is a proposal
for the person the `short_close` rule names (never the asker or a maker of the order). Nothing on the order changes
and the supplier is not told while it waits. On execution the balance is read again under the order's lock: a changed
balance is stale and must be asked again (OP-4).
"""

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.approvals import rules as approval_rules
from p2p.approvals.workflow import name_of, org_code_of
from p2p.formulas import closure as closure_rules
from p2p.formulas.calendar import business_today
from p2p.formulas.gst import Registration
from p2p.formulas.money import format_inr, format_qty
from p2p.formulas.receiving import order_status
from p2p.formulas.words import count
from p2p.orders import changes, closure
from p2p.orders import workflow as po_workflow
from p2p.platform import audit, notifications, proposals, tasks
from p2p.platform.sod import Actor, user

ACTION = "po.short_close"
KIND = "po_short_close"
SLA_HOURS = 18


class ShortCloseError(changes.ChangeError):
    pass


class StaleShortClose(ShortCloseError):
    """The balance changed while the short-close waited."""


def _order(db: Session, po_no: str) -> dict[str, Any]:
    row = db.execute(text("select * from po where no = :n for update"), {"n": po_no}).mappings().first()
    if row is None:
        raise changes.ChangeError(f"{po_no} does not exist")
    return dict(row)


def _open_lines(db: Session, po_no: str) -> list[dict[str, Any]]:
    return [
        dict(r)
        for r in db.execute(
            text(
                "select line_no, item_code, description, uom, qty, received_qty, coalesce(short_closed_qty, 0) "
                "as short_closed_qty, unit_price, gst_rate, status, request_no, request_line_no "
                "from po_line where po_no = :n and status in ('open', 'part_received') order by line_no"
            ),
            {"n": po_no},
        ).mappings()
    ]


def _registration(value: str | None) -> Registration:
    try:
        return Registration(str(value or "regular"))
    except ValueError:
        return Registration.REGULAR


def _states(db: Session, po: dict[str, Any]) -> tuple[str, str, Registration]:
    supplier = db.execute(
        text("select coalesce(state_code, left(gstin, 2)), gst_reg_type from supplier where code = :c"),
        {"c": po["supplier_code"]},
    ).first()
    place = db.execute(
        text("select coalesce(state_code, left(gstin, 2)) from location where code = :c"),
        {"c": po.get("ship_to_location")},
    ).scalar()
    return (
        str(supplier[0]) if supplier and supplier[0] else "",
        str(place or ""),
        _registration(supplier[1] if supplier else None),
    )


def _facts(db: Session, po: dict[str, Any], lines: list[dict[str, Any]]) -> closure_rules.ShortCloseFacts:
    supplier_state, place_state, registration = _states(db, po)
    facts = [
        closure_rules.ShortLine(
            int(ln["line_no"]),
            str(ln["item_code"] or ln["description"] or ""),
            ln["uom"],
            Decimal(ln["qty"]),
            Decimal(ln["received_qty"]),
            Decimal(ln["short_closed_qty"]),
            Decimal(ln["unit_price"]),
            Decimal(ln["gst_rate"]),
            supplier_state,
            place_state,
            registration,
        )
        for ln in lines
    ]
    return closure_rules.short_close_facts(facts, _flags(db, po, lines))


def _flags(db: Session, po: dict[str, Any], lines: list[dict[str, Any]]) -> list[closure_rules.Flag]:
    out: list[closure_rules.Flag] = []
    chosen = {int(ln["line_no"]) for ln in lines}
    seen: set[tuple[str, date | None]] = set()
    for ln in lines:
        for mr in db.execute(
            text(
                "select m.need_qty, coalesce(m.reserved_qty, 0) + coalesce(m.issued_qty, 0) as covered, "
                "m.need_by, m.item_code, m.uom, coalesce(mr.order_no, mr.no) as ref "
                "from material_request_buy b join material_request_line m on m.id = b.mr_line_id "
                "join material_request mr on mr.no = m.mr_no "
                "where m.status = 'open' and b.request_no = :r and b.request_line_no = :l "
                "and m.need_by is not null order by m.need_by, mr.no"
            ),
            {"r": ln["request_no"], "l": ln["request_line_no"]},
        ).mappings():
            short = Decimal(mr["need_qty"]) - Decimal(mr["covered"])
            if short <= 0 or (str(mr["ref"]), mr["need_by"]) in seen:
                continue
            seen.add((str(mr["ref"]), mr["need_by"]))
            qty = min(short, Decimal(ln["qty"]) - Decimal(ln["received_qty"]) - Decimal(ln["short_closed_qty"]))
            if qty <= 0:
                continue
            out.append(
                closure_rules.Flag(
                    "unmet_need",
                    closure_rules.unmet_need_words(
                        str(ln["description"] or mr["item_code"] or ln["item_code"] or ""),
                        qty,
                        mr["uom"] or ln["uom"],
                        mr["need_by"],
                        str(mr["ref"]),
                    ),
                )
            )
    advance = db.execute(
        text(
            "select payment_no, gross - coalesce(used_gross, 0) from advance where po_no = :p "
            "and paid_on is not null and gross - coalesce(used_gross, 0) > 0 order by payment_no limit 1"
        ),
        {"p": po["no"]},
    ).first()
    if advance:
        out.append(
            closure_rules.Flag("advance_open", closure_rules.advance_words(str(advance[0]), Decimal(advance[1])))
        )
    transit = db.execute(
        text(
            "select coalesce(sum(greatest(d.qty - pl.received_qty, 0)), 0), max(n.shipped_on) "
            "from dispatch_note_line d join dispatch_note n on n.id = d.note_id "
            "join po_line pl on pl.po_no = n.po_no and pl.line_no = d.po_line_no "
            "where n.po_no = :p and d.po_line_no = any(:l)"
        ),
        {"p": po["no"], "l": sorted(chosen)},
    ).first()
    if transit and Decimal(transit[0]) > 0 and transit[1] is not None:
        out.append(closure_rules.Flag("in_transit", closure_rules.in_transit_words(Decimal(transit[0]), transit[1])))
    return out


def _band(db: Session, value: Decimal, on_date: Any) -> approval_rules.ApprovalRule:
    """The short-close rule; with no rows of its own, the first PO band with a person (3.2)."""
    try:
        return approval_rules.find_rule(db, "short_close", value, on_date)
    except approval_rules.ApprovalRuleError:
        return _po_person_band(db, value, on_date)


def _po_person_band(db: Session, value: Decimal, on_date: Any) -> approval_rules.ApprovalRule:
    seen: set[str] = set()
    at = value
    for _ in range(12):
        rule = approval_rules.find_rule(db, "po", at, on_date)
        if not rule.is_automatic and rule.code not in seen:
            return rule
        seen.add(rule.code)
        if rule.max_value is None:
            break
        at = rule.max_value + Decimal("0.01")
    raise approval_rules.ApprovalRuleError(
        "no short-close rule and no purchase-order rule names a person to close a balance"
    )


def _band_above(db: Session, rule: approval_rules.ApprovalRule, on_date: Any) -> approval_rules.ApprovalRule | None:
    """The first short-close band above this one with a person; None when there is none."""
    if rule.max_value is None:
        return None
    try:
        above = approval_rules.find_rule(db, "short_close", rule.max_value + Decimal("0.01"), on_date)
    except approval_rules.ApprovalRuleError:
        return None
    seen = {rule.code}
    while above.code not in seen:
        if not above.is_automatic:
            return above
        if above.max_value is None:
            return None
        seen.add(above.code)
        try:
            above = approval_rules.find_rule(db, "short_close", above.max_value + Decimal("0.01"), on_date)
        except approval_rules.ApprovalRuleError:
            return None
    return None


def _approver(
    db: Session, po: dict[str, Any], asker: str | None, rule: approval_rules.ApprovalRule, on_date: Any
) -> approval_rules.Step:
    never = po_workflow.makers(db, po) | ({asker} if asker else set())
    try:
        steps = approval_rules.resolve_chain(
            db, rule.roles, requester=asker, department=None, on_date=on_date,
            location=po.get("ship_to_location"), exclude=frozenset(never),
        )  # fmt: skip
    except approval_rules.ApprovalRuleError:
        steps = []
    if not steps:
        raise approval_rules.ApprovalRuleError(
            "everyone the short-close rule names made this order or asked to close it; "
            "the rule has to name someone else"
        )
    return steps[0]


def _limit_words(rule: approval_rules.ApprovalRule, risk: str) -> str:
    limit = rule.max_value if rule.is_automatic else rule.min_value - Decimal("0.01")
    return f"value above {format_inr(limit)}" if risk == "high" else "value within the automatic limit"


def _written(
    db: Session, po: dict[str, Any], facts: closure_rules.ShortCloseFacts, rule: approval_rules.ApprovalRule, risk: str
) -> str:
    first = facts.flags[0].words if facts.flags else _limit_words(rule, risk)
    return f"Close {po['no']}'s balance: {format_inr(facts.value)}, {count(facts.line_count, 'line')}; {first}"[:200]


def _chosen(db: Session, po_no: str, wanted: list[int] | None) -> list[dict[str, Any]]:
    lines = _open_lines(db, po_no)
    if wanted is None:
        return lines
    by_no = {int(ln["line_no"]): ln for ln in lines}
    missing = sorted(set(wanted) - set(by_no))
    if missing:
        raise changes.InputError("line " + ", ".join(str(n) for n in missing) + " is not still open on this order")
    return [by_no[n] for n in sorted(set(wanted))]


def preview(db: Session, po_no: str, lines: list[int] | None = None) -> dict[str, Any]:
    """What would close: lines, balances, value, risk, flags, the rule and who would decide (R1.2)."""
    found = db.execute(text("select * from po where no = :n"), {"n": po_no}).mappings().first()
    if found is None:
        raise changes.ChangeError(f"{po_no} does not exist")
    po = dict(found)
    if po["status"] not in changes.CLOSABLE:
        raise changes.ChangeError(
            f"{po_no} is {po['status']}; only an order that is with the supplier can have its balance closed"
        )
    chosen = _chosen(db, po_no, lines)
    facts = _facts(db, po, chosen)
    if not facts.lines:
        raise changes.ChangeError("nothing is still to come on the lines chosen")
    today = business_today()
    band = _band(db, facts.value, today)
    risk = closure_rules.risk("auto" if band.is_automatic else "person", facts.flags)
    rule, approver = band, None
    if risk == "high":
        if band.is_automatic:
            rule = _band_above(db, band, today) or _po_person_band(db, facts.value, today)
        asker = None  # a preview only shows who would decide
        approver = _approver(db, po, asker, rule, today)
    return {
        "lines": [dict(row) for row in facts.lines],
        "value": facts.value,
        "risk": risk,
        "flags": [{"code": f.code, "words": f.words} for f in facts.flags],
        "rule": {"code": rule.code, "version": rule.version},
        "approver": name_of(db, approver.approver) if approver else None,
    }


def ask(
    db: Session,
    tenant: str | None,
    po_no: str,
    user_id: int,
    reason: str | None,
    lines: list[int] | None = None,
) -> dict[str, Any]:
    """The buyer asks to close the balance: low risk runs at once, anything else waits for its person (R1.1-R1.6)."""
    why = changes._reason(reason)
    today = business_today()
    actor = user(user_id)
    asker = org_code_of(db, user_id)
    po = _order(db, po_no)
    if po["status"] not in changes.CLOSABLE:
        db.rollback()
        raise changes.ChangeError(
            f"{po_no} is {po['status']}; only an order that is with the supplier can have its balance closed"
        )
    received = db.execute(
        text("select coalesce(sum(received_qty), 0) from po_line where po_no = :n and status <> 'cancelled'"),
        {"n": po_no},
    ).scalar_one()
    if Decimal(received) <= 0:
        db.rollback()
        raise changes.ChangeError(f"nothing has been received against {po_no}: cancel it instead")
    waiting = db.execute(
        text("select id, approver from po_short_close where po_no = :p and status = 'waiting'"), {"p": po_no}
    ).first()
    if waiting:
        db.rollback()
        raise changes.ChangeError(f"a short-close of {po_no} waits for {name_of(db, waiting[1])}")
    chosen = _chosen(db, po_no, lines)
    facts = _facts(db, po, chosen)
    if not facts.lines:
        db.rollback()
        raise changes.ChangeError("nothing is still to come on the lines chosen")
    try:
        band = _band(db, facts.value, today)
    except approval_rules.ApprovalRuleError as exc:
        db.rollback()
        raise changes.ChangeError(str(exc)) from None
    risk = closure_rules.risk("auto" if band.is_automatic else "person", facts.flags)
    rule = band
    step = None
    if risk == "high":
        if band.is_automatic:
            rule = _band_above(db, band, today) or _po_person_band(db, facts.value, today)
        try:
            step = _approver(db, po, asker, rule, today)
        except approval_rules.ApprovalRuleError as exc:
            db.rollback()
            raise changes.ChangeError(str(exc)) from None
    row_id = db.execute(
        text(
            "insert into po_short_close (po_no, lines, value, risk, flags, rule_code, rule_version, approver, reason, "
            "asked_by, status) values (:p, cast(:l as jsonb), :v, :r, cast(:f as jsonb), :rc, :rv, :a, :why, :by, "
            "'waiting') returning id"
        ),
        {
            "p": po_no,
            "l": json.dumps([dict(row) for row in facts.lines], default=str),
            "v": facts.value,
            "r": risk,
            "f": json.dumps([f.words for f in facts.flags]),
            "rc": rule.code,
            "rv": rule.version,
            "a": step.approver if step else None,
            "why": why,
            "by": asker or f"user:{user_id}",
        },
    ).scalar_one()
    audit.record(
        db, actor, "po.short_close_asked", "po", po_no,
        after={"short_close": row_id, "value": str(facts.value), "risk": risk,
               "flags": [f.words for f in facts.flags], "approver": step.approver if step else None},
        reason=why,
    )  # fmt: skip
    if risk == "low":
        db.commit()
        closed = _execute(db, int(row_id), actor, tenant)
        return {"status": "closed", "short_close_id": row_id, "risk": risk, "value": facts.value,
                "flags": [], **closed}  # fmt: skip
    if step is None:  # a high risk close always has a person
        raise ShortCloseError("a high-risk close needs a person to decide it")
    summary = _written(db, po, facts, rule, risk)
    pid = proposals.propose(
        db,
        actor,
        action=ACTION,
        summary=summary,
        payload={
            "po_no": po_no,
            "short_close_id": int(row_id),
            "approver": step.approver,
            "delegate": step.delegate,
            "tenant": tenant,
        },
        required_permission="po.approve",
        value=facts.value,
    )
    db.execute(text("update po_short_close set proposal_id = :p where id = :i"), {"p": pid, "i": row_id})
    db.commit()
    if tenant:
        who = notifications.login_of(db, tenant, step.delegate or step.approver)
        if who:
            above = _band_above(db, rule, today)
            chain: list[int] = []
            if above:
                try:
                    chain.append(
                        notifications.login_of(db, tenant, _approver(db, po, asker, above, today).approver) or 0
                    )
                except approval_rules.ApprovalRuleError:
                    chain = []
            tasks.create_task(
                db, tenant, kind=KIND, title=summary, assignee=who, sla_hours=SLA_HOURS,
                escalation_chain=[c for c in chain if c], payload={
                    "po_no": po_no, "short_close_id": int(row_id), "proposal_id": pid,
                    "lines": [dict(row) for row in facts.lines], "flags": [f.words for f in facts.flags],
                }, actor=actor,
            )  # fmt: skip
    return {
        "status": "waiting_approval",
        "short_close_id": row_id,
        "risk": risk,
        "value": facts.value,
        "flags": [{"code": f.code, "words": f.words} for f in facts.flags],
        "approver": name_of(db, step.approver),
        "proposal_id": pid,
    }


def _stale_words(line_no: int, then: Decimal, now: Decimal) -> str:
    return (
        f"the balance of line {line_no} changed since it was asked "
        f"({format_qty(then)} then, {format_qty(now)} now): ask again"
    )


def _current_balances(db: Session, po_no: str) -> dict[int, Decimal]:
    return {
        int(r[0]): max(Decimal(r[1]) - Decimal(r[2]) - Decimal(r[3]), Decimal(0))
        for r in db.execute(
            text(
                "select line_no, qty, received_qty, coalesce(short_closed_qty, 0) from po_line "
                "where po_no = :n and status in ('open', 'part_received', 'short_closed')"
            ),
            {"n": po_no},
        )
    }


def _check_stale(db: Session, payload: dict[str, Any], actor: Actor) -> dict[str, Any]:
    row = (
        db.execute(
            text("select * from po_short_close where id = :i and po_no = :p for update"),
            {"i": payload["short_close_id"], "p": payload["po_no"]},
        )
        .mappings()
        .first()
    )
    if row is None:
        raise proposals.ProposalError("the short-close this proposal was made for no longer exists")
    if row["status"] != "waiting":
        raise proposals.ProposalError(f"the short-close is {row['status']}")
    now = _current_balances(db, str(row["po_no"]))
    for line in row["lines"]:
        line_no = int(line["line"])
        then = Decimal(str(line["balance"]))
        if now.get(line_no, Decimal(0)) != then:
            db.execute(text("update po_short_close set status = 'stale', decided_by = :b, decided_at = now(), "
                            "note = :n where id = :i"),
                       {"b": actor.id, "n": _stale_words(line_no, then, now.get(line_no, Decimal(0))),
                        "i": row["id"]})  # fmt: skip
            audit.record(db, actor, "po.short_close_stale", "po", row["po_no"],
                         before={"line": line_no, "balance": str(then)},
                         after={"balance": str(now.get(line_no, Decimal(0)))})  # fmt: skip
            db.commit()
            raise StaleShortClose(_stale_words(line_no, then, now.get(line_no, Decimal(0))))
    return dict(row)


def _execute(db: Session, row_id: int, actor: Actor, tenant: str | None) -> dict[str, Any]:
    row = db.execute(text("select * from po_short_close where id = :i for update"), {"i": row_id}).mappings().first()
    if row is None:
        raise ShortCloseError(f"short-close {row_id} does not exist")
    if row["status"] == "closed":
        db.rollback()
        return {"closed": row["po_no"]}
    if row["status"] != "waiting":
        db.rollback()
        raise ShortCloseError(f"the short-close is {row['status']}")
    po = _order(db, str(row["po_no"]))
    now = _current_balances(db, po["no"])
    for line in row["lines"]:
        if now.get(int(line["line"]), Decimal(0)) != Decimal(str(line["balance"])):
            raise StaleShortClose("the balance changed since it was asked: ask again")
    closed_words: list[str] = []
    for line in row["lines"]:
        balance = Decimal(str(line["balance"]))
        db.execute(
            text(
                "update po_line set status = 'short_closed', short_closed_qty = coalesce(short_closed_qty, 0) + :b "
                "where po_no = :p and line_no = :l"
            ),
            {"b": balance, "p": po["no"], "l": int(line["line"])},
        )
        closed_words.append(str(line.get("item") or f"line {line['line']}"))
    statuses = [
        str(r[0]) for r in db.execute(text("select status from po_line where po_no = :n"), {"n": po["no"]}).all()
    ]
    fresh = order_status(statuses) or po["status"]
    db.execute(text("update po set status = :s where no = :n"), {"s": fresh, "n": po["no"]})
    db.execute(
        text("update po_short_close set status = 'closed', decided_by = :b, decided_at = now() where id = :i"),
        {"b": actor.id, "i": row_id},
    )
    audit.record(
        db, actor, "po.close_balance", "po", po["no"],
        before={"status": po["status"]},
        after={"risk": row["risk"], "value": str(row["value"]), "flags": row["flags"],
               "approved_by": actor.id, "lines": closed_words},
        reason=row["reason"],
    )  # fmt: skip
    db.commit()
    _after_close(db, tenant, po, row, closed_words)
    return {"closed": po["no"]}


def _after_close(db: Session, tenant: str | None, po: dict[str, Any], row: Any, closed_words: list[str]) -> None:
    """After the commit: tell the supplier, tell the people, run the store check (the need sent back), then check the
    order's closure."""
    body = (
        f"We are closing the balance of order {po['no']}; no further supply is expected on:\n"
        + "\n".join(closed_words)
        + f"\nReason: {row['reason']}"
    )
    changes.tell_supplier(db, po, f"Purchase order {po['no']}: balance closed", body)
    changes.tell_people(db, tenant, po["no"], f"The balance of order {po['no']} was closed", row["reason"],
                        buyer=po["buyer"])  # fmt: skip
    try:
        changes._check_again(db, tenant, po["no"], f"the balance of {po['no']} was closed")  # noqa: SLF001
    except Exception:  # noqa: BLE001 - the close stands even if the store check cannot run now
        db.rollback()
    try:
        _tell_need(db, tenant, po, row)
    except Exception:  # noqa: BLE001 - a notice never undoes the close
        db.rollback()
    closure.check(db, po["no"], audit.SYSTEM, tenant=tenant)


def _tell_need(db: Session, tenant: str | None, po: dict[str, Any], row: Any) -> None:
    """The requester of a flagged need hears that the balance was closed and that the store check raised it again."""
    if not tenant:
        return
    for flag in row["flags"] or []:
        if "production still needs" not in str(flag):
            continue
        item = db.execute(
            text("select item_code from po_line where po_no = :p order by line_no limit 1"), {"p": po["no"]}
        ).scalar()
        raised = db.execute(
            text(
                "select coalesce(sum(l.need_qty), 0) from material_request_line l "
                "join material_request_buy b on b.mr_line_id = l.id "
                "join material_request m on m.no = l.mr_no "
                "where m.status = 'open' and l.status = 'open' and l.item_code = :i"
            ),
            {"i": item},
        ).scalar()
        requester = db.execute(
            text(
                "select r.requester from material_request_line l "
                "join material_request_buy b on b.mr_line_id = l.id "
                "join request r on r.no = b.request_no "
                "where l.status = 'open' and l.item_code = :i order by r.created_at desc limit 1"
            ),
            {"i": item},
        ).scalar()
        who = notifications.login_of(db, tenant, requester)
        if who:
            notifications.notify(
                db, tenant, who, "short_close_need",
                f"{item}: the balance on {po['no']} was closed",
                f"The store check has raised {format_qty(raised)} again.",
                link=f"#/purchase-orders/{po['no']}",
            )  # fmt: skip


# ---- the proposal's guard and executor ------------------------------------------------------------


@proposals.guard(ACTION)
def _named(db: Session, payload: dict[str, Any], actor: Actor) -> None:
    code = org_code_of(db, int(actor.id)) if str(actor.id).isdigit() else None
    if code not in (payload.get("approver"), payload.get("delegate")):
        from p2p.platform.permissions import PermissionDenied  # noqa: PLC0415

        raise PermissionDenied(f"this short-close is for {name_of(db, payload.get('approver'))} to decide")
    try:
        _check_stale(db, payload, actor)
    except StaleShortClose as exc:
        raise proposals.StaleError(str(exc)) from None


@proposals.executor(ACTION)
def _run(db: Session, payload: dict[str, Any], actor: Actor) -> dict[str, Any]:
    return _execute(db, int(payload["short_close_id"]), actor, payload.get("tenant"))


@proposals.closer(ACTION)
def _closed(db: Session, payload: dict[str, Any], actor: Actor, status: str, note: str) -> None:
    row = (
        db.execute(
            text("select po_no, status, asked_by from po_short_close where id = :i for update"),
            {"i": payload.get("short_close_id")},
        )
        .mappings()
        .first()
    )
    if row is None or row["status"] != "waiting":
        db.rollback()
        return
    fresh = "rejected" if status == "rejected" else "withdrawn"
    db.execute(
        text("update po_short_close set status = :s, decided_by = :b, decided_at = now(), note = :n where id = :i"),
        {"s": fresh, "b": actor.id, "n": note, "i": payload.get("short_close_id")},
    )
    audit.record(db, actor, f"po.short_close_{fresh}", "po", row["po_no"], reason=note)
    db.commit()
    tenant = payload.get("tenant")
    if tenant and fresh == "rejected":
        who = notifications.login_of(db, tenant, row["asked_by"])
        if who:
            notifications.notify(
                db, tenant, who, "short_close_rejected",
                f"{name_of(db, actor.id)} did not close the balance of {row['po_no']}", note or "",
                link=f"#/purchase-orders/{row['po_no']}",
            )  # fmt: skip


def withdraw(db: Session, po_no: str, short_close_id: int, user_id: int) -> dict[str, Any]:
    """The asker takes back a waiting short-close (R1.8)."""
    actor = user(user_id)
    code = org_code_of(db, user_id)
    row = (
        db.execute(
            text(
                "select id, status, asked_by, proposal_id from po_short_close where id = :i and po_no = :p for update"
            ),
            {"i": short_close_id, "p": po_no},
        )
        .mappings()
        .first()
    )
    if row is None:
        raise changes.ChangeError(f"short-close {short_close_id} does not exist")
    if row["status"] != "waiting":
        raise changes.ChangeError(f"the short-close is {row['status']}, not waiting")
    if code != row["asked_by"]:
        raise changes.ChangeError("only the person who asked can take it back")
    db.execute(
        text("update po_short_close set status = 'withdrawn', decided_by = :b, decided_at = now() where id = :i"),
        {"b": code, "i": short_close_id},
    )
    audit.record(db, actor, "po.short_close_withdrawn", "po", po_no)
    db.commit()
    if row["proposal_id"]:
        proposals.withdraw(db, int(row["proposal_id"]), user_id, "the buyer took the short-close back")
    db.execute(
        text("update task set status = 'cancelled' where kind = :k and status in ('open', 'overdue') "
             "and payload ->> 'short_close_id' = :i"),
        {"k": KIND, "i": str(short_close_id)},
    )  # fmt: skip
    db.commit()
    return {"status": "withdrawn"}


def waiting(db: Session, po_no: str) -> dict[str, Any] | None:
    row = (
        db.execute(
            text(
                "select id, value, risk, flags, reason, approver, asked_by from po_short_close "
                "where po_no = :p and status = 'waiting'"
            ),
            {"p": po_no},
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


def timestamp() -> datetime:
    return datetime.now(UTC)
