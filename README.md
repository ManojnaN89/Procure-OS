# P2P platform

An India-first, domain-neutral, multi-tenant procure-to-pay platform: request, approval, sourcing, purchase order, receipt, invoice match and payment, with agents only where the work is open-ended.

## Start

1. Copy `.env.example` to `.env` and set the values (API keys are entered by you, never committed).
2. `cd backend && python -m venv .venv && .venv\Scripts\python -m pip install -r requirements-dev.in`, then `cd ../frontend && npm install`.
3. `python scripts/dev.py`

Backend http://localhost:8000/api/health, frontend http://localhost:5173, test mailbox http://localhost:8025.

Run every check with `python scripts/check.py`. Working rules are in `CLAUDE.md`, the data model in `docs/data-dictionary.md`, test data in `datagen/README.md`.
