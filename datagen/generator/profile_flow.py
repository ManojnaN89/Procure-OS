"""The small demo company for walking through the purchase flow: one plant, eight people, twelve items, four suppliers.

    python -I datagen/generator/profile_flow.py [--out data/datasets/flow-demo-v1]
    python -I datagen/generator/profile_flow.py --check data/datasets/flow-demo-v1

No history: no old requests, orders or invoices. The first screens are clean and the first action is obvious: stock is
low on four items, so "Check stock levels" on the Requisitions page creates three requests (one approves itself, two
go to the maintenance manager).

Everything here is written out by hand (nothing is cut from another dataset), except four tables of plain configuration
that live in datagen/catalog/flow: the chart of accounts, the banks, the payment terms and the document number series.
Safety rules of this repo: PANs carry the digits 0000 (never issued), phones are +91 00000 ..., emails end in .example.
"""

import argparse
import csv
import json
import re
import shutil
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CATALOG = REPO / "datagen" / "catalog" / "flow"
DEFAULT_OUT = REPO / "data" / "datasets" / "flow-demo-v1"
VERSION = "flow-demo-v1"

PLANT = "PLT-CKN"
ASOF = "2026-11-01"  # opening stock date
FY_START, FY_END = "2026-04-01", "2027-03-31"
ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def gstin(state: str, pan: str, entity: str = "1") -> str:
    """State code, PAN, entity number, Z and the check character (the same rule the platform checks)."""
    first14 = f"{state}{pan}{entity}Z"
    total = 0
    for i, ch in enumerate(first14):
        product = ALPHABET.index(ch) * (1 if i % 2 == 0 else 2)
        total += product // 36 + product % 36
    return first14 + ALPHABET[(36 - total % 36) % 36]


COMPANY_PAN = "FLWCP0000K"
PLANT_GSTIN = gstin("27", COMPANY_PAN)

# ---- the company and its people --------------------------------------------------------------

COMPANY = {
    "code": "FLOW",
    "legal_name": "Flow Engineering Private Limited",
    "short_name": "Flow Engineering",
    "pan": COMPANY_PAN,
    "cin": "U29299MH2015PTC000001",
    "registered_address": "Plot 7, MIDC Chakan Phase II, Pune, Maharashtra 410501",
    "turnover_band": "10-500cr",
    "fy_start_month": "4",
    "currency": "INR",
    "email_domain": "flow.example",
    "letterhead_text": "Flow Engineering Private Limited | CIN U29299MH2015PTC000001 | Plot 7, MIDC Chakan Phase II, Pune 410501",
}

LOCATION = {
    "code": PLANT, "name": "Plant, Chakan (Pune)", "type": "plant", "gstin": PLANT_GSTIN,
    "address": "Plot 7, MIDC Chakan Phase II", "city": "Pune", "state_code": "27", "pincode": "410501",
    "is_stock_location": "true", "working_days": "Mon-Sat", "receiving_hours": "08:00-17:00",
    "holiday_scope": "central;27", "loc_short": "CKN",
}  # fmt: skip

DEPARTMENTS = [
    ("MNT", "Maintenance", "U02"),
    ("STR", "Stores", "U04"),
    ("PUR", "Purchase", "U05"),
    ("ACC", "Accounts", "U07"),
]

