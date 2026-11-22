"""The company's state, its readiness, going live, and the cutover import (block 0d) for the admin app.

Going live is a control change (D-22): one administrator proposes it, another approves it, and readiness is checked both
times. Loading the open documents of the old system is for the people with `company.cutover`, only in cutover.
"""

import tempfile
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from p2p.platform import audit, company_state, cutover_import, ratelimit, readiness, routing, setting_changes, tenants
from p2p.platform.control_models import Tenant
from p2p.platform.files import MAX_BYTES
from p2p.platform.permissions import can_anywhere, need_any, require
from p2p.platform.rows import rows as _rows
from p2p.platform.setting_changes import Change
from p2p.platform.sod import Actor
from p2p.platform.sod import user as user_actor

router = APIRouter(prefix="/api/company", tags=["company"])
Db = Annotated[Session, Depends(routing.tenant_session)]
Ctx = Annotated[routing.TenantContext, Depends(routing.tenant_context)]
Loader = Annotated[routing.TenantContext, Depends(require("company.cutover"))]
SettingsAdmin = Annotated[routing.TenantContext, Depends(require("admin.settings"))]
READERS = ("admin.users", "admin.rules", "admin.settings", "audit.view")


class GoLive(BaseModel):
    reason: str = ""


@router.get("/state")
def state(db: Db, ctx: Ctx) -> dict[str, Any]:
    """The company's state and since when; its history for administrators and auditors."""
    with tenants.control_session() as s:
        row = s.scalars(select(Tenant).where(Tenant.slug == ctx.slug)).one()
        found = {
            "slug": row.slug, "name": row.name, "state": row.state, "since": row.state_changed_at.isoformat(),
            "before_suspension": row.state_before_suspension, "banner": company_state.banner(row.state),
        }  # fmt: skip
    may = any(can_anywhere(db, ctx.user_id, a) for a in READERS)
    return {**found, "history": company_state.history(ctx.slug) if may else []}


@router.get("/readiness")
def get_readiness(db: Db, ctx: Ctx, group: str = "all") -> dict[str, Any]:
    need_any(db, ctx.user_id, *READERS)
    if group not in readiness.GROUPS:
        raise HTTPException(422, f"group must be one of {', '.join(readiness.GROUPS)}")
    return readiness.check(db, ctx.slug, group=group).view()


def _not_ready(result: readiness.Readiness) -> JSONResponse:
    return JSONResponse({"detail": "not ready", "blockers": result.blockers}, status_code=422)


@router.post("/go-live", status_code=202, response_model=None)
def go_live(body: GoLive, db: Db, ctx: SettingsAdmin) -> dict[str, Any] | JSONResponse:
    """Propose going live: another administrator approves it, and readiness is checked again then."""
    if not body.reason.strip():
        raise HTTPException(422, "a reason is needed")
    current = company_state.state_of(ctx.slug)
    if current != "cutover":
        raise HTTPException(409, f"the company is {current}; only a company in cutover goes live")
    result = readiness.check(db, ctx.slug)
    if not result.ready:
        return _not_ready(result)
    return setting_changes.submit(
        db, user_actor(ctx.user_id), "company_go_live", {}, body.reason.strip(), tenant=ctx.slug
    )


@setting_changes.handler("company_go_live", "admin.settings")
def _go_live(db: Session, actor: Actor, args: dict[str, Any], reason: str, apply: bool) -> Change:
    slug = company_state.slug_of_session(db)
    current = company_state.state_of(slug)
    if current != "cutover":
        raise HTTPException(409, f"the company is {current}; only a company in cutover goes live")
    result = readiness.check(db, slug)
    if not result.ready:
        raise HTTPException(422, f"not ready: {', '.join(result.blockers)}")
    change = Change(area="company", key="state", old="cutover", new="live", result={"state": "live"})
    if not apply:
        return change
    from p2p.platform.setup_api import record_history  # noqa: PLC0415 - the setup screens build on this module

    audit.record(db, actor, "company.readiness", "company", slug, after=result.view(), reason=reason)
    company_state.move(slug, "live", actor=actor, approved_by=setting_changes.APPROVED_BY.get(), reason=reason, db=db)
    record_history(db, actor, "company", "state", "cutover", "live", reason)
    return change


# ---- the cutover import ---------------------------------------------------------------------------------------------


@router.post("/cutover/import", dependencies=[ratelimit.limited("upload")])
async def cutover_files(
    db: Db, ctx: Loader, files: Annotated[list[UploadFile], File()], dry_run: Annotated[bool, Form()] = True
) -> dict[str, Any]:
    """Check (dry run, the default) or load the open documents: the files of the cutover templates, CSV or Excel."""
    with tempfile.TemporaryDirectory() as folder:
        for upload in files:
            name = Path(upload.filename or "").name.lower()
            stem, suffix = Path(name).stem, Path(name).suffix
            if stem not in cutover_import.FILES or suffix not in (".csv", ".xlsx"):
                raise HTTPException(422, f"{name or 'a file'} is not a cutover file: {', '.join(cutover_import.FILES)}")
            data = await upload.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                raise HTTPException(413, "File is too large")
            (Path(folder) / f"{stem}{suffix}").write_bytes(data)
        report = cutover_import.import_cutover(db, Path(folder), user_id=ctx.user_id, dry_run=dry_run)
    return report.view()


@router.get("/cutover/imports")
def imports(db: Db, ctx: Loader) -> list[dict[str, Any]]:
    return [
        {**r, "at": r["at"].isoformat()} for r in _rows(db, "select * from cutover_import order by id desc limit 50")
    ]


@router.get("/cutover/templates/{kind}")
def template(kind: str, ctx: Loader) -> Response:
    columns = cutover_import.FILES.get(kind)
    if columns is None:
        raise HTTPException(404, f"no cutover template {kind}")
    return Response(
        ",".join(columns) + "\n",
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{kind}.csv"'},
    )
