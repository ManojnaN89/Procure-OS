"""Indian financial year: 1 April to 31 March."""

from datetime import date


def fy_start_year(d: date) -> int:
    return d.year if d.month >= 4 else d.year - 1


def financial_year(d: date) -> str:
    """'2025-26' for any date from 1 Apr 2025 to 31 Mar 2026."""
    y = fy_start_year(d)
    return f"{y}-{(y + 1) % 100:02d}"


def fy_bounds(d: date) -> tuple[date, date]:
    y = fy_start_year(d)
    return date(y, 4, 1), date(y + 1, 3, 31)


def quarter(d: date) -> int:
    """Quarter of the financial year: Apr-Jun is 1 ... Jan-Mar is 4."""
    return ((d.month - 4) % 12) // 3 + 1
