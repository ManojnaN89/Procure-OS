"""Block 12's worked figures (payment): due dates, the small supplier's interest, TDS, run totals and an advance.

Writes truth/truth_payment_block12.csv with rows `scenario,figure,value`, worked out here by the dataset's own
arithmetic (not the platform's), as section 5 of the block 12 spec gives them.
"""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal, localcontext

from common import D, q2

TRUTH = ["scenario", "figure", "value"]
PAISE = Decimal("0.01")


def _interest(total: Decimal, due: date, paid: date, bank_rate: Decimal) -> Decimal:
    """Three times the bank rate, compounded monthly, for the days after the due date (half up to paise)."""
    days = (paid - due).days
    if days <= 0:
        return D(0)
    rate = bank_rate * 3 / 100
    with localcontext() as ctx:
        ctx.prec = 28
        factor = (Decimal(1) + rate / 12) ** (Decimal(days) / Decimal("30.4"))
        return ((total * (factor - 1)).quantize(PAISE, rounding=ROUND_HALF_UP))


def write(data: dict, out) -> None:
    """The truth file; figures are the spec's examples (Y1-Y10), computed here."""
    del data  # the figures are the spec's worked cases, not the profile's history
    bank_rate = D("5.75")
    due = date(2026, 12, 17)
    y2_total = D("11210.00")
    y3_b_base = D("13575.00")
    y3_b_tds = q2(y3_b_base * D("0.1") / 100)
    rows = [
        {"scenario": "Y1", "figure": "due_date", "value": "2026-12-25"},
        {"scenario": "Y1", "figure": "pay_by", "value": "2026-12-24"},
        {"scenario": "Y1b", "figure": "due_date", "value": "2026-12-26"},
        {"scenario": "Y1b", "figure": "pay_by", "value": "2026-12-24"},
        {"scenario": "Y2", "figure": "interest_day_45", "value": str(_interest(y2_total, due, due, bank_rate))},
        {"scenario": "Y2", "figure": "interest_day_46", "value": str(_interest(y2_total, due, date(2026, 12, 18), bank_rate))},
        {"scenario": "Y2", "figure": "interest_day_49", "value": str(_interest(y2_total, due, date(2026, 12, 21), bank_rate))},
        {"scenario": "Y3", "figure": "a_tds", "value": "0.00"},
        {"scenario": "Y3", "figure": "b_base", "value": str(y3_b_base)},
        {"scenario": "Y3", "figure": "b_tds", "value": str(y3_b_tds)},
        {"scenario": "Y3", "figure": "b_net", "value": str(D("16018.50") - y3_b_tds)},
        {"scenario": "Y4", "figure": "c_tds", "value": "0.00"},
        {"scenario": "Y4", "figure": "d_tds", "value": str(q2(D("12000.00") * D("2") / 100))},
        {"scenario": "Y4", "figure": "d_net", "value": str(D("14160.00") - q2(D("12000.00") * D("2") / 100))},
        {"scenario": "Y5", "figure": "run_total", "value": str(D("10030.00") + D("26683.92") + D("29732.00"))},
        {"scenario": "Y9", "figure": "gross", "value": str(q2(D("38350.00") * D("30") / 100))},
        {"scenario": "Y9", "figure": "taxable", "value": str(q2(D("32500.00") * D("30") / 100))},
        {"scenario": "Y9", "figure": "invoice_payable", "value": str(D("38350.00") - q2(D("38350.00") * D("30") / 100))},
        {"scenario": "Y9", "figure": "tds_base", "value": str(D("32500.00") - q2(D("32500.00") * D("30") / 100))},
        {"scenario": "Y10", "figure": "payment", "value": str(D("5310.00") - D("1180.00"))},
    ]
    from master_files import write_csv  # noqa: PLC0415 - the dataset writer

    (out / "truth").mkdir(parents=True, exist_ok=True)
    write_csv(out / "truth" / "truth_payment_block12.csv", TRUTH, rows)
