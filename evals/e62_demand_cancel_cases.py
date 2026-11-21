"""E62: a cancel after the buyer has the purchase request becomes a case per document (task 6.3, Q21), written without
the implementation.

Independence: written from the block 1 spec (sections 4.2, 4.4, 5.2, 6.3 A2, 6.4 B2, 8), Q21, the dataset and
`data/reference` only. The oracle (`block1.Plant`) replays the base state, the setup (approval and sourcing make the
cancelled order's request lines the buyer's) and the cancel: everything the order held is released and handed at once
to the open orders earliest need-by first; what is with the buyer is not touched but becomes one buyer case per
purchase document holding it, owned by the buyer, escalating to the buyer's manager, due `demand_change_case_days`
(2) working days later on the company's own calendar (working days and holidays of its location, decision of 20 Nov).

- A2 (Apex): SO-1001 cancelled, MO-1 cancelled after its request was approved and sourced: released and passed on as
  the spec's table; buyer cases (Neha Kulkarni, escalating to Sanjay Rao) for 50 castings, 60 bearings, 40 kg copper
  wire and 30 cartons, one per document; their Exceptions rows (kind, title, owner, link, next step, since, amount =
  excess x the line's price); closing (by AP 403, unknown outcome and a missing note 422, the buyer 200, again 409,
  audited); a cancelled order sent open and lines of a cancelled order refused with the spec's words. Then the store
  cancels MO-2 by hand on Material requests (OP-17): its request, not yet with the buyer, is cut to nothing and
  cancelled, no case.
- B2 (Brightwave): the December engineering intake cancelled after its request was approved and sourced: three items
  released (staying free), buyer cases (Arjun Reddy, escalating to Farah Khan) for 3 developer laptops, 4 monitors,
  4 docks, a headset and 4 licences.
Each ends with the same file again and a tenant check. Mutations: a case raised for a line not yet with the buyer; a
cancel that leaves the order's own open purchase requests uncut (the old rule); and, for the due date, a Mon-Fri
calendar with no holidays.
"""

from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Any

import block1 as b
import env
import lib
from lib import Outcome

ID = "E62"
TITLE = "A cancel after the buyer has it raises one case per document, owned, escalated and due on the company calendar"
REQUIREMENT = (
    "Task 6.3, Q21 (block 1 spec 4.2, 4.4, 5.2): a cancelled ERP order releases its reservations (handed on earliest "
    "need-by first) and cuts purchase requests not yet with the buyer; with the buyer or on a PO, one demand_change "
    "case per document for the excess (owner the document's buyer, escalation the owner's manager, due in "
    "demand_change_case_days working days on the company calendar), on Exceptions with its amount; closing by the "
    "assignee only, once, audited; the manual cancel follows the same rules; tenant isolation"
)
PREFIX = "e62"
PIN = {"apex": date(2026, 11, 20), "brightwave": date(2026, 11, 23)}  # a Friday and a Monday: holidays 24 and 27 Nov
NEXT_STEP = {
    "po": "Revise or cancel the order, or keep the goods for stock; then close the case",
    "rfq": "Reduce or cancel the RFQ line",
}
LINK = {"po": "#/purchase-orders/", "rfq": "#/rfqs/", "request": "#/requisitions/"}


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
    apex = b.Company("apex", PREFIX, client)
    plant, rank = b.start(apex, check)
    b.check_base(check, apex, plant, b.SPEC_A0, b.SPEC_A0_VALUES)
    calendars: list[str] = []

    _a2(out, check, apex, plant, rank, bw, calendars)
    _b2(out, check, bw, bplant, brank, apex, calendars)

    told = any(c == "right" for c in calendars)
    check(told, f"mutation 'due date on Mon-Fri without holidays': no case's due date told the calendars apart "
          f"({calendars})", "low")  # fmt: skip
    out.metrics.setdefault("mutations caught", []).append(
        "due date on a Mon-Fri calendar without holidays" + ("" if told else " (NOT separated)")
    )
    out.metrics.update({"cases checked": len(calendars), "checks": check.done, "failed": len(check.failed)})
    out.notes += [
        "Spec gap: the working hours of the company calendar are not given, so a due date is checked by its day: "
        "2 working days after the day the case was made (1 when it was made before the working day began).",
        "Spec gap: the Exceptions 'amount' is 'paise' (spec 5.2): read as rupees to the paisa or a whole number of "
        "paise.",
        "Spec gap: a joiner case's title is not given; it is checked to say 'cancelled' and name its document.",
        "Spec gap: whether a store outcome on a buyer case is an 'unknown outcome' (422) is not said; checked as low.",
        "Spec gap: the next step of a buyer case on a request with no document yet is not given; it is not checked.",
        "Spec gap: the case owner when a document's buyer is not a person with the Buyer role is the category's "
        "buyer, else the first Buyer; both demo companies have one buyer, so the owner is that person.",
    ]
    return out


