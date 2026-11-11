"""Closing a company's history at the cutoff: what happened later is dropped, every document gets the status it had at
the cutoff, the answer keys are worked out, documents are numbered in date order, and the files are written in the
formats of the older datasets (history/*.csv and truth/*.csv), plus the planning history in inputs/history/.
"""

from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from common import D, iso, money, qty_s
from finance import renumber
from history import History
from history_buying import ZERO
from history_money import Money
from master_files import write_csv, write_table
from simulate import dt_at

# the files of history/ with their headers, as the older datasets have them, plus the suppliers' statements
HISTORY = {
    "request": "no,requester,on_behalf_of,department_code,ship_to_location,type,priority,project_code,created_at,need_by,channel,source_ref,justification,preferred_supplier,status,total_value",
    "request_line": "request_no,line_no,item_code,free_text,attributes,qty,uom,est_price,need_by,delivery_location,cost_centre_code,gl_code,category_code,project_code,status",
    "approval_event": "document,document_no,step,approver,on_behalf_of,rule_code,action,at,comment",
    "rfq": "no,buyer,created_at,due_date,type,weights,delivery_terms,payment_terms_proposed,terms_version,status,sent_at",
    "rfq_line": "rfq_no,line_no,request_no,request_line_no,item_code,description,attributes,qty,uom,need_by",
    "rfq_supplier": "rfq_no,supplier_code,invited_at,reminders_sent,responded_at,declined,decline_reason",
    "quote": "no,rfq_no,supplier_code,revision,received_at,valid_until,payment_terms_code,delivery_terms,packing_forwarding,freight,taxes_extra,warranty_months,deviations,document_ref",
    "quote_line": "quote_no,rfq_line_no,item_code,offered_brand,qty,moq,unit_price,discount_pct,gst_rate,delivery_days,deviation",
    "award": "rfq_no,rfq_line_no,supplier_code,quote_no,qty,price,reason,overridden,by_user,at",
    "po": "no,po_type,supplier_code,supplier_gstin,buyer_gstin,ship_to_location,buyer,project_code,contract_code,created_at,approved_at,sent_at,payment_terms_code,advance_pct,delivery_terms,freight_terms,terms_version,version,taxable,cgst,sgst,igst,total,status,cancel_reason",
    "po_line": "po_no,line_no,request_no,request_line_no,item_code,description,qty,uom,unit_price,gst_rate,cgst,sgst,igst,need_by,promised_date,received_qty,invoiced_qty,cost_centre_code,gl_code,status",
    "po_event": "po_no,type,at,field,old_value,new_value,email_ref,applied_by",
    "po_schedule": "po_no,line_no,delivery_date,qty",
    "po_charge": "po_no,type,amount,gst_rate,sac",
    "goods_receipt": "no,po_no,location_code,gate_entry_at,received_by,delivery_challan_no,supplier_invoice_ref,eway_bill_no,vehicle_no,inspection_status,accepted_on,remarks",
    "gr_line": "gr_no,po_no,po_line_no,qty_received,qty_accepted,qty_rejected,reject_reason",
    "return_to_vendor": "no,gr_no,item_code,qty,reason,debit_note_no,at",
    "service_receipt": "no,po_no,po_line_no,period_from,period_to,description,amount_accepted,accepted_by,accepted_on",
    "stock_movement": "id,item_code,location_code,date,type,qty,unit_cost,cost_centre_code,ref_doc",
    "invoice": "no,type,supplier_code,supplier_gstin,buyer_gstin,place_of_supply,supplier_invoice_no,invoice_date,fy,irn,ack_no,ack_date,signed_qr_ref,eway_bill_no,reverse_charge,received_at,received_on,channel,taxable,cgst,sgst,igst,cess,round_off,total,ref_invoice_no,due_date,payment_block,status,document_ref,document_hash",
    "invoice_line": "invoice_no,line_no,po_no,po_line_no,description,hsn_sac,qty,uom,unit_price,discount,taxable,gst_rate,cgst,sgst,igst",
    "invoice_charge": "invoice_no,type,amount,gst_rate,sac",
    "tds_entry": "invoice_no,payment_no,section,base,rate,amount,booked_on,quarter",
    "tds_challan": "period,section,amount,paid_on,bsr_code,challan_no",
    "payment_run": "no,run_date,company_bank_account,proposed_by,approved_by,approved_at,status",
    "payment": "no,run_no,type,supplier_code,po_no,amount,tds_amount,msme_interest,mode,reference,utr,paid_on,remittance_email_ref",
    "payment_allocation": "payment_no,invoice_no,amount",
    "advance_adjustment": "advance_payment_no,invoice_no,amount",
    "bank_statement_line": "account,value_date,amount,utr,reference,narration,matched_payment_no",
    "email_message": "id,message_id,in_reply_to,thread_id,mailbox,direction,from_addr,to_addr,subject,body_ref,attachments,at,plus_tag,linked_doc_type,linked_doc_no,kind,style,supplier_code",
    "vendor_statement_line": "supplier_code,statement_date,supplier_invoice_no,invoice_date,amount,our_invoice_no",
}
TRUTH = {
    "truth_case": "case_id,type,documents,cause,expected_result,planted_on",
    "truth_match_expected": "invoice_no,result,reason_codes,planted",
    "truth_shortfall": "material_request_no,line_no,item_code,needed,on_hand,on_order,held_for_earlier,reserved,short,request_no,request_line_no",
    "truth_combined_lines": "po_no,po_line_no,request_no,request_line_no,item_code,qty,route",
    "truth_supplier_selection": "rfq_no,rfq_line_no,item_code,qty,value_counted,quotes_required,suppliers_asked,options,selected,reason",
    "truth_grni": "month_end,receipt_no,po_no,po_line_no,item_code,qty_accepted,qty_invoiced,qty_not_invoiced,value_not_invoiced",
    "truth_payment": "invoice_no,supplier_code,total,tds_section,tds_amount,net_payable,due_date,msme,status_at_cutoff,paid_on",
}
# inputs/history/: the planning files of inputs/ over the year, each with a status; material requests are new
PLANNING = {
    "sales_order": "order_no,customer,product_code,qty,need_date,status",
    "demand_plan": "plan_no,product_code,qty,period,status",
    "production_order": "order_no,product_code,qty,need_by,reason,status",
    "production_order_line": "order_no,item_code,qty,unit,need_by,status",
    "joiner_plan": "plan_no,department,kit,count,start_date,status",
    "material_request": "mr_no,source,department,requested_by,raised_on,need_by,status",
    "material_request_line": "mr_no,line_no,item_code,qty,unit,issued_on,status",
}


