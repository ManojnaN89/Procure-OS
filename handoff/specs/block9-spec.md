# Block 9 spec: goods receipt and services (flow step 9)

*Cross-block contracts with blocks 10-13 (built after this one): `handoff/specs/blocks9-13-interfaces.md`.*

Tasks **3.8** (inspection by someone other than the receiver, DF-35), **4.2** (C3: goods above the tolerance held in
quarantine; the buyer accepts them by revision or returns them; serials of rejected units do not block the replacement,
DF-31), **4.3** (return document, replacement and recovery claim, D-21), **3.6** (receipt reversal and its effects) and
**4.5** (D-20: receipts from the ERP with their source keys). Spec only; nothing here is built yet.

**Not in this block:** invoice intake and the unique duplicate key (9a.1, 3.3 first half: block 10); the match itself,
locking order lines in the match, re-matching a quantity block when goods arrive (including a replacement or an accepted
excess), GST per line, D-17, and the general case model with "held quantities as cases" (3.1, 3.3, 3.4, 3.5, WP-5 /
5.3: block 11); the debit note, its approval and netting, and settling a recovery claim (2.4, block 12); short-closing a
quantity that will not be replaced, two-track closure with open returns and claims, PPV at receipt, GSTR-2B (6.6, 13.2,
13.3, 9b.1, 2.6: block 13). Also not here: D-08 receiving modes and the ERP posting of receipts (10.6, WP-10, a separate
workstream, decision #6); stock quality itself (4.1, built in block 2); events for receipt differences (WP-11a routers
come with the capability that needs them); a supplier portal. **Block 9 calls nothing of a later block.** Block 11 adds
the re-match call to this block's new paths (accepted excess, ERP receipts), block 12 the debit note call to claim
raising, block 13 the closure check to reversal, returns and excess decisions.

