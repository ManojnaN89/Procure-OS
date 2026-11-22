"""When a change a supplier asks for is small enough to accept without a person, or a price change small enough for a
buyer alone. Plain rules on dates, quantities and prices.

The limits come from the company's tolerance table ('supplier_date_days', 'supplier_change_pct', 'supplier_price_pct').
A price change is never applied without a person: judge_price only says whether the buyer may accept it or the purchase
manager has to.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from p2p.formulas.words import count

IN_TOLERANCE = "in_tolerance"
OUT = "out_of_tolerance"


@dataclass(frozen=True)
class Verdict:
    result: str  # in_tolerance or out_of_tolerance
    detail: str  # in plain words, for the buyer or the supplier

    @property
    def ok(self) -> bool:
        return self.result == IN_TOLERANCE


def requested_date(current: date | None, new_date: date | None, delay_days: int | None) -> date | None:
    """The date the supplier is asking for: the one it wrote, or the promised date moved by the days of delay it wrote."""
    if new_date is not None:
        return new_date
    if current is not None and delay_days is not None:
        return current + timedelta(days=delay_days)
    return None


def judge_date(current: date | None, requested: date | None, tolerance_days: int) -> Verdict:
    """A later date is accepted if it is no more than the tolerance (in calendar days, the last day included) after the
    date now promised; an earlier date is always acceptable. With no promised date or no requested date there is nothing to compare."""
    if requested is None:
        return Verdict(OUT, "the email gives no date and no number of days")
    if current is None:
        return Verdict(OUT, "there is no promised date on the order to compare with")
    slip = (requested - current).days
    if slip <= tolerance_days:
        return Verdict(
            IN_TOLERANCE,
            f"{requested:%d %b %Y} is {count(slip, 'day')} after the promised {current:%d %b %Y}, within {tolerance_days}",
        )
    return Verdict(
        OUT,
        f"{requested:%d %b %Y} is {count(slip, 'day')} after the promised {current:%d %b %Y}, more than the {tolerance_days} accepted",
    )


def judge_quantity(ordered: Decimal, offered: Decimal, tolerance_pct: Decimal) -> Verdict:
    """A quantity the supplier says it will supply is accepted if it is within the tolerance percentage of the quantity ordered
    (the percentage itself is allowed). Anything further off, either way, is for a person."""
    if ordered <= 0:
        return Verdict(OUT, "the ordered quantity is not known")
    difference = abs(ordered - offered)
    allowed = ordered * tolerance_pct / 100
    if difference <= allowed:
        return Verdict(IN_TOLERANCE, f"{offered} against {ordered} ordered is within {tolerance_pct}%")
    return Verdict(
        OUT,
        f"{offered} against {ordered} ordered is a difference of {difference}, more than the {tolerance_pct}% accepted",
    )


def judge_price(old: Decimal, new: Decimal, tolerance_pct: Decimal) -> Verdict:
    """Whether a buyer may accept a new unit price alone ('supplier_price_pct'): a lower price always, a higher one up to
    the tolerance (the percentage itself included). Above it the purchase manager decides. Exact: no rounding."""
    if old <= 0 or new <= 0:
        return Verdict(OUT, "the price is not known")
    allowed = old * tolerance_pct / 100
    if new - old <= allowed:
        return Verdict(IN_TOLERANCE, f"{new} against {old} is within {tolerance_pct}%")
    return Verdict(
        OUT, f"{new} against {old} is a rise of {new - old}, more than the {tolerance_pct}% a buyer may accept"
    )


def price_change_pct(old: Decimal | None, new: Decimal | None) -> Decimal | None:
    """How much a price would change, in percent, for the buyer to see. It never decides anything."""
    if not old or old <= 0 or new is None:
        return None
    return ((new - old) * 100 / old).quantize(Decimal("0.1"))
