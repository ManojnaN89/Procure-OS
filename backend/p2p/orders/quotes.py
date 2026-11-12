"""Quotes: a buyer enters what a supplier offered, the offers are compared, and the buyer selects a supplier per line.

All deterministic (plan step 6). A quote that fails a must-have is dropped with its reasons: it delivers after the need-by
date, it has expired, the supplier is not active or not approved for the category, its GST details are missing or its
rate is not the item's, or the quantity is below its minimum order. The rest are compared on total cost (price times
quantity, freight and other charges, and GST only when the input credit cannot be claimed), or on a weighted score of
price, delivery, quality and payment terms when the company's versioned rule says so. Equal results go by the rule's
tie-break. A line may be split between suppliers only when the saving beats the extra freight (or no one quote covers
it). A person confirms the recommendation with a click; anything else, or fewer qualifying quotes than the route asked
for, needs a written reason. A price far below the others or the last price paid is a question, never a bargain taken
on trust. Whether a recommendation may be selected without a person is the autonomy gate's decision ('rfq.award').
"""

from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.agents import autonomy
from p2p.formulas import comparison as rules
from p2p.formulas import selection as sel
from p2p.formulas.money import format_inr, format_qty, round_money
from p2p.formulas.orders import current_gst_rate, net_unit_price
from p2p.formulas.scoring import NEUTRAL
from p2p.orders import selection_rule
from p2p.platform import audit, worker
from p2p.platform.sod import Actor
from p2p.suppliers import scoring

AWARD_DECISION = "rfq.award"
SERVICE = Actor("service", "award")


class QuoteError(Exception):
    pass


@dataclass
class QuoteLineInput:
    rfq_line_no: int
    unit_price: Decimal
    gst_rate: Decimal
    discount_pct: Decimal = Decimal(0)
    delivery_days: int | None = None
    offered_brand: str | None = None
    moq: Decimal | None = None
    deviation: str | None = None
    qty: Decimal | None = None  # the most the supplier offers, when less than asked; None for the whole quantity


@dataclass
class QuoteInput:
    supplier_code: str
    lines: list[QuoteLineInput]
    valid_until: date | None = None
    payment_terms_code: str | None = None
    delivery_terms: str | None = None
    packing_forwarding: Decimal = Decimal(0)
    freight: Decimal = Decimal(0)
    warranty_months: int | None = None
    deviations: str | None = None
    document_ref: str | None = None


# ---- entering a quote -------------------------------------------------------------------------


