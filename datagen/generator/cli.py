"""Generate a dataset.

  python -I datagen/generator/cli.py --size demo --seed 42 [--out DIR]
Sizes: small (last 3 months of history, for fast tests) and demo (24 months, 1 Nov 2024 to 31 Oct 2026).
"""
import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import CUTOFF, START, REPO, write_csv  # noqa: E402
from master import build_master, copy_reference  # noqa: E402
from sim import Sim  # noqa: E402
from finance import finalize, renumber  # noqa: E402

MASTER = ["company", "company_bank_account", "gst_registration", "location", "department", "cost_centre", "gl_account", "project", "number_series",
          "user", "user_role", "delegation", "approval_rule", "budget", "setting", "tolerance", "payment_terms", "category", "item", "uom_conversion",
          "item_substitute", "supplier", "supplier_address", "supplier_contact", "supplier_bank_account", "supplier_category", "supplier_document",
          "supplier_status_history", "contract", "price_record", "stock_opening", "item_location", "bank", "holiday", "sanction_entry"]
HISTORY = ["request", "request_line", "approval_event", "rfq", "rfq_line", "rfq_supplier", "quote", "quote_line", "award", "po", "po_line", "po_event",
           "goods_receipt", "gr_line", "service_receipt", "stock_movement", "invoice", "invoice_line", "tds_entry", "payment_run", "payment",
           "payment_allocation", "bank_statement_line", "email_message", "po_schedule", "po_charge", "invoice_charge",
           "return_to_vendor", "advance_adjustment", "tds_challan"]
TRUTH = ["truth_item_demand", "truth_supplier", "truth_case", "truth_match_expected"]
SUPPLIER_INTERNAL = ["profile", "style", "hold_out", "price_level", "turnover_cr"]


def generate(seed=42, size="demo", out=None, verbose=True):
    start = START if size == "demo" else date(2026, 8, 1)
    t, ctx = build_master(seed)
    sim = Sim(t, ctx, seed=seed, start=start, end=CUTOFF)
    sim.run()
    tables = finalize(sim)
    tables["truth_item_demand"] = t["truth_item_demand"]
    tables["truth_supplier"] = t["truth_supplier"]
    from datetime import datetime
    sup_codes = {s["trade_name"]: s["code"] for s in t["supplier"]}
    for name, ctype, cause, expected in [
            ("Zorin Trading Company", "sanctions_match", "Prospective supplier name matches 'Zorin Trading Company Limited' on the sanctions list",
             "Screening must flag a potential match; activation blocked until a person clears it"),
            ("Apex Chemicals Trading", "onboarding_incomplete", "Prospective supplier has no cancelled cheque, no chemical licence and an unverified bank account",
             "Cannot be activated until the required documents are provided"),
            ("Lotus Industrial Fasteners", "onboarding_clean", "Prospective supplier with complete documents and no sanctions match",
             "Can be activated after the normal approval")]:
        tables["truth_case"].append(dict(case_id=f"CASE-SUP-{sup_codes[name]}", type=ctype, documents=sup_codes[name], cause=cause, expected_result=expected,
                                         planted_on="2026-09-21", _t=datetime(2026, 9, 21, 10, 0)))
    mapping = renumber(tables)
    for n, r in enumerate(sorted(tables["stock_movement"], key=lambda r: (r["date"], r["item_code"])), 1):
        r["id"] = f"SM-{n:07d}"
    for tn in ("request", "request_line"):
        pass
    for k in ("request_line", "po_line"):
        for r in tables[k]:
            for key in [x for x in r if x.startswith("_")]:
                r.pop(key)
    sup_internal = {s["code"]: {k: s[k] for k in SUPPLIER_INTERNAL} for s in t["supplier"]}
    for s in t["supplier"]:
        for k in SUPPLIER_INTERNAL:
            s.pop(k, None)
        tcr = float(sup_internal[s["code"]]["turnover_cr"])
        s["turnover_band"] = "<1.5cr" if tcr < 1.5 else "1.5-5cr" if tcr < 5 else "5-20cr" if tcr < 20 else "20-100cr" if tcr < 100 else "100cr+"
    if out is None:
        out = REPO / "data" / "datasets" / f"cbc-{size}-v1-seed{seed}"
    out = Path(out)
    for name in MASTER:
        rows = tables.get(name) or t.get(name, [])
        write_csv(out / "master" / f"{name}.csv", rows)
    # supplier statuses at the end of history come from the status history; keep the table as built
    for name in HISTORY:
        rows = sorted(tables.get(name, []), key=lambda r: r.get("_t") or 0) if name not in ("stock_movement",) else tables[name]
        rows = [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows]
        write_csv(out / "history" / f"{name}.csv", rows)
    for name in TRUTH:
        rows = tables.get(name) or t.get(name, [])
        rows = [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows]
        write_csv(out / "truth" / f"{name}.csv", rows)
    copy_reference(out)
    splits = {"hold_out_suppliers": sorted(c for c, v in sup_internal.items() if v["hold_out"] == "true"),
              "note": "Document layouts are held out when documents are generated (2 per document type)."}
    manifest = dict(company="CBC Private Limited (fictional)", size=size, seed=seed, generator_version="0.1", history_start=start.isoformat(),
                    history_end=CUTOFF.isoformat(), note="Every generated PAN carries the digits 0000 and can never belong to a real taxpayer. "
                    "Documents are not yet generated.", row_counts={n: len(tables.get(n) or t.get(n, [])) for n in MASTER + HISTORY + TRUTH})
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (out / "splits.json").write_text(json.dumps(splits, indent=2), encoding="utf-8")
    if verbose:
        print("written to", out)
        for n in HISTORY + TRUTH:
            print(f"  {n:24s} {len(tables.get(n) or t.get(n, [])):7d}")
    return out, tables, t


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--size", default="demo", choices=["small", "demo"])
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out")
    a = ap.parse_args()
    generate(a.seed, a.size, a.out)
