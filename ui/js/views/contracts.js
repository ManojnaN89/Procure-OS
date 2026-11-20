const ContractsView = {
  async render(container, params) {
    if (params && params.id) return this.renderDetail(container, params.id);
    return this.renderList(container);
  },

  async renderList(container) {
    container.innerHTML = `
      <div class="page-header page-header-row">
        <div>
          <h1>Contracts</h1>
          <p>Stored terms, value, and date range per vendor</p>
        </div>
        <div class="btn btn-primary" id="newContractBtn"><i class="ti ti-plus"></i>Store contract</div>
      </div>
      <div id="contractForm"></div>
      <div class="table-wrap" id="tableWrap"><div class="empty-state">Loading\u2026</div></div>
    `;
    document.getElementById("newContractBtn").addEventListener("click", async () => {
      const box = document.getElementById("contractForm");
      if (box.innerHTML) { box.innerHTML = ""; return; }
      let vendors = [];
      try { vendors = await API.listVendors(); } catch { box.textContent = "Couldn't load vendors."; return; }
      box.innerHTML = `<div class="panel" style="margin-bottom:14px;"><div class="panel-title">Store a contract (saved as a draft; an approver activates it)</div>
        <div style="display:flex;gap:8px;flex-wrap:wrap;">
          <select id="cSupplier" style="padding:6px;">${vendors.filter((v) => v.active).map((v) => `<option value="${escapeHtml(v.id)}">${escapeHtml(v.name)}</option>`).join("")}</select>
          <select id="cType" style="padding:6px;"><option value="rate_contract">Rate contract</option><option value="framework">Framework agreement</option><option value="service_contract">Service contract</option></select>
          <input id="cFrom" type="date" title="Valid from" style="padding:6px;"><input id="cTo" type="date" title="Valid to" style="padding:6px;">
          <input id="cMax" type="number" min="0" step="any" placeholder="Ceiling before tax (optional)" style="padding:6px;width:220px;">
          <input id="cTerms" placeholder="Payment terms code, e.g. NET30" style="padding:6px;"><input id="cDelivery" placeholder="Delivery terms" style="padding:6px;">
          <div class="btn btn-primary btn-sm" id="cSave">Store the contract</div></div>
        <div id="cNote" style="font-size:12.5px;color:var(--warning-text);margin-top:6px;"></div></div>`;
      document.getElementById("cSave").addEventListener("click", async () => {
        const res = await API.postJSON("/api/contracts", {
          supplier_code: document.getElementById("cSupplier").value, type: document.getElementById("cType").value,
          valid_from: document.getElementById("cFrom").value, valid_to: document.getElementById("cTo").value,
          max_value: document.getElementById("cMax").value, payment_terms_code: document.getElementById("cTerms").value,
          delivery_terms: document.getElementById("cDelivery").value });
        if (res.ok) { window.location.hash = `#/contracts/${res.data.id}`; return; }
        document.getElementById("cNote").textContent = res.data.detail || "The contract was not stored.";
      });
    });

    try {
      const rows = await API.listContracts();
      const wrap = document.getElementById("tableWrap");
      if (!wrap) return; // the person has already gone to another page
      if (!rows.length) {
        wrap.innerHTML = `<div class="empty-state">No contracts on file yet.</div>`;
        return;
      }
      const now = new Date();
      wrap.innerHTML = `
        <table class="data-table">
          <thead><tr><th>CONTRACT</th><th>VENDOR</th><th>CEILING</th><th>USED</th><th>ENDS</th></tr></thead>
          <tbody>
            ${rows.map((c) => {
              const endsSoon = c.status === "active" && c.end_date && (new Date(c.end_date) - now) / 86400000 <= 30;
              const high = c.pct_used !== null && Number(c.pct_used) >= 80;
              return `
                <tr data-id="${escapeHtml(c.id)}">
                  <td>${escapeHtml(c.title)}${c.status !== "active" ? ` <span class="badge badge-pending">${escapeHtml(c.status)}</span>` : ""}</td>
                  <td>${c.vendor ? escapeHtml(c.vendor.name) : "—"}</td>
                  <td>${c.value ? formatCurrency(c.value) : "—"}</td>
                  <td>${formatCurrency(c.used)}${c.pct_used !== null ? ` <span class="badge ${high ? "badge-rejected" : "badge-approved"}">${escapeHtml(c.pct_used)}%</span>` : ""}</td>
                  <td>${c.end_date ? formatDate(c.end_date) : "—"} ${endsSoon ? '<span class="badge badge-pending">expiring soon</span>' : ""}</td>
                </tr>
              `;
            }).join("")}
          </tbody>
        </table>
      `;
      wrap.querySelectorAll("tr[data-id]").forEach((tr) => {
        tr.addEventListener("click", () => { window.location.hash = `#/contracts/${tr.dataset.id}`; });
      });
    } catch {
      const gone = document.getElementById("tableWrap");
      if (gone) gone.innerHTML = `<div class="empty-state">Couldn't load contracts.</div>`;
    }
  },

  async renderDetail(container, id) {
    container.innerHTML = `<div class="page-header"><h1>Contract ${escapeHtml(id)}</h1></div><div class="panel">Loading\u2026</div>`;
    try {
      const c = await API.getContract(id);
      if (c.error) { container.innerHTML = `<div class="empty-state">${escapeHtml(c.error)}</div>`; return; }
      container.innerHTML = `
        <div class="page-header">
          <a href="#/contracts" style="color:var(--text-muted);font-size:12px;text-decoration:none;">\u2190 Contracts</a>
          <h1 style="margin-top:6px;">${escapeHtml(c.title)}</h1>
          <p>${c.vendor ? c.vendor.name : "\u2014"} \u00b7 ${formatCurrency(c.value)}</p>
        </div>
        <div class="panel" style="margin-bottom:14px;">
          <div class="panel-title">Ordered against this contract</div>
          <p style="color:var(--text-secondary);font-size:12.5px;margin:0 0 10px;">
            ${formatCurrency(c.used)} before tax${c.value ? ` of ${formatCurrency(c.value)} (${escapeHtml(c.pct_used)}%), ${formatCurrency(c.remaining)} left` : ""}
          </p>
          ${(c.orders || []).length ? `<table class="data-table"><thead><tr><th>ORDER</th><th>STATUS</th><th>BEFORE TAX</th></tr></thead><tbody>${c.orders.map((o) => `<tr data-po="${escapeHtml(o.id)}"><td>${escapeHtml(o.id)}</td><td>${statusBadge(o.status)}</td><td>${formatCurrency(o.amount)}</td></tr>`).join("")}</tbody></table>` : `<p class="cell-muted" style="font-size:12px;margin:0;">No orders yet.</p>`}
        </div>
        <div class="panel" style="margin-bottom:14px;">
          <div class="panel-title">Term</div>
          <p style="color:var(--text-secondary);font-size:12.5px;margin:0 0 10px;">
            ${c.start_date ? formatDate(c.start_date) : "\u2014"} \u2192 ${c.end_date ? formatDate(c.end_date) : "\u2014"}
          </p>
          <div class="btn btn-secondary" id="renewContractBtn" style="font-size:11.5px;padding:6px 12px;display:inline-block;">Renew contract</div>
        </div>
        <div class="panel" style="margin-bottom:14px;">
          <div class="panel-title">Price list ${c.status !== "active" ? `<span class="badge badge-pending">${escapeHtml(c.status)}</span>` : ""}</div>
          ${(c.prices || []).length ? `<table class="data-table"><thead><tr><th>ITEM</th><th>UNIT</th><th>FROM QTY</th><th>PRICE</th><th>VALID</th><th>VERSION</th></tr></thead><tbody>${c.prices.map((p) => `<tr><td>${escapeHtml(p.name || p.item_code)}<div class="cell-muted" style="font-size:11px;">${escapeHtml(p.item_code)}</div></td><td>${escapeHtml(p.uom || "")}</td><td>${escapeHtml(p.min_qty)}</td><td>${formatCurrency(p.price)}</td><td class="cell-muted">${p.valid_from ? formatDate(p.valid_from) : "—"} → ${p.valid_to ? formatDate(p.valid_to) : "—"}</td><td>${escapeHtml(p.version)}${p.status === "draft" ? ' <span class="badge badge-pending">draft</span>' : ""}</td></tr>`).join("")}</tbody></table>` : `<p class="cell-muted" style="font-size:12px;margin:0;">No prices yet.</p>`}
          ${c.can_manage ? `<div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:10px;"><input id="pItem" placeholder="Item code" style="padding:5px;"><input id="pUom" placeholder="Unit" style="padding:5px;width:70px;"><input id="pPrice" type="number" min="0" step="any" placeholder="Price" style="padding:5px;width:110px;"><input id="pMin" type="number" min="0" step="any" placeholder="From qty" style="padding:5px;width:90px;"><input id="pFrom" type="date" title="Valid from (empty: the contract's start)" style="padding:5px;"><input id="pTo" type="date" title="Valid to (empty: the contract's end)" style="padding:5px;"><div class="btn btn-secondary btn-sm" id="pAdd">${c.status === "active" ? "Add a draft price" : "Add a price"}</div></div>${c.status === "active" ? `<p class="cell-muted" style="font-size:11.5px;margin:6px 0 0;">A new price is a draft of the next version. It is used only after someone else activates the version; from its first day it replaces the price in force.</p>` : ""}` : ""}
          ${c.version_waiting ? `<p style="font-size:12.5px;margin:10px 0 0;">Version ${escapeHtml(c.draft_version)} is waiting to be activated by someone other than who entered it.</p>` : ""}
          ${c.can_submit_version ? `<div class="btn btn-primary btn-sm" id="vSubmit" style="margin-top:10px;display:inline-block;">Send version ${escapeHtml(c.draft_version)} for activation</div>` : ""}
          ${c.can_activate_version ? `<div class="btn btn-primary btn-sm" id="vActivate" style="margin-top:10px;display:inline-block;">Activate version ${escapeHtml(c.draft_version)}</div> <div class="btn btn-secondary btn-sm" id="vReject" style="margin-top:10px;display:inline-block;">Reject</div>` : ""}
          ${c.can_manage && c.status === "active" && c.draft_version ? `<div class="btn btn-secondary btn-sm" id="vWithdraw" style="margin-top:10px;display:inline-block;">Withdraw the draft</div>` : ""}
          ${c.can_activate ? `<div class="btn btn-primary btn-sm" id="cActivate" style="margin-top:10px;display:inline-block;">Activate this contract</div>` : ""}
          <div id="cMsg" style="font-size:12.5px;color:var(--warning-text);margin-top:6px;"></div>
        </div>
        <div class="panel">
          <div class="panel-title">Terms</div>
          <p style="color:var(--text-secondary);font-size:12.5px;margin:0;white-space:pre-wrap;">${escapeHtml(c.terms || "No terms recorded.")}</p>
        </div>
      `;

      const msg = (t) => { const el = document.getElementById("cMsg"); if (el) el.textContent = t; };
      const pAdd = document.getElementById("pAdd");
      if (pAdd) pAdd.addEventListener("click", async () => {
        const res = await API.postJSON(`/api/contracts/${encodeURIComponent(id)}/prices`, { item_code: document.getElementById("pItem").value, uom: document.getElementById("pUom").value, price: document.getElementById("pPrice").value, min_qty: document.getElementById("pMin").value, valid_from: document.getElementById("pFrom").value, valid_to: document.getElementById("pTo").value });
        if (res.ok) return this.renderDetail(container, id);
        msg(res.data.detail || "The price was not added.");
      });
      const act = document.getElementById("cActivate");
      if (act) act.addEventListener("click", async () => {
        const res = await API.postJSON(`/api/contracts/${encodeURIComponent(id)}/activate`, {});
        if (res.ok) return this.renderDetail(container, id);
        msg(res.data.detail || "The contract was not activated.");
      });
      const versionAction = (elId, path, ask) => {
        const el = document.getElementById(elId);
        if (!el) return;
        el.addEventListener("click", async () => {
          const body = {};
          if (ask) {
            const reason = window.prompt(ask);
            if (!reason) return;
            body.reason = reason;
          }
          const res = await API.postJSON(`/api/contracts/${encodeURIComponent(id)}/versions/${path}`, body);
          if (res.ok) return this.renderDetail(container, id);
          msg(res.data.detail || "That did not work.");
        });
      };
      versionAction("vSubmit", "submit");
      versionAction("vActivate", "activate");
      versionAction("vReject", "reject", "Why is this version rejected?");
      versionAction("vWithdraw", "withdraw", "Why is the draft withdrawn?");

      // Prefill, not auto-send — renewal needs a new end date the user
      // has to actually supply, unlike a one-click action like Dismiss
      document.getElementById("renewContractBtn").addEventListener("click", () => {
        Prefill.set(`Renew contract ${id} with a new end date of [YYYY-MM-DD]`);
        window.location.hash = "#/assistant";
      });
    } catch {
      container.innerHTML = `<div class="empty-state">Couldn't load this contract.</div>`;
    }
  },
};
