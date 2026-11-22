"""Supply promised too late for a need (rule Q17): the late-supply case for the buyer, one per order and material
request.

An evaluation reads every open material request line (or those of some item-locations), splits its cover by date
(requests/supply_dates.py) and works out what is at risk: what it still lacks that only late supply covers. The part at
risk is put on the late parts earliest first; each order whose part is at risk gets one open case per material request
(task `late_supply`, owner the order's buyer, else the category's, else the first buyer; escalation to the owner's
manager; due `late_supply_case_days` working days on the place's calendar). A later evaluation updates the case's lines,
never opens a second one, and closes it as `resolved` when nothing on it is at risk any more. The buyer closes it with
what was done: expedited, accepted late, or bought elsewhere (a note for the last two). Nothing here changes an order.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.approvals.workflow import name_of, org_code_of
from p2p.formulas import material as rules
from p2p.formulas.calendar import IST, business_today
from p2p.formulas.money import format_qty, round_money
from p2p.platform import audit, permissions, reference, settings, tasks
from p2p.platform.sod import user
from p2p.requests import demand_cases, supply_dates
from p2p.requests.reservation import Place, open_lines

log = logging.getLogger(__name__)
KIND = "late_supply"
DEFAULT_DAYS = "2"
OUTCOMES = ("expedited", "accepted_late", "bought_elsewhere")
NOTE_NEEDED = ("accepted_late", "bought_elsewhere")
ACTS = "po.create"  # who may close a case besides its owner
ZERO = Decimal(0)


class CaseError(demand_cases.CaseError):
    """The case cannot be closed: `status` is the HTTP answer (404, 403, 409, 422)."""


@dataclass
class Evaluated:
    """What one evaluation found: each line's cover by date, and the cases opened, updated and resolved."""

    covers: dict[int, rules.Cover] = field(default_factory=dict)
    dated: dict[int, supply_dates.LineCover] = field(default_factory=dict)
    opened: list[dict[str, Any]] = field(default_factory=list)
    updated: list[int] = field(default_factory=list)
    resolved: list[int] = field(default_factory=list)


def _now() -> datetime:
    """The business date at this time of day, or at noon when the business date is not the calendar's today (a pinned
    date): due dates come out the same whenever it runs."""
    real = datetime.now(IST)
    today = business_today()
    return real if real.date() == today else datetime.combine(today, time(12), tzinfo=IST)


def at_risk_parts(c: rules.Cover, dated: supply_dates.LineCover) -> list[tuple[rules.Part, Decimal]]:
    """The late parts that hold what is at risk, earliest first, with how much of each is at risk."""
    left, out = c.at_risk, []
    for p in sorted((p for p in dated.parts if p.date > dated.latest_arrival), key=lambda p: p.date):
        if left <= 0:
            break
        take = min(p.qty, left)
        left -= take
        out.append((p, take))
    return out


def evaluate(db: Session, tenant: str, places: list[Place] | None = None, *, today: date | None = None) -> Evaluated:
    """Work out what is at risk on the open lines (of these item-locations, else all) and open, update or resolve the
    late-supply cases. Commits. Never raises: a failure is logged and left to the next evaluation."""
    try:
        return _evaluate(db, tenant, places, today or business_today())
    except Exception:  # noqa: BLE001 - a case must never undo or hide what triggered it
        db.rollback()
        log.warning("could not evaluate late supply for %s", tenant, exc_info=True)
        return Evaluated()


