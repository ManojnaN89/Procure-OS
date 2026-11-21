"""E60: MRP results become material requests (task 6.4), written without the implementation.

Independence: written from the block 1 spec (sections 3, 4.3, 6.3 M2, 8), the plan and the dataset only. The oracle
(`block1.Plant`) works out every material request line from the raw files: the MRP results (and, beside them, the
demand plan DP-2027-01 times `bom.csv`), the production order lines, stock replayed from the opening and the movement
history per item and location; free stock is set aside earliest need-by first across production orders and MRP plans,
a gross line nets stock, a net line never takes free stock in place of buying.

On a fresh `demo reset apex`: the plan's MRP rows are loaded as requirements of kind mrp, need-by exactly as the ERP
sent it (10 January 2027, a Sunday); one material request per plan and location (`MRP-2027-01/PLT-CKN`, no product);
Run store check gives the spec's base table A0 (three purchase requests, Rs 72,930, 2,09,700 and 3,69,490); the same MRP
file again loads nothing. Then scenario M2 (the plan split between Chakan and Sanand, the Sanand lines net): Chakan's
open purchase request is cut, a new request `MRP-2027-01/PLT-SND` is made with ten net lines, and after a count of 50
terminal boxes at Sanand and a store check it sets nothing aside and buys every net line in full. The M2 file again
changes nothing; the other demo company sees none of it. Mutations: MRP skipped by the material requests (DF-18), and a
net line that sets free stock aside.
"""

from datetime import date
from decimal import Decimal
from typing import Any

import block1 as b
import env
import lib
from lib import Outcome
from sqlalchemy import text

ID = "E60"
TITLE = "MRP results become material requests; net lines buy in full; reservation earliest need-by first"
REQUIREMENT = (
    "Task 6.4 (block 1 spec section 3): every open MRP requirement gets a material request line; one request per MRP "
    "plan and location ('<plan>/<location>', source mrp, no product, need-by the earliest line, never moved off a "
    "Sunday); gross lines net stock like production orders, earliest need-by first across orders and plans; net lines "
    "never take free stock in place of buying; the same file again loads nothing; a re-sent plan is a change (open "
    "purchase requests cut); tenant isolation"
)
PREFIX = "e60"


def run(ctx: Any) -> Outcome:
    env.forget_tenants(PREFIX)
    try:
        return _run(ctx)
    finally:
        env.forget_tenants(PREFIX)


