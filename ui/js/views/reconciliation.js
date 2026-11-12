/**
 * Reconciliation (plan step 13): the order trace, received not invoiced, small suppliers' deadlines, supplier statements
 * and bank lines nobody matched. Every value shown is escaped; the numbers come from the server as exact text.
 */
const TracePanel = {
  STAGE_ROUTE: { request: "requisitions", rfq: "rfqs", po: "purchase-orders", invoice: "invoices", material_request: "material-requests" },

  /** Draw the chain of one document into an element (used on the order page and on the Reconciliation page). */
  async mount(el, no) {
    if (!el) return;
    el.innerHTML = `<div class="panel" style="margin-bottom:12px;"><div class="panel-title">Order trace</div><div class="cell-muted" style="font-size:12.5px;">Loading…</div></div>`;
    let res;
    try {
      res = await Auth.fetch(`/api/trace/${encodeURIComponent(no)}`);
    } catch { res = null; }
    if (!res || !res.ok) {
      const why = res && res.status === 404 ? `No document has the number ${escapeHtml(no)}.` : "The trace could not be loaded.";
      el.innerHTML = `<div class="panel" style="margin-bottom:12px;"><div class="panel-title">Order trace</div><div class="cell-muted" style="font-size:12.5px;">${why}</div></div>`;
      return;
    }
    const t = await res.json();
    const cell = (v) => (v == null ? "" : escapeHtml(v));
    const link = (stage, n) => (this.STAGE_ROUTE[stage] ? `<a href="#/${this.STAGE_ROUTE[stage]}/${encodeURIComponent(n)}" style="color:var(--accent-tint)">${escapeHtml(n)}</a>` : escapeHtml(n));
    el.innerHTML = `<div class="panel" style="margin-bottom:12px;"><div class="panel-title">Order trace · ${escapeHtml(t.document)}</div>
      <table class="data-table"><thead><tr><th>STEP</th><th>DOCUMENT</th><th>DATE</th><th>STATUS</th><th>QTY</th><th>AMOUNT</th><th>WHO</th><th>DETAIL</th></tr></thead><tbody>
      ${t.stages.map((s) => s.items.map((i, n) => `<tr><td class="cell-muted">${n === 0 ? escapeHtml(s.title) : ""}</td><td>${link(s.stage, i.no)}</td>
        <td>${i.date ? formatDate(i.date) : ""}</td><td>${cell(String(i.status || "").replace(/_/g, " "))}</td><td>${cell(i.qty)}</td>
        <td>${i.amount == null ? "" : formatCurrency(i.amount)}</td><td>${cell(i.who)}</td><td class="cell-muted" style="font-size:12px;">${cell(i.detail)}</td></tr>`).join("")).join("")}
      </tbody></table></div>`;
  },
};

