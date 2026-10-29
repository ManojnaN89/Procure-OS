const AuctionsView = {
  async render(container, params) {
    if (params && params.id) return this.renderDetail(container, params.id);
    return this.renderList(container);
  },

  async renderList(container) {
    container.innerHTML = `
      <div class="page-header page-header-row">
        <div>
          <h1>Auctions</h1>
          <p>Forward and reverse auctions across all vendors</p>
        </div>
        <div class="btn btn-primary" id="newAuctionBtn"><i class="ti ti-plus"></i>New auction</div>
      </div>
      <div class="table-wrap" id="tableWrap"><div class="empty-state">Loading\u2026</div></div>
    `;
    document.getElementById("newAuctionBtn").addEventListener("click", () => {
      Prefill.set("Start a reverse auction called [title] for [items], vendors [vendor IDs]");
      window.location.hash = "#/assistant";
    });

    try {
      const rows = await API.listAuctions();
      const wrap = document.getElementById("tableWrap");
      if (!rows.length) {
        wrap.innerHTML = `<div class="empty-state">No auctions yet. Ask the assistant to start one.</div>`;
        return;
      }
      wrap.innerHTML = `
        <table class="data-table">
          <thead><tr><th>ID</th><th>TITLE</th><th>TYPE</th><th>STATUS</th><th>VENDORS</th></tr></thead>
          <tbody>
            ${rows.map((a) => `
              <tr data-id="${a.id}">
                <td class="cell-muted">#${a.id}</td>
                <td>${a.title}</td>
                <td style="text-transform:capitalize;color:var(--text-secondary)">${a.auction_type}</td>
                <td>${statusBadge(a.status)}</td>
                <td class="cell-muted">${(a.vendor_ids || []).length} vendors</td>
              </tr>
            `).join("")}
          </tbody>
        </table>
      `;
      wrap.querySelectorAll("tr[data-id]").forEach((tr) => {
        tr.addEventListener("click", () => { window.location.hash = `#/auctions/${tr.dataset.id}`; });
      });
    } catch {
      document.getElementById("tableWrap").innerHTML = `<div class="empty-state">Couldn't load auctions.</div>`;
    }
  },

  async renderDetail(container, id) {
    container.innerHTML = `<div class="page-header"><h1>Auction #${id}</h1></div><div class="panel">Loading\u2026</div>`;
    try {
      const a = await API.getAuction(id);
      if (a.error) { container.innerHTML = `<div class="empty-state">${a.error}</div>`; return; }
      container.innerHTML = `
        <div class="page-header">
          <a href="#/auctions" style="color:var(--text-muted);font-size:12px;text-decoration:none;">\u2190 Auctions</a>
          <h1 style="margin-top:6px;">${a.title}</h1>
          <p>${a.auction_type === "reverse" ? "Reverse" : "Forward"} auction \u00b7 ${statusBadge(a.status)}</p>
        </div>
        <div class="panel">
          <div class="panel-title">Bids ${a.winner_vendor_id ? `\u2014 winner: vendor #${a.winner_vendor_id}` : ""}</div>
          ${a.bids.length ? a.bids.map((b) => `
            <div style="display:flex;justify-content:space-between;align-items:center;padding:9px 0;border-bottom:1px solid var(--border);font-size:12.5px;">
              <span style="color:${b.vendor_id === a.winner_vendor_id ? "var(--success-text)" : "var(--text-secondary)"}">${b.vendor ? b.vendor.name : "Vendor #" + b.vendor_id}</span>
              <span style="color:var(--text-primary);font-weight:500;">${formatCurrency(b.amount)}</span>
            </div>
          `).join("") : `<div class="empty-state">No bids yet.</div>`}
        </div>
      `;
    } catch {
      container.innerHTML = `<div class="empty-state">Couldn't load this auction.</div>`;
    }
  },
};
