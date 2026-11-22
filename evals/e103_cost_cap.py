"""E103: model prices and the monthly cost cap (task 11a.6, DF-32), written without the implementation.

Independence: written from the AI foundation spec (2.7, OP-12, OP-13, OP-14) only. The oracle's prices are this eval's
own row: provider `scripted`, model `paid-1`, 1.00 USD in and 2.00 USD out per million tokens; the scripted provider
answers every call with 1,000 tokens in and 500 out, so each paid call costs exactly 0.001 + 0.001 = 0.002000 USD.

Scenarios, on the main demo company (Apex) and Brightwave:
1. `costs.price` finds the row; `costs.cost(price, 1000, 500)` is the Decimal 0.002000; 0 and 1,000,000 tokens.
2. Brightwave (no cap) spends 0.008 first (each paid row exactly 0.002000; a repeated prompt is a cache hit at cost 0):
   it never counts for Apex.
3. Apex's cap 0.005 is set by two administrators (`ai_monthly_cost_cap_usd`). Paid calls succeed while the month's
   spend is under the cap; once the spend reaches it every further call raises `CostCapReached` (a `ModelError`),
   the provider is not called and a `model_call` row with outcome `error` is written. The first call's row costs exactly
   0.002000; later calls cost 0.0022 (1,200 tokens in), so the second takes the month past 80%.
4. Past 80% one `ai_cost` notification per administrator for the month (event `ai_cost.warn`, key YYYY-MM, once).
5. With the cap reached: a planning explanation falls back to `facts_only` with the reason; a quote reading opens its
   review (draft `needs_person`) without calling the model.
6. A model with no price: refused while a cap is set (Apex), allowed at cost 0 without one (Brightwave).
7. `GET /api/setup/ai-cost?month=` totals equal the `model_call` rows of the month (`ok` and `invalid`), per capability
   too; the cap and warning percent are shown; the unpriced model is listed.
Mutations caught: a float cost (scenario 1, 3 exact equality); the cap checked after the call (scenario 3: the provider
is called); spend summed across companies (scenario 2 makes Apex's first call refused).
"""

import contextlib
import json
import os
from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import ai_harness
import aif
import block1 as b
import block2 as s2
import env
import lib
from ai_cases import Item
from block1 import js
from lib import Outcome

ID = "E103"
TITLE = "Model calls are priced from the price file and stop at the company's monthly cap"
REQUIREMENT = (
    "DF-32 (spec 2.7): cost = tokens x the price in force (Decimal, 6 places); a company's month spend is its own; a "
    "call whose worst case would pass the cap is refused before the provider is asked (CostCapReached, a ModelError: "
    "planning gives facts only, an AI step a review); an unpriced model is refused when a cap is set; one warning per "
    "month past the warning percent; the cost card adds up to the model_call rows"
)
PREFIX = "e103"
PRICE_ROW = "scripted,paid-1,2026-01-01,1.00,2.00,paid,eval row,2026-11-06"
CAP = Decimal("0.005")
EACH = Decimal("0.002000")


def run(ctx: Any) -> Outcome:
    env.forget_tenants(PREFIX)
    try:
        with _price_row() as how:
            out = _run(ctx)
            out.notes.append(f"The eval's price row was given through {how}.")
            return out
    finally:
        env.forget_tenants(PREFIX)


@contextlib.contextmanager
def _price_row() -> Iterator[str]:
    """The test price: `MODEL_PRICES` in the environment (as MODEL_ROUTES, spec 2.7), as JSON rows of the file's
    columns; if that is not read, the row is added through `costs.EXTRA` (the reference file is never edited)."""
    from p2p.ai_steps import costs
    from p2p.config import get_settings

    head = [
        "provider",
        "model",
        "effective_from",
        "usd_in_per_mtok",
        "usd_out_per_mtok",
        "plan",
        "source",
        "checked_on",
    ]
    saved_env = os.environ.get("MODEL_PRICES")
    os.environ["MODEL_PRICES"] = json.dumps([dict(zip(head, PRICE_ROW.split(","), strict=True))])
    _clear(costs, get_settings)
    try:
        if costs.price("scripted", "paid-1", datetime.now(UTC).date()) is not None:
            yield "MODEL_PRICES"
            return
        # never the reference file itself: the test hook of ai_steps/costs.py (class c, the spec names no other way)
        p = PRICE_ROW.split(",")
        costs.EXTRA.append(costs.Price(p[0], p[1], date.fromisoformat(p[2]), Decimal(p[3]), Decimal(p[4]), p[5], p[6]))
        yield "costs.EXTRA"
    finally:
        costs.EXTRA.clear()
        if saved_env is None:
            os.environ.pop("MODEL_PRICES", None)
        else:
            os.environ["MODEL_PRICES"] = saved_env
        _clear(costs, get_settings)


