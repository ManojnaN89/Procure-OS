"""E30: scripted demo runs through the real screens, each person in their own browser, checked against answers worked
out here from the raw dataset (the main demo company, Apex Pumps; see `scenario()` for the three items it plans by stock
level for these runs). The plan's three scenarios (a normal run, a part delivery, a price mismatch on the invoice)
each run twice: with everything typed in at the screens, and with documents (the supplier's quotation arrives as an email
and the supplier's invoice is uploaded as a PDF). A held order (a quoted price above the estimate) is a fourth
scenario."""

import hashlib
import json
import os
import re
import sys
import traceback
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from fractions import Fraction
from functools import partial
from math import ceil
from pathlib import Path
from typing import Any

import lib
from demo_ui import RESULTS, Demo, Site, api
from e04_gst import half_up, oracle_line, oracle_net
from lib import D, Outcome
from sqlalchemy import text

sys.path.insert(0, str(lib.ROOT / "tests"))
import invoice_documents as docs  # noqa: E402

ID = "E30"
TITLE = (
    "Demo runs through the real screens: a normal run, a part delivery and a price mismatch on the invoice, each typed in "
    "and with documents, and a held order"
)
REQUIREMENT = (
    "Plan P1-25: the flow from stock level to a paid and closed order works for real people at real screens: the right "
    "person sees the right work, every money figure equals an independent calculation from the dataset, a document "
    "(an emailed quotation, an uploaded invoice) leaves the same figures as typing them in, and the separation of duties "
    "holds at every step"
)

TODAY = lib.TODAY
SERVICE_LEVEL_NOTE = "stock at its reorder point"

# Apex plans its raw materials by production order, not by stock level, so for these runs the eval gives three of them a
# min/max plan in a copy of the dataset, with the stock at the maximum, and takes the shaft off its contract, so that it is
# bought by quotation and estimated at its standard price (no price was ever paid in these runs' company). Everything the
# runs check is then worked out from that copy's files.
PLANS = {  # item -> (reorder point, maximum): a quotation item, a contract item, and the item quoted dear
    "RM-09": ("10", "160"),
    "RM-03": ("20", "60"),
    "RM-05": ("10", "50"),
}
DEAR = ("RM-05", "225.00", "240.00")  # the shaft: the best quote, 25% above its estimate of 180, and the other quote
QUOTE_WINNER, WIN_PRICE, WIN_DISCOUNT, OTHER_PRICE = "SUP-003", "88", "2", "92"
SCENARIO: Path | None = None


