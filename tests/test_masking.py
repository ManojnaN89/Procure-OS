"""D-25: bank details, PANs and Aadhaar numbers are masked before any model call; what the product reads stays."""

import json
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.ai_steps.adapters import Messages, RawResponse
from p2p.ai_steps.gateway import Gateway
from p2p.ai_steps.masking import mask, mask_messages
from p2p.platform import routing, tenants


@pytest.mark.parametrize(
    ("source", "masked", "kind"),
    [
        (
            "Please pay to A/c No. 501001234567890 from now on",
            "Please pay to A/c No. XXXXXXXXXXX7890 from now on",
            "bank_account",
        ),
        ('{"bank_account_no": "123456789012"}', '{"bank_account_no": "XXXXXXXX9012"}', "bank_account"),
        ("Account number: 00112233445566", "Account number: XXXXXXXXXX5566", "bank_account"),
        ("IFSC HDFC0001234", "IFSC [IFSC]", "ifsc"),
        ("ifsc: sbin0004321", "ifsc: [IFSC]", "ifsc"),
        ("PAN ABCPE0000F", "PAN [PAN]", "pan"),
        ("our pan is abcpe0000f.", "our pan is [PAN].", "pan"),
        ("Aadhaar 2345 6789 0123", "Aadhaar [Aadhaar]", "aadhaar"),
        ("aadhar no: 234567890123", "aadhar no: [Aadhaar]", "aadhaar"),
        ("ID 2345-6789-0123", "ID [Aadhaar]", "aadhaar"),
    ],
)
def test_each_kind_of_sensitive_value_is_masked(source: str, masked: str, kind: str) -> None:
    out, found = mask(source)
    assert out == masked
    assert found == {kind: 1}


@pytest.mark.parametrize(
    "source",
    [
        "GSTIN 27ABCPE0000F1Z5",  # invoice reading extracts it; its PAN is inside a word, so it stays
        "E-way bill 331001234567",  # 12 digits but not an Aadhaar
        "Invoice No 123456789 dated 01/11/2026",  # a long number with no account wording
        "Total 1,25,00,000.00 for 4 pumps, HSN 84137010",
        "Phone +91 00000 12345, PO-2026-000123",
        "The account manager will call; amount 125",  # account wording but no 9-digit number
    ],
)
def test_what_the_product_reads_is_left_as_written(source: str) -> None:
    assert mask(source) == (source, {})


def test_account_wording_on_another_line_does_not_mask_a_number() -> None:
    source = "Bank: HDFC Bank\nInvoice 123456789"
    assert mask(source)[0] == source


def test_masking_counts_every_message_and_keeps_roles() -> None:
    messages: Messages = [
        {"role": "system", "content": "Read it."},
        {"role": "user", "content": "PAN ABCPE0000F, A/c 501001234567890, IFSC HDFC0001234, PAN ABCPF0000G"},
    ]
    out, counts = mask_messages(messages)
    assert [m["role"] for m in out] == ["system", "user"]
    assert counts == {"pan": 2, "bank_account": 1, "ifsc": 1}
    assert "0000F" not in out[1]["content"] and "567890" not in out[1]["content"]
    assert messages[1]["content"].startswith("PAN ABCPE0000F")  # the caller's messages are not changed


class Capture:
    """A fake provider that keeps what was sent to it."""

    def __init__(self) -> None:
        self.sent: list[Messages] = []

    def chat(
        self, model: str, messages: Messages, *, temperature: float, max_tokens: int, json_output: bool
    ) -> RawResponse:
        self.sent.append(messages)
        return RawResponse('{"ok": true}', 10, 2, "stop")


@pytest.fixture
def db(control_db: str) -> Iterator[Session]:
    slug = "t" + uuid.uuid4().hex[:8]
    tenants.create_tenant(slug, load_reference=False)
    with Session(routing.engine_for(slug)) as session:
        yield session


def test_the_gateway_masks_before_sending_and_logs_only_the_counts(db: Session) -> None:
    # given a supplier email with a new bank account and a PAN
    provider = Capture()
    gateway = Gateway({"fake": provider}, {"default": [("fake", "m1")]}, limits={})
    email = "Please change our bank account to 501001234567890, IFSC HDFC0001234. PAN ABCPE0000F. GSTIN 27ABCPE0000F1Z5"
    # when any step sends it to a model
    gateway.complete("supplier_message", [{"role": "user", "content": email}], db=db)
    # then the provider never saw the account, the IFSC or the PAN, but did see the GSTIN
    sent = json.dumps(provider.sent)
    assert "501001234567890" not in sent and "HDFC0001234" not in sent and "PAN ABCPE0000F" not in sent
    assert "XXXXXXXXXXX7890" in sent and "27ABCPE0000F1Z5" in sent
    # and the call's log row says what kinds were masked, never the values
    logged = db.execute(text("select masked, error from model_call")).one()
    assert logged.masked == {"bank_account": 1, "ifsc": 1, "pan": 1}
    assert "501001234567890" not in json.dumps(logged.masked)


def test_extraction_with_its_repair_turn_is_masked_too(db: Session) -> None:
    from pydantic import BaseModel

    class Ok(BaseModel):
        ok: bool

    provider = Capture()
    gateway = Gateway({"fake": provider}, {"default": [("fake", "m1")]}, limits={})
    gateway.extract("invoice_reading", [{"role": "user", "content": "A/c 501001234567890"}], Ok, db=db)
    assert all("501001234567890" not in m["content"] for m in provider.sent[0])
    assert db.scalar(text("select masked from model_call")) == {"bank_account": 1}


def test_a_call_with_nothing_to_mask_logs_no_marker(db: Session) -> None:
    gateway = Gateway({"fake": Capture()}, {"default": [("fake", "m1")]}, limits={})
    gateway.complete("default", [{"role": "user", "content": "Two pumps by Friday"}], db=db)
    assert db.scalar(text("select masked from model_call")) is None
