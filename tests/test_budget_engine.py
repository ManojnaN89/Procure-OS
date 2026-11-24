"""The budget engine on the Apex demo (task 3.7, spec scenarios BG0, BG1, PL1, YE1): what requests, orders and actuals
hold, stock spent at receipt and the rest at payment (D-10), locked months, adjustments as proposals and the year end.
The expected figures are the generator's truth file (truth/truth_budget.csv), worked out from the raw files."""

import csv
import shutil
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from stock_scenarios_support import DATASETS, Run, drop, scenarios

from p2p.approvals import budget_ledger, budget_periods
from p2p.formulas.calendar import pin_today
from p2p.main import app
from p2p.masters.importer import import_masters
from p2p.orders import changes
from p2p.orders import service as orders
from p2p.platform import permissions, proposals
from p2p.platform.sod import user
from p2p.platform.tokens import create_access_token
from p2p.receiving import goods
from p2p.sourcing import service as sourcing

D = Decimal
client = TestClient(app)
FIELDS = ("budget", "held_by_requests", "held_by_orders", "spent", "committed", "requested", "used_pct", "status")


def _truth(scenario: str, step: str) -> dict[tuple[str, str], dict[str, str]]:
    with (DATASETS["apex"] / "truth" / "truth_budget.csv").open(encoding="utf-8", newline="") as f:
        return {
            (r["gl_code"], r["level"]): r for r in csv.DictReader(f) if (r["scenario"], r["step"]) == (scenario, step)
        }


@pytest.fixture
def a0(control_db: str) -> Iterator[Run]:
    r = Run("apex")
    for st in next(s for s in scenarios("apex") if s["name"] == "A0")["steps"]:
        r.step(st)
    r.s.rollback()
    yield r
    drop(r)


def _headers(r: Run, code: str) -> dict[str, str]:
    uid = r.uid(code)
    role = r.s.execute(text("select role from user_profile where user_id = :u"), {"u": uid}).scalar()
    return {"Authorization": f"Bearer {create_access_token(uid, r.slug, str(role or 'Requester'))}"}


def _groups(r: Run, request_no: str) -> dict[tuple[str, str], dict[str, Any]]:
    r.s.rollback()
    got = client.get(f"/api/budgets/check/{request_no}", headers=_headers(r, "U05"))
    assert got.status_code == 200, got.text
    return {(g["gl_code"], "location" if g["level"] == "location" else "department"): g for g in got.json()["groups"]}


def _same_as_truth(r: Run, request_no: str, scenario: str, step: str) -> None:
    want = _truth(scenario, step)
    got = _groups(r, request_no)
    assert set(got) == set(want), (got.keys(), want.keys())
    for key, row in want.items():
        assert {f: got[key][f] for f in FIELDS} == {f: row[f] for f in FIELDS}, (scenario, step, key)


def _wire_order(r: Run) -> None:
    """BG1 setup: MO-1 approved and sourced; the wire bought directly from SUP-005 at Rs 880 and the bearings from
    SUP-002 at Rs 38 (in the dataset bearings have no contract, so sourcing sends them to quotation; the buyer buys them
    direct instead), the purchase manager agrees; the orders made. Castings come from contract CNT-0001 at Rs 650."""
    r.step({"do": "approve_request", "who": "U07", "on": "2026-11-23", "args": {"mr_order_no": "MO-1"}})
    mo1 = r.request_of("MO-1")
    today = date(2026, 11, 23)
    sourcing.sweep(r.s, r.slug, as_of=today, dispatch=False)
    for item, supplier, price, why in (
        ("RM-08", "SUP-005", "880.0000", "Spot price agreed by phone"),
        ("RM-06", "SUP-002", "38.0000", "Usual bearing supplier at the standard price"),
    ):
        line = r.s.execute(
            text("select line_no from request_line where request_no = :n and item_code = :i"), {"n": mo1, "i": item}
        ).scalar_one()
        asked = sourcing.change_route(
            r.s, r.slug, mo1, int(line), "direct", user(r.uid("U03")), reason=why,
            as_of=today, supplier_code=supplier, price=D(price), dispatch=False,
        )  # fmt: skip
        proposals.approve(r.s, int(asked["proposal_id"]), r.uid("U08"))
    for (pid,) in r.s.execute(
        text("select id from proposal where status = 'pending' and action = 'sourcing.agree_direct'")
    ).all():
        proposals.approve(r.s, int(pid), r.uid("U08"))
    orders.sweep(r.s, r.slug, as_of=today)
    for item in ("RM-01", "RM-06", "RM-08"):
        r.step({"do": "issue_po", "who": "U03", "on": "2026-11-23", "args": {"item_code": item}})
    r.s.rollback()
    prices = dict(
        r.s.execute(
            text(
                "select pl.item_code, pl.unit_price from po_line pl join po p on p.no = pl.po_no "
                "where p.status not in ('cancelled', 'rejected') and pl.request_no = :n"
            ),
            {"n": mo1},
        ).all()
    )
    assert {k: D(v) for k, v in prices.items() if k != "PK-01"} == {"RM-01": D(650), "RM-06": D(38), "RM-08": D(880)}


