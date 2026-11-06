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

An eval fails on any `high` or `medium` finding. Add an eval as `evals/eNN_name.py` with `ID`, `TITLE`, `REQUIREMENT` and
`run(ctx) -> Outcome`. When an eval finds a real bug, fix the platform and add a normal regression test to `tests/` as well.
