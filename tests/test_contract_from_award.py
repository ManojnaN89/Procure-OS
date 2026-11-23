"""A rate contract from an approved award (task 12.3, D48, D53): drafted in the buyer's name, accepted by the supplier
through the signed link and a one-time code sent only to the letter's address, activated by a second person who is
neither its maker nor the award's approver, and then bought on by a later request (scenario P1-use, AT-02)."""

import uuid
from collections.abc import Iterator
from datetime import date
from decimal import Decimal

import pytest
from period_support import P1_DAY, Co, clear, client, make, p1_to_proposal
from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.formulas.calendar import pin_today
from p2p.masters import contract_acceptance as acceptance
from p2p.orders import contracts as usage
from p2p.orders import service as orders
from p2p.platform import routing, tenants
from p2p.sourcing import service as sourcing

D = Decimal


@pytest.fixture(scope="module")
def apex_co(control_db: str) -> Iterator[Co]:
    yield from make(control_db, "apex", "apexpumps.example")


@pytest.fixture
def apex(apex_co: Co) -> Co:
    pin_today(P1_DAY)
    clear(apex_co)
    return apex_co


def _approved(co: Co) -> str:
    rfq = p1_to_proposal(co)
    done = co.call("director", "POST", f"/api/rfqs/{rfq}/award/approve", {})
    assert done.status_code == 200, done.text
    return rfq


def test_the_draft_contract_is_the_awarded_suppliers_for_the_period_in_the_buyers_name(apex: Co) -> None:
    rfq = _approved(apex)

    c = apex.one(
        "select supplier_code, type, valid_from, valid_to, max_value, payment_terms_code, status, created_by, "
        "source_rfq, award_decision_id is not null from contract where code = 'CNT-0002'"
    )
    buyer = str(apex.uid("buyer"))
    assert tuple(c) == (
        "SUP-005", "rate_contract", date(2027, 1, 1), date(2027, 3, 31), D("792000.0000"), "NET45", "draft", buyer,
        rfq, True,
    )  # fmt: skip
    price = apex.one(
        "select item_code, uom, price, min_qty, lead_days, location_code, version, status, created_by from price_record "
        "where contract_code = 'CNT-0002'"
    )
    assert tuple(price) == ("RM-08", "KGS", D("880.0000"), 1, 5, "PLT-CKN", 1, "draft", buyer)
    shown = apex.call("buyer", "GET", "/api/contracts/CNT-0002").json()
    assert shown["source_rfq"] == rfq and shown["supplier_accepted_at"] is None and shown["status"] == "draft"
    assert sourcing.contract_options(apex.db, "RM-08", "KGS", date(2027, 1, 4), "PLT-CKN") == []  # not in force


def test_a_forwarded_link_without_the_code_decides_nothing_and_a_wrong_code_counts_its_tries(apex: Co) -> None:
    _approved(apex)
    token = apex.link("SUP-005")

    page = client.get(f"/contract/{token}")
    assert page.status_code == 200 and "Rate contract CNT-0002" in page.text and "Email me a code" in page.text
    no_code = client.post(f"/contract/{token}", data={"action": "accept", "code": ""})
    assert no_code.status_code == 409 and "needs a one-time code" in no_code.text
    asked = client.post(f"/contract/{token}/code")
    address = apex.contact("SUP-005")
    assert asked.status_code == 200 and f"A code was sent to {address}" in asked.text
    assert [m["to"] for m in apex.mails if "one-time code" in m["body"]] == [address]  # only the letter's address
    wrong = client.post(f"/contract/{token}", data={"action": "accept", "code": "000000"})
    assert wrong.status_code == 409 and "4 tries left" in wrong.text
    assert apex.one("select supplier_accepted_at from contract where code = 'CNT-0002'")[0] is None
    again = client.post(f"/contract/{token}/code")
    assert again.status_code == 409 and "a moment ago" in again.text


def test_the_supplier_accepts_once_and_a_second_person_is_asked_to_activate(apex: Co) -> None:
    _approved(apex)
    token = apex.link("SUP-005")
    client.post(f"/contract/{token}/code")
    address = apex.contact("SUP-005")

    done = client.post(f"/contract/{token}", data={"action": "accept", "code": apex.code_for(address)})

    assert done.status_code == 200 and "you accepted the contract" in done.text
    at, by = apex.one("select supplier_accepted_at, supplier_accepted_by from contract where code = 'CNT-0002'")
    assert at is not None and by == address
    assert apex.one("select used_for from contract_link")[0] == "accept"
    used = client.post(f"/contract/{token}", data={"action": "accept", "code": "123456"})
    assert used.status_code == 409 and "works once" in used.text
    assert client.get(f"/contract/{token}").status_code == 409
    task = apex.one("select assignee, title from task where kind = 'contract_activation' and status = 'open'")
    assert task[0] not in (apex.uid("buyer"), apex.uid("director")) and "CNT-0002" in task[1]
    assert apex.one("select status from one_time_code where address = :a", a=address)[0] == "used"