def test_the_order_price_replaces_the_estimate_and_stock_is_spent_when_accepted(a0: Run) -> None:
    r = a0
    pin_today(date(2026, 11, 23))
    mrp, mo2 = r.request_of("MRP-2027-01/PLT-CKN"), r.request_of("MO-2")
    # BG0: other requests hold the budget at their estimates; nothing ordered or spent
    _same_as_truth(r, mrp, "BG0", "base")

    # BG1: the orders replace their request lines (4,34,070 held, not 4,35,670 at the estimate)
    _wire_order(r)
    _same_as_truth(r, mo2, "BG1", "after_orders")

    # the wire is accepted on 25 Nov: Rs 35,200 spent in November at the order price, from the receipt
    pin_today(date(2026, 11, 25))
    r.step({"do": "receive", "who": "U02", "on": "2026-11-25", "args": {"item_code": "RM-08", "qty": "40"}})
    _same_as_truth(r, mo2, "BG1", "after_receipt")
    wire = r.s.execute(
        text("select source, period, amount, invoice_no, gr_no from budget_actual where gl_code = '1410'")
    ).all()
    assert [(w[0], w[1], D(w[2]), w[3]) for w in wire] == [("receipt", "2026-11", D("35200.00"), None)]
    assert (
        r.s.execute(
            text("select count(*) from audit_event where action = 'budget.actual' and entity_id = :g"),
            {"g": wire[0][4]},
        ).scalar_one()
        == 1
    )

    # bearings wait for inspection: nothing at the gate; 58 accepted spend Rs 2,204, the 2 rejected never
    po_no, line_no = r.po_of("RM-06")
    posted = goods.post_receipt(
        r.s, po_no, [goods.LineIn(line_no, D(60))], goods.Header(challan="DC-2"), r.uid("U02"), as_of=date(2026, 11, 25)
    )
    assert r.s.execute(text("select count(*) from budget_actual where po_no = :p"), {"p": po_no}).scalar_one() == 0
    goods.inspect(r.s, posted.no, [goods.Decision(line_no, D(58), D(2), "Two with cracked seals")], r.uid("U02"),
                  as_of=date(2026, 11, 25))  # fmt: skip
    _same_as_truth(r, mo2, "BG1", "after_inspection")

    # closing the balance releases the 2 bearings never to come
    changes.close_balance(r.s, r.slug, po_no, r.uid("U03"), "Two rejected; not replaced")
    _same_as_truth(r, mo2, "BG1", "after_close")

    # paying the wire's invoice: only the freight is spent, the wire was spent at receipt
    wire_po, wire_line = r.po_of("RM-08")
    inv = _invoice(r, wire_po, wire_line, D("35200.00"), freight=D("500.00"), received_on=date(2026, 11, 26))
    rows = budget_ledger.post_payment(r.s, inv, user(r.uid("U05")), date(2026, 11, 27))
    r.s.commit()
    assert [(x["source"], x["period"], x["amount"], x["po_line_no"]) for x in rows] == [
        ("payment", "2026-11", "500.00", None)
    ]
    pin_today(date(2026, 11, 27))
    _same_as_truth(r, mo2, "BG1", "after_payment")

    # a cancelled order gives the estimate back to its request line (PK-01 is still with the buyer: no order)
    variance = client.get("/api/budgets/variance", headers=_headers(r, "U05")).json()
    loc = next(x for x in variance["locations"] if (x["location"], x["gl_code"]) == ("PLT-CKN", "1410"))
    held = D(loc["held_by_requests"]) + D(loc["held_by_orders"]) + D(loc["spent"])
    assert D(loc["left"]) == D(loc["budget"]) - held


