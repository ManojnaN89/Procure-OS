"""Sourcing: contract price or quotation request for each approved line, who is asked, and how the request is sent."""

import time
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.agents import autonomy
from p2p.formulas.calendar import BusinessCalendar
from p2p.formulas.sourcing import PriceOption, pick_contract_price, reminder_due
from p2p.formulas.suppliers import SupplierDocument, check_documents, required_documents
from p2p.main import app
from p2p.platform import mail, proposals, routing, sod, tenants, users, worker
from p2p.platform.tokens import create_access_token
from p2p.sourcing import jobs, reminders, service

D = Decimal
client = TestClient(app)
TODAY = date(2026, 11, 6)  # a Friday
PASSWORD = "correct horse battery"


# ---- the price rule ---------------------------------------------------------------------------


def opt(supplier: str, price: str, min_qty: str = "1", lead: int | None = 10) -> PriceOption:
    return PriceOption(supplier, f"K-{supplier}", D(price), D(min_qty), lead, None)


@pytest.mark.parametrize(
    ("qty", "expected_price"),
    [
        ("1", "100"),
        ("49", "100"),
        ("50", "90"),  # a break at exactly the quantity applies
        ("99.999", "90"),
        ("100", "80"),
        ("5000", "80"),
    ],
)
def test_a_supplier_is_held_to_the_best_break_the_quantity_reaches(qty: str, expected_price: str) -> None:
    breaks = [opt("S1", "100", "1"), opt("S1", "90", "50"), opt("S1", "80", "100")]

    assert pick_contract_price(breaks, D(qty)).price == D(expected_price)  # type: ignore[union-attr]


def test_a_quantity_below_every_break_has_no_contract_price() -> None:
    assert pick_contract_price([opt("S1", "100", "10")], D("9.999")) is None
    assert pick_contract_price([], D(5)) is None


def test_between_suppliers_the_lowest_price_for_the_quantity_wins() -> None:
    options = [opt("S1", "100", "1"), opt("S1", "70", "500"), opt("S2", "95", "1"), opt("S3", "60", "1000")]

    assert pick_contract_price(options, D(10)).supplier_code == "S2"  # type: ignore[union-attr]
    assert pick_contract_price(options, D(500)).supplier_code == "S1"  # type: ignore[union-attr]  # S1's 500-break, 70
    assert pick_contract_price(options, D(1000)).supplier_code == "S3"  # type: ignore[union-attr]


def test_equal_prices_go_to_the_shorter_lead_time_then_the_supplier_code() -> None:
    assert pick_contract_price([opt("S2", "50", lead=5), opt("S1", "50", lead=9)], D(1)).supplier_code == "S2"  # type: ignore[union-attr]
    assert pick_contract_price([opt("S2", "50", lead=None), opt("S1", "50", lead=30)], D(1)).supplier_code == "S1"  # type: ignore[union-attr]
    assert pick_contract_price([opt("S2", "50", lead=7), opt("S1", "50", lead=7)], D(1)).supplier_code == "S1"  # type: ignore[union-attr]


# ---- a company to source in -------------------------------------------------------------------


def _world(db: Session) -> None:
    """Items in three categories, suppliers approved for them, and contracts of every kind."""
    x = db.execute
    x(text("insert into company (code, legal_name, short_name) values ('C1', 'CBC Private Limited', 'CBC')"))
    x(text("insert into location (code, name, is_stock_location) values ('WH-BLR', 'Bengaluru store', true)"))
    x(
        text(
            "insert into item_category (code, name, buyer_user, min_quotes) values "
            "('C1', 'Spares', 'U8', 2), ('C2', 'Packaging', 'U8', 3), ('C3', 'Rare goods', 'U8', 2)"
        )
    )
    x(
        text(
            "insert into item (code, name, category_code, base_uom, purchase_uom, active, stocked) values "
            "('A', 'Bearing', 'C1', 'NOS', 'NOS', true, true), ('B', 'Seal', 'C1', 'NOS', 'NOS', true, true), "
            "('D', 'Carton', 'C2', 'NOS', 'NOS', true, true), ('R', 'Rare part', 'C3', 'NOS', 'NOS', true, true)"
        )
    )
    suppliers = [
        ("S1", "Alpha Traders", "active"),
        ("S2", "Bravo Supplies", "active"),
        ("S3", "Charlie Works", "active"),
        ("S4", "Delta Parts", "active"),
        ("S5", "Echo Mailless", "active"),  # no email on file
        ("S6", "Foxtrot Draft", "draft"),  # not yet activated
        ("S7", "Zorin Trading Company Limited", "active"),  # on the sanctions list
    ]
    for code, name, status in suppliers:
        x(
            text("insert into supplier (code, legal_name, trade_name, status) values (:c, :n, null, :s)"),
            {"c": code, "n": name, "s": status},
        )
        x(
            text(
                "insert into supplier_category (supplier_code, category_code, approved_from) values (:c, 'C1', '2024-01-01')"
            ),
            {"c": code},
        )
        for doc_type in ("pan", "cancelled_cheque"):
            x(
                text(
                    "insert into supplier_document (supplier_code, doc_type, number, issued_on, verified) "
                    "values (:c, :t, 'X', '2020-01-01', true)"
                ),
                {"c": code, "t": doc_type},
            )
        if code != "S5":
            x(
                text("insert into supplier_contact (supplier_code, name, email, is_primary) values (:c, :n, :e, true)"),
                {"c": code, "n": f"Contact {code}", "e": f"{code.lower()}@supplier.example"},
            )
    for code in ("S1", "S2"):  # the packaging suppliers: only two, though the category wants three quotes
        x(
            text(
                "insert into supplier_category (supplier_code, category_code, approved_from) values (:c, 'C2', '2024-01-01')"
            ),
            {"c": code},
        )
    x(
        text(
            "insert into sanction_entry (name, country, list_name, listed_on, aliases) values ('Zorin Trading Company Limited', 'X', 'List', '2023-05-12', 'ZTC Group')"
        )
    )
    x(
        text(
            "insert into contract (code, supplier_code, type, valid_from, valid_to, status) values "
            "('K-ACTIVE', 'S1', 'rate_contract', '2026-01-01', '2026-12-31', 'active'), "
            "('K-EXPIRED', 'S2', 'rate_contract', '2025-01-01', '2025-12-31', 'expired'), "
            "('K-DRAFT', 'S3', 'rate_contract', '2026-01-01', '2026-12-31', 'draft')"
        )
    )
    x(
        text(
            "insert into number_series (document_type, prefix, scope, format, reset) values ('rfq', 'RFQ', 'company', '{prefix}-{fy4}-{seq:05d}', 'fy')"
        )
    )
    x(
        text(
            "insert into org_setting (key, value) values ('default_rfq_response_days', '5'), ('min_quotes_default', '2')"
        )
    )
    db.commit()


