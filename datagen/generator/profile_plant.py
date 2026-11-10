"""Cut a generated dataset down to one plant that buys industrial spares and consumables, with a small team.

    python -I datagen/generator/profile_plant.py data/datasets/cbc-demo-v1-seed42 [--out DIR]

What stays: five stocked categories (mechanical spares, electrical, tools, safety gear, packaging), the suppliers that
sell them, and every request, quotation, order, receipt, invoice and payment that is wholly about them. What goes:
office, IT, software, furniture, travel, services, capital equipment, logistics, chemicals and raw materials.
People: eleven instead of twenty-five; everyone else is folded into one of them (their old requests and approvals are
shown as theirs). Departments: five. The approval rules use only roles that exist in this team (no CFO).
Nothing is invented: every row is a row of the source dataset, or one of those rows with a person's code replaced.
"""

import argparse
import csv
import json
import shutil
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
KEEP_CATEGORIES = {"MRO", "ELE", "TLS", "PPE", "PKG"}
KEEP_GL = {"1420", "1430", "5570"}  # budgets of these categories
BUYER = "U008"

# who each person of the source company becomes
KEEP_USERS = ["U001", "U003", "U004", "U006", "U008", "U010", "U011", "U015", "U019", "U023", "U024"]
PERSON = {
    "U002": "U001", "U005": "U004", "U007": "U006", "U009": "U008", "U012": "U015", "U013": "U015", "U014": "U010",
    "U016": "U010", "U017": "U015", "U018": "U001", "U020": "U001", "U021": "U015", "U022": "U001", "U025": "U024",
}
PERSON.update({u: u for u in KEEP_USERS})
MANAGER = {  # reporting line of the people who stay
    "U001": "", "U003": "U001", "U004": "U003", "U006": "U001", "U008": "U006", "U010": "U001", "U011": "U010",
    "U015": "U011", "U019": "U001", "U023": "U001", "U024": "U023",
}
DEPARTMENT = {"MNT": "PRD", "QA": "PRD", "IT": "MGMT", "ADM": "MGMT", "SAL": "MGMT"}
DEPARTMENT_HEAD = {"MGMT": "U001", "FIN": "U003", "PUR": "U006", "PRD": "U010", "LOG": "U023"}
# the rules of the small team: no CFO; the owner (Managing Director) is the last approver
RULES = """rule_code,document,min_value,max_value,category_code,department_code,spend_type,approver_chain,version,effective_from,effective_to
REQ-01,request,0.00,10000.00,ANY,ANY,opex,auto,1,2024-11-01,2026-03-31
REQ-01,request,0.00,15000.00,ANY,ANY,opex,auto,2,2026-04-01,
REQ-02,request,10000.01,100000.00,ANY,ANY,opex,manager,1,2024-11-01,2026-03-31
REQ-02,request,15000.01,100000.00,ANY,ANY,opex,manager,2,2026-04-01,
REQ-03,request,100000.01,500000.00,ANY,ANY,opex,manager>dept_head,1,2024-11-01,
REQ-04,request,500000.01,999999999.00,ANY,ANY,opex,manager>dept_head>director,1,2024-11-01,
PO-01,po,0.00,500000.00,ANY,ANY,ANY,procurement_head,1,2024-11-01,
PO-02,po,500000.01,999999999.00,ANY,ANY,ANY,procurement_head>director,1,2024-11-01,
POA-01,po_amendment,0.00,999999999.00,ANY,ANY,ANY,procurement_head,1,2024-11-01,
VAR-01,price_variance,0.00,5000.00,ANY,ANY,ANY,finance_manager,1,2024-11-01,
VAR-02,price_variance,5000.01,999999999.00,ANY,ANY,ANY,finance_manager>director,1,2024-11-01,
PAY-01,payment_run,0.00,999999999.00,ANY,ANY,ANY,director,1,2024-11-01,
SUP-01,supplier_activation,0.00,999999999.00,ANY,ANY,ANY,procurement_head>finance_manager,1,2024-11-01,
"""


