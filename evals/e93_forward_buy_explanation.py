"""E93: the forward-buy explanation never invents (task 13.5, spec 5.5), written without the implementation.

Independence: written from the block 3 spec (sections 3.4 FB1, 5.5, 5.6, 9, 10), the plan and the dataset only. The
oracle writes the template sentence itself from its own FB1 numbers (`block3.evaluate`), the item's name and unit from
`master/item.csv`, the verifier's name from `master/user.csv` and the rule's holding cost, and checks it against the
spec's sentence word for word. The number check reads every number in a text and accepts it only when the record (or
the rule in force) holds it, as written or rounded to 0, 2 or 4 places.

Scenario (Apex Pumps, 23 November 2026): FB1. The record's `explanation` equals the template with the record's numbers
(`explanation_source` template with no model; with `--live` the AI step may replace it: then its text passes the number
check and is at most 600 characters, else the template stays). Every number in the explanation is in the record. The
checker itself refuses a text with a changed quantity (750 kg) and a made-up date. The audit row of the creation.
Ends with a tenant check against Brightwave.

Mutations: the template reads a number not in the record; model text with a changed quantity accepted.
"""

from decimal import Decimal
from typing import Any

import block1 as b
import block3 as b3
import env
import lib
from block1 import D
from lib import Outcome

ID = "E93"
TITLE = "The forward-buy explanation is the template with the record's own numbers, or an AI text that invents none"
REQUIREMENT = (
    "Task 13.5, rule 2 (block 3 spec 5.5): the template sentence with numbers only from the record; the AI step "
    "(one call, summary <= 600 characters, at most 3 risks) passes an evidence check of every number and date or the "
    "template stays; explanation_source template or ai; audit; tenant isolation"
)
PREFIX = "e93"
WIRE = "RM-08"
SPEC_SENTENCE = (
    "Copper winding wire costs Rs 828.00 a kg on the market now (Pune wire bulletin 2026-11 (mid-month), verified by "
    "Sanjay Rao), 8.36% below its 12-month trend of Rs 903.50, which rises about Rs 2.41 a month. Buying 746 kg more with "
    "the January delivery covers February to April 2027 at about 248.71 kg a month. Saving Rs 63,511.45 before holding, "
    "Rs 18,529.09 to hold the stock at 1.5% a month: Rs 44,982.36 net. Risks: the price may fall further; Rs 6,86,320.00 "
    "is tied up until April 2027."
)
UNIT = {"KGS": "kg", "LTR": "litre", "NOS": "piece"}
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November",
          "December")  # fmt: skip


def words(n: Decimal) -> str:
    """A quantity as people read it: 746, 248.71 (2 places at most, no trailing zeros)."""
    return b.fq(b3.r2(n))


def pct(n: Decimal) -> str:
    return b.fq(n.normalize()) if n == n.to_integral_value() else f"{n.normalize():f}"


def template(w: b3.Result, item_name: str, unit: str, source_ref: str, verifier: str, holding_pct: Decimal,
             anchor_month: str) -> str:  # fmt: skip
    """The spec's template (5.5), built from the oracle's numbers only."""
    first, last = w.months[0]["month"], w.months[-1]["month"]
    first_name, last_name = MONTHS[int(first[5:7]) - 1], MONTHS[int(last[5:7]) - 1]
    span = (f"{first_name} to {last_name} {last[:4]}" if first[:4] == last[:4]
            else f"{first_name} {first[:4]} to {last_name} {last[:4]}")  # fmt: skip
    a = w.avg_use or Decimal(0)
    direction = "rises" if (w.slope or 0) >= 0 else "falls"
    return (
        f"{item_name} costs Rs {b3.r2(w.price_now or 0):.2f} a {unit} on the market now ({source_ref}, verified by "
        f"{verifier}), {b3.r2(w.dip_pct or 0):.2f}% below its 12-month trend of Rs {b3.r2(w.trend_now or 0):.2f}, which "
        f"{direction} about Rs {b3.r2(abs(w.slope or 0)):.2f} a month. Buying {words(w.qty)} {unit} more with the "
        f"{MONTHS[int(anchor_month[5:7]) - 1]} delivery covers {span} at about {b3.r2(a):.2f} {unit} a month. Saving Rs "
        f"{b3.inr(w.gross_saving)} before holding, Rs {b3.inr(w.holding_cost)} to hold the stock at {pct(holding_pct)}% a "
        f"month: Rs {b3.inr(w.net_saving)} net. Risks: the price may fall further; Rs {b3.inr(w.value)} is tied up until "
        f"{last_name} {last[:4]}."
    )


