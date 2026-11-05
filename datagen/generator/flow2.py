"""Purchase orders, receipts, invoices and planted problems (mixin for the simulation)."""
import hashlib
import math
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP

from common import CUTOFF, D, fy, fy4, iso, money, q2, q3, qty_s, ts
from simulate import dt_at
from flow import STORE_USER

RCM_HSN = {"996511", "998211", "998212"}      # GTA and legal services: recipient pays when the supplier is not a body corporate


class PoMixin:
    def supplier_addr(self, sup_code, buyer_state):
        addrs = [a for a in self.t["supplier_address"] if a["supplier_code"] == sup_code]
        for a in addrs:
            if a["state_code"] == buyer_state and a["gstin"]:
                return a
        return addrs[0]

    def is_rcm(self, item, s):
        return item["hsn_sac"] in RCM_HSN and s["legal_form"] in ("proprietorship", "partnership") or \
            (item["hsn_sac"] == "998211" and s["legal_form"] != "pvt_ltd")

    # ------------------------------------------------------------------ PO
    def make_po(self, sup_code, lines, when, loc, buyer, req_no, po_type, project, rfq_no="", service_period=None, not_before=None):
        R = self.R
        s = self.sup[sup_code]
        tr = self.struth[sup_code]
        created = self.work_dt(when, "HO-PUN")
        if not_before is not None and created <= not_before + timedelta(minutes=20):
            created = self.work_hours_after(not_before, 0.5, "HO-PUN")
        if created.date() > self.end:
            return None
        po_no = self.num.next("po", created.date(), loc)
        buyer_gstin = self.loc[loc]["gstin"]
        addr = self.supplier_addr(sup_code, buyer_gstin[:2])
        sup_gstin = addr["gstin"]
        intra = addr["state_code"] == buyer_gstin[:2]
        reg = s["gst_reg_type"]
        tl, tc, ts_, ti = D(0), D(0), D(0), D(0)
        plines = []
        for n, ln in enumerate(lines, 1):
            r = ln["req_row"]
            it = self.items[r["item_code"]]
            qty = D(r["qty"]) if "qty" in r else D(r["_qty"])
            rate = self.gst_rate(it["code"], created.date()) if reg == "regular" and not self.is_rcm(it, s) else D(0)
            taxable = q2(ln["price"] * qty)
            c, sg, ig = self.tax(taxable, rate, intra)
            tl += taxable; tc += c; ts_ += sg; ti += ig
            plines.append(dict(po_no=po_no, line_no=n, request_no=r.get("request_no", ""), request_line_no=r.get("line_no", ""), item_code=it["code"],
                               description=it["name"], qty=qty_s(qty), uom=it["purchase_uom"], unit_price=money(ln["price"]), gst_rate=str(rate),
                               cgst=money(c), sgst=money(sg), igst=money(ig), need_by=r.get("need_by", ""), promised_date="", received_qty="0.000",
                               invoiced_qty="0.000", cost_centre_code=r.get("cost_centre_code", ""), gl_code=r.get("gl_code", ""), status="open",
                               _qty=qty, _price=ln["price"], _days=ln["promised_days"], _item=it, _taxable=taxable, _rcm=self.is_rcm(it, s)))
        charges = []
        goods_l = [pl for pl in plines if pl["_item"]["kind"] == "goods"]
        if goods_l and tl > 0:
            cat0 = goods_l[0]["_item"]["category_code"]
            crate = D(goods_l[0]["gst_rate"])
            if cat0 in ("MRO", "ELE", "TLS", "OFC", "CHM", "PPE") and R.bern(0.25):
                charges.append(("freight", max(D(500), D(round(float(tl) * R.uniform(0.008, 0.02) / 10)) * 10), ""))
            if cat0 in ("MRO", "ELE") and R.bern(0.20):
                charges.append(("packing_forwarding", q2(tl * D(R.uniform(0.004, 0.01))), ""))
            if cat0 == "CAP" and R.bern(0.6):
                charges.append(("installation", q2(tl * D(R.uniform(0.02, 0.05))), "998739"))
            for ctype, camt, csac in charges:
                c, sg, ig = self.tax(camt, crate, intra)
                tl += camt; tc += c; ts_ += sg; ti += ig
        total = tl + tc + ts_ + ti
        spend = "capex" if po_type == "capex" else "opex"
        appr, st = self.approve("po", po_no, tl, spend, buyer, created, "HO-PUN", force_ok=True)
        sent = self.work_hours_after(appr, R.uniform(1, 4), "HO-PUN")
        for ln, pl in zip(lines, plines):
            if ln.get("contract"):
                self.contract_used[ln["contract"]] += pl["_taxable"]
        terms = s["payment_terms_code"]
        po = dict(no=po_no, po_type=po_type, supplier_code=sup_code, supplier_gstin=sup_gstin, buyer_gstin=buyer_gstin, ship_to_location=loc,
                  buyer=buyer, project_code=project, contract_code=lines[0].get("contract", ""), created_at=ts(created), approved_at=ts(appr),
                  sent_at=ts(sent), payment_terms_code=terms, advance_pct="30" if terms == "ADV30N30" else "0", delivery_terms="for_destination", freight_terms="extra" if any(c[0] == "freight" for c in charges) else "included",
                  terms_version="1", version=1, taxable=money(tl), cgst=money(tc), sgst=money(ts_), igst=money(ti), total=money(total),
                  status="sent", cancel_reason="", _lines=plines, _rfq=rfq_no, _req=req_no, _charges=charges, _charges_in="")
        self.add("po", po, created)
        for pl in plines:
            self.add("po_line", pl, created)
        for ctype, camt, csac in charges:
            self.add("po_charge", dict(po_no=po_no, type=ctype, amount=money(camt), gst_rate=str(crate), sac=csac), created)
        self.email("out", s, buyer, sent, po_no, "po", "po_send", attach=True)
        # supplier acknowledgement
        ack = self.elapsed_after(sent, float(tr["reply_hours_mean"]) * R.lognorm(1.0, 0.6) + 2, "HO-PUN")
        ack_kind = R.weighted(["ack", "date_change", "qty_change", "price_change", "reject"], [80, 12, 4, 3, 1])
        no_ack = ack_kind == "ack" and R.bern({"reliable": 0.02, "average": 0.08, "poor": 0.22}[tr["profile"]])
        slip = 0
        if ack_kind == "reject":
            po["status"] = "cancelled"
            po["cancel_reason"] = "Supplier unable to accept"
            self.add("po_event", dict(po_no=po_no, type="rejected", at=ts(ack), field="", old_value="", new_value="", email_ref="", applied_by="buyer"), ack)
            self.email("in", s, buyer, ack, po_no, "po", "reject")
            self.case("supplier_reject_po", po_no, "Supplier declined the purchase order", "Cancel PO, re-source the lines", ack)
            self.resource_after_reject(po, plines, s, loc, buyer, ack, project, req_no)
            return po
        if ack_kind == "date_change":
            slip = R.choice([2, 3, 4, 6, 8, 10])
            self.add("po_event", dict(po_no=po_no, type="date_change", at=ts(ack), field="promised_date", old_value="", new_value=f"+{slip}d", email_ref="",
                                      applied_by="agent" if slip <= 3 else "buyer"), ack)
            self.email("in", s, buyer, ack, po_no, "po", "date_change")
            self.case("supplier_date_change", po_no, f"Supplier asked for +{slip} days", "Auto-accept if within 3 days else buyer decides", ack)
        elif no_ack:
            ack = dt_at(self.cal.add_working(sent.date(), 2, "HO-PUN"), 12)
            self.case("po_not_acknowledged", po_no, "Supplier never acknowledged the PO", "Chase the supplier; the order stays 'sent' until goods arrive", sent)
        else:
            self.add("po_event", dict(po_no=po_no, type="acknowledged", at=ts(ack), field="", old_value="", new_value="", email_ref="", applied_by="agent"), ack)
            self.email("in", s, buyer, ack, po_no, "po", ack_kind if ack_kind != "ack" else "ack")
        po["status"] = "sent" if no_ack else "acknowledged"
        for pl in plines:
            pd = ack.date() + timedelta(days=int(pl["_days"]) + slip)
            pl["promised_date"] = iso(pd)
        goods = [pl for pl in plines if pl["_item"]["kind"] == "goods"]
        svc = [pl for pl in plines if pl["_item"]["kind"] == "service"]
        if goods:
            gmax = max(date.fromisoformat(pl["promised_date"]) for pl in goods)
            for pl in goods:
                pl["promised_date"] = iso(gmax)
        sched_n = 1
        if goods and R.bern(0.30) and any(pl["_item"]["category_code"] == "RAW" and pl["_qty"] >= 10 for pl in goods):
            sched_n = R.choice([2, 3])
        for pl in plines:
            pd0 = date.fromisoformat(pl["promised_date"])
            if sched_n > 1 and pl in goods:
                q = pl["_qty"]
                base_q = (q / sched_n).to_integral_value(rounding="ROUND_FLOOR")
                parts = [base_q] * (sched_n - 1) + [q - base_q * (sched_n - 1)]
                pl["_sched"] = [(pd0 + timedelta(days=14 * k), parts[k]) for k in range(sched_n)]
            else:
                pl["_sched"] = [(pd0, pl["_qty"])]
            for dd, qq in pl["_sched"]:
                self.add("po_schedule", dict(po_no=po_no, line_no=pl["line_no"], delivery_date=iso(dd), qty=qty_s(qq)), created)
        if po["advance_pct"] == "30":
            adv_ready = self.work_hours_after(ack, R.uniform(8, 40), "HO-PUN")
            self.add("_adv", dict(po_no=po_no, supplier_code=sup_code, gross=q2(total * D("0.30")), taxable=q2(tl * D("0.30")), po_taxable=tl, ready_dt=adv_ready,
                                  category=plines[0]["_item"]["category_code"], hsn=plines[0]["_item"]["hsn_sac"]), adv_ready)
        if goods:
            self.deliver_goods(po, goods, s, loc, buyer, ack)
        if svc:
            self.deliver_services(po, svc, s, loc, buyer, ack, service_period)
        return po

    # ------------------------------------------------------------------ goods receipt
    def deliver_goods(self, po, glines, s, loc, buyer, ack):
        R = self.R
        tr = self.struth[s["code"]]
        promised = max(date.fromisoformat(pl["promised_date"]) for pl in glines)
        late = not R.bern(float(tr["on_time_prob"]))
        delay = 0
        if late:
            delay = int(R.uniform(2, 14) * (1.8 if tr["profile"] == "poor" else 1.0) * (1.15 if promised.month in (6, 7, 8, 9) else 1.0))
        elif R.bern(0.25):
            delay = -R.choice([1, 1, 2])
        arrival = promised + timedelta(days=delay)
        arrival = max(arrival, ack.date() + timedelta(days=1))
        planned = any(len(pl["_sched"]) > 1 for pl in glines)
        feasible = len(glines) >= 2 or glines[0]["_qty"] >= 4
        part = (not planned) and feasible and R.bern(min(0.7, float(tr["part_delivery_prob"]) * 1.8 + 0.02))
        consign = []
        if planned:
            for k in range(max(len(pl["_sched"]) for pl in glines)):
                items_k = [(pl, pl["_sched"][k][1]) for pl in glines if len(pl["_sched"]) > k]
                consign.append((arrival + timedelta(days=14 * k + R.randint(-1, 3)), items_k))
            self.case("scheduled_delivery", po["no"], f"PO has {len(consign)} agreed delivery dates", "PO stays open until the last drop; invoice per delivery", arrival)
        elif part and len(glines) >= 2:
            k = max(1, int(len(glines) * R.uniform(0.4, 0.7)))
            idx = list(range(len(glines)))
            R.shuffle(idx)
            first = [(glines[i], glines[i]["_qty"]) for i in idx[:k]]
            second = [(glines[i], glines[i]["_qty"]) for i in idx[k:]]
            consign = [(arrival, first), (arrival + timedelta(days=R.randint(4, 15)), second)]
        elif part and glines[0]["_qty"] >= 4:
            pl = glines[0]
            f = D(R.uniform(0.45, 0.8))
            q1 = max(D(1), (pl["_qty"] * f).quantize(D("1")))
            consign = [(arrival, [(pl, q1)] + [(g, g["_qty"]) for g in glines[1:]]), (arrival + timedelta(days=R.randint(5, 20)), [(pl, pl["_qty"] - q1)])]
        else:
            consign = [(arrival, [(g, g["_qty"]) for g in glines])]
        if late and delay > 3:
            self.case("late_delivery", po["no"], f"Delivered {delay} days after the promised date", "Delay flagged, supplier OTIF reduced",
                      arrival)
            if R.bern(0.35):
                self.email("in", s, buyer, dt_at(promised - timedelta(days=R.randint(0, 2)), 11), po["no"], "po", "delay")
        if len(consign) > 1 and not planned:
            self.case("part_delivery", po["no"], "PO delivered in two consignments", "Open balance stays; each invoice matches received qty only", arrival)
            self.email("in", s, buyer, dt_at(consign[0][0], 12), po["no"], "po", "partial_dispatch", attach=True)
        if len(consign) > 1 and planned:
            self.email("in", s, buyer, dt_at(consign[0][0], 12), po["no"], "po", "partial_dispatch", attach=True)
        for ci, (adate, items) in enumerate(consign, 1):
            self.grn(po, items, s, loc, adate, ci)

    def grn(self, po, items, s, loc, arrival_date, seq):
        R = self.R
        tr = self.struth[s["code"]]
        gdate = self.cal.next_working(arrival_date, loc)
        if gdate > self.end + timedelta(days=60):
            return
        no = self.num.next("goods_receipt", gdate, loc)
        insp = items[0][0]["_item"]["inspection_required"] == "true"
        failed = insp and R.bern(float(tr["rejection_rate"]) * 0.5)
        rh = self.loc[loc]["receiving_hours"].split("-")
        lo_h, hi_h = int(rh[0].split(":")[0]) + 0.25, int(rh[1].split(":")[0]) - 0.5
        gate = dt_at(gdate, R.uniform(lo_h, hi_h))
        total_val = sum(q * pl["_price"] * (1 + D(pl["gst_rate"]) / 100) for pl, q in items)
        eway = "".join(R.choice("0123456789") for _ in range(12)) if total_val > 50000 else ""
        accepted_on = gdate if not insp else self.cal.add_working(gdate, R.choice([0, 1, 1, 2, 3]), loc)
        grn = dict(no=no, po_no=po["no"], location_code=loc, gate_entry_at=ts(gate), received_by=STORE_USER.get(loc, "U021"),
                   delivery_challan_no=f"DC/{R.randint(1000, 9999)}", supplier_invoice_ref="", eway_bill_no=eway, vehicle_no=self.vehicle(),
                   inspection_status="not_required" if not insp else ("failed" if failed else "passed"), accepted_on=iso(accepted_on), remarks="")
        self.add("goods_receipt", grn, gate)
        recv_lines = []
        for pl, q in items:
            rej = D(0)
            if failed:
                rej = max(D(1), (q * D(R.uniform(0.1, 0.4))).quantize(D("1")))
            elif R.bern(float(tr["rejection_rate"])) and q >= 3:
                rej = max(D(1), (q * D(R.uniform(0.03, 0.12))).quantize(D("1")))
            rej = min(rej, q)
            acc = q - rej
            self.add("gr_line", dict(gr_no=no, po_no=po["no"], po_line_no=pl["line_no"], qty_received=qty_s(q), qty_accepted=qty_s(acc),
                                     qty_rejected=qty_s(rej), reject_reason="Quality: out of specification" if rej else ""), gate)
            if rej > 0 and R.bern(0.9):
                rno = self.num.next("return_to_vendor", gdate, loc)
                self.dn_n = getattr(self, "dn_n", 0) + 1
                rdt = dt_at(self.cal.add_working(gdate, R.choice([1, 2, 3]), loc), 11)
                self.add("return_to_vendor", dict(no=rno, gr_no=no, item_code=pl["item_code"], qty=qty_s(rej), reason="Rejected at inspection",
                                                  debit_note_no=f"DN-{fy4(gdate)}-{self.dn_n:04d}", at=ts(rdt)), rdt)
            pl["received_qty"] = qty_s(D(pl["received_qty"]) + acc)
            recv_lines.append((pl, q, acc, rej))
            it = pl["_item"]
            if it["stocked"] == "true" and (it["code"], loc) in self.inv:
                base = acc * D(it["pack_size"])
                self.add("stock_movement", dict(id="", item_code=it["code"], location_code=loc, date=iso(gdate), type="receipt", qty=qty_s(base),
                                                unit_cost=money(pl["_price"] / D(it["pack_size"])), cost_centre_code="", ref_doc=no), gate)
                self.inbound[(it["code"], loc)].append([gdate, base])
        self.make_invoice(po, recv_lines, s, loc, gdate, accepted_on, grn, kind="goods")

    def resource_after_reject(self, po, plines, s, loc, buyer, ack, project, req_no):
        """The supplier declined the PO: the buyer places the same lines with another approved supplier."""
        R = self.R
        cats = {pl["_item"]["category_code"] for pl in plines}
        cand = [x for x in self.t["supplier"] if x["code"] != s["code"] and all(c in self.sup_cats[x["code"]] for c in cats)
                and self.supplier_active(x["code"], ack.date()) and self.can_supply(x, loc)
                and self.supplier_has_valid_docs(x["code"], self.sup_cats[x["code"]], ack.date())]
        if not cand:
            return
        alt = max(cand, key=lambda x: {"reliable": 3, "average": 2, "poor": 1}[self.struth[x["code"]]["profile"]] + R.random())
        when = self.cal.next_working(ack.date() + timedelta(days=2), "HO-PUN")
        lines = []
        for pl in plines:
            price = q2(self.pb.market(pl["item_code"], when) * self.pb.supplier_mult(alt["code"], when) * D(alt["price_level"]))
            lines.append(dict(req_row=dict(item_code=pl["item_code"], qty=pl["qty"], request_no=pl["request_no"], line_no=pl["request_line_no"],
                                           need_by=pl["need_by"], cost_centre_code=pl["cost_centre_code"], gl_code=pl["gl_code"]),
                              price=price, promised_days=int(pl["_days"]), contract=""))
        self.make_po(alt["code"], lines, when, loc, buyer, req_no, "standard", project, not_before=ack)

    def vehicle(self):
        R = self.R
        return f"{R.choice(['MH', 'GJ', 'KA'])}{R.randint(1, 49):02d}{R.choice('ABCDEFGH')}{R.choice('ABCDEFGH')}{R.randint(1000, 9999)}"

    # ------------------------------------------------------------------ services
    def deliver_services(self, po, slines, s, loc, buyer, ack, period):
        R = self.R
        tr = self.struth[s["code"]]
        for pl in slines:
            promised = date.fromisoformat(pl["promised_date"])
            if period:
                d = period[1]
            else:
                d = promised + timedelta(days=int(R.uniform(0, 4) * (2 if tr["profile"] == "poor" else 1)))
            d = self.cal.next_working(d, loc)
            no = self.num.next("service_receipt", d, loc)
            acc = d + timedelta(days=R.choice([0, 1, 1, 2, 3]))
            acc = self.cal.next_working(acc, loc)
            req_user = self.service_requester(po)
            self.add("service_receipt", dict(no=no, po_no=po["no"], po_line_no=pl["line_no"], period_from=iso(period[0] if period else d),
                                             period_to=iso(period[1] if period else d), description=pl["description"],
                                             amount_accepted=money(pl["_taxable"]), accepted_by=req_user, accepted_on=iso(acc)), dt_at(acc, 15))
            pl["received_qty"] = pl["qty"]
            self.make_invoice(po, [(pl, pl["_qty"], pl["_qty"], D(0))], s, loc, d, acc, {"no": no, "eway_bill_no": ""}, kind="service")

    def service_requester(self, po):
        return "U021" if po["ship_to_location"] == "HO-PUN" else "U014" if po["ship_to_location"] == "PLT-VAD" else "U023"

    # ------------------------------------------------------------------ invoices
    def next_sup_inv_no(self, sup_code, d):
        key = (sup_code, fy(d) if self.fy_reset[sup_code] else "all")
        if key not in self.sup_inv_seq:
            self.sup_inv_seq[key] = (stable_hash_small(sup_code + str(key)) % 40) + 1 if not self.fy_reset[sup_code] else 1 + stable_hash_small(sup_code + fy(d)) % 15
        else:
            self.sup_inv_seq[key] += 1
        seq = self.sup_inv_seq[key]
        p = self.sup_prefix[sup_code]
        return f"{p}/{seq:04d}" if self.fy_reset[sup_code] else f"{p}/{fy4(d)}/{seq:04d}"

    def make_invoice(self, po, recv_lines, s, loc, gdate, accepted_on, grn, kind):
        """recv_lines: [(po_line, qty_received, qty_accepted, qty_rejected)]. Adds the invoice(s) and the finance hand-off."""
        R = self.R
        tr = self.struth[s["code"]]
        lag = {"reliable": (0, 3), "average": (1, 8), "poor": (3, 18)}[tr["profile"]]
        inv_date = gdate - timedelta(days=R.choice([0, 0, 1])) if kind == "goods" else gdate + timedelta(days=R.randint(0, 3))
        if kind == "service" and po["po_type"] == "contract_release":
            inv_date = gdate + timedelta(days=R.randint(0, 4))
        received = self.work_dt(inv_date + timedelta(days=R.randint(*lag)), "HO-PUN")
        received = max(received, self.work_dt(gdate, "HO-PUN") if kind == "service" else received)
        if received.date() > self.end:
            return
        buyer_gstin = po["buyer_gstin"]
        sup_gstin = po["supplier_gstin"]
        planted = None
        # decide planted problems (mutually exclusive, in priority order)
        has_rej = any(rej > 0 for _, _, _, rej in recv_lines)
        roll = R.random()
        if has_rej and R.bern(0.7):
            planted = "rejected_qty_invoiced"
        elif roll < 0.06:
            planted = "price_mismatch"
        elif roll < 0.10:
            planted = "qty_mismatch"
        elif roll < 0.11:
            planted = "wrong_gstin"
        elif s["gst_reg_type"] == "composition" and R.bern(0.12):
            planted = "composition_tax"
        elif s["e_invoice_applicable"] == "true" and R.bern(0.03):
            planted = "e_invoice_missing"
        qty_diff = D(0)
        lines_out = []
        intra = po["supplier_gstin"][:2] == buyer_gstin[:2] if po["supplier_gstin"] else True
        reg = s["gst_reg_type"]
        price_delta = D(0)
        if planted == "price_mismatch":
            price_delta = D(R.choice([0.01, 0.015, 0.03, 0.05, 0.08, 0.12]))
        tl = tc = tsg = ti = D(0)
        for n, (pl, q, acc, rej) in enumerate(recv_lines, 1):
            it = pl["_item"]
            qty = q if (planted == "rejected_qty_invoiced" or kind == "service") else acc
            if planted == "qty_mismatch" and kind == "goods":
                extra = max(D(1), (qty * D(R.uniform(0.05, 0.2))).quantize(D("1")))
                qty = qty + extra
                qty_diff += extra
            price = q2(pl["_price"] * (1 + price_delta)) if planted == "price_mismatch" else pl["_price"]
            rate = self.gst_rate(it["code"], inv_date) if reg == "regular" and not pl["_rcm"] else D(0)
            if planted == "composition_tax":
                rate = self.gst_rate(it["code"], inv_date)
            taxable = q2(price * qty)
            c, sg, ig = self.tax(taxable, rate, intra)
            tl += taxable; tc += c; tsg += sg; ti += ig
            lines_out.append(dict(po_line=pl, qty=qty, price=price, taxable=taxable, rate=rate, c=c, sg=sg, ig=ig, acc=acc, q=q))
        inv_charges = []
        if po.get("_charges") and not po.get("_charges_in") and kind == "goods":
            crate = lines_out[0]["rate"]
            for ctype, camt, csac in po["_charges"]:
                c, sg, ig = self.tax(camt, crate, intra)
                tl += camt; tc += c; tsg += sg; ti += ig
                inv_charges.append((ctype, camt, csac, crate))
        if planted == "wrong_gstin":
            buyer_gstin = R.choice([g["gstin"] for g in self.t["gst_registration"] if g["gstin"] != buyer_gstin])
        total = tl + tc + tsg + ti
        rounded = total.quantize(D("1"), rounding=ROUND_HALF_UP)
        round_off = rounded - total
        no_sup = self.next_sup_inv_no(po["supplier_code"], inv_date)
        if kind == "goods":
            grn["supplier_invoice_ref"] = no_sup
        sup_gstin_inv = sup_gstin
        if planted == "wrong_gstin" and R.bern(0.5) and sup_gstin:
            sup_gstin_inv = sup_gstin[:-1] + ("A" if sup_gstin[-1] != "A" else "B")
        inv_no = self.num.next("invoice_booking", received.date())
        e_inv = s["e_invoice_applicable"] == "true" and reg == "regular" and planted != "e_invoice_missing"
        irn = hashlib.sha256(f"{sup_gstin}{fy(inv_date)}INV{no_sup}".encode()).hexdigest().upper() if e_inv else ""
        eway = grn.get("eway_bill_no", "") if kind == "goods" else ""
        chan = "email" if R.bern(0.85) else "upload"
        inv = dict(no=inv_no, type="invoice", supplier_code=po["supplier_code"], supplier_gstin=sup_gstin_inv, buyer_gstin=buyer_gstin,
                   place_of_supply=buyer_gstin[:2], supplier_invoice_no=no_sup, invoice_date=iso(inv_date), fy=fy(inv_date), irn=irn,
                   ack_no=str(R.randint(10 ** 14, 10 ** 15 - 1)) if irn else "", ack_date=iso(inv_date) if irn else "",
                   signed_qr_ref="", eway_bill_no=eway, reverse_charge=str(any(l["po_line"]["_rcm"] for l in lines_out)).lower(),
                   received_at=ts(received), received_on=iso(received.date()), channel=chan, taxable=money(tl),
                   cgst=money(tc), sgst=money(tsg), igst=money(ti), cess="0.00", round_off=money(round_off), total=money(rounded), ref_invoice_no="",
                   due_date="", payment_block="none", status="received", document_ref="", document_hash="")
        self.add("invoice", inv, received)
        for n, l in enumerate(lines_out, 1):
            pl = l["po_line"]
            self.add("invoice_line", dict(invoice_no=inv_no, line_no=n, po_no=po["no"], po_line_no=pl["line_no"], description=pl["description"],
                                          hsn_sac=pl["_item"]["hsn_sac"], qty=qty_s(l["qty"]), uom=pl["uom"], unit_price=money(l["price"]),
                                          discount="0.00", taxable=money(l["taxable"]), gst_rate=str(l["rate"] if l["rate"] else (self.gst_rate(pl["_item"]["code"], inv_date) if pl["_rcm"] else l["rate"])), cgst=money(l["c"]),
                                          sgst=money(l["sg"]), igst=money(l["ig"])), received)
            pl["invoiced_qty"] = qty_s(D(pl["invoiced_qty"]) + l["qty"])
        for ctype, camt, csac, crate in inv_charges:
            self.add("invoice_charge", dict(invoice_no=inv_no, type=ctype, amount=money(camt), gst_rate=str(crate), sac=csac), received)
        if inv_charges:
            po["_charges_in"] = inv_no
        if chan == "email":
            self.email("in", s, po["buyer"], received, inv_no, "invoice", "invoice_cover", attach=True, mailbox="invoices@cbcpl.example")
        # expected match result (what a correct platform must conclude)
        result, reasons = self.expected_match(po, lines_out, buyer_gstin == po["buyer_gstin"] and True, sup_gstin_inv == sup_gstin)
        if planted in ("composition_tax", "e_invoice_missing", "wrong_gstin"):
            result = "blocked"
            reasons = {"composition_tax": "tax_not_allowed", "e_invoice_missing": "irn_missing", "wrong_gstin": "gstin_mismatch"}[planted]
        if planted and planted != "rejected_qty_invoiced" or (planted == "rejected_qty_invoiced"):
            self.case(planted, f"{inv_no};{po['no']}", self.planted_cause(planted, price_delta, qty_diff), result + (f" ({reasons})" if reasons else ""), received)
        self.add("truth_match_expected", dict(invoice_no=inv_no, result=result, reason_codes=reasons, planted=planted or ""), received)
        ready = self.work_hours_after(received, R.uniform(4, 40), "HO-PUN")
        pay = dict(invoice_no=inv_no, supplier_code=po["supplier_code"], po_no=po["no"], po_taxable=D(po["taxable"]), taxable=tl, total=rounded, tax=tc + tsg + ti, inv_date=inv_date,
                   received_dt=received, accepted_on=accepted_on, ready_dt=ready, block=None, credit=D(0), credit_no="", kind=kind,
                   category=lines_out[0]["po_line"]["_item"]["category_code"], hsn=lines_out[0]["po_line"]["_item"]["hsn_sac"],
                   rcm=any(l["po_line"]["_rcm"] for l in lines_out), grn_no=grn["no"], cancelled=False, loc=loc)
        if result == "blocked" or result == "within_tolerance" and False:
            self.resolve_block(po, inv, inv_no, lines_out, planted, price_delta, qty_diff, s, loc, received, pay, recv_lines, kind, grn, accepted_on)
        elif result == "within_tolerance":
            inv["status"] = "approved"
        else:
            inv["status"] = "approved"
        if inv["status"] != "cancelled" or pay.get("replaced_by"):
            self.add("_pay", pay, received)
        # duplicate re-send
        if inv["status"] == "approved" and R.bern(0.02):
            self.send_duplicate(inv, no_sup, po, s, received, lines_out)
        return inv

    def planted_cause(self, planted, price_delta, qty_diff):
        return {"price_mismatch": f"Invoice price {price_delta * 100:.1f}% above the PO price", "qty_mismatch": f"Invoice quantity {qty_diff} above received quantity",
                "rejected_qty_invoiced": "Invoice includes quantity rejected at goods receipt; credit note due",
                "wrong_gstin": "GSTIN on the invoice does not match the master or the delivery state", "composition_tax": "Composition supplier charged GST",
                "e_invoice_missing": "E-invoice applicable but IRN and QR missing"}[planted]

    def expected_match(self, po, lines_out, gstin_ok, sup_gstin_ok):
        res, reason = "matched", ""
        for l in lines_out:
            pl = l["po_line"]
            cat = pl["_item"]["category_code"]
            tol = {**self.tol["ANY"], **self.tol.get(cat, {})}
            diff = l["price"] - pl["_price"]
            if diff > 0:
                pct_ok = diff <= pl["_price"] * tol["price_pct"] / 100
                abs_ok = diff * l["qty"] <= tol["price_abs"]
                if pct_ok or abs_ok:
                    res = "within_tolerance" if res == "matched" else res
                else:
                    return "blocked", "price_variance"
            if l["qty"] > l["acc"] + (l["acc"] * tol.get("qty_pct", D(0)) / 100):
                return "blocked", "qty_over_received"
        return res, reason

    # ------------------------------------------------------------------ resolving blocked invoices
    def resolve_block(self, po, inv, inv_no, lines_out, planted, price_delta, qty_diff, s, loc, received, pay, recv_lines, kind, grn, accepted_on):
        R = self.R
        inv["status"] = "blocked"
        inv["payment_block"] = "match" if planted in ("price_mismatch", "qty_mismatch", "rejected_qty_invoiced") else "tax"
        delay = self.cal.add_working(received.date(), R.randint(2, 15), "HO-PUN")
        path = R.weighted(["credit", "corrected", "accept"], [50, 30, 20])
        if planted in ("wrong_gstin", "composition_tax", "e_invoice_missing"):
            path = "corrected"
        res_dt = self.work_dt(delay, "HO-PUN")
        pay["block"] = planted
        if path == "credit" and planted in ("price_mismatch", "qty_mismatch", "rejected_qty_invoiced"):
            amt = D(0)
            for l in lines_out:
                if planted == "price_mismatch":
                    amt += (l["price"] - l["po_line"]["_price"]) * l["qty"]
                elif planted == "qty_mismatch":
                    amt += l["po_line"]["_price"] * (l["qty"] - l["acc"])
                else:
                    amt += l["po_line"]["_price"] * (l["q"] - l["acc"])
            self.credit_note(inv, inv_no, amt, lines_out, s, res_dt, pay, reason=planted)
            if res_dt.date() <= self.end:
                inv["status"] = "approved"
                inv["payment_block"] = "none"
            pay["ready_dt"] = res_dt
        elif path == "accept" and planted == "price_mismatch":
            who = "finance_manager" if q2(sum(l["taxable"] for l in lines_out)) < 500000 else "cfo"
            self.add("approval_event", dict(document="price_variance", document_no=inv_no, step=1, approver="U003" if who == "finance_manager" else "U002",
                                            on_behalf_of="", action="approved", at=ts(res_dt), comment="Variance accepted", rule_code="VAR-01"), res_dt)
            if res_dt.date() <= self.end:
                inv["status"] = "approved"
                inv["payment_block"] = "none"
            pay["ready_dt"] = res_dt
        else:
            # supplier issues a corrected invoice; the original is cancelled
            inv["status"] = "cancelled"
            inv["payment_block"] = "match" if inv["payment_block"] == "none" else inv["payment_block"]
            pay["cancelled"] = True
            self.corrected_invoice(po, inv_no, lines_out, s, loc, res_dt, grn, accepted_on, kind, pay)

    def credit_note(self, inv, inv_no, amount_taxable, lines_out, s, when, pay, reason=""):
        if when.date() > self.end + timedelta(days=0):
            when_rec = when
        else:
            when_rec = when
        cn_no = self.num.next("invoice_booking", when.date())
        rate = lines_out[0]["rate"]
        intra = inv["supplier_gstin"][:2] == inv["buyer_gstin"][:2] if inv["supplier_gstin"] else True
        amt = q2(max(amount_taxable, D("0.01")))
        c, sg, ig = self.tax(amt, rate, intra)
        total = (amt + c + sg + ig).quantize(D("1"), rounding=ROUND_HALF_UP)
        no_sup = f"CN/{self.next_sup_inv_no(inv['supplier_code'], when.date()).split('/', 1)[1]}"
        s_ = self.sup[inv["supplier_code"]]
        irn_ = hashlib.sha256(f"{inv['supplier_gstin']}{fy(when.date())}CRN{no_sup}".encode()).hexdigest().upper() \
            if (s_["e_invoice_applicable"] == "true" and s_["gst_reg_type"] == "regular") else ""
        self.add("invoice", dict(no=cn_no, type="credit_note", supplier_code=inv["supplier_code"], supplier_gstin=inv["supplier_gstin"],
                                 buyer_gstin=inv["buyer_gstin"], place_of_supply=inv["place_of_supply"], supplier_invoice_no=no_sup[:16],
                                 invoice_date=iso(when.date()), fy=fy(when.date()), irn=irn_, ack_no=str(self.R.randint(10 ** 14, 10 ** 15 - 1)) if irn_ else "",
                                 ack_date=iso(when.date()) if irn_ else "", signed_qr_ref="", eway_bill_no="",
                                 reverse_charge="false", received_at=ts(when), received_on=iso(when.date()), channel="email", taxable=money(amt),
                                 cgst=money(c), sgst=money(sg), igst=money(ig), cess="0.00", round_off=money(total - (amt + c + sg + ig)),
                                 total=money(total), ref_invoice_no=inv_no, due_date="", payment_block="none", status="approved", document_ref="",
                                 document_hash=""), when)
        self.add("invoice_line", dict(invoice_no=cn_no, line_no=1, po_no=lines_out[0]["po_line"]["po_no"], po_line_no=lines_out[0]["po_line"]["line_no"],
                                      description=f"Credit note: {reason}", hsn_sac=lines_out[0]["po_line"]["_item"]["hsn_sac"], qty="1.000", uom="NOS",
                                      unit_price=money(amt), discount="0.00", taxable=money(amt), gst_rate=str(rate), cgst=money(c), sgst=money(sg),
                                      igst=money(ig)), when)
        pay["credit"] = total
        pay["credit_no"] = cn_no
        self.case("credit_note", f"{cn_no};{inv_no}", f"Credit note raised for {reason}", "Net the credit note against the payment of the invoice", when)

    def corrected_invoice(self, po, orig_no, lines_out, s, loc, when, grn, accepted_on, kind, orig_pay):
        R = self.R
        fix_lines = [(l["po_line"], l["po_line"]["_qty"] if False else l["acc"], l["acc"], D(0)) for l in lines_out]
        saved = self.R
        inv_date = when.date()
        no_sup = self.next_sup_inv_no(po["supplier_code"], inv_date)
        buyer_gstin = po["buyer_gstin"]
        intra = po["supplier_gstin"][:2] == buyer_gstin[:2] if po["supplier_gstin"] else True
        reg = s["gst_reg_type"]
        tl = tc = tsg = ti = D(0)
        rows = []
        for n, l in enumerate(lines_out, 1):
            pl = l["po_line"]
            qty = l["acc"] if kind == "goods" else l["qty"]
            rate = self.gst_rate(pl["_item"]["code"], inv_date) if reg == "regular" and not pl["_rcm"] else D(0)
            taxable = q2(pl["_price"] * qty)
            c, sg, ig = self.tax(taxable, rate, intra)
            tl += taxable; tc += c; tsg += sg; ti += ig
            rows.append((n, pl, qty, taxable, rate, c, sg, ig))
        corr_charges = []
        if po.get("_charges") and po.get("_charges_in") == orig_no and rows:
            crate = rows[0][4]
            for ctype, camt, csac in po["_charges"]:
                c, sg, ig = self.tax(camt, crate, intra)
                tl += camt; tc += c; tsg += sg; ti += ig
                corr_charges.append((ctype, camt, csac, crate))
        total = tl + tc + tsg + ti
        rounded = total.quantize(D("1"), rounding=ROUND_HALF_UP)
        no = self.num.next("invoice_booking", when.date())
        e_inv = s["e_invoice_applicable"] == "true" and reg == "regular"
        irn = hashlib.sha256(f"{po['supplier_gstin']}{fy(inv_date)}INV{no_sup}".encode()).hexdigest().upper() if e_inv else ""
        self.add("invoice", dict(no=no, type="invoice", supplier_code=po["supplier_code"], supplier_gstin=po["supplier_gstin"], buyer_gstin=buyer_gstin,
                                 place_of_supply=buyer_gstin[:2], supplier_invoice_no=no_sup, invoice_date=iso(inv_date), fy=fy(inv_date), irn=irn,
                                 ack_no=str(R.randint(10 ** 14, 10 ** 15 - 1)) if irn else "", ack_date=iso(inv_date) if irn else "", signed_qr_ref="",
                                 eway_bill_no=grn.get("eway_bill_no", "") if kind == "goods" else "", reverse_charge=str(any(r[1]["_rcm"] for r in rows)).lower(), received_at=ts(when),
                                 received_on=iso(when.date()), channel="email", taxable=money(tl), cgst=money(tc), sgst=money(tsg), igst=money(ti),
                                 cess="0.00", round_off=money(rounded - total), total=money(rounded), ref_invoice_no=orig_no, due_date="",
                                 payment_block="none", status="approved", document_ref="", document_hash=""), when)
        for n, pl, qty, taxable, rate, c, sg, ig in rows:
            self.add("invoice_line", dict(invoice_no=no, line_no=n, po_no=po["no"], po_line_no=pl["line_no"], description=pl["description"],
                                          hsn_sac=pl["_item"]["hsn_sac"], qty=qty_s(qty), uom=pl["uom"], unit_price=money(pl["_price"]), discount="0.00",
                                          taxable=money(taxable), gst_rate=str(rate), cgst=money(c), sgst=money(sg), igst=money(ig)), when)
        for ctype, camt, csac, crate in corr_charges:
            self.add("invoice_charge", dict(invoice_no=no, type=ctype, amount=money(camt), gst_rate=str(crate), sac=csac), when)
        if corr_charges:
            po["_charges_in"] = no
        self.add("truth_match_expected", dict(invoice_no=no, result="matched", reason_codes="", planted="corrected_invoice"), when)
        pay2 = dict(invoice_no=no, supplier_code=po["supplier_code"], po_no=po["no"], po_taxable=D(po["taxable"]), taxable=tl, total=rounded, tax=tc + tsg + ti, inv_date=inv_date,
                    received_dt=when, accepted_on=accepted_on, ready_dt=self.work_hours_after(when, 6, "HO-PUN"), block=None, credit=D(0), credit_no="",
                    kind=kind, category=orig_pay["category"], hsn=orig_pay["hsn"], rcm=orig_pay["rcm"], grn_no=orig_pay["grn_no"], cancelled=False,
                    loc=loc)
        self.add("_pay", pay2, when)
        self.case("corrected_invoice", f"{no};{orig_no}", "Original invoice cancelled and replaced by a corrected one", "Pay only the corrected invoice", when)

    def send_duplicate(self, inv, no_sup, po, s, received, lines_out):
        R = self.R
        when = self.work_dt(received.date() + timedelta(days=R.randint(3, 20)), "HO-PUN")
        n = self.num.next("invoice_booking", when.date())
        variant = no_sup if R.bern(0.7) else no_sup.replace("/", " ").lower()
        dup = dict(inv)
        dup.update(no=n, supplier_invoice_no=variant, received_at=ts(when), received_on=iso(when.date()), status="cancelled", payment_block="duplicate")
        self.add("invoice", dup, when)
        for ln in [x for x in self.T["invoice_line"] if x["invoice_no"] == inv["no"]]:
            self.add("invoice_line", dict(ln, invoice_no=n), when)
        for ch in [x for x in self.T["invoice_charge"] if x["invoice_no"] == inv["no"]]:
            self.add("invoice_charge", dict(ch, invoice_no=n), when)
        self.case("duplicate_invoice", f"{n};{inv['no']}", "Same supplier invoice number sent again" + ("" if variant == no_sup else " (different formatting)"),
                  "Block as duplicate; pay only once", when)


def stable_hash_small(s):
    return int(hashlib.sha256(s.encode()).hexdigest(), 16) % 1000
