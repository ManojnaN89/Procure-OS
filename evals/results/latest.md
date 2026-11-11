# Evals report

Run on 2026-11-11 12:40; AI evals used a scripted model.

| ID | What it checks | Result | Seconds | Key figures |
|---|---|---|---|---|
| E01 | Replenishment orders the right items in the right quantities | **PASS** | 5.2 | lines expected 95; lines planned 95; missing 0; extra 0 |
| E02 | Approval routing follows the rule table and the organisation | **PASS** | 22.8 | cases 2334; rule agreement % 100.0; cases with no rule 474; chain cases 1860 |
| E03 | Sourcing picks the right contract price, or sends the line to quotation | **PASS** | 11.5 | items 137; cases 3288; decided on a contract 984; sent to quotation 2304 |
| E04 | GST and order arithmetic is exact, to the paisa | **PASS** | 0.4 | random lines 5000; net price errors 0; line errors 0; identity errors 0 |
| E05 | Quote comparison recommends the right supplier and the award rules hold | **PASS** | 30.8 | scenarios 160; lines compared 323; recommendation correct % 100.0; compliance flags correct % 100.0 |
| E06 | The whole flow, stock level to approved purchase order, keeps its promises | **PASS** | 21.2 | requests 14; lines 95; lines ordered 94; RFQs 9 |
| E07 | Repeating or racing any stage makes no duplicates | **PASS** | 0.5 | serial repeats 3; concurrent callers 6; seconds for the race 0.2; tables that grew none |
| E08 | Decisions stay reproducible when the rules change | **PASS** | 0.3 | decisions 14; band holds before / after the change 14/0 and 14/0; old rule rows untouched True; past date reproduces the old version % 100.0 |
| E09 | Every write action is open to exactly the roles the plan names | **PASS** | 26.2 | actions 52; roles tried 8; cells checked 416; too permissive 0 |
| E10 | One company cannot see another company's data | **PASS** | 11.2 | probes 73; leaks 0; company A sees its own records True |
| E11 | Hostile text is shown as text and never run | **PASS** | 62.0 | screens opened 18; hostile payloads 6; script ran or markup injected 0; SQL probes 12 |
| E12 | No floats in money, exact amounts on the wire, India's calendar, no secrets, synthetic data stays synthetic | **PASS** | 1.4 | money modules scanned 6; float uses 0; money fields as JSON numbers 0; UTC dates in business logic 0 |
| E13 | Supplier emails are read into the right quotes, and when unsure the system asks a person | **PASS** | 15.4 | email cases 39; perfect reader: exact % 100.0; perfect reader: silent errors 0; sloppy reader: mistakes that did not slip through {'invented price': '33/33 not clean-but-wrong', 'invented gst': '33/33 not clean-but-wrong', 'invented delivery': '33/33 not clean-but-wrong', 'swapped prices': '16/16 not clean-but-wrong'} |
| E14 | Hostile emails, even obeyed by the model, change nothing they should not | **PASS** | 6.8 | attacks 13; runs 26; defended 26; failed 0 |
| E15 | The assistant tells the truth from the data, adds nothing up on its own, and changes nothing | **SKIP** | 0.0 |  |
| E16 | Documents, budget, contract ceilings and reminders do what the plan says | **PASS** | 9.0 | documents: suppliers checked 23; documents: spoiled cases 23 (23 right); documents: bad suppliers asked to quote 0; budget: cases (over / within) 80 (36 / 44) |
| E17 | Orders are sent to the right supplier with the right amounts, only when approved, and acknowledged on time | **PASS** | 22.6 | orders by status before sending {'approved': 34}; orders sent 34; acknowledged 25; late promises flagged 6 |
| E18 | Goods receipt: tolerance, rejections, inspection, stock and order progress are right | **PASS** | 30.9 | order lines to receive 94; scenarios {'full': 18, 'over_limit': 16, 'partial_then_rest': 14, 'reject': 16, 'over_refused': 13, 'inspect': 17}; stock positions changed 94; receipts posted 108 |
| E19 | Invoices are matched to orders and receipts the way the dataset's answers say | **PASS** | 20.4 | invoices entered 192; judged against the dataset 186; verdict agrees % 100.0; copies expected / found / false alarms 2 / 2 / 0 |
| E20 | Due dates and tax at source are worked out the way the dataset's answers say | **PASS** | 20.4 | invoices booked 170; invoices left out (advance paid on the order) 0; due dates agree % 100.0; tax amounts agree % 100.0 |
| E21 | Payments are for the right amounts, in the right order, once, and only for invoices that may be paid | **PASS** | 25.7 | payments compared 71; amounts agree % 100.0; interest agrees % (small suppliers) 100.0; invoices in the run 170 |
| E22 | Company setup is admin-only, logged, validated, and its locks cannot be undone | **PASS** | 16.7 | permission cells checked 112; permission cells wrong 0; policy values tried 181; policy problems (accepted bad / refused good / badly logged) 0 / 0 / 0 |
| E23 | Vendor scores follow how suppliers really behave, and planted repeat requests are caught | **PASS** | 12.8 | suppliers scored 23; rank agreement: delivery score vs true on-time rate -0.04; rank agreement: quality score vs true acceptance rate 0.56; mean score by hidden profile {'average': 77.2, 'poor': 72.3, 'reliable': 77.9} |
| E24 | Plain-words requests become the right drafts or one right question, and never a wrong draft | **PASS** | 23.3 | cases 72; careful reader: right action % 100.0; careful reader: lines mapped to the right item and quantity % 100.0; careful reader: wrong drafts 0 |
| E25 | Blocked invoices are explained with the right cause, and a block is never changed by the agent | **PASS** | 42.4 | invoices entered 192; blocked by the match 21; of those, with a cause the dataset planted 21; blocked without a planted cause (part-delivery carry-over, see E19) 0 |
| E26 | Advances, early-payment discounts and the closing notice are right | **PASS** | 80.8 | advance orders replayed 32; invoices replayed 35; advance payments agree % 100.0; adjustments on invoices the dataset had not yet paid 1 |
| E27 | Invoices are read from documents correctly, e-invoices without a model, and a wrong reading never looks clean | **PASS** | 97.8 | invoices: e-invoices / plain / sloppy-reader 12 / 18 / 12; e-invoices read with a model call 0; fields right % (e-invoices + plain, all fields) 100.0; fields right % (e-invoices only) 100.0 |
| E28 | Supplier emails are classified, small changes applied, the rest handed to a person, nothing wrong applied | **PASS** | 62.8 | orders sent and acknowledged / RFQs to answer 34 / 27; replies 84; careful reader: handled the right way % 100.0; careful reader: wrong changes / unsafe effects / spoofed mail read 0 / 0 / 0 |
| E29 | The control tower, the key figures and the alerts agree with an independent recount | **PASS** | 37.5 | control tower: cases by stage {'request_approval': 7, 'sourcing': 12, 'rfq_out': 6, 'award': 18, 'po_approval': 5, 'po_sent': 9, 'awaiting_receipt': 35, 'awaiting_invoice': 10, 'invoice_blocked': 21, 'awaiting_payment': 98}; key figures {'spend under management % / on contract %': ('100.0', '63.6'), 'savings': {'rfqs': 91, 'total': '146152.73', 'per_rfq': '1606.07', 'rfqs_with_saving': 52}, 'on-time delivery (lines, %)': (190, '65.8'), 'invoices matched first time (n, %)': (186, '88.7'), "  … by the dataset's answer key %": '88.7', 'small suppliers paid on time (n, %)': (55, '96.4'), 'touchless (requests, %)': (174, '97.7'), 'duplicates stopped (requests, invoices)': {'requests': {'count': 0, 'value': '0'}, 'invoices': {'count': 2, 'value': '17481.00'}}, 'cycle time, request to paid, working days': {'cases': 37, 'median': '35.0', 'p90': '50.0'}, 'paid invoices set up from the dataset': 70}; first-pass gap to the dataset's answer key, points 0.0; alerts raised, by rule {'contract_expiring': 2, 'invoice_blocked': 21, 'invoice_due': 22, 'late_delivery': 32, 'po_not_acknowledged': 9, 'rfq_no_quotes': 3, 'stale_requisition': 4} |
| E30 | Demo runs through the real screens: a normal run, a part delivery and a price mismatch on the invoice, each typed in and with documents, and a held order | **PASS** | 228.4 | straight: steps / checks passed / failed 26 / 65 / 0; blocked: steps / checks passed / failed 22 / 67 / 0; held: steps / checks passed / failed 12 / 40 / 0; part: steps / checks passed / failed 31 / 84 / 0 |
| E31 | Synthetic invoices and replies read back correctly by the code readers, and what still needs a model is counted | **PASS** | 366.6 | documents: invoices / replies 192 / 322; looks low_contrast_pdf 19, multipage_pdf 29, photo_png 20, scan_pdf 29, skewed_jpg 19, text_pdf 76; dev / hold-out documents 353 / 161; hold-out layouts seen {'invoice': ['low_contrast_pdf', 'skewed_jpg', 'text_pdf'], 'quote_reply': ['body_table', 'body_text', 'decline', 'pdf_attachment']} |
| E32 | Hostile text, even obeyed by the model, changes nothing it must not; limits, secrets and dependencies hold | **PASS** | 69.4 | company B: invoices entered / blocked 120 / 13; agent tool lists as written down 7 + 6 (all read-only); quotation replies (attacks) 13; quotation replies (runs) 26 |
| E33 | The small demo company completes the whole flow, from low stock to a paid invoice, with separate people for each job | **PASS** | 18.0 | people / master rows loaded 8 / 261; items at or below reorder point 4; recommended by the comparison (bearing / seal) SUP-001 / SUP-003; micro supplier: due date (60-day terms, 45-day rule) 2026-12-21 |
| E34 | The whole application for a real user: the demo script from low stock to a paid invoice in a browser, every role guide followed as a new user, and every screen swept for errors, overflow and raw codes | **PASS** | 216.6 | people / master rows loaded 8 / 261; people per step (audit log) {'approved the requests': 1, 'approved the orders': 1, 'received the goods': 1, 'entered the invoices': 1, 'released the block': 1, 'planned the run': 1, 'approved the run': 1}; screens swept (role x screen x width) 204; steps taken at screens 76 |

