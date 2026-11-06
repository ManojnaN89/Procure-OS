"""Quotes: a buyer enters what a supplier offered, the offers are compared, and the buyer awards each line.

All deterministic. The recommendation is the lowest price among offers that comply (still valid, within the minimum
order, on time, at the expected GST rate); a person confirms it with a click, or overrides it with a reason. Whether a
recommendation may be awarded without a person is the autonomy gate's decision ('rfq.award'), not this module's.
"""

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.agents import autonomy
from p2p.formulas.money import format_qty, round_money
from p2p.formulas.orders import current_gst_rate, net_unit_price
from p2p.platform import audit, worker
from p2p.platform.sod import Actor

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
                "qty": src["qty"],
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
    flags: list[str] = field(default_factory=list)

    @property
    def compliant(self) -> bool:
        return not self.flags


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
    recommended: str | None  # supplier code
    why: str  # in words
    awarded: str | None = None


@dataclass
class Comparison:
    rfq_no: str
    min_quotes: int
    quotes_received: int
    invited: int
    waiting_for: list[str]  # invited suppliers that have not replied and have not declined
    lines: list[LineComparison]
    suppliers: list[dict[str, Any]]


def _min_quotes(db: Session, category: str | None) -> int:
    if category:
        value = db.execute(text("select min_quotes from item_category where code = :c"), {"c": category}).scalar()
        if value:
            return int(value)
    default = db.execute(text("select value from org_setting where key = 'min_quotes_default'")).scalar()
    return int(default) if default else 2


def comparison(db: Session, rfq_no: str, as_of: date) -> Comparison:
    rfq = db.execute(text("select * from rfq where no = :n"), {"n": rfq_no}).mappings().first()
    if rfq is None:
        raise QuoteError(f"{rfq_no} does not exist")
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
                "select q.*, coalesce(s.trade_name, s.legal_name) as supplier_name from quote q "
                "join supplier s on s.code = q.supplier_code where q.rfq_no = :n and q.revision = "
                "(select max(revision) from quote x where x.rfq_no = q.rfq_no and x.supplier_code = q.supplier_code) "
                "order by q.supplier_code"
            ),
            {"n": rfq_no},
        ).mappings()
    ]
    qlines: dict[tuple[str, int], dict[str, Any]] = {
        (r["quote_no"], r["rfq_line_no"]): dict(r)
        for r in db.execute(
            text("select ql.* from quote_line ql join quote q on q.no = ql.quote_no where q.rfq_no = :n"), {"n": rfq_no}
        ).mappings()
    }
    awards = {
        r[0]: r[1]
        for r in db.execute(text("select rfq_line_no, supplier_code from award where rfq_no = :n"), {"n": rfq_no})
    }
    request_lines = {
        r[0]: r[1]
        for r in db.execute(text("select line_no, request_line_no from rfq_line where rfq_no = :n"), {"n": rfq_no})
    }
    result_lines: list[LineComparison] = []
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
            )
            if q["valid_until"] is not None and q["valid_until"] < as_of:
                offer.flags.append(f"expired on {q['valid_until']:%d %b %Y}")
            if ql["moq"] is not None and line["qty"] < ql["moq"]:
                offer.flags.append(f"below the supplier's minimum order of {format_qty(ql['moq'])}")
            if ql["delivery_days"] is not None and line["need_by"] is not None:
                late = (as_of + timedelta(days=ql["delivery_days"]) - line["need_by"]).days
                if late > 0:
                    offer.flags.append(f"delivers {late} day(s) after it is needed")
            if expected is not None and ql["gst_rate"] != expected:
                offer.flags.append(
                    f"GST rate {format_qty(ql['gst_rate'])}% differs from the expected {format_qty(expected)}%"
                )
            offers.append(offer)
        offers.sort(
            key=lambda o: (o.net_price, o.delivery_days if o.delivery_days is not None else 10_000, o.supplier_code)
        )
        good = [o for o in offers if o.compliant]
        if good:
            best = good[0]
            recommended, why = best.supplier_code, f"Lowest price among {len(good)} compliant offer(s)"
        elif offers:
            recommended, why = None, "No offer complies; a buyer has to decide"
        else:
            recommended, why = None, "No quotes yet"
        result_lines.append(
            LineComparison(
                rfq_line_no=line["line_no"],
                request_line_no=request_lines[line["line_no"]],
                item_code=line["item_code"],
                name=line["description"],
                qty=line["qty"],
                uom=line["uom"],
                need_by=line["need_by"],
                offers=offers,
                recommended=recommended,
                why=why,
                awarded=awards.get(line["line_no"]),
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
            "lines_quoted": sum(1 for line in lines if (q["no"], line["line_no"]) in qlines),
        }
        for q in quotes
    ]
    return Comparison(
        rfq_no=rfq_no,
        min_quotes=_min_quotes(db, rfq["category_code"]),
        quotes_received=len(quotes),
        invited=len(invited),
        waiting_for=sorted(i["supplier_code"] for i in invited if not i["responded_at"] and not i["declined"]),
        lines=result_lines,
        suppliers=suppliers,
    )


