"""E92: who never gets a forward buy, and Brightwave's coffee (task 13.5, FB7, FB8), written without the implementation.

Independence: written from the block 3 spec (sections 3.2, 3.4 FB7/FB8, 5.1-5.3, 5.6, 7, 9), the plan and the dataset
only. The oracle is `block3.evaluate` over the raw files (as in E90); eligibility from `master/item.csv` (stocked,
goods, capex), `master/forward_buy_rule.csv` (ITH disabled, OS-03 shelf life 3) and the contracts in force from
`price_record.csv` and `contract.csv` (a price record with a contract; a price list is not one).

Scenarios (23 November 2026):
- Brightwave B0: the coffee (OS-03 at Bengaluru) previewed with no anchor: without the scenario benchmark the October
  price 866 is fresh and only 3.02% under the trend: not_below_trend; with the 790 benchmark (FB7): every number = the
  oracle, saving_too_small; the oracle against `truth_forward_buy.csv`. The laptop (LP-01, capex, ITH disabled):
  not_eligible. Previews write nothing.
- Apex: the castings (contract CNT-0001): on_contract; the cartons (ex works benchmarks only): no_fresh_benchmark.
- A forward-buy request stops replenishment of a min-max item: on Brightwave, OS-03's rule given `min_net_saving_inr 0`,
  2 kg issued (4 left, reorder point 5), replenishment raises a request, the engine recommends on it, the buyer accepts
  and the CFO approves; with the replenishment request cancelled, the next replenishment buys no coffee (the forward-buy
  request's stock part is on order).
- Apex cannot read Brightwave's records (404), and the other way round.

Mutations: a contract or capex item recommended (RM-01, LP-01); a falling-trend item recommended (see the note).
"""

from typing import Any

import block1 as b
import block3 as b3
import env
import lib
from block1 import D, js
from lib import Outcome

ID = "E92"
TITLE = "Contract, capex, disabled and stale-priced items are never recommended; Brightwave's coffee saves too little"
REQUIREMENT = (
    "Task 13.5 (block 3 spec 3.4 FB7/FB8, 5.1-5.3, 7): eligibility (stocked goods, not capex, rule enabled, no contract "
    "price in force), no_fresh_benchmark without a delivered fresh benchmark, not_below_trend, saving_too_small, shelf "
    "life; the preview writes nothing; a forward-buy request counts as stock on order for replenishment; tenant isolation"
)
PREFIX = "e92"
COFFEE = "OS-03"


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
    bw = b3.Co3("brightwave", PREFIX, client)
    bst = b3.start3(bw, check)
    apex = b3.Co3("apex", PREFIX, client)
    state = b3.FbState(bw, bst)
    rules = b.read_csv(bw.dataset / "master" / "forward_buy_rule.csv")
    check(any(r.get("category_code") == "ITH" and str(r.get("enabled")).lower() == "false" for r in rules)
          and any(r.get("item_code") == COFFEE and D(r.get("shelf_life_months")) == 3 for r in rules),
          f"Brightwave's forward_buy_rule.csv lacks ITH disabled or OS-03 shelf life 3: {rules}", "medium")  # fmt: skip

    # ---- FB7 ----
    w = state.want(COFFEE, b3.BLR, anchored=False)
    check(w.reason == "not_below_trend" and w.price_now == D("866"),
          f"oracle: the coffee without FB7's benchmark gives {w.reason} at {w.price_now} (self-check)", "medium")  # fmt: skip
    b3.check_preview(check, "FB7 before", bw, state, COFFEE, b3.BLR)
    bid = state.add_bench(b3.scenario_bench(bw, check, "FB7"))
    check(bid is not None, "FB7: the benchmark could not be entered and verified (setup)", "medium")
    w = state.want(COFFEE, b3.BLR, anchored=False)
    check(w.reason == "saving_too_small", f"oracle: FB7 gives {w.reason} (self-check)", "medium")
    b3.check_preview(check, "FB7", bw, state, COFFEE, b3.BLR)
    truth = b3.truth_fb(bw.truth3("truth_forward_buy", "FB7"))
    wrong = b3.oracle_vs_truth(w, truth)
    check(not wrong, f"FB7: truth_forward_buy against the spec's rules: {'; '.join(wrong)}", "medium")
    out.metrics["FB7 qty / net (oracle)"] = f"{w.qty} / {w.net_saving} ({w.limit})"
    check(not bw.live(COFFEE, b3.BLR), "FB7: a live record for the coffee (it has no live request line)")

    # ---- FB8 ----
    w = state.want("LP-01", b3.BLR, anchored=False)
    check(w.reason == "not_eligible", f"oracle: LP-01 gives {w.reason} (self-check)", "medium")
    b3.check_preview(check, "FB8 LP-01", bw, state, "LP-01", b3.BLR)
    wrong_contract = b3.evaluate(b3.facts_for(apex.dataset, None, "RM-01", b3.CKN, b3.AS_OF),
                                 b3.rule_for(b3.rule_rows(apex.dataset), "RM-01", "RAW", b3.CKN, b3.AS_OF),
                                 b3.with_wrong3(ignore_contract=True))  # fmt: skip
    check(wrong_contract.reason != "on_contract", "FB8 cannot tell a contract item recommended (mutation check)", "medium")
    astate = b3.FbState(apex, None)
    for item, reason in (("RM-01", "on_contract"), ("PK-01", "no_fresh_benchmark")):
        w = astate.want(item, b3.CKN, anchored=False)
        check(w.reason == reason, f"oracle: {item} gives {w.reason}, the spec {reason} (self-check)", "medium")
        b3.check_preview(check, f"FB8 {item}", apex, astate, item, b3.CKN)
    with b3.day(b3.AS_OF):
        code, _ = bw.preview("NOPE-1", b3.BLR)
        check(code == 422, f"a preview of an unknown item answered {code}, expected 422")
        code, _ = bw.preview(COFFEE, "NOWHERE")
        check(code == 422, f"a preview at an unknown location answered {code}, expected 422")
        code, _ = bw.preview(COFFEE, b3.BLR, by=bw.role("store"))
        check(code == 403, f"the store (no price.view) previewing answered {code}, expected 403", "medium")

    _replenishment(out, check, bw, state)
    b3.tenant_check3(check, bw, apex)
    b3.tenant_check3(check, apex, bw)
    out.metrics.update({"checks": check.done, "failed": len(check.failed)})
    out.notes.append(
        "Spec gap: FB7's spec numbers (qty 33, net 2,789.86) cover three months, but the dataset gives OS-03 a shelf "
        "life of 3 months and OP-24 allows only months m <= shelf - 1 = 2, which gives 22 kg and Rs 1,960.80 (still "
        "saving_too_small). The oracle follows the rules; the truth file is compared with them (medium)."
    )
    out.notes.append(
        "Spec gap: the falling-trend mutation cannot be shown on the datasets: the only falling item (LP-01) is capex "
        "and its category disabled, so it never reaches the trend test."
    )
    return out


