"""Changed and cancelled ERP demand (rule Q21) and MRP results as material requests, on the two demo companies.

Each scenario starts from a fresh demo reset and one store check, runs its set-up, imports its files from the dataset's
changes/<scenario>/ folder and is checked against truth/truth_demand_change.csv, which the generator works out from the
raw files on its own. Then the boundary cases: a second import changes nothing, a dry run writes nothing, refused rows
say why, cases are closed only by who may, and one company never sees another's changes."""

import csv
import uuid
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.approvals import workflow
from p2p.formulas.calendar import business_today
from p2p.main import app
from p2p.platform import demo, notifications, reference, routing, tenants, users
from p2p.platform.sod import user
from p2p.platform.tokens import create_access_token
from p2p.receiving.stock import post_count
from p2p.requests import demand, material
from p2p.sourcing import service as sourcing

ROOT = Path(__file__).resolve().parents[1]
DATASETS = {
    "apex": ROOT / "data" / "datasets" / "apex-v1",
    "brightwave": ROOT / "data" / "datasets" / "brightwave-v1",
}
DOMAIN = {"apex": "apexpumps.example", "brightwave": "brightwave.example"}
STORE = {"apex": "store", "brightwave": "facilities"}  # who holds material.manage
D = Decimal
client = TestClient(app)


@dataclass
class World:
    name: str
    slug: str
    db: Session

    @property
    def folder(self) -> Path:
        return DATASETS[self.name]

    def uid(self, login: str) -> int:
        login = STORE[self.name] if login == "store" else login
        found = users.user_id_by_email(self.slug, f"{login}@{DOMAIN[self.name]}")
        assert found is not None, login
        return found

    def headers(self, login: str) -> dict[str, str]:
        role = self.db.execute(
            text("select role from user_profile where user_id = :u"),
            {"u": self.uid(login)},
        ).scalar()
        return {"Authorization": f"Bearer {create_access_token(self.uid(login), self.slug, str(role or 'Store'))}"}

    def changes(self, scenario: str) -> Path:
        return self.folder / "changes" / scenario


def _make(name: str) -> World:
    slug = "dc" + uuid.uuid4().hex[:8]
    demo.reset_company(demo.company(name), DATASETS[name], credentials=None, slug=slug)
    return World(name, slug, Session(routing.engine_for(slug)))


@pytest.fixture
def make(control_db: str) -> Iterator[object]:
    made: list[World] = []

    def build(name: str = "apex") -> World:
        made.append(_make(name))
        material.store_check(made[-1].db, user(made[-1].uid("store")), tenant=made[-1].slug)
        return made[-1]

    yield build
    for w in made:
        w.db.close()
    routing.clear_engine_cache()
    for w in made:
        tenants.drop_tenant(w.slug)


# ---- reading the state the way the truth file counts it ---------------------------------------------


def _truth(name: str, scenario: str) -> list[dict[str, str]]:
    with (DATASETS[name] / "truth" / "truth_demand_change.csv").open(encoding="utf-8", newline="") as f:
        return [r for r in csv.DictReader(f) if r["scenario"] == scenario]


def _lines(w: World) -> dict[tuple[str, str], list[dict[str, object]]]:
    """(order, item) -> its material request lines, oldest request first, with set aside and open cover."""
    w.db.rollback()
    out: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
    for r in w.db.execute(
        text(
            "select m.no, m.order_no, l.id, l.item_code, l.need_qty, l.reserved_qty, l.issued_qty, l.status, l.need_by, "
            "(select coalesce(sum(b.qty), 0) from material_request_buy b join request_line rl on rl.request_no = b.request_no "
            " and rl.line_no = b.request_line_no join request q on q.no = b.request_no where b.mr_line_id = l.id "
            " and rl.status = 'open' and q.status in ('draft', 'submitted', 'approved', 'returned')) as open_cover "
            "from material_request_line l join material_request m on m.no = l.mr_no order by m.created_at, m.no"
        )
    ).mappings():
        out[(r["order_no"], r["item_code"])].append(dict(r))
    return out


