# Block 11 spec: match and exceptions (flow step 11)

*Cross-block contracts with blocks 9-13 (built 9, 10, 11, 12, 13 in order): `handoff/specs/blocks9-13-interfaces.md`.*

Tasks **3.1** (D-17: price within both % and Rs, DF-12), **3.3** without its duplicate key (match locks the order
lines; a release re-checks, DF-11), **3.4** (GST rate and tax per line compared with the PO), **3.5** (a quantity block
is matched again when the goods arrive) and **WP-5** (5.1 to 5.5: one case model with owner, deadline, escalation and an
engine re-check before closing; reassignment; delivery failures and held stock as cases; failed jobs become cases; job
heartbeat). With them the invoice and AP agent (AG03) as built is brought up to the new reasons and its live result is
kept (Decision 14). Spec only; nothing here is built yet.

**Not in this block:** the duplicate-invoice key and invoices without a PO (block 10, 3.3 first half and 9a.1); receipt
reversal and its effect on invoices, inspection by another person, quarantine accepted by revision, returns, external
receipts (block 9: 3.6, 3.8, 4.2, 4.3, 4.5); payment states, runs, advances, credit and debit notes, TDS, payment
exceptions (block 12: 2.1-2.8, and 11b.4); PPV at the recognition point, GSTR-2B, accounting export, closure (block 13:
13.2, 13.3, 9b.1, 2.6, 6.6); the monitoring agent that summarises cases (11b.5, Across); budget at receipt (3.7, done).

**Sources.** `planning/P2P-Project-Plan-End-to-End.md` (**Plan**): decisions l.146-170 (l.167), steps 10-11 gap table
l.380-391, packages l.447 (WP-3), l.449 (WP-5), "Build on what exists" l.465, tasks l.499-518, l.567 (11b.4), block
order l.597-611, "Every package ends when" l.613-617, capability row l.688, AT-05, AT-07, AT-10, AT-15 (l.813-823),
DF-11, DF-12, DF-24 (l.855, 856, 870), D-17 l.1081. `planning/P2P-Plan-Agentic-Revision.md` (**Rev**) l.128, l.239,
l.429-430, l.441-442 and the five D54 checks, l.476. `planning/P2P-Phase1-Pilot-Baseline.md` (**Base**) l.205-206,
l.276, l.449-460. `planning/P2P-Screens-Brief.md` l.31, l.219-224. `planning/P2P-13-Step-Plan.md` l.41, l.104.
`planning/Block-0b-open-points.md` l.22-28, `specs/block0b-spec.md` section 3 and OP-2. `planning/Decisions-22-Nov.md`
#10, #14. **Code read:** worktree `p3-b8` at 8ac2221 (blocks 0 to 8; tenant migrations to 0150, evals to E143).

*RECOMMENDED* marks a default this spec proposes because the documents are silent. **NEEDS OWNER** marks a point that
would change an approved decision; none does here (section 12).

---

## 1. What the decisions say

| Source | Text |
|---|---|
| D-17 (Plan l.1081, Rev l.555) | "Quantity tolerance 0%; price within both % and Rs" |
| DF-12 (Plan l.856) | "Price passes within % OR Rs; D-17 says both" (task 3.1) |
| DF-11 (Plan l.855) | "Double release; concurrent match can over-invoice" (task 3.3) |
| Plan 3.3, 3.4, 3.5 | "Match locks order lines; release re-checks"; "GST rate and tax per line compared with the PO"; "Quantity block re-matched on arrival" |
| AT-05 | "Partial receipt and invoice (1,000 ordered, 400 in): invoiced never exceeds accepted, even with concurrent matching" |
| AT-07 | "Invoice mismatch and credit note: release by someone other than the enterer; no double release" |
| Plan l.167, Rev l.476 | "Release an invoice block: Approver, never who entered or asked for it"; "Confirm invoices; release blocks: AP; approver" |
| CLAUDE.md rule 5, Screens l.31 | Releasing a payment block and accepting a price variance are proposals a person approves |
| Rule 7 | Requester, approver, receiver, invoice approver and payer differ for the same case |
| Rev l.429, l.441 (AG03) | "Read into a draft; investigate blocks; draft supplier queries; propose fixes. Never: release blocks; accept variances; touch payments. AP confirms; approver releases" |
| Plan 5.1-5.4 | "C5 case model: new kinds of `task` with owner, deadline, status, engine re-check before closing" (no second case table); "N-3 reassignment and escalation in working days"; "N-4 delivery failures and held quantities as cases"; "Failed jobs become cases; job heartbeat" |
| DF-24 (Plan l.870) | "Long job can be taken by a second worker" (5.4) |
| Rev l.430, l.442 | Monitoring: "Open, route and escalate cases ... Never: close a case without the engine re-checking" |
| Screens l.224 | "Every stuck item: what, reason, owner, next step, age in working days, amount ... *New:* cases have an owner, a deadline and escalation; reassign; the engine re-checks before a case closes; failed background jobs become cases" |
| Plan l.389, 11b.7 | AG03's live result kept in `evals/results/`, threshold written before switch-on (D54 check 3) |
| Block 0b OP-1 | A match uses the tolerance version in force on the invoice's received date; a re-match keeps it |

What the documents leave open is in section 12 (OP-1 to OP-20).

---

## 2. What exists today

| Part | Where | Behaviour |
|---|---|---|
| Line rule | `formulas/matching.judge_line` | Price judged first: passes when `diff <= order_price x price_pct / 100` **or** `diff x qty <= price_abs` (DF-12; the test `test_the_price_limits_are_inclusive_and_either_one_lets_the_line_through` fixes the "or"). A price block returns at once, so a quantity problem on the same line is not recorded. Quantity: `qty <= available x (1 + qty_pct/100)`. Limits inclusive. Service line: `service_not_confirmed` |
| Header and sums | `header_problem`, `check_arithmetic` | Composition tax, IRN, GSTIN; line within 0.02, total within 1.00. **GST rate and tax per line are never compared with the order** (3.4) |
| Match | `invoices/service.match_invoice` | Locks the invoice row only; `available` = `po_line.received_qty` less booked invoices (`approved, part_paid, paid`) less earlier lines of this invoice. Two invoices matched at once both see the same goods (DF-11). One reason per line (`reason_codes`); the invoice's `block_reason` is the first blocking line. Writes `match_result` with `tolerance_version`; audit `invoice.match` with `rule_version = 'tolerance vN'` (block 0b) |
| Tolerances | `invoices/tolerances.py` | Versioned as a set; Apex v2 from 1 Jul 2026: `price_pct 2.00`, `price_abs 300.00`, `qty_pct 0.00`; Brightwave v1 2%, Rs 500, 0%; `small_diff_abs` exists and is unused |
| Release | `service.request_release`, `@executor("invoice.release_block")` `_release` | Any block but a duplicate; required `invoice.approve` (Finance and the Approver role), no value, no named person. Executor refuses the enterer and the requester or service confirmer (`asked_or_confirmed`); **not** the receiver or the order's makers. It does **not** re-check what may still be invoiced, so two price-blocked invoices for the same goods can both be released (DF-11); it commits inside the executor. A second pending release is refused by a check-then-insert (race) |
| Re-match | `rematch_invoice` (button), `receiving/services._rematch` | A service confirmation re-matches invoices held `service_not_confirmed`. **A goods receipt or inspection re-matches nothing** (3.5) |
| Cancel | `cancel_invoice` | Blocked or received only, reason needed |
| AG03 | `agents/invoice_exceptions.py` | `diagnose` (rules) and `explain` (model, kept only if same cause and grounded), `invoice_diagnosis` rows, vendor email drafted ("copy it into your email; nothing is sent"). `classify` knows price, quantity and header reasons only: a `service_not_confirmed` hold is diagnosed as **arithmetic**. `GET /api/invoices/{no}/diagnosis` has no permission check |
| Invoice API | `platform/invoice_api.py` | `GET /api/invoices`, `GET /api/invoices/{no}` need only a login (any role reads every invoice); release and re-match need `invoice.match` |
| Exceptions | `platform/exceptions.py`, `GET /api/exceptions` (`workspace_api`) | Read model: invoice blocks, goods without an order, services past due, approvals past reminder, proposals, supplier alerts, orders with no approver, demand and late-supply cases, AI hand-overs, block 6 and 8 tasks. Owner is text; no deadline, assignee or escalation for rows that are not tasks; no reassignment; `email_failed` tasks and held stock are not listed |
| Tasks | `platform/tasks.py` | Assignee, SLA hours on the business calendar (9:00-18:00), escalation chain, `task_escalate` job. No reassignment, no re-check before completion |
| Worker | `platform/worker.py` | Control-database jobs; a job fails after 5 attempts and nobody is told; a running job is taken again once `locked_at` is 5 minutes old, even if it is still running (DF-24) |
| Outbox | `platform/outbox.sweep` | A mail queued over 24 hours fails and opens an `email_failed` task (not on the exceptions screen) |
| Held stock | `receiving/quality.py` | Quarantine and blocked holds with a decision by `stock.quality`; not on the exceptions screen |
| Approval rule | `approval_rule` document `price_variance` | Apex VAR-01 purchase manager, Brightwave VAR-01 CFO, any value; used today only for a supplier's price rise on an order (`orders/confirmation._price_approver`) |

