# Phase 0 acceptance pass (6 Nov 2026)

Phase 0 was built and tested step by step. This pass ran it as a real environment for the first time: dev database set up from the command line, everything started with one command, a person logging in as each role, a real model driving an agent, the worker running on its own, and speed measured.

## What was checked and the result

| Check | Result |
|---|---|
| Set up from the command line: `control migrate`, `tenant create`, `user create` (7 roles), `tenant migrate-all` | Works. Migrate 2.8 s, create a company with all reference data 4.0 s. Creating the same company twice is refused clearly |
| One-command start (`python scripts/dev.py`): Postgres, Mailpit, API, web app, worker | Works. The worker was missing from it; now included |
| Browser walk as each of the 7 roles, laptop and phone for admin, finance and requester (10 combinations, 22 pages) | All clean: no page errors, no console errors, no failed requests, no sideways scroll. Menu follows the role |
| Real agent run (Groq gpt-oss-120b chooses tools): look up invoice, release payment | The autonomy gate turned the payment into a proposal; the run paused (1.3 s). Finance approved it in the browser; a fresh process resumed the run; the payment ran as the approver (`user:2`), not the agent. Steps, proposal and audit rows all recorded |
| Worker running on its own | A supplier email to the RFQ's reply address was stored, linked to the RFQ and added to the case within 3 s. A task escalated to the next person exactly at its due time (36 s). Three notification emails went out. The mailbox poll rescheduled itself |
| Wrong password | 401, same answer as an unknown email |

## Problems found and fixed

1. **Slow under load.** Every request opened and closed its own control-database engine, once or twice. One request took 63 ms, but 20 users at once got 2.1 s each and only 9 requests a second. Now one shared pooled engine: `/me` 63 ms to 10 ms, 20 at once 2.1 s to 0.13 s, throughput 9 to 130 requests a second.
2. **`localhost` is slow on this machine** (0.2 s per new connection, about 2 s from Python clients, because IPv6 is tried first). Defaults now use `127.0.0.1` for the database, mail and the dev proxy. Logins that took 2.2 s now take under 0.2 s.
3. **React warning on the Audit page** (missing row key). Fixed.
4. **Missing page:** rule decisions (what went into a decision table, what came out, which version, and why). Added, with a trace of the matching row.
5. **Dev auto-reload does not pick up new modules here.** A new server process is needed after changing `.env` or adding a module; stop and start `dev.py`.
6. **A test mailbox poll left running** kept ingesting every Mailpit message into the demo company. Stopped. In real use a mailbox belongs to one company.

## Speed (python scripts/measure.py, final code)

| Operation | Median | 95th percentile |
|---|---|---|
| Login (argon2 + token) | 72 ms | 194 ms |
| `/api/auth/me` | 15 ms | 18 ms |
| Audit search, 100 rows | 12 ms | 14 ms |
| Upload 1 MB / download 1 MB | 17 ms / 12 ms | 19 ms / 19 ms |
| 20 users at once, `/me` | 132 ms | 188 ms (130 requests a second) |
| Rule evaluation (table, trace, log) | 4.8 ms | 8 ms |
| Model call, Groq gpt-oss-20b, tiny prompt | 340 ms | 560 ms |

Earlier measurements: OCR of a scanned page 3.9 s (first run 87 s while models download); a text PDF a few seconds; a quote read by Groq about 1 s.

## Left as they are

- The pre-commit hook stays off until you enable it (`git config core.hooksPath .githooks`).
- Tax values are marked "for testing" until a tax adviser confirms them.
- Browser tests run in their own process because the OCR models make the main run heavy.
- Passwords of the demo logins are in the git-ignored file `.dev-credentials`.

## Reuse

`python scripts/acceptance_browser.py` repeats the browser walk, and `python scripts/measure.py` repeats the timings, against a running dev environment. Repeat both after each Phase 1 step that changes screens or services.
