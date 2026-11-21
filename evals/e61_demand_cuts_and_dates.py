"""E61: cuts, earlier dates and refused changes from the ERP (task 6.3, Q21), written without the implementation.

Independence: written from the block 1 spec (sections 4, 5, 6.3, 6.4, 8), Q21 and the dataset only. The oracle
(`block1.Plant`) replays the base state from the raw files and applies each change file by the spec's rules: a cut
takes the excess first from cover on purchase requests not yet with the buyer (newest first), then from the
reservation; released stock goes at once to the open lines earliest need-by first; an earlier date moves the request
lines not yet with the buyer and never takes stock set aside for another order.

Scenarios, each from a fresh base state:
- A1 (Apex, MO-2 cut from 100 to 60 pumps): a dry run first (lists, writes nothing), then the cut: MO-2's request
  lines cut before any reservation is released, 40 terminal boxes and 6 litres of paint released and taken by the MRP
  plan at once; MO-2's request Rs 1,24,380, the MRP request Rs 3,64,990; no case.
- A3 (Apex, MO-2 moved from 15 to 3 December, ahead of MO-1): every reservation as before (nothing taken from MO-1),
  MO-2's request lines and request need-by 3 December, no new request, no case.
- B1 (Brightwave, one admin joiner fewer): one of each kit item released and handed to engineering where it lacks,
  the licence request cut from 2 to 1, engineering's headset line cut to nothing; Rs 8,400 and Rs 4,15,500.
- R1 (Apex, planted refusals): a changed location, a changed product and a cancel of an order never loaded are left out
  with the spec's reasons, the valid row of the same file (MO-2 later) applies and moves MO-2's lines but not its
  request lines; then an unknown status, an unknown basis and a zero quantity are refused, and a header-only quantity
  change warns and leaves the material request alone.
Each ends with the same files again (nothing changes) and a tenant check. Mutations: the reservation released before
the purchase request is cut; an order moved earlier taking stock set aside for a later one.
"""

from datetime import date
from decimal import Decimal
from typing import Any

import block1 as b
import env
import lib
from lib import Outcome

ID = "E61"
TITLE = "ERP cuts take open purchase requests first, earlier dates take nothing held, bad changes are refused"
REQUIREMENT = (
    "Task 6.3, Q21 (block 1 spec 4.1-4.5): a known row with new values is applied; a cut takes open purchase request "
    "cover first (not yet with the buyer), then the reservation, released stock handed on earliest need-by first; an "
    "earlier date moves open request lines and takes no stock held for another order; a later date moves only the "
    "material request; refused rows carry the spec's reasons and the valid rows of the same file apply; dry run "
    "writes nothing; every change audited; a second import changes nothing; tenant isolation"
)
PREFIX = "e61"


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

    apex = b.Company("apex", PREFIX, client)
    plant, rank = b.start(apex, check)
    b.check_stock(check, apex, plant)
    b.check_base(check, apex, plant, b.SPEC_A0, b.SPEC_A0_VALUES)
    bw = b.Company("brightwave", PREFIX, client)
    bplant, brank = b.start(bw, check)
    b.check_stock(check, bw, bplant)
    b.check_base(check, bw, bplant, b.SPEC_B0, b.SPEC_B0_VALUES)

    _b1(out, check, bw, bplant, brank, apex)
    _a1(out, check, apex, plant, rank, bw)
    apex.reset()
    plant, rank = b.start(apex, check)
    _a3(out, check, apex, plant, rank, bw)
    apex.reset()
    plant, rank = b.start(apex, check)
    _r1(out, check, apex, plant, bw)

    out.metrics.update({"checks": check.done, "failed": len(check.failed)})
    out.notes += [
        "Spec gap: when a header moves an order's need-by and the same import's lines carry the new date, the spec "
        "does not say whether those line rows count as changes or as loaded before; A3's change count is checked "
        "only as at least one, its effects exactly.",
        "Spec gap: whether one changed row (a header cancel, a joiner row) is one change record or one per "
        "requirement is not fixed; `changed` is checked as rows changed, the records as at least that many.",
        "Not exercised: an earlier date when a purchase order promises later (OP-4 expedite case); no spec scenario "
        "puts an order with the buyer before an earlier change.",
    ]
    return out


