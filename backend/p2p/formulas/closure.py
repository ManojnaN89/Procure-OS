"""Closing an order: short-close facts and risk, and the two-track check (D-12, D-13, DF-26, AT-14).

All arithmetic here is Decimal to the paisa and comes from the order line's own amounts
(`formulas/orders.line_amounts`), so a short-close counts the same GST as the order it closes. The service reads the
rows; this module only works them out.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from p2p.formulas.gst import Registration
from p2p.formulas.money import format_inr, format_qty
from p2p.formulas.orders import line_amounts

QTY = Decimal("0.0001")
ZERO = Decimal("0.00")


@dataclass(frozen=True)
class ShortLine:
    """One order line the buyer may close: ordered, received and already short-closed quantities, at its price."""

    line_no: int
    item: str
    uom: str | None
    qty: Decimal
    received_qty: Decimal
    short_closed_qty: Decimal
    unit_price: Decimal
    gst_rate: Decimal
    supplier_state: str
    place_state: str
    registration: Registration = Registration.REGULAR

    @property
    def balance(self) -> Decimal:
        return max(self.qty - self.received_qty - self.short_closed_qty, Decimal(0)).quantize(
            QTY, rounding=ROUND_HALF_UP
        )

    @property
    def value(self) -> Decimal:
        """The balance at the line's price plus GST, exactly as the order would have charged it."""
        if self.balance <= 0:
            return ZERO
        return line_amounts(
            self.balance, self.unit_price, self.gst_rate, self.supplier_state, self.place_state, self.registration
        ).total


@dataclass(frozen=True)
class Flag:
    """Why a short-close is more than its value: a need still there, an advance open, goods on the way."""

    code: str  # unmet_need, advance_open, in_transit
    words: str


@dataclass(frozen=True)
class ShortCloseFacts:
    lines: tuple[dict[str, object], ...]
    value: Decimal
    flags: tuple[Flag, ...]

    @property
    def line_count(self) -> int:
        return len(self.lines)


def short_close_facts(lines: Sequence[ShortLine], flags: Sequence[Flag] = ()) -> ShortCloseFacts:
    """The chosen lines' balances and values, and the flags that make the close risky (R1.2). Lines with nothing left
    are left out; the value is the sum of their values including GST, rounded to the paisa."""
    kept = [ln for ln in lines if ln.balance > 0]
    rows: tuple[dict[str, object], ...] = tuple(
        {
            "line": ln.line_no,
            "item": ln.item,
            "uom": ln.uom,
            "balance": ln.balance,
            "unit_price": ln.unit_price,
            "gst_rate": ln.gst_rate,
            "value": ln.value,
        }
        for ln in kept
    )
    total = sum((ln.value for ln in kept), ZERO).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return ShortCloseFacts(rows, total, tuple(flags))


def risk(band: str, flags: Sequence[Flag]) -> str:
    """Low risk only in an automatic band with no flag; any flag or a band with a person is high (R1.3, OP-2)."""
    return "low" if band == "auto" and not flags else "high"


def unmet_need_words(item: str, qty: Decimal, uom: str | None, need_by: date, ref: str) -> str:
    return f"production still needs {format_qty(qty)} {uom or ''} of {item} by {need_by:%d %b %Y} ({ref})".replace(
        "  ", " "
    )


def advance_words(payment_no: str, amount: Decimal) -> str:
    return f"advance {payment_no}: {format_inr(amount)} not yet used"


def in_transit_words(qty: Decimal, shipped_on: date) -> str:
    return f"{format_qty(qty)} dispatched on {shipped_on:%d %b %Y}"


# ---- the two-track closure (R2) -------------------------------------------------------------------


@dataclass(frozen=True)
class LineState:
    line_no: int
    qty: Decimal
    received_qty: Decimal
    invoiced_qty: Decimal
    status: str
    claimed_qty: Decimal = Decimal(0)


@dataclass(frozen=True)
class Blocker:
    code: str
    words: str


def supply_done(lines: Sequence[LineState]) -> bool:
    """Every line not cancelled is received or short-closed. An order of only cancelled lines is cancelled, never
    closed, so a live line is needed (R2.2)."""
    live = [ln for ln in lines if ln.status != "cancelled"]
    return bool(live) and all(ln.status in ("received", "short_closed") for ln in live)


@dataclass(frozen=True)
class OpenInvoice:
    no: str
    status: str


@dataclass(frozen=True)
class FailedPayment:
    no: str
    on: date


@dataclass(frozen=True)
class OpenAdvance:
    no: str
    amount: Decimal


@dataclass(frozen=True)
class OpenNote:
    kind: str  # credit_note or debit_note
    no: str


@dataclass(frozen=True)
class OpenReturn:
    no: str


@dataclass(frozen=True)
class OpenExcess:
    qty: Decimal
    gr_no: str


@dataclass(frozen=True)
class OpenClaim:
    no: str


STATUS_WORDS = {
    "received": "received and not booked",
    "blocked": "blocked",
    "approved": "booked and not paid",
    "part_paid": "part paid",
    "draft": "a draft",
    "sent": "sent",
}
NOTE_WORDS = {"credit_note": "credit note", "debit_note": "debit note"}


def blockers(
    lines: Sequence[LineState],
    *,
    invoices: Sequence[OpenInvoice] = (),
    failed_payments: Sequence[FailedPayment] = (),
    advances: Sequence[OpenAdvance] = (),
    notes: Sequence[OpenNote] = (),
    returns: Sequence[OpenReturn] = (),
    excesses: Sequence[OpenExcess] = (),
    claims: Sequence[OpenClaim] = (),
    short_close_approver: str | None = None,
) -> tuple[Blocker, ...]:
    """Everything that keeps the money from being settled, in the order of the check (R2.3). Quantities, not values:
    a released price difference never blocks."""
    out: list[Blocker] = []
    for ln in lines:
        if ln.status == "cancelled":
            continue
        if ln.received_qty > ln.invoiced_qty:
            out.append(
                Blocker(
                    "not_invoiced",
                    f"line {ln.line_no}: {format_qty(ln.received_qty - ln.invoiced_qty)} received, not invoiced",
                )
            )
        if ln.invoiced_qty > ln.received_qty + ln.claimed_qty:
            out.append(
                Blocker(
                    "invoiced_not_received",
                    f"line {ln.line_no}: {format_qty(ln.invoiced_qty - ln.received_qty - ln.claimed_qty)} invoiced, "
                    "not received",
                )
            )
    for inv in invoices:
        out.append(Blocker("invoice_open", f"invoice {inv.no} is {STATUS_WORDS.get(inv.status, inv.status)}"))
    for pay in failed_payments:
        out.append(Blocker("payment_failed", f"payment {pay.no} failed on {pay.on:%d %b %Y}"))
    for adv in advances:
        out.append(Blocker("advance_open", f"advance {adv.no}: {format_inr(adv.amount)} not yet used or recovered"))
    for note in notes:
        out.append(Blocker("credit_open", f"{NOTE_WORDS.get(note.kind, note.kind)} {note.no} not yet applied"))
    for ret in returns:
        out.append(Blocker("return_open", f"return {ret.no} not closed"))
    for exc in excesses:
        out.append(Blocker("excess_open", f"{format_qty(exc.qty)} above the order on {exc.gr_no} waits for the buyer"))
    for claim in claims:
        out.append(Blocker("claim_open", f"claim {claim.no} open"))
    if short_close_approver:
        out.append(Blocker("short_close_waiting", f"a short-close waits for {short_close_approver}"))
    return tuple(out)
