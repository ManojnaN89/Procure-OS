"""Builds the item master from datagen/catalog/items/*.csv plus the GST rate history.

Output tables: item, uom_conversion, item_substitute (plus a rate-change report).
Run:  python -I datagen/generator/build_items.py [output_dir]
"""
import csv
import glob
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REF = ROOT.parent / "data" / "reference"
ITEMS = ROOT / "catalog" / "items"

# items removed because their classification is not settled (kept out of the data rather than guessed)
DROP = {"Grease gun": "hand pump (5%) or hand tool (18%): classification-dependent",
        "Sodium chloride AR": "salt is exempt, which muddles the test",
        "Corner protector 40 mm": "carton or other paper article: rate unclear"}
# HSN/SAC corrections suggested by the rate research: (item name or None for all, old code, new code)
FIX_CODE = [("Cotton coverall", "621133", "621132"), ("Welding helmet auto darkening", "902000", "650610"),
            ("Train ticket AC 3 tier", "996411", "996421"), ("Canteen catering per plate", "996331", "996333"),
            ("Legal retainer monthly", "998212", "998211"), ("PVC box file A4", "482030", "392610"),
            ("Box file A4 PVC", "482030", "392610"), ("Lithium grease EP2 18 kg", "271019", "340399"),
            ("Rubber hose 1 inch", "400910", "400912")]
# rates for corrected codes where the old code's row does not apply: code -> (before, after, confidence, note)
GL_OVERRIDE = {"AMC IT hardware annual": "5450", "AMC CCTV and network annual": "5450", "AMC lift annual": "5250", "AMC air conditioner annual": "5250",
               "AMC fire alarm annual": "5250", "Building repair and painting": "5250", "Instrument calibration": "5580", "Weighbridge calibration annual": "5580",
               "IT support retainer monthly": "5450", "Leased line 100 Mbps": "5460", "VAPT assessment": "5410", "Conference event management": "5540",
               "Exhibition stall": "5540", "Print advertisement insertion": "5540", "Express courier documents": "5330", "Express courier parcel 5 kg": "5330",
               "Warehouse pallet storage": "5220", "Full truck load Pune to Vadodara": "5220", "LTL freight Pune to Bengaluru": "5220",
               "Inter-state container freight": "5220", "Hydra crane hire per day": "5590", "Loading and unloading": "5220", "Legal retainer monthly": "5420",
               "Legal opinion per matter": "5420", "Statutory audit annual": "5430", "Tax audit annual": "5430", "Training programme per day": "5530",
               "Canteen catering per plate": "5520", "Pest control quarterly": "5340", "Garden maintenance monthly": "5340"}
OPEX_GL_FOR_SMALL_CAPEX = "5320"
EXTRA_RATES = {"400821": ("18", "18", "SECONDARY", "Vulcanised rubber sheets: 18% before and after"),
               "732690": ("18", "18", "SECONDARY", "Other articles of iron or steel: 18% before and after"),
               "848280": ("18", "18", "SECONDARY", "Other bearings: 18% before and after"),
               "848320": ("18", "18", "SECONDARY", "Bearing housings: 18% before and after"),
               "392329": ("18", "18", "SECONDARY", "Other plastic bags and pouches: 18% before and after"),
               "720839": ("18", "18", "SECONDARY", "Hot rolled coil under 3 mm: 18% before and after")}
RATE_FOR_FIXED = {"650610": ("18", "18", "SECONDARY", "Welding helmet is safety headgear 6506 10: 18% before and after"),
                  "340399": ("18", "18", "SECONDARY", "Lubricating preparations: 18% before and after"),
                  "392610": ("18", "18", "SECONDARY", "Office or school supplies of plastics: 18% before and after"), **EXTRA_RATES}
NIL_NOTE = "Nil rate from 22-Sep-2025 under the exemption Notification 10/2025-CT(Rate)"
HAZARD = re.compile(r"acid|acetone|methanol|isopropyl|thinner|solvent|paint|primer|epoxy|hydroxide|degreaser|hydrochloric|sulphuric|nitric", re.I)
SUBSTITUTES = [("A4 copier paper 75 GSM", "A4 copier paper 80 GSM", "Heavier grade accepted if 75 GSM is unavailable"),
               ("Ball pen blue 0.7 mm", "Ball pen black 0.7 mm", "Colour substitute when blue is out of stock"),
               ("Ball bearing 6205-2RS", "Ball bearing 6206-2RS", "Not interchangeable: listed only as a check that near-matches are not auto-substituted")]


def read(path, enc="utf-8"):
    with open(path, encoding=enc, newline="") as f:
        return list(csv.DictReader(f))


def split_rate(s):
    parts = [p.strip() for p in s.split("|")]
    return parts[0], (parts[1] if len(parts) > 1 else "")


