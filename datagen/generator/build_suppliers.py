"""Builds the supplier master tables from datagen/catalog/suppliers/suppliers_seed.csv.

Deterministic: the same seed file always gives the same output. Every PAN carries the digits 0000 (never issued
by the Income Tax Department), so no generated PAN or GSTIN can belong to a real taxpayer.

Run:  python -I datagen/generator/build_suppliers.py [output_dir]
"""
import csv
import hashlib
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from ids import make_gstin, make_pan, is_valid_gstin  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CAT = ROOT / "catalog"
STATE_ABBR = {"27": "MH", "24": "GJ", "29": "KA", "33": "TN", "7": "DL", "07": "DL", "36": "TS", "09": "UP", "9": "UP",
              "06": "HR", "6": "HR", "19": "WB"}
PAN_TYPE = {"pvt_ltd": "C", "public_ltd": "C", "llp": "F", "partnership": "F", "proprietorship": "P"}
TDS_BY_CAT = {"OFC": "194Q", "ITH": "194Q", "SWS": "194J(a)", "MRO": "194Q", "ELE": "194Q", "TLS": "194Q", "PKG": "194Q",
              "RAW": "194Q", "CHM": "194Q", "PPE": "194Q", "FUR": "194Q", "CAP": "194Q", "FAC": "194C", "AMC": "194C",
              "LOG": "194C", "PRO": "194J(b)", "TRV": "none"}
FIRST = {
    "west": ["Sandeep", "Vaishali", "Mahesh", "Rupali", "Nitin", "Snehal", "Amit", "Pallavi", "Jignesh", "Hetal", "Bhavesh", "Komal"],
    "south": ["Venkatesh", "Lakshmi", "Srinivas", "Divya", "Karthik", "Meena", "Ramesh", "Anitha", "Suresh", "Kavitha", "Prasad", "Shobha"],
    "north": ["Vikas", "Poonam", "Rakesh", "Seema", "Deepak", "Anjali", "Manoj", "Ritu", "Sanjeev", "Neeraj", "Pankaj", "Shalini"],
    "east": ["Subhajit", "Moumita", "Arnab", "Sudipta", "Debashis", "Ritika", "Partha", "Madhuri", "Soumya", "Ishita", "Anirban", "Rupa"],
}
LAST = {
    "west": ["Kulkarni", "Joshi", "Patil", "Shah", "Desai", "Patel", "Mehta", "Jadhav", "Gaikwad", "Pawar", "Chavan", "Bhosale"],
    "south": ["Iyer", "Nair", "Reddy", "Rao", "Naidu", "Pillai", "Gowda", "Shetty", "Murthy", "Hegde", "Krishnan", "Menon"],
    "north": ["Sharma", "Gupta", "Verma", "Mishra", "Agarwal", "Singh", "Chaudhary", "Tyagi", "Bansal", "Kapoor", "Saxena", "Arora"],
    "east": ["Banerjee", "Das", "Ghosh", "Mukherjee", "Sen", "Dutta", "Chatterjee", "Bose", "Roy", "Saha", "Mondal", "Pal"],
}
REGION = {"27": "west", "24": "west", "29": "south", "33": "south", "36": "south", "07": "north", "7": "north", "09": "north",
          "9": "north", "06": "north", "6": "north", "19": "east"}
PROFILE = {
    "reliable": dict(lead_mult=0.95, lead_sd=0.10, on_time=0.93, part=0.05, reject=0.010, drift=1.0, inv_err=0.02, reply_h=6),
    "average": dict(lead_mult=1.00, lead_sd=0.20, on_time=0.80, part=0.10, reject=0.030, drift=2.0, inv_err=0.05, reply_h=16),
    "poor": dict(lead_mult=1.25, lead_sd=0.40, on_time=0.55, part=0.20, reject=0.070, drift=3.5, inv_err=0.12, reply_h=40),
}
# a few suppliers also hold a registration in a second state (dispatch address decides IGST vs CGST+SGST)
EXTRA_STATE = {"Konkan Polymers": ("29", "Bengaluru", "560058"), "Baroda Industrial Corporation": ("27", "Pune", "411026"),
               "Gujarat Polychem": ("27", "Mumbai", "400093"), "Heritage Compressors & Engineering": ("27", "Pune", "411019"),
               "Delhi Datatech Solutions": ("29", "Bengaluru", "560100"), "Western Industrial Supplies": ("24", "Vadodara", "390010")}
SPECIAL = {  # planted supplier situations
    "Shiv Shakti Chemical Traders": dict(pan_status="inoperative"),
    "Yamuna Consulting": dict(lower_tds=("1.00", "2026-03-31", "2000000.00")),
}


def h(s, n=1_000_000):
    return int(hashlib.sha256(s.encode()).hexdigest(), 16) % n


