# Block 12 spec: payment (flow step 12)

*Cross-block contracts with blocks 9-13 (built 9, 10, 11, 12, 13 in order): `handoff/specs/blocks9-13-interfaces.md`.*

Tasks **2.1** (payment states, no invented UTR, evidence, recorder not approver, failure reopens), **2.2** (run
integrity), **2.3** (advances), **2.4** (credit and debit notes, recovery claims), **2.5** (TDS proposed and confirmed;
MSME shorter terms, DF-30), **2.7** (code on run approval; payer checked) and **2.8** (evals). Spec only; nothing here is
built yet.

**Task 2.6 stays in block 13.** It is the accounting export of payables, payments and advances (Plan l.496, "format per
client"). It only reads what this block writes (payment states, value dates, bank references, TDS rows, advance and
note balances), so it separates cleanly: block 12 keeps those columns stable (section 4.1) and block 13 writes the export.

**Not in this block:** the accounting export (2.6, block 13); two-track closure and short-close (D-12, D-13, 6.6, block
13: this block only moves today's closing from "marked paid" to "settled" and undoes it on a reversal, 12.4.6); GSTR-2B,
PPV, supplier statements and the bank-lines screen (block 13; the statement's settlement rules are here, 12.4); blocks,
block kinds, holds and their release (block 11: payment honours any `payment_block` other than `none`); receipt reversal,
the return document and the recovery claim record (block 9, built before: this block adds the debit note to its claims,
12.7.4); invoice intake, the duplicate key and the invoice approver `invoice.approved_by` (block 10); ERP-paid status and finance attestation (D-24, WP-10); agents on payments
(11b.4: drafts only; Rev D54 "never touch payments"); part-paid invoices at cutover (decision 3, unchanged).

**Sources.** `planning/P2P-Project-Plan-End-to-End.md` (**Plan**): roles l.155 and l.168 ("Approve a payment run; record
payment: Run approver; the recorder is not the approver"), steps 12-13 gap table l.393-409, WP-2 l.446, tasks l.491-498,
block order l.590-611, "Every package ends when" l.614, AT-06, AT-12, AT-13 l.814-821, release gate 8 l.834, defects
DF-01, DF-02 l.845-846, DF-05 l.849, DF-17 l.861, DF-21, DF-22 l.867-868, DF-30 l.878, decisions S1/C2 l.1064, D-11
l.1075, D-16 l.1080, D-18 l.1082, D-21 l.1085, D-23 l.1087, D-24 l.1088, Q18 l.1126, MSME l.1132, bank verification
decided 19 Nov l.1139. `planning/P2P-13-Step-Plan.md` l.73 ("Due invoices paid in a run the director approves"), l.148,
l.171 ("a person approves with a one-time code"), l.186 (MSME). `planning/P2P-Plan-Agentic-Revision.md` (**Rev**) AG03
l.429, D54 table l.441 ("Never: release; accept variances; touch payments"), decision points l.477-478.
`planning/P2P-Screens-Brief.md` l.65-66 (Finance, Tax owner), 5.12 l.226-236, decision card l.264.
`planning/Decisions-22-Nov.md` #2, #3, #9. `planning/P2P-Phase1-Pilot-Baseline.md` DF evidence l.195-224, WP-2 l.410-420.
**Code read:** worktree `p3-b8` at 8ac2221 (blocks 0 to 8; tenant migrations to 0150, evals to E143).

*RECOMMENDED* marks a default this spec proposes because the documents are silent. **NEEDS OWNER** marks a point that
would change an approved decision; none is needed here (section 6).

---

## 1. What the decisions say

| Source | Text |
|---|---|
| C2 (Plan l.1064) | "Payments outside the platform recorded with evidence"; baseline: "UTR never invented" |
| Q18 (Plan l.1126) | "Settlement evidence: 'recorded' with UTR or proof; 'settled' only on bank match, ERP paid status or finance attestation; failures reopen" |
| Plan 2.1 / WP-2 | "Payment states; no invented UTRs; evidence upload; recorder not approver; failure or reversal reopens" |
| Plan 2.2 | "Run integrity: one live run per invoice; rejected run releases items; file re-checks" (baseline l.676: "status, blocks and bank verification") |
| Plan 2.3, D-18, D-23 | "Advances: request, own approval (D-18), payment, adjustment, recovery, cancel, TDS when paid"; "Advances on the PO with limits and recovery"; "An advance means no touchless" (built, block 0) |
| Plan 2.4, D-21 | "Credit and debit notes; unlinked credits; recovery claims; the debit note raised from a return (4.3)" |
| Plan 2.5, D-16 | "TDS proposed and confirmed; 194C and 194J; service lines by category; mixed invoices; MSME due date when there is no agreement takes the shorter terms (DF-30)"; "TDS proposed by the platform, confirmed by the tax owner" |
| Plan 2.7 | "One-time code on run approval; payer checked against requester and invoice approver" |
| Plan l.168, Rev l.478 | "Approve a payment run; record payment: Run approver; the recorder is not the approver" |
| 13-step l.73, l.148 | "Due invoices paid in a run the director approves"; "Payment preparer drafts the run; Director approves" |
| AT-06 / AT-12 / AT-13 | "PO cannot be cancelled with an open advance; recovery traced"; "Invoice reopened; not settled; no invented UTR"; "Unverified account never paid; change after run approval removes the line" |
| Decided 19 Nov (l.1139) | A bank change takes the supplier out of every open run; approved runs keep their approval for the rest; the file is voided; "an invoice settled wholly by an advance also waits for verification" |
| Rule 2, 5, 7, 8, 9 (CLAUDE.md) | Numbers from `formulas/`; payment runs are proposals; requester, approver, receiver, invoice approver and payer differ; rates are versioned data; business calendar and financial year |
| Rev D54, AG03 | Invoice and AP agent: "Never: release; accept variances; touch payments" |
| MSME (Plan l.1132) | "15 days or agreed days up to 45 (the app uses 45)": tax adviser; release gate 8: adviser signs off TDS, advances, MSME |

Silent on: the calendar for a due date on a holiday (OP-3, OP-4), a code on every run or above a limit (OP-2), whether a
statement settles per payment or per run (OP-5), how a bank return is recognised (OP-7), which TDS rows need a person
(OP-8), how 194C and 194J thresholds apply (OP-9), who approves an advance (OP-12), a pending bank change (OP-16), the
payer's duties beyond requester and invoice approver (OP-17).

---

## 2. What exists today

| Part | Where | Behaviour |
|---|---|---|
| Booking | `invoices/payment.book_invoice` (called by `service.match_invoice`, `_release`) | Once, when an invoice is approved: `due_date` (terms from invoice or acceptance date; micro/small: `min(terms, agreed ≤ 45)` with agreement, **always accepted + 15 without one**, DF-30), advances taken off (`_use_advances`, any advance with `paid_on`), TDS **194Q only**; an invoice with any service line gets **none** (DF-22); one `tds_entry` per invoice (unique index), no confirmation |
| Due date | `formulas/payment.due_date`, `msme_deadline` | Calendar days; **no business calendar**: a due date on a holiday or Sunday stays there |
| What is due | `due_invoices` | `approved`, `payment_block = 'none'`, verified bank account, `due_date ≤ run date + window` (calendar days) or a discount lost by waiting; not in a run of status proposed/approved/file_generated; MSME first |
| Run | `propose_run` | One payment per supplier, advances first; **no lock**: two proposals at once can take the same invoice (DF-05); proposal `payment_run.approve`, permission `payment.approve`, no `document`/`value`, **no approver named**: anyone holding `payment.approve` (Finance, and the Approver role: plant head, purchase manager, director, from migration 0028) may approve, except the proposer |
| Approval | `_approve_run` executor; `admin_api` generic approve | No code (2.7); no requester/invoice-approver check; the dataset's `PAY-01 payment_run ... director` (`cfo` at Brightwave) is **not read** by any code |
| Rejection | `proposals.reject` | Proposal rejected; run stays `proposed` and its invoices and advances are **never released** (DF-05) |
| Bank change | `payment.take_out_supplier` (from `masters/suppliers._set_bank`) | Built (block 0): supplier's unpaid payments leave open runs; file voided; empty run cancelled. A **pending** bank change does nothing |
| Bank file | `generate_file` | Approved run only; one CSV line per payment with amount > 0, the latest verified account; **no re-check** of invoice status or blocks |
| Mark paid | `mark_paid`, `POST /api/payment-runs/{no}/paid` | Invents `UTR<yymmdd><run><n>` when none is given (DF-01), settles every invoice, closes cases, emails the advice with the invented UTR. Off unless `manual_mark_paid` = true; **both demo companies set it true** |
| Statement | `invoices/bank_statement.import_statement`, `confirm` | Debit lines matched by payment number or reference and amount; **a run is settled only when every payment is matched**, then `mark_paid` with the lines' UTRs (a line without one still gets an invented UTR); credit lines skipped; confirm refuses the run approver |
| Advances | `due_advances`, `_advance_payment` | No request or approval: any acknowledged order with `advance_pct` terms is advanced in the next run; TDS row written at proposal and counted in `_cumulative` even if never paid (DF-17); `orders/changes.cancel` ignores advances (DF-17); only header payment terms are frozen once an advance exists |
| Credit notes | `service.enter_invoice` | A credit or debit note is `approved` at entry (no second person, DF-21); a credit note is netted only against its linked unpaid invoice; debit notes never netted |
| Remittance | `_remit` | After mark paid, to the primary contact: amount, date, "Bank reference", invoices, TDS total |
| Codes | `platform/otp.py` | `OneTimeCodes(purpose)`, `needed(db, amount, setting)`; used for requests, POs (`approval_otp_above` 5,00,000), supplier answers, contracts; not for runs |
| SoD | `platform/sod.py` | Five duties incl. `payer`; nothing calls it for payments |
| API reads | `payment_api.due`, `list_runs`, `get_run` | **No permission check**: any signed-in person of the company reads runs and what is due |
| Reference | `ref_tds_section` (`data/reference/tds_sections.csv`) | 194Q, 194C (individual/HUF 1%, others 2%, single 30,000, annual 1,00,000), 194J(a) 2%, 194J(b) 10% (annual 50,000 from 1 Apr 2025), 1961 and 2025 Act rows; holidays to Dec 2026 only |
| Supplier master | `supplier.tds_section_default`, `lower_tds_rate/valid_to/limit`, `pan_status`; `item_category.default_tds_section` | Read only for 194Q today |

---

## 3. Gaps

| Task | Gap | Section |
|---|---|---|
| 2.1, DF-01, DF-02, Q18 | No payment states; invented UTR; settles with no evidence; no failure or reversal | 12.3, 12.4 |
| 2.2, DF-05 | Same invoice in two runs; rejected run keeps its items; no re-check at approval or file | 12.2 |
| 2.7 | No code on run approval; approver not from the rule; payer not checked | 12.1 |
| 2.3, DF-17 | No advance request or approval; paid advance does not stop cancel; advance TDS counted unpaid; no recovery | 12.6 |
| 2.4, DF-21 | Notes approved by nobody; unlinked credits unused; debit notes never netted; no recovery claim | 12.7 |
| 2.5, DF-22, DF-30, D-16 | No 194C/194J; mixed invoices; no confirmation; MSME without agreement ignores shorter terms | 12.5, 12.8 |
| Rule 9 | Due date on a holiday; runs on bank holidays | 12.8 |
| Bank change | Pending change still paid into the old account | 12.2.4 |
| Reads | Payment reads need no permission | 12.10 |

---

## 4. Design

### 4.1 Data: migration 0190 `payment_evidence` (one file; down_revision 0180, block 11)

```sql
-- payment states (12.3)
alter table payment add column status text not null default 'planned' check (status in ('planned', 'approved',
  'in_file', 'recorded', 'settled', 'failed', 'reversed', 'cancelled')),
  add column value_date date, add column bank_account_id bigint references supplier_bank_account (id),
  add column recorded_by text, add column recorded_at timestamptz,
  add column evidence_document_id bigint references document (id),
  add column settled_at timestamptz, add column settled_line_id bigint references bank_statement_line (id),
  add column failed_by text, add column failed_at timestamptz, add column fail_reason text,
  add column fail_line_id bigint references bank_statement_line (id), add column cancel_reason text;
create unique index payment_utr on payment (upper(utr)) where utr is not null;
-- backfill: run proposed -> planned, approved -> approved, file_generated -> in_file, cancelled -> cancelled,
-- paid -> settled where a bank_statement_line (matched/confirmed) names it (settled_line_id), else recorded;
-- value_date = paid_on. Cutover advances (run CUT-%) -> settled.
alter table payment_run add column rule_code text, add column rule_version integer, add column file_hash text,
  add column cancelled_at timestamptz, add column cancel_reason text;
create table payment_run_approval (run_no text not null references payment_run (no), step integer not null,
  role text not null, approver text not null, delegate text, approved_by text, approved_at timestamptz,
  code_id bigint references one_time_code (id), primary key (run_no, step));
-- invoices and notes (12.2, 12.7)
alter table invoice add column in_run text references payment_run (no), add column pay_by date,
  add column offset_used numeric(18,2) not null default 0,
  add column reason text, add column source_ref text;
-- backfill: in_run from allocations of live runs; pay_by = due_date moved to the last working day on or before it.
-- invoice.approved_by is block 10's column (0170), set by blocks 10 and 11; read here.
-- TDS (12.5)
alter table tds_entry add column section_code text, add column ref_tds_section_id integer,
  add column status text not null default 'confirmed' check (status in ('proposed', 'confirmed', 'cancelled')),
  add column proposed jsonb, add column confirmed_by text, add column confirmed_at timestamptz, add column change_reason text;
update tds_entry set section_code = '194Q', confirmed_by = 'rule';
drop index tds_entry_invoice;
create unique index tds_entry_invoice_section on tds_entry (invoice_no, section_code)
  where invoice_no is not null and status <> 'cancelled';
alter table cutover_supplier_ytd add column section text not null default '194Q';
alter table cutover_supplier_ytd drop constraint cutover_supplier_ytd_pkey, add primary key (supplier_code, fy, section);
-- advances (12.6)
create table advance_request (id bigserial primary key, po_no text not null references po (no), po_version integer not null,
  pct numeric(7,3) not null, gross numeric(18,2) not null, taxable numeric(18,2) not null,
  status text not null check (status in ('requested', 'approved', 'rejected', 'in_run', 'paid', 'recovering',
    'recovered', 'used', 'cancelled')),
  reason text, requested_by text not null, requested_at timestamptz not null default now(), proposal_id bigint,
  approver text, approved_by text, approved_at timestamptz, payment_no text references payment (no),
  recovery_note text references invoice (no), cancel_reason text);
create unique index advance_request_live on advance_request (po_no) where status not in ('rejected', 'cancelled', 'recovered', 'used');
alter table advance add column request_id bigint references advance_request (id), add column settled_on date;
-- statement credits (12.4.4)
alter table bank_statement_line add column direction text not null default 'debit' check (direction in ('debit', 'credit'));
insert into org_setting (key, value, description) values
  ('payment_run_otp_above', '0.00', 'Approving a payment run above this total needs a one-time code (0: every run that pays money)'),
  ('tds_auto_confirm_zero', 'true', 'A zero TDS under the item category''s own section needs no confirmation'),
  ('rtgs_from', '200000.00', 'A payment above this goes by RTGS, else NEFT')
  on conflict do nothing;
delete from org_setting where key = 'manual_mark_paid';
insert into role_permission (role, action) values ('Finance', 'payment.record'), ('Finance', 'tds.confirm'),
  ('Finance', 'advance.request'), ('Buyer', 'advance.request'), ('Approver', 'advance.approve')
  on conflict do nothing;
```

Down: drop the new table, columns and indexes, restore `tds_entry_invoice` and the old primary key, delete the grants.
Then `scripts/gen_schema_doc.py`; `docs/data-dictionary.md` rows for every new column and the states below. Columns the
block 13 export reads and this block keeps stable: `payment (status, value_date, utr, amount, tds_amount, msme_interest,
bank_account_id)`, `payment_allocation`, `tds_entry (section_code, base, rate, amount, booked_on, status)`,
`advance_request`, `advance`, `advance_adjustment`, `invoice (offset_used, in_run, pay_by)`.

### 4.2 Code (one job each; reuse first)

`invoices/payment.py` (786 lines) keeps booking and `payable`; the rest moves out, one job per module:

| Module | Job |
|---|---|
| `formulas/payment.py` | `due_date` fix (DF-30); `pay_by(due, calendar)`; `tds_for(section_row, base, cum_before, single, pan, lower)` for 194Q and the full-sum sections; `payee_type(pan)`; `utr_ok(text)`; `offset(payable, notes)` (pure) |
| `formulas/calendar.py` | `BusinessCalendar.on_or_before(d)`: d if working, else the latest working day before it |
| `invoices/tds.py` | propose per invoice and section at booking, cumulative per supplier, FY and section, confirm, change section, advance TDS when paid |
| `invoices/runs.py` | propose (lock, live guard, approver chain, left-out items), `recheck`, `take_out` (generalises `take_out_supplier`), cancel, approve step with code, file |
| `invoices/settlement.py` | record, settle (statement or confirmed line), fail, reverse, zero payments, remittance and return notices |
| `invoices/advances.py` | request, approve (proposal `advance.approve`), use at settlement, cancel, recovery |
| `invoices/notes.py` | credit and debit note approval, supplier balances, `raise_debit_note` (this block adds its call to block 9's claim raising, 12.7.4, and to advance recovery), refund |
| `invoices/bank_statement.py` | settle per payment; keep credit lines; suggest returns |
| `platform/payment_api.py` | endpoints (4.5); permission on every read |
| `platform/proposals.py` | registers `payment_run.approve` on block 9's `closer(action)` hook (run when a proposal ends `rejected` or `expired`) |
| `platform/admin_api.py` | the generic approve/reject refuse `payment_run.approve` (409, words in 4.5) |
| `orders/changes.cancel` | advance check (12.6.5) |
| `agents/autonomy.py` | `LOCKED` adds `payment.record`, `payment.fail`, `advance.approve`, `tds.confirm` (never automatic) |
| `platform/readiness.py` | `APPROVE["advance"] = "advance.approve"`; separation rows SEP-18 (recorder not run approver) and SEP-19 (payer); a warning when a role of the `payment_run` rule is also the only role of a `price_variance` or `non_po_invoice` band (its invoices would be left out of every run, 12.1.4) |
| `platform/workspace_api.py`, `exceptions.py`, `alerts.py` | My work and exception rows (4.6) |
| `platform/cutover_import.py` | `supplier_ytd` optional `section`; cutover advances and payments `settled` |
| `platform/demo.py`, `demo_cases.py` | settings, rows, the paid case through record and statement, run approval with the code |

### 4.3 Rules and settings (versioned data, defaults)

| Data | Where | Default (Apex / Brightwave) |
|---|---|---|
| Who approves a run | `approval_rule` rows `document = 'payment_run'`, banded on the run total (cash out), chain of roles (`a>b` = two steps), versioned by `version`/`effective_from` | `PAY-01 0-999999999 director` / `cfo` (already in both datasets; now enforced). RECOMMENDED (OP-1) |
| Who approves an advance | `approval_rule` rows `document = 'advance'`, on the advance gross | new `ADV-01 0-999999999 purchase_manager` / `cfo` |
| TDS rates, thresholds, payee types | `ref_tds_section` row in force on the booking date; the row id kept on `tds_entry` | reference file as loaded |
| TDS section of a line | `item_category.default_tds_section`, else `supplier.tds_section_default`, else none (a person picks) | SRV 194J(a) (Apex), SWS 194J(a), FAC 194C (Brightwave), goods 194Q |
| Code on run approval | `payment_run_otp_above` | 0.00: every run that pays money (OP-2) |
| Zero TDS without a person | `tds_auto_confirm_zero` | true (OP-8) |
| RTGS above | `rtgs_from` | 2,00,000.00 (was a constant) |
| Existing, unchanged | `payment_run_window_days` 7, `payment_take_discounts`, `msme_max_days` 45, `msme_no_agreement_days` 15, `msme_warn_days` 7, `tds_applicable_on_goods`, `bank_verification_methods` | as today |
| Removed | `manual_mark_paid` (both datasets and the migration) | - |

### 4.4 Requirements

#### 12.1 Who approves a run, with a code (2.7)

1. **The chain.** At proposal `rules.find_rule(db, "payment_run", total, run_date)` gives the rule (code and version kept
   on `payment_run`); `rules.resolve_chain` turns its roles into people, in order, excluding the proposer (conflicted items
   are then left out, 12.1.4). One `payment_run_approval` row per step. A rule naming nobody: 409 "no approval rule
   names who approves a payment run of `<amount>`".
2. **Only the named person** (or their deputy on the date, `approvals/deputies`) approves the current step; anyone else
   with `payment.approve` is refused (403 "this run waits for `<name>`"). Steps go in order; the last step approves the
   proposal and runs the executor (status `approved`). Rejecting is open to any step person (reason required); the
   proposer may withdraw before the first step. Both cancel the run (12.2.3).
3. **Code.** When `otp.needed(db, total, "payment_run_otp_above")`, each step needs a code from
   `OneTimeCodes("payment_run")`, ref `<run>:<step>:<total in paise>` (a total that changes after the code was sent makes
   it useless; the approver asks again and sees the new total). `POST /api/payment-runs/{no}/code` sends it to the
   acting person; `otp` words for refusals ("this needs a one-time code: ask for one (a code works once)", "wrong code; 4
   tries left", ...). The code's `used` commits with the step. `code_id` on the step row.
4. **Payer differs (SoD).** The run's step people are its payers. A payer may not be, for any item: the requester or
   `on_behalf_of` of a request line behind it, the person who confirmed its service, its **invoice approver**
   (`invoice.approved_by`, block 10's column: who released its block (block 11), for a bill its last step's approver or
   its authoriser on an `auto` chain (block 10), else who entered or confirmed it; OP-18), or, for an advance, the
   advance's requester. Also never the run's proposer. At proposal the chain is resolved first; an item whose conflicted
   person is a step person is **left out** with the reason ("`<name>` asked for it and approves this run") and one task
   `payment_left_out` for the proposer listing them (OP-17). The guard checks the same at approval (the facts may have
   changed) and refuses (403) naming the items.
5. **Recorder differs.** Whoever records a payment (12.3) or confirms its bank line may not be a step person of its run
   (403 "segregation of duties: `<name>` approved this run"). Built today for the confirm; extended to every step.
6. The generic `POST /api/admin/proposals/{id}/approve` and `/reject` refuse a `payment_run.approve` proposal (409 "a
   payment run is decided on its page, with a one-time code"). The admin approvals card links to the run page.

#### 12.2 Run integrity (2.2, DF-05)

1. **One live run per item.** `propose` takes a transaction advisory lock (`payment_run` per company), then sets
   `invoice.in_run = <run>` with `where in_run is null` for each chosen invoice and `advance_request.status = 'in_run'`
   with `where status = 'approved'`. A row that does not update is not taken. Two proposals at once: the second gets only
   what the first did not take (or 409 "nothing is due ..."). `in_run` is cleared when the payment is cancelled or fails;
   it stays on a settled invoice.
2. **What may be paid** (`payable_now`, one function, used at proposal, approval and file): invoice `approved`,
   `payment_block = 'none'`, `pay_by ≤ run date + window` (or a discount lost by waiting, as today), no `proposed` TDS
   row, a verified bank account, **no pending `supplier.set_bank` proposal** for the supplier (OP-16), and no live
   unsettled advance on its order (12.6.4). An advance: request `approved`, order not cancelled, same bank checks.
   `GET /api/payment-runs/due` lists what is held back and why ("TDS to confirm", "bank account not verified", "bank change
   waiting for approval", "in run `<no>`", "blocked: `<reason>`", "advance on `<po>` not settled").
3. **Ended runs release.** A run whose proposal is rejected, withdrawn or expired (block 9's `closer` hook), or which a person
   cancels before its file, becomes `cancelled` (`cancel_reason`, `cancelled_at`); its payments `cancelled`; its invoices'
   `in_run` cleared; its advance requests back to `approved`; its `advance` and `advance_adjustment` rows of unpaid
   advances deleted (no money moved). Audit `payment_run.cancel`. The items are due again at once.
4. **Re-check** (`recheck`) runs at each approval step and at the file. Every item that is no longer payable (12.2.2) is
   taken out (`take_out`: allocation deleted, the supplier's payment worked out again with `payable` on the run date or
   cancelled if nothing is left, total updated, `in_run` cleared), audit `payment_run.item_removed` (item, reason). A run
   left empty is cancelled. Approval stands for less than was approved (decided 19 Nov); it never covers more: a run
   never gains items after proposal. A file made before a take-out is voided (as today).
5. **Dates.** `run_date` is a working day of the payment calendar (12.8) on or after the business date (422 "the bank is
   closed on `<dd Mon yyyy>` (`<holiday>`)" / "a run cannot be dated in the past"). The proposal expires at the end of
   the run date (IST); an approved run with no file by then is cancelled by the daily job (`payment_run_lapse`, 18:30 IST)
   with reason "not filed on its date". The file is made only on or before the run date.

#### 12.3 Payment states and evidence (2.1, DF-01, DF-02, Q18, C2)

1. **States** (`payment.status`): `planned` (proposed run) → `approved` → `in_file` → `recorded` (optional) →
   `settled`; or `failed` (from `in_file`/`recorded`), `reversed` (from `settled`), `cancelled` (run ended or item
   removed). A run is `paid` when none of its payments is `approved`, `in_file` or `recorded` and at least one settled.
2. **Record** (`POST /api/payments/{no}/record`, multipart: `utr`, `value_date`, `file`; permission `payment.record`):
   only an `in_file` payment; a value date on or before the business date and on or after the run date; at least one of
   a UTR and a proof file (OP-6); a UTR is letters and digits, 6 to 30 long, unique across payments (409 "UTR `<x>` is
   already on `<payment>`"); the proof is a PDF or image (`files.save_file`, `doc_type` `payment_evidence`). The recorder
   is not a step person (12.1.5). Result `recorded`; nothing is settled: invoices stay `approved` (held by `in_run`), no
   budget actual, no closing. Audit `payment.record`. With a UTR the remittance goes now (12.9).
3. **Never invented.** `mark_paid` and `POST /api/payment-runs/{no}/paid` are removed: the endpoint answers 410 "Marking a
   run paid by hand is gone: record each payment with its bank reference, or import the bank statement". No code path
   writes a `utr` that a person or a bank line did not give; a statement line without a UTR leaves `utr` empty.
4. **Zero payments** (fully covered by advances or notes): settled when the run's file is made (nothing goes to the
   bank), `value_date` the run date, audit `payment.settle` `how: nothing to pay`.

#### 12.4 Settlement, failure, reversal

1. **Settled only by the bank** (standalone): a statement debit line matched to the payment (number or reference in the
   narration or UTR, amount to the paisa, date not in the future), or an open line a person confirms (`bank_statement.
   confirm`, recorder rule 12.1.5). **Per payment** (OP-5): a matched line settles its payment whatever the rest of the
   run. `settled_line_id`, `settled_at`, `value_date` from the line, `utr` from the line when the payment has none (a
   recorded UTR that differs from the line's: the line is listed for a person, "UTR differs from the one recorded").
2. **On settlement:** each invoice of the payment `paid` (`paid_on` = value date, `discount_taken`); notes netted in it
   advance `offset_used` (12.7); `budget_ledger.post_payment`; `close_case` (moved here from mark paid); an advance's
   `advance.paid_on` and `settled_on`, its request `paid`, its TDS row (12.5.6), and its use by the order's booked
   unpaid invoices (12.6.4); the notification to requester and buyer as today; the remittance if not sent (12.9).
3. **Failed** (`POST /api/payments/{no}/failed`: `reason`, `value_date`, and a credit `bank_line_id` or a proof file;
   `payment.record`; not a step person): an `in_file` or `recorded` payment becomes `failed`; its invoices `approved`
   with `in_run` cleared (due again, `pay_by` unchanged, MSME interest from the legal due date if paid later); advance
   request back to `approved` and its `advance` row and advance TDS row removed; audit `payment.fail`; the return notice
   (12.9); a task `payment_failed` for Finance ("Pay `<supplier>` again: `<reason>`"). The run's total is unchanged
   (history); it becomes `paid` when the rest is settled.
4. **Credit lines** of a statement are kept (`direction` credit). One whose narration or UTR names a payment of ours
   with the same amount is listed for a person as "may be the bank returning `<payment>`" (OP-7: never applied by
   itself). Other credits are listed as open lines.
5. **Reversed** (same endpoint on a `settled` payment): status `reversed`; invoices `paid` → `approved`, `paid_on` null,
   `in_run` cleared; a negative `budget_actual` row for each line posted at settlement; order lines and orders that
   `close_case` closed for these invoices reopened to their quantity status (`received`), audit `po.reopen`; a reversed
   advance that invoices already used: refused (409 "the advance was taken off `<invoices>`: raise a debit note to
   recover it") (OP-14).
6. **What block 13 takes over.** This block leaves: the settled state (`payment.status = 'settled'`, invoice `paid` only
   then), the events (`payment.settle`, `payment.fail`, `payment.reverse` audits) and two call sites in
   `invoices/settlement.py`, `close_case` on settlement (12.4.2) and the reopening on reversal (12.4.5). Block 13
   replaces both with `orders/closure.check` and `closure.reopen` and removes `close_case`; nothing here calls block 13.

#### 12.5 TDS proposed and confirmed (2.5, D-16, DF-22)

1. **Per line, per section** at booking: each invoice line takes the section of 4.3 (item category, else supplier, else
   none; a bill line without an order uses its own `category_code`, block 10; this replaces block 10's `_goods_only`
   branch). One `tds_entry` per invoice and `section_code` with `base` = the lines' taxable less the advance taxable taken
   off them (pro rata by line taxable, half up, last line takes the remainder), `ref_tds_section_id`, `proposed` (the
   platform's figures and the words of 12.5.3), `section` the label for the date (2025 Act form from 1 Apr 2026, as
   today: `393(1) Sl.8(ii) [194Q]`, `393(1) Sl.6(i) [194C]`, `393(1) Sl.6(iii) [194J]`).
2. **Cumulative** per supplier, financial year and section: confirmed or proposed entries of booked, not cancelled
   invoices (`booked_on` in the FY, in booking order) plus `cutover_supplier_ytd` for that section, plus **settled**
   advances' TDS rows only (DF-17).
3. **Amount** (`formulas/payment.tds_for`, the row in force on the booking date; Decimal, half up to paise):
   - 194Q: on the part of the year's cumulative above `threshold_annual` (built rule, unchanged);
   - 194C, 194J(a), 194J(b) and any section with `base_rule` "entire amount": the whole base when the cumulative after
     this invoice is **above** `threshold_annual`, or the taxable of the line's order (current version) is above
     `threshold_single`; else 0 (no catch-up of earlier sums, OP-9). Exactly at a threshold is not above;
   - rate: `rate_pct` of the row whose `payee_type` fits (194C: PAN 4th letter P or H is individual/HUF, else others);
     `rate_no_pan_pct` when `pan_status` is inoperative; the supplier's `lower_tds_rate` while `lower_tds_valid_to` ≥
     booking date and the cumulative after stays within `lower_tds_limit`.
   Words kept in `proposed`: "194J(a): `<supplier>` this year 62,000.00 is above 50,000.00: 2% of 12,000.00".
4. **Confirmation.** A row is `confirmed` by the system (`confirmed_by = 'rule'`) when its amount is 0, its section came
   from the item category and `tds_auto_confirm_zero` is true; every other row is `proposed`. A holder of `tds.confirm`
   (the tax owner; Finance in both demos) confirms one or many (`POST /api/tds/confirm`), or changes the section to
   another row in force or to none, with a reason; the amount is then worked out again by 12.5.3 (never typed). An
   invoice with a `proposed` row is not payable (12.2.2). Audit `tds.propose`, `tds.confirm`, `tds.change`.
5. `invoice.tds_amount` = sum of its confirmed rows; `payable` uses it as today.
6. **Advance TDS when paid** (DF-17): no row at proposal (the run shows the figure from 12.5.3 as "TDS to hold"); the row
   is written when the advance payment is settled (or recorded, whichever first), `booked_on` its value date, and deleted
   when it fails. Base = advance taxable (as built; tax adviser, OP-11).

#### 12.6 Advances (2.3, D-18, D-23, DF-17)

1. **Request** (`POST /api/purchase-orders/{no}/advance`: `pct` optional, `reason`; `advance.request`): an order
   `sent`, `acknowledged` or `part_received` whose payment terms carry `advance_pct` > 0; `pct` at most the terms' (422
   "the terms allow at most 30%"); gross and taxable = `formulas/payment.advance_amounts(current version total, taxable,
   pct)`. One live request per order (409). Audit `advance.request`.
2. **Approval**: proposal `advance.approve` (permission `advance.approve`) for the first person `find_rule(db,
   "advance", gross, today)` names, never the requester nor the order's makers (`po_workflow.makers`); guard: only that
   person or deputy. No code (the run that pays it needs one). Rule names nobody: 409. Approved → `approved`; rejected →
   `rejected` with the reason.
3. **Payment**: only approved requests enter runs (`due_advances` no longer reads terms alone), first in the run, its
   own payment of type advance; the `advance` row is written at proposal (as today) with `request_id`.
4. **Use against invoices**: `_use_advances` takes only **settled** advances; on an advance's settlement it runs for the
   order's booked, unpaid invoices not in a live run, in received order (their `advance_used`, TDS base and amount worked
   out again, a changed TDS row going back to `proposed`). An invoice of an order with an approved or in-run, unsettled
   advance is not payable ("advance on `<po>` not settled"). Fully used: request `used`.
5. **Cancel** (`orders/changes.cancel`): a live request not yet in a run is cancelled with the order; one in a run is
   taken out first (12.2.4); a **paid** advance not fully used refuses the cancel (409 "an advance of `<amount>` was paid
   on `<po>`: recover it before cancelling", AT-06) until a recovery note covers the unused gross (12.6.6).
6. **Recovery** (OP-14): `POST /api/advances/{id}/recover` (`advance.request` holder, reason): a debit note
   (12.7, `source_ref advance:<payment>`) for the unused gross and taxable; request `recovering`; the order may then be
   cancelled; when the note is fully netted or refunded, request `recovered`. Audit `advance.recover`.

#### 12.7 Credit and debit notes (2.4, D-21, DF-21)

1. A credit note (supplier's) and a debit note (ours: `POST /api/debit-notes`, `invoice.book`, supplier, optional
   `ref_invoice_no`, `source_ref`, taxable and GST lines, `reason`; also `notes.raise_debit_note` for 12.6.6 and 12.7.4)
   enter `received` (this replaces block 10's interim "a clean note is `approved` at entry"; a note failing block 10's
   header checks stays `blocked` as block 10 says, and after a clean **Match again** waits `received`); another person
   with `invoice.approve` approves them through block 10's `POST /api/invoices/{no}/approve`, to which this block adds
   the note branch (the enterer is refused, 403). Audit `invoice.note_approve`.
2. **Netting** in a run, per supplier, in order: credit notes linked to an invoice in the payment, then the supplier's
   other approved notes oldest first, each up to what is still to pay; never a negative payment. `payment_allocation`
   row with the negative amount used; on settlement `offset_used` grows; a note fully used becomes `paid`. What is left
   stays for the next run (`offset_used < total`).
3. **Refund**: a supplier's refund of a debit note, a credit bank line confirmed by a `payment.record` holder
   (`POST /api/invoices/{no}/refund`, `bank_line_id`, amount to the paisa): `offset_used` grows; `paid` when full.

4. **Recovery claims** (D-21; block 9's `recovery_claim`): this block adds to block 9's claim raising
   (`receiving/returns.py`) a call to `notes.raise_debit_note` in the same transaction: a debit note for the claim's
   taxable and GST (`source_ref claim:<RCL>`, `ref_invoice_no` the first of the claim's invoices), its number in
   `recovery_claim.debit_note_no`. The note is approved and netted (12.7.1-12.7.2) or refunded (12.7.3); when it is
   `paid` the claim becomes `settled`; a note cancelled before use sets the claim back to `open` with no note. Claims
   raised before this block (none outside tests; the demo companies are reset) get theirs through
   `POST /api/debit-notes` with `source_ref claim:<RCL>`. Audit `claim.settle`.

#### 12.8 Due dates on the business calendar (rule 9, DF-30)

1. `due_date` stays the legal and terms date in calendar days. **DF-30:** micro or small without a written agreement:
   `min(terms due, accepted + msme_no_agreement_days)` (today the deadline alone).
2. `pay_by` = `calendar.on_or_before(due_date)` on the **payment calendar** (OP-3): central holidays, Monday to Friday
   (`reference.calendar_for(db)`). Stored at booking. Runs pick by `pay_by`; MSME interest still counts from `due_date`.
3. Run dates are payment-calendar working days (12.2.5).
4. **Data gap:** `ref_holiday` ends on 31 Dec 2026; the 2027 central list is added before a run is planned in 2027
   (section 7.1).

#### 12.9 Remittance advice and return notice

1. Once per payment (`remittance_sent_at`), when it is recorded with a UTR or settled, whichever first; to the supplier's
   contact with role `accounts`, else `masters/suppliers.order_contact`. Fixed words (no model, D20): "Payment advice
   `<payment>`. `<company>` has paid `<amount>` on `<dd Mon yyyy>`. Bank reference: `<utr or the payment number>`." then
   one line per invoice ("`<their no>` dated `<date>`: `<total>`, TDS `<section>` `<amount>`, advance taken off
   `<amount>`, discount `<amount>`"), each note netted ("Your credit note `<no>`: -`<amount>`"; "Our debit note
   `<no>` (`<reason>`): -`<amount>`"), MSME interest, and the total. Never a reference nobody gave.
2. Failed or reversed: "Our payment `<payment>` of `<amount>` on `<date>` (reference `<utr>`) was returned by the bank:
   `<reason>`. The invoices are open and will be paid again." Sent only when an advice went before.

#### 12.10 Reads, screens and agents

1. Payment reads need `payment.view` or `payment.approve` (403 otherwise); a step person may read their run.
2. No agent tool writes a payment, run, TDS row, advance or note; `LOCKED` (4.2) keeps the gates off; a test walks the
   tool registry and fails on any such write.

### 4.5 API (all `/api` under the tenant session; 422 input, 409 state, 403 permission or SoD, 404 not in this company)

| Endpoint | Body | Success | Errors |
|---|---|---|---|
| `GET /payment-runs/due?on&window` | - | rows add `pay_by`, `due_date`, `msme_deadline`, `tds_status`; `held[] {item, reason}` | 403 |
| `POST /payment-runs` | `run_date`, `window_days`, `include_advances` | `{run, status, total, approvers[], left_out[] {item, reason}}` | 422 date (12.2.5); 409 nothing due, no rule |
| `GET /payment-runs/{no}` | - | adds per payment `status`, `utr`, `value_date`, `evidence`; `steps[] {step, role, name, approved_at}`; `can_approve`, `needs_code`, `removed[]`; `can_mark_paid` gone | 403, 404 |
| `POST /payment-runs/{no}/code` | - | `{"sent_to": "<masked>"}` | 403 not your step; 409 not waiting, within 30 s |
| `POST /payment-runs/{no}/approve` | `code`, `note` | `{status, step, next}` | 409 code words, past run date; 403 not named, SoD |
| `POST /payment-runs/{no}/reject` | `reason` | `{status: "cancelled"}` | 422 no reason; 403 |
| `POST /payment-runs/{no}/cancel` | `reason` | proposer or `payment.propose` before the file | 409 file made |
| `POST /payment-runs/{no}/file` | - | as today; `removed[]` | 409 not approved, past date |
| `POST /payment-runs/{no}/paid` | - | - | **410** (12.3.3) |
| `POST /payments/{no}/record` | multipart `utr`, `value_date`, `file` | `{status: "recorded"}` | 422 neither, bad UTR or date; 409 not in file, UTR used; 403 |
| `POST /payments/{no}/failed` | `reason`, `value_date`, `bank_line_id` or `file` | `{status: "failed"|"reversed", invoices[]}` | 409 state; 403 |
| `POST /payment-runs/statement` | `csv` | adds `settled[]` per payment, `returns[]` (credit lines that may be returns) | as today |
| `GET /tds?status=proposed` | - | `[{id, invoice, supplier, section_code, base, rate, amount, words}]` | 403 without `tds.confirm` |
| `POST /tds/confirm` | `{ids[], section_code?, reason?}` | confirmed rows | 422 change without reason; 409 not proposed |
| `POST /purchase-orders/{no}/advance` | `pct?`, `reason` | `{id, gross, taxable, approver}` | 422, 409 (12.6.1) |
| `GET /advances`; `POST /advances/{id}/recover` | `reason` | list; `{note}` | 409 nothing unused |
| `POST /debit-notes`; `POST /invoices/{no}/approve`; `POST /invoices/{no}/refund` | 12.7 | note; approved; refunded | 403 enterer; 409 |

Tables an eval may read: `payment (status, utr, value_date, recorded_by, settled_line_id, failed_*)`, `payment_run
(status, rule_code, total, cancel_reason)`, `payment_run_approval`, `payment_allocation`, `invoice (status, in_run,
pay_by, due_date, approved_by, tds_amount, offset_used)`, `tds_entry`, `advance_request`, `advance`,
`advance_adjustment`, `recovery_claim (debit_note_no, status)`, `bank_statement_line (direction)`, `one_time_code (purpose payment_run)`, `proposal`, `task`,
`budget_actual`, `audit_event`, `email_message`.

### 4.6 Screens per role (ProcureOS look, existing classes; no redesign)

- **Finance** (`ui/js/views/payments.js`): What is due with Pay by, Due, MSME badge and deadline, and a **Held back** list
  with reasons; plan a run (working days only; left-out items shown); run page: steps and who, per payment its state,
  **Record payment** (UTR, value date, proof), **Bank returned it**, **Cancel run**; **Import the bank statement** with
  settled, waiting and "may be a return" lines. **Mark paid** removed. Tabs **Advances** and **TDS to confirm** (tax
  owner: confirm, change section with reason, select several).
- **Director / CFO** (run approver): My work "Payment runs to approve" only for the named step person → run page with
  totals, MSME due, discounts, left-out items, **Email me a code**, code box, **Approve**, **Reject**.
- **Buyer**: order page **Request the advance** (percentage, reason; the amounts shown); advance state on the order.
- **Purchase manager** (advance approver): My work "Advances to approve" → approve or reject (proposal card).
- **AP**: enter credit and debit notes; a note waits for **Approve** by another person; invoice page shows Pay by, TDS
  rows and their state, payment state ("recorded, waiting for the bank").
- **Admin app** (`frontend/`): the `payment_run.approve` card says "Approve it on the run's page (a one-time code is
  needed)" with the link; `advance.approve` card as other proposals.
- **Exceptions** (`platform/exceptions.py`): `payment_failed`, `payment_left_out`, TDS waiting more than 2 working days,
  payments recorded and not settled after 3 working days, advances paid and unused on a cancelled-intent order.

### 4.7 Emails

One-time code (subject "Your code to approve payment run `<run>`", `otp` body); payment advice and return notice
(12.9); task notifications (`payment_left_out`, `payment_failed`, `tds_to_confirm`, `advance_to_approve`); requester
and buyer "Order `<po>` is paid and closed" now on settlement.

### 4.8 Audit actions

`payment_run.propose` (adds `rule`, `approvers`, `left_out`), `payment_run.step_approved` (step, code), `payment_run.
approve`, `payment_run.cancel`, `payment_run.item_removed`, `payment_run.lapse`, `payment_run.file`, `payment.record`,
`payment.settle` (`how`: line, confirmed line, nothing to pay), `payment.fail`, `payment.reverse`, `payment.remit`,
`tds.propose`, `tds.confirm`, `tds.change`, `advance.request`, `advance.approve`, `advance.reject`, `advance.use`,
`advance.recover`, `invoice.note_approve`, `invoice.note_refund`, `claim.settle`, `po.reopen`, `otp.issue/wrong/expired/use` (purpose
`payment_run`), `proposal.*`, `task.create`.

### 4.9 One-time codes

`OneTimeCodes("payment_run")`, 10 minutes, 5 tries, 30 s, works once (the `otp` defaults); to the acting step person
(their login's email; a deputy gets their own); needed when the run total is above `payment_run_otp_above` (0.00:
every run with money; a run of 0.00 needs none). `approval_otp_above` (5,00,000, orders) is not used for runs (OP-2).
Advances, TDS and notes need no code.

---

## 5. Boundary cases and worked figures

Common: `demo reset apex`; business date pinned; role logins; Mailpit for codes. FY 2026-27. Payment calendar: Mon-Fri,
central holidays (24 Nov, 25 Dec 2026). RBI bank rate 5.75% from 7 Oct 2026. Each invoice reaches `approved` by the
normal flow (order, receipt or service confirmation, invoice); "planted" means the eval writes the row named.

| # | Case | Figures and expected |
|---|---|---|
| **Y1** Due date on a holiday | SUP-003 (NET30, MH): RM-09 100 x 85.00 = 8,500.00, CGST 765.00, SGST 765.00, total 10,030.00; invoice dated Wed 25 Nov 2026 | `due_date` Fri 25 Dec 2026 (Christmas), `pay_by` Thu 24 Dec. Run Wed 23 Dec window 0: not in it; run Thu 24 Dec window 0: in it. Run dated 25 Dec: 422 "the bank is closed on 25 Dec 2026 (Christmas Day)"; Sat 26 Dec: 422. Mutation: without `pay_by` the 24 Dec run misses it |
| **Y1b** Saturday after a holiday | SUP-006 (NET30): invoice dated Thu 26 Nov | due Sat 26 Dec, `pay_by` Thu 24 Dec |
| **Y2** MSME day 45 | SUP-002 (micro, agreement 45, NET45, GJ): RM-07 100 x 95.00 = 9,500.00, IGST 1,710.00, total 11,210.00; goods accepted Mon 2 Nov, invoice dated 2 Nov, received 3 Nov | due = min(2 Nov + 45, 2 Nov + 45) = Thu 17 Dec = `pay_by`. Run Thu 17 Dec: interest 0.00, payment 11,210.00, first in the run. Run Fri 18 Dec (day 46): interest 5.26 (11,210.00 x ((1 + 0.1725/12)^(1/30.4) - 1), half up), payment 11,215.26. Day 49 (Mon 21 Dec): 21.07 |
| **Y2b** DF-30 | `formulas` only: micro, no agreement, terms IMM, accepted 2 Nov, invoice 3 Nov | due 3 Nov (today: 17 Nov). Terms NET30: 17 Nov (deadline shorter) |
| **Y3** 194Q at the limit and rounding | Planted `cutover_supplier_ytd` SUP-005 2026-27 194Q 49,90,950.00. Invoice A (dated and received Mon 9 Nov): RM-08 10 KGS x 905.00 = 9,050.00, CGST 814.50, SGST 814.50, total 10,679.00. Invoice B (same day, booked after A): RM-08 15 x 905.00 = 13,575.00, CGST 1,221.75, SGST 1,221.75, total 16,018.50 | A: cumulative exactly 50,00,000.00, not above: TDS 0.00, row `confirmed` by rule. B: base 13,575.00 x 0.1% = 13.575 -> **13.58**, row `proposed` (section `393(1) Sl.8(ii) [194Q]`), B not payable until finance@ confirms; then net 16,004.92. Due Thu 24 Dec (9 Nov + 45). With SUP-005 `pan_status` inoperative: 5%, 678.75 |
| **Y4** 194J, mixed invoice, exactly at threshold | Planted ytd SUP-006 2026-27 194J(a) 38,000.00. Invoice C (dated Wed 25 Nov): SV-01 1 x 12,000.00 (SRV) + SP-02 10 x 140.00 = 1,400.00 (MRO); taxable 13,400.00, CGST 1,206.00, SGST 1,206.00, total 15,812.00. Invoice D (Thu 26 Nov): SV-01 12,000.00, total 14,160.00 | C: two rows, 194J(a) cumulative 50,000.00 not above: 0.00; 194Q 0.00; both by rule; net 15,812.00. D: 62,000.00 above 50,000.00: 2% x 12,000.00 = **240.00** (no catch-up), `proposed`; net 13,920.00 after confirmation (the dataset's PI-2627-000211 figures) |
| **Y5** Run integrity | Thu 24 Dec, window 0, Y1, Y3 (B confirmed), Y4 (D confirmed) approved. finance@ proposes R1 and, at once, R2 | R1: SUP-003 10,030.00; SUP-005 26,683.92; SUP-006 29,732.00; total 66,445.92. R2: 409 "nothing is due ..." or empty of R1's items; no invoice with two live allocations |
| **Y5b** Blocked invoice in a run | R1 proposed; the eval sets B `payment_block = 'match'` (standing for block 11 or a receipt reversal); director approves | at approval B taken out (`payment_run.item_removed` "blocked: ..."); SUP-005 payment 10,679.00; R1 approved for 50,441.00; code ref changed: a code sent before the block is refused, a new one works; B `in_run` null; B not in the file |
| **Y5c** Pending bank change | buyer@ asks to change SUP-003's account (proposal pending) after R1 approved | SUP-003 not in a new run ("bank change waiting for approval"); at R1's file SUP-003 taken out; file has two lines; approved change: unverified, never paid (built) |
| **Y5d** Rejected and lapsed | director rejects R1 "check SUP-006" | R1 `cancelled`; all payments `cancelled`; `in_run` cleared; a new run takes them. An approved run not filed by 18:30 on its date: `cancelled` "not filed on its date" |
| **Y6** Approver and code | R1 (66,445.92): rule PAY-01 v1 -> director@ | finance@ (proposer) 403; planthead@ and purchasemanager@ (hold `payment.approve`) 403 "this run waits for Meera Iyer"; director@ without code 409; wrong code "4 tries left"; right code: approved, step row with `code_id`; generic admin approve 409; one code mail to director@ only |
| **Y6b** Payer SoD | director@ asks for SP-01 5 x 210.00 = 1,050.00 + 189.00 GST = 1,239.00 from SUP-006; invoice approved and due 24 Dec | left out of R1 with "Meera Iyer asked for it and approves this run"; task `payment_left_out` for finance@; no run can pay it until a rule row or deputy names someone else |
| **Y7** Recorded, then settled | R1 file Thu 24 Dec. finance@ records SUP-003's payment: UTR `HDFC26122400000101`, value date 24 Dec, proof PDF. Statement Mon 28 Dec: three debit lines naming the payments, amounts exact, SUP-003's line with that UTR | after record: `recorded`, invoice `approved`, advice sent with the UTR; no budget actual; director@ 403. A record with neither UTR nor file 422; the same UTR on another payment 409. After the statement: all three `settled`, invoices `paid`, run `paid`, orders closed where complete; a second import: nothing changes. A line without UTR: `utr` stays empty, advice says the payment number |
| **Y8** Failed | Statement 28 Dec settles SUP-003 and SUP-006 only; Tue 29 Dec a credit line "NEFT RTN PAY-... BENEFICIARY A/C CLOSED" 26,683.92 | listed as "may be the bank returning PAY-..."; nothing applied; finance@ marks SUP-005's payment failed with that line: `failed`, A and B `approved`, `in_run` null, due again; return notice; task; a run Wed 30 Dec window 0 takes them again (26,683.92, no interest: not MSME). Reversal: SUP-003 settled, then reversed: invoice `approved`, negative budget row, order line reopened |
| **Y9** Advance | Order to SUP-001: RM-01 50 x 650.00 = 32,500.00, CGST 2,925.00, SGST 2,925.00, total 38,350.00, terms ADV30N30 (not touchless, D-23), acknowledged v1. Mon 30 Nov buyer@ requests 30% | gross 11,505.00, taxable 9,750.00; approver purchasemanager@ (ADV-01); buyer@ refused as approver; run Tue 1 Dec: advance first; director@ approves with code; settled Wed 2 Dec: TDS row 194Q 0.00 booked 2 Dec. Before settlement no TDS row; a cancelled run leaves no advance row. Invoice 38,350.00 dated 10 Dec: advance 11,505.00 (9,750.00) taken off, payable 26,845.00, TDS base 22,750.00 -> 0.00 |
| **Y9b** Cancel with an advance | as Y9, advance settled, supplier cannot supply; buyer@ cancels | 409 "an advance of 11,505.00 was paid on `<po>`: recover it before cancelling"; finance@ recovers: debit note 11,505.00 (`source_ref advance:<payment>`); finance@ refused as its approver, purchasemanager@ approves it (`invoice.approve` through the Approver role); cancel allowed; request `recovering`; netted in SUP-001's next run or refunded -> `recovered` |
| **Y10** Notes | SUP-004: unlinked credit note 1,000.00 + CGST 90.00 + SGST 90.00 = 1,180.00 entered by ap@; invoice PK-01 100 x 45.00 = 4,500.00 + 405.00 + 405.00 = 5,310.00 | ap@ cannot approve the note (403); finance@ approves; next run: SUP-004 payment 4,130.00 with -1,180.00. A 6,000.00 note: payment 0.00, settled at the file, 690.00 left (`offset_used` 5,310.00) |
| **Y10b** Recovery claim | block 9's R5 (SUP-002, RM-06 500 x 38.00, IGST 18%, invoice 22,420.00 booked and unpaid; 20 returned counted, no replacement) | claim RCL 760.00 + IGST 136.80 = 896.80 and, in the same step, a debit note `received` for 896.80 (`source_ref claim:<RCL>`, `ref_invoice_no` that invoice), its number in `debit_note_no`; finance@ approves it; the next run nets -896.80 against SUP-002's invoice; on settlement the note is `paid` and the claim `settled` |
| **Y11** Tenants | Apex R1 and a Brightwave run | Brightwave finance: Apex run 404; Apex code on a Brightwave run refused; no Apex TDS, advance or payment rows in Brightwave |

pytest-only boundaries: 194Q cumulative 49,99,999.99 + 0.01 -> 0.00, + 0.02 -> base 0.01, 0.00 after rounding; 194C
single sum exactly 30,000.00 not above; 194C payee P at 1%, C at 2%; lower certificate valid on its last day and over
its limit; `on_or_before` across 24-26 Dec; advance pro rata base with a remainder paisa.

---

## 6. Open points (RECOMMENDED defaults)

| # | Question | Options | RECOMMENDED and reason |
|---|---|---|---|
| OP-1 | Who approves a run at Apex (owner deciding with the menu questions) | a) director for every run; b) finance manager; c) finance manager up to a limit, finance manager then director above | **a**, as data `PAY-01 director` (Brightwave `cfo`): the 13-step plan and demo script say the director; the finance manager proposes and records, so cannot also approve. b and c need someone other than the finance manager to propose (the proposer is always left out of the chain); c is rows `PAY-01 0-L finance_manager`, `PAY-02 L+ finance_manager>director` using block 10's `finance_manager` approver role: chains are supported, no code change. Not NEEDS OWNER: the plan already says director (Plan l.155 "Finance ... approves" is read as the role that plans; l.168 says "run approver") |
| OP-2 | Code on every run or above a limit | a) every run with money (`payment_run_otp_above` 0.00); b) `approval_otp_above` 5,00,000 | **a**: Plan 2.7 and 13-step l.171 name no limit; the setting lets a company raise it |
| OP-3 | Calendar for `pay_by` and run dates | a) central holidays, Mon-Fri; b) the company's state too | **a**: banks close on central holidays; Saturdays are treated as closed (2nd/4th Saturday rule not modelled) |
| OP-4 | Due date on a non-working day | a) the working day before; b) after | **a**: never late, MSME deadlines are calendar days |
| OP-5 | Settlement per payment or per run | a) per payment; b) whole run (today) | **a**: a returned payment must not hold the others (AT-12) |
| OP-6 | What recording needs | a) value date and a UTR or a proof file (Q18); b) both | **a**: Q18's words; the advice waits for a reference |
| OP-7 | A credit line that looks like a return | a) listed; a person marks the failure; b) applied by itself | **a**: reopening an invoice for payment again must be a person's act |
| OP-8 | Which TDS rows need the tax owner | a) all but zero under the category's section; b) every row | **a**: D-16's judgement is the section and non-zero amounts; zeros by formula keep the demos moving; setting turns it off |
| OP-9 | 194C/194J threshold | a) whole invoice once the year is above the annual threshold or the order above the single-sum; no catch-up; b) catch up earlier sums | **a**, as the dataset's checker; tax adviser signs off (release gate 8) |
| OP-10 | TDS section of a line | a) item category, else supplier, else a person; b) supplier first | **a**: services by category (Plan 2.5) |
| OP-11 | TDS on an advance | a) when paid, on its taxable part (as built); b) on the gross | **a**; tax adviser |
| OP-12 | Who approves an advance | a) `approval_rule` `advance` rows: Apex purchase manager, Brightwave CFO, no code; b) the run approver | **a**: D-18 "own approval"; the run that pays it carries the code |
| OP-13 | Advance amount and count | a) up to the terms' percentage of the current total; one live request per order; b) any amount | **a**: the terms are approved with the order (Q19) |
| OP-14 | Unused advance | a) debit note recovery before cancel; netted or refunded; b) cancel allowed | **a**: AT-06 |
| OP-15 | Unlinked credit notes | a) second person approves; netted oldest first, never negative; rest carried; b) only linked (today) | **a**: DF-21 |
| OP-16 | Pending bank change | a) held out of new runs and taken out at re-check; b) paid to the old account | **a**: the supplier said the account is changing |
| OP-17 | Payer duties | a) requester, on-behalf-of, service confirmer, invoice approver, advance requester, proposer; items in conflict left out with a task; b) all five FR32 duties | **a**: Plan 2.7's words; b would stop the director paying any invoice whose request he approved (REQ-03, above 1 lakh), with nobody else in PAY-01 |
| OP-18 | Invoice approver | a) `invoice.approved_by` (block 10): who released its block, a bill's last approver (or its authoriser on an `auto` chain), else who entered or confirmed it; b) only a releaser | **a**: AP confirming an invoice is the approval when the match passes. Effect: a bill above 5,00,000 (NPO-03 director/CFO) or a Brightwave invoice whose price variance the CFO accepted (VAR-01) is left out of runs PAY-01 gives the same person; no Apex bill can reach NPO-03 today (`non_po_rule` limits 50,000 and 2,00,000); the Brightwave case is on the owner's list |
| OP-19 | Run dates | a) working day, not past; proposal expires at the end of the day; approved and unfiled lapses at 18:30 | **a**: interest and discounts were worked out for that day |
| OP-20 | Advice address | a) `accounts` contact, else the order contact; b) every contact | **a** |
| OP-21 | Mark paid in demos | a) removed everywhere; b) kept for demos | **a**: DF-01, DF-02; the walk-throughs settle by statement |
| OP-22 | Task 2.6 | a) block 13 (reads this block's columns); b) here | **a**, as the block order says |
| OP-23 | Who may reject or cancel | a) step people reject; proposer or Finance cancel before the file; b) any `payment.approve` holder (today) | **a** |
| OP-24 | Migration | a) 0190 on the head at merge | **a** |