# code, login name (the role, no personal names), designation, department, manager, approval limit, login role,
# roles in the organisation's own table. The email is `<login>@flow.example`.
PEOPLE = [
    ("U01", "Plant Head", "Plant Head", "MNT", "", "99999999.00", "Approver", ("requester", "approver")),
    (
        "U02",
        "Maintenance Manager",
        "Maintenance Manager",
        "MNT",
        "U01",
        "100000.00",
        "Approver",
        ("requester", "approver"),
    ),
    ("U03", "Requester", "Maintenance Engineer", "MNT", "U02", "0.00", "Requester", ("requester",)),
    ("U04", "Storekeeper", "Storekeeper", "STR", "U01", "0.00", "Store", ("requester", "store")),
    ("U05", "Buyer", "Buyer", "PUR", "U01", "0.00", "Buyer", ("requester", "buyer")),
    ("U06", "Accounts Payable", "Accounts Payable Executive", "ACC", "U07", "0.00", "AP", ("requester", "ap")),
    ("U07", "Finance Manager", "Finance Manager", "ACC", "U01", "500000.00", "Finance", ("requester", "finance")),
    ("U08", "Admin", "System Administrator", "ACC", "U01", "0.00", "Admin", ("requester", "admin")),
]
LOGIN = {  # code -> the part of the email before the @
    "U01": "approver", "U02": "manager", "U03": "requester", "U04": "store",
    "U05": "buyer", "U06": "ap", "U07": "finance", "U08": "admin",
}  # fmt: skip


def email_of(code: str) -> str:
    return LOGIN[code] + "@flow.example"


# ---- approval rules: up to Rs 10,000 approves itself, up to Rs 1,00,000 the maintenance manager, above that the plant head
RULES = """rule_code,document,min_value,max_value,category_code,department_code,spend_type,approver_chain,version,effective_from,effective_to
REQ-01,request,0.00,10000.00,ANY,ANY,ANY,auto,1,2024-04-01,
REQ-02,request,10000.01,100000.00,ANY,ANY,ANY,manager,1,2024-04-01,
REQ-03,request,100000.01,999999999.00,ANY,ANY,ANY,plant_head,1,2024-04-01,
PO-01,po,0.00,999999999.00,ANY,ANY,ANY,plant_head,1,2024-04-01,
POA-01,po_amendment,0.00,999999999.00,ANY,ANY,ANY,plant_head,1,2024-04-01,
VAR-01,price_variance,0.00,999999999.00,ANY,ANY,ANY,plant_head,1,2024-04-01,
PAY-01,payment_run,0.00,999999999.00,ANY,ANY,ANY,plant_head,1,2024-04-01,
SUP-01,supplier_activation,0.00,999999999.00,ANY,ANY,ANY,plant_head,1,2024-04-01,
"""

SETTINGS = [
    ("budget_policy", "warn", "Budget check result when a request exceeds the available budget (warn or block)"),
    (
        "duplicate_window_days",
        "14",
        "A request for the same item and location within this many days is flagged as a possible duplicate",
    ),
    (
        "split_order_window_days",
        "7",
        "Requests to the same supplier or category within this window are summed to detect split orders",
    ),
    ("default_rfq_response_days", "5", "Working days suppliers get to respond to an RFQ"),
    ("rfq_reminder_after_days", "2", "Working days after which a silent supplier is reminded"),
    ("supplier_ack_due_days", "2", "Working days within which a PO acknowledgement is expected"),
    ("price_band_pct", "15", "A quote deviating more than this percent from the benchmark price is flagged"),
    ("cost_of_capital_pct", "12", "Used for early-payment discount decisions"),
    ("min_quotes_default", "2", "Minimum quotes when the category does not say"),
    ("working_saturdays", "none", "Company rule for Saturdays at head office (none or 1st-3rd-5th)"),
    ("approval_reminder_hours", "24", "Hours before an approver is reminded"),
    ("approval_escalation_hours", "72", "Hours before a pending approval escalates to the next level"),
    ("auto_approve_requires_budget", "true", "Auto-approval only when budget is available"),
    ("po_send_requires_confirmation", "true", "A person confirms before a PO is sent to a supplier"),
    ("grn_edit_window_days", "3", "Days a goods receipt can be corrected after posting"),
    ("invoice_receipt_mailbox", "invoices@flow.example", "Mailbox where supplier invoices arrive"),
    ("request_mailbox", "requests@flow.example", "Mailbox where staff email requests"),
    ("purchase_mailbox", "purchase@flow.example", "Mailbox buyers use for supplier correspondence"),
    ("tds_applicable_on_goods", "true", "Buyer turnover is above Rs 10 crore so TDS on purchase of goods applies"),
]