def _setup_with_buyer(check: b.Checker, co: b.Company, mr: str) -> dict[tuple[str, str], bool]:
    """Approve the order's purchase request through its chain and run sourcing: every line goes to the buyer."""
    reqs = co.request_of(mr)
    check(len(reqs) == 1, f"{mr}: one purchase request expected after the store check, found {reqs}")
    for no in reqs:
        status = co.approve(no)
        check(status == "approved", f"{mr}: request {no} is {status} after its approvers acted")
    co.source()
    lines = b.lines_buying(co, mr)
    open_lines = [(x["item"], x["status"]) for x in lines if x["status"] not in b.WITH_BUYER]
    check(not open_lines, f"precondition: every {mr} request line should be sourcing or ordered, these are not: "
          f"{open_lines} (the oracle follows what the platform holds)", "medium")  # fmt: skip
    return co.with_buyer()


def _cases(
    out: Outcome,
    check: b.Checker,
    co: b.Company,
    label: str,
    order: str | None,
    eff: b.Effects,
    docs: dict[str, tuple[str, str, Decimal]],
    made: list[dict[str, Any]],
    calendars: list[str],
) -> None:
    """One buyer case per document with its excess; owner, escalation, due date, title, amount and Exceptions row."""
    want: dict[tuple[str, str], dict[str, Decimal]] = defaultdict(dict)
    for c in eff.cases:
        if c.owner == "buyer" and c.item in docs:
            kind, doc, _ = docs[c.item]
            want[(kind, doc)][c.item] = want[(kind, doc)].get(c.item, b.Z) + c.qty
    buyer_cases = [c for c in made if c["owner_kind"] == "buyer"]
    got = {(c["doc_type"], c["doc_no"]): c["excess"] for c in buyer_cases}
    check(len(got) == len(buyer_cases), f"{label}: two cases on one document: {[c['doc_no'] for c in buyer_cases]}")
    check(got == dict(want), f"{label}: cases per document {got}, expected {dict(want)}")
    admin = co.holders("admin")[0]
    rows = {r.get("ref"): r for r in co.exceptions(admin)}
    buyer = co.holders("buyer")[0]
    mine = {r.get("ref") for r in co.exceptions(buyer, mine=True) if r.get("mine")}
    store_mine = {r.get("ref") for r in co.exceptions(co.holders("store")[0], mine=True)}
    for c in buyer_cases:
        calendars.append(b.check_case_task(check, co, c, anchors=(PIN[co.name],)))
        title = str(c["title"])
        prefix = f"{order} cancelled in the ERP" if order else "cancelled"
        ok_title = (title.startswith(prefix) if order else prefix in title.lower()) and c["doc_no"] in title
        check(ok_title, f"{label}: case title {title!r} should say '{prefix}' and name {c['doc_no']}", "medium")
        price = {i: docs[i][2] for i in c["excess"] if i in docs}
        amount = b.money(sum((q * price.get(i, b.Z) for i, q in c["excess"].items()), b.Z))
        check(b.D(c["amount"]) == amount, f"{label}: case {c['doc_no']} amount {c['amount']}, expected {amount}")
        row = rows.get(c["doc_no"])
        if not check(row is not None, f"{label}: no Exceptions row (kind demand) for case {c['doc_no']}"):
            continue
        name = co.people[b.owner_name(co, "buyer")]["name"]
        expect = {
            "kind": "demand",
            "kind_title": "Demand changes to act on",
            "reason": title,
            "owner": f"Buyer: {name}",
            "link": LINK[c["doc_type"]] + c["doc_no"],
        }
        bad = {k: row.get(k) for k, v in expect.items() if row.get(k) != v}
        check(not bad, f"{label}: Exceptions row of {c['doc_no']} has {bad}, expected {expect}")
        step = NEXT_STEP.get(c["doc_type"])
        if step:
            check(str(row.get("next_step", "")).startswith(step), f"{label}: next step {row.get('next_step')!r}, "
                  f"expected '{step}...'")  # fmt: skip
        shown = b.dec(row.get("amount"))  # 'paise' (spec 5.2): rupees to the paisa, or a whole number of paise
        check(shown in (amount, amount * 100), f"{label}: Exceptions amount {row.get('amount')}, expected {amount}")
        since = b.day(row.get("since"))
        check(since in {b.day(c["created_at"].astimezone(b.IST)), PIN[co.name]}, f"{label}: since {since}, "
              f"the case was made {c['created_at']}")  # fmt: skip
        check(c["doc_no"] in mine, f"{label}: the buyer's 'mine' Exceptions miss {c['doc_no']}")
        check(c["doc_no"] not in store_mine, f"{label}: the store's 'mine' Exceptions list the buyer's {c['doc_no']}")


