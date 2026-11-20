"""The two mailboxes the company reads (requests, invoices) are set on the Company setup page, checked, and logged."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from test_approvals import db  # noqa: F401 - fixture
from test_company_setup import Setup, s  # noqa: F401 - fixture

from p2p.main import app
from p2p.platform import emails

client = TestClient(app)


def _put(s_: Setup, key: str, value: str, reason: str | None = "company mailbox set up", **kw):  # type: ignore[no-untyped-def]
    if "headers" in kw:
        return client.put(f"/api/setup/policies/{key}", json={"value": value, "reason": reason}, headers=kw["headers"])
    return s_.put(f"/api/setup/policies/{key}", json={"value": value, "reason": reason})  # approved by a second admin


def _row(s_: Setup, key: str) -> dict:
    return next(p for p in client.get("/api/setup/policies", headers=s_.a).json()["settings"] if p["key"] == key)


def test_both_mailboxes_are_on_the_policies_page_and_start_empty(s: Setup) -> None:  # noqa: F811
    for key in ("request_mailbox", "invoice_receipt_mailbox"):
        row = _row(s, key)

        assert (row["kind"], row["value"], row["set"]) == ("email", "", False)


def test_an_address_is_stored_in_lower_case_logged_with_its_reason_and_used_by_the_mail_reader(s: Setup) -> None:  # noqa: F811
    done = _put(s, "request_mailbox", " Requests@Plant.Example ")

    assert done.status_code == 200 and done.json() == {"key": "request_mailbox", "value": "requests@plant.example"}
    assert _row(s, "request_mailbox")["value"] == "requests@plant.example"
    (change,) = s.changes("policy", "request_mailbox")
    assert (change["old_value"], change["new_value"], change["reason"]) == (
        {"value": ""},
        {"value": "requests@plant.example"},
        "company mailbox set up",
    )
    audited = s.db.execute(
        text("select count(*) from audit_event where action = 'setup.policy' and entity_id = 'request_mailbox'")
    ).scalar_one()
    assert audited == 1
    # the part that reads mail looks at the same stored value
    assert emails._to_request_mailbox(s.db, ["Someone <REQUESTS@plant.example>"]) is True
    assert emails._to_request_mailbox(s.db, ["other@plant.example"]) is False


def test_an_address_can_be_cleared_to_switch_the_mailbox_off(s: Setup) -> None:  # noqa: F811
    _put(s, "invoice_receipt_mailbox", "bills@plant.example")
    assert _put(s, "invoice_receipt_mailbox", "").status_code == 200

    assert _row(s, "invoice_receipt_mailbox")["value"] == ""
    s.db.expire_all()
    assert emails._to_invoice_mailbox(s.db, ["bills@plant.example"]) is False


@pytest.mark.parametrize(
    "value", ["not an address", "a@b", "two@x.example three@x.example", "@x.example", "a@@x.example"]
)
def test_something_that_is_not_an_address_is_refused(s: Setup, value: str) -> None:  # noqa: F811
    refused = _put(s, "request_mailbox", value)

    assert refused.status_code == 422 and "email address" in refused.json()["detail"]
    assert _row(s, "request_mailbox")["set"] is False


def test_the_same_address_cannot_serve_both_mailboxes(s: Setup) -> None:  # noqa: F811
    _put(s, "request_mailbox", "inbox@plant.example")

    refused = _put(s, "invoice_receipt_mailbox", "INBOX@plant.example")

    assert refused.status_code == 422 and "already receives" in refused.json()["detail"]
    assert _put(s, "invoice_receipt_mailbox", "bills@plant.example").status_code == 200


def test_a_change_needs_a_reason_and_the_admin(s: Setup) -> None:  # noqa: F811
    assert _put(s, "request_mailbox", "a@plant.example", reason=None).status_code == 422
    assert _put(s, "request_mailbox", "a@plant.example", reason="  ").status_code == 422
    for user_id, role in ((s.buyer, "Buyer"), (s.approver, "Approver")):
        assert _put(s, "request_mailbox", "a@plant.example", headers=s.h(user_id, role)).status_code == 403
    assert _row(s, "request_mailbox")["set"] is False
