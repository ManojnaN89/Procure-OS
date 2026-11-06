const RfqsView = {
  esc(value) {
    return String(value ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  },

  STATE_BADGE: { invited: "badge-approved", replied: "badge-approved", declined: "badge-rejected", failed: "badge-rejected", "not yet sent": "badge-pending" },

  async render(container, params) {
    if (params && params.id) return this.renderDetail(container, params.id);
    return this.renderList(container);
  },

  async renderList(container) {
    container.innerHTML = `
      <div class="page-header">
        <h1>RFQs</h1>
        <p>Requests for quotation sent to suppliers for approved requests</p>
      </div>
      <div class="table-wrap" id="tableWrap"><div class="empty-state">Loading…</div></div>
    `;
    try {
      const rows = await API.listRfqs();
      const wrap = document.getElementById("tableWrap");
      if (!wrap) return; // the person has already gone to another page
      if (!rows.length) {
        wrap.innerHTML = `<div class="empty-state">No quotation requests yet. They are made when an approved request has items with no contract price.</div>`;
        return;
      }
      wrap.innerHTML = `
        <table class="data-table">
          <thead><tr><th>NUMBER</th><th>TITLE</th><th>SUPPLIERS</th><th>REPLY BY</th><th>STATUS</th><th>QUOTES</th></tr></thead>
          <tbody>
            ${rows.map((r) => `
              <tr data-id="${this.esc(r.id)}">
                <td class="cell-muted" style="white-space:nowrap;">${this.esc(r.id)}</td>
                <td>${this.esc(r.title)}</td>
                <td class="cell-muted">${this.esc(r.vendor_count)} suppliers</td>
                <td class="cell-muted">${formatDate(r.due_date)}</td>
                <td>${statusBadge(r.status)}${r.waiting_to_send ? `<div class="cell-muted" style="font-size:11px;margin-top:3px;">waiting for a buyer to approve sending</div>` : ""}</td>
                <td class="cell-muted">${this.esc(r.quote_count)} of ${this.esc(r.vendor_count)}</td>
              </tr>
            `).join("")}
          </tbody>
        </table>
      `;
      wrap.querySelectorAll("tr[data-id]").forEach((tr) => {
        tr.addEventListener("click", () => { window.location.hash = `#/rfqs/${tr.dataset.id}`; });
      });
    } catch {
      const gone = document.getElementById("tableWrap");
      if (gone) gone.innerHTML = `<div class="empty-state">Couldn't load RFQs.</div>`;
    }
  },

  async renderDetail(container, id) {
    container.innerHTML = `<div class="page-header"><h1>${this.esc(id)}</h1></div><div class="panel">Loading…</div>`;
    try {
      const r = await API.getRfq(id);
      if (r.error) { container.innerHTML = `<div class="empty-state">${this.esc(r.error)}</div>`; return; }
      container.innerHTML = `
        <div class="page-header">
          <a href="#/rfqs" style="color:var(--text-muted);font-size:12px;text-decoration:none;">← RFQs</a>
          <h1 style="margin-top:6px;">${this.esc(r.title)}</h1>
          <p>${this.esc(r.id)} · ${statusBadge(r.status)} · for <a href="#/requisitions/${this.esc(r.request_no)}" style="color:var(--accent-tint)">${this.esc(r.request_no)}</a>
             · reply by ${formatDate(r.due_date)}${r.sent_at ? ` · sent ${formatDate(r.sent_at)}` : ""}</p>
        </div>
        ${r.status === "draft" && r.proposal_id ? `<div class="panel" style="margin-bottom:12px;font-size:12.5px;color:var(--text-secondary);">Not sent yet: a buyer has to approve sending (Admin → Approvals).</div>` : ""}
        ${r.note ? `<div class="panel" style="margin-bottom:12px;font-size:12.5px;color:var(--warning-text);">${this.esc(r.note)}</div>` : ""}
        <div class="table-wrap" style="margin-bottom:12px;">
          <table class="data-table">
            <thead><tr><th>SUPPLIER</th><th>EMAIL</th><th>STATE</th></tr></thead>
            <tbody>
              ${r.suppliers.map((s) => `
                <tr>
                  <td>${this.esc(s.name)}<div class="cell-muted" style="font-size:11px;">${this.esc(s.code)}</div></td>
                  <td class="cell-muted">${this.esc(s.email)}</td>
                  <td><span class="badge ${this.STATE_BADGE[s.state] || ""}">${this.esc(s.state)}</span>${s.error ? `<div class="cell-muted" style="font-size:11px;">${this.esc(s.error)}</div>` : ""}</td>
                </tr>`).join("")}
            </tbody>
          </table>
        </div>
        <div class="table-wrap" style="margin-bottom:12px;">
          <table class="data-table">
            <thead><tr><th>ITEM</th><th>QTY</th><th>NEEDED BY</th></tr></thead>
            <tbody>
              ${r.lines.map((l) => `
                <tr>
                  <td>${this.esc(l.name)}<div class="cell-muted" style="font-size:11px;">${this.esc(l.item_code || "free text")}</div></td>
                  <td>${this.esc(l.qty)} <span class="cell-muted">${this.esc(l.uom)}</span></td>
                  <td class="cell-muted">${formatDate(l.need_by)}</td>
                </tr>`).join("")}
            </tbody>
          </table>
        </div>
        <div class="panel">
          <div class="panel-title">Quotes</div>
          <div class="empty-state">${r.quotes.length ? "" : "No quotes received yet."}</div>
        </div>
      `;
    } catch {
      container.innerHTML = `<div class="empty-state">Couldn't load this RFQ.</div>`;
    }
  },
};
