"""What an order's versions say (task 0.2, D48) and what an approval covers (Q19, DF-07, DF-08).

Each version of an order is kept as a snapshot: the header (supplier, delivery place and our GSTIN, payment and delivery
terms, contract, totals and tax), its lines (with the contract price version a contract line was bought at) and its
charges. `diff` says what changed between any two versions.

A change is measured from the last approved version, never from the version before it, so small changes add up: two
2-day slips are a 4-day slip. Material: a price up, a quantity above the approved one, an item changed or a new line,
another supplier, delivery location or payment terms (which carry any advance), a date moved beyond the tolerance either
way, a total above the band that approved it, or any higher total. Anything else (delivery terms included, Q19) is a new
version the supplier acknowledges again, with the approval carried.
"""

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from p2p.formulas.money import format_inr
from p2p.formulas.words import count


def _dec(value: Any) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _day(value: Any) -> date | None:
    return date.fromisoformat(str(value)) if value else None


def _text(value: Any) -> str | None:
    return None if value is None else str(value)


def _int(value: Any) -> int | None:
    return None if value is None else int(value)


@dataclass(frozen=True)
class LineState:
    line_no: int
    item_code: str | None
    qty: Decimal
    unit_price: Decimal
    need_by: date | None
    # what the document says about the line; optional, so a block 4 snapshot (the five fields above) still loads
    description: str | None = None
    uom: str | None = None
    gst_rate: Decimal | None = None
    taxable: Decimal | None = None
    cgst: Decimal | None = None
    sgst: Decimal | None = None
    igst: Decimal | None = None
    price_record_id: int | None = None  # the contract price a contract line was bought at (D48)
    contract_version: int | None = None
    status: str | None = None

    def as_json(self) -> dict[str, Any]:
        return {
            "line_no": self.line_no,
            "item_code": self.item_code,
            "description": self.description,
            "qty": str(self.qty),
            "uom": self.uom,
            "unit_price": str(self.unit_price),
            "gst_rate": _text(self.gst_rate),
            "taxable": _text(self.taxable),
            "cgst": _text(self.cgst),
            "sgst": _text(self.sgst),
            "igst": _text(self.igst),
            "need_by": self.need_by.isoformat() if self.need_by else None,
            "price_record_id": self.price_record_id,
            "contract_version": self.contract_version,
            "status": self.status,
        }

    @staticmethod
    def from_json(ln: dict[str, Any]) -> "LineState":
        return LineState(
            int(ln["line_no"]),
            ln.get("item_code"),
            Decimal(str(ln["qty"])),
            Decimal(str(ln["unit_price"])),
            _day(ln.get("need_by")),
            ln.get("description"),
            ln.get("uom"),
            _dec(ln.get("gst_rate")),
            _dec(ln.get("taxable")),
            _dec(ln.get("cgst")),
            _dec(ln.get("sgst")),
            _dec(ln.get("igst")),
            _int(ln.get("price_record_id")),
            _int(ln.get("contract_version")),
            ln.get("status"),
        )


@dataclass(frozen=True)
class Charge:
    """Freight or packing and forwarding on the order, with its tax."""

    type: str
    amount: Decimal
    gst_rate: Decimal | None = None
    cgst: Decimal | None = None
    sgst: Decimal | None = None
    igst: Decimal | None = None

    def as_json(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "amount": str(self.amount),
            "gst_rate": _text(self.gst_rate),
            "cgst": _text(self.cgst),
            "sgst": _text(self.sgst),
            "igst": _text(self.igst),
        }

    @staticmethod
    def from_json(c: dict[str, Any]) -> "Charge":
        return Charge(
            str(c["type"]),
            Decimal(str(c["amount"])),
            _dec(c.get("gst_rate")),
            _dec(c.get("cgst")),
            _dec(c.get("sgst")),
            _dec(c.get("igst")),
        )


