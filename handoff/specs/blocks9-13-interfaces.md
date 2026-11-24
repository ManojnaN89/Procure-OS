# Blocks 9-13: interfaces between the specs

Blocks are built strictly in order **9 → 10 → 11 → 12 → 13**, each on the one before. An earlier block never calls a
later one: the later block adds its calls into the earlier block's code paths and owns that integration. Migrations
chain 0150 → **0160** (9) → **0170** (10) → **0180** (11) → **0190** (12) → **0200** (13).

## 1. Cross-block contracts

| Thing | Owner (builds it) | Extended or called by | Names |
|---|---|---|---|
| Recovery claim record | 9 (raised from a counted return, `no_replacement`, invoiced beyond received) | 12 adds the debit note call in claim raising and settles the claim | table `recovery_claim` (`debit_note_no`, `status` open/settled/cancelled); `receiving/returns.py`; `notes.raise_debit_note`, `source_ref claim:<RCL>`; audits `claim.raise` (9), `claim.settle` (12) |
| Proposal end hook | 9 (rejected excess back to `waiting`) | 12 (`payment_run.approve`), 13 (`po.short_close`) register | `platform/proposals.closer(action)`, run on `rejected` or `expired` |
| Re-match when accepted quantity rises | 11 | 11 adds every call: `goods.post_receipt`, `goods.inspect`, `services.confirm`, 9's `receiving/excess.py` (after commit) and `erp_receipts.import_receipts` | `invoices/rematch.after_accepted(db, po_no, line_nos, actor)`; responses add `rematched` |
| Line lock in the match | 11 | 9's reversal already locks `po_line for update`; the two exclude each other from block 11 on | lock order: invoice row, then `po_line` by `(po_no, line_no)` |
| Invoiced quantity | existing | 9 (reversal refusal, claim qty), 11 (invariant), 13 (blockers) | `po_line.invoiced_qty` = qty on invoices `approved`, `part_paid`, `paid` (`BOOKED`) |
| IGST against CGST plus SGST | 10 (`header_problem`, reason `tax_head`, kind `tax`) | 11 calls it unchanged; 11's per-line check does rate and amounts only, and skips amounts when `tax_head` is present | `formulas/matching.header_problem`, `judge_gst` |
| Bill never released by proposal | 10 (refusal added to today's `invoices/service.request_release`) | 11 moves `request_release` to `invoices/release.py` and keeps it | 409 |
| Invoice approver | 10 (column in 0170, set for bills and clean matches) | 11 sets it on a release; 12 checks the payer against it | `invoice.approved_by` |
| Invoice reads | 10 (10.11) | 11 adds the diagnosis read to the same check | `invoice.view`, `invoice.book`, `invoice.match`, `invoice.approve`, or the bill's authoriser and step approvers |
| Note approval | 10 (interim: clean note `approved` at entry; header checks) | 12: a clean note waits `received` for a second person, note branch added to 10's endpoint | `POST /api/invoices/{no}/approve`; audit `invoice.note_approve` |
| Bill hooks in payment | 10 (`payment.accepted_on`, `budget_ledger.post_payment` for bill lines) | 12 replaces 10's `_goods_only` with TDS per line (bill line's own `category_code`) | `invoice_line.category_code`, `cost_centre_code`, `gl_code` |
| Cases | 11 (`task.case_key`, sweep) | 9's and 10's task kinds listed in 11.6.1; 12's and 13's join through `exceptions.collect` (H-6) | 9: `receipt_excess`, `return_outcome`, rows `inspection`, `return_draft`; 10: `invoice_authorise`, `invoice_approve`, `invoice_sender_check`; 12: `payment_left_out`, `payment_failed`; 13: `po_short_close`, `gstr2b_differences` |
| Held stock rows | 11 | excludes holds 9 already covers | hold source `excess`, receipt quarantine waiting inspection, hold on a draft return |
| Settled state | 12 | 13 reads it | `payment.status = 'settled'` (bank line), invoice `paid` only then; audits `payment.settle`, `payment.fail`, `payment.reverse` |
| Order closure | 13 (`orders/closure.check`, `closure.reopen`) | replaces 12's two call sites in `invoices/settlement.py` (`close_case` on settlement, reopen on reversal); adds calls after 9's reversal, return dispatch and outcome, excess decisions | `close_case` removed; audits `po.close`, `po.reopen` |
| Closure blockers from 9 and 12 | 13 | reads | `return_open` (return `draft`, or dispatched with an order-line outcome unset), `excess_open` (`receipt_excess` waiting, proposed, returning), `claim_open` (claim `open`), `credit_open` (note not `paid`/`cancelled`), `advance_open` (`advance_request` approved, in_run, paid, recovering), `invoice_open`, `payment_failed` |
| Open quantity after a return | 9 lowers `received_qty` at a counted dispatch | today's `close_balance` closes it; 13 replaces that with short-close by risk | line status `short_closed` (13); 9's `replace` refused on it |
| Received not invoiced | 9 adds the reversal and counted-return filters to `received_vs_invoiced` | 13 freezes it at month close | `invoices/reconciliation.py` |
| Readiness separations | 9 SEP-16 inspection; 11 SEP-17 price variance; 12 SEP-18 recorder, SEP-19 payer | | `platform/readiness.py` |
| Approver roles and rules | 10 adds approver role `finance_manager` and `non_po_invoice` NPO-01..03 | 12 OP-1 option c reuses `finance_manager`; 12 adds `advance` ADV-01; 13 adds `short_close` SC-01..03 | `approval_rule` documents; existing `price_variance` VAR-01, `payment_run` PAY-01 |

