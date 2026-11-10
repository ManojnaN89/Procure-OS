# Capability matrix (Phase 0 and Phase 1)

One row per function of the plan. Status: todo, verified (works, evidence given), fixed (was missing or broken, now
works and has a test), deferred (not Phase 1, with the reason) or blocked (needs the owner, with what).
Only a row with evidence may be `verified` or `fixed`.

## Analytics & Insight

| ID | Function | How | Plan step | Status | Evidence (test, eval or screen) | Notes |
|---|---|---|---|---|---|---|
| 11.1.1 | Control tower: Requests, POs, deliveries and invoices by unit and stage | Rules | P1-21 | todo |  |  |
| 11.1.2 | Control tower: Ageing and drill-down | Rules | P1-21 | todo |  |  |
| 11.1.3 | Control tower: Home page per role showing what needs action | Platform | P1-21 | todo |  |  |
| 11.1.4 | Control tower: Alerts on stale requests, overruns and late orders | Rules | P1-23 | todo |  |  |
| 11.2.1 | KPIs: Cycle time, spend under management, savings per RFQ, touchless share, duplicates avoided, MSME on-time payment | Rules | P1-21 | todo |  |  |
| 11.4.1 | Assistant: Ask about status, pending items, suppliers and spend | AI agent | P1-22 | todo |  |  |
| 11.4.2 | Assistant: Safe actions by chat: draft a request, remind an approver | AI agent | P1-22 | todo |  |  |

## Automation & AI Control

| ID | Function | How | Plan step | Status | Evidence (test, eval or screen) | Notes |
|---|---|---|---|---|---|---|
| 12.1.1 | Agents: Four agents: intake, supplier communication, invoice exceptions, assistant | AI agent | P1-08, P1-14, P1-18 | todo |  |  |
| 12.1.2 | Agents: Agent runtime that pauses for people and resumes | Platform | P0-15 | todo |  |  |
| 12.1.3 | Agents: Viewer of every agent run, model call and cost | Platform | P0-23 | todo |  |  |
| 12.2.1 | AI steps: Single-call AI steps with code checks and fallback to a person | AI | P0-16 | todo |  |  |
| 12.2.2 | AI steps: Read PDFs, scans, Excel and Word | Platform | P0-18 | todo |  |  |
| 12.3.1 | Human confirmation: Sensitive actions wait for an allowed person | Person decides | P0-13 | todo |  |  |
| 12.4.1 | Autonomy: Level per decision type, value cap and instant off switch | Person decides | P1-24 | todo |  |  |
| 12.5.1 | AI quality: Model gateway and cost log | AI | P0-14 | todo |  |  |

## Demand & Requisition

| ID | Function | How | Plan step | Status | Evidence (test, eval or screen) | Notes |
|---|---|---|---|---|---|---|
| 1.1.1 | Request intake: Request by form | Platform | P1-04 | todo |  |  |
| 1.1.2 | Request intake: Request by email or free text, turned into a draft | AI agent | P1-08 | todo |  |  |
| 1.1.3 | Request intake: Request by chat | AI agent | P1-08 | todo |  |  |
| 1.1.4 | Request intake: Ask the requester for missing details | AI agent | P1-08 | todo |  |  |
| 1.2.1 | Items & catalogue: Item master: category, unit, tax code | Platform | P1-01 | todo |  |  |
| 1.2.2 | Items & catalogue: Catalogue and contract items | Platform | P1-01, P1-04 | todo |  |  |
| 1.2.3 | Items & catalogue: Match free-text items to the item master | AI | P1-08 | todo |  |  |
| 1.2.4 | Items & catalogue: Default GL account and cost centre by category | Rules | P1-01 | todo |  |  |
| 1.3.1 | Pre-order checks: Stock and open-order check | Rules | P1-02, P1-05 | todo |  |  |
| 1.3.2 | Pre-order checks: Duplicate request check | Rules | P1-05 | todo |  |  |
| 1.3.3 | Pre-order checks: Suggested net quantity | Rules | P1-05 | todo |  |  |
| 1.3.4 | Pre-order checks: Stock movements from receipts, issues and adjustments | Rules | P1-02, P1-15 | todo |  |  |
| 1.4.1 | Request lifecycle: Draft, submit, withdraw, cancel, copy a previous request | Platform | P1-04 | todo |  |  |
| 1.4.2 | Request lifecycle: Status tracking for requesters | Platform | P1-04 | todo |  |  |
| 1.4.3 | Request lifecycle: In-app and email notifications | Platform | P0-12 | todo |  |  |

