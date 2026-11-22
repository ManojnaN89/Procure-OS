"""E81: the cutover import of open purchase orders, receipts, invoices and advances (task 7.4), written from the block
0d spec only.

Both demo profiles are made in setup and moved to cutover by the platform team. The import is refused in setup (409).
On the main demo company, `cutover-bad/` (the good files plus the planted rows of spec section 6) is dry-run through the
API (nothing written: rows and audit counted) and then loaded with the CLI: every planted row is left out with the reason
`truth/truth_cutover.csv` gives, the control totals fail and readiness `cutover_import` fails. Then `cutover/` is dry-run,
loaded through the API and loaded again with the CLI (the second loads nothing and counts every document `already`).
Brightwave loads its `cutover/` the same way.

The oracle reads the cutover files and the masters: every open document of the files is in the company with its number;
PO totals recomputed line by line (GST by the supplier's and the ship-to state, in paise; the file's total within Rs 1.00);
the carrier request `CUT-<po_no>` holds each PO; receipts set `received_qty` to what was accepted and post **no** stock
movement (stock per item unchanged by every run); received not invoiced on 31 Oct 2026 equals the truth (Rs 3,600.00 for
the main demo company); invoices keep the legacy numbers and amounts, the blocked ones are exactly the file's blocked
ones, due dates follow the supplier's payment terms; the advance's balance equals the truth, `due_advances` no longer
lists its order and a later invoice on that order takes the advance off; `cutover_supplier_ytd` holds the file; the
platform's next numbers lie above every loaded one; one `cutover.*` audit row per document by the importing person;
templates per file kind. Another company is unchanged while one imports.

Mutations (cases that tell a wrong rule from the right one): (1) cutover receipts posting stock (SP-03 would rise by the
20 L received on PO-CKN-2627-00203); (2) control totals not compared (the bad control file, Rs 1,000.00 off, would be
accepted and readiness would pass).
"""

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import env
import lib
from block0d import (
    CUTOFF,
    CUTOVER_FILES,
    Company,
    D,
    check_of,
    csv_rows,
    failed,
    master,
    money,
    snapshot,
    tenant_database,
    truth_cutover,
)
from block1 import Checker, js
from lib import Outcome

ID = "E81"
TITLE = "Cutover import: open orders, receipts, invoices and advances loaded with checks, control totals, nothing twice"
REQUIREMENT = (
    "Task 7.4 (cutover import of open POs, receipts, invoices and advances, with checks), OP-9 (part-paid invoices "
    "refused), OP-10 (receipts post no stock), OP-11 (invoices re-matched by the platform's rules), OP-12 (year-to-date "
    "purchases for tax at source), OP-13 (carrier request per PO)"
)
COUNTED = ("po", "po_line", "goods_receipt", "gr_line", "invoice", "invoice_line", "advance", "payment", "payment_run",
           "request", "stock_movement", "cutover_supplier_ytd", "audit_event")  # fmt: skip
GRNI = {"apex": Decimal("3600.00")}  # spec section 6: received not invoiced at the cutoff


def run(ctx: lib.Ctx) -> Outcome:
    env.forget_tenants("e81")
    try:
        return _run(ctx)
    finally:
        env.forget_tenants("e81")


def counts(co: Company) -> dict[str, int | None]:
    from block0d import count_in

    database = tenant_database(co.slug)
    found = {t: count_in(database, t) for t in COUNTED}
    found["cutover_import (real)"] = count_in(database, "cutover_import", "not dry_run")
    return found


def stock(co: Company) -> dict[tuple[str, str], Decimal]:
    return {(r["item_code"], r["location_code"]): D(r["on_hand"]) for r in co.q("select * from stock_level")}


def _run(ctx: lib.Ctx) -> Outcome:
    out = Outcome(True)
    check = Checker(out)
    lib.capture_mail()
    ap = Company.make("e81", "apex", state="setup")
    bw = Company.make("e81b", "brightwave", state="setup")
    r = ap.upload(ap.dataset / "cutover", "finance", dry_run=True)
    check(r.status_code == 409 and "cutover import is only for a company in cutover; it is setup" in r.text,
          f"a cutover import in setup: {r.status_code} {r.text[:200]}")  # fmt: skip
    for co in (ap, bw):
        code, said = co.cutover()
        check(code == 0 and co.state() == "cutover", f"{co.profile}: company cutover exit {code}: {said[-200:]}")
    if ap.state() != "cutover":
        return out
    before_bw = snapshot(bw.slug)
    _templates(ap, check)
    _bad(ap, check, out)
    _good(ap, check, out)
    check(snapshot(bw.slug) == before_bw, "the second company changed while the first imported")
    if bw.state() == "cutover":
        _good(bw, check, out)
    out.metrics["checks"] = f"{check.done - len(check.failed)} of {check.done} passed"
    return out


