"""A supplier's score: price, delivery and quality, each between 0 and 1, combined by weights into 0 to 100.

Plain arithmetic on counts and prices that come from our own orders and receipts. A supplier with little history is
pulled towards a neutral value (a few imagined average observations), so one good delivery does not outrank a long record.
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

ONE = Decimal(1)
NEUTRAL = Decimal("0.7")  # what a supplier with no history is assumed to be worth, on each of delivery and quality
NEUTRAL_PRICE = Decimal("0.8")  # a supplier with no price to compare
PRIOR_OBSERVATIONS = Decimal(3)  # the weight of that assumption, in observations
ON_TIME_SHARE_OF_DELIVERY = Decimal("0.8")  # delivery is 80% on-time receipts and 20% quick acknowledgement of orders


@dataclass(frozen=True)
class Weights:
    price: Decimal = Decimal(40)
    delivery: Decimal = Decimal(40)
    quality: Decimal = Decimal(20)

    @property
    def total(self) -> Decimal:
        return self.price + self.delivery + self.quality


def smoothed(good: Decimal, observations: Decimal) -> Decimal:
    """The share of good outcomes, pulled towards the neutral value when there are few observations."""
    return (good + NEUTRAL * PRIOR_OBSERVATIONS) / (observations + PRIOR_OBSERVATIONS)


def price_score(best_price: Decimal, price: Decimal) -> Decimal:
    """1 for the lowest price on offer; the best price divided by this price for anything dearer."""
    if price <= 0 or best_price <= 0:
        return Decimal(0)
    return min(ONE, best_price / price)


def delivery_score(on_time: int, receipts: int, quick_acks: int, acks: int) -> Decimal:
    """Receipts on or before the promised date, and orders acknowledged within the allowed working days."""
    on_time_part = smoothed(Decimal(on_time), Decimal(receipts))
    ack_part = smoothed(Decimal(quick_acks), Decimal(acks))
    return ON_TIME_SHARE_OF_DELIVERY * on_time_part + (ONE - ON_TIME_SHARE_OF_DELIVERY) * ack_part


def quality_score(accepted_shares_sum: Decimal, receipt_lines: int) -> Decimal:
    """The average share of each delivery that was accepted (the sum of those shares over the number of deliveries)."""
    return smoothed(accepted_shares_sum, Decimal(receipt_lines))


def combine(weights: Weights, price: Decimal, delivery: Decimal, quality: Decimal) -> Decimal:
    """The weighted average of the three, as a number from 0 to 100 with one decimal."""
    if weights.total <= 0:
        return Decimal(0)
    raw = (weights.price * price + weights.delivery * delivery + weights.quality * quality) / weights.total * 100
    return raw.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def risk_level(score: Decimal, medium_cutoff: Decimal, high_cutoff: Decimal) -> str:
    """Risk is the other side of the score: 100 less the score, with the company's cut-offs for medium and high."""
    risk = Decimal(100) - score
    if risk >= high_cutoff:
        return "high"
    if risk >= medium_cutoff:
        return "medium"
    return "low"
