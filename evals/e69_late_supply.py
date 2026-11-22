"""E69: late supply never covers an earlier need (task 6.1, rule Q17), written without the implementation.

Independence: written from the block 2 spec (sections 4, 9.3, 11), Q17 and the dataset only. The oracle (`block2.Stock`)
dates every supply part: a request line not yet ordered by its need-by, an order line by its promise once the supplier
gave one, a date already passed counts as today; the latest arrival is the need-by moved back `receiving_buffer_days`
working days on the location's calendar (Apex Mon-Sat, Brightwave Mon-Fri, holidays from `data/reference`). Timely
supply covers; the late part is not bought again (OP-1) but flags the line at risk, and free stock goes to it first.

Scenarios (spec 9.3), each from a fresh reset:
- T1 / T2 (Apex): MO-1's castings promised for 8 Dec (need 5 Dec): at risk 50, nothing bought again, one late-supply case
  for the buyer (escalating to her manager, due 2 working days later on the Mon-Sat calendar, buffer 0), its Exceptions
  row and the supply-risks API; then 20 castings counted (they go to MO-1, not to the MRP plan), then the lot arrives on
  8 Dec (MO-1 50, the MRP plan 20 / 100 and Rs 3,56,490) and the system resolves the case.
- T1b / T3: promised on the day: nothing at risk, no case; then administrators set a one-day buffer: latest arrival 4 Dec,
  at risk 50, a case with buffer_days 1; closing rules on that case (AP 403, unknown outcome / 'resolved' / no note 422,
  the buyer 200, again 409).
- T4: promised 3 Dec, nothing in: the job on 4 Dec finds nothing at risk, on Monday 7 Dec the supply date is today.
- BT1 / BT2 (Brightwave): laptops, screens and docks promised for 3 Dec (need 1 Dec): three lines at risk, a case for the
  buyer escalating to the CFO; the burn-in decision then sets 2 monitors aside; a Saturday promise with a two-day buffer
  (latest arrival two working days before Tuesday 1 Dec on the Mon-Fri office calendar with its holidays) is at risk
  where calendar days would say it is not.
Each ends with a second evaluation (nothing changes) and a tenant check. Mutations: date-blind cover; the late part bought
again; the buffer in calendar days.
"""

from datetime import date
from decimal import Decimal
from typing import Any

import block1 as b
import block2 as s2
import env
import lib
from lib import Outcome

ID = "E69"
TITLE = "Late supply never covers an earlier need: at risk, not bought twice, one case for the buyer (Q17)"
REQUIREMENT = (
    "Task 6.1, Q17 (block 2 spec 4, 9.3 T1-T4, BT1-BT2): supply counts only if due by the latest arrival (need-by less "
    "the buffer in working days on the location calendar; a passed date is today); free stock goes first to short + at "
    "risk; only the short part is bought; one late_supply case per PO and material request (owner the buyer, escalation "
    "to the manager, due late_supply_case_days, buffer recorded), resolved by the system, closing rules; Exceptions row; "
    "tenant isolation"
)
PREFIX = "e69"
CKN, BLR = "PLT-CKN", "OFF-BLR"
ACK_DAY = date(2026, 11, 23)  # a Monday (spec 9.3 T1: business date 23 Nov)


def run(ctx: Any) -> Outcome:
    env.forget_tenants(PREFIX)
    try:
        return _run(ctx)
    finally:
        env.forget_tenants(PREFIX)


