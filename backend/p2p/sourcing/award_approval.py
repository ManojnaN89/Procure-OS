"""Award approval of a period RFQ (task 12.2, D45, C1): the buyer proposes, the band's approver decides.

Selecting suppliers on a period RFQ makes an award decision instead of final awards: the award rows wait as 'proposed'
and the RFQ as 'award_proposed'. Its value (with GST, the same basis as an order's band) names the approver from the
`po` approval rows (OP-1, interim: `approver_for`); never an automatic band and never the buyer. Only that person (or
their deputy) decides, and never anyone who proposed the award, entered one of its quotes or confirmed its sending.
Before the approval runs, its figures are checked again (quotes still the latest, not expired, suppliers still usable):
a change makes the award stale and the buyer selects again. Approving makes a draft rate contract per supplier and
queues the award letters and regret notes, once. Nothing is ordered or committed: a contract buys only once the
supplier has accepted it and a second person has activated it, through a request.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.agents import capabilities, recommendations
from p2p.approvals import rules as approval_rules
from p2p.approvals.workflow import org_code_of
from p2p.formulas import period as figures
from p2p.formulas.calendar import business_today
from p2p.formulas.money import format_inr, format_qty, round_money
from p2p.masters import regions
from p2p.orders import selection_rule
from p2p.platform import audit, events, notifications, outbox, permissions, proposals, tasks, worker, workflows
from p2p.platform.sod import Actor

ACTION = "rfq.award_approve"
KIND = "award"  # the recommendation kind and the basis registered with workflows
REFRESH = "award.refresh"
NOTE_JOB = "award_note"
TASK = "award_approval"
SERVICE = Actor("service", "award_approval")


class AwardError(Exception):
    def __init__(self, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Proposed:
    decision_id: int
    proposal_id: int
    approver: str
    approver_name: str
    value_total: Decimal


# ---- who approves (OP-1, NEEDS OWNER: the interim rule, in one place) ------------------------------------------


def approver_for(db: Session, value: Decimal, on: date, buyer: str | None) -> tuple[approval_rules.ApprovalRule, Any]:
    """The `po` rule for the award's value on the date, moved up while its band approves automatically (an award is
    always a person's decision), and the person it names, never the buyer (C1); a deputy may act."""
    rule = approval_rules.find_rule(db, "po", value, on)
    while not rule.roles:
        rule = approval_rules.find_rule(db, "po", rule.max_value + Decimal("0.01"), on)
    steps = approval_rules.resolve_chain(
        db, rule.roles, requester=None, department=None, on_date=on, sources=rule.sources,
        exclude=frozenset({buyer} if buyer else set()),
    )  # fmt: skip
    return rule, steps[-1]


# ---- reading ---------------------------------------------------------------------------------------------------


def _name(db: Session, sql: str, key: str | None) -> str:
    found = db.execute(text(sql), {"k": key}).scalar() if key else None
    return str(found) if found else str(key or "")


def person(db: Session, code: str | None) -> str:
    return _name(db, "select name from org_user where code = :k", code)


def user_name(db: Session, user_id: str | None) -> str:
    if not user_id or not str(user_id).isdigit():
        return str(user_id or "")
    return _name(db, "select display_name from user_profile where user_id = cast(:k as int)", str(user_id))


def waiting(db: Session, rfq_no: str, *, lock: bool = False) -> dict[str, Any] | None:
    sql = "select * from award_decision where rfq_no = :n and status = 'waiting'" + (" for update" if lock else "")  # noqa: S608
    row = db.execute(text(sql), {"n": rfq_no}).mappings().first()
    return dict(row) if row else None


def _waiting_or_refuse(db: Session, rfq_no: str) -> dict[str, Any]:
    if db.execute(text("select 1 from rfq where no = :n"), {"n": rfq_no}).first() is None:
        raise AwardError(f"{rfq_no} does not exist", 404)
    decision = waiting(db, rfq_no)
    if decision is None:
        raise AwardError(f"no award of {rfq_no} is waiting for a decision")
    return decision


def latest(db: Session, rfq_no: str) -> dict[str, Any] | None:
    row = db.execute(
        text("select * from award_decision where rfq_no = :n order by round desc limit 1"), {"n": rfq_no}
    ).mappings().first()  # fmt: skip
    return dict(row) if row else None