**Sources.** `planning/P2P-Project-Plan-End-to-End.md` (**Plan**): step 9 gap table l.370-378 ("GRN with rejection:
Partly. Extra goods refused, not held; no reversal"; "Inspection: Partly. Receiver can inspect (DF-35)"; "Into stock and
set aside: Partly. No quarantine, no return document"; "Service confirmation: Built"), recovery claims l.405, WP-4
l.448, tasks l.494 (2.4: "the debit note raised from a return (4.3)"), l.504 (3.6), l.506 (3.8), l.507 (3.9), l.508
(4.1), l.509 (4.2), l.510 (4.3: "the debit note itself comes from 2.4"), l.512 (4.5), l.513 (4.6), l.543 (10.6), block
order l.596-611 (step 9 = 3.6, 3.8, 4.2, 4.3, 4.5), "Every package ends when" l.614, AT-05 l.813, defects DF-35 l.876
and DF-31 l.879, D-08 l.1072, D-10 l.1074, D-20 l.1084, D-21 l.1085. `planning/P2P-Plan-Agentic-Revision.md` (**Rev**)
s2 l.120 ("receipt differences" as an event), s9 l.425-447 (no agent owns receipts; D54 checks), s10 l.474-475
("Confirm a service: Requester"; "Receive and inspect goods: Store, quality"), s13 l.546, l.558-559.
`planning/P2P-Phase1-Pilot-Baseline.md` (**Base**) l.99 (A-6 returns: "Only gr_line.qty_rejected; no return document or
claim"), l.106 (B-4), l.179, l.225 (DF-31: `receiving/goods.py`, migration 0053), l.229 (DF-35: `receiving/goods.py:324`),
l.436-446 (WP-4: "Tests: ... return creates the debit note link"), l.692-697. `planning/P2P-Screens-Brief.md` l.85
("Quality hold and returns (new)"), l.204-211, l.309 ("Receive without order actions use browser prompt boxes").
`handoff/specs/block2-spec.md` l.7, l.101 ("Blocked stock leaves only through 4.3 (block 9)"), OP-10, OP-11, OP-13;
`block4-spec.md` OP-19 (no code on the service link); `block7-spec.md` 5.1, 5.6; `block8-spec.md` 8.2.3
(`order_contact`). Owner decisions not in these files: a failed mail is queued and retried, a task after 24 h; an award
is approved by the po band's approver, never the buyer; recommended defaults are applied, not re-asked. **Code read:**
worktree `p3-b8` at 8ac2221 (blocks 0 to 8; tenant migrations to 0150, evals to E143).

*RECOMMENDED* marks a default this spec proposes because the documents are silent. **NEEDS OWNER** would mark a point
that changes an approved decision; there is none in this block (section 12).

---

## 1. What the decisions say

| Source | Text |
|---|---|
| Plan 3.8 / DF-35 | "Inspection by someone other than the receiver, for items or categories that need inspection" / "Receiver can decide the inspection" |
| Plan 4.2 / Base 693 | "C3 excess held in quarantine; buyer accepts by revision or returns; serials of rejected units do not block the replacement (DF-31)" / "excess over tolerance held in quarantine instead of refused" |
| DF-31 | "Serial numbers of rejected units are kept, blocking the replacement unit" |
| Plan 4.3 / Base 694 | "Return document, replacement and recovery claim; the debit note itself comes from 2.4" / "Return to supplier document, replacement, link to debit note and recovery claim" |
| Plan 3.6 / Base 688 | "Receipt reversal and its effects" / "with its effects on stock, reservation, invoices and budget" |
| Plan 4.5 / D-20 | "D-20 external import receipts (ERP clients)" / "Domestic first release; external import receipts with source keys" |
| D-08 | "The stock owner records receipts; inward record (level 2); three receiving modes" (modes: 10.6) |
| D-10 | "Stock items use budget at receipt" (built in block 3: `budget_ledger.post_receipt`) |
| D-21 | "Recovery claims" |
| Rev s10 | "Receive and inspect goods: Store, quality"; "Confirm a service: Requester" |
| Block 2 l.101 | "Blocked stock leaves only through 4.3 (block 9)"; block 2 OP-13: "no QC role in either demo; inspection SoD is task 3.8" |
| Block 4 OP-19 | "a service confirmation is a receipt, not an approval": its link needs no code |
| Screens brief l.207, l.209 | "receipt reversal; inspection by someone other than the receiver; extra goods held in quarantine, accepted by revision or returned; return document (return, replacement, recovery claim, debit note link); external receipts with source keys"; Receive without order "needs proper forms" |
| Q19 | "any quantity increase ... means re-approval and a new acknowledgement" |
| CLAUDE.md rules 5, 7 | Sensitive actions are proposals; requester, approver, receiver, invoice approver and payer differ |

Silent on: who besides the receiver may not inspect (OP-1), who holds the inspection permission (OP-2), category
against item flags (OP-3), whether rejected units count against the tolerance (OP-4), how an excess revision is approved
(OP-6), gate rejections (OP-9), when a claim is raised and for how much (OP-10), returns of stock with no receipt
(OP-11), scrapping (OP-12), what a reversal covers and when it is refused (OP-13 to OP-16), stock and tolerance of ERP
receipts (OP-17, OP-18). The plan gives step 9 **no AI capability** (Rev s9 table; D50 "Store, quality").

---

## 2. What exists today

| Part | Where | Behaviour |
|---|---|---|
| Posting a receipt | `receiving/goods.py` `post_receipt` | Order `sent`, `acknowledged` or `part_received`; receiver not the buyer, an order approver or the requester (`conflict_of`); per line `room_left(ordered, counted, receipt_over_pct)` from the tolerance version in force; **`qty_received` (rejects included) above the room is refused** (409 "is more than the ... that may still be received"); gate rejection needs a reason; rejected units go nowhere (no stock, no document) |
| Inspection items | `goods.post_receipt`, `goods.inspect`, `receiving/quality.py` `receive_into_quarantine`, `inspect_hold` | `item.inspection_required` only (the category flag `item_category.inspection_required` is loaded but **ignored**); all received units into quarantine (hold source `receipt`); inspection splits accepted to usable and rejected to a blocked hold; permission `grn.create`, **no check of who inspects** (DF-35); `goods_receipt` keeps no inspector |
| Counting | `goods.count_accepted`, `refresh_order`, `formulas/receiving.py` | `po_line.received_qty` += accepted; stocked goods into usable stock at the order price per stock unit; budget actual at receipt (D-10); `order_status` returns None when nothing is received (status left as it was) |
| Serials | `goods._serials`, table `serial_number (item_code, serial)` primary key | One per unit received, rejected units included; a serial on file is refused for ever (DF-31) |
| Holds | `quality.hold`, `decide`, `holds`; `POST /api/inventory/holds`, `/decide` (`stock.quality`, Store) | Usable to quarantine/blocked and back; **blocked stock cannot leave** (no return, no scrap) |
| Reservation | `goods.reserve_arrived` -> `reservation.stock_rose`; `reservation.release_latest` (used by holds) | Accepted goods set aside at once; a hold gives back the latest need first, then the store check |
| Revisions | `orders/changes.revise` | Order `draft` to `part_received` (not `received`); open lines only; quantity never above the request's; quantity up is material (Q19) -> order back to `draft` and approval again; `workflow.reject` **cancels the whole order** |
| Receipt without order | `receiving/unordered.py`, `platform/service_entry_api.py`, `ui/js/views/unordered.js` | Record, purchase manager's proposal, after-the-fact order, receipt on it; rejected goods `mark_returned` with a free-text ref; **actions use `window.prompt`** |
| Services | `receiving/services.py`, `service_links.py`, `kinds.py` | Requester confirms by qty, %, amount; signed link works once, no code (block 4 OP-19); invoices held `service_not_confirmed` re-matched on confirmation. **No reversal** |
| Reversal | - | None. The setting `grn_edit_window_days` (3, both companies: "Days a goods receipt can be corrected after posting") is **read by no code** |
| ERP receipts | `platform/cutover_import.py` (`goods_receipt` at cutover only, `posted_by 'cutover'`) | No ongoing import; the demand import (`requests/demand.py`, `demand_files.py`, `erp_column_map`) has the file pattern to reuse |
| Readers of receipts | `invoices/reconciliation.py` l.39, `invoices/trace.py` l.272, `suppliers/scoring.py` l.109-122, `platform/ui_api.py` `/receipts` | Read every `gr_line`; nothing is ever undone |
| Roles | migration 0003 (`Store: request.view, grn.create, stock.view`), 0039, 0044, 0045, 0053 (`Approver: grn.unordered_approve`), 0070 (`stock.quality`) | No quality role; Apex and Brightwave have one store person each (U02) |

---

## 3. Pre-implementation check (Plan 9.3)

### 3.1 Data

| Need | Apex Pumps | Brightwave | Enough? |
|---|---|---|---|
| A second person who inspects | Only U02 (store) | Only U02 (facilities) | **Gap** |
| Inspection items | RM-06, RM-07 (`inspection: true`) | none | Yes (Apex); category flag set by the eval |
| Tolerance | `receipt_over_pct` 2.00 ANY (v1, v2) | 0.00 ANY | Yes |
| Contract items with tax both ways | CNT-0001 SUP-001 (MH): RM-02 480.00 18%, RM-03 220.00 5%, RM-05 180.00 5%; SUP-002 (Gujarat, IGST) RM-06 38.00 | SUP-001 (KA) LP-01 62,000.00 18%, serialised | Yes |
| Blocked stock to scrap or return | `stock_held.csv` H-APX-02 RM-06 blocked 8 | H-BWT-02 LP-01 blocked 1 | Yes |
| Window setting | `grn_edit_window_days` 3 | 3 | Yes (wording changes) |
| ERP receipt file | none | - | **Gap:** template only; evals write their own rows |

**Generator (data first):**
1. `company.yaml` people: Apex `{code: U11, name: Swati Patil, designation: Quality Inspector, department: PRD, manager:
   U07, limit: "0.00", login: quality, roles: [requester, quality]}`; Brightwave `{code: U11, name: Ravi Shetty,
   designation: IT Support Engineer, department: ITO, manager: U08, limit: "0.00", login: itsupport, roles: [requester,
   quality]}`. `platform/demo.py` `ROLE_ORDER` gains `("quality", "Quality")` after store; `.dev-credentials`.
2. `setting.csv` (both): `grn_edit_window_days,3,Working days after posting in which the store may reverse a goods
   receipt; later the purchase manager approves`.
3. `common/number_series.csv`: `supplier_return,RTN,location,{prefix}-{loc}-{fy4}-{seq:05d},fy` and
   `recovery_claim,RCL,...` (same format).
4. Template `goods_receipt` for the ERP import (section 9.8), listed with the demand templates.
5. `truth/truth_receipts.csv` (`scenario,figure,value`), by the generator's own arithmetic, for every figure of section 9.

### 3.2 Configuration

No new rule table. `receipt_over_pct` stays the versioned tolerance (the receipt keeps `tolerance_version`).
`grn_edit_window_days` becomes working days on the receiving location's calendar (OP-14). Task SLAs are constants
(18 working hours, as block 8). No autonomy gate: nothing here is automatic beyond what a person starts.

### 3.3 Today's behaviour

E18, E43, E68, E86, E09, E33, E34, E46 and `tests/test_receiving.py`, `test_service_entry.py`, `test_stock_*.py` pass on
p3-b8 before anything changes. They will be updated (section 10.1).

### 3.4 Gaps

Inspector separation and category flag (9.1), excess held and decided (9.2, 9.3), serial statuses (9.4), return
document and replacement (9.5), recovery claim and scrap (9.6), reversal of receipts and service entries (9.7), ERP
receipts (9.8), forms for receipts without an order (9.9).

---

## 4. Depends on earlier blocks

| # | Built before | Block 9 uses it for |
|---|---|---|
| D1 | Block 2: `stock_hold`, qualities, `quality._requalify`, `release_latest`, `stock_rose` | Excess holds, returns and scrap leave held stock; reversal gives back reservations |
| D2 | Block 3: `budget_ledger.post_receipt`, `_insert` (locked month moves forward, `budget.actual_moved`) | Excess taken in, reversal and counted returns post their actuals |
| D3 | Block 7: `changes.revise`, `versions`, `approved_versions.record`, `_mail_version` | The excess revision (9.3) |
| D4 | Block 7 / 0.1: `workflow.refuse_maker`, `refuse_outside_band`; an automatic band moves up to the next band with a person | Who approves an excess (9.3) |
| D5 | Block 8: `masters/suppliers.order_contact` | Who gets the return mail |
| D6 | AI foundation: outbox (`outbox.queue_email`), proposals with failure shown (0.5) | Mails; late reversal and excess proposals |

---

## 5. Requirements

### 9.1 Inspection by another person (3.8, DF-35)

1. An order line **needs inspection** when its item says so **or** its category does (`formulas/receiving.
   needs_inspection(item_flag, category_flag)`, OP-3). Used at posting instead of the item flag alone.
2. New permission `grn.inspect` ("Inspect goods"), held by the new role **Quality** and by **Store** (OP-2).
3. `goods.inspect` refuses (409) when the person: received this receipt (`goods_receipt.received_by`, or posted it):
   "segregation of duties: the person who received the goods cannot also inspect them"; or is the order's buyer, an
   approver of the order or the requester (`conflict_of`, words "inspect the goods", OP-1). Without `grn.inspect`: 403.
4. `goods_receipt.inspected_by` (org code) and `inspected_at` are written; audit `grn.inspect` gains `inspected_by`.
5. `GET /api/receipts/{no}` `can_inspect` = pending, `grn.inspect`, and none of the refusals above.
6. Readiness gains `SEP-16 "an inspection decided"`, makers `grn.create`, checkers `grn.inspect`,
   enforced in `receiving/goods.py`.

### 9.2 Goods above the tolerance are held, not refused (C3, 4.2)

1. Per line, **good** = units not rejected at the gate (received less gate-rejected); for an inspection line, good =
   accepted at the inspection. **Counted** = `min(good, room_left)`; **excess** = good - counted
   (`formulas/receiving.split_excess(good, room)`, pure). The tolerance is tested on good units (OP-4): a unit rejected at
   the gate no longer makes a receipt fail.
2. Non-inspection line: counted as today (usable stock, `received_qty`, budget). The excess: `gr_line.qty_excess`;
   stocked item: into stock as **quarantine** (movement `SM-<gr>-<line>-X`, type receipt, quality quarantine), hold
   source `excess`, reason "Above the order on <gr>: waiting for the buyer"; non-stocked: recorded only.
3. Inspection line: at posting every unit goes to quarantine as today, **no room check** (nothing counts yet); at the
   inspection the room is the most receivable less what is already **accepted** on the line (receipts decided first are
   served first), and the accepted part splits into counted (to usable) and excess (stays quarantine, the receipt hold's
   rest becomes a child hold source `excess`); rejected to blocked as today.
4. A **serialised** item keeps today's refusal above the room (OP-5).
5. Each excess is a `receipt_excess` row (`waiting`) and one task `receipt_excess` for the order's buyer, chain the
   first active Purchase Manager, SLA 18 working hours on the ship-to calendar: title "`<qty uom>` of `<item>` arrived
   above `<po>` on `<gr>`", next step "Accept them by revising the order, or return them". The receipt response and the
   requester/buyer notification say "`<qty>` above the order held in quarantine; the buyer decides".
6. Held excess is never set aside, issued, counted or invoiceable (block 2 rules: quarantine).

### 9.3 Accepting or returning the excess

1. **Accept** (`POST /api/receipt-excess/{id}/accept`, `po.create`, body `version`, `reason`): a proposal
   `receipt.excess_accept` (rule 5) for the revision that sets the line's quantity to what is accepted on it plus this
   excess. The proposal shows the new line and order amounts (code,
   `formulas/orders`), "quantity up" (Q19), the budget check at the order price (`approvals/budget`), and waits for the
   person the `po` approval rows name for the **new total**; never a maker of the order, the proposer included (C1); a
   band with no eligible person (an automatic band, or only makers) moves up to the next band with a person (OP-6). Excess
   `proposed`, task completed.
2. **Executor** (inside the proposal's transaction, 0.5): re-checks the excess is `proposed`, the order is at the version
   seen, the approver is still the band's and not a maker, the budget; then `changes.revise(..., excess_id, approved_by)`:
   the line may be `received` and the order `received`; the quantity may exceed the request's by this excess only; the new
   version is recorded as approved by the proposal's approver (`approved_versions.record`), so the approval carries (no
   `draft`, no second round). Then in the same transaction: quarantine -> usable (`requalify`, ref `<gr>`), `count_accepted`
   (`in_stock`), budget actual for the excess; `refresh_order`; excess `accepted`, `version_made`. After commit:
   `stock_rose` for the place; the version is mailed to the supplier with a respond link like any carried revision (block
   7 `_mail_version`; block 8 acknowledgement rules apply, OP-22). Failure: the proposal shows it, nothing changes.
3. **Rejected proposal:** excess back to `waiting`, a new task for the buyer ("the approver refused: return them").
   This runs from a new hook in `platform/proposals.py`: `closer(action)` registers a function called when a proposal
   of that action ends `rejected` or `expired` (same transaction). Block 9 owns the hook; blocks 12 (payment runs) and
   13 (short-close) register on it.
4. **Return** (`POST /api/receipt-excess/{id}/return`, `po.create`, `reason`): a draft return (9.5) with that hold;
   excess `returning`, then `returned` when it is dispatched. Whole excess only (OP-7).
5. Nothing happens by itself to an undecided excess: the task escalates (OP-8).

### 9.4 Serial numbers (DF-31)

1. `serial_number` gains `id` (primary key) and `status` (`received`, `rejected`, `returned`, `reversed`, `scrapped`);
   a serial is unique per item only among `received` rows. "Already received" means a `received` row.
2. A gate rejection or an inspection rejection of a serialised item needs `rejected_serials` (one per rejected unit,
   each among the serials of that line on that receipt): 422 "line `<n>`: name the `<k serial numbers>` rejected, from
   those received". They become `rejected`; returned with a return, `returned`; reversed, `reversed`.
3. A replacement unit with the serial of a `rejected`, `returned` or `reversed` row is received (a new row).

### 9.5 Return document and replacement (4.3)

1. `supplier_return` (number `RTN-<loc>-<fy4>-<seq>`, one supplier, one location; status `draft`, `dispatched`,
   `cancelled`) with lines. A line comes from: a **gate rejection** (made at posting, source `gate`, already
   `dispatched` that day, "sent back with the delivery", OP-9); a **blocked hold** (inspection rejection or manual hold);
   an **excess hold** (9.3); a rejected **receipt without an order** (9.9).
2. **Create** (`POST /api/returns`, `grn.create`): lines `{hold_id, qty, serials?, gr_no?, po_line_no?}`. Holds must be
   blocked or excess and open; qty <= `left_qty`. A hold from a receipt carries its receipt and order line. A **manual**
   hold must name the receipt line it came from (same item and location, accepted on it minus what was already returned
   from it as counted >= qty), else "say which receipt the goods came from"; stock with no receipt (opening, import) may
   name only the supplier (no order effect, no claim; OP-11). Reason required. Audit `return.create`.
3. **Dispatch** (`POST /api/returns/{no}/dispatch`, `grn.create`, `challan`, `vehicle`): stock leaves (type `return`,
   the hold's quality, cost of the receipt), holds reduced or closed (`closed_by_doc` = RTN), serials `returned`. A line
   from a manual hold of accepted goods is **counted**: `received_qty` of its order line goes down (the shared `uncount`
   of 9.7: line and order status from the lines, budget actual minus at the order price, source `return`). Excess lines:
   excess `returned`. The supplier is mailed (outbox, `order_contact`): "We have returned `<qty uom> <item>` (our receipt
   `<gr>`, your challan `<challan>`) on `<dd Mon yyyy>`, return challan `<RTN>`. Reason: `<reason>`." with the lines;
   plain template, no model. Audit `return.dispatch`.
4. **Outcome** per line from an order line (`POST /api/returns/{no}/outcome`, `po.create`): `replace` or
   `no_replacement`; excess and no-order lines are `none`. A dispatched return with an undecided line opens one task
   `return_outcome` for the order's buyer (SLA 18 working hours, chain Purchase Manager): "Agree with `<supplier>`
   whether the `<qty>` returned on `<RTN>` will be replaced". `replace`: the order line stays open for it; the
   replacement is an ordinary receipt (tolerance, inspection, serials 9.4). `replace` on a line that is closed or
   cancelled is refused. `no_replacement`: the quantity stays open until the buyer closes it with today's **Close the
   balance** (`changes.close_balance`; block 13 later adds approval by risk); the task says so. Audit `return.outcome`.
5. **Cancel** a draft (`POST /api/returns/{no}/cancel`, reason): holds untouched. A dispatched return is final.

### 9.6 Recovery claim and scrap (4.3, D-21)

1. A **recovery claim** is raised by the system when a **counted** return line has outcome `no_replacement` and its
   order line is invoiced beyond what remains received: claimed qty = `min(returned, invoiced_qty - received_qty)`
   (`formulas/returns.claim_qty`, pure; nothing when <= 0). Amounts by `formulas/orders.line_amounts` at the order line's
   price and GST rate, CGST+SGST or IGST as the order (OP-10). `recovery_claim` (`RCL-<loc>-<fy4>-<seq>`), status
   `open`, the invoices that counted the quantity, `debit_note_no` empty (block 12 adds the call from claim raising
   to its `notes.raise_debit_note` and fills it; block 9 calls nothing of block 12). Audit `claim.raise`;
   the buyer and every AP person (`invoice.book`) notified "Recovery claim `<RCL>`: `<total>` from `<supplier>`". The
   claim is a record, not a payment act: the debit note and its approval are block 12's.
2. A counted line with `replace` raises no claim (the replacement restores what was invoiced). Gate, inspection and
   excess lines never raise one (they were never counted, so the match already holds any invoice for them).
3. **Scrap** (`POST /api/inventory/holds/{id}/scrap`, `stock.quality`, `qty`, `reason`): blocked holds only; movement
   type `scrap`, quality blocked; hold reduced; serials `scrapped`; value at the receipt's cost shown; audit `stock.scrap`
   (OP-12).

### 9.7 Receipt reversal (3.6)

1. `POST /api/receipts/{no}/reverse` (`grn.create`, `reason`): the **whole receipt** (OP-13), by a store person who is
   not the order's buyer, an approver or its requester. Within `grn_edit_window_days` **working days** after the posting
   day on the receiving location's calendar (OP-14): done at once (200). After: a proposal `receipt.reverse` (202
   `{proposal_id}`) approved by a person with `grn.unordered_approve` who is not the proposer; the executor re-runs every
   check of 9.7.2.
2. **Refused (409)** when: already reversed; the receipt came from the ERP or the cutover ("`<gr>` came from the ERP:
   reverse it there; the reversal comes with the next import"); any of its holds was returned, scrapped or decided by a
   person after the inspection, or its excess was proposed, accepted or returned (OP-16); the order is `closed` or
   `cancelled`; a line's **invoiced** quantity (`po_line.invoiced_qty`, booked invoices) would exceed what remains received: "line `<n>`: `<q>` is invoiced
   (`<invoice nos>`); reversing would leave `<r>` received" (OP-15); usable stock at the place is less than the accepted
   stock units to take back: "`<item>`: only `<q>` usable is at `<location name>`, so `<r>` cannot be taken back".
3. **Effects, one transaction**, order lines and stock place locked (`for update`, the advisory stock lock):
   - stock: accepted units out (type `receipt_reversal`, usable); waiting inspection and excess units out of quarantine,
     gate-rejected units nothing; their holds closed (`closed_by_doc` = `<gr>`);
   - reservation: when usable falls below what is set aside, `release_latest` gives back the latest need first, then the
     store check runs for the place (as a hold does);
   - order: `received_qty` down; line status from the lines; order status from the lines, and when nothing is received,
     `acknowledged` if `acknowledged_version` = `version`, else `sent` (`goods.uncount`, shared with 9.5);
   - budget: minus the accepted value at the order price (source `receipt_reversal`), in the reversal's month, a locked
     month moved forward by `_insert`;
   - serials `reversed`; a gate return of the receipt is left as it is (the goods went back);
   - unordered receipt received on it: back to `ordered`;
   - `goods_receipt.status` `reversed`, `reversed_on`, `reversed_by`, `reversal_reason`. Audit `grn.reverse`.
4. After commit: buyer and requesters told ("Receipt `<gr>` was reversed: `<reason>`"; `tell_requesters` pattern).
5. **Readers:** `/api/receipts` and the receipt page show "reversed"; supplier scoring leaves reversed receipts out;
   received-not-invoiced as of a date counts a reversed receipt only before its `reversed_on`, and a counted return as
   minus from its dispatch date; the order trace shows both.
6. **Service entries** (`POST /api/services/entries/{no}/reverse`, the person who confirmed it or who asked for the
   service, `reason`): same window, proposal after it, same invoice refusal, `received_qty` down; `service_entry.status`
   `reversed`; audit `service.reverse` (OP-21). No stock or budget effect (services are spent at payment).

### 9.8 Receipts from the ERP (4.5, D-20)

1. File kind `goods_receipt` (CSV or Excel, our template or a client's columns through `erp_column_map`):
   `erp_receipt_no`*, `erp_line`*, `po_no`, `po_line_no`, `received_on`, `qty_received`, `qty_rejected`, `reject_reason`,
   `challan`, `received_by` (ERP user), `serials` (`;` separated), `reverses` (an earlier `erp_receipt_no`; then no
   quantities). Key `(erp_receipt_no, erp_line)`. One class in `receiving/erp_receipts.py` on the `DemandFile` pattern;
   reading, mapping, template and report reused from `requests/demand.py`.
2. `python -m p2p.cli import receipts <tenant> <dataset-or-file> [--dry-run]` (a folder is read from its `inputs/`) and
   `POST /api/receiving/import` (`grn.create`, file, `dry_run`). Rows in file order. One platform receipt per ERP receipt
   number and order (`source` `erp`, `external_ref`, `gr_line.external_line`), posted through `goods.post_receipt` with
   `as_of = received_on`.
3. Checks per row (the row left out with its reason; others go on): order exists and is receivable; line open and not a
   service ("confirm services in the app"); quantity above the room refused, no excess holding (OP-18); serials for a
   serialised item; the receiver, when it maps to a person (org code or login), passes `conflict_of`, else recorded as
   `erp:<name>` (OP-19). Inspection is the ERP's: accepted = received - rejected at once.
4. **No stock movement** (OP-17): the ERP's stock comes through `import stock`. Order line, order status and budget
   (D-10) as any receipt.
5. A row loaded before is skipped (a second import loads nothing); a changed one is listed "already loaded; changed:
   `<column> <old> -> <new>`" and not applied. A `reverses` row reverses the platform receipt of that ERP number through
   9.7 without the window; a refusal (invoiced) leaves the row out with its reason. Reversing an ERP receipt in the app is
   refused (9.7.2). Audit `erp_receipt.import` with the counts, plus `grn.post` / `grn.reverse` per receipt.

### 9.9 Receipt without an order

1. Forms replace `window.prompt` in `ui/js/views/unordered.js`: reject (reason), raise the order (price per unit before
   GST, shown with the order value), record the return (challan, vehicle).
2. `mark_returned` makes a dispatched return document (source `unordered`, no order, no claim; `unordered_receipt.
   return_no`), so every return has one number and one mail to the supplier. Statuses unchanged.

### 9.10 Boundary cases

1. Good exactly equal to the room: no excess; one unit more: excess 1. Brightwave (0%): any unit above the ordered
   quantity is excess.
2. A delivery for a line already `received` (or an order `received`) is refused as today ("line `<n>` is not open"):
   goods nobody ordered go through Receive without order. Excess arises only within a receipt the line could take.
3. Everything rejected at the gate: nothing counted, one gate return for all. Everything rejected at inspection: all
   blocked, `received_qty` unchanged.
4. An excess whose order changed version, was cancelled or closed before the decision: accept refused (409 "the order
   changed: return the goods or propose again"); return allowed.
5. Non-stocked excess: no stock movement; accepted, it counts on the line only; returned, a line of quality `none`.
6. Two receipts at the same moment on one line: the order lines are locked (today's `for update`); the second computes
   its room after the first, so their counted total never passes the room.
7. A reversal on the posting day is allowed; a reversal of a receipt whose gate return went back leaves that return as
   it is.
8. A counted return with `no_replacement` when nothing beyond what remains received was invoiced: no claim.
9. A return to a supplier that is now blocked: allowed (the goods go back whatever its status).
10. An ERP `reverses` row naming a receipt never loaded: left out "ERP-GR-`<n>` has not been loaded". An ERP receipt
    dated in a locked budget month: its actual moves to the first open month (`budget.actual_moved`).
11. Claim amounts are rounded per line to paise, half up, as order lines; CGST and SGST each half the rate.

---

## 6. Settings

| Key | Where | Default | Use |
|---|---|---|---|
| `receipt_over_pct` | tolerance, versioned (existing) | Apex 2.00, Brightwave 0.00 | Room per line; above it is excess (9.2) |
| `grn_edit_window_days` | existing, now read | 3 | Working days on the receiving location's calendar (9.7) |

Task SLA 18 working hours and the claim rule are constants; code lifetimes unchanged.

---

## 7. Data: migration 0160 `receipt_quality_returns` (down_revision 0150)

```sql
insert into role_permission (role, action) values ('Quality', 'grn.inspect'), ('Quality', 'stock.view'),
  ('Quality', 'stock.quality'), ('Quality', 'request.view'), ('Store', 'grn.inspect') on conflict do nothing;
alter table goods_receipt
  add column status text not null default 'posted' check (status in ('posted', 'reversed')),
  add column source text not null default 'platform' check (source in ('platform', 'erp', 'cutover')),
  add column external_ref text, add column inspected_by text, add column inspected_at timestamptz,
  add column reversed_on date, add column reversed_by text, add column reversal_reason text,
  add column reversal_proposal_id bigint references proposal (id);
update goods_receipt set source = 'cutover' where posted_by = 'cutover';
create unique index goods_receipt_external on goods_receipt (external_ref, po_no) where external_ref is not null;
alter table gr_line add column qty_excess numeric(18,4) not null default 0 check (qty_excess >= 0),
  add column external_line text;
alter table service_entry
  add column status text not null default 'posted' check (status in ('posted', 'reversed')),
  add column reversed_on date, add column reversed_by text, add column reversal_reason text,
  add column reversal_proposal_id bigint references proposal (id);
alter table serial_number drop constraint serial_number_pkey, add column id bigserial primary key,
  add column status text not null default 'received'
    check (status in ('received', 'rejected', 'returned', 'reversed', 'scrapped')),
  add column return_no text;
create unique index serial_number_received on serial_number (item_code, serial) where status = 'received';
alter table stock_hold drop constraint stock_hold_source_check, add constraint stock_hold_source_check
  check (source in ('receipt', 'manual', 'import', 'decision', 'excess')), add column closed_by_doc text;
alter table stock_movement drop constraint stock_movement_type_check, add constraint stock_movement_type_check
  check (type in ('receipt', 'issue', 'return', 'adjust', 'transfer_in', 'transfer_out', 'requalify',
                  'receipt_reversal', 'scrap'));  -- both new types reduce stock in the existing views
alter table budget_actual drop constraint budget_actual_source_check, add constraint budget_actual_source_check
  check (source in ('payment', 'receipt', 'receipt_reversal', 'return'));
create table receipt_excess (id bigserial primary key, gr_no text not null references goods_receipt (no),
  po_no text not null, po_line_no integer not null, qty numeric(18,4) not null check (qty > 0), uom text,
  hold_id bigint references stock_hold (id), status text not null default 'waiting' check (status in
  ('waiting', 'proposed', 'accepted', 'returning', 'returned', 'reversed')), task_id integer references task (id),
  proposal_id bigint references proposal (id), version_made integer, return_no text, decided_by text,
  decided_at timestamptz, reason text, created_at timestamptz not null default now());
create table supplier_return (no text primary key, supplier_code text not null references supplier (code),
  location_code text not null references location (code), status text not null default 'draft'
  check (status in ('draft', 'dispatched', 'cancelled')), source text not null
  check (source in ('store', 'gate', 'excess', 'unordered')), reason text not null, challan text, vehicle text,
  dispatched_on date, created_by text not null, dispatched_by text, task_id integer references task (id),
  created_at timestamptz not null default now());
create table supplier_return_line (return_no text not null references supplier_return (no), line_no integer not null,
  item_code text references item (code), description text, qty numeric(18,4) not null check (qty > 0), uom text,
  quality text not null check (quality in ('blocked', 'quarantine', 'none')), hold_id bigint references stock_hold (id),
  gr_no text references goods_receipt (no), po_no text, po_line_no integer, unordered_no text,
  counted boolean not null default false, serials jsonb not null default '[]', unit_cost numeric(18,4),
  outcome text check (outcome in ('replace', 'no_replacement', 'none')), outcome_by text, outcome_at timestamptz,
  claim_no text, primary key (return_no, line_no));
create table recovery_claim (no text primary key, supplier_code text not null references supplier (code),
  po_no text not null, po_line_no integer not null, return_no text not null, return_line_no integer not null,
  qty numeric(18,4) not null check (qty > 0), unit_price numeric(18,4) not null, taxable numeric(18,2) not null,
  cgst numeric(18,2) not null, sgst numeric(18,2) not null, igst numeric(18,2) not null, total numeric(18,2) not null,
  invoices text[] not null, reason text not null, status text not null default 'open'
  check (status in ('open', 'settled', 'cancelled')), debit_note_no text, raised_by text not null,
  raised_at timestamptz not null default now());
alter table unordered_receipt add column return_no text references supplier_return (no);
-- number series RTN and RCL for a company already set up (as 0053 does for URN and SRN)
```

Constraint names are as the earlier migrations made them (the builder confirms each). Down: drop the new tables and
columns, restore the constraints and the serial primary key (refused if a serial has two rows). Then
`scripts/gen_schema_doc.py`; `docs/data-dictionary.md` rows for each new table and column; `recovery_claim` is the one
claims table block 12 extends (2.4: other sources, debit note, settlement).

### 7.1 Code (one job each; reuse first)

| Module | Change |
|---|---|
| `formulas/receiving.py` | `needs_inspection`, `split_excess(good, room)`, `check_rejected_serials` (pure) |
| `formulas/returns.py` (new) | `claim_qty(returned, invoiced, received_after)`; claim amounts through `formulas/orders.line_amounts` |
| `formulas/budget.py` | `receipt_reversal_actual(qty, price)` = minus `receipt_actual` |
| `formulas/calendar` / `reference.calendar_for_location` | window: last working day after posting (reused) |
| `receiving/goods.py` | inspector separation, category flag, excess at posting and inspection, gate return, serial statuses, `uncount` |
| `receiving/quality.py` | excess holds (`receive_excess`, three-way `inspect_hold`), `scrap`, `closed_by_doc` |
| `receiving/excess.py` (new) | `propose_accept`, executor `receipt.excess_accept`, `send_back` |
| `receiving/returns.py` (new) | class `SupplierReturn` (create, dispatch, outcome, cancel), claim raising, the supplier mail |
| `receiving/reversal.py` (new) | `reverse_receipt`, `reverse_service_entry`, window, executor `receipt.reverse` |
| `receiving/erp_receipts.py` (new) | file class `GoodsReceipts`, `import_receipts` |
| `receiving/unordered.py` | `mark_returned` through `returns` |
| `orders/changes.py` | `revise(..., excess_id, approved_by)` (9.3.2) |
| `platform/proposals.py` | `closer(action)` hook (9.3.3), run when a proposal ends `rejected` or `expired` |
| `approvals/budget_ledger.py` | `post_reversal(gr, line, qty, as_of, source)` through `_insert` |
| `invoices/reconciliation.py`, `invoices/trace.py`, `suppliers/scoring.py` | reversed receipts and counted returns (9.7.5) |
| `platform/ui_api.py`, `platform/service_entry_api.py`, `platform/returns_api.py` (new, registered in `main.py`) | endpoints of section 8 |
| `platform/exceptions.py` | `RECEIPT_NEXT` kinds `receipt_excess`, `return_outcome`; rows `inspection` (pending receipts, owner Quality, act `grn.inspect`) and `return_draft` (owner Store) |
| `platform/readiness.py`, `platform/demo.py`, `platform/guides_api.py`, `platform/setup_api.py` | SEP-16; role Quality; guide; permission label "Inspect goods" |
| `p2p/cli.py` | `import receipts` |
| `datagen/` | section 3.1 |

No new agent tool; no model call.

---

## 8. Public interface (for the eval writer)

All `/api` under the tenant session, JSON; errors 422 (input), 409 (state or separation), 403 (permission), 404 (not
in this company).

| Endpoint | Body | Success | Errors |
|---|---|---|---|
| `POST /api/purchase-orders/{no}/receive` | lines add `rejected_serials` | adds `excess {line: qty}`, `return_no` (gate rejections) | 409 serialised above room; 422 rejected serials |
| `POST /api/receipts/{no}/inspect` (`grn.inspect`) | lines add `rejected_serials` | adds `excess {line: qty}` | 409 separation (9.1.3); 403 |
| `GET /api/receipts/{no}` | - | adds `status`, `source`, `inspected_by`, `can_inspect`, `can_reverse`, `reverse_by` (`store` or `proposal`), lines `excess`, `excess_status`, `returns[]` | - |
| `POST /api/receipts/{no}/reverse` | `{"reason"}` | 200 `{"status": "reversed"}` or 202 `{"proposal_id"}` | 409 (9.7.2); 422 no reason |
| `GET /api/receipt-excess?open=true` | - | `[{id, gr_no, po_no, line, qty, uom, status, hold_id}]` | - |
| `POST /api/receipt-excess/{id}/accept` | `{"version", "reason"}` | `{"proposal_id", "approver", "new_qty", "new_total"}` | 409 not waiting, version changed |
| `POST /api/receipt-excess/{id}/return` | `{"reason"}` | `{"return_no"}` | 409 not waiting |
| `GET /api/returns`, `GET /api/returns/{no}` | - | header, lines (`counted`, `outcome`, `claim_no`), status | - |
| `POST /api/returns` | `{"reason", "lines": [{"hold_id", "qty", "serials"?, "gr_no"?, "po_line_no"?, "supplier_code"?}]}` | 201 `{"return_no"}` | 409 (9.5.2); 422 |
| `POST /api/returns/{no}/dispatch` | `{"challan", "vehicle"}` | `{"status": "dispatched", "claims": []}` | 409 not draft |
| `POST /api/returns/{no}/outcome` | `{"lines": {"<line>": "replace" \| "no_replacement"}}` | `{"claims": ["RCL-..."]}` | 409 replace on a closed line |
| `POST /api/returns/{no}/cancel` | `{"reason"}` | `{"status": "cancelled"}` | 409 dispatched |
| `GET /api/recovery-claims` (`invoice.view` or `po.create`) | - | `[{no, supplier, po_no, line, qty, taxable, cgst, sgst, igst, total, status, invoices, return_no}]` | - |
| `POST /api/inventory/holds/{id}/scrap` (`stock.quality`) | `{"qty", "reason"}` | `{"left_qty"}` | 409 not blocked, more than left |
| `POST /api/services/entries/{no}/reverse` | `{"reason"}` | 200 or 202 as receipts | 409 invoiced; 403 not the confirmer or requester |
| `POST /api/receiving/import` (`grn.create`) | multipart `file`, `dry_run` | `{"loaded", "already", "changed": [], "left_out": [{"row", "reason"}], "reversed"}` | 422 unreadable file |

Proposals `receipt.excess_accept` and `receipt.reverse` are decided on the existing proposal endpoints.

Tables the eval may read: `goods_receipt`, `gr_line`, `receipt_excess`, `supplier_return`, `supplier_return_line`,
`recovery_claim`, `serial_number`, `stock_hold`, `stock_movement`, views `stock_level` and `stock_quality_level`,
`po`, `po_line`, `po_version`, `budget_actual`, `service_entry`, `proposal`, `task`, `audit_event`.

Audit actions: `grn.post` (adds `excess`, `return_no`), `grn.inspect` (`inspected_by`, `excess`), `grn.reverse`,
`receipt.excess_propose`, `receipt.excess_accept`, `receipt.excess_return`, `return.create`, `return.dispatch`,
`return.outcome`, `return.cancel`, `claim.raise`, `stock.scrap`, `service.reverse`, `erp_receipt.import`, plus the
existing `po.revise`, `budget.actual`, `budget.actual_moved`, `proposal.*`, `task.create`, `task.complete`.

### 8.1 Screens (original ProcureOS look and classes; no redesign)

- **Store** (`ui/js/views/receiving.js`): the receipt result names the excess and the gate return; rejected serials
  are ticked from the serials typed; the receipt page has **Reverse** (reason; after the window "Ask the purchase
  manager"). New page **Quality hold and returns** (menu Receiving, Screens brief l.85): open blocked and excess holds,
  **Return to supplier** (select holds, receipt line for a manual hold), drafts with **Dispatch**, returns list,
  **Scrap** on a blocked hold.
- **Quality** (new role menu: Receiving, Quality hold and returns, Inventory, Help): receipts waiting for inspection;
  **Record inspection** with rejected serials; hold decisions (block 2).
- **Buyer**: My work and Exceptions show `receipt_excess` and `return_outcome` with **Accept by revision** / **Return**
  and **Replace** / **No replacement**; the order page lists receipts (reversed marked), excess, returns and claims.
- **Purchase manager / approvers**: the two proposals on the existing approvals screen; receive-without-order forms.
- **AP**: **Recovery claims** list (read) under Invoices; the order page shows them.
- **Requester**: Confirm services shows **Reverse** on their own entries.
- Admin (`frontend/`): no change beyond the role and permission label.

### 8.2 Docs

`docs/guides/store.md` (excess, gate returns, returns, reversal, ERP import), new `docs/guides/quality.md`,
`buyer.md` (excess, outcomes), `ap.md` (claims), `requester.md` (service reversal); `docs/demo-script.md` step 12 (the
seals inspected by quality@) and a short "returns" part; data dictionary; schema doc; CLAUDE.md (the receipt rows of
"Where things are", the `import receipts` command, latest migration 0160).

---

## 9. Scenarios and worked figures

Common: `demo reset apex` (and `brightwave` where named); business date pinned; role logins (`store@`, `quality@`,
`buyer@`, `purchasemanager@`, `ap@`, `production@` at `apexpumps.example`; `facilities@`, `itsupport@`, `buyer@`,
`employee@` at `brightwave.example`); Mailpit. PLT-CKN works Mon-Sat (closed 25 Dec 2026); OFF-BLR Mon-Fri. Orders are
made through the normal flow (request, approval, sourcing, order, sent); their numbers are read, not assumed.

**A** (Apex): one-line order to SUP-001 for RM-03 200 NOS at 220.00, GST 5% (CGST 2.5% + SGST 2.5%): taxable
44,000.00, CGST 1,100.00, SGST 1,100.00, total 46,200.00; sent. **B**: one-line order to SUP-002 (Gujarat) for RM-06
500 NOS at 38.00, IGST 18%: 19,000.00 + 3,420.00 = 22,420.00; sent. **D**: one-line order to SUP-001 for RM-05 100 NOS
at 180.00, 5%: 18,000.00 + 450.00 + 450.00 = 18,900.00. **F**: one-line order to SUP-001 for RM-02 100 NOS at 480.00,
18%. **E** (Brightwave): order to SUP-001 for LP-01 2 NOS at 62,000.00, 18% intra-state: 1,24,000.00 + 11,160.00 +
11,160.00 = 1,46,320.00, approved through its chain and sent.

| # | Steps | Expected |
|---|---|---|
| **R1** Excess at the gate | A; Mon 7 Dec store@ receives 210, rejects 3 "chipped vane" | room 204 (200 x 1.02); good 207; counted 204; excess 3. `received_qty` 204, line and order `received`; usable RM-03 +204, quarantine +3 (hold source excess); budget actual 44,880.00 (2026-12); a gate return RTN for 3 NOS dispatched 7 Dec, outcome undecided, task `return_outcome`; one `receipt_excess` `waiting`, one task `receipt_excess` for U03, chain U08 |
| **R2** Accept by revision | R1; Tue 8 Dec buyer@ accepts (version 1); purchasemanager@ approves the proposal | approver = U08 (new total 47,817.00 is in PO-01, an automatic band, so it moves up to PO-02); U03 cannot approve. Version 2: line 207 NOS, taxable 45,540.00, CGST 1,138.50, SGST 1,138.50, total 47,817.00; approval carried (no `draft`); quarantine -3, usable +3; `received_qty` 207; budget +660.00 (total 45,540.00 = 207 x 220); excess `accepted`, `version_made` 2; v2 mailed to sales@deccancastings.example with a respond link |
| **R2b** Return instead | R1; buyer@ returns the excess; store@ dispatches Tue 8 Dec | quarantine -3 (type return), hold closed by the RTN; excess `returned`; `received_qty` 204 unchanged; no claim; supplier mail with the RTN |
| **R3** Inspection by another person, excess at inspection | B; Wed 9 Dec store@ receives 515 (all to quarantine, no refusal); store@ inspects; buyer@ inspects; Thu 10 Dec quality@ accepts 512, rejects 3 "rough running" | store@ 409 "the person who received the goods cannot also inspect them"; buyer@ 403; quality@: counted 510 (room 510), excess 2, blocked 3; `received_qty` 510; usable +510; quarantine 2 (excess), blocked 3; budget 19,380.00; `inspected_by` U11 |
| **R3b** Category flag | PKG `inspection_required` set true in the database (as E18 does for items); PK-01 (item flag false) received | waits for inspection |
| **R4** Return and replacement | B (fresh); Wed 9 Dec store@ receives 500; Thu 10 Dec quality@ accepts 490, rejects 10; Fri 11 Dec store@ returns the blocked 10 and dispatches (challan RDC-11, vehicle MH 14 XX 0000); buyer@ outcome replace; Thu 17 Dec 10 arrive, quality@ accepts 10 | after inspection `received_qty` 490, part received, budget 18,620.00; return: blocked -10, hold closed, mail to sales@gujaratbearings.example; no claim; after the replacement `received_qty` 500, order `received`, budget total 19,000.00 |
| **R5** Counted return and claim | R4 done; AP enters SUP-002's invoice for 500 x 38.00 (22,420.00), matched and approved; Mon 21 Dec store@ holds 20 usable RM-06 blocked "noisy after fitting"; creates a return naming R4's first receipt line 1; dispatches Tue 22 Dec; buyer@ outcome no replacement | blocked -20; `received_qty` 480, line and order `part_received`; budget -760.00 (source return, 2026-12); claim RCL: qty 20, taxable 760.00, CGST 0.00, SGST 0.00, IGST 136.80, total 896.80, `open`, invoices = that invoice, no debit note; AP and buyer notified. Variant replace: no claim. Variant invoice of 490 only: claim 10, 380.00 + 68.40 = 448.40 |
| **R6** Reversal | D; Mon 7 Dec store@ receives 100; Wed 9 Dec store@ reverses "posted on the wrong order" | usable RM-05 -100; `received_qty` 0; order back to `sent` (or `acknowledged` if acknowledged at its version); budget -18,000.00 (2026-12); receipt `reversed`; buyer and requester told |
| **R6b** Refusals | D received 100 on Mon 7 Dec: (i) 150 RM-05 issued first (on hand 60 + 100 = 160, 10 left); (ii) instead AP's invoice for 100 matched; (iii) reversed Fri 11 Dec | (i) "RM-05: only 10 usable is at Plant, Chakan (Pune), so 100 cannot be taken back"; (ii) "line 1: 100 is invoiced (<inv>); reversing would leave 0 received"; (iii) window: 7 Dec + 3 working days = Thu 10 Dec, so 202 proposal; store@ cannot approve it; purchasemanager@ approves; reversed |
| **R6c** Locked month | D received Mon 30 Nov; U05 locks 2026-11; Wed 2 Dec store@ reverses | budget -18,000.00 booked in 2026-12 with `budget.actual_moved`; the receipt's own +18,000.00 stays in 2026-11 |
| **R7** Serials (DF-31) | E; Mon 7 Dec facilities@ receives 2 (BWL-0001, BWL-0002), rejects 1 "cracked screen" naming BWL-0002; Thu 10 Dec the repaired unit BWL-0002 arrives; then a receipt naming BWL-0001 | BWL-0001 `received`, BWL-0002 `rejected` then `returned` (gate return); the replacement accepted, a new `received` row; BWL-0001 again 409 "serial number already received for LP-01: BWL-0001"; rejecting 1 without naming a serial 422 |
| **R8** ERP receipts | F; file rows in order: ERP-GR-7001/1 line 1 qty 60 on 2026-12-07; ERP-GR-7002/1 qty 50; ERP-GR-7003/1 for an unknown order; ERP-GR-7004/1 qty 40; then the same file again; then 7001 with qty 55; then ERP-GR-7005/1 `reverses` ERP-GR-7004 | first import: 7001 loaded (received 60, budget 28,800.00, no stock movement), 7002 left out "line 1: 50 is more than the 42 that may still be received", 7003 left out "... does not exist", 7004 loaded (received 100, budget 19,200.00); second import: loaded 0, already 2; 7001 changed: listed "already loaded; changed: qty_received 60 -> 55", nothing applied; 7005: receipt of 7004 reversed, received 60, budget -19,200.00; reversing 7001 in the app 409; dry run writes nothing |
| **R9** Service entry reversal | Brightwave; a sent housekeeping order (SV-01, 85,000.00); employee@ confirms 100% Mon 7 Dec; Tue 8 Dec employee@ reverses | `received_qty` 0; entry `reversed`; facilities@ cannot reverse it (403); with an invoice matched to it: refused |
| **R10** Scrap | Apex H-APX-02 (RM-06 blocked 8); store@ scraps 8 "rusted, not returnable" | blocked RM-06 -8, hold closed, `stock.scrap` audit; usable unchanged |
| **R11** Tenants | Apex R1 to R5 and Brightwave R7 | Brightwave's buyer: Apex return, claim, excess and receipt 404; an Apex `hold_id` in Brightwave's return 404; lists show only their own company |

**R6d** Window edges (the date checks, two separate runs): D received on Thu 10 Dec. On the PLT-CKN calendar the window ends Mon 14 Dec
(Fri 11, Sat 12, Mon 14). A reversal on Mon 14 Dec is done at once (counting calendar days would end it Sun 13 Dec: a
proposal); a reversal on Tue 15 Dec is a proposal (the Mon-Fri company calendar would end it Tue 15 Dec: done at once).

---

## 10. Tests and evals

### 10.1 pytest (scenario names; given, when, then)

- `tests/test_receiving_formulas.py` (extend `test_receiving.py` formula part): `split_excess` (good = room, room + 1,
  good below room, room 0), `needs_inspection` truth table, `check_rejected_serials`, `claim_qty` (returned above, equal,
  below the invoiced surplus; none invoiced), `receipt_reversal_actual` sign and paise.
- `tests/test_inspection_separation.py`: R3 refusals; buyer, approver and requester refused; another store person
  inspects; `can_inspect`; SEP-16 readiness.
- `tests/test_receipt_excess.py`: R1, R2, R2b; rejected proposal; version changed before approval; budget hold at
  approval; excess never set aside or invoiceable; serialised above room refused.
- `tests/test_supplier_returns.py`: R4, R5 and variants; manual hold without receipt; supplier-only return; cancel;
  replace on a closed line; mail words; DF-31 statuses.
- `tests/test_receipt_reversal.py`: R6, R6b, R6c; reservation given back and store check; unordered receipt back to
  ordered; ERP and cutover refused; two reversals of one receipt at once (one wins). A reversal racing a match on the
  same line is safe only once block 11's match takes the line lock (11.3.1); block 11's concurrency test covers it.
- `tests/test_erp_receipts.py`: R8; mapping; dry run; serials; receiver mapping.
- `tests/test_service_entry.py` (extend): R9.
- `tests/test_tenant_isolation.py` (extend): R11.
- **Update:** `test_receiving.py` (above tolerance is now excess, not 409; the concurrency test counts counted plus
  excess; the API inspection codes and the inspector); E18 (oracle: excess instead of refusal; inspection by quality@),
  E68, E86, `evals/block0d.py`, E33 and E34 (seals inspected by quality@), E09 (`/inspect` needs `grn.inspect`), E43
  (serials by status; unordered return has an RTN); E44 (the new `inspection`, `return_draft`, `receipt_excess` and
  `return_outcome` rows join its oracle where its scenario raises them); any test or eval counting people or roles (one
  more person in each company); `demo_cases.py`. Each change is listed in the report.

### 10.2 Evals E144-E151 (independent; written from this spec only)

Common: fresh `demo reset apex` (and `brightwave` where named); business date pinned as said; role logins; Mailpit read
for every mail. Oracle: section 9 arithmetic in exact fractions (half up to paise for money, to 4 places for
quantities), `tolerance.csv` and `tolerance_version.csv` for the room, `approval_rule.csv` for the approver,
`location.csv` and the holiday files for working days, `truth/truth_receipts.csv`. Each ends with a tenant check and
names two planted mutations it must catch.

| Eval | Given / when | Then | Oracle | Mutations caught |
|---|---|---|---|---|
| **E144** Goods above the order are held, then accepted or returned | R1, R2, R2b | excess held in quarantine, not refused, not counted, not set aside; task; acceptance only by the band's approver who is no maker; version 2 figures; stock, `received_qty` and budget after acceptance; return path leaves the line untouched | room from the tolerance in force; amounts and approver from the masters | excess counted on the line at once; the buyer approves the acceptance |
| **E145** Someone else inspects | R3, R3b | receiver refused (409, words), no permission 403, quality@ decides; counted/excess/blocked split; category flag; `inspected_by`; SEP-16 in readiness | room arithmetic; people and roles from `org_user` files | receiver may inspect; category flag ignored |
| **E146** Returns and the replacement | R4 | blocked stock leaves only by the return; mail words and address; outcome task; no claim; the replacement is received and inspected normally; budget totals | quantities and money by fractions | blocked stock still counted after dispatch; return closes the order line |
| **E147** A return after acceptance and its recovery claim | R5 and both variants | `received_qty` down, budget minus, claim qty and amounts with IGST, invoices named, no debit note, notifications; replace raises none | `claim_qty` rule and GST by fractions | claim on the full returned qty when less was invoiced; CGST/SGST used for an inter-state supplier |
| **E148** Reversing a receipt | R6, R6b, R6c, R6d | effects on stock, line, order status, budget (locked month moved), audit; refusals with words; window on the PLT-CKN calendar; proposal after it, never by the proposer | working days from `location.csv`; budget months | window counted on the Mon-Fri calendar; reversal allowed with an invoice matched |
| **E149** Rejected serials free the replacement (DF-31) | R7 (Brightwave) | statuses; repaired unit accepted; a received serial refused; unnamed rejection refused | the serial lists of the scenario | rejected serial still blocks; rejected unit's serial set `received` |
| **E150** Receipts from the ERP with their keys | R8 | loaded, skipped, changed and left-out rows with reasons; no stock movement; budget; reversal row; app reversal refused; dry run | the file rows and the room | a second import loads again; an ERP receipt moves stock |
| **E151** Services, scrap and companies | R9, R10, R11 | service reversal rules; scrap only of blocked stock; every block 9 list and endpoint scoped to its company; Quality role permissions | `role_permission` from this spec; the dataset's holds | Brightwave reads an Apex claim; scrap takes usable stock |

---

## 11. Cross-cutting

| Rule | Here |
|---|---|
| Money | `Decimal`; claim and revision amounts by `formulas/orders.line_amounts`, budget by `formulas/budget`, paise half up |
| A model never decides | no model call in this block; no AI capability (the plan assigns none to step 9; D54 checks not applicable) |
| One service per write | receipts `goods`, holds `quality`, excess `excess`, returns `returns`, reversals `reversal`, versions `changes.revise`, budget `budget_ledger`, mail the outbox |
| Audit | every write (section 8 list) |
| Proposals | excess acceptance (a material change, approved before the version is made) and a late reversal; claims are records, the debit note is block 12's proposal |
| Segregation | receiver and inspector differ (DF-35); neither is the buyer, an approver or the requester; the excess approver is no maker (C1); a reversal proposal is not approved by its proposer; SEP-16 |
| Tenant safety | all new tables are tenant tables; every endpoint 404s another company's numbers |
| Rules as data | tolerance versioned and recorded on the receipt; window a setting |
| Dates | working days on the receiving location's calendar; budget months with period locks; number series per financial year |

### 11.1 Files shared with other work

| File | Other work | Block 9 keeps to |
|---|---|---|
| `orders/changes.py` | blocks 7, 8, 13 (short-close) | one optional path in `revise` (`excess_id`, `approved_by`) |
| `invoices/reconciliation.py`, `invoices/trace.py` | block 13 | the reversed and counted-return filters only |
| `platform/exceptions.py` | blocks 8, 11 (WP-5) | two task kinds and two read rows |
| `recovery_claim` | block 12 (2.4) | raises `open` claims from returns; block 12 adds debit notes and settlement |
| `platform/proposals.py` | blocks 12, 13 | the `closer` hook; later blocks register on it |
| `receiving/*` (this block's paths) | blocks 11, 13 | block 11 adds `rematch.after_accepted`, block 13 `closure.check`, each after commit |
| Migrations, evals | blocks 8, 10-13 | 0160; E144-E151 |

---

## 12. Open points (RECOMMENDED defaults)

| # | Question | Options | RECOMMENDED and reason |
|---|---|---|---|
| OP-1 | Who besides the receiver may not inspect | a) also the buyer, order approvers, requester (`conflict_of`); b) receiver only | **a**: the inspector releases goods into the order as a receiver does |
| OP-2 | Who holds `grn.inspect` | a) new role Quality and Store (another store person may inspect); b) Quality only | **a**: companies with no QC person keep working; the receiver is still refused |
| OP-3 | Item and category flags | a) either one; b) item overrides category | **a**: items are generated with an explicit false, so b would ignore every category |
| OP-4 | Gate-rejected units and the tolerance | a) tested on good units; b) on all received (today) | **a**: rejected units go back; only kept goods can be too many |
| OP-5 | Excess of a serialised item | a) refused as today; b) held | **a**: a unit not ordered goes through Receive without order |
| OP-6 | How the excess revision is approved | a) proposal to the band's approver for the new total before the version (carries); b) block 7 re-approval (order to `draft`; a rejection cancels the whole order) | **a**: Q19's re-approval without cancelling an order with goods in; receipts of other lines go on |
| OP-7 | Part of an excess | a) whole only; b) split | **a**: one decision per receipt line |
| OP-8 | Undecided excess | a) task escalates; nothing automatic; b) auto-return after N days | **a**: AT-10's principle |
| OP-9 | Gate rejections | a) a return document dispatched with the delivery; b) none (today) | **a**: the supplier gets a return challan; one return trail |
| OP-10 | When a claim is raised | a) counted return, no replacement, invoiced beyond received; order price and GST; b) every return line | **a**: only money actually at stake; uncounted goods are already held by the match |
| OP-11 | Stock with no receipt | a) returnable naming the supplier, no order effect or claim; b) scrap only | **a**: opening stock is returned in practice |
| OP-12 | Scrap approval | a) `stock.quality` with a reason, no approval (as a count); b) purchase manager above a value | **a**: a count needs none today; revisit with D-22 if the owner wants |
| OP-13 | What is reversed | a) whole receipt; b) single lines | **a**: correct by reversing and posting again |
| OP-14 | Window | a) `grn_edit_window_days` working days, ship-to calendar; then a proposal to `grn.unordered_approve`; b) calendar days; c) never after | **a**: rule 9; the setting already exists |
| OP-15 | Reversal with invoices | a) refused when invoiced would exceed received; b) allowed, invoice re-matched | **a**: a correction must not leave an invoice unsupported; block 11 owns re-matching |
| OP-16 | Reversal after later acts | a) refused once a hold of it was returned, scrapped or decided, or its excess decided; b) unwind all | **a**: simple and safe; correct with a return instead |
| OP-17 | Stock from ERP receipts | a) none (the stock import carries it); b) movement too | **a**: no double count with `import stock` |
| OP-18 | ERP receipt above tolerance | a) row left out with its reason; b) held as excess | **a**: ERP-owned data stays read-only; the ERP or a revision fixes it |
| OP-19 | ERP receiver separation | a) checked when the name maps to a person, else `erp:<name>`; b) never | **a**: rule 7 where the platform can know |
| OP-20 | Code on the service link | a) keep block 4 OP-19 (no code); b) code now | **a**: an approved default; the link names one person and works once |
| OP-21 | Service entry reversal | a) in this block, same rules; b) later | **a**: "receipt reversal" covers the service's receipt |
| OP-22 | Acknowledgement of the excess version | a) mailed with a respond link as any revision (Q19, D53); b) the delivery counts as acknowledged | **a**: no change to approved decisions |
| OP-23 | Migration number | a) 0160 after 0150; b) next free | **a** |

