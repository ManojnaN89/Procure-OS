"""Synthetic supplier emails with known answers, in many styles, for the AI evals.

Every case says what a careful reader should get from the email. Nothing here calls a model.
"""

import random
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction

import lib
from lib import D, rows


@dataclass
class Item:
    line_no: int
    code: str
    name: str
    qty: Decimal
    est: Decimal
    gst: Decimal  # the item's expected rate


@dataclass
class Truth:
    intent: str  # quote, decline or question
    lines: dict[int, dict] = field(
        default_factory=dict
    )  # line_no -> price (before GST), discount, gst (as stated; None if not), days
    valid_until: str | None = None
    freight: Decimal | None = None
    packing: Decimal | None = None
    question: str | None = None


@dataclass
class Case:
    id: str
    style: str
    items: list[Item]
    body: str
    truth: Truth
    tricky: bool = False  # a case where a careless reader goes wrong


def items_for_cases(n: int = 3) -> list[Item]:
    """A few real items with real estimates and GST rates, from the dataset."""
    from datetime import date

    from p2p.formulas.orders import current_gst_rate

    cat = [
        r
        for r in rows("master", "item")
        if r["category_code"] == "MRO" and r["stocked"] == "true" and r["standard_price"]
    ]
    out = []
    for i, r in enumerate(cat[: n * 6]):
        change = date.fromisoformat(r["gst_rate_change_date"]) if r["gst_rate_change_date"] else None
        rate = current_gst_rate(
            D(r["gst_rate_before"]) if r["gst_rate_before"] else None,
            D(r["gst_rate_after"]) if r["gst_rate_after"] else None,
            change,
            lib.TODAY,
        ) or Decimal(18)
        out.append(
            Item(
                0,
                r["code"],
                r["name"],
                Decimal(5 + 3 * i),
                D(r["standard_price"]),
                rate,
            )
        )
    return out


def inr(value: Decimal, style: int) -> str:
    """The same amount written the ways suppliers write it."""
    s = f"{value:.2f}"
    whole, dec = s.split(".")
    if style == 0:
        return f"Rs {whole}.{dec}"
    if style == 1:
        return f"₹{indian(whole)}"  # whole rupees, Indian grouping
    if style == 2:
        return f"INR {whole}.{dec}"
    if style == 3:
        return f"Rs.{whole}/-"
    return f"{indian(whole)}.{dec}/-"


