"""Forward buying on the demo companies (task 13.5, spec scenarios FB0-FB8): the recommendation and its one live record,
the explanation (template, and the AI step with a scripted model), the buyer's and the approver's decisions, the request
the approval makes, revalidation, a rejected recommendation not offered again, and one company never reaching another's.
The expected figures are the generator's truth file (truth/truth_forward_buy.csv)."""

import csv
import json
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from stock_scenarios_support import DATASETS, Run, drop, scenarios
from test_ai_steps import Scripted

from p2p.ai_steps import forward_buy_note
from p2p.ai_steps.gateway import Gateway
from p2p.formulas.calendar import pin_today
from p2p.main import app
from p2p.platform.tokens import create_access_token
from p2p.requests import forward_buy
from p2p.sourcing import benchmarks

D = Decimal
client = TestClient(app)
ON = date(2026, 11, 23)
SENTENCE = (
    "Copper winding wire costs Rs 828.00 a kg on the market now (Pune wire bulletin 2026-11 (mid-month), verified by "
    "Sanjay Rao), 8.36% below its 12-month trend of Rs 903.50, which rises about Rs 2.41 a month. Buying 746 kg more "
    "with the January delivery covers February to April 2027 at about 248.71 kg a month. Saving Rs 63,511.45 before "
    "holding, Rs 18,529.09 to hold the stock at 1.5% a month: Rs 44,982.36 net. Risks: the price may fall further; "
    "Rs 6,86,320.00 is tied up until April 2027."
)


def _truth(company: str, scenario: str) -> list[dict[str, str]]:
    with (DATASETS[company] / "truth" / "truth_forward_buy.csv").open(encoding="utf-8", newline="") as f:
        return [r for r in csv.DictReader(f) if r["scenario"] == scenario]


@pytest.fixture
def made(control_db: str) -> Iterator[Any]:
    runs: list[Run] = []

    def build(name: str, scenario: str, *, full: bool = True) -> Run:
        r = Run(name)
        runs.append(r)
        for st in next(s for s in scenarios(name) if s["name"] == scenario)["steps"]:
            if st["do"] == "reset" or full:
                r.step(st)
        r.s.rollback()
        pin_today(ON)
        return r

    yield build
    for r in runs:
        drop(r)


def _headers(r: Run, code: str) -> dict[str, str]:
    uid = r.uid(code)
    role = r.s.execute(text("select role from user_profile where user_id = :u"), {"u": uid}).scalar()
    return {"Authorization": f"Bearer {create_access_token(uid, r.slug, str(role or 'Requester'))}"}


def _benchmark(r: Run, item: str, price: str, uom: str, region: str, on: str, kind: str, name: str) -> int:
    bid = benchmarks.add(
        r.s, {"item_code": item, "price": D(price), "uom": uom, "basis": "delivered", "effective_date": date.fromisoformat(on),
              "region_code": region, "source_kind": kind, "source_name": f"{name} (source)", "source_ref": name},
        r.uid("U03"), today=ON,
    )  # fmt: skip
    benchmarks.verify(r.s, bid, r.uid("U08"), "checked")
    r.s.commit()
    return bid


def _same_as_truth(rec: dict[str, Any], want: list[dict[str, str]]) -> None:
    head = want[0]
    for key in ("price_now", "trend_now", "slope", "dip_pct", "avg_use", "cover", "qty"):
        assert D(rec[key]) == D(head[key]), key
    for key, col in (("value", "value"), ("gross_saving", "gross_saving"), ("holding_cost", "holding_cost"), ("net_saving", "net_saving")):  # fmt: skip
        assert D(rec[key]) == D(head[col]), key
    assert rec["limit"] == head["limit"] and rec["rule"] == head["rule"]
    assert [(m["month"], D(m["expected_price"]), D(m["net_per_unit"]), D(m["qty"])) for m in rec["months"]] == [
        (w["month"], D(w["expected_price"]), D(w["net_per_unit"]), D(w["month_qty"])) for w in want
    ]


