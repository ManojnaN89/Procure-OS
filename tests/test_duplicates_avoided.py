"""The 'duplicates stopped' figure: requests that looked like a repeat and were given up, and invoices set aside as a copy."""

from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session
from test_approvals import Logins, _request, db, logins  # noqa: F401 - fixtures

from p2p.platform import control_tower
from p2p.requests import duplicates

D = Decimal


def _gasket(db: Session, no: str, total: str) -> str:
    _request(db, no, total, requester="U4")
    db.execute(
        text(
            "update request_line set qty = 10, base_qty = 10, est_price = 100, free_text = 'Gasket' where request_no = :n"
        ),
        {"n": no},
    )
    db.commit()
    return no


def test_a_repeat_that_was_given_up_counts_and_one_that_went_ahead_on_a_reason_does_not(
    db: Session, logins: Logins
) -> None:  # noqa: F811
    _gasket(db, "R-D1", "1000.00")
    given_up = _gasket(db, "R-D2", "1000.00")
    went_ahead = _gasket(db, "R-D3", "1500.00")
    assert control_tower.duplicates_avoided(db)["count"] == 0  # nothing flagged yet, nothing stopped

    for no in (given_up, went_ahead):
        duplicates.record_flags(db, no, duplicates.find_duplicates(db, no))
    db.commit()
    duplicates.override(db, went_ahead, "the second batch is for the new line", logins.keeper)
    assert control_tower.duplicates_avoided(db)["count"] == 0  # flagged but still open: nobody has stopped it yet

    db.execute(text("update request set status = 'cancelled' where no in (:a, :b)"), {"a": given_up, "b": went_ahead})
    db.commit()

    got = control_tower.duplicates_avoided(db)
    assert got["requests"] == {"count": 1, "value": "1000.00"}  # the one that went ahead on a reason is not 'avoided'
    assert got["invoices"] == {"count": 0, "value": "0"}
    assert (got["count"], got["value"]) == (1, "1000.00")


def test_an_invoice_set_aside_as_a_copy_counts_with_its_value(db: Session) -> None:  # noqa: F811
    db.execute(text("insert into supplier (code, legal_name, trade_name, status) values ('SD', 'Dup Supplier', null, 'active')"))
    for no, block, status, total in (
        ("PI-D1", "duplicate", "cancelled", "590.00"),
        ("PI-D2", "duplicate", "blocked", "100.50"),
        ("PI-D3", "none", "approved", "999.00"),  # an ordinary invoice is not counted
    ):
        db.execute(
            text(
                "insert into invoice (no, type, supplier_code, supplier_invoice_no, invoice_date, fy, received_on, taxable, total, "
                "payment_block, status, dedupe_key) values (:n, 'invoice', 'SD', :n, '2026-11-01', '2026-27', '2026-11-02', 0, :t, :b, :s, :n)"
            ),
            {"n": no, "t": total, "b": block, "s": status},
        )
    db.commit()

    got = control_tower.duplicates_avoided(db)["invoices"]

    assert got == {"count": 2, "value": "690.50"}