def _templates(co: Company, check: Checker) -> None:
    from block0d import header_of

    for kind in CUTOVER_FILES:
        r = co.call("finance", "GET", f"/api/company/cutover/templates/{kind}")
        want = header_of(co.dataset / "cutover" / f"{kind}.csv")
        got = r.text.strip().splitlines()[0].split(",") if r.status_code == 200 and r.text.strip() else []
        check(
            r.status_code == 200 and "text/csv" in r.headers.get("content-type", ""),
            f"template {kind}: {r.status_code}",
        )
        check(
            set(want) <= set(got) or set(got) <= set(want),
            f"template {kind} {got} does not match the file's columns {want}",
            "medium",
        )
    check(
        co.call("finance", "GET", "/api/company/cutover/templates/nope").status_code == 404,
        "an unknown template is not 404",
    )
    check(co.call("admin2", "GET", "/api/company/cutover/imports").status_code in (200, 403), "the list of imports")


# ---- the planted refusals ------------------------------------------------------------------------------------------


def row_at(folder: Path, file: str, line: Any) -> dict[str, str]:
    """The file's row at a reported line: line 2 is the first data row (the header is line 1); a report that counts the
    first data row as line 1 is accepted too (the spec does not say)."""
    name = str(file).split("/")[-1].removesuffix(".csv")
    found = csv_rows(folder / f"{name}.csv")
    try:
        n = int(line)
    except (TypeError, ValueError):
        return {}
    merged: dict[str, str] = {}
    for i in (n - 2, n - 1):
        if 0 <= i < len(found):
            merged.update({f"{k}@{i}": v for k, v in found[i].items()})
    return merged


def _bad(co: Company, check: Checker, out: Outcome) -> None:
    folder = co.dataset / "cutover-bad"
    truth = [t for t in truth_cutover(co.dataset, co.profile) if t.get("expected") == "left_out"]
    before, on_hand = counts(co), stock(co)
    dry = co.upload(folder, "finance", dry_run=True)
    body = js(dry)
    check(
        dry.status_code == 200 and body.get("dry_run") is True,
        f"cutover-bad dry run: {dry.status_code} {dry.text[:200]}",
    )
    after = counts(co)
    check(after == before, f"the dry run wrote rows or audit: {_diff(before, after)}")

    def matched(rejected: list[dict[str, Any]]) -> dict[str, str]:
        found: dict[str, str] = {}
        for t in truth:
            if t.get("kind") in ("control", "total"):
                continue
            hit = [
                r for r in rejected
                if t.get("reason_contains", "") in str(r.get("reason"))
                and (t["key"] in str(r.get("reason")) or t["key"] in row_at(folder, r.get("file", ""), r.get("line")).values()
                     or any(t["key"].startswith(v) for v in row_at(folder, r.get("file", ""), r.get("line")).values() if v))
            ]  # fmt: skip
            found[f"{t.get('kind')} {t['key']}"] = "ok" if hit else f"not left out with '{t.get('reason_contains')}'"
        return found

    got = matched(body.get("rejected") or [])
    out.metrics["planted rows left out (dry run)"] = f"{sum(v == 'ok' for v in got.values())} of {len(got)}"
    for k, v in got.items():
        check(v == "ok", f"cutover-bad dry run: {k}: {v}")
    check(
        body.get("control_ok") is False,
        f"cutover-bad: control_ok {body.get('control_ok')} with the invoice total off by Rs 1,000",
    )

    code, said = co.cutover_import(folder, "finance")
    check(code == 1, f"cutover-bad loaded by the CLI: exit {code}, expected 1 (rows left out, control fails)")
    rows = co.owner_q("select * from cutover_import where not dry_run order by id desc limit 1")
    check(bool(rows) and rows[0]["control_ok"] is False and int(rows[0]["rejected"] or 0) > 0,
          f"the cutover_import row of the bad run: {rows[:1]}")  # fmt: skip
    ready = co.readiness("cutover")
    check("cutover_import" in failed(ready), f"readiness after the bad run: {sorted(failed(ready))}")
    verdict = "caught" if (body.get("control_ok") is False and "cutover_import" in failed(ready)) else "NOT caught"
    out.metrics["mutation: control totals not compared"] = verdict
    if verdict != "caught":
        out.find("high", "mutation 'control totals not compared': the bad control file does not fail")
    keys = {r["no"] for r in co.q("select no from po")}
    planted = {t["key"].split("/")[0] for t in truth if t.get("kind") in ("po", "po_line")}
    check(not (keys & planted), f"orders left out of cutover-bad were loaded: {sorted(keys & planted)}")
    check(stock(co) == on_hand, "the bad run moved stock")


