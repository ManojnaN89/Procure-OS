"""Short-close by risk (block 13 R1, SC1-SC4): below the limit, above it, a changed balance, an advance and taking the
ask back. Given, when, then in every test."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_invoices import _goods, ledger  # noqa: F401 - fixtures
from test_orders import shop  # noqa: F401 - fixture
from test_receiving import _sent, store  # noqa: F401 - fixtures
from test_reconciliation import _production_order
from test_sourcing import Outbox, db, outbox  # noqa: F401 - fixtures

from p2p.orders import short_close
from p2p.platform import proposals

D = Decimal
TODAY = date(2026, 11, 6)


@pytest.fixture
def short(ledger: Session) -> Session:  # noqa: F811
    """The invoice world with the short-close rule: automatic to 25,000, the director above it."""
    ledger.execute(
        text(
            "insert into approval_rule (rule_code, document, min_value, max_value, category_code, department_code, "
            "spend_type, approver_chain, version, effective_from) values "
            "('SC-01', 'short_close', 0, 25000, 'ANY', 'ANY', 'ANY', 'auto', 1, '2024-11-01'), "
            "('SC-02', 'short_close', 25000.01, 999999999, 'ANY', 'ANY', 'ANY', 'director', 1, '2024-11-01')"
        )
    )
    ledger.commit()
    return ledger


def _row(db: Session, po_no: str) -> dict:
    return dict(db.execute(text("select status, closed_at from po where no = :n"), {"n": po_no}).mappings().one())


def test_given_a_balance_below_the_limit_when_the_buyer_closes_it_then_it_closes_at_once(
    short: Session, outbox: Outbox
) -> None:
    po_no, logins = _goods(short, qty="100", price="220", received="40")
    outbox.sent.clear()

    done = short_close.ask(short, None, po_no, logins["keeper"], "Supplier will not deliver the rest")

    assert done["status"] == "closed" and done["risk"] == "low"
    line = short.execute(text("select status, short_closed_qty from po_line")).one()
    assert (line[0], D(line[1])) == ("short_closed", D("60"))
    assert short.execute(text("select count(*) from proposal")).scalar_one() == 0
    assert any("balance closed" in mail["subject"] for mail in outbox.sent)  # the supplier hears only now


def test_given_a_balance_above_the_limit_when_asked_then_it_waits_and_nothing_changes(
    short: Session, outbox: Outbox
) -> None:
    po_no, logins = _goods(short, qty="200", price="220", received="40")
    outbox.sent.clear()

    asked = short_close.ask(short, None, po_no, logins["keeper"], "Supplier will not deliver the rest")

    assert asked["status"] == "waiting_approval" and asked["risk"] == "high"
    assert asked["approver"] == "Dir Rajesh"
    assert _row(short, po_no)["status"] == "part_received"
    assert short.execute(text("select status from po_line")).scalar_one() == "part_received"
    assert not [m for m in outbox.sent if "balance closed" in m["subject"]]

    proposals.approve(short, int(asked["proposal_id"]), logins["director"])

    assert _row(short, po_no)["status"] == "received"  # supply done, money not
    assert short.execute(text("select status from po_line")).scalar_one() == "short_closed"
    assert any("balance closed" in m["subject"] for m in outbox.sent)


def test_given_goods_arriving_while_a_short_close_waits_when_approved_then_it_is_stale_and_must_be_asked_again(
    short: Session,
) -> None:
    po_no, logins = _goods(short, qty="200", price="220", received="40")
    asked = short_close.ask(short, None, po_no, logins["keeper"], "Supplier will not deliver the rest")
    # the store accepts 10 more before the director decides
    short.execute(text("update po_line set received_qty = received_qty + 10 where po_no = :p"), {"p": po_no})
    short.commit()

    with pytest.raises(proposals.StaleError, match="changed since it was asked"):
        proposals.approve(short, int(asked["proposal_id"]), logins["director"])

    row = short.execute(text("select status, note from po_short_close")).one()
    assert row[0] == "stale" and "160 then, 150 now" in row[1]
    assert (
        short.execute(text("select count(*) from audit_event where action = 'po.short_close_stale'")).scalar_one() == 1
    )


def test_given_an_open_advance_when_previewed_then_it_is_a_flag_and_goes_to_a_person(short: Session) -> None:
    po_no, _logins = _goods(short, qty="100", price="220", received="40")
    short.execute(
        text(
            "insert into payment_run (no, run_date, proposed_by, status, total) "
            "values ('PR-1', '2026-11-01', 'U2', 'paid', 5000.00)"
        )
    )
    short.execute(
        text(
            "insert into payment (no, run_no, type, supplier_code, po_no, amount, mode) values "
            "('PAY-1', 'PR-1', 'advance', 'S1', :p, 5000.00, 'neft')"
        ),
        {"p": po_no},
    )
    short.execute(
        text("insert into advance (payment_no, po_no, gross, taxable, used_gross, used_taxable, paid_on) "
             "values ('PAY-1', :p, 5000.00, 4237.29, 0, 0, '2026-11-01')"), {"p": po_no})  # fmt: skip
    short.commit()

    seen = short_close.preview(short, po_no)

    assert seen["risk"] == "high"
    assert [f["code"] for f in seen["flags"]] == ["advance_open"]
    assert "not yet used" in seen["flags"][0]["words"]


def test_given_a_waiting_short_close_when_the_asker_takes_it_back_then_it_is_withdrawn(short: Session) -> None:
    po_no, logins = _goods(short, qty="200", price="220", received="40")
    asked = short_close.ask(short, None, po_no, logins["keeper"], "Supplier will not deliver the rest")

    done = short_close.withdraw(short, po_no, int(asked["short_close_id"]), logins["keeper"])

    assert done == {"status": "withdrawn"}
    assert short.execute(text("select status from po_short_close")).scalar_one() == "withdrawn"
    assert short.execute(text("select status from po_line")).scalar_one() == "part_received"


def test_given_nothing_received_when_asked_then_cancel_instead_is_the_refusal(short: Session) -> None:
    po_no, logins = _sent(short, qty="10", price="100")

    with pytest.raises(Exception, match="cancel it instead"):
        short_close.ask(short, None, po_no, logins["keeper"], "No longer needed")


def test_given_nothing_left_on_every_chosen_line_when_asked_then_it_is_refused(short: Session) -> None:
    po_no, logins = _goods(short, qty="10", price="100", received="4")
    short.execute(text("update po_line set received_qty = qty where po_no = :p"), {"p": po_no})
    short.execute(text("update po set status = 'part_received' where no = :p"), {"p": po_no})
    short.commit()

    with pytest.raises(Exception, match="nothing is still to come"):
        short_close.ask(short, None, po_no, logins["keeper"], "No longer needed")


def test_given_production_still_needs_the_balance_when_asked_then_the_need_goes_back(short: Session) -> None:
    # SC3: a part-delivered order pegged to MO-77, whose material request line still needs the balance
    po_no, logins = _goods(short, qty="100", price="220", received="40")
    short.execute(
        text(
            "insert into number_series (document_type, prefix, scope, format, reset) "
            "values ('request', 'REQ', 'location', '{prefix}-{loc}-{fy4}-{seq:05d}', 'fy')"
        )
    )
    short.execute(text("insert into location (code, name, is_stock_location) values ('WH', 'Store', true) "
                       "on conflict (code) do nothing"))  # fmt: skip
    short.commit()
    request_no, request_line_no = short.execute(
        text("select request_no, request_line_no from po_line where po_no = :p and line_no = 1"), {"p": po_no}
    ).one()
    mo_no, mr_no = _production_order(short, str(request_no))
    short.execute(
        text("update material_request_line set need_qty = 20, reserved_qty = 0, issued_qty = 0 "
             "where mr_no = :m and item_code = 'A'"), {"m": mr_no})  # fmt: skip
    short.execute(
        text("update material_request_buy set qty = 0 where request_no = :r and request_line_no = :l"),
        {"r": request_no, "l": request_line_no},
    )
    short.commit()

    seen = short_close.preview(short, po_no)
    asked = short_close.ask(short, None, po_no, logins["keeper"], "The supplier stopped making it")

    assert seen["risk"] == "high"
    assert [f["code"] for f in seen["flags"]] == ["unmet_need"]
    assert "production still needs 20 NOS of Bearing by" in seen["flags"][0]["words"]
    assert f"({mo_no})" in seen["flags"][0]["words"]
    assert asked["status"] == "waiting_approval" and asked["approver"] == "Dir Rajesh"

    proposals.approve(short, int(asked["proposal_id"]), logins["director"])

    line = short.execute(text("select status, received_qty, short_closed_qty from po_line")).one()
    assert (line[0], D(line[1]), D(line[2])) == ("short_closed", D("40"), D("60"))
    draft = short.execute(
        text("select justification from request where justification like :j order by no desc limit 1"),
        {"j": f"Short for {mo_no}%"},
    ).scalar()
    assert draft is not None and "Short for" in draft  # the need went back to the store check
    assert short.execute(text("select status from po where no = :p"), {"p": po_no}).scalar_one() == "received"


def test_given_a_waiting_short_close_when_the_named_person_rejects_it_then_the_row_is_rejected(
    short: Session,
) -> None:
    po_no, logins = _goods(short, qty="200", price="220", received="40")
    asked = short_close.ask(short, None, po_no, logins["keeper"], "Supplier will not deliver the rest")

    proposals.reject(short, int(asked["proposal_id"]), logins["director"], "Keep the balance open")

    row = short.execute(text("select status, note from po_short_close")).one()
    assert (row[0], row[1]) == ("rejected", "Keep the balance open")
    assert short.execute(text("select status from po_line")).scalar_one() == "part_received"
    assert short.execute(text("select count(*) from audit_event where action = 'po.short_close_rejected'")
                        ).scalar_one() == 1  # fmt: skip
