"""Supplier documents (added, then verified by someone else) and contracts (stored as a draft, priced, then activated).

A supplier document counts only once a person has checked it against the original, and that person is not the one who
added it. A contract is stored as a draft with its price list; making it active is the approver's decision, and only for
a supplier that can be used.
"""

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.formulas.suppliers import LABELS
from p2p.platform import audit
from p2p.platform.sod import user
from p2p.sourcing import documents

D = Decimal


class MasterError(Exception):
    pass


# ---- supplier documents -----------------------------------------------------------------------


def add_document(
    db: Session,
    supplier: str,
    doc_type: str,
    number: str | None,
    issued_on: date | None,
    expires_on: date | None,
    user_id: int,
) -> int:
    if doc_type not in LABELS:
        raise MasterError(f"the document type must be one of {', '.join(sorted(LABELS))}")
    if db.execute(text("select 1 from supplier where code = :c"), {"c": supplier}).first() is None:
        raise MasterError(f"supplier {supplier} does not exist")
    if issued_on and expires_on and expires_on < issued_on:
        raise MasterError("a document cannot expire before it was issued")
    doc_id = int(
        db.execute(
            text(
                "insert into supplier_document (supplier_code, doc_type, number, issued_on, expires_on, verified, added_by) "
                "values (:s, :t, :n, :i, :e, false, :u) returning id"
            ),
            {"s": supplier, "t": doc_type, "n": number, "i": issued_on, "e": expires_on, "u": str(user_id)},
        ).scalar_one()
    )
    audit.record(
        db,
        user(user_id),
        "supplier.document_add",
        "supplier",
        supplier,
        after={"type": doc_type, "number": number, "id": doc_id},
    )
    db.commit()
    return doc_id


def set_verified(db: Session, supplier: str, doc_id: int, verified: bool, user_id: int) -> None:
    row = db.execute(
        text("select added_by, verified from supplier_document where id = :i and supplier_code = :s for update"),
        {"i": doc_id, "s": supplier},
    ).first()
    if row is None:
        raise MasterError(f"document {doc_id} of {supplier} does not exist")
    if verified and row[0] == str(user_id):
        raise MasterError("segregation of duties: the person who added a document cannot verify it")
    db.execute(
        text("update supplier_document set verified = :v, verified_by = :u, verified_at = now() where id = :i"),
        {"v": verified, "u": str(user_id), "i": doc_id},
    )
    audit.record(
        db, user(user_id), "supplier.document_verify" if verified else "supplier.document_unverify", "supplier", supplier,
        before={"verified": bool(row[1])}, after={"verified": verified, "id": doc_id},
    )  # fmt: skip
    db.commit()


# ---- contracts --------------------------------------------------------------------------------

TYPES = ("rate_contract", "service_contract", "framework")


def create_contract(db: Session, data: dict[str, Any], user_id: int) -> str:
    """Store a contract as a draft. It is used for sourcing only after an approver activates it."""
    supplier = str(data["supplier_code"])
    if db.execute(text("select 1 from supplier where code = :c"), {"c": supplier}).first() is None:
        raise MasterError(f"supplier {supplier} does not exist")
    kind = str(data.get("type") or "rate_contract")
    if kind not in TYPES:
        raise MasterError(f"the type must be one of {', '.join(TYPES)}")
    start, end = data["valid_from"], data.get("valid_to")
    if end is not None and end < start:
        raise MasterError("a contract cannot end before it starts")
    ceiling = data.get("max_value")
    if ceiling is not None and Decimal(ceiling) <= 0:
        raise MasterError("the ceiling must be more than zero (leave it empty for none)")
    last = db.execute(text("select max(code) from contract where code ~ '^CNT-[0-9]+$'")).scalar()
    code = f"CNT-{int(str(last)[4:]) + 1:04d}" if last else "CNT-0001"
    db.execute(
        text(
            "insert into contract (code, supplier_code, type, valid_from, valid_to, max_value, payment_terms_code, delivery_terms, "
            "freight_terms, warranty_months, sla_text, status) values (:c, :s, :t, :f, :e, :m, :p, :d, :fr, :w, :sla, 'draft')"
        ),
        {
            "c": code, "s": supplier, "t": kind, "f": start, "e": end, "m": ceiling, "p": data.get("payment_terms_code"),
            "d": data.get("delivery_terms"), "fr": data.get("freight_terms"), "w": data.get("warranty_months"),
            "sla": data.get("sla_text"),
        },
    )  # fmt: skip
    audit.record(
        db,
        user(user_id),
        "contract.create",
        "contract",
        code,
        after={"supplier": supplier, "type": kind, "ceiling": ceiling},
    )
    db.commit()
    return code


def add_price(db: Session, contract: str, data: dict[str, Any], user_id: int) -> int:
    c = db.execute(
        text("select supplier_code, status, valid_from, valid_to from contract where code = :c for update"),
        {"c": contract},
    ).first()
    if c is None:
        raise MasterError(f"contract {contract} does not exist")
    if c[1] == "expired":
        raise MasterError("an expired contract takes no new prices")
    item = str(data["item_code"])
    if db.execute(text("select 1 from item where code = :i"), {"i": item}).first() is None:
        raise MasterError(f"item {item} does not exist")
    price = Decimal(data["price"])
    if price <= 0:
        raise MasterError("the price must be more than zero")
    start = data.get("valid_from") or c[2]
    end = data.get("valid_to") or c[3]
    if end is not None and end < start:
        raise MasterError("a price cannot end before it starts")
    rid = int(
        db.execute(
            text(
                "insert into price_record (supplier_code, item_code, contract_code, uom, price, min_qty, valid_from, valid_to, lead_days) "
                "values (:s, :i, :c, :u, :p, :m, :f, :e, :l) returning id"
            ),
            {
                "s": c[0], "i": item, "c": contract, "u": data.get("uom"), "p": price, "m": data.get("min_qty") or 1,
                "f": start, "e": end, "l": data.get("lead_days"),
            },
        ).scalar_one()
    )  # fmt: skip
    audit.record(
        db, user(user_id), "contract.price_add", "contract", contract, after={"item": item, "price": price, "id": rid}
    )
    db.commit()
    return rid


def activate(db: Session, contract: str, user_id: int, *, today: date) -> None:
    c = db.execute(
        text("select supplier_code, status, valid_to from contract where code = :c for update"), {"c": contract}
    ).first()
    if c is None:
        raise MasterError(f"contract {contract} does not exist")
    if c[1] != "draft":
        raise MasterError(f"{contract} is {c[1]}; only a draft is activated")
    if c[2] is not None and c[2] < today:
        raise MasterError("the contract has already ended")
    status = db.execute(text("select status from supplier where code = :c"), {"c": c[0]}).scalar()
    if status != "active":
        raise MasterError(f"the supplier {c[0]} is {status}, so a contract with it cannot be activated")
    check = documents.check_supplier(db, c[0], today)
    if not check.ok:
        raise MasterError("the supplier cannot be used: " + "; ".join(check.blocking))
    if (
        db.execute(text("select count(*) from price_record where contract_code = :c"), {"c": contract}).scalar_one()
        == 0
    ):
        raise MasterError("a contract with no prices cannot be used: add its price list first")
    db.execute(text("update contract set status = 'active' where code = :c"), {"c": contract})
    audit.record(
        db,
        user(user_id),
        "contract.activate",
        "contract",
        contract,
        before={"status": "draft"},
        after={"status": "active"},
    )
    db.commit()
