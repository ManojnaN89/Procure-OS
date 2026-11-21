"""E63: an increase while stock is held for another order (task 6.3, Q21), written without the implementation.

Independence: written from the block 1 spec (sections 4.2 "Up", 4.3, 6.3 A4, 6.4 B3, 8), Q21 and the dataset only.
The oracle (`block1.Plant`) replays the base state and applies the increase: the need goes up, and at the end of the
import the store check runs for the changed item-locations only: free stock first (earliest need-by first), never stock
set aside for another order even when the changed order is needed first; what is still short goes on a NEW purchase
request, because a submitted request never grows.

- A4 (Apex, MO-1 raised from 50 to 60 pumps): no free stock for any MO-1 item (the MRP plan, needed later, keeps its
  impellers, terminal boxes and paint): a new request for 10 of each (20 bearings, 18 kg wire, 2 L paint), Rs 31,370;
  MO-1's first request unchanged (Rs 72,930, line for line); MO-2's and the MRP plan's reservations byte for byte.
- A4 with MO-2's request rejected first (the scope probe): the store check after the increase looks only at MO-1's
  items, so MO-2's uncovered impellers and castings for the 0.5 HP pump (items MO-1 does not use) are not bought, while
  its lines on MO-1's items are (all open lines at the changed places).
- B3 (Brightwave, a fifth developer): a new request for one of each kit item (Rs 1,39,900); admin's reservations kept,
  engineering's first request unchanged.
Each ends with the same files again and a tenant check. Mutations: the increase takes a later order's reservation; the
submitted request grown instead of a new one; and the store check not scoped to the changed items.
"""

from decimal import Decimal
from typing import Any

import block1 as b
import env
import lib
from lib import Outcome

ID = "E63"
TITLE = "An increase buys on a new request, never takes stock held for another order, checks only the changed items"
REQUIREMENT = (
    "Task 6.3, Q21 'up re-runs the store check' (block 1 spec 4.2, 4.3): need raised; the store check runs at the end "
    "of the import for the changed item-locations only (all open lines there, earliest need-by first) with submit; "
    "stock set aside for another order is never taken; a submitted request never grows, so the increase is a new "
    "request; other orders' reservations unchanged; a second import changes nothing; tenant isolation"
)
PREFIX = "e63"
SPEC_A4_NEW = {"RM-01": 10, "RM-03": 10, "RM-05": 10, "RM-06": 20, "RM-07": 10, "RM-08": 18, "RM-09": 10, "RM-10": 10,
               "RM-11": 2, "PK-01": 10}  # fmt: skip
SPEC_B3_NEW = {"LP-02": 1, "MN-01": 1, "DK-01": 1, "HS-01": 1, "KB-01": 1, "SW-01": 1}


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
    bw = b.Company("brightwave", PREFIX, client)
    bplant, brank = b.start(bw, check)
    b.check_base(check, bw, bplant, b.SPEC_B0, b.SPEC_B0_VALUES)
    apex = b.Company("apex", PREFIX, client)
    plant, rank = b.start(apex, check)
    b.check_base(check, apex, plant, b.SPEC_A0, b.SPEC_A0_VALUES)

    _increase(out, check, apex, plant, rank, bw, "A4", "MO-1", ("MO-2", b.MRP_CKN), SPEC_A4_NEW, Decimal("31370"))
    apex.reset()
    plant, rank = b.start(apex, check)
    _scope(out, check, apex, plant, rank)
    bplant.mark_buyer(bw.with_buyer())
    _increase(out, check, bw, bplant, brank, apex, "B3", b.ENG, (b.ADM,), SPEC_B3_NEW, Decimal("139900"))

    out.metrics.update({"checks": check.done, "failed": len(check.failed)})
    out.notes.append(
        "The scope probe (MO-2's request rejected before the increase) is this eval's own case for spec 4.2's "
        "'store check for the changed item-locations only; all open lines at those places'."
    )
    return out


def _held(co: b.Company, orders: tuple[str, ...]) -> list[Any]:
    return sorted(
        co.q(
            "select m.order_no, l.item_code, l.reserved_qty::text, l.need_qty::text from material_request_line l "
            "join material_request m on m.no = l.mr_no where m.order_no = any(:o)",
            o=list(orders),
        )
    )


def _lines(co: b.Company, nos: list[str]) -> list[Any]:
    return sorted(
        co.q(
            "select request_no, line_no, item_code, qty::text, status from request_line where request_no = any(:n)",
            n=nos,
        )  # fmt: skip
    )


