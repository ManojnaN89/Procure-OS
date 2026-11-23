"""Prospective suppliers (task 4.4, D-03): a prospect may be invited and quote; its quote counts only after an
independent identity check and with no open flag; selecting it is a conditional award, and no order is made to a
supplier that is not active by any route; a blocked supplier cannot come back under another name; a conditional award
lapses after its deadline and nothing is awarded to anyone else automatically; an RFQ keeps its quotation count."""

import uuid
from collections.abc import Iterator
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_sourcing import PASSWORD, TODAY, Outbox, _headers, _request, db, outbox  # noqa: F401 - fixtures

from p2p.formulas.gstin import gstin_check_char
from p2p.main import app
from p2p.masters import prospects, supplier_status
from p2p.orders import quotes
from p2p.orders import service as orders
from p2p.platform import proposals, users
from p2p.platform.sod import user
from p2p.sourcing import prospects as rfq_prospects
from p2p.sourcing import service

D = Decimal
client = TestClient(app)


def _gstin(state: str, pan: str) -> str:
    first = f"{state}{pan}1Z"
    return first + gstin_check_char(first)


def _form(**kw: str) -> dict[str, str]:
    pan = kw.pop("pan", "SABCS0000K")
    return {
        "legal_name": "Sabarmati Corrugators Pvt Ltd", "legal_form": "pvt_ltd", "pan": pan, "gst_reg_type": "regular",
        "gstin": _gstin("24", pan), "address": "Plot 7, Naroda GIDC", "city": "Ahmedabad", "pincode": "380009",
        "contact_name": "Sales", "email": "sales@sabarmati.example", "phone": "+91 00000 70001", "category": "C1",
        **kw,
    }  # fmt: skip


@pytest.fixture
def world(db: Session) -> Iterator[Session]:  # noqa: F811
    """A blocked supplier with a PAN, a bank account and a contact, and the people involved."""
    x = db.execute
    x(
        text(
            "insert into supplier (code, legal_name, pan, gstin, status) values ('S9', 'Shivneri Foundry Works', 'SHIPS0000F', :g, 'blocked')"
        ),
        {"g": _gstin("27", "SHIPS0000F")},
    )
    x(
        text(
            "insert into supplier_contact (supplier_code, name, email, phone, is_primary) values ('S9', 'Sales', 'sales@shivneri.example', '+91 00000 90107', true)"
        )
    )
    x(
        text(
            "insert into supplier_bank_account (supplier_code, bank_code, account_no, ifsc, verified) values ('S9', 'KKBK', '000100000107', 'KKBK0900107', true)"
        )
    )
    x(
        text(
            "insert into supplier_address (supplier_code, type, address, city, pincode) values ('S2', 'registered', '15, Industrial Area', 'Pune', '411034')"
        )
    )
    x(text("update supplier_contact set phone = '+91 00000 90005' where supplier_code = 'S2'"))
    db.commit()
    slug, sfx = db.info["slug"], db.info["sfx"]
    db.info["buyer"] = users.create_user(slug, f"pb{sfx}@x.example", PASSWORD, "A Buyer", "Buyer")
    db.info["checker"] = users.create_user(slug, f"pc{sfx}@x.example", PASSWORD, "Plant Head", "Approver")
    db.info["director"] = users.create_user(slug, f"pd{sfx}@x.example", PASSWORD, "Director", "Approver")
    yield db


def _register(db_: Session, **kw: str) -> tuple[str, list[prospects.Flag]]:
    code, flags = prospects.register(db_, _form(**kw), db_.info["buyer"])
    # registered 'today'; the tests run on an earlier fixed date
    db_.execute(text("update supplier_category set approved_from = '2024-01-01' where supplier_code = :c"), {"c": code})
    db_.commit()
    return code, flags


def _proof(db_: Session, code: str) -> int:
    doc = db_.execute(
        text(
            "insert into document (doc_type, filename, content_type, size, sha256, storage_key, linked_type, linked_id) "
            "values ('pan', 'pan.pdf', 'application/pdf', 1, 'x', :k, 'supplier', :c) returning id"
        ),
        {"c": code, "k": uuid.uuid4().hex},
    ).scalar_one()
    db_.commit()
    return int(doc)


def _check(db_: Session, code: str, who: str = "checker") -> dict:
    return prospects.check_identity(
        db_, code, db_.info[who], method="pan_and_gstin_online", document_ids=[_proof(db_, code)], note="PAN seen"
    )


# ---- registering ---------------------------------------------------------------------------------