def _b1(out: Outcome, check: b.Checker, bw: b.Company, plant: b.Plant, rank: dict, apex: b.Company) -> None:
    """B1: one admin joiner withdrawn."""
    firsts = {mr: bw.request_of(mr) for mr in (b.ADM, b.ENG)}
    statuses = {mr: [bw.request(n).get("status") for n in nos] for mr, nos in firsts.items()}
    check(
        all(s == ["submitted"] for s in statuses.values()),
        f"B0 precondition: both joiner requests should be submitted (not with the buyer), are {statuses}; the oracle "
        "follows what the platform holds",
        "medium",
    )
    plant.mark_buyer(bw.with_buyer())
    raw, parsed = b.scenario_files(bw, check, "B1")
    if not raw:
        return
    apex_before = apex.snapshot()
    before = plant.observe()
    audit0, tasks0 = b.audit_counts(bw), b.task_ids(bw)
    summary = b.import_change(bw, check, "B1", raw)
    eff = plant.apply(parsed)
    b.check_import(check, bw, "B1", plant, eff, summary, audit0, tasks0)
    b.agree(check, "B1", plant, bw)
    obs = plant.observe()
    spec = {"MN-01": (1, 3), "DK-01": (1, 3), "HS-01": (4, 0)}
    mine = {i: (obs[(b.ENG, i)]["reserved"], obs[(b.ENG, i)]["requested"]) for i in spec}
    check(mine == {i: (Decimal(x), Decimal(y)) for i, (x, y) in spec.items()}, f"B1: the oracle's engineering lines "
          f"{mine} are not the spec's {spec} (oracle self-check)", "medium")  # fmt: skip
    for mr, value in ((b.ADM, Decimal("8400")), (b.ENG, Decimal("415500"))):
        check(plant.request_value(mr) == value, f"B1: oracle value of {mr} {plant.request_value(mr)}, spec {value}",
              "medium")  # fmt: skip
        got = sum((bw.request(n).get("total", b.Z) for n in firsts[mr]), b.Z)
        check(b.money(got) == plant.request_value(mr), f"B1: {mr}'s request is worth {got}, expected "
              f"{plant.request_value(mr)}")  # fmt: skip
    hs = b.lines_buying(bw, b.ENG, "HS-01")
    check(
        # cut to nothing: the line is cancelled and buys nothing for engineering; a cancelled request line keeps its last
        # quantity on record (request_line.qty must stay above zero), so its stored qty is not read (review of 21 Nov)
        bool(hs) and all(b.D(x["buys"]) == 0 and x["status"] == "cancelled" for x in hs),
        f"B1: engineering's headset request line should be cut to nothing and cancelled, is {hs}",
    )
    b.compare_truth(check, bw, "B1", before, [obs], eff)
    b.check_again(check, bw, "B1", raw, eff)
    b.isolation(check, bw, apex, {"JP-2026-12", b.ADM, b.ENG}, apex_before)