---

## 3. Pre-implementation check (Plan 9.3)

### 3.1 Data

| Need | Apex Pumps | Brightwave | Enough? |
|---|---|---|---|
| Contract items at known prices and GST, one intra-state supplier | CNT-0001 (SUP-001, MH): RM-02 480.00 (18%), RM-03 220.00 (5%), RM-05 180.00 (5%), no inspection | CNT-0001, CNT-0002 | Yes |
| Inter-state supplier (IGST) | SUP-002 (GJ) RM-06 38.00 (18%), inspection required | SUP-002 (TN) | Yes |
| Tolerance versions | v1 (2%, Rs 500, 5%), v2 from 1 Jul 2026 (2%, Rs 300, 0%) | v1; v2 from 1 Jan 2027 | **Gap:** no `tax_line_abs` key |
| History edges | `tolerance_edges.price`: Rs 400 over on a small line, 3 to 6% over, "within v1" only through the rupee test | - | **Gap:** under "both" the March edge (PI-2526-000174) is blocked; truth and demo script say it passes |
| Planted GST errors | none | none | **Gap:** E19/E25 never see a GST-per-line block |
| Approver of a price variance | VAR-01 purchase manager (U08) | VAR-01 CFO (U09) | Yes |
| Held stock | seals 20 in quarantine, bearings 8 blocked (block 2 demo) | none | Yes |
| Managers for escalation | U04 -> U05 -> U09 (AP); U03 -> U08 -> U07 | per `user.csv` | Yes |

**Generator (data first):**
1. Both `company.yaml`, tolerance v1 values: `[tax_line_abs, "1.00", GST on an invoice line may differ from the order's
   by at most this many rupees per tax]`. Rename the `price_abs` note to "And at most this many rupees per line".
2. `history_money.judge` and `history_check` use **both** tests. Re-plant `tolerance_edges.price` (Apex): a line worth
   Rs 15,000 to 26,000, 1.5% to 1.9% above the order price, Rs 301 to 499 over on the line: within v1 (March), blocked
   by v2's Rs 300 (August). Every history invoice is re-judged; statuses follow.
3. Apex `planted: gst_mismatch: 2`: one line billed at 18% for a 5% item, one line's CGST Rs 5.00 above; each blocked,
   cancelled and followed by a corrected invoice that matches (the `qty_mismatch` pattern).
4. `truth/truth_match_expected.csv` regenerated, gains `reasons` (all, `;`-joined) and `rules` (2).
5. New `truth/truth_match_block11.csv` (`scenario,figure,value`) by the generator's own arithmetic for scenarios M, X, N
   and H of section 9 (line amounts, GST, totals, variances, available quantities after each step).
6. `docs/demo-script.md` l.131 rewritten for the new edge.

### 3.2 Configuration

No new rule table. New tolerance key `tax_line_abs` (versioned; code default `1.00` when a version lacks it). New
setting `case_due_days` (2). The `price_variance` approval rows exist in both companies. Capability `invoice_ap` exists
(AI foundation), on in both demo companies.

### 3.3 Today's behaviour

E19, E21, E25, E43, E44, E55, the walk-throughs E33, E34, E46 and `tests/test_invoices.py`, `test_tolerance_versions.py`,
`test_exceptions.py`, `test_invoice_exceptions_agent.py`, `test_service_entry.py`, `test_security.py`,
`test_permissions.py`, `test_demo_populate.py` pass on the branch with blocks 9 and 10 before anything changes; those changed on purpose: 14.1.

### 3.4 Gaps

DF-12 (11.1), GST per line (11.2), locks and re-check (11.3, DF-11), re-match on arrival (11.4), release by the rule
(11.5), cases (11.6-11.8), AG03 (11.9), invoice read permissions (11.10). Section 13 lists them against the code.

---

## 4. Boundaries with blocks 9, 10, 12, 13

| # | Other block | Contract |
|---|---|---|
| H-1 | Block 9 (receipt, built before) | Every path that **raises** an order line's accepted quantity (receipt, inspection, quarantine accepted by revision, external receipt, service confirmation) calls `invoices.rematch.after_accepted(db, po_no, line_nos, actor)` after its commit. Block 9 calls nothing of block 11: **block 11 adds every call**, to `receiving/goods.post_receipt`, `goods.inspect`, `receiving/services.confirm` (replacing `services._rematch`) and to block 9's paths: the `receipt.excess_accept` executor's after-commit step (`receiving/excess.py`) and each receipt of `receiving/erp_receipts.import_receipts`. A replacement after a return is an ordinary receipt |
| H-2 | Block 9 (reversal, returns) | A receipt reversal that **lowers** accepted quantity is refused by block 9 when booked invoices would exceed it; block 11 guarantees that a later match or release counts the lowered quantity (it reads `po_line.received_qty` under the line lock), so a reversal and a match on one line run one after the other. A counted return (block 9 9.5.3) may leave invoiced above accepted on purpose: that difference is block 9's recovery claim (block 12's debit note), not a match error |
| H-3 | Block 10 (intake, built before) | `enter_invoice` stays the one entry; block 10 added the duplicate key and non-PO invoices. A non-PO invoice is never three-way matched (no `match_invoice` call); its approval is block 10's; its block is never released (block 10's refusal in `request_release` moves with it to `invoices/release.py`). Block 10's `header_problem` owns the IGST against CGST plus SGST test (`tax_head`); 11.2 checks rate and amounts only. Block 10's `invoice.approved_by` is set here on a release (11.5.5) |
| H-4 | Block 12 (payment) | A released or matched invoice is `approved`, `payment_block 'none'`, booked by `payment.book_invoice` as today. Payment holds (bank, advance, MSME) and their release are block 12's; a payment failure reopening an invoice is block 12's |
| H-5 | Block 13 (PPV) | Accepted price variance is kept per line (`match_result.variance_value`, `resolution_kind 'price_accepted'`) and per invoice (`invoice.variance_accepted`); block 13 reads them for PPV and leaves them unchanged |
| H-6 | All | New exception kinds of other blocks join through `exceptions.collect` (a source returning `Stuck`); the case sweep (11.6) then gives them an owner and a deadline with no extra code |