No point needs the owner.

---

## 7. Pre-implementation check (Plan l.590-594)

### 7.1 Data

| Need | Apex | Brightwave | Enough? |
|---|---|---|---|
| Payment run rule, approver role | `PAY-01 director`, `approver_role director` | `PAY-01 cfo` | Yes (now enforced) |
| Advance rule | none | none | **Gap:** add `ADV-01,advance,0.00,999999999.00,ANY,ANY,ANY,ANY,purchase_manager,1,2025-04-01,` (Apex) and `... cfo ...` (Brightwave) |
| Advance terms | ADV30N30, ADV100 in `payment_terms`; no supplier on them | same | Yes (the scenario sets the order's terms) |
| Services with TDS | SV-01 (SRV 194J(a)), SUP-006 PAN `WESPW0000S` | SWS 194J(a), FAC 194C (SUP-005 pvt ltd 2%) | Yes |
| Micro supplier with agreement | SUP-002 45 days | SUP-004 45 days | Yes; none without agreement (DF-30 tested in `formulas`) |
| Section year-to-date | `cutover/supplier_ytd.csv` one taxable per supplier | - | **Gap:** add a `section` column (194Q; SUP-006 a 194J(a) row) |
| Holidays | 2026 central and state | same | **Gap:** add `data/reference/holidays_2027_central_gazetted.csv` |
| Settings | `manual_mark_paid` true | true | **Gap:** remove; add `payment_run_otp_above`, `tds_auto_confirm_zero`, `rtgs_from` |
| Dataset rule bug | `generator/finance.due_date` has DF-30 too | same | **Gap:** fix, regenerate, `--check` |
| Truth | `truth_payment.csv` | same | **Gap:** `truth/truth_payment_block12.csv` (`scenario,figure,value`) for Y1-Y10 by the generator's own arithmetic |

### 7.2 Configuration

Permissions `payment.record`, `tds.confirm`, `advance.request`, `advance.approve` (migration grants); autonomy `LOCKED`
additions; readiness: every company needs a `payment_run` and an `advance` rule naming a person who is not its only
Finance person, and separation coverage for recorder ≠ approver.

### 7.3 Today's behaviour (must pass before any change, on the branch with blocks 9 to 11)

`tests/test_payments.py`, `test_advances.py`, `test_bank_statement.py`, `test_payment_screens.py`, `test_proposals.py`,
`test_reconciliation.py`, `test_export.py`, `test_security.py`, `test_company_setup.py`, `test_web_shell.py`; evals E09,
E20, E21, E22, E26, E30, E32, E33, E34, E44, E45, E46, E52, E83, E86 and the walk-throughs E33, E34, E46.

**They change, each listed in the build report:** the invented-UTR test (`test_payments.py` l.574-589) becomes "no UTR,
no settlement"; mark-paid tests become record/statement tests; `test_bank_statement` whole-run settlement becomes per
payment; `evals/replay.py`, E21, E26, E86 settle the dataset's payments by importing its `bank_statement_line` rows and
approve runs through the run service with the code from the captured mail; E21's `goods_only` replay stays and a service
replay is added against `truth_payment.csv`'s 194C/194J rows; E33 records and settles instead of `/paid`; E34 and E46:
the approver opens the run page and takes the code from Mailpit, Brightwave's finance@ confirms the housekeeping 194C
row before the run; E09 adds the new permissions; E22 lists the new settings; `demo_cases.py` (paid case by statement,
filed case `in_file`). Also, each only where it pays or approves notes: E30 (its `#markPaid` click and the generic run
approval become the run page with the code and a statement import); E45 (`payment.mark_paid` in its replay becomes the
statement import); `evals/block3.py` (the payment helper of E86 and its neighbours); block 9's E147 (the claim now carries
a `received` debit note); block 10's E154 (the run paying N2: its 194J(b) row 7,500.00 confirmed by finance@, code,
statement) and E157 (a clean note waits `received` for a second person). Block 13 later changes E26, E30, E33, E34, E46
again (closure on settlement): their payment steps are this block's.

