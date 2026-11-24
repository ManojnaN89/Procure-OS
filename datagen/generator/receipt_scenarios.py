"""Block 9's receipt figures, worked out here from the prices and tolerances the dataset carries (never by the backend).

`truth/truth_receipts.csv` has `scenario,figure,value`: the room, counted and excess of R1 and R3, the revision's line
and order totals (R2), the budget at receipt (R1, R3, R4), the claim amounts (R5 and its variant), and the reversal
amount (R6). The scenarios' dates and actions are in block 9's spec, section 9.
"""

from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from master_files import write_csv

TRUTH = ["scenario", "figure", "value"]
HUNDRED = Decimal(100)


def _q(value: Decimal) -> str:
    text = f"{value:f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def _money(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _gst(taxable: Decimal, rate: str, *, inter_state: bool) -> tuple[Decimal, Decimal, Decimal]:
    """CGST and SGST each half the rate, or IGST at it, each rounded to the paisa (as an order line)."""
    r = Decimal(rate) / HUNDRED
    if inter_state:
        return Decimal("0.00"), Decimal("0.00"), (taxable * r).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    half = (taxable * r / 2).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return half, half, Decimal("0.00")


def _rows() -> list[dict[str, str]]:
    out: list[dict[str, str]] = []

    def add(scenario: str, figure: str, value: Any) -> None:
        out.append({"scenario": scenario, "figure": figure, "value": str(value)})

    # R1: A - RM-03 200 at 220, 2% room; 210 arrive, 3 rejected at the gate
    room = (Decimal(200) * Decimal("102") / HUNDRED).quantize(Decimal("0.0001"))
    add("R1", "room", _q(room))
    add("R1", "good", _q(Decimal(207)))
    add("R1", "counted", _q(Decimal(204)))
    add("R1", "excess", _q(Decimal(3)))
    add("R1", "received_qty", _q(Decimal(204)))
    add("R1", "budget", _money(Decimal(204) * Decimal("220")))
    add("R1", "gate_return_qty", _q(Decimal(3)))

    # R2: the revision to 207 NOS at 220, GST 5% (CGST 2.5% + SGST 2.5%)
    taxable = (Decimal(207) * Decimal("220")).quantize(Decimal("0.01"))
    cgst, sgst, igst = _gst(taxable, "5", inter_state=False)
    add("R2", "version", "2")
    add("R2", "new_qty", _q(Decimal(207)))
    add("R2", "taxable", _money(taxable))
    add("R2", "cgst", _money(cgst))
    add("R2", "sgst", _money(sgst))
    add("R2", "igst", _money(igst))
    add("R2", "total", _money(taxable + cgst + sgst + igst))
    add("R2", "budget_after", _money(taxable))
    add("R2", "approver", "U08")

    # R2b: the return leaves the line as it was
    add("R2b", "received_qty", _q(Decimal(204)))
    add("R2b", "claim", "none")

    # R3: B - RM-06 500 at 38, 2% room; 515 arrive, 512 accepted, 3 rejected at the inspection
    add("R3", "room", _q((Decimal(500) * Decimal("102") / HUNDRED).quantize(Decimal("0.0001"))))
    add("R3", "counted", _q(Decimal(510)))
    add("R3", "excess", _q(Decimal(2)))
    add("R3", "blocked", _q(Decimal(3)))
    add("R3", "received_qty", _q(Decimal(510)))
    add("R3", "budget", _money(Decimal(510) * Decimal("38")))
    add("R3", "inspected_by", "U11")

    # R4: the blocked 10 go back; the replacement makes 500
    add("R4", "received_after_inspection", _q(Decimal(490)))
    add("R4", "budget_after_inspection", _money(Decimal(490) * Decimal("38")))
    add("R4", "received_after_replacement", _q(Decimal(500)))
    add("R4", "budget_after_replacement", _money(Decimal(500) * Decimal("38")))
    add("R4", "claim", "none")

    # R5: 20 counted back, invoiced 500, 480 received: claim 20; IGST 18% (Gujarat supplier)
    add("R5", "received_qty", _q(Decimal(480)))
    add("R5", "budget_minus", _money(Decimal(20) * Decimal("38")))
    cgst, sgst, igst = _gst(Decimal(20) * Decimal("38"), "18", inter_state=True)
    add("R5", "claim_qty", _q(Decimal(20)))
    add("R5", "claim_taxable", _money(Decimal(20) * Decimal("38")))
    add("R5", "claim_cgst", _money(cgst))
    add("R5", "claim_sgst", _money(sgst))
    add("R5", "claim_igst", _money(igst))
    add("R5", "claim_total", _money(Decimal(20) * Decimal("38") + igst))
    # the variant with the invoice for 490: 490 - 480 = 10 at stake
    cgst, sgst, igst = _gst(Decimal(10) * Decimal("38"), "18", inter_state=True)
    add("R5", "variant_claim_qty", _q(Decimal(10)))
    add("R5", "variant_claim_taxable", _money(Decimal(10) * Decimal("38")))
    add("R5", "variant_claim_igst", _money(igst))
    add("R5", "variant_claim_total", _money(Decimal(10) * Decimal("38") + igst))

    # R6: D - RM-05 100 at 180
    add("R6", "received_qty", _q(Decimal(100)))
    add("R6", "budget", _money(Decimal(100) * Decimal("180")))
    add("R6", "budget_after_reversal", _money(Decimal(0)))

    # R6b/c: the window ends on the third working day after posting; a locked month moves the reversal's actual
    add("R6d", "platinum_window_end", "2026-12-14")
    add("R6d", "monday_friday_window_end", "2026-12-15")
    add("R6c", "reversal_period", "2026-12")
    add("R6c", "original_period", "2026-11")
    return out


def write_receipts(profile: dict[str, Any], out: Path) -> int:
    """Write truth/truth_receipts.csv (the figures are the spec's arithmetic; the profile only dates the dataset)."""
    del profile
    rows = _rows()
    write_csv(out / "truth" / "truth_receipts.csv", TRUTH, rows)
    return len(rows)
