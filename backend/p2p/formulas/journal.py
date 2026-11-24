"""Journal vouchers for the accounting export (2.6, R8): payables, payments, advances and the month-end accrual.

The export is a file the client's ledger imports; nothing here writes to our books. Every voucher balances to the paisa
or the export stops and names it. Ledgers come from `ledger_map` and each line's own GL account.
"""

import csv
import io
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

PAISE = Decimal("0.01")
ZERO = Decimal("0.00")


class UnmappedLedger(Exception):
    """A purpose has no ledger in `ledger_map`; the export refuses until it is mapped (3.2)."""


@dataclass(frozen=True)
class Ledger:
    code: str
    name: str


@dataclass(frozen=True)
class Line:
    ledger_code: str
    ledger_name: str
    debit: Decimal = ZERO
    credit: Decimal = ZERO
    party_code: str | None = None
    party_name: str | None = None
    party_gstin: str | None = None
    reference: str | None = None
    narration: str | None = None


@dataclass(frozen=True)
class Voucher:
    type: str
    no: str
    on: date
    event_period: str
    lines: tuple[Line, ...] = field(default_factory=tuple)

    @property
    def debit(self) -> Decimal:
        return sum((ln.debit for ln in self.lines), ZERO).quantize(PAISE)

    @property
    def credit(self) -> Decimal:
        return sum((ln.credit for ln in self.lines), ZERO).quantize(PAISE)

    @property
    def balanced(self) -> bool:
        return self.debit == self.credit


def ledger(maps: Mapping[str, Ledger], purpose: str) -> Ledger:
    found = maps.get(purpose)
    if found is None:
        raise UnmappedLedger(f"map '{purpose}' to a ledger first")
    return found


def _line(led: Ledger, *, debit: Decimal = ZERO, credit: Decimal = ZERO, **party: str | None) -> Line:
    return Line(led.code, led.name, debit.quantize(PAISE), credit.quantize(PAISE), **party)


def _payable(msme: bool) -> str:
    return "payable_msme" if msme else "payable_other"


def _party(supplier: str, name: str, gstin: str | None) -> dict[str, Any]:
    return {"party_code": supplier, "party_name": name, "party_gstin": gstin}


def tax_ledger(head: str) -> str:
    return {"cgst": "gst_input_cgst", "sgst": "gst_input_sgst", "igst": "gst_input_igst"}[head]


def purchase(
    *,
    no: str,
    on: date,
    event_period: str,
    supplier: str,
    supplier_name: str,
    gstin: str | None,
    gl_lines: Sequence[tuple[str, Decimal, str | None]],
    freight: Decimal,
    tax: Mapping[str, Decimal],
    round_off: Decimal,
    tds: Decimal,
    msme: bool,
    reverse_charge: bool,
    maps: Mapping[str, Ledger],
) -> Voucher:
    """A booked invoice: each line's GL, the GST input heads, TDS to its ledger, and the rest to payables."""
    party, lines = _party(supplier, supplier_name, gstin), []
    for gl, amount, ref in gl_lines:
        if amount:
            lines.append(_line(ledger(maps, gl), debit=amount, reference=ref, narration=f"Invoice {no}", **party))
    if freight:
        lines.append(_line(ledger(maps, "freight_inward"), debit=freight, narration=f"Freight on {no}", **party))
    charged = ZERO
    for head, amount in tax.items():
        if amount:
            lines.append(
                _line(ledger(maps, tax_ledger(head)), debit=amount, narration=f"{head.upper()} on {no}", **party)
            )
            charged += amount
    if round_off:
        led = ledger(maps, "round_off")
        if round_off > 0:
            lines.append(_line(led, debit=round_off, narration=f"Round off on {no}", **party))
        else:
            lines.append(_line(led, credit=-round_off, narration=f"Round off on {no}", **party))
    if reverse_charge and charged:
        lines.append(_line(ledger(maps, "rcm_payable"), credit=charged, narration=f"Reverse charge on {no}", **party))
    if tds:
        lines.append(_line(ledger(maps, "tds_payable"), credit=tds, narration=f"TDS on {no}", **party))
    debits = sum((ln.debit for ln in lines), ZERO) - sum((ln.credit for ln in lines), ZERO)
    payable = (debits - tds).quantize(PAISE)
    if payable:
        lines.append(_line(ledger(maps, _payable(msme)), credit=payable, narration=f"Invoice {no}", **party))
    return Voucher("purchase", no, on, event_period, tuple(lines))


def note(
    *,
    kind: str,
    no: str,
    on: date,
    event_period: str,
    supplier: str,
    supplier_name: str,
    gstin: str | None,
    gl_lines: Sequence[tuple[str, Decimal, str | None]],
    tax: Mapping[str, Decimal],
    msme: bool,
    maps: Mapping[str, Ledger],
) -> Voucher:
    """A credit or debit note: the purchase's lines reversed against payables."""
    party, lines = _party(supplier, supplier_name, gstin), []
    for gl, amount, ref in gl_lines:
        if amount:
            lines.append(_line(ledger(maps, gl), credit=amount, reference=ref, narration=f"Note {no}", **party))
    for head, amount in tax.items():
        if amount:
            lines.append(
                _line(ledger(maps, tax_ledger(head)), credit=amount, narration=f"{head.upper()} on {no}", **party)
            )
    total = sum((ln.credit for ln in lines), ZERO)
    payable = ledger(maps, _payable(msme))
    if kind == "credit_note":
        lines.insert(0, _line(payable, debit=total, narration=f"Credit note {no}", **party))
    else:
        lines.append(_line(payable, credit=total, narration=f"Debit note {no}", **party))
    return Voucher(kind, no, on, event_period, tuple(lines))


