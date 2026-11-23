"""E142: late lines downstream (task 11b.3 line-level dates, spec 8.6.2-8.6.4, K9), written without the implementation.

Independence: written from the block 8 spec (sections 5 8.6, 8, 9 P, Q, K4, K9) and the dataset only. The oracle is
each line's due date (its promised date, else its need-by: `line_due`), late when before the business date with
something still to receive, the oldest late line's days, and the words of the late-order alert written from 8.6.2.

Given a fresh Apex reset (Mon 23 Nov 2026), P acknowledged with 7, 9, 14 Dec (K4), Q (SP-01 20 to SUP-006, need-by Thu
3 Dec) sent on Thu 26 Nov and never acknowledged:
- on Fri 4 Dec Q is late by its need-by: "Goods on <Q> are 1 day late", "1 line late: line 1 (V-belt B-42) due 03 Dec
  2026 (needed)" on the exceptions screen (today's alert misses a line with only a need-by date); the store's list
  shows Q's line due 2026-12-03, `due_source` need_by, late.
- store@ receives P's line 1 in full on Mon 7 Dec; on Thu 10 Dec: "Goods on <P> are 1 day late", "1 line late: line
  2 (Impeller 1 HP) due 09 Dec 2026 (promised)", line 3 not named; the store's list shows P's lines 2 (due 9 Dec,
  promised, late) and 3 (due 14 Dec, promised, not late), and Q (earliest due 3 Dec) before P.
Ends with a tenant check.
Mutations: the promised-only late test (Q never late); one order-level date on the store's list (line 2 due 14 Dec).
"""

from datetime import date
from typing import Any

import block1 as b
import block4 as k
import block8 as e
import env
from lib import Outcome, capture_mail

ID = "E142"
TITLE = "Late lines downstream: the alert, the exceptions row and the store's list work per line, need-by lines too"
REQUIREMENT = (
    "Task 11b.3 (block 8 spec 8.6.2-8.6.4, K9): a line is due on its promised date, else its need-by; the late-order "
    "alert and the exceptions row name the late lines with their due dates (one finding per order, the oldest line's "
    "days); the store's expected receipts carry due, due_source and late per line, earliest due first; tenant check"
)
PREFIX = "e142"


def run(ctx: Any) -> Outcome:
    env.forget_tenants(PREFIX)
    try:
        return _run(ctx)
    finally:
        e.pin(e.TODAY)
        env.forget_tenants(PREFIX)


def _run(ctx: Any) -> Outcome:
    from fastapi.testclient import TestClient

    from p2p.main import app
    from p2p.platform import mail

    out = Outcome(True)
    check = b.Checker(out)
    saved = mail.send_email
    box = capture_mail()
    notes: list[str] = []
    try:
        client = TestClient(app)
        bw = e.Co8("brightwave", PREFIX, client, box)
        apex = e.Co8("apex", PREFIX, client, box)
        e.start(apex)
        start = apex.mark()
        orders, tokens = _k9(out, check, apex, notes)
        e.pin(e.TODAY)
        e.tenant_check8(check, apex, bw, orders, tokens, start)
    finally:
        mail.send_email = saved  # type: ignore[assignment]
    out.metrics.update({"checks": check.done, "failed": len(check.failed)})
    out.notes += sorted(set(notes)) + [
        ("The alert is looked for on the exceptions screen and GET /api/alerts after POST /api/alerts/run on the "
         "business day; the words are compared without case or spacing."),
        ("The store's list is read from GET /api/receiving/waiting: an order by `id` or `po_no` (or `no`), its lines "
         "by `line_no` with `due`, `due_source` and `late` (section 8)."),
    ]
    return out


def late_words(po: str, today: date, late: list[tuple[int, str, date, str]]) -> tuple[str, str]:
    """Spec 8.6.2: (title, detail) of one order's late finding; `late` = (line, item name, due, source) oldest first."""
    days = (today - late[0][2]).days
    title = f"Goods on {po} are {e.count(days, 'day')} late"
    parts = ", ".join(f"line {n} ({name}) due {e.long_date(d)} ({src})" for n, name, d, src in late)
    return title, f"{e.count(len(late), 'line')} late: {parts}"


def _order_of(rows: list[dict[str, Any]], po: str) -> tuple[int, dict[str, Any]]:
    for i, x in enumerate(rows):
        if po in (x.get("id"), x.get("po_no"), x.get("no")):
            return i, x
    return -1, {}


def _lines(order: dict[str, Any]) -> dict[int, dict[str, Any]]:
    return {int(x.get("line_no") or x.get("line") or 0): x for x in e.as_rows(order.get("lines"), "lines") or []}


