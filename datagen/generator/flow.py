"""Purchasing lifecycle (mixin for the simulation): request -> sourcing -> RFQ, with email metadata."""
import hashlib
import math
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal

from common import CUTOFF, D, iso, money, q2, q3, qty_s, ts
from simulate import dt_at, CUT_DT
import build_suppliers

STORE_USER = {"PLT-VAD": "U025", "WH-BLR": "U024", "HO-PUN": "U021"}


class FlowMixin:
    # ------------------------------------------------------------------ requests
    def cost_centre_for(self, cat, loc, requester, gl=None):
        """Choose a cost centre that has a budget for this GL at this location (the requester's own department first)."""
        gl = gl or self.cats[cat]["default_gl"]
        cands = self.bp_by_gl.get(gl, [])
        if not cands:
            return self.R.choice(self.t["cost_centre"])["code"]
        pool = [c for c in cands if self.cc_loc[c[0]] == loc] or cands
        dept = self.users[requester]["department_code"]
        own = [c for c in pool if self.cc_dept[c[0]] == dept]
        pool = own or pool
        return self.R.weighted([c[0] for c in pool], [float(c[1]) for c in pool])

    def est_price(self, item, d):
        return q2(self.pb.market(item, d))

    def new_request(self, d, requester, loc, lines, channel="form", project="", priority="normal", justification=""):
        """lines: [dict(item_code, qty (purchase uom), need_by)]. Creates the request, approvals and everything downstream."""
        created = self.work_dt(d, loc)
        if created.date() > self.end:
            return None
        no = self.num.next("request", created.date(), loc)
        spend = "capex" if any(self.items[l["item_code"]]["capex"] == "true" for l in lines) else "opex"
        rows = []
        total = D(0)
        for n, l in enumerate(lines, 1):
            it = self.items[l["item_code"]]
            cat = it["category_code"]
            gl = it["default_gl"]
            cc = self.proj_cc[project] if project else self.cost_centre_for(cat, loc, requester, gl)
            price = self.est_price(it["code"], created.date())
            total += price * D(l["qty"])
            rows.append(dict(request_no=no, line_no=n, item_code=it["code"], free_text="", attributes=it["attributes"], qty=qty_s(l["qty"]),
                             uom=it["purchase_uom"], est_price=money(price), need_by=iso(l["need_by"]), delivery_location=loc,
                             cost_centre_code=cc, gl_code=gl, category_code=cat, project_code=project, status="open",
                             _qty=D(l["qty"]), _price=price))
        req = dict(no=no, requester=requester, on_behalf_of="", department_code=self.users[requester]["department_code"], ship_to_location=loc,
                   type=("capex" if spend == "capex" else ("service" if all(self.items[l["item_code"]]["kind"] == "service" for l in lines) else "goods")),
                   priority=priority, project_code=project, created_at=ts(created), need_by=iso(min(l["need_by"] for l in lines)), channel=channel,
                   source_ref="", justification=justification, preferred_supplier="", status="submitted", total_value=money(total))
        if channel == "email":
            req["source_ref"] = self.request_email(req, created)
        self.add("request", req, created)
        for r in rows:
            self.add("request_line", r, created)
        appr, st = self.approve("request", no, total, spend, requester, created, loc)
        if st == "rejected":
            req["status"] = "rejected"
            for r in rows:
                r["status"] = "cancelled"
            return req
        req["status"] = "approved"
        self.source(req, rows, appr, loc)
        return req

    def request_email(self, req, when):
        n = len(self.T["email_message"]) + 1
        eid = f"EML-{n:06d}"
        u = self.users[req["requester"]]
        self.add("email_message", dict(id=eid, message_id=f"<{hashlib.md5(f'{n}{req['no']}'.encode()).hexdigest()[:16]}@mail.example>", in_reply_to="",
                                       thread_id=f"THR-request-{req['no']}", mailbox="requests@cbcpl.example", direction="in", from_addr=u["email"],
                                       to_addr="requests@cbcpl.example", subject="", body_ref="", attachments="0", at=ts(when - timedelta(minutes=3)),
                                       plus_tag=f"request-{req['no']}", linked_doc_type="request", linked_doc_no=req["no"], kind="request_email", style="",
                                       supplier_code=""), when - timedelta(minutes=3))
        return eid

    def work_dt(self, d, loc):
        d2 = self.cal.next_working(d, loc)
        return dt_at(d2, self.R.uniform(9.5, 17.0))

    # ------------------------------------------------------------------ sourcing
    def valid_price_records(self, item, d, qty, cat, loc=None):
        out = []
        for p in self.price_rec[item]:
            if not (p["valid_from"] <= d.isoformat() <= p["valid_to"] and D(p["min_qty"]) <= D(qty)):
                continue
            sc = p["supplier_code"]
            if not self.supplier_active(sc, d) or not self.supplier_has_valid_docs(sc, self.sup_cats[sc], d):
                continue
            if loc and not self.can_supply(self.sup[sc], loc):
                continue
            if loc and cat == "FAC" and self.sup[sc]["state_code"] != self.loc[loc]["state_code"]:
                continue
            cc = p["contract_code"]
            mv = self.contract_max.get(cc)
            if cc and mv is not None and self.contract_used.get(cc, D(0)) + D(p["price"]) * D(qty) > mv:
                continue
            out.append(p)
        return out

    def source(self, req, rows, approved_dt, loc):
        """Contract lines go straight to PO; the rest go to an RFQ per category."""
        contract_lines = defaultdict(list)
        rfq_lines = defaultdict(list)
        d_check = (approved_dt + timedelta(days=2)).date()
        for r in rows:
            cat = r["category_code"]
            cands = self.valid_price_records(r["item_code"], d_check, r["_qty"], cat, loc)
            if cands:
                cands.sort(key=lambda p: D(p["price"]))
                best = cands[1] if (len(cands) > 1 and cands[1]["supplier_code"] != cands[0]["supplier_code"] and self.R.bern(0.25)) else cands[0]
                contract_lines[best["supplier_code"]].append((r, D(best["price"]), best))
            else:
                rfq_lines[cat].append(r)
        for sup, lines in contract_lines.items():
            when = self.cal.next_working((approved_dt + timedelta(days=self.R.choice([0, 1, 1, 2]))).date(), loc)
            self.make_po(sup, [dict(req_row=r, price=pr, promised_days=int(p["lead_days"]), contract=p["contract_code"]) for r, pr, p in lines],
                         when, loc, self.dept_buyer(lines[0][0]["category_code"], approved_dt.date()), req["no"],
                         "contract_release" if lines[0][2]["contract_code"] else "standard", req["project_code"], not_before=approved_dt)
        for cat, lines in rfq_lines.items():
            self.run_rfq(req, lines, cat, approved_dt, loc, self.dept_buyer(cat, approved_dt.date()))

    def dept_buyer(self, cat, d):
        b = self.cats[cat]["buyer_user"]
        return self.deleg_for(b, d) or b

    def run_rfq(self, req, lines, cat, approved_dt, loc, buyer):
        R = self.R
        c = self.cats[cat]
        created = self.work_dt((approved_dt + timedelta(days=R.choice([0, 0, 1, 1, 2]))).date(), "HO-PUN")
        created = max(created, self.work_hours_after(approved_dt, 0.5, "HO-PUN"))
        if created.date() > self.end:
            return
        cand = [s for s in self.t["supplier"] if cat in self.sup_cats[s["code"]] and self.supplier_active(s["code"], created.date())
                and self.supplier_has_valid_docs(s["code"], self.sup_cats[s["code"]], created.date()) and self.can_supply(s, loc)]
        if not cand:
            return
        rfq_no = self.num.next("rfq", created.date())
        min_q = int(c["min_quotes"])

        def pick(pool, n):
            chosen = []
            w = {s["code"]: (3.0 if self.struth[s["code"]]["profile"] == "reliable" else 2.0 if self.struth[s["code"]]["profile"] == "average" else 1.0) for s in pool}
            pool = list(pool)
            while pool and len(chosen) < n:
                i = R.choices(range(len(pool)), weights=[w[s["code"]] for s in pool], k=1)[0]
                chosen.append(pool.pop(i))
            return chosen

        invited = pick(cand, max(min(len(cand), min_q + R.choice([0, 1, 1, 2])), min(len(cand), 2)))
        sent = self.work_hours_after(created, R.uniform(1, 5), "HO-PUN")
        due = self.cal.add_working(sent.date(), 8 if cat in ("CAP", "RAW") else 5, "HO-PUN")
        rfq = dict(no=rfq_no, buyer=buyer, created_at=ts(created), due_date=iso(due), type="combined" if cat in ("CAP", "ITH") else "commercial",
                   weights='{"price":0.6,"delivery":0.2,"history":0.2}' if cat in ("CAP", "ITH") else "", delivery_terms="for_destination",
                   payment_terms_proposed="", terms_version="1", status="sent", sent_at=ts(sent))
        self.add("rfq", rfq, created)
        for n, r in enumerate(lines, 1):
            it = self.items[r["item_code"]]
            self.add("rfq_line", dict(rfq_no=rfq_no, line_no=n, request_no=r["request_no"], request_line_no=r["line_no"], item_code=it["code"],
                                      description=it["name"], attributes=it["attributes"], qty=r["qty"], uom=r["uom"], need_by=r["need_by"]), created)
        rs_rows = {}

        def solicit(sups, sent_dt, due_d):
            got = []
            for s in sups:
                tr = self.struth[s["code"]]
                p_resp = {"reliable": 0.95, "average": 0.8, "poor": 0.6}[tr["profile"]]
                declined = R.bern(0.05)
                reminders = 0
                resp_dt = None
                if R.bern(p_resp):
                    resp_dt = self.elapsed_after(sent_dt, float(tr["reply_hours_mean"]) * R.lognorm(1.0, 0.5) + 6, "HO-PUN")
                    if resp_dt.date() > due_d + timedelta(days=1):
                        reminders = 1
                        resp_dt = None if R.bern(0.5) else resp_dt
                else:
                    reminders = R.choice([1, 2])
                if resp_dt is not None and resp_dt.date() > due_d + timedelta(days=3):
                    resp_dt = None
                row = dict(rfq_no=rfq_no, supplier_code=s["code"], invited_at=ts(sent_dt), reminders_sent=reminders,
                           responded_at=ts(resp_dt) if resp_dt else "", declined=str(bool(resp_dt and declined)).lower(),
                           decline_reason="Not in our product range" if (resp_dt and declined) else "")
                rs_rows[s["code"]] = row
                self.add("rfq_supplier", row, sent_dt)
                self.email("out", s, buyer, sent_dt, rfq_no, "rfq", "rfq_send")
                if resp_dt and declined:
                    self.email("in", s, buyer, resp_dt, rfq_no, "rfq", "decline_rfq")
                elif resp_dt:
                    got.append((s, resp_dt))
                    self.email("in", s, buyer, resp_dt, rfq_no, "rfq", "quote_cover", attach=True)
                    if R.bern(0.06):
                        self.email("in", s, buyer, sent_dt + timedelta(hours=R.uniform(3, 30)), rfq_no, "rfq", "question")
                    elif R.bern(0.04):
                        self.email("in", s, buyer, sent_dt + timedelta(hours=R.uniform(3, 30)), rfq_no, "rfq", "extension_request")
            return got

        quotes = solicit(invited, sent, due)
        if len(quotes) < min(min_q, len(cand)):
            rest = [s for s in cand if s not in invited]
            if rest:
                extra = pick(rest, min_q - len(quotes) + 1)
                sent2 = self.work_dt(due + timedelta(days=1), "HO-PUN")
                due = self.cal.add_working(sent2.date(), 4, "HO-PUN")
                quotes += solicit(extra, sent2, due)
        if not quotes:
            # nobody replied in writing: the buyer phones the most reliable approved supplier, who confirms a quote by email
            s = max(cand, key=lambda x: {"reliable": 3, "average": 2, "poor": 1}[self.struth[x["code"]]["profile"]])
            resp_dt = self.work_dt(due + timedelta(days=R.choice([1, 2])), "HO-PUN")
            if s["code"] in rs_rows:
                rs_rows[s["code"]]["responded_at"] = ts(resp_dt)
            else:
                self.add("rfq_supplier", dict(rfq_no=rfq_no, supplier_code=s["code"], invited_at=ts(sent), reminders_sent=2, responded_at=ts(resp_dt),
                                              declined="false", decline_reason=""), sent)
            quotes = [(s, resp_dt)]
            self.email("in", s, buyer, resp_dt, rfq_no, "rfq", "quote_cover", attach=True)
        qrows = []
        for qi, (s, resp_dt) in enumerate(quotes, 1):
            tr = self.struth[s["code"]]
            qno = f"QT-{rfq_no[4:]}-{qi}"
            lead_mult = float(tr["lead_mult"])
            qlines = []
            for n, r in enumerate(lines, 1):
                it = self.items[r["item_code"]]
                base = self.pb.market(it["code"], resp_dt.date()) * self.pb.supplier_mult(s["code"], resp_dt.date()) * D(s["price_level"])
                noise = R.lognorm(1.0, 0.04)
                if R.bern(0.03):
                    noise *= R.choice([0.72, 1.4])
                price = q2(base * D(noise) * (D(1) - D("0.01") * D(min(5, int(math.log10(max(1, float(r["_qty"])) + 1) * 2)))))
                dd = max(1, int(round(int(it["default_lead_days"]) * lead_mult * R.lognorm(1.0, 0.15))))
                rate = self.gst_rate(it["code"], resp_dt.date()) if (s["gst_reg_type"] == "regular" and not self.is_rcm(it, s)) else D(0)
                qlines.append(dict(quote_no=qno, rfq_line_no=n, item_code=it["code"], offered_brand=it["brand"], qty=r["qty"], moq=it["moq"],
                                   unit_price=money(price), discount_pct="0.00", gst_rate=str(rate), delivery_days=dd, deviation="", _price=price, _dd=dd))
            qrows.append((s, resp_dt, qno, qlines))
            self.add("quote", dict(no=qno, rfq_no=rfq_no, supplier_code=s["code"], revision=1, received_at=ts(resp_dt),
                                   valid_until=iso(resp_dt.date() + timedelta(days=R.choice([15, 30]))), payment_terms_code=s["payment_terms_code"],
                                   delivery_terms="for_destination", packing_forwarding="0.00", freight="0.00", taxes_extra="true", warranty_months="",
                                   deviations="", document_ref=""), resp_dt)
            for ql in qlines:
                self.add("quote_line", {k: v for k, v in ql.items() if not k.startswith("_")}, resp_dt)
        last_resp = max(q[1] for q in qrows)
        award_dt = self.work_dt((due + timedelta(days=R.choice([0, 1, 2, 3]))), "HO-PUN")
        award_dt = max(award_dt, self.work_hours_after(last_resp, 4, "HO-PUN"))
        # who wins each line (score: price, delivery, history, with a small penalty for giving one supplier everything)
        winners = []
        for n, r in enumerate(lines, 1):
            offers = [(s, qno, [x for x in ql if x["rfq_line_no"] == n][0]) for s, _, qno, ql in qrows]
            minp = min(o[2]["_price"] for o in offers)
            mind = min(o[2]["_dd"] for o in offers)
            tot = sum(v for (cc_, _), v in self.award_count.items() if cc_ == cat) + 1

            def score(o):
                prof = {"reliable": 1.0, "average": 0.75, "poor": 0.45}[self.struth[o[0]["code"]]["profile"]]
                share = self.award_count.get((cat, o[0]["code"]), 0) / tot
                return 0.6 * float(minp / o[2]["_price"]) + 0.2 * (mind / o[2]["_dd"]) + 0.2 * prof - 0.25 * share
            offers.sort(key=score, reverse=True)
            over = len(offers) > 1 and R.bern(0.10)
            pick1 = offers[1] if over else offers[0]
            winners.append((n, r, pick1, offers, over))
            self.award_count[(cat, pick1[0]["code"])] = self.award_count.get((cat, pick1[0]["code"]), 0) + 1
        # negotiation: some winners give a better price in a second round (quote revision 2)
        revised = {}
        for sc in sorted({w[2][0]["code"] for w in winners}):
            if R.bern(0.25):
                s, resp_dt, qno, ql = next(q for q in qrows if q[0]["code"] == sc)
                neg_dt = max(self.work_hours_after(last_resp, 2, "HO-PUN"), resp_dt)
                neg_dt = min(neg_dt, award_dt - timedelta(minutes=30)) if award_dt - timedelta(minutes=30) > resp_dt else resp_dt + timedelta(minutes=30)
                f = D(R.uniform(0.95, 0.985))
                rno = f"{qno}-R2"
                revised[sc] = (rno, f, {x["rfq_line_no"]: q2(x["_price"] * f) for x in ql})
                self.add("quote", dict(no=rno, rfq_no=rfq_no, supplier_code=sc, revision=2, received_at=ts(neg_dt), valid_until=iso(neg_dt.date() + timedelta(days=15)),
                                       payment_terms_code=s["payment_terms_code"], delivery_terms="for_destination", packing_forwarding="0.00", freight="0.00",
                                       taxes_extra="true", warranty_months="", deviations="Revised after negotiation", document_ref=""), neg_dt)
                for x in ql:
                    self.add("quote_line", {**{k: v for k, v in x.items() if not k.startswith("_")}, "quote_no": rno, "unit_price": money(revised[sc][2][x["rfq_line_no"]])}, neg_dt)
                self.email("in", s, buyer, neg_dt, rfq_no, "rfq", "quote_cover", attach=True)
        award_by_sup = defaultdict(list)
        for n, r, (s, qno, ql), offers, over in winners:
            price, qn = ql["_price"], qno
            if s["code"] in revised:
                qn, price = revised[s["code"]][0], revised[s["code"]][2][n]
            reason = "Better delivery and supplier history" if over else ("Price improved in negotiation" if s["code"] in revised else "Best overall score")
            self.add("award", dict(rfq_no=rfq_no, rfq_line_no=n, supplier_code=s["code"], quote_no=qn, qty=r["qty"], price=money(price),
                                   reason=reason, overridden=str(over).lower(), by_user=buyer, at=ts(award_dt)), award_dt)
            first_offer = max(o[2]["_price"] for o in offers)
            self.add("_saving", dict(rfq_no=rfq_no, line=n, first=first_offer, awarded=price, qty=D(r["qty"])), award_dt)
            award_by_sup[s["code"]].append(dict(req_row=r, price=price, promised_days=ql["_dd"], contract=""))
        rfq["status"] = "awarded"
        for sup, ls in award_by_sup.items():
            when = self.cal.next_working((award_dt + timedelta(days=R.choice([0, 1, 1, 2]))).date(), "HO-PUN")
            self.make_po(sup, ls, when, loc, buyer, req["no"], "capex" if req["type"] == "capex" else "standard", req["project_code"], rfq_no=rfq_no,
                         not_before=award_dt)

    def email(self, direction, supplier, user, when, doc_no, doc_type, kind, attach=False, mailbox="purchase@cbcpl.example"):
        s = supplier
        dom = s["trade_name"].lower().replace(" ", "").replace("&", "") + ".example"
        mail = lambda p: f"{p}@{dom}"
        buyer = f"{self.users[user]['name'].split()[0].lower()}.{self.users[user]['name'].split()[-1].lower()}@cbcpl.example"
        n = len(self.T["email_message"]) + 1
        self.add("email_message", dict(id=f"EML-{n:06d}", message_id=f"<{hashlib.md5(f'{n}{doc_no}'.encode()).hexdigest()[:16]}@mail.example>",
                                       in_reply_to="", thread_id=f"THR-{doc_type}-{doc_no}", mailbox=mailbox, direction=direction,
                                       from_addr=buyer if direction == "out" else mail("sales" if doc_type != "invoice" else "accounts"),
                                       to_addr=mail("sales") if direction == "out" else (buyer if doc_type != "invoice" else mailbox),
                                       subject="", body_ref="", attachments="1" if attach else "0", at=ts(when), plus_tag=f"{doc_type}-{doc_no}",
                                       linked_doc_type=doc_type, linked_doc_no=doc_no, kind=kind, style=s.get("style", ""), supplier_code=s["code"]), when)
