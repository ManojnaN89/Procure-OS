const RequisitionsView = {
  esc(value) {
    return String(value ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  },

  async render(container, params) {
    if (params && params.id) return this.renderDetail(container, params.id);
    return this.renderList(container);
  },

  async renderList(container) {
    const canRun = Auth.can("replenishment.run");
    container.innerHTML = `
      <div class="page-header page-header-row">
        <div>
          <h1>Requisitions</h1>
          <p>Internal purchase requests and their approval status</p>
        </div>
        <div style="display:flex;gap:8px;">
          ${canRun ? `<div class="btn" id="replenishBtn"><i class="ti ti-refresh"></i>Check stock levels</div>` : ""}
          <div class="btn btn-primary" id="newReqBtn"><i class="ti ti-plus"></i>New requisition</div>
        </div>
      </div>
      <div id="replenishPanel"></div>
      <div class="table-wrap" id="tableWrap"><div class="empty-state">Loading…</div></div>
    `;
    document.getElementById("newReqBtn").addEventListener("click", () => {
      Prefill.set("Create a requisition for [item/description], amount [amount], requested by [name]");
      window.location.hash = "#/assistant";
    });
    if (canRun) document.getElementById("replenishBtn").addEventListener("click", () => this.previewReplenishment());
    await this.loadList();
  },

  async loadList() {
    try {
      const rows = await API.listRequisitions();
      const wrap = document.getElementById("tableWrap");
      if (!wrap) return; // the person has already gone to another page
      if (!rows.length) {
        wrap.innerHTML = `<div class="empty-state">No requisitions yet. Check the stock levels, or ask the assistant to raise one.</div>`;
        return;
      }
      wrap.innerHTML = `
        <table class="data-table">
          <thead><tr><th>NUMBER</th><th>TITLE</th><th>AMOUNT</th><th>REQUESTED BY</th><th>STATUS</th></tr></thead>
          <tbody>
            ${rows.map((r) => `
              <tr data-id="${this.esc(r.id)}">
                <td class="cell-muted">${this.esc(r.id)}</td>
                <td>${this.esc(r.title)}${r.priority === "urgent" ? ' <span class="badge badge-rejected" style="font-size:10px;">urgent</span>' : ""}</td>
                <td>${formatCurrency(r.amount)}</td>
                <td class="cell-muted">${this.esc(r.requested_by)}</td>
                <td>${statusBadge(r.status)}</td>
              </tr>
            `).join("")}
          </tbody>
        </table>
      `;
      wrap.querySelectorAll("tr[data-id]").forEach((tr) => {
        tr.addEventListener("click", () => { window.location.hash = `#/requisitions/${tr.dataset.id}`; });
      });
    } catch {
      const gone = document.getElementById("tableWrap");
      if (gone) gone.innerHTML = `<div class="empty-state">Couldn't load requisitions.</div>`;
    }
  },

  // Shows what the stock check would create, and only creates it when the person confirms.
  async previewReplenishment() {
    const panel = document.getElementById("replenishPanel");
    if (!panel) return;
    panel.innerHTML = `<div class="panel" style="margin-bottom:12px;">Checking stock levels…</div>`;
    try {
      const r = await API.runReplenishment(true);
      if (!document.getElementById("replenishPanel")) return;
      const skipped = Object.entries(r.skipped || {});
      panel.innerHTML = `
        <div class="panel" style="margin-bottom:12px;">
          <div class="panel-title">Stock check</div>
          ${r.requests.length
            ? `<p style="margin:0 0 10px;font-size:12.5px;color:var(--text-secondary);">
                 ${r.line_count} item${r.line_count === 1 ? "" : "s"} at or below their reorder point would become
                 ${r.requests.length} draft request${r.requests.length === 1 ? "" : "s"}, about ${formatCurrency(r.total_value)} in all.
               </p>`
            : `<p style="margin:0 0 10px;font-size:12.5px;color:var(--text-secondary);">Nothing needs ordering right now.</p>`}
          ${skipped.length
            ? `<div style="font-size:12px;color:var(--text-muted);margin-bottom:10px;">Left out: ${skipped.map(([why, n]) => `${n} — ${this.esc(why)}`).join("; ")}</div>`
            : ""}
          <div style="display:flex;gap:8px;">
            ${r.requests.length ? `<div class="btn btn-primary btn-sm" id="createDraftsBtn"><i class="ti ti-check"></i>Create the drafts</div>` : ""}
            <div class="btn btn-sm" id="closeReplenishBtn">Close</div>
          </div>
        </div>`;
      document.getElementById("closeReplenishBtn").addEventListener("click", () => { panel.innerHTML = ""; });
      const create = document.getElementById("createDraftsBtn");
      if (create) create.addEventListener("click", () => this.createDrafts());
    } catch {
      panel.innerHTML = `<div class="panel" style="margin-bottom:12px;">Couldn't check the stock levels.</div>`;
    }
  },

  async createDrafts() {
    const panel = document.getElementById("replenishPanel");
    panel.innerHTML = `<div class="panel" style="margin-bottom:12px;">Creating the drafts…</div>`;
    try {
      const r = await API.runReplenishment(false);
      if (!document.getElementById("replenishPanel")) return;
      panel.innerHTML = `<div class="panel" style="margin-bottom:12px;">Created ${r.requests.length} draft request${r.requests.length === 1 ? "" : "s"}.</div>`;
      await this.loadList();
    } catch {
      panel.innerHTML = `<div class="panel" style="margin-bottom:12px;">The drafts could not be created.</div>`;
    }
  },

  async renderDetail(container, id) {
    container.innerHTML = `<div class="page-header"><h1>${this.esc(id)}</h1></div><div class="panel">Loading…</div>`;
    try {
      const r = await API.getRequisition(id);
      if (r.error) { container.innerHTML = `<div class="empty-state">${this.esc(r.error)}</div>`; return; }
      const canDecide = r.status === "pending";
      const lines = r.lines || [];
      container.innerHTML = `
        <div class="page-header">
          <a href="#/requisitions" style="color:var(--text-muted);font-size:12px;text-decoration:none;">← Requisitions</a>
          <h1 style="margin-top:6px;">${this.esc(r.title)}</h1>
          <p>${this.esc(r.id)} · ${formatCurrency(r.amount)} · ${this.esc(r.requested_by)} · ${statusBadge(r.status)}
             ${r.ship_to ? ` · deliver to ${this.esc(r.ship_to)}` : ""}${r.need_by ? ` · needed by ${formatDate(r.need_by)}` : ""}</p>
        </div>
        ${lines.length ? `
        <div class="table-wrap" style="margin-bottom:12px;">
          <table class="data-table">
            <thead><tr><th>ITEM</th><th>QTY</th><th>EST. PRICE</th><th>VALUE</th><th>NEEDED BY</th></tr></thead>
            <tbody>
              ${lines.map((l) => `
                <tr>
                  <td>${this.esc(l.name)}<div class="cell-muted" style="font-size:11px;">${this.esc(l.item_code || "")}${l.reason ? ` · ${this.esc(l.reason)}` : ""}</div></td>
                  <td>${this.esc(l.qty)} <span class="cell-muted">${this.esc(l.uom)}</span></td>
                  <td class="cell-muted">${l.est_price === null ? "—" : formatCurrency(l.est_price)}</td>
                  <td>${l.value === null ? "—" : formatCurrency(l.value)}</td>
                  <td class="cell-muted">${formatDate(l.need_by)}</td>
                </tr>
              `).join("")}
            </tbody>
          </table>
        </div>` : ""}
        <div class="panel">
          <div class="panel-title">Details</div>
          <p style="color:var(--text-secondary);font-size:12.5px;margin:0 0 14px;">${this.esc(r.description) || "No description provided."}</p>
          ${r.purchase_order
            ? `<div style="font-size:12px;color:var(--text-secondary);">Linked purchase order: <a href="#/purchase-orders/${this.esc(r.purchase_order.id)}" style="color:var(--accent-tint)">#${this.esc(r.purchase_order.id)}</a> — ${statusBadge(r.purchase_order.status)}</div>`
            : canDecide
              ? `<div style="display:flex;gap:10px;" id="decideBtns">
                   <div class="btn btn-primary btn-sm" id="approveBtn"><i class="ti ti-check"></i>Approve</div>
                   <div class="btn btn-sm" id="rejectBtn"><i class="ti ti-x"></i>Reject</div>
                 </div>`
              : `<div style="font-size:12px;color:var(--text-muted);">No purchase order linked yet.</div>`
          }
        </div>
      `;
      if (canDecide) {
        // These fire the same Assistant/skill path a typed message would —
        // not a separate write route. See Action Model in the plan.
        document.getElementById("approveBtn").addEventListener("click", () => {
          Conversation.send(`Approve requisition ${r.id}`);
          window.location.hash = "#/assistant";
        });
        document.getElementById("rejectBtn").addEventListener("click", () => {
          Conversation.send(`Reject requisition ${r.id}`);
          window.location.hash = "#/assistant";
        });
      }
    } catch {
      container.innerHTML = `<div class="empty-state">Couldn't load this requisition.</div>`;
    }
  },
};
