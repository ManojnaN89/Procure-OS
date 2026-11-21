"""E64: ERP changes after stock was issued to production (task 6.3, Q21, OP-14, OP-15), written without the
implementation.

Independence: written from the block 1 spec (sections 4.2, 4.4, 4.5, 6.3 A4/A5/A5b, 8), Q21 and the dataset only. The
oracle (`block1.Plant`) replays the base state and the issue, then the change: the excess is taken from open purchase
request cover, then the reservation, then the buyer's cover; what is still over can only be issued stock, so the line
closes as issued with the new need and the store gets a case for issued minus the new need ("return to stock or keep
for production"). An increase after issue reopens the line with what is still short.

- A5 (Apex): 30 impellers issued to MO-1, then MO-1 cut to 20 pumps: a store case for 10 (Sunil Pawar, escalating to
  Vikram Joshi), the 20 still set aside released to the MRP plan (30 set aside, 90 requested), the bearings' request
  line cut exactly to 0 and cancelled while its 40 set aside stay (the 'exactly the open cover' boundary), 14 kg of
  wire released after its request line went to 0; then the impeller line alone at exactly the issued 30 (no case), at
  31 (reopened, 1 short, bought on a new request) and at 29 (a store case for 1, the new request line cut).
- A5b (Apex): 30 impellers issued, then the A2 file (MO-1 cancelled before its request reached the buyer): the request
  cut to nothing and cancelled, the material request cancelled with the 30 issued kept, a store case for 30.
- A4 after a full issue (Apex): everything set aside for MO-1 issued, then MO-1 raised to 60: the issued lines reopen
  with 'short = need - issued' and a new request buys exactly that.
Each with the same files again and a tenant check. Mutations: the store case missing; an issued line not reopened.
"""

from decimal import Decimal
from typing import Any

import block1 as b
import env
import lib
from lib import Outcome

ID = "E64"
TITLE = "Changes after issue: a store case for what was issued too many, issued lines reopen when the need goes up"
REQUIREMENT = (
    "Task 6.3, Q21, OP-14, OP-15 (block 1 spec 4.2, 4.4, 4.5): a cut below what was issued closes the line as issued "
    "with the new need and raises one store case (owner the first store person, escalating to their manager, due in "
    "working days) for issued minus need, never a negative reservation; a cancel after part issue keeps the issued "
    "quantity, cancels the material request and raises a store case; a request line cut exactly to its cover is "
    "cancelled and the reservation untouched; an increase after issue reopens the line with need minus issued short"
)
PREFIX = "e64"
STORE_STEP = "Return the extra to stock or keep it for production; then close the case"


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
    b.start(bw, check)
    apex = b.Company("apex", PREFIX, client)

    plant, rank = b.start(apex, check)
    b.check_base(check, apex, plant, b.SPEC_A0, b.SPEC_A0_VALUES)
    _a5(out, check, apex, plant, rank, bw)
    apex.reset()
    plant, rank = b.start(apex, check)
    _a5b(out, check, apex, plant, rank, bw)
    apex.reset()
    plant, rank = b.start(apex, check)
    _a4_after_issue(out, check, apex, plant, rank, bw)

    out.metrics.update({"checks": check.done, "failed": len(check.failed)})
    out.notes += [
        "Spec gap: a change up to exactly the issued quantity on an issued line ('goes back to open' with nothing "
        "short, or stays issued) is not decided; at 30 the eval checks only no case, nothing set aside and nothing "
        "bought.",
        "Spec gap: a store case's document number (the material request number is assumed by its link "
        "'#/material-requests') and its title on a cancel are not given; the cut's title is checked against the "
        "spec's example.",
    ]
    return out


def _issue(check: b.Checker, co: b.Company, order: str, item: str | None = None, qty: str | None = None) -> None:
    mr = co.one("select no from material_request where order_no = :o", o=order)
    body: dict[str, Any] = {}
    if item:
        line = co.one("select line_no from material_request_line where mr_no = :m and item_code = :i", m=mr, i=item)
        body = {"lines": {str(line): qty}}
    r = co.call(co.holders("store")[0], "POST", f"/api/material-requests/{mr}/issue", body)
    check(r.status_code == 200, f"issuing {item or 'everything'} for {order} answered {r.status_code}: {r.text[:200]}")


def _no_negative(check: b.Checker, co: b.Company, label: str) -> None:
    neg = co.q("select mr_no, item_code, reserved_qty from material_request_line where reserved_qty < 0")
    check(not neg, f"{label}: negative reservations {neg}")


