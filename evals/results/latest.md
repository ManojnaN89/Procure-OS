# Evals report

Run on 2026-11-10 18:46; AI evals used a scripted model.

| ID | What it checks | Result | Seconds | Key figures |
|---|---|---|---|---|
| E01 | Replenishment orders the right items in the right quantities | **PASS** | 2.8 | lines expected 95; lines planned 95; missing 0; extra 0 |
| E02 | Approval routing follows the rule table and the organisation | **PASS** | 14.9 | cases 2334; rule agreement % 100.0; cases with no rule 474; chain cases 1860 |
| E03 | Sourcing picks the right contract price, or sends the line to quotation | **PASS** | 7.4 | items 137; cases 3288; decided on a contract 984; sent to quotation 2304 |
| E04 | GST and order arithmetic is exact, to the paisa | **PASS** | 0.2 | random lines 5000; net price errors 0; line errors 0; identity errors 0 |
| E05 | Quote comparison recommends the right supplier and the award rules hold | **PASS** | 18.0 | scenarios 160; lines compared 323; recommendation correct % 100.0; compliance flags correct % 100.0 |
| E06 | The whole flow, stock level to approved purchase order, keeps its promises | **PASS** | 11.9 | requests 14; lines 95; lines ordered 94; RFQs 9 |
| E07 | Repeating or racing any stage makes no duplicates | **PASS** | 0.5 | serial repeats 3; concurrent callers 6; seconds for the race 0.2; tables that grew none |
| E08 | Decisions stay reproducible when the rules change | **PASS** | 0.2 | decisions 14; band holds before / after the change 14/0 and 14/0; old rule rows untouched True; past date reproduces the old version % 100.0 |
| E09 | Every write action is open to exactly the roles the plan names | **PASS** | 13.9 | actions 40; roles tried 8; cells checked 320; too permissive 0 |
| E10 | One company cannot see another company's data | **PASS** | 7.8 | probes 73; leaks 0; company A sees its own records True |
| E11 | Hostile text is shown as text and never run | **PASS** | 47.2 | screens opened 18; hostile payloads 6; script ran or markup injected 0; SQL probes 12 |
| E12 | No floats in money, exact amounts on the wire, India's calendar, no secrets, synthetic data stays synthetic | **PASS** | 1.4 | money modules scanned 6; float uses 0; money fields as JSON numbers 0; UTC dates in business logic 0 |
| E13 | Supplier emails are read into the right quotes, and when unsure the system asks a person | **PASS** | 11.7 | email cases 39; perfect reader: exact % 100.0; perfect reader: silent errors 0; sloppy reader: mistakes that did not slip through {'invented price': '33/33 not clean-but-wrong', 'invented gst': '33/33 not clean-but-wrong', 'invented delivery': '33/33 not clean-but-wrong', 'swapped prices': '16/16 not clean-but-wrong'} |
| E14 | Hostile emails, even obeyed by the model, change nothing they should not | **PASS** | 5.5 | attacks 13; runs 26; defended 26; failed 0 |
| E15 | The assistant tells the truth from the data, adds nothing up on its own, and changes nothing | **SKIP** | 0.0 |  |
| E16 | Documents, budget, contract ceilings and reminders do what the plan says | **PASS** | 7.6 | documents: suppliers checked 23; documents: spoiled cases 23 (23 right); documents: bad suppliers asked to quote 0; budget: cases (over / within) 80 (36 / 44) |
| E17 | Orders are sent to the right supplier with the right amounts, only when approved, and acknowledged on time | **PASS** | 19.2 | orders by status before sending {'approved': 28}; orders sent 28; acknowledged 25; late promises flagged 6 |
| E18 | Goods receipt: tolerance, rejections, inspection, stock and order progress are right | **PASS** | 24.8 | order lines to receive 94; scenarios {'full': 17, 'over_limit': 15, 'partial_then_rest': 17, 'reject': 16, 'over_refused': 12, 'inspect': 17}; stock positions changed 94; receipts posted 111 |
| E19 | Invoices are matched to orders and receipts the way the dataset's answers say | **PASS** | 21.3 | invoices entered 192; judged against the dataset 186; verdict agrees % 100.0; copies expected / found / false alarms 2 / 2 / 0 |
| E20 | Due dates and tax at source are worked out the way the dataset's answers say | **PASS** | 23.8 | invoices booked 170; invoices left out (advance paid on the order) 0; due dates agree % 100.0; tax amounts agree % 100.0 |
| E21 | Payments are for the right amounts, in the right order, once, and only for invoices that may be paid | **PASS** | 24.9 | payments compared 71; amounts agree % 100.0; interest agrees % (small suppliers) 100.0; invoices in the run 170 |
| E22 | Company setup is admin-only, logged, validated, and its locks cannot be undone | **PASS** | 12.5 | permission cells checked 112; permission cells wrong 0; policy values tried 126; policy problems (accepted bad / refused good / badly logged) 0 / 0 / 0 |
| E23 | Vendor scores follow how suppliers really behave, and planted repeat requests are caught | **PASS** | 12.7 | suppliers scored 23; rank agreement: delivery score vs true on-time rate -0.04; rank agreement: quality score vs true acceptance rate 0.56; mean score by hidden profile {'average': 77.2, 'poor': 72.3, 'reliable': 77.9} |
| E24 | Plain-words requests become the right drafts or one right question, and never a wrong draft | **PASS** | 22.8 | cases 72; careful reader: right action % 100.0; careful reader: lines mapped to the right item and quantity % 100.0; careful reader: wrong drafts 0 |
| E25 | Blocked invoices are explained with the right cause, and a block is never changed by the agent | **PASS** | 37.6 | invoices entered 192; blocked by the match 21; of those, with a cause the dataset planted 21; blocked without a planted cause (part-delivery carry-over, see E19) 0 |
| E26 | Advances, early-payment discounts and the closing notice are right | **PASS** | 69.8 | advance orders replayed 32; invoices replayed 35; advance payments agree % 100.0; adjustments on invoices the dataset had not yet paid 1 |
| E27 | Invoices are read from documents correctly, e-invoices without a model, and a wrong reading never looks clean | **PASS** | 30.4 | invoices: e-invoices / plain / sloppy-reader 12 / 18 / 12; e-invoices read with a model call 0; fields right % (e-invoices + plain, all fields) 100.0; fields right % (e-invoices only) 100.0 |
| E28 | Supplier emails are classified, small changes applied, the rest handed to a person, nothing wrong applied | **PASS** | 55.4 | orders sent and acknowledged / RFQs to answer 28 / 27; replies 84; careful reader: handled the right way % 100.0; careful reader: wrong changes / unsafe effects / spoofed mail read 0 / 0 / 0 |
| E29 | The control tower, the key figures and the alerts agree with an independent recount | **PASS** | 35.7 | control tower: cases by stage {'request_approval': 7, 'sourcing': 12, 'rfq_out': 6, 'award': 18, 'po_approval': 5, 'po_sent': 9, 'awaiting_receipt': 35, 'awaiting_invoice': 10, 'invoice_blocked': 21, 'awaiting_payment': 98}; key figures {'spend under management % / on contract %': ('100.0', '63.6'), 'savings': {'rfqs': 91, 'total': '146152.73', 'per_rfq': '1606.07', 'rfqs_with_saving': 52}, 'on-time delivery (lines, %)': (190, '65.8'), 'invoices matched first time (n, %)': (186, '88.7'), "  … by the dataset's answer key %": '88.7', 'small suppliers paid on time (n, %)': (55, '96.4'), 'touchless (requests, %)': (174, '97.7'), 'cycle time, request to paid, working days': {'cases': 37, 'median': '35.0', 'p90': '50.0'}, 'paid invoices set up from the dataset': 70}; first-pass gap to the dataset's answer key, points 0.0; alerts raised, by rule {'contract_expiring': 2, 'invoice_blocked': 21, 'invoice_due': 22, 'late_delivery': 32, 'po_not_acknowledged': 9, 'rfq_no_quotes': 3, 'stale_requisition': 4} |