def test_a_clean_prospect_is_registered_with_its_category_and_no_flags(world: Session) -> None:
    code, flags = _register(world)

    row = world.execute(text("select status, registered_by from supplier where code = :c"), {"c": code}).one()
    assert row == ("prospect", f"user:{world.info['buyer']}") and flags == []
    assert (
        world.execute(
            text("select category_code from supplier_category where supplier_code = :c"), {"c": code}
        ).scalar()
        == "C1"
    )
    assert (
        world.execute(text("select count(*) from audit_event where action = 'supplier.prospect_add'")).scalar_one() == 1
    )


@pytest.mark.parametrize(
    ("change", "matched"),
    [
        ({"pan": "SHIPS0000F", "legal_name": "Shivneri Castings New"}, "same PAN"),  # the blocked one under a new name
        ({"account_no": "000100000107", "ifsc": "KKBK0900107"}, "same bank account"),
        ({"legal_name": "ZTC Group"}, "sanctions list"),
    ],
)
def test_a_duplicate_or_sanctioned_prospect_is_refused_and_nothing_is_created(
    world: Session, change: dict, matched: str
) -> None:
    before = world.execute(text("select count(*) from supplier")).scalar_one()

    with pytest.raises(prospects.ProspectError, match=matched):
        _register(world, **change)

    assert world.execute(text("select count(*) from supplier")).scalar_one() == before
    assert (
        world.execute(text("select count(*) from audit_event where action = 'supplier.prospect_refused'")).scalar_one()
        == 1
    )


def test_a_contact_of_a_blocked_supplier_is_always_a_blocked_match_flag(world: Session) -> None:
    code, flags = _register(world, email="sales@shivneri.example")

    assert [(f.kind, f.other_ref) for f in flags] == [("blocked_match", "S9")]
    assert prospects.counts_as_checked(world, code) == (False, "identity not checked: does not count")


def test_a_shared_phone_and_address_are_two_flags_for_review(world: Session) -> None:
    _, flags = _register(world, phone="+91 00000 90005", address="15, Industrial Area", pincode="411034")
    assert sorted((f.kind, f.other_ref) for f in flags) == [("same_address", "S2"), ("same_contact", "S2")]


def test_a_shared_mail_domain_flags_unless_anyone_can_use_it(world: Session) -> None:
    _, own_domain = _register(world, email="other@supplier.example")
    _, public = _register(
        world, pan="GMAPG0000A", email="someone@gmail.com", phone="+91 00000 70002", address="Shed 2, Vatva GIDC"
    )
    assert [f.kind for f in own_domain][0] == "same_domain" and public == []


def test_the_api_registers_a_prospect_and_refuses_a_duplicate_with_422(world: Session) -> None:
    h = _headers(world.info["slug"], world.info["buyer"], "Buyer")

    made = client.post("/api/vendors/prospects", json=_form(), headers=h)
    again = client.post("/api/vendors/prospects", json=_form(legal_name="Another name"), headers=h)

    assert made.status_code == 200 and made.json()["status"] == "prospect"
    assert again.status_code == 422 and "refused: same GSTIN" in again.text
    listed = client.get("/api/vendors?status=prospect", headers=h).json()
    assert [v["id"] for v in listed] == [made.json()["code"]] and listed[0]["prospect"]


# ---- the identity check --------------------------------------------------------------------------


def test_the_buyer_who_registered_or_invited_never_checks_the_identity(world: Session) -> None:
    code, _ = _register(world)

    with pytest.raises(prospects.ProspectError) as by_buyer:
        _check(world, code, "buyer")
    assert by_buyer.value.status == 403
    done = _check(world, code)
    assert done["method"] == "pan_and_gstin_online"
    with pytest.raises(prospects.ProspectError) as twice:
        _check(world, code, "director")
    assert twice.value.status == 409
    assert world.execute(text("select status from supplier where code = :c"), {"c": code}).scalar() == "prospect"  # R11


def test_the_identity_check_needs_an_allowed_method_and_a_proof(world: Session) -> None:
    code, _ = _register(world)
    h = _headers(world.info["slug"], world.info["checker"], "Approver")

    bad = client.post(
        f"/api/vendors/{code}/identity-check",
        json={"method": "looked", "document_ids": [_proof(world, code)], "note": "x"},
        headers=h,
    )
    no_proof = client.post(
        f"/api/vendors/{code}/identity-check",
        json={"method": "gst_certificate_seen", "document_ids": [], "note": "x"},
        headers=h,
    )
    by_buyer = client.post(
        f"/api/vendors/{code}/identity-check",
        json={"method": "gst_certificate_seen", "document_ids": [_proof(world, code)], "note": "x"},
        headers=_headers(world.info["slug"], world.info["buyer"], "Approver"),
    )
    not_prospect = client.post(
        "/api/vendors/S1/identity-check",
        json={"method": "gst_certificate_seen", "document_ids": [1], "note": "x"},
        headers=h,
    )

    assert (bad.status_code, no_proof.status_code, not_prospect.status_code) == (422, 422, 409)
    assert by_buyer.status_code == 403  # the buyer has no right to it at all


