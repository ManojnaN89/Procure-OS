"""E90: forward-buy numbers against an independent oracle (task 13.5), written without the implementation.

Independence: written from the block 3 spec (sections 3.4, 5.1-5.4, 5.6, 9), the plan and the dataset only. The oracle
(`block3.evaluate`) is spec 5.2 in plain `Decimal` code over the raw files: trend points are the monthly averages of
`master/market_benchmark.csv` (verified, delivered, in region, convertible) for the 12 complete months before November
2026, a least-squares line, the dip of the newest counting benchmark (D52 freshness from `price_threshold_rule.csv`), the
average use from `history/stock_movement.csv`, the cover from block 2's replayed usable stock and A0's open requests,
the months ahead, the limits (cover months, holding cost, shelf life, storage, budget left), whole packs cut from the
latest month, the money. Rule rows from spec 5.3 (company v1), `master/forward_buy_rule.csv` and each scenario's
`rule.csv`; benchmarks from each scenario's `benchmark.csv`.

Scenarios (Apex Pumps, 23 November 2026), two resets:
- A0 alone: no `forward_buy` record for the wire, an evaluation in `rule_evaluation` saying no_fresh_benchmark.
- FB1 (copper dips to 828): one open record anchored on the MRP request's wire line, every field = the oracle (a, b, dip,
  A, cover, months, quantity, money, limit), the oracle = `truth_forward_buy.csv`; the anchored preview the same and
  writing nothing.
- FB2 (12 months of cover, storage 2,500): the holding cost stops it at month 8 (1,989 kg); FB1's record superseded by
  the new one; one live record.
- FB3 (storage 600): 600 kg, limit storage.
- FB8 on Apex: the castings (contract CNT-0001) on_contract, the cartons (ex works only) no_fresh_benchmark.
- Reset, FB1, FB5: the smallest saving set to exactly the net saving -> none (saving_too_small, the record lapses); one
  paisa lower -> recommended. FB4: a benchmark of 870 on the same day -> the higher one counts, not_below_trend.
- The CLI's dry run writes nothing. Ends with a tenant check against Brightwave.

Mutations: holding cost ignored (FB2 then gives 2,500, the storage cap, not 1,989); cover months beyond
`max_cover_months` (FB1 then gives 1,500, the storage cap, not 746).
"""

from typing import Any

import block1 as b
import block3 as b3
import env
import lib
from block1 import D
from lib import Outcome

ID = "E90"
TITLE = "Forward-buy recommendations equal an independent oracle: trend, dip, cover, months, limits and money"
REQUIREMENT = (
    "Task 13.5 (block 3 spec 3.4 FB1-FB5, FB8, 5.1-5.4, 5.6): price now = newest counting benchmark; trend = least squares "
    "of 12 monthly averages; dip above min_gap_pct; cover from usable stock and open supply; months 1..max_cover with "
    "net per unit above 0; storage and budget limits; whole packs from the latest month; recommend only when net > "
    "min_net_saving_inr; one live record per item and location (superseded, lapsed); every evaluation in "
    "rule_evaluation; preview writes nothing; tenant isolation"
)
PREFIX = "e90"
WIRE = "RM-08"


def run(ctx: Any) -> Outcome:
    env.forget_tenants(PREFIX)
    try:
        return _run(ctx)
    finally:
        env.forget_tenants(PREFIX)


