# Data generator

Generates the synthetic demo companies for the P2P platform: each company is one profile file, and the generator writes its
master data, its ERP input files and twelve months of history, with a hidden truth the platform never sees. Same profile and
seed give byte-identical files. Everything is made up: PANs carry the digits 0000, phones are `+91 00000 ...`, emails end in
`.example`.

## The companies (`apex`, `brightwave`)

```
python -I datagen/generator/company_profile.py apex          # -> data/datasets/apex-v1
python -I datagen/generator/company_profile.py brightwave    # -> data/datasets/brightwave-v1
python -I datagen/generator/company_profile.py --check data/datasets/apex-v1
python -m p2p.cli demo reset apex                            # from backend/: drop and make the company, logins to .dev-credentials
```

Each company is one file, `datagen/catalog/<name>/company.yaml`: people and logins, approval rules and who the chain roles are,
settings, budgets, categories, items with opening stock, suppliers, contracts, last prices paid, and its ERP input files. A new
company is a new file. `master/` has the files and headers agreed in `generator/master_files.py` (plain configuration every
company shares, such as the chart of accounts, comes from `catalog/common/`); `inputs/` holds the company's planning files
(see section F of `docs/data-dictionary.md`), loaded by `python -m p2p.cli import demand <tenant> <dataset>`.

Each also has twelve months of history (1 Nov 2025 to the cutoff, 31 Oct 2026; decisions D36 and D37), driven by the
`history:` section of its profile (supplier behaviour, demand, buying days, planted problems). Same seed, same files.
`history/` (with the metadata of the RFQ emails) and `truth/` (with each supplier's behaviour in `truth_supplier`) are what
`import stock` and the evals read; `inputs/history/` has the production orders, material requests and joiner plans of the year.
The history ends exactly at the profile's opening stock (so the demo's MO-1 sees that stock, with nothing open for its
materials): `master/stock_opening.csv` is the stock on 1 Nov 2025, worked out backwards from the movements, and
`demo reset` loads the movements. New answer keys: `truth_shortfall`, `truth_combined_lines`, `truth_supplier_selection`,
`truth_grni`, `truth_payment` (section D of the data dictionary).

| File | Role |
|---|---|
| `generator/history_plan.py` | What the year needs, from the profile and the seed: production orders, joiner plans, services, splits, breakdowns |
| `generator/history.py`, `history_buying.py` | The day loop (class `History`): stock first, requisitions, approval by value band, the buying route, RFQs, orders with GST, confirmations, deliveries, inspection, service entries |
| `generator/history_money.py` | Invoices with the planted problems, the expected match, blocks, TDS, due dates, weekly payment runs, bank lines, statements |
| `generator/history_close.py` | The cutoff, statuses at the cutoff, answer keys, numbering in date order, the files |
| `generator/history_check.py` | Checks from the files alone: references, GST, TDS (from `data/reference/tds_sections.csv`) and totals to the paisa, stock, working days, segregation of duties, every planted problem counted |