def scenario() -> Path:
    """The copy of the dataset these runs use (made once per run of the eval)."""
    import csv
    import shutil
    import tempfile

    global SCENARIO
    if SCENARIO is not None:
        return SCENARIO
    folder = Path(tempfile.mkdtemp(prefix="e30_")) / lib.DATASET.name
    shutil.copytree(lib.DATASET, folder, ignore=shutil.ignore_patterns("documents"))
    master = folder / "master"

    def rewrite(name: str, change: Any) -> None:
        with (master / f"{name}.csv").open(encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            fields, table = list(reader.fieldnames or []), list(reader)
        change(table)
        with (master / f"{name}.csv").open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
            writer.writeheader()
            writer.writerows(table)

    loc = lib.location()

    def plans(table: list[dict[str, str]]) -> None:
        table[:] = [r for r in table if r["item_code"] not in PLANS]
        for item, (point, top) in PLANS.items():
            table.append({"item_code": item, "location_code": loc, "reorder_policy": "minmax", "reorder_point": point,
                          "safety_stock": "0", "min_qty": point, "max_qty": top, "order_qty": "",
                          "service_level_pct": "95", "lead_days_override": ""})  # fmt: skip

    def full(table: list[dict[str, str]]) -> None:  # every planned item starts at its maximum
        top = {r["item_code"]: r["max_qty"] for r in rows("master", "item_location") if r["reorder_policy"] == "minmax"}
        for r in table:
            if r["item_code"] in top:
                r["qty"] = top[r["item_code"]]

    def dear(table: list[dict[str, str]]) -> None:  # the shaft is no longer on contract
        table[:] = [r for r in table if not (r["item_code"] == DEAR[0] and r["contract_code"])]

    SCENARIO = folder
    rewrite("item_location", plans)
    rewrite("stock_opening", full)
    rewrite("price_record", dear)
    return folder


def rows(folder: str, name: str) -> list[dict[str, str]]:
    """A file of the dataset copy these runs use."""
    return lib.rows(folder, name, scenario())


def po_route(taxable: Decimal, on: date = TODAY) -> str:
    """The approval chain the rule table gives an order of this value ('auto' needs no person)."""
    for r in rows("master", "approval_rule"):
        if r["document"] == "po" and D(r["min_value"]) <= taxable <= D(r["max_value"]):
            return r["approver_chain"]
    raise AssertionError(f"no approval rule for an order of {taxable}")


def person(key: str) -> str:
    """The name of the person of the organisation behind a key."""
    return next(u["name"] for u in rows("master", "user") if u["code"] == PEOPLE[key][0])


# ---- the answers, worked out from the raw dataset -------------------------------------------------


def csv_items() -> dict[str, dict[str, str]]:
    return {r["code"]: r for r in rows("master", "item")}


def csv_suppliers() -> dict[str, dict[str, str]]:
    return {r["code"]: r for r in rows("master", "supplier")}


def csv_locations() -> dict[str, dict[str, str]]:
    return {r["code"]: r for r in rows("master", "location")}


def in_force(r: dict[str, str], on: date) -> bool:
    start = date.fromisoformat(r["valid_from"])
    end = date.fromisoformat(r["valid_to"]) if r["valid_to"] else None
    return start <= on and (end is None or on <= end)


def contract_prices(item: str, qty: Decimal, on: date = TODAY) -> list[dict[str, str]]:
    """Price records of an active contract that cover the item and quantity on the date (price breaks by quantity)."""
    contracts = {c["code"]: c for c in rows("master", "contract") if c["status"] == "active" and in_force(c, on)}
    suppliers = csv_suppliers()
    return [
        p
        for p in rows("master", "price_record")
        if p["item_code"] == item
        and p["contract_code"] in contracts
        and in_force(p, on)
        and D(p["min_qty"]) <= qty
        and suppliers[p["supplier_code"]]["status"] == "active"
    ]


def item_rate(item: str, on: date = TODAY) -> Decimal:
    """The GST rate of an item on a date: the old rate until its change date, the new one from then on."""
    it = csv_items()[item]
    changed = date.fromisoformat(it["gst_rate_change_date"]) if it["gst_rate_change_date"] else None
    after = it["gst_rate_after"]
    return D(after if changed and on >= changed and after else it["gst_rate_before"])


def request_route(value: Decimal, on: date = TODAY) -> str:
    """The approval chain the rule table gives a request of this value (the rule in force on the date)."""
    for r in rows("master", "approval_rule"):
        if r["document"] != "request":
            continue
        end = date.fromisoformat(r["effective_to"]) if r["effective_to"] else None
        if (
            D(r["min_value"]) <= value <= D(r["max_value"])
            and date.fromisoformat(r["effective_from"]) <= on
            and (end is None or on <= end)
        ):
            return r["approver_chain"]
    raise AssertionError(f"no approval rule for a request of {value}")


def due_date(supplier: dict[str, str], invoice_date: date, accepted_on: date) -> date:
    """Payment terms from their baseline; a micro or small supplier is paid within what was agreed in writing, at most 45
    days from acceptance, and within 15 days when nothing was agreed."""
    terms = {t["code"]: t for t in rows("master", "payment_terms")}[supplier["payment_terms_code"]]
    base = invoice_date if terms["baseline"] == "invoice_date" else accepted_on
    due = base + timedelta(days=int(terms["days"]))
    if supplier["msme_category"] in ("micro", "small"):
        if supplier["msme_agreement"] == "true":
            return min(
                due,
                accepted_on + timedelta(days=min(45, int(supplier["agreed_payment_days"] or 45))),
            )
        return accepted_on + timedelta(days=15)
    return due


def tolerance(key: str, category: str) -> Decimal:
    table = rows("master", "tolerance")
    exact = [t for t in table if t["key"] == key and t["category_code"] == category]
    any_ = [t for t in table if t["key"] == key and t["category_code"] == "ANY"]
    return D((exact or any_)[0]["value"])


def money(value: Any) -> str:
    """An amount as the screens show it: Indian digit grouping, paise only when there are some."""
    whole, frac = f"{abs(Decimal(str(value))):.2f}".split(".")
    head, tail, groups = whole[:-3], whole[-3:], []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    shown = ",".join([*groups, tail]) if len(whole) > 3 else whole
    return shown + ("" if frac == "00" else "." + frac)


def to_rupee(x: Decimal) -> Decimal:
    """An invoice's total is rounded to the whole rupee (the round-off line every Indian invoice carries)."""
    return Decimal(int(Fraction(x) + Fraction(1, 2)))


def supplier_mails(d: Demo, **where: str) -> list[dict[str, Any]]:
    """The mails that went to a supplier's contacts (the rest are notices to our own people)."""
    contacts = {r["email"].lower() for r in rows("master", "supplier_contact") if r["email"]}
    return [m for m in d.outbox.sent if m["to"].lower() in contacts and all(v in m[k] for k, v in where.items())]


# ---- a company whose people are the dataset's people ----------------------------------------------

PEOPLE = {  # key -> (organisation code, what to call them on the transcript)
    "buyer": ("U03", "Neha Kulkarni, Buyer"),
    "store": ("U02", "Sunil Pawar, Storekeeper"),
    "ap": ("U04", "Priya Shah, AP Executive"),
    "finance": ("U05", "Anil Mehta, Finance Manager"),
    "stores_head": ("U07", "Vikram Joshi, Plant Head (approves requests up to Rs 1,00,000)"),
    "proc_head": ("U08", "Sanjay Rao, Purchase Manager"),
    "owner": ("U09", "Meera Iyer, Director"),
    "plant_head": ("U07", "Vikram Joshi, Plant Head"),
}


@dataclass
class Company:
    world: Any
    ids: dict[str, int]  # key -> login id
    emails: dict[str, str]
    org: dict[str, str]  # key -> organisation code


def company(prefix: str) -> Company:
    """The dataset's masters, no stock history, and a login for each person the runs need: the buyer, storekeeper and
    accounts clerk take over the generic logins; the approvers already have logins; the finance manager also gets the
    finance role."""
    from p2p.platform import users

    w = lib.build_world(prefix, with_stock=False, dataset=scenario())
    db = w.db
    ids: dict[str, int] = {}
    emails: dict[str, str] = {}
    for key, (code, label) in PEOPLE.items():
        generic = {"buyer": "Buyer", "store": "Store", "ap": "AP", "finance": "Finance"}.get(key)
        if generic:
            emails[key] = w.emails[generic]
            ids[key] = w.logins[generic]
            db.execute(
                text("update org_user set email = :e where code = :c"),
                {"e": emails[key], "c": code},
            )
        else:
            ids[key] = w.org_logins[code]
            emails[key] = str(db.execute(text("select email from org_user where code = :c"), {"c": code}).scalar_one())
        db.execute(
            text("update user_profile set display_name = :n where user_id = :i"),
            {"n": label.split(",")[0], "i": ids[key]},
        )
    # the company opens on the dashboard rather than the full-screen assistant (a setting of the company)
    db.execute(
        text(
            "insert into app_setting (key, value) values ('launch_in_focus_mode', 'false') "
            "on conflict (key) do update set value = 'false'"
        )
    )
    db.commit()
    users.grant_role(w.slug, ids["finance"], "Finance")
    return Company(w, ids, emails, {k: v[0] for k, v in PEOPLE.items()})


def lower_stock_to_reorder_point(db: Any, item: str, loc: str) -> Decimal:
    """Consumption takes the item down to its reorder point: it is the only stock that needs ordering."""
    row = db.execute(
        text(
            "select so.qty, il.reorder_point from stock_opening so join item_location il on il.item_code = so.item_code "
            "and il.location_code = so.location_code where so.item_code = :i and so.location_code = :l"
        ),
        {"i": item, "l": loc},
    ).one()
    used = D(row[0]) - D(row[1])
    db.execute(
        text(
            "insert into stock_movement (id, item_code, location_code, date, type, qty, source) "
            "values (:id, :i, :l, :d, 'issue', :q, 'import')"
        ),
        {
            "id": f"E30-{item}-{loc}",
            "i": item,
            "l": loc,
            "d": TODAY - timedelta(days=2),
            "q": used,
        },
    )
    db.commit()
    return D(row[1])


@dataclass
class Pick:
    item: str
    loc: str
    name: str
    category: str
    reorder_point: Decimal
    max_qty: Decimal
    moq: Decimal
    price: Decimal  # the estimate the request is made at: the contract price in force, else the standard price
    qty: Decimal = Decimal(0)
    value: Decimal = Decimal(0)
    notes: dict[str, Any] = field(default_factory=dict)


def candidates() -> list[Pick]:
    """Items whose stock plan is min/max and which are bought and stocked in the same unit."""
    items = csv_items()
    out = []
    for il in rows("master", "item_location"):
        it = items.get(il["item_code"])
        if (
            il["reorder_policy"] != "minmax"
            or it is None
            or it["active"] != "true"
            or it["stocked"] != "true"
            or it["purchase_uom"] != it["base_uom"]
            or D(it["pack_size"], "1") != 1
        ):
            continue
        p = Pick(
            il["item_code"], il["location_code"], it["name"], it["category_code"],
            D(il["reorder_point"]), D(il["max_qty"]), D(it["moq"]), D(it["standard_price"]),
        )  # fmt: skip
        # what the stock plan asks for from the reorder point: up to the maximum, never below the minimum order
        p.qty = max(p.moq, Decimal(ceil(p.max_qty - p.reorder_point)))
        # plan step 3: the contract price for the quantity, else the last price paid (none: the runs' company has no
        # orders before them), else the standard price
        prices = contract_prices(p.item, p.qty)
        if prices:
            best = min(prices, key=lambda x: D(x["price"]))
            p.price, p.notes["estimate"] = D(best["price"]), f"contract {best['contract_code']}"
        else:
            p.notes["estimate"] = "standard price"
        p.value = half_up(Fraction(p.qty) * Fraction(p.price), 2)
        out.append(p)
    return sorted(out, key=lambda p: (p.item, p.loc))


def pick_for(kind: str) -> Pick:
    """The first item (by code) that suits the run. 'quotation': no contract price, a manager approves the request.
    'contract': a contract price within the price band, small enough to approve itself. 'dear': the shaft, off contract,
    whose quotes come in well above its estimate. Contract suppliers are ones that charge GST (not on the composition
    scheme)."""
    suppliers = csv_suppliers()
    for p in candidates():
        prices = contract_prices(p.item, p.qty)
        if kind == "dear":
            if p.item == DEAR[0] and not prices:
                return p
            continue
        if p.item == DEAR[0]:
            continue  # kept for the held run
        if kind == "quotation":
            if not prices and request_route(p.value) == "plant_head":
                return p
            continue
        if not prices:
            continue
        best = min(prices, key=lambda x: D(x["price"]))
        if suppliers[best["supplier_code"]]["gst_reg_type"] != "regular":
            continue
        ratio = D(best["price"]) / p.price
        small = D("6000") <= p.value <= D("15000")
        if kind == "contract" and ratio <= D("1.10") and small:
            p.notes.update(
                price=D(best["price"]),
                supplier=best["supplier_code"],
                contract=best["contract_code"],
            )
            return p
    raise AssertionError(f"the dataset has no item for the '{kind}' run")


# ---- checks -----------------------------------------------------------------------------------


class Checks:
    """Each comparison is written to the transcript, and a difference is a finding."""

    def __init__(self, out: Outcome, run: str) -> None:
        self.out, self.run, self.facts, self.passed, self.failed = out, run, [], 0, 0

    def same(self, label: str, platform: Any, oracle: Any) -> bool:
        ok = platform == oracle
        self.facts.append(f"{label}: {platform}" + ("" if ok else f" (the dataset's answer is {oracle})"))
        if ok:
            self.passed += 1
        else:
            self.failed += 1
            self.out.find(
                "high",
                f"{self.run}: {label}: the platform says {platform}, the dataset's answer is {oracle}",
            )
        return ok

    def true(self, label: str, condition: bool, detail: str = "") -> bool:
        self.facts.append(label + ("" if condition else f" - NOT TRUE{(': ' + detail) if detail else ''}"))
        if condition:
            self.passed += 1
        else:
            self.failed += 1
            self.out.find("high", f"{self.run}: {label}{(': ' + detail) if detail else ''}")
        return condition


def one(db: Any, sql: str, **params: Any) -> Any:
    return db.execute(text(sql), params).scalar_one()


def first(db: Any, sql: str, **params: Any) -> Any:
    return db.execute(text(sql), params).scalar()


# ---- the screens, as steps ------------------------------------------------------------------------


def expect_text(page: Any, selector: str, *parts: str) -> None:
    from playwright.sync_api import expect

    for part in parts:
        expect(page.locator(selector)).to_contain_text(part)


def my_work(d: Demo, key: str, section: str, fragment: str, *, what: str) -> Any:
    """The person opens their dashboard, sees the work waiting for them, and opens the item from there."""
    from playwright.sync_api import expect

    with d.step(key, what) as page:
        d.go(key, "dashboard")
        expect(page.locator("#myWork")).to_contain_text(section)
        link = page.locator("#myWork a", has_text=fragment).first
        expect(link).to_be_visible()
        link.click()
        page.wait_for_load_state("networkidle")
    return d.pages[key]


def nothing_in_work(d: Demo, key: str, section: str, *, what: str) -> None:
    """The dashboard of a person who must not decide this shows no such work."""
    from playwright.sync_api import expect

    with d.step(key, what) as page:
        d.go(key, "dashboard")
        expect(page.locator("#myWork")).to_be_visible()
        expect(page.locator("#myWork")).not_to_contain_text(section)


def decide_in_approvals(d: Demo, key: str, section: str, fragment: str, *, what: str) -> None:
    """From the work list to the approvals screen of the admin console, where the person approves the proposal."""
    from playwright.sync_api import expect

    my_work(
        d,
        key,
        section,
        fragment,
        what=f"finds '{fragment}' in the work waiting for them",
    )
    with d.step(key, what) as page:
        if "/admin/approvals" not in page.url:  # the list normally brings them here
            d.go_admin(key, "approvals")
        card = page.locator(".space-y-3", has_text=fragment).first
        expect(card).to_be_visible()
        card.get_by_role("button", name="Approve", exact=True).click()
        expect(page.locator(".space-y-3", has_text=fragment)).to_have_count(0)
    d.sync()


def token(co: Company, key: str) -> dict[str, str]:
    from p2p.platform.tokens import create_access_token

    role = {"finance": "Finance", "ap": "AP", "buyer": "Buyer", "store": "Store"}.get(key, "Approver")
    return {"Authorization": f"Bearer {create_access_token(co.ids[key], co.world.slug, role)}"}


def sign_in_all(d: Demo, co: Company, keys: tuple[str, ...]) -> None:
    for key in keys:
        d.sign_in(key, co.emails[key], PEOPLE[key][1])


def replenish(d: Demo, c: Checks, pick: Pick, route: str) -> str:
    """The buyer checks the stock levels, sees what would be requested and how it would be approved, and creates it."""
    from playwright.sync_api import expect

    with d.step("buyer", "checks the stock levels (a preview, nothing is created yet)") as page:
        d.go("buyer", "requisitions")
        expect(page.locator("#tableWrap")).to_contain_text("No requests yet")
        page.locator("#replenishBtn").click()
        panel = page.locator("#replenishPanel")
        expect(panel).to_contain_text("1 item at or below their reorder point would become 1 request")
        expect(panel).to_contain_text("approves automatically" if route == "auto" else "person's approval")
        expect(panel).to_contain_text(money(pick.value))
    d.sync()
    c.same(
        "no request exists after the preview",
        one(d.db, "select count(*) from request"),
        0,
    )
    with d.step("buyer", "creates the request and sends it for approval") as page:
        page.locator("#createDraftsBtn").click()
        expect(page.locator("#replenishPanel")).to_contain_text(
            "1 approved automatically, 0 waiting for approval"
            if route == "auto"
            else "0 approved automatically, 1 waiting for approval"
        )
    d.sync()
    req = str(one(d.db, "select no from request"))
    line = d.db.execute(
        text("select item_code, qty, est_price, delivery_location from request_line where request_no = :n"),
        {"n": req},
    ).one()
    c.same("the request is for the item that fell to its reorder point", line[0], pick.item)
    c.same("quantity: up to the maximum, not below the minimum order", D(line[1]), pick.qty)
    c.same(
        f"estimate: the {pick.notes['estimate']} (the contract price in force, else the standard price: nothing was paid "
        "before in this company)",
        D(line[2]),
        pick.price,
    )
    c.same(
        "the request line keeps where its estimate came from",
        one(d.db, "select est_source from request_line where request_no = :n", n=req),
        pick.notes["estimate"],
    )
    c.same(
        "the request's value",
        D(one(d.db, "select total_value from request where no = :n", n=req)),
        pick.value,
    )
    c.same(
        "how the rule table routes a request of this value",
        request_route(pick.value),
        route,
    )
    c.same("delivery place", line[3], pick.loc)
    c.same(
        "request status straight after the check",
        one(d.db, "select status from request where no = :n", n=req),
        "approved" if route == "auto" else "submitted",
    )
    return req


def check_order(
    d: Demo,
    c: Checks,
    po: str,
    supplier: str,
    pick: Pick,
    unit: Decimal,
    *,
    status: str | None = None,
) -> tuple[Decimal, Decimal, Decimal]:
    """The order's figures against the exact-fraction answer: price before tax, GST split, total. Returns the three."""
    sup, loc, qty = csv_suppliers()[supplier], pick.loc, pick.qty
    place = csv_locations()[loc]["gstin"][:2]
    taxable, cgst, sgst, igst = oracle_line(
        qty, unit, item_rate(pick.item), sup["state_code"], place, sup["gst_reg_type"]
    )
    total = taxable + cgst + sgst + igst
    h = d.db.execute(
        text("select supplier_code, taxable, cgst, sgst, igst, total, status, buyer_gstin from po where no = :n"),
        {"n": po},
    ).one()
    c.same("order: supplier", h[0], supplier)
    c.same("order: price before tax (quantity x net price)", D(h[1]), taxable)
    c.same(
        f"order: GST split (CGST, SGST, IGST: supplier in state {sup['state_code']}, delivery in {place})",
        (D(h[2]), D(h[3]), D(h[4])),
        (cgst, sgst, igst),
    )
    c.same("order: total", D(h[5]), total)
    c.same("order: our GSTIN is the delivery place's", h[7], csv_locations()[loc]["gstin"])
    if status is None:  # the rule table decides: an order within the band for no approval is approved at once
        status = "approved" if po_route(taxable) == "auto" else "submitted"
    c.same("order status", h[6], status)
    return taxable, total, igst


def approve_order(d: Demo, c: Checks, po: str, taxable: Decimal, total: Decimal, igst: Decimal) -> None:
    from playwright.sync_api import expect

    if po_route(taxable) == "auto":
        c.same(
            f"the order of {money(taxable)} is within the band that needs no approver: approved by the rule",
            one(d.db, "select status from po where no = :n", n=po),
            "approved",
        )
        refused = api("POST", f"/api/purchase-orders/{po}/approve", d.w.headers("Buyer"), json={})
        c.true(
            "the buyer cannot approve an order (refused)", refused.status_code in (403, 409), str(refused.status_code)
        )
        return
    my_work(
        d,
        "proc_head",
        "Orders waiting for my approval",
        po,
        what="finds the order in the work waiting for them",
    )
    with d.step("proc_head", "checks the order's figures and approves it") as page:
        expect_text(page, "#appContent", money(taxable), money(total))
        expect(page.locator("#appContent")).to_contain_text("IGST" if igst else "CGST")
        if igst:
            expect(page.locator("#appContent")).not_to_contain_text("CGST")
        page.locator("#poApprove").click()
        expect(page.locator("#appContent")).to_contain_text(f"Approved by {person('proc_head')} under rule")
    d.sync()
    c.same(
        "order status after approval",
        one(d.db, "select status from po where no = :n", n=po),
        "approved",
    )
    refused = api("POST", f"/api/purchase-orders/{po}/approve", d.w.headers("Buyer"), json={})
    c.true(
        "the buyer cannot approve an order (refused)",
        refused.status_code in (403, 409),
        str(refused.status_code),
    )


def send_and_acknowledge(d: Demo, c: Checks, po: str, supplier: str, total: Decimal) -> None:
    from playwright.sync_api import expect

    contact = next(
        r["email"]
        for r in rows("master", "supplier_contact")
        if r["supplier_code"] == supplier and r["is_primary"] == "true"
    )
    my_work(
        d,
        "buyer",
        "Approved orders to send to the supplier",
        po,
        what="finds the approved order to send",
    )
    with d.step("buyer", "sends the order to the supplier") as page:
        page.locator("#poSend").click()
        expect(page.locator("#appContent")).to_contain_text("Sent to")
    d.sync()
    mails = supplier_mails(d, subject=po)
    c.same(
        "the order mail goes to the supplier's main contact, once",
        [m["to"].lower() for m in mails],
        [contact.lower()],
    )
    stated = re.search(r"Order total: (.+)", mails[0]["body"]) if mails else None
    c.same(
        "the order mail states the order total",
        D(re.sub(r"[^\d.]", "", stated.group(1))) if stated else None,
        total,
    )
    promised = TODAY + timedelta(days=5)
    with d.step("buyer", f"records the supplier's acknowledgement, delivery promised {promised}") as page:
        page.locator("#poAckDate").fill(promised.isoformat())
        page.locator("#poAck").click()
        expect(page.locator("#appContent")).to_contain_text("Acknowledged on")
    d.sync()
    c.same(
        "order status after the acknowledgement",
        one(d.db, "select status from po where no = :n", n=po),
        "acknowledged",
    )


def receive_goods(d: Demo, c: Checks, co: Company, po: str, pick: Pick) -> None:
    from playwright.sync_api import expect

    with d.step(
        "store",
        "sees the order in the work waiting for them and posts the goods receipt",
    ) as page:
        d.go("store", "dashboard")
        expect(page.locator("#myWork")).to_contain_text("Orders with goods to come")
        d.go("store", "receiving")
        card = page.locator("#waitingWrap .panel", has_text=po)
        expect(card).to_be_visible()
        card.locator(".recv-open").click()
        card.locator(".recv-qty").first.fill(str(pick.qty))
        card.locator(".recv-challan").fill("DC/2611/0412")
        card.locator(".recv-send").click()
        expect(page.locator("#appContent")).to_contain_text("GRN-")
    d.sync()
    db = d.db
    c.same(
        "goods received on the order line",
        D(
            one(
                db,
                "select received_qty from po_line where po_no = :n and line_no = 1",
                n=po,
            )
        ),
        pick.qty,
    )
    c.same(
        "order status after the receipt",
        one(db, "select status from po where no = :n", n=po),
        "received",
    )
    arrived = D(
        one(
            db,
            "select coalesce(sum(qty), 0) from stock_movement where source = 'platform' and item_code = :i and location_code = :l",
            i=pick.item,
            l=pick.loc,
        )
    )
    c.same(
        "stock at the delivery place: the reorder point plus what arrived is the maximum",
        pick.reorder_point + arrived,
        pick.max_qty,
    )
    c.true(
        "the store person is not the buyer, an approver or the requester",
        co.ids["store"] not in (co.ids["buyer"], co.ids["proc_head"], co.ids["stores_head"]),
    )


def enter_invoice(
    d: Demo,
    supplier: str,
    number: str,
    qty: Decimal,
    price: Decimal | None,
    on: date = TODAY,
) -> None:
    """The accounts clerk sees the goods waiting for an invoice and types in what the supplier's invoice says."""
    from playwright.sync_api import expect

    sup = csv_suppliers()[supplier]
    irn = (
        hashlib.sha256(f"{sup['gstin']}|{number}".encode()).hexdigest() if sup["e_invoice_applicable"] == "true" else ""
    )
    what = "sees the goods waiting for an invoice and enters the supplier's invoice"
    with d.step(
        "ap",
        what + ("" if price is None else f", with a higher price ({money(price)})"),
    ) as page:
        d.go("ap", "dashboard")
        expect(page.locator("#myWork")).to_contain_text("Goods received, waiting for an invoice")
        d.go("ap", "invoices/new")
        page.locator("#invSupplier").select_option(supplier)
        page.locator("#invNo").fill(number)
        page.locator("#invDate").fill(on.isoformat())
        if irn:
            page.locator("#invIrn").fill(irn)
        row = page.locator("#invLines tr[data-po]").first
        row.locator(".il-qty").fill(str(qty))
        if price is not None:
            row.locator(".il-price").fill(str(price))
        page.locator("#invSave").click()
        expect(page.locator("#appContent")).to_contain_text("Taxable")
    d.sync()


def pay(
    d: Demo,
    c: Checks,
    co: Company,
    inv_no: str,
    supplier: str,
    owed: Decimal,
    taxable: Decimal,
    due: date,
    po: str,
) -> None:
    pay_many(d, c, co, [(inv_no, owed, taxable, due)], supplier, po)


def pay_many(
    d: Demo,
    c: Checks,
    co: Company,
    invoices: list[tuple[str, Decimal, Decimal, date]],
    supplier: str,
    po: str,
) -> None:
    """Finance plans a payment run, the owner approves it, finance makes the bank file and records the payment. Each
    invoice is (number, amount owed, price before tax, due date); a supplier is paid once, with all that is due to it."""
    from p2p.formulas.calendar import business_today, pin_today
    from playwright.sync_api import expect

    db = d.db
    invoices = sorted(invoices, key=lambda i: i[3])
    several = len(invoices) > 1
    nos = [i[0] for i in invoices]
    owed = sum((i[1] for i in invoices), Decimal(0))
    first_due, last_due = min(i[3] for i in invoices), max(i[3] for i in invoices)
    run_date = first_due - timedelta(days=7)
    if several:
        run_date = max(run_date, business_today())  # time only goes forward
    window = max(7, (last_due - run_date).days) if several else 7
    pin_today(run_date)  # the day of the payment run has come: the invoice falls due within the week
    d.sync()
    my_work(
        d,
        "finance",
        "Invoices due: plan a payment run",
        nos[0],
        what=f"on {run_date:%d %b %Y} finds the invoice that falls due in the work waiting for them",
    )
    with d.step(
        "finance",
        "previews what is due and sends a payment run for the owner's approval",
    ) as page:
        page.locator("#runDate").fill(run_date.isoformat())
        page.locator("#runWindow").fill(str(window))
        page.locator("#previewRun").click()
        for no, amount, _, _ in invoices:
            expect(page.locator("#duePreview")).to_contain_text(no)
            expect(page.locator("#duePreview")).to_contain_text(money(amount))
        page.locator("#planRun").click()
        expect(page.locator("#appContent")).to_contain_text("Waiting for the owner")
    d.sync()
    run = str(one(db, "select no from payment_run"))
    c.same(
        "payment run status",
        one(db, "select status from payment_run where no = :n", n=run),
        "proposed",
    )
    p = db.execute(text("select amount, supplier_code, tds_amount, msme_interest from payment")).one()
    c.same(
        "the payment: the invoice total less tax held back, no interest"
        + (" (one payment for all the invoices of the supplier)" if several else ""),
        (D(p[0]), p[1], D(p[2]), D(p[3])),
        (owed, supplier, Decimal(0), Decimal(0)),
    )
    proposal = int(
        one(
            db,
            "select id from proposal where action = 'payment_run.approve' and status = 'pending'",
        )
    )
    c.same(
        "the person who proposed the run cannot approve it (refused)",
        api(
            "POST",
            f"/api/admin/proposals/{proposal}/approve",
            token(co, "finance"),
            json={},
        ).status_code,
        403,
    )
    c.same(
        "no bank file before the owner approves (refused)",
        api("POST", f"/api/payment-runs/{run}/file", token(co, "finance"), json={}).status_code,
        409,
    )

    decide_in_approvals(d, "owner", "Payment runs to approve", "Pay ", what="approves the payment run")
    c.same(
        "payment run status after the owner approves",
        one(db, "select status from payment_run where no = :n", n=run),
        "approved",
    )

    my_work(
        d,
        "finance",
        "Approved runs: make the bank file",
        run,
        what="sees the approved run waiting for its bank file",
    )
    with d.step("finance", "makes the bank file for the approved run") as page:
        page.locator("#makeFile").click()
        page.locator("#showFile").click()
        expect(page.locator("#fileText")).to_contain_text("payment_no")
    d.sync()
    bank = {r["supplier_code"]: r for r in rows("master", "supplier_bank_account")}[supplier]
    lines = str(one(db, "select file_text from payment_run where no = :n", n=run)).strip().splitlines()
    c.same("the bank file has one payment line", len(lines), 2)
    f = lines[1].split(",")
    c.same(
        "bank file line: the supplier's verified account and exactly the approved amount",
        (f[2], f[3], D(f[4])),
        (bank["account_no"], bank["ifsc"], owed),
    )
    with d.step("finance", f"records that the bank has paid, on {run_date}") as page:
        page.locator("#paidOn").fill(run_date.isoformat())
        page.locator("#markPaid").click()
        # the run is drawn again without the button once it is paid ("bank reference" is also in the page's help text)
        expect(page.locator("#markPaid")).to_have_count(0)
    d.sync()
    for no, _, taxable, _ in invoices:
        c.same(
            f"invoice status{' of ' + no if several else ''}",
            one(db, "select status from invoice where no = :n", n=no),
            "paid",
        )
    c.same("order status", one(db, "select status from po where no = :n", n=po), "closed")
    for no, _, taxable, _ in invoices:
        c.same(
            f"budget actual booked{' for ' + no if several else ''}: the price before tax",
            D(
                one(
                    db,
                    "select coalesce(sum(amount), 0) from budget_actual where invoice_no = :n",
                    n=no,
                )
            ),
            taxable,
        )
    contact = next(
        r["email"]
        for r in rows("master", "supplier_contact")
        if r["supplier_code"] == supplier and r["is_primary"] == "true"
    )
    told = [m for m in supplier_mails(d) if "Bank reference" in m["body"] and m["to"].lower() == contact.lower()]
    c.same(
        "the supplier was told what was paid, with the bank reference (mails)",
        len(told),
        1,
    )
    with d.step("buyer", "sees that the order is paid and closed in the notifications") as page:
        d.go("buyer", "dashboard")
        page.locator("#notificationBellBtn").click()
        expect(page.locator(".notif-row", has_text=f"Order {po} is paid and closed")).to_be_visible()


def audit_trail(d: Demo, c: Checks, actions: tuple[str, ...]) -> None:
    """Every step that changes data left a row in the audit log."""
    d.sync()
    have = {r[0] for r in d.db.execute(text("select distinct action from audit_event"))}
    if "po.auto_approve" in have:  # an order within the band for no approver is approved by the rule
        have.add("po.approve")
    missing = [a for a in actions if a not in have]
    c.true(
        f"the audit log has a row for each of the {len(actions)} actions",
        not missing,
        f"missing: {missing}",
    )
    total = one(d.db, "select count(*) from audit_event")
    c.true(
        f"the audit log holds {total} rows in all, for {len(have)} kinds of action",
        total > 0,
    )
    ordered = d.db.execute(
        text(
            "select action, string_agg(distinct actor_type || ':' || actor_id, ',') from audit_event "
            "group by action order by min(id)"
        )
    ).all()
    c.facts.append("the audit log, in order, with who did it: " + "; ".join(f"{a} by {w}" for a, w in ordered))


def order_approver(d: Demo) -> dict[str, str]:
    """The audit action of the order's approval and who did it, when the rule named a person for it."""
    taxable = D(one(d.db, "select taxable from po"))
    return {} if po_route(taxable) == "auto" else {"po.approve": "proc_head"}


def actors_of(d: Demo, action: str) -> set[str]:
    return {
        r[0]
        for r in d.db.execute(
            text("select distinct actor_type || ':' || actor_id from audit_event where action = :a"),
            {"a": action},
        )
    }


def separation_of_duties(
    d: Demo,
    c: Checks,
    co: Company,
    duties: dict[str, dict[str, str]],
    also: dict[str, str] | None = None,
) -> None:
    """The audit log names the person who did each thing, and it is the one who was at the screen. For one case the
    requester, approver, receiver, invoice approver and payer must be different people (`duties` lists the audit actions
    of each duty and who should have done them; `also` lists other actions to check but not to hold apart)."""
    d.sync()
    held: dict[str, set[str]] = {}
    for duty, actions in duties.items():
        held[duty] = set()
        for action, key in actions.items():
            got = actors_of(d, action)
            c.same(
                f"audit: '{action}' ({duty}) was done by {PEOPLE[key][1]}",
                got,
                {f"user:{co.ids[key]}"},
            )
            held[duty] |= got
    for action, key in (also or {}).items():
        c.same(
            f"audit: '{action}' was done by {PEOPLE[key][1]}",
            actors_of(d, action),
            {f"user:{co.ids[key]}"},
        )
    names = list(held)
    clash = [
        (a, b, sorted(held[a] & held[b])) for i, a in enumerate(names) for b in names[i + 1 :] if held[a] & held[b]
    ]
    c.true(
        "separation of duties: " + ", ".join(names) + " are different people in this case",
        not clash,
        str(clash),
    )


# ---- documents: the supplier's email and the supplier's invoice as files ---------------------------


class DocModel:
    """The one model of the document runs, scripted: it answers what a careful reader would write for the document in
    front of it (the script sets `answer` just before the document is read). It is never a real model, and the platform
    still checks every value it writes against the document."""

    def __init__(self) -> None:
        self.answer = "{}"
        self.calls = 0

    def chat(
        self,
        model: str,
        messages: Any,
        *,
        temperature: float,
        max_tokens: int,
        json_output: bool,
        **kw: Any,
    ) -> Any:
        from p2p.ai_steps.adapters import RawResponse

        self.calls += 1
        return RawResponse(self.answer, 300, 120, "stop")


MODEL = DocModel()


def model_gateway() -> Any:
    from p2p.ai_steps.gateway import Gateway

    steps = ("quote_reading", "invoice_reading")
    routes = {name: [("fake", "m")] for name in steps}
    routes.update({f"{name}.strong": [("fake", "m2")] for name in steps})
    return Gateway({"fake": MODEL}, routes, sleep=lambda s: None)


def keep(d: Demo, name: str, data: bytes) -> Path:
    """The document, kept beside the transcript so it can be opened."""
    folder = RESULTS / d.name / "documents"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(data)
    return path


def slug_of(number: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", number).strip("-")


def quote_mail(
    rfq: str,
    supplier: dict[str, str],
    sender: str,
    pick: Pick,
    price: str,
    discount: str | None,
    days: int,
    valid_until: date,
) -> tuple[bytes, dict[str, Any]]:
    """The supplier's reply to a request for quotation as an email (what arrives in the mailbox), and what a careful
    reader would write down from it."""
    from email.message import EmailMessage

    from p2p.platform.emails import plus_address

    gst = item_rate(pick.item)
    body = [
        "Dear Sir / Madam,",
        "",
        f"Thank you for your enquiry {rfq}. Our quotation is below.",
        "",
        f"1. {pick.name} ({pick.item}) - {pick.qty:f} nos",
        f"   Rate: Rs {price} per nos",
    ]
    if discount:
        body.append(f"   Discount: {discount}%")
    body += [
        f"   GST: {gst:f}%",
        f"   Delivery: {days} days from the order",
        "",
        f"This quotation is valid until {valid_until.isoformat()}.",
        "",
        "Regards,",
        supplier["trade_name"],
    ]
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = plus_address("rfq", rfq)
    msg["Subject"] = f"Re: Request for quotation {rfq}"
    msg["Message-ID"] = f"<{rfq}.{supplier['code']}@mail.supplier.example>"
    msg.set_content("\n".join(body) + "\n")
    line: dict[str, Any] = {
        "item": pick.item,
        "unit_price": price,
        "discount_pct": discount,
        "gst_rate": f"{gst:f}",
        "delivery_days": days,
        "price_includes_gst": False,
    }
    answer = {
        "intent": "quote",
        "lines": [line],
        "valid_until": valid_until.isoformat(),
        "confidence": 0.95,
    }
    return msg.as_bytes(), answer


def deliver_quote(
    d: Demo,
    co: Company,
    rfq: str,
    raw: bytes,
    answer: dict[str, Any],
    name: str,
) -> int:
    """The mail reaches the mailbox and is stored, linked to its quotation request; the worker's reading job runs with the
    scripted model (the job itself would call a real one). Returns the draft's number."""
    from p2p.platform import emails, mailbox, tenants
    from p2p.sourcing import replies

    keep(d, name, raw)
    d.sync()
    email_id = emails.ingest(d.db, co.world.slug, mailbox.parse_rfc822(raw))
    assert email_id is not None
    with tenants.control_engine().begin() as conn:  # the queued reading job would use the configured model
        conn.execute(
            text("delete from job where tenant_slug = :t and kind = 'read_supplier_reply'"),
            {"t": co.world.slug},
        )
    MODEL.answer = json.dumps(answer)
    draft = replies.read_reply(d.db, co.world.slug, email_id, model_gateway(), as_of=TODAY)
    assert draft is not None
    d.sync()
    return draft


def confirm_quote_mails(d: Demo, c: Checks, rfq: str, quotes: dict[str, tuple[str, str | None]]) -> None:
    """The buyer opens the quotation request: the replies read from email wait as drafts; each is checked and confirmed."""
    from playwright.sync_api import expect

    with d.step("buyer", "opens the quotation request: the replies read from email are waiting") as page:
        d.go("buyer", f"rfqs/{rfq}")
        expect(page.locator("#appContent")).to_contain_text("Replies read from email")
        expect(page.locator("#appContent")).to_contain_text(f"{len(quotes)} to look at")
        expect(page.locator("#appContent")).to_contain_text("read as quote")
    for n in range(len(quotes)):
        with d.step(
            "buyer",
            f"checks the figures of reply {n + 1} against the supplier's email and confirms the quote",
        ) as page:
            page.locator(".draftConfirm").first.click()
            expect(page.locator("#appContent")).to_contain_text("Quotes")
        d.sync()
    got = {
        r[0]: (D(r[1]), D(r[2]))
        for r in d.db.execute(
            text(
                "select q.supplier_code, l.unit_price, l.discount_pct from quote q join quote_line l on l.quote_no = q.no "
                "where q.rfq_no = :r"
            ),
            {"r": rfq},
        )
    }
    c.same(
        "the quotes the replies made: price and discount of each supplier, as the emails say",
        got,
        {s: (D(p), D(disc or 0)) for s, (p, disc) in quotes.items()},
    )
    c.same(
        "no reply was left unread: every draft is entered",
        one(d.db, "select count(*) from quote_draft where status <> 'applied'"),
        0,
    )


class RoundedSpec(docs.Spec):
    """An invoice as an Indian supplier prints it: the total rounded to the whole rupee, with the round-off shown."""

    @property
    def total(self) -> Decimal:
        return to_rupee(docs.Spec.total.fget(self))  # type: ignore[attr-defined]


def invoice_document(
    d: Demo,
    supplier: str,
    po: str,
    pick: Pick,
    number: str,
    qty: Decimal,
    price: Decimal,
    on: date,
    *,
    qr: bool,
) -> tuple[Path, Any]:
    """The supplier's invoice as a PDF, from the order's own line: with the e-invoice QR code, or plain (the IRN printed
    when the supplier issues e-invoices)."""
    sup = csv_suppliers()[supplier]
    line = d.db.execute(
        text("select description, gst_rate from po_line where po_no = :p and line_no = 1"),
        {"p": po},
    ).one()
    spec = RoundedSpec(
        supplier_name=sup["legal_name"],
        gstin=sup["gstin"],
        buyer_gstin=csv_locations()[pick.loc]["gstin"],
        number=number,
        on=on,
        po_no=po,
        rows=[docs.Row(line[0], qty, price, D(line[1]), po_ref=f"{po}/1")],
    )
    irn = spec.irn_value if qr or sup["e_invoice_applicable"] == "true" else None
    lines = docs.text_lines(spec, irn=irn)
    at = next(i for i, text_ in enumerate(lines) if text_.startswith("Total "))
    lines.insert(
        at,
        f"Round off {docs.money(spec.total - docs.Spec.total.fget(spec))}",  # type: ignore[attr-defined]
    )
    code = docs.qr_array(docs.qr_text(spec)) if qr else None
    return keep(d, f"{slug_of(number)}.pdf", docs.pdf(lines, code)), spec


def invoice_answer(spec: Any, sup: dict[str, str]) -> dict[str, Any]:
    """What a careful reader would write down from a plain invoice page."""
    taxable, cgst, sgst, igst = spec.amounts()
    row = spec.rows[0]
    return {
        "document_type": "invoice",
        "supplier_name": spec.supplier_name,
        "supplier_gstin": spec.gstin,
        "buyer_gstin": spec.buyer_gstin,
        "invoice_number": spec.number,
        "invoice_date": spec.on.isoformat(),
        "irn": spec.irn_value if sup["e_invoice_applicable"] == "true" else None,
        "po_numbers": [spec.po_no],
        "lines": [
            {
                "description": row.description,
                "po_no": spec.po_no,
                "qty": f"{row.qty:f}",
                "unit_price": f"{row.price:.2f}",
                "gst_rate": f"{row.rate:f}",
                "taxable": f"{row.qty * row.price:.2f}",
            }
        ],
        "taxable_total": f"{taxable:.2f}",
        "cgst": f"{cgst:.2f}" if cgst else None,
        "sgst": f"{sgst:.2f}" if sgst else None,
        "igst": f"{igst:.2f}" if igst else None,
        "total": f"{spec.total:.2f}",
        "confidence": 0.95,
    }


def upload_invoice(
    d: Demo,
    c: Checks,
    supplier: str,
    po: str,
    pick: Pick,
    number: str,
    qty: Decimal,
    price: Decimal | None,
    on: date = TODAY,
    *,
    qr: bool,
) -> None:
    """The accounts clerk sees the goods waiting for an invoice and uploads the supplier's invoice as a PDF. It is read into
    a draft (by code from its QR code, or by the scripted model checked against the page); the clerk checks it and
    enters it, and from then on it is an invoice like any other."""
    from playwright.sync_api import expect

    sup = csv_suppliers()[supplier]
    unit = (
        price
        if price is not None
        else D(
            one(
                d.db,
                "select unit_price from po_line where po_no = :p and line_no = 1",
                p=po,
            )
        )
    )
    path, spec = invoice_document(d, supplier, po, pick, number, qty, unit, on, qr=qr)
    MODEL.answer = json.dumps(invoice_answer(spec, sup))
    calls = MODEL.calls
    shown = "" if price is None else f", with a higher price ({money(price)})"
    with d.step(
        "ap",
        f"sees the goods waiting for an invoice and uploads the supplier's invoice ({path.name}{shown})",
    ) as page:
        d.go("ap", "dashboard")
        expect(page.locator("#myWork")).to_contain_text("Goods received, waiting for an invoice")
        d.go("ap", "invoices")
        page.set_input_files("#invFile", str(path))
        expect(page.locator("#invDrafts")).to_contain_text("Invoices read from documents")
        expect(page.locator("#invDrafts")).to_contain_text("Read from its QR code" if qr else "Read by the assistant")
        expect(page.locator("#invDrafts")).to_contain_text(number)
    d.sync()
    draft = d.db.execute(
        text("select status, path, source, supplier_code from invoice_draft order by id desc limit 1")
    ).one()
    c.same(
        "the draft: read from "
        + ("the QR code, by code alone" if qr else "the page by the scripted model, checked against the page"),
        tuple(draft),
        ("pending", "qr" if qr else "model", "upload", supplier),
    )
    c.same(
        "calls to a model while reading: none for an e-invoice's QR code, one for a plain page",
        MODEL.calls - calls,
        0 if qr else 1,
    )
    with d.step("ap", "checks what was read against the invoice and enters it") as page:
        page.locator("#invDrafts .draft-row [data-act=confirm]").first.click()
        expect(page.locator("#appContent")).to_contain_text("Taxable")
    d.sync()
    c.same(
        "the draft is applied once the clerk has entered the invoice",
        one(d.db, "select status from invoice_draft order by id desc limit 1"),
        "applied",
    )


# a typed run leaves its invoices' figures here; the same scenario run with documents must leave the same ones
INVOICE_FIGURES: dict[str, list[tuple[Any, ...]]] = {}


def invoice_figures(d: Demo) -> list[tuple[Any, ...]]:
    d.sync()
    return [
        (r[0], D(r[1]), D(r[2]), D(r[3]), D(r[4]), D(r[5]), r[6])
        for r in d.db.execute(
            text("select status, taxable, cgst, sgst, igst, total, due_date from invoice order by no")
        )
    ]


def same_as_typed(d: Demo, c: Checks, scenario: str, typed: bool) -> None:
    """Typed runs record their invoices' figures; the same scenario run with documents must leave exactly those."""
    got = invoice_figures(d)
    if typed:
        INVOICE_FIGURES[scenario] = got
    elif scenario in INVOICE_FIGURES:
        c.same(
            "the invoices left by the documents have the same status, amounts and due dates as the ones typed in",
            got,
            INVOICE_FIGURES[scenario],
        )


# ---- run 1: straight through, by quotation --------------------------------------------------------

BUYING_KEYS = ("buyer", "stores_head", "proc_head", "store", "ap", "finance", "owner")


def type_quotes(d: Demo, pick: Pick, rfq: str, prices: dict[str, tuple[str, str | None]]) -> None:
    """The buyer opens the quotation request and types in each supplier's quote (price, discount)."""
    from playwright.sync_api import expect

    suppliers = csv_suppliers()
    with d.step("buyer", "opens the quotation request: no quotes yet") as page:
        d.go("buyer", f"rfqs/{rfq}")
        expect(page.locator("#appContent")).to_contain_text("No quotes received yet")
    for supplier, (price, discount) in prices.items():
        with d.step(
            "buyer",
            f"enters the quote of {suppliers[supplier]['trade_name']}: {price} less {discount or 0}%",
        ) as page:
            page.locator("#addQuoteBtn").click()
            page.locator("#qSupplier").select_option(supplier)
            row = page.locator("#quoteForm tbody tr").first
            row.locator(".qPrice").fill(price)
            row.locator(".qDisc").fill(discount or "0")
            row.locator(".qGst").fill(f"{item_rate(pick.item):f}")
            row.locator(".qDays").fill("5")
            page.locator("#saveQuote").click()
            expect(page.locator("#appContent")).to_contain_text("Quotes")


def quotation_to_order(
    d: Demo, c: Checks, co: Company, pick: Pick, by_documents: bool
) -> tuple[str, str, Decimal, Decimal, Decimal, Decimal]:
    """From a request that a manager approves to an order the supplier has acknowledged: no contract covers the item, so
    quotations are asked for, two suppliers quote (typed in by the buyer, or arriving as emails that are read into drafts
    the buyer confirms), the cheaper one after its discount is awarded, and the order is approved, sent and acknowledged.
    Returns the order, its supplier, the net unit price, the price before tax, the total and the IGST."""
    from playwright.sync_api import expect

    db = d.db
    with d.step("buyer", "signs in: the dashboard shows nothing waiting yet") as page:
        expect(page.locator("#myWork")).to_be_visible()
    req = replenish(d, c, pick, "plant_head")

    # the plant head (the rule's approver for a request of this value) approves
    my_work(
        d,
        "stores_head",
        "Requests waiting for my approval",
        req,
        what="finds the request in the work waiting for them",
    )
    with d.step("stores_head", "approves the request") as page:
        expect(page.locator("#appContent")).to_contain_text(req)
        page.locator("#approveBtn").click()
        expect(page.locator("#appContent")).to_contain_text(f"Approved by {person('stores_head')}")
        page.get_by_text("How this was routed").click()  # the rule is kept with the request and shown on demand
        expect(page.locator("#appContent")).to_contain_text("Approval rule REQ-02")
    d.sync()
    c.same(
        "request status",
        one(db, "select status from request where no = :n", n=req),
        "approved",
    )

    # the worker decides how to buy it: no contract covers the item, so quotations are asked for
    d.work()
    c.same(
        "nothing was sent to a supplier yet: sending needs a buyer's approval",
        len(supplier_mails(d)),
        0,
    )
    rfq = str(one(db, "select no from rfq"))
    invited = [
        r[0]
        for r in db.execute(
            text("select supplier_code from rfq_supplier where rfq_no = :n order by 1"),
            {"n": rfq},
        )
    ]
    suppliers = csv_suppliers()
    approved_for = {(r["supplier_code"], r["category_code"]) for r in rows("master", "supplier_category")}
    contacts = {r["supplier_code"]: r["email"] for r in rows("master", "supplier_contact") if r["is_primary"] == "true"}
    c.true(
        "no contract price is in force for the item, so quotations were asked for",
        not contract_prices(pick.item, pick.qty),
    )
    c.true(
        f"the suppliers asked ({', '.join(invited)}) are active, approved for {pick.category} and have an email address",
        len(invited) >= 2
        and all(
            suppliers[s]["status"] == "active" and (s, pick.category) in approved_for and s in contacts for s in invited
        ),
    )
    decide_in_approvals(
        d,
        "buyer",
        "RFQs waiting to be sent",
        rfq,
        what="approves sending the quotation request to the suppliers",
    )
    sent = supplier_mails(d, subject=rfq)
    c.same(
        "one quotation request mail per supplier asked",
        sorted(m["to"].lower() for m in sent),
        sorted(contacts[s].lower() for s in invited),
    )

    # quotes come in: the second supplier is the cheaper one after its discount
    winner = QUOTE_WINNER
    c.true(f"{winner} is among those asked", winner in invited, str(invited))
    other = next(s for s in invited if s != winner)
    prices: dict[str, tuple[str, str | None]] = {
        other: (OTHER_PRICE, None),
        winner: (WIN_PRICE, WIN_DISCOUNT),
    }
    if by_documents:
        for supplier, (price, discount) in prices.items():
            raw, answer = quote_mail(
                rfq, suppliers[supplier], contacts[supplier], pick, price, discount, 5,
                TODAY + timedelta(days=30),
            )  # fmt: skip
            deliver_quote(d, co, rfq, raw, answer, f"quote-{supplier}.eml")
            d.event(
                f"{suppliers[supplier]['trade_name']} (supplier)",
                f"replies to the quotation request by email: {price} per unit"
                + (f", {discount}% discount" if discount else ""),
                "The mail reaches the mailbox, is stored with the quotation request, and is read into a draft quote "
                "that waits for the buyer.",
            )
        confirm_quote_mails(d, c, rfq, prices)
    else:
        type_quotes(d, pick, rfq, prices)
    with d.step(
        "buyer",
        "compares the quotes: the cheaper one after discount is recommended; awards it",
    ) as page:
        expect(page.locator("#appContent")).to_contain_text("2 of")
        expect(page.locator("#appContent tr", has_text="recommended")).to_contain_text(suppliers[winner]["trade_name"])
        page.locator("#awardBtn").click()
        expect(page.locator("#appContent")).to_contain_text(f"Supplier selected: {suppliers[winner]['trade_name']}")
    d.sync()
    c.same("RFQ status", one(db, "select status from rfq where no = :n", n=rfq), "awarded")

    d.work()  # the worker makes the order from the award
    po = str(one(db, "select no from po"))
    unit = oracle_net(Decimal(WIN_PRICE), Decimal(WIN_DISCOUNT))
    taxable, total, igst = check_order(d, c, po, winner, pick, unit)
    approve_order(d, c, po, taxable, total, igst)
    send_and_acknowledge(d, c, po, winner, total)
    return po, winner, unit, taxable, total, igst


def check_booked_invoice(
    d: Demo,
    c: Checks,
    co: Company,
    number: str,
    supplier: str,
    pick: Pick,
    qty: Decimal,
    unit: Decimal,
    invoice_date: date,
    *,
    what: str = "invoice",
) -> tuple[str, Decimal, Decimal, date]:
    """The invoice the clerk entered, against what the dataset's rules give: status, price before tax, GST, the total rounded to
    the rupee, the due date from the supplier's terms, and who entered it. Returns (number, owed, price before tax, due)."""
    db = d.db
    sup = csv_suppliers()[supplier]
    inv = db.execute(
        text(
            "select no, status, taxable, cgst, sgst, igst, total, due_date, tds_amount, entered_by, round_off "
            "from invoice where supplier_invoice_no = :n"
        ),
        {"n": number},
    ).one()
    t, cg, sg, ig = oracle_line(
        qty, unit, item_rate(pick.item), sup["state_code"],
        csv_locations()[pick.loc]["gstin"][:2], sup["gst_reg_type"],
    )  # fmt: skip
    total = t + cg + sg + ig
    owed = to_rupee(total)
    c.same(
        f"{what}: status (price and quantity agree with the order and what was received)",
        inv[1],
        "approved",
    )
    c.same(
        f"{what}: price before tax and GST are the order's, the total is rounded to the rupee",
        (D(inv[2]), D(inv[3]), D(inv[4]), D(inv[5]), D(inv[6])),
        (t, cg, sg, ig, owed),
    )
    c.same(f"{what}: the round-off line", D(inv[10]), owed - total)
    accepted = first(db, "select max(accepted_on) from goods_receipt")
    due = due_date(sup, invoice_date, accepted)
    c.same(
        f"{what}: due date from the supplier's payment terms, the day the goods were accepted ({accepted})",
        inv[7],
        due,
    )
    c.same(
        f"{what}: tax held back at source (far below the yearly limit)",
        D(inv[8]),
        Decimal(0),
    )
    c.same(f"{what} was entered by the accounts clerk", inv[9], str(co.ids["ap"]))
    return str(inv[0]), owed, t, due


def run_straight_through(d: Demo, c: Checks, co: Company, pick: Pick, by_documents: bool = False) -> None:
    sign_in_all(d, co, BUYING_KEYS)
    po, winner, unit, taxable, total, igst = quotation_to_order(d, c, co, pick, by_documents)
    receive_goods(d, c, co, po, pick)

    number = "WIS/26-27/0412"
    if by_documents:
        upload_invoice(d, c, winner, po, pick, number, pick.qty, None, qr=True)
    else:
        enter_invoice(d, winner, number, pick.qty, None)
    inv_no, owed, taxable, due = check_booked_invoice(d, c, co, number, winner, pick, pick.qty, unit, TODAY)
    c.same("invoice: total of the order, to the rupee", owed, to_rupee(total))
    same_as_typed(d, c, "straight", not by_documents)

    pay(d, c, co, inv_no, winner, owed, taxable, due, po)
    audit_trail(
        d, c,
        ("request.create", "request.approve", "rfq.create", "rfq.send", "po.create", "po.approve", "po.send",
         "payment_run.propose", "payment_run.approve", "payment_run.file", "payment_run.paid", "po.close",
         *(("email.receive", "quote_draft.create", "invoice_draft.create") if by_documents else ())),
    )  # fmt: skip
    separation_of_duties(
        d, c, co,
        {
            "requester": {"request.submit": "buyer"},
            "approver": {"request.approve": "stores_head", **order_approver(d), "payment_run.approve": "owner"},
            "receiver": {"grn.post": "store"},
            "payer": {"payment_run.propose": "finance", "payment_run.paid": "finance"},
        },
        also={"rfq.send": "buyer", "quote.enter": "buyer", "rfq.award": "buyer", "po.send": "buyer", "invoice.enter": "ap", "invoice.match": "ap", "payment_run.file": "finance"},
    )  # fmt: skip


# ---- run 4: a part delivery: the second delivery completes the order, two invoices, one payment -----


def receive_part(
    d: Demo,
    c: Checks,
    po: str,
    pick: Pick,
    qty: Decimal,
    received: Decimal,
    challan: str,
) -> None:
    """The storekeeper posts a receipt for what has come so far; `received` is the total on the order line afterwards."""
    from playwright.sync_api import expect

    complete = received >= pick.qty
    with d.step(
        "store",
        f"sees the order in the work waiting for them and posts the receipt for {qty:f} of {pick.qty:f}",
    ) as page:
        d.go("store", "dashboard")
        expect(page.locator("#myWork")).to_contain_text("Orders with goods to come")
        d.go("store", "receiving")
        card = page.locator("#waitingWrap .panel", has_text=po)
        expect(card).to_be_visible()
        card.locator(".recv-open").click()
        card.locator(".recv-qty").first.fill(f"{qty:f}")
        card.locator(".recv-challan").fill(challan)
        card.locator(".recv-send").click()
        expect(page.locator("#appContent")).to_contain_text("GRN-")
    d.sync()
    db = d.db
    c.same(
        "goods received on the order line so far",
        D(
            one(
                db,
                "select received_qty from po_line where po_no = :n and line_no = 1",
                n=po,
            )
        ),
        received,
    )
    c.same(
        "order status after the receipt: " + ("complete" if complete else "part received"),
        one(db, "select status from po where no = :n", n=po),
        "received" if complete else "part_received",
    )
    arrived = D(
        one(
            db,
            "select coalesce(sum(qty), 0) from stock_movement where source = 'platform' and item_code = :i and location_code = :l",
            i=pick.item,
            l=pick.loc,
        )
    )
    c.same(
        "stock that has arrived at the delivery place equals what was received",
        arrived,
        received,
    )


def run_part_delivery(d: Demo, c: Checks, co: Company, pick: Pick, by_documents: bool = False) -> None:
    from p2p.agents import supplier_comms
    from p2p.formulas.calendar import business_today, pin_today
    from p2p.platform import alerts
    from playwright.sync_api import expect

    db = d.db
    sign_in_all(d, co, BUYING_KEYS)
    po, winner, unit, taxable, total, igst = quotation_to_order(d, c, co, pick, by_documents)
    promised = TODAY + timedelta(days=5)
    c.true("the order is bigger than one delivery", pick.qty >= 2, f"{pick.qty}")
    part1 = (pick.qty / 2).to_integral_value(rounding="ROUND_FLOOR")
    part2 = pick.qty - part1

    # the first delivery: part of the order
    receive_part(d, c, po, pick, part1, part1, "DC/2611/0518")
    c.same(
        "the order line is part received, the balance is still to come",
        one(db, "select status from po_line where po_no = :n and line_no = 1", n=po),
        "part_received",
    )
    with d.step("store", "sees the order still waiting for its balance") as page:
        d.go("store", "dashboard")
        expect(page.locator("#myWork")).to_contain_text("Orders with goods to come")
        expect(page.locator("#myWork")).to_contain_text(po)

    # the supplier's invoice for what was delivered: matched against what was received so far
    number1 = "WIS/26-27/0511"
    if by_documents:
        upload_invoice(d, c, winner, po, pick, number1, part1, None, qr=True)
    else:
        enter_invoice(d, winner, number1, part1, None)
    inv1 = check_booked_invoice(
        d, c, co, number1, winner, pick, part1, unit, TODAY, what="invoice for the first delivery"
    )  # fmt: skip

    # the promised date passes with the balance still to come: the daily jobs raise an alert and a chaser for the buyer
    late_day = promised + timedelta(days=1)
    pin_today(late_day)
    d.sync()
    chase = supplier_comms.chase_run(db, co.world.slug, as_of=business_today())
    alerts.run(db, co.world.slug, today=business_today())
    d.sync()
    d.event(
        "the daily jobs",
        f"run on {late_day:%d %b %Y}, a day after the promised date: the supplier has delivered {part1:f} of {pick.qty:f}",
        f"chaser: {chase.tasks or chase.emailed}; alert: goods on {po} are late",
    )
    c.same(
        "the balance chaser: the supplier is not emailed on its own (reminders are set to propose), a task is made for the buyer",
        (chase.emailed, chase.tasks),
        ([], [(po, "balance")]),
    )
    c.same(
        "no mail went to the supplier yet",
        len(supplier_mails(d, subject=f"Balance of purchase order {po}")),
        0,
    )
    with d.step("buyer", "sees that the balance is late: the alert and the chaser to send") as page:
        d.go("buyer", "dashboard")
        page.locator("#notificationBellBtn").click()
        expect(page.locator(".notif-row", has_text=f"Goods on {po} are 1 day(s) late")).to_be_visible()
        expect(page.locator(".notif-row", has_text="New task: Chase")).to_be_visible()
    task = db.execute(text("select kind, title, status, payload from task where kind = 'supplier_chase'")).one()
    sup_name = csv_suppliers()[winner]
    c.true(
        "the buyer's task names the order and the balance, with the chaser written out",
        po in task[1] and f"{part2:f} to come" in str(task[3].get("draft", "")),
        str(task[1]),
    )

    # the balance arrives and completes the order
    receive_part(d, c, po, pick, part2, pick.qty, "DC/2611/0544")
    chase_after = supplier_comms.chase_run(db, co.world.slug, as_of=business_today())
    c.same(
        "no more chasing once the order is complete",
        (chase_after.emailed, chase_after.tasks),
        ([], []),
    )
    c.same(
        "the late-delivery alert is still open until the next daily run",
        one(
            db,
            "select count(*) from alert where rule = 'late_delivery' and status = 'open'",
        ),
        1,
    )
    alerts.run(db, co.world.slug, today=business_today())  # the next morning's run
    d.sync()
    c.same(
        "the next daily run resolves the late-delivery alert: the balance has come",
        one(
            db,
            "select count(*) from alert where rule = 'late_delivery' and status = 'open'",
        ),
        0,
    )
    arrived_second = first(db, "select max(accepted_on) from goods_receipt")
    number2 = "WIS/26-27/0563"
    if by_documents:
        upload_invoice(d, c, winner, po, pick, number2, part2, None, late_day, qr=False)
    else:
        enter_invoice(d, winner, number2, part2, None, late_day)
    inv2 = check_booked_invoice(
        d, c, co, number2, winner, pick, part2, unit, late_day, what="invoice for the balance"
    )  # fmt: skip
    c.same(
        "both invoices together are the whole order: invoiced equals ordered and received",
        (
            D(
                one(
                    db,
                    "select invoiced_qty from po_line where po_no = :n and line_no = 1",
                    n=po,
                )
            ),
            D(
                one(
                    db,
                    "select received_qty from po_line where po_no = :n and line_no = 1",
                    n=po,
                )
            ),
        ),
        (pick.qty, pick.qty),
    )
    c.same(
        "both invoices matched on the quantity received so far: no blocked or held invoice",
        db.execute(text("select distinct result from match_result where invoice_line_no is not null")).scalars().all(),
        ["matched"],
    )
    c.same(
        "the order is not closed yet: nothing is paid",
        one(db, "select status from po where no = :n", n=po),
        "received",
    )
    c.same("the second delivery was accepted on the day it came", arrived_second, late_day)
    same_as_typed(d, c, "part", not by_documents)

    # one payment to the supplier for both invoices; the order closes when it is paid
    pay_many(d, c, co, [inv1, inv2], winner, po)
    c.same(
        "order lines are closed with the order",
        db.execute(text("select distinct status from po_line where po_no = :n"), {"n": po}).scalars().all(),
        ["closed"],
    )
    audit_trail(
        d, c,
        ("request.create", "request.approve", "po.create", "po.approve", "po.send", "grn.post", "invoice.enter",
         "supplier.chase", "payment_run.propose", "payment_run.approve", "payment_run.paid", "po.close",
         *(("email.receive", "quote_draft.create", "invoice_draft.create") if by_documents else ())),
    )  # fmt: skip
    separation_of_duties(
        d, c, co,
        {
            "requester": {"request.submit": "buyer"},
            "approver": {"request.approve": "stores_head", **order_approver(d), "payment_run.approve": "owner"},
            "receiver": {"grn.post": "store"},
            "payer": {"payment_run.propose": "finance", "payment_run.paid": "finance"},
        },
        also={"po.send": "buyer", "invoice.enter": "ap", "payment_run.file": "finance"},
    )  # fmt: skip
    c.true(
        f"the winning supplier is {sup_name['trade_name']}, paid once for the whole order",
        one(db, "select count(*) from payment") == 1,
    )


# ---- run 2: a contract price, an invoice that does not match, released by someone else ------------


def sourced_from_contract(d: Demo, c: Checks, pick: Pick) -> tuple[str, Decimal]:
    """After the worker: the line was bought from the contract, at the contract's price for the quantity."""
    db = d.db
    prices = contract_prices(pick.item, pick.qty)
    best = min(prices, key=lambda x: D(x["price"]))
    sd = db.execute(text("select method, supplier_code, contract_code from sourcing_decision")).one()
    c.same(
        "how the item is bought: a contract price is in force, so no quotation is asked for",
        sd[0],
        "contract",
    )
    c.same(
        "the supplier and contract, from the price records in force",
        (sd[1], sd[2]),
        (best["supplier_code"], best["contract_code"]),
    )
    c.same("no quotation request was made", one(db, "select count(*) from rfq"), 0)
    return best["supplier_code"], D(best["price"])


def script_the_explainer(d: Demo, inv_no: str, po: str, supplier: str) -> None:
    """The invoice-exceptions agent investigates with a scripted model (never a real one): it looks at the invoice, the order,
    the receipts and the supplier, then repeats the rules' own explanation in words. The platform checks the cause and
    every number against what the tools returned before it shows the explanation."""
    from e25_invoice_exceptions import Investigator
    from p2p.agents import invoice_exceptions as agent
    from p2p.ai_steps.gateway import Gateway
    from p2p.platform import ui_api

    d.sync()
    base = agent.diagnose(d.db, inv_no)
    assert base is not None
    fake = Investigator()
    fake.invoice, fake.order, fake.supplier = inv_no, po, supplier
    fake.explanation, fake.cause = base.explanation, base.cause
    routes = {k: [("fake", "m")] for k in ("agent_invoice_exceptions", "agent", "default")}
    ui_api.use_gateway(Gateway({"fake": fake}, routes, sleep=lambda s: None))
    d.db.commit()


def run_blocked_invoice(d: Demo, c: Checks, co: Company, pick: Pick, by_documents: bool = False) -> None:
    from playwright.sync_api import expect

    db = d.db
    sign_in_all(d, co, ("buyer", "proc_head", "store", "ap", "plant_head", "finance", "owner"))
    req = replenish(d, c, pick, "auto")
    d.work()  # the request approved itself: the worker finds the contract price and makes the order
    supplier, price = sourced_from_contract(d, c, pick)
    po = str(one(db, "select no from po"))
    taxable, total, igst = check_order(d, c, po, supplier, pick, price)
    contract = pick.notes["contract"]
    c.same(
        "the order refers to the contract",
        one(db, "select contract_code from po where no = :n", n=po),
        contract,
    )
    with d.step("buyer", f"opens contract {contract}: the order counts against its ceiling") as page:
        d.go("buyer", f"contracts/{contract}")
        expect(page.locator("#appContent")).to_contain_text(po)
        expect(page.locator("#appContent")).to_contain_text(money(taxable))
    approve_order(d, c, po, taxable, total, igst)
    send_and_acknowledge(d, c, po, supplier, total)
    receive_goods(d, c, co, po, pick)

    # the supplier's invoice has a price 12% above the order's: more than the 2% and 500 rupees the tolerance allows
    markup = (price * Decimal("1.12")).quantize(Decimal("0.01"))
    pct_limit, abs_limit = (
        tolerance("price_pct", pick.category),
        tolerance("price_abs", pick.category),
    )
    over_pct, over_line = (markup - price) / price * 100, (markup - price) * pick.qty
    c.true(
        f"the invoice price {markup} is above the order price {price} by more than {pct_limit}% and {abs_limit} rupees on the line",
        over_pct > pct_limit and over_line > abs_limit,
        f"{over_pct:.1f}% and {over_line}",
    )
    number = "PIS/26-27/7731"
    if by_documents:
        upload_invoice(d, c, supplier, po, pick, number, pick.qty, markup, qr=False)
    else:
        enter_invoice(d, supplier, number, pick.qty, markup)
    inv = db.execute(text("select no, status, block_reason, payment_block, entered_by from invoice")).one()
    inv_no = inv[0]
    c.same(
        "invoice status: the price is above what the order and the tolerance allow",
        inv[1],
        "blocked",
    )
    c.true("the block reason names the price", "price" in str(inv[2]).lower(), str(inv[2]))
    unit_price_matched = db.execute(
        text("select result, reason_codes from match_result where invoice_no = :n and invoice_line_no is not null"),
        {"n": inv_no},
    ).all()
    c.same(
        "the match of the invoice line: blocked for the price",
        [tuple(r) for r in unit_price_matched],
        [("blocked", "price_variance")],
    )
    with d.step("ap", "sees the invoice is blocked and why, and what to do about it") as page:
        expect(page.locator("#appContent")).to_contain_text("Blocked")
        expect(page.locator("#appContent")).to_contain_text("Price above the order price")
        script_the_explainer(d, inv_no, po, supplier)
        page.locator("#invExplain").click()  # the clerk asks for the explanation
        expect(page.locator("#invAdvice")).to_contain_text("What to do")
        expect(page.locator("#invAdvice")).to_contain_text("Only a person can release")
    d.sync()
    diagnosis = db.execute(
        text("select cause, fix, source, created_by from invoice_diagnosis where invoice_no = :n"),
        {"n": inv_no},
    ).all()
    c.same(
        "the explanation: cause, advice, written by the agent and kept for the audit, asked for by the clerk",
        [tuple(r) for r in diagnosis],
        [("price_mismatch", "corrected_invoice", "agent", co.ids["ap"])],
    )
    c.same(
        "the explaining changed nothing: the invoice is still blocked",
        one(db, "select status from invoice where no = :n", n=inv_no),
        "blocked",
    )
    c.true(
        "nothing can be paid while it is blocked: the payment run has nothing due",
        api("GET", f"/api/payment-runs/due?on={TODAY}&window=90", token(co, "finance")).json() == [],
    )

    reason = "The supplier raised its price from 1 November; the plant needs these parts for the shutdown."
    with d.step("ap", "asks for the invoice to be paid anyway, giving a reason") as page:
        page.locator("#invRelease").click()
        page.locator("#invReason").fill(reason)
        page.locator("#invWhyOk").click()
        expect(page.locator("#appContent")).to_contain_text("Waiting for approval")
    d.sync()
    pid = int(
        one(
            db,
            "select id from proposal where action = 'invoice.release_block' and status = 'pending'",
        )
    )
    c.same(
        "the invoice is still blocked until someone else decides",
        one(db, "select status from invoice where no = :n", n=inv_no),
        "blocked",
    )
    c.same(
        "the clerk who entered the invoice cannot release it (refused)",
        api("POST", f"/api/admin/proposals/{pid}/approve", token(co, "ap"), json={}).status_code,
        403,
    )
    c.same(
        "and has no such decision in the approvals list",
        api("GET", "/api/admin/proposals", token(co, "ap")).json(),
        [],
    )

    decide_in_approvals(
        d,
        "plant_head",
        "Blocked invoices to release",
        inv_no,
        what="reads the reason and approves paying the invoice",
    )
    row = db.execute(
        text("select status, payment_block, due_date, total, taxable from invoice where no = :n"),
        {"n": inv_no},
    ).one()
    sup = csv_suppliers()[supplier]
    t, cg, sg, ig = oracle_line(
        pick.qty,
        markup,
        item_rate(pick.item),
        sup["state_code"],
        csv_locations()[pick.loc]["gstin"][:2],
        sup["gst_reg_type"],
    )
    owed = to_rupee(t + cg + sg + ig)
    c.same("invoice status after the release", row[0], "approved")
    c.same(
        "the invoice is paid at the price it carries: before tax and total",
        (D(row[4]), D(row[3])),
        (t, owed),
    )
    accepted = first(db, "select max(accepted_on) from goods_receipt")
    due = due_date(sup, TODAY, accepted)
    c.same(
        "due date worked out when the block was released (small supplier: terms and the 45-day rule)",
        row[2],
        due,
    )
    c.same(
        "the match result records who resolved it",
        first(
            db,
            "select resolved_by from match_result where invoice_no = :n and result = 'blocked'",
            n=inv_no,
        ),
        str(co.ids["plant_head"]),
    )
    c.true(
        "the release is in the audit log with its reason",
        int(
            one(
                db,
                "select count(*) from audit_event where action = 'invoice.release_block' and reason like :r",
                r=f"{reason[:30]}%",
            )
        )
        == 1,
    )

    pay(d, c, co, inv_no, supplier, owed, t, due, po)
    same_as_typed(d, c, "blocked", not by_documents)
    audit_trail(
        d, c,
        ("request.create", "po.create", "po.approve", "po.send", "invoice.release_block", "payment_run.propose",
         "payment_run.approve", "payment_run.paid", "po.close", *(("invoice_draft.create",) if by_documents else ())),
    )  # fmt: skip
    separation_of_duties(
        d, c, co,
        {
            "requester": {"request.submit": "buyer"},
            "approver": {**order_approver(d), "payment_run.approve": "owner"},
            "receiver": {"grn.post": "store"},
            "invoice approver": {"invoice.release_block": "plant_head"},
            "payer": {"payment_run.propose": "finance", "payment_run.paid": "finance"},
        },
        also={"po.send": "buyer", "invoice.enter": "ap", "payment_run.file": "finance"},
    )  # fmt: skip
    c.same(
        "the request was approved by the rule, not by a person",
        first(db, "select count(*) from approval_step where request_no = :n", n=req),
        0,
    )


# ---- run 3: quotes far above the estimate hold the order until an approver accepts -----------------


def run_held_order(d: Demo, c: Checks, co: Company, pick: Pick) -> None:
    from playwright.sync_api import expect

    db = d.db
    sign_in_all(d, co, ("buyer", "proc_head"))
    replenish(d, c, pick, request_route(pick.value))
    d.work()
    c.true("no contract price is in force for the item, so quotations are asked for", not contract_prices(pick.item, pick.qty))
    rfq = str(one(db, "select no from rfq"))
    invited = [r[0] for r in db.execute(text("select supplier_code from rfq_supplier where rfq_no = :n order by 1"), {"n": rfq})]
    decide_in_approvals(d, "buyer", "RFQs waiting to be sent", rfq, what="approves sending the quotation request")
    supplier, price = invited[0], D(DEAR[1])
    # two quotes, as in the quotation runs: the cheapest, 25% above the estimate, and a dearer one
    type_quotes(d, pick, rfq, {s: (DEAR[1] if s == supplier else DEAR[2], None) for s in invited[:2]})
    with d.step("buyer", "compares the quotes and awards the cheapest") as page:
        expect(page.locator("#appContent tr", has_text="recommended")).to_contain_text(
            csv_suppliers()[supplier]["trade_name"]
        )
        page.locator("#awardBtn").click()
        expect(page.locator("#appContent")).to_contain_text("Supplier selected")
    d.sync()
    d.work()  # the worker makes the order from the award
    po = str(one(db, "select no from po"))
    band = D(next(r["value"] for r in rows("master", "setting") if r["key"] == "price_band_pct"))
    over = (price - pick.price) / pick.price * 100
    c.true(
        f"the quoted price {price} is {over:.1f}% above the estimate {pick.price} (its {pick.notes['estimate']}): "
        f"more than the {band}% allowed",
        over > band,
    )
    taxable, total, igst = check_order(d, c, po, supplier, pick, price, status="draft")
    held = db.execute(text("select hold_reason, hold_proposal_id from po where no = :n"), {"n": po}).one()
    c.true(
        "the order carries the reason it is held, and a proposal for an approver",
        held[0] is not None and held[1] is not None,
        str(held),
    )
    with d.step("buyer", "sees the new order in the list, held: the price is above the estimate") as page:
        d.go("buyer", "purchase-orders")
        expect(page.locator("#tableWrap")).to_contain_text(po)
        expect(page.locator("#tableWrap")).to_contain_text("held: price above the estimate")
    with d.step(
        "buyer",
        "opens the order: it says why it is on hold, and offers nothing to send",
    ) as page:
        page.locator(f"#tableWrap tr[data-id='{po}']").click()
        expect(page.locator("#appContent")).to_contain_text("On hold until a person accepts the price")
        expect(page.locator("#appContent")).to_contain_text("Price above the estimate by more than")
        expect(page.locator("#poSend")).to_have_count(0)
    nothing_in_work(
        d,
        "proc_head",
        "Orders waiting for my approval",
        what="has no such order to approve: it is not yet up for approval",
    )
    c.same(
        "the procurement head cannot approve it while it is held (refused)",
        api(
            "POST",
            f"/api/purchase-orders/{po}/approve",
            token(co, "proc_head"),
            json={},
        ).status_code,
        409,
    )
    c.same(
        "the buyer cannot send it while it is held (refused)",
        api("POST", f"/api/purchase-orders/{po}/send", d.w.headers("Buyer"), json={}).status_code,
        409,
    )
    c.same("the order did not go to the supplier", len(supplier_mails(d, subject=po)), 0)

    decide_in_approvals(
        d,
        "proc_head",
        "Orders held for a price or ceiling",
        po,
        what="reads the reason and accepts the price",
    )
    c.same(
        "order status: accepted, so it follows the approval band for its value",
        first(db, "select status from po where no = :n", n=po),
        "approved" if po_route(taxable) == "auto" else "submitted",
    )
    c.same(
        "the hold is lifted",
        first(db, "select hold_reason from po where no = :n", n=po),
        None,
    )
    c.same(
        "the proposal was decided by the purchase manager (the rule for price variances)",
        first(db, "select decided_by from proposal where id = :i", i=held[1]),
        co.ids["proc_head"],
    )
    c.true(
        "the acceptance is in the audit log",
        int(
            one(
                db,
                "select count(*) from audit_event where action = 'po.variance_accepted' and entity_id = :n",
                n=po,
            )
        )
        == 1,
    )

    approve_order(d, c, po, taxable, total, igst)
    send_and_acknowledge(d, c, po, supplier, total)
    c.same(
        "the price on the order is the quoted price, not the estimate",
        D(
            one(
                db,
                "select unit_price from po_line where po_no = :n and line_no = 1",
                n=po,
            )
        ),
        price,
    )
    audit_trail(
        d,
        c,
        (
            "po.create",
            "po.variance_accepted",
            "po.send",
            "proposal.approved",
        ),
    )
    separation_of_duties(
        d, c, co,
        {
            "requester": {"request.submit": "buyer"},
            "approver": {"po.variance_accepted": "proc_head"},
        },
        also={"po.send": "buyer", "po.acknowledge": "buyer"},
    )  # fmt: skip


RUNS = {
    "1": (
        "straight",
        "Run 1: from stock at its reorder point to a paid and closed order (by quotation, everything typed in)",
        "quotation",
        run_straight_through,
    ),
    "2": (
        "blocked",
        "Run 2: a contract price, an invoice that does not match, released by another person, then paid",
        "contract",
        run_blocked_invoice,
    ),
    "3": (
        "held",
        "Run 3: quotes far above the estimate hold the order until an approver accepts the price",
        "dear",
        run_held_order,
    ),
    "4": (
        "part",
        "Run 4: a part delivery: the balance is late and chased, the second delivery completes the order, two invoices, one payment",
        "quotation",
        run_part_delivery,
    ),
    "5": (
        "straight_docs",
        "Run 5: the same as run 1 with documents: the quotations arrive as emails, the invoice is uploaded as a PDF",
        "quotation",
        partial(run_straight_through, by_documents=True),
    ),
    "6": (
        "part_docs",
        "Run 6: the same as run 4 with documents: emailed quotations, one invoice read from its QR code, one from a plain PDF",
        "quotation",
        partial(run_part_delivery, by_documents=True),
    ),
    "7": (
        "blocked_docs",
        "Run 7: the same as run 2 with a document: the invoice that does not match is uploaded as a PDF",
        "contract",
        partial(run_blocked_invoice, by_documents=True),
    ),
}


def run(ctx: Any) -> Outcome:
    import logging

    from p2p.formulas.calendar import pin_today
    from p2p.platform import ui_api
    from playwright.sync_api import expect, sync_playwright

    # closing a browser connection on Windows makes asyncio print a harmless "connection reset" trace
    logging.getLogger("asyncio").setLevel(logging.CRITICAL)
    expect.set_options(timeout=20_000)  # a slow machine must not turn into a false failure
    out = Outcome(True)
    if not (lib.ROOT / "frontend" / "dist" / "index.html").is_file():
        out.skipped = True  # type: ignore[attr-defined]
        out.notes.append("skipped: the admin console is not built (cd frontend && npm run build)")
        return out
    # Jobs the evals that ran before this one left queued would be run by the worker this eval drives by hand.
    from sqlalchemy import text as _text

    from p2p.platform import tenants as _tenants

    with _tenants.control_engine().begin() as _conn:
        _conn.execute(_text("delete from job"))
    global SCENARIO
    SCENARIO = None
    wanted = [s for s in os.environ.get("P2P_E30_RUNS", "1,2,3,4,5,6,7").split(",") if s]
    # no screen of these runs calls a real model; anything that tried would find no provider and fail loudly
    total_steps = total_checks = 0
    with Site(), sync_playwright() as p:
        browser = p.chromium.launch()
        for number in wanted:
            name, title, kind, fn = RUNS[number]
            pin_today(TODAY)  # every run starts on the same Friday
            ui_api.use_gateway(model_gateway())  # the scripted model, and no other
            co = company(f"e30{number}")
            box = lib.capture_mail()
            pick = pick_for(kind)
            lower_stock_to_reorder_point(co.world.db, pick.item, pick.loc)
            d = Demo(name, title, co.world, box, browser)
            c = Checks(out, name)
            verdict = "passed"
            try:
                fn(d, c, co, pick)
            except Exception as exc:  # noqa: BLE001 - a failed step is a finding with the step it stopped at
                verdict = "stopped"
                last = d.steps[-1]["did"] if d.steps else "the start"
                message = str(exc).splitlines()[0][:300] if str(exc) else ""
                out.find(
                    "high",
                    f"{name}: stopped after '{last}': {type(exc).__name__}: {message}",
                )
                out.notes.append(traceback.format_exc(limit=4))
            serious = [p_ for p_ in d.problems if " 5" in p_[:14] or "page error" in p_ or "console error" in p_]
            if serious:
                out.find("medium", f"{name}: the browser reported: {serious[:3]}")
            elif d.problems:
                out.find("low", f"{name}: the browser saw refused requests: {d.problems[:3]}")
            if c.failed and verdict == "passed":
                verdict = "FAILED"
            d.write_transcript(c.facts, verdict)
            total_steps += len(d.steps)
            total_checks += c.passed + c.failed
            out.metrics[f"{name}: steps / checks passed / failed"] = f"{len(d.steps)} / {c.passed} / {c.failed}"
            d.close()
        browser.close()
    ui_api.use_gateway(None)
    pin_today(TODAY)
    out.metrics["steps taken at screens"] = total_steps
    out.metrics["checks against the dataset"] = total_checks
    out.metrics["transcripts and screenshots"] = "evals/results/demo_runs/"
    return out
