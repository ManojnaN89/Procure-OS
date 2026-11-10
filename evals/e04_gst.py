"""E04: net prices, GST and order totals, checked against an exact-fraction oracle on thousands of random lines."""

import random
from decimal import Decimal
from fractions import Fraction

from lib import Outcome

ID = "E04"
TITLE = "GST and order arithmetic is exact, to the paisa"
REQUIREMENT = "Rule 1: money is Decimal to paise; GST splits into CGST+SGST within the state and IGST across states; composition and unregistered suppliers charge none"


def half_up(x: Fraction, places: int) -> Decimal:
    """Round half up, from the exact fraction (no Decimal context involved: an independent route to the same answer)."""
    scale = 10**places
    n = (x * scale + Fraction(1, 2)).__floor__()
    return Decimal(n) / Decimal(scale)


def oracle_net(price: Decimal, discount: Decimal) -> Decimal:
    return half_up(Fraction(price) * (100 - Fraction(discount)) / 100, 4)


def oracle_line(
    qty: Decimal,
    unit_price: Decimal,
    rate: Decimal,
    supplier_state: str,
    place: str,
    registration: str,
) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    taxable = half_up(Fraction(qty) * Fraction(unit_price), 2)
    if registration != "regular":
        return taxable, Decimal("0.00"), Decimal("0.00"), Decimal("0.00")
    if supplier_state == place:
        half = half_up(Fraction(taxable) * Fraction(rate) / 200, 2)
        return taxable, half, half, Decimal("0.00")
    return (
        taxable,
        Decimal("0.00"),
        Decimal("0.00"),
        half_up(Fraction(taxable) * Fraction(rate) / 100, 2),
    )


def run(ctx) -> Outcome:
    from p2p.formulas.gst import Registration
    from p2p.formulas.orders import line_amounts, net_unit_price, order_totals

    out = Outcome(True)
    rng = random.Random(2026)
    states = [f"{n:02d}" for n in range(1, 38)]
    rates = [Decimal(r) for r in ("0", "0.25", "3", "5", "12", "18", "28")]
    n = 800 if ctx.quick else 5000
    bad_net = bad_line = bad_identity = bad_total = 0
    examples: list[str] = []
    for _ in range(n):
        qty = Decimal(rng.randint(1, 5_000_000)) / Decimal(10 ** rng.choice([0, 1, 2, 3]))
        price = Decimal(rng.randint(1, 900_000_000)) / Decimal(10 ** rng.choice([2, 3, 4]))
        discount = Decimal(rng.randint(0, 4000)) / 100
        rate = rng.choice(rates)
        supplier, place = rng.choice(states), rng.choice(states)
        reg = rng.choice(["regular"] * 7 + ["composition", "unregistered"])
        net = net_unit_price(price, discount)
        if net != oracle_net(price, discount):
            bad_net += 1
            examples.append(f"net price {price} less {discount}%: got {net}, expected {oracle_net(price, discount)}")
        got = line_amounts(qty, net, rate, supplier, place, Registration(reg))
        want = oracle_line(qty, net, rate, supplier, place, reg)
        if (got.taxable, got.cgst, got.sgst, got.igst) != want:
            bad_line += 1
            examples.append(
                f"line qty {qty} price {net} rate {rate} {supplier}->{place} {reg}: got {(got.taxable, got.cgst, got.sgst, got.igst)}, expected {want}"
            )
        if got.total != got.taxable + got.cgst + got.sgst + got.igst or any(
            v < 0 for v in (got.taxable, got.cgst, got.sgst, got.igst)
        ):
            bad_identity += 1
        if (
            reg == "regular"
            and supplier == place
            and got.igst != 0
            or reg == "regular"
            and supplier != place
            and (got.cgst != 0 or got.sgst != 0)
        ):
            bad_identity += 1
    # totals of several lines are the exact sums of their parts
    for _ in range(300 if not ctx.quick else 60):
        parts = [
            line_amounts(
                Decimal(rng.randint(1, 500)),
                Decimal(rng.randint(1, 99999)) / 100,
                rng.choice(rates),
                "27",
                rng.choice(["27", "29"]),
            )
            for _ in range(rng.randint(1, 12))
        ]
        t = order_totals(parts)
        if t.taxable != sum((p.taxable for p in parts), Decimal(0)) or t.total != t.taxable + t.cgst + t.sgst + t.igst:
            bad_total += 1
    out.metrics = {
        "random lines": n,
        "net price errors": bad_net,
        "line errors": bad_line,
        "identity errors": bad_identity,
        "total errors": bad_total,
    }
    for label, count in (
        ("net price", bad_net),
        ("line amount", bad_line),
        ("identity", bad_identity),
        ("order total", bad_total),
    ):
        if count:
            out.find(
                "high",
                f"{count} {label} mismatch(es) against the exact oracle; first: {examples[0] if examples else ''}",
            )
    return out
