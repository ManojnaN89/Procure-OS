const Dashboard = {
  mounted: false,

  async render(container) {
    this.mounted = true;
    container.innerHTML = `
      <div class="page-header">
        <h1>Procurement overview</h1>
        <p>Auctions, RFQs, requisitions and spend across all categories \u2014 live from the database.</p>
      </div>

      <div id="operationalStrip" style="display:flex;gap:18px;margin-bottom:16px;font-family:var(--font-mono);font-size:11px;color:var(--text-muted);"></div>

      <div class="panel" id="attentionPanel" style="margin-bottom:16px;">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:2px;">
          <div style="display:flex;align-items:center;gap:8px;">
            <span class="panel-title" style="margin:0;">Needs attention</span>
            <span id="attentionBadges"></span>
          </div>
          <a href="#/notification-history" style="color:var(--text-muted);font-size:11px;text-decoration:none;">View all</a>
        </div>
        <p style="color:var(--text-muted);font-size:11px;margin:0 0 12px;">Surfaced automatically \u2014 nothing here was asked for</p>
        <div id="attentionList"><div class="empty-state">Loading\u2026</div></div>
      </div>

      <div class="stat-grid" id="headlineGrid" style="grid-template-columns:repeat(3,1fr);margin-bottom:16px;"></div>

      <div class="two-col">
        <div class="panel">
          <div class="panel-row"><span class="panel-title">Requisition status</span></div>
          <div id="reqStatusBlock"></div>
          <div id="cycleTimeStat" style="margin-top:12px;padding-top:12px;border-top:1px solid var(--border);"></div>
        </div>
        <div class="panel">
          <div class="panel-row">
            <span class="panel-title">Spend by category</span>
          </div>
          <div id="spendBlock"></div>
          <div id="secondaryStats" style="margin-top:12px;padding-top:12px;border-top:1px solid var(--border);display:flex;gap:20px;"></div>
        </div>
      </div>
    `;
    await this.load();
    await this.loadAttention();
  },

  async load() {
    try {
      const d = await API.getDashboard();
      this.renderOperationalStrip(d);
      this.renderHeadline(d.headline);
      this.renderReqStatus(d);
      this.renderCycleTime(d.inline_stats);
      this.renderSpend(d);
      this.renderSecondaryStats(d.inline_stats);
    } catch (err) {
      const grid = document.getElementById("headlineGrid");
      if (grid) grid.innerHTML = `<div class="empty-state">Couldn't load dashboard data. Is the backend running?</div>`;
    }
  },

  renderOperationalStrip(d) {
    const el = document.getElementById("operationalStrip");
    if (!el) return;
    el.innerHTML = `
      <span><a href="#/auctions" style="color:inherit;text-decoration:none;">${d.open_auctions} open auctions</a></span>
      <span><a href="#/rfqs" style="color:inherit;text-decoration:none;">${d.open_rfqs} open RFQs</a></span>
      <span><a href="#/requisitions" style="color:inherit;text-decoration:none;">${d.pending_requisitions} pending requisitions</a></span>
      <span><a href="#/vendors" style="color:inherit;text-decoration:none;">${d.vendors} vendors</a></span>
      <span><a href="#/purchase-orders" style="color:inherit;text-decoration:none;">${d.purchase_orders} purchase orders</a></span>
    `;
  },

  refreshIfMounted() {
    if (this.mounted && document.getElementById("headlineGrid")) { this.load(); this.loadAttention(); }
  },

  renderHeadline(h) {
    const grid = document.getElementById("headlineGrid");
    if (!grid || !h) return;
    grid.style.gridTemplateColumns = "repeat(4, 1fr)";

    const cards = [
      {
        id: "total-spend", icon: "ti-currency-rupee", iconBg: "#3C3489", iconFg: "#CECBF6",
        label: "TOTAL SPEND", value: formatCurrency(h.total_spend),
        changePct: h.total_spend_change_pct, trend: h.total_spend_trend, sparkColor: "#9B8CFF",
        prompt: "Show me my spend trend over the last 6 months",
      },
      {
        id: "cost-savings", icon: "ti-piggy-bank", iconBg: "#085041", iconFg: "#9FE1CB",
        label: "COST SAVINGS THIS PERIOD", value: formatCurrency(h.cost_savings_period),
        changePct: h.cost_savings_change_pct, trend: h.cost_savings_trend, sparkColor: "#2ECC71",
        prompt: "Show me budget versus actual spend for this month",
      },
      {
        id: "exceptions", icon: "ti-alert-triangle", iconBg: "#501313", iconFg: "#F09595",
        label: "EXCEPTIONS", value: String(h.exceptions_count),
        changePct: h.exceptions_change_pct, trend: h.exceptions_trend, sparkColor: "#EF4444",
        invertTrendColor: true, // for exceptions, going UP is bad — flip the green/red convention
        onClick: () => { if (typeof Notifications !== "undefined") Notifications.toggleDropdown(); },
      },
    ];

    grid.innerHTML = cards.map((c) => {
      const hasChange = c.changePct !== null && c.changePct !== undefined;
      let pillClass = "";
      let pillLabel = "";
      if (hasChange) {
        const isIncrease = c.changePct >= 0;
        const badIfUp = !!c.invertTrendColor;
        const isGood = badIfUp ? !isIncrease : isIncrease;
        pillClass = isGood ? "trend-up" : "trend-down";
        pillLabel = `${isIncrease ? "\u2197" : "\u2198"} ${Math.abs(c.changePct)}%`;
      }
      return `
        <div class="stat-card stat-card-clickable" data-card-id="${c.id}" title="Click for more detail">
          <div class="stat-card-top">
            <div class="stat-icon" style="background:${c.iconBg}"><i class="ti ${c.icon}" style="color:${c.iconFg}"></i></div>
            ${hasChange ? `<span class="trend-pill ${pillClass}">${pillLabel}</span>` : ""}
          </div>
          <div class="stat-label">${c.label}</div>
          <div class="stat-value">${c.value}</div>
          <div class="stat-card-sparkline"><canvas id="spark-${c.id}"></canvas></div>
        </div>
      `;
    }).join("") + `
        <div class="stat-card">
          <div style="display:flex;align-items:center;gap:5px;">
            <div class="stat-label">SPEND UNDER MANAGEMENT</div>
            <i class="ti ti-info-circle" style="font-size:12px;color:var(--text-muted);cursor:help;" title="${h.proxy_note}"></i>
          </div>
          <div class="stat-value">${h.spend_under_management_pct}%</div>
          <div style="display:flex;height:5px;border-radius:6px;overflow:hidden;margin-top:8px;">
            <div style="width:${h.spend_under_management_pct}%;background:var(--accent);"></div>
            <div style="width:${h.maverick_spend_pct}%;background:var(--warning);"></div>
          </div>
          <div style="font-size:10px;color:var(--text-muted);margin-top:4px;">${h.maverick_spend_pct}% maverick spend</div>
        </div>
    `;

    cards.forEach((c) => this.renderSparkline(`spark-${c.id}`, c.trend, c.sparkColor));

    // Click-through: an unambiguous question (the click IS the whole question,
    // nothing more to type) sends immediately rather than just pre-filling —
    // same reasoning already used for notification routing, applied to reads.
    grid.querySelectorAll(".stat-card-clickable").forEach((cardEl) => {
      const card = cards.find((c) => c.id === cardEl.dataset.cardId);
      if (!card) return;
      cardEl.addEventListener("click", () => {
        if (card.onClick) { card.onClick(); return; }
        if (card.prompt) {
          if (typeof FloatingAssistant !== "undefined") FloatingAssistant.open();
          Conversation.send(card.prompt);
        }
      });
    });
  },

  renderSparkline(canvasId, data, color) {
    const canvas = document.getElementById(canvasId);
    if (!canvas || typeof Chart === "undefined" || !data) return;
    new Chart(canvas, {
      type: "line",
      data: {
        labels: data.map((_, i) => i),
        datasets: [{
          data,
          borderColor: color,
          borderWidth: 1.5,
          tension: 0.35,
          pointRadius: 0,
          fill: false,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: false }, tooltip: { enabled: false } },
        scales: { x: { display: false }, y: { display: false } },
      },
    });
  },

  renderReqStatus(d) {
    const el = document.getElementById("reqStatusBlock");
    if (!el) return;
    const total = (d.pending_requisitions || 0) + (d.approved_requisitions || 0) + (d.rejected_requisitions || 0);
    const pct = (n) => total ? Math.round((n / total) * 100) : 0;
    el.innerHTML = `
      <div class="progress-bar">
        <div style="width:${pct(d.pending_requisitions)}%;background:var(--warning)"></div>
        <div style="width:${pct(d.approved_requisitions)}%;background:var(--success)"></div>
        <div style="width:${pct(d.rejected_requisitions)}%;background:var(--danger)"></div>
      </div>
      <div class="progress-legend">
        <div class="row"><span>\u25cf Pending</span><span>${d.pending_requisitions || 0}</span></div>
        <div class="row"><span>\u25cf Approved</span><span>${d.approved_requisitions || 0}</span></div>
        <div class="row"><span>\u25cf Rejected</span><span>${d.rejected_requisitions || 0}</span></div>
      </div>
    `;
  },

  renderCycleTime(stats) {
    const el = document.getElementById("cycleTimeStat");
    if (!el || !stats) return;
    const val = stats.po_cycle_time_days;
    el.innerHTML = `
      <div style="display:flex;justify-content:space-between;font-size:11.5px;">
        <span style="color:var(--text-muted);">Avg. PO cycle time</span>
        <span style="color:var(--text-primary);font-weight:500;">${val === null ? "\u2014" : val + " days"}</span>
      </div>
    `;
  },

  renderSecondaryStats(stats) {
    const el = document.getElementById("secondaryStats");
    if (!el || !stats) return;
    const otd = stats.on_time_delivery_rate_pct;
    const cc = stats.contract_coverage_pct;
    el.innerHTML = `
      <div style="flex:1;">
        <div style="color:var(--text-muted);font-size:10.5px;">On-time delivery</div>
        <div style="color:var(--text-primary);font-size:15px;font-weight:600;">${otd === null ? "\u2014" : otd + "%"}</div>
      </div>
      <div style="flex:1;">
        <div style="color:var(--text-muted);font-size:10.5px;">Contract coverage</div>
        <div style="color:var(--text-primary);font-size:15px;font-weight:600;">${cc}%</div>
      </div>
    `;
  },

  renderSpend(d) {
    const el = document.getElementById("spendBlock");
    if (!el) return;
    const entries = Object.entries(d.spend_by_category || {});
    if (!entries.length) {
      el.innerHTML = `<div class="empty-state">No purchase orders yet \u2014 spend will show up here once POs are created.</div>`;
      return;
    }
    const max = Math.max(...entries.map(([, v]) => v));
    el.innerHTML = entries.map(([cat, val]) => `
      <div class="category-row" data-category="${cat}" style="margin-bottom:10px;cursor:pointer;" title="Click to see ${cat} vendor detail">
        <div style="display:flex;justify-content:space-between;font-size:11.5px;margin-bottom:4px;">
          <span style="color:var(--text-secondary)">${cat}</span>
          <span style="color:var(--text-primary)">${formatCurrency(val)}</span>
        </div>
        <div style="background:var(--surface-sunken);border-radius:6px;height:6px;overflow:hidden;">
          <div style="width:${max ? (val / max) * 100 : 0}%;background:var(--accent);height:100%;"></div>
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
      const rows = await API.listNotifications({ unresolved_only: true, limit: 20 });
      if (!rows.length) {
        list.innerHTML = `<div class="empty-state">Nothing needs attention right now.</div>`;
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
              <div class="attention-row" data-id="${n.id}">
                <div class="attention-row-icon" style="background:${t.bg}"><i class="ti ${t.icon}" style="color:${t.fg}"></i></div>
                <div class="attention-row-body">
                  <div class="attention-row-type" style="color:${t.fg}">${Notifications.labelFor(n.type)}</div>
                  <div class="attention-row-msg">${n.message}</div>
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
