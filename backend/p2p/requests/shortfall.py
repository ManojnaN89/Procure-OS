"""The engine's figures for each open material request line: what it lacks, what comes in time, what is at risk, what
is short, and why (the causes of formulas/material.py). Read-only. The material requests screen shows them and the
planning agent is given them; nobody else works them out."""

from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.formulas import material as rules
from p2p.formulas.calendar import business_today
from p2p.requests import supply_dates
from p2p.requests.reservation import Place, safety_stock

ZERO = Decimal(0)


def positions(db: Session, places: list[Place]) -> dict[Place, dict[str, Decimal]]:
    """Per item-location: usable, quarantine, blocked, set aside for open lines, safety stock."""
    items = sorted({i for i, _ in places})
    out: dict[Place, dict[str, Decimal]] = {
        p: {"usable": ZERO, "quarantine": ZERO, "blocked": ZERO, "reserved": ZERO, "safety": ZERO} for p in places
    }
    for i, loc, quality, qty in db.execute(
        text("select item_code, location_code, quality, qty from stock_quality_level where item_code = any(:i)"),
        {"i": items},
    ):
        if (i, loc) in out:
            out[(i, loc)][quality] = Decimal(qty)
    for i, loc, qty in db.execute(
        text(
            "select item_code, location_code, sum(reserved_qty) from material_request_line where status = 'open' "
            "and item_code = any(:i) group by 1, 2"
        ),
        {"i": items},
    ):
        if (i, loc) in out:
            out[(i, loc)]["reserved"] = Decimal(qty)
    for p, q in safety_stock(db, places).items():
        out[p]["safety"] = q
    return out


def figures(db: Session, mr_nos: list[str], today: date | None = None) -> dict[int, dict[str, Any]]:
    """Per open line of these material requests (by line id): the cover split by date, the supply, and the causes."""
    today = today or business_today()
    rows = [
        dict(r)
        for r in db.execute(
            text(
                "select c.id, c.mr_no, c.line_no, c.item_code, c.location_code, c.need_qty, c.issued_qty, c.reserved_qty, "
                "c.requested_qty, c.need_by, q.basis from material_request_cover c "
                "join material_request_line l on l.id = c.id join material_requirement q on q.id = l.requirement_id "
                "where c.mr_no = any(:n) and c.status = 'open'"
            ),
            {"n": mr_nos},
        ).mappings()
    ]
    if not rows:
        return {}
    dated = supply_dates.cover_by_date(db, rows, today)
    stock = positions(db, sorted({(r["item_code"], r["location_code"]) for r in rows}))
    rejected = supply_dates.last_rejected(db, [r["id"] for r in rows])
    out: dict[int, dict[str, Any]] = defaultdict(dict)
    for r in rows:
        d = dated[r["id"]]
        c = rules.cover(r["need_qty"], r["issued_qty"], r["reserved_qty"], d.timely, d.late)
        pos = stock[(r["item_code"], r["location_code"])]
        out[r["id"]] = {
            "cover": c,
            "latest_arrival": d.latest_arrival,
            "supply": d.supply(),
            "last_rejected": rejected.get(r["id"]),
            "causes": rules.causes(
                c, need=r["need_qty"], issued=r["issued_qty"], reserved=r["reserved_qty"], usable=pos["usable"],
                set_aside_elsewhere=pos["reserved"] - r["reserved_qty"], safety=pos["safety"],
                held=pos["quarantine"] + pos["blocked"],
            ),
        }  # fmt: skip
    return dict(out)
