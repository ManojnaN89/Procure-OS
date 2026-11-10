# Evals report

Run on 2026-11-10 13:11; AI evals used a scripted model.

| ID | What it checks | Result | Seconds | Key figures |
|---|---|---|---|---|
| E01 | Replenishment orders the right items in the right quantities | **PASS** | 2.8 | lines expected 118; lines planned 118; missing 0; extra 0 |
| E02 | Approval routing follows the rule table and the organisation | **PASS** | 14.1 | cases 2388; rule agreement % 100.0; cases with no rule 361; chain cases 2027 |
| E03 | Sourcing picks the right contract price, or sends the line to quotation | **PASS** | 7.8 | items 172; cases 4128; decided on a contract 1236; sent to quotation 2892 |
| E04 | GST and order arithmetic is exact, to the paisa | **PASS** | 0.2 | random lines 5000; net price errors 0; line errors 0; identity errors 0 |
| E05 | Quote comparison recommends the right supplier and the award rules hold | **PASS** | 13.5 | scenarios 160; lines compared 323; recommendation correct % 100.0; compliance flags correct % 100.0 |
| E06 | The whole flow, stock level to approved purchase order, keeps its promises | **PASS** | 10.9 | requests 16; lines 118; lines ordered 117; RFQs 11 |
| E07 | Repeating or racing any stage makes no duplicates | **PASS** | 0.4 | serial repeats 3; concurrent callers 6; seconds for the race 0.2; tables that grew none |
| E08 | Decisions stay reproducible when the rules change | **PASS** | 0.2 | decisions 16; band holds before / after the change 16/0 and 16/0; old rule rows untouched True; past date reproduces the old version % 100.0 |
| E09 | Every write action is open to exactly the roles the plan names | **PASS** | 7.8 | actions 24; roles tried 8; cells checked 192; too permissive 0 |
| E10 | One company cannot see another company's data | **PASS** | 6.9 | probes 73; leaks 0; company A sees its own records True |
| E11 | Hostile text is shown as text and never run | **PASS** | 31.5 | screens opened 17; hostile payloads 6; script ran or markup injected 0; SQL probes 12 |
| E12 | No floats in money, exact amounts on the wire, India's calendar, no secrets, synthetic data stays synthetic | **PASS** | 0.5 | money modules scanned 6; float uses 0; money fields as JSON numbers 0; UTC dates in business logic 0 |
| E13 | Supplier emails are read into the right quotes, and when unsure the system asks a person | **PASS** | 8.5 | email cases 39; perfect reader: exact % 100.0; perfect reader: silent errors 0; sloppy reader: mistakes that did not slip through {'invented price': '33/33 not clean-but-wrong', 'invented gst': '33/33 not clean-but-wrong', 'invented delivery': '33/33 not clean-but-wrong', 'swapped prices': '16/16 not clean-but-wrong'} |
| E14 | Hostile emails, even obeyed by the model, change nothing they should not | **PASS** | 4.0 | attacks 13; runs 26; defended 26; failed 0 |
| E15 | The assistant tells the truth from the data, adds nothing up on its own, and changes nothing | **SKIP** | 0.0 |  |
| E16 | Documents, budget, contract ceilings and reminders do what the plan says | **PASS** | 7.6 | documents: suppliers checked 60; documents: spoiled cases 50 (50 right); documents: bad suppliers asked to quote 0; budget: cases (over / within) 80 (44 / 36) |
| E17 | Orders are sent to the right supplier with the right amounts, only when approved, and acknowledged on time | **PASS** | 14.7 | orders by status before sending {'approved': 31, 'submitted': 3}; orders sent 31; acknowledged 25; late promises flagged 6 |
| E18 | Goods receipt: tolerance, rejections, inspection, stock and order progress are right | **PASS** | 19.3 | order lines to receive 108; scenarios {'full': 20, 'over_limit': 20, 'partial_then_rest': 19, 'reject': 18, 'over_refused': 18, 'inspect': 13}; stock positions changed 108; receipts posted 127 |

**17 of 18 passed.**

## Details

### E01 Replenishment orders the right items in the right quantities

Checks: Plan: read stock levels, create requests for items at their reorder point (quantity from the planning data, whole packs, supplier minimum)

