const ReceivingView = {
  esc(value) {
    return String(value ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  },

  INSPECTION: { not_required: "", pending: "waiting for inspection", passed: "inspection passed", failed: "inspection: some rejected" },

  async render(container, params) {
    if (params && params.id) return this.renderDetail(container, params.id);
    return this.renderList(container);
  },

  async renderList(container) {
    container.innerHTML = `
      <div class="page-header">
        <h1>Goods receipt</h1>
        <p>Goods that suppliers still have to deliver, and what has been received</p>
      </div>
      <div id="waitingWrap"><div class="empty-state">Loading…</div></div>
      <h2 class="section-title" style="margin-top:22px;">Receipts already booked</h2>
      <div class="table-wrap" id="receiptsWrap"></div>
    `;
    try {
      const [waiting, receipts] = await Promise.all([API.getJSON("/api/receiving/waiting"), API.getJSON("/api/receipts")]);
      const w = document.getElementById("waitingWrap");
      if (!w) return; // the person has already gone to another page
      w.innerHTML = waiting.length
        ? waiting.map((o) => this.orderCard(o)).join("")
        : `<div class="empty-next"><strong>Nothing is waiting to be received.</strong><br>Orders appear here once they have been sent to the supplier and confirmed.</div>`;
      document.getElementById("receiptsWrap").innerHTML = receipts.length
        ? `<table class="data-table"><thead><tr><th>RECEIPT</th><th>ORDER</th><th>SUPPLIER</th><th>WHEN</th><th>STATUS</th></tr></thead><tbody>
            ${receipts.map((r) => `<tr data-id="${this.esc(r.id)}"><td class="cell-muted">${this.esc(r.id)}</td><td>${this.esc(r.po_no)}</td><td>${this.esc(r.supplier)}</td><td class="cell-muted">${formatDate(r.received_at)}</td><td>${this.esc(this.INSPECTION[r.inspection] || "received")}</td></tr>`).join("")}
          </tbody></table>`
        : `<div class="empty-state">No goods have been received yet.</div>`;
      document.querySelectorAll("#receiptsWrap tr[data-id]").forEach((tr) => {
        tr.addEventListener("click", () => { window.location.hash = `#/receiving/${tr.dataset.id}`; });
      });
      w.querySelectorAll(".recv-open").forEach((btn) => {
        btn.addEventListener("click", () => {
          const box = document.getElementById(`recv-${btn.dataset.po}`);
          box.style.display = box.style.display === "none" ? "block" : "none";
        });
      });
      w.querySelectorAll(".recv-send").forEach((btn) => btn.addEventListener("click", () => this.send(btn.dataset.po)));
    } catch {
      const w = document.getElementById("waitingWrap");
      if (w) w.innerHTML = `<div class="empty-state">Couldn't load the goods to come.</div>`;
    }
  },

  orderCard(o) {
    return `
      <div class="panel" style="margin-bottom:12px;">
        <div style="display:flex;justify-content:space-between;gap:12px;align-items:center;">
          <div>
            <a href="#/purchase-orders/${this.esc(o.id)}" style="color:var(--accent-tint);">${this.esc(o.id)}</a>
            <span class="cell-muted"> · ${this.esc(o.supplier)} · deliver to ${this.esc(o.ship_to)}${o.promised_date ? ` · promised ${formatDate(o.promised_date)}` : ""}</span>
          </div>
          <div class="btn btn-secondary btn-sm recv-open" data-po="${this.esc(o.id)}">Receive goods</div>
        </div>
        ${(o.dispatches || []).map((n) => `
          <div style="font-size:12px;margin-top:6px;color:var(--text-secondary);"><i class="ti ti-truck"></i> On the way: shipped ${formatDate(n.shipped_on)}${n.expected_on ? `, expected ${formatDate(n.expected_on)}` : ""} · ${n.lines.map((l) => `${this.esc(l.qty)} ${this.esc(l.uom || "")} ${this.esc(l.name)}`).join(", ")}${n.vehicle ? ` · vehicle ${this.esc(n.vehicle)}` : ""}${n.lr_no ? ` · LR/AWB ${this.esc(n.lr_no)}` : ""}${n.invoice_no ? ` · invoice ${this.esc(n.invoice_no)}` : ""}</div>`).join("")}
        <div id="recv-${this.esc(o.id)}" style="display:none;margin-top:12px;">
          <table class="data-table"><thead><tr><th>ITEM</th><th>ORDERED</th><th>RECEIVED SO FAR</th><th>ARRIVED NOW</th><th>REJECTED</th><th>REASON FOR REJECTION</th></tr></thead><tbody>
            ${o.lines.map((l) => `
              <tr data-line="${this.esc(l.line_no)}">
                <td>${this.esc(l.name)}<div class="cell-muted" style="font-size:11px;">${this.esc(l.item_code || "")}</div></td>
                <td>${this.esc(l.ordered)} <span class="cell-muted">${this.esc(l.uom)}</span></td>
                <td>${this.esc(l.received)}</td>
                <td><input class="recv-qty" type="number" min="0" step="any" style="width:90px;padding:5px;"></td>
                <td><input class="recv-rej" type="number" min="0" step="any" style="width:80px;padding:5px;"></td>
                <td><input class="recv-why" style="width:100%;padding:5px;"></td>
              </tr>`).join("")}
          </tbody></table>
          <div style="margin-top:8px;display:flex;gap:8px;flex-wrap:wrap;">
            <input class="recv-challan" placeholder="Delivery challan no." style="padding:6px;">
            <input class="recv-invoice" placeholder="Supplier invoice no." style="padding:6px;">
            <input class="recv-vehicle" placeholder="Vehicle no." style="padding:6px;">
            <input class="recv-remarks" placeholder="Remarks" style="padding:6px;flex:1;">
            <div class="btn btn-primary btn-sm recv-send" data-po="${this.esc(o.id)}">Post receipt</div>
          </div>
          <div class="recv-note" style="font-size:12px;color:var(--warning-text);margin-top:6px;"></div>
        </div>
      </div>`;
  },

  async send(po) {
    const box = document.getElementById(`recv-${po}`);
    const note = box.querySelector(".recv-note");
    const lines = [...box.querySelectorAll("tr[data-line]")]
      .map((tr) => ({
        line_no: Number(tr.dataset.line),
        qty_received: tr.querySelector(".recv-qty").value,
        qty_rejected: tr.querySelector(".recv-rej").value,
        reject_reason: tr.querySelector(".recv-why").value,
      }))
      .filter((l) => l.qty_received !== "" && Number(l.qty_received) > 0);
    if (!lines.length) { note.textContent = "Enter the quantity that arrived on at least one line."; return; }
    const res = await API.postJSON(`/api/purchase-orders/${encodeURIComponent(po)}/receive`, {
      lines,
      challan: box.querySelector(".recv-challan").value,
      invoice_ref: box.querySelector(".recv-invoice").value,
      vehicle: box.querySelector(".recv-vehicle").value,
      remarks: box.querySelector(".recv-remarks").value,
    });
    if (res.ok) { window.location.hash = `#/receiving/${res.data.receipt}`; return; }
    note.textContent = res.data.detail || "The receipt was not posted.";
  },

  async renderDetail(container, id) {
    container.innerHTML = `<div class="page-header"><h1>${this.esc(id)}</h1></div><div class="panel">Loading…</div>`;
    try {
      const g = await API.getJSON(`/api/receipts/${encodeURIComponent(id)}`);
      const pending = g.inspection === "pending";
      container.innerHTML = `
        <div class="page-header">
          <a href="#/receiving" style="color:var(--text-muted);font-size:12px;text-decoration:none;">← Goods receipt</a>
          <h1 style="margin-top:6px;">${this.esc(g.id)}</h1>
          <p>for <a href="#/purchase-orders/${this.esc(g.po_no)}" style="color:var(--accent-tint)">${this.esc(g.po_no)}</a> · ${formatDate(g.received_at)} · received by ${this.esc(g.received_by)}${this.INSPECTION[g.inspection] ? ` · ${this.esc(this.INSPECTION[g.inspection])}` : ""}</p>
        </div>
        <div class="panel" style="margin-bottom:12px;font-size:12.5px;color:var(--text-secondary);">
          ${g.challan ? `Challan ${this.esc(g.challan)} · ` : ""}${g.invoice_ref ? `supplier invoice ${this.esc(g.invoice_ref)} · ` : ""}${g.vehicle ? `vehicle ${this.esc(g.vehicle)} · ` : ""}deliver to ${this.esc(g.location)}${g.remarks ? `<div style="margin-top:6px;">${this.esc(g.remarks)}</div>` : ""}
        </div>
        <div class="table-wrap"><table class="data-table"><thead><tr><th>ITEM</th><th>RECEIVED</th><th>ACCEPTED</th><th>REJECTED</th></tr></thead><tbody>
          ${g.lines.map((l) => `<tr data-line="${this.esc(l.line_no)}"><td>${this.esc(l.name)}<div class="cell-muted" style="font-size:11px;">${this.esc(l.item_code || "")}</div></td>
            <td>${this.esc(l.received)} <span class="cell-muted">${this.esc(l.uom)}</span></td>
            <td>${pending && g.can_inspect ? `<input class="insp-acc" type="number" min="0" step="any" value="${this.esc(l.received)}" style="width:90px;padding:5px;">` : this.esc(l.accepted ?? "waiting")}</td>
            <td>${pending && g.can_inspect ? `<input class="insp-rej" type="number" min="0" step="any" value="0" style="width:80px;padding:5px;"> <input class="insp-why" placeholder="Reason" style="padding:5px;">` : `${this.esc(l.rejected ?? "")}${l.reason ? ` <span class="cell-muted">“${this.esc(l.reason)}”</span>` : ""}`}</td></tr>`).join("")}
        </tbody></table></div>
        ${pending && g.can_inspect ? `<div style="margin-top:10px;"><div class="btn btn-primary btn-sm" id="inspectBtn">Record inspection</div><div id="inspectNote" style="font-size:12px;color:var(--warning-text);margin-top:6px;"></div></div>` : ""}
      `;
      const btn = document.getElementById("inspectBtn");
      if (btn) {
        btn.addEventListener("click", async () => {
          const lines = [...container.querySelectorAll("tr[data-line]")].map((tr) => ({
            line_no: Number(tr.dataset.line),
            qty_accepted: tr.querySelector(".insp-acc").value,
            qty_rejected: tr.querySelector(".insp-rej").value || "0",
            reason: tr.querySelector(".insp-why").value,
          }));
          const res = await API.postJSON(`/api/receipts/${encodeURIComponent(id)}/inspect`, { lines });
          if (res.ok) return this.renderDetail(container, id);
          document.getElementById("inspectNote").textContent = res.data.detail || "The inspection was not recorded.";
        });
      }
    } catch {
      container.innerHTML = `<div class="empty-state">Couldn't load this receipt.</div>`;
    }
  },
};
