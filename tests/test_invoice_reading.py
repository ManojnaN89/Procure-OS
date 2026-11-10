"""Invoices read from documents: the QR code by code, the rest by a scripted model whose every value is checked against
the text, who may be read at all, the mapping to order lines, and what a person (or the gate) does with the draft."""

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

import invoice_documents as docs
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_ai_steps import Scripted
from test_invoices import PASSWORD, S1_GSTIN, _goods, _headers, _row, ledger  # noqa: F401 - fixtures
from test_orders import shop  # noqa: F401 - fixtures
from test_receiving import store  # noqa: F401 - fixtures
from test_sourcing import Outbox, db, outbox  # noqa: F401 - fixtures

from p2p.agents import autonomy
from p2p.ai_steps.gateway import Gateway
from p2p.ai_steps.invoice_reading import InvoiceReading, ReadLine
from p2p.invoices import einvoice, reading
from p2p.invoices import service as invoices
from p2p.main import app
from p2p.platform import emails, sod, ui_api, users, worker
from p2p.platform.mailbox import IncomingMail

D = Decimal
client = TestClient(app)
TODAY = date(2026, 11, 6)
S1_MAIL = "Alpha Traders <s1@supplier.example>"
S2_GSTIN = "29CCCCC0000C1Z5"
Spec, Row = docs.Spec, docs.Row


# ---- a scripted model --------------------------------------------------------------------------


def _gateway(*cheap: str, strong: tuple[str, ...] = ()) -> tuple[Gateway, Scripted]:
    provider = Scripted({"cheap": list(cheap), "strong": list(strong)})
    gw = Gateway(
        {"fake": provider},
        {"invoice_reading": [("fake", "cheap")], "invoice_reading.strong": [("fake", "strong")]},
        sleep=lambda s: None,
    )
    return gw, provider


def _says(spec: Spec, **over: Any) -> str:
    """What a model that read the document correctly would answer."""
    taxable, cgst, sgst, igst = spec.amounts()
    fields: dict[str, Any] = {
        "document_type": "credit_note" if spec.credit_note else "invoice",
        "supplier_name": spec.supplier_name, "supplier_gstin": spec.gstin, "buyer_gstin": spec.buyer_gstin,
        "invoice_number": spec.number, "invoice_date": spec.on, "po_numbers": [spec.po_no] if spec.po_no else [],
        "ref_invoice_no": spec.ref_invoice, "freight": spec.freight or None, "taxable_total": taxable,
        "cgst": cgst or None, "sgst": sgst or None, "igst": igst or None, "total": spec.total, "confidence": 0.95,
        "lines": [
            ReadLine(description=r.description, qty=r.qty, unit_price=r.price, gst_rate=r.rate, taxable=r.qty * r.price)
            for r in spec.rows
        ],
    }  # fmt: skip
    fields.update(over)
    return InvoiceReading(**fields).model_dump_json()


def _read(
    db: Session, data: bytes, gw: Gateway, *, name: str = "inv.pdf", sender: str | None = S1_MAIL, **kw: Any
) -> int:
    return reading.read_document(
        db, db.info["slug"], data, name, source=kw.pop("source", "upload"), gateway=gw, as_of=TODAY, sender=sender, **kw
    )


def _draft(db: Session, draft_id: int) -> dict[str, Any]:
    db.expire_all()
    return dict(db.execute(text("select * from invoice_draft where id = :i"), {"i": draft_id}).mappings().one())


def _spec(po_no: str, **kw: Any) -> Spec:
    return Spec(po_no=po_no, **kw)


# ---- the QR code and the rows, no database ----------------------------------------------------


def test_the_irn_is_recomputed_from_the_invoice_and_a_changed_detail_breaks_it() -> None:
    spec = Spec()
    qr = einvoice.parse_qr_text(docs.qr_text(spec))
    assert qr is not None and qr.irn_ok and qr.signed
    assert (qr.seller_gstin, qr.doc_no, qr.doc_date, qr.total, qr.item_count) == (
        spec.gstin,
        spec.number,
        spec.on,
        D("1180"),
        1,
    )
    for field in ("doc_no", "doc_type", "doc_date", "seller_gstin"):
        changed = {"doc_no": "AT/2627/0102", "doc_type": "CRN", "doc_date": date(2027, 4, 2), "seller_gstin": S2_GSTIN}
        other = einvoice.parse_qr_text(docs.qr_text(spec, irn=spec.irn_value, **{field: changed[field]}))
        assert other is not None and not other.irn_ok, field