## India Compliance

| ID | Function | How | Plan step | Status | Evidence (test, eval or screen) | Notes |
|---|---|---|---|---|---|---|
| 9.1.1 | GST: HSN/SAC codes, GST rate table and state-based tax split | Rules | P0-11, P1-13 | todo |  |  |
| 9.1.2 | GST: Composition, unregistered and reverse-charge cases | Rules | P1-01, P1-13 | todo |  |  |
| 9.2.1 | TDS: Rate and threshold rules | Rules | P1-19 | todo |  |  |
| 9.3.1 | MSME: MSME payment deadline tracking | Rules | P1-19 | todo |  |  |
| 9.4.1 | E-invoice: IRN and QR verification | Rules | P1-16 | todo |  |  |

## Integration & Data

| ID | Function | How | Plan step | Status | Evidence (test, eval or screen) | Notes |
|---|---|---|---|---|---|---|
| 13.1.1 | Data import and export: Excel import with a dry run; export of any list | Platform | P1-01 | todo |  |  |
| 13.1.2 | Data import and export: Reference data: HSN/SAC, GST states, units, holidays | Platform | P0-21 | todo |  |  |
| 13.2.1 | Email: Email in and out, linked to cases | Platform | P0-19 | todo |  |  |

## Invoice & Matching

| ID | Function | How | Plan step | Status | Evidence (test, eval or screen) | Notes |
|---|---|---|---|---|---|---|
| 6.1.1 | Invoice capture: Invoice by email or upload | Platform | P1-16 | todo |  |  |
| 6.1.2 | Invoice capture: E-invoice QR code read in code | Rules | P1-16 | todo |  |  |
| 6.1.3 | Invoice capture: Read other invoices | AI | P1-16 | todo |  |  |
| 6.1.4 | Invoice capture: Credit notes accepted and netted against payments | Rules | P1-16, P1-17, P1-19 | todo |  |  |
| 6.2.1 | Validation: GSTIN matches the supplier record | Rules | P1-16 | todo |  |  |
| 6.2.2 | Validation: GST arithmetic and totals | Rules | P1-16 | todo |  |  |
| 6.2.3 | Validation: Duplicate invoice check | Rules | P1-16 | todo |  |  |
| 6.2.4 | Validation: Every value read by AI must exist in the document | Rules | P0-16 | todo |  |  |
| 6.3.1 | Matching: Three-way match: PO, goods received, invoice | Rules | P1-17 | todo |  |  |
| 6.3.2 | Matching: Price and quantity tolerances set by rules | Rules | P1-17 | todo |  |  |
| 6.4.1 | Exceptions: Matching exceptions screen | Platform | P1-17 | todo |  |  |
| 6.4.2 | Exceptions: Find the cause of a mismatch | AI agent | P1-18 | todo |  |  |
| 6.4.3 | Exceptions: Propose a fix and draft the supplier query | AI agent | P1-18 | todo |  |  |
| 6.4.4 | Exceptions: Request a credit note | AI agent | P1-18 | todo |  |  |
| 6.4.5 | Exceptions: Person releases the payment block | Person decides | P0-13, P1-18 | todo |  |  |

## Payments

| ID | Function | How | Plan step | Status | Evidence (test, eval or screen) | Notes |
|---|---|---|---|---|---|---|
| 7.1.1 | Due date: Due date from payment terms | Rules | P1-19 | todo |  |  |
| 7.1.2 | Due date: MSME payment-day rule: agreed days up to 45, otherwise 15 days | Rules | P1-19 | todo |  |  |
| 7.2.1 | Tax at payment: TDS calculation | Rules | P1-19 | todo |  |  |
| 7.3.1 | Payment run: Propose the payment run by due date, MSME first | Rules | P1-19 | todo |  |  |
| 7.3.2 | Payment run: Finance approves the payment batch | Person decides | P0-13, P1-19 | todo |  |  |
| 7.3.3 | Payment run: Bank file and bank statement import | Rules | P1-19 | todo |  |  |
| 7.3.4 | Payment run: Remittance advice to the supplier | Platform | P1-20 | todo |  |  |
| 7.3.5 | Payment run: Close the case with full history | Rules | P1-20 | todo |  |  |

