"""GSTIN and PAN format checks. The 15th character of a GSTIN is a check digit over the first 14."""

import re

_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_PAN = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
_GSTIN = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")


def gstin_check_char(first14: str) -> str:
    total = 0
    for i, ch in enumerate(first14):
        product = _ALPHABET.index(ch) * (1 if i % 2 == 0 else 2)
        total += product // 36 + product % 36
    return _ALPHABET[(36 - total % 36) % 36]


def is_valid_gstin(gstin: str) -> bool:
    g = gstin.strip().upper()
    return bool(_GSTIN.match(g)) and gstin_check_char(g[:14]) == g[14]


def is_valid_pan(pan: str) -> bool:
    return bool(_PAN.match(pan.strip().upper()))


def state_of_gstin(gstin: str) -> str:
    """The two-digit GST state code at the start of a GSTIN."""
    return gstin.strip()[:2]