def _run(ctx: Any) -> Outcome:
    from fastapi.testclient import TestClient

    from p2p.main import app

    out = Outcome(True)
    check = b.Checker(out)
    lib.capture_mail()
    client = TestClient(app)

    apex = s2.Co("apex", PREFIX, client)
    bw = s2.Co("brightwave", PREFIX, client)
    _t1_t2(out, check, apex, bw)
    apex.reset()
    _t1b_t3(out, check, apex)
    apex.reset()
    _t4(check, apex)
    _bt1(out, check, bw, apex)
    bw.reset()
    _bt2(out, check, bw)

    out.metrics.update({"checks": check.done, "failed": len(check.failed)})
    out.notes += [
        "Spec ambiguity: a passed date counts as today for every supply part, so on 7 Dec (T4) MO-1's lines still out for "
        "quotation (dated by their 5 Dec need-by) are at risk too; the oracle applies the rule to every part and "
        "compares every line; the case is checked only for the order (one per PO and material request).",
        "Spec gap: the supplier's promise is per order (OP-23), so every open line of the acknowledged order takes it.",
        "Spec gap (calendar): 24 Nov 2026 is Guru Nanak's Birthday (central gazetted, closed), so T1's case made on Monday "
        "23 Nov is due Thursday 26 Nov on the Mon-Sat plant calendar, not 25 Nov as 9.3 says; the eval counts on the "
        "location calendar (block 1 decision). BT2: 27 Nov is Kanakadasa Jayanthi in Karnataka, so the latest arrival "
        "is 26 Nov, not 27 Nov; the outcome (at risk) is the same.",
        "Spec gap: the API shape of `lines[].promised` / `latest_arrival` in a case is not fixed; dates are read as ISO.",
    ]
    return out


# ---- setup shared by the scenarios ------------------------------------------------------------------------------------


def _ordered(check: b.Checker, co: s2.Co, mr: str, item: str) -> tuple[s2.Stock, str | None]:
    """Base state, the material request's purchase request approved through its chain and bought (`source`, `order`);
    the order holding `item` approved through its tier and sent."""
    st, _ = s2.start2(co, check)
    for no in co.request_of(mr):
        status = co.approve(no)
        check(status == "approved", f"{co.name}: {mr}'s request {no} is {status} after its approvers acted")
    co.source()
    st.mark_buyer(co.with_buyer())
    found = co.po_of(mr, item)
    if not check(found is not None, f"{co.name}: no purchase order buys {mr}'s {item} after sourcing"):
        return st, None
    status = co.advance_po(found[0])
    check(status == "sent", f"{co.name}: order {found[0]} is {status}, expected sent")
    return st, found[0]


def _ack(check: b.Checker, co: s2.Co, st: s2.Stock, po: str, mr: str, items: list[str], promised: date) -> None:
    r = co.acknowledge(po, promised, ACK_DAY)
    check(r.status_code == 200, f"{co.name}: acknowledging {po} answered {r.status_code}: {r.text[:200]}")
    st.today = ACK_DAY
    for item in items:
        st.promise(mr, item, promised)
    # the acknowledgement is an event: evaluation and a store check scoped to the lines pegged to the order (spec 6.5.2)
    st.store_check({(ln.item, ln.loc) for ln in st.lines if ln.mr == mr and ln.item in items}, automatic=True)


def _observe(co: s2.Co, on: date) -> dict:
    with b.on_day(on):
        return co.observe2()


def _cases_for(co: s2.Co, po: str, open_only: bool = True) -> list[dict[str, Any]]:
    return [c for c in co.risks() if c["po_no"] == po and (c["closed_at"] is None or not open_only)]


def _case(check: b.Checker, co: s2.Co, label: str, po: str, mr: str, at_risk: dict[str, Decimal], promised: date,
          latest: date, buffer: int) -> dict[str, Any] | None:  # fmt: skip
    """One open late-supply case for (PO, MR): lines, owner, escalation, due date, buffer, title."""
    cases = _cases_for(co, po)
    mr_no = dict((lbl, n) for lbl, n, _ in co.mr_labels()).get(mr)
    if not check(len(cases) == 1, f"{label}: {len(cases)} open late-supply case(s) on {po}, expected one"):
        return cases[0] if cases else None
    c = cases[0]
    check(c["mr_no"] == mr_no and c["kind"] == "late_supply", f"{label}: case {c['task_id']} is kind {c['kind']} for "
          f"{c['mr_no']}, expected late_supply for {mr_no}")  # fmt: skip
    got = {str(x.get("item_code")): b.D(x.get("at_risk")) for x in c["lines"]}
    check(got == at_risk, f"{label}: case lines at risk {got}, expected {at_risk}")
    dates = {(b.day(x.get("promised")), b.day(x.get("latest_arrival"))) for x in c["lines"]}
    check(dates == {(promised, latest)}, f"{label}: case lines promised / latest arrival {dates}, expected "
          f"{{({promised}, {latest})}}")  # fmt: skip
    check(c["buffer_days"] == buffer, f"{label}: case buffer_days {c['buffer_days']}, expected {buffer}")
    buyer = co.role("buyer")
    check(co.code_of(c["assignee"]) == buyer, f"{label}: case assigned to {c['assignee']}, expected "
          f"{co.people[buyer]['name']}")  # fmt: skip
    first = c["chain"][0] if c["chain"] else None
    if isinstance(first, dict):
        first = first.get("user_id") or first.get("id") or first.get("code") or first.get("email")
    manager = co.people[buyer].get("manager_code")
    check(co.code_of(first) == manager, f"{label}: case escalates to {first}, expected "
          f"{co.people.get(manager or '', {}).get('name')} (the buyer's manager)")  # fmt: skip
    days = int(
        b.D(next((r["value"] for r in co.rows("master", "setting") if r["key"] == "late_supply_case_days"), "2"))
    )
    cal = s2.calendars(co.dataset)[co.location()["code"]]
    check(b.due_ok(cal, c["created_at"], c["due_at"], days, anchors=(ACK_DAY,)), f"{label}: case made "
          f"{c['created_at']} due {c['due_at']}, not {days} working days later on {co.location().get('working_days')}")  # fmt: skip
    title = str(c["title"])
    check(po in title and mr in title, f"{label}: case title {title!r} should name {po} and {mr}", "medium")
    return c


