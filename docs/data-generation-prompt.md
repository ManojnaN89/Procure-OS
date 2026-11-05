# Prompt: design and generate the synthetic data for the P2P platform

## Your role

You are a senior data engineer who also knows Indian procurement, GST and accounts-payable practice well. You are working in this repository (`C:\CBC\PR to PO`) with Claude Code. Your job is to design and build a synthetic dataset, and the generator that produces it, for a procure-to-pay (P2P) platform. The dataset must be realistic enough that a buyer, an accounts-payable clerk or a finance controller in an Indian mid-size company would find nothing odd in it.

Work step by step. Ask me before any decision that is not covered here. Show me samples before generating at full size.

## The product

- A multi-tenant SaaS platform that automates procure-to-pay end to end: purchase request → approval → sourcing (contract price or RFQ) → quote comparison and award → purchase order → supplier confirmation → goods or service receipt → invoice (including e-invoice QR) → three-way match → payment, plus planning (automatic reorder from stock, supply, usage and lead time) and analytics.
- It is built by CBC, a consulting company, and sold to client companies in any industry. Nothing may be specific to CBC or to one industry: categories, locations, approval chains and suppliers are client data, never code.
- India first: INR only, domestic suppliers only, GST, TDS, the MSME payment rule and e-invoicing apply.
- Building blocks: services (rules and formulas, no AI) do almost all of the work; single AI steps read messy input (quotes, invoices, supplier emails, free-text requests, certificates); four agents handle open-ended work (Intake, Supplier communication, Invoice exceptions, Assistant). An AI never produces a number that a decision uses.
- Stack: Python 3.12, FastAPI, Pydantic, SQLAlchemy and PostgreSQL (one database per client), React frontend. Documents are English only.

## Why the data matters

No real data exists. Every screen, rule, formula, agent, AI step, demo and test will be built and checked against this dataset until the first client pilot. The data must therefore:

1. exercise every rule branch at least once (same-state and inter-state GST, MSME with and without an agreement, composition and unregistered suppliers, part deliveries, credit notes, duplicates, and so on);
2. contain a hidden truth (what really happened) that the platform never sees, so tests can grade whether the platform discovers it;
3. be internally consistent: totals tie out, dates are in order, stock balances add up, every reference points to a real record;
4. be reproducible: the same seed and generator version always give the same output.

## What to build

### 1. Content banks (authored by you, saved as files in `datagen/catalog/`)

Write these yourself as CSV, YAML or JSON. They are the realism layer. The generator only combines them with a seed, and never calls an AI at run time.

- **Company profile:** one fictional generic mid-size Indian company (turnover about ₹300–800 crore). Head office in one state, a plant in a second state and a warehouse in a third, with a GST registration in each state.
- **Organisation:** about 8 departments and 10–12 cost centres; a chart of about 40 GL accounts (expense, capital and inventory) with Indian naming conventions.
- **People:** about 20 users with Indian names, realistic designations and reporting lines, covering every role (requester, approver, buyer, store, accounts payable, finance, admin), including two delegations during leave periods.
- **Categories:** about 15–18 categories covering goods and services, stocked and non-stocked, operating and capital spend. Suggested: office supplies, IT hardware, software subscriptions, MRO spares, tools, electrical, packaging, raw materials (generic), chemicals and consumables, safety and PPE, furniture, facility services, AMC and repairs, logistics and courier, professional services, travel and events. Each has a default GL account, an HSN/SAC range, stocked yes/no, capital yes/no and a minimum number of quotes.
- **Items:** about 300 realistic items. Each has a name and a description written the way Indian buyers write them (sizes, grades, pack sizes, brand where normal, for example "A4 copier paper 75 GSM, 500 sheets/ream"), a base unit and purchase unit with conversion (box of 10, roll, kg, litre, set), MOQ, pack size, a correct HSN/SAC code, the GST rate in force from 22 Sep 2025, a realistic price in rupees, and default lead days.
- **Suppliers:** about 60 fictional suppliers across at least 6 states. The mix:
  - legal forms (Pvt Ltd, LLP, proprietorship, partnership);
  - GST registration types: about 85% regular, 8% composition, 7% unregistered;
  - MSME categories: about 40% micro or small, 10% medium, the rest none, with and without a written payment agreement;
  - turnover bands, so some suppliers must issue e-invoices and some must not;
  - categories supplied, payment terms (Net 30/45/60, some with early-payment discount), bank details (real bank IFSC prefix, fictional branch and account), and contacts with Indian names and email domains.
  - Give each supplier a hidden behaviour profile: lead-time mean and spread, on-time rate, part-delivery rate, price drift per quarter, invoice error tendency, email reply speed and writing style.
