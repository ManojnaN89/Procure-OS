"""The Dashboard's company-wide figures are counted from the real tables: an order on its way adds to the ordered value and
the category it was for, and an empty company reads zero. Nothing here is invented."""

from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_orders import TODAY, _headers, _logins, _ordered, shop  # noqa: F401 - fixtures
from test_sourcing import Outbox, db, outbox  # noqa: F401 - fixtures

from p2p.main import app

client = TestClient(app)


def test_an_order_on_its_way_shows_in_the_figures_and_a_draft_does_not(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    slug = shop.info["slug"]
    buyer = _headers(slug, _logins(shop)["buyer"], "Buyer")
    before = client.get("/api/dashboard", headers=buyer).json()
    assert (before["purchase_orders"], before["total_po_value"], before["spend_by_category"]) == (0, "0", {})
    _ordered(shop, outbox)  # a request, its quotations, the award and the order made from it
    shop.execute(text("update po set status = 'draft'"))
    shop.commit()
    assert client.get("/api/dashboard", headers=buyer).json()["purchase_orders"] == 0  # an order still being prepared

    shop.execute(text("update po set status = 'sent'"))
    shop.commit()
    after = client.get("/api/dashboard", headers=buyer).json()

    made, total, taxable = shop.execute(text("select count(*), sum(total), sum(taxable) from po")).one()
    assert after["purchase_orders"] == made >= 1
    assert Decimal(after["total_po_value"]) == total
    items = shop.execute(text("select sum(round(qty * unit_price, 2)) from po_line")).scalar_one()
    assert sum(Decimal(v) for v in after["spend_by_category"].values()) == items  # the goods, before tax and freight
    assert items <= taxable
    assert all(isinstance(v, str) for v in after["spend_by_category"].values())  # money travels as text, never a float
    assert after["vendors"] >= 1 and after["blocked_invoices"] == 0 and Decimal(after["paid_total"]) == 0
    vendors = {v["id"]: v for v in client.get("/api/vendors", headers=buyer).json()}
    for supplier, ordered in shop.execute(text("select supplier_code, sum(total) from po group by 1")):
        assert Decimal(vendors[supplier]["total_spend"]) == ordered  # what has been ordered from each supplier
    assert client.get("/api/briefing", headers=buyer).json()["total_spend"] == str(total)


def test_the_dashboard_figures_need_a_login(shop: Session) -> None:  # noqa: F811
    assert client.get("/api/dashboard").status_code == 401