**33 of 34 passed.**

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
- offer order correct %: 95.98
- award rule correct %: 100.0

### E06 The whole flow, stock level to approved purchase order, keeps its promises

Checks: Plan: nothing is lost, money reconciles, every change is audited, only eligible suppliers are asked, stock on the way is counted once

- requests: 14
- lines: 95
- lines ordered: 94
- RFQs: 9
- quotes: 24
- purchase orders: 34
- orders approved: 34
- line fates: {'ordered': 94, 'quotation open': 1}
- stage seconds: {'replenishment and submission': 2.3, 'request approvals': 0.8, 'sourcing and RFQ emails': 3.38, 'quotes and awards': 1.83, 'purchase orders and their approval': 7.06}
- total seconds: 15.4
- company build seconds: 5.61
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

- actions: 52
- roles tried: 8
- cells checked: 416
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
- orders on screen: 32
- pipeline note: 0

### E12 No floats in money, exact amounts on the wire, India's calendar, no secrets, synthetic data stays synthetic

Checks: Rules 1, 9, 10 and the generated-data safety rule

- money modules scanned: 6
- float uses: 0
- money fields as JSON numbers: 0
- UTC dates in business logic: 0
- tracked files scanned for secrets: 681
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

- orders by status before sending: {'approved': 34}
- orders sent: 34
- acknowledged: 25
- late promises flagged: 6
- overdue day-checks: 48

