const PurchaseOrdersView = {
  esc(value) {
    return String(value ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  },

  async render(container, params) {
    if (params && params.id) return this.renderDetail(container, params.id);
    return this.renderList(container);
  },

  async renderList(container) {
    container.innerHTML = `
      <div class="page-header">
        <h1>Purchase orders</h1>
        <p>What has been ordered from suppliers, and where each order is</p>
      </div>
      <div class="table-wrap" id="tableWrap"><div class="empty-state">Loading…</div></div>
    `;
    try {
      const rows = await API.listPurchaseOrders();
      const wrap = document.getElementById("tableWrap");
      if (!wrap) return; // the person has already gone to another page
      if (!rows.length) {
        wrap.innerHTML = `<div class="empty-next"><strong>No purchase orders yet.</strong><br>An order is made when an approved request has a contract price or an awarded quotation.<br>Next: <a href="#/rfqs" style="color:var(--accent-tint);">see the quotations</a>.</div>`;
        return;
      }
      wrap.innerHTML = `
        <table class="data-table">
          <thead><tr><th>NUMBER</th><th>SUPPLIER</th><th>FOR REQUEST</th><th>AMOUNT</th><th>STATUS</th></tr></thead>
          <tbody>
            ${rows.map((r) => `
              <tr data-id="${this.esc(r.id)}">
                <td class="cell-muted" style="white-space:nowrap;">${this.esc(r.id)}</td>
                <td>${this.esc(r.supplier)}<div class="cell-muted" style="font-size:11px;">${this.esc(r.supplier_code)}</div></td>
                <td class="cell-muted" style="white-space:nowrap;">${this.esc(r.request_no)}</td>
                <td>${formatCurrency(r.amount)}</td>
                <td>${statusBadge(r.status)}${r.waiting_for ? `<div class="cell-muted" style="font-size:11px;margin-top:3px;">waiting for ${this.esc(r.waiting_for)}</div>` : ""}${r.held ? `<div style="font-size:11px;margin-top:3px;color:var(--warning-text);">held: price above the estimate</div>` : ""}</td>
              </tr>
            `).join("")}
          </tbody>
        </table>
      `;
      wrap.querySelectorAll("tr[data-id]").forEach((tr) => {
        tr.addEventListener("click", () => { window.location.hash = `#/purchase-orders/${tr.dataset.id}`; });
      });
    } catch {
      const gone = document.getElementById("tableWrap");
      if (gone) gone.innerHTML = `<div class="empty-state">Couldn't load purchase orders.</div>`;
    }
  },

  STEP_LABEL: { pending: "Waiting", waiting: "Later", approved: "Approved", rejected: "Rejected", cancelled: "Cancelled" },

  approvalPanel(po) {
    const a = po.approval || {};
    const steps = a.steps || [];
    const trail = steps.map((s) => `
      <div style="display:flex;justify-content:space-between;gap:12px;padding:7px 0;border-bottom:1px solid var(--border);font-size:12.5px;">
        <div>
          <span style="color:var(--text-primary);">${this.esc(s.name)}</span>
          <span class="cell-muted"> · ${this.esc(s.designation || s.role)}${s.stand_in ? ` · ${this.esc(s.stand_in)} standing in` : ""}</span>
          ${s.comment ? `<div class="cell-muted" style="font-size:11.5px;margin-top:2px;">“${this.esc(s.comment)}”</div>` : ""}
        </div>
        <div style="text-align:right;white-space:nowrap;">
          <span class="badge badge-${this.esc(s.status === "pending" ? "waiting" : s.status)}">${this.esc(this.STEP_LABEL[s.status] || s.status)}</span>
          ${s.acted_at ? `<div class="cell-muted" style="font-size:11px;margin-top:2px;">${formatDate(s.acted_at)}</div>` : ""}
        </div>
      </div>`).join("");
    return `
      <div class="panel" style="margin-bottom:12px;">
        <div class="panel-title">Approval</div>
        ${po.hold_reason ? `<p style="margin:0 0 10px;font-size:12.5px;color:var(--warning-text);">On hold until a person accepts the price: ${this.esc(po.hold_reason)}</p>` : ""}
        ${a.decision ? `<p style="margin:0 0 10px;font-size:12.5px;color:var(--text-secondary);">${this.esc(a.decision)}</p>` : ""}
        ${trail}
        ${a.rule_code ? `<div class="cell-muted" style="font-size:11.5px;margin:10px 0;">Rule ${this.esc(a.rule_code)} version ${this.esc(a.rule_version)}</div>` : ""}
        ${a.can_act ? `<div style="display:flex;gap:10px;"><div class="btn btn-primary btn-sm" id="poApprove"><i class="ti ti-check"></i>Approve</div><div class="btn btn-sm" id="poReject"><i class="ti ti-x"></i>Reject</div></div>` : ""}
        <div id="poNote" style="font-size:12px;color:var(--warning-text);margin-top:8px;"></div>
        <div id="poReasonBox" style="display:none;margin-top:10px;">
          <textarea id="poReason" rows="2" placeholder="Reason (required)" style="width:100%;background:var(--surface-sunken);border:1px solid var(--border);color:var(--text-primary);border-radius:8px;padding:8px;font-size:12.5px;"></textarea>
          <div style="display:flex;gap:8px;margin-top:8px;"><div class="btn btn-primary btn-sm" id="poReasonOk">Confirm</div><div class="btn btn-sm" id="poReasonCancel">Cancel</div></div>
        </div>
      </div>`;
  },

  dispatchPanel(po) {
    if (!["approved", "sent", "acknowledged", "part_received", "received", "closed"].includes(po.status) && !po.sent_at) return "";
    const sent = po.sent_at ? `Sent to ${this.esc(po.sent_to)} on ${formatDate(po.sent_at)}.` : "Approved, not sent to the supplier yet.";
    const ack = po.acknowledged_at
      ? ` Acknowledged on ${formatDate(po.acknowledged_at)}${po.promised_date ? `, delivery promised by ${formatDate(po.promised_date)}` : ""}.${po.ack_note ? ` “${this.esc(po.ack_note)}”` : ""}`
      : (po.ack_overdue ? ' <span style="color:var(--warning-text);">The supplier has not acknowledged it yet and is overdue.</span>' : "");
    return `
      <div class="panel" style="margin-bottom:12px;">
        <div class="panel-title">Supplier</div>
        <p style="margin:0 0 10px;font-size:12.5px;color:var(--text-secondary);">${sent}${ack}</p>
        ${po.can_send ? `<div class="btn btn-primary btn-sm" id="poSend" style="display:inline-block;"><i class="ti ti-send"></i>Send to supplier</div>` : ""}
        ${po.can_acknowledge ? `<div id="poAckBox"><input id="poAckDate" type="date" style="padding:6px 8px;font-size:12.5px;"> <input id="poAckNote" placeholder="Note (optional)" style="padding:6px 8px;font-size:12.5px;width:40%;"> <div class="btn btn-primary btn-sm" id="poAck" style="display:inline-block;">Record acknowledgement</div></div>` : ""}
        <div id="poDispatchNote" style="font-size:12px;color:var(--warning-text);margin-top:8px;"></div>
      </div>`;
  },

  wire(po) {
    const act = async (verb, body) => {
      const res = await API.postJSON(`/api/purchase-orders/${encodeURIComponent(po.id)}/${verb}`, body);
      if (res.ok) return this.renderDetail(document.getElementById("appContent"), po.id);
      const note = document.getElementById("poNote");
      if (note) note.textContent = res.data.detail || "That did not work.";
    };
    const on = (id, fn) => { const el = document.getElementById(id); if (el) el.addEventListener("click", fn); };
    on("poApprove", () => act("approve", {}));
    on("poSend", () => act("send", {}));
    on("poAck", async () => {
      const res = await API.postJSON(`/api/purchase-orders/${encodeURIComponent(po.id)}/acknowledge`, {
        promised_date: document.getElementById("poAckDate").value || null,
        note: document.getElementById("poAckNote").value,
      });
      if (res.ok && !(res.data.warnings || []).length) return this.renderDetail(document.getElementById("appContent"), po.id);
      const note = document.getElementById("poDispatchNote");
      if (note) note.textContent = res.ok ? `Recorded. Later than needed: ${res.data.warnings.join("; ")}` : (res.data.detail || "That did not work.");
    });
    on("poReject", () => { document.getElementById("poReasonBox").style.display = "block"; document.getElementById("poReason").focus(); });
    on("poReasonCancel", () => { document.getElementById("poReasonBox").style.display = "none"; });
    on("poReasonOk", () => act("reject", { reason: document.getElementById("poReason").value }));
  },

  // The order document, its version and history, and what a buyer may change after the order was made.
  async mountChanges(po) {
    const box = document.getElementById("poChangesBox");
    if (!box) return;
    const res = await Auth.fetch(`${API.base}/api/purchase-orders/${encodeURIComponent(po.id)}/controls`);
    if (!res.ok) return;
    const c = await res.json();
    const open = po.lines;
    const history = (c.history || []).map((h) => `
      <div style="padding:6px 0;border-bottom:1px solid var(--border);font-size:12px;">
        <b>Version ${this.esc(h.version)}</b> · ${formatDate(h.at)} · ${this.esc(h.by)}
        <div class="cell-muted">${this.esc(h.reason)} · total ${formatCurrency(h.total_before)} → ${formatCurrency(h.total_after)}${h.reapproval ? " · approved again" : ""}</div>
      </div>`).join("");
    box.innerHTML = `
      <div class="panel" style="margin-bottom:12px;">
        <div class="panel-title">Order document and changes</div>
        <p style="margin:0 0 10px;font-size:12.5px;color:var(--text-secondary);">Version ${this.esc(c.version)}.
          <a href="#" id="poPdf" style="color:var(--accent-tint);">Open the order document (PDF)</a></p>
        <div style="display:flex;gap:8px;flex-wrap:wrap;">
          ${c.can_revise ? `<div class="btn btn-sm" id="poReviseOpen"><i class="ti ti-edit"></i>Change quantity or date</div>` : ""}
          ${c.can_cancel ? `<div class="btn btn-sm" id="poCancelOpen"><i class="ti ti-x"></i>Cancel this order</div>` : ""}
          ${c.can_close_balance ? `<div class="btn btn-sm" id="poCloseOpen"><i class="ti ti-lock"></i>Close what is still to come</div>` : ""}
        </div>
        <div id="poChangeForm" style="display:none;margin-top:10px;"></div>
        <div id="poChangeNote" style="font-size:12px;color:var(--warning-text);margin-top:8px;"></div>
        ${history ? `<div style="margin-top:12px;"><div class="cell-muted" style="font-size:11.5px;margin-bottom:4px;">HISTORY</div>${history}</div>` : ""}
      </div>`;
    const reload = () => this.renderDetail(document.getElementById("appContent"), po.id);
    const form = document.getElementById("poChangeForm");
    const show = (html) => { form.innerHTML = html; form.style.display = "block"; };
    const reason = `<textarea id="poChangeReason" rows="2" placeholder="Reason (required)" style="width:100%;background:var(--surface-sunken);border:1px solid var(--border);color:var(--text-primary);border-radius:8px;padding:8px;font-size:12.5px;margin-top:8px;"></textarea>`;
    const on = (id, fn) => { const el = document.getElementById(id); if (el) el.addEventListener("click", fn); };
    const send = async (verb, body) => {
      const r = await API.postJSON(`/api/purchase-orders/${encodeURIComponent(po.id)}/${verb}`, { ...body, reason: document.getElementById("poChangeReason").value });
      if (r.ok) return reload();
      document.getElementById("poChangeNote").textContent = r.data.detail || "That did not work.";
    };
    on("poPdf", async (e) => {
      e.preventDefault();
      const r = await Auth.fetch(`${API.base}/api/purchase-orders/${encodeURIComponent(po.id)}/pdf`);
      if (r.ok) window.open(URL.createObjectURL(await r.blob()), "_blank");
    });
    on("poReviseOpen", () => {
      show(`<div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;">
          <select id="poRevLine" style="padding:6px;">${open.map((l) => `<option value="${this.esc(l.line_no)}">Line ${this.esc(l.line_no)}: ${this.esc(l.name)} (${this.esc(l.qty)})</option>`).join("")}</select>
          <input id="poRevQty" type="number" min="0" step="any" placeholder="New quantity" style="padding:6px;width:130px;">
          <input id="poRevDate" type="date" title="New date needed" style="padding:6px;">
        </div>${reason}
        <div class="btn btn-primary btn-sm" id="poRevGo" style="margin-top:8px;display:inline-block;">Make a new version</div>
        <span class="cell-muted" style="font-size:11.5px;"> A higher total goes for approval again.</span>`);
      on("poRevGo", () => {
        const line = { line_no: Number(document.getElementById("poRevLine").value) };
        const q = document.getElementById("poRevQty").value, d = document.getElementById("poRevDate").value;
        if (q) line.qty = q;
        if (d) line.need_by = d;
        send("revise", { lines: [line] });
      });
    });
    on("poCancelOpen", () => {
      show(`${reason}
        <label style="font-size:12.5px;display:block;margin-top:8px;"><input type="checkbox" id="poCancelAgain"> Order these lines again (a different supplier or price is wanted)</label>
        <div class="btn btn-primary btn-sm" id="poCancelGo" style="margin-top:8px;display:inline-block;">Cancel the order</div>`);
      on("poCancelGo", () => send("cancel", { reorder: document.getElementById("poCancelAgain").checked }));
    });
    on("poCloseOpen", () => {
      show(`<p style="margin:0;font-size:12.5px;color:var(--text-secondary);">The part the supplier has not delivered is closed. It no longer counts as stock on the way, and the supplier is told.</p>${reason}
        <div class="btn btn-primary btn-sm" id="poCloseGo" style="margin-top:8px;display:inline-block;">Close what is still to come</div>`);
      on("poCloseGo", () => send("close-balance", {}));
    });
  },

  async renderDetail(container, id) {
    container.innerHTML = `<div class="page-header"><h1>${this.esc(id)}</h1></div><div class="panel">Loading…</div>`;
    try {
      const po = await API.getPurchaseOrder(id);
      if (po.error) { container.innerHTML = `<div class="empty-state">${this.esc(po.error)}</div>`; return; }
      const taxCols = Number(po.igst) > 0 ? ["IGST"] : (Number(po.cgst) > 0 ? ["CGST", "SGST"] : []);
      const taxCell = (l) => (Number(po.igst) > 0 ? `<td>${formatCurrency(l.igst)}</td>` : (Number(po.cgst) > 0 ? `<td>${formatCurrency(l.cgst)}</td><td>${formatCurrency(l.sgst)}</td>` : ""));
      container.innerHTML = `
        <div class="page-header">
          <a href="#/purchase-orders" style="color:var(--text-muted);font-size:12px;text-decoration:none;">← Purchase orders</a>
          <h1 style="margin-top:6px;">${this.esc(po.id)} · ${this.esc(po.supplier)}</h1>
          <p>${statusBadge(po.status)} · ${formatCurrency(po.total)} · for <a href="#/requisitions/${this.esc(po.request_no)}" style="color:var(--accent-tint)">${this.esc(po.request_no)}</a>
             ${po.rfq_no ? ` · <a href="#/rfqs/${this.esc(po.rfq_no)}" style="color:var(--accent-tint)">${this.esc(po.rfq_no)}</a>` : ""}
             ${po.contract_code ? ` · contract ${this.esc(po.contract_code)}` : ""}</p>
        </div>
        ${FlowStrip.forOrder(po)}
        ${this.approvalPanel(po)}
        ${this.dispatchPanel(po)}
        <div id="poChangesBox"></div>
        <div id="supplierMessagesBox"></div>
        <div class="panel" style="margin-bottom:12px;font-size:12.5px;color:var(--text-secondary);">
          Supplier GSTIN ${this.esc(po.supplier_gstin || "none")} · buyer GSTIN ${this.esc(po.buyer_gstin || "none")} · deliver to ${this.esc(po.ship_to)}
          ${po.payment_terms ? ` · payment ${this.esc(po.payment_terms)}` : ""}${po.delivery_terms ? ` · ${this.esc(po.delivery_terms)}` : ""}
          ${po.cancel_reason ? `<div style="margin-top:6px;color:var(--warning-text);">Cancelled: ${this.esc(po.cancel_reason)}</div>` : ""}
        </div>
        <div class="table-wrap" style="margin-bottom:12px;">
          <table class="data-table">
            <thead><tr><th>ITEM</th><th>QTY</th><th>PRICE</th><th>GST</th><th>TAXABLE</th>${taxCols.map((c) => `<th>${c}</th>`).join("")}</tr></thead>
            <tbody>
              ${po.lines.map((l) => `
                <tr>
                  <td>${this.esc(l.name)}<div class="cell-muted" style="font-size:11px;">${this.esc(l.item_code || "")} · ${l.source === "contract" ? `contract ${this.esc(l.source_ref)}` : l.source === "direct" ? `direct buy (${this.esc(String(l.source_ref || "").replace("_", " "))})` : `quote ${this.esc(l.source_ref)}`}</div></td>
                  <td>${this.esc(l.qty)} <span class="cell-muted">${this.esc(l.uom)}</span></td>
                  <td>${formatCurrency(l.unit_price)}${l.est_price ? `<div class="cell-muted" style="font-size:11px;">estimate ${formatCurrency(l.est_price)}</div>` : ""}</td>
                  <td class="cell-muted">${this.esc(l.gst_rate)}%</td>
                  <td>${formatCurrency(l.taxable)}</td>
                  ${taxCell(l)}
                </tr>`).join("")}
              ${po.charges.map((c) => `
                <tr>
                  <td>${c.type === "freight" ? "Freight" : "Packing and forwarding"}</td><td></td><td></td>
                  <td class="cell-muted">${this.esc(c.gst_rate)}%</td><td>${formatCurrency(c.amount)}</td>
                  ${Number(po.igst) > 0 ? `<td>${formatCurrency(c.igst)}</td>` : (Number(po.cgst) > 0 ? `<td>${formatCurrency(c.cgst)}</td><td>${formatCurrency(c.sgst)}</td>` : "")}
                </tr>`).join("")}
            </tbody>
          </table>
        </div>
        <div class="stat-grid" style="grid-template-columns:repeat(auto-fit,minmax(150px,1fr));">
          <div class="stat-card"><div class="stat-label">TAXABLE</div><div class="stat-value" style="font-size:18px;">${formatCurrency(po.taxable)}</div></div>
          ${Number(po.igst) > 0 ? `<div class="stat-card"><div class="stat-label">IGST</div><div class="stat-value" style="font-size:18px;">${formatCurrency(po.igst)}</div></div>` : ""}
          ${Number(po.cgst) > 0 ? `<div class="stat-card"><div class="stat-label">CGST</div><div class="stat-value" style="font-size:18px;">${formatCurrency(po.cgst)}</div></div><div class="stat-card"><div class="stat-label">SGST</div><div class="stat-value" style="font-size:18px;">${formatCurrency(po.sgst)}</div></div>` : ""}
          <div class="stat-card"><div class="stat-label">TOTAL</div><div class="stat-value" style="font-size:18px;">${formatCurrency(po.total)}</div></div>
        </div>
      `;
      this.wire(po);
      this.mountChanges(po);
      if (po.sent_at) SupplierMessages.mount(document.getElementById("supplierMessagesBox"), "orders", po.id);
    } catch {
      container.innerHTML = `<div class="empty-state">Couldn't load this purchase order.</div>`;
    }
  },
};
