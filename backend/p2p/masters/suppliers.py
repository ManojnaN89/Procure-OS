"""Creating and maintaining supplier records: the onboarding form, edits, and bank-detail changes.

A new supplier is always saved as a draft: it cannot be used until its documents are verified and a person activates it
(see supplier_status.py). Changing the bank account of a supplier that is already in use is a sensitive action (rule 5),
so it only creates a proposal that another person approves. A new or changed account is paid into only after a second
person verifies it against its proof (method, file, who and when). Every write is audited.
"""

from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.formulas import supplier_master as rules
from p2p.formulas.calendar import business_today
from p2p.invoices import payment
from p2p.platform import audit, files, proposals, settings
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
    """A supplier that already has this GSTIN, this PAN (one PAN is one legal person, whatever the name: a blocked
    supplier does not come back under another name, D-03 R7) or this bank account."""
    checks = []
    if data.get("gstin"):
        checks.append(("select code from supplier where gstin = :v and code <> :o", data["gstin"], "GSTIN"))
    if data.get("pan"):
        checks.append(("select code from supplier where pan = :v and code <> :o", data["pan"], "PAN"))
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
                "insert into supplier_bank_account (supplier_code, bank_code, account_no, ifsc, verified, submitted_by) "
                "values (:c, :b, :a, :i, false, :u)"
            ),
            {"c": code, "b": str(data["ifsc"])[:4], "a": data["account_no"], "i": data["ifsc"], "u": str(user_id)},
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
        summary=f"New bank account for {code}: {mask(data['account_no'])}, IFSC {data['ifsc']}. Reason: {reason.strip()}",
        payload={"supplier": code, "account_no": data["account_no"], "ifsc": data["ifsc"], "reason": reason.strip()},
        required_permission="supplier.activate",
        risk="high",
        document="supplier_bank",
    )
    return {"changed": False, "proposal": pid}


def _set_bank(
    db: Session, code: str, account_no: str, ifsc: str, actor: Actor, reason: str, submitted_by: str | None = None
) -> None:
    """The new account replaces the old one, unverified: nothing is paid into it until a second person verifies it, and
    the supplier's payments still waiting in a run leave it (a bank file already made for that run is voided)."""
    old = db.execute(
        text("select account_no, ifsc from supplier_bank_account where supplier_code = :c order by id"), {"c": code}
    ).all()
    db.execute(text("delete from supplier_bank_account where supplier_code = :c"), {"c": code})
    db.execute(
        text(
            "insert into supplier_bank_account (supplier_code, bank_code, account_no, ifsc, verified, submitted_by) "
            "values (:c, :b, :a, :i, false, :u)"
        ),
        {"c": code, "b": ifsc[:4], "a": account_no, "i": ifsc, "u": submitted_by or actor.id},
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
        before={"accounts": [f"{mask(a)} {i}" for a, i in old]}, after={"account": f"{mask(account_no)} {ifsc}"},
        reason=reason,
    )  # fmt: skip
    payment.take_out_supplier(db, code, actor, f"the bank account of {code} changed and is not verified yet")


@proposals.executor("supplier.set_bank")
def _apply_bank(db: Session, payload: dict[str, Any], actor: Actor) -> dict[str, Any]:
    code = str(payload["supplier"])
    problems = rules.check_bank(payload)
    if problems or db.execute(text("select 1 from supplier where code = :c"), {"c": code}).first() is None:
        raise SupplierError("the bank details in this proposal are no longer valid")
    dup = _duplicate_of(db, {"account_no": payload["account_no"]}, own=code)
    if dup:
        raise SupplierError(f"possible duplicate: {dup}")
    # who asked for the change, read from the proposal being run (not from its payload): that person never verifies it
    asked_by = db.execute(
        text(
            "select proposed_by_id from proposal where action = 'supplier.set_bank' and status = 'approved' "
            "and execution is null and payload ->> 'supplier' = :c and payload ->> 'account_no' = :a "
            "order by decided_at desc limit 1"
        ),
        {"c": code, "a": str(payload["account_no"])},
    ).scalar()
    _set_bank(
        db, code, str(payload["account_no"]), str(payload["ifsc"]), actor, str(payload.get("reason") or ""),
        submitted_by=str(asked_by) if asked_by is not None else None,
    )  # fmt: skip
    db.commit()
    return {"supplier": code, "bank_changed": True}


# ---- verifying a bank account -----------------------------------------------------------------

DEFAULT_METHODS = "cancelled_cheque,bank_letter,penny_drop"


