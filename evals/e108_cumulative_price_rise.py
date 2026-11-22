"""E108: price rises add up from the first approved price (DF-08, OP-3, OP-16), written without the implementation.

Independence: written from the block 4 spec (sections 4.2, 4.3, 6.3 V4 and V4b, 7) and the dataset only. The oracle
(`block4.price_rule`) judges a supplier's price exactly from the first approved price with `supplier_price_pct` from
`tolerance.csv`, and values the variance proposal at (new - base) x quantity; who decides comes from the
`price_variance` rule row and the company profile.

V4 on a fresh Apex reset: Q(120, 905.00), approved, sent. The supplier asks 920.00 from its link: base 905, within
(1.66%); the buyer accepts: version 2 (1,30,272.00), material (price up; the total went up), back to the purchase
manager with version 2, who approves; sent. The supplier asks 925.00: out (2.21% from 905, though 0.54% from 920); the
buyer's acceptance does not apply it (still 920.00, version 2) but makes the `po.supplier_price` proposal for the
purchase manager worth 2,400.00; he accepts: version 3 (1,30,980.00) and a new round for him (the price approver is not
a maker).
V4b: another Q(120, 905.00) to version 2 at 920.00, sent; 923.10 is within (exactly 2.00%): version 3 (1,30,710.96),
approved, sent; 923.11 is out, value 2,173.20.
Ends with a tenant check. Mutations: the base is the current price (925 within, value 600.00); the variance valued from
the current price.
"""

from decimal import Decimal
from typing import Any

import block1 as b
import block4 as k
import env
import lib
from block1 import D, js
from lib import Outcome

ID = "E108"
TITLE = "Supplier price rises add up from the first approved price; above 2% the price approver decides"
REQUIREMENT = (
    "DF-08, OP-3, OP-16 (block 4 spec 4.2, 4.3, V4, V4b): a supplier's price is judged exactly from the first approved "
    "price; the buyer alone accepts up to supplier_price_pct in all; above it the buyer's acceptance makes the "
    "po.supplier_price proposal valued (new - base) x quantity for the price_variance person; C1 kept; base_value and "
    "rise_pct shown; amounts against the oracle and the truth file; tenant check"
)
PREFIX = "e108"


def run(ctx: Any) -> Outcome:
    env.forget_tenants(PREFIX)
    try:
        return _run(ctx)
    finally:
        env.forget_tenants(PREFIX)


def _run(ctx: Any) -> Outcome:
    from fastapi.testclient import TestClient
    from p2p.main import app
    from p2p.platform import mail

    out = Outcome(True)
    check = b.Checker(out)
    saved = mail.send_email
    box = lib.capture_mail()
    try:
        client = TestClient(app)
        bw = k.Co("brightwave", PREFIX, client, box)
        apex = k.Co("apex", PREFIX, client, box)
        start = apex.mark()
        oracle = k.Oracle(apex)
        m = k.Mutations()
        truth: list[dict[str, Any]] = []
        po4 = _v4(out, check, apex, oracle, m, truth)
        po4b = _v4b(out, check, apex, oracle, m, truth)
        out.metrics["truth rows compared"] = k.compare_truth(check, apex, truth)
        m.report(check, out, ["base_current", "variance_current"])
        k.tenant_check(check, apex, bw, [po4, po4b], start)
    finally:
        mail.send_email = saved  # type: ignore[assignment]
    out.metrics.update({"checks": check.done, "failed": len(check.failed)})
    out.notes += [
        ("V4b's 923.11 is asked on the same order after 923.10 was accepted and approved, not on a separate run: the base "
        "stays the first approved 905.00 (OP-3) and the quantity 120, so the spec's 2,173.20 is the same; and the wrong "
        "rule (base = current 923.10) would call it within, so the case tells the rules apart."),
        ("Who decides a price above tolerance is read from the price_variance rule row and the profile's approver roles; "
        "the proposal's payload 'approver' or its task's assignee is accepted as naming that person."),
    ]
    return out


def price_approver(oracle: k.Oracle) -> str:
    row = next(r for r in oracle.rules if r["document"] == "price_variance")
    return oracle.person(row["approver_chain"].split(">")[0].lstrip("+"))


