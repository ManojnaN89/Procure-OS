"""Block 10's tenant and permission isolation: a bill is seen only by those who may see invoices or are named on it, and
one company never reads another's bill, rule or key. Given two people and two companies, when each asks, then the answer
is theirs alone.
"""

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_non_po_invoices import _enter, bills  # noqa: F401 - fixtures
from test_orders import _headers, shop  # noqa: F401 - fixtures
from test_sourcing import PASSWORD, Outbox, db, outbox  # noqa: F401 - fixtures

from p2p.invoices import service as invoices
from p2p.main import app
from p2p.platform import routing, tenants, users

client = TestClient(app)


def test_p1_a_bill_is_seen_only_by_invoice_people_or_the_people_named_on_it(bills: Session, outbox: Outbox) -> None:  # noqa: F811
    slug, sfx = bills.info["slug"], bills.info["sfx"]
    ap = bills.info["bills"]["ap"]
    named = bills.info["bills"]["owner"]  # U4, the cost centre's owner and the bill's authoriser
    bills.execute(text("insert into org_user (code, name, designation, department_code, email, active) values ('U8', 'Store Person', 'Storekeeper', 'LOG', :e, true)"), {"e": f"store2{sfx}@x.example"})  # fmt: skip
    bills.commit()
    store = users.create_user(slug, f"store2{sfx}@x.example", PASSWORD, "Store Person", "Store")
    bills.execute(text("insert into cost_centre (code, name, department_code, location_code, owner_user, active) values ('CC-MNT', 'Maintenance', 'LOG', 'PLT-CKN', 'U2', true)"))  # fmt: skip
    bills.commit()
    first = _enter(bills, number="SNL/2627/0458", on=date(2026, 11, 23))
    other = _enter(bills, number="SNL/2627/0459", on=date(2026, 11, 23), cost_centre="CC-MNT", taxable="12000.00")

    assert client.get(f"/api/invoices/{first.no}", headers=_headers(slug, ap, "AP")).status_code == 200
    options = client.get("/api/invoices/non-po/options?supplier=S1", headers=_headers(slug, ap, "AP"))
    assert options.status_code == 200 and [o["category"] for o in options.json()] == ["UTL"]
    assert client.get("/api/invoice-drafts", headers=_headers(slug, ap, "AP")).status_code == 200
    assert client.get(f"/api/invoices/{first.no}", headers=_headers(slug, named, "Requester")).status_code == 200
    assert client.get(f"/api/invoices/{other.no}", headers=_headers(slug, named, "Requester")).status_code == 403
    assert client.get(f"/api/invoices/{first.no}", headers=_headers(slug, store, "Store")).status_code == 403
    assert client.get("/api/invoice-drafts", headers=_headers(slug, store, "Store")).status_code == 403
    assert client.get("/api/invoices", headers=_headers(slug, store, "Store")).status_code == 403


def test_t1_another_company_has_no_apex_bill_and_no_apex_rule(bills: Session) -> None:  # noqa: F811
    slug = "t" + uuid.uuid4().hex[:8]
    assert tenants.create_tenant(slug, load_reference=False).ok
    try:
        with Session(routing.engine_for(slug)) as other:
            with pytest.raises(invoices.InvoiceError, match="supplier SUP-010 does not exist"):
                invoices.enter_invoice(
                    other,
                    invoices.InvoiceIn(
                        supplier_code="SUP-010", supplier_invoice_no="SNL/2627/0458",
                        invoice_date=date(2026, 11, 23), kind="non_po",
                        category_code="UTL", cost_centre_code="CC-ADM",
                        lines=[invoices.InvLine(None, None, Decimal("1"), Decimal("100"))],
                    ),
                    1,
                )
            assert other.execute(text("select count(*) from non_po_rule")).scalar_one() == 0
            assert other.execute(text("select count(*) from invoice")).scalar_one() == 0
    finally:
        routing.clear_engine_cache()
        tenants.drop_tenant(slug)