### 7.4 Gaps

Section 3; built in the order of section 9.

---

## 8. Evals E168-E175 (independent; written from this spec only)

Common: fresh `demo reset apex` (and `brightwave` for E175); business date pinned per scenario; role logins; Mailpit for
every code and advice; the oracle is the section 5 arithmetic in exact fractions (`Fraction`, half up to paise), the
holiday files, `tds_sections.csv`, the master CSVs and `truth/truth_payment_block12.csv`; never the platform's own
functions. Each ends with a tenant check and names two planted mutations it must catch.

| Eval | Given | When | Then | Oracle | Mutations caught |
|---|---|---|---|---|---|
| **E168** Due dates on the business calendar and the MSME deadline | Y1, Y1b, Y2 invoices approved | runs proposed on 23, 24 Dec (window 0), 17 and 18 Dec; run dated 25, 26 Dec | `due_date` and `pay_by` as Y1, Y1b; Y1 only in the 24 Dec run; 25 and 26 Dec refused with the words; Y2 first in its run, interest 0.00 on day 45 and 5.26 on day 46 | calendar days from the CSV dates; holidays file; interest formula in fractions with the rate file | `pay_by` ignored (due date used); interest counted from `pay_by` |
| **E169** TDS proposed by the platform, confirmed by the tax owner | Y3, Y4 planted year-to-date | invoices booked; due list read; finance@ confirms B and D; one change of section with and without a reason | rows per section with the Y3/Y4 figures; A and C zero rows confirmed by rule; B and D `proposed` and held back ("TDS to confirm") until confirmed; nets 16,004.92 and 13,920.00; change without reason 422; amount never typed | `tds_sections.csv` rows by date, payee by PAN letter, cumulative from the planted rows | 194J taxed at exactly 50,000.00; services given no TDS (DF-22) |
| **E170** One live run; blocks, bank changes and rejection release | Y5 invoices | two proposals at once (threads); Y5b block; Y5c pending bank change; Y5d reject | no invoice in two live runs; B taken out at approval, the old code refused; SUP-003 held and taken out at the file; rejected run `cancelled`, items due again; lapsed run cancelled | sums of the Y5 totals; the run must shrink, never grow | no lock (duplicate allocation); file without re-check |
| **E171** The run approver the rule names, with a code; the payer differs | Y6, Y6b | approve as finance@, planthead@, purchasemanager@, director@ (no code, wrong, right); generic admin approve; director's own request in the run | only director@ with the right code approves; step row; one code mail, to director@; Y6b left out with the words and a task; a deputy of director@ for the date (delegation) approves with their own code | `approval_rule.csv` and `approver_role` from the profile; requesters from the requests | any `payment.approve` holder approves; payer = requester accepted |
| **E172** Payments recorded with evidence, settled by the bank, never invented | Y7 | record (missing evidence, bad UTR, duplicate UTR, as director@, as finance@); `POST .../paid`; statement import twice; a line with no UTR | 410 on `/paid`; recorded is not paid; settlement per line; every `utr` equals a typed or statement value (no other value anywhere, including mail); advice once per payment with the right reference and lines; budget and closing only on settlement | the statement CSV written by the eval; the typed UTRs | invented UTR on a line without one; invoice paid at record |
| **E173** A failed or reversed payment reopens the invoice | Y8 | credit return line imported; failure marked; next run; reversal of a settled payment | nothing applied by the import alone; failed payment's invoices `approved` and due again; second run pays the same amounts; return notice; reversal reopens invoice, budget and order line | amounts from the Y3 figures; budget rows summed | failure leaves invoice `paid`; return applied without a person |
| **E174** Advances: own approval, payment, use, cancel, recovery | Y9, Y9b | request (over 30% refused), approvals (buyer refused), run with code, settlement, invoice, cancel, recovery | gross 11,505.00 and taxable 9,750.00; no TDS row before settlement; invoice payable 26,845.00; cancel refused with the words; recovery note; cancel then allowed; `recovered` after netting or refund | `payment_terms.csv`, order arithmetic in fractions | advance paid without approval; cancel allowed with a paid advance (DF-17) |
| **E175** Credit and debit notes; payments stay in their company | Y10, Y10b, Y11 | note approvals; the claim's note; runs; Brightwave reads Apex runs; Apex code on Brightwave | enterer refused; netting 4,130.00; 0.00 payment settled at the file, 690.00 left; never negative; the claim's note 896.80 named on the claim, netted, claim `settled` on settlement; Brightwave 404s, no rows | note and invoice arithmetic | credit used twice; negative payment written |

