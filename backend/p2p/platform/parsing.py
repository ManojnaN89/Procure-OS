"""Document parsing: PDF, scans, Word, Excel and CSV into text and tables with page positions.

PDF, Word and images go through Docling (with RapidOCR for scans). Excel and CSV are read directly so the cell
values stay exact. The original file is stored separately by the file store; this only reads it.
"""

import csv
import io
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import openpyxl

_converter: Any = None
_lock = threading.Lock()


@dataclass
class ParsedTable:
    rows: list[list[str]]
    page: int | None = None
    sheet: str | None = None


@dataclass
class ParsedDocument:
    text: str
    tables: list[ParsedTable] = field(default_factory=list)
    pages: dict[int, str] = field(default_factory=dict)  # page number to the text found on that page
    engine: str = ""


class UnsupportedDocument(Exception):
    pass


def _docling() -> Any:
    global _converter
    with _lock:
        if _converter is None:
            from docling.document_converter import DocumentConverter  # noqa: PLC0415 - heavy import, only when needed

            _converter = DocumentConverter()
        return _converter


def _parse_docling(data: bytes, filename: str) -> ParsedDocument:
    from docling.datamodel.base_models import DocumentStream  # noqa: PLC0415

    result = _docling().convert(DocumentStream(name=filename, stream=io.BytesIO(data)))
    doc = result.document
    pages: dict[int, list[str]] = {}
    for item, _level in doc.iterate_items():
        label = getattr(item, "label", None)
        text = getattr(item, "text", None)
        prov = getattr(item, "prov", None)
        if text and prov and str(label) != "DocItemLabel.TABLE":
            pages.setdefault(prov[0].page_no, []).append(text)
    tables = []
    for table in doc.tables:
        frame = table.export_to_dataframe(doc)
        rows = [[str(c) for c in frame.columns], *[[str(c) for c in row] for row in frame.values.tolist()]]
        page = table.prov[0].page_no if table.prov else None
        tables.append(ParsedTable(rows=rows, page=page))
    return ParsedDocument(
        text=doc.export_to_markdown(),
        tables=tables,
        pages={p: "\n".join(lines) for p, lines in sorted(pages.items())},
        engine="docling",
    )


def _parse_xlsx(data: bytes) -> ParsedDocument:
    wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    tables, parts = [], []
    for ws in wb.worksheets:
        rows = [["" if c is None else str(c) for c in row] for row in ws.iter_rows(values_only=True)]
        rows = [r for r in rows if any(c.strip() for c in r)]
        if rows:
            tables.append(ParsedTable(rows=rows, sheet=ws.title))
            parts.append(f"## {ws.title}\n" + "\n".join(" | ".join(r) for r in rows))
    return ParsedDocument(text="\n\n".join(parts), tables=tables, engine="openpyxl")


def _parse_csv(data: bytes) -> ParsedDocument:
    text = data.decode("utf-8-sig", errors="replace")
    rows = [r for r in csv.reader(io.StringIO(text)) if any(c.strip() for c in r)]
    return ParsedDocument(text="\n".join(" | ".join(r) for r in rows), tables=[ParsedTable(rows=rows)], engine="csv")


def parse_document(data: bytes, filename: str) -> ParsedDocument:
    suffix = Path(filename).suffix.lower()
    if suffix in (".xlsx", ".xlsm"):
        return _parse_xlsx(data)
    if suffix in (".csv", ".txt"):
        return _parse_csv(data)
    if suffix in (".pdf", ".docx", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"):
        return _parse_docling(data, filename)
    raise UnsupportedDocument(f"cannot read '{suffix or filename}' files")
