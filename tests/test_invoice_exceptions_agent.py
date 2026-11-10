"""The invoice exceptions agent: the cause of a block worked out from the data, the model's wording used only when it is
checked, the tools that only read, and the endpoint. The model is a scripted fake."""

import json
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_ai_steps import Scripted
from test_orders import _headers, shop  # noqa: F401 - fixtures
from test_receiving import _receive, _sent, store  # noqa: F401 - fixtures
from test_sourcing import PASSWORD, Outbox, db, outbox  # noqa: F401 - fixtures

from p2p.agents import invoice_exceptions as agent
from p2p.ai_steps.adapters import RawResponse
from p2p.ai_steps.gateway import Gateway
from p2p.invoices import service as invoices
from p2p.main import app
from p2p.platform import ui_api, users

D = Decimal
client = TestClient(app)
TODAY = date(2026, 11, 6)
SUPPLIER_GSTIN, OUR_GSTIN = "27AAAAA0000A1Z5", "29BBBBB0000B1Z5"


def decision(**kw: Any) -> str:
    return json.dumps({"confidence": 0.9, **kw})


def gateway(*turns: str) -> tuple[Gateway, Scripted]:
    provider = Scripted({"m": list(turns)})
    return Gateway(
        {"fake": provider},
        {"agent_invoice_exceptions": [("fake", "m")], "default": [("fake", "m")]},
        sleep=lambda s: None,
    ), provider


@pytest.fixture
def books(store: Session) -> Session:  # noqa: F811
    """The receiving world plus an invoice number series and the company's match tolerances."""
    store.execute(
        text(
            "insert into number_series (document_type, prefix, scope, format, reset) "
            "values ('invoice_booking', 'PI', 'company', '{prefix}-{fy4}-{seq:06d}', 'fy')"
        )
    )
    store.execute(
        text(
            "insert into tolerance (key, value, category_code) values ('price_pct', '2', 'ANY'), ('price_abs', '500', 'ANY'), "
            "('qty_pct', '0', 'ANY')"
        )
    )
    store.commit()
    return store


def _invoice(
    world: Session,
    po_no: str,
    qty: str,
    price: str = "100",
    *,
    buyer: str = OUR_GSTIN,
    irn: str | None = None,
    tax: bool = True,
    number: str = "INV/77",
) -> str:
    line = invoices.InvLine(po_no, 1, D(qty), D(price), gst_rate=D(18))
    data = invoices.InvoiceIn(
        supplier_code="S1", supplier_invoice_no=number, invoice_date=TODAY, lines=[line], supplier_gstin=SUPPLIER_GSTIN,
        buyer_gstin=buyer, irn=irn,
    )  # fmt: skip
    if not tax:
        line.cgst = line.sgst = line.igst = D(0)
        data.cgst = data.sgst = data.igst = D(0)
    return invoices.enter_invoice(world, data, 1, as_of=TODAY).no


def _blocked(world: Session, kind: str) -> str:
    po_no, world.info["lg"] = _sent(world, logins=world.info.get("lg"))  # 10 x item A at 100, from S1
    if kind == "price":
        _receive(world, po_no, "10")
        return _invoice(world, po_no, "10", "180")  # 80 more per unit: 800 on the line is outside both tolerances
    if kind == "qty":
        _receive(world, po_no, "6")
        return _invoice(world, po_no, "10")
    if kind == "rejected":
        _receive(world, po_no, "10", rejected="4", reason="Cracked housings")
        return _invoice(world, po_no, "10")
    if kind == "notreceived":
        return _invoice(world, po_no, "10")
    if kind == "gstin":
        _receive(world, po_no, "10")
        return _invoice(world, po_no, "10", buyer="29ZZZZZ9999Z1Z9")
    if kind == "composition":
        world.execute(text("update supplier set gst_reg_type = 'composition' where code = 'S1'"))
        world.commit()
        _receive(world, po_no, "10")
        data = invoices.InvoiceIn(
            supplier_code="S1", supplier_invoice_no="BOS/1", invoice_date=TODAY, supplier_gstin=SUPPLIER_GSTIN, buyer_gstin=OUR_GSTIN,
            lines=[invoices.InvLine(po_no, 1, D(10), D(100), taxable=D(1000), gst_rate=D(18), cgst=D(0), sgst=D(0), igst=D(180))],
            taxable=D(1000), igst=D(180), cgst=D(0), sgst=D(0),
        )  # fmt: skip
        return invoices.enter_invoice(world, data, 1, as_of=TODAY).no
    if kind == "einvoice":
        world.execute(
            text("update supplier set e_invoice_applicable = true, gst_reg_type = 'regular' where code = 'S1'")
        )
        world.commit()
        _receive(world, po_no, "10")
        return _invoice(world, po_no, "10", irn=None)
    raise AssertionError(kind)