**Files shared:** `receiving/goods.py`, `receiving/services.py` (one call each, H-1), `platform/exceptions.py`,
`invoices/service.py` (blocks 10, 12), `platform/worker.py` (AI foundation).

---

## 5. Requirements

### 11.1 Price within both % and Rs (3.1, DF-12, D-17)

1. Per invoice line, with `diff = (unit_price - discount) - order_price` (exact Decimal, no rounding):
   - `diff <= 0`: the price matches;
   - else **within** only when `diff <= order_price x price_pct / 100` **and** `diff x qty <= price_abs`; both inclusive;
   - else **blocked** `price_variance`.
2. `price_abs` stays per line (OP-1). `qty_pct` unchanged (0 at Apex v2 and Brightwave, D-17).
3. The line's rupee variance `variance_value = round_half_up(diff x qty, paise)` is stored for every line with
   `diff > 0`, within tolerance or not.
4. Price and quantity are **both** judged on every line; a line keeps every reason it has (11.3.5).

### 11.2 GST rate and tax per line (3.4)

1. For each invoice line, from the order (`po_line.gst_rate`, `po.supplier_gstin` state, `po.buyer_gstin` state) and
   the supplier's registration, the expected tax is `formulas/gst.gst_split(invoice line taxable, order rate, ...)`.
2. Blocked `gst_mismatch` (block kind `tax`) when, on any line:
   - the invoice rate differs from the order rate: "line `<n>`: GST `<r>`% on the invoice, `<s>`% on the order";
   - any of CGST, SGST, IGST is more than `tax_line_abs` away from the expected: "line `<n>`: CGST `<x>` against `<y>`
     expected (`<d>` apart, more than `<t>`)". Exactly `tax_line_abs` apart is within.
3. Reverse-charge invoices: the rate is compared, the amounts are not. A composition supplier stays the header `TAX`
   rule. A line entered without tax amounts gets them computed (as today) and so always passes 2. IGST charged where
   CGST and SGST are due, or the reverse, is block 10's header reason `tax_head` (kind `tax`, its words); when it is
   present the amounts are not compared per line (one reason, not two).
4. A pure function `formulas/matching.judge_gst(order_rate, invoice_rate, expected, charged, tol) -> list[str]`.

### 11.3 The match locks the order lines; reasons recorded (3.3)

1. `match_invoice` locks the invoice row, then its order lines `for update` in `(po_no, line_no)` order, then computes
   `available` (OP-8). Any service that changes `received_qty` or books an invoice on a line takes the same row lock, so
   two matches, a match and a receipt, or a match and a release on the same line run one after the other.
2. Invariant: for every order line, the quantity on invoices `approved, part_paid, paid` never exceeds
   `po_line.received_qty` (accepted), whatever runs at once (AT-05); the one exception is a counted return after
   invoicing (H-2), held by its recovery claim.
3. Every match records `rules = 2` on its `match_result` rows and the invoice (`match_rules`); the audit `invoice.match`
   gains `rule_version = 'tolerance vN; match rules 2'`. Rows written before this block keep `rules = 1` (OP-5).
4. A re-match keeps the tolerance version of the first match (block 0b) and uses rules 2.
5. `match_result.reasons` holds every reason of the line, in this order: `price_variance`, `qty_over_received`,
   `service_not_confirmed`, `gst_mismatch`; `reason_codes` keeps the first (existing readers). The invoice's
   `block_reason` lists one sentence per reason; `payment_block` is `tax` if any reason is a tax reason, else `match`.

### 11.4 Matched again when goods arrive (3.5)

1. `invoices/rematch.after_accepted(db, po_no, line_nos, actor)`: every invoice that is `blocked` with an open
   `qty_over_received` or `service_not_confirmed` reason on one of those lines is matched again, **oldest received
   first** (`received_on`, then number; OP-6), each in its own transaction, actor `service:rematch`. Returns the numbers
   now approved.
2. Called after the commit of a receipt (`POST /api/purchase-orders/{no}/receive`), an inspection
   (`POST /api/receipts/{no}/inspect`), a service confirmation, an accepted excess and an ERP receipt import (H-1). Their responses gain `rematched: [numbers]`.
3. A re-match never approves beyond what is accepted (11.3.2). Price-blocked invoices are not re-matched by an arrival.
4. Audit `invoice.match` with `reason "goods accepted on <GRN>"`.

### 11.5 Releasing a block and accepting a price variance (rule 5, AT-07)

1. `POST /api/invoices/{no}/release` (`invoice.match`, reason required) on a `blocked` invoice:
   - any open `qty_over_received` or `service_not_confirmed` reason: **refused** (409) "a quantity or service hold is
     cleared by a goods receipt, a service confirmation or a corrected invoice; it is never released" (OP-3);
   - a duplicate: refused as today;
   - `price_variance` among the reasons: a proposal to **accept the price variance**. `variance` = the sum of the
     blocked price lines' `variance_value`. The approver is the first person the `price_variance` approval rule names
     for that value on the business date (as `orders/confirmation._price_approver`), excluding the people of 11.5.3; the
     proposal carries `approver`, `delegate`, `variance`, the lines; `value = variance`, no `document` (no user limit
     row is needed, as on the order side). If the rule names nobody eligible: 409 "the price variance rule names nobody
     who may accept this price; the rule has to name someone else";
   - only tax or arithmetic reasons: a release proposal as today (any `invoice.approve` holder not excluded).
   One action, `invoice.release_block`; one pending per invoice, now by a unique index (migration 0180).
2. Summary words: "Accept the price variance of `<Rs x>` on `<invoice>` (`<item>`: `<invoice price>` against
   `<order price>` on `<po>`) and pay it. Reason: `<reason>`" or "Pay `<invoice>` despite the block (`<reasons>`).
   Reason: `<reason>`".
3. **Who may never approve** (guard, 403, the proposal stays pending): who entered the invoice; who asked for the
   release; a requester or on-behalf person of a line, or who confirmed its service; who received or inspected the goods
   of a line (rule 7, new); for a price variance, a maker of the order (C1, `orders/workflow.makers`, new) and anyone but
   the named approver or their deputy.
4. **The executor re-checks** (DF-11): under the invoice and order-line locks it re-runs the match with the proposed
   lines' price accepted; if anything else blocks (the goods were invoiced meanwhile, a new reason) it raises "`<invoice>`
   can no longer be released: line `<n>` (order line `<m>`): `<q>` more than can be invoiced (`<other invoice>` took the
   goods since); match it again". The proposal is `approved` with execution `failed`; the invoice stays as it was (OP-7).
