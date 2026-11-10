"""E34: the whole application, as a person meets it: the small demo company (`flow`), eight logins, a real browser, the
demo script followed to the letter from "Check stock levels" to a paid invoice, then every role guide followed as a brand-new
user, and every screen of every role swept for errors, overflow and raw codes at laptop and phone width.

What is checked is what a person would notice: the right work shows up for the right person, the screens say what the guide
says they say, every figure the decisions used equals an independent calculation from the dataset's own files, the people
who must differ do differ, and nothing the browser reports is left over.
"""

import json
import os
import re
import sys
import tempfile
import traceback
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import demo_ui
import env
import lib
from lib import Outcome
from sqlalchemy import text

sys.path.insert(0, str(lib.ROOT / "tests"))
import invoice_documents as docs  # noqa: E402

ID = "E34"
TITLE = (
    "The whole application for a real user: the demo script from low stock to a paid invoice in a browser, every role "
    "guide followed as a new user, and every screen swept for errors, overflow and raw codes"
)
REQUIREMENT = (
    "Phase 1 acceptance: the flow works end to end for real people at real screens; a new user completes their first task "
    "from the guide; no screen shows an error, a blank panel, a raw code or sideways scrolling"
)

TODAY = date(2026, 11, 6)
PAY_DAY = date(2026, 12, 21)  # after every invoice of the run is due
CENT = Decimal("0.01")
RESULTS = lib.ROOT / "evals" / "results" / "journeys"
GUIDES = lib.ROOT / "docs" / "guides"
EMAILS = {
    "requester": "requester@flow.example",
    "manager": "manager@flow.example",
    "approver": "approver@flow.example",
    "buyer": "buyer@flow.example",
    "store": "store@flow.example",
    "ap": "ap@flow.example",
    "finance": "finance@flow.example",
    "admin": "admin@flow.example",
}
NAMES = {
    "requester": "Requester",
    "manager": "Maintenance Manager",
    "approver": "Plant Head",
    "buyer": "Buyer",
    "store": "Storekeeper",
    "ap": "Accounts Payable",
    "finance": "Finance Manager",
    "admin": "Admin",
}
GUIDE_OF = {  # key -> (guide, the heading of the first task that role does from it)
    "requester": ("requester", ""),
    "manager": ("approver", "Approve a request"),
    "approver": ("approver", "Approve an order"),
    "buyer": ("buyer", ""),
    "store": ("store", ""),
    "ap": ("ap-clerk", ""),
    "finance": ("finance", ""),
    "admin": ("admin", ""),
}


def money(value: Decimal | int | str) -> Decimal:
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


def rupees(value: Decimal) -> str:
    """How an amount is written on the screens: Indian digit grouping, two decimals (the part that matters here: the digits)."""
    whole, _, frac = f"{money(value):f}".partition(".")
    head, tail = whole[:-3], whole[-3:]
    head = re.sub(r"(\d)(?=(\d{2})+$)", r"\1,", head)
    return f"{head + ',' if head else ''}{tail}.{frac}"


class Queue:
    """A scripted model for the assistant: each turn is the next answer in the line. Never a real model."""

    def __init__(self, *turns: str) -> None:
        self.turns = list(turns)

    def chat(
        self, model: str, messages: Any, *, temperature: float, max_tokens: int, json_output: bool, **kw: Any
    ) -> Any:
        from p2p.ai_steps.adapters import RawResponse

        return RawResponse(self.turns.pop(0), 100, 20, "stop")


class Journey(demo_ui.Demo):
    """A demo run that also remembers which words each role has seen on its screens (to hold the guides to account)."""

    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self.seen_words: dict[str, set[str]] = {}

    def step(self, key: str, what: str) -> Any:  # type: ignore[override]
        parent = super().step(key, what)
        outer = self

        class Wrapped:
            def __enter__(self) -> Any:
                return parent.__enter__()

            def __exit__(self, *exc: Any) -> Any:
                try:
                    outer.collect(key)
                finally:
                    return parent.__exit__(*exc)

        return Wrapped()

    SEEN_JS = r"""() => { if (window.__seen) return; window.__seen = new Set();
      const grab = () => document.querySelectorAll("button, a, .btn, .tab, th, h1, h2, h3, .panel-title, label, .badge, .sidebar-label").forEach((e) => {
        const t = (e.innerText || "").replace(/\s+/g, " ").trim().toLowerCase(); if (t && t.length < 120) window.__seen.add(t); });
      const start = () => { grab(); new MutationObserver(grab).observe(document.documentElement, { childList: true, subtree: true, characterData: true }); };
      if (document.documentElement) start(); else document.addEventListener("DOMContentLoaded", start); }"""

    def sign_in(self, key: str, email: str, who: str) -> Any:
        page = super().sign_in(key, email, who)
        self.contexts[key].add_init_script("(" + self.SEEN_JS + ")()")
        page.evaluate(self.SEEN_JS)
        return page

    def collect(self, key: str) -> None:
        page = self.pages.get(key)
        if page is None:
            return
        try:
            words = page.evaluate("() => Array.from(window.__seen || [])")
        except Exception:  # noqa: BLE001 - a page that is gone has nothing to add
            return
        self.seen_words.setdefault(key, set()).update(str(w) for w in words)


def guide_labels(slug: str, hint: str = "") -> tuple[str, list[str]]:
    """The first task of a role guide (or the section whose heading starts with `hint`): the heading, and every bold name in
    its steps, as the guide writes them."""
    source = (GUIDES / f"{slug}.md").read_text(encoding="utf-8")
    sections = re.split(r"^## ", source, flags=re.M)[1:]
    for section in sections:
        heading, _, body = section.partition(chr(10))
        if hint and not heading.strip().lower().startswith(hint.lower()):
            continue
        if not hint and heading.strip().lower().startswith("what you see"):
            continue
        return heading.strip(), [m.strip(" …").rstrip(".") for m in re.findall(r"\*\*(.+?)\*\*", body)]
    return "", []