def enter_quote(db: Session, rfq_no: str, data: QuoteInput, actor: Actor, *, now: datetime | None = None) -> str:
    """Record what a supplier offered. A second quote from the same supplier is a new revision (a negotiation round)."""
    now = now or datetime.now(UTC)
    rfq = db.execute(text("select status from rfq where no = :n for update"), {"n": rfq_no}).first()
    if rfq is None:
        raise QuoteError(f"{rfq_no} does not exist")
    if rfq[0] != "sent":
        raise QuoteError(f"{rfq_no} is {rfq[0]}; quotes can only be entered while it is out with the suppliers")
    invited = db.execute(
        text("select 1 from rfq_supplier where rfq_no = :r and supplier_code = :s"),
        {"r": rfq_no, "s": data.supplier_code},
    ).first()
    if not invited:
        raise QuoteError(f"{data.supplier_code} was not asked to quote on {rfq_no}")
    if not data.lines:
        raise QuoteError("a quote needs at least one priced line")
    rfq_lines = {
        r["line_no"]: dict(r)
        for r in db.execute(text("select * from rfq_line where rfq_no = :n"), {"n": rfq_no}).mappings()
    }
    seen: set[int] = set()
    for line in data.lines:
        if line.rfq_line_no not in rfq_lines:
            raise QuoteError(f"line {line.rfq_line_no} is not on {rfq_no}")
        if line.rfq_line_no in seen:
            raise QuoteError(f"line {line.rfq_line_no} is priced twice")
        seen.add(line.rfq_line_no)
        if line.unit_price <= 0:
            raise QuoteError(f"line {line.rfq_line_no}: the price must be above zero")
        if not 0 <= line.discount_pct <= 100:
            raise QuoteError(f"line {line.rfq_line_no}: the discount must be between 0 and 100 percent")
        if not 0 <= line.gst_rate <= 100:
            raise QuoteError(f"line {line.rfq_line_no}: the GST rate must be between 0 and 100 percent")
        if line.delivery_days is not None and line.delivery_days < 0:
            raise QuoteError(f"line {line.rfq_line_no}: delivery days must not be negative")
        if line.qty is not None and not 0 < line.qty <= rfq_lines[line.rfq_line_no]["qty"]:
            raise QuoteError(f"line {line.rfq_line_no}: the quantity offered must be above zero and at most the asked")
    if data.freight < 0 or data.packing_forwarding < 0:
        raise QuoteError("freight and packing charges must not be negative")

    revision = (
        db.execute(
            text("select coalesce(max(revision), 0) from quote where rfq_no = :r and supplier_code = :s"),
            {"r": rfq_no, "s": data.supplier_code},
        ).scalar_one()
        + 1
    )
    quote_no = f"{rfq_no}-{data.supplier_code}-R{revision}"
    db.execute(
        text(
            "insert into quote (no, rfq_no, supplier_code, revision, received_at, valid_until, payment_terms_code, "
            "delivery_terms, packing_forwarding, freight, warranty_months, deviations, document_ref, entered_by) values "
            "(:no, :r, :s, :rev, :at, :vu, :pt, :dt, :pf, :fr, :w, :dev, :doc, :by)"
        ),
        {
            "no": quote_no,
            "r": rfq_no,
            "s": data.supplier_code,
            "rev": revision,
            "at": now,
            "vu": data.valid_until,
            "pt": data.payment_terms_code,
            "dt": data.delivery_terms,
            "pf": data.packing_forwarding,
            "fr": data.freight,
            "w": data.warranty_months,
            "dev": data.deviations,
            "doc": data.document_ref,
            "by": actor.id,
        },
    )
    for line in data.lines:
        src = rfq_lines[line.rfq_line_no]
        db.execute(
            text(
                "insert into quote_line (quote_no, rfq_line_no, item_code, offered_brand, qty, moq, unit_price, discount_pct, "
                "gst_rate, delivery_days, deviation) values (:q, :l, :i, :b, :qty, :moq, :p, :d, :g, :days, :dev)"
            ),
            {
                "q": quote_no,
                "l": line.rfq_line_no,
                "i": src["item_code"],
                "b": line.offered_brand,
                "qty": line.qty if line.qty is not None else src["qty"],
                "moq": line.moq,
                "p": line.unit_price,
                "d": line.discount_pct,
                "g": line.gst_rate,
                "days": line.delivery_days,
                "dev": line.deviation,
            },
        )
    db.execute(
        text(
            "update rfq_supplier set responded_at = coalesce(responded_at, :n) where rfq_no = :r and supplier_code = :s"
        ),
        {"n": now, "r": rfq_no, "s": data.supplier_code},
    )
    audit.record(
        db,
        actor,
        "quote.enter",
        "quote",
        quote_no,
        after={"rfq": rfq_no, "supplier": data.supplier_code, "revision": revision, "lines": len(data.lines)},
    )
    db.commit()
    return quote_no


# ---- comparing ---------------------------------------------------------------------------------