def test_a_flag_is_cleared_only_by_someone_independent_with_a_reason(world: Session) -> None:
    code, _ = _register(world, phone="+91 00000 90005")
    (flag,) = prospects.open_flags(world, code)
    _check(world, code)
    assert prospects.counts_as_checked(world, code) == (False, "flagged for review: does not count")

    with pytest.raises(prospects.ProspectError, match="reason"):
        prospects.clear_flag(world, code, flag["id"], world.info["checker"], " ")
    with pytest.raises(prospects.ProspectError) as by_buyer:
        prospects.clear_flag(world, code, flag["id"], world.info["buyer"], "known")
    assert by_buyer.value.status == 403
    prospects.clear_flag(
        world, code, flag["id"], world.info["checker"], "a shared switchboard of the industrial estate"
    )

    assert prospects.counts_as_checked(world, code) == (True, None)


def test_a_prospect_is_activated_only_after_its_identity_check(world: Session) -> None:
    code, _ = _register(world)
    with pytest.raises(supplier_status.SupplierStatusError, match="identity"):
        supplier_status.request_change(world, code, "active", user(world.info["buyer"]), "won the RFQ", today=TODAY)


# ---- quoting, counting and the conditional award -------------------------------------------------


def _rfq(db_: Session, outbox_: Outbox, item: str = "A") -> str:
    no = _request(db_, f"R-P-{item}", [(item, "100", None, "C1")])
    service.source_request(db_, db_.info["slug"], no, as_of=TODAY, dispatch=False)
    rfq = str(db_.execute(text("select no from rfq where request_no = :n"), {"n": no}).scalar_one())
    db_.execute(
        text("update rfq set status = 'sent', confirmed_at = now(), confirmed_via = 'proposal' where no = :n"),
        {"n": rfq},
    )
    db_.commit()
    return rfq


def _quote(db_: Session, rfq: str, supplier: str, price: str) -> None:
    db_.execute(
        text("update rfq_supplier set responded_at = now() where rfq_no = :r and supplier_code = :s"),
        {"r": rfq, "s": supplier},
    )
    quotes.enter_quote(
        db_, rfq, quotes.QuoteInput(supplier, [quotes.QuoteLineInput(1, D(price), D("18"), delivery_days=1)]), user(1)
    )


def test_unchecked_prospects_quote_and_rank_but_do_not_count_and_cannot_be_selected(
    world: Session, outbox: Outbox
) -> None:  # noqa: F811
    rfq = _rfq(world, outbox)
    world.execute(text("delete from rfq_supplier where rfq_no = :r"), {"r": rfq})  # only the two prospects are asked
    world.commit()
    first, _ = _register(world)
    second, _ = _register(
        world, pan="SECOS0000B", email="s@second.example", phone="+91 00000 70009", legal_name="Second"
    )
    for code in (first, second):
        rfq_prospects.invite(world, rfq, code, world.info["buyer"])
    _quote(world, rfq, first, "90")
    _quote(world, rfq, second, "95")

    comp = quotes.comparison(world, rfq, TODAY)

    (line,) = comp.lines
    assert line.qualifying == 0 and comp.counting == 0  # fake cover quotes count for nothing
    assert [o.why_not for o in line.offers] == ["identity not checked: does not count"] * 2
    assert line.recommended is None
    with pytest.raises(quotes.QuoteError, match="identity checked before an award"):
        quotes.award(world, rfq, {1: first}, user(world.info["buyer"]), reason="cheapest", as_of=TODAY)


def test_a_checked_prospect_is_a_conditional_award_and_no_order_comes_until_it_is_active(
    world: Session, outbox: Outbox
) -> None:  # noqa: F811
    rfq = _rfq(world, outbox)
    code, _ = _register(world)
    rfq_prospects.invite(world, rfq, code, world.info["buyer"])
    _check(world, code)
    for supplier, price in (("S1", "100"), ("S2", "101"), (code, "90")):
        _quote(world, rfq, supplier, price)

    comp = quotes.comparison(world, rfq, TODAY)
    assert comp.lines[0].recommended == code and comp.counting == 3
    quotes.award(world, rfq, {1: code}, user(world.info["buyer"]), as_of=TODAY)

    assert world.execute(text("select conditional from award")).scalar_one() is True
    made = orders.order_request(
        world, world.info["slug"], str(world.execute(text("select request_no from rfq")).scalar()), as_of=TODAY
    )
    assert made.pos == [] and "waiting for the supplier's activation" in " ".join(made.skipped)
    actions = (
        world.execute(
            text("select action from audit_event where action in ('award.conditional', 'rfq.prospect_invite')")
        )
        .scalars()
        .all()
    )
    assert sorted(actions) == ["award.conditional", "rfq.prospect_invite"]


