"""Master-data builder: combines the content banks into the full set of master tables.

Adds what is derived rather than authored: price index per item, contracts and price records, demand truth,
per-location planning parameters (set by a "human" with noise, so they are sometimes wrong), opening stock,
monthly budgets, and copies of the reference tables.

Used by cli.py:  tables, ctx = build_master(seed)
"""
import math
import shutil
from datetime import date, timedelta
from decimal import Decimal

import yaml

import build_items
import build_suppliers
from common import (CAT, REF, START, CUTOFF, D, add_months, fy, fy_start, iso, month_start, money, qty_s, q2, read_csv, stable_hash,
                    sub_rng, write_csv)

ORG = CAT / "org"
STOCK_LOCS_ALL = ["PLT-VAD"]
WH_CATS = {"MRO", "ELE", "TLS", "PKG", "PPE"}
STOCKED_CATS = {"MRO", "ELE", "TLS", "PKG", "RAW", "CHM", "PPE"}
CAT_GL_SHARE = {  # category -> (budget GL code, share of that GL)
    "RAW": ("1410", 1.0), "PKG": ("1420", 1.0), "CHM": ("1440", 1.0), "PPE": ("5570", 1.0),
    "MRO": ("1430", 0.55), "ELE": ("1430", 0.30), "TLS": ("1430", 0.15),
}
DISCRETE_UOM = {"NOS", "PCS", "BOX", "PAC", "ROL", "SET", "PRS", "CAN", "BTL", "DRM", "BAG", "CTN", "DOZ", "BDL"}
PATTERNS = [("steady", 0.45), ("seasonal", 0.20), ("trend", 0.10), ("intermittent", 0.15), ("lumpy", 0.10)]


class PriceBook:
    """Market price per item over time (monthly index), and supplier-specific price multiplier."""

    def __init__(self, items, supplier_truth, seed):
        self.base = {i["code"]: D(i["standard_price"]) * D("0.96") for i in items}
        self.idx = {}
        self.cat = {i["code"]: i["category_code"] for i in items}
        n_months = 36
        for i in items:
            r = sub_rng(seed, "price:" + i["code"])
            idx, series = 1.0, []
            raw = i["category_code"] == "RAW"
            april_step = r.bern(0.5)
            for m in range(n_months):
                d = add_months(START, m)
                drift = 0.003 + (r.gauss(0, 0.025) if raw else r.gauss(0, 0.004))
                if d.month == 4 and april_step and not raw:
                    drift += r.uniform(0.02, 0.05)
                idx *= (1 + drift)
                series.append(idx)
            self.idx[i["code"]] = series
        self.sup_drift = {t["supplier_code"]: float(t["price_drift_pct_per_quarter"]) / 100 for t in supplier_truth}

    def market(self, item, d):
        m = max(0, min(35, (d.year - START.year) * 12 + d.month - START.month))
        return self.base[item] * D(self.idx[item][m])

    def supplier_mult(self, supplier, d):
        q = max(0.0, ((d - START).days) / 91.0)
        return D((1 + self.sup_drift.get(supplier, 0.02)) ** q)


def _load_org():
    co = yaml.safe_load(open(ORG / "company.yaml", encoding="utf-8"))
    t = {}
    c = co["company"]
    t["company"] = [dict(code=c["code"], legal_name=c["legal_name"], short_name=c["short_name"], pan=c["pan"], cin=c["cin"],
                         registered_address=c["registered_address"], turnover_band=c["turnover_band"],
                         fy_start_month=c["fy_start_month"], currency=c["currency"], email_domain=c["email_domain"],
                         letterhead_text=c["letterhead_text"])]
    t["gst_registration"] = [dict(gstin=r["gstin"], state_code=r["state_code"], legal_address=r["legal_address"],
                                  pincode=r["pincode"], is_default=str(r["is_default"]).lower()) for r in co["gst_registrations"]]
    t["location"] = [dict(code=l["code"], name=l["name"], type=l["type"], gstin=l["gstin"], address=l["address"], city=l["city"],
                          state_code=l["state_code"], pincode=l["pincode"], is_stock_location=str(l["is_stock_location"]).lower(),
                          working_days=l["working_days"], receiving_hours=l["receiving_hours"],
                          holiday_scope=";".join(l["holiday_scope"]), loc_short=l["loc_short"]) for l in co["locations"]]
    t["company_bank_account"] = [dict(r) for r in co["company_bank_accounts"]]
    for name in ["departments", "cost_centres", "gl_accounts", "projects", "users", "user_roles", "delegations", "approval_rules",
                 "settings", "tolerances", "payment_terms", "number_series", "banks"]:
        key = {"departments": "department", "cost_centres": "cost_centre", "gl_accounts": "gl_account", "projects": "project",
               "users": "user", "user_roles": "user_role", "delegations": "delegation", "approval_rules": "approval_rule",
               "settings": "setting", "tolerances": "tolerance", "payment_terms": "payment_terms", "number_series": "number_series",
               "banks": "bank"}[name]
        t[key] = read_csv(ORG / f"{name}.csv")
    return t, read_csv(ORG / "budget_plan.csv")