@dataclass
class Offer:
    supplier_code: str
    supplier_name: str
    quote_no: str
    revision: int
    unit_price: Decimal
    discount_pct: Decimal
    net_price: Decimal
    value: Decimal  # quantity times the net price, before tax
    gst_rate: Decimal
    delivery_days: int | None
    flags: list[str] = field(default_factory=list)  # the must-haves it fails: it is dropped and cannot be recommended
    charges: Decimal = Decimal(0)  # this line's share of the quote's freight and packing
    tax: Decimal = Decimal(0)  # the GST the supplier will charge (shown even when it is claimed back)
    landed: Decimal = Decimal(0)  # value + charges + tax: what the line costs to get to our door
    cost: Decimal = Decimal(0)  # what is compared: value + charges, plus the tax only when no input credit is claimed
    last_price: Decimal | None = None  # what we last paid per unit for this item
    questions: list[str] = field(default_factory=list)  # reasons to ask the supplier before trusting the price
    score: Decimal | None = None  # 0 to 100, only in the weighted comparison
    score_parts: dict[str, Decimal] | None = None  # price, delivery, quality and payment terms, each 0 to 1
    qty_offered: Decimal | None = None  # less than the line's quantity when the supplier offers only part
    payment_days: int = 0
    vendor_score: Decimal | None = None  # looked up only when needed (a weighted comparison or a tie)
    rank: int | None = None  # 1 for the best qualifying offer for the whole quantity

    @property
    def compliant(self) -> bool:
        return not self.flags

    @property
    def partial(self) -> bool:
        return self.qty_offered is not None


@dataclass
class LineComparison:
    rfq_line_no: int
    request_line_no: int
    item_code: str | None
    name: str
    qty: Decimal
    uom: str | None
    need_by: date | None
    offers: list[Offer]
    recommended: str | None  # supplier code; None when a split is recommended or nothing qualifies
    why: str  # in words
    awarded: str | None = None  # the selected supplier(s), comma separated
    cheapest: str | None = None  # the qualifying offer with the lowest total cost for the whole quantity
    qualifying: int = 0  # offers that pass every must-have
    split: sel.SplitPlan | None = None  # the cheapest way to share the line between suppliers, when there is one
    awarded_parts: list[tuple[str, Decimal]] = field(default_factory=list)

    @property
    def split_recommended(self) -> bool:
        return self.split is not None and self.split.worthwhile

    @property
    def needs_question(self) -> bool:
        """The recommended offer has a price that needs asking about: a person must decide, never the gate."""
        chosen = {p.supplier_code for p in self.split.parts} if self.split_recommended and self.split else set()
        chosen |= {self.recommended} if self.recommended else set()
        return any(o.questions for o in self.offers if o.supplier_code in chosen)


@dataclass
class Comparison:
    rfq_no: str
    min_quotes: int
    quotes_received: int
    invited: int
    waiting_for: list[str]  # invited suppliers that have not replied and have not declined
    lines: list[LineComparison]
    suppliers: list[dict[str, Any]]
    rule: str = ""  # the selection rule version the comparison used
    basis: str = ""  # what is compared, in words


def _min_quotes(db: Session, category: str | None) -> int:
    if category:
        value = db.execute(text("select min_quotes from item_category where code = :c"), {"c": category}).scalar()
        if value:
            return int(value)
    default = db.execute(text("select value from org_setting where key = 'min_quotes_default'")).scalar()
    return int(default) if default else 2


def _org_setting(db: Session, key: str, default: str) -> str:
    value = db.execute(text("select value from org_setting where key = :k"), {"k": key}).scalar()
    return str(value) if value not in (None, "") else default


def last_prices(db: Session, item_codes: list[str]) -> dict[str, Decimal]:
    """What we last paid per unit, net of discount, for each item that has been ordered before."""
    if not item_codes:
        return {}
    rows = db.execute(
        text(
            "select distinct on (pl.item_code) pl.item_code, pl.unit_price from po_line pl join po p on p.no = pl.po_no "
            "where pl.item_code = any(:c) and p.status not in ('draft', 'cancelled') and pl.status <> 'cancelled' "
            "order by pl.item_code, p.created_at desc"
        ),
        {"c": item_codes},
    )
    return {r[0]: r[1] for r in rows}


def _input_credit(db: Session, rule: sel.SelectionRule, request_no: str | None) -> bool:
    """GST is claimed back when the rule says the company claims it and the delivery place is registered for GST."""
    if not rule.input_credit:
        return False
    gstin = (
        db.execute(
            text("select l.gstin from request r join location l on l.code = r.ship_to_location where r.no = :n"),
            {"n": request_no},
        ).scalar()
        or db.execute(text("select gstin from gst_registration where is_default limit 1")).scalar()
    )
    return bool(gstin)


