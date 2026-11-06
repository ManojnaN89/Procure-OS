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
      <div class="table-wrap" id="tableWrap"><div class="empty-state">Loading\u2026</div></div>
    `;
    document.getElementById("newContractBtn").addEventListener("click", () => {
      Prefill.set("Store a contract for vendor [ID] titled [title], terms [terms], value [value], end date [YYYY-MM-DD]");
      window.location.hash = "#/assistant";
    });

    try {
      const rows = await API.listContracts();
      const wrap = document.getElementById("tableWrap");
      if (!rows.length) {
        wrap.innerHTML = `<div class="empty-state">No contracts on file yet.</div>`;
        return;
      }
      const now = new Date();
      wrap.innerHTML = `
        <table class="data-table">
          <thead><tr><th>ID</th><th>TITLE</th><th>VENDOR</th><th>VALUE</th><th>ENDS</th></tr></thead>
          <tbody>
            ${rows.map((c) => {
              const endsSoon = c.end_date && (new Date(c.end_date) - now) / 86400000 <= 30;
              return `
                <tr data-id="${c.id}">
                  <td class="cell-muted">#${c.id}</td>
                  <td>${c.title}</td>
                  <td>${c.vendor ? c.vendor.name : "\u2014"}</td>
                  <td>${formatCurrency(c.value)}</td>
                  <td>${c.end_date ? formatDate(c.end_date) : "\u2014"} ${endsSoon ? '<span class="badge badge-pending">expiring soon</span>' : ""}</td>
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
      document.getElementById("tableWrap").innerHTML = `<div class="empty-state">Couldn't load contracts.</div>`;
    }
  },

  async renderDetail(container, id) {
    container.innerHTML = `<div class="page-header"><h1>Contract #${id}</h1></div><div class="panel">Loading\u2026</div>`;
    try {
      const c = await API.getContract(id);
      if (c.error) { container.innerHTML = `<div class="empty-state">${c.error}</div>`; return; }
      container.innerHTML = `
        <div class="page-header">
          <a href="#/contracts" style="color:var(--text-muted);font-size:12px;text-decoration:none;">\u2190 Contracts</a>
          <h1 style="margin-top:6px;">${c.title}</h1>
          <p>${c.vendor ? c.vendor.name : "\u2014"} \u00b7 ${formatCurrency(c.value)}</p>
        </div>
        <div class="panel" style="margin-bottom:14px;">
          <div class="panel-title">Term</div>
          <p style="color:var(--text-secondary);font-size:12.5px;margin:0 0 10px;">
            ${c.start_date ? formatDate(c.start_date) : "\u2014"} \u2192 ${c.end_date ? formatDate(c.end_date) : "\u2014"}
          </p>
          <div class="btn btn-secondary" id="renewContractBtn" style="font-size:11.5px;padding:6px 12px;display:inline-block;">Renew contract</div>
        </div>
        <div class="panel">
          <div class="panel-title">Terms</div>
          <p style="color:var(--text-secondary);font-size:12.5px;margin:0;white-space:pre-wrap;">${c.terms || "No terms recorded."}</p>
        </div>
      `;

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
