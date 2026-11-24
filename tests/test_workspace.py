"""The role home, the control tower, the KPI formulas and the alert engine."""

from datetime import date, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_invoices import S1_GSTIN, TODAY, _goods, _invoice, ledger  # noqa: F401 - fixtures
from test_orders import _headers, _sent_rfq, _three_quotes, shop  # noqa: F401 - fixtures
from test_receiving import _receive, _sent, store  # noqa: F401 - fixtures
from test_sourcing import PASSWORD, Outbox, db, outbox  # noqa: F401 - fixtures

from p2p.formulas import kpis as k
from p2p.formulas.calendar import BusinessCalendar
from p2p.main import app
from p2p.platform import alerts, proposals, users

D = Decimal
client = TestClient(app)
CAL = BusinessCalendar.of()


# ---- the formulas -----------------------------------------------------------------------------


def test_working_days_are_counted_after_the_start_day_up_to_and_including_the_end_day() -> None:
    friday = date(2026, 11, 6)
    assert k.working_days_between(friday, friday, CAL) == 0
    assert k.working_days_between(friday, date(2026, 11, 7), CAL) == 0  # Saturday
    assert k.working_days_between(friday, date(2026, 11, 9), CAL) == 1  # Monday
    assert k.working_days_between(friday, date(2026, 11, 13), CAL) == 5
    assert k.working_days_between(date(2026, 11, 9), friday, CAL) == 0  # nothing for a date in the future
    assert k.working_days_between(friday, date(2026, 11, 9), BusinessCalendar.of([date(2026, 11, 9)])) == 0  # a holiday


@pytest.mark.parametrize(
    ("days", "bucket"),
    [(0, "0-2"), (2, "0-2"), (3, "3-7"), (7, "3-7"), (8, "8-14"), (14, "8-14"), (15, "15+"), (400, "15+")],
)
def test_the_ageing_bucket_edges(days: int, bucket: str) -> None:
    assert k.bucket_of(days) == bucket


def test_median_and_the_ninetieth_percentile_by_nearest_rank() -> None:
    assert k.median([]) is None and k.percentile_90([]) is None
    assert k.median([3]) == D("3.0") and k.median([1, 2, 3, 4]) == D("2.5") and k.median([5, 1, 3]) == D("3.0")
    ten = list(range(1, 11))
    assert k.percentile_90(ten) == D("9.0")  # rank ceil(0.9 x 10) = 9
    assert k.percentile_90(list(range(1, 12))) == D("10.0")  # rank ceil(9.9) = 10
    assert k.percentile_90([7]) == D("7.0")


def test_shares_and_savings_are_exact() -> None:
    assert k.share(1, 3) == D("33.3") and k.share(2, 3) == D("66.7") and k.share(0, 0) is None
    assert k.spend_under_management(D("750"), D("1000")) == D("75.0")
    assert k.saving(D("110"), D("100"), D("5")) == D("50.00") and k.saving(D("90"), D("100"), D("5")) == D("-50.00")


# ---- people for each role ---------------------------------------------------------------------


def _roles(shop_: Session) -> dict[str, int]:
    slug, sfx = shop_.info["slug"], shop_.info["sfx"]
    return {
        r: users.create_user(slug, f"{r.lower()}{sfx}@w.example", PASSWORD, f"A {r}", r)
        for r in ("Approver", "Buyer", "AP", "Finance", "Store", "Requester")
    }


def _notified(shop_: Session, user_id: int) -> list[tuple[str, str, str | None]]:
    shop_.expire_all()
    return [
        (r[0], r[1], r[2])
        for r in shop_.execute(
            text(
                "select title, severity, link from notification where user_id = :u and related_type = 'alert' order by id"
            ),
            {"u": user_id},
        )
    ]


def _alert(shop_: Session, key: str) -> dict:
    shop_.expire_all()
    return dict(shop_.execute(text("select * from alert where key = :k"), {"k": key}).mappings().one())


