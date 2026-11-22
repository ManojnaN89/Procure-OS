"""E74: concurrent store checks (AT-09, part), written without the implementation.

Independence: written from the block 2 spec (sections 6, 11), AT-09 and the dataset only. The oracle (`block2.Stock`)
gives A0 plus a count of 20 castings at Chakan: the same final state whichever runs first (count first: MO-1 takes the
20 and buys 30; check first: MO-1 buys 50, the count then takes 20 off its open request), so the platform must end there
whatever the interleaving.

Scenario: a fresh Apex reset (no button), then two Run store check calls and a count of RM-01 to 20, started together from
three threads (each its own client); repeated on a second reset unless `--quick`. Checks: every call answers (200, or 409
when the platform refuses a second check while one runs); never more set aside than usable - safety stock per place; one
purchase request per material request; every line and the request values equal the oracle (MO-1 Rs 59,930, MO-2
2,09,700, the MRP plan 3,69,490). A second store check changes nothing; tenant check. Mutation: the store-check lock
removed (two checks reading the same free stock set it aside twice and buy twice).
"""

import threading
from decimal import Decimal
from typing import Any

import block1 as b
import block2 as s2
import env
import lib
from lib import Outcome

ID = "E74"
TITLE = "Two store checks and a count at once never set stock aside twice or buy twice (AT-09)"
REQUIREMENT = (
    "AT-09 (part), block 2 spec 6 and 11: concurrent demand and reservations: no double reservation or buying; set aside "
    "never above usable - safety per place; one purchase request per material request; the result equals the serial one"
)
PREFIX = "e74"
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
    co = s2.Co("apex", PREFIX, client)
    bw = s2.Co("brightwave", PREFIX, client)
    bw_before = bw.snapshot2()
    rounds = 1 if ctx.quick else 2
    for n in range(1, rounds + 1):
        if n > 1:
            co.reset()
        _round(out, check, co, n, app)
    s2.tenant_check(check, co, bw, bw_before)
    out.metrics.update({"rounds": rounds, "checks": check.done, "failed": len(check.failed)})
    out.notes.append(
        "Spec gap: whether a second store check started while one runs waits for the lock or is refused is not said; "
        "both 200 and 409 are accepted, the end state must be the serial one either way."
    )
    return out


def _round(out: Outcome, check: b.Checker, co: s2.Co, n: int, app: Any) -> None:
    from fastapi.testclient import TestClient

    store = co.role("store")
    headers = co.headers(store)
    calls = [
        ("POST", "/api/material-requests/check", {}),
        ("POST", "/api/material-requests/check", {}),
        ("POST", "/api/inventory/count", {"item_code": "RM-01", "location": CKN, "counted_qty": "20",
                                          "reason": "eval: found during a check"}),
    ]  # fmt: skip
    gate = threading.Barrier(len(calls))
    answers: list[Any] = [None] * len(calls)

    def go(i: int) -> None:
        c = TestClient(app)
        method, path, body = calls[i]
        gate.wait(timeout=30)
        try:
            answers[i] = c.request(method, path, json=body, headers=headers).status_code
        except Exception as exc:  # noqa: BLE001 - a crash in a thread is a finding, not a lost thread
            answers[i] = f"{type(exc).__name__}: {exc}"

    co.db.rollback()
    threads = [threading.Thread(target=go, args=(i,)) for i in range(len(calls))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=300)
    check(all(a in (200, 409) for a in answers[:2]) and 200 in answers[:2] and answers[2] == 200,
          f"round {n}: the calls answered {answers} (checks 200 or 409, at least one 200; count 200)")  # fmt: skip
    co.first_requests = {x for (x,) in co.q("select distinct request_no from material_request_buy")}
    rank = {label: i for i, (label, _, _) in enumerate(co.mr_labels())}
    st = s2.Stock(co.dataset, s2.RIGHT2, rank)
    st.store_check(tag="first")
    st.count("RM-01", CKN, Decimal(20))
    obs = st.observe()
    check((obs[("MO-1", "RM-01")]["reserved"], obs[("MO-1", "RM-01")]["requested"]) == (20, 30),
          "the oracle does not give MO-1 RM-01 20 / 30 (self-check)", "medium")  # fmt: skip
    over = co.q(
        "select l.item_code, l.location_code, sum(l.reserved_qty), coalesce(max(s.on_hand), 0), "
        "coalesce(max(il.safety_stock), 0) from material_request_line l left join stock_level s on s.item_code = "
        "l.item_code and s.location_code = l.location_code left join item_location il on il.item_code = l.item_code "
        "and il.location_code = l.location_code group by l.item_code, l.location_code "
        "having sum(l.reserved_qty) > coalesce(max(s.on_hand), 0) - coalesce(max(il.safety_stock), 0)"
    )
    check(not over, f"round {n}: more set aside than usable - safety stock at {over}")
    per_mr = {label: co.mr_requests(label) for label, _, _ in co.mr_labels()}
    two = {k: sorted(v) for k, v in per_mr.items() if len(v) != 1}
    check(not two, f"round {n}: material requests without exactly one purchase request: {two}")
    b.agree(check, f"round {n}", st, co, fields=("need", "reserved", "issued", "requested"))
    for mr, value in (("MO-1", Decimal("59930")), ("MO-2", Decimal("209700")), (b.MRP_CKN, Decimal("369490"))):
        mine = st.request_value(mr, "first")
        check(mine == value, f"oracle value of {mr} {mine}, expected {value} (self-check)", "medium")
        got = sum((co.request(x).get("total", b.Z) for x in per_mr.get(mr, set())), b.Z)
        check(b.money(got) == mine, f"round {n}: {mr}'s purchase request is worth {got}, expected {mine}")
    # the lock removed: two checks read the same free stock and both act on it
    racy = s2.Stock(co.dataset, s2.RIGHT2, rank)
    twice = s2.Stock(co.dataset, s2.RIGHT2, rank)
    racy.store_check(tag="first")
    twice.store_check(tag="first")
    for a, c in zip(racy.lines, twice.lines, strict=True):
        a.reserved += c.reserved
        a.covers += c.covers
    racy.count("RM-01", CKN, Decimal(20))
    s2.mutation(check, out, "the store-check lock removed", s2.told(obs, racy.observe(), ("reserved", "requested")))
    s2.second_run(check, co, f"round {n}: a second store check", lambda: co.store_check())
