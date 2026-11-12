"""Combined requisition lines: what several production orders (and the restock of the same item) need at one delivery
place within a few days is bought on one request line.

A planned line (a material request's shortfall, or a restock) joins an open line of a *draft* planning request for the
same item and delivery location when all their need-by dates fall within `combine_window_days` (default 7). Nothing is
added to a request once it has been submitted: the approver approved what they saw. Within one store check the requests
are written one after the other as drafts and submitted at the end, so shortfalls of several orders checked together
combine; a restock run combines with a shortfall still in draft, and the other way round.

A combined line keeps every source with its own quantity (stock unit): one `material_request_buy` row per material
request line, and `request_line.stock_qty` for what it buys for stock. Its quantity is always worked out from them,
rounded up once to the pack and minimum order. So the material requests see exactly what is bought for each of them,
cancelling one order takes its part off the line, and replenishment counts only the stock part as already requested.
"""

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.formulas.combining import combined_quantity, within_window
from p2p.formulas.money import format_qty
from p2p.platform import audit, settings
from p2p.platform.sod import Actor
from p2p.requests import drafts

DEFAULT_WINDOW_DAYS = "7"
LIVE_REQUEST = ("draft", "submitted", "approved", "returned")  # a line of these may still be made smaller

# what each line buys for material requests that are not cancelled, and the latest need-by among them
_LINKED = """
    select b.request_no, b.request_line_no, sum(b.qty) as qty, max(m.need_by) as latest
    from material_request_buy b join material_request_line m on m.id = b.mr_line_id
    where m.status <> 'cancelled' {where}
    group by b.request_no, b.request_line_no
"""


def window_days(db: Session) -> int:
    return int(settings.number(db, "combine_window_days", DEFAULT_WINDOW_DAYS))


def open_line(db: Session, item: str, location: str, need_by: date, window: int) -> dict[str, Any] | None:
    """An open line of a draft planning request this need can join: same item and delivery location, every need-by
    within the window. The request is locked. None when there is none."""
    rows = db.execute(
        text(
            "select rl.request_no, rl.line_no, rl.need_by, rl.uom, k.latest from request_line rl "  # noqa: S608 - fixed fragments only
            "join request r on r.no = rl.request_no "
            "left join (" + _LINKED.format(where="") + ") k "
            "on k.request_no = rl.request_no and k.request_line_no = rl.line_no "
            "where r.status = 'draft' and r.channel = 'planning' and rl.status = 'open' and rl.stock_qty is not null "
            "and rl.item_code = :i and rl.delivery_location = :l order by rl.need_by, rl.request_no, rl.line_no"
        ),
        {"i": item, "l": location},
    ).mappings()
    for r in rows:
        dates = [r["need_by"], need_by] + ([r["latest"]] if r["latest"] else [])
        if within_window(dates, window):
            db.execute(text("select 1 from request where no = :n for update"), {"n": r["request_no"]})
            return dict(r)
    return None


def _resize(db: Session, request_no: str, line_no: int, *, may_grow: bool) -> tuple[Decimal, Decimal]:
    """Work the line's quantity out again from its sources, and the request's value and need-by after it. A request
    already submitted is never made bigger. Returns the quantity before and after (after 0: the line is cancelled)."""
    line = (
        db.execute(
            text(
                "select item_code, qty, coalesce(stock_qty, 0) as stock_qty, est_price from request_line "
                "where request_no = :n and line_no = :l for update"
            ),
            {"n": request_no, "l": line_no},
        )
        .mappings()
        .one()
    )
    linked = db.execute(
        text(_LINKED.format(where="and b.request_no = :n and b.request_line_no = :l")), {"n": request_no, "l": line_no}
    ).first()
    item = drafts.item_info(db, [line["item_code"]])[line["item_code"]]
    _, factor = drafts.purchase_unit(item)
    qty = combined_quantity(
        [Decimal(line["stock_qty"]), Decimal(linked.qty) if linked else Decimal(0)],
        base_per_purchase_unit=factor,
        moq=Decimal(item["moq"] or 0),
    )
    old = Decimal(line["qty"])
    if not may_grow:
        qty = min(qty, old)
    if qty:
        db.execute(
            text("update request_line set qty = :q, base_qty = :b where request_no = :n and line_no = :l"),
            {"q": qty, "b": qty * factor, "n": request_no, "l": line_no},
        )
    else:  # nothing left to buy on it: the line goes, its quantity stays on record
        db.execute(
            text("update request_line set status = 'cancelled' where request_no = :n and line_no = :l"),
            {"n": request_no, "l": line_no},
        )
    db.execute(
        text(
            "update request set total_value = (select coalesce(sum(round(qty * coalesce(est_price, 0), 2)), 0) "
            "from request_line where request_no = :n and status <> 'cancelled'), "
            "need_by = coalesce((select min(need_by) from request_line where request_no = :n and status <> 'cancelled'), "
            "need_by) where no = :n"
        ),
        {"n": request_no},
    )
    return old, qty


