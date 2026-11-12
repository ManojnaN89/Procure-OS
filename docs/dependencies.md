# Dependencies and licences

Free and open-source only (CLAUDE.md rule 11). Licences were read from each package's own metadata or licence file in this
repository's environment on 11 Nov 2026. A package is added only after its licence is checked and the name is confirmed to be
the real project. `python scripts/audit_deps.py` checks known vulnerabilities (see `docs/security-review.md`).

**One thing to know:** `psycopg` is LGPL-3.0. It is used as an unmodified library (the usual and permitted way), so nothing in
this repository becomes LGPL. Everything else is MIT, BSD, Apache-2.0 or a permissive mix.

## Backend (Python, `backend/requirements.in`; exact versions in `requirements.txt`)

| Package | Licence | Why |
|---|---|---|
| fastapi, uvicorn, pydantic, pydantic-settings, python-multipart, httpx2 | MIT, BSD-3, MIT, MIT, Apache-2.0, BSD-3 | The web API, settings, forms and uploads, and outgoing HTTP (`httpx2` is the Pydantic team's maintained successor of `httpx`, github.com/pydantic/httpx2) |
| sqlalchemy, alembic, psycopg[binary] | MIT, MIT, LGPL-3.0 | Database access and migrations (PostgreSQL) |
| argon2-cffi, pyjwt, cryptography | MIT, MIT, Apache-2.0 or BSD-3 | Password hashing, login tokens, encrypting tenant connection strings |
| zen-engine | MIT (GoRules) | Decision tables for the approval rules |
| openai, anthropic | Apache-2.0 | Model clients (the gateway also talks to Groq, Gemini, NVIDIA and others through the OpenAI-compatible interface) |
| langgraph, langgraph-checkpoint-postgres, langchain-core | MIT | The agent tool loop with its state saved in Postgres |
| docling | MIT (IBM) | Reading PDFs, scans and Office files (with its OCR) |
| pypdfium2 | BSD-3 / Apache-2.0 (bundles PDFium) | The text layer of a PDF, and page images |
| zxing-cpp | Apache-2.0 | Reading the QR code of an e-invoice |
| reportlab | BSD | The purchase order PDF |
| pillow | MIT-CMU (HPND style) | Images for OCR and QR reading |
| openpyxl | MIT | Excel import, export and the quote form |
| jinja2 | BSD-3 | The editable wording templates (sandboxed) |

Development only (`requirements-dev.in`): pytest (MIT), ruff (MIT), mypy (MIT), playwright (Apache-2.0), python-docx (MIT, test documents), numpy (BSD-3, test and generator documents), PyYAML (MIT, evals).

## Admin app (`frontend/package.json`)

| Package | Licence | Why |
|---|---|---|
| react, react-dom | MIT | The interface |
| @tanstack/react-query, @tanstack/react-router | MIT | Data fetching and routing |
| @gorules/jdm-editor | MIT | The visual editor for decision tables |
| tailwind-merge, clsx, class-variance-authority | MIT, MIT, Apache-2.0 | Class names for the components |
| Build and check tools: vite, @vitejs/plugin-react, tailwindcss, @tailwindcss/vite, typescript (Apache-2.0), @biomejs/biome (MIT or Apache-2.0), @types/* | MIT | Build, styling, types, lint and format |

## Main app (`ui/vendor/`, copied in, no build step)

| Asset | Licence |
|---|---|
| Chart.js 4.4.4 | MIT |
| Tabler Icons 2.47.0 | MIT |

Text uses the system font of each device (Segoe UI, San Francisco, Roboto), so no web font is shipped.

## Services (`docker-compose.yml`)

PostgreSQL 17 (PostgreSQL licence, permissive) and Mailpit (MIT, the local test mailbox).

## To check before any shared deployment

- The model providers' own terms and the free-tier limits in `docs/model-selection.md` (the code is free; the services are not all unlimited).
- `psycopg`'s LGPL note above, if the product is ever shipped as a closed binary.