5. On success: invoice `approved`, `payment_block 'none'`, booked (`payment.book_invoice`); its open blocked
   `match_result` rows resolved (`resolved_by`, `resolution` = the reason, `resolution_kind` `price_accepted` for price
   lines, else `released`); `invoice.variance_accepted = variance`; `invoice.approved_by` (block 10's column) = the
   proposal's approver; audits `invoice.release_block` and, for a variance,
   `invoice.variance_accepted` (`variance`, `rule_version 'VAR-01 v1'`). The executor no longer commits
   (`proposals.approve` commits the effect with the execution status, task 0.5).
6. The asker is told the outcome (notification): approved, rejected with the reason, or not released with the reason.
7. Readiness: `SEP-17 "an invoice price variance accepted"` (SEP-16 is block 9's), makers `invoice.book`, checkers `chain:price_variance`.
8. The exceptions row of an invoice waiting for a price acceptance names that person (`people`), owner "`<role>`:
   `<name>`"; a release of other blocks keeps today's owner text.

### 11.6 Every stuck item is a case (5.1, 5.5)

1. A **case** is a `task` row with `case_key` (`<kind>:<ref>`, e.g. `invoice:PI-2627-000011`); at most one open case per
   key (unique index). Rows of `exceptions.collect` that already come from a task (demand, late supply, block 6 and 8
   tasks, AI hand-over, approval steps through `approval_step.task_id`, `email_failed`, `job_failed`, block 9's
   `receipt_excess` and `return_outcome`, block 10's `invoice_authorise`, `invoice_approve` and `invoice_sender_check`)
   are that task; it gets the `case_key`. Rows that are not tasks (block 9's `inspection` and `return_draft` among them)
   get a case from the sweep.
2. **Sweep** `platform/exception_cases.sweep(db, tenant, now)`, job `case_sweep` hourly (self-rescheduling,
   `enqueue_unique`) and CLI `python -m p2p.cli cases sweep <tenant>`:
   - each row with no open case gets one: kind `exception`, title = the row's reason, `step` = the row's owner step,
     assignee by 11.6.3, SLA `case_due_days` x 9 working hours on the company calendar, chain = the assignee's manager,
     then that manager's manager; in-app notice only, no email (OP-10); audit `case.open`;
   - each open `exception`, `email_failed` or `job_failed` case whose row is gone is completed: `status 'done'`,
     `resolution 'resolved'`, completed by the system; audit `case.resolved`;
   - when a row's owner step changes (e.g. AP asked for a release, the named approver now acts) the case moves to the
     new step's assignee, due time restarted; audit `case.reassign` by the system with `why: "next step"`;
   - a second run with nothing changed writes nothing.
3. **Assignee:** a person named on the row (`people`, e.g. the requester of a service, the named price approver) first;
   else the document's own person when they may act (the invoice's enterer, the order's buyer, the receipt's poster);
   else the first active login that may act (`_may_act`). Nobody: the first Admin, title prefixed "No one may act: ".
4. **Re-check before closing** (Rev l.442): `POST /api/cases/{task_id}/recheck` runs the row's source now; gone:
   resolved as in 11.6.2; still stuck: 409 "still stuck: `<reason>`", nothing written. A person never closes an
   `exception` case by hand (OP-12). Cases with their own outcomes (demand, late supply, and blocks 9 and 10's tasks, closed by their own act) keep their
   close.
5. The exceptions screen rows gain the case: `task_id`, assignee, due, overdue, escalation level.

### 11.7 Reassignment and escalation in working days (5.2, N-3)

1. `POST /api/cases/{task_id}/reassign {to, reason}`: by the assignee or a person in the case's chain above them; the
   new person must be able to act on the row (named, or holds its permission and is not refused by segregation): else
   409 "`<name>` cannot act on this: needs `<permission>`". Reason required (422). Due restarts from now, chain
   recomputed from the new assignee, level 0; notice to both; audit `case.reassign` (`from`, `to`, `reason`).
2. `GET /api/cases/{task_id}/candidates`: the people who may take it.
3. Escalation unchanged (`task_escalate`), now for every case: due time in working hours on the company calendar.

### 11.8 Delivery failures, held stock and failed jobs (5.3, 5.4, N-4, DF-24)

1. **Failed email:** `email_failed` tasks become exception rows (kind `email_failed`, act by the mail's document:
   `po` and `rfq` -> `po.send`, `invoice` -> `invoice.match`, else `admin.settings`), next step "Check the address in
   supplier records and send the document again from its page". Resolved by the sweep when a later mail for the same
   document has been sent; or closed by its assignee with outcome `handled_otherwise` and a required note.
2. **Held stock:** each open hold (`stock_hold`, `left_qty > 0`, not closed) is a row, except holds another row already
   covers (block 9): source `excess` (its `receipt_excess` task), a receipt's quarantine waiting for inspection (its
   `inspection` row) and a hold on a draft return (its `return_draft` row). Row kind `held_stock`, act
   `stock.quality`, since `held_at`: quarantine "Decide the hold: release to usable or block"; blocked "Return it to
   the supplier or scrap it" (the return is block 9's). Resolved when the hold is closed.
3. **Failed job:** when a job fails its last attempt, the worker opens one task kind `job_failed` in that company
   (`case_key job:<id>`, the first Admin, SLA 8 working hours, payload job id, kind, attempts, last error). A job that
   fails again while its case is open adds to `failures` in the payload, no second case. Row kind `job_failed`, act
   `admin.settings`, next "Read the error, fix the cause, then Run again". `POST /api/cases/{task_id}/requeue`
   (`admin.settings`): the job back to `queued`, attempts 0, due now; audit `job.requeue`. Resolved by the sweep when the
   job is `done`.
4. **Heartbeat (DF-24):** while a handler runs, the worker refreshes the job's `locked_at` every 60 seconds
   (`worker.heartbeat(job_id, now)`); a running job is taken again only when `locked_at` is older than 5 minutes. No
   control migration (OP-13).

### 11.9 The invoice and AP agent (AG03)

1. Causes added: `service_not_confirmed` (fix `wait_for_confirmation`, "Wait for the requester to confirm the service;
   the invoice is matched again by itself") and `gst_mismatch` (fix `corrected_invoice`). `classify` reads
   `match_result.reasons`; the primary cause follows the order header, service, quantity, GST, price; the vendor query
   names every problem. `wait_for_receipt` reads "Wait for the goods; the invoice is matched again when they are
   accepted".
2. **AP sends the query** (OP-15): `POST /api/invoices/{no}/query {subject, body}` (`invoice.match`, invoice blocked):
   the edited draft through the outbox to the supplier's order contact (`masters/suppliers.order_contact`, block 8),
   reply-to `invoice_receipt_mailbox`, linked to the invoice; `invoice_diagnosis` row gets `query_email_id`, by, at;
   audit `invoice.query_sent`. The agent never sends.
3. Unchanged: the model only explains; nothing it says changes an invoice; the rules' cause is the floor.
4. **D54 checks for `invoice_ap`:** (1) its setting by two admins (built); (2) permission tests: explain, diagnosis and
   query need `invoice.match` (diagnosis read: also `invoice.view`, `invoice.approve`), another company never sees them;
   (3) live result kept: `python evals/run.py --live --only E25,E167 --out evals/results/live`, threshold written before
   switch-on: at least 80% of model explanations kept (same cause, grounded), 0 kept explanations with an ungrounded
   number, link or "released" claim (OP-17); (4) tracing (built); (5) switch-off: only the rules' diagnosis, no model
   call; nothing open to hand over.

### 11.10 Who reads invoices (OP-16)

`GET /api/invoices` and `GET /api/invoices/{no}` keep block 10's rule (10.11: `invoice.view`, `invoice.book`,
`invoice.match` or `invoice.approve`, or a bill's own authoriser and step approvers); `GET /api/invoices/{no}/diagnosis`
takes the same check, else 403.

---

## 6. Settings and rules (data)

| Key | Where | Default | Use |
|---|---|---|---|
| `price_pct`, `price_abs`, `qty_pct` | tolerance, versioned | as today | 11.1, now both tests |
| `tax_line_abs` | tolerance, versioned (new key) | 1.00 | 11.2: per tax head per line |
| `case_due_days` | `org_setting` (new) | 2 | Working days to act on a case before it escalates |
| `invoice_block_alert_days` | existing | 3 | Alert, unchanged |
| `price_variance` rows | `approval_rule`, versioned | VAR-01 (both companies) | Who accepts an invoice price variance |

A tolerance or rule change stays a two-admin change (D-22, decision 10).

---

## 7. Data: migration 0180 `match_and_cases` (down_revision 0170, block 10)

```sql
alter table match_result add column reasons text[] not null default '{}', add column rules smallint not null default 1,
  add column variance_value numeric(18,2), add column resolution_kind text
  check (resolution_kind in ('price_accepted', 'released'));
update match_result set reasons = array[reason_codes] where reason_codes is not null;
alter table match_result alter column rules set default 2;
alter table invoice add column variance_accepted numeric(18,2) not null default 0, add column match_rules smallint;
update invoice set match_rules = 1 where tolerance_version is not null;
alter table task add column case_key text, add column step text, add column resolution text;
create unique index ux_task_open_case on task (case_key) where case_key is not null and status in ('open', 'overdue');
create unique index ux_proposal_open_release on proposal ((payload->>'invoice'))
  where action = 'invoice.release_block' and status = 'pending';   -- fails loudly if two are pending today
alter table invoice_diagnosis add column query_email_id int references email_message (id),
  add column query_sent_by int, add column query_sent_at timestamptz;
insert into org_setting (key, value, description) values ('case_due_days', '2',
  'Working days to act on a stuck item before it moves to the manager') on conflict do nothing;
```

Down: drop indexes and columns, delete the setting. Then `scripts/gen_schema_doc.py`; `docs/data-dictionary.md` rows
for each new column; cutover classifies nothing new (no new table).

### 7.1 Code (one job each; reuse first)

| Module | Change |
|---|---|
| `formulas/matching.py` | both tests; `judge_line` returns every reason and `variance_value`; `judge_gst` (pure) |
| `invoices/service.py` | line locks; reasons; rules 2; GST per line; `block_reason` per reason; release code moves out |
| `invoices/release.py` (new) | `request_release` (keeps block 10's refusal for bills), approver by the `price_variance` rule, guard, executor with re-check, `approved_by` |
| `invoices/rematch.py` (new) | `after_accepted`; calls added to `receiving/goods.py`, `receiving/services.py`, `receiving/excess.py`, `receiving/erp_receipts.py` (H-1) |
| `invoices/tolerances.py`, `setup_api.py` | default and screen entry for `tax_line_abs` |
| `platform/exception_cases.py` (new) | sweep, assignee, recheck, reassign, candidates, requeue, close (`email_failed`) |
| `platform/exceptions.py` | sources `email_failed`, `held_stock`, `job_failed`; `task_id` on task rows; invoice rows by reasons |
| `platform/worker.py` | heartbeat; on final failure the `job_failed` case |
| `workspace_api.py`, `invoice_api.py`, `agent_api.py` | case endpoints and fields; permissions (11.10); `query` |
| `agents/invoice_exceptions.py` | causes from `reasons`; `send_query` (AP's act through the outbox; never the agent's) |
| `readiness.py`, `cli.py`, `demo_cases.py`, `datagen/` | SEP-17; `cases sweep`; the demo release waits for U08; section 3.1 |

No new agent tool.

---

## 8. Public interface (for the eval writer)

All `/api` under the tenant session, JSON; errors 422 (input), 409 (state), 403 (permission or segregation), 404 (not in
this company).

| Endpoint | Body | Success | Errors |
|---|---|---|---|
| `POST /api/invoices` (existing) | as today | adds `reasons` (all, invoice level) | as today |
| `GET /api/invoices/{no}` | - | per match line adds `reasons[]`, `variance_value`, `rules`, `gst {order_rate, invoice_rate, expected {cgst, sgst, igst}, charged {...}}`; invoice adds `variance_accepted`, `release {allowed, why_not, kind: price|other, variance, approver_name}` | 403 (11.10), 404 |
| `POST /api/invoices/{no}/release` | `{reason}` | `{proposal_id, status, kind, variance, approver}` | 409 quantity or service hold, duplicate, not blocked, already waiting, rule names nobody; 422 no reason |
| `POST /api/admin/proposals/{id}/approve` (existing) | `{}` | as today; execution `failed` with the 11.5.4 words when the re-check fails | 403 guard (11.5.3) |
| `POST /api/invoices/{no}/rematch` (existing) | - | as today | - |
| `POST /api/invoices/{no}/query` (new) | `{subject, body}` | `{email_id, to}` | 409 not blocked; 422 empty; 403 |
| `POST /api/purchase-orders/{no}/receive`, `POST /api/receipts/{no}/inspect`, service confirm (existing) | as today | add `rematched: [invoice numbers approved]` | as today |
| `GET /api/exceptions` (existing) | - | each row adds `case {task_id, assignee, assignee_name, due_at, overdue, level}`; kinds add `email_failed`, `held_stock`, `job_failed` | - |
| `POST /api/cases/{task_id}/recheck` (new) | - | `{resolution: "resolved"}` | 409 "still stuck: ..." |
| `POST /api/cases/{task_id}/reassign` (new) | `{to, reason}` | `{assignee, due_at}` | 403 not assignee or above; 409 cannot act; 422 no reason |
| `GET /api/cases/{task_id}/candidates` (new) | - | `[{user_id, name}]` | 403 |
| `POST /api/cases/{task_id}/requeue` (new) | - | `{job_id, status: "queued"}` | 409 not a failed job; 403 without `admin.settings` |
| `POST /api/cases/{task_id}/close` (new, `email_failed` only) | `{outcome: "handled_otherwise", note}` | `{resolution}` | 409 other kinds; 422 no note |

Tables the eval may read: `invoice (status, payment_block, block_reason, tolerance_version, match_rules,
variance_accepted)`, `match_result (result, reasons, reason_codes, price_var, qty_var, variance_value, rules,
tolerance_version, resolution_kind, resolved_by)`, `po_line (received_qty, invoiced_qty)`, `proposal`, `task (kind,
case_key, step, assignee, due_at, escalation_level, status, resolution)`, `invoice_diagnosis`, `email_message`,
`audit_event`; the control `job` table (status, attempts, locked_at).

Audit actions: `invoice.match` (`rule_version`), `invoice.release_block`, `invoice.variance_accepted`,
`invoice.query_sent`, `invoice.diagnose`, `proposal.*`, `case.open`, `case.resolved`, `case.reassign`, `case.close`,
`job.requeue`, `task.create`, `task.escalate`, `task.overdue`, `task.complete`.

### 8.1 Screens (original ProcureOS look and classes; no redesign)

- `ui/js/views/invoices.js` (AP, Finance, approvers): every reason per line; a GST cell per line (order rate, invoice
  rate, expected and charged tax when they differ); "Judged under tolerance version N and match rules 2". Actions:
  **Accept the price and pay** (price only; shows the variance and "decided by `<name>` (`<role>`, rule VAR-01)"), **Ask
  to pay it anyway** (tax or arithmetic), neither for a quantity or service hold, which shows "Waits for the goods (or
  the service confirmation); matched again by itself". **Send the query to the supplier** with the editable draft.
- `ui/js/views/exceptions.js` (everyone): Assigned to, Due (overdue badge), escalation level; **Check again**,
  **Reassign** (candidates list, reason), **Run again** (failed job, admin), **Close** with a note (failed email).
- My work: "Price variances to accept" for the named approver (the existing "Blocked invoices to release" entry).
- Admin (`frontend/`): Tolerances card lists `tax_line_abs`; Approvals card of a release shows variance and lines.

### 8.2 Emails

- Price variance asked: the named approver, "Accept a price variance on `<invoice>`: `<Rs x>`", Approvals link.
- Release decided: the asker, "`<invoice>` was released by `<name>`" / "rejected: `<reason>`" / "could not be released:
  `<why>`".
- Query: the supplier's order contact, AP's subject and body, reply-to the invoice mailbox.
- Case escalated or reassigned: existing task notices. Job failed: first Admin, "A background job failed: `<kind>`".

### 8.3 Docs

Guides (each within 50 lines): `ap-clerk.md` (both tests, GST per line, holds that wait for goods, the query, cases),
`approver.md` (accepting a price variance), `finance.md` (tax releases only), `store.md` (held stock cases),
`admin.md` (failed jobs, Run again). Demo script step 14 (the seal variance goes to purchasemanager@, as today) and
l.131; data dictionary; schema doc; CLAUDE.md "Where things are" (`invoices/release.py`, `invoices/rematch.py`,
`platform/exception_cases.py`) and the latest migration number.

---

## 9. Scenarios and worked figures

Common: `demo reset apex`; business date pinned; role logins (ap@ U04 enters invoices, store@ U02 receives, buyer@ U03
makes orders, purchasemanager@ U08, finance@ U05, director@ U09); company calendar for cases (9:00-18:00, Mon-Fri, no
holiday 3-15 Dec 2026). Tolerance v2 applies (received from 1 Jul 2026): 2%, Rs 300, quantity 0%, `tax_line_abs` 1.00.

**M** (order as block 8's P): PO to SUP-001 (MH to PLT-CKN, CGST and SGST): line 1 RM-02 100 x 480.00 (18%), line 2 RM-03
200 x 220.00 (5%), line 3 RM-05 150 x 180.00 (5%); taxable 1,19,000.00, CGST 6,095.00, SGST 6,095.00, total 1,31,190.00;
approved by U08, sent. Receipt R1 Thu 3 Dec: line 1 100, line 2 120, line 3 150, all accepted. Pinned Mon 7 Dec; ap@
enters, in this order:

| Inv | Supplier no. | Lines | Taxable, CGST, SGST | Expected |
|---|---|---|---|---|
| A | DC/2612/0301 | line 1 100 @ 483.01 | 48,301.00; 4,347.09; 4,347.09 | blocked `price_variance`: 3.01 <= 9.60 but 301.00 > 300.00; variance 301.00 |
| B | DC/2612/0302 | line 2 10 @ 224.40 | 2,244.00; 56.10; 56.10 | within tolerance, approved: 4.40 = 2% exactly, 44.00 <= 300 |
| C | DC/2612/0303 | line 2 10 @ 224.41 | 2,244.10; 56.10; 56.10 | blocked `price_variance`: 4.41 > 4.40 (44.10 <= 300); variance 44.10 |
| D1 | DC/2612/0304 | line 3 150 @ 180.00 at 12% | 27,000.00; 1,620.00; 1,620.00 | blocked `gst_mismatch` "GST 12% on the invoice, 5% on the order" |
| D2 | DC/2612/0305 | line 3, 5%, CGST 676.01, SGST 675.00 | 27,000.00 | blocked `gst_mismatch` (1.01 apart) |
| D4 | DC/2612/0307 | line 3, IGST 1,350.00 | 27,000.00; IGST 1,350.00 | blocked `tax_head`, kind `tax`: "IGST was charged on a supply within Maharashtra (27): CGST and SGST are due" (block 10's header check) |
| D3 | DC/2612/0306 | line 3, 5%, CGST 676.00, SGST 676.00 | 27,000.00 | matched, approved (1.00 apart: within) |
| E | DC/2612/0308 | line 2 190 @ 220.00 | 41,800.00; 1,045.00; 1,045.00 | blocked `qty_over_received` 80 (available 120 - 10 = 110) |

Then: release asked on E -> 409 (quantity hold). Tue 8 Dec store@ receives R2 line 2 80 -> response `rematched [E]`, E
approved (available 200 - 10 = 190); C not re-matched. ap@ asks release of C -> proposal for U08 (variance 44.10); U08
approves -> execution failed "... line 1 (order line 2): 10 more than can be invoiced (DC/2612/0308 took ..."; C still
blocked; ap@ matches C again -> reasons `price_variance`, `qty_over_received`. ap@ asks release of A -> proposal for U08,
value 301.00; finance@ approves -> 403 (not the named person); ap@ approves -> 403; store@ (received the goods) holds no
`invoice.approve`; U08 approves -> A approved, `variance_accepted` 301.00, `resolution_kind price_accepted`, due date
set. Invoiced quantities at the end: line 1 100, line 2 200, line 3 150; never above accepted at any step.

**X** (AT-05, fresh reset): PO to SUP-001 RM-05 1,000 x 180.00 (5%): taxable 1,80,000.00, CGST 4,500.00, SGST 4,500.00,
total 1,89,000.00 (band PO-02, U08). R1 Mon 7 Dec 400. Invoices I1 1,000 and I2 400, both @ 180.00: I1 blocked quantity
600; I2 approved. R2 Wed 9 Dec 600 -> I1 re-matched: available 1,000 - 400 = 600 < 1,000: still blocked, 400 over; cause
`qty_mismatch`, fix `corrected_invoice`. **Concurrent variant:** after R1, two sessions enter 400 each at the same moment
(different numbers): exactly one approved, the other blocked 400 over; `invoiced_qty` 400.

**N** (inter-state): PO to SUP-002 (GJ) RM-06 500 x 38.00 (18%): taxable 19,000.00, IGST 3,420.00, total 22,420.00.
Received Thu 3 Dec, inspected and accepted 500 (any person block 9 allows; only the 500 accepted matter here). F with
IGST 3,420.00: matched. G (other number) with CGST 1,710.00 and SGST 1,710.00: blocked `tax_head` (block 10's words: "CGST and SGST were
charged on a supply from Gujarat (24) to Maharashtra (27): IGST is due") and quantity (F took the goods).

**H** (paise, fresh reset, as M up to R1): line 1 100 @ 482.995: taxable 48,299.50, CGST 4,346.96, SGST 4,346.96
(4,346.955 half up); variance 299.50 <= 300: within tolerance, approved.

**Cases** (M state, sweep at Mon 7 Dec 17:00): open `exception` cases for A, C, D1, D2, D4, E (assignee U04, the
enterer; due Wed 9 Dec 17:00; chain U05, U09), for the two held-stock rows (U02) and whatever else the demo holds; a
second sweep adds none. After the release of A is asked: A's case moves to U08 (step release). After R2: E's case
resolved by the next sweep. `recheck` on D1: 409 "still stuck: ...". D2 not acted on: Wed 9 Dec 17:00 escalated to U05,
due Fri 11 Dec 17:00; then U09, due Tue 15 Dec 17:00; then overdue. Reassign D1 from U04 to store@: 409 cannot act; to a
second AP person the eval adds (two admins): done, due restarted. Brightwave: an invoice for the housekeeping service
before confirmation: case for the requester employee@ (named); the confirmation resolves it.

**J** (jobs): an eval-registered job kind that raises; after the 5th attempt one `job_failed` case for admin@ (U06);
U06 **Run again** with the handler fixed -> job done; sweep resolves. A running job whose `locked_at` was refreshed 1
minute ago is not taken by a second claim at 6 minutes after it started; one not refreshed is.

---

## 10. Boundary cases

| # | Case | Expected |
|---|---|---|
| B1 | Price exactly at a limit (B's 224.40; 483.00 at 100); limits not rounded (482.50 x 2% = 9.65) | within; inclusive |
| B2 | One paisa over per unit (483.01: Rs test; 224.41: % test) | blocked; under "or" both would pass (DF-12) |
| B3 | Price below the order | matched; negative `price_var` stored, no `variance_value` |
| B4 | Variance and taxable in paise (H) | half up to paise; the Rs test uses the unrounded product |
| B5 | Quantity exactly accepted; 0.001 over | matched; blocked (0%) |
| B6 | Partial receipt (X: 400 in, 1,000 invoiced) | blocked 600; never approved above accepted |
| B7 | One invoice over two receipts (E: 120 then 80) | approved by the second receipt's re-match |
| B8 | Two invoices for the last goods at once | one approved, one blocked (line lock) |
| B9 | Two price-blocked invoices for the same goods, both released | the second release's execution fails; still blocked |
| B10 | Release asked on a quantity or service hold | 409 |
| B11 | Price and quantity on one line | both reasons; release refused until the quantity clears |
| B12 | GST exactly `tax_line_abs` apart; 0.01 more | within; blocked |
| B13 | IGST on an intra-state supply, CGST and SGST inter-state | blocked by block 10's header `tax_head`, no per-line amount reason |
| B14 | Received 30 Jun vs 1 Jul 2026; re-match of an invoice matched before this block | v1 vs v2, both tests; old version kept, rules 2 |
| B15 | Release approver: enterer, asker, requester, receiver, order maker, not named | 403 each; proposal stays pending |
| B16 | Item resolved between sweeps; two sweeps at once | screen hides it at once; case done at next sweep; one case per key |

---

## 11. Cross-cutting

- **Money:** Decimal; tax by `formulas/gst`; variance half up to paise; limits compared on unrounded values.
- **A model never decides:** match, reasons, variance, approver, case owner and due date are code; AG03 explains, drafts.
- **One service per write, audited:** `match_invoice`, `invoices/release`, `exception_cases` and `tasks` (section 8 list).
- **Proposals and segregation:** price variance and block release (rule 5); enterer, asker, requester, confirmer,
  receiver and order maker never approve; a quantity or service hold is never released.
- **Tenant safety:** tenant tables only; a failed-job case in the job's own company; 404 across companies.
- **Rules as data, dates:** tolerances versioned (new key), `price_variance` rows, `case_due_days`; the received date
  picks the tolerance version; case due times in working hours on the company calendar.

---

## 12. Open points (RECOMMENDED defaults)

| # | Question | Options | RECOMMENDED and reason |
|---|---|---|---|
| OP-1 | Rupee tolerance per line or per unit | a) per line (today, data notes "per line"); b) per unit | **a**: no data change; a large line cannot hide a big total |
| OP-2 | Who accepts an invoice price variance | a) the person the `price_variance` rule names (Apex purchase manager, Brightwave CFO), not an order maker; b) any `invoice.approve` holder (today, incl. Finance) | **a**: Rev "accept price holds: the band's approver"; rules as data; same rule as the supplier's price rise. Finance keeps releasing tax and arithmetic blocks |
| OP-3 | Can a quantity or service hold be released | a) never: receipt, confirmation, credit note or corrected invoice; b) yes (today) | **a**: D-17 quantity 0% and AT-05 "invoiced never exceeds accepted" |
| OP-4 | GST amount tolerance | a) `tax_line_abs` 1.00 per tax head per line, versioned; b) exact to the paisa; c) `small_diff_abs` | **a**: suppliers round per line or per invoice; a rupee is the arithmetic check's own slack |
| OP-5 | Recording the rule change | a) `rules` 2 on new matches, old rows keep 1; b) none | **a**: reproducible (rule 8) at the cost of one column |
| OP-6 | Which waiting invoice gets late goods first | a) oldest received, then number; b) largest first | **a**: fair and predictable |
| OP-7 | A release whose goods were taken meanwhile | a) execution fails with the reason, invoice unchanged, AP matches again; b) the executor writes the new block | **a**: the approval transaction stays all-or-nothing |
| OP-8 | Lock order | a) invoice row, then order lines by `(po_no, line_no)`; receipts and bookings take the same line locks | **a**: no deadlocks |
| OP-9 | Which stuck items become cases | a) every exceptions row, through an hourly sweep (one source, Base l.460); b) only invoice blocks | **a**: 5.5 "every stuck item has a case" |
| OP-10 | Case deadline, calendar, chain, mail | a) `case_due_days` 2, company calendar, manager then manager's manager, in-app on open, mail on escalation; b) per-kind SLAs | **a**: simple; per-kind later if asked |
| OP-11 | Who may reassign | a) assignee or someone above in its chain, to a person who may act; b) any holder of the permission | **a**: N-3 without bypassing segregation |
| OP-12 | Closing a case by hand | a) never for `exception` cases (only the re-check); `email_failed` with a note; demand and late supply keep their outcomes; b) any close with a note | **a**: Rev "never close without the engine re-checking" |
| OP-13 | Heartbeat storage | a) refresh `locked_at` (no control migration); b) new `heartbeat_at` column | **a**: same meaning, no migration |
| OP-14 | Held stock as cases | a) yes, open holds, owner `stock.quality`; b) no | **a**: N-4 "held quantities" |
| OP-15 | Sending AG03's draft query | a) AP edits and sends through the outbox to the order contact; b) copy-paste (today) | **a**: "AP confirms" (Rev l.441), the mail is kept on the invoice |
| OP-16 | Who reads invoices | a) `invoice.view`, `invoice.match` or `invoice.approve`; b) any login (today) | **a**: D54 check 2 and S1's direction |
| OP-17 | AG03 live threshold | a) 80% of model explanations kept, 0 ungrounded kept; b) 90% | **a**: the plan's P1-18 figure |
| OP-18 | `small_diff_abs` | a) stays unused (block 0b note 6); b) use it | **a**: nothing in the decisions needs it |
| OP-19 | Re-match of price blocks after the order's price is revised | a) by hand (Match again); b) automatic | **a**: a revised price goes through block 7's approval first |
| OP-20 | Migration number | a) 0180 after block 10's 0170 (blocks are built 9 to 13 in order) | **a** |