New names checked for clashes (none found): settings `non_po_duplicate_window_days` (10), `case_due_days` (11),
`payment_run_otp_above`, `tds_auto_confirm_zero`, `rtgs_from` (12), `ppv_recognition`, `accounts_export_format` (13);
tolerance keys `tax_line_abs` (11), `gstr2b_diff_abs` (13); permissions `grn.inspect` (9), `payment.record`,
`tds.confirm`, `advance.request`, `advance.approve` (12), `price.standard`, `accounts.export`, `gst.reconcile` (13);
proposal actions `receipt.excess_accept`, `receipt.reverse` (9), `invoice.release_block` (existing, 11), `advance.approve`
(12), `po.short_close`, `price.standard` (13). People and masters: U11 quality in both companies (9, the only new
person); suppliers SUP-010, SUP-011 (Apex), SUP-007 (Brightwave), categories UTL, PRF, TEL, Apex cost centre CC-ADM (10).

## 2. Decisions made while resolving

1. **Recovery claims and debit notes:** one table, block 9's. Block 9 cannot call block 12, so block 12 adds the
   `raise_debit_note` call to claim raising and the `settled` status. Block 12 gains scenario Y10b and E175 checks it.
2. **Tax head:** one owner, block 10 (built first; the check needs only the two GSTINs on the header). Block 11's 3.4
   keeps rate and amount per line. Its D4 and G (and B13, E161) now expect `tax_head` with block 10's words, quoted in
   block 11 so its eval stays self-contained.
3. **Re-match:** block 9 promises no call (its text says so); block 11's H-1 now adds the calls to block 9's paths. The
   "concurrent reversal and match" test moved from block 9 to block 11 (it only holds once the match takes the line lock).
4. **Counted return after invoicing:** block 11's invariant "invoiced never exceeds accepted" names this one exception;
   block 13's `invoiced_not_received` subtracts the line's claimed quantity, so a claimed line can still close.
5. **Closure:** block 12 leaves the settled state, its events and two call sites; block 13 replaces them. Block 13's
   "until block 12 lands" interim is removed (block 12 is always built first). A receipt reversal never meets a closed
   order (block 9 refuses it), so block 13's reopen covers notes, failed or reversed payments and counted returns.
6. **Invoice approver:** block 10 owns `invoice.approved_by` (it builds the first approval beyond the match). The payer is
   a step person of the run (the run approver), as block 12 says; the recorder is a separate duty. Block 12 keeps Plan
   2.7's list (requester, on-behalf, service confirmer, invoice approver, advance requester, proposer), not the request
   approver, so the director can still pay what they approved as a request (block 12 OP-17).
7. **Large bills at Apex:** no rule makes them unpayable today. NPO-03 (director) equals PAY-01 (director), but Apex's
   `non_po_rule` limits (50,000 UTL, 2,00,000 PRF) keep every bill below NPO-03. Block 12's readiness warns when a
   `payment_run` role is also the only role of a `price_variance` or `non_po_invoice` band.
8. **Note approval:** block 10's "approved at entry" is the interim; block 12 replaces it on the same endpoint.
9. **Proposal end hook:** block 9 needs it first (a rejected excess acceptance), so block 9 owns `closer`; block 12 no
   longer adds it.
10. **Excess and price variance** do not clash: `receipt.excess_accept` raises a quantity and goes to the `po` band's
    approver for the new total (block 9); `invoice.release_block` accepts a price and goes to VAR-01 (block 11).
11. **"No replacement":** block 9's dispatch lowers `received_qty`, reopening the balance; until block 13 the buyer
    closes it with today's **Close the balance**; from block 13, by short-close by risk. Goods returned this way still
    count as "received" for block 13's "nothing received: cancel instead" refusal.