- lines expected: 118
- lines planned: 118
- missing: 0
- extra: 0
- quantity exact %: 100.0
- value exact %: 100.0
- grouping exact %: 100.0
- requests: 16
- total value: 15975720.79

### E02 Approval routing follows the rule table and the organisation

Checks: Rule 8 and the plan: a rule is chosen by value band, spend type, category, department and the date it is in force; its chain becomes real people

- cases: 2388
- rule agreement %: 100.0
- cases with no rule: 361
- chain cases: 2027
- chain agreement %: 100.0

### E03 Sourcing picks the right contract price, or sends the line to quotation

Checks: Plan: contract vs RFQ per line; only an active contract in force, for an active supplier, in the right unit, with the quantity break

- items: 172
- cases: 4128
- decided on a contract: 1236
- sent to quotation: 2892
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

- requests: 16
- lines: 118
- lines ordered: 117
- RFQs: 11
- quotes: 30
- purchase orders: 34
- orders approved: 31
- line fates: {'ordered': 117, 'quotation open': 1}
- stage seconds: {'replenishment and submission': 1.12, 'request approvals': 0.59, 'sourcing and RFQ emails': 1.94, 'quotes and awards': 0.92, 'purchase orders and their approval': 3.38}
- total seconds: 8.0
- company build seconds: 2.81
- orders lines checked: 117

### E07 Repeating or racing any stage makes no duplicates

Checks: Plan: no duplicate requests, RFQs, emails or orders; document numbers have no gaps and no repeats

- serial repeats: 3
- concurrent callers: 6
- seconds for the race: 0.2
- tables that grew: none
- extra emails: 0

### E08 Decisions stay reproducible when the rules change

Checks: Rule 8: rules and rates are versioned data; a decision stays reproducible against the version it used

- decisions: 16
- band holds before / after the change: 16/0 and 16/0
- old rule rows untouched: True
- past date reproduces the old version %: 100.0
- a new submission uses: ('REQ-02', 3)
- approval audit rows without a rule version: 0

### E09 Every write action is open to exactly the roles the plan names

Checks: Rule 7 and the plan: requester, buyer, approver and administrator duties are separate; nobody else can do them; nobody can without logging in

- actions: 24
- roles tried: 8
- cells checked: 192
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

- screens opened: 17
- hostile payloads: 6
- script ran or markup injected: 0
- SQL probes: 12
- server errors: 0
- slow (sleep ran): 0
- orders on screen: 34
- pipeline note: 9

### E12 No floats in money, exact amounts on the wire, India's calendar, no secrets, synthetic data stays synthetic

Checks: Rules 1, 9, 10 and the generated-data safety rule

- money modules scanned: 6
- float uses: 0
- money fields as JSON numbers: 0
- UTC dates in business logic: 0
- tracked files scanned for secrets: 494
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

- documents: suppliers checked: 60
- documents: spoiled cases: 50 (50 right)
- documents: bad suppliers asked to quote: 0
- budget: cases (over / within): 80 (44 / 36)
- budget: disagreements: 0
- ceilings: random cases: 3000
- ceilings: disagreements: 0
- ceilings: contract orders replayed: 18
- ceilings: usage figures that differ from the replay: 0
- reminders: expected days: Tue 10 Nov, Thu 12 Nov
- reminders: silent suppliers on schedule: 2 of 2
- reminders: reminded though replied or declined: 0

### E17 Orders are sent to the right supplier with the right amounts, only when approved, and acknowledged on time

Checks: Plan: an approved order goes to the supplier by email; nothing else does; the supplier's promised date is checked against the date the goods are needed; an order with no acknowledgement in two working days is overdue

- orders by status before sending: {'approved': 31, 'submitted': 3}
- orders sent: 31
- acknowledged: 25
- late promises flagged: 6
- overdue day-checks: 48

### E18 Goods receipt: tolerance, rejections, inspection, stock and order progress are right

Checks: Plan: goods are received against a sent order; over-delivery is accepted up to the category's tolerance; rejected goods stay out of stock; items needing inspection wait; the order line and the stock follow what was accepted

- order lines to receive: 108
- scenarios: {'full': 20, 'over_limit': 20, 'partial_then_rest': 19, 'reject': 18, 'over_refused': 18, 'inspect': 13}
- stock positions changed: 108
- receipts posted: 127