---

## 9. Done criteria (Plan l.614)

- `python scripts/check.py` green (ruff, mypy strict, pytest, biome, tsc), browser tests included.
- E168-E175 and every existing eval pass with the scripted model; one results file.
- Both demo companies reset; walk-throughs E33, E34, E46 pass with the code step and statement settlement.
- Migration 0190 run; `scripts/gen_schema_doc.py`; `docs/database-schema.md` current; data dictionary rows (4.1).
- Docs: `docs/demo-script.md` steps 15-16 (code from Mailpit, statement), `docs/guides/finance.md` (record, failure,
  TDS, advances), `buyer.md` (advance request), `approver.md` (run page with code), CLAUDE.md "Where things are"
  (payment rows: `invoices/runs.py`, `settlement.py`, `tds.py`, `advances.py`, `notes.py`) and the latest migration.
- No defect DF-01, DF-02, DF-05, DF-17, DF-21, DF-22, DF-30 left open; AT-06, AT-12, AT-13 pass.
- Audit rows for every write (4.8); money in Decimal, paise half up; no model call anywhere in this block.

### 9.1 pytest (scenario names; given, when, then)

`test_payment_formulas.py` (due date, DF-30, `pay_by`, `tds_for` boundaries of section 5, `utr_ok`, `offset`),
`test_tds_confirmation.py` (Y3, Y4, change of section, advance TDS when paid), `test_run_integrity.py` (Y5-Y5d,
concurrent proposals, lapse), `test_run_approval.py` (Y6, Y6b, chains of two, deputy, recorder ≠ approver in a company
where the approver holds `payment.record`), `test_settlement.py` (Y7, Y8, reversal, zero payments, advice words),
`test_advances.py` (extend: Y9, Y9b), `test_notes.py` (Y10, Y10b), `test_tenant_isolation.py` (Y11), an agent-registry test
(12.10.2).