**28 of 29 passed.**

## Details

### E01 Replenishment orders the right items in the right quantities

Checks: Plan: read stock levels, create requests for items at their reorder point (quantity from the planning data, whole packs, supplier minimum)

- lines expected: 95
- lines planned: 95
- missing: 0
- extra: 0
- quantity exact %: 100.0
- value exact %: 100.0
- grouping exact %: 100.0
- requests: 14
- total value: 4941030.22

### E02 Approval routing follows the rule table and the organisation

Checks: Rule 8 and the plan: a rule is chosen by value band, spend type, category, department and the date it is in force; its chain becomes real people

- cases: 2334
- rule agreement %: 100.0
- cases with no rule: 474
- chain cases: 1860
- chain agreement %: 100.0

### E03 Sourcing picks the right contract price, or sends the line to quotation

Checks: Plan: contract vs RFQ per line; only an active contract in force, for an active supplier, in the right unit, with the quantity break

- items: 137
- cases: 3288
- decided on a contract: 984
- sent to quotation: 2304
- agreement %: 100.0

### E04 GST and order arithmetic is exact, to the paisa

Checks: Rule 1: money is Decimal to paise; GST splits into CGST+SGST within the state and IGST across states; composition and unregistered suppliers charge none

- random lines: 5000
- net price errors: 0
- line errors: 0
- identity errors: 0
- total errors: 0

