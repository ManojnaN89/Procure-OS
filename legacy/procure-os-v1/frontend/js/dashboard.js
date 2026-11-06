/**
 * Polls/refreshes the dashboard cards from live Postgres-backed counts.
 */
const Dashboard = {
  async refresh() {
    try {
      const data = await API.getDashboard();
      document.getElementById("dashAuctions").textContent = data.open_auctions;
      document.getElementById("dashRfqs").textContent = data.open_rfqs;
      document.getElementById("dashRequisitions").textContent = data.pending_requisitions;
      document.getElementById("dashPOs").textContent = data.purchase_orders;
      document.getElementById("dashVendors").textContent = data.vendors;
    } catch {
      /* backend may not be up yet on first load; ignore and retry later */
    }
  },

  startPolling(intervalMs = 15000) {
    this.refresh();
    setInterval(() => this.refresh(), intervalMs);
  },
};