def test_copper_dips_the_buyer_accepts_and_the_purchase_manager_approves_the_extra(made: Any) -> None:
    r = made("apex", "A0")
    # FB0: nothing counts yet (September is stale, October unverified): no record, the evaluation says why
    (out,) = forward_buy.run(r.s, r.slug, ON, item="RM-08", location="PLT-CKN")
    assert (out.recommended, out.reason, out.outcome) == (False, "no_fresh_benchmark", "nothing")
    assert not r.s.execute(text("select 1 from forward_buy")).first()
    last = r.s.execute(text("select output from rule_evaluation where key = 'forward_buy' order by id desc")).scalar()
    assert last["reason"] == "no_fresh_benchmark"

    # FB1: the mid-month bulletin at Rs 828 makes a recommendation anchored on the MRP request
    _benchmark(r, "RM-08", "828.0000", "KGS", "MH", "2026-11-16", "index", "Pune wire bulletin 2026-11 (mid-month)")
    (out,) = forward_buy.run(r.s, r.slug, ON, item="RM-08", location="PLT-CKN")
    assert out.outcome == "created"
    buyer, pm, director = _headers(r, "U03"), _headers(r, "U08"), _headers(r, "U09")
    rec = client.get(f"/api/forward-buys/{out.id}", headers=buyer).json()
    _same_as_truth(rec, _truth("apex", "FB1"))
    assert rec["request_no"] == r.request_of("MRP-2027-01/PLT-CKN") and rec["status"] == "open"
    assert rec["explanation"] == SENTENCE and rec["explanation_source"] == "template"
    # the same figures again change nothing; a preview writes nothing
    assert forward_buy.run(r.s, r.slug, ON, item="RM-08", location="PLT-CKN")[0].outcome == "unchanged"
    rows = r.s.execute(text("select count(*) from forward_buy")).scalar_one()
    preview = client.get("/api/forward-buys/preview?item=RM-08&location=PLT-CKN", headers=buyer).json()
    assert (
        preview["reason"] in ("", "saving_too_small")
        and r.s.execute(text("select count(*) from forward_buy")).scalar_one() == rows
    )
    assert (
        client.get("/api/forward-buys/preview?item=RM-01&location=PLT-CKN", headers=buyer).json()["reason"]
        == "on_contract"
    )
    assert (
        client.get("/api/forward-buys/preview?item=PK-01&location=PLT-CKN", headers=buyer).json()["reason"]
        == "no_fresh_benchmark"
    )

    # FB6: the buyer accepts; the purchase manager (the rule's person) approves, nobody else
    assert client.post(f"/api/forward-buys/{out.id}/accept", json={"qty": "746"}, headers=buyer).status_code == 422
    assert (
        client.post(f"/api/forward-buys/{out.id}/accept", json={"qty": "747", "reason": "x"}, headers=buyer).status_code
        == 422
    )
    got = client.post(
        f"/api/forward-buys/{out.id}/accept", json={"qty": "746", "reason": "Copper dip; Q4 plan firm"}, headers=buyer
    )
    assert got.status_code == 200, got.text
    assert got.json()["approver"] == "Sanjay Rao" and got.json()["accepted_net_saving"] == "44982.36"
    pid = got.json()["proposal_id"]
    prop = r.s.execute(text("select value, required_permission from proposal where id = :i"), {"i": pid}).one()
    assert (D(prop[0]), prop[1]) == (D("686320.00"), "sourcing.route_approve")
    assert r.s.execute(text("select count(*) from task where kind = 'forward_buy'")).scalar_one() == 1
    assert client.post(f"/api/forward-buys/{out.id}/accept", json={"reason": "again"}, headers=buyer).status_code == 409
    assert client.post(f"/api/forward-buys/{out.id}/approve", json={}, headers=buyer).status_code == 403
    assert client.post(f"/api/forward-buys/{out.id}/approve", json={}, headers=director).status_code == 403
    done = client.post(f"/api/forward-buys/{out.id}/approve", json={"note": "fine"}, headers=pm)
    assert done.status_code == 200, done.text
    new_no = done.json()["request_no"]
    assert done.json()["status"] == "approved" and done.json()["request_status"] == "submitted"
    line = r.s.execute(
        text("select item_code, qty, uom, stock_qty, est_price, need_by from request_line where request_no = :n"),
        {"n": new_no},
    ).all()
    assert [(x[0], D(x[1]), x[2], D(x[3]), D(x[4]), x[5]) for x in line] == [
        ("RM-08", D(746), "KGS", D(746), D("920.00"), date(2027, 1, 10))
    ]
    req = r.s.execute(text("select source_ref, total_value from request where no = :n"), {"n": new_no}).one()
    assert (req[0], D(req[1])) == (f"FB-{out.id}", D("686320.00"))
    first = r.s.execute(
        text("select approver from approval_step where request_no = :n order by step limit 1"), {"n": new_no}
    ).scalar()
    assert first == "U09"
    check = client.get(f"/api/budgets/check/{new_no}", headers=_headers(r, "U05")).json()
    g = next(x for x in check["groups"] if x["gl_code"] == "1410" and x["level"] == "department")
    assert (g["committed"], g["requested"], g["used_pct"]) == ("640870.00", "686320.00", "11.44")
    page = client.get(f"/api/requisitions/{new_no}", headers=buyer).json()
    assert page["forward_buy_of"]["id"] == out.id
    actions = {a for (a,) in r.s.execute(text("select action from audit_event where entity = 'forward_buy'"))}
    assert {"forward_buy.create", "forward_buy.accept", "forward_buy.approve"} <= actions

    # while the forward-buy request is open nothing more is bought ahead for the wire (no ping-pong)
    (again,) = forward_buy.run(r.s, r.slug, ON, item="RM-08", location="PLT-CKN")
    assert (again.recommended, again.reason) == (False, "forward_buy_open")
    # once it is cancelled the dip is recommended again; the buyer rejects it
    r.s.execute(text("update request set status = 'cancelled' where no = :n"), {"n": new_no})
    r.s.commit()
    (again,) = forward_buy.run(r.s, r.slug, ON, item="RM-08", location="PLT-CKN")
    assert again.recommended
    if again.recommended:
        rej = client.post(f"/api/forward-buys/{again.id}/reject", json={"reason": "Enough for now"}, headers=buyer)
        assert rej.status_code == 200
        # not offered again until a newer benchmark counts
        assert forward_buy.run(r.s, r.slug, ON, item="RM-08", location="PLT-CKN")[0].outcome == "suppressed"
        _benchmark(r, "RM-08", "800.0000", "KGS", "MH", "2026-11-20", "index", "Pune wire bulletin 2026-11-20")
        (fresh,) = forward_buy.run(r.s, r.slug, ON, item="RM-08", location="PLT-CKN")
        assert fresh.outcome == "created"
        # a benchmark after the recommendation: accepting works the figures out again and refuses with the new one
        _benchmark(r, "RM-08", "790.0000", "KGS", "MH", "2026-11-21", "index", "Pune wire bulletin 2026-11-21")
        stale = client.post(f"/api/forward-buys/{fresh.id}/accept", json={"reason": "go"}, headers=buyer)
        assert stale.status_code == 409 and stale.json()["new_id"] != fresh.id
        assert (
            r.s.execute(text("select status from forward_buy where id = :i"), {"i": fresh.id}).scalar() == "superseded"
        )
    live = r.s.execute(text("select count(*) from forward_buy where status in ('open', 'accepted')")).scalar_one()
    assert live <= 1