No point needs the owner: each default fills a gap and keeps Q19, C1, D53, D-10 and block 4 OP-19 as decided.

---

## 13. Where the plan and the code disagree

1. **Over-delivery refused** (C3 says hold): `post_receipt` 409, counting gate-rejected units against the room.
2. **The receiver inspects** (DF-35): `goods.inspect` checks only `grn.create`; no inspector kept.
3. **Category inspection flag ignored**: only `item.inspection_required` is read (plan 3.8 "items or categories").
4. **Rejected units vanish**: no return document; blocked stock cannot leave (block 2 promised 4.3).
5. **Serials of rejected units block for ever** (DF-31): primary key `(item_code, serial)`.
6. **No reversal**; `grn_edit_window_days` exists in both datasets and is read by nothing.
7. **Order status never goes back**: `order_status` returns None when nothing is received.
8. **A rejected re-approval cancels the order** (`workflow.reject`), even with goods received: avoided for the excess by
   OP-6; for other revisions it stays a block 7 matter.
9. **Receive without order** uses `window.prompt` (Screens brief l.309); its return has only a free-text ref.
10. **CLAUDE.md** says the latest tenant migration is 0055; the worktree is at 0150 (0160 after this block).

---

## 14. Build order

1. **Data first:** U11 in both companies, role Quality, setting wording, number series, ERP template,
   `truth/truth_receipts.csv`; `--check`.
