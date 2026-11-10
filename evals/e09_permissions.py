"""E09: who may do what, as a role-by-action matrix from the plan, against what the API actually allows."""

import lib
from fastapi.testclient import TestClient
from lib import Outcome

ID = "E09"
TITLE = "Every write action is open to exactly the roles the plan names"
REQUIREMENT = "Rule 7 and the plan: requester, buyer, approver and administrator duties are separate; nobody else can do them; nobody can without logging in"

ROLES = ["Admin", "Requester", "Buyer", "Approver", "Store", "AP", "Finance"]

# (method, path, body, the roles the plan lets do it). Identifiers do not exist: the permission check comes first, so a
# role that is allowed gets 404, 409 or 422 and a role that is not gets 403.
MATRIX = [
    ("POST", "/api/replenishment/run", {}, {"Buyer"}),
    ("POST", "/api/requisitions", {"lines": [{"item_code": "A", "qty": "1"}], "need_by": "2026-11-20"}, {"Requester"}),
    ("POST", "/api/requisitions/NOPE/copy", {}, {"Requester"}),
    ("POST", "/api/inventory/issue", {"item_code": "A", "location": "WH", "qty": "1"}, {"Store"}),
    (
        "POST",
        "/api/inventory/count",
        {"item_code": "A", "location": "WH", "counted_qty": "1", "reason": "x"},
        {"Store"},
    ),
    ("POST", "/api/requisitions/NOPE/submit", None, {"Requester", "Buyer"}),
    ("POST", "/api/requisitions/NOPE/approve", {}, {"Approver"}),
    ("POST", "/api/requisitions/NOPE/reject", {"reason": "x"}, {"Approver"}),
    ("POST", "/api/requisitions/NOPE/return", {"reason": "x"}, {"Approver"}),
    ("POST", "/api/requisitions/NOPE/source", None, {"Buyer"}),
    ("POST", "/api/rfqs/NOPE/quotes", {"supplier_code": "S", "lines": []}, {"Buyer"}),
    ("POST", "/api/rfqs/NOPE/award", {"choices": {}}, {"Buyer"}),
    ("POST", "/api/quote-drafts/1/confirm", {}, {"Buyer"}),
    ("POST", "/api/quote-drafts/1/dismiss", {}, {"Buyer"}),
    ("POST", "/api/purchase-orders/NOPE/send", None, {"Buyer"}),
    ("POST", "/api/purchase-orders/NOPE/acknowledge", {}, {"Buyer"}),
    ("POST", "/api/purchase-orders/NOPE/cancel", {"reason": "x"}, {"Buyer"}),
    ("POST", "/api/purchase-orders/NOPE/close-balance", {"reason": "x"}, {"Buyer"}),
    ("POST", "/api/purchase-orders/NOPE/revise", {"reason": "x", "lines": [{"line_no": 1, "qty": "1"}]}, {"Buyer"}),
    ("POST", "/api/purchase-orders/NOPE/receive", {"lines": [{"line_no": 1, "qty_received": "1"}]}, {"Store"}),
    (
        "POST",
        "/api/receipts/NOPE/inspect",
        {"lines": [{"line_no": 1, "qty_accepted": "1", "qty_rejected": "0"}]},
        {"Store"},
    ),
    ("POST", "/api/invoices", {"supplier_code": "S", "number": "N", "date": "2026-11-06", "lines": []}, {"AP"}),
    ("POST", "/api/invoices/NOPE/rematch", None, {"AP"}),
    ("POST", "/api/invoices/NOPE/release", {"reason": "x"}, {"AP"}),
    ("POST", "/api/invoices/NOPE/cancel", {"reason": "x"}, {"AP"}),
    ("POST", "/api/payment-runs", {"run_date": "2026-11-06"}, {"Finance"}),
    ("POST", "/api/payment-runs/NOPE/file", None, {"Finance"}),
    ("POST", "/api/payment-runs/NOPE/paid", {}, {"Finance"}),
    ("POST", "/api/vendors", {"legal_name": "x"}, {"Buyer", "Approver"}),
    ("PUT", "/api/vendors/NOPE", {"trade_name": "x"}, {"Buyer", "Approver"}),
    ("POST", "/api/vendors/NOPE/bank", {"account_no": "1", "ifsc": "x"}, {"Buyer", "Approver"}),
    ("POST", "/api/vendors/NOPE/documents", {"type": "pan"}, {"Buyer", "Approver"}),
    ("POST", "/api/vendors/NOPE/documents/1/verify", {}, {"Approver"}),
    ("POST", "/api/contracts", {"supplier_code": "S", "valid_from": "2026-11-01"}, {"Buyer", "Approver"}),
    ("POST", "/api/contracts/NOPE/prices", {"item_code": "A", "price": "1"}, {"Buyer", "Approver"}),
    ("POST", "/api/contracts/NOPE/activate", {}, {"Approver"}),
    ("POST", "/api/alerts/run", None, {"Admin", "Buyer"}),
    ("POST", "/api/supplier-agent/chase", {}, {"Buyer"}),
    ("POST", "/api/invoice-drafts/1/confirm", {}, {"AP"}),
    ("POST", "/api/invoice-drafts/1/dismiss", {}, {"AP"}),
    ("POST", "/api/purchase-orders/NOPE/approve", {}, {"Approver"}),
    ("POST", "/api/purchase-orders/NOPE/reject", {"reason": "x"}, {"Approver"}),
    ("POST", "/api/vendors/NOPE/status", {"to": "active", "reason": "x"}, {"Buyer", "Approver"}),
    ("POST", "/api/settings", {"stale_requisition_days": "5"}, {"Admin"}),
    ("GET", "/api/admin/audit", None, {"Admin"}),
    ("GET", "/api/admin/agent-runs", None, {"Admin"}),
    ("GET", "/api/admin/users", None, {"Admin"}),
    ("GET", "/api/admin/rules", None, {"Admin"}),
    ("POST", "/api/admin/rules/approval/publish", {}, {"Admin"}),
    (
        "POST",
        "/api/admin/users",
        {
            "email": "x@y.example",
            "password": "p",
            "display_name": "x",
            "role": "Requester",
        },
        {"Admin"},
    ),
]
READS = [
    "/api/requisitions",
    "/api/rfqs",
    "/api/purchase-orders",
    "/api/receipts",
    "/api/workspace",
    "/api/control-tower",
    "/api/kpis",
    "/api/alerts",
    "/api/invoice-drafts",
    "/api/invoices",
    "/api/payment-runs",
    "/api/receiving/waiting",
    "/api/inventory",
    "/api/export/requests",
    "/api/vendors",
    "/api/payment-terms",
    "/api/dashboard",
    "/api/settings",
    "/api/notifications",
    "/api/trail",
]