def _states(db: Session, location: str | None, supplier: str) -> tuple[str, str]:
    loc = db.execute(text("select gstin, state_code from location where code = :c"), {"c": location}).first()
    gstin = (loc[0] if loc else None) or db.execute(
        text("select gstin from gst_registration where is_default order by gstin limit 1")
    ).scalar()
    place = (str(gstin)[:2] if gstin else None) or (loc[1] if loc else None)
    sup = db.execute(text("select state_code, gstin from supplier where code = :c"), {"c": supplier}).first()
    state = (sup[0] if sup else None) or (str(sup[1])[:2] if sup and sup[1] else None)
    if not state or not place:
        raise AwardError(f"the state of {supplier} or of the delivery place is unknown, so the GST cannot be split")
    return str(state), str(place)


def _parts(db: Session, rfq_no: str, decision_id: int) -> list[dict[str, Any]]:
    """The awarded parts of a decision with what the value and the contract need."""
    return [
        dict(r)
        for r in db.execute(
            text(
                "select a.rfq_line_no, a.supplier_code, a.quote_no, a.qty, a.price, q.revision, q.valid_until, "
                "q.payment_terms_code, q.delivery_terms, ql.gst_rate, ql.delivery_days, l.item_code, l.description, "
                "l.uom from award a join quote q on q.no = a.quote_no join quote_line ql on ql.quote_no = a.quote_no "
                "and ql.rfq_line_no = a.rfq_line_no join rfq_line l on l.rfq_no = a.rfq_no and l.line_no = a.rfq_line_no "
                "where a.rfq_no = :n and a.decision_id = :d order by a.rfq_line_no, a.supplier_code"
            ),
            {"n": rfq_no, "d": decision_id},
        ).mappings()
    ]


def value_of(db: Session, location: str | None, parts: list[dict[str, Any]]) -> figures.AwardValue:
    """The award's value: each supplier's parts with GST by its state and the delivery place's (formulas/period.py)."""
    by_supplier: dict[str, list[figures.AwardPart]] = defaultdict(list)
    for p in parts:
        by_supplier[p["supplier_code"]].append(figures.AwardPart(p["qty"], p["price"], p["gst_rate"]))
    total = figures.NOTHING
    for supplier, mine in sorted(by_supplier.items()):
        total = total.plus(figures.award_value(mine, *_states(db, location, supplier)))
    return total


# ---- the figures behind the recommendation, worked out again before the approval ---------------------------------


def _engine_figures(db: Session, rfq: dict[str, Any], parts: list[dict[str, Any]], on: date) -> dict[str, Any]:
    from p2p.orders import quotes  # noqa: PLC0415 - orders build on sourcing

    comp = quotes.comparison(db, str(rfq["no"]), on)
    offers = {(ln.rfq_line_no, o.supplier_code): (o.rank, ln.qualifying) for ln in comp.lines for o in ln.offers}
    value = value_of(db, rfq["delivery_location"], parts)
    lines = []
    for p in parts:
        rank, of = offers.get((p["rfq_line_no"], p["supplier_code"]), (None, 0))
        lines.append(
            {
                "line": p["rfq_line_no"], "item": p["item_code"], "supplier": p["supplier_code"], "quote": p["quote_no"],
                "revision": p["revision"], "qty": format_qty(p["qty"]), "net_price": str(p["price"]),
                "taxable": str(round_money(p["qty"] * p["price"])),
                "rank": rank, "qualifying": of,
            }
        )  # fmt: skip
    ranking = {
        str(ln.rfq_line_no): [{"supplier": o.supplier_code, "rank": o.rank, "cost": str(o.cost)} for o in ln.offers if o.rank]
        for ln in comp.lines
    }  # fmt: skip
    return {
        "lines": lines, "ranking": ranking, "value_taxable": str(value.taxable), "cgst": str(value.cgst),
        "sgst": str(value.sgst), "igst": str(value.igst), "value_total": str(value.total), "basis": comp.basis,
    }  # fmt: skip


