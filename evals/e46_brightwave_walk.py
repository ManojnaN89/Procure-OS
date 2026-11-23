"""E46: the second demo company (Brightwave Technologies, IT services) through the plan's 13 steps in a real browser.

The same harness as E34 (evals/demo_ui.py): `demo reset brightwave`, each person signs in on the login screen in their own
window and does their own step, the worker runs between steps, mail goes to a fake outbox. Brightwave buys services and
IT goods, so the journey is: a laptop (serialised, on the rate contract), headsets by quotation and the monthly
housekeeping (a service contract, above the buyer's tier: the CFO approves the order); the requests approved in the app
and by the emailed link; the orders sent by the buyer (Brightwave has no touchless orders); the laptop supplier accepts
by its link; the store receives the laptop with its serial number; an invoice for the housekeeping before anyone
confirmed it is held, the requester confirms the service (the two-way match) and it is matched; the laptop and headsets
are matched three ways; finance plans one run, the CFO approves it, the bank file and the statement pay everything and
close the orders. Every figure is compared with the dataset's own files; who did what is read back from the audit log.
"""

import hashlib
import re
import tempfile
import time
import traceback
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import demo_ui
import env
import lib
from e34_flow_journey import Journey, demo_ui_checks, money, rupees
from lib import Outcome
from sqlalchemy import text

ID = "E46"
TITLE = "The IT services company (Brightwave) through all 13 steps in a browser: services, a serialised laptop, two-way match"
REQUIREMENT = (
    "Phase 1 done-when: both demo companies run through all 13 steps in the browser; services are confirmed by the "
    "requester and matched two ways; a serialised item is received with its serial; each step by its own person"
)

TODAY = date(2026, 11, 6)
PAY_DAY = date(2026, 12, 21)
DOMAIN = "brightwave.example"
KEYS = ("employee", "facilities", "buyer", "ap", "finance", "depthead", "ithead", "cfo")
CODE_OF = {
    "U01": "employee", "U02": "facilities", "U03": "buyer", "U04": "ap", "U05": "finance",
    "U07": "depthead", "U08": "ithead", "U09": "cfo",
}  # fmt: skip
HEADSET_QUOTES = {"SUP-002": "2350", "SUP-001": "2450"}  # typed in by the buyer
SERIAL = "BW-LP01-0001"


def run(ctx: Any) -> Outcome:
    env.forget_tenants("e46")
    try:
        return _run(ctx)
    finally:
        env.forget_tenants("e46")


