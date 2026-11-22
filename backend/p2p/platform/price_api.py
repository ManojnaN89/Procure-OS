"""Prices kept apart and the market as evidence (tasks 13.1 and 12.5): market benchmarks (enter, upload, verify, reject),
the price view of an item at a location, and strategic reviews (open, list, close). Services in sourcing/benchmarks.py,
sourcing/price_view.py and sourcing/price_review.py."""

import tempfile
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.formulas.calendar import business_today
from p2p.formulas.scope import Scope
from p2p.formulas.scope import label as scope_label
from p2p.masters import regions
from p2p.platform import files, ratelimit, routing, worker
from p2p.platform.permissions import can_anywhere, need_any, require
from p2p.platform.sod import user
from p2p.sourcing import benchmarks, price_review, price_view

router = APIRouter(prefix="/api", tags=["prices"])
Db = Annotated[Session, Depends(routing.tenant_session)]
Viewer = Annotated[routing.TenantContext, Depends(require("price.view"))]
Enterer = Annotated[routing.TenantContext, Depends(require("benchmark.enter"))]
Verifier = Annotated[routing.TenantContext, Depends(require("benchmark.verify"))]
Buyer = Annotated[routing.TenantContext, Depends(require("rfq.create"))]
Ctx = Annotated[routing.TenantContext, Depends(routing.tenant_context)]


def _http(exc: benchmarks.BenchmarkError | price_review.ReviewError) -> HTTPException:
    return HTTPException(exc.status, str(exc))


# ---- benchmarks ----------------------------------------------------------------------------------


@router.get("/benchmarks")
def list_benchmarks(
    db: Db, ctx: Viewer, item: str | None = None, status: str | None = None, region: str | None = None
) -> dict[str, Any]:
    """Benchmarks with who entered and verified them, their age, and whether each counts today for its own region."""
    today = business_today()
    rule = price_review.rule_for(db, None, today)
    rows = benchmarks.records(db, item=item, status=status, region=region)
    out = [
        benchmarks.described(
            db, [r], on=today, max_age_days=rule.evidence_max_age_days,
            scope=Scope.of(regions=[r["region_code"]]) if r["region_code"] else Scope(), uom=None,
        )[0]
        for r in rows
    ]  # fmt: skip
    return {
        "benchmarks": out,
        "on": today.isoformat(),
        "evidence_max_age_days": rule.evidence_max_age_days,
        "can_enter": can_anywhere(db, ctx.user_id, "benchmark.enter"),
        "can_verify": can_anywhere(db, ctx.user_id, "benchmark.verify"),
    }


def _fields(payload: dict[str, Any]) -> dict[str, Any]:
    data: dict[str, Any] = {k: payload.get(k) for k, _ in benchmarks.COLUMNS if payload.get(k) not in (None, "")}
    if "price" in data:
        data["price"] = benchmarks.parse_price(data["price"])
        if data["price"] is None:
            raise HTTPException(422, "price is not a number")
    if "effective_date" in data:
        try:
            data["effective_date"] = date.fromisoformat(str(data["effective_date"]))
        except ValueError:
            raise HTTPException(422, "effective_date is a date like 2026-10-31") from None
    for key in ("document_id", "replaces"):
        if payload.get(key) not in (None, ""):
            try:
                data[key] = int(payload[key])
            except (TypeError, ValueError):
                raise HTTPException(422, f"{key} is a number") from None
    return data


@router.post("/benchmarks")
def add_benchmark(payload: dict[str, Any], db: Db, ctx: Enterer) -> dict[str, Any]:
    try:
        bid = benchmarks.add(db, _fields(payload), ctx.user_id)
    except benchmarks.BenchmarkError as exc:
        db.rollback()
        raise _http(exc) from None
    return {"id": bid, "status": "unverified"}


@router.put("/benchmarks/{bid}")
def edit_benchmark(bid: int, payload: dict[str, Any], db: Db, ctx: Enterer) -> dict[str, Any]:
    """Correct an unverified benchmark; a verified one is never edited (409): enter a correction that replaces it."""
    try:
        benchmarks.edit(db, bid, _fields(payload), ctx.user_id)
    except benchmarks.BenchmarkError as exc:
        db.rollback()
        raise _http(exc) from None
    return {"id": bid, "status": "unverified"}


@router.get("/benchmarks/template")
def benchmark_template(ctx: Enterer) -> Response:
    return Response(
        content=("﻿" + benchmarks.template_csv()).encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="market_benchmark.csv"'},
    )


@router.post("/benchmarks/upload", dependencies=[ratelimit.limited("upload")])
async def upload_benchmarks(file: UploadFile, db: Db, ctx: Enterer) -> dict[str, Any]:
    """A sheet in our template (CSV or Excel): good rows become unverified records; bad rows come back with the reason."""
    name = (file.filename or "").lower()
    suffix = ".xlsx" if name.endswith(".xlsx") else ".csv" if name.endswith(".csv") else None
    if suffix is None:
        raise HTTPException(415, "a benchmark sheet is a .csv or .xlsx file in our template")
    data = await file.read(files.MAX_BYTES + 1)
    if len(data) > files.MAX_BYTES:
        raise HTTPException(413, "File is too large")
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / f"market_benchmark{suffix}"
        path.write_bytes(data)
        try:
            report = benchmarks.upload(db, path, ctx.user_id)
        except benchmarks.BenchmarkError as exc:
            db.rollback()
            raise _http(exc) from None
    return {
        "loaded": report.loaded,
        "skipped": report.skipped,
        "changed": report.changed,
        "rejected": report.rejected,
    }