def _price(
    db: Session,
    supplier: str,
    item: str,
    price: str,
    *,
    contract: str | None = "K-ACTIVE",
    min_qty: str = "1",
    uom: str = "NOS",
    valid_from: str = "2026-01-01",
    valid_to: str | None = "2026-12-31",
    lead: int = 10,
) -> None:
    db.execute(
        text(
            "insert into price_record (supplier_code, item_code, contract_code, uom, price, min_qty, valid_from, valid_to, lead_days) "
            "values (:s, :i, :c, :u, :p, :m, :f, :t, :l)"
        ),
        {
            "s": supplier,
            "i": item,
            "c": contract,
            "u": uom,
            "p": price,
            "m": min_qty,
            "f": valid_from,
            "t": valid_to,
            "l": lead,
        },
    )
    db.commit()


@pytest.fixture
def db(control_db: str) -> Iterator[Session]:
    slug = "q" + uuid.uuid4().hex[:8]
    assert tenants.create_tenant(slug, load_reference=False).ok
    with Session(routing.engine_for(slug)) as session:
        session.info["slug"] = slug
        session.info["sfx"] = uuid.uuid4().hex[:6]
        _world(session)
        yield session
    routing.clear_engine_cache()


def _request(
    db: Session,
    no: str,
    lines: list[tuple[str | None, str, str | None, str]],
    *,
    status: str = "approved",
    total: str = "50000",
) -> str:
    """lines: (item code, quantity, free text, category)."""
    db.execute(
        text(
            "insert into request (no, ship_to_location, type, channel, status, total_value) "
            "values (:n, 'WH-BLR', 'goods', 'planning', :s, :t)"
        ),
        {"n": no, "s": status, "t": total},
    )
    for n, (item, qty, free, cat) in enumerate(lines, start=1):
        db.execute(
            text(
                "insert into request_line (request_no, line_no, item_code, free_text, qty, uom, category_code, need_by, status) "
                "values (:r, :n, :i, :f, :q, 'NOS', :c, '2026-11-20', 'open')"
            ),
            {"r": no, "n": n, "i": item, "f": free, "q": qty, "c": cat},
        )
    db.commit()
    return no


def _source(db: Session, no: str, **kw: Any) -> service.SourcingResult:
    return service.source_request(db, db.info["slug"], no, as_of=TODAY, **kw)


