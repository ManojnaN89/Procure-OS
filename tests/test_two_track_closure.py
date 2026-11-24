"""Two-track closure (block 13 R2, D-12, TT1-TT3): supply complete and money settled, reopening, and the notice once."""

from datetime import date
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session
from test_invoices import _goods, _invoice, ledger  # noqa: F401 - fixtures
from test_orders import shop  # noqa: F401 - fixture
from test_payments import _paid_run, _people
from test_receiving import store  # noqa: F401 - fixture
from test_sourcing import db, outbox  # noqa: F401 - fixtures

from p2p.invoices import payment
from p2p.orders import closure

D = Decimal


def _status(db: Session, po_no: str) -> tuple[str, list[str]]:
    db.expire_all()
    po = db.execute(text("select status from po where no = :p"), {"p": po_no}).scalar_one()
    lines = list(db.execute(text("select status from po_line where po_no = :p"), {"p": po_no}).scalars())
    return po, lines


def test_given_supply_complete_and_an_invoice_paid_when_the_bank_settles_it_then_the_order_closes_once(
    ledger: Session, outbox: object
) -> None:  # noqa: F811
    finance, owner, _ = _people(ledger)
    po_no, _ = _goods(ledger, qty="10", price="100")
    _invoice(ledger, po_no, "10", "100")
    run = _paid_run(ledger, finance, owner)

    payment.mark_paid(ledger, run, finance, paid_on=date(2026, 11, 12), tenant=ledger.info["slug"])

    assert _status(ledger, po_no) == ("closed", ["closed"])
    assert ledger.execute(text("select count(*) from audit_event where action = 'po.close'")).scalar_one() == 1
    # a second check changes nothing
    closure.check(ledger, po_no, tenant=ledger.info["slug"])
    assert _status(ledger, po_no) == ("closed", ["closed"])


def test_given_an_order_closed_then_a_credit_note_when_booked_then_it_reopens_and_closes_again(
    ledger: Session,
) -> None:
    finance, owner, _ = _people(ledger)
    po_no, _ = _goods(ledger, qty="10", price="100")
    _invoice(ledger, po_no, "10", "100")
    run = _paid_run(ledger, finance, owner)
    payment.mark_paid(ledger, run, finance, paid_on=date(2026, 11, 12), tenant=ledger.info["slug"])
    assert _status(ledger, po_no)[0] == "closed"

    ledger.execute(
        text(
            "insert into invoice (no, type, supplier_code, supplier_invoice_no, dedupe_key, invoice_date, fy, received_at, "
            "received_on, channel, taxable, cgst, sgst, igst, cess, round_off, total, payment_block, status, tds_amount, "
            "advance_used, advance_taxable_used, discount_taken, reverse_charge) values "
            "('CN-1', 'credit_note', 'S1', 'CN-1', 'CN-1', '2026-11-12', '2026-27', now(), '2026-11-12', 'manual', "
            "-100, 0, 0, -18, 0, 0, -118, 'none', 'approved', 0, 0, 0, 0, false)"
        )
    )
    ledger.execute(
        text("insert into invoice_line (invoice_no, line_no, po_no, po_line_no, qty, unit_price, discount, taxable, "
             "gst_rate, cgst, sgst, igst) values ('CN-1', 1, :p, 1, -1, 100, 0, -100, 18, 0, 0, -18)"), {"p": po_no})  # fmt: skip
    ledger.commit()

    reopened = closure.reopen(ledger, po_no, "credit note CN-1 on the invoice", tenant=ledger.info["slug"])

    assert reopened["reopened"] is True and _status(ledger, po_no)[0] == "received"
    assert ledger.execute(text("select count(*) from audit_event where action = 'po.reopen'")).scalar_one() == 1
    seen = closure.state(ledger, po_no)
    assert seen["supply_done"] is True and not seen["settled"]
    assert [b["code"] for b in seen["blockers"]] == ["credit_open"]

    ledger.execute(text("update invoice set status = 'paid' where no = 'CN-1'"))
    ledger.commit()
    closed_again = closure.check(ledger, po_no, tenant=ledger.info["slug"])
    assert closed_again["closed"] is True and _status(ledger, po_no)[0] == "closed"


