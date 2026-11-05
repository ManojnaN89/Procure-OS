import os
from datetime import date

import pytest

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

import sample_documents as sd  # noqa: E402

from p2p.ai_steps.evidence import check_evidence  # noqa: E402
from p2p.platform.parsing import UnsupportedDocument, parse_document  # noqa: E402


def test_a_pdf_with_a_text_layer_is_read_with_its_page_number() -> None:
    doc = parse_document(sd.text_pdf(), "quote.pdf")

    assert doc.engine == "docling" and list(doc.pages) == [1]
    assert "Q-77" in doc.text and "Shree Steel Traders" in doc.text and "5,000.00" in doc.text


def test_a_scanned_pdf_is_read_by_ocr() -> None:
    doc = parse_document(sd.scanned_pdf(), "scan.pdf")

    assert "Shree Steel Traders" in doc.text
    assert "5,000.00" in doc.pages[1] and "6205" in doc.pages[1]


def test_what_ocr_read_passes_the_evidence_check_for_correct_values_and_fails_for_invented_ones() -> None:
    text = parse_document(sd.scanned_pdf(), "scan.pdf").text  # OCR may run words together, e.g. '05Nov 2026'

    right = {"supplier": "Shree Steel Traders", "total": 5000, "qty": 40, "valid_until": date(2026, 11, 30)}
    wrong = {"supplier": "Shree Steel Traders", "total": 5100, "qty": 40, "valid_until": date(2026, 12, 30)}
    paths = ["supplier", "total", "qty", "valid_until"]

    assert check_evidence(right, text, paths) == []
    assert {f.path for f in check_evidence(wrong, text, paths)} == {"total", "valid_until"}


def test_a_word_file_gives_text_and_a_table() -> None:
    doc = parse_document(sd.quote_docx(), "quote.docx")

    assert "Quotation Q-78" in doc.text
    assert doc.tables[0].rows == [["Item", "Qty", "Rate"], ["Ball bearing 6205", "40", "125"]]


def test_an_excel_quote_keeps_exact_cell_values_per_sheet() -> None:
    doc = parse_document(sd.quote_xlsx(), "quote.xlsx")

    assert doc.engine == "openpyxl" and doc.tables[0].sheet == "Quote"
    assert doc.tables[0].rows == [
        ["Item", "Qty", "Rate", "Amount"],
        ["Ball bearing 6205", "40", "125", "5000"],
        ["Grease 1kg", "10", "310.5", "3105"],
    ]


def test_a_vendor_csv_becomes_a_table() -> None:
    doc = parse_document(sd.vendor_csv(), "vendors.csv")

    assert doc.tables[0].rows[0] == ["name", "gstin", "city"] and len(doc.tables[0].rows) == 3


def test_other_file_types_are_refused_clearly() -> None:
    with pytest.raises(UnsupportedDocument, match="exe"):
        parse_document(b"MZ", "setup.exe")
