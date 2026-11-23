"""Sender authentication of a supplier's mail (D53, block 6): only our own server's Authentication-Results counts."""

from p2p.formulas import sender_auth

OURS = "mx.apexpumps.example"
FROM = "Pune Copper <sales@punecopper.example>"


def _verdict(*headers: str, trusted: str | None = OURS, sender: str = FROM) -> dict[str, object]:
    return sender_auth.read(list(headers), trusted, sender)


def test_dmarc_pass_from_our_server_is_a_pass() -> None:
    got = _verdict(f"{OURS}; spf=pass smtp.mailfrom=punecopper.example; dkim=pass header.d=punecopper.example; "
                   "dmarc=pass header.from=punecopper.example")  # fmt: skip

    assert got == {
        "server": OURS,
        "spf": "pass",
        "dkim": "pass",
        "dmarc": "pass",
        "verdict": "pass",
    }


def test_with_no_dmarc_an_aligned_dkim_or_spf_pass_is_a_pass() -> None:
    assert _verdict(f"{OURS}; dkim=pass header.d=punecopper.example")["verdict"] == "pass"
    assert _verdict(f"{OURS}; spf=pass smtp.mailfrom=bounce@punecopper.example")["verdict"] == "pass"


def test_a_parent_domain_is_aligned_with_a_subdomain_sender_but_not_the_other_way() -> None:
    sub = "sales@mail.punecopper.example"
    assert _verdict(f"{OURS}; dkim=pass header.d=punecopper.example", sender=sub)["verdict"] == "pass"
    assert _verdict(f"{OURS}; dkim=pass header.d=mail.punecopper.example")["verdict"] == "none"
    assert _verdict(f"{OURS}; dkim=pass header.d=otherpunecopper.example")["verdict"] == "none"


def test_dmarc_fail_is_a_fail_whatever_else_passed() -> None:
    got = _verdict(f"{OURS}; spf=pass smtp.mailfrom=punecopper.example; dkim=pass header.d=punecopper.example; "
                   "dmarc=fail header.from=punecopper.example")  # fmt: skip

    assert got["verdict"] == "fail" and got["failed"] == "dmarc=fail"


def test_spf_fail_or_softfail_with_no_dkim_pass_and_no_dmarc_is_a_fail() -> None:
    assert _verdict(f"{OURS}; spf=softfail smtp.mailfrom=punecopper.example")["failed"] == "spf=softfail"
    assert _verdict(f"{OURS}; spf=fail smtp.mailfrom=punecopper.example; dkim=none")["verdict"] == "fail"
    assert _verdict(f"{OURS}; spf=fail; dkim=pass header.d=punecopper.example")["verdict"] == "pass"


def test_a_header_from_another_server_is_ignored() -> None:
    assert _verdict("relay.other.example; dmarc=pass")["verdict"] == "none"
    assert _verdict("relay.other.example; dmarc=fail")["verdict"] == "none"


def test_the_topmost_header_of_our_server_is_used() -> None:
    got = _verdict("relay.other.example; dmarc=pass", f"{OURS}; dmarc=fail", f"{OURS}; dmarc=pass")

    assert got["verdict"] == "fail"


def test_with_no_setting_every_verdict_is_none() -> None:
    assert _verdict(f"{OURS}; dmarc=pass", trusted="")["verdict"] == "none"
    assert _verdict(f"{OURS}; dmarc=pass", trusted=None)["verdict"] == "none"


def test_a_header_that_cannot_be_read_is_none() -> None:
    assert _verdict("")["verdict"] == "none"
    assert _verdict(f"{OURS}; garbage without equals")["verdict"] == "none"
    assert _verdict(f"{OURS} (comment); dmarc=pass (p=none) header.from=punecopper.example")["verdict"] == "pass"