class _VendorScores:
    """Vendor scores from our own records, read once and only when a comparison needs them."""

    def __init__(self, db: Session, as_of: date) -> None:
        self.db, self.as_of = db, as_of
        self._scores: dict[str, scoring.SupplierScore] | None = None

    def get(self, code: str) -> scoring.SupplierScore | None:
        if self._scores is None:
            self._scores = scoring.score_all(self.db, self.as_of)
        return self._scores.get(code)


def _rank_line(good: list[Offer], rule: sel.SelectionRule, vendors: _VendorScores) -> tuple[list[Offer], str | None]:
    """Order the qualifying offers for the whole quantity, best first, and say how a tie was broken."""
    costs = [o.cost for o in good]
    tie = len(costs) != len(set(costs))
    if rule.method == "weighted" or tie:
        for o in good:
            found = vendors.get(o.supplier_code)
            o.vendor_score = found.total if found else Decimal(0)
    if rule.method == "weighted" and good:
        best = min(costs)
        longest_terms = max(o.payment_days for o in good)  # like the price, scaled among the qualifying offers only
        for o in good:
            found = vendors.get(o.supplier_code)
            parts = {
                "price": sel.price_score(best, o.cost),
                "delivery": found.delivery if found else NEUTRAL,
                "quality": found.quality if found else NEUTRAL,
                "payment_terms": sel.terms_score(o.payment_days, longest_terms),
            }
            o.score_parts = parts
            o.score = sel.weighted_score(
                rule.weights, parts["price"], parts["delivery"], parts["quality"], parts["payment_terms"]
            )
    candidates = {
        o.supplier_code: sel.Candidate(o.supplier_code, o.cost, o.delivery_days, o.vendor_score or Decimal(0), o.score)
        for o in good
    }
    ranked = sel.rank(list(candidates.values()), rule)
    order = {c.supplier_code: i for i, c in enumerate(ranked)}
    good.sort(key=lambda o: order[o.supplier_code])
    for i, o in enumerate(good, start=1):
        o.rank = i
    return good, sel.tie_reason(ranked, rule)


def _split_offer(o: Offer, moq: Decimal | None, gst_reg_type: str | None, credit: bool) -> sel.SplitOffer:
    """An offer as the split arithmetic sees it: GST counted on the goods and the charges only without input credit."""
    rate = Decimal(0) if credit else sel.charged_rate(o.gst_rate, gst_reg_type)
    return sel.SplitOffer(
        o.supplier_code, o.net_price, rate, o.qty_offered, moq, o.charges + round_money(o.charges * rate / 100)
    )


def _why(best: Offer, n: int, rule: sel.SelectionRule, basis: str, tie: str | None) -> str:
    """Why this offer is recommended, in words, from the numbers that decided it (a template, no model)."""
    if rule.method == "weighted":
        pct = {k: int((v * 100).to_integral_value()) for k, v in (best.score_parts or {}).items()}
        why = (
            f"Best score ({best.score} of 100) among {n} qualifying offer(s): price {pct.get('price')}, "
            f"delivery {pct.get('delivery')}, quality {pct.get('quality')}, payment terms {pct.get('payment_terms')}; "
            f"weights {rule.weights.label()}"
        )
    else:
        why = f"Lowest total cost among {n} qualifying offer(s): {format_inr(best.cost)} ({basis})"
    return f"{why}; {tie}" if tie else why


