# Evals report

Run on 2026-11-23 03:47; AI evals used a scripted model.

| ID | What it checks | Result | Seconds | Key figures |
|---|---|---|---|---|
| E33 | The main demo company completes the whole flow, from low stock to a paid invoice, with separate people for each job | **PASS** | 41.8 | people / master rows loaded 10 / 703; items at or below reorder point ['SP-01']; recommended by the comparison (seal / copper wire) SUP-002 / SUP-005; orders issued touchless 1 |
| E34 | The whole application for a real user: the demo script from low stock to a paid invoice in a browser, every role guide followed as a new user, and every screen swept for errors, overflow and raw codes | **PASS** | 341.7 | people / master rows loaded 10 / 703; people per step (audit log) {'approved the requests': 2, 'approved the orders': 1, 'received the goods': 1, 'entered the invoices': 1, 'released the block': 1, 'planned the run': 1, 'approved the run': 1}; screens swept (role x screen x width) 274; steps taken at screens 95 |
| E66 | A later masters import keeps second-person controls: status, bank, policies, versions, history, nothing deleted | **PASS** | 18.7 | first import: audit rows / mode 1 / ['first']; first import: tables whose rows differ from the file none; order bound to a contract price (po, price_record id) PO-CKN-2627-00001, 1; rows the company holds differently from the file, untouched by the scenario {'supplier_document': 1} |
| E67 | The app runs without owner rights: its own login, insert-only audit by grant, nothing it could switch off | **PASS** | 15.2 | app login / control database p2p_eval_m2_control_app / p2p_eval_m2_control; flow: request / approved by / jobs taken / orders REQ-CKN-2627-00001 / ['planthead 200'] / 8 / ['PO-CKN-2627-00001']; jobs: total / refused by the database / failed otherwise 16 / 0 / 0; runtime engines sign in as {'tenants.control_engine()': 'p2p_eval_m2_control_app', 'routing.engine_for(slug)': 'p2p_eval_m2_control_app', 'tenants.tenant_engine(slug)': 'p2p_eval_m2_control_app'} |
| E68 | Held stock (quarantine, blocked) is never set aside, issued or counted; decisions hand usable stock on at once | **PASS** | 36.3 | mutations caught ['blocked stock counted as on hand', 'quarantine set aside before the decision']; checks 135; failed 0 |
| E69 | Late supply never covers an earlier need: at risk, not bought twice, one case for the buyer (Q17) | **PASS** | 70.5 | mutations caught ['date-blind cover', 'the late part bought again', 'date-blind cover (the count goes to the MRP plan)', 'date-blind cover (Brightwave: nothing set aside)', 'the buffer in calendar days']; checks 163; failed 1 |
| E70 | Safety stock is never set aside or issued without the manager's approved override (C4) | **PASS** | 22.0 | mutations caught ['safety stock ignored in reservation', 'override approved by the asker']; checks 78; failed 0 |
| E71 | The store check runs every morning and after short-close, cancel, promise and rejection, never re-raising a rejection | **PASS** | 56.7 | mutations caught ['no store check after a short-close', 'the job re-raising a rejected request']; checks 70; failed 0 |
| E72 | Replenishment counts only the stock part of open orders (DF-25): production orders never block a restock | **PASS** | 21.9 | mutations caught ['PO lines for production counted as restock on order']; checks 28; failed 0 |
| E73 | The planning agent explains shortages from tool results only; anything ungrounded falls back to the facts | **PASS** | 39.8 | mutations caught ['grounding check skipping numbers under 10', 'causes not compared']; checks 58; failed 0 |
| E74 | Two store checks and a count at once never set stock aside twice or buy twice (AT-09) | **PASS** | 30.6 | mutations caught ['the store-check lock removed', 'the store-check lock removed']; rounds 2; checks 31; failed 0 |
| E78 | Company states: setup, cutover, live, suspended; who may sign in; user creation in two databases is safe | **FAIL** | 20.2 |  |
| E79 | Setup is archived at cutover; live starts from a clean database with the configuration carried over | **FAIL** | 26.2 | setup test documents {'request': 'REQ-CKN-2627-00001', 'po': 'PO-CKN-2627-00001', 'receipt': 'GRN-CKN-2627-00001', 'invoice': 'PI-2627-000001'} |
| E80 | Readiness checks find every gap, including separation coverage, before a company may cut over or go live | **PASS** | 27.8 | apex: requesters warned (platform / oracle) {} / {}; brightwave: requesters warned (platform / oracle) {} / {}; plants {'a change waiting': "new blockers ['pending_changes']", 'a band gap (REQ-02 from 20,000.00)': "new blockers ['approval_bands']", 'the buyer verifies only their own proposals': "new blockers ['separation']", 'mutation: the maker counted as their own checker': 'caught', 'planthead deactivated': "new blockers ['approvers']", 'purchasemanager deactivated': "new blockers ['duties']", 'director deactivated': 'new blockers []'}; mutation: one person's two Admin roles counted twice caught |
| E81 | Cutover import: open orders, receipts, invoices and advances loaded with checks, control totals, nothing twice | **FAIL** | 25.0 |  |
| E82 | Imports follow the company state; opening stock and bank accounts are locked after go-live | **FAIL** | 21.2 |  |
| E83 | A company goes from setup to live and pays its cutover invoices in the first live payment run | **FAIL** | 44.0 | apex {}; brightwave {}; checks 8 of 10 passed |
| E85 | The budget counts each request line once: at its estimate until ordered, then at the order's price | **PASS** | 21.8 | A0 values by GL (oracle) {'MO-1': {'1410': '71580.00', '1420': '1350.00'}, 'MO-2': {'1410': '205200.00', '1420': '4500.00'}, 'MRP-2027-01/PLT-CKN': {'1410': '364090.00', '1420': '5400.00'}}; BG1 1410 committed (platform / oracle) 434070.00 / 434070.00; checks 78; failed 0 |
| E86 | Stock spends its budget when accepted at receipt, at the order price; services and charges when paid | **PASS** | 24.6 | checks 56; failed 0; actual rows 4 |
| E87 | A locked budget month is frozen: nothing posts into it, it is locked in order, and late postings move on | **PASS** | 24.2 | checks 67; failed 0 |
| E88 | Year end locks the year, records the open commitments it carries, and the new year counts them | **PASS** | 19.8 | checks 49; failed 0 |
| E89 | Budget supplements, returns and transfers are proposals another finance person approves | **PASS** | 19.1 | checks 33; failed 0 |
| E90 | Forward-buy recommendations equal an independent oracle: trend, dip, cover, months, limits and money | **PASS** | 40.0 | FB1 qty / net (platform) 746.0000 / 44982.36; FB1 qty / net (oracle) 746.0000 / 44982.36; checks 118; failed 0 |
| E91 | A forward buy is accepted by the buyer and approved by the person the rule names, as a new linked request | **PASS** | 37.2 | checks 91; failed 0 |
| E92 | Contract, capex, disabled and stale-priced items are never recommended; Brightwave's coffee saves too little | **PASS** | 22.4 | FB7 qty / net (oracle) 22.0000 / 1960.80 (shelf_life); checks 45; failed 0 |
| E93 | The forward-buy explanation is the template with the record's own numbers, or an AI text that invents none | **PASS** | 20.6 | checks 22; failed 0; explanation_source ai |
| E105 | An order's approval is of the version the approver saw; the approved version is recorded | **PASS** | 22.4 | truth rows compared 2; V1 totals v1 / v2 / v4 128148.00 / 106790.00 / 117469.00; mutations caught ['version_ignored: approve with version 1 after version 2', 'no_snapshot: snapshot rows after approving version 2', 'snapshot_first: snapshot rows after approving version 2']; checks 72 |
| E106 | A material change after approval is approved again; anything else carries the approval (Q19) | **PASS** | 21.4 | V2b totals v2 / v3 106790.00 / 106790.00; truth rows compared 4; mutations caught ['nonmaterial_restarts: V2b cut to 100 after acknowledgement', 'material_keeps: V2 +5 days after acknowledgement']; checks 72 |
| E107 | Changes add up from the approved version: dates, quantities, earlier dates | **PASS** | 21.1 | V3 revisions {'V3 +2 days (16 Dec)': 'v2 not material', 'V3 +2 again (18 Dec)': 'v3 material', 'V3 qty 100': 'v4 not material', 'V3 qty 110 (below the approved 120)': 'v5 not material', 'V3 14 Dec (4 days earlier than the approved 18 Dec)': 'v6 material', 'V3 +2 from the approved 14 Dec': 'v7 not material', 'V3 back to 14 Dec': 'v8 not material'}; truth rows compared 3; mutations caught ['previous_baseline: V3 +2 again (18 Dec)', 'no_abs: V3 14 Dec (4 days earlier than the approved 18 Dec)']; checks 63 |
| E108 | Supplier price rises add up from the first approved price; above 2% the price approver decides | **PASS** | 24.5 | V4 925.00: within / value False / 2400.00; V4b 923.10 / 923.11 within / out, value 2173.20; truth rows compared 4; mutations caught ['base_current: V4 925.00 within', 'variance_current: V4 925.00 value'] |
| E109 | A revised order is never touchless: a person approves the change | **PASS** | 18.4 | V5 PO-CKN-2627-00001: 1890.00 in PO-01; after +5 days submitted, step U08; mutations caught ['rule_reapproves: V5 +5 days', 'no_revision_check: V5 +5 days']; checks 20; failed 0 |
| E110 | Every decision from an email link needs a one-time code (D-07) | **PASS** | 21.0 | Apex requests (value, first approver) {'REQ-CKN-2627-00001': '11400.00 U07', 'REQ-CKN-2627-00002': '110400.00 U09'}; Brightwave request (value, chain) REQ-BLR-2627-00001: 124000.00 ['U08', 'U09']; mutations caught ['code_above_only: O1 Rs 11,400 approve without a code', 'reject_free: O1 Rs 11,400 reject without a code']; checks 97 |
| E111 | Approving an order above Rs 5,00,000 needs a one-time code for that version | **PASS** | 23.4 | O2 / O2b totals 533950.00 / 512592.00; truth rows compared 2; mutations caught ['code_after_writes: O2 wrong code', "ref_no_version: O2 the stored code's ref"]; checks 76 |

