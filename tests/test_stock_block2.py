"""Block 2 boundaries beyond the scenario truth (test_stock_scenarios.py): held stock, safety stock and its override,
late-supply cases, the store check job and its events, replenishment's stock part, the planning capability with a
scripted model, and one company never reaching another's holds, cases or notes."""

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from stock_scenarios_support import Run, drop, scenarios
from test_ai_steps import Scripted

from p2p.agents import autonomy, planning
from p2p.ai_steps.gateway import Gateway
from p2p.formulas.calendar import business_today
from p2p.main import app
from p2p.orders import changes
from p2p.platform import permissions, proposals, setting_changes, tenants
from p2p.platform.sod import user
from p2p.platform.tokens import create_access_token
from p2p.receiving import quality, safety
from p2p.receiving.stock import SafetyStockError, StockError, post_count, post_issue
from p2p.requests import jobs, material, replenishment, supply_cases

D = Decimal
client = TestClient(app)


@pytest.fixture
def world(control_db: str) -> Iterator[Any]:
    made: list[Run] = []

    def build(scenario: str, name: str = "apex", upto: str | None = None) -> Run:
        sc = next(s for s in scenarios(name) if s["name"] == scenario)
        r = Run(name)
        made.append(r)
        for st in sc["steps"]:
            r.step(st)
            if upto and st["label"] == upto:
                break
        r.s.rollback()
        return r

    yield build
    for r in made:
        drop(r)


def _headers(r: Run, code: str) -> dict[str, str]:
    uid = r.uid(code)
    role = r.s.execute(text("select role from user_profile where user_id = :u"), {"u": uid}).scalar()
    return {"Authorization": f"Bearer {create_access_token(uid, r.slug, str(role or 'Store'))}"}


def _held(r: Run) -> dict[tuple[str, str, str], D]:
    r.s.rollback()
    return {
        (x[0], x[1], x[2]): D(x[3])
        for x in r.s.execute(text("select item_code, location_code, quality, qty from stock_quality_level"))
    }


# ---- 4.1 held stock --------------------------------------------------------------------------------------------


def test_quarantine_stock_is_never_set_aside_issued_or_counted(world: Any) -> None:
    r = world("A0")
    assert _held(r)[("RM-07", "PLT-CKN", "quarantine")] == D(20)
    reserved = D(
        r.s.execute(text("select sum(reserved_qty) from material_request_line where item_code = 'RM-07'")).scalar()
    )
    assert reserved == D(60)  # the usable 60, never the 20 in quarantine
    with pytest.raises(StockError):
        post_issue(r.s, r.uid("U02"), "RM-07", "PLT-CKN", D(1))
    assert post_count(r.s, r.uid("U02"), "RM-07", "PLT-CKN", D(60), "count") is None  # the count is of usable stock
    res = client.post(
        "/api/inventory/issue",
        json={"item_code": "RM-06", "location": "PLT-CKN", "qty": "1"},
        headers=_headers(r, "U02"),
    )
    assert res.status_code == 409  # the 8 blocked bearings are not on the shelf for use


def test_holding_reserved_stock_gives_back_the_latest_need_first(world: Any) -> None:
    r = world("A0")
    h = _headers(r, "U02")
    bad = {"item_code": "RM-10", "location": "PLT-CKN", "qty": "10", "quality": "blocked", "reason": "crushed"}
    assert client.post("/api/inventory/holds", json={**bad, "quality": "usable"}, headers=h).status_code == 422
    assert client.post("/api/inventory/holds", json={**bad, "qty": "0"}, headers=h).status_code == 422
    assert client.post("/api/inventory/holds", json={**bad, "reason": " "}, headers=h).status_code == 422
    assert client.post("/api/inventory/holds", json={**bad, "item_code": "XX"}, headers=h).status_code == 404
    assert client.post("/api/inventory/holds", json={**bad, "qty": "201"}, headers=h).status_code == 409
    assert client.post("/api/inventory/holds", json=bad, headers=_headers(r, "U03")).status_code == 403
    res = client.post("/api/inventory/holds", json=bad, headers=h)
    assert res.status_code == 201, res.text
    mrp = r.mr_of("MRP-2027-01/PLT-CKN")
    assert res.json()["released"] == {mrp: {"RM-10": "10"}} and res.json()["store_check"] is True
    held = client.get("/api/inventory/holds?item_code=RM-10", headers=h).json()["holds"]
    assert [(x["quality"], x["left_qty"], x["source"]) for x in held] == [("blocked", "10", "manual")]


