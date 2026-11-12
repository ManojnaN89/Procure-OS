"""Setting stock aside for open material request lines: the one service that does it, for the store check and on its own
whenever stock goes up (goods accepted at receipt or inspection, a count that finds more, a cancelled material request
giving its stock back).

Free stock (on hand less what is already set aside) goes to the open lines earliest need-by first, in two passes: first
to what no purchase request covers; then, with what is left, to what a purchase request covers that is not yet with the
buyer (its line still open, the request live): that request line is cut by as much, audited, and its requester told; a
request left with nothing to buy is cancelled. Cover already with the buyer (quotations asked, ordered) is left alone and
takes no stock. So for every line, set aside plus still coming on requests and orders never exceeds what it needs.

Stock that arrives on an order bought for a line stops counting as coming (`material_request_cover`), so the line can
take it here. Running again sets nothing more aside: it only ever takes free stock.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.approvals import workflow
from p2p.formulas import material as rules
from p2p.formulas.money import format_qty
from p2p.platform import audit, notifications
from p2p.platform.sod import Actor
from p2p.requests import combining, lifecycle
from p2p.requests.combining import LIVE_REQUEST

Place = tuple[str, str]  # (item, location)

_OPEN_LINES = """
    select l.id, l.mr_no, l.line_no, l.item_code, l.location_code, l.uom, l.need_qty, l.reserved_qty, l.issued_qty,
           l.need_by, l.requested_qty, m.order_no, m.department_code
    from material_request_cover l join material_request m on m.no = l.mr_no
    where l.status = 'open' and m.status = 'open' {where}
    order by l.need_by, m.created_at, m.no, l.line_no
"""

# what open request lines (not yet with the buyer) of live requests buy for each material request line
_OPEN_COVER = """
    select b.mr_line_id, sum(b.qty) from material_request_buy b
    join request_line rl on rl.request_no = b.request_no and rl.line_no = b.request_line_no
    join request r on r.no = b.request_no
    where b.mr_line_id = any(:ids) and rl.status = 'open' and r.status = any(:live) group by b.mr_line_id
