"""Creating and maintaining supplier records: the onboarding form, edits, and bank-detail changes.

A new supplier is always saved as a draft: it cannot be used until its documents are verified and a person activates it
(see supplier_status.py). Changing the bank account of a supplier that is already in use is a sensitive action (rule 5),
so it only creates a proposal that another person approves. Every write is audited.
"""

from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.formulas import supplier_master as rules
from p2p.platform import audit, proposals
from p2p.platform.sod import Actor, user

IDENTITY_FIELDS = (
    "legal_name", "trade_name", "legal_form", "pan", "gst_reg_type", "gstin", "msme_category", "udyam_no",
    "agreed_payment_days", "payment_terms_code",
)  # fmt: skip
FROZEN_WHEN_IN_USE = (
    "pan",
    "gstin",
    "gst_reg_type",
    "msme_category",
    "udyam_no",
)  # these decide which documents are needed


class SupplierError(Exception):
    pass


def _terms(db: Session) -> set[str]:
    return {str(r[0]) for r in db.execute(text("select code from payment_terms"))}


def _refuse(problems: dict[str, str]) -> None:
    if problems:
        raise SupplierError("; ".join(f"{k}: {v}" for k, v in problems.items()))


def _clean(data: dict[str, Any]) -> dict[str, Any]:
    out = {k: (v.strip() if isinstance(v, str) else v) for k, v in data.items()}
    for key in ("pan", "gstin", "udyam_no", "ifsc"):
        if out.get(key):
            out[key] = str(out[key]).upper()
    if out.get("email"):
        out["email"] = str(out["email"]).lower()
    return out


def _duplicate_of(db: Session, data: dict[str, Any], own: str | None = None) -> str | None:
    """A supplier that already has this GSTIN, or the same PAN under the same name, or this bank account."""
    checks = []
    if data.get("gstin"):
        checks.append(("select code from supplier where gstin = :v and code <> :o", data["gstin"], "GSTIN"))
    if data.get("pan") and data.get("legal_name"):
        checks.append((
            "select code from supplier where pan = :v and lower(legal_name) = lower(:n) and code <> :o",
            data["pan"], "PAN and name",
        ))  # fmt: skip
    if data.get("account_no"):
        checks.append((
            "select supplier_code from supplier_bank_account where account_no = :v and supplier_code <> :o",
            data["account_no"], "bank account",
        ))  # fmt: skip
    for sql, value, what in checks:
        found = db.execute(text(sql), {"v": value, "n": data.get("legal_name"), "o": own or ""}).scalar()
        if found:
            return f"{found} already has this {what}"
    return None


