# Block 10 spec: invoice (flow step 10)

*Cross-block contracts with blocks 9-13 (built 9, 10, 11, 12, 13 in order): `handoff/specs/blocks9-13-interfaces.md`.*

Tasks **9a.1** (D-14: invoices without a purchase order for configured categories, with department authorisation,
evidence, approval by amount and duplicate checks) and **3.3, duplicate key only** (a unique duplicate key, so two
entries of one invoice can never both be live). Also the intake defects and decisions found in the plan: invoice mail
and its sender, the received date, the AI reading's limits (D54), GSTIN checks, credit and debit notes as they come in.
Spec only; nothing here is built yet.

**Not in this block:** the three-way match of order lines, both tolerances (D-17, DF-12), GST rate and tax per line
against the order (3.4), match locks and release re-checks (rest of 3.3, DF-11), quantity re-match (3.5), cases (WP-5):
block 11. Goods receipt, services, quarantine, returns and recovery claims (3.6, 3.8, 4.2, 4.3, 4.5): block 9 (built
before this one); the debit note raised from a claim: block 12. Approving, netting and paying credit and debit notes, unlinked credits, recovery claims (2.4, DF-21), TDS
on services and on invoices without an order (2.5, DF-22), payment states, runs and the payer check (2.1-2.8): block 12.
GSTR-2B (9b.1), PPV (13.2, 13.3), closure (6.6), accounting export (2.6): block 13. The QR signature against the portal's
public key (V04, owner to supply the key); e-invoice expected but missing by turnover and the 30-day IRN rule (FR45,
P2-07); IRN status at the portal (P2-15): Phase 2. Email approval links for invoices; reverse charge on services.