def _cases(w: World) -> dict[tuple[str, str, str], Decimal]:
    """(owner kind, material request, item) -> the excess on the cases."""
    out: dict[tuple[str, str, str], Decimal] = defaultdict(Decimal)
    for kind, lines in w.db.execute(text("select owner_kind, lines from demand_change_case")):
        for ln in lines:
            out[(kind, ln["mr_no"], ln["item_code"])] += D(ln["qty"])
    return out


def _bought_after(w: World, requests_before: set[str]) -> dict[tuple[str, str], Decimal]:
    out: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    for mr_no, item, qty, req in w.db.execute(
        text(
            "select l.mr_no, l.item_code, b.qty, b.request_no from material_request_buy b "
            "join material_request_line l on l.id = b.mr_line_id"
        )
    ):
        if req not in requests_before:
            out[(mr_no, item)] += D(qty)
    return out


def _requests(w: World) -> set[str]:
    return {r[0] for r in w.db.execute(text("select no from request"))}


def _check_truth(w: World, scenario: str, requests_before: set[str]) -> int:
    lines, cases, bought = _lines(w), _cases(w), _bought_after(w, requests_before)
    rows = _truth(w.name, scenario)
    assert rows, scenario
    for t in rows:
        found = lines[(t["mr_order_no"], t["item_code"])]
        ln = found[-1] if D(t["need_before"]) == 0 else found[0]  # a new request for the same order is the newest
        need = D(0) if ln["status"] == "cancelled" else D(ln["need_qty"])
        got = (need, D(ln["reserved_qty"]), D(ln["open_cover"]))
        want = (D(t["need_after"]), D(t["reserved_after"]), D(t["open_request_after"]))
        assert got == want, (t, got)
        key = (str(ln["no"]), t["item_code"])
        assert cases.get(("buyer", *key), D(0)) == D(t["buyer_case_qty"]), t
        assert cases.get(("store", *key), D(0)) == D(t["store_case_qty"]), t
        assert bought.get(key, D(0)) == D(t["new_request_qty"]), t
    return len(rows)


def _approve_and_source(w: World, order: str) -> None:
    """The order's purchase request approved through its chain, then sourced: the buyer has every line."""
    no = w.db.execute(
        text("select r.no from request r join material_request m on m.no = r.source_ref where m.order_no = :o"),
        {"o": order},
    ).scalar_one()
    for _ in range(5):
        w.db.rollback()
        step = w.db.execute(
            text(
                "select coalesce(s.delegate, s.approver) from approval_step s join request r on r.no = s.request_no "
                "where s.request_no = :n and s.status = 'pending' and s.round = r.approval_round"
            ),
            {"n": no},
        ).scalar()
        if step is None:
            break
        login = notifications.login_of(w.db, w.slug, step)
        assert login is not None
        workflow.approve(w.db, w.slug, no, login, "approved for the scenario")
    assert w.db.execute(text("select status from request where no = :n"), {"n": no}).scalar() == "approved"
    sourcing.source_request(w.db, w.slug, no, as_of=business_today(), dispatch=False)
    w.db.rollback()
    open_lines = w.db.execute(
        text("select count(*) from request_line where request_no = :n and status = 'open'"),
        {"n": no},
    ).scalar()
    assert open_lines == 0, "every line must be with the buyer"


def _import(w: World, scenario: str, **kw: object) -> demand.DemandReport:
    return demand.import_demand(w.db, w.changes(scenario), actor=user(w.uid("store")), tenant=w.slug, **kw)  # type: ignore[arg-type]


# ---- 6.4: MRP results -------------------------------------------------------------------------------


def test_the_mrp_plan_gets_its_own_material_request_and_the_base_check_matches_the_plan(
    make,
) -> None:  # type: ignore[no-untyped-def]
    # given Apex after reset and one store check
    w = make("apex")
    heads = dict(w.db.execute(text("select order_no, product_code from material_request")).all())
    assert heads == {"MO-1": "FG-01", "MO-2": "FG-02", "MRP-2027-01/PLT-CKN": None}
    need_by = w.db.execute(text("select need_by from material_request where source_kind = 'mrp'")).scalar_one()
    assert need_by == date(2027, 1, 10)  # the ERP's date, a Sunday, kept as given
    # then the three purchase requests of the plan, at the demo prices
    values = dict(
        w.db.execute(
            text("select m.order_no, r.total_value from request r join material_request m on m.no = r.source_ref")
        ).all()
    )
    assert values == {
        "MO-1": D("72930.00"),
        "MO-2": D("209700.00"),
        "MRP-2027-01/PLT-CKN": D("369490.00"),
    }
    lines = _lines(w)
    assert (
        D(lines[("MRP-2027-01/PLT-CKN", "RM-10")][0]["reserved_qty"]),
        lines[("MRP-2027-01/PLT-CKN", "RM-10")][0]["open_cover"],
    ) == (D(50), D(70))
    # the same MRP file again loads nothing
    again = demand.import_demand(w.db, w.folder, tenant=w.slug, kind="mrp_result")
    assert (again.rows_loaded, again.rows_changed, again.already_loaded) == (0, 0, 10)


