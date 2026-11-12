"""E34: the whole application, as a person meets it: the main demo company (Apex Pumps, `apex`), its nine logins, a real
browser, the demo script followed to the letter from "Check stock levels" to a paid invoice, then every role guide followed as a brand-new
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
DOMAIN = "apexpumps.example"
EMAILS = {  # key -> login; the key is the part before the @
    key: f"{key}@{DOMAIN}"
    for key in (
        "production", "planthead", "director", "purchasemanager", "buyer", "store", "ap", "finance", "admin",
    )
}  # fmt: skip
NAMES = {
    "production": "Production Manager",
    "planthead": "Plant Head",
    "director": "Director",
    "purchasemanager": "Purchase Manager",
    "buyer": "Buyer",
    "store": "Storekeeper",
    "ap": "AP Executive",
    "finance": "Finance Manager",
    "admin": "System Administrator",
}
GUIDE_OF = {  # key -> (guide, the heading of the first task that role does from it); the director only uses a link
    "production": ("requester", ""),
    "planthead": ("approver", "Approve a request"),
    "purchasemanager": ("approver", "Approve an order"),
    "buyer": ("buyer", ""),
    "store": ("store", ""),
    "ap": ("ap-clerk", ""),
    "finance": ("finance", ""),
    "admin": ("admin", ""),
}
WANTED = [  # what the production manager asks for on the form: item, quantity, why
    ("RM-08", 120, "Winding wire for the December pumps"),
    ("RM-07", 120, "Seals for the 1 HP pumps"),
    ("RM-03", 200, "Impellers for the 1 HP pumps"),
]
BELT_REPLY = ("SUP-006", "205")  # the only spares supplier answers the V-belt quotation request by email
SEAL_QUOTES = {"SUP-002": "94", "SUP-001": "99", "SUP-003": "97"}  # typed in by the buyer
COPPER_QUOTES = {"SUP-003": "905", "SUP-005": "915", "SUP-001": "930"}
ASKED = 3  # suppliers asked for a quotation (the platform's default `rfq_supplier_count`)


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
    env.forget_tenants("e34")  # left by an earlier run that stopped half way
    try:
        return _run(ctx)
    finally:
        env.forget_tenants("e34")


def _run(ctx: Any) -> Outcome:
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
    made = demo.reset_company(demo.company("apex"), folder / "dataset", credentials=None, slug=slug)
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
    prices = lib.rows("master", "price_record", dataset)
    rules = lib.rows("master", "approval_rule", dataset)
    names_by_designation = {u["designation"]: u["name"] for u in lib.rows("master", "user", dataset)}
    approved_for = lib.rows("master", "supplier_category", dataset)
    plan = {r["item_code"]: r for r in lib.rows("master", "item_location", dataset)}
    on_hand = {r["item_code"]: Decimal(r["qty"]) for r in lib.rows("master", "stock_opening", dataset)}
    for m in lib.rows("history", "stock_movement", dataset):  # the stock history, replayed
        sign = 1 if m["type"] in ("receipt", "transfer_in", "adjust") else -1
        on_hand[m["item_code"]] = on_hand.get(m["item_code"], Decimal(0)) + sign * Decimal(m["qty"])
    belts_qty = Decimal(plan["SP-01"]["max_qty"]) - on_hand["SP-01"]  # up to the maximum

    def chain(document: str, value: Decimal) -> str:
        for r in rules:
            if r["document"] == document and Decimal(r["min_value"]) <= value <= Decimal(r["max_value"]):
                return r["approver_chain"]
        return "?"

    def rate(item: str) -> Decimal:
        it = items[item]
        changed = it["gst_rate_change_date"] and date.fromisoformat(it["gst_rate_change_date"]) <= TODAY
        return Decimal(it["gst_rate_after"] if changed else it["gst_rate_before"])

    def asked_for(category: str, codes: list[str]) -> list[str]:
        """Suppliers approved for the category, those with prices for the items first, then by code."""
        approved = sorted(s["supplier_code"] for s in approved_for if s["category_code"] == category)
        priced = {s: sum(1 for p in prices if p["supplier_code"] == s and p["item_code"] in codes) for s in approved}
        return sorted(sorted(approved, key=lambda s: (-priced[s], s))[:ASKED])

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
                d.sign_in(
                    key, EMAILS[key], f"{names_by_designation.get(NAMES[key], NAMES[key])}, {NAMES[key]} ({key}@)"
                )

            # ---------------------------------------------------------------- 1. the buyer checks the stock levels
            with d.step("buyer", "opens Requests: nothing yet, and says what to do next") as page:
                d.go("buyer", "requisitions")
                expect(page.locator("#tableWrap")).to_contain_text("No requests yet")
                expect(page.locator("#tableWrap")).to_contain_text("Next:")
            belts_value = money(belts_qty * Decimal(items["SP-01"]["standard_price"]))
            with d.step("buyer", "clicks Check stock levels: a preview, nothing is created yet") as page:
                page.locator("#replenishBtn").click()
                panel = page.locator("#replenishPanel")
                expect(panel).to_contain_text(
                    re.compile(
                        rf"1 item at or below their reorder point would become\s+1 request, about ₹{rupees(belts_value)[:-3]}"
                    )
                )
                expect(panel).to_contain_text("1 approves automatically")
            c.same("no request exists after the preview", one("select count(*) from request"), 0)
            with d.step("buyer", "clicks Create and submit") as page:
                page.locator("#createDraftsBtn").click()
                expect(page.locator("#replenishPanel")).to_contain_text(
                    "1 approved automatically, 0 waiting for approval"
                )
            r_belts, belts_status, belts_total = q("select no, status, total_value from request")[0]
            c.same(
                f"the V-belt request is {belts_qty} up to the maximum at the item price, and approves itself (under Rs 10,000)",
                (money(belts_total), belts_status),
                (belts_value, "approved"),
            )

            # ---------------------------------------------------------------- 2. the production manager raises three requests on the form
            made: dict[str, str] = {}
            for item, qty, why in WANTED:
                name = items[item]["name"]
                value = money(Decimal(qty) * Decimal(items[item]["standard_price"]))
                waits = names_by_designation["Director" if value > 100000 else "Plant Head"]
                with d.step("production", "opens Requests and clicks New request") as page:
                    d.go("production", "requisitions")
                    page.get_by_role("link", name="New request").click()
                    expect(page.get_by_role("heading", name="New request")).to_be_visible()
                with d.step("production", f"chooses {name}, {qty}, and clicks Save as draft") as page:
                    page.locator(".req-item").first.select_option(item)
                    page.locator(".req-qty").first.fill(str(qty))
                    page.locator("#reqWhy").fill(why)
                    page.locator("#reqSave").click()
                    expect(page.locator("#appContent")).to_contain_text(name)
                    expect(page.locator("#appContent")).to_contain_text("Draft")
                d.sync()
                made[item] = one("select no from request where no <> all(:known)", known=[r_belts, *made.values()])
                c.same(
                    f"the form made a draft of {name}",
                    q("select status from request where no = :n", n=made[item])[0][0],
                    "draft",
                )
                with d.step(
                    "production", f"checks the line and clicks Submit for approval: it waits for {waits}"
                ) as page:
                    page.get_by_role("button", name="Submit for approval").click()
                    expect(page.locator("#appContent")).to_contain_text(f"Waiting for {waits}")
                d.sync()
                c.same(
                    f"{made[item]} of {value} waits for {waits} (the dataset's rule for that value)",
                    q("select status, total_value from request where no = :n", n=made[item])[0],
                    ("submitted", value),
                )
            r_copper, r_seal, r_imp = made["RM-08"], made["RM-07"], made["RM-03"]
            for who in ("production", "buyer", "admin", "director", "purchasemanager"):
                refused(who, "POST", f"/api/requisitions/{r_seal}/approve", {})
            work()

            # ---------------------------------------------------------------- 3. the plant head approves two at once; the director by emailed link
            with d.step("planthead", "opens Dashboard: Requests waiting for my approval lists both") as page:
                d.go("planthead", "dashboard")
                expect(page.locator("#myWork")).to_contain_text("Requests waiting for my approval")
                for no in (r_seal, r_imp):
                    expect(page.locator("#myWork")).to_contain_text(no)
            with d.step(
                "planthead",
                f"clicks {r_seal} in My work: Approve, Approve less, Return for changes and Reject are offered",
            ) as page:
                page.locator("#myWork a", has_text=r_seal).first.click()
                page.wait_for_load_state("networkidle")
                for label in ("Approve", "Approve less", "Return for changes", "Reject"):
                    expect(page.get_by_role("button", name=re.compile(label)).first).to_be_visible()
            with d.step(
                "planthead", "opens Requests, Needs my decision, ticks both and clicks Approve selected"
            ) as page:
                d.go("planthead", "requisitions")
                expect(page.locator("#reqTabs")).to_contain_text("Needs my decision")
                for no in (r_seal, r_imp):
                    page.locator(f"tr[data-id='{no}'] .pickReq").check()
                expect(page.locator("#bulkApprove")).to_contain_text("Approve selected (2)")
                page.locator("#bulkApprove").click()
                for no in (r_seal, r_imp):  # nothing is left to decide, so the list shows them approved
                    expect(page.locator(f"#tableWrap tr[data-id='{no}']")).to_contain_text("Approved")
            d.sync()
            c.same(
                "both selected requests are approved by the plant head",
                sorted(r[0] for r in q("select status from request where no in (:a, :b)", a=r_seal, b=r_imp)),
                ["approved", "approved"],
            )
            c.same(
                "the copper wire request is still waiting for the director",
                q("select status from request where no = :n", n=r_copper)[0][0],
                "submitted",
            )
            link = next(
                (m for m in mails_to("director") if "/approve/" in m["body"] and r_copper in m["body"] + m["subject"]),
                None,
            )
            c.true(
                "the director was emailed a link to approve the copper wire request",
                link is not None,
                str([m["subject"] for m in mails_to("director")]),
            )
            if link:
                url = re.search(r"https?://\S+/approve/[\w.\-]+", link["body"]).group(0)  # type: ignore[union-attr]
                with d.step(
                    "director", "opens the link in the email: it shows the request and decides nothing yet"
                ) as page:
                    page.goto(url)
                    expect(page.locator("body")).to_contain_text(items["RM-08"]["name"])
                    expect(page.get_by_role("button", name="Approve")).to_be_visible()
                d.sync()
                c.same(
                    "opening the link decided nothing",
                    q("select status from request where no = :n", n=r_copper)[0][0],
                    "submitted",
                )
                with d.step("director", "clicks Approve on that page") as page:
                    page.get_by_role("button", name="Approve").click()
                    expect(page.locator("body")).to_contain_text("Approved")
                d.sync()
                c.same(
                    "the link approved the request, once",
                    q("select status from request where no = :n", n=r_copper)[0][0],
                    "approved",
                )
                c.same(
                    "the audit log says it came by an emailed link",
                    one(
                        "select count(*) from audit_event where action = 'request.approve' and entity_id = :n and coalesce(reason, '') || coalesce(after::text, '') ilike '%emailed link%'",
                        n=r_copper,
                    ),
                    1,
                )
                page2 = d.contexts["director"].new_page()
                page2.goto(url)
                c.true(
                    "the same link does not work a second time",
                    "no longer" in page2.locator("body").inner_text().lower()
                    or "not" in page2.locator("body").inner_text().lower(),
                )
                page2.close()
            with d.step("buyer", f"opens the approved request {r_seal} and clicks Find suppliers") as page:
                d.go("buyer", f"requisitions/{r_seal}")
                page.get_by_role("button", name="Find suppliers").click()
                page.wait_for_load_state("networkidle")
                page.wait_for_timeout(500)
            d.sync()
            c.same(
                "Find suppliers made a quotation request for the seals",
                one("select count(*) from rfq where request_no = :n", n=r_seal),
                1,
            )
            work()
            with d.step("production", "sees in the notifications that the copper wire request was approved") as page:
                d.go("production", "dashboard")
                page.locator("#notificationBellBtn").click()
                expect(page.locator(".notif-row", has_text=r_copper).first).to_be_visible()
            c.true(
                "the production manager was also told by email",
                len(mails_to("production")) >= 1,
                str(len(mails_to("production"))),
            )

            # ---------------------------------------------------------------- 4. the assistant says where the request is
            status_now = q("select status from request where no = :n", n=r_copper)[0][0]
            fake = Queue(
                json.dumps({"confidence": 0.9, "tool": "find_requests", "args": {}}),
                json.dumps(
                    {
                        "confidence": 0.9,
                        "final": f"Your request {r_copper} is {status_now}; the buyer takes it from here.",
                    }
                ),
            )
            ui_api.use_gateway(
                Gateway({"fake": fake}, {k: [("fake", "m")] for k in ("agent", "default")}, sleep=lambda s: None)
            )
            with d.step("production", "asks the Assistant: where is my request?") as page:
                d.go("production", "assistant")
                page.locator("#assistantInput").fill("Where is my request?")
                page.locator("#assistantSendBtn").click()
                expect(page.locator("#assistantThread")).to_contain_text(r_copper)
                expect(page.locator("#assistantThread")).to_contain_text(status_now)
            ui_api.use_gateway(model_gateway())

            # ---------------------------------------------------------------- 5. sourcing: a contract order, three quotation requests
            rfq_of = {r[1]: r[0] for r in q("select no, request_no from rfq order by no")}
            c.same(
                "three quotation requests (V-belts, seals, copper wire), none sent before a buyer approves",
                sorted(rfq_of),
                sorted([r_belts, r_seal, r_copper]),
            )
            c.same("nothing was mailed to a supplier yet", len(supplier_mails()), 0)
            belts_rfq, seal_rfq, copper_rfq = rfq_of.get(r_belts, ""), rfq_of.get(r_seal, ""), rfq_of.get(r_copper, "")
            contract = next(p for p in prices if p["item_code"] == "RM-03" and p["contract_code"])
            contract_pos = q("select no, supplier_code, taxable, status from po where contract_code is not null")
            c.same(
                "the impellers went straight to an order on the castings contract, approved by the rule (under Rs 1,00,000)",
                [(r[1], money(r[2]), r[3]) for r in contract_pos],
                [(contract["supplier_code"], money(200 * Decimal(contract["price"])), "approved")],
            )
            for rfq_no in (belts_rfq, seal_rfq, copper_rfq):
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
                for r in (belts_rfq, seal_rfq, copper_rfq)
            }
            for rfq_no, category, wanted in (
                (belts_rfq, "MRO", ["SP-01"]),
                (seal_rfq, "RAW", ["RM-07"]),
                (copper_rfq, "RAW", ["RM-08"]),
            ):
                c.same(f"{rfq_no} asked the suppliers the dataset gives", invited[rfq_no], asked_for(category, wanted))
            c.same(
                "one quotation email per supplier asked",
                sorted(m["to"].lower() for m in supplier_mails(subject=seal_rfq)),
                sorted(contacts[s].lower() for s in invited[seal_rfq]),
            )

            # ---------------------------------------------------------------- 6. quotes: the belts by email, the rest typed in
            belts_line = q("select description from rfq_line where rfq_no = :n", n=belts_rfq)[0][0]
            belt_supplier, belt_price = BELT_REPLY
            from email.message import EmailMessage

            from p2p.platform.emails import plus_address

            msg = EmailMessage()
            msg["From"] = contacts[belt_supplier]
            msg["To"] = plus_address("rfq", belts_rfq)
            msg["Subject"] = f"Re: Request for quotation {belts_rfq}"
            msg["Message-ID"] = f"<{belts_rfq}.{belt_supplier}@mail.supplier.example>"
            msg.set_content(
                "Dear Sir / Madam,\n\n"
                f"Thank you for your enquiry {belts_rfq}. Our quotation is below.\n\n"
                f"1. {belts_line} (SP-01) - {belts_qty} nos\n   Rate: Rs {belt_price} per nos\n   GST: 18%\n"
                "   Delivery: 5 days from the order\n\n"
                "This quotation is valid until 2026-12-31.\n\nRegards,\n"
                + suppliers[belt_supplier]["trade_name"]
                + "\n"
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
                            "item": "SP-01",
                            "unit_price": belt_price,
                            "discount_pct": None,
                            "gst_rate": "18",
                            "delivery_days": 5,
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
                f"{suppliers[belt_supplier]['trade_name']} (supplier)",
                f"replies to {belts_rfq} by email: {belt_price} a belt",
                "The mail is stored with the quotation request and read into a draft quote that waits for the buyer.",
            )
            with d.step("buyer", f"opens {belts_rfq}: the reply read from email is waiting as a draft") as page:
                d.go("buyer", f"rfqs/{belts_rfq}")
                expect(page.locator("#appContent")).to_contain_text("Replies read from email")
                expect(page.locator("#appContent")).to_contain_text("1 to look at")
            with d.step("buyer", "checks the reply against the email and confirms the quote") as page:
                page.locator(".draftConfirm").first.click()
                expect(page.locator("#appContent")).to_contain_text("Quotes")
            d.sync()
            c.same(
                "the emailed quote became a quote, with the supplier's own price",
                [
                    money(r[0])
                    for r in q(
                        "select l.unit_price from quote q join quote_line l on l.quote_no = q.no where q.rfq_no = :r",
                        r=belts_rfq,
                    )
                ],
                [money(belt_price)],
            )
            with d.step(
                "buyer",
                "sees one quote where two are wanted: writes the reason and clicks Select these suppliers",
            ) as page:
                page.locator("#awardReason").fill("The only approved supplier of spares; the line needs the belts")
                page.locator("#awardBtn").click()
                expect(page.locator("#appContent")).to_contain_text("Awarded")
            d.sync()
            c.same(
                "V-belt quotation request awarded",
                q("select status from rfq where no = :n", n=belts_rfq)[0][0],
                "awarded",
            )

            for rfq_no, offers in ((seal_rfq, SEAL_QUOTES), (copper_rfq, COPPER_QUOTES)):
                item = "RM-07" if rfq_no == seal_rfq else "RM-08"
                for supplier, price in offers.items():
                    with d.step(
                        "buyer",
                        f"clicks Enter a quote for {suppliers[supplier]['trade_name']}: {price}, and Save the quote",
                    ) as page:
                        d.go("buyer", f"rfqs/{rfq_no}")
                        page.locator("#addQuoteBtn").click()
                        page.locator("#qSupplier").select_option(supplier)
                        row = page.locator("#quoteForm tbody tr").first
                        row.locator(".qPrice").fill(price)
                        row.locator(".qDisc").fill("0")
                        row.locator(".qGst").fill(str(rate(item)))
                        row.locator(".qDays").fill("7")
                        page.locator("#saveQuote").click()
                        expect(page.locator("#appContent")).to_contain_text("Quotes")
                    d.sync()
                cheapest = min(offers, key=lambda s: Decimal(offers[s]))
                with d.step(
                    "buyer",
                    f"sees {suppliers[cheapest]['trade_name']} recommended (the lowest total cost) and clicks Select these suppliers",
                ) as page:
                    d.go("buyer", f"rfqs/{rfq_no}")
                    expect(page.locator("#appContent tr", has_text="recommended")).to_contain_text(
                        suppliers[cheapest]["trade_name"].split()[0]
                    )
                    page.locator("#awardBtn").click()
                    expect(page.locator("#appContent")).to_contain_text("Awarded")
                d.sync()
                c.same(f"{rfq_no} awarded", q("select status from rfq where no = :n", n=rfq_no)[0][0], "awarded")
            work()

            # ---------------------------------------------------------------- 7. orders: GST by state, the purchase manager approves the large one
            pos = {
                r[0]: r
                for r in q("select no, supplier_code, status, taxable, cgst, sgst, igst, total from po order by no")
            }
            by_supplier: dict[str, list[str]] = {}
            for no, row in pos.items():
                by_supplier.setdefault(row[1], []).append(no)
            seal_winner = min(SEAL_QUOTES, key=lambda s: Decimal(SEAL_QUOTES[s]))
            copper_winner = min(COPPER_QUOTES, key=lambda s: Decimal(COPPER_QUOTES[s]))
            expected = {
                contract["supplier_code"]: ("RM-03", Decimal(200), Decimal(contract["price"])),
                belt_supplier: ("SP-01", belts_qty, Decimal(belt_price)),
                seal_winner: ("RM-07", Decimal(120), Decimal(SEAL_QUOTES[seal_winner])),
                copper_winner: ("RM-08", Decimal(120), Decimal(COPPER_QUOTES[copper_winner])),
            }
            c.same("one order per supplier awarded", sorted(by_supplier), sorted(expected))
            for supplier, (item, qty, price) in expected.items():
                taxable = money(qty * price)
                c.same(
                    f"{suppliers[supplier]['trade_name']}: {qty} x {price} before tax",
                    sum((Decimal(pos[n][3]) for n in by_supplier.get(supplier, [])), Decimal(0)),
                    taxable,
                )
                band = chain("po", taxable)
                for n in by_supplier.get(supplier, []):
                    c.same(
                        f"{n} of {taxable}: {'approved by the rule' if band == 'auto' else 'waits for the purchase manager'}",
                        pos[n][2],
                        "approved" if band == "auto" else "submitted",
                    )
            for no, row in pos.items():
                inter = suppliers[row[1]]["state_code"] != plant_gstin[:2]
                c.true(
                    f"{no}: {'IGST only' if inter else 'CGST and SGST'} (supplier in state {suppliers[row[1]]['state_code']}, plant in {plant_gstin[:2]})",
                    (Decimal(row[6]) > 0) == inter and (Decimal(row[4]) > 0) == (not inter),
                )
            for no in [n for n, row in pos.items() if row[2] == "submitted"]:
                refused("buyer", "POST", f"/api/purchase-orders/{no}/approve", {})
                with d.step(
                    "purchasemanager", f"opens Dashboard: Orders waiting for my approval includes {no}"
                ) as page:
                    d.go("purchasemanager", "dashboard")
                    expect(page.locator("#myWork")).to_contain_text("Orders waiting for my approval")
                    page.locator("#myWork a", has_text=no).first.click()
                    page.wait_for_load_state("networkidle")
                with d.step("purchasemanager", f"checks {no} and clicks Approve") as page:
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

            # ---------------------------------------------------------------- 8. goods receipt: a part delivery, then the rest; the seals are inspected
            imp_po = by_supplier[contract["supplier_code"]][0]
            seal_po = by_supplier[seal_winner][0]

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
                    if po == imp_po:
                        receive(po, [Decimal(120)], "DC/2611/0001")
                        c.same(
                            "after 120 of 200 the order is still open for the rest",
                            q("select status from po where no = :n", n=po)[0][0],
                            "part_received",
                        )
                        c.same(
                            "stock of the impellers: what the books had plus the 120 that arrived",
                            Decimal(q("select on_hand from stock_level where item_code = 'RM-03'")[0][0]),
                            on_hand["RM-03"] + 120,
                        )
                        with d.step("buyer", "is told in the app that part of the goods arrived") as page:
                            d.go("buyer", "dashboard")
                            page.locator("#notificationBellBtn").click()
                            expect(page.locator(".notif-row", has_text=f"Goods arrived for {po}").first).to_be_visible()
                        receive(po, [Decimal(80)], "DC/2611/0002")
                    else:
                        receive(po, [Decimal(r[0]) for r in lines], f"DC/2611/{po[-4:]}")
                    if po == seal_po:
                        c.same(
                            "the seals wait for inspection: nothing counts yet",
                            q("select status from po where no = :n", n=po)[0][0],
                            "acknowledged",
                        )
                        with d.step(
                            "store",
                            "sees the receipt waiting for inspection; keeps every seal and clicks Record inspection",
                        ) as page:
                            expect(page.locator("#appContent")).to_contain_text("waiting for inspection")
                            page.locator("#inspectBtn").click()
                            expect(page.locator("#appContent")).to_contain_text("inspection passed")
                        d.sync()
                    c.same(f"{po} is fully received", q("select status from po where no = :n", n=po)[0][0], "received")
            with d.step("production", "is told in the app that the goods for their request arrived") as page:
                d.go("production", "dashboard")
                page.locator("#notificationBellBtn").click()
                page.wait_for_timeout(800)
                expect(page.locator(".notif-row", has_text=re.compile(r"Goods arrived for")).first).to_be_visible()
            stock_now = {r[0]: Decimal(r[1]) for r in q("select item_code, on_hand from stock_level")}
            c.same(
                "stock after all receipts: what the books had plus what each order brought",
                {i: stock_now[i] for i, _, _ in expected.values()},
                {i: on_hand[i] + qty for i, qty, _ in expected.values()},
            )

            # ---------------------------------------------------------------- 9. invoices: QR upload, typed, and one blocked
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

            e_invoicing = [s for s in by_supplier if suppliers[s]["e_invoice_applicable"] == "true"]
            c.true("one supplier awarded issues e-invoices", len(e_invoicing) == 1, str(e_invoicing))
            for n, po in enumerate(by_supplier[e_invoicing[0]] if e_invoicing else [], start=1):
                number = f"SW/26-27/{1100 + n}"
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

            def type_invoice(supplier: str, po: str, number: str, qty: Decimal, price: Decimal) -> None:
                with d.step(
                    "ap", f"opens Invoices, clicks Enter an invoice: {number}, {qty} at {price}, and Enter and match"
                ) as page:
                    d.go("ap", "invoices/new")
                    page.locator("#invSupplier").select_option(supplier)
                    page.locator("#invNo").fill(number)
                    page.locator("#invDate").fill(TODAY.isoformat())
                    row = page.locator("#invLines tr[data-po]").first
                    row.locator(".il-qty").fill(str(qty))
                    row.locator(".il-price").fill(str(price))
                    page.locator("#invSave").click()
                    expect(page.locator("#appContent")).to_contain_text("Taxable")
                d.sync()

            type_invoice(contract["supplier_code"], imp_po, "DC/2611/0207", Decimal(200), Decimal(contract["price"]))
            type_invoice(belt_supplier, by_supplier[belt_supplier][0], "WIS/2611/0031", belts_qty, Decimal(belt_price))
            markup = (Decimal(SEAL_QUOTES[seal_winner]) * Decimal("1.12")).quantize(CENT)
            type_invoice(seal_winner, seal_po, "GBS/2611/0418", Decimal(120), markup)
            inv_rows = {
                r[1]: r for r in q("select no, supplier_code, status, entered_by, due_date from invoice order by no")
            }
            for supplier in (contract["supplier_code"], belt_supplier, *e_invoicing):
                c.same(
                    f"invoice of {supplier} matched the order and the receipt: approved for payment",
                    inv_rows[supplier][2],
                    "approved",
                )
            c.same(
                f"the {seal_winner} invoice, 12% above the order price, is blocked", inv_rows[seal_winner][2], "blocked"
            )
            seal_inv = inv_rows[seal_winner][0]
            micro = suppliers[seal_winner]
            c.true(
                "the micro supplier is due within the days agreed in writing (at most 45 from acceptance)",
                inv_rows[seal_winner][4] is None
                or inv_rows[seal_winner][4] <= TODAY + timedelta(days=min(45, int(micro["agreed_payment_days"] or 45))),
                str(inv_rows[seal_winner][4]),
            )

            def tower() -> dict[str, int]:
                body = demo_ui.api("GET", "/api/control-tower", token("director")).json()
                return {x["stage"]: int(x["count"]) for x in body["stages"]}

            now = tower()
            out.notes.append(f"control tower with the seal invoice blocked: {now}")
            c.same("control tower: one invoice is blocked", now.get("invoice_blocked"), 1)
            c.same("control tower: the invoices that matched wait for payment", now.get("awaiting_payment"), 3)
            fake_x = Investigator()
            base = exceptions_agent.diagnose(db, seal_inv)
            assert base is not None
            fake_x.invoice, fake_x.order, fake_x.supplier = seal_inv, seal_po, seal_winner
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
                d.go("ap", f"invoices/{seal_inv}")
                expect(page.locator("#appContent")).to_contain_text("Blocked")
                expect(page.locator("#appContent")).to_contain_text("Price above the order price")
                page.locator("#invExplain").click()
                expect(page.locator("#invAdvice")).to_contain_text("What to do")
            ui_api.use_gateway(model_gateway())
            reason = "The supplier raised its price from 1 November; the line needs these seals this month."
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
                "purchasemanager", "opens Dashboard: Blocked invoices to release; Admin, Approvals, clicks Approve"
            ) as page:
                d.go("purchasemanager", "dashboard")
                expect(page.locator("#myWork")).to_contain_text("Blocked invoices to release")
                page.locator("#myWork a", has_text=seal_inv).first.click()
                page.wait_for_load_state("networkidle")
                if "/admin/approvals" not in page.url:
                    d.go_admin("purchasemanager", "approvals")
                card = page.locator(".space-y-3", has_text=seal_inv).first
                expect(card).to_be_visible()
                card.get_by_role("button", name="Approve", exact=True).click()
                expect(page.locator(".space-y-3", has_text=seal_inv)).to_have_count(0)
            d.sync()
            now = tower()
            c.same(
                "control tower after the release: nothing blocked, every invoice waits for payment",
                (now.get("invoice_blocked"), now.get("awaiting_payment")),
                (0, 4),
            )
            c.same(
                "the released invoice is approved for payment",
                q("select status from invoice where no = :n", n=seal_inv)[0][0],
                "approved",
            )
            c.true(
                "the match result names a releaser who is not the person who entered the invoice",
                str(
                    q("select resolved_by from match_result where invoice_no = :n and result = 'blocked'", n=seal_inv)[
                        0
                    ][0]
                )
                != str(inv_rows[seal_winner][3]),
            )

            # ---------------------------------------------------------------- 10. payment run: finance plans, the director approves, bank file, statement
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
                for supplier in by_supplier:
                    expect(page.locator("#duePreview")).to_contain_text(suppliers[supplier]["trade_name"].split()[0])
                page.locator("#planRun").click()
                expect(page.locator("#appContent")).to_contain_text("Waiting for the owner")
            d.sync()
            run_no = one("select no from payment_run")
            pay_rows = q(
                "select no, supplier_code, amount from payment where run_no = :r order by supplier_code", r=run_no
            )
            c.same("one payment per supplier", [r[1] for r in pay_rows], sorted(by_supplier))
            c.same(
                "the micro supplier comes first in the run",
                one("select supplier_code from payment where run_no = :r order by no limit 1", r=run_no),
                seal_winner,
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
                "no bank file before the director approves",
                demo_ui.api("POST", f"/api/payment-runs/{run_no}/file", token("finance"), json={}).status_code,
                409,
            )
            with d.step(
                "director", "opens Dashboard: Payment runs to approve; Admin, Approvals, clicks Approve"
            ) as page:
                d.go("director", "dashboard")
                expect(page.locator("#myWork")).to_contain_text("Payment runs to approve")
                page.locator("#myWork a", has_text="Pay ").first.click()
                page.wait_for_load_state("networkidle")
                if "/admin/approvals" not in page.url:
                    d.go_admin("director", "approvals")
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
            c.same("the bank file has a header and one line per supplier", len(lines), 1 + len(by_supplier))
            statement = folder / "statement.csv"
            statement.write_text(
                "Date,Bank reference,Narration,Debit\n"
                + "".join(
                    f"{PAY_DAY.isoformat()},UTR{n:06d},NEFT {r[0]} APEX PUMPS,{money(r[2])}\n"
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
                "each supplier paid got a payment advice with the bank reference",
                sorted(m["to"].lower() for m in advice),
                sorted(contacts[s].lower() for s in by_supplier),
            )
            with d.step("buyer", "sees in the notifications that the orders are paid and closed") as page:
                d.go("buyer", "dashboard")
                page.locator("#notificationBellBtn").click()
                expect(page.locator(".notif-row", has_text="paid and closed").first).to_be_visible()

            # ---------------------------------------------------------------- 11. the control tower and the dashboard agree with what happened
            end_tower = tower()
            c.same("control tower at the end: nothing is left in any stage", sum(end_tower.values()), 0)
            cycle = demo_ui.api("GET", "/api/kpis", token("director")).json()["cycle_time"]
            c.same(
                "KPIs: every stage of the cycle counted the four requests",
                sorted({int(v["cases"]) for v in cycle.values() if isinstance(v, dict) and "cases" in v}),
                [4],
            )
            out.notes.append(
                "kpis at the end: " + json.dumps(demo_ui.api("GET", "/api/kpis", token("director")).json())[:700]
            )
            with d.step("director", "opens the Control tower: everything has moved to its last stage") as page:
                d.go("director", "control-tower")
                expect(page.locator("#ctTower")).to_be_visible()
                expect(page.locator("#ctKpis")).to_be_visible()
            with d.step("director", "opens the Dashboard: Where things stand") as page:
                d.go("director", "dashboard")
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
                expect(page.get_by_role("row").filter(has_text="default_rfq_response_days").first).to_contain_text(
                    reason_text
                )
            for who in ("production", "buyer", "store", "ap", "finance"):
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
