# Block 13 spec: reconcile and close (flow step 13)

*Cross-block contracts with blocks 9-12 (all built before this one): `handoff/specs/blocks9-13-interfaces.md`.*

Tasks **6.6** (D-13 short-close by risk with the unmet need sent back; D-12 two-track closure), **9b.1** (D-19 monthly
GSTR-2B comparison, no holding), **13.2** (accounting PPV at the client's recognition point, D47, O3), **13.3** (contract
variance, market gap and saving kept apart; DF-29) and **2.6** (accounting export for payables, payments and advances),
plus the gaps left in the reconciliation built in P1-39 and the month-end close the release gate asks for. Spec only;
nothing here is built yet. This is the last flow block: section 14 lists what the Phase 1 walk-through must show.

**Not in this block:** receipt reversal, inspection by a second person, quarantine of excess, return documents, external
receipts (block 9: 3.6, 3.8, 4.2, 4.3, 4.5); invoices without a PO and the duplicate key (block 10: 9a.1, 3.3); match
rules, GST per line, quantity re-match, cases (block 11: 3.1, 3.3-3.5, WP-5); payment states and settlement evidence, run
integrity, advances, credit and debit notes, recovery claims, TDS on services, MSME no-agreement terms (DF-30), the run
code (block 12: 2.1-2.5, 2.7, 2.8): this block only **reads** their results (section 4); the PPV watch agent (11b.6,
Across); holding GST on a GSTR-2B mismatch (D-19 later part, legal review); MSME-1 and the statutory registers (P2-07);
AI month-end accruals and the reconciliation helper (Phase 2, 13-Step Plan "Closing"); Tally or ERP connectors (Phase 5).