def _a1(out: Outcome, check: b.Checker, co: b.Company, plant: b.Plant, rank: dict, other: b.Company) -> None:
    """A1: MO-2 cut from 100 to 60 pumps; a dry run first."""
    raw, parsed = b.scenario_files(co, check, "A1")
    if not raw:
        return
    other_before = other.snapshot()
    store = co.holders("store")[0]
    out.metrics["A1 dry-run changes listed"] = b.check_dry_run(check, co, "A1", raw)
    before = plant.observe()
    audit_id = co.last_audit_id()
    audit0, tasks0 = b.audit_counts(co), b.task_ids(co)
    first_mo2 = co.request_of("MO-2")
    summary = b.import_change(co, check, "A1", raw)
    eff = plant.apply(parsed)
    b.check_import(check, co, "A1", plant, eff, summary, audit0, tasks0)
    b.agree(check, "A1", plant, co)
    obs = plant.observe()
    spec_mo2 = b._t(
        "RM-02 0/60, RM-04 0/60, RM-05 10/50, RM-06 0/120, RM-07 10/50, RM-08 0/66, RM-09 10/50, RM-10 60/0, "
        "RM-11 9/0, PK-01 0/60"
    )
    mine = {i: (v["reserved"], v["requested"]) for (m, i), v in obs.items() if m == "MO-2"}
    check(mine == spec_mo2, f"A1: the oracle's MO-2 lines are not the spec's (oracle self-check): {mine}", "medium")
    mrp = {i: (obs[(b.MRP_CKN, i)]["reserved"], obs[(b.MRP_CKN, i)]["requested"]) for i in ("RM-10", "RM-11")}
    check(mrp == b._t("RM-10 90/30, RM-11 11/13"), f"A1: oracle MRP lines {mrp} (self-check)", "medium")
    check(
        dict(eff.released) == {"RM-10": Decimal(40), "RM-11": Decimal(6)},
        f"A1: oracle released {dict(eff.released)} (self-check)",
        "medium",
    )
    for mr, value in (("MO-2", Decimal("124380")), (b.MRP_CKN, Decimal("364990"))):
        check(plant.request_value(mr) == value, f"A1: oracle value of {mr} {plant.request_value(mr)}, spec {value}",
              "medium")  # fmt: skip
        got = sum((co.request(n).get("total", b.Z) for n in co.request_of(mr)), b.Z)
        check(b.money(got) == plant.request_value(mr), f"A1: {mr}'s request is worth {got}, expected "
              f"{plant.request_value(mr)}")  # fmt: skip
    lines = {x["item"]: b.D(x["qty"]) for x in b.lines_buying(co, "MO-2") if x["request_no"] in first_mo2}
    want = {i: v["first"] for (m, i), v in obs.items() if m == "MO-2" and (v["first"] or i in lines)}
    check(lines == want, f"A1: MO-2's request lines {lines}, expected {want}")
    actors = {co.code_of(r[2]) for r in co.audit_rows("demand.change", audit_id)}
    check(actors == {store}, f"A1: demand.change audit rows by {actors}, expected the importing store person {store}")
    # the changes API: this import's changes, and MO-2's newest first
    import_ids = sorted({c.get("import_id") for c in summary["changes"] if c.get("import_id") is not None})
    listed = [c for i in import_ids for c in co.changes(store, import_id=i)[1]]
    check(
        bool(import_ids) and len(listed) == len(summary["changes"]),
        f"A1: GET /api/demand/changes for imports {import_ids} lists {len(listed)} change(s), the import answered "
        f"{len(summary['changes'])}",
    )
    status, mo2 = co.changes(store, doc_no="MO-2")
    stamps = [str(c.get("applied_at")) for c in mo2]
    check(status == 200 and bool(mo2) and stamps == sorted(stamps, reverse=True), "A1: MO-2's changes are not listed "
          "newest first")  # fmt: skip
    check(co.changes(store, import_id="not-a-number")[0] == 422, "A1: a bad import_id should answer 422")
    card = b.mr_cards(co, store).get("MO-2", {})
    check(bool(card.get("changes")), "A1: the Material requests list shows no change on MO-2's request", "medium")
    # mutation: release the reservation before cutting the request
    wrong = b.Plant(co.dataset, b.Rules(release_first=True), rank)
    wrong.store_check(tag="first")
    wrong_eff = wrong.apply(parsed)
    b.mutation(check, out, "reservation released before cutting the purchase request", obs, wrong.observe(),
               extra=dict(wrong_eff.released) != dict(eff.released))  # fmt: skip
    b.compare_truth(check, co, "A1", before, [obs], eff)
    b.check_again(check, co, "A1", raw, eff)
    b.isolation(check, co, other, {"MO-1", "MO-2", "MRP-2027-01"}, other_before)


