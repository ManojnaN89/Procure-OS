"""Any list can be exported as a CSV file: the same rows the screen shows, as text, audited, and safe to open in a spreadsheet."""

import csv
import io

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_approvals import Logins, _headers, _request, _submit, db, logins  # noqa: F401 - fixtures

from p2p.main import app
from p2p.platform.export_api import LISTS, to_csv

client = TestClient(app)
SCREEN = {  # the export name -> the endpoint the screen reads
    "requests": "/api/requisitions",
    "quotations": "/api/rfqs",
    "orders": "/api/purchase-orders",
    "receipts": "/api/receipts",
    "invoices": "/api/invoices",
    "payment-runs": "/api/payment-runs",
    "vendors": "/api/vendors",
    "contracts": "/api/contracts",
}


def _read(body: bytes) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(body.decode("utf-8-sig"))))


def test_the_requests_export_has_the_screens_rows_with_exact_text_and_a_file_name_with_the_date(
    db: Session, logins: Logins
) -> None:  # noqa: F811
    slug = db.info["slug"]
    _request(db, "R-E1", "20000.00", requester="U4")
    _request(db, "R-E2", "600000.00", requester="U4")
    _submit(db, "R-E2")
    head = _headers(slug, logins.stores, "Approver")

    response = client.get("/api/export/requests", headers=head)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert response.headers["content-disposition"] == 'attachment; filename="requests-2026-11-06.csv"'
    assert response.content.startswith("﻿".encode())  # a spreadsheet then shows rupee signs and accents right
    rows = {r["id"]: r for r in _read(response.content)}
    assert set(rows) == {"R-E1", "R-E2"}
    assert rows["R-E1"]["amount"] == "20000.00" and rows["R-E1"]["status"] == "draft"
    assert rows["R-E2"]["status"] == "pending" and rows["R-E2"]["waiting_for"] == "Stores Prakash"
    assert rows["R-E2"]["needs_me"] == "yes" and rows["R-E1"]["needs_me"] == "no"


def test_every_export_has_the_same_number_of_rows_as_the_list_on_the_screen(db: Session, logins: Logins) -> None:  # noqa: F811
    slug = db.info["slug"]
    _request(db, "R-E3", "1000.00", requester="U4")
    head = _headers(slug, logins.stores, "Approver")

    for name, endpoint in SCREEN.items():
        shown = client.get(endpoint, headers=head)
        exported = client.get(f"/api/export/{name}", headers=head)
        assert shown.status_code == 200 and exported.status_code == 200, name
        assert len(_read(exported.content)) == len(shown.json()), name
    stock = client.get("/api/export/stock", headers=head)
    assert stock.status_code == 200 and len(_read(stock.content)) == len(
        client.get("/api/inventory", headers=head).json()["lines"]
    )
    assert set(LISTS) == set(SCREEN) | {"stock"}  # a list added to the screens is added here too


def test_an_export_leaves_an_audit_row_and_needs_a_login_and_a_known_list(db: Session, logins: Logins) -> None:  # noqa: F811
    head = _headers(db.info["slug"], logins.stores, "Approver")

    assert client.get("/api/export/vendors", headers=head).status_code == 200
    assert client.get("/api/export/vendors").status_code == 401
    unknown = client.get("/api/export/passwords", headers=head)

    assert unknown.status_code == 404 and "Choose one of" in unknown.json()["detail"]
    rows = db.execute(text("select entity_id, actor_id, after from audit_event where action = 'export.list'")).all()
    assert [(r[0], r[1]) for r in rows] == [("vendors", str(logins.stores))]
    assert rows[0][2] == {"rows": 0}


@pytest.mark.parametrize(
    ("value", "shown"),
    [
        ('=HYPERLINK("http://x.example")', '\'=HYPERLINK("http://x.example")'),
        ("+1+1", "'+1+1"),
        ("@SUM(A1)", "'@SUM(A1)"),
        ("-cmd", "'-cmd"),
        ("-5.00", "-5.00"),  # a negative number is a number
        ("12.50", "12.50"),
        ("Bearing 6205", "Bearing 6205"),
    ],
)
def test_a_cell_a_spreadsheet_would_run_as_a_formula_is_defanged_and_numbers_are_left_alone(
    value: str, shown: str
) -> None:
    rows = _read(("﻿" + to_csv([{"title": value}])).encode("utf-8"))

    assert rows == [{"title": shown}]


def test_nested_values_become_text_and_missing_ones_are_empty() -> None:
    out = to_csv([{"id": "A", "tags": ["x", "y"], "ok": True}, {"id": "B", "extra": None}])

    rows = _read(("﻿" + out).encode("utf-8"))

    assert rows == [
        {"id": "A", "tags": '["x", "y"]', "ok": "yes", "extra": ""},
        {"id": "B", "tags": "", "ok": "", "extra": ""},
    ]