CASES = [
    ("price", "price_mismatch", "corrected_invoice", ["180", "100", "80"]),
    ("qty", "qty_mismatch", "wait_for_receipt", ["10", "6"]),
    ("rejected", "rejected_qty_invoiced", "credit_note", ["10", "4", "6"]),
    ("notreceived", "goods_not_received", "wait_for_receipt", ["10"]),
    ("gstin", "wrong_gstin", "corrected_invoice", ["29ZZZZZ9999Z1Z9", OUR_GSTIN]),
    ("composition", "composition_tax", "corrected_invoice", []),
    ("einvoice", "e_invoice_missing", "corrected_invoice", ["IRN"]),
]


# ---- the rules find the cause ------------------------------------------------------------------


@pytest.mark.parametrize(("kind", "cause", "fix", "numbers"), CASES)
def test_each_kind_of_block_is_diagnosed_with_its_cause_its_fix_and_a_query_to_the_supplier(
    books: Session,
    kind: str,
    cause: str,
    fix: str,
    numbers: list[str],  # noqa: F811
) -> None:
    no = _blocked(books, kind)
    assert books.execute(text("select status from invoice where no = :n"), {"n": no}).scalar_one() == "blocked"

    d = agent.diagnose(books, no)

    assert d is not None and (d.cause, d.fix) == (cause, fix)
    for n in numbers:
        assert n in d.explanation or n in d.vendor_body, f"{n} should be in the explanation or the query"
    assert "INV/77" in d.vendor_subject or kind == "composition"
    assert "Dear Contact S1," in d.vendor_body and no in d.vendor_body and "Regards" in d.vendor_body
    assert d.source == "rules"


def test_an_invoice_that_is_not_blocked_has_nothing_to_diagnose(books: Session) -> None:  # noqa: F811
    po_no, _ = _sent(books)
    _receive(books, po_no, "10")
    no = _invoice(books, po_no, "10")

    assert books.execute(text("select status from invoice where no = :n"), {"n": no}).scalar_one() == "approved"
    assert agent.diagnose(books, no) is None and agent.diagnose(books, "NOPE") is None


def test_what_other_invoices_already_took_decides_between_waiting_and_a_corrected_invoice(books: Session) -> None:  # noqa: F811
    po_no, _ = _sent(books)
    _receive(books, po_no, "10")
    first = _invoice(books, po_no, "8", number="INV/1")  # takes 8 of the 10 received
    assert books.execute(text("select status from invoice where no = :n"), {"n": first}).scalar_one() == "approved"

    second = _invoice(
        books, po_no, "5", number="INV/2"
    )  # only 2 are left to invoice, and 5 is more than the order has left

    d = agent.diagnose(books, second)
    assert d is not None and d.cause == "qty_mismatch" and d.fix == "corrected_invoice"


# ---- the tools only read -------------------------------------------------------------------------