def _budgets(plan, projects):
    rows = []
    proj_by_cc = {}
    for p in projects:
        proj_by_cc.setdefault(p["cost_centre_code"], []).append(p)
    for r in plan:
        if r["seasonality"] == "project":
            continue
        base = D(r["annual_fy2425"])
        g = D(r["growth_pct_per_year"]) / 100
        for fyi, fy_year in enumerate([2024, 2025, 2026]):
            annual = base * (1 + g) ** fyi
            for mi in range(12):
                d = add_months(date(fy_year, 4, 1), mi)
                if r["seasonality"] == "q4_heavy":
                    w = D("1.8") if d.month in (1, 2, 3) else D("0.6")
                    amt = annual * w / D("10.8")
                else:
                    amt = annual / D(12)
                proj = ""
                if r["seasonality"] == "project":
                    cands = [p for p in proj_by_cc.get(r["cost_centre"], [])
                             if date.fromisoformat(p["start_date"]) <= d <= date.fromisoformat(p["end_date"])]
                    proj = cands[0]["code"] if cands else ""
                    if not proj:
                        continue
                rows.append(dict(cost_centre_code=r["cost_centre"], gl_code=r["gl_code"], project_code=proj,
                                 period=d.strftime("%Y-%m"), amount=money(amt), version="1", policy="warn"))
    project_gl = {"PRJ-2425-01": [("1210", 1.0)], "PRJ-2425-02": [("1230", 1.0)], "PRJ-2526-01": [("1210", 0.7), ("1220", 0.3)],
                  "PRJ-2627-01": [("1210", 1.0)], "PRJ-2627-02": [("1220", 0.6), ("1210", 0.4)]}
    for p in projects:
        vf, vt = date.fromisoformat(p["start_date"]), date.fromisoformat(p["end_date"])
        months = max(1, (vt.year - vf.year) * 12 + vt.month - vf.month + 1)
        for gl, share in project_gl.get(p["code"], [("1210", 1.0)]):
            for mi in range(months):
                d = add_months(month_start(vf), mi)
                rows.append(dict(cost_centre_code=p["cost_centre_code"], gl_code=gl, project_code=p["code"], period=d.strftime("%Y-%m"),
                                 amount=money(D(p["budget_amount"]) * D(share) / D(months)), version="1", policy="warn"))
    return rows


