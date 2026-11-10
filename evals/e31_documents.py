"""E31: the synthetic documents are real enough to test readers with, and the deterministic readers (QR code, text layer,
arithmetic) do what they claim. No model is called.

The generator (datagen/generator/documents.py) is run from its command line on the eval dataset. Its answer key comes from
the dataset's CSV rows; this eval re-reads the CSVs itself and does not trust the key.

- The key agrees with the raw CSV rows for every invoice, and every invoice with lines and every reply to an RFQ has a file.
- The same seed makes byte-identical files; nothing in any document could belong to a real person (own patterns, not the
  generator's).
- The platform's QR reader reads the e-invoice code on at least 95% of the documents that carry one, and on every clean one.
- On documents with a text layer the rows found by arithmetic are exactly the key's lines on at least 95% of them, and a
  perfect reading of the key passes the platform's evidence check against the text on every one.
- Silent errors are 0: no document passes the gate that lets the code read an e-invoice alone (code valid, row count equals
  the code's item count, the rows with their GST come to the code's total) with rows that differ from the key.
- A perfect reading of every reply passes the evidence check; each reply is routed by its plus-address to its RFQ.
- Scans and photographs: a sample goes through the platform's OCR and is scored the same way.
It also reports the share of invoices that still need a model or a person, and why."""

import hashlib
import json
import re
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Any

import lib
from lib import Outcome, pct, rows

ID = "E31"
TITLE = (
    "Synthetic invoices and replies read back correctly by the code readers, and what still needs a model is counted"
)
REQUIREMENT = (
    "Plan P1-03: documents generated from the dataset with an answer key; QR >=95% (100% on clean), rows by arithmetic >=95% "
    "on text-layer invoices, silent errors 0, perfect readings pass the evidence check, deterministic, nothing real"
)

D = Decimal
GENERATOR = lib.ROOT / "datagen" / "generator" / "documents.py"
IMAGE_LOOKS = ("scan_pdf", "photo_png", "skewed_jpg", "low_contrast_pdf")

# What could belong to a real person or company, written here again so the generator does not mark its own work.
PAN = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")
GSTIN = re.compile(r"\b\d{2}([A-Z]{5}\d{4}[A-Z])[1-9A-Z]Z[0-9A-Z]\b")
PHONE = re.compile(r"(?:\+91|0091)[ -]?(\d{5})[ -]?\d{5}")
EMAIL = re.compile(r"[\w.+-]+@([\w-]+(?:\.[\w-]+)*)")


def unsafe(text: str) -> list[str]:
    bad = [m for m in PAN.findall(text) if "0000" not in m]
    bad += [m.group(0) for m in GSTIN.finditer(text) if "0000" not in m.group(1)]
    bad += [m.group(0) for m in PHONE.finditer(text) if m.group(1) != "00000"]
    bad += [m.group(0) for m in EMAIL.finditer(text) if not m.group(1).endswith(".example")]
    return bad


def generate(out: Path, *, limit: int | None, seed: int = 42) -> dict[str, Any]:
    cmd = [sys.executable, "-I", str(GENERATOR), "--dataset", str(lib.DATASET), "--seed", str(seed), "--out", str(out)]
    if limit is not None:
        cmd += ["--limit", str(limit)]
    run = subprocess.run(cmd, capture_output=True, text=True, check=False)  # noqa: S603
    if run.returncode:
        raise RuntimeError(f"the generator failed:\n{run.stderr[-1500:]}")
    return json.loads((out / "manifest.json").read_text(encoding="utf-8"))


def hashes(root: Path, manifest: dict[str, Any]) -> dict[str, str]:
    out = {d["file"]: hashlib.sha256((root / d["file"]).read_bytes()).hexdigest() for d in manifest["documents"]}
    out["manifest.json"] = hashlib.sha256((root / "manifest.json").read_bytes()).hexdigest()
    return out


# ---- reading with the platform's own code ---------------------------------------------------------------