None needs the owner. OP-2 and OP-3 change today's behaviour (Finance no longer accepts price variances at Apex; a
quantity hold can no longer be paid by a release); both follow approved decisions. They are on the owner's list in
`blocks9-13-interfaces.md`, with one effect found there: at Brightwave VAR-01 and PAY-01 both name the CFO, so an invoice
whose price variance the CFO accepted is left out of the CFO's payment runs by block 12's payer check (rule 7).

---

## 13. Where the plan and the code disagree

1. **"% or Rs"** (`formulas/matching.py:85-87`, fixed by a test) against D-17's both (DF-12); the demo data plants a
   price edge only the "or" lets through (demo script l.131).
2. **No line lock** in `match_invoice`; the release executor does not re-check (DF-11); a price block hides a quantity
   problem on the same line; goods arriving re-match nothing; GST per line is never compared (Plan l.387).
3. **Release** ignores the receiver (rule 7) and the order's makers (C1), commits inside its executor, and releases a
   quantity hold.
4. **AG03** diagnoses a service hold as arithmetic; diagnosis and invoice reads need no permission; live result not
   kept (Plan l.389).
5. **Exceptions** are read only for non-task rows (Plan l.390); failed emails, held stock and failed jobs are not
   listed; a long job can run twice (DF-24).
