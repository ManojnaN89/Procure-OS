"""The sourcing capability's records (task 11b.2, block 6): the engine's figures and fixed words, a model only rewords.

Five kinds of `recommendation` (capability `sourcing`), each written by the engine whether or not the capability is on
(OP-22), with its figures, evidence, rule versions and a template summary:
- `path_case` (key `<request>/<line>`): a contract fits but the market is lower beyond every threshold (D40 steps
  4-5): path B, ask for quotes, is recommended; the buyer chooses (sourcing/service.decide_path);
- `rfq_draft` (key `<rfq>`): why these suppliers are asked, linked to the `rfq.send` proposal; a changed basis makes
  the confirmation stale and a fresh draft is written;
- `ranking` (key `<rfq>`): the comparison in words, once the RFQ has closed or everyone asked has answered; the
  buyer's selection decides it;
- `negotiation_brief` (key `<rfq>/<line>`): L1 above the last price paid or the market beyond the thresholds, with a
  target from the engine (formulas/negotiation.py); the buyer marks it used or dismissed;
- `review_note` (key `review:<task>`): a strategic review's figures in words.
When the capability is on, the job `sourcing_note` asks the model to reword the summary (ai_steps/sourcing_note.py);
nothing here sends mail, sets a price or decides anything.
"""

import json
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.agents import capabilities, recommendations
from p2p.formulas import negotiation, path
from p2p.formulas.calendar import business_today
from p2p.formulas.money import format_inr, format_qty
from p2p.formulas.scope import Scope
from p2p.formulas.words import count
from p2p.masters import regions
from p2p.platform import audit, events, outbox, worker, workflows
from p2p.platform.sod import Actor
from p2p.sourcing import price_history, price_review

CAPABILITY = "sourcing"
JOB = "sourcing_note"
ACTOR = Actor("service", "sourcing")
_PENDING = "sourcing_notes_pending"
TWO = Decimal("0.01")


def _pct(value: Any) -> str:
    return f"{Decimal(str(value)).quantize(TWO, rounding=ROUND_HALF_UP)}"


def _day(value: Any) -> str:
    d = value if isinstance(value, date) else date.fromisoformat(str(value))
    return f"{d:%d %b %Y}"


def _plain(figures: dict[str, Any]) -> dict[str, Any]:
    return dict(json.loads(json.dumps(figures, default=str)))


def _open(db: Session, kind: str, key: str) -> dict[str, Any] | None:
    row = db.execute(
        text(
            "select * from recommendation where kind = :k and business_key = :b and status = 'proposed' "
            "order by id desc limit 1"
        ),
        {"k": kind, "b": key},
    ).mappings().first()  # fmt: skip
    return dict(row) if row else None


def _write(
    db: Session,
    kind: str,
    key: str,
    summary: str,
    figures: dict[str, Any],
    evidence: list[dict[str, Any]],
    rule_versions: dict[str, Any] | None = None,
) -> int:
    """Record (or keep, when its figures are the same) the open record of this kind and key. Does not commit."""
    figures = _plain(figures)
    held = _open(db, kind, key)
    if held is not None and held["basis_hash"] == workflows.hash_of(figures) and held["summary"] == summary:
        return int(held["id"])
    rec_id = recommendations.record(
        db, capability=CAPABILITY, kind=kind, business_key=key, summary=summary, evidence=evidence,
        engine_figures=figures, actor=ACTOR, rule_versions=rule_versions,
    )  # fmt: skip
    audit.record(db, ACTOR, "recommendation.create", "recommendation", rec_id, after={"kind": kind, "key": key})
    db.info.setdefault(_PENDING, []).append(rec_id)
    return rec_id


def flush(db: Session) -> None:
    """Commit, then queue the model's rewording of the records just written (only while the capability is on)."""
    db.commit()
    ids: list[int] = db.info.pop(_PENDING, [])
    if not ids or not capabilities.is_on(db, CAPABILITY):
        return
    tenant = outbox.tenant_of(db)
    for rec_id in ids:
        worker.enqueue(tenant, JOB, {"recommendation_id": rec_id}, key=f"{JOB}:{rec_id}")


