"""Identifier helpers shared by the generator.

All generated PANs carry the digits 0000, which the Income Tax Department never issues,
so a generated PAN (and any GSTIN built on it) can never belong to a real taxpayer.
"""
import re

_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")


def gstin_check_digit(first14: str) -> str:
    """Luhn mod-36 check character over the first 14 characters of a GSTIN."""
    total = 0
    for i, ch in enumerate(first14):
        v = _CHARS.index(ch) * (1 if i % 2 == 0 else 2)
        total += v // 36 + v % 36
    return _CHARS[(36 - total % 36) % 36]


def make_gstin(state_code: str, pan: str, entity_no: int = 1) -> str:
    base = f"{state_code}{pan}{_CHARS[entity_no]}Z"
    return base + gstin_check_digit(base)


def is_valid_gstin(g: str) -> bool:
    return bool(GSTIN_RE.match(g)) and gstin_check_digit(g[:14]) == g[14]


def make_pan(prefix3: str, holder_type: str, name_initial: str, last: str) -> str:
    """holder_type: C company, F firm, P person, H HUF, T trust, A association, B body, L local authority, J artificial juridical person, G government."""
    pan = f"{prefix3}{holder_type}{name_initial}0000{last}"
    assert PAN_RE.match(pan), pan
    return pan


if __name__ == "__main__":
    assert is_valid_gstin("27AAPFU0939F1ZV")  # known-good example from public documentation
    pan = make_pan("AXQ", "C", "C", "M")
    for st in ("27", "24", "29"):
        print(st, make_gstin(st, pan))
