# Evals report

Run on 2026-11-10 17:07; AI evals used a scripted model.

| ID | What it checks | Result | Seconds | Key figures |
|---|---|---|---|---|
| E25 | Blocked invoices are explained with the right cause, and a block is never changed by the agent | **PASS** | 139.2 | invoices entered 2639; blocked by the match 302; of those, with a cause the dataset planted 297; blocked without a planted cause (part-delivery carry-over, see E19) 5 |

**1 of 1 passed.**

## Details

### E25 Blocked invoices are explained with the right cause, and a block is never changed by the agent

Checks: Plan P1-18 (AG03): at least 80% of mismatch cases diagnosed correctly (proposed target); the payment block stays until a person releases it; numbers in the answer come from the data

- invoices entered: 2639
- blocked by the match: 302
- of those, with a cause the dataset planted: 297
- blocked without a planted cause (part-delivery carry-over, see E19): 5
- rules: cause diagnosed right %: 100.0
- rules: fixes proposed: {'corrected_invoice': 254, 'credit_note': 43}
- rules: queries to the supplier that name our reference %: 100.0
- investigator, careful: wording used / cause right: 40 / 40 of 40
- investigator naming a wrong cause: wording used / cause right: 0 / 40 of 40
- investigator adding a made-up number: wording used / cause right: 0 / 40 of 40
- tables changed by looking into invoices: none
- note: the real models were not called (run with --live)