def _demand_and_planning(items, cats, budget_plan, seed, supplier_truth):
    """Truth demand per item-location, and the planning parameters a human would have set (with noise)."""
    gl_total = {}
    for r in budget_plan:
        gl_total[r["gl_code"]] = gl_total.get(r["gl_code"], D(0)) + D(r["annual_fy2425"])
    by_cat = {}
    for i in items:
        if i["category_code"] in STOCKED_CATS:
            by_cat.setdefault(i["category_code"], []).append(i)
    truth, itemloc, opening = [], [], []
    wt = {"A": 5.0, "B": 2.0, "C": 1.0}
    for cat, its in by_cat.items():
        gl, share = CAT_GL_SHARE[cat]
        monthly_cat = gl_total[gl] * D(share) / D(12)
        pairs = []
        for i in its:
            locs = ["PLT-VAD"]
            if cat in WH_CATS and stable_hash(i["code"] + "wh", 3) == 0:
                locs.append("WH-BLR")
            for l in locs:
                pairs.append((i, l, wt[i["abc_class"]] * (0.15 if l == "WH-BLR" else 1.0)))
        tot_w = sum(p[2] for p in pairs)
        for i, loc, w in pairs:
            r = sub_rng(seed, f"demand:{i['code']}:{loc}")
            spend = monthly_cat * D(w / tot_w)
            price_base = D(i["standard_price"]) / D(i["pack_size"])
            mean_month = float(spend / price_base)
            mean_month = max(mean_month, 0.4)
            pattern = r.weighted([p[0] for p in PATTERNS], [p[1] for p in PATTERNS])
            if mean_month < 1.5 and pattern in ("steady", "seasonal", "trend"):
                pattern = "intermittent"
            cv = {"steady": 0.25, "seasonal": 0.35, "trend": 0.30, "intermittent": 1.2, "lumpy": 0.9}[pattern]
            peak = r.randint(1, 12)
            trend = round(r.uniform(-12, 18), 1) if pattern == "trend" else 0.0
            amp = 0.35 if pattern == "seasonal" else 0.0
            truth.append(dict(item_code=i["code"], location_code=loc, pattern=pattern, mean_per_month=f"{mean_month:.3f}", cv=f"{cv:.2f}",
                              season_peak_month=peak if pattern == "seasonal" else "", seasonal_amplitude=f"{amp:.2f}",
                              trend_pct_per_year=f"{trend:.1f}", stockout_periods=""))
            lead = int(i["default_lead_days"]) + 24           # the person setting it knows approval, sourcing and supplier lateness take weeks
            daily = mean_month / 30.0
            human_err = max(0.85, r.lognorm(1.05, 0.15))      # not exact, and slightly conservative
            ss = max(1.0, daily * math.sqrt(lead) * cv * 1.65 * human_err)
            rop = daily * lead * human_err + ss
            pack = float(i["pack_size"])
            moq = float(i["moq"]) * pack if i["purchase_uom"] != i["base_uom"] else float(i["moq"])
            order_qty = max(moq, mean_month * (1.0 if i["abc_class"] == "A" else 1.5))
            maxq = rop + order_qty
            if i["base_uom"] in DISCRETE_UOM:
                rop, ss, maxq, order_qty = math.ceil(rop), math.ceil(ss), math.ceil(maxq), math.ceil(order_qty)
            policy = "rop" if i["abc_class"] == "A" else "minmax"
            itemloc.append(dict(item_code=i["code"], location_code=loc, reorder_policy=policy, reorder_point=f"{rop:.3f}",
                                safety_stock=f"{ss:.3f}", min_qty=f"{rop:.3f}", max_qty=f"{maxq:.3f}", order_qty=f"{order_qty:.3f}",
                                service_level_pct="95", lead_days_override=""))
            open_q = max(rop + order_qty * r.uniform(0.3, 1.0), 1)
            if i["base_uom"] in DISCRETE_UOM:
                open_q = float(math.ceil(open_q))
            opening.append(dict(item_code=i["code"], location_code=loc, qty=f"{open_q:.3f}",
                                unit_cost=money(price_base), as_of=START.isoformat()))
    return truth, itemloc, opening