def _exceptions(check: b.Checker, co: s2.Co, label: str, c: dict[str, Any], price: Decimal, qty: Decimal) -> None:
    buyer, store = co.role("buyer"), co.role("store")
    rows = {r.get("ref"): r for r in co.exceptions_of(co.holders("admin")[0], "late_supply")}
    row = rows.get(c["po_no"])
    if not check(row is not None, f"{label}: no Exceptions row (kind late_supply) for {c['po_no']}"):
        return
    expect = {
        "kind_title": "Supply promised too late",
        "link": f"#/purchase-orders/{c['po_no']}",
        "owner": f"Buyer: {co.people[buyer]['name']}",
        "reason": c["title"],
    }
    bad = {k: row.get(k) for k, v in expect.items() if row.get(k) != v}
    check(not bad, f"{label}: Exceptions row has {bad}, expected {expect}")
    amount = b.money(qty * price)
    check(b.dec(row.get("amount")) in (amount, amount * 100), f"{label}: Exceptions amount {row.get('amount')}, "
          f"expected {amount} (at risk x order price)")  # fmt: skip
    check(bool(row.get("next_step")), f"{label}: the Exceptions row has no next step", "medium")
    mine = {r.get("ref") for r in co.exceptions_of(buyer, "late_supply", mine=True) if r.get("mine")}
    check(c["po_no"] in mine, f"{label}: the buyer's 'mine' Exceptions miss {c['po_no']}")
    theirs = {r.get("ref") for r in co.exceptions_of(store, "late_supply", mine=True) if r.get("mine")}
    check(c["po_no"] not in theirs, f"{label}: the store's 'mine' Exceptions list the buyer's case", "medium")
    status, listed = co.supply_risks_api(buyer, "open")
    hit = [x for x in listed if x.get("task_id") == c["task_id"]]
    check(status == 200 and len(hit) == 1 and hit[0].get("po_no") == c["po_no"] and hit[0].get("order_no"),
          f"{label}: GET /api/supply-risks?status=open answered {status} with {hit}")  # fmt: skip
    check(co.supply_risks_api(buyer, "maybe")[0] == 422, f"{label}: a bad status should answer 422")


def _no_rebuy(check: b.Checker, co: s2.Co, label: str, before: set[str], item: str) -> None:
    new = co.requests_now() - before
    bought = {n: co.request_lines(n) for n in new}
    again = {n: lines for n, lines in bought.items() if item in lines}
    check(not again, f"{label}: the late part was bought again: {again}")


# ---- the scenarios ----------------------------------------------------------------------------------------------------