def _problems(db: Session, rfq: dict[str, Any], parts: list[dict[str, Any]], on: date) -> list[str]:
    """Why the award cannot be approved as it stands (Rev s12 #16, 17)."""
    problems = []
    for p in parts:
        newest = db.execute(
            text("select max(revision) from quote where rfq_no = :r and supplier_code = :s"),
            {"r": rfq["no"], "s": p["supplier_code"]},
        ).scalar()
        if newest != p["revision"]:
            problems.append(f"{p['supplier_code']} sent a newer quote (revision {newest}); it is ranked again")
        if p["valid_until"] is not None and p["valid_until"] < on:
            problems.append(f"{p['supplier_code']}'s quote expired on {p['valid_until']:%d %b %Y}")
        status = db.execute(text("select status from supplier where code = :c"), {"c": p["supplier_code"]}).scalar()
        if status not in ("active", "prospect"):
            problems.append(f"{p['supplier_code']} is {status}")
        elif not regions.approved_at(db, p["supplier_code"], str(rfq["category_code"]), rfq["delivery_location"], on):
            problems.append(f"{p['supplier_code']} is no longer approved for {rfq['category_code']} here")
    return list(dict.fromkeys(problems))


@workflows.basis(KIND)
def _basis(db: Session, rfq_no: str) -> workflows.Basis:
    rfq = dict(db.execute(text("select * from rfq where no = :n"), {"n": rfq_no}).mappings().one())
    decision = waiting(db, rfq_no)
    if decision is None:
        return workflows.Basis(workflows.hash_of({}), {}, [f"no award of {rfq_no} is waiting"])
    parts = _parts(db, rfq_no, int(decision["id"]))
    on = business_today()
    found = _engine_figures(db, rfq, parts, on)
    return workflows.Basis(workflows.hash_of(found), found, _problems(db, rfq, parts, on))


# ---- the note the approver reads (section 7.3: fixed words, engine figures only) --------------------------------


def template_note(
    db: Session, rfq: dict[str, Any], buyer: str, found: dict[str, Any], reason: str | None, method: str
) -> str:
    where = regions.names(db).get(str(rfq["delivery_location"]), str(rfq["delivery_location"]))
    words = [
        f"{buyer} proposes to award {rfq['no']} for {rfq['period_from']:%d %b %Y} to {rfq['period_to']:%d %b %Y} "
        f"at {where}. "
    ]
    basis = "weighted score" if method == "weighted" else "total cost"
    uoms = {int(r[0]): (r[1], r[2]) for r in db.execute(
        text("select line_no, uom, description from rfq_line where rfq_no = :n"), {"n": rfq["no"]}
    )}  # fmt: skip
    for ln in found["lines"]:
        uom, item = uoms.get(int(ln["line"]), ("", ""))
        supplier = _name(db, "select coalesce(trade_name, legal_name) from supplier where code = :k", ln["supplier"])
        rank = f"ranked {ln['rank']} of {ln['qualifying']}" if ln["rank"] else f"one of {ln['qualifying']}"
        words.append(
            f"Line {ln['line']}, {item}: {supplier} at {format_inr(Decimal(ln['net_price']))} per {uom} for "
            f"{ln['qty']} {uom}, {format_inr(Decimal(ln['taxable']))} before GST; {rank} qualifying quotes on {basis}. "
        )
    words.append(f"Award value {format_inr(Decimal(found['value_total']))} with GST.")
    if reason and reason.strip():
        words.append(f" Reason: {reason.strip()}")
    return "".join(words)


# ---- proposing ---------------------------------------------------------------------------------------------------


