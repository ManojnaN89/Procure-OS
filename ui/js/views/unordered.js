/**
 * Receive without order: goods that came with no purchase order, taken in as a flagged exception. The store records
 * them, the purchase manager approves or rejects, the buyer raises the order afterwards, and the store then posts the
 * receipt on it (or returns rejected goods). Nothing enters stock before that receipt.
 */
const UnorderedView = {
  esc(value) {
    return String(value ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  },

  STATUS: {
    pending: "waiting for the purchase manager",
    approved: "approved: waiting for the buyer's order",
    rejected: "rejected: to go back to the supplier",
    ordered: "ordered: receive it once the order is sent",
    received: "received on the order",
    returned: "returned to the supplier",
  },

  async render(container) {
    container.innerHTML = `
      <div class="page-header">
        <h1>Receive without order</h1>
        <p>Goods that arrived with no purchase order. They stay out of stock until the purchase manager approves, the buyer raises the order and the receipt is posted on it.</p>
      </div>
      <div class="panel" style="margin-bottom:16px;" id="unoForm">
        <div style="display:flex;gap:8px;flex-wrap:wrap;">
          <input class="uno-supplier" placeholder="Supplier code" style="padding:6px;width:120px;">
          <input class="uno-item" placeholder="Item code" style="padding:6px;width:110px;">
          <input class="uno-qty" type="number" min="0" step="any" placeholder="Quantity" style="padding:6px;width:100px;">
          <input class="uno-location" placeholder="Location code" style="padding:6px;width:120px;">
          <input class="uno-challan" placeholder="Delivery challan no." style="padding:6px;width:160px;">
          <input class="uno-reason" placeholder="Why did it come without an order?" style="padding:6px;flex:1;min-width:220px;">
        </div>
        <textarea class="uno-serials" rows="2" placeholder="Serial numbers, one per unit (serialised items only)" style="width:100%;margin-top:8px;padding:6px;"></textarea>
        <div style="margin-top:8px;"><div class="btn btn-primary btn-sm" id="unoRecord">Record and ask the purchase manager</div></div>
        <div id="unoNote" style="font-size:12px;color:var(--warning-text);margin-top:6px;"></div>
      </div>
      <div class="table-wrap" id="unoList"><div class="empty-state">Loading…</div></div>
    `;
    document.getElementById("unoRecord").addEventListener("click", () => this.record());
    await this.load();
  },

  async load() {
    let rows;
    try {
      rows = await API.getJSON("/api/unordered-receipts");
    } catch {
      const w = document.getElementById("unoList");
      if (w) w.innerHTML = `<div class="empty-state">Couldn't load the receipts without an order.</div>`;
      return;
    }
    const w = document.getElementById("unoList");
    if (!w) return; // the person has already gone to another page
    w.innerHTML = rows.length
      ? `<table class="data-table"><thead><tr><th>RECEIPT</th><th>SUPPLIER</th><th>ITEM</th><th>QUANTITY</th><th>WHY</th><th>STATUS</th><th></th></tr></thead><tbody>
          ${rows.map((r) => `<tr data-id="${this.esc(r.id)}"><td class="cell-muted">${this.esc(r.id)}<div style="font-size:11px;">${formatDate(r.received_on)}</div></td>
            <td>${this.esc(r.supplier)}</td><td>${this.esc(r.item)}<div class="cell-muted" style="font-size:11px;">${this.esc(r.item_code)}${r.serials.length ? ` · ${this.esc(r.serials.join(", "))}` : ""}</div></td>
            <td>${this.esc(r.qty)} <span class="cell-muted">${this.esc(r.uom || "")}</span></td><td>${this.esc(r.reason)}</td>
            <td>${this.esc(this.STATUS[r.status] || r.status)}${r.po_no ? `<div style="font-size:11px;"><a href="#/purchase-orders/${this.esc(r.po_no)}" style="color:var(--accent-tint);">${this.esc(r.po_no)}</a></div>` : ""}${r.decision_note ? `<div class="cell-muted" style="font-size:11px;">“${this.esc(r.decision_note)}”</div>` : ""}</td>
            <td>${this.actions(r)}</td></tr>`).join("")}
        </tbody></table><div id="unoActNote" style="font-size:12px;color:var(--warning-text);margin-top:6px;"></div>`
      : `<div class="empty-state">No goods have come without an order.</div>`;
    w.querySelectorAll("[data-act]").forEach((btn) => btn.addEventListener("click", () => this.act(btn.dataset.id, btn.dataset.act)));
  },

  actions(r) {
    const b = (act, label, kind = "btn-secondary") => `<div class="btn ${kind} btn-sm" data-id="${this.esc(r.id)}" data-act="${act}" style="margin:2px;">${label}</div>`;
    return [
      r.can_decide ? b("approve", "Approve", "btn-primary") + b("reject", "Reject") : "",
      r.can_order ? b("order", "Raise the order", "btn-primary") : "",
      r.can_receive ? b("receive", "Post the receipt", "btn-primary") : "",
      r.can_return ? b("returned", "Mark returned") : "",
    ].join("");
  },

  async act(id, act) {
    const body = {};
    if (act === "reject") {
      body.reason = window.prompt("Why are the goods rejected? (they go back to the supplier)") || "";
      if (!body.reason.trim()) return;
    }
    if (act === "order") {
      body.unit_price = window.prompt("Price per unit agreed with the supplier (before GST)") || "";
      if (!body.unit_price.trim()) return;
    }
    if (act === "returned") body.ref = window.prompt("Return challan number (optional)") || "";
    const res = await API.postJSON(`/api/unordered-receipts/${encodeURIComponent(id)}/${act}`, body);
    if (res.ok) return this.load();
    const note = document.getElementById("unoActNote");
    if (note) note.textContent = res.data.detail || "That did not go through.";
  },

  async record() {
    const f = document.getElementById("unoForm");
    const note = document.getElementById("unoNote");
    const v = (cls) => f.querySelector(cls).value.trim();
    if (!v(".uno-supplier") || !v(".uno-item") || !v(".uno-qty") || !v(".uno-location") || !v(".uno-reason")) {
      note.textContent = "Supplier, item, quantity, location and the reason are needed.";
      return;
    }
    const res = await API.postJSON("/api/unordered-receipts", {
      supplier_code: v(".uno-supplier"),
      item_code: v(".uno-item"),
      qty: v(".uno-qty"),
      location_code: v(".uno-location"),
      challan: v(".uno-challan"),
      reason: v(".uno-reason"),
      serials: v(".uno-serials"),
    });
    if (!res.ok) { note.textContent = res.data.detail || "The receipt was not recorded."; return; }
    note.textContent = `Recorded as ${res.data.receipt}; the purchase manager has been asked.`;
    f.querySelectorAll("input, textarea").forEach((el) => { el.value = ""; });
    this.load();
  },
};