6. **CLAUDE.md** says the latest tenant migration is 0055; the worktree is at 0150.

---

## 14. Tests and evals

### 14.1 pytest (scenario names; given, when, then)

- `test_invoices.py` (change): "either one lets the line through" becomes "both must hold" (B1-B5); price releases by
  the rule's person, Finance refused for a price and allowed for a tax block, quantity release 409.
- New: `test_match_gst.py` (B12, B13, reverse charge, composition, D1-D4); `test_match_concurrency.py` (B8, B9 with two
  sessions; a match and a block 9 receipt reversal on one line: one waits, the other sees its result; invariant 11.3.2
  after each step of M); `test_rematch_on_arrival.py` (E, X, inspection, service, OP-6);
  `test_invoice_release.py` (11.5, every refusal of B15, deputy, execution failure, notice, unique pending index);
  `test_exception_cases.py` (sweep, assignee, step move, resolution, recheck, reassign, escalation times, failed email,
  held stock, idempotence, two companies). Extend `test_worker.py` (failed-job case, requeue, heartbeat) and
  `test_invoice_exceptions_agent.py` (new causes, query, permissions).
- **Update:** E19 (truth regenerated), E55 (oracle "both"), E44 (new kinds, case fields, price release owner), E25 (new
  causes), E34 (check the named approver), `test_exceptions.py`, `test_tolerance_versions.py`, `test_demo_populate.py`,
  `demo_cases.py`. Each change listed in the report.