def propose(
    db: Session,
    actor: Actor,
    rfq_no: str,
    chosen: dict[int, list[tuple[Any, Decimal]]],
    *,
    reason: str | None,
    overridden: list[int],
    as_of: date,
    regret_notes: bool = True,
    tenant: str | None = None,
) -> Proposed:
    """Record the buyer's selection as an award waiting for the band's approver (called by orders/quotes.award)."""
    rfq = dict(db.execute(text("select * from rfq where no = :n for update"), {"n": rfq_no}).mappings().one())
    if actor.kind != "user":
        raise AwardError("only a person proposes the award of a period RFQ")
    buyer_code = org_code_of(db, int(actor.id))
    rnd = int(db.execute(text("select coalesce(max(round), 0) + 1 from award_decision where rfq_no = :n"),
                         {"n": rfq_no}).scalar_one())  # fmt: skip
    decision_id = int(
        db.execute(
            text(
                "insert into award_decision (rfq_no, round, status, proposed_by, reason, value_taxable, cgst, sgst, igst, "
                "value_total, regret_notes) values (:n, :r, 'waiting', :by, :why, 0, 0, 0, 0, 0, :regret) returning id"
            ),
            {"n": rfq_no, "r": rnd, "by": actor.id, "why": reason, "regret": regret_notes},
        ).scalar_one()
    )
    rule_label = selection_rule.rule_for(db, as_of)
    for line_no, picked in chosen.items():
        for offer, qty in picked:
            db.execute(
                text(
                    "insert into award (rfq_no, rfq_line_no, supplier_code, quote_no, qty, price, reason, overridden, "
                    "by_user, split, selection_rule, conditional, awarded_on, status, decision_id) values (:r, :l, :s, "
                    ":q, :qty, :p, :why, :o, :by, :split, :rule, :cond, :on, 'proposed', :d)"
                ),
                {
                    "r": rfq_no, "l": line_no, "s": offer.supplier_code, "q": offer.quote_no, "qty": qty,
                    "p": offer.net_price, "why": reason, "o": line_no in overridden, "by": actor.id,
                    "split": len(picked) > 1, "rule": rule_label.label, "cond": offer.supplier_status == "prospect",
                    "on": as_of, "d": decision_id,
                },
            )  # fmt: skip
    parts = _parts(db, rfq_no, decision_id)
    value = value_of(db, rfq["delivery_location"], parts)
    rule, step = approver_for(db, value.total, as_of, buyer_code)
    found = _engine_figures(db, rfq, parts, as_of)
    buyer_name = user_name(db, actor.id)
    note = template_note(db, rfq, buyer_name, found, reason, rule_label.method)
    db.execute(
        text(
            "update award_decision set value_taxable = :t, cgst = :c, sgst = :s, igst = :i, value_total = :v, "
            "rule_code = :rc, rule_version = :rv, approver = :a, delegate = :dl, note = :note, note_source = 'template' "
            "where id = :d"
        ),
        {
            "t": value.taxable, "c": value.cgst, "s": value.sgst, "i": value.igst, "v": value.total, "rc": rule.code,
            "rv": rule.version, "a": step.approver, "dl": step.delegate, "note": note, "d": decision_id,
        },
    )  # fmt: skip
    db.execute(text("update rfq set status = 'award_proposed' where no = :n"), {"n": rfq_no})
    rec_id = recommendations.record(
        db, capability="sourcing", kind=KIND, business_key=rfq_no, summary=note,
        evidence=[{"kind": "comparison", "rfq": rfq_no, "quotes": sorted({p["quote_no"] for p in parts})}],
        engine_figures=found, actor=actor, directed_by=int(actor.id),
        rule_versions={"selection": rule_label.label, "po": f"{rule.code} v{rule.version}"},
    )  # fmt: skip
    approver_name = person(db, step.approver)
    audit.record(
        db, actor, "award.propose", "rfq", rfq_no,
        after={
            "decision": decision_id, "round": rnd, "value_taxable": value.taxable, "cgst": value.cgst,
            "sgst": value.sgst, "igst": value.igst, "value_total": value.total, "rule": rule.code,
            "rule_version": rule.version, "approver": step.approver, "delegate": step.delegate,
            "regret_notes": regret_notes, "awards": found["lines"],
        },
        reason=reason, rule_version=f"{rule.code} v{rule.version}",
    )  # fmt: skip
    pid = proposals.propose(
        db, actor, action=ACTION,
        summary=f"Approve the award of {rfq_no} ({format_inr(value.total)} with GST), proposed by {buyer_name}",
        payload={
            "rfq_no": rfq_no, "decision_id": decision_id, "approver": step.approver, "delegate": step.delegate,
            "tenant": tenant, recommendations.KEY: rec_id,
        },
        required_permission="po.approve", value=value.total,
    )  # fmt: skip
    db.execute(
        text("update award_decision set proposal_id = :p, recommendation_id = :r where id = :d"),
        {"p": pid, "r": rec_id, "d": decision_id},
    )
    recommendations.link_proposal(db, rec_id, pid)
    db.commit()
    if tenant:
        _task_for_approver(db, tenant, rfq_no, step, value.total, buyer_name)
        if capabilities.is_on(db, "sourcing"):
            worker.enqueue(tenant, NOTE_JOB, {"decision_id": decision_id}, key=f"award_note:{decision_id}")
    return Proposed(decision_id, pid, step.approver, approver_name, value.total)


def _task_for_approver(db: Session, tenant: str, rfq_no: str, step: Any, value: Decimal, buyer: str) -> None:
    for code in (step.delegate, step.approver):
        login = notifications.login_of(db, tenant, code)
        if login:
            tasks.create_task(
                db, tenant, kind=TASK, title=f"Approve the award of {rfq_no} ({format_inr(value)})", assignee=login,
                sla_hours=24, payload={"rfq_no": rfq_no, "link": f"#/rfqs/{rfq_no}", "proposed_by": buyer},
            )  # fmt: skip
            return