- **Contracts and price records:** about 30% of items on contract or price record, with validity dates, quantity breaks and some expiring during the history.
- **Approval matrix (default, client-editable):** up to ₹10,000 auto-approved; up to ₹1 lakh by the manager; up to ₹5 lakh by the department head; above that by a director; capital spend always adds finance.
- **Tolerances:** price ±2% or ₹500, quantity 0%, receipt over-delivery 5%, small-difference ₹100, supplier date change up to 3 days.
- **Text banks:** varied, realistic phrasing for messy requests (chat and email), supplier emails (acknowledgement, date change, quantity change, rejection, question, quote cover note) and assistant questions. Use Indian business English as it really appears ("PFA", "kindly do the needful", "revert", "Qty", "Nos", "asap", "pls"), typos and abbreviations, mixed date formats and partial information. Every template must have slots so the generator can fill in true values and keep the answer key exact.
- **Document layouts:** at least 8 layouts each for quotes and invoices (HTML templates for PDF, plus Excel and Word variants). Vary header placement, table styles, tax summary styles, "Amount in words", round-off lines, freight and packing lines, bank details blocks, terms and conditions, logos (generic shapes only) and stamp or signature areas. Mark 2 layouts per document type as hold-out.

### 2. Master data and history (generated by `datagen/generator/`)

Simulate the company day by day over 24 months (two financial years, for example April 2024 to March 2026). Do not generate random rows. Let consumption drive stock, stock and ad-hoc needs drive requests, and requests drive approvals, sourcing, POs, deliveries, invoices and payments. Each step follows the rules and the hidden supplier behaviour.

Output every table in the data dictionary (section "Data dictionary" below) as CSV.

### 3. Documents (generated from the history)

Render real files from the simulated records:

- quotes (PDF, scanned PDF, Excel, Word, email body);
- invoices and credit notes (PDF with an e-invoice QR where the supplier must issue one, PDF without, scans);
- supplier emails (`.eml`, with attachments);
- free-text requests;
- invoice-mismatch cases (invoice, PO, receipt and emails together);
- assistant questions with expected answers;
- certificates (GST, PAN, Udyam, ISO, insurance), for a later phase;
- a few attack documents containing hidden instructions, which must cause no action.

Every document has an answer key line holding the true values. Scans get noise with Augraphy.

For QR codes, follow the published e-invoice JSON schema and IRN rules. Sign them with our own test key, since the government key cannot be used, and record that in the manifest.

## Real-world rules the data must follow

**GST and invoicing**
- A GSTIN has 15 characters: state code (2) + PAN (10) + entity number (1) + "Z" + check digit (computed with the official mod-36 algorithm).
- A PAN's 4th character shows the holder type (C company, P person, F firm, and so on).
- CGST + SGST apply when the supplier's state equals the place of supply; IGST applies otherwise.
- Composition and unregistered suppliers charge no GST.
- Some services fall under reverse charge.
- Invoice numbers are at most 16 characters and unique per supplier per financial year, and series restart each April.
- HSN is shown at 6 digits for suppliers above ₹5 crore turnover and 4 digits below.
- Invoices show taxable value, the tax split, round-off and total in figures and words (lakh and crore format).
- Goods consignments above ₹50,000 show an e-way bill number.
- Some invoices are dated before and some after the 22 Sep 2025 rate change, and must use the correct rate for their date.

**TDS**
- Deducted at the earlier of booking and payment, on the value excluding GST when GST is shown separately.
- Goods purchases: 0.1% above ₹50 lakh per supplier per year (buyer turnover above ₹10 crore).
- Contractors and professional services use their own sections and rates from the versioned TDS table.
- Mark every TDS value as "to be confirmed by a tax adviser".

**MSME**
- Micro and small suppliers are paid within the agreed days, up to 45, or within 15 days of acceptance if there is no written agreement.
- Acceptance is deemed 15 days after delivery if no objection is raised.
- Include some invoices paid late, for the MSME interest and reporting tests.

**Seasonality and calendar**
- Financial year April to March, with a spending rush and budget pressure in February and March.
- Supplier price increases often take effect in April.
- Slowdowns around Diwali and year-end holidays; monsoon logistics delays June to September.
- Many companies work some Saturdays: make it a company setting.
- Use the holiday file in `data/reference/`.

**Demand patterns per item:** steady, seasonal, trending, intermittent and lumpy, plus stock-outs. Record the true pattern in the hidden truth.

**Prices:** realistic market ranges per category. Quotes vary around a market price; an occasional quote is abnormally low or high.

## Planted problems (with hidden truth), at these approximate rates