**28 of 33 passed.**

## Findings

- **high** (E78): company cutover: exit 1, state setup: cutover failed: tables neither carried nor left in the archive: budget_period_lock, budget_year_end, forward_buy, forward_buy_rule, po_approved_version

- **high** (E79): company cutover: exit 1, state setup: cutover failed: tables neither carried nor left in the archive: budget_period_lock, budget_year_end, forward_buy, forward_buy_rule, po_approved_version

- **high** (E79): one tenant_archive row expected: []
- **high** (E81): apex: company cutover exit 1: cutover failed: tables neither carried nor left in the archive: budget_period_lock, budget_year_end, forward_buy, forward_buy_rule, po_approved_version

- **high** (E81): brightwave: company cutover exit 1: cutover failed: tables neither carried nor left in the archive: budget_period_lock, budget_year_end, forward_buy, forward_buy_rule, po_approved_version

- **high** (E82): company cutover exit 1: cutover failed: tables neither carried nor left in the archive: budget_period_lock, budget_year_end, forward_buy, forward_buy_rule, po_approved_version

- **high** (E83): apex: company cutover exit 1, state setup: cutover failed: tables neither carried nor left in the archive: budget_period_lock, budget_year_end, forward_buy, forward_buy_rule, po_approved_version

- **high** (E83): brightwave: company cutover exit 1, state setup: cutover failed: tables neither carried nor left in the archive: budget_period_lock, budget_year_end, forward_buy, forward_buy_rule, po_approved_version

- **low** (E69): BT2: spec 9.3 says latest arrival 27 Nov; the location calendar gives 2026-11-26 (27 Nov is a Karnataka holiday): spec gap

## Details

### E33 The main demo company completes the whole flow, from low stock to a paid invoice, with separate people for each job

