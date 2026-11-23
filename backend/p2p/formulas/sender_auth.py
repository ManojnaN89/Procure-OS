"""Sender authentication of an incoming mail (D53, block 6): what our own mail server said about the sender.

A receiving mail server writes an `Authentication-Results` header (RFC 8601): its own name (the authserv-id), then a
result per method, e.g. `mx.example; spf=pass smtp.mailfrom=a.example; dkim=pass header.d=a.example; dmarc=pass`.
Anyone can write such a header into a mail, so only a header whose authserv-id is our own server's (the setting
`mail_authserv_id`) is trusted; the topmost one is used and every other header is ignored.

Verdict:
- pass: dmarc=pass; or no dmarc result and dkim=pass signed by the From domain (or a parent of it), or spf=pass for
  the From domain (or a parent of it);
- fail: dmarc=fail; or no dmarc result, spf fail or softfail, and dkim not pass;
- none: no trusted header, no setting, or anything else.
"""

import re
from dataclasses import dataclass, field
from email.utils import parseaddr
from typing import Any

PASS, FAIL, NONE = "pass", "fail", "none"
_COMMENT = re.compile(r"\([^()]*\)")


@dataclass(frozen=True)
class Header:
    server: str
    results: dict[str, str]  # method to result (the first of each method)
    props: dict[str, str] = field(default_factory=dict)  # e.g. 'dkim.header.d' to 'a.example'


def parse(value: str) -> Header | None:
    """One Authentication-Results header; None when it cannot be read."""
    text = _COMMENT.sub(" ", value or "")
    parts = [p.strip() for p in text.split(";")]
    if not parts or not parts[0]:
        return None
    server = parts[0].split()[0].strip().lower()
    results: dict[str, str] = {}
    props: dict[str, str] = {}
    for part in parts[1:]:
        tokens = part.split()
        if not tokens or "=" not in tokens[0]:
            continue
        method, _, result = tokens[0].partition("=")
        method, result = method.strip().lower(), result.strip().lower()
        if not method or not result or method in results:
            continue
        results[method] = result
        for token in tokens[1:]:
            key, eq, val = token.partition("=")
            if eq and key and val:
                props[f"{method}.{key.strip().lower()}"] = val.strip().strip('"').lower()
    return Header(server, results, props) if results else None


def _aligned(domain: str | None, sender: str) -> bool:
    """The domain is the sender's own or a parent of it (mail.a.example is aligned with a.example)."""
    if not domain or not sender:
        return False
    domain = domain.split("@")[-1].strip(".").lower()
    return sender == domain or sender.endswith("." + domain)


def verdict(header: Header, sender_domain: str) -> tuple[str, str]:
    """The verdict and, for a failure, what failed in words (e.g. 'dmarc=fail')."""
    r = header.results
    dmarc = r.get("dmarc")
    if dmarc == "pass":
        return PASS, ""
    if dmarc == "fail":
        return FAIL, "dmarc=fail"
    if dmarc is None:
        dkim_ok = r.get("dkim") == "pass" and _aligned(header.props.get("dkim.header.d"), sender_domain)
        spf_ok = r.get("spf") == "pass" and _aligned(header.props.get("spf.smtp.mailfrom"), sender_domain)
        if dkim_ok or spf_ok:
            return PASS, ""
        if r.get("spf") in ("fail", "softfail") and r.get("dkim") != "pass":
            return FAIL, f"spf={r['spf']}"
    return NONE, ""


def read(headers: list[str], trusted_server: str | None, from_addr: str) -> dict[str, Any]:
    """The result kept on the mail: {server, spf, dkim, dmarc, verdict} (and `failed` when it failed)."""
    trusted = (trusted_server or "").strip().lower()
    none: dict[str, Any] = {"server": None, "spf": None, "dkim": None, "dmarc": None, "verdict": NONE}
    if not trusted:
        return none
    sender = parseaddr(from_addr or "")[1].rpartition("@")[2].strip().lower()
    for value in headers:
        header = parse(value)
        if header is None or header.server != trusted:
            continue
        got, failed = verdict(header, sender)
        out = {
            "server": header.server, "spf": header.results.get("spf"), "dkim": header.results.get("dkim"),
            "dmarc": header.results.get("dmarc"), "verdict": got,
        }  # fmt: skip
        return out | ({"failed": failed} if failed else {})
    return none