def accept_rise(co: k.Co, oracle: k.Oracle, change_id: int) -> tuple[bool, str]:
    """The buyer accepts a price rise within the tolerance. In Apex the only buyer awarded the quotation, so is one of
    the order's makers, and spec 4.2 rule 4 keeps C1: a maker never accepts a rise, even within tolerance, so the
    buyer's acceptance is referred to the price approver, who accepts it. (Spec V4 says "buyer accepts"; its own C1 rule
    sends it on: a spec conflict, listed for the owner.) Returns whether the change ended accepted, and the answers."""
    r = co.decide_change(co.buyer(), change_id)
    co.work()
    said = f"{r.status_code} {r.text[:200]}"
    if r.status_code == 200 and js(r).get("status") == "waiting_approval":
        r = co.decide_change(price_approver(oracle), change_id)
        co.work()
        said += f"; then the price approver {r.status_code} {r.text[:200]}"
    status = co.one("select status from po_change where id = :i", i=change_id)
    return r.status_code == 200 and status == "accepted", said


def _judge(check: b.Checker, co: k.Co, oracle: k.Oracle, m: k.Mutations, po: str, change: dict[str, Any], *,
           first: Decimal, current: Decimal, new: Decimal, label: str) -> tuple[bool, Decimal]:  # fmt: skip
    """The change row and the buyer's view against the oracle; returns (within, value) of the right rule."""
    qty = D(co.po_line1(po).get("qty"))
    pct = oracle.price_pct()
    within, value = k.price_rule(first, current, new, pct, qty)
    row = change.get("row") or {}
    got_within = row.get("within_tolerance")
    check(got_within is within, f"{label}: {new} judged within={got_within}; oracle {within} ({k.rise_pct(first, new)}% "
          f"above the first approved {first})")  # fmt: skip
    check(D(row.get("base_value")) == first and D(change.get("base_value")) == first,
          f"{label}: base_value {row.get('base_value')} (row) / {change.get('base_value')} (API); expected {first}")  # fmt: skip
    check(D(row.get("old_value")) == current, f"{label}: old_value {row.get('old_value')}, expected the current price "
          f"{current}", "medium")  # fmt: skip
    check(str(change.get("rise_pct")) == k.rise_pct(first, new), f"{label}: rise_pct {change.get('rise_pct')}, expected "
          f"{k.rise_pct(first, new)}", "medium")  # fmt: skip
    wrong = k.price_rule(first, current, new, pct, qty, k.Wrong(base_current=True))
    m.case("base_current", f"{label} within", got_within, within, wrong[0])
    return within, value


def _to_v2_sent(check: b.Checker, co: k.Co, oracle: k.Oracle, m: k.Mutations, label: str, truth: list) -> tuple[str, str | None]:
    """Q(120, 905.00), approved, sent; the supplier asks 920.00; the buyer accepts; approved again; sent."""
    po = co.quotation_order(check, 120, "905.00", label)
    if not po:
        return "", None
    pm, supplier = oracle.person("purchase_manager"), str(co.po_row(po).get("supplier_code"))
    k.approve_ok(check, co, pm, po, 1, f"{label} approve version 1")
    token = k.send_ok(check, co, po, f"{label} send version 1")
    first = Decimal("905.00")
    change = k.ask_price(check, co, po, token, "920.00", f"{label} ask 920.00")
    if not change:
        return po, None
    within, _ = _judge(check, co, oracle, m, po, change, first=first, current=first, new=Decimal("920.00"),
                       label=f"{label} 920.00")  # fmt: skip
    ok, said = accept_rise(co, oracle, int(change["id"]))
    check(ok, f"{label}: the buyer accepting 920.00 (within) answered {said}")
    v1 = oracle.snap(1, 120, "905.00", k.NEED_BY, supplier)
    v2 = oracle.snap(2, 120, "920.00", k.NEED_BY, supplier)
    want = k.material(v1, v2, v1, oracle.date_days())
    k.check_amounts(check, co, oracle, po, 120, "920.00", f"{label} v2")
    row, rev = co.po_row(po), co.revisions(po).get(2, {})
    check(row.get("status") == "submitted" and int(row.get("version") or 0) == 2 and rev.get("baseline_version") == 1,
          f"{label} v2: {row.get('status')}, version {row.get('version')}, baseline {rev.get('baseline_version')}; "
          "expected submitted (price up is material), 2, 1")  # fmt: skip
    k.check_reasons(check, f"{label} v2", rev.get("material"), want)
    pend = co.open_steps(po)
    check(len(pend) == 1 and co.code_of(pend[0].get("approver")) == pm and pend[0].get("version") == 2,
          f"{label} v2: open steps {[(s.get('approver'), s.get('version')) for s in pend]}; expected {pm}, version 2")
    k.approve_ok(check, co, pm, po, 2, f"{label} approve version 2")
    truth += [k.truth_row(label, v2, oracle, approver=pm, reasons=want, base=first, within=within)]
    return po, k.send_ok(check, co, po, f"{label} send version 2")