def _run(ctx: Any) -> Outcome:  # noqa: C901 - one story, step by step
    import httpx
    from p2p.config import get_settings
    from p2p.formulas.calendar import pin_today
    from p2p.platform import demo, routing, tenants, worker
    from playwright.sync_api import expect, sync_playwright
    from sqlalchemy.orm import Session

    expect.set_options(timeout=20_000)
    out = Outcome(True)
    if not (lib.ROOT / "frontend" / "dist" / "index.html").is_file():
        out.skipped = True  # type: ignore[attr-defined]
        out.notes.append("skipped: the admin console is not built (cd frontend && npm run build)")
        return out
    with tenants.control_engine().begin() as conn:
        conn.execute(text("delete from job"))
    import os

    os.environ["PUBLIC_BASE_URL"] = demo_ui.URL
    get_settings.cache_clear()
    demo_ui.RESULTS = lib.ROOT / "evals" / "results" / "journeys"
    demo_ui.lib.PASSWORD = demo.DEV_PASSWORD
    pin_today(TODAY)

    folder = Path(tempfile.mkdtemp(prefix="e46_"))
    slug = env.slug("e46")
    demo.reset_company(demo.company("brightwave"), folder / "dataset", credentials=None, slug=slug)
    dataset = folder / "dataset"
    db = Session(routing.engine_for(slug))
    box = lib.capture_mail()
    c = demo_ui_checks(out)
    c.run = "brightwave"

    items = {r["code"]: r for r in lib.rows("master", "item", dataset)}
    suppliers = {r["code"]: r for r in lib.rows("master", "supplier", dataset)}
    people = {r["code"]: r for r in lib.rows("master", "user", dataset)}
    contacts = {
        r["supplier_code"]: r["email"].lower()
        for r in lib.rows("master", "supplier_contact", dataset)
        if r["is_primary"] == "true"
    }
    contract = {
        p["item_code"]: (p["supplier_code"], Decimal(p["price"]))
        for p in lib.rows("master", "price_record", dataset)
        if p["contract_code"]
    }
    rules = lib.rows("master", "approval_rule", dataset)

    def estimate(item: str) -> Decimal:
        """Plan step 3: the contract price, else the last price paid (no order exists yet), else the standard price."""
        return contract[item][1] if item in contract else Decimal(items[item]["standard_price"])

    def band(document: str, value: Decimal) -> str:
        return next(
            (
                r["approver_chain"]
                for r in rules
                if r["document"] == document
                and r["category_code"] == "ANY"
                and r["spend_type"] == "ANY"
                and Decimal(r["min_value"]) <= value <= Decimal(r["max_value"])
            ),
            "?",
        )

    def q(sql: str, **params: Any) -> list[Any]:
        db.rollback()
        got = list(db.execute(text(sql), params).all())
        db.rollback()
        return got

    def one(sql: str, **params: Any) -> Any:
        return q(sql, **params)[0][0]

    def work() -> None:
        db.commit()
        for _ in range(60):
            if not worker.run_once():
                break
        db.rollback()

    tokens: dict[str, dict[str, str]] = {}

    def token(key: str) -> dict[str, str]:
        if key not in tokens:
            r = httpx.post(
                f"{demo_ui.URL}/api/auth/login",
                json={"email": f"{key}@{DOMAIN}", "password": demo.DEV_PASSWORD},
                timeout=30,
            )
            tokens[key] = {"Authorization": "Bearer " + r.json()["access_token"]}
        return tokens[key]

    def mails_to(address: str) -> list[dict[str, Any]]:
        return [m for m in box.sent if m["to"].lower() == address.lower()]

    verdict = "passed"
    with demo_ui.Site(), sync_playwright() as p:
        browser = p.chromium.launch()
        d = Journey("brightwave", TITLE, SimpleNamespace(db=db, slug=slug), box, browser)
        try:
            for key in KEYS:
                code = next(k for k, v in CODE_OF.items() if v == key)
                who, designation = people[code]["name"], people[code]["designation"]
                d.sign_in(key, f"{key}@{DOMAIN}", f"{who}, {designation} ({key}@)")

            # ---------------------------------------------------------------- 1-3. requests on the form
            def raise_request(key: str, item: str, qty: int, why: str) -> str:
                with d.step(key, f"opens Requests, New request: {items[item]['name']}, {qty}; Save as draft") as page:
                    d.go(key, "requisitions/new")
                    expect(page.get_by_role("heading", name="New request")).to_be_visible()
                    page.locator(".req-item").first.select_option(item)
                    page.locator(".req-qty").first.fill(str(qty))
                    page.locator("#reqWhy").fill(why)
                    page.locator("#reqSave").click()
                    expect(page.locator("#appContent")).to_contain_text("Draft")
                d.sync()
                no = one("select no from request order by created_at desc, no desc limit 1")
                with d.step(key, f"checks {no} and clicks Submit for approval") as page:
                    page.get_by_role("button", name="Submit for approval").click()
                    expect(page.locator("#appContent")).to_contain_text("Waiting for")
                d.sync()
                return no

            r_laptop = raise_request("employee", "LP-01", 1, "Laptop for the new engineer")
            d.mark(1)
            with d.step("employee", f"opens {r_laptop}: the stock check, the estimate and the budget") as page:
                d.go("employee", f"requisitions/{r_laptop}")
                expect(page.locator("#appContent")).to_contain_text("Check before ordering")
                expect(page.locator("#appContent")).to_contain_text("Budget account")
                expect(page.locator("#appContent")).to_contain_text(rupees(estimate("LP-01"))[:-3])
                if "LP-01" in contract:  # the request line says where its estimate came from
                    expect(page.locator("#appContent")).to_contain_text("estimate from contract")
            d.mark(3)
            r_head = raise_request("employee", "HS-01", 5, "Headsets for the support desk")
            r_house = raise_request("employee", "SV-01", 1, "Housekeeping for the engineering floor in December")
            values = {
                n: Decimal(one("select total_value from request where no = :n", n=n))
                for n in (r_laptop, r_head, r_house)
            }
            c.same(
                "each request is valued at the estimate: the contract price, else the standard price (nothing paid yet)",
                values,
                {
                    r_laptop: money(estimate("LP-01")),
                    r_head: money(5 * estimate("HS-01")),
                    r_house: money(estimate("SV-01")),
                },
            )
            with d.step(
                "buyer", "clicks Check stock levels on Requests: the office supplies against their reorder points"
            ) as page:
                d.go("buyer", "requisitions")
                page.locator("#replenishBtn").click()
                expect(page.locator("#replenishPanel")).to_contain_text(
                    re.compile("reorder point|Nothing needs ordering")
                )
                page.locator("#closeReplenishBtn").click()
            d.mark(2)
            work()

            # ---------------------------------------------------------------- 4. approvals: in the app, the last one by its emailed link
            def approve_all(no: str) -> list[str]:
                who_approved: list[str] = []
                for _ in range(5):
                    pending = q(
                        "select coalesce(s.delegate, s.approver) from approval_step s join request r on r.no = s.request_no "
                        "and s.round = r.approval_round where s.request_no = :n and s.status = 'pending'",
                        n=no,
                    )
                    if not pending:
                        break
                    key = CODE_OF[pending[0][0]]
                    later = one(
                        "select count(*) from approval_step s join request r on r.no = s.request_no and s.round = "
                        "r.approval_round where s.request_no = :n and s.status = 'waiting'",
                        n=no,
                    )
                    link = next(
                        (
                            m
                            for m in reversed(mails_to(f"{key}@{DOMAIN}"))
                            if "/approve/" in m["body"] and no in m["body"] + m["subject"]
                        ),
                        None,
                    )
                    if later == 0 and link:
                        url = re.search(r"https?://\S+/approve/[\w.\-]+", link["body"]).group(0)  # type: ignore[union-attr]
                        # D-07 (block 4): a decision from an email link needs the one-time code emailed to that person
                        with d.step(
                            key, f"opens the emailed link for {no}, asks for a code, types it and clicks Approve"
                        ) as page:
                            page.goto(url)
                            page.get_by_role("button", name="Email me a code").click()
                            expect(page.locator("body")).to_contain_text("A code was sent to")
                            coded = [m for m in mails_to(f"{key}@{DOMAIN}") if "code is " in m["body"]]
                            page.locator("input[name=code]").fill(
                                coded[-1]["body"].split("code is ")[1][:6] if coded else ""
                            )
                            page.get_by_role("button", name="Approve").click()
                            expect(page.locator("body")).to_contain_text("Approved")
                        d.mark(4)
                    else:
                        with d.step(key, f"opens {no} from My work and clicks Approve") as page:
                            d.go(key, "dashboard")
                            expect(page.locator("#myWork")).to_contain_text(no)
                            d.go(key, f"requisitions/{no}")
                            page.locator("#approveBtn").click()
                            page.wait_for_load_state("networkidle")
                            # wait until the click is saved before looking up the next approver (bounded)
                            for _ in range(40):
                                if (
                                    one(
                                        "select count(*) from approval_step s join request r on r.no = s.request_no "
                                        "and s.round = r.approval_round where s.request_no = :n and s.status = 'pending' "
                                        "and coalesce(s.delegate, s.approver) = :who",
                                        n=no,
                                        who=pending[0][0],
                                    )
                                    == 0
                                ):
                                    break
                                time.sleep(0.5)
                    who_approved.append(key)
                    d.sync()
                    work()
                return who_approved

            chains = {no: approve_all(no) for no in (r_laptop, r_head, r_house)}
            out.metrics["approvers per request"] = chains
            for no in (r_laptop, r_head, r_house):
                status = one("select status from request where no = :n", n=no)
                c.true(f"{no} approved (closed once all of it is ordered)", status in ("approved", "closed"), status)
            c.true("the laptop (IT hardware) went through the IT head", "ithead" in chains[r_laptop], str(chains))
            c.true(
                "nobody approved their own request",
                all("employee" not in v and "facilities" not in v for v in chains.values()),
            )

            # ---------------------------------------------------------------- 5. buying route: contracts and a quotation
            routes = {r[0]: r[1] for r in q("select request_no, method from sourcing_decision")}
            c.same(
                "the laptop and the housekeeping are bought on their contracts, the headsets by quotation",
                (routes.get(r_laptop), routes.get(r_house), routes.get(r_head)),
                ("contract", "contract", "rfq"),
            )
            with d.step("buyer", f"opens {r_head}: the line's buying route and why") as page:
                d.go("buyer", f"requisitions/{r_head}")
                expect(page.locator("#appContent")).to_contain_text("quotes")
            d.mark(5)

            # ---------------------------------------------------------------- 6. quotation: send, two quotes, select
            rfq_no = one("select no from rfq where request_no = :n", n=r_head)
            with d.step("buyer", f"opens Admin, Approvals and clicks Approve to send {rfq_no}") as page:
                d.go_admin("buyer", "approvals")
                card = page.locator(".space-y-3", has_text=rfq_no).first
                expect(card).to_be_visible()
                expect(card).not_to_contain_text("rfq.send")
                card.get_by_role("button", name="Approve", exact=True).click()
                expect(page.locator(".space-y-3", has_text=rfq_no)).to_have_count(0)
            d.sync()
            work()
            asked = sorted(r[0] for r in q("select supplier_code from rfq_supplier where rfq_no = :n", n=rfq_no))
            approved_ith = sorted(
                r["supplier_code"]
                for r in lib.rows("master", "supplier_category", dataset)
                if r["category_code"] == "ITH"
            )
            c.same(f"{rfq_no} asked the suppliers approved for IT hardware", asked, approved_ith)
            for supplier, price in HEADSET_QUOTES.items():
                if supplier not in asked:
                    continue
                with d.step(
                    "buyer", f"Enter a quote for {suppliers[supplier]['trade_name']}: {price} a headset"
                ) as page:
                    d.go("buyer", f"rfqs/{rfq_no}")
                    page.locator("#addQuoteBtn").click()
                    page.locator("#qSupplier").select_option(supplier)
                    row = page.locator("#quoteForm tbody tr").first
                    row.locator(".qPrice").fill(price)
                    row.locator(".qDisc").fill("0")
                    row.locator(".qGst").fill(items["HS-01"]["gst_rate_before"])
                    row.locator(".qDays").fill("5")
                    page.locator("#saveQuote").click()
                    expect(page.locator("#appContent")).to_contain_text("Quotes")
                d.sync()
            cheapest = min((s for s in HEADSET_QUOTES if s in asked), key=lambda s: Decimal(HEADSET_QUOTES[s]))
            with d.step(
                "buyer", f"sees {suppliers[cheapest]['trade_name']} recommended and clicks Select these suppliers"
            ) as page:
                d.go("buyer", f"rfqs/{rfq_no}")
                expect(page.locator("#appContent tr", has_text="recommended")).to_contain_text(
                    suppliers[cheapest]["trade_name"].split()[0]
                )
                if page.locator("#awardReason").count():
                    page.locator("#awardReason").fill("Two quotes; the cheaper one")
                page.locator("#awardBtn").click()
                expect(page.locator("#appContent")).to_contain_text("Awarded")
            d.mark(6)
            d.sync()
            work()

            # ---------------------------------------------------------------- 7. orders: the CFO approves the large one; the buyer sends
            pos = {
                r[0]: r
                for r in q(
                    "select no, supplier_code, status, taxable, total, touchless, request_no from po order by no"
                )
            }
            by_request = {r[6]: no for no, r in pos.items()}
            c.same("one order per request", sorted(by_request), sorted([r_laptop, r_head, r_house]))
            want = {
                r_laptop: (contract["LP-01"][0], contract["LP-01"][1]),
                r_house: (contract["SV-01"][0], contract["SV-01"][1]),
                r_head: (cheapest, 5 * Decimal(HEADSET_QUOTES[cheapest])),
            }
            for req, (supplier, taxable) in want.items():
                row = pos.get(by_request.get(req, ""), None)
                c.same(
                    f"{req}: ordered from {supplier} at {taxable} before tax",
                    (row[1], money(row[3])) if row else None,
                    (supplier, money(taxable)),
                )
            c.true(
                "no order went out touchless (Brightwave issues every order by a buyer)",
                not any(r[5] for r in pos.values()),
            )
            for no, row in pos.items():
                c.same(
                    f"{no} of {money(row[4])}: waits for the cfo (not touchless; never the buyer)",
                    row[2],
                    "submitted",
                )
                if row[2] != "submitted":
                    continue
                c.true(
                    "the buyer cannot approve it",
                    demo_ui.api("POST", f"/api/purchase-orders/{no}/approve", token("buyer"), json={}).status_code
                    in (403, 409, 422),
                )
                with d.step("cfo", f"opens {no} from My work: Orders waiting for my approval; clicks Approve") as page:
                    d.go("cfo", "dashboard")
                    expect(page.locator("#myWork")).to_contain_text(no)
                    d.go("cfo", f"purchase-orders/{no}")
                    if page.locator("#poCodeAsk").count():  # above Rs 5,00,000 approving needs a code (block 4)
                        page.locator("#poCodeAsk").click()
                        expect(page.locator("#poCodeSent")).to_contain_text("Sent to")
                        coded = [m for m in mails_to(f"cfo@{DOMAIN}") if "code is " in m["body"]]
                        page.locator("#poCode").fill(coded[-1]["body"].split("code is ")[1][:6] if coded else "")
                    page.locator("#poApprove").click()
                    expect(page.locator("#appContent")).to_contain_text("Approved by")
                d.mark(7)
                d.sync()
            for no in pos:
                with d.step(
                    "buyer", f"opens {no}: why it was not touchless, in plain words; clicks Send to supplier"
                ) as page:
                    d.go("buyer", f"purchase-orders/{no}")
                    content = page.locator("#appContent")
                    expect(content).not_to_contain_text("po.issue")
                    expect(content).not_to_contain_text("autonomy gate")
                    page.locator("#poSend").click()
                    expect(content).to_contain_text("Sent to")
                d.sync()

            # ---------------------------------------------------------------- 8. the laptop supplier accepts by its link; the buyer records the others
            laptop_po, head_po, house_po = by_request[r_laptop], by_request[r_head], by_request[r_house]
            promised = TODAY + timedelta(days=5)
            for no in pos:
                supplier = pos[no][1]
                if no == laptop_po:
                    mail = next(
                        m
                        for m in reversed(box.sent)
                        if m["to"].lower() == contacts[supplier] and no in m["subject"] and "/supplier/" in m["body"]
                    )
                    url = re.search(r"https?://\S+/supplier/[\w.\-]+", mail["body"]).group(0)  # type: ignore[union-attr]
                    d.visitor("supplier", f"{suppliers[supplier]['trade_name']} (supplier, no login)")
                    with d.step(
                        "supplier",
                        f"opens the link in the order email for {no}, clicks Email me a code, types the code from its "
                        "mail and clicks Accept the order (each line on the date ordered)",
                    ) as page:
                        page.goto(url)
                        before = len(box.sent)
                        page.get_by_role("button", name="Email me a code").first.click()
                        expect(page.locator("body")).to_contain_text("A code was sent to")
                        coded = [  # D53: the code goes only to the address the order went to
                            m for m in box.sent[before:] if no in m["subject"] and "one-time code is " in m["body"]
                        ]
                        c.true(
                            f"{no}: the code went to the order's contact",
                            [m["to"].lower() for m in coded] == [contacts[supplier]],
                        )
                        accept = page.locator("form", has=page.locator("button[value=accept]"))
                        accept.locator("input[name=code]").fill(
                            coded[-1]["body"].split("code is ")[1][:6] if coded else ""
                        )
                        page.get_by_role("button", name="Accept the order").click()
                        expect(page.locator("body")).to_contain_text("accepted")
                    d.mark(8)
                else:
                    with d.step(
                        "buyer",
                        f"clicks Record acknowledgement on {no}: each line on the date ordered, and how the "
                        "supplier confirmed",
                    ) as page:
                        d.go("buyer", f"purchase-orders/{no}")
                        page.locator("#poAckNote").fill(f"Confirmed by phone, {promised:%d %b}")
                        page.locator("#poAck").click()
                        expect(page.locator("#appContent")).to_contain_text("Acknowledged version")
                d.sync()
            c.same("every order is acknowledged", sorted({r[0] for r in q("select status from po")}), ["acknowledged"])

            # ---------------------------------------------------------------- 9. receipt: the laptop with its serial, the headsets
            for no, qty in ((laptop_po, 1), (head_po, 5)):
                with d.step(
                    "facilities",
                    f"opens Goods receipt, Receive goods on {no}: {qty}"
                    + (f", serial {SERIAL}" if no == laptop_po else ""),
                ) as page:
                    d.go("facilities", "receiving")
                    card = page.locator("#waitingWrap .panel", has_text=no)
                    card.locator(".recv-open").click()
                    card.locator(".recv-qty").first.fill(str(qty))
                    if no == laptop_po:
                        card.locator(".recv-serials").first.fill(SERIAL)
                    card.locator(".recv-challan").fill(f"DC/{no[-4:]}")
                    card.locator(".recv-send").click()
                    expect(page.locator("#appContent")).to_contain_text("GRN-")
                if no == laptop_po:
                    d.mark(9)
                d.sync()
            c.same(
                "the laptop is in stock under its serial",
                q("select serial, item_code from serial_number"),
                [(SERIAL, "LP-01")],
            )

            # ---------------------------------------------------------------- 10-11. invoices: the service held until confirmed, then two-way; goods three-way
            def enter_invoice(supplier: str, number: str, qty: Decimal, price: Decimal, po: str) -> str:
                with d.step(
                    "ap", f"Enter an invoice: {suppliers[supplier]['trade_name']} {number}, {qty} at {price}"
                ) as page:
                    d.go("ap", "invoices/new")
                    page.locator("#invSupplier").select_option(supplier)
                    page.locator("#invNo").fill(number)
                    page.locator("#invDate").fill(TODAY.isoformat())
                    if suppliers[supplier]["e_invoice_applicable"] == "true":  # its invoice carries the IRN
                        page.locator("#invIrn").fill(hashlib.sha256(number.encode()).hexdigest())
                    row = page.locator(f"#invLines tr[data-po='{po}']").first
                    row.locator(".il-qty").fill(str(qty))
                    row.locator(".il-price").fill(str(price))
                    page.locator("#invSave").click()
                    expect(page.locator("#appContent")).to_contain_text("Taxable")
                d.sync()
                return str(one("select no from invoice where supplier_invoice_no = :n", n=number))

            house_supplier, house_price = contract["SV-01"]
            inv_house = enter_invoice(house_supplier, "CSF/2611/0091", Decimal(1), house_price, house_po)
            d.mark(10)
            c.same(
                "the housekeeping invoice, before anyone confirmed the service, is held",
                one("select status from invoice where no = :n", n=inv_house),
                "blocked",
            )
            with d.step("ap", f"opens {inv_house}: held because the service is not confirmed") as page:
                d.go("ap", f"invoices/{inv_house}")
                expect(page.locator("#appContent")).to_contain_text(re.compile("not confirmed", re.I))
            with d.step("employee", "opens Confirm services: the housekeeping line; 100 percent; Confirm") as page:
                d.go("employee", "services")
                card = page.locator(f"[data-po='{house_po}']").first
                card.locator(".svc-basis").select_option("pct")
                card.locator(".svc-value").fill("100")
                card.locator(".svc-send").click()
                expect(page.locator("#svcDone")).to_contain_text(house_po)
            d.sync()
            work()
            with d.step(
                "ap", f"opens {inv_house} again: matched two ways (order and confirmation) and approved"
            ) as page:
                d.go("ap", f"invoices/{inv_house}")
                expect(page.locator("#appContent")).not_to_contain_text("Blocked")
            d.mark(11)
            c.same(
                "the confirmation freed the housekeeping invoice",
                one("select status from invoice where no = :n", n=inv_house),
                "approved",
            )
            enter_invoice(contract["LP-01"][0], "BITD/2611/2201", Decimal(1), contract["LP-01"][1], laptop_po)
            enter_invoice(cheapest, "HDS/2611/0042", Decimal(5), Decimal(HEADSET_QUOTES[cheapest]), head_po)
            c.same(
                "every invoice is approved for payment",
                sorted({r[0] for r in q("select status from invoice")}),
                ["approved"],
            ) or out.notes.append(str(q("select no, status, block_reason from invoice")))

            # ---------------------------------------------------------------- 12. payment: finance plans, the CFO approves, bank file
            pin_today(PAY_DAY)
            d.sync()
            with d.step("finance", "opens Payments, See what is due, Send the run for approval") as page:
                d.go("finance", "payments")
                page.locator("#runDate").fill(PAY_DAY.isoformat())
                page.locator("#runWindow").fill("7")
                page.locator("#previewRun").click()
                page.locator("#planRun").click()
                expect(page.locator("#appContent")).to_contain_text("Waiting for the owner")
            d.sync()
            run_no = one("select no from payment_run")
            pid = int(one("select id from proposal where action = 'payment_run.approve' and status = 'pending'"))
            c.same(
                "finance cannot approve its own run",
                demo_ui.api("POST", f"/api/admin/proposals/{pid}/approve", token("finance"), json={}).status_code,
                403,
            )
            with d.step("cfo", "opens Admin, Approvals: the payment run; clicks Approve") as page:
                d.go_admin("cfo", "approvals")
                card = page.locator(".space-y-3", has_text="Pay ").first
                card.get_by_role("button", name="Approve", exact=True).click()
                expect(page.locator(".space-y-3", has_text="Pay ")).to_have_count(0)
            d.sync()
            with d.step("finance", f"opens {run_no}: Make the bank file, Show the bank file") as page:
                d.go("finance", f"payments/{run_no}")
                page.locator("#makeFile").click()
                page.locator("#showFile").click()
                expect(page.locator("#fileText")).to_contain_text("payment_no")
            d.mark(12)
            d.sync()
            pays = q("select no, supplier_code, amount from payment where run_no = :r order by no", r=run_no)
            c.same("one payment per supplier", sorted(r[1] for r in pays), sorted({r[1] for r in pos.values()}))

            # ---------------------------------------------------------------- 13. the bank statement: paid, closed
            statement = folder / "statement.csv"
            statement.write_text(
                "Date,Bank reference,Narration,Debit\n"
                + "".join(
                    f"{PAY_DAY},UTR{n:06d},NEFT {r[0]} BRIGHTWAVE,{money(r[2])}\n" for n, r in enumerate(pays, 1)
                ),
                encoding="utf-8",
            )
            with d.step("finance", "chooses the bank's statement and clicks Import the bank statement") as page:
                page.set_input_files("#stmtFile", str(statement))
                page.locator("#importStmt").click()
                expect(page.locator("#appContent .fs-note")).to_contain_text("The bank has paid")
            d.sync()
            with d.step("employee", "sees in the notifications that the orders are paid and closed") as page:
                d.go("employee", "dashboard")
                page.locator("#notificationBellBtn").click()
                expect(page.locator(".notif-row", has_text="paid and closed").first).to_be_visible()
            d.mark(13)
            c.same("every invoice is paid", sorted({r[0] for r in q("select status from invoice")}), ["paid"])
            c.same("every order is closed", sorted({r[0] for r in q("select status from po")}), ["closed"])

            # ---------------------------------------------------------------- 13. reconciliation: the service order's trace, nothing left open
            entries = [r[0] for r in q("select no from service_entry where po_no = :p order by no", p=house_po)]
            house_pay = [r[0] for r in pays if r[1] == house_supplier]
            with d.step(
                "finance", f"opens Reconciliation, Order trace; traces {house_po}: the service from request to payment"
            ) as page:
                d.go("finance", "reconciliation")
                page.locator("#recTabs .tab[data-tab=trace]").click()
                page.locator("#traceNo").fill(house_po)
                page.locator("#traceGo").click()
                box = page.locator("#traceBox")
                expect(box).to_contain_text("Service entries")
                for no in (r_house, house_po, *entries, inv_house, *house_pay):
                    expect(box).to_contain_text(no)
            d.keep(13, "order trace")
            chain = demo_ui.api("GET", f"/api/trace/{house_po}", token("finance")).json()
            c.same(
                f"the trace of {house_po}: the request, the order, the requester's confirmation, the invoice, the payment",
                {s["stage"]: [i["no"] for i in s["items"]] for s in chain["stages"]},
                {
                    "request": [r_house],
                    "po": [house_po],
                    "service_entry": entries,
                    "invoice": [inv_house],
                    "payment": house_pay,
                },
            )
            with d.step(
                "finance", f"opens Received not invoiced as of {PAY_DAY:%d %b %Y}: nothing is left open"
            ) as page:
                page.locator("#recTabs .tab[data-tab=grni]").click()
                page.locator("#grniDate").fill(PAY_DAY.isoformat())
                page.locator("#grniGo").click()
                expect(page.locator("#grniBox")).to_contain_text("Everything received by this date is invoiced")
            d.keep(13, "received not invoiced")
            grni = demo_ui.api(
                "GET", f"/api/reconciliation/received-not-invoiced?as_of={PAY_DAY}", token("finance")
            ).json()
            # each line was invoiced at its order price for what was received: the laptop, five headsets, the service
            c.same(
                "received not invoiced and invoiced not received: both nothing",
                (Decimal(grni["not_invoiced"]), Decimal(grni["not_received"]), grni["lines"]),
                (Decimal(0), Decimal(0), []),
            )

            # ---------------------------------------------------------------- who did what
            def actors(*actions: str) -> set[str]:
                return {
                    str(r[0])
                    for r in q(
                        "select distinct actor_id from audit_event where action = any(:a) and actor_type = 'user'",
                        a=list(actions),
                    )
                }

            parts = {
                "received the goods": actors("grn.post"),
                "entered the invoices": actors("invoice.enter", "invoice.book"),
                "planned the run": actors("payment_run.propose"),
                "approved the run": actors("payment_run.approve"),
            }
            c.true("the audit log names who did each step", all(parts.values()), str(parts))
            c.true(
                "receiver, AP clerk, payer and run approver are four different people",
                len(set().union(*parts.values())) == 4,
                str(parts),
            )
        except Exception as exc:  # noqa: BLE001 - a failed step is a finding with the step it stopped at
            verdict = "stopped"
            last = d.steps[-1]["did"] if d.steps else "the start"
            first_line = str(exc).splitlines()[0][:300] if str(exc) else ""
            out.find("high", f"brightwave: stopped after '{last}': {type(exc).__name__}: {first_line}")
            out.notes.append(traceback.format_exc(limit=5))
        serious = [x for x in d.problems if " 5" in x[:24] or "page error" in x or "console error" in x]
        if serious:
            out.find("medium", f"brightwave: the browser reported: {serious[:4]}")
        if c.failed and verdict == "passed":
            verdict = "FAILED"
        d.write_transcript(c.facts, verdict)
        out.metrics["steps taken at screens"] = len(d.steps)
        out.metrics["plan steps shown"] = f"{len(d.flow)} of 13"
        out.metrics["checks passed / failed"] = f"{c.passed} / {c.failed}"
        d.close()
        browser.close()
    pin_today(TODAY)
    out.metrics["transcript and screenshots"] = "evals/results/journeys/brightwave.md"
    out.passed = out.passed and not any(f.severity in ("high", "medium") for f in out.findings)
    db.close()
    return out