def _replenishment(out: Outcome, check: b.Checker, co: b3.Co3, state: b3.FbState) -> None:
    """A forward-buy request (all stock part) counts as stock on order: replenishment does not buy the coffee again."""
    store, buyer, cfo = co.role("store"), co.buyer(), co.pm()
    state.add_rule({"item_code": COFFEE, "shelf_life_months": "3", "min_net_saving_inr": "0"})
    with b3.day(b3.AS_OF):
        r = co.call(store, "POST", "/api/inventory/issue", {"item_code": COFFEE, "location": b3.BLR, "qty": "2",
                                                             "reference": "eval: pantry"})  # fmt: skip
        check(r.status_code == 200, f"issuing 2 kg of coffee answered {r.status_code} {r.text[:200]}", "medium")
        before = co.requests_now()
        b.cli("replenish", co.slug)
        co.work()
        first = sorted(n for n in co.requests_now() - before if COFFEE in co.request_lines(n))
    if not first:
        out.notes.append("Setup: replenishment raised no coffee request after the issue; the replenishment part is "
                         "not tested.")  # fmt: skip
        return
    co.fb_cli("--item", COFFEE, "--location", b3.BLR)
    live = co.live(COFFEE, b3.BLR)
    if not live:
        out.notes.append(f"Setup: no forward-buy record for the coffee on {first[0]} (its line may already be ordered); "
                         "the replenishment part is not tested.")  # fmt: skip
        return
    fid = live[-1]["id"]
    with b3.day(b3.AS_OF):
        _, rec = co.fb_get(fid)
        r = co.fb_act(fid, "accept", buyer, {"qty": rec.get("qty"), "reason": "eval: beans before the price rises"})
        check(r.status_code == 200, f"accepting the coffee forward buy answered {r.status_code} {r.text[:200]}", "medium")
        check(co.code_of(js(r).get("approver")) == cfo or js(r).get("approver") == cfo,
              f"the coffee forward buy goes to {js(r).get('approver')}, the rule names the CFO {cfo}", "medium")  # fmt: skip
        r = co.fb_act(fid, "approve", cfo, {})
        new_no = js(r).get("request_no")
        check(r.status_code == 200 and bool(new_no), f"the CFO approving answered {r.status_code} {r.text[:200]}",
              "medium")  # fmt: skip
        if not new_no:
            return
        stock = co.q("select stock_qty, qty from request_line where request_no = :n", n=new_no)
        check(bool(stock) and D(stock[0][0]) == D(stock[0][1]), f"the forward-buy request's stock part {stock}")
        cancelled = False
        for who in [co.one("select requester from request where no = :n", n=first[0]), *co.holders("admin")]:
            if who and co.call(str(who), "POST", f"/api/requisitions/{first[0]}/cancel",
                               {"reason": "eval: replaced by the forward buy"}).status_code == 200:  # fmt: skip
                cancelled = True
                break
        if not cancelled:
            out.notes.append(f"Setup: the replenishment request {first[0]} could not be cancelled; the skip is not "
                             "isolated and not tested.")  # fmt: skip
            return
        before = co.requests_now()
        b.cli("replenish", co.slug)
        co.work()
        again = sorted(n for n in co.requests_now() - before if COFFEE in co.request_lines(n))
        on_hand = D(co.one("select on_hand from stock_level where item_code = :i and location_code = :l", i=COFFEE,
                           l=b3.BLR))  # fmt: skip
        check(not again, f"replenishment bought the coffee again ({again}) although the forward-buy request {new_no} has "
              f"{stock[0][0] if stock else '?'} kg on order (on hand {on_hand})")  # fmt: skip
        out.metrics["coffee forward-buy request"] = f"{new_no}: {stock[0][0] if stock else '?'} kg"