def payment(
    *,
    no: str,
    on: date,
    event_period: str,
    supplier: str,
    supplier_name: str,
    gstin: str | None,
    amount: Decimal,
    interest: Decimal,
    msme: bool,
    bank_account: str,
    maps: Mapping[str, Ledger],
) -> Voucher:
    party = _party(supplier, supplier_name, gstin)
    lines = [_line(ledger(maps, _payable(msme)), debit=amount, narration=f"Payment {no}", **party)]
    if interest:
        lines.append(_line(ledger(maps, "msme_interest"), debit=interest, narration=f"Interest on {no}", **party))
    lines.append(
        _line(
            ledger(maps, f"bank:{bank_account}"),
            credit=amount + interest,
            narration=f"Payment {no}",
            **party,
        )
    )
    return Voucher("payment", no, on, event_period, tuple(lines))


def advance(
    *,
    no: str,
    on: date,
    event_period: str,
    supplier: str,
    supplier_name: str,
    gstin: str | None,
    amount: Decimal,
    bank_account: str,
    maps: Mapping[str, Ledger],
) -> Voucher:
    party = _party(supplier, supplier_name, gstin)
    return Voucher(
        "advance",
        no,
        on,
        event_period,
        (
            _line(ledger(maps, "advance"), debit=amount, narration=f"Advance {no}", **party),
            _line(ledger(maps, f"bank:{bank_account}"), credit=amount, narration=f"Advance {no}", **party),
        ),
    )


def advance_adjustment(
    *,
    no: str,
    on: date,
    event_period: str,
    supplier: str,
    supplier_name: str,
    gstin: str | None,
    amount: Decimal,
    msme: bool,
    maps: Mapping[str, Ledger],
) -> Voucher:
    party = _party(supplier, supplier_name, gstin)
    return Voucher(
        "advance_adjustment",
        no,
        on,
        event_period,
        (
            _line(ledger(maps, _payable(msme)), debit=amount, narration=f"Advance adjusted on {no}", **party),
            _line(ledger(maps, "advance"), credit=amount, narration=f"Advance adjusted on {no}", **party),
        ),
    )


def accrual(
    *,
    period: str,
    on: date,
    event_period: str,
    gl_lines: Sequence[tuple[str, Decimal, str | None]],
    maps: Mapping[str, Ledger],
) -> tuple[Voucher, Voucher]:
    """Goods received and not invoiced at a month end, and its reversal on the first of the next month."""
    lines = [
        _line(ledger(maps, gl), debit=amount, reference=ref, narration=f"Accrual {period}")
        for gl, amount, ref in gl_lines
        if amount
    ]
    total = sum((ln.debit for ln in lines), ZERO)
    lines.append(_line(ledger(maps, "grni"), credit=total, narration=f"Accrual {period}"))
    made = Voucher("accrual", f"ACCRUAL-{period}", on, event_period, tuple(lines))
    back = Voucher(
        "accrual_reversal",
        f"ACCRUAL-{period}-REV",
        _first_of_next(period),
        _next_period(period),
        tuple(
            Line(
                ln.ledger_code,
                ln.ledger_name,
                ln.credit,
                ln.debit,
                ln.party_code,
                ln.party_name,
                ln.party_gstin,
                ln.reference,
                f"Reversal of accrual {period}",
            )
            for ln in lines
        ),
    )
    return made, back


def _next_period(period: str) -> str:
    y, m = int(period[:4]), int(period[5:])
    return f"{y + 1}-01" if m == 12 else f"{y}-{m + 1:02d}"


def _first_of_next(period: str) -> date:
    y, m = int(period[:4]), int(period[5:])
    return date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)


HEADER = (
    "voucher_type,voucher_no,date,event_period,ledger_code,ledger_name,party_code,party_name,party_gstin,"
    "debit,credit,reference,narration"
)


def render(vouchers: Sequence[Voucher]) -> tuple[str, dict[str, Decimal]]:
    """The generic CSV and the net total per ledger. Amounts are exact text; a voucher that does not balance stops it."""
    for v in vouchers:
        if not v.balanced:
            raise ValueError(f"voucher {v.no} does not balance: debit {v.debit}, credit {v.credit}")
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(HEADER.split(","))
    totals: dict[str, Decimal] = {}
    for v in vouchers:
        for ln in v.lines:
            w.writerow(
                [v.type, v.no, v.on.isoformat(), v.event_period, ln.ledger_code, ln.ledger_name,
                 ln.party_code or "", ln.party_name or "", ln.party_gstin or "", f"{ln.debit:.2f}",
                 f"{ln.credit:.2f}", ln.reference or "", ln.narration or ""]
            )  # fmt: skip
            totals[ln.ledger_code] = totals.get(ln.ledger_code, ZERO) + ln.debit - ln.credit
    return out.getvalue(), totals