# ---- who may decide ----------------------------------------------------------------------------------------------


def makers(db: Session, rfq_no: str, decision: dict[str, Any]) -> set[str]:
    """Logins that may never decide the award (C1): who proposed it, entered one of its quotes or confirmed its sending."""
    who = {str(decision["proposed_by"])}
    who |= {str(r[0]) for r in db.execute(text("select distinct entered_by from quote where rfq_no = :n"), {"n": rfq_no})
            if r[0] is not None}  # fmt: skip
    confirmed = db.execute(text("select confirmed_by from rfq where no = :n"), {"n": rfq_no}).scalar()
    return who | ({str(confirmed)} if confirmed is not None else set())


def check_decider(db: Session, rfq_no: str, decision: dict[str, Any], user_id: int) -> None:
    """Only the named approver or their deputy, and never a maker (raises PermissionDenied)."""
    if str(user_id) in makers(db, rfq_no, decision):
        raise permissions.PermissionDenied(
            "whoever proposed the award, entered its quotes or confirmed its sending does not decide it (C1)"
        )
    if org_code_of(db, user_id) not in {decision["approver"], decision["delegate"]} - {None}:
        raise permissions.PermissionDenied(f"the award of {rfq_no} waits for {person(db, decision['approver'])}")


@proposals.guard(ACTION)
def _guard(db: Session, payload: dict[str, Any], actor: Actor) -> None:
    decision = waiting(db, str(payload["rfq_no"]))
    if decision is None or int(decision["id"]) != int(payload["decision_id"]):
        raise AwardError(f"no award of {payload['rfq_no']} is waiting for a decision")
    if actor.kind != "user":
        raise permissions.PermissionDenied("an award is always decided by a person")
    check_decider(db, str(payload["rfq_no"]), decision, int(actor.id))


# ---- deciding ----------------------------------------------------------------------------------------------------


def _reopen(db: Session, decision: dict[str, Any], status: str, by: str | None, note: str) -> None:
    """The decision ends without awards: its rows take the status, the RFQ is out again for a new selection."""
    db.execute(
        text(
            "update award_decision set status = :s, decided_by = :by, decided_at = now(), decision_note = :n "
            "where id = :d"
        ),
        {"s": status, "by": by, "n": note, "d": decision["id"]},
    )
    award_status = {"stale": "withdrawn"}.get(status, status)
    db.execute(text("update award set status = :s where decision_id = :d"), {"s": award_status, "d": decision["id"]})
    db.execute(text("update rfq set status = 'sent' where no = :n"), {"n": decision["rfq_no"]})


def _tell_buyer(db: Session, tenant: str | None, decision: dict[str, Any], title: str, kind: str) -> None:
    if not tenant or not str(decision["proposed_by"]).isdigit():
        return
    tasks.create_task(
        db, tenant, kind=kind, title=title, assignee=int(decision["proposed_by"]), sla_hours=24,
        payload={"rfq_no": decision["rfq_no"], "link": f"#/rfqs/{decision['rfq_no']}", "decision": decision["id"]},
    )  # fmt: skip


def go_stale(db: Session, tenant: str | None, decision: dict[str, Any], reason: str) -> None:
    """The award's basis changed: no letter, no contract; the buyer selects again. Commits."""
    _reopen(db, decision, "stale", None, reason)
    audit.record(db, SERVICE, "award.stale", "rfq", decision["rfq_no"], after={"decision": decision["id"]},
                 reason=reason)  # fmt: skip
    db.commit()
    if decision["proposal_id"]:
        proposals.withdraw(db, int(decision["proposal_id"]), None, f"the award is stale: {reason}")
    _tell_buyer(db, tenant, decision, f"Select again on {decision['rfq_no']}: {reason}", "award_refresh")


def _stale_now(db: Session, rfq_no: str, decision: dict[str, Any]) -> str | None:
    """Work the figures out again; a change marks the recommendation stale (event award.refresh) and returns why."""
    rec_id = decision["recommendation_id"]
    rec = recommendations.get(db, int(rec_id)) if rec_id else None
    if rec is None or rec["status"] != "proposed":
        return None
    got = workflows.revalidate(db, KIND, rfq_no, str(rec["basis_hash"]))
    if isinstance(got, workflows.Stale):
        workflows.refresh(db, int(rec_id), got)
        return got.reason
    return None


