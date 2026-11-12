const WorkspaceView = {
  esc(value) {
    return String(value ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  },

  SEVERITY: { high: "badge-rejected", medium: "badge-pending", low: "badge-approved" },

  // What the person does with each kind of item: one plain verb per row.
  VERBS: {
    drafts: "Submit", returned: "Change", requests: "Review", orders: "Review", payment_runs: "Approve", releases: "Review",
    suppliers: "Review", variances: "Decide", rfq_sends: "Approve", to_source: "Find suppliers", to_send: "Send",
    to_acknowledge: "Record", supplier_messages: "Read", to_receive: "Receive", inspections: "Inspect",
    to_invoice: "Enter invoice", blocked: "Fix", due: "Plan payment", restock: "Raise",
  },

  // What to suggest when nothing is waiting: the usual first move of each role.
  NEXT: {
    Requester: { text: "raise a request for something you need", label: "New request", link: "#/requisitions/new" },
    Buyer: { text: "check which stock is at its reorder point", label: "Check stock levels", link: "#/requisitions" },
    Store: { text: "receive goods when an order arrives", label: "Open goods receipt", link: "#/receiving" },
    AP: { text: "enter a supplier invoice", label: "Open invoices", link: "#/invoices" },
    Finance: { text: "plan the next payment run", label: "Open payments", link: "#/payments" },
    Approver: { text: "look at where the purchases in progress stand", label: "Open the control tower", link: "#/control-tower" },
    Admin: { text: "review the people, approval chains and policies", label: "Open Admin", link: "/admin/" },
  },

  href(link) {
    return this.esc(link || "#");
  },

  // an amount sent as plain digits ("1,16,000.00" or "116,000.00") is shown the Indian way, with the rupee sign
  money(sub) {
    return /^\d[\d,]*\.\d{2}$/.test(sub) ? formatCurrency(Number(sub.replace(/,/g, ""))) : sub;
  },

  // The first thing to do: how much is waiting, and one button for the item at the top of the first list.
  startHere(w) {
    const open = (w.sections || []).filter((s) => s.count > 0 && s.rows.length);
    if (!open.length) return "";
    const total = open.reduce((n, s) => n + s.count, 0);
    const first = open[0];
    return `
      <div class="start-here">
        <div class="start-here-text">
          <div class="start-here-count">${this.esc(total)} thing${total === 1 ? "" : "s"} need${total === 1 ? "s" : ""} you</div>
          <div>Start with the first of <strong>${this.esc(first.title.charAt(0).toLowerCase() + first.title.slice(1))}</strong>, or pick any item below.</div>
        </div>
        <a class="btn btn-primary btn-lg" href="${this.href(first.rows[0].link)}">${this.esc(this.VERBS[first.key] || "Open")}<i class="ti ti-arrow-right" aria-hidden="true"></i></a>
      </div>`;
  },

  async mount(container) {
    if (!container) return;
    container.innerHTML = `<h2 class="section-title">My work</h2><div class="empty-state" style="padding:16px;">Loading…</div>`;
    try {
      const w = await API.getJSON("/api/workspace");
      if (!document.body.contains(container)) return; // the person has gone to another page
      const alerts = (w.alerts || []).map((a) => `
        <a class="todo-row" href="${this.href(a.link)}">
          <span><span class="badge ${this.SEVERITY[a.severity] || ""}">${this.esc(a.severity)}</span>
            <span style="margin-left:8px;">${this.esc(a.title)}</span>
            <div class="sub">${this.esc(a.detail)}</div></span>
          <span class="go">Open ›</span>
        </a>`).join("");
      const viewing = (w.roles || []).join() === "Admin" && !!w.flow; // the administrator only watches
      const sections = (w.sections || []).filter((s) => s.count > 0).map((s) => {
        const verb = viewing ? "View" : this.VERBS[s.key] || "Open";
        return `
        <div class="todo-group">
          <div class="todo-head">
            <h2>${this.esc(s.title)} <span class="todo-count">${this.esc(s.count)}</span></h2>
            <a class="btn btn-sm" href="${this.href(s.link)}">See all</a>
          </div>
          ${s.rows.map((r) => `<a class="todo-row" href="${this.href(r.link)}">
              <span>${this.esc(r.label)}${r.sub ? `<span class="sub"> · ${this.esc(this.money(r.sub))}</span>` : ""}</span>
              <span class="go">${this.esc(verb)} ›</span></a>`).join("")}
          ${s.count > s.rows.length ? `<div class="todo-more">and ${this.esc(s.count - s.rows.length)} more</div>` : ""}
        </div>`;
      }).join("");
      const role = (w.roles || [])[0];
      const next = this.NEXT[role];
      const start = viewing ? "" : this.startHere(w);
      const empty = !sections && !alerts
        ? `<div class="next-up"><strong>Nothing needs you.</strong><br>${next ? `Next: ${this.esc(next.text)}.<br><a class="btn btn-primary" href="${this.href(next.link)}">${this.esc(next.label)}</a>` : "Everything in your area is up to date."}</div>`
        : "";
      const strip = w.flow
        ? `<div class="flow-steps">${w.flow.map((f, i) => `<a class="flow-step${f.count ? " has" : ""}" href="${this.href(f.link)}"><span class="n">${this.esc(f.count)}</span><span class="t">${i + 1}. ${this.esc(f.title)}</span></a>`).join("")}</div>`
        : "";
      container.innerHTML = `
        ${strip}
        <h2 class="section-title">${viewing ? "Everything in the flow" : "My work"}${(w.roles || []).length ? ` <span class="cell-muted" style="font-weight:400;">· ${this.esc(w.roles.join(", "))}</span>` : ""}</h2>
        ${start}
        ${alerts ? `<div class="todo-group"><div class="todo-head"><h2>Alerts <span class="todo-count">${this.esc((w.alerts || []).length)}</span></h2></div>${alerts}</div>` : ""}
        ${sections}${empty}`;
    } catch {
      if (document.body.contains(container)) container.innerHTML = `<h2 class="section-title">My work</h2><div class="next-up">Couldn't load your work. Reload the page; if it still fails, tell your administrator.</div>`;
    }
  },
};
