"""E70: safety stock is protected (task 6.2, C4), written without the implementation.

Independence: written from the block 2 spec (sections 5, 9.3 S1-S2, 11) and the dataset only. The oracle
(`block2.Stock`) takes safety stock from `master/item_location.csv` (and the value the administrators set in the
scenario): free stock for reservation = usable - set aside - safety stock; a manual issue takes at most that; an
approved override sets aside up to its quantity (bounded by what the line lacks and by usable - set aside) and cuts the
line's open request by as much. Replenishment's figure (min-max: order up to the maximum, urgent below safety stock) is
worked out from `item_location.csv` and the item price.

Scenarios, on one Apex reset:
- Setting safety stock (OP-9): an administrator asks for RM-03 safety 20; nothing changes until the second administrator
  approves; the maker and a director are refused; a negative value, a maximum below the reorder point and an unknown item
  are refused.
- S2: the store check leaves 20 impellers: MO-1 40 / 10, the MRP plan 0 / 120, Rs 75,130 and Rs 3,71,690; then the store
  asks to use 10 of safety stock for MO-1's line; the plant head approves: MO-1 50 / 0, its request line cut to 0 and
  cancelled, MO-1's request Rs 72,930, RM-03 available -10 ("below safety stock").
- S1: the storekeeper issues 3 V-belts for lathe 2: refused 409 with available 2 and safety stock 2; the override asked
  with a reason (Sent to the plant head); the asker, the buyer, AP and the purchase manager (an approver outside the
  storekeeper's manager chain) are refused 403; the plant head approves: 3 issued, SP-01 usable 1, below safety; then
  `replenish` orders 14 urgent, Rs 2,940.
Ends with a second store check (nothing changes) and a tenant check. Mutations: safety ignored in reservation; the
override approved by the asker.
"""

from decimal import Decimal
from typing import Any

import block1 as b
import block2 as s2
import env
import lib
from lib import Outcome

ID = "E70"
TITLE = "Safety stock is never set aside or issued without the manager's approved override (C4)"
REQUIREMENT = (
    "Task 6.2, C4 (block 2 spec 5, 9.3 S1-S2): free = usable - set aside - safety stock; a manual issue above that is "
    "409 with needs_override, available and safety_stock; an override is a proposal approved only by the asker's "
    "manager chain holding request.approve (never the asker); approved, it issues or sets aside and cuts the open "
    "request; safety stock changes wait for a second administrator; replenishment after the issue; audit; tenant "
    "isolation"
)
PREFIX = "e70"
CKN = "PLT-CKN"


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
    bw_before = bw.snapshot2()
    s2.scenario_known(check, apex, "S1-breakdown")
    s2.scenario_known(check, apex, "S2-impeller-safety")
    _setting(check, apex)
    st = _s2_check(out, check, apex)
    _s1(out, check, apex, st)
    _s2_override(check, apex, st)
    s2.second_run(check, apex, "Apex after S1/S2", lambda: apex.store_check())
    s2.tenant_check(check, apex, bw, bw_before)

    out.metrics.update({"checks": check.done, "failed": len(check.failed)})
    out.notes += [
        "Spec ambiguity (5.1.5): the material override is 'bounded by the line's short + at_risk', yet S2 expects 10 set "
        "aside on a line whose 10 are already on a timely purchase request (short 0, at risk 0); the oracle bounds it by "
        "what the line lacks (need - issued - set aside), which gives the spec's 50 / 0.",
        "Spec ambiguity (5.3): for kind 'issue', whether `qty` is the whole issue (3) or the part taken from safety stock "
        "(1); the eval asks for the whole issue, as S1 says '3 issued'.",
        "S1 and S2 run on one reset (both start from reset; the V-belts are in no material request), with S2's safety "
        "change made before the first store check, as the scenario says.",
    ]
    return out