# ---- the alert engine -------------------------------------------------------------------------


def test_a_stale_requisition_is_raised_once_to_approvers_only_resolved_and_raised_again(
    store: Session, outbox: Outbox
) -> None:  # noqa: F811
    people = _roles(store)
    slug = store.info["slug"]
    store.execute(
        text(
            "insert into request (no, ship_to_location, type, channel, status, total_value, submitted_at) values ('R-S', 'WH-BLR', 'goods', 'planning', 'submitted', 5000, '2026-10-26')"
        )
    )
    store.commit()

    first = alerts.run(store, slug, today=TODAY)

    assert first.opened == ["stale_requisition:R-S"]
    assert _notified(store, people["Approver"]) == [
        ("R-S has waited 9 working days for approval", "high", "#/requisitions/R-S")
    ]
    assert all(_notified(store, people[r]) == [] for r in ("Buyer", "AP", "Finance", "Store", "Requester"))
    again = alerts.run(store, slug, today=TODAY)
    assert again.opened == [] and again.resolved == [] and again.open_now == 1  # nothing is sent a second time
    assert len(_notified(store, people["Approver"])) == 1

    store.execute(text("update request set status = 'approved' where no = 'R-S'"))
    store.commit()
    gone = alerts.run(store, slug, today=TODAY)
    assert gone.resolved == ["stale_requisition:R-S"] and _alert(store, "stale_requisition:R-S")["status"] == "resolved"
    assert (
        store.execute(
            text("select resolved from notification where related_type = 'alert' and user_id = :u"),
            {"u": people["Approver"]},
        ).scalar_one()
        is True
    )

    store.execute(text("update request set status = 'submitted' where no = 'R-S'"))
    store.commit()
    back = alerts.run(store, slug, today=TODAY)
    row = _alert(store, "stale_requisition:R-S")
    assert back.opened == ["stale_requisition:R-S"] and (row["status"], row["times_opened"]) == ("open", 2)
    assert len(_notified(store, people["Approver"])) == 2  # told again, because it came back


def test_a_requisition_within_the_limit_is_not_stale_and_the_limit_is_a_setting(store: Session, outbox: Outbox) -> None:  # noqa: F811
    _roles(store)
    slug = store.info["slug"]
    store.execute(
        text(
            "insert into request (no, ship_to_location, type, channel, status, total_value, submitted_at) values ('R-S', 'WH-BLR', 'goods', 'planning', 'submitted', 5000, '2026-11-03')"
        )
    )
    store.commit()  # three working days before the 6th: not yet more than the 3 allowed
    assert alerts.run(store, slug, today=TODAY).opened == []
    store.execute(
        text(
            "insert into app_setting (key, value) values ('stale_requisition_days', '2') on conflict (key) do update set value = '2'"
        )
    )
    store.commit()
    assert alerts.run(store, slug, today=TODAY).opened == ["stale_requisition:R-S"]


def test_an_rfq_past_its_reply_date_with_no_quote_alerts_the_buyer_until_a_quote_arrives(
    shop: Session, outbox: Outbox
) -> None:  # noqa: F811
    people = _roles(shop)
    slug = shop.info["slug"]
    _, rfq = _sent_rfq(shop, outbox)  # replies were due on 13 November

    assert alerts.run(shop, slug, today=date(2026, 11, 13)).opened == []
    assert alerts.run(shop, slug, today=date(2026, 11, 16)).opened == [f"rfq_no_quotes:{rfq}"]
    assert _notified(shop, people["Buyer"])[0][2] == f"#/rfqs/{rfq}"
    _three_quotes(shop, rfq)
    assert alerts.run(shop, slug, today=date(2026, 11, 16)).resolved == [f"rfq_no_quotes:{rfq}"]


