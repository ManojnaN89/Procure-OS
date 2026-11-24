"""The block 11 scenarios' arithmetic (spec section 9): M, X, N and H, worked out here in exact decimals.

The platform reads this file as `truth/truth_match_block11.csv`; the eval's oracle works the same figures out again
from the tolerances and the section's inputs. Nothing here reads the platform's code.
"""

from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

D = Decimal
CENT = D("0.01")


def q(value: Any) -> Decimal:
    return D(value).quantize(CENT, rounding=ROUND_HALF_UP)


def tax(value: Decimal, rate: Decimal, intra: bool) -> tuple[Decimal, Decimal, Decimal]:
    if intra:
        half = q(value * rate / 200)
        return half, half, D("0.00")
    return D("0.00"), D("0.00"), q(value * rate / 100)


def rows() -> list[dict[str, str]]:
    out: list[dict[str, str]] = []

    def add(scenario: str, figure: str, value: Any) -> None:
        out.append(
            {
                "scenario": scenario,
                "figure": figure,
                "value": f"{q(value):f}" if isinstance(value, Decimal) else str(value),
            }
        )

    # M: PO to SUP-001 (MH), three lines at 18%, 5% and 5% within Maharashtra
    lines = [(100, D("480.00"), D("18")), (200, D("220.00"), D("5")), (150, D("180.00"), D("5"))]
    taxable = sum((q(qty * price) for qty, price, _ in lines), D(0))
    for n, (qty, price, rate) in enumerate(lines, 1):
        cgst, sgst, igst = tax(q(qty * price), rate, True)
        add("M", f"order.line{n}.taxable", q(qty * price))
        add("M", f"order.line{n}.cgst", cgst)
        add("M", f"order.line{n}.sgst", sgst)
        add("M", f"order.line{n}.igst", igst)
    add("M", "order.taxable", taxable)
    tax_total = sum((tax(q(qty * price), rate, True)[0] * 2 for qty, price, rate in lines), D(0))
    add("M", "order.tax", tax_total)
    add("M", "order.total", taxable + tax_total)

    invoices = {
        "A": [(100, D("483.01"), D("18"))],
        "B": [(10, D("224.40"), D("5"))],
        "C": [(10, D("224.41"), D("5"))],
        "D1": [(150, D("180.00"), D("12"))],
        "D2": [(150, D("180.00"), D("5"))],
        "D3": [(150, D("180.00"), D("5"))],
        "E": [(190, D("220.00"), D("5"))],
    }
    for name, its in invoices.items():
        value = sum((q(qty * price) for qty, price, _ in its), D(0))
        add("M", f"{name}.taxable", value)
        c, s, i = tax(value, its[0][2], True)
        add("M", f"{name}.cgst", c)
        add("M", f"{name}.sgst", s)
        add("M", f"{name}.igst", i)
        add("M", f"{name}.total", value + c + s + i)
    add("M", "A.variance", q((D("483.01") - D("480.00")) * 100))
    add("M", "B.variance", q((D("224.40") - D("220.00")) * 10))
    add("M", "C.variance", q((D("224.41") - D("220.00")) * 10))
    add("M", "E.available_before_R2", 110)  # 120 received less B's 10
    add("M", "E.quantity_over", 80)
    add("M", "E.available_after_R2", 190)  # 200 received less B's 10
    add("M", "D2.cgst_apart", D("1.01"))

    # X: 1,000 ordered and 400 received, then 600 more
    add("X", "order.taxable", q(D("1000") * D("180.00")))
    add("X", "order.cgst", q(D("180000") * D("5") / 200))
    add("X", "order.sgst", q(D("180000") * D("5") / 200))
    add("X", "order.total", q(D("180000") + D("9000")))
    add("X", "I1.quantity_blocked_after_R1", 600)
    add("X", "I2.available_after_I1", 400)
    add("X", "I1.available_after_R2", 600)
    add("X", "I1.quantity_over_after_R2", 400)
    add("X", "invoiced_after_competition", 400)

    # N: a supply from Gujarat into Maharashtra, 500 at 38.00 at 18%
    add("N", "order.taxable", q(D("500") * D("38.00")))
    add("N", "order.igst", q(D("19000") * D("18") / 100))
    add("N", "order.total", q(D("19000") + D("3420")))
    add("N", "F.igst", D("3420.00"))
    add("N", "G.cgst", D("1710.00"))
    add("N", "G.sgst", D("1710.00"))

    # H: paise, half up
    add("H", "line1.taxable", q(D("100") * D("482.995")))
    add("H", "line1.cgst", q(q(D("100") * D("482.995")) * D("18") / 200))
    add("H", "line1.sgst", q(q(D("100") * D("482.995")) * D("18") / 200))
    add("H", "line1.variance", q((D("482.995") - D("480.00")) * 100))
    return out


def write_truth(profile: Any) -> dict[str, list[dict[str, Any]]]:
    """The truth table for an Apex dataset (the scenarios are written from the same arithmetic for any company)."""
    if profile.name != "apex":
        return {}
    return {"truth_match_block11": [{**r, "_t": datetime(2000, 1, 1)} for r in rows()]}