def test_the_irn_follows_the_financial_year_so_a_date_either_side_of_march_gives_another_one() -> None:
    a = einvoice.irn_of("27AAAAA0000A1Z5", date(2027, 3, 31), "INV", "X-1")
    b = einvoice.irn_of("27AAAAA0000A1Z5", date(2027, 4, 1), "INV", "X-1")
    assert a != b and len(a) == 64


def test_a_qr_code_that_is_not_an_e_invoice_is_not_one() -> None:
    assert einvoice.parse_qr_text("https://example.com/pay?x=1") is None
    assert einvoice.parse_qr_text('{"hello": "world"}') is None
    assert einvoice.parse_qr_text("a.b") is None


def test_the_code_is_found_on_a_pdf_and_on_a_picture_and_a_document_without_one_has_none() -> None:
    spec = Spec()
    assert einvoice.find_einvoice(docs.make(spec, qr=True), "a.pdf") is not None
    assert einvoice.find_einvoice(docs.make(spec, qr=True, kind="png"), "a.png") is not None
    assert einvoice.find_einvoice(docs.make(spec), "a.pdf") is None
    assert einvoice.find_einvoice(b"not a document", "a.pdf") is None


def test_rows_are_found_by_their_arithmetic_and_totals_taxes_and_charges_are_not_rows() -> None:
    source = "\n".join(
        docs.text_lines(
            Spec(
                rows=[Row("Ball bearing 6205", D(40), D("125")), Row("Seal 25mm", D(4), D("37.5"), D(12))],
                freight=D(100),
            )
        )
    )

    rows, charges = reading.parse_rows(source, [])

    assert [(r.description, r.qty, r.unit_price, r.taxable, r.gst_rate) for r in rows] == [
        ("Ball bearing 6205", D(40), D("125.00"), D("5000.00"), D(18)),
        ("Seal 25mm", D(4), D("37.50"), D("150.00"), D(12)),
    ]
    assert charges == {"freight": (D("100.00"), None)}


def test_a_row_whose_numbers_do_not_multiply_is_not_taken_as_a_line() -> None:
    rows, _ = reading.parse_rows("1 Bearing 10 100.00 999.00 18\n2 Seal 3 10.00 30.00", [])
    assert [r.description for r in rows] == ["Seal"]


# ---- an e-invoice: no model ------------------------------------------------------------------


def test_an_e_invoice_is_read_by_code_with_no_model_call_and_a_person_confirms_it(
    ledger: Session, outbox: Outbox
) -> None:  # noqa: F811
    po_no, _ = _goods(ledger)
    gw, provider = _gateway("{}")
    spec = _spec(po_no)

    draft_id = _read(ledger, docs.make(spec, qr=True), gw)

    draft = _draft(ledger, draft_id)
    assert provider.calls == []  # code read all of it
    assert (draft["status"], draft["path"], draft["supplier_code"], draft["problems"], draft["assumptions"]) == (
        "pending", "qr", "S1", [], [],
    )  # fmt: skip
    proposal = draft["proposal"]
    assert (proposal["invoice_number"], proposal["invoice_date"], proposal["irn"]) == (
        spec.number,
        "2026-11-06",
        spec.irn_value,
    )
    assert [(ln["po_no"], ln["po_line_no"], ln["qty"], ln["unit_price"]) for ln in proposal["lines"]] == [
        (po_no, 1, "10", "100.00")
    ]
    assert (
        ledger.execute(text("select count(*) from invoice")).scalar_one() == 0
    )  # nothing is entered until a person says

    done = reading.confirm_draft(ledger, draft_id, 1, as_of=TODAY)

    assert (done.status, done.result) == ("approved", "matched")
    row = _row(ledger, done.no)
    assert (row["supplier_invoice_no"], row["irn"], row["total"], row["channel"]) == (
        spec.number,
        spec.irn_value,
        D("1180.00"),
        "upload",
    )
    after = _draft(ledger, draft_id)
    assert (after["status"], after["invoice_no"], after["decided_by"]) == ("applied", done.no, "user:1")
    assert (
        ledger.execute(text("select count(*) from audit_event where action = 'invoice_draft.confirm'")).scalar_one()
        == 1
    )


