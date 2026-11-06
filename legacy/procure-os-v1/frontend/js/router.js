/**
 * Minimal hash router. Each view module exposes render(container, params).
 * This keeps one shared shell (sidebar/topbar/floating assistant) instead
 * of duplicating that markup across separate HTML pages.
 */
const Router = {
  routes: {
    dashboard: Dashboard,
    auctions: AuctionsView,
    rfqs: RfqsView,
    requisitions: RequisitionsView,
    vendors: VendorsView,
    "purchase-orders": PurchaseOrdersView,
    payments: PaymentsView,
    contracts: ContractsView,
    assistant: AssistantPageView,
    trail: TrailPageView,
    help: GuideMeView,
    settings: SettingsView,
    "notification-history": NotificationHistoryView,
  },

  currentView: null,

  init() {
    window.addEventListener("hashchange", () => this.handle());
    this.handle();
  },

  go(route) {
    window.location.hash = `#/${route}`;
  },

  handle() {
    const hash = window.location.hash.replace(/^#\//, "") || "dashboard";
    const [route, id] = hash.split("/");
    const view = this.routes[route] || Dashboard;

    if (this.currentView && this.currentView.unmount) this.currentView.unmount();
    this.currentView = view;

    document.querySelectorAll(".nav-item").forEach((el) => {
      el.classList.toggle("active", el.dataset.route === route);
    });

    const container = document.getElementById("appContent");
    view.render(container, id ? { id } : {});
  },
};
