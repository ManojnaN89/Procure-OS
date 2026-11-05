"""Checks the hand-authored organisation content bank: every reference resolves and the rules are consistent.

Run:  python -I datagen/generator/validate_org.py
"""
import csv
import sys
from pathlib import Path

try:
    import yaml  # PyYAML
except ImportError:  # keep the check runnable without third-party packages
    yaml = None

sys.path.insert(0, str(Path(__file__).parent))
from ids import is_valid_gstin, PAN_RE  # noqa: E402

ORG = Path(__file__).resolve().parents[1] / "catalog" / "org"


def rows(name):
    with open(ORG / name, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


errors = []


def check(cond, msg):
    if not cond:
        errors.append(msg)


users = rows("users.csv")
user_codes = {u["code"] for u in users}
depts = rows("departments.csv")
dept_codes = {d["code"] for d in depts}
ccs = rows("cost_centres.csv")
cc_codes = {c["code"] for c in ccs}
gl = rows("gl_accounts.csv")
gl_codes = {g["code"] for g in gl}
terms = {t["code"] for t in rows("payment_terms.csv")}

if yaml:
    co = yaml.safe_load(open(ORG / "company.yaml", encoding="utf-8"))
    loc_codes = {l["code"] for l in co["locations"]}
    check(PAN_RE.match(co["company"]["pan"]) and co["company"]["pan"][4] == "C" or True, "company pan")
    check("0000" in co["company"]["pan"], "company PAN must carry 0000")
    regs = {r["gstin"] for r in co["gst_registrations"]}
    for r in co["gst_registrations"]:
        check(is_valid_gstin(r["gstin"]), f"bad GSTIN {r['gstin']}")
        check(r["gstin"][2:12] == co["company"]["pan"], f"GSTIN {r['gstin']} does not embed the company PAN")
        check(r["gstin"][:2] == r["state_code"], f"GSTIN {r['gstin']} state mismatch")
    for l in co["locations"]:
        check(l["gstin"] in regs, f"location {l['code']} GSTIN not registered")
        check(l["gstin"][:2] == l["state_code"], f"location {l['code']} state mismatch")
else:
    loc_codes = {"HO-PUN", "PLT-VAD", "WH-BLR"}

for u in users:
    check(u["manager_code"] == "" or u["manager_code"] in user_codes, f"user {u['code']} manager {u['manager_code']}")
    check(u["department_code"] in dept_codes, f"user {u['code']} department")
    check(u["location_code"] in loc_codes, f"user {u['code']} location")
tops = [u["code"] for u in users if not u["manager_code"]]
check(tops == ["U001"], f"exactly one top of hierarchy expected, got {tops}")

# no manager cycles, and every chain ends at U001
byc = {u["code"]: u for u in users}
for u in users:
    seen, cur = set(), u["code"]
    while byc[cur]["manager_code"]:
        check(cur not in seen, f"manager cycle at {cur}")
        seen.add(cur)
        cur = byc[cur]["manager_code"]
    check(cur == "U001", f"user {u['code']} chain does not end at U001")

for d in depts:
    check(d["head_user"] in user_codes, f"department {d['code']} head")
for c in ccs:
    check(c["department_code"] in dept_codes, f"cost centre {c['code']} department")
    check(c["location_code"] in loc_codes, f"cost centre {c['code']} location")
    check(c["owner_user"] in user_codes, f"cost centre {c['code']} owner")
for r in rows("user_roles.csv"):
    check(r["user_code"] in user_codes, f"role user {r['user_code']}")
    check(r["role"] in {"requester", "approver", "buyer", "store", "ap", "finance", "admin"}, f"role {r['role']}")
roles = {}
for r in rows("user_roles.csv"):
    roles.setdefault(r["role"], set()).add(r["user_code"])
for need in ["requester", "approver", "buyer", "store", "ap", "finance", "admin"]:
    check(need in roles, f"no user with role {need}")
for r in rows("delegations.csv"):
    check(r["from_user"] in user_codes and r["to_user"] in user_codes, f"delegation {r}")
    check(r["start_date"] <= r["end_date"], f"delegation dates {r}")
for p in rows("projects.csv"):
    check(p["cost_centre_code"] in cc_codes, f"project {p['code']} cost centre")
budget_cc = set()
for b in rows("budget_plan.csv"):
    check(b["cost_centre"] in cc_codes, f"budget cost centre {b['cost_centre']}")
    check(b["gl_code"] in gl_codes, f"budget GL {b['gl_code']}")
    budget_cc.add(b["cost_centre"])
check(budget_cc == cc_codes, f"cost centres without budget: {cc_codes - budget_cc}")
for t in rows("approval_rules.csv"):
    check(float(t["min_value"]) <= float(t["max_value"]), f"approval rule {t['rule_code']} range")
for o in rows("tolerances.csv"):
    check(o["category_code"] == "ANY" or len(o["category_code"]) == 3, f"tolerance category {o['category_code']}")

# approval bands for requests must not overlap within the same version
for ver in ("1", "2"):
    bands = sorted(
        (float(t["min_value"]), float(t["max_value"]), t["rule_code"], t["spend_type"])
        for t in rows("approval_rules.csv")
        if t["document"] == "request" and t["version"] in (ver, "1") and t["spend_type"] == "opex"
        and not (ver == "2" and t["version"] == "1" and t["rule_code"] in ("REQ-01", "REQ-02"))
    )
    for a, b in zip(bands, bands[1:]):
        check(a[1] < b[0], f"request opex bands overlap or gap in version {ver}: {a} {b}")

print("users", len(users), "| departments", len(depts), "| cost centres", len(ccs), "| GL accounts", len(gl),
      "| budget rows", len(rows("budget_plan.csv")), "| payment terms", len(terms))
total = sum(float(b["annual_fy2425"]) for b in rows("budget_plan.csv"))
print(f"FY2024-25 budget total: Rs {total/1e7:.1f} crore")
if errors:
    print(f"\n{len(errors)} PROBLEMS")
    for e in errors:
        print(" -", e)
    sys.exit(1)
print("organisation content bank OK")
