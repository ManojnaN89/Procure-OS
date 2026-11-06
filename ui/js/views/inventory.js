const InventoryView = {
  // worst first, so the page opens on what needs attention
  ORDER: ["negative", "stockout", "below_safety", "reorder", "covered", "over_max", "ok", "not_planned"],
  LABELS: {
    negative: "Negative stock",
    stockout: "Stockout",
    below_safety: "Below safety",
    reorder: "Reorder",
    covered: "On order",
    over_max: "Over max",
    ok: "OK",
    not_planned: "Not planned",
  },
  BADGE: {
    negative: "badge-rejected",
    stockout: "badge-rejected",
    below_safety: "badge-rejected",
    reorder: "badge-pending",
    covered: "badge-pending",
    over_max: "badge-pending",
    ok: "badge-approved",
    not_planned: "badge-pending",
  },

  esc(value) {
    return String(value ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  },

  async render(container) {
    this.filter = { location: "", status: "" };
    container.innerHTML = `
      <div class="page-header">
        <h1>Inventory</h1>
        <p>Stock on hand against each item's reorder level</p>
      </div>
      <div class="stat-grid" id="invStats" style="grid-template-columns:repeat(auto-fit,minmax(150px,1fr));margin-bottom:12px;"></div>
      <div style="display:flex;gap:8px;margin-bottom:12px;flex-wrap:wrap;">
        <select id="invLocation" style="background:var(--surface-sunken);border:1px solid var(--border);color:var(--text-primary);border-radius:8px;padding:7px 10px;font-size:12px;" aria-label="Location"><option value="">All locations</option></select>
        <select id="invStatus" style="background:var(--surface-sunken);border:1px solid var(--border);color:var(--text-primary);border-radius:8px;padding:7px 10px;font-size:12px;" aria-label="Status"><option value="">All statuses</option></select>
      </div>
      <div class="table-wrap" id="tableWrap"><div class="empty-state">Loading…</div></div>
    `;
    await this.load();
  },

  async load() {
    try {
      const qs = new URLSearchParams();
      if (this.filter.location) qs.set("location", this.filter.location);
      const data = await API.getJSON(`/api/inventory${qs.toString() ? `?${qs}` : ""}`);
      const wrap = document.getElementById("tableWrap");
      if (!wrap) return; // the person has already gone to another page
      this.fillControls(data);
      const lines = data.lines
        .filter((l) => !this.filter.status || l.status === this.filter.status)
        .sort((a, b) => this.ORDER.indexOf(a.status) - this.ORDER.indexOf(b.status) || a.item_code.localeCompare(b.item_code));
      if (!data.summary.total) {
        wrap.innerHTML = `<div class="empty-state">No stock on record yet. Import the master data and the stock history first.</div>`;
        return;
      }
      if (!lines.length) {
        wrap.innerHTML = `<div class="empty-state">Nothing matches this filter.</div>`;
        return;
      }
      wrap.innerHTML = `
        <table class="data-table">
          <thead><tr><th>ITEM</th><th>LOCATION</th><th>ON HAND</th><th>REORDER AT</th><th>MAX</th><th>STATUS</th></tr></thead>
          <tbody>
            ${lines.map((l) => `
              <tr>
                <td>${this.esc(l.item_name)}<div class="cell-muted" style="font-size:11px;">${this.esc(l.item_code)} · ${this.esc(l.category)}</div></td>
                <td class="cell-muted">${this.esc(l.location_code)}</td>
                <td>${this.esc(l.on_hand)} <span class="cell-muted">${this.esc(l.uom)}</span></td>
                <td class="cell-muted">${this.esc(l.reorder_point ?? "—")}</td>
                <td class="cell-muted">${this.esc(l.max_qty ?? "—")}</td>
                <td><span class="badge ${this.BADGE[l.status] || ""}">${this.esc(this.LABELS[l.status] || l.status)}</span></td>
              </tr>
            `).join("")}
          </tbody>
        </table>
      `;
    } catch {
      const gone = document.getElementById("tableWrap");
      if (gone) gone.innerHTML = `<div class="empty-state">Couldn't load inventory.</div>`;
    }
  },

  fillControls(data) {
    const stats = document.getElementById("invStats");
    if (!stats) return;
    const by = data.summary.by_status;
    const attention = ["negative", "stockout", "below_safety", "reorder"].reduce((n, k) => n + (by[k] || 0), 0);
    stats.innerHTML = `
      <div class="stat-card"><div class="stat-label">ITEM-LOCATIONS</div><div class="stat-value" style="font-size:20px;">${data.summary.total}</div></div>
      <div class="stat-card"><div class="stat-label">NEED ATTENTION</div><div class="stat-value" style="font-size:20px;">${attention}</div></div>
      <div class="stat-card"><div class="stat-label">STOCKOUTS</div><div class="stat-value" style="font-size:20px;">${by.stockout || 0}</div></div>
      <div class="stat-card"><div class="stat-label">NEGATIVE BOOKS</div><div class="stat-value" style="font-size:20px;">${by.negative || 0}</div></div>
    `;
    const loc = document.getElementById("invLocation");
    if (loc && loc.options.length === 1) {
      data.locations.forEach((l) => loc.add(new Option(`${l.name} (${l.code})`, l.code)));
      loc.addEventListener("change", () => { this.filter.location = loc.value; this.load(); });
    }
    const status = document.getElementById("invStatus");
    if (status && status.options.length === 1) {
      this.ORDER.forEach((s) => status.add(new Option(this.LABELS[s], s)));
      status.addEventListener("change", () => { this.filter.status = status.value; this.load(); });
    }
  },
};