**Sources.** `planning/P2P-Project-Plan-End-to-End.md` (**Plan**): worked example l.137 ("1 duplicate stopped"), roles
l.146-171 (AP confirms invoices; releases by another person), steps 10-11 gap table l.384-386 ("Bills without a PO: Not
built, D-14"; "Duplicate check: Built; Race between two entries"), WP-9a l.453, tasks l.501 (3.3), l.536 (9a.1), l.494
(2.4), l.502 (3.4), l.570 (11b.7), block order l.597-611 (step 10 = 9a.1, 3.3 duplicate key), "Every package ends when"
l.614, AT-07 l.815, defects l.855 (DF-11), l.867 (DF-21), l.868 (DF-22), D-07 l.1071, D-14 l.1078, D-15 l.1079.
`planning/P2P-Plan-Agentic-Revision.md` (**Rev**) l.121 (invoice intake is an event), l.173 ("Non-PO invoices keep
their own route"), AG03 rows l.429 and l.441 (D54: "Read into drafts ... AP confirms"), l.476, D-14 l.552.
`planning/P2P-Phase1-Pilot-Baseline.md` (**Base**) H-1 l.136 ("invoice_line.po_no is NOT NULL"), H-2 l.137, WP-9a
l.501-509 ("Changes: invoice_line without PO for configured categories; department approval. Agentic part: Invoice
reading drafts. Tests: No PO invoice needs department authorisation and evidence"). `P2P-Platform-Project-Plan-v10.xlsx`
Plan P1-16 and Services & Rules FR33 (duplicate rule), FR32 (segregation), AI Steps S02. `planning/P2P-Screens-Brief.md`
l.45, l.86, l.213-221. `planning/Decisions-22-Nov.md` #14. **Code read:** worktree `p3-b8` at 8ac2221 (blocks 0 to 8;
tenant migrations to 0150, evals to E143).

*RECOMMENDED* marks a default this spec proposes because the documents are silent. **NEEDS OWNER** marks a point that
would change an approved decision or a built behaviour the owner has seen; the most conservative behaviour is the interim.

---

## 1. What the decisions say

| Source | Text |
|---|---|
| D-14 | "Invoices without a PO for configured categories (required for model A)" |
| Base WP-9a, 9a.1 | "Configured categories with department authorisation, evidence, approval by amount and duplicate checks"; "invoice_line without PO for configured categories; department approval"; "No PO invoice needs department authorisation and evidence" |
| Plan 3.3 (half) | "unique duplicate key"; gap table: "Duplicate check: Built. Race between two entries" |
| FR33 | "Same vendor + normalised invoice number (strip spaces, prefixes, leading zeros) within the same financial year OR same vendor + amount + date within 7 days. 'INV-001' and 'inv 1' flagged; 'INV-001' in April does not match last year's" |
| P1-16 | "Invoice by email or upload (manual entry works without AI). Credit notes ... linked to the original invoice or the vendor. E-invoice: ... recompute the IRN hash ... check the signature against the portal's published public key (confirm availability at setup) ... Checks: vendor GSTIN matches master, duplicate invoice (FR33)" |
| Rev AG03 / D54 | Invoice and AP, service role: "Read into drafts; investigate; draft queries". Confirmation: "AP confirms; approver releases". Never: "Release; accept variances; touch payments" |
| FR32, rule 7 | Requester, approver, receiver, invoice approver and payer differ for the same case |
| D53 (block 6, 8) | Mail identifies a supplier only from a registered contact; our server's sender authentication is recorded and a `fail` is never read |
| Screens Brief | "Payables: Invoices, Invoices without PO (new)"; "Drafts look different from facts"; "Duplicate stopped: show the earlier invoice" |
| Decision 14 | "The AI capability of a block is built with that block" |
| Owner, settled | Mail that fails is queued and retried; a task goes out after 24 h. Recommended defaults are applied, not re-asked |

Silent on: which categories, their limits and evidence (OP-1, OP-2), who "the department" is (OP-3), what an invoice
without an order costs the budget and when (OP-7), FR33's amount-and-date clause for invoices with an order (OP-9), the
date an emailed invoice counts as received (OP-12), and whether a clean reading may still be entered with no AP
(OP-13, NEEDS OWNER).

---

## 2. What exists today

| Part | Where | Behaviour |
|---|---|---|
| Entering | `invoices/service.py` `enter_invoice`, `InvoiceIn`, `InvLine` | Supplier must exist; every line names an order line of that supplier (`_po_line`); tax worked out when not given (`formulas/orders.line_amounts`), total rounded to the rupee; status `received`, then `match_invoice`. Credit and debit notes: no line needed, `ref_invoice_no` must be an invoice of the same supplier, then `approved` at once with no check (DF-21) |
| Duplicate key | `enter_invoice` l.164-172, `formulas/matching.normalize_invoice_no` | Key = number upper-cased, every non-alphanumeric removed (`PIS/2425/0036` = `pis 2425 0036` = `PIS24250036`); a twin = same supplier, financial year of the invoice date, type, key, not cancelled. The copy is inserted `received`, then set `cancelled`, `payment_block = 'duplicate'`, `duplicate_of`. **Select then insert, no lock, no unique index** (index `invoice_dedupe` is not unique): two entries at once both live. Leading zeros kept (`INV-001` is not `INV-1`); the same IRN under another number is not caught |
| Header checks | `formulas/matching.header_problem`, called in `match_invoice` | Composition or unregistered supplier charging GST (`tax_not_allowed`); e-invoice supplier without IRN (`irn_missing`); buyer GSTIN not the order's; supplier GSTIN not one on file (`gstin_mismatch`). Block kind `tax`. No GSTIN format check on typed entry; whether IGST or CGST+SGST fits the two states is not checked when the invoice gives its tax |
| Typed entry | `platform/invoice_api.py` `POST /api/invoices` (`invoice.book`) | Lines need `po_no`, `po_line_no`; 422 otherwise |
| Reading | `invoices/reading.py` `read_document`, `draft_from_qr`, `draft_from_reading`, `_finish`, `confirm_draft`, `dismiss_draft` | Same file twice = one draft (`document_hash`). Supplier by sender (`supplier_by_sender`: **any** `supplier_contact` address, the sender verdict ignored) else by GSTIN on the page. QR with a recomputing IRN and rows that add up: no model. Else AI step S02. Lines mapped to the supplier's open order lines only. Doc types `INV`, `CRN` (a `DBN` QR falls to the model, whose schema has no debit note) |
| Auto entry | `reading._maybe_apply`, gate `invoice.read` | A clean draft (no problem, no assumption, sender path) is entered by the platform when the gate is `auto`; demo companies leave it at `propose`; E27 switches it on |
| Mail | `platform/emails.ingest`, `_route_email` -> job `read_invoice_email` | Mail to `invoice_receipt_mailbox` read attachment by attachment; `email_message.auth` (block 6 verdict) stored but not used here. `received_at` = the mail's own Date header; confirming passes `as_of = business_today()`, so `received_on` is the **day AP confirms**, not the day it arrived |
| AI step | `ai_steps/invoice_reading.py` (S02, prompt v1) | Schema, evidence check on every value, `document_type` invoice / credit_note / other, confidence 0.6 floor; capability `invoice_ap` (`runner.py`); masking of bank and PAN (`ai_steps/masking.py`) |
| Drafts API | `platform/invoice_intake_api.py` | List and detail need only a tenant login (no permission); upload and confirm `invoice.book` |
| Invoice API | `invoice_api.py` `GET /api/invoices`, `/{no}` | Tenant login only, no permission |
| Without an order | - | None: `invoice_line.po_no`, `po_line_no` NOT NULL (0027); no rule, no authorisation, no evidence |
| Due date, tax | `invoices/payment.book_invoice`, `accepted_on`, `_goods_only` | Baseline invoice date or acceptance (latest receipt or service entry); 194Q when no line is a service item: a line with no order would count as goods |
| Budget | `approvals/budget_ledger.post_payment`, view `budget_use` | Non-stock lines spent when paid, found through their order line: a line with no order line posts nothing |
| Tests, evals | `tests/test_invoices.py` (asserts `PIS24250036`), `test_invoice_reading.py`, `test_invoice_screens.py`, `test_mailbox_settings.py`; E19, E27 (sets `invoice.read` auto), E30, E31, E32, E44, E45, E55, E81 | Pass on p3-b8 |

---

## 3. Pre-implementation check (Plan 9.3)

### 3.1 Data

| Need | Apex Pumps | Brightwave | Enough? |
|---|---|---|---|
| Categories allowed without an order | RAW, PKG, MRO (stocked), SRV (PO-bought) | ITH, OFS (stocked), SWS, FAC (PO-bought) | **Gap** |
| Suppliers billing without an order | none | none | **Gap** |
| A cost centre owner who is not AP, an approver or the run approver | CC-PRD, CC-MNT owned by U01 | CC-ITO owned by U08 | **Gap** at Apex (no admin cost centre) |
| Approval rows `non_po_invoice` | none | none | **Gap** |
| Budget on the bills' GL accounts | none for 5410, 5460 | none for 5460 | **Gap** |
| e-invoice supplier, interstate supplier | SUP-003, SUP-004 e-invoice; SUP-002 Gujarat (IGST) | - | Yes |
| Invoice documents | `tests/invoice_documents.py`, `datagen/generator/documents.py` (PO invoices, QR and plain) | same | **Gap:** no bill without an order, no debit note |
| History | 479 invoices (Apex), 272 (Brightwave), all with orders | | Kept as is: no bills without an order in history (their replays in E19, E45, E55 stay unchanged) |

**Generator (data first):**
1. `categories` (Apex): `{code: UTL, name: Utilities and telecom, kind: service, gl: 5460, stocked: false}`,
   `{code: PRF, name: Professional fees, kind: service, gl: 5410, stocked: false, tds: 194J(b)}`. Brightwave:
   `{code: TEL, name: Internet and telephone, kind: service, gl: 5460, stocked: false}`.
2. Suppliers. Apex `SUP-010 Sahyadri Netlink Pvt Ltd` (pvt_ltd, PAN `SAHCS0000N`, state 27, Pune, `e_invoice: true`,
   msme none, NET30, categories [UTL], contact `billing@sahyadrinetlink.example`); `SUP-011 Mehendale and Gokhale,
   Chartered Accountants` (partnership, PAN `MEHFM0000G`, state 27, Pune, `e_invoice: false`, msme micro, written
   agreement 30 days, NET45, categories [PRF], contact `accounts@mehendalegokhale.example`). Brightwave `SUP-007 Nandi
   Fibernet Pvt Ltd` (PAN `NANCN0000F`, state 29, Bengaluru, `e_invoice: false`, NET30, categories [TEL], contact
   `billing@nandifibernet.example`). All active, bank accounts verified like the others. Names are made up.
3. Apex cost centre `CC-ADM, Plant administration, MGT, PLT-CKN, owner U07`; budget per month CC-ADM `{"5460":
   "20000.00", "5410": "40000.00"}` (location row unchanged). Brightwave CC-ITO adds `"5460": "30000.00"` (total 930000).
4. `approver_roles` both: `[finance_manager, designation, Finance Manager]`. `approval_rules` both:
   `NPO-01,non_po_invoice,0.00,50000.00,ANY,...,auto`, `NPO-02,...,50000.01,500000.00,...,finance_manager`, `NPO-03,
   ...,500000.01,999999999.00,...,director` (Brightwave `cfo`), version 1 from 2025-04-01.
5. New master file `non_po_rule.csv` (both): `category_code,version,effective_from,enabled,max_value,supporting_doc,
   note`. Apex `UTL,1,2025-04-01,true,50000.00,,Telephone and internet bills` and `PRF,1,2025-04-01,true,200000.00,
   Engagement letter,Audit and tax fees`; Brightwave `TEL,1,2025-04-01,true,100000.00,,Office internet`.
6. `setting.csv` (both): `non_po_duplicate_window_days,7,A bill without an order with the same supplier, category and
   total within this many days of another is asked about`.
7. Documents: `tests/invoice_documents.py` and `documents.py` print a bill without an order (no order number, a SAC on
   the line) with and without the QR code, and a debit note (QR `DBN` and plain, "Against invoice <no>").
8. `truth/truth_invoice_intake.csv` (`scenario,figure,value`) by the generator's own arithmetic for N1, N2, N6, N7, B1,
   B2 and every due date of section 9.

### 3.2 Configuration

New versioned rule table `non_po_rule` (section 6). New approval document `non_po_invoice` in `approval_rule` (rows
above; the admin approval-rules screen lists it as "Invoice without an order", prefix `NPO`). New setting
`non_po_duplicate_window_days`. Existing: `invoice_receipt_mailbox`, `mail_authserv_id`, `budget_policy`,
`budget_warn_pct`, `budget_block_pct`. Autonomy gate `invoice.read` becomes locked (OP-13).

### 3.3 Today's behaviour

E19, E27, E30, E31, E32, E44, E45, E55, E81, E33, E34, E46 and the tests of section 2 pass on the branch with block 9
before anything changes. Section 10.1 lists those this block updates.

### 3.4 Gaps

D-14 (10.1-10.5), the duplicate key and its race (10.6), header checks (10.7), mail sender and received date (10.8),
drafts and the AI step (10.9-10.10), auto entry against D54 (10.10.3), who may see invoices and drafts (10.11).

---

## 4. Neighbours

Blocks are built in order 9, 10, 11, 12, 13. Block 9 (migration 0160) is built; block 10 calls nothing of blocks
11-13, which extend what it delivers here.

| # | Block 10 delivers | Used by |
|---|---|---|
| N-1 | `invoice.kind` (`po`, `non_po`); `invoice_line.po_no` nullable with the line's own `category_code`, `cost_centre_code`, `gl_code`, `location_code` | 11: the line match runs only on `kind = 'po'`; 12: TDS by category (2.5), payment of approved bills; 13: GSTR-2B and accounting export read both kinds |
| N-2 | `header_problem` (owned here) with `tax_head` and the bill's buyer GSTIN: the one check of IGST against CGST plus SGST from the two GSTIN states | 11 keeps the line verdicts, arithmetic, locks and release; it calls `header_problem` unchanged; its GST per line (3.4) compares rate and amounts only and leaves the state test to `tax_head` |
| N-3 | A bill without an order that fails a header or arithmetic check is blocked and is **never released by proposal** (OP-6): block 10 adds the refusal (409) to today's `invoices/service.request_release`; `rematch_invoice` re-runs its checks | 11 moves `request_release` to `invoices/release.py` and keeps this refusal |
| N-4 | `payment.accepted_on` returns `authorised_on` for a bill; `_goods_only` is false when any line has no order line | 12 owns `payment.py`; these two lines are block 10's |
| N-5 | `budget_ledger.post_payment` posts a bill's lines from the line's own cost centre and GL | 12 calls it as today |
| N-6 | Credit and debit notes are recorded with the header checks of 10.7; a clean one stays `approved` at entry as today (interim) | 12 (2.4) makes a clean note wait `received` for a second person through this block's `POST /api/invoices/{no}/approve` (one endpoint, a note branch added by 12); nothing here nets or pays them |
| N-8 | `invoice.approved_by` (new column, the **invoice approver** of rule 7): the person whose act made the invoice `approved`. A bill: the last step's approver, or the authoriser on an `auto` chain. An invoice with an order matched clean: who entered it or confirmed its draft | 11 sets it to the release proposal's approver when a block is released; 12 checks the payer against it (2.7) |
| N-7 | The unique key (10.6) | 11 matches only live invoices; 13's statements use the same `normalize_invoice_no` |

**Files shared:** `invoices/service.py` (11, 12), `formulas/matching.py` (11), `invoices/payment.py` (12),
`approvals/budget_ledger.py` (3, 12), `platform/invoice_api.py` (11), `platform/exceptions.py` (9, 11). Block 10 adds
functions and the branches named here; it changes no line verdict, tolerance or payment rule.

---

## 5. Requirements

### 10.1 Invoices without an order: what may be entered (D-14)

1. A bill without an order (`kind = 'non_po'`) is allowed only for a category with an **enabled** `non_po_rule` version
   in force on the bill's received date. Else 409 "`<category name>` cannot be billed without a purchase order".
2. Refused at entry (409), nothing written:
   - total above the rule's `max_value`: "a `<category>` bill without an order may be at most Rs `<max>`; this one is
     Rs `<total>`: raise a purchase order" (the recorded total, rounded to the rupee; equal is allowed);
   - supplier not `active`: "`<supplier>` is `<status>`: only an active supplier's bill can be entered without an order";
   - supplier not approved for the category (`supplier_category`): "`<supplier>` is not approved for `<category>`";
   - cost centre inactive or unknown; GL account not of type `expense`, or capex, or a tax account: "GL `<code>` cannot
     take a bill without an order".
3. Refused 422: a line naming an order line (a bill is all one kind); a GST rate that is not a slab (0, 0.25, 3, 5,
   12, 18, 28); no bill copy; a supporting document missing when the rule names one ("a `<category>` bill needs the
   `<supporting_doc>` attached").
4. Lines: description, quantity, unit price, GST rate, SAC (optional). Each line takes the bill's category, cost centre,
   location (the cost centre's) and GL (the category's default unless AP chose another allowed one). Tax as today.
5. **Evidence** (OP-2): the bill copy (a document, `doc_type` `invoice_document` or `invoice_evidence`) always; plus one
   supporting document when `supporting_doc` is set. Typed entry gives document ids (uploaded first through `POST
   /api/files?doc_type=invoice_evidence`); a draft's own document is the copy. Rows in `invoice_document_link`.
6. A rule is never applied to stocked categories: a version for a category with `stocked = true` is refused when
   proposed (422).

### 10.2 Checks and who authorises

1. After the duplicate checks (10.6), the header checks (10.7) and the arithmetic check (as today) run. A failure blocks
   the bill (`blocked`, kind `tax` or `match`, as today). AP fixes the master or the bill and runs **Match again**
   (rematch), or cancels it; never released by proposal (OP-6, N-3).
2. A clean bill becomes `awaiting_authorisation`. The **authoriser** (OP-3) is the cost centre's owner, else the head of
   its department, else that person's manager; the first of these who is not the person who entered it. A deputy in
   force (`approvals/rules.deputy_on`) may act. Stored in `invoice.authoriser`.
3. Task `invoice_authorise` to the authoriser: title "Authorise `<supplier>`'s bill `<number>` for Rs `<total>`
   (`<category>`, `<cost centre>`)", SLA 18 working hours on the cost centre location's calendar, escalation chain the
   authoriser's manager. Email (10.12). Audit `invoice.to_authorise`.

### 10.3 Authorisation and approval by amount

1. **Authorise** (`POST /api/invoices/{no}/authorise`, note optional): only the authoriser or their deputy, never the
   enterer (403 "segregation of duties: ..."). It records `authorised_by`, `authorised_at`, `authorised_on`; the task
   completes. Meaning: the service or supply was received and belongs to this cost centre.
2. The approval chain: `approvals/rules.find_rule(db, 'non_po_invoice', total, authorised_on, category, department of
   the cost centre, location of the cost centre)`, then `resolve_chain(..., exclude = {enterer, named authoriser, who
   authorised})`. Rule code
   and version stored on the bill. An `auto` chain (no roles): `approved` at once, `approved_by` = who authorised (N-8). No rule fits: 409 with
   `ApprovalRuleError`'s words; the bill stays `awaiting_authorisation`.
3. Otherwise `awaiting_approval`, one `invoice_approval_step` per person in order; the first `pending` with task
   `invoice_approve` (same SLA and escalation), the rest `waiting`.
4. **Approve** (`POST /api/invoices/{no}/approve`): only the open step's approver or deputy, who must hold
   `invoice.approve` (403 otherwise); never the enterer or authoriser (excluded, and refused 403 if they reach it through
   a deputy). The last step makes the bill `approved` (`approved_by` = that approver, N-8), then `payment.book_invoice` (due date with baseline acceptance =
   `authorised_on`, N-4; MSME deadline from it). Audit `invoice.approve_step` per step, `invoice.approve` at the end.
5. **Reject** (`POST /api/invoices/{no}/reject`, reason required): the authoriser at authorisation, the open step's
   approver at approval. The bill becomes `cancelled`, `cancel_reason` "Rejected by `<name>`: `<reason>`", open tasks
   cancelled, AP told (10.12). The same number may be entered again (a cancelled bill is no twin, 10.6).
6. AP may cancel a bill `awaiting_authorisation` or `awaiting_approval` with a reason (`invoice.cancel`, as today).

### 10.4 Budget for bills without an order (OP-7)

1. Held: from authorisation until paid, a bill's lines count in `budget_use` part `order` at their taxable value, on
   their cost centre and GL (the view is replaced in 0170). Spent: when paid, `post_payment` posts each line's taxable
   value from the line's own cost centre, GL and location, in the month the bill was received (locked month: the next
   open one, as today) (N-5).
2. The check (`approvals/budget.check_invoice(db, no, today)`, same `check_budget`, same warn and block percentages)
   runs at authorisation and at each approval; the bill checked counts in none of the parts (as a request's own
   check); the result is shown and kept in the audit row.
3. Over a budget whose policy blocks: the **last** decider (the authoriser on an `auto` chain, else the last step's
   approver) must give `over_budget_reason`; without it 409 "over the budget of cost centre `<cc>` for `<gl>` by Rs
   `<x>`: give a reason to approve it". Audit `invoice.approve_over_budget`. A bill is never refused for budget alone
   (the cost is already incurred). Warn policy: shown only.

### 10.5 Drafts of bills without an order

1. A reading whose supplier has **no** open order line and exactly one category with an enabled rule becomes a bill
   draft: `kind non_po`, that category, lines from the rows read (QR rows by arithmetic, or the model's lines), cost
   centre = the one of that supplier's last bill in the category (an assumption shown), else none (a problem: "choose
   the cost centre"). Several such categories: none chosen (problem "choose the category").
2. A supplier with open order lines and a rule: the PO mapping runs first; rows that map to no order line make the
   problem "`<row>` matches no order line; if this is a bill without an order, choose the category" (AP may switch the
   whole draft to `non_po` by edits).
3. `confirm_draft` edits gain `kind`, `category_code`, `cost_centre_code`, `gl_code`, `supporting_document_id` (another
   document already stored, e.g. a second attachment of the same mail), `not_duplicate_reason`, and per line
   `description` and `gst_rate`. Confirming goes through `enter_invoice` (all of 10.1-10.6).

### 10.6 The duplicate key (3.3) and duplicate checks

1. **Key** (FR33, OP-8): upper case, every non-alphanumeric removed, and leading zeros dropped from each run of digits
   (a run of zeros keeps one `0`): `PIS/2425/0036` -> `PIS242536`, `INV-001` = `inv 1` = `INV1`, `INV-000` -> `INV0`.
   One function, `formulas/matching.normalize_invoice_no`, used by entry, statements and cutover as today.
2. **Twin:** same supplier, financial year of the invoice date, type, key, status not `cancelled`. Or (new) the same
   IRN (case-insensitive) on any live document. A twin makes the new one a **cancelled copy**: inserted directly as
   `cancelled`, `payment_block 'duplicate'`, `duplicate_of`, `block_reason` "The same invoice number is already on file
   as `<no>`" or "The same IRN is already on file as `<no>`". Audit `invoice.duplicate` as today.
3. **Race:** the twin check and the insert run under a transaction advisory lock on (supplier, fy, type, key), plus
   partial unique indexes `(supplier_code, fy, type, dedupe_key) where status <> 'cancelled'` and `(upper(irn)) where irn
   is not null and status <> 'cancelled'`. Two entries at once: one live, the other its cancelled copy; if the index is
   reached (an IRN race under two different keys), the second is refused 409 "entered at the same moment as `<no>`:
   look at it before entering again". Never two live.
4. A cancelled original is no twin (the corrected bill can be entered). Another financial year, supplier or type is no
   twin.
5. **Possible duplicate, bills only** (OP-9): a live bill of the same supplier, category and total whose invoice date is
   within `non_po_duplicate_window_days` (7, both directions, both ends included) and whose key differs: 409 "this may
   be the same bill as `<no>` (`<number>`, `<dd Mon yyyy>`, Rs `<total>`): enter it with a reason if it is not";
   with `not_duplicate_reason`: entered, `suspect_of`, audit `invoice.not_duplicate`. Asked only when 10.6.2 found no
   twin. Invoices with an order are not asked (the match bounds them by quantity received).
6. Migration 0170 recomputes every `dedupe_key` with the new rule; if two live rows then share a key or an IRN, it stops
   and lists them (no live company has any; the demo data has none).

### 10.7 Header checks (all kinds and types)

`header_problem` (block kind `tax`), in this order:
1. `tax_not_allowed` and `irn_missing` as today (IRN still only for type `invoice` of an e-invoice supplier).
2. `gstin_mismatch` as today for invoices with an order. For a bill: the buyer GSTIN must be the GSTIN of the cost
   centre's location; the supplier GSTIN one of the supplier's on file.
3. **New** `tax_head`: supplier state (supplier GSTIN's first two digits) and place of supply (buyer GSTIN's) equal ->
   only CGST and SGST may be charged; different -> only IGST. Reason "IGST was charged on a supply within `<state>`
   (`<ss>`): CGST and SGST are due" or "CGST and SGST were charged on a supply from `<state>` (`<ss>`) to `<state>`
   (`<pp>`): IGST is due". Zero tax passes.
4. Typed entry refuses (422) a supplier or buyer GSTIN that fails `formulas/gstin.is_valid_gstin` ("`<value>` is not a
   valid GSTIN"); in a draft it is a problem.
5. Credit and debit notes: checks 1 (composition only), 2, 3 run at entry; a failure makes the note `blocked` (kind
   `tax`) instead of `approved`; a clean note stays `approved` (N-6; until block 12, which makes it wait for a second
   person). A debit note, like a credit note, names its invoice
   when a draft is confirmed (problem "a debit note must say which invoice it is for").
6. The typed IRN is not recomputed (OP-11); the QR path keeps its recomputation.

### 10.8 Invoice mail: sender and received date

1. **Sender** (D53, OP-10): `supplier_by_sender` uses `masters/suppliers.registered_sender` (current contact of a
   supplier). The mail's stored verdict (`email_message.auth`):
   - `fail`: nothing read, no model call; one draft `needs_person`, path `none`, problem "the sender's mail server
     failed its authentication (`<detail>`): the mail was not read"; task `invoice_sender_check` for AP ("Invoice mail
     from `<address>` not read: check it with the supplier"); audit `invoice_draft.create` with `auth_verdict`;
   - `none`: read; the draft carries the assumption "the sender could not be authenticated";
   - `pass`: read as today.
   A sender who is no registered contact: the GSTIN fallback stays (as today), with the assumption "sent from
   `<address>`, not a registered contact of `<supplier>`".
2. **Received date** (OP-12): an emailed document's draft keeps `received_on` = `email_message.received_on` (the day we
   stored the mail) and `received_at` = the mail row's `created_at`; an upload's = the upload's day and time. Confirming
   enters the invoice with that date (`as_of`), whatever day AP confirms. The tolerance version, budget month and MSME
   clock follow from it as today.

### 10.9 The AI step (S02) and the code readers

1. `InvoiceReading.document_type` adds `debit_note`; prompt version 2 says so and that a bill may have no order number.
   Schema, evidence paths, confidence floor and masking unchanged. A model still computes nothing.
2. `draft_from_qr` accepts `DBN` (draft type `debit_note`) and builds bill lines per 10.5.
3. Every value a decision uses (totals, tax, due date, budget, duplicate, approver, chain) comes from code; the model
   only copies what is printed (rule 2).

### 10.10 The invoice capability (AG03 intake) and D54

1. Acts as service `invoice_reading`; reads into drafts; never confirms, authorises, approves or releases.
2. Every draft, including a perfect QR one, waits for AP: the gate `invoice.read` joins `autonomy.LOCKED` (OP-13, NEEDS
   OWNER, interim); `_maybe_apply` is removed; the Automation screen shows it locked (`setup_api.LOCKED_NOTES`: "D54:
   AP confirms every invoice read from a document").
3. **D54 checks** before the capability counts as switched on for bills: (1) `invoice_ap` stays switched on by two
   administrators (AI foundation 11a.5); a `non_po_rule` version or a `non_po_invoice` approval row needs a second
   administrator (D-22); (2) permission tests: drafts and invoices only for 10.11's people, the reading never writes an
   invoice, another company's documents never in a prompt; (3) eval E158 meets its threshold, written before switch-on:
   at least 95% of header and line fields right, 0 silent errors, live result kept in `evals/results/live.json` and
   `live.md` (OP-16); (4) tracing: each model call carries capability `invoice_ap`, `started_by` (`job:<id>` or
   `user:<id>`) and the draft id; (5) switched off, invoice mail becomes an `ai_handover` task for AP and an upload is
   read by the QR code only, else `needs_person` with no model call (as built; E101, E102).

### 10.11 Who may see invoices and drafts

1. `GET /api/invoice-drafts`, `/{id}`: `invoice.view` or `invoice.book` (403 otherwise).
2. `GET /api/invoices`, `/{no}`: `invoice.view`, `invoice.book`, `invoice.match` or `invoice.approve`; or, for one bill,
   its authoriser, a step approver or their deputy (that bill only; 403 for others, 404 for another company's).

### 10.12 Emails (fixed words, through the outbox; a failed mail retried, a task after 24 h)

| To | When | Subject / body |
|---|---|---|
| Authoriser (and deputy) | Bill waits for authorisation | "Bill to authorise: `<supplier>` `<number>`" / "`<supplier>` has billed Rs `<total>` for `<category>` on `<cost centre>` without a purchase order (`<number>` of `<dd Mon yyyy>`). Please confirm it was received and belongs to you, or reject it: `<app link>`" |
| Step approver | Step opens | "Bill to approve: `<supplier>` `<number>`" / "Rs `<total>` for `<category>` on `<cost centre>`, authorised by `<name>` on `<dd Mon yyyy>`. Budget: `<status in words>`. `<app link>`" |
| Enterer (AP) | Rejected | "Bill `<number>` rejected" / "`<name>` rejected `<supplier>`'s bill `<number>`: `<reason>`. It is cancelled; enter it again if the supplier corrects it." |

No email action links (OP-14): decisions are made in the app after login.

---

## 6. Rules and settings (versioned data)

`non_po_rule`: one row per category and version; a new version from a date (today or later) closes the one in force the
day before; changed only through `POST /api/setup/non-po-rules` (`admin.rules`, a reason, a second administrator: D-22,
`setting_changes`, 202). A bill keeps the rule id it was entered under (`invoice.non_po_rule_id`).

| Column | Meaning | Default (demo data) |
|---|---|---|
| `enabled` | Bills allowed for the category | true for UTL, PRF, TEL |
| `max_value` | Highest bill total, rupees | 50,000.00 (UTL), 2,00,000.00 (PRF), 1,00,000.00 (TEL) |
| `supporting_doc` | Name of the one supporting document needed, or empty | "Engagement letter" (PRF) |

| Setting | Default | Use |
|---|---|---|
| `non_po_duplicate_window_days` | 7 | Days around a bill's date for the possible-duplicate question (10.6.5) |
| `approval_rule` document `non_po_invoice` | NPO-01..03 (3.1) | Approval by amount |

SLA of the two task kinds: 18 working hours (constant, as block 8).

---

## 7. Data: migration 0170 `invoices_without_order` (down_revision 0160, block 9)

```sql
create table non_po_rule (id bigserial primary key, category_code text not null references item_category (code),
  version int not null, effective_from date not null, effective_to date, enabled boolean not null default true,
  max_value numeric(14,2) not null check (max_value > 0), supporting_doc text, note text, created_by text,
  created_at timestamptz not null default now(), unique (category_code, version));
alter table invoice add column kind text not null default 'po' check (kind in ('po', 'non_po')),
  add column category_code text references item_category (code), add column cost_centre_code text
  references cost_centre (code), add column non_po_rule_id bigint references non_po_rule (id),
  add column authoriser text, add column authorised_by text, add column authorised_at timestamptz,
  add column authorised_on date, add column approval_rule_code text, add column approval_rule_version int,
  add column suspect_of text references invoice (no), add column not_duplicate_reason text,
  add column over_budget_reason text, add column approved_by text;
-- approved_by backfill (N-8): match_result.resolved_by of a release, else entered_by, for invoices approved or paid
alter table invoice drop constraint invoice_status_check, add constraint invoice_status_check check (status in
  ('received', 'matched', 'blocked', 'awaiting_authorisation', 'awaiting_approval', 'approved', 'part_paid', 'paid',
  'cancelled'));
alter table invoice_line alter column po_no drop not null, alter column po_line_no drop not null,
  add column category_code text, add column cost_centre_code text, add column gl_code text, add column location_code text,
  add constraint invoice_line_order_or_bill check ((po_no is not null and po_line_no is not null) or (po_no is null
  and po_line_no is null and category_code is not null and cost_centre_code is not null and gl_code is not null));
create table invoice_approval_step (id bigserial primary key, invoice_no text not null references invoice (no),
  step int not null, role text not null, approver text not null, delegate text, status text not null
  check (status in ('waiting', 'pending', 'approved', 'rejected', 'cancelled')), acted_by text, acted_at timestamptz,
  comment text, task_id bigint, rule_id bigint, rule_code text, rule_version int, unique (invoice_no, step));
create table invoice_document_link (invoice_no text not null references invoice (no), document_id int not null
  references document (id), kind text not null check (kind in ('copy', 'supporting')), added_by text,
  primary key (invoice_no, document_id));
alter table invoice_draft add column received_on date, add column received_at timestamptz,
  add column sender_verdict text, add column kind text;
-- dedupe_key recomputed in Python (10.6.6), then:
drop index invoice_dedupe;
create unique index invoice_live_key on invoice (supplier_code, fy, type, dedupe_key) where status <> 'cancelled';
create unique index invoice_live_irn on invoice (upper(irn)) where irn is not null and status <> 'cancelled';
create or replace view budget_use ... ;  -- adds bills from authorisation until paid in part 'order' (10.4.1)
insert into org_setting (key, value, description) values ('non_po_duplicate_window_days', '7', '...')
  on conflict do nothing;
```
Existing invoices: `kind 'po'`. The masters importer (`masters/schema.py` via `scripts/gen_master_schema.py`) learns
`non_po_rule.csv`; readiness (`platform/readiness.APPROVE`) adds `"non_po_invoice": "invoice.approve"` and warns when a
rule is enabled and no `non_po_invoice` approval row exists. Cutover (`company cutover`) carries `non_po_rule` as
configuration. Down: refused while any `kind = 'non_po'` invoice exists; else drops the above and restores the old key
and index. Then `scripts/gen_schema_doc.py`; `docs/data-dictionary.md` rows.

### 7.1 Code (one job each)

| Module | Change |
|---|---|
| `formulas/matching.py` | `normalize_invoice_no` (leading zeros), `header_problem` (`tax_head`, bill buyer GSTIN), `TAX_HEAD` reason in `BLOCK_KIND` (`tax`) |
| `formulas/non_po.py` (new, pure) | `rule_problems(rule, category, total, supplier status, approved, cost centre, gl)`, `possible_duplicate(candidates, window)` with boundary tests |
| `invoices/non_po.py` (new) | rule in force, authoriser, `authorise`, `approve`, `reject`, steps and tasks, budget check call, evidence links |
| `invoices/service.py` | `InvoiceIn.kind`, bill fields, documents; advisory lock and unique key; IRN twin; copy inserted as cancelled; bill branch: header and arithmetic only, then `non_po.to_authorise`; notes' header checks; `approved_by` when a match approves (N-8); the bill refusal in `request_release` (N-3) |
| `invoices/reading.py` | registered sender and verdict; received date; DBN; bill drafts; edits; `_maybe_apply` removed |
| `ai_steps/invoice_reading.py` | `debit_note`, prompt v2 |
| `invoices/payment.py` | `accepted_on`, `_goods_only` (N-4) |
| `approvals/budget.py`, `budget_ledger.py` | `check_invoice`; `post_payment` for bill lines |
| `agents/autonomy.py` | `invoice.read` in `LOCKED` |
| `platform/invoice_api.py`, `invoice_intake_api.py` | fields, three actions, permissions (10.11) |
| `platform/buying_rules_api.py` | `GET/POST /api/setup/non-po-rules` (same pattern as the other versioned rules) |
| `platform/workspace_api.py`, `exceptions.py` | sections and kinds (8.1) |
| `platform/demo_cases.py` | one bill waiting for authorisation (Apex) |
| `datagen/`, `tests/invoice_documents.py` | section 3.1 |

No new agent tool, no CLI.

---

## 8. Public interface (for the eval writer)

All `/api` under the tenant session, JSON; errors 422 (input), 409 (state or rule), 403 (permission or segregation),
404 (not in this company).

| Endpoint | Body | Success | Errors |
|---|---|---|---|
| `POST /api/invoices` | as today, plus `kind` (`po` default, `non_po`); for `non_po`: `category_code`, `cost_centre_code`, `gl_code`?, `documents: [{"id", "kind": "copy"|"supporting"}]`, `not_duplicate_reason`?; lines `description`, `qty`, `unit_price`, `gst_rate`, `hsn_sac`? and no `po_no` | `{"invoice", "status", "result", "reasons", "duplicate_of", "kind", "authoriser"}`; status `cancelled` (copy), `blocked`, `approved`, `awaiting_authorisation` | 422 (10.1.3, 10.7.4); 409 (10.1.1-2, 10.6.3, 10.6.5); 403 no `invoice.book` |
| `POST /api/invoices/{no}/authorise` | `{"note"?, "over_budget_reason"?}` | `{"status", "approvers": [{"step", "name", "role"}], "rule", "budget": [...]}` | 403 not the authoriser or the enterer; 409 not waiting, no rule, over budget without reason |
| `POST /api/invoices/{no}/approve` | `{"note"?, "over_budget_reason"?}` | `{"status", "next"}` | 403; 409 not this step, over budget without reason |
| `POST /api/invoices/{no}/reject` | `{"reason"}` | `{"status": "cancelled"}` | 422 no reason; 403; 409 |
| `GET /api/invoices/{no}` | - | adds `kind`, `category`, `cost_centre`, `gl_code`, `authoriser`, `authorised_by`, `authorised_on`, `steps[]`, `budget[]`, `documents[]`, `suspect_of`, `not_duplicate_reason`, `can_authorise`, `can_approve` | 403 (10.11), 404 |
| `GET /api/invoices` | `?kind=&status=` | as today plus `kind`, `category` | 403 |
| `GET /api/invoices/non-po/options?supplier=` | - | categories with a rule in force for the supplier: `max_value`, `supporting_doc`, default GL, proposed cost centre | 403 |
| `POST /api/invoice-drafts/{id}/confirm` | `edits` as 10.5.3 | as today | as today plus 10.1, 10.6 |
| `GET /api/invoice-drafts[/{id}]` | - | adds `kind`, `received_on`, `sender_verdict` | 403 (10.11) |
| `GET/POST /api/setup/non-po-rules` | `{"category_code", "effective_from", "enabled", "max_value", "supporting_doc", "reason"}` | versions; 202 with the proposal | 422 stocked category, past start; 403; 409 one waiting |
| `PUT /api/setup/automation/invoice.read` (existing) | `{"level": "auto", ...}` | - | 422 "Enter an invoice read from a document is always decided by a person and can never be set to act alone" (`LOCKED`, note in `LOCKED_NOTES`) |

Tables the eval may read: `invoice (kind, status, dedupe_key, fy, irn, duplicate_of, suspect_of, authoriser,
authorised_by, authorised_on, approved_by, approval_rule_code, non_po_rule_id, due_date, received_on, payment_block, block_reason,
cancel_reason)`, `invoice_line`, `invoice_approval_step`, `invoice_document_link`, `invoice_draft (status, path, kind,
received_on, sender_verdict, problems, assumptions)`, `non_po_rule`, `match_result`, `budget_actual`, `budget_use`,
`task`, `email_message`, `model_call`, `audit_event`.

Audit actions: `invoice.enter` (adds `kind`, `category`, `cost_centre`), `invoice.duplicate` (`by`: `number`|`irn`),
`invoice.not_duplicate`, `invoice.evidence`, `invoice.to_authorise`, `invoice.authorise`, `invoice.approve_step`,
`invoice.approve`, `invoice.approve_over_budget`, `invoice.reject`, `invoice.cancel`, `invoice.match` (header reasons),
`invoice_draft.create` (adds `auth_verdict`, `received_on`), `invoice_draft.confirm`, `non_po_rule.version` (with
`setup_change`), `task.create`, `task.complete`, `task.cancel`, `email.queue`.

### 8.1 Screens (original ProcureOS look and classes; no redesign)

- **AP** (`ui/js/views/invoices.js`): a tab **Without an order**; **Enter an invoice** gains "Is there a purchase
  order?" (no: category from the supplier's allowed ones with its limit, cost centre, GL, lines without order, the bill
  copy and the named supporting document as uploads); the possible-duplicate question with a reason box; a draft shows
  `kind`, the sender's verdict and the received date; the invoice page shows authoriser, steps, budget, documents.
- **Authoriser and approvers**: **My work** sections "Bills to authorise" and "Bills to approve" (from their tasks); the
  invoice page with **Authorise** / **Approve** / **Reject** and the over-budget reason box when needed. They see only
  that bill (10.11).
- **Exceptions** (`exceptions.js` through existing rows): `invoice_authorise`, `invoice_approve` past due ("Bills
  without an order waiting", owner the named person, next step "Authorise or reject the bill"), `invoice_sender_check`
  (AP).
- **Admin** (`frontend/src/pages/setup-buying.tsx`): a section "Bills without an order": version in force per category,
  history, a new version from a date; the approval-rules page lists `non_po_invoice`; Automation shows `invoice.read`
  locked.

### 8.2 Docs

`docs/demo-script.md` a part "A bill without an order" (ap@ enters Sahyadri's internet bill N1 -> planthead@
authorises -> approved; the CA's fee N2 with its engagement letter -> planthead@ -> finance@ approves); guides
`ap-clerk.md`, `approver.md`, `finance.md`; data dictionary; schema doc; CLAUDE.md "Where things are" (invoices row:
`invoices/non_po.py`, `formulas/non_po.py`) and the latest migration number (0170).

---

## 9. Boundary cases and worked figures

Common: `demo reset apex` (and `brightwave` for N7, T1); business date pinned; role logins; Mailpit. PLT-CKN works
Mon-Sat, closed 24 Nov and 25 Dec 2026. Tax per line, half up to paise; total rounded to the rupee.

| # | Steps | Expected |
|---|---|---|
| **N1** Bill, auto band | Wed 25 Nov: ap@ enters SUP-010 `SNL/2627/0458` of Mon 23 Nov, UTL, CC-ADM, 1 x 15,000.00 at 18%, buyer 27APXCA0000P1ZL, IRN = the e-invoice hash of the number, bill copy; planthead@ authorises | taxable 15,000.00, CGST 1,350.00, SGST 1,350.00, total 17,700.00; `awaiting_authorisation`, authoriser U07; on authorising: NPO-01 v1 (auto) -> `approved`, due Wed 23 Dec 2026 (NET30 from the invoice date); no TDS booked; held 15,000.00 on CC-ADM 5460 |
| **N2** Bill, finance band | Wed 25 Nov ap@ enters SUP-011 `MG/2627/112` of Thu 19 Nov, PRF, 1 x 75,000.00 at 18%, copy and engagement letter; Fri 27 Nov planthead@ authorises; Mon 30 Nov finance@ approves | 75,000.00 + 6,750.00 + 6,750.00 = 88,500.00; NPO-02 -> one step U05; approved; due Sun 27 Dec 2026 = min(NET45 from 19 Nov = 3 Jan 2027, MSME 30 days from acceptance 27 Nov); with acceptance = received 25 Nov it would be 25 Dec (mutation) |
| **N3** Refusals | as N2 without the engagement letter; RAW bill; SUP-001 billing UTL; SUP-007 (blocked) billing UTL; UTL to GL 1430; a line with `po_no` | 422 letter words; 409 "Raw materials cannot be billed without a purchase order"; 409 not approved for UTL; 409 blocked; 409 GL; 422; nothing written |
| **B1/B2** Limit | UTL taxable 42,372.88 at 18%; then 42,373.73 | B1 CGST = SGST 3,813.56, total 50,000.00: allowed. B2 3,813.64 each, 50,001.01 -> 50,001.00: 409 limit words |
| **N4** Segregation | N2 entered by ap@; ap@ authorises; planthead@ approves the step; delegation U07 -> U09 from 26 Nov: director@ authorises | 403, 403 (not the step's approver); the deputy's authorisation recorded with `authorised_by` U09; U05 still approves |
| **N5** Reject | N2 authorised; finance@ rejects "fee not agreed"; ap@ enters `MG/2627/112` again | `cancelled`, reason words, task cancelled, mail to ap@; the second entry is live (no twin) |
| **N6** Over budget | N2 approved; MG/2627/115 (80,000.00, total 94,400.00), /116 (85,000.00, 1,00,300.00), /117 (90,000.00, 1,06,200.00) each with its letter, authorised and approved in turn | CC-ADM 5410 budget Apr-Nov 3,20,000.00; at /117's approval held 2,40,000.00 + 90,000.00 = 3,30,000.00 (103.13%): 409 "over the budget ... by Rs 10,000.00"; with a reason approved, `invoice.approve_over_budget`; /116 was 75% (ok). When N2 is paid: `budget_actual` 75,000.00, source payment, CC-ADM, 5410, 2026-11 |
| **N7** Brightwave | SUP-007 `NFN/2627/1187` of 23 Nov, TEL, CC-ITO, 25,000.00 at 18% | 29,500.00; authoriser U08; auto band; approved; due 23 Dec 2026 |
| **D1** Key | after N1: `SNL/2627/458`; `snl 2627 0458`; same number dated 5 Apr 2027; as a credit note | first two cancelled copies of N1; third live (FY 2027-28); fourth live (type) |
| **D2** Race | two `POST /api/invoices` of one new SUP-006 invoice at the same moment (20 tries) | each time one live, one cancelled copy or 409; never two live |
| **D3** IRN | Shree Wires invoice X with its IRN; another number with X's IRN | copy of X, "The same IRN ..." |
| **D4** Possible duplicate | after N1: SUP-010 `SNL/2627/0461` dated 30 Nov, 17,700.00, with its IRN; then `/0462` dated 1 Dec; then `/0463` dated 27 Nov with a reason | 30 Nov (7 days): 409 with N1's number, date, total; 1 Dec (8 days): entered; with reason: entered, `suspect_of` N1, audit |
| **D5** Migration | rows with `INV-001` and `INV-1` (cancelled) before 0170 | keys `INV1`; upgrade succeeds; a second live `INV-1` would be a copy |
| **G1** Tax head | ap@ enters SUP-002 (24) to PLT-CKN (27) with CGST and SGST; SUP-001 (27) with IGST; zero-rated line | first two blocked `tax_head` with the words; zero passes |
| **G2** GSTIN | typed supplier GSTIN with a wrong check character; a UTL bill `SNL/2627/0470` like N1 with buyer 24APXCA0000P1ZR; a PRF bill of SUP-011 from a Gujarat registration (`24MEHFM0000G1Z` + its check character, IGST 18%) not yet on file, then that GSTIN added to SUP-011's addresses | 422; `/0470` blocked `gstin_mismatch`, release asked 409 (N-3), cancelled by AP; the PRF bill blocked, then **Match again** -> `awaiting_authorisation` |
| **G3** Notes | credit note of SUP-001 with IGST; clean credit note; DBN QR of Shree Wires | first blocked `tax_head`; second approved (as today); DBN draft type `debit_note`, problem "must say which invoice" until edited |
| **M1** Sender | owner sets `mail_authserv_id` = `mx.apexpumps.example`; after N1, four mails to invoices@, each with a different generated SUP-010 bill (QR), from `billing@sahyadrinetlink.example` with a trusted header saying pass; none (no header); fail; and from an unregistered address | pass: draft pending, kind `non_po`, UTL, CC-ADM proposed from N1 (assumption); none: the assumption; fail: no `model_call`, path none, task `invoice_sender_check`; unregistered: read by GSTIN with the assumption |
| **M2** Received date | mail stored Fri 27 Nov; ap@ confirms Mon 30 Nov | invoice `received_on` 27 Nov; tolerance version and budget month of 27 Nov |
| **A1** No auto entry | admin sets `invoice.read` to auto; a clean QR e-invoice mail | 422 (locked), nothing waits for a second administrator; the draft stays `pending` until ap@ confirms |
| **P1** Who sees | ap@ enters a UTL bill on CC-MNT (owner U01, who holds no invoice permission); store@ lists drafts and opens it; production@ opens it, N1 and the draft list | store@ 403, 403; production@ 200 on that bill (its authoriser), 403 on N1 and on drafts. planthead@ (Approver role, `invoice.approve`) sees every invoice, as today |
| **T1** Tenants | Brightwave ap@ opens N1; enters SUP-010's number | 404; no Apex rule used; key spaces apart |

Mutation date checks: due date from `received_on` instead of `authorised_on` (N2 -> 25 Dec); possible-duplicate window
exclusive (D4 30 Nov entered).

---

## 10. Tests and evals

### 10.1 pytest (scenario names; given, when, then)

- `tests/test_non_po_formulas.py`: `rule_problems` each branch; `possible_duplicate` at 6, 7, 8 days both ways;
  `normalize_invoice_no` (`INV-001`, `inv 1`, `INV-000`, `0`, `A-0B-007`); `header_problem` tax head both ways, zero
  tax, bill buyer GSTIN.
- `tests/test_non_po_invoices.py`: N1-N7, B1/B2; tasks, escalation chain, emails, audit rows.
- `tests/test_invoice_duplicates.py`: D1-D5, threads for D2, the migration's recompute and stop.
- `tests/test_invoice_reading.py` (extend): M1, M2, A1, DBN, bill drafts (QR and model), edits.
- `tests/test_budget_engine.py` (extend): N6 held and spent.
- `tests/test_tenant_isolation.py` (extend): T1, P1.
- **Update:** `test_invoices.py` (`PIS242536`), E27 (the gate part: a locked gate enters nothing; clean drafts wait for
  AP), `test_mailbox_settings.py` if it relies on the Date header, E44 (the `invoice_authorise`, `invoice_approve` and
  `invoice_sender_check` rows join its oracle where its scenario raises them), `demo_cases.py`. Each change listed in
  the report.

### 10.2 Evals E152-E159 (independent; written from this spec only)

Common: fresh `demo reset apex` (and `brightwave` where named); business date pinned as in section 9; role logins;
Mailpit read for mails; oracle = section 9 arithmetic in exact fractions with half-up paise, `non_po_rule.csv`,
`approval_rule.csv`, `budget.csv`, `payment_terms.csv`, the holiday files and `truth/truth_invoice_intake.csv`; each ends
with a tenant check and names two planted mutations it must catch.

| Eval | Given | When | Then | Independent oracle | Mutations caught |
|---|---|---|---|---|---|
| **E152** Bills without an order are allowed only as configured | Apex as 3.1 | N1, N2, N3, B1/B2; a new UTL version from tomorrow with `max_value` 40,000 approved by admin2@ (admin@ refused) | totals and taxes; refusals with their words and status codes, nothing written by a refusal; evidence rows; today's bill uses v1, tomorrow's v2 (its `non_po_rule_id`) | rule rows and amounts from the CSV files; GST by hand | limit compared on taxable value; stocked category allowed |
| **E153** Department authorisation and approval by amount | Apex | N1, N2, N4, N5, N7 (Brightwave) | authoriser, steps, rule code and version, statuses, tasks and their SLA due times, mails' words, segregation refusals, deputy, rejection, due dates | chain from `approval_rule.csv` by hand; working hours on the PLT-CKN calendar; MSME days from the settings | enterer authorises; due date from received date |
| **E154** The budget sees bills without an order | Apex | N6, then a run paying N2 | held per bill from authorisation, over-budget refusal and reason, `budget_actual` at payment | budget rows summed Apr-Nov; taxable values | held from entry instead of authorisation; bill not posted at payment |
| **E155** One live invoice per key | Apex | D1, D2 (20 parallel pairs), D3, D5 | copies, `duplicate_of`, words; never two live under the partial key or IRN; FY and type apart; migration result | its own normaliser written from 10.6.1; counts per key | leading zeros kept; check without lock |
| **E156** Possible duplicate bills | Apex | D4, plus the same total from SUP-011 in another category | 409 words at 7 days, none at 8, reason recorded and audited, other category not asked, invoices with an order never asked | date arithmetic | window exclusive; PO invoices asked |
| **E157** Header checks and notes | Apex | G1, G2, G3 | block reasons and words; 422 on a bad check character; bill not releasable; rematch after the fix; notes blocked or approved | state codes from the GSTINs; `formulas/gstin` rule written out | tax head ignored; credit note approved with a wrong GSTIN |
| **E158** Invoice mail and drafts (AI capability, D54) | Apex with the generated bills, debit note and PO invoices | M1, M2, A1, P1; the documents read with the scripted model; `--live` with the real model | sender verdicts; no model call on `fail` or on QR; received date; nothing entered without AP; field accuracy at least 95%, silent errors 0; live result written to `evals/results/live.*` with the threshold | the documents' answer key from the dataset rows | `fail` mail read by the model; received date = confirm day |
| **E159** Tenant isolation of intake | Apex and Brightwave | T1, P1; Brightwave N7 | 404s, 403s, separate rules and keys, no Apex row in Brightwave's database | direct reads of both databases | permission check missing on drafts; another tenant's rule used |

---

## 11. Cross-cutting

| Rule | Here |
|---|---|
| Money | Decimal, paise; totals and taxes by `formulas/orders`; limits compared on the recorded total |
| A model never decides | S02 only copies; category, cost centre, duplicates, checks, chain, budget, due date are code |
| One service per write | `enter_invoice`, `invoices/non_po.py`, `confirm_draft`; rules through the setup service; mail through the outbox |
| Audit | every write (section 8 list) |
| Proposals | rule versions (D-22); no release of a bill's block; nothing pays |
| Segregation | enterer, authoriser, approvers different; approver holds `invoice.approve`; `approved_by` kept (N-8); payer check is block 12's (2.7) |
| Tenant safety | new rows in tenant tables; T1, P1 |
| Rules as data | `non_po_rule` and `approval_rule` versioned; the bill keeps both versions |
| Dates | financial year of the invoice date for the key; working days on the location's calendar for tasks; received date from the mail |

---

## 12. Open points (RECOMMENDED defaults)

| # | Question | Options | RECOMMENDED and reason |
|---|---|---|---|
| OP-1 | Which categories, limits | a) a versioned `non_po_rule` per category (enabled, max total, supporting document); stocked never; b) a flag on the category | **a**: "configured categories" with limits as versioned data (rule 8) |
| OP-2 | What is "evidence" | a) the bill copy always, plus one named supporting document per rule; b) copy only; c) free attachments | **a**: testable, and the rule says what a reviewer expects |
| OP-3 | Who authorises for the department | a) the cost centre owner, else department head, else their manager, never the enterer; b) always the department head | **a**: the budget holder knows the bill; data already has owners |
| OP-4 | Approval by amount | a) `approval_rule` document `non_po_invoice` (auto to 50,000; finance manager to 5,00,000; director or CFO above); b) a new table | **a**: same rule engine, versions, deputies, readiness |
| OP-5 | Send back for correction | a) no: reject cancels, AP enters again; b) return with edits | **a**: simplest; the number is free again |
| OP-6 | Releasing a blocked bill | a) never by proposal: fix and match again, or cancel; b) as invoices with an order | **a**: no order to compare against; a tax or arithmetic fault needs a corrected bill |
| OP-7 | Budget | a) held from authorisation, spent at payment, over-budget reason from the last decider; b) not counted | **a**: D-10's "the rest when paid"; an incurred cost is never refused |
| OP-8 | Key normalisation | a) FR33 leading zeros added, letters kept, separators dropped; b) also strip letter prefixes | **a**: FR33's own example passes; letters tell series apart |
| OP-9 | FR33's "amount + date within 7 days" | a) only for bills, as a question with a reason; b) every invoice; c) none | **a**: 31 legitimate same-total pairs within 7 days in the two histories; orders bound invoices by quantity |
| OP-10 | Sender of invoice mail | a) block 6's verdicts (`fail` not read, `none` read and flagged), GSTIN fallback kept; b) registered contacts only | **a**: accounts teams send from many addresses; AP confirms everything anyway |
| OP-11 | Recompute a typed IRN | a) no, until the formula is confirmed on one real e-invoice at pilot setup (V04); b) yes | **a**: a wrong formula would block every real e-invoice |
| OP-12 | Received date of a mailed invoice | a) the day we stored the mail; b) the mail's Date header; c) the confirm day (today) | **a**: as quotes (block 6); a sender cannot backdate it and AP's delay does not move it |
| OP-13 **NEEDS OWNER** | May the platform enter a clean reading without AP? | a) never: `invoice.read` locked (D54 "AP confirms"); b) keep the gate (auto possible); c) auto for code-only QR invoices from a `pass` sender | **a, interim**: D54's table names AP for every invoice; the owner has seen the gate on the Automation screen and E27 uses it, so the owner confirms |
| OP-14 | Email links to authorise or approve | a) no, app only; b) signed link plus code (D-07) | **a**: a login is enough; links can follow with block 4's machinery |
| OP-15 | Code above `approval_otp_above` on bills | a) no: bill limits are far below it; b) yes | **a** |
| OP-16 | E158 live threshold | a) 95% fields, 0 silent errors (P1-16); b) higher | **a**: the plan's figure |
| OP-17 | History of bills in the datasets | a) none: scenarios are made live; b) a year of bills | **a**: E19, E45, E55 replay the history unchanged |

