const RequisitionsView = {
  esc(value) {
    return String(value ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  },

  async render(container, params) {
    if (params && params.id === "new") return this.renderForm(container);
    if (params && params.id) return this.renderDetail(container, params.id);
    return this.renderList(container);
  },

  TABS: [
    { key: "needs_me", label: "Needs my decision", test: (r) => r.needs_me },
    { key: "mine", label: "My requests", test: (r) => r.mine },
    { key: "approved", label: "Approved", test: (r) => r.status === "approved" },
    { key: "waiting", label: "Waiting for approval", test: (r) => r.status === "pending" },
    { key: "all", label: "All", test: () => true },
  ],

  async renderList(container) {
    const canRun = Auth.can("replenishment.run");
    const canRaise = Auth.can("request.create");
    container.innerHTML = `
      <div class="page-header page-header-row">
        <div>
          <h1>Requests</h1>
          <p>What people have asked for, and where each request is in approval</p>
        </div>
        <div style="display:flex;gap:8px;flex-wrap:wrap;">
          ${canRun ? `<div class="btn" id="replenishBtn"><i class="ti ti-refresh"></i>Check stock levels</div>` : ""}
          ${canRaise ? `<a class="btn btn-primary" href="#/requisitions/new"><i class="ti ti-plus"></i>New request</a>` : ""}
        </div>
      </div>
      <div id="replenishPanel"></div>
      <div class="tabs" id="reqTabs"></div>
      <div class="table-wrap" id="tableWrap"><div class="empty-state">Loading…</div></div>
    `;
    if (canRun) document.getElementById("replenishBtn").addEventListener("click", () => this.previewReplenishment());
    await this.loadList();
  },

  async loadList() {
    try {
      this.rows = await API.listRequisitions();
      if (!document.getElementById("tableWrap")) return; // the person has already gone to another page
      const counts = Object.fromEntries(this.TABS.map((t) => [t.key, this.rows.filter(t.test).length]));
      if (!this.tab || (this.tab !== "all" && !counts[this.tab])) {
        this.tab = (this.TABS.find((t) => t.key !== "all" && counts[t.key]) || { key: "all" }).key;
      }
      this.drawList(counts);
    } catch {
      const gone = document.getElementById("tableWrap");
      if (gone) gone.innerHTML = `<div class="empty-state">Couldn't load the requests. Reload the page to try again.</div>`;
    }
  },

  drawList(counts) {
    const tabs = document.getElementById("reqTabs");
    const wrap = document.getElementById("tableWrap");
    if (!tabs || !wrap) return;
    tabs.innerHTML = this.TABS.filter((t) => t.key === "all" || counts[t.key] || t.key === this.tab)
      .map((t) => `<button class="tab${t.key === this.tab ? " active" : ""}" data-tab="${t.key}">${t.label}<span class="count">${counts[t.key]}</span></button>`).join("");
    tabs.querySelectorAll(".tab").forEach((el) => el.addEventListener("click", () => { this.tab = el.dataset.tab; this.drawList(counts); }));
    const test = this.TABS.find((t) => t.key === this.tab).test;
    const rows = this.rows.filter(test);
    if (!rows.length) {
      wrap.innerHTML = this.rows.length
        ? `<div class="empty-next"><strong>Nothing in this list.</strong><br>Try another tab above.</div>`
        : `<div class="empty-next"><strong>No requests yet.</strong><br>${Auth.can("request.create") ? `Next: <a href="#/requisitions/new" style="color:var(--accent-tint);">raise a new request</a>${Auth.can("replenishment.run") ? ", or check the stock levels." : "."}` : Auth.can("replenishment.run") ? "Next: click <strong>Check stock levels</strong> to see what has fallen to its reorder point." : "Stock that falls to its reorder point creates requests automatically."}</div>`;
      return;
    }
    const pick = this.tab === "needs_me" && Auth.can("request.approve");  // approve several at once from the inbox
    wrap.innerHTML = `
      ${pick ? `<div style="display:flex;gap:10px;align-items:center;margin-bottom:8px;">
        <div class="btn btn-primary btn-sm" id="bulkApprove" style="display:inline-flex;opacity:.5;pointer-events:none;"><i class="ti ti-checks"></i>Approve selected</div>
        <span class="cell-muted" id="bulkNote" style="font-size:12px;"></span></div>` : ""}
      <table class="data-table">
        <thead><tr>${pick ? `<th style="width:28px;"><input type="checkbox" id="selAll" title="Select all"></th>` : ""}<th>REQUEST</th><th>AMOUNT</th><th>REQUESTED BY</th><th>STATUS</th></tr></thead>
        <tbody>
          ${rows.map((r) => `
            <tr data-id="${this.esc(r.id)}">
              ${pick ? `<td><input type="checkbox" class="pickReq" value="${this.esc(r.id)}"></td>` : ""}
              <td>${this.esc(r.title)}${r.priority === "urgent" ? ' <span class="badge badge-rejected" style="font-size:10px;">urgent</span>' : ""}
                  <div class="cell-muted" style="margin-top:2px;">${this.esc(r.id)}${r.items > 1 ? ` \u00b7 ${r.items} items` : ""}</div></td>
              <td style="white-space:nowrap;">${formatCurrency(r.amount)}</td>
              <td class="cell-muted">${this.esc(r.requested_by)}</td>
              <td>${statusBadge(r.status)}${r.waiting_for ? `<div class="cell-muted" style="font-size:11px;margin-top:3px;">waiting for ${this.esc(r.waiting_for)}</div>` : ""}</td>
            </tr>
          `).join("")}
        </tbody>
      </table>`;
    wrap.querySelectorAll("tr[data-id]").forEach((tr) => {
      tr.addEventListener("click", (e) => { if (e.target.closest("input")) return; window.location.hash = `#/requisitions/${tr.dataset.id}`; });
    });
    if (pick) this.wireBulk(wrap);
  },

  // Select requests in the inbox and approve them together. Each is decided on its own; what is refused is listed.
  wireBulk(wrap) {
    const btn = document.getElementById("bulkApprove");
    const note = document.getElementById("bulkNote");
    const boxes = () => [...wrap.querySelectorAll(".pickReq")];
    const refresh = () => {
      const n = boxes().filter((b) => b.checked).length;
      btn.style.opacity = n ? "1" : ".5";
      btn.style.pointerEvents = n ? "auto" : "none";
      btn.lastChild.textContent = n ? `Approve selected (${n})` : "Approve selected";
    };
    wrap.querySelectorAll(".pickReq").forEach((b) => b.addEventListener("change", refresh));
    document.getElementById("selAll").addEventListener("change", (e) => { boxes().forEach((b) => { b.checked = e.target.checked; }); refresh(); });
    btn.addEventListener("click", async () => {
      const requests = boxes().filter((b) => b.checked).map((b) => b.value);
      const res = await API.postJSON("/api/requisitions/bulk-approve", { requests });
      if (!res.ok) { note.textContent = res.data.detail || "That did not work."; return; }
      const refused = Object.entries(res.data.refused || {});
      if (refused.length) {
        note.textContent = `${res.data.approved.length} approved. Left alone: ${refused.map(([no, why]) => `${no} (${why})`).join("; ")}`;
        setTimeout(() => this.renderList(document.getElementById("appContent")), 4000);
      } else {
        this.renderList(document.getElementById("appContent"));
      }
    });
  },

  // The New request screen: pick items from the catalogue, say how many, by when, and where. Saved as a draft.
  async renderForm(container) {
    container.innerHTML = `<div class="page-header"><h1>New request</h1></div><div class="panel">Loading…</div>`;
    let form;
    try {
      form = await API.getJSON("/api/requisition-form");
    } catch {
      container.innerHTML = `<div class="empty-next"><strong>You can't raise requests here.</strong><br>Ask your administrator to give your role the right to raise requests.</div>`;
      return;
    }
    const itemOptions = `<option value="">Choose an item…</option>${form.items.map((i) => `<option value="${this.esc(i.code)}" data-uom="${this.esc(i.uom || "")}">${this.esc(i.name)} (${this.esc(i.code)})</option>`).join("")}`;
    const row = () => `<div class="form-grid req-line" style="grid-template-columns:minmax(220px,3fr) minmax(90px,1fr) 70px 40px;margin-bottom:8px;">
        <label>Item<select class="req-item">${itemOptions}</select></label>
        <label>Quantity<input class="req-qty" type="number" min="0" step="any" placeholder="0"></label>
        <label>Unit<span class="req-uom cell-muted" style="padding:10px 0;">\u2014</span></label>
        <button type="button" class="tab req-remove" title="Remove this line">\u2715</button>
      </div>`;
    container.innerHTML = `
      <div class="page-header"><a href="#/requisitions" style="color:var(--text-muted);font-size:12px;text-decoration:none;">\u2190 Requests</a>
        <h1 style="margin-top:6px;">New request</h1><p>Say what you need. It is saved as a draft; you submit it for approval when it is right.</p></div>
      <div class="panel" style="max-width:860px;">
        <div id="reqLines">${row()}</div>
        <button type="button" class="btn btn-sm" id="addLine"><i class="ti ti-plus"></i>Add another item</button>
        <div class="form-grid" style="margin-top:18px;">
          <label>Needed by<input id="reqNeed" type="date" value="${this.esc(form.need_by)}"></label>
          <label>Deliver to<select id="reqLoc">${form.locations.length > 1 && !form.location ? '<option value="">Choose…</option>' : ""}${form.locations.map((l) => `<option value="${this.esc(l.code)}"${l.code === form.location ? " selected" : ""}>${this.esc(l.name)}</option>`).join("")}</select></label>
        </div>
        <label style="display:flex;flex-direction:column;gap:5px;font-size:12px;color:var(--text-secondary);margin-top:12px;">Why it is needed (optional)<textarea id="reqWhy" rows="2" placeholder="For example: spare for the line 3 repair"></textarea></label>
        <div class="form-actions"><button type="button" class="btn btn-primary" id="reqSave">Save as draft</button><a class="btn" href="#/requisitions">Cancel</a></div>
        <div class="note-line" id="reqNote"></div>
      </div>`;
    const lines = document.getElementById("reqLines");
    const wire = () => {
      lines.querySelectorAll(".req-line").forEach((el) => {
        el.querySelector(".req-item").onchange = (e) => { el.querySelector(".req-uom").textContent = e.target.selectedOptions[0].dataset.uom || "\u2014"; };
        el.querySelector(".req-remove").onclick = () => { if (lines.children.length > 1) el.remove(); };
      });
    };
    wire();
    document.getElementById("addLine").onclick = () => { lines.insertAdjacentHTML("beforeend", row()); wire(); };
    document.getElementById("reqSave").onclick = async () => {
      const note = document.getElementById("reqNote");
      const picked = [...lines.querySelectorAll(".req-line")].map((el) => ({ item_code: el.querySelector(".req-item").value, qty: el.querySelector(".req-qty").value })).filter((l) => l.item_code || l.qty);
      if (!picked.length) { note.textContent = "Choose at least one item."; return; }
      if (picked.some((l) => !l.item_code || !(Number(l.qty) > 0))) { note.textContent = "Every line needs an item and a quantity above zero."; return; }
      const res = await API.postJSON("/api/requisitions", {
        lines: picked, need_by: document.getElementById("reqNeed").value, location: document.getElementById("reqLoc").value || null,
        reason: document.getElementById("reqWhy").value,
      });
      if (res.ok) { window.location.hash = `#/requisitions/${res.data.id}`; return; }
      note.textContent = res.data.detail || "The request was not saved.";
    };
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

  ROUTE_LABEL: { contract: "contract", single_source: "single source", emergency: "emergency", reorder: "safe reorder", rfq: "quotes", direct: "direct buy", best_vendor: "best vendor" },

  // The buying route, why it was chosen and every test it ran (D37), under the line's sourcing.
  routeNote(s) {
    if (!s.route) return "";
    const checks = (s.checks || []).map((c) => `<div>${c.passed ? "\u2713" : "\u2717"} ${this.esc(c.detail)}</div>`).join("");
    const waiting = s.agreement === "pending" ? ` <span class="badge badge-pending" style="font-size:10px;">purchase manager to agree</span>` : "";
    return `${waiting}<details class="cell-muted" style="font-size:11px;margin-top:2px;"><summary>${this.esc(this.ROUTE_LABEL[s.route] || s.route)} \u00b7 ${this.esc(s.rule || "")}${s.min_quotes ? ` \u00b7 ${this.esc(s.min_quotes)} quotes` : ""}</summary><div>${this.esc(s.reason)}</div>${checks}</details>`;
  },

  sourceCell(s) {
    if (!s) return `<span class="cell-muted">\u2014</span>`;
    const order = s.po_no ? ` <span class="badge badge-approved" style="font-size:10px;">ordered</span> <a href="#/purchase-orders/${this.esc(s.po_no)}" style="color:var(--accent-tint)">${this.esc(s.po_no)}</a>` : "";
    const route = this.routeNote(s);
    if (s.method === "contract") return `<span class="badge badge-approved" style="font-size:10px;">contract</span> <span class="cell-muted">${s.route ? "" : this.esc(s.reason)}</span>${order}${route}`;
    if (s.method === "direct") return `<span class="badge badge-approved" style="font-size:10px;">${this.esc(this.ROUTE_LABEL[s.route] || "direct buy")}</span>${order}${route}`;
    if (s.method === "rfq") return `<span class="badge badge-pending" style="font-size:10px;">quotes</span> <a href="#/rfqs/${this.esc(s.rfq_no)}" style="color:var(--accent-tint)">${this.esc(s.rfq_no)}</a>${order}${route}`;
    return `<span class="badge badge-waiting" style="font-size:10px;">buyer</span> <span class="cell-muted">${this.esc(s.reason)}</span>${route}`;
  },

  STEP_LABEL: { pending: "Waiting", waiting: "Later", approved: "Approved", rejected: "Rejected", returned: "Returned", cancelled: "Cancelled" },

  approvalPanel(r) {
    const a = r.approval || {};
    const steps = a.steps || [];
    const trail = UI.approvalSteps(steps, this.STEP_LABEL);
    const rule = a.rule_code ? `<details class="more" style="margin:0 0 10px;"><summary>How this was routed</summary><div class="cell-muted">Approval rule ${this.esc(a.rule_code)}, version ${this.esc(a.rule_version)}. The rule is kept with the request, so the decision can be reproduced later.</div></details>` : "";
    const actions = a.can_act
      ? `<div class="btn-row" id="decideBtns">
           <div class="btn btn-primary" id="approveBtn"><i class="ti ti-check"></i>Approve</div>
           <div class="btn" id="lowerBtn" title="Approve a smaller quantity"><i class="ti ti-adjustments"></i>Approve less…</div>
           <div class="btn" id="returnBtn"><i class="ti ti-arrow-back-up"></i>Return for changes</div>
           <div class="btn btn-danger" id="rejectBtn"><i class="ti ti-x"></i>Reject</div>
         </div>`
      : a.can_submit
        ? `<div class="btn btn-primary" id="submitBtn"><i class="ti ti-send"></i>Submit for approval</div>`
        : "";
    const own = [
      a.can_withdraw ? `<div class="btn btn-sm" id="withdrawBtn"><i class="ti ti-arrow-back-up"></i>Withdraw</div>` : "",
      a.can_cancel ? `<div class="btn btn-sm" id="cancelReqBtn"><i class="ti ti-ban"></i>Cancel request</div>` : "",
      a.can_copy ? `<div class="btn btn-sm" id="copyBtn"><i class="ti ti-copy"></i>Copy as a new request</div>` : "",
      a.can_remind ? `<div class="btn btn-sm" id="remindBtn"><i class="ti ti-bell-ringing"></i>Remind approver</div>` : "",
    ].join("");
    return `
      <div class="panel" style="margin-bottom:12px;">
        <div class="panel-title">Approval</div>
        ${a.decision ? `<p style="margin:0 0 10px;font-size:12.5px;color:var(--text-secondary);">${this.esc(String(a.decision).replace(/ under rule [A-Za-z0-9-]+ v\d+/, ""))}</p>` : `<p style="margin:0 0 10px;font-size:12.5px;color:var(--text-muted);">Not submitted yet.</p>`}
        ${(a.budget || []).filter((b) => b.status !== "unchecked").map((b) => `<div class="budget-check" style="margin:0 0 6px;font-size:12px;color:${b.status === "ok" ? "var(--text-secondary)" : "var(--warning-text)"};">Budget account ${this.esc(b.gl_code)}${b.holder ? ` of ${this.esc(b.holder)}` : ""}${b.period ? `, ${this.esc(b.period)}` : ""}: ${formatCurrency(b.budget)} in all, ${formatCurrency(b.committed)} already committed, this request asks for ${formatCurrency(b.requested)}${b.used_pct ? ` · ${this.esc(b.used_pct)}% used` : ""}${b.status === "over" ? ` · over the budget by ${formatCurrency(b.over_by)}` : b.status === "warn" ? " · nearly used up" : ""}</div>`).join("")}
        ${trail}${rule}${actions}
        ${own ? `<div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:10px;">${own}</div>` : ""}
        ${a.can_source ? `<div class="btn btn-primary" id="sourceBtn" style="margin-top:6px;"><i class="ti ti-search"></i>Find suppliers</div>` : ""}
        ${a.can_act ? `<div id="lowerBox" style="display:none;margin-top:10px;">
          <div class="cell-muted" style="font-size:12px;margin-bottom:6px;">Approve less than was asked. A quantity can go down, not up. A reason is needed.</div>
          ${(r.lines || []).filter((l) => l.status !== "cancelled").map((l) => `<div style="display:flex;gap:10px;align-items:center;margin-bottom:6px;font-size:12.5px;">
            <span style="flex:1;">${this.esc(l.name)}</span>
            <input type="number" class="lowerQty" data-line="${this.esc(l.line_no)}" min="0.0001" max="${this.esc(l.qty)}" step="any" value="${this.esc(l.qty)}" style="width:110px;padding:5px;">
            <span class="cell-muted">${this.esc(l.uom || "")} of ${this.esc(l.qty)}</span></div>`).join("")}
          <textarea id="lowerReason" rows="2" placeholder="Reason (required)" style="width:100%;background:var(--surface-sunken);border:1px solid var(--border);color:var(--text-primary);border-radius:8px;padding:8px;font-size:12.5px;"></textarea>
          <div style="margin-top:8px;"><div class="btn btn-primary btn-sm" id="lowerOk" style="display:inline-flex;">Approve with these quantities</div></div>
        </div>` : ""}
        <div id="actionNote" style="font-size:12px;color:var(--warning-text);margin-top:8px;"></div>
        <div id="reasonBox" style="display:none;margin-top:10px;">
          <textarea id="reasonText" rows="2" placeholder="Reason (required)" style="width:100%;background:var(--surface-sunken);border:1px solid var(--border);color:var(--text-primary);border-radius:8px;padding:8px;font-size:12.5px;"></textarea>
          <div class="btn-row" style="margin-top:8px;"><div class="btn btn-primary btn-sm" id="reasonOk">Confirm</div><div class="btn btn-sm" id="reasonCancel">Cancel</div></div>
        </div>
      </div>`;
  },

  boughtFor(sources) {
    // a combined line: what it buys for each production order and for stock
    if (!sources || !sources.length) return "";
    const part = (x) => x.stock
      ? `stock ${this.esc(x.qty)} ${this.esc(x.uom || "")}`
      : `<a href="#/material-requests" style="color:var(--accent-tint)">${this.esc(x.order_no)}</a> ${this.esc(x.qty)} ${this.esc(x.uom || "")}${x.cancelled ? " (cancelled)" : ""}`;
    return `<div class="bought-for cell-muted" style="font-size:11px;">For ${sources.map(part).join(" · ")}</div>`;
  },

  checksPanel(r) {
    const c = r.checks || { lines: [], duplicates: [] };
    const dup = (c.duplicates || []).map((d) => `
      <div style="font-size:12.5px;margin-bottom:6px;color:${d.overridden ? "var(--text-secondary)" : "var(--warning-text)"};">
        ${d.likely ? "Likely a repeat" : "Possibly a repeat"} of <a href="#/requisitions/${this.esc(d.other_request)}" style="color:var(--accent-tint)">${this.esc(d.other_request)}</a>
        (${this.esc(d.other_status)}; ${d.lines.length} line(s) alike)${d.overridden ? ` — gone ahead: “${this.esc(d.override_reason)}”` : ""}
      </div>`).join("");
    const rows = (c.lines || []).filter((l) => l.item_code);
    if (!dup && !rows.length) return "";
    return `
      <div class="panel" style="margin-bottom:12px;">
        <div class="panel-title">Check before ordering</div>
        ${dup}
        ${rows.length ? `<table class="data-table" style="margin-top:6px;"><thead><tr><th>ITEM</th><th>ON HAND</th><th>OTHER REQUESTS</th><th>ORDERED, NOT SENT</th><th>ON THE WAY</th><th>SUGGESTED TO BUY</th></tr></thead><tbody>
          ${rows.map((l) => `<tr><td>${this.esc(l.name)}</td><td>${this.esc(l.on_hand)}</td><td>${this.esc(l.open_requests)}</td><td>${this.esc(l.open_po_balance)}</td><td>${this.esc(l.inbound)}</td><td><strong>${this.esc(l.suggested_net_qty)}</strong> <span class="cell-muted">${this.esc(l.uom)}</span></td></tr>`).join("")}
        </tbody></table>` : ""}
      </div>`;
  },

  wireApproval(r) {
    const act = async (verb, body) => {
      const res = await API.postJSON(`/api/requisitions/${encodeURIComponent(r.id)}/${verb}`, body);
      if (res.ok) return this.renderDetail(document.getElementById("appContent"), r.id);
      const note = document.getElementById("actionNote");
      if (note) note.textContent = res.data.detail || res.data.error || "That did not work.";
      if (verb === "submit" && String(res.data.detail || "").includes("looks like a repeat")) {
        this.pendingVerb = "submit";  // go ahead only with a reason, which is kept with the request
        document.getElementById("reasonBox").style.display = "block";
        document.getElementById("reasonText").focus();
      }
    };
    const on = (id, fn) => { const el = document.getElementById(id); if (el) el.addEventListener("click", fn); };
    on("submitBtn", () => act("submit"));
    on("sourceBtn", () => act("source"));
    on("approveBtn", () => act("approve", {}));
    on("lowerBtn", () => { const b = document.getElementById("lowerBox"); if (b) b.style.display = b.style.display === "none" ? "block" : "none"; });
    on("lowerOk", () => {
      const quantities = {};
      document.querySelectorAll(".lowerQty").forEach((el) => { if (el.value !== "" && Number(el.value) !== Number(el.max)) quantities[el.dataset.line] = el.value; });
      act("approve", { quantities, comment: document.getElementById("lowerReason").value });
    });
    on("remindBtn", async () => {
      const res = await API.postJSON(`/api/requisitions/${encodeURIComponent(r.id)}/remind-approver`, {});
      const note = document.getElementById("actionNote");
      if (note) { note.style.color = res.ok ? "var(--text-secondary)" : "var(--warning-text)"; note.textContent = res.ok ? `${res.data.reminded} has been reminded.` : (res.data.detail || "The reminder was not sent."); }
    });
    const ask = (verb) => {
      document.getElementById("reasonBox").style.display = "block";
      document.getElementById("reasonText").focus();
      this.pendingVerb = verb;
    };
    on("returnBtn", () => ask("return"));
    on("rejectBtn", () => ask("reject"));
    on("withdrawBtn", () => act("withdraw", {}));
    on("cancelReqBtn", () => ask("cancel"));
    on("copyBtn", async () => {
      const res = await API.postJSON(`/api/requisitions/${encodeURIComponent(r.id)}/copy`, {});
      if (res.ok) { window.location.hash = `#/requisitions/${encodeURIComponent(res.data.id)}`; return; }
      const note = document.getElementById("actionNote");
      if (note) note.textContent = res.data.detail || "The request could not be copied.";
    });
    on("reasonCancel", () => { document.getElementById("reasonBox").style.display = "none"; });
    on("reasonOk", () => act(this.pendingVerb, this.pendingVerb === "submit"
      ? { override_reason: document.getElementById("reasonText").value }
      : { reason: document.getElementById("reasonText").value }));
  },

  async renderDetail(container, id) {
    container.innerHTML = `<div class="page-header"><h1>${this.esc(id)}</h1></div><div class="panel">Loading…</div>`;
    try {
      const r = await API.getRequisition(id);
      if (r.error) { container.innerHTML = `<div class="empty-state">${this.esc(r.error)}</div>`; return; }
      const lines = r.lines || [];
      container.innerHTML = `
        <div class="page-header">
          ${UI.backLink("#/requisitions", "Requests")}
          <h1>${this.esc(r.title)}</h1>
          <p>${statusBadge(r.status)} · ${this.esc(r.id)} · ${formatCurrency(r.amount)} · ${this.esc(r.requested_by)}
             ${r.ship_to ? ` · deliver to ${this.esc(r.ship_to)}` : ""}${r.need_by ? ` · needed by ${formatDate(r.need_by)}` : ""}</p>
        </div>
        ${FlowStrip.forRequisition(r)}
        <div class="doc-layout">
        <div class="doc-main">
        ${this.approvalPanel(r)}
        ${this.checksPanel(r)}
        ${lines.length ? `
        <div class="table-wrap" style="margin-bottom:12px;">
          <table class="data-table">
            <thead><tr><th>ITEM</th><th>QTY</th><th>EST. PRICE</th><th>VALUE</th><th>NEEDED BY</th><th>HOW IT IS BOUGHT</th></tr></thead>
            <tbody>
              ${lines.map((l) => `
                <tr>
                  <td>${this.esc(l.name)}<div class="cell-muted" style="font-size:11px;">${this.esc(l.item_code || "")}${l.reason ? ` · ${this.esc(l.reason)}` : ""}</div>${this.boughtFor(l.bought_for)}</td>
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
        </div>
        <div class="doc-side">
        <div class="panel">
          <div class="panel-title">Request details</div>
          ${UI.facts([
            ["Number", this.esc(r.id)],
            ["Requested by", this.esc(r.requested_by)],
            ["Estimated value", formatCurrency(r.amount)],
            ["Deliver to", this.esc(r.ship_to || "")],
            ["Needed by", r.need_by ? formatDate(r.need_by) : ""],
          ])}
        </div>
        <div class="panel">
          <div class="panel-title">Why it is needed</div>
          <p style="color:var(--text-secondary);margin:0 0 14px;">${this.esc(r.description) || "No reason was given."}</p>
          ${r.purchase_order
            ? `<div style="font-size:12.5px;color:var(--text-secondary);">Linked purchase order: <a href="#/purchase-orders/${this.esc(r.purchase_order.id)}">#${this.esc(r.purchase_order.id)}</a> ${statusBadge(r.purchase_order.status)}</div>`
            : `<div style="font-size:12.5px;color:var(--text-muted);">No purchase order linked yet.</div>`}
        </div>
        </div>
        </div>
      `;
      this.wireApproval(r);
    } catch {
      container.innerHTML = `<div class="empty-state">Couldn't load this request. Go back to the list and try again.</div>`;
    }
  },
};