# ---- the good files -------------------------------------------------------------------------------------------------


def _good(co: Company, check: Checker, out: Outcome) -> None:  # noqa: C901 - one company's import, step by step
    name = co.profile
    folder = co.dataset / "cutover"
    f = {k: csv_rows(folder / f"{k}.csv") for k in CUTOVER_FILES}
    truth = truth_cutover(co.dataset, name)
    open_inv = [
        i
        for i in f["invoice"]
        if i.get("status") in ("received", "approved", "blocked") and D(i.get("paid_amount")) <= 0
    ]
    before, on_hand = counts(co), stock(co)

    dry = co.upload(folder, "finance", dry_run=True)
    body = js(dry)
    check(
        dry.status_code == 200 and not body.get("rejected"),
        f"{name}: cutover/ dry run: {dry.status_code} {str(body.get('rejected'))[:300]}",
    )
    check(body.get("control_ok") is True, f"{name}: cutover/ dry run control {body.get('control')}")
    b2 = counts(co)
    check(b2 == before, f"{name}: the dry run wrote rows or audit: {_diff(before, b2)}")

    real = co.upload(folder, "finance", dry_run=False)
    rb = js(real)
    check(real.status_code == 200 and not rb.get("rejected") and rb.get("control_ok") is True,
          f"{name}: cutover/ loaded: {real.status_code} rejected {str(rb.get('rejected'))[:200]} control {rb.get('control')}")  # fmt: skip
    for kind, c in (rb.get("control") or {}).items():
        check(
            bool(c.get("ok")) and money(c.get("file_total")) == money(c.get("loaded_total")),
            f"{name}: control {kind}: {c}",
        )
    ready = co.readiness("cutover")
    check(
        check_of(ready, "cutover_import").get("ok") is True,
        f"{name}: readiness cutover_import after a clean import: {check_of(ready, 'cutover_import')}",
    )

    # what the company now holds, against the files
    pos = {r["no"]: r for r in co.q("select * from po")}
    check(
        set(pos) == {p["no"] for p in f["po"]},
        f"{name}: orders {sorted(set(pos) ^ {p['no'] for p in f['po']})[:5]} differ from the file",
    )
    loaded_truth = {(t.get("kind"), t["key"]): t for t in truth if t.get("expected") == "loaded"}
    for (kind, key), t in loaded_truth.items():
        table = {
            "po": "po",
            "invoice": "invoice",
            "advance": "advance",
            "goods_receipt": "goods_receipt",
            "receipt": "goods_receipt",
        }.get(kind or "")
        if not table:
            continue
        col = "payment_no" if table == "advance" else "no"
        row = co.q(f"select * from {table} where {col} = :k", k=key)  # noqa: S608 - fixed names
        check(bool(row), f"{name}: truth {kind} {key} is not loaded")
        if row and t.get("amount") not in (None, ""):
            got = {D(row[0].get(c)) for c in ("total", "gross") if row[0].get(c) is not None}
            if table == "advance":
                got.add(D(row[0]["gross"]) - D(row[0].get("used_gross")))
            check(
                money(t["amount"]) in {money(g) for g in got}, f"{name}: {kind} {key} amount {got}, truth {t['amount']}"
            )
    oracle = po_oracle(co, f)
    for no, want in oracle.items():
        got = pos.get(no)
        if got is None:
            continue
        check(abs(D(got["total"]) - want) <= Decimal("0.05"), f"{name}: {no} total {got['total']}, recomputed {want}")
        fil = next((p for p in f["po"] if p["no"] == no and p.get("total")), None)
        if fil:
            check(
                abs(D(got["total"]) - D(fil["total"])) <= Decimal("1.00"),
                f"{name}: {no} total {got['total']}, file {fil['total']}",
            )
    carriers = {r["no"]: r for r in co.q("select no, channel, status from request where no like 'CUT-%'")}
    check(set(carriers) == {f"CUT-{n}" for n in pos}, f"{name}: carrier requests {len(carriers)} for {len(pos)} orders")
    check(
        all(r["channel"] == "cutover" for r in carriers.values()), f"{name}: a carrier request is not channel cutover"
    )
    sources = {r["source"] for r in co.q("select distinct source from po_line")}
    check(sources == {"cutover"}, f"{name}: po_line sources {sources}")

    # receipts: received from what was accepted; no stock moved
    accepted: dict[tuple[str, int], Decimal] = {}
    for g in f["gr_line"]:
        accepted[(g["po_no"], int(g["po_line_no"]))] = accepted.get((g["po_no"], int(g["po_line_no"])), Decimal(0)) + D(
            g["qty_accepted"]
        )
    # review of 23 Nov: open service orders come with the old system's service confirmations (cutover/service_entry.csv,
    # accepted by the owner's review); a service line counts as received by what was confirmed (amount / unit price)
    service_file = folder / "service_entry.csv"
    prices = {(ln["po_no"], int(ln["line_no"])): D(ln["unit_price"]) for ln in f["po_line"]}
    for e in csv_rows(service_file) if service_file.exists() else []:
        key = (e["po_no"], int(e["po_line_no"]))
        if prices.get(key):
            done = (D(e["amount_accepted"]) / prices[key]).quantize(Decimal("0.001"))
            accepted[key] = accepted.get(key, Decimal(0)) + done
    wrong = [
        (r["po_no"], r["line_no"], r["received_qty"], accepted.get((r["po_no"], int(r["line_no"])), Decimal(0)))
        for r in co.q("select po_no, line_no, received_qty from po_line")
        if D(r["received_qty"]) != accepted.get((r["po_no"], int(r["line_no"])), Decimal(0))
    ]
    check(not wrong, f"{name}: received_qty differs from the accepted receipts: {wrong[:4]}")
    check(
        set(r["no"] for r in co.q("select no from goods_receipt")) == {g["no"] for g in f["goods_receipt"]},
        f"{name}: receipts differ",
    )
    moved = stock(co) != on_hand or counts(co)["stock_movement"] != before["stock_movement"]
    check(not moved, f"{name}: the cutover import posted stock")
    if name == "apex":
        verdict = (
            "caught" if not moved and any(g["po_no"] == "PO-CKN-2627-00203" for g in f["gr_line"]) else "NOT caught"
        )
        out.metrics["mutation: cutover receipts post stock"] = verdict
        if verdict != "caught":
            out.find("high", "mutation 'cutover receipts post stock': the planted receipt is not there to tell it")

    # invoices: numbers, amounts, blocks, due dates
    invs = {r["no"]: r for r in co.q("select * from invoice")}
    check(
        set(invs) == {i["no"] for i in open_inv},
        f"{name}: invoices differ: {sorted(set(invs) ^ {i['no'] for i in open_inv})[:5]}",
    )
    for i in open_inv:
        got = invs.get(i["no"])
        if got:
            check(
                money(got["total"]) == money(i["total"]), f"{name}: {i['no']} total {got['total']}, file {i['total']}"
            )
    blocked = {n for n, r in invs.items() if r["status"] == "blocked"}
    check(
        blocked == {i["no"] for i in open_inv if i["status"] == "blocked"},
        f"{name}: blocked {sorted(blocked)}, the file's {sorted(i['no'] for i in open_inv if i['status'] == 'blocked')}",
    )
    late = [(n, r["due_date"], due_oracle(co, r)) for n, r in invs.items() if r.get("due_date") and r["due_date"] != due_oracle(co, r)
            and str(r["due_date"]) != next((i.get("due_date") for i in open_inv if i["no"] == n), "")]  # fmt: skip
    check(not late, f"{name}: due dates not by the supplier's terms: {late[:4]}", "medium")
    check(all(r.get("channel") == "cutover" for r in invs.values()), f"{name}: an invoice is not channel cutover")
    payable = sum((money(i["total"]) for i in open_inv), Decimal(0))
    out.metrics[f"{name}: orders / invoices / open payable"] = f"{len(pos)} / {len(invs)} / {payable}"

    # received not invoiced at the cutoff
    from p2p.invoices import payment, reconciliation

    co.db.rollback()
    grni = reconciliation.received_vs_invoiced(co.db, CUTOFF)
    co.db.rollback()
    want_grni = GRNI.get(name, _truth_total(truth, "grni"))
    check(
        money(grni["not_invoiced"]) == money(want_grni),
        f"{name}: received not invoiced {grni['not_invoiced']}, truth {want_grni}",
    )
    out.metrics[f"{name}: received not invoiced on {CUTOFF}"] = str(grni["not_invoiced"])
    for t in truth:
        if t.get("kind") == "total" and t.get("amount") not in (None, ""):
            got_t = _platform_total(co, t["key"], grni)
            if got_t is not None:
                check(money(got_t) == money(t["amount"]), f"{name}: total {t['key']} {got_t}, truth {t['amount']}")

    # advances
    for a in f["advance"]:
        row = co.q("select * from advance where payment_no = :p", p=a["payment_no"])
        pay = co.q("select * from payment where no = :p", p=a["payment_no"])
        check(bool(row) and money(row[0]["gross"]) == money(a["gross"]) and money(row[0]["used_gross"]) == money(a.get("recovered_gross")),
              f"{name}: advance {a['payment_no']}: {row[:1]}")  # fmt: skip
        check(bool(pay) and pay[0]["type"] == "advance" and money(pay[0]["amount"]) == money(D(a["gross"]) - D(a["tds_amount"])),
              f"{name}: advance payment {a['payment_no']}: {pay[:1]}")  # fmt: skip
        run_no = pay[0]["run_no"] if pay else ""
        run_row = co.q("select status from payment_run where no = :r", r=run_no)
        check(
            str(run_no).startswith("CUT-") and bool(run_row) and run_row[0]["status"] == "paid",
            f"{name}: advance run {run_no} {run_row}",
        )
        co.db.rollback()
        listed = [d for d in payment.due_advances(co.db) if (d.get("no") or d.get("po_no")) == a["po_no"]]
        co.db.rollback()
        check(not listed, f"{name}: due_advances still lists {a['po_no']}")
    ytd = {(r["supplier_code"], r["fy"]): money(r["taxable"]) for r in co.q("select * from cutover_supplier_ytd")}
    check(
        ytd == {(r["supplier_code"], r["fy"]): money(r["taxable"]) for r in f["supplier_ytd"]},
        f"{name}: cutover_supplier_ytd differs from the file",
    )

    # audit: one row per document by the importing person
    finance = str(co.uid("finance"))
    for action, n in (("cutover.po", len(pos)), ("cutover.invoice", len(invs)), ("cutover.receipt", len(f["goods_receipt"])),
                      ("cutover.advance", len(f["advance"])), ("cutover.import", counts(co)["cutover_import (real)"])):  # fmt: skip
        rows = co.audit(action)
        check(
            len(rows) == n and all(str(r["actor_id"]) == finance for r in rows),
            f"{name}: {len(rows)} {action} rows (expected {n}) by {set(str(r['actor_id']) for r in rows)}",
        )

    # numbers above every loaded one
    _numbers(co, check, pos, invs, f)

    # the same files again: nothing loaded, everything already
    again_before = counts(co)
    code, said = co.cutover_import(folder, "finance")
    check(code == 0 and _docs(counts(co)) == _docs(again_before),
          f"{name}: a second identical import exit {code} or wrote documents: {_diff(again_before, counts(co))}")  # fmt: skip
    last = co.owner_q("select * from cutover_import where not dry_run order by id desc limit 1")
    if last:
        loaded = last[0].get("loaded") or {}
        check(not any(int(v or 0) for v in loaded.values()), f"{name}: the second import loaded {loaded}")
        check(
            sum(int(v or 0) for v in (last[0].get("already") or {}).values()) >= len(pos) + len(invs),
            f"{name}: already {last[0].get('already')}",
        )

    if name == "apex":
        _later_invoice(co, check, f)