def build():
    cats = {c["code"]: c for c in read(ITEMS / "categories.csv")}
    hist = {r["hsn_sac"]: r for r in read(REF / "gst_rate_history_catalogue.csv", "utf-8-sig")}
    uqc = {r["code"] for r in read(REF / "gst_uqc_units.csv")} | {"OTH"}
    raw = []
    for f in ["items_ofc.csv", "items_ith_sws.csv", "items_mro.csv", "items_ele_tls.csv", "items_pkg_raw.csv",
              "items_chm_ppe.csv", "items_fur_cap.csv", "items_services.csv"]:
        raw += read(ITEMS / f)
    items, convs, subs, report, errs = [], [], [], [], []
    by_name = {}
    n = 0
    for r in raw:
        if r["name"] in DROP:
            continue
        n += 1
        code = f"ITM-{n:05d}"
        cat = cats[r["category"]]
        hsn = r["hsn_sac"]
        src_hsn = hsn
        for name, old, new in FIX_CODE:
            if r["name"] == name and hsn == old:
                hsn = new
        if hsn not in hist and hsn in RATE_FOR_FIXED:
            b, a, conf, note = RATE_FOR_FIXED[hsn]
            h = dict(rate_before_pct=b, rate_after_pct=a, confidence=conf, condition_note=note, reverse_charge="no", source="Derived", changed="no")
        else:
            h = dict(hist[src_hsn]) if hsn != src_hsn and src_hsn in hist and hsn not in RATE_FOR_FIXED else dict(hist[hsn]) if hsn in hist else None
            if hsn in RATE_FOR_FIXED and r["name"] in ("Welding helmet auto darkening", "Lithium grease EP2 18 kg", "Box file A4 PVC", "PVC box file A4"):
                b, a, conf, note = RATE_FOR_FIXED[hsn]
                h = dict(rate_before_pct=b, rate_after_pct=a, confidence=conf, condition_note=note, reverse_charge="no", source="Derived", changed="no")
        if h is None:
            errs.append(f"{r['name']}: no rate row for {hsn}")
            continue
        b1, b2 = split_rate(h["rate_before_pct"])
        a1, a2 = split_rate(h["rate_after_pct"])
        if r["name"] in ("Corrugated box 5-ply 450x300x300",) and False:
            pass
        if r["base_uom"] not in uqc or r["purchase_uom"] not in uqc:
            errs.append(f"{r['name']}: unit {r['base_uom']}/{r['purchase_uom']} not in UQC list")
        price = float(r["price"])
        if price <= 0:
            errs.append(f"{r['name']}: price")
        if cat["kind"] == "goods" and not re.fullmatch(r"\d{6}", hsn):
            errs.append(f"{r['name']}: goods HSN {hsn}")
        attrs = dict(kv.split("=", 1) for kv in r["attrs"].split(";") if "=" in kv)
        note = h["condition_note"]
        changed = h["changed"] == "yes" or (b1 != a1)
        small_capex = cat["capex"] == "true" and price < 10000
        gl = GL_OVERRIDE.get(r["name"]) or (OPEX_GL_FOR_SMALL_CAPEX if small_capex else cat["default_gl"])
        item = dict(code=code, name=r["name"], description=r["description"], category_code=r["category"], kind=cat["kind"], default_gl=gl,
                    brand=r["brand"], base_uom=r["base_uom"], purchase_uom=r["purchase_uom"], pack_size=r["pack_qty"], moq=r["moq"],
                    hsn_sac=hsn, gst_rate_before=b1, gst_rate_after=a1, gst_rate_alt_before=b2, gst_rate_alt_after=a2,
                    gst_rate_change_date="2025-09-22" if changed else "", gst_rate_confidence=h["confidence"],
                    gst_reverse_charge=h["reverse_charge"].split(" - ")[0].split(":")[0].strip()[:12] if h["reverse_charge"] else "no",
                    gst_note=note, standard_price=f"{price:.2f}", abc_class=r["abc"], stocked=cat["stocked"], capex="false" if small_capex else cat["capex"],
                    inspection_required=cat["inspection_required"],
                    hazardous=str(bool(HAZARD.search(r["name"])) and r["category"] in ("CHM",)).lower(),
                    default_lead_days=r["lead_days"], attributes=json.dumps(attrs, separators=(",", ":")), active="true")
        items.append(item)
        by_name[r["name"]] = code
        if r["purchase_uom"] != r["base_uom"] and float(r["pack_qty"]) != 1:
            convs.append(dict(item_code=code, from_uom=r["purchase_uom"], to_uom=r["base_uom"], factor=f"{float(r['pack_qty']):.3f}"))
        if changed:
            report.append((code, r["name"], hsn, b1 + ("|" + b2 if b2 else ""), a1 + ("|" + a2 if a2 else ""), h["confidence"]))
    for a, b, why in SUBSTITUTES:
        if a in by_name and b in by_name:
            subs.append(dict(item_code=by_name[a], substitute_code=by_name[b], note=why))
    return dict(item=items, uom_conversion=convs, item_substitute=subs), report, errs


def write(tables, out):
    out.mkdir(parents=True, exist_ok=True)
    for name, rows in tables.items():
        if not rows:
            continue
        with open(out / f"{name}.csv", "w", encoding="utf-8", newline="\n") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT.parent / "data" / "datasets" / "_preview" / "items"
    tables, report, errs = build()
    write(tables, out)
    from collections import Counter
    it = tables["item"]
    print("items", len(it), "| goods", sum(i["kind"] == "goods" for i in it), "| services", sum(i["kind"] == "service" for i in it))
    print("by category", dict(Counter(i["category_code"] for i in it)))
    print("rate after 22-Sep-2025", dict(Counter(i["gst_rate_after"] for i in it)))
    print("rate before", dict(Counter(i["gst_rate_before"] for i in it)))
    print("confidence", dict(Counter(i["gst_rate_confidence"] for i in it)))
    print(f"\n{len(report)} items whose rate changed on 2025-09-22:")
    for code, name, hsn, b, a, conf in report:
        print(f"  {code} {name[:38]:38s} {hsn}  {b:>5s}% -> {a}%  [{conf}]")
    if errs:
        print("\nPROBLEMS:", *errs, sep="\n  ")
        sys.exit(1)
    print("\nitem build OK ->", out)
