"""The master folder every demo company has: the file set and headers, writing and reading the files, the supplier
tables, and the checks every company must pass (safe identifiers, valid GSTINs, complete supplier documents).

Used by company_profile.py (companies described by a profile file).
"""

import csv
import re
import shutil
import sys
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from ids import is_valid_gstin, make_gstin  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
COMMON = REPO / "datagen" / "catalog" / "common"  # plain configuration every company shares
COMMON_TABLES = ("gl_account", "bank", "payment_terms", "number_series")

# every file in master/, in the order the companies write them, with its header
HEADERS = {
    "gl_account": "code,name,type,is_capex,is_tax_account,parent_code",
    "bank": "code,name,ifsc_prefix",
    "payment_terms": "code,name,days,baseline,advance_pct,discount_pct,discount_days",
    "number_series": "document_type,prefix,scope,format,reset",
    "company": "code,legal_name,short_name,pan,cin,registered_address,turnover_band,fy_start_month,currency,email_domain,letterhead_text",
    "region": "code,name",
    "location": "code,name,type,gstin,address,city,state_code,pincode,is_stock_location,working_days,receiving_hours,holiday_scope,loc_short,head_user,region_code",
    "department": "code,name,head_user",
    "cost_centre": "code,name,department_code,location_code,owner_user,active",
    "user": "code,employee_no,name,designation,email,phone,department_code,location_code,manager_code,approval_limit,notify_by,active",
    "user_role": "user_code,role,scope_location,scope_department",
    "approval_rule": "rule_code,document,min_value,max_value,category_code,department_code,spend_type,approver_chain,version,effective_from,effective_to,location_code",
    "setting": "key,value,description",
    "tolerance_version": "version,effective_from,note",
    "tolerance": "key,value,category_code,note,version,effective_from",
    "price_threshold_rule": "category_code,version,effective_from,effective_to,gap_pct,gap_inr_per_unit,min_saving_inr,evidence_max_age_days,min_sources,expiry_review_days,note",
    "company_bank_account": "code,bank_code,account_no,ifsc,purpose,payment_file_format",
    "gst_registration": "gstin,state_code,legal_address,pincode,is_default",
    "budget": "cost_centre_code,gl_code,project_code,period,amount,version,policy,location_code",
    "budget_period_lock": "period,locked_by,locked_at,reason",
    "forward_buy_rule": "category_code,item_code,location_code,version,effective_from,effective_to,enabled,min_gap_pct,min_trend_months,max_cover_months,holding_cost_pct_month,min_net_saving_inr,shelf_life_months,storage_max_qty,note",
    "category": "code,name,parent_code,kind,default_gl,stocked,capex,min_quotes,buyer_user,approval_category,required_attributes,required_supplier_docs,inspection_required,default_tds_section,reverse_charge_default",
    "item": "code,name,description,category_code,kind,default_gl,brand,base_uom,purchase_uom,pack_size,moq,hsn_sac,gst_rate_before,gst_rate_after,gst_rate_alt_before,gst_rate_alt_after,gst_rate_change_date,gst_rate_confidence,gst_reverse_charge,gst_note,standard_price,abc_class,stocked,capex,inspection_required,serialised,hazardous,default_lead_days,attributes,active",
    "item_location": "item_code,location_code,reorder_policy,reorder_point,safety_stock,min_qty,max_qty,order_qty,service_level_pct,lead_days_override",
    "stock_opening": "item_code,location_code,qty,unit_cost,as_of",
    "supplier": "code,legal_name,trade_name,legal_form,pan,pan_status,gst_reg_type,gstin,state_code,e_invoice_applicable,hsn_digits,msme_category,udyam_no,msme_agreement,agreed_payment_days,payment_terms_code,tds_section_default,lower_tds_rate,lower_tds_valid_to,lower_tds_limit,reverse_charge,status,turnover_band,registered_by",
    "supplier_address": "supplier_code,type,gstin,address,city,state_code,pincode",
    "supplier_bank_account": "supplier_code,bank_code,account_no,ifsc,verified,verified_on",
    "supplier_category": "supplier_code,category_code,approved_from",
    "supplier_category_scope": "supplier_code,category_code,location_code,region_code",
    "supplier_contact": "supplier_code,name,role,email,phone,is_primary",
    "supplier_document": "supplier_code,doc_type,number,issued_on,expires_on,verified",
    "supplier_status_history": "supplier_code,at,from_status,to_status,reason,by_user",
    "supplier_flag": "supplier_code,kind,other_ref,detail,raised_at",
    "contract": "code,supplier_code,type,valid_from,valid_to,max_value,payment_terms_code,delivery_terms,freight_terms,warranty_months,sla_text,status",
    "price_record": "supplier_code,item_code,contract_code,uom,price,min_qty,price_basis,freight_included,valid_from,valid_to,lead_days,version,status,location_code,region_code",
    "market_benchmark": "item_code,price,uom,basis,effective_date,region_code,source_kind,source_name,source_ref,note,status,entered_by,entered_at,verified_by,verified_at,verify_note,rejected_reason",
    "delegation": "from_user,to_user,start_date,end_date,reason",
    "item_substitute": "item_code,substitute_code,note",
    "uom_conversion": "item_code,from_uom,to_uom,factor",
    "project": "code,name,type,cost_centre_code,budget_amount,start_date,end_date,status",
    "sanction_entry": "name,country,list_name,listed_on,aliases",
}
EMPTY_TABLES = ("delegation", "item_substitute", "uom_conversion", "project", "sanction_entry")
PAN_OK = re.compile(r"^[A-Z]{5}0000[A-Z]$")