def _t1_t2(out: Outcome, check: b.Checker, co: s2.Co, other: s2.Co) -> None:
    s2.scenario_known(check, co, "T1-late")
    s2.scenario_known(check, co, "T2-count-and-arrival")
    st, po = _ordered(check, co, "MO-1", "RM-01")
    if po is None:
        return
    other_before = other.snapshot2()
    before = co.requests_now()
    pre = s2.copy_state(st, st.wrong)
    _ack(check, co, st, po, "MO-1", ["RM-01"], date(2026, 12, 8))
    obs = st.observe2()
    t1 = obs[("MO-1", "RM-01")]
    check((t1["timely"], t1["late"], t1["at_risk"], t1["short"]) == (0, 50, 50, 0), f"T1: the oracle gives MO-1 RM-01 "
          f"{t1}, spec timely 0, late 50, at risk 50, short 0 (self-check)", "medium")  # fmt: skip
    states = [_observe(co, ACK_DAY)]
    s2.agree2(check, "T1", st, co)
    s2.agree2(check, "T1 causes", st, co, fields=("causes",), only=[("MO-1", "RM-01")])
    _no_rebuy(check, co, "T1", before, "RM-01")
    c = _case(check, co, "T1", po, "MO-1", {"RM-01": Decimal(50)}, date(2026, 12, 8), date(2026, 12, 5), 0)
    if c is not None:
        price = b.D(co.one("select unit_price from po_line where po_no = :p and item_code = 'RM-01'", p=po))
        _exceptions(check, co, "T1", c, price, Decimal(50))
    card = co.api_lines().get(("MO-1", "RM-01"), {}).get("_card", {})
    check(card.get("at_risk") is True, f"T1: MO-1's card at_risk {card.get('at_risk')}, expected true", "medium")
    supply = co.api_lines().get(("MO-1", "RM-01"), {}).get("supply") or []
    po_parts = [x for x in supply if x.get("doc_no") == po]
    check(len(po_parts) == 1 and po_parts[0].get("date_kind") == "promised" and po_parts[0].get("timely") is False
          and b.day(po_parts[0].get("date")) == date(2026, 12, 8), f"T1: MO-1 RM-01's supply {supply}, expected the order "
          "promised 8 Dec, not timely")  # fmt: skip
    # mutations on T1: date-blind cover; the late part bought again
    for name, flag in (("date-blind cover", "date_blind"), ("the late part bought again", "buy_late")):
        wrong = s2.copy_state(pre, s2.Wrong(**{flag: True}))
        wrong.today = ACK_DAY
        wrong.promise("MO-1", "RM-01", date(2026, 12, 8))
        wrong.store_check({("RM-01", CKN)}, automatic=True)
        right = s2.copy_state(pre, s2.RIGHT2)
        right.today = ACK_DAY
        right.promise("MO-1", "RM-01", date(2026, 12, 8))
        right.store_check({("RM-01", CKN)}, automatic=True)
        s2.mutation(check, out, name, s2.told(right.observe2(), wrong.observe2()))
    # a second evaluation (the button) opens no second case and buys nothing
    s2.second_run(check, co, "T1 again", lambda: _button(co, ACK_DAY))
    check(len(_cases_for(co, po)) == 1, "T1: a re-evaluation opened a second case")
    # T2: twenty castings found by a count
    with b.on_day(ACK_DAY):
        r = co.call(co.role("store"), "POST", "/api/inventory/count",
                    {"item_code": "RM-01", "location": CKN, "counted_qty": "20", "reason": "Found in bay 4"})  # fmt: skip
    check(r.status_code == 200, f"T2: the count answered {r.status_code}: {r.text[:200]}")
    blind = s2.copy_state(st, s2.Wrong(date_blind=True))
    st.count("RM-01", CKN, Decimal(20))
    blind.count("RM-01", CKN, Decimal(20))
    obs = st.observe2()
    check((obs[("MO-1", "RM-01")]["reserved"], obs[("MO-1", "RM-01")]["at_risk"]) == (20, 30)
          and (obs[(b.MRP_CKN, "RM-01")]["reserved"], obs[(b.MRP_CKN, "RM-01")]["requested"]) == (0, 120),
          "T2: the oracle does not give MO-1 20 set aside, at risk 30, MRP 0 / 120 (self-check)", "medium")  # fmt: skip
    s2.mutation(check, out, "date-blind cover (the count goes to the MRP plan)", s2.told(obs, blind.observe2()))
    states.append(_observe(co, ACK_DAY))
    s2.agree2(check, "T2 after the count", st, co)
    check(len(_cases_for(co, po)) == 1, "T2: after the count there is not exactly one open case on the order")
    # the late lot arrives on 8 Dec
    _, line = co.po_of("MO-1", "RM-01") or (po, 1)
    arrive = date(2026, 12, 8)
    r = co.receive(po, line, "50", arrive)
    check(r.status_code == 200, f"T2: the receipt answered {r.status_code}: {r.text[:200]}")
    st.today = arrive
    st.arrive("MO-1", "RM-01", Decimal(50))
    obs = st.observe2()
    check(obs[("MO-1", "RM-01")]["reserved"] == 50 and (obs[(b.MRP_CKN, "RM-01")]["reserved"],
          obs[(b.MRP_CKN, "RM-01")]["requested"]) == (20, 100), "T2: the oracle does not give MO-1 50, MRP 20 / 100 "
          "(self-check)", "medium")  # fmt: skip
    with b.on_day(arrive):
        s2.agree2(check, "T2 after the arrival", st, co, fields=("need", "reserved", "issued"))
        states.append(co.observe2())
    mrp = co.request_of(b.MRP_CKN)
    got = sum((co.request(n).get("total", b.Z) for n in mrp), b.Z)
    check(b.money(got) == Decimal("356490"), f"T2: the MRP plan's request is worth {got}, expected 3,56,490")
    # "a later evaluation" resolves the case: a receipt is not in the spec's list of events, so the daily job runs
    if _cases_for(co, po):
        co.run_job(arrive)
        st.store_check(None, automatic=True)
    done = _cases_for(co, po, open_only=False)
    check(len(done) == 1 and done[0]["outcome"] == "resolved" and done[0]["closed_at"] is not None
          and done[0]["status"] == "done", f"T2: after the arrival the case is "
          f"{[(c['outcome'], c['status']) for c in done]}, expected resolved by the system")  # fmt: skip
    s2.truth_check(check, co, "T1-late", states[:1], None)
    s2.truth_check(check, co, "T2-count-and-arrival", states, None)
    s2.tenant_check(check, co, other, other_before)