### E05 Quote comparison recommends the right supplier and the award rules hold

Checks: Plan: collect quotes, compare, award; the recommendation is the lowest compliant offer; an override or too few quotes needs a reason

- scenarios: 160
- lines compared: 323
- recommendation correct %: 100.0
- compliance flags correct %: 100.0
- offer order correct %: 100.0
- award rule correct %: 100.0

### E06 The whole flow, stock level to approved purchase order, keeps its promises

Checks: Plan: nothing is lost, money reconciles, every change is audited, only eligible suppliers are asked, stock on the way is counted once

- requests: 14
- lines: 95
- lines ordered: 94
- RFQs: 9
- quotes: 24
- purchase orders: 28
- orders approved: 28
- line fates: {'ordered': 94, 'quotation open': 1}
- stage seconds: {'replenishment and submission': 1.27, 'request approvals': 0.7, 'sourcing and RFQ emails': 2.2, 'quotes and awards': 0.97, 'purchase orders and their approval': 3.91}
- total seconds: 9.1
- company build seconds: 2.59
- orders lines checked: 94

### E07 Repeating or racing any stage makes no duplicates

Checks: Plan: no duplicate requests, RFQs, emails or orders; document numbers have no gaps and no repeats

- serial repeats: 3
- concurrent callers: 6
- seconds for the race: 0.2
- tables that grew: none
- extra emails: 0

### E08 Decisions stay reproducible when the rules change

Checks: Rule 8: rules and rates are versioned data; a decision stays reproducible against the version it used

- decisions: 14
- band holds before / after the change: 14/0 and 14/0
- old rule rows untouched: True
- past date reproduces the old version %: 100.0
- a new submission uses: ('REQ-02', 3)
- approval audit rows without a rule version: 0

### E09 Every write action is open to exactly the roles the plan names

Checks: Rule 7 and the plan: requester, buyer, approver and administrator duties are separate; nobody else can do them; nobody can without logging in

- actions: 40
- roles tried: 8
- cells checked: 320
- too permissive: 0
- too strict: 0
- reads open without login: 0

### E10 One company cannot see another company's data

Checks: Rule 6: one database per tenant; a request without tenant context is refused; tenant A cannot read tenant B

- probes: 73
- leaks: 0
- company A sees its own records: True

### E11 Hostile text is shown as text and never run

Checks: Security: names from imported data and typed identifiers cannot inject script into the screens or SQL into the database

- screens opened: 18
- hostile payloads: 6
- script ran or markup injected: 0
- SQL probes: 12
- server errors: 0
- slow (sleep ran): 0
- orders on screen: 28
- pipeline note: 0

### E12 No floats in money, exact amounts on the wire, India's calendar, no secrets, synthetic data stays synthetic

Checks: Rules 1, 9, 10 and the generated-data safety rule

- money modules scanned: 6
- float uses: 0
- money fields as JSON numbers: 0
- UTC dates in business logic: 0
- tracked files scanned for secrets: 554
- secret hits: 0
- unsafe generated values: 0
- note: money fields sent as numbers: []

### E13 Supplier emails are read into the right quotes, and when unsure the system asks a person

Checks: Plan step 8 and rule 2: the model reads, code checks; a wrong reading must never look clean (silent errors = 0)

- email cases: 39
- perfect reader: exact %: 100.0
- perfect reader: silent errors: 0
- sloppy reader: mistakes that did not slip through: {'invented price': '33/33 not clean-but-wrong', 'invented gst': '33/33 not clean-but-wrong', 'invented delivery': '33/33 not clean-but-wrong', 'swapped prices': '16/16 not clean-but-wrong'}
- note: the real models were not called (run with --live)

### E14 Hostile emails, even obeyed by the model, change nothing they should not

Checks: Security and rule 5: untrusted text cannot award, decline others, set a nonsense price, or reach a model from a stranger

- attacks: 13
- runs: 26
- defended: 26
- failed: 0
- note: the real models were not called (run with --live)