def test_releasing_a_hold_sets_stock_aside_at_once_and_errors_say_why(world: Any) -> None:
    r = world("A0")
    h = _headers(r, "U02")
    hold_id = r.s.execute(text("select id from stock_hold where import_id = 'H-APX-01'")).scalar_one()
    url = f"/api/inventory/holds/{hold_id}/decide"
    assert client.post(url, json={"to_usable": "14", "reason": ""}, headers=h).status_code == 422
    assert client.post(url, json={"to_usable": "0", "to_blocked": "0", "reason": "x"}, headers=h).status_code == 422
    assert client.post(url, json={"to_usable": "21", "reason": "x"}, headers=h).status_code == 409
    assert (
        client.post("/api/inventory/holds/999999/decide", json={"to_usable": "1", "reason": "x"}, headers=h).status_code
        == 404
    )
    res = client.post(url, json={"to_usable": "14", "to_blocked": "6", "reason": "seal face check"}, headers=h)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["left_qty"] == "0" and body["reserved_now"] == {r.mr_of("MO-2"): {"RM-07": "14"}}
    assert client.post(url, json={"to_usable": "1", "reason": "x"}, headers=h).status_code == 409  # closed
    blocked = body["blocked_hold_id"]
    again = f"/api/inventory/holds/{blocked}/decide"
    assert client.post(again, json={"to_blocked": "1", "reason": "x"}, headers=h).status_code == 422


def test_hold_ledger_matches_movements(world: Any) -> None:
    r = world("A0")
    quality.hold(r.s, r.slug, r.uid("U02"), "RM-10", "PLT-CKN", D(10), "quarantine", "suspect lot")
    hid = r.s.execute(text("select id from stock_hold where import_id = 'H-APX-01'")).scalar_one()
    quality.decide(r.s, r.slug, r.uid("U02"), int(hid), D(5), D(3), "part passed")
    r.s.rollback()
    ledger: dict[tuple[str, str, str], D] = {}
    for i, loc, q, left in r.s.execute(
        text(
            "select item_code, location_code, quality, sum(left_qty) from stock_hold where closed_at is null group by 1, 2, 3"
        )
    ):
        ledger[(i, loc, q)] = D(left)
    held = {k: v for k, v in _held(r).items() if k[2] != "usable" and v != 0}
    assert ledger == held


# ---- 6.2 safety stock ------------------------------------------------------------------------------------------


def test_issue_below_safety_needs_the_managers_override(world: Any) -> None:
    r = world("A0")
    h = _headers(r, "U02")
    res = client.post("/api/inventory/issue", json={"item_code": "SP-01", "location": "PLT-CKN", "qty": "3"}, headers=h)
    assert res.status_code == 409
    assert {k: res.json()[k] for k in ("needs_override", "available", "safety_stock")} == {
        "needs_override": True, "available": "2", "safety_stock": "2",
    }  # fmt: skip
    ask = {"kind": "issue", "item_code": "SP-01", "location": "PLT-CKN", "qty": "3", "reason": "lathe 2 down"}
    assert client.post("/api/inventory/safety-overrides", json={**ask, "qty": "2"}, headers=h).status_code == 409
    assert client.post("/api/inventory/safety-overrides", json={**ask, "qty": "5"}, headers=h).status_code == 422
    assert client.post("/api/inventory/safety-overrides", json={**ask, "reason": ""}, headers=h).status_code == 422
    got = client.post("/api/inventory/safety-overrides", json=ask, headers=h)
    assert got.status_code == 202 and got.json()["approver_hint"] == "Vikram Joshi"
    pid = got.json()["proposal_id"]
    for who in ("U02", "U03", "U04"):  # the asker, the buyer, AP
        with pytest.raises(permissions.PermissionDenied):
            proposals.approve(r.s, pid, r.uid(who))
    proposals.approve(r.s, pid, r.uid("U07"))
    r.s.rollback()
    assert r.positions([("SP-01", "PLT-CKN")])[("SP-01", "PLT-CKN")]["usable"] == "1"
    audit = r.s.execute(
        text("select after from audit_event where action = 'stock.issue' order by id desc limit 1")
    ).scalar()
    assert audit["from_safety"] == "1.0000" or D(audit["from_safety"]) == D(1)