def _button(co: s2.Co, on: date) -> None:
    with b.on_day(on):
        co.store_check()


def _t1b_t3(out: Outcome, check: b.Checker, co: s2.Co) -> None:
    s2.scenario_known(check, co, "T1b-on-time")
    s2.scenario_known(check, co, "T3-buffer")
    st, po = _ordered(check, co, "MO-1", "RM-01")
    if po is None:
        return
    _ack(check, co, st, po, "MO-1", ["RM-01"], date(2026, 12, 5))
    s2.agree2(check, "T1b", st, co)
    check(not _cases_for(co, po), "T1b: promised on the day, yet a late-supply case is open")
    tasks = len(co.risks())
    with b.on_day(ACK_DAY):
        asked, approved = co.policy("receiving_buffer_days", "1")
    check(asked.status_code == 202 and approved is not None and approved.status_code == 200, f"T3: the buffer change "
          f"answered {asked.status_code} / {getattr(approved, 'status_code', None)}")  # fmt: skip
    st.buffer = 1
    st.store_check({(ln.item, ln.loc) for ln in st.lines}, automatic=True)
    obs = st.observe2()
    check(obs[("MO-1", "RM-01")]["latest"] == date(2026, 12, 4) and obs[("MO-1", "RM-01")]["at_risk"] == 50,
          "T3: the oracle does not give latest arrival 4 Dec and at risk 50 (self-check)", "medium")  # fmt: skip
    with b.on_day(ACK_DAY):
        s2.agree2(check, "T3", st, co)
        got = co.api_lines().get(("MO-1", "RM-01"), {})
    check(b.day(got.get("latest_arrival")) == date(2026, 12, 4), f"T3: latest_arrival {got.get('latest_arrival')}, "
          "expected 2026-12-04")  # fmt: skip
    check(len(co.risks()) == tasks + 1, "T3: the buffer change applied did not open exactly one case (event)")
    c = _case(check, co, "T3", po, "MO-1", {"RM-01": Decimal(50)}, date(2026, 12, 5), date(2026, 12, 4), 1)
    s2.truth_check(check, co, "T3-buffer", [_observe(co, ACK_DAY)], None)
    if c is not None:
        _close(check, co, "T3", c)