def _store_case(check: b.Checker, co: b.Company, label: str, c: dict[str, Any], title: str | None) -> None:
    b.check_case_task(check, co, c, anchors=(lib.TODAY,))
    check(c["doc_type"] == "material_request", f"{label}: store case doc type {c['doc_type']}")
    if title:
        check(str(c["title"]).startswith(title), f"{label}: store case title {c['title']!r}, expected '{title}...'",
              "medium")  # fmt: skip
    row = {r.get("ref"): r for r in co.exceptions(co.holders("admin")[0])}.get(c["doc_no"])
    if check(row is not None, f"{label}: no Exceptions row for the store case on {c['doc_no']}"):
        name = co.people[b.owner_name(co, "store")]["name"]
        check(
            row.get("owner") == f"Store: {name}" and row.get("link") == "#/material-requests"
            and str(row.get("next_step", "")).startswith(STORE_STEP) and row.get("amount") is None,
            f"{label}: the store case's Exceptions row is {({k: row.get(k) for k in ('owner', 'link', 'next_step', 'amount')})}",
        )  # fmt: skip


def _a5(out: Outcome, check: b.Checker, co: b.Company, plant: b.Plant, rank: dict, other: b.Company) -> None:
    raw, parsed = b.scenario_files(co, check, "A5")
    if not raw:
        return
    other_before = other.snapshot()
    _issue(check, co, "MO-1", "RM-03", "30")
    plant.issue("MO-1", "RM-03", Decimal(30))
    b.agree(check, "A5 after issuing 30 impellers", plant, co)
    before = plant.observe()
    audit0, tasks0 = b.audit_counts(co), b.task_ids(co)
    summary = b.import_change(co, check, "A5", raw)
    eff = plant.apply(parsed)
    made = b.check_import(check, co, "A5", plant, eff, summary, audit0, tasks0)
    b.agree(check, "A5", plant, co)
    _no_negative(check, co, "A5")
    obs = plant.observe()
    check(eff.case_qty("store") == {"RM-03": Decimal(10)} and not eff.case_qty("buyer"), f"A5: oracle cases "
          f"{eff.cases} (self-check: one store case for 10 impellers)", "medium")  # fmt: skip
    mrp = (obs[(b.MRP_CKN, "RM-03")]["reserved"], obs[(b.MRP_CKN, "RM-03")]["requested"])
    check(mrp == (30, 90), f"A5: oracle MRP impellers {mrp} (self-check: 30/90)", "medium")
    opens = {i: obs[("MO-1", i)]["open"] for i in ("RM-01", "RM-06", "RM-08", "PK-01")}
    check(opens == {"RM-01": 20, "RM-06": 0, "RM-08": 0, "PK-01": 0}, f"A5: oracle open cover {opens} (self-check)",
          "medium")  # fmt: skip
    check(eff.released.get("RM-08") == 14, f"A5: oracle released {eff.released.get('RM-08')} kg wire (self-check)",
          "medium")  # fmt: skip
    # columns qualified: material_request has a status too (eval bug, review of 21 Nov)
    line = co.q("select l.status, l.need_qty, l.issued_qty, l.reserved_qty from material_request_line l join material_request "
                "m on m.no = l.mr_no where m.order_no = 'MO-1' and l.item_code = 'RM-03'")  # fmt: skip
    check(
        bool(line)
        and line[0][0] == "issued"
        and b.D(line[0][1]) == 20
        and b.D(line[0][2]) == 30
        and b.D(line[0][3]) == 0,
        f"A5: MO-1's impeller line is {line}, expected issued, need 20, issued 30, nothing set aside",
    )
    # the boundary 'a cut exactly equal to the open cover': bearings 100 -> 40 with 40 set aside and 60 requested
    bearings = b.lines_buying(co, "MO-1", "RM-06")
    check(
        # cut to 0: cancelled and buying nothing; a cancelled request line keeps its last quantity on record
        # (request_line.qty must stay above zero), so its stored qty is not read (review of 21 Nov)
        bool(bearings) and all(b.D(x["buys"]) == 0 and x["status"] == "cancelled" for x in bearings),
        f"A5: MO-1's bearing request line should be cut to 0 and cancelled, is {bearings}",
    )
    held = b.D(co.one("select reserved_qty from material_request_line l join material_request m on m.no = l.mr_no "
                      "where m.order_no = 'MO-1' and l.item_code = 'RM-06'"))  # fmt: skip
    check(held == 40, f"A5: MO-1's bearings set aside {held} after the cut, expected 40 (untouched)")
    stores = [c for c in made if c["owner_kind"] == "store"]
    for c in stores:
        _store_case(check, co, "A5", c, "MO-1 cut to 20: 10 Impeller 1 HP")
    if stores:
        path = f"/api/demand/cases/{stores[0]['task_id']}/close"
        r = co.call(co.holders("buyer")[0], "POST", path, {"outcome": "returned_to_stock", "note": "x"})
        check(r.status_code == 403, f"A5: the buyer closing a store case answered {r.status_code}")
        r = co.call(co.holders("store")[0], "POST", path, {"outcome": "kept_for_production", "note": "fitted already"})
        check(r.status_code == 200 and b.js(r).get("outcome") == "kept_for_production", f"A5: the store closing its "
              f"case answered {r.status_code} {b.js(r)}")  # fmt: skip
    wrong = b.Plant(co.dataset, b.Rules(no_store_case=True), rank)
    wrong.store_check(tag="first")
    wrong.issue("MO-1", "RM-03", Decimal(30))
    wrong_eff = wrong.apply(parsed)
    b.mutation(check, out, "no store case for issued too many", obs, wrong.observe(),
               extra=wrong_eff.case_qty("store") != eff.case_qty("store"))  # fmt: skip
    b.compare_truth(check, co, "A5", before, [obs], eff)
    b.check_again(check, co, "A5", raw, eff)
    _edges(out, check, co, plant)
    b.isolation(check, co, other, {"MO-1", "MO-2", "MRP-2027-01"}, other_before)


