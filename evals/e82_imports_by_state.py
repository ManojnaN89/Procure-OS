"""E82: imports by company state and the locks after go-live (task 7.6, D-02), written from the block 0d spec only.

A fresh company of the main demo profile is taken through setup, cutover, live and suspended (and back to live). In each
state the eval runs every import the CLI has: masters (`import masters`), the stock history (`import stock`), the ERP's
demand (`import demand`) and the cutover files (`company cutover-import`), and compares with the table of spec 4.6:
masters in setup, cutover and live; stock in setup and cutover only (live: "opening stock and its history are locked
after go-live; correct stock with a count"); demand in every state but suspended; cutover only in cutover. A refusal
exits 2 with its text and writes nothing (master, stock, demand and document rows and the audit log counted before and
after). Through the API: the cutover import answers 409 outside cutover, the demand import still works in live.

In live, a copy of the masters with a changed opening-stock quantity, a new opening-stock key, a new bank account for
SUP-003 (marked verified) and an item renamed: exit 0; opening stock, on hand and SUP-003's bank accounts unchanged, each
with the spec's lock warning; the item renamed with its `masters.row_change` audit row (block 0c's update mode).
Another company is unchanged.

Mutations: (1) `import stock` in live replacing the history (`company_state.require_state` made a no-op: the import
runs); (2) a new opening-stock key added in live (the importer told the company is in cutover: the new key lands).
"""

from decimal import Decimal
from pathlib import Path
from typing import Any

import env
import lib
from block0d import (
    LOCK_BANK,
    LOCK_OPENING,
    LOCK_STOCK_HISTORY,
    Company,
    copy_folder,
    count_in,
    csv_rows,
    header_of,
    patched,
    snapshot,
    tenant_database,
    write_csv,
)
from block1 import Checker, js
from lib import Outcome

ID = "E82"
TITLE = "Imports follow the company state; opening stock and bank accounts are locked after go-live"
REQUIREMENT = (
    "Task 7.6, D-02 (imports depend on company state; opening stock and verified accounts locked after go-live), "
    "Base WP-7 (imports refused after go-live), block 0c update mode underneath"
)
ALLOWED = {
    "setup": {"masters", "stock", "demand"},
    "cutover": {"masters", "stock", "demand", "cutover"},
    "live": {"masters", "demand"},
    "suspended": set(),
}
WATCHED = ("supplier", "item", "stock_opening", "supplier_bank_account", "org_setting", "stock_movement",
           "production_order", "production_order_line", "po", "invoice", "cutover_import", "audit_event")  # fmt: skip


def run(ctx: lib.Ctx) -> Outcome:
    env.forget_tenants("e82")
    try:
        return _run(ctx)
    finally:
        env.forget_tenants("e82")


def rows_count(co: Company) -> dict[str, int | None]:
    database = tenant_database(co.slug)
    return {t: count_in(database, t) for t in WATCHED}


