/**
 * Material requests: what each production order needs from the store. The store check sets free stock aside (earliest
 * need-by first) and sends what is short for approval; at production start the store issues what is set aside.
 * Every number comes from the server.
 */
const MaterialRequestsView = {
  BADGE: { open: "badge-waiting", issued: "badge-approved", cancelled: "badge-draft" },
  STATUS: { open: "Open", issued: "Issued", cancelled: "Cancelled" },

  esc(value) {
    return String(value ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  },

  async render(container) {
    container.innerHTML = `
      <div class="page-header">
        <h1>Material requests</h1>
        <p>What each production order needs from the store: set aside, issued, and still short</p>
      </div>
      <div id="mrAction"></div>
      <div id="mrNote"></div>
      <div id="mrList"><div class="empty-state">Loading…</div></div>
    `;
    await this.load();
  },

  async load() {
    let data;
    try {
      data = await API.getJSON("/api/material-requests");
    } catch {
      const gone = document.getElementById("mrList");
      if (gone) gone.innerHTML = `<div class="empty-state">Couldn't load material requests.</div>`;
      return;
    }
    const list = document.getElementById("mrList");
    if (!list) return; // the person has already gone to another page
    const action = document.getElementById("mrAction");
    const open = data.requests.filter((r) => r.status === "open");
    action.innerHTML = (data.can_manage && open.length) || data.can_import
      ? `<div style="display:flex;gap:8px;margin-bottom:12px;align-items:center;flex-wrap:wrap;">
           ${data.can_manage && open.length ? `<span class="btn btn-primary btn-sm" id="mrCheck">Run store check</span>
           <span class="cell-muted" style="font-size:12.5px;">Sets free stock aside, earliest need-by first, and sends what is short for approval.</span>` : ""}
           ${data.can_import ? `<span class="btn btn-sm" id="mrLoad">Load from the ERP</span>` : ""}
         </div>`
      : "";
    const check = document.getElementById("mrCheck");
    if (check) check.addEventListener("click", () => this.runCheck());
    const load = document.getElementById("mrLoad");
    if (load) load.addEventListener("click", () => this.loadForm());
    if (!data.requests.length) {
      list.innerHTML = `<div class="empty-state">No material requests yet. They are made when production orders are loaded from the ERP${data.can_import ? ": click Load from the ERP" : ""}.</div>`;
      return;
    }
    this.requests = data.requests;
    list.innerHTML = data.requests.map((r, i) => this.card(r, i, data.can_manage)).join("");
    list.querySelectorAll("[data-mr]").forEach((b) => {
      b.addEventListener("click", () => this.act(b.dataset.mr, this.requests[Number(b.dataset.i)]));
    });
  },

  card(r, i, canManage) {
    const anySetAside = r.lines.some((l) => l.reserved_qty !== "0");
    const buttons = canManage && r.status === "open"
      ? `${anySetAside ? `<span class="btn btn-sm" data-mr="issue" data-i="${i}">Issue for production</span>` : ""}
         <span class="btn btn-sm" data-mr="cancel" data-i="${i}">Cancel</span>`
      : "";
    return `
      <div class="panel" style="margin-bottom:12px;">
        <div style="display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap;align-items:center;">
          <div class="panel-title" style="margin:0;">${this.esc(r.no)} · ${this.esc(r.order_no)} (${this.esc(r.product_code)}) · need by ${formatDate(r.need_by)}
            <span class="badge ${this.BADGE[r.status] || ""}">${this.esc(this.STATUS[r.status] || r.status)}</span></div>
          <div style="white-space:nowrap;">${buttons}</div>
        </div>
        ${r.cancel_reason ? `<p class="cell-muted" style="font-size:12.5px;">Cancelled: ${this.esc(r.cancel_reason)}</p>` : ""}
        <div class="table-wrap" style="margin-top:8px;">
          <table class="data-table">
            <thead><tr><th>ITEM</th><th>NEEDED</th><th>SET ASIDE</th><th>ISSUED</th><th>SHORT</th><th>ASKED FOR</th></tr></thead>
            <tbody>
              ${r.lines.map((l) => `
                <tr>
                  <td>${this.esc(l.item_name)}<div class="cell-muted" style="font-size:11px;">${this.esc(l.item_code)}</div></td>
                  <td>${this.esc(l.need_qty)} <span class="cell-muted">${this.esc(l.uom)}</span></td>
                  <td>${this.esc(l.reserved_qty)}</td>
                  <td>${this.esc(l.issued_qty)}</td>
                  <td>${l.short === "0" ? `<span class="cell-muted">0</span>` : `<strong>${this.esc(l.short)}</strong>`}</td>
                  <td class="cell-muted">${l.requested_qty === "0" ? "—" : `${this.esc(l.requested_qty)} on ${l.requests.map((n) => `<a href="#/requisitions/${encodeURIComponent(n)}">${this.esc(n)}</a>`).join(", ")}`}</td>
                </tr>`).join("")}
            </tbody>
          </table>
        </div>
      </div>`;
  },

  note(html) {
    const box = document.getElementById("mrNote");
    if (box) box.innerHTML = html ? `<div class="panel" style="margin-bottom:12px;font-size:12.5px;">${html}</div>` : "";
  },

  async runCheck() {
    const res = await API.postJSON("/api/material-requests/check", {});
    if (!res.ok) {
      this.note(this.esc(res.data.detail || "The store check did not work."));
      return;
    }
    const made = res.data.checked.filter((c) => c.purchase_request);
    const lines = made.map((c) => {
      const p = c.purchase_request;
      const fate = p.outcome === "submitted" ? `sent for approval (${this.esc(p.route)})`
        : p.outcome === "approved" ? "approved at once"
        : `left as a draft: ${this.esc(p.submit_error || "")}`;
      return `${this.esc(c.order_no)}: <a href="#/requisitions/${encodeURIComponent(p.no)}">${this.esc(p.no)}</a> for ${plural(p.lines, "item")}, ${this.esc(p.total_shown)}, ${fate}`;
    });
    const unpriced = res.data.checked.flatMap((c) => c.unpriced);
    this.note(
      (lines.length ? `Store check done. Purchase requests for what is short:<br>${lines.join("<br>")}` : "Store check done. Nothing new to buy.")
      + (unpriced.length ? `<br>No price to estimate ${this.esc(unpriced.join(", "))}: ask the buyer.` : ""),
    );
    await this.load();
  },

  // Step 1: the ERP's production orders (and their component lines, sales orders, demand plan) as a CSV or Excel file.
  async loadForm() {
    const box = document.getElementById("mrAction");
    if (!box) return;
    let kinds = [];
    try {
      kinds = await API.getJSON("/api/demand/kinds");
    } catch {
      this.note("Couldn't load the kinds of ERP file.");
      return;
    }
    box.innerHTML = `
      <div class="panel" style="margin-bottom:12px;">
        <div class="panel-title">Load from the ERP</div>
        <p class="cell-muted" style="margin:0 0 10px;font-size:12.5px;">A CSV or Excel file in our template or with your ERP's column names. Rows loaded before are skipped; every row left out is listed with its reason. A production order gets its material request.</p>
        <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;">
          <select id="mrKind" aria-label="Kind of file" style="padding:6px;">${kinds.map((k) => `<option value="${this.esc(k.kind)}">${this.esc(k.title)}</option>`).join("")}</select>
          <input type="file" id="mrFile" accept=".csv,.xlsx" aria-label="The ERP's file">
          <span class="btn btn-primary btn-sm" id="mrLoadGo">Load the file</span>
          <span class="btn btn-sm" id="mrBack">Back</span>
        </div>
        <div id="mrErr" style="font-size:12.5px;color:var(--warning-text);margin-top:8px;"></div>
      </div>`;
    document.getElementById("mrBack").addEventListener("click", () => this.load());
    document.getElementById("mrLoadGo").addEventListener("click", async () => {
      const file = document.getElementById("mrFile").files[0];
      if (!file) { document.getElementById("mrErr").textContent = "Choose the file first."; return; }
      const form = new FormData();
      form.append("file", file);
      const res = await Auth.fetch(`${API.base}/api/demand/import/${encodeURIComponent(document.getElementById("mrKind").value)}`, { method: "POST", body: form });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) { document.getElementById("mrErr").textContent = body.detail || "The file was not loaded."; return; }
      const left = (body.rejected || []).map((p) => `line ${this.esc(p.line)}: ${this.esc(p.reason)}`);
      this.note(`${this.esc(body.summary.charAt(0).toUpperCase() + body.summary.slice(1))}.${body.material_requests.length ? `<br>New material requests: ${body.material_requests.map((n) => this.esc(n)).join(", ")}.` : ""}${left.length ? `<br>Left out: ${left.join("; ")}` : ""}`);
      await this.load();
    });
  },

  act(kind, r) {
    const box = document.getElementById("mrAction");
    if (!box) return;
    const field = 'style="background:var(--surface-sunken);border:1px solid var(--border);color:var(--text-primary);border-radius:8px;padding:7px 10px;font-size:12.5px;"';
    const issue = kind === "issue";
    box.innerHTML = `
      <div class="panel" style="margin-bottom:12px;">
        <div class="panel-title">${issue ? "Issue for production" : "Cancel"}: ${this.esc(r.no)} (${this.esc(r.order_no)})</div>
        <p class="cell-muted" style="margin:0 0 10px;font-size:12.5px;">${issue
          ? "Everything set aside for this order is issued. What is still short stays on the request until it arrives."
          : "The stock set aside goes back to the shelf. Purchase requests already made stay for the buyer."}</p>
        <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;">
          ${issue ? "" : `<input id="mrWhy" placeholder="Why it is cancelled (required)" style="min-width:260px;" ${field}>`}
          <span class="btn btn-primary btn-sm" id="mrSave">${issue ? "Issue what is set aside" : "Cancel the request"}</span>
          <span class="btn btn-sm" id="mrBack">Back</span>
        </div>
        <div id="mrErr" style="font-size:12.5px;color:var(--warning-text);margin-top:8px;"></div>
      </div>`;
    document.getElementById("mrBack").addEventListener("click", () => this.load());
    document.getElementById("mrSave").addEventListener("click", async () => {
      const res = issue
        ? await API.postJSON(`/api/material-requests/${encodeURIComponent(r.no)}/issue`, {})
        : await API.postJSON(`/api/material-requests/${encodeURIComponent(r.no)}/cancel`, { reason: document.getElementById("mrWhy").value });
      if (!res.ok) {
        document.getElementById("mrErr").textContent = res.data.detail || "That did not work.";
        return;
      }
      this.note(issue
        ? `Issued: ${Object.entries(res.data.issued).map(([k, v]) => `${this.esc(k)} ${this.esc(v)}`).join(", ")}.`
        : `${this.esc(r.no)} cancelled; its stock is free again.${res.data.purchase_requests_left.length ? ` Tell the buyer about ${this.esc(res.data.purchase_requests_left.join(", "))}.` : ""}`);
      await this.load();
    });
  },
};