def _increase(
    out: Outcome,
    check: b.Checker,
    co: b.Company,
    plant: b.Plant,
    rank: dict,
    other: b.Company,
    label: str,
    mr: str,
    others: tuple[str, ...],
    spec_new: dict[str, int],
    spec_value: Decimal,
) -> None:
    raw, parsed = b.scenario_files(co, check, label)
    if not raw:
        return
    other_before = other.snapshot()
    before = plant.observe()
    held_before = _held(co, others)
    first = co.request_of(mr)
    first_lines, first_value = _lines(co, first), [co.request(n).get("total") for n in first]
    requests_before = {n for (n,) in co.q("select no from request")}
    audit0, tasks0 = b.audit_counts(co), b.task_ids(co)
    summary = b.import_change(co, check, label, raw)
    eff = plant.apply(parsed)
    b.check_import(check, co, label, plant, eff, summary, audit0, tasks0)
    b.agree(check, label, plant, co)
    new = {k[1]: v for k, v in eff.new.items() if k[0] == mr}
    check(new == {i: Decimal(q) for i, q in spec_new.items()}, f"{label}: the oracle buys {new} (self-check)", "medium")
    check(plant.new_value(mr) == spec_value, f"{label}: oracle value of the new request {plant.new_value(mr)}, spec "
          f"{spec_value} (self-check)", "medium")  # fmt: skip
    made = sorted({n for (n,) in co.q("select no from request")} - requests_before)
    check(len(made) == 1, f"{label}: the increase made {len(made)} purchase request(s) {made}, expected one new one")
    got = {x["item"]: b.D(x["buys"]) for x in b.lines_buying(co, mr) if x["request_no"] in made}
    check(got == new, f"{label}: the new request buys {got} for {mr}, expected {new}")
    for no in made:
        req = co.request(no)
        check(b.money(req.get("total", b.Z)) == plant.new_value(mr), f"{label}: new request {no} is worth "
              f"{req.get('total')}, expected {plant.new_value(mr)}")  # fmt: skip
        check(req.get("status") not in (None, "draft"), f"{label}: the new request {no} was not submitted "
              f"({req.get('status')})")  # fmt: skip
    check(_lines(co, first) == first_lines and [co.request(n).get("total") for n in first] == first_value,
          f"{label}: {mr}'s first request {first} changed (a submitted request never grows)")  # fmt: skip
    check(_held(co, others) == held_before, f"{label}: the reservations of {others} changed")
    mutations = [
        ("an increase takes stock set aside for a later order", b.Rules(take_held=True)),
        ("the submitted request grown instead of a new one", b.Rules(grow_first=True)),
    ]
    if label == "B3":  # ADM and ENG are both needed on 1 Dec: no order is later, so A4 alone can catch it (eval bug, 21 Nov)
        mutations = mutations[1:]
    for name, rules in mutations:
        wrong = b.Plant(co.dataset, rules, rank)
        wrong.store_check(tag="first")
        wrong.mark_buyer(co.with_buyer())
        wrong.apply(parsed)
        b.mutation(check, out, f"{name} ({label})", plant.observe(), wrong.observe())
    b.compare_truth(check, co, label, before, [plant.observe()], eff)
    b.check_again(check, co, label, raw, eff)
    docs = {"MO-1", "MO-2", "MRP-2027-01"} if co.name == "apex" else {"JP-2026-12", b.ADM, b.ENG}
    b.isolation(check, co, other, docs, other_before)
    out.metrics[f"{label} new request"] = {k: b.fq(v) for k, v in new.items()}


def _scope(out: Outcome, check: b.Checker, co: b.Company, plant: b.Plant, rank: dict) -> None:
    """A4 after MO-2's request was rejected: the check after the increase buys only at the changed item-locations."""
    raw, parsed = b.scenario_files(co, check, "A4")
    if not raw:
        return
    mo2 = co.request_of("MO-2")
    for no in mo2:
        for code in co.holders("approver"):
            if (
                co.call(
                    code, "POST", f"/api/requisitions/{no}/reject", {"reason": "E63: plan to be redone"}
                ).status_code
                == 200
            ):
                break
    statuses = [co.request(n).get("status") for n in mo2]
    if not check(statuses == ["rejected"], f"scope probe: MO-2's request could not be rejected ({statuses}); skipped",
                 "medium"):  # fmt: skip
        return
    plant.drop_covers("MO-2")
    b.agree(check, "scope probe after the rejection", plant, co)
    requests_before = {n for (n,) in co.q("select no from request")}
    summary = b.import_change(co, check, "A4 (scope probe)", raw)
    check(summary["changed"] >= 1, "scope probe: the A4 file changed nothing")
    eff = plant.apply(parsed)
    b.agree(check, "scope probe after the increase", plant, co)
    made = {n for (n,) in co.q("select no from request")} - requests_before
    bought = {x["item"]: b.D(x["buys"]) for x in b.lines_buying(co, "MO-2") if x["request_no"] in made}
    want = {k[1]: v for k, v in eff.new.items() if k[0] == "MO-2"}
    check(bought == want, f"scope probe: the check after the increase bought {bought} for MO-2, expected {want} "
          "(MO-2's lines at MO-1's item-locations, none of RM-02 or RM-04)")  # fmt: skip
    wrong = b.Plant(co.dataset, b.Rules(unscoped_check=True), rank)
    wrong.store_check(tag="first")
    wrong.drop_covers("MO-2")
    wrong.apply(parsed)
    b.mutation(check, out, "the store check after an increase not scoped to the changed items", plant.observe(),
               wrong.observe())  # fmt: skip