def _withdraw(db: Session, kind: str, key: str, reason: str) -> None:
    held = _open(db, kind, key)
    if held is not None:
        recommendations.withdraw(db, int(held["id"]), reason)


def _decide(db: Session, kind: str, key: str, actor: Actor, *, accepted: bool, note: str | None) -> None:
    """A person's act decides the open record (a system act leaves it open). Does not commit."""
    held = _open(db, kind, key)
    if held is not None and actor.kind == "user" and str(actor.id).isdigit():
        recommendations.decide(db, int(held["id"]), int(actor.id), accepted=accepted, note=note)


def _name(db: Session, sql: str, key: Any) -> str:
    found = db.execute(text(sql), {"k": key}).scalar()
    return str(found) if found else str(key)


def _supplier(db: Session, code: str) -> str:
    return _name(db, "select coalesce(trade_name, legal_name) from supplier where code = :k", code)


# ---- path A or B (D40, D43) ----------------------------------------------------------------------------------------


def path_figures(db: Session, request_no: str, line_no: int, on: date) -> tuple[dict[str, Any] | None, list[str]]:
    """The A/B test of a line sourced on a contract, as it stands now: its figures (None when no contract price of the
    line is in force) and the reasons it is no longer a case."""
    d = db.execute(
        text(
            "select d.contract_code, d.supplier_code, d.price, d.price_record_id, d.path_case, rl.item_code, rl.qty, "
            "rl.uom, rl.need_by, rl.status, r.priority, coalesce(i.name, rl.free_text) as name, i.category_code, "
            "pr.status as pr_status, pr.valid_from, pr.valid_to, pr.lead_days, pr.location_code, pr.region_code, "
            "pr.price as pr_price, exists (select 1 from po_line p where p.request_no = d.request_no and "
            "p.request_line_no = d.line_no and p.status <> 'cancelled') as ordered "
            "from sourcing_decision d join request_line rl on rl.request_no = d.request_no and rl.line_no = d.line_no "
            "join request r on r.no = d.request_no left join item i on i.code = rl.item_code "
            "left join price_record pr on pr.id = d.price_record_id where d.request_no = :n and d.line_no = :l"
        ),
        {"n": request_no, "l": line_no},
    ).mappings().first()  # fmt: skip
    if d is None or d["price_record_id"] is None:
        return None, [f"line {line_no} of {request_no} is not on a contract"]
    problems = []
    if d["ordered"] or d["status"] != "sourcing":
        problems.append(f"line {line_no} of {request_no} is no longer open")
    if d["pr_status"] != "active" or d["valid_from"] > on or (d["valid_to"] is not None and d["valid_to"] < on):
        problems.append(f"the price of contract {d['contract_code']} is no longer in force")
    try:
        rule = price_review.rule_for(db, d["category_code"], on)
    except price_review.ReviewError as exc:  # no threshold rule in force: no market claim, no case
        return None, [str(exc)]
    scope = regions.price_scope(dict(d))
    found = price_review.counting_markets(db, str(d["item_code"]), str(d["uom"]), scope, on, rule.evidence_max_age_days)
    rfq_days = int(
        db.execute(text("select value from org_setting where key = 'default_rfq_response_days'")).scalar() or 5
    )
    price = Decimal(d["pr_price"])
    t = path.test(
        price, [p for p, _ in found.prices], Decimal(d["qty"]), rule, priority=d["priority"], need_by=d["need_by"],
        on=on, lead_days=int(d["lead_days"] or 0), rfq_days=rfq_days,
    )  # fmt: skip
    if not t.case:
        problems.append(f"no case any more: {t.why}")
    lowest = min(found.prices, key=lambda m: (m[0], m[1]["id"])) if found.prices else None
    figures = {
        "request": request_no, "line": line_no, "item": d["item_code"], "item_name": d["name"],
        "qty": format_qty(d["qty"]), "uom": d["uom"], "need_by": d["need_by"], "priority": d["priority"],
        "contract": d["contract_code"], "supplier": d["supplier_code"], "price_record": d["price_record_id"],
        "contract_price": price, "market": t.gap.market, "gap": t.gap.gap_inr, "gap_pct": t.gap.gap_pct,
        "saving": t.gap.saving, "sources": t.gap.sources, "tests": t.gap.tests, "urgent": t.urgent, "case": t.case,
        "benchmark": lowest[1]["id"] if lowest else None, "market_source": lowest[1]["source_name"] if lowest else None,
        "market_date": lowest[1]["effective_date"] if lowest else None, "rule": rule.version,
    }  # fmt: skip
    return _plain(figures), problems