def _clear(costs: Any, get_settings: Any) -> None:
    get_settings.cache_clear()
    for name in dir(costs):
        fn = getattr(costs, name)
        if callable(fn) and hasattr(fn, "cache_clear"):
            fn.cache_clear()


def _run(ctx: Any) -> Outcome:
    from fastapi.testclient import TestClient

    from p2p.main import app

    out = Outcome(True)
    check = b.Checker(out)
    aif.install_smtp()
    client = TestClient(app)
    co = s2.Co("apex", PREFIX, client)
    bw = s2.Co("brightwave", PREFIX, client)
    model = aif.Model(default={"final": "Done."}, tokens=(1000, 500))
    with aif.no_live_models(aif.Model()):
        _prices(check)
        _brightwave(check, bw, model)
        _cap(check, co, model)
        _fallbacks(check, co, model)
        _unpriced(check, co, bw, model)
        _card(check, co)
    out.metrics.update({"checks": check.done, "failed": len(check.failed), "paid calls": model.calls})
    out.notes += [
        (
            "Spec gap: the worst case of a call ('estimate at the output price') depends on the token estimate, which the "
            "spec does not fix; the eval checks the boundary from the spend side: under the cap a call may run, at or "
            "past it none does."
        ),
        (
            "Spec gap: the shape of MODEL_PRICES is not given; the eval tries JSON rows of the file's columns and falls "
            "back to costs.EXTRA."
        ),
    ]
    return out


def _prices(check: b.Checker) -> None:
    from p2p.ai_steps import costs

    p = costs.price("scripted", "paid-1", datetime.now(UTC).date())
    if not check(p is not None, "the eval's price row is not found by costs.price"):
        return
    check(costs.price("scripted", "paid-1", date(2025, 12, 31)) is None, "a price is found before its effective date",
          "medium")  # fmt: skip
    got = costs.cost(p, 1000, 500)
    check(isinstance(got, Decimal) and got == EACH and str(got) == "0.002000", f"cost of 1,000 in and 500 out is "
          f"{got!r}, expected Decimal('0.002000')")  # fmt: skip
    check(costs.cost(p, 0, 0) == 0, f"cost of no tokens is {costs.cost(p, 0, 0)!r}")
    check(costs.cost(p, 1_000_000, 1_000_000) == Decimal("3.000000"), "a million tokens each way is not 3.000000")
    check(costs.cost(p, 1, 0) == Decimal("0.000001"), f"one input token costs {costs.cost(p, 1, 0)!r}, expected "
          "0.000001 (6 places)", "medium")  # fmt: skip


def _paid(model: aif.Model) -> Any:
    return aif.gateway(model, provider="scripted", name="paid-1")


def _call(db: Any, gw: Any, n: int, *, same: bool = False) -> tuple[str, Any]:
    """One completion; returns ('ok', completion) or ('refused', the error)."""
    text_ = "Say done." if same else f"Say done, call {n}."
    try:
        got = gw.complete("eval_cost", [{"role": "user", "content": text_}], db=db, max_tokens=500)
        db.commit()
        return "ok", got
    except Exception as exc:  # noqa: BLE001 - CostCapReached or the ModelError the gateway raises after it
        db.rollback()
        return "refused", exc


def _month_spend(co: s2.Co) -> Decimal:
    return b.D(co.one("select coalesce(sum(cost_usd), 0) from model_call where outcome in ('ok', 'invalid') and "
                      "to_char(at at time zone 'UTC', 'YYYY-MM') = :m", m=datetime.now(UTC).strftime("%Y-%m")))  # fmt: skip


