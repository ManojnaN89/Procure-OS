const PurchaseOrdersView = {
  async render(container, params) {
    if (params && params.id) return this.renderDetail(container, params.id);
    return this.renderList(container);
  },

  async renderList(container) {
    container.innerHTML = `
      <div class="page-header page-header-row">
        <div>
          <h1>Purchase orders</h1>
          <p>Every PO, whether raised from a requisition or created directly</p>
        </div>
        <div class="btn btn-primary" id="newPoBtn"><i class="ti ti-plus"></i>New purchase order</div>
      </div>
      <div class="table-wrap" id="tableWrap"><div class="empty-state">Loading\u2026</div></div>
    `;
    document.getElementById("newPoBtn").addEventListener("click", () => {
      Prefill.set("Create a purchase order for vendor [ID], items: [items], amount [amount]");
      window.location.hash = "#/assistant";
    });

    try {
      const rows = await API.listPurchaseOrders();
      const wrap = document.getElementById("tableWrap");
      if (!rows.length) {
        wrap.innerHTML = `<div class="empty-state">No purchase orders yet.</div>`;
        return;
      }
      wrap.innerHTML = `
        <table class="data-table">
          <thead><tr><th>ID</th><th>VENDOR</th><th>AMOUNT</th><th>STATUS</th><th>CREATED</th></tr></thead>
          <tbody>
            ${rows.map((po) => `
              <tr data-id="${po.id}">
                <td class="cell-muted">#${po.id}</td>
                <td>${po.vendor ? po.vendor.name : "\u2014"}</td>
                <td>${formatCurrency(po.amount)}</td>
                <td>${statusBadge(po.status)}</td>
                <td class="cell-muted">${formatDate(po.created_at)}</td>
              </tr>
            `).join("")}
          </tbody>
        </table>
      `;
      wrap.querySelectorAll("tr[data-id]").forEach((tr) => {
        tr.addEventListener("click", () => { window.location.hash = `#/purchase-orders/${tr.dataset.id}`; });
      });
    } catch {
      document.getElementById("tableWrap").innerHTML = `<div class="empty-state">Couldn't load purchase orders.</div>`;
    }
  },

  async renderDetail(container, id) {
    container.innerHTML = `<div class="page-header"><h1>Purchase order #${id}</h1></div><div class="panel">Loading\u2026</div>`;
    try {
      const po = await API.getPurchaseOrder(id);
      if (po.error) { container.innerHTML = `<div class="empty-state">${po.error}</div>`; return; }
      const canPay = po.status !== "paid";
      container.innerHTML = `
        <div class="page-header">
          <a href="#/purchase-orders" style="color:var(--text-muted);font-size:12px;text-decoration:none;">\u2190 Purchase orders</a>
          <h1 style="margin-top:6px;">PO #${po.id}</h1>
          <p>${po.vendor ? po.vendor.name : "Unknown vendor"} \u00b7 ${formatCurrency(po.amount)} \u00b7 ${statusBadge(po.status)}</p>
        </div>
        <div class="panel" style="margin-bottom:14px;">
          <div class="panel-title">Items</div>
          <p style="color:var(--text-secondary);font-size:12.5px;margin:0;">${(po.items || []).join(", ") || "\u2014"}</p>
        </div>
        <div class="panel">
          <div class="panel-row"><span class="panel-title">Payments</span>
            ${canPay ? `<div class="btn btn-primary btn-sm" id="payBtn"><i class="ti ti-credit-card"></i>Process payment</div>` : ""}
          </div>
          ${po.payments.length ? po.payments.map((p) => `
            <div style="display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid var(--border);font-size:12.5px;">
              <span style="color:var(--text-secondary)">${formatDate(p.created_at)}</span>
              <span>${formatCurrency(p.amount)} ${statusBadge(p.status)}</span>
            </div>
          `).join("") : `<div class="empty-state">No payments recorded yet.</div>`}
        </div>
      `;
      if (canPay) {
        document.getElementById("payBtn").addEventListener("click", () => {
          Conversation.send(`Process payment of ${po.amount} for purchase order ${po.id}`);
          window.location.hash = "#/assistant";
        });
      }
    } catch {
      container.innerHTML = `<div class="empty-state">Couldn't load this purchase order.</div>`;
    }
  },
};