def path_summary(db: Session, f: dict[str, Any]) -> str:
    uom = f["uom"] or ""
    return (
        f"Line {f['line']}, {f['item_name']}: contract {f['contract']} with {_supplier(db, f['supplier'])} at "
        f"{format_inr(Decimal(f['contract_price']))} per {uom} fits, but the market is lower: "
        f"{format_inr(Decimal(f['market']))} per {uom} ({f['market_source']}, {_day(f['market_date'])}), a gap of "
        f"{format_inr(Decimal(f['gap']))} ({_pct(f['gap_pct'])}%), {format_inr(Decimal(f['saving']))} on {f['qty']} "
        f"{uom}. Recommended: path B, ask for quotes. Path A buys on the contract; the contract stays valid either way."
    )


def is_path_case(db: Session, request_no: str, line_no: int, on: date) -> bool:
    figures, _ = path_figures(db, request_no, line_no, on)
    return bool(figures and figures["case"])


def write_path_case(db: Session, request_no: str, line_no: int, on: date) -> int:
    """The A/B case's record (recommending B). Does not commit."""
    figures, _ = path_figures(db, request_no, line_no, on)
    assert figures is not None
    return _write(
        db, "path_case", f"{request_no}/{line_no}", path_summary(db, figures), figures,
        [{"kind": "price_record", "id": figures["price_record"]}, {"kind": "benchmark", "id": figures["benchmark"]}],
        {"price_threshold": figures["rule"]},
    )  # fmt: skip


@workflows.basis("path_case")
def _path_basis(db: Session, key: str) -> workflows.Basis:
    request_no, _, line = key.rpartition("/")
    figures, problems = path_figures(db, request_no, int(line), business_today())
    waiting = db.execute(
        text("select path_case from sourcing_decision where request_no = :n and line_no = :l"),
        {"n": request_no, "l": int(line)},
    ).scalar()
    if waiting != "waiting":
        problems.append(f"no A/B case waits on line {line} of {request_no}")
    return workflows.Basis(workflows.hash_of(figures or {}), figures or {}, problems)


# ---- the RFQ draft (D42) --------------------------------------------------------------------------------------------


