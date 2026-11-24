# Handoff: blocks 9 to 13

This is for the AI coding tool (ChatGPT Codex) that builds blocks 9 to 13 of this repo. Read it after `AGENTS.md` and `CLAUDE.md`.

## 1. How the work is split

| Who | Does what |
|---|---|
| **You (the builder)** | Build each block from its spec, with unit tests. The five blocks run in parallel, one task each. Open one pull request per block. |
| **The owner's machine** | Writes the block's **evals** blind, from the spec only, without seeing your code. Then pulls your PR and runs the full check: all tests (about 2 h 20 min), all evals, both demo companies and the browser walk-throughs. Failures come back to you as a fix list. |
| **The owner** | Answers questions, and merges at the end. |

**You never write the block's evals** (the `E1xx` files in `evals/`). That is what keeps them independent. You may update an *existing* eval only when the spec says it must change, and you name the spec section when you do.

## 2. Branches and pull requests

| Block | Flow step | Spec | Your PR goes into | Migration | Evals (written by the owner's side) |
|---|---|---|---|---|---|
| 9 | goods receipt and services | `handoff/specs/block9-spec.md` | `phase1-block9` | 0160 | E144-E151 |
| 10 | invoice | `handoff/specs/block10-spec.md` | `phase1-block10` | 0170 | E152-E159 |
| 11 | match and exceptions | `handoff/specs/block11-spec.md` | `phase1-block11` | 0180 | E160-E167 |
| 12 | payment | `handoff/specs/block12-spec.md` | `phase1-block12` | 0190 | E168-E175 |
| 13 | reconcile and close | `handoff/specs/block13-spec.md` | `phase1-block13` | 0200 | E176-E183 |

### The five blocks are built in parallel
All five blocks are built at the same time, each in its own task. All five branches start from the same commit: block 8 plus this handoff. The owner's machine then merges them in order (9, 10, 11, 12, 13), connects them, writes the evals, and runs the full check. These rules make that merge possible:

1. **Migration parent.** Every block's migration has `down_revision = "0150"`, the last migration on your branch. Keep your own `revision` number (0160, 0170 and so on). The owner's machine chains them 0160 → 0170 → 0180 → 0190 → 0200 at merge.
2. **Build only your own block's parts.** `handoff/specs/blocks9-13-interfaces.md` says who owns each shared thing.
   - If your block **extends or calls** something an earlier block owns (for example, block 12 extends block 9's `recovery_claim`), don't build the earlier block's part yourself.
   - Write your code against the names in the interfaces file.
   - Mark each such place with a comment `# NEEDS block N: <what>`, so it can be connected at merge.
   - Skip or mark `xfail` any test that needs the missing part, with the same reason.
   - List every such place in the PR description.
3. **Only change what your block needs in shared files** (`platform/exceptions.py`, `invoices/service.py`, `platform/proposals.py`, `invoices/payment.py`, `ui/js/...`, `CLAUDE.md`, the demo data). Add to them; don't reorganise or reformat them, so the five changes merge cleanly.
4. **Demo data.** Add the people, roles, categories and suppliers your spec names, using its codes exactly. Don't renumber existing rows.

- **Never open a PR into `main`.** `main` does not have blocks 0-8 yet; everything reaches `main` together at the end.
- **An earlier block never calls code a later block builds.**
- The latest existing migration is `0150_supplier_confirmation_versions.py`, and the latest eval is E143. Use only your block's numbers. Numbers E184-E199 and any other migration are taken elsewhere.
- Some specs cite `planning/...` files and line numbers. Those are the owner's private plan, which is not in the repo. The spec quotes what matters; if something is missing, ask.

## 3. Building a block

1. **Read the spec end to end**, and the interfaces file.
2. **Apply every RECOMMENDED default**; don't re-ask them. For a **NEEDS OWNER** point, build the interim the spec names.
3. **Do the pre-implementation check in the spec:**
   - extend the demo data in `datagen/` first where the spec says so;
   - add the settings with their defaults;
   - update the existing tests and evals the spec lists.
4. **Build:**
   - **Reuse first.** Before writing a new service, search for one that does the job (CLAUDE.md lists where things are).
   - **Migration:** one per block, with its reserved number. Then run `scripts/gen_schema_doc.py` to regenerate `docs/database-schema.md`; a test fails if it is stale.
   - **Tests:** unit tests named after scenarios, with given, when and then in the body. Include the boundary cases the spec lists.
   - **Documents:** update the docs the spec names: `docs/data-dictionary.md`, the role guides in `docs/guides/` (50 lines each at most), `docs/demo-script.md` and the CLAUDE.md module table.
5. **Check what you can.** If your environment can't run Postgres, run at least:
   - `ruff check .` and `ruff format --check .`;
   - `mypy` (strict) from `backend/`;
   - `cd frontend && npm run build`, if you touched `frontend/`.

   If it can, also run the pytest files for your area (the full suite takes over 2 hours; leave that to the owner's machine).
6. **Open the PR** with:
   - what you built, section by section of the spec;
   - every open point and the default taken;
   - what you could not run;
   - anything in the spec that was unclear, and what you chose.

### Rules most often broken (all from CLAUDE.md)
- **Money** is `Decimal`, quantised to paise. Never write a float literal in money code.
- **Decision numbers come from code.** A model never computes one: totals, tax, TDS, due dates and match results come from `formulas/` or `rules/`.
- **Every write** goes through a service that writes the audit log.
- **Sensitive actions are proposals** a person approves: payment runs, releasing a block, accepting a price variance or excess, and bank details.
- **Segregation of duties:** the requester, approver, receiver, invoice approver and payer differ for one case.
- **Rules and rates are versioned rows.** A decision stores the version it used.
- **Dates come from the business date and calendar**, never the computer's clock. The financial year runs from April to March.
- **Words people read** are plain and short: `count(3, 'line')`, never "line(s)".
- **Test data is synthetic only.** PANs contain `0000`, phones start `+91 00000`, and emails end in `.example`.

## 4. Git

- **Author:** commit as **Nikethan CBC**.
- **No co-author or "generated by" lines** in commits or PRs (the owner's rule).
- **Messages** are plain language, in the style of `git log`.
- **Never commit** `.env`, `.dev-credentials`, `planning/`, `data/datasets/`, or eval result files a run regenerates.

## 5. When a fix list comes back

The owner's machine sends failures as a list. Each says what failed, the expected result versus the actual result, and the spec section involved.
- If the list says the code is wrong, fix the code.
- If it says the spec has a gap, the owner decides first.
- **Never change a blind eval to match your code.** Push the fixes to the same PR.

## 6. Open with the owner (don't wait on them)

These are built as data or with the spec's interim, so the owner can change them later:
- block 10 OP-13 (AP confirms every invoice read from a document);
- block 12 OP-1 (who approves a payment run);
- block 13 OP-1 (short-close limits);
- the Brightwave CFO segregation-of-duties conflict in the interfaces file.