| Problem | Rate |
|---|---|
| Duplicate requests | 2% |
| Split orders below an approval limit | 1% |
| Part deliveries | 10% |
| Late deliveries | 15% |
| Rejected quantity on receipt | 3% |
| Invoice price mismatch | 6% |
| Invoice quantity mismatch | 4% |
| Duplicate invoices (including the same number in a new financial year, which is NOT a duplicate) | 2% |
| Wrong GSTIN, or GST charged by a composition supplier | 1% |
| Credit notes | 3% of invoices |
| Supplier documents expiring | ongoing |
| MSME invoices near or past the deadline | ongoing |

Each planted case gets a `truth_case` row with its type, the documents involved, the true cause and the result the platform should produce.

## Data dictionary

Follow `docs/data-dictionary.md` if it exists. Otherwise use the tables agreed in chat:

- **Reference:** gst_state, uqc_unit, hsn_sac, gst_rate, holiday, tds_section.
- **Organisation:** company, gst_registration, location, department, cost_centre, gl_account.
- **People and approvals:** user, user_role, delegation, approval_rule, budget, setting.
- **Catalogue and suppliers:** category, item, supplier, supplier_category, supplier_document, payment_terms, contract, price_record, tolerance, stock_opening.
- **Requests and sourcing:** request, request_line, approval_event, rfq, rfq_line, rfq_supplier, quote, quote_line, award.
- **Orders and receipts:** po, po_line, po_event, goods_receipt, gr_line, stock_movement.
- **Invoices and payments:** invoice, invoice_line, match_result, tds_entry, payment_run, payment, bank_statement_line, email_message.
- **Hidden truth:** truth_item_demand, truth_supplier, truth_case.

Conventions:
- readable codes as keys (`ITM-00042`, `SUP-0017`, `PO-MH01-2026-00123`);
- money as text with 2 decimals, never floating point;
- quantities with 3 decimals;
- dates `YYYY-MM-DD`, timestamps ISO with +05:30;
- financial year written `2026-27`;
- fixed status lists per table.

## Output layout

```
datagen/
  config/      company_general.yaml, sizes.yaml (small, demo, large)
  catalog/     the content banks above
  generator/   master.py, simulate.py, documents.py, validate.py, cli.py
data/datasets/<company>-<size>-v<version>-seed<seed>/
  manifest.json   seed, generator version, counts, date range, test-key note
  master/*.csv
  history/*.csv
  documents/quotes|invoices|credit_notes|emails|requests|certificates|attacks/
  answer_keys/*.jsonl
  truth/*.csv
  splits.json     build vs hold-out (2 layouts per document type, 10 suppliers)
  report.md       quality-check results and distribution summary
```

Sizes:
- **small:** 30 items, 3 months, for unit tests.
- **demo:** the full company, 24 months.
- **large:** 20 companies, for performance tests.

## Quality checks (must all pass, reported in report.md)

- Every reference resolves; there are no orphans.
- Line totals, tax and invoice totals add up to the paisa.
- Dates follow the process order; no document is dated on a holiday unless it is planted.
- All GSTIN check digits are valid; PAN matches GSTIN; pincode matches state.
- Stock never goes negative unless a stock-out is planted.
- Budgets, commitments and actuals reconcile.
- Every planted problem appears at roughly its target rate and has a truth row.
- Every document has an answer key, and every answer-key value appears in its document.
- The same seed gives byte-identical CSV output.
- The report shows distributions (spend by category and month, lead times, supplier mix by state, MSME and registration type) so a person can eyeball realism.

## Constraints

- **Everything fictional.** No real company, person, GSTIN, PAN, bank account or phone number. Real brand names may appear only in item descriptions, the way buyers write them. Generated GSTINs and PANs must be valid in format but must not belong to real taxpayers: use clearly fictional name patterns, and keep a list of generated identifiers in the manifest.
- **No AI calls at run time.** You author content banks now; the generator is plain Python with a seeded random generator (numpy and Faker en_IN are allowed).
- **Same models as the backend.** Pydantic models define every table; the generator imports them so data always matches the database.
- **Free and open-source libraries only.** Check each licence before adding it.

## How to proceed

1. Read this prompt, `data/reference/README.md` and the data dictionary. List your assumptions and questions, and wait for my answers.
2. Propose the company profile, category list, GL chart, approval matrix, supplier mix and planted-problem rates as short tables. Wait for approval.
3. Author the content banks. Show me 20 items, 5 suppliers, 10 messy requests, 5 supplier emails and one quote and one invoice layout as samples.
4. Build the master-data generator and the quality checks; generate the small size.
5. Build the simulation and hidden truth; generate the small size, then the demo size; share report.md.
6. Build the document renderer and answer keys; share 3 sample documents of each type.
7. Build the loader stub, the manifest and the hold-out split.

After each step, tell me what was done, what was checked and what is still open, in plain English.
