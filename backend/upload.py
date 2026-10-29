"""
File upload + extraction endpoint. This does NOT write anything to the
database and does NOT call the LLM — it just turns a PDF/Excel/CSV into
plain text or structured rows. The frontend sends that extracted content
into the chat as part of the user's message, and the existing agents/skills
(record_quote, import_vendors_csv) handle the rest — no separate "parsing
skill" needed, since the agent's own LLM call already reads the extracted
text in-context and fills in skill arguments accordingly.
"""
import csv
import io

from fastapi import APIRouter, UploadFile, File

router = APIRouter(prefix="/api")

MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB, generous for a quote/vendor-list document


@router.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    contents = await file.read()
    if len(contents) > MAX_FILE_SIZE:
        return {"error": "File too large (5MB limit)"}

    filename = file.filename or "upload"
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    if ext == "pdf":
        return _extract_pdf(contents, filename)
    if ext in ("xlsx", "xls"):
        return _extract_excel(contents, filename)
    if ext == "csv":
        return _extract_csv(contents, filename)

    return {"error": f"Unsupported file type: .{ext}. Supported: pdf, xlsx, xls, csv"}


def _extract_pdf(contents: bytes, filename: str):
    import pdfplumber

    text_parts = []
    try:
        with pdfplumber.open(io.BytesIO(contents)) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)
    except Exception as exc:
        return {"error": f"Could not read PDF: {exc}"}

    full_text = "\n".join(text_parts).strip()
    if not full_text:
        return {"error": "No extractable text found in this PDF (it may be a scanned image)."}

    return {"filename": filename, "type": "pdf", "extracted_text": full_text}


def _extract_excel(contents: bytes, filename: str):
    import openpyxl

    try:
        wb = openpyxl.load_workbook(io.BytesIO(contents), data_only=True)
        ws = wb.active
    except Exception as exc:
        return {"error": f"Could not read Excel file: {exc}"}

    rows = []
    for row in ws.iter_rows(values_only=True):
        if any(cell is not None for cell in row):
            rows.append([str(cell) if cell is not None else "" for cell in row])

    # Also render as plain text so the LLM can read it naturally in-context,
    # same as the PDF branch — rows are kept too in case a future skill wants
    # structured access instead of parsing text.
    text_repr = "\n".join(" | ".join(r) for r in rows)

    return {"filename": filename, "type": "excel", "rows": rows, "extracted_text": text_repr}


def _extract_csv(contents: bytes, filename: str):
    try:
        text = contents.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = contents.decode("latin-1")

    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        return {"error": "CSV appears to be empty or has no data rows."}

    return {"filename": filename, "type": "csv", "rows": rows}
