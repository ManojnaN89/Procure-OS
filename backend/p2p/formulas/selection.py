"""Selecting a supplier from quotes (plan step 6): the must-haves, the total cost compared, the weighted score, the
tie-break, and when one line is split between suppliers.

Plain comparisons and arithmetic on the numbers in the quotes and our own records; nothing here is a model's opinion
(CLAUDE.md rule 2). The method, the weights and the tie-break order come from a versioned rule (`SelectionRule`).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from itertools import combinations

from p2p.formulas.money import format_inr, format_qty, round_money

D = Decimal
ONE = D(1)
METHODS = ("total_cost", "weighted")
TIE_BREAKS = ("delivery", "vendor_score")  # earlier delivery first; higher vendor score first
REGISTERED = ("regular", "composition")  # a supplier registered for GST must have a GSTIN on file
NO_GST = ("composition", "unregistered")  # these cannot charge GST at all


@dataclass(frozen=True)
class Weights:
    price: Decimal
    delivery: Decimal
    quality: Decimal
    payment_terms: Decimal

    @property
    def total(self) -> Decimal:
        return self.price + self.delivery + self.quality + self.payment_terms

    def label(self) -> str:
        return "/".join(format_qty(w) for w in (self.price, self.delivery, self.quality, self.payment_terms))


@dataclass(frozen=True)
class SelectionRule:
    version: int
    method: str  # total_cost or weighted
    weights: Weights
    tie_break: tuple[str, ...]  # applied in order when the method's number is equal
    input_credit: bool  # the company claims GST input credit, so GST is not part of the cost compared

    @property
    def label(self) -> str:
        return f"supplier_selection v{self.version}"


def check_rule(method: str, weights: Weights, tie_break: Sequence[str]) -> list[str]:
    """What is wrong with a rule, in words (empty when it is fine)."""
    found: list[str] = []
    if method not in METHODS:
        found.append(f"the method must be one of {', '.join(METHODS)}")
    if any(w < 0 for w in (weights.price, weights.delivery, weights.quality, weights.payment_terms)):
        found.append("a weight must not be negative")
    if method == "weighted" and weights.total <= 0:
        found.append("a weighted comparison needs at least one weight above zero")
    unknown = [t for t in tie_break if t not in TIE_BREAKS]
    if unknown or len(set(tie_break)) != len(tie_break):
        found.append(f"the tie-break is a list of {', '.join(TIE_BREAKS)}, each at most once")
    return found


# ---- must-haves --------------------------------------------------------------------------------


@dataclass(frozen=True)
class OfferFacts:
    """What decides whether a quote may be selected at all."""

    as_of: date
    qty: Decimal  # the quantity asked for
    need_by: date | None
    valid_until: date | None
    delivery_days: int | None
    moq: Decimal | None
    gst_rate: Decimal
    expected_gst_rate: Decimal | None  # the item's rate on the date
    supplier_status: str | None
    approved: bool  # approved for the RFQ's category on the date
    gst_reg_type: str | None
    gstin: str | None


def days_late(as_of: date, delivery_days: int | None, need_by: date | None) -> int:
    """How many days after the need-by date the goods would arrive (0 when on time or unknown)."""
    if delivery_days is None or need_by is None:
        return 0
    return max(0, (as_of + timedelta(days=delivery_days) - need_by).days)


def must_haves(f: OfferFacts) -> list[str]:
    """Why a quote is dropped, one reason per must-have it fails. An empty list means it qualifies.

    Arriving on the need-by date is on time; a quote valid until today is still valid."""
    out: list[str] = []
    if f.valid_until is not None and f.valid_until < f.as_of:
        out.append(f"expired on {f.valid_until:%d %b %Y}")
    if f.moq is not None and f.qty < f.moq:
        out.append(f"below the supplier's minimum order of {format_qty(f.moq)}")
    late = days_late(f.as_of, f.delivery_days, f.need_by)
    if late:
        out.append(f"delivers {late} day(s) after it is needed")
    if f.expected_gst_rate is not None and f.gst_rate != f.expected_gst_rate:
        out.append(f"GST rate {format_qty(f.gst_rate)}% differs from the expected {format_qty(f.expected_gst_rate)}%")
    if f.supplier_status != "active":
        out.append(f"supplier is {f.supplier_status or 'not set up'}, not active")
    elif not f.approved:
        out.append("supplier is not approved for this category")
    if f.gst_reg_type in REGISTERED and not f.gstin:
        out.append("GST details missing: no GSTIN on file for a registered supplier")
    return out


# ---- what is compared --------------------------------------------------------------------------


def charged_rate(gst_rate: Decimal, gst_reg_type: str | None) -> Decimal:
    """The GST rate the supplier will actually charge: none for a composition or unregistered supplier."""
    return D(0) if gst_reg_type in NO_GST else gst_rate


def total_cost(value: Decimal, charges: Decimal, tax: Decimal, input_credit: bool) -> Decimal:
    """What a quote costs us: price times quantity, plus freight and other charges, plus GST only when the input credit
    cannot be claimed (claimed GST comes back, so it is shown but not counted)."""
    return value + charges + (D(0) if input_credit else tax)


def terms_score(days: int, longest: int) -> Decimal:
    """Longer credit is better: the payment days over the longest on offer (1 when nobody offers credit)."""
    if longest <= 0:
        return ONE
    return min(ONE, D(max(days, 0)) / D(longest))


def price_score(best: Decimal, cost: Decimal) -> Decimal:
    """1 for the lowest total cost; the lowest divided by this one for anything dearer."""
    if cost <= 0 or best <= 0:
        return D(0)
    return min(ONE, best / cost)


def weighted_score(w: Weights, price: Decimal, delivery: Decimal, quality: Decimal, terms: Decimal) -> Decimal:
    """The weighted average of the four parts (each 0 to 1), as 0 to 100 with one decimal."""
    if w.total <= 0:
        return D(0)
    raw = (w.price * price + w.delivery * delivery + w.quality * quality + w.payment_terms * terms) / w.total * 100
    return raw.quantize(D("0.1"), rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class Candidate:
    supplier_code: str
    cost: Decimal
    delivery_days: int | None
    vendor_score: Decimal
    score: Decimal | None = None  # only in the weighted comparison


def _tie_key(c: Candidate, tie_break: Sequence[str]) -> tuple[Decimal, ...]:
    keys: list[Decimal] = []
    for name in tie_break:
        if name == "delivery":
            keys.append(D(c.delivery_days) if c.delivery_days is not None else D(10**6))
        elif name == "vendor_score":
            keys.append(-c.vendor_score)
    return tuple(keys)


def rank(candidates: Sequence[Candidate], rule: SelectionRule) -> list[Candidate]:
    """Best first: the lowest cost (or the highest score), then the rule's tie-break in order, then the supplier code so
    the order is always the same."""

    def key(c: Candidate) -> tuple[Decimal, tuple[Decimal, ...], str]:
        primary = -(c.score or D(0)) if rule.method == "weighted" else c.cost
        return primary, _tie_key(c, rule.tie_break), c.supplier_code

    return sorted(candidates, key=key)


def tie_reason(ranked: Sequence[Candidate], rule: SelectionRule) -> str | None:
    """When the first two are equal on the method's number, which tie-break separated them, in words."""
    if len(ranked) < 2:
        return None
    a, b = ranked[0], ranked[1]
    if (a.score != b.score) if rule.method == "weighted" else (a.cost != b.cost):
        return None
    for name in rule.tie_break:
        if _tie_key(a, (name,)) != _tie_key(b, (name,)):
            words = "earlier delivery" if name == "delivery" else "higher vendor score"
            return f"tied with {b.supplier_code}, decided by {words}"
    return f"tied with {b.supplier_code}, decided by supplier code"


