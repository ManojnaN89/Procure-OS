const PaymentsView = {
  async render(container) {
    container.innerHTML = `
      <div class="page-header">
        <h1>Payments</h1>
        <p>Every payment processed against a purchase order</p>
      </div>
      <div class="table-wrap" id="tableWrap"><div class="empty-state">Loading\u2026</div></div>
    `;
    try {
      const rows = await API.listPayments();
      const wrap = document.getElementById("tableWrap");
      if (!rows.length) {
        wrap.innerHTML = `<div class="empty-state">No payments yet.</div>`;
        return;
      }
      wrap.innerHTML = `
        <table class="data-table">
          <thead><tr><th>ID</th><th>VENDOR</th><th>PURCHASE ORDER</th><th>AMOUNT</th><th>STATUS</th><th>DATE</th></tr></thead>
          <tbody>
            ${rows.map((p) => `
              <tr data-po="${p.purchase_order_id}">
                <td class="cell-muted">#${p.id}</td>
                <td>${p.vendor ? p.vendor.name : "\u2014"}</td>
                <td class="cell-muted">PO #${p.purchase_order_id}</td>
                <td>${formatCurrency(p.amount)}</td>
                <td>${statusBadge(p.status)}</td>
                <td class="cell-muted">${formatDate(p.created_at)}</td>
              </tr>
            `).join("")}
          </tbody>
        </table>
      `;
      wrap.querySelectorAll("tr[data-po]").forEach((tr) => {
        tr.addEventListener("click", () => { window.location.hash = `#/purchase-orders/${tr.dataset.po}`; });
      });
    } catch {
      document.getElementById("tableWrap").innerHTML = `<div class="empty-state">Couldn't load payments.</div>`;
    }
  },
};
