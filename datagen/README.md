# Data generator

Generates the synthetic dataset for the P2P platform: a fictional company (CBC Private Limited) buying for 24 months,
with a hidden truth the platform never sees. Same seed and generator version give byte-identical files.

```
python -I datagen/generator/cli.py --size demo --seed 42      # 24 months: 1 Nov 2024 to 31 Oct 2026
python -I datagen/generator/cli.py --size small --seed 42     # last 3 months, for fast tests
python -I datagen/generator/validate.py data/datasets/cbc-demo-v1-seed42
```

Output goes to `data/datasets/cbc-<size>-v1-seed<seed>/` (git-ignored; regenerate it any time):

| Folder | Contents |
|---|---|
| `master/` | Organisation, users, approval rules, budgets, items, suppliers, contracts, price records, planning parameters, opening stock |
| `history/` | Requests, approvals, RFQs, quotes, awards, POs, receipts, stock movements, invoices, TDS, payments, bank lines, email metadata |
| `truth/` | Hidden: item demand patterns and stockouts, supplier behaviour profiles, every planted problem (`truth_case`). Read only by tests |
| `reference/` | Tax and calendar reference tables copied from `data/reference` |
| `manifest.json`, `splits.json`, `report.md` | Seed and counts, held-out suppliers, quality report |

## The one-plant profile (the demo company)

```
python -I datagen/generator/profile_plant.py data/datasets/cbc-demo-v1-seed42    # -> plant-demo-v1-seed42
python -I datagen/generator/profile_plant.py data/datasets/cbc-small-v1-seed42   # -> plant-small-v1-seed42
```

Cuts a generated dataset down to one plant that buys industrial spares and consumables (mechanical spares, electrical, tools,
safety gear, packaging): 137 items, 25 suppliers, 11 people in 5 departments, and only the requests, quotations, orders, receipts,
invoices and payments that are wholly about those items. Every row is a row of the source, or that row with a person's code replaced.
The approval rules use only roles that exist in the small team (no CFO): the Managing Director is the last approver and approves payment runs.
The evals use `plant-small` (set `P2P_EVAL_DATASET` to use another).

## How it is built

| File | Role |
|---|---|
| `catalog/` | Content authored once with Claude and kept as files: organisation, items, suppliers, text banks. No AI runs at generation time |
| `generator/master.py` | Combines the content banks; adds price index, contracts, price records, demand truth, planning parameters, budgets |
| `generator/streams.py`, `flow.py`, `flow2.py`, `simulate.py` | Day-by-day simulation: stock-driven and ad hoc requests, approvals, RFQs, POs, receipts, invoices, planted problems |
| `generator/finance.py` | TDS in booking order, MSME due dates, weekly payment runs, bank lines, cut-off filter, statuses, chronological numbering |
| `generator/validate.py` | Quality checks and `report.md` |
| `generator/ids.py` | GSTIN/PAN helpers. Every generated PAN has the digits 0000, which are never issued, so none can belong to a real taxpayer |

## Known simplifications (v0.2)

- Documents are not generated yet: quote and invoice layouts, our outgoing templates and the `document_file` table come later. Email rows hold metadata only (type, sender, time, links), not text.
- `match_result` is not generated: it is the platform's own output. `truth/truth_match_expected.csv` says what a correct match must conclude for every invoice.
- Advance payments exist only for the five suppliers on 30% advance terms. TDS is deducted on the advance at payment, and the invoice TDS base is reduced by the advanced amount.
- Free-text request lines are not generated; the document step adds them.
- Tax values are for testing and are marked for confirmation by a tax adviser.
- The planning parameters (reorder points) are deliberately imperfect, so stockouts and overstock exist.
- Tables that only the running platform produces (tasks, notifications, audit, agent runs, proposals) are not generated.