def approve(db: Session, tenant: str, rfq_no: str, user_id: int, note: str | None = None) -> dict[str, Any]:
    """The named approver approves the waiting award (through its proposal, so every check is one code path)."""
    decision = _waiting_or_refuse(db, rfq_no)
    if not permissions.can_anywhere(db, user_id, "po.approve"):
        raise permissions.PermissionDenied("role does not allow po.approve")
    check_decider(db, rfq_no, decision, user_id)
    if why := _stale_now(db, rfq_no, decision):
        go_stale(db, tenant, decision, why)
        raise AwardError(f"the award is stale: {why}")
    from p2p.masters import documents_and_contracts as contracts  # noqa: PLC0415 - masters build on sourcing

    rfq = dict(db.execute(text("select * from rfq where no = :n"), {"n": rfq_no}).mappings().one())
    for supplier, parts in _by_supplier(_parts(db, rfq_no, int(decision["id"]))).items():
        if clash := contracts.award_clash(db, supplier, parts, rfq):
            db.rollback()
            raise AwardError(clash)
    db.rollback()
    try:
        proposals.approve(db, int(decision["proposal_id"]), user_id, note=note)
    except proposals.ProposalError as exc:
        raise AwardError(str(exc)) from None
    execution, result = db.execute(
        text("select execution, result from proposal where id = :i"), {"i": decision["proposal_id"]}
    ).one()
    if execution != "ok":
        raise AwardError(f"the award was not approved: {(result or {}).get('error', 'see the log')}")
    return {"status": "approved", **dict(result or {})}