def _invoice(r: Run, po_no: str, line_no: int, taxable: Decimal, *, freight: Decimal, received_on: date) -> str:
    """A paid invoice for one order line, written as the invoice service leaves it (only what the ledger reads)."""
    no = f"PI-T{line_no}-{received_on:%d}"
    supplier = r.s.execute(text("select supplier_code from po where no = :p"), {"p": po_no}).scalar_one()
    r.s.execute(
        text(
            "insert into invoice (no, type, supplier_code, supplier_invoice_no, dedupe_key, invoice_date, fy, received_at, "
            "received_on, channel, taxable, cgst, sgst, igst, cess, round_off, total, payment_block, status, tds_amount, "
            "advance_used, advance_taxable_used, discount_taken, reverse_charge) values (:n, 'invoice', :s, :n, :n, :d, "
            "'2026-27', now(), :d, 'manual', :t, 0, 0, 0, 0, 0, :t, 'none', 'paid', 0, 0, 0, 0, false)"
        ),
        {"n": no, "s": supplier, "d": received_on, "t": taxable},
    )
    r.s.execute(
        text(
            "insert into invoice_line (invoice_no, line_no, po_no, po_line_no, qty, unit_price, discount, taxable, "
            "gst_rate, cgst, sgst, igst) values (:n, 1, :p, :l, 1, :t, 0, :t, 18, 0, 0, 0)"
        ),
        {"n": no, "p": po_no, "l": line_no, "t": taxable},
    )
    if freight:
        r.s.execute(
            text("insert into invoice_charge (invoice_no, type, amount, gst_rate) values (:n, 'freight', :a, 18)"),
            {"n": no, "a": freight},
        )
    r.s.commit()
    return no


def test_locked_months_take_no_postings_and_adjustments_need_a_second_person(a0: Run, tmp_path: Path) -> None:
    r = a0
    pin_today(date(2026, 12, 7))
    finance, buyer = _headers(r, "U05"), _headers(r, "U03")
    # the demo has every month to October locked; November is the next
    periods = client.get("/api/budgets/periods?fy=2026-27", headers=finance).json()
    assert periods["can_lock"] == "2026-11" and not periods["can_close"]
    assert [p["status"] for p in periods["periods"]][6:9] == [
        "locked",
        "open",
        "future",
    ]  # Oct, Nov, Dec (current: not ended)
    assert client.post("/api/budgets/periods/2026-11/lock", json={"reason": "x"}, headers=buyer).status_code == 403
    assert client.post("/api/budgets/periods/2026-11/lock", json={}, headers=finance).status_code == 422
    done = client.post("/api/budgets/periods/2026-11/lock", json={"reason": "Month-end close"}, headers=finance)
    assert done.status_code == 200 and done.json()["locked_by"] == "U05"
    assert client.post("/api/budgets/periods/2026-12/lock", json={"reason": "x"}, headers=finance).status_code == 422
    assert client.post("/api/budgets/periods/2026-11/lock", json={"reason": "x"}, headers=finance).status_code == 409

    # a payment for an invoice received in the locked month is booked in December, keeping November as its event
    _wire_order(r)
    po_no, line_no = r.po_of("RM-01")
    r.s.execute(text("update item set stocked = false where code = 'RM-01'"))  # a non-stock line for this check
    inv = _invoice(r, po_no, line_no, D("1300.00"), freight=D(0), received_on=date(2026, 11, 28))
    (row,) = budget_ledger.post_payment(r.s, inv, user(r.uid("U05")), date(2026, 12, 7))
    r.s.commit()
    assert (row["period"], row["event_period"], row["amount"]) == ("2026-12", "2026-11", "1300.00")
    assert r.s.execute(text("select count(*) from audit_event where action = 'budget.actual_moved'")).scalar_one() == 1
    locked = {p for (p,) in r.s.execute(text("select period from budget_period_lock"))}
    assert not r.s.execute(text("select 1 from budget_actual where period = any(:l)"), {"l": list(locked)}).first()

    # an adjustment into a locked month is refused; one into December waits for another finance person
    body = {"kind": "supplement", "gl_code": "5240", "cost_centre_code": "CC-MNT", "period": "2026-11",
            "amount": "50000", "reason": "Pump overhaul brought forward"}  # fmt: skip
    refused = client.post("/api/budgets/adjustments", json=body, headers=finance)
    assert refused.status_code == 422 and "2026-11 is locked" in refused.text
    assert (
        client.post(
            "/api/budgets/adjustments", json={**body, "period": "2026-12", "amount": "0"}, headers=finance
        ).status_code
        == 422
    )
    made = client.post("/api/budgets/adjustments", json={**body, "period": "2026-12"}, headers=finance)
    assert made.status_code == 201
    pid = made.json()["proposal_id"]
    assert not r.s.execute(text("select 1 from budget where kind = 'adjustment'")).first()  # nothing before approval
    with pytest.raises(permissions.PermissionDenied):
        proposals.approve(r.s, pid, r.uid("U05"))  # the proposer never approves
    r.s.execute(text("insert into user_role (user_id, role) values (:u, 'Finance')"), {"u": r.uid("U09")})
    r.s.commit()
    proposals.approve(r.s, pid, r.uid("U09"))
    (adj,) = r.s.execute(text("select amount, reason, proposal_id from budget where kind = 'adjustment'")).all()
    assert (D(adj[0]), adj[1], adj[2]) == (D("50000.00"), "Pump overhaul brought forward", pid)

    # a masters import leaves out a new budget row for a locked month, with the reason
    copy = tmp_path / "apex-v1"
    shutil.copytree(DATASETS["apex"] / "master", copy / "master")
    with (copy / "master" / "budget.csv").open("a", encoding="utf-8", newline="") as f:
        f.write("CC-MNT,5240,,2026-11,1000.00,2,block,\n")
    report = import_masters(r.s, copy, dry_run=True)
    assert any(p.reason == "period 2026-11 is locked" for p in report.rejected)


