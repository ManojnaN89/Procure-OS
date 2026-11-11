"""`demo populate`: the main demo company (Apex Pumps) is filled with cases at every stage, made by the real services, and is safe to run."""

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.formulas.calendar import business_today
from p2p.platform import demo, mail, routing, tenants
from p2p.platform.demo_cases import EARLY, LATER, STAGES, Populated, populate


@dataclass
class Filled:
    slug: str
    db: Session
    result: Populated


@pytest.fixture(scope="module")
def filled(control_db: str, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Filled]:
    folder = tmp_path_factory.mktemp("populate")
    slug = "apexp" + uuid.uuid4().hex[:8]
    previous = mail.send_email
    mail.send_email = lambda to, subject, body, **kw: str(kw.get("message_id"))
    try:
        demo.reset_company(demo.company("apex"), folder / "dataset", credentials=None, slug=slug)
        result = populate(slug)
        with Session(routing.engine_for(slug)) as session:
            yield Filled(slug, session, result)
    finally:
        mail.send_email = previous
        routing.clear_engine_cache()
        tenants.drop_tenant(slug)


def one(f: Filled, sql: str, **params: object) -> tuple[object, ...]:
    f.db.rollback()
    row = f.db.execute(text(sql), params).first()
    assert row is not None, sql
    return tuple(row)


def first_number(f: Filled, stage: str) -> str:
    return f.result.cases[stage].split(",")[0].strip()


def test_every_stage_has_a_case_and_nothing_stopped(filled: Filled) -> None:
    assert filled.result.stopped is None
    assert set(filled.result.cases) == set(STAGES)


def pending_approver(f: Filled, no: str) -> tuple[object, ...]:
    return one(
        f,
        "select r.status, s.approver from request r join approval_step s on s.request_no = r.no "
        "where r.no = :n and s.status = 'pending'",
        n=no,
    )


def test_the_stock_check_made_a_quotation_request_that_waits_for_a_buyer(filled: Filled) -> None:
    rfq = first_number(filled, EARLY[0])
    status, proposal_status = one(
        filled, "select r.status, p.status from rfq r join proposal p on p.id = r.proposal_id where r.no = :n", n=rfq
    )
    assert (status, proposal_status) == ("draft", "pending")  # nothing is sent until a buyer approves it


def test_the_new_requests_are_a_draft_one_for_the_plant_head_and_one_for_the_director(filled: Filled) -> None:
    assert one(filled, "select status from request where no = :n", n=filled.result.cases[EARLY[1]]) == ("draft",)
    assert pending_approver(filled, filled.result.cases[EARLY[2]]) == ("submitted", "U07")  # the plant head
    assert pending_approver(filled, filled.result.cases[EARLY[3]]) == ("submitted", "U09")  # the director


def test_one_quotation_request_is_sent_with_quotes_in_and_not_awarded(filled: Filled) -> None:
    rfq = filled.result.cases[EARLY[4]]
    status, quotes, orders = one(
        filled,
        "select r.status, (select count(*) from quote q where q.rfq_no = r.no), "
        "(select count(*) from po_line l where l.request_no = r.request_no) from rfq r where r.no = :n",
        n=rfq,
    )
    assert status == "sent" and quotes >= 2 and orders == 0  # type: ignore[operator]


def test_the_orders_are_at_the_stages_they_are_named_for(filled: Filled) -> None:
    expected = {
        LATER[0]: "submitted",
        LATER[1]: "approved",
        LATER[2]: "acknowledged",
        LATER[3]: "part_received",
        LATER[4]: "received",
    }
    for stage, status in expected.items():
        assert one(filled, "select status from po where no = :n", n=filled.result.cases[stage]) == (status,), stage


def test_invoices_and_payment_runs_are_at_the_stages_they_are_named_for(filled: Filled) -> None:
    today = business_today()
    status, due = one(filled, "select status, due_date from invoice where no = :n", n=filled.result.cases[LATER[5]])
    assert status == "approved" and isinstance(due, date) and due > today  # not due yet
    assert one(filled, "select status, payment_block from invoice where no = :n", n=filled.result.cases[LATER[6]]) == (
        "blocked",
        "match",
    )
    assert one(
        filled, "select count(*) from proposal where action = 'invoice.release_block' and status = 'pending'"
    ) == (1,)
    assert one(filled, "select status from payment_run where no = :n", n=filled.result.cases[LATER[7]]) == ("proposed",)
    assert one(filled, "select status from payment_run where no = :n", n=filled.result.cases[LATER[8]]) == (
        "file_generated",
    )
    assert one(filled, "select status from invoice where no = :n", n=filled.result.cases[LATER[9]]) == ("paid",)
    assert one(filled, "select count(*) from po where status = 'closed'") == (1,)  # the paid case


def test_the_two_spares_used_up_are_left_for_the_stock_check_to_suggest(filled: Filled) -> None:
    assert filled.result.low_items_left == ["SP-02", "SP-03"]


def test_what_was_made_was_made_by_the_people_and_left_audit_rows(filled: Filled) -> None:
    # nothing here was written by the populate code itself: every request, order and invoice has a history
    missing = one(
        filled,
        "select (select count(*) from request t where not exists (select 1 from audit_event a where a.entity_id = t.no)) "
        "+ (select count(*) from po t where not exists (select 1 from audit_event a where a.entity_id = t.no)) "
        "+ (select count(*) from invoice t where not exists (select 1 from audit_event a where a.entity_id = t.no))",
    )
    assert missing == (0,)


def test_a_company_that_already_has_requests_is_refused(filled: Filled) -> None:
    with pytest.raises(demo.DemoError, match="already has requests"):
        populate(filled.slug)


def test_a_company_that_does_not_exist_is_refused(control_db: str) -> None:
    with pytest.raises(demo.DemoError, match="no company"):
        populate("nosuchcompany")