def _run(ctx: Any) -> Outcome:  # noqa: C901 - one scenario, step by step
    from fastapi.testclient import TestClient
    from p2p.main import app

    out = Outcome(True)
    check = b.Checker(out)
    lib.capture_mail()
    client = TestClient(app)

    bw = b.Company("brightwave", PREFIX, client)  # the other company, for the isolation check at the end
    b.start(bw, check)
    bw_before = bw.snapshot()

    co = b.Company("apex", PREFIX, client)
    store = co.holders("store")[0]

    # ---- 1. the dataset: MRP rows are the demand plan times the bill of materials --------------------------------
    mrp_rows = co.rows("inputs", "mrp_result")
    if not check(bool(mrp_rows), "apex: inputs/mrp_result.csv is missing or empty (spec 6.2)"):
        return out
    plan_qty = {r["plan_no"]: (r["product_code"], b.D(r["qty"])) for r in co.rows("inputs", "demand_plan")}
    bom: dict[str, dict[str, Decimal]] = {}
    for r in co.rows("inputs", "bom"):
        bom.setdefault(r["product_code"], {})[r["item_code"]] = b.D(r["qty"])
    run_of = {"MRP-2027-01": "DP-2027-01"}  # company.yaml mrp_runs: the plan the MRP run was made for
    product, pumps = plan_qty[run_of["MRP-2027-01"]]
    want_rows = {i: (q * pumps).quantize(b.Q4) for i, q in bom[product].items()}
    got_rows = {r["item_code"]: b.D(r["qty"]) for r in mrp_rows if r["plan_no"] == "MRP-2027-01"}
    check(got_rows == want_rows, f"mrp_result.csv {got_rows} is not DP-2027-01 x BOM {want_rows}", "medium")
    check(
        all((r.get("basis") or "gross") == "gross" and r.get("location_code") == "PLT-CKN" for r in mrp_rows),
        "mrp_result.csv rows should be gross at PLT-CKN (spec 6.2)",
        "medium",
    )

    # ---- 2. loaded at reset: requirements of kind mrp and one material request per plan and location ------------
    plant0 = b.Plant(co.dataset)
    reqs = {
        (r[0], r[1]): r
        for r in co.q(
            "select line_key, item_code, base_qty, need_by, location_code, status from material_requirement "
            "where source_kind = 'mrp'"
        )
    }
    by_item = {r[1]: r for r in reqs.values()}
    for r in mrp_rows:
        got = by_item.get(r["item_code"])
        want_qty = plant0.base_qty(r["item_code"], b.D(r["qty"]), r.get("unit", ""))
        check(
            got is not None and b.D(got[2]) == want_qty and b.day(got[3]) == date.fromisoformat(r["need_by"]),
            f"MRP requirement {r['item_code']}: stored {got and (b.fq(b.D(got[2])), got[3])}, expected "
            f"{b.fq(want_qty)} by {r['need_by']}",
        )
    sunday = date(2027, 1, 10)
    check(sunday.weekday() == 6, "2027-01-10 should be a Sunday (spec OP-19)", "low")
    head = co.q(
        "select no, source_kind, product_code, need_by, location_code, status from material_request "
        "where order_no = :o",
        o=b.MRP_CKN,
    )
    if check(len(head) == 1, f"one material request {b.MRP_CKN} expected, found {len(head)}"):
        no, kind, prod, need_by, loc, status = head[0]
        check(kind == "mrp", f"{b.MRP_CKN} source_kind {kind}, expected mrp")
        check(prod is None, f"{b.MRP_CKN} product_code {prod}, expected none (an MRP plan has no product)")
        check(
            b.day(need_by) == sunday, f"{b.MRP_CKN} need-by {need_by}, expected 2027-01-10 (the ERP's date, a Sunday)"
        )
        check(loc == "PLT-CKN", f"{b.MRP_CKN} location {loc}, expected PLT-CKN")
        lines = dict(co.q("select item_code, need_qty from material_request_line where mr_no = :m", m=no))
        check(
            {i: b.D(q) for i, q in lines.items()} == want_rows,
            f"{b.MRP_CKN} lines {lines} are not the MRP rows {want_rows}",
        )

    # ---- 3. Run store check: the base state A0 ---------------------------------------------------------------------
    plant, rank = b.start(co, check)
    b.check_stock(check, co, plant)
    b.check_base(check, co, plant, b.SPEC_A0, b.SPEC_A0_VALUES)
    a0 = plant.observe()
    got0 = co.observe()
    # reservation earliest need-by first across orders and plans: where stock runs out, the earlier holds it all
    order = sorted({m for m, _ in a0}, key=lambda m: min(v["need_by"] for (mm, _), v in a0.items() if mm == m))
    for item in ("RM-03", "RM-10", "RM-11"):
        held = [got0.get((m, item), {}).get("reserved", b.Z) for m in order if (m, item) in a0]
        needs = [a0[(m, item)]["need"] for m in order if (m, item) in a0]
        starved = next((k for k, (h, n) in enumerate(zip(held, needs, strict=True)) if h < n), len(held))
        check(
            all(h == 0 for h in held[starved + 1 :]),
            f"{item}: set aside {[b.fq(h) for h in held]} for {order}: a later need holds stock an earlier one lacks",
        )
    cards = b.mr_cards(co, store)
    card = cards.get(b.MRP_CKN, {})
    check(card.get("source_kind") == "mrp", f"the Material requests list gives {b.MRP_CKN} source_kind "
          f"{card.get('source_kind')!r}, expected 'mrp'")  # fmt: skip
    check(
        all((ln.get("basis") or "") == "gross" for ln in card.get("lines") or [{}]),
        f"{b.MRP_CKN} lines should carry basis 'gross' on the list",
        "medium",
    )
    wrong_mrp = b.Plant(co.dataset, b.Rules(skip_mrp=True), rank)
    wrong_mrp.store_check(tag="first")
    b.mutation(check, out, "MRP results skipped by the material requests", a0, wrong_mrp.observe())

    # ---- 4. the same MRP file again loads nothing ----------------------------------------------------------------
    before = co.snapshot()
    status, again = co.upload(store, "mrp_result", (co.dataset / "inputs" / "mrp_result.csv").read_bytes())
    check(
        status == 200
        and again.get("loaded") == 0
        and again.get("already_loaded") == len(mrp_rows)
        and not again.get("changed")
        and not again.get("material_requests"),
        f"the same mrp_result.csv again answered {status} { ({k: again.get(k) for k in ('loaded', 'already_loaded', 'changed', 'material_requests')}) }",
    )
    check(co.snapshot() == before, "loading the same MRP file again changed something")

    # ---- 5. M2: the plan split between Chakan (gross) and Sanand (net) ---------------------------------------------
    raw, parsed = b.scenario_files(co, check, "M2")
    if not raw:
        return out
    audit0, tasks0 = b.audit_counts(co), b.task_ids(co)
    summary = b.import_change(co, check, "M2", raw)
    eff = plant.apply(parsed)
    b.check_import(check, co, "M2", plant, eff, summary, audit0, tasks0)
    after_import = plant.observe()
    b.agree(check, "M2 after the import", plant, co)
    ckn = {i: (v["reserved"], v["requested"]) for (m, i), v in after_import.items() if m == b.MRP_CKN}
    spec_ckn = b._t(
        "RM-01 0/100, RM-03 10/90, RM-05 0/100, RM-06 0/200, RM-07 0/100, RM-08 0/180, RM-09 0/100, RM-10 50/50, "
        "RM-11 5/15, PK-01 0/100"
    )
    check(ckn == spec_ckn, f"M2: the oracle's Chakan lines are not the spec's (oracle self-check): {ckn}", "medium")
    value = plant.request_value(b.MRP_CKN)
    check(value == Decimal("306750"), f"M2: oracle Chakan request value {value}, spec 306750 (self-check)", "medium")
    got_value = sum((co.request(n).get("total", b.Z) for n in co.request_of(b.MRP_CKN)), b.Z)
    check(
        b.money(got_value) == value,
        f"M2: Chakan's purchase request is worth {got_value} after the cut, expected {value}",
    )
    snd = co.q(
        "select no, source_kind, product_code, need_by, location_code from material_request where order_no = :o",
        o=b.MRP_SND,
    )
    if check(len(snd) == 1, f"M2: one new material request {b.MRP_SND} expected, found {len(snd)}"):
        no, kind, prod, need_by, loc = snd[0]
        check(
            kind == "mrp" and prod is None and loc == "PLT-SND" and b.day(need_by) == date(2027, 1, 15),
            f"M2: {b.MRP_SND} is ({kind}, {prod}, {need_by}, {loc}), expected (mrp, none, 2027-01-15, PLT-SND)",
        )
        bases = dict(
            co.q(
                "select r.item_code, r.basis from material_requirement r where r.source_kind = 'mrp' "
                "and r.location_code = 'PLT-SND'"
            )
        )
        check(
            len(bases) == 10 and set(bases.values()) == {"net"},
            f"M2: Sanand's MRP requirements carry basis {bases}, expected 10 net lines",
        )
        card = b.mr_cards(co, store).get(b.MRP_SND, {})
        check(
            {str(ln.get("basis")) for ln in card.get("lines") or []} == {"net"},
            f"M2: the list shows {b.MRP_SND} lines with basis {[ln.get('basis') for ln in card.get('lines') or []]}",
            "medium",
        )
    created = co.audit_rows("material_request.create") + co.audit_rows("material_request.add_lines")
    check(any('"net"' in str(r[4]) for r in created), "M2: no material_request.create/add_lines audit row records "
          "basis net for the Sanand lines", "medium")  # fmt: skip

    # ---- 6. 50 terminal boxes counted at Sanand, then Run store check: net lines buy in full --------------------
    # Block 0b made PLT-SND not yet a stock location (no store until it opens), so a count there is refused; the spec
    # (6.3 M2) assumed two stock locations. Spec gap for the owner: Sanand's store is opened here first, as the
    # scenario's `open_store PLT-SND` step says (review of 21 Nov).
    co.db.execute(text("update location set is_stock_location = true where code = 'PLT-SND'"))
    co.db.commit()
    r = co.call(
        store,
        "POST",
        "/api/inventory/count",
        {"item_code": "RM-10", "location": "PLT-SND", "counted_qty": "50", "reason": "E60: first count at Sanand"},
    )
    check(r.status_code == 200, f"counting 50 RM-10 at PLT-SND answered {r.status_code}: {r.text[:200]}")
    plant.count("RM-10", "PLT-SND", Decimal(50))
    check(co.store_check().status_code == 200, "Run store check after the Sanand count failed")
    plant.store_check()
    b.agree(check, "M2 after the count and the store check", plant, co)
    got = co.observe()
    rm10 = got.get((b.MRP_SND, "RM-10"), {})
    check(
        rm10.get("reserved") == 0 and rm10.get("requested") == 20,
        f"M2: Sanand RM-10 (net 20, 50 free) set aside {rm10.get('reserved')} and requested {rm10.get('requested')}, "
        "expected 0 and 20",
    )
    short_snd = [i for (m, i), v in got.items() if m == b.MRP_SND and v["requested"] != v["need"]]
    check(not short_snd, f"M2: Sanand net lines not bought in full: {short_snd}")
    held = b.D(
        co.one("select coalesce(sum(reserved_qty), 0) from material_request_line where location_code = 'PLT-SND'")
    )
    check(held == 0, f"M2: {held} set aside at Sanand; net lines never take free stock")
    wrong_net = b.Plant(co.dataset, b.Rules(net_as_gross=True), rank)
    wrong_net.store_check(tag="first")
    wrong_net.apply(parsed)
    wrong_net.count("RM-10", "PLT-SND", Decimal(50))
    wrong_net.store_check()
    b.mutation(check, out, "a net line sets free stock aside", plant.observe(), wrong_net.observe())
    b.compare_truth(check, co, "M2", a0, [after_import, plant.observe()], eff)

    # ---- 7. the same M2 file again; the other company sees none of it --------------------------------------------
    b.check_again(check, co, "M2", raw, eff)
    b.isolation(check, co, bw, {"MO-1", "MO-2", "MRP-2027-01", b.MRP_CKN, b.MRP_SND}, bw_before)

    out.metrics.update(
        {
            "MRP lines checked (Chakan + Sanand)": sum(1 for m, _ in plant.observe() if m.startswith("MRP-")),
            "material request lines compared at each step": len(plant.observe()),
            "M2 changes applied": summary["changed"],
            "checks": check.done,
            "failed": len(check.failed),
        }
    )
    out.notes.append(
        "Spec gap: the shape of the import's `material_requests` list is not specified for a request made by an "
        "import; only the new Sanand request in the table is checked (E65 checks the list for a closed request)."
    )
    return out