def rows_match(parsed: list[Any], charges: dict[str, Any], exp: dict[str, Any]) -> bool:
    got = [(D(r.qty), D(r.unit_price), D(r.taxable)) for r in parsed]
    want = [(D(x["qty"]), D(x["unit_price"]), D(x["taxable"])) for x in exp["lines"]]
    freight = next((D(c["amount"]) for c in exp["charges"] if c["type"] == "freight"), None)
    packing = next((D(c["amount"]) for c in exp["charges"] if c["type"] != "freight"), None)
    got_freight = charges["freight"][0] if "freight" in charges else None
    got_packing = charges["packing_forwarding"][0] if "packing_forwarding" in charges else None
    return got == want and got_freight == freight and got_packing == packing


def gate_passes(parsed: list[Any], charges: dict[str, Any], qr: Any, supplier: dict[str, str]) -> bool:
    """The test that lets the platform read an e-invoice by code alone (reading.draft_from_qr and _finish): the row count
    is the code's item count, and the rows with their GST come to the code's total within one rupee."""
    from p2p.formulas.gst import Registration
    from p2p.formulas.orders import charge_rate, line_amounts

    if qr is None or not parsed or len(parsed) != qr.item_count or any(r.gst_rate is None for r in parsed):
        return False
    state, place = supplier["state_code"] or qr.seller_gstin[:2], qr.buyer_gstin[:2]
    registration = Registration(supplier["gst_reg_type"] or "regular")
    total = D(0)
    for r in parsed:
        calc = line_amounts(r.qty, r.unit_price, r.gst_rate, state, place, registration)
        total += calc.taxable + calc.cgst + calc.sgst + calc.igst
    top = charge_rate([r.gst_rate for r in parsed])
    for amount, rate in charges.values():
        calc = line_amounts(1, amount, rate if rate is not None else top, state, place, registration)
        total += calc.taxable + calc.cgst + calc.sgst + calc.igst
    return abs(total.quantize(D("1")) - qr.total) <= D("1.00")


def without_repeats(parsed: list[Any]) -> list[Any]:
    """The rows with a row that repeats the one before it left out (a table the OCR gave twice)."""
    out: list[Any] = []
    for r in parsed:
        if not any((r.qty, r.unit_price, r.taxable) == (o.qty, o.unit_price, o.taxable) for o in out):
            out.append(r)
    return out


def perfect_invoice(exp: dict[str, Any]) -> Any:
    """What a reader that copied the page perfectly would answer, built from the key."""
    from p2p.ai_steps.invoice_reading import InvoiceReading, ReadLine

    def amount(kind: str) -> str | None:
        return next((c["amount"] for c in exp["charges"] if c["type"] == kind), None)

    return InvoiceReading(
        document_type=exp["document_type"], supplier_name=exp["supplier_name"], supplier_gstin=exp["supplier_gstin"],
        buyer_gstin=exp["buyer_gstin"], invoice_number=exp["invoice_number"], invoice_date=exp["invoice_date"], irn=exp["irn"],
        po_numbers=exp["po_numbers"], ref_invoice_no=exp["ref_invoice_number"],
        lines=[
            ReadLine(description=x["description"], po_no=x["po_no"], qty=x["qty"], unit_price=x["unit_price"],
                     gst_rate=x["gst_rate"], taxable=x["taxable"])
            for x in exp["lines"]
        ],
        freight=amount("freight"), packing_forwarding=amount("packing_forwarding"), taxable_total=exp["taxable"],
        cgst=exp["cgst"] if D(exp["cgst"]) else None, sgst=exp["sgst"] if D(exp["sgst"]) else None,
        igst=exp["igst"] if D(exp["igst"]) else None, total=exp["total"], confidence=0.9,
    )  # fmt: skip