def _seen(check: b.Checker, co: e.Co8, po: str, title: str, detail: str, label: str, absent: str | None = None) -> bool:
    co.alerts_run()
    rows = co.exception_rows()
    hit = [x for x in rows if e.norm(title) in e.norm(e.blob(x))]
    check(bool(hit) and e.norm(detail) in e.norm(e.blob(hit[0])), f"{label}: the exceptions screen has "
          f"{'no row' if not hit else 'a row without the detail'} '{title}' / '{detail}': "
          f"{e.blob(hit[0])[:300] if hit else [e.blob(x)[:80] for x in rows if po.lower() in e.blob(x)][:3]}")  # fmt: skip
    if absent and hit:
        check(e.norm(absent) not in e.norm(e.blob(hit[0])), f"{label}: the row names {absent}: {e.blob(hit[0])[:300]}")
    alerts = co.call(co.buyer(), "GET", "/api/alerts")
    check(e.norm(title) in e.page(alerts), f"{label}: GET /api/alerts ({alerts.status_code}) lacks '{title}'", "medium")
    return bool(hit)


def _k9(out: Outcome, check: b.Checker, co: e.Co8, notes: list[str]) -> tuple[list[str], list[str | None]]:
    oracle = e.Oracle8(co)
    m = k.Mutations()
    _rule, who = oracle.approver(oracle.p()["total"], frozenset({co.buyer()}))
    po = co.p_order(check, "P", who, notes)
    if not po:
        return [], []
    l1, _ = co.send8(check, po, "P send")
    r = co.accept_code(check, l1, {1: e.DEC7, 2: e.DEC9, 3: e.DEC14}, "K9 P accepted")
    check(r.status_code == 200 and co.promised(po) == {1: e.DEC7, 2: e.DEC9, 3: e.DEC14}, f"K9 precondition: P accepted "
          f"answered {r.status_code}; promised {co.promised(po)}; expected 7, 9, 14 Dec (K4)")  # fmt: skip
    e.pin(e.NOV26)
    q, lq, _ = co.q_order(check, "Q")
    if q:
        co.place(q, 1, "sent_at", e.NOV26)

    # Fri 4 Dec: Q late by its need-by
    q_flagged = None
    if q:
        e.pin(e.DEC4)
        title, detail = late_words(q, e.DEC4, [(1, oracle.item_name(e.Q_ITEM), e.Q_NEED, "needed")])
        q_flagged = _seen(check, co, q, title, detail, "K9 Q on 4 Dec")
        _i, order = _order_of(co.waiting(), q)
        ln = _lines(order).get(1, {})
        got = (e.iso(ln.get("due")), ln.get("due_source"), ln.get("late"))
        check(got == ("2026-12-03", "need_by", True), f"K9: the store's list shows Q's line 1 as {got}; expected "
              "(2026-12-03, need_by, true) (8.6.4)")  # fmt: skip
        m.case("promised_only_late", "K9 Q with only a need-by on 4 Dec", (q_flagged, got[2]), (True, True), (False, False))
    rows = co.exception_rows()
    check(not any(e.norm(f"Goods on {po} are") in e.norm(e.blob(x)) for x in rows), f"K9: on 4 Dec {po} is reported "
          "late before any line is due")  # fmt: skip

    # Mon 7 Dec: line 1 received in full; Thu 10 Dec: line 2 late
    e.pin(e.DEC7)
    r = co.call(co.role("store"), "POST", f"/api/purchase-orders/{po}/receive",
                {"lines": [{"line_no": 1, "qty_received": "100"}], "challan": f"DC-E142-{po[-5:]}"})  # fmt: skip
    co.work()
    check(r.status_code == 200, f"K9: store@ receiving line 1 (100) answered {r.status_code} {r.text[:200]}", "medium")
    e.pin(e.DEC10)
    title, detail = late_words(po, e.DEC10, [(2, oracle.item_name("RM-03"), e.DEC9, "promised")])
    check(title == f"Goods on {po} are 1 day late" and detail == "1 line late: line 2 (Impeller 1 HP) due 09 Dec 2026 "
          "(promised)", f"K9: the oracle's words {title!r} / {detail!r} differ from section 9's (self-check)", "medium")  # fmt: skip
    _seen(check, co, po, title, detail, "K9 P on 10 Dec", absent="line 3")
    rows = co.waiting()
    ip, order = _order_of(rows, po)
    iq, _ = _order_of(rows, q) if q else (-1, {})
    lines = _lines(order)
    got = {n: (e.iso(x.get("due")), x.get("due_source"), x.get("late")) for n, x in lines.items() if n in (2, 3)}
    want = {2: ("2026-12-09", "promised", True), 3: ("2026-12-14", "promised", False)}
    check(got == want, f"K9: the store's list shows P's lines {got}; expected {want} (8.6.4)")
    m.case("order_level_date", "K9 P's line 2 on the store's list", got.get(2, (None,))[0], "2026-12-09", "2026-12-14")
    check(e.iso(order.get("promised_date")) == "2026-12-14", f"K9: the order-level promised_date "
          f"{order.get('promised_date', 'missing')}; the spec keeps the latest line (14 Dec)", "low")  # fmt: skip
    if q:
        check(0 <= iq < ip, f"K9: the store's list has Q at {iq} and P at {ip}; orders go by their earliest open line due "
              "(Q 3 Dec first)", "medium")  # fmt: skip
    m.report(check, out, ["promised_only_late", "order_level_date"])
    out.metrics["K9"] = {"P": po, "Q": q, "P alert": title, "P detail": detail, "Q flagged on 4 Dec": q_flagged}
    return [po, q], [l1, lq]