def draft_figures(db: Session, rfq_no: str, on: date) -> tuple[dict[str, Any], list[str]]:
    """Who the RFQ asks and why, who is left out, and the reasons it can no longer be sent as it is."""
    from p2p.sourcing import service  # noqa: PLC0415 - the service writes these records

    rfq = dict(db.execute(text("select * from rfq where no = :n"), {"n": rfq_no}).mappings().one())
    lines = [
        {"line": r["line_no"], "item": r["item_code"], "name": r["description"], "qty": format_qty(r["qty"]),
         "uom": r["uom"], "need_by": r["need_by"]}
        for r in db.execute(text("select * from rfq_line where rfq_no = :n order by line_no"), {"n": rfq_no}).mappings()
    ]  # fmt: skip
    items = [str(ln["item"]) for ln in lines if ln["item"]]
    eligible, notes = (
        service.eligible_suppliers(db, rfq["category_code"], items, on, rfq["delivery_location"])
        if rfq["category_code"]
        else ([], [])
    )
    order = {e.code: n for n, e in enumerate(eligible)}
    asked = db.execute(
        text(
            "select rs.supplier_code, s.status from rfq_supplier rs join supplier s on s.code = rs.supplier_code "
            "where rs.rfq_no = :n"
        ),
        {"n": rfq_no},
    ).all()
    invitees = []
    for code, status in sorted(asked, key=lambda r: (order.get(str(r[0]), len(order)), str(r[0]))):
        found = next((e for e in eligible if e.code == code), None)
        if status == "prospect":
            passes, why = True, "a prospect of the category, invited by the buyer"
        elif found is not None:
            passes = True
            why = count(found.history, "price") + " on file for these items" if found.history else "approved here"
        else:
            note = next((n for n in notes if n.startswith(f"{code} left out: ")), None)
            passes = False
            why = (
                note.split(": ", 1)[1]
                if note
                else (f"is {status}" if status != "active" else "no longer approved here")
            )
        invitees.append({"code": code, "name": _supplier(db, code), "why": why, "passes": passes})
    invited = {i["code"] for i in invitees}
    how_many = count(len(invitees), "supplier")
    left_out = [n.replace(" left out: ", ": ", 1) for n in notes if n.split(" ", 1)[0] not in invited]
    left_out += [f"{e.code}: not asked, {how_many} are asked" for e in eligible if e.code not in invited]
    left_out += [f"{i['code']}: will not be sent, {i['why']}" for i in invitees if not i["passes"]]
    figures = {
        "rfq": rfq_no, "request": rfq["request_no"], "lines": lines, "invitees": invitees, "left_out": left_out,
        "min_quotes": rfq["min_quotes"], "min_quotes_rule": rfq["min_quotes_rule"], "due_date": rfq["due_date"],
        "location": rfq["delivery_location"], "note": rfq["note"],
        "location_name": service.deliver_to(db, rfq),
    }  # fmt: skip
    problems = []
    if rfq["status"] not in ("draft", "sent"):  # a sent RFQ may still invite a prospect (D-03)
        problems.append(f"{rfq_no} is {rfq['status']}")
    if rfq["request_no"]:
        status = db.execute(text("select status from request where no = :n"), {"n": rfq["request_no"]}).scalar()
        if status != "approved":
            problems.append(f"request {rfq['request_no']} is {status}")
    return _plain(figures), problems


def draft_summary(f: dict[str, Any]) -> str:
    names = ", ".join(i["name"] for i in f["invitees"] if i["passes"])
    words = (
        f"{f['rfq']} asks {count(sum(1 for i in f['invitees'] if i['passes']), 'supplier')} for "
        f"{count(len(f['lines']), 'item')} delivered to {f['location_name']}, replies by {_day(f['due_date'])}: "
        f"{names}. Left out: {'; '.join(f['left_out']) or 'nobody'}. At least {f['min_quotes']} quotes are wanted "
        f"({f['min_quotes_rule']})."
    )
    return words + (f" Note: {f['note']}." if f.get("note") else "")


def write_rfq_draft(db: Session, rfq_no: str) -> int:
    """The RFQ draft's record, for the buyer who confirms the send (block 5 OP-19: not an approval). Does not commit."""
    figures, _ = draft_figures(db, rfq_no, business_today())
    return _write(
        db, "rfq_draft", rfq_no, draft_summary(figures), figures,
        [{"kind": "rfq", "rfq": rfq_no, "suppliers": [i["code"] for i in figures["invitees"]]}],
        {"min_quotes": figures["min_quotes_rule"]},
    )  # fmt: skip


@workflows.basis("rfq_draft")
def _draft_basis(db: Session, rfq_no: str) -> workflows.Basis:
    figures, problems = draft_figures(db, rfq_no, business_today())
    held = _open(db, "rfq_draft", rfq_no)
    if held is not None:  # an invitee that passed when the draft was written and no longer does
        before = {i["code"]: i["passes"] for i in (held["engine_figures"] or {}).get("invitees", [])}
        for i in figures["invitees"]:
            if before.get(i["code"]) and not i["passes"]:
                problems.append(f"{i['code']} {i['why']}")
    words = ["the RFQ draft is stale: " + "; ".join(problems)] if problems else []
    return workflows.Basis(workflows.hash_of(figures), figures, words)