def _setting(check: b.Checker, co: s2.Co) -> None:
    """RM-03 safety 20 through the planning change (D-22); refused values first."""
    maker, checker = co.holders("admin")[0], co.holders("admin")[1]
    director = co.designation("Director")
    snap = co.snapshot2()
    for body, want, what in (
        ({"item_code": "RM-03", "location": CKN, "safety_stock": "-1", "reason": "x"}, 422, "a negative safety stock"),
        ({"item_code": "SP-01", "location": CKN, "reorder_point": "20", "max_qty": "10", "reason": "x"}, 422,
         "a maximum below the reorder point"),
        ({"item_code": "RM-99", "location": CKN, "safety_stock": "1", "reason": "x"}, 404, "an unknown item"),
    ):  # fmt: skip
        r = co.call(maker, "POST", "/api/setup/item-planning", body)
        check(r.status_code == want, f"S2 setting: {what} answered {r.status_code}, expected {want}")
    asked = co.call(maker, "POST", "/api/setup/item-planning",
                    {"item_code": "RM-03", "location": CKN, "safety_stock": "20", "reason": "Keep 20 impellers"})  # fmt: skip
    pid = b.js(asked).get("proposal_id")
    check(asked.status_code == 202 and pid is not None, f"S2 setting: the ask answered {asked.status_code} "
          f"{asked.text[:200]}")  # fmt: skip
    if pid is None:
        return
    safety = lambda: b.D(co.one("select safety_stock from item_location where item_code = 'RM-03' and "  # noqa: E731
                                "location_code = :l", l=CKN))  # fmt: skip
    check(safety() == 0, f"S2 setting: RM-03 safety stock is {safety()} before the second administrator approved")
    for who, what in ((maker, "the maker"), (director, "a director (not an administrator)")):
        r = co.call(who, "POST", f"/api/admin/proposals/{pid}/approve", {})
        check(r.status_code in (403, 409), f"S2 setting: {what} approving answered {r.status_code}")
    check(co.snapshot2() == snap, "S2 setting: refused or waiting changes moved something")
    r = co.call(checker, "POST", f"/api/admin/proposals/{pid}/approve", {})
    check(r.status_code == 200 and safety() == 20, f"S2 setting: the second administrator approving answered "
          f"{r.status_code}; RM-03 safety stock now {safety()}")  # fmt: skip
    rows = co.q("select area, key from setup_change order by id desc limit 1")
    check(bool(rows) and rows[0][0] == "item_planning", f"S2 setting: setup_change has {rows}, expected item_planning",
          "medium")  # fmt: skip


def _s2_check(out: Outcome, check: b.Checker, co: s2.Co) -> s2.Stock:
    r = co.base_state()
    check(r.status_code == 200, f"S2: Run store check answered {r.status_code}")
    rank = {label: i for i, (label, _, _) in enumerate(co.mr_labels())}
    st = s2.Stock(co.dataset, s2.RIGHT2, rank)
    st.safety[("RM-03", CKN)] = Decimal(20)
    blind = s2.Stock(co.dataset, s2.Wrong(ignore_safety=True), rank)
    blind.safety[("RM-03", CKN)] = Decimal(20)
    for s in (st, blind):
        s.store_check(tag="first")
    obs = st.observe2()
    pairs = {mr: (obs[(mr, "RM-03")]["reserved"], obs[(mr, "RM-03")]["requested"]) for mr in ("MO-1", b.MRP_CKN)}
    check(pairs == {"MO-1": (40, 10), b.MRP_CKN: (0, 120)}, f"S2: the oracle gives {pairs}, spec MO-1 40 / 10, MRP "
          "0 / 120 (self-check)", "medium")  # fmt: skip
    for mr, value in (("MO-1", Decimal("75130")), (b.MRP_CKN, Decimal("371690"))):
        check(st.request_value(mr, "first") == value, f"S2: oracle value of {mr} {st.request_value(mr, 'first')}, spec "
              f"{value} (self-check)", "medium")  # fmt: skip
        got = sum((co.request(n).get("total", b.Z) for n in co.request_of(mr)), b.Z)
        check(b.money(got) == value, f"S2: {mr}'s request is worth {got}, expected {value}")
    s2.agree2(check, "S2", st, co)
    s2.check_positions(check, "S2", co, st, [("RM-03", CKN)])
    reserved = b.D(co.one("select coalesce(sum(reserved_qty), 0) from material_request_line where item_code = 'RM-03'"))
    check(reserved == 40, f"S2: {reserved} impellers set aside, expected usable 60 - safety 20 = 40")
    s2.mutation(check, out, "safety stock ignored in reservation", s2.told(obs, blind.observe2()))
    s2.agree2(check, "S2 causes", st, co, fields=("causes",), only=[("MO-1", "RM-03")])
    return st


def _approvers(check: b.Checker, co: s2.Co, label: str, pid: Any, asker: str) -> bool:
    """Everyone outside the asker's manager chain is refused; returns whether the asker's own try was made."""
    chain = co.manager_chain(asker)
    outside = [c for c in co.holders("approver") if c not in chain]
    tried_asker = False
    for who, what in ((asker, "the asker"), (co.role("buyer"), "the buyer"), (co.role("ap"), "AP"),
                      *((c, f"{co.people[c]['name']} (outside the manager chain)") for c in outside)):  # fmt: skip
        r = co.call(who, "POST", f"/api/admin/proposals/{pid}/approve", {})
        check(r.status_code == 403, f"{label}: {what} approving the override answered {r.status_code}, expected 403")
        tried_asker |= who == asker
    return tried_asker


