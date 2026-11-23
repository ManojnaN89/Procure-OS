"""Award approval of a period RFQ (task 12.2, D45, C1; scenarios P1 to P5 and B1 of the block 5 spec).

The buyer proposes the award; the person the `po` rows name for its value (never an automatic band, never the buyer)
decides; letters go once, after the approval; regret notes never say a price or the winner. On Apex Pumps and
Brightwave as `demo reset` leaves them, business date Monday 23 November 2026."""

import re
from collections.abc import Iterator
from datetime import date
from decimal import Decimal

import pytest
from period_support import P1_DAY, Co, clear, client, make, p1_to_proposal

from p2p.formulas.calendar import pin_today

D = Decimal


@pytest.fixture(scope="module")
def apex_co(control_db: str) -> Iterator[Co]:
    yield from make(control_db, "apex", "apexpumps.example")


@pytest.fixture
def apex(apex_co: Co) -> Co:
    pin_today(P1_DAY)
    clear(apex_co)
    return apex_co


def _decision(co: Co, rfq: str) -> tuple:
    return co.one(
        "select round, status, value_taxable, cgst, sgst, igst, value_total, rule_code, rule_version, approver "
        "from award_decision where rfq_no = :n order by round desc limit 1",
        n=rfq,
    )


def test_p1_the_buyer_proposes_and_the_director_is_named_for_the_value_with_no_order_and_no_budget(apex: Co) -> None:
    budget_before = apex.all("select * from budget_actual order by 1")
    orders_before = apex.one("select count(*) from po")[0]

    rfq = p1_to_proposal(apex)

    assert _decision(apex, rfq) == (
        1, "waiting", D("792000.00"), D("71280.00"), D("71280.00"), D("0.00"), D("934560.00"), "PO-03", 1, "U09"
    )  # fmt: skip
    assert apex.one("select status from rfq where no = :n", n=rfq)[0] == "award_proposed"
    assert apex.all("select status, supplier_code from award where rfq_no = :n", n=rfq) == [("proposed", "SUP-005")]
    assert apex.one("select count(*) from po")[0] == orders_before  # no order comes from a period award
    assert apex.all("select * from budget_actual order by 1") == budget_before
    action, permission, value, status = apex.one(
        "select p.action, p.required_permission, p.value, p.status from proposal p join award_decision d on d.proposal_id = p.id "
        "where d.rfq_no = :n",
        n=rfq,
    )
    assert (action, permission, value, status) == ("rfq.award_approve", "po.approve", D("934560.00"), "pending")
    task = apex.one("select title from task where kind = 'award_approval' and status = 'open'")
    assert task is not None and rfq in task[0]
    # while it waits, no new quote and no new award
    assert apex.award(rfq, {1: "SUP-003"}, reason="x").status_code == 409
    assert apex.call("buyer", "POST", f"/api/rfqs/{rfq}/quotes", {"supplier_code": "SUP-001", "lines": [
        {"rfq_line_no": 1, "unit_price": "1", "gst_rate": "18"}]}).status_code == 409  # fmt: skip


def test_p1_approval_awards_makes_the_draft_contract_and_sends_the_letters_once(apex: Co) -> None:
    rfq = p1_to_proposal(apex)

    done = apex.call("director", "POST", f"/api/rfqs/{rfq}/award/approve", {"note": "Good price"})

    assert done.status_code == 200, done.text
    assert done.json() == {"status": "approved", "contracts": ["CNT-0002"], "letters": 3}
    assert apex.one("select status from rfq where no = :n", n=rfq)[0] == "awarded"
    assert apex.one("select status from award where rfq_no = :n", n=rfq)[0] == "approved"
    assert apex.one("select status from period_demand_line")[0] == "awarded"
    letters = apex.all("select supplier_code, kind, contract_code from award_letter order by kind, supplier_code")
    assert letters == [("SUP-005", "award", "CNT-0002"), ("SUP-001", "regret", None), ("SUP-003", "regret", None)]
    award_mail = apex.sent_to(apex.contact("SUP-005"))
    assert (
        len(award_mail) == 1
        and "notice of selection, not an order and not a commitment to buy" in award_mail[0]["body"]
    )
    assert "/contract/" in award_mail[0]["body"] and award_mail[0]["attachments"][0][0] == "CNT-0002.pdf"
    # approving again finds nothing waiting, and the worker sends nothing more
    again = apex.call("director", "POST", f"/api/rfqs/{rfq}/award/approve", {})
    assert again.status_code == 409
    apex.work()
    letters_sent = [m for m in apex.mails if m["subject"].startswith(("Award notice", "Your quotation"))]
    assert len(letters_sent) == 3 and apex.one("select count(*) from award_letter")[0] == 3
    assert apex.one("select count(*) from audit_event where action = 'award.approve' and entity_id = :n", n=rfq)[0] == 1


