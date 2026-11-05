"""Day-by-day simulation of CBC's purchasing: requests -> approvals -> sourcing -> PO -> receipt -> invoice.

Every case is simulated at the moment it is created and its whole future is computed immediately; rows dated after
CUTOFF are dropped at the end and statuses are derived from what remains, so the dataset ends with work in progress.
Finance (TDS, due dates, payments) is in finance.py.

Simplifications (deliberate): one currency, no advance payments, no po_charge, no returns table (rejections are
credited), free-text request lines are not generated (the document step adds them), email bodies come later.
"""
import hashlib
import math
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal

from common import (CUTOFF, START, Calendar, Numbering, Rng, D, add_months, daterange, fy, fy4, fy_start, iso, month_start, money,
                    q2, q3, qty_s, stable_hash, sub_rng, ts)
import build_suppliers

CUT_DT = datetime(CUTOFF.year, CUTOFF.month, CUTOFF.day, 23, 59)
GENERAL_STAFF = ["U012", "U013", "U015", "U017", "U019", "U021", "U011", "U007", "U008", "U009", "U004", "U005", "U003"]
APPROVE_HOURS = {"manager": (1, 20), "dept_head": (2, 36), "director": (4, 72), "cfo": (4, 48), "procurement_head": (1, 24),
                 "finance_manager": (1, 16)}


def dt_at(d, hour=10.0):
    return datetime(d.year, d.month, d.day, int(hour), int((hour % 1) * 60))


