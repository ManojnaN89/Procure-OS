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
                <td class="cell-muted" style="white-space:nowrap;">${this.esc(r.id)}</td>
                <td>${this.esc(r.title)}${r.priority === "urgent" ? ' <span class="badge badge-rejected" style="font-size:10px;">urgent</span>' : ""}</td>
                <td>${formatCurrency(r.amount)}</td>
                <td class="cell-muted">${this.esc(r.requested_by)}</td>
                <td>${statusBadge(r.status)}${r.waiting_for ? `<div class="cell-muted" style="font-size:11px;margin-top:3px;">waiting for ${this.esc(r.waiting_for)}</div>` : ""}</td>
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

  // Shows what the stock check would create and how each request would be approved, and only does it when the person confirms.
  async previewReplenishment() {
    const panel = document.getElementById("replenishPanel");
    if (!panel) return;
    panel.innerHTML = `<div class="panel" style="margin-bottom:12px;">Checking stock levels…</div>`;
    try {
      const r = await API.runReplenishment(true);
      if (!document.getElementById("replenishPanel")) return;
      const skipped = Object.entries(r.skipped || {});
      const routes = [
        r.auto_approved ? `${r.auto_approved} approve${r.auto_approved === 1 ? "s" : ""} automatically` : "",
        r.needs_approval ? `${r.needs_approval} need${r.needs_approval === 1 ? "s" : ""} a person's approval` : "",
        r.blocked ? `${r.blocked} cannot be routed` : "",
      ].filter(Boolean).join(", ");
      const blocked = r.requests.filter((q) => q.route_kind === "blocked");
      panel.innerHTML = `
        <div class="panel" style="margin-bottom:12px;">
          <div class="panel-title">Stock check</div>
          ${r.requests.length
            ? `<p style="margin:0 0 6px;font-size:12.5px;color:var(--text-secondary);">
                 ${r.line_count} item${r.line_count === 1 ? "" : "s"} at or below their reorder point would become
                 ${r.requests.length} request${r.requests.length === 1 ? "" : "s"}, about ${formatCurrency(r.total_value)} in all.
               </p>
               <p style="margin:0 0 10px;font-size:12.5px;color:var(--text-secondary);">Of these: ${this.esc(routes)}.</p>`
            : `<p style="margin:0 0 10px;font-size:12.5px;color:var(--text-secondary);">Nothing needs ordering right now.</p>`}
          ${blocked.length
            ? `<div style="font-size:12px;color:var(--warning-text);margin-bottom:10px;">${this.esc(blocked[0].route)}. These stay as drafts.</div>`
            : ""}
          ${skipped.length
            ? `<div style="font-size:12px;color:var(--text-muted);margin-bottom:10px;">Left out: ${skipped.map(([why, n]) => `${n} — ${this.esc(why)}`).join("; ")}</div>`
            : ""}
          <div style="display:flex;gap:8px;">
            ${r.requests.length ? `<div class="btn btn-primary btn-sm" id="createDraftsBtn"><i class="ti ti-check"></i>Create and submit</div>` : ""}
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
    panel.innerHTML = `<div class="panel" style="margin-bottom:12px;">Creating the requests…</div>`;
    try {
      const r = await API.runReplenishment(false);
      if (!document.getElementById("replenishPanel")) return;
      const approved = r.requests.filter((q) => q.outcome === "approved").length;
      const waiting = r.requests.filter((q) => q.outcome === "submitted").length;
      const drafts = r.requests.filter((q) => !q.outcome);
      panel.innerHTML = `<div class="panel" style="margin-bottom:12px;">
        Created ${r.requests.length} request${r.requests.length === 1 ? "" : "s"}:
        ${approved} approved automatically, ${waiting} waiting for approval${drafts.length ? `, ${drafts.length} left as drafts (${this.esc(drafts[0].submit_error || "")})` : ""}.
      </div>`;
      await this.loadList();
    } catch {
      panel.innerHTML = `<div class="panel" style="margin-bottom:12px;">The requests could not be created.</div>`;
    }
  },

  sourceCell(s) {
    if (!s) return `<span class="cell-muted">\u2014</span>`;
    if (s.method === "contract") return `<span class="badge badge-approved" style="font-size:10px;">contract</span> <span class="cell-muted">${this.esc(s.reason)}</span>`;
    if (s.method === "rfq") return `<span class="badge badge-pending" style="font-size:10px;">quotes</span> <a href="#/rfqs/${this.esc(s.rfq_no)}" style="color:var(--accent-tint)">${this.esc(s.rfq_no)}</a>`;
    return `<span class="badge badge-rejected" style="font-size:10px;">buyer</span> <span class="cell-muted">${this.esc(s.reason)}</span>`;
  },

  STEP_LABEL: { pending: "Waiting", waiting: "Later", approved: "Approved", rejected: "Rejected", returned: "Returned", cancelled: "Cancelled" },

  approvalPanel(r) {
    const a = r.approval || {};
    const steps = a.steps || [];
    const trail = steps.length
      ? `<div style="margin:0 0 12px;">
          ${steps.map((s) => `
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
            </div>`).join("")}
        </div>` : "";
    const rule = a.rule_code ? `<div class="cell-muted" style="font-size:11.5px;margin-bottom:10px;">Rule ${this.esc(a.rule_code)} version ${this.esc(a.rule_version)}</div>` : "";
    const actions = a.can_act
      ? `<div style="display:flex;gap:10px;flex-wrap:wrap;" id="decideBtns">
           <div class="btn btn-primary btn-sm" id="approveBtn"><i class="ti ti-check"></i>Approve</div>
           <div class="btn btn-sm" id="returnBtn"><i class="ti ti-arrow-back-up"></i>Return for changes</div>
           <div class="btn btn-sm" id="rejectBtn"><i class="ti ti-x"></i>Reject</div>
         </div>`
      : a.can_submit
        ? `<div class="btn btn-primary btn-sm" id="submitBtn" style="display:inline-flex;"><i class="ti ti-send"></i>Submit for approval</div>`
        : "";
    return `
      <div class="panel" style="margin-bottom:12px;">
        <div class="panel-title">Approval</div>
        ${a.decision ? `<p style="margin:0 0 10px;font-size:12.5px;color:var(--text-secondary);">${this.esc(a.decision)}</p>` : `<p style="margin:0 0 10px;font-size:12.5px;color:var(--text-muted);">Not submitted yet.</p>`}
        ${trail}${rule}${actions}
        ${a.can_source ? `<div class="btn btn-primary btn-sm" id="sourceBtn" style="display:inline-flex;margin-top:6px;"><i class="ti ti-search"></i>Find suppliers</div>` : ""}
        <div id="actionNote" style="font-size:12px;color:var(--warning-text);margin-top:8px;"></div>
        <div id="reasonBox" style="display:none;margin-top:10px;">
          <textarea id="reasonText" rows="2" placeholder="Reason (required)" style="width:100%;background:var(--surface-sunken);border:1px solid var(--border);color:var(--text-primary);border-radius:8px;padding:8px;font-size:12.5px;"></textarea>
          <div style="display:flex;gap:8px;margin-top:8px;"><div class="btn btn-primary btn-sm" id="reasonOk">Confirm</div><div class="btn btn-sm" id="reasonCancel">Cancel</div></div>
        </div>
      </div>`;
  },

  wireApproval(r) {
    const act = async (verb, body) => {
      const res = await API.postJSON(`/api/requisitions/${encodeURIComponent(r.id)}/${verb}`, body);
      if (res.ok) return this.renderDetail(document.getElementById("appContent"), r.id);
      const note = document.getElementById("actionNote");
      if (note) note.textContent = res.data.detail || res.data.error || "That did not work.";
    };
    const on = (id, fn) => { const el = document.getElementById(id); if (el) el.addEventListener("click", fn); };
    on("submitBtn", () => act("submit"));
    on("sourceBtn", () => act("source"));
    on("approveBtn", () => act("approve", {}));
    const ask = (verb) => {
      document.getElementById("reasonBox").style.display = "block";
      document.getElementById("reasonText").focus();
      this.pendingVerb = verb;
    };
    on("returnBtn", () => ask("return"));
    on("rejectBtn", () => ask("reject"));
    on("reasonCancel", () => { document.getElementById("reasonBox").style.display = "none"; });
    on("reasonOk", () => act(this.pendingVerb, { reason: document.getElementById("reasonText").value }));
  },

  async renderDetail(container, id) {
    container.innerHTML = `<div class="page-header"><h1>${this.esc(id)}</h1></div><div class="panel">Loading…</div>`;
    try {
      const r = await API.getRequisition(id);
      if (r.error) { container.innerHTML = `<div class="empty-state">${this.esc(r.error)}</div>`; return; }
      const lines = r.lines || [];
      container.innerHTML = `
        <div class="page-header">
          <a href="#/requisitions" style="color:var(--text-muted);font-size:12px;text-decoration:none;">← Requisitions</a>
          <h1 style="margin-top:6px;">${this.esc(r.title)}</h1>
          <p>${this.esc(r.id)} · ${formatCurrency(r.amount)} · ${this.esc(r.requested_by)} · ${statusBadge(r.status)}
             ${r.ship_to ? ` · deliver to ${this.esc(r.ship_to)}` : ""}${r.need_by ? ` · needed by ${formatDate(r.need_by)}` : ""}</p>
        </div>
        ${this.approvalPanel(r)}
        ${lines.length ? `
        <div class="table-wrap" style="margin-bottom:12px;">
          <table class="data-table">
            <thead><tr><th>ITEM</th><th>QTY</th><th>EST. PRICE</th><th>VALUE</th><th>NEEDED BY</th><th>HOW IT IS BOUGHT</th></tr></thead>
            <tbody>
              ${lines.map((l) => `
                <tr>
                  <td>${this.esc(l.name)}<div class="cell-muted" style="font-size:11px;">${this.esc(l.item_code || "")}${l.reason ? ` · ${this.esc(l.reason)}` : ""}</div></td>
                  <td>${this.esc(l.qty)} <span class="cell-muted">${this.esc(l.uom)}</span></td>
                  <td class="cell-muted">${l.est_price === null ? "—" : formatCurrency(l.est_price)}</td>
                  <td>${l.value === null ? "—" : formatCurrency(l.value)}</td>
                  <td class="cell-muted">${formatDate(l.need_by)}</td>
                  <td style="font-size:12px;">${this.sourceCell(l.source)}</td>
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
            : `<div style="font-size:12px;color:var(--text-muted);">No purchase order linked yet.</div>`}
        </div>
      `;
      this.wireApproval(r);
    } catch {
      container.innerHTML = `<div class="empty-state">Couldn't load this requisition.</div>`;
    }
  },
};
