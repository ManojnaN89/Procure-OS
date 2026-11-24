"""Every stuck item is a case: an owner, a deadline, escalation, reassignment and a re-check before it closes (block 11,
5.1-5.5). A second sweep writes nothing; the engine, not a person, says when it is resolved.
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_invoices import _goods, _invoice, ledger  # noqa: F401 - fixtures
from test_orders import _headers, shop  # noqa: F401 - fixtures
from test_receiving import _receive, store  # noqa: F401 - fixtures
from test_sourcing import PASSWORD, Outbox, db, outbox  # noqa: F401 - fixtures

from p2p.invoices import service as invoices
from p2p.platform import (
    exception_cases,
    proposals,
    routing,
    tasks,
    tenants,
    users,
    worker,
)

TODAY = date(2026, 11, 6)
D = Decimal
NOW = datetime.now(UTC)


def _person(world: Session, role: str, name: str) -> int:
    return users.create_user(
        world.info["slug"],
        f"{role.lower()}{world.info['sfx']}@x.example",
        PASSWORD,
        name,
        role,
    )


def _ap(world: Session) -> int:
    """An AP login that is a person in the organisation (U11), so a case assigned to them has a manager chain."""
    sfx = world.info["sfx"]
    world.execute(
        text(
            "insert into org_user (code, name, designation, department_code, manager_code, email, active) "
            "values ('U11', 'Ap Asha', 'AP Executive', 'FIN', 'U2', :e, true) on conflict (code) do nothing"
        ),
        {"e": f"ap{sfx}@x.example"},
    )
    world.commit()
    return users.create_user(world.info["slug"], f"ap{sfx}@x.example", PASSWORD, "Ap Asha", "AP")


def _ap2(world: Session) -> int:
    sfx = world.info["sfx"]
    world.execute(
        text(
            "insert into org_user (code, name, designation, department_code, manager_code, email, active) "
            "values ('U12', 'Ap Second', 'AP Executive', 'FIN', 'U2', :e, true) on conflict (code) do nothing"
        ),
        {"e": f"ap2{sfx}@x.example"},
    )
    world.commit()
    return users.create_user(world.info["slug"], f"ap2{sfx}@x.example", PASSWORD, "Ap Second", "AP")


def _admin(world: Session) -> int:
    return _person(world, "Admin", "Admin One")


def _cases(world: Session) -> list[dict]:
    world.expire_all()
    return [
        dict(r)
        for r in world.execute(
            text(
                "select id, kind, case_key, title, assignee, step, due_at, status, resolution "
                "from task where case_key is not null order by id"
            )
        ).mappings()
    ]


def _case_for(world: Session, key: str) -> dict:
    return next(c for c in _cases(world) if c["case_key"] == key)


def test_given_a_price_block_a_held_lot_a_failed_mail_and_a_failed_job_when_the_sweep_runs_then_each_has_one_case(
    ledger: Session, outbox: Outbox
) -> None:  # noqa: F811
    po_no, logins = _goods(ledger, qty="10", price="1000")
    _admin(ledger)
    ap = _ap(ledger)
    held = _invoice(ledger, po_no, "10", "1060", number="SW-1", user=ap)
    ledger.execute(
        text(
            "insert into stock_hold (item_code, location_code, quality, qty, left_qty, reason, source, held_by) "
            "values ('A', 'WH-BLR', 'quarantine', 20, 20, 'seals', 'manual', 'U3')"
        )
    )
    tasks.create_task(
        ledger, ledger.info["slug"], kind="email_failed", title="An email could not be sent to a@supplier.example",
        assignee=ap, sla_hours=8, payload={"linked_type": "po", "ref": "NO-SUCH-PO"}, now=NOW,
    )  # fmt: skip
    with tenants.control_engine().begin() as c:
        job = c.execute(
            text(
                "insert into job (tenant_slug, kind, payload, status, attempts, run_at, last_error) "
                "values (:t, 'raise_test', '{}', 'failed', 5, :n, 'RuntimeError: boom') returning id"
            ),
            {"t": ledger.info["slug"], "n": NOW},
        ).scalar_one()
    exception_cases.open_job_failure(ledger.info["slug"], int(job), "raise_test", 5, "RuntimeError: boom", NOW)

    first = exception_cases.sweep(ledger, ledger.info["slug"], NOW)

    cases = _cases(ledger)
    assert first["created"] >= 2, (
        cases
    )  # the invoice and the held lot; the mail and the job tasks were already the cases
    assert _case_for(ledger, f"invoice:{held.no}")["assignee"] == ap  # the invoice's enterer
    assert any(c["case_key"].startswith("held_stock:") for c in cases)
    assert any(c["kind"] == "email_failed" for c in cases)
    assert any(c["kind"] == "job_failed" for c in cases)
    for c in cases:
        assert c["status"] == "open" and c["step"] and c["due_at"] > NOW

    second = exception_cases.sweep(ledger, ledger.info["slug"], NOW + timedelta(minutes=1))

    assert (second["created"], second["moved"], second["resolved"]) == (0, 0, 0)
    assert len(_cases(ledger)) == len(cases)  # one case per key


def test_when_the_next_step_changes_the_case_moves_and_its_deadline_restarts(ledger: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, logins = _goods(ledger, qty="10", price="1000")
    ap = _ap(ledger)
    held = _invoice(ledger, po_no, "10", "1060", number="SW-2", user=ap)
    exception_cases.sweep(ledger, ledger.info["slug"], NOW)
    before = _case_for(ledger, f"invoice:{held.no}")

    invoices.request_release(ledger, held.no, "agreed", ap)
    moved = exception_cases.sweep(ledger, ledger.info["slug"], NOW + timedelta(hours=1))

    after = _case_for(ledger, f"invoice:{held.no}")
    assert moved["moved"] == 1
    assert after["assignee"] != before["assignee"] and "Procure Vikram" in after["step"]
    assert after["due_at"] > before["due_at"]


def test_a_case_no_longer_stuck_is_resolved_by_the_next_sweep(ledger: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, logins = _goods(ledger, qty="10", price="1000")
    ap = _ap(ledger)
    held = _invoice(ledger, po_no, "10", "1060", number="SW-3", user=ap)
    exception_cases.sweep(ledger, ledger.info["slug"], NOW)
    pid = invoices.request_release(ledger, held.no, "agreed", ap)
    proposals.approve(ledger, pid, logins["procurement"])

    done = exception_cases.sweep(ledger, ledger.info["slug"], NOW + timedelta(hours=2))

    assert done["resolved"] >= 1
    case = _case_for(ledger, f"invoice:{held.no}")
    assert (case["status"], case["resolution"]) == ("done", "resolved")


def test_a_person_cannot_close_an_exception_case_by_hand_and_the_recheck_refuses_a_stuck_one(
    ledger: Session, outbox: Outbox
) -> None:  # noqa: F811
    po_no, _ = _goods(ledger, qty="10", price="1000")
    ap = _ap(ledger)
    held = _invoice(ledger, po_no, "10", "1060", number="SW-4", user=ap)
    exception_cases.sweep(ledger, ledger.info["slug"], NOW)
    case = _case_for(ledger, f"invoice:{held.no}")

    with pytest.raises(exception_cases.CaseError, match="still stuck"):
        exception_cases.recheck(ledger, ledger.info["slug"], case["id"], NOW)

    with pytest.raises(exception_cases.CaseError, match="only a failed email"):
        exception_cases.close_email_failed(ledger, case["id"], "handled_otherwise", "x", 1, NOW)


def test_reassigning_a_case_needs_someone_who_may_act_and_the_right_person(ledger: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, logins = _goods(ledger, qty="10", price="1000")
    ap, ap2 = _ap(ledger), _ap2(ledger)
    done = invoices.enter_invoice(
        ledger,
        invoices.InvoiceIn(
            supplier_code="S1", supplier_invoice_no="SW-5", invoice_date=TODAY,
            supplier_gstin="27AAAAA0000A1Z5", buyer_gstin="29BBBBB0000B1Z5",
            lines=[invoices.InvLine(po_no, 1, D("10"), D("1000"))], total=D("2000"),
        ),
        ap, as_of=TODAY,
    )  # fmt: skip
    assert done.reasons == ["arithmetic_mismatch"]  # an AP person may act on this block
    exception_cases.sweep(ledger, ledger.info["slug"], NOW)
    case = _case_for(ledger, f"invoice:{done.no}")
    assert case["assignee"] == ap

    with pytest.raises(exception_cases.CaseError, match="cannot act"):
        exception_cases.reassign(
            ledger,
            ledger.info["slug"],
            case["id"],
            logins["keeper"],
            "the keeper is closer",
            logins["cfo"],
            NOW,
        )
    with pytest.raises(exception_cases.CaseError, match="reason is needed"):
        exception_cases.reassign(ledger, ledger.info["slug"], case["id"], ap2, " ", logins["cfo"], NOW)
    with pytest.raises(exception_cases.CasePermission):
        exception_cases.reassign(ledger, ledger.info["slug"], case["id"], ap2, "x", logins["keeper"], NOW)  # not above

    result = exception_cases.reassign(ledger, ledger.info["slug"], case["id"], ap2, "a second AP", logins["cfo"], NOW)

    assert result["due_at"]
    assert ledger.execute(text("select assignee from task where id = :i"), {"i": case["id"]}).scalar_one() == ap2
    assert ap2 in [c["user_id"] for c in exception_cases.candidates(ledger, case["id"], NOW)]


def test_a_case_escalates_to_the_manager_and_then_the_managers_manager_on_the_company_calendar(
    ledger: Session, outbox: Outbox
) -> None:  # noqa: F811
    po_no, logins = _goods(ledger, qty="10", price="1000")
    ap = _ap(ledger)
    held = _invoice(ledger, po_no, "10", "1060", number="SW-6", user=ap)
    exception_cases.sweep(ledger, ledger.info["slug"], NOW)
    case = _case_for(ledger, f"invoice:{held.no}")
    due = case["due_at"]

    tasks._escalate(worker.JobContext(id=-1, tenant=ledger.info["slug"], payload={"task_id": case["id"]}, now=due))  # noqa: SLF001

    ledger.expire_all()
    row = ledger.execute(
        text("select assignee, escalation_level, due_at from task where id = :i"),
        {"i": case["id"]},
    ).one()
    assert row[0] == logins["cfo"] and row[1] == 1 and row[2] > due  # U3 escalates to the CFO (U2)
    tasks._escalate(
        worker.JobContext(
            id=-1,
            tenant=ledger.info["slug"],
            payload={"task_id": case["id"]},
            now=row[2],
        )
    )  # noqa: SLF001
    ledger.expire_all()
    row = ledger.execute(
        text("select assignee, escalation_level from task where id = :i"),
        {"i": case["id"]},
    ).one()
    assert row[0] == logins["director"] and row[1] == 2  # then the director (U1)


def test_a_failed_email_is_resolved_by_a_later_mail_for_the_same_document(ledger: Session, outbox: Outbox) -> None:  # noqa: F811
    from p2p.platform import outbox as mail_outbox

    po_no, _ = _goods(ledger, qty="10", price="1000")
    ap = _ap(ledger)
    tasks.create_task(
        ledger, ledger.info["slug"], kind="email_failed", title="An email could not be sent",
        assignee=ap, sla_hours=8, payload={"linked_type": "po", "ref": "NO-SUCH-PO"}, now=NOW,
    )  # fmt: skip
    exception_cases.sweep(ledger, ledger.info["slug"], NOW)
    case = next(c for c in _cases(ledger) if c["kind"] == "email_failed")

    mail_outbox.queue_email(
        ledger,
        to="orders@supplier.example",
        subject="The order again",
        body="Please accept",
        linked_type="po",
        linked_ref="NO-SUCH-PO",
    )
    ledger.commit()
    done = exception_cases.sweep(ledger, ledger.info["slug"], NOW + timedelta(minutes=5))

    assert done["resolved"] >= 1
    assert _case_for(ledger, case["case_key"])["status"] == "done"


def test_a_held_lot_is_resolved_when_it_is_decided(ledger: Session, outbox: Outbox) -> None:  # noqa: F811
    _person(ledger, "Admin", "Admin One")
    ledger.execute(
        text(
            "insert into stock_hold (item_code, location_code, quality, qty, left_qty, reason, source, held_by) "
            "values ('A', 'WH-BLR', 'quarantine', 20, 20, 'seals', 'manual', 'U3')"
        )
    )
    ledger.commit()
    exception_cases.sweep(ledger, ledger.info["slug"], NOW)
    case = next(c for c in _cases(ledger) if c["case_key"].startswith("held_stock:"))
    ledger.execute(
        text("update stock_hold set closed_at = :n where id = :i"),
        {"n": NOW, "i": int(case["case_key"].split(":")[1])},
    )
    ledger.commit()

    exception_cases.sweep(ledger, ledger.info["slug"], NOW + timedelta(minutes=5))

    assert _case_for(ledger, case["case_key"])["status"] == "done"


def test_a_failed_job_is_resolved_when_the_job_has_run(ledger: Session, outbox: Outbox) -> None:  # noqa: F811
    _person(ledger, "Admin", "Admin One")
    with tenants.control_engine().begin() as c:
        job = c.execute(
            text(
                "insert into job (tenant_slug, kind, payload, status, attempts, run_at, last_error) "
                "values (:t, 'raise_test', '{}', 'failed', 5, :n, 'RuntimeError: boom') returning id"
            ),
            {"t": ledger.info["slug"], "n": NOW},
        ).scalar_one()
    exception_cases.open_job_failure(ledger.info["slug"], int(job), "raise_test", 5, "RuntimeError: boom", NOW)
    exception_cases.sweep(ledger, ledger.info["slug"], NOW)
    case = next(c for c in _cases(ledger) if c["kind"] == "job_failed")
    assert case["case_key"] == f"job:{job}"

    with tenants.control_engine().begin() as c:
        c.execute(
            text("update job set status = 'done', finished_at = :n where id = :i"),
            {"n": NOW, "i": int(job)},
        )
    exception_cases.sweep(ledger, ledger.info["slug"], NOW + timedelta(minutes=5))

    assert _case_for(ledger, case["case_key"])["status"] == "done"


def test_a_case_in_one_company_is_never_seen_in_another(ledger: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, _ = _goods(ledger, qty="10", price="1000")
    _admin(ledger)
    _invoice(ledger, po_no, "10", "1060", number="SW-7")
    exception_cases.sweep(ledger, ledger.info["slug"], NOW)

    other = "d" + uuid.uuid4().hex[:8]
    assert tenants.create_tenant(other, load_reference=False).ok
    try:
        with Session(routing.engine_for(other)) as db_:
            assert db_.execute(text("select count(*) from task where case_key is not null")).scalar_one() == 0
    finally:
        tenants.drop_tenant(other)
