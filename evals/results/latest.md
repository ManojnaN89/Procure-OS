# Evals report

Run on 2026-11-23 00:08; AI evals used a scripted model.

| ID | What it checks | Result | Seconds | Key figures |
|---|---|---|---|---|
| E33 | The main demo company completes the whole flow, from low stock to a paid invoice, with separate people for each job | **PASS** | 18.5 | people / master rows loaded 10 / 567; items at or below reorder point ['SP-01']; recommended by the comparison (seal / copper wire) SUP-002 / SUP-005; orders issued touchless 1 |
| E35 | ERP demand files load exactly once, with every bad row explained | **PASS** | 11.3 | Brightwave joiner rows (kits / plan) 12 / 2; Brightwave requirements checked against kit x joiners 12; Brightwave planted joiner faults caught 6 of 6; rows in the files 25 |
| E37 | Stock is set aside earliest need-by first and only the shortfall is bought | **PASS** | 4.1 | production orders 2; lines checked against the oracle 20; lines set aside differently 0; items short per order {'MO-1': 4, 'MO-2': 8} |
| E47 | Cancelling a production order releases its stock to the next order at once, never stocked and bought twice | **PASS** | 5.3 | production orders (one planted) 3; lines checked against the oracle, before and after 30 / 20; lines differing (first check / after the cancel) 0 / 0; lines whose request was not cut to what stock leaves short 0 |
| E51 | Control changes wait for a second administrator; nobody approves their own change or their own access | **PASS** | 15.2 | kinds of change checked / correct 11 / 11; per kind {'policy (budget_policy)': 'ok', 'tolerance (price_pct)': 'ok', 'approval rule version (REQ-02 to the purchase manager)': 'ok', 'buying route version': 'ok', 'supplier selection version': 'ok', 'touchless rule version': 'ok', 'role given (Store to the buyer)': 'ok', 'approval limit (plant head, orders)': 'ok', 'automation level (rfq.award to act alone)': 'ok', 'person deactivated (AP)': 'ok', 'manager changed (storekeeper)': 'ok'}; kill switch: gate before / on at once / off waits / after approval True / 200 True / 202 True / 200 True; rejected: value kept / approve later 0.00 / 409 |
| E60 | MRP results become material requests; net lines buy in full; reservation earliest need-by first | **PASS** | 17.7 | mutations caught ['MRP results skipped by the material requests', 'a net line sets free stock aside']; MRP lines checked (Chakan + Sanand) 20; material request lines compared at each step 40; M2 changes applied 10 |
| E61 | ERP cuts take open purchase requests first, earlier dates take nothing held, bad changes are refused | **PASS** | 38.9 | A1 dry-run changes listed 11; mutations caught ['reservation released before cutting the purchase request', 'an order moved earlier takes stock set aside for a later one']; checks 200; failed 0 |
| E62 | A cancel after the buyer has it raises one case per document, owned, escalated and due on the company calendar | **PASS** | 22.9 | mutations caught ['a case raised for a line not yet with the buyer', "a cancel leaves the order's own open purchase requests uncut", 'due date on a Mon-Fri calendar without holidays (NOT separated)']; cases checked 6; checks 190; failed 1 |
| E63 | An increase buys on a new request, never takes stock held for another order, checks only the changed items | **PASS** | 29.5 | mutations caught ['an increase takes stock set aside for a later order (A4)', 'the submitted request grown instead of a new one (A4)', 'the store check after an increase not scoped to the changed items', 'the submitted request grown instead of a new one (B3)']; A4 new request {'RM-01': '10', 'RM-03': '10', 'RM-05': '10', 'RM-06': '20', 'RM-07': '10', 'RM-08': '18', 'RM-09': '10', 'RM-10': '10', 'RM-11': '2', 'PK-01': '10'}; B3 new request {'LP-02': '1', 'MN-01': '1', 'DK-01': '1', 'HS-01': '1', 'KB-01': '1', 'SW-01': '1'}; checks 109 |
| E64 | Changes after issue: a store case for what was issued too many, issued lines reopen when the need goes up | **PASS** | 39.3 | mutations caught ['no store case for issued too many', 'an issued line not reopened when the need goes up']; edges at the issued quantity 30 (equal), 31 (one above), 29 (one below); checks 206; failed 0 |
| E65 | A moved start date cancels the old request and makes a new one that takes the released stock first | **PASS** | 25.0 | mutations caught ['new lines for a cancelled material request skipped (DF-19)', 'the moved row loaded as extra demand without the cancel']; checks 57; failed 0 |
| E66 | A later masters import keeps second-person controls: status, bank, policies, versions, history, nothing deleted | **PASS** | 14.3 | first import: audit rows / mode 1 / ['first']; first import: tables whose rows differ from the file none; order bound to a contract price (po, price_record id) PO-CKN-2627-00001, 1; rows the company holds differently from the file, untouched by the scenario {'supplier_document': 1} |
| E67 | The app runs without owner rights: its own login, insert-only audit by grant, nothing it could switch off | **PASS** | 11.5 | app login / control database p2p_eval_m2_control_app / p2p_eval_m2_control; flow: request / approved by / jobs taken / orders REQ-CKN-2627-00001 / ['planthead 200'] / 7 / ['PO-CKN-2627-00001']; jobs: total / refused by the database / failed otherwise 14 / 0 / 0; runtime engines sign in as {'tenants.control_engine()': 'p2p_eval_m2_control_app', 'routing.engine_for(slug)': 'p2p_eval_m2_control_app', 'tenants.tenant_engine(slug)': 'p2p_eval_m2_control_app'} |
| E68 | Held stock (quarantine, blocked) is never set aside, issued or counted; decisions hand usable stock on at once | **PASS** | 22.2 | mutations caught ['blocked stock counted as on hand', 'quarantine set aside before the decision']; checks 135; failed 0 |
| E69 | Late supply never covers an earlier need: at risk, not bought twice, one case for the buyer (Q17) | **PASS** | 54.7 | mutations caught ['date-blind cover', 'the late part bought again', 'date-blind cover (the count goes to the MRP plan)', 'date-blind cover (Brightwave: nothing set aside)', 'the buffer in calendar days']; checks 163; failed 1 |
| E70 | Safety stock is never set aside or issued without the manager's approved override (C4) | **PASS** | 19.1 | mutations caught ['safety stock ignored in reservation', 'override approved by the asker']; checks 78; failed 0 |
| E71 | The store check runs every morning and after short-close, cancel, promise and rejection, never re-raising a rejection | **PASS** | 49.0 | mutations caught ['no store check after a short-close', 'the job re-raising a rejected request']; checks 70; failed 0 |
| E72 | Replenishment counts only the stock part of open orders (DF-25): production orders never block a restock | **PASS** | 18.2 | mutations caught ['PO lines for production counted as restock on order']; checks 28; failed 0 |
| E73 | The planning agent explains shortages from tool results only; anything ungrounded falls back to the facts | **PASS** | 34.5 | mutations caught ['grounding check skipping numbers under 10', 'causes not compared']; checks 58; failed 0 |
| E74 | Two store checks and a count at once never set stock aside twice or buy twice (AT-09) | **PASS** | 28.4 | mutations caught ['the store-check lock removed', 'the store-check lock removed']; rounds 2; checks 31; failed 0 |

