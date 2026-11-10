"""Invoices read from documents: upload one, see what was read, confirm or dismiss the draft."""

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.formulas.calendar import business_today
from p2p.invoices import reading
from p2p.invoices import service as invoices
from p2p.platform import files, routing
from p2p.platform.permissions import require

router = APIRouter(prefix="/api", tags=["invoice-intake"])
Db = Annotated[Session, Depends(routing.tenant_session)]
Ctx = Annotated[routing.TenantContext, Depends(routing.tenant_context)]
Book = Annotated[routing.TenantContext, Depends(require("invoice.book"))]


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _row(d: dict[str, Any], detail: bool = False) -> dict[str, Any]:
    proposal = d["proposal"] or {}
    out = {
        "id": d["id"],
        "source": d["source"],
        "filename": d["filename"],
        "supplier_code": d["supplier_code"],
        "supplier": d.get("supplier"),
        "status": d["status"],
        "path": d["path"],
        "number": proposal.get("invoice_number"),
        "date": proposal.get("invoice_date"),
        "type": proposal.get("doc_type"),
        "total": proposal.get("total"),
        "problems": d["problems"],
        "assumptions": d["assumptions"],
        "confidence": str(d["confidence"]) if d["confidence"] is not None else None,
        "invoice": d["invoice_no"],
        "created_at": _iso(d["created_at"]),
    }
    if detail:
        out |= {
            "lines": proposal.get("lines", []),
            "charges": proposal.get("charges", []),
            "supplier_gstin": proposal.get("supplier_gstin"),
            "buyer_gstin": proposal.get("buyer_gstin"),
            "irn": proposal.get("irn"),
            "qr": d["qr"],
            "model": d["model"],
            "decided_by": d["decided_by"],
            "decided_at": _iso(d["decided_at"]),
        }
    return out


_SELECT = (
    "select d.*, coalesce(s.trade_name, s.legal_name) as supplier from invoice_draft d "
    "left join supplier s on s.code = d.supplier_code"
)


@router.get("/invoice-drafts")
def list_drafts(db: Db, ctx: Ctx, status: str | None = None) -> list[dict[str, Any]]:
    rows = db.execute(
        text(
            f"{_SELECT} where (cast(:s as text) is null and d.status in ('pending', 'needs_person') or d.status = :s) "
            "order by d.id desc limit 200"
        ),
        {"s": status},
    ).mappings()
    return [_row(dict(r)) for r in rows]


@router.get("/invoice-drafts/{draft_id}")
def get_draft(draft_id: int, db: Db, ctx: Ctx) -> dict[str, Any]:
    row = db.execute(text(f"{_SELECT} where d.id = :i"), {"i": draft_id}).mappings().first()
    if row is None:
        raise HTTPException(404, "draft not found")
    return _row(dict(row), detail=True)


@router.post("/invoice-drafts/upload", status_code=201)
def upload_invoice(file: UploadFile, db: Db, ctx: Book, supplier_code: str | None = None) -> dict[str, Any]:
    """An AP clerk uploads an invoice document: it is read now, and the draft is returned."""
    name = file.filename or "invoice"
    if not name.lower().endswith(reading.SUPPORTED):
        raise HTTPException(422, "upload a PDF or an image (png, jpg, tif, bmp)")
    data = file.file.read(files.MAX_BYTES + 1)
    if len(data) > files.MAX_BYTES:
        raise HTTPException(413, "File is too large")
    if not data:
        raise HTTPException(422, "the file is empty")
    if supplier_code and not db.execute(text("select 1 from supplier where code = :c"), {"c": supplier_code}).first():
        raise HTTPException(404, f"supplier {supplier_code} does not exist")
    from p2p.platform import ui_api  # noqa: PLC0415 - the shared gateway, so rate limits are shared

    stored = files.save_file(
        db, ctx.slug, data, filename=name, content_type=file.content_type or "application/octet-stream",
        doc_type="invoice_document", uploaded_by=ctx.user_id, linked_type="invoice_draft", linked_id=None,
    )  # fmt: skip
    draft_id = reading.read_document(
        db, ctx.slug, data, name, source="upload", gateway=ui_api.assistant_gateway(), as_of=business_today(),
        document_id=stored.id, supplier_code=supplier_code, user_id=ctx.user_id, received_at=datetime.now(UTC),
    )  # fmt: skip
    row = db.execute(text(f"{_SELECT} where d.id = :i"), {"i": draft_id}).mappings().one()
    return _row(dict(row), detail=True)


@router.post("/invoice-drafts/{draft_id}/confirm")
def confirm_draft(draft_id: int, payload: dict[str, Any], db: Db, ctx: Book) -> dict[str, Any]:
    """AP enters the invoice of a draft, optionally correcting what was read: header fields and lines by position."""
    edits = payload.get("edits")
    if edits is not None and not isinstance(edits, dict):
        raise HTTPException(422, "edits must be an object")
    try:
        done = reading.confirm_draft(db, draft_id, ctx.user_id, as_of=business_today(), edits=edits)
    except reading.ReadingError as exc:
        raise HTTPException(404 if "does not exist" in str(exc) else 409, str(exc)) from None
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        db.rollback()
        raise HTTPException(422, f"the correction could not be read: {exc}") from None
    except invoices.InvoiceError as exc:
        message = str(exc)
        raise HTTPException(
            404 if "does not exist" in message else 403 if message.startswith("segregation") else 409, message
        ) from None
    return {"invoice": done.no, "status": done.status, "result": done.result, "reasons": done.reasons}


@router.post("/invoice-drafts/{draft_id}/dismiss")
def dismiss_draft(draft_id: int, payload: dict[str, Any], db: Db, ctx: Book) -> dict[str, str]:
    try:
        reading.dismiss_draft(db, draft_id, ctx.user_id, payload.get("note"))
    except reading.ReadingError as exc:
        raise HTTPException(404 if "does not exist" in str(exc) else 409, str(exc)) from None
    return {"status": "dismissed"}
