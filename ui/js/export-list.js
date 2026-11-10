/**
 * "Export" on every list page: the list as a CSV file for a spreadsheet. The server makes the file (the same rows the screen
 * shows, as exact text); this only adds the button and saves the download with the login token.
 */
const ExportList = {
  NAMES: {
    requisitions: "requests",
    rfqs: "quotations",
    "purchase-orders": "orders",
    receiving: "receipts",
    invoices: "invoices",
    payments: "payment-runs",
    vendors: "vendors",
    contracts: "contracts",
    inventory: "stock",
  },

  /** Called after a page has drawn. Only a list page (no case id) gets the button, and only once. */
  attach(route, id) {
    const name = this.NAMES[route];
    const bar = document.querySelector("#appContent .page-header");
    if (!name || id || !bar || bar.querySelector("[data-export]")) return;
    const button = document.createElement("div");
    button.className = "btn btn-sm";
    button.dataset.export = name;
    button.setAttribute("role", "button");
    button.innerHTML = '<i class="ti ti-download"></i>Export';
    button.addEventListener("click", () => this.download(name, button));
    const host = bar.querySelector(".btn")?.parentElement ?? bar; // beside the page's own buttons, else under the title
    if (host === bar) button.style.cssText = "display:inline-flex;margin-top:8px;";
    host.appendChild(button);
  },

  async download(name, button) {
    button.classList.add("disabled");
    try {
      const res = await Auth.fetch(`/api/export/${encodeURIComponent(name)}`);
      if (!res.ok) throw new Error(String(res.status));
      const match = /filename="([^"]+)"/.exec(res.headers.get("content-disposition") || "");
      const link = document.createElement("a");
      link.href = URL.createObjectURL(await res.blob());
      link.download = match ? match[1] : `${name}.csv`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(link.href), 1000);
    } catch {
      button.textContent = "Export failed. Try again.";
      setTimeout(() => { button.innerHTML = '<i class="ti ti-download"></i>Export'; }, 3000);
    } finally {
      button.classList.remove("disabled");
    }
  },
};