def test_safety_change_waits_for_the_second_administrator(world: Any) -> None:
    r = world("A0", upto="reset")
    made = setting_changes.submit(
        r.s, user(r.uid("U06")), "item_planning", {"item_code": "RM-03", "location": "PLT-CKN", "safety_stock": "20"},
        "rework", tenant=None,
    )  # fmt: skip
    r.s.rollback()
    assert r.s.execute(text("select safety_stock from item_location where item_code = 'RM-03'")).scalar() is None
    with pytest.raises(permissions.PermissionDenied):
        proposals.approve(r.s, made["proposal_id"], r.uid("U06"))  # the maker never approves
    res = client.post(
        "/api/setup/item-planning",
        json={"item_code": "RM-03", "location": "PLT-CKN", "max_qty": "5", "reorder_point": "10", "reason": "x"},
        headers=_headers(r, "U10"),
    )
    assert res.status_code == 422
    proposals.approve(r.s, made["proposal_id"], r.uid("U10"))
    r.s.rollback()
    assert D(r.s.execute(text("select safety_stock from item_location where item_code = 'RM-03'")).scalar()) == D(20)


# ---- 6.1 late supply --------------------------------------------------------------------------------------------


def test_one_late_case_per_po_and_request_closed_only_by_who_may(world: Any) -> None:
    r = world("T1-late")
    supply_cases.evaluate(r.s, r.slug)
    material.store_check(r.s, user(r.uid("U02")), tenant=r.slug)
    r.s.rollback()
    cases = r.s.execute(text("select task_id, po_no from supply_risk")).all()
    assert len(cases) == 1
    task_id = cases[0][0]
    rows = client.get("/api/exceptions?kind=late_supply", headers=_headers(r, "U03")).json()["rows"]
    assert [x["ref"] for x in rows] == [cases[0][1]] and rows[0]["kind_title"] == "Supply promised too late"
    assert rows[0]["amount"] == "32500.00" and rows[0]["mine"] is True
    url = f"/api/supply-risks/{task_id}/close"
    assert client.post(url, json={"outcome": "expedited"}, headers=_headers(r, "U04")).status_code == 403
    assert client.post(url, json={"outcome": "resolved"}, headers=_headers(r, "U03")).status_code == 422
    assert client.post(url, json={"outcome": "bought_elsewhere"}, headers=_headers(r, "U03")).status_code == 422
    assert client.post(url, json={"outcome": "later"}, headers=_headers(r, "U03")).status_code == 422
    assert client.post(url, json={"outcome": "expedited"}, headers=_headers(r, "U03")).json()["status"] == "done"
    assert client.post(url, json={"outcome": "expedited"}, headers=_headers(r, "U03")).status_code == 409
    listed = client.get("/api/supply-risks?status=closed", headers=_headers(r, "U02")).json()["cases"]
    assert [c["outcome"] for c in listed] == ["expedited"]
    assert client.get("/api/supply-risks?status=x", headers=_headers(r, "U02")).status_code == 422


def test_the_material_requests_screen_shows_the_late_promise(world: Any) -> None:
    r = world("T1-late")
    data = client.get("/api/material-requests", headers=_headers(r, "U02")).json()
    mo1 = next(x for x in data["requests"] if x["order_no"] == "MO-1")
    line = next(x for x in mo1["lines"] if x["item_code"] == "RM-01")
    assert mo1["at_risk"] is True
    assert (line["timely_qty"], line["late_qty"], line["at_risk_qty"], line["latest_arrival"]) == (
        "0", "50", "50", "2026-12-05",
    )  # fmt: skip
    assert [(s["doc_type"], s["qty"], s["date"], s["date_kind"], s["timely"]) for s in line["supply"]] == [
        ("po", "50", "2026-12-08", "promised", False),
    ]  # fmt: skip
    assert line["causes"] == ["late_supply", "no_stock"]


