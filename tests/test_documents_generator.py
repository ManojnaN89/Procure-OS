"""The synthetic documents generator (datagen/generator/documents.py): invoices as PDFs and pictures, supplier replies as
.eml files, and a manifest that is the answer key. No database, no network, no model."""

import csv
import hashlib
import importlib.util
import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from p2p.ai_steps import quote_reading
from p2p.ai_steps.evidence import check_evidence
from p2p.invoices import einvoice, reading
from p2p.platform import emails, mailbox

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "data" / "datasets" / "apex-v1"
GENERATOR = ROOT / "datagen" / "generator"

_spec = importlib.util.spec_from_file_location("datagen_documents", GENERATOR / "documents.py")
assert _spec and _spec.loader
gen = importlib.util.module_from_spec(_spec)
sys.modules["datagen_documents"] = gen
_spec.loader.exec_module(gen)
pk = gen.pk

needs_dataset = pytest.mark.skipif(not DATASET.is_dir(), reason="the apex dataset is not generated")
D = Decimal


@pytest.fixture(scope="module")
def made(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict[str, Any]]:
    out = tmp_path_factory.mktemp("documents")
    return out, gen.generate(DATASET, seed=42, limit=24, out=out)


def _csv(folder: str, name: str) -> list[dict[str, str]]:
    with (DATASET / folder / f"{name}.csv").open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


# ---- small pieces -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "text"),
    [("0", "0.00"), ("999.5", "999.50"), ("1000", "1,000.00"), ("99999.99", "99,999.99"), ("100000", "1,00,000.00"),
     ("1234567.89", "12,34,567.89"), ("-0.41", "-0.41")],
)  # fmt: skip
def test_money_is_written_with_indian_grouping(value: str, text: str) -> None:
    assert gen.money(D(value)) == text


@pytest.mark.parametrize(
    ("value", "words"),
    [("115039.00", "Rupees one lakh fifteen thousand thirty-nine only"), ("100", "Rupees one hundred only"),
     ("10000000", "Rupees one crore only"), ("0.50", "Rupees zero and fifty paise only"),
     ("21026.00", "Rupees twenty-one thousand twenty-six only"), ("1.01", "Rupees one and one paise only")],
)  # fmt: skip
def test_the_total_is_also_written_in_words(value: str, words: str) -> None:
    assert gen.amount_in_words(D(value)) == words


def test_quantities_drop_their_trailing_zeros() -> None:
    assert [gen.qty_text(q) for q in ("15.000", "2.500", "100.000", "0.250")] == ["15", "2.5", "100", "0.25"]


@pytest.mark.parametrize(
    "text",
    [
        "PAN ABCDE1234F",
        "GSTIN 27ABCDE1234F1Z5",
        "call +91 98765 43210",
        "mail someone@gmail.com",
        "mail a@b.example.com",
    ],
)
def test_text_that_could_belong_to_a_real_person_is_refused(text: str) -> None:
    assert gen.unsafe_text(text)


def test_text_made_of_the_agreed_fake_values_is_allowed() -> None:
    assert gen.unsafe_text("PAN ABCDE0000F GSTIN 27ABCDE0000F1Z5 +91 00000 12345 buyer@firm.example") == []


def test_a_pdf_keeps_brackets_and_other_odd_characters_in_its_text_layer() -> None:
    page = [pk.Text(40, 100, "Nuts (M8) \\ bolts & 50% off - Rs 5,000.00 ₹", 10)]

    data = pk.pdf_from_pages([page, page])

    text = reading.extract_text(data, "x.pdf")
    assert "Nuts (M8) \\ bolts & 50% off - Rs 5,000.00" in text
    assert data.count(b"/Type /Page ") == 2


# ---- the documents ------------------------------------------------------------------------------