def _expired_and_valid_quotes(world: Session, outbox: Outbox) -> str:  # noqa: F811
    rfq = _rfq(world, outbox)
    for supplier in ("S1", "S2"):
        world.execute(
            text("update rfq_supplier set responded_at = now() where rfq_no = :r and supplier_code = :s"),
            {"r": rfq, "s": supplier},
        )
    quotes.enter_quote(
        world, rfq,
        quotes.QuoteInput("S1", [quotes.QuoteLineInput(1, D("90"), D("18"), delivery_days=1)], valid_until=date(2026, 11, 1)),
        user(1),
    )  # fmt: skip
    _quote(world, rfq, "S2", "100")
    return rfq


def test_an_expired_quote_cannot_be_re_selected_after_a_lapsed_award_without_a_renewed_price(
    world: Session, outbox: Outbox
) -> None:  # noqa: F811
    rfq = _expired_and_valid_quotes(world, outbox)
    world.execute(
        text(
            "insert into award (rfq_no, rfq_line_no, supplier_code, quote_no, qty, price, overridden, conditional, "
            "lapsed_at, lapse_reason) select :r, 1, 'S2', no, 100, 100, false, true, now(), 'not activated in time' "
            "from quote where rfq_no = :r and supplier_code = 'S2'"
        ),
        {"r": rfq},
    )
    world.commit()

    with pytest.raises(quotes.QuoteError, match="renewed price"):
        quotes.award(world, rfq, {1: "S1"}, user(world.info["buyer"]), reason="it was cheaper", as_of=TODAY)


def test_an_expired_quote_is_never_selected_even_in_a_first_selection_with_a_reason(  # block 6, OP-9
    world: Session,
    outbox: Outbox,  # noqa: F811
) -> None:
    rfq = _expired_and_valid_quotes(world, outbox)

    for reason in (None, "it was cheaper"):
        with pytest.raises(quotes.QuoteError, match="expired on .*: ask for a renewed price before selecting it"):
            quotes.award(world, rfq, {1: "S1"}, user(world.info["buyer"]), reason=reason, as_of=TODAY)
    assert world.execute(text("select count(*) from award")).scalar_one() == 0


def test_the_rfq_keeps_its_quotation_count_when_the_category_changes(world: Session, outbox: Outbox) -> None:  # noqa: F811
    rfq = _rfq(world, outbox)
    world.execute(text("update item_category set min_quotes = 5 where code = 'C1'"))
    world.commit()

    comp = quotes.comparison(world, rfq, TODAY)
    detail = client.get(f"/api/rfqs/{rfq}", headers=_headers(world.info["slug"], world.info["buyer"], "Buyer")).json()

    assert (comp.min_quotes, comp.min_quotes_rule) == (2, "category C1")
    assert (detail["min_quotes"], detail["min_quotes_rule"]) == (2, "category C1")


def test_a_conditional_award_lapses_on_the_tenth_working_day_and_nothing_else_is_awarded(
    world: Session, outbox: Outbox
) -> None:  # noqa: F811
    rfq = _rfq(world, outbox)
    code, _ = _register(world)
    rfq_prospects.invite(world, rfq, code, world.info["buyer"])
    _check(world, code)
    for supplier, price in (("S1", "100"), ("S2", "101"), (code, "90")):
        _quote(world, rfq, supplier, price)
    quotes.award(world, rfq, {1: code}, user(world.info["buyer"]), as_of=TODAY)
    world.execute(text("update award set at = :t"), {"t": "2026-11-06T10:00:00+05:30"})
    world.commit()
    due = rfq_prospects.deadline(world, date(2026, 11, 6))

    assert rfq_prospects.lapse_due(world, world.info["slug"], date.fromordinal(due.toordinal() - 1)) == []
    assert rfq_prospects.lapse_due(world, world.info["slug"], due) == [f"{rfq}/1"]

    assert world.execute(text("select count(*) from award where lapsed_at is null")).scalar_one() == 0
    assert world.execute(text("select count(*) from task where kind = 'prospect_lapsed'")).scalar_one() == 1
    assert world.execute(text("select status from rfq")).scalar_one() == "sent"  # back with the buyer to decide again