## Platform & Administration

| ID | Function | How | Plan step | Status | Evidence (test, eval or screen) | Notes |
|---|---|---|---|---|---|---|
| 14.1.1 | Client separation: Separate secure space for each client | Platform | P0-05, P0-06 | todo |  |  |
| 14.2.1 | Users and security: Sign-in and roles | Platform | P0-07, P0-08 | todo |  |  |
| 14.2.2 | Users and security: Security testing and hardening | Platform | P1-26 | todo |  |  |
| 14.3.1 | Rules and flow: Business rules in tables an admin can change | Rules | P0-10, P0-11 | todo |  |  |
| 14.3.2 | Rules and flow: Case flow, tasks and timers | Platform | P0-12 | todo |  |  |
| 14.4.1 | Audit and storage: History of every action | Platform | P0-09 | todo |  |  |
| 14.4.2 | Audit and storage: File storage per client | Platform | P0-17 | todo |  |  |
| 14.5.1 | Web app: Web app shell and admin pages | Platform | P0-20 | todo |  |  |
| 14.6.1 | Demo and test data: Demo company with a year of history | Platform | P0-21 | todo |  |  |
| 14.6.2 | Demo and test data: Test documents and cases with answer keys | Platform | P1-03 | todo |  |  |

## Purchase Orders

| ID | Function | How | Plan step | Status | Evidence (test, eval or screen) | Notes |
|---|---|---|---|---|---|---|
| 4.1.1 | PO creation: Group lines by supplier, ship-to and terms | Rules | P1-13 | todo |  |  |
| 4.1.2 | PO creation: Price taken from the contract or the award | Rules | P1-13 | todo |  |  |
| 4.1.3 | PO creation: GST split: CGST+SGST or IGST | Rules | P1-13 | todo |  |  |
| 4.1.4 | PO creation: PO numbering and versions | Rules | P1-13 | todo |  |  |
| 4.1.5 | PO creation: PO document (PDF) | Platform | P1-13 | todo |  |  |
| 4.2.1 | PO approval & release: Re-check budget and approval limit on the final amount | Rules | P1-13 | todo |  |  |
| 4.2.2 | PO approval & release: Approval through the approval matrix | Person decides | P1-13, P1-07 | todo |  |  |
| 4.2.3 | PO approval & release: Person confirms sending; PO emailed to the supplier | Person decides | P0-13, P1-13 | todo |  |  |
| 4.3.1 | Supplier confirmation: Record the supplier's acknowledgement | AI agent | P1-14 | todo |  |  |
| 4.3.2 | Supplier confirmation: Accept small changes within agreed limits | AI agent | P1-14 | todo |  |  |
| 4.3.3 | Supplier confirmation: Chase when there is no reply or the date slips | AI agent | P1-14 | todo |  |  |
| 4.4.1 | PO changes: Cancel or close a PO | Rules | P1-13, P1-20 | todo |  |  |

## Receiving

| ID | Function | How | Plan step | Status | Evidence (test, eval or screen) | Notes |
|---|---|---|---|---|---|---|
| 5.1.1 | Goods receipt: Record a full delivery | Rules | P1-15 | todo |  |  |
| 5.1.2 | Goods receipt: Part delivery keeps the balance open | Rules | P1-15 | todo |  |  |
| 5.1.3 | Goods receipt: Over and under delivery tolerance | Rules | P1-15 | todo |  |  |
| 5.1.4 | Goods receipt: Rejected or damaged quantity | Rules | P1-15 | todo |  |  |
| 5.1.5 | Goods receipt: Tell the requester what arrived | Platform | P1-15 | todo |  |  |
| 5.3.1 | Short or bad goods: Chase the supplier for the balance of a part delivery | AI agent | P1-14 | todo |  |  |

## Sourcing

