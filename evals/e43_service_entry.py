"""E43: service entry and receipt without an order (plan step 9, P1-37), on the IT services company (Brightwave).

Its service orders are loaded as sent, and each person who asked for a service confirms it through the API with the amount
the dataset's own service entry accepted. An oracle written here from the raw files says, for every confirmation, whether
it may be made (the buyer may not confirm what they bought; nothing beyond the order plus the category's over-delivery
tolerance) and what it counts on the line (amount / price, half up to four places). The edges: 100% and 100.01% of a line,
an amount a paisa above the lump sum. An invoice for a service nobody has confirmed must be held as "service not
confirmed" and freed by the confirmation; the person who confirmed may not approve the invoice. Goods that come without an
order stay out of stock until the purchase manager approves, the buyer orders them after the fact and the store receives
them on that order; rejected ones only go back. A serialised laptop needs one serial per unit and a serial twice is refused.
The emailed confirmation link works once and expires. Every change is audited.
"""

import time
from collections import defaultdict
from datetime import date
from decimal import Decimal
from fractions import Fraction

import lib
from fastapi.testclient import TestClient
from lib import D, Outcome, rows
from replay import insert
from sqlalchemy import text

ID = "E43"
TITLE = "Service entry, the two-way match of services, receipts without an order and serial numbers are right"
REQUIREMENT = (
    "Plan step 9, P1-37: the requester confirms a service (quantity, percent or amount), a service invoice is matched "
    "against the order and the confirmation, an unconfirmed service cannot be invoiced, a receipt without an order needs "
    "the purchase manager's approval and an after-the-fact order, serialised items need a unique serial per unit"
)
DATASET = lib.ROOT / "data" / "datasets" / "brightwave-v1"


def half_up4(x: Fraction) -> Decimal:
    return Decimal((x * 10000 + Fraction(1, 2)).__floor__()) / Decimal(10000)


def paise(x: Fraction) -> Decimal:
    return Decimal((x * 100 + Fraction(1, 2)).__floor__()) / Decimal(100)


