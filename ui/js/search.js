/**
 * Global search in the top bar. Read-only — searches vendors, RFQs,
 * auctions, requisitions, and purchase orders, and routes to the
 * matching detail page on click.
 */
const Search = {
  init() {
    this.input = document.getElementById("searchInput");
    this.results = document.getElementById("searchResults");
    let debounce;

    this.input.addEventListener("input", () => {
      clearTimeout(debounce);
      const q = this.input.value.trim();
      if (q.length < 2) { this.close(); return; }
      debounce = setTimeout(() => this.run(q), 200);
    });

    document.addEventListener("click", (e) => {
      if (!document.getElementById("searchBox").contains(e.target)) this.close();
    });
  },

  async run(q) {
    try {
      const rows = await API.search(q);
      this.render(rows);
    } catch {
      this.close();
    }
  },

  render(rows) {
    if (!rows.length) {
      this.results.innerHTML = `<div class="search-result-item"><span>No matches</span></div>`;
      this.results.classList.add("open");
      return;
    }
    const routeFor = {
      vendor: "vendors", rfq: "rfqs", auction: "auctions",
      requisition: "requisitions", purchase_order: "purchase-orders",
    };
    this.results.innerHTML = "";
    rows.forEach((r) => {
      const item = document.createElement("div");
      item.className = "search-result-item";
      item.innerHTML = `<span>${r.label}</span><span class="type-badge">${r.type.replace("_", " ")}</span>`;
      item.addEventListener("click", () => {
        window.location.hash = `#/${routeFor[r.type]}/${r.id}`;
        this.close();
        this.input.value = "";
      });
      this.results.appendChild(item);
    });
    this.results.classList.add("open");
  },

  close() {
    this.results.classList.remove("open");
  },
};