def test_p1_regret_notes_name_no_price_quantity_value_or_other_supplier(apex: Co) -> None:
    rfq = p1_to_proposal(apex)
    apex.call("director", "POST", f"/api/rfqs/{rfq}/award/approve", {})

    for supplier in ("SUP-003", "SUP-001"):
        (note,) = apex.sent_to(apex.contact(supplier))
        body = note["body"]
        assert "we have not selected yours this time" in body and f"{rfq}-{supplier}-R1" in body
        for figure in ("880", "895", "910", "900", "792", "934", "7,92", "9,34"):
            assert figure not in body, (supplier, figure)
        for other in ("SUP-005", "Pune Copper", "SUP-003" if supplier == "SUP-001" else "SUP-001"):
            assert other not in body
        assert not re.search(r"\d{3,}", body.replace(rfq, "").replace(f"-{supplier}-R1", ""))


def test_regret_notes_turned_off_by_the_buyer_send_none(apex: Co) -> None:
    rfq = p1_to_proposal(apex, regret_notes=False)

    apex.call("director", "POST", f"/api/rfqs/{rfq}/award/approve", {})

    assert apex.all("select supplier_code, kind from award_letter") == [("SUP-005", "award")]
    assert len(apex.mails) == 1


def test_p2_the_director_rejects_and_the_buyer_selects_again_in_round_two(apex: Co) -> None:
    rfq = p1_to_proposal(apex)

    no_reason = apex.call("director", "POST", f"/api/rfqs/{rfq}/award/reject", {"reason": " "})
    assert no_reason.status_code == 422
    rejected = apex.call(
        "director", "POST", f"/api/rfqs/{rfq}/award/reject", {"reason": "Check SUP-003's delivery record"}
    )

    assert rejected.status_code == 200 and rejected.json() == {"status": "rejected"}
    assert _decision(apex, rfq)[1] == "rejected"
    assert apex.one("select status from award where rfq_no = :n", n=rfq)[0] == "rejected"
    assert apex.one("select status from rfq where no = :n", n=rfq)[0] == "sent"
    assert apex.mails == [] and apex.one("select count(*) from contract where source_rfq = :n", n=rfq)[0] == 0
    without = apex.award(rfq, {1: "SUP-003"})
    assert without.status_code == 422 and "reason" in without.text  # not the cheapest: a reason is needed
    again = apex.award(rfq, {1: "SUP-003"}, reason="Better delivery record")
    assert again.status_code == 202
    assert _decision(apex, rfq) == (
        2, "waiting", D("805500.00"), D("72495.00"), D("72495.00"), D("0.00"), D("950490.00"), "PO-03", 1, "U09"
    )  # fmt: skip


def test_p3_an_award_whose_quote_expired_is_stale_and_goes_back_to_the_buyer(apex: Co) -> None:
    rfq = p1_to_proposal(apex)
    pin_today(date(2027, 1, 1))

    stale = apex.call("director", "POST", f"/api/rfqs/{rfq}/award/approve", {})

    assert stale.status_code == 409
    assert stale.json()["detail"] == "the award is stale: SUP-005's quote expired on 31 Dec 2026"
    assert _decision(apex, rfq)[1] == "stale"
    assert apex.one("select status from award where rfq_no = :n", n=rfq)[0] == "withdrawn"
    assert apex.one("select status from rfq where no = :n", n=rfq)[0] == "sent"
    assert apex.one("select status from recommendation where kind = 'award' and business_key = :n order by id desc",
                    n=rfq)[0] == "stale"  # fmt: skip
    task = apex.one("select title from task where kind = 'award_refresh' and status = 'open'")
    assert task[0] == f"Select again on {rfq}: SUP-005's quote expired on 31 Dec 2026"
    assert apex.mails == [] and apex.one("select count(*) from award_letter")[0] == 0


def test_p4_the_automatic_band_never_approves_an_award_the_next_band_does(apex: Co) -> None:
    made = apex.period_rfq("PLT-CKN", "2027-01-01", "2027-03-31", [("RM-07", "1000")])
    rfq = made.json()["rfq_no"]
    assert made.json()["invited"] == ["SUP-002", "SUP-001", "SUP-003"] and made.json()["min_quotes"] == 2
    apex.confirm(rfq)
    apex.quote(rfq, "SUP-002", ["84.00"])
    apex.quote(rfq, "SUP-003", ["86.00"])

    proposed = apex.award(rfq, {1: "SUP-002"})

    assert proposed.status_code == 202 and proposed.json()["approver"] == "U08"
    assert _decision(apex, rfq) == (
        1, "waiting", D("84000.00"), D("0.00"), D("0.00"), D("15120.00"), D("99120.00"), "PO-02", 1, "U08"
    )  # fmt: skip


def test_p5_only_the_named_approver_decides_never_the_buyer_nor_through_the_proposals_screen(apex: Co) -> None:
    rfq = p1_to_proposal(apex)
    pid = apex.one("select proposal_id from award_decision where rfq_no = :n", n=rfq)[0]

    assert apex.call("buyer", "POST", f"/api/rfqs/{rfq}/award/approve", {}).status_code == 403
    other = apex.call("purchasemanager", "POST", f"/api/rfqs/{rfq}/award/approve", {})
    assert other.status_code == 403 and "waits for Meera Iyer" in other.text
    assert apex.call("purchasemanager", "POST", f"/api/rfqs/{rfq}/award/reject", {"reason": "no"}).status_code == 403
    assert apex.call("buyer", "POST", f"/api/admin/proposals/{pid}/approve", {}).status_code == 403
    assert apex.call("purchasemanager", "POST", f"/api/admin/proposals/{pid}/approve", {}).status_code == 403
    assert _decision(apex, rfq)[1] == "waiting"
    shown = apex.call("director", "GET", f"/api/rfqs/{rfq}").json()["award_decision"]
    assert shown["can_decide"] is True and shown["approver"] == "U09" and shown["value_total"] == "934560.00"
    assert apex.call("buyer", "GET", f"/api/rfqs/{rfq}").json()["award_decision"]["can_decide"] is False