def test_an_order_not_acknowledged_in_time_and_goods_later_than_promised_alert_the_buyer(
    store: Session, outbox: Outbox
) -> None:  # noqa: F811
    people = _roles(store)
    slug = store.info["slug"]
    po_no, _ = _sent(store)
    store.execute(text("update po set sent_at = '2026-11-02 10:00+05:30'"))
    store.execute(text("update po_version set sent_at = '2026-11-02 10:00+05:30'"))  # the version's clock (block 8)
    store.execute(text("update po_line set promised_date = '2026-11-03'"))
    store.commit()

    done = alerts.run(store, slug, today=TODAY)

    assert sorted(done.opened) == [f"late_delivery:{po_no}", f"po_not_acknowledged:{po_no}"]
    assert _alert(store, f"late_delivery:{po_no}")["title"] == f"Goods on {po_no} are 3 days late"
    assert len(_notified(store, people["Buyer"])) == 2
    _receive(store, po_no, "10")  # the goods arrive
    store.execute(text("update po set status = 'acknowledged'"))
    store.commit()
    assert sorted(alerts.run(store, slug, today=TODAY).resolved) == [
        f"late_delivery:{po_no}",
        f"po_not_acknowledged:{po_no}",
    ]


def test_a_blocked_invoice_that_waits_too_long_alerts_ap_and_a_duplicate_does_not(
    ledger: Session, outbox: Outbox
) -> None:  # noqa: F811
    people = _roles(ledger)
    slug = ledger.info["slug"]
    po_no, _ = _goods(ledger, qty="10", price="1000")
    blocked = _invoice(ledger, po_no, "10", "1060", number="B-1")
    _invoice(ledger, po_no, "10", "1060", number="B 1")  # a copy of it

    assert alerts.run(ledger, slug, today=TODAY).opened == []  # just arrived
    later = alerts.run(ledger, slug, today=date(2026, 11, 13))
    assert later.opened == [f"invoice_blocked:{blocked.no}"]
    assert _notified(ledger, people["AP"])[0][2] == f"#/invoices/{blocked.no}"


def test_an_invoice_falling_due_alerts_finance_high_for_a_small_supplier_and_not_when_it_is_already_in_a_run(
    ledger: Session, outbox: Outbox
) -> None:  # noqa: F811
    people = _roles(ledger)
    slug = ledger.info["slug"]
    po_no, _ = _goods(ledger, qty="10")
    inv = _invoice(ledger, po_no, "10", "100")
    ledger.execute(text("update invoice set due_date = '2026-11-08' where no = :n"), {"n": inv.no})
    ledger.commit()

    done = alerts.run(ledger, slug, today=TODAY)
    row = _alert(ledger, f"invoice_due:{inv.no}")
    assert done.opened == [f"invoice_due:{inv.no}"] and (row["severity"], "is due soon" in row["title"]) == (
        "medium",
        True,
    )
    assert _notified(ledger, people["Finance"])[0][1] == "medium"

    ledger.execute(text("update supplier set msme_category = 'small' where code = 'S1'"))
    ledger.commit()
    alerts.run(ledger, slug, today=date(2026, 11, 10))
    row = _alert(ledger, f"invoice_due:{inv.no}")
    assert (row["severity"], "is overdue" in row["title"], "45-day rule" in row["detail"]) == ("high", True, True)

    ledger.execute(
        text(
            "insert into payment_run (no, run_date, proposed_by, status) values ('PR-1', '2026-11-10', '1', 'proposed')"
        )
    )
    ledger.execute(text("insert into payment (no, run_no, supplier_code, amount) values ('PAY-1', 'PR-1', 'S1', 1180)"))
    ledger.execute(
        text("insert into payment_allocation (payment_no, invoice_no, amount) values ('PAY-1', :i, 1180)"),
        {"i": inv.no},
    )
    ledger.commit()
    assert alerts.run(ledger, slug, today=date(2026, 11, 10)).resolved == [
        f"invoice_due:{inv.no}"
    ]  # someone is already paying it