def _run(ctx: lib.Ctx) -> Outcome:  # noqa: C901 - one company through its states
    from p2p.platform import tenants

    out = Outcome(True)
    check = Checker(out)
    lib.capture_mail()
    other = env.slug("e82b")
    check(tenants.create_tenant(other, "Other company").ok, "the second company could not be made")
    before_other = snapshot(other)
    co = Company.make("e82", "apex", state="setup")
    ds = co.dataset
    table: dict[str, dict[str, str]] = {}

    def attempt(state: str) -> None:
        commands = {
            "masters": ("import", "masters", co.slug, str(ds)),
            "stock": ("import", "stock", co.slug, str(ds)),
            "demand": ("import", "demand", co.slug, str(ds)),
            "cutover": ("company", "cutover-import", co.slug, str(ds / "cutover"), "--as", co.email("finance")),
        }
        row: dict[str, str] = {}
        for kind, args in commands.items():
            before = rows_count(co)
            code, said = co.cli(*args)
            allowed = kind in ALLOWED[state]
            usage = "invalid choice" in said or "error: argument" in said
            if allowed:
                check(code == 0, f"{state}: {kind} import must be allowed: exit {code}: {said.strip()[-200:]}")
            else:
                words = [f"not allowed while the company is {state}"]
                if kind == "cutover":
                    words.append(f"cutover import is only for a company in cutover; it is {state}")
                if kind == "stock" and state == "live":
                    words = [LOCK_STOCK_HISTORY]
                check(code == 2 and not usage and any(w in said for w in words),
                      f"{state}: {kind} import must be refused (exit 2, '{words[0]}'): exit {code}: {said.strip()[-200:]}")  # fmt: skip
                after = rows_count(co)
                check(after == before, f"{state}: the refused {kind} import wrote: {_diff(before, after)}")
            row[kind] = f"exit {code}"
        table[state] = row

    # ---- setup ----------------------------------------------------------------------------------------------------
    attempt("setup")
    r = co.upload(ds / "cutover", "finance", dry_run=True)
    check(r.status_code == 409, f"setup: the cutover import API answered {r.status_code}")

    # ---- cutover --------------------------------------------------------------------------------------------------
    code, said = co.cutover()
    check(code == 0 and co.state() == "cutover", f"company cutover exit {code}: {said[-200:]}")
    if co.state() != "cutover":
        return out
    attempt("cutover")

    # ---- live -------------------------------------------------------------------------------------------------------
    pid = js(co.go_live()).get("proposal_id")
    if pid:
        co.approve(int(pid), "admin2")
    check(co.state() == "live", f"the company did not go live: {co.state()}")
    if co.state() != "live":
        return out
    on_hand = _stock(co)
    attempt("live")
    check(_stock(co) == on_hand, "live: the stock changed during the refused stock import")
    r = co.upload(ds / "cutover", "finance", dry_run=True)
    check(
        r.status_code == 409 and "it is live" in r.text,
        f"live: the cutover import API answered {r.status_code} {r.text[:160]}",
    )
    store = co.holders("store")[0]
    demand_file = ds / "inputs" / "production_order.csv"
    if demand_file.exists():
        r = co.client.post("/api/demand/import/production_order", files={"file": ("production_order.csv", demand_file.read_bytes(), "text/csv")},
                           params={"dry_run": "true"}, headers=co.h(store))  # fmt: skip
        check(r.status_code == 200, f"live: the demand import API answered {r.status_code} {r.text[:160]}")
    _locks(co, check, out)

    # mutation 1: the stock history import in live runs (the guard switched off)
    from p2p.platform import company_state

    with patched(company_state, "require_state", lambda *a, **k: None) as n:
        code, said = co.cli("import", "stock", co.slug, str(ds))
    m1 = "not applied" if not n else ("caught" if code != 2 else "NOT caught")
    out.metrics["mutation: import stock in live replaces the history"] = m1
    if m1 != "caught":
        out.find("high" if m1 == "NOT caught" else "low", f"mutation 'stock import in live': {m1}")

    # ---- suspended, then live again -------------------------------------------------------------------------------
    code, said = co.cli(
        "company", "suspend", co.slug, "--kind", "commercial", "--reason", "eval", "--operator", "Eval Operator"
    )
    check(code == 0 and co.state() == "suspended", f"suspend: exit {code}: {said[-200:]}")
    if co.state() == "suspended":
        attempt("suspended")
        co.cli("company", "resume", co.slug, "--reason", "eval", "--operator", "Eval Operator")
    out.metrics["imports by state (exit codes)"] = table
    check(snapshot(other) == before_other, "another company changed")
    out.metrics["checks"] = f"{check.done - len(check.failed)} of {check.done} passed"
    return out


def _stock(co: Company) -> dict[tuple[str, str], Decimal]:
    return {(r["item_code"], r["location_code"]): Decimal(str(r["on_hand"])) for r in co.q("select * from stock_level")}