def _a3(out: Outcome, check: b.Checker, co: b.Company, plant: b.Plant, rank: dict, other: b.Company) -> None:
    """A3: MO-2 needed by 3 December, ahead of MO-1."""
    raw, parsed = b.scenario_files(co, check, "A3")
    if not raw:
        return
    other_before = other.snapshot()
    before_raw = sorted(co.q("select mr_no, item_code, reserved_qty::text from material_request_line"))
    requests_before = {n for (n,) in co.q("select no from request")}
    before = plant.observe()
    audit0, tasks0 = b.audit_counts(co), b.task_ids(co)
    moved_audit = co.audit_count("request.line_need_by")
    summary = b.import_change(co, check, "A3", raw)
    eff = plant.apply(parsed)
    b.check_import(check, co, "A3", plant, eff, summary, audit0, tasks0, exact_count=False)
    b.agree(check, "A3", plant, co)
    check(
        sorted(co.q("select mr_no, item_code, reserved_qty::text from material_request_line")) == before_raw,
        "A3: an earlier date moved stock set aside (expected every reservation exactly as before)",
    )
    new = {n for (n,) in co.q("select no from request")} - requests_before
    check(not new, f"A3: an earlier date made purchase request(s) {sorted(new)}")
    new_date = date(2026, 12, 3)
    head = co.q("select need_by from material_request where order_no = 'MO-2'")
    check([b.day(h[0]) for h in head] == [new_date], f"A3: MO-2's material request need-by {head}, expected 3 Dec")
    lines = {b.day(r[0]) for r in co.q("select l.need_by from material_request_line l join material_request m on m.no = "
                                       "l.mr_no where m.order_no = 'MO-2'")}  # fmt: skip
    check(lines == {new_date}, f"A3: MO-2's lines need-by {lines}, expected 3 Dec")
    bought = b.lines_buying(co, "MO-2")
    late = [x for x in bought if x["status"] == "open" and b.day(x["need_by"]) != new_date]
    check(bool(bought) and not late, f"A3: MO-2's open request lines not moved to 3 Dec: {late[:3]}")
    for no in co.request_of("MO-2"):
        nb = co.request(no).get("need_by")
        check(b.day(nb) == new_date, f"A3: request {no} need-by {nb}, expected 3 Dec (recomputed from its lines)")
    check(
        co.audit_count("request.line_need_by") - moved_audit >= len(eff.moved),
        f"A3: {co.audit_count('request.line_need_by') - moved_audit} request.line_need_by rows for "
        f"{len(eff.moved)} line(s) moved",
    )
    moved = {(m, i) for m, i, _, _ in eff.moved}
    check(len(moved) == 8, f"A3: the oracle moves {len(moved)} request lines (MO-2's eight bought lines; self-check)",
          "medium")  # fmt: skip
    wrong = b.Plant(co.dataset, b.Rules(earlier_steals=True), rank)
    wrong.store_check(tag="first")
    wrong.apply(parsed)
    b.mutation(check, out, "an order moved earlier takes stock set aside for a later one", plant.observe(),
               wrong.observe())  # fmt: skip
    b.compare_truth(check, co, "A3", before, [plant.observe()], eff)
    b.check_again(check, co, "A3", raw, eff)
    b.isolation(check, co, other, {"MO-1", "MO-2", "MRP-2027-01"}, other_before)