def test_mrp_split_cuts_chakan_and_net_sanand_lines_buy_in_full(make) -> None:  # type: ignore[no-untyped-def]
    w = make("apex")
    before = _requests(w)
    report = _import(w, "M2-mrp-split")
    assert report.rejected == [] and report.rows_changed == 10 and report.rows_loaded == 10
    snd = w.db.execute(text("select no from material_request where order_no = 'MRP-2027-01/PLT-SND'")).scalar_one()
    assert snd in report.material_requests
    # Sanand's store opens and counts 50 water seals; the store check sets nothing aside for the net lines
    w.db.execute(text("update location set is_stock_location = true where code = 'PLT-SND'"))
    w.db.commit()
    post_count(w.db, w.uid("store"), "RM-10", "PLT-SND", D(50), "Sanand's first count")
    material.store_check(w.db, user(w.uid("store")), tenant=w.slug)
    assert _check_truth(w, "M2-mrp-split", before) == 20
    audit = w.db.execute(
        text("select after from audit_event where action = 'material_request.create' and entity_id = :n"),
        {"n": snd},
    ).scalar_one()
    assert all(line.endswith(" net") for line in audit["lines"])


# ---- 6.3: Apex ---------------------------------------------------------------------------------------


def test_a1_a_cut_takes_open_requests_first_and_passes_released_stock_on(make) -> None:  # type: ignore[no-untyped-def]
    w = make("apex")
    before = _requests(w)
    report = _import(w, "A1-cut")
    assert report.rejected == [] and report.warnings == []
    assert {c.what[0] for c in report.changes} == {
        "header_qty",
        "qty_down",
    } and report.rows_changed == 11
    assert _check_truth(w, "A1-cut", before) == 12
    values = dict(
        w.db.execute(
            text("select m.order_no, r.total_value from request r join material_request m on m.no = r.source_ref")
        ).all()
    )
    assert (values["MO-2"], values["MRP-2027-01/PLT-CKN"]) == (
        D("124380.00"),
        D("364990.00"),
    )
    assert w.db.execute(text("select count(*) from task where kind = 'demand_change'")).scalar() == 0
    rm10 = next(c for c in report.changes if c.item_code == "RM-10")
    assert rm10.released == {"RM-10": D(40)}
    # the same files again change nothing and write no audit row
    audits = w.db.execute(text("select count(*) from audit_event")).scalar()
    again = _import(w, "A1-cut")
    assert (again.rows_changed, again.rows_loaded, again.already_loaded) == (0, 0, 11)
    assert w.db.execute(text("select count(*) from audit_event")).scalar() == audits + 1  # demand.import only