**20 of 20 passed.**

## Findings

- **low** (E62): mutation 'due date on Mon-Fri without holidays': no case's due date told the calendars apart (['both', 'both', 'both', 'both', 'both', 'both'])
- **low** (E69): BT2: spec 9.3 says latest arrival 27 Nov; the location calendar gives 2026-11-26 (27 Nov is a Karnataka holiday): spec gap

## Details

### E33 The main demo company completes the whole flow, from low stock to a paid invoice, with separate people for each job

Checks: Phase 1: request, approval, price or quotation, order with GST, receipt, invoice match, payment run, closing; requester, approver, receiver, invoice enterer and payer differ for the same case; Rs 10,000 approves itself, up to Rs 1,00,000 the plant head, above that the director; orders above Rs 1,00,000 the purchase manager; payment runs the director

- people / master rows loaded: 10 / 567
- items at or below reorder point: ['SP-01']
- recommended by the comparison (seal / copper wire): SUP-002 / SUP-005
- orders issued touchless: 1
- orders waiting for the purchase manager: 3
- micro supplier: due date (45-day terms and agreement): 2026-12-21
- people per step (audit log): {'approved the requests': 1, 'approved the orders': 1, 'sent the orders': 1, 'received the goods': 1, 'entered the invoices': 1, 'planned the payment run': 1, 'approved the payment run': 1}
- documents with no audit row at all: none
- steps checked (one assertion each): 142
- emails sent (quotation requests, orders, payment advice, ...): 26

