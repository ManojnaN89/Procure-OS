# Data generator

Generates the synthetic dataset for the P2P platform: a fictional company (CBC Private Limited) buying for 24 months,
with a hidden truth the platform never sees. Same seed and generator version give byte-identical files.

```
python -I datagen/generator/cli.py --size demo --seed 42      # 24 months: 1 Nov 2024 to 31 Oct 2026
python -I datagen/generator/cli.py --size small --seed 42     # last 3 months, for fast tests
python -I datagen/generator/validate.py data/datasets/cbc-demo-v1-seed42
```

Output goes to `data/datasets/cbc-<size>-v1-seed<seed>/` (git-ignored; regenerate it any time):

| Folder | Contents |
|---|---|
| `master/` | Organisation, users, approval rules, budgets, items, suppliers, contracts, price records, planning parameters, opening stock |
| `history/` | Requests, approvals, RFQs, quotes, awards, POs, receipts, stock movements, invoices, TDS, payments, bank lines, email metadata |
| `truth/` | Hidden: item demand patterns and stockouts, supplier behaviour profiles, every planted problem (`truth_case`). Read only by tests |
| `reference/` | Tax and calendar reference tables copied from `data/reference` |
| `manifest.json`, `splits.json`, `report.md` | Seed and counts, held-out suppliers, quality report |

## The one-plant profile (the demo company)

```
python -I datagen/generator/profile_plant.py data/datasets/cbc-demo-v1-seed42    # -> plant-demo-v1-seed42
python -I datagen/generator/profile_plant.py data/datasets/cbc-small-v1-seed42   # -> plant-small-v1-seed42
```

Cuts a generated dataset down to one plant that buys industrial spares and consumables (mechanical spares, electrical, tools,
safety gear, packaging): 137 items, 25 suppliers, 11 people in 5 departments, and only the requests, quotations, orders, receipts,
invoices and payments that are wholly about those items. Every row is a row of the source, or that row with a person's code replaced.
The approval rules use only roles that exist in the small team (no CFO): the Managing Director is the last approver and approves payment runs.
The evals use `plant-small` (set `P2P_EVAL_DATASET` to use another).

## How it is built

| File | Role |
|---|---|
| `catalog/` | Content authored once with Claude and kept as files: organisation, items, suppliers, text banks. No AI runs at generation time |
| `generator/master.py` | Combines the content banks; adds price index, contracts, price records, demand truth, planning parameters, budgets |
| `generator/streams.py`, `flow.py`, `flow2.py`, `simulate.py` | Day-by-day simulation: stock-driven and ad hoc requests, approvals, RFQs, POs, receipts, invoices, planted problems |
| `generator/finance.py` | TDS in booking order, MSME due dates, weekly payment runs, bank lines, cut-off filter, statuses, chronological numbering |
| `generator/validate.py` | Quality checks and `report.md` |
| `generator/ids.py` | GSTIN/PAN helpers. Every generated PAN has the digits 0000, which are never issued, so none can belong to a real taxpayer |

## Documents (v1)

```
backend/.venv/Scripts/python -I datagen/generator/documents.py --dataset data/datasets/plant-small-v1-seed42 --seed 42 [--limit N] [--out DIR]
```

Run it with the backend's Python (it needs Pillow, numpy and zxing-cpp). It writes `<dataset>/documents/`, git-ignored like the rest of `data/datasets/`:

| What | Files |
|---|---|
| Supplier invoices, one per invoice of the dataset | `invoices/<invoice no>.pdf`, `.png` or `.jpg`, in six looks: `text_pdf` (clean, text layer), `multipage_pdf` (text layer, rows split over pages, terms page), `scan_pdf` (picture only, slightly crooked, grain), `photo_png` (phone photo: tilt, uneven light, soft), `skewed_jpg` (put in several degrees crooked), `low_contrast_pdf` (faded print on grey). An invoice with an IRN carries the e-invoice QR code and prints its IRN; a credit note or an invoice without an IRN has none |
| Supplier replies to RFQs, one per reply in the dataset | `quotes/<email id>_<rfq>_<supplier>.eml`: prices in the body as text or as a table, or in an attached quotation PDF, or a refusal with the dataset's reason. Some are messy on purpose (`quirks`): `partial` (not every line quoted), `unit_differs` (a price per box of 10), `incl_gst` (prices include GST), `relative_validity` ("valid for 15 days") |
| The answer key | `manifest.json`: for each file its look, whether it has a text layer and a QR code, and `expected`, the values a reader should find |

The key is copied from the dataset's CSV rows, never read back from the rendered document, so it can score a reader independently. A value a careful reader must not guess is `null` in the key (the unit price of a price per box, the end date of a relative validity) with the printed figure beside it. The look and the quirk of each file are chosen by the generator from the seed (interleaved, so a small `--limit` still shows several); the dataset's `email_message.attachments` column is not followed. The e-invoice signature is a placeholder, as in the platform's own tests.

Safe by construction: every document is checked before it is written (a PAN or GSTIN without `0000`, a phone not `+91 00000 ...`, an email outside `.example` stops the run). Same dataset and seed give the same files on the same machine; the pictures use the installed Arial or DejaVu font, so their bytes can differ across machines. `generator/pagekit.py` has the PDF writer, the page drawing and the damage (scan, photo, skew, fade).

Eval E31 reads them back with the platform's code readers (QR, text layer, arithmetic) and counts what still needs a model or a person.

## Known simplifications (v0.2)

- Documents are generated separately by `documents.py` (above). The `document_file` table, our outgoing templates (RFQ, PO, reminder) and the text of the dataset's other emails are not generated; `email_message` rows hold metadata only.
- `match_result` is not generated: it is the platform's own output. `truth/truth_match_expected.csv` says what a correct match must conclude for every invoice.
- Advance payments exist only for the five suppliers on 30% advance terms. TDS is deducted on the advance at payment, and the invoice TDS base is reduced by the advanced amount.
- Free-text request lines are not generated; the document step adds them.
- Tax values are for testing and are marked for confirmation by a tax adviser.
- The planning parameters (reorder points) are deliberately imperfect, so stockouts and overstock exist.
- Tables that only the running platform produces (tasks, notifications, audit, agent runs, proposals) are not generated.
