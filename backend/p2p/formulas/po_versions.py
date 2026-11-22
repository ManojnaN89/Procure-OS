"""What an order's approval covers (Q19, DF-07, DF-08): the approved version of an order, and whether a later version
changed something material since.

A change is measured from the last approved version, never from the version before it, so small changes add up: two
2-day slips are a 4-day slip. Material: a price up, a quantity above the approved one, an item changed or a new line,
another supplier, delivery location or payment terms (which carry any advance), a date moved beyond the tolerance either
way, a total above the band that approved it, or any higher total. Anything else is a new version the supplier
acknowledges again, with the approval carried.
"""

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from p2p.formulas.money import format_inr
from p2p.formulas.words import count


@dataclass(frozen=True)
class LineState:
    line_no: int
    item_code: str | None
    qty: Decimal
    unit_price: Decimal
    need_by: date | None

    def as_json(self) -> dict[str, Any]:
        return {
            "line_no": self.line_no,
            "item_code": self.item_code,
            "qty": str(self.qty),
            "unit_price": str(self.unit_price),
            "need_by": self.need_by.isoformat() if self.need_by else None,
        }


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

    def as_json(self) -> dict[str, Any]:
        """The stored snapshot (po_approved_version.snapshot)."""
        return {
            "supplier_code": self.supplier_code,
            "ship_to_location": self.ship_to_location,
            "payment_terms_code": self.payment_terms_code,
            "lines": [ln.as_json() for ln in self.lines],
        }

    @staticmethod
    def from_json(
        data: dict[str, Any], *, version: int, total: Decimal, band_max: Decimal | None, rule_code: str | None
    ) -> "Snapshot":
        lines = tuple(
            LineState(
                int(ln["line_no"]),
                ln.get("item_code"),
                Decimal(str(ln["qty"])),
                Decimal(str(ln["unit_price"])),
                date.fromisoformat(ln["need_by"]) if ln.get("need_by") else None,
            )
            for ln in data.get("lines") or []
        )
        return Snapshot(
            version,
            str(data.get("supplier_code") or ""),
            data.get("ship_to_location"),
            data.get("payment_terms_code"),
            total,
            band_max,
            lines,
            rule_code,
        )


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
    material (a new version the supplier acknowledges again; the approval carries)."""
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