class SimBase:
    def __init__(self, t, ctx, seed=42, start=START, end=CUTOFF, only_streams=None):
        self.t, self.pb, self.seed = t, ctx["pricebook"], seed
        self.start, self.end = start, end
        self.R = sub_rng(seed, "sim")
        self.items = {i["code"]: i for i in t["item"]}
        self.sup = {s["code"]: s for s in t["supplier"]}
        self.struth = {x["supplier_code"]: x for x in t["truth_supplier"]}
        self.users = {u["code"]: u for u in t["user"]}
        self.dept = {d["code"]: d for d in t["department"]}
        self.loc = {l["code"]: l for l in t["location"]}
        self.cats = {c["code"]: c for c in t["category"]}
        self.cal = Calendar(t["location"])
        self.num = Numbering(t["number_series"], {l["code"]: l["loc_short"] for l in t["location"]})
        self.tol = defaultdict(dict)
        for o in t["tolerance"]:
            self.tol[o["category_code"]][o["key"]] = D(o["value"])
        self.terms = {p["code"]: p for p in t["payment_terms"]}
        self.sup_cats = defaultdict(set)
        for s in t["supplier_category"]:
            self.sup_cats[s["supplier_code"]].add(s["category_code"])
        self.price_rec = defaultdict(list)
        for p in t["price_record"]:
            self.price_rec[p["item_code"]].append(p)
        self.sup_docs = defaultdict(list)
        for d in t["supplier_document"]:
            self.sup_docs[d["supplier_code"]].append(d)
        self.sup_hist = defaultdict(list)
        for h in t["supplier_status_history"]:
            self.sup_hist[h["supplier_code"]].append(h)
        self.rate_hist = {}
        for i in t["item"]:
            self.rate_hist[i["code"]] = (D(i["gst_rate_before"]), D(i["gst_rate_after"]), i["gst_rate_change_date"])
        self.deleg = t["delegation"]
        self.rules = self._rule_versions(t["approval_rule"])
        self.users_by_dept = defaultdict(list)
        for u in t["user"]:
            self.users_by_dept[u["department_code"]].append(u["code"])
        self.T = defaultdict(list)
        self.inv = {}      # (item, loc) -> on hand
        self.inbound = defaultdict(list)    # (item, loc) -> [(date, qty)]
        self.pending = {}                   # (item, loc) -> [(expiry date, qty)] requested but not yet ordered
        self.consumed_week = defaultdict(lambda: D(0))
        self.stockouts = []
        self.il = {(x["item_code"], x["location_code"]): x for x in t["item_location"]}
        self.dem = {(x["item_code"], x["location_code"]): x for x in t["truth_item_demand"]}
        for o in t["stock_opening"]:
            self.inv[(o["item_code"], o["location_code"])] = D(o["qty"])
        self.sup_inv_seq = defaultdict(int)
        self.sup_prefix = {}
        for s in t["supplier"]:
            words = [w for w in s["trade_name"].replace("&", " ").split() if w[0].isalpha()]
            self.sup_prefix[s["code"]] = ("".join(w[0] for w in words)[:4]).upper()
        self.fy_reset = {s["code"]: stable_hash(s["code"] + "fy", 10) < 4 for s in t["supplier"]}  # numbering restarts every April
        self.last_sup_inv_no = {}
        self.case_n = 0
        self.proj_by_cc = {}
        self.only_streams = only_streams
        self.contract_used = defaultdict(lambda: D(0))
        self.contract_max = {c["code"]: D(c["max_value"]) for c in t["contract"] if c["max_value"]}
        self.award_count = {}
        self.proj_cc = {p["code"]: p["cost_centre_code"] for p in t["project"]}
        self.cc_loc = {c["code"]: c["location_code"] for c in t["cost_centre"]}
        self.cc_dept = {c["code"]: c["department_code"] for c in t["cost_centre"]}
        bp = defaultdict(lambda: D(0))
        for b in t["budget"]:
            if not b["project_code"]:
                bp[(b["cost_centre_code"], b["gl_code"])] += D(b["amount"])
        self.bp_by_gl = defaultdict(list)
        for (cc_, gl_), amt in bp.items():
            self.bp_by_gl[gl_].append((cc_, amt))
        self.sup_states = defaultdict(set)
        for a in t["supplier_address"]:
            self.sup_states[a["supplier_code"]].add(a["state_code"])

    # ------------------------------------------------------------------ small helpers
    def add(self, table, row, t_event):
        row["_t"] = t_event if isinstance(t_event, datetime) else dt_at(t_event)
        self.T[table].append(row)

    def case(self, ctype, docs, cause, expected, when):
        self.case_n += 1
        self.add("truth_case", dict(case_id=f"CASE-{self.case_n:05d}", type=ctype, documents=docs, cause=cause, expected_result=expected,
                                    planted_on=iso(when if isinstance(when, date) and not isinstance(when, datetime) else when.date())), when)

    def user_loc(self, u):
        return self.users[u]["location_code"]

    def deleg_for(self, user, d):
        for x in self.deleg:
            if x["from_user"] == user and date.fromisoformat(x["start_date"]) <= d <= date.fromisoformat(x["end_date"]):
                return x["to_user"]
        return None

    def _rule_versions(self, rows):
        by = defaultdict(list)
        for r in rows:
            by[r["rule_code"]].append(r)
        for v in by.values():
            v.sort(key=lambda r: r["effective_from"])
        return by

    def active_rules(self, document, d):
        out = []
        for code, vs in self.rules.items():
            act = [r for r in vs if r["effective_from"] <= d.isoformat() and r["document"] == document]
            if act:
                out.append(act[-1])
        return out

    def work_hours_after(self, dt, hours, loc):
        """Advance by working hours (9 per working day) on the location calendar."""
        d = dt.date()
        h = dt.hour + dt.minute / 60.0
        if not self.cal.is_working(d, loc):
            d = self.cal.next_working(d, loc)
            h = 9.5
        h = max(h, 9.0)
        total = h - 9.0 + hours
        d = self.cal.add_working(d, int(total // 9), loc) if int(total // 9) else d
        return dt_at(d, 9.0 + (total % 9))

    def can_supply(self, s, loc):
        """Composition and unregistered suppliers cannot make inter-state supplies."""
        if s["gst_reg_type"] == "regular":
            return True
        return self.loc[loc]["state_code"] in self.sup_states[s["code"]]

    def elapsed_after(self, dt, hours, loc):
        """Clock hours after dt, moved to the next working time (replies and approvals take elapsed, not working, hours)."""
        t = dt + timedelta(hours=hours)
        d = t.date()
        if not self.cal.is_working(d, loc):
            return dt_at(self.cal.next_working(d, loc), 9.5)
        if t.hour < 9:
            return dt_at(d, 9.25)
        if t.hour >= 18:
            return dt_at(self.cal.next_working(d + timedelta(days=1), loc), 9.5)
        return t

    def supplier_active(self, code, d):
        status = None
        for h in sorted(self.sup_hist[code], key=lambda x: x["at"]):
            if h["at"][:10] <= d.isoformat():
                status = h["to_status"]
        return status == "active"

    def supplier_has_valid_docs(self, code, cats, d):
        for doc in self.sup_docs[code]:
            if doc["doc_type"] in ("licence", "iso") and doc["expires_on"] and doc["expires_on"] < d.isoformat():
                if doc["doc_type"] == "licence" and "CHM" in cats:
                    return False
        return True

    def gst_rate(self, item_code, d):
        before, after, chg = self.rate_hist[item_code]
        return after if (chg and d >= date.fromisoformat(chg)) else before if chg else after

    def tax(self, taxable, rate, intra):
        t = q2(taxable * rate / D(100))
        if intra:
            c = q2(taxable * rate / D(200))
            return c, c, D("0.00")
        return D("0.00"), D("0.00"), t

    # ------------------------------------------------------------------ approvals
    def resolve_chain(self, chain, requester, d):
        out = []
        for tok in chain.split(">"):
            if tok == "auto":
                return "auto"
            u = None
            if tok == "manager":
                u = self.users[requester]["manager_code"] or None
            elif tok == "dept_head":
                u = self.dept[self.users[requester]["department_code"]]["head_user"]
            elif tok == "director":
                u = "U001"
            elif tok == "cfo":
                u = "U002"
            elif tok == "procurement_head":
                u = "U006"
            elif tok == "finance_manager":
                u = "U003"
            if u and u != requester and u not in [x[1] for x in out]:
                out.append((tok, u))
        if not out:
            # nobody senior enough is left (for example the head of procurement raising his own PO): escalate one level
            out.append(("cfo", "U002") if requester != "U002" else ("director", "U001"))
        return out

    def approve(self, document, doc_no, value, spend_type, requester, created_dt, loc, *, force_ok=False):
        """Returns (approved_dt or None, status). Adds approval_event rows."""
        d = created_dt.date()
        rules = [r for r in self.active_rules(document, d)
                 if D(r["min_value"]) <= value <= D(r["max_value"]) and r["spend_type"] in (spend_type, "ANY")]
        if not rules:
            return created_dt, "approved"
        rule = rules[0]
        chain = self.resolve_chain(rule["approver_chain"], requester, d)
        if chain == "auto":
            self.add("approval_event", dict(document=document, document_no=doc_no, step=1, approver="SYSTEM", on_behalf_of="", action="auto_approved",
                                            at=ts(created_dt), comment="Within auto-approval limit", rule_code=rule["rule_code"]), created_dt)
            return created_dt, "approved"
        cur = created_dt
        reject_step = self.R.randint(1, len(chain)) if (not force_ok and self.R.bern(0.03)) else 0
        ret = (not force_ok) and self.R.bern(0.02)
        for step, (tok, u) in enumerate(chain, 1):
            lo, hi = APPROVE_HOURS.get(tok, (2, 24))
            cur = self.elapsed_after(cur, self.R.uniform(lo, hi), self.users[u]["location_code"])
            who, behalf = u, ""
            dl = self.deleg_for(u, cur.date())
            if dl:
                who, behalf = (dl, u) if dl != requester else ("U001", u)
            ev = dict(document=document, document_no=doc_no, step=step, approver=who, on_behalf_of=behalf, rule_code=rule["rule_code"])
            if step == reject_step:
                self.add("approval_event", dict(ev, action="rejected", at=ts(cur), comment="Not required or over budget"), cur)
                return cur, "rejected"
            if ret and step == 1:
                self.add("approval_event", dict(ev, action="returned", at=ts(cur), comment="Please add justification"), cur)
                cur = self.elapsed_after(cur, self.R.uniform(6, 30), self.users[u]["location_code"])
                ret = False
            self.add("approval_event", dict(ev, action="approved", at=ts(cur), comment=""), cur)
        return cur, "approved"