def _gateway(*turns: str) -> Gateway:
    provider = Scripted({"m": list(turns)})
    return Gateway(
        {"fake": provider}, {"default": [("fake", "m")], "forward_buy_note": [("fake", "m")]}, sleep=lambda s: None
    )


def test_the_explanation_is_reworded_only_when_every_number_is_in_the_record(made: Any) -> None:
    r = made("apex", "A0")
    _benchmark(r, "RM-08", "828.0000", "KGS", "MH", "2026-11-16", "index", "Pune wire bulletin 2026-11 (mid-month)")
    (out,) = forward_buy.run(r.s, None, ON, item="RM-08", location="PLT-CKN")
    assert out.id is not None
    made_up = json.dumps({"summary": "Buy 900 kg more now to save Rs 50,000.", "risks": []})
    assert forward_buy_note.explain(r.s, out.id, _gateway(made_up)) == "template"
    rec = forward_buy.load(r.s, out.id)
    assert rec is not None and rec["explanation"] == SENTENCE and rec["explanation_source"] == "template"
    r.s.execute(text("delete from model_cache"))  # the refused answer is cached for the same prompt
    r.s.commit()
    good = json.dumps({"summary": "Copper is 8.36% below its trend; buying 746 kg more saves Rs 44,982.36 net.",
                       "risks": ["The price may fall further."]})  # fmt: skip
    assert forward_buy_note.explain(r.s, out.id, _gateway(good)) == "ai"
    rec = forward_buy.load(r.s, out.id)
    assert rec is not None and rec["explanation_source"] == "ai" and "746 kg" in rec["explanation"]
    assert D(rec["qty"]) == D(746)  # the model never changes a number


def test_brightwave_coffee_and_laptops_and_neither_company_sees_the_other(made: Any) -> None:
    bw = made("brightwave", "B0", full=False)
    h = _headers(bw, "U03")
    without = client.get("/api/forward-buys/preview?item=OS-03&location=OFF-BLR", headers=h).json()
    assert (without["reason"], without["dip_pct"]) == ("not_below_trend", _truth("brightwave", "FB7b")[0]["dip_pct"])
    _benchmark(
        bw,
        "OS-03",
        "790.0000",
        "KGS",
        "KA",
        "2026-11-16",
        "market_survey",
        "Plantation coffee wholesale rate (three traders asked)",
    )
    got = client.get("/api/forward-buys/preview?item=OS-03&location=OFF-BLR", headers=h).json()
    want = _truth("brightwave", "FB7")
    assert (got["reason"], got["qty"], got["net_saving"], got["limit"]) == (
        want[0]["reason"], want[0]["qty"], want[0]["net_saving"], want[0]["limit"],
    )  # fmt: skip
    assert (
        client.get("/api/forward-buys/preview?item=LP-01&location=OFF-BLR", headers=h).json()["reason"]
        == "not_eligible"
    )
    assert not bw.s.execute(text("select 1 from forward_buy")).first()
    # a record of Apex is not found from Brightwave, nor its budget months
    apex = made("apex", "A0", full=False)
    apex.s.execute(
        text(
            "insert into forward_buy (item_code, location_code, status, rule_label, months) "
            "values ('RM-08', 'PLT-CKN', 'open', 'forward_buy v1', '[]') returning id"
        )
    )
    apex.s.commit()
    fid = apex.s.execute(text("select max(id) from forward_buy")).scalar_one()
    assert client.get(f"/api/forward-buys/{fid}", headers=h).status_code == 404
    periods = client.get("/api/budgets/periods?fy=2026-27", headers=_headers(bw, "U05")).json()
    assert all(p["locked_by"] in (None, "U05") for p in periods["periods"])
