"""How a company's history buys: the buyer's round over approved requisition lines, the route each item takes
(valid contract with money left, single source, safe reorder, else an RFQ), RFQs and awards, purchase orders with GST
and their approval, the supplier's confirmation and date changes, deliveries in one lot or two, inspection, returns
and service entries. A mixin of History (history.py), whose state it works on.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from common import D, iso, money, q2, q3, qty_s, ts, dt_at


ZERO = Decimal(0)


@dataclass
class Need:
    """An approved requisition line waiting for the buyer."""

    request_no: str
    line_no: int
    item: str
    qty: Decimal
    value: Decimal  # at the estimated price
    approved: datetime
    need_by: date
    priority: str
    cost_centre: str
    share: Decimal = Decimal(1)  # a month's usage as a share of the price (cloud)


@dataclass
class Order:
    """What a purchase order line carries through the simulation."""

    po: dict[str, Any]
    line: dict[str, Any]
    item: dict[str, str]
    price: Decimal
    rate: Decimal
    needs: list[Need] = field(default_factory=list)


class Buying:
    """The buying half of History: everything from an approved requisition line to stock and service entries."""

    # ---- buying -----------------------------------------------------------------------------------

    def buy(self, day: date) -> None:
        """The buyer's round: approved lines of the same item are combined and bought by the first route that fits."""
        noon = dt_at(day, 12)
        ready = [n for n in self.waiting if n.approved <= noon]
        if not ready:
            return
        self.waiting = [n for n in self.waiting if n.approved > noon]
        groups: dict[tuple[str, Decimal], list[Need]] = defaultdict(list)
        for n in ready:
            groups[(n.item, n.share)].append(n)
        orders: dict[tuple[str, str, str], list[Need]] = defaultdict(list)
        for (item, _), needs in sorted(groups.items()):
            route, supplier, contract = self.choose(day, item, needs)
            if route == "rfq":
                self.rfq(day, item, needs)
            else:
                orders[(route, supplier, contract)].extend(needs)
        for (route, supplier, contract), needs in sorted(orders.items()):
            self.order(self.later(noon, self.R.uniform(0.5, 3)), supplier, needs, route, contract=contract)

    def choose(self, day: date, item: str, needs: list[Need]) -> tuple[str, str, str]:
        value = sum((n.value for n in needs), ZERO)
        contract = self.contract_for(item, day, sum((n.qty for n in needs), ZERO))
        if contract:
            return "contract", contract["supplier_code"], contract["code"]
        pool = self.pool(item)
        if len(pool) == 1:
            return "single_source", pool[0], ""
        if self.safe_reorder(day, item, value):
            return "safe_reorder", self.bought[item][-1][1], ""
        return "rfq", "", ""

    def safe_reorder(self, day: date, item: str, value: Decimal) -> bool:
        past = [b for b in self.bought[item] if b[0] < day]
        if not past or value >= D(self.route["reorder_line_below"]):
            return False
        when, supplier, price = past[-1]
        if (day - when).days > self.route["reorder_within_days"]:
            return False
        done = [ok for arrived, ok in self.deliveries[supplier] if arrived < day]
        if len(done) < 3 or 100 * sum(done) < self.route["reorder_on_time_pct"] * len(done):
            return False
        return self.list_price(supplier, item, day) <= price * (1 + D(self.route["reorder_price_pct"]) / 100)

    def rfq(self, day: date, item: str, needs: list[Need]) -> None:
        """Ask the item's suppliers to quote, two quotes up to the threshold and three above, the value counted over
        the requisitions of the last 30 days; award the lowest, a few split between the two lowest."""
        window = day - timedelta(days=self.route["value_window_days"])
        counted = sum((v for d, v in self.requested[item] if d > window), ZERO)
        required = 2 if counted <= D(self.route["two_quotes_up_to"]) else 3
        pool = self.pool(item)
        created = self.later(dt_at(day, 12), self.R.uniform(0.5, 2))
        sent = self.later(created, self.R.uniform(0.25, 1.5))
        due = self.plus(day, int(self.setting.get("default_rfq_response_days", "5")))
        no = self.num.next("rfq", day)
        qty = sum((n.qty for n in needs), ZERO)
        it = self.items[item]
        self.add("rfq", {"no": no, "buyer": self.people["buyer"], "created_at": ts(created), "due_date": iso(due), "type": "commercial",
                         "weights": "", "delivery_terms": "for_destination", "payment_terms_proposed": "", "terms_version": "1",
                         "status": "awarded", "sent_at": ts(sent)}, created)  # fmt: skip
        self.add("rfq_line", {"rfq_no": no, "line_no": 1, "request_no": needs[0].request_no, "request_line_no": needs[0].line_no,
                              "item_code": item, "description": it["name"], "attributes": "{}", "qty": qty_s(qty),
                              "uom": it["base_uom"], "need_by": iso(min(n.need_by for n in needs))}, created)  # fmt: skip
        quotes = []
        for k, supplier in enumerate(pool, 1):
            b = self.behaviour[supplier]
            days = self.R.randint(*b["reply"])
            replied = dt_at(self.plus(day, days), self.R.uniform(10, 17))
            price = q2(self.list_price(supplier, item, day) * D(str(round(self.R.uniform(0.97, 1.04), 3))))
            qno = f"QT-{no[4:]}-{k}"
            reminded = days > int(self.setting.get("rfq_reminder_after_days", "2"))
            self.add("rfq_supplier", {"rfq_no": no, "supplier_code": supplier, "invited_at": ts(sent), "reminders_sent": int(reminded),
                                      "responded_at": ts(replied), "declined": "false", "decline_reason": ""}, sent)  # fmt: skip
            self.add("quote", {"no": qno, "rfq_no": no, "supplier_code": supplier, "revision": 1, "received_at": ts(replied),
                               "valid_until": iso(replied.date() + timedelta(days=30)), "payment_terms_code": self.sup[supplier]["payment_terms_code"],
                               "delivery_terms": "for_destination", "packing_forwarding": "0.00", "freight": "0.00", "taxes_extra": "true",
                               "warranty_months": "", "deviations": "", "document_ref": ""}, replied)  # fmt: skip
            lead = self.lead.get((supplier, item), int(it["default_lead_days"])) + b["lead_extra"]
            self.add("quote_line", {"quote_no": qno, "rfq_line_no": 1, "item_code": item, "offered_brand": "", "qty": qty_s(qty),
                                    "moq": "1", "unit_price": money(price), "discount_pct": "0.00", "gst_rate": str(self.rate_on(it, day)),
                                    "delivery_days": lead, "deviation": ""}, replied)  # fmt: skip
            quotes.append((price, lead, supplier, qno, replied))
        quotes.sort()
        awarded = self.later(max(q[4] for q in quotes), self.R.uniform(1, 4))
        whole = it["base_uom"] in ("NOS", "PAC", "OTH")
        split = len(quotes) >= 2 and qty >= 4 and self.R.bern(self.route["split_award"])
        if split:
            first = (qty * D("0.6")).quantize(D(1)) if whole else q3(qty * D("0.6"))
            shares = [(quotes[0], first), (quotes[1], qty - first)]
            reason = "Split between the two lowest quotes for supply security"
        else:
            shares = [(quotes[0], qty)]
            reason = "Lowest price"
        selection = {"rfq_no": no, "rfq_line_no": 1, "item_code": item, "qty": qty_s(qty), "value_counted": money(counted),
                     "quotes_required": required, "suppliers_asked": len(pool),
                     "options": ";".join(f"{s}:{money(p)}:{d}d" for p, d, s, _, _ in quotes),
                     "selected": ";".join(q[2] for q, _ in shares), "reason": reason}  # fmt: skip
        if len(pool) < required:
            selection["reason"] += f"; only {len(pool)} approved suppliers deal in this item"
        self.add("truth_supplier_selection", selection, awarded)
        remaining = list(needs)
        for (price, _, supplier, qno, _), part in shares:
            self.add("award", {"rfq_no": no, "rfq_line_no": 1, "supplier_code": supplier, "quote_no": qno, "qty": qty_s(part),
                               "price": money(price), "reason": reason, "overridden": "false", "by_user": self.people["buyer"],
                               "at": ts(awarded)}, awarded)  # fmt: skip
            taken, left = [], part
            while remaining and left > 0:
                n = remaining[0]
                if n.qty <= left:
                    taken.append(remaining.pop(0))
                    left -= n.qty
                else:  # the line is shared between the two orders
                    taken.append(
                        Need(
                            n.request_no,
                            n.line_no,
                            n.item,
                            left,
                            q2(n.value * left / n.qty),
                            n.approved,
                            n.need_by,
                            n.priority,
                            n.cost_centre,
                        )
                    )
                    remaining[0] = Need(
                        n.request_no,
                        n.line_no,
                        n.item,
                        n.qty - left,
                        n.value - taken[-1].value,
                        n.approved,
                        n.need_by,
                        n.priority,
                        n.cost_centre,
                    )
                    left = ZERO
            self.order(self.later(awarded, self.R.uniform(0.5, 2)), supplier, taken, "rfq", rfq=no, price=price)

    # ---- purchase orders and what follows ----------------------------------------------------------

    def order(self, when: datetime, supplier: str, needs: list[Need], route: str, *, contract: str = "", rfq: str = "",
              price: Decimal | None = None, gate: datetime | None = None) -> None:  # fmt: skip
        """A purchase order with GST, its approval by value, the supplier's confirmation and everything delivered."""
        s, day = self.sup[supplier], when.date()
        no = self.num.next("po", day, self.loc)
        intra = s["state_code"] == self.state
        by_item: dict[str, list[Need]] = defaultdict(list)
        for n in needs:
            by_item[n.item].append(n)
        lines, taxable, cgst, sgst, igst = [], ZERO, ZERO, ZERO, ZERO
        for k, (item, group) in enumerate(sorted(by_item.items()), 1):
            it = self.items[item]
            qty = sum((n.qty for n in group), ZERO)
            share = group[0].share
            unit = (
                price
                if price is not None
                else (
                    self.contract_price[(contract, item)]
                    if contract
                    else q2(self.list_price(supplier, item, day) * share)
                )
            )
            rate = self.rate_on(it, day)
            value = q2(unit * qty)
            c = s_ = q2(value * rate / 200) if intra else ZERO
            i = ZERO if intra else q2(value * rate / 100)
            taxable, cgst, sgst, igst = taxable + value, cgst + c, sgst + s_, igst + i
            lead = (
                self.lead.get((supplier, item), int(it["default_lead_days"])) + self.behaviour[supplier]["lead_extra"]
            )
            need_by = min(n.need_by for n in group)
            if route == "emergency":
                promised = self.plus(day, 1)
            else:
                promised = max(self.plus(day, lead), need_by) if it["kind"] == "goods" else need_by
            line = {"po_no": no, "line_no": k, "request_no": group[0].request_no, "request_line_no": group[0].line_no,
                    "item_code": item, "description": it["name"], "qty": qty_s(qty), "uom": it["base_uom"],
                    "unit_price": money(unit), "gst_rate": str(rate), "cgst": money(c), "sgst": money(s_), "igst": money(i),
                    "need_by": iso(need_by), "promised_date": iso(promised), "received_qty": "0.000", "invoiced_qty": "0.000",
                    "cost_centre_code": group[0].cost_centre, "gl_code": it["default_gl"], "status": "open"}  # fmt: skip
            lines.append(Order({}, line, it, unit, rate, group))
            for n in group:
                self.add("truth_combined_lines", {"po_no": no, "po_line_no": k, "request_no": n.request_no,
                                                  "request_line_no": n.line_no, "item_code": item, "qty": qty_s(n.qty),
                                                  "route": route}, when)  # fmt: skip
            self.bought[item].append((day, supplier, unit))
        total = taxable + cgst + sgst + igst
        if contract:
            self.contract_used[contract] += taxable
        approved = self.approve("po", no, total, when, "ANY")
        sent = self.later(approved, self.R.uniform(0.25, 1.5))
        po = self.add("po", {"no": no, "po_type": "contract_release" if contract else "standard", "supplier_code": supplier,
                             "supplier_gstin": s["gstin"], "buyer_gstin": self.m["location"][0]["gstin"], "ship_to_location": self.loc,
                             "buyer": self.people["buyer"], "project_code": "", "contract_code": contract, "created_at": ts(when),
                             "approved_at": ts(approved), "sent_at": ts(sent),
                             "payment_terms_code": next((c["payment_terms_code"] for c in self.contracts if c["code"] == contract), s["payment_terms_code"]),
                             "advance_pct": "0", "delivery_terms": "for_destination", "freight_terms": "included", "terms_version": "1",
                             "version": "1", "taxable": money(taxable), "cgst": money(cgst), "sgst": money(sgst), "igst": money(igst),
                             "total": money(total), "status": "sent", "cancel_reason": "", "_route": route, "_rfq": rfq,
                             "_sent": sent}, when)  # fmt: skip
        for o in lines:
            o.po = po
            self.add("po_line", o.line, when)
        self.confirm(po, lines, sent, gate)

    def confirm(self, po: dict[str, Any], lines: list[Order], sent: datetime, gate: datetime | None) -> None:
        """The supplier confirms (sometimes asking for a later date) and delivers, on time or late, whole or in two lots."""
        b = self.behaviour[po["supplier_code"]]
        ack = self.later(sent, self.R.uniform(1, 9) + 9 * self.R.randint(*b["ack"]))
        self.add("po_event", {"po_no": po["no"], "type": "acknowledged", "at": ts(ack), "field": "", "old_value": "",
                              "new_value": "", "email_ref": "", "applied_by": "agent"}, ack)  # fmt: skip
        goods = [o for o in lines if o.item["kind"] == "goods"]
        late = date.fromisoformat(po["created_at"][:10]) > self.buy_by - timedelta(days=20)
        if (
            goods
            and gate is None
            and self.R.bern(b["date_change"])
            and not (late and any(o.item["code"] in self.protected for o in goods))
        ):
            slip = self.R.randint(*b["slip"])
            asked = self.later(ack, self.R.uniform(2, 9))
            # within the company's tolerance (calendar days) the supplier agent applies it, else a buyer accepts it
            limit = int(next(t["value"] for t in self.m["tolerance"] if t["key"] == "supplier_date_days"))
            for o in goods:
                old = o.line["promised_date"]
                o.line["promised_date"] = iso(self.plus(date.fromisoformat(old), slip))
                moved = (date.fromisoformat(o.line["promised_date"]) - date.fromisoformat(old)).days
                self.add("po_event", {"po_no": po["no"], "type": "date_change", "at": ts(asked), "field": "promised_date",
                                      "old_value": old, "new_value": o.line["promised_date"], "email_ref": "",
                                      "applied_by": "agent" if moved <= limit else "buyer"}, asked)  # fmt: skip
            self.case("supplier_date_change", po["no"], f"Supplier asked for a delivery {slip} working days later",
                      "Accepted by the buyer; the promised date moves and on-time is judged against the new date", asked.date())  # fmt: skip
        for o in lines:
            self.add("po_schedule", {"po_no": po["no"], "line_no": o.line["line_no"], "delivery_date": o.line["promised_date"],
                                     "qty": o.line["qty"]}, dt_at(sent.date(), 18))  # fmt: skip
        if goods:
            self.deliver(po, goods, gate)
        for o in lines:
            if o.item["kind"] == "service":
                self.service_entry(po, o)

    def deliver(self, po: dict[str, Any], goods: list[Order], gate: datetime | None) -> None:
        b = self.behaviour[po["supplier_code"]]
        promised = date.fromisoformat(goods[0].line["promised_date"])
        on_time = gate is not None or self.R.bern(b["on_time"])
        arrive = gate.date() if gate else promised if on_time else self.plus(promised, self.R.randint(*b["late"]))
        protected = any(o.item["code"] in self.protected for o in goods)
        if protected:
            arrive = min(arrive, max(self.clip_day, self.plus(date.fromisoformat(po["created_at"][:10]), 1)))
        lots: dict[date, list[tuple[Order, Decimal]]] = defaultdict(list)
        part = gate is None and self.R.bern(b["part"])
        second = self.plus(arrive, self.R.randint(4, 8))
        if protected:
            second = min(second, self.clip_day)
        part = part and second > arrive
        for o in goods:
            qty = D(o.line["qty"])
            first = (qty * D(str(round(self.R.uniform(0.5, 0.7), 2)))).quantize(D(1)) if part else qty
            if part and 0 < first < qty:
                lots[arrive].append((o, first))
                lots[second].append((o, qty - first))
            else:
                lots[arrive].append((o, qty))
        if len(lots) > 1 and second <= self.cutoff:
            self.case("part_delivery", po["no"], "Delivered in two lots",
                      "The balance stays open until the second lot; each invoice matches what was received", second)  # fmt: skip
        for day, got in sorted(lots.items()):
            self.deliveries[po["supplier_code"]].append((day, day <= promised))
            self.receipt(po, day, got, gate if day == arrive else None)

    def receipt(self, po: dict[str, Any], day: date, got: list[tuple[Order, Decimal]], gate: datetime | None,
                remarks: str = "") -> None:  # fmt: skip
        """A goods receipt: gate entry, inspection of the items that need it, stock for what is accepted."""
        if day > self.cutoff:
            return
        b = self.behaviour[po["supplier_code"]]
        at = gate or dt_at(day, self.R.uniform(9.5, 16))
        no = self.num.next("goods_receipt", day, self.loc)
        challan = f"DC/{self.R.randint(1000, 9999)}"
        inspect = any(o.item["inspection_required"] == "true" for o, _ in got)
        accepted_on = self.plus(day, 1) if inspect else day
        dispatched = (
            max(dt_at(self.cal.prev_working(day - timedelta(days=1), self.loc), 16), po["_sent"]) if not gate else at
        )
        self.add("po_event", {"po_no": po["no"], "type": "dispatched", "at": ts(min(dispatched, at)), "field": "dispatch_note",
                              "old_value": "", "new_value": challan, "email_ref": "", "applied_by": "agent"}, min(dispatched, at))  # fmt: skip
        failed, lines = False, []
        for o, qty in got:
            rejected = ZERO
            if o.item["inspection_required"] == "true" and self.R.bern(b["reject"]):
                rejected = max(D(1), (qty * D(str(round(self.R.uniform(0.05, 0.2), 2)))).quantize(D(1)))
                failed = True
            lines.append((o, qty, qty - rejected, rejected))
        pending = accepted_on > self.cutoff
        grn = self.add("goods_receipt", {"no": no, "po_no": po["no"], "location_code": self.loc, "gate_entry_at": ts(at),
                                         "received_by": self.people["receiver"], "delivery_challan_no": challan,
                                         "supplier_invoice_ref": "", "eway_bill_no": "", "vehicle_no": "",
                                         "inspection_status": "pending" if pending and inspect else "failed" if failed else
                                         "passed" if inspect else "not_required",
                                         "accepted_on": "" if pending else iso(accepted_on), "remarks": remarks}, at)  # fmt: skip
        for o, qty, ok, rejected in lines:
            self.add("gr_line", {"gr_no": no, "po_no": po["no"], "po_line_no": o.line["line_no"], "qty_received": qty_s(qty),
                                 "qty_accepted": qty_s(ZERO if pending else ok), "qty_rejected": qty_s(ZERO if pending else rejected),
                                 "reject_reason": "Failed inspection" if rejected and not pending else ""}, at)  # fmt: skip
            if pending:
                continue
            use = o.needs[0].request_no if po["_route"] == "emergency" else ""
            self.accept[accepted_on].append((o.item["code"], ok, o.price, no, use))
            if rejected:
                self.return_rejected(po, o, no, rejected, accepted_on)
        if not pending and any(ok > 0 for _, _, ok, _ in lines):
            self.drafts.append({"kind": "goods", "po": po, "doc": grn, "day": day, "accepted_on": accepted_on,
                                "lines": [(o, ok) for o, _, ok, _ in lines if ok > 0]})  # fmt: skip

    def return_rejected(self, po: dict[str, Any], o: Order, grn: str, qty: Decimal, on: date) -> None:
        """Rejected at inspection: returned with a debit note, and the supplier sends the quantity again."""
        rtv = self.num.next("return_to_vendor", on, self.loc)
        dn = f"DN-{rtv.split('-')[2]}-{len(self.tables['return_to_vendor']) + 1:04d}"
        self.add("return_to_vendor", {"no": rtv, "gr_no": grn, "item_code": o.item["code"], "qty": qty_s(qty),
                                      "reason": "Rejected at inspection", "debit_note_no": dn, "at": ts(dt_at(on, 11))},
                 dt_at(on, 11))  # fmt: skip
        self.case("failed_inspection", f"{grn};{po['no']}", f"{qty_s(qty)} {o.item['base_uom']} of {o.item['code']} failed inspection",
                  "Only the accepted quantity enters stock and may be invoiced; the rest goes back and is sent again", on)  # fmt: skip
        again = self.plus(on, self.R.randint(4, 7))
        if o.item["code"] in self.protected:
            again = min(again, self.clip_day)
        again = max(again, self.plus(on, 1))
        self.receipt(po, again, [(o, qty)], None, f"Sent again after rejection ({grn})")

    def service_entry(self, po: dict[str, Any], o: Order) -> None:
        """A service is accepted by a service entry at the end of its month (a one-off job a few days after ordering)."""
        start = date.fromisoformat(po["created_at"][:10])
        if o.item["code"] in {s["item"] for s in self.h.get("services", []) if s["months"] == "all"}:
            nxt = date(start.year + start.month // 12, start.month % 12 + 1, 1)
            done = self.cal.prev_working(nxt - timedelta(days=1), self.loc)
            period = (date(start.year, start.month, 1), nxt - timedelta(days=1))
        else:
            done = self.plus(start, self.R.randint(2, 5))
            period = (done, done)
        if done > self.cutoff:
            return
        no = self.num.next("service_receipt", done, self.loc)
        amount = q2(o.price * D(o.line["qty"]))
        doc = self.add("service_receipt", {"no": no, "po_no": po["no"], "po_line_no": o.line["line_no"], "period_from": iso(period[0]),
                                           "period_to": iso(period[1]), "description": o.item["name"], "amount_accepted": money(amount),
                                           "accepted_by": self.people["receiver"], "accepted_on": iso(done)}, dt_at(done, 15))  # fmt: skip
        self.deliveries[po["supplier_code"]].append((done, True))
        self.drafts.append(
            {
                "kind": "service",
                "po": po,
                "doc": doc,
                "day": done,
                "accepted_on": done,
                "lines": [(o, D(o.line["qty"]))],
            }
        )
