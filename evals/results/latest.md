# Evals report (block 0b, 21 Nov 2026)

Full run E01-E59 finished 02:22 on 21 Nov; AI evals used a scripted model. The per-eval detail files in this folder are from block 0 and were not refreshed (later single-eval runs overwrite them).

```
E01   PASS     5.8s  Replenishment orders the right items in the right quantities
E02   PASS    37.4s  Approval routing follows the rule table and the organisation
E03   PASS     5.8s  Sourcing picks the right contract price, or sends the line to quotation
E04   PASS     0.4s  GST and order arithmetic is exact, to the paisa
E05   FAIL    40.7s  Quote comparison recommends the right supplier and the award rules hold
E06   PASS     6.0s  The whole flow, stock level to approved purchase order, keeps its promises
E07   PASS     0.6s  Repeating or racing any stage makes no duplicates
E08   PASS     0.4s  Decisions stay reproducible when the rules change
E09   PASS    36.4s  Every write action is open to exactly the roles the plan names
E10   PASS    14.3s  One company cannot see another company's data
E11   PASS    46.8s  Hostile text is shown as text and never run
E12   PASS     1.3s  No floats in money, exact amounts on the wire, India's calendar, no secrets, synthetic data stays synthetic
E13   PASS    16.2s  Supplier emails are read into the right quotes, and when unsure the system asks a person
E14   PASS     6.5s  Hostile emails, even obeyed by the model, change nothing they should not
E15   SKIP     0.0s  The assistant tells the truth from the data, adds nothing up on its own, and changes nothing
E16   PASS    10.5s  Documents, budget, contract ceilings and reminders do what the plan says
E17   PASS     5.1s  Orders are sent to the right supplier with the right amounts, only when approved, and acknowledged on time
E18   PASS     5.8s  Goods receipt: tolerance, rejections, inspection, stock and order progress are right
E19   PASS    69.5s  Invoices are matched to orders and receipts the way the dataset's answers say
E20   PASS    65.3s  Due dates and tax at source are worked out the way the dataset's answers say
E21   PASS    81.9s  Payments are for the right amounts, in the right order, once, and only for invoices that may be paid
E22   PASS    36.0s  Company setup is admin-only, logged, validated, and its locks cannot be undone
E23   PASS    25.9s  Vendor scores follow how suppliers really behave, and planted repeat requests are caught
E24   PASS    31.8s  Plain-words requests become the right drafts or one right question, and never a wrong draft
E25   PASS   106.3s  Blocked invoices are explained with the right cause, and a block is never changed by the agent
E26   PASS   444.5s  Advances, early-payment discounts and the closing notice are right
E27   PASS   190.7s  Invoices are read from documents correctly, e-invoices without a model, and a wrong reading never looks clean
E28   PASS    66.5s  Supplier emails are classified, small changes applied, the rest handed to a person, nothing wrong applied
E29   PASS   131.1s  The control tower, the key figures and the alerts agree with an independent recount
E30   PASS   372.4s  Demo runs through the real screens: a normal run, a part delivery and a price mismatch on the invoice, each typed in and with documents, and a held order
E31   PASS   898.7s  Synthetic invoices and replies read back correctly by the code readers, and what still needs a model is counted
E32   PASS   327.7s  Hostile text, even obeyed by the model, changes nothing it must not; limits, secrets and dependencies hold
E33   PASS   175.1s  The main demo company completes the whole flow, from low stock to a paid invoice, with separate people for each job
E34   PASS   709.7s  The whole application for a real user: the demo script from low stock to a paid invoice in a browser, every role guide followed as a new user, and every screen swept for errors, overflow and raw codes
E35   PASS    62.2s  ERP demand files load exactly once, with every bad row explained
E36   PASS    39.0s  The buying route: contract, single source, emergency, safe reorder, else quotation with the right quotes
E37   PASS    17.2s  Stock is set aside earliest need-by first and only the shortfall is bought
E38   PASS    30.2s  Shortfalls of several orders combine into one line, and the budget is checked per location
E39   FAIL   150.0s  Supplier selection: must-haves, total cost, tie-break, split only when it saves, reasons, orders per supplier
E40   FAIL   126.1s  PO tiers by value, delivery location, touchless orders only within every limit
E41   PASS   128.2s  Approval by email: the right person, a full email, one reminder, escalation on time, a code above the limit
E42   PASS    47.6s  Supplier confirmation: the link works once, every change is the buyer's, a new version tells the requester
E43   PASS    51.9s  Service entry, the two-way match of services, receipts without an order and serial numbers are right
E44   PASS   376.5s  The exceptions screen lists every stuck case with the right reason, owner and age, by role
E45   PASS   634.2s  Received not invoiced, supplier statements, small-supplier deadlines and the order trace reconcile
E46   FAIL   148.0s  The IT services company (Brightwave) through all 13 steps in a browser: services, a serialised laptop, two-way match
E47   PASS    21.6s  Cancelling a production order releases its stock to the next order at once, never stocked and bought twice
E48   PASS    52.9s  Shortfall requests are estimated at the contract price, else the last price paid, else the standard price
E49   PASS   202.0s  Supplier selection by weighted score, and the answer key's cheapest supplier, match an independent oracle
E50   PASS    66.9s  Contract price versions: a draft is never used, a second person activates it, the old one ends the day before
E51   PASS    51.5s  Control changes wait for a second administrator; nobody approves their own change or their own access
E52   PASS    36.9s  Bank accounts: verified by a second person against a proof before anything is paid into them
E53   PASS    83.0s  PO makers never approve: not the order, its price, a supplier's price rise, nor as a deputy
E54   FAIL   181.5s  A maker of a purchase order never approves it, by any route (C1), written without the implementation
E55   PASS   315.7s  Tolerance versions: each match records the version in force on the day received, and past decisions reproduce
E56   PASS   120.2s  Prospects quote, count only when their identity is checked by someone else, and never reach an order early
E57   PASS   148.5s  A regional supplier approval or contract price is never used outside its scope; the delivery location decides
E58   PASS    96.7s  Strategic reviews open on their triggers; price triggers only above every threshold, on verified fresh evidence
E59   PASS   125.9s  Price types are kept apart; only verified, fresh, in-region, convertible benchmarks count, and never as a price
```

54 of 59 passed; report in C:\CBC\PR to PO\.claude\worktrees\c1-po-sod\evals\results\latest.md

## Re-runs after the full run

- E05, E39, E40, E54: code fixes (the expired-quote refusal applies only on re-award after a lapsed award; quote comparison falls back to the request's ship-to location; an order to a supplier blocked after sourcing is made but never issued). E05 and E54 updated for the 20 Nov decisions (blocked SUP-007 in the data; OP-6 agreement). All PASS.
- E46: the eval now waits until an in-app approval is saved before looking up the next approver. PASS (160 s).
- E50-E59: PASS after the fixes.
- E15 needs a live model (skipped).