### E15 The assistant tells the truth from the data, adds nothing up on its own, and changes nothing

Checks: Plan step 8 and rule 2: read-only tools; every figure it gives comes from a tool; it cannot approve or delete

- note: skipped: this eval needs the real models (run with --live)

### E16 Documents, budget, contract ceilings and reminders do what the plan says

Checks: Plan, phase 1 controls: a supplier needs good documents, a request must fit its budget, a contract has a ceiling, and a silent supplier is reminded a limited number of times

- documents: suppliers checked: 23
- documents: spoiled cases: 23 (23 right)
- documents: bad suppliers asked to quote: 0
- budget: cases (over / within): 80 (36 / 44)
- budget: disagreements: 0
- ceilings: random cases: 3000
- ceilings: disagreements: 0
- ceilings: contract orders replayed: 15
- ceilings: usage figures that differ from the replay: 0
- reminders: expected days: Tue 10 Nov, Thu 12 Nov
- reminders: silent suppliers on schedule: 2 of 2
- reminders: reminded though replied or declined: 0

### E17 Orders are sent to the right supplier with the right amounts, only when approved, and acknowledged on time

Checks: Plan: an approved order goes to the supplier by email; nothing else does; the supplier's promised date is checked against the date the goods are needed; an order with no acknowledgement in two working days is overdue

- orders by status before sending: {'approved': 28}
- orders sent: 28
- acknowledged: 25
- late promises flagged: 6
- overdue day-checks: 48

### E18 Goods receipt: tolerance, rejections, inspection, stock and order progress are right

Checks: Plan: goods are received against a sent order; over-delivery is accepted up to the category's tolerance; rejected goods stay out of stock; items needing inspection wait; the order line and the stock follow what was accepted

- order lines to receive: 94
- scenarios: {'full': 17, 'over_limit': 15, 'partial_then_rest': 17, 'reject': 16, 'over_refused': 12, 'inspect': 17}
- stock positions changed: 94
- receipts posted: 111

### E19 Invoices are matched to orders and receipts the way the dataset's answers say

Checks: Plan P1-16/17: three-way match per line (quantity against accepted receipt, price within tolerance), tax and identity checks, duplicates caught, credit notes taken in; nothing is paid that the match blocks

- invoices entered: 192
- judged against the dataset: 186
- verdict agrees %: 100.0
- copies expected / found / false alarms: 2 / 2 / 0
- credit notes taken in: 4
- intake errors: 0
- by expected -> got: {'matched -> matched': 164, 'blocked qty_over_received -> blocked qty_over_received': 12, 'blocked price_variance -> blocked price_variance': 4, 'blocked irn_missing -> blocked irn_missing': 3, 'within_tolerance -> within_tolerance': 1, 'blocked tax_not_allowed -> blocked tax_not_allowed': 1, 'blocked gstin_mismatch -> blocked gstin_mismatch': 1}
- differences explained by part deliveries: 0
- verdict agrees % (part-delivery differences aside): 100.0
- order lines invoiced beyond what was received: 0
- note: differences: 

### E20 Due dates and tax at source are worked out the way the dataset's answers say

Checks: Plan P1-19 (FR23, FR26): due date from the payment terms with the 45-day rule for micro and small suppliers; tax on goods (194Q) at booking on the part of a supplier's year above 50 lakh

- invoices booked: 170
- invoices left out (advance paid on the order): 0
- due dates agree %: 100.0
- tax amounts agree %: 100.0
- suppliers whose year crosses 50 lakh: 0
- note: due differences: 
- note: tax differences: 

### E21 Payments are for the right amounts, in the right order, once, and only for invoices that may be paid

Checks: Plan P1-19/20: due date and tax at booking, credit notes netted, MSME first, MSME interest, the run approved by a person, the file equal to the approved run, invoices paid once, the case closed on payment

- payments compared: 71
- amounts agree %: 100.0
- interest agrees % (small suppliers): 100.0
- invoices in the run: 170
- payments in the run: 17
- orders closed by the payment: 132
- run total: 10622770.94
- note: differences: 

### E22 Company setup is admin-only, logged, validated, and its locks cannot be undone

Checks: Plan P1-24 and rule 5/8: who approves, tolerances and automation are data an Admin changes with a reason; payment runs, vendor activation and releasing a payment block are never automatic; past decisions stay reproducible

