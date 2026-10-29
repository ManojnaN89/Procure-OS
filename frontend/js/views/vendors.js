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
      <div class="table-wrap" id="tableWrap"><div class="empty-state">Loading\u2026</div></div>
    `;
    document.getElementById("newVendorBtn").addEventListener("click", () => {
      Prefill.set("Onboard a vendor called [name], email [email], category [category]");
      window.location.hash = "#/assistant";
    });

    try {
      const rows = await API.listVendors();
      const wrap = document.getElementById("tableWrap");
      if (!rows.length) {
        wrap.innerHTML = `<div class="empty-state">No vendors yet. Ask the assistant to onboard one.</div>`;
        return;
      }
      wrap.innerHTML = `
        <table class="data-table">
          <thead><tr><th>NAME</th><th>CATEGORY</th><th>RATING</th><th>TOTAL SPEND</th></tr></thead>
          <tbody>
            ${rows.map((v) => `
              <tr data-id="${v.id}">
                <td>${v.name}</td>
                <td class="cell-muted">${v.category || "\u2014"}</td>
                <td class="cell-muted">${v.rating ?? "\u2014"}</td>
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
      document.getElementById("tableWrap").innerHTML = `<div class="empty-state">Couldn't load vendors.</div>`;
    }
  },

  async renderDetail(container, id) {
    container.innerHTML = `<div class="page-header"><h1>Vendor #${id}</h1></div><div class="panel">Loading\u2026</div>`;
    try {
      const v = await API.getVendor(id);
      if (v.error) { container.innerHTML = `<div class="empty-state">${v.error}</div>`; return; }

      const risk = v.risk || {};
      const riskColor = { low: "var(--success-text)", medium: "var(--warning-text)", high: "var(--danger-text)" }[risk.risk_level] || "var(--text-secondary)";
      const riskBg = { low: "var(--success-bg)", medium: "var(--warning-bg)", high: "var(--danger-bg)" }[risk.risk_level] || "var(--surface-sunken)";

      container.innerHTML = `
        <div class="page-header">
          <a href="#/vendors" style="color:var(--text-muted);font-size:12px;text-decoration:none;">\u2190 Vendors</a>
          <h1 style="margin-top:6px;">
            ${v.name}
            ${v.under_review ? '<span class="badge badge-pending" style="font-size:11px;vertical-align:middle;">under review</span>' : ""}
            ${v.active === false ? '<span class="badge badge-rejected" style="font-size:11px;vertical-align:middle;">inactive</span>' : ""}
            ${v.verified ? '<span class="badge badge-approved" style="font-size:11px;vertical-align:middle;">verified</span>' : ""}
          </h1>
          <p>${v.category || "\u2014"} \u00b7 ${v.email || "no email on file"}</p>
        </div>
        <div class="stat-grid" style="grid-template-columns:repeat(4,1fr);margin-bottom:8px;">
          <div class="stat-card"><div class="stat-label">TOTAL SPEND</div><div class="stat-value" style="font-size:20px;">${formatCurrency(v.total_spend)}</div></div>
          <div class="stat-card"><div class="stat-label">PURCHASE ORDERS</div><div class="stat-value" style="font-size:20px;">${v.purchase_orders.length}</div></div>
          <div class="stat-card"><div class="stat-label">RFQS INVOLVED IN</div><div class="stat-value" style="font-size:20px;">${v.rfq_count}</div></div>
          <div class="stat-card">
            <div class="stat-label">RISK LEVEL</div>
            <div class="stat-value" style="font-size:20px;color:${riskColor};text-transform:capitalize;">
              <span style="background:${riskBg};color:${riskColor};font-size:12px;padding:2px 10px;border-radius:20px;">${risk.risk_level || "\u2014"}</span>
            </div>
          </div>
        </div>
        ${risk.risk_level ? `
          <div class="panel" style="margin-bottom:16px;">
            <div class="panel-title">Risk breakdown</div>
            <div style="display:flex;gap:24px;font-size:12.5px;color:var(--text-secondary);margin-bottom:8px;">
              <span>Late deliveries: <strong style="color:var(--text-primary)">${risk.late_delivery_rate_pct}%</strong></span>
              <span>Price volatility: <strong style="color:var(--text-primary)">${risk.price_volatility_pct}%</strong></span>
              <span>Flagged incidents: <strong style="color:var(--text-primary)">${risk.flagged_incidents}</strong></span>
            </div>
            <p style="color:var(--text-muted);font-size:11px;margin:0 0 12px;font-style:italic;">${risk.basis || ""}</p>
            <div class="btn btn-secondary" id="reviewToggleBtn" style="font-size:11.5px;padding:6px 12px;display:inline-block;">
              ${v.under_review ? "Remove from review" : "Flag for review"}
            </div>
          </div>
        ` : ""}
        <div class="panel" style="margin-bottom:16px;">
          <div class="panel-title">Vendor status</div>
          ${v.verification_note ? `<p style="color:var(--text-muted);font-size:11px;margin:0 0 10px;">Verification note: ${v.verification_note}</p>` : ""}
          <div style="display:flex;gap:8px;">
            <div class="btn btn-secondary" id="activeToggleBtn" style="font-size:11.5px;padding:6px 12px;">
              ${v.active === false ? "Reactivate" : "Deactivate"}
            </div>
            <div class="btn btn-secondary" id="verifyToggleBtn" style="font-size:11.5px;padding:6px 12px;">
              ${v.verified ? "Remove verified mark" : "Mark as verified"}
            </div>
          </div>
        </div>
        <div class="panel">
          <div class="panel-title">Purchase order history</div>
          ${v.purchase_orders.length ? v.purchase_orders.map((po) => `
            <div style="display:flex;justify-content:space-between;padding:9px 0;border-bottom:1px solid var(--border);font-size:12.5px;">
              <a href="#/purchase-orders/${po.id}" style="color:var(--text-secondary);text-decoration:none;">PO #${po.id} \u00b7 ${formatDate(po.created_at)}</a>
              <span>${formatCurrency(po.amount)} ${statusBadge(po.status)} ${po.flagged ? '<span class="badge badge-rejected" title="' + (po.flag_reason || "") + '">\u26a0 flagged</span>' : ""}</span>
            </div>
          `).join("") : `<div class="empty-state">No purchase orders yet.</div>`}
        </div>
      `;

      const reviewBtn = document.getElementById("reviewToggleBtn");
      if (reviewBtn) {
        reviewBtn.addEventListener("click", () => {
          Conversation.send(
            v.under_review
              ? `Remove vendor ${id} from review`
              : `Flag vendor ${id} for review`
          );
          if (typeof FloatingAssistant !== "undefined") FloatingAssistant.open();
        });
      }

      // Deactivate/Reactivate is a single unambiguous action — auto-send,
      // same pattern as Dismiss elsewhere in the app
      document.getElementById("activeToggleBtn").addEventListener("click", () => {
        Conversation.send(
          v.active === false
            ? `Reactivate vendor ${id}`
            : `Deactivate vendor ${id}, we're no longer working with them`
        );
        if (typeof FloatingAssistant !== "undefined") FloatingAssistant.open();
      });

      // Mark as Verified usually wants a note (tax ID, registration number)
      // — prefill, not auto-send, same pattern as contract renewal. Removing
      // an existing verified mark needs no extra detail, so that direction
      // auto-sends.
      document.getElementById("verifyToggleBtn").addEventListener("click", () => {
        if (v.verified) {
          Conversation.send(`Remove the verified mark from vendor ${id}`);
          if (typeof FloatingAssistant !== "undefined") FloatingAssistant.open();
        } else {
        Prefill.set(`Mark vendor ${id} as verified, tax ID is [tax ID or registration number]`);
          window.location.hash = "#/assistant";
        }
      });
    } catch {
      container.innerHTML = `<div class="empty-state">Couldn't load this vendor.</div>`;
    }
  },
};