def _planted(co: Company, new_item: str, account: str) -> tuple[Path, dict[str, Any]]:
    """A copy of the masters: one opening quantity changed, one new opening key, a new verified account for SUP-003,
    an item renamed."""
    copy = copy_folder(co.dataset, "e82_")
    m = copy / "master"
    opening = csv_rows(m / "stock_opening.csv")
    head = header_of(m / "stock_opening.csv")
    changed = dict(opening[0])
    opening[0] = {**changed, "qty": f"{Decimal(changed['qty']) + 5:.3f}"}
    items = {i["code"]: i for i in csv_rows(m / "item.csv")}
    opening.append({**changed, "item_code": new_item, "qty": "7.000", "unit_cost": items[new_item]["standard_price"]})
    write_csv(m / "stock_opening.csv", opening, head)
    banks = csv_rows(m / "supplier_bank_account.csv")
    sample = next(b for b in banks if b["supplier_code"] == "SUP-003")
    banks.append({**sample, "account_no": account, "ifsc": "HDFC0009803", "verified": "true"})
    write_csv(m / "supplier_bank_account.csv", banks, header_of(m / "supplier_bank_account.csv"))
    item_rows = csv_rows(m / "item.csv")
    renamed = next(i for i in item_rows if i["code"] != new_item)
    renamed["name"] = renamed["name"] + " (revised)"
    write_csv(m / "item.csv", item_rows, header_of(m / "item.csv"))
    return copy, {"changed": changed["item_code"], "renamed": renamed["code"], "name": renamed["name"]}


def _free_items(co: Company) -> list[str]:
    held = {r["item_code"] for r in co.q("select item_code from stock_opening")}
    return sorted(i["code"] for i in csv_rows(co.dataset / "master" / "item.csv") if i["code"] not in held)


def _locks(co: Company, check: Checker, out: Outcome) -> None:
    free = _free_items(co)
    if not free:
        check(False, f"the dataset has no item without opening stock for the lock cases: {free}", "medium")
        return
    # review of 23 Nov: Apex has one item without opening stock (SV-01) and no data is added for the eval; with one
    # item the mutation case below reuses it (the direct case has already shown the new key refused)
    opening = sorted(map(str, co.q("select * from stock_opening")))
    banks = sorted(map(str, co.q("select * from supplier_bank_account where supplier_code = 'SUP-003'")))
    on_hand = _stock(co)
    audit_changes = len(co.audit("masters.row_change"))
    copy, plant = _planted(co, free[0], "000988000003")
    code, said = co.cli("import", "masters", co.slug, str(copy))
    check(
        code == 0,
        f"live: the masters copy with locked changes must load with warnings (exit 0): exit {code}: {said[-300:]}",
    )
    check(sorted(map(str, co.q("select * from stock_opening"))) == opening, "live: opening stock changed")
    check(_stock(co) == on_hand, "live: on hand changed by a masters import")
    check(sorted(map(str, co.q("select * from supplier_bank_account where supplier_code = 'SUP-003'"))) == banks,
          "live: SUP-003's bank accounts changed by a masters import")  # fmt: skip
    lines = said.splitlines()
    check(
        any(free[0] in ln and LOCK_OPENING in ln for ln in lines),
        f"live: no '{LOCK_OPENING}' warning for the new key {free[0]}",
    )
    check(any(plant["changed"] in ln and (LOCK_OPENING in ln or "opening stock is loaded once" in ln) for ln in lines),
          f"live: no opening-stock warning for the changed key {plant['changed']}")  # fmt: skip
    check(any("SUP-003" in ln and LOCK_BANK in ln for ln in lines), f"live: no '{LOCK_BANK}' warning for SUP-003")
    name = co.one("select name from item where code = :c", c=plant["renamed"])
    check(name == plant["name"], f"live: the renamed item reads {name!r}; other changes apply as in update mode")
    check(
        len(co.audit("masters.row_change")) > audit_changes,
        "live: the renamed item has no masters.row_change audit row",
    )

    # mutation 2: the importer believes the company is in cutover; the second new key must then land
    from p2p.platform import company_state

    copy2, _ = _planted(co, free[1] if len(free) > 1 else free[0], "000988000004")
    with (
        patched(company_state, "state_of_session", lambda *a, **k: "cutover") as n1,
        patched(company_state, "state_of", lambda *a, **k: "cutover") as n2,
    ):
        co.cli("import", "masters", co.slug, str(copy2))
    added = bool(co.q("select 1 from stock_opening where item_code = :i", i=free[1] if len(free) > 1 else free[0]))
    m2 = (
        "caught"
        if added
        else ("not applied" if not (n1 or n2) else "not applied (the lock does not read the state there)")
    )
    out.metrics["mutation: a new opening-stock key added in live"] = m2
    if m2 != "caught":
        out.find("low", f"mutation 'new opening key in live': {m2}; the direct case above still holds the rule")


def _diff(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    return {k: (a.get(k), b.get(k)) for k in set(a) | set(b) if a.get(k) != b.get(k)}
