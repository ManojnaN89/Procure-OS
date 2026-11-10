const InvoicesView = {
  esc(value) {
    return String(value ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  },

  STATUS: { received: "Received", matched: "Matched", blocked: "Blocked", approved: "Approved for payment", part_paid: "Part paid", paid: "Paid", cancelled: "Cancelled" },
  BADGE: { blocked: "badge-rejected", approved: "badge-approved", paid: "badge-approved", cancelled: "badge-pending", received: "badge-pending", part_paid: "badge-pending", matched: "badge-approved" },
  REASON: {
    price_variance: "Price above the order price",
    qty_over_received: "Quantity more than was received",
    irn_missing: "No IRN on an e-invoice",
    gstin_mismatch: "GSTIN does not match",
    tax_not_allowed: "GST charged by a supplier who cannot",
    arithmetic_mismatch: "The invoice does not add up",
  },

  async render(container, params) {
    if (params && params.id === "new") return this.renderEntry(container);
    if (params && params.id) return this.renderDetail(container, params.id);
    return this.renderList(container);
  },

  async renderList(container) {
    container.innerHTML = `
      <div class="page-header page-header-row">
        <div><h1>Invoices</h1><p>Supplier invoices, matched to the order and the goods received. Blocked ones wait for a person.</p></div>
        <div style="display:flex;gap:8px;">
          <div class="btn btn-secondary" id="uploadInvoice"><i class="ti ti-upload"></i>Upload an invoice</div>
          <div class="btn btn-primary" id="newInvoice"><i class="ti ti-plus"></i>Enter an invoice</div>
          <input type="file" id="invFile" accept=".pdf,.png,.jpg,.jpeg,.tif,.tiff,.bmp" style="display:none;">
        </div>
      </div>
      <div id="invDrafts"></div>
      <div class="tabs" id="invFilter">
        <button class="tab active" data-s="">All</button>
        <button class="tab" data-s="blocked">Blocked</button>
        <button class="tab" data-s="approved">Approved for payment</button>
        <button class="tab" data-s="paid">Paid</button>
      </div>
      <div class="table-wrap" id="tableWrap"><div class="empty-state">Loading…</div></div>`;
    document.getElementById("newInvoice").addEventListener("click", () => { window.location.hash = "#/invoices/new"; });
    this.wireDrafts(container);
    const load = async (status) => {
      const wrap = document.getElementById("tableWrap");
      try {
        const rows = await API.getJSON(`/api/invoices${status ? `?status=${encodeURIComponent(status)}` : ""}`);
        if (!wrap) return;
        wrap.innerHTML = rows.length
          ? `<table class="data-table"><thead><tr><th>INVOICE</th><th>SUPPLIER</th><th>SUPPLIER'S NUMBER</th><th>DATE</th><th>AMOUNT</th><th>STATUS</th></tr></thead><tbody>
              ${rows.map((r) => `<tr data-id="${this.esc(r.id)}">
                <td class="cell-muted">${this.esc(r.id)}${r.type !== "invoice" ? ` <span class="badge badge-pending">${this.esc(r.type.replace("_", " "))}</span>` : ""}</td>
                <td>${this.esc(r.supplier)}</td><td>${this.esc(r.number)}</td><td class="cell-muted">${formatDate(r.date)}</td><td>${formatCurrency(r.total)}</td>
                <td><span class="badge ${this.BADGE[r.status] || ""}">${this.esc(this.STATUS[r.status] || r.status)}</span>${r.reason ? `<div class="cell-muted" style="font-size:11px;margin-top:3px;max-width:340px;">${this.esc(r.reason)}</div>` : ""}${r.next_action ? `<div class="next-action" style="font-size:11.5px;margin-top:3px;max-width:340px;"><strong>Next:</strong> ${this.esc(r.next_action)}</div>` : ""}</td></tr>`).join("")}
            </tbody></table>`
          : `<div class="empty-next"><strong>No invoices${status ? " in this state" : " yet"}.</strong>${status ? "" : "<br>Next: enter the supplier's invoice for goods that have arrived, or upload the document."}</div>`;
        wrap.querySelectorAll("tr[data-id]").forEach((tr) => tr.addEventListener("click", () => { window.location.hash = `#/invoices/${tr.dataset.id}`; }));
      } catch {
        if (wrap) wrap.innerHTML = `<div class="empty-state">Couldn't load invoices.</div>`;
      }
    };
    document.querySelectorAll("#invFilter [data-s]").forEach((b) => b.addEventListener("click", () => {
      document.querySelectorAll("#invFilter .tab").forEach((x) => x.classList.toggle("active", x === b));
      load(b.dataset.s);
    }));
    load("");
  },

  // Invoices read from documents (upload, or mail to the invoice mailbox): a person checks each draft, then confirms it.
  async wireDrafts(container) {
    const box = document.getElementById("invDrafts");
    const file = document.getElementById("invFile");
    const upload = document.getElementById("uploadInvoice");
    if (!box || !file || !upload) return;
    upload.addEventListener("click", () => file.click());
    file.addEventListener("change", async () => {
      if (!file.files.length) return;
      const form = new FormData();
      form.append("file", file.files[0]);
      upload.textContent = "Reading…";
      const res = await Auth.fetch("/api/invoice-drafts/upload", { method: "POST", body: form });
      file.value = "";
      upload.innerHTML = `<i class="ti ti-upload"></i>Upload an invoice`;
      if (res.ok) return this.paintDrafts(box);
      let msg = "The invoice could not be read.";
      try { msg = (await res.json()).detail || msg; } catch {}
      box.insertAdjacentHTML("afterbegin", `<div class="panel" style="margin-bottom:10px;color:var(--warning-text);font-size:12.5px;">${this.esc(msg)}</div>`);
    });
    this.paintDrafts(box);
  },

  async paintDrafts(box) {
    let drafts = [];
    try { drafts = await API.getJSON("/api/invoice-drafts"); } catch { box.innerHTML = ""; return; }
    if (!drafts.length) { box.innerHTML = ""; return; }
    const WAY = { qr: "Read from its QR code", model: "Read by the assistant and checked against the page", none: "Not read" };
    box.innerHTML = `<div class="panel" style="margin-bottom:14px;"><div class="panel-title">Invoices read from documents (${drafts.length})</div>
      ${drafts.map((d) => `<div class="draft-row" data-id="${this.esc(d.id)}" style="border-top:1px solid var(--border);padding:8px 0;font-size:12.5px;">
        <div><strong>${this.esc(d.supplier || "Unknown sender")}</strong> · ${this.esc(d.number || d.filename || "")} ${d.date ? "· " + formatDate(d.date) : ""} ${d.total ? "· " + formatCurrency(d.total) : ""}
          <span class="cell-muted"> · ${this.esc(WAY[d.path] || "")} · ${this.esc(d.source)}</span></div>
        ${d.problems.map((p) => `<div style="color:var(--warning-text);">${this.esc(p)}</div>`).join("")}
        ${d.assumptions.map((p) => `<div class="cell-muted">Assumed: ${this.esc(p)}</div>`).join("")}
        <div style="margin-top:6px;display:flex;gap:8px;">
          ${d.number && !d.problems.length ? `<div class="btn btn-primary btn-sm" data-act="confirm">Check and enter</div>` : ""}
          <div class="btn btn-secondary btn-sm" data-act="dismiss">Dismiss</div></div>
        <div class="draft-note" style="color:var(--warning-text);margin-top:4px;"></div></div>`).join("")}</div>`;
    box.querySelectorAll(".draft-row").forEach((row) => {
      const note = row.querySelector(".draft-note");
      row.querySelectorAll("[data-act]").forEach((b) => b.addEventListener("click", async () => {
        const res = await API.postJSON(`/api/invoice-drafts/${row.dataset.id}/${b.dataset.act}`, {});
        if (res.ok && b.dataset.act === "confirm" && res.data.invoice) { window.location.hash = `#/invoices/${res.data.invoice}`; return; }
        if (res.ok) return this.paintDrafts(box);
        note.textContent = res.data.detail || "That did not work.";
      }));
    });
  },

  async renderEntry(container) {
    container.innerHTML = `
      <div class="page-header"><a href="#/invoices" style="color:var(--text-muted);font-size:12px;text-decoration:none;">← Invoices</a><h1 style="margin-top:6px;">Enter an invoice</h1>
      <p>Choose the supplier, then type what the supplier's invoice says. The platform matches it straight away.</p></div>
      <div class="panel" id="entryBox">Loading…</div>`;
    let ready = [];
    try { ready = await API.getJSON("/api/invoicing/ready"); } catch { document.getElementById("entryBox").textContent = "Couldn't load the goods that can be invoiced."; return; }
    const suppliers = [...new Map(ready.map((r) => [r.supplier_code, r.supplier])).entries()];
    const box = document.getElementById("entryBox");
    box.innerHTML = suppliers.length ? `
      <div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:10px;">
        <select id="invSupplier" style="padding:6px;">${suppliers.map(([c, n]) => `<option value="${this.esc(c)}">${this.esc(n)}</option>`).join("")}</select>
        <select id="invType" style="padding:6px;"><option value="invoice">Invoice</option><option value="credit_note">Credit note</option></select>
        <input id="invNo" placeholder="Supplier's invoice number" style="padding:6px;">
        <input id="invDate" type="date" style="padding:6px;">
        <input id="invRef" placeholder="Credit note for invoice (our number)" style="padding:6px;display:none;">
      </div>
      <div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:10px;">
        <input id="invSG" placeholder="Supplier GSTIN" style="padding:6px;"><input id="invBG" placeholder="Our GSTIN" style="padding:6px;">
        <input id="invIrn" placeholder="IRN (e-invoice)" style="padding:6px;width:260px;"><input id="invEway" placeholder="E-way bill" style="padding:6px;">
      </div>
      <div id="invLines"></div>
      <div class="btn btn-primary btn-sm" id="invSave" style="margin-top:10px;display:inline-block;">Enter and match</div>
      <div id="invNote" style="font-size:12.5px;margin-top:8px;color:var(--warning-text);"></div>` : `<div class="empty-state">Nothing has been received that is waiting for an invoice.</div>`;
    if (!suppliers.length) return;
    const paint = () => {
      const code = document.getElementById("invSupplier").value;
      const mine = ready.filter((r) => r.supplier_code === code);
      document.getElementById("invSG").value = (mine[0] || {}).supplier_gstin || "";
      document.getElementById("invBG").value = (mine[0] || {}).buyer_gstin || "";
      document.getElementById("invLines").innerHTML = `<table class="data-table"><thead><tr><th>ORDER</th><th>ITEM</th><th>CAN BE INVOICED</th><th>QTY ON THE INVOICE</th><th>PRICE ON THE INVOICE</th></tr></thead><tbody>
        ${mine.map((r) => `<tr data-po="${this.esc(r.po_no)}" data-line="${this.esc(r.line_no)}" data-rate="${this.esc(r.gst_rate)}"><td>${this.esc(r.po_no)}</td><td>${this.esc(r.name)}</td><td>${this.esc(r.available)} <span class="cell-muted">${this.esc(r.uom)}</span></td>
          <td><input class="il-qty" type="number" min="0" step="any" style="width:90px;padding:5px;"></td><td><input class="il-price" type="number" min="0" step="any" value="${this.esc(r.unit_price)}" style="width:110px;padding:5px;"></td></tr>`).join("")}</tbody></table>`;
    };
    document.getElementById("invSupplier").addEventListener("change", paint);
    document.getElementById("invType").addEventListener("change", (e) => { document.getElementById("invRef").style.display = e.target.value === "invoice" ? "none" : "inline-block"; });
    paint();
    document.getElementById("invSave").addEventListener("click", async () => {
      const note = document.getElementById("invNote");
      const lines = [...document.querySelectorAll("#invLines tr[data-po]")].map((tr) => ({
        po_no: tr.dataset.po, po_line_no: Number(tr.dataset.line), gst_rate: tr.dataset.rate,
        qty: tr.querySelector(".il-qty").value, unit_price: tr.querySelector(".il-price").value,
      })).filter((l) => l.qty !== "" && Number(l.qty) > 0);
      if (!lines.length) { note.textContent = "Enter the quantity on at least one line."; return; }
      const res = await API.postJSON("/api/invoices", {
        supplier_code: document.getElementById("invSupplier").value, type: document.getElementById("invType").value,
        number: document.getElementById("invNo").value, date: document.getElementById("invDate").value,
        supplier_gstin: document.getElementById("invSG").value, buyer_gstin: document.getElementById("invBG").value,
        irn: document.getElementById("invIrn").value, eway_bill_no: document.getElementById("invEway").value,
        ref_invoice_no: document.getElementById("invRef").value, lines,
      });
      if (res.ok) { window.location.hash = `#/invoices/${res.data.invoice}`; return; }
      note.textContent = res.data.detail || "The invoice was not entered.";
    });
  },

  async renderDetail(container, id) {
    container.innerHTML = `<div class="page-header"><h1>${this.esc(id)}</h1></div><div class="panel">Loading…</div>`;
    try {
      const i = await API.getJSON(`/api/invoices/${encodeURIComponent(id)}`);
      const problems = i.match.filter((m) => m.result === "blocked" || m.reason);
      container.innerHTML = `
        <div class="page-header"><a href="#/invoices" style="color:var(--text-muted);font-size:12px;text-decoration:none;">← Invoices</a>
          <h1 style="margin-top:6px;">${this.esc(i.id)} · ${this.esc(i.supplier)}</h1>
          <p><span class="badge ${this.BADGE[i.status] || ""}">${this.esc(this.STATUS[i.status] || i.status)}</span> · ${formatCurrency(i.total)} · supplier's number ${this.esc(i.number)} · ${formatDate(i.date)}${i.type !== "invoice" ? ` · ${this.esc(i.type.replace("_", " "))}${i.ref_invoice_no ? ` for ${this.esc(i.ref_invoice_no)}` : ""}` : ""}</p></div>
        ${FlowStrip.forInvoice(i)}
        ${i.reason ? `<div class="panel" style="margin-bottom:12px;color:var(--warning-text);font-size:12.5px;"><strong>${this.esc(i.status === "cancelled" && i.duplicate_of ? "Copy of " + i.duplicate_of : "Why it is blocked")}:</strong> ${this.esc(i.reason)}</div>` : ""}
        ${i.pending_release ? `<div class="panel" style="margin-bottom:12px;font-size:12.5px;">Waiting for approval: ${this.esc(i.pending_release.summary)} (approve it under Admin → Approvals).</div>` : ""}
        <div class="table-wrap" style="margin-bottom:12px;"><table class="data-table"><thead><tr><th>ORDER LINE</th><th>ITEM</th><th>QTY</th><th>PRICE</th><th>TAXABLE</th><th>MATCH</th></tr></thead><tbody>
          ${i.lines.map((l) => { const m = i.match.find((x) => x.line_no === l.line_no) || {};
            return `<tr><td>${this.esc(l.po_no)} / ${this.esc(l.po_line_no)}</td><td>${this.esc(l.name)}</td><td>${this.esc(l.qty)} <span class="cell-muted">${this.esc(l.uom)}</span></td><td>${formatCurrency(l.unit_price)}</td><td>${formatCurrency(l.taxable)}</td>
              <td>${m.result ? `<span class="badge ${m.result === "blocked" ? "badge-rejected" : "badge-approved"}">${this.esc(m.result.replace("_", " "))}</span>${m.reason ? `<div class="cell-muted" style="font-size:11px;">${this.esc(this.REASON[m.reason] || m.reason)}</div>` : ""}` : ""}</td></tr>`; }).join("")}
        </tbody></table></div>
        <div class="panel" style="font-size:12.5px;color:var(--text-secondary);margin-bottom:12px;">Taxable ${formatCurrency(i.taxable)} · CGST ${formatCurrency(i.cgst)} · SGST ${formatCurrency(i.sgst)} · IGST ${formatCurrency(i.igst)} · total ${formatCurrency(i.total)}${i.due_date ? ` · due ${formatDate(i.due_date)}` : ""}<br>Supplier GSTIN ${this.esc(i.supplier_gstin || "none")} · our GSTIN ${this.esc(i.buyer_gstin || "none")}${i.irn ? ` · IRN ${this.esc(i.irn.slice(0, 16))}…` : ""}</div>
        ${problems.filter((m) => m.line_no === null && m.detail).map((m) => `<div class="cell-muted" style="font-size:12px;margin-bottom:6px;">${this.esc(m.detail)}</div>`).join("")}
        ${i.can_act ? `<div style="display:flex;gap:8px;flex-wrap:wrap;">
            <div class="btn btn-secondary btn-sm" id="invExplain"><i class="ti ti-search"></i>Explain why and what to do</div>
            <div class="btn btn-secondary btn-sm" id="invRematch">Match again</div>
            <div class="btn btn-secondary btn-sm" id="invRelease">Ask to pay it anyway</div>
            <div class="btn btn-secondary btn-sm" id="invCancel">Cancel (a corrected invoice is coming)</div></div>
          <div id="invWhy" style="display:none;margin-top:10px;"><input id="invReason" placeholder="Why? (needed)" style="width:55%;padding:6px;"> <div class="btn btn-primary btn-sm" id="invWhyOk" style="display:inline-block;">Send</div></div>` : ""}
        <div id="invAdvice"></div>
        <div id="invNote" style="font-size:12.5px;color:var(--warning-text);margin-top:8px;"></div>`;
      const note = document.getElementById("invNote");
      const FIX = { credit_note: "Ask the supplier for a credit note", corrected_invoice: "Ask the supplier for a corrected invoice", wait_for_receipt: "Wait for the goods receipt, then match again", request_release: "Ask for a release" };
      const showAdvice = (d) => {
        if (!d) return;
        document.getElementById("invAdvice").innerHTML = `<div class="panel" style="margin-top:12px;font-size:12.5px;">
          <div class="panel-title">What to do</div>
          <p style="margin:0 0 8px;">${this.esc(d.explanation)}</p>
          <p style="margin:0 0 8px;"><strong>${this.esc(FIX[d.fix] || d.fix)}.</strong> Only a person can release the block; this is advice.</p>
          <div class="cell-muted" style="font-size:11.5px;margin-bottom:4px;">Draft message to the supplier (copy it into your email; nothing is sent):</div>
          <pre style="white-space:pre-wrap;font-size:12px;margin:0;">${this.esc((d.vendor_email || {}).body || d.vendor_body || "")}</pre></div>`;
      };
      if (i.status === "blocked") API.getJSON(`/api/invoices/${encodeURIComponent(id)}/diagnosis`).then((r) => showAdvice(r.diagnosis)).catch(() => {});
      const post = async (verb, body) => {
        const res = await API.postJSON(`/api/invoices/${encodeURIComponent(id)}/${verb}`, body || {});
        if (res.ok) return this.renderDetail(container, id);
        note.textContent = res.data.detail || "That did not work.";
      };
      let verb = null;
      const ask = (v) => { verb = v; document.getElementById("invWhy").style.display = "block"; document.getElementById("invReason").focus(); };
      const on = (eid, fn) => { const el = document.getElementById(eid); if (el) el.addEventListener("click", fn); };
      on("invExplain", async () => {
        note.textContent = "Looking into it…";
        const res = await API.postJSON(`/api/invoices/${encodeURIComponent(id)}/explain`, {});
        note.textContent = res.ok ? "" : (res.data.detail || "That did not work.");
        if (res.ok) showAdvice(res.data);
      });
      on("invRematch", () => post("rematch"));
      on("invRelease", () => ask("release"));
      on("invCancel", () => ask("cancel"));
      on("invWhyOk", () => post(verb, { reason: document.getElementById("invReason").value }));
    } catch {
      container.innerHTML = `<div class="empty-state">Couldn't load this invoice.</div>`;
    }
  },
};