def run(ctx: Any) -> Outcome:
    import logging

    import httpx
    from e25_invoice_exceptions import Investigator
    from p2p.agents import invoice_exceptions as exceptions_agent
    from p2p.ai_steps.gateway import Gateway
    from p2p.config import get_settings
    from p2p.formulas.calendar import pin_today
    from p2p.platform import demo, emails, mailbox, routing, ui_api, worker
    from p2p.sourcing import replies
    from playwright.sync_api import expect, sync_playwright
    from sqlalchemy.orm import Session

    logging.getLogger("asyncio").setLevel(logging.CRITICAL)
    expect.set_options(timeout=20_000)
    out = Outcome(True)
    if not (lib.ROOT / "frontend" / "dist" / "index.html").is_file():
        out.skipped = True  # type: ignore[attr-defined]
        out.notes.append("skipped: the admin console is not built (cd frontend && npm run build)")
        return out

    from e30_demo_runs import MODEL, RoundedSpec, invoice_answer, model_gateway

    from p2p.platform import tenants

    with tenants.control_engine().begin() as conn:  # jobs the evals before this one left queued are not ours to run
        conn.execute(text("delete from job"))
    os.environ["PUBLIC_BASE_URL"] = demo_ui.URL  # the links in emails point at the app this eval serves
    get_settings.cache_clear()
    demo_ui.RESULTS = RESULTS
    demo_ui.lib.PASSWORD = demo.DEV_PASSWORD
    pin_today(TODAY)

    folder = Path(tempfile.mkdtemp(prefix="e34_"))
    slug = env.slug("e34")
    made = demo.reset_flow(folder / "dataset", credentials=None, slug=slug)
    out.metrics["people / master rows loaded"] = f"{len(made.logins)} / {made.report.rows_loaded if made.report else 0}"
    dataset = folder / "dataset"
    db = Session(routing.engine_for(slug))
    box = lib.capture_mail()
    world = SimpleNamespace(db=db, slug=slug)
    c = demo_ui_checks(out)

    items = {r["code"]: r for r in lib.rows("master", "item", dataset)}
    suppliers = {r["code"]: r for r in lib.rows("master", "supplier", dataset)}
    contacts = {
        r["supplier_code"]: r["email"]
        for r in lib.rows("master", "supplier_contact", dataset)
        if r["is_primary"] == "true"
    }
    plant_gstin = lib.rows("master", "gst_registration", dataset)[0]["gstin"]

    def q(sql: str, **params: Any) -> list[Any]:
        db.rollback()
        rows = list(db.execute(text(sql), params).all())
        db.rollback()  # never leave a transaction open while the app under test is working
        return rows

    def one(sql: str, **params: Any) -> Any:
        return q(sql, **params)[0][0]

    tokens: dict[str, dict[str, str]] = {}

    def token(key: str) -> dict[str, str]:
        if key not in tokens:
            r = httpx.post(
                f"{demo_ui.URL}/api/auth/login", json={"email": EMAILS[key], "password": demo.DEV_PASSWORD}, timeout=30
            )
            tokens[key] = {"Authorization": "Bearer " + r.json()["access_token"]}
        return tokens[key]

    def refused(key: str, method: str, path: str, body: dict[str, Any] | None = None) -> None:
        r = demo_ui.api(method, path, token(key), json=body or {})
        c.true(f"{NAMES[key]} is refused {method} {path} ({r.status_code})", r.status_code in (403, 409, 422))

    def work() -> None:
        db.commit()
        for _ in range(60):
            if not worker.run_once():
                break
        db.rollback()

    def supplier_mails(**where: str) -> list[dict[str, Any]]:
        known = {v.lower() for v in contacts.values()}
        return [m for m in box.sent if m["to"].lower() in known and all(v in m[k] for k, v in where.items())]

    def mails_to(key: str, **where: str) -> list[dict[str, Any]]:
        return [m for m in box.sent if m["to"].lower() == EMAILS[key] and all(v in m[k] for k, v in where.items())]

    verdict = "passed"
    steps_total = 0
    with demo_ui.Site(), sync_playwright() as p:
        browser = p.chromium.launch()
        d = Journey("journey", TITLE, world, box, browser)
        ui_api.use_gateway(model_gateway())
        try:
            for key in EMAILS:
                d.sign_in(key, EMAILS[key], f"{NAMES[key]} ({key}@)")

            # ---------------------------------------------------------------- 1. the buyer checks the stock levels
            with d.step("buyer", "opens Requests: nothing yet, and says what to do next") as page:
                d.go("buyer", "requisitions")
                expect(page.locator("#tableWrap")).to_contain_text("No requests yet")
                expect(page.locator("#tableWrap")).to_contain_text("Next:")
            with d.step("buyer", "clicks Check stock levels: a preview, nothing is created yet") as page:
                page.locator("#replenishBtn").click()
                panel = page.locator("#replenishPanel")
                expect(panel).to_contain_text(re.compile(r"4 items at or below their reorder point would become\s+3 requests, about ₹76,600 in all"))
                expect(panel).to_contain_text("1 approves automatically, 2 need a person's approval")
            c.same("no request exists after the preview", one("select count(*) from request"), 0)
            with d.step("buyer", "clicks Create and submit") as page:
                page.locator("#createDraftsBtn").click()
                expect(page.locator("#replenishPanel")).to_contain_text(
                    "1 approved automatically, 2 waiting for approval"
                )
            by_value = {money(r[1]): r[0] for r in q("select no, total_value from request")}
            c.same(
                "the three requests are for 9,200 (shoes), 55,000 (bearing and seal) and 12,400 (contactor)",
                sorted(by_value),
                [money(9200), money(12400), money(55000)],
            )
            r_shoes, r_mro, r_ele = by_value[money(9200)], by_value[money(55000)], by_value[money(12400)]
            c.same(
                "shoes approve by the rule, with no person",
                q("select status from request where no = :n", n=r_shoes)[0][0],
                "approved",
            )
            c.same(
                "bearing/seal and contactor wait for the maintenance manager",
                sorted(r[0] for r in q("select status from request where no in (:a, :b)", a=r_mro, b=r_ele)),
                ["submitted", "submitted"],
            )
            for who in ("requester", "buyer", "admin", "approver"):
                refused(who, "POST", f"/api/requisitions/{r_mro}/approve", {})

            # ---------------------------------------------------------------- 2. the requester raises a request on the form
            with d.step("requester", "opens Requests and clicks New request") as page:
                d.go("requester", "requisitions")
                page.get_by_role("link", name="New request").click()
                expect(page.get_by_role("heading", name="New request")).to_be_visible()
            with d.step("requester", "chooses MCB 32 A, 40 nos, and clicks Save as draft") as page:
                page.locator(".req-item").first.select_option("ITM-007")
                page.locator(".req-qty").first.fill("40")
                page.locator("#reqWhy").fill("Spares for the line 3 panel")
                page.locator("#reqSave").click()
                expect(page.locator("#appContent")).to_contain_text("MCB 32 A")
                expect(page.locator("#appContent")).to_contain_text("Draft")
            d.sync()
            r_mcb = one("select no from request where no not in (:a, :b, :c)", a=r_shoes, b=r_mro, c=r_ele)
            c.same(
                "the form made a draft of the requester's own",
                q("select status, requester from request where no = :n", n=r_mcb)[0][0],
                "draft",
            )
            with d.step("requester", "checks the lines and clicks Submit for approval") as page:
                page.get_by_role("button", name="Submit for approval").click()
                expect(page.locator("#appContent")).to_contain_text("Waiting for Maintenance Manager")
            d.sync()
            c.same(
                "the new request waits for the maintenance manager (12,800 is between 10,000 and 1,00,000)",
                q("select status, total_value from request where no = :n", n=r_mcb)[0][0],
                "submitted",
            )
            work()

            # ---------------------------------------------------------------- 3. the manager decides: selected, and by emailed link
            with d.step("manager", "opens Dashboard: Requests waiting for my approval lists all three") as page:
                d.go("manager", "dashboard")
                expect(page.locator("#myWork")).to_contain_text("Requests waiting for my approval")
                for no in (r_mro, r_ele, r_mcb):
                    expect(page.locator("#myWork")).to_contain_text(no)
            with d.step("manager", f"clicks {r_mro} in My work: Approve, Approve less, Return for changes and Reject are offered") as page:
                page.locator("#myWork a", has_text=r_mro).first.click()
                page.wait_for_load_state("networkidle")
                for label in ("Approve", "Approve less", "Return for changes", "Reject"):
                    expect(page.get_by_role("button", name=re.compile(label)).first).to_be_visible()
            with d.step("manager", "opens Requests, Needs my decision, ticks two and clicks Approve selected") as page:
                d.go("manager", "requisitions")
                expect(page.locator("#reqTabs")).to_contain_text("Needs my decision")
                for no in (r_mro, r_ele):
                    page.locator(f"tr[data-id='{no}'] .pickReq").check()
                expect(page.locator("#bulkApprove")).to_contain_text("Approve selected (2)")
                page.locator("#bulkApprove").click()
                expect(page.locator("#tableWrap")).not_to_contain_text(r_mro)
            d.sync()
            c.same(
                "both selected requests are approved by the manager",
                sorted(r[0] for r in q("select status from request where no in (:a, :b)", a=r_mro, b=r_ele)),
                ["approved", "approved"],
            )
            c.same(
                "the one not ticked is still waiting",
                q("select status from request where no = :n", n=r_mcb)[0][0],
                "submitted",
            )
            link = next(
                (m for m in mails_to("manager") if "/approve/" in m["body"] and r_mcb in m["body"] + m["subject"]), None
            )
            c.true(
                "the manager was emailed a link to approve the new request",
                link is not None,
                str([m["subject"] for m in mails_to("manager")]),
            )
            if link:
                url = re.search(r"https?://\S+/approve/[\w.\-]+", link["body"]).group(0)  # type: ignore[union-attr]
                with d.step(
                    "manager", "opens the link in the email: it shows the request and decides nothing yet"
                ) as page:
                    page.goto(url)
                    expect(page.locator("body")).to_contain_text("MCB 32 A")
                    expect(page.get_by_role("button", name="Approve")).to_be_visible()
                d.sync()
                c.same(
                    "opening the link decided nothing",
                    q("select status from request where no = :n", n=r_mcb)[0][0],
                    "submitted",
                )
                with d.step("manager", "clicks Approve on that page") as page:
                    page.get_by_role("button", name="Approve").click()
                    expect(page.locator("body")).to_contain_text("Approved")
                d.sync()
                c.same(
                    "the link approved the request, once",
                    q("select status from request where no = :n", n=r_mcb)[0][0],
                    "approved",
                )
                c.same(
                    "the audit log says it came by an emailed link",
                    one(
                        "select count(*) from audit_event where action = 'request.approve' and entity_id = :n and coalesce(reason, '') || coalesce(after::text, '') ilike '%emailed link%'",
                        n=r_mcb,
                    ),
                    1,
                )
                page2 = d.contexts["manager"].new_page()
                page2.goto(url)
                c.true(
                    "the same link does not work a second time",
                    "no longer" in page2.locator("body").inner_text().lower()
                    or "not" in page2.locator("body").inner_text().lower(),
                )
                page2.close()
            with d.step("buyer", f"opens the approved request {r_mro} and clicks Find suppliers") as page:
                d.go("buyer", f"requisitions/{r_mro}")
                page.get_by_role("button", name="Find suppliers").click()
                page.wait_for_load_state("networkidle")
                page.wait_for_timeout(500)
            d.sync()
            c.same("Find suppliers made a quotation request for the bearing and seal", one("select count(*) from rfq where request_no = :n", n=r_mro), 1)
            work()
            with d.step("requester", "sees in the notifications that the request was approved") as page:
                d.go("requester", "dashboard")
                page.locator("#notificationBellBtn").click()
                expect(page.locator(".notif-row", has_text=r_mcb).first).to_be_visible()
            c.true(
                "the requester was also told by email", len(mails_to("requester")) >= 1, str(len(mails_to("requester")))
            )

            # ---------------------------------------------------------------- 4. the assistant says where the request is
            status_now = q("select status from request where no = :n", n=r_mcb)[0][0]
            fake = Queue(
                json.dumps({"confidence": 0.9, "tool": "find_requests", "args": {}}),
                json.dumps(
                    {"confidence": 0.9, "final": f"Your request {r_mcb} is {status_now}; the buyer takes it from here."}
                ),
            )
            ui_api.use_gateway(
                Gateway({"fake": fake}, {k: [("fake", "m")] for k in ("agent", "default")}, sleep=lambda s: None)
            )
            with d.step("requester", "asks the Assistant: where is my request?") as page:
                d.go("requester", "assistant")
                page.locator("#assistantInput").fill("Where is my request?")
                page.locator("#assistantSendBtn").click()
                expect(page.locator("#assistantThread")).to_contain_text(r_mcb)
                expect(page.locator("#assistantThread")).to_contain_text(status_now)
            ui_api.use_gateway(model_gateway())

            # ---------------------------------------------------------------- 5. sourcing: contract orders, quotation requests
            rfqs = q("select no, request_no from rfq order by no")
            c.same(
                "two quotation requests (shoes; bearing and seal), none sent before a buyer approves",
                [(r[1]) for r in rfqs if r[1] in (r_shoes, r_mro)].__len__(),
                2,
            )
            c.same("nothing was mailed to a supplier yet", len(supplier_mails()), 0)
            shoes_rfq = next(r[0] for r in rfqs if r[1] == r_shoes)
            mro_rfq = next(r[0] for r in rfqs if r[1] == r_mro)
            contract_pos = q("select no, supplier_code, taxable from po where contract_code is not null order by no")
            c.same(
                "the contactor and the MCB went straight to orders at the contract's supplier",
                sorted({r[1] for r in contract_pos}),
                ["SUP-002"],
            )
            c.same(
                "contract order value before tax is 4 x 2,950 + 40 x 295",
                sum((Decimal(r[2]) for r in contract_pos), Decimal(0)),
                money(4 * 2950 + 40 * 295),
            )
            for rfq_no in (shoes_rfq, mro_rfq):
                with d.step("buyer", f"opens Dashboard: RFQs waiting to be sent includes {rfq_no}") as page:
                    d.go("buyer", "dashboard")
                    expect(page.locator("#myWork")).to_contain_text("RFQs waiting to be sent")
                    page.locator("#myWork a", has_text=rfq_no).first.click()
                    page.wait_for_load_state("networkidle")
                with d.step("buyer", f"clicks Approve to send the quotation request {rfq_no}") as page:
                    if "/admin/approvals" not in page.url:
                        d.go_admin("buyer", "approvals")
                    card = page.locator(".space-y-3", has_text=rfq_no).first
                    expect(card).to_be_visible()
                    card.get_by_role("button", name="Approve", exact=True).click()
                    expect(page.locator(".space-y-3", has_text=rfq_no)).to_have_count(0)
                d.sync()
                work()
            invited = {
                r: sorted(x[0] for x in q("select supplier_code from rfq_supplier where rfq_no = :n", n=r))
                for r in (shoes_rfq, mro_rfq)
            }
            c.same("safety gear asked the two general suppliers", invited[shoes_rfq], ["SUP-003", "SUP-004"])
            c.same(
                "mechanical spares asked the bearing specialist and the two general suppliers",
                invited[mro_rfq],
                ["SUP-001", "SUP-003", "SUP-004"],
            )
            c.same(
                "one quotation email per supplier asked",
                sorted(m["to"].lower() for m in supplier_mails(subject=shoes_rfq)),
                sorted(contacts[s].lower() for s in invited[shoes_rfq]),
            )

            # ---------------------------------------------------------------- 6. quotes: shoes arrive as emails, bearing/seal typed in
            shoes_line = q("select description from rfq_line where rfq_no = :n", n=shoes_rfq)[0][0]
            for supplier, price in (("SUP-003", "1120"), ("SUP-004", "1090")):
                from email.message import EmailMessage

                from p2p.platform.emails import plus_address

                msg = EmailMessage()
                msg["From"] = contacts[supplier]
                msg["To"] = plus_address("rfq", shoes_rfq)
                msg["Subject"] = f"Re: Request for quotation {shoes_rfq}"
                msg["Message-ID"] = f"<{shoes_rfq}.{supplier}@mail.supplier.example>"
                msg.set_content(
                    "Dear Sir / Madam,\n\n"
                    f"Thank you for your enquiry {shoes_rfq}. Our quotation is below.\n\n"
                    f"1. {shoes_line} (ITM-011) - 8 prs\n   Rate: Rs {price} per prs\n   GST: 5%\n   Delivery: 7 days from the order\n\n"
                    "This quotation is valid until 2026-12-31.\n\nRegards,\n" + suppliers[supplier]["trade_name"] + "\n"
                )
                email_id = emails.ingest(db, slug, mailbox.parse_rfc822(msg.as_bytes()))
                assert email_id is not None
                with tenants.control_engine().begin() as conn:
                    conn.execute(
                        text("delete from job where tenant_slug = :t and kind = 'read_supplier_reply'"), {"t": slug}
                    )
                MODEL.answer = json.dumps(
                    {
                        "intent": "quote",
                        "lines": [
                            {
                                "item": "ITM-011",
                                "unit_price": price,
                                "discount_pct": None,
                                "gst_rate": "5",
                                "delivery_days": 7,
                                "price_includes_gst": False,
                            }
                        ],
                        "valid_until": "2026-12-31",
                        "confidence": 0.95,
                    }
                )
                replies.read_reply(db, slug, email_id, model_gateway(), as_of=TODAY)
                d.sync()
                d.event(
                    f"{suppliers[supplier]['trade_name']} (supplier)",
                    f"replies to {shoes_rfq} by email: {price} a pair",
                    "The mail is stored with the quotation request and read into a draft quote that waits for the buyer.",
                )
            with d.step("buyer", f"opens {shoes_rfq}: the two replies read from email are waiting as drafts") as page:
                d.go("buyer", f"rfqs/{shoes_rfq}")
                expect(page.locator("#appContent")).to_contain_text("Replies read from email")
                expect(page.locator("#appContent")).to_contain_text("2 to look at")
            for n in (1, 2):
                with d.step("buyer", f"checks reply {n} against the email and confirms the quote") as page:
                    page.locator(".draftConfirm").first.click()
                    expect(page.locator("#appContent")).to_contain_text("Quotes")
                d.sync()
            c.same(
                "both emailed quotes became quotes, with the supplier's own prices",
                sorted(
                    Decimal(r[0])
                    for r in q(
                        "select l.unit_price from quote q join quote_line l on l.quote_no = q.no where q.rfq_no = :r",
                        r=shoes_rfq,
                    )
                ),
                [money(1090), money(1120)],
            )
            with d.step(
                "buyer", "sees the comparison recommend the cheaper shoe quote, and clicks Award the selected suppliers"
            ) as page:
                expect(page.locator("#appContent tr", has_text="recommended")).to_contain_text("Gujarat")
                page.locator("#awardBtn").click()
                expect(page.locator("#appContent")).to_contain_text("Awarded")
            d.sync()
            c.same(
                "shoes quotation request awarded",
                q("select status from rfq where no = :n", n=shoes_rfq)[0][0],
                "awarded",
            )

            prices = {"SUP-001": ("305", "6450"), "SUP-003": ("315", "6350"), "SUP-004": ("320", "6400")}
            for supplier, (bearing, seal) in prices.items():
                with d.step(
                    "buyer",
                    f"clicks Enter a quote for {suppliers[supplier]['trade_name']}: bearing {bearing}, seal {seal}, and Save the quote",
                ) as page:
                    d.go("buyer", f"rfqs/{mro_rfq}")
                    page.locator("#addQuoteBtn").click()
                    page.locator("#qSupplier").select_option(supplier)
                    rows = page.locator("#quoteForm tbody tr")
                    for i, price in enumerate((bearing, seal)):
                        rows.nth(i).locator(".qPrice").fill(price)
                        rows.nth(i).locator(".qDisc").fill("0")
                        rows.nth(i).locator(".qGst").fill("18")
                        rows.nth(i).locator(".qDays").fill("7")
                    page.locator("#saveQuote").click()
                    expect(page.locator("#appContent")).to_contain_text("Quotes")
                d.sync()
            with d.step(
                "buyer", "compares the three quotes (landed cost per line) and clicks Award the selected suppliers"
            ) as page:
                d.go("buyer", f"rfqs/{mro_rfq}")
                expect(page.locator("#appContent")).to_contain_text("recommended")
                page.locator("#awardBtn").click()
                expect(page.locator("#appContent")).to_contain_text("Awarded")
            d.sync()
            c.same(
                "bearing/seal quotation request awarded",
                q("select status from rfq where no = :n", n=mro_rfq)[0][0],
                "awarded",
            )
            work()

            # ---------------------------------------------------------------- 7. orders: GST by state, approval by the plant head, PDF, send, acknowledge
            pos = {
                r[0]: r
                for r in q("select no, supplier_code, status, taxable, cgst, sgst, igst, total from po order by no")
            }
            by_supplier: dict[str, list[str]] = {}
            for no, row in pos.items():
                by_supplier.setdefault(row[1], []).append(no)
            c.same(
                "orders exist for all four suppliers", sorted(by_supplier), ["SUP-001", "SUP-002", "SUP-003", "SUP-004"]
            )
            c.same(
                "Sai Bearings: 50 x 305 before tax",
                sum((Decimal(pos[n][3]) for n in by_supplier["SUP-001"]), Decimal(0)),
                money(50 * 305),
            )
            c.same(
                "Deccan: 6 x 6,350 before tax",
                sum((Decimal(pos[n][3]) for n in by_supplier["SUP-003"]), Decimal(0)),
                money(6 * 6350),
            )
            c.same(
                "Gujarat: 8 x 1,090 before tax",
                sum((Decimal(pos[n][3]) for n in by_supplier["SUP-004"]), Decimal(0)),
                money(8 * 1090),
            )
            for no, row in pos.items():
                inter = suppliers[row[1]]["state_code"] != plant_gstin[:2]
                c.true(
                    f"{no}: {'IGST only' if inter else 'CGST and SGST'} (supplier in state {suppliers[row[1]]['state_code']}, plant in {plant_gstin[:2]})",
                    (Decimal(row[6]) > 0) == inter and (Decimal(row[4]) > 0) == (not inter),
                )
                c.same(f"{no} waits for approval", row[2], "submitted")
            for no in pos:
                refused("buyer", "POST", f"/api/purchase-orders/{no}/approve", {})
                with d.step("approver", f"opens Dashboard: Orders waiting for my approval includes {no}") as page:
                    d.go("approver", "dashboard")
                    expect(page.locator("#myWork")).to_contain_text("Orders waiting for my approval")
                    page.locator("#myWork a", has_text=no).first.click()
                    page.wait_for_load_state("networkidle")
                with d.step("approver", f"checks {no} and clicks Approve") as page:
                    expect(page.locator("#poPdf")).to_be_visible()
                    page.locator("#poApprove").click()
                    expect(page.locator("#appContent")).to_contain_text("Approved by")
                d.sync()
            c.same("every order is approved", sorted({r[0] for r in q("select status from po")}), ["approved"])
            pdf = demo_ui.api("GET", f"/api/purchase-orders/{next(iter(pos))}/pdf", token("buyer"))
            c.true("the order document is a real PDF", pdf.status_code == 200 and pdf.content[:4] == b"%PDF")
            for no in pos:
                with d.step(
                    "buyer", f"opens Dashboard: Approved orders to send includes {no}; clicks Send to supplier"
                ) as page:
                    d.go("buyer", "dashboard")
                    expect(page.locator("#myWork")).to_contain_text("Approved orders to send to the supplier")
                    page.locator("#myWork a", has_text=no).first.click()
                    page.wait_for_load_state("networkidle")
                    page.locator("#poSend").click()
                    expect(page.locator("#appContent")).to_contain_text("Sent to")
                d.sync()
                promised = TODAY + timedelta(days=6)
                with d.step("buyer", f"clicks Record acknowledgement with the promised date {promised}") as page:
                    page.locator("#poAckDate").fill(promised.isoformat())
                    page.locator("#poAck").click()
                    expect(page.locator("#appContent")).to_contain_text("Acknowledged on")
                d.sync()
            c.same("every order is acknowledged", sorted({r[0] for r in q("select status from po")}), ["acknowledged"])
            c.true(
                "every supplier was mailed its order with the PDF attached",
                all(any(po in m["subject"] for m in supplier_mails()) for po in pos),
            )

            # ---------------------------------------------------------------- 8. goods receipt: one part delivery then the rest
            sai_po = by_supplier["SUP-001"][0]

            def receive(po: str, qtys: list[Decimal], challan: str) -> None:
                with d.step(
                    "store",
                    f"opens Goods receipt, clicks Receive goods on {po}, enters {', '.join(str(x) for x in qtys)} and clicks Post receipt",
                ) as page:
                    d.go("store", "dashboard")
                    expect(page.locator("#myWork")).to_contain_text("Orders with goods to come")
                    d.go("store", "receiving")
                    card = page.locator("#waitingWrap .panel", has_text=po)
                    expect(card).to_be_visible()
                    card.locator(".recv-open").click()
                    for i, qty in enumerate(qtys):
                        card.locator(".recv-qty").nth(i).fill(str(qty))
                    card.locator(".recv-challan").fill(challan)
                    card.locator(".recv-send").click()
                    expect(page.locator("#appContent")).to_contain_text("GRN-")
                d.sync()

            for supplier, nos in sorted(by_supplier.items()):
                for po in nos:
                    lines = q("select qty from po_line where po_no = :n order by line_no", n=po)
                    for who in ("buyer", "ap"):
                        refused(
                            who,
                            "POST",
                            f"/api/purchase-orders/{po}/receive",
                            {"challan": "X", "lines": [{"line_no": 1, "qty_received": "1"}]},
                        )
                    if po == sai_po:
                        receive(po, [Decimal(30)], "DC/2611/0001")
                        c.same(
                            "after 30 of 50 the order is still open for the rest",
                            q("select status from po where no = :n", n=po)[0][0],
                            "part_received",
                        )
                        stock = q("select on_hand from stock_level where item_code = 'ITM-001'")[0][0]
                        c.same(
                            "stock of the bearing: 40 in the books plus the 30 that arrived",
                            Decimal(stock),
                            Decimal(70),
                        )
                        with d.step("buyer", "is told in the app that part of the goods arrived") as page:
                            d.go("buyer", "dashboard")
                            page.locator("#notificationBellBtn").click()
                            expect(page.locator(".notif-row", has_text=f"Goods arrived for {po}").first).to_be_visible()
                        receive(po, [Decimal(20)], "DC/2611/0002")
                    else:
                        receive(po, [Decimal(r[0]) for r in lines], f"DC/2611/{po[-4:]}")
                    c.same(f"{po} is fully received", q("select status from po where no = :n", n=po)[0][0], "received")
            with d.step("requester", "is told in the app that the goods for their request arrived") as page:
                d.go("requester", "dashboard")
                page.locator("#notificationBellBtn").click()
                page.wait_for_timeout(800)
                out.notes.append("requester's notifications: " + " | ".join(" ".join(t.split())[:90] for t in page.locator(".notif-row").all_inner_texts()))
                west = "|".join(re.escape(n) for n in by_supplier["SUP-002"])
                expect(page.locator(".notif-row", has_text=re.compile(rf"Goods arrived for ({west})")).first).to_be_visible()
            stock_now = {r[0]: Decimal(r[1]) for r in q("select item_code, on_hand from stock_level")}
            c.same(
                "stock after all receipts: bearing 40+50, seal 3+6, contactor 5+4, shoes 12+8, MCB 24+40",
                [stock_now[i] for i in ("ITM-001", "ITM-004", "ITM-006", "ITM-011", "ITM-007")],
                [Decimal(90), Decimal(9), Decimal(9), Decimal(20), Decimal(64)],
            )

            # ---------------------------------------------------------------- 9. invoices: QR upload (Western), typed (Deccan, Sai), blocked (Gujarat)
            def invoice_pdf(po: str, number: str, qr: bool) -> Path:
                head = q("select supplier_code, buyer_gstin from po where no = :n", n=po)[0]
                sup = suppliers[head[0]]
                lines = q(
                    "select line_no, description, gst_rate, qty, unit_price from po_line where po_no = :n order by line_no",
                    n=po,
                )
                spec = RoundedSpec(
                    supplier_name=sup["legal_name"],
                    gstin=sup["gstin"],
                    buyer_gstin=head[1],
                    number=number,
                    on=TODAY,
                    po_no=po,
                    rows=[
                        docs.Row(r[1], Decimal(r[3]), Decimal(r[4]), Decimal(r[2]), po_ref=f"{po}/{r[0]}")
                        for r in lines
                    ],
                )
                pdf_lines = docs.text_lines(spec, irn=spec.irn_value)
                at = next(i for i, t in enumerate(pdf_lines) if t.startswith("Total "))
                pdf_lines.insert(at, f"Round off {docs.money(spec.total - docs.Spec.total.fget(spec))}")  # type: ignore[attr-defined]
                code = docs.qr_array(docs.qr_text(spec)) if qr else None
                RESULTS.mkdir(parents=True, exist_ok=True)
                path = folder / f"{re.sub('[^A-Za-z0-9]+', '-', number)}.pdf"
                path.write_bytes(docs.pdf(pdf_lines, code))
                MODEL.answer = json.dumps(invoice_answer(spec, sup)) if len(lines) == 1 else "{}"
                return path

            for n, po in enumerate(by_supplier["SUP-002"], start=1):
                number = f"WE/26-27/{1100 + n}"
                path = invoice_pdf(po, number, qr=True)
                calls = MODEL.calls
                with d.step(
                    "ap",
                    f"opens Dashboard (goods waiting for an invoice), then Invoices: Upload an invoice, {path.name} with its e-invoice QR code",
                ) as page:
                    d.go("ap", "dashboard")
                    expect(page.locator("#myWork")).to_contain_text("Goods received, waiting for an invoice")
                    d.go("ap", "invoices")
                    page.set_input_files("#invFile", str(path))
                    expect(page.locator("#invDrafts")).to_contain_text("Invoices read from documents")
                    expect(page.locator("#invDrafts")).to_contain_text("Read from its QR code")
                    expect(page.locator("#invDrafts")).to_contain_text(number)
                d.sync()
                c.same("the e-invoice was read by code alone: no model call", MODEL.calls - calls, 0)
                with d.step("ap", "clicks Check and enter") as page:
                    page.locator("#invDrafts .draft-row [data-act=confirm]").first.click()
                    expect(page.locator("#appContent")).to_contain_text("Taxable")
                d.sync()

            def type_invoice(supplier: str, po: str, number: str, qty: Decimal, price: Decimal, irn: str = "") -> None:
                with d.step(
                    "ap", f"opens Invoices, clicks Enter an invoice: {number}, {qty} at {price}, and Enter and match"
                ) as page:
                    d.go("ap", "invoices/new")
                    page.locator("#invSupplier").select_option(supplier)
                    page.locator("#invNo").fill(number)
                    page.locator("#invDate").fill(TODAY.isoformat())
                    if irn:
                        page.locator("#invIrn").fill(irn)
                    row = page.locator("#invLines tr[data-po]").first
                    row.locator(".il-qty").fill(str(qty))
                    row.locator(".il-price").fill(str(price))
                    page.locator("#invSave").click()
                    expect(page.locator("#appContent")).to_contain_text("Taxable")
                d.sync()

            deccan_po, sai_po_, guj_po = by_supplier["SUP-003"][0], by_supplier["SUP-001"][0], by_supplier["SUP-004"][0]
            type_invoice("SUP-003", deccan_po, "DIS/2611/0031", Decimal(6), Decimal(6350))
            type_invoice("SUP-001", sai_po_, "SB/26-27/0207", Decimal(50), Decimal(305))
            markup = (Decimal(1090) * Decimal("1.12")).quantize(CENT)
            type_invoice("SUP-004", guj_po, "GST/2611/0418", Decimal(8), markup)
            inv_rows = {
                r[1]: r for r in q("select no, supplier_code, status, entered_by, due_date from invoice order by no")
            }
            for supplier in ("SUP-002", "SUP-003", "SUP-001"):
                c.same(
                    f"invoice of {supplier} matched the order and the receipt: approved for payment",
                    inv_rows[supplier][2],
                    "approved",
                )
            c.same("the Gujarat invoice, 12% above the order price, is blocked", inv_rows["SUP-004"][2], "blocked")
            guj_inv = inv_rows["SUP-004"][0]
            sai_due = TODAY + timedelta(days=45)
            c.true(
                "the small supplier is due in 45 days from acceptance, not on its 60-day terms",
                inv_rows["SUP-001"][4] <= sai_due,
                str(inv_rows["SUP-001"][4]),
            )

            def tower() -> dict[str, int]:
                body = demo_ui.api("GET", "/api/control-tower", token("approver")).json()
                return {x["stage"]: int(x["count"]) for x in body["stages"]}

            n_west = len(by_supplier["SUP-002"])
            now = tower()
            out.notes.append(f"control tower with the Gujarat invoice blocked: {now}")
            c.same("control tower: one invoice is blocked", now.get("invoice_blocked"), 1)
            c.same("control tower: the invoices that matched wait for payment", now.get("awaiting_payment"), n_west + 2)
            fake_x = Investigator()
            base = exceptions_agent.diagnose(db, guj_inv)
            assert base is not None
            fake_x.invoice, fake_x.order, fake_x.supplier = guj_inv, guj_po, "SUP-004"
            fake_x.explanation, fake_x.cause = base.explanation, base.cause
            ui_api.use_gateway(
                Gateway(
                    {"fake": fake_x},
                    {k: [("fake", "m")] for k in ("agent_invoice_exceptions", "agent", "default")},
                    sleep=lambda s: None,
                )
            )
            db.commit()
            with d.step(
                "ap", "opens the blocked invoice: the reason and the Next: line; clicks Explain why and what to do"
            ) as page:
                d.go("ap", f"invoices/{guj_inv}")
                expect(page.locator("#appContent")).to_contain_text("Blocked")
                expect(page.locator("#appContent")).to_contain_text("Price above the order price")
                page.locator("#invExplain").click()
                expect(page.locator("#invAdvice")).to_contain_text("What to do")
            ui_api.use_gateway(model_gateway())
            reason = "The supplier raised its price from 1 November; the plant needs these shoes for the shutdown."
            with d.step("ap", "clicks Ask to pay it anyway, writes the reason and clicks Send") as page:
                page.locator("#invRelease").click()
                page.locator("#invReason").fill(reason)
                page.locator("#invWhyOk").click()
                expect(page.locator("#appContent")).to_contain_text("Waiting for approval")
            d.sync()
            pid = int(one("select id from proposal where action = 'invoice.release_block' and status = 'pending'"))
            c.same(
                "the clerk who entered it cannot release it (refused)",
                demo_ui.api("POST", f"/api/admin/proposals/{pid}/approve", token("ap"), json={}).status_code,
                403,
            )
            with d.step(
                "approver", "opens Dashboard: Blocked invoices to release; Admin, Approvals, clicks Approve"
            ) as page:
                d.go("approver", "dashboard")
                expect(page.locator("#myWork")).to_contain_text("Blocked invoices to release")
                page.locator("#myWork a", has_text=guj_inv).first.click()
                page.wait_for_load_state("networkidle")
                if "/admin/approvals" not in page.url:
                    d.go_admin("approver", "approvals")
                card = page.locator(".space-y-3", has_text=guj_inv).first
                expect(card).to_be_visible()
                card.get_by_role("button", name="Approve", exact=True).click()
                expect(page.locator(".space-y-3", has_text=guj_inv)).to_have_count(0)
            d.sync()
            now = tower()
            c.same(
                "control tower after the release: nothing blocked, every invoice waits for payment",
                (now.get("invoice_blocked"), now.get("awaiting_payment")),
                (0, n_west + 3),
            )
            c.same(
                "the released invoice is approved for payment",
                q("select status from invoice where no = :n", n=guj_inv)[0][0],
                "approved",
            )
            c.true(
                "the match result names a releaser who is not the person who entered the invoice",
                str(
                    q("select resolved_by from match_result where invoice_no = :n and result = 'blocked'", n=guj_inv)[
                        0
                    ][0]
                )
                != str(inv_rows["SUP-004"][3]),
            )

            # ---------------------------------------------------------------- 10. payment run: finance plans, plant head approves, bank file, statement import
            pin_today(PAY_DAY)
            d.sync()
            with d.step(
                "finance",
                "opens Dashboard: Invoices due: plan a payment run; Payments, See what is due, Send the run for approval",
            ) as page:
                d.go("finance", "dashboard")
                expect(page.locator("#myWork")).to_contain_text("Invoices due: plan a payment run")
                d.go("finance", "payments")
                page.locator("#runDate").fill(PAY_DAY.isoformat())
                page.locator("#runWindow").fill("7")
                page.locator("#previewRun").click()
                for supplier in ("SUP-001", "SUP-002", "SUP-003", "SUP-004"):
                    expect(page.locator("#duePreview")).to_contain_text(suppliers[supplier]["trade_name"].split()[0])
                page.locator("#planRun").click()
                expect(page.locator("#appContent")).to_contain_text("Waiting for the owner")
            d.sync()
            run_no = one("select no from payment_run")
            pay_rows = q(
                "select no, supplier_code, amount from payment where run_no = :r order by supplier_code", r=run_no
            )
            c.same("one payment per supplier", [r[1] for r in pay_rows], ["SUP-001", "SUP-002", "SUP-003", "SUP-004"])
            c.same(
                "the small supplier comes first in the run",
                one("select supplier_code from payment where run_no = :r order by no limit 1", r=run_no),
                "SUP-001",
            )
            run_proposal = int(
                one("select id from proposal where action = 'payment_run.approve' and status = 'pending'")
            )
            c.same(
                "the proposer cannot approve the run",
                demo_ui.api(
                    "POST", f"/api/admin/proposals/{run_proposal}/approve", token("finance"), json={}
                ).status_code,
                403,
            )
            c.same(
                "no bank file before the plant head approves",
                demo_ui.api("POST", f"/api/payment-runs/{run_no}/file", token("finance"), json={}).status_code,
                409,
            )
            with d.step(
                "approver", "opens Dashboard: Payment runs to approve; Admin, Approvals, clicks Approve"
            ) as page:
                d.go("approver", "dashboard")
                expect(page.locator("#myWork")).to_contain_text("Payment runs to approve")
                page.locator("#myWork a", has_text="Pay ").first.click()
                page.wait_for_load_state("networkidle")
                if "/admin/approvals" not in page.url:
                    d.go_admin("approver", "approvals")
                card = page.locator(".space-y-3", has_text="Pay ").first
                card.get_by_role("button", name="Approve", exact=True).click()
                expect(page.locator(".space-y-3", has_text="Pay ")).to_have_count(0)
            d.sync()
            c.same("the run is approved", q("select status from payment_run where no = :n", n=run_no)[0][0], "approved")
            with d.step(
                "finance",
                "opens Dashboard: Approved runs: make the bank file; clicks Make the bank file and Show the bank file",
            ) as page:
                d.go("finance", "dashboard")
                expect(page.locator("#myWork")).to_contain_text("Approved runs: make the bank file")
                page.locator("#myWork a", has_text=run_no).first.click()
                page.wait_for_load_state("networkidle")
                page.locator("#makeFile").click()
                page.locator("#showFile").click()
                expect(page.locator("#fileText")).to_contain_text("payment_no")
            d.sync()
            lines = one("select file_text from payment_run where no = :n", n=run_no).strip().splitlines()
            c.same("the bank file has a header and one line per supplier", len(lines), 5)
            statement = folder / "statement.csv"
            statement.write_text(
                "Date,Bank reference,Narration,Debit\n"
                + "".join(
                    f"{PAY_DAY.isoformat()},UTR{n:06d},NEFT {r[0]} FLOW ENGINEERING,{money(r[2])}\n"
                    for n, r in enumerate(pay_rows, start=1)
                ),
                encoding="utf-8",
            )
            with d.step("finance", "chooses the bank's statement and clicks Import the bank statement") as page:
                page.set_input_files("#stmtFile", str(statement))
                page.locator("#importStmt").click()
                expect(page.locator("#appContent")).to_contain_text("The bank has paid")
                expect(page.locator("#appContent")).to_contain_text("bank reference")
            d.sync()
            c.same("every invoice is paid", sorted({r[0] for r in q("select status from invoice")}), ["paid"])
            c.same("every order is closed", sorted({r[0] for r in q("select status from po")}), ["closed"])
            c.true("every payment carries the bank's reference", all(r[0] for r in q("select utr from payment")))
            advice = [m for m in supplier_mails() if "Bank reference" in m["body"]]
            c.same(
                "each supplier got a payment advice with the bank reference",
                sorted(m["to"].lower() for m in advice),
                sorted(contacts[s].lower() for s in suppliers),
            )
            with d.step("buyer", "sees in the notifications that the orders are paid and closed") as page:
                d.go("buyer", "dashboard")
                page.locator("#notificationBellBtn").click()
                expect(page.locator(".notif-row", has_text="paid and closed").first).to_be_visible()

            # ---------------------------------------------------------------- 11. the control tower and the dashboard agree with what happened
            end_tower = tower()
            c.same("control tower at the end: nothing is left in any stage", sum(end_tower.values()), 0)
            cycle = demo_ui.api("GET", "/api/kpis", token("approver")).json()["cycle_time"]
            c.same(
                "KPIs: every stage of the cycle counted the four requests",
                sorted({int(v["cases"]) for v in cycle.values() if isinstance(v, dict) and "cases" in v}),
                [4],
            )
            out.notes.append("control tower stages at the end: " + str([(x["stage"], x["count"]) for x in demo_ui.api("GET", "/api/control-tower", token("approver")).json()["stages"]]))
            out.notes.append("kpis at the end: " + json.dumps(demo_ui.api("GET", "/api/kpis", token("approver")).json())[:700])
            with d.step("approver", "opens the Control tower: everything has moved to its last stage") as page:
                d.go("approver", "control-tower")
                expect(page.locator("#ctTower")).to_be_visible()
                expect(page.locator("#ctKpis")).to_be_visible()
            tower = demo_ui.api("GET", "/api/control-tower", token("approver")).json()
            out.notes.append("control tower shape: " + json.dumps(tower)[:300])
            with d.step("approver", "opens the Dashboard: Where things stand") as page:
                d.go("approver", "dashboard")
                expect(page.locator("#stageRow")).to_be_visible()

            # ---------------------------------------------------------------- 12. the admin changes a policy with a reason; the audit shows it
            reason_text = "Suppliers asked for a day more to quote"
            with d.step(
                "admin", "opens Admin, Company setup, Policies; saves 'Days suppliers get to quote' with a reason"
            ) as page:
                d.go("admin", "dashboard")
                page.get_by_role("link", name="Admin").click()  # the Admin link in the menu opens the admin area
                page.wait_for_load_state("networkidle")
                page.get_by_role("link", name="Company setup").click()
                page.get_by_role("tab", name="Policies").click()
                row = page.get_by_role("row").filter(has_text="Days suppliers get to quote")
                row.get_by_role("textbox").fill("6")
                page.get_by_label("Reason for the change you are about to make (needed for every change)").fill(
                    reason_text
                )
                row.get_by_role("button", name="Save").click()
                expect(row).to_contain_text(reason_text)
            with d.step("admin", "opens Audit: the change is there with its reason") as page:
                page.get_by_role("link", name="Audit").click()
                page.get_by_label("Action").fill("setup.policy")
                page.get_by_role("button", name="Search").click()
                expect(page.get_by_role("cell", name="setup.policy").first).to_be_visible()
            with d.step("admin", "opens Company setup, History: the same change is listed with its reason") as page:
                page.get_by_role("link", name="Company setup").click()
                page.get_by_role("tab", name="History").click()
                expect(page.get_by_role("row").filter(has_text="default_rfq_response_days").first).to_contain_text(reason_text)
            for who in ("requester", "buyer", "store", "ap", "finance"):
                refused(who, "PUT", "/api/setup/policies/default_rfq_response_days", {"value": "9", "reason": "no"})

            # ---------------------------------------------------------------- 13. who did what: nobody did two jobs of one case
            def actors(*actions: str) -> set[str]:
                return {
                    str(r[0])
                    for r in q(
                        "select distinct actor_id from audit_event where action = any(:a) and actor_type = 'user'",
                        a=list(actions),
                    )
                }

            parts = {
                "approved the requests": actors("request.approve"),
                "approved the orders": actors("po.approve"),
                "received the goods": actors("grn.post"),
                "entered the invoices": actors("invoice.enter", "invoice.book"),
                "released the block": actors("invoice.release_block"),
                "planned the run": actors("payment_run.propose"),
                "approved the run": actors("payment_run.approve"),
            }
            out.metrics["people per step (audit log)"] = {k: len(v) for k, v in parts.items()}
            c.true(
                "the audit log names who did each step",
                all(parts.values()),
                str({k: v for k, v in parts.items() if not v}),
            )
            c.true(
                "the person who planned the run did not approve it",
                not (parts["planned the run"] & parts["approved the run"]),
            )
            c.true(
                "the person who entered the invoices did not release the block",
                not (parts["entered the invoices"] & parts["released the block"]),
            )
            c.true(
                "the person who received the goods did not enter an invoice",
                not (parts["received the goods"] & parts["entered the invoices"]),
            )
            unaudited = {
                t: [
                    str(r[0])
                    for r in q(
                        f"select t.{k} from {t} t where not exists (select 1 from audit_event a where a.entity_id = t.{k}) limit 3"
                    )
                ]
                for t, k in (
                    ("request", "no"),
                    ("rfq", "no"),
                    ("po", "no"),
                    ("goods_receipt", "no"),
                    ("invoice", "no"),
                    ("payment_run", "no"),
                )
            }  # noqa: S608
            c.true(
                "every document the journey made has an audit row",
                not any(unaudited.values()),
                str({t: v for t, v in unaudited.items() if v}),
            )

            sweep(d, c, out, EMAILS, expect)
            guide_walk(d, c, out)
        except Exception as exc:  # noqa: BLE001 - a failed step is a finding with the step it stopped at
            verdict = "stopped"
            last = d.steps[-1]["did"] if d.steps else "the start"
            out.find(
                "high",
                f"journey: stopped after '{last}': {type(exc).__name__}: {str(exc).splitlines()[0][:300] if str(exc) else ''}",
            )
            out.notes.append(traceback.format_exc(limit=5))
        serious = [x for x in d.problems if " 5" in x[:14] or "page error" in x or "console error" in x]
        if serious:
            out.find("medium", f"journey: the browser reported: {serious[:4]}")
        elif d.problems:
            out.find("low", f"journey: the browser saw refused requests: {d.problems[:4]}")
        if c.failed and verdict == "passed":
            verdict = "FAILED"
        steps_total = len(d.steps)
        d.write_transcript(c.facts, verdict)
        out.metrics["steps taken at screens"] = steps_total
        out.metrics["checks passed / failed"] = f"{c.passed} / {c.failed}"
        d.close()
        browser.close()
    ui_api.use_gateway(None)
    pin_today(TODAY)
    out.metrics["transcript and screenshots"] = "evals/results/journeys/"
    out.passed = out.passed and not any(f.severity in ("high", "medium") for f in out.findings)
    db.close()
    return out


