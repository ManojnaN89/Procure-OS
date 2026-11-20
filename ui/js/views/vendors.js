const VendorsView = {
  async render(container, params) {
    if (params && params.id) return this.renderDetail(container, params.id);
    return this.renderList(container);
  },

  async renderList(container) {
    container.innerHTML = `
      <div class="page-header page-header-row">
        <div>
          <h1>Vendors</h1>
          <p>Directory of onboarded suppliers</p>
        </div>
        <div class="btn btn-primary" id="newVendorBtn"><i class="ti ti-plus"></i>Onboard vendor</div>
      </div>
      <div id="vendorForm"></div>
      <div class="table-wrap" id="tableWrap"><div class="empty-state">Loading\u2026</div></div>
    `;
    document.getElementById("newVendorBtn").addEventListener("click", () => this.showOnboarding(document.getElementById("vendorForm")));

    try {
      const rows = await API.listVendors();
      const wrap = document.getElementById("tableWrap");
      if (!wrap) return; // the person has already gone to another page
      if (!rows.length) {
        wrap.innerHTML = `<div class="empty-next"><strong>No vendors yet.</strong><br>Next: click Onboard vendor and fill in the form. The supplier is saved as a draft until its documents are verified and a person activates it.</div>`;
        return;
      }
      wrap.innerHTML = `
        <table class="data-table">
          <thead><tr><th>NAME</th><th>SUPPLIES</th><th>ORDERED SO FAR</th></tr></thead>
          <tbody>
            ${rows.map((v) => `
              <tr data-id="${escapeHtml(v.id)}">
                <td>${escapeHtml(v.name)}</td>
                <td class="cell-muted">${escapeHtml(v.category || "\u2014")}</td>
                <td>${formatCurrency(v.total_spend)}</td>
              </tr>
            `).join("")}
          </tbody>
        </table>
      `;
      wrap.querySelectorAll("tr[data-id]").forEach((tr) => {
        tr.addEventListener("click", () => { window.location.hash = `#/vendors/${tr.dataset.id}`; });
      });
    } catch {
      const gone = document.getElementById("tableWrap");
      if (gone) gone.innerHTML = `<div class="empty-state">Couldn't load vendors.</div>`;
    }
  },

  // The fields of a supplier record, in the order the form shows them: [key, label, kind, hint]
  FIELDS: [
    ["legal_name", "Legal name", "text", ""], ["trade_name", "Trade name", "text", "if different"],
    ["legal_form", "Legal form", "legal_form", ""], ["pan", "PAN", "text", "ABCDE1234F"],
    ["gst_reg_type", "GST registration", "gst", ""], ["gstin", "GSTIN", "text", "15 characters"],
    ["msme_category", "MSME category", "msme", ""], ["udyam_no", "Udyam number", "text", "UDYAM-MH-00-1234567"],
    ["payment_terms_code", "Payment terms", "terms", ""], ["agreed_payment_days", "Agreed payment days", "number", "MSME written agreement"],
    ["contact_name", "Contact person", "text", ""], ["email", "Email", "text", "sales@supplier.example"],
    ["phone", "Phone", "text", "+91 \u2026"], ["address", "Address", "text", ""],
    ["city", "City", "text", ""], ["pincode", "Pincode", "text", ""],
  ],
  CHOICES: {
    legal_form: [["", "\u2014"], ["proprietorship", "Proprietorship"], ["partnership", "Partnership"], ["llp", "LLP"], ["pvt_ltd", "Private limited"], ["public_ltd", "Public limited"], ["other", "Other"]],
    gst: [["regular", "Regular"], ["composition", "Composition"], ["unregistered", "Not registered"]],
    msme: [["none", "Not an MSME"], ["micro", "Micro"], ["small", "Small"], ["medium", "Medium"]],
  },

  fieldInput(key, kind, hint, value, terms, disabled) {
    const v = value === null || value === undefined ? "" : String(value);
    const dis = disabled ? " disabled" : "";
    if (kind === "terms") {
      return `<select data-f="${key}"${dis}><option value="">\u2014</option>${terms.map((t) => `<option value="${escapeHtml(t.code)}"${t.code === v ? " selected" : ""}>${escapeHtml(t.name)} (${escapeHtml(t.code)})</option>`).join("")}</select>`;
    }
    if (this.CHOICES[kind]) {
      return `<select data-f="${key}"${dis}>${this.CHOICES[kind].map(([val, label]) => `<option value="${val}"${val === v ? " selected" : ""}>${label}</option>`).join("")}</select>`;
    }
    return `<input data-f="${key}" ${kind === "number" ? 'type="number" min="0" max="365"' : ""} value="${escapeHtml(v)}" placeholder="${escapeHtml(hint)}"${dis}>`;
  },

  collect(box) {
    const out = {};
    box.querySelectorAll("[data-f]").forEach((el) => { if (!el.disabled) out[el.dataset.f] = el.value.trim(); });
    return out;
  },

  async showOnboarding(box) {
    if (!box) return;
    if (box.innerHTML) { box.innerHTML = ""; return; }
    let terms = [];
    try { terms = await API.getJSON("/api/payment-terms"); } catch { /* the list stays empty */ }
    box.innerHTML = `<div class="panel" style="margin-bottom:14px;">
      <div class="panel-title">Onboard a vendor (saved as a draft)</div>
      <div id="vendorFormFields" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px;">
        ${this.FIELDS.map(([key, label, kind, hint]) => `<label style="font-size:12px;color:var(--text-secondary);">${label}${this.fieldInput(key, kind, hint, key === "gst_reg_type" ? "regular" : key === "msme_category" ? "none" : "", terms, false)}</label>`).join("")}
        <label style="font-size:12px;color:var(--text-secondary);">Bank account number<input data-f="account_no" placeholder="9 to 18 digits"></label>
        <label style="font-size:12px;color:var(--text-secondary);">IFSC<input data-f="ifsc" placeholder="HDFC0001234"></label>
      </div>
      <div style="margin-top:12px;display:flex;gap:8px;align-items:center;">
        <div class="btn btn-primary btn-sm" id="vendorSave">Save as draft</div>
        <div class="btn btn-secondary btn-sm" id="vendorCancel">Cancel</div>
        <span id="vendorNote" style="font-size:12.5px;color:var(--warning-text);"></span>
      </div>
      <div class="cell-muted" style="font-size:11px;margin-top:6px;">After saving you add the documents (PAN, bank proof, GST and Udyam certificates). A person other than you verifies them, and another person activates the supplier.</div>
    </div>`;
    document.getElementById("vendorCancel").addEventListener("click", () => { box.innerHTML = ""; });
    document.getElementById("vendorSave").addEventListener("click", async () => {
      const res = await API.postJSON("/api/vendors", this.collect(box));
      if (res.ok) { window.location.hash = `#/vendors/${res.data.id}`; return; }
      document.getElementById("vendorNote").textContent = res.data.detail || "The vendor was not saved.";
    });
  },

  async showDetails(box, id, p, terms, reload) {
    const frozen = new Set(p.frozen_fields || []);
    const f = p.fields;
    const rows = this.FIELDS.map(([key, label, kind, hint]) => `<label style="font-size:12px;color:var(--text-secondary);">${label}${this.fieldInput(key, kind, hint, f[key], terms, !p.can_edit || frozen.has(key))}</label>`).join("");
    const how = (m) => String(m || "").replace(/_/g, " ");
    const checked = p.bank && p.bank.verified ? `verified${p.bank.method ? ` by ${escapeHtml(how(p.bank.method))}` : ""}${p.bank.verified_at ? ` on ${escapeHtml(p.bank.verified_at.slice(0, 10))}` : ""}` : "not verified: nothing is paid into it until a second person verifies it";
    const bank = p.bank ? `${escapeHtml(p.bank.account)} \u00b7 ${escapeHtml(p.bank.ifsc)} \u00b7 ${checked}` : "no bank account yet";
    const verify = p.can_verify_bank ? `<div style="margin-top:8px;display:flex;gap:6px;flex-wrap:wrap;align-items:center;font-size:12.5px;">
        Check account <strong>${escapeHtml(p.bank.account_no)}</strong>, IFSC ${escapeHtml(p.bank.ifsc)} against the proof:
        <select id="bankMethod" style="padding:5px;">${p.bank_methods.map((m) => `<option value="${escapeHtml(m)}">${escapeHtml(how(m))}</option>`).join("")}</select>
        <input type="file" id="bankProof" accept=".pdf,.png,.jpg,.jpeg">
        <div class="btn btn-primary btn-sm" id="bankVerify">Verify bank account</div><span id="bankVerifyNote" style="font-size:12.5px;color:var(--warning-text);"></span></div>` : "";
    box.innerHTML = `<div class="panel-title">Details</div>
      <div id="detailFields" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px;">${rows}</div>
      ${frozen.size ? `<div class="cell-muted" style="font-size:11px;margin-top:6px;">PAN, GST and MSME details are locked while the supplier is in use: put it under review first.</div>` : ""}
      ${p.can_edit ? `<div style="margin-top:10px;display:flex;gap:8px;align-items:center;"><div class="btn btn-secondary btn-sm" id="detailSave">Save details</div><span id="detailNote" style="font-size:12.5px;color:var(--warning-text);"></span></div>` : ""}
      <div style="margin-top:14px;font-size:12.5px;"><strong>Bank account:</strong> ${bank}</div>
      ${verify}
      ${p.can_edit ? `<div style="margin-top:8px;display:flex;gap:6px;flex-wrap:wrap;align-items:center;">
        <input id="bankAccount" placeholder="New account number" style="padding:5px;"><input id="bankIfsc" placeholder="IFSC" style="padding:5px;width:130px;">
        ${p.status !== "draft" ? '<input id="bankReason" placeholder="Why? (needed)" style="padding:5px;">' : ""}
        <div class="btn btn-secondary btn-sm" id="bankSave">${p.status === "draft" ? "Set bank account" : "Ask to change the bank account"}</div><span id="bankNote" style="font-size:12.5px;color:var(--warning-text);"></span></div>
        ${p.status !== "draft" ? '<div class="cell-muted" style="font-size:11px;margin-top:4px;">For a supplier in use a person other than you must approve the change (Admin \u2192 Approvals), and the bank proof is checked again.</div>' : ""}` : ""}`;
    const save = document.getElementById("detailSave");
    if (save) save.addEventListener("click", async () => {
      const res = await API.putJSON(`/api/vendors/${encodeURIComponent(id)}`, this.collect(document.getElementById("detailFields")));
      document.getElementById("detailNote").textContent = res.ok ? (res.data.changed.length ? "Saved." : "Nothing changed.") : (res.data.detail || "Not saved.");
    });
    const bankVerify = document.getElementById("bankVerify");
    if (bankVerify) bankVerify.addEventListener("click", async () => {
      const note = document.getElementById("bankVerifyNote");
      const proof = document.getElementById("bankProof");
      if (!proof.files.length) { note.textContent = "Attach the proof the account was checked against."; return; }
      const form = new FormData();
      form.append("method", document.getElementById("bankMethod").value);
      form.append("file", proof.files[0]);
      const res = await Auth.fetch(`/api/vendors/${encodeURIComponent(id)}/bank/${p.bank.id}/verify`, { method: "POST", body: form });
      if (res.ok) return reload();
      let detail = "Not verified.";
      try { detail = (await res.json()).detail || detail; } catch {}
      note.textContent = detail;
    });
    const bankSave = document.getElementById("bankSave");
    if (bankSave) bankSave.addEventListener("click", async () => {
      const reason = document.getElementById("bankReason");
      const res = await API.postJSON(`/api/vendors/${encodeURIComponent(id)}/bank`, { account_no: document.getElementById("bankAccount").value.trim(), ifsc: document.getElementById("bankIfsc").value.trim(), reason: reason ? reason.value.trim() : "" });
      const note = document.getElementById("bankNote");
      if (!res.ok) { note.textContent = res.data.detail || "Not changed."; return; }
      if (res.data.proposal) { note.textContent = `Sent for approval (proposal ${res.data.proposal}). The account does not change until a person approves it.`; return; }
      return reload();
    });
  },

  async openFile(fileId, name) {
    const res = await Auth.fetch(`/api/files/${fileId}`);
    if (!res.ok) return;
    const url = URL.createObjectURL(await res.blob());
    const a = document.createElement("a");
    a.href = url; a.download = name; a.click();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
  },

  async renderDetail(container, id) {
    container.innerHTML = `<div class="page-header"><h1>Vendor #${escapeHtml(id)}</h1></div><div class="panel">Loading\u2026</div>`;
    try {
      const v = await API.getVendor(id);
      if (v.error) { container.innerHTML = `<div class="empty-state">${escapeHtml(v.error)}</div>`; return; }
      let profile = null;
      let terms = [];
      try { [profile, terms] = await Promise.all([API.getJSON(`/api/vendors/${encodeURIComponent(id)}/profile`), API.getJSON("/api/payment-terms")]); } catch { /* the details panel is left out */ }

      const risk = v.risk || {};
      const riskColor = { low: "var(--success-text)", medium: "var(--warning-text)", high: "var(--danger-text)" }[risk.risk_level] || "var(--text-secondary)";
      const riskBg = { low: "var(--success-bg)", medium: "var(--warning-bg)", high: "var(--danger-bg)" }[risk.risk_level] || "var(--surface-sunken)";

      const files = (profile && profile.files) || {};
      const scanCell = (d) => {
        const f = files[String(d.id)];
        if (f) return `<a href="#" class="doc-open" data-file="${f.id}" data-name="${escapeHtml(f.filename)}">${escapeHtml(f.filename)}</a>`;
        return v.can_add_documents ? `<label class="btn btn-secondary btn-sm" style="padding:2px 8px;font-size:11px;cursor:pointer;">Attach<input type="file" class="doc-attach" data-id="${d.id}" accept=".pdf,.png,.jpg,.jpeg" style="display:none;"></label>` : "—";
      };
      container.innerHTML = `
        <div class="page-header">
          <a href="#/vendors" style="color:var(--text-muted);font-size:12px;text-decoration:none;">\u2190 Vendors</a>
          <h1 style="margin-top:6px;">
            ${escapeHtml(v.name)}
            ${v.under_review ? '<span class="badge badge-pending" style="font-size:11px;vertical-align:middle;">under review</span>' : ""}
            ${v.active === false ? '<span class="badge badge-rejected" style="font-size:11px;vertical-align:middle;">inactive</span>' : ""}
            ${v.verified ? '<span class="badge badge-approved" style="font-size:11px;vertical-align:middle;">verified</span>' : ""}
          </h1>
          <p>${escapeHtml(v.category || "\u2014")} \u00b7 ${escapeHtml(v.email || "no email on file")}</p>
        </div>
        <div class="stat-grid" style="grid-template-columns:repeat(4,1fr);margin-bottom:8px;">
          <div class="stat-card"><div class="stat-label">ORDERED SO FAR</div><div class="stat-value" style="font-size:20px;">${formatCurrency(v.total_spend)}</div></div>
          <div class="stat-card"><div class="stat-label">PURCHASE ORDERS</div><div class="stat-value" style="font-size:20px;">${v.purchase_orders.length}</div></div>
          <div class="stat-card"><div class="stat-label">RFQS INVOLVED IN</div><div class="stat-value" style="font-size:20px;">${v.rfq_count}</div></div>
          <div class="stat-card">
            <div class="stat-label">RISK LEVEL</div>
            <div class="stat-value" style="font-size:20px;color:${riskColor};text-transform:capitalize;">
              <span style="background:${riskBg};color:${riskColor};font-size:12px;padding:2px 10px;border-radius:20px;">${escapeHtml(risk.risk_level || "\u2014")}</span>
            </div>
          </div>
        </div>
        ${risk.risk_level ? `
          <div class="panel" style="margin-bottom:16px;">
            <div class="panel-title">Risk breakdown</div>
            <div style="display:flex;gap:24px;font-size:12.5px;color:var(--text-secondary);margin-bottom:8px;">
              <span>Late deliveries: <strong style="color:var(--text-primary)">${risk.late_delivery_rate_pct}%</strong></span>
              <span>Above the lowest price: <strong style="color:var(--text-primary)">${risk.price_volatility_pct}%</strong></span>
              <span>Flagged incidents: <strong style="color:var(--text-primary)">${risk.flagged_incidents}</strong></span>
            </div>
            <p style="color:var(--text-muted);font-size:11px;margin:0 0 12px;font-style:italic;">${escapeHtml(risk.basis || "")}</p>
          </div>
        ` : ""}
        ${v.score ? `
          <div class="panel" style="margin-bottom:16px;">
            <div class="panel-title">Score ${escapeHtml(v.score.score)} of 100</div>
            <div style="font-size:12.5px;color:var(--text-secondary);display:grid;grid-template-columns:repeat(3,1fr);gap:10px;">
              <div>Price <strong style="color:var(--text-primary)">${escapeHtml(v.score.price)}</strong> <span class="cell-muted">(weight ${escapeHtml(v.score.weights.price)})</span><div class="cell-muted" style="font-size:11px;">${escapeHtml(plural(v.score.price_items_compared, "item"))} compared; ${escapeHtml(v.score.price_gap_pct)}% above the lowest on average</div></div>
              <div>Delivery <strong style="color:var(--text-primary)">${escapeHtml(v.score.delivery)}</strong> <span class="cell-muted">(weight ${escapeHtml(v.score.weights.delivery)})</span><div class="cell-muted" style="font-size:11px;">${escapeHtml(v.score.on_time)} of ${escapeHtml(v.score.receipts_judged)} deliveries on time${v.score.on_time_pct ? ` (${escapeHtml(v.score.on_time_pct)}%)` : ""}; ${escapeHtml(v.score.acknowledged_quickly)} of ${escapeHtml(v.score.acknowledged)} orders acknowledged quickly</div></div>
              <div>Quality <strong style="color:var(--text-primary)">${escapeHtml(v.score.quality)}</strong> <span class="cell-muted">(weight ${escapeHtml(v.score.weights.quality)})</span><div class="cell-muted" style="font-size:11px;">${v.score.accepted_pct ? `${escapeHtml(v.score.accepted_pct)}% of what was delivered was accepted` : "no deliveries yet"}; ${escapeHtml(v.score.rejected_qty)} rejected of ${escapeHtml(v.score.received_qty)} received</div></div>
            </div>
          </div>` : ""}
        ${profile ? '<div class="panel" id="detailsPanel" style="margin-bottom:16px;"></div>' : ""}
        ${v.documents ? `
          <div class="panel" style="margin-bottom:16px;">
            <div class="panel-title">Documents ${v.documents.ok ? '<span class="badge badge-approved" style="font-size:10px;">complete</span>' : '<span class="badge badge-rejected" style="font-size:10px;">cannot be used until fixed</span>'}</div>
            ${v.documents.problems.map((p) => `<div style="font-size:12.5px;color:var(--danger-text);margin-bottom:4px;">${escapeHtml(p)}</div>`).join("")}
            ${v.documents.warnings.map((p) => `<div style="font-size:12.5px;color:var(--warning-text);margin-bottom:4px;">${escapeHtml(p)}</div>`).join("")}
            ${v.documents.on_file.length ? `<table class="data-table" style="margin-top:8px;"><thead><tr><th>DOCUMENT</th><th>NUMBER</th><th>EXPIRES</th><th>SCAN</th><th>CHECKED</th></tr></thead><tbody>${v.documents.on_file.map((d) => `<tr><td>${escapeHtml(d.type)}</td><td class="cell-muted">${escapeHtml(d.number || "—")}</td><td>${d.expires_on ? formatDate(d.expires_on) : "—"}${d.expired ? ' <span class="badge badge-rejected">expired</span>' : ""}</td><td>${scanCell(d)}</td><td>${d.verified ? "yes" : (v.can_verify_documents ? `<span class="btn btn-secondary btn-sm doc-verify" data-id="${d.id}" style="padding:2px 8px;font-size:11px;">Verify</span>` : "no")}</td></tr>`).join("")}</tbody></table>` : '<p class="cell-muted" style="font-size:12px;margin:0;">No documents on file.</p>'}
            ${v.can_add_documents ? `<div style="margin-top:10px;display:flex;gap:6px;flex-wrap:wrap;align-items:center;">
              <select id="docType" style="padding:5px;"><option value="pan">PAN card</option><option value="gst_cert">GST certificate</option><option value="udyam">Udyam certificate</option><option value="cancelled_cheque">Bank proof</option><option value="msme_agreement">MSME agreement</option><option value="iso">ISO certificate</option><option value="licence">Licence</option><option value="insurance">Insurance</option></select>
              <input id="docNumber" placeholder="Number" style="padding:5px;"><input id="docExpires" type="date" title="Expires on" style="padding:5px;">
              <span class="btn btn-secondary btn-sm" id="docAdd">Add a document</span><span id="docNote" style="font-size:12px;color:var(--warning-text);"></span></div>
              <div class="cell-muted" style="font-size:11px;margin-top:4px;">A document counts once a person other than the one who added it has checked it against the original and verified it.</div>` : ""}
          </div>` : ""}
        <div class="panel" style="margin-bottom:16px;">
          <div class="panel-title">Vendor status: ${escapeHtml(v.status || "")}</div>
          ${v.verification_note ? `<p style="color:var(--text-muted);font-size:11px;margin:0 0 10px;">Verification note: ${escapeHtml(v.verification_note)}</p>` : ""}
          ${v.pending_change ? `<p style="font-size:12.5px;color:var(--warning-text);margin:0 0 10px;">Waiting for approval: ${escapeHtml(v.pending_change.summary)} (approve it under Admin → Approvals).</p>` : ""}
          ${v.can_propose && !v.pending_change ? `
            <div style="display:flex;gap:8px;flex-wrap:wrap;">
              ${(({ draft: [["active", "Activate"]], active: [["blocked", "Block"], ["draft", "Put under review"]], blocked: [["active", "Reactivate"]] })[v.status] || []).map(([to, label]) => `<div class="btn btn-secondary vendor-status-btn" data-to="${escapeHtml(to)}" style="font-size:11.5px;padding:6px 12px;">${escapeHtml(label)}</div>`).join("")}
            </div>
            <div id="statusReasonBox" style="display:none;margin-top:10px;">
              <input id="statusReason" placeholder="Why? (needed)" style="width:60%;padding:6px 8px;font-size:12.5px;">
              <div class="btn btn-primary btn-sm" id="statusSend" style="display:inline-block;margin-left:6px;">Send for approval</div>
              <div class="btn btn-secondary btn-sm" id="statusCancel" style="display:inline-block;margin-left:4px;">Cancel</div>
            </div>
            <p id="statusMsg" style="font-size:12px;margin:8px 0 0;"></p>` : ""}
        </div>
          </div>
        </div>
        <div class="panel">
          <div class="panel-title">Purchase order history</div>
          ${v.purchase_orders.length ? v.purchase_orders.map((po) => `
            <div style="display:flex;justify-content:space-between;padding:9px 0;border-bottom:1px solid var(--border);font-size:12.5px;">
              <a href="#/purchase-orders/${escapeHtml(po.id)}" style="color:var(--text-secondary);text-decoration:none;">PO #${escapeHtml(po.id)} \u00b7 ${formatDate(po.created_at)}</a>
              <span>${formatCurrency(po.amount)} ${statusBadge(po.status)} ${po.flagged ? '<span class="badge badge-rejected" title="' + escapeHtml(po.flag_reason || "") + '">\u26a0 flagged</span>' : ""}</span>
            </div>
          `).join("") : `<div class="empty-state">No purchase orders yet.</div>`}
        </div>
      `;

      const detailsBox = document.getElementById("detailsPanel");
      if (detailsBox && profile) this.showDetails(detailsBox, id, profile, terms, () => this.renderDetail(container, id));
      container.querySelectorAll(".doc-open").forEach((a) => a.addEventListener("click", (e) => { e.preventDefault(); this.openFile(a.dataset.file, a.dataset.name); }));
      container.querySelectorAll(".doc-attach").forEach((input) => input.addEventListener("change", async () => {
        if (!input.files.length) return;
        const form = new FormData();
        form.append("file", input.files[0]);
        const res = await Auth.fetch(`/api/vendors/${encodeURIComponent(id)}/documents/${input.dataset.id}/file`, { method: "POST", body: form });
        if (res.ok) return this.renderDetail(container, id);
        let detail = "The scan was not attached.";
        try { detail = (await res.json()).detail || detail; } catch {}
        const note = document.getElementById("docNote"); if (note) note.textContent = detail;
      }));
      container.querySelectorAll(".doc-verify").forEach((b) => b.addEventListener("click", async () => {
        const res = await API.postJSON(`/api/vendors/${encodeURIComponent(id)}/documents/${b.dataset.id}/verify`, { verified: true });
        if (res.ok) return this.renderDetail(container, id);
        const note = document.getElementById("docNote"); if (note) note.textContent = res.data.detail || "Not verified.";
      }));
      const docAdd = document.getElementById("docAdd");
      if (docAdd) docAdd.addEventListener("click", async () => {
        const res = await API.postJSON(`/api/vendors/${encodeURIComponent(id)}/documents`, {
          type: document.getElementById("docType").value, number: document.getElementById("docNumber").value, expires_on: document.getElementById("docExpires").value });
        if (res.ok) return this.renderDetail(container, id);
        document.getElementById("docNote").textContent = res.data.detail || "Not added.";
      });
      let chosen = null;
      const box = document.getElementById("statusReasonBox");
      container.querySelectorAll(".vendor-status-btn").forEach((btn) => {
        btn.addEventListener("click", () => {
          chosen = btn.dataset.to;
          box.style.display = "block";
          document.getElementById("statusReason").focus();
        });
      });
      if (box) {
        document.getElementById("statusCancel").addEventListener("click", () => { box.style.display = "none"; });
        document.getElementById("statusSend").addEventListener("click", async () => {
          const msg = document.getElementById("statusMsg");
          const reason = document.getElementById("statusReason").value.trim();
          if (!reason) { msg.textContent = "A reason is needed."; return; }
          const res = await API.postJSON(`/api/vendors/${encodeURIComponent(id)}/status`, { to: chosen, reason });
          msg.textContent = res.ok
            ? `Sent for approval (proposal ${res.data.proposal_id}). Nothing changes until a person approves it.`
            : (res.data.detail || "Could not send this for approval.");
          if (res.ok) box.style.display = "none";
        });
      }
    } catch {
      container.innerHTML = `<div class="empty-state">Couldn't load this vendor.</div>`;
    }
  },
};