### E18 Goods receipt: tolerance, rejections, inspection, stock and order progress are right

Checks: Plan: goods are received against a sent order; over-delivery is accepted up to the category's tolerance; rejected goods stay out of stock; items needing inspection wait; the order line and the stock follow what was accepted

- order lines to receive: 94
- scenarios: {'full': 18, 'over_limit': 16, 'partial_then_rest': 14, 'reject': 16, 'over_refused': 13, 'inspect': 17}
- stock positions changed: 94
- receipts posted: 108

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
- policy values tried: 181
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

- orders sent and acknowledged / RFQs to answer: 34 / 27
- replies: 84
- careful reader: handled the right way %: 100.0
- careful reader: wrong changes / unsafe effects / spoofed mail read: 0 / 0 / 0
- replies by type: {'ack': 13, 'bank_change': 3, 'date_change': 9, 'decline_rfq': 4, 'delay': 4, 'extension_request': 2, 'invoice_cover': 4, 'other': 2, 'partial_dispatch': 4, 'payment_followup': 4, 'price_change': 7, 'qty_change': 6, 'question': 8, 'quote': 8, 'reject': 4, 'substitution': 2}
- date changes: applied in tolerance / handed to a buyer: 6 / 11
- sloppy readers (invented date / invented quantity / unsure): wrong changes: {'date': 0, 'qty': 0, 'unsure': 0}
- gate off: emails that became tasks / orders changed: 8 of 8 / 0
- chasers emailed / turned into tasks when the gate is off: 22 / 22
- mutation checks (rule broken on purpose: caught?): {'a date of any size is accepted': 'caught', 'a quantity of any size is accepted': 'caught', 'anyone counts as the supplier': 'caught', 'a price change moves the promised date': 'caught', 'the autonomy gate is ignored': 'caught'}
- note: the real models were not called (run with --live)