def _run(ctx: Any) -> Outcome:  # one story, told in order
    from fastapi.testclient import TestClient
    from p2p.main import app

    out = Outcome(True)
    check = b.Checker(out)
    lib.capture_mail()
    client = TestClient(app)
    apex = b3.Co3("apex", PREFIX, client)
    bw = b3.Co3("brightwave", PREFIX, client)
    st = b3.start3(apex, check)
    state = b3.FbState(apex, st)
    file_rules = b.read_csv(apex.dataset / "master" / "forward_buy_rule.csv")
    check(any(r.get("item_code") == WIRE and r.get("location_code") == b3.CKN and D(r.get("storage_max_qty")) == 1500
              for r in file_rules), "forward_buy_rule.csv lacks RM-08@PLT-CKN storage 1500 (spec 3.2 step 3)", "medium")  # fmt: skip

    # ---- A0 alone ----
    want = state.want(WIRE, b3.CKN)
    check(want.reason == "no_fresh_benchmark", f"oracle: A0 gives {want.reason}, the spec no_fresh_benchmark (self-check)",
          "medium")  # fmt: skip
    check(not apex.fb_rows(WIRE, b3.CKN), f"A0: forward-buy records for the wire: {apex.fb_rows(WIRE, b3.CKN)}")
    evals = apex.evaluations(WIRE)
    check(bool(evals) and "no_fresh_benchmark" in str(evals[-1][3]),
          f"A0: the wire's last forward_buy evaluation is {evals[-1][3][:200] if evals else 'missing'}; every "
          "evaluation is recorded, here with no_fresh_benchmark (spec 5.4)")  # fmt: skip
    b3.check_preview(check, "A0", apex, state, WIRE, b3.CKN, anchored=True)

    # ---- FB1 ----
    bid = state.add_bench(b3.scenario_bench(apex, check, "FB1"))
    check(bid is not None, "FB1: the benchmark could not be entered and verified (setup)", "medium")
    rec1 = b3.check_record(check, "FB1", apex, state, WIRE, b3.CKN, truth="FB1", request_label=b3.MRP)
    b3.check_preview(check, "FB1", apex, state, WIRE, b3.CKN, anchored=True)
    right = state.want(WIRE, b3.CKN)
    unbounded = state.want(WIRE, b3.CKN, wrong=b3.with_wrong3(cover_unbounded=True))
    check(right.qty != unbounded.qty, f"FB1 cannot tell cover months beyond max_cover_months ({right.qty} both; "
          "mutation check)", "medium")  # fmt: skip
    if rec1:
        check(D(rec1.get("qty")) != unbounded.qty, f"FB1: quantity {rec1.get('qty')} is the unbounded cover's")
        bench = rec1.get("benchmark") or {}
        check(D(bench.get("price")) == right.price_now and str(bench.get("effective_date", ""))[:10] ==
              right.bench.effective.isoformat() if right.bench else False,
              f"FB1: the record's benchmark {bench}", "medium")  # fmt: skip
        check("forward_buy" in str(rec1.get("rule") or "") and "RM-08" in str(rec1.get("rule") or ""),
              f"FB1: rule label {rec1.get('rule')!r}; expected the rows used, e.g. 'forward_buy v1 + RM-08@PLT-CKN v1'",
              "low")  # fmt: skip
        out.metrics["FB1 qty / net (platform)"] = f"{rec1.get('qty')} / {rec1.get('net_saving')}"
    out.metrics["FB1 qty / net (oracle)"] = f"{right.qty} / {right.net_saving}"

    # ---- FB2 ----
    for row in b3.scenario_rules(apex, check, "FB2"):
        state.add_rule(row)
    apex.fb_cli("--item", WIRE, "--location", b3.CKN)
    rec2 = b3.check_record(check, "FB2", apex, state, WIRE, b3.CKN, truth="FB2", request_label=b3.MRP)
    no_hold = state.want(WIRE, b3.CKN, wrong=b3.with_wrong3(no_holding=True))
    check(no_hold.qty != state.want(WIRE, b3.CKN).qty, "FB2 cannot tell holding cost ignored (mutation check)", "medium")
    if rec2:
        check(D(rec2.get("qty")) != no_hold.qty, f"FB2: quantity {rec2.get('qty')} is what ignoring holding gives")
    if rec1 and rec2:
        rows = {r["id"]: r for r in apex.fb_rows(WIRE, b3.CKN)}
        old = rows.get(rec1["_id"], {})
        check(old.get("status") == "superseded" and str(old.get("superseded_by")) == str(rec2["_id"]),
              f"FB2: FB1's record is {old.get('status')} superseded_by {old.get('superseded_by')}, expected superseded by "
              f"{rec2['_id']}")  # fmt: skip
    check(len(apex.live(WIRE, b3.CKN)) == 1, f"FB2: {len(apex.live(WIRE, b3.CKN))} live records, one expected")

    # ---- FB3 ----
    for row in b3.scenario_rules(apex, check, "FB3"):
        state.add_rule({**row, "max_cover_months": row.get("max_cover_months", "")})
    apex.fb_cli("--item", WIRE, "--location", b3.CKN)
    b3.check_record(check, "FB3", apex, state, WIRE, b3.CKN, truth="FB3", request_label=b3.MRP)

    # ---- FB8 on Apex ----
    for item, reason in (("RM-01", "on_contract"), ("PK-01", "no_fresh_benchmark")):
        w = state.want(item, b3.CKN, anchored=False)
        check(w.reason == reason, f"oracle: {item} gives {w.reason}, the spec {reason} (self-check)", "medium")
        b3.check_preview(check, f"FB8 {item}", apex, state, item, b3.CKN)
        check(not apex.live(item, b3.CKN), f"FB8: a live record for {item}: {apex.live(item, b3.CKN)}")

    # ---- the CLI's dry run ----
    before = (apex.fb_rows(WIRE, b3.CKN), len(apex.evaluations()))
    with b3.day(b3.AS_OF):
        code, said = apex.fb_cli("--item", WIRE, "--location", b3.CKN, "--dry-run")
    check(code == 0 and (apex.fb_rows(WIRE, b3.CKN), len(apex.evaluations())) == before,
          f"forward-buy --dry-run: exit {code}, changed the records or evaluations: {said[:200]}", "medium")  # fmt: skip
    if "owner" in state.modes:
        out.notes.append("Rule versions were written as the owner: none of the paths " + ", ".join(b3.RULE_PATHS) +
                         " answered 202 (the spec does not name the admin path; the engine was still tested).")  # fmt: skip

    # ---- second reset: FB5 both sides of the boundary, then FB4 ----
    apex.reset()
    st = b3.start3(apex, check)
    state = b3.FbState(apex, st)
    state.add_bench(b3.scenario_bench(apex, check, "FB1"))
    first = b3.record_of(apex, check, "FB5 start", WIRE, b3.CKN)
    rules5 = b3.scenario_rules(apex, check, "FB5")
    for row in rules5:
        state.add_rule(row)
    apex.fb_cli("--item", WIRE, "--location", b3.CKN)
    want5 = state.want(WIRE, b3.CKN)
    check(want5.reason == "saving_too_small", f"oracle: FB5 gives {want5.reason} (self-check)", "medium")
    b3.check_record(check, "FB5 at the net saving", apex, state, WIRE, b3.CKN, truth="FB5")
    if first:
        rows = {r["id"]: r for r in apex.fb_rows(WIRE, b3.CKN)}
        old = rows.get(first["_id"], {})
        check(old.get("status") == "lapsed" and "saving_too_small" in str(old.get("lapse_reason") or ""),
              f"FB5: the FB1 record is {old.get('status')} ({old.get('lapse_reason')}); expected lapsed, saving_too_small")  # fmt: skip
    company = next((r for r in rules5 if not r.get("item_code") and not r.get("category_code")), None)
    if company:
        lower = D(company.get("min_net_saving_inr")) - D("0.01")
        state.add_rule({**company, "min_net_saving_inr": f"{lower:.2f}"})
        apex.fb_cli("--item", WIRE, "--location", b3.CKN)
        check(state.want(WIRE, b3.CKN).recommended, "oracle: one paisa below the net saving should recommend", "medium")
        b3.check_record(check, "FB5 one paisa below", apex, state, WIRE, b3.CKN, request_label=b3.MRP)
    state.add_bench(b3.scenario_bench(apex, check, "FB4"))
    want4 = state.want(WIRE, b3.CKN)
    check(want4.reason == "not_below_trend" or want4.price_now != D("870"),
          f"oracle: FB4 gives {want4.reason} at {want4.price_now} (self-check)", "medium")  # fmt: skip
    b3.check_record(check, "FB4 (870 on the same day)", apex, state, WIRE, b3.CKN)
    b3.check_preview(check, "FB4", apex, state, WIRE, b3.CKN, anchored=True)
    fb4 = apex.truth3("truth_forward_buy", "FB4")
    if fb4:
        check(fb4[0].get("reason") == "not_below_trend", f"truth FB4 reason {fb4[0].get('reason')}", "medium")
    b3.tenant_check3(check, apex, bw)
    out.metrics.update({"checks": check.done, "failed": len(check.failed)})
    out.notes.append(
        "Spec gap: 5.2's budget limit takes 'the least available - requested of the anchor request's groups'; read "
        "literally that includes the MRP request's 1420 (cartons) group, Rs 3,88,750 left, which would cap FB1 at 422 kg. "
        "The spec's own FB1 (746 kg) implies the wire line's own account (1410), which the oracle uses."
    )
    out.notes.append(
        "Spec gap: the `limit` when several bounds meet is not ordered; the oracle names the bound that cut the "
        "quantity last (budget or storage before holding, shelf life, max_cover_months); `need` is never produced."
    )
    out.notes.append(
        "Setup, not a spec rule: FB4 is entered on the same day as FB1 in the second company, so the tie rule (highest "
        "price) makes 870 the price now; a second reset for FB4 alone would give the same answer."
    )
    return out