def norm(text_: str) -> str:
    return " ".join(str(text_ or "").split())


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
    bench = b3.scenario_bench(apex, check, "FB1")
    create0 = apex.audit_count("forward_buy.create")
    state.add_bench(bench)
    w = state.want(WIRE, b3.CKN)
    rule = b3.rule_for(state.rules, WIRE, state.items[WIRE]["category_code"], b3.CKN, b3.AS_OF)
    verifier = apex.people[apex.pm()]["name"]
    item = state.items[WIRE]
    anchor = state.anchor(WIRE, b3.CKN)
    mine = template(w, item["name"], UNIT.get(item["base_uom"], item["base_uom"].lower()), bench.get("source_ref", ""),
                    verifier, rule.holding_cost_pct_month, b3.month(anchor) if anchor else b3.month(b3.AS_OF))  # fmt: skip
    check(norm(mine) == norm(SPEC_SENTENCE), f"oracle: the template from the oracle's numbers differs from the spec's "
          f"sentence (self-check):\n  {mine}\n  {SPEC_SENTENCE}", "medium")  # fmt: skip

    rec = b3.record_of(apex, check, "FB1", WIRE, b3.CKN)
    if not check(rec is not None, "FB1: no record to explain"):
        return out
    assert rec is not None
    text_ = str(rec.get("explanation") or "")
    source = rec.get("explanation_source")
    stored = apex.one("select explanation from forward_buy where id = :i", i=rec["_id"])
    check(norm(stored) == norm(text_), "the API's explanation is not the stored one", "medium")
    allowed = b3.record_numbers(rec, [rule.holding_cost_pct_month, 12, bench.get("source_ref", "")])
    made_up = b3.invented(text_, allowed)
    check(bool(text_) and not made_up, f"the explanation has numbers the record does not hold: {made_up}: {text_[:300]}")
    if source == "template":
        check(norm(text_) == norm(mine), f"the template differs from the spec's sentence with the record's numbers:\n"
              f"  got:  {text_}\n  want: {mine}")  # fmt: skip
    elif source == "ai":
        # the summary is at most 600 characters; the stored text may add the (at most 3) risks
        check(len(text_) <= 1200, f"the AI explanation is {len(text_)} characters", "medium")
        out.notes.append(f"the AI explanation: {text_[:300]}")
    else:
        check(False, f"explanation_source {source!r}, expected template or ai")
    check(apex.audit_count("forward_buy.create") >= create0 + 1, "no forward_buy.create audit row", "medium")

    # the checker itself: a changed quantity and a made-up date are caught (the mutation checks)
    for bad in (SPEC_SENTENCE.replace("746 kg", "750 kg"), SPEC_SENTENCE + " Prices recover by 15 March 2027."):
        check(bool(b3.invented(bad, allowed)), f"the number check accepts an invented figure (mutation check): "
              f"{bad[-80:]}", "medium")  # fmt: skip
    check(D(rec.get("qty")) == w.qty, f"the record's quantity {rec.get('qty')}, the oracle {w.qty}")

    if ctx.live:
        _live(out, check, apex, rec, allowed)
    b3.tenant_check3(check, apex, bw)
    out.metrics.update({"checks": check.done, "failed": len(check.failed), "explanation_source": source})
    out.notes.append(
        "Spec gap: the template's numbers 12 (the trend window) and 1.5% (the rule's holding cost) are not record "
        "fields in 5.6; the number check accepts them from the rule in force."
    )
    return out


def _live(out: Outcome, check: b.Checker, co: b3.Co3, rec: dict[str, Any], allowed: set[Decimal]) -> None:
    """With the real models: run the explanation job; an AI text must pass the number check, else the template stays."""
    from p2p.platform import worker

    with b3.day(b3.AS_OF):
        worker.enqueue(co.slug, "forward_buy_explain", {"id": rec["_id"]})
        co.work()
    row = co.q("select explanation, explanation_source from forward_buy where id = :i", i=rec["_id"])
    if not row:
        return
    text_, source = str(row[0][0] or ""), row[0][1]
    made_up = b3.invented(text_, allowed)
    check(not made_up, f"live: the {source} explanation has numbers the record does not hold: {made_up}")
    out.metrics["live explanation_source"] = source
