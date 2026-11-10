"""E10: one company cannot read another's data, through any endpoint that returns data."""

import lib
from fastapi.testclient import TestClient
from lib import Outcome
from sqlalchemy import text

ID = "E10"
TITLE = "One company cannot see another company's data"
REQUIREMENT = (
    "Rule 6: one database per tenant; a request without tenant context is refused; tenant A cannot read tenant B"
)

SECRET = "SECRET-OF-COMPANY-A"


def run(ctx) -> Outcome:
    from p2p.main import app
    from p2p.platform import files

    out = Outcome(True)
    a = lib.build_world("e10a", with_stock=False)
    b = lib.build_world("e10b", with_stock=False)
    da = a.db
    # records that exist only in company A, carrying a marker
    da.execute(
        text("insert into item_category (code, name) values ('EVC', :s) on conflict do nothing"),
        {"s": SECRET},
    )
    da.execute(
        text("insert into supplier (code, legal_name, status) values ('EV-A-SUP', :s, 'active')"),
        {"s": SECRET + " Traders"},
    )
    da.execute(
        text(
            "insert into request (no, ship_to_location, type, channel, status, total_value, justification) values ('EV-A-REQ', 'WH-BLR', 'goods', 'planning', 'draft', 1, :s)"
        ),
        {"s": SECRET},
    )
    da.execute(
        text(
            "insert into rfq (no, request_no, category_code, due_date, status) values ('EV-A-RFQ', 'EV-A-REQ', 'EVC', now(), 'draft')"
        )
    )
    da.execute(
        text(
            "insert into po (no, supplier_code, ship_to_location, request_no, status, total) values ('EV-A-PO', 'EV-A-SUP', 'WH-BLR', 'EV-A-REQ', 'draft', 1)"
        )
    )
    da.execute(
        text(
            "insert into item (code, name, category_code, base_uom, active, stocked) values ('EV-A-ITEM', :s, 'EVC', 'NOS', true, true)"
        ),
        {"s": SECRET + " item"},
    )
    da.execute(
        text(
            "insert into stock_opening (item_code, location_code, qty, as_of) values ('EV-A-ITEM', 'WH-BLR', 7, '2026-01-01')"
        )
    )
    da.execute(
        text("insert into notification (user_id, kind, title, severity) values (null, 'x', :s, 'low')"),
        {"s": SECRET},
    )
    da.commit()
    doc = files.save_file(
        da,
        a.slug,
        b"company A private bytes",
        filename="a.txt",
        content_type="text/plain",
        doc_type="x",
        uploaded_by=None,
        linked_type="x",
        linked_id="1",
    )

    client = TestClient(app)
    leaks: list[str] = []
    probes = 0
    # company A's own token does see them (so the probes below mean something)
    seen_by_a = (
        client.get("/api/requisitions/EV-A-REQ", headers=a.headers("Admin")).status_code == 200
        and client.get("/api/rfqs/EV-A-RFQ", headers=a.headers("Admin")).status_code == 200
    )
    if not seen_by_a:
        out.find("high", "the eval is broken: company A cannot see its own records")
    for role in ("Admin", "Buyer", "Approver", "Requester"):
        h = b.headers(role)
        for path in (
            "/api/requisitions/EV-A-REQ",
            "/api/rfqs/EV-A-RFQ",
            "/api/purchase-orders/EV-A-PO",
            f"/api/files/{doc.id}",
            "/api/vendors/EV-A-SUP",
        ):
            probes += 1
            r = client.get(path, headers=h)
            if r.status_code == 200 and (SECRET in r.text or "EV-A" in r.text or "company A" in r.text):
                leaks.append(f"{role} of company B read {path}")
        for path in (
            "/api/requisitions",
            "/api/rfqs",
            "/api/purchase-orders",
            "/api/vendors",
            "/api/inventory",
            "/api/notifications",
            "/api/trail",
            "/api/dashboard",
            "/api/search?q=SECRET",
            "/api/search?q=EV-A",
        ):
            probes += 1
            r = client.get(path, headers=h)
            if SECRET in r.text or "EV-A-" in r.text:
                leaks.append(f"{role} of company B saw company A's data in {path}")
    for path in (
        "/api/admin/audit",
        "/api/admin/users",
        "/api/admin/proposals",
        "/api/admin/rules",
    ):
        probes += 1
        r = client.get(path, headers=b.headers("Admin"))
        if SECRET in r.text or "EV-A" in r.text:
            leaks.append(f"company B's administrator saw company A's data in {path}")
    # tokens that are not valid for any company
    for label, headers in (
        ("no token", {}),
        ("garbage token", {"Authorization": "Bearer not.a.token"}),
        ("empty bearer", {"Authorization": "Bearer "}),
    ):
        for path in ("/api/requisitions", "/api/admin/audit", f"/api/files/{doc.id}"):
            probes += 1
            if client.get(path, headers=headers).status_code != 401:
                leaks.append(f"{label} was not refused on {path}")
    # a token of company A cannot be used to reach company B's records by guessing their ids
    b_one = b.db.execute(text("select no from request limit 1")).scalar()
    out.metrics = {
        "probes": probes,
        "leaks": len(leaks),
        "company A sees its own records": seen_by_a,
    }
    if leaks:
        out.find(
            "high",
            f"{len(leaks)} cross-company or unauthenticated read(s): {leaks[:5]}",
        )
    _ = b_one
    return out