def test_a2_cancel_with_the_buyer_raises_one_case_per_document(make) -> None:  # type: ignore[no-untyped-def]
    w = make("apex")
    _approve_and_source(w, "MO-1")
    before = _requests(w)
    report = _import(w, "A2-cancel")
    assert report.rejected == [] and report.rows_changed == 1
    assert _check_truth(w, "A2-cancel", before) == 19
    assert w.db.execute(text("select status from material_request where order_no = 'MO-1'")).scalar() == "cancelled"
    change = report.changes[0]
    docs = [(c["doc_type"], c["doc_no"]) for c in change.cases]
    assert len(docs) == len(set(docs)) and all(c["owner_kind"] == "buyer" for c in change.cases)
    buyer, manager = w.uid("buyer"), w.uid("purchasemanager")
    for task in w.db.execute(
        text("select assignee, escalation_chain, sla_hours, created_at, due_at from task where kind = 'demand_change'")
    ):
        assert task.assignee == buyer and task.escalation_chain == [manager]
        cal = reference.calendar_for_location(w.db, "PLT-CKN")
        assert task.due_at == cal.add_business_hours(task.created_at, D(2) * cal.hours_per_day)
    rows = client.get("/api/exceptions?kind=demand", headers=w.headers("buyer")).json()["rows"]
    assert {r["ref"] for r in rows} == {d for _, d in docs} and all(
        r["mine"] and r["owner"].startswith("Buyer: ") for r in rows
    )
    assert all(r["amount"] is not None and D(r["amount"]) > 0 for r in rows)
    # closing: refused to AP, unknown outcome and missing note refused, done by the buyer, then closed for good
    task_id = change.cases[0]["task_id"]
    url = f"/api/demand/cases/{task_id}/close"
    assert client.post(url, json={"outcome": "reduced"}, headers=w.headers("ap")).status_code == 403
    assert client.post(url, json={"outcome": "returned_to_stock"}, headers=w.headers("buyer")).status_code == 422
    assert client.post(url, json={"outcome": "kept_for_stock"}, headers=w.headers("buyer")).status_code == 422
    done = client.post(
        url,
        json={"outcome": "reduced", "note": "PO revised"},
        headers=w.headers("buyer"),
    )
    assert done.status_code == 200 and done.json() == {
        "task_id": task_id,
        "status": "done",
        "outcome": "reduced",
    }
    assert client.post(url, json={"outcome": "reduced"}, headers=w.headers("buyer")).status_code == 409
    assert (
        client.post(
            "/api/demand/cases/999999/close",
            json={"outcome": "reduced"},
            headers=w.headers("buyer"),
        ).status_code
        == 404
    )


def test_a3_earlier_moves_dates_and_takes_no_stock_from_mo1(make) -> None:  # type: ignore[no-untyped-def]
    w = make("apex")
    held = {k: [x["reserved_qty"] for x in v] for k, v in _lines(w).items()}
    before = _requests(w)
    report = _import(w, "A3-earlier")
    assert report.rejected == [] and "earlier" in report.changes[0].what
    assert _check_truth(w, "A3-earlier", before) == 10
    assert {k: [x["reserved_qty"] for x in v] for k, v in _lines(w).items()} == held
    assert _requests(w) == before
    dates = {
        r[0]
        for r in w.db.execute(
            text(
                "select distinct rl.need_by from request_line rl join request r on r.no = rl.request_no join material_request m on m.no = r.source_ref where m.order_no = 'MO-2'"
            )
        )
    }
    assert dates == {date(2026, 12, 3)}
    assert w.db.execute(text("select need_by from material_request where order_no = 'MO-2'")).scalar() == date(
        2026, 12, 3
    )


def test_a4_up_buys_the_increase_and_never_takes_a_later_orders_stock(make) -> None:  # type: ignore[no-untyped-def]
    w = make("apex")
    others = {k: [x["reserved_qty"] for x in v] for k, v in _lines(w).items() if k[0] != "MO-1"}
    first = w.db.execute(
        text(
            "select total_value from request where source_ref = (select no from material_request where order_no = 'MO-1')"
        )
    ).scalar()
    before = _requests(w)
    _import(w, "A4-up")
    assert _check_truth(w, "A4-up", before) == 10
    new = _requests(w) - before
    assert len(new) == 1
    assert w.db.execute(text("select total_value from request where no = :n"), {"n": new.pop()}).scalar() == D(
        "31370.00"
    )
    assert {k: [x["reserved_qty"] for x in v] for k, v in _lines(w).items() if k[0] != "MO-1"} == others
    assert first == D("72930.00")