def comparison(db: Session, rfq_no: str, as_of: date) -> Comparison:
    rfq = db.execute(text("select * from rfq where no = :n"), {"n": rfq_no}).mappings().first()
    if rfq is None:
        raise QuoteError(f"{rfq_no} does not exist")
    rule = selection_rule.rule_for(db, as_of)
    credit = _input_credit(db, rule, rfq["request_no"])
    basis = (
        "price and freight; GST is claimed back, so it is shown but not counted" if credit else "price, freight and GST"
    )
    lines = [
        dict(r)
        for r in db.execute(
            text(
                "select l.*, i.gst_rate_before, i.gst_rate_after, i.gst_rate_change_date from rfq_line l "
                "left join item i on i.code = l.item_code where l.rfq_no = :n order by l.line_no"
            ),
            {"n": rfq_no},
        ).mappings()
    ]
    quotes = [
        dict(r)
        for r in db.execute(
            text(
                "select q.*, coalesce(s.trade_name, s.legal_name) as supplier_name, s.status as supplier_status, "
                "s.gst_reg_type, s.gstin, coalesce((select t.days from payment_terms t "
                "where t.code = coalesce(q.payment_terms_code, s.payment_terms_code)), 0) as payment_days, "
                "(cast(:cat as text) is null or exists (select 1 from supplier_category c where c.supplier_code = s.code "
                "and c.category_code = :cat and (c.approved_from is null or c.approved_from <= :d))) as approved "
                "from quote q join supplier s on s.code = q.supplier_code where q.rfq_no = :n and q.revision = "
                "(select max(revision) from quote x where x.rfq_no = q.rfq_no and x.supplier_code = q.supplier_code) "
                "order by q.supplier_code"
            ),
            {"n": rfq_no, "cat": rfq["category_code"], "d": as_of},
        ).mappings()
    ]
    qlines: dict[tuple[str, int], dict[str, Any]] = {
        (r["quote_no"], r["rfq_line_no"]): dict(r)
        for r in db.execute(
            text("select ql.* from quote_line ql join quote q on q.no = ql.quote_no where q.rfq_no = :n"), {"n": rfq_no}
        ).mappings()
    }
    awards: dict[int, list[tuple[str, Decimal]]] = defaultdict(list)
    for line_no, supplier, qty in db.execute(
        text("select rfq_line_no, supplier_code, qty from award where rfq_no = :n order by qty desc, supplier_code"),
        {"n": rfq_no},
    ):
        awards[line_no].append((supplier, qty))
    threshold = Decimal(_org_setting(db, "low_price_flag_pct", "40"))
    last = last_prices(db, sorted({line["item_code"] for line in lines if line["item_code"]}))
    vendors = _VendorScores(db, as_of)
    per_line: dict[int, list[Offer]] = {}
    of_quote: dict[str, list[Offer]] = defaultdict(list)  # a quote's offers, in line order
    for line in lines:
        expected = current_gst_rate(
            line["gst_rate_before"], line["gst_rate_after"], line["gst_rate_change_date"], as_of
        )
        offers: list[Offer] = []
        for q in quotes:
            ql = qlines.get((q["no"], line["line_no"]))
            if ql is None:
                continue
            net = net_unit_price(ql["unit_price"], ql["discount_pct"])
            offered = ql["qty"] if ql["qty"] is not None and ql["qty"] < line["qty"] else None
            offer = Offer(
                supplier_code=q["supplier_code"],
                supplier_name=q["supplier_name"],
                quote_no=q["no"],
                revision=q["revision"],
                unit_price=ql["unit_price"],
                discount_pct=ql["discount_pct"],
                net_price=net,
                value=round_money(line["qty"] * net),
                gst_rate=ql["gst_rate"],
                delivery_days=ql["delivery_days"],
                last_price=last.get(line["item_code"]),
                qty_offered=offered,
                payment_days=int(q["payment_days"]),
            )
            offer.flags = sel.must_haves(
                sel.OfferFacts(
                    as_of=as_of,
                    qty=line["qty"],
                    need_by=line["need_by"],
                    valid_until=q["valid_until"],
                    delivery_days=ql["delivery_days"],
                    moq=ql["moq"],
                    gst_rate=ql["gst_rate"],
                    expected_gst_rate=expected,
                    supplier_status=q["supplier_status"],
                    approved=bool(q["approved"]),
                    gst_reg_type=q["gst_reg_type"],
                    gstin=q["gstin"],
                )
            )
            offers.append(offer)
            of_quote[q["no"]].append(offer)
        per_line[line["line_no"]] = offers
    # the freight and packing of a quote are one amount for the whole quote: share them over the lines it prices
    registration = {q["no"]: q["gst_reg_type"] for q in quotes}
    for q in quotes:
        mine = of_quote.get(q["no"], [])
        shares = rules.allocate(q["freight"] + q["packing_forwarding"], [o.value for o in mine])
        for o, share in zip(mine, shares, strict=True):
            landed = rules.landed_cost(o.value, share, sel.charged_rate(o.gst_rate, registration[q["no"]]))
            o.charges, o.tax, o.landed = landed.charges, landed.tax, landed.total
            o.cost = sel.total_cost(o.value, o.charges, o.tax, credit)
    result_lines: list[LineComparison] = []
    for line in lines:
        offers = per_line[line["line_no"]]
        for o in offers:
            others = [x.net_price for x in offers if x is not o]
            o.questions = rules.price_questions(o.net_price, o.last_price, others, threshold)
        qualifying = [o for o in offers if o.compliant]
        whole = [o for o in qualifying if not o.partial]
        ranked, tie = _rank_line(whole, rule, vendors)
        in_ranking = {id(o) for o in ranked}
        rest = sorted(  # offers for part of the quantity, then those dropped
            (o for o in offers if id(o) not in in_ranking), key=lambda o: (not o.compliant, o.cost, o.supplier_code)
        )
        offers[:] = [*ranked, *rest]
        plan = sel.plan_split(
            line["qty"],
            [_split_offer(o, qlines[(o.quote_no, line["line_no"])]["moq"], registration[o.quote_no], credit)
             for o in qualifying],
        )  # fmt: skip
        by_cost = sel.rank(  # the cheapest qualifying offer, equal totals settled by the rule's tie-break
            [sel.Candidate(o.supplier_code, o.cost, o.delivery_days, o.vendor_score or Decimal(0)) for o in whole],
            replace(rule, method="total_cost"),
        )
        cheapest = by_cost[0].supplier_code if by_cost else None
        recommended: str | None = None
        if plan is not None and plan.worthwhile:
            why = plan.reason()
        elif ranked:
            recommended = ranked[0].supplier_code
            why = _why(ranked[0], len(qualifying), rule, basis, tie)
            if ranked[0].questions:
                why += f"; ask the supplier first: price is {ranked[0].questions[0]}"
        elif offers:
            why = "No offer qualifies; a buyer has to decide"
        else:
            why = "No quotes yet"
        parts = awards.get(line["line_no"], [])
        result_lines.append(
            LineComparison(
                rfq_line_no=line["line_no"],
                request_line_no=line["request_line_no"],
                item_code=line["item_code"],
                name=line["description"],
                qty=line["qty"],
                uom=line["uom"],
                need_by=line["need_by"],
                offers=offers,
                recommended=recommended,
                why=why,
                awarded=", ".join(s for s, _ in parts) or None,
                cheapest=cheapest,
                qualifying=len(qualifying),
                split=plan,
                awarded_parts=parts,
            )
        )
    invited = [
        dict(r)
        for r in db.execute(
            text("select supplier_code, responded_at, declined from rfq_supplier where rfq_no = :n"), {"n": rfq_no}
        ).mappings()
    ]
    suppliers = [
        {
            "supplier_code": q["supplier_code"],
            "supplier_name": q["supplier_name"],
            "quote_no": q["no"],
            "revision": q["revision"],
            "valid_until": q["valid_until"],
            "freight": q["freight"],
            "packing_forwarding": q["packing_forwarding"],
            "payment_terms_code": q["payment_terms_code"],
            "payment_days": q["payment_days"],
            "lines_quoted": sum(1 for line in lines if (q["no"], line["line_no"]) in qlines),
        }
        for q in quotes
    ]
    return Comparison(
        rfq_no=rfq_no,
        min_quotes=rfq["min_quotes"] or _min_quotes(db, rfq["category_code"]),  # the buying route's, when it set one
        quotes_received=len(quotes),
        invited=len(invited),
        waiting_for=sorted(i["supplier_code"] for i in invited if not i["responded_at"] and not i["declined"]),
        lines=result_lines,
        suppliers=suppliers,
        rule=rule.label,
        basis=basis,
    )