def po_oracle(co: Company, f: dict[str, list[dict[str, str]]]) -> dict[str, Decimal]:
    """Each order's total from its lines: taxable in paise, CGST and SGST halves within a state, else IGST."""
    states = {s["code"]: s["state_code"] for s in master(co.dataset, "supplier")}
    locs = {loc["code"]: loc["state_code"] for loc in master(co.dataset, "location")}
    head = {p["no"]: p for p in f["po"]}
    totals: dict[str, Decimal] = {}
    for ln in f["po_line"]:
        p = head.get(ln["po_no"])
        if p is None:
            continue
        taxable = money(D(ln["qty"]) * D(ln["unit_price"]))
        rate = D(ln["gst_rate"])
        if states.get(p["supplier_code"]) == locs.get(p["ship_to_location"]):
            gst = 2 * money(taxable * rate / 200)
        else:
            gst = money(taxable * rate / 100)
        totals[p["no"]] = totals.get(p["no"], Decimal(0)) + taxable + gst
    return totals


def due_oracle(co: Company, inv: dict[str, Any]) -> date | None:
    """Invoice date plus the supplier's payment terms; micro and small suppliers within the MSMED Act limits."""
    sup = next((s for s in master(co.dataset, "supplier") if s["code"] == inv["supplier_code"]), None)
    if sup is None or not inv.get("invoice_date"):
        return None
    terms = next((t for t in master(co.dataset, "payment_terms") if t["code"] == sup["payment_terms_code"]), None)
    days = int(terms["days"]) if terms else 30
    start = (
        inv["invoice_date"] if isinstance(inv["invoice_date"], date) else date.fromisoformat(str(inv["invoice_date"]))
    )
    if sup.get("msme_category") in ("micro", "small"):
        agreed = int(sup["agreed_payment_days"]) if sup.get("agreed_payment_days") else None
        if sup.get("msme_agreement") == "true" and agreed:
            # review of 23 Nov: the MSMED Act counts the agreed days (at most 45) from the day of acceptance, not from
            # the invoice date; the platform's existing rule is right and the oracle follows it
            return min(start + timedelta(days=days), _accepted_on(co, inv) + timedelta(days=min(agreed, 45)))
        days = min(days, 15)
    return start + timedelta(days=days)