def join(
    db: Session,
    actor: Actor,
    target: dict[str, Any],
    *,
    need_by: date,
    note: str,
    link: tuple[int, Decimal] | None = None,
    stock_qty: Decimal = Decimal(0),
) -> Decimal:
    """Add a need to an open draft line (from `open_line`): a material request line with its quantity, or a quantity for
    stock (stock unit). The line's quantity is worked out again from all its sources. The caller commits. Returns the
    line's new quantity."""
    no, line_no = target["request_no"], target["line_no"]
    if link is not None:
        db.execute(
            text(
                "insert into material_request_buy (mr_line_id, request_no, request_line_no, qty) values (:m, :r, :n, :q)"
            ),
            {"m": link[0], "r": no, "n": line_no, "q": link[1]},
        )
    db.execute(
        text(
            "update request_line set stock_qty = stock_qty + :s, need_by = least(need_by, :d), "
            "reason = left(coalesce(reason, '') || '; combined with ' || :note, 1000) where request_no = :n and line_no = :l"
        ),
        {"s": stock_qty, "d": need_by, "note": note, "n": no, "l": line_no},
    )
    old, new = _resize(db, no, line_no, may_grow=True)
    audit.record(
        db,
        actor,
        "request.combine",
        "request",
        no,
        before={"line": line_no, "qty": str(old)},
        after={
            "line": line_no,
            "qty": str(new),
            "material_request_line": link[0] if link else None,
            "for_material_request": str(link[1]) if link else None,
            "for_stock": str(stock_qty) if stock_qty else None,
            "need_by": need_by.isoformat(),
        },
        reason=f"combined with {note}",
    )
    return new


def release(db: Session, actor: Actor, mr_no: str, reason: str) -> tuple[list[str], list[str]]:
    """A material request was cancelled: each line still buying for it together with other sources is made smaller by
    its part (never below what the others need). Lines that bought only for it, or that are already with the buyer, are
    left for the buyer. Call after its lines are marked cancelled; the caller commits. Returns (requests made smaller,
    requests left for the buyer)."""
    lines = db.execute(
        text(
            "select distinct b.request_no, b.request_line_no, r.status as request_status, rl.status as line_status, "
            "coalesce(rl.stock_qty, 0) > 0 or exists (select 1 from material_request_buy o "
            "join material_request_line m on m.id = o.mr_line_id where o.request_no = b.request_no "
            "and o.request_line_no = b.request_line_no and m.status <> 'cancelled') as shared "
            "from material_request_buy b join material_request_line l on l.id = b.mr_line_id "
            "join request r on r.no = b.request_no "
            "join request_line rl on rl.request_no = b.request_no and rl.line_no = b.request_line_no "
            "where l.mr_no = :m order by 1, 2"
        ),
        {"m": mr_no},
    ).mappings()
    reduced: set[str] = set()
    left: set[str] = set()
    for ln in list(lines):
        no, line_no = ln["request_no"], ln["request_line_no"]
        if ln["request_status"] not in LIVE_REQUEST or ln["line_status"] == "cancelled":
            continue
        if not ln["shared"] or ln["line_status"] != "open":
            left.add(no)
            continue
        old, new = _resize(db, no, line_no, may_grow=ln["request_status"] == "draft")
        if new != old:
            audit.record(
                db, actor, "request.line_reduce", "request", no,
                before={"line": line_no, "qty": str(old)}, after={"line": line_no, "qty": str(new)},
                reason=f"{mr_no} cancelled: {reason}",
            )  # fmt: skip
            reduced.add(no)
    return sorted(reduced), sorted(left - reduced)


def sources(db: Session, request_no: str) -> dict[int, list[dict[str, Any]]]:
    """For the request screen: per line, what it buys for each production order and for stock."""
    out: dict[int, list[dict[str, Any]]] = {}
    for r in db.execute(
        text(
            "select b.request_line_no, m.mr_no, h.order_no, m.uom, sum(b.qty) as qty, max(m.status) as status "
            "from material_request_buy b join material_request_line m on m.id = b.mr_line_id "
            "join material_request h on h.no = m.mr_no where b.request_no = :n "
            "group by b.request_line_no, m.mr_no, h.order_no, m.uom order by b.request_line_no, h.order_no"
        ),
        {"n": request_no},
    ).mappings():
        out.setdefault(r["request_line_no"], []).append(
            {
                "material_request": r["mr_no"],
                "order_no": r["order_no"],
                "qty": format_qty(r["qty"]),
                "uom": r["uom"],
                "cancelled": r["status"] == "cancelled",
            }
        )
    for line_no, qty, uom in db.execute(
        text(
            "select l.line_no, l.stock_qty, i.base_uom from request_line l join item i on i.code = l.item_code "
            "where l.request_no = :n and l.stock_qty > 0"
        ),
        {"n": request_no},
    ):
        out.setdefault(line_no, []).append({"stock": True, "qty": format_qty(qty), "uom": uom})
    return out
