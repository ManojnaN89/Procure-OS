"""E52: a supplier's bank account is paid into only after a second person verified it (DF-03, F1), on the main demo
company, against an oracle from the raw files.

The dataset's suppliers each get one approved invoice due in the run window. Two suppliers have their bank account
changed (one asked for by a buyer, one by a finance person; a third person approves each): the oracle says a changed
account is unverified, so those two suppliers are never planned into a run, never advanced and never in a bank file,
while every other supplier is paid its invoice total into the account the dataset gives it. Verifying: the person who
asked for the account is refused, so are a buyer, an approver and another company's finance person; a method outside
`bank_verification_methods` (the plan's default list when the dataset has no row), no proof and a wrong file type are
refused; Finance verifies with an allowed method and a proof, and only then is the supplier planned. A bank change after
a run is approved and its file made takes that supplier's payment out, the run total becomes the sum of the payments
left (oracle: the remaining suppliers' invoice totals) and the file is void. Lists and profiles show the last four
digits only; no audit row ever holds a whole account number. Four rules are broken on purpose and the eval must notice
each.
"""

import csv
import inspect
import io
from datetime import timedelta
from decimal import Decimal
from typing import Any

import env
import lib
from fastapi.testclient import TestClient
from lib import D, Outcome, rows
from replay import insert
from sqlalchemy import text

ID = "E52"
TITLE = "Bank accounts: verified by a second person against a proof before anything is paid into them"
REQUIREMENT = (
    "DF-03 / F1: a new or changed supplier bank account is unverified; it is never planned into a payment run, advanced "
    "or written to a bank file until a person other than the one who entered or asked for it verifies it (Finance, an "
    "allowed method from `bank_verification_methods`, a proof file); a bank change after a run is approved takes that "
    "supplier out, recomputes the total and voids a file already made; lists show the last four digits; the audit never "
    "holds the whole number; another company sees nothing"
)
PLAN_METHODS = ["cancelled_cheque", "bank_letter", "penny_drop"]  # the plan's default when the company sets none
PDF = ("proof.pdf", b"%PDF-1.4 bank letter for the eval", "application/pdf")


def allowed_methods() -> list[str]:
    found = next((r["value"] for r in rows("master", "setting") if r["key"] == "bank_verification_methods"), None)
    return [w.strip() for w in found.split(",") if w.strip()] if found else PLAN_METHODS


def new_account(code: str, n: int) -> tuple[str, str]:
    """A fresh, well-formed account and IFSC for a supplier (12 digits; generated data stays synthetic)."""
    return f"0009{n:02d}{code[-3:]}{n:03d}", f"HDFC000{n:04d}"


def masked(number: str) -> str:
    return "****" + number[-4:]