def _accepted_on(co: Company, inv: dict[str, Any]) -> date:
    """The latest acceptance (goods accepted or service confirmed) on the invoice's order lines up to the day it was
    received, from the cutover files; the day received when there is none."""
    folder = co.dataset / "cutover"
    received = inv["received_on"] if isinstance(inv["received_on"], date) else date.fromisoformat(str(inv["received_on"]))
    keys = {(ln["po_no"], ln["po_line_no"]) for ln in csv_rows(folder / "invoice_line.csv") if ln["invoice_no"] == inv["no"]}
    heads = {g["no"]: g for g in csv_rows(folder / "goods_receipt.csv")}
    days = [
        date.fromisoformat(heads[g["gr_no"]]["accepted_on"])
        for g in csv_rows(folder / "gr_line.csv")
        if (g["po_no"], g["po_line_no"]) in keys and heads.get(g["gr_no"], {}).get("accepted_on")
    ]
    service_file = folder / "service_entry.csv"
    days += [
        date.fromisoformat(e["accepted_on"])
        for e in (csv_rows(service_file) if service_file.exists() else [])
        if (e["po_no"], e["po_line_no"]) in keys and e.get("accepted_on")
    ]
    days = [d for d in days if d <= received]
    return max(days) if days else received


def _truth_total(truth: list[dict[str, str]], measure: str) -> Decimal:
    return sum(
        (D(t["amount"]) for t in truth if t.get("kind") == "total" and measure in t["key"] and "SUP-" not in t["key"]),
        Decimal(0),
    )


