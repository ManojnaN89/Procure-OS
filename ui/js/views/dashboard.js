const Dashboard = {
  mounted: false,

  greeting() {
    const h = new Date().getHours();
    const part = h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";
    const name = String((Auth.me && Auth.me.display_name) || "").trim();
    return name ? `${part}, ${name}` : part;
  },

  // The start page: what needs this person first, then where everything stands. Figures and charts are one click away.
  async render(container) {
    this.mounted = true;
    this.analyticsLoaded = false;
    const canRaise = Auth.can("request.create");
    container.innerHTML = `
      <div class="page-header page-header-row">
        <div>
          <h1>${escapeHtml(this.greeting())}</h1>
          <p>What needs you today, and where the purchases in progress stand.</p>
        </div>
        ${canRaise ? `<a class="btn btn-primary" href="#/requisitions/new"><i class="ti ti-plus"></i>New request</a>` : ""}
      </div>

      <div id="myWork" style="margin-bottom:20px;"></div>

      <h2 class="section-title">Where things stand</h2>
      <div id="stageRow" class="stage-row"><div class="empty-state" style="grid-column:1/-1;padding:16px;">Loading…</div></div>

      <div class="panel" id="attentionPanel" style="margin-bottom:16px;display:none;">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:2px;">
          <div style="display:flex;align-items:center;gap:8px;">
            <span class="panel-title" style="margin:0;">Notices</span>
            <span id="attentionBadges"></span>
          </div>
          <a href="#/notification-history" style="color:var(--text-muted);font-size:12px;text-decoration:none;">View all</a>
        </div>
        <div id="attentionList"></div>
      </div>

      <details class="more" id="analytics">
        <summary>The company in figures</summary>
        <div class="stat-grid" id="headlineGrid" style="margin-bottom:16px;"></div>
        <div class="two-col">
          <div class="panel">
            <div class="panel-row"><span class="panel-title">Requests</span></div>
            <div id="reqStatusBlock"></div>
          </div>
          <div class="panel">
            <div class="panel-row"><span class="panel-title">Ordered by category (goods, before tax)</span></div>
            <div id="spendBlock"></div>
          </div>
        </div>
      </details>
    `;
    if (typeof WorkspaceView !== "undefined") WorkspaceView.mount(document.getElementById("myWork"));
    document.getElementById("analytics").addEventListener("toggle", (e) => {
      if (e.target.open && !this.analyticsLoaded) { this.analyticsLoaded = true; this.load(); }
    });
    this.loadStages();
    await this.loadAttention();
  },

  // The flow as six counts. Each one opens the screen where that work is done.
  async loadStages() {
    const row = document.getElementById("stageRow");
    if (!row) return;
    const GROUPS = [
      { label: "Waiting for approval", keys: ["request_approval"], link: "#/requisitions" },
      { label: "Being priced", keys: ["sourcing", "rfq_out", "award"], link: "#/rfqs" },
      { label: "Orders to approve or confirm", keys: ["po_approval", "po_sent"], link: "#/purchase-orders" },
      { label: "Goods to come", keys: ["awaiting_receipt"], link: "#/receiving" },
      { label: "Invoices to enter or fix", keys: ["awaiting_invoice", "invoice_blocked"], link: "#/invoices" },
      { label: "To pay", keys: ["awaiting_payment"], link: "#/payments" },
    ];
    try {
      const t = await API.getJSON("/api/control-tower");
      if (!document.body.contains(row)) return;
      const by = Object.fromEntries(t.stages.map((s) => [s.stage, Number(s.count) || 0]));
      row.innerHTML = GROUPS.map((g) => {
        const n = g.keys.reduce((sum, k) => sum + (by[k] || 0), 0);
        return `<a class="stage-chip${n ? " hot" : ""}" href="${g.link}"><div class="n">${n}</div><div class="l">${g.label}</div></a>`;
      }).join("");
    } catch {
      row.style.display = "none"; // a role that may not see the tower just has no strip
      const title = row.previousElementSibling;
      if (title && title.classList.contains("section-title")) title.style.display = "none";
    }
  },

  async load() {
    try {
      const d = await API.getDashboard();
      this.renderHeadline(d);
      this.renderReqStatus(d);
      this.renderSpend(d);
    } catch (err) {
      const grid = document.getElementById("headlineGrid");
      if (grid) grid.innerHTML = `<div class="empty-state" style="grid-column:1/-1;">Couldn't load the figures. Reload the page to try again.</div>`;
    }
  },

  refreshIfMounted() {
    if (this.mounted && document.getElementById("stageRow")) {
      if (this.analyticsLoaded) this.load();
      this.loadStages();
      this.loadAttention();
    }
  },

  // Four figures counted from the real records: what has been ordered, what has been paid, and the two that need a person.
  renderHeadline(d) {
    const grid = document.getElementById("headlineGrid");
    if (!grid) return;
    const tiles = [
      { label: "ORDERED SO FAR", value: formatCurrency(d.total_po_value), sub: `${d.purchase_orders} order${d.purchase_orders === 1 ? "" : "s"} placed`, link: "#/purchase-orders" },
      { label: "PAID SO FAR", value: formatCurrency(d.paid_total), sub: `${d.invoices_to_pay} invoice${d.invoices_to_pay === 1 ? "" : "s"} approved and waiting to be paid`, link: "#/payments" },
      { label: "INVOICES BLOCKED", value: String(d.blocked_invoices), sub: "held back by the match", link: "#/invoices", hot: d.blocked_invoices > 0 },
      { label: "QUOTATIONS OUT", value: String(d.open_rfqs), sub: `${d.vendors} active supplier${d.vendors === 1 ? "" : "s"}`, link: "#/rfqs" },
    ];
    grid.innerHTML = tiles.map((c) => `
      <a class="stat-card stat-card-clickable" href="${c.link}" style="text-decoration:none;${c.hot ? "border-color:var(--warning);" : ""}">
        <div class="stat-label">${c.label}</div>
        <div class="stat-value">${escapeHtml(c.value)}</div>
        <div class="cell-muted" style="margin-top:6px;">${escapeHtml(c.sub)}</div>
      </a>`).join("");
  },

  renderReqStatus(d) {
    const el = document.getElementById("reqStatusBlock");
    if (!el) return;
    const rows = [
      ["Drafts and returned", d.draft_requisitions, "var(--text-muted)"],
      ["Waiting for approval", d.pending_requisitions, "var(--warning)"],
      ["Approved", d.approved_requisitions, "var(--success)"],
      ["Rejected", d.rejected_requisitions, "var(--danger)"],
    ];
    const total = rows.reduce((sum, r) => sum + (r[1] || 0), 0);
    if (!total) {
      el.innerHTML = `<div class="empty-next" style="padding:14px;"><strong>No requests yet.</strong></div>`;
      return;
    }
    el.innerHTML = `
      <div class="progress-bar">${rows.map((r) => `<div style="width:${Math.round(((r[1] || 0) / total) * 100)}%;background:${r[2]}"></div>`).join("")}</div>
      <div class="progress-legend">${rows.map((r) => `<div class="row"><span>\u25cf ${r[0]}</span><span>${r[1] || 0}</span></div>`).join("")}</div>`;
  },

  renderSpend(d) {
    const el = document.getElementById("spendBlock");
    if (!el) return;
    const entries = Object.entries(d.spend_by_category || {});
    if (!entries.length) {
      el.innerHTML = `<div class="empty-state">Nothing has been ordered yet.</div>`;
      return;
    }
    const max = Math.max(...entries.map(([, v]) => Number(v)));
    el.innerHTML = entries.map(([cat, val]) => `
      <div class="category-row" data-category="${escapeHtml(cat)}" style="margin-bottom:10px;cursor:pointer;" title="Ask the assistant about this category">
        <div style="display:flex;justify-content:space-between;font-size:11.5px;margin-bottom:4px;">
          <span style="color:var(--text-secondary)">${escapeHtml(cat)}</span>
          <span style="color:var(--text-primary)">${formatCurrency(val)}</span>
        </div>
        <div style="background:var(--surface-sunken);border-radius:6px;height:6px;overflow:hidden;">
          <div style="width:${max ? (Number(val) / max) * 100 : 0}%;background:var(--accent);height:100%;"></div>
        </div>
      </div>
    `).join("");

    el.querySelectorAll(".category-row").forEach((row) => {
      row.addEventListener("click", () => {
        const category = row.dataset.category;
        if (typeof FloatingAssistant !== "undefined") FloatingAssistant.open();
        Conversation.send(`Tell me about ${category} spend`);
      });
    });
  },

  TYPE_ICONS: {
    exception_flagged: { icon: "ti-alert-triangle", bg: "#412402", fg: "#FAC775" },
    vendor_risk: { icon: "ti-building-store", bg: "#501313", fg: "#F09595" },
    stale_requisition: { icon: "ti-clock", bg: "#412402", fg: "#FAC775" },
    budget_overrun: { icon: "ti-chart-bar", bg: "#412402", fg: "#FAC775" },
    contract_expiring: { icon: "ti-file-certificate", bg: "#3C3489", fg: "#CECBF6" },
  },

  async loadAttention() {
    const list = document.getElementById("attentionList");
    const badges = document.getElementById("attentionBadges");
    if (!list) return;
    try {
      // a task notice repeats what My work already lists, so only the other notices are shown here
      const rows = (await API.listNotifications({ unresolved_only: true, limit: 20 })).filter((n) => n.type !== "task");
      const panel = document.getElementById("attentionPanel");
      if (panel) panel.style.display = rows.length ? "" : "none";
      if (!rows.length) {
        list.innerHTML = "";
        badges.innerHTML = "";
        return;
      }
      const counts = { high: 0, medium: 0, low: 0 };
      rows.forEach((n) => { counts[n.severity] = (counts[n.severity] || 0) + 1; });
      badges.innerHTML = ["high", "medium", "low"].filter((s) => counts[s]).map((s) => `
        <span style="background:${s === "high" ? "var(--danger-bg)" : s === "medium" ? "var(--warning-bg)" : "var(--accent-dim)"};
                     color:${s === "high" ? "var(--danger-text)" : s === "medium" ? "var(--warning-text)" : "var(--accent-tint)"};
                     font-size:9.5px;font-weight:600;padding:3px 10px;border-radius:20px;margin-right:5px;">${counts[s]} ${s}</span>
      `).join("");

      list.innerHTML = `
        <div style="display:flex;flex-direction:column;gap:8px;">
          ${rows.map((n) => {
            const t = this.TYPE_ICONS[n.type] || { icon: "ti-bell", bg: "var(--surface-sunken)", fg: "var(--text-muted)" };
            return `
              <div class="attention-row" data-id="${escapeHtml(n.id)}">
                <div class="attention-row-icon" style="background:${t.bg}"><i class="ti ${t.icon}" style="color:${t.fg}"></i></div>
                <div class="attention-row-body">
                  <div class="attention-row-type" style="color:${t.fg}">${Notifications.labelFor(n.type)}</div>
                  <div class="attention-row-msg">${escapeHtml(n.message)}</div>
                </div>
                <i class="ti ti-chevron-right attention-row-chevron"></i>
              </div>
            `;
          }).join("")}
        </div>
      `;
      list.querySelectorAll(".attention-row").forEach((row) => {
        row.addEventListener("click", () => {
          const n = rows.find((r) => String(r.id) === row.dataset.id);
          if (n) Notifications.handleClick(n);
        });
      });
    } catch {
      list.innerHTML = `<div class="empty-state">Couldn't load signals.</div>`;
    }
  },
};
