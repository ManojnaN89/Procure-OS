# Overnight run: prove every capability, fix what is missing, retest

Goal: by morning every Phase 0 and Phase 1 function of the plan (`docs/capability-matrix.md`, 125 rows) works end to end,
with evidence, and the code is tidy. Nothing from Phase 2 or later is built.

## Rules for every worker

1. **Work on branch `overnight/p1-complete`.** Commit only your own files, one commit per finished area, with
   `git commit <paths> -m "..."` (never `git add -A`, never amend, never push, never reset or checkout other people's
   changes). Author is Nikethan CBC, no co-author line. If git reports a lock, wait a few seconds and retry.
2. **One area at a time, one row at a time.** For each row of your area in the matrix:
   - Find the evidence that it works: an existing test or eval that really exercises it, or a screen you drove in a browser.
     Read the test; a test that does not check the function does not count.
   - If there is none, write the check first (API level, or Playwright for a screen), run it.
   - If it fails or the function is missing or only half there: implement it the way the repo does things (CLAUDE.md rules:
     money is Decimal, models never compute a number, every write through a service with an audit row, sensitive actions
     are proposals, tenant safety, segregation of duties, rules and rates are data), then run the check again.
   - Update the row: Status `verified` (worked already) or `fixed` (you built or repaired it), the Evidence column with the
     test or eval name (and the screen), and a short Notes entry for anything a person should know.
   - `deferred` only if the row is really a later-phase function (say which phase and why); `blocked` only if it needs the
     owner (a key, a decision), and say exactly what. Never leave a row `todo` without a note saying how far you got.
3. **Money and rules:** do not change a financial formula or a rule table to make a test pass. If a test and the code
   disagree, find out which is right from the plan (`planning/` is local only, git-ignored) and the data dictionary.
4. **Machine rules.** Two workers run at once at most, each in its own slot; use only your slot's names:
   - Slot A: `P2P_TEST_CONTROL_DB=p2p_ctl_slot_a`, `P2P_EVAL_CONTROL_DB=p2p_ectl_slot_a`, server/browser port 18310.
   - Slot B: `P2P_TEST_CONTROL_DB=p2p_ctl_slot_b`, `P2P_EVAL_CONTROL_DB=p2p_ectl_slot_b`, server/browser port 18320.
   - Set both variables in every command you run. Never run `scripts/check.py` or the whole test suite; run only the test
     files and evals for your area. Never touch the dev control database or the tenants `demo`, `plant`, `flow` (read-only
     is fine); build throwaway tenants in your slot's databases.
   - Never kill, stop or restart any process you did not start. Never touch ports 8000, 5173, 8101. Stop only your own.
   - Windows: keep LF line endings when rewriting files from Python; biome and ruff formatting must pass on what you touch.
5. **No outside effects.** Emails go only to the local Mailpit. No real bank, no real GST or other service. Live model calls
   are off unless your brief says so (then a few, paced).
6. **Phase discipline.** Do not build supplier portal, full vendor scorecards, service receipts, statutory reports, cash-flow
   views or anything else the matrix marks as a later phase. If you are unsure, mark it `deferred` with the reason.
7. **Keep it clean.** Match the surrounding code. No new dependency without checking its licence and that it is the real
   package. No dead code left behind. Tests are named after scenarios (given, when, then in the body). Docs updated when the
   data model or a screen label changes (`docs/data-dictionary.md`, `docs/guides/`).
8. **Time-box.** About 90 minutes per area. When the time is up or every row is resolved, stop, run ruff, mypy (from
   `backend/`) and the tests you added, commit, and report.

## What a worker reports

- Rows resolved: counts by status, and the list of `fixed` rows (what was missing and what you built).
- Rows left and why.
- Files changed, tests and evals added with their results.
- Anything a person must decide in the morning.

## Order of the night (the orchestrator, the main session, starts each wave and checks each report)

| Wave | Areas | Slots |
|---|---|---|
| 1 | Demand & Requisition (+ Integration & Data); Sourcing | A, B |
| 2 | Purchase Orders; Receiving + Supplier Management | A, B |
| 3 | Invoice & Matching; Payments + India Compliance | A, B |
| 4 | Spend Control + Analytics & Insight; Automation & AI Control + Platform & Administration + the Phase 0 foundation rows | A, B |
| 5 | End-to-end journeys in a real browser on a fresh `flow` company, one slot only: every role, every screen, the whole flow, with a recorded transcript | A |
| 6 | Structure: module map in CLAUDE.md, dead code, duplicated helpers, oversized files reported (not rewritten blindly), docs | A |
| 7 | Orchestrator: full `scripts/check.py`, all evals, matrix summary, `docs/morning-report.md`, final commit on the branch | none |

Wave 5 and 7 run alone: they use the fixed browser ports and the whole database.