| ID | Function | How | Plan step | Status | Evidence (test, eval or screen) | Notes |
|---|---|---|---|---|---|---|
| 3.1.1 | Source determination: Use a valid contract or price record, otherwise raise an RFQ | Rules | P1-09 | todo |  |  |
| 3.1.2 | Source determination: Skip blocked suppliers, expired documents, unapproved categories | Rules | P1-09 | todo |  |  |
| 3.2.1 | RFQ (request for quotation): Create an RFQ from approved request lines | Rules | P1-10 | todo |  |  |
| 3.2.2 | RFQ (request for quotation): Select approved suppliers for the category | Rules | P1-10 | todo |  |  |
| 3.2.4 | RFQ (request for quotation): Prepare the RFQ text from an editable template | Rules | P1-10 | todo |  |  |
| 3.2.5 | RFQ (request for quotation): Person approves before sending | Person decides | P0-13, P1-10 | todo |  |  |
| 3.2.6 | RFQ (request for quotation): Send by email and track replies | Rules | P0-19, P1-10 | todo |  |  |
| 3.2.7 | RFQ (request for quotation): Answer supplier questions and send reminders | AI agent | P1-14 | todo |  |  |
| 3.3.1 | Quote capture: Read quotes from email: PDF, Excel, Word | AI | P1-11 | todo |  |  |
| 3.3.2 | Quote capture: Manual quote entry | Platform | P1-10 | todo |  |  |
| 3.4.1 | Evaluation & award: Landed cost: price, freight, tax | Rules | P1-12 | todo |  |  |
| 3.4.2 | Evaluation & award: Compare with last price and other quotes | Rules | P1-12 | todo |  |  |
| 3.4.3 | Evaluation & award: Weighted score with knock-out rules | Rules | P1-12 | todo |  |  |
| 3.4.4 | Evaluation & award: Recommendation with reasons from the score breakdown | Rules | P1-12 | todo |  |  |
| 3.4.5 | Evaluation & award: Person chooses the supplier | Person decides | P1-12 | todo |  |  |
| 3.5.1 | Contracts & price records: Price records and validity dates | Rules | P1-01, P1-09 | todo |  |  |
| 3.5.2 | Contracts & price records: Contract expiry alerts | Rules | P1-23 | todo |  |  |

## Spend Control

| ID | Function | How | Plan step | Status | Evidence (test, eval or screen) | Notes |
|---|---|---|---|---|---|---|
| 2.1.1 | Budget: Budgets by cost centre and GL account | Rules | P1-06 | todo |  |  |
| 2.1.2 | Budget: Reserve on approval, move to PO, actual on invoice | Rules | P1-06 | todo |  |  |
| 2.1.3 | Budget: Release on cancel | Rules | P1-06 | todo |  |  |
| 2.1.4 | Budget: Warn or block when over budget, set per client | Rules | P1-06 | todo |  |  |
| 2.2.1 | Approval workflow: Approval route by value, category and department | Rules | P0-10, P1-07 | todo |  |  |
| 2.2.2 | Approval workflow: Approver inbox: approve, edit, reject, return with comment | Person decides | P1-07 | todo |  |  |
| 2.2.3 | Approval workflow: Approve from email | Person decides | P1-07 | todo |  |  |
| 2.2.4 | Approval workflow: Delegation with dates | Rules | P1-07 | todo |  |  |
| 2.2.5 | Approval workflow: Reminders and escalation | Rules | P0-12, P1-07 | todo |  |  |
| 2.2.6 | Approval workflow: Separation of duties (no one approves their own request) | Rules | P0-08 | todo |  |  |
| 2.3.1 | Auto-approval: Small in-policy requests approved by the system | Rules | P1-07 | todo |  |  |
| 2.3.2 | Auto-approval: Reason written down for every auto-approval (the rule that matched) | Rules | P1-07 | todo |  |  |

## Supplier Management

| ID | Function | How | Plan step | Status | Evidence (test, eval or screen) | Notes |
|---|---|---|---|---|---|---|
| 8.1.1 | Supplier records: Create and maintain supplier records | Platform | P1-01 | todo |  |  |
| 8.1.2 | Supplier records: GSTIN, PAN, GST registration type, MSME category and bank checks | Rules | P1-01 | todo |  |  |
| 8.1.3 | Supplier records: Payment terms master | Platform | P1-01 | todo |  |  |
| 8.1.4 | Supplier records: Activate, block and unblock a supplier | Person decides | P0-13, P1-01 | todo |  |  |
| 8.2.1 | Onboarding: Onboarding form with document upload | Platform | P1-01 | todo |  |  |
| 8.3.2 | Qualification & scorecards: Document expiry alerts | Rules | P1-23 | todo |  |  |
| 8.4.1 | Communication: Every supplier email linked to its RFQ or PO | Platform | P0-19 | todo |  |  |