Checks: Phase 1: request, approval, price or quotation, order with GST, receipt, invoice match, payment run, closing; requester, approver, receiver, invoice enterer and payer differ for the same case; Rs 10,000 approves itself, up to Rs 1,00,000 the plant head, above that the director; orders above Rs 1,00,000 the purchase manager; payment runs the director

- people / master rows loaded: 10 / 703
- items at or below reorder point: ['SP-01']
- recommended by the comparison (seal / copper wire): SUP-002 / SUP-005
- orders issued touchless: 1
- orders waiting for the purchase manager: 3
- micro supplier: due date (45-day terms and agreement): 2026-12-21
- people per step (audit log): {'approved the requests': 1, 'approved the orders': 1, 'sent the orders': 1, 'received the goods': 1, 'entered the invoices': 1, 'planned the payment run': 1, 'approved the payment run': 1}
- documents with no audit row at all: none
- steps checked (one assertion each): 142
- emails sent (quotation requests, orders, payment advice, ...): 26

### E34 The whole application for a real user: the demo script from low stock to a paid invoice in a browser, every role guide followed as a new user, and every screen swept for errors, overflow and raw codes

Checks: Phase 1 acceptance: the flow works end to end for real people at real screens; a new user completes their first task from the guide; no screen shows an error, a blank panel, a raw code or sideways scrolling