def test_the_year_end_carries_open_commitments_and_lets_unspent_budget_lapse(a0: Run) -> None:
    r = a0
    finance = _headers(r, "U05")
    pin_today(date(2027, 3, 31))
    assert client.post("/api/budgets/years/2026-27/close", json={}, headers=finance).status_code == 422
    pin_today(date(2027, 4, 5))
    assert client.post("/api/budgets/years/2027-28/close", json={}, headers=finance).status_code == 422
    closed = client.post("/api/budgets/years/2026-27/close", json={"note": "Year end"}, headers=finance)
    assert closed.status_code == 200, closed.text
    got = closed.json()
    assert got["locked"] == ["2026-11", "2026-12", "2027-01", "2027-02", "2027-03"]
    assert got["carried"] == [
        {"gl_code": "1410", "cost_centre_code": None, "location_code": "PLT-CKN", "held_by_requests": "640870.00",
         "held_by_orders": "0.00"},
        {"gl_code": "1420", "cost_centre_code": None, "location_code": "PLT-CKN", "held_by_requests": "11250.00",
         "held_by_orders": "0.00"},
    ]  # fmt: skip
    assert got["carried_total"] == "652120.00"
    assert client.get("/api/budgets/years/2026-27", headers=finance).json()["carried"] == got["carried"]
    assert client.post("/api/budgets/years/2026-27/close", json={}, headers=finance).status_code == 409
    # the open requests count against the new year's budget, which alone is read
    _same_as_truth(r, r.request_of("MRP-2027-01/PLT-CKN"), "YE1", "new_year")
    assert budget_periods.is_locked(r.s, "2027-03")


# ---- block 10: a bill without an order, held from authorisation and spent at payment ---------------

from test_non_po_invoices import _authorise, _enter, bills  # noqa: E402, F401 - fixtures
from test_orders import shop  # noqa: E402, F401 - fixtures


def test_n6_a_bill_without_an_order_is_held_from_authorisation_and_spent_when_paid(bills: Any) -> None:  # noqa: F811
    done = _enter(bills, number="SNL/2627/0458", on=date(2026, 11, 23))
    _authorise(bills, done.no)

    held = bills.execute(text("select part, amount from budget_use where cost_centre_code = 'CC-ADM'")).all()
    assert held == [("order", D("15000.00"))]
    bills.execute(text("update invoice set status = 'paid' where no = :n"), {"n": done.no})
    budget_ledger.post_payment(bills, done.no, user(bills.info["bills"]["owner"]), date(2026, 11, 30))
    bills.commit()

    actual = bills.execute(
        text("select source, amount, cost_centre_code, gl_code, period, event_period from budget_actual")
    ).all()
    assert [(s, a, c, g, p, e) for s, a, c, g, p, e in actual] == [
        ("payment", D("15000.00"), "CC-ADM", "5460", "2026-11", "2026-11")
    ]
    assert bills.execute(text("select count(*) from budget_use where cost_centre_code = 'CC-ADM'")).scalar_one() == 1
from test_sourcing import db  # noqa: E402, F401 - fixtures