def perfect_quote(exp: dict[str, Any]) -> Any:
    from p2p.ai_steps.quote_reading import QuoteReading, ReadLine

    if exp["intent"] == "decline":
        return QuoteReading(intent="decline", decline_reason=exp["decline_reason"], confidence=0.9)
    return QuoteReading(
        intent="quote",
        lines=[
            ReadLine(item=x["item_written"], unit_price=x["unit_price"], gst_rate=x["gst_rate"], delivery_days=x["delivery_days"],
                     price_includes_gst=x["price_includes_gst"])
            for x in exp["lines"]
        ],
        valid_until=exp["valid_until"], freight=exp["freight"], packing_forwarding=exp["packing_forwarding"], confidence=0.9,
    )  # fmt: skip


def qr_matches(found: Any, qr: dict[str, Any]) -> bool:
    return bool(
        found is not None and found.irn_ok and found.doc_no == qr["doc_no"] and found.total == D(qr["total"])
        and found.seller_gstin == qr["seller_gstin"] and found.buyer_gstin == qr["buyer_gstin"]
        and found.item_count == qr["item_count"] and found.doc_date is not None and found.doc_date.isoformat() == qr["doc_date"]
    )  # fmt: skip


def run(ctx) -> Outcome:
    from p2p.ai_steps import invoice_reading, quote_reading
    from p2p.ai_steps.evidence import check_evidence
    from p2p.invoices import einvoice, reading
    from p2p.platform import emails, mailbox

    out = Outcome(True)
    suppliers = {r["code"]: r for r in rows("master", "supplier")}
    work = Path(tempfile.mkdtemp(prefix="e31_"))
    limit = 40 if ctx.quick else None
    docs_dir = work / "docs"
    manifest = generate(docs_dir, limit=limit)
    documents = manifest["documents"]
    invoices = [d for d in documents if d["kind"] == "invoice"]
    replies = [d for d in documents if d["kind"] == "quote_reply"]
    out.metrics["documents: invoices / replies"] = f"{len(invoices)} / {len(replies)}"
    out.metrics["looks"] = ", ".join(
        f"{k.split(':', 1)[1]} {v}" for k, v in manifest["counts"].items() if k.startswith("invoice:")
    )

    # the hold-out slice: layouts and suppliers kept back to measure on (plan P1-03)
    held = [d for d in documents if d["split"] == "hold_out"]
    out.metrics["dev / hold-out documents"] = f"{manifest['splits']['dev']} / {manifest['splits']['hold_out']}"
    held_layouts = {k: sorted({d["variant"] for d in held if d["kind"] == k}) for k in ("invoice", "quote_reply")}
    out.metrics["hold-out layouts seen"] = held_layouts
    if not limit and (not held or any(len(v) < 2 for v in held_layouts.values())):
        out.find(
            "high", f"the hold-out slice is missing or has fewer than two layouts of a document kind: {held_layouts}"
        )
    if any(d["split"] == "dev" and d["variant"] in manifest["hold_out_rule"]["layouts"][d["kind"]] for d in documents):
        out.find("high", "a held-out layout appears in the dev slice")

    # 1. the key is the dataset's, and nothing is missing -------------------------------------------------------
    csv_inv = {r["no"]: r for r in rows("history", "invoice")}
    csv_lines: dict[str, list[dict[str, str]]] = defaultdict(list)
    for r in rows("history", "invoice_line"):
        csv_lines[r["invoice_no"]].append(r)
    key_wrong = 0
    for d in invoices:
        row, e = csv_inv[d["dataset_invoice"]], d["expected"]
        same = (e["invoice_number"], e["invoice_date"], D(e["total"]), e["supplier_gstin"], e["buyer_gstin"], bool(e["irn"])) == (
            row["supplier_invoice_no"], row["invoice_date"], D(row["total"]), row["supplier_gstin"], row["buyer_gstin"], bool(row["irn"])
        )  # fmt: skip
        same = same and [(x["po_no"], D(x["qty"]), D(x["unit_price"]), D(x["taxable"])) for x in e["lines"]] == [
            (x["po_no"], D(x["qty"]), D(x["unit_price"]), D(x["taxable"])) for x in csv_lines[row["no"]]
        ]
        key_wrong += not same
    out.metrics["key differs from the CSV rows"] = key_wrong
    if key_wrong:
        out.find("high", f"{key_wrong} answer-key entries differ from the dataset's own rows")
    if limit is None:
        want_inv = sum(1 for no in csv_inv if csv_lines[no])
        want_mail = sum(
            1
            for r in rows("history", "email_message")
            if r["kind"] in ("quote_cover", "decline_rfq") and r["direction"] == "in"
        )
        out.metrics["files vs rows"] = f"invoices {len(invoices)}/{want_inv}, replies {len(replies)}/{want_mail}"
        if (len(invoices), len(replies)) != (want_inv, want_mail):
            out.find(
                "high",
                f"documents are missing: invoices {len(invoices)} of {want_inv}, replies {len(replies)} of {want_mail}",
            )
    missing = [d["file"] for d in documents if not (docs_dir / d["file"]).is_file()]
    if missing:
        out.find("high", f"{len(missing)} files named in the manifest do not exist, e.g. {missing[0]}")

    # 2. the QR code, read by the platform ------------------------------------------------------------------------
    qr_try: Counter[str] = Counter()
    qr_ok: Counter[str] = Counter()
    qr_docs: dict[str, Any] = {}
    for d in (d for d in invoices if d["has_qr"]):
        found = einvoice.find_einvoice((docs_dir / d["file"]).read_bytes(), d["file"])
        qr_try[d["variant"]] += 1
        good = qr_matches(found, d["qr"])
        qr_ok[d["variant"]] += good
        qr_docs[d["file"]] = found if good else None
    total_try, total_ok = sum(qr_try.values()), sum(qr_ok.values())
    out.metrics["QR read, all looks"] = f"{total_ok}/{total_try} ({pct(total_ok, total_try)}%)"
    for variant in sorted(qr_try):
        out.notes.append(f"QR {variant}: {qr_ok[variant]}/{qr_try[variant]}")
    for name in ("dev", "hold_out"):
        tried = [d for d in invoices if d["has_qr"] and d["split"] == name]
        done = sum(1 for d in tried if qr_docs.get(d["file"]) is not None)
        out.metrics[f"QR read, {name} slice"] = f"{done}/{len(tried)} ({pct(done, len(tried))}%)"
    clean_bad = [v for v in ("text_pdf", "multipage_pdf") if qr_ok[v] != qr_try[v]]
    if clean_bad or pct(total_ok, total_try) < 95:
        out.find("high", f"QR codes read: {total_ok}/{total_try}; clean looks with a failure: {clean_bad or 'none'}")

    # 3. rows by arithmetic and the evidence check on the text layer ---------------------------------------------
    text_docs = [d for d in invoices if d["has_text_layer"]]
    rows_ok = evidence_bad = silent = gate = 0
    row_failures: list[str] = []
    needs: Counter[str] = Counter()
    gate_by_file: dict[str, bool] = {}
    for d in text_docs:
        data = (docs_dir / d["file"]).read_bytes()
        source = reading.extract_text(data, d["file"])
        po_numbers = d["expected"]["po_numbers"]
        parsed, charges = reading.parse_rows(source, po_numbers)
        exact = rows_match(parsed, charges, d["expected"])
        rows_ok += exact
        if not exact:
            row_failures.append(d["file"])
        qr = qr_docs.get(d["file"])
        passes = gate_passes(parsed, charges, qr, suppliers[d["expected"]["supplier_code"]])
        gate += passes
        gate_by_file[d["file"]] = passes
        if passes and not exact:
            silent += 1
            out.find("high", f"silent error: {d['file']} passes the e-invoice gate with rows that differ from the key")
        if evidence := check_evidence(perfect_invoice(d["expected"]), source, invoice_reading.STEP.evidence_paths):
            evidence_bad += 1
            out.find(
                "medium",
                f"{d['file']}: a perfect reading fails the evidence check: {evidence[0].path}={evidence[0].value!r}",
            )
        if not d["has_qr"]:
            needs["no IRN, so no QR code (a model or a person reads it)"] += 1
        elif qr is None:
            needs["QR code not readable"] += 1
        elif not exact:
            needs["rows not found by arithmetic"] += 1
    out.metrics["rows exact, text layer"] = f"{rows_ok}/{len(text_docs)} ({pct(rows_ok, len(text_docs))}%)"
    out.metrics["silent errors"] = silent
    out.metrics["e-invoice gate passes, text layer"] = f"{gate}/{len(text_docs)}"
    out.metrics["perfect readings failing the evidence check"] = evidence_bad
    if pct(rows_ok, len(text_docs)) < 95:
        out.find("high", f"rows by arithmetic exact on {rows_ok}/{len(text_docs)} text-layer invoices")
    if row_failures:
        out.find(
            "low",
            f"rows by arithmetic missed {len(row_failures)} text-layer invoices (they would go to the model): {', '.join(row_failures[:6])}",
        )

    # 4. scans and photographs: a sample through the platform's OCR ----------------------------------------------
    images = [d for d in invoices if not d["has_text_layer"]]
    per_look = 1 if ctx.quick else 2
    sample = [d for look in IMAGE_LOOKS for d in [x for x in images if x["variant"] == look and x["has_qr"]][:per_look]]
    ocr_rows = ocr_evidence = ocr_repeated = ocr_if_once = ocr_gate = 0
    for d in sample:
        data = (docs_dir / d["file"]).read_bytes()
        try:
            source = reading.extract_text(data, d["file"])
        except Exception as exc:  # noqa: BLE001 - a file the OCR cannot read is a result, not a crash
            out.notes.append(f"OCR failed on {d['file']}: {type(exc).__name__}")
            continue
        parsed, charges = reading.parse_rows(source, d["expected"]["po_numbers"])
        exact = rows_match(parsed, charges, d["expected"])
        ocr_rows += exact
        passes = gate_passes(parsed, charges, qr_docs.get(d["file"]), suppliers[d["expected"]["supplier_code"]])
        ocr_gate += passes
        if passes and not exact:
            out.find(
                "high",
                f"silent error: the OCR text of {d['file']} passes the e-invoice gate with rows that differ from the key",
            )
        once = rows_match(without_repeats(parsed), charges, d["expected"])
        ocr_if_once += once and not exact
        ocr_repeated += len(parsed) > len(d["expected"]["lines"])
        ocr_evidence += not check_evidence(perfect_invoice(d["expected"]), source, invoice_reading.STEP.evidence_paths)
    rate = (
        ocr_gate / len(sample) if sample else 0.0
    )  # the share of readable-code images that the gate would let through
    out.metrics["OCR sample: rows exact / gate passes / evidence passes"] = (
        f"{ocr_rows}/{len(sample)} / {ocr_gate}/{len(sample)} / {ocr_evidence}/{len(sample)}"
    )
    out.metrics["OCR sample: rows exact if a repeated row is ignored"] = f"{ocr_rows + ocr_if_once}/{len(sample)}"
    if ocr_repeated:
        out.find(
            "low",
            f"the OCR text of {ocr_repeated} of {len(sample)} sampled scans and photographs gives the item rows twice (the ruled "
            "table appears once in the page text and once in the table list that extract_text appends), so the row count differs "
            "from the QR code's item count and an image e-invoice goes to the model instead of being read by code",
        )
    if len(sample) < len(IMAGE_LOOKS):
        out.find("medium", "the sample of scans and photographs did not cover every look")
    # Image documents: the QR code decides first; the rows then need the OCR text, which works at the sampled rate.
    for d in images:
        if not d["has_qr"]:
            needs["no IRN, so no QR code (a model or a person reads it)"] += 1
        elif qr_docs.get(d["file"]) is None:
            needs["QR code not readable"] += 1
    image_qr_ok = sum(1 for d in images if d["has_qr"] and qr_docs.get(d["file"]) is not None)
    ocr_miss = round(image_qr_ok * (1 - rate))
    needs["scan/photo whose OCR text the arithmetic cannot read (estimated from the sample)"] += ocr_miss

    review = sum(needs.values())
    out.metrics["need a model or a person"] = f"{review}/{len(invoices)} ({pct(review, len(invoices))}%)"
    for reason, n in needs.most_common():
        out.notes.append(f"needs a model or a person: {n} - {reason}")
    out.notes.append(
        f"read by code alone: {len(invoices) - review}/{len(invoices)} (text-layer gate passes {gate}, plus an estimate of "
        f"{image_qr_ok - ocr_miss} scans and photographs from the OCR sample)"
    )

    # 5. the replies ------------------------------------------------------------------------------------------------
    routed = evidence_failed = 0
    quirks: Counter[str] = Counter()
    for d in replies:
        mail = mailbox.parse_rfc822((docs_dir / d["file"]).read_bytes())
        ok = (
            mail.message_id == d["message_id"] and emails.parse_plus_tag(mail.to_addrs[0]) == d["rfq_no"]
            and bool(mail.in_reply_to) and (len(mail.attachments) == 1) == (d["variant"] == "pdf_attachment")
        )  # fmt: skip
        routed += ok
        source = mail.body + "\n" + "\n".join(reading.extract_text(b, n) for n, _, b in mail.attachments)
        if check_evidence(perfect_quote(d["expected"]), source, quote_reading.STEP.evidence_paths):
            evidence_failed += 1
            out.find("medium", f"{d['file']}: a perfect reading of the reply fails the evidence check")
        quirks.update(d["quirks"])
    out.metrics["replies routed by address / evidence passes"] = (
        f"{routed}/{len(replies)} / {len(replies) - evidence_failed}/{len(replies)}"
    )
    out.notes.append(
        "reply looks: "
        + ", ".join(f"{k.split(':', 1)[1]} {v}" for k, v in manifest["counts"].items() if k.startswith("quote_reply:"))
    )
    out.notes.append("reply quirks: " + (", ".join(f"{k} {v}" for k, v in sorted(quirks.items())) or "none"))
    if routed != len(replies):
        out.find(
            "high", f"{len(replies) - routed} replies are not routed to their RFQ by address, thread and attachments"
        )
    if limit is None:
        absent = {"partial", "unit_differs", "incl_gst", "relative_validity"} - set(quirks)
        if absent:
            out.find("medium", f"no reply shows the quirk(s) {sorted(absent)}")

    # 6. nothing real, and the same seed gives the same files ------------------------------------------------------
    texts: list[str] = []
    for d in documents:
        data = (docs_dir / d["file"]).read_bytes()
        if d["file"].endswith(".eml"):
            parsed = mailbox.parse_rfc822(data)
            texts += [
                parsed.body,
                parsed.from_addr,
                *parsed.to_addrs,
                *[reading.extract_text(b, n) for n, _, b in parsed.attachments],
            ]
        elif d["has_text_layer"]:
            texts.append(reading.extract_text(data, d["file"]))
    found = unsafe("\n".join(texts))
    out.metrics["real-looking identifiers found"] = len(found)
    if found:
        out.find("high", f"documents carry things that could be real: {found[:5]}")
    a, b = work / "again_a", work / "again_b"
    first, second = generate(a, limit=12, seed=7), generate(b, limit=12, seed=7)
    same = hashes(a, first) == hashes(b, second)
    out.metrics["same seed, same bytes"] = "yes" if same else "NO"
    if not same:
        out.find("high", "two runs with the same seed gave different files")
    return out