def _edges(out: Outcome, check: b.Checker, co: b.Company, plant: b.Plant) -> None:
    """MO-1's impeller line (issued 30, need 20) at exactly 30, one above (31) and one below (29)."""
    header = ["order_no", "item_code", "qty", "unit", "need_by", "status"]
    for n in ("30", "31", "29"):
        rows = [
            {"order_no": "MO-1", "item_code": "RM-03", "qty": n, "unit": "NOS", "need_by": "2026-12-05", "status": ""}
        ]
        tasks0, audit0 = b.task_ids(co), b.audit_counts(co)
        status, answer = co.upload(co.holders("store")[0], "production_order_line",
                                   b.csv_bytes(header, [list(rows[0].values())]))  # fmt: skip
        eff = plant.apply({"production_order_line": rows})
        check(status == 200 and answer.get("changed") == 1, f"impellers at {n}: the import answered {status} "
              f"changed {answer.get('changed')}")  # fmt: skip
        b.check_import(check, co, f"impellers at {n}", plant, eff, b.summary_of([{"kind": "production_order_line",
                       "status": status, **answer}]), audit0, tasks0)  # fmt: skip
        b.agree(check, f"impellers at {n}", plant, co)
        got = co.observe()[("MO-1", "RM-03")]
        if n == "30":
            check(got["reserved"] == 0 and got["requested"] == 0 and not b.new_cases(co, tasks0),
                  f"impellers at exactly the issued 30: {got}, expected no case and nothing set aside or bought")  # fmt: skip
        elif n == "31":
            check(got["status"] == "open" and got["need"] - got["issued"] == 1 and got["requested"] == 1,
                  f"impellers at 31: {got}, expected the line open with 1 short, bought")  # fmt: skip
        else:
            check(eff.case_qty("store") == {"RM-03": Decimal(1)}, f"impellers at 29: oracle {eff.cases} (self-check)",
                  "medium")  # fmt: skip
            check(got["status"] == "issued" and got["requested"] == 0, f"impellers at 29: {got}, expected issued, "
                  "its new request line cut")  # fmt: skip
    out.metrics["edges at the issued quantity"] = "30 (equal), 31 (one above), 29 (one below)"


