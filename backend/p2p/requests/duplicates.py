"""The check before ordering: what is already there for each line, what is left to ask for, and whether the request repeats
another one.

Per line: stock on hand, other open requests, the balance of orders not yet sent, and what is on the way from suppliers.
The suggested net quantity is the quantity asked for less everything that is already asked for or ordered ('20 ordered, 10
received, 20 requested' suggests 10). A request is flagged as a possible repeat of another when they ask for the same item
at the same location with need dates close together (the company's duplicate window), or for lines worded almost the same.
Submitting a flagged request needs a reason from the person who goes ahead.
"""

import difflib
import json
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.platform import audit
from p2p.platform.sod import user

D = Decimal
QTY = D("0.0001")
TEXT_SIMILARITY = 0.82  # how alike two wordings must be to count as the same thing
LIVE_REQUEST = ("draft", "submitted", "approved", "returned")
NOT_SENT = ("draft", "submitted", "approved")
SENT = ("sent", "acknowledged", "part_received")


class DuplicateError(Exception):
    def __init__(self, message: str, duplicates: list["Duplicate"] | None = None) -> None:
        super().__init__(message)
        self.duplicates = duplicates or []


@dataclass
class LineCheck:
    line_no: int
    item_code: str | None
    name: str
    qty: Decimal
    uom: str | None
    base_qty: Decimal
    on_hand: Decimal
    open_requests: Decimal  # other requests still asking for the item here (stock units)
    open_po_balance: Decimal  # ordered but not yet sent to the supplier, still to receive
    inbound: Decimal  # sent to the supplier, still to receive
    suggested_net_qty: Decimal  # in the unit of the line


@dataclass
class Duplicate:
    other_request: str
    other_status: str
    similarity: Decimal  # the share of the lines that repeat, 0 to 1
    kind: str  # same_items or similar_text
    lines: list[dict[str, Any]] = field(default_factory=list)
    overridden: bool = False
    override_reason: str | None = None

    @property
    def likely(self) -> bool:
        return self.similarity >= D("0.99") and all(ln["same_qty"] for ln in self.lines)


# ---- the numbers for each line ----------------------------------------------------------------


def _setting(db: Session, key: str, default: str) -> str:
    for table in ("app_setting", "org_setting"):
        value = db.execute(text(f"select value from {table} where key = :k"), {"k": key}).scalar()  # noqa: S608
        if value not in (None, ""):
            return str(value)
    return default


def _lines(db: Session, request_no: str) -> list[dict[str, Any]]:
    return [
        dict(r)
        for r in db.execute(
            text(
                "select rl.line_no, rl.item_code, coalesce(i.name, rl.free_text) as name, rl.qty, rl.uom, "
                "coalesce(rl.base_qty, rl.qty) as base_qty, coalesce(rl.need_by, r.need_by) as need_by, "
                "coalesce(rl.delivery_location, r.ship_to_location) as loc from request_line rl "
                "join request r on r.no = rl.request_no left join item i on i.code = rl.item_code "
                "where rl.request_no = :n and rl.status <> 'cancelled' order by rl.line_no"
            ),
            {"n": request_no},
        ).mappings()
    ]