def slug(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


def read(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def build():
    seed = read(CAT / "suppliers" / "suppliers_seed.csv")
    banks = read(CAT / "org" / "banks.csv")
    gl = {}
    sup, addr, contact, bank, scat, docs, hist, truth = [], [], [], [], [], [], [], []
    used_pan = set()
    for idx, r in enumerate(seed, 1):
        code = f"SUP-{idx:04d}"
        st = r["state"].zfill(2)
        letters = re.sub(r"[^A-Z]", "", r["trade_name"].upper())
        p3 = (letters + "XXX")[:3]
        ptype = PAN_TYPE[r["legal_form"]]
        initial = letters[0]
        last = "ABCDEFGHJKLMNPQRSTUVWXYZ"[h(code) % 24]
        pan = make_pan(p3, ptype, initial, last)
        k = 0
        while pan in used_pan:
            k += 1
            pan = make_pan(p3, ptype, initial, "ABCDEFGHJKLMNPQRSTUVWXYZ"[(h(code) + k) % 24])
        used_pan.add(pan)
        reg = r["gst_reg"]
        gstin = make_gstin(st, pan, 1) if reg in ("regular", "composition") else ""
        turnover = float(r["turnover_cr"])
        e_inv = turnover > 5.0
        hsn_digits = 6 if turnover > 5.0 else 4
        msme = r["msme"]
        agreed = r["agreed_days"]
        if agreed:
            assert msme in ("micro", "small") and int(agreed) <= 45, code
        cats = r["categories"].split(";")
        tds = next((TDS_BY_CAT[c] for c in cats if TDS_BY_CAT[c] != "none"), "none")
        sp = SPECIAL.get(r["trade_name"], {})
        lower = sp.get("lower_tds", ("", "", ""))
        udyam = f"UDYAM-{STATE_ABBR[st]}-00-{1000000 + idx:07d}" if msme != "none" else ""
        domain = slug(r["trade_name"]) + ".example"
        region = REGION[st]
        bk = banks[h(code + "b") % len(banks)]
        ifsc = f"{bk['ifsc_prefix']}9{h(code + 'i', 100000):05d}"
        assert len(ifsc) == 11, ifsc
        terms = r["terms"]
        rc = "conditional" if any(c in ("LOG", "PRO") for c in cats) else "no"
        sup.append(dict(code=code, legal_name=r["legal_name"], trade_name=r["trade_name"], legal_form=r["legal_form"], pan=pan,
                        pan_status=sp.get("pan_status", "operative"), gst_reg_type=reg, gstin=gstin, state_code=st,
                        turnover_cr=r["turnover_cr"], e_invoice_applicable=str(e_inv).lower(), hsn_digits=hsn_digits,
                        msme_category=msme, udyam_no=udyam, msme_agreement=str(bool(agreed)).lower(), agreed_payment_days=agreed,
                        payment_terms_code=terms, tds_section_default=tds, lower_tds_rate=lower[0], lower_tds_valid_to=lower[1],
                        lower_tds_limit=lower[2], reverse_charge=rc, status=(r.get("status") or "active"), profile=r["profile"], style=r["style"],
                        hold_out=str(idx % 6 == 3).lower(), price_level=r["price_level"]))
        addr.append(dict(supplier_code=code, type="registered", gstin=gstin, address=f"{10 + h(code + 'a', 90)}, Industrial Area",
                         city=r["city"], state_code=st, pincode=r["pincode"]))
        if r["trade_name"] in EXTRA_STATE and gstin:
            s2, c2, p2 = EXTRA_STATE[r["trade_name"]]
            addr.append(dict(supplier_code=code, type="dispatch", gstin=make_gstin(s2, pan, 1),
                             address=f"Unit {1 + h(code + 'u', 9)}, Warehouse Complex", city=c2, state_code=s2, pincode=p2))
        for j in range(2 if turnover > 5 else 1):
            fn = FIRST[region][h(code + f"f{j}") % 12]
            ln = LAST[region][h(code + f"l{j}") % 12]
            role = ["sales", "accounts"][j]
            contact.append(dict(supplier_code=code, name=f"{fn} {ln}", role=role, email=f"{fn.lower()}.{ln.lower()}@{domain}",
                                phone=f"+91 00000 {h(code + str(j), 100000):05d}", is_primary=str(j == 0).lower()))
        draft = r["trade_name"] == "Apex Chemicals Trading"
        bank.append(dict(supplier_code=code, bank_code=bk["code"], account_no=f"000{h(code + 'n', 10**9):09d}", ifsc=ifsc,
                         verified="false" if draft else "true", verified_on="" if draft else "2024-09-15"))
        for c in cats:
            scat.append(dict(supplier_code=code, category_code=c, approved_from="2024-09-01"))
        # documents with expiry; a few planted to lapse inside the dataset window
        def doc(t, num, issued, expires):
            docs.append(dict(supplier_code=code, doc_type=t, number=num, issued_on=issued, expires_on=expires, verified="true"))
        if gstin:
            doc("gst_cert", gstin, f"{2017 + h(code + 'g', 6)}-{1 + h(code + 'gm', 12):02d}-{1 + h(code + 'gd', 28):02d}", "")
        doc("pan", pan, "2015-04-01", "")
        if lower[0]:
            doc("lower_tds_cert", f"LDC-{idx:04d}", "2025-04-10", lower[1])
        if udyam:
            doc("udyam", udyam, f"{2020 + h(code + 'u', 4)}-{1 + h(code + 'um', 12):02d}-{1 + h(code + 'ud', 28):02d}", "")
        if agreed:
            doc("msme_agreement", f"AGR-{code}", f"2024-{4 + h(code + 'a', 6):02d}-{1 + h(code + 'ad', 28):02d}", "2027-07-31")
        if any(c in ("RAW", "CAP", "PKG") for c in cats) and turnover > 8:
            doc("iso", f"ISO9001-{idx:04d}", "2023-11-01", "2026-10-31" if idx % 4 == 0 else "2027-10-31")
        if "CHM" in cats and r["trade_name"] != "Apex Chemicals Trading":
            doc("licence", f"CHEM-LIC-{idx:04d}", "2024-04-01", "2026-03-31" if idx % 2 == 0 else "2027-03-31")
        if any(c in ("FAC", "LOG") for c in cats):
            doc("insurance", f"INS-{idx:05d}", "2025-04-01", "2026-09-30" if idx % 9 == 0 else "2027-03-31")
        incomplete = r["trade_name"] == "Apex Chemicals Trading"
        if not incomplete:
            doc("cancelled_cheque", f"CHQ-{idx:04d}", "2024-09-10", "")
        if r.get("status") == "draft":
            hist.append(dict(supplier_code=code, at="2026-09-21T10:00:00+05:30", from_status="", to_status="draft", reason="Onboarding started", by_user="U006"))
        else:
            hist.append(dict(supplier_code=code, at="2024-09-20T10:00:00+05:30", from_status="draft", to_status="active",
                             reason="Onboarding complete", by_user="U006"))
        p = PROFILE[r["profile"]]
        truth.append(dict(supplier_code=code, profile=r["profile"], lead_mult=p["lead_mult"], lead_sd=p["lead_sd"], on_time_prob=p["on_time"],
                          part_delivery_prob=p["part"], rejection_rate=p["reject"], price_drift_pct_per_quarter=p["drift"],
                          invoice_error_rate=p["inv_err"], reply_hours_mean=p["reply_h"], writing_style=r["style"]))
    # planted status changes
    by_trade = {s["trade_name"]: s for s in sup}
    hub = by_trade["Hubli Fastener House"]["code"]
    hist.append(dict(supplier_code=hub, at="2026-03-01T09:00:00+05:30", from_status="active", to_status="blocked",
                     reason="Repeated quality rejections", by_user="U006"))
    hist.append(dict(supplier_code=hub, at="2026-04-15T11:30:00+05:30", from_status="blocked", to_status="active",
                     reason="Corrective action accepted", by_user="U006"))
    return dict(supplier=sup, supplier_address=addr, supplier_contact=contact, supplier_bank_account=bank,
                supplier_category=scat, supplier_document=docs, supplier_status_history=hist, truth_supplier=truth)


def check(t):
    errs = []
    for s in t["supplier"]:
        if s["gstin"] and not is_valid_gstin(s["gstin"]):
            errs.append(f"bad GSTIN {s['code']}")
        if s["gstin"] and s["gstin"][2:12] != s["pan"]:
            errs.append(f"GSTIN/PAN mismatch {s['code']}")
        if "0000" not in s["pan"]:
            errs.append(f"PAN not synthetic {s['code']}")
        if s["gst_reg_type"] == "unregistered" and s["gstin"]:
            errs.append(f"unregistered with GSTIN {s['code']}")
    for a in t["supplier_address"]:
        if a["gstin"] and not is_valid_gstin(a["gstin"]):
            errs.append(f"bad dispatch GSTIN {a['supplier_code']}")
    assert len({s["pan"] for s in t["supplier"]}) == len(t["supplier"]), "duplicate PAN"
    return errs


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT.parent / "data" / "datasets" / "_preview" / "suppliers"
    out.mkdir(parents=True, exist_ok=True)
    tables = build()
    errs = check(tables)
    for name, rows in tables.items():
        with open(out / f"{name}.csv", "w", encoding="utf-8", newline="\n") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
    from collections import Counter
    sup = tables["supplier"]
    print("suppliers", len(sup), "| states", dict(Counter(s["state_code"] for s in sup)))
    print("gst_reg", dict(Counter(s["gst_reg_type"] for s in sup)), "| msme", dict(Counter(s["msme_category"] for s in sup)))
    print("e-invoice", dict(Counter(s["e_invoice_applicable"] for s in sup)), "| profile", dict(Counter(s["profile"] for s in sup)),
          "| hold-out", sum(s["hold_out"] == "true" for s in sup))
    print("addresses", len(tables["supplier_address"]), "| contacts", len(tables["supplier_contact"]), "| documents", len(tables["supplier_document"]))
    if errs:
        print("PROBLEMS:", *errs, sep="\n  ")
        sys.exit(1)
    print("supplier build OK ->", out)
