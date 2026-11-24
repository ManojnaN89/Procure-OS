"""A payment run, its tax rows and its payments stay in their own company (block 12, Y11). Given, when, then."""

import uuid
from datetime import date

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_invoices import _headers, ledger  # noqa: F401 - fixtures
from test_orders import shop  # noqa: F401 - fixtures
from test_payments import _people, _propose, _two_suppliers
from test_receiving import store  # noqa: F401 - fixtures
from test_sourcing import PASSWORD, Outbox, db, outbox  # noqa: F401 - fixtures

from p2p.main import app
from p2p.platform import routing, tenants, users

client = TestClient(app)


def test_a_run_of_one_company_is_404_in_another_and_no_rows_are_shared(
    ledger: Session, outbox: Outbox, control_db: str
) -> None:  # noqa: F811
    # given a run in the first company, when a person of a second company asks for it,
    # then it is not there, and the second company has no payment, tax or advance row
    finance, _, _ = _people(ledger)
    _, inv_no = _two_suppliers(ledger)
    ledger.execute(text("update invoice set due_date = '2026-11-12', pay_by = '2026-11-12' where no = :n"), {"n": inv_no})
    ledger.commit()
    run_no = _propose(ledger, finance, date(2026, 11, 12))
    slug = "b" + uuid.uuid4().hex[:8]
    assert tenants.create_tenant(slug, load_reference=False).ok
    other = users.create_user(slug, f"fin{slug}@x.example", PASSWORD, "Other Finance", "Finance")
    try:
        headers = _headers(slug, other, "Finance")
        assert client.get(f"/api/payment-runs/{run_no}", headers=headers).status_code == 404
        session = Session(routing.engine_for(slug))
        try:
            for table in ("payment_run", "payment", "payment_allocation", "tds_entry", "advance_request", "advance"):
                assert session.execute(text(f"select count(*) from {table}")).scalar_one() == 0  # noqa: S608 - fixed names
        finally:
            session.close()
        assert ledger.execute(text("select count(*) from payment_run where no = :r"), {"r": run_no}).scalar_one() == 1
    finally:
        routing.clear_engine_cache()
        tenants.drop_tenant(slug)