# ---- 6.5 the job and the events -----------------------------------------------------------------------------------


def test_store_check_job_runs_and_queues_the_next_and_a_second_run_changes_nothing(world: Any) -> None:
    r = world("A0")
    jobs.schedule_check(r.slug)
    with tenants.control_engine().begin() as c:
        queued = c.execute(
            text("select count(*) from job where tenant_slug = :t and kind = 'store_check' and status = 'queued'"),
            {"t": r.slug},
        ).scalar_one()
    assert queued == 1
    count = (
        "select count(*) from audit_event where action in ('material_request.reserve', 'request.create', 'task.create')"
    )
    before = r.s.execute(text(count)).scalar_one()
    assert jobs.run_check(r.s, r.slug) is not None
    assert jobs.run_check(r.s, r.slug) is not None
    r.s.rollback()
    assert r.s.execute(text(count)).scalar_one() == before
    runs = r.s.execute(text("select count(*) from audit_event where action = 'store_check.run'")).scalar_one()
    assert runs == 2
    r.s.execute(text("update app_setting set value = 'false' where key = 'auto_store_check'"))
    r.s.commit()
    assert jobs.run_check(r.s, r.slug) is None
    assert (
        jobs.next_run(datetime(2026, 11, 23, 3, 0, tzinfo=UTC), jobs.CHECK_AT).isoformat()
        == "2026-11-24T00:00:00+00:00"
    )


def test_po_cancel_reraises_the_shortfall_as_a_draft(world: Any) -> None:
    r = world("T1-late")
    po_no, _ = r.po_of("RM-01")
    before = set(r.requests())
    changes.cancel(r.s, r.slug, po_no, r.uid("U03"), "supplier cannot make it")
    made = {no: x for no, x in r.requests().items() if no not in before}
    assert [(x["order_no"], x["lines"], x["status"]) for x in made.values()] == [("MO-1", ["RM-01:50"], "draft")]
    r.s.rollback()
    assert r.s.execute(text("select outcome from supply_risk")).scalar() == "resolved"


def test_rejected_shortfall_is_not_reraised_automatically(world: Any) -> None:
    r = world("E2-rejected", upto="after_job")
    data = client.get("/api/material-requests", headers=_headers(r, "U02")).json()
    mo2 = next(x for x in data["requests"] if x["order_no"] == "MO-2")
    rejected = r.s.execute(text("select no from request where status = 'rejected'")).scalar_one()
    assert {ln["last_rejected"] for ln in mo2["lines"] if ln["requests"]} == {rejected}
    assert {ln["last_rejected"] for ln in mo2["lines"] if not ln["requests"]} <= {None}


# ---- 6.7 replenishment ------------------------------------------------------------------------------------------


def test_restock_po_still_blocks_a_second_restock(world: Any) -> None:
    r = world("A0")
    replenishment.run(r.s, as_of=business_today(), tenant=r.slug, submit=False)
    open_ = replenishment._open_requests(r.s)  # noqa: SLF001
    assert ("SP-01", "PLT-CKN") in open_  # the restock request counts as on order for stock
    second = replenishment.plan(r.s, business_today())[0]
    assert all(ln.item_code != "SP-01" for req in second for ln in req.lines)


# ---- 11b.1 the planning capability ---------------------------------------------------------------------------------


def _gateway(*turns: str) -> tuple[Gateway, Scripted]:
    provider = Scripted({"m": list(turns)})
    gw = Gateway({"fake": provider}, {"agent": [("fake", "m")], "default": [("fake", "m")]}, sleep=lambda s: None)
    return gw, provider


def _turn(**kw: Any) -> str:
    return json.dumps({"confidence": 0.9, **kw})


