# P2P platform: rules for working in this repo

An India-first, domain-neutral, multi-tenant procure-to-pay platform (request, approval, sourcing, PO, receipt, invoice match, payment). Built by one developer with Claude Code. The plan lives in `planning/` (git-ignored); the data model is `docs/data-dictionary.md`.

## Layout

| Folder | What |
|---|---|
| `backend/p2p/` | FastAPI app. One package per area: masters, requests, approvals, sourcing, orders, receiving, invoices, payments, agents, ai_steps, rules, formulas, platform |
| `frontend/` | React + Vite + TypeScript, Biome |
| `datagen/`, `data/` | Synthetic data generator and reference tables. Generated datasets go to `data/datasets/` (git-ignored) |
| `tests/` | pytest, named after scenarios (given, when, then in the body) |
| `scripts/` | `dev.py` starts everything, `check.py` runs every check |

## Commands

- Start everything: `python scripts/dev.py` (Postgres and Mailpit in Docker, backend on 8000, frontend on 5173)
- All checks: `python scripts/check.py` (ruff, mypy strict, pytest, biome, tsc). Run before saying a task is done.
- Backend tools use `backend/.venv`. Dependencies: edit `requirements.in` or `requirements-dev.in`, install, then `pip freeze > requirements.txt`.
- Browser tests need `cd frontend && npm run build` and `playwright install chromium` once. They skip when the build is missing.
- Run the job worker (timers, escalations, email, mailbox polling): `python -c "from p2p.platform import worker; worker.run_forever()"` from `backend/`.
- Compare models: `python scripts/model_eval.py --models groq:openai/gpt-oss-20b --out result.json` (live calls, synthetic data only, paced by the gateway).
- Never run two pytest runs at once: they share one test database.
- Load a dataset into a company (masters first, then stock history): `python -m p2p.cli import masters <tenant> <dataset> [--dry-run]`, then `import stock ...`, from `backend/`. Each prints every row it left out and why.
- Create draft purchase requests for stock at its reorder point: `python -m p2p.cli replenish <tenant> [--dry-run] [--no-submit] [--schedule]` (creates the requests and sends them through approval; `--schedule` queues the daily run, which creates requests only when the setting `auto_replenishment` is on). Automatic requests need the setting `replenishment_department`: its head is who approves them.
- Decide how approved requests are bought and send the quotation requests: `python -m p2p.cli source <tenant> [--request NO] [--no-send]` (the worker does this by itself when a request is approved). Sending goes through the autonomy gate: unless `rfq.send` is set to automatic, a proposal waits for a buyer.
- Make purchase orders from the lines that are ready (contract prices and awarded quotations): `python -m p2p.cli order <tenant> [--request NO]` (the worker does it when a contract line is sourced or an RFQ is awarded). Quotes are entered by a buyer on the RFQ page.
- Regenerate data: `python -I datagen/generator/cli.py --size demo --seed 42`, check it with `datagen/generator/validate.py`.

## Rules

1. **Money is always `Decimal`**, quantised to paise. Never float for money, tax or quantity.
2. **A model never computes a number a decision uses.** Totals, tax, TDS, due dates and match results come from code in `formulas/` or `rules/`. A model may extract, classify, draft or explain.
3. **Three building blocks.** A *service* is deterministic. An *AI step* is one model call with schema validation and an evidence check. An *agent* is a tool loop. Use the simplest one that works.
4. **Every write goes through a service that writes the audit log.** No route or agent writes to the database directly.
5. **Sensitive actions are proposals.** Payment runs, vendor activation, releasing a payment block, accepting a price variance and approvals above a limit create a proposal that a person approves. Agents and services never execute these themselves.
6. **Tenant safety.** One database per tenant plus a control database. A request without tenant context is refused. A test must prove tenant A cannot read tenant B.
7. **Segregation of duties:** requester, approver, receiver, invoice approver and payer differ for the same case.
8. **Rules and rates are data, not code.** Approval matrices, tolerances, GST rates and TDS sections live in versioned tables. A decision stays reproducible against the version it used.
9. **Dates and time.** Store timestamps with time zone; business logic uses the financial year (April to March) and the business calendar with holidays.
10. **No secrets in the repo or in chat.** `.env` is git-ignored; the owner enters API keys manually. Development uses synthetic data only.
11. **Free and open-source tools only** (see the Tech Stack sheet in the plan). Do not add a dependency without checking its licence and that the package is the real one.

## Generated data safety

Generated PANs always carry the digits `0000` (never issued), phones are `+91 00000 …`, emails end in `.example`. Keep it that way.

## Definition of done for a task

Code, tests (including boundary cases for formulas), `python scripts/check.py` green, audit rows written for any write, docs updated if the data model changed.

## Style

Match the surrounding code. Short plain-language comments only where the reason is not obvious. Keep documents simple and short.

## Where things are (Phase 0)

| Area | Module in `backend/p2p/` |
|---|---|
| Tenants, migrations, routing by token | `platform/tenants.py`, `platform/routing.py`, `platform/migrations/{control,tenant}/` |
| Login, roles, limits, segregation of duties | `platform/auth.py`, `users.py`, `permissions.py`, `sod.py` |
| Audit log (insert only) | `platform/audit.py` |
| Rules (decision tables) and formulas | `rules/engine.py`, `formulas/` |
| Cases, tasks, notifications, worker | `platform/cases.py`, `tasks.py`, `notifications.py`, `worker.py` |
| Proposals (human confirmation) | `platform/proposals.py` |
| Models (routes and free-tier limits in `docs/model-selection.md`), AI steps, agents | `ai_steps/gateway.py`, `ai_steps/runner.py`, `agents/runtime.py`, `agents/autonomy.py` |
| Master data import (schema generated by `scripts/gen_master_schema.py`), stock levels | `masters/importer.py`, `masters/schema.py`, `receiving/stock.py`, `formulas/stock.py` |
| Quotes, comparison, award, purchase orders with GST, order approval | `orders/quotes.py`, `orders/service.py`, `orders/workflow.py`, `formulas/orders.py` |
| Sourcing: contract price or quotation request, suppliers asked, RFQ emails | `sourcing/service.py`, `sourcing/jobs.py`, `formulas/sourcing.py` |
| Approval of requests by the rule tables (lookup, chain, submit/approve/reject/return) | `approvals/rules.py`, `approvals/workflow.py` |
| Replenishment into draft requests, document numbers | `requests/replenishment.py`, `requests/jobs.py`, `formulas/replenishment.py`, `platform/numbering.py` |
| Files, parsing, email | `platform/files.py`, `parsing.py`, `emails.py`, `mailbox.py` |
| Admin API and run viewer | `platform/admin_api.py`, `logs.py` |

A new tenant-database table is a new file in `platform/migrations/tenant/versions/` (next number). A new agent tool is registered with `ToolRegistry`; modules that register tools or executors are listed in `EXTRA_MODULES` so the API process loads them.
