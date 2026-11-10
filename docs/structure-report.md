# Structure report (11 Nov 2026)

What the code looks like, what was tidied overnight, and what is worth doing later. Nothing here changes behaviour.

## Shape

About 83,000 lines of source and tests (not counting migrations, `legacy/` or vendored files): 12 backend packages, 40 tenant migrations and 3 control migrations, a plain-JS main app (`ui/`), a React admin app (`frontend/`), 83 test files, 34 evals and two data generators.

## The 15 largest source files

| Lines | File | What to do |
|---|---|---|
| 2206 | `evals/e30_demo_runs.py` | Split into shared helpers and one file per run; E34 now covers the full flow, so check which E30 runs are still needed |
| 1878 | `backend/p2p/platform/ui_api.py` | Split by screen (dashboard, requests, vendors, inventory, contracts, settings) into routers |
| 1328 | `backend/p2p/platform/setup_api.py` | Split by area (people, roles, approval chains, policies, automation) |
| 1238 | `evals/e34_flow_journey.py` | Same split as E30 (journey steps vs. screen sweep) |
| 1189 | `tests/test_orders.py` | Split creation, approval, dispatch and changes |
| 1067 | `evals/e32_security.py` | Split per attack path |
| 1041 | `tests/test_sourcing.py` | Its fixtures (`db`, `outbox`, worlds) are imported by about 30 test files: move them to a shared `tests/fixtures.py` first |
| 962 | `datagen/generator/profile_flow.py` | Data tables inside the code: move to `datagen/catalog/flow/` files |
| 931 | `tests/test_main_ui.py` | Split per screen |
| 903 | `backend/p2p/invoices/reading.py` | Text extraction, row parsing and the draft pipeline are three jobs |
| 889 | `backend/p2p/agents/intake.py` | Prompt, mapping and the job wrapper |
| 863 | `datagen/generator/documents.py` | Page drawing (`pagekit.py`) is already apart; split invoices from replies |
| 850 | `backend/p2p/agents/supplier_comms.py` | Handlers per message kind |
| 843 | `evals/e29_control_tower.py` | Fine as one oracle; leave |
| 780 | `tests/test_invoice_reading.py` | Leave |

None of this is urgent: the files are long but each does one job and is covered by tests.

## Dependencies between packages

- **`formulas/` imports nothing from the rest of the app.** That is the rule (rule 2: numbers come from pure code) and it holds.
- **`platform/` is two things in one package.** The foundation (auth, audit, permissions, tenants, jobs, tasks, notifications, proposals) is imported by every domain package, while the API routers and screens' backends (`ui_api.py`, `setup_api.py`, `control_tower.py`, `workspace_api.py`, ...) import the domain packages back (orders, invoices, sourcing, approvals, agents). That is why 13 package pairs import each other. Splitting the routers into their own package (`api/`) would make the direction one-way: domain packages -> platform foundation, routers -> everything. It is a rename plus moving files; do it when `ui_api.py` is split.
- **Two-file cycles** exist only where one side registers itself with the other: every job module imports `platform/worker.py` to register, and the worker imports them to load them; `routing` and `tenants`; `approvals/workflow` and `approvals/email_links`; `invoices/payment` and `invoices/service`. They work because the imports are inside functions. Leave them; the registration pattern is deliberate.
- **`orders/quotes.py` belongs to sourcing** (quotes, comparison and award come before an order exists) but lives in `orders/` for history. Moving it is a rename; tests would follow.

## Names

- The screens say **Requests** and **Quotations**; the API paths and some modules still say `requisitions` and `rfqs`. Leave the API (changing it breaks nothing today but buys nothing); keep this in mind when reading logs.
- `datagen/generator/flow.py` and `flow2.py` and `sim.py` and `simulate.py` do not say what they do; `flow` here means the generator's event flow, and not the `flow` demo company (which is `profile_flow.py`). Rename when next touched.
- The package `p2p.requests` has the same name as the popular HTTP library; harmless inside the `p2p` namespace.
- Reference tables are called `ref_*` in the database and by plain names in the data dictionary (`gst_state` and `ref_gst_state`); `docs/database-schema.md` shows the real names.

## Things that are there but not in use

- **Focus mode** (`ui/js/focus-mode.js` 708 lines, `ui/css/focus-mode.css` 461 lines) is off by default and its toggle is hidden. It still works if switched on in code. Delete it, or give it a home, in the next UI pass.
- **`agents/demo.py`** is a development-only toolset with a payment tool; the product never imports it and a test enforces that. Keep it out of `EXTRA_MODULES` on any shared server.
- **11 stale scenario tests** in `tests/test_scenarios_sap_d365.py` are marked "expected to fail" because they import module names from the Phase 0 plan (for example `p2p.payments.due_date`). The features exist under other names (`formulas/payment.py`, `orders/`, `formulas/matching.py` and so on) and are tested there. Rewrite them against the real modules, or delete them: as they stand they say "not built" about things that are built.
- The unused CSS rules inside `ui/css/*.css` have not been audited; there is no tool for it in the allowed set.

## What was removed in this pass

- Python: 5 unused functions (`to_json` in intake, `on_or_after` in the calendar, `round_qty`, `gstin_like`, `_empty_list`), 3 unused constants (`FIXES`, `STATES`, `KINDS`), the empty package `p2p/payments/` (payments live in `invoices/`), and 9 copies of the same `_rows` database helper merged into `platform/rows.py`.
- Tests: one dead `if False` branch and one unused constant.
- UI: 4 files that `index.html` never loaded (`css/styles.css`, `css/trail.css`, `js/chat.js`, `js/dashboard.js`).
- Found by checking every module against every import in the code, tests, evals and scripts, including strings that name modules (the `EXTRA_MODULES` setting, the router, the executors registered by action name). Functions registered by name (`@proposals.executor`, `@worker.register`, route handlers) were kept.

## Added in this pass

- `docs/database-schema.md`, generated from the migrations by `scripts/gen_schema_doc.py`, with a test that fails when it is out of date (the hand-written data dictionary stays the place that explains meaning).
- `docs/dependencies.md`, and the packages the code imports directly but `requirements.in` did not declare are now declared (docling, pillow, jinja2, langchain-core, openpyxl, httpx2; python-docx, numpy and pyyaml for tests and evals). They were already installed and pinned in `requirements.txt`, so no install changes.