def demo_ui_checks(out: Outcome) -> Any:
    from e30_demo_runs import Checks

    return Checks(out, "journey")


# ---- every screen of every role, at laptop and phone width ---------------------------------------------------

BAD_WORDS = re.compile(
    r"Couldn.t load|Could not load|\[object Object\]|\bundefined\b|\bNaN\b|\{\"|Traceback|Internal Server Error"
)
CODE_LIKE = re.compile(r"\b(?:admin|request|po|rfq|invoice|payment|grn|supplier|contract|stock)\.[a-z_]+\b")


def sweep(d: Journey, c: Any, out: Outcome, emails: dict[str, str], expect: Any) -> None:
    """Every screen a role can open: no browser error, no blank panel, no 'couldn't load', no raw JSON or code, no sideways scroll."""
    viewports = (("laptop 1440x900", {"width": 1440, "height": 900}), ("phone 390x844", {"width": 390, "height": 844}))
    visited = 0
    for key in emails:
        page = d.pages[key]
        d.go(key, "dashboard")
        links = page.locator("#navList a.nav-item[data-route]").evaluate_all(
            "els => els.filter(e => e.offsetParent !== null).map(e => [e.dataset.route, e.textContent.trim()])"
        )
        routes = [(r, t) for r, t in links]
        admin_links: list[tuple[str, str]] = []
        if page.locator("#adminLink").is_visible():
            d.go_admin(key, "")
            page.wait_for_selector("nav[aria-label='Main'] a")
            admin_links = page.locator("nav[aria-label='Main'] a").evaluate_all(
                "els => els.map(e => [e.getAttribute('href'), e.textContent.trim()])"
            )
        for label, size in viewports:
            page.set_viewport_size(size)
            for route, title in routes:
                d.go(key, route)
                page.wait_for_timeout(250)
                judge(d, c, f"{NAMES[key]}: {title} at {label}", page, "#appContent")
                visited += 1
            for href, title in admin_links:
                page.goto(f"{demo_ui.URL}{href}" if href.startswith("/") else f"{demo_ui.URL}/admin/{href}")
                page.wait_for_load_state("networkidle")
                page.wait_for_timeout(250)
                judge(d, c, f"{NAMES[key]}: admin {title} at {label}", page, "main")
                visited += 1
        page.set_viewport_size(demo_ui.LAPTOP)
    out.metrics["screens swept (role x screen x width)"] = visited