@events.route("rfq_draft.refresh")
def _refresh_draft(db: Session, tenant: str, ev: events.EventRow) -> None:
    """A stale RFQ draft: a fresh one and a fresh sending proposal, while the RFQ can still go out."""
    from p2p.sourcing import service  # noqa: PLC0415

    rec = recommendations.get(db, int(ev.payload["recommendation_id"]))
    if rec is None:
        return
    rfq_no = str(rec["business_key"])
    rfq = db.execute(text("select status, request_no from rfq where no = :n"), {"n": rfq_no}).first()
    if rfq is None or rfq[0] not in ("draft", "sent"):
        return
    waiting = db.execute(
        text("select 1 from rfq_supplier where rfq_no = :n and invited_at is null and send_error is null limit 1"),
        {"n": rfq_no},
    ).first()
    if waiting is None:
        return
    if rfq[1] and db.execute(text("select status from request where no = :n"), {"n": rfq[1]}).scalar() != "approved":
        return
    service.confirm_send(db, rfq_no, ACTOR)


# ---- the ranking and the negotiation briefs -------------------------------------------------------------------------


def on_quote(db: Session, rfq_no: str) -> None:
    """After a quote, a decline or a closing: the ranking and briefs, once the RFQ has closed or everyone answered."""
    from p2p.orders import quotes  # noqa: PLC0415 - orders build on sourcing

    rfq = db.execute(text("select status, due_date, kind from rfq where no = :n"), {"n": rfq_no}).first()
    if rfq is None or rfq[0] != "sent":
        return
    today = business_today()
    comp = quotes.comparison(db, rfq_no, today)
    if comp.quotes_received == 0 or (rfq[1] >= today and comp.waiting_for):
        return
    write_ranking(db, rfq_no, comp, today)
    flush(db)


def ranking_summary(db: Session, comp: Any) -> str:
    words = []
    for line in comp.lines:
        words.append(f"Line {line.rfq_line_no}, {line.name}: {line.why}.")
        for o in line.offers:
            if o.flags:
                words.append(f"{o.supplier_name} left out: {'; '.join(o.flags)}.")
    words.append(f"{comp.quotes_received} of {comp.invited} invited suppliers quoted.")
    return " ".join(words)


def write_ranking(db: Session, rfq_no: str, comp: Any, on: date) -> int:
    """The comparison in words and its figures, then each line's brief. Does not commit."""
    figures = {
        "rfq": rfq_no, "rule": comp.rule, "basis": comp.basis, "min_quotes": comp.min_quotes,
        "quoted": comp.quotes_received, "invited": comp.invited,
        "lines": [
            {
                "line": ln.rfq_line_no, "name": ln.name, "recommended": ln.recommended, "cheapest": ln.cheapest,
                "qualifying": ln.qualifying, "why": ln.why,
                "offers": [
                    {"supplier": o.supplier_code, "quote": o.quote_no, "revision": o.revision, "net_price": o.net_price,
                     "cost": o.cost, "score": o.score, "score_parts": o.score_parts, "rank": o.rank, "flags": o.flags}
                    for o in ln.offers
                ],
            }
            for ln in comp.lines
        ],
    }  # fmt: skip
    rec_id = _write(
        db, "ranking", rfq_no, ranking_summary(db, comp), figures,
        [{"kind": "quotes", "quotes": sorted({o.quote_no for ln in comp.lines for o in ln.offers})}],
        {"selection": comp.rule},
    )  # fmt: skip
    for line in comp.lines:
        write_brief(db, rfq_no, line, on)
    return rec_id


def decide_ranking(db: Session, rfq_no: str, actor: Actor, *, accepted: bool, note: str | None) -> None:
    _decide(db, "ranking", rfq_no, actor, accepted=accepted, note=note)