12. **Small fixes:** block 13's return words use `RTN`, not `RTV`; block 11's SEP-16 became SEP-17; block 12's SEP rows
    numbered 18-19; down_revisions fixed in blocks 11 and 13; PPV at receipt is reversed by a receipt reversal or counted
    return (block 13 R6.1); "passes on p3-b8" became "on the branch with the earlier blocks" in blocks 10-12.

## 3. Existing evals and tests each block updates

| Eval or test | Block 9 | 10 | 11 | 12 | 13 |
|---|---|---|---|---|---|
| E09 permissions | `grn.inspect`, Quality | | | new permissions, `/paid` 410 | short-close of a pegged line |
| E18, E43, E68, `evals/block0d.py` | inspection, excess, serials, RTN | | | | |
| E19, E25, E55 | | | both tests, causes, truth regenerated | | |
| E21, E22, `evals/replay.py` | | | | settle by statement, settings | |
| E26 | | | | advance request and approval | closes on settlement |
| E27 | | gate locked | | | |
| E29, E71, E87, E88 | | | | | savings, short-close, month close |
| E30 | | | | run page with code, statement (no `#markPaid`) | closes on settlement |
| E33, E34, E46 walk-throughs | quality@ inspects (E33, E34) | | named price approver (E34) | code from Mailpit, statement, TDS confirm | closure on settlement |
| E44 exceptions | new rows if raised | new rows if raised | case fields, new kinds | | |
| E45 | | | | `mark_paid` in its replay becomes the statement | unchanged |
| E86 (and `evals/block3.py`) | inspection by quality@ | | | payment helper by statement | pegged short-close to U08 |
| E147 (block 9's) | | | | claim carries a debit note | |
| E154, E157 (block 10's) | | | | run with TDS confirm, code, statement; note waits `received` | |
| E172, E173 (block 12's) | | | | | close and reopen through `orders/closure` (same statuses, `po.reopen`) |
| Tests | `test_receiving`, `test_service_entry`, `test_stock_*` | `test_invoices` (key), `test_invoice_reading`, `test_mailbox_settings` | `test_invoices` (both tests), `test_tolerance_versions`, `test_exceptions`, `test_invoice_exceptions_agent`, `test_demo_populate` | `test_payments`, `test_advances`, `test_bank_statement`, `test_payment_screens`, `test_reconciliation` | `test_order_changes`, `test_budget_engine`, `test_payments` |

`demo_cases.py` changes in every block. No two specs give contradictory instructions for one eval: each later change
builds on the earlier one, in block order.

## 4. For the owner

| # | Point | Interim each spec builds |
|---|---|---|
| 1 | **Block 10 OP-13 (NEEDS OWNER):** may the platform enter a clean invoice reading without AP? | `invoice.read` locked: AP confirms every invoice (D54); auto entry removed; E27 changed |
| 2 | **Block 11 OP-2:** an invoice price variance is accepted only by the person `price_variance` names (Apex purchase manager, Brightwave CFO), never an order maker | Finance no longer accepts price variances; it still releases tax and arithmetic blocks |
| 3 | **Block 11 OP-3:** a quantity or service hold is never released | cleared only by a receipt, a confirmation or a corrected invoice |
| 4 | **Block 12 OP-1 and OP-2:** the run approver is the director (Apex) / CFO (Brightwave) from PAY-01, with a one-time code on every run | as data; option c (finance manager up to a limit) needs only rule rows |
| 5 | **Block 13 OP-1:** short-close limits: auto to Rs 25,000 incl. GST, purchase manager, director above Rs 5,00,000 (Brightwave CFO above 25,000) | as data, to validate with the client |
| 6 | **Found: Brightwave's CFO** is VAR-01, PAY-01 and the owner of CC-ADM. An invoice whose price variance the CFO accepted, or a CC-ADM bill up to 50,000 the CFO authorised, is left out of every run by the payer check (rule 7) | left out with a `payment_left_out` task; a deputy for the date may approve the run. Options: a second run approver at Brightwave, or another VAR-01 person (changes block 8's supplier price-rise approver) |
| 7 | **Found: Apex NPO-03 = PAY-01 (director):** a bill above Rs 5,00,000 could never be paid by the director | unreachable today (`non_po_rule` limits); readiness warns if a limit is raised |
| 8 | **Behaviour changes to note:** block 12 removes Mark paid in the demos (DF-01, settlement by statement only); block 12's payer check leaves out the request approver from rule 7's list (OP-17) | as specified |