TOLERANCES = [
    ("price_pct", "2.00", "Invoice price above PO price by this percent is within tolerance"),
    ("price_abs", "500.00", "Or by this many rupees per line"),
    ("qty_pct", "0.00", "Invoiced quantity above received quantity is not allowed"),
    ("small_diff_abs", "100.00", "Total invoice difference up to this amount is accepted"),
    ("receipt_over_pct", "5.00", "Receipt above ordered quantity by this percent is accepted"),
    ("supplier_change_pct", "5.00", "Supplier quantity or price change accepted by the agent up to this percent"),
    ("supplier_date_days", "3", "Supplier delivery date slip accepted by the agent up to this many days"),
]

# ---- categories and items ---------------------------------------------------------------------

CATEGORIES = [
    ("MRO", "MRO spares (mechanical)", 1430, "mro", "size;make"),
    ("ELE", "Electrical items", 1430, "mro", "rating;make"),
    ("PPE", "Safety & PPE", 5570, "safety", "size;standard"),
]

# code, name, description, category, uom, hsn, gst %, price, lead days, moq, attributes, brand,
# then the plan at the plant: on hand, reorder point, safety stock, lot to order, maximum
ITEMS = [
    (
        "ITM-001",
        "Ball bearing 6205-2RS",
        "Deep groove ball bearing 25x52x15 mm, sealed both sides",
        "MRO",
        "NOS",
        "848210",
        18,
        "320.00",
        5,
        10,
        {"size": "6205-2RS", "make": "SKF"},
        "SKF",
        (40, 60, 20, 50, 150),
    ),
    (
        "ITM-002",
        "V-belt B-68",
        "V-belt, section B, 68 inch",
        "MRO",
        "NOS",
        "401032",
        18,
        "420.00",
        5,
        10,
        {"size": "B-68", "make": "Fenner"},
        "Fenner",
        (30, 10, 4, 20, 60),
    ),
    (
        "ITM-003",
        "Hydraulic hose 1/2 inch 2SN",
        "Two-wire braided hydraulic hose, 1/2 inch, per metre",
        "MRO",
        "MTR",
        "400942",
        18,
        "380.00",
        7,
        10,
        {"size": "1/2 inch", "make": "Gates"},
        "Gates",
        (100, 40, 15, 50, 200),
    ),
    (
        "ITM-004",
        "Mechanical seal 25 mm",
        "Single spring mechanical seal for 25 mm shaft",
        "MRO",
        "NOS",
        "848420",
        18,
        "6500.00",
        14,
        2,
        {"size": "25 mm", "make": "John Crane"},
        "John Crane",
        (3, 4, 2, 6, 12),
    ),
    (
        "ITM-005",
        "Lithium grease EP2 18 kg",
        "Extreme pressure lithium grease, 18 kg pail",
        "MRO",
        "NOS",
        "340399",
        18,
        "4200.00",
        7,
        1,
        {"size": "18 kg", "make": "Servo"},
        "Servo",
        (8, 3, 1, 4, 12),
    ),
    (
        "ITM-006",
        "Contactor 32 A 3 pole",
        "Three pole power contactor, 32 A, 230 V coil",
        "ELE",
        "NOS",
        "853649",
        18,
        "3100.00",
        7,
        2,
        {"rating": "32 A", "make": "Siemens"},
        "Siemens",
        (5, 6, 2, 4, 14),
    ),
    (
        "ITM-007",
        "MCB 32 A single pole C curve",
        "Miniature circuit breaker, 32 A, single pole, C curve",
        "ELE",
        "NOS",
        "853620",
        18,
        "320.00",
        5,
        6,
        {"rating": "32 A", "make": "Havells"},
        "Havells",
        (24, 10, 4, 20, 60),
    ),
    (
        "ITM-008",
        "PVC copper cable 2.5 sq mm",
        "Flexible PVC insulated copper cable, 2.5 sq mm, 90 m roll",
        "ELE",
        "ROL",
        "854449",
        18,
        "2950.00",
        5,
        2,
        {"rating": "2.5 sq mm", "make": "Polycab"},
        "Polycab",
        (4, 3, 1, 4, 10),
    ),
    (
        "ITM-009",
        "Push button 22 mm green",
        "Push button, 22 mm, green, momentary",
        "ELE",
        "NOS",
        "853650",
        18,
        "180.00",
        5,
        10,
        {"rating": "22 mm", "make": "L&T"},
        "L&T",
        (40, 15, 5, 20, 80),
    ),
    (
        "ITM-010",
        "Safety helmet",
        "Industrial safety helmet with chin strap",
        "PPE",
        "NOS",
        "650610",
        18,
        "185.00",
        5,
        10,
        {"size": "Universal", "standard": "IS 2925"},
        "Karam",
        (30, 10, 5, 20, 60),
    ),
    (
        "ITM-011",
        "Safety shoes steel toe",
        "Steel toe safety shoes, per pair",
        "PPE",
        "PRS",
        "640340",
        5,
        "1150.00",
        7,
        5,
        {"size": "6-10", "standard": "IS 15298"},
        "Liberty",
        (12, 15, 6, 8, 40),
    ),
    (
        "ITM-012",
        "Cotton hand gloves",
        "Knitted cotton hand gloves, dozen pairs",
        "PPE",
        "DOZ",
        "611692",
        5,
        "220.00",
        5,
        5,
        {"size": "Free", "standard": "IS 6994"},
        "Karam",
        (11, 10, 4, 10, 30),
    ),
]

