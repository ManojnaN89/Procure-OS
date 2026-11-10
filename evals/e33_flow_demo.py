"""E33: the small demo company (`flow`) completes the whole purchase flow with its eight logins, and nobody does two jobs of one case.

Not a replay of history: the company starts empty. Stock is low on four items; one click of "Check stock levels" starts a
run that goes through approval, a contract price and two quotation requests, four orders, goods receipt, four invoices
(each matched), one payment run approved by the plant head, the bank file, and "the bank has paid". Every figure that a
decision used is checked against what the dataset's own files say (item prices, reorder lots, approval bands, GST by state,
payment terms), not against the platform's formulas.
"""

import re
import tempfile
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

import env
import lib
from lib import Outcome

ID = "E33"
TITLE = "The small demo company completes the whole flow, from low stock to a paid invoice, with separate people for each job"
REQUIREMENT = (
    "Phase 1: request, approval, price or quotation, order with GST, receipt, invoice match, payment run, closing; "
    "requester, approver, receiver, invoice enterer and payer differ for the same case; "
    "Rs 10,000 approves itself, up to Rs 1,00,000 the maintenance manager, orders and payment runs the plant head"
)

TODAY = date(2026, 11, 6)
PAY_DAY = date(2026, 12, 21)  # the day the payment run is made: after every invoice of the run is due
CENT = Decimal("0.01")


def money(value: Decimal | int | str) -> Decimal:
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