def _close(check: b.Checker, co: b.Company, label: str, case: dict[str, Any]) -> None:
    """Closing a buyer case: AP refused, bad outcome and missing note refused, the assignee closes it once."""
    path = f"/api/demand/cases/{case['task_id']}/close"
    buyer, ap = b.owner_name(co, "buyer"), co.holders("ap")[0]
    closes = co.audit_count("demand.case_close")
    r = co.call(ap, "POST", path, {"outcome": "reduced", "note": "eval"})
    check(r.status_code == 403, f"{label}: AP closing a buyer case answered {r.status_code}")
    r = co.call(buyer, "POST", path, {"outcome": "returned_to_stock", "note": "eval"})
    check(r.status_code == 422, f"{label}: a store outcome on a buyer case answered {r.status_code}", "low")
    r = co.call(buyer, "POST", path, {"outcome": "shredded", "note": "eval"})
    check(r.status_code == 422, f"{label}: an unknown outcome answered {r.status_code}")
    r = co.call(buyer, "POST", path, {"outcome": "kept_for_stock", "note": ""})
    check(r.status_code == 422, f"{label}: kept_for_stock without a note answered {r.status_code}")
    r = co.call(buyer, "POST", path, {"outcome": "reduced", "note": "PO revised with the supplier"})
    body = b.js(r)
    check(
        r.status_code == 200 and body.get("status") == "done" and body.get("outcome") == "reduced"
        and str(body.get("task_id")) == str(case["task_id"]),
        f"{label}: the buyer closing case {case['task_id']} answered {r.status_code} {body}",
    )  # fmt: skip
    row = co.q("select outcome, closed_at from demand_change_case where task_id = :t", t=case["task_id"])
    status = co.one("select status from task where id = :t", t=case["task_id"])
    check(row and row[0][0] == "reduced" and row[0][1] is not None and status == "done", f"{label}: after closing, "
          f"the case is {row} and its task {status}")  # fmt: skip
    r = co.call(buyer, "POST", path, {"outcome": "reduced", "note": "again"})
    check(r.status_code == 409, f"{label}: closing a closed case answered {r.status_code}")
    check(
        co.audit_count("demand.case_close") == closes + 1, f"{label}: closing is not audited once (demand.case_close)"
    )
    still_open = [c for c in co.cases() if c["doc_no"] == case["doc_no"] and c["status"] != "done"]
    left = {x.get("ref") for x in co.exceptions(co.holders("admin")[0])}
    check(case["doc_no"] not in left or bool(still_open), f"{label}: a closed case is still on Exceptions", "medium")


def _a2_oracle(co: b.Company, rules: b.Rules, rank: dict, with_buyer: dict, parsed: dict) -> b.Plant:
    """The oracle of A2 then the manual cancel of MO-2, under a set of rules (for the mutations)."""
    p = b.Plant(co.dataset, rules, rank)
    p.store_check(tag="first")
    p.mark_buyer(with_buyer)
    p.apply(parsed)
    _manual_cancel(p, "MO-2")
    return p


def _manual_cancel(p: b.Plant, mr: str) -> b.Effects:
    eff = b.Effects()
    for ln in p.lines_of(mr):
        p._cancel(ln, eff, mr)
    p.settle(mr)
    p.stock_rose(eff.released_places)
    return eff


