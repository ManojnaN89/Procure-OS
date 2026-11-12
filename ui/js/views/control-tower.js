const ControlTowerView = {
  esc(value) {
    return String(value ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  },

  BUCKETS: ["0-2", "3-7", "8-14", "15+"],
  BUCKET_STYLE: { "0-2": "badge-approved", "3-7": "badge-pending", "8-14": "badge-pending", "15+": "badge-rejected" },
  KPI_LABELS: {
    spend_under_management: "Spend under management",
    touchless: "Touchless (no person touched it)",
    on_time_delivery: "Delivered by the promised date",
    first_pass_match: "Invoices matched first time",
    msme_on_time_payment: "Small suppliers paid by the due date",
    duplicates_avoided: "Duplicates stopped",
  },

  async render(container) {
    this.location = "";
    container.innerHTML = `
      <div class="page-header page-header-row">
        <div><h1>Control tower</h1><p>Where every case stands, how long it has been there (in working days), and the numbers behind it</p></div>
        <select id="ctLocation" style="padding:6px;"><option value="">All locations</option></select>
      </div>
      <div id="ctTower" class="table-wrap"><div class="empty-state">Loading…</div></div>
      <div id="ctDrill" style="margin-top:14px;"></div>
      <h2 style="font-size:14px;margin:22px 0 8px;">Key figures</h2>
      <div id="ctKpis"><div class="empty-state">Loading…</div></div>
      ${Auth.can("budget.view") ? `<h2 style="font-size:14px;margin:22px 0 8px;">Budgets, this financial year so far</h2><div id="ctBudgets"><div class="empty-state">Loading…</div></div>` : ""}`;
    document.getElementById("ctLocation").addEventListener("change", (e) => { this.location = e.target.value; document.getElementById("ctDrill").innerHTML = ""; this.loadTower(); });
    await Promise.all([this.loadTower(true), this.loadKpis(), Auth.can("budget.view") ? this.loadBudgets() : null]);
  },

  async loadTower(first) {
    const box = document.getElementById("ctTower");
    try {
      const t = await API.getJSON(`/api/control-tower${this.location ? `?location=${encodeURIComponent(this.location)}` : ""}`);
      if (!box || !document.body.contains(box)) return;
      if (first) {
        const sel = document.getElementById("ctLocation");
        sel.innerHTML = `<option value="">All locations</option>` + t.locations.map((l) => `<option value="${this.esc(l)}">${this.esc(l)}</option>`).join("");
      }
      box.innerHTML = `<table class="data-table"><thead><tr><th>STAGE</th><th>CASES</th><th>VALUE</th>${this.BUCKETS.map((b) => `<th>${this.esc(b)} DAYS</th>`).join("")}<th>OLDEST</th></tr></thead><tbody>
        ${t.stages.map((s) => `<tr>
          <td>${this.esc(s.title)}</td><td>${this.esc(s.count)}</td><td>${formatCurrency(s.value)}</td>
          ${this.BUCKETS.map((b) => `<td>${s.buckets[b] ? `<span class="badge ${this.BUCKET_STYLE[b]} ct-cell" data-stage="${this.esc(s.stage)}" data-bucket="${this.esc(b)}" style="cursor:pointer;">${this.esc(s.buckets[b])}</span>` : '<span class="cell-muted">—</span>'}</td>`).join("")}
          <td class="cell-muted">${s.count ? `${this.esc(s.oldest_days)} days` : "—"}</td></tr>`).join("")}
      </tbody></table>`;
      box.querySelectorAll(".ct-cell").forEach((el) => el.addEventListener("click", () => this.drill(el.dataset.stage, el.dataset.bucket)));
    } catch {
      if (box) box.innerHTML = `<div class="empty-state">Couldn't load the control tower.</div>`;
    }
  },

  async drill(stage, bucket) {
    const box = document.getElementById("ctDrill");
    box.innerHTML = `<div class="empty-state">Loading…</div>`;
    const q = `?bucket=${encodeURIComponent(bucket)}${this.location ? `&location=${encodeURIComponent(this.location)}` : ""}`;
    try {
      const d = await API.getJSON(`/api/control-tower/${encodeURIComponent(stage)}${q}`);
      box.innerHTML = `<div class="panel"><div class="panel-title">${this.esc(d.title)} · ${this.esc(bucket)} working days</div>
        <table class="data-table"><thead><tr><th>CASE</th><th>LOCATION</th><th>VALUE</th><th>SINCE</th><th>AGE</th></tr></thead><tbody>
        ${d.rows.map((r) => `<tr><td><a href="${this.esc(r.link)}" style="color:var(--accent-tint);">${this.esc(r.label)}</a></td><td class="cell-muted">${this.esc(r.location || "—")}</td><td>${formatCurrency(r.value)}</td><td class="cell-muted">${formatDate(r.since)}</td><td>${this.esc(r.age_days)} days</td></tr>`).join("")}
        </tbody></table></div>`;
    } catch {
      box.innerHTML = `<div class="empty-state">Couldn't load these cases.</div>`;
    }
  },

  pct(v) { return v === null || v === undefined ? "—" : `${this.esc(v)}%`; },

  // Budget against what requests already hold and what was spent, per cost centre and account.
  async loadBudgets() {
    const box = document.getElementById("ctBudgets");
    try {
      const b = await API.getJSON("/api/budgets/variance");
      if (!box || !document.body.contains(box)) return;
      if (!b.rows.length) { box.innerHTML = `<div class="empty-next"><strong>No budgets are set up.</strong><br>Budgets are loaded with the company's master data.</div>`; return; }
      box.innerHTML = `<div class="table-wrap"><table class="data-table"><thead><tr><th>COST CENTRE</th><th>ACCOUNT</th><th>BUDGET</th><th>HELD BY REQUESTS</th><th>SPENT</th><th>LEFT</th></tr></thead><tbody>
        ${b.rows.map((r) => `<tr><td>${this.esc(r.cost_centre_name || r.cost_centre)}<div class="cell-muted" style="font-size:11px;">${this.esc(r.cost_centre)}</div></td><td>${this.esc(r.gl_code)}</td>
          <td>${formatCurrency(r.budget)}</td><td>${formatCurrency(r.committed)}</td><td>${formatCurrency(r.spent)}</td>
          <td>${r.over ? `<span class="badge badge-rejected">${formatCurrency(r.left)}</span>` : formatCurrency(r.left)}</td></tr>`).join("")}
      </tbody></table></div>
      ${b.unassigned.length ? `<p class="cell-muted" style="font-size:12px;margin-top:8px;">Also held against the location, with no cost centre: ${b.unassigned.map((u) => `${this.esc(u.gl_code)} at ${this.esc(u.location)} ${formatCurrency(u.committed)}`).join("; ")}</p>` : ""}`;
    } catch {
      if (box) box.innerHTML = `<div class="empty-state">Couldn't load the budgets.</div>`;
    }
  },

  async loadKpis() {
    const box = document.getElementById("ctKpis");
    try {
      const k = await API.getJSON("/api/kpis");
      if (!box || !document.body.contains(box)) return;
      const cards = [
        ["spend_under_management", this.pct(k.spend_under_management.pct), `${this.esc(k.spend_under_management.contract_pct ?? "—")}% of it on contract`],
        ["touchless", this.pct(k.touchless.pct), `${this.esc(k.touchless.requests)} requests reached an order`],
        ["on_time_delivery", this.pct(k.on_time_delivery.pct), `${this.esc(k.on_time_delivery.lines)} order lines`],
        ["first_pass_match", this.pct(k.first_pass_match.pct), `${this.esc(k.first_pass_match.invoices)} invoices`],
        ["msme_on_time_payment", this.pct(k.msme_on_time_payment.pct), `${this.esc(k.msme_on_time_payment.invoices)} invoices`],
        ["duplicates_avoided", this.esc(k.duplicates_avoided.count), `${formatCurrency(k.duplicates_avoided.value)} stopped: ${this.esc(k.duplicates_avoided.requests.count)} requests, ${this.esc(k.duplicates_avoided.invoices.count)} invoices`],
      ];
      box.innerHTML = `
        <div class="stat-grid" style="grid-template-columns:repeat(3,1fr);margin-bottom:14px;">
          ${cards.map(([key, value, sub]) => `<div class="stat-card"><div class="stat-label">${this.esc(this.KPI_LABELS[key]).toUpperCase()}</div><div class="stat-value" style="font-size:22px;">${value}</div><div class="cell-muted" style="font-size:11.5px;">${sub}</div></div>`).join("")}
          <div class="stat-card"><div class="stat-label">SAVING PER AWARDED RFQ</div><div class="stat-value" style="font-size:22px;">${k.savings.per_rfq === null ? "—" : formatCurrency(k.savings.per_rfq)}</div><div class="cell-muted" style="font-size:11.5px;">first offer less awarded price · ${this.esc(k.savings.rfqs)} RFQs</div></div>
        </div>
        <div class="panel"><div class="panel-title">Cycle time, request to paid (working days, paid cases only)</div>
          <table class="data-table"><thead><tr><th>STAGE</th><th>CASES</th><th>MEDIAN</th><th>90TH PERCENTILE</th></tr></thead><tbody>
          ${Object.entries(k.cycle_time).map(([key, v]) => `<tr><td>${this.esc(v.label)}${key === "total" ? " <strong>(total)</strong>" : ""}</td><td>${this.esc(v.cases)}</td><td>${this.esc(v.median ?? "—")}</td><td>${this.esc(v.p90 ?? "—")}</td></tr>`).join("")}
          </tbody></table></div>`;
    } catch {
      if (box) box.innerHTML = `<div class="empty-state">Couldn't load the key figures.</div>`;
    }
  },
};