# ---- selecting --------------------------------------------------------------------------------


@dataclass
class AwardResult:
    rfq_no: str
    awarded: dict[int, str]  # line to supplier(s), comma separated for a split
    overridden: list[int]
    complete: bool  # every line of the RFQ now has a selection


Parts = list[tuple[str, Decimal]]


def _parts_of_choice(line: LineComparison, supplier: str) -> Parts:
    offer = next((o for o in line.offers if o.supplier_code == supplier), None)
    if offer is None:
        raise QuoteError(f"{supplier} has not quoted line {line.rfq_line_no}")
    if offer.partial:
        raise QuoteError(
            f"{supplier} offers only {format_qty(offer.qty_offered)} of {format_qty(line.qty)} on line "
            f"{line.rfq_line_no}; split the line"
        )
    return [(supplier, line.qty)]


def _check_split(line: LineComparison, parts: Parts) -> None:
    if len({s for s, _ in parts}) != len(parts) or len(parts) < 2:
        raise QuoteError(f"line {line.rfq_line_no}: a split names two or more different suppliers")
    for supplier, qty in parts:
        offer = next((o for o in line.offers if o.supplier_code == supplier), None)
        if offer is None:
            raise QuoteError(f"{supplier} has not quoted line {line.rfq_line_no}")
        if qty <= 0 or (offer.qty_offered is not None and qty > offer.qty_offered):
            raise QuoteError(f"line {line.rfq_line_no}: {supplier} cannot take {format_qty(qty)}")
    if sum((q for _, q in parts), Decimal(0)) != line.qty:
        raise QuoteError(f"line {line.rfq_line_no}: the split must add up to {format_qty(line.qty)}")


