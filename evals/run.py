"""Run the evals.

    python evals/run.py                  every eval, with a scripted model where the AI is involved
    python evals/run.py --live           the AI evals call the real models (needs the keys in .env; uses the free tiers)
    python evals/run.py --only E04,E09   some of them
    python evals/run.py --quick          smaller samples

The report goes to evals/results/latest.md and latest.json. The exit code is 1 if any eval failed.
"""

import argparse
import importlib
import json
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import env
from lib import Ctx, Finding, Outcome


def discover() -> list:
    names = sorted(p.stem for p in HERE.glob("e[0-9][0-9]_*.py"))
    return [importlib.import_module(n) for n in names]


def render(results: list[dict], live: bool) -> str:
    lines = [
        "# Evals report",
        "",
        f"Run on {time.strftime('%Y-%m-%d %H:%M')}; AI evals used {'the real models' if live else 'a scripted model'}.",
        "",
        "| ID | What it checks | Result | Seconds | Key figures |",
        "|---|---|---|---|---|",
    ]
    for r in results:
        key = "; ".join(f"{k} {v}" for k, v in list(r["metrics"].items())[:4])
        lines.append(
            f"| {r['id']} | {r['title']} | **{r['status']}** | {r['seconds']} | {key} |"
        )
    passed = sum(1 for r in results if r["status"] == "PASS")
    lines += ["", f"**{passed} of {len(results)} passed.**", ""]
    findings = [(r["id"], f) for r in results for f in r["findings"]]
    if findings:
        lines += ["## Findings", ""]
        for rid, f in sorted(
            findings, key=lambda x: {"high": 0, "medium": 1, "low": 2}[x[1]["severity"]]
        ):
            lines.append(f"- **{f['severity']}** ({rid}): {f['text']}")
        lines.append("")
    lines += ["## Details", ""]
    for r in results:
        lines += [f"### {r['id']} {r['title']}", "", f"Checks: {r['requirement']}", ""]
        for k, v in r["metrics"].items():
            lines.append(f"- {k}: {v}")
        for n in r["notes"]:
            lines.append(f"- note: {n}")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--only", default="")
    ap.add_argument("--out", default=str(HERE / "results" / "latest"))
    args = ap.parse_args()
    ctx = Ctx(
        live=args.live,
        quick=args.quick,
        only={s.strip().upper() for s in args.only.split(",") if s.strip()},
    )
    results: list[dict] = []
    with env.environment():
        for mod in discover():
            if ctx.only and mod.ID.upper() not in ctx.only:
                continue
            started = time.monotonic()
            try:
                outcome: Outcome = mod.run(ctx)
            except Exception:  # noqa: BLE001 - an eval that crashes is a failed eval with the reason
                outcome = Outcome(False)
                outcome.findings.append(
                    Finding(
                        "high",
                        "the eval itself crashed:\n" + traceback.format_exc(limit=6),
                    )
                )
            status = "PASS" if outcome.passed else "FAIL"
            if getattr(outcome, "skipped", False):
                status = "SKIP"
            seconds = round(time.monotonic() - started, 1)
            results.append(
                {
                    "id": mod.ID,
                    "title": mod.TITLE,
                    "requirement": mod.REQUIREMENT,
                    "status": status,
                    "seconds": seconds,
                    "metrics": outcome.metrics,
                    "findings": [
                        {"severity": f.severity, "text": f.text}
                        for f in outcome.findings
                    ],
                    "notes": outcome.notes,
                }
            )
            print(f"{mod.ID:<5} {status:<5} {seconds:>6}s  {mod.TITLE}")
            for f in outcome.findings:
                print(f"        {f.severity}: {f.text.splitlines()[0][:200]}")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(
        json.dumps(results, indent=2, default=str), encoding="utf-8"
    )
    out.with_suffix(".md").write_text(render(results, args.live), encoding="utf-8")
    failed = [r for r in results if r["status"] == "FAIL"]
    print(
        f"\n{len(results) - len(failed)} of {len(results)} passed; report in {out.with_suffix('.md')}"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