def test_activation_waits_for_the_supplier_and_never_by_the_award_approver_or_the_buyer(apex: Co) -> None:
    _approved(apex)

    early = apex.call("purchasemanager", "POST", "/api/contracts/CNT-0002/activate", {})
    assert early.status_code == 409 and "waits for the supplier to accept its terms" in early.text
    token = apex.link("SUP-005")
    client.post(f"/contract/{token}/code")
    client.post(f"/contract/{token}", data={"action": "accept", "code": apex.code_for(apex.contact("SUP-005"))})

    director = apex.call("director", "POST", "/api/contracts/CNT-0002/activate", {})
    assert director.status_code == 403 and "whoever approved the award does not activate its contract" in director.text
    assert apex.call("buyer", "POST", "/api/contracts/CNT-0002/activate", {}).status_code == 403
    shown = apex.call("director", "GET", "/api/contracts/CNT-0002").json()
    assert shown["activation_refused"] == "whoever approved the award does not activate its contract"
    assert apex.call("purchasemanager", "POST", "/api/contracts/CNT-0002/activate", {}).status_code == 200
    assert apex.one("select status from contract where code = 'CNT-0002'")[0] == "active"


def test_a_supplier_declines_with_a_reason_and_nothing_is_awarded_to_anyone_else(apex: Co) -> None:
    rfq = _approved(apex)
    token = apex.link("SUP-005")
    client.post(f"/contract/{token}/code")
    code = apex.code_for(apex.contact("SUP-005"))

    no_reason = client.post(f"/contract/{token}", data={"action": "decline", "code": code})
    assert no_reason.status_code == 409 and "say why you decline" in no_reason.text
    done = client.post(f"/contract/{token}", data={"action": "decline", "code": code, "reason": "Copper prices rose"})

    assert done.status_code == 200
    status, why = apex.one("select status, decline_reason from contract where code = 'CNT-0002'")
    assert (status, why) == ("declined", "Copper prices rose")
    assert apex.one("select status from price_record where contract_code = 'CNT-0002'")[0] == "withdrawn"
    task = apex.one("select title from task where kind = 'contract_declined' and status = 'open'")
    assert task[0] == "Pune Copper Co. declined CNT-0002: Copper prices rose"
    assert apex.one("select count(*) from award where rfq_no = :n and status = 'approved'", n=rfq)[0] == 1
    assert apex.one("select count(*) from contract where source_rfq = :n", n=rfq)[0] == 1  # no second contract


def test_an_expired_or_altered_link_is_refused(apex: Co) -> None:
    _approved(apex)
    token = apex.link("SUP-005")

    assert client.get(f"/contract/{token[:-2]}xx").status_code == 400
    link = acceptance.read(token)
    with pytest.raises(acceptance.LinkError, match="expired"):
        acceptance.read(token, now=link.expires + 1)
    assert (link.contract, link.supplier) == ("CNT-0002", "SUP-005")


def test_p1_use_a_later_request_buys_on_the_activated_contract_and_its_order_is_approved_by_band(apex: Co) -> None:
    _approved(apex)
    token = apex.link("SUP-005")
    client.post(f"/contract/{token}/code")
    client.post(f"/contract/{token}", data={"action": "accept", "code": apex.code_for(apex.contact("SUP-005"))})
    assert apex.call("purchasemanager", "POST", "/api/contracts/CNT-0002/activate", {}).status_code == 200
    pin_today(date(2027, 1, 4))
    made = apex.call(
        "production", "POST", "/api/requisitions",
        {"lines": [{"item_code": "RM-08", "qty": "120"}], "need_by": "2027-01-20", "location": "PLT-CKN",
         "reason": "January winding"},
    )  # fmt: skip
    no = made.json()["id"]
    assert apex.one("select est_price, est_source from request_line where request_no = :n", n=no) == (
        D("880.0000"), "contract CNT-0002"
    )  # fmt: skip
    assert (
        apex.call("production", "POST", f"/api/requisitions/{no}/submit", {"override_reason": "test"}).status_code
        == 200
    )
    assert apex.call("director", "POST", f"/api/requisitions/{no}/approve", {}).status_code == 200

    sourcing.source_request(apex.db, apex.slug, no, as_of=date(2027, 1, 4))
    orders.order_request(apex.db, apex.slug, no, as_of=date(2027, 1, 4))

    route, contract, price = apex.one(
        "select route, contract_code, price from sourcing_decision where request_no = :n", n=no
    )
    assert (route, contract, price) == ("contract", "CNT-0002", D("880.0000"))
    po = apex.one(
        "select p.taxable, p.cgst, p.sgst, p.total, p.rule_code, l.price_record_id is not null from po p "
        "join po_line l on l.po_no = p.no where l.request_no = :n",
        n=no,
    )
    assert tuple(po) == (D("105600.00"), D("9504.00"), D("9504.00"), D("124608.00"), "PO-02", True)
    step = apex.one(
        "select s.approver from po_approval_step s join po_line l on l.po_no = s.po_no where l.request_no = :n", n=no
    )
    assert step[0] == "U08"
    use = usage.usage(apex.db, "CNT-0002")
    assert (use.used, use.remaining) == (D("105600.00"), D("686400.00"))


def test_another_company_cannot_use_the_link(apex: Co) -> None:
    _approved(apex)
    token = apex.link("SUP-005")
    slug = "otherp" + uuid.uuid4().hex[:6]
    assert tenants.create_tenant(slug, load_reference=False).ok
    try:
        with Session(routing.engine_for(slug)) as other:
            assert other.execute(text("select count(*) from contract_link")).scalar_one() == 0
        link = acceptance.read(token)
        assert link.tenant == apex.slug  # the token names its own company; it opens nothing elsewhere
        forged = acceptance.Link(slug, link.id, link.contract, link.supplier, link.expires)
        with Session(routing.engine_for(slug)) as other, pytest.raises(acceptance.LinkError, match="not valid"):
            acceptance.check(other, forged)
    finally:
        routing.clear_engine_cache()
        tenants.drop_tenant(slug)