def line_checks(db: Session, request_no: str) -> list[LineCheck]:
    out: list[LineCheck] = []
    for ln in _lines(db, request_no):
        qty, base_qty = D(ln["qty"]), D(ln["base_qty"])
        on_hand = open_requests = open_po = inbound = D(0)
        if ln["item_code"]:
            item, loc = ln["item_code"], ln["loc"]
            on_hand = D(
                db.execute(
                    text(
                        "select coalesce(sum(on_hand), 0) from stock_level where item_code = :i and location_code = :l"
                    ),
                    {"i": item, "l": loc},
                ).scalar_one()
            )
            open_requests = D(
                db.execute(
                    text(
                        "select coalesce(sum(coalesce(rl.base_qty, rl.qty)), 0) from request_line rl "
                        "join request r on r.no = rl.request_no where rl.item_code = :i and r.no <> :n "
                        "and coalesce(rl.delivery_location, r.ship_to_location) = :l and rl.status in ('open', 'sourcing') "
                        "and r.status = any(:st)"
                    ),
                    {"i": item, "n": request_no, "l": loc, "st": list(LIVE_REQUEST)},
                ).scalar_one()
            )
            for statuses, which in ((NOT_SENT, "open_po"), (SENT, "inbound")):
                balance = D(
                    db.execute(
                        text(
                            "select coalesce(sum(coalesce(pl.base_qty, pl.qty) * greatest(pl.qty - pl.received_qty, 0) "
                            "/ nullif(pl.qty, 0)), 0) from po_line pl join po on po.no = pl.po_no "
                            "where pl.item_code = :i and po.ship_to_location = :l and pl.status in ('open', 'part_received') "
                            "and po.status = any(:st)"
                        ),
                        {"i": item, "l": loc, "st": list(statuses)},
                    ).scalar_one()
                )
                if which == "open_po":
                    open_po = balance
                else:
                    inbound = balance
        coming = open_requests + open_po + inbound
        net_base = max(base_qty - coming, D(0))
        suggested = (net_base * qty / base_qty).quantize(QTY, rounding=ROUND_HALF_UP) if base_qty > 0 else qty
        out.append(
            LineCheck(
                ln["line_no"], ln["item_code"], ln["name"], qty, ln["uom"], base_qty,
                on_hand.quantize(QTY), open_requests.quantize(QTY), open_po.quantize(QTY), inbound.quantize(QTY), suggested,
            )
        )  # fmt: skip
    return out


# ---- is it a repeat? --------------------------------------------------------------------------


