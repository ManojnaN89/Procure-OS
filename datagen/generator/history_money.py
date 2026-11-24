"""The money side of a company's history: supplier invoices (with the planted problems), the three-way match a correct
platform must conclude, blocks and how they were cleared, tax at source, due dates, weekly payment runs, bank lines
and supplier statements. Then the cut-off, the statuses at the cutoff and the answer keys.

Reuses finance.py for the rules of tax at source, due dates and the run days, and for numbering documents in date order.
"""

import hashlib
import math
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from common import D, fy, fy4, iso, money, q2, qty_s, sub_rng, ts, dt_at
from finance import due_date, quarter_of, run_after, run_on_or_before, section_label, tds_on
from history import WEEKDAYS, History
from history_buying import ZERO, Order
from master_files import tolerance_on


DAY_END = 23.99


class Money:
    """Invoices to payments for one run of the history, added to its tables."""

    def __init__(self, hist: History) -> None:
        self.s, self.h = hist, hist.h
        self.R = sub_rng(f"{hist.p.name}:{hist.h['seed']}", "money")
        self.T = hist.tables
        self.end = dt_at(hist.cutoff, DAY_END)
        self.edges = self.h.get("tolerance_edges", {})
        self.edge_line: dict[int, int] = {}  # a tolerance-edge receipt: the order line that gets the extra
        self.terms = {r["code"]: r for r in hist.m["payment_terms"]}
        self.weekday = WEEKDAYS[self.h["payment_day"]]
        self.planted = self.h["planted"]
        self.invoices: list[dict[str, Any]] = []  # one per invoice row, with what payment needs
        self.seq: dict[tuple[str, str], int] = defaultdict(int)
        self.open_block = 0  # the receipt whose price problem is still blocked at the cutoff
        self.gst_mode = 0  # the next planted GST problem: 0 a wrong rate, 1 an amount five rupees high

    # ---- invoices -------------------------------------------------------------------------------

    def run(self) -> "Money":
        plants = self.choose_plants()
        for draft in sorted(self.s.drafts, key=lambda d: (d["day"], d["doc"]["no"])):
            self.invoice(draft, plants.get(id(draft), ""))
        self.duplicates()
        self.pay()
        self.bank_charges()
        return self

    def choose_plants(self) -> dict[int, str]:
        """Which receipts get which planted problem: spread over the year, each receipt at most one. The last price
        problem is on a recent receipt, still blocked at the cutoff."""
        late = self.s.cutoff - timedelta(days=50)
        goods = sorted((d for d in self.s.drafts if d["kind"] == "goods"), key=lambda d: (d["day"], d["doc"]["no"]))
        early = [d for d in goods if d["day"] < late]
        chosen: dict[int, str] = {}

        def big(d: dict[str, Any]) -> bool:
            return any(q2(o.price * q) >= 10000 for o, q in d["lines"])

        def pick(pool: list[dict[str, Any]], kind: str, count: int) -> None:
            for k in range(count):
                at = (2 * k + 1) * len(pool) // (2 * count)
                free = [d for d in pool[at:] + pool[:at] if id(d) not in chosen]
                if not free:
                    raise RuntimeError(f"no receipt left to plant {kind}")
                chosen[id(free[0])] = kind

        prices = self.planted.get("price_mismatch", 0)
        recent = [d for d in goods if late <= d["day"] <= self.s.cutoff - timedelta(days=12) and big(d)]
        if prices and recent:
            self.open_block = id(recent[len(recent) // 2])
            chosen[self.open_block] = "price_mismatch"
            prices -= 1
        pick([d for d in early if big(d)], "price_mismatch", prices)
        pick(
            # not on a lot that failed inspection: there the extra quantity is the rejected one, a different cause
            [d for d in early if any(q >= 5 for _, q in d["lines"]) and d["doc"]["inspection_status"] != "failed"],
            "qty_mismatch",
            self.planted.get("qty_mismatch", 0),
        )
        pick(
            [d for d in early if d["doc"]["inspection_status"] != "failed"],
            "gst_mismatch",
            self.planted.get("gst_mismatch", 0),
        )
        pick(early, "grni_month_end", self.planted.get("grni_month_end", 0))
        pick(early, "invoice_not_received", self.planted.get("invoice_not_received", 0))
        self.pick_edges(goods, chosen)
        return chosen

    def pick_edges(self, goods: list[dict[str, Any]], chosen: dict[int, str]) -> None:
        """Invoices on either side of a tolerance version's start (task 3.2): one receipt in each month listed, a line
        invoiced a little over the accepted quantity, or a little above the order price on a small line."""
        for kind, edge in (
            ("tolerance_edge_qty", self.edges.get("qty")),
            ("tolerance_edge_price", self.edges.get("price")),
        ):
            if not edge or not self.planted.get(kind):
                continue
            for month in edge["months"]:
                found = []
                for d in goods:  # early in the month, so the invoice is received in the same month
                    if id(d) in chosen or iso(d["day"])[:7] != month or d["day"].day > 20:
                        continue
                    if d["doc"]["inspection_status"] == "failed":
                        continue
                    for o, q in d["lines"]:
                        if kind == "tolerance_edge_qty":
                            ok = (d["po"]["supplier_code"] == edge["supplier"] and o.item["code"] == edge["item"]
                                  and q >= 25)  # fmt: skip
                        else:  # both tests: 1.5% to 1.9% over and Rs 301 to 499 on the line (DF-12, D-17)
                            ok = D(15000) <= q2(o.price * q) <= D(26000) and q <= 150
                        if ok:
                            found.append((d, o))
                            break
                if not found:
                    raise RuntimeError(f"no receipt in {month} to plant {kind}")
                d, o = found[len(found) // 2]
                chosen[id(d)], self.edge_line[id(d)] = kind, id(o)

    def supplier_no(self, supplier: str, d: date) -> str:
        s = self.s.sup[supplier]
        initials = "".join(w[0] for w in s["legal_name"].split() if w[0].isalpha())[:4].upper()
        self.seq[(supplier, fy4(d))] += 1  # numbered afresh each financial year
        return f"{initials}/{fy4(d)}/{self.seq[(supplier, fy4(d))]:04d}"

    def invoice(self, draft: dict[str, Any], plant: str) -> None:
        po, day = draft["po"], draft["day"]
        supplier = self.s.sup[po["supplier_code"]]
        lag = self.R.randint(0, 3)
        if plant == "grni_month_end":
            lag = self.R.randint(28, 34)
        received = dt_at(self.s.plus(day, lag), self.R.uniform(10, 16.5))
        statement = None
        if plant == "invoice_not_received":
            month_end = date(day.year + day.month // 12, day.month % 12 + 1, 1) - timedelta(days=1)
            statement = self.s.cal.prev_working(
                month_end if (month_end - day).days >= 5 else month_end + timedelta(days=28), self.s.loc
            )
            received = dt_at(self.s.plus(statement, self.R.randint(2, 4)), 11)
        if received > self.end:
            if plant:
                raise RuntimeError(f"a planted {plant} falls after the cutoff")
            return
        if plant.startswith("tolerance_edge") and iso(received.date())[:7] != iso(day)[:7]:
            raise RuntimeError(f"a planted {plant} is received in another month")
        lines = []
        for k, (o, qty) in enumerate(draft["lines"]):
            price = o.price
            if id(o) == self.edge_line.get(id(draft)):
                if plant == "tolerance_edge_qty":
                    qty = qty + max(D(1), (qty * D(self.edges["qty"]["pct"]) / 100).quantize(D(1)))
                else:
                    price = q2(o.price + self._edge_over(qty, o.price))
            if plant == "price_mismatch" and q2(o.price * qty) >= 10000 and not any(x[3] for x in lines):
                price = q2(o.price * D(str(round(self.R.uniform(1.05, 1.1), 3))))
            if plant == "qty_mismatch" and k == 0:
                qty = qty + max(D(1), D(math.ceil(qty * D("0.1"))))
            price = q2(price)
            lines.append((o, qty, price, price != o.price))
        number = self.supplier_no(supplier["code"], day)
        row = self.book(draft, number, received, lines)
        if plant == "gst_mismatch":
            self.plant_gst(row)
        if draft["kind"] == "goods":
            draft["doc"]["supplier_invoice_ref"] = number
        result, reason, version, reasons = self.judge(row, draft)
        row["_truth"] = {
            "result": result,
            "reason_codes": reason,
            "reasons": reasons,
            "rules": 2,
            "planted": plant
            if plant in ("price_mismatch", "qty_mismatch", "tolerance_edge_qty", "tolerance_edge_price", "gst_mismatch")
            else "",
            "tolerance_version": version,
        }
        if plant.startswith("tolerance_edge") and result != "blocked":
            what = "quantity" if plant == "tolerance_edge_qty" else "price"
            self.s.case(plant, f"{row['no']};{po['no']}", f"Invoiced {what} a little over, received under tolerance version {version}",
                        f"Within tolerance version {version}: approved for payment", received.date())  # fmt: skip
        if statement:
            self.statement(row, statement)
        if plant == "grni_month_end":
            self.s.case("grni_month_end", f"{draft['doc']['no']};{po['no']}",
                        f"Invoice arrived {lag} working days after the receipt, after a month end",
                        "Shown as goods received not invoiced at the month end (truth_grni)", day)  # fmt: skip
        if result == "blocked":
            self.block(row, draft, lines, plant, reason)

    def book(self, draft: dict[str, Any], number: str, received: datetime, lines: list[tuple[Order, Decimal, Decimal, bool]],
             *, of: str = "") -> dict[str, Any]:  # fmt: skip
        """One invoice row and its lines, with GST the generator works out itself."""
        po, supplier = draft["po"], self.s.sup[draft["po"]["supplier_code"]]
        no = self.s.num.next("invoice_booking", received.date())
        inv_date = draft["day"] if draft["kind"] == "goods" else self.s.plus(draft["day"], 1)
        inv_date = min(inv_date, received.date())
        intra = supplier["state_code"] == self.s.state
        taxable = cgst = sgst = igst = ZERO
        for n, (o, qty, price, _) in enumerate(lines, 1):
            value = q2(price * qty)
            c = q2(value * o.rate / 200) if intra else ZERO
            i = ZERO if intra else q2(value * o.rate / 100)
            taxable, cgst, sgst, igst = taxable + value, cgst + c, sgst + c, igst + i
            self.s.add("invoice_line", {"invoice_no": no, "line_no": n, "po_no": po["no"], "po_line_no": o.line["line_no"],
                                        "description": o.item["name"], "hsn_sac": o.item["hsn_sac"], "qty": qty_s(qty),
                                        "uom": o.item["base_uom"], "unit_price": money(price), "discount": "0.00",
                                        "taxable": money(value), "gst_rate": str(o.rate), "cgst": money(c), "sgst": money(c),
                                        "igst": money(i)}, received)  # fmt: skip
        gross = taxable + cgst + sgst + igst
        total = gross.quantize(Decimal(1), ROUND_HALF_UP)
        irn = ""
        if supplier["e_invoice_applicable"] == "true":
            irn = hashlib.sha256(f"{supplier['gstin']}{fy(inv_date)}INV{number}".encode()).hexdigest().upper()
        row = self.s.add("invoice", {"no": no, "type": "invoice", "supplier_code": supplier["code"], "supplier_gstin": supplier["gstin"],
                                     "buyer_gstin": po["buyer_gstin"], "place_of_supply": po["buyer_gstin"][:2],
                                     "supplier_invoice_no": number, "invoice_date": iso(inv_date), "fy": fy(inv_date), "irn": irn,
                                     "ack_no": str(10**14 + int(irn[:13], 16) % (9 * 10**14)) if irn else "",
                                     "ack_date": iso(inv_date) if irn else "", "signed_qr_ref": "", "eway_bill_no": "",
                                     "reverse_charge": "false", "received_at": ts(received), "received_on": iso(received.date()),
                                     "channel": "email" if self.R.bern(0.85) else "upload", "taxable": money(taxable),
                                     "cgst": money(cgst), "sgst": money(sgst), "igst": money(igst), "cess": "0.00",
                                     "round_off": money(total - gross), "total": money(total), "ref_invoice_no": of,
                                     "due_date": "", "payment_block": "none", "status": "approved", "document_ref": "",
                                     "document_hash": "", "_draft": draft, "_lines": lines,
                                     "_ready": self.s.later(received, self.R.uniform(2, 12))}, received)  # fmt: skip
        self.invoices.append(row)
        return row

    def _edge_over(self, qty: Decimal, order_price: Decimal) -> Decimal:
        """The per-unit rise that puts Rs 301 to 499 on this line at 1.5% to 1.9% of a Rs 15,000 to 26,000 line."""
        value = q2(order_price * qty)
        wanted = min(max(q2(value * D("0.017")), D("301")), D("499"))
        per = (wanted / qty).quantize(D("0.01"))
        while per * qty < D("301"):
            per += D("0.01")
        while per * qty > D("499"):
            per -= D("0.01")
        return per

    def plant_gst(self, row: dict[str, Any]) -> None:
        """One planted GST problem: a 5% item billed at 18%, or a CGST five rupees above the order's split."""
        lines = [x for x in self.T["invoice_line"] if x["invoice_no"] == row["no"]]
        line = lines[0]
        value = D(line["taxable"])
        intra = self.s.sup[row["supplier_code"]]["state_code"] == self.s.state
        if self.gst_mode % 2 == 0:
            rate = D("18") if D(line["gst_rate"]) != D("18") else D("5")
            line["gst_rate"] = str(rate)
        else:
            rate = D(line["gst_rate"])
        cgst = q2(value * rate / 200) if intra else ZERO
        igst = ZERO if intra else q2(value * rate / 100)
        if self.gst_mode % 2 == 1:
            cgst = cgst + D("5.00")
        line["cgst"], line["sgst"], line["igst"] = money(cgst), money(cgst), money(igst)
        self.gst_mode += 1
        taxable, cgst_total, sgst_total, igst_total = ZERO, ZERO, ZERO, ZERO
        for x in [y for y in self.T["invoice_line"] if y["invoice_no"] == row["no"]]:
            taxable, cgst_total, sgst_total, igst_total = (
                taxable + D(x["taxable"]),
                cgst_total + D(x["cgst"]),
                sgst_total + D(x["sgst"]),
                igst_total + D(x["igst"]),
            )
        gross = taxable + cgst_total + sgst_total + igst_total
        total = gross.quantize(Decimal(1), ROUND_HALF_UP)
        for target in (row, next(x for x in self.T["invoice"] if x["no"] == row["no"])):
            target.update(cgst=money(cgst_total), sgst=money(sgst_total), igst=money(igst_total),
                          round_off=money(total - gross), total=money(total))  # fmt: skip

    def judge(self, row: dict[str, Any], draft: dict[str, Any]) -> tuple[str, str, int, str]:
        """The three-way match (two-way for a service): price within both limits, quantity within what was accepted,
        and the GST rate and each tax head against the order's split, under the version in force on the received day."""
        result, version = "matched", 0
        reasons: list[str] = []
        stored = {int(x["line_no"]): x for x in self.T["invoice_line"] if x["invoice_no"] == row["no"]}
        for n, (o, qty, price, _) in enumerate(row["_lines"], start=1):
            version, tol = tolerance_on(self.s.m["tolerance"], self.s.m["tolerance_version"], row["received_on"],
                                        o.item["category_code"])  # fmt: skip
            available = next(q for x, q in draft["lines"] if x is o)
            diff = price - o.price
            if diff > 0 and not (diff <= o.price * tol["price_pct"] / 100 and diff * qty <= tol["price_abs"]):
                reasons.append("price_variance")
            elif diff > 0:
                result = "within_tolerance"
            if qty > available + available * tol["qty_pct"] / 100:
                reasons.append("qty_over_received")
            line = stored[n]
            order_rate = D(o.rate)
            if D(line["gst_rate"]) != order_rate:
                reasons.append("gst_mismatch")
            else:
                taxable = D(line["taxable"])
                intra = self.s.sup[row["supplier_code"]]["state_code"] == self.s.state
                want = (
                    (q2(taxable * order_rate / 200), q2(taxable * order_rate / 200), ZERO)
                    if intra
                    else (ZERO, ZERO, q2(taxable * order_rate / 100))
                )
                got = (D(line["cgst"]), D(line["sgst"]), D(line["igst"]))
                tolerance = Decimal(tol.get("tax_line_abs", "1.00"))
                if any(abs(a - b) > tolerance for a, b in zip(got, want, strict=True)):
                    reasons.append("gst_mismatch")
        first = reasons[0] if reasons else ""
        return ("blocked" if reasons else result), first, version, ";".join(reasons)

    def block(self, row: dict[str, Any], draft: dict[str, Any], lines: list[Any], plant: str, reason: str) -> None:
        """A blocked invoice: a price variance accepted by its approver; too much quantity sent back for a corrected
        invoice. The last price problem of the year is still blocked at the cutoff."""
        row["status"], row["payment_block"] = "blocked", "match"
        received = datetime.fromisoformat(row["received_at"][:19])
        decided = dt_at(self.s.plus(received.date(), self.R.randint(2, 5)), self.R.uniform(10, 17))
        po = draft["po"]
        if reason == "price_variance":
            self.s.case(plant, f"{row['no']};{po['no']}", "Invoice price above the order price beyond tolerance"
                        + (f" (version {row['_truth']['tolerance_version']})" if plant != "price_mismatch" else ""),
                        "Blocked for price variance; paid only after the variance is accepted", received.date())  # fmt: skip
            if id(draft) == self.open_block or decided > self.end:
                row["_ready"] = None  # still blocked at the cutoff
                return
            value = sum((q2((p - o.price) * q) for o, q, p, _ in lines), ZERO)
            self.s.approve("price_variance", row["no"], value, decided, "ANY")
            row["status"], row["payment_block"], row["_ready"] = "approved", "none", self.s.later(decided, 1)
            return
        what = (
            "Invoiced quantity above the accepted quantity"
            if reason == "qty_over_received"
            else ("GST on the invoice does not match the order")
        )
        self.s.case(plant, f"{row['no']};{po['no']}", what
                    + (f" (version {row['_truth']['tolerance_version']})" if plant != "qty_mismatch" and plant != "gst_mismatch" else ""),
                    "Blocked; the supplier sends a corrected invoice for what was accepted", received.date())  # fmt: skip
        row["status"], row["_ready"] = "cancelled", None
        again = dt_at(self.s.plus(decided.date(), self.R.randint(1, 3)), 11)
        if again <= self.end:
            right = [(o, next(q for x, q in draft["lines"] if x is o), o.price, False) for o, *_ in lines]
            fixed = self.book(draft, row["supplier_invoice_no"] + "-R", again, right, of=row["no"])
            version = tolerance_on(self.s.m["tolerance"], self.s.m["tolerance_version"], fixed["received_on"])[0]
            fixed["_truth"] = {
                "result": "matched",
                "reason_codes": "",
                "reasons": "",
                "rules": 2,
                "planted": "corrected_invoice",
                "tolerance_version": version,
            }
            if draft["kind"] == "goods":
                draft["doc"]["supplier_invoice_ref"] = fixed["supplier_invoice_no"]

    def duplicates(self) -> None:
        """The same supplier invoice sent again a few days later: booked, then stopped as a duplicate."""
        normal = [r for r in self.invoices if r["status"] == "approved" and not r["_truth"]["planted"]
                  and r["_draft"]["day"] < self.s.cutoff - timedelta(days=40)]  # fmt: skip
        count = self.planted.get("duplicate_invoice", 0)
        for k in range(count):
            first = normal[(k * 2 + 1) * len(normal) // (2 * count)]
            when = dt_at(self.s.plus(first["_draft"]["day"], self.R.randint(6, 15)), 12)
            no = self.s.num.next("invoice_booking", when.date())
            copy = {key: v for key, v in first.items() if not key.startswith("_")}
            variant = (
                first["supplier_invoice_no"] if k % 2 == 0 else first["supplier_invoice_no"].replace("/", " ").lower()
            )
            copy.update(no=no, supplier_invoice_no=variant, received_at=ts(when), received_on=iso(when.date()),
                        status="cancelled", payment_block="duplicate")  # fmt: skip
            if variant != first["supplier_invoice_no"]:  # typed in again by hand: no e-invoice number came with it
                copy.update(irn="", ack_no="", ack_date="")
            self.s.add("invoice", copy, when)
            for line in [x for x in self.T["invoice_line"] if x["invoice_no"] == first["no"]]:
                self.s.add("invoice_line", {**{k2: v for k2, v in line.items() if k2 != "_t"}, "invoice_no": no}, when)
            self.s.case("duplicate_invoice", f"{no};{first['no']}",
                        "Same supplier invoice sent again" + ("" if k % 2 == 0 else " (written differently)"),
                        "Block as duplicate; pay only once", when.date())  # fmt: skip

    def statement(self, row: dict[str, Any], on: date) -> None:
        """The supplier's statement at the month end lists every invoice it sent, one of them never received."""
        supplier = row["supplier_code"]
        booked = [r for r in self.invoices if r["supplier_code"] == supplier and r is not row
                  and r["received_on"] <= iso(on) and r["invoice_date"][:7] == row["invoice_date"][:7]]  # fmt: skip
        when = dt_at(on, 17)
        for r in [*booked, row]:
            self.s.add("vendor_statement_line", {"supplier_code": supplier, "statement_date": iso(on),
                                                 "supplier_invoice_no": r["supplier_invoice_no"], "invoice_date": r["invoice_date"],
                                                 "amount": r["total"], "our_invoice_no": "" if r is row else r["no"]}, when)  # fmt: skip
        self.s.case("invoice_not_received", f"{supplier};{row['supplier_invoice_no']}",
                    "The supplier's statement shows an invoice that never reached us",
                    "Ask for a copy; book it against its receipt (it is booked a few days later)", on)  # fmt: skip

    # ---- payment ------------------------------------------------------------------------------------

    def pay(self) -> None:
        """Tax at source in booking order, the due date, and the weekly run on or before it (or the first after the
        invoice is ready); a few non-MSME invoices a week late. Micro and small suppliers are never paid late."""
        cum: dict[tuple[str, str, str], Decimal] = defaultdict(Decimal)
        runs: dict[date, list[dict[str, Any]]] = defaultdict(list)
        for row in sorted((r for r in self.invoices if r["status"] != "cancelled"), key=lambda r: r["received_at"]):
            draft, s = row["_draft"], self.s.sup[row["supplier_code"]]
            booked = date.fromisoformat(row["received_on"])
            category = row["_lines"][0][0].item["category_code"]
            section = self.s.cats[category]["default_tds_section"]
            key = (s["code"], fy(booked), section)
            sec, base, rate, tds = tds_on(section, D(row["taxable"]), D(draft["po"]["taxable"]), cum[key], booked, s)
            cum[key] += D(row["taxable"])
            row["_tds"], row["_section"] = tds, sec or ""
            if sec:
                self.s.add("tds_entry", {"invoice_no": row["no"], "payment_no": "", "section": section_label(sec, booked),
                                         "base": money(base), "rate": str(rate), "amount": money(tds), "booked_on": iso(booked),
                                         "quarter": quarter_of(booked)}, datetime.fromisoformat(row["received_at"][:19]))  # fmt: skip
            due = due_date(
                self.terms[draft["po"]["payment_terms_code"]],
                s,
                date.fromisoformat(row["invoice_date"]),
                draft["accepted_on"],
            )
            row["due_date"], row["_msme"] = iso(due), s["msme_category"] in ("micro", "small")
            if row["_ready"] is None:
                continue
            ready = row["_ready"].date() + timedelta(days=1)
            want = run_on_or_before(due, self.s.cal, self.s.loc, self.weekday)
            if want < ready:
                want = run_after(ready, self.s.cal, self.s.loc, self.weekday)
            elif not row["_msme"] and self.R.bern(0.15):
                want = run_after(want + timedelta(days=7), self.s.cal, self.s.loc, self.weekday)
            if want <= self.s.cutoff:
                runs[want].append(row)
        approver = self.s.chain(self.s.rule("payment_run", ZERO, None), "ANY")[0]
        for day in sorted(runs):
            proposed = dt_at(self.s.cal.prev_working(day - timedelta(days=1), self.s.loc), 15)
            approved = proposed + timedelta(hours=1.5)
            run_no = self.s.num.next("payment_run", day)
            self.s.add("payment_run", {"no": run_no, "run_date": iso(day), "company_bank_account": "CBA-01",
                                       "proposed_by": self.s.people["payer"], "approved_by": approver,
                                       "approved_at": ts(approved), "status": "paid"}, dt_at(day, 12))  # fmt: skip
            self.s.add("approval_event", {"document": "payment_run", "document_no": run_no, "step": 1, "approver": approver,
                                          "on_behalf_of": "", "rule_code": self.s.rule("payment_run", ZERO, None)["rule_code"],
                                          "action": "approved", "at": ts(approved), "comment": ""}, approved)  # fmt: skip
            for row in sorted(runs[day], key=lambda r: r["no"]):
                self.payment(row, run_no, day)

    def payment(self, row: dict[str, Any], run_no: str, day: date) -> None:
        net = D(row["total"]) - row["_tds"]
        no = self.s.num.next("payment", day)
        bank = self.s.m["company_bank_account"][0]["bank_code"]
        utr = f"{bank[:4]}{day:%y%m%d}{self.R.randint(10**7, 10**8 - 1)}"
        when = dt_at(day, 14)
        self.s.add("payment", {"no": no, "run_no": run_no, "type": "invoice", "supplier_code": row["supplier_code"],
                               "po_no": row["_draft"]["po"]["no"], "amount": money(net), "tds_amount": money(row["_tds"]),
                               "msme_interest": "0.00", "mode": "rtgs" if net > 200000 else "neft", "reference": row["no"],
                               "utr": utr, "paid_on": iso(day), "remittance_email_ref": ""}, when)  # fmt: skip
        self.s.add("payment_allocation", {"payment_no": no, "invoice_no": row["no"], "amount": money(net)}, when)
        self.s.add("bank_statement_line", {"account": "CBA-01", "value_date": iso(day), "amount": money(-net), "utr": utr,
                                           "reference": row["no"], "narration": f"NEFT/RTGS {self.s.sup[row['supplier_code']]['legal_name'][:22].upper()}",
                                           "matched_payment_no": no}, dt_at(day, 15))  # fmt: skip
        row["_paid"] = day

    def bank_charges(self) -> None:
        """A bank charge at each month end: a statement line no payment explains."""
        d = self.s.start
        while d <= self.s.cutoff:
            nxt = date(d.year + d.month // 12, d.month % 12 + 1, 1)
            day = self.s.cal.prev_working(min(nxt - timedelta(days=1), self.s.cutoff), self.s.loc)
            amount = -q2(D(self.R.randint(118, 590)))
            self.s.add("bank_statement_line", {"account": "CBA-01", "value_date": iso(day), "amount": money(amount), "utr": "",
                                               "reference": "BANKCHG", "narration": "BANK CHARGES", "matched_payment_no": ""},
                       dt_at(day, 23))  # fmt: skip
            self.s.case("unmatched_bank_line", f"CBA-01;{iso(day)};{money(amount)}", "Bank charges on the statement",
                        "No payment matches it; booked as a bank charge", day)  # fmt: skip
            d = nxt