# ---- suppliers --------------------------------------------------------------------------------

# code, name, form, pan, state, e-invoice, hsn digits, msme, udyam, agreement days, terms, turnover, bank, contact, categories, city
SUPPLIERS = [
    (
        "SUP-001",
        "Sai Bearings & Transmission",
        "proprietorship",
        "SAIPB0000A",
        "27",
        False,
        "4",
        "micro",
        "UDYAM-MH-00-1000001",
        45,
        "NET60",
        "1.5-5cr",
        "HDFC",
        "Sales Desk",
        "saibearings",
        ("MRO",),
        "Pune",
        "411026",
    ),
    (
        "SUP-002",
        "Western Electricals Pvt Ltd",
        "pvt_ltd",
        "WESCP0000D",
        "27",
        True,
        "6",
        "none",
        "",
        None,
        "NET30",
        "20-100cr",
        "UNION",
        "Sales Desk",
        "westernelectricals",
        ("ELE",),
        "Pune",
        "411018",
    ),
    (
        "SUP-003",
        "Deccan Industrial Supplies Pvt Ltd",
        "pvt_ltd",
        "DECCS0000G",
        "27",
        False,
        "6",
        "none",
        "",
        None,
        "NET30",
        "5-20cr",
        "ICICI",
        "Sales Desk",
        "deccansupplies",
        ("MRO", "ELE", "PPE"),
        "Pune",
        "411038",
    ),
    (
        "SUP-004",
        "Gujarat Safety & Tools Pvt Ltd",
        "pvt_ltd",
        "GUJCS0000H",
        "24",
        False,
        "6",
        "none",
        "",
        None,
        "NET45",
        "5-20cr",
        "SBI",
        "Sales Desk",
        "gujaratsafety",
        ("MRO", "ELE", "PPE"),
        "Ahmedabad",
        "380015",
    ),
]

CONTRACT = ("CNT-0001", "SUP-002", "rate_contract", FY_START, FY_END, "500000.00", "NET30")
# the rate contract covers three electrical items: item, uom, price, lead days
CONTRACT_PRICES = [("ITM-006", "NOS", "2950.00", 7), ("ITM-007", "NOS", "295.00", 5), ("ITM-008", "ROL", "2800.00", 5)]


# ---- writing ----------------------------------------------------------------------------------

EMPTY_TABLES = {
    "delegation": "from_user,to_user,start_date,end_date,reason",
    "item_substitute": "item_code,substitute_code,note",
    "uom_conversion": "item_code,from_uom,to_uom,factor",
    "project": "code,name,type,cost_centre_code,budget_amount,start_date,end_date,status",
    "sanction_entry": "name,country,list_name,listed_on,aliases",
}