def _switch_on(r: Run) -> None:
    autonomy.configure(r.s, user(r.uid("U06")), planning.DECISION, level="auto")


def _faithful(r: Run, mr: str, *, qty: str = "50", causes: list[str] | None = None) -> list[str]:
    line = r.s.execute(
        text("select line_no from material_request_line where mr_no = :m and item_code = 'RM-01'"), {"m": mr}
    ).scalar_one()
    po_no, _ = r.po_of("RM-01")
    answer = {
        "summary": f"{po_no} brings {qty} motor bodies on 8 Dec, after MO-1 needs them on 5 Dec.",
        "lines": [{"line_no": line, "causes": causes or ["late_supply", "no_stock"],
                   "text": "No usable stock at the plant; ask the supplier to deliver by 5 Dec or buy elsewhere."}],
    }  # fmt: skip
    return [
        _turn(tool="material_request", args={"mr_no": mr}),
        _turn(tool="supply_for_line", args={"mr_no": mr, "line_no": line}),
        _turn(final=json.dumps(answer)),
    ]


def test_planning_tools_only_read(world: Any) -> None:
    r = world("T1-late")
    registry = planning.build_registry(r.s)
    assert registry.names() == sorted(planning.TOOLS)
    assert not any(registry.get(n).writes for n in registry.names())  # type: ignore[union-attr]
    tables = ["stock_movement", "stock_hold", "material_request_line", "request_line", "po_line", "supply_risk", "task"]
    before = {t: r.s.execute(text(f"select count(*) from {t}")).scalar_one() for t in tables}  # noqa: S608
    mr = r.mr_of("MO-1")
    for name, args in (("material_request", {"mr_no": mr}), ("supply_for_line", {"mr_no": mr, "line_no": 1}),
                       ("stock_position", {"item_code": "RM-07", "location": "PLT-CKN"}),
                       ("set_aside_elsewhere", {"item_code": "RM-03", "location": "PLT-CKN"}),
                       ("demand_freshness", {}), ("open_cases", {"mr_no": mr})):  # fmt: skip
        tool = registry.get(name)
        assert tool is not None
        tool.fn(r.s, tool.params.model_validate(args), user(r.uid("U02")))
    r.s.rollback()
    assert {t: r.s.execute(text(f"select count(*) from {t}")).scalar_one() for t in tables} == before  # noqa: S608


def test_explanation_off_makes_no_model_call(world: Any) -> None:
    r = world("T1-late")
    gw, provider = _gateway()
    got = planning.explain(r.s, r.slug, r.uid("U02"), r.mr_of("MO-1"), gw)
    assert got.status == "off" and provider.calls == [] and got.facts


def test_a_faithful_explanation_is_accepted_and_the_same_snapshot_is_not_explained_twice(world: Any) -> None:
    r = world("T1-late")
    _switch_on(r)
    mr = r.mr_of("MO-1")
    gw, provider = _gateway(*_faithful(r, mr))
    got = planning.explain(r.s, r.slug, r.uid("U02"), mr, gw)
    assert got.status == "accepted", got.reasons
    calls = len(provider.calls)
    again = planning.explain(r.s, r.slug, r.uid("U02"), mr, gw)
    assert again.note_id == got.note_id and len(provider.calls) == calls
    listed = client.get("/api/material-requests", headers=_headers(r, "U02")).json()["requests"]
    assert next(x for x in listed if x["no"] == mr)["explanation"]["status"] == "accepted"


def test_explanation_with_an_invented_number_falls_back_to_facts(world: Any) -> None:
    r = world("T1-late")
    _switch_on(r)
    mr = r.mr_of("MO-1")
    gw, _ = _gateway(*_faithful(r, mr, qty="55"))
    got = planning.explain(r.s, r.slug, r.uid("U02"), mr, gw)
    assert got.status == "facts_only" and any("number 55" in x for x in got.reasons), got.reasons


def test_explanation_with_wrong_causes_is_refused(world: Any) -> None:
    r = world("T1-late")
    _switch_on(r)
    mr = r.mr_of("MO-1")
    gw, _ = _gateway(*_faithful(r, mr, causes=["short"]))
    got = planning.explain(r.s, r.slug, r.uid("U02"), mr, gw)
    assert got.status == "facts_only" and any("causes" in x for x in got.reasons)