"""


@dataclass
class Cut:
    """A purchase request line made smaller because stock now covers part of what it bought for."""

    request_no: str
    line_no: int
    mr_no: str
    item_code: str
    taken: Decimal  # stock unit
    qty_before: Decimal
    qty_after: Decimal


def open_lines(db: Session, *, location: str | None = None, items: list[str] | None = None) -> list[dict[str, Any]]:
    """The open lines of open material requests, earliest need-by first, with what live requests still bring for them."""
    clauses: list[str] = []
    params: dict[str, Any] = {}
    if location:
        clauses.append("and l.location_code = :loc")
        params["loc"] = location
    if items is not None:
        clauses.append("and l.item_code = any(:items)")
        params["items"] = items
    return [dict(r) for r in db.execute(text(_OPEN_LINES.format(where=" ".join(clauses))), params).mappings()]


def lock(db: Session, rows: list[dict[str, Any]]) -> dict[Place, Decimal]:
    """Take the per item-location lock every stock posting takes (in a fixed order, so two callers never wait on each
    other) and lock the lines. Returns the free stock per place."""
    places = sorted({(r["item_code"], r["location_code"]) for r in rows})
    for item, loc in places:
        db.execute(text("select pg_advisory_xact_lock(hashtext(:k))"), {"k": f"stock:{item}:{loc}"})
    db.execute(
        text("select id from material_request_line where id = any(:ids) for update"), {"ids": [r["id"] for r in rows]}
    )
    items = sorted({i for i, _ in places})
    on_hand = {
        (r[0], r[1]): Decimal(r[2])
        for r in db.execute(
            text("select item_code, location_code, on_hand from stock_level where item_code = any(:i)"), {"i": items}
        )
    }
    held = {
        (r[0], r[1]): Decimal(r[2])
        for r in db.execute(
            text(
                "select item_code, location_code, sum(reserved_qty) from material_request_line "
                "where status = 'open' and item_code = any(:i) group by item_code, location_code"
            ),
            {"i": items},
        )
    }
    return {p: on_hand.get(p, Decimal(0)) - held.get(p, Decimal(0)) for p in places}


def _cut_cover(db: Session, actor: Actor, row: dict[str, Any], qty: Decimal) -> list[Cut]:
    """Take `qty` (stock unit) off what open request lines buy for a material request line, newest first, and work each
    request line out again. The caller commits."""
    links = db.execute(
        text(
            "select b.id, b.qty, b.request_no, b.request_line_no from material_request_buy b "
            "join request_line rl on rl.request_no = b.request_no and rl.line_no = b.request_line_no "
            "join request r on r.no = b.request_no where b.mr_line_id = :l and b.qty > 0 and rl.status = 'open' "
            "and r.status = any(:live) order by b.id desc for update of b"
        ),
        {"l": row["id"], "live": list(LIVE_REQUEST)},
    ).all()
    left, cuts = qty, []
    for link_id, link_qty, request_no, line_no in links:
        if left <= 0:
            break
        take = min(Decimal(link_qty), left)
        left -= take
        db.execute(text("update material_request_buy set qty = qty - :q where id = :i"), {"q": take, "i": link_id})
        why = f"stock set aside for {row['order_no']} ({row['mr_no']}) covers {format_qty(take)} {row['uom']}"
        before, after = combining.reduce(db, actor, request_no, line_no, why)
        cuts.append(Cut(request_no, line_no, row["mr_no"], row["item_code"], take, before, after))
    return cuts


def reserve(
    db: Session, actor: Actor, rows: list[dict[str, Any]], free: dict[Place, Decimal]
) -> tuple[dict[int, Decimal], list[Cut]]:
    """Set free stock aside for the lines (from `open_lines`, locked with `lock`), as the module says. The rows are
    updated in place (set aside, still coming). The caller commits, then calls `settle`. Returns what each line took
    now, and the request lines cut."""
    open_cover: dict[int, Decimal] = {
        int(r[0]): Decimal(r[1])
        for r in db.execute(text(_OPEN_COVER), {"ids": [r["id"] for r in rows], "live": list(LIVE_REQUEST)})
    }
    by_place: dict[Place, list[dict[str, Any]]] = {}
    for r in rows:
        by_place.setdefault((r["item_code"], r["location_code"]), []).append(r)
    taken: dict[int, Decimal] = {}
    cuts: list[Cut] = []
    for place, lines in by_place.items():
        wants = [
            (
                rules.still_short(r["need_qty"], r["issued_qty"], r["reserved_qty"], r["requested_qty"]),
                rules.releasable(
                    r["need_qty"],
                    r["issued_qty"],
                    r["reserved_qty"],
                    r["requested_qty"],
                    Decimal(open_cover.get(r["id"], 0)),
                ),
            )  # fmt: skip
            for r in lines
        ]
        for r, (take, over) in zip(lines, rules.allocate(wants, free.get(place, Decimal(0))), strict=True):
            taken[r["id"]] = take + over
            if not take + over:
                continue
            r["reserved_qty"] += take + over
            db.execute(
                text("update material_request_line set reserved_qty = :q where id = :i"),
                {"q": r["reserved_qty"], "i": r["id"]},
            )
            if over:
                cuts += _cut_cover(db, actor, r, over)
                r["requested_qty"] -= over
    return taken, cuts


def settle(db: Session, tenant: str | None, cuts: list[Cut]) -> None:
    """After the cut is committed: a request with nothing left to buy is cancelled, and each requester hears of the cut."""
    for no in sorted({c.request_no for c in cuts}):
        why = "Stock now covers what it was raised for: " + "; ".join(
            f"{c.item_code} {format_qty(c.qty_before)} -> {format_qty(c.qty_after)} ({c.mr_no})"
            for c in cuts
            if c.request_no == no
        )
        if lifecycle.cancel_emptied(db, tenant, no, why):
            continue
        requester = db.execute(text("select requester from request where no = :n"), {"n": no}).scalar()
        workflow.notify_person(
            db, tenant, requester, "request", f"Request {no} was made smaller", why, link=f"#/requisitions/{no}"
        )


def _tell_store(
    db: Session, tenant: str, reserved: dict[str, dict[str, Decimal]], orders: dict[str, str], why: str
) -> None:
    """Everyone who manages material requests sees it in the app (no email: it is on their material requests page)."""
    what = "; ".join(
        f"{orders[mr]} ({mr}): " + ", ".join(f"{format_qty(q)} {item}" for item, q in items.items())
        for mr, items in reserved.items()
    )
    store = [
        int(r[0])
        for r in db.execute(
            text(
                "select distinct ur.user_id from user_role ur join role_permission rp on rp.role = ur.role "
                "where rp.action = 'material.manage' order by 1"
            )
        )
    ]
    for user_id in store:
        notifications.notify(
            db, tenant, user_id, "material", "Stock set aside for production", f"{why}: set aside {what}.",
            link="#/material-requests", email=False,
        )  # fmt: skip


def stock_rose(db: Session, tenant: str | None, places: list[Place], *, why: str) -> dict[str, dict[str, Decimal]]:
    """Stock went up at these item-locations: set it aside for the open material request lines there, as the store
    check would, by the system. Commits. The store is told. Returns per material request what was set aside now."""
    wanted = set(places)
    if not wanted:
        return {}
    db.execute(text("select pg_advisory_xact_lock(hashtext('store_check'))"))  # one at a time with the store check
    rows = [
        r
        for r in open_lines(db, items=sorted({i for i, _ in wanted}))
        if (r["item_code"], r["location_code"]) in wanted
    ]
    if not rows:
        db.commit()
        return {}
    taken, cuts = reserve(db, audit.SYSTEM, rows, lock(db, rows))
    reserved: dict[str, dict[str, Decimal]] = {}
    orders: dict[str, str] = {}
    for r in rows:
        if taken[r["id"]]:
            reserved.setdefault(r["mr_no"], {})[r["item_code"]] = taken[r["id"]]
            orders[r["mr_no"]] = r["order_no"]
    for mr_no, items in reserved.items():
        audit.record(
            db, audit.SYSTEM, "material_request.reserve", "material_request", mr_no,
            after={
                "reserved_now": {i: str(q) for i, q in items.items()},
                "requests_cut": [f"{c.request_no}/{c.line_no}" for c in cuts if c.mr_no == mr_no],
            },
            reason=why,
        )  # fmt: skip
    db.commit()
    settle(db, tenant, cuts)
    if reserved and tenant:
        _tell_store(db, tenant, reserved, orders, why)
    return reserved
