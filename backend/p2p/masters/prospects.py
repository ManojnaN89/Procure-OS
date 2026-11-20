"""Prospective suppliers (D-03, task 4.4): registered by a buyer to quote before full onboarding.

A prospect may be invited to an RFQ and quote (R1). Its quote counts toward the minimum only after a person other than
the buyer who registered or invited it checks its identity (R2, R3, R11), and while no related-party flag on it is open
(R8). A blocked supplier cannot come back under another name: the same GSTIN, PAN or bank account as any supplier is
refused, and a contact, address or mail domain shared with a blocked supplier is always a flag (R7). Flags are for
independent review, never an automatic verdict of fraud. An identity check is not an activation: activation stays the
proposal with documents (R5), and bank verification stays a separate manual step before payment (R6).
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.approvals.workflow import org_code_of
from p2p.formulas import supplier_master as rules
from p2p.formulas.calendar import business_today
from p2p.platform import audit
from p2p.platform.sod import user

DEFAULT_METHODS = "pan_and_gstin_online,gst_certificate_seen,call_back_registered_number"
DEFAULT_PUBLIC = "gmail.com,yahoo.com,yahoo.co.in,outlook.com,hotmail.com,rediffmail.com,icloud.com"


class ProspectError(Exception):
    def __init__(self, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Flag:
    kind: str
    other_ref: str
    detail: str


def person(db: Session, user_id: int) -> str:
    return org_code_of(db, user_id) or f"user:{user_id}"


def _setting(db: Session, key: str, default: str) -> str:
    value = db.execute(text("select value from org_setting where key = :k"), {"k": key}).scalar()
    return str(value) if value else default


def methods(db: Session) -> list[str]:
    return [m.strip() for m in _setting(db, "identity_check_methods", DEFAULT_METHODS).split(",") if m.strip()]


def _clean(data: dict[str, Any]) -> dict[str, Any]:
    out = {k: (v.strip() if isinstance(v, str) else v) for k, v in data.items()}
    for key in ("pan", "gstin", "ifsc"):
        if out.get(key):
            out[key] = str(out[key]).upper()
    if out.get("email"):
        out["email"] = str(out["email"]).lower()
    return out


def refusal(db: Session, data: dict[str, Any]) -> str | None:
    """Why a supplier cannot be registered at all (R7): the same GSTIN, PAN or bank account as any supplier, or a name
    on the sanctions list. None when it may be."""
    checks = [
        ("gstin", "select code from supplier where gstin = :v", "GSTIN"),
        ("pan", "select code from supplier where pan = :v", "PAN (a PAN is one legal person)"),
        ("account_no", "select supplier_code from supplier_bank_account where account_no = :v", "bank account"),
    ]
    for key, sql, what in checks:
        if data.get(key) and (found := db.execute(text(sql + " limit 1"), {"v": data[key]}).scalar()):
            status = db.execute(text("select status from supplier where code = :c"), {"c": found}).scalar()
            return f"same {what} as {found}" + (f", which is {status}" if status == "blocked" else "")
    names = {str(data.get("legal_name") or "").lower(), str(data.get("trade_name") or "").lower()} - {""}
    for name, aliases in db.execute(text("select name, aliases from sanction_entry")):
        listed = {str(name).lower()} | {a.strip().lower() for a in str(aliases or "").split(";") if a.strip()}
        if names & listed:
            return f"the name is on the sanctions list ({name})"
    return None


def flags_for(db: Session, data: dict[str, Any], own: str | None = None) -> list[Flag]:
    """Possible conflicts to review (R8): contacts, address or mail domain shared with another supplier or a member of
    staff. A match with a blocked supplier is always flagged as such."""
    status = {str(r[0]): str(r[1]) for r in db.execute(text("select code, status from supplier"))}
    out: dict[tuple[str, str], Flag] = {}

    def add(kind: str, other: str, detail: str) -> None:
        if other == own:
            return
        if status.get(other) == "blocked":
            kind = "blocked_match"
        out.setdefault((kind, other), Flag(kind, other, detail))

    email, phone = data.get("email"), data.get("phone")
    for code, c_email, c_phone in db.execute(text("select supplier_code, lower(email), phone from supplier_contact")):
        if email and c_email == email:
            add("same_contact", str(code), f"contact email {email} is also {code}'s")
        if phone and c_phone == phone:
            add("same_contact", str(code), f"contact phone {phone} is also {code}'s")
    for code, u_email, u_phone in db.execute(text("select code, lower(email), phone from org_user")):
        if (email and u_email == email) or (phone and u_phone == phone):
            out.setdefault(
                ("staff_contact", str(code)), Flag("staff_contact", str(code), f"a contact is staff member {code}'s")
            )
    if data.get("address") and data.get("pincode"):
        for (code,) in db.execute(
            text(
                "select distinct supplier_code from supplier_address where lower(trim(address)) = lower(trim(:a)) "
                "and pincode = :p"
            ),
            {"a": data["address"], "p": data["pincode"]},
        ):
            add("same_address", str(code), f"address {data['address']}, {data['pincode']} is also {code}'s")
    domain = email.split("@", 1)[1] if email and "@" in email else None
    public = {d.strip().lower() for d in _setting(db, "public_mail_domains", DEFAULT_PUBLIC).split(",")}
    if domain and domain not in public:
        for (code,) in db.execute(
            text("select distinct supplier_code from supplier_contact where lower(split_part(email, '@', 2)) = :d"),
            {"d": domain},
        ):
            if not any(other == str(code) for _, other in out):
                add("same_domain", str(code), f"mail domain {domain} is also {code}'s")
    return list(out.values())


def register(db: Session, data: dict[str, Any], user_id: int) -> tuple[str, list[Flag]]:
    """Save a prospect: identity, address, one contact, the category it is to quote for, optionally a bank account
    (unverified). Refused (nothing saved) on a duplicate or a sanctioned name; created with flags on a possible
    conflict."""
    data = _clean(data)
    data["gst_reg_type"] = data.get("gst_reg_type") or ("regular" if data.get("gstin") else "unregistered")
    problems = {**rules.check_identity(data), **rules.check_contact(data)}
    for key in ("email", "phone", "address", "pincode", "category"):
        if not data.get(key):
            problems[key] = "required for a prospect"
    if data.get("account_no") or data.get("ifsc"):
        problems.update(rules.check_bank(data))
    if (
        data.get("category")
        and db.execute(text("select 1 from item_category where code = :c"), {"c": data["category"]}).first() is None
    ):
        problems["category"] = f"there is no category {data['category']}"
    if problems:
        raise ProspectError("; ".join(f"{k}: {v}" for k, v in problems.items()))
    if why := refusal(db, data):
        audit.record(
            db, user(user_id), "supplier.prospect_refused", "supplier", data.get("legal_name") or "",
            after={"pan": data.get("pan"), "gstin": data.get("gstin")}, reason=why,
        )  # fmt: skip
        db.commit()
        raise ProspectError(f"refused: {why}")
    flags = flags_for(db, data)
    me = person(db, user_id)
    code = rules.next_code([str(r[0]) for r in db.execute(text("select code from supplier"))])
    gstin = data.get("gstin") or None
    state = rules.state_for(gstin, data.get("state_code"))
    db.execute(
        text(
            "insert into supplier (code, legal_name, trade_name, legal_form, pan, gst_reg_type, gstin, state_code, "
            "msme_category, payment_terms_code, status, registered_by) values "
            "(:c, :ln, :tn, :lf, :pan, :gt, :gstin, :st, 'none', :terms, 'prospect', :by)"
        ),
        {
            "c": code, "ln": data["legal_name"], "tn": data.get("trade_name") or data["legal_name"],
            "lf": data.get("legal_form") or None, "pan": data["pan"], "gt": data["gst_reg_type"], "gstin": gstin,
            "st": state, "terms": data.get("payment_terms_code") or None, "by": me,
        },
    )  # fmt: skip
    db.execute(
        text(
            "insert into supplier_address (supplier_code, type, gstin, address, city, state_code, pincode) "
            "values (:c, 'registered', :g, :a, :ci, :st, :pin)"
        ),
        {"c": code, "g": gstin, "a": data["address"], "ci": data.get("city"), "st": state, "pin": data["pincode"]},
    )
    db.execute(
        text(
            "insert into supplier_contact (supplier_code, name, role, email, phone, is_primary) "
            "values (:c, :n, 'sales', :e, :p, true)"
        ),
        {"c": code, "n": data.get("contact_name") or "Sales Desk", "e": data["email"], "p": data["phone"]},
    )
    # the category it is to quote for; for a prospect this is what it may be invited to, not an approval
    db.execute(
        text("insert into supplier_category (supplier_code, category_code, approved_from) values (:c, :cat, :d)"),
        {"c": code, "cat": data["category"], "d": business_today()},
    )
    if data.get("account_no"):
        db.execute(
            text(
                "insert into supplier_bank_account (supplier_code, bank_code, account_no, ifsc, verified, submitted_by) "
                "values (:c, :b, :a, :i, false, :u)"
            ),
            {"c": code, "b": str(data["ifsc"])[:4], "a": data["account_no"], "i": data["ifsc"], "u": str(user_id)},
        )
    db.execute(
        text(
            "insert into supplier_status_history (supplier_code, at, from_status, to_status, reason, by_user) "
            "values (:c, now(), null, 'prospect', 'Registered to quote', :u)"
        ),
        {"c": code, "u": me},
    )
    _raise_flags(db, code, flags, user_id)
    audit.record(
        db, user(user_id), "supplier.prospect_add", "supplier", code,
        after={"legal_name": data["legal_name"], "pan": data["pan"], "gstin": gstin, "category": data["category"],
               "flags": [f.kind for f in flags]},
    )  # fmt: skip
    db.commit()
    return code, flags


def _raise_flags(db: Session, code: str, flags: list[Flag], user_id: int | None) -> None:
    for f in flags:
        fid = db.execute(
            text(
                "insert into supplier_flag (supplier_code, kind, other_ref, detail) values (:c, :k, :o, :d) returning id"
            ),
            {"c": code, "k": f.kind, "o": f.other_ref, "d": f.detail},
        ).scalar_one()
        who = user(user_id) if user_id else audit.SYSTEM
        audit.record(
            db, who, "supplier.flag_raised", "supplier", code, after={"flag": fid, "kind": f.kind, "other": f.other_ref}
        )


def open_flags(db: Session, code: str) -> list[dict[str, Any]]:
    return [
        dict(r)
        for r in db.execute(
            text("select * from supplier_flag where supplier_code = :c and cleared_at is null order by id"), {"c": code}
        ).mappings()
    ]


def all_flags(db: Session, code: str) -> list[dict[str, Any]]:
    return [
        {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in dict(r).items()}
        for r in db.execute(
            text("select * from supplier_flag where supplier_code = :c order by id"), {"c": code}
        ).mappings()
    ]


def _interested(db: Session, code: str) -> set[str]:
    """Who registered or invited the prospect: they never check its identity or clear its flags (R3)."""
    row = db.execute(text("select registered_by, invited_by from supplier where code = :c"), {"c": code}).first()
    people = {str(x) for x in (row or ()) if x}
    for (by,) in db.execute(
        text("select invited_by from rfq_supplier where supplier_code = :c and invited_by is not null"), {"c": code}
    ):
        people.add(str(by))
    return people


def _not_interested(db: Session, code: str, user_id: int, what: str) -> None:
    if {person(db, user_id), f"user:{user_id}", str(user_id)} & _interested(db, code):
        raise ProspectError(f"segregation of duties: the buyer who registered or invited {code} cannot {what}", 403)


def check_identity(
    db: Session, code: str, user_id: int, *, method: str, document_ids: list[int], note: str
) -> dict[str, Any]:
    """Record the independent basic identity check of a prospect (R2, R3, R11). Not an activation."""
    row = db.execute(
        text("select status, identity_checked_at from supplier where code = :c for update"), {"c": code}
    ).first()
    if row is None:
        raise ProspectError(f"supplier {code} does not exist", 404)
    _not_interested(db, code, user_id, "check its identity")
    if row[0] != "prospect":
        raise ProspectError(f"{code} is {row[0]}, not a prospect", 409)
    if row[1] is not None:
        raise ProspectError(f"{code}'s identity is already checked", 409)
    if method not in methods(db):
        raise ProspectError(f"the method must be one of {', '.join(methods(db))}")
    if not document_ids:
        raise ProspectError("attach at least one proof (a PAN card or GST certificate scan)")
    known = {
        int(r[0])
        for r in db.execute(
            text(
                "select d.id from document d where d.id = any(:ids) and ((d.linked_type = 'supplier' and d.linked_id = :c) "
                "or (d.linked_type = 'supplier_document' and d.linked_id in "
                "(select id::text from supplier_document where supplier_code = :c)))"
            ),
            {"ids": document_ids, "c": code},
        )
    }
    if missing := sorted(set(document_ids) - known):
        raise ProspectError(f"no proof file {', '.join(map(str, missing))} on {code}")
    if not note.strip():
        raise ProspectError("a note is needed")
    me, now = person(db, user_id), datetime.now(UTC)
    db.execute(
        text(
            "update supplier set identity_checked_by = :by, identity_checked_at = :at, identity_method = :m, "
            "identity_document_ids = :docs, identity_note = :n where code = :c"
        ),
        {"by": me, "at": now, "m": method, "docs": document_ids, "n": note.strip(), "c": code},
    )
    audit.record(
        db, user(user_id), "supplier.identity_check", "supplier", code,
        after={"method": method, "documents": document_ids}, reason=note,
    )  # fmt: skip
    db.commit()
    return {"checked_by": me, "checked_at": now.isoformat(), "method": method}


def clear_flag(db: Session, code: str, flag_id: int, user_id: int, reason: str) -> dict[str, Any]:
    """An independent person clears a flag with a reason (R8)."""
    if not reason.strip():
        raise ProspectError("a reason is needed")
    row = db.execute(
        text("select * from supplier_flag where id = :i and supplier_code = :c for update"), {"i": flag_id, "c": code}
    ).mappings().first()  # fmt: skip
    if row is None:
        raise ProspectError(f"{code} has no flag {flag_id}", 404)
    _not_interested(db, code, user_id, "clear its flags")
    if row["kind"] == "staff_contact" and row["other_ref"] == person(db, user_id):
        raise ProspectError("segregation of duties: the member of staff named by the flag cannot clear it", 403)
    if row["cleared_at"] is not None:
        raise ProspectError(f"flag {flag_id} is already cleared", 409)
    me = person(db, user_id)
    db.execute(
        text("update supplier_flag set cleared_by = :by, cleared_at = now(), clear_reason = :r where id = :i"),
        {"by": me, "r": reason.strip(), "i": flag_id},
    )
    audit.record(db, user(user_id), "supplier.flag_cleared", "supplier", code, after={"flag": flag_id}, reason=reason)
    db.commit()
    return {"id": flag_id, "cleared_by": me, "cleared": True}


def identity(db: Session, code: str) -> dict[str, Any]:
    """The identity state for the screen and the APIs."""
    row = db.execute(
        text(
            "select status, registered_by, invited_by, identity_checked_by, identity_checked_at, identity_method, "
            "identity_document_ids, identity_note from supplier where code = :c"
        ),
        {"c": code},
    ).mappings().first()  # fmt: skip
    if row is None:
        return {}
    flags = all_flags(db, code)
    return {
        "prospect": row["status"] == "prospect",
        "checked": row["identity_checked_at"] is not None,
        "checked_by": row["identity_checked_by"],
        "checked_at": row["identity_checked_at"].isoformat() if row["identity_checked_at"] else None,
        "method": row["identity_method"],
        "documents": list(row["identity_document_ids"] or []),
        "note": row["identity_note"],
        "registered_by": row["registered_by"],
        "invited_by": sorted(_interested(db, code) - {row["registered_by"]} - {None}),
        "flags": flags,
        "open_flags": sum(1 for f in flags if not f["cleared_at"]),
    }


def counts_as_checked(db: Session, code: str) -> tuple[bool, str | None]:
    """Whether a prospect's quote may count: identity checked and no open flag. (True, None) for any other status."""
    row = db.execute(text("select status, identity_checked_at from supplier where code = :c"), {"c": code}).first()
    if row is None or row[0] != "prospect":
        return True, None
    if row[1] is None:
        return False, "identity not checked: does not count"
    if open_flags(db, code):
        return False, "flagged for review: does not count"
    return True, None