### E35 ERP demand files load exactly once, with every bad row explained

Checks: Plan P1-29: production orders with their component lines, MRP results, sales orders and the demand plan are imported from the client's ERP files; every rejected row is listed with its reason; the same ERP order is never loaded twice; a changed order is applied and listed with what changed (Q21, 20 Nov: the change propagates); one company never sees another's imports. For an IT services company the joiner kits and the joiner plan load the same way: each joiner row becomes its kit's items times the joiners, by the start date, for the joiner's department, and one material request per plan and department

- Brightwave joiner rows (kits / plan): 12 / 2
- Brightwave requirements checked against kit x joiners: 12
- Brightwave planted joiner faults caught: 6 of 6
- rows in the files: 25
- requirements checked against the BOM: 20
- planted faults caught: 8 of 8
- rows loaded twice: 0

### E37 Stock is set aside earliest need-by first and only the shortfall is bought

Checks: Plan P1-30 and step 2 of the flow: each imported production order gets a material request; the store check sets free stock aside earliest need-by first and never more than is on the shelf; only the shortfall becomes a purchase request, priced at the contract price else the last price paid else the standard price, sent to the approver the rule table names; the plan's MO-1 example (Rs 72,930 for four items short) holds; issue at production start takes exactly what was set aside; nothing is set aside or bought twice; only the store may do it; supply counts for a need only when due by its date (rule Q17): set aside plus timely coming never exceeds the need

- production orders: 2
- lines checked against the oracle: 20
- lines set aside differently: 0
- items short per order: {'MO-1': 4, 'MO-2': 8}
- purchase request value per order: {'MO-1': '72930.00', 'MO-2': '209700.00'}
- MO-1, the plan's four items: Rs 72930.00
- lines with cover split by date (Q17): 20

### E47 Cancelling a production order releases its stock to the next order at once, never stocked and bought twice

Checks: Plan P1-30 and step 2 of the flow: cancelling an order releases its reservation (a reason is needed; only the store; once); the freed stock is set aside at once for the open orders earliest need-by first, each up to what it still lacks, never more than is on the shelf; a purchase request still open for that shortfall is cut by as much (set aside plus timely coming never exceeds the need, rule Q17) and the next check buys nothing; purchase requests made only for the cancelled order and not yet with the buyer are cut and cancelled (Q21, 20 Nov); issue never moves more than is set aside; the cancel is audited

- production orders (one planted): 3
- lines checked against the oracle, before and after: 30 / 20
- lines differing (first check / after the cancel): 0 / 0
- lines whose request was not cut to what stock leaves short: 0
- freed per item: {'RM-03': '50', 'RM-05': '50', 'RM-06': '40.000', 'RM-07': '50', 'RM-08': '50.000', 'RM-09': '50', 'RM-10': '50', 'RM-11': '10.0', 'PK-01': '20.000'}
- edges hit: {'exact': ['RM-03->MO-3'], 'less': ['RM-05->MO-2', 'RM-06->MO-2', 'RM-07->MO-2', 'RM-08->MO-2', 'RM-09->MO-2', 'PK-01->MO-2'], 'more': ['RM-10->MO-3', 'RM-11->MO-3']}
- purchase requests left after the cancel: []

### E51 Control changes wait for a second administrator; nobody approves their own change or their own access

Checks: D-22 (second administrator): a change to rules, rule versions, policies, tolerances, automation, roles, limits, logins and managers applies only when a different administrator approves it; the proposer never approves; a rejection keeps the old value; stopping automation is immediate; an unapproved change expires after seven days; one change at a time per setting; the history shows maker and approver; nobody widens or approves their own access