def test_contracts_that_end_soon_or_are_nearly_used_alert_the_buyer(store: Session, outbox: Outbox) -> None:  # noqa: F811
    people = _roles(store)
    slug = store.info["slug"]
    po_no, _ = _sent(store, qty="10", price="100")  # 1,000 ordered on K-ACTIVE, which ends on 31 December 2026
    store.execute(text("update contract set max_value = 1100 where code = 'K-ACTIVE'"))
    store.commit()

    done = alerts.run(store, slug, today=date(2026, 12, 20))

    # the order is also long unacknowledged by now; only the contract alerts are under test here
    assert sorted(o for o in done.opened if o.startswith("contract")) == [
        "contract_ceiling:K-ACTIVE",
        "contract_expiring:K-ACTIVE",
    ]
    assert _alert(store, "contract_ceiling:K-ACTIVE")["title"] == "Contract K-ACTIVE is 90.9% used"
    assert _alert(store, "contract_expiring:K-ACTIVE")["severity"] == "low"  # 11 days left
    assert len([n for n in _notified(store, people["Buyer"]) if n[0].startswith("Contract")]) == 2 and po_no
    store.execute(text("update contract set max_value = 100000 where code = 'K-ACTIVE'"))
    store.commit()
    assert alerts.run(store, slug, today=date(2026, 12, 20)).resolved == ["contract_ceiling:K-ACTIVE"]
    assert alerts.run(store, slug, today=date(2026, 12, 28)).opened == []  # still open: nothing new to say
    assert _alert(store, "contract_expiring:K-ACTIVE")["severity"] == "medium"  # now within a week


def test_a_supplier_document_that_is_wrong_or_about_to_expire_alerts_the_buyer(store: Session, outbox: Outbox) -> None:  # noqa: F811
    _roles(store)
    slug = store.info["slug"]
    store.execute(text("delete from supplier_document where supplier_code = 'S2' and doc_type = 'pan'"))
    store.execute(
        text(
            "insert into supplier_document (supplier_code, doc_type, number, issued_on, expires_on, verified) values ('S1', 'iso', 'I', '2020-01-01', '2026-11-20', true)"
        )
    )
    store.commit()

    done = alerts.run(store, slug, today=TODAY)

    assert "supplier_documents:S2" in done.opened and "supplier_documents:S1" in done.opened
    assert (_alert(store, "supplier_documents:S2")["severity"], _alert(store, "supplier_documents:S1")["severity"]) == (
        "high",
        "medium",
    )
    assert "PAN card is missing" in _alert(store, "supplier_documents:S2")["detail"]


def test_a_budget_nearly_used_alerts_approvers_and_finance(store: Session, outbox: Outbox) -> None:  # noqa: F811
    people = _roles(store)
    slug = store.info["slug"]
    x = store.execute
    x(text("insert into gl_account (code, name) values ('G1', 'Materials')"))
    x(text("insert into cost_centre (code, name, location_code, active) values ('CC1', 'Production', 'WH-BLR', true)"))
    for month in ("2026-04", "2026-05", "2026-06", "2026-07", "2026-08", "2026-09", "2026-10", "2026-11"):
        x(
            text(
                "insert into budget (cost_centre_code, gl_code, period, amount, version, policy) values ('CC1', 'G1', :p, 100000, 1, 'warn')"
            ),
            {"p": month},
        )
    x(
        text(
            "insert into request (no, ship_to_location, type, channel, status, total_value, created_at) values ('R-B', 'WH-BLR', 'goods', 'planning', 'approved', 1, '2026-09-01')"
        )
    )
    x(
        text(
            "insert into request_line (request_no, line_no, free_text, qty, est_price, gl_code, status) values ('R-B', 1, 'x', 1, 719999, 'G1', 'open')"
        )
    )
    store.commit()
    assert alerts.run(store, slug, today=TODAY).opened == []  # 89.99% of 800,000

    x(text("update request_line set est_price = 720000"))
    store.commit()
    assert alerts.run(store, slug, today=TODAY).opened == ["budget_used:G1"]  # exactly 90%
    assert _notified(store, people["Approver"])[0][0] == "GL G1: 90% of the budget so far this year is used"
    assert len(_notified(store, people["Finance"])) == 1