class Outbox:
    """Stands in for the mail server: remembers every message and can be told to refuse an address."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.refuse: set[str] = set()

    def send_email(self, to: str, subject: str, body: str, **kw: Any) -> str:
        if to in self.refuse:
            raise OSError(f"mailbox {to} refused")
        self.sent.append({"to": to, "subject": subject, "body": body, **kw})
        return str(kw.get("message_id"))


@pytest.fixture
def outbox(monkeypatch: pytest.MonkeyPatch) -> Outbox:
    box = Outbox()
    monkeypatch.setattr(mail, "send_email", box.send_email)
    return box


def _auto_send(db: Session) -> None:
    autonomy.configure(db, sod.user(1), "rfq.send", level="auto")


# ---- contract or quotation --------------------------------------------------------------------


def test_only_a_contract_price_in_force_on_the_date_counts(db: Session) -> None:
    _price(db, "S1", "A", "100")  # a good one
    _price(db, "S1", "B", "100", valid_to="2026-11-05")  # ended yesterday
    _price(db, "S1", "D", "100", valid_from="2026-11-07")  # starts tomorrow
    _price(
        db, "S2", "A", "50", contract="K-EXPIRED", valid_from="2025-01-01", valid_to="2025-12-31"
    )  # contract expired
    _price(db, "S3", "A", "40", contract="K-DRAFT")  # contract never activated
    _price(db, "S4", "A", "30", contract=None)  # a price list, not a contract
    _price(db, "S1", "R", "100", uom="BOX")  # bought in another unit

    def found(item: str, uom: str = "NOS", on: date = TODAY) -> list[str]:
        return [f"{o.supplier_code}:{o.price}" for o in service.contract_options(db, item, uom, on)]

    assert found("A") == ["S1:100.0000"]
    assert found("B") == []
    assert found("B", on=date(2026, 11, 5)) == [
        "S1:100.0000"
    ]  # the last day of the price is still a day it is in force
    assert found("D") == [] and found("D", on=date(2026, 11, 7)) == ["S1:100.0000"]
    assert found("R", "NOS") == [] and found("R", "BOX") == ["S1:100.0000"]


def test_a_supplier_that_is_no_longer_active_has_no_contract_price(db: Session) -> None:
    _price(db, "S1", "A", "100")
    db.execute(text("update supplier set status = 'inactive' where code = 'S1'"))
    db.commit()

    assert service.contract_options(db, "A", "NOS", TODAY) == []


def test_each_line_is_bought_on_contract_or_goes_into_a_quotation_request(db: Session) -> None:
    _price(db, "S1", "A", "100", min_qty="1")
    _price(db, "S1", "A", "90", min_qty="50")
    no = _request(db, "R-1", [("A", "60", None, "C1"), ("B", "5", None, "C1"), (None, "2", "Special gasket", "C1")])

    result = _source(db, no, dispatch=False)

    by_line = {o.line_no: o for o in result.lines}
    assert (by_line[1].method, by_line[1].supplier_code, by_line[1].contract_code, by_line[1].price) == (
        "contract",
        "S1",
        "K-ACTIVE",
        D("90"),
    )
    assert "Contract K-ACTIVE with Alpha Traders" in by_line[1].reason and "per NOS" in by_line[1].reason
    assert by_line[2].method == "rfq" and "No contract price in force" in by_line[2].reason
    assert by_line[3].method == "rfq" and "free-text line" in by_line[3].reason
    rows = db.execute(
        text("select line_no, status from request_line where request_no = :n order by line_no"), {"n": no}
    ).all()
    assert rows == [(1, "sourcing"), (2, "sourcing"), (3, "sourcing")]
    decisions = db.execute(
        text("select line_no, method, price, rfq_no from sourcing_decision where request_no = :n order by line_no"),
        {"n": no},
    ).all()
    assert [(d[0], d[1]) for d in decisions] == [(1, "contract"), (2, "rfq"), (3, "rfq")]
    assert decisions[0][2] == D("90") and decisions[1][3] == "RFQ-2627-00001"


def test_a_quantity_below_the_first_price_break_is_quoted_not_bought_on_contract(db: Session) -> None:
    _price(db, "S1", "A", "90", min_qty="50")
    no = _request(db, "R-2", [("A", "10", None, "C1")])

    (line,) = _source(db, no, dispatch=False).lines

    assert line.method == "rfq" and "first price break is higher" in line.reason


def test_only_an_approved_request_can_be_sourced(db: Session) -> None:
    for status in ("draft", "submitted", "rejected"):
        no = _request(db, f"R-{status}", [("B", "1", None, "C1")], status=status)
        with pytest.raises(service.SourcingError, match="only an approved request"):
            _source(db, no)
    with pytest.raises(service.SourcingError, match="does not exist"):
        _source(db, "NOPE")


def test_sourcing_twice_does_not_ask_twice(db: Session) -> None:
    no = _request(db, "R-3", [("B", "5", None, "C1")])
    first = _source(db, no, dispatch=False)

    second = _source(db, no, dispatch=False)

    assert first.rfqs == ["RFQ-2627-00001"] and second.lines == [] and second.rfqs == []
    assert db.execute(text("select count(*) from rfq")).scalar_one() == 1


# ---- who is asked -----------------------------------------------------------------------------


def test_the_suppliers_asked_are_those_approved_for_the_category_with_a_history_first(db: Session) -> None:
    _price(db, "S3", "B", "70", contract=None)  # Charlie has quoted this item before
    no = _request(db, "R-4", [("B", "5", None, "C1")])

    result = _source(db, no, dispatch=False)

    asked = [
        r[0]
        for r in db.execute(
            text("select supplier_code from rfq_supplier where rfq_no = :r order by supplier_code"),
            {"r": result.rfqs[0]},
        )
    ]
    assert asked == ["S1", "S2", "S3"]  # three asked: the one with history (S3) and the first two by code
    assert result.notes.count("S5 left out: no email address on file") == 1
    assert "S7 left out: matches the sanctions list" in result.notes
    assert not any("S6" in n for n in result.notes)  # a draft supplier is not even considered
    # the history decides who is asked: for item A, S4 has quoted before, so S4 is asked in place of S3
    _price(db, "S4", "A", "70", contract=None)
    other = _request(db, "R-5", [("A", "5", None, "C1")])
    again = _source(db, other, dispatch=False)
    asked2 = [
        r[0]
        for r in db.execute(
            text("select supplier_code from rfq_supplier where rfq_no = :r order by supplier_code"),
            {"r": again.rfqs[0]},
        )
    ]
    assert asked2 == ["S1", "S2", "S4"]


def test_the_rfq_carries_its_lines_the_due_date_and_the_buyer(db: Session) -> None:
    no = _request(db, "R-6", [("B", "5", None, "C1"), (None, "2", "Special gasket", "C1")])

    result = _source(db, no, dispatch=False)

    rfq = db.execute(text("select no, buyer, request_no, category_code, due_date, status, proposal_id from rfq")).one()
    assert tuple(rfq)[:6] == (
        "RFQ-2627-00001",
        "U8",
        "R-6",
        "C1",
        date(2026, 11, 13),
        "draft",
    )  # five working days from a Friday
    lines = db.execute(
        text("select line_no, request_line_no, item_code, description, qty, uom from rfq_line order by line_no")
    ).all()
    assert [tuple(x) for x in lines] == [
        (1, 1, "B", "Seal", D("5"), "NOS"),
        (2, 2, None, "Special gasket", D("2"), "NOS"),
    ]
    assert result.rfqs == ["RFQ-2627-00001"]


def test_one_quotation_request_is_made_for_each_category_in_the_request(db: Session) -> None:
    no = _request(db, "R-7", [("B", "5", None, "C1"), ("D", "9", None, "C2")])

    result = _source(db, no, dispatch=False)

    assert result.rfqs == ["RFQ-2627-00001", "RFQ-2627-00002"]
    cats = db.execute(text("select no, category_code from rfq order by no")).all()
    assert [tuple(c) for c in cats] == [("RFQ-2627-00001", "C1"), ("RFQ-2627-00002", "C2")]


def test_fewer_approved_suppliers_than_the_category_wants_is_flagged_on_the_rfq(db: Session) -> None:
    no = _request(db, "R-8", [("D", "9", None, "C2")])  # C2 asks for 3 quotes and has 2 suppliers

    result = _source(db, no, dispatch=False)

    note = db.execute(text("select note from rfq where no = :r"), {"r": result.rfqs[0]}).scalar_one()
    assert note == "Only 2 suppliers are approved for C2; at least 3 quotes are wanted"
    assert db.execute(text("select count(*) from rfq_supplier")).scalar_one() == 2


def test_lines_nobody_can_be_asked_about_are_left_for_a_buyer_with_the_reason(db: Session) -> None:
    no = _request(db, "R-9", [("R", "1", None, "C3"), (None, "1", "Odd item", "")])  # no supplier approved; no category

    result = _source(db, no, dispatch=False)

    assert [(o.method, o.rfq_no) for o in result.lines] == [("manual", None), ("manual", None)]
    assert result.rfqs == []
    assert db.execute(text("select count(*) from rfq")).scalar_one() == 0
    statuses = [
        r[0] for r in db.execute(text("select status from request_line where request_no = 'R-9' order by line_no"))
    ]
    assert statuses == ["open", "open"]  # still open: someone has to deal with them
    reasons = {r[0] for r in db.execute(text("select reason from sourcing_decision"))}
    assert "No active supplier approved for category C3 with an email address" in reasons
    assert any("No category" in r for r in reasons)


def test_the_number_of_suppliers_asked_is_a_setting(db: Session) -> None:
    db.execute(text("insert into app_setting (key, value) values ('rfq_supplier_count', '2')"))
    db.commit()
    no = _request(db, "R-10", [("B", "5", None, "C1")])

    result = _source(db, no, dispatch=False)

    assert (
        db.execute(text("select count(*) from rfq_supplier where rfq_no = :r"), {"r": result.rfqs[0]}).scalar_one() == 2
    )


# ---- sending ----------------------------------------------------------------------------------


def test_by_default_the_rfq_waits_for_a_buyer_to_approve_sending(db: Session, outbox: Outbox) -> None:
    no = _request(db, "R-11", [("B", "5", None, "C1")])

    result = _source(db, no)

    assert outbox.sent == [] and result.sent == 0
    (pid,) = result.proposals
    proposal = db.execute(
        text("select action, status, summary, proposed_by_type, required_permission from proposal where id = :i"),
        {"i": pid},
    ).one()
    assert proposal[:2] == ("rfq.send", "pending") and proposal[3:] == ("service", "rfq.send")
    assert proposal[2] == (
        "Send RFQ-2627-00001 to 3 suppliers: a person confirms it first "
        "(automation setting “Send a request for quotation”: propose)"
    )
    assert db.execute(text("select status, proposal_id from rfq")).one() == ("draft", pid)


def test_a_buyer_who_approves_the_proposal_sends_the_emails(db: Session, outbox: Outbox) -> None:
    slug = db.info["slug"]
    buyer = users.create_user(slug, f"buyer{db.info['sfx']}@x.example", PASSWORD, "Buyer", "Buyer")
    no = _request(db, "R-12", [("B", "5", None, "C1")])
    (pid,) = _source(db, no).proposals

    proposals.approve(db, pid, buyer)

    assert sorted(m["to"] for m in outbox.sent) == ["s1@supplier.example", "s2@supplier.example", "s3@supplier.example"]
    assert db.execute(text("select status from rfq")).scalar_one() == "sent"
    assert db.execute(text("select status from proposal where id = :i"), {"i": pid}).scalar_one() == "approved"
    assert db.execute(text("select count(*) from rfq_supplier where invited_at is not null")).scalar_one() == 3


def test_when_set_to_automatic_the_emails_go_out_at_once_and_say_what_is_wanted(db: Session, outbox: Outbox) -> None:
    _auto_send(db)
    no = _request(db, "R-13", [("B", "5", None, "C1"), (None, "2.5", "Special gasket", "C1")])

    result = _source(db, no)

    assert result.sent == 3 and result.proposals == []
    first = next(m for m in outbox.sent if m["to"] == "s1@supplier.example")
    assert first["subject"] == "Request for quotation RFQ-2627-00001: Spares"
    assert first["reply_to"].startswith("rfq+RFQ-2627-00001@")  # replies come back tagged with the RFQ
    body = first["body"]
    assert "Dear Contact S1," in body and "CBC invites you to quote" in body
    assert (
        "Reference: RFQ-2627-00001" in body
        and "Please reply by: 13 Nov 2026" in body
        and "Deliver to: Bengaluru store" in body
    )
    assert "1. Seal (B) - 5 NOS - needed by 20 Nov 2026" in body
    assert "2. Special gasket - 2.5 NOS" in body
    assert db.execute(text("select status, sent_at is not null from rfq")).one() == ("sent", True)
    assert (
        db.execute(
            text("select count(*) from email_message where direction = 'out' and linked_id = 'RFQ-2627-00001'")
        ).scalar_one()
        == 3
    )
    assert (
        db.execute(
            text(
                "select count(*) from autonomy_outcome where decision_type = 'rfq.send' and auto and ref = 'RFQ-2627-00001'"
            )
        ).scalar_one()
        == 1
    )
    assert (
        db.execute(
            text("select count(*) from audit_event where action = 'rfq.send' and entity_id = 'RFQ-2627-00001'")
        ).scalar_one()
        == 1
    )


def test_the_kill_switch_stops_automatic_sending(db: Session, outbox: Outbox) -> None:
    _auto_send(db)
    autonomy.set_kill_switch(db, sod.user(1), True, "incident")
    no = _request(db, "R-14", [("B", "5", None, "C1")])

    result = _source(db, no)

    assert outbox.sent == [] and len(result.proposals) == 1


def test_a_value_above_the_autonomy_cap_waits_for_a_person(db: Session, outbox: Outbox) -> None:
    autonomy.configure(db, sod.user(1), "rfq.send", level="auto", value_cap=D("10000"))
    no = _request(db, "R-15", [("B", "5", None, "C1")], total="50000")

    result = _source(db, no)

    assert outbox.sent == [] and len(result.proposals) == 1


def test_one_supplier_whose_email_fails_does_not_hold_up_the_rest_and_is_retried(db: Session, outbox: Outbox) -> None:
    outbox.refuse = {"s2@supplier.example"}
    _auto_send(db)
    no = _request(db, "R-16", [("B", "5", None, "C1")])

    result = _source(db, no)

    assert result.sent == 3  # every invitation is queued in one transaction (DF-28); two leave at once
    assert sorted(m["to"] for m in outbox.sent) == ["s1@supplier.example", "s3@supplier.example"]
    db.rollback()
    state = {
        r[0]: (r[1] is not None, r[2])
        for r in db.execute(text("select supplier_code, invited_at, send_error from rfq_supplier"))
    }
    assert state["S1"] == (True, None) and state["S3"] == (True, None)
    assert state["S2"][0] is True and "refused" in state["S2"][1]  # shown on the RFQ page; the email job retries it
    assert db.execute(text("select status from rfq")).scalar_one() == "sent"

    outbox.refuse = set()
    assert service.send_rfq(db, "RFQ-2627-00001") == 0  # nobody is invited twice
    worker.run_once(tenant=db.info["slug"])  # the email job sends the one that had failed
    assert [m["to"] for m in outbox.sent].count("s2@supplier.example") == 1
    db.rollback()
    assert db.execute(text("select send_error from rfq_supplier where supplier_code = 'S2'")).scalar_one() is None
    worker.run_once(tenant=db.info["slug"])
    assert len(outbox.sent) == 3


def test_an_rfq_that_is_closed_cannot_be_sent(db: Session, outbox: Outbox) -> None:
    no = _request(db, "R-17", [("B", "5", None, "C1")])
    (rfq_no,) = _source(db, no, dispatch=False).rfqs
    db.execute(text("update rfq set status = 'cancelled' where no = :n"), {"n": rfq_no})
    db.commit()

    with pytest.raises(service.SourcingError, match="cancelled"):
        service.send_rfq(db, rfq_no)


# ---- audit, sweeping and the job --------------------------------------------------------------


def test_every_decision_and_every_rfq_is_in_the_audit_log(db: Session) -> None:
    _price(db, "S1", "A", "100")
    no = _request(db, "R-18", [("A", "1", None, "C1"), ("B", "5", None, "C1")])

    _source(db, no, dispatch=False)

    (decide,) = (
        db.execute(text("select after from audit_event where action = 'sourcing.decide' and entity_id = 'R-18'"))
        .scalars()
        .all()
    )
    assert decide["contract"] == 1 and decide["rfq"] == 1 and decide["rfqs"] == ["RFQ-2627-00001"]
    (create,) = db.execute(text("select after from audit_event where action = 'rfq.create'")).scalars().all()
    assert create["request"] == "R-18" and create["suppliers"] == ["S1", "S2", "S3"] and create["lines"] == 1


def test_the_sweep_sources_every_approved_request_that_still_has_open_lines(db: Session) -> None:
    done = _request(db, "R-19", [("B", "5", None, "C1")])
    _source(db, done, dispatch=False)
    waiting = _request(db, "R-20", [("B", "6", None, "C1")])
    _request(db, "R-21", [("B", "7", None, "C1")], status="submitted")  # not approved: left alone

    results = service.sweep(db, db.info["slug"], as_of=TODAY, dispatch=False)

    assert [r.request_no for r in results] == [waiting]


def test_the_worker_job_sources_a_request_and_ignores_one_that_is_not_approved(db: Session) -> None:
    slug = db.info["slug"]
    ok = _request(db, "R-22", [("B", "5", None, "C1")])
    not_ready = _request(db, "R-23", [("B", "5", None, "C1")], status="draft")
    now = datetime(2026, 11, 6, 5, 0, tzinfo=UTC)

    jobs._run(worker.JobContext(id=-1, tenant=slug, payload={"request_no": ok}, now=now))
    jobs._run(
        worker.JobContext(id=-1, tenant=slug, payload={"request_no": not_ready}, now=now)
    )  # no error, nothing done

    assert db.execute(text("select count(*) from sourcing_decision where request_no = :n"), {"n": ok}).scalar_one() == 1
    assert (
        db.execute(text("select count(*) from sourcing_decision where request_no = :n"), {"n": not_ready}).scalar_one()
        == 0
    )


# ---- the screen's data ------------------------------------------------------------------------


def _headers(slug: str, user_id: int, role: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user_id, slug, role)}"}


def test_the_pages_show_the_sourcing_of_each_line_and_the_rfq_with_its_suppliers(db: Session, outbox: Outbox) -> None:
    slug, sfx = db.info["slug"], db.info["sfx"]
    buyer = users.create_user(slug, f"b{sfx}@x.example", PASSWORD, "Buyer", "Buyer")
    h = _headers(slug, buyer, "Buyer")
    _price(db, "S1", "A", "100")
    no = _request(db, "R-30", [("A", "1", None, "C1"), ("B", "5", None, "C1")])
    _auto_send(db)

    done = client.post(f"/api/requisitions/{no}/source", headers=h).json()
    assert (done["contract"], done["rfq"], done["emails_sent"], done["rfqs"]) == (1, 1, 3, ["RFQ-2627-00001"])

    detail = client.get(f"/api/requisitions/{no}", headers=h).json()
    by_name = {line["name"]: line for line in detail["lines"]}
    assert (
        by_name["Bearing"]["source"]["method"] == "contract" and by_name["Bearing"]["source"]["contract"] == "K-ACTIVE"
    )
    assert by_name["Seal"]["source"]["rfq_no"] == "RFQ-2627-00001"
    assert detail["approval"]["can_source"] is False  # nothing left to decide

    listed = client.get("/api/rfqs", headers=h).json()
    assert listed[0]["id"] == "RFQ-2627-00001" and listed[0]["vendor_count"] == 3 and listed[0]["status"] == "sent"
    assert listed[0]["due_date"] == "2026-11-13" or listed[0]["due_date"]  # a date, as text
    rfq = client.get("/api/rfqs/RFQ-2627-00001", headers=h).json()
    assert rfq["lines"][0]["name"] == "Seal" and rfq["lines"][0]["qty"] == "5"
    assert [(s["code"], s["state"]) for s in rfq["suppliers"]] == [
        ("S1", "invited"),
        ("S2", "invited"),
        ("S3", "invited"),
    ]
    assert rfq["quotes"] == [] and rfq["request_no"] == no
    assert client.get("/api/rfqs/NOPE", headers=h).status_code == 404
    assert client.get("/api/rfqs").status_code == 401


def test_who_may_source_a_request_and_when(db: Session, outbox: Outbox) -> None:
    slug, sfx = db.info["slug"], db.info["sfx"]
    buyer = _headers(slug, users.create_user(slug, f"b{sfx}@x.example", PASSWORD, "Buyer", "Buyer"), "Buyer")
    other = _headers(slug, users.create_user(slug, f"r{sfx}@x.example", PASSWORD, "Req", "Requester"), "Requester")
    approved = _request(db, "R-31", [("B", "5", None, "C1")])
    draft = _request(db, "R-32", [("B", "5", None, "C1")], status="draft")

    assert client.get(f"/api/requisitions/{approved}", headers=buyer).json()["approval"]["can_source"] is True
    assert client.get(f"/api/requisitions/{approved}", headers=other).json()["approval"]["can_source"] is False
    assert client.post(f"/api/requisitions/{approved}/source", headers=other).status_code == 403
    refused = client.post(f"/api/requisitions/{draft}/source", headers=buyer)
    assert refused.status_code == 409 and "only an approved request" in refused.json()["detail"]
    assert client.post(f"/api/requisitions/{approved}/source").status_code == 401

    # without automatic sending the buyer's click prepares the RFQ and a proposal; the buyer may then approve it
    made = client.post(f"/api/requisitions/{approved}/source", headers=buyer).json()
    assert made["emails_sent"] == 0 and len(made["proposals"]) == 1
    assert client.get("/api/rfqs", headers=buyer).json()[0]["waiting_to_send"] is True


# ---- one real message through the mail server -------------------------------------------------


def _mailpit_up() -> bool:
    try:
        return httpx.get("http://127.0.0.1:8025/api/v1/info", timeout=2).status_code == 200
    except httpx.HTTPError:
        return False


@pytest.mark.skipif(not _mailpit_up(), reason="Mailpit is not running (docker compose up -d)")
def test_the_rfq_really_arrives_in_the_suppliers_mailbox_with_the_reply_address(db: Session) -> None:
    _auto_send(db)
    marker = uuid.uuid4().hex[:8]
    db.execute(
        text("update supplier_contact set email = :e where supplier_code = 'S1'"),
        {"e": f"s1-{marker}@supplier.example"},
    )
    db.commit()
    no = _request(db, "R-40", [("B", "5", None, "C1")])

    _source(db, no)

    found = None
    for _ in range(20):
        hits = httpx.get(
            "http://127.0.0.1:8025/api/v1/search", params={"query": f"to:s1-{marker}@supplier.example"}
        ).json()
        if hits.get("messages"):
            found = hits["messages"][0]
            break
        time.sleep(0.25)
    assert found is not None, "the message did not arrive"
    assert found["Subject"].startswith("Request for quotation RFQ-")
    message = httpx.get(f"http://127.0.0.1:8025/api/v1/message/{found['ID']}").json()
    assert message["ReplyTo"][0]["Address"].startswith("rfq+RFQ-")
    assert "Seal (B) - 5 NOS" in message["Text"]


# ---- reminders to suppliers who stay silent -----------------------------------------------------

FRI, MON, TUE, WED, THU = (date(2026, 11, d) for d in (6, 9, 10, 11, 12))
REPLY_BY = date(2026, 11, 13)


def _due(
    today: date,
    *,
    sent: int = 0,
    after: int = 2,
    most: int = 2,
    last: date = FRI,
    due: date = REPLY_BY,
) -> bool:
    return reminder_due(
        last_contact=last,
        reminders_sent=sent,
        max_reminders=most,
        after_days=after,
        today=today,
        due_date=due,
        calendar=BusinessCalendar.of(),
    )


@pytest.mark.parametrize(
    ("today", "expected"),
    [
        (FRI, False),
        (MON, False),
        (TUE, True),
        (WED, True),
    ],  # two working days after Friday is Tuesday, not Sunday
)
def test_a_reminder_is_due_after_the_working_days_not_the_calendar_days(today: date, expected: bool) -> None:
    assert _due(today) is expected


def test_the_reminder_rule_stops_at_its_limits() -> None:
    assert _due(REPLY_BY) is True  # the reply date itself is still a day to remind
    assert _due(date(2026, 11, 14)) is False  # after the reply date it is a decision for the buyer
    assert _due(TUE, sent=1, most=2) is True and _due(TUE, sent=2, most=2) is False
    assert _due(TUE, after=0) is False  # zero switches reminders off
    assert _due(REPLY_BY, last=TUE) is True and _due(WED, last=TUE) is False  # counted from the last reminder
    on_a_holiday = reminder_due(
        last_contact=FRI,
        reminders_sent=0,
        max_reminders=2,
        after_days=2,
        today=TUE,
        due_date=REPLY_BY,
        calendar=BusinessCalendar.of([MON]),
    )
    assert on_a_holiday is False  # a holiday does not count as a working day


def _sent_rfq(db: Session, outbox: Outbox) -> str:
    """An RFQ sent on Friday 6 November to S1, S2 and S3."""
    _auto_send(db)
    no = _request(db, "R-30", [("B", "5", None, "C1")])
    _source(db, no)
    db.execute(text("update rfq_supplier set invited_at = '2026-11-06 10:00+05:30'"))
    db.commit()
    outbox.sent.clear()
    return str(db.execute(text("select no from rfq")).scalar_one())


def _remind(db: Session, today: date, **kw: Any) -> reminders.ReminderResult:
    return reminders.run(
        db,
        as_of=today,
        now=datetime.combine(today, datetime.min.time(), tzinfo=UTC),
        **kw,
    )


def test_suppliers_who_stay_silent_are_reminded_twice_and_then_left_alone(db: Session, outbox: Outbox) -> None:
    rfq = _sent_rfq(db, outbox)

    assert _remind(db, MON).sent == []  # too early
    first = _remind(db, TUE)
    assert sorted(s for _, s in first.sent) == ["S1", "S2", "S3"]
    mail_one = next(m for m in outbox.sent if m["to"] == "s1@supplier.example")
    assert mail_one["subject"].startswith(f"Reminder: request for quotation {rfq}")
    assert mail_one["reply_to"].startswith(f"rfq+{rfq}@")  # a reply still comes back tagged with the RFQ
    assert "reminder 1" in mail_one["body"] and "Please reply by: 13 Nov 2026" in mail_one["body"]
    assert "1. Seal (B) - 5 NOS" in mail_one["body"]
    assert _remind(db, TUE).sent == []  # the same day again sends nothing
    assert _remind(db, WED).sent == []  # two working days have not passed since Tuesday
    second = _remind(db, THU)
    assert len(second.sent) == 3 and "reminder 2" in outbox.sent[-1]["body"]
    assert _remind(db, REPLY_BY).sent == []  # the limit of two
    assert set(db.execute(text("select reminders_sent from rfq_supplier")).scalars()) == {2}
    assert db.execute(text("select count(*) from audit_event where action = 'rfq.remind'")).scalar_one() == 6


def test_a_supplier_who_has_answered_in_any_way_is_not_reminded(db: Session, outbox: Outbox) -> None:
    rfq = _sent_rfq(db, outbox)
    db.execute(text("update rfq_supplier set responded_at = now() where supplier_code = 'S1'"))  # a quote was entered
    db.execute(text("update rfq_supplier set declined = true where supplier_code = 'S2'"))
    db.commit()
    assert [s for _, s in _remind(db, TUE, dry_run=True).sent] == ["S3"]
    db.execute(  # S3 replied by email and a buyer has not looked at it yet
        text(
            "insert into email_message (message_id, direction, from_addr, to_addr, subject, body_text, sent_at, "
            "linked_type, linked_id) values ('<r@x>', 'in', 'Contact S3 <S3@Supplier.example>', 'p@x', 's', 'b', now(), "
            "'rfq', :r)"
        ),
        {"r": rfq},
    )
    db.commit()

    assert _remind(db, TUE).sent == [] and outbox.sent == []


def test_a_supplier_whose_reply_is_waiting_as_a_draft_is_not_reminded(db: Session, outbox: Outbox) -> None:
    rfq = _sent_rfq(db, outbox)
    db.execute(text("update rfq_supplier set declined = true where supplier_code in ('S2', 'S3')"))
    db.execute(
        text("insert into quote_draft (rfq_no, supplier_code, email_id, status) values (:r, 'S1', 1, 'needs_person')"),
        {"r": rfq},
    )
    db.commit()

    assert _remind(db, TUE).sent == []


def test_no_reminder_goes_out_while_the_kill_switch_is_on_or_for_an_rfq_that_is_not_open(
    db: Session, outbox: Outbox
) -> None:
    rfq = _sent_rfq(db, outbox)
    autonomy.set_kill_switch(db, sod.user(1), True, "incident")
    stopped = _remind(db, TUE)
    assert stopped.sent == [] and stopped.stopped == "the kill switch is on"
    autonomy.set_kill_switch(db, sod.user(1), False, "over")
    db.execute(text("update rfq set status = 'awarded' where no = :r"), {"r": rfq})
    db.commit()

    assert _remind(db, TUE).sent == [] and outbox.sent == []


def test_a_reminder_the_mail_server_refuses_is_counted_once_and_its_mail_is_retried(
    db: Session, outbox: Outbox
) -> None:
    _sent_rfq(db, outbox)
    outbox.refuse = {"s2@supplier.example"}
    before = len(outbox.sent)

    done = _remind(db, TUE)

    # the reminder is written with its mail (11a.1): counted once, and the email job sends it when the server takes it
    assert len(done.sent) == 3 and not done.failed and len(outbox.sent) == before + 2
    assert db.execute(text("select reminders_sent from rfq_supplier where supplier_code = 'S2'")).scalar_one() == 1
    outbox.refuse = set()
    worker.run_once(tenant=db.info["slug"])
    assert len(outbox.sent) == before + 3
    assert _remind(db, WED).sent == []  # nobody is reminded twice for one round


def test_the_daily_job_sends_reminders_and_queues_the_next_morning(db: Session, outbox: Outbox) -> None:
    _sent_rfq(db, outbox)
    slug = db.info["slug"]
    now = datetime(2026, 11, 10, 4, 30, tzinfo=UTC)  # 10:00 in India

    reminders._run(worker.JobContext(id=-1, tenant=slug, payload={}, now=now))

    assert len(outbox.sent) == 3
    with tenants.control_engine().connect() as c:
        queued = (
            c.execute(
                text("select run_at from job where tenant_slug = :t and kind = 'rfq_reminders' and status = 'queued'"),
                {"t": slug},
            )
            .scalars()
            .all()
        )
    assert len(queued) == 1 and queued[0] == datetime(2026, 11, 11, 4, 0, tzinfo=UTC)  # 09:30 India time


def test_the_rfq_page_shows_how_often_a_supplier_was_reminded(db: Session, outbox: Outbox) -> None:
    rfq = _sent_rfq(db, outbox)
    _remind(db, TUE)
    slug, sfx = db.info["slug"], db.info["sfx"]
    buyer = users.create_user(slug, f"b{sfx}@x.example", PASSWORD, "Buyer", "Buyer")

    r = client.get(f"/api/rfqs/{rfq}", headers=_headers(slug, buyer, "Buyer"))

    assert r.status_code == 200
    assert {s["code"]: s["reminders"] for s in r.json()["suppliers"]} == {
        "S1": 1,
        "S2": 1,
        "S3": 1,
    }


# ---- supplier documents -------------------------------------------------------------------------


def _doc(kind: str, expires: str | None = None, verified: bool = True) -> SupplierDocument:
    return SupplierDocument(kind, date.fromisoformat(expires) if expires else None, verified)


@pytest.mark.parametrize(
    ("gst", "msme", "expected"),
    [
        # the bank proof is not needed before payment (owner decision 9, 22 Nov 2026)
        (None, None, ("pan",)),
        ("composition", "none", ("pan",)),
        ("regular", "none", ("pan", "gst_cert")),
        ("regular", "small", ("pan", "gst_cert", "udyam")),
        ("unregistered", "micro", ("pan", "udyam")),
    ],
)
def test_the_documents_a_supplier_must_have_follow_its_gst_and_msme_status(
    gst: str | None, msme: str | None, expected: tuple[str, ...]
) -> None:
    assert required_documents(gst, msme) == expected


def test_a_required_document_must_be_there_verified_and_not_expired() -> None:
    required = ("pan", "gst_cert")
    today = date(2026, 11, 10)

    assert check_documents(required, [_doc("pan"), _doc("gst_cert")], today).ok
    assert check_documents(required, [_doc("pan")], today).blocking == ["GST certificate is missing"]
    assert check_documents(required, [_doc("pan"), _doc("gst_cert", verified=False)], today).blocking == [
        "GST certificate is not verified"
    ]
    assert check_documents(required, [_doc("pan"), _doc("gst_cert", "2026-11-09")], today).blocking == [
        "GST certificate has expired"
    ]
    assert check_documents(required, [_doc("pan"), _doc("gst_cert", "2026-11-10")], today).ok  # the last day is good
    # an old expired copy does not matter when a newer verified one is on file
    assert check_documents(
        required, [_doc("pan"), _doc("gst_cert", "2025-01-01"), _doc("gst_cert", "2028-01-01")], today
    ).ok


def test_other_documents_only_warn_when_they_expire_or_are_about_to() -> None:
    today = date(2026, 11, 10)
    docs = [
        _doc("pan"),
        _doc("iso", "2026-10-31"),  # expired
        _doc("insurance", "2026-12-10"),  # 30 days away: warns
        _doc("licence", "2026-12-11"),  # 31 days away: does not yet
        _doc("lower_tds_cert"),  # never expires
    ]

    check = check_documents(("pan",), docs, today, warn_days=30)

    assert check.ok
    assert check.warnings == ["insurance expires on 10 Dec 2026", "ISO certificate expired on 31 Oct 2026"]


def test_a_supplier_with_a_document_missing_or_expired_is_not_asked_to_quote(db: Session) -> None:
    db.execute(text("delete from supplier_document where supplier_code = 'S2' and doc_type = 'pan'"))
    db.execute(text("update supplier set gst_reg_type = 'regular' where code = 'S3'"))
    db.execute(
        text(
            "insert into supplier_document (supplier_code, doc_type, number, issued_on, expires_on, verified) "
            "values ('S3', 'gst_cert', 'G', '2020-01-01', '2026-11-05', true)"
        )
    )
    db.commit()

    chosen, notes = service.eligible_suppliers(db, "C1", ["A"], TODAY)

    assert [c.code for c in chosen] == ["S1", "S4"]
    assert "S2 left out: PAN card is missing" in notes
    assert "S3 left out: GST certificate has expired" in notes


def test_a_contract_supplier_with_bad_documents_is_not_used_and_the_line_goes_to_quotation(db: Session) -> None:
    _price(db, "S1", "A", "100")
    db.execute(text("update supplier_document set verified = false where supplier_code = 'S1' and doc_type = 'pan'"))
    db.commit()
    no = _request(db, "R-40", [("A", "5", None, "C1")])

    result = _source(db, no, dispatch=False)

    (line,) = result.lines
    assert (
        line.method == "rfq"
        and "contract supplier cannot be used" in line.reason
        and "PAN card is not verified" in line.reason
    )


def test_the_vendor_page_lists_the_documents_and_what_is_wrong_with_them(db: Session) -> None:
    slug, sfx = db.info["slug"], db.info["sfx"]
    buyer = users.create_user(slug, f"b{sfx}@x.example", PASSWORD, "Buyer", "Buyer")
    db.execute(
        text(
            "insert into supplier_document (supplier_code, doc_type, number, issued_on, expires_on, verified) "
            "values ('S1', 'iso', 'ISO-1', '2020-01-01', '2026-10-31', true)"
        )
    )
    db.execute(text("delete from supplier_document where supplier_code = 'S2' and doc_type = 'pan'"))
    db.commit()
    h = _headers(slug, buyer, "Buyer")

    s1 = client.get("/api/vendors/S1", headers=h).json()["documents"]
    s2 = client.get("/api/vendors/S2", headers=h).json()["documents"]

    assert (
        s1["ok"] is True
        and s1["problems"] == []
        and len(s1["warnings"]) == 1
        and "ISO certificate expired" in s1["warnings"][0]
    )
    assert {d["type"]: d["expired"] for d in s1["on_file"]}["ISO certificate"] is True
    assert s2["ok"] is False and s2["problems"] == ["PAN card is missing"]