def _is_recommended(line: LineComparison, parts: Parts) -> bool:
    """The choice is the recommendation: the cheapest qualifying offer, or the split the comparison recommends."""
    if line.split_recommended and line.split is not None:
        return sorted(parts) == sorted((p.supplier_code, p.qty) for p in line.split.parts)
    if len(parts) != 1:
        return False
    supplier = parts[0][0]
    offer = next(o for o in line.offers if o.supplier_code == supplier)
    return offer.compliant and supplier == line.recommended and supplier == line.cheapest


def award(
    db: Session,
    rfq_no: str,
    choices: dict[int, str],
    actor: Actor,
    *,
    reason: str | None = None,
    as_of: date,
    tenant: str | None = None,
    splits: dict[int, Parts] | None = None,
) -> AwardResult:
    """Select a supplier for RFQ lines (or share a line between suppliers, `splits`: line to supplier and quantity).

    A written reason is required when a choice is not the recommended, cheapest qualifying offer (or the recommended
    split), and when a line has fewer qualifying quotes than the route asked for. A selection is final for a line; the
    purchase orders follow from it, one per supplier.
    """
    rfq = db.execute(text("select status, request_no from rfq where no = :n for update"), {"n": rfq_no}).first()
    if rfq is None:
        raise QuoteError(f"{rfq_no} does not exist")
    if rfq[0] != "sent":
        raise QuoteError(f"{rfq_no} is {rfq[0]}; a supplier can only be selected while the RFQ is out")
    wanted: dict[int, Parts] = {**{k: [(v, Decimal(0))] for k, v in choices.items()}, **(splits or {})}
    if not wanted:
        raise QuoteError("choose a supplier for at least one line")
    comp = comparison(db, rfq_no, as_of)
    by_line = {line.rfq_line_no: line for line in comp.lines}
    overridden: list[int] = []
    short: list[str] = []
    chosen: dict[int, list[tuple[Offer, Decimal]]] = {}
    for line_no, asked in wanted.items():
        line = by_line.get(line_no)
        if line is None:
            raise QuoteError(f"line {line_no} is not on {rfq_no}")
        if line.awarded:
            raise QuoteError(f"line {line_no} already has a supplier selected: {line.awarded}")
        if splits and line_no in splits:
            parts = list(asked)
            _check_split(line, parts)
        else:
            parts = _parts_of_choice(line, asked[0][0])
        offers = {o.supplier_code: o for o in line.offers}
        chosen[line_no] = [(offers[s], q) for s, q in parts]
        if not _is_recommended(line, parts) or not all(offers[s].compliant for s, _ in parts):
            overridden.append(line_no)
        if line.qualifying < comp.min_quotes:
            dropped = len(line.offers) - line.qualifying
            short.append(
                f"line {line_no}: only {line.qualifying} quote(s) came in and {comp.min_quotes} are wanted"
                + (f" ({dropped} more did not qualify)" if dropped else "")
            )
    if (overridden or short) and not (reason and reason.strip()):
        why = []
        if overridden:
            why.append("a choice is not the recommended, cheapest qualifying offer")
        why.extend(short)
        raise QuoteError("a reason is required: " + " and ".join(why))

    who = actor.id if actor.kind == "user" else f"{actor.kind}:{actor.id}"
    for line_no, picked in chosen.items():
        for offer, qty in picked:
            db.execute(
                text(
                    "insert into award (rfq_no, rfq_line_no, supplier_code, quote_no, qty, price, reason, overridden, "
                    "by_user, split, selection_rule) values (:r, :l, :s, :q, :qty, :p, :why, :o, :by, :split, :rule)"
                ),
                {
                    "r": rfq_no,
                    "l": line_no,
                    "s": offer.supplier_code,
                    "q": offer.quote_no,
                    "qty": qty,
                    "p": offer.net_price,
                    "why": reason if line_no in overridden or short else by_line[line_no].why,
                    "o": line_no in overridden,
                    "by": who,
                    "split": len(picked) > 1,
                    "rule": comp.rule,
                },
            )
    awarded_all = db.execute(
        text("select count(distinct rfq_line_no) from award where rfq_no = :n"), {"n": rfq_no}
    ).scalar_one()
    complete = awarded_all == len(comp.lines)
    if complete:
        db.execute(text("update rfq set status = 'awarded' where no = :n"), {"n": rfq_no})
    audit.record(
        db,
        actor,
        "rfq.award",
        "rfq",
        rfq_no,
        after={
            "awards": {
                str(k): [
                    {"supplier": o.supplier_code, "qty": format_qty(q), "price": str(o.net_price), "quote": o.quote_no}
                    for o, q in picked
                ]
                for k, picked in chosen.items()
            },
            "ranking": {
                str(k): [
                    {
                        "supplier": o.supplier_code,
                        "rank": o.rank,
                        "cost": str(o.cost),
                        "score": o.score,
                        "dropped": o.flags,
                    }
                    for o in by_line[k].offers
                ]
                for k in chosen
            },
            "why": {str(k): by_line[k].why for k in chosen},
            "overridden": overridden,
            "too_few_quotes": short,
            "complete": complete,
        },
        reason=reason,
        rule_version=comp.rule,
    )
    db.commit()
    if tenant:
        worker.enqueue(tenant, "order_request", {"request_no": rfq[1]})
    return AwardResult(
        rfq_no, {k: ", ".join(o.supplier_code for o, _ in picked) for k, picked in chosen.items()}, overridden, complete
    )