def test_given_goods_still_to_come_when_the_invoiced_part_is_paid_then_the_order_never_closes(
    ledger: Session,
) -> None:
    finance, owner, _ = _people(ledger)
    po_no, _ = _goods(ledger, qty="10", price="100", received="6")
    _invoice(ledger, po_no, "6", "100")
    run = _paid_run(ledger, finance, owner)

    payment.mark_paid(ledger, run, finance, paid_on=date(2026, 11, 12), tenant=ledger.info["slug"])

    assert _status(ledger, po_no) == ("part_received", ["part_received"])
    seen = closure.state(ledger, po_no)
    assert seen["supply_done"] is False


def test_given_a_received_but_uninvoiced_line_when_checked_then_money_is_not_settled(ledger: Session) -> None:
    po_no, _ = _goods(ledger, qty="10", price="100")

    seen = closure.state(ledger, po_no)

    assert seen["supply_done"] is True and seen["settled"] is False
    assert [b["code"] for b in seen["blockers"]] == ["not_invoiced"]
    assert seen["blockers"][0]["words"] == "line 1: 10 received, not invoiced"


def test_given_a_draft_return_on_the_order_line_when_the_money_is_checked_then_it_blocks_until_closed(
    ledger: Session, owner_session: object
) -> None:
    """Block 9's return header has no order; the order link is its line (the interface H9-2)."""
    po_no, _ = _goods(ledger, qty="10", price="100", received="6")
    from p2p.platform import db_logins  # noqa: PLC0415 - the test makes block 9's tables

    with owner_session(ledger.info["slug"]) as own:  # type: ignore[operator]
        own.execute(text("create table supplier_return (no text primary key, status text not null)"))
        own.execute(text("create table supplier_return_line (id bigserial primary key, return_no text not null, "
                         "po_no text, po_line_no integer, outcome text)"))  # fmt: skip
        own.execute(
            text(
                "grant select, insert, update, delete on supplier_return, supplier_return_line "
                f"to {db_logins.app_role_name()}"
            )
        )  # noqa: S608 - our own role name
        own.commit()
    ledger.execute(text("insert into supplier_return (no, status) values ('RTN-1', 'draft')"))
    ledger.execute(text("insert into supplier_return_line (return_no, po_no, po_line_no) values ('RTN-1', :p, 1)"),
                  {"p": po_no})  # fmt: skip
    ledger.commit()

    assert [b["code"] for b in closure.state(ledger, po_no)["blockers"] if b["code"] == "return_open"] == [
        "return_open"
    ]

    ledger.execute(text("update supplier_return set status = 'dispatched' where no = 'RTN-1'"))
    ledger.commit()
    assert any(b["code"] == "return_open" for b in closure.state(ledger, po_no)["blockers"])

    ledger.execute(text("update supplier_return_line set outcome = 'replace' where return_no = 'RTN-1'"))
    ledger.commit()
    assert not any(b["code"] == "return_open" for b in closure.state(ledger, po_no)["blockers"])


def test_given_an_open_recovery_claim_on_the_order_when_the_money_is_checked_then_it_blocks(
    ledger: Session, owner_session: object
) -> None:
    """Block 9 owns recovery_claim; block 12 settles it (H12-4)."""
    po_no, _ = _goods(ledger, qty="10", price="100", received="6")
    from p2p.platform import db_logins  # noqa: PLC0415

    with owner_session(ledger.info["slug"]) as own:  # type: ignore[operator]
        own.execute(text("create table recovery_claim (no text primary key, po_no text, po_line_no integer, "
                         "status text not null, qty numeric(18,4))"))  # fmt: skip
        own.execute(text(f"grant select, insert, update, delete on recovery_claim to {db_logins.app_role_name()}"))
        own.commit()
    ledger.execute(text("insert into recovery_claim (no, po_no, po_line_no, status, qty) "
                        "values ('RCL-1', :p, 1, 'open', 1)"), {"p": po_no})  # fmt: skip
    ledger.commit()

    assert [b["code"] for b in closure.state(ledger, po_no)["blockers"] if b["code"] == "claim_open"] == ["claim_open"]

    ledger.execute(text("update recovery_claim set status = 'settled' where no = 'RCL-1'"))
    ledger.commit()
    assert not any(b["code"] == "claim_open" for b in closure.state(ledger, po_no)["blockers"])
