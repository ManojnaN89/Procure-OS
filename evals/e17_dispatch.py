"""E17: orders reach the supplier correctly, only when they should, and the acknowledgement rules hold."""

import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from fractions import Fraction

import lib
import pipeline
from fastapi.testclient import TestClient
from lib import Outcome, rows
from sqlalchemy import text

ID = "E17"
TITLE = "Orders are sent to the right supplier with the right amounts, only when approved, and acknowledged on time"
REQUIREMENT = (
    "Plan: an approved order goes to the supplier by email; nothing else does; the supplier's promised date is checked "
    "against the date the goods are needed; an order with no acknowledgement in two working days is overdue"
)


def money(text_value: str) -> Decimal:
    return Decimal(re.sub(r"[^\d.]", "", text_value))


def working_days_after(start: date, n: int) -> date:
    day, left = start, n
    while left:
        day += timedelta(days=1)
        left -= day.weekday() < 5
    return day


def run(ctx) -> Outcome:
    from p2p.main import app

    out = Outcome(True)
    state = pipeline.run_pipeline(lib.build_world("e17"))
    world, db = state.world, state.world.db
    db.commit()  # the API runs in its own session: nothing may stay open here, or it waits for our locks
    client = TestClient(app)
    buyer = world.headers("Buyer")
    box = state.outbox

    pos = {r["no"]: dict(r) for r in db.execute(text("select * from po")).mappings()}
    by_status: dict[str, list[str]] = {}
    for no, po in pos.items():
        by_status.setdefault(po["status"], []).append(no)
    out.metrics["orders by status before sending"] = {k: len(v) for k, v in sorted(by_status.items())}

    # 1. nothing that is not approved can be sent, and nothing goes out when it is refused
    refused_wrong = []
    mails_before = len(box.sent)
    for no, po in pos.items():
        if po["status"] != "approved":
            r = client.post(f"/api/purchase-orders/{no}/send", headers=buyer)
            if r.status_code != 409:
                refused_wrong.append(f"{no} ({po['status']}) answered {r.status_code}")
    if len(box.sent) != mails_before:
        out.find("high", "an email went out for an order that was not approved")
    if refused_wrong:
        out.find(
            "high",
            f"{len(refused_wrong)} order(s) that are not approved were not refused with 409: {refused_wrong[:3]}",
        )

    # 2. every approved order is sent once, to the supplier's main contact, with exactly its amounts
    contacts: dict[str, list[dict]] = {}
    for c in rows("master", "supplier_contact"):
        contacts.setdefault(c["supplier_code"], []).append(c)

    def main_contact(code: str) -> str | None:
        mine = [c for c in contacts.get(code, []) if c["email"]]
        mine.sort(key=lambda c: (c["is_primary"] != "true",))
        return mine[0]["email"] if mine else None

    wrong_to, wrong_amounts, twice, sent = [], [], [], 0
    for no in by_status.get("approved", []):
        po = pos[no]
        before = len(box.sent)
        r = client.post(f"/api/purchase-orders/{no}/send", headers=buyer)
        if r.status_code != 200:
            if po["hold_reason"] or main_contact(po["supplier_code"]) is None:
                continue
            out.find("high", f"{no} could not be sent: {r.status_code} {r.text[:100]}")
            continue
        sent += 1
        mails = box.sent[before:]
        if len(mails) != 1:
            twice.append(f"{no}: {len(mails)} emails")
            continue
        mail = mails[0]
        if mail["to"].lower() != str(main_contact(po["supplier_code"])).lower():
            wrong_to.append(f"{no}: sent to {mail['to']}, expected {main_contact(po['supplier_code'])}")
        lines = (
            db.execute(
                text("select * from po_line where po_no = :n and status <> 'cancelled' order by line_no"), {"n": no}
            )
            .mappings()
            .all()
        )
        total_in_mail = re.search(r"Order total: (.+)", mail["body"])
        taxable_in_mail = re.search(r"Total before tax: (.+)", mail["body"])
        want_taxable = sum((Fraction(l["taxable"]) for l in lines), Fraction(0)) + sum(
            (Fraction(c) for (c,) in db.execute(text("select amount from po_charge where po_no = :n"), {"n": no})),
            Fraction(0),
        )
        problems = []
        if not total_in_mail or money(total_in_mail.group(1)) != Decimal(po["total"]):
            problems.append(f"total {total_in_mail.group(1) if total_in_mail else None} vs {po['total']}")
        if not taxable_in_mail or Fraction(money(taxable_in_mail.group(1))) != want_taxable:
            problems.append(f"taxable {taxable_in_mail.group(1) if taxable_in_mail else None} vs {float(want_taxable)}")
        for l in lines:
            if l["description"] not in mail["body"]:
                problems.append(f"line '{l['description']}' missing")
        if not mail["reply_to"].startswith(f"po+{no}@"):
            problems.append("reply address does not carry the order number")
        if problems:
            wrong_amounts.append(f"{no}: {'; '.join(problems)}")
        again = client.post(f"/api/purchase-orders/{no}/send", headers=buyer)
        if again.status_code != 409 or len(box.sent) != before + 1:
            twice.append(f"{no}: a second send was not refused")
    out.metrics["orders sent"] = sent
    if sent == 0:
        out.find("medium", "no order was sent: the pipeline produced no approved order to test")
    if wrong_to:
        out.find("high", f"{len(wrong_to)} order(s) went to the wrong address: {wrong_to[0]}")
    if wrong_amounts:
        out.find("high", f"{len(wrong_amounts)} order email(s) do not match the order: {wrong_amounts[0]}")
    if twice:
        out.find("high", f"orders sent more than once: {twice[:2]}")

    # 3. acknowledgement: promised dates against need-by dates, counted by hand
    sent_orders = [
        no
        for no in by_status.get("approved", [])
        if db.execute(text("select status from po where no = :n"), {"n": no}).scalar_one() == "sent"
    ]
    ack_wrong, flagged = [], 0
    for i, no in enumerate(sent_orders[:25]):
        need = [
            r["need_by"]
            for r in db.execute(
                text("select need_by from po_line where po_no = :n and status = 'open'"), {"n": no}
            ).mappings()
            if r["need_by"]
        ]
        if not need:
            continue
        offset = [0, 3, 4, -5][i % 4]
        promised = min(need) + timedelta(days=offset)
        r = client.post(
            f"/api/purchase-orders/{no}/acknowledge", json={"promised_date": promised.isoformat()}, headers=buyer
        )
        if r.status_code != 200:
            ack_wrong.append(f"{no}: acknowledge answered {r.status_code}")
            continue
        want = sorted(
            n
            for n, d in enumerate(
                db.execute(
                    text("select line_no, need_by from po_line where po_no = :n and status = 'open' order by line_no"),
                    {"n": no},
                ).all(),
                start=1,
            )
            if d[1] and promised > d[1] + timedelta(days=3)
        )
        got = len(r.json()["warnings"])
        flagged += got > 0
        if got != len(want):
            ack_wrong.append(f"{no}: {got} warning(s), expected {len(want)} (promised {promised})")
        after = db.execute(text("select status from po where no = :n"), {"n": no}).scalar_one()
        if after != "acknowledged":
            ack_wrong.append(f"{no}: status {after}")
        if client.post(f"/api/purchase-orders/{no}/acknowledge", json={}, headers=buyer).status_code != 409:
            ack_wrong.append(f"{no}: acknowledged twice")
    out.metrics["acknowledged"] = min(len(sent_orders), 25)
    out.metrics["late promises flagged"] = flagged
    if ack_wrong:
        out.find("high", f"{len(ack_wrong)} acknowledgement(s) differ from the rule; first: {ack_wrong[0]}")

    # 4. overdue acknowledgement, over many days, against a hand-counted calendar (Monday to Friday, no holidays loaded)
    from p2p.orders import dispatch

    bad_overdue = []
    for start in (date(2026, 11, 6), date(2026, 11, 9), date(2026, 11, 13), date(2026, 12, 31)):
        sent_at = datetime(start.year, start.month, start.day, 10, 0, tzinfo=UTC)
        deadline = working_days_after(start, 2)
        for offset in range(0, 12):
            day = start + timedelta(days=offset)
            if dispatch.ack_overdue(db, sent_at, day) != (day > deadline):
                bad_overdue.append(f"sent {start}, checked {day}: platform {not (day > deadline)}")
    out.metrics["overdue day-checks"] = 4 * 12
    if bad_overdue:
        out.find(
            "high", f"overdue acknowledgement differs from the rule in {len(bad_overdue)} case(s): {bad_overdue[0]}"
        )

    # 5. a blocked supplier or an unverified document stops the send
    ready = next(iter(by_status.get("approved", [])), None)
    other = db.execute(text("select no, supplier_code from po where status = 'approved' limit 1")).first()
    if other is not None:
        no, sup = other
        before = len(box.sent)
        db.execute(text("update supplier set status = 'blocked' where code = :c"), {"c": sup})
        db.commit()
        blocked = client.post(f"/api/purchase-orders/{no}/send", headers=buyer)
        db.execute(text("update supplier set status = 'active' where code = :c"), {"c": sup})
        db.execute(
            text("update supplier_document set verified = false where supplier_code = :c and doc_type = 'pan'"),
            {"c": sup},
        )
        db.commit()
        unverified = client.post(f"/api/purchase-orders/{no}/send", headers=buyer)
        if blocked.status_code != 409 or unverified.status_code != 409 or len(box.sent) != before:
            out.find("high", "an order was sent to a blocked supplier or one with an unverified document")
    elif ready is None:
        out.notes.append("no approved order was left to test the blocked-supplier case")
    return out