class Closing:
    def __init__(self, hist: History, books: Money) -> None:
        self.s, self.money = hist, books
        self.T = hist.tables
        self.end = dt_at(hist.cutoff, 23.99)

    def run(self) -> dict[str, list[dict[str, Any]]]:
        for name, rows in self.s.planned.files.items():
            self.T[name] = [{**r, "_t": dt_at(self.s.start, 0)} for r in rows]
        self.keys_before_cutoff()
        self.cut()
        self.statuses()
        self.grni()
        renumber(self.T)
        for n, r in enumerate(sorted(self.T["stock_movement"], key=lambda r: (r["date"], r["_t"], r["item_code"])), 1):
            r["id"] = f"SM-{n:07d}"
        self.T["truth_case"].sort(key=lambda r: (r["_t"], r["type"], r["documents"]))
        for n, r in enumerate(self.T["truth_case"], 1):
            r["case_id"] = f"CASE-{n:05d}"
        return self.T

    def keys_before_cutoff(self) -> None:
        """The answer keys that follow invoices and material requests, and the tax deposits, before anything is cut."""
        for row in self.money.invoices:
            when = datetime.fromisoformat(row["received_at"][:19])
            self.T["truth_match_expected"].append({"invoice_no": row["no"], **row["_truth"], "_t": when})
            if row["status"] == "cancelled":
                continue
            tds = row.get("_tds", ZERO)
            self.T["truth_payment"].append(
                {"invoice_no": row["no"], "supplier_code": row["supplier_code"], "total": row["total"],
                 "tds_section": row.get("_section", ""), "tds_amount": money(tds), "net_payable": money(D(row["total"]) - tds),
                 "due_date": row["due_date"], "msme": str(row.get("_msme", False)).lower(), "status_at_cutoff": "",
                 "paid_on": iso(row["_paid"]) if row.get("_paid") else "", "_t": when, "_row": row}
            )  # fmt: skip
        for (mr, n), line in sorted(self.s.mr_lines.items()):
            raised = next(r["_t"] for r in self.T["material_request"] if r["mr_no"] == mr)
            self.T["truth_shortfall"].append({"material_request_no": mr, "line_no": n, "item_code": line["item_code"],
                                              "needed": line["qty"], **{k: line[k] for k in ("on_hand", "on_order", "held_for_earlier",
                                              "reserved", "short", "request_no", "request_line_no")}, "_t": raised})  # fmt: skip
            self.T["material_request_line"].append({**{k: line[k] for k in ("item_code", "qty", "unit", "issued_on", "status")},
                                                    "mr_no": mr, "line_no": n, "_t": raised})  # fmt: skip
        for mr in self.T["material_request"]:
            lines = [ln for ln in self.T["material_request_line"] if ln["mr_no"] == mr["mr_no"]]
            mr["status"] = "issued" if all(ln["status"] == "issued" for ln in lines) else "open"
        for c in self.s.cases:
            self.T["truth_case"].append({"case_id": "", **c})
        self.challans()

    def challans(self) -> None:
        """Tax deducted in a month is deposited by the 7th of the next month, one challan per section."""
        due: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
        for t in self.T["tds_entry"]:  # the 2025 Act's labels keep the old section in brackets: "... [194C]"
            code = next(c for c in ("194Q", "194C", "194J") if c in t["section"])
            due[(t["booked_on"][:7], code)] += D(t["amount"])
        for n, ((period, code), amount) in enumerate(sorted(due.items()), 1):
            y, m = int(period[:4]), int(period[5:])
            paid = self.s.cal.prev_working(date(y + m // 12, m % 12 + 1, 7), self.s.loc)
            if amount > 0 and paid <= self.s.cutoff:
                self.T["tds_challan"].append({"period": period, "section": code, "amount": money(amount), "paid_on": iso(paid),
                                              "bsr_code": "0510308", "challan_no": f"{(n * 7919) % 90000 + 10000}",
                                              "_t": dt_at(paid, 13)})  # fmt: skip

    def cut(self) -> None:
        """Rows of events after the cutoff are dropped; times after it are blanked, and the statuses follow."""
        for name in list(self.T):
            self.T[name] = [r for r in self.T[name] if r["_t"] <= self.end]
        late = self.end.isoformat()[:16]
        for po in self.T["po"]:
            if po["approved_at"][:16] > late:
                po["approved_at"] = po["sent_at"] = ""
            elif po["sent_at"][:16] > late:
                po["sent_at"] = ""
        for r in self.T["rfq"]:
            if r["sent_at"][:16] > late:
                r["sent_at"] = ""
        for r in self.T["rfq_supplier"]:
            if r["responded_at"][:16] > late:
                r["responded_at"] = ""

    def statuses(self) -> None:
        """Each document as it stood at the cutoff."""
        T = self.T
        paid = {a["invoice_no"] for a in T["payment_allocation"]}
        kept = {id(r) for r in T["invoice"]}
        for inv in self.money.invoices:
            if inv["status"] in ("cancelled", "blocked") or id(inv) not in kept:
                continue
            ready = inv["_ready"]
            inv["status"] = "paid" if inv["no"] in paid else "approved" if ready and ready <= self.end else "received"
        for t in T["truth_payment"]:
            t["status_at_cutoff"] = t.pop("_row")["status"]
        got: dict[tuple[str, int], Decimal] = defaultdict(Decimal)
        for g in T["gr_line"]:
            got[(g["po_no"], g["po_line_no"])] += D(g["qty_accepted"])
        served = {(s["po_no"], s["po_line_no"]) for s in T["service_receipt"]}
        billed: dict[tuple[str, int], Decimal] = defaultdict(Decimal)
        live = {i["no"] for i in T["invoice"] if i["status"] != "cancelled"}
        for ln in T["invoice_line"]:
            if ln["invoice_no"] in live:
                billed[(ln["po_no"], ln["po_line_no"])] += D(ln["qty"])
        lines: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for pl in T["po_line"]:
            key = (pl["po_no"], pl["line_no"])
            received = D(pl["qty"]) if key in served else got[key]
            pl["received_qty"], pl["invoiced_qty"] = qty_s(received), qty_s(billed[key])
            pl["status"] = "received" if received >= D(pl["qty"]) else "part_received" if received > 0 else "open"
            lines[pl["po_no"]].append(pl)
        acked = {e["po_no"] for e in T["po_event"] if e["type"] == "acknowledged"}
        invoices_of: dict[str, list[str]] = defaultdict(list)
        status_of = {i["no"]: i["status"] for i in T["invoice"]}
        for ln in T["invoice_line"]:
            if ln["invoice_no"] in live and ln["invoice_no"] not in invoices_of[ln["po_no"]]:
                invoices_of[ln["po_no"]].append(ln["invoice_no"])
        for po in T["po"]:
            pls = lines[po["no"]]
            if not po["approved_at"]:
                po["status"] = "draft"
            elif not po["sent_at"]:
                po["status"] = "approved"
            elif all(pl["status"] == "received" for pl in pls):
                bills = invoices_of[po["no"]]
                po["status"] = "closed" if bills and all(status_of[b] == "paid" for b in bills) else "received"
            elif any(pl["status"] != "open" for pl in pls):
                po["status"] = "part_received"
            else:
                po["status"] = "acknowledged" if po["no"] in acked else "sent"
        po_status = {p["no"]: p["status"] for p in T["po"]}
        ordered: dict[tuple[str, int], list[str]] = defaultdict(list)
        for c in T["truth_combined_lines"]:
            ordered[(c["request_no"], c["request_line_no"])].append(c["po_no"])
        waiting = {(n.request_no, n.line_no) for n in self.s.waiting}
        by_request: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for rl in T["request_line"]:
            key = (rl["request_no"], rl["line_no"])
            rl["status"] = "ordered" if ordered.get(key) else "open" if key in waiting else "sourcing"
            by_request[rl["request_no"]].append(rl)
        decided = {e["document_no"] for e in T["approval_event"] if e["document"] == "request"}
        for rq in T["request"]:
            if self.s.approved_at[rq["no"]] > self.end or rq["no"] not in decided:
                rq["status"] = "submitted"
                continue
            done = all(
                po_status.get(p) == "closed"
                for rl in by_request[rq["no"]]
                for p in ordered.get((rl["request_no"], rl["line_no"]), [None])
            )
            rq["status"] = "closed" if done else "approved"
        awarded = {a["rfq_no"] for a in T["award"]}
        for r in T["rfq"]:
            r["status"] = "awarded" if r["no"] in awarded else "sent"

    def grni(self) -> None:
        """Goods (and services) accepted but not covered by a valid invoice received by each month end."""
        T, kept = self.T, {id(r) for r in self.T["invoice"]}
        money_rows = [i for i in self.money.invoices if id(i) in kept and i["status"] != "cancelled"]
        receipts = {id(r) for r in T["goods_receipt"] + T["service_receipt"]}
        price = {(pl["po_no"], pl["line_no"]): (D(pl["unit_price"]), pl["item_code"]) for pl in T["po_line"]}
        d = self.s.start
        while d <= self.s.cutoff:
            nxt = date(d.year + d.month // 12, d.month % 12 + 1, 1)
            me = min(nxt - timedelta(days=1), self.s.cutoff)
            for draft in self.s.drafts:
                doc = draft["doc"]
                if id(doc) not in receipts or draft["accepted_on"] > me:
                    continue
                bills = [i for i in money_rows if i["_draft"] is draft and i["received_on"] <= iso(me)]
                for o, qty in draft["lines"]:
                    billed = max((q for i in bills for x, q, _, _ in i["_lines"] if x is o), default=ZERO)
                    left = qty - min(billed, qty)
                    if left > 0:
                        unit, item = price[(o.line["po_no"], o.line["line_no"])]
                        T["truth_grni"].append({"month_end": iso(me), "receipt_no": doc["no"], "po_no": o.line["po_no"],
                                                "po_line_no": o.line["line_no"], "item_code": item, "qty_accepted": qty_s(qty),
                                                "qty_invoiced": qty_s(min(billed, qty)), "qty_not_invoiced": qty_s(left),
                                                "value_not_invoiced": money(unit * left), "_t": dt_at(me, 23.9)})  # fmt: skip
            d = nxt


def write(out: Path, tables: dict[str, list[dict[str, Any]]], opening: dict[str, Decimal], start: date, loc: str,
          cost: dict[str, str]) -> dict[str, int]:  # fmt: skip
    """history/, truth/ and inputs/history/ in their formats, and the opening stock at the start of the history."""
    counts = {}
    order = {"stock_movement": lambda r: r["id"]}
    for folder, headers in (("history", HISTORY), ("truth", TRUTH), ("inputs/history", PLANNING)):
        for name, header in headers.items():
            if folder == "inputs/history" and name not in tables:
                continue  # a company writes only the planning files it has
            rows = sorted(tables.get(name, []), key=order.get(name, lambda r: r["_t"]))
            clean = [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows]
            write_csv(out / folder / f"{name}.csv", header.split(","), clean)
            counts[f"{folder}/{name}"] = len(clean)
    write_table(out, "stock_opening", [{"item_code": item, "location_code": loc, "qty": qty_s(qty), "unit_cost": cost[item],
                                        "as_of": iso(start)} for item, qty in opening.items()])  # fmt: skip
    return counts
