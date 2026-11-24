"""Checks of a company's history, worked out again from the files alone: references, GST, TDS and totals to the paisa,
stock, dates and working days, segregation of duties, and each planted problem the number of times truth_case says.

TDS is recomputed from data/reference/tds_sections.csv (rates and thresholds), not with the generator's own rule.
"""

from collections import Counter, defaultdict
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from common import Calendar
from history_close import HISTORY, PLANNING, TRUTH
from master_files import read, tolerance_on

REFERENCE = Path(__file__).resolve().parents[2] / "data" / "reference"
CENT = Decimal("0.01")


def q2(x: Decimal) -> Decimal:
    return x.quantize(CENT, ROUND_HALF_UP)


class HistoryCheck:
    def __init__(self, out: Path, profile: Any) -> None:
        self.out, self.p, self.h = out, profile, profile["history"]
        self.problems: list[str] = []
        self.m = {p.stem: read(p) for p in (out / "master").glob("*.csv")}
        self.t = {name: read(out / "history" / f"{name}.csv") for name in HISTORY}
        self.k = {name: read(out / "truth" / f"{name}.csv") for name in TRUTH}
        self.plan = {p.stem: read(p) for p in (out / "inputs" / "history").glob("*.csv")}
        self.start, self.cutoff = date.fromisoformat(self.h["start"]), date.fromisoformat(self.h["cutoff"])
        self.loc = self.m["location"][0]
        self.cal = Calendar(self.m["location"])
        self.sup = {r["code"]: r for r in self.m["supplier"]}
        self.items = {r["code"]: r for r in self.m["item"]}
        self.line_item = {(r["po_no"], r["line_no"]): r["item_code"] for r in self.t["po_line"]}

    def bad(self, text: str) -> None:
        if len(self.problems) < 200:
            self.problems.append(text)

    def run(self) -> list[str]:
        for check in (self.files, self.references, self.money, self.tds, self.stock, self.dates, self.duties,
                      self.planted, self.closed_items, self.not_trading):  # fmt: skip
            if not self.problems:
                check()
        return self.problems

    # ---- shape and references ---------------------------------------------------------------------

    def files(self) -> None:
        for folder, headers in (("history", HISTORY), ("truth", TRUTH)):
            for name, header in headers.items():
                with (self.out / folder / f"{name}.csv").open(encoding="utf-8") as f:
                    if f.readline().strip() != header:
                        self.bad(f"{folder}/{name}.csv header differs from the standard one")
        for name in self.plan:
            with (self.out / "inputs" / "history" / f"{name}.csv").open(encoding="utf-8") as f:
                if name not in PLANNING or f.readline().strip() != PLANNING[name]:
                    self.bad(f"inputs/history/{name}.csv is not a planning file of the standard shape")

    def not_trading(self) -> None:
        """A prospect or a blocked supplier never quotes, receives an order or sends an invoice in the history."""
        idle = {code for code, s in self.sup.items() if s["status"] != "active"}
        for table in ("rfq_supplier", "quote", "po", "invoice"):
            for r in self.t[table]:
                if r["supplier_code"] in idle:
                    self.bad(f"{table} names {r['supplier_code']}, which is {self.sup[r['supplier_code']]['status']}")

    def references(self) -> None:
        t, k = self.t, self.k
        keys = {
            "user": {r["code"] for r in self.m["user"]} | {"SYSTEM"},
            "item": set(self.items), "supplier": set(self.sup),
            "request": {r["no"] for r in t["request"]},
            "request_line": {(r["request_no"], r["line_no"]) for r in t["request_line"]},
            "po": {r["no"] for r in t["po"]}, "po_line": {(r["po_no"], r["line_no"]) for r in t["po_line"]},
            "rfq": {r["no"] for r in t["rfq"]}, "quote": {r["no"] for r in t["quote"]},
            "goods_receipt": {r["no"] for r in t["goods_receipt"]},
            "service_receipt": {r["no"] for r in t["service_receipt"]},
            "invoice": {r["no"] for r in t["invoice"]}, "payment": {r["no"] for r in t["payment"]},
            "payment_run": {r["no"] for r in t["payment_run"]}, "contract": {r["code"] for r in self.m["contract"]},
            "cost_centre": {r["code"] for r in self.m["cost_centre"]}, "gl": {r["code"] for r in self.m["gl_account"]},
            "mr": {r["mr_no"] for r in self.plan.get("material_request", [])},
        }  # fmt: skip
        keys["receipt"] = keys["goods_receipt"] | keys["service_receipt"]
        links = [
            ("request", "requester", "user"), ("request_line", ("request_no",), "request"), ("request_line", "item_code", "item"),
            ("request_line", "cost_centre_code", "cost_centre"), ("request_line", "gl_code", "gl"),
            ("approval_event", "approver", "user"), ("rfq_line", "rfq_no", "rfq"),
            ("rfq_line", ("request_no", "request_line_no"), "request_line"), ("rfq_supplier", "rfq_no", "rfq"),
            ("rfq_supplier", "supplier_code", "supplier"), ("quote", "rfq_no", "rfq"), ("quote_line", "quote_no", "quote"),
            ("award", "quote_no", "quote"), ("po", "supplier_code", "supplier"), ("po", "contract_code", "contract"),
            ("po_line", "po_no", "po"), ("po_line", ("request_no", "request_line_no"), "request_line"),
            ("po_line", "item_code", "item"), ("po_event", "po_no", "po"), ("po_schedule", ("po_no", "line_no"), "po_line"),
            ("goods_receipt", "po_no", "po"), ("goods_receipt", "received_by", "user"), ("gr_line", "gr_no", "goods_receipt"),
            ("gr_line", ("po_no", "po_line_no"), "po_line"), ("service_receipt", ("po_no", "po_line_no"), "po_line"),
            ("return_to_vendor", "gr_no", "goods_receipt"), ("invoice", "supplier_code", "supplier"),
            ("invoice_line", "invoice_no", "invoice"), ("invoice_line", ("po_no", "po_line_no"), "po_line"),
            ("tds_entry", "invoice_no", "invoice"), ("payment", "run_no", "payment_run"), ("payment", "reference", "invoice"),
            ("payment_allocation", "payment_no", "payment"), ("payment_allocation", "invoice_no", "invoice"),
            ("bank_statement_line", "matched_payment_no", "payment"), ("stock_movement", "item_code", "item"),
            ("vendor_statement_line", "our_invoice_no", "invoice"),
        ]  # fmt: skip
        for table, col, target in links:
            cols = col if isinstance(col, tuple) else (col,)
            for r in t[table]:
                value = tuple(r[c] for c in cols) if len(cols) > 1 else r[cols[0]]
                if (value if isinstance(value, str) else value[0]) and value not in keys[target]:
                    self.bad(f"{table}.{'/'.join(cols)} '{value}' is not in {target}")
        for r in t["stock_movement"]:
            if r["ref_doc"] and r["ref_doc"] not in keys["goods_receipt"] | keys["mr"] | keys["request"] | {
                "month-end issue"
            }:
                self.bad(f"stock movement {r['id']} refers to '{r['ref_doc']}', which is no document")
        truth_links = [
            ("truth_match_expected", "invoice_no", "invoice"), ("truth_payment", "invoice_no", "invoice"),
            ("truth_combined_lines", ("po_no", "po_line_no"), "po_line"),
            ("truth_combined_lines", ("request_no", "request_line_no"), "request_line"),
            ("truth_supplier_selection", "rfq_no", "rfq"), ("truth_grni", "receipt_no", "receipt"),
            ("truth_shortfall", "material_request_no", "mr"), ("truth_shortfall", ("request_no", "request_line_no"), "request_line"),
        ]  # fmt: skip
        for table, col, target in truth_links:
            cols = col if isinstance(col, tuple) else (col,)
            for r in k[table]:
                value = tuple(r[c] for c in cols) if len(cols) > 1 else r[cols[0]]
                if (value if isinstance(value, str) else value[0]) and value not in keys[target]:
                    self.bad(f"{table}.{'/'.join(cols)} '{value}' is not in {target}")
        every = set().union(*(v for v in keys.values() if all(isinstance(x, str) for x in v)))
        for c in k["truth_case"]:
            for part in c["documents"].split(";"):
                if part[:3] in ("REQ", "PO-", "GRN", "PI-", "SRN") and part not in every:
                    self.bad(f"case {c['case_id']} names {part}, which is no document")
        orders = {r["order_no"] for r in self.plan.get("production_order", [])}
        reasons = {r["order_no"] for r in self.plan.get("sales_order", [])} | {
            r["plan_no"] for r in self.plan.get("demand_plan", [])
        }
        for r in self.plan.get("production_order", []):
            if r["reason"] not in reasons:
                self.bad(f"production order {r['order_no']} is for {r['reason']}, which is no sales order or plan")
        for r in self.plan.get("production_order_line", []):
            if r["order_no"] not in orders:
                self.bad(f"production order line names {r['order_no']}, which is no production order")
        plans = {f"{r['plan_no']}/{r['department']}" for r in self.plan.get("joiner_plan", [])}
        for r in self.plan.get("material_request", []):
            if r["source"] not in orders | plans | {"replacement"}:
                self.bad(f"material request {r['mr_no']} is for {r['source']}, which is no order or plan")
        for r in self.plan.get("material_request_line", []):
            if r["mr_no"] not in keys["mr"]:
                self.bad(f"material request line names {r['mr_no']}, which is no material request")

    # ---- money ------------------------------------------------------------------------------------

    def money(self) -> None:
        """GST and totals of orders and invoices worked out again; payments, allocations and bank lines agree."""
        state = self.loc["state_code"]
        po_of = {r["no"]: r for r in self.t["po"]}
        planted_gst = {r["invoice_no"] for r in self.k["truth_match_expected"] if r.get("planted") == "gst_mismatch"}
        for kind, head, lines, key in (("po", self.t["po"], self.t["po_line"], "po_no"),
                                       ("invoice", self.t["invoice"], self.t["invoice_line"], "invoice_no")):  # fmt: skip
            sums: dict[str, list[Decimal]] = defaultdict(lambda: [Decimal(0)] * 4)
            for ln in lines:
                h = po_of[ln["po_no"]]
                value = q2(Decimal(ln["qty"]) * Decimal(ln["unit_price"]))
                if kind == "invoice" and ln[key] in planted_gst:
                    # a planted GST problem is meant to disagree with the order; its own amounts still add up
                    s = sums[ln[key]]
                    s[0], s[1], s[2], s[3] = (
                        s[0] + value,
                        s[1] + Decimal(ln["cgst"]),
                        s[2] + Decimal(ln["sgst"]),
                        s[3] + Decimal(ln["igst"]),
                    )
                    continue
                item = ln["item_code"] if kind == "po" else self.line_item[(ln["po_no"], ln["po_line_no"])]
                rate = self.rate(item, h["created_at"][:10])
                if Decimal(ln["gst_rate"]) != rate:
                    self.bad(f"{kind} {ln[key]}: GST rate {ln['gst_rate']} is not the item's rate {rate}")
                intra = self.sup[h["supplier_code"]]["state_code"] == state
                c = q2(value * rate / 200) if intra else Decimal(0)
                i = Decimal(0) if intra else q2(value * rate / 100)
                if (Decimal(ln["cgst"]), Decimal(ln["sgst"]), Decimal(ln["igst"])) != (c, c, i):
                    self.bad(
                        f"{kind} {ln[key]} line {ln['line_no']}: GST {ln['cgst']}/{ln['sgst']}/{ln['igst']} should be {c}/{c}/{i}"
                    )
                if kind == "invoice" and Decimal(ln["taxable"]) != value:
                    self.bad(f"invoice {ln[key]} line {ln['line_no']}: taxable {ln['taxable']} is not {value}")
                s = sums[ln[key]]
                s[0], s[1], s[2], s[3] = s[0] + value, s[1] + c, s[2] + c, s[3] + i
            for r in head:
                taxable, cgst, sgst, igst = sums[r["no"]]
                gross = taxable + cgst + sgst + igst
                total = gross.quantize(Decimal(1), ROUND_HALF_UP) if kind == "invoice" else gross
                got = [Decimal(r[c]) for c in ("taxable", "cgst", "sgst", "igst", "total")]
                if got != [taxable, cgst, sgst, igst, total]:
                    self.bad(f"{kind} {r['no']}: totals {got} should be {[taxable, cgst, sgst, igst, total]}")
                if kind == "invoice" and Decimal(r["round_off"]) != total - gross:
                    self.bad(f"invoice {r['no']}: round off {r['round_off']} should be {total - gross}")
        tds = {r["invoice_no"]: Decimal(r["amount"]) for r in self.t["tds_entry"]}
        invoice = {r["no"]: r for r in self.t["invoice"]}
        alloc = {a["payment_no"]: a for a in self.t["payment_allocation"]}
        bank = {b["matched_payment_no"]: b for b in self.t["bank_statement_line"] if b["matched_payment_no"]}
        for p in self.t["payment"]:
            net = Decimal(invoice[p["reference"]]["total"]) - tds.get(p["reference"], Decimal(0))
            if Decimal(p["amount"]) != net or Decimal(p["tds_amount"]) != tds.get(p["reference"], Decimal(0)):
                self.bad(f"payment {p['no']}: {p['amount']} with TDS {p['tds_amount']} should be {net}")
            if Decimal(alloc[p["no"]]["amount"]) != net or Decimal(bank[p["no"]]["amount"]) != -net:
                self.bad(f"payment {p['no']}: its allocation or bank line is not {net}")
        for r in self.k["truth_payment"]:
            if Decimal(r["net_payable"]) != Decimal(r["total"]) - Decimal(r["tds_amount"]):
                self.bad(f"truth_payment {r['invoice_no']}: net is not total less TDS")

    def rate(self, item: str, on: str) -> Decimal:
        it = self.items[item]
        change = it["gst_rate_change_date"]
        return Decimal(it["gst_rate_after"] if not change or on >= change else it["gst_rate_before"])

    def tds(self) -> None:
        """Tax at source per invoice in booking order, from the reference table's rates and thresholds."""
        sections = read(REFERENCE / "tds_sections.csv")
        cats = {r["code"]: r for r in self.m["category"]}
        line_item = self.line_item
        po_taxable = {r["no"]: Decimal(r["taxable"]) for r in self.t["po"]}
        first_line = {}
        for ln in self.t["invoice_line"]:
            first_line.setdefault(ln["invoice_no"], ln)
        booked = {r["invoice_no"]: r for r in self.t["tds_entry"]}
        cum: dict[tuple[str, str, str], Decimal] = defaultdict(Decimal)
        for inv in sorted((r for r in self.t["invoice"] if r["status"] != "cancelled"), key=lambda r: r["received_at"]):
            ln = first_line[inv["no"]]
            section = cats[self.items[line_item[(ln["po_no"], ln["po_line_no"])]]["category_code"]][
                "default_tds_section"
            ]
            s, on, base = self.sup[inv["supplier_code"]], inv["received_on"], Decimal(inv["taxable"])
            fy = f"{int(on[:4]) - (on[5:7] < '04')}"
            key = (s["code"], fy, section)
            before, cum[key] = cum[key], cum[key] + base
            payee = "Individual/HUF" if s["pan"][3] in "PH" else "Others"
            row = next(r for r in sections if r["section"] == section and r["effective_from"] <= on
                       and (not r["effective_to"] or on <= r["effective_to"])
                       and (section != "194C" or r["payee_type"].startswith(payee[:5])))  # fmt: skip
            rate, single, annual = Decimal(row["rate_pct"]), row["threshold_single_inr"], row["threshold_annual_inr"]
            if section == "194Q":
                taxed = max(Decimal(0), cum[key] - max(Decimal(annual), before))
            elif cum[key] > Decimal(annual) or (single and po_taxable[ln["po_no"]] > Decimal(single)):
                taxed = base
            else:
                taxed = None
            want = q2(taxed * rate / 100) if taxed is not None else None
            got = booked.get(inv["no"])
            if want is None and got is not None:
                self.bad(f"invoice {inv['no']}: TDS booked although no threshold is crossed")
            elif want is not None and (got is None or Decimal(got["amount"]) != want or Decimal(got["rate"]) != rate):
                self.bad(f"invoice {inv['no']}: TDS should be {want} at {rate}%, booked {got and got['amount']}")

    # ---- stock, dates, duties -----------------------------------------------------------------------

    def stock(self) -> None:
        """Never below zero; the opening stock plus receipts less issues is the profile's stock at the cutoff."""
        level = {r["item_code"]: Decimal(r["qty"]) for r in self.m["stock_opening"]}
        for r in sorted(self.t["stock_movement"], key=lambda r: (r["date"], r["type"] != "receipt", r["id"])):
            level[r["item_code"]] += Decimal(r["qty"]) * (1 if r["type"] == "receipt" else -1)
            if level[r["item_code"]] < 0:
                self.bad(f"{r['item_code']} is below zero on {r['date']}")
        for it in self.p["items"]:
            if "on_hand" in it and level.get(it["code"], Decimal(0)) != Decimal(str(it["on_hand"])):
                self.bad(f"{it['code']} ends at {level.get(it['code'])}, the profile says {it['on_hand']}")

    def dates(self) -> None:
        """Inside the window and on working days; a receipt never before its order, an invoice never before its
        receipt, a payment never before its invoice."""
        loc = self.loc["code"]
        stamps = [("request", "created_at"), ("approval_event", "at"), ("po", "created_at"), ("po", "approved_at"),
                  ("rfq", "created_at"), ("goods_receipt", "gate_entry_at"), ("goods_receipt", "accepted_on"),
                  ("service_receipt", "accepted_on"), ("invoice", "received_on"), ("payment", "paid_on"),
                  ("payment_run", "run_date"), ("stock_movement", "date"), ("bank_statement_line", "value_date")]  # fmt: skip
        for table, col in stamps:
            for r in self.t[table]:
                if not r[col]:
                    continue
                d = date.fromisoformat(r[col][:10])
                if not self.start <= d <= self.cutoff:
                    self.bad(f"{table}.{col} {r[col]} is outside the history")
                elif not self.cal.is_working(d, loc):
                    self.bad(f"{table}.{col} {r[col]} is not a working day")
        po_at = {r["no"]: r["created_at"] for r in self.t["po"]}
        without = {c["documents"].split(";")[0] for c in self.k["truth_case"] if c["type"] == "receipt_without_po"}
        for g in self.t["goods_receipt"]:
            if g["gate_entry_at"] < po_at[g["po_no"]] and g["no"] not in without:
                self.bad(f"receipt {g['no']} is before its order {g['po_no']}")
        received = {}
        for g in self.t["goods_receipt"]:
            received[(g["po_no"], g["supplier_invoice_ref"])] = g["gate_entry_at"][:10]
        for s in self.t["service_receipt"]:
            received[(s["po_no"], s["po_line_no"])] = s["accepted_on"]
        first_line = {}
        for ln in self.t["invoice_line"]:
            first_line.setdefault(ln["invoice_no"], ln)
        for inv in self.t["invoice"]:
            ln = first_line[inv["no"]]
            got = received.get((ln["po_no"], inv["supplier_invoice_no"])) or received.get(
                (ln["po_no"], ln["po_line_no"])
            )
            if got and inv["received_on"] < got:
                self.bad(f"invoice {inv['no']} is booked before its receipt")
        booked = {r["no"]: r["received_on"] for r in self.t["invoice"]}
        for p in self.t["payment"]:
            if p["paid_on"] < booked[p["reference"]]:
                self.bad(f"payment {p['no']} is before its invoice")

    def duties(self) -> None:
        """For every order line: requester, approvers, receiver and payer are different people."""
        requester = {r["no"]: r["requester"] for r in self.t["request"]}
        approvers = defaultdict(set)
        for e in self.t["approval_event"]:
            if e["document"] == "request" and e["action"] == "approved":
                approvers[e["document_no"]].add(e["approver"])
        receivers = defaultdict(set)
        for g in self.t["goods_receipt"]:
            receivers[g["po_no"]].add(g["received_by"])
        for s in self.t["service_receipt"]:
            receivers[s["po_no"]].add(s["accepted_by"])
        proposer = {r["no"]: r["proposed_by"] for r in self.t["payment_run"]}
        payers = defaultdict(set)
        for p in self.t["payment"]:
            payers[p["po_no"]].add(proposer[p["run_no"]])
        for c in self.k["truth_combined_lines"]:
            who = [{requester[c["request_no"]]}, approvers[c["request_no"]], receivers[c["po_no"]], payers[c["po_no"]]]
            people = [p for group in who for p in group]
            if len(people) != len(set(people)):
                self.bad(f"order {c['po_no']}: one person holds two of requester, approver, receiver and payer")

    # ---- planted problems ---------------------------------------------------------------------------

    def planted(self) -> None:
        """Each planted problem appears in the data the number of times truth_case lists it."""
        listed = Counter(c["type"] for c in self.k["truth_case"])
        for kind, count in self.h["planted"].items():
            if listed[kind] != count:
                self.bad(f"{kind} is planted {listed[kind]} times, the profile says {count}")
        if listed["split_order"] != len(self.h.get("split_orders", [])):
            self.bad("split orders in truth_case differ from the profile")
        t = self.t
        po_at = {r["no"]: r["created_at"] for r in t["po"]}
        seen = {
            "duplicate_invoice": sum(1 for r in t["invoice"] if r["payment_block"] == "duplicate"),
            "unmatched_bank_line": sum(1 for r in t["bank_statement_line"] if not r["matched_payment_no"]),
            "invoice_not_received": sum(1 for r in t["vendor_statement_line"] if not r["our_invoice_no"]),
            "failed_inspection": sum(1 for r in t["gr_line"] if Decimal(r["qty_rejected"]) > 0),
            "supplier_date_change": len({e["po_no"] for e in t["po_event"] if e["type"] == "date_change"}),
            "receipt_without_po": sum(1 for g in t["goods_receipt"] if g["gate_entry_at"] < po_at[g["po_no"]]),
            "price_mismatch": sum(1 for r in self.k["truth_match_expected"]
                                  if r["reason_codes"] == "price_variance" and r["planted"] == "price_mismatch"),
            "qty_mismatch": sum(1 for r in self.k["truth_match_expected"]
                                if r["reason_codes"] == "qty_over_received" and r["planted"] == "qty_mismatch"),
            "tolerance_edge_qty": sum(1 for r in self.k["truth_match_expected"] if r["planted"] == "tolerance_edge_qty"),
            "tolerance_edge_price": sum(1 for r in self.k["truth_match_expected"] if r["planted"] == "tolerance_edge_price"),
            "part_delivery": self.part_deliveries(),
        }  # fmt: skip
        for kind, count in seen.items():
            if listed[kind] != count:
                self.bad(f"{kind}: truth_case lists {listed[kind]}, the data shows {count}")
        self.mismatches()
        grni = {r["receipt_no"] for r in self.k["truth_grni"]}
        for c in self.k["truth_case"]:
            if c["type"] == "grni_month_end" and c["documents"].split(";")[0] not in grni:
                self.bad(f"case {c['case_id']}: the receipt is not in truth_grni")

    def part_deliveries(self) -> int:
        """Orders delivered in more than one lot, not counting the lots sent again after a rejection."""
        lots: dict[str, int] = Counter(g["po_no"] for g in self.t["goods_receipt"] if not g["remarks"])
        return sum(1 for n in lots.values() if n > 1)

    def mismatches(self) -> None:
        """Price, quantity and GST per line judged again from the order, the receipt and the tolerances: every reason
        the line has, in the match's order, as in truth_match_expected."""
        price = {(r["po_no"], r["line_no"]): Decimal(r["unit_price"]) for r in self.t["po_line"]}
        rate = {(r["po_no"], r["line_no"]): Decimal(r["gst_rate"]) for r in self.t["po_line"]}
        grn_ref = {g["no"]: g["supplier_invoice_ref"].removesuffix("-R") for g in self.t["goods_receipt"]}
        accepted: dict[tuple[str, str, str], Decimal] = defaultdict(Decimal)
        for ln in self.t["gr_line"]:
            accepted[(grn_ref[ln["gr_no"]], ln["po_no"], ln["po_line_no"])] += Decimal(ln["qty_accepted"])
        found: dict[str, list[str]] = {}
        judged: dict[str, str] = {}  # the tolerance version in force on the day each invoice was received
        number = {r["no"]: r for r in self.t["invoice"]}
        supplier = {s["code"]: s for s in self.m["supplier"]}
        for ln in self.t["invoice_line"]:
            inv = number[ln["invoice_no"]]
            if inv["payment_block"] == "duplicate":
                continue
            item = self.items[self.line_item[(ln["po_no"], ln["po_line_no"])]]
            version, tol = tolerance_on(
                self.m["tolerance"], self.m["tolerance_version"], inv["received_on"], item["category_code"]
            )
            judged[inv["no"]] = str(version)
            ordered, qty = price[(ln["po_no"], ln["po_line_no"])], Decimal(ln["qty"])
            reasons = found.setdefault(inv["no"], [])
            diff = Decimal(ln["unit_price"]) - ordered
            if diff > 0 and not (diff <= ordered * tol["price_pct"] / 100 and diff * qty <= tol["price_abs"]):
                reasons.append("price_variance")
            got = accepted.get((inv["supplier_invoice_no"].removesuffix("-R"), ln["po_no"], ln["po_line_no"]))
            if got is not None and qty > got + got * tol["qty_pct"] / 100:
                reasons.append("qty_over_received")
            order_rate = rate[(ln["po_no"], ln["po_line_no"])]
            taxable = Decimal(ln["taxable"])
            if Decimal(ln["gst_rate"]) != order_rate:
                reasons.append("gst_mismatch")
            else:
                intra = supplier[inv["supplier_code"]]["state_code"] == self.loc["state_code"]
                want = (
                    (taxable * order_rate / 200).quantize(CENT, ROUND_HALF_UP),
                    (taxable * order_rate / 200).quantize(CENT, ROUND_HALF_UP),
                    Decimal("0.00"),
                ) if intra else (
                    Decimal("0.00"),
                    Decimal("0.00"),
                    (taxable * order_rate / 100).quantize(CENT, ROUND_HALF_UP),
                )  # fmt: skip
                tolerance = Decimal(tol.get("tax_line_abs", "1.00"))
                if any(
                    abs(Decimal(ln[k]) - w) > tolerance for k, w in zip(("cgst", "sgst", "igst"), want, strict=True)
                ):
                    reasons.append("gst_mismatch")
        for r in self.k["truth_match_expected"]:
            if judged.get(r["invoice_no"]) != r["tolerance_version"]:
                self.bad(
                    f"invoice {r['invoice_no']}: truth says tolerance version {r['tolerance_version']}, "
                    f"it was received under {judged.get(r['invoice_no'])}"
                )
            says = [x for x in str(r.get("reasons") or r["reason_codes"] or "").split(";") if x]
            if found.get(r["invoice_no"], []) != says:
                self.bad(
                    f"invoice {r['invoice_no']}: the data says '{';'.join(found.get(r['invoice_no'], []))}', "
                    f"truth says '{';'.join(says)}'"
                )

    def closed_items(self) -> None:
        """Nothing open at the cutoff for the materials the demo's first production order needs."""
        items = set(self.h.get("closed_at_cutoff", []))
        lines = {(r["po_no"], r["line_no"]): r for r in self.t["po_line"]}
        for r in self.t["request_line"]:
            if r["item_code"] in items and r["status"] != "ordered":
                self.bad(f"request {r['request_no']} for {r['item_code']} is still {r['status']} at the cutoff")
        for r in lines.values():
            if r["item_code"] in items and r["status"] != "received":
                self.bad(f"order {r['po_no']} for {r['item_code']} is still {r['status']} at the cutoff")
        for g in self.t["gr_line"]:
            if lines[(g["po_no"], g["po_line_no"])]["item_code"] in items and Decimal(g["qty_accepted"]) + Decimal(
                g["qty_rejected"]
            ) < Decimal(g["qty_received"]):
                self.bad(f"receipt {g['gr_no']} for a closed item is not yet accepted")


def history_problems(out: Path, profile: Any) -> list[str]:
    if not (out / "history").is_dir():
        return ["there is no history"]
    return HistoryCheck(out, profile).run()
