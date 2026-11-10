# Evals report

Run on 2026-11-10 13:12; AI evals used a scripted model.

| ID | What it checks | Result | Seconds | Key figures |
|---|---|---|---|---|
| E11 | Hostile text is shown as text and never run | **PASS** | 33.5 | screens opened 18; hostile payloads 6; script ran or markup injected 0; SQL probes 12 |

**1 of 1 passed.**

## Details

### E11 Hostile text is shown as text and never run

Checks: Security: names from imported data and typed identifiers cannot inject script into the screens or SQL into the database

- screens opened: 18
- hostile payloads: 6
- script ran or markup injected: 0
- SQL probes: 12
- server errors: 0
- slow (sleep ran): 0
- orders on screen: 34
- pipeline note: 9
