"""Masking before any model call (D-25): bank account numbers, IFSC codes, PANs and Aadhaar numbers never leave for a
provider. The gateway runs every message through mask_messages(), so no AI step or agent can skip it.

Left as written, on purpose:
- a GSTIN: invoice reading extracts it and the evidence check must find it in the document; it is printed on every tax
  invoice and public on the GST portal. Its embedded PAN is not matched because a PAN must stand on its own as a word.
- a 12-digit number on its own (no Aadhaar wording, not written 4-4-4): an e-way bill number is 12 digits too.
- a long number with no account wording near it: invoice, acknowledgement and e-way bill numbers are long numbers too.
"""

import re
from collections import Counter

from p2p.ai_steps.adapters import Messages

_PAN = re.compile(r"\b[A-Z]{3}[ABCFGHJLPT][A-Z]\d{4}[A-Z]\b", re.IGNORECASE)
_IFSC = re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b", re.IGNORECASE)
_AADHAAR_SPACED = re.compile(r"\b[2-9]\d{3}[ -]\d{4}[ -]\d{4}\b")
_AADHAAR_WORDED = re.compile(r"(?i)(aadhaa?r|\buid\b)([^\d\n]{0,30})([2-9]\d{3}[ -]?\d{4}[ -]?\d{4})\b")
# A number of 9 to 18 digits on the same line as account wording, at most 40 characters after it.
_ACCOUNT = re.compile(r"(?i)(account|a/c|acct|\bac\b|bank)([^\n]{0,40}?)(?<!\d)(\d{9,18})(?!\d)")


def _keep_last4(number: str) -> str:
    return "X" * (len(number) - 4) + number[-4:]


def mask(text: str) -> tuple[str, Counter[str]]:
    """The text with each sensitive value replaced, and how many of each kind were masked (never the values)."""
    found: Counter[str] = Counter()

    def sub(pattern: re.Pattern[str], kind: str, repl: str | None, value: str, group: int = 0) -> str:
        def one(m: re.Match[str]) -> str:
            found[kind] += 1
            if group == 0:
                return repl or ""
            # keep the wording before the number, replace only the number
            return m.group(0)[: m.start(group) - m.start(0)] + (repl or _keep_last4(m.group(group)))

        return pattern.sub(one, value)

    text = sub(_AADHAAR_WORDED, "aadhaar", "[Aadhaar]", text, group=3)
    text = sub(_AADHAAR_SPACED, "aadhaar", "[Aadhaar]", text)
    text = sub(_ACCOUNT, "bank_account", None, text, group=3)
    text = sub(_IFSC, "ifsc", "[IFSC]", text)
    text = sub(_PAN, "pan", "[PAN]", text)
    return text, found


def mask_messages(messages: Messages) -> tuple[Messages, dict[str, int]]:
    """Every message's content masked, and the counts per kind over all of them."""
    total: Counter[str] = Counter()
    out: Messages = []
    for message in messages:
        content, found = mask(message.get("content", ""))
        total.update(found)
        out.append({**message, "content": content})
    return out, dict(total)