---

## 10. Where the plan and the code disagree

1. A UTR is invented when none is given (`payment.mark_paid` l.625) and mailed; a test asserts it (DF-01).
2. Both demo companies switch `manual_mark_paid` on, so the walk-through E33 settles by hand (DF-02).
3. The dataset's `PAY-01` rows name the director and the CFO; no code reads them: plant head and purchase manager hold
   `payment.approve` through the Approver role and can approve any run.
4. A rejected run keeps its invoices and advances forever; two proposals can take the same invoice (DF-05).
5. Payment reads (`/payment-runs`, `/due`, `/payment-runs/{no}`) need no permission.
6. Advance TDS is written at proposal and counted in the year's cumulative though never paid; an order with a paid
   advance can be cancelled (DF-17).
7. Credit and debit notes are approved at entry by nobody; debit notes are never netted (DF-21).
8. Services get no TDS; mixed invoices get none (DF-22); one TDS row per invoice by index.
9. Without an MSME agreement the due date is the 15-day deadline even when the terms are shorter (DF-30); the dataset's
   generator has the same rule.
10. Due dates and run dates ignore holidays; the RTGS limit is a constant (rule 8).
11. Plan l.155 says Finance "approves" payments while the 13-step plan and the demo say the director: this spec follows
    the director as data (OP-1).
12. CLAUDE.md says the latest tenant migration is 0055; the worktree is at 0150.

## 11. Build order

1. **Data first:** advance rule rows, `supplier_ytd` section, 2027 holidays, settings, DF-30 in the generator,
   `truth_payment_block12.csv`; `--check`.
2. Migration 0190, backfill, schema doc, data dictionary.
3. `formulas/payment.py` and `calendar.on_or_before` with boundary tests.
4. TDS (12.5), due dates (12.8).
5. Runs: lock, live guard, re-check, cancel, lapse (12.2); approval chain, code, SoD (12.1).
6. States, record, settlement, failure, reversal, advice (12.3, 12.4, 12.9); remove mark paid.
7. Advances (12.6), notes (12.7).
8. API, screens, exceptions, demo cases, docs; update the tests and evals of 7.3.
9. Evals E168-E175 by a separate agent from this spec; one full `scripts/check.py` and all evals; both demo resets and
   walk-throughs (E33, E34, E46).