def run(ctx) -> Outcome:
    from p2p.main import app

    out = Outcome(True)
    world = lib.build_world("e09", with_stock=False)
    client = TestClient(app)
    over: list[str] = []
    under: list[str] = []
    cells = 0
    for method, path, body, allowed in MATRIX:
        for role in [*ROLES, None]:
            headers = world.headers(role) if role else {}
            if method == "GET":
                resp = client.get(path, headers=headers)
            else:
                resp = client.request(method, path, json=body, headers=headers)
            passed_gate = resp.status_code not in (401, 403)
            should = role in allowed
            cells += 1
            if passed_gate and not should:
                over.append(f"{role or 'nobody signed in'} can {method} {path} (status {resp.status_code})")
            elif should and not passed_gate:
                under.append(f"{role} cannot {method} {path} (status {resp.status_code})")
    anon_reads = [p for p in READS if client.get(p).status_code != 401]
    signed_in_reads = [
        (r, p) for r in ROLES for p in READS if client.get(p, headers=world.headers(r)).status_code != 200
    ]
    out.metrics = {
        "actions": len(MATRIX),
        "roles tried": len(ROLES) + 1,
        "cells checked": cells,
        "too permissive": len(over),
        "too strict": len(under),
        "reads open without login": len(anon_reads),
    }
    if over:
        out.find(
            "high",
            f"{len(over)} role(s) can do something the plan does not give them, e.g. {over[0]}; all: {over[:8]}",
        )
    if under:
        out.find(
            "medium",
            f"{len(under)} role(s) cannot do something the plan gives them, e.g. {under[0]}",
        )
    if anon_reads:
        out.find("high", f"these read endpoints answer without a login: {anon_reads}")
    if signed_in_reads:
        out.find(
            "low",
            f"some read endpoints fail for a signed-in role: {signed_in_reads[:4]}",
        )
    return out
