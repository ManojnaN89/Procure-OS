# ProcureOS

**Procurement intelligence you can talk to.**

ProcureOS is a conversational procurement platform. You type (or say) what you need, such as *"compare the quotes on RFQ 2"* or *"approve requisition 4"*, and an AI agent carries it out against a real database. In the background, ProcureOS watches for problems on its own (overpriced quotes, risky vendors, stuck approvals, budget overruns, expiring contracts) and raises them before you ask. Every action the AI takes is logged in an **Agent Trail**, so you can always see exactly what it did and why.

---

## Contents

- [How it works](#how-it-works)
- [Features](#features)
- [The five agents](#the-five-agents)
- [Procurement intelligence](#procurement-intelligence)
- [Tech stack](#tech-stack)
- [Installation](#installation)
- [Try it](#try-it)
- [Project structure](#project-structure)
- [Configuration](#configuration)
- [Known limitations](#known-limitations)

---

## How it works

```
 You (Focus Mode, Assistant, dashboard pages, voice, file upload)
   │
   ▼
 FastAPI backend ── POST /api/chat
   │
   ▼
 Orchestrator (router) ── gpt-oss-120b on Groq picks one agent
   │                     (Review buttons name the agent directly)
   ▼
 Auction · RFQ · Requisition · Source-to-Pay · Insights agents
   │
   ▼
 51 skills (Python tools) ──► PostgreSQL
   │
   └──► Agent Trail (WebSocket) ── every tool call shown live + stored for audit

 Background loop, no one has to ask:
 Scheduler (every 2 min) ─► 5 rule checks ─► Notifications ─► "Needs attention" + bell
```

1. A message goes to the **orchestrator**, which uses the LLM to decide which specialist agent should handle it.
2. That **agent** runs a tool-calling loop: it picks one of its own skills, the skill reads or writes the database, and the result goes back to the model, which repeats until it can give a final answer.
3. Each agent can only use its own skills. It can't invent an action that doesn't exist, and its instructions tell it to say so plainly when something is out of scope.
4. Every skill call is streamed to the **Agent Trail** and saved to the database.
5. Separately, a **scheduler** runs rule checks every few minutes and pushes alerts to every open browser over a WebSocket.

---

## Features

### Conversational actions
- Plain-English requests that perform **real actions**, not just answers.
- **Focus Mode**: a full-screen, distraction-free conversation view, with a morning briefing at launch that covers what needs attention and a spend snapshot. Press `Esc` to exit.
- **Assistant** available as its own page and as a floating panel on every page, sharing one conversation.
- **Suggested next steps** after each reply, limited to actions the platform can actually perform.
- **Inline charts, progress bars and expandable lists** returned by analytics skills.
- **File upload**: attach a quote (PDF, Excel) or a vendor list (CSV). The text is extracted, then the agent reads it and fills in the right action, for example recording a quote or importing vendors.
- **Voice**: push-to-talk speech-to-text (faster-whisper) and spoken replies (Kokoro).

### Proactive monitoring
- A background scheduler checks for problems every 2 minutes, with no prompt needed.
- Alerts appear in a **Needs attention** panel and a notification bell, with notification history.
- Clicking **Review** on an alert in Focus Mode opens a conversation about that exact record, handled by the agent that owns it.

### Dashboard and records
- Dashboard with total spend, savings, spend under management, requisition status and spend by category.
- Pages for **Auctions, RFQs, Requisitions, Vendors, Purchase Orders, Payments and Contracts**, each with list and detail views.
- Global search across records.
- A **Guide me** help page.

### Trust and audit
- **Agent Trail**: a live drawer plus a full, filterable, exportable history of every action any agent has taken.
- Any button that changes data (Approve, Process payment, and so on) goes through the same agent path as a typed message, so the trail is always complete.

### Configuration
- A **Settings** page for every threshold: price-flag %, stale-requisition days, contract-expiry window, check interval, risk-score cutoffs, currency (INR, USD, EUR, GBP), and whether to launch in Focus Mode.

---

## The five agents

| Agent | Tools | What it can do |
|---|---|---|
| **Auction** | 7 | Start forward or reverse auctions, record bids, close and pick the winner, cancel, list and view |
| **RFQ** | 8 | Draft RFQs, record quotes (with an automatic price check), compare quotes, find vendors by name, cancel, list and view |
| **Requisition** | 8 | Raise requests, approve or reject, adjust, cancel, convert to a purchase order, list and view |
| **Source-to-Pay** | 20 | Onboard vendors and import them from CSV; verify, flag, deactivate or reactivate vendors; create, adjust and cancel POs; process payments; store, renew and end contracts; adjust budgets |
| **Insights** | 24 | Spend trends, category breakdowns, budget vs actual, vendor comparisons, vendor risk, pending bills, what needs attention, recent activity. **Read-only**: never changes data |

Every agent can also dismiss or resolve a notification. Skills are grouped in `backend/skills/<domain>/`, and each folder has a `SKILL.md` describing its tools.

---

## Procurement intelligence

The **detection** is done with clear, checkable rules. The **AI** explains what the rules found and acts on it.

| Check | How it works | Default |
|---|---|---|
| **Price flags** | A new quote or PO is compared with the average of all quotes and POs in its category | Flag if more than **30%** above |
| **Vendor risk score** | A 0–100 score from the vendor's own history: late-delivery rate (up to 50 points), price volatility across quotes (up to 30), flagged incidents (10 each, capped at 3) | Medium ≥ 25, High ≥ 55 |
| **Stuck requisitions** | Requisitions still pending after a set number of days | **3** days |
| **Budget overruns** | This month's PO spend vs budget, per category | Any overrun |
| **Expiring contracts** | Contracts ending within a set window | **30** days |

Risk scores use only ProcureOS's own order and quote data, not third-party risk feeds. Alerts clear themselves when the underlying problem is fixed, for example when a requisition is decided or a contract is renewed. The exception is price flags, which stay until someone dismisses them.

---

## Tech stack

| Layer | Technology |
|---|---|
| LLM | `openai/gpt-oss-120b` served by **Groq** (configurable via `MODEL_NAME`) |
| Backend | Python, **FastAPI**, Uvicorn, SQLAlchemy, APScheduler |
| Database | **PostgreSQL** |
| Real-time | WebSockets (Agent Trail, notifications), Server-Sent Events (chat replies) |
| Files | pdfplumber (PDF), openpyxl (Excel), csv |
| Voice | faster-whisper (speech-to-text), Kokoro (text-to-speech), openWakeWord |
| Frontend | Plain HTML, CSS and JavaScript single-page app with hash routing, no build step |

---

## Installation

### Prerequisites
- **Python 3.10+** (developed on 3.12)
- **PostgreSQL** running locally
- A **Groq API key**: <https://console.groq.com>
- An internet connection. The frontend loads fonts and icons from a CDN, and the voice models download from Hugging Face the first time they're used.

### 1. Clone the repo
```bash
git clone https://github.com/ManojnaN89/Procure-OS.git
cd Procure-OS
```

### 2. Create the database
```bash
psql -U postgres -h localhost -c "CREATE DATABASE procureos;"
```

### 3. Set up the backend
```bash
cd backend
python -m venv venv
```
Activate the virtual environment:
```bash
# Windows (Command Prompt)
venv\Scripts\activate

# macOS / Linux
source venv/bin/activate
```
Install dependencies:
```bash
pip install -r requirements.txt
```

### 4. Add your settings
Copy the example env file:
```bash
# Windows
copy .env.example .env

# macOS / Linux
cp .env.example .env
```
Then edit `backend/.env`:
```env
GROQ_API_KEY=your_groq_api_key_here
DATABASE_URL=postgresql://postgres:YOUR_PASSWORD@localhost:5432/procureos
MODEL_NAME=openai/gpt-oss-120b
```
> `.env` holds your API key and is excluded by `.gitignore`. Never commit it.

### 5. Start the server
From the `backend` folder:
```bash
uvicorn app:app --reload --port 8000
```
Tables are created automatically on first start.

### 6. Load the demo data (recommended)
In a second terminal, in the `backend` folder with the virtual environment activated:
```bash
python seed.py
```
This loads 6 months of realistic history: 12 vendors, 15 RFQs, 37 quotes (one deliberately flagged), 24 requisitions, 23 purchase orders, budgets, an open auction and 2 contracts.

> `seed.py` doesn't check for existing data. To reset, drop and recreate the database, start the server once, then run `seed.py` again:
> ```bash
> psql -U postgres -h localhost -c "DROP DATABASE procureos;"
> psql -U postgres -h localhost -c "CREATE DATABASE procureos;"
> ```

### 7. Open the app
Go to **<http://localhost:8000>**. The backend serves the frontend, so there's nothing else to run.

---

## Try it

Open Focus Mode and try these in order. See [`procureos-demo-prompts.md`](procureos-demo-prompts.md) for the expected results.

1. Attach `sample-files/vertex-electronics-quote.pdf` and type: **"Record this quote from Vertex Electronics (vendor 6) on RFQ #2."** It gets flagged automatically.
2. **"Compare all quotes on RFQ #2 and recommend which vendor I should go with."**
3. **"Which of my vendors are the riskiest right now, and why?"**
4. **"Approve requisition #2 and convert it to a purchase order with Summit Electronics (vendor 11) for monitors. Tell me if anything gets flagged."**
5. **"Why has my spending gone up, and are any categories over budget?"**

More scenarios, covering vendor lifecycle, auctions, contracts and monitoring, are in [`procureos-full-demo-guide.md`](procureos-full-demo-guide.md) and [`test-cases`](test-cases). Sample files for upload are in [`sample-files/`](sample-files).

---

## Project structure

```
Procure-OS/
├── backend/
│   ├── app.py              # FastAPI app: /api/chat (SSE), WebSockets, serves the frontend
│   ├── orchestrator.py     # Routes each message to an agent; proposes next-step suggestions
│   ├── agents/             # The 5 domain agents + the shared tool-calling loop (base_agent.py)
│   ├── skills/             # 51 skills grouped by domain, each folder with a SKILL.md
│   │   ├── auctions/  rfq/  requisition/  source_to_pay/  insights/  shared/
│   ├── intelligence.py     # Price-flag and vendor-risk rules
│   ├── signals.py          # The 5 background checks that create notifications
│   ├── scheduler.py        # Runs the checks on an interval
│   ├── notifications.py    # Notification WebSocket broadcaster
│   ├── trail.py            # Agent Trail WebSocket broadcaster
│   ├── reads.py            # Read-only API: lists, details, search, dashboard, settings, briefing
│   ├── settings.py         # Configurable thresholds with defaults
│   ├── upload.py           # PDF / Excel / CSV text extraction
│   ├── voice.py            # Speech-to-text, text-to-speech, wake-word endpoints
│   ├── models.py  db.py  config.py  groq_client.py  skill_loader.py
│   ├── seed.py             # Demo data loader
│   ├── requirements.txt
│   └── .env.example
├── frontend/
│   ├── index.html          # Single-page shell
│   ├── css/                # Design tokens, layout, components, motion, Focus Mode
│   └── js/                 # API client, shared state, assistant, Focus Mode, notifications,
│       └── views/          #   trail, voice, search, router — plus one module per page
├── sample-files/           # Sample quote PDF, quote Excel, vendor CSV
├── procureos-demo-prompts.md
├── procureos-full-demo-guide.md
└── test-cases
```

---

## Configuration

| Setting | Where | Default |
|---|---|---|
| `GROQ_API_KEY` | `backend/.env` | (required) |
| `DATABASE_URL` | `backend/.env` | `postgresql://postgres:123@localhost:5432/procureos` |
| `MODEL_NAME` | `backend/.env` | `openai/gpt-oss-120b` |
| Price-flag threshold | Settings page | 30% |
| Stale requisition | Settings page | 3 days |
| Contract expiry warning | Settings page | 30 days |
| Signal check interval | Settings page | 2 minutes |
| Risk cutoffs (medium / high) | Settings page | 25 / 55 |
| Currency | Settings page | INR |
| Launch in Focus Mode | Settings page | On |

---

## Known limitations

**Not built yet** (compared with a full source-to-pay platform):
- Goods receipt (POs store only a delivery date) and invoices, so there's no 3-way match yet.
- Product catalogs.
- A vendor portal. Vendors don't log in; their quotes and bids are entered for them.
- User accounts and roles. There's no authentication, so add it before deploying anywhere shared.

**Partly built:**
- Contracts are stored as records (terms, value, dates) with renew and terminate. There's no contract drafting or clause checking.
- Payments are made per PO, without an invoice behind them.

**Technical:**
- Chat history is kept in server memory per session and resets when the server restarts. Database records persist normally.
- Typed messages are routed by the LLM, which can occasionally pick the wrong agent. Review clicks bypass this by naming the agent directly.
- Chat replies are sent in chunks after the agent finishes, not streamed token by token.
- The schema is created with `create_all()`, not migrations.