@needs_dataset
def test_every_look_and_every_kind_of_reply_is_made(made: tuple[Path, dict[str, Any]]) -> None:
    out, manifest = made

    counts = manifest["counts"]

    assert {k for k in counts if k.startswith("invoice:")} == {
        f"invoice:{v}" for v in ("text_pdf", "multipage_pdf", "scan_pdf", "photo_png", "skewed_jpg", "low_contrast_pdf")
    }
    assert {k for k in counts if k.startswith("quote_reply:")} >= {
        "quote_reply:pdf_attachment", "quote_reply:body_text", "quote_reply:body_table",
    }  # fmt: skip
    assert sum(counts.values()) == len(manifest["documents"]) == 48
    assert all((out / d["file"]).is_file() for d in manifest["documents"])
    assert json.loads((out / "manifest.json").read_text(encoding="utf-8")) == manifest


@needs_dataset
def test_two_layouts_of_each_kind_and_the_held_out_suppliers_are_kept_back_as_a_hold_out_slice(
    made: tuple[Path, dict[str, Any]],
) -> None:
    _, manifest = made
    docs = manifest["documents"]
    rule = manifest["hold_out_rule"]
    held_suppliers = set(rule["suppliers"])

    assert held_suppliers, "the dataset's splits.json names no held-out supplier"
    assert {k: len(v) for k, v in rule["layouts"].items()} == {"invoice": 2, "quote_reply": 2}
    assert {d["split"] for d in docs} == {"dev", "hold_out"}
    for d in docs:
        held = (d.get("supplier_code") or d["expected"]["supplier_code"]) in held_suppliers or d["variant"] in rule[
            "layouts"
        ][d["kind"]]
        assert d["split"] == ("hold_out" if held else "dev"), d["file"]
    assert manifest["splits"] == {k: sum(1 for d in docs if d["split"] == k) for k in ("dev", "hold_out")}


def test_the_same_seed_gives_the_same_files_and_another_seed_another_order(
    made: tuple[Path, dict[str, Any]], tmp_path: Path
) -> None:
    out, manifest = made

    again = gen.generate(DATASET, seed=42, limit=24, out=tmp_path / "again")
    other = gen.generate(DATASET, seed=7, limit=6, out=tmp_path / "other")

    def digest(root: Path, m: dict[str, Any]) -> dict[str, str]:
        return {d["file"]: hashlib.sha256((root / d["file"]).read_bytes()).hexdigest() for d in m["documents"]}

    assert digest(out, manifest) == digest(tmp_path / "again", again)
    assert again == manifest
    assert {d["file"] for d in other["documents"]} != {d["file"] for d in manifest["documents"][:6]}


@needs_dataset
def test_the_answer_key_is_the_datasets_own_rows_not_something_read_off_the_document(
    made: tuple[Path, dict[str, Any]],
) -> None:
    _, manifest = made
    invoices = {r["no"]: r for r in _csv("history", "invoice")}
    lines: dict[str, list[dict[str, str]]] = {}
    for r in _csv("history", "invoice_line"):
        lines.setdefault(r["invoice_no"], []).append(r)

    for d in (d for d in manifest["documents"] if d["kind"] == "invoice"):
        row, want = invoices[d["dataset_invoice"]], d["expected"]
        assert (want["invoice_number"], want["invoice_date"], want["total"], want["supplier_gstin"]) == (
            row["supplier_invoice_no"], row["invoice_date"], row["total"], row["supplier_gstin"],
        )  # fmt: skip
        assert [(x["po_no"], x["qty"], x["unit_price"], x["taxable"]) for x in want["lines"]] == [
            (x["po_no"], x["qty"], x["unit_price"], x["taxable"]) for x in lines[row["no"]]
        ]
        assert (want["irn"] is not None) == bool(row["irn"])
        assert d["has_qr"] == (bool(row["irn"]) and row["type"] == "invoice")