def _s1(out: Outcome, check: b.Checker, co: s2.Co, st: s2.Stock) -> None:
    store = co.role("store")
    plant_head = co.designation("Plant Head")
    limit = st.manual_limit("SP-01", CKN)
    check(limit == 2 and st.safety.get(("SP-01", CKN)) == 2, f"S1: the oracle's free V-belts {limit}, safety "
          f"{st.safety.get(('SP-01', CKN))}; spec 2 and 2 (self-check)", "medium")  # fmt: skip
    snap = co.snapshot2()
    r = co.call(store, "POST", "/api/inventory/issue",
                {"item_code": "SP-01", "location": CKN, "qty": "3", "reference": "Lathe 2 main drive"})  # fmt: skip
    body = b.js(r).get("detail") if isinstance(b.js(r).get("detail"), dict) else b.js(r)
    check(r.status_code == 409 and body.get("needs_override") is True and b.dec(body.get("available")) == limit
          and b.dec(body.get("safety_stock")) == 2, f"S1: issuing 3 V-belts answered {r.status_code} {body}, expected 409 "
          f"with needs_override, available {limit}, safety_stock 2")  # fmt: skip
    check(co.snapshot2() == snap, "S1: the refused issue changed something")
    path = "/api/inventory/safety-overrides"
    base = {"kind": "issue", "item_code": "SP-01", "location": CKN, "reference": "Lathe 2 main drive"}
    for extra, want, what in (({"qty": "3", "reason": ""}, 422, "no reason"), ({"qty": "0", "reason": "x"}, 422, "qty 0"),
                              ({"qty": "2", "reason": "x"}, 409, "enough available (nothing to override)")):  # fmt: skip
        r = co.call(store, "POST", path, {**base, **extra})
        check(r.status_code == want, f"S1: an override with {what} answered {r.status_code}, expected {want}")
    r = co.call(store, "POST", path, {**base, "qty": "3", "reason": "Lathe 2 stopped; main drive needs three belts"})
    body = b.js(r)
    pid = body.get("proposal_id")
    check(r.status_code == 202 and body.get("status") == "waiting" and body.get("approver_hint")
          == co.people[plant_head]["name"], f"S1: the override answered {r.status_code} {body}, expected 202 waiting "
          f"for {co.people[plant_head]['name']}")  # fmt: skip
    if pid is None:
        return
    check(co.snapshot2()["movements"] == snap["movements"], "S1: the override moved stock before its approval")
    tried = _approvers(check, co, "S1", pid, store)
    s2.mutation(check, out, "override approved by the asker", tried)
    audits = co.audit_count("stock.safety_override")
    r = co.call(plant_head, "POST", f"/api/admin/proposals/{pid}/approve", {})
    body = b.js(r)
    check(r.status_code == 200 and (body.get("result") or {}).get("movement"), f"S1: the plant head approving answered "
          f"{r.status_code} {body}")  # fmt: skip
    st.issue_by_hand("SP-01", CKN, Decimal(3))
    s2.check_positions(check, "S1", co, st, [("SP-01", CKN)])
    line = co.inventory().get(("SP-01", CKN), {})
    check(line.get("status") == "below_safety", f"S1: SP-01 status {line.get('status')}, expected below_safety")
    check(
        co.audit_count("stock.safety_override") == audits + 1, "S1: the override is not audited (stock.safety_override)"
    )
    issue = co.q("select after::text from audit_event where action = 'stock.issue' order by id desc limit 1")
    check(bool(issue) and "from_safety" in str(issue[0][0]), "S1: the stock.issue audit row has no from_safety",
          "medium")  # fmt: skip
    _replenish(check, co, st)