2. **Migration 0160**, backfill, schema doc, data dictionary.
3. Formulas (`split_excess`, `needs_inspection`, `check_rejected_serials`, `claim_qty`, `receipt_reversal_actual`) with
   boundary tests.
4. 9.1 inspection separation; 9.4 serial statuses.
5. 9.2, 9.3 excess (with `changes.revise` path and the proposal executor).
6. 9.5, 9.6 returns, claims, scrap; 9.9 unordered return and forms.
7. 9.7 reversal (receipts, service entries) and the readers.
8. 9.8 ERP receipts and the CLI.
9. Screens, exceptions, guides, demo script, demo cases; update the tests and evals of 10.1.
10. Evals E144-E151 by a separate agent from this spec; one full `scripts/check.py` and all evals; both demo resets and
    walk-throughs (E33, E34, E46).

## 15. Done criteria (Plan l.614)

- `python scripts/check.py` green (ruff, mypy strict, pytest, biome, tsc), including the boundary tests of 10.1.
- E144-E151 pass, and every existing eval passes after the updates listed in 10.1 (each listed in the report).
- `demo reset apex` and `demo reset brightwave` work; E33, E34 and E46 pass (the seals inspected by quality@).
- `docs/database-schema.md` regenerated after migration 0160; data dictionary, guides, demo script and CLAUDE.md updated.
- Every write in this block has its audit row; a test proves company A cannot read company B's receipts, returns,
  claims or excess.