def test_unknown_documents_and_dates_are_not_trusted() -> None:
    tool_text = json.dumps({"order": "MO-1", "need_by": "2026-12-05 (5 Dec 2026)", "need": "50"})
    assert planning._ungrounded("MO-1 needs 50 on 5 Dec", tool_text) == []  # noqa: SLF001
    assert planning._ungrounded("PO-CKN-2627-00009 brings it", tool_text)  # noqa: SLF001
    assert planning._ungrounded("needs 50 on 6 Dec", tool_text)  # noqa: SLF001
    assert planning._ungrounded("needs 3 of them", tool_text)  # noqa: SLF001 - no small-number exemption


def test_requester_cannot_ask_for_an_explanation_and_another_company_cannot_see_it(world: Any) -> None:
    r = world("T1-late")
    mr = r.mr_of("MO-1")
    assert client.post(f"/api/material-requests/{mr}/explain", headers=_headers(r, "U01")).status_code == 403
    assert client.post(f"/api/material-requests/{mr}/explain", headers=_headers(r, "U02")).json()["status"] == "off"
    b = world("B0", name="brightwave")
    h = _headers(b, "U02")
    assert client.post(f"/api/material-requests/{mr}/explain", headers=h).status_code == 404
    case_id = r.s.execute(text("select task_id from supply_risk limit 1")).scalar_one()
    assert all(
        x["item_code"] not in ("RM-06", "RM-07") for x in client.get("/api/inventory/holds", headers=h).json()["holds"]
    )
    assert client.get("/api/supply-risks", headers=h).json()["cases"] == []
    assert (
        client.post(
            f"/api/supply-risks/{case_id}/close", json={"outcome": "expedited"}, headers=_headers(b, "U03")
        ).status_code
        == 404
    )


def test_switching_off_drops_queued_explanations(world: Any) -> None:
    r = world("T1-late")
    _switch_on(r)
    assert planning.queue_changed(r.s, r.slug, [r.mr_of("MO-1")]) == [r.mr_of("MO-1")]
    assert planning.queue_changed(r.s, r.slug, [r.mr_of("MO-1")]) == []  # one job per request
    assert planning.drop_queued(r.slug) == 1
    assert safety.ACTION == "stock.safety_override"
    assert isinstance(SafetyStockError("x", D(0), D(0)), StockError)


def test_earlier_erp_date_raises_the_late_supply_case_not_a_demand_case(world: Any, tmp_path: Any) -> None:
    from p2p.requests import demand

    r = world("T1b-on-time")
    assert r.s.execute(text("select count(*) from supply_risk")).scalar_one() == 0
    (tmp_path / "production_order.csv").write_text(
        "order_no,product_code,qty,need_by,reason,location_code,status\nMO-1,FG-01,50,2026-12-03,SO-1001,PLT-CKN,\n",
        encoding="utf-8",
    )
    report = demand.import_demand(r.s, tmp_path, actor=user(r.uid("U02")), tenant=r.slug)
    assert report.rejected == [] and ["earlier"] == report.changes[0].what
    r.s.rollback()
    assert r.s.execute(text("select count(*) from demand_change_case")).scalar_one() == 0
    lines = r.s.execute(text("select lines from supply_risk where outcome is null")).scalar_one()
    assert [(x["item_code"], x["at_risk"], x["latest_arrival"]) for x in lines] == [("RM-01", "50", "2026-12-03")]


def test_a_call_to_a_tool_outside_the_six_is_not_trusted(world: Any) -> None:
    r = world("T1-late")
    _switch_on(r)
    mr = r.mr_of("MO-1")
    gw, _ = _gateway(_turn(tool="release_hold", args={"hold_id": 1, "to_usable": "1"}), *_faithful(r, mr))
    got = planning.explain(r.s, r.slug, r.uid("U02"), mr, gw)
    assert got.status == "facts_only" and any("release_hold" in x for x in got.reasons), got.reasons