- permission cells checked: 112
- permission cells wrong: 0
- policy values tried: 126
- policy problems (accepted bad / refused good / badly logged): 0 / 0 / 0
- policies the code never reads: 0
- tolerance problems: 0
- locked decisions that could be opened: 0
- automation gate problems: 0
- Admin or other roles given what they must not have: 0
- rule versions created / chain problems: 6 / 0
- history replay works: True
- self-lockout refused: True

### E23 Vendor scores follow how suppliers really behave, and planted repeat requests are caught

Checks: Plan P1-05 and the vendor choice: a supplier's score reflects its delivery, quality and price; the best-vendor mode is no worse than the cheapest; a request that repeats another is flagged and an ordinary one is not

- suppliers scored: 23
- rank agreement: delivery score vs true on-time rate: -0.04
- rank agreement: quality score vs true acceptance rate: 0.56
- mean score by hidden profile: {'average': 77.2, 'poor': 72.3, 'reliable': 77.9}
- receipt lines judged per supplier (on average): 9.2
- scores that differ from the independent recomputation: 0
- items with two or more suppliers priced: 6
- picks that differ from the cheapest: 2
- true on-time rate of the picks vs of the cheapest: 0.930 vs 0.887
- planted repeat requests / caught: 2 / 2
- other requests flagged: 3 of 199
- requests flagged in all: 5
- note: too little history for rank agreement to mean much: only the order of the profile means is required

### E24 Plain-words requests become the right drafts or one right question, and never a wrong draft

Checks: Plan P1-08 (AG01): at least 90% of lines mapped to the right item and quantity; every value traces to the message; an instruction hidden in a message is ignored; only drafts are made

- cases: 72
- careful reader: right action %: 100.0
- careful reader: lines mapped to the right item and quantity %: 100.0
- careful reader: wrong drafts: 0
- sloppy readers (invented quantity / item / date): wrong drafts: {'qty': 0, 'item': 0, 'date': 0}
- hidden-instruction messages / requests ever left past draft: 3 / 0
- note: careful reader: action differs from the template: 
- note: the real models were not called (run with --live)

### E25 Blocked invoices are explained with the right cause, and a block is never changed by the agent

Checks: Plan P1-18 (AG03): at least 80% of mismatch cases diagnosed correctly (proposed target); the payment block stays until a person releases it; numbers in the answer come from the data

- invoices entered: 192
- blocked by the match: 21
- of those, with a cause the dataset planted: 21
- blocked without a planted cause (part-delivery carry-over, see E19): 0
- rules: cause diagnosed right %: 100.0
- rules: fixes proposed: {'corrected_invoice': 17, 'credit_note': 4}
- rules: queries to the supplier that name our reference %: 100.0
- investigator, careful: wording used / cause right: 21 / 21 of 21
- investigator naming a wrong cause: wording used / cause right: 0 / 21 of 21
- investigator adding a made-up number: wording used / cause right: 0 / 21 of 21
- tables changed by looking into invoices: none
- note: the real models were not called (run with --live)

### E26 Advances, early-payment discounts and the closing notice are right

Checks: Plan P1-19/20: 30% advance on an order and its adjustment against invoices (tax at source on the advance), a cash discount for paying inside the window, the requester told when the order is paid and closed

- advance orders replayed: 32
- invoices replayed: 35
- advance payments agree %: 100.0
- adjustments on invoices the dataset had not yet paid: 1
- adjustments agree %: 100.0
- adjustments compared: 30
- adjustments left out (invoice not approved): 0
- invoice payments compared: 32
- invoice payments agree %: 100.0
- invoices in the run that had an advance taken off: 31
- advances paid: 32
- adjustments made: 31
- supplier-years checked (limit lowered): 12
- of which cross the limit: 7
- invoices with discount terms: 103
- discount checks: 1020
- of which on or just after a window's last day: 206
- discount agrees %: 100.0
- run date: 2026-10-08
- invoices pulled into the run only by their discount: 21
- early-payment discount earned: 33424.95
- orders closed by the run: 30
- closing notices: 30

### E27 Invoices are read from documents correctly, e-invoices without a model, and a wrong reading never looks clean

Checks: Plan P1-16 (second half): >=95% of fields right on 30 invoices; QR invoices read with 0 model calls; silent errors 0; strangers' mail never reaches a model; automatic entry only when nothing is in doubt

