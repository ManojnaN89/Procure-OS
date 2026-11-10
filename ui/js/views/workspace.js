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
    to_invoice: "Enter invoice", blocked: "Fix", due: "Plan payment",
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
      const sections = (w.sections || []).filter((s) => s.count > 0).map((s) => {
        const verb = this.VERBS[s.key] || "Open";
        return `
        <div class="todo-group">
          <div class="todo-head">
            <h2>${this.esc(s.title)} <span class="todo-count">${this.esc(s.count)}</span></h2>
            <a class="btn btn-sm" href="${this.href(s.link)}">See all</a>
          </div>
          ${s.rows.map((r) => `<a class="todo-row" href="${this.href(r.link)}">
              <span>${this.esc(r.label)}${r.sub ? `<span class="sub"> · ${this.esc(r.sub)}</span>` : ""}</span>
              <span class="go">${this.esc(verb)} ›</span></a>`).join("")}
          ${s.count > s.rows.length ? `<div class="todo-more">and ${this.esc(s.count - s.rows.length)} more</div>` : ""}
        </div>`;
      }).join("");
      const role = (w.roles || [])[0];
      const next = this.NEXT[role];
      const empty = !sections && !alerts
        ? `<div class="next-up"><strong>Nothing needs you.</strong><br>${next ? `Next: ${this.esc(next.text)}.<br><a class="btn btn-primary" href="${this.href(next.link)}">${this.esc(next.label)}</a>` : "Everything in your area is up to date."}</div>`
        : "";
      container.innerHTML = `
        <h2 class="section-title">My work${(w.roles || []).length ? ` <span class="cell-muted" style="font-weight:400;">· ${this.esc(w.roles.join(", "))}</span>` : ""}</h2>
        ${alerts ? `<div class="todo-group"><div class="todo-head"><h2>Alerts <span class="todo-count">${this.esc((w.alerts || []).length)}</span></h2></div>${alerts}</div>` : ""}
        ${sections}${empty}`;
    } catch {
      if (document.body.contains(container)) container.innerHTML = `<h2 class="section-title">My work</h2><div class="next-up">Couldn't load your work. Reload the page; if it still fails, tell your administrator.</div>`;
    }
  },
};
