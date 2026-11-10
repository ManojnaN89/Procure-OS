# Evals

Checks of the whole platform against the plan, kept apart from the unit tests. The tests prove that a piece does what its
author meant. The evals ask whether the platform does what the plan promises, using **independent oracles**: each eval
recomputes the right answer from the raw dataset with its own plain code and compares it with what the platform did.

    python evals/run.py                  everything; the AI evals use a scripted model
    python evals/run.py --live           the AI evals call the real models (keys in .env, free tiers)
    python evals/run.py --only E04,E09   some of them
    python evals/run.py --quick          smaller samples

It builds its own databases (`p2p_control_eval` and one per company) and drops them afterwards. The report is written to
`evals/results/latest.md` (and `.json`); the exit code is 1 if any eval failed.

| ID | Checks |
|---|---|
| E01 | Replenishment orders the right items in the right quantities (stock replayed from the raw files) |
| E02 | Approval rule and chain, for hundreds of values, dates and requesters, against the rule table and org data |
| E03 | Contract or quotation for every stocked item, against the raw price records |
| E04 | GST, net price and order totals, exact to the paisa, on thousands of random lines (exact fractions) |
| E05 | Quote comparison, recommendation and the award rules, on random sets of quotes |
| E06 | The whole flow, stock level to approved purchase order: nothing lost, money reconciles, every change audited, only eligible suppliers asked, stock on the way counted once |
| E07 | Repeating or racing any stage makes no duplicates; document numbers have no gaps |
| E08 | Decisions stay reproducible when the rules change |
| E09 | Role-by-action permission matrix against what the plan lets each role do |
| E10 | One company cannot see another's data |
| E11 | Hostile text is shown as text (real browser) and SQL probes do nothing |
| E12 | No floats in money, exact amounts on the wire, India's calendar day, no secrets, synthetic data stays synthetic |
| E13 | Supplier emails read into quotes: 39 synthetic emails with known answers; silent errors must be 0 |
| E14 | Hostile emails, even obeyed by a model, change nothing they should not |
| E15 | The assistant (live only): right answers from the data, no arithmetic it cannot ground, no writes |
| E30 | Seven scripted demo runs through the real screens (real browser, each person signed in on their own): the plan's three scenarios (normal run, part delivery, invoice that does not match), each typed in and with documents (quotations arrive as emails, invoices are uploaded as PDFs), and an order held for its price; every money figure against an independent calculation from the dataset, and a document leaves the same figures as typing |
| E31 | The generated documents (invoices as PDFs and pictures, supplier replies as .eml) with their answer key: the code readers (QR, text layer, arithmetic, OCR sample) read them back, no model is called, and the share still needing a model or a person is counted |
| E32 | The security pass: hostile text (in supplier names, supplier emails, invoices, plain-words requests, questions, the invoice explainer's own sources, quotation replies) is put in front of every model-facing step and the model OBEYS it (asks for tools it was not given, answers with values not in the text, claims things were released or paid); afterwards the money, suppliers, banks, orders, invoices and payments are exactly as before and nothing in the log was done by a machine that only a person may do. Also: each agent's tool list is exactly as written down and read-only; another company's data is never in a prompt or an answer; failed-login and per-person limits answer 429; no secret from `.env` or key-shaped text is in a file git would commit or in what the API says or logs; the dependency audit has no unaccepted high finding. Six defences are broken on purpose and the eval has to notice each. |
| E33 | The small demo company (`flow`, made by `python -m p2p.cli demo reset flow`) completes the whole flow with its eight logins: three requests from low stock (one approves itself), contract order, two quotation requests, four orders with GST, receipt, four matched invoices, one payment run approved by the plant head, paid; every figure compared with the dataset's own files; wrong person refused at each step | Plan Phase 1 acceptance; `docs/demo-script.md` |
| E34 | The whole application for a real user, on the small demo company: `docs/demo-script.md` followed literally in a real browser (each of the eight logins in its own window) from "Check stock levels" to a paid invoice, including the New request form, "Approve selected", approval by the emailed link, replies read from email, a QR e-invoice upload, a part delivery, a blocked invoice released by another person, the bank statement import, notifications, the control tower numbers, the assistant and the admin audit; then every role guide's first task checked name by name against the screens that role saw; then every screen of every role opened at 1440x900 and 390x844 and failed on any browser error, blank panel, "couldn't load", raw code or sideways scroll. Writes `evals/results/journeys/journey.md` (screenshots git-ignored). About three minutes. |

An eval fails on any `high` or `medium` finding. Add an eval as `evals/eNN_name.py` with `ID`, `TITLE`, `REQUIREMENT` and
`run(ctx) -> Outcome`. When an eval finds a real bug, fix the platform and add a normal regression test to `tests/` as well.

## Demo runs (E30)

`python evals/run.py --only E30` serves the web app inside the eval process (port 18290, or `P2P_DEMO_PORT`) and drives it
with Playwright (headless Chromium, needs `playwright install chromium`; the admin console must be built:
`cd frontend && npm run build`). Each person of the plant (buyer, storekeeper, accounts clerk, finance manager, heads of
stores and procurement, plant head, managing director) signs in on the login screen in their own browser window, finds their
work on the dashboard and does it; the job worker is run by hand between steps; mail goes to a fake outbox; no real model is
called (one scripted model answers what a careful reader would write for the document in front of it, and the platform still
checks every value it writes against the document).

The plan's three scenarios each run twice, "typed in" (slice A) and "with documents" (slice B):

| Run | Path | What it proves |
|---|---|---|
| 1 | Stock at its reorder point, a manager's approval, quotation (suppliers asked, two quote), award, order with IGST, send, receipt, invoice with IRN, payment run, owner's approval, bank file, paid, closed | the whole flow, with every figure checked |
| 5 | Run 1 with documents: the two quotations arrive as emails into the mailbox and are read into drafts the buyer confirms on the RFQs page; the invoice is uploaded as a PDF with the e-invoice QR code and read by code (no model call) | documents leave the same quotes, invoice and payment as typing |
| 4 | Part delivery: half the order arrives, the first invoice is matched on what was received, the promised date passes (late alert and a chaser task for the buyer), the balance arrives and completes the order, the second invoice, one payment for both, order closes | cumulative matching; the order closes only when complete and paid; the balance is chased once, not after completion |
| 6 | Run 4 with documents: emailed quotations, the first invoice read from its QR code, the second from a plain PDF read by the scripted model | both reading paths in one case |
| 2 | Contract price, request approves itself, order, receipt, invoice 12% dearer than the order is blocked, the clerk asks to pay it anyway, the plant head releases it, paid to a small supplier | a block is released only by another person; due date under the 45-day rule |
| 7 | Run 2 with the invoice uploaded as a plain PDF (read by the scripted model, checked against the page, confirmed by the clerk) | a document with a wrong price is blocked like a typed one |
| 3 | Contract price 24% above the estimate: the order is held, nobody can approve or send it, the finance manager accepts the price, then approval, send, acknowledgement | a price outside the band waits for a person |

Each step asserts what the screen shows; the data afterwards is compared with answers worked out here from the raw dataset
(request quantity and route, price breaks, GST split, rupee rounding, due date, bank file line, budget actual). A document run
is also compared with the typed run of the same scenario: the invoices must have the same status, amounts and due dates.
Refusals the screens never offer are checked straight on the API (a buyer approving an order, the person who proposed a
payment run approving it, a bank file before approval). A transcript of every step (who, what, what the screen showed) and a
screenshot of each is written to `evals/results/demo_runs/` (`<run>.md`, one folder of pictures and, for the document runs,
the emails and PDFs used, in `documents/`). `P2P_E30_RUNS=2,7` runs only those runs (a document run is compared with its typed
run only when that one ran too).