def _v4(out: Outcome, check: b.Checker, co: k.Co, oracle: k.Oracle, m: k.Mutations, truth: list) -> str:
    po, token = _to_v2_sent(check, co, oracle, m, "V4", truth)
    if not token:
        return po
    pm, buyer, approver = oracle.person("purchase_manager"), co.buyer(), price_approver(oracle)
    first, current, new = Decimal("905.00"), Decimal("920.00"), Decimal("925.00")
    change = k.ask_price(check, co, po, token, "925.00", "V4 ask 925.00")
    if not change:
        return po
    within, value = _judge(check, co, oracle, m, po, change, first=first, current=current, new=new, label="V4 925.00")
    r = co.decide_change(buyer, int(change["id"]))
    co.work()
    ln, row = co.po_line1(po), co.po_row(po)
    check(r.status_code == 200 and js(r).get("status") == "waiting_approval",
          f"V4: the buyer accepting 925.00 (out) answered {r.status_code} {r.text[:200]}; expected waiting_approval")  # fmt: skip
    check(D(ln.get("unit_price")) == current and int(row.get("version") or 0) == 2,
          f"V4 (C1): the buyer's acceptance applied the price: {ln.get('unit_price')}, version {row.get('version')}")  # fmt: skip
    pid = (change.get("row") or {}).get("proposal_id") or js(r).get("proposal_id")
    pid = pid or next((c.get("proposal_id") for c in co.price_changes(po) if c["id"] == change["id"]), None)
    found = co.qd("select * from proposal where id = :i", i=pid) if pid else []
    prop = found[0] if found else {}
    task = co.one("select t.assignee from po_change c join task t on t.id = c.task_id where c.id = :i", i=change["id"])
    named = k.as_json(prop.get("payload")) or {}
    check(prop.get("action") == "po.supplier_price" and D(prop.get("value")) == value,
          f"V4: proposal {prop.get('action')} valued {prop.get('value')}; expected po.supplier_price worth {value}")  # fmt: skip
    wrong_value = k.price_rule(first, current, new, oracle.price_pct(), Decimal(120), k.Wrong(variance_current=True))[1]
    m.case("variance_current", "V4 925.00 value", D(prop.get("value")), value, wrong_value)
    check(approver == pm and (co.code_of(named.get("approver")) == approver or co.code_of(task) == approver),
          f"V4: the price decision waits for {named.get('approver')} / task {task}; the rule names {approver}")  # fmt: skip
    r = co.decide_change(approver, int(change["id"]))
    co.work()
    check(r.status_code == 200, f"V4: {approver} accepting 925.00 answered {r.status_code} {r.text[:200]}")
    supplier = str(co.po_row(po).get("supplier_code"))
    v2 = oracle.snap(2, 120, "920.00", k.NEED_BY, supplier)
    v3 = oracle.snap(3, 120, "925.00", k.NEED_BY, supplier)
    want = k.material(v2, v3, v2, oracle.date_days())
    k.check_amounts(check, co, oracle, po, 120, "925.00", "V4 v3")
    row, rev = co.po_row(po), co.revisions(po).get(3, {})
    pend = co.open_steps(po)
    check(row.get("status") == "submitted" and int(row.get("version") or 0) == 3 and len(pend) == 1
          and co.code_of(pend[0].get("approver")) == pm and pend[0].get("version") == 3,
          f"V4 v3: {row.get('status')}, version {row.get('version')}, open steps "
          f"{[(s.get('approver'), s.get('version')) for s in pend]}; expected submitted, 3, {pm} with version 3 (the "
          "price approver is not a maker)")  # fmt: skip
    k.check_reasons(check, "V4 v3", rev.get("material"), want)
    k.approve_ok(check, co, pm, po, 3, "V4 approve version 3")
    truth += [k.truth_row("V4", v3, oracle, approver=pm, reasons=want, base=first, within=within, variance=value)]
    out.metrics["V4 925.00: within / value"] = f"{change['row'].get('within_tolerance')} / {prop.get('value')}"
    return po