- kinds of change checked / correct: 11 / 11
- per kind: {'policy (budget_policy)': 'ok', 'tolerance (price_pct)': 'ok', 'approval rule version (REQ-02 to the purchase manager)': 'ok', 'buying route version': 'ok', 'supplier selection version': 'ok', 'touchless rule version': 'ok', 'role given (Store to the buyer)': 'ok', 'approval limit (plant head, orders)': 'ok', 'automation level (rfq.award to act alone)': 'ok', 'person deactivated (AP)': 'ok', 'manager changed (storekeeper)': 'ok'}
- kill switch: gate before / on at once / off waits / after approval: True / 200 True / 202 True / 200 True
- rejected: value kept / approve later: 0.00 / 409
- expiry: days to expire / approve after / value: 7 / 409 / 5
- duplicate pending change / another setting: 409 / 202
- add a person as Admin: 403
- own access, at once: {'give yourself a role': 403, 'set your own limit': 403, 'deactivate yourself': 409}
- admin2 approves a change to their own (role, limit, login, manager): {'role': True, 'limit': True, 'login': True, 'manager': True}
- mutation checks (rule broken on purpose: caught?): {'the proposer may approve': 'caught', 'a change applies before approval': 'caught', 'the person a change is for may approve it': 'caught', 'a change waits thirty days': 'caught'}

### E60 MRP results become material requests; net lines buy in full; reservation earliest need-by first

Checks: Task 6.4 (block 1 spec section 3): every open MRP requirement gets a material request line; one request per MRP plan and location ('<plan>/<location>', source mrp, no product, need-by the earliest line, never moved off a Sunday); gross lines net stock like production orders, earliest need-by first across orders and plans; net lines never take free stock in place of buying; the same file again loads nothing; a re-sent plan is a change (open purchase requests cut); tenant isolation

- mutations caught: ['MRP results skipped by the material requests', 'a net line sets free stock aside']
- MRP lines checked (Chakan + Sanand): 20
- material request lines compared at each step: 40
- M2 changes applied: 10
- checks: 92
- failed: 0
- note: Spec gap: the shape of the import's `material_requests` list is not specified for a request made by an import; only the new Sanand request in the table is checked (E65 checks the list for a closed request).

### E61 ERP cuts take open purchase requests first, earlier dates take nothing held, bad changes are refused

Checks: Task 6.3, Q21 (block 1 spec 4.1-4.5): a known row with new values is applied; a cut takes open purchase request cover first (not yet with the buyer), then the reservation, released stock handed on earliest need-by first; an earlier date moves open request lines and takes no stock held for another order; a later date moves only the material request; refused rows carry the spec's reasons and the valid rows of the same file apply; dry run writes nothing; every change audited; a second import changes nothing; tenant isolation

- A1 dry-run changes listed: 11
- mutations caught: ['reservation released before cutting the purchase request', 'an order moved earlier takes stock set aside for a later one']
- checks: 200
- failed: 0
- note: Spec gap: when a header moves an order's need-by and the same import's lines carry the new date, the spec does not say whether those line rows count as changes or as loaded before; A3's change count is checked only as at least one, its effects exactly.
- note: Spec gap: whether one changed row (a header cancel, a joiner row) is one change record or one per requirement is not fixed; `changed` is checked as rows changed, the records as at least that many.
- note: Not exercised: an earlier date when a purchase order promises later (OP-4 expedite case); no spec scenario puts an order with the buyer before an earlier change.

### E62 A cancel after the buyer has it raises one case per document, owned, escalated and due on the company calendar

Checks: Task 6.3, Q21 (block 1 spec 4.2, 4.4, 5.2): a cancelled ERP order releases its reservations (handed on earliest need-by first) and cuts purchase requests not yet with the buyer; with the buyer or on a PO, one demand_change case per document for the excess (owner the document's buyer, escalation the owner's manager, due in demand_change_case_days working days on the company calendar), on Exceptions with its amount; closing by the assignee only, once, audited; the manual cancel follows the same rules; tenant isolation

- mutations caught: ['a case raised for a line not yet with the buyer', "a cancel leaves the order's own open purchase requests uncut", 'due date on a Mon-Fri calendar without holidays (NOT separated)']
- cases checked: 6
- checks: 190
- failed: 1
- note: Spec gap: the working hours of the company calendar are not given, so a due date is checked by its day: 2 working days after the day the case was made (1 when it was made before the working day began).
- note: Spec gap: the Exceptions 'amount' is 'paise' (spec 5.2): read as rupees to the paisa or a whole number of paise.
- note: Spec gap: a joiner case's title is not given; it is checked to say 'cancelled' and name its document.
- note: Spec gap: whether a store outcome on a buyer case is an 'unknown outcome' (422) is not said; checked as low.
- note: Spec gap: the next step of a buyer case on a request with no document yet is not given; it is not checked.
- note: Spec gap: the case owner when a document's buyer is not a person with the Buyer role is the category's buyer, else the first Buyer; both demo companies have one buyer, so the owner is that person.

