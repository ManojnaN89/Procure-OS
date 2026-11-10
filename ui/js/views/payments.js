const PaymentsView = {
  esc(value) {
    return String(value ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  },

  STATUS: { proposed: "Waiting for approval", approved: "Approved", file_generated: "Bank file made", paid: "Paid", cancelled: "Cancelled" },
  BADGE: { proposed: "badge-pending", approved: "badge-approved", file_generated: "badge-pending", paid: "badge-approved", cancelled: "badge-rejected" },

  async render(container, params) {
    if (params && params.id) return this.renderRun(container, params.id);
    return this.renderList(container);
  },

  async renderList(container) {
    container.innerHTML = `
      <div class="page-header page-header-row">
        <div><h1>Payments</h1><p>Pay what is due: plan a run, the owner approves it, then it goes to the bank</p></div>
      </div>
      <div class="panel" style="margin-bottom:14px;">
        <div class="panel-title">Plan a payment run</div>
        <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
          <input id="runDate" type="date" style="padding:6px;"> <span class="cell-muted" style="font-size:12px;">pays everything due within</span>
          <input id="runWindow" type="number" min="0" value="7" style="width:60px;padding:6px;"> <span class="cell-muted" style="font-size:12px;">days; small suppliers first</span>
          <label style="font-size:12px;display:flex;gap:4px;align-items:center;"><input id="runAdvances" type="checkbox" checked> include advances on orders</label>
          <div class="btn btn-secondary btn-sm" id="previewRun">See what is due</div>
          <div class="btn btn-primary btn-sm" id="planRun">Send the run for approval</div>
        </div>
        <div id="duePreview" style="margin-top:10px;"></div>
        <div id="planNote" style="font-size:12.5px;color:var(--warning-text);margin-top:6px;"></div>
      </div>
      <div class="table-wrap" id="tableWrap"><div class="empty-state">Loading…</div></div>`;
    const date = document.getElementById("runDate");
    date.value = new Date().toISOString().slice(0, 10);
    const params = () => `on=${encodeURIComponent(date.value)}&window=${encodeURIComponent(document.getElementById("runWindow").value || 7)}`;
    document.getElementById("previewRun").addEventListener("click", async () => {
      const box = document.getElementById("duePreview");
      try {
        const rows = await API.getJSON(`/api/payment-runs/due?${params()}`);
        const advances = document.getElementById("runAdvances").checked;
        const shown = rows.filter((r) => advances || r.kind !== "advance");
        const money = (v) => (v == null ? "" : formatCurrency(v));
        const saving = shown.reduce((sum, r) => sum + Number(r.discount || 0), 0);
        box.innerHTML = shown.length
          ? `<table class="data-table"><thead><tr><th>FOR</th><th>SUPPLIER</th><th>DUE</th><th>TOTAL</th><th>TAX HELD</th><th>CREDIT</th><th>ADVANCE TAKEN OFF</th><th>DISCOUNT</th><th>INTEREST</th><th>TO PAY</th></tr></thead><tbody>
              ${shown.map((r) => `<tr><td class="cell-muted">${r.kind === "advance" ? `Advance on ${this.esc(r.po_no)}` : this.esc(r.invoice)}</td><td>${this.esc(r.supplier)}${r.msme ? ' <span class="badge badge-pending">small supplier</span>' : ""}</td><td>${formatDate(r.due_date)}</td>
                <td>${money(r.total)}</td><td>${money(r.tds)}</td><td>${money(r.credit)}</td><td>${money(r.advance)}</td>
                <td>${Number(r.discount) ? `${formatCurrency(r.discount)} <span class="cell-muted" style="font-size:11px;">if paid by ${formatDate(r.discount_until)}</span>` : ""}</td><td>${money(r.interest)}</td><td>${formatCurrency(r.net)}</td></tr>`).join("")}
            </tbody></table>${saving ? `<div class="cell-muted" style="font-size:12px;margin-top:6px;">Early-payment discount earned by paying on this run: ${formatCurrency(saving)}</div>` : ""}
            ${shown.some((r) => r.kind === "advance") ? `<div class="cell-muted" style="font-size:12px;">Tax at source on an advance is worked out when the run is planned.</div>` : ""}`
          : `<div class="cell-muted" style="font-size:12.5px;">Nothing is due in this window.</div>`;
      } catch { box.textContent = "Couldn't load what is due."; }
    });
    document.getElementById("planRun").addEventListener("click", async () => {
      const res = await API.postJSON("/api/payment-runs", { run_date: date.value, window_days: document.getElementById("runWindow").value, include_advances: document.getElementById("runAdvances").checked });
      if (res.ok) { window.location.hash = `#/payments/${res.data.run}`; return; }
      document.getElementById("planNote").textContent = res.data.detail || "The run was not planned.";
    });
    try {
      const rows = await API.getJSON("/api/payment-runs");
      const wrap = document.getElementById("tableWrap");
      if (!wrap) return;
      wrap.innerHTML = rows.length
        ? `<table class="data-table"><thead><tr><th>RUN</th><th>DATE</th><th>PAYMENTS</th><th>TOTAL</th><th>STATUS</th></tr></thead><tbody>
            ${rows.map((r) => `<tr data-id="${this.esc(r.id)}"><td class="cell-muted">${this.esc(r.id)}</td><td>${formatDate(r.run_date)}</td><td>${this.esc(r.payments)}</td><td>${formatCurrency(r.total)}</td>
              <td><span class="badge ${this.BADGE[r.status] || ""}">${this.esc(this.STATUS[r.status] || r.status)}</span></td></tr>`).join("")}</tbody></table>`
        : `<div class="empty-next"><strong>No payment runs yet.</strong><br>Next: choose a date above and press <strong>See what is due</strong>.</div>`;
      wrap.querySelectorAll("tr[data-id]").forEach((tr) => tr.addEventListener("click", () => { window.location.hash = `#/payments/${tr.dataset.id}`; }));
    } catch {
      const wrap = document.getElementById("tableWrap");
      if (wrap) wrap.innerHTML = `<div class="empty-state">Couldn't load payment runs.</div>`;
    }
  },

  async renderRun(container, id) {
    container.innerHTML = `<div class="page-header"><h1>${this.esc(id)}</h1></div><div class="panel">Loading\\u2026</div>`;
    try {
      const r = await API.getJSON(`/api/payment-runs/${encodeURIComponent(id)}`);
      container.innerHTML = `
        <div class="page-header"><a href="#/payments" style="color:var(--text-muted);font-size:12px;text-decoration:none;">← Payments</a>
          <h1 style="margin-top:6px;">${this.esc(r.id)}</h1>
          <p><span class="badge ${this.BADGE[r.status] || ""}">${this.esc(this.STATUS[r.status] || r.status)}</span> · ${formatCurrency(r.total)} · run date ${formatDate(r.run_date)}</p></div>
        ${FlowStrip.forRun(r)}
        ${r.status === "proposed" ? `<div class="panel" style="margin-bottom:12px;font-size:12.5px;">Waiting for the owner to approve${r.approval ? `: ${this.esc(r.approval.summary)}` : ""} (under Admin → Approvals). Nothing is paid until then.</div>` : ""}
        ${r.payments.map((p) => `
          <div class="panel" style="margin-bottom:10px;">
            <div style="display:flex;justify-content:space-between;"><div><strong>${this.esc(p.supplier)}</strong>${p.kind === "advance" ? ` <span class="badge badge-pending">advance on ${this.esc(p.po_no)}</span>` : ""} <span class="cell-muted">${this.esc(p.id)} · ${this.esc(p.mode.toUpperCase())}${p.utr ? ` · bank reference ${this.esc(p.utr)}` : ""}${p.paid_on ? ` · paid ${formatDate(p.paid_on)}` : ""}</span></div><div>${formatCurrency(p.amount)}</div></div>
            <table class="data-table" style="margin-top:8px;"><tbody>${p.invoices.map((i) => `<tr><td class="cell-muted">${this.esc(i.id)}</td><td>${this.esc(i.number)}${i.type !== "invoice" ? ` <span class="badge badge-pending">${this.esc(i.type.replace("_", " "))}</span>` : ""}</td><td style="text-align:right;">${formatCurrency(i.amount)}${Number(i.discount) ? `<div class="cell-muted" style="font-size:11px;">less early-payment discount ${formatCurrency(i.discount)}</div>` : ""}</td></tr>`).join("")}</tbody></table>
            ${p.kind === "advance" ? `<div class="cell-muted" style="font-size:11.5px;margin-top:6px;">An advance against the order: what is left of it is taken off this order's invoices when they arrive.</div>` : ""}
            ${Number(p.tds) ? `<div class="cell-muted" style="font-size:11.5px;margin-top:6px;">Tax held back ${formatCurrency(p.tds)}</div>` : ""}${Number(p.interest) ? `<div class="cell-muted" style="font-size:11.5px;">Interest to a small supplier ${formatCurrency(p.interest)}</div>` : ""}
          </div>`).join("")}
        <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:6px;">
          ${r.can_file ? `<div class="btn btn-primary btn-sm" id="makeFile">Make the bank file</div>` : ""}
          ${r.has_file ? `<div class="btn btn-secondary btn-sm" id="showFile">Show the bank file</div>` : ""}
          ${r.can_mark_paid ? `<input id="paidOn" type="date" style="padding:6px;"><div class="btn btn-primary btn-sm" id="markPaid">The bank has paid this run</div>` : ""}
        </div>
        ${r.can_mark_paid ? `<div class="panel" style="margin-top:12px;"><div class="panel-title">Or settle it from the bank statement</div>
          <p class="cell-muted" style="font-size:12px;margin:0 0 8px;">A CSV with a date, the bank reference, the narration (it must carry the payment number) and the debit. A payment is settled when its reference and amount agree; every other line is listed for you.</p>
          <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;"><input id="stmtFile" type="file" accept=".csv,text/csv"><div class="btn btn-secondary btn-sm" id="importStmt">Import the bank statement</div></div>
          <div id="stmtResult" style="font-size:12.5px;margin-top:8px;"></div></div>` : ""}
        <pre id="fileText" style="display:none;margin-top:10px;font-size:11.5px;background:var(--surface-sunken);padding:10px;border-radius:8px;overflow:auto;"></pre>
        <div id="runNote" style="font-size:12.5px;color:var(--warning-text);margin-top:8px;"></div>`;
      const note = document.getElementById("runNote");
      const on = (eid, fn) => { const el = document.getElementById(eid); if (el) el.addEventListener("click", fn); };
      const paid = document.getElementById("paidOn");
      if (paid) paid.value = new Date().toISOString().slice(0, 10);
      on("makeFile", async () => {
        const res = await API.postJSON(`/api/payment-runs/${encodeURIComponent(id)}/file`, {});
        if (res.ok) return this.renderRun(container, id);
        note.textContent = res.data.detail || "The file was not made.";
      });
      on("showFile", async () => {
        const res = await API.getJSON(`/api/payment-runs/${encodeURIComponent(id)}/file`);
        const pre = document.getElementById("fileText");
        pre.textContent = res.file;
        pre.style.display = "block";
      });
      on("markPaid", async () => {
        const res = await API.postJSON(`/api/payment-runs/${encodeURIComponent(id)}/paid`, { paid_on: paid.value });
        if (res.ok) return this.renderRun(container, id);
        note.textContent = res.data.detail || "That did not work.";
      });
      on("importStmt", async () => {
        const file = document.getElementById("stmtFile").files[0];
        const out = document.getElementById("stmtResult");
        if (!file) { out.textContent = "Choose the statement file first."; return; }
        const res = await API.postJSON("/api/payment-runs/statement", { csv: await file.text() });
        if (!res.ok) { out.textContent = res.data.detail || "The statement was not read."; return; }
        const d = res.data;
        const open = [...d.for_a_person.map((x) => `line ${x.row}: ${x.why}`), ...d.waiting.map((x) => `line ${x.row}: ${x.why}`)];
        out.innerHTML = `<div>${d.matched.length} payment(s) matched, ${d.runs_paid.length} run(s) settled${d.already_paid.length ? `, ${d.already_paid.length} already paid` : ""}.</div>`
          + (open.length ? `<div style="margin-top:6px;color:var(--warning-text);">${open.length} line(s) need a person (a task was made):<ul style="margin:4px 0 0 18px;">${open.slice(0, 10).map((t) => `<li>${this.esc(t)}</li>`).join("")}</ul></div>` : "");
        if (d.runs_paid.includes(id)) return this.renderRun(container, id);
      });
    } catch {
      container.innerHTML = `<div class="empty-state">Couldn't load this payment run.</div>`;
    }
  },
};