def brief_figures(db: Session, rfq_no: str, line: Any, on: date) -> dict[str, Any] | None:
    """A line's brief figures, or None when it gets none (no single recommended offer, or nothing triggers)."""
    if line.recommended is None or line.split_recommended or line.awarded or not line.item_code:
        return None
    ranked = [o for o in line.offers if o.rank is not None and o.counts and o.compliant]
    l1 = next((o for o in ranked if o.supplier_code == line.recommended), None)
    if l1 is None:
        return None
    l2 = next((o for o in ranked if o.supplier_code != l1.supplier_code), None)
    rfq = db.execute(text("select category_code, delivery_location from rfq where no = :n"), {"n": rfq_no}).one()
    try:
        rule = price_review.rule_for(db, rfq[0], on)
    except price_review.ReviewError:
        return None
    paid = price_history.last_paid_at(db, line.item_code, rfq[1], line.uom, on)
    last = negotiation.Reference("last_paid", paid.price, paid.on, paid.po_no) if paid else None
    found = price_review.counting_markets(
        db, line.item_code, str(line.uom), Scope.of([rfq[1]]), on, rule.evidence_max_age_days
    )
    markets = [
        negotiation.Reference("market", p, date.fromisoformat(d["effective_date"]), str(d["source_name"]), int(d["id"]))
        for p, d in found.prices
    ]
    got = negotiation.brief(l1.net_price, line.qty, rule, last_paid=last, markets=markets)
    if got is None:
        return None
    return _plain({
        "rfq": rfq_no, "line": line.rfq_line_no, "item": line.item_code, "item_name": line.name,
        "qty": format_qty(line.qty), "uom": line.uom, "rule": rule.version,
        "l1": {"supplier": l1.supplier_code, "name": l1.supplier_name, "quote": l1.quote_no, "net_price": l1.net_price},
        "l2": {"supplier": l2.supplier_code, "name": l2.supplier_name, "net_price": l2.net_price} if l2 else None,
        "references": [
            {"kind": t.reference.kind, "price": t.reference.price, "on": t.reference.on, "ref": t.reference.ref,
             "benchmark": t.reference.ref_id, "gap": t.gap.gap_inr, "gap_pct": t.gap.gap_pct, "saving": t.gap.saving}
            for t in got.triggers
        ],
        "target": got.target, "saving_at_target": got.saving_at_target,
    })  # fmt: skip


def brief_summary(f: dict[str, Any]) -> str:
    uom = f["uom"] or ""
    refs = []
    for r in f["references"]:
        if r["kind"] == "last_paid":
            refs.append(
                f"the last price paid, {format_inr(Decimal(r['price']))} on {r['ref']} of {_day(r['on'])} "
                f"({_pct(r['gap_pct'])}% gap)"
            )
        else:
            refs.append(
                f"the market benchmark, {format_inr(Decimal(r['price']))} ({r['ref']}, {_day(r['on'])}; "
                f"{_pct(r['gap_pct'])}% gap)"
            )
    words = (
        f"{f['l1']['name']} is lowest on line {f['line']} ({f['item_name']}) at "
        f"{format_inr(Decimal(f['l1']['net_price']))} per {uom}, above {' and '.join(refs)}. Ask for "
        f"{format_inr(Decimal(f['target']))} per {uom}: {format_inr(Decimal(f['saving_at_target']))} less on "
        f"{f['qty']} {uom}."
    )
    if f.get("l2"):
        words += f" The next offer is {format_inr(Decimal(f['l2']['net_price']))} from {f['l2']['name']}."
    return words


def write_brief(db: Session, rfq_no: str, line: Any, on: date) -> int | None:
    key = f"{rfq_no}/{line.rfq_line_no}"
    figures = brief_figures(db, rfq_no, line, on)
    if figures is None:
        _withdraw(db, "negotiation_brief", key, "L1 is now within the thresholds")
        return None
    return _write(
        db, "negotiation_brief", key, brief_summary(figures), figures,
        [{"kind": "quote", "quote": figures["l1"]["quote"]},
         *({"kind": r["kind"], "ref": r["ref"], "benchmark": r["benchmark"]} for r in figures["references"])],
        {"price_threshold": figures["rule"]},
    )  # fmt: skip