const ReconciliationView = {
  esc(value) {
    return escapeHtml(value);
  },

  TABS: [
    ["trace", "Order trace"],
    ["grni", "Received not invoiced"],
    ["msme", "Small suppliers"],
    ["statements", "Supplier statements"],
    ["bank", "Bank lines"],
  ],

  async render(container) {
    container.innerHTML = `
      <div class="page-header page-header-row">
        <div><h1>Reconciliation</h1><p>The books and the flow agree: what was received and not invoiced, supplier statements, bank lines and small suppliers' deadlines</p></div>
      </div>
      <div class="tabs" id="recTabs">${this.TABS.map(([k, t], n) => `<button class="tab${n === 1 ? " active" : ""}" data-tab="${k}">${t}</button>`).join("")}</div>
      <div id="recBody"></div>`;
    const tabs = document.querySelectorAll("#recTabs .tab");
    tabs.forEach((b) => b.addEventListener("click", () => {
      tabs.forEach((x) => x.classList.toggle("active", x === b));
      this.show(b.dataset.tab);
    }));
    this.show("grni");
  },

  show(tab) {
    const body = document.getElementById("recBody");
    if (!body) return;
    const draw = { trace: () => this.trace(body), grni: () => this.grni(body), msme: () => this.msme(body), statements: () => this.statements(body), bank: () => this.bank(body) };
    draw[tab]();
  },

  money(v) { return v == null ? "" : formatCurrency(v); },

  denied(box, err) {
    box.innerHTML = `<div class="empty-state">${/\(403\)/.test(String(err)) ? "Your role cannot see this." : "Couldn't load this."}</div>`;
  },

  trace(body) {
    body.innerHTML = `<div class="panel" style="margin-bottom:12px;"><div class="panel-title">Trace one document</div>
      <p class="cell-muted" style="font-size:12px;margin:0 0 8px;">Any number: production order, material request, request, quotation request, order, receipt, service entry, invoice or payment.</p>
      <div style="display:flex;gap:8px;flex-wrap:wrap;"><input id="traceNo" placeholder="e.g. PO-CKN-2627-00012" style="padding:6px;min-width:240px;"><div class="btn btn-primary btn-sm" id="traceGo">Trace</div></div></div>
      <div id="traceBox"></div>`;
    const go = () => { const no = document.getElementById("traceNo").value.trim(); if (no) TracePanel.mount(document.getElementById("traceBox"), no); };
    document.getElementById("traceGo").addEventListener("click", go);
    document.getElementById("traceNo").addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
  },

  async grni(body) {
    body.innerHTML = `<div class="panel" style="margin-bottom:12px;"><div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
        <span class="cell-muted" style="font-size:12px;">As of</span><input id="grniDate" type="date" style="padding:6px;">
        <div class="btn btn-secondary btn-sm" id="grniGo">Show</div></div>
        <p class="cell-muted" style="font-size:12px;margin:8px 0 0;">Goods accepted (at the order price) and services confirmed, against invoices received by that date, before tax. Rejected goods never count.</p></div>
      <div id="grniBox"><div class="empty-state">Loading…</div></div>`;
    const date = document.getElementById("grniDate");
    date.value = new Date().toISOString().slice(0, 10);
    const load = async () => {
      const box = document.getElementById("grniBox");
      try {
        const d = await API.getJSON(`/api/reconciliation/received-not-invoiced?as_of=${encodeURIComponent(date.value)}`);
        box.innerHTML = `<div class="stat-grid" style="grid-template-columns:repeat(auto-fit,minmax(180px,1fr));margin-bottom:12px;">
            <div class="stat-card"><div class="stat-label">RECEIVED, NOT INVOICED</div><div class="stat-value" style="font-size:18px;">${formatCurrency(d.not_invoiced)}</div></div>
            <div class="stat-card"><div class="stat-label">INVOICED, NOT RECEIVED</div><div class="stat-value" style="font-size:18px;">${formatCurrency(d.not_received)}</div></div></div>
          ${d.suppliers.length ? `<div class="table-wrap" style="margin-bottom:12px;"><table class="data-table"><thead><tr><th>SUPPLIER</th><th>LINES</th><th>RECEIVED, NOT INVOICED</th><th>INVOICED, NOT RECEIVED</th></tr></thead><tbody>
            ${d.suppliers.map((s) => `<tr><td>${this.esc(s.supplier)} <span class="cell-muted">${this.esc(s.supplier_code)}</span></td><td>${this.esc(s.lines)}</td><td>${formatCurrency(s.not_invoiced)}</td><td>${formatCurrency(s.not_received)}</td></tr>`).join("")}</tbody></table></div>` : ""}
          ${d.lines.length ? `<div class="table-wrap"><table class="data-table"><thead><tr><th>ORDER LINE</th><th>SUPPLIER</th><th>ITEM</th><th>RECEIVED</th><th>INVOICED</th><th>NOT INVOICED</th><th>NOT RECEIVED</th><th>SINCE</th><th>AGE (DAYS)</th></tr></thead><tbody>
            ${d.lines.map((l) => `<tr data-po="${this.esc(l.po_no)}"><td>${this.esc(l.po_no)} / ${this.esc(l.line_no)}</td><td>${this.esc(l.supplier)}</td><td>${this.esc(l.item_code || l.description || "")}</td>
              <td>${this.esc(l.received_qty)}<div class="cell-muted" style="font-size:11px;">${formatCurrency(l.received_value)}</div></td><td>${this.esc(l.invoiced_qty)}<div class="cell-muted" style="font-size:11px;">${formatCurrency(l.invoiced_value)}</div></td>
              <td>${Number(l.not_invoiced) ? formatCurrency(l.not_invoiced) : ""}</td><td>${Number(l.not_received) ? formatCurrency(l.not_received) : ""}</td><td>${l.since ? formatDate(l.since) : ""}</td><td>${this.esc(l.age_days)}</td></tr>`).join("")}</tbody></table></div>`
            : `<div class="empty-state">Everything received by this date is invoiced, and nothing is invoiced beyond what was received.</div>`}`;
        box.querySelectorAll("tr[data-po]").forEach((tr) => tr.addEventListener("click", () => { window.location.hash = `#/purchase-orders/${tr.dataset.po}`; }));
      } catch (err) { this.denied(box, err); }
    };
    document.getElementById("grniGo").addEventListener("click", load);
    await load();
  },

  async msme(body) {
    body.innerHTML = `<div class="panel" style="margin-bottom:12px;"><div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
        <span class="cell-muted" style="font-size:12px;">Late, or due within</span><input id="msmeWithin" type="number" min="0" style="width:64px;padding:6px;"><span class="cell-muted" style="font-size:12px;">days</span>
        <div class="btn btn-secondary btn-sm" id="msmeGo">Show</div></div>
        <p class="cell-muted" style="font-size:12px;margin:8px 0 0;">Micro and small suppliers must be paid within the days agreed in writing, at most 45 days from acceptance, or within 15 days without an agreement (MSMED Act section 15). The limits are company settings.</p></div>
      <div id="msmeBox"><div class="empty-state">Loading…</div></div>`;
    const load = async () => {
      const box = document.getElementById("msmeBox");
      const within = document.getElementById("msmeWithin").value;
      try {
        const d = await API.getJSON(`/api/reconciliation/msme${within === "" ? "" : `?within=${encodeURIComponent(within)}`}`);
        document.getElementById("msmeWithin").value = d.within;
        box.innerHTML = d.invoices.length
          ? `<div class="table-wrap"><table class="data-table"><thead><tr><th>INVOICE</th><th>SUPPLIER</th><th>TOTAL</th><th>ACCEPTED</th><th>LIMIT</th><th>DEADLINE</th><th>STANDING</th></tr></thead><tbody>
            ${d.invoices.map((i) => `<tr data-id="${this.esc(i.invoice)}"><td>${this.esc(i.invoice)}<div class="cell-muted" style="font-size:11px;">${this.esc(i.number)} · ${this.esc(String(i.status).replace(/_/g, " "))}</div></td>
              <td>${this.esc(i.supplier)} <span class="badge badge-pending">${this.esc(i.msme_category)}</span></td><td>${formatCurrency(i.total)}</td><td>${formatDate(i.accepted_on)}</td>
              <td>${this.esc(i.limit_days)} days${i.agreement ? "" : " (no agreement)"}</td><td>${formatDate(i.deadline)}</td>
              <td>${i.days_late > 0 ? `<span class="badge badge-rejected">${this.esc(plural(i.days_late, "day"))} late</span>` : `<span class="badge badge-pending">${i.days_left ? `${this.esc(plural(i.days_left, "day"))} left` : "due today"}</span>`}</td></tr>`).join("")}</tbody></table></div>`
          : `<div class="empty-state">No invoice of a small supplier is late or due within ${this.esc(d.within)} days.</div>`;
        box.querySelectorAll("tr[data-id]").forEach((tr) => tr.addEventListener("click", () => { window.location.hash = `#/invoices/${tr.dataset.id}`; }));
      } catch (err) { this.denied(box, err); }
    };
    document.getElementById("msmeGo").addEventListener("click", load);
    await load();
  },

  OUTCOME: { matched: ["Matched", "badge-approved"], amount_differs: ["Amount differs", "badge-pending"], only_theirs: ["Only on theirs", "badge-rejected"], only_ours: ["Only on ours", "badge-rejected"] },

  async statements(body) {
    body.innerHTML = `<div class="panel" style="margin-bottom:12px;"><div class="panel-title">Import a supplier's statement of account</div>
        <p class="cell-muted" style="font-size:12px;margin:0 0 8px;">CSV or Excel with the columns date, type (invoice, credit note, debit note or payment), document_no, amount, reference. <a href="#" id="stmtTemplate" style="color:var(--accent-tint)">Download the template</a>. The period is the dates on the statement unless you give one.</p>
        <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;">
          <select id="stmtSupplier" style="padding:6px;"><option value="">Supplier…</option></select>
          <span class="cell-muted" style="font-size:12px;">from</span><input id="stmtFrom" type="date" style="padding:6px;"><span class="cell-muted" style="font-size:12px;">to</span><input id="stmtTo" type="date" style="padding:6px;">
          <input id="stmtFile" type="file" accept=".csv,.xlsx"><div class="btn btn-primary btn-sm" id="stmtGo">Import and match</div></div>
        <div id="stmtNote" style="font-size:12.5px;color:var(--warning-text);margin-top:6px;"></div></div>
      <div id="stmtResult"></div>
      <div class="panel"><div class="panel-title">Statements imported</div><div id="stmtList" class="cell-muted" style="font-size:12.5px;">Loading…</div></div>`;
    try {
      const vendors = await API.getJSON("/api/vendors");
      document.getElementById("stmtSupplier").innerHTML += vendors.map((v) => `<option value="${this.esc(v.id || v.code)}">${this.esc(v.name || v.legal_name || v.id)}</option>`).join("");
    } catch { /* the supplier list is only a convenience */ }
    document.getElementById("stmtTemplate").addEventListener("click", async (e) => {
      e.preventDefault();
      const res = await Auth.fetch("/api/reconciliation/vendor-statements/template");
      if (!res.ok) return;
      const a = document.createElement("a");
      a.href = URL.createObjectURL(await res.blob());
      a.download = "supplier-statement.csv";
      document.body.appendChild(a); a.click(); a.remove();
    });
    document.getElementById("stmtGo").addEventListener("click", async () => {
      const note = document.getElementById("stmtNote");
      const supplier = document.getElementById("stmtSupplier").value;
      const file = document.getElementById("stmtFile").files[0];
      if (!supplier || !file) { note.textContent = "Choose the supplier and the statement file first."; return; }
      const q = new URLSearchParams({ supplier });
      const from = document.getElementById("stmtFrom").value; const to = document.getElementById("stmtTo").value;
      if (from) q.set("period_from", from);
      if (to) q.set("period_to", to);
      const form = new FormData();
      form.append("file", file, file.name);
      const res = await Auth.fetch(`/api/reconciliation/vendor-statements?${q}`, { method: "POST", body: form });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) { note.textContent = data.detail || "The statement was not imported."; return; }
      note.textContent = data.already_imported ? "This file was imported before: here is that result." : "";
      this.showStatement(data);
      this.loadStatements();
    });
    await this.loadStatements();
  },

  async loadStatements() {
    const list = document.getElementById("stmtList");
    if (!list) return;
    try {
      const rows = await API.getJSON("/api/reconciliation/vendor-statements");
      list.innerHTML = rows.length
        ? `<table class="data-table"><thead><tr><th>SUPPLIER</th><th>PERIOD</th><th>LINES</th><th>MATCHED</th><th>AMOUNT DIFFERS</th><th>ONLY THEIRS</th><th>ONLY OURS</th><th>IMPORTED</th></tr></thead><tbody>
          ${rows.map((r) => `<tr data-id="${this.esc(r.id)}"><td>${this.esc(r.supplier)}</td><td>${formatDate(r.period_from)} to ${formatDate(r.period_to)}</td><td>${this.esc(r.lines)}</td><td>${this.esc(r.matched)}</td><td>${this.esc(r.amount_differs)}</td><td>${this.esc(r.only_theirs)}</td><td>${this.esc(r.only_ours)}</td><td>${formatDate(r.imported_at)}</td></tr>`).join("")}</tbody></table>`
        : "No statement imported yet.";
      list.querySelectorAll("tr[data-id]").forEach((tr) => tr.addEventListener("click", async () => {
        this.showStatement(await API.getJSON(`/api/reconciliation/vendor-statements/${encodeURIComponent(tr.dataset.id)}`));
      }));
    } catch (err) { this.denied(list, err); }
  },

  showStatement(s) {
    const box = document.getElementById("stmtResult");
    if (!box) return;
    box.innerHTML = `<div class="panel" style="margin-bottom:12px;"><div class="panel-title">${this.esc(s.supplier_code)} · ${formatDate(s.period_from)} to ${formatDate(s.period_to)}</div>
      ${(s.unreadable || []).length ? `<div style="font-size:12.5px;color:var(--warning-text);margin-bottom:6px;">Not read: ${s.unreadable.map((u) => `line ${this.esc(u.row)} (${this.esc(u.why)})`).join(", ")}</div>` : ""}
      <table class="data-table"><thead><tr><th>LINE</th><th>DATE</th><th>DOCUMENT</th><th>THEIRS</th><th>OURS</th><th>DIFFERENCE</th><th>OUTCOME</th><th>WHY</th></tr></thead><tbody>
      ${s.results.map((r) => { const [label, badge] = this.OUTCOME[r.outcome] || [r.outcome, ""]; return `<tr><td class="cell-muted">${this.esc(r.row_no ?? "")}</td><td>${r.doc_date ? formatDate(r.doc_date) : ""}</td>
        <td>${this.esc(String(r.doc_type).replace(/_/g, " "))} ${this.esc(r.doc_no)}${r.our_ref ? `<div class="cell-muted" style="font-size:11px;">ours ${this.esc(r.our_ref)}</div>` : ""}</td>
        <td>${this.money(r.their_amount)}</td><td>${this.money(r.our_amount)}</td><td>${r.difference != null && Number(r.difference) ? formatCurrency(r.difference) : ""}</td>
        <td><span class="badge ${badge}">${this.esc(label)}</span></td><td class="cell-muted" style="font-size:12px;">${this.esc(r.reason)}</td></tr>`; }).join("")}
      </tbody></table></div>`;
  },

  async bank(body) {
    body.innerHTML = `<div class="panel cell-muted" style="margin-bottom:12px;font-size:12.5px;">Bank statement lines no payment fitted (import the statement on a payment run). Choose the payment a line is and confirm: the amounts must agree, and the person who approved the run cannot confirm it.</div>
      <div id="bankBox"><div class="empty-state">Loading…</div></div>`;
    const box = document.getElementById("bankBox");
    try {
      const rows = await API.getJSON("/api/reconciliation/bank-lines");
      box.innerHTML = rows.length
        ? `<div class="table-wrap"><table class="data-table"><thead><tr><th>DATE</th><th>NARRATION</th><th>AMOUNT</th><th>WHY OPEN</th><th>IT IS PAYMENT</th></tr></thead><tbody>
          ${rows.map((r) => `<tr><td>${r.value_date ? formatDate(r.value_date) : ""}</td><td>${this.esc(r.narration || "")}<div class="cell-muted" style="font-size:11px;">${this.esc(r.utr || "")}</div></td><td>${formatCurrency(r.amount)}</td>
            <td class="cell-muted" style="font-size:12px;">${this.esc(r.why || "")}</td>
            <td>${r.suggestions.length ? `<select data-line="${this.esc(r.id)}" style="padding:4px;">${r.suggestions.map((p) => `<option value="${this.esc(p)}">${this.esc(p)}</option>`).join("")}</select> <div class="btn btn-primary btn-sm" data-confirm="${this.esc(r.id)}">Confirm</div>` : `<span class="cell-muted" style="font-size:12px;">no payment of this amount is waiting</span>`}
            <div class="cell-muted" data-note="${this.esc(r.id)}" style="font-size:11.5px;color:var(--warning-text);"></div></td></tr>`).join("")}</tbody></table></div>`
        : `<div class="empty-state">Every imported bank line is matched.</div>`;
      box.querySelectorAll("[data-confirm]").forEach((b) => b.addEventListener("click", async () => {
        const id = b.dataset.confirm;
        const payment = box.querySelector(`select[data-line="${CSS.escape(id)}"]`).value;
        const res = await API.postJSON(`/api/reconciliation/bank-lines/${encodeURIComponent(id)}/confirm`, { payment });
        if (res.ok) return this.bank(body);
        box.querySelector(`[data-note="${CSS.escape(id)}"]`).textContent = res.data.detail || "That did not work.";
      }));
    } catch (err) { this.denied(box, err); }
  },
};
