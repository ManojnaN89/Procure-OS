"""Uploading and downloading files. Every file belongs to one tenant and is only served to a logged-in user of it."""

import hashlib
import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, UploadFile
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.platform import audit, ratelimit, routing, storage
from p2p.platform.sod import user as user_actor

MAX_BYTES = 25 * 1024 * 1024

router = APIRouter(prefix="/api/files", tags=["files"])


class FileOut(BaseModel):
    id: int
    filename: str
    content_type: str
    size: int
    sha256: str


def safe_filename(name: str) -> str:
    """Keep the name readable but harmless in a header and on disk."""
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r"[^\w.\- ]+", "_", name).strip(" .")
    return (name if name.strip("_. ") else "file")[:150]


def save_file(
    db: Session,
    tenant: str,
    data: bytes,
    *,
    filename: str,
    content_type: str,
    doc_type: str,
    uploaded_by: int | None,
    linked_type: str | None = None,
    linked_id: str | None = None,
    commit: bool = True,
) -> FileOut:
    """Store the bytes, add the document row and audit it. Used by the upload route and by services (email, parsing).
    `commit=False` leaves the row in the caller's transaction (the email outbox)."""
    if len(data) > MAX_BYTES:
        raise ValueError("file is too large")
    digest = hashlib.sha256(data).hexdigest()
    key = storage.get_storage().put(tenant, data)
    name = safe_filename(filename)
    doc_id = int(
        db.execute(
            text(
                "insert into document (doc_type, filename, content_type, size, sha256, storage_key, linked_type, linked_id, "
                "uploaded_by) values (:dt, :fn, :ct, :sz, :h, :k, :lt, :li, :u) returning id"
            ),
            {
                "dt": doc_type,
                "fn": name,
                "ct": content_type,
                "sz": len(data),
                "h": digest,
                "k": key,
                "lt": linked_type,
                "li": linked_id,
                "u": uploaded_by,
            },
        ).scalar_one()
    )
    actor = user_actor(uploaded_by) if uploaded_by is not None else audit.SYSTEM
    audit.record(
        db, actor, "file.upload", "document", doc_id, after={"filename": name, "size": len(data), "sha256": digest}
    )
    if commit:
        db.commit()
    return FileOut(id=doc_id, filename=name, content_type=content_type, size=len(data), sha256=digest)


@router.post("", status_code=201, dependencies=[ratelimit.limited("upload")])
async def upload(
    file: UploadFile,
    ctx: Annotated[routing.TenantContext, Depends(routing.tenant_context)],
    db: Annotated[Session, Depends(routing.tenant_session)],
    doc_type: str = "attachment",
) -> FileOut:
    data = await file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "File is too large")
    return save_file(
        db,
        ctx.slug,
        data,
        filename=file.filename or "file",
        content_type=file.content_type or "application/octet-stream",
        doc_type=doc_type,
        uploaded_by=ctx.user_id,
    )


@router.get("/{file_id}")
def download(
    file_id: int,
    ctx: Annotated[routing.TenantContext, Depends(routing.tenant_context)],
    db: Annotated[Session, Depends(routing.tenant_session)],
) -> Response:
    row = db.execute(
        text("select filename, content_type, storage_key from document where id = :i"), {"i": file_id}
    ).first()
    if row is None:
        raise HTTPException(404, "File not found")
    try:
        data = storage.get_storage().get(ctx.slug, row[2])
    except storage.StorageError:
        raise HTTPException(404, "File not found") from None
    return Response(
        data,
        media_type=row[1],
        headers={"Content-Disposition": f'attachment; filename="{row[0]}"', "X-Content-Type-Options": "nosniff"},
    )