Only **OP-13** needs the owner.

---

## 13. Where the plan and the code disagree

1. No invoice without an order: `invoice_line.po_no` NOT NULL (D-14, H-1).
2. The duplicate check is select-then-insert with no unique key: two entries at once are both live (3.3).
3. FR33's leading zeros: `INV-001` and `INV-1` are different keys; the same IRN under another number is not caught.
4. `invoice.read` can enter an invoice with no AP, though D54 says AP confirms.
5. Invoice mail ignores the sender verdict; a `fail` mail is read by the model (D53's principle).
6. An emailed invoice's received date is the day AP confirms (or the sender's Date header for `received_at`).
7. Credit and debit notes are approved with no header check; a debit note is never read as one.
8. A line with no order line would get 194Q and post nothing to the budget (`_goods_only`, `post_payment`).
9. Drafts and invoices are readable by any login of the company.
10. CLAUDE.md says the latest tenant migration is 0055; the worktree is at 0150.

---

## 14. Build order

1. **Data first:** section 3.1; `--check`; documents; truth file.
2. Migration 0170, key recompute, schema doc, data dictionary, importer schema.
3. `formulas/matching` and `formulas/non_po` with boundary tests.
4. 10.6 key, lock, IRN; 10.7 header checks.
5. 10.1-10.4 bills, authorisation, approval, budget; payment hooks (N-4, N-5).
6. 10.8-10.10 mail, drafts, AI step v2, gate locked; 10.11 permissions.
7. Screens, emails, admin page, demo case, docs; tests of 10.1 updated.
8. Evals E152-E159 by a separate agent from this spec; E158 `--live` run kept.

## 15. Done when (Plan l.614)

- `python scripts/check.py` is green (ruff, mypy strict, pytest, biome, tsc);
- E152-E159 and every existing eval pass, E27 updated for the locked gate; E158's live result kept with its threshold;
- both demo companies reset, `--check` passes, and the walk-throughs E33, E34 and E46 pass unchanged;
- `docs/database-schema.md` regenerated after 0170; data dictionary, guides, demo script and CLAUDE.md updated;
- every write in this block has its audit row; OP-13's interim is listed for the owner.
