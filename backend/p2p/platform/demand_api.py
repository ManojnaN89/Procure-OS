"""Demand from the ERP: our templates to download, a file to upload (checked, or loaded, with the changes it applied),
the column mapping per kind of file, the imports and changes so far, and closing a case a change left for a person."""

import tempfile
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.platform import files, ratelimit, routing
from p2p.platform.permissions import need_any, require
from p2p.platform.sod import user
from p2p.requests import demand, demand_cases
from p2p.requests.demand_files import KINDS

router = APIRouter(prefix="/api/demand", tags=["demand"])
Db = Annotated[Session, Depends(routing.tenant_session)]
Importer = Annotated[routing.TenantContext, Depends(require("demand.import"))]
Ctx = Annotated[routing.TenantContext, Depends(routing.tenant_context)]


def _kind(kind: str) -> str:
    if kind not in KINDS:
        raise HTTPException(404, f"There is no kind of file called {kind}. Choose one of: {', '.join(KINDS)}.")
    return kind


@router.get("/kinds")
def kinds(db: Db, ctx: Importer) -> list[dict[str, Any]]:
    """Each kind of ERP file with our columns and the client's names for them (the mapping in use)."""
    out = []
    for f in KINDS.values():
        version, mapping = demand.current_mapping(db, f.kind)
        out.append(
            {
                "kind": f.kind,
                "title": f.title,
                "columns": [c for c, _ in f.columns],
                "required": list(f.required),
                "mapping_version": version,
                "mapping": mapping,
            }
        )
    return out


@router.get("/templates/{kind}")
def template(kind: str, ctx: Importer) -> Response:
    body = "﻿" + demand.template_csv(_kind(kind))
    return Response(
        content=body.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{kind}.csv"'},
    )


@router.put("/mappings/{kind}")
def save_mapping(kind: str, payload: dict[str, Any], db: Db, ctx: Importer) -> dict[str, Any]:
    """A new version of the mapping: {"columns": {our column: the client's column}}."""
    columns = payload.get("columns")
    if not isinstance(columns, dict):
        raise HTTPException(422, "columns must map our column names to the client's")
    try:
        version = demand.save_mapping(db, user(ctx.user_id), _kind(kind), {str(k): str(v) for k, v in columns.items()})
    except demand.DemandError as exc:
        raise HTTPException(422, str(exc)) from None
    return {"kind": kind, "version": version}


@router.post("/import/{kind}", dependencies=[ratelimit.limited("upload")])
async def import_file(kind: str, file: UploadFile, db: Db, ctx: Importer, dry_run: bool = False) -> dict[str, Any]:
    """Check (dry_run) or load one ERP file, CSV or Excel. Every row left out comes back with its line and reason."""
    _kind(kind)
    name = (file.filename or "").lower()
    suffix = ".xlsx" if name.endswith(".xlsx") else ".csv" if name.endswith(".csv") else None
    if suffix is None:
        raise HTTPException(422, "an ERP file must be a .csv or .xlsx file")
    data = await file.read(files.MAX_BYTES + 1)
    if len(data) > files.MAX_BYTES:
        raise HTTPException(413, "File is too large")
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / f"{kind}{suffix}"
        path.write_bytes(data)
        report = demand.import_demand(db, path, actor=user(ctx.user_id), dry_run=dry_run, kind=kind, tenant=ctx.slug)
    return {
        "summary": demand.summary(report),
        "dry_run": report.dry_run,
        "import_id": report.import_id,
        "loaded": report.rows_loaded,
        "changed": report.rows_changed,
        "already_loaded": report.already_loaded,
        "mapping_version": report.mappings.get(kind, 0),
        "material_requests": report.material_requests,
        "changes": [c.view() for c in report.changes],
        "warnings": [p.reason for p in report.warnings],
        "rejected": [{"line": p.line, "reason": p.reason} for p in report.rejected],
    }


@router.get("/changes")
def changes(db: Db, ctx: Ctx, import_id: int | None = None, doc_no: str | None = None) -> dict[str, Any]:
    """The changes the ERP's files made, newest first: what changed and what it did (requests cut, stock released, cases)."""
    need_any(db, ctx.user_id, "demand.import", "stock.view")
    return {"changes": demand.list_changes(db, import_id=import_id, doc_no=doc_no)}


@router.post("/cases/{task_id}/close")
def close_case(task_id: int, payload: dict[str, Any], db: Db, ctx: Ctx) -> dict[str, Any]:
    """The owner of a case a change left (or someone who acts for them) says what was done: {"outcome", "note"}."""
    try:
        return demand_cases.close(
            db,
            ctx.slug,
            ctx.user_id,
            task_id,
            str(payload.get("outcome") or ""),
            payload.get("note") and str(payload["note"]),
        )
    except demand_cases.CaseError as exc:
        raise HTTPException(exc.status, str(exc)) from None


@router.get("/imports")
def imports(db: Db, ctx: Importer) -> list[dict[str, Any]]:
    rows = db.execute(
        text(
            "select id, source, files, loaded, changed, already_loaded, rejected, imported_by, imported_at "
            "from demand_import order by id desc limit 50"
        )
    ).mappings()
    return [{**r, "imported_at": r["imported_at"].isoformat()} for r in rows]