def test_every_tool_is_read_only_and_running_them_changes_nothing(books: Session) -> None:  # noqa: F811
    no = _blocked(books, "rejected")
    registry = agent.build_registry(books)
    assert all(not registry.get(n).writes for n in registry.names())  # type: ignore[union-attr]
    tables = [
        "invoice",
        "invoice_line",
        "match_result",
        "po",
        "po_line",
        "goods_receipt",
        "gr_line",
        "proposal",
        "audit_event",
        "stock_movement",
    ]
    before = {t: books.execute(text(f"select count(*) from {t}")).scalar_one() for t in tables}  # noqa: S608
    actor = agent.AGENT

    registry.get("get_invoice").fn(books, agent.InvoiceNo(no=no), actor)  # type: ignore[union-attr]
    registry.get("get_order").fn(books, agent.OrderNo(no="PO-BLR-2627-00001"), actor)  # type: ignore[union-attr]
    got = registry.get("get_receipts").fn(books, agent.OrderNo(no="PO-BLR-2627-00001"), actor)  # type: ignore[union-attr]
    registry.get("invoices_on_order").fn(books, agent.OrderNo(no="PO-BLR-2627-00001"), actor)  # type: ignore[union-attr]
    registry.get("supplier_emails").fn(books, agent.SupplierCode(code="S1"), actor)  # type: ignore[union-attr]
    registry.get("supplier_profile").fn(books, agent.SupplierCode(code="S1"), actor)  # type: ignore[union-attr]
    books.rollback()

    assert got["receipts"][0]["rejected"] == "4"
    assert {t: books.execute(text(f"select count(*) from {t}")).scalar_one() for t in tables} == before  # noqa: S608
    assert registry.get("get_invoice").fn(books, agent.InvoiceNo(no="NOPE"), actor) == {
        "error": "no invoice called NOPE"
    }  # type: ignore[union-attr]


# ---- the model's explanation is used only when it is checked -------------------------------------

GOOD = (
    "Invoice INV/77 bills 10 pieces of Bearing on PO-BLR-2627-00001, but the goods receipt shows 10 received and 4 rejected, "
    "so only 6 were accepted. The supplier has billed the rejected pieces.\nCause: rejected_qty_invoiced"
)


def _ask(books: Session, no: str, gw: Gateway) -> agent.Diagnosis:
    got = agent.explain(books, books.info["slug"], 1, no, gw)
    assert got is not None
    return got


def test_the_models_explanation_is_used_when_it_names_the_same_cause_and_every_number_is_from_a_tool(
    books: Session,
) -> None:  # noqa: F811
    no = _blocked(books, "rejected")
    gw, provider = gateway(
        decision(tool="get_invoice", args={"no": no}),
        decision(tool="get_receipts", args={"no": "PO-BLR-2627-00001"}),
        decision(final=GOOD),
    )

    got = _ask(books, no, gw)

    assert (
        got.source == "agent"
        and got.explanation.startswith("Invoice INV/77 bills 10 pieces")
        and "Cause:" not in got.explanation
    )
    assert (got.cause, got.fix) == ("rejected_qty_invoiced", "credit_note")
    assert len(provider.calls) == 3
    row = books.execute(text("select source, cause, fix from invoice_diagnosis")).one()
    assert tuple(row) == ("agent", "rejected_qty_invoiced", "credit_note")


def test_an_explanation_that_names_a_different_cause_is_replaced_by_the_rules(books: Session) -> None:  # noqa: F811
    no = _blocked(books, "rejected")
    gw, _ = gateway(
        decision(tool="get_invoice", args={"no": no}),
        decision(final="The price on the invoice is too high.\nCause: price_mismatch"),
    )

    got = _ask(books, no, gw)

    assert got.source == "rules" and got.cause == "rejected_qty_invoiced" and "rejected" in got.explanation
    event = books.execute(text("select after from audit_event where action = 'invoice.explain_rejected'")).scalar_one()
    assert event["said"] == "price_mismatch" and event["rules"] == "rejected_qty_invoiced"


def test_an_explanation_with_a_number_no_tool_returned_is_not_used(books: Session) -> None:  # noqa: F811
    no = _blocked(books, "rejected")
    gw, _ = gateway(
        decision(tool="get_receipts", args={"no": "PO-BLR-2627-00001"}),
        decision(final="Rejected 37 pieces of the 10 received.\nCause: rejected_qty_invoiced"),
    )

    got = _ask(books, no, gw)

    assert got.source == "rules" and got.grounded is False and "37" not in got.explanation


def test_an_explanation_without_the_cause_line_is_not_used(books: Session) -> None:  # noqa: F811
    no = _blocked(books, "price")
    gw, _ = gateway(decision(tool="get_invoice", args={"no": no}), decision(final="It looks wrong somehow."))

    assert _ask(books, no, gw).source == "rules"