def _replenish(check: b.Checker, co: s2.Co, st: s2.Stock) -> None:
    """`replenish` after S1: SP-01 up to its maximum from usable 1, urgent below safety stock."""
    plan = next(r for r in b.read_csv(co.dataset / "master" / "item_location.csv") if r["item_code"] == "SP-01")
    usable = st.usable("SP-01", CKN)
    qty = b.D(plan["max_qty"]) - usable
    price = b.D(next(r for r in co.rows("master", "item") if r["code"] == "SP-01")["standard_price"])
    check(qty == 14 and b.money(qty * price) == Decimal("2940"), f"S1 replenishment: the oracle orders {qty} worth "
          f"{qty * price}; spec 14 and Rs 2,940 (self-check)", "medium")  # fmt: skip
    before = co.requests_now()
    code, text_ = b.cli("replenish", co.slug)
    check(code == 0, f"S1 replenishment: `replenish` exited {code}: {text_[-300:]}")
    new = co.requests_now() - before
    hits = [n for n in new if co.request_lines(n).get("SP-01")]
    check(len(hits) == 1 and co.request_lines(hits[0])["SP-01"] == qty, f"S1 replenishment: SP-01 on "
          f"{[(n, co.request_lines(n)) for n in hits]}, expected one request line of {b.fq(qty)}")  # fmt: skip
    if hits:
        head = co.request_head(hits[0])
        check(head.get("priority") == "urgent", f"S1 replenishment: priority {head.get('priority')}, expected urgent")
        value = b.D(co.one("select sum(qty * est_price) from request_line where request_no = :n and item_code = 'SP-01'",
                           n=hits[0]))  # fmt: skip
        check(b.money(value) == b.money(qty * price), f"S1 replenishment: SP-01 line worth {value}, expected 2,940")
    s2.truth_requests(check, co, "S1-breakdown", new)


def _s2_override(check: b.Checker, co: s2.Co, st: s2.Stock) -> None:
    store = co.role("store")
    plant_head = co.designation("Plant Head")
    lines = co.api_lines()
    rm03, rm05 = lines.get(("MO-1", "RM-03"), {}), lines.get(("MO-1", "RM-05"), {})
    mr_no = rm03.get("_card", {}).get("no")
    path = "/api/inventory/safety-overrides"
    for line, qty, want, what in ((rm03, "21", 422, "qty above what safety stock holds"),
                                  (rm05, "1", 409, "a line that lacks nothing")):  # fmt: skip
        r = co.call(store, "POST", path, {"kind": "material", "mr_no": mr_no, "line_no": line.get("line_no"), "qty": qty,
                                          "reason": "x"})  # fmt: skip
        check(r.status_code == want, f"S2 override: {what} answered {r.status_code}, expected {want}")
    first = co.request_of("MO-1")
    r = co.call(store, "POST", path, {"kind": "material", "mr_no": mr_no, "line_no": rm03.get("line_no"), "qty": "10",
                                      "reason": "MO-1 starts first; restock follows"})  # fmt: skip
    pid = b.js(r).get("proposal_id")
    check(r.status_code == 202 and pid is not None, f"S2 override: the ask answered {r.status_code} {r.text[:200]}")
    if pid is None:
        return
    _approvers(check, co, "S2 override", pid, store)
    r = co.call(plant_head, "POST", f"/api/admin/proposals/{pid}/approve", {})
    result = b.js(r).get("result") or {}
    check(r.status_code == 200 and "reserved" in result, f"S2 override: the plant head approving answered "
          f"{r.status_code} {b.js(r)}")  # fmt: skip
    took = st.override("MO-1", "RM-03", Decimal(10))
    obs = st.observe2()
    check(took == 10 and (obs[("MO-1", "RM-03")]["reserved"], obs[("MO-1", "RM-03")]["requested"]) == (50, 0),
          f"S2 override: the oracle took {took}, MO-1 RM-03 {obs[('MO-1', 'RM-03')]} (self-check)", "medium")  # fmt: skip
    s2.agree2(check, "S2 after the override", st, co, fields=("need", "reserved", "issued", "short"))
    s2.check_positions(check, "S2 after the override", co, st, [("RM-03", CKN)])
    avail = b.dec(co.inventory().get(("RM-03", CKN), {}).get("available"))
    check(avail == -10, f"S2 override: RM-03 available {avail}, expected -10")
    rl = b.lines_buying(co, "MO-1", "RM-03")
    check(bool(rl) and all(b.D(x["buys"]) == 0 and x["status"] == "cancelled" for x in rl if x["request_no"] in first),
          f"S2 override: MO-1's impeller request line should be cut to 0 and cancelled, is {rl}")  # fmt: skip
    got = sum((co.request(n).get("total", b.Z) for n in first), b.Z)
    check(b.money(got) == Decimal("72930"), f"S2 override: MO-1's request is worth {got}, expected 72,930")
    audit = co.q(
        "select after::text from audit_event where action = 'material_request.reserve' order by id desc limit 1"
    )
    check(bool(audit) and "from_safety" in str(audit[0][0]), "S2 override: the reservation's audit row has no "
          "from_safety", "medium")  # fmt: skip
    s2.truth_check(check, co, "S2-impeller-safety", [co.observe2()], [obs], items={"RM-03"})
    s2.truth_positions(check, co, "S2-impeller-safety", st, items={"RM-03"})