@router.post("/benchmarks/{bid}/verify")
def verify_benchmark(bid: int, payload: dict[str, Any], db: Db, ctx: Verifier) -> dict[str, Any]:
    try:
        item = benchmarks.verify(db, bid, ctx.user_id, payload.get("note"))
    except benchmarks.BenchmarkError as exc:
        db.rollback()
        raise _http(exc) from None
    worker.enqueue(ctx.slug, "price_review_item", {"item": item})  # a new fact: test that item's contract prices
    for place in _stock_places(db, item):  # and whether it is now worth buying ahead (task 13.5)
        worker.enqueue_unique(
            ctx.slug, "forward_buy_item", {"item": item, "location": place}, datetime.now(UTC), same_payload=True
        )
    return {"id": bid, "status": "verified"}


def _stock_places(db: Session, item: str) -> list[str]:
    return [
        str(r[0])
        for r in db.execute(
            text(
                "select il.location_code from item_location il join item i on i.code = il.item_code where il.item_code = :i and i.stocked"
            ),
            {"i": item},
        )
    ]


@router.post("/benchmarks/{bid}/reject")
def reject_benchmark(bid: int, payload: dict[str, Any], db: Db, ctx: Verifier) -> dict[str, Any]:
    try:
        benchmarks.reject(db, bid, ctx.user_id, str(payload.get("reason") or ""))
    except benchmarks.BenchmarkError as exc:
        db.rollback()
        raise _http(exc) from None
    return {"id": bid, "status": "rejected"}


# ---- the price view ------------------------------------------------------------------------------


@router.get("/items/{code}/prices")
def item_prices(code: str, db: Db, ctx: Viewer, location: str | None = None) -> dict[str, Any]:
    """Each price type in its own section, never one in place of another (D41)."""
    found = price_view.for_item(db, code, location, business_today())
    if found is None:
        raise HTTPException(404, "Not found")
    return found


@router.get("/contracts/{code}/market")
def contract_market(code: str, db: Db, ctx: Viewer) -> dict[str, Any]:
    """The Market panel of a contract: per price in force, the latest counting benchmark and the gap (evidence only),
    the rule version, whether it is above the thresholds, and the open review. The contract stays valid either way."""
    today = business_today()
    rows = db.execute(
        text(
            "select p.id, p.item_code from price_record p where p.contract_code = :c and p.status = 'active' "
            "and p.valid_from <= :d and (p.valid_to is null or p.valid_to >= :d) order by p.item_code, p.id"
        ),
        {"c": code, "d": today},
    ).all()
    reviews = [r for r in price_review.listing(db) if r.get("contract") == code]
    out = []
    for rid, item in rows:
        a = price_review.assess(db, int(rid), today)
        counting = [d for d in a.described if d["counts"]]
        latest = max(counting, key=lambda d: d["effective_date"], default=None)
        g = a.gap
        out.append(
            {
                "price_record": rid, "item_code": item, "contract_price": str(a.row["price"]), "uom": a.row["uom"],
                "where": scope_label(a.scope, regions.names(db)), "latest_benchmark": latest,
                "market": None if g.market is None else str(g.market),
                "gap_inr": None if g.gap_inr is None else str(g.gap_inr),
                "gap_pct": None if g.gap_pct is None else str(g.gap_pct),
                "saving": None if g.saving is None else str(g.saving), "relevant_qty": str(a.qty),
                "above_thresholds": g.goes_ahead, "why": g.why, "rule": a.rule.version,
                "review": next((r for r in reviews if r.get("item") == item), None),
            }
        )  # fmt: skip
    return {"contract": code, "on": today.isoformat(), "prices": out, "reviews": reviews, "label": "evidence only"}


# ---- strategic reviews ---------------------------------------------------------------------------


def _reviewer(ctx: Ctx, db: Db) -> routing.TenantContext:
    need_any(db, ctx.user_id, "rfq.create", "contract.activate")
    return ctx


@router.get("/reviews")
def list_reviews(
    db: Db, ctx: Annotated[routing.TenantContext, Depends(_reviewer)], status: str = "open"
) -> list[dict[str, Any]]:
    return price_review.listing(db, status)


@router.post("/reviews")
def open_review(payload: dict[str, Any], db: Db, ctx: Buyer) -> dict[str, Any]:
    """A buyer asks for a strategic review of a contract or an item: it always opens one (or joins the open one)."""
    if payload.get("trigger", "buyer") != "buyer":
        raise HTTPException(422, "a person opens a review with the trigger buyer")
    try:
        task_id, joined = price_review.buyer_trigger(
            db, ctx.slug, user(ctx.user_id), contract=payload.get("contract_code") or None,
            item=payload.get("item_code") or None, reason=str(payload.get("reason") or ""),
        )  # fmt: skip
    except price_review.ReviewError as exc:
        db.rollback()
        raise _http(exc) from None
    return {"task_id": task_id, "joined": joined}


@router.post("/reviews/{task_id}/close")
def close_review(task_id: int, payload: dict[str, Any], db: Db, ctx: Buyer) -> dict[str, Any]:
    try:
        price_review.close(
            db, task_id, ctx.user_id, str(payload.get("outcome") or ""), str(payload.get("reason") or "")
        )
    except price_review.ReviewError as exc:
        db.rollback()
        raise _http(exc) from None
    return {"task_id": task_id, "status": "done"}