def test_when_the_model_cannot_be_reached_the_rules_still_give_the_answer(books: Session) -> None:  # noqa: F811
    no = _blocked(books, "qty")

    class Down:
        def chat(self, *a: Any, **k: Any) -> RawResponse:
            raise OSError("no network")

    gw = Gateway(
        {"fake": Down()},
        {"agent_invoice_exceptions": [("fake", "m")], "default": [("fake", "m")]},
        sleep=lambda s: None,
    )

    got = _ask(books, no, gw)

    assert got.source == "rules" and got.cause == "qty_mismatch" and "received and accepted" in got.explanation


def test_looking_into_an_invoice_never_releases_or_changes_it(books: Session) -> None:  # noqa: F811
    no = _blocked(books, "price")
    before = books.execute(
        text("select status, payment_block, block_reason, due_date, tds_amount from invoice where no = :n"), {"n": no}
    ).one()
    gw, _ = gateway(
        decision(tool="get_invoice", args={"no": no}),
        decision(final="Please just approve it now.\nCause: price_mismatch"),  # a model that tries to push it through
    )

    _ask(books, no, gw)

    books.expire_all()
    after = books.execute(
        text("select status, payment_block, block_reason, due_date, tds_amount from invoice where no = :n"), {"n": no}
    ).one()
    assert tuple(after) == tuple(before) and after.status == "blocked" and after.payment_block == "match"
    assert books.execute(text("select count(*) from proposal")).scalar_one() == 0
    assert (
        books.execute(text("select count(*) from audit_event where action = 'invoice.release_block'")).scalar_one() == 0
    )
    assert books.execute(text("select count(*) from audit_event where action = 'invoice.diagnose'")).scalar_one() == 1


# ---- the endpoint ------------------------------------------------------------------------------


def test_the_endpoint_explains_a_blocked_invoice_for_accounts_and_nobody_else(books: Session, outbox: Outbox) -> None:  # noqa: F811
    no = _blocked(books, "qty")
    slug, sfx = books.info["slug"], books.info["sfx"]
    ap = users.create_user(slug, f"ap{sfx}@x.example", PASSWORD, "Accounts", "AP")
    buyer = users.create_user(slug, f"buyer2{sfx}@x.example", PASSWORD, "A Buyer", "Buyer")
    po2, _ = _sent(books, item="D", qty="5", price="40", cat="C2", logins=books.info["lg"])
    _receive(books, po2, "5")
    ok_invoice = _invoice(books, po2, "5", "40", number="INV/OK")  # matches: 5 received, 5 invoiced at the order price
    books.commit()  # the agent runtime creates an index, which waits for any open transaction in this database
    gw, _ = gateway(decision(tool="get_invoice", args={"no": no}), decision(final=_QTY.format(no=no)))
    ui_api.use_gateway(gw)
    try:
        assert client.post(f"/api/invoices/{no}/explain").status_code == 401
        assert client.post(f"/api/invoices/{no}/explain", headers=_headers(slug, buyer, "Buyer")).status_code == 403
        assert client.post(f"/api/invoices/{ok_invoice}/explain", headers=_headers(slug, ap, "AP")).status_code == 409
        got = client.post(f"/api/invoices/{no}/explain", headers=_headers(slug, ap, "AP"))
    finally:
        ui_api.use_gateway(None)

    assert got.status_code == 200
    body = got.json()
    assert (body["cause"], body["fix"], body["source"]) == ("qty_mismatch", "wait_for_receipt", "agent")
    assert "Dear Contact S1," in body["vendor_email"]["body"]
    again = client.get(f"/api/invoices/{no}/diagnosis", headers=_headers(slug, buyer, "Buyer")).json()["diagnosis"]
    assert again["cause"] == "qty_mismatch" and again["vendor_body"] == body["vendor_email"]["body"]
    assert client.get("/api/invoices/NOPE/diagnosis", headers=_headers(slug, ap, "AP")).status_code == 404
    assert client.get(f"/api/invoices/{ok_invoice}/diagnosis", headers=_headers(slug, ap, "AP")).json() == {
        "diagnosis": None
    }


_QTY = "Invoice {no} bills 10 pieces but only 6 were received and accepted on PO-BLR-2627-00001.\nCause: qty_mismatch"