def run(ctx: Any) -> Outcome:
    from fastapi.testclient import TestClient
    from p2p.formulas.calendar import pin_today
    from p2p.main import app
    from p2p.platform import demo, routing, worker
    from sqlalchemy import text
    from sqlalchemy.orm import Session

    out = Outcome(True)
    box = lib.capture_mail()
    client = TestClient(app)
    folder = Path(tempfile.mkdtemp(prefix="e33_"))
    dataset, credentials = folder / "dataset", folder / "credentials"
    slug = env.slug("e33")
    made = demo.reset_flow(dataset, credentials=credentials, slug=slug)
    out.metrics["people / master rows loaded"] = f"{len(made.logins)} / {made.report.rows_loaded if made.report else 0}"
    db = Session(routing.engine_for(slug))
    checks = 0

    def check(ok: bool, message: str, severity: str = "high") -> None:
        nonlocal checks
        checks += 1
        if not ok:
            out.find(severity, message)

    # ---- the eight logins, by their designation --------------------------------------------------
    people: dict[str, tuple[str, str]] = {}
    for line in credentials.read_text(encoding="utf-8").splitlines():
        found = re.match(r"^(\S+@\S+)\s+(\S+)\s+#\s*(.+) \((\w+)\)$", line)
        if found:
            people[found.group(3)] = (found.group(1), found.group(2))
    check(len(people) == 8, f"expected 8 logins, found {len(people)}")
    tokens: dict[str, dict[str, str]] = {}

    def who(designation: str) -> dict[str, str]:
        if designation not in tokens:
            email, password = people[designation]
            r = client.post("/api/auth/login", json={"email": email, "password": password})
            check(r.status_code == 200, f"{designation} cannot sign in")
            tokens[designation] = {"Authorization": "Bearer " + r.json()["access_token"]}
        return tokens[designation]

    PLANT_HEAD, MAINT_MGR, ENGINEER = "Plant Head", "Maintenance Manager", "Maintenance Engineer"
    STORE, BUYER, AP, FINANCE, ADMIN = (
        "Storekeeper",
        "Buyer",
        "Accounts Payable Executive",
        "Finance Manager",
        "System Administrator",
    )

    def call(
        designation: str, method: str, path: str, body: dict[str, Any] | None = None, *, expect: int | None = 200
    ) -> Any:
        db.rollback()
        r = client.request(method, path, json=body, headers=who(designation))
        if expect is not None and r.status_code != expect:
            check(False, f"{designation}: {method} {path} gave {r.status_code} (wanted {expect}): {r.text[:160]}")
        db.rollback()
        return r

    def refused(designation: str, method: str, path: str, body: dict[str, Any] | None = None) -> None:
        r = call(designation, method, path, body, expect=None)
        check(
            r.status_code in (403, 409, 422),
            f"{designation} was allowed to {method} {path} ({r.status_code}); it must be refused",
        )

    def work() -> None:
        db.commit()
        for _ in range(60):
            if not worker.run_once():
                break
        db.rollback()

    def q(sql: str, **params: Any) -> list[Any]:
        db.rollback()
        return list(db.execute(text(sql), params).all())

    # ---- what the dataset's own files say (the oracle) ---------------------------------------------
    items = {r["code"]: r for r in lib.rows("master", "item", dataset)}
    plan = {r["item_code"]: r for r in lib.rows("master", "item_location", dataset)}
    opening = {r["item_code"]: lib.D(r["qty"]) for r in lib.rows("master", "stock_opening", dataset)}
    suppliers = {r["code"]: r for r in lib.rows("master", "supplier", dataset)}
    terms = {r["code"]: int(r["days"]) for r in lib.rows("master", "payment_terms", dataset)}
    bands = [r for r in lib.rows("master", "approval_rule", dataset) if r["document"] == "request"]
    plant_gstin = lib.rows("master", "gst_registration", dataset)[0]["gstin"]

    def route_of(value: Decimal) -> str:
        for band in bands:
            if lib.D(band["min_value"]) <= value <= lib.D(band["max_value"]):
                return band["approver_chain"]
        return "?"

    expect_requests: dict[str, Decimal] = {}
    for code, p in plan.items():
        if opening[code] <= lib.D(p["reorder_point"]):
            qty = max(lib.D(p["order_qty"]), lib.D(items[code]["moq"]))
            cat = items[code]["category_code"]
            expect_requests[cat] = expect_requests.get(cat, Decimal(0)) + qty * lib.D(items[code]["standard_price"])
    out.metrics["items at or below reorder point"] = sum(
        1 for c, p in plan.items() if opening[c] <= lib.D(p["reorder_point"])
    )

    # ---- 1. check stock levels -------------------------------------------------------------------
    preview = call(BUYER, "POST", "/api/replenishment/run", {"dry_run": True}).json()
    check(
        len(preview["requests"]) == 3 and all(r["no"] is None for r in preview["requests"]),
        "the preview must show three requests and make none",
    )
    check(q("select count(*) from request")[0][0] == 0, "a preview made a request")
    created = call(BUYER, "POST", "/api/replenishment/run", {"dry_run": False}).json()["requests"]
    got = {r["category"]: r for r in created}
    check(
        sorted(got) == sorted(expect_requests),
        f"requests by category {sorted(got)} differ from the dataset's {sorted(expect_requests)}",
    )
    for cat, value in expect_requests.items():
        r = got.get(cat)
        if r is None:
            continue
        check(
            money(r["total_value"]) == money(value),
            f"{cat} request is {r['total_value']}, the dataset's reorder lots give {value}",
        )
        chain = route_of(value)
        want = "approved" if chain == "auto" else "submitted"
        check(
            r["outcome"] == want, f"{cat} request of {value} should be {want} (rule says '{chain}'), is {r['outcome']}"
        )
    request_of = {r["category"]: r["no"] for r in created}
    pending = q(
        "select a.approver, r.no from approval_step a join request r on r.no = a.request_no where a.status = 'pending' order by r.no"
    )
    check(
        [p[0] for p in pending] == ["U02", "U02"],
        f"two requests should wait for the maintenance manager (U02): {[tuple(p) for p in pending]}",
    )

    # ---- 2. approval: the maintenance manager; nobody else may -----------------------------------
    for no in (request_of["MRO"], request_of["ELE"]):
        refused(ENGINEER, "POST", f"/api/requisitions/{no}/approve", {})
        refused(BUYER, "POST", f"/api/requisitions/{no}/approve", {})
        refused(ADMIN, "POST", f"/api/requisitions/{no}/approve", {})
        refused(PLANT_HEAD, "POST", f"/api/requisitions/{no}/approve", {})  # not their step: the rule names the manager
    for no in (request_of["MRO"], request_of["ELE"]):
        call(MAINT_MGR, "POST", f"/api/requisitions/{no}/approve", {})
    check(
        all(r[0] in ("approved", "closed") for r in q("select status from request")),
        "every request should be approved by now",
    )
    work()  # the worker decides how each line is bought

    # ---- 3. sourcing: the contract line becomes an order; the rest are quotation requests ---------
    contract_pos = q("select no, supplier_code, status, taxable, total from po where contract_code is not null")
    check(
        len(contract_pos) == 1 and contract_pos[0][1] == "SUP-002",
        f"the contactor should be ordered at the contract's supplier: {[tuple(c) for c in contract_pos]}",
    )
    if contract_pos:
        contract_price = lib.D(lib.rows("master", "price_record", dataset)[0]["price"])
        qty = max(lib.D(plan["ITM-006"]["order_qty"]), lib.D(items["ITM-006"]["moq"]))
        check(
            lib.D(contract_pos[0][3]) == money(qty * contract_price),
            f"contract order taxable {contract_pos[0][3]} is not {qty} x {contract_price}",
        )
    rfqs = q("select no, request_no from rfq order by no")
    check(len(rfqs) == 2, f"two quotation requests expected (safety shoes; bearing and seal), found {len(rfqs)}")
    check(
        q("select count(*) from rfq where status = 'sent'")[0][0] == 0,
        "no quotation request may be sent before a buyer approves it ('rfq.send' is set to propose)",
    )
    check(
        len(box.sent) == 0 or all("RFQ" not in (m.get("subject") or "") for m in box.sent),
        "a quotation request was emailed before approval",
    )

    proposals = call(BUYER, "GET", "/api/admin/proposals?status=pending").json()
    sends = [p for p in proposals if p["action"] == "rfq.send"]
    check(len(sends) == 2, f"two 'send quotation request' proposals expected, found {len(sends)}")
    for p in sends:
        refused(ENGINEER, "POST", f"/api/admin/proposals/{p['id']}/approve", {})
        call(BUYER, "POST", f"/api/admin/proposals/{p['id']}/approve", {})
    work()
    sent_rfqs = {r[0]: r[1] for r in q("select no, status from rfq")}
    check(all(s == "sent" for s in sent_rfqs.values()), f"the quotation requests should be sent now: {sent_rfqs}")
    invited = {
        r[0]: sorted(x[0] for x in q("select supplier_code from rfq_supplier where rfq_no = :n", n=r[0])) for r in rfqs
    }
    shoes_rfq = next(r[0] for r in rfqs if r[1] == request_of["PPE"])
    mro_rfq = next(r[0] for r in rfqs if r[1] == request_of["MRO"])
    check(
        invited[shoes_rfq] == ["SUP-003", "SUP-004"],
        f"safety gear: asked {invited[shoes_rfq]}, expected the two general suppliers",
    )
    check(invited[mro_rfq] == ["SUP-001", "SUP-003", "SUP-004"], f"mechanical spares: asked {invited[mro_rfq]}")
    check(
        sum(1 for m in box.sent if "sales@deccansupplies.example" == m.get("to")) >= 2,
        "the general supplier should have two quotation emails",
    )

    # ---- 4. quotes entered by the buyer, compared, awarded -----------------------------------------
    def quote(rfq: str, supplier: str, prices: dict[int, str], gst: dict[int, int]) -> None:
        body = {
            "supplier_code": supplier,
            "valid_until": "2026-12-31",
            "freight": "0",
            "lines": [
                {
                    "rfq_line_no": n,
                    "unit_price": price,
                    "discount_pct": "0",
                    "gst_rate": str(gst[n]),
                    "delivery_days": 7,
                }
                for n, price in prices.items()
            ],
        }
        refused(ENGINEER, "POST", f"/api/rfqs/{rfq}/quotes", body)
        call(BUYER, "POST", f"/api/rfqs/{rfq}/quotes", body)

    quote(shoes_rfq, "SUP-003", {1: "1120"}, {1: 5})
    quote(shoes_rfq, "SUP-004", {1: "1090"}, {1: 5})
    quote(mro_rfq, "SUP-001", {1: "305", 2: "6450"}, {1: 18, 2: 18})
    quote(mro_rfq, "SUP-003", {1: "315", 2: "6350"}, {1: 18, 2: 18})
    quote(mro_rfq, "SUP-004", {1: "320", 2: "6400"}, {1: 18, 2: 18})
    page = call(BUYER, "GET", f"/api/rfqs/{mro_rfq}").json()
    rec = {ln["rfq_line_no"]: ln["recommended"] for ln in page["comparison"]["lines"]}
    out.metrics["recommended by the comparison (bearing / seal)"] = f"{rec.get(1)} / {rec.get(2)}"
    check(
        str(rec.get(1)) == "SUP-001" and str(rec.get(2)) == "SUP-003",
        f"the comparison should recommend the cheapest quote per line (SUP-001, SUP-003), says {rec}",
    )
    refused(ENGINEER, "POST", f"/api/rfqs/{mro_rfq}/award", {"choices": {"1": "SUP-001", "2": "SUP-003"}})
    call(BUYER, "POST", f"/api/rfqs/{mro_rfq}/award", {"choices": {"1": "SUP-001", "2": "SUP-003"}})
    call(BUYER, "POST", f"/api/rfqs/{shoes_rfq}/award", {"choices": {"1": "SUP-004"}})
    work()

    # ---- 5. orders: GST by state, approved by the plant head only -----------------------------------
    orders = {
        r[1]: r for r in q("select no, supplier_code, status, taxable, cgst, sgst, igst, total from po order by no")
    }
    check(
        sorted(orders) == ["SUP-001", "SUP-002", "SUP-003", "SUP-004"],
        f"one order per supplier expected, found {sorted(orders)}",
    )
    expected_lines = {
        "SUP-001": ("ITM-001", Decimal("305")),
        "SUP-003": ("ITM-004", Decimal("6350")),
        "SUP-004": ("ITM-011", Decimal("1090")),
    }
    for supplier, (item, price) in expected_lines.items():
        po = orders.get(supplier)
        if po is None:
            continue
        qty = max(lib.D(plan[item]["order_qty"]), lib.D(items[item]["moq"]))
        rate = Decimal(items[item]["gst_rate_after"])
        taxable = money(qty * price)
        interstate = suppliers[supplier]["state_code"] != plant_gstin[:2]
        half = money(taxable * rate / 200)
        want_total = taxable + (money(taxable * rate / 100) if interstate else 2 * half)
        check(lib.D(po[3]) == taxable, f"{po[0]}: taxable {po[3]}, expected {qty} x {price} = {taxable}")
        check(abs(lib.D(po[7]) - want_total) <= CENT * 2, f"{po[0]}: total {po[7]}, expected {want_total}")
        check(
            (lib.D(po[6]) > 0) == interstate,
            f"{po[0]}: {'IGST' if interstate else 'CGST and SGST'} expected for {suppliers[supplier]['state_code']} -> 27",
        )
    order_nos = [r[0] for r in orders.values()]
    check(
        all(r[2] == "submitted" for r in orders.values()),
        f"every order should wait for approval: {[r[2] for r in orders.values()]}",
    )
    for no in order_nos:
        refused(BUYER, "POST", f"/api/purchase-orders/{no}/approve", {})
        refused(ENGINEER, "POST", f"/api/purchase-orders/{no}/approve", {})
        refused(BUYER, "POST", f"/api/purchase-orders/{no}/send")  # not approved yet
        call(PLANT_HEAD, "POST", f"/api/purchase-orders/{no}/approve", {"comment": "ok"})
    check(all(r[0] == "approved" for r in q("select status from po")), "every order should be approved")
    for no in order_nos:
        refused(PLANT_HEAD, "POST", f"/api/purchase-orders/{no}/send")  # the approver does not send
        call(BUYER, "POST", f"/api/purchase-orders/{no}/send")
        call(BUYER, "POST", f"/api/purchase-orders/{no}/acknowledge", {"promised_date": "2026-11-12"})
    check(all(r[0] == "acknowledged" for r in q("select status from po")), "every order should be acknowledged")
    check(
        sum(1 for m in box.sent if "PO-" in (m.get("subject") or "")) >= 4,
        "each supplier should have been sent its order",
    )

    # ---- 6. goods receipt by the storekeeper -------------------------------------------------------
    for no in order_nos:
        lines = q("select line_no, qty from po_line where po_no = :n order by line_no", n=no)
        body = {
            "challan": f"DC-{no[-5:]}",
            "vehicle": "MH12AB1234",
            "lines": [{"line_no": ln[0], "qty_received": str(ln[1])} for ln in lines],
        }
        refused(BUYER, "POST", f"/api/purchase-orders/{no}/receive", body)
        refused(AP, "POST", f"/api/purchase-orders/{no}/receive", body)
        call(STORE, "POST", f"/api/purchase-orders/{no}/receive", body)
    check(q("select count(*) from goods_receipt")[0][0] == 4, "four goods receipts expected")
    check(
        all(r[0] == "received" for r in q("select status from po")),
        f"every order should be fully received: {[r[0] for r in q('select status from po')]}",
    )
    stock_after = {r[0]: lib.D(r[1]) for r in q("select item_code, on_hand from stock_level")}
    for code in ("ITM-001", "ITM-004", "ITM-006", "ITM-011"):
        qty = max(lib.D(plan[code]["order_qty"]), lib.D(items[code]["moq"]))
        check(
            stock_after.get(code) == opening[code] + qty,
            f"{code}: stock {stock_after.get(code)}, expected {opening[code]} + {qty}",
        )

    # ---- 7. invoices entered by AP and matched ------------------------------------------------------
    invoice_nos: dict[str, str] = {}
    for supplier, po in sorted(orders.items()):
        line = q("select po_line.line_no, po_line.qty, po_line.unit_price from po_line where po_no = :n", n=po[0])[0]
        body = {
            "supplier_code": supplier,
            "number": f"INV-{supplier[-3:]}-2627",
            "date": TODAY.isoformat(),
            "supplier_gstin": suppliers[supplier]["gstin"],
            "buyer_gstin": plant_gstin,
            "lines": [{"po_no": po[0], "po_line_no": line[0], "qty": str(line[1]), "unit_price": str(line[2])}],
        }
        if suppliers[supplier]["e_invoice_applicable"] == "true":
            body["irn"] = "A" * 64
        for designation in (STORE, BUYER, ENGINEER):
            refused(designation, "POST", "/api/invoices", body)
        r = call(AP, "POST", "/api/invoices", body)
        if r.status_code == 200:
            result = r.json()
            check(
                (result["status"], result["result"]) == ("approved", "matched"),
                f"{supplier}: the invoice should match and be approved, is {result['status']}/{result['result']}",
            )
            invoice_nos[supplier] = result["invoice"]
    for supplier, no in invoice_nos.items():
        inv = q("select taxable, total, due_date, entered_by, status from invoice where no = :n", n=no)[0]
        po = orders[supplier]
        check(lib.D(inv[0]) == lib.D(po[3]), f"{no}: invoice taxable {inv[0]} differs from the order's {po[3]}")
        check(
            abs(lib.D(inv[1]) - lib.D(po[7])) <= Decimal("0.50"),
            f"{no}: invoice total {inv[1]} differs from the order's {po[7]} by more than the rupee round-off",
        )
        days = terms[suppliers[supplier]["payment_terms_code"]]
        due = TODAY + timedelta(days=days)
        if suppliers[supplier]["msme_category"] in ("micro", "small") and suppliers[supplier]["agreed_payment_days"]:
            due = min(due, TODAY + timedelta(days=min(45, int(suppliers[supplier]["agreed_payment_days"]))))
        check(
            inv[2] == due,
            f"{no}: due date {inv[2]}, expected {due} from the supplier's terms ({suppliers[supplier]['payment_terms_code']})",
        )
    micro_due = q("select due_date from invoice where supplier_code = 'SUP-001'")
    out.metrics["micro supplier: due date (60-day terms, 45-day rule)"] = str(micro_due[0][0]) if micro_due else "-"

    # ---- 8. the payment run: planned by finance, approved by the plant head ---------------------------
    pin_today(PAY_DAY)
    refused(AP, "POST", "/api/payment-runs", {"run_date": PAY_DAY.isoformat(), "window_days": 7})
    plan_run = call(FINANCE, "POST", "/api/payment-runs", {"run_date": PAY_DAY.isoformat(), "window_days": 7}).json()
    run_no = plan_run.get("run") or plan_run.get("id") or plan_run.get("no")
    check(bool(run_no), f"no run number in {plan_run}")
    rows = q("select supplier_code, amount from payment where run_no = :r order by supplier_code", r=run_no)
    check(
        [r[0] for r in rows] == ["SUP-001", "SUP-002", "SUP-003", "SUP-004"],
        f"one payment per supplier expected: {[tuple(r) for r in rows]}",
    )
    refused(FINANCE, "POST", f"/api/payment-runs/{run_no}/file")  # not approved yet
    waiting = [
        p
        for p in call(PLANT_HEAD, "GET", "/api/admin/proposals?status=pending").json()
        if p["action"].startswith("payment_run")
    ]
    check(len(waiting) == 1, f"one payment run proposal should wait for the plant head, found {len(waiting)}")
    if waiting:
        proposal = waiting[0]["id"]
        refused(
            FINANCE, "POST", f"/api/admin/proposals/{proposal}/approve", {}
        )  # the proposer does not approve their own run
        refused(AP, "POST", f"/api/admin/proposals/{proposal}/approve", {})
        refused(ADMIN, "POST", f"/api/admin/proposals/{proposal}/approve", {})
        call(PLANT_HEAD, "POST", f"/api/admin/proposals/{proposal}/approve", {})
    refused(AP, "POST", f"/api/payment-runs/{run_no}/file")
    bank_file = call(FINANCE, "POST", f"/api/payment-runs/{run_no}/file").json()
    lines = [ln for ln in bank_file["file"].splitlines() if ln.strip()]
    check(len(lines) >= 5, f"the bank file should list four payments below its header: {len(lines)} lines")
    paid_total = sum((lib.D(r[1]) for r in rows), Decimal(0))
    check(
        paid_total
        == sum(
            (lib.D(q("select total from invoice where no = :n", n=n)[0][0]) for n in invoice_nos.values()), Decimal(0)
        ),
        "the run should pay exactly what the four invoices add up to (no tax at source below the threshold)",
    )
    refused(AP, "POST", f"/api/payment-runs/{run_no}/paid", {"paid_on": PAY_DAY.isoformat()})
    refused(
        FINANCE, "POST", f"/api/payment-runs/{run_no}/paid", {"paid_on": (PAY_DAY + timedelta(days=1)).isoformat()}
    )  # not a future day
    call(FINANCE, "POST", f"/api/payment-runs/{run_no}/paid", {"paid_on": PAY_DAY.isoformat()})
    work()

    # ---- 9. closed: invoices paid, orders closed, supplier told ----------------------------------------
    check(
        all(r[0] == "paid" for r in q("select status from invoice")),
        f"every invoice should be paid: {[r[0] for r in q('select status from invoice')]}",
    )
    check(
        all(r[0] == "closed" for r in q("select status from po")),
        f"every order should be closed: {[r[0] for r in q('select status from po')]}",
    )
    check(all(r[0] is not None for r in q("select utr from payment")), "every payment should carry a bank reference")
    check(
        sum(1 for m in box.sent if "payment" in (m.get("subject") or "").lower()) >= 4,
        "each supplier should be sent a payment advice",
    )

    # ---- the people: nobody did two jobs of this case ----------------------------------------------------
    def actors(*actions: str) -> set[str]:
        return {
            f"{r[0]}"
            for r in q(
                "select distinct actor_id from audit_event where action = any(:a) and actor_type = 'user'",
                a=list(actions),
            )
        }

    steps = {
        "approved the requests": actors("request.approve"),
        "approved the orders": actors("po.approve"),
        "sent the orders": actors("po.send"),
        "received the goods": actors("goods.receive", "grn.post", "receipt.create"),
        "entered the invoices": actors("invoice.enter", "invoice.book"),
        "planned the payment run": actors("payment_run.create", "payment_run.propose"),
    }
    steps["approved the payment run"] = {
        str(r[0]) for r in q("select approved_by from payment_run") if r[0] is not None
    }
    out.metrics["people per step (audit log)"] = {k: len(v) for k, v in steps.items()}
    named = {k: v for k, v in steps.items() if v}
    check(len(named) >= 5, f"the audit log should name who did each step; only {sorted(named)} were found")
    approvals_of_run = {r[0] for r in q("select approved_by from payment_run")} - {None}
    proposers_of_run = {r[0] for r in q("select proposed_by from payment_run")} - {None}
    check(not (approvals_of_run & proposers_of_run), "the person who planned the payment run also approved it")
    received_by = {r[0] for r in q("select received_by from goods_receipt")} | {
        r[0] for r in q("select posted_by from goods_receipt")
    }
    entered_by = {r[0] for r in q("select entered_by from invoice")}
    check(
        not ({str(x) for x in received_by} & {str(x) for x in entered_by}),
        "the person who received the goods also entered the invoice",
    )
    check(
        q("select count(*) from audit_event where action like 'payment_run%' or action like 'invoice%'")[0][0] > 0,
        "payment and invoice steps left no audit rows",
    )

    out.metrics["steps checked (one assertion each)"] = checks
    out.metrics["emails sent (quotation requests, orders, payment advice, ...)"] = len(box.sent)
    pin_today(TODAY)
    out.passed = out.passed and not any(f.severity in ("high", "medium") for f in out.findings)
    db.close()
    return out
