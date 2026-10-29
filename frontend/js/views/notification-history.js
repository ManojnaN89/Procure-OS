/**
 * Full notification history — mirrors the Agent Trail's full-history page:
 * everything, not just unresolved/recent, filterable, resolved entries
 * visually dimmed rather than hidden. Reachable via "View all" links, not
 * a sidebar item, matching how the Trail's equivalent page is reached.
 */
const NotificationHistoryView = {
  async render(container) {
    container.innerHTML = `
      <div class="page-header">
        <h1>Notification history</h1>
        <p>Every signal the system has surfaced, resolved and unresolved</p>
      </div>
      <div class="panel">
        <div style="display:flex;gap:8px;margin-bottom:14px;">
          <select id="typeFilter" style="background:var(--surface-sunken);border:1px solid var(--border);color:var(--text-primary);border-radius:8px;padding:7px 10px;font-size:12px;">
            <option value="">All types</option>
            <option value="exception_flagged">Exception flagged</option>
            <option value="vendor_risk">Vendor risk</option>
            <option value="stale_requisition">Stuck requisition</option>
            <option value="budget_overrun">Budget overrun</option>
            <option value="contract_expiring">Contract expiring</option>
          </select>
          <select id="statusFilter" style="background:var(--surface-sunken);border:1px solid var(--border);color:var(--text-primary);border-radius:8px;padding:7px 10px;font-size:12px;">
            <option value="all">All statuses</option>
            <option value="unresolved" selected>Unresolved only</option>
            <option value="resolved">Resolved only</option>
          </select>
        </div>
        <div id="historyList"><div class="empty-state">Loading\u2026</div></div>
      </div>
    `;

    document.getElementById("typeFilter").addEventListener("change", () => this.load());
    document.getElementById("statusFilter").addEventListener("change", () => this.load());
    await this.load();
  },

  TYPE_ICONS: {
    exception_flagged: { icon: "ti-alert-triangle", bg: "#412402", fg: "#FAC775" },
    vendor_risk: { icon: "ti-building-store", bg: "#501313", fg: "#F09595" },
    stale_requisition: { icon: "ti-clock", bg: "#412402", fg: "#FAC775" },
    budget_overrun: { icon: "ti-chart-bar", bg: "#412402", fg: "#FAC775" },
    contract_expiring: { icon: "ti-file-certificate", bg: "#3C3489", fg: "#CECBF6" },
  },

  async load() {
    const list = document.getElementById("historyList");
    if (!list) return;

    const type = document.getElementById("typeFilter").value;
    const status = document.getElementById("statusFilter").value;
    const params = { limit: 200, unresolved_only: false };
    if (type) params.type = type;

    try {
      let rows = await API.listNotifications(params);
      if (status === "unresolved") rows = rows.filter((n) => !n.resolved);
      if (status === "resolved") rows = rows.filter((n) => n.resolved);

      if (!rows.length) {
        list.innerHTML = `<div class="empty-state">No notifications match this filter.</div>`;
        return;
      }

      list.innerHTML = rows.map((n) => {
        const t = this.TYPE_ICONS[n.type] || { icon: "ti-bell", bg: "var(--surface-sunken)", fg: "var(--text-muted)" };
        return `
          <div class="attention-row" data-id="${n.id}" style="margin-bottom:8px;${n.resolved ? "opacity:0.5;" : ""}">
            <div class="attention-row-icon" style="background:${t.bg}"><i class="ti ${t.icon}" style="color:${t.fg}"></i></div>
            <div class="attention-row-body">
              <div class="attention-row-type" style="color:${t.fg}">
                ${Notifications.labelFor(n.type)}
                ${n.resolved ? '<span style="color:var(--text-muted);text-transform:none;font-weight:400;"> \u00b7 resolved</span>' : ""}
              </div>
              <div class="attention-row-msg">${n.message}</div>
              <div style="color:var(--text-muted);font-size:10px;margin-top:3px;">${formatDate(n.created_at)}</div>
            </div>
            <i class="ti ti-chevron-right attention-row-chevron"></i>
          </div>
        `;
      }).join("");

      list.querySelectorAll(".attention-row").forEach((row) => {
        row.addEventListener("click", () => {
          const n = rows.find((r) => String(r.id) === row.dataset.id);
          if (n) Notifications.handleClick(n);
        });
      });
    } catch {
      list.innerHTML = `<div class="empty-state">Couldn't load notification history.</div>`;
    }
  },
};