@needs_dataset
def test_the_qr_code_of_a_clean_e_invoice_is_read_by_the_platforms_own_reader(
    made: tuple[Path, dict[str, Any]],
) -> None:
    out, manifest = made
    clean = [
        d
        for d in manifest["documents"]
        if d["kind"] == "invoice" and d["has_qr"] and d["variant"] in ("text_pdf", "multipage_pdf")
    ]
    assert clean

    for d in clean:
        found = einvoice.find_einvoice((out / d["file"]).read_bytes(), d["file"])

        assert found is not None and found.irn_ok
        assert (found.doc_no, found.total, found.seller_gstin, found.buyer_gstin) == (
            d["qr"]["doc_no"], D(d["qr"]["total"]), d["qr"]["seller_gstin"], d["qr"]["buyer_gstin"],
        )  # fmt: skip
        assert found.item_count == d["qr"]["item_count"]


@needs_dataset
def test_an_invoice_without_an_irn_has_no_code_and_a_picture_has_no_text_layer(
    made: tuple[Path, dict[str, Any]],
) -> None:
    out, manifest = made
    docs = [d for d in manifest["documents"] if d["kind"] == "invoice"]

    for d in docs:
        data = (out / d["file"]).read_bytes()
        if not d["has_qr"]:
            assert einvoice.find_einvoice(data, d["file"]) is None
        if d["file"].endswith(".pdf"):
            text = reading.extract_text(data, d["file"]) if d["has_text_layer"] else None
            if text is not None:
                assert d["expected"]["invoice_number"] in text and gen.money(D(d["expected"]["total"])) in text
            else:
                import pypdfium2 as pdfium  # type: ignore[import-untyped]

                pdf = pdfium.PdfDocument(data)
                assert len("".join(pdf[i].get_textpage().get_text_range() for i in range(len(pdf))).strip()) == 0
        else:
            assert not d["has_text_layer"] and d["file"].endswith((".png", ".jpg"))


@needs_dataset
def test_a_multipage_invoice_has_its_terms_on_a_second_page_and_its_total_on_the_last_page(
    made: tuple[Path, dict[str, Any]],
) -> None:
    out, manifest = made

    for d in (d for d in manifest["documents"] if d["variant"] == "multipage_pdf"):
        data = (out / d["file"]).read_bytes()
        assert d["pages"] >= 2 and data.count(b"/Type /Page ") == d["pages"]
        assert "Terms and conditions" in reading.extract_text(data, d["file"])


@needs_dataset
def test_nothing_generated_could_belong_to_a_real_person_or_company(made: tuple[Path, dict[str, Any]]) -> None:
    out, manifest = made
    texts: list[str] = []
    for d in manifest["documents"]:
        data = (out / d["file"]).read_bytes()
        if d["file"].endswith(".eml"):
            texts.append(data.decode("utf-8", "replace"))
            parsed = mailbox.parse_rfc822(data)
            texts += [parsed.body, parsed.from_addr, *parsed.to_addrs]
            texts += [reading.extract_text(b, n) for n, _, b in parsed.attachments]
        elif d["has_text_layer"]:
            texts.append(reading.extract_text(data, d["file"]))

    assert gen.unsafe_text("\n".join(texts)) == []


# ---- the replies --------------------------------------------------------------------------------


def _reading_from_key(expected: dict[str, Any]) -> quote_reading.QuoteReading:
    if expected["intent"] == "decline":
        return quote_reading.QuoteReading(intent="decline", decline_reason=expected["decline_reason"], confidence=0.9)
    return quote_reading.QuoteReading(
        intent="quote",
        lines=[
            quote_reading.ReadLine(
                item=x["item_written"], unit_price=x["unit_price"], gst_rate=x["gst_rate"], delivery_days=x["delivery_days"],
                price_includes_gst=x["price_includes_gst"],
            )
            for x in expected["lines"]
        ],
        valid_until=expected["valid_until"], freight=expected["freight"], packing_forwarding=expected["packing_forwarding"],
        confidence=0.9,
    )  # fmt: skip