- people / master rows loaded: 10 / 703
- people per step (audit log): {'approved the requests': 2, 'approved the orders': 1, 'received the goods': 1, 'entered the invoices': 1, 'released the block': 1, 'planned the run': 1, 'approved the run': 1}
- screens swept (role x screen x width): 274
- steps taken at screens: 95
- checks passed / failed: 411 / 0
- transcript and screenshots: evals/results/journeys/
- note: control tower with the seal invoice blocked: {'request_approval': 0, 'sourcing': 0, 'rfq_out': 0, 'award': 0, 'po_approval': 0, 'po_sent': 0, 'awaiting_receipt': 0, 'awaiting_invoice': 0, 'invoice_blocked': 1, 'awaiting_payment': 3}
- note: kpis at the end: {"cycle_time": {"approval": {"label": "request raised to approved", "cases": 4, "median": "0.0", "p90": "0.0"}, "ordering": {"label": "approved to order made", "cases": 4, "median": "0.0", "p90": "0.0"}, "po_approval": {"label": "order made to order approved", "cases": 4, "median": "0.0", "p90": "0.0"}, "dispatch": {"label": "order approved to sent", "cases": 4, "median": "0.0", "p90": "0.0"}, "delivery": {"label": "sent to goods received", "cases": 4, "median": "0.0", "p90": "0.0"}, "invoicing": {"label": "goods received to invoice received", "cases": 4, "median": "0.0", "p90": "0.0"}, "payment": {"label": "invoice received to paid", "cases": 4, "median": "19.0", "p90": "19.0"}, "total": {"

### E66 A later masters import keeps second-person controls: status, bank, policies, versions, history, nothing deleted

Checks: Task 0.6, DF-16, D-22, rule 8 (block 0c spec 3.1-3.5): the first import loads everything; any later import matches rows by key, never deletes, never changes a supplier's status or an existing supplier's bank details, adds new suppliers as drafts with unverified accounts, leaves out possible duplicates, never applies policies, approval rules or opening stock, adds price versions and contracts as drafts, keeps approvers; every added or changed row audited; a dry run writes nothing; a repeat changes nothing; tenant isolation. Decisions of 22 Nov: the admin API makes no Admin login and gives no role or limit without a second administrator

- first import: audit rows / mode: 1 / ['first']
- first import: tables whose rows differ from the file: none
- order bound to a contract price (po, price_record id): PO-CKN-2627-00001, 1
- rows the company holds differently from the file, untouched by the scenario: {'supplier_document': 1}
- dry run: exit code / first line: 1 / update by key (dry run, nothing written): would add 6, would change 4, unchanged 690, not in the file 13 (kept), in 0.3 s; 4 left out, 14 warnings
- import: exit code / first line: 1 / update by key: added 6, changed 4, unchanged 690, not in the file 13 (kept), in 0.31 s; 4 left out, 14 warnings
- oracle delta: added / changed / removed: {'supplier': 1, 'supplier_address': 1, 'supplier_bank_account': 1, 'supplier_contact': 1, 'contract': 1, 'price_record': 1} / {'department': 1, 'item': 1, 'supplier': 1, 'supplier_contact': 1} / none
- warnings expected / printed / missing: 13 / 14 / 0
- controls after the import (spec 3.5): kept
- audit: imports / row_add / row_change: 1 / 6 / 4
- the same copy again: first line: update by key: added 0, changed 0, unchanged 700, not in the file 13 (kept), in 0.19 s; 4 left out, 14 warnings
- admin API (decisions of 22 Nov): {'Admin login': 'refused (403)', 'role': 'proposed; maker approves 403, other admin 200; 0 -> 1', 'limit': 'proposed; maker approves 403, other admin 200; None -> 50000.00', 'own role': '403', 'own limit': '403'}
- mutation checks (rule broken on purpose: caught?): {'the import applies supplier.status from the file': 'caught in the database', "a new supplier's bank account keeps verified=true": 'caught in the database', 'the later import deletes and re-inserts': 'caught in the database'}
- note: rows the company held differently from the file before the import: {'supplier_document': [('SUP-002', 'cancelled_cheque', 'CHQ-0002')]}

### E67 The app runs without owner rights: its own login, insert-only audit by grant, nothing it could switch off

Checks: Task 1.7, D-25, DF-37 (block 0c spec 4.1-4.5, 5): every runtime path (API, worker, CLI, engines) signs in as the app login, which is no superuser, makes no database or role, bypasses no row security and owns nothing; it may select, insert, update and delete business tables, only select and insert audit_event and setup_change, only select alembic_version and the control tenant tables; every forbidden statement fails with 42501 and changes nothing; stored tenant URLs hold no password; health says restricted; the owner still migrates, and tables it makes later reach the app by default privileges

- app login / control database: p2p_eval_m2_control_app / p2p_eval_m2_control
- flow: request / approved by / jobs taken / orders: REQ-CKN-2627-00001 / ['planthead 200'] / 8 / ['PO-CKN-2627-00001']
- jobs: total / refused by the database / failed otherwise: 16 / 0 / 0
- runtime engines sign in as: {'tenants.control_engine()': 'p2p_eval_m2_control_app', 'routing.engine_for(slug)': 'p2p_eval_m2_control_app', 'tenants.tenant_engine(slug)': 'p2p_eval_m2_control_app'}
- /api/health: {'status': 'ok', 'database': 'ok', 'database_login': 'restricted'}
- as the app: refused statements (42501 with the spec's text): 10 of 10 (company), 2 of 2 (control)
- as the app: tables checked for the spec's grants: 157
- stored company connections / with a password or user: 1 / 0
- tenant migrate-all as the owner: exit code: 0
- a table the owner makes after migrating: the app may: ['DELETE', 'INSERT', 'SELECT', 'UPDATE']
- no APP_DATABASE_PASSWORD: MissingAppLogin: APP_DATABASE_PASSWORD is not set: the app signs in to the database with its own restricted login. Add it to .env (see .env.example), then run p
- modules using the owner's login outside tenants, demo and cli: none
- mutation checks (rule broken on purpose: caught?): {'the runtime engines use the owner URL': 'caught (174 findings)', 'the grant on audit_event includes update or delete': 'caught (1 findings)', 'default privileges missing': 'caught (1 findings)'}

### E68 Held stock (quarantine, blocked) is never set aside, issued or counted; decisions hand usable stock on at once

Checks: Task 4.1 (block 2 spec 3, 9.3 Q1-Q3): three qualities; held stock never reserved, issued or counted; an inspection receipt waits in quarantine and is not invoiceable; rejected at inspection goes to blocked; a manual hold of set-aside stock gives back the latest need first, then the store check buys; a decision to usable sets stock aside at once; holds API fields and errors; audit stock.hold / stock.hold_decide; ledger equals the quality view; tenant isolation

- mutations caught: ['blocked stock counted as on hand', 'quarantine set aside before the decision']
- checks: 135
- failed: 0
- note: Setup, not a spec rule: the castings (RM-01) are marked inspection_required in this company only, so an inspection receipt can be posted on the touchless contract order (the dataset's inspection items RM-06 and RM-07 are bought by quotation, which needs quotes and an award first).
- note: Spec gap: whether the store check run by a hold (OP-12) submits its request follows `store_check_submit` (22 Nov decision for event checks) is not said; Q3's request is checked for its lines and value only.
- note: Spec gap: the truth files' `step` values are the generator's own; each step must equal one state the eval saw, the last step the last state, compared only for the items of that scenario.

### E69 Late supply never covers an earlier need: at risk, not bought twice, one case for the buyer (Q17)

Checks: Task 6.1, Q17 (block 2 spec 4, 9.3 T1-T4, BT1-BT2): supply counts only if due by the latest arrival (need-by less the buffer in working days on the location calendar; a passed date is today); free stock goes first to short + at risk; only the short part is bought; one late_supply case per PO and material request (owner the buyer, escalation to the manager, due late_supply_case_days, buffer recorded), resolved by the system, closing rules; Exceptions row; tenant isolation

- mutations caught: ['date-blind cover', 'the late part bought again', 'date-blind cover (the count goes to the MRP plan)', 'date-blind cover (Brightwave: nothing set aside)', 'the buffer in calendar days']
- checks: 163
- failed: 1
- note: Spec ambiguity: a passed date counts as today for every supply part, so on 7 Dec (T4) MO-1's lines still out for quotation (dated by their 5 Dec need-by) are at risk too; the oracle applies the rule to every part and compares every line; the case is checked only for the order (one per PO and material request).
- note: Spec gap: the supplier's promise is per order (OP-23), so every open line of the acknowledged order takes it.
- note: Spec gap (calendar): 24 Nov 2026 is Guru Nanak's Birthday (central gazetted, closed), so T1's case made on Monday 23 Nov is due Thursday 26 Nov on the Mon-Sat plant calendar, not 25 Nov as 9.3 says; the eval counts on the location calendar (block 1 decision). BT2: 27 Nov is Kanakadasa Jayanthi in Karnataka, so the latest arrival is 26 Nov, not 27 Nov; the outcome (at risk) is the same.
- note: Spec gap: the API shape of `lines[].promised` / `latest_arrival` in a case is not fixed; dates are read as ISO.

### E70 Safety stock is never set aside or issued without the manager's approved override (C4)

Checks: Task 6.2, C4 (block 2 spec 5, 9.3 S1-S2): free = usable - set aside - safety stock; a manual issue above that is 409 with needs_override, available and safety_stock; an override is a proposal approved only by the asker's manager chain holding request.approve (never the asker); approved, it issues or sets aside and cuts the open request; safety stock changes wait for a second administrator; replenishment after the issue; audit; tenant isolation

- mutations caught: ['safety stock ignored in reservation', 'override approved by the asker']
- checks: 78
- failed: 0
- note: Spec ambiguity (5.1.5): the material override is 'bounded by the line's short + at_risk', yet S2 expects 10 set aside on a line whose 10 are already on a timely purchase request (short 0, at risk 0); the oracle bounds it by what the line lacks (need - issued - set aside), which gives the spec's 50 / 0.
- note: Spec ambiguity (5.3): for kind 'issue', whether `qty` is the whole issue (3) or the part taken from safety stock (1); the eval asks for the whole issue, as S1 says '3 issued'.
- note: S1 and S2 run on one reset (both start from reset; the V-belts are in no material request), with S2's safety change made before the first store check, as the scenario says.

### E71 The store check runs every morning and after short-close, cancel, promise and rejection, never re-raising a rejection

Checks: Task 6.5 (block 2 spec 6, 9.3 E1-E2, decision OP-14 of 22 Nov): job store_check daily 05:30 IST when auto_store_check, drafts unless store_check_submit, always queues the next run, audited, idempotent; events (close-balance, cancel, acknowledgement, rejection) re-run evaluation and a scoped store check; a rejected shortfall is not re-raised automatically but is by the button; tenant isolation

- mutations caught: ['no store check after a short-close', 'the job re-raising a rejected request']
- checks: 70
- failed: 0
- note: Spec conflict settled by the 22 Nov decision: section 9.3 E1 says the event's request goes 'to the plant head'; with `store_check_submit` off by default it stays a draft. The eval expects a draft and checks the approver only if the request was submitted.
- note: Spec ambiguity: 'late promise re-reserves' is read as the acknowledgement event running the evaluation and the scoped store check by itself (at risk and the case without a button or job); T1 has no free castings to set aside, so the re-reservation itself is exercised in E69 (T2, BT1).
- note: Spec gap: whether demo reset queues the first store_check job is not said; the eval queues it itself.
- note: Not exercised: `revise`, request withdrawal and the supplier agent's date change as triggers.

### E72 Replenishment counts only the stock part of open orders (DF-25): production orders never block a restock

Checks: Task 6.7, DF-25 (block 2 spec 7, 9.3 D1): an open PO line counts as on order for stock only by its request line's stock part (base qty less linked material request quantities); a line with no request line counts fully; a restock order still blocks a second restock; a second run makes nothing; tenant isolation

- mutations caught: ['PO lines for production counted as restock on order']
- checks: 28
- failed: 0
- note: The restock oracle uses min-max as the spec states it (order up to the maximum from the position) and the item's estimate (contract RM-01 650, spec 9.3); minimum order quantities are not in play for 60 castings.

### E73 The planning agent explains shortages from tool results only; anything ungrounded falls back to the facts

Checks: Task 11b.1, D49, rule 2 (block 2 spec 8): six read-only tools; an explanation is accepted only if its schema is valid, every short or at-risk line is present, each line's causes equal the engine's, and every number, date and document number appears in a tool result (no small-number exemption), else facts only; off by default (no model call), switched on by two administrators, kill switch; one explanation per snapshot; traced; permission and tenant

- mutations caught: ['grounding check skipping numbers under 10', 'causes not compared']
- checks: 58
- failed: 0
- note: Spec gap: a tool result carries free text (a hold's reason, a supplier name), so a number written there is 'grounded' by check 3 even when it is no figure of the engine; the injection case therefore uses words, and the causes check is what must catch a model that obeys it.
- note: Spec gap: whether a call to an unknown tool (refused by the runtime) still lets the answer be accepted is not said (check 4 says 'no tool but the six was called'); the eval requires nothing written and flags an accepted answer as low.
- note: Spec gap: the in-process `explain` is given the gateway; the API endpoint's gateway cannot be scripted, so the API is used only where no model call is made (off, 403, 404).
- note: Live run (--live): not part of this eval; OP-24's threshold stands in evals/README.md.

### E74 Two store checks and a count at once never set stock aside twice or buy twice (AT-09)

Checks: AT-09 (part), block 2 spec 6 and 11: concurrent demand and reservations: no double reservation or buying; set aside never above usable - safety per place; one purchase request per material request; the result equals the serial one

- mutations caught: ['the store-check lock removed', 'the store-check lock removed']
- rounds: 2
- checks: 31
- failed: 0
- note: Spec gap: whether a second store check started while one runs waits for the lock or is refused is not said; both 200 and 409 are accepted, the end state must be the serial one either way.

### E78 Company states: setup, cutover, live, suspended; who may sign in; user creation in two databases is safe

Checks: Task 7.1, D-04 (states Setup / Cutover / Live / Suspended and what each allows), D-22 (go-live by two administrators), DF-36 (user creation writes both databases safely; no Admin login made by one administrator)


### E79 Setup is archived at cutover; live starts from a clean database with the configuration carried over

Checks: Task 7.2, D-05 (testing in setup, archived at cutover; cutover rebuilds a clean live database), OP-6 (new database, the test database kept as an archive), OP-7 (what is carried), OP-22 (queued jobs cancelled)

- setup test documents: {'request': 'REQ-CKN-2627-00001', 'po': 'PO-CKN-2627-00001', 'receipt': 'GRN-CKN-2627-00001', 'invoice': 'PI-2627-000001'}

### E80 Readiness checks find every gap, including separation coverage, before a company may cut over or go live

Checks: Task 7.3 (readiness checks including separation coverage: every rule that needs a second or different person has enough eligible people), Plan line 1086 (go-live readiness requires two administrators), FR32 (five different people for the five duties)

- apex: requesters warned (platform / oracle): {} / {}
- brightwave: requesters warned (platform / oracle): {} / {}
- plants: {'a change waiting': "new blockers ['pending_changes']", 'a band gap (REQ-02 from 20,000.00)': "new blockers ['approval_bands']", 'the buyer verifies only their own proposals': "new blockers ['separation']", 'mutation: the maker counted as their own checker': 'caught', 'planthead deactivated': "new blockers ['approvers']", 'purchasemanager deactivated': "new blockers ['duties']", 'director deactivated': 'new blockers []'}
- mutation: one person's two Admin roles counted twice: caught
- checks: 65 of 65 passed
- note: spec gap: section 6 expects warnings for ['Meera Iyer'] on apex; the rules of 4.3 applied to the dataset give [] (resolve_chain refuses a request whose every approver is the requester)
- note: spec gap: section 6 expects warnings for ['Farah Khan', 'Vivek Menon'] on brightwave; the rules of 4.3 applied to the dataset give [] (resolve_chain refuses a request whose every approver is the requester)

### E81 Cutover import: open orders, receipts, invoices and advances loaded with checks, control totals, nothing twice

Checks: Task 7.4 (cutover import of open POs, receipts, invoices and advances, with checks), OP-9 (part-paid invoices refused), OP-10 (receipts post no stock), OP-11 (invoices re-matched by the platform's rules), OP-12 (year-to-date purchases for tax at source), OP-13 (carrier request per PO)


### E82 Imports follow the company state; opening stock and bank accounts are locked after go-live

Checks: Task 7.6, D-02 (imports depend on company state; opening stock and verified accounts locked after go-live), Base WP-7 (imports refused after go-live), block 0c update mode underneath


### E83 A company goes from setup to live and pays its cutover invoices in the first live payment run

Checks: Task 7.7 (evals: Setup to Live), Base WP-7 (a company goes Setup to Live; imports refused after go-live), D-04, D-05, D-22 (go-live by two administrators), OP-12 (year-to-date purchases for tax at source), plan acceptance gate 9 (the pilot client rehearses cutover)

- apex: {}
- brightwave: {}
- checks: 8 of 10 passed

### E85 The budget counts each request line once: at its estimate until ordered, then at the order's price

Checks: Task 3.7, FR27 (block 3 spec 3.3 BG0/BG1, 4.1, 4.5, 4.6): used = held by requests (open or sourcing lines of submitted or approved requests) + held by orders (open order balance before GST, less actuals) + spent; the request checked excluded; final prices; variance left = budget - the three parts, per location too; a cancelled order puts the estimate back; GET /api/budgets/check fields, 404, 403; tenant isolation

- A0 values by GL (oracle): {'MO-1': {'1410': '71580.00', '1420': '1350.00'}, 'MO-2': {'1410': '205200.00', '1420': '4500.00'}, 'MRP-2027-01/PLT-CKN': {'1410': '364090.00', '1420': '5400.00'}}
- BG1 1410 committed (platform / oracle): 434070.00 / 434070.00
- checks: 78
- failed: 0
- note: Spec gap: the variance rows per cost centre for request lines with no cost centre (A0's lines are the department's) are not described; only `left = budget - the three parts` on every row and the location rows' parts are checked.
- note: Spec gap: whether a request line returns to `open` or `sourcing` after its order is cancelled is not said; either status counts it at its estimate again (4.1), which is what is checked.

### E86 Stock spends its budget when accepted at receipt, at the order price; services and charges when paid

Checks: D-10 (block 3 spec 3.3 BG1, 4.1, 4.2): a stocked line's actual = accepted x order price in the acceptance month (source receipt), nothing at gate entry, never the rejected part; held by orders falls by the actual; a balance closed holds nothing; payment adds only charges for stocked lines and the taxable value of a service in the invoice's month; audit budget.actual once per receipt or invoice; tenant isolation

- checks: 56
- failed: 0
- actual rows: 4
- note: Setup, not a spec rule: SV-01 is bought directly from SUP-006 (agreed by the purchase manager) and the request line put on CC-MNT as the owner, since the request form takes no cost centre; the services are paid in one run with the wire's invoice (Finance proposes, the director approves, `manual_mark_paid` as the demo has it).
- note: Spec gap: 4.2 says the receipt actual's period is 'the month of the acceptance date'; for an inspection that is the inspection day (both in November here).

### E87 A locked budget month is frozen: nothing posts into it, it is locked in order, and late postings move on

Checks: D-09 (block 3 spec 3.3 PL1, 4.3, 4.4, 4.6, 4.7): lock only ended months, in order, once, with a reason, by budget.manage; a late actual goes to the first open month keeping event_period (audit budget.actual_moved); adjustments and imported budget rows for a locked month are refused; periods API and CLI; tenant isolation

- checks: 67
- failed: 0
- note: Spec gap: 4.3 calls a month 'future' until it has ended, so the current month (2026-12 on 7 December) is future; the spec still lets a payment of 7 December post into it and E89 adjusts it. The eval reads 'open' as 'ended and not locked' for the periods list only.
- note: Spec gap: the CLI's output format is not given; `budget periods` is checked for its exit code and that it names the months.

### E88 Year end locks the year, records the open commitments it carries, and the new year counts them

Checks: D-09, OP-5 (block 3 spec 3.3 YE1, 4.3, 4.6, 4.7): close allowed from 1 April by budget.manage; locks every month of the FY not yet locked; carried per GL, cost centre, location (held by requests, held by orders) and total; 409 twice, 422 early or for a later FY; GET years/{fy} = the close; open commitments count in the new FY, unspent budget lapses; CLI close-year; audit budget.year_close; tenant isolation

- checks: 49
- failed: 0
- note: Spec gap: `carried` rows name cost_centre_code null for A0's department-level lines; the spec's example keys rows by (gl_code, cost_centre_code, location_code) and the eval matches on that key.

### E89 Budget supplements, returns and transfers are proposals another finance person approves

Checks: D-09, OP-8 (block 3 spec 4.3, 4.6): POST /api/budgets/adjustments by budget.manage -> 201 pending proposal; the proposer never approves; on approval `budget` rows of kind adjustment with reason and proposal id (a transfer two rows summing 0); open periods only; 422 for bad amounts, holders, GL, reason; the check and the variance read the new budget; audit budget.adjust; tenant isolation

- checks: 33
- failed: 0
- note: Spec gap: 4.3 says adjustments go 'into open periods only' while 4.3 also calls a month that has not ended 'future'; the spec's own scenario adjusts 2026-12 on 7 December, so the eval reads 'open' as 'not locked'. The 422 for a 'future-FY-only period' in 4.6 is not explained and is not tested.
- note: Spec gap: the transfer's `to` object is not specified beyond '{...}'; the eval sends {gl_code, cost_centre_code} and accepts the rows in either order.

### E90 Forward-buy recommendations equal an independent oracle: trend, dip, cover, months, limits and money

Checks: Task 13.5 (block 3 spec 3.4 FB1-FB5, FB8, 5.1-5.4, 5.6): price now = newest counting benchmark; trend = least squares of 12 monthly averages; dip above min_gap_pct; cover from usable stock and open supply; months 1..max_cover with net per unit above 0; storage and budget limits; whole packs from the latest month; recommend only when net > min_net_saving_inr; one live record per item and location (superseded, lapsed); every evaluation in rule_evaluation; preview writes nothing; tenant isolation

- FB1 qty / net (platform): 746.0000 / 44982.36
- FB1 qty / net (oracle): 746.0000 / 44982.36
- checks: 118
- failed: 0
- note: Spec gap: 5.2's budget limit takes 'the least available - requested of the anchor request's groups'; read literally that includes the MRP request's 1420 (cartons) group, Rs 3,88,750 left, which would cap FB1 at 422 kg. The spec's own FB1 (746 kg) implies the wire line's own account (1410), which the oracle uses.
- note: Spec gap: the `limit` when several bounds meet is not ordered; the oracle names the bound that cut the quantity last (budget or storage before holding, shelf life, max_cover_months); `need` is never produced.
- note: Setup, not a spec rule: FB4 is entered on the same day as FB1 in the second company, so the tie rule (highest price) makes 870 the price now; a second reset for FB4 alone would give the same answer.

### E91 A forward buy is accepted by the buyer and approved by the person the rule names, as a new linked request

Checks: Task 13.5 (block 3 spec 3.4 FB6, 5.4, 5.6): accept (po.create) with a reason and whole packs up to the recommendation; a forward_buy.approve proposal and task for the forward_buy rule's person, never the buyer; revalidation 409 with new_id; approval writes a planning request (stock part all, anchor's need-by, department, estimate) through the normal chain and budget check; the anchor never grows; reject with a reason, not offered again until a newer benchmark; audit; tenant isolation

- checks: 91
- failed: 0
- note: Spec gap: the 409 body for a changed recommendation is `{detail, new_id}`; whether new_id sits at the top or inside detail is not fixed, both are read.

### E92 Contract, capex, disabled and stale-priced items are never recommended; Brightwave's coffee saves too little

Checks: Task 13.5 (block 3 spec 3.4 FB7/FB8, 5.1-5.3, 7): eligibility (stocked goods, not capex, rule enabled, no contract price in force), no_fresh_benchmark without a delivered fresh benchmark, not_below_trend, saving_too_small, shelf life; the preview writes nothing; a forward-buy request counts as stock on order for replenishment; tenant isolation

- FB7 qty / net (oracle): 22.0000 / 1960.80 (shelf_life)
- checks: 45
- failed: 0
- note: Setup: the replenishment request REQ-BLR-2627-00003 could not be cancelled; the skip is not isolated and not tested.
- note: Spec gap: FB7's spec numbers (qty 33, net 2,789.86) cover three months, but the dataset gives OS-03 a shelf life of 3 months and OP-24 allows only months m <= shelf - 1 = 2, which gives 22 kg and Rs 1,960.80 (still saving_too_small). The oracle follows the rules; the truth file is compared with them (medium).
- note: Spec gap: the falling-trend mutation cannot be shown on the datasets: the only falling item (LP-01) is capex and its category disabled, so it never reaches the trend test.

### E93 The forward-buy explanation is the template with the record's own numbers, or an AI text that invents none

Checks: Task 13.5, rule 2 (block 3 spec 5.5): the template sentence with numbers only from the record; the AI step (one call, summary <= 600 characters, at most 3 risks) passes an evidence check of every number and date or the template stays; explanation_source template or ai; audit; tenant isolation

- checks: 22
- failed: 0
- explanation_source: ai
- note: the AI explanation: Buying 746 kg now at Rs 828.00 a kg saves Rs 63,511.45 before holding and Rs 18,529.09 to hold the stock at 1.5 % a month, for a net saving of Rs 44,982.36. The purchase covers February to April 2027 at about 248.71 kg a month, tying up Rs 6,86,320.00 until April 2027. Risks: price may fall further;
- note: Spec gap: the template's numbers 12 (the trend window) and 1.5% (the rule's holding cost) are not record fields in 5.6; the number check accepts them from the rule in force.

### E105 An order's approval is of the version the approver saw; the approved version is recorded

Checks: Task 0.2, DF-07 (block 4 spec 4.1 rules 1-3, 4.3, V1): each step carries the version of its round; approve and reject with a stale version are 409 and write nothing; approving records po.approved_version and one po_approved_version snapshot (total, band, rule, approver, lines) in the same step; audit po.approve with version and with_code; omitted version approves the current one; amounts against the oracle and the truth file; tenant check

- truth rows compared: 2
- V1 totals v1 / v2 / v4: 128148.00 / 106790.00 / 117469.00
- mutations caught: ['version_ignored: approve with version 1 after version 2', 'no_snapshot: snapshot rows after approving version 2', 'snapshot_first: snapshot rows after approving version 2']
- checks: 72
- failed: 0
- note: Spec 4.3 'omitted: approves the current version and records it' is checked on the same order after a carried cut to 90 and a material rise to 110 (version 4), not on a second order: the rule is the same.
- note: A refused stale decision is checked to write nothing by counting audit rows of the order and comparing its steps and status before and after.

### E106 A material change after approval is approved again; anything else carries the approval (Q19)

Checks: Task 0.2, Q19, AT-08 (block 4 spec 4.1 rules 4-7 and 9, V2, V2b): a change is measured from the last approved version; material means approval again (submitted, ack cleared, new round with the new version, reasons and baseline on the revision); otherwise the approval carries (approved_version unchanged, ack cleared, new respond link); sending a version changed materially after approval is refused; tenant check

- V2b totals v2 / v3: 106790.00 / 106790.00
- truth rows compared: 4
- mutations caught: ['nonmaterial_restarts: V2b cut to 100 after acknowledgement', 'material_keeps: V2 +5 days after acknowledgement']
- checks: 72
- failed: 0
- note: V2's old respond link was already used by the supplier's acceptance, so its refusal is expected on two grounds; V2b checks the version-bound refusal on an unused link of version 2 after version 3 is mailed.
- note: The send guard is forced by inserting a po_revision row (reapproval true, version above approved_version) and raising po.version as the owner, then removed again; the spec names the guard, not how a test reaches it.

### E107 Changes add up from the approved version: dates, quantities, earlier dates

Checks: Task 0.2, DF-07, OP-2, OP-5, OP-22 (block 4 spec 4.1 rule 4, 4.3, V3): every revision is compared with the last approved version; two +2 day slips make the second material; a quantity back below the approved one is not; an earlier date beyond tolerance is; +2 then -2 is not; reasons on the revision and in the history; tenant check

- V3 revisions: {'V3 +2 days (16 Dec)': 'v2 not material', 'V3 +2 again (18 Dec)': 'v3 material', 'V3 qty 100': 'v4 not material', 'V3 qty 110 (below the approved 120)': 'v5 not material', 'V3 14 Dec (4 days earlier than the approved 18 Dec)': 'v6 material', 'V3 +2 from the approved 14 Dec': 'v7 not material', 'V3 back to 14 Dec': 'v8 not material'}
- truth rows compared: 3
- mutations caught: ['previous_baseline: V3 +2 again (18 Dec)', 'no_abs: V3 14 Dec (4 days earlier than the approved 18 Dec)']
- checks: 63
- failed: 0
- note: Spec gap: for an earlier date the reason's `<d>` is read as the size of the move ('moved 4 days'); a signed 'moved -4 days' is accepted too (the comparison drops the sign).
- note: The quantity boundary 121 is checked only as a refusal (the request asked for 120), as spec 4.3 says.

### E108 Supplier price rises add up from the first approved price; above 2% the price approver decides

Checks: DF-08, OP-3, OP-16 (block 4 spec 4.2, 4.3, V4, V4b): a supplier's price is judged exactly from the first approved price; the buyer alone accepts up to supplier_price_pct in all; above it the buyer's acceptance makes the po.supplier_price proposal valued (new - base) x quantity for the price_variance person; C1 kept; base_value and rise_pct shown; amounts against the oracle and the truth file; tenant check

- V4 925.00: within / value: False / 2400.00
- V4b 923.10 / 923.11: within / out, value 2173.20
- truth rows compared: 4
- mutations caught: ['base_current: V4 925.00 within', 'variance_current: V4 925.00 value']
- checks: 110
- failed: 0
- note: V4b's 923.11 is asked on the same order after 923.10 was accepted and approved, not on a separate run: the base stays the first approved 905.00 (OP-3) and the quantity 120, so the spec's 2,173.20 is the same; and the wrong rule (base = current 923.10) would call it within, so the case tells the rules apart.
- note: Who decides a price above tolerance is read from the price_variance rule row and the profile's approver roles; the proposal's payload 'approver' or its task's assignee is accepted as naming that person.

### E109 A revised order is never touchless: a person approves the change

Checks: Rev s5, OP-7 (block 4 spec 4.1 rules 3 and 8, V5): a touchless order's approval by the rule records its first approved version ('rule:PO-01', the contract price); after a material revision the touchless tests run again, touchless.revision fails with its text, the order is not approved by the rule but waits for the next band's person (the purchase manager), who approves the new version; tenant check

- V5: PO-CKN-2627-00001: 1890.00 in PO-01; after +5 days submitted, step U08
- mutations caught: ['rule_reapproves: V5 +5 days', 'no_revision_check: V5 +5 days']
- checks: 20
- failed: 0
- note: The shaft request is dated 14 Dec like the other scenarios (demo_cases dates it a week out); the spec's 'Rs 44,000' example is not the figure of 10 shafts at the contract price, so the eval works the total out itself.
- note: Touchless checks are read from po.touchless_checks (a list of {test, passed, detail}; 'code' or 'name' are accepted for the test's name).

### E110 Every decision from an email link needs a one-time code (D-07)

Checks: D-07, OP-9, OP-10 (block 4 spec 5.1, O1): approve, reject and return from /approve/{token} each need a valid code whatever the amount; the code is mailed only to the login the link names and never shown on the page or in another mail; wrong codes count down and lock after five; a code works once, for one step of one round for one person; expired codes are refused; the email and page carry the code note and the app link; audit otp.* and 'emailed link'; Apex and Brightwave; a link re-signed for another company decides nothing; tenant check

- Apex requests (value, first approver): {'REQ-CKN-2627-00001': '11400.00 U07', 'REQ-CKN-2627-00002': '110400.00 U09'}
- Brightwave request (value, chain): REQ-BLR-2627-00001: 124000.00 ['U08', 'U09']
- mutations caught: ['code_above_only: O1 Rs 11,400 approve without a code', 'reject_free: O1 Rs 11,400 reject without a code']
- checks: 97
- failed: 0
- note: Codes and approval emails are read from the mail caught in this process (the worker sends the queued approval emails into it; the codes go to it straight, as platform/otp.py sends them).
- note: Code ageing (a lock's 30-second resend gap, an expired code) is done by moving one_time_code times back as the owner; the spec names the rules, not a clock to move.
- note: The cross-company link is re-signed with email_links.make (unchanged by block 4) for Apex's slug and Brightwave's request and approver: it must decide nothing and send no code.

### E111 Approving an order above Rs 5,00,000 needs a one-time code for that version

Checks: Task 1.2, D-07, OP-11, OP-14, OP-15 (block 4 spec 5.2, O2, O2b): needs_code = total with GST above approval_otp_above; approve without, with a wrong or another version's code is 409 and writes nothing; the code goes only to the person whose turn it is, ref <po>:<version>:<round>:<step>, resend gap 30 s; the right code approves (with_code true, otp.use); reject needs none; at or below the limit no code (409 'needs no code'); totals against the oracle and the truth file; tenant check

- O2 / O2b totals: 533950.00 / 512592.00
- truth rows compared: 2
- mutations caught: ['code_after_writes: O2 wrong code', "ref_no_version: O2 the stored code's ref"]
- checks: 76
- failed: 0
- note: The 30-second resend gap is checked by asking twice in a row; the eval does not wait it out.
- note: 'Nothing written' on a refused approval is the order's audit rows, steps and status unchanged; the otp.wrong audit row the code service writes is on the code's own entity (po_approval:<ref>), not on the order.
- note: The buyer asking for the director's code may be 403 (no po.approve) or 409 (not her turn); both are accepted.