### 14.2 Evals E160-E167 (independent; written from this spec only)

Common: fresh `demo reset apex` (and `brightwave` where named); business date pinned as in section 9; role logins;
Mailpit read for mails. Oracle: the section 9 figures recomputed in exact fractions from the rules of section 5 (not from
the platform), `master/tolerance.csv` and `tolerance_version.csv`, `approval_rule.csv`, `user.csv` (managers), the
holiday files, and `truth/truth_match_block11.csv`. Each eval ends with a tenant check and names two planted mutations
it must catch.

**E160 Price within both % and Rs (3.1, DF-12).**
Given scenarios M and H, and the Apex history replayed as E19 does. When A, B, C and H are entered and every history
invoice is re-judged on its received date. Then A and C are blocked for price, B and H are within tolerance; the variances are A
301.00, B 44.00, C 44.10, H 299.50; the March and August price edges are within and blocked; every history result
and its tolerance version and `rules` equal the oracle. Oracle: its own both-tests over the tolerance files and the
history CSVs. Mutations: the "or" rule; the % limit rounded to paise before comparing.

**E161 GST rate and tax per line (3.4).**
Given M after R1, and N. When D1, D2, D4, D3, F and G are entered in that order. Then D1 and D2 are blocked
`gst_mismatch` with the section 5 words, D4 and G `tax_head` with block 10's words (its 10.7.3), all kind `tax`; D3 and F are approved; the expected tax per line on the invoice
page equals the oracle. Oracle: GST split from the order's rate and the two states, half up to paise, compared with the
charged amounts within 1.00. Mutations: the rate ignored when the amounts add up; only the invoice total compared. (The `tax_head` words are
block 10's, quoted in section 9 for D4 and G.)

**E162 No over-invoicing, no double release (3.3, DF-11, AT-05, AT-07).**
Given X's concurrent variant and M up to the release of C. When two invoices for the same 400 are entered at once (two
sessions, started together, repeated 5 times on fresh order lines), and C is released after E took the goods. Then each
time exactly one is approved; C's release execution fails with the 11.5.4 words and C stays blocked; at every step and
on every line, booked invoice quantity never exceeds accepted. Oracle: its own sum of booked quantities against
`gr_line` accepted quantities. Mutations: no line lock; release without re-check.