def _brightwave(check: b.Checker, bw: s2.Co, model: aif.Model) -> None:
    db = aif.new_session(bw)
    try:
        for n in range(3):
            status, got = _call(db, _paid(model), 100 + n)
            check(status == "ok", f"Brightwave (no cap) call {n + 1} was refused: {got}")
        first = aif.rows(bw, "select cost_usd, outcome from model_call order by id desc limit 1")[0]
        check(first["cost_usd"] == EACH and first["outcome"] == "ok", f"a paid call's row costs {first['cost_usd']!r} "
              f"({first['outcome']}), expected 0.002000")  # fmt: skip
        hits = model.calls
        _call(db, _paid(model), 0, same=True)
        _call(db, _paid(model), 0, same=True)
        hit = aif.rows(bw, "select cost_usd, outcome from model_call order by id desc limit 1")[0]
        check(model.calls == hits + 1 and hit["outcome"] == "cache_hit" and hit["cost_usd"] == 0, f"a repeated prompt "
              f"called the model {model.calls - hits} time(s), last row {hit}, expected one call then a cache hit at "
              "cost 0")  # fmt: skip
    finally:
        db.close()
    check(_month_spend(bw) == 4 * EACH, f"Brightwave's month spend is {_month_spend(bw)}, expected {4 * EACH}")


def _cap(check: b.Checker, co: s2.Co, model: aif.Model) -> None:
    from p2p.ai_steps import adapters, costs

    asked, approved = co.policy("ai_monthly_cost_cap_usd", str(CAP))
    check(asked.status_code == 202 and approved is not None and approved.status_code == 200, "setting the cap by two "
          f"administrators answered {asked.status_code} / {getattr(approved, 'status_code', None)}")  # fmt: skip
    db = aif.new_session(co)
    results: list[tuple[str, Decimal, int, Any]] = []
    try:
        for n in range(8):
            spend = _month_spend(co)
            calls = model.calls
            status, got = _call(db, _paid(model), n)
            results.append((status, spend, model.calls - calls, got))
            if n == 0 and status == "ok":
                first = aif.rows(co, "select cost_usd, outcome from model_call order by id desc limit 1")[0]
                check(first["cost_usd"] == EACH and first["outcome"] == "ok", f"the first paid call's row costs "
                      f"{first['cost_usd']!r} ({first['outcome']}), expected 0.002000")  # fmt: skip
                model.tokens = (1200, 500)  # 0.0022 a call from here: the second call takes the month past 80%
    finally:
        db.close()
    check(results[0][0] == "ok", f"Apex's first paid call was refused ({results[0][3]}): another company's spend "
          "counted, or the first call's worst case was taken as above the cap")  # fmt: skip
    refused_at = next((i for i, r in enumerate(results) if r[0] == "refused"), None)
    if not check(refused_at is not None, f"no call was refused with a cap of {CAP} after spending {_month_spend(co)}"):
        return
    check(
        all(r[0] == "refused" for r in results[refused_at:]), "a call ran after an earlier one was refused for the cap"
    )
    for status, spend, asked_provider, got in results:
        if status == "ok":
            check(spend < CAP, f"a call ran with the month's spend already {spend}, at or past the cap {CAP}")
        else:
            check(asked_provider == 0, f"a refused call still asked the provider ({asked_provider} call(s)): the cap "
                  "must be checked before")  # fmt: skip
            check(isinstance(got, (costs.CostCapReached, adapters.ModelError)), f"a refused call raised "
                  f"{type(got).__name__}, expected CostCapReached or a ModelError")  # fmt: skip
    errors = aif.rows(co, "select outcome, error from model_call where outcome = 'error' order by id desc limit 1")
    check(bool(errors) and ("budget" in str(errors[0]["error"]).lower() or "cap" in str(errors[0]["error"]).lower()),
          f"the refused call's model_call row: {errors[:1]}, expected outcome error with the reason")  # fmt: skip
    check(issubclass(costs.CostCapReached, adapters.BadRequest) and issubclass(costs.CostCapReached,
          adapters.ModelError), "CostCapReached is not a BadRequest and a ModelError (spec 2.7)", "medium")  # fmt: skip
    # the warning: once per month, to the administrators
    notes = aif.rows(co, "select user_id, count(*) as n from notification where kind = 'ai_cost' group by user_id")
    check(bool(notes), "no ai_cost warning after the spend passed 80% of the cap")
    check(all(int(n["n"]) == 1 for n in notes), f"ai_cost warnings per person {[(n['user_id'], n['n']) for n in notes]}"
          ", expected one each for the month")  # fmt: skip
    check(all(aif.holds(co, n["user_id"], ("admin",)) for n in notes), "an ai_cost warning went to someone who is "
          "not an administrator", "medium")  # fmt: skip
    month = datetime.now(UTC).strftime("%Y-%m")
    check(aif.count(co, "select count(*) from event where type = 'ai_cost.warn' and key = :k", k=month) == 1,
          f"not exactly one ai_cost.warn event keyed {month}", "medium")  # fmt: skip