**Sources.** `planning/P2P-Project-Plan-End-to-End.md` (**Plan**): l.80-81, l.287 (P1-39), gap tables l.368 ("Short-close
by risk, need sent back: Not built, DF-26") and l.393-408 (year end D-09, two-track closure D-12, PPV D47), WP-6 l.450,
WP-9b l.454, WP-13 l.458, tasks l.496 (2.6), l.523-524 (6.5, 6.6), l.537 (9b.1), l.559-561 (13.1-13.3), block order
l.596-611, "Every package ends when" l.614-618, AT-14 l.822, release gates l.825-835, DF-26 l.872, DF-29 l.877, D47 l.1050,
D52 l.1055, D-09 to D-19 l.1073-1083, O3 l.1096, MSME l.1132. `P2P-Plan-Agentic-Revision.md` (**Rev**): s8 l.378-407 (four
measures, PPV timing), #7 l.513 ("minimum-volume shortfalls are tracked (D-13)"), D-12 l.550 ("supply complete + money
settled"), D-13 l.551, D-19 l.557, D54 l.433-449. `P2P-Phase1-Pilot-Baseline.md` (**Base**): A-9, E-1 to E-3 l.102,
l.119-121, DF-26 l.220, task 6.6 l.708 ("advances, credits, returns and failed payments block financial closure"), WP-9b
l.514-521, WP-13 l.566-576. `P2P-Screens-Brief.md` l.87, l.193, l.236, l.240-244, l.277. `P2P-13-Step-Plan.md` l.74,
l.149, l.172. `Decisions-22-Nov.md` #7, #14. Specs: block 2 l.8, block 8 l.7, block 3 (D-09 locks and year end), block 0b
OP-19 ("version the standard price in 13.2"). **Code read:** worktree `p3-b8` at 8ac2221 (blocks 0 to 8; tenant
migrations to 0150, evals to E143).

*RECOMMENDED* marks a default this spec proposes because the documents are silent. **NEEDS OWNER** marks a point that
would change an approved decision; there is none in this block (section 12).

---

## 1. What the decisions say

| Source | Text |
|---|---|
| D-12 (Rev l.550) | "Two-track closure (supply complete + money settled)": Confirmed |
| D-13 (Rev l.551, #7) | "Short-close approval by risk": Confirmed; "minimum-volume shortfalls are tracked (D-13)" |
| Plan 6.6 / Base l.708 | "D-13 short-close by risk with the unmet need sent back; D-12 two-track closure; advances, credits, returns and failed payments block financial closure" |
| DF-26 | "Short-close needs no approval and checks no open invoices, advances or demand; the PO counts as closed before the money is settled" |
| AT-14 | "Balance short-closed while production still needs it: second person by risk; need returns to the store check; PO financially open until settled" |
| Screens l.193, l.241, l.277 | "closing a part-delivered order needs a second person by risk; the unmet need goes back to the store check"; "an order closes only when supply is complete and money is settled"; short-close screen "Order, open balance, risk, unmet need" |
| 9b.1, D-19, screens l.242 | "monthly GSTR-2B comparison from an uploaded file"; "no holding"; "differences listed (no holding of payment in Phase 1)" |
| 13.2, Rev s8, O3 | Accounting PPV "(actual unit purchase price − standard unit price) × quantity. Standard = the version in force at the recognition point"; recognition "goods receipt at the PO price, or invoice match at the invoice price, or both with a true-up"; "price basis (net of recoverable GST)"; O3 "client finance confirms"; gate 8 "finance signs off the PPV recognition point" |
| 13.3, Rev s8, DF-29 | Contract variance "PO or invoice price vs the effective contract version"; market gap "effective contract price vs a verified, fresh benchmark (D52)", evidence only; saving "Potential: from a quote or market gap. Realised: only from actual prices on matched invoices"; "none is shown as another"; DF-29 "Saving KPI uses the earliest quote from any supplier as the baseline" |
| 2.6, Base C-3 | "Accounting export for payables, payments, advances", "Format per client"; "No journals or GL export" |
| D-09 (block 3, built) | "Year-end close, period locks, adjustments" |
| P1-39, screens l.240 | Reconciliation: order trace, received not invoiced as of a date, small suppliers against the MSMED Act deadline, supplier statements, bank lines a person confirms (built) |
| Plan docx, 13-Step l.149 | "A year-end alert for small-supplier invoices near 31 March"; "Month-end accrual report (received, not invoiced)"; "Ledger export to Tally and common ERPs" |
| Gate 9 (l.835) | "The product owner signs off after the pilot client rehearses cutover and one full month-end" |
| Decision 14 | "The AI capability of a block is built with that block" |

Silent on: the risk measure and its limits (OP-1, OP-2), partial short-close (OP-3), goods arriving while a short-close
waits (OP-4), what "money settled" reads (OP-7), a document after closure (OP-6), the 2B file, key and rounding (OP-8 to
OP-11), the PPV defaults (OP-12 to OP-14), the saving baseline (OP-15), what a month close blocks on (OP-17 to OP-19), the
export format and dates (OP-20 to OP-22), statement balances and periods (OP-23, OP-24), bank lines that are not ours
(OP-25), the MSME year end (OP-26).

**What 2.6 is and why it is here.** 2.6 is a file of journal vouchers the client's ledger (Tally or an ERP) imports: the
purchase invoices booked (payables with GST input and TDS), the payments the bank settled, advances paid and adjusted, and
the month-end accrual for goods received not invoiced. It writes nothing back and decides nothing; payment works without
it. It sits in close because a ledger needs each period's vouchers **once and final**: the export of a month is frozen
when the month is closed, and a document that arrives later goes into the next open month. Building it in block 12 would
mean a second freeze rule later. ERP pilots (model C) drop it with WP-2 (Plan l.462).

---

## 2. What exists today

| Part | Where | Behaviour |
|---|---|---|
| Short-close | `orders/changes.close_balance` (l.268-322); `POST /api/purchase-orders/{no}/close-balance` (`platform/order_api.py` l.232, buyer) | One person and a reason close every open line's balance at once: line `status = 'closed'`, `short_closed_qty`; order `closed` when no line is open; `po.closed_reason`, `closed_at`; audit `po.close_balance`; supplier mailed, requester and buyer told; then `material.after_event` re-runs the store check for the pegged item-locations (6.5, block 2; E71). Refused when nothing was received. **No approval, risk, advance, invoice or demand check** |
| Closing on payment | `invoices/payment.close_case` (l.659) from `mark_paid` | A line closes when received ≥ ordered, invoiced ≥ received and none of its invoices is received/blocked/approved; the order when all lines are closed or cancelled; audit `po.close`; `_notify_closed` "Order X is paid and closed" (E26, E30, E34, E46 read it). Ignores advances, credit notes, a line received short and not short-closed (never closes) and short-closed lines (already closed) |
| Statuses | migration 0019; `formulas/receiving.order_status` | `po_line`: open, part_received, received, closed, cancelled. `po`: ... part_received, received, closed, cancelled |
| Budget | view `budget_use` (0090) | Counts an order line until it is `closed`: a short-closed non-stock line received but not yet paid drops out of the commitment today |
| Order trace | `invoices/trace.chain` | Production order to payment; order rows show "still to receive" and "received, not invoiced"; no closure state, no short-close decision, no GST check |
| Received not invoiced | `invoices/reconciliation.received_vs_invoiced`, `formulas/reconciliation.position` | Per order line on any date, both directions, aged first in first out; computed live, never kept; E45 against `truth_grni.csv` |
| MSME | `reconciliation.msme_due`; `formulas/payment.msme_deadline`, `msme_interest` | Unpaid micro and small invoices past or within `msme_warn_days`; interest is worked out only inside a payment run; no year-end view |
| Supplier statements | `invoices/vendor_statement.py`, `formulas/reconciliation.match_statement` | Period given, else the first and last date on it; ours = documents dated in it and received by its end, payments paid in it, plus any document their lines name. A payment line dated outside the period finds nothing ("no payment of ours ..."). Results kept; a difference can never be marked explained |
| Bank lines | `invoices/bank_statement.py` `import_statement`, `open_lines`, `confirm` | Statuses matched, waiting, open, confirmed; confirm refused to the run's approver. A debit that is no payment of ours (bank charges; E34 plants `CHG000001`) stays `open` for ever with its `bank_statement_lines` task |
| Month locks, year end | `approvals/budget_periods.py`, `budget_ledger.py`, `platform/budget_api.py`, control tower budget panel | Finance (`budget.manage`) locks an ended month, in order, with a reason, never undone; a late actual goes to the next open month keeping `event_period`; `close_year` from 1 April locks the rest and keeps the carried commitments. Datasets lock 2025-11 to 2026-10 ("Month-end close"); 2026-11 is the first that can be locked (E87, E88) |
| Prices | `item.standard_price` (one column, block 0b OP-19); `market_benchmark`, `formulas/benchmarks`, `formulas/price_gap`; `po_line.source`, `price_record_id`; `request_line.est_price`, `est_source` | No PPV or variance record. `budget_ledger` leaves price differences out ("that is purchase price variance, task 13.2") |
| Saving KPI | `platform/control_tower.kpis` l.311-330, `formulas/kpis.saving` | First quote received (any supplier) less the awarded price (DF-29); E29 recounts it |
| GSTR-2B | none | `invoice` has `supplier_gstin`, `buyer_gstin`, `type`, `taxable`, `cgst`, `sgst`, `igst`, `cess`, `reverse_charge`, `fy`, `irn` |
| Accounting export | none (`platform/export_api.py` exports lists) | `gl_account`: 1510-1530 GST input, 1610 advances, 2110/2120 payables (MSME / others), 2210 TDS, 2220 RCM GST, 2310 GR/IR |
| AI | none on this step | Assistant (AG04) read only |

---

## 3. Pre-implementation check (Plan 9.3)

### 3.1 Data

| Need | Apex Pumps | Brightwave | Enough? |
|---|---|---|---|
| Part-received contract order, a line pegged to a production need | CNT-0001 (SUP-001, RM-01..05), MO-1 castings via the store check (demo script) | CNT-0001, joiner kits | Yes (made live, section 9) |
| Short-close approval rows | none | none | **Gap** |
| An order with an advance | `ADV30N30` payment terms | `ADV30N30` | Yes |
| Micro supplier with an agreement | SUP-002 (micro, 45 days) | SUP-004 (micro, 45 days) | Yes |
| GSTR-2B files | none | none | **Gap** |
| Our GSTINs | 27APXCA0000P1ZL (default), 24APXCA0000P1ZR | 29BRTCB0000T1Z7, 36BRTCB0000T1ZC | Yes |
| Standard price versions | `item.csv` one price each | one each | **Gap** (no second version) |
| Benchmarks against a contract | RM-08 only (no contract on RM-08) | LP-01 exactly 5.00% below CNT-0001 on 31 Oct 2026 (block 0b) | Yes (market gap on Brightwave) |
| Ledger accounts | `gl_account.csv` | same | Yes; **gap**: no map from purpose to account |
| Supplier statements | `history/vendor_statement_line.csv` (SUP-003, Jan 2026) | SUP-004, Mar 2026 | **Gap**: none with a period other than its dates |
| Bank lines not ours | `history/bank_statement_line.csv` "BANK CHARGES" | same | Yes |
| GRNI answer key | `truth/truth_grni.csv` | same | Yes |

**Generator (data first):**
1. `master/approval_rule.csv`, document `short_close` (version 1, from 2025-04-01, all ANY): Apex `SC-01` 0.00-25,000.00
   `auto`, `SC-02` 25,000.01-5,00,000.00 `purchase_manager`, `SC-03` 5,00,000.01-999999999.00 `director`; Brightwave
   `SC-01` 0.00-25,000.00 `auto`, `SC-02` 25,000.01-999999999.00 `cfo` (OP-1).
2. `master/item_standard_price.csv` (`item_code,price,version,effective_from,effective_to,reason`): version 1 of every item
   from 2000-01-01 (today's prices); Apex RM-08 version 2 950.00 from 2026-12-01 (v1 ends 2026-11-30); Brightwave LP-01
   version 2 at 2% above version 1 from 2026-12-01.
3. `master/ledger_map.csv` (`purpose,gl_code,ledger_name,version,effective_from`), both: `payable_msme` 2110,
   `payable_other` 2120, `gst_input_cgst` 1510, `gst_input_sgst` 1520, `gst_input_igst` 1530, `rcm_payable` 2220,
   `tds_payable` 2210, `advance` 1610, `grni` 2310, `freight_inward` 5220, `round_off` (no GL, "Round Off"),
   `msme_interest` (no GL, "Interest on delayed payment to MSME"), `bank:CBA-01` (no GL, "HDFC Current A/c 0201").
4. `master/tolerance.csv`: key `gstr2b_diff_abs` 1.00 ("GSTR-2B and our books agree when each tax head and the taxable
   value differ by at most this") in the version in force (both).
5. `master/setting.csv`: `ppv_recognition,invoice,...`; `accounts_export_format,generic_csv,...`.
6. `gst/gstr2b_<gstin>_<yyyymm>.json` in the portal's shape (section 5 R4.1) for each history month of the default GSTIN,
   from `history/invoice.csv`, with these planted in **2026-10** (Apex) and **2026-09** (Brightwave): G1 a plain match; G2
   taxable ₹100.00 higher in 2B; G3 CGST ₹0.50 higher (within tolerance); G4 an invoice of ours left out, filed in the
   next month's file; G5 a document only in 2B (an invoice we never received); G6 `itcavl` N, reason "P" (place of
   supply); G7 the supplier's other GSTIN (same PAN, state 24); G8 the same document twice; plus a 2026-11 (Apex) and
   2026-10 (Brightwave) file holding only G4. Brightwave adds an IGST case.
7. `history/vendor_statement_period.csv` (Apex SUP-003): a statement with period 1 Feb-28 Feb 2026 carrying one January
   invoice and one payment dated 2 Mar 2026.
8. Truth by the generator's own arithmetic: `truth/truth_gstr2b.csv` (`gstin,period,doc,outcome,field,ours,theirs`),
   `truth/truth_price_measures.csv` (history: every receipt and invoice line, PPV for each of the three recognition
   points, contract variance, saving), `truth/truth_accounting_export.csv` (Oct 2026 and Sep 2026 vouchers by ledger).
9. `--check`: every `short_close` band contiguous; standard versions do not overlap; each 2B file's documents trace to
   history invoices except G5.

### 3.2 Configuration

New versioned data: `short_close` rows of `approval_rule` (existing table and versioning), `item_standard_price`,
`ledger_map`, tolerance `gstr2b_diff_abs`. Settings `ppv_recognition`, `accounts_export_format` (D-22 changes, as every
setting). Code defaults when a company has none: no `short_close` rows means every short-close needs a person (the
PO approval rows' first band with a person, never the asker); no `gstr2b_diff_abs` means 1.00; no `ledger_map` row for a
purpose means the export refuses with "map `<purpose>` to a ledger first". Readiness (7.3) lists the missing rows.

### 3.3 Today's behaviour

E45 (reconciliation), E26, E29, E30, E33, E34, E46, E71, E86, E87, E88, E09 and `tests/test_reconciliation.py`,
`test_order_changes.py`, `test_payments.py`, `test_budget_engine.py` pass on p3-b8 (b8 full run). They must pass again on
the branch with blocks 9 to 12 before anything here changes. Those that change are listed in 10.1.

### 3.4 Gaps

Short-close by risk with the need sent back (R1, DF-26, AT-14); two-track closure (R2, D-12, E-3); GSTR-2B (R4, D-19);
standard price versions, PPV and the four measures (R5, R6, D47, DF-29); month-end and year-end close (R7); accounting
export (R8); reconciliation gaps: statement periods and explained differences, bank lines not ours, MSME at year end, the
trace (R3). No AI capability (R9).

---

## 4. Depends on blocks 9 to 12

Blocks 9 to 12 are built before this one. Block 13 reads their results through these names and **adds every call into
their code** (they call nothing of block 13); where a built name differs, block 13 adapts and the report lists it.

| # | From | Block 13 reads | For |
|---|---|---|---|
| H9-1 | 3.6 reversal, 4.3 returns, 4.2 excess | `po_line.received_qty` lowered by a reversal or a counted return; block 13 adds `closure.check` after block 9's reversal, return dispatch and outcome, and excess accept and return | R2.5, R2.6: block 9 refuses a reversal on a `closed` order (9.7.2), so only a counted return reopens one |
| H9-2 | 4.3 return documents | `supplier_return` `draft`, or `dispatched` with a line from an order line whose `outcome` is not yet set (a `replace` still to arrive and a `no_replacement` still open show as supply not complete) | R2 blocker `return_open` |
| H9-3 | 4.2 excess | `receipt_excess` `waiting`, `proposed` or `returning` | R2 blocker `excess_open` |
| H9-4 | 3.6, 4.3 | `invoices/reconciliation.received_vs_invoiced` with block 9's filters (a reversed receipt counts only before `reversed_on`; a counted return is minus from its dispatch date); `recovery_claim.qty` per order line | R7.3 frozen GRNI; R2.3 `invoiced_not_received` |
| H10-1 | 9a.1 invoices without a PO | the invoice and its lines with a GL code | R4 books side, R8 vouchers; no order to close |
| H11-1 | 3.1-3.5 match | an invoice "booked": status `approved`, `part_paid` or `paid` (the match's `BOOKED`), booking date from `booked_at`; its lines' unit price and discount; `po_line.invoiced_qty` | R5, R6 (invoice recognition), R8 |
| H12-1 | 2.1 settlement (Q18) | an invoice **settled**: status `paid`, which block 12 sets only when its payment is `settled` (bank line); a `recorded` payment is not settled; a `failed` or `reversed` payment puts its invoices back to `approved` | R2 blockers `invoice_open`, `payment_failed`; R8 payment voucher date = the payment's `value_date` |
| H12-2 | 2.4 credit and debit notes | a note not `paid` (fully netted or refunded) and not `cancelled` | R2 blocker `credit_open` |
| H12-3 | 2.3 advances | an `advance_request` of the order in `approved`, `in_run`, `paid` or `recovering` | R1 risk flag, R2 blocker `advance_open`, R8 vouchers |
| H12-4 | D-21 recovery claims | `recovery_claim.status = 'open'` (block 9's table; block 12 sets `settled`) | R2 blocker `claim_open` |
| H12-5 | 2.1 settlement | block 12's `invoices/settlement.py` calls `payment.close_case` on settlement and reopens orders on a reversal (its 12.4.6); block 13 replaces both call sites with `orders/closure.check(db, po_no)` and `closure.reopen` and removes `close_case` | R2 |
| H12-6 | 2.5 TDS | `invoice.tds_amount` and section per invoice | R8 |


---

## 5. Requirements

Numbered R1 to R9 (the plan's 13.x numbers are task numbers).

### R1 Short-close by risk, the need sent back (6.6, D-13, DF-26)

1. **Ask.** The buyer (`po.create`) asks with a reason and optionally the lines (OP-3; default every open line). Refused as
   today when nothing was ever received ("cancel it instead"; goods taken back by a block 9 counted return were received) or nothing is still to come; refused when a short-close of the
   order already waits (409 "a short-close of `<po>` waits for `<name>`").
2. **Facts** (pure `formulas/closure.short_close_facts`): per line the balance `qty − received_qty − short_closed_qty`;
   the **value** = balance × unit price plus GST at the line's rate, through `formulas/orders.line_amounts` (the same
   amounts as an order, paise half up); the **flags**:
   - `unmet_need`: an open material request line pegged to the order line (`material.places_of`, `requests/supply_dates`)
     that would be short without this balance: "production still needs `<qty uom>` of `<item>` by `<dd Mon yyyy>`
     (`<MO or MR>`)";
   - `advance_open`: an advance on the order not yet adjusted or recovered (H12-3): "advance `<PAY>`: `<₹>` not yet used";
   - `in_transit`: a dispatch note line (`dispatch_note_line`) for the line not yet received: "`<qty>` dispatched on
     `<dd Mon yyyy>`".
3. **Approver.** `approvals/rules.find_rule(db, "short_close", value, business date)`. Band `auto` and no flag: **low
   risk**, the buyer's request is the decision and runs at once. A flag, or a band with a person: **high risk**; a flag
   moves an `auto` band to the first band with a person (OP-2). The approver is resolved as the price-variance approver
   is (`orders/confirmation._price_approver` pattern): `resolve_chain(..., exclude = the order's makers and the asker)`;
   nobody left: 409 "everyone the short-close rule names made this order or asked to close it; the rule has to name
   someone else".
4. **Proposal.** High risk: `proposals.propose(action = "po.short_close", value, required_permission = "po.approve")`
   naming the approver (and deputy); task `po_short_close` for that person, SLA 18 working hours, chain the next band's
   person, title "Close `<po>`'s balance: `<₹ value>`, `<k lines>`; `<first flag or 'value above ₹<limit>'>`". Nothing on
   the order changes and the supplier is not told while it waits. A `po_short_close` row keeps lines, value, risk,
   flags, rule code and version, approver, proposal, reason, status `waiting`.
5. **Decision.** Only the named person (or deputy) decides, through the existing proposal routes. Reject needs a note;
   the buyer is told. Approve runs the executor, which **re-reads the balance under the order's row lock**: when any
   line's balance differs from the one asked, the proposal fails, the row becomes `stale`, audit `po.short_close_stale`,
   409 "the balance of line `<n>` changed since it was asked (`<a>` then, `<b>` now): ask again" (OP-4).
6. **Execution** (low risk at once, high risk on approval), one transaction: the lines get `status = 'short_closed'` and
   `short_closed_qty += balance`; the order's status is worked out again by `formulas/receiving.order_status`, where
   `short_closed` counts like `received`, so an order with nothing else to come becomes **`received`, not `closed`**; row
   `closed`, `decided_by`; audit `po.close_balance` (kept) with `risk`, `value`, `flags`, `approved_by`. After the commit,
   as today: the supplier mail ("balance closed"), requester and buyer told, `material.after_event` (the store check
   re-raises the need: the **need sent back**, block 2 rules, a draft request "Short for `<MO>`"), then
   `orders/closure.check` (R2). A `short_closed` line takes no block 9 `replace` outcome (refused as on a closed line).
   The requester of a flagged need is also told "`<item>`: the balance on `<po>` was closed;
   the store check has raised `<qty>` again".
7. `po.closed_reason` is no longer written (the reason lives on `po_short_close`); `po.closed_at` means fully closed (R2).
8. Withdraw: the asker withdraws a waiting short-close (`withdrawn`); revising or cancelling the order withdraws it.
9. Minimum-volume shortfalls on contracts (Rev #7) are not tracked: no contract carries a minimum volume (OP-27).

### R2 Two-track closure (D-12, E-3)

1. One service `orders/closure.py`: `state(db, po_no) -> {supply_done, settled, blockers[]}` and `check(db, po_no, actor)`;
   the arithmetic in `formulas/closure.py` (pure). Line-by-line closing in `payment.close_case` goes; lines close with
   their order (OP-5).
2. **Supply complete:** every line not cancelled is `received` or `short_closed` (a service line: its confirmed quantity
   reaches the ordered one, or short-closed).
3. **Money settled:** no blocker, each with a code and words:

   | Code | When | Words |
   |---|---|---|
   | `not_invoiced` | a line's accepted quantity above its invoiced quantity | "line `<n>`: `<qty>` received, not invoiced" |
   | `invoiced_not_received` | invoiced quantity above accepted plus the line's recovery claims' quantity (H9-4) | "line `<n>`: `<qty>` invoiced, not received" |
   | `invoice_open` | an invoice (type invoice) of the order not settled and not cancelled (H12-1) | "invoice `<PI>` is `<status words>`" |
   | `payment_failed` | a failed or reversed payment of its invoices not paid again (H12-1) | "payment `<PAY>` failed on `<date>`" |
   | `advance_open` | advance not fully adjusted or recovered (H12-3) | "advance `<PAY>`: `<₹>` not yet used or recovered" |
   | `credit_open` | a credit or debit note on its invoices not applied (H12-2) | "`<credit note>` `<no>` not yet applied" |
   | `return_open` | a return document not closed (H9-2) | "return `<RTN>` not closed" |
   | `excess_open` | goods above the order not yet accepted or returned (H9-3) | "`<qty>` above the order on `<gr>` waits for the buyer" |
   | `claim_open` | a recovery claim not closed (H12-4) | "claim `<no>` open" |
   | `short_close_waiting` | R1.4 | "a short-close waits for `<name>`" |

   Quantities, not values: a released price difference never blocks.
4. **Close:** when supply is complete and nothing blocks, `po.status = 'closed'`, `closed_at`, every live line `closed`;
   audit `po.close` with `{supply_done_on, settled_on}`; `_notify_closed` as today ("Order `<po>` is paid and closed",
   once per person per order). Idempotent: a second check changes nothing.
5. **Called** after: a short-close runs; a receipt, inspection or reversal posts, a return is dispatched or decided, an
   excess is accepted or returned (block 9, H9-1); an invoice or note is booked, cancelled or applied; a payment is
   settled or fails (block 12, H12-5); an advance is adjusted or recovered. Block 13 adds each call, after the commit. A daily job
   `order_closure` (06:30 IST, `enqueue_unique`) checks every order `received` or `part_received` and catches anything
   missed.
6. **After closure** (OP-6): a credit or debit note, a failed or reversed payment, or a block 9 counted return that
   names a closed order **reopens** it: `status = 'received'` (`part_received` when the return lowered a line below its
   quantity), lines back to `received`/`short_closed`/`part_received`, `closed_at` null, audit `po.reopen` with the
   cause; it closes again by R2.4. A receipt reversal never meets a closed order (block 9 refuses it). A new invoice
   against a closed order is entered as today and blocked by the match (nothing left to invoice); it reopens nothing.
7. Budget: `budget_use` (unchanged) now counts a `short_closed` line until the order closes, so a received unpaid
   non-stock line stays committed. Order page and trace show "Supply: complete (`<date>`) | Money: settled" or the blockers.

### R3 Reconciliation gaps (P1-39)

1. **Statement periods** (OP-24): a statement line dated outside its period is matched like any other (ours: any document
   it names; payments by reference, else amount within 3 days, any date) and carries the flag "dated outside the
   statement's period (`<from>` to `<to>`)". A line dated before the company went Live (`tenant_state_change`) is
   `only_theirs` with the reason "before our records start (`<dd Mon yyyy>`)". The opening and closing balance rows of a
   statement (type `opening`, `closing`) are read and shown, not compared (OP-23).
2. **Explained differences:** AP (`invoice.book`) marks a statement line that is not `matched` as explained with a note
   (`resolved_by`, `resolved_at`); audit `vendor_statement.resolve`. The statement shows "`<k>` of `<n>` differences
   explained".
3. **Bank lines not ours** (OP-25): a person with `payment.propose` marks an `open` line `not_ours` with a reason ("bank
   charges"); audit `bank_statement.not_ours`; the `bank_statement_lines` task completes when no open line is left.
   Refused for a line already matched or confirmed.
4. **MSME at year end** (OP-26): `GET /api/reconciliation/msme/year-end?fy=` as of 31 March: per micro or small supplier,
   principal unpaid at 31 March, of it past the deadline (the amount the Income-tax Act s.43B(h) disallows until paid),
   interest due on it to 31 March (`formulas/payment.msme_interest`), and interest paid in the year (`payment.msme_interest`
   of the year's payments). Wording reviewed by the tax adviser (gate 8). From 1 March the Small suppliers tab shows a
   banner "Year end 31 March: `<k>` invoices of small suppliers will be past their deadline unless paid by then".
5. **Trace:** each order row adds the closure state (R2.7) and its short-closes; each invoice row its latest GSTR-2B
   outcome (R4). Received not invoiced stays a live report; the month-end snapshot is R7.

### R4 GSTR-2B comparison (9b.1, D-19)

1. **Input:** the portal JSON (`data.gstin`, `rtnprd` `mmyyyy`, `gendt`, `docdata.b2b[].inv[]`, `cdnr[].nt[]`, `b2ba`),
   read with every amount as exact `Decimal` (JSON numbers parsed as `Decimal`, never float); or our CSV template
   (`supplier_gstin,type,doc_no,doc_date,taxable,igst,cgst,sgst,cess,reverse_charge,itc_available,reason`). Our GSTIN
   must be one of `gst_registration`, else 422. Same file twice: the first result (`already_imported`). A different file
   for the same GSTIN and month: kept; the newest is the current comparison.
2. **Books in scope** for GSTIN g, month m: our invoices, credit and debit notes **issued by the supplier** with
   `buyer_gstin` g, supplier registered (has a GSTIN), not cancelled, dated on or before the end of m, and not matched in
   any earlier current comparison of g.
3. **Key** (OP-10): supplier GSTIN, type, `normalize_invoice_no(number)`, financial year of the document date. A `b2ba`
   amendment is read with its new values and matched on its new number, else its original.
4. **Outcome** per document (`formulas/gstr2b.compare`, pure), first that applies:
   `duplicate` (the same key twice in the file); `matched` (taxable and each of IGST, CGST, SGST, cess within
   `gstr2b_diff_abs`, date equal); `differs` (keyed, but a field outside it: each difference listed, "taxable: ours
   ₹26,400.00, 2B ₹26,500.00 (₹100.00 more)"; a date difference listed alone); `gstin_differs` (number and FY match a
   document of ours from the same PAN, GSTIN characters 3-12, another GSTIN); `only_2b`; `only_books` with "not in any
   2B for `<k months>`" counted from the document's month. Independently, `itc_available = false` with the portal reason
   is flagged "credit not available: `<reason>`". The tolerance version used is kept on the import.
5. **No holding** (D-19): the comparison never changes an invoice, a block, a due date or a run.
6. **Follow-up:** when anything is not `matched`, one task `gstr2b_differences` for the first AP person (SLA 40 working
   hours), payload the counts and the supplier list ("ask `<supplier>` to file or correct `<k documents>`"). No mail to
   suppliers (OP-11).
7. Screens: Close > **GSTR-2B check**: upload, list by month and GSTIN, results by outcome with ours and theirs side by
   side, "Our invoices not yet in any GSTR-2B" (as of a date).

### R5 Standard price versions (13.2, block 0b OP-19)

1. `item_standard_price` holds the versions; version 1 of each item comes from `item.standard_price` (2000-01-01). The
   version in force on a date is the one whose range holds it.
2. A new version: Finance (`price.standard`) proposes item, price, effective from (not before the first open month) and a
   reason; another holder of `price.standard` approves (proposal `price.standard`); on approval the version in force the
   day before the new one starts ends then (as D48). Audit `price.standard_proposed`, `price.standard`.
3. The estimate chain's standard step (`requests/estimates`, `formulas/estimate`) reads the version in force on the
   estimate date. A masters import of a changed `standard_price` once the company is Live is refused with "change it on
   Standard prices" (D-02 pattern, `masters/import_rules`).

### R6 PPV and the four measures (13.2, 13.3, D47, DF-29)

One record per measure and event in `price_measure`, written by `invoices/price_measures.record(db, through_date)` (daily
job `price_measures` 06:45 IST, and before a month close); unique per measure, part and source line, so a second run
adds nothing. Arithmetic in `formulas/ppv.py` (pure, Decimal, unit prices 4 places, amounts paise half up). Unit price
basis everywhere: taxable unit price after line discount, GST excluded (OP-13). Each row keeps its reference (which
standard version, contract version, benchmark, estimate) so it stays reproducible (rule 8).

| Measure | Part and when | Amount | Reference |
|---|---|---|---|
| **Accounting PPV** | `ppv_recognition` = `invoice` (default): at booking of an invoice line; `receipt`: at acceptance; `both`: at acceptance at the PO price, then a `true_up` at booking for the invoice price less the PO price | qty × (actual − standard) | standard version in force on the recognition date; no standard or no unit conversion: no row, listed as "no standard" |
| **Contract variance** | `order`: when an order line from a contract is approved; `invoice`: at booking | qty × (order or invoice price − contract price) | `po_line.price_record_id` (its version) |
| **Market gap** | `month_end`: at month close, one per active contract price with counting benchmarks | `formulas/price_gap.gap_pct` and gap per unit (evidence only, no amount) | lowest counting benchmark (D52 rule of block 0b) |
| **Saving, potential** | `award`: when an RFQ line is awarded (final or approved) | awarded qty × (baseline − awarded net price) | baseline = the request line's `est_price` (`est_source`); a period RFQ line without a request: the estimate chain on the award date (OP-15) |
| **Saving, realised** | `invoice`: at booking of an invoice line whose order line came from an award | invoiced qty × (baseline − invoice price) | the award's baseline |

1. A credit note line reverses its share with a negative row (part `invoice`). With `receipt` or `both`, a block 9
   receipt reversal or counted return reverses that receipt's PPV with a negative row (part `receipt`, source the
   reversal or the return line). Positive = unfavourable for PPV and
   contract variance; positive = saved for savings.
2. The control tower's `savings` KPI becomes `{"potential", "realised", "awards"}` from these rows (DF-29); the first-quote
   rule goes.
3. `GET /api/prices/measures` returns each measure in its own list with its own label and totals; never one sum.
   Screens: Close > **Price variance and savings**, five labelled tabs; filters month, item, supplier.
4. PPV is a record and a report: not posted to the budget (unchanged) and not exported as a journal (OP-22).

### R7 Month-end and year-end close

1. **One action:** "Close the month" (Finance, `budget.manage`, a reason) is the budget lock of block 3 with the
   month-end work added; `POST /api/budgets/periods/{p}/lock` and `POST /api/close/periods/{p}/close` call the same
   service `invoices/period_close.close`. Same order rule (the month has ended; every earlier month of its year closed),
   never reopened in Phase 1 (OP-17).
2. **Preview** (`GET /api/close/periods/{p}`), nothing written. Blocking: month not ended; an earlier month open. Warnings,
   each with a count and a link (OP-18): GSTR-2B not compared for the month (each GSTIN); bank lines open dated in or
   before it; invoices received in it not booked ("their vouchers go to the next month"); short-closes waiting; small
   suppliers past their deadline at its end (principal, interest); orders supply complete and money open.
3. **Close**, one transaction: run `price_measures.record` to the month end; freeze **received not invoiced as of the
   last day** (`received_vs_invoiced`, with block 9's reversal and return filters, H9-4) with each line's GL account in `period_close.grni`; market gap rows; the **final
   accounting export** of the month (R8); the lock row; `period_close` with the checklist as shown. Audit `period.close`
   with the totals. Datasets' locked months (2025-11 to 2026-10) have no row: "locked before month-end close existed".
4. **Late documents** (OP-19): anything whose event falls in a closed month is booked or exported in the first open month
   and keeps its event month (as `budget_ledger` today).
5. **Year end:** `close_year` (block 3) closes each month of the year not yet closed through R7.3, in order, then keeps
   the carried commitments as today; it is refused while March has not ended. The March snapshot is as of 31 March; the
   MSME year-end report (R3.4) is kept in `period_close.checklist` of March.

### R8 Accounting export (2.6)

1. `invoices/accounting_export.py`; vouchers built by `formulas/journal.py` (pure): each voucher balances to the paisa or
   the export stops and names it. Ledgers from `ledger_map` (the version in force on the voucher date) and each line's GL
   code (`coalesce(request_line.gl_code, po_line.gl_code)`; a non-PO invoice line's own, H10-1).

   | Voucher | Date | Lines |
   |---|---|---|
   | `purchase` per booked invoice | booking date | Dr each line's GL (taxable), Dr freight (`invoice_charge`), Dr `gst_input_*` per head (reverse charge: also Cr `rcm_payable`), Dr/Cr `round_off`, Cr TDS to `tds_payable` (H12-6), Cr the rest to `payable_msme` (micro or small) or `payable_other`, party = supplier |
   | `debit_note` / `credit_note` | booking date | the purchase lines reversed |
   | `payment` per settled payment | settlement (bank) date (H12-1) | Dr payable (party), Dr `msme_interest`, Cr `bank:<company account>` |
   | `advance` per settled advance | settlement date | Dr `advance` (party), Cr bank |
   | `advance_adjustment` | the invoice's booking date | Dr payable, Cr `advance` |
   | `accrual` (month close only) | last day of the month | Dr each GRNI line's GL, Cr `grni`; its `reversal` dated the 1st of the next month, in that month's export |

2. **File** (`accounts_export_format` = `generic_csv`, OP-20): `voucher_type,voucher_no,date,event_period,ledger_code,
   ledger_name,party_code,party_name,party_gstin,debit,credit,reference,narration`; amounts exact text; voucher numbers
   are the document numbers, so a ledger import can drop a repeat. Plus a total per ledger.
3. **Draft** any time (`GET /api/close/exports/{p}?kind=draft`, `accounts.export`): what the month holds now; nothing kept
   but the audit row `accounts.export_draft`. **Final** only by R7.3: kept with its hash; every document in it recorded
   once (`accounting_export_doc`); a document already in a final export is never in another; downloading a final export
   again gives the same bytes. Audit `accounts.export_final`. Late documents follow R7.4 (`event_period` set).

### R9 No AI capability in this block

The plan gives 6.6, 9b.1, 13.2, 13.3 and 2.6 no agent, so Decision 14 has nothing to build and no D54 check applies. The
PPV watch (11b.6) and payment exception alerts (11b.4, 11b.5) are Across; explaining differences is Phase 2.

---

## 6. Settings and rules

| Key | Where | Default | Use |
|---|---|---|---|
| `short_close` rows | `approval_rule` (versioned) | Apex SC-01..03, Brightwave SC-01..02 (3.1) | R1.3 |
| `gstr2b_diff_abs` | `tolerance` (versioned) | 1.00 | R4.4 |
| `item_standard_price` | new table (versioned) | v1 from `item.standard_price` | R5, R6 |
| `ledger_map` | new table (versioned) | 3.1 step 3 | R8 |
| `ppv_recognition` | setting (D-22) | `invoice` | R6 (O3: finance confirms at onboarding) |
| `accounts_export_format` | setting (D-22) | `generic_csv` | R8 |
| `msme_*` | existing | 45 / 15 / 7 | R3.4 |

Task SLAs are constants as in block 8 (18 and 40 working hours).

---

## 7. Data: migration 0200 `reconcile_and_close` (down_revision 0190, block 12)

| Change | Columns and rules |
|---|---|
| `po_line.status` | check adds `short_closed`; backfill: `closed` lines with `short_closed_qty > 0` become `short_closed`, and their orders go from `closed` to `received`, `closed_at` null (OP-28: the R2 job re-closes what is settled) |
| `po_short_close` (new) | `id`, `po_no` (fk), `lines` jsonb `[{line, balance, value}]`, `value` num(18,2), `risk` (`low`, `high`), `flags` jsonb, `rule_code`, `rule_version`, `approver`, `proposal_id`, `reason` not null, `status` (`waiting`, `closed`, `rejected`, `stale`, `withdrawn`), `asked_by`, `asked_at`, `decided_by`, `decided_at`, `note`; unique (`po_no`) where `waiting`; backfill one `closed` row per order with `closed_reason` (`asked_by` 'migration') |
| `item_standard_price` (new) | `id`, `item_code` (fk), `price` num(18,4) ≥ 0, `version`, `effective_from`, `effective_to`, `status` (`proposed`, `active`, `rejected`), `reason`, `proposal_id`, `created_by`, `created_at`, `approved_by`, `approved_at`; unique (`item_code`, `version`); backfill version 1 from `item.standard_price`, from 2000-01-01, `active`, 'system:setup' |
| `price_measure` (new) | `id`, `measure` (`ppv`, `contract_variance`, `market_gap`, `saving_potential`, `saving_realised`), `part`, `source_type`, `source_ref`, `source_line` (default 0), `item_code`, `supplier_code`, `po_no`, `po_line_no`, `on_date`, `period` (yyyy-mm), `qty` num(18,4), `unit_price`, `reference_price` num(18,4), `reference` text not null, `amount` num(18,2), `pct` num(9,4), `basis` jsonb (recognition point, versions), `recorded_at`; unique (`measure`, `part`, `source_type`, `source_ref`, `source_line`) |
| `gstr2b_import` (new) | `id`, `gstin`, `period`, `generated_on`, `file_name`, `file_hash`, `tolerance_version`, `documents`, `outcomes` jsonb (count per outcome), `current` bool, `imported_by`, `imported_at`; unique (`gstin`, `file_hash`) |
| `gstr2b_line` (new) | `id`, `import_id` (fk), `supplier_gstin`, `doc_type`, `doc_no`, `doc_date`, `taxable`, `igst`, `cgst`, `sgst`, `cess` num(18,2), `reverse_charge`, `itc_available`, `itc_reason`, `our_invoice_no`, `outcome` (`matched`, `differs`, `gstin_differs`, `only_2b`, `only_books`, `duplicate`), `differences` jsonb, `reason` |
| `period_close` (new) | `period` pk, `closed_by`, `closed_at`, `reason`, `checklist` jsonb (preview as shown, MSME year-end in March), `grni` jsonb (lines with GL), `grni_total` num(18,2), `export_id` |
| `accounting_export` (new) | `id`, `period` unique, `format`, `vouchers`, `debit`, `credit` num(18,2), `content` text, `file_hash`, `made_by`, `made_at` (finals only; drafts are not kept) |
| `accounting_export_doc` (new) | `export_id` (fk), `doc_type`, `doc_no`, `event_date`; pk (`doc_type`, `doc_no`): a document is in one final export |
| `ledger_map` (new) | `id`, `purpose`, `gl_code` (fk, may be empty), `ledger_name`, `version`, `effective_from`, `effective_to`; unique (`purpose`, `version`) |
| `bank_statement_line` | status check adds `not_ours`; the reason goes in `why`, the person in `confirmed_by`, `confirmed_at` |
| `vendor_statement_line` | adds `flag`, `note`, `resolved_by`, `resolved_at` |
| `role_permission` | Finance `price.standard`, `accounts.export`, `gst.reconcile`; AP `gst.reconcile` |
| `org_setting` | `ppv_recognition` = `invoice`, `accounts_export_format` = `generic_csv` (on conflict do nothing) |

Down: drop the new tables and columns, restore the constraints, lines `short_closed` back to `closed`. Then
`scripts/gen_schema_doc.py`, `docs/data-dictionary.md` rows, `scripts/gen_master_schema.py` (masters `ledger_map`,
`item_standard_price`, policy C of `masters/import_rules`), cutover classification (configuration carried:
`ledger_map`, `item_standard_price`; the rest archived), readiness rows (3.2).

### 7.1 Code (one job each)

| Module | Change |
|---|---|
| `formulas/closure.py` | `short_close_facts`, `risk`, `blockers`, `supply_done` (pure) |
| `orders/short_close.py` (new) | ask, preview, executor `po.short_close`, withdraw; `changes.close_balance` delegates to it |
| `orders/closure.py` (new) | `state`, `check`, `reopen`, job `order_closure`; replaces `payment.close_case` (H12-5) |
| `formulas/receiving.py` | `order_status`: `short_closed` counts as received |
| `formulas/gstr2b.py`, `invoices/gstr2b.py` (new) | read, compare (pure); import, books in scope, task |
| `masters/standard_prices.py` (new) | versions, proposal, version in force; `requests/estimates` reads it |
| `formulas/ppv.py`, `invoices/price_measures.py` (new) | measures (pure); job and record |
| `platform/control_tower.py`, `formulas/kpis.py` | savings from `price_measure` (DF-29) |
| `invoices/period_close.py` (new) | preview, close; `budget_periods.lock` and `close_year` call it |
| `formulas/journal.py`, `invoices/accounting_export.py` (new) | vouchers (pure), draft and final files |
| `invoices/vendor_statement.py`, `bank_statement.py`, `reconciliation.py`, `trace.py` | R3 |
| `platform/close_api.py` (new); `reconciliation_api.py`, `order_api.py` | section 8 |
| `ui/js/views/reconciliation.js` (Close: new tabs), `purchase-orders.js`, `control-tower.js` | 8.1 |
| `platform/demo_cases.py` | the paid-and-closed case through settlement; a short-closed case |
| `datagen/` | section 3.1 |

No agent tool; CLI `python -m p2p.cli close <tenant> <yyyy-mm> --as <email> [--dry-run]` (preview, then close as the Finance login
given by `--as`), for the month-end rehearsal (gate 9).

---

## 8. Public interface (for the eval writer)

All `/api` under the tenant session, JSON; errors 422 (input), 409 (state), 403 (permission), 404 (not in this company).

| Endpoint | Permission | Body / query | Success | Errors |
|---|---|---|---|---|
| `GET /api/purchase-orders/{no}/close-balance/preview` | `po.create` | `lines=1,2` | `{lines: [{line, balance, value}], value, risk, flags[], rule, approver}` | 409 nothing received or to come |
| `POST /api/purchase-orders/{no}/close-balance` | `po.create` | `{reason, lines?}` | `{status: "closed"\|"waiting_approval", short_close_id, risk, value, flags, approver?, proposal_id?}` | 422 no reason; 409 as R1.1, R1.3 |
| `POST /api/purchase-orders/{no}/short-closes/{id}/withdraw` | the asker | - | `{status: "withdrawn"}` | 409 not waiting |
| `POST /api/admin/proposals/{id}/approve` / `reject` | named approver | existing | existing | 409 stale (R1.5); 403 not named |
| `GET /api/purchase-orders/{no}` | existing | - | adds `closure {supply_done, supply_done_on, settled, blockers [{code, words}], short_closes []}` | - |
| `GET /api/trace/{no}` | existing | - | order items add `closure`; invoice items `gstr2b` | - |
| `POST /api/gst/gstr2b` | `gst.reconcile` | file (.json or .csv) | `{id, gstin, period, documents, outcomes {..}, results [], already_imported, unreadable [], task_id}` | 422 bad file, unknown GSTIN |
| `GET /api/gst/gstr2b?period=&gstin=` / `{id}` / `template` | `gst.reconcile` or `invoice.view` | - | list / one / CSV | 404 |
| `GET /api/gst/not-in-2b?as_of=&gstin=` | same | - | `[{invoice, supplier, date, total, months}]` | - |
| `GET /api/prices/standard?item=` | `price.view` | - | versions | - |
| `POST /api/prices/standard` | `price.standard` | `{item, price, effective_from, reason}` | `{proposal_id}` | 422; 409 date before the first open month |
| `GET /api/prices/measures?measure=&from=&to=&item=&supplier=` | `price.view` | - | `{ppv: {label, rows, total}, contract_variance: {...}, market_gap: {...}, saving_potential: {...}, saving_realised: {...}}` | 422 unknown measure |
| `GET /api/close/periods?fy=` / `{p}` | `budget.manage`, `payment.view` | - | months with state / the preview (R7.2) | 422 bad month |
| `POST /api/close/periods/{p}/close` (and the block 3 lock route) | `budget.manage` | `{reason}` | `{period, closed_at, grni_total, export {vouchers, debit, credit}, warnings}` | 422 not ended, no reason; 409 earlier month open, closed |
| `GET /api/close/periods/{p}/grni` | `invoice.view`, `payment.view` | - | the frozen lines and total | 404 not closed |
| `GET /api/close/exports/{p}?kind=draft\|final` | `accounts.export` | - | CSV | 404 no final; 409 a voucher does not balance, a purpose unmapped |
| `POST /api/reconciliation/bank-lines/{id}/not-ours` | `payment.propose` | `{reason}` | `{line, status}` | 409 not open; 422 no reason |
| `POST /api/reconciliation/vendor-statements/{id}/lines/{line}/resolve` | `invoice.book` | `{note}` | `{line, resolved_by}` | 409 matched or resolved |
| `GET /api/reconciliation/msme/year-end?fy=` | `invoice.view`, `payment.view` | - | R3.4 per supplier and totals | 422 bad year |

Tables the eval may read: `po (status, closed_at)`, `po_line (status, short_closed_qty)`, `po_short_close`, `proposal`,
`task`, `item_standard_price`, `price_measure`, `gstr2b_import`, `gstr2b_line`, `period_close`, `budget_period_lock`,
`accounting_export`, `accounting_export_doc`, `ledger_map`, `bank_statement_line`, `vendor_statement_line`,
`notification`, `email_message`, `audit_event`.

Audit actions: `po.short_close_asked` (value, risk, flags, approver), `po.close_balance` (kept; executed), `po.short_close_
rejected`, `po.short_close_stale`, `po.short_close_withdrawn`, `po.close` (tracks), `po.reopen` (cause), `gstr2b.import`,
`price.standard_proposed`, `price.standard`, `price_measure.run` (counts), `period.close`, `budget.period_lock` (kept,
same transaction), `accounts.export_draft`, `accounts.export_final`, `bank_statement.not_ours`, `vendor_statement.resolve`,
`task.create`, `task.complete`.

### 8.1 Screens (original ProcureOS look; no redesign)

- **Buyer**, order page: **Close what is still to come** opens the preview: lines and balances (tick lines), value,
  "Closes now" or "Goes to `<name>` because `<value above ₹25,000 | production still needs ... | advance ...>`", reason,
  **Close** / **Send for approval**; a waiting short-close with **Withdraw**; the closure panel (R2.7).
- **Purchase manager, director:** My work "Short-closes to approve": order, balance per line, value, risk reasons, unmet
  need, advance; **Approve** / **Reject** (note).
- **Finance**, Close menu: Reconciliation (existing tabs; bank **Not ours**; MSME year-end view), **GSTR-2B check**,
  **Month-end close** (months of the year, preview, **Close the month**, frozen received-not-invoiced, **Download the
  accounting export**, draft or final), **Price variance and savings** (five labelled tabs; **Standard prices** with
  versions and **Propose a new price**).
- **AP:** GSTR-2B upload and results; statement differences **Mark explained**.
- **Control tower:** budget panel's **Lock** becomes **Close the month** (same place); savings "potential ₹ / realised ₹".
- **Admin** (`frontend/`): approval rules screen lists the `short_close` document like the others; no other change.

### 8.2 Emails

Short-close for approval: the task email (existing task mail) with the 8.1 facts. Supplier "balance closed" (existing
words) only after execution. Requester and buyer: execution, rejection ("`<name>` did not close the balance: `<note>`"),
need raised again (R1.6). "Order `<po>` is paid and closed" (existing). GSTR-2B differences: the task email. No supplier
mail about GSTR-2B.

### 8.3 Docs

`docs/demo-script.md` "Reconciliation (step 13)" gains: short-close by risk, the order closing after the bank match,
GSTR-2B, month close and the export; `docs/guides/finance.md`, `ap.md`, `buyer.md`, `approver.md` (each within 50 lines);
data dictionary; schema doc; CLAUDE.md "Where things are" (closure, short-close, GSTR-2B, PPV, period close, export) and
the latest migration number.

---

## 9. Scenarios, worked figures and boundary cases

Common: `demo reset apex` (and `brightwave` where named); business date pinned; role logins; Mailpit. PLT-CKN: Mon-Sat,
closed 24 Nov and 25 Dec 2026. **P** is block 8's order (scenario P): sent Mon 23 Nov 2026 to SUP-001 on CNT-0001,
line 1 RM-02 100 × 480.00 (18%), line 2 RM-03 200 × 220.00 (5%), line 3 RM-05 150 × 180.00 (5%), total 1,31,190.00,
approved by U08. Mon 30 Nov store@ receives and accepts line 1 40, line 2 120, line 3 150.

| # | Steps | Expected |
|---|---|---|
| **SC1** Below the limit | Tue 1 Dec buyer@ closes line 2 | balance 80; 17,600.00 + CGST 440.00 + SGST 440.00 = **18,480.00** ≤ 25,000.00, no flag: low risk, closed at once by U03; line 2 `short_closed` 80; order `part_received`; supplier mail; no proposal |
| **SC2** Above the limit | then line 1 | balance 60; 28,800.00 + 2,592.00 + 2,592.00 = **33,984.00**: SC-02, proposal to U08, task; order unchanged, no supplier mail; U03 approving: 403; U08 approves Wed 2 Dec: line 1 `short_closed` 60; order **`received`**, not closed; blockers `not_invoiced` lines 1-3 |
| **SC2b** Stale | as SC2, but store@ accepts 10 more on line 1 before U08 decides | U08's approval 409 "the balance of line 1 changed since it was asked (60 then, 50 now): ask again"; row `stale`; asked again: 24,000.00 + 4,320.00 = 28,320.00, SC-02 again |
| **SC3** Need still there | MO-1's castings (RM-01 50 at contract 650.00, 18%, buyer tier, touchless) for MO-1; 30 received; buyer@ closes the 20 left while MO-1 still needs them | 13,000.00 + 1,170.00 + 1,170.00 = 15,340.00 ≤ 25,000.00 but flag `unmet_need` "production still needs 20 NOS of Motor body casting 1 HP by `<MO-1 date>` (MO-1)": high, to U08; on approval the store check raises a draft request "Short for MO-1", RM-01 20 NOS, estimate 13,000.00 (contract); production@ told |
| **SC4** Advance | an order on `ADV30N30`, its 30% advance settled, part received; balance value under 25,000.00 | flag `advance_open`: to the purchase manager; after closing, blocker `advance_open` until the advance is adjusted |
| **TT1** Closes on settlement | SC1+SC2; Fri 4 Dec ap@ books SUP-001's invoice dated 30 Nov: RM-02 40 × 480.00, RM-03 120 × **222.20**, RM-05 150 × 180.00 (within tolerance) | taxable 19,200.00 + 26,664.00 + 27,000.00 = 72,864.00; GST 3,456.00 + 1,333.20 + 1,350.00 = 6,139.20 (CGST 3,069.60, SGST 3,069.60). Blocker `invoice_open`; run approved, bank file, statement matched: order `closed`, all lines `closed`, "paid and closed" to U03 and the requester once; second check adds nothing |
| **TT2** Reopen | after TT1, a credit note of SUP-001 for ₹264.00 + GST on line 2 is booked | order back to `received`, audit `po.reopen` (cause the note); blocker `credit_open` until applied; closes again |
| **TT3** Received short, never closed | an order received 90 of 100, invoiced and settled for 90 | stays `part_received`, blocker none on money, supply not complete: never closes until short-closed (today's `close_case` also never closes it; now visible on the page) |
| **PV1** PPV and variance | TT1's invoice; `ppv_recognition` invoice | PPV line 2: 120 × (222.20 − 220.00) = **264.00** (standard v1); contract variance line 2 264.00 (CNT-0001 RM-03 v1); lines 1, 3: 0.00; `receipt`: line 2 0.00 at 30 Nov; `both`: 0.00 then true-up 264.00 |
| **PV2** Version in force | the demo wire: request 120 KGS RM-08 estimate 920.00; Shree awarded at 905.00; invoice 120 × 905.00 booked Fri 4 Dec | potential saving 120 × 15.00 = **1,800.00** (baseline estimate, source as stored); realised 1,800.00; PPV against v2 950.00 (from 1 Dec): 120 × (905.00 − 950.00) = **−5,400.00**; booked 30 Nov instead: against v1 920.00, −1,800.00. Today's KPI (first quote 905.00) shows 0.00 |
| **PV3** Market gap (Brightwave) | close Nov 2026 | LP-01 on CNT-0001 vs its benchmark: gap 5.0000% (block 0b figure), evidence only, no amount |
| **ME1** Month close | Thu 3 Dec finance@ closes 2026-11 (P received 30 Nov, not invoiced) | preview warnings: GSTR-2B for 2026-11 not compared; small-supplier and bank items as found. Close: GRNI frozen as of 30 Nov **72,600.00** (19,200.00 + 26,400.00 + 27,000.00, GL 1410); final export: `accrual` Dr 1410 72,600.00 / Cr 2310 72,600.00 dated 30 Nov; `reversal` in the December export dated 1 Dec; budget month locked; closing 2026-12 on 3 Dec: 422 not ended |
| **ME2** Period closed while documents are open | after ME1, TT1's invoice (dated 30 Nov) booked 4 Dec; its run settled with bank value date 30 Nov imported 7 Dec | purchase voucher in December (booking date); payment voucher in December with `event_period` 2026-11; the November final export downloads byte for byte as on 3 Dec; nothing refused |
| **YE1** 31 March | pinned 1 Apr 2027: SUP-002 (micro, 45 days) invoice accepted 10 Feb 2027, ₹11,800.00, unpaid; an invoice booked 31 Mar; a receipt on 31 Mar not invoiced; `close_year 2026-27` | deadline 27 Mar 2027; at 31 Mar 4 days late; year-end report: unpaid past deadline 11,800.00 (s.43B(h)), interest by `msme_interest` to 31 Mar; months Nov-Mar closed in order with snapshots; the 31 Mar invoice in the March export, the receipt in the March GRNI and accrual; a short-close on 1 Apr belongs to FY 2027-28 |
| **G1** GSTR-2B (Apex) | replay history (as E45), upload `gstr2b_27APXCA0000P1ZL_202610.json`, then `_202611.json` | outcomes = `truth_gstr2b.csv`: G1 matched, G2 differs (taxable ₹100.00 more), G3 matched (₹0.50 within 1.00), G4 only_books (0 months) then matched in November, G5 only_2b, G6 credit not available "P", G7 gstin_differs, G8 duplicate; no invoice, block or due date changed; one task for ap@ |
| **RC1** Statement period | import `vendor_statement_period.csv` with period 1-28 Feb 2026 | the January invoice matched and flagged "dated outside the statement's period"; the 2 Mar payment matched by reference, flagged; ap@ marks a difference explained |
| **RC2** Bank charges | E34's statement line `CHG000001` ₹17.70 | finance@ **Not ours**, "bank charges": status `not_ours`; the task completes |
| **TN1** Tenants | P (Apex) and a Brightwave order | Brightwave buyer: Apex's short-close, closure, 2B import, measures and exports 404 or absent; Apex's `price_measure`, `gstr2b_line`, `period_close` rows absent from Brightwave's database |

**Boundary cases** (pytest on the pure formulas, and named in the evals):
- Short-close value exactly 25,000.00: SC-01 (auto); 25,000.01: SC-02. A flag at any value: a person.
- Short-close asked when the balance is 0 on every chosen line: 409 "nothing is still to come on the lines chosen".
- Closure: invoiced quantity equal to accepted: no blocker; one unit less: `not_invoiced`; price difference released: no
  blocker. Cancelled lines ignored; an order of only cancelled lines is `cancelled`, never `closed`.
- GSTR-2B: each tax head ₹1.00 apart: matched; ₹1.01: differs. Invoice numbers "SWAE/2526/0019" and "swae-2526-0019":
  same key; same number in another financial year: not the same document.
- PPV: no standard: no row ("no standard"); standard version starting on the booking day applies that day; quantity
  in another unit without conversion: no row.
- Month close: the last day of the month is not ended (422); the 1st of the next month is; a month after an open one:
  409; 31 March closes FY months only after 1 April; the export of a month with no documents has zero vouchers and still
  closes.
- A statement in a different date range: lines outside it matched and flagged (RC1); a statement before go-live: "before
  our records start".

---

## 10. Tests and evals

### 10.1 pytest (scenario names; given, when, then)

- `tests/test_closure_formulas.py`: short-close facts, value and band boundaries; risk flags; blockers by quantity; supply
  done with cancelled and short-closed lines.
- `tests/test_short_close.py`: SC1-SC4; approver never the asker or a maker; stale; withdraw; supplier mail only after.
- `tests/test_two_track_closure.py`: TT1-TT3; idempotent check; job; reopen; notification once.
- `tests/test_gstr2b.py`: parser (JSON numbers as Decimal, b2ba, cdnr, CSV), compare boundaries, G1, re-import.
- `tests/test_ppv.py`: PV1-PV3 for the three recognition points; credit note reversal; standard versions (R5).
- `tests/test_period_close.py`: ME1, ME2, YE1; the block 3 lock route does the same; export balances, final bytes
  stable, a document once.
- `tests/test_reconciliation.py` (extend): RC1, RC2, MSME year end. `tests/test_tenant_isolation.py` (extend): TN1.
- **Update** (each listed in the report): `test_order_changes.py`, `test_budget_engine.py`, `test_payments.py`
  (`close_case` gone); E71, E86, E09 (a short-close of a pegged line now goes to U08: approve, then their checks); E29
  (savings as potential and realised); E26, E30, E33, E34, E46 (orders close on settlement; words unchanged); E87, E88
  (lock and year end take the snapshots); E45 unchanged; `demo_cases.py`.

### 10.2 Evals E176-E183 (independent; written from this spec only)

Common: fresh `demo reset apex` (and `brightwave` where named); business date pinned; role logins; Mailpit; oracle =
section 9 arithmetic in exact fractions from the dataset files (`approval_rule.csv`, `item_standard_price.csv`,
`ledger_map.csv`, `gl_account.csv`, `history/*`, `truth/*`), not from the code; each ends with a tenant check and names
the planted mutations it must catch.

| Eval | Given | When | Then | Oracle | Mutations caught |
|---|---|---|---|---|---|
| **E176** Short-close by risk | P part received (SC1, SC2, SC2b, SC4) | the buyer closes below and above the limit, with an advance, and goods arrive while one waits | below: closed by the buyer; above or flagged: a proposal to the person the `short_close` row names, never the asker or the order's maker; nothing changes and no supplier mail until approved; a changed balance is refused and must be asked again | values from quantities, prices and GST rates in exact fractions; band from the rows | the value compared without GST; approval executed on the old balance |
| **E177** The need goes back (AT-14) | MO-1's castings part received (SC3) | the buyer closes the balance while MO-1 still needs it | high risk whatever the value; on approval a draft request for MO-1's shortfall at the contract price; the requester told; the order stays open until settled | MO-1's need less what arrived, from the demand file and receipts | short-close not flagged when production needs the balance; no store check after it |
| **E178** Two-track closure (D-12) | TT1, TT2, TT3 | supply completes, then the invoice is booked, paid and the bank statement matched; then a credit note | the order closes only when supply is complete and every blocker is gone, all lines with it, one notice; reopened by the note, closed again after; a short-received order never closes; the walk-through's orders still close | blockers recomputed from receipts, invoices, notes and payments by quantity | closed on payment with an advance open; a released price difference blocking |
| **E179** GSTR-2B check (D-19) | Apex history replayed; Brightwave history; the generated 2B files | AP uploads October then November (Brightwave September then October) | each document's outcome and listed differences equal `truth_gstr2b.csv`; G4 moves to matched in November; no invoice, block, due date or run changes; one task; a second upload adds nothing | `truth_gstr2b.csv` and the JSON read independently with exact decimals | amounts read as floats (G3 flips); a mismatch that holds the invoice |
| **E180** The four measures (D47, DF-29) | PV1, PV2 on Apex; PV3 on Brightwave; history replayed for PPV at all three recognition points | invoices booked, awards made, the month closed; the job run twice | PPV, contract variance, market gap, potential and realised saving each equal the oracle, each in its own list, never summed together; standard version in force by date; the KPI shows potential and realised; a second run adds nothing | `truth_price_measures.csv`; section 9 figures | PPV against today's standard instead of the version in force; saving from the first quote |
| **E181** Month-end and year-end | ME1, ME2, YE1 | Finance closes November, then FY 2026-27 on 1 April 2027 | blocked only for a month not ended or out of order; warnings listed; GRNI frozen equals the oracle and never changes later; late documents land in the first open month with their event month; March closed with the MSME year-end figures | `truth_grni.csv` method from E45 at the month end; MSMED deadline and interest from the Act's rule and `ref_rbi_bank_rate` | snapshot computed on reading instead of frozen; a closed month's export changed by a late document |
| **E182** Accounting export (2.6) | history replayed for Oct 2026 (Apex) and Sep 2026 (Brightwave); ME1 | a draft, then the final at close; download twice | every voucher balances to the paisa; totals per ledger equal `truth_accounting_export.csv`; MSME suppliers to 2110, others 2120; GST heads and TDS right; the final downloads identically; a document is never in two finals | vouchers rebuilt from the history files and `ledger_map.csv` | TDS left in the payable; a settled payment exported in two months |
| **E183** Reconciliation gaps and tenants | RC1, RC2, TN1, the trace of P | statement and bank actions; Brightwave reads Apex's data | outside-period lines matched and flagged; explained differences kept with who; a bank line marked not ours closes its task; the trace shows closure and 2B status; nothing of Apex visible to Brightwave | the statement file and our documents; the planted lines | an outside-period payment left as only theirs; tenant missing from the closure query |

---

## 11. Cross-cutting

| Rule | Here |
|---|---|
| Money | Decimal, paise half up; unit prices 4 places; GSTR-2B JSON parsed with `Decimal`; no float literal (E12) |
| A model never decides | no model in this block; risk, blockers, outcomes, measures and vouchers are code |
| One service per write | `short_close`, `closure`, `gstr2b`, `standard_prices`, `price_measures`, `period_close`, `accounting_export`; each audits |
| Proposals | high-risk short-close and standard price versions are proposals a named other person decides |
| Segregation | short-close approver ≠ asker ≠ order makers; standard price approver ≠ proposer; bank confirm rule unchanged |
| Tenant safety | every new table in the tenant database; TN1 |
| Rules as data | `short_close` rows, tolerance `gstr2b_diff_abs`, standard price versions, ledger map, settings; each record keeps the version it used |
| Dates | financial year April-March (FY of a document for 2B keys; year end 31 March); working hours for task SLAs on the company calendar; business date in India |

### 11.1 Files shared with other work

| File | Other work | Block 13 keeps to |
|---|---|---|
| `invoices/payment.py` (`close_case`), `invoices/settlement.py` | block 12 | `close_case` removed; its two call sites in `settlement.py` call `orders/closure.check` and `closure.reopen` (H12-5) |
| `receiving/reversal.py`, `returns.py`, `excess.py` | block 9 | one `closure.check` call each, after commit (H9-1) |
| `orders/changes.py` | blocks 7, 8 | `close_balance` delegates to `orders/short_close.py` |
| `invoices/bank_statement.py`, `vendor_statement.py` | block 12 (settlement) | only `not_ours` and statement flags and notes |
| `approvals/budget_periods.py` | block 3 | `lock` and `close_year` call `period_close` |
| `formulas/receiving.py`, `receiving/returns.py` | block 9 | `order_status` for `short_closed`; `replace` refused on a `short_closed` line |
| `platform/exceptions.py` | blocks 9-12 | task kinds `po_short_close`, `gstr2b_differences` (block 11's sweep makes them cases) |
| `platform/proposals.py` | blocks 9, 12 | `po.short_close` registered on block 9's `closer` hook (rejected: row `rejected`, buyer told) |
| Migrations, evals | blocks 9-12 | 0200; E176-E183 |

---

## 12. Open points (RECOMMENDED defaults)

| # | Question | RECOMMENDED and reason |
|---|---|---|
| OP-1 | Short-close limits | `short_close` rows (3.1): auto to ₹25,000.00 including GST, then the purchase manager (Brightwave CFO), director above ₹5,00,000.00 (Apex). Versioned data; values are defaults to validate with the client (as O2) |
| OP-2 | What risk is besides value | unmet production need, an open advance, goods in transit: each moves an auto band to the first band with a person (AT-14, Base E-2, D-18) |
| OP-3 | Short-close per line | allowed; default every open line (the screen's "Order, open balance") |
| OP-4 | Goods arriving while a short-close waits | receipts go on; approval re-checks the balance; a changed balance is stale (0.5's "the limit is checked on the payload that runs") |
| OP-5 | Closure level | the order; lines close with it (D-12 speaks of the order; one notice) |
| OP-6 | A note, failed or reversed payment, or counted return after closure (a receipt reversal is refused on a closed order by block 9) | reopens the order and closes it again (the engine re-checks, Rev monitoring "never close without the engine re-checking") |
| OP-7 | "Money settled" | block 12's settled state (Q18): invoice `paid` after a `settled` payment |
| OP-8 | GSTR-2B file | the portal JSON and our CSV template; Excel later |
| OP-9 | 2B rounding | ₹1.00 per tax head and taxable (tolerance, versioned) |
| OP-10 | 2B key | supplier GSTIN, type, normalised number, document FY; same PAN other GSTIN reported apart |
| OP-11 | 2B follow-up | one task for AP; no supplier mail; nothing held (D-19) |
| OP-12 | PPV recognition point | `invoice` (only actual prices; no true-up); `receipt` and `both` built; finance confirms per client (O3, gate 8) |
| OP-13 | PPV price basis | taxable unit price after discount, GST excluded (all GST treated as recoverable in Phase 1; blocked credit later) |
| OP-14 | Standard price versions | new table; a second Finance person approves; import refused once Live |
| OP-15 | Saving baseline (DF-29) | the request line's estimate (contract, last paid or standard, as stored); period RFQ lines: the estimate chain on the award date |
| OP-16 | Market gap record | at month close per active contract price; evidence only (D52) |
| OP-17 | Month close | the block 3 lock with the month-end work; Finance alone (as the lock); never reopened in Phase 1 |
| OP-18 | What blocks a month close | only a month not ended or out of order; everything else is a warning (closing never waits for suppliers) |
| OP-19 | Documents of a closed month | the first open month, event month kept (as `budget_ledger`) |
| OP-20 | Export format | one generic double-entry CSV; Tally XML with the Phase 5 connector |
| OP-21 | Voucher dates | invoice: booking date; payment and advance: settlement date; adjustment with its invoice; accrual at month end, reversed on the 1st |
| OP-22 | PPV in the export | no: a record and a report (most Indian clients do not post standard costs) |
| OP-23 | Statement balances | read and shown, not compared: our books start at go-live |
| OP-24 | Statement lines outside the period | matched and flagged; before go-live "before our records start" |
| OP-25 | Bank lines not ours | a person marks them with a reason; no auto rule for charges |
| OP-26 | MSME year end | report at 31 March (s.22 disclosure figures, s.43B(h) amount); banner from 1 March; MSME-1 in Phase 2; tax adviser reviews words |
| OP-27 | Contract minimum-volume shortfall (Rev #7) | out of Phase 1: no contract field; listed |
| OP-28 | Orders short-closed before 0200 | lines to `short_closed`, order back to `received`; the closure job re-closes what is settled |
| OP-29 | Migration | one file 0200 after the last of blocks 9 to 12 |
| OP-30 | Where 2.6 sits | here (section 1): the export is frozen by the month close |

No point needs the owner. OP-1 is a new approval authority like block 5's award approval; it follows D-13 (confirmed),
so it is a default for review, not an owner decision.

---

## 13. Build order

1. **Data first:** section 3.1; `--check`.
2. Confirm blocks 9 to 12 hand-overs (section 4) on their branch; run 3.3.
3. **Migration 0200**, backfill, schema doc, data dictionary, master schema, cutover classes, readiness.
4. `formulas/closure`, `gstr2b`, `ppv`, `journal` with boundary tests.
5. R1 short-close, R2 closure (replace `close_case`), job; update E71, E86, E09.
6. R5, R6 standard prices and measures; control tower savings; update E29.
7. R4 GSTR-2B.
8. R7 month close and R8 export (lock route and `close_year` through it); update E87, E88.
9. R3 reconciliation gaps; screens, CLI, demo cases, docs; update the tests of 10.1.
10. Evals E176-E183 by a separate agent from this spec; one full `scripts/check.py` and all evals; both demo resets and
    walk-throughs (E33, E34, E46); section 14.

---

## 14. Phase 1 end-to-end (once blocks 9 to 13 are in)

The walk-throughs E33 (API), E34 (Apex, browser) and E46 (Brightwave, browser) are extended to show, against Plan 12.2:

| Gate | What the full walk-through must show |
|---|---|
| 1 | `scripts/check.py` green on the release commit with `test_web_shell.py` and `test_main_ui.py` |
| 2 | E01-E183 pass scripted; live E13, E15, E24, E25, E28, E140 kept in `evals/results/live.*`; no new capability here |
| 3 | Apex from the ERP demand (MO-1) to every order **closed**: request, approval by link with code, route, quotes, selection, order, supplier acceptance with code, part receipt with inspection by another person (block 9), a short-close of a pegged balance approved by the purchase manager with the need raised again (AT-14), invoice match with a released block (AT-07), settlement on the bank statement (AT-12 path), closure only then (D-12), five different people in the audit log (AT-01). Brightwave: services confirmed, a serialised laptop, an advance adjusted and the order closing after it (AT-06), IGST in its GSTR-2B. AT-02 to AT-15 each named to its eval (R.1 later makes them one suite) |
| 4 | No Critical or High defect open: DF-01 to DF-17, DF-37, DF-38 each closed by its block, with the eval that shows it |
| 5 | The break tests for C1, D42, D53, D54, D-07 and D-22 pass (E-numbers listed in `evals/README.md`) |
| 6 | Every capability switched on for the demo (planning, supplier agent, invoice agent) has its five D54 checks recorded; switching each off leaves cases, not orphans |
| 7 | Out of the walk-through: restore drill, audit protection, masking before models, provider terms (WP-R) |
| 8 | The tax adviser's review list is produced from the walk-through: TDS (2.5), advances (2.3), MSME (R3.4), GSTR-2B words (R4); finance sees PPV at `invoice` and the other two points (R6) |
| 9 | **One full month-end** on Apex with the CLI and the screens: November 2026 previewed, warnings cleared or accepted, closed; GRNI frozen; GSTR-2B compared (a file the walk-through writes from the month's invoices, one planted difference); PPV and savings shown; the final export downloaded and balanced; a document after the close landing in December |

---

## 15. Done criteria (Plan "Every package ends when", l.614; CLAUDE.md)

- `python scripts/check.py` green (ruff, mypy strict, pytest, biome, tsc), browser tests included.
- E176-E183 pass, and every existing eval (updated as 10.1 says), in one results file.
- `demo reset apex` and `demo reset brightwave` work; E33, E34 and E46 pass with the section 14 additions.
- `docs/database-schema.md` regenerated after 0200; data dictionary, guides, demo script and CLAUDE.md updated.
- Every write in this block has its audit row (section 8 list); boundary tests for every new formula (section 9).
- The gaps of 3.4 and DF-26, DF-29 are closed; the report lists each hand-over name that differed (section 4) and each
  test or eval changed, with its reason.