def gstin(state: str, pan: str) -> str:
    """The first registration of a PAN in a state."""
    return make_gstin(state, pan, 1)


def tolerance_on(
    rows: list[dict[str, str]], versions: list[dict[str, str]], day: str, category: str | None = None
) -> tuple[int, dict[str, Decimal]]:
    """The tolerance version in force on a day ('YYYY-MM-DD') and its values: the 'ANY' rows, overridden key by key by
    the category's own rows of the same version."""
    started = [v for v in versions if v["effective_from"] <= day]
    version = max(started, key=lambda v: (v["effective_from"], int(v["version"])))["version"]
    values: dict[str, Decimal] = {}
    for scope in ("ANY", category):
        for r in rows:
            if r["version"] == version and r["category_code"] == scope:
                values[r["key"]] = Decimal(r["value"])
    return int(version), values


def write_csv(path: Path, header: list[str], rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=header, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def write_table(out: Path, name: str, rows: list[dict[str, object]]) -> int:
    """One master file with its fixed header. Returns the number of rows."""
    write_csv(out / "master" / f"{name}.csv", HEADERS[name].split(","), rows)
    return len(rows)


def read(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def copy_common(out: Path) -> dict[str, int]:
    """The chart of accounts, banks, payment terms and number series, as they are in the catalogue."""
    (out / "master").mkdir(parents=True, exist_ok=True)
    counts = {}
    for name in COMMON_TABLES:
        shutil.copyfile(COMMON / f"{name}.csv", out / "master" / f"{name}.csv")
        counts[name] = len(read(COMMON / f"{name}.csv"))
    return counts


def months(first: str, last: str) -> list[str]:
    """Every month from first to last ('YYYY-MM'), both included."""
    y, m = int(first[:4]), int(first[5:])
    out = []
    while f"{y}-{m:02d}" <= last:
        out.append(f"{y}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


@dataclass(frozen=True)
class Supplier:
    code: str
    name: str
    form: str  # legal form
    pan: str
    state: str
    e_invoice: bool
    hsn_digits: str
    msme: str  # micro, small, medium or none
    udyam: str
    agreed_days: int | None  # days agreed in writing with an MSME supplier
    terms: str  # payment terms code
    turnover: str
    bank: str  # bank code
    contact: str
    domain: str  # the supplier's email domain, before .example
    categories: tuple[str, ...]
    city: str
    pincode: str
    tds_section: str = "194Q"
    onboarded_by: str = ""  # the buyer who activated it
    status: str = (
        "active"  # active, blocked (was active, then blocked) or prospect (registered to quote, not onboarded)
    )
    registered_by: str = ""  # a prospect: who registered it
    registered_on: str = ""
    blocked_on: str = ""  # a blocked supplier: when, why and who approved the block
    blocked_reason: str = ""
    blocked_by: str = ""
    address: str = ""  # the registered address line, when not the made-up one
    phone: str = ""  # the contact's phone, when not the made-up one

    @property
    def prospect(self) -> bool:
        return self.status == "prospect"


def supplier_tables(suppliers: list[Supplier]) -> dict[str, list[dict[str, object]]]:
    """The supplier and its addresses, bank account, categories, contact, documents and status history, for each one."""
    t: dict[str, list[dict[str, object]]] = {
        name: []
        for name in (
            "supplier",
            "supplier_address",
            "supplier_bank_account",
            "supplier_category",
            "supplier_contact",
            "supplier_document",
            "supplier_status_history",
        )
    }
    for i, s in enumerate(suppliers, start=1):
        g = gstin(s.state, s.pan)
        t["supplier"].append(
            {
                "code": s.code,
                "legal_name": s.name,
                "trade_name": s.name,
                "legal_form": s.form,
                "pan": s.pan,
                "pan_status": "operative",
                "gst_reg_type": "regular",
                "gstin": g,
                "state_code": s.state,
                "e_invoice_applicable": str(s.e_invoice).lower(),
                "hsn_digits": s.hsn_digits,
                "msme_category": s.msme,
                "udyam_no": s.udyam,
                "msme_agreement": str(s.agreed_days is not None).lower(),
                "agreed_payment_days": "" if s.agreed_days is None else s.agreed_days,
                "payment_terms_code": s.terms,
                "tds_section_default": s.tds_section,
                "lower_tds_rate": "",
                "lower_tds_valid_to": "",
                "lower_tds_limit": "",
                "reverse_charge": "no",
                "status": s.status,
                "turnover_band": s.turnover,
                "registered_by": s.registered_by,
            }
        )
        t["supplier_address"].append(
            {
                "supplier_code": s.code,
                "type": "registered",
                "gstin": g,
                "address": s.address or f"{10 + i}, Industrial Area",
                "city": s.city,
                "state_code": s.state,
                "pincode": s.pincode,
            }
        )
        if not s.prospect:  # a prospect quotes before it gives its bank details (D-03 R6)
            t["supplier_bank_account"].append(
                {
                    "supplier_code": s.code,
                    "bank_code": s.bank,
                    "account_no": f"00010000{i:04d}",
                    "ifsc": f"{s.bank[:4].ljust(4, 'X')}0900{i:03d}",
                    "verified": "true",
                    "verified_on": "2024-09-15",
                }
            )
        t["supplier_category"] += [  # for a prospect: the category it asked to quote for
            {"supplier_code": s.code, "category_code": c, "approved_from": s.registered_on or "2024-04-01"}
            for c in s.categories
        ]
        t["supplier_contact"].append(
            {
                "supplier_code": s.code,
                "name": s.contact,
                "role": "sales",
                "email": f"sales@{s.domain}.example",
                "phone": s.phone or f"+91 00000 9{i:04d}",
                "is_primary": "true",
            }
        )
        docs = [
            ("gst_cert", g, "2019-10-07", ""),
            ("pan", s.pan, "2015-04-01", ""),
        ]
        if not s.prospect:
            docs.append(("cancelled_cheque", f"CHQ-{i:04d}", "2024-09-10", ""))
        if s.msme != "none":
            docs += [
                ("udyam", s.udyam, "2022-03-17", ""),
                ("msme_agreement", f"AGR-{s.code}", "2024-05-28", "2027-07-31"),
            ]
        t["supplier_document"] += [
            {
                "supplier_code": s.code,
                "doc_type": kind,
                "number": number,
                "issued_on": issued,
                "expires_on": expires,
                "verified": str(not s.prospect).lower(),  # a prospect's papers are uploaded, not yet checked
            }
            for kind, number, issued, expires in docs
        ]
        if s.prospect:
            t["supplier_status_history"].append(
                {
                    "supplier_code": s.code,
                    "at": f"{s.registered_on}T10:30:00+05:30",
                    "from_status": "",
                    "to_status": "prospect",
                    "reason": "Registered as a prospect to quote",
                    "by_user": s.registered_by,
                }
            )
            continue
        t["supplier_status_history"].append(
            {
                "supplier_code": s.code,
                "at": "2024-09-20T10:00:00+05:30",
                "from_status": "draft",
                "to_status": "active",
                "reason": "Onboarding complete",
                "by_user": s.onboarded_by,
            }
        )
        if s.status == "blocked":
            t["supplier_status_history"].append(
                {
                    "supplier_code": s.code,
                    "at": f"{s.blocked_on}T16:00:00+05:30",
                    "from_status": "active",
                    "to_status": "blocked",
                    "reason": s.blocked_reason,
                    "by_user": s.blocked_by,
                }
            )
    return t


def common_problems(out: Path) -> list[str]:
    """What breaks the rules every company follows: the file set and headers, PANs with 0000, .example emails,
    +91 00000 phones, valid GSTINs, people and managers that exist, complete and verified supplier documents."""
    m = out / "master"
    problems: list[str] = []
    present = {p.stem for p in m.glob("*.csv")}
    if present != set(HEADERS):
        problems.append(f"master files differ from the standard set: extra {sorted(present - set(HEADERS))}, "
                        f"missing {sorted(set(HEADERS) - present)}")  # fmt: skip
    for name in sorted(present & set(HEADERS)):
        with (m / f"{name}.csv").open(encoding="utf-8", newline="") as f:
            header = f.readline().strip()
        if header != HEADERS[name]:
            problems.append(f"{name}.csv header differs from the standard one")
    if problems:
        return problems

    def rows(name: str) -> list[dict[str, str]]:
        return read(m / f"{name}.csv")

    for table_name in ("company", "supplier"):
        for r in rows(table_name):
            if not PAN_OK.match(r["pan"]):
                problems.append(f"{table_name} {r.get('code')}: PAN {r['pan']} does not carry 0000")
    for r in rows("user") + rows("supplier_contact"):
        if not r["email"].endswith(".example"):
            problems.append(f"{r.get('name')}: email {r['email']} does not end in .example")
        if not r["phone"].startswith("+91 00000 "):
            problems.append(f"{r.get('name')}: phone {r['phone']} is not a +91 00000 number")
    for r in rows("supplier") + rows("location") + rows("gst_registration") + rows("supplier_address"):
        if not is_valid_gstin(r["gstin"]):
            problems.append(f"GSTIN {r['gstin']} fails its check character")
    ids = [r["code"] for r in rows("user")]
    if len(set(ids)) != len(ids):
        problems.append("two people share a code")
    for r in rows("user"):
        if r["manager_code"] and r["manager_code"] not in ids:
            problems.append(f"{r['code']}'s manager {r['manager_code']} is not a person")
    docs = rows("supplier_document")
    for r in rows("supplier"):
        if r["status"] != "active":
            continue  # a prospect's papers wait for the identity check; a blocked supplier is not used
        have = {d["doc_type"] for d in docs if d["supplier_code"] == r["code"] and d["verified"] == "true"}
        need = {"pan", "cancelled_cheque", "gst_cert"} | ({"udyam"} if r["msme_category"] != "none" else set())
        if need - have:
            problems.append(f"{r['code']} lacks verified {sorted(need - have)}")
    return problems
