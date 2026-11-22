"""When the supply bought for a material request line comes, and whether it comes in time (rule Q17, task 6.1).

Each line's pegged supply (what `material_request_buy` links to it) is split into the parts still to come, each dated:
on an order line its promised date, else the date the order asked for; on a request line not yet ordered (open, or out
for quotation) its need-by. A date already passed with the goods not in counts as today. The parts of one request line
go earliest first to its material request lines earliest need-by first (as `material_request_cover` shares what arrived).
A part counts for the need (timely) only when it comes by the line's latest arrival: its need-by moved back
`receiving_buffer_days` working days on the calendar of the line's place. The rest is late. All of it is read here; the
arithmetic is in formulas/material.py.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.formulas import material as rules
from p2p.formulas.calendar import business_today
from p2p.platform import reference, settings

ZERO = Decimal(0)
LIVE = ("cancelled", "rejected")  # request statuses whose cover no longer counts

# every link of the request lines that buy for the given material request lines, in the order cover is shared
_LINKS = """
    select b.id, b.mr_line_id, b.qty, b.request_no, b.request_line_no, m.need_by,
           coalesce(rl.base_qty, rl.qty) as rl_base, rl.need_by as rl_need_by, rl.status as rl_status
    from material_request_buy b
    join material_request_line m on m.id = b.mr_line_id
    join request_line rl on rl.request_no = b.request_no and rl.line_no = b.request_line_no
    join request r on r.no = b.request_no
    where (b.request_no, b.request_line_no) in (
            select request_no, request_line_no from material_request_buy where mr_line_id = any(:ids))
      and m.status <> 'cancelled' and rl.status <> 'cancelled' and r.status not in ('cancelled', 'rejected')
    order by b.request_no, b.request_line_no, m.need_by, b.id
"""

_ORDER_LINES = """
    select pl.request_no, pl.request_line_no, pl.po_no, pl.line_no, pl.qty, coalesce(pl.base_qty, pl.qty) as base,
           pl.received_qty, pl.short_closed_qty, pl.status, pl.promised_date, pl.need_by, pl.unit_price,
           p.buyer, p.supplier_code, s.legal_name as supplier
    from po_line pl join po p on p.no = pl.po_no left join supplier s on s.code = p.supplier_code
    where (pl.request_no, pl.request_line_no) in (select unnest(cast(:r as text[])), unnest(cast(:l as int[])))
      and pl.status <> 'cancelled' and p.status <> 'cancelled' and pl.qty > 0
    order by pl.po_no, pl.line_no
"""

_RFQS = """
    select distinct on (l.request_no, l.request_line_no) l.request_no, l.request_line_no, r.no
    from rfq_line l join rfq r on r.no = l.rfq_no
    where (l.request_no, l.request_line_no) in (select unnest(cast(:r as text[])), unnest(cast(:l as int[])))
      and r.status <> 'cancelled'
    order by l.request_no, l.request_line_no, r.no desc
"""


@dataclass
class LineCover:
    """One material request line's supply still to come, split by date (stock unit)."""

    latest_arrival: date
    buffer_days: int
    parts: list[rules.Part] = field(default_factory=list)

    @property
    def timely(self) -> Decimal:
        return sum((p.qty for p in self.parts if p.date <= self.latest_arrival), ZERO)

    @property
    def late(self) -> Decimal:
        return sum((p.qty for p in self.parts if p.date > self.latest_arrival), ZERO)

    def supply(self) -> list[dict[str, Any]]:
        """The parts as the screen and the planning tools show them."""
        return [
            {**p.ref, "qty": p.qty, "date": p.date, "timely": p.date <= self.latest_arrival}
            for p in sorted(self.parts, key=lambda p: (p.date, p.ref["doc_no"], p.ref["line_no"]))
        ]


def buffer_days(db: Session) -> int:
    return int(settings.number(db, "receiving_buffer_days", "0"))


