"""Payment: what is due and held back, one live run per item, the approver the rule names with a code, the bank file,
recording with evidence, settlement by the bank statement, failure and reversal, TDS and notes. Given, when, then in
each test; the arithmetic itself is in test_payment_formulas.py."""

import re
from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_invoices import TODAY, _goods, _headers, _invoice, _row, ledger  # noqa: F401 - fixtures
from test_orders import _logins, shop  # noqa: F401 - fixtures
from test_receiving import store  # noqa: F401 - fixtures
from test_sourcing import PASSWORD, Outbox, db, outbox  # noqa: F401 - fixtures

from p2p.invoices import runs
from p2p.main import app
from p2p.platform import users

D = Decimal
client = TestClient(app)


# ---- the world these tests need ---------------------------------------------------------------


def _payment_rules(ledger_: Session) -> None:
    """The rule that names who approves a run and an advance (the tests' company has only order rules)."""
    ledger_.execute(
        text(
            "insert into approver_role (role, source, value) values ('procurement_head', 'designation', 'Head - Procurement'), "
            "('director', 'designation', 'Managing Director') on conflict (role) do nothing"
        )
    )
    ledger_.execute(
        text(
            "insert into approval_rule (rule_code, document, min_value, max_value, category_code, department_code, "
            "spend_type, approver_chain, version, effective_from) values "
            "('PAY-01', 'payment_run', 0, 999999999, 'ANY', 'ANY', 'ANY', 'procurement_head', 1, '2024-11-01'), "
            "('ADV-01', 'advance', 0, 999999999, 'ANY', 'ANY', 'ANY', 'procurement_head', 1, '2024-11-01') "
            "on conflict do nothing"
        )
    )
    ledger_.execute(
        text(
            "update item_category set default_tds_section = '194Q' where code in "
            "(select category_code from item where category_code is not null)"
        )
    )
    ledger_.commit()


def _people(ledger_: Session) -> tuple[int, int, int]:
    """Finance (proposes and records), the named approver (procurement head U5), and AP."""
    slug, sfx = ledger_.info["slug"], ledger_.info["sfx"]
    if "flow_logins" not in ledger_.info:
        ledger_.info["flow_logins"] = _logins(ledger_)
    logins = ledger_.info["flow_logins"]
    finance = users.create_user(slug, f"fin{sfx}@x.example", PASSWORD, "A Finance", "Finance")
    ap = users.create_user(slug, f"ap{sfx}@x.example", PASSWORD, "An AP", "AP")
    ledger_.execute(
        text(
            "insert into org_user (code, name, designation, department_code, manager_code, email, active) "
            "values (:c, 'A Finance', 'Finance Manager', 'FIN', 'U1', :e, true) on conflict (code) do nothing"
        ),
        {"c": f"F{sfx}", "e": f"fin{sfx}@x.example"},
    )
    ledger_.commit()
    _payment_rules(ledger_)
    return finance, logins["procurement"], ap


def _two_suppliers(ledger_: Session) -> tuple[str, str]:
    """An approved invoice of S1 due in a week: goods received against a sent order."""
    po_no, _ = _goods(ledger_, qty="10", price="100")
    inv = _invoice(ledger_, po_no, "10", "100", number="S1-1")
    assert inv.status == "approved"
    return po_no, inv.no


def _due_on(ledger_: Session, invoice_no: str, on: date) -> None:
    ledger_.execute(text("update invoice set due_date = :d, pay_by = :d where no = :n"), {"d": on, "n": invoice_no})
    ledger_.commit()


def _propose(ledger_: Session, finance: int, on: date = date(2026, 11, 6), **kw: object) -> str:
    return str(runs.propose_run(ledger_, on, finance, **kw)["run"])


def _code(outbox_: Outbox) -> str:
    for mail in reversed(outbox_.sent):
        found = re.search(r"Your one-time code is (\d{6})", str(mail.get("body", "")))
        if found:
            return found.group(1)
    raise AssertionError("no code was mailed")


def _approve(ledger_: Session, outbox_: Outbox, run_no: str, approver: int) -> dict:
    runs.send_code(ledger_, run_no, approver)
    return runs.approve_step(ledger_, run_no, approver, _code(outbox_))


def _file(ledger_: Session, outbox_: Outbox, run_no: str, finance: int, approver: int) -> str:
    _approve(ledger_, outbox_, run_no, approver)
    return runs.generate_file(ledger_, run_no, finance)


def _statement_for(ledger_: Session, run_no: str, *, without_utr: str | None = None) -> str:
    """The bank's debit lines for a run, naming each payment and its exact amount."""
    rows = ["date,utr,narration,debit,credit"]
    for p in ledger_.execute(text("select no, amount from payment where run_no = :r order by no"), {"r": run_no}).all():
        ref = "" if p[0] == without_utr else f"UTR{p[0][-8:]}"
        rows.append(f"2026-11-12,{ref},NEFT {p[0]} SUPPLIER,{p[1]},0.00")
    return "\n".join(rows) + "\n"


