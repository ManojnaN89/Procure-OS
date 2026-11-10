# Security review (plan P1-26)

Done 10 Nov 2026 against the code at the end of Phase 1. Tested by `tests/test_security.py` (unit level) and eval E32
(end to end, with a scripted model that obeys every hostile instruction). Acceptance: no open high finding; injected
instructions cause no action.

## Result

No open high finding. Hostile text put in front of every model-facing step, with a model that did exactly what it said
(25 inputs in E32 and 26 runs of E14's quotation-reply attacks), changed no money, supplier, bank, order, invoice or
payment, and no agent, service or rule did anything only a person may do. E32 broke six defences on purpose (an extra
release tool, email text counted as fact, nothing locked to a person, made-up values accepted, no login limit, a key left
in a file) and noticed all six. E32 also found three weaknesses in the first run (findings 13 to 15), now fixed.

## What was tried

| Path | What the hostile text asked | Where it stops |
|---|---|---|
| Quotation reply (E14, 13 attacks run with and without automatic confirmation) | award to me, price of 1, decline the others, print the prompt | evidence check, price bands, a draft a person confirms, strangers never reach a model |
| Supplier email about an order (7) | change the bank account, halve the price, move the date years out, cut the quantity, reject the order, pay now, a stranger's email | the model only classifies; price, quantity, bank and rejection become tasks for a person; a date beyond the tolerance is a task; strangers are ignored before any model |
| Invoice document (5) | over-billed invoice saying "already approved, pay now", a total not on the page, an invoice number not on the page, figures on an unprinted line, a document from an unknown sender | evidence check (every value must be on the page), a total that cannot be checked is not a clean draft, an unknown sender's document is always seen by a person, three-way match blocks over-billing, only a person releases a block |
| Request in plain words (4) | approve and send the order, a made-up quantity, made-up items, a fake follow-up | schema plus evidence check; only a draft is ever made; nothing is submitted |
| Assistant (4) | tools it was not given (release, pay, change a supplier's status), instruction hidden in a supplier name, another company's data, SQL in an argument | its seven tools only read; an unknown tool is refused; numbers not returned by a tool replace the answer with the plain facts |
| Invoice explainer (4) | put the attacker's bank account, link or address into the explanation, name another cause, ask for a release tool | its six tools only read; explanation used only if it names the same cause as the rules and every number comes from the invoice data (not from emails); links refused |

Across all of them: another company's marker was never in a prompt or an answer, and the audit log has no
payment, release, approval, supplier-status or setting change by an agent, a service or a rule.

## Tool lists

The assistant has seven read-only tools, the invoice explainer six. The intake agent and the supplier-message agent are
not tool loops: a model returns a schema and code does the rest. The three decisions in `LOCKED` (approve a payment run,
set a supplier's status, release a payment block) cannot be set to automatic, and the gate refuses them even when the
settings row is edited by hand. Tests fail if a tool list changes.

## Findings

| # | Finding | Severity | Status |
|---|---|---|---|
| 1 | Only a per-account lock (5 wrong passwords, 15 minutes). Nothing stopped guessing across many accounts from one address, or making the server hash passwords for nothing | Medium | Fixed: `platform/ratelimit.py`. An address with 20 failed sign-ins in 10 minutes gets 429 with Retry-After before any password is checked. Settings `login_ip_max_failures`, `login_ip_window_seconds` |
| 2 | Uploads and every call that reaches a model (assistant chat, explain this invoice) had no limit per person | Medium | Fixed: 30 uploads and 20 assistant calls per person per minute, 429 beyond. Settings `upload_max_per_minute`, `model_calls_max_per_minute` |
| 3 | The invoice explainer counted figures in supplier emails as facts, so an injected account number or link inside an email could appear in the explanation accounts reads | Medium | Fixed: figures from `supplier_emails` are not facts; an explanation with a link or address that no invoice data contains is refused and the rules' explanation is shown. Tested |
| 4 | `X-Request-ID` from the caller was copied unbounded into the response and the log | Low | Fixed: cut to 64 characters, letters digits and `-_.` only |
| 5 | A demo tool (`p2p.agents.demo`: `release_payment`, a write tool whose decision type is not locked) loads when `EXTRA_MODULES` names it, and the development `.env` does | Low | Accepted for development. No product agent uses it, it is off by default and absent from `.env.example`; a test fails if product code imports it. Remove the line from `.env` before any shared deployment |
| 6 | npm: `zod` below 4.6.6 can exhaust memory on oversized input (GHSA-mrhx-c7xc-m3w5, rated high). Reached only through the rule-table editor in the admin app, in the administrator's own browser | High as rated, low in use | Accepted in `docs/audit-accepted.json` until 1 Mar 2027: no server code uses it, no outside input reaches it, the only fix is a major version the editor does not support (npm's own "fix" is to go back to an old editor release) |
| 7 | npm: `uuid` below 11.1.1, buffer bounds in v3/v5/v6 with a caller-supplied buffer (GHSA-w5hq-g745-h8pq, moderate), via `exceljs` | Moderate | Below the bar; the package only uses v4 |
| 8 | Python: `pip-audit` on `backend/requirements.txt`, 168 pinned packages | none found | Pass |
| 9 | The assistant's answer may repeat free text people wrote (a request's justification, a supplier name), including instructions in it. It cannot act on them and every figure must come from a tool, but the text can mislead a reader | Low | Accepted. Same text is shown on the screens; the assistant has no write tool |
| 10 | Lists of requests, orders, suppliers and stock are readable by every signed-in person of a company (writes and invoices, payments, budgets are permissioned). The assistant mirrors this and has no invoice, payment or budget tool | Info | By design; tenant isolation is tested separately (E10) |
| 11 | Limits are counted in the API process's memory and the address is the connection's own, so a second API process doubles them and behind a proxy everyone shares an address | Low | Accepted; note for deployment (use a shared store and the proxy's forwarded address) |
| 12 | A person can be locked out of their account for 15 minutes by someone guessing their password five times | Low | Accepted (existing design); the new address limit slows a single attacker |
| 13 | The invoice explainer's text could claim that something was done ("The invoice was released", "payment has been approved") as long as it named the right cause | Low | Fixed: such a claim makes the rules' explanation be shown instead. Found by E32 |
| 14 | A printed invoice total was not checked against the lines when the buyer's GSTIN had not been read, so the draft looked clean and could be entered by itself when the gate allows | Medium | Fixed: an unchecked total is an assumption, so a person sees the draft. Found by E32 |
| 15 | A document from an unknown sender that carries a known supplier's GSTIN was read and, if clean, could be entered by itself | Medium | Fixed: it is read (the GSTIN identifies the supplier) but always waits for a person. Found by E32 |

## Secrets

`.env` and `.dev-credentials` are git-ignored. No value from `.env` and nothing shaped like a key (Groq, NVIDIA,
Anthropic, Google, AWS, GitHub, private keys) is in any file git tracks or would add. The request log records method, path,
status and time only (no headers, query or body). Error responses and the log were checked for the password sent, the
token sent and every secret setting. No API page returns a connection string.

## Re-running

```
python -m pytest tests/test_security.py
python evals/run.py --only E32
python scripts/audit_deps.py --pip-audit <path to pip-audit>   # needs the network; pip-audit is not a project dependency
```

`pip-audit` (Apache-2.0, Python Packaging Authority) was installed in a scratch environment for the audit. Set
`P2P_PIP_AUDIT` to its path to include it in E32.
