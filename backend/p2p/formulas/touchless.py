"""Touchless purchase orders (D35): may an order be issued and sent with no buyer at all?

Only when every test passes: each line is bought on a contract or as a safe reorder, the order is within the buyer's
tier and the touchless limit, the supplier is active, no contract goes over its ceiling, each price is within the
tolerance of the requester's estimate, the budget check passes and the company's autonomy gate allows it. Every test is
written down with what it found, whether it passed or not, so the order says why it was or was not touchless.
"""

from dataclasses import dataclass
from decimal import Decimal

from p2p.formulas.buying_route import Check
from p2p.formulas.contracts import ceiling_breach
from p2p.formulas.money import format_inr
from p2p.formulas.orders import variance_pct

PRICE_LIST = (
    "price_list"  # a supplier's listed price with no contract behind it: bought like one, never touchless (DF-09)
)
TOUCHLESS_ROUTES = ("contract", "reorder")  # single source, emergency and quotation lines always wait for a buyer


@dataclass(frozen=True)
class TouchlessRule:
    version: int
    value_limit: Decimal | None  # None: the buyer's tier is the limit
    price_pct: Decimal  # how far above the estimate a price may be

    @property
    def label(self) -> str:
        return f"touchless_po v{self.version}"


@dataclass(frozen=True)
class OrderLine:
    name: str
    route: str  # contract, price_list, reorder, single_source, emergency or quotation
    price: Decimal
    estimate: Decimal | None


@dataclass(frozen=True)
class ContractCeiling:
    code: str
    max_value: Decimal | None
    used: Decimal  # ordered so far before tax, this order included


@dataclass(frozen=True)
class Facts:
    total: Decimal
    buyer_tier: bool  # the approval rule for this total needs no person: only a touchless order is approved there
    buyer_tier_max: Decimal | None  # the top of that band (the touchless band)
    supplier: str
    supplier_status: str
    lines: tuple[OrderLine, ...]
    contracts: tuple[ContractCeiling, ...]
    budget_over: str | None  # the budget check's summary when it is over, else None
    gate_allowed: bool
    gate_reason: str
    advance_pct: int = 0  # an advance agreed in the order's payment terms: never touchless (D-23)
    revised: bool = False  # changed materially after approval: a person approves it (Rev s5)
    directed: bool = False  # a person chose path A for a line (D43): a person's order is never touchless


def limit(rule: TouchlessRule, buyer_tier_max: Decimal | None) -> Decimal | None:
    """The touchless limit: the rule's own when it has one, else the top of the buyer's tier."""
    return rule.value_limit if rule.value_limit is not None else buyer_tier_max


def checks(facts: Facts, rule: TouchlessRule) -> list[Check]:
    """Every test, in order. The order is touchless only if all of them pass."""
    others = [ln for ln in facts.lines if ln.route not in TOUCHLESS_ROUTES]
    cap = limit(rule, facts.buyer_tier_max)
    priced: list[str] = []
    price_ok = True
    for ln in facts.lines:
        v = variance_pct(ln.price, ln.estimate) if ln.estimate is not None else None
        if v is None:
            price_ok = False
            priced.append(f"{ln.name}: no estimate to compare {format_inr(ln.price)} with")
        else:
            price_ok = price_ok and v <= rule.price_pct
            priced.append(f"{ln.name}: {format_inr(ln.price)} is {v}% against the estimate {format_inr(ln.estimate)}")
    over = [(c, ceiling_breach(c.max_value, c.used, Decimal(0))) for c in facts.contracts]
    return [
        Check(
            "touchless.route",
            bool(facts.lines) and not others,
            "every line is on a contract or a safe reorder"
            if not others
            else "; ".join(f"{ln.name} is {ln.route.replace('_', ' ')}: never touchless" for ln in others),
        ),
        Check(
            "touchless.tier",
            facts.buyer_tier,
            "within the touchless band" if facts.buyer_tier else f"{format_inr(facts.total)} needs an approver",
        ),
        Check(
            "touchless.value",
            cap is not None and facts.total <= cap,
            f"{format_inr(facts.total)} against the touchless limit "
            + (format_inr(cap) if cap is not None else "(none set)"),
        ),
        Check(
            "touchless.supplier",
            facts.supplier_status == "active",
            f"supplier {facts.supplier} is {facts.supplier_status}",
        ),
        Check(
            "touchless.contract",
            all(breach == 0 for _, breach in over),
            "; ".join(
                f"contract {c.code}: "
                + (
                    "no ceiling"
                    if c.max_value is None
                    else f"{format_inr(c.used)} of {format_inr(c.max_value)} used"
                    + (f", over by {format_inr(b)}" if b else "")
                )
                for c, b in over
            )
            or "no contract on the order",
        ),
        Check(
            "touchless.price",
            price_ok,
            "; ".join(priced) + f" (within {rule.price_pct.normalize():f}%)",
        ),
        Check("touchless.budget", facts.budget_over is None, facts.budget_over or "within budget"),
        Check(
            "touchless.advance",
            facts.advance_pct == 0,
            "no advance" if facts.advance_pct == 0 else f"an advance of {facts.advance_pct}% is agreed",
        ),
        Check("touchless.gate", facts.gate_allowed, f"automatic issue: {facts.gate_reason}"),
        Check(
            "touchless.directed",
            not facts.directed,
            "a person chose path A (D43)" if facts.directed else "no line was directed by a person",
        ),
        Check(
            "touchless.revision",
            not facts.revised,
            "the order was changed after approval: a person approves it"
            if facts.revised
            else "not changed after approval",
        ),
    ]


def reason(result: list[Check]) -> str:
    """One sentence: touchless, or the tests that stopped it."""
    failed = [c.detail for c in result if not c.passed]
    if not failed:
        return "Touchless: issued and sent without a buyer; " + "; ".join(c.detail for c in result)
    return "Not touchless, so a buyer issues it: " + "; ".join(failed)