def _a2(out: Outcome, check: b.Checker, co: b.Company, plant: b.Plant, rank: dict, other: b.Company,
        calendars: list[str]) -> None:  # fmt: skip
    with_buyer = _setup_with_buyer(check, co, "MO-1")
    plant.mark_buyer(with_buyer)
    docs = co.buyer_docs("MO-1")
    raw, parsed = b.scenario_files(co, check, "A2")
    if not raw:
        return
    other_before = other.snapshot()
    before = plant.observe()
    audit0, tasks0 = b.audit_counts(co), b.task_ids(co)
    with b.on_day(PIN["apex"]):
        summary = b.import_change(co, check, "A2", raw)
    eff = plant.apply(parsed)
    made = b.check_import(check, co, "A2", plant, eff, summary, audit0, tasks0)
    b.agree(check, "A2", plant, co)
    obs = plant.observe()
    spec_rel = {i: Decimal(q) for i, q in (("RM-03", 50), ("RM-05", 50), ("RM-06", 40), ("RM-07", 50), ("RM-08", 50),
                                          ("RM-09", 50), ("RM-10", 50), ("RM-11", 10), ("PK-01", 20))}  # fmt: skip
    check(dict(eff.released) == spec_rel, f"A2: oracle released {dict(eff.released)} (self-check)", "medium")
    spec_cases = {"RM-01": Decimal(50), "RM-06": Decimal(60), "RM-08": Decimal(40), "PK-01": Decimal(30)}
    check(eff.case_qty("buyer") == spec_cases, f"A2: oracle buyer cases {eff.case_qty('buyer')} (self-check)", "medium")
    spec_mo2 = b._t("RM-05 60/40, RM-06 40/160, RM-07 60/40, RM-08 50/60, RM-09 60/40, PK-01 20/80")
    spec_mrp = b._t("RM-03 60/60, RM-10 100/20, RM-11 15/9")
    for mr, spec in (("MO-2", spec_mo2), (b.MRP_CKN, spec_mrp)):
        mine = {i: (obs[(mr, i)]["reserved"], obs[(mr, i)]["requested"]) for i in spec}
        check(mine == spec, f"A2: oracle {mr} {mine} is not the spec's (self-check)", "medium")
    for mr, value in (("MO-2", Decimal("143280")), (b.MRP_CKN, Decimal("351990"))):
        check(plant.request_value(mr) == value, f"A2: oracle value of {mr} {plant.request_value(mr)} (self-check)",
              "medium")  # fmt: skip
        got = sum((co.request(n).get("total", b.Z) for n in co.request_of(mr)), b.Z)
        check(b.money(got) == value, f"A2: {mr}'s request is worth {got}, expected {value}")
    mr_status = co.one("select status from material_request where order_no = 'MO-1'")
    po_status = co.one("select status from production_order where order_no = 'MO-1'")
    req = {r[0] for r in co.q("select status from material_requirement where source_doc_no = 'MO-1'")}
    check(mr_status == "cancelled" and po_status == "cancelled" and req == {"cancelled"}, f"A2: MO-1's material "
          f"request {mr_status}, order {po_status}, requirements {req}; all should be cancelled")  # fmt: skip
    buyer_lines = [x for x in b.lines_buying(co, "MO-1") if x["status"] in b.WITH_BUYER]
    check(all(b.D(x["buys"]) > 0 for x in buyer_lines), "A2: the cover with the buyer was touched (it must stay)")
    _cases(out, check, co, "A2", "MO-1", eff, docs, made, calendars)
    # refused: a cancelled order sent open again, and lines of a cancelled order
    store = co.holders("store")[0]
    before_refusals = co.snapshot()
    header = b.SPEC_FILES["A2"]["production_order"][0]
    status, answer = co.upload(
        store, "production_order", b.csv_bytes(list(header), [[*list(header.values())[:-1], ""]])
    )
    reasons = [str(x.get("reason")) for x in answer.get("rejected") or []]
    check(status == 200 and len(reasons) == 1 and "a cancelled order is not reopened; send a new order number" in
          reasons[0].lower(), f"A2: a cancelled order sent open answered {reasons}")  # fmt: skip
    line = b.SPEC_FILES["A4"]["production_order_line"][0]
    status, answer = co.upload(store, "production_order_line", b.csv_bytes(list(line), [list(line.values())]))
    reasons = [str(x.get("reason")) for x in answer.get("rejected") or []]
    check(status == 200 and len(reasons) == 1 and "mo-1 is cancelled" in reasons[0].lower(), f"A2: a line of the "
          f"cancelled MO-1 answered {reasons}")  # fmt: skip
    check(co.snapshot() == before_refusals, "A2: refused rows changed something")
    b.check_again(check, co, "A2", raw, eff)
    buyer_cases = [c for c in made if c["owner_kind"] == "buyer"]
    if buyer_cases:
        _close(check, co, "A2", buyer_cases[0])
    # the manual cancel of MO-2 on Material requests: its request is not with the buyer, so it is cut (OP-17)
    mo2_mr = co.one("select no from material_request where order_no = 'MO-2'")
    mo2_req = co.request_of("MO-2")
    tasks1 = b.task_ids(co)
    r = co.call(store, "POST", f"/api/material-requests/{mo2_mr}/cancel", {"reason": "Dealer order withdrawn"})
    body = b.js(r)
    eff2 = _manual_cancel(plant, "MO-2")
    check(r.status_code == 200, f"cancelling MO-2 by hand answered {r.status_code}: {r.text[:200]}")
    check(
        sorted(body.get("requests_cancelled") or []) == sorted(mo2_req) and not body.get("cases")
        and not body.get("purchase_requests_left") and bool(body.get("requests_cut")),
        f"cancelling MO-2 by hand: requests_cancelled {body.get('requests_cancelled')} (expected {mo2_req}), cases "
        f"{body.get('cases')}, purchase_requests_left {body.get('purchase_requests_left')}, requests_cut "
        f"{bool(body.get('requests_cut'))}",
    )  # fmt: skip
    check(all(co.request(n).get("status") == "cancelled" for n in mo2_req), "MO-2's request is not cancelled")
    check(not eff2.case_qty("buyer") and not b.new_cases(co, tasks1), "cancelling MO-2 by hand raised a case")
    b.agree(check, "after cancelling MO-2 by hand", plant, co)
    right = plant.observe()
    for name, rules in (
        ("a case raised for a line not yet with the buyer", b.Rules(case_for_open=True)),
        ("a cancel leaves the order's own open purchase requests uncut", b.Rules(keep_sole_pr=True)),
    ):
        b.mutation(check, out, name, right, _a2_oracle(co, rules, rank, with_buyer, parsed).observe())
    b.compare_truth(check, co, "A2", before, [obs], eff)
    b.isolation(check, co, other, {"MO-1", "MO-2", "MRP-2027-01"}, other_before)


