"""The invoice scenarios of block 10: the bills without an order, their tax and due dates.

    Written: truth/truth_invoice_intake.csv, rows `scenario,figure,value`.

The figures are worked out here from the scenario's own lines, the same way the platform does (tax per line, half up to
paise, total rounded to the rupee), and checked against the dataset's `non_po_rule.csv` limits. The scenario steps and
dates are the ones the spec gives (N1, N2, N6, N7, B1, B2, D4).
"""

import csv
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

D = Decimal
PAISE = D("0.01")
TRUTH = ["scenario", "figure", "value"]


def _half(value: Decimal) -> Decimal:
    return value.quantize(PAISE, rounding=ROUND_HALF_UP)


def _tax(taxable: Decimal, rate: Decimal) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    """(cgst, sgst, igst, total) for a local supply at a rate: half each way, the recorded total rounded to the rupee."""
    half = _half(taxable * rate / 200)
    total = (taxable + half + half).quantize(D("1"), rounding=ROUND_HALF_UP).quantize(PAISE)
    return half, half, D("0.00"), total


def _rows(name: str, out: Path) -> list[dict[str, str]]:
    limits = {r["category_code"]: D(r["max_value"]) for r in _read(out / "master" / "non_po_rule.csv")}
    rows: list[dict[str, str]] = []

    def put(scenario: str, **figures: object) -> None:
        for figure, value in figures.items():
            rows.append({"scenario": scenario, "figure": figure, "value": str(value)})

    if name == "apex":
        # N1: Sahyadri's internet bill, UTL, CC-ADM, 15,000 at 18%, NET30 from the invoice date (23 Nov 2026)
        cgst, sgst, igst, total = _tax(D("15000.00"), D("18"))
        put(
            "N1", supplier="SUP-010", number="SNL/2627/0458", category="UTL", taxable="15000.00",
            cgst=cgst, sgst=sgst, igst=igst, total=total, due="2026-12-23", held="15000.00",
            authoriser="U07", approval_rule="NPO-01", status="awaiting_authorisation",
        )
        # N2: the CA's fee, PRF, 75,000 at 18%, NET45 from 19 Nov but MSME 30 days from acceptance on 27 Nov
        cgst, sgst, igst, total = _tax(D("75000.00"), D("18"))
        put(
            "N2", supplier="SUP-011", number="MG/2627/112", category="PRF", taxable="75000.00",
            cgst=cgst, sgst=sgst, igst=igst, total=total,
            due=(date(2026, 11, 27) + timedelta(days=30)).isoformat(), authoriser="U07",
            approval_rule="NPO-02", approver="U05",
        )
        # N6: three more fees; the third brings CC-ADM 5410 to 3,30,000 of a 3,20,000 budget
        for number, taxable in (("MG/2627/115", "80000.00"), ("MG/2627/116", "85000.00"), ("MG/2627/117", "90000.00")):
            cgst, sgst, igst, total = _tax(D(taxable), D("18"))
            put(f"N6-{number[-3:]}", taxable=taxable, cgst=cgst, sgst=sgst, total=total)
        put("N6", budget="320000.00", held_before_last="240000.00", held_with_last="330000.00",
            over_by="10000.00", used_pct="103.13")
        # B1/B2: the UTL limit of 50,000 is reached exactly, and one paisa of tax over is refused
        for code, taxable in (("B1", "42372.88"), ("B2", "42373.73")):
            cgst, sgst, igst, total = _tax(D(taxable), D("18"))
            put(code, taxable=taxable, cgst=cgst, sgst=sgst, igst=igst, total=total, limit=limits["UTL"])
        put("B1", allowed="true")
        put("B2", allowed="false")
        # D4: the possible-duplicate window around N1's date of 23 Nov 2026: 30 Nov is inside 7 days, 1 Dec outside
        put("D4", inside="2026-11-30", outside="2026-12-01", window_days="7")
    elif name == "brightwave":
        # N7: Nandi's internet bill, TEL, CC-ITO, 25,000 at 18% (Bengaluru to Bengaluru), NET30 from 23 Nov 2026
        cgst, sgst, igst, total = _tax(D("25000.00"), D("18"))
        put(
            "N7", supplier="SUP-007", number="NFN/2627/1187", category="TEL", taxable="25000.00",
            cgst=cgst, sgst=sgst, igst=igst, total=total, due="2026-12-23", authoriser="U08",
            approval_rule="NPO-01", status="awaiting_authorisation",
        )
    return rows


def _read(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def write_invoice(profile: object, out: Path) -> int:
    """truth/truth_invoice_intake.csv from the profile's name (nothing for a company without bills)."""
    name = getattr(profile, "name", "")
    rows = _rows(name, out)
    if not rows:
        return 0
    path = out / "truth" / "truth_invoice_intake.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=TRUTH, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def check(out: Path) -> list[str]:
    """Every scenario's parts add up, and its limit test names the limit in force."""
    path = out / "truth" / "truth_invoice_intake.csv"
    if not path.is_file():
        return []
    problems: list[str] = []
    rows = _read(path)
    figures: dict[str, dict[str, str]] = {}
    for r in rows:
        figures.setdefault(r["scenario"], {})[r["figure"]] = r["value"]
    for scenario in sorted({r["scenario"] for r in rows}):
        f = figures[scenario]
        if {"taxable", "cgst", "sgst", "igst", "total"} <= set(f):
            raw = D(f["taxable"]) + D(f["cgst"]) + D(f["sgst"]) + D(f["igst"])
            rounded = raw.quantize(D("1"), rounding=ROUND_HALF_UP)
            if rounded != D(f["total"]):
                problems.append(f"truth_invoice_intake {scenario}: the parts make {rounded}, the total says {f['total']}")
        if scenario in ("B1", "B2"):
            limit = D(f["limit"])
            over = D(f["total"]) > limit
            if over == (f["allowed"] == "true"):
                problems.append(f"truth_invoice_intake {scenario}: the limit test does not agree with {f['limit']}")
    return problems