def create_supplier(db: Session, data: dict[str, Any], user_id: int) -> str:
    """Save a new supplier as a draft with its registered address, primary contact and bank account."""
    data = _clean(data)
    problems = {**rules.check_identity(data, _terms(db)), **rules.check_contact(data)}
    wants_bank = bool(data.get("account_no") or data.get("ifsc"))
    if wants_bank:
        problems.update(rules.check_bank(data))
    _refuse(problems)
    dup = _duplicate_of(db, data)
    if dup:
        raise SupplierError(f"possible duplicate: {dup}")
    code = rules.next_code([str(r[0]) for r in db.execute(text("select code from supplier"))])
    gstin = data.get("gstin") or None
    state = rules.state_for(gstin, data.get("state_code"))
    db.execute(
        text(
            "insert into supplier (code, legal_name, trade_name, legal_form, pan, gst_reg_type, gstin, state_code, "
            "msme_category, udyam_no, agreed_payment_days, payment_terms_code, status) values "
            "(:c, :ln, :tn, :lf, :pan, :gt, :gstin, :st, :msme, :ud, :days, :terms, 'draft')"
        ),
        {
            "c": code, "ln": data["legal_name"], "tn": data.get("trade_name") or data["legal_name"],
            "lf": data.get("legal_form") or None, "pan": data["pan"], "gt": data.get("gst_reg_type") or "unregistered",
            "gstin": gstin, "st": state, "msme": data.get("msme_category") or "none", "ud": data.get("udyam_no") or None,
            "days": int(data["agreed_payment_days"]) if data.get("agreed_payment_days") not in (None, "") else None,
            "terms": data.get("payment_terms_code") or None,
        },
    )  # fmt: skip
    if data.get("address") or data.get("city") or data.get("pincode"):
        db.execute(
            text(
                "insert into supplier_address (supplier_code, type, gstin, address, city, state_code, pincode) "
                "values (:c, 'registered', :g, :a, :ci, :st, :pin)"
            ),
            {
                "c": code, "g": gstin, "a": data.get("address"), "ci": data.get("city"), "st": state,
                "pin": data.get("pincode"),
            },
        )  # fmt: skip
    if data.get("email") or data.get("phone") or data.get("contact_name"):
        db.execute(
            text(
                "insert into supplier_contact (supplier_code, name, role, email, phone, is_primary) "
                "values (:c, :n, 'sales', :e, :p, true)"
            ),
            {"c": code, "n": data.get("contact_name") or "Sales Desk", "e": data.get("email"), "p": data.get("phone")},
        )
    if wants_bank:
        db.execute(
            text(
                "insert into supplier_bank_account (supplier_code, bank_code, account_no, ifsc, verified) "
                "values (:c, :b, :a, :i, false)"
            ),
            {"c": code, "b": str(data["ifsc"])[:4], "a": data["account_no"], "i": data["ifsc"]},
        )
    audit.record(
        db, user(user_id), "supplier.create", "supplier", code,
        after={"legal_name": data["legal_name"], "gstin": gstin, "pan": data["pan"], "status": "draft"},
    )  # fmt: skip
    db.commit()
    return code


def update_supplier(db: Session, code: str, data: dict[str, Any], user_id: int) -> list[str]:
    """Change names, terms and contact at any time; PAN, GST and MSME status only while the supplier is a draft."""
    row = db.execute(text("select * from supplier where code = :c for update"), {"c": code}).mappings().first()
    if row is None:
        raise SupplierError(f"supplier {code} does not exist")
    data = _clean(data)
    merged = {**{k: row[k] for k in IDENTITY_FIELDS}, **{k: v for k, v in data.items() if k in IDENTITY_FIELDS}}
    changed = [k for k in IDENTITY_FIELDS if k in data and str(data[k] or "") != str(row[k] or "")]
    if row["status"] != "draft":
        frozen = [k for k in changed if k in FROZEN_WHEN_IN_USE]
        if frozen:
            raise SupplierError(
                f"{', '.join(frozen)} cannot be changed while the supplier is {row['status']}: "
                "put it back under review first (that needs another person's approval)"
            )
    contact_keys = [k for k in ("email", "phone", "contact_name") if k in data]
    problems = {**rules.check_identity(merged, _terms(db)), **rules.check_contact(data)}
    _refuse(problems)
    dup = _duplicate_of(db, {k: merged.get(k) for k in ("gstin", "pan", "legal_name")}, own=code)
    if dup and any(k in changed for k in ("gstin", "pan", "legal_name")):
        raise SupplierError(f"possible duplicate: {dup}")
    before = {k: str(row[k]) if row[k] is not None else None for k in changed}
    if changed:
        sets = ", ".join(f"{k} = :{k}" for k in changed)
        params = {k: (data[k] or None) for k in changed}
        if "gstin" in changed:
            sets += ", state_code = :st"
            params["st"] = rules.state_for(data["gstin"] or None, row["state_code"])
        db.execute(text(f"update supplier set {sets} where code = :c"), {**params, "c": code})  # noqa: S608 - fixed column names
    if contact_keys:
        primary = db.execute(
            text(
                "select id from supplier_contact where supplier_code = :c order by is_primary desc nulls last, id limit 1"
            ),
            {"c": code},
        ).scalar()
        values = {"name": data.get("contact_name"), "email": data.get("email"), "phone": data.get("phone")}
        if primary is None:
            db.execute(
                text(
                    "insert into supplier_contact (supplier_code, name, role, email, phone, is_primary) "
                    "values (:c, :n, 'sales', :e, :p, true)"
                ),
                {"c": code, "n": values["name"] or "Sales Desk", "e": values["email"], "p": values["phone"]},
            )
        else:
            for key, column in (("contact_name", "name"), ("email", "email"), ("phone", "phone")):
                if key in data:
                    db.execute(
                        text(f"update supplier_contact set {column} = :v where id = :i"),  # noqa: S608 - fixed column names
                        {"v": data[key] or None, "i": primary},
                    )
        changed += contact_keys
    if changed:
        audit.record(
            db, user(user_id), "supplier.update", "supplier", code, before=before,
            after={k: data.get(k) for k in changed},
        )  # fmt: skip
    db.commit()
    return changed