def rerun_for_item(db: Session, item: str) -> None:
    """A benchmark of the item was verified: the briefs of its open RFQs are worked out again."""
    for (rfq_no,) in db.execute(
        text(
            "select distinct r.no from rfq r join rfq_line l on l.rfq_no = r.no where r.status = 'sent' "
            "and l.item_code = :i order by r.no"
        ),
        {"i": item},
    ).all():
        on_quote(db, str(rfq_no))


# ---- strategic review notes -----------------------------------------------------------------------------------------


def review_summary(title: str, p: dict[str, Any]) -> str:
    words = f"{title}: {p.get('reason')}."
    if p.get("market"):
        words += (
            f" Market {format_inr(Decimal(p['market']))} per unit; gap {format_inr(Decimal(p['gap_inr']))} "
            f"({_pct(p['gap_pct'])}%), {format_inr(Decimal(p['saving']))} at the relevant quantity."
        )
    allows = p.get("allows") or {}
    if p.get("contract"):
        until = f" to {_day(allows['valid_to'])}" if allows.get("valid_to") else ""
        left = (
            f" with {format_inr(Decimal(allows['ceiling_left']))} of its ceiling left"
            if allows.get("ceiling_left")
            else ""
        )
        words += f" {p['contract']} stays valid{until}{left}; a review never sets aside its obligations."
    return words


def write_review_note(db: Session, task_id: int, title: str, payload: dict[str, Any], rule: str | None) -> int:
    """The review's figures in words. Does not commit."""
    keep = (
        "trigger",
        "contract",
        "item",
        "scope",
        "reason",
        "market",
        "gap_inr",
        "gap_pct",
        "saving",
        "rule",
        "allows",
    )
    figures = {"task": task_id, "title": title, **{k: payload.get(k) for k in keep}}
    return _write(
        db, "review_note", f"review:{task_id}", review_summary(title, payload), figures,
        [{"kind": "task", "id": task_id}, {"kind": "evaluation", "id": payload.get("evaluation")}],
        {"price_threshold": rule} if rule else None,
    )  # fmt: skip


def decide_review_note(db: Session, task_id: int, actor: Actor, *, accepted: bool, note: str | None) -> None:
    _decide(db, "review_note", f"review:{task_id}", actor, accepted=accepted, note=note)


# ---- for the screens ------------------------------------------------------------------------------------------------


def as_json(rec: dict[str, Any] | None) -> dict[str, Any] | None:
    if rec is None:
        return None
    return {
        "id": rec["id"], "kind": rec["kind"], "key": rec["business_key"], "status": rec["status"],
        "summary": rec["summary"], "ai_note": rec.get("ai_note"), "figures": rec["engine_figures"],
    }  # fmt: skip


def latest(db: Session, kind: str, key: str) -> dict[str, Any] | None:
    """The newest record of this kind and key that is open or was decided (a superseded or stale one is history)."""
    row = db.execute(
        text(
            "select * from recommendation where kind = :k and business_key = :b "
            "and status in ('proposed', 'accepted', 'rejected', 'handed_over') order by id desc limit 1"
        ),
        {"k": kind, "b": key},
    ).mappings().first()  # fmt: skip
    return dict(row) if row else None


def for_rfq(db: Session, rfq_no: str) -> dict[str, Any]:
    briefs = [
        {"line": int(str(r["business_key"]).rpartition("/")[2]), **(as_json(dict(r)) or {})}
        for r in db.execute(
            text(
                "select * from recommendation where kind = 'negotiation_brief' and business_key like :p "
                "and status in ('proposed', 'accepted', 'rejected') order by business_key, id desc"
            ),
            {"p": f"{rfq_no}/%"},
        ).mappings()
    ]
    newest: dict[int, dict[str, Any]] = {}
    for b in briefs:  # newest first within a line
        newest.setdefault(b["line"], b)
    return {
        "rfq_draft": as_json(latest(db, "rfq_draft", rfq_no)),
        "ranking": as_json(latest(db, "ranking", rfq_no)),
        "briefs": list(newest.values()),
    }