**E163 A quantity hold is matched again when goods arrive (3.5).**
Given M and X. When R2 is posted on M, and on X. Then E is approved by R2 (listed in `rematched`) and C is not
re-matched; X's I1 stays blocked 400 over with fix `corrected_invoice`; a release on E before R2 and on I1 is 409;
on Brightwave, the housekeeping invoice held `service_not_confirmed` is approved by the requester's confirmation.
Oracle: available = accepted less booked, oldest invoice first. Mutations: no re-match after a receipt; a re-match that
approves beyond accepted.

**E164 Accepting a price variance is a proposal for the rule's person (rule 5, rule 7, C1).**
Given M at the release of A, with line 1's goods received by a login the eval adds (two admins) holding the store and
approver roles. When finance@, ap@ (enterer and asker) and that receiver try to approve, then purchasemanager@ approves;
on a fresh M where U08 revised the order (a maker), release of a price block is asked; on a fresh M, D1 is entered first
on line 3 and its release asked; on Brightwave a price-blocked invoice is released. Then the three refusals are 403
with the proposal still pending; U08 approves A: `variance_accepted` 301.00, `resolution_kind price_accepted`, audit
with `VAR-01 v1`; the maker case is 409 "the price variance rule names nobody ..."; D1 is released by finance@; the
Brightwave proposal names cfo@. Oracle: `approval_rule.csv` VAR rows by value and date; who made, entered and received
from the scenario. Mutations: any `invoice.approve` holder accepts a price; the receiver allowed to release.

**E165 Every stuck item is a case, re-checked before it closes (5.1, 5.2, 5.5).**
Given the cases of section 9 (M state, held stock, Brightwave service). When the sweep runs, runs again, the release of
A is asked, R2 posted, time moves to Wed 9 Dec 17:00, Fri 11 Dec 17:00, Tue 15 Dec 17:00, D1 rechecked and reassigned.
Then every exceptions row has exactly one open case with an assignee and due time equal to the oracle; the second sweep
writes nothing; A's case moves to U08; E's is resolved; D2 escalates to U05, then U09, then is overdue at those times;
recheck of D1 is 409; reassign to store@ is 409 and to the second AP person succeeds; a person cannot close an
`exception` case by hand; no Brightwave login's exceptions list or case call shows an Apex item. Oracle: its own reading of the
raw tables for stuck items and its own working-hours count from the holiday files and managers in `user.csv`.
Mutations: a case closed while its item is still stuck; a second case for the same item.

**E166 Failed jobs, failed emails and held stock become cases (5.3, 5.4, DF-24).**
Given scenario J, a mail queued more than 24 hours, and the demo's quarantined seals. When the worker runs, admin@
presses Run again, the outbox sweep runs, a newer mail for the same order is sent, store@ decides the hold. Then one
`job_failed` case for U06 with the error, gone after the job succeeds; one `email_failed` row resolved by the newer
mail; one `held_stock` row per open hold, resolved by the decision; the refreshed job is not claimed twice, the stale
one is. Oracle: the control `job` rows and the tenant tables read by its own queries. Mutations: a failed job leaves
no case; a running job claimed again despite its heartbeat.

**E167 The invoice and AP agent on the new holds (AG03, D54).**
Given M's D1, E (before R2) and the Brightwave service hold, with a scripted model (careful, wrong cause, invented
number). When each is explained, the draft query is sent by ap@, and store@ and a Brightwave login ask for them. Then the
causes are `gst_mismatch`, `qty_mismatch` (fix `wait_for_receipt`) and `service_not_confirmed`; the sloppy models'
words are never shown; no invoice, match row or proposal changes; the query reaches the order contact with reply-to the
invoice mailbox, audited; store@ 403; a Brightwave login never receives Apex's invoice or diagnosis. `--live`: the same cases and E25's read by the real model, kept in
`evals/results/live.*`, at least 80% kept, 0 ungrounded kept. Oracle: the planted causes from the scenario, not the
platform. Mutations: service hold classified as arithmetic; diagnosis readable by any login.

---

## 15. Build order and done criteria

1. **Data first:** section 3.1 (both tests in the generator, re-planted edge, `tax_line_abs`, planted GST errors, truth
   files, demo script); `--check`.
2. **Migration 0180**, schema doc, data dictionary.
3. `formulas/matching` (both tests, reasons, `judge_gst`) with boundary tests B1-B5, B12, B13.
4. `match_invoice` locks, reasons, rules 2; `invoices/rematch` and its calls (H-1).
5. `invoices/release` (rule approver, guard, re-check, notices); readiness SEP-17.
6. Cases: sources, sweep, recheck, reassign, requeue, heartbeat, failed-job case; CLI.
7. AG03 causes, query sending, permissions; E25 and E167 live run kept.
8. Screens, demo cases, guides, docs; update the tests and evals of 14.1.
9. Evals E160-E167 by a separate agent from this spec.

**Done when** (Plan l.613-617): `python scripts/check.py` is green; E160-E167 and every existing eval pass (E01 onward,
with the updates of 14.1); `demo reset apex` and `demo reset brightwave` work and the walk-throughs E33, E34 and E46 pass;
`docs/database-schema.md` is regenerated after migration 0180; audit rows exist for every write of section 8; the
AG03 live result is in `evals/results/live.*` at or above its threshold.