def test_a5_cut_below_what_was_issued_is_a_store_case(make) -> None:  # type: ignore[no-untyped-def]
    w = make("apex")
    mo1 = w.db.execute(text("select no from material_request where order_no = 'MO-1'")).scalar_one()
    line = w.db.execute(
        text("select line_no from material_request_line where mr_no = :n and item_code = 'RM-03'"),
        {"n": mo1},
    ).scalar_one()
    material.issue(w.db, w.uid("store"), mo1, {line: D(30)})
    before = _requests(w)
    report = _import(w, "A5-cut-after-issue")
    assert report.rejected == []
    assert _check_truth(w, "A5-cut-after-issue", before) == 17
    ln = _lines(w)[("MO-1", "RM-03")][0]
    assert (ln["status"], ln["issued_qty"], ln["reserved_qty"]) == (
        "issued",
        D(30),
        D(0),
    )
    store = w.db.execute(
        text(
            "select t.assignee, t.title from task t join demand_change_case c on c.task_id = t.id where c.owner_kind = 'store'"
        )
    ).one()
    assert store.assignee == w.uid("store") and "issued too many" in store.title


def test_a5b_cancel_after_issue_cuts_the_request_and_keeps_what_was_issued(
    make,
) -> None:  # type: ignore[no-untyped-def]
    w = make("apex")
    mo1 = w.db.execute(text("select no from material_request where order_no = 'MO-1'")).scalar_one()
    pr = w.db.execute(text("select no from request where source_ref = :n"), {"n": mo1}).scalar_one()
    line = w.db.execute(
        text("select line_no from material_request_line where mr_no = :n and item_code = 'RM-03'"),
        {"n": mo1},
    ).scalar_one()
    material.issue(w.db, w.uid("store"), mo1, {line: D(30)})
    before = _requests(w)
    report = _import(w, "A5b-cancel-after-issue")
    assert _check_truth(w, "A5b-cancel-after-issue", before) == 19
    assert report.changes[0].requests_cancelled == [pr]
    assert w.db.execute(text("select status from request where no = :n"), {"n": pr}).scalar() == "cancelled"
    assert w.db.execute(text("select status from material_request where no = :n"), {"n": mo1}).scalar() == "cancelled"
    assert _lines(w)[("MO-1", "RM-03")][0]["issued_qty"] == D(30)


def test_r1_refused_rows_say_why_and_the_valid_row_applies(make) -> None:  # type: ignore[no-untyped-def]
    w = make("apex")
    dry = _import(w, "R1-refused", dry_run=True)
    assert dry.import_id is None and [c.what for c in dry.changes] == [["later"]]
    assert w.db.execute(text("select count(*) from demand_change")).scalar() == 0
    report = _import(w, "R1-refused")
    reasons = {p.line: p.reason for p in report.rejected}
    assert reasons == {
        2: "location_code PLT-CKN -> PLT-SND cannot change: cancel MO-1 and send a new order",
        3: "product_code FG-02 -> FG-01 cannot change: cancel MO-2 and send a new order",
        4: "nothing to cancel: MO-9 was never loaded",
    }
    assert [c.what for c in report.changes] == [["later"]]
    assert w.db.execute(text("select need_by from production_order where order_no = 'MO-2'")).scalar() == date(
        2026, 12, 18
    )
    assert w.db.execute(text("select count(*) from audit_event where action = 'demand.change'")).scalar() == 1


