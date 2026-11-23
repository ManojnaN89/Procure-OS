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
    "Plan: an approved order goes to the supplier by email; nothing else does; each line's promised date is checked "
    "against the date ordered (block 8: up to 3 days later accepted and flagged, later refused, never in the past or "
    "on a day the delivery place is closed); an order whose version has no acknowledgement in two working days of the "
    "delivery place is overdue"
)


def money(text_value: str) -> Decimal:
    return Decimal(re.sub(r"[^\d.]", "", text_value))


def working_days_after(start: date, n: int, week: int = 5) -> date:
    """n working days after start, counted by hand: Monday to Friday (week 5) or to Saturday (week 6), no holidays."""
    day, left = start, n
    while left:
        day += timedelta(days=1)
        left -= day.weekday() < week
    return day


def week_of(db, po_no: str) -> int:
    """The delivery place's working week, from its record: 6 for Monday to Saturday, else 5."""
    days = db.execute(
        text("select l.working_days from po join location l on l.code = po.ship_to_location where po.no = :n"),
        {"n": po_no},
    ).scalar()
    return 6 if "sat" in str(days or "").lower() else 5


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

    # 3. acknowledgement: a date per line against the date ordered, counted by hand (block 8, 8.5)
    sent_orders = [
        no
        for no in by_status.get("approved", [])
        if db.execute(text("select status from po where no = :n"), {"n": no}).scalar_one() == "sent"
    ]
    ack_wrong, flagged, refused = [], 0, 0
    today = lib.TODAY
    for i, no in enumerate(sent_orders[:25]):
        lines = [
            (int(r["line_no"]), r["need_by"])
            for r in db.execute(
                text("select line_no, need_by from po_line where po_no = :n and status = 'open' order by line_no"),
                {"n": no},
            ).mappings()
        ]
        if not lines or not all(d for _, d in lines):
            continue
        week = week_of(db, no)
        # tries, in turn, until one is accepted: 4 days later (beyond the 3 accepted), a year back (the past), then
        # 3 days later or 1 earlier (accepted, the later lines flagged) or the dates ordered
        for offset in ([4, -400, 3, -1, 0] if i % 2 == 0 else [4, -400, 0]):
            given = {n: d + timedelta(days=offset) for n, d in lines}
            bad = [
                n
                for n, d in given.items()
                if d != dict(lines)[n] and (d < today or d.weekday() >= week or (d - dict(lines)[n]).days > 3)
            ]
            later = [n for n, d in given.items() if d > dict(lines)[n] and n not in bad]
            db.commit()
            r = client.post(
                f"/api/purchase-orders/{no}/acknowledge",
                json={"lines": {str(n): d.isoformat() for n, d in given.items()}, "note": "eval: by phone"},
                headers=buyer,
            )
            after = db.execute(text("select status from po where no = :n"), {"n": no}).scalar_one()
            db.commit()
            if bad:
                refused += 1
                if r.status_code != 422 or after != "sent":
                    ack_wrong.append(f"{no}: dates {given} should be refused, answered {r.status_code}, status {after}")
                continue
            if r.status_code != 200:
                ack_wrong.append(f"{no}: acknowledge answered {r.status_code} {r.text[:120]}")
                break
            got = len(r.json()["warnings"])
            flagged += got > 0
            if got != len(later):
                ack_wrong.append(f"{no}: {got} warning(s), expected {len(later)} (offset {offset})")
            if after != "acknowledged":
                ack_wrong.append(f"{no}: status {after}")
            promised = dict(
                db.execute(
                    text("select line_no, promised_date from po_line where po_no = :n and status = 'open'"), {"n": no}
                ).all()
            )
            db.commit()
            if promised != given:
                ack_wrong.append(f"{no}: promised {promised}, given {given} (one date per line)")
            again = client.post(f"/api/purchase-orders/{no}/acknowledge", json={"note": "again"}, headers=buyer)
            if again.status_code != 409:
                ack_wrong.append(f"{no}: acknowledged twice")
            break
    out.metrics["acknowledged"] = min(len(sent_orders), 25)
    out.metrics["later dates flagged / refused acknowledgements"] = f"{flagged} / {refused}"
    if ack_wrong:
        out.find("high", f"{len(ack_wrong)} acknowledgement(s) differ from the rule; first: {ack_wrong[0]}")

    # 4. overdue acknowledgement, over many days, against a hand-counted calendar of the delivery place (its working
    # week, no holidays loaded), from when the current version was sent
    from p2p.orders import dispatch

    bad_overdue = []
    probe = next(iter(sent_orders), None)
    if probe is not None:
        week = week_of(db, probe)
        for start in (date(2026, 11, 6), date(2026, 11, 9), date(2026, 11, 13), date(2026, 12, 31)):
            sent_at = datetime(start.year, start.month, start.day, 10, 0, tzinfo=UTC)
            db.execute(text("update po set status = 'sent', acknowledged_version = null where no = :n"), {"n": probe})
            db.execute(
                text(
                    "update po_version set sent_at = :t where po_no = :n and version = (select version from po where no = :n)"
                ),
                {"t": sent_at, "n": probe},
            )
            po = dict(db.execute(text("select * from po where no = :n"), {"n": probe}).mappings().one())
            deadline = working_days_after(start, 2, week)
            for offset in range(0, 12):
                day = start + timedelta(days=offset)
                if dispatch.ack_overdue(db, po, day) != (day > deadline):
                    bad_overdue.append(f"sent {start}, checked {day}: platform {not (day > deadline)}")
        db.rollback()
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