def _evaluate(db: Session, tenant: str, places: list[Place] | None, today: date) -> Evaluated:
    wanted = set(places) if places is not None else None
    rows = [
        r
        for r in open_lines(db, items=sorted({i for i, _ in wanted}) if wanted else None)
        if (wanted is None or (r["item_code"], r["location_code"]) in wanted) and r.get("basis") != "net"
    ]
    found = Evaluated()
    found.dated = supply_dates.cover_by_date(db, rows, today)
    risky: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rows:
        dated = found.dated[r["id"]]
        c = rules.cover(r["need_qty"], r["issued_qty"], r["reserved_qty"], dated.timely, dated.late)
        found.covers[r["id"]] = c
        for part, qty in at_risk_parts(c, dated):
            if part.ref["doc_type"] != "po":
                continue  # nothing ordered yet: the buyer sees the date on the request or quotation
            case = risky.setdefault(
                (part.ref["doc_no"], r["mr_no"]),
                {"row": r, "ref": part.ref, "lines": [], "amount": ZERO, "buffer": dated.buffer_days},
            )
            case["lines"].append(
                {"mr_line_no": r["line_no"], "item_code": r["item_code"], "at_risk": format_qty(qty),
                 "promised": part.ref["asked"].isoformat(), "latest_arrival": dated.latest_arrival.isoformat(),
                 "need_by": r["need_by"].isoformat(), "po_line_no": part.ref["line_no"]}
            )  # fmt: skip
            case["amount"] += qty * (part.ref.get("price") or ZERO)
    scope = {(r["item_code"], r["location_code"]) for r in rows} if wanted is not None else None
    for held in db.execute(
        text(
            "select s.task_id, s.po_no, s.mr_no, s.location_code, s.lines from supply_risk s where s.closed_at is null "
            "order by s.task_id for update of s"
        )
    ).mappings():
        key = (held["po_no"], held["mr_no"])
        items = {(ln["item_code"], held["location_code"]) for ln in held["lines"]}
        new = risky.pop(key, None)
        if new is None:
            if scope is None or items <= scope:
                _resolve(db, int(held["task_id"]))
                found.resolved.append(int(held["task_id"]))
            continue
        lines = new["lines"] if scope is None else _merge(held["lines"], new["lines"], scope, held["location_code"])
        if lines != held["lines"]:
            db.execute(
                text("update supply_risk set lines = cast(:l as jsonb) where task_id = :t"),
                {"l": json.dumps(lines), "t": held["task_id"]},
            )
            audit.record(
                db, audit.SYSTEM, "supply_risk.update", "task", held["task_id"], before={"lines": held["lines"]},
                after={"lines": lines},
            )  # fmt: skip
            found.updated.append(int(held["task_id"]))
    db.commit()
    for (po_no, _mr_no), case in sorted(risky.items()):
        found.opened.append(_open(db, tenant, po_no, case))
    return found


def _merge(
    old: list[dict[str, Any]], new: list[dict[str, Any]], scope: set[Place], location: str
) -> list[dict[str, Any]]:
    """A scoped evaluation replaces the lines of the item-locations it looked at and keeps the others."""
    kept = [ln for ln in old if (ln["item_code"], location) not in scope]
    return sorted([*kept, *new], key=lambda ln: (ln["mr_line_no"], ln.get("po_line_no") or 0))


def _resolve(db: Session, task_id: int) -> None:
    db.execute(
        text("update supply_risk set outcome = 'resolved', closed_by = 'system', closed_at = now() where task_id = :t"),
        {"t": task_id},
    )
    audit.record(db, audit.SYSTEM, "supply_risk.resolved", "task", task_id, reason="nothing on it is at risk any more")
    db.execute(
        text("update task set status = 'done', completed_at = now() where id = :i and status in ('open', 'overdue')"),
        {"i": task_id},
    )


def _day(d: date) -> str:
    return f"{d.day} {d:%b}"


def _title(db: Session, po_no: str, case: dict[str, Any]) -> tuple[str, str]:
    names = {
        str(r[0]): str(r[1])
        for r in db.execute(
            text("select code, name from item where code = any(:c)"), {"c": [ln["item_code"] for ln in case["lines"]]}
        )
    }
    row = case["row"]
    brings = ", ".join(
        f"{ln['at_risk']} {names.get(ln['item_code'], ln['item_code'])} on {_day(date.fromisoformat(ln['promised']))}"
        for ln in case["lines"]
    )
    by = min(date.fromisoformat(ln["latest_arrival"]) for ln in case["lines"])
    needs = min(date.fromisoformat(ln["need_by"]) for ln in case["lines"])
    title = f"{po_no} brings {brings}, after {row['order_no']} needs them on {_day(needs)}"
    supplier = case["ref"].get("supplier") or "the supplier"
    return title, f"Ask {supplier} to deliver by {_day(by)}, or buy elsewhere; then close the case"