# ---- splitting one line between suppliers ------------------------------------------------------


@dataclass(frozen=True)
class SplitOffer:
    supplier_code: str
    net_price: Decimal
    rate_counted: Decimal  # GST percent counted in the cost (0 when the input credit is claimed)
    available: Decimal | None  # the most the supplier offers; None for the whole quantity
    moq: Decimal | None
    fixed: Decimal  # the freight and other charges that come with using this supplier for the line

    def goods(self, qty: Decimal) -> Decimal:
        value = round_money(qty * self.net_price)
        return value + round_money(value * self.rate_counted / 100)

    def covers(self, qty: Decimal) -> bool:
        return (self.available is None or self.available >= qty) and (self.moq is None or qty >= self.moq)


@dataclass(frozen=True)
class Part:
    supplier_code: str
    qty: Decimal
    goods: Decimal
    fixed: Decimal

    @property
    def total(self) -> Decimal:
        return self.goods + self.fixed


@dataclass(frozen=True)
class SplitPlan:
    parts: tuple[Part, ...]
    single: Part | None  # the cheapest single supplier for the whole quantity, if any can supply it
    saving: Decimal  # what the split saves on the goods against the single supplier
    extra_freight: Decimal  # what the split adds in freight and other charges
    worthwhile: bool  # split only when the saving beats the extra freight, or no one supplier covers the quantity

    @property
    def total(self) -> Decimal:
        return sum((p.total for p in self.parts), D(0))

    def reason(self) -> str:
        who = " and ".join(f"{p.supplier_code} {format_qty(p.qty)}" for p in self.parts)
        if self.single is None:
            return f"Split {who}: no one qualifying quote covers the whole quantity"
        verdict = "beats" if self.worthwhile else "does not beat"
        return (
            f"Split {who}: saves {format_inr(self.saving)} on the goods, which {verdict} the extra freight of "
            f"{format_inr(self.extra_freight)} (single supplier {self.single.supplier_code} at {format_inr(self.single.total)})"
        )


