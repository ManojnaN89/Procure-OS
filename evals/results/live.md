# Evals report

Run on 2026-11-07 01:02; AI evals used the real models.

| ID | What it checks | Result | Seconds | Key figures |
|---|---|---|---|---|
| E13 | Supplier emails are read into the right quotes, and when unsure the system asks a person | **PASS** | 256.5 | email cases 39; perfect reader: exact % 100.0; perfect reader: silent errors 0; sloppy reader: mistakes that did not slip through {'invented price': '33/33 not clean-but-wrong', 'invented gst': '33/33 not clean-but-wrong', 'invented delivery': '33/33 not clean-but-wrong', 'swapped prices': '16/16 not clean-but-wrong'} |
| E15 | The assistant tells the truth from the data, adds nothing up on its own, and changes nothing | **PASS** | 30.2 | questions 12; answers right % 100.0; answers grounded % 91.7; right tool chosen % 100.0 |

**2 of 2 passed.**

## Details

### E13 Supplier emails are read into the right quotes, and when unsure the system asks a person

Checks: Plan step 8 and rule 2: the model reads, code checks; a wrong reading must never look clean (silent errors = 0)

- email cases: 39
- perfect reader: exact %: 100.0
- perfect reader: silent errors: 0
- sloppy reader: mistakes that did not slip through: {'invented price': '33/33 not clean-but-wrong', 'invented gst': '33/33 not clean-but-wrong', 'invented delivery': '33/33 not clean-but-wrong', 'swapped prices': '16/16 not clean-but-wrong'}
- LIVE model: exact %: 94.9
- LIVE model: exact or safely refused %: 100.0
- LIVE model: silent errors: 0
- LIVE model: sent to a person: 4
- LIVE model: intent right %: 100.0
- LIVE model: median seconds per email: 1.22
- LIVE model: by style: {'sentences': '3/3', 'table': '3/3', 'by-code': '1/3', 'discount': '3/3', 'gst-included': '3/3', 'partial': '3/3', 'noisy': '4/4', 'validity-date': '2/2', 'validity-relative': '2/2', 'charges': '3/3', 'total-only': '2/2', 'no-gst-stated': '2/2', 'loose-wording': '2/2', 'decline': '2/2', 'question': '2/2'}
- note: live misses: ["live-by-code-07: status needs_person: ['no line could be matched and priced']", "live-by-code-09: status needs_person: ['no line could be matched and priced']"]

### E15 The assistant tells the truth from the data, adds nothing up on its own, and changes nothing

Checks: Plan step 8 and rule 2: read-only tools; every figure it gives comes from a tool; it cannot approve or delete

- questions: 12
- answers right %: 100.0
- answers grounded %: 91.7
- right tool chosen %: 100.0
- median seconds: 1.4
- data changed: False
