const PaymentsView = {
  esc(value) {
    return String(value ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  },

  STATUS: {
    proposed: "Waiting for approval", approved: "Approved", file_generated: "Bank file made", paid: "Paid",
    cancelled: "Cancelled", planned: "Planned", in_file: "In the bank file", recorded: "Recorded, waiting for the bank",
    settled: "Settled by the bank", failed: "Returned by the bank", reversed: "Reversed",
  },
  BADGE: {
    proposed: "badge-waiting", approved: "badge-approved", file_generated: "badge-sent", paid: "badge-paid",
    cancelled: "badge-draft", planned: "badge-waiting", in_file: "badge-sent", recorded: "badge-waiting",
    settled: "badge-paid", failed: "badge-danger", reversed: "badge-draft",
  },

  async render(container, params) {
    if (params && params.id) return this.renderRun(container, params.id);
    return this.renderList(container);
  },

  async renderList(container) {
    container.innerHTML = `
      <div class="page-header page-header-row">
        <div><h1>Payments</h1><p>What is due, a run the rule's approver decides with a code, and the bank's statement</p></div>
      </div>
      <div class="panel" style="margin-bottom:14px;">
        <div class="panel-title">Plan a payment run</div>
        <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
          <input id="runDate" type="date" style="padding:6px;"> <span class="cell-muted" style="font-size:12px;">pays what is due by its pay-by day within</span>
          <input id="runWindow" type="number" min="0" value="7" style="width:60px;padding:6px;"> <span class="cell-muted" style="font-size:12px;">days; small suppliers first</span>
          <label style="font-size:12px;display:flex;gap:4px;align-items:center;"><input id="runAdvances" type="checkbox" checked> include approved advances</label>
          <div class="btn btn-secondary btn-sm" id="previewRun">See what is due</div>
          <div class="btn btn-primary btn-sm" id="planRun">Send the run for approval</div>
        </div>
        <div id="duePreview" style="margin-top:10px;"></div>
        <div id="planNote" style="font-size:12.5px;color:var(--warning-text);margin-top:6px;"></div>
      </div>
      <div style="display:flex;gap:8px;margin-bottom:10px;">
        <div class="btn btn-secondary btn-sm" id="tabRuns">Runs</div>
        <div class="btn btn-secondary btn-sm" id="tabTds">TDS to confirm</div>
        <div class="btn btn-secondary btn-sm" id="tabAdvances">Advances</div>
      </div>
      <div id="paymentBody"></div>`;
    const date = document.getElementById("runDate");
    date.value = new Date().toISOString().slice(0, 10);
    document.getElementById("previewRun").addEventListener("click", () => this.preview());
    document.getElementById("planRun").addEventListener("click", async () => {
      const res = await API.postJSON("/api/payment-runs", {
        run_date: date.value,
        window_days: document.getElementById("runWindow").value,
        include_advances: document.getElementById("runAdvances").checked,
      });
      if (res.ok) { window.location.hash = `#/payments/${res.data.run}`; return; }
      document.getElementById("planNote").textContent = res.data.detail || "The run was not planned.";
    });
    document.getElementById("tabRuns").addEventListener("click", () => this.listRuns());
    document.getElementById("tabTds").addEventListener("click", () => this.tds(container));
    document.getElementById("tabAdvances").addEventListener("click", () => this.advances(container));
    this.listRuns();
  },

  async preview() {
    const box = document.getElementById("duePreview");
    const on = document.getElementById("runDate").value;
    const windowDays = document.getElementById("runWindow").value || 7;
    try {
      const data = await API.getJSON(`/api/payment-runs/due?on=${encodeURIComponent(on)}&window=${encodeURIComponent(windowDays)}`);
      const advances = document.getElementById("runAdvances").checked;
      const rows = (data.due || []).filter((r) => advances || r.kind !== "advance");
      box.innerHTML = rows.length
        ? `<table class="data-table"><thead><tr><th>FOR</th><th>SUPPLIER</th><th>PAY BY</th><th>DUE</th><th>MSME</th><th>TDS</th><th>NOTES</th><th>ADVANCE TAKEN OFF</th><th>TO PAY</th></tr></thead><tbody>
            ${rows.map((r) => `<tr><td class="cell-muted">${r.kind === "advance" ? `Advance on ${this.esc(r.po_no)}` : this.esc(r.invoice)}</td>
              <td>${this.esc(r.supplier)}</td><td>${formatDate(r.pay_by)}</td><td>${formatDate(r.due_date)}</td>
              <td>${r.msme ? `small supplier${r.msme_deadline ? `, by ${formatDate(r.msme_deadline)}` : ""}` : ""}</td>
              <td>${r.tds ? formatCurrency(r.tds) : ""}${r.tds_status === "proposed" ? ' <span class="badge badge-pending">to confirm</span>' : ""}</td>
              <td>${r.credit ? formatCurrency(r.credit) : ""}</td><td>${r.advance ? formatCurrency(r.advance) : ""}</td>
              <td>${formatCurrency(r.net)}</td></tr>`).join("")}</tbody></table>`
        : `<div class="cell-muted" style="font-size:12.5px;">Nothing is due in this window.</div>`;
      if ((data.held || []).length) {
        box.innerHTML += `<div class="panel" style="margin-top:10px;"><div class="panel-title">Held back</div><ul style="margin:4px 0 0 18px;font-size:12.5px;">
          ${data.held.map((h) => `<li>${this.esc(h.item || "")}: ${this.esc(h.reason)}</li>`).join("")}</ul></div>`;
      }
    } catch { box.textContent = "Couldn't load what is due."; }
  },

  async listRuns() {
    try {
      const rows = await API.getJSON("/api/payment-runs");
      const body = document.getElementById("paymentBody");
      if (!body) return;
      body.innerHTML = rows.length
        ? `<table class="data-table"><thead><tr><th>RUN</th><th>DATE</th><th>PAYMENTS</th><th>TOTAL</th><th>STATUS</th></tr></thead><tbody>
            ${rows.map((r) => `<tr data-id="${this.esc(r.id)}"><td class="cell-muted">${this.esc(r.id)}</td><td>${formatDate(r.run_date)}</td>
              <td>${this.esc(r.payments)}</td><td>${formatCurrency(r.total)}</td>
              <td><span class="badge ${this.BADGE[r.status] || ""}">${this.esc(this.STATUS[r.status] || r.status)}</span></td></tr>`).join("")}</tbody></table>`
        : `<div class="empty-next"><strong>No payment runs yet.</strong><br>Next: choose a date above and press <strong>See what is due</strong>.</div>`;
      body.querySelectorAll("tr[data-id]").forEach((tr) => tr.addEventListener("click", () => {
        window.location.hash = `#/payments/${tr.dataset.id}`;
      }));
    } catch { /* the tab stays as it was */ }
  },

  async renderRun(container, id) {
    container.innerHTML = `<div class="page-header"><h1>${this.esc(id)}</h1></div><div class="panel">Loading…</div>`;
    try {
      const r = await API.getJSON(`/api/payment-runs/${encodeURIComponent(id)}`);
      const steps = (r.steps || []).map((s) => `<li>${s.step}. ${this.esc(s.name)} (${this.esc(s.role)})${s.approved_at ? " — approved" : " — waiting"}</li>`).join("");
      container.innerHTML = `
        <div class="page-header"><a href="#/payments" style="color:var(--text-muted);font-size:12px;text-decoration:none;">← Payments</a>
          <h1 style="margin-top:6px;">${this.esc(r.id)}</h1>
          <p><span class="badge ${this.BADGE[r.status] || ""}">${this.esc(this.STATUS[r.status] || r.status)}</span> · ${formatCurrency(r.total)} · run date ${formatDate(r.run_date)}${r.rule ? ` · ${this.esc(r.rule)}` : ""}</p></div>
        ${FlowStrip.forRun(r)}
        ${r.cancel_reason ? `<div class="panel" style="margin-bottom:12px;">Cancelled: ${this.esc(r.cancel_reason)}</div>` : ""}
        ${r.status === "proposed" ? `<div class="panel" style="margin-bottom:12px;font-size:12.5px;">
          <div class="panel-title">Approval</div><ol style="margin:4px 0 8px 18px;">${steps}</ol>
          ${r.can_approve ? `<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
            <div class="btn btn-secondary btn-sm" id="sendCode">Email me a code</div>
            <input id="code" placeholder="one-time code" style="padding:6px;width:150px;">
            <div class="btn btn-primary btn-sm" id="approveRun">Approve this run</div>
            <div class="btn btn-secondary btn-sm" id="rejectRun">Reject</div></div>` : `<div class="cell-muted">Waiting for the person the rule names.</div>`}
        </div>` : ""}
        ${(r.removed || []).length ? `<div class="panel" style="margin-bottom:10px;"><div class="panel-title">Left out</div><ul style="margin:4px 0 0 18px;font-size:12.5px;">${r.removed.map((x) => `<li>${this.esc(x.item)}: ${this.esc(x.reason)}</li>`).join("")}</ul></div>` : ""}
        ${r.payments.map((p) => `
          <div class="panel" style="margin-bottom:10px;">
            <div style="display:flex;justify-content:space-between;"><div><strong>${this.esc(p.supplier)}</strong>${p.kind === "advance" ? ` <span class="badge badge-pending">advance on ${this.esc(p.po_no)}</span>` : ""}
              <span class="cell-muted">${this.esc(p.id)} · ${this.esc((p.mode || "").toUpperCase())} · <span class="badge ${this.BADGE[p.status] || ""}">${this.esc(this.STATUS[p.status] || p.status)}</span>${p.utr ? ` · bank reference ${this.esc(p.utr)}` : ""}${p.value_date ? ` · value date ${formatDate(p.value_date)}` : ""}</span></div><div>${formatCurrency(p.amount)}</div></div>
            <table class="data-table" style="margin-top:8px;"><tbody>${p.invoices.map((i) => `<tr><td class="cell-muted">${this.esc(i.id)}</td><td>${this.esc(i.number)}${i.type !== "invoice" ? ` <span class="badge badge-pending">${this.esc(i.type.replace("_", " "))}</span>` : ""}</td><td style="text-align:right;">${formatCurrency(i.amount)}</td></tr>`).join("")}</tbody></table>
            ${p.fail_reason ? `<div class="cell-muted" style="font-size:12px;margin-top:6px;">Returned: ${this.esc(p.fail_reason)}</div>` : ""}
            ${p.status === "in_file" && r.can_record ? `<div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:8px;align-items:center;">
              <input data-utr="${this.esc(p.id)}" placeholder="bank reference" style="padding:5px;width:160px;">
              <input data-date="${this.esc(p.id)}" type="date" value="${r.run_date}" style="padding:5px;">
              <input data-proof="${this.esc(p.id)}" type="file" accept=".pdf,image/*">
              <div class="btn btn-secondary btn-sm" data-record="${this.esc(p.id)}">Record this payment</div>
              <div class="btn btn-secondary btn-sm" data-failed="${this.esc(p.id)}">Bank returned it</div></div>` : ""}
          </div>`).join("")}
        <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:6px;">
          ${r.can_file ? `<div class="btn btn-primary btn-sm" id="makeFile">Make the bank file</div>` : ""}
          ${r.has_file ? `<div class="btn btn-secondary btn-sm" id="showFile">Show the bank file</div>` : ""}
          ${r.can_cancel ? `<div class="btn btn-secondary btn-sm" id="cancelRun">Cancel run</div>` : ""}
        </div>
        <div class="panel" style="margin-top:12px;">${r.can_import_statement ? `<div class="panel-title">Import the bank statement</div>
          <p class="cell-muted" style="font-size:12px;margin:0 0 8px;">A CSV with a date, the bank reference, the narration (carrying the payment number) and the debit. Each payment is settled on its own line; a credit that names one of our payments is listed as a return.</p>
          <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;"><input id="stmtFile" type="file" accept=".csv,text/csv">
            <div class="btn btn-secondary btn-sm" id="importStmt">Import the statement</div></div>
          <div id="stmtResult" style="font-size:12.5px;margin-top:8px;"></div>` : ""}</div>
        <pre id="fileText" style="display:none;margin-top:10px;font-size:11.5px;background:var(--surface-sunken);padding:10px;border-radius:8px;overflow:auto;"></pre>
        <div id="runNote" style="font-size:12.5px;color:var(--warning-text);margin-top:8px;"></div>`;
      const note = document.getElementById("runNote");
      const on = (eid, fn) => { const el = document.getElementById(eid); if (el) el.addEventListener("click", fn); };
      on("sendCode", async () => {
        const res = await API.postJSON(`/api/payment-runs/${encodeURIComponent(id)}/code`, {});
        note.textContent = res.ok ? `The code went to ${res.data.sent_to}.` : (res.data.detail || "The code was not sent.");
      });
      on("approveRun", async () => {
        const res = await API.postJSON(`/api/payment-runs/${encodeURIComponent(id)}/approve`, {
          code: (document.getElementById("code") || {}).value || null,
        });
        if (res.ok) return this.renderRun(container, id);
        note.textContent = res.data.detail || "The run was not approved.";
      });
      on("rejectRun", async () => {
        const reason = window.prompt("Why is this run rejected?");
        if (!reason) return;
        const res = await API.postJSON(`/api/payment-runs/${encodeURIComponent(id)}/reject`, { reason });
        if (res.ok) return this.renderRun(container, id);
        note.textContent = res.data.detail || "That did not work.";
      });
      on("cancelRun", async () => {
        const reason = window.prompt("Why is this run cancelled?") || "cancelled";
        const res = await API.postJSON(`/api/payment-runs/${encodeURIComponent(id)}/cancel`, { reason });
        if (res.ok) return this.renderRun(container, id);
        note.textContent = res.data.detail || "That did not work.";
      });
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
      on("importStmt", async () => {
        const file = document.getElementById("stmtFile").files[0];
        const out = document.getElementById("stmtResult");
        if (!file) { out.textContent = "Choose the statement file first."; return; }
        const res = await API.postJSON("/api/payment-runs/statement", { csv: await file.text() });
        if (!res.ok) { out.textContent = res.data.detail || "The statement was not read."; return; }
        const d = res.data;
        out.innerHTML = `<div>${plural((d.settled || []).length, "payment")} settled, ${plural((d.for_a_person || []).length, "line")} for a person.</div>`
          + ((d.returns || []).length ? `<div style="margin-top:6px;color:var(--warning-text);">Maybe a return: ${d.returns.map((x) => this.esc(x.why)).join("; ")}</div>` : "");
        if ((d.settled || []).length) return this.renderRun(container, id);
      });
      container.querySelectorAll("[data-record]").forEach((el) => el.addEventListener("click", async () => {
        const pay = el.dataset.record;
        const form = new FormData();
        const utr = container.querySelector(`[data-utr="${pay}"]`).value;
        if (utr) form.append("utr", utr);
        form.append("value_date", container.querySelector(`[data-date="${pay}"]`).value);
        const proof = container.querySelector(`[data-proof="${pay}"]`).files[0];
        if (proof) form.append("proof", proof);
        const res = await API.postForm(`/api/payments/${encodeURIComponent(pay)}/record`, form);
        if (res.ok) return this.renderRun(container, id);
        note.textContent = res.data.detail || "The payment was not recorded.";
      }));
      container.querySelectorAll("[data-failed]").forEach((el) => el.addEventListener("click", async () => {
        const pay = el.dataset.failed;
        const reason = window.prompt("What did the bank say?");
        if (!reason) return;
        const form = new FormData();
        form.append("reason", reason);
        form.append("value_date", new Date().toISOString().slice(0, 10));
        const res = await API.postForm(`/api/payments/${encodeURIComponent(pay)}/failed`, form);
        if (res.ok) return this.renderRun(container, id);
        note.textContent = res.data.detail || "That did not work.";
      }));
    } catch {
      container.innerHTML = `<div class="empty-state">Couldn't load this payment run.</div>`;
    }
  },

  async tds(container) {
    const body = document.getElementById("paymentBody");
    try {
      const rows = await API.getJSON("/api/tds?status=proposed");
      body.innerHTML = rows.length
        ? `<div class="panel"><div class="panel-title">Tax at source to confirm</div>
          <table class="data-table"><thead><tr><th></th><th>INVOICE</th><th>SUPPLIER</th><th>SECTION</th><th>BASE</th><th>AMOUNT</th><th>WHY</th></tr></thead><tbody>
          ${rows.map((r) => `<tr><td><input type="checkbox" data-tds="${r.id}"></td><td>${this.esc(r.invoice)} ${this.esc(r.invoice_no || "")}</td><td>${this.esc(r.supplier)}</td>
            <td>${this.esc(r.section)}</td><td>${formatCurrency(r.base)}</td><td>${formatCurrency(r.amount)}</td><td class="cell-muted">${this.esc(r.words)}</td></tr>`).join("")}</tbody></table>
          <div style="display:flex;gap:8px;margin-top:8px;align-items:center;flex-wrap:wrap;">
            <input id="tdsSection" placeholder="change section (optional)" style="padding:6px;width:180px;">
            <input id="tdsReason" placeholder="reason for a change" style="padding:6px;width:220px;">
            <div class="btn btn-primary btn-sm" id="confirmTds">Confirm the selected</div></div>
          <div id="tdsNote" style="font-size:12.5px;color:var(--warning-text);margin-top:6px;"></div></div>`
        : `<div class="empty-state"><strong>Nothing waits for the tax owner.</strong></div>`;
      const el = document.getElementById("confirmTds");
      if (el) el.addEventListener("click", async () => {
        const ids = [...body.querySelectorAll("[data-tds]:checked")].map((x) => Number(x.dataset.tds));
        const payload = { ids };
        const section = document.getElementById("tdsSection").value.trim();
        if (section) payload.section_code = section;
        const reason = document.getElementById("tdsReason").value.trim();
        if (reason) payload.reason = reason;
        const res = await API.postJSON("/api/tds/confirm", payload);
        if (res.ok) return this.tds(container);
        document.getElementById("tdsNote").textContent = res.data.detail || "The rows were not confirmed.";
      });
    } catch { if (body) body.innerHTML = `<div class="empty-state">Couldn't load the tax rows.</div>`; }
  },

  async advances(container) {
    const body = document.getElementById("paymentBody");
    try {
      const rows = await API.getJSON("/api/advances");
      body.innerHTML = rows.length
        ? `<table class="data-table"><thead><tr><th>ORDER</th><th>SUPPLIER</th><th>PERCENT</th><th>GROSS</th><th>TAXABLE</th><th>STATUS</th><th>APPROVER</th><th></th></tr></thead><tbody>
          ${rows.map((r) => `<tr><td>${this.esc(r.po_no)}</td><td>${this.esc(r.supplier)}</td><td>${this.esc(r.pct)}%</td>
            <td>${formatCurrency(r.gross)}</td><td>${formatCurrency(r.taxable)}</td><td><span class="badge badge-pending">${this.esc(r.status)}</span></td>
            <td>${this.esc(r.approver || "")}</td>
            <td>${["paid", "recovering"].includes(r.status) && Number(r.unused) > 0 ? `<div class="btn btn-secondary btn-sm" data-recover="${r.id}">Recover</div>` : ""}</td></tr>`).join("")}</tbody></table>
          <div id="advanceNote2" style="font-size:12.5px;color:var(--warning-text);margin-top:6px;"></div>`
        : `<div class="empty-next"><strong>No advances yet.</strong><br>Next: a buyer asks for one on the order's page.</div>`;
      body.querySelectorAll("[data-recover]").forEach((el) => el.addEventListener("click", async () => {
        const reason = window.prompt("Why is this advance recovered?");
        if (!reason) return;
        const res = await API.postJSON(`/api/advances/${el.dataset.recover}/recover`, { reason });
        if (res.ok) return this.advances(container);
        document.getElementById("advanceNote2").textContent = res.data.detail || "That did not work.";
      }));
    } catch { if (body) body.innerHTML = `<div class="empty-state">Couldn't load the advances.</div>`; }
  },
};