def _close(check: b.Checker, co: s2.Co, label: str, c: dict[str, Any]) -> None:
    path = f"/api/supply-risks/{c['task_id']}/close"
    buyer, ap = co.role("buyer"), co.role("ap")
    closes = co.last_audit_id()
    for by, body, want, what in (
        (ap, {"outcome": "expedited", "note": "x"}, 403, "AP (not the assignee, no po.create)"),
        (buyer, {"outcome": "shredded", "note": "x"}, 422, "an unknown outcome"),
        (buyer, {"outcome": "resolved", "note": "x"}, 422, "'resolved' given by a person"),
        (buyer, {"outcome": "accepted_late", "note": ""}, 422, "accepted_late without a note"),
        (buyer, {"outcome": "bought_elsewhere"}, 422, "bought_elsewhere without a note"),
    ):
        r = co.call(by, "POST", path, body)
        check(r.status_code == want, f"{label}: closing with {what} answered {r.status_code}, expected {want}")
    r = co.call(buyer, "POST", path, {"outcome": "expedited", "note": ""})
    body = b.js(r)
    check(r.status_code == 200 and body.get("status") == "done" and body.get("outcome") == "expedited",
          f"{label}: the buyer closing as expedited answered {r.status_code} {body}")  # fmt: skip
    row = co.q("select outcome, closed_by, closed_at from supply_risk where task_id = :t", t=c["task_id"])
    check(bool(row) and row[0][0] == "expedited" and row[0][2] is not None, f"{label}: the case after closing: {row}")
    r = co.call(buyer, "POST", path, {"outcome": "expedited", "note": "again"})
    check(r.status_code == 409, f"{label}: closing a closed case answered {r.status_code}, expected 409")
    check(bool(co.q("select 1 from audit_event where id > :i and entity_id = :t", i=closes, t=str(c["task_id"]))),
          f"{label}: closing the case left no audit row", "medium")  # fmt: skip


def _t4(check: b.Checker, co: s2.Co) -> None:
    s2.scenario_known(check, co, "T4-overdue")
    st, po = _ordered(check, co, "MO-1", "RM-01")
    if po is None:
        return
    _ack(check, co, st, po, "MO-1", ["RM-01"], date(2026, 12, 3))
    states = []
    for d, risk in ((date(2026, 12, 4), 0), (date(2026, 12, 7), 50)):
        taken = co.run_job(d)
        check(taken >= 1, f"T4: the store_check job did not run on {d}")
        st.today = d
        st.store_check(None, automatic=True)
        obs = st.observe2()
        check(obs[("MO-1", "RM-01")]["at_risk"] == risk, f"T4 {d}: the oracle gives at risk "
              f"{obs[('MO-1', 'RM-01')]['at_risk']}, spec {risk} (self-check)", "medium")  # fmt: skip
        with b.on_day(d):
            s2.agree2(check, f"T4 on {d}", st, co)
            states.append(co.observe2())
        check(bool(_cases_for(co, po)) == bool(risk), f"T4 {d}: late-supply case open {bool(_cases_for(co, po))}, "
              f"expected {bool(risk)}")  # fmt: skip
    s2.truth_check(check, co, "T4-overdue", states, None)


