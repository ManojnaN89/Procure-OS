"""Demo companies described by a profile file: master data, ERP input files and twelve months of history.

    python -I datagen/generator/company_profile.py apex [--out DIR]       # -> data/datasets/apex-v1
    python -I datagen/generator/company_profile.py brightwave             # -> data/datasets/brightwave-v1
    python -I datagen/generator/company_profile.py --check data/datasets/apex-v1

A company is one file, datagen/catalog/<name>/company.yaml: who works there, what it buys, from whom, its rules and
budgets, and its ERP input files. A new company is a new file. The master folder has the files, headers and value
formats agreed in master_files.py; `inputs/` holds the company's own planning files. The history
(history.py, history_money.py, history_close.py; checked by history_check.py) ends at the profile's opening stock, so
master/stock_opening.csv holds the stock at the start of the history and history/stock_movement.csv the rest.
Safety rules of this repo: PANs carry the digits 0000 (never issued), phones are +91 00000 ..., emails end in .example.
"""

import argparse
import csv
import json
import re
import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).parent))
from history import simulate  # noqa: E402
from history_check import history_problems  # noqa: E402
from history_close import Closing  # noqa: E402
from history_close import write as write_tables  # noqa: E402
from history_money import Money  # noqa: E402
from history_plan import production_lines  # noqa: E402
from master_files import (  # noqa: E402
    EMPTY_TABLES,
    HEADERS,
    REPO,
    Supplier,
    common_problems,
    copy_common,
    gstin,
    months,
    read,
    supplier_tables,
    write_csv,
    write_table,
)

CATALOG = REPO / "datagen" / "catalog"
REFERENCE = REPO / "data" / "reference"
DATASETS = REPO / "data" / "datasets"
TOP = "999999999.00"  # the open upper end of an approval band
QTY = Decimal("0.001")
GST_CHANGE = "2025-09-22"