- invoices: e-invoices / plain / sloppy-reader: 12 / 18 / 12
- e-invoices read with a model call: 0
- fields right % (e-invoices + plain, all fields): 100.0
- fields right % (e-invoices only): 100.0
- fields checked: 276
- sloppy-model mistakes tried: 12
-   caught or harmless %: 100.0
-   by kind: {'invented qty': '2/2', 'invented total': '2/2', 'swapped prices': '2/2', 'dropped line': '2/2', 'date not on the page': '2/2', 'invented number': '2/2'}
- silent errors: 0
- documents that must be refused / refused with no model call: 4 / 4
- page with orders for the model: draft status: needs_person
- model calls on those documents: 2
- the same file twice -> one draft: True
- gate on: e-invoices entered by themselves / entered wrongly: 4 / 0
- gate on: invoices entered from a doubtful reading: 0
- audit rows for drafts: 58

### E28 Supplier emails are classified, small changes applied, the rest handed to a person, nothing wrong applied

Checks: Plan P1-14 (AG02): at least 90% of replies handled the right way; a change within the tolerance is applied, outside it goes to a buyer; a price change, a substitution or a bank change never changes anything; mail from outside the supplier is never read by a model; part deliveries and unacknowledged orders are chased

- orders sent and acknowledged / RFQs to answer: 28 / 27
- replies: 84
- careful reader: handled the right way %: 100.0
- careful reader: wrong changes / unsafe effects / spoofed mail read: 0 / 0 / 0
- replies by type: {'ack': 13, 'bank_change': 3, 'date_change': 9, 'decline_rfq': 4, 'delay': 4, 'extension_request': 2, 'invoice_cover': 4, 'other': 2, 'partial_dispatch': 4, 'payment_followup': 4, 'price_change': 7, 'qty_change': 6, 'question': 8, 'quote': 8, 'reject': 4, 'substitution': 2}
- date changes: applied in tolerance / handed to a buyer: 6 / 11
- sloppy readers (invented date / invented quantity / unsure): wrong changes: {'date': 0, 'qty': 0, 'unsure': 0}
- gate off: emails that became tasks / orders changed: 10 of 10 / 0
- chasers emailed / turned into tasks when the gate is off: 18 / 18
- mutation checks (rule broken on purpose: caught?): {'a date of any size is accepted': 'caught', 'a quantity of any size is accepted': 'caught', 'anyone counts as the supplier': 'caught', 'a price change moves the promised date': 'caught', 'the autonomy gate is ignored': 'caught'}
- note: the real models were not called (run with --live)

### E29 The control tower, the key figures and the alerts agree with an independent recount

Checks: Plan P1-21/P1-23: every role sees what needs it and nothing else; the control tower shows every case by stage, location and age in working days; the key figures reconcile with the raw data; alerts are raised once, routed by role, and resolved when their cause goes away

- control tower: cases by stage: {'request_approval': 7, 'sourcing': 12, 'rfq_out': 6, 'award': 18, 'po_approval': 5, 'po_sent': 9, 'awaiting_receipt': 35, 'awaiting_invoice': 10, 'invoice_blocked': 21, 'awaiting_payment': 98}
- key figures: {'spend under management % / on contract %': ('100.0', '63.6'), 'savings': {'rfqs': 91, 'total': '146152.73', 'per_rfq': '1606.07', 'rfqs_with_saving': 52}, 'on-time delivery (lines, %)': (190, '65.8'), 'invoices matched first time (n, %)': (186, '88.7'), "  … by the dataset's answer key %": '88.7', 'small suppliers paid on time (n, %)': (55, '96.4'), 'touchless (requests, %)': (174, '97.7'), 'cycle time, request to paid, working days': {'cases': 37, 'median': '35.0', 'p90': '50.0'}, 'paid invoices set up from the dataset': 70}
- first-pass gap to the dataset's answer key, points: 0.0
- alerts raised, by rule: {'contract_expiring': 2, 'invoice_blocked': 21, 'invoice_due': 22, 'late_delivery': 32, 'po_not_acknowledged': 9, 'rfq_no_quotes': 3, 'stale_requisition': 4}
- role homes: sections shown: {'Admin': [], 'Requester': [], 'Buyer': ['rfq_award', 'rfq_wait', 'to_acknowledge', 'to_source'], 'Approver': [], 'Store': ['to_receive'], 'AP': ['blocked', 'to_invoice'], 'Finance': ['due'], 'kpi_keys': ['cycle_time', 'first_pass_match', 'msme_on_time_payment', 'on_time_delivery', 'savings', 'spend_under_management', 'touchless']}
- formula checks against brute force: 1030
- alert rules compared with the recount: ['stale_requisition: 4', 'rfq_no_quotes: 3', 'invoice_blocked: 21', 'late_delivery: 32', 'invoice_due: 22', 'contract_expiring: 2']