def run(ctx) -> Outcome:  # noqa: C901 - one story, told in order
    from p2p.invoices import service as invoices
    from p2p.main import app
    from p2p.orders import dispatch, workflow
    from p2p.platform import proposals, users
    from p2p.platform.tokens import create_access_token
    from p2p.receiving import service_links

    out = Outcome(True)
    world = lib.build_world("e43", dataset=DATASET)
    db, slug = world.db, world.slug
    client = TestClient(app)
    hist = lambda name: rows("history", name, DATASET)  # noqa: E731

    # ---- the oracle's own reading of the dataset ----
    items = {r["code"]: r for r in rows("master", "item", DATASET)}
    cats = {r["code"]: r for r in rows("master", "category", DATASET)}
    tol = {
        r["category_code"]: Fraction(D(r["value"]))
        for r in rows("master", "tolerance", DATASET)
        if r["key"] == "receipt_over_pct"
    }

    def is_service(code: str) -> bool:
        return (items[code]["kind"] or cats[items[code]["category_code"]]["kind"]) == "service"

    def over(code: str) -> Fraction:
        return tol.get(items[code]["category_code"], tol.get("ANY", Fraction(0)))

    requests = {r["no"]: r for r in hist("request")}
    lines = [
        ln
        for ln in hist("po_line")
        if is_service(ln["item_code"]) and requests.get(ln["request_no"], {}).get("requester")
    ]
    pos = {p["no"]: p for p in hist("po") if p["no"] in {ln["po_no"] for ln in lines}}
    entries = defaultdict(list)
    for e in hist("service_receipt"):
        entries[(e["po_no"], e["po_line_no"])].append(e)
    if ctx.quick:
        keep = sorted(pos)[:12]
        pos = {k: pos[k] for k in keep}
        lines = [ln for ln in lines if ln["po_no"] in pos]
    out.metrics["service order lines"] = len(lines)

    # ---- load the service orders as sent, with the requests behind them ----
    for no in sorted({ln["request_no"] for ln in lines}):
        insert(db, "request", requests[no], {"status": "closed"})
    for p in pos.values():
        insert(db, "po", p, {"status": "sent"})
    for ln in lines:
        insert(db, "po_line", ln, {"received_qty": 0, "invoiced_qty": 0, "status": "open", "source": "award",
                                   "taxable": (D(ln["qty"]) * D(ln["unit_price"])).quantize(D("0.01"))})  # fmt: skip
    # the loaded requests took numbers of this year: the company's own counter carries on after them
    taken = [("request", no, requests[no]["ship_to_location"]) for no in {ln["request_no"] for ln in lines}]
    taken += [("po", no, p["ship_to_location"]) for no, p in pos.items()]
    for kind, no, place in taken:
        _, _, fy, seq = no.split("-")
        db.execute(
            text(
                "insert into number_counter (document_type, scope_key, fy, last_seq) values (:k, :s, :f, :q) "
                "on conflict (document_type, scope_key, fy) do update set last_seq = greatest(number_counter.last_seq, :q)"
            ),
            {"k": kind, "s": place, "f": fy, "q": int(seq)},
        )
    db.commit()

    # each person who asked gets a login under their own address
    people: dict[str, int] = dict(world.org_logins)
    sfx = str(int(time.time()))[-5:]
    for code in sorted({requests[ln["request_no"]]["requester"] for ln in lines} - set(people)):
        own = f"{code.lower()}.{sfx}@e43.example"
        db.execute(
            text("update org_user set email = :e where code = :c"),
            {"e": own, "c": code},
        )
        db.commit()
        people[code] = users.create_user(slug, own, lib.PASSWORD, code, "Requester")

    def as_person(code: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {create_access_token(people[code], slug, 'Requester')}"}

    def confirm(code: str, po_no: str, line_no: str, basis: str, value: str):  # type: ignore[no-untyped-def]
        return client.post(
            "/api/services/confirm", headers=as_person(code),
            json={"po_no": po_no, "line_no": int(line_no), "basis": basis, "value": value},
        )  # fmt: skip

    # ---- an invoice for a service nobody has confirmed is held ----
    held: dict[str, str] = {}  # platform invoice -> po
    inv_lines = defaultdict(list)
    for r in hist("invoice_line"):
        inv_lines[r["invoice_no"]].append(r)
    eligible = [ln for ln in lines if requests[ln["request_no"]]["requester"] != pos[ln["po_no"]]["buyer"]]
    if len(eligible) < 5:
        out.find(
            "high",
            f"only {len(eligible)} service lines a requester may confirm: the dataset is too small for this eval",
        )
        return out
    # kept aside: the 100% edge, the paisa edge, the invoice approval and the emailed link
    edge, one_paisa, spare, link_line = eligible[:4]
    aside = {(x["po_no"], x["line_no"]) for x in eligible[:4]}
    for inv in hist("invoice"):
        ls = inv_lines.get(inv["no"], [])
        if inv["type"] != "invoice" or not ls or len(held) >= 3 or any(x["po_no"] not in pos for x in ls):
            continue
        keys = {(x["po_no"], x["po_line_no"]) for x in ls}
        if keys & aside or not keys <= {(e["po_no"], e["line_no"]) for e in eligible}:
            continue
        data = invoices.InvoiceIn(
            supplier_code=inv["supplier_code"], supplier_invoice_no=inv["supplier_invoice_no"],
            invoice_date=date.fromisoformat(inv["invoice_date"]), supplier_gstin=inv["supplier_gstin"] or None,
            buyer_gstin=inv["buyer_gstin"] or None, irn=inv["irn"] or None,
            lines=[invoices.InvLine(x["po_no"], int(x["po_line_no"]), D(x["qty"]), D(x["unit_price"])) for x in ls],
        )  # fmt: skip
        done = invoices.enter_invoice(db, data, world.logins["AP"], as_of=lib.TODAY)
        if done.status != "blocked" or done.reasons != ["service_not_confirmed"]:
            out.find(
                "high",
                f"an invoice for an unconfirmed service was not held as not confirmed: {done}",
            )
        held[done.no] = ls[0]["po_no"]
    out.metrics["invoices held before confirmation"] = len(held)

    # ---- the edges ----
    who = requests[edge["request_no"]]["requester"]
    limit_pct = 100 + over(edge["item_code"])
    for pct in ("100.01", "100"):
        r = confirm(who, edge["po_no"], edge["line_no"], "pct", pct)
        want = Fraction(D(pct)) <= limit_pct
        if (r.status_code == 200) != want:
            out.find(
                "high",
                f"{pct}% of {edge['po_no']}/{edge['line_no']} with {limit_pct}% allowed: {r.status_code} {r.text[:120]}",
            )
    # ---- every other line: the dataset's own confirmations, by amount ----
    confirmed: dict[tuple[str, str], Fraction] = defaultdict(Fraction)
    confirmed[(edge["po_no"], edge["line_no"])] = Fraction(D(edge["qty"]))
    checked = refused_right = 0
    for ln in lines:
        key = (ln["po_no"], ln["line_no"])
        if key in aside:
            continue
        price, ordered = Fraction(D(ln["unit_price"])), Fraction(D(ln["qty"]))
        requester = requests[ln["request_no"]]["requester"]
        for e in entries.get(key, []) or [{"amount_accepted": str(D(ln["qty"]) * D(ln["unit_price"]))}]:
            amount = Fraction(D(e["amount_accepted"]))
            room = paise((ordered * (100 + over(ln["item_code"])) / 100 - confirmed[key]) * price)
            ok = requester != pos[ln["po_no"]]["buyer"] and paise(amount) <= room
            r = confirm(requester, ln["po_no"], ln["line_no"], "amount", str(paise(amount)))
            checked += 1
            if (r.status_code == 200) != ok:
                out.find(
                    "high",
                    f"{key} amount {paise(amount)} by {requester}: expected {'accepted' if ok else 'refused'}, got {r.status_code} {r.text[:120]}",
                )
                continue
            if not ok:
                refused_right += 1
                continue
            qty = half_up4(amount / price)
            confirmed[key] += Fraction(qty)
            got = r.json()
            if D(got["qty"]) != qty or D(got["amount"]) != paise(amount):
                out.find(
                    "high",
                    f"{key}: confirmed {got['qty']} / {got['amount']}, the oracle says {qty} / {paise(amount)}",
                )
    out.metrics["confirmations checked"] = checked
    out.metrics["refused as the oracle said (buyer asked, or beyond the order)"] = refused_right
    if one_paisa:  # a lump sum and a paisa: nothing may be confirmed beyond it
        over_by = paise(
            Fraction(D(one_paisa["qty"]))
            * Fraction(D(one_paisa["unit_price"]))
            * (100 + over(one_paisa["item_code"]))
            / 100
        ) + D("0.01")
        r = confirm(
            requests[one_paisa["request_no"]]["requester"],
            one_paisa["po_no"],
            one_paisa["line_no"],
            "amount",
            str(over_by),
        )
        if r.status_code != 409:
            out.find(
                "high",
                f"an amount a paisa above what may be confirmed was taken: {r.status_code}",
            )
    db.expire_all()
    for (po_no, line_no), qty in confirmed.items():
        got = D(
            db.execute(
                text("select received_qty from po_line where po_no = :p and line_no = :l"),
                {"p": po_no, "l": int(line_no)},
            ).scalar_one()
        )
        if got != Decimal(qty.numerator) / Decimal(qty.denominator):
            out.find(
                "high",
                f"{po_no}/{line_no} counts {got} received, the confirmations add up to {float(qty)}",
            )
    entries_made = db.execute(text("select count(*) from service_entry")).scalar_one()
    audited = db.execute(text("select count(*) from audit_event where action = 'service.confirm'")).scalar_one()
    if entries_made != audited:
        out.find("high", f"{entries_made} service entries but {audited} audit rows")
    if db.execute(text("select count(*) from stock_movement where ref_doc like 'SRN-%'")).scalar_one():
        out.find("high", "a service entry moved stock")

    # the held invoices were matched again: none is still held as not confirmed
    still = db.execute(
        text(
            "select count(*) from invoice where no = any(:n) and status = 'blocked' and block_reason like 'Service not confirmed%'"
        ),
        {"n": sorted(held)},
    ).scalar_one()
    if still:
        out.find(
            "high",
            f"{still} invoice(s) still held as not confirmed after the service was confirmed",
        )

    # ---- the person who confirmed cannot approve the invoice ----
    approvers = sorted(world.org_logins)
    if len(approvers) >= 2:
        boss, other = approvers[-1], approvers[0]
        db.execute(
            text("update request set requester = :c where no = :r"),
            {"c": boss, "r": spare["request_no"]},
        )
        db.commit()
        confirm(boss, spare["po_no"], spare["line_no"], "pct", "50")
        data = invoices.InvoiceIn(
            supplier_code=pos[spare["po_no"]]["supplier_code"], supplier_invoice_no=f"E43-{spare['po_no']}", invoice_date=lib.TODAY,
            supplier_gstin=pos[spare["po_no"]]["supplier_gstin"] or None, buyer_gstin=pos[spare["po_no"]]["buyer_gstin"] or None,
            lines=[invoices.InvLine(spare["po_no"], int(spare["line_no"]), D(spare["qty"]), D(spare["unit_price"]))],
        )  # fmt: skip
        inv = invoices.enter_invoice(db, data, world.logins["AP"], as_of=lib.TODAY)
        pid = invoices.request_release(db, inv.no, "agreed with the supplier", world.logins["AP"])
        proposals.approve(db, pid, world.org_logins[boss])
        db.expire_all()
        if db.execute(text("select status from invoice where no = :n"), {"n": inv.no}).scalar_one() != "blocked":
            out.find("high", "the person who confirmed the service released its invoice")
        pid = invoices.request_release(db, inv.no, "agreed with the supplier", world.logins["AP"])
        proposals.approve(db, pid, world.org_logins[other])
        db.expire_all()
        if db.execute(text("select status from invoice where no = :n"), {"n": inv.no}).scalar_one() != "approved":
            out.find("medium", "another approver could not release the invoice")

    # ---- the emailed link: works once, expires ----
    if link_line:
        code = db.execute(
            text("select requester from request where no = :r"),
            {"r": link_line["request_no"]},
        ).scalar_one()
        seen = db.execute(text("select count(*) from service_entry where po_no = :p and po_line_no = :l"),
                          {"p": link_line["po_no"], "l": int(link_line["line_no"])}).scalar_one()  # fmt: skip
        token = service_links.make(slug, link_line["po_no"], int(link_line["line_no"]), people[code], seen)
        first = client.post(f"/confirm-service/{token}", data={"basis": "pct", "value": "10"})
        second = client.post(f"/confirm-service/{token}", data={"basis": "pct", "value": "10"})
        old = service_links.make(
            slug,
            link_line["po_no"],
            int(link_line["line_no"]),
            people[code],
            seen + 1,
            now=time.time() - 8 * 86400,
        )
        expired = client.get(f"/confirm-service/{old}")
        if (first.status_code, second.status_code, expired.status_code) != (
            200,
            409,
            400,
        ):
            out.find(
                "high",
                f"link: first {first.status_code}, again {second.status_code}, expired {expired.status_code}",
            )

    # ---- goods without an order: approved, ordered after the fact, received; rejected and returned ----
    store, buyer = world.headers("Store"), world.headers("Buyer")
    laptop = next(c for c, i in items.items() if i.get("serialised") == "true")
    supplier = next(
        r["supplier_code"]
        for r in rows("master", "supplier_category", DATASET)
        if r["category_code"] == items[laptop]["category_code"]
    )
    loc = lib.location(DATASET)

    def on_hand() -> Decimal:
        db.expire_all()
        return D(
            db.execute(
                text("select coalesce(sum(qty), 0) from stock_movement where item_code = :i"),
                {"i": laptop},
            ).scalar_one()
        )

    before = on_hand()
    body = {"supplier_code": supplier, "item_code": laptop, "qty": "2", "location_code": loc,
            "reason": "Two laptops delivered against a phone call", "challan": "DC/43", "serials": "SN-E43-1, SN-E43-2"}  # fmt: skip
    r = client.post("/api/unordered-receipts", headers=store, json=body)
    if r.status_code != 200:
        out.find("high", f"the store could not record goods without an order: {r.text[:160]}")
        return out
    first_no = r.json()["receipt"]
    rejected = client.post(
        "/api/unordered-receipts",
        headers=store,
        json={**body, "serials": "SN-E43-3, SN-E43-4"},
    ).json()["receipt"]
    if on_hand() != before:
        out.find("high", "goods without an order entered stock before approval")
    if client.post(f"/api/unordered-receipts/{first_no}/approve", headers=store, json={}).status_code != 403:
        out.find("high", "the store approved its own receipt without an order")
    if (
        client.post(
            f"/api/unordered-receipts/{first_no}/order",
            headers=buyer,
            json={"unit_price": "62000"},
        ).status_code
        != 409
    ):
        out.find(
            "high",
            "an order was raised for goods the purchase manager had not approved",
        )
    pm = world.org_headers(approvers[0])
    if client.post(f"/api/unordered-receipts/{first_no}/approve", headers=pm, json={}).status_code != 200:
        out.find("high", "the purchase manager could not approve")
    r = client.post(
        f"/api/unordered-receipts/{first_no}/order",
        headers=buyer,
        json={"unit_price": "62000"},
    )
    po_no = r.json().get("po_no") if r.status_code == 200 else None
    if po_no is None:
        out.find(
            "high",
            f"the buyer could not raise the order after the fact: {r.text[:160]}",
        )
    else:
        db.expire_all()
        for _ in range(4):  # the order's own approval chain, then it is sent
            step = db.execute(
                text("select approver from po_approval_step where po_no = :p and status = 'pending'"),
                {"p": po_no},
            ).scalar()
            if step is None:
                break
            workflow.approve(db, slug, po_no, world.org_logins[step])
        if db.execute(text("select status from po where no = :p"), {"p": po_no}).scalar_one() == "approved":
            dispatch.send_po(db, po_no)
        db.commit()
        r = client.post(f"/api/unordered-receipts/{first_no}/receive", headers=store)
        if r.status_code != 200 or on_hand() - before != D("2"):
            out.find(
                "high",
                f"the receipt on the after-the-fact order did not put exactly 2 into stock: {r.text[:160]}",
            )
        serials = {
            s
            for (s,) in db.execute(
                text("select serial from serial_number where item_code = :i and status = 'received'"),
                {"i": laptop},
            )
        }
        if serials != {"SN-E43-1", "SN-E43-2"}:
            out.find("high", f"the serials kept are {sorted(serials)}")
        expected = lib.D(db.execute(text("select taxable from po where no = :p"), {"p": po_no}).scalar_one())
        if expected != D("124000.00"):
            out.find(
                "high",
                f"the after-the-fact order is {expected}, the oracle says 2 x 62,000.00 = 124000.00",
            )
    r = client.post(
        "/api/unordered-receipts",
        headers=store,
        json={**body, "qty": "1", "serials": "sn-e43-1"},
    )
    if r.status_code != 409 or "already received" not in r.text:
        out.find("high", f"a serial received before was taken again: {r.status_code}")
    client.post(
        f"/api/unordered-receipts/{rejected}/reject",
        headers=pm,
        json={"reason": "Not needed"},
    )
    returned = client.post(
        f"/api/unordered-receipts/{rejected}/returned",
        headers=store,
        json={"challan": "RTN/43", "vehicle": "KA 01 XX 0000"},
    )
    if returned.status_code != 200 or on_hand() - before != D("2"):
        out.find("high", "rejected goods were not simply returned (or they touched stock)")
    # block 9 (spec 9.9 and 10.1): the return is a return document (RTN), dispatched, one mail, no stock
    rtn = returned.json().get("return_no") if returned.status_code == 200 else None
    if not rtn:
        out.find("high", "the rejected receipt without an order made no return document")
    else:
        doc = db.execute(
            text("select source, status, reason from supplier_return where no = :n"), {"n": rtn}
        ).mappings().first()
        if doc is None or (doc["source"], doc["status"]) != ("unordered", "dispatched"):
            out.find("high", f"the return document is {dict(doc) if doc else None}")
        mine = db.execute(
            text("select return_no from unordered_receipt where no = :n"), {"n": rejected}
        ).scalar()
        if mine != rtn:
            out.find("high", f"the receipt without an order names {mine}, the return is {rtn}")
        if not db.execute(
            text("select 1 from audit_event where action = 'return.dispatch' and entity_id = :n"), {"n": rtn}
        ).first():
            out.find("high", f"no return.dispatch audit row for {rtn}")
    listed = {r["id"]: r["status"] for r in client.get("/api/unordered-receipts", headers=store).json()}
    if listed.get(first_no) != "received" or listed.get(rejected) != "returned":
        out.find("high", f"the list says {listed}")
    actions = [
        a for (a,) in db.execute(text("select action from audit_event where entity = 'unordered_receipt' order by id"))
    ]
    for wanted in (
        "unordered.record",
        "unordered.approve",
        "unordered.order",
        "unordered.receive",
        "unordered.reject",
        "unordered.return",
    ):
        if wanted not in actions:
            out.find("high", f"no audit row {wanted}")
    out.metrics["receipts without an order"] = len(listed)
    return out