@dataclass(frozen=True)
class Snapshot:
    version: int
    supplier_code: str
    ship_to_location: str | None
    payment_terms_code: str | None
    total: Decimal
    band_max: Decimal | None  # the top of the band that approved it; None for the top band (or not approved)
    lines: tuple[LineState, ...]
    rule_code: str | None = None
    # the rest of the header as the document says it; optional, so a block 4 snapshot still loads
    buyer_gstin: str | None = None
    delivery_terms: str | None = None
    contract_code: str | None = None
    taxable: Decimal | None = None
    cgst: Decimal | None = None
    sgst: Decimal | None = None
    igst: Decimal | None = None
    charges: tuple[Charge, ...] = ()

    def as_json(self) -> dict[str, Any]:
        """The stored snapshot (po_version.snapshot, po_approved_version.snapshot)."""
        return {
            "supplier_code": self.supplier_code,
            "ship_to_location": self.ship_to_location,
            "buyer_gstin": self.buyer_gstin,
            "payment_terms_code": self.payment_terms_code,
            "delivery_terms": self.delivery_terms,
            "contract_code": self.contract_code,
            "taxable": _text(self.taxable),
            "cgst": _text(self.cgst),
            "sgst": _text(self.sgst),
            "igst": _text(self.igst),
            "total": str(self.total),
            "lines": [ln.as_json() for ln in self.lines],
            "charges": [c.as_json() for c in self.charges],
        }

    @staticmethod
    def from_json(
        data: dict[str, Any],
        *,
        version: int,
        total: Decimal | None = None,
        band_max: Decimal | None = None,
        rule_code: str | None = None,
    ) -> "Snapshot":
        """A stored snapshot; `total` from its own row where the row keeps one (an approved version)."""
        return Snapshot(
            version,
            str(data.get("supplier_code") or ""),
            data.get("ship_to_location"),
            data.get("payment_terms_code"),
            total if total is not None else Decimal(str(data.get("total") or 0)),
            band_max,
            tuple(LineState.from_json(ln) for ln in data.get("lines") or []),
            rule_code,
            data.get("buyer_gstin"),
            data.get("delivery_terms"),
            data.get("contract_code"),
            _dec(data.get("taxable")),
            _dec(data.get("cgst")),
            _dec(data.get("sgst")),
            _dec(data.get("igst")),
            tuple(Charge.from_json(c) for c in data.get("charges") or []),
        )


# ---- the differences between two versions (7.3) ----------------------------------------------------------------

HEADER_FIELDS = (
    "supplier_code", "ship_to_location", "buyer_gstin", "payment_terms_code", "delivery_terms", "contract_code",
    "taxable", "cgst", "sgst", "igst", "total",
)  # fmt: skip
LINE_FIELDS = (
    "item_code", "description", "qty", "uom", "unit_price", "gst_rate", "taxable", "cgst", "sgst", "igst", "need_by",
    "price_record_id", "contract_version", "status",
)  # fmt: skip
CHARGE_FIELDS = ("amount", "gst_rate", "cgst", "sgst", "igst")


@dataclass(frozen=True)
class Difference:
    """One value that differs between two versions. `scope` is header, line or charge; a line or a charge only in one
    version has the field 'added' or 'dropped' (its description, or the charge's amount, before or after)."""

    scope: str
    line_no: int | None
    field: str
    before: Any
    after: Any
    charge: str | None = None  # the charge's type, for scope 'charge'


def _fields(a: Any, b: Any, names: tuple[str, ...]) -> list[tuple[str, Any, Any]]:
    """The named fields whose values differ; Decimals compare as numbers (650 equals 650.00), dates as dates."""
    return [(n, getattr(a, n), getattr(b, n)) for n in names if getattr(a, n) != getattr(b, n)]