## Plan steps (for reference)

- P0-01: Prepare the machine: Free 40-50 GB on C:. Confirm Docker Desktop, Python 3.12, Node 24, Git. Create free API keys (Groq and Gemini) and keep them in backend/.en
- P0-02: Create the repository: Folders: backend/ (FastAPI app with one package per area: masters, requests, approvals, sourcing, orders, receiving, invoices, payments, 
- P0-03: Docker Compose for services: Services: PostgreSQL (enable pg_trgm for text similarity) and Mailpit (test mailbox). App code runs natively. Named volumes, health
- P0-04: Write CLAUDE.md for the repo: Rules for Claude Code: package boundaries; money is always Decimal; an AI model never computes a number a decision uses; every wri
- P0-05: Control database and tenant provisioning: SQLAlchemy 2 + Alembic. Control DB tables: tenant, tenant_database (connection string encrypted with a key from env), 
- P0-06: Per-request tenant routing: FastAPI dependency resolves the tenant from the login token, fetches its connection, keeps a small cache of engines, gives the reque
- P0-07: Login and users: Own minimal auth: argon2 password hashes, short-lived JWT access token with tenant and role claims, refresh token, lockout after repeated failu
- P0-08: Roles, limits and segregation of duties: Roles: Requester, Approver, Buyer, Store, AP, Finance, Admin, Supplier (later). Permission check functions by role + un
- P0-09: Tamper-proof audit log: Table audit_event: actor type (user, agent, service, rule), actor id, action, entity, before and after JSON, reason, rule version, model
- P0-10: Rules engine with editable decision tables: GoRules ZEN engine (Python package) evaluates JDM decision tables stored in rule_table (key, version, status). Every
- P0-11: Formula library: Reference-data loader first (HSN/SAC codes, a versioned GST rate table seeded from the GST rate notifications, GST state codes, units of measur
- P0-12: Case flow engine and human tasks: Deterministic state machine, one case per purchase: request, approval, sourcing, order, supplier, receipt, invoice, payment, c
- P0-13: Human confirmation mechanism: A 'proposal' record for any sensitive action: action, payload, who or what proposed it, risk, status (pending, approved, edited, r
- P0-14: Model gateway: One interface: complete and extract(schema). Provider adapters: OpenAI-compatible (Groq, Gemini, OpenRouter, local Ollama) and Anthropic. Model c
- P0-15: Agent runtime: LangGraph (Python) with Postgres checkpoints. Tool registry: each tool is a service function with name, description and JSON-schema parameters (s
- P0-16: AI step runner: Shared wrapper for single-call AI steps: render prompt, call gateway with schema, validate with Pydantic, evidence check (FR38: every extracted 
- P0-17: File storage: Files on local disk in a per-tenant folder behind a small storage interface (put, get, delete); download only through an authenticated endpoint; d
- P0-18: Document parsing: Docling with RapidOCR (or Tesseract) wrapped as a backend function: PDF, scans, Excel, Word into text and tables with page positions. Original
- P0-19: Email in and out: Send with smtplib (Mailpit locally). Read mail behind a mailbox interface: from Mailpit's REST API in development (Mailpit has no IMAP) and th
- P0-20: Web shell and admin pages: React + Vite + TypeScript, shadcn/ui, TanStack Router and Query. Login, tenant name, navigation by role, light theme, Indian number f
- P0-21: Data generator v0: Generator (datagen/) with seed and version, grown with each P1 schema step: org units, departments, cost centres, GL accounts, users with rol
- P0-22: Scenario test harness: pytest tests named after scenarios, with given, when and then in the test body. The first 15 describe SAP and Dynamics 365 behaviour we c
- P0-23: Logs and run viewer: Structured logs. Admin viewer of agent runs (steps, tool calls, waits), model calls (tokens, cost) and rule evaluations.
- P1-01: Masters, thin: Org units and locations, departments, cost centres, GL accounts. Items: goods or service, category, UoM and conversions, pack size and MOQ, HSN o
- P1-02: Stock and open supply: stock_balance and movements: receipts are posted by the goods receipt step; issues and adjustments are imported or entered by stores. Inv
- P1-03: Documents generator v1: Render quotes (PDF, Excel, Word, email text) in several layouts, GST invoices with QR and deliberate errors, supplier replies of each ki
- P1-04: Request screens: Create a request from catalogue search or free fields: lines with quantity, UoM, need date, ship-to, cost centre, GL account, justification, at
- P1-05: Check before ordering: Show stock, open requests, open PO balance and inbound for each line and the suggested net quantity (FR01); override needs a reason. Dupl
- P1-06: Budget: Budgets by cost centre and GL account per period. Commit on approval, move to PO, actual on invoice, release on cancel (FR27). Policy per tenant: warn o
- P1-07: Approvals: Approval matrix as a ZEN decision table (FR28): value band, category, unit, department, spend type to ordered approver roles. Delegation with dates, 
- P1-08: Slice B: Intake agent (AG01): Implement AG01 from the Agents sheet: plain-language request by chat or email becomes a draft request (free-text items are matched
- P1-09: Source determination: FR11: valid contract, then approved price record, else RFQ. Skips blocked vendors, expired vendor documents, unapproved categories. Quota 
- P1-10: RFQ, thin: Create RFQ from request lines; pick approved vendors for the category; the text comes from an editable template; proposal for a person to approve bef
- P1-11: Slice B: Quote reading: AI step S01 reads PDF, scan, Excel, Word and email-body quotes into quote lines; review screen shows document and fields side by side; a
- P1-12: Comparison and award: Landed cost (FR13), simple benchmark vs last price and other quotes (FR15), weighted score with knock-outs (FR16). Comparison screen with 
- P1-13: Purchase order: Group lines by vendor, ship-to, currency and terms (FR43); price from contract or award (FR10); GST split (FR25); terms from template; PO number
- P1-14: Slice B: Supplier communication agent (AG02): Implement AG02: reads supplier emails (quotes, acknowledgements, change requests, questions), classifies (AI step 
- P1-15: Goods receipt: Receipt against PO lines: partial, over and under tolerance (FR20), damaged quantity, close short. Open balance stays as supply. A stock movement
- P1-16: Invoice intake: Invoice by email or upload (manual entry works without AI). Credit notes are accepted as negative invoices linked to the original invoice or the
- P1-17: Three-way match and exceptions screen: FR21: per line, invoiced quantity within accepted received quantity minus already invoiced; price within tolerance of PO;
- P1-18: Slice B: Invoice exceptions agent (AG03): Implement AG03: investigates why an invoice is blocked using order, receipts and supplier emails; explains in plain wo
- P1-19: Payment planning: Due date from payment terms with the MSME rule (FR23); TDS computed when the invoice is matched and booked, at the earlier of booking and paym
- P1-20: Case closing: On payment: budget actuals, close request and PO lines, remittance advice emailed to the supplier, notify the requester, complete the case history
- P1-21: Role workspaces and control tower: Home per role showing only items needing action. Control tower by unit and stage with ageing buckets (FR46) and drill-down. K
- P1-22: Slice B: Assistant agent (AG04): Implement AG04: chat panel answers from live data (status, what is pending with me, why this vendor, spend) with read-only tool
- P1-23: Alerts: Rule checks (flagged price, stale requests, budget overrun, contract expiry, vendor document expiry, late PO) create notifications through the core buil
- P1-24: Automation controls: Per decision type: level (recommend, act with approval, act within limits), value cap, confidence threshold, kill switch, change history. P
- P1-25: Demo scenarios: Three scripted end-to-end runs in Playwright: (1) normal run request to paid, (2) part delivery, (3) price mismatch on invoice. Each is run with
- P1-26: Security pass: Prompt-injection test documents (agents ignore embedded instructions), tool allow-list per agent, secrets only in env, rate limits, dependency au
- P1-27: Short guides: One page per role in plain language; admin page on rules and automation controls.