def test_a_header_quantity_alone_warns_and_a_cancelled_header_refuses_its_lines(make, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    w = make("apex")
    head = tmp_path / "h" / "production_order.csv"
    head.parent.mkdir()
    head.write_text(
        "order_no,product_code,qty,need_by,reason,location_code\nMO-2,FG-02,60,2026-12-15,DP-2026-12,PLT-CKN\n",
        encoding="utf-8",
    )
    report = demand.import_demand(w.db, head, tenant=w.slug)
    assert [p.reason for p in report.warnings] == [
        "MO-2: quantity 100 -> 60; its components were not in this import, so its material request is unchanged"
    ]
    folder = tmp_path / "both"
    folder.mkdir()
    (folder / "production_order.csv").write_text(
        "order_no,product_code,qty,need_by,status\nMO-1,FG-01,50,2026-12-05,cancelled\n",
        encoding="utf-8",
    )
    (folder / "production_order_line.csv").write_text("order_no,item_code,qty\nMO-1,RM-01,40\n", encoding="utf-8")
    report = demand.import_demand(w.db, folder, tenant=w.slug)
    assert [(p.table, p.reason) for p in report.rejected] == [("production_order_line", "MO-1 is cancelled")]
    again = demand.import_demand(w.db, folder / "production_order.csv", tenant=w.slug)
    assert (again.rows_changed, again.already_loaded) == (0, 1)
    bad = tmp_path / "bad.csv"
    bad.write_text(
        "order_no,product_code,qty,need_by,status\nMO-2,FG-02,60,2026-12-15,closed\n",
        encoding="utf-8",
    )
    got = demand.import_demand(w.db, bad, tenant=w.slug, kind="production_order")
    assert [p.reason for p in got.rejected] == ["status 'closed' must be open or cancelled"]


def test_manual_cancel_cuts_requests_not_yet_with_the_buyer(make) -> None:  # type: ignore[no-untyped-def]
    w = make("apex")
    mo1 = w.db.execute(text("select no from material_request where order_no = 'MO-1'")).scalar_one()
    pr = w.db.execute(text("select no from request where source_ref = :n"), {"n": mo1}).scalar_one()
    resp = client.post(
        f"/api/material-requests/{mo1}/cancel",
        json={"reason": "customer called it off"},
        headers=w.headers("store"),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["requests_cancelled"] == [pr] and body["purchase_requests_left"] == [] and body["cases"] == []
    listing = client.get("/api/material-requests", headers=w.headers("store")).json()["requests"]
    mine = next(r for r in listing if r["no"] == mo1)
    assert mine["changes"][0]["what"] == ["cancelled"] and mine["source_kind"] == "production_order"
    mrp = next(r for r in listing if r["source_kind"] == "mrp")
    assert mrp["product_code"] is None and {ln["basis"] for ln in mrp["lines"]} == {"gross"}


def test_changes_endpoint_and_tenant_isolation(make) -> None:  # type: ignore[no-untyped-def]
    a = make("apex")
    report = _import(a, "A1-cut")
    got = client.get(f"/api/demand/changes?import_id={report.import_id}", headers=a.headers("store")).json()["changes"]
    assert len(got) == 11 and got[0]["id"] > got[-1]["id"]
    rm10 = next(c for c in got if c["item_code"] == "RM-10")
    assert rm10["released"] == {"RM-10": "40"} and rm10["what"] == ["qty_down"] and rm10["location_code"] == "PLT-CKN"
    assert client.get("/api/demand/changes?import_id=x", headers=a.headers("store")).status_code == 422
    assert client.get("/api/demand/changes", headers=a.headers("production")).status_code == 403
    b = make("brightwave")
    assert client.get("/api/demand/changes", headers=b.headers("store")).json() == {"changes": []}


# ---- 6.3: Brightwave ---------------------------------------------------------------------------------


@pytest.mark.parametrize(("scenario", "rows"), [("B1-cut", 9), ("B3-up", 6), ("B4-moved", 12)])
def test_brightwave_joiner_changes(make, scenario: str, rows: int) -> None:  # type: ignore[no-untyped-def]
    w = make("brightwave")
    assert w.db.execute(text("select count(*) from material_request where source_kind = 'joiner'")).scalar() == 2
    states = dict(
        w.db.execute(
            text("select m.order_no, r.status from request r join material_request m on m.no = r.source_ref")
        ).all()
    )
    assert set(states.values()) == {"submitted"}  # not yet with the buyer
    before = _requests(w)
    report = _import(w, scenario)
    assert report.rejected == []
    if scenario == "B4-moved":
        assert len(report.material_requests) == 1
        material.store_check(w.db, user(w.uid("store")), tenant=w.slug)
    assert _check_truth(w, scenario, before) == rows


def test_b2_cancel_with_the_buyer(make) -> None:  # type: ignore[no-untyped-def]
    w = make("brightwave")
    _approve_and_source(w, "JP-2026-12/ENG")
    before = _requests(w)
    report = _import(w, "B2-cancel")
    assert _check_truth(w, "B2-cancel", before) == 6
    buyer = w.uid("buyer")
    assert {t[0] for t in w.db.execute(text("select assignee from task where kind = 'demand_change'"))} == {buyer}
    other = make("apex")
    task_id = report.changes[0].cases[0]["task_id"]
    assert (
        client.post(
            f"/api/demand/cases/{task_id}/close",
            json={"outcome": "reduced"},
            headers=other.headers("buyer"),
        ).status_code
        == 404
    )