def _open(db: Session, tenant: str, po_no: str, case: dict[str, Any]) -> dict[str, Any]:
    """The task and its case row. Commits (the task tells its owner)."""
    row = case["row"]
    category = db.execute(
        text("select c.buyer_user from item i join item_category c on c.code = i.category_code where i.code = :i"),
        {"i": row["item_code"]},
    ).scalar()
    login = demand_cases.buyer_login(db, tenant, [case["ref"].get("buyer"), category])
    calendar = reference.calendar_for_location(db, row["location_code"])
    days = settings.number(db, "late_supply_case_days", DEFAULT_DAYS)
    title, step = _title(db, po_no, case)
    task_id = tasks.create_task(
        db, tenant, kind=KIND, title=title[:200], assignee=login, sla_hours=days * calendar.hours_per_day,
        escalation_chain=demand_cases.chain(db, tenant, login),
        payload={"doc_type": "po", "doc_no": po_no, "mr_no": row["mr_no"], "next_step": step,
                 "calendar_location": row["location_code"]},
        calendar=calendar, email_note=step, now=_now().astimezone(UTC),
    )  # fmt: skip
    db.execute(
        text(
            "insert into supply_risk (task_id, po_no, mr_no, location_code, lines, buffer_days) "
            "values (:t, :p, :m, :l, cast(:ls as jsonb), :b)"
        ),
        {
            "t": task_id, "p": po_no, "m": row["mr_no"], "l": row["location_code"], "ls": json.dumps(case["lines"]),
            "b": case["buffer"],
        },
    )  # fmt: skip
    audit.record(
        db, audit.SYSTEM, "supply_risk.open", "task", task_id,
        after={"po": po_no, "mr": row["mr_no"], "lines": case["lines"], "buffer_days": case["buffer"]},
    )  # fmt: skip
    due = db.execute(text("select due_at from task where id = :i"), {"i": task_id}).scalar_one()
    db.commit()
    code = org_code_of(db, login)
    return {
        "task_id": task_id, "po_no": po_no, "mr_no": row["mr_no"], "assignee": name_of(db, code) if code else f"user {login}",
        "assignee_id": login, "due_at": due.isoformat(), "lines": case["lines"],
        "amount": str(round_money(case["amount"])),
    }  # fmt: skip


# ---- closing by a person, and reading -------------------------------------------------------------


def close(db: Session, user_id: int, task_id: int, outcome: str, note: str | None) -> dict[str, Any]:
    """The owner (or someone who buys) says what was done; the task is done. Commits."""
    row = (
        db.execute(
            text(
                "select s.po_no, s.closed_at, t.assignee, t.status from supply_risk s join task t on t.id = s.task_id "
                "where s.task_id = :t for update of s"
            ),
            {"t": task_id},
        )
        .mappings()
        .first()
    )
    if row is None:
        db.rollback()
        raise CaseError(404, f"there is no late-supply case {task_id}")
    if int(row["assignee"]) != user_id and not permissions.can_anywhere(db, user_id, ACTS):
        db.rollback()
        raise CaseError(403, "only the case's owner, or a buyer, may close it")
    if row["closed_at"] is not None or row["status"] in ("done", "cancelled"):
        db.rollback()
        raise CaseError(409, f"case {task_id} is already closed")
    if outcome == "resolved":
        db.rollback()
        raise CaseError(422, "only the system closes a case as resolved, when nothing on it is at risk any more")
    if outcome not in OUTCOMES:
        db.rollback()
        raise CaseError(422, f"the outcome must be one of {', '.join(OUTCOMES)}")
    note = (note or "").strip() or None
    if outcome in NOTE_NEEDED and not note:
        db.rollback()
        raise CaseError(422, "say why in the note")
    who = org_code_of(db, user_id) or f"user {user_id}"
    db.execute(
        text("update supply_risk set outcome = :o, note = :n, closed_by = :b, closed_at = now() where task_id = :t"),
        {"o": outcome, "n": note, "b": who, "t": task_id},
    )
    audit.record(
        db, user(user_id), "supply_risk.close", "task", task_id, before={"status": row["status"]},
        after={"outcome": outcome, "po": row["po_no"]}, reason=note,
    )  # fmt: skip
    db.commit()
    tasks.complete_task(db, task_id, user_id)
    return {"task_id": task_id, "status": "done", "outcome": outcome}


def listing(db: Session, *, status: str = "open") -> list[dict[str, Any]]:
    """The cases, newest first: open ones, or those closed."""
    where = "s.closed_at is null and t.status in ('open', 'overdue')" if status == "open" else "s.closed_at is not null"
    rows = db.execute(
        text(
            "select s.task_id, s.po_no, s.mr_no, s.location_code, s.lines, s.buffer_days, s.outcome, s.note, "  # noqa: S608
            "s.closed_by, s.closed_at, t.title, t.assignee, t.due_at, t.created_at, t.payload, m.order_no, "
            "sp.legal_name as supplier from supply_risk s join task t on t.id = s.task_id "
            "join material_request m on m.no = s.mr_no join po p on p.no = s.po_no "
            f"left join supplier sp on sp.code = p.supplier_code where {where} order by s.task_id desc"
        )
    ).mappings()
    return [dict(r) for r in rows]


def amount(db: Session, case: dict[str, Any]) -> Decimal:
    """What is at risk on a case at the order's price (rupees)."""
    total = ZERO
    for ln in case["lines"]:
        price = db.execute(
            text("select unit_price, qty, coalesce(base_qty, qty) from po_line where po_no = :p and line_no = :l"),
            {"p": case["po_no"], "l": ln.get("po_line_no")},
        ).first()
        if price is not None and price[0] is not None:
            total += Decimal(ln["at_risk"]) * Decimal(price[0]) * Decimal(price[1]) / Decimal(price[2])
    return round_money(total)