def _a5b(out: Outcome, check: b.Checker, co: b.Company, plant: b.Plant, rank: dict, other: b.Company) -> None:
    raw, parsed = b.scenario_files(co, check, "A2")  # A5b is the A2 file after a part issue (spec 6.3)
    if not raw:
        return
    other_before = other.snapshot()
    _issue(check, co, "MO-1", "RM-03", "30")
    plant.issue("MO-1", "RM-03", Decimal(30))
    plant.mark_buyer(co.with_buyer())
    first = co.request_of("MO-1")
    before = plant.observe()
    audit0, tasks0 = b.audit_counts(co), b.task_ids(co)
    summary = b.import_change(co, check, "A5b", raw)
    eff = plant.apply(parsed)
    made = b.check_import(check, co, "A5b", plant, eff, summary, audit0, tasks0)
    b.agree(check, "A5b", plant, co)
    _no_negative(check, co, "A5b")
    check(eff.case_qty("store") == {"RM-03": Decimal(30)} and not eff.case_qty("buyer"), f"A5b: oracle cases "
          f"{eff.cases} (self-check)", "medium")  # fmt: skip
    check(all(co.request(n).get("status") == "cancelled" for n in first), f"A5b: MO-1's request {first} should be cut "
          "to nothing and cancelled")  # fmt: skip
    check(co.audit_count("request.cancel") - audit0.get("request.cancel", 0) >= len(first), "A5b: the emptied request "
          "has no request.cancel audit row")  # fmt: skip
    mr = co.q("select status from material_request where order_no = 'MO-1'")
    line = co.q("select l.status, l.issued_qty, l.reserved_qty from material_request_line l join material_request m on "
                "m.no = l.mr_no where m.order_no = 'MO-1' and l.item_code = 'RM-03'")  # fmt: skip
    check(mr == [("cancelled",)] and bool(line) and line[0][0] == "cancelled" and b.D(line[0][1]) == 30
          and b.D(line[0][2]) == 0, f"A5b: MO-1's request {mr}, impeller line {line}; expected cancelled with the 30 "
          "issued kept and nothing set aside")  # fmt: skip
    for c in made:
        if c["owner_kind"] == "store":
            _store_case(check, co, "A5b", c, None)
    b.compare_truth(check, co, "A5b", before, [plant.observe()], eff, missing="low")  # a variant: the key may omit it
    b.check_again(check, co, "A5b", raw, eff)
    b.isolation(check, co, other, {"MO-1", "MO-2", "MRP-2027-01"}, other_before)


def _a4_after_issue(
    out: Outcome, check: b.Checker, co: b.Company, plant: b.Plant, rank: dict, other: b.Company
) -> None:
    raw, parsed = b.scenario_files(co, check, "A4")
    if not raw:
        return
    other_before = other.snapshot()
    _issue(check, co, "MO-1")
    plant.issue("MO-1")
    b.agree(check, "A4 after a full issue: the issue", plant, co, fields=("need", "reserved", "issued", "requested"))
    issued = {i for (m, i), v in plant.observe().items() if m == "MO-1" and v["status"] == "issued"}
    got_issued = {r[0] for r in co.q("select l.item_code from material_request_line l join material_request m on m.no "
                                     "= l.mr_no where m.order_no = 'MO-1' and l.status = 'issued'")}  # fmt: skip
    check(got_issued == issued and bool(issued), f"A4 after issue: issued lines {got_issued}, expected {issued}")
    audit0, tasks0 = b.audit_counts(co), b.task_ids(co)
    requests_before = {n for (n,) in co.q("select no from request")}
    summary = b.import_change(co, check, "A4 after issue", raw)
    eff = plant.apply(parsed)
    b.check_import(check, co, "A4 after issue", plant, eff, summary, audit0, tasks0)
    b.agree(check, "A4 after a full issue", plant, co)
    got = co.observe()
    made = {n for (n,) in co.q("select no from request")} - requests_before
    bought = {x["item"]: b.D(x["buys"]) for x in b.lines_buying(co, "MO-1") if x["request_no"] in made}
    for item in sorted(issued):
        g = got.get(("MO-1", item), {})
        short = g.get("need", b.Z) - g.get("issued", b.Z) - g.get("reserved", b.Z)
        check(g.get("status") == "open" and short == bought.get(item), f"A4 after issue: {item} is {g.get('status')} "
              f"with {short} short and {bought.get(item)} bought; expected open, need - issued bought")  # fmt: skip
    wrong = b.Plant(co.dataset, b.Rules(no_reopen=True), rank)
    wrong.store_check(tag="first")
    wrong.issue("MO-1")
    wrong.apply(parsed)
    b.mutation(check, out, "an issued line not reopened when the need goes up", plant.observe(), wrong.observe())
    b.check_again(check, co, "A4 after issue", raw, eff)
    b.isolation(check, co, other, {"MO-1", "MO-2", "MRP-2027-01"}, other_before)
