/**
 * Quality hold and returns: the open blocked and excess holds, the return to a supplier (with the receipt line a
 * manual hold came from), the drafts and their dispatch, and scrap of blocked stock.
 */
const ReturnsView = {
  esc(value) {
    return String(value ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  },

  STATUS: { draft: "draft", dispatched: "dispatched", cancelled: "cancelled" },
  QUALITY: { blocked: "blocked", quarantine: "quarantine (above the order)" },

  async render(container) {
    container.innerHTML = `
      <div class="page-header">
        <h1>Quality hold and returns</h1>
        <p>Blocked and excess stock, the return to the supplier, and its dispatch</p>
      </div>
      <h2 class="section-title">Stock held</h2>
      <div class="table-wrap" id="holdWrap"><div class="empty-state">Loading…</div></div>
      <div class="panel" style="margin-top:12px;" id="returnForm">
        <h3 style="margin-top:0;">Return to supplier</h3>
        <div class="cell-muted" style="font-size:12px;margin-bottom:8px;">Tick the holds that go back and say how much of each. A manual hold also says which receipt line the goods came from.</div>
        <label>Why do the goods go back?<input class="ret-reason" style="width:100%" placeholder="For example: cracked on arrival"></label>
        <div style="margin-top:8px;"><div class="btn btn-primary btn-sm" id="retCreate">Make the draft return</div></div>
        <div id="retNote" style="font-size:12px;color:var(--warning-text);margin-top:6px;"></div>
      </div>
      <div id="scrapPanel"></div>
      <div id="excessWrap"></div>
      <div id="outcomeWrap"></div>
      <h2 class="section-title" style="margin-top:22px;">Returns</h2>
      <div class="table-wrap" id="returnWrap"><div class="empty-state">Loading…</div></div>
    `;
    document.getElementById("retCreate").addEventListener("click", () => this.create());
    await Promise.all([this.loadHolds(), this.loadReturns(), this.loadExcess(), this.loadOutcomes()]);
  },

  async loadExcess() {
    let rows;
    try { rows = await API.getJSON("/api/receipt-excess?open=true"); } catch { return; }
    const wrap = document.getElementById("excessWrap");
    if (!wrap) return;
    const mine = rows.filter((r) => r.status === "waiting");
    if (!mine.length) { wrap.innerHTML = ""; return; }
    wrap.innerHTML = `<div class="panel" style="margin-top:12px;"><div class="panel-title">Above the order, waiting for the buyer (${mine.length})</div>
      ${mine.map((r) => `<div class="excess-row" data-id="${this.esc(r.id)}" data-version="${this.esc(r.version)}" style="border-top:1px solid var(--border);padding:8px 0;font-size:12.5px;">
        <strong>${this.esc(r.qty)} ${this.esc(r.uom || "")} of ${this.esc(r.item)}</strong> above <a href="#/purchase-orders/${this.esc(r.po_no)}" style="color:var(--accent-tint);">${this.esc(r.po_no)}</a> on ${this.esc(r.gr_no)} (${this.esc(r.supplier)})
        <div style="margin-top:4px;display:flex;gap:6px;flex-wrap:wrap;align-items:center;">
          <input class="exc-why" placeholder="Why?" style="min-width:180px;padding:5px;">
          <span class="btn btn-primary btn-sm" data-act="accept">Accept by revision</span>
          <span class="btn btn-secondary btn-sm" data-act="return">Return</span>
        </div><div class="exc-note" style="color:var(--warning-text);"></div></div>`).join("")}</div>`;
    wrap.querySelectorAll(".excess-row").forEach((row) => {
      row.querySelectorAll("[data-act]").forEach((button) => button.addEventListener("click", async () => {
        const note = row.querySelector(".exc-note");
        const body = { reason: row.querySelector(".exc-why").value.trim() };
        if (button.dataset.act === "accept") body.version = Number(row.dataset.version);
        const res = await API.postJSON(`/api/receipt-excess/${encodeURIComponent(row.dataset.id)}/${button.dataset.act}`, body);
        if (!res.ok) { note.textContent = res.data.detail || "That did not work."; return; }
        note.textContent = button.dataset.act === "accept"
          ? `Asked ${res.data.approver} to approve the revision to ${res.data.new_qty} (proposal ${res.data.proposal_id}).`
          : `Draft ${res.data.return_no} made: the store dispatches it.`;
        await Promise.all([this.loadExcess(), this.loadReturns()]);
      }));
    });
  },

  async loadOutcomes() {
    let rows;
    try { rows = await API.getJSON("/api/returns?status=dispatched"); } catch { return; }
    const wrap = document.getElementById("outcomeWrap");
    if (!wrap) return;
    const open = [];
    for (const r of rows) {
      const detail = await API.getJSON(`/api/returns/${encodeURIComponent(r.no)}`);
      const lines = detail.lines.filter((l) => l.po_no && l.outcome === null);
      if (lines.length) open.push({ return: detail, lines });
    }
    if (!open.length) { wrap.innerHTML = ""; return; }
    wrap.innerHTML = `<div class="panel" style="margin-top:12px;"><div class="panel-title">Returns to decide (${open.length})</div>
      ${open.map((o) => `<div class="outcome-row" data-no="${this.esc(o.return.no)}" style="border-top:1px solid var(--border);padding:8px 0;font-size:12.5px;">
        <strong>${this.esc(o.return.no)}</strong> to ${this.esc(o.return.supplier)} (${this.esc(o.return.reason)})
        ${o.lines.map((l) => `<div style="margin-top:4px;">${this.esc(l.qty)} ${this.esc(l.uom || "")} ${this.esc(l.name)}: <select class="out-c" data-line="${this.esc(l.line_no)}"><option value="">Choose</option><option value="replace">Replace</option><option value="no_replacement">No replacement</option></select></div>`).join("")}
        <div style="margin-top:4px;"><span class="btn btn-primary btn-sm outcome-save">Save</span></div>
        <div class="out-note" style="color:var(--warning-text);"></div></div>`).join("")}</div>`;
    wrap.querySelectorAll(".outcome-row").forEach((row) => {
      row.querySelector(".outcome-save").addEventListener("click", async () => {
        const lines = {};
        row.querySelectorAll(".out-c").forEach((s) => { if (s.value) lines[s.dataset.line] = s.value; });
        if (!Object.keys(lines).length) { row.querySelector(".out-note").textContent = "Choose replace or no replacement."; return; }
        const res = await API.postJSON(`/api/returns/${encodeURIComponent(row.dataset.no)}/outcome`, { lines });
        if (!res.ok) { row.querySelector(".out-note").textContent = res.data.detail || "That did not work."; return; }
        row.querySelector(".out-note").textContent = res.data.claims.length ? `Claims raised: ${res.data.claims.join(", ")}` : "Saved.";
        await this.loadOutcomes();
      });
    });
  },

  async loadHolds() {
    let holds;
    try {
      holds = (await API.getJSON("/api/inventory/holds")).holds;
    } catch {
      document.getElementById("holdWrap").innerHTML = `<div class="empty-state">Couldn't load the holds.</div>`;
      return;
    }
    const usable = holds.filter((h) => h.quality === "blocked" || h.source === "excess");
    const w = document.getElementById("holdWrap");
    if (!w) return;
    w.innerHTML = usable.length
      ? `<table class="data-table"><thead><tr><th></th><th>ITEM</th><th>PLACE</th><th>HELD</th><th>LEFT</th><th>WHY</th><th>FROM</th><th>QTY BACK</th><th></th></tr></thead><tbody>
          ${usable.map((h) => `<tr data-hold="${this.esc(h.id)}" data-source="${this.esc(h.source)}" data-gr="${this.esc(h.gr_no || "")}" data-line="${this.esc(h.gr_line_no || "")}">
            <td><input type="checkbox" class="hold-tick" aria-label="Return hold ${this.esc(h.id)}"></td>
            <td>${this.esc(h.item_code)}<div class="cell-muted" style="font-size:11px;">${this.esc(h.item_name || "")}</div></td>
            <td>${this.esc(h.location_code)}</td><td>${this.esc(h.quality)}</td><td>${this.esc(h.left_qty)}</td>
            <td>${this.esc(h.reason)}</td>
            <td class="cell-muted" style="font-size:11px;">${h.gr_no ? `${this.esc(h.po_no || "")} · ${this.esc(h.gr_no)} line ${this.esc(h.gr_line_no)}` : this.esc(h.source)}${h.source === "manual" ? `<div style="margin-top:4px;"><input class="hold-gr" placeholder="Receipt" style="width:100px;"><input class="hold-line" placeholder="Line" style="width:50px;margin-left:4px;"></div>` : ""}</td>
            <td><input class="hold-qty" inputmode="decimal" value="${this.esc(h.left_qty)}" style="width:80px;" aria-label="Quantity back"></td>
            <td>${h.quality === "blocked" ? `<div class="btn btn-secondary btn-sm scrap-open" data-id="${this.esc(h.id)}" data-left="${this.esc(h.left_qty)}">Scrap</div>` : ""}</td>
          </tr>`).join("")}
        </tbody></table>`
      : `<div class="empty-state">Nothing is held blocked or above an order.</div>`;
    w.querySelectorAll(".scrap-open").forEach((btn) => btn.addEventListener("click", () => this.scrapForm(btn.dataset.id, btn.dataset.left)));
  },

  async create() {
    const note = document.getElementById("retNote");
    const reason = document.querySelector(".ret-reason").value.trim();
    if (!reason) { note.textContent = "Say why the goods go back."; return; }
    const lines = [...document.querySelectorAll("#holdWrap tr[data-hold]")]
      .filter((tr) => tr.querySelector(".hold-tick").checked)
      .map((tr) => ({
        hold_id: Number(tr.dataset.hold),
        qty: tr.querySelector(".hold-qty").value,
        gr_no: tr.dataset.source === "manual" ? (tr.querySelector(".hold-gr")?.value || "") : undefined,
        po_line_no: tr.dataset.source === "manual" ? (tr.querySelector(".hold-line")?.value || "") : undefined,
      }));
    if (!lines.length) { note.textContent = "Tick at least one hold."; return; }
    const res = await API.postJSON("/api/returns", { reason, lines });
    if (!res.ok) { note.textContent = res.data.detail || "The return was not made."; return; }
    note.textContent = `Draft ${res.data.return_no} made: give the challan and dispatch it below.`;
    await this.loadHolds();
    await this.loadReturns();
  },

  scrapForm(id, left) {
    const panel = document.getElementById("scrapPanel");
    panel.innerHTML = `<div class="panel" style="margin-bottom:12px;">
      <label>Quantity scrapped<input class="scr-qty" inputmode="decimal" value="${this.esc(left)}" style="width:100px;"></label>
      <label>Why is it scrapped?<input class="scr-why" style="width:100%"></label>
      <div class="form-actions"><div class="btn btn-primary btn-sm" id="scrGo">Scrap</div><div class="btn btn-secondary btn-sm" id="scrCancel">Cancel</div></div>
      <div id="scrNote" style="font-size:12px;color:var(--warning-text);"></div></div>`;
    panel.querySelector("#scrCancel").addEventListener("click", () => { panel.innerHTML = ""; });
    panel.querySelector("#scrGo").addEventListener("click", async () => {
      const res = await API.postJSON(`/api/inventory/holds/${encodeURIComponent(id)}/scrap`, {
        qty: panel.querySelector(".scr-qty").value, reason: panel.querySelector(".scr-why").value.trim(),
      });
      if (!res.ok) { document.getElementById("scrNote").textContent = res.data.detail || "The scrap was not recorded."; return; }
      panel.innerHTML = "";
      await this.loadHolds();
    });
  },

  async loadReturns() {
    let rows;
    try {
      rows = await API.getJSON("/api/returns");
    } catch {
      document.getElementById("returnWrap").innerHTML = `<div class="empty-state">Couldn't load the returns.</div>`;
      return;
    }
    const w = document.getElementById("returnWrap");
    if (!w) return;
    w.innerHTML = rows.length
      ? `<table class="data-table"><thead><tr><th>RETURN</th><th>SUPPLIER</th><th>PLACE</th><th>QTY</th><th>WHY</th><th>STATUS</th><th></th></tr></thead><tbody>
          ${rows.map((r) => `<tr data-no="${this.esc(r.no)}"><td><a href="#/returns/${this.esc(r.no)}" style="color:var(--accent-tint);">${this.esc(r.no)}</a><div class="cell-muted" style="font-size:11px;">${this.esc(r.source)}</div></td>
            <td>${this.esc(r.supplier)}</td><td>${this.esc(r.location)}</td><td>${this.esc(r.qty)}</td><td>${this.esc(r.reason)}</td>
            <td>${this.esc(this.STATUS[r.status] || r.status)}${r.dispatched_on ? `<div class="cell-muted" style="font-size:11px;">${formatDate(r.dispatched_on)}</div>` : ""}</td>
            <td>${r.status === "draft" ? `<input class="rt-challan" placeholder="Challan" style="width:110px;"><input class="rt-vehicle" placeholder="Vehicle" style="width:110px;margin-left:4px;"><div class="btn btn-primary btn-sm rt-go" data-no="${this.esc(r.no)}" style="margin-top:4px;">Dispatch</div><div class="btn btn-secondary btn-sm rt-cancel" data-no="${this.esc(r.no)}">Cancel</div>` : ""}</td>
          </tr>`).join("")}
        </tbody></table><div id="rtNote" style="font-size:12px;color:var(--warning-text);margin-top:6px;"></div>`
      : `<div class="empty-state">No returns yet.</div>`;
    w.querySelectorAll(".rt-go").forEach((btn) => btn.addEventListener("click", () => this.dispatch(btn.dataset.no, btn)));
    w.querySelectorAll(".rt-cancel").forEach((btn) => btn.addEventListener("click", () => this.cancel(btn.dataset.no)));
  },

  async dispatch(no, btn) {
    const row = btn.closest("tr");
    const res = await API.postJSON(`/api/returns/${encodeURIComponent(no)}/dispatch`, {
      challan: row.querySelector(".rt-challan").value.trim(), vehicle: row.querySelector(".rt-vehicle").value.trim(),
    });
    if (!res.ok) { document.getElementById("rtNote").textContent = res.data.detail || "The return was not dispatched."; return; }
    await Promise.all([this.loadHolds(), this.loadReturns()]);
  },

  async cancel(no) {
    const reason = "Cancelled on the quality hold and returns screen";
    const res = await API.postJSON(`/api/returns/${encodeURIComponent(no)}/cancel`, { reason });
    if (res.ok) await Promise.all([this.loadHolds(), this.loadReturns()]);
  },
};
