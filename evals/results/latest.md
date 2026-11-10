# Evals report

Run on 2026-11-10 17:17; AI evals used a scripted model.

| ID | What it checks | Result | Seconds | Key figures |
|---|---|---|---|---|
| E12 | No floats in money, exact amounts on the wire, India's calendar, no secrets, synthetic data stays synthetic | **PASS** | 19.1 | money modules scanned 6; float uses 0; money fields as JSON numbers 0; UTC dates in business logic 0 |

**1 of 1 passed.**

## Details

### E12 No floats in money, exact amounts on the wire, India's calendar, no secrets, synthetic data stays synthetic

Checks: Rules 1, 9, 10 and the generated-data safety rule

- money modules scanned: 6
- float uses: 0
- money fields as JSON numbers: 0
- UTC dates in business logic: 0
- tracked files scanned for secrets: 501
- secret hits: 0
- unsafe generated values: 0
- note: money fields sent as numbers: []