@dataclass(frozen=True)
class Evidence:
    """The proof an account was checked against: a scan of the cancelled cheque, the bank's letter, a penny-drop result."""

    tenant: str
    data: bytes
    filename: str
    content_type: str


def mask(account_no: object) -> str:
    """Only the last four digits of an account number are shown in a list or kept in the audit log."""
    return f"****{str(account_no)[-4:]}"


def verification_methods(db: Session) -> list[str]:
    """The ways an account may be verified: the setting `bank_verification_methods`, a comma-separated list."""
    words = settings.value(db, "bank_verification_methods", DEFAULT_METHODS).split(",")
    return [w.strip() for w in words if w.strip()]


def verify_bank(
    db: Session, code: str, account_id: int, method: str, evidence: Evidence, user_id: int
) -> dict[str, Any]:
    """A second person checks the account against its proof. Until then the supplier is never put in a payment run."""
    row = db.execute(
        text(
            "select account_no, submitted_by, coalesce(verified, false) from supplier_bank_account "
            "where id = :i and supplier_code = :c for update"
        ),
        {"i": account_id, "c": code},
    ).first()
    if row is None:
        raise SupplierError(f"bank account {account_id} of {code} does not exist")
    if row[2]:
        raise SupplierError("this bank account is already verified")
    methods = verification_methods(db)
    if method not in methods:
        raise SupplierError(f"the method must be one of {', '.join(methods)}")
    if row[1] == str(user_id):
        raise SupplierError(
            "segregation of duties: the person who entered or asked for a bank account cannot verify it"
        )
    if not evidence.data:
        raise SupplierError("the proof the account was checked against is needed")
    saved = files.save_file(
        db, evidence.tenant, evidence.data, filename=evidence.filename, content_type=evidence.content_type,
        doc_type="bank_verification", uploaded_by=user_id, linked_type="supplier_bank_account", linked_id=str(account_id),
    )  # fmt: skip
    done = db.execute(  # saving the file committed: verify only if nobody changed or verified the account meanwhile
        text(
            "update supplier_bank_account set verified = true, verified_on = :d, verified_at = now(), verified_by = :u, "
            "verification_method = :m, evidence_document_id = :f where id = :i and not coalesce(verified, false) "
            "returning id"
        ),
        {"d": business_today(), "u": str(user_id), "m": method, "f": saved.id, "i": account_id},
    ).first()
    if done is None:
        db.rollback()
        raise SupplierError(f"bank account {account_id} of {code} changed while it was being verified")
    audit.record(
        db, user(user_id), "supplier.bank_verify", "supplier", code, before={"verified": False},
        after={"account": mask(row[0]), "method": method, "evidence": saved.id},
    )  # fmt: skip
    db.commit()
    return {"verified": True, "method": method, "evidence": saved.id}


# ---- the supplier's registered contacts (D53) ------------------------------------------------------------------------


def order_contact(db: Session, supplier_code: str) -> tuple[str, str | None] | None:
    """The contact orders, revisions, chasers and reminders go to: the primary contact, else the first with an email.
    Address and name; None when the supplier has no address on file."""
    row = db.execute(
        text(
            "select email, name from supplier_contact where supplier_code = :c and email is not null "
            "order by is_primary desc nulls last, id limit 1"
        ),
        {"c": supplier_code},
    ).first()
    return (str(row[0]), row[1]) if row else None


def is_contact(db: Session, supplier_code: str, address: str | None) -> bool:
    """The address is a current registered contact of the supplier."""
    return bool(address) and bool(
        db.execute(
            text("select 1 from supplier_contact where supplier_code = :c and lower(email) = lower(:a)"),
            {"c": supplier_code, "a": str(address).strip()},
        ).first()
    )


def registered_sender(
    db: Session, address: str, supplier_codes: list[str], statuses: tuple[str, ...] | None = None
) -> list[tuple[str, int]]:
    """The suppliers among `supplier_codes` (of the given statuses) that have `address` as a registered contact, with
    the contact's id; the address a mail was sent to identifies nobody by itself (D53). Empty when none."""
    if not address or not supplier_codes:
        return []
    rows = db.execute(
        text(
            "select c.supplier_code, min(c.id) from supplier_contact c join supplier s on s.code = c.supplier_code "
            "where lower(c.email) = :a and c.supplier_code = any(:codes) "
            "and (cast(:st as text[]) is null or s.status = any(cast(:st as text[]))) "
            "group by c.supplier_code order by c.supplier_code"
        ),
        {"a": address.strip().lower(), "codes": list(supplier_codes), "st": list(statuses) if statuses else None},
    ).all()
    return [(str(r[0]), int(r[1])) for r in rows]
