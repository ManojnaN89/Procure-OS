# ProcureOS: P2P platform

An India-first, domain-neutral, multi-tenant procure-to-pay platform: request, approval, sourcing, purchase order, receipt, invoice match and payment. The system does the routine work and stops at a person only for a decision. Agents are used only where the work is open-ended.

## Run it

1. Copy `.env.example` to `.env` and fill it in (API keys are entered by you, never committed).
2. `cd backend && python -m venv .venv && .venv\Scripts\python -m pip install -r requirements-dev.in`, then `cd ../frontend && npm install && npm run build`.
3. `cd backend && .venv\Scripts\python -m p2p.cli control migrate`, then make the demo company: `.venv\Scripts\python -m p2p.cli demo reset flow`.
4. From the project folder: `python scripts/dev.py` (it refuses to start if ports 8000 or 5173 are still taken by an earlier run).

| What | Address |
|---|---|
| The app people work in | http://127.0.0.1:8000/ |
| Admin (approvals, company setup, automation, audit) | http://127.0.0.1:8000/admin (also http://127.0.0.1:5173 while developing) |
| Test mailbox (supplier and approval emails land here) | http://127.0.0.1:8025 |

## Log in (the `flow` demo company)

One login per role, password for all `flow123456` (a throwaway development password for synthetic data; `demo reset` refuses a database that is not local).

| Login | Role |
|---|---|
| requester@flow.example | Requester |
| manager@flow.example | Maintenance manager (approves up to Rs 1,00,000) |
| approver@flow.example | Plant head (large amounts, orders, price variances, payment runs) |
| buyer@flow.example | Buyer |
| store@flow.example | Storekeeper |
| ap@flow.example | Accounts payable |
| finance@flow.example | Finance manager |
| admin@flow.example | Admin (setup only) |

Walk the whole flow with `docs/demo-script.md`. Start as the buyer: **Requests** → **Check stock levels**.

## Where things are

| Read | For |
|---|---|
| `docs/guides/` | One short guide per role (also on the Help page), and how rules and automation controls are set |
| `docs/demo-script.md` | The nine-step walk-through, as run by eval E34 |
| `docs/data-dictionary.md`, `docs/database-schema.md` | What the data means, and every table and column as built (generated) |
| `docs/capability-matrix.md` | Every function of the plan, with the evidence that it works |
| `docs/security-review.md`, `docs/dependencies.md`, `docs/structure-report.md` | Security findings, packages and licences, code structure |
| `CLAUDE.md` | Working rules and the map of modules |
| `evals/README.md`, `datagen/README.md` | The evals against the plan, and the synthetic data |

## Check it

`python scripts/check.py` runs ruff, mypy, pytest, the browser tests, biome and tsc. `python evals/run.py` runs the evals (`--live` uses the real models; keys in `.env`). Never run two test runs at once: they share one test database.

## Repository

The platform is `backend/`, `ui/`, `frontend/`, `datagen/`, `docs/`, `tests/`, `evals/`. The original Procure-OS code is in `legacy/procure-os-v1/` as a reference only; nothing in it is run, tested or imported. Work happens on branches and reaches `main` by pull request; nothing is pushed unless the owner does it.
