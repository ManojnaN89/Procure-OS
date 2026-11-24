"""The control tower: where every case stands (by stage, location and age) and the KPIs, from the platform's own tables."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.formulas import kpis as rules
from p2p.formulas.calendar import IST, BusinessCalendar
from p2p.platform.rows import rows as _rows

D = Decimal
IST_DATE = "(%s at time zone 'Asia/Kolkata')::date"


@dataclass(frozen=True)
class Item:
    id: str
    label: str
    location: str | None
    value: Decimal
    since: date
    link: str


STAGES: list[tuple[str, str]] = [
    ("request_approval", "Request waiting for approval"),
    ("sourcing", "Approved, not yet sourced"),
    ("rfq_out", "RFQ out, no quotes yet"),
    ("award", "Quotes in, waiting for an award"),
    ("po_approval", "Order waiting for approval"),
    ("po_sent", "Order approved or sent, not acknowledged"),
    ("awaiting_receipt", "Acknowledged, goods to come"),
    ("awaiting_invoice", "Goods received, no invoice yet"),
    ("invoice_blocked", "Invoice blocked"),
    ("awaiting_payment", "Invoice approved, awaiting payment"),
]


def _d(ts: Any) -> date:
    return ts.astimezone(IST).date() if isinstance(ts, datetime) else date(ts.year, ts.month, ts.day)


def stage_items(db: Session, stage: str) -> list[Item]:
    """The cases that are in one stage, each with when it got there."""
    items: list[Item] = []
    if stage == "request_approval":
        for r in _rows(
            db,
            "select no, total_value, ship_to_location, coalesce(submitted_at, created_at) as since from request where status = 'submitted'",
        ):
            items.append(
                Item(
                    r["no"],
                    r["no"],
                    r["ship_to_location"],
                    D(r["total_value"]),
                    _d(r["since"]),
                    f"#/requisitions/{r['no']}",
                )
            )
    elif stage == "sourcing":
        for r in _rows(
            db,
            "select r.no, r.total_value, r.ship_to_location, coalesce(r.decided_at, r.created_at) as since from request r where r.status = 'approved' "
            "and exists (select 1 from request_line l where l.request_no = r.no and l.status = 'open' and not exists "
            "(select 1 from sourcing_decision d where d.request_no = l.request_no and d.line_no = l.line_no))",
        ):
            items.append(
                Item(
                    r["no"],
                    r["no"],
                    r["ship_to_location"],
                    D(r["total_value"]),
                    _d(r["since"]),
                    f"#/requisitions/{r['no']}",
                )
            )
    elif stage in ("rfq_out", "award"):
        quoted = stage == "award"
        for r in _rows(
            db,
            "select f.no, r.ship_to_location, r.total_value, "  # noqa: S608 - fixed fragments only
            "coalesce((select min(q.received_at) from quote q where q.rfq_no = f.no), f.sent_at, f.created_at) as since "
            "from rfq f join request r on r.no = f.request_no where f.status = 'sent' and "
            + ("exists" if quoted else "not exists")
            + " (select 1 from quote q where q.rfq_no = f.no)",
        ):
            items.append(
                Item(r["no"], r["no"], r["ship_to_location"], D(r["total_value"]), _d(r["since"]), f"#/rfqs/{r['no']}")
            )
    elif stage == "po_approval":
        for r in _rows(
            db, "select no, total, ship_to_location, created_at as since from po where status in ('draft', 'submitted')"
        ):
            items.append(
                Item(
                    r["no"],
                    r["no"],
                    r["ship_to_location"],
                    D(r["total"]),
                    _d(r["since"]),
                    f"#/purchase-orders/{r['no']}",
                )
            )
    elif stage == "po_sent":
        for r in _rows(
            db,
            "select no, total, ship_to_location, coalesce(sent_at, approved_at, created_at) as since from po where status in ('approved', 'sent')",
        ):
            items.append(
                Item(
                    r["no"],
                    r["no"],
                    r["ship_to_location"],
                    D(r["total"]),
                    _d(r["since"]),
                    f"#/purchase-orders/{r['no']}",
                )
            )
    elif stage == "awaiting_receipt":
        for r in _rows(
            db,
            "select p.no, p.ship_to_location, coalesce((select max(g.gate_entry_at) from goods_receipt g where g.po_no = p.no), p.acknowledged_at, p.sent_at, p.created_at) as since, "
            "coalesce((select sum(greatest(l.qty - l.received_qty, 0) * l.unit_price) from po_line l where l.po_no = p.no and l.status in ('open', 'part_received')), 0) as value "
            "from po p where p.status in ('acknowledged', 'part_received')",
        ):
            items.append(
                Item(
                    r["no"],
                    r["no"],
                    r["ship_to_location"],
                    D(r["value"]),
                    _d(r["since"]),
                    f"#/purchase-orders/{r['no']}",
                )
            )
    elif stage == "awaiting_invoice":
        for r in _rows(
            db,
            "select p.no, p.ship_to_location, coalesce((select max(g.accepted_on) from goods_receipt g where g.po_no = p.no), "  # noqa: S608
            + IST_DATE % "p.created_at"
            + ") as since, "
            "sum((l.received_qty - l.invoiced_qty) * l.unit_price) as value from po p join po_line l on l.po_no = p.no "
            "where l.received_qty > l.invoiced_qty and l.status <> 'cancelled' and p.status not in ('draft', 'submitted', 'cancelled') "
            # an order whose invoice is blocked is shown under "Invoice blocked", not twice
            "and not exists (select 1 from invoice_line il join invoice i on i.no = il.invoice_no where il.po_no = p.no "
            "and i.type = 'invoice' and i.status = 'blocked' and i.payment_block <> 'duplicate') "
            "group by p.no, p.ship_to_location, p.created_at",
        ):
            items.append(Item(r["no"], r["no"], r["ship_to_location"], D(r["value"]), _d(r["since"]), "#/invoices/new"))
    elif stage in ("invoice_blocked", "awaiting_payment"):
        blocked = stage == "invoice_blocked"
        for r in _rows(
            db,
            "select i.no, i.total, "  # noqa: S608 - fixed fragments only
            + ("i.received_on" if blocked else "coalesce(" + IST_DATE % "i.booked_at" + ", i.received_on)")
            + " as since, "
            "(select po.ship_to_location from invoice_line l join po on po.no = l.po_no where l.invoice_no = i.no order by l.line_no limit 1) as location "
            "from invoice i where i.type = 'invoice' and "
            + ("i.status = 'blocked' and i.payment_block <> 'duplicate'" if blocked else "i.status = 'approved'"),
        ):
            items.append(Item(r["no"], r["no"], r["location"], D(r["total"]), _d(r["since"]), f"#/invoices/{r['no']}"))
    else:
        raise KeyError(stage)
    return items


def aged(db: Session, stage: str, today: date, calendar: BusinessCalendar) -> list[tuple[Item, int, str]]:
    out = []
    for item in stage_items(db, stage):
        days = rules.working_days_between(item.since, today, calendar)
        out.append((item, days, rules.bucket_of(days)))
    return out


def tower(db: Session, today: date, calendar: BusinessCalendar, location: str | None = None) -> dict[str, Any]:
    stages = []
    for key, title in STAGES:
        rows = [
            (i, d, b) for i, d, b in aged(db, key, today, calendar) if location in (None, "") or i.location == location
        ]
        buckets = dict.fromkeys(rules.BUCKETS, 0)
        by_location: dict[str, dict[str, Any]] = defaultdict(lambda: {"count": 0, "value": D(0)})
        for item, _, bucket in rows:
            buckets[bucket] += 1
            slot = by_location[item.location or "-"]
            slot["count"] += 1
            slot["value"] += item.value
        stages.append(
            {
                "stage": key,
                "title": title,
                "count": len(rows),
                "value": str(sum((i.value for i, _, _ in rows), D(0)).quantize(D("0.01"))),
                "buckets": buckets,
                "oldest_days": max((d for _, d, _ in rows), default=0),
                "by_location": {
                    k: {"count": v["count"], "value": str(v["value"].quantize(D("0.01")))}
                    for k, v in sorted(by_location.items())
                },
            }
        )
    locations = [r["code"] for r in _rows(db, "select code from location order by code")]
    return {"as_of": today.isoformat(), "locations": locations, "stages": stages}


def drill(
    db: Session, stage: str, today: date, calendar: BusinessCalendar, bucket: str | None, location: str | None
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item, days, b in aged(db, stage, today, calendar):
        if bucket and b != bucket:
            continue
        if location and item.location != location:
            continue
        rows.append(
            {
                "id": item.id, "label": item.label, "location": item.location, "value": str(item.value.quantize(D("0.01"))),
                "since": item.since.isoformat(), "age_days": days, "bucket": b, "link": item.link,
            }
        )  # fmt: skip
    rows.sort(key=lambda r: (-r["age_days"], str(r["id"])))
    return rows


# ---- the KPIs ---------------------------------------------------------------------------------

CYCLE_STAGES = [
    ("approval", "request raised to approved"),
    ("ordering", "approved to order made"),
    ("po_approval", "order made to order approved"),
    ("dispatch", "order approved to sent"),
    ("delivery", "sent to goods received"),
    ("invoicing", "goods received to invoice received"),
    ("payment", "invoice received to paid"),
    ("total", "request raised to paid"),
]


def cycle_times(db: Session, calendar: BusinessCalendar) -> dict[str, dict[str, Any]]:
    """Working days spent in each stage, over the requests that have been paid in full."""
    per_stage: dict[str, list[int]] = defaultdict(list)
    cases = _rows(
        db,
        "select r.no, r.created_at, r.decided_at, min(po.created_at) as po_created, max(po.approved_at) as po_approved, "
        "min(po.sent_at) as sent, array_agg(po.no) as pos from request r join po on po.request_no = r.no "
        "where po.status <> 'cancelled' group by r.no, r.created_at, r.decided_at",
    )
    for c in cases:
        inv = _rows(
            db,
            "select min(i.received_at) as invoiced, max(i.paid_on) as paid, bool_and(i.status = 'paid') as all_paid, count(*) as n "
            "from invoice i where i.type = 'invoice' and i.status <> 'cancelled' and exists "
            "(select 1 from invoice_line l where l.invoice_no = i.no and l.po_no = any(:pos))",
            pos=c["pos"],
        )[0]
        if not inv["n"] or not inv["all_paid"]:
            continue
        received = db.execute(
            text("select min(gate_entry_at) from goods_receipt where po_no = any(:p)"), {"p": c["pos"]}
        ).scalar()
        marks = {
            "created": _d(c["created_at"]), "approved": _d(c["decided_at"]) if c["decided_at"] else None,
            "po_created": _d(c["po_created"]), "po_approved": _d(c["po_approved"]) if c["po_approved"] else None,
            "sent": _d(c["sent"]) if c["sent"] else None, "received": _d(received) if received else None,
            "invoiced": _d(inv["invoiced"]), "paid": inv["paid"],
        }  # fmt: skip
        for name, a, b in (
            ("approval", "created", "approved"), ("ordering", "approved", "po_created"), ("po_approval", "po_created", "po_approved"),
            ("dispatch", "po_approved", "sent"), ("delivery", "sent", "received"), ("invoicing", "received", "invoiced"),
            ("payment", "invoiced", "paid"), ("total", "created", "paid"),
        ):  # fmt: skip
            if marks[a] is not None and marks[b] is not None:
                per_stage[name].append(rules.working_days_between(marks[a], marks[b], calendar))
    return {
        key: {
            "label": label,
            "cases": len(per_stage[key]),
            "median": _s(rules.median(per_stage[key])),
            "p90": _s(rules.percentile_90(per_stage[key])),
        }
        for key, label in CYCLE_STAGES
    }


def _s(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def kpis(db: Session, calendar: BusinessCalendar) -> dict[str, Any]:
    out: dict[str, Any] = {"cycle_time": cycle_times(db, calendar)}
    # spend under management: order value (before tax) on a contract or an awarded quotation
    managed, contract, total = db.execute(
        text(
            "select coalesce(sum(l.taxable) filter (where l.source in ('contract', 'award')), 0), "
            "coalesce(sum(l.taxable) filter (where l.source = 'contract'), 0), coalesce(sum(l.taxable), 0) "
            "from po_line l join po on po.no = l.po_no where po.status not in ('draft', 'submitted', 'cancelled') and l.status <> 'cancelled'"
        )
    ).one()
    out["spend_under_management"] = {
        "pct": _s(rules.spend_under_management(D(managed), D(total))),
        "contract_pct": _s(rules.share(D(contract), D(total))),
        "total": str(D(total)),
    }
    # savings: potential and realised from the price measures (DF-29, R6.2); never the first quote again
    from p2p.invoices import price_measures  # noqa: PLC0415 - price measures are block 13's

    out["savings"] = price_measures.kpis(db)
    # touchless: reached an order and no person touched the request, its RFQs or its orders
    touched = db.execute(
        text(
            "select count(*) filter (where users = 0), count(*) from (select r.no, (select count(*) from audit_event a where a.actor_type = 'user' and "
            "((a.entity = 'request' and a.entity_id = r.no) or (a.entity = 'po' and a.entity_id in (select po.no from po where po.request_no = r.no)) "
            "or (a.entity = 'rfq' and a.entity_id in (select f.no from rfq f where f.request_no = r.no)))) as users "
            "from request r where exists (select 1 from po where po.request_no = r.no and po.status <> 'cancelled')) x"
        )
    ).one()
    out["touchless"] = {"requests": int(touched[1]), "pct": _s(rules.share(int(touched[0]), int(touched[1])))}
    # on-time delivery: the first receipt of a line was on or before its promised date
    line_times = _rows(
        db,
        "select (select min("  # noqa: S608 - fixed fragments only
        + IST_DATE % "g.gate_entry_at"
        + ") from gr_line gl join goods_receipt g on g.no = gl.gr_no "
        "where gl.po_no = l.po_no and gl.po_line_no = l.line_no and gl.qty_received > 0) as received, coalesce(l.promised_date, l.need_by) as promised "
        "from po_line l join po on po.no = l.po_no where po.status not in ('draft', 'submitted', 'cancelled')",
    )
    judged = [r for r in line_times if r["received"] is not None and r["promised"] is not None]
    on_time = sum(1 for r in judged if r["received"] <= r["promised"])
    out["on_time_delivery"] = {"lines": len(judged), "pct": _s(rules.share(on_time, len(judged)))}
    # invoice first-pass match: never blocked, not even for a while
    first, matched = db.execute(
        text(
            "select count(*) filter (where not exists (select 1 from match_result m where m.invoice_no = i.no and m.result = 'blocked')), count(*) "
            "from invoice i where i.type = 'invoice' and i.payment_block <> 'duplicate' and exists (select 1 from match_result m where m.invoice_no = i.no)"
        )
    ).one()
    out["first_pass_match"] = {"invoices": int(matched), "pct": _s(rules.share(int(first), int(matched)))}
    # small suppliers paid by the due date
    paid_late = db.execute(
        text(
            "select count(*) filter (where i.paid_on <= i.due_date), count(*) from invoice i join supplier s on s.code = i.supplier_code "
            "where i.type = 'invoice' and i.status = 'paid' and i.paid_on is not null and i.due_date is not null and s.msme_category in ('micro', 'small')"
        )
    ).one()
    out["msme_on_time_payment"] = {
        "invoices": int(paid_late[1]),
        "pct": _s(rules.share(int(paid_late[0]), int(paid_late[1]))),
    }
    out["duplicates_avoided"] = duplicates_avoided(db)
    return out


def duplicates_avoided(db: Session) -> dict[str, Any]:
    """What the duplicate checks stopped: requests that looked like a repeat and were given up (cancelled or rejected) with
    nobody going ahead on a reason, and invoices blocked as a duplicate of one already on file. Count and value of each."""
    req_n, req_value = db.execute(
        text(
            "select count(*), coalesce(sum(r.total_value), 0) from request r where r.status in ('cancelled', 'rejected') "
            "and exists (select 1 from request_check c where c.request_no = r.no and c.kind = 'duplicate') "
            "and not exists (select 1 from request_check c where c.request_no = r.no and c.kind = 'duplicate' and c.override_reason is not null)"
        )
    ).one()
    inv_n, inv_value = db.execute(
        text(
            "select count(*), coalesce(sum(total), 0) from invoice where payment_block = 'duplicate' "
            "and status in ('blocked', 'cancelled')"
        )
    ).one()
    return {
        "requests": {"count": int(req_n), "value": str(D(req_value))},
        "invoices": {"count": int(inv_n), "value": str(D(inv_value))},
        "count": int(req_n) + int(inv_n),
        "value": str(D(req_value) + D(inv_value)),
    }