class CompanyProfile:
    """One demo company as its profile file describes it."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.name = path.parent.name
        self.data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))

    @classmethod
    def named(cls, name: str) -> "CompanyProfile":
        return cls(CATALOG / name / "company.yaml")

    def __getitem__(self, key: str) -> Any:
        return self.data[key]

    @property
    def dataset(self) -> str:
        return str(self["dataset"])

    @property
    def tenant(self) -> str:
        return str(self["tenant"])

    @property
    def gstin(self) -> str:
        return gstin(self["location"]["state_code"], self["company"]["pan"])

    @property
    def approver_roles(self) -> list[tuple[str, str, str | None]]:
        """How each role an approval chain names becomes a person: (role, source, value)."""
        return [(r, s, v) for r, s, v in self["approver_roles"]]

    def email_of(self, person: dict[str, Any]) -> str:
        return f"{person['login']}@{self['company']['email_domain']}"


def _rows(columns: list[str], values: list[list[Any]]) -> list[dict[str, str]]:
    return [dict(zip(columns, (str(v) for v in row), strict=True)) for row in values]


def _qty(value: Decimal) -> str:
    return f"{value.quantize(QTY)}"


class MasterWriter:
    """Writes master/*.csv for a profile, in the order and the formats of master_files.py."""

    def __init__(self, profile: CompanyProfile, out: Path) -> None:
        self.p, self.out = profile, out
        self.counts: dict[str, int] = {}

    def put(self, name: str, rows: list[dict[str, object]]) -> None:
        self.counts[name] = write_table(self.out, name, rows)

    def write(self) -> dict[str, int]:
        self.counts.update(copy_common(self.out))
        self.organisation()
        self.people()
        self.rules()
        self.money()
        self.catalogue()
        self.suppliers()
        self.contracts()
        for name in EMPTY_TABLES:
            self.put(name, [])
        return self.counts

    def organisation(self) -> None:
        co, loc = self.p["company"], self.p["location"]
        self.put(
            "company",
            [
                {
                    **{k: co[k] for k in ("code", "legal_name", "short_name", "pan", "cin", "registered_address")},
                    "turnover_band": co["turnover_band"],
                    "fy_start_month": "4",
                    "currency": co["currency"],
                    "email_domain": co["email_domain"],
                    "letterhead_text": f"{co['legal_name']} | CIN {co['cin']} | {loc['address']}, {loc['city']} {loc['pincode']}",
                }
            ],
        )
        self.put(
            "location",
            [
                {
                    "code": loc["code"],
                    "name": loc["name"],
                    "type": loc["type"],
                    "gstin": self.p.gstin,
                    "address": loc["address"],
                    "city": loc["city"],
                    "state_code": loc["state_code"],
                    "pincode": loc["pincode"],
                    "is_stock_location": "true",
                    "working_days": loc["working_days"],
                    "receiving_hours": loc["receiving_hours"],
                    "holiday_scope": f"central;{loc['state_code']}",
                    "loc_short": loc["loc_short"],
                }
            ],
        )
        self.put("department", [{"code": c, "name": n, "head_user": h} for c, n, h in self.p["departments"]])
        self.put(
            "cost_centre",
            [
                {
                    "code": c,
                    "name": n,
                    "department_code": d,
                    "location_code": loc["code"],
                    "owner_user": o,
                    "active": "true",
                }
                for c, n, d, o in self.p["cost_centres"]
            ],
        )

    def people(self) -> None:
        people, plant = self.p["people"], self.p["location"]["code"]
        self.put(
            "user",
            [
                {
                    "code": u["code"],
                    "employee_no": f"EMP{u['code'][1:]:0>4}",
                    "name": u["name"],
                    "designation": u["designation"],
                    "email": self.p.email_of(u),
                    "phone": f"+91 00000 {int(u['code'][1:]):05d}",
                    "department_code": u["department"],
                    "location_code": plant,
                    "manager_code": u["manager"],
                    "approval_limit": u["limit"],
                    "notify_by": "both",
                    "active": "true",
                }
                for u in people
            ],
        )
        self.put(
            "user_role",
            [
                {"user_code": u["code"], "role": role, "scope_location": "ANY", "scope_department": "ANY"}
                for u in people
                for role in u["roles"]
            ],
        )

    def rules(self) -> None:
        lines = self.p["approval_rules"].strip().splitlines()
        self.put("approval_rule", list(csv.DictReader(lines)))
        self.put("setting", [{"key": k, "value": v, "description": d} for k, v, d in self.p["settings"]])
        self.put(
            "tolerance", [{"key": k, "value": v, "category_code": "ANY", "note": n} for k, v, n in self.p["tolerances"]]
        )

    def money(self) -> None:
        bank, loc = self.p["company_bank_account"], self.p["location"]
        self.put(
            "company_bank_account",
            [{"code": "CBA-01", **bank, "purpose": "payments", "payment_file_format": "generic_csv"}],
        )
        self.put(
            "gst_registration",
            [
                {
                    "gstin": self.p.gstin,
                    "state_code": loc["state_code"],
                    "legal_address": self.p["company"]["registered_address"],
                    "pincode": loc["pincode"],
                    "is_default": "true",
                }
            ],
        )
        b = self.p["budgets"]
        held = [(cc, "", plan) for cc, plan in b["per_month"].items()]
        held += [("", loc, plan) for loc, plan in b["per_location"].items()]  # the location's own budget
        self.put(
            "budget",
            [
                {
                    "cost_centre_code": cc,
                    "gl_code": gl,
                    "project_code": "",
                    "period": period,
                    "amount": amount,
                    "version": "1",
                    "policy": b["policy"],
                    "location_code": loc,
                }
                for cc, loc, plan in held
                for gl, amount in plan["gl"].items()
                for period in months(*b["periods"])
            ],
        )

    def catalogue(self) -> None:
        buyer = next(u["code"] for u in self.p["people"] if "buyer" in u["roles"])
        cats = {c["code"]: c for c in self.p["categories"]}
        self.put(
            "category",
            [
                {
                    "code": c["code"],
                    "name": c["name"],
                    "parent_code": "",
                    "kind": c["kind"],
                    "default_gl": c["gl"],
                    "stocked": str(c["stocked"]).lower(),
                    "capex": "false",
                    "min_quotes": "2",
                    "buyer_user": buyer,
                    "approval_category": c["approval_category"],
                    "required_attributes": "",
                    "required_supplier_docs": "gst_cert;pan",
                    "inspection_required": "false",
                    "default_tds_section": c["tds"],
                    "reverse_charge_default": "false",
                }
                for c in cats.values()
            ],
        )
        items, plan, stock = [], [], []
        plant, asof = self.p["location"]["code"], self.p["opening_stock_date"]
        for it in self.p["items"]:
            before, after = it["gst"]
            stocked = "on_hand" in it
            items.append(
                {
                    "code": it["code"],
                    "name": it["name"],
                    "description": it["description"],
                    "category_code": it["category"],
                    "kind": it.get("kind", "goods"),
                    "default_gl": it.get("gl", cats[it["category"]]["gl"]),
                    "brand": "",
                    "base_uom": it["unit"],
                    "purchase_uom": it["unit"],
                    "pack_size": "1",
                    "moq": "1",
                    "hsn_sac": it["hsn"],
                    "gst_rate_before": before,
                    "gst_rate_after": after,
                    "gst_rate_alt_before": "",
                    "gst_rate_alt_after": "",
                    "gst_rate_change_date": "" if before == after else GST_CHANGE,
                    "gst_rate_confidence": it.get("gst_confidence", "CONFIRMED"),
                    "gst_reverse_charge": "no",
                    "gst_note": "",
                    "standard_price": it["price"],
                    "abc_class": "B",
                    "stocked": str(stocked).lower(),
                    "capex": str(it.get("capex", False)).lower(),
                    "inspection_required": str(it.get("inspection", False)).lower(),
                    "hazardous": "false",
                    "default_lead_days": it["lead"],
                    "attributes": "{}",
                    "active": "true",
                }
            )
            if not stocked:
                continue
            point, up_to = (_qty(Decimal(v)) for v in it["reorder"]) if "reorder" in it else ("", "")
            plan.append(
                {
                    "item_code": it["code"],
                    "location_code": plant,
                    "reorder_policy": "minmax" if "reorder" in it else "none",
                    "reorder_point": point,
                    "safety_stock": _qty(Decimal(0)) if point else "",
                    "min_qty": point,
                    "max_qty": up_to,
                    "order_qty": "",
                    "service_level_pct": "95" if point else "",
                    "lead_days_override": "",
                }
            )
            stock.append(
                {
                    "item_code": it["code"],
                    "location_code": plant,
                    "qty": _qty(Decimal(str(it["on_hand"]))),
                    "unit_cost": it["price"],
                    "as_of": asof,
                }
            )
        self.put("item", items)
        self.put("item_location", plan)
        self.put("stock_opening", stock)

    def suppliers(self) -> None:
        buyer = next(u["code"] for u in self.p["people"] if "buyer" in u["roles"])
        specs = [
            Supplier(
                code=s["code"],
                name=s["name"],
                form=s["form"],
                pan=s["pan"],
                state=s["state"],
                e_invoice=s["e_invoice"],
                hsn_digits=s["hsn_digits"],
                msme=s["msme"],
                udyam=s.get("udyam", ""),
                agreed_days=s.get("agreed_days"),
                terms=s["terms"],
                turnover=s["turnover"],
                bank=s["bank"],
                contact="Sales Desk",
                domain=s["domain"],
                categories=tuple(s["categories"]),
                city=s["city"],
                pincode=s["pincode"],
                tds_section=s.get("tds", "194Q"),
                onboarded_by=buyer,
            )
            for s in self.p["suppliers"]
        ]
        for name, rows in supplier_tables(specs).items():
            self.put(name, rows)

    def contracts(self) -> None:
        units = {it["code"]: it["unit"] for it in self.p["items"]}
        contracts, prices = [], []

        def price_rows(supplier: str, contract: str, lead: int, item_prices: dict[str, str], span: list[str]) -> None:
            prices.extend(
                {
                    "supplier_code": supplier,
                    "item_code": item,
                    "contract_code": contract,
                    "uom": units[item],
                    "price": price,
                    "min_qty": "1",
                    "price_basis": "for_destination",
                    "freight_included": "true",
                    "valid_from": span[0],
                    "valid_to": span[1],
                    "lead_days": lead,
                }
                for item, price in item_prices.items()
            )

        for c in self.p["contracts"]:
            contracts.append(
                {
                    "code": c["code"],
                    "supplier_code": c["supplier"],
                    "type": c["type"],
                    "valid_from": c["valid_from"],
                    "valid_to": c["valid_to"],
                    "max_value": c["max_value"],
                    "payment_terms_code": c["terms"],
                    "delivery_terms": "for_destination",
                    "freight_terms": "included",
                    "warranty_months": "",
                    "sla_text": "",
                    "status": "active",
                }
            )
            price_rows(c["supplier"], c["code"], c["lead"], c["prices"], [c["valid_from"], c["valid_to"]])
        for r in self.p["price_records"]:
            price_rows(r["supplier"], "", r["lead"], r["prices"], self.p["price_list_dates"])
        self.put("contract", contracts)
        self.put("price_record", prices)


def write_inputs(profile: CompanyProfile, out: Path) -> dict[str, int]:
    """The company's ERP input files as the profile gives them, plus the production order lines worked out from them."""
    counts: dict[str, int] = {}
    tables = {name: _rows(t["columns"], t["rows"]) for name, t in profile["inputs"].items()}
    if "bom" in tables and "production_order" in tables:
        tables["production_order_line"] = production_lines(tables["bom"], tables["production_order"])
    for name, rows in tables.items():
        header = profile["inputs"][name]["columns"] if name in profile["inputs"] else list(rows[0])
        write_csv(out / "inputs" / f"{name}.csv", header, list(rows))
        counts[name] = len(rows)
    return counts


def write_history(profile: CompanyProfile, out: Path) -> dict[str, int]:
    """Twelve months of history that end at the profile's stock; the opening stock becomes the stock at its start."""
    hist = simulate(profile, out)
    tables = Closing(hist, Money(hist).run()).run()
    prices = {it["code"]: it["price"] for it in profile["items"]}
    return write_tables(out, tables, hist.opening, hist.start, hist.loc, prices)


def build(profile: CompanyProfile, out: Path) -> dict[str, dict[str, int]]:
    out.mkdir(parents=True, exist_ok=True)
    master = MasterWriter(profile, out).write()
    inputs = write_inputs(profile, out)
    history = write_history(profile, out) if "history" in profile.data else {}
    manifest = {
        "dataset": profile.dataset,
        "profile": profile.name,
        "description": profile["description"],
        "seed": profile["history"]["seed"] if history else None,
        "history": {"start": profile["history"]["start"], "cutoff": profile["history"]["cutoff"]} if history else None,
        "rows": master,
        "inputs": inputs,
        "history_rows": history,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    held = {"hold_out_suppliers": list(profile.data.get("hold_out_suppliers", []))}
    (out / "splits.json").write_text(json.dumps(held, indent=2) + "\n", encoding="utf-8")
    return {"master": master, "inputs": inputs, "history": history}


# ---- checks -----------------------------------------------------------------------------------

# (table, column) -> (table, column) it must name; an empty cell names nothing
REFERENCES = [
    ("user", "department_code", "department", "code"),
    ("user", "location_code", "location", "code"),
    ("department", "head_user", "user", "code"),
    ("cost_centre", "department_code", "department", "code"),
    ("cost_centre", "location_code", "location", "code"),
    ("cost_centre", "owner_user", "user", "code"),
    ("user_role", "user_code", "user", "code"),
    ("budget", "cost_centre_code", "cost_centre", "code"),
    ("budget", "gl_code", "gl_account", "code"),
    ("category", "default_gl", "gl_account", "code"),
    ("category", "buyer_user", "user", "code"),
    ("item", "category_code", "category", "code"),
    ("item", "default_gl", "gl_account", "code"),
    ("item_location", "item_code", "item", "code"),
    ("item_location", "location_code", "location", "code"),
    ("stock_opening", "item_code", "item", "code"),
    ("stock_opening", "location_code", "location", "code"),
    ("location", "gstin", "gst_registration", "gstin"),
    ("company_bank_account", "bank_code", "bank", "code"),
    ("supplier", "payment_terms_code", "payment_terms", "code"),
    ("supplier_address", "supplier_code", "supplier", "code"),
    ("supplier_bank_account", "supplier_code", "supplier", "code"),
    ("supplier_bank_account", "bank_code", "bank", "code"),
    ("supplier_category", "supplier_code", "supplier", "code"),
    ("supplier_category", "category_code", "category", "code"),
    ("supplier_contact", "supplier_code", "supplier", "code"),
    ("supplier_document", "supplier_code", "supplier", "code"),
    ("supplier_status_history", "supplier_code", "supplier", "code"),
    ("supplier_status_history", "by_user", "user", "code"),
    ("contract", "supplier_code", "supplier", "code"),
    ("contract", "payment_terms_code", "payment_terms", "code"),
    ("price_record", "supplier_code", "supplier", "code"),
    ("price_record", "item_code", "item", "code"),
    ("price_record", "contract_code", "contract", "code"),
]
# an input column of this name must name a row of that table
INPUT_REFERENCES = {
    "item_code": ("master", "item", "code"),
    "product_code": ("inputs", "product", "product_code"),
    "department": ("master", "department", "code"),
    "kit": ("inputs", "joiner_kit", "kit"),
}
MONEY = re.compile(r"^\d+\.\d{2}$")
JOBS = ("approver", "store", "ap", "finance", "buyer", "admin")  # one of these per person at most
GST_RATES = {"0", "5", "12", "18", "28", "40"}


def _column(path: Path, column: str) -> set[str]:
    return {r[column] for r in read(path)}


def check(out: Path, profile: CompanyProfile | None = None) -> list[str]:
    """What is wrong with the dataset, in plain words. Empty when it is as designed."""
    problems = common_problems(out)
    if problems:
        return problems
    m = out / "master"
    t = {name: read(m / f"{name}.csv") for name in HEADERS}

    for table, col, ref_table, ref_col in REFERENCES:
        known = {r[ref_col] for r in t[ref_table]}
        for r in t[table]:
            if r[col] and r[col] not in known:
                problems.append(f"{table}.{col} '{r[col]}' is not in {ref_table}")

    units = _column(REFERENCE / "gst_uqc_units.csv", "code")
    states = _column(REFERENCE / "gst_state_codes.csv", "code")
    pins = {r["pincode"]: r["state_code"] for r in read(REFERENCE / "pincode_directory.csv")}
    hsn = _column(REFERENCE / "hsn_sac_codes.csv", "code")
    tds = _column(REFERENCE / "tds_sections.csv", "section")
    for r in t["item"]:
        if r["base_uom"] not in units or r["purchase_uom"] not in units:
            problems.append(f"item {r['code']}: unit {r['base_uom']} is not a GST unit code")
        if r["hsn_sac"] not in hsn:
            problems.append(f"item {r['code']}: HSN/SAC {r['hsn_sac']} is not in the directory")
        if r["gst_rate_before"] not in GST_RATES or r["gst_rate_after"] not in GST_RATES:
            problems.append(
                f"item {r['code']}: GST rate {r['gst_rate_before']}/{r['gst_rate_after']} is not a GST rate"
            )
        if (r["gst_rate_before"] != r["gst_rate_after"]) != bool(r["gst_rate_change_date"]):
            problems.append(f"item {r['code']}: a rate change needs its date, and only a change has one")
        if not MONEY.match(r["standard_price"]):
            problems.append(f"item {r['code']}: price {r['standard_price']} is not rupees and paise")
    for table in ("location", "supplier_address", "gst_registration"):
        for r in t[table]:
            if r["state_code"] not in states:
                problems.append(f"{table}: state {r['state_code']} is not a GST state code")
            if pins.get(r["pincode"]) != r["state_code"]:
                problems.append(f"{table}: pincode {r['pincode']} is not in state {r['state_code']}")
    for r in t["category"]:
        if r["default_tds_section"] not in tds:
            problems.append(f"category {r['code']}: TDS section {r['default_tds_section']} is not known")
    for r in t["supplier"]:
        if r["tds_section_default"] not in tds:
            problems.append(f"supplier {r['code']}: TDS section {r['tds_section_default']} is not known")

    # segregation of duties: requester, approver, receiver, AP and payer are different people
    held: dict[str, set[str]] = defaultdict(set)
    for r in t["user_role"]:
        held[r["user_code"]].add(r["role"])
    for code, roles in held.items():
        if len(roles & set(JOBS)) > 1:
            problems.append(f"{code} holds {sorted(roles & set(JOBS))}: one person, one job")
    for job in ("approver", "store", "ap", "finance", "buyer"):
        if not any(job in roles for roles in held.values()):
            problems.append(f"nobody holds the job '{job}'")
    if not any(roles == {"requester"} for roles in held.values()):
        problems.append("nobody is only a requester")

    # approval bands: for each document and scope, from zero to the top with no gap and no overlap
    bands: dict[tuple[str, ...], list[tuple[Decimal, Decimal]]] = defaultdict(list)
    for r in t["approval_rule"]:
        key = (r["document"], r["category_code"], r["department_code"], r["spend_type"])
        bands[key].append((Decimal(r["min_value"]), Decimal(r["max_value"])))
    for key, spans in bands.items():
        spans.sort()
        edges = [Decimal(0)] + [hi + Decimal("0.01") for _, hi in spans[:-1]]
        if [lo for lo, _ in spans] != edges or spans[-1][1] != Decimal(TOP):
            problems.append(f"approval bands for {'/'.join(key)} leave a gap or overlap")
    for document in ("request", "po", "price_variance", "payment_run", "supplier_activation"):
        if not any(k[0] == document for k in bands):
            problems.append(f"no approval rule for {document}")

    # budgets: every cost centre and every location with cost centres has every month, the same amount each month; a row
    # belongs to a cost centre or to a location, never both
    per_month: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    for r in t["budget"]:
        holder = r["cost_centre_code"] or r["location_code"]
        if bool(r["cost_centre_code"]) == bool(r["location_code"]):
            problems.append(f"budget row {r['gl_code']} {r['period']}: neither or both of cost centre and location")
        if not MONEY.match(r["amount"]):
            problems.append(f"budget {holder} {r['period']}: amount {r['amount']} is not rupees and paise")
        per_month[(holder, r["period"])] += Decimal(r["amount"])
    holders = {r["code"] for r in t["cost_centre"]} | {r["location_code"] for r in t["cost_centre"]}
    for holder in holders:
        amounts = {amount for (c, _), amount in per_month.items() if c == holder}
        if len(amounts) != 1:
            problems.append(f"{holder} has no budget or a different budget in different months")

    # stock: a stocked item has a plan and an opening balance at the location; min-max reaches above its point
    planned = {r["item_code"]: r for r in t["item_location"]}
    opening = {r["item_code"] for r in t["stock_opening"]}
    for r in t["item"]:
        stocked = r["stocked"] == "true"
        if stocked != (r["code"] in planned) or stocked != (r["code"] in opening):
            problems.append(f"item {r['code']}: stocked is {r['stocked']} but its plan or opening stock says otherwise")
    for p in planned.values():
        if p["reorder_policy"] == "minmax" and Decimal(p["max_qty"]) <= Decimal(p["reorder_point"]):
            problems.append(
                f"item {p['item_code']}: order up to {p['max_qty']} is not above the point {p['reorder_point']}"
            )

    # prices: in the item's unit, from a supplier approved for its category, within the contract's dates
    item_of = {r["code"]: r for r in t["item"]}
    approved = {(r["supplier_code"], r["category_code"]) for r in t["supplier_category"]}
    contract_of = {r["code"]: r for r in t["contract"]}
    for r in t["price_record"]:
        item = item_of.get(r["item_code"])
        if item is None:
            continue
        if r["uom"] != item["base_uom"]:
            problems.append(
                f"price of {r['item_code']} from {r['supplier_code']} is per {r['uom']}, not per {item['base_uom']}"
            )
        if (r["supplier_code"], item["category_code"]) not in approved:
            problems.append(
                f"{r['supplier_code']} is not approved for {item['category_code']} but prices {r['item_code']}"
            )
        if not MONEY.match(r["price"]):
            problems.append(
                f"price of {r['item_code']} from {r['supplier_code']}: {r['price']} is not rupees and paise"
            )
        c = contract_of.get(r["contract_code"])
        if c and not (c["valid_from"] <= r["valid_from"] <= r["valid_to"] <= c["valid_to"]):
            problems.append(f"price of {r['item_code']} on {c['code']} runs outside the contract's dates")

    problems += _input_problems(out, t)
    if profile is not None:
        problems += _profile_problems(profile, t, per_month)
        if "history" in profile.data and not problems:
            problems += history_problems(out, profile)
    return problems


def _input_problems(out: Path, t: dict[str, list[dict[str, str]]]) -> list[str]:
    problems: list[str] = []
    files = {p.stem: read(p) for p in sorted((out / "inputs").glob("*.csv"))}
    if not files:
        return ["there are no input files"]
    for name, rows in files.items():
        for col, (folder, table, ref_col) in INPUT_REFERENCES.items():
            if not rows or col not in rows[0] or (folder == "inputs" and table == name):
                continue
            known = {r[ref_col] for r in (t[table] if folder == "master" else files.get(table, []))}
            for r in rows:
                if r[col] not in known:
                    problems.append(f"inputs/{name}: {col} '{r[col]}' is not in {table}")
        if rows and "unit" in rows[0] and "item_code" in rows[0]:
            unit_of = {r["code"]: r["base_uom"] for r in t["item"]}
            for r in rows:
                if r["item_code"] in unit_of and r["unit"] != unit_of[r["item_code"]]:
                    problems.append(
                        f"inputs/{name}: {r['item_code']} is in {r['unit']}, the item master says {unit_of[r['item_code']]}"
                    )
    if "production_order" in files:
        wanted = production_lines(files.get("bom", []), files["production_order"])
        if wanted != files.get("production_order_line"):
            problems.append("inputs/production_order_line is not the bill of materials times the order quantities")
    return problems


def _profile_problems(
    profile: CompanyProfile, t: dict[str, list[dict[str, str]]], per_month: dict[tuple[str, str], Decimal]
) -> list[str]:
    """What the dataset must agree with in its profile: who the chain roles are and the budget totals."""
    problems: list[str] = []
    roles = {role: (source, value) for role, source, value in profile.approver_roles}
    for r in t["approval_rule"]:
        for role in r["approver_chain"].split(">"):
            if role != "auto" and role not in roles:
                problems.append(f"rule {r['rule_code']} names '{role}', which approver_roles does not define")
    for role, (source, value) in roles.items():
        if source == "designation" and sum(1 for u in t["user"] if u["designation"] == value) != 1:
            problems.append(f"role {role}: not exactly one person is '{value}'")
        if source == "department_head" and any(not d["head_user"] for d in t["department"]):
            problems.append(f"role {role}: a department has no head")
    plans = {**profile["budgets"]["per_month"], **profile["budgets"]["per_location"]}
    for holder, plan in plans.items():
        if sum((Decimal(v) for v in plan["gl"].values()), Decimal(0)) != Decimal(plan["total"]):
            problems.append(f"budget of {holder}: the GL split does not add up to {plan['total']}")
        if {a for (c, _), a in per_month.items() if c == holder} != {Decimal(plan["total"])}:
            problems.append(f"budget of {holder} is not {plan['total']} every month")
    return problems


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("company", nargs="?", help="a folder name under datagen/catalog, e.g. apex")
    p.add_argument("--out", help="default: data/datasets/<dataset>")
    p.add_argument("--check", metavar="DATASET", help="only check an existing dataset")
    args = p.parse_args(argv)
    if args.check:
        folder = Path(args.check)
        name = json.loads((folder / "manifest.json").read_text(encoding="utf-8")).get("profile")
        problems = check(folder, CompanyProfile.named(name) if name else None)
    elif args.company:
        profile = CompanyProfile.named(args.company)
        folder = Path(args.out) if args.out else DATASETS / profile.dataset
        counts = build(profile, folder)
        problems = check(folder, profile)
        print(
            f"wrote {sum(counts['master'].values())} master rows in {len(counts['master'])} tables and "
            f"{sum(counts['inputs'].values())} input rows in {len(counts['inputs'])} files and "
            f"{sum(counts['history'].values())} history rows in {len(counts['history'])} files to {folder}"
        )
    else:
        p.error("name a company or --check a dataset")
    for line in problems:
        print("  " + line)
    if args.check:
        print("dataset is as designed" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
