"""The data generator: the same seed gives the same files, and the history holds the planted problems the platform is tested on."""

import csv
import hashlib
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GENERATOR = ROOT / "datagen" / "generator"


def _generate(out: Path, seed: int) -> Path:
    subprocess.run(
        [sys.executable, "-I", str(GENERATOR / "cli.py"), "--size", "small", "--seed", str(seed), "--out", str(out)],
        check=True,
        cwd=ROOT,
        capture_output=True,
    )
    return out


def _hashes(root: Path) -> dict[str, str]:
    # report.md is written by the validator, not by the generator
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file() and p.name != "report.md"
    }


def _rows(root: Path, folder: str, name: str) -> list[dict[str, str]]:
    with (root / folder / f"{name}.csv").open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


@pytest.fixture(scope="module")
def first(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _generate(tmp_path_factory.mktemp("gen") / "run1-small", 42)


def test_the_same_seed_gives_identical_files_and_another_seed_gives_different_ones(first: Path, tmp_path: Path) -> None:
    again = _generate(tmp_path / "run2-small", 42)
    other = _generate(tmp_path / "run3-small", 43)

    assert _hashes(first) == _hashes(again) and len(_hashes(first)) > 50
    assert _hashes(first) != _hashes(other)


def test_the_validator_passes_on_a_generated_dataset(first: Path) -> None:
    done = subprocess.run(
        [sys.executable, "-I", str(GENERATOR / "validate.py"), str(first)], cwd=ROOT, capture_output=True, text=True
    )

    assert done.returncode == 0, done.stdout[-600:] + done.stderr[-600:]
    assert "All hard checks passed" in (first / "report.md").read_text(encoding="utf-8")


def test_the_history_holds_the_problems_the_platform_is_tested_on(first: Path) -> None:
    planted = Counter(r["type"] for r in _rows(first, "truth", "truth_case"))
    for kind in (
        "part_delivery",
        "late_delivery",
        "price_mismatch",
        "qty_mismatch",
        "duplicate_invoice",
        "credit_note",
    ):
        assert planted[kind] > 0, f"no {kind} case was planted: {dict(planted)}"
    demand = _rows(first, "truth", "truth_item_demand")
    patterns = {r["pattern"] for r in demand}
    assert {"steady", "intermittent", "seasonal", "trend", "lumpy"} <= patterns  # spikes are the lumpy items
    assert any(r["stockout_periods"] for r in demand), "no item ran out of stock in the history"
    suppliers = _rows(first, "truth", "truth_supplier")
    assert suppliers and all(float(r["price_drift_pct_per_quarter"]) > 0 for r in suppliers)  # prices drift over time


def test_generated_identifiers_can_never_belong_to_a_real_person_or_company(first: Path) -> None:
    for r in _rows(first, "master", "supplier"):
        assert "0000" in (r.get("pan") or "0000"), r.get("pan")
    for r in _rows(first, "master", "user"):
        assert (r.get("email") or "x.example").endswith(".example"), r.get("email")
        assert (r.get("phone") or "+91 00000").startswith("+91 00000"), r.get("phone")