### E29 The control tower, the key figures and the alerts agree with an independent recount

Checks: Plan P1-21/P1-23: every role sees what needs it and nothing else; the control tower shows every case by stage, location and age in working days; the key figures reconcile with the raw data; alerts are raised once, routed by role, and resolved when their cause goes away

- control tower: cases by stage: {'request_approval': 7, 'sourcing': 12, 'rfq_out': 6, 'award': 18, 'po_approval': 5, 'po_sent': 9, 'awaiting_receipt': 35, 'awaiting_invoice': 10, 'invoice_blocked': 21, 'awaiting_payment': 98}
- key figures: {'spend under management % / on contract %': ('100.0', '63.6'), 'savings': {'rfqs': 91, 'total': '146152.73', 'per_rfq': '1606.07', 'rfqs_with_saving': 52}, 'on-time delivery (lines, %)': (190, '65.8'), 'invoices matched first time (n, %)': (186, '88.7'), "  … by the dataset's answer key %": '88.7', 'small suppliers paid on time (n, %)': (55, '96.4'), 'touchless (requests, %)': (174, '97.7'), 'duplicates stopped (requests, invoices)': {'requests': {'count': 0, 'value': '0'}, 'invoices': {'count': 2, 'value': '17481.00'}}, 'cycle time, request to paid, working days': {'cases': 37, 'median': '35.0', 'p90': '50.0'}, 'paid invoices set up from the dataset': 70}
- first-pass gap to the dataset's answer key, points: 0.0
- alerts raised, by rule: {'contract_expiring': 2, 'invoice_blocked': 21, 'invoice_due': 22, 'late_delivery': 32, 'po_not_acknowledged': 9, 'rfq_no_quotes': 3, 'stale_requisition': 4}
- role homes: sections shown: {'Admin': ['blocked', 'due', 'rfq_award', 'rfq_wait', 'to_acknowledge', 'to_invoice', 'to_receive', 'to_source'], 'Requester': [], 'Buyer': ['rfq_award', 'rfq_wait', 'to_acknowledge', 'to_source'], 'Approver': [], 'Store': ['to_receive'], 'AP': ['blocked', 'to_invoice'], 'Finance': ['due'], 'kpi_keys': ['cycle_time', 'duplicates_avoided', 'first_pass_match', 'msme_on_time_payment', 'on_time_delivery', 'savings', 'spend_under_management', 'touchless']}
- formula checks against brute force: 1030
- alert rules compared with the recount: ['stale_requisition: 4', 'rfq_no_quotes: 3', 'invoice_blocked: 21', 'late_delivery: 32', 'invoice_due: 22', 'contract_expiring: 2']

### E30 Demo runs through the real screens: a normal run, a part delivery and a price mismatch on the invoice, each typed in and with documents, and a held order