def indian(whole: str) -> str:
    if len(whole) <= 3:
        return whole
    head, tail = whole[:-3], whole[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return ",".join([*parts, tail])


def price_for(rng: random.Random, it: Item, whole: bool = False) -> Decimal:
    p = (it.est * Decimal(str(round(rng.uniform(0.82, 1.18), 3)))).quantize(
        Decimal(1) if whole else Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    return p or Decimal(1)


NOISE = (
    "\n\n--\nRegards,\nSales Desk\nAlpha Industrial Corporation Pvt Ltd\nPlot 14, Phase 2, MIDC, Pune 411019\n"
    "Ph: +91 98765 43210 | 020-2712 3456 | GSTIN: 27AAAAA1234A1Z5 | PAN: AAAAA1234A | CIN U12345MH2010PTC123456\n"
    "Bank: HDFC A/c 50200012345678 IFSC HDFC0001234. Our ref: Q/2026/117 dated 03/11/2026. "
    "This email and attachments are confidential. If you are not the intended recipient please delete it. "
    "Registered office: 14 Phase 2. Terms: payment 30 days. Please quote Q/2026/117 in your PO.\n"
)


def exclusive(incl: Decimal, rate: Decimal) -> Decimal:
    x = Fraction(incl) * 100 / (100 + Fraction(rate))
    return Decimal(((x * 10000) + Fraction(1, 2)).__floor__()) / Decimal(10000)


def build_cases(seed: int = 4, quick: bool = False) -> list[Case]:
    rng = random.Random(seed)
    pool = items_for_cases()
    cases: list[Case] = []
    counter = 0

    def pick(k: int) -> list[Item]:
        chosen = rng.sample(pool, k)
        return [Item(n, it.code, it.name, it.qty, it.est, it.gst) for n, it in enumerate(chosen, start=1)]

    def add(style: str, items: list[Item], body: str, truth: Truth, tricky: bool = False) -> None:
        nonlocal counter
        counter += 1
        cases.append(Case(f"{style}-{counter:02d}", style, items, body, truth, tricky))

    for _ in range(3):  # plain sentences, GST extra, one to three items
        items = pick(rng.randint(1, 3))
        fmt = rng.randint(0, 4)
        lines, truth = [], Truth("quote")
        for it in items:
            p, days = price_for(rng, it), rng.choice([3, 5, 7, 10])
            lines.append(f"{it.name} - {inr(p, fmt)} per piece, GST {it.gst:f}% extra. Delivery within {days} days.")
            truth.lines[it.line_no] = {
                "price": p,
                "discount": None,
                "gst": it.gst,
                "days": days,
            }
        add(
            "sentences",
            items,
            "Dear Sir,\nThank you for your enquiry. Our best rates are as follows:\n"
            + "\n".join(lines)
            + "\n\nThanks & regards\nSales",
            truth,
        )

    for _ in range(3):  # a table written as text
        items = pick(3)
        truth, rows_txt = Truth("quote"), []
        for it in items:
            p, days = price_for(rng, it), rng.choice([4, 6, 8])
            rows_txt.append(f"{it.line_no} | {it.name} | {it.qty:f} | {p} | {it.gst:f}% | {days} days")
            truth.lines[it.line_no] = {
                "price": p,
                "discount": None,
                "gst": it.gst,
                "days": days,
            }
        add(
            "table",
            items,
            "S.No | Item | Qty | Rate (Rs) | GST | Lead time\n" + "\n".join(rows_txt) + "\nPrices exclusive of taxes.",
            truth,
        )

    for _ in range(3):  # by item code only
        items = pick(2)
        truth, parts = Truth("quote"), []
        for it in items:
            p = price_for(rng, it)
            parts.append(f"{it.code}: Rs {p} + {it.gst:f}% GST")
            truth.lines[it.line_no] = {
                "price": p,
                "discount": None,
                "gst": it.gst,
                "days": None,
            }
        add(
            "by-code",
            items,
            "Hi,\nRates for your requirement:\n" + "\n".join(parts) + "\nDelivery ex-stock.",
            truth,
        )

    for _ in range(3):  # a discount stated separately: the model must not compute the net
        items = pick(2)
        truth, parts = Truth("quote"), []
        for it in items:
            p, disc = price_for(rng, it), rng.choice([5, 10, 12])
            parts.append(f"{it.name}: list price Rs {p}, our discount {disc}%, GST {it.gst:f}% extra.")
            truth.lines[it.line_no] = {
                "price": p,
                "discount": Decimal(disc),
                "gst": it.gst,
                "days": 7,
            }
        add(
            "discount",
            items,
            "Dear Buyer,\n" + "\n".join(parts) + "\nDelivery 7 days from PO.",
            truth,
            tricky=True,
        )

    for _ in range(3):  # GST included: the price is converted by code, not by the model
        items = pick(2)
        truth, parts = Truth("quote"), []
        for it in items:
            incl = (price_for(rng, it) * (1 + it.gst / 100)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            parts.append(f"{it.name} @ Rs {incl} per piece inclusive of {it.gst:f}% GST.")
            truth.lines[it.line_no] = {
                "price": exclusive(incl, it.gst),
                "discount": None,
                "gst": it.gst,
                "days": 5,
                "incl": True,
                "written": incl,
            }
        add(
            "gst-included",
            items,
            "Dear Sir,\n" + "\n".join(parts) + "\nDelivery 5 days.",
            truth,
            tricky=True,
        )

    for _ in range(3):  # a partial quote
        items = pick(3)
        truth = Truth("quote")
        it = items[rng.randint(0, 2)]
        p = price_for(rng, it)
        truth.lines[it.line_no] = {
            "price": p,
            "discount": None,
            "gst": it.gst,
            "days": 6,
        }
        others = ", ".join(i.name for i in items if i is not it)
        add(
            "partial",
            items,
            f"Dear Sir,\nWe can offer {it.name} at Rs {p} each plus {it.gst:f}% GST, delivery 6 days. We do not stock {others}.",
            truth,
        )

    for _ in range(4):  # noise full of other numbers
        items = pick(2)
        truth, parts = Truth("quote"), []
        for it in items:
            p, days = price_for(rng, it), rng.choice([5, 9])
            parts.append(f"{it.name} Rs {p} per piece, GST {it.gst:f}% extra, {days} days")
            truth.lines[it.line_no] = {
                "price": p,
                "discount": None,
                "gst": it.gst,
                "days": days,
            }
        add(
            "noisy",
            items,
            "Dear Sir,\nPlease find our offer:\n" + "\n".join(parts) + NOISE,
            truth,
            tricky=True,
        )

    for kind in (
        "date",
        "relative",
        "date",
        "relative",
    ):  # validity: a date is read; 'valid for 30 days' must not be turned into a date
        items = pick(1)
        it = items[0]
        p = price_for(rng, it)
        truth = Truth(
            "quote",
            {it.line_no: {"price": p, "discount": None, "gst": it.gst, "days": 5}},
        )
        if kind == "date":
            truth.valid_until = "2026-12-31"
            validity = "Prices valid till 31 Dec 2026."
        else:
            validity = "This offer is valid for 30 days."
        add(
            f"validity-{kind}",
            items,
            f"Dear Sir,\n{it.name}: Rs {p} each, GST {it.gst:f}% extra, delivery 5 days. {validity}",
            truth,
            tricky=(kind == "relative"),
        )

    for _ in range(3):  # freight and packing
        items = pick(1)
        it = items[0]
        p = price_for(rng, it)
        fr, pk = Decimal(rng.choice([250, 500, 750])), Decimal(rng.choice([100, 150]))
        truth = Truth(
            "quote",
            {it.line_no: {"price": p, "discount": None, "gst": it.gst, "days": 4}},
            freight=fr,
            packing=pk,
        )
        add(
            "charges",
            items,
            f"{it.name} rate Rs {p} + GST {it.gst:f}%. Freight Rs {fr} extra and packing & forwarding Rs {pk} extra. Delivery 4 days.",
            truth,
        )

    for _ in range(2):  # a total for the whole quantity, no unit price
        items = pick(1)
        it = items[0]
        total = (price_for(rng, it) * it.qty).quantize(Decimal("0.01"))
        add(
            "total-only",
            items,
            f"Dear Sir, for {it.qty:f} nos of {it.name} our total price is Rs {total} plus GST. Delivery 5 days.",
            Truth("quote", {}),
            tricky=True,
        )

    for _ in range(2):  # GST not stated
        items = pick(1)
        it = items[0]
        p = price_for(rng, it)
        add(
            "no-gst-stated",
            items,
            f"{it.name} Rs {p} per piece. Delivery 8 days.",
            Truth(
                "quote",
                {it.line_no: {"price": p, "discount": None, "gst": None, "days": 8}},
            ),
            tricky=True,
        )

    for _ in range(2):  # Hinglish and loose wording
        items = pick(1)
        it = items[0]
        p, days = price_for(rng, it, whole=True), rng.choice([3, 5])
        add(
            "loose-wording",
            items,
            f"sir {it.name} ka rate {p}/- per piece hai, gst {it.gst:f}% alag se lagega, delivery {days} din mein ho jayegi",
            Truth(
                "quote",
                {
                    it.line_no: {
                        "price": p,
                        "discount": None,
                        "gst": it.gst,
                        "days": days,
                    }
                },
            ),
        )

    for reason in (
        "the item has been discontinued",
        "we do not deal in this category any more",
    ):
        items = pick(2)
        add(
            "decline",
            items,
            f"Dear Sir,\nThank you for the enquiry. Regret to inform you that we are unable to quote because {reason}.\n\nRegards"
            + NOISE[:120],
            Truth("decline"),
        )

    for q in (
        "can you accept delivery in two lots",
        "is the quantity firm or can it change",
    ):
        items = pick(2)
        add(
            "question",
            items,
            f"Dear Sir,\nBefore we quote, please confirm: {q}? We will send rates after your reply.\nRegards",
            Truth("question", question=q),
        )
    return cases if not quick else cases[::2]