def test_the_buyer_withdraws_an_award_before_a_decision(apex: Co) -> None:
    rfq = p1_to_proposal(apex)

    assert apex.call("purchasemanager", "POST", f"/api/rfqs/{rfq}/award/withdraw", {"reason": "x"}).status_code == 403
    done = apex.call("buyer", "POST", f"/api/rfqs/{rfq}/award/withdraw", {"reason": "A better offer is coming"})

    assert done.status_code == 200 and _decision(apex, rfq)[1] == "withdrawn"
    assert apex.one("select status from rfq where no = :n", n=rfq)[0] == "sent"
    assert apex.call("buyer", "POST", f"/api/rfqs/{rfq}/award/withdraw", {"reason": "again"}).status_code == 409


def test_the_award_note_is_the_template_from_the_engine_figures(apex: Co) -> None:
    rfq = p1_to_proposal(apex)

    note, source = apex.one("select note, note_source from award_decision where rfq_no = :n", n=rfq)

    assert source == "template"
    assert note == (
        f"Neha Kulkarni proposes to award {rfq} for 01 Jan 2027 to 31 Mar 2027 at Plant, Chakan (Pune). "
        "Line 1, Copper winding wire: Pune Copper Co. at ₹ 880.00 per KGS for 900 KGS, ₹ 7,92,000.00 before GST; "
        "ranked 1 of 3 qualifying quotes on total cost. Award value ₹ 9,34,560.00 with GST."
    )


# ---- Brightwave: too few quotes (B1) ------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def bright_co(control_db: str) -> Iterator[Co]:
    yield from make(control_db, "brightwave", "brightwave.example")


def test_b1_too_few_quotes_need_a_reason_and_the_purchase_agreement_before_the_cfo_decides(bright_co: Co) -> None:
    co = bright_co
    pin_today(P1_DAY)
    clear(co)
    made = co.period_rfq("OFF-BLR", "2027-04-01", "2028-03-31", [("HS-01", "60"), ("KB-01", "60")])
    assert made.status_code == 201, made.text
    rfq = made.json()["rfq_no"]
    assert made.json()["invited"] == ["SUP-002", "SUP-001"] and made.json()["min_quotes"] == 3
    assert "Only 2 suppliers are approved for ITH; at least 3 quotes are wanted" in made.json()["note"]
    co.confirm(rfq)
    co.quote(rfq, "SUP-002", ["2350.00", "1750.00"])
    co.quote(rfq, "SUP-001", ["2450.00", "1800.00"])

    assert co.award(rfq, {1: "SUP-002", 2: "SUP-002"}).status_code == 422
    short = co.award(rfq, {1: "SUP-002", 2: "SUP-002"}, reason="Only two suppliers for headsets")
    assert short.status_code == 200 and short.json()["status"] == "waiting"
    pid = short.json()["proposal_id"]
    assert co.one("select action from proposal where id = :i", i=pid)[0] == "rfq.award_short"
    agreed = co.call("depthead", "POST", f"/api/admin/proposals/{pid}/approve", {})
    assert agreed.status_code == 200, agreed.text

    assert _decision(co, rfq) == (
        1, "waiting", D("246000.00"), D("0.00"), D("0.00"), D("44280.00"), D("290280.00"), "PO-02", 1, "U09"
    )  # fmt: skip
    done = co.call("cfo", "POST", f"/api/rfqs/{rfq}/award/approve", {})
    assert done.status_code == 200 and done.json()["contracts"] == ["CNT-0003"]
    ceiling, terms, where = co.one(
        "select c.max_value, c.payment_terms_code, p.location_code from contract c join price_record p "
        "on p.contract_code = c.code where c.code = 'CNT-0003' limit 1"
    )
    assert (ceiling, terms, where) == (D("246000.0000"), "NET30", "OFF-BLR")
    refused = co.call("cfo", "POST", "/api/contracts/CNT-0003/activate", {})
    assert refused.status_code == 403 and "approved the award" in refused.text
    assert co.one("select count(*) from period_demand_line where status = 'awarded'")[0] == 2
    token = co.link("SUP-002")
    client.post(f"/contract/{token}/code")
    client.post(f"/contract/{token}", data={"action": "accept", "code": co.code_for(co.contact("SUP-002"))})
    activated = co.call("ithead", "POST", "/api/contracts/CNT-0003/activate", {})
    assert activated.status_code == 200, activated.text
    assert co.one("select status from contract where code = 'CNT-0003'")[0] == "active"