def _fallbacks(check: b.Checker, co: s2.Co, model: aif.Model) -> None:
    from p2p.agents import planning
    from p2p.sourcing import replies

    aif.planning_on(co, check)
    co.base_state()
    mr = next((no for label, no, _ in co.mr_labels() if label == "MO-1"), None)
    if check(mr is not None, "set-up: no material request for MO-1", "medium"):
        calls = model.calls
        db = aif.new_session(co)
        try:
            got = planning.explain(db, co.slug, co.user_id(co.role("store")), str(mr), _paid(model))
            db.commit()
        finally:
            db.close()
        reasons = " ".join(str(r) for r in getattr(got, "reasons", None) or []).lower()
        check(getattr(got, "status", None) == "facts_only" and ("budget" in reasons or "cap" in reasons),
              f"planning past the cap: {getattr(got, 'status', None)} with reasons {reasons[:200]!r}, expected "
              "facts_only naming the budget")  # fmt: skip
        check(model.calls == calls, "planning past the cap still called the model")
    category = lib.busiest_category()
    item = next(r for r in co.rows("master", "item") if r["category_code"] == category)
    world = SimpleNamespace(db=co.db, slug=co.slug)
    rfq, _s, email = ai_harness.setup_rfq(world, "E103", [Item(1, item["code"], item["name"], Decimal(5), Decimal(100),
                                                               Decimal(18))])  # fmt: skip
    email_id = ai_harness.deliver(world, rfq, email, "Rate Rs 99 per unit, GST 18%, delivery 6 days.")
    calls = model.calls
    db = aif.new_session(co)
    try:
        replies.read_reply(db, co.slug, email_id, _paid(model), as_of=lib.TODAY)
        db.commit()
    finally:
        db.close()
    status = co.one("select status from quote_draft where email_id = :e", e=email_id)
    check(status == "needs_person" and model.calls == calls, f"a quote read past the cap: draft {status!r} after "
          f"{model.calls - calls} model call(s), expected needs_person and none")  # fmt: skip


def _unpriced(check: b.Checker, co: s2.Co, bw: s2.Co, model: aif.Model) -> None:
    free = aif.gateway(model, provider="scripted", name="free-unknown")
    db = aif.new_session(co)
    try:
        calls = model.calls
        status, got = _call(db, free, 900)
        check(status == "refused" and model.calls == calls, f"an unpriced model with a cap set: {status} after "
              f"{model.calls - calls} call(s), expected refused before the provider")  # fmt: skip
        check("price" in str(got).lower() or "budget" in str(got).lower(), f"the refusal says {str(got)[:150]!r}, "
              "expected that no price is known", "low")  # fmt: skip
    finally:
        db.close()
    db = aif.new_session(bw)
    try:
        status, got = _call(db, free, 901)
        check(status == "ok" and getattr(got, "cost_usd", None) == 0, f"an unpriced model without a cap: {status}, "
              f"cost {getattr(got, 'cost_usd', None)!r}, expected allowed at 0")  # fmt: skip
    finally:
        db.close()


def _card(check: b.Checker, co: s2.Co) -> None:
    month = datetime.now(UTC).strftime("%Y-%m")
    r = co.call(aif.admins(co)[0], "GET", f"/api/setup/ai-cost?month={month}")
    if not aif.check_status(check, r, 200, "GET /api/setup/ai-cost"):
        return
    body = js(r)
    spent = _month_spend(co)
    check(b.D(body.get("spent_usd")) == spent, f"the cost card says {body.get('spent_usd')!r}, model_call adds up to "
          f"{spent}")  # fmt: skip
    check(b.D(body.get("cap_usd")) == CAP, f"the cost card's cap {body.get('cap_usd')!r}, expected {CAP}")
    check(str(body.get("warn_pct")) == "80", f"the cost card's warning percent {body.get('warn_pct')!r}", "medium")
    by_cap = body.get("by_capability") or []
    total = sum((b.D(x.get("cost_usd")) for x in by_cap), Decimal(0))
    check(total == spent, f"the per-capability costs add up to {total}, the month to {spent}", "medium")
    check("free-unknown" in json.dumps(body.get("unpriced_models") or []), "the unpriced model is not listed",
          "medium")  # fmt: skip
    requester = next(c for c, held in sorted(co.roles.items()) if held == {"requester"})
    r = co.call(requester, "GET", f"/api/setup/ai-cost?month={month}")
    aif.check_status(check, r, 403, "a requester reading the cost card")