def test_two_lines_charges_and_in_state_tax_are_all_read_by_code(ledger: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, _ = _goods(ledger)
    ledger.execute(text("update po set supplier_gstin = :g where no = :p"), {"g": S1_GSTIN, "p": po_no})
    ledger.commit()
    # one order line is open, so a second invoice row has nothing to map to: build the invoice from that one line, with freight
    spec = _spec(po_no, freight=D(100))
    gw, provider = _gateway("{}")

    draft = _draft(ledger, _read(ledger, docs.make(spec, qr=True), gw))

    assert provider.calls == [] and draft["path"] == "qr"
    assert draft["proposal"]["charges"] == [{"type": "freight", "amount": "100.00", "rate": "18"}]
    assert "taxed at 18%" in " ".join(draft["assumptions"]) and draft["status"] == "pending"


def test_a_photographed_e_invoice_is_read_through_ocr_and_its_code_with_no_model(
    ledger: Session, outbox: Outbox
) -> None:  # noqa: F811
    po_no, _ = _goods(ledger)
    gw, provider = _gateway("{}")

    draft = _draft(ledger, _read(ledger, docs.make(_spec(po_no), qr=True, kind="png"), gw, name="photo.png"))

    assert provider.calls == [] and (draft["status"], draft["path"], draft["problems"]) == ("pending", "qr", [])


def test_an_altered_irn_is_stopped_before_any_model_and_says_why(ledger: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, _ = _goods(ledger)
    spec = _spec(po_no)
    forged = docs.pdf(docs.text_lines(spec), docs.qr_array(docs.qr_text(spec, doc_no="AT/2627/9999")))
    gw, provider = _gateway(_says(spec))

    draft = _draft(ledger, _read(ledger, forged, gw))

    assert provider.calls == []
    assert draft["status"] == "needs_person" and draft["proposal"] is None
    assert "IRN" in draft["problems"][0] and "altered" in draft["problems"][0]


def test_a_code_that_names_another_suppliers_gstin_is_stopped(ledger: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, _ = _goods(ledger)
    spec = _spec(po_no, gstin=S2_GSTIN)  # the invoice is Bravo's, the mail came from Alpha
    gw, provider = _gateway(_says(spec))

    draft = _draft(ledger, _read(ledger, docs.make(spec, qr=True), gw))

    assert provider.calls == []
    assert draft["status"] == "needs_person" and "another supplier" in draft["problems"][0]


def test_a_qr_code_that_does_not_fit_the_text_falls_back_to_the_model_which_must_agree(
    ledger: Session, outbox: Outbox
) -> None:  # noqa: F811
    po_no, _ = _goods(ledger)
    spec = _spec(po_no)
    code = docs.qr_array(docs.qr_text(spec, item_count=3))  # claims three items; the page shows one
    gw, provider = _gateway(_says(spec))

    draft = _draft(ledger, _read(ledger, docs.pdf(docs.text_lines(spec), code), gw))

    assert provider.calls == ["cheap"] and draft["path"] == "model" and draft["status"] == "pending"
    assert draft["qr"]["irn_ok"] is True  # the code is kept with the draft for the person to see


# ---- a document without a code: the model, checked ---------------------------------------------


def test_a_plain_pdf_is_read_by_the_model_checked_against_its_text_and_confirmed(
    ledger: Session, outbox: Outbox
) -> None:  # noqa: F811
    po_no, _ = _goods(ledger)
    spec = _spec(po_no)
    gw, provider = _gateway(_says(spec))

    draft_id = _read(ledger, docs.make(spec), gw)

    draft = _draft(ledger, draft_id)
    assert provider.calls == ["cheap"]
    assert (draft["status"], draft["path"], draft["problems"], draft["prompt_version"]) == ("pending", "model", [], "1")
    assert draft["model"] == "fake/cheap" and D(str(draft["confidence"])) == D("0.95")
    done = reading.confirm_draft(ledger, draft_id, 1, as_of=TODAY)
    assert (done.status, done.result) == ("approved", "matched")


def test_a_value_the_model_made_up_is_refused_by_the_evidence_check_and_goes_to_a_person(
    ledger: Session, outbox: Outbox
) -> None:  # noqa: F811
    po_no, _ = _goods(ledger)
    spec = _spec(po_no)
    invented = _says(
        spec, lines=[ReadLine(description="Bearing", qty=D(12), unit_price=D(100), gst_rate=D(18), taxable=D(1200))]
    )
    gw, provider = _gateway(invented, strong=(invented,))

    draft_id = _read(ledger, docs.make(spec), gw)

    draft = _draft(ledger, draft_id)
    assert provider.calls == ["cheap", "strong"]  # the stronger model was tried and was no better
    assert draft["status"] == "needs_person" and draft["proposal"] is None
    assert any("lines[0].qty" in p for p in draft["problems"])
    with pytest.raises(reading.ReadingError, match="nothing was read"):
        reading.confirm_draft(ledger, draft_id, 1, as_of=TODAY)
    assert ledger.execute(text("select count(*) from task where kind = 'ai_review'")).scalar_one() >= 1


def test_a_reading_the_model_is_unsure_of_is_not_used(ledger: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, _ = _goods(ledger)
    spec = _spec(po_no)
    gw, _ = _gateway(_says(spec, confidence=0.3), strong=(_says(spec, confidence=0.3),))

    draft = _draft(ledger, _read(ledger, docs.make(spec), gw))

    assert draft["status"] == "needs_person" and any("below 0.6" in p for p in draft["problems"])


def test_a_stronger_model_is_used_when_the_first_answer_fails_the_check(ledger: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, _ = _goods(ledger)
    spec = _spec(po_no)
    gw, provider = _gateway(_says(spec, invoice_number="AT/2627/0999"), strong=(_says(spec),))

    draft = _draft(ledger, _read(ledger, docs.make(spec), gw))

    assert (
        provider.calls == ["cheap", "strong"]
        and draft["status"] == "pending"
        and draft["proposal"]["invoice_number"] == spec.number
    )


def test_a_total_that_is_not_the_sum_of_the_lines_by_our_rules_is_a_problem(ledger: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, _ = _goods(ledger)
    spec = _spec(po_no, wrong_total=D("1250.00"))  # the page says 1,250.00; the line and tax make 1,180.00
    gw, _ = _gateway(_says(spec, total=D("1250.00")))

    draft = _draft(ledger, _read(ledger, docs.make(spec), gw))

    assert draft["status"] == "needs_person"
    assert any("1,180" in p and "1,250.00" in p for p in draft["problems"])


def test_an_invoice_line_with_no_order_line_to_go_to_leaves_the_draft_for_a_person(
    ledger: Session, outbox: Outbox
) -> None:  # noqa: F811
    po_no, _ = _goods(ledger)
    spec = _spec(po_no, rows=[Row("Bearing", D(10), D(100)), Row("Gasket kit", D(2), D(50))])
    gw, _ = _gateway(_says(spec))

    draft = _draft(ledger, _read(ledger, docs.make(spec), gw))

    assert draft["status"] == "needs_person"
    assert any("Gasket kit" in p for p in draft["problems"])


def test_the_only_open_line_is_assumed_and_said_so_and_a_person_still_decides(ledger: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, _ = _goods(ledger)
    spec = _spec(po_no, rows=[Row("Hex shaft coupling", D(10), D(100))])
    gw, _ = _gateway(_says(spec))

    draft = _draft(ledger, _read(ledger, docs.make(spec), gw))

    assert draft["status"] == "pending" and draft["problems"] == []
    assert "only line open" in draft["assumptions"][0]


def test_two_order_lines_that_fit_a_description_equally_well_are_not_guessed() -> None:
    pool = [
        reading.Candidate("PO-1", 1, None, "Bearing", D(5), D(100), D(18)),
        reading.Candidate("PO-2", 1, None, "Bearing", D(5), D(100), D(18)),
        reading.Candidate("PO-2", 2, None, "Seal", D(5), D(10), D(18)),
    ]

    none, problem, _ = reading.map_line("Bearing", [], None, pool)
    named, _, _ = reading.map_line("Bearing", ["PO-2"], None, pool)
    numbered, _, _ = reading.map_line("whatever it says", ["PO-2"], 2, pool)

    assert none is None and "could not tell which order line" in (problem or "")
    assert (named and named.po_no, numbered and numbered.po_line_no) == (
        "PO-2",
        2,
    )  # the order named, or its line number, decides


def test_a_gstin_on_the_page_that_belongs_to_another_supplier_is_a_problem(ledger: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, _ = _goods(ledger)
    spec = _spec(po_no)
    wrong = _says(spec, supplier_gstin=S2_GSTIN)
    page = docs.pdf([ln.replace(spec.gstin, S2_GSTIN) for ln in docs.text_lines(spec)])  # Alpha's mail, Bravo's GSTIN

    draft = _draft(ledger, _read(ledger, page, _gateway(wrong)[0]))

    assert draft["status"] == "needs_person" and any("is that of S2" in p for p in draft["problems"])


def test_a_credit_note_must_name_the_invoice_it_is_for(ledger: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, _ = _goods(ledger)
    first = invoices.enter_invoice(
        ledger,
        invoices.InvoiceIn(
            "S1",
            "AT/2627/0101",
            TODAY,
            [invoices.InvLine(po_no, 1, D(10), D(100))],
            supplier_gstin=S1_GSTIN,
            buyer_gstin="29BBBBB0000B1Z5",
        ),
        1,
        as_of=TODAY,
    )
    credit = _spec(
        po_no, credit_note=True, number="AT/CN/01", rows=[Row("Bearing", D(2), D(100))], ref_invoice="AT/2627/0101"
    )
    bare = _spec(po_no, credit_note=True, number="AT/CN/02", rows=[Row("Bearing", D(2), D(100))])

    ok = _draft(ledger, _read(ledger, docs.make(credit), _gateway(_says(credit))[0]))
    missing = _draft(ledger, _read(ledger, docs.make(bare), _gateway(_says(bare))[0]))

    assert (
        ok["status"] == "pending"
        and ok["proposal"]["ref_invoice_no"] == first.no
        and ok["proposal"]["doc_type"] == "credit_note"
    )
    assert missing["status"] == "needs_person" and "which invoice" in " ".join(missing["problems"])


# ---- who may be read ------------------------------------------------------------------------------


def test_mail_from_someone_who_is_not_a_known_supplier_is_never_shown_to_a_model(
    ledger: Session, outbox: Outbox
) -> None:  # noqa: F811
    po_no, _ = _goods(ledger)
    spec = _spec(po_no, gstin="27ZZZZZ0000Z1Z5")  # a GSTIN nobody has on file
    gw, provider = _gateway(_says(spec))

    draft = _draft(ledger, _read(ledger, docs.make(spec), gw, sender="Mallory <pay-me@evil.example>"))

    assert provider.calls == []
    assert draft["status"] == "needs_person" and draft["supplier_code"] is None
    assert "no model read it" in draft["problems"][0]


def test_an_unknown_sender_is_still_placed_when_the_document_carries_a_gstin_on_file(
    ledger: Session, outbox: Outbox
) -> None:  # noqa: F811
    po_no, _ = _goods(ledger)
    spec = _spec(po_no)
    gw, provider = _gateway(_says(spec))

    draft = _draft(ledger, _read(ledger, docs.make(spec), gw, sender="Accounts <accounts@alpha-traders.example>"))

    assert draft["supplier_code"] == "S1" and provider.calls == ["cheap"]  # the GSTIN is Alpha's


def test_a_document_that_cannot_be_read_becomes_a_task_not_an_error(ledger: Session, outbox: Outbox) -> None:  # noqa: F811
    _goods(ledger)
    gw, provider = _gateway("{}")

    draft = _draft(ledger, _read(ledger, b"%PDF-1.4 truncated", gw))

    assert provider.calls == [] and draft["status"] == "needs_person" and "could not be read" in draft["problems"][0]


def test_the_same_file_twice_is_one_draft_and_one_reading(ledger: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, _ = _goods(ledger)
    spec = _spec(po_no)
    gw, provider = _gateway(_says(spec))
    data = docs.make(spec)

    first, second = _read(ledger, data, gw), _read(ledger, data, gw)

    assert first == second and provider.calls == ["cheap"]
    assert ledger.execute(text("select count(*) from invoice_draft")).scalar_one() == 1


# ---- the person's decision ---------------------------------------------------------------------


def test_ap_can_correct_a_quantity_and_the_three_way_match_still_decides(ledger: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, _ = _goods(ledger)
    draft_id = _read(ledger, docs.make(_spec(po_no), qr=True), _gateway("{}")[0])

    done = reading.confirm_draft(ledger, draft_id, 1, as_of=TODAY, edits={"lines": {"0": {"qty": "6"}}})

    assert (done.status, done.result) == ("approved", "matched")
    row = _row(ledger, done.no)
    assert row["taxable"] == D("600.00") and row["total"] == D("708.00")  # the amounts follow the corrected quantity
    assert ledger.execute(text("select invoiced_qty from po_line")).scalar_one() == D("6")


def test_a_correction_that_cannot_be_read_is_refused_and_leaves_the_draft_open(ledger: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, _ = _goods(ledger)
    draft_id = _read(ledger, docs.make(_spec(po_no), qr=True), _gateway("{}")[0])

    with pytest.raises((ValueError, KeyError, IndexError, ArithmeticError)):
        reading.confirm_draft(ledger, draft_id, 1, as_of=TODAY, edits={"lines": {"5": {"qty": "6"}}})
    ledger.rollback()

    assert _draft(ledger, draft_id)["status"] == "pending"


def test_a_duplicate_of_an_invoice_on_file_is_caught_when_the_second_draft_is_confirmed(
    ledger: Session, outbox: Outbox
) -> None:  # noqa: F811
    po_no, _ = _goods(ledger)
    spec = _spec(po_no)
    first = _read(ledger, docs.make(spec, qr=True), _gateway("{}")[0])
    again = _read(
        ledger,
        docs.pdf([*docs.text_lines(spec), "DUPLICATE COPY"], docs.qr_array(docs.qr_text(spec))),
        _gateway("{}")[0],
    )
    assert first != again  # different bytes, so a different document

    one = reading.confirm_draft(ledger, first, 1, as_of=TODAY)
    two = reading.confirm_draft(ledger, again, 1, as_of=TODAY)

    assert one.status == "approved" and two.status == "cancelled" and two.duplicate_of == one.no


def test_a_dismissed_draft_cannot_be_confirmed_and_both_decisions_are_audited(ledger: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, _ = _goods(ledger)
    draft_id = _read(ledger, docs.make(_spec(po_no), qr=True), _gateway("{}")[0])

    reading.dismiss_draft(ledger, draft_id, 1, "not ours")

    with pytest.raises(reading.ReadingError, match="dismissed"):
        reading.confirm_draft(ledger, draft_id, 1, as_of=TODAY)
    assert _draft(ledger, draft_id)["decision_note"] == "not ours"
    assert (
        ledger.execute(
            text("select count(*) from audit_event where action in ('invoice_draft.create', 'invoice_draft.dismiss')")
        ).scalar_one()
        == 2
    )


def test_a_draft_with_problems_cannot_be_confirmed_unchanged(ledger: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, _ = _goods(ledger)
    spec = _spec(po_no, rows=[Row("Bearing", D(10), D(100)), Row("Gasket kit", D(2), D(50))])
    draft_id = _read(ledger, docs.make(spec), _gateway(_says(spec))[0])

    with pytest.raises(reading.ReadingError, match="problems"):
        reading.confirm_draft(ledger, draft_id, 1, as_of=TODAY)


# ---- the autonomy gate -----------------------------------------------------------------------


def test_a_clean_e_invoice_is_entered_by_itself_only_when_the_gate_allows(ledger: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, logins = _goods(ledger)
    first = _read(ledger, docs.make(_spec(po_no), qr=True), _gateway("{}")[0])
    assert _draft(ledger, first)["status"] == "pending"  # the default: a person confirms

    autonomy.configure(ledger, sod.user(1), "invoice.read", level="auto")
    po2, _ = _goods(ledger, qty="5", price="40", logins=logins)
    second = _read(
        ledger,
        docs.make(_spec(po2, number="AT/2627/0202", rows=[Row("Bearing", D(5), D(40))]), qr=True),
        _gateway("{}")[0],
    )

    after = _draft(ledger, second)
    assert after["status"] == "applied" and after["decided_by"] == "service:invoice_reading"
    assert (
        ledger.execute(text("select entered_by from invoice where no = :n"), {"n": after["invoice_no"]}).scalar_one()
        == "invoice_reading"
    )
    assert (
        ledger.execute(
            text("select count(*) from autonomy_outcome where decision_type = 'invoice.read' and auto")
        ).scalar_one()
        == 1
    )


def test_anything_in_doubt_waits_for_a_person_even_when_automatic(ledger: Session, outbox: Outbox) -> None:  # noqa: F811
    autonomy.configure(ledger, sod.user(1), "invoice.read", level="auto")
    po_no, logins = _goods(ledger)
    spec = _spec(po_no, freight=D(100))  # the freight's tax rate is an assumption
    assumed = _read(ledger, docs.make(spec, qr=True), _gateway("{}")[0])
    assert (
        _draft(ledger, assumed)["status"] == "pending"
        and ledger.execute(text("select count(*) from invoice")).scalar_one() == 0
    )

    autonomy.set_kill_switch(ledger, sod.user(1), True, "incident")
    po2, _ = _goods(ledger, qty="5", price="40", logins=logins)
    stopped = _read(
        ledger,
        docs.make(_spec(po2, number="AT/2627/0303", rows=[Row("Bearing", D(5), D(40))]), qr=True),
        _gateway("{}")[0],
    )
    assert _draft(ledger, stopped)["status"] == "pending"


def test_a_reading_by_the_model_is_never_entered_by_itself_when_it_has_assumptions_or_problems(
    ledger: Session, outbox: Outbox
) -> None:  # noqa: F811
    autonomy.configure(ledger, sod.user(1), "invoice.read", level="auto")
    po_no, _ = _goods(ledger)
    spec = _spec(po_no, wrong_total=D("1250.00"))
    draft = _draft(ledger, _read(ledger, docs.make(spec), _gateway(_says(spec, total=D("1250.00")))[0]))
    assert draft["status"] == "needs_person" and ledger.execute(text("select count(*) from invoice")).scalar_one() == 0


# ---- the mailbox and the API ---------------------------------------------------------------------


def _mail(to: str, sender: str, attachments: list[tuple[str, str, bytes]]) -> IncomingMail:
    return IncomingMail(
        message_id=f"<{uuid.uuid4().hex}@supplier>", in_reply_to=None, references=[], from_addr=sender, to_addrs=[to],
        subject="Invoice", body="Please find our invoice attached.", sent_at=None, attachments=attachments, raw=b"raw",
    )  # fmt: skip


def _queued(slug: str, kind: str) -> list[str]:
    with worker.tenants.control_engine().connect() as c:
        rows = c.execute(
            text("select payload->>'email_id' from job where tenant_slug = :t and kind = :k order by id"),
            {"t": slug, "k": kind},
        ).all()
    return [r[0] for r in rows]


def test_mail_to_the_invoice_mailbox_queues_a_job_and_the_job_reads_every_attachment(
    ledger: Session, outbox: Outbox
) -> None:  # noqa: F811
    po_no, _ = _goods(ledger)
    ledger.execute(
        text("insert into org_setting (key, value) values ('invoice_receipt_mailbox', 'invoices@cbcpl.example')")
    )
    ledger.commit()
    spec = _spec(po_no)
    pdf = docs.make(spec, qr=True)

    email_id = emails.ingest(
        ledger,
        ledger.info["slug"],
        _mail(
            "invoices@cbcpl.example",
            S1_MAIL,
            [("inv.pdf", "application/pdf", pdf), ("terms.txt", "text/plain", b"terms")],
        ),
    )

    assert _queued(ledger.info["slug"], "read_invoice_email") == [str(email_id)]
    gw, provider = _gateway("{}")
    ids = reading.read_email(ledger, ledger.info["slug"], int(email_id or 0), gw, as_of=TODAY)  # type: ignore[arg-type]
    assert len(ids) == 1 and provider.calls == []  # the text file is not an invoice document
    draft = _draft(ledger, ids[0])
    assert (draft["source"], draft["email_id"], draft["status"], draft["path"]) == ("email", email_id, "pending", "qr")
    assert (
        reading.read_email(ledger, ledger.info["slug"], int(email_id or 0), gw, as_of=TODAY) == []
    )  # reading it again adds nothing


def test_mail_to_any_other_address_is_not_taken_for_an_invoice(ledger: Session, outbox: Outbox) -> None:  # noqa: F811
    ledger.execute(
        text("insert into org_setting (key, value) values ('invoice_receipt_mailbox', 'invoices@cbcpl.example')")
    )
    ledger.commit()

    emails.ingest(
        ledger,
        ledger.info["slug"],
        _mail("someone@cbcpl.example", S1_MAIL, [("inv.pdf", "application/pdf", docs.make(Spec(), qr=True))]),
    )

    assert _queued(ledger.info["slug"], "read_invoice_email") == []


def test_a_mail_from_a_stranger_to_the_invoice_mailbox_makes_a_draft_for_a_person_and_no_model_call(
    ledger: Session, outbox: Outbox
) -> None:  # noqa: F811
    po_no, _ = _goods(ledger)
    ledger.execute(
        text("insert into org_setting (key, value) values ('invoice_receipt_mailbox', 'invoices@cbcpl.example')")
    )
    ledger.commit()
    spec = _spec(po_no, gstin="27ZZZZZ0000Z1Z5")
    email_id = emails.ingest(
        ledger,
        ledger.info["slug"],
        _mail("invoices@cbcpl.example", "Mallory <m@evil.example>", [("inv.pdf", "application/pdf", docs.make(spec))]),
    )
    gw, provider = _gateway(_says(spec))

    (draft_id,) = reading.read_email(ledger, ledger.info["slug"], int(email_id or 0), gw, as_of=TODAY)  # type: ignore[arg-type]

    assert provider.calls == [] and _draft(ledger, draft_id)["status"] == "needs_person"


def test_a_mail_with_no_invoice_document_is_a_draft_that_says_so(ledger: Session, outbox: Outbox) -> None:  # noqa: F811
    ledger.execute(
        text("insert into org_setting (key, value) values ('invoice_receipt_mailbox', 'invoices@cbcpl.example')")
    )
    ledger.commit()
    email_id = emails.ingest(ledger, ledger.info["slug"], _mail("invoices@cbcpl.example", S1_MAIL, []))

    (draft_id,) = reading.read_email(ledger, ledger.info["slug"], int(email_id or 0), _gateway("{}")[0], as_of=TODAY)  # type: ignore[arg-type]

    draft = _draft(ledger, draft_id)
    assert (
        draft["status"] == "needs_person"
        and "no invoice document" in draft["problems"][0]
        and draft["supplier_code"] == "S1"
    )


def test_ap_uploads_reads_and_confirms_over_the_api_and_others_cannot(ledger: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, logins = _goods(ledger)
    slug, sfx = ledger.info["slug"], ledger.info["sfx"]
    ap = users.create_user(slug, f"ap{sfx}@x.example", PASSWORD, "An AP", "AP")
    h_ap, h_buyer = _headers(slug, ap, "AP"), _headers(slug, logins["buyer"], "Buyer")
    spec = _spec(po_no)
    pdf = docs.make(spec)
    gw, provider = _gateway(_says(spec))
    ui_api.use_gateway(gw)
    try:
        files = {"file": ("inv.pdf", pdf, "application/pdf")}
        assert client.post("/api/invoice-drafts/upload", files=files).status_code == 401
        assert client.post("/api/invoice-drafts/upload", files=files, headers=h_buyer).status_code == 403
        assert (
            client.post(
                "/api/invoice-drafts/upload", files={"file": ("x.exe", b"MZ", "application/octet-stream")}, headers=h_ap
            ).status_code
            == 422
        )

        up = client.post("/api/invoice-drafts/upload", files=files, headers=h_ap)
    finally:
        ui_api.use_gateway(None)

    assert up.status_code == 201 and provider.calls == ["cheap"]
    body = up.json()
    assert (body["status"], body["path"], body["supplier_code"], body["number"], body["total"]) == (
        "pending",
        "model",
        "S1",
        spec.number,
        "1180.00",
    ) or body["total"] == "1180"
    assert body["lines"][0]["po_no"] == po_no
    assert [d["id"] for d in client.get("/api/invoice-drafts", headers=h_ap).json()] == [body["id"]]
    assert client.get(f"/api/invoice-drafts/{body['id']}", headers=h_ap).json()["filename"] == "inv.pdf"
    assert client.post(f"/api/invoice-drafts/{body['id']}/confirm", json={}, headers=h_buyer).status_code == 403

    done = client.post(f"/api/invoice-drafts/{body['id']}/confirm", json={}, headers=h_ap)

    assert done.status_code == 200 and (done.json()["status"], done.json()["result"]) == ("approved", "matched")
    assert (
        client.post(f"/api/invoice-drafts/{body['id']}/confirm", json={}, headers=h_ap).status_code == 409
    )  # only once
    assert client.post(f"/api/invoice-drafts/{body['id']}/dismiss", json={}, headers=h_ap).status_code == 409
    assert client.get("/api/invoice-drafts", headers=h_ap).json() == []  # nothing left to do
    assert client.get("/api/invoice-drafts/9999", headers=h_ap).status_code == 404
    assert client.get("/api/invoice-drafts").status_code == 401


def test_the_invoice_decision_is_in_the_automation_catalogue() -> None:
    from p2p.platform import setup_api

    assert "invoice.read" in setup_api.DECISIONS
