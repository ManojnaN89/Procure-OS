const WorkspaceView = {
  esc(value) {
    return String(value ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  },

  SEVERITY: { high: "badge-rejected", medium: "badge-pending", low: "badge-approved" },

  // A link that starts with "/" leaves this page (the admin console); the others are screens of this page.
  href(link) {
    return String(link || "#").startsWith("/") ? this.esc(link) : this.esc(link);
  },

  async mount(container) {
    if (!container) return;
    container.innerHTML = `<div class="panel"><div class="panel-title">My work</div><div class="empty-state">Loading…</div></div>`;
    try {
      const w = await API.getJSON("/api/workspace");
      if (!document.body.contains(container)) return; // the person has gone to another page
      const sections = w.sections.map((s) => `
        <div class="panel" style="margin-bottom:12px;">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">
            <a href="${this.href(s.link)}" style="color:var(--text-primary);text-decoration:none;font-weight:600;font-size:13px;">${this.esc(s.title)}</a>
            <span class="badge badge-pending">${this.esc(s.count)}</span>
          </div>
          ${s.rows.map((r) => `<div style="display:flex;justify-content:space-between;gap:12px;padding:5px 0;border-top:1px solid var(--border);font-size:12.5px;">
              <a href="${this.href(r.link)}" style="color:var(--accent-tint);text-decoration:none;">${this.esc(r.label)}</a>
              <span class="cell-muted">${this.esc(r.sub)}</span></div>`).join("")}
          ${s.count > s.rows.length ? `<div class="cell-muted" style="font-size:11.5px;margin-top:6px;">and ${this.esc(s.count - s.rows.length)} more</div>` : ""}
        </div>`).join("");
      const alerts = w.alerts.length
        ? `<div class="panel" style="margin-bottom:12px;"><div class="panel-title">Alerts for my role</div>
            ${w.alerts.map((a) => `<div style="padding:6px 0;border-top:1px solid var(--border);font-size:12.5px;">
                <span class="badge ${this.SEVERITY[a.severity] || ""}">${this.esc(a.severity)}</span>
                <a href="${this.href(a.link)}" style="color:var(--text-primary);text-decoration:none;margin-left:6px;">${this.esc(a.title)}</a>
                <div class="cell-muted" style="font-size:11.5px;margin-top:2px;">${this.esc(a.detail)}</div></div>`).join("")}</div>`
        : "";
      container.innerHTML = `
        <div class="panel-title" style="margin-bottom:8px;">My work${w.roles.length ? ` <span class="cell-muted" style="font-weight:400;">· ${this.esc(w.roles.join(", "))}</span>` : ""}</div>
        ${alerts}
        ${sections || ""}
        ${!sections && !alerts ? `<div class="empty-state">Nothing needs your action right now.</div>` : ""}`;
    } catch {
      if (document.body.contains(container)) container.innerHTML = `<div class="panel"><div class="empty-state">Couldn't load your work.</div></div>`;
    }
  },
};