def _platform_total(co: Company, key: str, grni: dict[str, Any]) -> Decimal | None:
    """A summary row of the truth (open_payable, grni, advance_balance; for a supplier when the key names one)."""
    supplier = next((p for p in key.replace("/", " ").replace(":", " ").split() if p.startswith("SUP-")), None)
    if "grni" in key:
        if supplier is None:
            return D(grni["not_invoiced"])
        return sum((D(s["not_invoiced"]) for s in grni["suppliers"] if s["supplier_code"] == supplier), Decimal(0))
    if "open_payable" in key:
        rows = co.q("select supplier_code, total from invoice where status not in ('paid', 'cancelled')")
    elif "advance_balance" in key:
        rows = co.q(
            "select po.supplier_code, a.gross - a.used_gross as total from advance a join po on po.no = a.po_no"
        )
    else:
        return None
    return sum((D(r["total"]) for r in rows if supplier is None or r["supplier_code"] == supplier), Decimal(0))


def _numbers(co: Company, check: Checker, pos: dict[str, Any], invs: dict[str, Any], f: dict[str, Any]) -> None:
    """The next number of each series lies above every loaded number of that series (made in a transaction undone)."""
    from p2p.platform import numbering

    loc = master(co.dataset, "location")[0]["code"]
    loaded = {
        "po": list(pos),
        "goods_receipt": [g["no"] for g in f["goods_receipt"]],
        "invoice_booking": list(invs),
        "payment": [a["payment_no"] for a in f["advance"]],
    }
    for kind, numbers in loaded.items():
        if not numbers:
            continue
        co.db.rollback()
        try:
            try:
                nxt = numbering.next_number(co.db, kind, lib.TODAY)
            except ValueError:
                nxt = numbering.next_number(co.db, kind, lib.TODAY, location_code=loc)
        except Exception as exc:  # noqa: BLE001 - a series the company does not have
            co.db.rollback()
            check(kind != "po", f"{co.profile}: no next {kind} number: {exc}", "medium")
            continue
        co.db.rollback()
        stem = nxt.rstrip("0123456789")
        same = [int(n[len(stem) :]) for n in numbers if n.startswith(stem) and n[len(stem) :].isdigit()]
        tail = int(nxt[len(stem) :]) if nxt[len(stem) :].isdigit() else 0
        check(
            not same or tail > max(same),
            f"{co.profile}: the next {kind} number {nxt} is not above the loaded {stem}{max(same) if same else ''}",
        )