def _b2(out: Outcome, check: b.Checker, co: b.Company, plant: b.Plant, rank: dict, other: b.Company,
        calendars: list[str]) -> None:  # fmt: skip
    with_buyer = _setup_with_buyer(check, co, b.ENG)
    plant.mark_buyer(with_buyer)
    docs = co.buyer_docs(b.ENG)
    raw, parsed = b.scenario_files(co, check, "B2")
    if not raw:
        return
    other_before = other.snapshot()
    before = plant.observe()
    audit0, tasks0 = b.audit_counts(co), b.task_ids(co)
    with b.on_day(PIN["brightwave"]):
        summary = b.import_change(co, check, "B2", raw)
    eff = plant.apply(parsed)
    made = b.check_import(check, co, "B2", plant, eff, summary, audit0, tasks0)
    b.agree(check, "B2", plant, co)
    spec_rel = {"LP-02": Decimal(1), "HS-01": Decimal(3), "KB-01": Decimal(4)}
    check(dict(eff.released) == spec_rel, f"B2: oracle released {dict(eff.released)} (self-check)", "medium")
    spec_cases = {
        "LP-02": Decimal(3),
        "MN-01": Decimal(4),
        "DK-01": Decimal(4),
        "HS-01": Decimal(1),
        "SW-01": Decimal(4),
    }
    check(eff.case_qty("buyer") == spec_cases, f"B2: oracle buyer cases {eff.case_qty('buyer')} (self-check)", "medium")
    adm = {k: v["reserved"] for k, v in plant.observe().items() if k[0] == b.ADM}
    check(adm == {k: v["reserved"] for k, v in before.items() if k[0] == b.ADM}, "B2: the oracle gave admin more stock "
          "(self-check: admin needs none of what was released)", "medium")  # fmt: skip
    mr_status = co.one("select status from material_request where order_no = :o", o=b.ENG)
    check(mr_status == "cancelled", f"B2: engineering's material request is {mr_status}, expected cancelled")
    _cases(out, check, co, "B2", None, eff, docs, made, calendars)  # a joiner case's title is not given exactly
    b.compare_truth(check, co, "B2", before, [plant.observe()], eff)
    b.check_again(check, co, "B2", raw, eff)
    b.isolation(check, co, other, {"JP-2026-12", b.ADM, b.ENG}, other_before)