def _rl_parts(
    rl_base: Decimal,
    rl_status: str,
    rl_need_by: date,
    orders: list[dict[str, Any]],
    rfq: str | None,
    key: Any,
    today: date,
) -> list[rules.Part]:
    """The parts still to come on one request line: each order line's balance, then what is not ordered yet."""
    parts: list[rules.Part] = []
    ordered = ZERO
    for o in orders:
        per = Decimal(o["base"]) / Decimal(o["qty"])
        ordered += Decimal(o["qty"]) * per
        if o["status"] == "closed":
            continue
        left = max(Decimal(o["qty"]) - Decimal(o["received_qty"]) - Decimal(o["short_closed_qty"] or 0), ZERO) * per
        if left > 0:
            asked = o["promised_date"] or o["need_by"] or rl_need_by
            kind = "today" if asked < today else "promised" if o["promised_date"] else "need_by"
            parts.append(
                rules.Part(
                    left, rules.supply_date(o["promised_date"], o["need_by"] or rl_need_by, today),
                    {"doc_type": "po", "doc_no": o["po_no"], "line_no": o["line_no"], "date_kind": kind, "asked": asked,
                     "supplier": o["supplier"], "supplier_code": o["supplier_code"], "buyer": o["buyer"],
                     "price": Decimal(o["unit_price"] or 0) / per},
                )
            )  # fmt: skip
    rest = max(rl_base - ordered, ZERO) if rl_status in ("open", "sourcing") else ZERO
    if rest > 0:
        parts.append(_unordered(rest, rl_need_by, rfq, key, today))
    return parts


def _unordered(qty: Decimal, need_by: date, rfq: str | None, key: Any, today: date) -> rules.Part:
    when = rules.supply_date(None, need_by, today)
    return rules.Part(
        qty, when,
        {"doc_type": "rfq" if rfq else "request", "doc_no": rfq or key[0], "line_no": key[1],
         "date_kind": "today" if need_by < today else "need_by", "supplier": None, "supplier_code": None, "buyer": None,
         "price": None},
    )  # fmt: skip


def cover_by_date(db: Session, lines: list[dict[str, Any]], today: date | None = None) -> dict[int, LineCover]:
    """Per material request line (rows with id, need_by, location_code): its supply still to come, dated, and its latest
    arrival. The quantities add up to what `material_request_cover` says is still coming for the line."""
    today = today or business_today()
    buffer = buffer_days(db)
    calendars: dict[str, Any] = {}
    out: dict[int, LineCover] = {}
    for ln in lines:
        loc = ln["location_code"]
        if loc not in calendars:
            calendars[loc] = reference.calendar_for_location(db, loc)
        out[int(ln["id"])] = LineCover(rules.latest_arrival(ln["need_by"], buffer, calendars[loc]), buffer)
    if not out:
        return out
    links = [dict(r) for r in db.execute(text(_LINKS), {"ids": list(out)}).mappings()]
    keys = sorted({(r["request_no"], r["request_line_no"]) for r in links})
    params = {"r": [k[0] for k in keys], "l": [k[1] for k in keys]}
    orders: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    rfqs: dict[tuple[str, int], str] = {}
    if keys:
        for o in db.execute(text(_ORDER_LINES), params).mappings():
            orders[(o["request_no"], o["request_line_no"])].append(dict(o))
        rfqs = {(r[0], r[1]): str(r[2]) for r in db.execute(text(_RFQS), params)}
    by_rl: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for r in links:
        by_rl[(r["request_no"], r["request_line_no"])].append(r)
    for key, rl_links in by_rl.items():
        head = rl_links[0]
        mine = orders.get(key, [])
        done = sum(
            ((Decimal(o["received_qty"]) + Decimal(o["short_closed_qty"] or 0)) * Decimal(o["base"]) / Decimal(o["qty"])
             for o in mine),
            ZERO,
        )  # fmt: skip
        wants: list[Decimal] = []
        before = ZERO
        for r in rl_links:  # what is still coming per link, as the cover view shares what arrived or was closed
            wants.append(max(Decimal(r["qty"]) - max(done - before, ZERO), ZERO))
            before += Decimal(r["qty"])
        parts = _rl_parts(
            Decimal(head["rl_base"]), head["rl_status"], head["rl_need_by"], mine, rfqs.get(key), key, today
        )
        for r, got, want in zip(rl_links, rules.time_phase(parts, wants), wants, strict=True):
            line = out.get(int(r["mr_line_id"]))
            if line is None:
                continue
            missing = want - sum((p.qty for p in got), ZERO)
            if missing > 0:  # more is linked than the request line still brings: dated by what was asked
                got = [*got, _unordered(missing, head["rl_need_by"], rfqs.get(key), key, today)]
            line.parts.extend(got)
    return out


def last_rejected(db: Session, line_ids: list[int]) -> dict[int, str]:
    """Lines whose latest purchase request for them was rejected (after their last live one): that request."""
    if not line_ids:
        return {}
    rows = db.execute(
        text(
            "select distinct on (b.mr_line_id) b.mr_line_id, r.no, r.status from material_request_buy b "
            "join request r on r.no = b.request_no where b.mr_line_id = any(:ids) "
            "order by b.mr_line_id, r.created_at desc, r.no desc"
        ),
        {"ids": line_ids},
    ).all()
    return {int(r[0]): str(r[1]) for r in rows if r[2] == "rejected"}