def _contracts(items, suppliers, scat, terms, pricebook, seed):
    """Rate contracts for stocked categories, service contracts for services, and price records."""
    cats_of = {}
    for s in scat:
        cats_of.setdefault(s["supplier_code"], set()).add(s["category_code"])
    sup_by_code = {s["code"]: s for s in suppliers}
    items_by_cat = {}
    for i in items:
        items_by_cat.setdefault(i["category_code"], []).append(i)
    contracts, prices = [], []
    n = 0

    contract_cache = {}

    def contract(sup, ctype, vfrom, vto, **kw):
        nonlocal n
        ck = (sup["code"], ctype, vfrom, vto)
        if ck in contract_cache:
            return contract_cache[ck]
        n += 1
        code = f"CNT-{n:04d}"
        contracts.append(dict(code=code, supplier_code=sup["code"], type=ctype, valid_from=iso(vfrom), valid_to=iso(vto),
                              max_value=kw.get("max_value", ""), payment_terms_code=sup["payment_terms_code"],
                              delivery_terms=kw.get("delivery_terms", "for_destination"), freight_terms=kw.get("freight_terms", "included"),
                              warranty_months=kw.get("warranty", ""), sla_text=kw.get("sla", ""),
                              status="active" if vto >= CUTOFF else "expired"))
        contract_cache[ck] = code
        return code

    def price_rec(sup, item, ccode, vfrom, vto, disc, qty_break=True):
        base = pricebook.market(item["code"], vfrom) * pricebook.supplier_mult(sup["code"], vfrom) * D(sup["price_level"]) * D(disc)
        price = q2(base)
        lead = max(1, int(round(int(item["default_lead_days"]) * float(build_suppliers.PROFILE[sup["profile"]]["lead_mult"]))))
        prices.append(dict(supplier_code=sup["code"], item_code=item["code"], contract_code=ccode, uom=item["purchase_uom"], price=money(price),
                           min_qty="1", price_basis="for_destination", freight_included="true", valid_from=iso(vfrom), valid_to=iso(vto),
                           lead_days=lead))
        if qty_break and item["kind"] == "goods" and item["abc_class"] in ("A", "B"):
            mq = max(5, int(float(item["moq"]) * 5))
            prices.append(dict(supplier_code=sup["code"], item_code=item["code"], contract_code=ccode, uom=item["purchase_uom"],
                               price=money(price * D("0.97")), min_qty=str(mq), price_basis="for_destination", freight_included="true",
                               valid_from=iso(vfrom), valid_to=iso(vto), lead_days=lead))

    yearly = [(date(2024, 4, 1), date(2025, 3, 31)), (date(2025, 4, 1), date(2026, 3, 31)), (date(2026, 4, 1), date(2027, 3, 31))]
    for cat in ["RAW", "PKG", "MRO", "ELE", "TLS", "CHM", "PPE", "OFC"]:
        sups = [s for s in suppliers if cat in cats_of[s["code"]] and s["status"] == "active"]
        r = sub_rng(seed, "contracts:" + cat)
        r.shuffle(sups)
        chosen = sups[: (2 if cat in ("RAW", "PKG") else 1) if len(sups) > 1 else 1]
        its = items_by_cat[cat]
        for sup in chosen:
            covered = [i for i in its if r.bern(0.55)]
            # one contract chain per supplier: yearly renewals with price revision, one starts mid-year
            chain = yearly if r.bern(0.7) else [(date(2024, 7, 1), date(2025, 6, 30)), (date(2025, 7, 1), date(2026, 6, 30)),
                                                (date(2026, 7, 1), date(2027, 6, 30))]
            for vf, vt in chain:
                code = contract(sup, "rate_contract", vf, vt, max_value="20000000.00" if cat == "RAW" else "5000000.00")
                for it in covered:
                    price_rec(sup, it, code, vf, vt, "0.96")
    # approved price records without a contract for a handful of other items (not those that already have contract prices)
    contract_items = {p["item_code"] for p in prices}
    for cat in ["ITH", "FUR", "SWS"]:
        sups = [s for s in suppliers if cat in cats_of[s["code"]]]
        r = sub_rng(seed, "pr:" + cat)
        for it in items_by_cat[cat]:
            if it["code"] in contract_items or "billing" in it["attributes"]:
                continue
            if r.bern(0.35):
                sup = r.choice(sups)
                price_rec(sup, it, "", date(2025, 4, 1), date(2026, 3, 31), "0.98", qty_break=False)
                price_rec(sup, it, "", date(2026, 4, 1), date(2027, 3, 31), "0.98", qty_break=False)
    # service contracts (monthly or annual)
    svc = {"FAC": "service_contract", "AMC": "annual_maintenance", "PRO": "service_contract", "LOG": "rate_contract"}
    for cat, ctype in svc.items():
        sups = [s for s in suppliers if cat in cats_of[s["code"]]]
        r = sub_rng(seed, "svc:" + cat)
        for it in items_by_cat[cat]:
            if cat == "PRO" and '"billing":"monthly"' not in it["attributes"] and '"billing":"annual"' not in it["attributes"]:
                continue
            if cat == "LOG" and not r.bern(0.8):
                continue
            if cat == "FAC":
                chosen_sups = []
                for st in ("27", "24", "29"):
                    in_state = [s for s in sups if s["state_code"] == st and s["status"] == "active"]
                    if in_state:
                        chosen_sups.append(r.choice(in_state))
            else:
                chosen_sups = [r.choice(sups)]
            for sup in chosen_sups:
                for vf, vt in yearly:
                    code = contract(sup, ctype, vf, vt, sla="Monthly service levels as agreed" if cat == "FAC" else "")
                    price_rec(sup, it, code, vf, vt, "1.0", qty_break=False)
    # monthly software and hosting subscriptions
    swsups = [s for s in suppliers if "SWS" in cats_of[s["code"]]]
    r = sub_rng(seed, "swsvc")
    for it in items_by_cat["SWS"]:
        if '"billing":"monthly"' in it["attributes"] or '"term":"12m"' in it["attributes"]:
            sup = r.choice(swsups)
            for vf, vt in yearly:
                code = contract(sup, "service_contract", vf, vt)
                price_rec(sup, it, code, vf, vt, "1.0", qty_break=False)
    return contracts, prices