def auto_award(db: Session, tenant: str, rfq_no: str, *, as_of: date) -> AwardResult | None:
    """Select the recommendations without a person, when the autonomy gate allows it and nothing is in doubt.

    Only when every invited supplier has replied or declined, and every line has enough qualifying quotes and a single
    recommended offer that is also the cheapest and raises no price question. Otherwise the buyer decides.
    """
    comp = comparison(db, rfq_no, as_of)
    if comp.waiting_for or comp.quotes_received < comp.min_quotes:
        return None
    for line in comp.lines:
        if line.recommended is None or line.awarded or line.needs_question:
            return None
        if line.recommended != line.cheapest or line.qualifying < comp.min_quotes:
            return None
    value = sum(
        (o.value for line in comp.lines for o in line.offers if o.supplier_code == line.recommended), Decimal(0)
    )
    gate = autonomy.gate(db, AWARD_DECISION, value=value, confidence=1.0)  # a rule picked it, not a model's guess
    if not gate.allowed:
        return None
    result = award(
        db,
        rfq_no,
        {line.rfq_line_no: str(line.recommended) for line in comp.lines},
        SERVICE,
        as_of=as_of,
        tenant=tenant,
    )
    autonomy.record_outcome(db, AWARD_DECISION, auto=True, ref=rfq_no)
    return result