def test_a_rule_that_fails_does_not_stop_the_others_or_resolve_what_it_raised_before(
    store: Session, outbox: Outbox, monkeypatch: pytest.MonkeyPatch
) -> None:  # noqa: F811
    _roles(store)
    slug = store.info["slug"]
    store.execute(
        text(
            "insert into request (no, ship_to_location, type, channel, status, total_value, submitted_at) values ('R-S', 'WH-BLR', 'goods', 'planning', 'submitted', 5000, '2026-10-26')"
        )
    )
    store.execute(text("update contract set valid_to = '2026-11-20' where code = 'K-ACTIVE'"))
    store.commit()
    alerts.run(store, slug, today=TODAY)
    assert _alert(store, "stale_requisition:R-S")["status"] == "open"

    def broken(_: alerts.Context) -> list[alerts.Finding]:
        raise RuntimeError("the rule is broken")

    monkeypatch.setitem(alerts.RULES, "stale_requisition", broken)
    store.execute(text("update contract set status = 'expired' where code = 'K-ACTIVE'"))
    store.commit()
    done = alerts.run(store, slug, today=TODAY)

    assert done.failed_rules == ["stale_requisition"]
    assert _alert(store, "stale_requisition:R-S")["status"] == "open"  # not resolved by mistake
    assert done.resolved == ["contract_expiring:K-ACTIVE"]  # the other rules still ran


def test_the_daily_job_runs_the_rules_and_queues_itself_for_the_next_morning(store: Session, outbox: Outbox) -> None:  # noqa: F811
    from p2p.platform import tenants, worker

    _roles(store)
    slug = store.info["slug"]
    store.execute(
        text(
            "insert into request (no, ship_to_location, type, channel, status, total_value, submitted_at) values ('R-S', 'WH-BLR', 'goods', 'planning', 'submitted', 5000, '2026-10-26')"
        )
    )
    store.commit()

    alerts._daily(worker.JobContext(id=-1, tenant=slug, payload={}, now=datetime(2026, 11, 6, 2, 0, tzinfo=alerts.UTC)))

    assert _alert(store, "stale_requisition:R-S")["status"] == "open"
    with tenants.control_engine().connect() as c:
        queued = (
            c.execute(
                text("select run_at from job where tenant_slug = :t and kind = 'alerts' and status = 'queued'"),
                {"t": slug},
            )
            .scalars()
            .all()
        )
    assert len(queued) == 1 and queued[0] == datetime(
        2026, 11, 7, 2, 0, tzinfo=alerts.UTC
    )  # 07:30 India time, next day


# ---- the role home ----------------------------------------------------------------------------


def _sections(shop_: Session, user_id: int, role: str) -> dict[str, dict]:
    body = client.get("/api/workspace", headers=_headers(shop_.info["slug"], user_id, role)).json()
    return {s["key"]: s for s in body["sections"]}


def test_each_role_sees_only_what_needs_its_own_action(ledger: Session, outbox: Outbox) -> None:  # noqa: F811
    people = _roles(ledger)
    po_no, _ = _goods(ledger, qty="10", price="1000")
    _invoice(ledger, po_no, "10", "1060", number="W-1")  # blocked

    buyer, store_, ap, finance, requester = (
        _sections(ledger, people[r], r) for r in ("Buyer", "Store", "AP", "Finance", "Requester")
    )

    assert all(s not in buyer for s in ("blocked", "to_receive", "due"))  # a buyer is not shown AP or store work
    assert (
        set(ap) == {"blocked"}
        and ap["blocked"]["count"] == 1
        and ap["blocked"]["rows"][0]["link"].startswith("#/invoices/PI-")
    )
    assert "blocked" not in store_ and "blocked" not in finance
    assert requester == {}  # nothing of the requester's own