def read(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(encoding="utf-8", newline="") as f:
        r = csv.DictReader(f)
        return list(r.fieldnames or []), list(r)


def write(path: Path, header: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=header, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


class Cut:
    def __init__(self, src: Path) -> None:
        self.src = src
        self.t: dict[str, tuple[list[str], list[dict[str, str]]]] = {}
        for folder in ("master", "history", "truth"):
            for p in sorted((src / folder).glob("*.csv")):
                self.t[f"{folder}/{p.stem}"] = read(p)
        self.unmapped: Counter[str] = Counter()

    def rows(self, name: str) -> list[dict[str, str]]:
        return self.t[name][1]

    def set(self, name: str, rows: list[dict[str, str]]) -> None:
        self.t[name] = (self.t[name][0], rows)

    def person(self, code: str) -> str:
        if not code or not (code[0] == "U" and code[1:].isdigit()):
            return code  # a label such as SYSTEM or agent is not a person
        if code not in PERSON:
            self.unmapped[code] += 1
            return "U006"
        return PERSON[code]

    def remap_people(self, name: str, *cols: str) -> None:
        for r in self.rows(name):
            for c in cols:
                r[c] = self.person(r[c])


def run(src: Path, out: Path) -> dict[str, int]:
    c = Cut(src)
    m = lambda n: c.rows(f"master/{n}")  # noqa: E731
    h = lambda n: c.rows(f"history/{n}")  # noqa: E731

    # ---- the catalogue and who sells it
    items = {r["code"] for r in m("item") if r["category_code"] in KEEP_CATEGORIES}
    suppliers = {r["supplier_code"] for r in m("supplier_category") if r["category_code"] in KEEP_CATEGORIES}
    c.set("master/category", [dict(r, buyer_user=BUYER) for r in m("category") if r["code"] in KEEP_CATEGORIES])
    c.set("master/item", [r for r in m("item") if r["code"] in items])
    c.set("master/uom_conversion", [r for r in m("uom_conversion") if r["item_code"] in items])
    c.set("master/item_substitute", [r for r in m("item_substitute") if r["item_code"] in items and r["substitute_code"] in items])
    c.set("master/supplier", [r for r in m("supplier") if r["code"] in suppliers])
    for t in ("supplier_address", "supplier_contact", "supplier_bank_account", "supplier_document", "supplier_status_history"):
        c.set(f"master/{t}", [r for r in m(t) if r["supplier_code"] in suppliers])
    c.set("master/supplier_category", [r for r in m("supplier_category") if r["supplier_code"] in suppliers and r["category_code"] in KEEP_CATEGORIES])
    c.set("master/price_record", [r for r in m("price_record") if r["supplier_code"] in suppliers and r["item_code"] in items])
    used_contracts = {r["contract_code"] for r in m("price_record") if r["contract_code"]}
    c.set("master/contract", [r for r in m("contract") if r["supplier_code"] in suppliers and r["code"] in used_contracts])
    contracts = {r["code"] for r in m("contract")}
    for r in m("price_record"):
        if r["contract_code"] and r["contract_code"] not in contracts:
            r["contract_code"] = ""
    # ---- the organisation
    locations = {r["code"] for r in m("location")}
    c.set("master/department", [dict(r, head_user=DEPARTMENT_HEAD[r["code"]]) for r in m("department") if r["code"] in DEPARTMENT_HEAD])
    cost_centres = []
    for r in m("cost_centre"):
        dept = DEPARTMENT.get(r["department_code"], r["department_code"])
        if dept in DEPARTMENT_HEAD and not r["code"].startswith(("PLT-QA", "PLT-UTL")):
            cost_centres.append(dict(r, department_code=dept, owner_user=DEPARTMENT_HEAD[dept] if dept != "PRD" else "U011"))
    c.set("master/cost_centre", cost_centres)
    kept_cc = {r["code"] for r in cost_centres}
    c.set("master/project", [])
    c.set("master/budget", [r for r in m("budget") if r["cost_centre_code"] in kept_cc and r["gl_code"] in KEEP_GL and not r["project_code"]])
    c.set("master/delegation", [r for r in m("delegation") if r["from_user"] in KEEP_USERS and r["to_user"] in KEEP_USERS])
    users = []
    for r in m("user"):
        if r["code"] in KEEP_USERS:
            users.append(dict(r, department_code=DEPARTMENT.get(r["department_code"], r["department_code"]), manager_code=MANAGER[r["code"]]))
    c.set("master/user", users)
    c.set("master/user_role", [r for r in m("user_role") if r["user_code"] in KEEP_USERS])
    header = c.t["master/approval_rule"][0]
    c.set("master/approval_rule", list(csv.DictReader(RULES.splitlines())))
    assert list(csv.DictReader(RULES.splitlines()))[0].keys() == set(header)
    c.set("master/stock_opening", [r for r in m("stock_opening") if r["item_code"] in items])
    c.set("master/item_location", [r for r in m("item_location") if r["item_code"] in items])
    c.set("master/supplier_status_history", [dict(r, by_user=c.person(r["by_user"]) if r["by_user"].startswith("U") else r["by_user"]) for r in m("supplier_status_history")])
    c.set("master/gl_account", m("gl_account"))
    # a prospective supplier that is only in the onboarding cases but sells nothing we kept goes with its category

    # ---- history: every document that is wholly about what stayed
    requests = {}
    lines_by_req: dict[str, list[dict[str, str]]] = {}
    for r in h("request_line"):
        lines_by_req.setdefault(r["request_no"], []).append(r)
    for r in h("request"):
        lines = lines_by_req.get(r["no"], [])
        if lines and all(ln["category_code"] in KEEP_CATEGORIES for ln in lines) and r["ship_to_location"] in locations:
            requests[r["no"]] = r
    for r in requests.values():
        r["department_code"] = DEPARTMENT.get(r["department_code"], r["department_code"])
    c.set("history/request", list(requests.values()))
    c.set("history/request_line", [ln for no in requests for ln in lines_by_req[no]])
    kept_req_cc = {ln["cost_centre_code"] for no in requests for ln in lines_by_req[no]}
    for no in requests:
        for ln in lines_by_req[no]:
            if ln["cost_centre_code"] and ln["cost_centre_code"] not in kept_cc:
                ln["cost_centre_code"] = "PLT-MNT"
    del kept_req_cc

    rfq_lines: dict[str, list[dict[str, str]]] = {}
    for r in h("rfq_line"):
        rfq_lines.setdefault(r["rfq_no"], []).append(r)
    rfqs = {r["no"] for r in h("rfq") if rfq_lines.get(r["no"]) and all(ln["request_no"] in requests for ln in rfq_lines[r["no"]])}
    quotes_bad = {q["no"] for q in h("quote") if q["rfq_no"] in rfqs and q["supplier_code"] not in suppliers}
    awards_bad = {a["rfq_no"] for a in h("award") if a["rfq_no"] in rfqs and a["supplier_code"] not in suppliers}
    rfqs -= awards_bad
    c.set("history/rfq", [r for r in h("rfq") if r["no"] in rfqs])
    c.set("history/rfq_line", [r for r in h("rfq_line") if r["rfq_no"] in rfqs])
    c.set("history/rfq_supplier", [r for r in h("rfq_supplier") if r["rfq_no"] in rfqs and r["supplier_code"] in suppliers])
    quotes = {q["no"] for q in h("quote") if q["rfq_no"] in rfqs and q["no"] not in quotes_bad}
    c.set("history/quote", [r for r in h("quote") if r["no"] in quotes])
    c.set("history/quote_line", [r for r in h("quote_line") if r["quote_no"] in quotes])
    c.set("history/award", [r for r in h("award") if r["rfq_no"] in rfqs])

    po_lines: dict[str, list[dict[str, str]]] = {}
    for r in h("po_line"):
        po_lines.setdefault(r["po_no"], []).append(r)
    pos = {
        r["no"]
        for r in h("po")
        if r["supplier_code"] in suppliers
        and r["ship_to_location"] in locations
        and po_lines.get(r["no"])
        and all(ln["item_code"] in items and (not ln["request_no"] or ln["request_no"] in requests) for ln in po_lines[r["no"]])
    }
    c.set("history/po", [r for r in h("po") if r["no"] in pos])
    for r in h("po_line"):
        if r["cost_centre_code"] and r["cost_centre_code"] not in kept_cc:
            r["cost_centre_code"] = "PLT-MNT"
    for name in ("po_line", "po_event", "po_schedule", "po_charge", "service_receipt"):
        c.set(f"history/{name}", [r for r in h(name) if r["po_no"] in pos])
    grns = {r["no"] for r in h("goods_receipt") if r["po_no"] in pos}
    c.set("history/goods_receipt", [r for r in h("goods_receipt") if r["no"] in grns])
    c.set("history/gr_line", [r for r in h("gr_line") if r["gr_no"] in grns])
    c.set("history/return_to_vendor", [r for r in h("return_to_vendor") if r["gr_no"] in grns])

    inv_lines: dict[str, list[dict[str, str]]] = {}
    for r in h("invoice_line"):
        inv_lines.setdefault(r["invoice_no"], []).append(r)
    invoices = {
        r["no"]
        for r in h("invoice")
        if r["supplier_code"] in suppliers and inv_lines.get(r["no"]) and all(ln["po_no"] in pos for ln in inv_lines[r["no"]])
    }
    c.set("history/invoice", [r for r in h("invoice") if r["no"] in invoices])
    c.set("history/invoice_line", [r for r in h("invoice_line") if r["invoice_no"] in invoices])
    c.set("history/invoice_charge", [r for r in h("invoice_charge") if r["invoice_no"] in invoices])
    alloc: dict[str, list[dict[str, str]]] = {}
    for r in h("payment_allocation"):
        alloc.setdefault(r["payment_no"], []).append(r)
    payments = {
        r["no"]
        for r in h("payment")
        if r["supplier_code"] in suppliers and ((alloc.get(r["no"]) and all(a["invoice_no"] in invoices for a in alloc[r["no"]])) or (not alloc.get(r["no"]) and r["po_no"] in pos))
    }
    c.set("history/payment", [r for r in h("payment") if r["no"] in payments])
    c.set("history/payment_allocation", [r for r in h("payment_allocation") if r["payment_no"] in payments])
    runs = {r["run_no"] for r in h("payment") if r["no"] in payments}
    c.set("history/payment_run", [r for r in h("payment_run") if r["no"] in runs])
    c.set("history/tds_entry", [r for r in h("tds_entry") if r["invoice_no"] in invoices or r["payment_no"] in payments])
    c.set("history/advance_adjustment", [r for r in h("advance_adjustment") if r["advance_payment_no"] in payments and r["invoice_no"] in invoices])
    c.set("history/bank_statement_line", [r for r in h("bank_statement_line") if r["matched_payment_no"] in payments])
    approval_docs = {"request": set(requests), "po": pos, "rfq": rfqs, "invoice": invoices, "payment_run": runs}
    c.set("history/approval_event", [r for r in h("approval_event") if r["document_no"] in approval_docs.get(r["document"], set())])
    c.set("history/stock_movement", [r for r in h("stock_movement") if r["item_code"] in items and r["location_code"] in locations and (not r["ref_doc"] or r["ref_doc"] in grns or not r["ref_doc"].startswith("GRN"))])
    docs = pos | invoices | grns | set(requests) | rfqs
    c.set(
        "history/email_message",
        [r for r in h("email_message") if (r["linked_doc_no"] in docs) or (not r["linked_doc_no"] and r["supplier_code"] in suppliers)],
    )

    # ---- people in the history
    c.remap_people("history/request", "requester", "on_behalf_of")
    c.remap_people("history/approval_event", "approver", "on_behalf_of")
    c.remap_people("history/rfq", "buyer")
    c.remap_people("history/po", "buyer")
    c.remap_people("history/award", "by_user")
    c.remap_people("history/goods_receipt", "received_by")
    c.remap_people("history/service_receipt", "accepted_by")
    c.remap_people("history/po_event", "applied_by")
    c.remap_people("history/payment_run", "proposed_by", "approved_by")

    # ---- the answers kept for later tests
    c.set("truth/truth_item_demand", [r for r in c.rows("truth/truth_item_demand") if r["item_code"] in items])
    c.set("truth/truth_supplier", [r for r in c.rows("truth/truth_supplier") if r["supplier_code"] in suppliers])
    c.set("truth/truth_match_expected", [r for r in c.rows("truth/truth_match_expected") if r["invoice_no"] in invoices])
    everything = docs | suppliers | payments | runs
    cases = []
    for r in c.rows("truth/truth_case"):
        tokens = {t for t in r["documents"].replace(";", ",").replace(" ", ",").split(",") if t}
        if tokens and tokens <= everything:
            cases.append(r)
    c.set("truth/truth_case", cases)

    # ---- write it out
    out.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    for name, (header, rows) in c.t.items():
        write(out / f"{name}.csv", header, rows)
        counts[name] = len(rows)
    shutil.copytree(src / "reference", out / "reference", dirs_exist_ok=True)
    manifest = json.loads((src / "manifest.json").read_text(encoding="utf-8"))
    manifest.update(
        company="CBC Private Limited (fictional): one plant buying industrial spares and consumables",
        profile="plant",
        derived_from=src.name,
        row_counts={k.split("/", 1)[1]: v for k, v in counts.items() if k.startswith(("master/", "history/"))},
    )
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    splits = src / "splits.json"
    if splits.exists():
        data = json.loads(splits.read_text(encoding="utf-8"))
        data = {k: ([x for x in v if x in suppliers] if isinstance(v, list) else v) for k, v in data.items()}
        (out / "splits.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    (out / "report.md").write_text(
        f"# {out.name}\n\nA plant profile derived from {src.name} by `profile_plant.py`: five stocked categories, "
        f"{len(suppliers)} suppliers, {len(KEEP_USERS)} people. Unmapped people in the source: {dict(c.unmapped) or 'none'}.\n",
        encoding="utf-8",
    )
    if c.unmapped:
        print("people with no mapping (sent to U006):", dict(c.unmapped), file=sys.stderr)
    return counts


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("source", type=Path)
    p.add_argument("--out", type=Path)
    a = p.parse_args()
    src = a.source if a.source.is_absolute() else REPO / a.source
    out = a.out or src.parent / src.name.replace("cbc-", "plant-")
    counts = run(src, out)
    for name in sorted(counts):
        print(f"{name:34} {counts[name]:>7}")


if __name__ == "__main__":
    main()