### E63 An increase buys on a new request, never takes stock held for another order, checks only the changed items

Checks: Task 6.3, Q21 'up re-runs the store check' (block 1 spec 4.2, 4.3): need raised; the store check runs at the end of the import for the changed item-locations only (all open lines there, earliest need-by first) with submit; stock set aside for another order is never taken; a submitted request never grows, so the increase is a new request; other orders' reservations unchanged; a second import changes nothing; tenant isolation

- mutations caught: ['an increase takes stock set aside for a later order (A4)', 'the submitted request grown instead of a new one (A4)', 'the store check after an increase not scoped to the changed items', 'the submitted request grown instead of a new one (B3)']
- A4 new request: {'RM-01': '10', 'RM-03': '10', 'RM-05': '10', 'RM-06': '20', 'RM-07': '10', 'RM-08': '18', 'RM-09': '10', 'RM-10': '10', 'RM-11': '2', 'PK-01': '10'}
- B3 new request: {'LP-02': '1', 'MN-01': '1', 'DK-01': '1', 'HS-01': '1', 'KB-01': '1', 'SW-01': '1'}
- checks: 109
- failed: 0
- note: The scope probe (MO-2's request rejected before the increase) is this eval's own case for spec 4.2's 'store check for the changed item-locations only; all open lines at those places'.

### E64 Changes after issue: a store case for what was issued too many, issued lines reopen when the need goes up

Checks: Task 6.3, Q21, OP-14, OP-15 (block 1 spec 4.2, 4.4, 4.5): a cut below what was issued closes the line as issued with the new need and raises one store case (owner the first store person, escalating to their manager, due in working days) for issued minus need, never a negative reservation; a cancel after part issue keeps the issued quantity, cancels the material request and raises a store case; a request line cut exactly to its cover is cancelled and the reservation untouched; an increase after issue reopens the line with need minus issued short

- mutations caught: ['no store case for issued too many', 'an issued line not reopened when the need goes up']
- edges at the issued quantity: 30 (equal), 31 (one above), 29 (one below)
- checks: 206
- failed: 0
- note: Spec gap: a change up to exactly the issued quantity on an issued line ('goes back to open' with nothing short, or stays issued) is not decided; at 30 the eval checks only no case, nothing set aside and nothing bought.
- note: Spec gap: a store case's document number (the material request number is assumed by its link '#/material-requests') and its title on a cancel are not given; the cut's title is checked against the spec's example.

### E65 A moved start date cancels the old request and makes a new one that takes the released stock first

Checks: Task 6.3, OP-11, DF-19 (block 1 spec 3.1 rule 8, 4.1-4.3, 5.3, 6.4 B4): a joiner row cancelled and re-sent with a new start date cancels the old material request and cuts its open purchase request (emptied: cancelled); the new row's lines get a new material request (listed in the import's report), which takes the released stock at once, earliest need-by first; the next store check buys what is still short by the new date; a folder import and file-by-file uploads end the same; a second import changes nothing; tenant isolation

- mutations caught: ['new lines for a cancelled material request skipped (DF-19)', 'the moved row loaded as extra demand without the cancel']
- checks: 57
- failed: 0
- note: Spec gap: whether the CLI's 'loaded N rows' counts a row that only cancels (and the new row that replaces it) is not said; the new row is accepted as loaded 0 or 1 there, the changed and left-out counts exactly.
- note: Not exercised: new lines for an issued material request (the same DF-19 rule; no spec scenario issues a whole request before a new line arrives).

### E66 A later masters import keeps second-person controls: status, bank, policies, versions, history, nothing deleted

Checks: Task 0.6, DF-16, D-22, rule 8 (block 0c spec 3.1-3.5): the first import loads everything; any later import matches rows by key, never deletes, never changes a supplier's status or an existing supplier's bank details, adds new suppliers as drafts with unverified accounts, leaves out possible duplicates, never applies policies, approval rules or opening stock, adds price versions and contracts as drafts, keeps approvers; every added or changed row audited; a dry run writes nothing; a repeat changes nothing; tenant isolation. Decisions of 22 Nov: the admin API makes no Admin login and gives no role or limit without a second administrator

