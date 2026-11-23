"""The supplier acknowledges the exact version (block 8 K3, 8.3, 8.5.4): a link of an older version says which version
is current and can have it emailed to the contact (never shown on the page), the new version is accepted with link and
code on its own row, one acknowledgement per version, the buyer's record needs the version, a date per line and a note,
and the migration fills what rows made before lacked."""

import importlib.util
from datetime import date
from decimal import Decimal
from pathlib import Path

from confirmation_support import audits, count, link_of, send_p, token_in
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session
from supplier_answer_support import answer, ask_code
from test_orders import _headers, shop  # noqa: F401 - fixtures
from test_sourcing import Outbox, db, outbox  # noqa: F401 - fixtures

import p2p
from p2p.formulas.calendar import pin_today
from p2p.main import app
from p2p.orders import changes, dispatch, versions
from p2p.platform import ratelimit, sod

D = Decimal
client = TestClient(app)


def _revise_line_3(shop: Session, logins: dict[str, int], po_no: str) -> dict[str, object]:
    pin_today(date(2026, 11, 25))
    return changes.revise(
        shop, shop.info["slug"], po_no, logins["buyer"], "plan cut", [changes.LineChange(3, qty=D(140))]
    )


def test_an_old_versions_link_names_the_current_version_and_can_have_it_emailed(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    # given P sent on Mon 23 Nov (link L1), and line 3 cut to 140 on Wed 25 Nov: version 2, the approval carried
    ratelimit.reset()
    po_no, logins, l1 = send_p(shop, outbox)
    made = _revise_line_3(shop, logins, po_no)
    assert made["version"] == 2 and made["reapproval"] is False and made["supplier_told"] is True
    shop.rollback()
    # version 2's figures: 48,000 + 44,000 + 140 x 180 = 25,200; IGST 18% (the supplier is in another state)
    assert shop.execute(text("select taxable, igst, total from po")).one() == (
        D("117200.00"), D("21096.00"), D("138296.00"),
    )  # fmt: skip
    l2 = token_in(outbox.sent[-1]["body"])
    assert link_of(l2).id != link_of(l1).id

    # when the old link is opened
    page = client.get(f"/supplier/{l1}")

    # then it says which version is current, when it was revised and emailed and to whom (masked), and what changed
    assert page.status_code == 409
    words = (
        f"Order {po_no} is now version 2 (revised on 25 Nov 2026). This link was for version 1 and no longer works. "
        "Version 2 was emailed to s****@supplier.example on 25 Nov 2026: please use that email."
    )
    assert words in page.text and "What changed" in page.text and "line 3" in page.text
    assert "has changed since this link was sent" in page.text  # block 7's words stay
    assert "Email me a code" not in page.text  # no code is asked for on a stale link
    sent = len(outbox.sent)
    assert ask_code(client, l1).status_code == 409 and len(outbox.sent) == sent

    # and "Email me the current version" mails a new link for version 2 to the contact, once per ten minutes
    current = client.post(f"/supplier/{l1}/current")
    assert current.status_code == 200 and "We have emailed version 2 to s****@supplier.example." in current.text
    l3 = token_in(outbox.sent[-1]["body"])
    assert l3 not in current.text and outbox.sent[-1]["to"] == "s1@supplier.example"
    assert link_of(l3).id not in (link_of(l1).id, link_of(l2).id)
    assert client.post(f"/supplier/{l1}/current").status_code == 429
    (resend,) = audits(shop, "po.link_resend")
    assert resend["version"] == 2 and resend["from_link"] == link_of(l1).id
    assert client.post(f"/supplier/{l2}/current").status_code == 409  # a current link answers instead


def test_the_new_version_is_acknowledged_with_link_and_code_on_its_own_row(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, logins, _ = send_p(shop, outbox)
    _revise_line_3(shop, logins, po_no)
    l2 = token_in(outbox.sent[-1]["body"])
    pin_today(date(2026, 11, 26))

    done = answer(client, outbox.sent, l2, {"action": "accept"})

    assert done.status_code == 200
    shop.rollback()
    assert shop.execute(text("select acknowledged_version, status from po")).one() == (2, "acknowledged")
    rows = shop.execute(
        text(
            "select version, ack_via, ack_address, ack_code_id is not null, document_id from po_version order by version"
        )
    ).all()
    assert [r[:4] for r in rows] == [(1, None, None, False), (2, "link", "s1@supplier.example", True)]
    assert rows[1][4] is not None  # the stored document of version 2 is the one accepted
    listed = {v["version"]: v for v in versions.listing(shop, po_no)}
    assert listed[2]["ack_with_code"] is True and listed[2]["ack_address"] == "s1@supplier.example"
    assert listed[2]["ack_lines"] == {"1": "2026-12-07", "2": "2026-12-07", "3": "2026-12-14"}
    assert listed[1]["acknowledged_at"] is None
    # three commitments, one per line, of version 2, from the link
    assert count(shop, "select count(*) from po_commitment where po_version = 2 and source = 'ack_link'") == 3
    # one acknowledgement per version: a second record of version 2 is refused
    try:
        dispatch.acknowledge(shop, po_no, sod.user(logins["buyer"]), version=2, via="app", note="phone")
    except dispatch.DispatchError as exc:
        assert "only a sent order" in str(exc)
    else:
        raise AssertionError("a version was acknowledged twice")
    assert len(audits(shop, "po.acknowledge")) == 1


def test_the_buyers_record_needs_the_current_version_a_note_and_good_dates(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, logins, _ = send_p(shop, outbox)
    _revise_line_3(shop, logins, po_no)
    buyer = _headers(shop.info["slug"], logins["buyer"], "Buyer")
    url = f"/api/purchase-orders/{po_no}/acknowledge"

    no_note = client.post(url, json={"version": 2}, headers=buyer)
    old = client.post(url, json={"version": 1, "note": "phone"}, headers=buyer)
    late = client.post(url, json={"version": 2, "note": "phone", "lines": {"3": "2026-12-18"}}, headers=buyer)
    unknown = client.post(url, json={"version": 2, "note": "phone", "lines": {"9": "2026-12-08"}}, headers=buyer)
    store = client.post(
        url, json={"version": 2, "note": "phone"}, headers=_headers(shop.info["slug"], logins["keeper"], "Store")
    )

    assert no_note.status_code == 422 and "note" in no_note.json()["detail"]
    assert old.status_code == 409 and "it is now version 2" in old.json()["detail"]
    assert late.status_code == 422 and "line 3: 18 Dec 2026 is 4 days after the date ordered" in late.json()["detail"]
    assert unknown.status_code == 422 and store.status_code == 403
    assert audits(shop, "po.acknowledge") == []

    done = client.post(
        url,
        json={"version": 2, "note": "Mr Rao confirmed by phone", "lines": {"2": "2026-12-09", "3": "2026-12-13"}},
        headers=buyer,
    )

    # the buyer records what the supplier said: a day the place is closed is recorded and said back (not refused)
    assert done.status_code == 200
    assert done.json()["warnings"] == [
        "line 2 (Seal): 09 Dec 2026, 2 days later than ordered (07 Dec 2026)",
        "line 3 (Carton): 13 Dec 2026 is not a working day at Bengaluru store",
    ]
    shop.rollback()
    assert [r[0] for r in shop.execute(text("select promised_date from po_line order by line_no"))] == [
        date(2026, 12, 7), date(2026, 12, 9), date(2026, 12, 13),
    ]  # fmt: skip
    assert shop.execute(text("select ack_via, ack_note from po_version where version = 2")).one() == (
        "app", "Mr Rao confirmed by phone",
    )  # fmt: skip
    page = client.get(f"/api/purchase-orders/{po_no}", headers=buyer).json()
    assert [(ln["due"], ln["due_source"]) for ln in page["lines"]] == [
        ("2026-12-07", "promised"), ("2026-12-09", "promised"), ("2026-12-13", "promised"),
    ]  # fmt: skip


def test_the_migration_fills_the_address_of_old_links_and_the_version_of_old_changes(
    shop: Session,
    outbox: Outbox,  # noqa: F811
) -> None:
    po_no, logins, token = send_p(shop, outbox)
    answer(client, outbox.sent, token, {"action": "change", "qty_1": "90"})
    shop.execute(text("update supplier_link set sent_to = null"))
    shop.execute(text("update po_change set po_version = null"))
    shop.execute(text("insert into supplier_chase (po_no, kind, how) values (:p, 'ack', 'email')"), {"p": po_no})
    shop.commit()
    path = Path(p2p.__file__).parent / "platform" / "migrations" / "tenant" / "versions"
    spec = importlib.util.spec_from_file_location(
        "confirmation_migration", path / "0150_supplier_confirmation_versions.py"
    )
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    for _ in range(2):  # twice: nothing doubles or moves
        for statement in migration.BACKFILL:
            shop.execute(text(statement))
        shop.commit()

    assert {r[0] for r in shop.execute(text("select sent_to from supplier_link"))} == {"s1@supplier.example"}
    assert shop.execute(text("select po_version from po_change")).scalar_one() == 1
    assert shop.execute(text("select po_version from supplier_chase")).scalar_one() == 1