MAX_SPLIT_OFFERS = 8  # the cheapest offers per unit tried in a split (2^8 groups at most)


def _unit_cost(o: SplitOffer) -> tuple[Decimal, str]:
    return o.net_price * (100 + o.rate_counted), o.supplier_code


def _fill(qty: Decimal, group: Sequence[SplitOffer]) -> list[Part] | None:
    """Cover the quantity from exactly this group, cheapest per unit first, each up to what it offers. None when the
    group cannot cover it, a part is below that supplier's minimum order, or a member would get nothing (a smaller
    group does that)."""
    parts: list[Part] = []
    left = qty
    for o in sorted(group, key=_unit_cost):
        take = left if o.available is None else min(left, o.available)
        if take <= 0 or (o.moq is not None and take < o.moq):
            return None
        parts.append(Part(o.supplier_code, take, o.goods(take), o.fixed))
        left -= take
    return parts if left == 0 else None


def plan_split(qty: Decimal, offers: Sequence[SplitOffer]) -> SplitPlan | None:
    """The cheapest way to cover the quantity from two or more suppliers, and whether it is worth it.

    Every group of two or more offers is tried, each filled cheapest per unit first, and the group with the lowest total
    of goods and charges wins (fewer suppliers on a tie). A split is only worth it when its goods saving against the
    cheapest single supplier is more than the extra freight (equal is not enough), or when no one supplier offers the
    whole quantity. None when no group of two or more covers the quantity with every member used."""
    singles = [Part(o.supplier_code, qty, o.goods(qty), o.fixed) for o in offers if o.covers(qty)]
    single = min(singles, key=lambda p: (p.total, p.supplier_code)) if singles else None
    pool = sorted(offers, key=_unit_cost)[:MAX_SPLIT_OFFERS]
    best: list[Part] | None = None
    for size in range(2, len(pool) + 1):
        for group in combinations(pool, size):
            parts = _fill(qty, group)
            if parts is None:
                continue
            key = (sum((p.total for p in parts), D(0)), len(parts), [p.supplier_code for p in parts])
            if best is None or key < (sum((p.total for p in best), D(0)), len(best), [p.supplier_code for p in best]):
                best = parts
    if best is None:
        return None
    goods = sum((p.goods for p in best), D(0))
    fixed = sum((p.fixed for p in best), D(0))
    if single is None:
        return SplitPlan(tuple(best), None, D(0), D(0), True)
    saving, extra = single.goods - goods, fixed - single.fixed
    return SplitPlan(tuple(best), single, saving, extra, saving > extra)
