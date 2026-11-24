/**
 * The strip at the top of every case page: where this case is in the flow, and who acts next.
 *   Request > Approval > Price > Order > Receipt > Invoice > Payment
 * Steps before the current one are done, the current one is lit, the rest wait. Each page passes what it knows
 * (its status and who is being waited for); the wording of "who acts next" is decided here, once, so the pages agree.
 */
const FlowStrip = {
  STAGES: ["Request", "Approval", "Price", "Order", "Receipt", "Invoice", "Payment"],

  esc(value) {
    return String(value ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  },

  /** stage: 0..6 is the current step, 7 means everything is done; stopped: a short word ("Rejected") when the case ended early */
  html({ stage, note, stopped }) {
    const steps = this.STAGES.map((name, i) => {
      const state = stopped && i === stage ? "stopped" : i < stage ? "done" : i === stage ? "current" : "todo";
      const mark = state === "done" ? "✓" : state === "stopped" ? "✕" : String(i + 1);
      return `<li class="fs-step fs-${state}"${state === "current" ? ' aria-current="step"' : ""}><span class="fs-dot">${mark}</span><span class="fs-name">${name}</span></li>`;
    }).join("");
    const text = stopped ? `${this.esc(stopped)}${note ? ` · ${this.esc(note)}` : ""}` : note ? this.esc(note) : "";
    return `<div class="flow-strip"><ol class="fs-steps" aria-label="Where this case is in the flow">${steps}</ol>${
      text ? `<div class="fs-note"><strong>${stage >= 7 ? "Done" : "Now"}:</strong> ${text}</div>` : ""
    }</div>`;
  },

  forRequisition(r) {
    const s = String(r.status || "").toLowerCase();
    if (r.purchase_order && !["rejected", "cancelled"].includes(s)) {
      return this.html({ stage: 3, note: `Order ${r.purchase_order.id} has been made` });
    }
    if (s === "draft") return this.html({ stage: 0, note: "A draft: the person who raised it submits it for approval" });
    if (s === "returned") return this.html({ stage: 0, note: "Sent back to the person who raised it for changes" });
    if (s === "pending" || s === "submitted") {
      return this.html({ stage: 1, note: r.waiting_for ? `Waiting for ${r.waiting_for} to approve` : "Waiting for an approver" });
    }
    if (s === "rejected") return this.html({ stage: 1, stopped: "Rejected", note: "Nothing will be bought for this request" });
    if (s === "cancelled") return this.html({ stage: 0, stopped: "Cancelled" });
    if (s === "approved") return this.html({ stage: 2, note: "Approved: a buyer finds a price (a contract price, or quotations from suppliers)" });
    return this.html({ stage: 2, note: "A buyer is finding a price" });
  },

  forRfq(r) {
    const s = String(r.status || "").toLowerCase();
    if (s.includes("award") || s === "closed") return this.html({ stage: 3, note: "Awarded: the order is made from the chosen quotations" });
    if (s === "draft") return this.html({ stage: 2, note: "Not sent yet: a buyer sends the request for quotation to the suppliers" });
    return this.html({ stage: 2, note: "Waiting for the suppliers' quotations; the buyer compares them and awards" });
  },

  forOrder(o) {
    const s = String(o.status || "").toLowerCase();
    if (o.hold_reason && ["pending", "submitted", "draft", "approved"].includes(s)) {
      return this.html({ stage: 3, note: "Held until an approver accepts the price or contract limit" });
    }
    if (s === "draft") return this.html({ stage: 3, note: "Being prepared by the buyer" });
    if (s === "pending" || s === "submitted") {
      return this.html({ stage: 3, note: o.waiting_for ? `Waiting for ${o.waiting_for} to approve the order` : "Waiting for an approver" });
    }
    if (s === "approved") return this.html({ stage: 3, note: "Approved: a buyer sends it to the supplier" });
    if (s === "sent") return this.html({ stage: 3, note: "Sent: waiting for the supplier to confirm and give a delivery date" });
    if (s === "acknowledged") return this.html({ stage: 4, note: "Confirmed: waiting for the goods to arrive at the store" });
    if (s === "part_received" || s === "part received") return this.html({ stage: 4, note: "Part of the goods have arrived: the store receives the rest" });
    if (s === "received") return this.html({ stage: 5, note: "Goods received: waiting for the supplier's invoice" });
    if (s === "closed") return this.html({ stage: 7, note: "Received, invoiced and paid" });
    if (s === "cancelled" || s === "rejected") return this.html({ stage: 3, stopped: s === "rejected" ? "Rejected" : "Cancelled" });
    return this.html({ stage: 3 });
  },

  forInvoice(i) {
    const s = String(i.status || "").toLowerCase();
    if (s === "paid") return this.html({ stage: 7, note: "Paid" });
    if (s === "approved") return this.html({ stage: 6, note: "Matched and approved: finance puts it in the next payment run" });
    if (s === "blocked") return this.html({ stage: 5, note: "Blocked by the match: accounts fixes it, or asks another person to release it" });
    if (s === "cancelled") return this.html({ stage: 5, stopped: "Cancelled" });
    return this.html({ stage: 5, note: "Being matched to the order and the goods received" });
  },

  forRun(r) {
    const s = String(r.status || "").toLowerCase();
    if (s === "paid") return this.html({ stage: 7, note: "The bank has paid; the suppliers have their payment advice" });
    if (s === "proposed") return this.html({ stage: 6, note: "Waiting for the person the rule names to approve the run, with a code" });
    if (s === "approved") return this.html({ stage: 6, note: "Approved: finance makes the bank file" });
    if (s === "file_generated") return this.html({ stage: 6, note: "Bank file made: each payment is recorded, and the bank statement settles it" });
    if (s === "rejected" || s === "cancelled") return this.html({ stage: 6, stopped: s === "rejected" ? "Rejected" : "Cancelled" });
    return this.html({ stage: 6 });
  },
};
