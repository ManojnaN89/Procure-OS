"""Supplier records: the checks on a record, the onboarding form (always a draft), edits, bank changes and scanned documents."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_sourcing import PASSWORD, _headers, db  # noqa: F401 - fixture

from p2p.formulas import supplier_master as rules
from p2p.formulas.gstin import gstin_check_char
from p2p.main import app
from p2p.masters import suppliers
from p2p.platform import proposals, tenants, users
from p2p.platform.permissions import PermissionDenied
from p2p.platform.sod import user

client = TestClient(app)


def _gstin(pan: str, state: str = "27", entity: str = "1") -> str:
    first14 = f"{state}{pan}{entity}Z"
    return first14 + gstin_check_char(first14)


PAN = "ABCDE0000F"
GOOD = {
    "legal_name": "Kaveri Fasteners Pvt Ltd",
    "legal_form": "pvt_ltd",
    "pan": PAN,
    "gst_reg_type": "regular",
    "gstin": _gstin(PAN),
    "msme_category": "small",
    "udyam_no": "UDYAM-MH-00-1234567",
    "payment_terms_code": "NET30",
    "email": "sales@kaveri.example",
    "phone": "+91 00000 12345",
    "account_no": "000123456789",
    "ifsc": "HDFC0001234",
}


# ---- the checks, at their edges ---------------------------------------------------------------


def test_a_well_formed_record_has_no_problems() -> None:
    assert rules.check_identity(GOOD, {"NET30"}) == {}
    assert rules.check_contact(GOOD) == {}
    assert rules.check_bank(GOOD) == {}


@pytest.mark.parametrize(
    ("change", "field"),
    [
        ({"legal_name": " "}, "legal_name"),
        ({"pan": "ABCDE0000"}, "pan"),
        ({"pan": ""}, "pan"),
        ({"gstin": _gstin(PAN)[:14] + "0"}, "gstin"),  # the check character is wrong
        ({"gstin": _gstin("PQRST0000U")}, "gstin"),  # the PAN inside it is another company's
        ({"gstin": ""}, "gstin"),  # registered for GST but no GSTIN
        ({"gst_reg_type": "unregistered"}, "gstin"),  # not registered but has one
        ({"gst_reg_type": "maybe"}, "gst_reg_type"),
        ({"msme_category": "tiny"}, "msme_category"),
        ({"udyam_no": ""}, "udyam_no"),  # small, so it needs one
        ({"udyam_no": "UDYAM-MH-0-1234567"}, "udyam_no"),
        ({"payment_terms_code": "NET99"}, "payment_terms_code"),
        ({"agreed_payment_days": "366"}, "agreed_payment_days"),
        ({"legal_form": "club"}, "legal_form"),
    ],
)
def test_each_wrong_identity_field_is_named(change: dict, field: str) -> None:
    assert field in rules.check_identity({**GOOD, **change}, {"NET30"})


def test_an_unregistered_supplier_with_no_msme_status_needs_neither_gstin_nor_udyam() -> None:
    plain = {"legal_name": "Raju Traders", "pan": PAN, "gst_reg_type": "unregistered", "msme_category": "none"}
    assert rules.check_identity(plain) == {}


@pytest.mark.parametrize(
    ("account", "ok"),
    [
        ("12345678", False),
        ("123456789", True),
        ("123456789012345678", True),
        ("1234567890123456789", False),
        ("12345678a", False),
    ],
)
def test_the_account_number_is_nine_to_eighteen_digits(account: str, ok: bool) -> None:
    assert ("account_no" not in rules.check_bank({"account_no": account, "ifsc": "HDFC0001234"})) is ok


@pytest.mark.parametrize(
    ("ifsc", "ok"),
    [
        ("HDFC0001234", True),
        ("hdfc0001234", True),
        ("HDFC1001234", False),
        ("HDF0001234", False),
        ("HDFC000123", False),
    ],
)
def test_the_ifsc_is_four_letters_a_zero_and_six_characters(ifsc: str, ok: bool) -> None:
    assert ("ifsc" not in rules.check_bank({"account_no": "123456789", "ifsc": ifsc})) is ok


def test_the_next_code_follows_the_highest_one_and_ignores_other_styles() -> None:
    assert rules.next_code([]) == "SUP-001"
    assert rules.next_code(["SUP-001", "SUP-009", "S1", "SUP-X"]) == "SUP-010"
    assert rules.next_code(["SUP-999"]) == "SUP-1000"


# ---- the onboarding form ----------------------------------------------------------------------


@pytest.fixture
def world(db: Session) -> Session:  # noqa: F811
    db.execute(
        text("insert into payment_terms (code, name, days, baseline) values ('NET30', 'Net 30', 30, 'invoice_date')")
    )
    db.commit()
    return db


def _people(db_: Session) -> tuple[int, int, int]:
    slug, sfx = db_.info["slug"], db_.info["sfx"]
    buyer = users.create_user(slug, f"b{sfx}@x.example", PASSWORD, "A Buyer", "Buyer")
    boss = users.create_user(slug, f"o{sfx}@x.example", PASSWORD, "An Owner", "Approver")
    other = users.create_user(slug, f"r{sfx}@x.example", PASSWORD, "A Requester", "Requester")
    return buyer, boss, other


def test_a_new_supplier_is_saved_as_a_draft_with_its_address_contact_and_bank_account(world: Session) -> None:
    buyer, _, _ = _people(world)

    code = suppliers.create_supplier(
        world, {**GOOD, "address": "Plot 4, MIDC", "city": "Pune", "pincode": "411019"}, buyer
    )

    assert code.startswith("SUP-")
    row = world.execute(
        text("select status, gstin, state_code, msme_category, payment_terms_code from supplier where code = :c"),
        {"c": code},
    ).one()
    assert row == ("draft", GOOD["gstin"], "27", "small", "NET30")
    assert world.execute(
        text("select city, state_code from supplier_address where supplier_code = :c"), {"c": code}
    ).one() == ("Pune", "27")
    assert world.execute(
        text("select email, is_primary from supplier_contact where supplier_code = :c"), {"c": code}
    ).one() == (
        "sales@kaveri.example",
        True,
    )
    assert world.execute(
        text("select account_no, ifsc, verified from supplier_bank_account where supplier_code = :c"), {"c": code}
    ).one() == (
        "000123456789",
        "HDFC0001234",
        False,
    )
    assert world.execute(text("select count(*) from audit_event where action = 'supplier.create'")).scalar_one() == 1


def test_a_draft_supplier_cannot_be_used_until_it_is_activated(world: Session) -> None:
    from datetime import date

    from p2p.sourcing import documents

    buyer, _, _ = _people(world)
    code = suppliers.create_supplier(world, GOOD, buyer)

    check = documents.check_supplier(world, code, date(2026, 11, 6))

    assert not check.ok and "PAN card is missing" in check.blocking  # nothing is on file yet


def test_a_record_with_problems_names_every_one_and_saves_nothing(world: Session) -> None:
    buyer, _, _ = _people(world)

    with pytest.raises(suppliers.SupplierError) as err:
        suppliers.create_supplier(world, {**GOOD, "pan": "BAD", "ifsc": "X", "email": "nope"}, buyer)

    assert all(word in str(err.value) for word in ("pan:", "ifsc:", "email:"))
    assert world.execute(text("select count(*) from supplier where code like 'SUP-%'")).scalar_one() == 0


def test_a_second_supplier_with_the_same_gstin_or_bank_account_is_refused_as_a_duplicate(world: Session) -> None:
    buyer, _, _ = _people(world)
    first = suppliers.create_supplier(world, GOOD, buyer)

    with pytest.raises(suppliers.SupplierError, match=f"possible duplicate: {first} already has this GSTIN"):
        suppliers.create_supplier(world, {**GOOD, "legal_name": "Another name"}, buyer)
    other_pan = "PQRST0000U"
    with pytest.raises(suppliers.SupplierError, match="already has this bank account"):
        suppliers.create_supplier(
            world,
            {
                **GOOD,
                "legal_name": "Third",
                "pan": other_pan,
                "gstin": _gstin(other_pan),
                "udyam_no": "UDYAM-MH-00-7654321",
            },
            buyer,
        )


# ---- edits and bank changes -------------------------------------------------------------------


def test_names_terms_and_contact_can_change_but_pan_and_gst_only_while_it_is_a_draft(world: Session) -> None:
    buyer, _, _ = _people(world)
    code = suppliers.create_supplier(world, GOOD, buyer)

    changed = suppliers.update_supplier(
        world, code, {"trade_name": "Kaveri", "email": "NEW@Kaveri.example", "msme_category": "micro"}, buyer
    )
    assert sorted(changed) == ["email", "msme_category", "trade_name"]
    assert world.execute(text("select trade_name, msme_category from supplier where code = :c"), {"c": code}).one() == (
        "Kaveri",
        "micro",
    )
    assert (
        world.execute(text("select email from supplier_contact where supplier_code = :c"), {"c": code}).scalar_one()
        == "new@kaveri.example"
    )

    world.execute(text("update supplier set status = 'active' where code = :c"), {"c": code})
    world.commit()
    assert suppliers.update_supplier(world, code, {"trade_name": "Kaveri Fasteners"}, buyer) == ["trade_name"]
    with pytest.raises(suppliers.SupplierError, match="msme_category cannot be changed while the supplier is active"):
        suppliers.update_supplier(world, code, {"msme_category": "medium"}, buyer)
    assert world.execute(text("select count(*) from audit_event where action = 'supplier.update'")).scalar_one() == 2


def test_the_bank_account_of_a_draft_is_set_but_of_a_supplier_in_use_it_is_a_proposal_a_person_approves(
    world: Session,
) -> None:
    buyer, boss, _ = _people(world)
    code = suppliers.create_supplier(world, GOOD, buyer)
    world.execute(
        text(
            "insert into supplier_document (supplier_code, doc_type, number, verified) values (:c, 'cancelled_cheque', 'CH1', true)"
        ),
        {"c": code},
    )
    world.commit()

    direct = suppliers.change_bank(world, code, {"account_no": "999888777666", "ifsc": "ICIC0004321"}, user(buyer), "")
    assert direct == {"changed": True, "proposal": None}

    world.execute(text("update supplier set status = 'active' where code = :c"), {"c": code})
    world.commit()
    with pytest.raises(suppliers.SupplierError, match="a reason is needed"):
        suppliers.change_bank(world, code, {"account_no": "111222333444", "ifsc": "ICIC0004321"}, user(buyer), " ")
    waiting = suppliers.change_bank(
        world, code, {"account_no": "111222333444", "ifsc": "ICIC0004321"}, user(buyer), "Moved bank"
    )
    assert waiting["changed"] is False and waiting["proposal"]
    assert (
        world.execute(
            text("select account_no from supplier_bank_account where supplier_code = :c"), {"c": code}
        ).scalar_one()
        == "999888777666"
    )

    with pytest.raises(PermissionDenied):  # a buyer may ask but not approve
        proposals.approve(world, waiting["proposal"], buyer)
    proposals.approve(world, waiting["proposal"], boss)

    assert world.execute(
        text("select account_no, verified from supplier_bank_account where supplier_code = :c"), {"c": code}
    ).one() == ("111222333444", False)
    assert (
        world.execute(
            text("select verified from supplier_document where supplier_code = :c and doc_type = 'cancelled_cheque'"),
            {"c": code},
        ).scalar_one()
        is False
    )
    entry = world.execute(
        text("select before, after from audit_event where action = 'supplier.bank_change' order by id desc limit 1")
    ).one()
    assert "111222333444" not in str(entry) and "****3444" in str(entry)  # the audit never holds the whole number


# ---- the screens' API -------------------------------------------------------------------------


def test_the_api_lets_a_buyer_onboard_and_edit_and_nobody_without_the_permission(world: Session) -> None:
    buyer, boss, other = _people(world)
    slug = world.info["slug"]
    h_buyer, h_boss, h_other = (
        _headers(slug, buyer, "Buyer"),
        _headers(slug, boss, "Approver"),
        _headers(slug, other, "Requester"),
    )

    assert client.post("/api/vendors", json=GOOD).status_code == 401
    assert client.post("/api/vendors", json=GOOD, headers=h_other).status_code == 403
    bad = client.post("/api/vendors", json={**GOOD, "gstin": "27ABCDE0000F1Z0"}, headers=h_buyer)
    assert bad.status_code == 422 and "gstin:" in bad.json()["detail"]
    made = client.post("/api/vendors", json=GOOD, headers=h_buyer)
    code = made.json()["id"]
    assert made.status_code == 200 and made.json()["status"] == "draft"
    assert client.post("/api/vendors", json={**GOOD, "legal_name": "Again"}, headers=h_buyer).status_code == 409

    profile = client.get(f"/api/vendors/{code}/profile", headers=h_buyer).json()
    assert profile["fields"]["gstin"] == GOOD["gstin"] and {
        k: profile["bank"][k] for k in ("account", "ifsc", "verified", "method")
    } == {"account": "****6789", "ifsc": "HDFC0001234", "verified": False, "method": None}
    assert "account_no" not in profile["bank"] and profile["can_verify_bank"] is False  # a buyer never verifies
    assert profile["frozen_fields"] == [] and profile["can_edit"] is True
    assert client.get(f"/api/vendors/{code}/profile", headers=h_other).json()["can_edit"] is False
    assert client.get("/api/vendors/NOPE/profile", headers=h_buyer).status_code == 404

    assert client.put(f"/api/vendors/{code}", json={"trade_name": "Kaveri"}, headers=h_other).status_code == 403
    assert client.put(f"/api/vendors/{code}", json={"trade_name": "Kaveri"}, headers=h_buyer).json()["changed"] == [
        "trade_name"
    ]
    assert client.put("/api/vendors/NOPE", json={"trade_name": "x"}, headers=h_buyer).status_code == 404
    waiting = client.post(
        f"/api/vendors/{code}/bank", json={"account_no": "5551234567", "ifsc": "SBIN0001234"}, headers=h_buyer
    )
    assert waiting.status_code == 200 and waiting.json()["changed"] is True  # still a draft
    assert [t["code"] for t in client.get("/api/payment-terms", headers=h_other).json()] == ["NET30"]
    assert client.get("/api/payment-terms").status_code == 401
    assert (
        client.post(f"/api/vendors/{code}/bank", json={"account_no": "x", "ifsc": "y"}, headers=h_boss).status_code
        == 422
    )


def test_a_scan_is_kept_with_its_document_and_only_a_scan_is_accepted(world: Session) -> None:
    buyer, _, other = _people(world)
    slug = world.info["slug"]
    h_buyer, h_other = _headers(slug, buyer, "Buyer"), _headers(slug, other, "Requester")
    code = client.post("/api/vendors", json=GOOD, headers=h_buyer).json()["id"]
    doc = client.post(f"/api/vendors/{code}/documents", json={"type": "pan", "number": PAN}, headers=h_buyer).json()[
        "id"
    ]
    pdf = b"%PDF-1.4 a scan"

    assert (
        client.post(
            f"/api/vendors/{code}/documents/{doc}/file", files={"file": ("pan.pdf", pdf)}, headers=h_other
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/api/vendors/{code}/documents/{doc}/file", files={"file": ("pan.exe", pdf)}, headers=h_buyer
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"/api/vendors/{code}/documents/999/file", files={"file": ("pan.pdf", pdf)}, headers=h_buyer
        ).status_code
        == 404
    )
    saved = client.post(f"/api/vendors/{code}/documents/{doc}/file", files={"file": ("pan.pdf", pdf)}, headers=h_buyer)
    assert saved.status_code == 201 and saved.json()["filename"] == "pan.pdf"

    profile = client.get(f"/api/vendors/{code}/profile", headers=h_buyer).json()
    assert profile["files"][str(doc)]["filename"] == "pan.pdf"
    assert client.get(f"/api/files/{saved.json()['id']}", headers=h_buyer).content == pdf
    assert (
        world.execute(text("select count(*) from audit_event where action = 'supplier.document_file'")).scalar_one()
        == 1
    )


# ---- verifying a bank account -----------------------------------------------------------------


def _proof(db_: Session, data: bytes = b"%PDF-1.4 a cancelled cheque") -> suppliers.Evidence:
    return suppliers.Evidence(db_.info["slug"], data, "cheque.pdf", "application/pdf")


def _finance(db_: Session) -> int:
    return users.create_user(db_.info["slug"], f"f{db_.info['sfx']}@x.example", PASSWORD, "A Finance", "Finance")


def _account(db_: Session, code: str) -> tuple[int, str | None, bool]:
    row = db_.execute(
        text("select id, submitted_by, coalesce(verified, false) from supplier_bank_account where supplier_code = :c"),
        {"c": code},
    ).one()
    return int(row[0]), row[1], bool(row[2])


def test_whoever_entered_a_bank_account_cannot_verify_it_and_a_second_person_verifies_it_against_a_proof(
    world: Session,
) -> None:
    buyer, boss, _ = _people(world)
    finance = _finance(world)
    code = suppliers.create_supplier(world, GOOD, buyer)
    account, entered_by, verified = _account(world, code)
    assert (entered_by, verified) == (str(buyer), False)

    with pytest.raises(suppliers.SupplierError, match="segregation of duties"):
        suppliers.verify_bank(world, code, account, "cancelled_cheque", _proof(world), buyer)
    with pytest.raises(suppliers.SupplierError, match="must be one of cancelled_cheque, bank_letter, penny_drop"):
        suppliers.verify_bank(world, code, account, "phone_call", _proof(world), finance)
    with pytest.raises(suppliers.SupplierError, match="proof"):
        suppliers.verify_bank(world, code, account, "cancelled_cheque", _proof(world, b""), finance)
    with pytest.raises(suppliers.SupplierError, match="does not exist"):
        suppliers.verify_bank(world, code, account + 99, "cancelled_cheque", _proof(world), finance)
    assert _account(world, code)[2] is False  # nothing refused changed anything

    done = suppliers.verify_bank(world, code, account, "penny_drop", _proof(world), finance)

    row = world.execute(
        text(
            "select verified, verified_by, verification_method, evidence_document_id, verified_at is not null "
            "from supplier_bank_account where id = :i"
        ),
        {"i": account},
    ).one()
    assert row == (True, str(finance), "penny_drop", done["evidence"], True)
    assert world.execute(
        text("select linked_type, linked_id, doc_type from document where id = :d"), {"d": done["evidence"]}
    ).one() == ("supplier_bank_account", str(account), "bank_verification")
    entry = world.execute(
        text("select actor_id, after from audit_event where action = 'supplier.bank_verify' and entity_id = :c"),
        {"c": code},
    ).one()
    assert entry[0] == str(finance) and entry[1]["account"] == "****6789" and "000123456789" not in str(entry)
    with pytest.raises(suppliers.SupplierError, match="already verified"):
        suppliers.verify_bank(world, code, account, "penny_drop", _proof(world), boss)


def test_the_ways_to_verify_are_a_setting(world: Session) -> None:
    buyer, _, _ = _people(world)
    finance = _finance(world)
    code = suppliers.create_supplier(world, GOOD, buyer)
    world.execute(
        text("insert into org_setting (key, value) values ('bank_verification_methods', 'video_call, bank_letter')")
    )
    world.commit()
    account = _account(world, code)[0]

    assert suppliers.verification_methods(world) == ["video_call", "bank_letter"]
    with pytest.raises(suppliers.SupplierError, match="must be one of video_call, bank_letter"):
        suppliers.verify_bank(world, code, account, "penny_drop", _proof(world), finance)
    assert suppliers.verify_bank(world, code, account, "video_call", _proof(world), finance)["verified"] is True


def test_an_approved_bank_change_is_unverified_and_the_person_who_asked_for_it_cannot_verify_it(world: Session) -> None:
    buyer, boss, _ = _people(world)
    finance = _finance(world)
    code = suppliers.create_supplier(world, GOOD, buyer)
    suppliers.verify_bank(world, code, _account(world, code)[0], "cancelled_cheque", _proof(world), finance)
    world.execute(text("update supplier set status = 'active' where code = :c"), {"c": code})
    world.commit()

    new = {"account_no": "111222333444", "ifsc": "ICIC0004321"}
    asked = suppliers.change_bank(world, code, new, user(buyer), "Moved bank")
    assert _account(world, code)[2] is True  # the old, verified account stays until the change is approved
    proposals.approve(world, asked["proposal"], boss)

    account, asked_by, verified = _account(world, code)
    assert (asked_by, verified) == (str(buyer), False)  # the one who asked, not the one who approved
    with pytest.raises(suppliers.SupplierError, match="segregation of duties"):
        suppliers.verify_bank(world, code, account, "bank_letter", _proof(world), buyer)
    assert suppliers.verify_bank(world, code, account, "bank_letter", _proof(world), finance)["verified"] is True


def test_only_finance_verifies_over_the_api_others_see_four_digits_and_another_company_cannot_reach_it(
    world: Session,
) -> None:
    buyer, boss, _ = _people(world)
    finance = _finance(world)
    slug = world.info["slug"]
    h_buyer, h_boss = _headers(slug, buyer, "Buyer"), _headers(slug, boss, "Approver")
    h_fin = _headers(slug, finance, "Finance")
    code = client.post("/api/vendors", json=GOOD, headers=h_buyer).json()["id"]
    account = _account(world, code)[0]
    url = f"/api/vendors/{code}/bank/{account}/verify"
    form = {"method": "cancelled_cheque"}
    pdf = {"file": ("cheque.pdf", b"%PDF-1.4 cheque")}

    seen = client.get(f"/api/vendors/{code}/profile", headers=h_boss).json()
    assert seen["bank"]["account"] == "****6789" and "account_no" not in seen["bank"] and not seen["can_verify_bank"]
    mine = client.get(f"/api/vendors/{code}/profile", headers=h_fin).json()
    assert (
        mine["can_verify_bank"] is True and mine["bank"]["account_no"] == "000123456789"
    )  # to check against the proof
    assert mine["bank_methods"] == ["cancelled_cheque", "bank_letter", "penny_drop"]

    assert client.post(url, data=form, files=pdf, headers=h_boss).status_code == 403
    assert client.post(url, data=form, files=pdf, headers=h_buyer).status_code == 403
    assert client.post(url, data=form, files={"file": ("cheque.exe", b"x")}, headers=h_fin).status_code == 422

    other = "q" + uuid.uuid4().hex[:8]  # another company: its finance person cannot reach this account
    assert tenants.create_tenant(other, load_reference=False).ok
    stranger = users.create_user(other, f"f{world.info['sfx']}@y.example", PASSWORD, "Elsewhere", "Finance")
    assert client.post(url, data=form, files=pdf, headers=_headers(other, stranger, "Finance")).status_code == 404
    assert _account(world, code)[2] is False

    done = client.post(url, data=form, files=pdf, headers=h_fin)
    assert done.status_code == 200 and done.json()["verified"] is True
    after = client.get(f"/api/vendors/{code}/profile", headers=h_fin).json()["bank"]
    assert (after["verified"], after["method"], "account_no" in after) == (True, "cancelled_cheque", False)
    assert client.post(url, data=form, files=pdf, headers=h_fin).status_code == 409
