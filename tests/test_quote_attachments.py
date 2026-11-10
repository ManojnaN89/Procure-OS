"""A quotation often arrives as an attachment: a PDF, an Excel sheet or a Word file. What is written in it is read like an email body."""

import io
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import docx
import openpyxl
import pytest
import sample_documents as sd
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_orders import _sent_rfq, shop  # noqa: F401 - fixtures
from test_quote_reading import SELLER, _draft, _gateway, _read, _seal
from test_sourcing import Outbox, db, outbox  # noqa: F401 - fixtures

from p2p.platform import emails
from p2p.platform.mailbox import IncomingMail

D = Decimal
NOTE = "Dear Sir, please find our quotation attached. Regards, Alpha Traders"
FACTS = [
    "Seal - Rs 190 per piece, GST 18% extra",
    "Delivery in 4 days",
    "Prices valid till 31 Dec 2026",
    "Freight Rs 50 extra",
]


def _pdf() -> tuple[str, str, bytes]:
    return "quotation.pdf", "application/pdf", sd.text_pdf(FACTS)


def _xlsx() -> tuple[str, str, bytes]:
    wb = openpyxl.Workbook()
    ws = wb.active
    assert ws is not None
    ws.append(["Item", "Offer"])
    for fact in FACTS:
        ws.append(["Quote", fact])
    buf = io.BytesIO()
    wb.save(buf)
    return "quotation.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", buf.getvalue()


def _docx() -> tuple[str, str, bytes]:
    d = docx.Document()
    for fact in FACTS:
        d.add_paragraph(fact)
    buf = io.BytesIO()
    d.save(buf)
    return "quotation.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", buf.getvalue()


def _deliver(db_: Session, rfq: str, attachments: list[tuple[str, str, bytes]]) -> int:
    incoming = IncomingMail(
        message_id=f"<att-{rfq}-{len(attachments)}-{datetime.now(UTC).timestamp()}@supplier>",
        from_addr=SELLER,
        to_addrs=[f"rfq+{rfq}@cbcpl.example"],
        subject=f"Re: Request for quotation {rfq}",
        body=NOTE,
        sent_at=datetime.now(UTC),
        attachments=attachments,
        raw=b"raw",
    )
    email_id = emails.ingest(db_, db_.info["slug"], incoming)
    assert email_id is not None
    db_.commit()
    return email_id


@pytest.mark.parametrize("make", [_pdf, _xlsx, _docx], ids=["pdf", "excel", "word"])
def test_a_quotation_in_an_attachment_is_read_into_a_draft_the_email_body_alone_could_not_support(
    shop: Session,
    outbox: Outbox,
    make: Any,  # noqa: F811
) -> None:
    _, rfq = _sent_rfq(shop, outbox)
    email_id = _deliver(shop, rfq, [make()])
    gw, _ = _gateway(_seal().model_dump_json())

    draft_id = _read(shop, email_id, gw)

    assert draft_id is not None
    draft = _draft(shop, draft_id)
    assert draft["status"] == "pending"  # every value was found in the attachment, so it passed the evidence check
    assert shop.execute(text("select count(*) from quote")).scalar_one() == 0  # a person still turns it into a quote


def test_the_same_reading_with_no_attachment_has_nothing_to_stand_on_and_goes_to_a_person(
    shop: Session,
    outbox: Outbox,  # noqa: F811
) -> None:
    _, rfq = _sent_rfq(shop, outbox)
    email_id = _deliver(shop, rfq, [])
    gw, _ = _gateway(_seal().model_dump_json(), strong=(_seal().model_dump_json(),))

    draft_id = _read(shop, email_id, gw)

    assert draft_id is None or _draft(shop, draft_id)["status"] != "pending"
    assert shop.execute(text("select count(*) from quote")).scalar_one() == 0


def test_an_attachment_that_cannot_be_read_does_not_stop_the_email_being_read(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    _, rfq = _sent_rfq(shop, outbox)
    email_id = _deliver(shop, rfq, [("broken.pdf", "application/pdf", b"this is not a pdf"), _docx()])
    gw, _ = _gateway(_seal().model_dump_json())

    draft_id = _read(shop, email_id, gw)

    assert draft_id is not None and _draft(shop, draft_id)["status"] == "pending"