def _bt1(out: Outcome, check: b.Checker, co: s2.Co, other: s2.Co) -> None:
    s2.scenario_known(check, co, "BT1-late")
    st, po = _ordered(check, co, b.ENG, "LP-02")
    if po is None:
        return
    items = ["LP-02", "MN-01", "DK-01"]
    same = {i: (co.po_of(b.ENG, i) or ("", 0))[0] for i in items}
    check(set(same.values()) == {po}, f"BT1: laptops, screens and docks are on {same}, expected one CNT-0001 order",
          "medium")  # fmt: skip
    other_before = other.snapshot2()
    _ack(check, co, st, po, b.ENG, [i for i in items if same[i] == po], date(2026, 12, 3))
    obs = st.observe2()
    want = {"LP-02": Decimal(3), "MN-01": Decimal(4), "DK-01": Decimal(4)}
    mine = {i: obs[(b.ENG, i)]["at_risk"] for i in items}
    check(mine == want, f"BT1: the oracle's at risk {mine}, spec {want} (self-check)", "medium")
    s2.agree2(check, "BT1", st, co)
    _case(check, co, "BT1", po, b.ENG, want, date(2026, 12, 3), date(2026, 12, 1), 0)
    lot = co.hold_of("MN-01", "quarantine", BLR)
    if not check(lot is not None, "BT1: H-BWT-01 is not in the holds list"):
        return
    blind = s2.copy_state(st, s2.Wrong(date_blind=True))
    r = co.decide(co.role("store"), lot["id"], "2", "0", "Burn-in test passed")
    check(r.status_code == 200, f"BT1: the burn-in decision answered {r.status_code}: {r.text[:200]}")
    st.decide("MN-01", BLR, "quarantine", Decimal(2), b.Z)
    blind.decide("MN-01", BLR, "quarantine", Decimal(2), b.Z)
    obs = st.observe2()
    check((obs[(b.ENG, "MN-01")]["reserved"], obs[(b.ENG, "MN-01")]["at_risk"]) == (2, 2), "BT1: the oracle does not "
          "give engineering's monitors 2 set aside, at risk 2 (self-check)", "medium")  # fmt: skip
    s2.mutation(check, out, "date-blind cover (Brightwave: nothing set aside)", s2.told(obs, blind.observe2()))
    s2.agree2(check, "BT1 after the burn-in", st, co)
    s2.truth_check(check, co, "BT1-late", [co.observe2()], None, items={"LP-02", "MN-01", "DK-01"})
    s2.tenant_check(check, co, other, other_before)


def _bt2(out: Outcome, check: b.Checker, co: s2.Co) -> None:
    s2.scenario_known(check, co, "BT2-buffer")
    st, po = _ordered(check, co, b.ENG, "LP-02")
    if po is None:
        return
    items = [i for i in ("LP-02", "MN-01", "DK-01") if (co.po_of(b.ENG, i) or ("", 0))[0] == po]
    _ack(check, co, st, po, b.ENG, items, date(2026, 11, 28))
    s2.agree2(check, "BT2 buffer 0", st, co)
    check(not _cases_for(co, po), "BT2: a Saturday promise before a Tuesday need opened a case with buffer 0")
    with b.on_day(ACK_DAY):
        asked, approved = co.policy("receiving_buffer_days", "2")
    check(asked.status_code == 202 and approved is not None and approved.status_code == 200, f"BT2: the buffer change "
          f"answered {asked.status_code} / {getattr(approved, 'status_code', None)}")  # fmt: skip
    cal = s2.copy_state(st, s2.Wrong(calendar_buffer=True))
    for s in (st, cal):
        s.buffer = 2
        s.store_check({(ln.item, ln.loc) for ln in s.lines}, automatic=True)
    obs = st.observe2()
    # spec 9.3 says Friday 27 Nov, but 27 Nov 2026 is Kanakadasa Jayanthi, closed in Karnataka (data/reference,
    # holiday scope central;29): two working days before Tuesday 1 Dec is Thursday 26 Nov on the office's own calendar
    latest = obs[(b.ENG, "LP-02")]["latest"]
    check(latest in (date(2026, 11, 26), date(2026, 11, 27)), f"BT2: the oracle's latest arrival {latest} (self-check)",
          "medium")  # fmt: skip
    check(latest == date(2026, 11, 27), f"BT2: spec 9.3 says latest arrival 27 Nov; the location calendar gives "
          f"{latest} (27 Nov is a Karnataka holiday): spec gap", "low")  # fmt: skip
    s2.mutation(check, out, "the buffer in calendar days", s2.told(obs, cal.observe2()))
    with b.on_day(ACK_DAY):
        s2.agree2(check, "BT2 buffer 2", st, co)
    want = {i: obs[(b.ENG, i)]["at_risk"] for i in items if obs[(b.ENG, i)]["at_risk"]}
    _case(check, co, "BT2", po, b.ENG, want, date(2026, 11, 28), latest, 2)
    s2.truth_check(check, co, "BT2-buffer", [_observe(co, ACK_DAY)], None, items={"LP-02", "MN-01", "DK-01"})
