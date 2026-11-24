"""What a later masters import may do to each table (task 0.6, DF-16, D-22): the key a file row is matched by, and what
happens to a row that is new or differs.

Kinds: U update (changed columns applied, protected ones stay with a warning), K keep (an existing row never changes,
new keys are added), C control (after the first import the file neither adds nor changes rows: another administrator
changes them on the screens), A add-only (rows not already there are added). Rows of the company the file leaves out
are always kept.
"""

from dataclasses import dataclass

from p2p.masters.schema import Table

# keys of the tables without a natural primary key; an add-only table is keyed by all its columns (None)
NATURAL_KEYS: dict[str, tuple[str, ...] | None] = {
    "org_user_role": None,
    "delegation": None,
    "supplier_category_scope": None,
    "supplier_status_history": None,
    "supplier_flag": None,
    "budget": ("cost_centre_code", "location_code", "gl_code", "project_code", "period", "version"),
    "number_series": ("document_type", "scope"),
    "tolerance": ("key", "category_code", "version"),
    "approval_rule": ("rule_code", "version"),
    "supplier_address": ("supplier_code", "type"),
    "supplier_bank_account": ("supplier_code", "account_no", "ifsc"),
    "supplier_contact": ("supplier_code", "name"),
    "supplier_document": ("supplier_code", "doc_type", "number"),
    "price_record": (
        "supplier_code",
        "item_code",
        "contract_code",
        "uom",
        "min_qty",
        "location_code",
        "region_code",
        "version",
    ),
    "sanction_entry": ("name", "country", "list_name"),
    "price_threshold_rule": ("category_code", "version"),
    "market_benchmark": ("item_code", "region_code", "source_name", "effective_date"),
    "forward_buy_rule": ("category_code", "item_code", "location_code", "version"),
}


def key_columns(spec: Table) -> tuple[str, ...]:
    return spec.pk or NATURAL_KEYS.get(spec.name) or tuple(c for c, _ in spec.columns)


@dataclass(frozen=True)
class Policy:
    kind: str  # U, K, C or A
    protected: tuple[str, ...] = ()
    why: str = ""  # the end of the warning for a protected column (U) or a kept row (K)
    noun: str = ""  # C: what the rows are and where they change
    where: str = ""


SETUP = "another administrator approves"
APPROVERS = f"who approves is changed on the setup screen ({SETUP})"

POLICIES: dict[str, Policy] = {
    "company": Policy("K", why="kept as it is; an import does not change the company record"),
    "department": Policy("U", ("head_user",), APPROVERS),
    "location": Policy("U", ("head_user",), APPROVERS),
    "item_category": Policy(
        "U",
        ("approval_category", "min_quotes", "required_supplier_docs", "inspection_required"),
        f"category rules are changed on the setup screen ({SETUP})",
    ),
    "org_user": Policy(
        "U",
        ("email", "department_code", "location_code", "manager_code", "approval_limit", "active"),
        f"who a person is for approvals is changed on the setup screen ({SETUP})",
    ),
    "cost_centre": Policy("U", ("owner_user",), "change it on the setup screen"),
    "project": Policy("U", ("budget_amount",), "a budget changes by a new budget version"),
    "budget": Policy("K", why="kept as it is; a budget version does not change: add the next version"),
    "company_bank_account": Policy("K", why="kept as it is; an import does not change the company's bank accounts"),
    "stock_opening": Policy("K", why="kept as it is; opening stock is loaded once: correct stock with a stock count"),
    "org_setting": Policy("C", noun="policies", where="the setup screen"),
    "approval_rule": Policy("C", noun="approval rules", where="the setup screen"),
    "tolerance": Policy("C", noun="tolerances", where="the setup screen"),
    "tolerance_version": Policy("C", noun="tolerances", where="the setup screen"),
    "price_threshold_rule": Policy("C", noun="price thresholds", where="the buying rules screen"),
    "number_series": Policy("C", noun="number series", where="the setup screen"),
    "org_user_role": Policy("C", noun="roles", where="the setup screen"),
    "delegation": Policy("C", noun="deputies", where="the Deputies page"),
    "supplier": Policy("U", ("status", "lower_tds_rate", "lower_tds_valid_to", "lower_tds_limit")),
    "supplier_bank_account": Policy("K"),
    "supplier_category": Policy("K", why="kept as it is; change the approval on the supplier page"),
    "supplier_category_scope": Policy("A"),
    "supplier_flag": Policy("A"),
    "supplier_status_history": Policy("A"),
    "supplier_document": Policy(
        "K", why="kept as it is; a document on file does not change: add the renewed document with its new number"
    ),
    "contract": Policy("K", why="kept as it is; a contract changes on its contract page"),
    "price_record": Policy("K", why="kept as it is; a price version does not change: add the next version"),
    "market_benchmark": Policy("K", why="kept as it is; a benchmark does not change: add a new one"),
    "budget_period_lock": Policy("C", noun="locked budget months", where="the budgets panel (Finance)"),
    "forward_buy_rule": Policy("C", noun="forward-buy rules", where="the buying rules screen"),
    "non_po_rule": Policy("C", noun="bill-without-order rules", where="the buying rules screen"),
}
DEFAULT = Policy("U")  # bank, region, payment terms, GL accounts, GST registrations, items, item details, sanctions

# a supplier's identity columns stay while it is in use (as on the supplier screen, suppliers.FROZEN_WHEN_IN_USE)
SUPPLIER_FROZEN = ("pan", "gstin", "state_code", "gst_reg_type", "msme_category", "udyam_no")
LOWER_TDS = ("lower_tds_rate", "lower_tds_valid_to", "lower_tds_limit")


def policy(table: str) -> Policy:
    return POLICIES.get(table, DEFAULT)


def shown(value: object) -> str:
    """A value in a warning or a key: empty shows as '-'."""
    if value is None:
        return "-"
    if hasattr(value, "isoformat"):
        return str(value.isoformat())
    return str(value)


def protected_reason(table: str, column: str, old: object, status: str | None = None) -> str:
    if table == "supplier":
        if column == "status":
            return f"status stays {shown(old)}; an import does not change it"
        if column in LOWER_TDS:
            return f"{column} stays {shown(old)}; a lower deduction certificate is entered on the supplier page"
        return (
            f"{column} stays {shown(old)} while the supplier is {status}; put it under review to change it "
            "(another person approves)"
        )
    return f"{column} stays {shown(old)}; {policy(table).why}"


def control_reason(table: str, new: bool) -> str:
    p = policy(table)
    return (
        f"{'not added' if new else 'not changed'}: {p.noun} are changed on {p.where} after the first import ({SETUP})"
    )


# warnings of the rows that arrive in the state a second person moves on (2P)
DRAFT_SUPPLIER = "added as a draft; a second person activates it"
UNVERIFIED_BANK = "bank account added unverified; a second person verifies it before any payment"
BANK_KEPT = "bank details kept; change them through a bank change"
UNVERIFIED_DOCUMENT = "added unverified; verify it on the supplier page"
DRAFT_CONTRACT = "added as a draft; a second person activates it on the contract page"
DRAFT_PRICE = "added as a draft version; a second person activates it on the contract page"
UNVERIFIED_BENCHMARK = "added unverified; verify it on the benchmarks page"
HISTORY_NOT_ADDED = "not added: the status history of an existing supplier is written by its status changes"