def test_too_few_counting_quotes_above_the_route_limit_waits_for_the_purchase_manager(
    world: Session, outbox: Outbox
) -> None:  # noqa: F811
    world.execute(text("update buying_route_rule set agree_above = 1000"))
    world.commit()
    rfq = _rfq(world, outbox)
    _quote(world, rfq, "S1", "100")  # 100 x 100 = Rs 10,000, above the Rs 1,000 limit, one quote of two

    with pytest.raises(quotes.QuoteError, match="reason is required"):
        quotes.award(world, rfq, {1: "S1"}, user(world.info["buyer"]), as_of=TODAY)
    waiting = quotes.award(world, rfq, {1: "S1"}, user(world.info["buyer"]), reason="only one replied", as_of=TODAY)
    assert waiting.proposal_id is not None and world.execute(text("select count(*) from award")).scalar_one() == 0
    proposals.approve(world, waiting.proposal_id, world.info["director"])
    assert world.execute(text("select supplier_code from award")).scalar_one() == "S1"


def test_a_category_only_prospects_can_quote_for_gets_an_rfq_that_goes_out_once_one_is_invited(
    world: Session, outbox: Outbox
) -> None:  # noqa: F811
    code, _ = _register(world, category="C3")
    no = _request(world, "R-P-C3", [("R", "1", None, "C3")])  # no active supplier is approved for C3
    service.source_request(world, world.info["slug"], no, as_of=TODAY)

    rfq = str(world.execute(text("select no from rfq where request_no = :n"), {"n": no}).scalar_one())
    assert world.execute(text("select count(*) from rfq_supplier")).scalar_one() == 0
    assert world.execute(text("select count(*) from proposal where action = 'rfq.send'")).scalar_one() == 0
    assert "invite a prospect" in str(world.execute(text("select note from rfq")).scalar_one())

    rfq_prospects.invite(world, rfq, code, world.info["buyer"])

    waiting = world.execute(text("select payload->>'rfq_no' from proposal where action = 'rfq.send'")).scalars().all()
    assert waiting == [rfq]  # sent only once the buyer confirms, like any RFQ (D42)


def test_the_deadline_of_a_conditional_award_counts_from_the_business_day_it_was_made(
    world: Session, outbox: Outbox
) -> None:  # noqa: F811
    rfq = _rfq(world, outbox)
    code, _ = _register(world)
    rfq_prospects.invite(world, rfq, code, world.info["buyer"])
    _check(world, code)
    for supplier, price in (("S1", "100"), ("S2", "101"), (code, "90")):
        _quote(world, rfq, supplier, price)
    quotes.award(world, rfq, {1: code}, user(world.info["buyer"]), as_of=TODAY)
    world.execute(text("update award set at = '2026-10-01T10:00:00+05:30'"))  # the clock, not the business day
    world.commit()

    assert world.execute(text("select awarded_on from award")).scalar_one() == TODAY
    due = rfq_prospects.deadline(world, TODAY)
    assert rfq_prospects.lapse_due(world, world.info["slug"], date.fromordinal(due.toordinal() - 1)) == []
    assert rfq_prospects.lapse_due(world, world.info["slug"], due) == [f"{rfq}/1"]


def test_activating_a_prospect_queues_the_orders_its_conditional_awards_wait_for(
    world: Session, outbox: Outbox
) -> None:  # noqa: F811
    from p2p.platform import tenants

    rfq = _rfq(world, outbox)
    code, _ = _register(world)
    rfq_prospects.invite(world, rfq, code, world.info["buyer"])
    _check(world, code)
    for supplier, price in (("S1", "100"), ("S2", "101"), (code, "90")):
        _quote(world, rfq, supplier, price)
    quotes.award(world, rfq, {1: code}, user(world.info["buyer"]), as_of=TODAY)
    request_no = str(world.execute(text("select request_no from rfq where no = :n"), {"n": rfq}).scalar_one())
    with tenants.control_engine().begin() as c:
        c.execute(text("delete from job where tenant_slug = :t"), {"t": world.info["slug"]})

    supplier_status._order_waiting_awards(world, world.info["slug"], code)

    with tenants.control_engine().begin() as c:
        queued = (
            c.execute(
                text("select payload->>'request_no' from job where tenant_slug = :t and kind = 'order_request'"),
                {"t": world.info["slug"]},
            )
            .scalars()
            .all()
        )
    assert queued == [request_no]