def test_the_buyer_sees_orders_to_send_and_acknowledge_and_the_store_sees_goods_to_come(
    store: Session, outbox: Outbox
) -> None:  # noqa: F811
    people = _roles(store)
    po_no, _ = _sent(store, qty="10", price="100")  # sent, not acknowledged, nothing received

    buyer = _sections(store, people["Buyer"], "Buyer")
    receiving = _sections(store, people["Store"], "Store")

    assert buyer["to_acknowledge"]["rows"] == [
        {"id": po_no, "label": po_no, "sub": "₹ 1,180.00", "link": f"#/purchase-orders/{po_no}"}
    ]
    assert "to_send" not in buyer  # sections with nothing in them are left out
    assert receiving["to_receive"]["count"] == 1 and receiving["to_receive"]["rows"][0]["sub"] == "Alpha Traders"
    _receive(store, po_no, "10")
    assert _sections(store, people["Store"], "Store") == {}  # nothing left to receive


def test_finance_sees_what_is_due_and_the_runs_to_pay_and_ap_the_goods_waiting_for_an_invoice(
    ledger: Session, outbox: Outbox
) -> None:  # noqa: F811
    people = _roles(ledger)
    ledger.execute(text("update item_category set default_tds_section = '194Q' where code = 'C1'"))
    ledger.commit()
    po_no, _ = _goods(ledger, qty="10")
    assert _sections(ledger, people["AP"], "AP")["to_invoice"]["rows"][0]["id"] == po_no
    inv = _invoice(ledger, po_no, "10", "100")
    ledger.execute(text("update invoice set due_date = :d, pay_by = :d where no = :n"), {"d": TODAY, "n": inv.no})
    ledger.commit()
    assert "to_invoice" not in _sections(ledger, people["AP"], "AP")

    finance = _sections(ledger, people["Finance"], "Finance")
    assert finance["due"]["count"] == 1 and finance["due"]["rows"][0]["id"] == inv.no


def test_an_approver_sees_proposals_that_need_them_but_never_their_own(store: Session, outbox: Outbox) -> None:  # noqa: F811
    people = _roles(store)
    other = users.create_user(
        store.info["slug"], f"boss{store.info['sfx']}@w.example", PASSWORD, "Another Approver", "Approver"
    )
    proposals.propose(
        store, __import__("p2p.platform.sod", fromlist=["user"]).user(people["Approver"]), action="supplier.set_status",
        summary="Block: Alpha Traders (S1) is active. Reason: x", payload={"supplier": "S1", "to": "blocked", "reason": "x"},
        required_permission="supplier.activate",
    )  # fmt: skip

    assert "suppliers" not in _sections(store, people["Approver"], "Approver")  # they asked, so someone else decides
    other_view = _sections(store, other, "Approver")
    assert other_view["suppliers"]["count"] == 1 and other_view["suppliers"]["link"] == "/admin/approvals"
    assert "suppliers" not in _sections(store, people["Buyer"], "Buyer")  # a buyer may not approve it


