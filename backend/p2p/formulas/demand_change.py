"""What a change in the ERP's demand does to one material request line (rule Q21). Plain arithmetic in the item's base
unit; the services in requests/demand_changes.py apply it."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

ZERO = Decimal(0)


@dataclass(frozen=True)
class Excess:
    """Where a line's excess goes, in the order it is taken."""

    cut: Decimal  # off purchase request lines not yet with the buyer
    release: Decimal  # off the stock set aside
    buyer: Decimal  # on cover the buyer already has (ordered or asked to quote): a case for the buyer
    store: Decimal  # issued beyond the new need: a case for the store

    @property
    def total(self) -> Decimal:
        return self.cut + self.release + self.buyer + self.store


def excess(need: Decimal, issued: Decimal, reserved: Decimal, open_cover: Decimal, buyer_cover: Decimal) -> Excess:
    """A line now needs `need` (0 when cancelled). What it holds beyond that (issued, set aside and still coming) is taken
    first from cover not yet with the buyer (no money committed), then from the stock set aside; what is still over, up to
    the buyer's cover, is the buyer's to act on, and the rest can only be stock already issued."""
    left = max(issued + reserved + open_cover + buyer_cover - need, ZERO)
    cut = min(left, max(open_cover, ZERO))
    left -= cut
    release = min(left, max(reserved, ZERO))
    left -= release
    buyer = min(left, max(buyer_cover, ZERO))
    return Excess(cut, release, buyer, left - buyer)


def date_change(before: date, after: date) -> str | None:
    """'earlier', 'later' or None when the date is the same."""
    if after < before:
        return "earlier"
    if after > before:
        return "later"
    return None


def qty_change(before: Decimal, after: Decimal) -> str | None:
    """'qty_down', 'qty_up' or None when the quantity is the same."""
    if after < before:
        return "qty_down"
    if after > before:
        return "qty_up"
    return None


def late_for(promised: date | None, need_by: date | None, new_need_by: date) -> bool:
    """An order line promised (else asked for) after the new need-by date will come too late for it."""
    when = promised or need_by
    return when is not None and when > new_need_by
