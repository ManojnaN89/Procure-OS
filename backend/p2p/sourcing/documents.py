"""The document check of a supplier, from the supplier master and the documents on file."""

from datetime import date

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.formulas.suppliers import DocumentCheck, SupplierDocument, check_documents, required_documents


def warn_days(db: Session) -> int:
    value = db.execute(text("select value from org_setting where key = 'supplier_document_warn_days'")).scalar()
    return int(value) if value not in (None, "") else 30


def check_supplier(db: Session, supplier_code: str, today: date) -> DocumentCheck:
    sup = db.execute(
        text("select gst_reg_type, msme_category from supplier where code = :c"), {"c": supplier_code}
    ).first()
    if sup is None:
        return DocumentCheck(blocking=["the supplier does not exist"])
    docs = [
        SupplierDocument(r[0], r[1], bool(r[2]))
        for r in db.execute(
            text("select doc_type, expires_on, verified from supplier_document where supplier_code = :c"),
            {"c": supplier_code},
        )
    ]
    return check_documents(required_documents(sup[0], sup[1]), docs, today, warn_days(db))
