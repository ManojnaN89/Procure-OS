const RfqsView = {
  async render(container, params) {
    if (params && params.id) return this.renderDetail(container, params.id);
    return this.renderList(container);
  },

  async renderList(container) {
    container.innerHTML = `
      <div class="page-header page-header-row">
        <div>
          <h1>RFQs</h1>
          <p>Requests for quotation across all vendors</p>
        </div>
        <div class="btn btn-primary" id="newRfqBtn"><i class="ti ti-plus"></i>New RFQ</div>
      </div>
      <div class="table-wrap" id="tableWrap"><div class="empty-state">Loading\u2026</div></div>
    `;
    document.getElementById("newRfqBtn").addEventListener("click", () => {
      Prefill.set("Draft an RFQ for [items] to vendors [vendor IDs]");
      window.location.hash = "#/assistant";
    });

    try {
      const rows = await API.listRfqs();
      const wrap = document.getElementById("tableWrap");
      if (!rows.length) {
        wrap.innerHTML = `<div class="empty-state">No RFQs yet. Ask the assistant to draft one.</div>`;
        return;
      }
      wrap.innerHTML = `
        <table class="data-table">
          <thead><tr><th>ID</th><th>TITLE</th><th>VENDORS</th><th>STATUS</th><th>QUOTES</th></tr></thead>
          <tbody>
            ${rows.map((r) => `
              <tr data-id="${r.id}">
                <td class="cell-muted">#${r.id}</td>
                <td>${r.title}</td>
                <td class="cell-muted">${r.vendor_count} vendors</td>
                <td>${statusBadge(r.status)}</td>
                <td class="cell-muted">${r.quote_count} of ${r.vendor_count}</td>
              </tr>
            `).join("")}
          </tbody>
        </table>
      `;
      wrap.querySelectorAll("tr[data-id]").forEach((tr) => {
        tr.addEventListener("click", () => { window.location.hash = `#/rfqs/${tr.dataset.id}`; });
      });
    } catch {
      document.getElementById("tableWrap").innerHTML = `<div class="empty-state">Couldn't load RFQs.</div>`;
    }
  },

  async renderDetail(container, id) {
    container.innerHTML = `<div class="page-header"><h1>RFQ #${id}</h1></div><div class="panel">Loading\u2026</div>`;
    try {
      const r = await API.getRfq(id);
      if (r.error) { container.innerHTML = `<div class="empty-state">${r.error}</div>`; return; }
      const cheapest = r.quotes.length ? Math.min(...r.quotes.map((q) => q.price)) : null;
      container.innerHTML = `
        <div class="page-header">
          <a href="#/rfqs" style="color:var(--text-muted);font-size:12px;text-decoration:none;">\u2190 RFQs</a>
          <h1 style="margin-top:6px;">${r.title}</h1>
          <p>${statusBadge(r.status)}</p>
        </div>
        <div class="panel">
          <div class="panel-title">Quote comparison \u2014 lowest first</div>
          ${r.quotes.length ? r.quotes.map((q) => `
            <div style="padding:9px 10px;margin-bottom:6px;border-radius:7px;background:${q.flagged ? "var(--danger-bg)" : q.price === cheapest ? "var(--success-bg)" : "transparent"};">
              <div style="display:flex;justify-content:space-between;align-items:center;">
                <span style="color:${q.flagged ? "var(--danger-text)" : q.price === cheapest ? "var(--success-text)" : "var(--text-secondary)"};font-size:12.5px;">
                  ${q.vendor ? q.vendor.name : "Vendor #" + q.vendor_id}
                  ${q.flagged ? '<span class="badge badge-rejected" style="margin-left:8px;">\u26a0 flagged</span>' : ""}
                </span>
                <span style="color:${q.flagged ? "var(--danger-text)" : q.price === cheapest ? "var(--success-text)" : "var(--text-primary)"};font-size:12.5px;font-weight:600;">${formatCurrency(q.price)} \u00b7 ${q.delivery_days} days</span>
              </div>
              ${q.flagged ? `
                <div style="margin-top:6px;font-size:11.5px;color:var(--danger-text);">${q.flag_reason || "Flagged as an outlier price"}</div>
                <div style="margin-top:8px;display:flex;gap:8px;">
                  <div class="btn btn-secondary rfq-accept-btn" data-quote-id="${q.id}" data-vendor-id="${q.vendor ? q.vendor.id : q.vendor_id}" data-price="${q.price}" style="font-size:11.5px;padding:6px 12px;">Accept anyway</div>
                  <div class="btn btn-secondary rfq-dismiss-btn" data-quote-id="${q.id}" style="font-size:11.5px;padding:6px 12px;">Dismiss flag</div>
                </div>
              ` : ""}
            </div>
          `).join("") : `<div class="empty-state">No quotes received yet.</div>`}
        </div>
      `;

      // Accept/Dismiss follow the same pattern as every other write button
      // in the app (Requisition page's Approve/Reject, etc.) — a pre-filled
      // prompt sent through the assistant, not a separate direct-write path
      container.querySelectorAll(".rfq-accept-btn").forEach((btn) => {
        btn.addEventListener("click", () => {
          Conversation.send(
            `Create a purchase order for vendor ${btn.dataset.vendorId}, items: ${r.items.join(", ")}, amount ${btn.dataset.price}, and dismiss the flagged-quote notification for RFQ ${id}`
          );
          if (typeof FloatingAssistant !== "undefined") FloatingAssistant.open();
        });
      });
      container.querySelectorAll(".rfq-dismiss-btn").forEach((btn) => {
        btn.addEventListener("click", () => {
          Conversation.send(`Dismiss the flagged-quote notification for RFQ ${id}`);
          if (typeof FloatingAssistant !== "undefined") FloatingAssistant.open();
        });
      });
    } catch {
      container.innerHTML = `<div class="empty-state">Couldn't load this RFQ.</div>`;
    }
  },
};