A run that does not land on the profile's stock is drawn again (the attempt number is part of the seed). TDS rules, due
dates, payment-run days and the numbering in date order are in `generator/finance.py`. `splits.json` names the suppliers
whose documents are held out from tuning (the profile's `hold_out_suppliers`).

- **Apex Pumps** (pump maker, plant at Chakan, Mon-Sat): 9 people, 16 items in four categories, 6 suppliers (one in Gujarat, one
  micro enterprise), a rate contract for castings. Inputs: two pumps, their bills of materials, a sales order, a demand plan and
  two production orders whose material lines are worked out from the bills of materials.
- **Brightwave Technologies** (IT services, office in Bengaluru, Mon-Fri): 9 people, 16 items (laptops, licences, cloud, office
  supplies, facility services), 5 suppliers (one in Tamil Nadu, one micro enterprise), a rate contract and a service contract.
  IT hardware and software always need the IT head. Inputs: joiner kits and the joiners planned for December.

## How it is built

| File | Role |
|---|---|
| `catalog/<name>/company.yaml` | One company: people, rules, items, suppliers, contracts, inputs and how its year goes. Authored once with Claude; no AI runs at generation time |
| `catalog/common/`, `catalog/text/` | Configuration every company shares (chart of accounts, banks, payment terms, number series); text banks the evals use for requests and supplier emails |
| `generator/company_profile.py`, `master_files.py` | Reads a profile, writes and checks `master/` and `inputs/` |
| `generator/finance.py`, `common.py` | TDS, MSME due dates, payment-run days, numbering in date order; money, dates, the working-day calendar |
| `generator/ids.py` | GSTIN/PAN helpers. Every generated PAN has the digits 0000, which are never issued, so none can belong to a real taxpayer |

## Documents (v1)

```
backend/.venv/Scripts/python -I datagen/generator/documents.py --dataset data/datasets/apex-v1 --seed 42 [--limit N] [--out DIR]
```

Run it with the backend's Python (it needs Pillow, numpy and zxing-cpp). It writes `<dataset>/documents/`, git-ignored like the rest of `data/datasets/`:

| What | Files |
|---|---|
| Supplier invoices, one per invoice of the dataset | `invoices/<invoice no>.pdf`, `.png` or `.jpg`, in six looks: `text_pdf` (clean, text layer), `multipage_pdf` (text layer, rows split over pages, terms page), `scan_pdf` (picture only, slightly crooked, grain), `photo_png` (phone photo: tilt, uneven light, soft), `skewed_jpg` (put in several degrees crooked), `low_contrast_pdf` (faded print on grey). An invoice with an IRN carries the e-invoice QR code and prints its IRN; a credit note or an invoice without an IRN has none |
| Supplier replies to RFQs, one per reply in the dataset | `quotes/<email id>_<rfq>_<supplier>.eml`: prices in the body as text or as a table, or in an attached quotation PDF, or a refusal with the dataset's reason (the demo companies' suppliers always quote, so they have no refusals). Some are messy on purpose (`quirks`): `partial` (not every line quoted), `unit_differs` (a price per box of 10), `incl_gst` (prices include GST), `relative_validity` ("valid for 15 days") |
| The answer key | `manifest.json`: for each file its look, whether it has a text layer and a QR code, and `expected`, the values a reader should find |
| The hold-out slice | Each document has `split`: `hold_out` for two layouts of each kind (invoices: skewed photo, low-contrast scan; replies: table in the body, refusal) and for every document of the suppliers in `splits.json`, else `dev`. Tune on `dev`; report on `hold_out`. E31 prints both |

The key is copied from the dataset's CSV rows, never read back from the rendered document, so it can score a reader independently. A value a careful reader must not guess is `null` in the key (the unit price of a price per box, the end date of a relative validity) with the printed figure beside it. The look and the quirk of each file are chosen by the generator from the seed (interleaved, so a small `--limit` still shows several); the dataset's `email_message.attachments` column is not followed. The e-invoice signature is a placeholder, as in the platform's own tests.

Safe by construction: every document is checked before it is written (a PAN or GSTIN without `0000`, a phone not `+91 00000 ...`, an email outside `.example` stops the run). Same dataset and seed give the same files on the same machine; the pictures use the installed Arial or DejaVu font, so their bytes can differ across machines. `generator/pagekit.py` has the PDF writer, the page drawing and the damage (scan, photo, skew, fade).

Eval E31 reads them back with the platform's code readers (QR, text layer, arithmetic) and counts what still needs a model or a person.

## Known simplifications

- The text of emails is not generated: `email_message` rows hold the metadata of the RFQ emails only; `documents.py` writes the replies.
- `match_result` is not generated: it is the platform's own output. `truth/truth_match_expected.csv` says what a correct match must conclude for every invoice.
- No advance payments and no supplier refusals yet; services are received by service entries, which the platform does not have yet (the evals leave service orders out of the invoice match).
- Tax values are for testing and are marked for confirmation by a tax adviser.
- Tables that only the running platform produces (tasks, notifications, audit, agent runs, proposals) are not generated.