Checks: Plan P1-25: the flow from stock level to a paid and closed order works for real people at real screens: the right person sees the right work, every money figure equals an independent calculation from the dataset, a document (an emailed quotation, an uploaded invoice) leaves the same figures as typing them in, and the separation of duties holds at every step

- straight: steps / checks passed / failed: 26 / 65 / 0
- blocked: steps / checks passed / failed: 22 / 67 / 0
- held: steps / checks passed / failed: 12 / 40 / 0
- part: steps / checks passed / failed: 31 / 84 / 0
- straight_docs: steps / checks passed / failed: 29 / 71 / 0
- part_docs: steps / checks passed / failed: 35 / 93 / 0
- blocked_docs: steps / checks passed / failed: 23 / 71 / 0
- steps taken at screens: 178
- checks against the dataset: 491
- transcripts and screenshots: evals/results/demo_runs/

### E31 Synthetic invoices and replies read back correctly by the code readers, and what still needs a model is counted

Checks: Plan P1-03: documents generated from the dataset with an answer key; QR >=95% (100% on clean), rows by arithmetic >=95% on text-layer invoices, silent errors 0, perfect readings pass the evidence check, deterministic, nothing real

- documents: invoices / replies: 192 / 322
- looks: low_contrast_pdf 19, multipage_pdf 29, photo_png 20, scan_pdf 29, skewed_jpg 19, text_pdf 76
- dev / hold-out documents: 353 / 161
- hold-out layouts seen: {'invoice': ['low_contrast_pdf', 'skewed_jpg', 'text_pdf'], 'quote_reply': ['body_table', 'body_text', 'decline', 'pdf_attachment']}
- key differs from the CSV rows: 0
- files vs rows: invoices 192/192, replies 322/322
- QR read, all looks: 166/167 (99.4%)
- QR read, dev slice: 131/132 (99.24%)
- QR read, hold_out slice: 35/35 (100.0%)
- rows exact, text layer: 105/105 (100.0%)
- silent errors: 0
- e-invoice gate passes, text layer: 91/105
- perfect readings failing the evidence check: 0
- OCR sample: rows exact / gate passes / evidence passes: 6/8 / 6/8 / 6/8
- OCR sample: rows exact if a repeated row is ignored: 6/8
- need a model or a person: 45/192 (23.44%)
- replies routed by address / evidence passes: 322/322 / 322/322
- real-looking identifiers found: 0
- same seed, same bytes: yes
- note: QR low_contrast_pdf: 17/17
- note: QR multipage_pdf: 23/23
- note: QR photo_png: 16/16
- note: QR scan_pdf: 25/26
- note: QR skewed_jpg: 17/17
- note: QR text_pdf: 68/68
- note: needs a model or a person: 25 - no IRN, so no QR code (a model or a person reads it)
- note: needs a model or a person: 19 - scan/photo whose OCR text the arithmetic cannot read (estimated from the sample)
- note: needs a model or a person: 1 - QR code not readable
- note: read by code alone: 147/192 (text-layer gate passes 91, plus an estimate of 56 scans and photographs from the OCR sample)
- note: reply looks: body_table 99, body_text 103, decline 18, pdf_attachment 102
- note: reply quirks: incl_gst 32, partial 8, relative_validity 32, unit_differs 29

### E32 Hostile text, even obeyed by the model, changes nothing it must not; limits, secrets and dependencies hold

Checks: Plan P1-26: injected instructions cause no action; each agent has its own short tool list; secrets only in the environment; rate limits; no open high-severity dependency finding

- company B: invoices entered / blocked: 120 / 13
- agent tool lists as written down: 7 + 6 (all read-only)
- quotation replies (attacks): 13
- quotation replies (runs): 26
- quotation replies (defended): 26
- quotation replies (failed): 0
- failed logins / uploads / assistant questions allowed before 429: 4 / 3 / 3 (settings)
- files scanned for secrets / values looked for: 680 / 5 from .env
- dependency audit: 2 finding(s), 0 open
- hostile inputs run (a model that obeys each one): 25
- inputs that changed something they must not: 0
- mutations: defences broken / noticed: 6 / 6
- note: dependency audit partly or not run here (needs pip-audit and the network): pip-audit not found: the Python packages were not audited
- note: hostile inputs per path: assistant 4, invoice document 5, invoice explainer 4, quotation reply 1, request in plain words 4, supplier email 7
- note: 44 model calls were made by the obeying model across the runs