def _words(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", " ", s.lower()).strip()


def text_similarity(a: str, b: str) -> float:
    """How alike two wordings are, 0 to 1: the better of character-level likeness and shared words."""
    x, y = _words(a), _words(b)
    if not x or not y:
        return 0.0
    chars = difflib.SequenceMatcher(None, x, y).ratio()
    tx, ty = set(x.split()), set(y.split())
    return max(chars, len(tx & ty) / len(tx | ty))


def _close(a: date | None, b: date | None, window: int) -> bool:
    return a is None or b is None or abs((a - b).days) <= window


def find_duplicates(db: Session, request_no: str) -> list[Duplicate]:
    """The other requests this one looks like a repeat of, most alike first (it does not write anything)."""
    me = db.execute(
        text("select (created_at at time zone 'Asia/Kolkata')::date as day from request where no = :n"),
        {"n": request_no},
    ).first()
    if me is None:
        raise DuplicateError(f"request {request_no} does not exist")
    window = int(_setting(db, "duplicate_window_days", "14"))
    mine = _lines(db, request_no)
    if not mine:
        return []
    others = db.execute(
        text(
            "select o.no, o.status, ol.line_no, ol.item_code, coalesce(i.name, ol.free_text) as name, ol.qty, "
            "coalesce(ol.need_by, o.need_by) as need_by, coalesce(ol.delivery_location, o.ship_to_location) as loc "
            "from request o join request_line ol on ol.request_no = o.no left join item i on i.code = ol.item_code "
            "where o.no <> :n and o.status not in ('cancelled', 'rejected') and ol.status <> 'cancelled' "
            "and abs((o.created_at at time zone 'Asia/Kolkata')::date - :d) <= :w "
            "and coalesce(ol.delivery_location, o.ship_to_location) = any(:locs) order by o.no, ol.line_no"
        ),
        {"n": request_no, "d": me[0], "w": window, "locs": sorted({ln["loc"] for ln in mine})},
    ).mappings()
    by_request: dict[str, list[dict[str, Any]]] = {}
    for r in others:
        by_request.setdefault(r["no"], []).append(dict(r))
    found: list[Duplicate] = []
    for no, lines in by_request.items():
        pairs: list[dict[str, Any]] = []
        used: set[int] = set()
        for ln in mine:
            for other in lines:
                if (
                    other["line_no"] in used
                    or other["loc"] != ln["loc"]
                    or not _close(ln["need_by"], other["need_by"], window)
                ):
                    continue
                if ln["item_code"]:
                    same = other["item_code"] == ln["item_code"]
                    alike = 1.0 if same else 0.0
                else:
                    same = False
                    alike = text_similarity(ln["name"] or "", other["name"] or "")
                if (ln["item_code"] and same) or (not ln["item_code"] and alike >= TEXT_SIMILARITY):
                    used.add(other["line_no"])
                    pairs.append(
                        {
                            "line_no": ln["line_no"],
                            "other_line_no": other["line_no"],
                            "name": ln["name"],
                            "same_qty": D(ln["qty"]) == D(other["qty"]),
                            "wording": round(alike, 2) if not ln["item_code"] else None,
                        }
                    )
                    break
        if not pairs:
            continue
        total = max(len(mine), len(_lines(db, no)))
        similarity = (D(len(pairs)) / D(total)).quantize(D("0.001"))
        kind = (
            "same_items"
            if any(ln["item_code"] for ln in mine if ln["line_no"] in {p["line_no"] for p in pairs})
            else "similar_text"
        )
        found.append(Duplicate(no, lines[0]["status"], similarity, kind, pairs))
    found.sort(key=lambda d: (-d.similarity, d.other_request))
    return found


# ---- flags, overrides and the gate at submission ----------------------------------------------


def record_flags(db: Session, request_no: str, dups: list[Duplicate]) -> None:
    for d in dups:
        db.execute(
            text(
                "insert into request_check (request_no, kind, other_request, similarity, detail) "
                "values (:n, 'duplicate', :o, :s, cast(:d as jsonb)) "
                "on conflict (request_no, kind, other_request) do update set similarity = :s, detail = cast(:d as jsonb)"
            ),
            {"n": request_no, "o": d.other_request, "s": d.similarity, "d": _json({"lines": d.lines, "kind": d.kind})},
        )


def _json(value: Any) -> str:
    return json.dumps(value, default=str)


def with_overrides(db: Session, request_no: str, dups: list[Duplicate]) -> list[Duplicate]:
    given = {
        r[0]: r[1]
        for r in db.execute(
            text(
                "select other_request, override_reason from request_check "
                "where request_no = :n and kind = 'duplicate' and override_reason is not null"
            ),
            {"n": request_no},
        )
    }
    for d in dups:
        if d.other_request in given:
            d.overridden, d.override_reason = True, given[d.other_request]
    return dups


def override(db: Session, request_no: str, reason: str, user_id: int) -> list[Duplicate]:
    """A person says why the request goes ahead although it looks like a repeat. The reason is kept with their name."""
    if not (reason or "").strip():
        raise DuplicateError("a reason is needed to go ahead with a request that looks like a repeat")
    dups = find_duplicates(db, request_no)
    if not dups:
        raise DuplicateError(f"{request_no} does not look like a repeat of anything")
    record_flags(db, request_no, dups)
    db.execute(
        text(
            "update request_check set override_reason = :r, overridden_by = :u, overridden_at = now() "
            "where request_no = :n and kind = 'duplicate' and other_request = any(:o)"
        ),
        {"r": reason.strip(), "u": str(user_id), "n": request_no, "o": [d.other_request for d in dups]},
    )
    audit.record(
        db, user(user_id), "request.duplicate_override", "request", request_no,
        after={"repeats": [d.other_request for d in dups]}, reason=reason.strip(),
    )  # fmt: skip
    db.commit()
    return with_overrides(db, request_no, dups)


def gate(db: Session, request_no: str, override_reason: str | None, user_id: int) -> list[Duplicate]:
    """Called before a request is submitted by a person. Returns the repeats that were gone ahead with; raises when some
    have not been given a reason."""
    dups = find_duplicates(db, request_no)
    if not dups:
        return []
    record_flags(db, request_no, dups)
    db.commit()
    dups = with_overrides(db, request_no, dups)
    if override_reason and override_reason.strip() and any(not d.overridden for d in dups):
        dups = override(db, request_no, override_reason, user_id)
    open_ = [d for d in dups if not d.overridden]
    if open_:
        names = ", ".join(
            f"{d.other_request} ({'a likely repeat' if d.likely else 'possibly a repeat'}, {len(d.lines)} line(s) alike)"
            for d in open_
        )
        raise DuplicateError(f"{request_no} looks like a repeat of {names}. Give a reason to go ahead.", open_)
    return dups