def _earlier_purchase(ledger_: Session, taxable: str, section: str = "194Q") -> None:
    ledger_.execute(
        text(
            "insert into invoice (no, supplier_code, supplier_invoice_no, dedupe_key, invoice_date, fy, received_on, taxable, total, "
            "status, booked_at) values ('PI-OLD', 'S1', 'OLD-1', 'OLD1', '2026-06-01', '2026-27', '2026-06-01', :t, :t, 'paid', now())"
        ),
        {"t": taxable},
    )
    ledger_.execute(
        text(
            "insert into tds_entry (invoice_no, section, section_code, base, rate, amount, booked_on, quarter, status) "
            "values ('PI-OLD', '194Q', :c, 0, 0.1, 0, '2026-06-01', '2026-27 Q1', 'confirmed')"
        ),
        {"c": section},
    )
    ledger_.commit()


# ---- TDS at booking ---------------------------------------------------------------------------


def test_no_agent_tool_writes_a_payment_run_tds_advance_or_note(ledger: Session) -> None:
    # given every product tool registry (the assistant, planning and the invoice exceptions agent), when every tool
    # that writes is looked at, then none of them touches a payment, a run, a TDS row, an advance or a note
    from p2p.agents import assistant, invoice_exceptions, planning

    registries = (
        assistant.build_registry(ledger, 1),
        planning.build_registry(ledger),
        invoice_exceptions.build_registry(ledger),
    )
    sensitive = ("payment", "payment_run", "tds", "advance", "credit note", "debit note", "note_")
    for registry in registries:
        for name in registry.names():
            tool = registry.get(name)
            assert tool is not None
            if tool.writes:
                said = f"{tool.name} {tool.description}".lower()
                assert not any(word in said for word in sensitive), f"{tool.name} may write {said}"


def test_only_a_payment_permission_reads_what_is_due_and_the_named_person_approves_over_the_api(
    ledger: Session, outbox: Outbox
) -> None:  # noqa: F811
    # given Finance and a named approver, when what is due is read, then held-back rows come with reasons; when a run
    # is planned, then it waits for the named person; when they take a code and approve it, then it is approved (Y6)
    finance, approver, ap = _people(ledger)
    _, inv_no = _two_suppliers(ledger)
    _due_on(ledger, inv_no, date(2026, 11, 12))
    slug = ledger.info["slug"]
    h_fin, h_ap = _headers(slug, finance, "Finance"), _headers(slug, ap, "AP")
    h_app = _headers(slug, approver, "Approver")
    nobody = users.create_user(slug, f"nobody{ledger.info['sfx']}@x.example", PASSWORD, "A Requester", "Requester")
    h_none = _headers(slug, nobody, "Requester")

    due = client.get("/api/payment-runs/due?on=2026-11-06&window=7", headers=h_fin)
    assert due.status_code == 200
    assert [(p["invoice"], p["net"]) for p in due.json()["due"]] == [(inv_no, "1180.00")]
    assert client.get("/api/payment-runs/due", headers=h_none).status_code == 403
    assert client.post("/api/payment-runs", json={"run_date": "2026-11-06"}, headers=h_ap).status_code == 403
    planned = client.post("/api/payment-runs", json={"run_date": "2026-11-06", "window_days": 7}, headers=h_fin)
    assert planned.status_code == 200
    run_no = planned.json()["run"]
    page = client.get(f"/api/payment-runs/{run_no}", headers=h_fin).json()
    assert page["status"] == "proposed" and page["total"] == "1180.00" and page["needs_code"] is True
    assert page["can_approve"] is False
    assert client.get(f"/api/payment-runs/{run_no}", headers=h_app).json()["can_approve"] is True
    proposal_id = ledger.execute(text("select proposal_id from payment_run where no = :r"), {"r": run_no}).scalar_one()
    assert client.post(f"/api/admin/proposals/{proposal_id}/approve", json={}, headers=h_app).status_code == 409

    assert client.post(f"/api/payment-runs/{run_no}/code", headers=h_app).status_code == 200
    code = _code(outbox)
    approved = client.post(f"/api/payment-runs/{run_no}/approve", json={"code": code}, headers=h_app)
    assert approved.status_code == 200 and approved.json()["status"] == "approved"
    assert client.post(f"/api/payment-runs/{run_no}/file", headers=h_fin).status_code == 200
    assert client.get(f"/api/payment-runs/{run_no}", headers=h_fin).json()["has_file"] is True
