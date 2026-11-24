/**
 * Close (plan step 13): the GSTR-2B check, the month-end close with its frozen received-not-invoiced and accounting
 * export, and the price measures with the standard prices behind them. Every number is shown from the server as exact
 * text; nothing here computes money.
 */
const CloseView = {
  esc(v) { return escapeHtml(v); },
  TABS: [["month", "Month-end close"], ["gst", "GSTR-2B check"], ["prices", "Price variance and savings"]],

  async render(container) {
    container.innerHTML = `
      <div class="page-header page-header-row">
        <div><h1>Close</h1><p>The month's figures frozen, the 2B compared and the price measures kept apart</p></div>
      </div>
      <div class="tabs" id="closeTabs">${this.TABS.map(([k, t], n) => `<button class="tab${n === 0 ? " active" : ""}" data-tab="${k}">${t}</button>`).join("")}</div>
      <div id="closeBody"></div>`;
    const tabs = document.querySelectorAll("#closeTabs .tab");
    tabs.forEach((b) => b.addEventListener("click", () => {
      tabs.forEach((x) => x.classList.toggle("active", x === b));
      this.show(b.dataset.tab);
    }));
    this.show("month");
  },

  show(tab) {
    const body = document.getElementById("closeBody");
    if (!body) return;
    ({ month: () => this.month(body), gst: () => this.gst(body), prices: () => this.prices(body) }[tab])();
  },

  note(el, text) { if (el) el.textContent = text; },

  async month(body) {
    body.innerHTML = `<div class="panel" style="margin-bottom:12px;"><div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
        <span class="cell-muted" style="font-size:12px;">Financial year</span><input id="closeFy" placeholder="2026-27" style="padding:6px;width:110px;">
        <div class="btn btn-secondary btn-sm" id="closeLoad">Show the months</div></div></div>
      <div id="closeMonths"><div class="empty-state">Loading…</div></div>
      <div id="closePreview"></div>`;
    const fy = document.getElementById("closeFy");
    const now = new Date();
    const fyYear = now.getMonth() >= 3 ? now.getFullYear() : now.getFullYear() - 1;
    fy.value = `${fyYear}-${String((fyYear + 1) % 100).padStart(2, "0")}`;
    const load = async () => {
      const box = document.getElementById("closeMonths");
      try {
        const got = await API.getJSON(`/api/close/periods?fy=${encodeURIComponent(fy.value)}`);
        box.innerHTML = `<div class="table-wrap"><table class="data-table"><thead><tr><th>MONTH</th><th>STATE</th><th>RECEIVED NOT INVOICED</th><th></th></tr></thead><tbody>
          ${got.periods.map((p) => `<tr><td>${this.esc(p.label)}</td><td>${this.esc(p.status)}</td><td>${p.grni_total ? formatCurrency(p.grni_total) : ""}</td>
            <td>${p.status === "open" ? `<span class="btn btn-sm" data-preview="${this.esc(p.period)}">Preview</span>` : ""}</td></tr>`).join("")}
          </tbody></table></div>`;
        box.querySelectorAll("[data-preview]").forEach((b) => b.addEventListener("click", () => this.preview(b.dataset.preview)));
      } catch { box.innerHTML = `<div class="empty-state">Your role cannot see the months, or none is set up.</div>`; }
    };
    document.getElementById("closeLoad").addEventListener("click", load);
    await load();
  },

  async preview(period) {
    const box = document.getElementById("closePreview");
    box.innerHTML = `<div class="panel"><div class="empty-state">Loading…</div></div>`;
    try {
      const p = await API.getJSON(`/api/close/periods/${encodeURIComponent(period)}`);
      const warn = (p.warnings || []).map((w) => `<li>${this.esc(w.words)}${w.amount ? ` (${formatCurrency(w.amount)})` : ""}</li>`).join("");
      const block = (p.blocking || []).map((b) => `<li>${this.esc(b)}</li>`).join("");
      box.innerHTML = `<div class="panel"><div class="panel-title">${this.esc(p.label)}</div>
        ${block ? `<p style="color:var(--warning-text);">Blocked: <ul>${block}</ul></p>` : ""}
        <p class="cell-muted" style="font-size:12.5px;">This close records the price measures, freezes what was received and not invoiced, keeps the final export and locks the month.</p>
        ${warn ? `<div style="font-size:12.5px;">Warnings: <ul>${warn}</ul></div>` : ""}
        <p style="font-size:12.5px;"><a href="#" id="closeDraft" style="color:var(--accent-tint);">Download the accounting export as a draft</a></p>
        ${p.status === "closed" ? `<p>Closed by ${this.esc(p.closed_by)}; received not invoiced ${formatCurrency(p.grni_total)}. <a href="#" id="closeExport">Download the accounting export</a></p>
          <div id="closeGrni"></div>` : p.blocking && p.blocking.length ? "" : `
          <textarea id="closeReason" rows="2" placeholder="Reason (required)" style="width:100%;margin-top:8px;padding:8px;"></textarea>
          <div class="btn btn-primary btn-sm" id="closeGo" style="margin-top:8px;display:inline-block;">Close the month</div>`}
        <div id="closeNote" style="font-size:12.5px;color:var(--warning-text);margin-top:6px;"></div></div>`;
      const go = document.getElementById("closeGo");
      if (go) go.addEventListener("click", async () => {
        const r = await API.postJSON(`/api/close/periods/${encodeURIComponent(period)}/close`, { reason: document.getElementById("closeReason").value });
        if (!r.ok) { this.note(document.getElementById("closeNote"), r.data.detail || "The month was not closed."); return; }
        this.preview(period);
      });
      const dl = document.getElementById("closeExport");
      if (dl) dl.addEventListener("click", async (e) => {
        e.preventDefault();
        const res = await Auth.fetch(`${API.base}/api/close/exports/${encodeURIComponent(period)}?kind=final`);
        if (res.ok) window.open(URL.createObjectURL(await res.blob()), "_blank");
      });
      document.getElementById("closeDraft").addEventListener("click", async (e) => {
        e.preventDefault();
        const res = await Auth.fetch(`${API.base}/api/close/exports/${encodeURIComponent(period)}?kind=draft`);
        if (res.ok) window.open(URL.createObjectURL(await res.blob()), "_blank");
      });
      if (p.status === "closed") {
        const g = await API.getJSON(`/api/close/periods/${encodeURIComponent(period)}/grni`);
        document.getElementById("closeGrni").innerHTML = `<div class="table-wrap" style="margin-top:8px;"><table class="data-table"><thead><tr><th>ORDER</th><th>ITEM</th><th>NOT INVOICED</th><th>GL</th></tr></thead><tbody>
          ${g.lines.map((l) => `<tr><td>${this.esc(l.po_no)} / ${this.esc(l.line_no)}</td><td>${this.esc(l.item_code)}</td><td>${formatCurrency(l.value)}</td><td>${this.esc(l.gl_code)}</td></tr>`).join("")}
          </tbody></table></div>`;
      }
    } catch (err) { box.innerHTML = `<div class="panel">${/\(403\)/.test(String(err)) ? "Your role cannot close a month." : "The month could not be read."}</div>`; }
  },

  async gst(body) {
    body.innerHTML = `<div class="panel" style="margin-bottom:12px;"><div class="panel-title">GSTR-2B check</div>
        <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;"><input id="gstFile" type="file" accept=".json,.csv"><div class="btn btn-primary btn-sm" id="gstGo">Upload</div>
        <a href="#" id="gstTemplate" class="cell-muted" style="font-size:12px;">CSV template</a></div>
        <div id="gstNote" style="font-size:12.5px;color:var(--warning-text);margin-top:6px;"></div></div>
      <div id="gstList"></div><div id="gstResult"></div>`;
    document.getElementById("gstTemplate").addEventListener("click", async (e) => {
      e.preventDefault();
      const res = await Auth.fetch(`${API.base}/api/gst/gstr2b/template`);
      if (res.ok) window.open(URL.createObjectURL(await res.blob()), "_blank");
    });
    document.getElementById("gstGo").addEventListener("click", async () => {
      const file = document.getElementById("gstFile").files[0];
      if (!file) return;
      const form = new FormData(); form.append("file", file, file.name);
      const res = await Auth.fetch(`${API.base}/api/gst/gstr2b`, { method: "POST", body: form });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) { this.note(document.getElementById("gstNote"), data.detail || "The file was not read."); return; }
      this.note(document.getElementById("gstNote"), data.already_imported ? "This file was imported before: here is that result." : "");
      this.showGst(data);
      this.loadGst();
    });
    await this.loadGst();
  },

  async loadGst() {
    const box = document.getElementById("gstList");
    try {
      const rows = await API.getJSON("/api/gst/gstr2b");
      box.innerHTML = rows.length ? `<div class="table-wrap" style="margin-bottom:12px;"><table class="data-table"><thead><tr><th>PERIOD</th><th>GSTIN</th><th>DOCUMENTS</th><th>OUTCOMES</th><th></th></tr></thead><tbody>
        ${rows.map((r) => `<tr><td>${this.esc(r.period)}</td><td>${this.esc(r.gstin)}</td><td>${this.esc(r.documents)}</td><td>${Object.entries(r.outcomes || {}).map(([k, n]) => `${this.esc(k)} ${this.esc(n)}`).join(", ")}</td><td><span class="btn btn-sm" data-gst="${this.esc(r.id)}">Open</span></td></tr>`).join("")}
        </tbody></table></div>` : `<div class="empty-state">No GSTR-2B has been compared yet.</div>`;
      box.querySelectorAll("[data-gst]").forEach((b) => b.addEventListener("click", async () => this.showGst(await API.getJSON(`/api/gst/gstr2b/${b.dataset.gst}`))));
    } catch { box.innerHTML = ""; }
  },

  showGst(data) {
    const box = document.getElementById("gstResult");
    if (!box || !data.results) return;
    box.innerHTML = `<div class="panel"><div class="panel-title">${this.esc(data.period)} · ${this.esc(data.gstin)}</div>
      <div class="table-wrap"><table class="data-table"><thead><tr><th>OUTCOME</th><th>DOCUMENT</th><th>SUPPLIER GSTIN</th><th>OURS</th><th>2B</th><th>DIFFERENCES</th></tr></thead><tbody>
      ${data.results.map((r) => `<tr><td>${this.esc(r.outcome)}</td><td>${this.esc(r.doc_no)}</td><td>${this.esc(r.supplier_gstin)}</td><td>${this.esc(r.our_invoice_no || "")}</td><td>${formatCurrency(r.taxable)}</td><td class="cell-muted" style="font-size:12px;">${(r.differences || []).map((d) => this.esc(d)).join("; ")}${r.reason ? this.esc(r.reason) : ""}</td></tr>`).join("")}
      </tbody></table></div></div>`;
  },

  async prices(body) {
    body.innerHTML = `<div class="panel" style="margin-bottom:12px;"><div class="panel-title">Price variance and savings</div>
        <p class="cell-muted" style="font-size:12px;margin:0;">Each measure is its own figure: never added to another.</p></div>
      <div class="tabs" id="measureTabs"></div><div id="measureBody"></div>
      <div class="panel" style="margin-top:12px;"><div class="panel-title">Standard prices</div>
        <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;"><input id="stdItem" placeholder="Item code" style="padding:6px;width:120px;"><input id="stdPrice" placeholder="Price" style="padding:6px;width:100px;">
          <input id="stdFrom" type="date" style="padding:6px;"><input id="stdReason" placeholder="Reason" style="padding:6px;min-width:220px;"><div class="btn btn-primary btn-sm" id="stdGo">Propose a new price</div></div>
        <div id="stdNote" style="font-size:12.5px;color:var(--warning-text);margin-top:6px;"></div><div id="stdVersions"></div></div>`;
    const load = async (measure) => {
      const box = document.getElementById("measureBody");
      try {
        const got = await API.getJSON(`/api/prices/measures${measure ? `?measure=${measure}` : ""}`);
        const tabs = document.getElementById("measureTabs");
        tabs.innerHTML = Object.entries(got).map(([k, v], n) => `<button class="tab${n === 0 ? " active" : ""}" data-measure="${this.esc(k)}">${this.esc(v.label)} · ${formatCurrency(v.total)}</button>`).join("");
        tabs.querySelectorAll("[data-measure]").forEach((b) => b.addEventListener("click", () => load(b.dataset.measure)));
        const one = got[measure] || Object.values(got)[0];
        box.innerHTML = `<div class="panel"><div class="panel-title">${this.esc(one.label)}</div>
          <div class="table-wrap"><table class="data-table"><thead><tr><th>DATE</th><th>ITEM</th><th>ORDER</th><th>QTY</th><th>PRICE</th><th>REFERENCE</th><th>AMOUNT</th></tr></thead><tbody>
          ${one.rows.map((r) => `<tr><td>${formatDate(r.on_date)}</td><td>${this.esc(r.item_code || "")}</td><td>${this.esc(r.po_no || "")}</td><td>${this.esc(r.qty)}</td><td>${this.esc(r.unit_price || "")}</td><td class="cell-muted" style="font-size:12px;">${this.esc(r.reference)}</td><td>${formatCurrency(r.amount)}</td></tr>`).join("")}
          </tbody></table></div><p style="margin:8px 0 0;"><b>${formatCurrency(one.total)}</b></p></div>`;
      } catch (err) { box.innerHTML = `<div class="panel">${/\(403\)/.test(String(err)) ? "Your role cannot see prices." : "The measures could not be read."}</div>`; }
    };
    document.getElementById("stdGo").addEventListener("click", async () => {
      const item = document.getElementById("stdItem").value.trim();
      const r = await API.postJSON("/api/prices/standard", {
        item, price: document.getElementById("stdPrice").value.trim(), effective_from: document.getElementById("stdFrom").value,
        reason: document.getElementById("stdReason").value.trim(),
      });
      if (!r.ok) { this.note(document.getElementById("stdNote"), r.data.detail || "The price was not proposed."); return; }
      this.note(document.getElementById("stdNote"), "Proposed; another holder of price.standard decides it.");
      this.loadVersions(item);
    });
    document.getElementById("stdItem").addEventListener("change", () => this.loadVersions(document.getElementById("stdItem").value.trim()));
    await load();
  },

  async loadVersions(item) {
    if (!item) return;
    const box = document.getElementById("stdVersions");
    try {
      const rows = await API.getJSON(`/api/prices/standard?item=${encodeURIComponent(item)}`);
      box.innerHTML = `<div class="table-wrap" style="margin-top:8px;"><table class="data-table"><thead><tr><th>VERSION</th><th>PRICE</th><th>FROM</th><th>TO</th><th>STATUS</th></tr></thead><tbody>
        ${rows.map((r) => `<tr><td>${this.esc(r.version)}</td><td>${formatCurrency(r.price)}</td><td>${formatDate(r.effective_from)}</td><td>${r.effective_to ? formatDate(r.effective_to) : ""}</td><td>${this.esc(r.status)}</td></tr>`).join("")}
        </tbody></table></div>`;
    } catch { box.innerHTML = ""; }
  },
};
