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
      <div id="stockAction"></div>
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
      const acts = data.can_issue || data.can_adjust || data.can_hold;
      this.lines = lines;
      wrap.innerHTML = `
        <table class="data-table">
          <thead><tr><th>ITEM</th><th>LOCATION</th><th>ON HAND</th><th>REORDER AT</th><th>MAX</th><th>STATUS</th>${acts ? "<th></th>" : ""}</tr></thead>
          <tbody>
            ${lines.map((l, i) => `
              <tr>
                <td>${this.esc(l.item_name)}<div class="cell-muted" style="font-size:11px;">${this.esc(l.item_code)} · ${this.esc(l.category)}</div></td>
                <td class="cell-muted">${this.esc(l.location_code)}</td>
                <td>${this.esc(l.on_hand)} <span class="cell-muted">${this.esc(l.uom)}</span>${this.held(l)}</td>
                <td class="cell-muted">${this.esc(l.reorder_point ?? "—")}</td>
                <td class="cell-muted">${this.esc(l.max_qty ?? "—")}</td>
                <td><span class="badge ${this.BADGE[l.status] || ""}">${this.esc(this.LABELS[l.status] || l.status)}</span></td>
                ${acts ? `<td style="white-space:nowrap;text-align:right;">
                  ${data.can_issue ? `<span class="btn btn-sm" data-stock="issue" data-i="${i}">Issue</span>` : ""}
                  ${data.can_adjust ? `<span class="btn btn-sm" data-stock="count" data-i="${i}">Count</span>` : ""}
                  ${data.can_hold ? `<span class="btn btn-sm" data-stock="hold" data-i="${i}">Hold</span>` : ""}
                  ${data.can_hold && l.holds ? `<span class="btn btn-sm" data-stock="holds" data-i="${i}">Holds</span>` : ""}
                </td>` : ""}
              </tr>
            `).join("")}
          </tbody>
        </table>
      `;
      wrap.querySelectorAll("[data-stock]").forEach((b) => {
        b.addEventListener("click", () => {
          const line = this.lines[Number(b.dataset.i)];
          if (b.dataset.stock === "hold") this.openHold(line);
          else if (b.dataset.stock === "holds") this.openHolds(line);
          else this.openAction(b.dataset.stock, line);
        });
      });
    } catch {
      const gone = document.getElementById("tableWrap");
      if (gone) gone.innerHTML = `<div class="empty-state">Couldn't load inventory.</div>`;
    }
  },

  /** The small form above the table for issuing stock or correcting it after a count. The server works out every number. */
  openAction(kind, line) {
    const box = document.getElementById("stockAction");
    if (!box) return;
    const field = 'style="background:var(--surface-sunken);border:1px solid var(--border);color:var(--text-primary);border-radius:8px;padding:7px 10px;font-size:12.5px;"';
    const issue = kind === "issue";
    box.innerHTML = `
      <div class="panel" style="margin-bottom:12px;">
        <div class="panel-title">${issue ? "Issue stock" : "Correct the books after a count"}: ${this.esc(line.item_name)} at ${this.esc(line.location_code)}</div>
        <p class="cell-muted" style="margin:0 0 10px;font-size:12.5px;">The books say ${this.esc(line.on_hand)} ${this.esc(line.uom)}.${issue ? " You cannot issue more than that." : " Enter what you counted on the shelf; the difference is worked out for you."}</p>
        <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;">
          <input id="stockQty" type="number" min="0" step="any" placeholder="${issue ? "Quantity to issue" : "Quantity counted"}" ${field}>
          <input id="stockWhy" placeholder="${issue ? "Issued to (department, machine or job)" : "Why they differ (required)"}" style="min-width:260px;" ${field}>
          <span class="btn btn-primary btn-sm" id="stockSave">${issue ? "Issue stock" : "Save the count"}</span>
          <span class="btn btn-sm" id="stockCancel">Cancel</span>
        </div>
        <div id="stockNote" style="font-size:12.5px;color:var(--warning-text);margin-top:8px;"></div>
      </div>`;
    document.getElementById("stockQty").focus();
    document.getElementById("stockCancel").addEventListener("click", () => { box.innerHTML = ""; });
    document.getElementById("stockSave").addEventListener("click", async () => {
      const qty = document.getElementById("stockQty").value;
      const why = document.getElementById("stockWhy").value;
      const body = issue
        ? { item_code: line.item_code, location: line.location_code, qty, reference: why }
        : { item_code: line.item_code, location: line.location_code, counted_qty: qty, reason: why };
      const res = await API.postJSON(issue ? "/api/inventory/issue" : "/api/inventory/count", body);
      if (!res.ok && res.data.needs_override) {
        this.askOverride(line, qty, why, res.data);
        return;
      }
      if (!res.ok) {
        document.getElementById("stockNote").textContent = res.data.detail || "That did not work.";
        return;
      }
      box.innerHTML = `<div class="panel" style="margin-bottom:12px;font-size:12.5px;">${issue ? "Stock issued." : res.data.movement ? "The books now match the count." : "The books already matched the count."}</div>`;
      await this.load();
    });
  },

  /** Muted lines under ON HAND: what is set aside, held for quality, and kept as safety stock. */
  held(l) {
    const shown = (v) => v && v !== "0";
    const parts = [];
    if (shown(l.reserved)) parts.push(`${this.esc(l.reserved)} set aside for production`);
    if (shown(l.quarantine)) parts.push(`${this.esc(l.quarantine)} in quarantine`);
    if (shown(l.blocked)) parts.push(`${this.esc(l.blocked)} blocked`);
    if (shown(l.safety_stock)) parts.push(`${this.esc(l.safety_stock)} safety stock`);
    if (l.available && l.available.startsWith("-")) parts.push(`${this.esc(l.available.slice(1))} below safety stock`);
    return parts.map((t) => `<div class="cell-muted" style="font-size:11px;">${t}</div>`).join("");
  },

  /** The issue would use safety stock: the store person may ask the manager, with a reason. */
  askOverride(line, qty, reference, info) {
    const note = document.getElementById("stockNote");
    note.innerHTML = `Only ${this.esc(info.available)} is free; ${this.esc(info.safety_stock)} is safety stock.
      <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:6px;">
        <input id="ovWhy" placeholder="Why the safety stock is needed (required)" style="min-width:260px;background:var(--surface-sunken);border:1px solid var(--border);color:var(--text-primary);border-radius:8px;padding:7px 10px;font-size:12.5px;">
        <span class="btn btn-sm" id="ovAsk">Ask to use safety stock</span>
      </div>`;
    document.getElementById("ovAsk").addEventListener("click", async () => {
      const res = await API.postJSON("/api/inventory/safety-overrides", {
        kind: "issue", item_code: line.item_code, location: line.location_code, qty, reference,
        reason: document.getElementById("ovWhy").value,
      });
      note.textContent = res.ok ? `Sent to ${res.data.approver_hint}.` : res.data.detail || "That did not work.";
    });
  },

  /** Hold usable stock as quarantine or blocked, with a reason. */
  openHold(line) {
    const box = document.getElementById("stockAction");
    if (!box) return;
    const field = 'style="background:var(--surface-sunken);border:1px solid var(--border);color:var(--text-primary);border-radius:8px;padding:7px 10px;font-size:12.5px;"';
    box.innerHTML = `
      <div class="panel" style="margin-bottom:12px;">
        <div class="panel-title">Hold stock: ${this.esc(line.item_name)} at ${this.esc(line.location_code)}</div>
        <p class="cell-muted" style="margin:0 0 10px;font-size:12.5px;">Held stock is never set aside or issued. Stock set aside for production is given back from the latest order first.</p>
        <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;">
          <input id="holdQty" type="number" min="0" step="any" placeholder="Quantity" ${field}>
          <select id="holdQuality" aria-label="Quality" ${field}><option value="quarantine">Quarantine</option><option value="blocked">Blocked</option></select>
          <input id="holdWhy" placeholder="Why (damage, expiry, suspect lot)" style="min-width:240px;" ${field}>
          <span class="btn btn-primary btn-sm" id="holdSave">Hold</span>
          <span class="btn btn-sm" id="holdCancel">Cancel</span>
        </div>
        <div id="holdNote" style="font-size:12.5px;color:var(--warning-text);margin-top:8px;"></div>
      </div>`;
    document.getElementById("holdCancel").addEventListener("click", () => { box.innerHTML = ""; });
    document.getElementById("holdSave").addEventListener("click", async () => {
      const res = await API.postJSON("/api/inventory/holds", {
        item_code: line.item_code, location: line.location_code, qty: document.getElementById("holdQty").value,
        quality: document.getElementById("holdQuality").value, reason: document.getElementById("holdWhy").value,
      });
      if (!res.ok) {
        document.getElementById("holdNote").textContent = res.data.detail || "That did not work.";
        return;
      }
      box.innerHTML = `<div class="panel" style="margin-bottom:12px;font-size:12.5px;">Stock held.</div>`;
      await this.load();
    });
  },

  /** The open holds of a line, each with its decision. */
  async openHolds(line) {
    const box = document.getElementById("stockAction");
    if (!box) return;
    const qs = new URLSearchParams({ item_code: line.item_code, location: line.location_code });
    const data = await API.getJSON(`/api/inventory/holds?${qs}`);
    const field = 'style="background:var(--surface-sunken);border:1px solid var(--border);color:var(--text-primary);border-radius:8px;padding:6px 8px;font-size:12px;width:90px;"';
    box.innerHTML = `
      <div class="panel" style="margin-bottom:12px;">
        <div class="panel-title">Held stock: ${this.esc(line.item_name)} at ${this.esc(line.location_code)}</div>
        <table class="data-table"><thead><tr><th>HELD</th><th>QUALITY</th><th>LEFT</th><th>REASON</th><th></th></tr></thead><tbody>
          ${data.holds.map((h) => `<tr>
            <td class="cell-muted">${this.esc(h.held_at.slice(0, 10))}</td>
            <td><span class="badge ${h.quality === "blocked" ? "badge-rejected" : "badge-pending"}">${this.esc(h.quality)}</span></td>
            <td>${this.esc(h.left_qty)}</td>
            <td class="cell-muted">${this.esc(h.reason)}${h.reference ? ` · ${this.esc(h.reference)}` : ""}</td>
            <td style="white-space:nowrap;">${h.source === "receipt" ? '<span class="cell-muted">Decided by the inspection</span>' : `
              <input type="number" min="0" step="any" placeholder="To usable" data-usable="${h.id}" ${field}>
              ${h.quality === "quarantine" ? `<input type="number" min="0" step="any" placeholder="To blocked" data-blocked="${h.id}" ${field}>` : ""}
              <input placeholder="Reason" data-why="${h.id}" ${field.replace("width:90px", "width:160px")}>
              <span class="btn btn-sm" data-release="${h.id}">Release</span>`}</td>
          </tr>`).join("")}
        </tbody></table>
        <div id="holdNote" style="font-size:12.5px;color:var(--warning-text);margin-top:8px;"></div>
        <span class="btn btn-sm" id="holdClose" style="margin-top:8px;">Close</span>
      </div>`;
    document.getElementById("holdClose").addEventListener("click", () => { box.innerHTML = ""; });
    box.querySelectorAll("[data-release]").forEach((b) => {
      b.addEventListener("click", async () => {
        const id = b.dataset.release;
        const val = (sel) => (box.querySelector(sel) || { value: "" }).value || "0";
        const res = await API.postJSON(`/api/inventory/holds/${encodeURIComponent(id)}/decide`, {
          to_usable: val(`[data-usable="${id}"]`), to_blocked: val(`[data-blocked="${id}"]`),
          reason: (box.querySelector(`[data-why="${id}"]`) || { value: "" }).value,
        });
        if (!res.ok) {
          document.getElementById("holdNote").textContent = res.data.detail || "That did not work.";
          return;
        }
        await this.load();
        await this.openHolds(line);
      });
    });
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