def write(path: Path, header: list[str], rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=header, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def table(out: Path, name: str, header: str, rows: list[dict[str, object]]) -> None:
    write(out / "master" / f"{name}.csv", header.split(","), rows)


def months() -> list[str]:
    return [f"2026-{m:02d}" for m in range(4, 13)] + [f"2027-{m:02d}" for m in range(1, 4)]


def build(out: Path) -> dict[str, int]:
    out.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}

    def put(name: str, header: str, rows: list[dict[str, object]]) -> None:
        table(out, name, header, rows)
        counts[name] = len(rows)

    (out / "master").mkdir(parents=True, exist_ok=True)
    for name in ("gl_account", "bank", "payment_terms", "number_series"):
        shutil.copyfile(CATALOG / f"{name}.csv", out / "master" / f"{name}.csv")
        counts[name] = len(read(CATALOG / f"{name}.csv"))

    put("company", ",".join(COMPANY), [COMPANY])
    put("location", ",".join(LOCATION), [LOCATION])
    put("department", "code,name,head_user", [{"code": c, "name": n, "head_user": h} for c, n, h in DEPARTMENTS])
    put(
        "cost_centre",
        "code,name,department_code,location_code,owner_user,active",
        [
            {
                "code": f"CC-{c}",
                "name": n,
                "department_code": c,
                "location_code": PLANT,
                "owner_user": h,
                "active": "true",
            }
            for c, n, h in DEPARTMENTS
        ],
    )
    put(
        "user",
        "code,employee_no,name,designation,email,phone,department_code,location_code,manager_code,approval_limit,notify_by,active",
        [
            {
                "code": code,
                "employee_no": f"EMP{code[1:]:0>4}",
                "name": name,
                "designation": designation,
                "email": email_of(code),
                "phone": f"+91 00000 {int(code[1:]):05d}",
                "department_code": dept,
                "location_code": PLANT,
                "manager_code": manager,
                "approval_limit": limit,
                "notify_by": "both",
                "active": "true",
            }
            for code, name, designation, dept, manager, limit, _role, _roles in PEOPLE
        ],
    )
    put(
        "user_role",
        "user_code,role,scope_location,scope_department",
        [
            {"user_code": code, "role": role, "scope_location": "ANY", "scope_department": "ANY"}
            for code, _n, _d, _dept, _m, _l, _r, roles in PEOPLE
            for role in roles
        ],
    )
    put("approval_rule", RULES.splitlines()[0], list(csv.DictReader(RULES.splitlines())))
    put("setting", "key,value,description", [{"key": k, "value": v, "description": d} for k, v, d in SETTINGS])
    put(
        "tolerance",
        "key,value,category_code,note",
        [{"key": k, "value": v, "category_code": "ANY", "note": n} for k, v, n in TOLERANCES],
    )
    put(
        "company_bank_account",
        "code,bank_code,account_no,ifsc,purpose,payment_file_format",
        [
            {
                "code": "CBA-01",
                "bank_code": "ICICI",
                "account_no": "000000000101",
                "ifsc": "ICIC0009991",
                "purpose": "payments",
                "payment_file_format": "generic_csv",
            }
        ],
    )
    put(
        "gst_registration",
        "gstin,state_code,legal_address,pincode,is_default",
        [
            {
                "gstin": PLANT_GSTIN,
                "state_code": "27",
                "legal_address": "Plot 7, MIDC Chakan Phase II, Pune, Maharashtra 410501",
                "pincode": "410501",
                "is_default": "true",
            }
        ],
    )
    put(
        "budget",
        "cost_centre_code,gl_code,project_code,period,amount,version,policy",
        [
            {
                "cost_centre_code": "CC-MNT",
                "gl_code": gl,
                "project_code": "",
                "period": p,
                "amount": "2500000.00",
                "version": "1",
                "policy": "warn",
            }
            for gl in ("1430", "5570")
            for p in months()
        ],
    )
    put(
        "category",
        "code,name,parent_code,kind,default_gl,stocked,capex,min_quotes,buyer_user,approval_category,required_attributes,required_supplier_docs,inspection_required,default_tds_section,reverse_charge_default",
        [
            {
                "code": c,
                "name": n,
                "parent_code": "",
                "kind": "goods",
                "default_gl": gl,
                "stocked": "true",
                "capex": "false",
                "min_quotes": "2",
                "buyer_user": "U05",
                "approval_category": ap,
                "required_attributes": attrs,
                "required_supplier_docs": "gst_cert;pan",
                "inspection_required": "false",
                "default_tds_section": "194Q",
                "reverse_charge_default": "false",
            }
            for c, n, gl, ap, attrs in CATEGORIES
        ],
    )

    items, plan, stock = [], [], []
    gl_of = {c: gl for c, _n, gl, _a, _t in CATEGORIES}
    for code, name, desc, cat, uom, hsn, gst, price, lead, moq, attrs, brand, (
        on_hand,
        rop,
        safety,
        lot,
        maximum,
    ) in ITEMS:
        items.append(
            {
                "code": code,
                "name": name,
                "description": desc,
                "category_code": cat,
                "kind": "goods",
                "default_gl": gl_of[cat],
                "brand": brand,
                "base_uom": uom,
                "purchase_uom": uom,
                "pack_size": "1",
                "moq": moq,
                "hsn_sac": hsn,
                "gst_rate_before": gst,
                "gst_rate_after": gst,
                "gst_rate_alt_before": "",
                "gst_rate_alt_after": "",
                "gst_rate_change_date": "",
                "gst_rate_confidence": "CONFIRMED",
                "gst_reverse_charge": "no",
                "gst_note": "",
                "standard_price": price,
                "abc_class": "B",
                "stocked": "true",
                "capex": "false",
                "inspection_required": "false",
                "hazardous": "false",
                "default_lead_days": lead,
                "attributes": json.dumps(attrs, separators=(",", ":")),
                "active": "true",
            }
        )
        plan.append(
            {
                "item_code": code,
                "location_code": PLANT,
                "reorder_policy": "rop",
                "reorder_point": f"{rop}.000",
                "safety_stock": f"{safety}.000",
                "min_qty": f"{rop}.000",
                "max_qty": f"{maximum}.000",
                "order_qty": f"{lot}.000",
                "service_level_pct": "95",
                "lead_days_override": "",
            }
        )
        stock.append(
            {"item_code": code, "location_code": PLANT, "qty": f"{on_hand}.000", "unit_cost": price, "as_of": ASOF}
        )
    put("item", ",".join(items[0]), items)
    put("item_location", ",".join(plan[0]), plan)
    put("stock_opening", ",".join(stock[0]), stock)

    sup, addr, bank, cat_rows, contacts, docs, history = [], [], [], [], [], [], []
    for i, (
        code,
        name,
        form,
        pan,
        state,
        einv,
        hsn_d,
        msme,
        udyam,
        agreed,
        terms,
        turnover,
        bank_code,
        contact,
        domain,
        cats,
        city,
        pin,
    ) in enumerate(SUPPLIERS, start=1):
        g = gstin(state, pan)
        sup.append(
            {
                "code": code,
                "legal_name": name,
                "trade_name": name,
                "legal_form": form,
                "pan": pan,
                "pan_status": "operative",
                "gst_reg_type": "regular",
                "gstin": g,
                "state_code": state,
                "e_invoice_applicable": str(einv).lower(),
                "hsn_digits": hsn_d,
                "msme_category": msme,
                "udyam_no": udyam,
                "msme_agreement": str(agreed is not None).lower(),
                "agreed_payment_days": "" if agreed is None else agreed,
                "payment_terms_code": terms,
                "tds_section_default": "194Q",
                "lower_tds_rate": "",
                "lower_tds_valid_to": "",
                "lower_tds_limit": "",
                "reverse_charge": "no",
                "status": "active",
                "turnover_band": turnover,
            }
        )
        addr.append(
            {
                "supplier_code": code,
                "type": "registered",
                "gstin": g,
                "address": f"{10 + i}, Industrial Area",
                "city": city,
                "state_code": state,
                "pincode": pin,
            }
        )
        bank.append(
            {
                "supplier_code": code,
                "bank_code": bank_code,
                "account_no": f"00010000{i:04d}",
                "ifsc": f"{bank_code[:4].ljust(4, 'X')}0900{i:03d}",
                "verified": "true",
                "verified_on": "2024-09-15",
            }
        )
        cat_rows += [{"supplier_code": code, "category_code": c, "approved_from": "2024-04-01"} for c in cats]
        contacts.append(
            {
                "supplier_code": code,
                "name": contact,
                "role": "sales",
                "email": f"sales@{domain}.example",
                "phone": f"+91 00000 9{i:04d}",
                "is_primary": "true",
            }
        )
        docs += [
            {
                "supplier_code": code,
                "doc_type": "gst_cert",
                "number": g,
                "issued_on": "2019-10-07",
                "expires_on": "",
                "verified": "true",
            },
            {
                "supplier_code": code,
                "doc_type": "pan",
                "number": pan,
                "issued_on": "2015-04-01",
                "expires_on": "",
                "verified": "true",
            },
            {
                "supplier_code": code,
                "doc_type": "cancelled_cheque",
                "number": f"CHQ-{i:04d}",
                "issued_on": "2024-09-10",
                "expires_on": "",
                "verified": "true",
            },
        ]
        if msme != "none":
            docs += [
                {
                    "supplier_code": code,
                    "doc_type": "udyam",
                    "number": udyam,
                    "issued_on": "2022-03-17",
                    "expires_on": "",
                    "verified": "true",
                },
                {
                    "supplier_code": code,
                    "doc_type": "msme_agreement",
                    "number": f"AGR-{code}",
                    "issued_on": "2024-05-28",
                    "expires_on": "2027-07-31",
                    "verified": "true",
                },
            ]
        history.append(
            {
                "supplier_code": code,
                "at": "2024-09-20T10:00:00+05:30",
                "from_status": "draft",
                "to_status": "active",
                "reason": "Onboarding complete",
                "by_user": "U05",
            }
        )
    put("supplier", ",".join(sup[0]), sup)
    put("supplier_address", ",".join(addr[0]), addr)
    put("supplier_bank_account", ",".join(bank[0]), bank)
    put("supplier_category", ",".join(cat_rows[0]), cat_rows)
    put("supplier_contact", ",".join(contacts[0]), contacts)
    put("supplier_document", ",".join(docs[0]), docs)
    put("supplier_status_history", ",".join(history[0]), history)

    code, supplier, kind, valid_from, valid_to, ceiling, terms = CONTRACT
    put(
        "contract",
        "code,supplier_code,type,valid_from,valid_to,max_value,payment_terms_code,delivery_terms,freight_terms,warranty_months,sla_text,status",
        [
            {
                "code": code,
                "supplier_code": supplier,
                "type": kind,
                "valid_from": valid_from,
                "valid_to": valid_to,
                "max_value": ceiling,
                "payment_terms_code": terms,
                "delivery_terms": "for_destination",
                "freight_terms": "included",
                "warranty_months": "",
                "sla_text": "",
                "status": "active",
            }
        ],
    )
    put(
        "price_record",
        "supplier_code,item_code,contract_code,uom,price,min_qty,price_basis,freight_included,valid_from,valid_to,lead_days",
        [
            {
                "supplier_code": supplier,
                "item_code": item,
                "contract_code": code,
                "uom": uom,
                "price": price,
                "min_qty": "1",
                "price_basis": "for_destination",
                "freight_included": "true",
                "valid_from": valid_from,
                "valid_to": valid_to,
                "lead_days": lead,
            }
            for item, uom, price, lead in CONTRACT_PRICES
        ],
    )
    for name, header in EMPTY_TABLES.items():
        put(name, header, [])

    manifest = {
        "dataset": VERSION,
        "description": "One plant, eight people, twelve items, four suppliers, no history. For walking through the purchase flow.",
        "built": str(date.today()),
        "rows": counts,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return counts


# ---- checks -----------------------------------------------------------------------------------


def read(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def check(out: Path) -> list[str]:
    """What is wrong with the dataset, in plain words. Empty when it is as designed."""
    problems: list[str] = []
    m = out / "master"

    def rows(name: str) -> list[dict[str, str]]:
        return read(m / f"{name}.csv")

    if len(rows("user")) != 8:
        problems.append(f"{len(rows('user'))} people, expected 8")
    if len(rows("item")) != 12:
        problems.append(f"{len(rows('item'))} items, expected 12")
    if len(rows("supplier")) != 4:
        problems.append(f"{len(rows('supplier'))} suppliers, expected 4")
    stock = {r["item_code"]: Decimal(r["qty"]) for r in rows("stock_opening")}
    below = above = healthy = 0
    for p in rows("item_location"):
        on_hand, rop, safety = stock[p["item_code"]], Decimal(p["reorder_point"]), Decimal(p["safety_stock"])
        if on_hand <= rop:
            below += 1
            if on_hand < safety:
                problems.append(f"{p['item_code']} is below its safety stock, so it would be urgent")
        elif on_hand <= rop + 1:
            above += 1
        else:
            healthy += 1
    if (below, above, healthy) != (4, 2, 6):
        problems.append(f"stock: {below} below reorder, {above} just above, {healthy} healthy; expected 4, 2, 6")
    pan_ok = re.compile(r"^[A-Z]{5}0000[A-Z]$")
    for table_name, column in (("company", "pan"), ("supplier", "pan")):
        for r in rows(table_name):
            if not pan_ok.match(r[column]):
                problems.append(f"{table_name} {r.get('code')}: PAN {r[column]} does not carry 0000")
    for r in rows("user") + rows("supplier_contact"):
        if not r["email"].endswith(".example"):
            problems.append(f"{r.get('name')}: email {r['email']} does not end in .example")
        if not r["phone"].startswith("+91 00000 "):
            problems.append(f"{r.get('name')}: phone {r['phone']} is not a +91 00000 number")
    for r in rows("supplier") + rows("location") + rows("gst_registration"):
        g = r["gstin"]
        if len(g) != 15 or gstin(g[:2], g[2:12], g[12])[14] != g[14]:
            problems.append(f"GSTIN {g} fails its check character")
    ids = [r["code"] for r in rows("user")]
    if len(set(ids)) != len(ids):
        problems.append("two people share a code")
    for r in rows("user"):
        if r["manager_code"] and r["manager_code"] not in ids:
            problems.append(f"{r['code']}'s manager {r['manager_code']} is not a person")
    if len([r for r in rows("contract") if r["status"] == "active"]) != 1:
        problems.append("expected one active contract")
    for r in rows("supplier"):
        have = {
            d["doc_type"]
            for d in rows("supplier_document")
            if d["supplier_code"] == r["code"] and d["verified"] == "true"
        }
        need = {"pan", "cancelled_cheque", "gst_cert"} | ({"udyam"} if r["msme_category"] != "none" else set())
        if need - have:
            problems.append(f"{r['code']} lacks verified {sorted(need - have)}")
    return problems


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", default=str(DEFAULT_OUT))
    p.add_argument("--check", metavar="DATASET", help="only check an existing dataset")
    args = p.parse_args(argv)
    if args.check:
        problems = check(Path(args.check))
        for line in problems:
            print("  " + line)
        print("dataset is as designed" if not problems else f"{len(problems)} problem(s)")
        return 1 if problems else 0
    out = Path(args.out)
    counts = build(out)
    problems = check(out)
    print(f"wrote {sum(counts.values())} rows in {len(counts)} tables to {out}")
    for line in problems:
        print("  " + line)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
