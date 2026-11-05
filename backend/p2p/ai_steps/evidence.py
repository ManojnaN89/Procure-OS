"""FR38: evidence check. Every value a model extracted must appear in the source text.

Values are compared after normalising: text ignores case, punctuation and spacing; numbers ignore thousands separators
(including Indian grouping) and read 'lakh', 'crore' and 'k'; dates are read in the common Indian formats. A value that
is not found is a failure: the model may have made it up, so a person must look at it.
"""

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

_UNITS = {
    "lakh": Decimal(100_000),
    "lac": Decimal(100_000),
    "crore": Decimal(10_000_000),
    "cr": Decimal(10_000_000),
    "thousand": Decimal(1000),
    "k": Decimal(1000),
}
_NUMBER = re.compile(
    r"(?<![\w.])(\d{1,3}(?:,\d{2,3})+|\d+)(?:\.(\d+))?(?:\s*(lakhs?|lacs?|crores?|cr|thousand|k)\b)?", re.IGNORECASE
)
_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_DMY = re.compile(r"\b(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})\b")
_NAMED = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s*([A-Za-z]{3,9})\.?,?\s*(\d{4})\b")
_MONTHS = {
    m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)
}


@dataclass(frozen=True)
class EvidenceFailure:
    path: str
    value: Any
    reason: str


def normalise_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(re.sub(r"[^\w\s]", " ", value).split())


def numbers_in(source: str) -> set[Decimal]:
    found: set[Decimal] = set()
    for m in _NUMBER.finditer(source):
        digits = m.group(1).replace(",", "") + (f".{m.group(2)}" if m.group(2) else "")
        value = Decimal(digits)
        unit = (m.group(3) or "").lower().rstrip("s")
        found.add(value * _UNITS[unit] if unit in _UNITS else value)
    return found


def dates_in(source: str) -> set[date]:
    found: set[date] = set()
    for y, mo, d in _ISO.findall(source):
        _add(found, int(y), int(mo), int(d))
    for d, mo, y in _DMY.findall(source):
        _add(found, int(y), int(mo), int(d))
    for d, name, y in _NAMED.findall(source):
        if (month := _MONTHS.get(name[:3].lower())) is not None:
            _add(found, int(y), month, int(d))
    return found


def _add(found: set[date], y: int, m: int, d: int) -> None:
    try:
        found.add(date(y, m, d))
    except ValueError:
        pass


def _values(obj: Any, path: str) -> list[tuple[str, Any]]:
    """Values at a path such as 'supplier' or 'lines[].qty'. A missing field gives nothing."""
    head, _, rest = path.partition(".")
    many = head.endswith("[]")
    key = head[:-2] if many else head
    value = obj.get(key) if isinstance(obj, dict) else getattr(obj, key, None)
    if value is None:
        return []
    if many:
        out: list[tuple[str, Any]] = []
        for i, item in enumerate(value):
            out.extend(
                (f"{key}[{i}]" + (f".{p}" if p else ""), v) for p, v in (_values(item, rest) if rest else [("", item)])
            )
        return out
    if rest:
        return [(f"{key}.{p}", v) for p, v in _values(value, rest)]
    return [(key, value)]


def check_evidence(extracted: Any, source: str, paths: list[str]) -> list[EvidenceFailure]:
    """Return the values at the given paths that cannot be found in the source text."""
    norm_source = normalise_text(source)
    nums, dts = numbers_in(source), dates_in(source)
    failures: list[EvidenceFailure] = []
    for path in paths:
        for where, value in _values(extracted, path):
            if value is None or isinstance(value, bool):
                continue
            if isinstance(value, datetime):
                value = value.date()
            if isinstance(value, date):
                ok, kind = value in dts, "date"
            elif isinstance(value, int | float | Decimal):
                ok, kind = Decimal(str(value)) in nums, "number"
            else:
                text_value = normalise_text(str(value))
                ok, kind = bool(text_value) and text_value in norm_source, "text"
            if not ok:
                failures.append(EvidenceFailure(where, value, f"{kind} not found in the source"))
    return failures