def run(ctx: lib.Ctx) -> Outcome:  # noqa: C901 - one scenario, step by step
    from p2p.invoices import payment
    from p2p.main import app
    from p2p.masters import suppliers
    from p2p.platform import proposals, sod, tenants, users

    out = Outcome(True)
    lib.capture_mail()
    world = lib.build_world("e52", with_stock=False)
    db, slug, client = world.db, world.slug, TestClient(app)
    buyer, finance, approver = world.logins["Buyer"], world.logins["Finance"], world.logins["Approver"]
    finance2 = users.create_user(slug, f"finance2.{slug}@eval.example", lib.PASSWORD, "Second Finance", "Finance")
    hdr = {r: world.headers(r) for r in ("Buyer", "Finance", "Approver")}
    from p2p.platform.tokens import create_access_token

    hdr["Finance2"] = {"Authorization": f"Bearer {create_access_token(finance2, slug, 'Finance')}"}

    # ---- the oracle's inputs: the dataset's accounts and one invoice per supplier --------------------------------
    dataset_bank = {r["supplier_code"]: r for r in rows("master", "supplier_bank_account")}
    every_number = {r["account_no"] for r in dataset_bank.values()}
    first_invoice = {}
    for r in rows("history", "invoice"):
        if r["type"] == "invoice":
            first_invoice.setdefault(r["supplier_code"], r)
    planted: dict[str, tuple[str, Decimal]] = {}  # supplier -> (invoice no, total)
    for code, r in sorted(first_invoice.items()):
        no = f"E52-{code}"
        insert(db, "invoice", r, {
            "no": no, "status": "approved", "payment_block": "none", "due_date": lib.TODAY + timedelta(days=2),
            "invoice_date": lib.TODAY - timedelta(days=40), "received_on": lib.TODAY - timedelta(days=30),
            "supplier_invoice_no": f"E52/{code}", "irn": None, "ack_no": None, "fy": "2026-27", "dedupe_key": f"E52{code}",
        })  # fmt: skip
        planted[code] = (no, D(r["total"]))
    db.commit()
    stored = {
        r[0]: (r[1], bool(r[2]))
        for r in db.execute(
            text("select supplier_code, account_no, coalesce(verified, false) from supplier_bank_account")
        )
    }
    want_stored = {c: (r["account_no"], r["verified"] == "true") for c, r in dataset_bank.items()}
    if stored != want_stored:
        out.find("high", f"the imported bank accounts are not the dataset's: {stored} vs {want_stored}")

    def account_of(code: str) -> dict[str, Any]:
        db.rollback()
        return dict(
            db.execute(
                text(
                    "select id, account_no, coalesce(verified, false) as verified, submitted_by, verified_by, "
                    "verification_method, evidence_document_id from supplier_bank_account where supplier_code = :c"
                ),
                {"c": code},
            )
            .mappings()
            .one()
        )

    def change_bank(code: str, n: int, asker: int, *, api: bool) -> str:
        """Someone asks for a new account for a supplier in use and the approver approves it. Returns the number."""
        number, ifsc = new_account(code, n)
        every_number.add(number)
        if api:
            got = client.post(
                f"/api/vendors/{code}/bank", json={"account_no": number, "ifsc": ifsc, "reason": "moved bank"},
                headers=hdr["Buyer"],
            )  # fmt: skip
            pid = got.json().get("proposal")
        else:
            pid = suppliers.change_bank(db, code, {"account_no": number, "ifsc": ifsc}, sod.user(asker), "moved bank")[
                "proposal"
            ]
        proposals.approve(db, int(pid), approver)
        return number

    suppliers_all = sorted(planted)
    by_buyer, by_finance2, removed_later = "SUP-003", "SUP-005", "SUP-001"
    if not {by_buyer, by_finance2, removed_later} <= set(suppliers_all):
        out.find("high", f"the dataset has no invoice for every supplier the eval needs: {suppliers_all}")
        return out

    # 1. an approved bank change makes an unverified account, submitted by the one who asked (not the approver)
    change_bank(by_buyer, 1, buyer, api=True)
    change_bank(by_finance2, 2, finance2, api=False)
    for code, asker in ((by_buyer, buyer), (by_finance2, finance2)):
        acc = account_of(code)
        if acc["verified"] or acc["submitted_by"] != str(asker):
            out.find("high", f"{code} after an approved change: verified {acc['verified']}, submitted by "
                             f"{acc['submitted_by']} (the oracle: unverified, by {asker})")  # fmt: skip
    unverified = {by_buyer, by_finance2}
    out.metrics["approved bank changes left unverified"] = sum(not account_of(c)["verified"] for c in unverified)

    # 2. an unverified supplier is never planned, never advanced, never in a file
    db.execute(
        text(
            "insert into po (no, po_type, supplier_code, ship_to_location, status, payment_terms_code, taxable, total) values "
            "('E52-ADV-1', 'standard', :a, :loc, 'acknowledged', 'ADV30N30', 1000, 1180), "
            "('E52-ADV-2', 'standard', :b, :loc, 'acknowledged', 'ADV30N30', 1000, 1180)"
        ),
        {"a": by_buyer, "b": removed_later, "loc": lib.location()},
    )
    db.commit()
    advanced = {a["supplier_code"] for a in payment.due_advances(db)}
    named = {a["supplier_code"] for a in payment.due_advances(db, ["E52-ADV-1"])}
    out.metrics["advances due (unverified supplier / verified supplier)"] = (
        f"{by_buyer in advanced or bool(named)} / {removed_later in advanced}"
    )
    if by_buyer in advanced or named or removed_later not in advanced:
        out.find("high", f"advances: {advanced}, named {named}; only {removed_later} has a verified account")
    db.execute(text("delete from po where no like 'E52-ADV-%'"))
    db.commit()

    due = {d["supplier_code"] for d in payment.due_invoices(db, lib.TODAY)}
    want_due = {c for c in suppliers_all if dataset_bank[c]["verified"] == "true"} - unverified
    if due != want_due:
        out.find("high", f"planned for payment: {sorted(due)}, the oracle {sorted(want_due)}")
    run1 = payment.propose_run(db, lib.TODAY, finance, include_advances=False)
    paid = dict(db.execute(text("select supplier_code, amount from payment where run_no = :r"), {"r": run1}).all())
    want_paid = {c: planted[c][1] for c in want_due}
    run_total = D(db.execute(text("select total from payment_run where no = :r"), {"r": run1}).scalar_one())
    out.metrics["run 1: suppliers paid / oracle, total / oracle"] = (
        f"{len(paid)} / {len(want_paid)}, {run_total} / {sum(want_paid.values())}"
    )
    if paid != want_paid or run_total != sum(want_paid.values()):
        out.find("high", f"run {run1}: paid {paid}, total {run_total}; the oracle {want_paid}")
    proposals.approve(
        db, db.execute(text("select proposal_id from payment_run where no = :r"), {"r": run1}).scalar_one(), approver
    )
    file1 = payment.generate_file(db, run1, finance)
    lines = list(csv.DictReader(io.StringIO(file1)))
    in_file = {ln["account_no"] for ln in lines}
    want_file = {dataset_bank[c]["account_no"] for c in want_due}
    if in_file != want_file or {D(ln["amount"]) for ln in lines} != set(want_paid.values()):
        out.find("high", f"bank file of {run1}: accounts {sorted(in_file)}, the oracle {sorted(want_file)}")
    out.metrics["bank file lines into an unverified account"] = len(in_file - want_file)

    # 3. verifying: who may, how, against what
    url = lambda code: f"/api/vendors/{code}/bank/{account_of(code)['id']}/verify"  # noqa: E731
    methods = allowed_methods()
    form = {"method": methods[0]}
    pdf = {"file": PDF}
    other = env.slug("e52x")
    assert tenants.create_tenant(other, load_reference=False).ok
    stranger = users.create_user(other, f"stranger.{other}@eval.example", lib.PASSWORD, "Elsewhere", "Finance")
    h_other = {"Authorization": f"Bearer {create_access_token(stranger, other, 'Finance')}"}
    refusals = {
        "the buyer who asked": (
            client.post(url(by_buyer), data=form, files=pdf, headers=hdr["Buyer"]).status_code,
            403,
        ),
        "an approver": (client.post(url(by_buyer), data=form, files=pdf, headers=hdr["Approver"]).status_code, 403),
        "the finance person who asked": (
            client.post(url(by_finance2), data=form, files=pdf, headers=hdr["Finance2"]).status_code,
            403,
        ),
        "a method not allowed": (
            client.post(url(by_finance2), data={"method": "phone_call"}, files=pdf, headers=hdr["Finance"]).status_code,
            422,
        ),
        "no proof (empty file)": (
            client.post(
                url(by_finance2), data=form, files={"file": ("p.pdf", b"", "application/pdf")}, headers=hdr["Finance"]
            ).status_code,
            422,
        ),  # fmt: skip
        "a proof that is not a PDF or picture": (
            client.post(
                url(by_finance2),
                data=form,
                files={"file": ("p.exe", b"MZ", "application/x-msdownload")},
                headers=hdr["Finance"],
            ).status_code,
            422,
        ),  # fmt: skip
        "another company's finance person": (
            client.post(url(by_finance2), data=form, files=pdf, headers=h_other).status_code,
            404,
        ),
        "another company reading the profile": (
            client.get(f"/api/vendors/{by_finance2}/profile", headers=h_other).status_code,
            404,
        ),
    }
    try:  # the service refuses the asker too, not only the screen
        suppliers.verify_bank(
            db,
            by_finance2,
            account_of(by_finance2)["id"],
            methods[0],
            suppliers.Evidence(slug, PDF[1], PDF[0], PDF[2]),
            finance2,
        )
        refusals["the asker, at the service"] = (200, 422)
    except suppliers.SupplierError as exc:
        db.rollback()
        refusals["the asker, at the service"] = (403 if str(exc).startswith("segregation") else 422, 403)
    out.metrics["verification attempts refused as the oracle says"] = (
        f"{sum(g == w for g, w in refusals.values())} of {len(refusals)}"
    )
    for name, (got, want) in refusals.items():
        if got != want:
            out.find("high", f"verifying by {name}: answered {got}, expected {want}")
    if any(account_of(c)["verified"] for c in unverified):
        out.find("high", "a refused verification left an account verified")
    tenants.drop_tenant(other)

    done = {}
    for code, method in ((by_buyer, methods[0]), (by_finance2, methods[-1])):
        got = client.post(url(code), data={"method": method}, files=pdf, headers=hdr["Finance"])
        acc = account_of(code)
        evidence = db.execute(
            text("select linked_type, linked_id from document where id = :d"), {"d": acc["evidence_document_id"]}
        ).first()
        done[code] = got.status_code == 200 and (
            acc["verified"],
            acc["verified_by"],
            acc["verification_method"],
            tuple(evidence or ()),
        ) == (True, str(finance), method, ("supplier_bank_account", str(acc["id"])))
        if not done[code]:
            out.find("high", f"Finance verifying {code} with {method}: {got.status_code} {got.text[:200]}, {acc}")
    again = client.post(url(by_buyer), data=form, files=pdf, headers=hdr["Finance"]).status_code
    if again != 409:
        out.find("high", f"verifying an account already verified answered {again}, expected 409")
    out.metrics["verified by Finance with method and proof"] = f"{sum(done.values())} of {len(done)}"

    # once verified, they are planned (the others are already in run 1)
    run2 = payment.propose_run(db, lib.TODAY, finance, include_advances=False)
    paid2 = dict(db.execute(text("select supplier_code, amount from payment where run_no = :r"), {"r": run2}).all())
    want2 = {c: planted[c][1] for c in unverified}
    if paid2 != want2:
        out.find("high", f"after verification, run {run2} pays {paid2}; the oracle {want2}")
    out.metrics["run 2 after verification: suppliers paid / oracle"] = f"{len(paid2)} / {len(want2)}"

    # 4. a bank change after run 1 was approved and its file made: that supplier leaves, the total is recomputed, the
    # file is void and the next file has no line for it
    change_bank(removed_later, 3, buyer, api=True)
    db.rollback()
    status, total, file_text = db.execute(
        text("select status, total, file_text from payment_run where no = :r"), {"r": run1}
    ).one()
    left = dict(db.execute(text("select supplier_code, amount from payment where run_no = :r"), {"r": run1}).all())
    want_left = {c: v for c, v in want_paid.items() if c != removed_later}
    voided = db.execute(
        text("select after from audit_event where action = 'payment_run.supplier_removed' and entity_id = :r"),
        {"r": run1},
    ).scalar()
    out.metrics["run 1 after the change: status / total / oracle / file void"] = (
        f"{status} / {total} / {sum(want_left.values())} / {file_text is None}"
    )
    if (
        status != "approved" or left != want_left or D(total) != sum(left.values()) or D(total) != sum(want_left.values())
        or file_text is not None or not voided or voided.get("file_voided") is not True
    ):  # fmt: skip
        out.find("high", f"run {run1} after {removed_later}'s bank changed: {status}, {total}, {left}, file "
                         f"{'kept' if file_text else 'void'}, audit {voided}; the oracle {want_left}")  # fmt: skip
    file1b = payment.generate_file(db, run1, finance)
    accounts_b = {ln["account_no"] for ln in csv.DictReader(io.StringIO(file1b))}
    if accounts_b != {dataset_bank[c]["account_no"] for c in want_left}:
        out.find("high", f"the new file of {run1} pays {sorted(accounts_b)}")
    if removed_later in {d["supplier_code"] for d in payment.due_invoices(db, lib.TODAY)}:
        out.find("high", f"{removed_later} is planned again before its new account is verified")

    # 5. lists and profiles show four digits; the audit never holds a whole number
    shown = _shown(client, hdr, by_buyer, account_of(by_buyer)["account_no"])
    out.metrics["profiles and lists with a whole account number"] = shown or "none"
    if shown:
        out.find("high", f"a whole account number is shown: {shown}")
    listed = client.get("/api/admin/proposals?status=approved", headers=hdr["Approver"]).text
    leaked = sorted(n for n in every_number if n in listed)
    out.metrics["whole account numbers in the proposals list"] = len(leaked)
    if leaked:
        out.find(
            "medium",
            f"GET /api/admin/proposals (the approvals list, shown on the admin Approvals screen) returns the payload of "
            f"'supplier.set_bank' proposals with the whole account number ({', '.join(masked(n) for n in leaked)} in "
            "full); its summary is masked, the payload is not",
        )
    db.rollback()
    trail = " ".join(
        str(r)
        for r in db.execute(text("select before::text, after::text, reason, rule_version from audit_event")).all()
    )
    in_audit = sorted(n for n in every_number if n in trail)
    out.metrics["whole account numbers in the audit log"] = len(in_audit)
    if in_audit:
        out.find("high", f"the audit log holds whole account numbers: {[masked(n) for n in in_audit]}")
    trail_rows = {
        a: db.execute(text("select count(*) from audit_event where action = :a"), {"a": a}).scalar_one()
        for a in ("supplier.bank_change", "supplier.bank_verify", "payment_run.supplier_removed")
    }
    out.metrics["audit rows"] = trail_rows
    if not all(trail_rows.values()):
        out.find("high", f"missing audit rows: {trail_rows}")

    out.metrics["mutation checks (rule broken on purpose: caught?)"] = _mutations(
        db, slug, client, hdr, run2, change_bank, account_of, (finance, finance2, approver), out
    )
    return out


