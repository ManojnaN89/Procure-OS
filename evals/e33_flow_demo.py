"""E33: the main demo company (Apex Pumps, `apex`) completes the whole purchase flow with its role logins, and nobody
does two jobs of one case.

Not a replay of history: the company starts with its master data and stock, and nothing open. One click of "Check
stock levels" makes a request for the V-belts; the production manager asks for impellers, seals and copper wire. The run
goes through approval by value (the request above Rs 1,00,000 to the director), a contract price and two quotation
requests (one with a single quote, awarded with a reason), four orders with GST (one interstate, one above the purchase
manager's threshold, the contract one issued and sent touchless), receipt with an inspection, four matched invoices, one payment run approved by the director, the
bank file and "the bank has paid". Every figure that a decision used is checked against what the dataset's own files say
(stock, reorder levels, item prices, approval bands, the suppliers asked, GST by state, payment terms), not against the
platform's formulas.
"""

import re
import tempfile
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

import env
import lib
import yaml
from lib import Outcome

ID = "E33"
# Block 13 (spec 10.1): an order is received after supply and closes only on settlement, with closed_at (D-12).
TITLE = "The main demo company completes the whole flow, from low stock to a paid invoice, with separate people for each job"
REQUIREMENT = (
    "Phase 1: request, approval, price or quotation, order with GST, receipt, invoice match, payment run, closing; "
    "requester, approver, receiver, invoice enterer and payer differ for the same case; "
    "Rs 10,000 approves itself, up to Rs 1,00,000 the plant head, above that the director; orders above Rs 1,00,000 the "
    "purchase manager; payment runs the director"
)

TODAY = date(2026, 11, 6)
PAY_DAY = date(2026, 12, 21)  # the day the payment run is made: after every invoice of the run is due
CENT = Decimal("0.01")
ASKED = 3  # suppliers asked for a quotation (the platform's default `rfq_supplier_count`)
# what the production manager asks for: impellers (on the castings contract), seals (inspected on arrival) and copper
# winding wire (enough to go above the plant head's limit)
WANTED = {"RM-03": Decimal(100), "RM-07": Decimal(100), "RM-08": Decimal(120)}
QUOTES = {  # the buyer types these in: supplier -> item -> price
    "SUP-002": {"RM-07": "94"},
    "SUP-003": {"RM-07": "97", "RM-08": "915"},
    "SUP-005": {"RM-07": "99", "RM-08": "905"},
}
BELT_QUOTE = ("SUP-006", "205")


def money(value: Decimal | int | str) -> Decimal:
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


def run(ctx: Any) -> Outcome:
    env.forget_tenants("e33")  # left by an earlier run that stopped half way
    try:
        return _run(ctx)
    finally:
        env.forget_tenants("e33")