def _r1(out: Outcome, check: b.Checker, co: b.Company, plant: b.Plant, other: b.Company) -> None:
    """R1: three planted refusals and one valid row in one file; then bad values and a header-only quantity."""
    raw, parsed = b.scenario_files(co, check, "R1")
    if not raw:
        return
    other_before = other.snapshot()
    store = co.holders("store")[0]
    audit0, tasks0 = b.audit_counts(co), b.task_ids(co)
    bought_before = {(x["request_no"], x["line_no"]): x["need_by"] for x in b.lines_buying(co, "MO-2")}
    summary = b.import_change(co, check, "R1", raw)
    eff = plant.apply(parsed)
    check(len(eff.refused) == 3 and [c["what"] for c in eff.changes] == [{"later"}], f"R1: the oracle refuses "
          f"{eff.refused} and applies {eff.changes} (self-check: 3 refused, 1 later)", "medium")  # fmt: skip
    b.check_import(check, co, "R1", plant, eff, summary, audit0, tasks0)
    b.agree(check, "R1", plant, co, fields=("need", "reserved", "issued", "requested", "open"))
    later = date(2026, 12, 18)
    lines = {b.day(r[0]) for r in co.q("select l.need_by from material_request_line l join material_request m on m.no = "
                                       "l.mr_no where m.order_no = 'MO-2'")}  # fmt: skip
    check(lines == {later}, f"R1: MO-2's lines need-by {lines}, expected 18 Dec (they had the header's old date)")
    bought_after = {(x["request_no"], x["line_no"]): x["need_by"] for x in b.lines_buying(co, "MO-2")}
    check(bought_after == bought_before, "R1: a later date moved MO-2's purchase request lines (they keep their date)")
    check(
        b.D(co.one("select qty from production_order where order_no = 'MO-1'")) == 50, "R1: a refused row changed MO-1"
    )
    b.check_again(check, co, "R1", raw, eff)
    # bad values: each refused with the spec's words, nothing changes
    before = co.snapshot()
    bad = [
        ("production_order", b.csv_bytes(list(b.SPEC_FILES["R1"]["production_order"][0]),
         [["MO-2", "FG-02", "100", "2026-12-18", "DP-2026-12", "PLT-CKN", "closed"]]),
         "status 'closed' must be open or cancelled"),
        ("mrp_result", b.csv_bytes(list(b.SPEC_FILES["M2"]["mrp_result"][0]),
         [[r["plan_no"], r["line_no"], r["item_code"], r["qty"], r.get("unit", ""), r["need_by"], r["location_code"],
           "netted", ""] for r in co.rows("inputs", "mrp_result")[:1]]),
         "basis 'netted' must be gross or net"),
        ("production_order_line", b.csv_bytes(list(b.SPEC_FILES["A1"]["production_order_line"][0]),
         [["MO-2", "RM-02", "0", "NOS", "2026-12-18", ""]]), "zero"),
    ]  # fmt: skip
    for kind, body, words in bad:
        status, answer = co.upload(store, kind, body)
        reasons = [str(x.get("reason", "")) for x in answer.get("rejected") or []]
        check(
            status == 200 and len(reasons) == 1 and words.lower() in reasons[0].lower() and not answer.get("changed"),
            f"R1: a {kind} row with a bad value should be left out with '{words}', got {status} {reasons}",
        )
    check(co.snapshot() == before, "R1: rows left out for a bad value changed something")
    # a header-only quantity change: the header moves, the material request does not, and the report warns
    lines_before = co.q("select item_code, need_qty::text from material_request_line l join material_request m on "
                        "m.no = l.mr_no where m.order_no = 'MO-2' order by item_code")  # fmt: skip
    status, answer = co.upload(store, "production_order", b.csv_bytes(
        list(b.SPEC_FILES["R1"]["production_order"][0]), [["MO-2", "FG-02", "60", "2026-12-18", "DP-2026-12", "PLT-CKN",
                                                            ""]]))  # fmt: skip
    warn = "MO-2: quantity 100 -> 60; its components were not in this import, so its material request is unchanged"
    check(
        status == 200 and any(warn.lower() in str(w).lower() for w in answer.get("warnings") or []),
        f"R1: a header-only quantity change should warn '{warn}', got {answer.get('warnings')}",
    )
    whats = {w for c in answer.get("changes") or [] for w in c.get("what") or []}
    check("header_qty" in whats, f"R1: the header-only change is reported as {whats}, expected header_qty", "medium")
    check(
        b.D(co.one("select qty from production_order where order_no = 'MO-2'")) == 60, "R1: MO-2's header not updated"
    )
    lines_after = co.q("select item_code, need_qty::text from material_request_line l join material_request m on "
                       "m.no = l.mr_no where m.order_no = 'MO-2' order by item_code")  # fmt: skip
    check(lines_after == lines_before, "R1: a header-only quantity change scaled MO-2's material request lines")
    b.isolation(check, co, other, {"MO-1", "MO-2", "MO-9"}, other_before)