def change_bank(db: Session, code: str, data: dict[str, Any], actor: Actor, reason: str) -> dict[str, Any]:
    """A draft supplier's bank account is simply set. For any other supplier a person must approve the change."""
    data = _clean(data)
    _refuse(rules.check_bank(data))
    status = db.execute(text("select status from supplier where code = :c for update"), {"c": code}).scalar()
    if status is None:
        raise SupplierError(f"supplier {code} does not exist")
    dup = _duplicate_of(db, {"account_no": data["account_no"]}, own=code)
    if dup:
        raise SupplierError(f"possible duplicate: {dup}")
    if status == "draft":
        _set_bank(
            db, code, str(data["account_no"]), str(data["ifsc"]), actor, reason or "set while the supplier is a draft"
        )
        db.commit()
        return {"changed": True, "proposal": None}
    if not reason.strip():
        raise SupplierError("a reason is needed to change the bank account of a supplier in use")
    pid = proposals.propose(
        db,
        actor,
        action="supplier.set_bank",
        summary=f"New bank account for {code}: ****{str(data['account_no'])[-4:]}, IFSC {data['ifsc']}. Reason: {reason.strip()}",
        payload={"supplier": code, "account_no": data["account_no"], "ifsc": data["ifsc"], "reason": reason.strip()},
        required_permission="supplier.activate",
        risk="high",
        document="supplier_bank",
    )
    return {"changed": False, "proposal": pid}


def _set_bank(db: Session, code: str, account_no: str, ifsc: str, actor: Actor, reason: str) -> None:
    old = db.execute(
        text("select account_no, ifsc from supplier_bank_account where supplier_code = :c order by id"), {"c": code}
    ).all()
    db.execute(text("delete from supplier_bank_account where supplier_code = :c"), {"c": code})
    db.execute(
        text(
            "insert into supplier_bank_account (supplier_code, bank_code, account_no, ifsc, verified) "
            "values (:c, :b, :a, :i, false)"
        ),
        {"c": code, "b": ifsc[:4], "a": account_no, "i": ifsc},
    )
    # the bank proof on file belonged to the old account: it must be checked again against the new one
    db.execute(
        text(
            "update supplier_document set verified = false where supplier_code = :c and doc_type = 'cancelled_cheque'"
        ),
        {"c": code},
    )
    audit.record(
        db, actor, "supplier.bank_change", "supplier", code,
        before={"accounts": [f"****{str(a)[-4:]} {i}" for a, i in old]}, after={"account": f"****{account_no[-4:]} {ifsc}"},
        reason=reason,
    )  # fmt: skip


@proposals.executor("supplier.set_bank")
def _apply_bank(db: Session, payload: dict[str, Any], actor: Actor) -> dict[str, Any]:
    code = str(payload["supplier"])
    problems = rules.check_bank(payload)
    if problems or db.execute(text("select 1 from supplier where code = :c"), {"c": code}).first() is None:
        raise SupplierError("the bank details in this proposal are no longer valid")
    dup = _duplicate_of(db, {"account_no": payload["account_no"]}, own=code)
    if dup:
        raise SupplierError(f"possible duplicate: {dup}")
    _set_bank(db, code, str(payload["account_no"]), str(payload["ifsc"]), actor, str(payload.get("reason") or ""))
    db.commit()
    return {"supplier": code, "bank_changed": True}