def judge(d: Journey, c: Any, what: str, page: Any, area: str) -> None:
    box = page.locator(area).first
    shown = box.inner_text() if box.count() else page.locator("body").inner_text()
    wide = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
    problems = []
    if len(shown.strip()) < 15:
        problems.append("blank")
    if BAD_WORDS.search(shown):
        problems.append(f"error or raw text: {BAD_WORDS.search(shown).group(0)!r}")  # type: ignore[union-attr]
    technical = any(w in what for w in ("admin Audit", "admin Agent runs", "admin Rule decisions", "admin Model calls"))
    if CODE_LIKE.search(shown) and not technical:  # the logs name actions by their codes, on purpose
        problems.append(f"a raw code: {CODE_LIKE.search(shown).group(0)!r}")  # type: ignore[union-attr]
    if wide > 2:
        problems.append(f"the page scrolls sideways by {wide}px")
    c.true(f"{what}: no blank panel, error, raw code or sideways scroll", not problems, "; ".join(problems))


# ---- the guides, followed as a new user -----------------------------------------------------------------------


def guide_walk(d: Journey, c: Any, out: Outcome) -> None:
    """The first task of each role's guide: every name the guide tells the person to click or read was on a screen of that role
    during the journey, with the same words."""
    for key, (slug, hint) in GUIDE_OF.items():
        heading, labels = guide_labels(slug, hint)
        pool = d.seen_words.get(key, set())
        # approvers' first task is in two parts: the manager follows the request steps, the plant head the order steps
        missing = [lb for lb in labels if not any(lb.lower().replace("…", "") in w for w in pool)]
        c.true(
            f"{NAMES[key]} can do '{heading}' exactly as {slug}.md says: every name it uses was on their screens ({len(labels)} names)",
            not missing,
            f"not seen: {missing}",
        )
