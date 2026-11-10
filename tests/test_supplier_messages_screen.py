"""Supplier messages on the order and RFQ pages, and the buyer's My work list: only what a person has to read, only for a buyer."""

import itertools

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_orders import _sent_rfq, shop  # noqa: F401 - fixture
from test_receiving import _sent, store  # noqa: F401 - fixture
from test_sourcing import PASSWORD, Outbox, _headers, db, outbox  # noqa: F401 - fixtures

from p2p.main import app
from p2p.platform import tasks, users

client = TestClient(app)
_n = itertools.count(1)


def _people(db_: Session) -> tuple[int, int, int]:
    slug, sfx = db_.info["slug"], db_.info["sfx"]
    buyer = users.create_user(slug, f"b{sfx}@x.example", PASSWORD, "A Buyer", "Buyer")
    other = users.create_user(slug, f"r{sfx}@x.example", PASSWORD, "A Requester", "Requester")
    approver = users.create_user(slug, f"o{sfx}@x.example", PASSWORD, "An Approver", "Approver")
    return buyer, other, approver


def _message(
    db_: Session,
    linked_type: str,
    ref: str,
    status: str,
    subject: str,
    *,
    kind: str = "delivery_date",
    task_id: int | None = None,
    reply: str | None = None,
) -> int:
    n = next(_n)
    email_id = db_.execute(
        text(
            "insert into email_message (message_id, direction, from_addr, subject, linked_type, linked_id) "
            "values (:m, 'in', 's1@supplier.example', :s, :t, :r) returning id"
        ),
        {"m": f"<m{db_.info['sfx']}{n}@x.example>", "s": subject, "t": linked_type, "r": ref},
    ).scalar_one()
    return int(
        db_.execute(
            text(
                "insert into supplier_message (email_id, linked_type, linked_ref, kind, status, action, task_id, reply_draft, confidence) "
                "values (:e, :t, :r, :k, :st, 'noted the new date', :task, :d, 0.9) returning id"
            ),
            {"e": email_id, "t": linked_type, "r": ref, "k": kind, "st": status, "task": task_id, "d": reply},
        ).scalar_one()
    )


def test_the_order_page_lists_what_the_supplier_wrote_and_only_for_that_order(store: Session) -> None:  # noqa: F811
    po_no, logins = _sent(store)
    other_po, _ = _sent(store, item="B", qty="5", logins=logins)
    buyer, requester, _ = _people(store)
    slug = store.info["slug"]
    _message(store, "po", po_no, "needs_person", "Re: delay", reply="Thanks, please confirm the new date.")
    _message(store, "po", other_po, "applied", "Re: other order")
    _message(store, "rfq", po_no, "applied", "an RFQ that shares the number")  # not an order's mail
    store.commit()

    mine = client.get(f"/api/orders/{po_no}/supplier-messages", headers=_headers(slug, buyer, "Buyer")).json()

    assert [(m["subject"], m["status"], m["reply_draft"]) for m in mine["messages"]] == [
        ("Re: delay", "needs_person", "Thanks, please confirm the new date.")
    ]
    assert (
        client.get(f"/api/orders/{po_no}/supplier-messages", headers=_headers(slug, requester, "Requester")).status_code
        == 403
    )
    assert client.get("/api/orders/NOPE/supplier-messages", headers=_headers(slug, buyer, "Buyer")).status_code == 404
    assert client.get(f"/api/orders/{po_no}/supplier-messages").status_code == 401


def test_the_rfq_page_lists_what_suppliers_wrote_about_the_quotation_request(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    _, rfq = _sent_rfq(shop, outbox)
    buyer, requester, _ = _people(shop)
    slug = shop.info["slug"]
    _message(shop, "rfq", rfq, "needs_person", "Question about the quantity", kind="question")
    _message(shop, "po", rfq, "applied", "a PO that shares the number")
    shop.commit()

    out = client.get(f"/api/rfqs/{rfq}/supplier-messages", headers=_headers(slug, buyer, "Buyer")).json()

    assert [(m["subject"], m["kind"]) for m in out["messages"]] == [("Question about the quantity", "question")]
    assert out["commitments"] == [] and out["chasers"] == []
    assert (
        client.get(f"/api/rfqs/{rfq}/supplier-messages", headers=_headers(slug, requester, "Requester")).status_code
        == 403
    )
    assert client.get("/api/rfqs/NOPE/supplier-messages", headers=_headers(slug, buyer, "Buyer")).status_code == 404


def test_the_buyers_my_work_lists_supplier_messages_that_wait_for_a_person_and_links_to_the_right_page(
    store: Session,  # noqa: F811
) -> None:
    po_no, _ = _sent(store)
    buyer, _, approver = _people(store)
    slug = store.info["slug"]
    open_task = tasks.create_task(store, slug, kind="supplier_message", title="Read", assignee=buyer, sla_hours=24)
    done_task = tasks.create_task(store, slug, kind="supplier_message", title="Read", assignee=buyer, sla_hours=24)
    tasks.complete_task(store, done_task, buyer)
    _message(store, "po", po_no, "task", "Re: delivery", task_id=open_task)
    _message(store, "po", po_no, "needs_person", "Re: price")
    _message(store, "rfq", "RFQ-1", "needs_person", "Re: quotation")
    _message(store, "po", po_no, "task", "Already read", task_id=done_task)  # its task is done: nothing to do
    _message(store, "po", po_no, "applied", "Handled by the system")
    store.commit()

    work = client.get("/api/workspace", headers=_headers(slug, buyer, "Buyer")).json()
    section = next(s for s in work["sections"] if s["key"] == "supplier_messages")

    assert section["title"] == "Supplier messages to read" and section["count"] == 3
    assert sorted((r["label"], r["link"]) for r in section["rows"]) == sorted(
        [
            (f"{po_no}: Re: delivery", f"#/purchase-orders/{po_no}"),
            (f"{po_no}: Re: price", f"#/purchase-orders/{po_no}"),
            ("RFQ-1: Re: quotation", "#/rfqs/RFQ-1"),
        ]
    )
    approver_work = client.get("/api/workspace", headers=_headers(slug, approver, "Approver")).json()
    assert all(s["key"] != "supplier_messages" for s in approver_work["sections"])  # not for an approver


def test_a_message_with_nothing_to_read_does_not_appear_in_my_work(store: Session) -> None:  # noqa: F811
    po_no, _ = _sent(store)
    buyer, _, _ = _people(store)
    _message(store, "po", po_no, "noted", "Thanks")
    _message(store, "po", po_no, "ignored", "Out of office")
    store.commit()

    work = client.get("/api/workspace", headers=_headers(store.info["slug"], buyer, "Buyer")).json()

    assert all(s["key"] != "supplier_messages" for s in work["sections"])


def test_chasing_now_is_for_the_buyer(store: Session) -> None:  # noqa: F811
    buyer, requester, _ = _people(store)
    slug = store.info["slug"]

    assert client.post("/api/supplier-agent/chase", headers=_headers(slug, requester, "Requester")).status_code == 403
    done = client.post("/api/supplier-agent/chase", headers=_headers(slug, buyer, "Buyer"))
    assert done.status_code == 200 and set(done.json()) == {"emailed", "tasks", "skipped"}