def _by_supplier(parts: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for p in parts:
        out[p["supplier_code"]].append(p)
    return dict(sorted(out.items()))


@proposals.executor(ACTION)
def _approved(db: Session, payload: dict[str, Any], actor: Actor) -> dict[str, Any]:
    """Approve in one transaction: decision and awards approved, the RFQ awarded, a draft contract per supplier, the
    letters queued; the mail leaves after the commit, once."""
    from p2p.masters import documents_and_contracts as contracts  # noqa: PLC0415 - masters build on sourcing
    from p2p.sourcing import award_letters  # noqa: PLC0415 - the letters read the decision this module writes

    rfq_no = str(payload["rfq_no"])
    decision = waiting(db, rfq_no, lock=True)
    if decision is None or int(decision["id"]) != int(payload["decision_id"]):
        raise AwardError(f"no award of {rfq_no} is waiting for a decision")
    rfq = dict(db.execute(text("select * from rfq where no = :n for update"), {"n": rfq_no}).mappings().one())
    db.execute(
        text("update award_decision set status = 'approved', decided_by = :by, decided_at = now() where id = :d"),
        {"by": actor.id, "d": decision["id"]},
    )
    db.execute(text("update award set status = 'approved' where decision_id = :d"), {"d": decision["id"]})
    db.execute(text("update rfq set status = 'awarded' where no = :n"), {"n": rfq_no})
    db.execute(
        text(
            "update period_demand_line set status = 'awarded' where id in (select l.period_line_id from rfq_line l "
            "join award a on a.rfq_no = l.rfq_no and a.rfq_line_no = l.line_no where a.decision_id = :d)"
        ),
        {"d": decision["id"]},
    )
    made: dict[str, str] = {}
    for supplier, parts in _by_supplier(_parts(db, rfq_no, int(decision["id"]))).items():
        made[supplier] = contracts.from_award(db, rfq, decision, supplier, parts, int(actor.id))
    queued = award_letters.queue(db, str(payload.get("tenant") or outbox.tenant_of(db)), rfq, decision, made)
    audit.record(
        db, actor, "award.approve", "rfq", rfq_no,
        after={"decision": decision["id"], "value_total": decision["value_total"], "rule": decision["rule_code"],
               "rule_version": decision["rule_version"], "contracts": list(made.values()), "letters": len(queued)},
        rule_version=f"{decision['rule_code']} v{decision['rule_version']}",
    )  # fmt: skip
    db.commit()
    outbox.send_now(outbox.tenant_of(db), queued, engine=db.get_bind().engine)
    return {"contracts": list(made.values()), "letters": len(queued)}


def reject(db: Session, tenant: str, rfq_no: str, user_id: int, reason: str) -> None:
    """The named approver rejects the award (a reason is needed): no letter, no contract; the buyer may select again."""
    if not (reason or "").strip():
        raise AwardError("a reason is needed to reject", 422)
    decision = _waiting_or_refuse(db, rfq_no)
    check_decider(db, rfq_no, decision, user_id)
    try:
        proposals.reject(db, int(decision["proposal_id"]), user_id, reason.strip())
    except proposals.ProposalError as exc:
        raise AwardError(str(exc)) from None
    _reopen(db, decision, "rejected", str(user_id), reason.strip())
    audit.record(db, Actor("user", str(user_id)), "award.reject", "rfq", rfq_no, after={"decision": decision["id"]},
                 reason=reason.strip())  # fmt: skip
    db.commit()
    _tell_buyer(db, tenant, decision, f"The award of {rfq_no} was rejected: {reason.strip()}", "award_refresh")


def withdraw(db: Session, rfq_no: str, user_id: int, reason: str) -> None:
    """The buyer who proposed the award takes it back before a decision (a reason is needed)."""
    if not (reason or "").strip():
        raise AwardError("a reason is needed to withdraw", 422)
    decision = _waiting_or_refuse(db, rfq_no)
    if str(decision["proposed_by"]) != str(user_id):
        raise permissions.PermissionDenied("only the buyer who proposed the award withdraws it")
    if decision["recommendation_id"]:
        recommendations.withdraw(db, int(decision["recommendation_id"]), reason.strip())
    _reopen(db, decision, "withdrawn", str(user_id), reason.strip())
    audit.record(db, Actor("user", str(user_id)), "award.withdraw", "rfq", rfq_no, after={"decision": decision["id"]},
                 reason=reason.strip())  # fmt: skip
    db.commit()
    if decision["proposal_id"]:
        proposals.withdraw(db, int(decision["proposal_id"]), user_id, f"withdrawn by the buyer: {reason.strip()}")


@events.route(REFRESH)
def _refresh(db: Session, tenant: str, ev: events.EventRow) -> None:
    """A stale award found on the proposals screen: the decision follows, the buyer selects again."""
    rec_id = ev.payload.get("recommendation_id")
    row = db.execute(
        text("select * from award_decision where recommendation_id = :r and status = 'waiting'"), {"r": rec_id}
    ).mappings().first()  # fmt: skip
    if row is not None:
        go_stale(db, tenant, dict(row), str(ev.payload.get("reason") or "the figures changed"))


# ---- for the screens -----------------------------------------------------------------------------------------------


def as_json(db: Session, rfq_no: str, user_id: int) -> dict[str, Any] | None:
    """The latest award decision of the RFQ, with what the person looking may do."""
    d = latest(db, rfq_no)
    if d is None:
        return None
    can = False
    if d["status"] == "waiting":
        try:
            check_decider(db, rfq_no, d, user_id)
            can = permissions.can_anywhere(db, user_id, "po.approve")
        except permissions.PermissionDenied:
            can = False
    letters = [
        {"supplier_code": r[0], "kind": r[1], "contract_code": r[2], "queued_at": r[3].isoformat()}
        for r in db.execute(
            text(
                "select supplier_code, kind, contract_code, queued_at from award_letter where decision_id = :d "
                "order by kind, supplier_code"
            ),
            {"d": d["id"]},
        )
    ]
    made = [str(r[0]) for r in db.execute(
        text("select code from contract where award_decision_id = :d order by code"), {"d": d["id"]}
    )]  # fmt: skip
    return {
        "id": d["id"], "round": d["round"], "status": d["status"], "value_taxable": str(d["value_taxable"]),
        "cgst": str(d["cgst"]), "sgst": str(d["sgst"]), "igst": str(d["igst"]), "value_total": str(d["value_total"]),
        "rule": f"{d['rule_code']} v{d['rule_version']}" if d["rule_code"] else None, "approver": d["approver"],
        "approver_name": person(db, d["approver"]), "delegate": d["delegate"], "note": d["note"],
        "note_source": d["note_source"], "reason": d["reason"], "proposed_by": user_name(db, d["proposed_by"]),
        "can_decide": can, "can_withdraw": d["status"] == "waiting" and str(d["proposed_by"]) == str(user_id),
        "decision_note": d["decision_note"], "regret_notes": d["regret_notes"], "letters": letters, "contracts": made,
    }  # fmt: skip
