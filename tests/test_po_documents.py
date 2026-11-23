"""A stored document per issued version (block 7, R6-R10, R24, R25): the document of a version is rendered once when it is
mailed, stored, and that stored file is the attachment; a reprint is those bytes whatever changed since; a version not
mailed has no document and its PDF is a draft; the versions are listed and any two compared; the supplier's page serves
the stored document of its link's version; another company sees none of it."""

import uuid
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from po_versions_support import attachment, audits, buyer, order, revise, rows, sent_order, sha, versions
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_order_changes import _pdf_text
from test_orders import _headers, shop  # noqa: F401 - fixtures
from test_sourcing import Outbox, db, outbox  # noqa: F401 - fixtures
from test_supplier_confirmation import _token

from p2p.main import app
from p2p.orders import changes, document, supplier_links, workflow
from p2p.platform import routing, tenants

D = Decimal
client = TestClient(app)


def _pdf(shop: Session, logins: dict[str, int], path: str) -> bytes:
    got = client.get(path, headers=buyer(shop, logins))
    assert got.status_code == 200, got.text
    return got.content


def test_a_sent_version_is_stored_once_and_the_mail_attaches_that_very_file(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, logins, _ = sent_order(shop, outbox)

    (v1,) = versions(shop, po_no)
    (doc,) = rows(shop, "select * from document where id = :i", i=v1["document_id"])
    assert (doc["doc_type"], doc["linked_type"], doc["linked_id"], doc["filename"]) == (
        "po_version", "po", po_no, f"{po_no}.pdf",
    )  # fmt: skip
    assert sha(attachment(outbox)) == doc["sha256"]  # what the supplier got is what is kept
    (mail,) = rows(shop, "select attachment_ids from email_message where linked_id = :n", n=po_no)
    assert mail["attachment_ids"] == [doc["id"]]  # attached by id: no second copy
    assert rows(shop, "select 1 from document where doc_type = 'email_attachment_out'") == []
    assert (v1["sent_to"], v1["sent_at"] is not None) == ("s1@supplier.example", True)
    assert audits(shop, "po.version_issued", po_no) == [{"version": 1, "document": doc["id"], "sha256": doc["sha256"]}]
    issued = audits(shop, "supplier_link.issue", po_no)[-1]
    assert (issued["purpose"], issued["version"]) == ("respond", 1)


def test_a_reprint_is_the_stored_bytes_whatever_changed_since(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, logins, _ = sent_order(shop, outbox)
    mailed = attachment(outbox)

    # the company changes its terms template and its name after the order went out
    shop.execute(
        text("insert into org_setting (key, value) values ('po_terms_template', 'New terms for {{ po_no }}.')")
    )
    shop.execute(text("update company set legal_name = 'Renamed Private Limited'"))
    shop.commit()

    assert _pdf(shop, logins, f"/api/purchase-orders/{po_no}/versions/1/pdf") == mailed
    assert _pdf(shop, logins, f"/api/purchase-orders/{po_no}/pdf") == mailed  # the current version, as issued
    assert "New terms for" in _pdf_text(document.order_document(shop, po_no))  # only a new rendering would change


def test_a_minor_revision_with_the_supplier_is_issued_with_its_own_document_and_link(
    shop: Session,
    outbox: Outbox,  # noqa: F811
) -> None:
    po_no, logins, _ = sent_order(shop, outbox)
    outbox.sent.clear()

    done = revise(shop, logins, po_no, {"lines": [{"line_no": 1, "qty": "8"}], "version": 1})

    assert done.status_code == 200 and done.json()["reapproval"] is False
    (mail,) = outbox.sent
    assert mail["subject"] == f"Purchase order {po_no} (version 2): revised"
    v2 = versions(shop, po_no)[1]
    (stored,) = rows(shop, "select sha256, filename from document where id = :i", i=v2["document_id"])
    assert sha(attachment(outbox)) == stored["sha256"] and stored["filename"] == f"{po_no}-v2.pdf"
    assert v2["sent_to"] == "s1@supplier.example"
    page = _pdf_text(attachment(outbox))
    assert f"PURCHASE ORDER {po_no}, version 2" in page and "This version:" in page and "944.00" in page
    (link,) = rows(
        shop, "select po_version from supplier_link where id = :i", i=supplier_links.read(_token(mail["body"])).id
    )
    assert link["po_version"] == 2  # a link for the version mailed (R24)


def test_a_version_waiting_for_approval_has_no_document_and_its_pdf_is_a_draft_until_sent(
    shop: Session,
    outbox: Outbox,  # noqa: F811
) -> None:
    po_no, logins, _ = sent_order(shop, outbox)
    # the need-by moves 5 days, beyond the 3 accepted: approved again
    done = revise(shop, logins, po_no, {"lines": [{"line_no": 1, "need_by": "2026-11-25"}]})
    assert done.json()["status"] == "submitted"

    missing = client.get(f"/api/purchase-orders/{po_no}/versions/2/pdf", headers=buyer(shop, logins))
    assert (missing.status_code, missing.json()["detail"]) == (404, "version 2 was not issued")
    draft = _pdf_text(_pdf(shop, logins, f"/api/purchase-orders/{po_no}/pdf"))
    assert f"PURCHASE ORDER {po_no}, version 2, DRAFT: not issued" in draft
    assert versions(shop, po_no)[1]["document_id"] is None

    workflow.approve(shop, shop.info["slug"], po_no, logins["procurement"], version=2)
    outbox.sent.clear()
    client.post(f"/api/purchase-orders/{po_no}/send", headers=_headers(shop.info["slug"], logins["buyer"], "Buyer"))

    v2 = versions(shop, po_no)[1]
    assert _pdf(shop, logins, f"/api/purchase-orders/{po_no}/versions/2/pdf") == attachment(outbox)
    assert v2["document_id"] is not None and outbox.sent[-1]["subject"] == f"Purchase order {po_no} (version 2)"
    assert "DRAFT" not in _pdf_text(_pdf(shop, logins, f"/api/purchase-orders/{po_no}/pdf"))


def test_when_the_document_cannot_be_made_the_order_goes_as_text_and_the_failure_is_kept(
    shop: Session,
    outbox: Outbox,
    monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    def broken(*_: object, **__: object) -> bytes:
        raise RuntimeError("the layout failed")

    monkeypatch.setattr(document, "order_document", broken)

    po_no, _, _ = sent_order(shop, outbox)

    assert outbox.sent[-1]["attachments"] == [] and order(shop, po_no)["status"] == "sent"
    assert versions(shop, po_no)[0]["document_id"] is None
    assert audits(shop, "po.document_failed", po_no) == [{"version": 1}]
    assert audits(shop, "po.version_issued", po_no) == []


def test_the_versions_are_listed_newest_first_and_any_two_compared(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, logins, _ = sent_order(shop, outbox)
    revise(shop, logins, po_no, {"lines": [{"line_no": 1, "qty": "8"}]})
    revise(shop, logins, po_no, {"lines": [{"line_no": 1, "need_by": "2026-11-25"}]})  # material: waits
    head = buyer(shop, logins)

    listed = client.get(f"/api/purchase-orders/{po_no}/versions", headers=head).json()
    assert [(v["version"], v["approval"], v["made_by"]) for v in listed] == [
        (3, "waiting", f"user:{logins['buyer']}"),
        (2, "carried", f"user:{logins['buyer']}"),
        (1, "approved", "service:ordering"),
    ]
    assert listed[2]["document"]["sha256"] and listed[1]["document"] and listed[0]["document"] is None
    assert listed[0]["material"] == [
        "line 1: date moved 5 days from the approved 20 Nov 2026, more than the 3 accepted"
    ]

    one_two = client.get(f"/api/purchase-orders/{po_no}/versions/compare?from=1&to=2", headers=head).json()
    assert [(h["field"], h["before"], h["after"]) for h in one_two["header"]] == [
        ("taxable", "1000.00", "800.00"), ("igst", "180.00", "144.00"), ("total", "1180.00", "944.00"),
    ]  # fmt: skip
    (line,) = one_two["lines"]
    assert (line["line_no"], line["change"]) == (1, "changed")
    assert [(f["field"], D(f["before"]), D(f["after"])) for f in line["fields"]] == [
        ("qty", D("10"), D("8")), ("taxable", D("1000"), D("800")), ("igst", D("180"), D("144")),
    ]  # fmt: skip
    two_three = client.get(f"/api/purchase-orders/{po_no}/versions/compare?from=2&to=3", headers=head).json()
    assert two_three["header"] == [] and [f["field"] for f in two_three["lines"][0]["fields"]] == ["need_by"]
    assert two_three["lines"][0]["fields"][0]["after"] == "2026-11-25"

    same = client.get(f"/api/purchase-orders/{po_no}/versions/compare?from=2&to=2", headers=head)
    assert same.status_code == 422
    none = client.get(f"/api/purchase-orders/{po_no}/versions/compare?from=1&to=9", headers=head)
    assert (none.status_code, none.json()["detail"]) == (
        404, f"version 9 of {po_no} has no record (made before versions were kept)",
    )  # fmt: skip
    assert client.get(f"/api/purchase-orders/{po_no}/versions/compare?from=a&to=2", headers=head).status_code == 422
    assert client.get("/api/purchase-orders/NOPE/versions", headers=head).status_code == 404


def test_the_supplier_page_offers_the_stored_document_of_its_links_version(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, logins, token = sent_order(shop, outbox)
    mailed = attachment(outbox)

    page = client.get(f"/supplier/{token}")
    assert "This version of the order (PDF)" in page.text and f"/supplier/{token}/pdf" in page.text
    got = client.get(f"/supplier/{token}/pdf")
    assert got.status_code == 200 and got.content == mailed

    # after a revision the old link is refused as changed, the PDF too; the new link serves version 2
    outbox.sent.clear()
    changes.revise(shop, shop.info["slug"], po_no, logins["buyer"], "Cut", [changes.LineChange(1, qty=D("8"))])
    old = client.get(f"/supplier/{token}/pdf")
    assert old.status_code == 409 and "has changed since this link was sent" in old.text
    new = _token(outbox.sent[-1]["body"])
    assert client.get(f"/supplier/{new}/pdf").content == attachment(outbox)
    altered = client.get(f"/supplier/{new[:-3]}{'A' if new[-3] != 'A' else 'B'}{new[-2:]}/pdf")
    assert altered.status_code == 400 and "not valid" in altered.text


def test_another_company_sees_no_version_comparison_or_document_of_this_order(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    po_no, logins, _ = sent_order(shop, outbox)
    other = "q" + uuid.uuid4().hex[:8]
    assert tenants.create_tenant(other, load_reference=False).ok
    try:
        head = _headers(other, 1, "Buyer")
        for path in ("versions", "versions/compare?from=1&to=2", "versions/1/pdf", "pdf"):
            assert client.get(f"/api/purchase-orders/{po_no}/{path}", headers=head).status_code == 404, path
        stranger = client.post(
            f"/api/purchase-orders/{po_no}/revise",
            json={"reason": "x", "header": {"delivery_terms": "ex_works"}},
            headers=head,
        )
        assert stranger.status_code in (403, 404)
    finally:
        routing.clear_engine_cache()
        tenants.drop_tenant(other)
    assert order(shop, po_no)["version"] == 1  # nothing happened to this company's order