# ---- awarding ---------------------------------------------------------------------------------


@dataclass
class AwardResult:
    rfq_no: str
    awarded: dict[int, str]
    overridden: list[int]
    complete: bool  # every line of the RFQ now has an award


def award(
    db: Session,
    rfq_no: str,
    choices: dict[int, str],
    actor: Actor,
    *,
    reason: str | None = None,
    as_of: date,
    tenant: str | None = None,
) -> AwardResult:
    """Award RFQ lines to suppliers that quoted them.

    A reason is required when a choice is not the recommendation, when the chosen offer does not comply, or when fewer
    quotes came in than the category asks for. Awarding is final for a line; the purchase order follows from it.
    """
    rfq = db.execute(text("select status, request_no from rfq where no = :n for update"), {"n": rfq_no}).first()
    if rfq is None:
        raise QuoteError(f"{rfq_no} does not exist")
    if rfq[0] != "sent":
        raise QuoteError(f"{rfq_no} is {rfq[0]}; only an RFQ that is out with the suppliers can be awarded")
    if not choices:
        raise QuoteError("choose a supplier for at least one line")
    comp = comparison(db, rfq_no, as_of)
    by_line = {line.rfq_line_no: line for line in comp.lines}
    exception = comp.quotes_received < comp.min_quotes
    overridden: list[int] = []
    chosen: dict[int, Offer] = {}
    for line_no, supplier in choices.items():
        line = by_line.get(line_no)
        if line is None:
            raise QuoteError(f"line {line_no} is not on {rfq_no}")
        if line.awarded:
            raise QuoteError(f"line {line_no} is already awarded to {line.awarded}")
        offer = next((o for o in line.offers if o.supplier_code == supplier), None)
        if offer is None:
            raise QuoteError(f"{supplier} has not quoted line {line_no}")
        chosen[line_no] = offer
        if supplier != line.recommended or not offer.compliant:
            overridden.append(line_no)
    if (overridden or exception) and not (reason and reason.strip()):
        why = []
        if overridden:
            why.append("a choice is not the recommended compliant offer")
        if exception:
            why.append(f"only {comp.quotes_received} quote(s) came in and {comp.min_quotes} are wanted")
        raise QuoteError("a reason is required: " + " and ".join(why))

    for line_no, offer in chosen.items():
        db.execute(
            text(
                "insert into award (rfq_no, rfq_line_no, supplier_code, quote_no, qty, price, reason, overridden, by_user) "
                "values (:r, :l, :s, :q, :qty, :p, :why, :o, :by)"
            ),
            {
                "r": rfq_no,
                "l": line_no,
                "s": offer.supplier_code,
                "q": offer.quote_no,
                "qty": by_line[line_no].qty,
                "p": offer.net_price,
                "why": reason if line_no in overridden or exception else "Lowest price among compliant offers",
                "o": line_no in overridden,
                "by": actor.id if actor.kind == "user" else f"{actor.kind}:{actor.id}",
            },
        )
    awarded_all = db.execute(text("select count(*) from award where rfq_no = :n"), {"n": rfq_no}).scalar_one()
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
                str(k): {"supplier": o.supplier_code, "price": str(o.net_price), "quote": o.quote_no}
                for k, o in chosen.items()
            },
            "overridden": overridden,
            "complete": complete,
        },
        reason=reason,
    )
    db.commit()
    if tenant:
        worker.enqueue(tenant, "order_request", {"request_no": rfq[1]})
    return AwardResult(rfq_no, {k: o.supplier_code for k, o in chosen.items()}, overridden, complete)


def auto_award(db: Session, tenant: str, rfq_no: str, *, as_of: date) -> AwardResult | None:
    """Award the recommendations without a person, when the autonomy gate allows it and nothing is in doubt.

    Only when every invited supplier has replied or declined, enough quotes came in, and every line has a compliant
    recommendation. Otherwise nothing happens and the buyer decides.
    """
    comp = comparison(db, rfq_no, as_of)
    if comp.waiting_for or comp.quotes_received < comp.min_quotes:
        return None
    if any(line.recommended is None or line.awarded for line in comp.lines):
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