def _run(ctx: Any) -> Outcome:
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
    made = demo.reset_company(demo.company("apex"), dataset, credentials=credentials, slug=slug)
    out.metrics["people / master rows loaded"] = f"{len(made.logins)} / {made.report.rows_loaded if made.report else 0}"
    db = Session(routing.engine_for(slug))
    checks = 0

    def check(ok: bool, message: str, severity: str = "high") -> None:
        nonlocal checks
        checks += 1
        if not ok:
            out.find(severity, message)

    # ---- the logins, by their designation --------------------------------------------------------
    people: dict[str, tuple[str, str]] = {}
    for line in credentials.read_text(encoding="utf-8").splitlines():
        found = re.match(r"^(\S+@\S+)\s+(\S+)\s+#\s*(.+) \((\w+)\)$", line)
        if found:
            people[found.group(3)] = (found.group(1), found.group(2))
    check(len(people) == 10, f"expected 10 logins (nine roles and a second administrator), found {len(people)}")
    tokens: dict[str, dict[str, str]] = {}

    def who(designation: str) -> dict[str, str]:
        if designation not in tokens:
            email, password = people[designation]
            r = client.post("/api/auth/login", json={"email": email, "password": password})
            check(r.status_code == 200, f"{designation} cannot sign in")
            tokens[designation] = {"Authorization": "Bearer " + r.json()["access_token"]}
        return tokens[designation]

    REQUESTER, STORE, BUYER, AP, FINANCE, ADMIN = (
        "Production Manager",
        "Storekeeper",
        "Buyer",
        "AP Executive",
        "Finance Manager",
        "System Administrator",
    )
    PLANT_HEAD, PURCHASE_MANAGER, DIRECTOR = "Plant Head", "Purchase Manager", "Director"

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
    suppliers = {r["code"]: r for r in lib.rows("master", "supplier", dataset)}
    terms = {r["code"]: int(r["days"]) for r in lib.rows("master", "payment_terms", dataset)}
    rules = lib.rows("master", "approval_rule", dataset)
    plant_gstin = lib.rows("master", "gst_registration", dataset)[0]["gstin"]
    profile = yaml.safe_load((lib.ROOT / "datagen" / "catalog" / "apex" / "company.yaml").read_text(encoding="utf-8"))
    touchless_on = (profile.get("autonomy") or {}).get("po.issue") == "auto"
    prices = lib.rows("master", "price_record", dataset)
    approved_for = lib.rows("master", "supplier_category", dataset)
    on_hand = {r["item_code"]: lib.D(r["qty"]) for r in lib.rows("master", "stock_opening", dataset)}
    for m in lib.rows("history", "stock_movement", dataset):  # the stock history, replayed
        sign = 1 if m["type"] in ("receipt", "transfer_in", "adjust") else -1
        on_hand[m["item_code"]] = on_hand.get(m["item_code"], Decimal(0)) + sign * lib.D(m["qty"])

    def chain(document: str, value: Decimal) -> str:
        for r in rules:
            if r["document"] == document and lib.D(r["min_value"]) <= value <= lib.D(r["max_value"]):
                return r["approver_chain"]
        return "?"

    def rate(item: str) -> Decimal:
        it = items[item]
        changed = it["gst_rate_change_date"] and date.fromisoformat(it["gst_rate_change_date"]) <= TODAY
        return Decimal(it["gst_rate_after"] if changed else it["gst_rate_before"])

    def asked_for(category: str, codes: list[str]) -> list[str]:
        """Suppliers approved for the category, those with prices for the items first, then by code."""
        approved = sorted(s["supplier_code"] for s in approved_for if s["category_code"] == category)
        with_prices = {
            s: sum(1 for p in prices if p["supplier_code"] == s and p["item_code"] in codes) for s in approved
        }
        return sorted(approved, key=lambda s: (-with_prices[s], s))[:ASKED]

    low = {
        c: p for c, p in plan.items() if p["reorder_point"] and on_hand.get(c, Decimal(0)) <= lib.D(p["reorder_point"])
    }
    restock: dict[str, Decimal] = {}
    for code, p in low.items():
        need = lib.D(p["max_qty"]) - on_hand[code] if p["reorder_policy"] == "minmax" else lib.D(p["order_qty"])
        restock[code] = max(need, lib.D(items[code]["moq"]))
    out.metrics["items at or below reorder point"] = sorted(low)

    # ---- 1. check stock levels -------------------------------------------------------------------
    preview = call(BUYER, "POST", "/api/replenishment/run", {"dry_run": True}).json()
    check(
        len(preview["requests"]) == len({items[c]["category_code"] for c in restock})
        and all(r["no"] is None for r in preview["requests"]),
        "the preview must show one request per category of the low items and make none",
    )
    check(q("select count(*) from request")[0][0] == 0, "a preview made a request")
    created = call(BUYER, "POST", "/api/replenishment/run", {"dry_run": False}).json()["requests"]
    check(len(created) == 1, f"one request for the low spares expected, made {len(created)}")
    belts_value = sum((qty * lib.D(items[c]["standard_price"]) for c, qty in restock.items()), Decimal(0))
    belts_request = created[0]["no"] if created else ""
    if created:
        check(
            money(created[0]["total_value"]) == money(belts_value),
            f"the stock request is {created[0]['total_value']}, the dataset's reorder levels give {belts_value}",
        )
        check(
            created[0]["outcome"] == ("approved" if chain("request", belts_value) == "auto" else "submitted"),
            f"a stock request of {belts_value} should follow the rule '{chain('request', belts_value)}'",
        )

    # ---- 2. the production manager's request, approved by the person the band names -------------
    value = sum((qty * lib.D(items[c]["standard_price"]) for c, qty in WANTED.items()), Decimal(0))
    raised = call(
        REQUESTER,
        "POST",
        "/api/requisitions",
        {
            "lines": [{"item_code": c, "qty": str(qty)} for c, qty in WANTED.items()],
            "need_by": (TODAY + timedelta(days=21)).isoformat(),
            "location": plan["RM-03"]["location_code"],
            "reason": "Materials for the November pumps",
        },
    ).json()
    request_no = str(raised.get("id"))
    call(REQUESTER, "POST", f"/api/requisitions/{request_no}/submit", {})
    stored = q("select total_value, status from request where no = :n", n=request_no)
    check(
        bool(stored) and money(stored[0][0]) == money(value),
        f"the request is worth {stored[0][0] if stored else '-'}, its lines at the item prices give {value}",
    )
    band = chain("request", value)
    pending = q("select approver from approval_step where request_no = :n and status = 'pending'", n=request_no)
    director_code = next(u["code"] for u in lib.rows("master", "user", dataset) if u["designation"] == DIRECTOR)
    check(
        band == "director" and [p[0] for p in pending] == [director_code],
        f"a request of {value} (rule '{band}') should wait for the director only: {[p[0] for p in pending]}",
    )
    for designation in (REQUESTER, BUYER, ADMIN, PLANT_HEAD, PURCHASE_MANAGER):  # not their step
        refused(designation, "POST", f"/api/requisitions/{request_no}/approve", {})
    call(DIRECTOR, "POST", f"/api/requisitions/{request_no}/approve", {})
    check(
        all(r[0] in ("approved", "closed") for r in q("select status from request")),
        "every request should be approved by now",
    )
    work()  # the worker decides how each line is bought

    # ---- 3. sourcing: the contract line becomes an order; the rest are quotation requests ---------
    contract = next(p for p in prices if p["item_code"] == "RM-03" and p["contract_code"])
    contract_pos = q("select no, supplier_code, taxable from po where contract_code is not null")
    check(
        [(c[1], money(c[2])) for c in contract_pos]
        == [(contract["supplier_code"], money(100 * lib.D(contract["price"])))],
        f"the impellers should be ordered from the contract supplier at its price: {[tuple(c) for c in contract_pos]}",
    )
    rfqs = {r[1]: r[0] for r in q("select no, request_no from rfq order by no")}
    check(sorted(rfqs) == sorted([belts_request, request_no]), f"one quotation request per request expected: {rfqs}")
    check(q("select count(*) from rfq where status = 'sent'")[0][0] == 0, "an RFQ was sent before a buyer approved it")
    sends = [p for p in call(BUYER, "GET", "/api/admin/proposals?status=pending").json() if p["action"] == "rfq.send"]
    check(len(sends) == 2, f"two 'send quotation request' proposals expected, found {len(sends)}")
    for p in sends:
        refused(REQUESTER, "POST", f"/api/admin/proposals/{p['id']}/approve", {})
        call(BUYER, "POST", f"/api/admin/proposals/{p['id']}/approve", {})
    work()
    sent = {r[0]: r[1] for r in q("select no, status from rfq")}
    check(all(s == "sent" for s in sent.values()), f"the quotation requests should be sent now: {sent}")
    raw_rfq, belts_rfq = rfqs.get(request_no, ""), rfqs.get(belts_request, "")
    invited = {
        n: [x[0] for x in q("select supplier_code from rfq_supplier where rfq_no = :n order by 1", n=n)]
        for n in (raw_rfq, belts_rfq)
    }
    want_raw = sorted(asked_for("RAW", ["RM-07", "RM-08"]))
    check(invited[raw_rfq] == want_raw, f"raw materials: asked {invited[raw_rfq]}, the dataset gives {want_raw}")
    want_mro = sorted(asked_for("MRO", list(restock)))
    check(invited[belts_rfq] == want_mro, f"spares: asked {invited[belts_rfq]}, the dataset gives {want_mro}")
    contacts = {c["supplier_code"]: c["email"] for c in lib.rows("master", "supplier_contact", dataset)}
    check(
        all(any(m.get("to") == contacts[s] for m in box.sent) for s in want_raw + want_mro),
        "every supplier asked should have its quotation email",
    )

    # ---- 4. quotes entered by the buyer, compared, awarded -----------------------------------------
    lines_of = {
        n: {r[1]: r[0] for r in q("select line_no, item_code from rfq_line where rfq_no = :n", n=n)}
        for n in (raw_rfq, belts_rfq)
    }

    def quote(rfq: str, supplier: str, offer: dict[str, str]) -> None:
        body = {
            "supplier_code": supplier,
            "valid_until": "2026-12-31",
            "freight": "0",
            "lines": [
                {
                    "rfq_line_no": lines_of[rfq][item],
                    "unit_price": price,
                    "discount_pct": "0",
                    "gst_rate": str(rate(item)),
                    "delivery_days": 7,
                }
                for item, price in offer.items()
                if item in lines_of[rfq]
            ],
        }
        refused(REQUESTER, "POST", f"/api/rfqs/{rfq}/quotes", body)
        call(BUYER, "POST", f"/api/rfqs/{rfq}/quotes", body)

    for supplier, offer in QUOTES.items():
        quote(raw_rfq, supplier, offer)
    quote(belts_rfq, BELT_QUOTE[0], {"SP-01": BELT_QUOTE[1]})
    cheapest = {
        item: min((s for s in QUOTES if item in QUOTES[s]), key=lambda s: Decimal(QUOTES[s][item]))
        for item in ("RM-07", "RM-08")
    }
    page = call(BUYER, "GET", f"/api/rfqs/{raw_rfq}").json()
    item_of = {n: i for i, n in lines_of[raw_rfq].items()}
    rec = {item_of.get(ln["rfq_line_no"]): ln["recommended"] for ln in page["comparison"]["lines"]}
    out.metrics["recommended by the comparison (seal / copper wire)"] = f"{rec.get('RM-07')} / {rec.get('RM-08')}"
    check(rec == cheapest, f"the comparison should recommend the cheapest quote per line {cheapest}, says {rec}")
    choices = {str(lines_of[raw_rfq][i]): s for i, s in cheapest.items()}
    refused(REQUESTER, "POST", f"/api/rfqs/{raw_rfq}/award", {"choices": choices})
    call(BUYER, "POST", f"/api/rfqs/{raw_rfq}/award", {"choices": choices})
    belts_choice = {str(lines_of[belts_rfq]["SP-01"]): BELT_QUOTE[0]}
    # one quote where two are wanted: an award needs the buyer's reason
    refused(BUYER, "POST", f"/api/rfqs/{belts_rfq}/award", {"choices": belts_choice})
    call(
        BUYER,
        "POST",
        f"/api/rfqs/{belts_rfq}/award",
        {"choices": belts_choice, "reason": "The only approved supplier of spares; the belts are needed"},
    )
    work()

    # ---- 5. orders: GST by state, approved by whom the band names ------------------------------------
    orders = {
        r[1]: r for r in q("select no, supplier_code, status, taxable, cgst, sgst, igst, total from po order by no")
    }
    expected_lines = {
        contract["supplier_code"]: ("RM-03", WANTED["RM-03"], lib.D(contract["price"])),
        cheapest["RM-07"]: ("RM-07", WANTED["RM-07"], Decimal(QUOTES[cheapest["RM-07"]]["RM-07"])),
        cheapest["RM-08"]: ("RM-08", WANTED["RM-08"], Decimal(QUOTES[cheapest["RM-08"]]["RM-08"])),
        BELT_QUOTE[0]: ("SP-01", restock.get("SP-01", Decimal(0)), Decimal(BELT_QUOTE[1])),
    }
    check(sorted(orders) == sorted(expected_lines), f"one order per supplier expected, found {sorted(orders)}")
    for supplier, (item, qty, price) in expected_lines.items():
        po = orders.get(supplier)
        if po is None:
            continue
        taxable = money(qty * price)
        interstate = suppliers[supplier]["state_code"] != plant_gstin[:2]
        half = money(taxable * rate(item) / 200)
        want_total = taxable + (money(taxable * rate(item) / 100) if interstate else 2 * half)
        check(lib.D(po[3]) == taxable, f"{po[0]}: taxable {po[3]}, expected {qty} x {price} = {taxable}")
        check(abs(lib.D(po[7]) - want_total) <= CENT * 2, f"{po[0]}: total {po[7]}, expected {want_total}")
        check(
            (lib.D(po[6]) > 0) == interstate,
            f"{po[0]}: {'IGST' if interstate else 'CGST and SGST'} expected for {suppliers[supplier]['state_code']} -> 27",
        )
        band = chain("po", lib.D(po[7]))
        # touchless (D35): the company's profile turns po.issue on; a contract line in the buyer's band goes out alone
        touchless = band == "auto" and supplier == contract["supplier_code"] and touchless_on
        want = "sent" if touchless else "submitted"  # never approved by the buyer who made it (v1.1)
        check(po[2] == want, f"{po[0]} of {po[7]} should be {want} (rule '{band}', touchless {touchless})")
    sent_alone = [r[0] for r in orders.values() if r[2] == "sent"]
    out.metrics["orders issued touchless"] = len(sent_alone)
    for no in sent_alone:
        flag = q("select touchless, approval_tier from po where no = :n", n=no)[0]
        check(tuple(flag) == (True, "buyer"), f"{no} was sent without a buyer but is not marked touchless: {flag}")
        refused(BUYER, "POST", f"/api/purchase-orders/{no}/send")  # already out
    waiting_pos = [r[0] for r in orders.values() if r[2] == "submitted"]
    out.metrics["orders waiting for the purchase manager"] = len(waiting_pos)
    check(
        len(waiting_pos) == len(orders) - len(sent_alone),
        f"every order that is not touchless waits for the purchase manager: {waiting_pos}",
    )
    for no in waiting_pos:
        refused(BUYER, "POST", f"/api/purchase-orders/{no}/approve", {})
        refused(REQUESTER, "POST", f"/api/purchase-orders/{no}/approve", {})
        refused(BUYER, "POST", f"/api/purchase-orders/{no}/send")  # not approved yet
        call(PURCHASE_MANAGER, "POST", f"/api/purchase-orders/{no}/approve", {"comment": "ok"})
    check(
        all(r[0] == "approved" or r[1] in sent_alone for r in q("select status, no from po")),
        "every order should be approved",
    )
    order_nos = [r[0] for r in orders.values()]
    for no in order_nos:
        if no in sent_alone:
            call(BUYER, "POST", f"/api/purchase-orders/{no}/acknowledge", {"note": "Confirmed by phone"})
            continue
        refused(PURCHASE_MANAGER, "POST", f"/api/purchase-orders/{no}/send")  # the approver does not send
        call(BUYER, "POST", f"/api/purchase-orders/{no}/send")
        call(BUYER, "POST", f"/api/purchase-orders/{no}/acknowledge", {"note": "Confirmed by phone"})
    check(all(r[0] == "acknowledged" for r in q("select status from po")), "every order should be acknowledged")
    check(
        sum(1 for m in box.sent if "PO-" in (m.get("subject") or "")) >= 4,
        "each supplier should have been sent its order",
    )

    # ---- 6. goods receipt by the storekeeper; the seals wait for inspection --------------------------
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
    inspected = [c for c, it in items.items() if it["inspection_required"] == "true" and c in WANTED]
    waiting_gr = q("select no, po_no from goods_receipt where inspection_status = 'pending'")
    check(
        len(waiting_gr) == len(inspected),
        f"the receipts of items that need inspection should wait ({inspected}): {[tuple(g) for g in waiting_gr]}",
    )
    for gr, po_no in waiting_gr:
        lines = q("select line_no, qty from po_line where po_no = :n order by line_no", n=po_no)
        decisions = [{"line_no": ln[0], "qty_accepted": str(ln[1]), "qty_rejected": "0"} for ln in lines]
        refused(AP, "POST", f"/api/receipts/{gr}/inspect", {"lines": decisions})
        call(STORE, "POST", f"/api/receipts/{gr}/inspect", {"lines": decisions})
    check(q("select count(*) from goods_receipt")[0][0] == 4, "four goods receipts expected")
    check(
        all(r[0] == "received" for r in q("select status from po")),
        f"every order should be fully received: {[r[0] for r in q('select status from po')]}",
    )
    check(all(r[0] is None for r in q("select closed_at from po")),
          "an order closed before its money was settled (block 13 R2)")
    stock_after = {r[0]: lib.D(r[1]) for r in q("select item_code, on_hand from stock_level")}
    for code, qty, _ in expected_lines.values():
        check(
            stock_after.get(code) == on_hand[code] + qty,
            f"{code}: stock {stock_after.get(code)}, expected {on_hand[code]} + {qty}",
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
        for designation in (STORE, BUYER, REQUESTER):
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
        inv = q("select taxable, total, due_date from invoice where no = :n", n=no)[0]
        po = orders[supplier]
        check(lib.D(inv[0]) == lib.D(po[3]), f"{no}: invoice taxable {inv[0]} differs from the order's {po[3]}")
        check(
            abs(lib.D(inv[1]) - lib.D(po[7])) <= Decimal("0.50"),
            f"{no}: invoice total {inv[1]} differs from the order's {po[7]} by more than the rupee round-off",
        )
        due = TODAY + timedelta(days=terms[suppliers[supplier]["payment_terms_code"]])
        if suppliers[supplier]["msme_category"] in ("micro", "small") and suppliers[supplier]["agreed_payment_days"]:
            due = min(due, TODAY + timedelta(days=min(45, int(suppliers[supplier]["agreed_payment_days"]))))
        check(
            inv[2] == due,
            f"{no}: due date {inv[2]}, expected {due} from the supplier's terms ({suppliers[supplier]['payment_terms_code']})",
        )
    micro = next(c for c, s in suppliers.items() if s["msme_category"] == "micro")
    micro_due = q("select due_date from invoice where supplier_code = :s", s=micro)
    out.metrics["micro supplier: due date (45-day terms and agreement)"] = str(micro_due[0][0]) if micro_due else "-"

    # ---- 8. the payment run: planned by finance, approved by the director -----------------------------
    pin_today(PAY_DAY)
    refused(AP, "POST", "/api/payment-runs", {"run_date": PAY_DAY.isoformat(), "window_days": 7})
    plan_run = call(FINANCE, "POST", "/api/payment-runs", {"run_date": PAY_DAY.isoformat(), "window_days": 7}).json()
    run_no = plan_run.get("run") or plan_run.get("id") or plan_run.get("no")
    check(bool(run_no), f"no run number in {plan_run}")
    rows = q("select supplier_code, amount from payment where run_no = :r order by supplier_code", r=run_no)
    check(
        [r[0] for r in rows] == sorted(orders),
        f"one payment per supplier expected: {[tuple(r) for r in rows]}",
    )
    check(
        chain("payment_run", Decimal(1)) == "director", "the dataset's rule for payment runs should name the director"
    )
    refused(FINANCE, "POST", f"/api/payment-runs/{run_no}/file")  # not approved yet
    waiting = [
        p
        for p in call(DIRECTOR, "GET", "/api/admin/proposals?status=pending").json()
        if p["action"].startswith("payment_run")
    ]
    check(len(waiting) == 1, f"one payment run proposal should wait for the director, found {len(waiting)}")
    if waiting:
        proposal = waiting[0]["id"]
        refused(FINANCE, "POST", f"/api/admin/proposals/{proposal}/approve", {})  # the proposer does not approve it
        refused(AP, "POST", f"/api/admin/proposals/{proposal}/approve", {})
        refused(ADMIN, "POST", f"/api/admin/proposals/{proposal}/approve", {})
        call(DIRECTOR, "POST", f"/api/admin/proposals/{proposal}/approve", {})
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
    check(all(r[0] is not None for r in q("select closed_at from po")),
          "a closed order has no closed_at (block 13 R2.4)")
    check(all(r[0] == "closed" for r in q("select status from po_line where status <> 'cancelled'")),
          "an order closed but a line did not (block 13 R2.4)")
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

    # ---- every document the flow made has a history: no write without an audit row (CLAUDE.md rule 4) --------
    unaudited: dict[str, list[str]] = {}
    # (a payment is a line of its payment run: its history is the run's, found through run_no)
    for table, key in (
        ("request", "no"), ("rfq", "no"), ("quote", "no"), ("po", "no"), ("goods_receipt", "no"),
        ("invoice", "no"), ("payment_run", "no"), ("payment", "run_no"),
    ):  # fmt: skip
        missing = [
            str(r[0])
            for r in q(
                f"select t.{key} from {table} t where not exists "  # noqa: S608 - fixed table and column names
                f"(select 1 from audit_event a where a.entity_id = t.{key}) order by t.{key}"
            )
        ]
        if missing:
            unaudited[table] = missing[:3]
    out.metrics["documents with no audit row at all"] = unaudited or "none"
    check(not unaudited, f"documents the flow created have no audit row: {unaudited}")

    out.metrics["steps checked (one assertion each)"] = checks
    out.metrics["emails sent (quotation requests, orders, payment advice, ...)"] = len(box.sent)
    pin_today(TODAY)
    out.passed = out.passed and not any(f.severity in ("high", "medium") for f in out.findings)
    db.close()
    return out
