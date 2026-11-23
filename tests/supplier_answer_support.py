"""Answering an order from its supplier link the way a supplier does (D53): ask for the one-time code on the page, read
it from the mail it went to, and send the answer with it. Nothing here goes around the code check."""

import re
from typing import Any

from fastapi.testclient import TestClient
from httpx import Response

CODE = re.compile(r"one-time code is (\d{6})")
_codes: dict[str, str] = {}  # token -> the code mailed for it and not yet used


def code_mail(sent: list[dict[str, Any]], to: str | None = None) -> dict[str, Any]:
    """The latest code mail (to the address, if given)."""
    found = [m for m in sent if CODE.search(str(m.get("body") or "")) and (to is None or m["to"] == to)]
    assert found, f"no code was mailed{f' to {to}' if to else ''}"
    return found[-1]


def code_from(sent: list[dict[str, Any]], to: str | None = None) -> str:
    match = CODE.search(str(code_mail(sent, to)["body"]))
    assert match
    return match.group(1)


def ask_code(client: TestClient, token: str) -> Response:
    """Press Email me a code on the link's page."""
    return client.post(f"/supplier/{token}/code")


def answer(client: TestClient, sent: list[dict[str, Any]], token: str, data: dict[str, str]) -> Response:
    """Post an answer with the code the link's address got (asked for once per link)."""
    if token not in _codes:
        before = len(sent)
        asked = ask_code(client, token)
        if len(sent) == before:  # no code could be sent (a used or stale link): answer without one, and be refused
            return client.post(f"/supplier/{token}", data=data)
        assert asked.status_code == 200, asked.text
        _codes[token] = code_from(sent[before:])
    got = client.post(f"/supplier/{token}", data={**data, "code": _codes[token]})
    if got.status_code == 200:
        _codes.pop(token, None)  # used with the answer
    return got


def service_code(db: Any, sent: list[dict[str, Any]], token: str) -> str:
    """For a test that answers through the service: the code the link's address gets (supplier_links.send_code)."""
    from p2p.orders import supplier_links  # noqa: PLC0415

    before = len(sent)
    supplier_links.send_code(db, supplier_links.read(token))
    return code_from(sent[before:])