def _shown(client: Any, hdr: dict, code: str, number: str) -> list[str]:
    """Where a whole account number shows to someone who is not verifying it right now."""
    found = []
    for role in ("Buyer", "Approver", "Finance"):
        profile = client.get(f"/api/vendors/{code}/profile", headers=hdr[role])
        bank = profile.json().get("bank") or {}
        if number in profile.text or bank.get("account") != masked(number):
            found.append(f"profile as {role}: {bank}")
        if number in client.get("/api/vendors", headers=hdr[role]).text:
            found.append(f"vendor list as {role}")
    return found


def _mutations(
    db: Any, slug: str, client: Any, hdr: dict, run2: str, change_bank: Any, account_of: Any, people: tuple,
    out: Outcome,
) -> dict[str, str]:  # fmt: skip
    from p2p.invoices import payment
    from p2p.masters import suppliers
    from p2p.platform import proposals

    finance, finance2, approver = people
    results: dict[str, str] = {}

    # the lists show the whole number
    saved_mask = suppliers.mask
    suppliers.mask = lambda account_no: str(account_no)
    try:
        number = account_of("SUP-003")["account_no"]
        results["the profile shows the whole number"] = (
            "caught" if _shown(client, hdr, "SUP-003", number) else "NOT caught"
        )
    finally:
        suppliers.mask = saved_mask

    # the one who asked for the account can verify it (the change forgets who asked)
    saved_set = suppliers._set_bank
    suppliers._set_bank = lambda db_, code, a, i, actor, reason, submitted_by=None: saved_set(
        db_, code, a, i, actor, reason, submitted_by="nobody"
    )
    try:
        change_bank("SUP-006", 4, finance2, api=False)
    finally:
        suppliers._set_bank = saved_set
    got = client.post(
        f"/api/vendors/SUP-006/bank/{account_of('SUP-006')['id']}/verify",
        data={"method": allowed_methods()[0]}, files={"file": PDF}, headers=hdr["Finance2"],
    )  # fmt: skip
    results["the asker verifies their own account"] = "caught" if got.status_code != 403 else "NOT caught"

    # a bank change after approval leaves the supplier in the run and the file as it was
    proposals.approve(
        db, db.execute(text("select proposal_id from payment_run where no = :r"), {"r": run2}).scalar_one(), approver
    )
    payment.generate_file(db, run2, finance)
    saved_take = payment.take_out_supplier
    payment.take_out_supplier = lambda *a, **k: []
    try:
        change_bank("SUP-005", 5, finance2, api=False)
    finally:
        payment.take_out_supplier = saved_take
    db.rollback()
    still = db.execute(
        text("select count(*) from payment where run_no = :r and supplier_code = 'SUP-005'"), {"r": run2}
    ).scalar_one()
    kept = db.execute(text("select file_text is not null from payment_run where no = :r"), {"r": run2}).scalar_one()
    results["a bank change leaves the run and its file alone"] = "caught" if still or kept else "NOT caught"

    # an unverified supplier is planned: the verified test taken out of the plan
    source = inspect.getsource(payment.due_invoices)
    clause = (
        '"and exists (select 1 from supplier_bank_account b where b.supplier_code = i.supplier_code and b.verified) "'
    )
    if clause in source:
        space: dict[str, Any] = {}
        exec(compile(inspect.cleandoc("\n" + source).replace(clause, '""'), "mutant", "exec"), payment.__dict__, space)  # noqa: S102
        saved_due = payment.due_invoices
        payment.due_invoices = space["due_invoices"]
        try:
            db.execute(text("delete from payment_allocation where invoice_no = 'E52-SUP-005'"))  # rolled back below
            planned = {d["supplier_code"] for d in payment.due_invoices(db, lib.TODAY)}
            results["an unverified account is planned"] = "caught" if "SUP-005" in planned else "NOT caught"
        finally:
            payment.due_invoices = saved_due
            db.rollback()
    else:
        results["an unverified account is planned"] = "could not mutate (the query changed)"
    for name, verdict in results.items():
        if verdict != "caught":
            out.find("high", f"mutation '{name}' was not caught: {verdict}")
    return results