def test_the_home_lists_the_open_alerts_for_the_roles_of_the_person(store: Session, outbox: Outbox) -> None:  # noqa: F811
    people = _roles(store)
    store.execute(
        text(
            "insert into request (no, ship_to_location, type, channel, status, total_value, submitted_at) values ('R-S', 'WH-BLR', 'goods', 'planning', 'submitted', 5000, '2026-10-26')"
        )
    )
    store.commit()
    slug = store.info["slug"]

    ran = client.post("/api/alerts/run", headers=_headers(slug, people["Buyer"], "Buyer"))
    assert ran.status_code == 200 and ran.json()["opened"] == ["stale_requisition:R-S"]
    assert client.post("/api/alerts/run", headers=_headers(slug, people["Requester"], "Requester")).status_code == 403
    assert client.post("/api/alerts/run").status_code == 401

    mine = client.get("/api/workspace", headers=_headers(slug, people["Approver"], "Approver")).json()
    assert [a["rule"] for a in mine["alerts"]] == ["stale_requisition"] and mine["alerts"][0][
        "link"
    ] == "#/requisitions/R-S"
    assert client.get("/api/workspace", headers=_headers(slug, people["Buyer"], "Buyer")).json()["alerts"] == []
    assert [
        a["id"] for a in client.get("/api/alerts", headers=_headers(slug, people["Approver"], "Approver")).json()
    ] == [mine["alerts"][0]["id"]]
    assert client.get("/api/workspace").status_code == 401


# ---- the control tower ------------------------------------------------------------------------


def test_every_stage_places_its_cases_by_age_and_location_and_can_be_drilled_into(
    ledger: Session, outbox: Outbox
) -> None:  # noqa: F811
    people = _roles(ledger)
    slug = ledger.info["slug"]
    po_no, _ = _sent(ledger, qty="10", price="100")
    ledger.execute(text("update po set sent_at = '2026-10-01 10:00+05:30', status = 'sent'"))
    ledger.commit()
    h = _headers(slug, people["Buyer"], "Buyer")

    tower = client.get("/api/control-tower", headers=h).json()
    stages = {s["stage"]: s for s in tower["stages"]}

    assert [s["stage"] for s in tower["stages"]] == [
        "request_approval",
        "sourcing",
        "rfq_out",
        "award",
        "po_approval",
        "po_sent",
        "awaiting_receipt",
        "awaiting_invoice",
        "invoice_blocked",
        "awaiting_payment",
    ]
    assert stages["po_sent"]["count"] == 1 and stages["po_sent"]["buckets"] == {"0-2": 0, "3-7": 0, "8-14": 0, "15+": 1}
    assert (
        stages["po_sent"]["by_location"] == {"WH-BLR": {"count": 1, "value": "1180.00"}}
        and stages["po_sent"]["oldest_days"] == 26
    )
    assert tower["locations"] == ["WH-BLR"] and stages["awaiting_receipt"]["count"] == 0
    assert client.get("/api/control-tower?location=NOPE", headers=h).json()["stages"][5]["count"] == 0
    drill = client.get("/api/control-tower/po_sent?bucket=15%2B&location=WH-BLR", headers=h).json()
    assert drill["rows"] == [
        {
            "id": po_no,
            "label": po_no,
            "location": "WH-BLR",
            "value": "1180.00",
            "since": "2026-10-01",
            "age_days": 26,
            "bucket": "15+",
            "link": f"#/purchase-orders/{po_no}",
        }
    ]
    assert client.get("/api/control-tower/po_sent?bucket=0-2", headers=h).json()["rows"] == []
    assert client.get("/api/control-tower/nowhere", headers=h).status_code == 404
    assert client.get("/api/control-tower/po_sent?bucket=soon", headers=h).status_code == 422
    assert client.get("/api/control-tower").status_code == 401


def test_a_case_moves_from_stage_to_stage_as_it_progresses(ledger: Session, outbox: Outbox) -> None:  # noqa: F811
    people = _roles(ledger)
    slug = ledger.info["slug"]
    h = _headers(slug, people["Buyer"], "Buyer")

    def counts() -> dict[str, int]:
        return {
            s["stage"]: s["count"] for s in client.get("/api/control-tower", headers=h).json()["stages"] if s["count"]
        }

    po_no, _ = _sent(ledger, qty="10", price="1000")
    assert counts() == {"po_sent": 1}
    ledger.execute(text("update po set status = 'acknowledged'"))
    ledger.commit()
    assert counts() == {"awaiting_receipt": 1}
    _receive(ledger, po_no, "10")
    assert counts() == {"awaiting_invoice": 1}
    blocked = _invoice(ledger, po_no, "10", "1060", number="T-1")
    assert counts() == {"invoice_blocked": 1}
    from p2p.invoices import service as invoices

    invoices.cancel_invoice(ledger, blocked.no, "corrected one coming", 1)
    good = _invoice(ledger, po_no, "10", "1000", number="T-2")
    assert counts() == {"awaiting_payment": 1} and good.status == "approved"