@needs_dataset
def test_each_reply_is_routed_by_its_address_and_a_perfect_reading_of_it_passes_the_evidence_check(
    made: tuple[Path, dict[str, Any]],
) -> None:
    out, manifest = made

    for d in (d for d in manifest["documents"] if d["kind"] == "quote_reply"):
        mail = mailbox.parse_rfc822((out / d["file"]).read_bytes())

        assert mail.message_id == d["message_id"]
        assert emails.parse_plus_tag(mail.to_addrs[0]) == d["rfq_no"]
        assert mail.in_reply_to is not None and mail.from_addr.endswith(".example")
        source = mail.body + "\n".join(reading.extract_text(b, n) for n, _, b in mail.attachments)
        assert (len(mail.attachments) == 1) == (d["variant"] == "pdf_attachment")
        value = _reading_from_key(d["expected"])
        failures = check_evidence(value, source, quote_reading.STEP.evidence_paths)
        assert failures == [], (d["file"], failures)


@needs_dataset
def test_the_messy_replies_say_in_the_key_what_a_careful_reader_must_not_guess(
    made: tuple[Path, dict[str, Any]],
) -> None:
    _, manifest = made
    quotes = [d for d in manifest["documents"] if d["kind"] == "quote_reply" and d["variant"] != "decline"]

    by_quirk: dict[str, list[dict[str, Any]]] = {}
    for d in quotes:
        for q in d["quirks"]:
            by_quirk.setdefault(q, []).append(d)

    assert {"unit_differs", "incl_gst", "relative_validity"} <= set(by_quirk)
    for d in by_quirk["unit_differs"]:
        first = d["expected"]["lines"][0]
        assert first["unit_price"] is None and D(first["derivable_unit_price"]) * 10 == D(first["printed_price"])
    for d in by_quirk["incl_gst"]:
        assert all(
            x["price_includes_gst"] is True and not d["expected"]["prices_exclude_gst"] for x in d["expected"]["lines"]
        )
    for d in by_quirk["relative_validity"]:
        assert d["expected"]["valid_until"] is None and d["expected"]["relative_validity_days"] > 0
    for d in by_quirk.get("partial", []):
        assert d["expected"]["missing_rfq_line_nos"]


@needs_dataset
def test_a_refusal_carries_the_datasets_reason() -> None:
    # the demo companies' suppliers always quote, so one invitation is turned into a refusal here
    ctx = gen.Ctx(DATASET)
    sent = next(m for m in ctx.emails if m["kind"] == "rfq_send")
    key = (sent["linked_doc_no"], sent["supplier_code"])
    ctx.rfq_supplier[key] = {**ctx.rfq_supplier[key], "declined": "true", "decline_reason": "Not in our product range"}
    mail = {
        **sent,
        "id": "EML-999999",
        "message_id": "<decline@mail.example>",
        "direction": "in",
        "kind": "decline_rfq",
    }

    data, entry, _ = gen.make_quote_mail(ctx, mail, 0, 42)

    assert entry["variant"] == "decline"
    assert entry["expected"] == {"intent": "decline", "decline_reason": "Not in our product range"}
    assert "Not in our product range" in mailbox.parse_rfc822(data).body


@needs_dataset
def test_the_command_line_writes_the_documents_and_a_limit_limits_them(tmp_path: Path) -> None:
    run = subprocess.run(  # noqa: S603
        [sys.executable, "-I", str(GENERATOR / "documents.py"), "--dataset", str(DATASET), "--seed", "42", "--limit", "3", "--out", str(tmp_path)],
        capture_output=True, text=True, check=False,
    )  # fmt: skip

    assert run.returncode == 0, run.stderr
    assert "wrote 6 documents" in run.stdout
    assert len(json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))["documents"]) == 6
    assert len(list((tmp_path / "invoices").iterdir())) == 3 and len(list((tmp_path / "quotes").iterdir())) == 3


@needs_dataset
def test_running_again_clears_what_an_earlier_run_left(tmp_path: Path) -> None:
    gen.generate(DATASET, seed=42, limit=5, out=tmp_path)
    left = [p.name for p in (tmp_path / "invoices").iterdir()]

    gen.generate(DATASET, seed=43, limit=2, out=tmp_path)

    assert len(list((tmp_path / "invoices").iterdir())) == 2 and len(left) == 5