def _holidays():
    rows = []
    for f in ["holidays_central_2024_2025.csv", "holidays_2026_central_gazetted.csv"]:
        for r in read_csv(REF / f):
            rows.append(dict(date=r["date"], name=r["name"], scope="central", type=r.get("type", "closed")))
    for r in read_csv(REF / "holidays_state_MH_GJ_KA_2024_2026.csv"):
        rows.append(dict(date=r["date"], name=r["name"], scope=r["state_code"], type="closed"))
    return sorted(rows, key=lambda x: (x["date"], x["scope"]))


def build_master(seed=42):
    t, budget_plan = _load_org()
    cat_rows = read_csv(CAT / "items" / "categories.csv")
    t["category"] = [dict(code=c["code"], name=c["name"], parent_code="", kind=c["kind"], default_gl=c["default_gl"], stocked=c["stocked"],
                          capex=c["capex"], min_quotes=c["min_quotes"], buyer_user=c["buyer_user"], approval_category=c["approval_category"],
                          required_attributes=c["required_attributes"], required_supplier_docs=c["required_supplier_docs"],
                          inspection_required=c["inspection_required"], default_tds_section=c["default_tds_section"],
                          reverse_charge_default=c["reverse_charge_default"]) for c in cat_rows]
    it_tables, rate_changes, errs = build_items.build()
    if errs:
        raise SystemExit("item build problems: " + "; ".join(errs))
    t.update(it_tables)
    sup_tables = build_suppliers.build()
    t.update(sup_tables)
    items, suppliers = t["item"], t["supplier"]
    pricebook = PriceBook(items, t["truth_supplier"], seed)
    t["budget"] = _budgets(budget_plan, t["project"])
    truth, itemloc, opening = _demand_and_planning(items, cat_rows, budget_plan, seed, t["truth_supplier"])
    t["truth_item_demand"], t["item_location"], t["stock_opening"] = truth, itemloc, opening
    t["contract"], t["price_record"] = _contracts(items, suppliers, t["supplier_category"], t["payment_terms"], pricebook, seed)
    t["holiday"] = _holidays()
    t["sanction_entry"] = read_csv(CAT / "suppliers" / "sanction_entries.csv")
    # clean supplier internal columns out of the public supplier table but keep them in the truth table
    for s in t["supplier"]:
        t.setdefault("_supplier_internal", []).append(dict(code=s["code"], profile=s["profile"], style=s["style"], hold_out=s["hold_out"],
                                                         price_level=s["price_level"]))
    ctx = dict(pricebook=pricebook, seed=seed)
    return t, ctx


def copy_reference(out):
    ref = out / "reference"
    ref.mkdir(parents=True, exist_ok=True)
    for src, dst in [("gst_state_codes.csv", "gst_state.csv"), ("gst_uqc_units.csv", "uqc_unit.csv"),
                     ("gst_goods_rates_2025-09-22.csv", "gst_rate_goods.csv"), ("gst_services_rates.csv", "gst_rate_services.csv"),
                     ("gst_rate_history_catalogue.csv", "gst_rate_history.csv"), ("tds_sections.csv", "tds_section.csv")]:
        s = REF / src
        if s.exists():
            shutil.copyfile(s, ref / dst)
    rbi = [("2024-11-01", "6.75"), ("2025-02-07", "6.50"), ("2025-04-09", "6.25"), ("2025-06-06", "5.75"), ("2025-12-05", "5.50"),
           ("2026-10-07", "5.75")]
    write_csv(ref / "rbi_bank_rate.csv", [dict(rate_pct=r, effective_from=d, source="rbi.org.in via research; last row to verify") for d, r in rbi])
    comp = [("e_invoice_turnover_limit_cr", "5", "2023-08-01"), ("e_invoice_report_within_days_if_turnover_cr_10_plus", "30", "2025-04-01"),
            ("e_way_bill_value_limit_inr", "50000", "2018-04-01"), ("hsn_digits_turnover_up_to_5cr", "4", "2021-04-01"),
            ("hsn_digits_turnover_above_5cr", "6", "2021-04-01"), ("invoice_number_max_length", "16", "2017-07-01"),
            ("msme_days_with_agreement_max", "45", "2023-04-01"), ("msme_days_without_agreement", "15", "2023-04-01"),
            ("msme_interest_multiple_of_bank_rate", "3", "2006-10-02"), ("tds_goods_threshold_inr", "5000000", "2021-07-01")]
    write_csv(ref / "compliance_parameter.csv", [dict(key=k, value=v, effective_from=d, source="tax_rules_facts.md") for k, v, d in comp])
