# Morning report: Phase 0 and Phase 1 audit (10-11 Nov 2026)

## Result

Every one of the 125 Phase 0 and Phase 1 functions of the plan now has a verdict and evidence (`docs/capability-matrix.md`):

| Status | Rows |
|---|---|
| verified (worked, evidence named) | 93 |
| fixed (was missing or half there; built and tested tonight) | 31 |
| blocked (needs your decision) | 1 |
| todo / deferred | 0 |

- Full check (`python scripts/check.py`): green (ruff, format, mypy, all tests, browser tests, biome, tsc).
- All 34 evals pass offline (E15, the assistant, only runs with real models and was green in the live run).
- Live-model run (Groq, NVIDIA): assistant 100% right; invoice reading 98.6% of fields right, QR e-invoices need no model; supplier-email handling 90.0% right (target 90), 0 wrong changes ever applied; plain-words requests 96.7% right action, 100% of lines right, one draft in 30 had an extra line (drafts are always reviewed by a person); an injected invoice page was refused by the real model.
- The whole flow runs in a real browser as all eight `flow` logins (E34, 305 checks) and in seven scripted scenarios (E30).
- Work is on the local branch `overnight/p1-complete`, 15 commits, nothing pushed. To keep it: `git switch main` then `git merge overnight/p1-complete`.

## How to try it

1. Stop the old dev stack (close the terminal that runs `python scripts/dev.py`, or end the python and node processes on ports 8000 and 5173). The server that was running was days old and lacked recent routes.
2. `python scripts/dev.py`, then open http://127.0.0.1:8000/ . Supplier emails land at http://127.0.0.1:8025 .
3. The `flow` company: requester@, manager@, approver@, buyer@, store@, ap@, finance@, admin@ `flow.example`, password `flow123456`. Follow `docs/demo-script.md`; each role has a short guide under `docs/guides/` (also on the Help page).
4. `python -m p2p.cli demo reset flow` rebuilds the company from scratch (local databases only).

## What was built tonight (the 31 `fixed` rows)

- **Requests:** withdraw, cancel and copy; the requester is told when a request is approved, rejected or returned; stock issues and counts on Inventory; Excel import and an Export button on every list.
- **Approvals:** approve with lower quantities, approve several at once, approve from a signed emailed link (opening it decides nothing), escalation to the approver's manager, a Remind approver button, budgets visible with variance per cost centre and account.
- **Sourcing:** editable RFQ wording, Excel quote form attached to RFQ emails, drafted answers to supplier questions, comparison on landed cost with the last price paid, price questions, a weighted-score option with knock-outs, reasons for each recommendation.
- **Orders:** grouping by supplier and terms, versions with history, a PDF order document sent with the email, budget re-checked at the final price, cancel and close-what-is-left.
- **Receiving and suppliers:** requesters told what arrived; supplier records (create, edit) with GSTIN, PAN, Udyam, account and IFSC checks and a second-person approval of bank changes; a real onboarding form with document upload.
- **Invoices and payments:** a "Next:" action on every blocked invoice; TDS rates read from the versioned table (rule 8); bank statement import marks payments paid; item GST rates checked against the official table.
- **Analytics and platform:** the missing "duplicates stopped" figure, real dashboard and budget numbers, hold-out slices for the generated test documents.
- **Screens:** flow-ordered menu per role, My work first, progress strip on each case page, keyboard focus on every clickable control, the Assistant and Settings as before.

## For you to decide

1. **Reverse-charge GST (row 9.1.2, blocked).** No supplier or item in the data is reverse-charge and nothing reads the flag. Say which purchases are (for example road freight) and how the tax should show, and it can be built and checked.
2. **E-invoice QR signature.** Not checked against the portal's public key; that needs the key from you.
3. **Credit notes and matched quantity.** A credit note does not reverse matched quantity (the dataset answers say so; the plan's P1-17 wording says it should).
4. **Approver limits on big changes.** Cancelling or revising a large order, cutting a quantity, or a large stock adjustment needs only a reason and an audit row today. Should an approver be required above some value?
5. **An upload screen for the Excel master import** (it runs from the command line today) and an admin screen for payment terms and the PO terms template (settings and import only today).
6. **Security advisory:** one npm advisory (zod, rated high, reached only in the admin rule editor) is accepted until 1 Mar 2027 in `docs/audit-accepted.json`. The plan says no open high finding; please confirm.
7. **Dev `.env`** loads a demo agent with a payment tool (`EXTRA_MODULES=p2p.agents.demo`). Harmless in development; remove it before any shared deployment.
8. **Assistant accuracy sample:** the plan asks for 30 typical questions at 90%; E15 holds 11 (100% right). Adding 19 more and running it live would close that.
9. **New dependency:** reportlab (BSD) for the order PDF; the full list with licences is in `docs/dependencies.md` (psycopg is LGPL, flagged).

## Known limits

- Phase 2 and later are not started: supplier portal, full vendor scorecards, service receipts, tax and statutory reports, cash-flow views, harder documents.
- Splitting the largest files (`ui_api.py`, `setup_api.py`, E30) is recommended in `docs/structure-report.md`, not done.
- Eleven old "expected to fail" scenario tests name modules that do not exist (listed in the structure report).
- Case-page tables for orders, invoices and goods receipt are still wide.