- first import: audit rows / mode: 1 / ['first']
- first import: tables whose rows differ from the file: none
- order bound to a contract price (po, price_record id): PO-CKN-2627-00001, 1
- rows the company holds differently from the file, untouched by the scenario: {'supplier_document': 1}
- dry run: exit code / first line: 1 / update by key (dry run, nothing written): would add 6, would change 4, unchanged 554, not in the file 13 (kept), in 0.17 s; 4 left out, 14 warnings
- import: exit code / first line: 1 / update by key: added 6, changed 4, unchanged 554, not in the file 13 (kept), in 0.22 s; 4 left out, 14 warnings
- oracle delta: added / changed / removed: {'supplier': 1, 'supplier_address': 1, 'supplier_bank_account': 1, 'supplier_contact': 1, 'contract': 1, 'price_record': 1} / {'department': 1, 'item': 1, 'supplier': 1, 'supplier_contact': 1} / none
- warnings expected / printed / missing: 13 / 14 / 0
- controls after the import (spec 3.5): kept
- audit: imports / row_add / row_change: 1 / 6 / 4
- the same copy again: first line: update by key: added 0, changed 0, unchanged 564, not in the file 13 (kept), in 0.16 s; 4 left out, 14 warnings
- admin API (decisions of 22 Nov): {'Admin login': 'refused (403)', 'role': 'proposed; maker approves 403, other admin 200; 0 -> 1', 'limit': 'proposed; maker approves 403, other admin 200; None -> 50000.00', 'own role': '403', 'own limit': '403'}
- mutation checks (rule broken on purpose: caught?): {'the import applies supplier.status from the file': 'caught in the database', "a new supplier's bank account keeps verified=true": 'caught in the database', 'the later import deletes and re-inserts': 'caught in the database'}
- note: rows the company held differently from the file before the import: {'supplier_document': [('SUP-002', 'cancelled_cheque', 'CHQ-0002')]}

### E67 The app runs without owner rights: its own login, insert-only audit by grant, nothing it could switch off

Checks: Task 1.7, D-25, DF-37 (block 0c spec 4.1-4.5, 5): every runtime path (API, worker, CLI, engines) signs in as the app login, which is no superuser, makes no database or role, bypasses no row security and owns nothing; it may select, insert, update and delete business tables, only select and insert audit_event and setup_change, only select alembic_version and the control tenant tables; every forbidden statement fails with 42501 and changes nothing; stored tenant URLs hold no password; health says restricted; the owner still migrates, and tables it makes later reach the app by default privileges

- app login / control database: p2p_eval_m2_control_app / p2p_eval_m2_control
- flow: request / approved by / jobs taken / orders: REQ-CKN-2627-00001 / ['planthead 200'] / 7 / ['PO-CKN-2627-00001']
- jobs: total / refused by the database / failed otherwise: 14 / 0 / 0
- runtime engines sign in as: {'tenants.control_engine()': 'p2p_eval_m2_control_app', 'routing.engine_for(slug)': 'p2p_eval_m2_control_app', 'tenants.tenant_engine(slug)': 'p2p_eval_m2_control_app'}
- /api/health: {'status': 'ok', 'database': 'ok', 'database_login': 'restricted'}
- as the app: refused statements (42501 with the spec's text): 10 of 10 (company), 2 of 2 (control)
- as the app: tables checked for the spec's grants: 149
- stored company connections / with a password or user: 6 / 0
- tenant migrate-all as the owner: exit code: 0
- a table the owner makes after migrating: the app may: ['DELETE', 'INSERT', 'SELECT', 'UPDATE']
- no APP_DATABASE_PASSWORD: MissingAppLogin: APP_DATABASE_PASSWORD is not set: the app signs in to the database with its own restricted login. Add it to .env (see .env.example), then run p
- modules using the owner's login outside tenants, demo and cli: none
- mutation checks (rule broken on purpose: caught?): {'the runtime engines use the owner URL': 'caught (166 findings)', 'the grant on audit_event includes update or delete': 'caught (1 findings)', 'default privileges missing': 'caught (1 findings)'}

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