def diff(a: Snapshot, b: Snapshot) -> list[Difference]:
    """What changed from version a to version b: header fields in a fixed order, then the lines by number, then the
    charges. Equal values are left out."""
    out = [Difference("header", None, f, x, y) for f, x, y in _fields(a, b, HEADER_FIELDS)]
    before, after = {ln.line_no: ln for ln in a.lines}, {ln.line_no: ln for ln in b.lines}
    for n in sorted(before.keys() | after.keys()):
        old, new = before.get(n), after.get(n)
        if old is None and new is not None:
            out.append(Difference("line", n, "added", None, new.description or new.item_code))
        elif new is None and old is not None:
            out.append(Difference("line", n, "dropped", old.description or old.item_code, None))
        elif old is not None and new is not None:
            out += [Difference("line", n, f, x, y) for f, x, y in _fields(old, new, LINE_FIELDS)]
    was, now = {c.type: c for c in a.charges}, {c.type: c for c in b.charges}
    for kind in sorted(was.keys() | now.keys()):
        old_c, new_c = was.get(kind), now.get(kind)
        if old_c is None and new_c is not None:
            out.append(Difference("charge", None, "added", None, new_c.amount, kind))
        elif new_c is None and old_c is not None:
            out.append(Difference("charge", None, "dropped", old_c.amount, None, kind))
        elif old_c is not None and new_c is not None:
            out += [Difference("charge", None, f, x, y, kind) for f, x, y in _fields(old_c, new_c, CHARGE_FIELDS)]
    return out


# ---- what is material (Q19) -------------------------------------------------------------------------------------


def _moved(base: LineState, new: LineState, date_days: int) -> str | None:
    if base.need_by is None or new.need_by is None:
        return None
    moved = abs((new.need_by - base.need_by).days)
    if moved <= date_days:
        return None
    return (
        f"line {new.line_no}: date moved {count(moved, 'day')} from the approved {base.need_by:%d %b %Y}, "
        f"more than the {date_days} accepted"
    )


def material_reasons(base: Snapshot, new: Snapshot, date_days: int) -> list[str]:
    """Why the new version is material against the approved one, one plain reason each, in a fixed order. Empty: not
    material (a new version the supplier acknowledges again; the approval carries). Delivery terms are not material
    (Q19; OP-2 waits for the owner): this list is the one place that decides it."""
    approved = {ln.line_no: ln for ln in base.lines}
    pairs = [(approved.get(ln.line_no), ln) for ln in new.lines]
    same = [(b, n) for b, n in pairs if b is not None and b.item_code == n.item_code]
    reasons = [f"line {n.line_no}: price up" for b, n in same if n.unit_price > b.unit_price]
    reasons += [f"line {n.line_no}: quantity up" for b, n in same if n.qty > b.qty]
    for b, n in pairs:
        if b is None:
            reasons.append(f"line {n.line_no}: new line")
        elif b.item_code != n.item_code:
            reasons.append(f"line {n.line_no}: item changed")
    if new.supplier_code != base.supplier_code:
        reasons.append("supplier changed")
    if new.ship_to_location != base.ship_to_location:
        reasons.append("delivery location changed")
    if new.payment_terms_code != base.payment_terms_code:
        reasons.append("payment terms changed")
    reasons += [r for b, n in same if (r := _moved(b, n, date_days))]
    if base.band_max is not None and new.total > base.band_max:
        band = f"the {base.rule_code} band" if base.rule_code else "its band"
        reasons.append(f"the total is above {band} (up to {format_inr(base.band_max)})")
    if new.total > base.total:
        reasons.append("the total went up")
    return reasons


def rise_pct(base: Decimal | None, new: Decimal | None) -> Decimal | None:
    """How far a price is above the base price, in percent with two decimals, for people to read. It decides nothing:
    the decision is formulas.supplier_changes.judge_price on the exact figures."""
    if base is None or base <= 0 or new is None:
        return None
    return ((new - base) * 100 / base).quantize(Decimal("0.01"), ROUND_HALF_UP)
