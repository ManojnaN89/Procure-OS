"""Make docs/capability-matrix.md: every Phase 0 and Phase 1 function of the plan, with room for the evidence that it works.

    python scripts/capability_matrix.py            # first time: builds the matrix from the plan workbook (planning/, git-ignored)
    python scripts/capability_matrix.py --summary  # counts by status from the committed matrix

The committed matrix is the working copy after the first run: the audit updates its Status, Evidence and Notes columns by
hand (one row per function). The plan workbook is only read to build the first version.
"""

import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "planning" / "P2P-Platform-Project-Plan-v7.xlsx"
OUT = ROOT / "docs" / "capability-matrix.md"
STATUSES = ("todo", "verified", "fixed", "deferred", "blocked")
HEAD = "| ID | Function | How | Plan step | Status | Evidence (test, eval or screen) | Notes |"


def build() -> str:
    import openpyxl

    wb = openpyxl.load_workbook(PLAN, read_only=True, data_only=True)
    steps: dict[str, str] = {}
    for r in wb["Plan"].iter_rows(values_only=True):
        if r and isinstance(r[0], str) and re.fullmatch(r"P\d-\d+", r[0]):
            steps[r[0]] = f"{r[2]}: {r[3]}"[:160]
    by_area: dict[str, list[tuple[str, ...]]] = defaultdict(list)
    seen: dict[str, tuple[str, ...]] = {}
    for r in list(wb["Capability Steps"].iter_rows(values_only=True))[3:]:
        if not r or not r[0] or r[4] not in ("P0", "P1"):
            continue
        lid = str(r[0])
        if lid in seen:  # one function can be built by several steps: list them together
            row = seen[lid]
            if str(r[6]) not in row[3]:
                new = (*row[:3], f"{row[3]}, {r[6]}", *row[4:])
                by_area[row[5]][by_area[row[5]].index(row)] = new
                seen[lid] = new
            continue
        row = (lid, str(r[3]), str(r[5]), str(r[6]), "todo", str(r[1]), str(r[2]))
        seen[lid] = row
        by_area[str(r[1])].append(row)
    out = [
        "# Capability matrix (Phase 0 and Phase 1)",
        "",
        "One row per function of the plan. Status: todo, verified (works, evidence given), fixed (was missing or broken, now",
        "works and has a test), deferred (not Phase 1, with the reason) or blocked (needs the owner, with what).",
        "Only a row with evidence may be `verified` or `fixed`.",
        "",
    ]
    for area in sorted(by_area):
        out += [f"## {area}", "", HEAD, "|---|---|---|---|---|---|---|"]
        for lid, name, how, step, status, _area, group in sorted(
            by_area[area], key=lambda x: [int(p) for p in x[0].split(".")]
        ):
            out.append(f"| {lid} | {group}: {name} | {how} | {step} | {status} |  |  |")
        out.append("")
    out += ["## Plan steps (for reference)", ""] + [
        f"- {k}: {v}" for k, v in sorted(steps.items()) if k.startswith(("P0", "P1"))
    ]
    return "\n".join(out) + "\n"


def summary() -> str:
    counts: Counter[str] = Counter()
    per_area: dict[str, Counter[str]] = defaultdict(Counter)
    area = ""
    for line in OUT.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            area = line[3:]
        m = re.match(r"\| \d+\.\d+\.\d+ \|", line)
        if m:
            cells = [c.strip() for c in line.strip("|").split("|")]
            counts[cells[4]] += 1
            per_area[area][cells[4]] += 1
    lines = [f"{k}: {counts[k]}" for k in STATUSES]
    lines += [f"  {a}: " + ", ".join(f"{k} {v}" for k, v in sorted(c.items())) for a, c in sorted(per_area.items())]
    return "\n".join(lines)


if __name__ == "__main__":
    if "--summary" in sys.argv:
        print(summary())
    else:
        OUT.write_text(build(), encoding="utf-8", newline="\n")
        print(f"wrote {OUT}")