def _v4b(out: Outcome, check: b.Checker, co: k.Co, oracle: k.Oracle, m: k.Mutations, truth: list) -> str:
    po, token = _to_v2_sent(check, co, oracle, m, "V4b", truth)
    if not token:
        return po
    pm, buyer = oracle.person("purchase_manager"), co.buyer()
    first = Decimal("905.00")
    edge = k.ask_price(check, co, po, token, "923.10", "V4b ask 923.10")
    if not edge:
        return po
    within, _ = _judge(check, co, oracle, m, po, edge, first=first, current=Decimal("920.00"), new=Decimal("923.10"),
                       label="V4b 923.10")  # fmt: skip
    ok, said = accept_rise(co, oracle, int(edge["id"]))
    check(ok, f"V4b: the buyer accepting 923.10 (exactly 2.00%) answered {said}")
    k.check_amounts(check, co, oracle, po, 120, "923.10", "V4b v3")
    supplier = str(co.po_row(po).get("supplier_code"))
    v3 = oracle.snap(3, 120, "923.10", k.NEED_BY, supplier)
    truth.append(k.truth_row("V4b", v3, oracle, approver=pm, base=first, within=within))
    k.approve_ok(check, co, pm, po, 3, "V4b approve version 3")
    token = k.send_ok(check, co, po, "V4b send version 3")
    out_ = k.ask_price(check, co, po, token, "923.11", "V4b ask 923.11")
    if not out_:
        return po
    within, value = _judge(check, co, oracle, m, po, out_, first=first, current=Decimal("923.10"), new=Decimal("923.11"),
                           label="V4b 923.11")  # fmt: skip
    r = co.decide_change(buyer, int(out_["id"]))
    co.work()
    pid = js(r).get("proposal_id") or next((c.get("proposal_id") for c in co.price_changes(po) if c["id"] == out_["id"]), None)
    prop = co.qd("select * from proposal where id = :i", i=pid) if pid else []
    got = D(prop[0].get("value")) if prop else None
    check(r.status_code == 200 and js(r).get("status") == "waiting_approval" and got == value,
          f"V4b: the buyer accepting 923.11 answered {r.status_code} {r.text[:160]}; proposal value {got}, expected "
          f"waiting_approval worth {value}")  # fmt: skip
    m.case("variance_current", "V4b 923.11 value", got, value,
           k.price_rule(first, Decimal("923.10"), Decimal("923.11"), oracle.price_pct(), Decimal(120),
                        k.Wrong(variance_current=True))[1])  # fmt: skip
    check(D(co.po_line1(po).get("unit_price")) == Decimal("923.10"), "V4b (C1): the buyer's acceptance of 923.11 "
          f"applied it: {co.po_line1(po).get('unit_price')}")  # fmt: skip
    v_out = oracle.snap(3, 120, "923.11", k.NEED_BY, supplier)  # the spec's separate run: version 3 at 923.11
    truth.append(k.truth_row("V4b", v_out, oracle, approver=pm, base=first, within=within, variance=value))
    out.metrics["V4b 923.10 / 923.11"] = f"within / out, value {got}"
    return po
