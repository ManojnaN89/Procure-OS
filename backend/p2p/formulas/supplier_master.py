"""Checks on a supplier's master record: identity, GST registration, MSME status, contact and bank details.

Pure rules, no database. Each check returns the problems it found as {field: message}, so a screen can show every problem
at once. A record that passes is only *well formed*; whether the supplier may be used is decided by its documents.
"""

import re
from typing import Any

from p2p.formulas.gstin import is_valid_gstin, is_valid_pan, state_of_gstin

GST_TYPES = ("regular", "composition", "unregistered")
MSME = ("micro", "small", "medium", "none")
LEGAL_FORMS = ("proprietorship", "partnership", "llp", "pvt_ltd", "public_ltd", "other")
_UDYAM = re.compile(r"^UDYAM-[A-Z]{2}-\d{2}-\d{7}$")
_IFSC = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")
_ACCOUNT = re.compile(r"^\d{9,18}$")
_EMAIL = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$")
_PHONE = re.compile(r"^\+?[0-9][0-9 \-]{6,17}$")


def _text(data: dict[str, Any], key: str) -> str:
    return str(data.get(key) or "").strip()


def check_identity(data: dict[str, Any], terms: set[str] | None = None) -> dict[str, str]:
    """Name, legal form, PAN, GST registration, MSME status and payment terms."""
    bad: dict[str, str] = {}
    if not _text(data, "legal_name"):
        bad["legal_name"] = "the legal name is required"
    form = _text(data, "legal_form")
    if form and form not in LEGAL_FORMS:
        bad["legal_form"] = f"the legal form must be one of {', '.join(LEGAL_FORMS)}"
    pan = _text(data, "pan").upper()
    if not pan:
        bad["pan"] = "the PAN is required"
    elif not is_valid_pan(pan):
        bad["pan"] = "the PAN must be 5 letters, 4 digits and a letter, like ABCDE1234F"
    gst = _text(data, "gst_reg_type") or "unregistered"
    gstin = _text(data, "gstin").upper()
    if gst not in GST_TYPES:
        bad["gst_reg_type"] = f"the GST registration must be one of {', '.join(GST_TYPES)}"
    elif gst == "unregistered":
        if gstin:
            bad["gstin"] = "an unregistered supplier has no GSTIN: choose regular or composition, or clear the GSTIN"
    elif not gstin:
        bad["gstin"] = "a GST-registered supplier needs its GSTIN"
    elif not is_valid_gstin(gstin):
        bad["gstin"] = "this is not a valid GSTIN (15 characters, the last one a check character)"
    elif "pan" not in bad and gstin[2:12] != pan:
        bad["gstin"] = "the PAN inside the GSTIN does not match the PAN"
    msme = _text(data, "msme_category") or "none"
    udyam = _text(data, "udyam_no").upper()
    if msme not in MSME:
        bad["msme_category"] = f"the MSME category must be one of {', '.join(MSME)}"
    elif msme != "none" and not udyam:
        bad["udyam_no"] = "an MSME supplier needs its Udyam number"
    if udyam and not _UDYAM.match(udyam):
        bad["udyam_no"] = "the Udyam number looks like UDYAM-MH-00-1234567"
    code = _text(data, "payment_terms_code")
    if code and terms is not None and code not in terms:
        bad["payment_terms_code"] = f"there is no payment term {code}"
    days = data.get("agreed_payment_days")
    if days not in (None, ""):
        try:
            if int(days) < 0 or int(days) > 365:
                raise ValueError
        except (TypeError, ValueError):
            bad["agreed_payment_days"] = "the agreed days must be a whole number from 0 to 365"
    return bad


def check_contact(data: dict[str, Any]) -> dict[str, str]:
    bad: dict[str, str] = {}
    email, phone = _text(data, "email"), _text(data, "phone")
    if email and not _EMAIL.match(email):
        bad["email"] = "this is not an email address"
    if phone and not _PHONE.match(phone):
        bad["phone"] = "the phone number can have digits, spaces and dashes, and may start with +"
    return bad


def check_bank(data: dict[str, Any]) -> dict[str, str]:
    """An account number is 9 to 18 digits and an IFSC is 4 letters, a zero, then 6 letters or digits."""
    bad: dict[str, str] = {}
    account, ifsc = _text(data, "account_no"), _text(data, "ifsc").upper()
    if not account:
        bad["account_no"] = "the account number is required"
    elif not _ACCOUNT.match(account):
        bad["account_no"] = "the account number is 9 to 18 digits"
    if not ifsc:
        bad["ifsc"] = "the IFSC is required"
    elif not _IFSC.match(ifsc):
        bad["ifsc"] = "the IFSC is 4 letters, a zero, then 6 letters or digits, like HDFC0001234"
    return bad


def state_for(gstin: str | None, fallback: str | None = None) -> str | None:
    """The GST state code of the supplier: from its GSTIN when it has one."""
    return state_of_gstin(gstin) if gstin else fallback


def next_code(existing: list[str], prefix: str = "SUP-") -> str:
    """The next supplier code after the highest SUP-nnn, keeping at least three digits."""
    numbers = [int(c[len(prefix) :]) for c in existing if c.startswith(prefix) and c[len(prefix) :].isdigit()]
    return f"{prefix}{(max(numbers) + 1 if numbers else 1):03d}"