# ---- the KPIs ---------------------------------------------------------------------------------


def test_the_key_figures_are_worked_out_from_the_cases(ledger: Session, outbox: Outbox) -> None:  # noqa: F811
    people = _roles(ledger)
    slug = ledger.info["slug"]
    po_no, _ = _goods(ledger, qty="10", price="1000")
    ledger.execute(text("update po_line set promised_date = '2026-11-05'"))  # the goods came on the 6th: a day late
    ledger.commit()
    bad = _invoice(ledger, po_no, "10", "1060", number="K-1")  # blocked at first
    from p2p.invoices import service as invoices

    invoices.cancel_invoice(ledger, bad.no, "corrected", 1)
    _invoice(ledger, po_no, "10", "1000", number="K-2")  # matches first time
    h = _headers(slug, people["Buyer"], "Buyer")

    body = client.get("/api/kpis", headers=h).json()

    assert body["spend_under_management"] == {"pct": "100.0", "contract_pct": "100.0", "total": "10000.00"}
    assert body["first_pass_match"] == {"invoices": 2, "pct": "50.0"}  # one blocked, one clean
    assert body["on_time_delivery"] == {"lines": 1, "pct": "0.0"}
    assert body["savings"] == {"rfqs": 0, "total": "0", "per_rfq": None, "rfqs_with_saving": 0}
    assert body["msme_on_time_payment"] == {"invoices": 0, "pct": None}
    assert set(body["cycle_time"]) == {
        "approval",
        "ordering",
        "po_approval",
        "dispatch",
        "delivery",
        "invoicing",
        "payment",
        "total",
    }
    assert body["cycle_time"]["total"]["cases"] == 0  # nothing has been paid yet
    assert client.get("/api/kpis").status_code == 401


def test_the_administrator_sees_every_step_and_every_waiting_approval_but_can_act_on_none(
    store: Session,
    outbox: Outbox,  # noqa: F811
) -> None:
    people = _roles(store)
    admin = users.create_user(store.info["slug"], f"admin{store.info['sfx']}@w.example", PASSWORD, "An Admin", "Admin")
    proposals.propose(
        store, __import__("p2p.platform.sod", fromlist=["user"]).user(people["Approver"]), action="supplier.set_status",
        summary="Block: Alpha Traders (S1) is active. Reason: x", payload={"supplier": "S1", "to": "blocked", "reason": "x"},
        required_permission="supplier.activate",
    )  # fmt: skip
    po_no, _ = _sent(store, qty="10", price="100")

    body = client.get("/api/workspace", headers=_headers(store.info["slug"], admin, "Admin")).json()
    sections = {s["key"]: s for s in body["sections"]}
    steps = {f["key"]: f["count"] for f in body["flow"]}

    assert sections["suppliers"]["count"] == 1  # an approval waiting for someone else is visible to the administrator
    assert sections["to_acknowledge"]["rows"][0]["id"] == po_no
    assert steps["po_sent"] == 1 and list(steps)[0] == "restock" and list(steps)[-1] == "closed"  # every step, in order
    assert "rfq_unsent" in steps
    me = client.get("/api/auth/me", headers=_headers(store.info["slug"], admin, "Admin")).json()
    assert not {"request.approve", "po.approve", "payment.approve", "grn.create", "invoice.book"} & set(
        me["permissions"]
    )