def _later_invoice(co: Company, check: Checker, f: dict[str, list[dict[str, str]]]) -> None:
    """A later invoice on the advanced order takes what is left of the advance off (the platform's own services)."""
    from p2p.invoices import payment, service

    adv = next((a for a in f["advance"] if a["po_no"] == "PO-CKN-2627-00202"), None)
    lines = [ln for ln in f["po_line"] if ln["po_no"] == "PO-CKN-2627-00202"]
    po = next((p for p in f["po"] if p["no"] == "PO-CKN-2627-00202"), None)
    if not adv or not lines or not po:
        check(False, "the planted advance on PO-CKN-2627-00202 is not in the cutover files")
        return
    sup = next(s for s in master(co.dataset, "supplier") if s["code"] == po["supplier_code"])
    data = service.InvoiceIn(
        supplier_code=po["supplier_code"],
        supplier_invoice_no="EV81-ADV-1",
        invoice_date=lib.TODAY,
        lines=[service.InvLine(ln["po_no"], int(ln["line_no"]), D(ln["qty"]), D(ln["unit_price"])) for ln in lines],
        supplier_gstin=sup.get("gstin"),
        buyer_gstin=master(co.dataset, "gst_registration")[0]["gstin"],
    )
    try:
        co.db.rollback()
        entered = service.enter_invoice(co.db, data, co.uid("ap"))
        co.db.commit()
        if co.one("select booked_at from invoice where no = :n", n=entered.no) is None:
            payment.book_invoice(co.db, entered.no)
            co.db.commit()
    except Exception as exc:  # noqa: BLE001 - reported as a finding
        co.db.rollback()
        check(False, f"a later invoice on PO-CKN-2627-00202 could not be entered and booked: {exc}", "medium")
        return
    row = co.q("select gross, used_gross from advance where payment_no = :p", p=adv["payment_no"])
    total = D(co.one("select total from invoice where no = :n", n=entered.no))
    want = min(D(adv["gross"]) - D(adv.get("recovered_gross")), total)
    check(bool(row) and money(D(row[0]["used_gross"]) - D(adv.get("recovered_gross"))) == money(want),
          f"the later invoice {entered.no} ({total}) on PO-CKN-2627-00202 used {row[0]['used_gross'] if row else '-'} of the advance, expected {want}")  # fmt: skip


def _docs(found: dict[str, Any]) -> dict[str, Any]:
    """The counts of business rows (not the import's own record and audit, which a real run adds)."""
    return {k: v for k, v in found.items() if k not in ("cutover_import (real)", "audit_event")}


def _diff(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    return {k: (a.get(k), b.get(k)) for k in set(a) | set(b) if a.get(k) != b.get(k)}
