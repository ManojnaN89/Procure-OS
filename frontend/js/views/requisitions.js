const RequisitionsView = {
  async render(container, params) {
    if (params && params.id) return this.renderDetail(container, params.id);
    return this.renderList(container);
  },

  async renderList(container) {
    container.innerHTML = `
      <div class="page-header page-header-row">
        <div>
          <h1>Requisitions</h1>
          <p>Internal purchase requests and their approval status</p>
        </div>
        <div class="btn btn-primary" id="newReqBtn"><i class="ti ti-plus"></i>New requisition</div>
      </div>
      <div class="table-wrap" id="tableWrap"><div class="empty-state">Loading\u2026</div></div>
    `;
    document.getElementById("newReqBtn").addEventListener("click", () => {
      Prefill.set("Create a requisition for [item/description], amount [amount], requested by [name]");
      window.location.hash = "#/assistant";
    });

    try {
      const rows = await API.listRequisitions();
      const wrap = document.getElementById("tableWrap");
      if (!rows.length) {
        wrap.innerHTML = `<div class="empty-state">No requisitions yet. Ask the assistant to raise one.</div>`;
        return;
      }
      wrap.innerHTML = `
        <table class="data-table">
          <thead><tr><th>ID</th><th>TITLE</th><th>AMOUNT</th><th>REQUESTED BY</th><th>STATUS</th></tr></thead>
          <tbody>
            ${rows.map((r) => `
              <tr data-id="${r.id}">
                <td class="cell-muted">#${r.id}</td>
                <td>${r.title}</td>
                <td>${formatCurrency(r.amount)}</td>
                <td class="cell-muted">${r.requested_by}</td>
                <td>${statusBadge(r.status)}</td>
              </tr>
            `).join("")}
          </tbody>
        </table>
      `;
      wrap.querySelectorAll("tr[data-id]").forEach((tr) => {
        tr.addEventListener("click", () => { window.location.hash = `#/requisitions/${tr.dataset.id}`; });
      });
    } catch {
      document.getElementById("tableWrap").innerHTML = `<div class="empty-state">Couldn't load requisitions.</div>`;
    }
  },

  async renderDetail(container, id) {
    container.innerHTML = `<div class="page-header"><h1>Requisition #${id}</h1></div><div class="panel">Loading\u2026</div>`;
    try {
      const r = await API.getRequisition(id);
      if (r.error) { container.innerHTML = `<div class="empty-state">${r.error}</div>`; return; }
      const canDecide = r.status === "pending";
      container.innerHTML = `
        <div class="page-header">
          <a href="#/requisitions" style="color:var(--text-muted);font-size:12px;text-decoration:none;">\u2190 Requisitions</a>
          <h1 style="margin-top:6px;">${r.title}</h1>
          <p>${formatCurrency(r.amount)} \u00b7 requested by ${r.requested_by} \u00b7 ${statusBadge(r.status)}</p>
        </div>
        <div class="panel">
          <div class="panel-title">Details</div>
          <p style="color:var(--text-secondary);font-size:12.5px;margin:0 0 14px;">${r.description || "No description provided."}</p>
          ${r.purchase_order
            ? `<div style="font-size:12px;color:var(--text-secondary);">Linked purchase order: <a href="#/purchase-orders/${r.purchase_order.id}" style="color:var(--accent-tint)">#${r.purchase_order.id}</a> \u2014 ${statusBadge(r.purchase_order.status)}</div>`
            : canDecide
              ? `<div style="display:flex;gap:10px;" id="decideBtns">
                   <div class="btn btn-primary btn-sm" id="approveBtn"><i class="ti ti-check"></i>Approve</div>
                   <div class="btn btn-sm" id="rejectBtn"><i class="ti ti-x"></i>Reject</div>
                 </div>`
              : `<div style="font-size:12px;color:var(--text-muted);">No purchase order linked yet.</div>`
          }
        </div>
      `;
      if (canDecide) {
        // These fire the same Assistant/skill path a typed message would \u2014
        // not a separate write route. See Action Model in the plan.
        document.getElementById("approveBtn").addEventListener("click", () => {
          Conversation.send(`Approve requisition ${r.id}`);
          window.location.hash = "#/assistant";
        });
        document.getElementById("rejectBtn").addEventListener("click", () => {
          Conversation.send(`Reject requisition ${r.id}`);
          window.location.hash = "#/assistant";
        });
      }
    } catch {
      container.innerHTML = `<div class="empty-state">Couldn't load this requisition.</div>`;
    }
  },
};