### E33 The small demo company completes the whole flow, from low stock to a paid invoice, with separate people for each job

Checks: Phase 1: request, approval, price or quotation, order with GST, receipt, invoice match, payment run, closing; requester, approver, receiver, invoice enterer and payer differ for the same case; Rs 10,000 approves itself, up to Rs 1,00,000 the maintenance manager, orders and payment runs the plant head

- people / master rows loaded: 8 / 261
- items at or below reorder point: 4
- recommended by the comparison (bearing / seal): SUP-001 / SUP-003
- micro supplier: due date (60-day terms, 45-day rule): 2026-12-21
- people per step (audit log): {'approved the requests': 1, 'approved the orders': 1, 'sent the orders': 1, 'received the goods': 1, 'entered the invoices': 1, 'planned the payment run': 1, 'approved the payment run': 1}
- documents with no audit row at all: none
- steps checked (one assertion each): 141
- emails sent (quotation requests, orders, payment advice, ...): 97

### E34 The whole application for a real user: the demo script from low stock to a paid invoice in a browser, every role guide followed as a new user, and every screen swept for errors, overflow and raw codes

Checks: Phase 1 acceptance: the flow works end to end for real people at real screens; a new user completes their first task from the guide; no screen shows an error, a blank panel, a raw code or sideways scrolling

- people / master rows loaded: 8 / 261
- people per step (audit log): {'approved the requests': 1, 'approved the orders': 1, 'received the goods': 1, 'entered the invoices': 1, 'released the block': 1, 'planned the run': 1, 'approved the run': 1}
- screens swept (role x screen x width): 204
- steps taken at screens: 76
- checks passed / failed: 317 / 0
- transcript and screenshots: evals/results/journeys/
- note: requester's notifications: GOODS RECEIVED just now Goods arrived for PO-CKN-2627-00002 | REQUEST just now Request REQ-CKN-2627-00004 approved
- note: control tower with the Gujarat invoice blocked: {'request_approval': 0, 'sourcing': 0, 'rfq_out': 0, 'award': 0, 'po_approval': 0, 'po_sent': 0, 'awaiting_receipt': 0, 'awaiting_invoice': 0, 'invoice_blocked': 1, 'awaiting_payment': 4}
- note: control tower stages at the end: [('request_approval', 0), ('sourcing', 0), ('rfq_out', 0), ('award', 0), ('po_approval', 0), ('po_sent', 0), ('awaiting_receipt', 0), ('awaiting_invoice', 0), ('invoice_blocked', 0), ('awaiting_payment', 0)]
- note: kpis at the end: {"cycle_time": {"approval": {"label": "request raised to approved", "cases": 4, "median": "0.0", "p90": "0.0"}, "ordering": {"label": "approved to order made", "cases": 4, "median": "0.0", "p90": "0.0"}, "po_approval": {"label": "order made to order approved", "cases": 4, "median": "0.0", "p90": "0.0"}, "dispatch": {"label": "order approved to sent", "cases": 4, "median": "0.0", "p90": "0.0"}, "delivery": {"label": "sent to goods received", "cases": 4, "median": "0.0", "p90": "0.0"}, "invoicing": {"label": "goods received to invoice received", "cases": 4, "median": "0.0", "p90": "0.0"}, "payment": {"label": "invoice received to paid", "cases": 4, "median": "27.0", "p90": "27.0"}, "total": {"
- note: control tower shape: {"as_of": "2026-12-21", "locations": ["PLT-CKN"], "stages": [{"stage": "request_approval", "title": "Request waiting for approval", "count": 0, "value": "0.00", "buckets": {"0-2": 0, "3-7": 0, "8-14": 0, "15+": 0}, "oldest_days": 0, "by_location": {}}, {"stage": "sourcing", "title": "Approved, not y
