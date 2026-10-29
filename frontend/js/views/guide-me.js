/**
 * Help — the one page in the app that isn't live operational data.
 * Explains how ProcureOS works, what every page actually does, both
 * background mechanisms (the "ask" path and the "watch" path), who should
 * use it how, and what the app-specific terms mean. Mostly static content;
 * pulls a small number of real counts so it doesn't read like generic
 * marketing copy sitting on an empty app.
 */
const GuideMeView = {
  async render(container) {
    container.innerHTML = `
      <div class="page-header">
        <h1>Help</h1>
        <p>How ProcureOS actually works, end to end \u2014 not a live page, just an explanation.</p>
      </div>

      <div class="panel" style="margin-bottom:20px;" id="guideIntro"></div>

      <div class="panel" style="margin-bottom:20px;">
        <div class="panel-title">How it works, end to end</div>
        <div id="guideFlow"></div>
      </div>

      <div class="panel" style="margin-bottom:20px;">
        <div class="panel-title">What each page does</div>
        <p style="color:var(--text-muted);font-size:11.5px;margin:0 0 16px;">Every item in the sidebar, and the tools in the top bar, in plain terms.</p>
        <div id="pageGuideList"></div>
      </div>

      <div class="panel" style="margin-bottom:20px;">
        <div class="panel-title">What's happening behind the scenes</div>
        <p style="color:var(--text-muted);font-size:11.5px;margin:0 0 16px;">Two separate mechanisms \u2014 one triggered by you, one triggered by nothing at all.</p>
        <div class="two-col" style="align-items:start;">
          <div>
            <div style="color:var(--accent-tint);font-size:12px;font-weight:600;margin-bottom:10px;">The "ask" path \u2014 when you type something</div>
            <div id="askDiagram"></div>
          </div>
          <div>
            <div style="color:var(--warning-text);font-size:12px;font-weight:600;margin-bottom:10px;">The "watch" path \u2014 running in the background</div>
            <div id="watchDiagram"></div>
          </div>
        </div>
      </div>

      <div class="panel" style="margin-bottom:20px;">
        <div class="panel-title">Get the most out of it, by role</div>
        <p style="color:var(--text-muted);font-size:11.5px;margin:0 0 16px;">
          ProcureOS doesn't have real login-based roles or permissions yet \u2014 everyone sees everything.
          These are use-case lenses to help you focus, not an access system.
        </p>
        <div id="roleCards" class="stat-grid" style="grid-template-columns:repeat(2,1fr);"></div>
      </div>

      <div class="panel">
        <div class="panel-title">Glossary</div>
        <div id="glossaryList"></div>
      </div>
    `;

    this.renderIntro();
    this.renderFlow();
    this.renderPageGuide();
    this.renderDiagram("askDiagram", [
      { title: "You", subtitle: "Type a message to the assistant", tone: "neutral" },
      { title: "Orchestrator", subtitle: "Classifies what you're asking for", tone: "accent" },
      { title: "Domain agent", subtitle: "Picks the right skill", tone: "accent" },
      { title: "Postgres", subtitle: "Reads or writes real data", tone: "success" },
      { title: "Agent trail", subtitle: "Logs exactly what happened", tone: "success" },
    ]);
    this.renderDiagram("watchDiagram", [
      { title: "Scheduler", subtitle: "Runs every few minutes", tone: "neutral" },
      { title: "Signal rules", subtitle: "Scan the database for issues", tone: "warning" },
      { title: "New notification", subtitle: "Saved and broadcast live", tone: "warning" },
      { title: "Shows up everywhere", subtitle: "Bell, dashboard, ticker, Focus Mode", tone: "success" },
    ]);
    this.renderRoles();
    this.renderGlossary();
    await this.loadLiveCounts();
  },

  /** The per-page reference: what each sidebar item and top-bar tool
   *  actually shows and how to use it. This is the practical companion to
   *  the architecture diagrams below it — "what do I click" rather than
   *  "how does it work under the hood." */
  renderPageGuide() {
    const pages = [
      {
        icon: "ti-layout-dashboard", name: "Dashboard",
        shows: "Live counts (open auctions, RFQs, pending requisitions, vendors, POs), a \u201cNeeds attention\u201d panel of automatically-detected issues, four headline financial cards with trend sparklines, and spend/requisition breakdowns.",
        use: "Start here each session. Click a headline card or a category bar to ask the assistant more about it \u2014 the click sends the question for you. Click a Needs Attention row to jump straight to the relevant record.",
      },
      {
        icon: "ti-gavel", name: "Auctions",
        shows: "Every forward and reverse auction, with status and vendor count.",
        use: "Click an auction to see every bid and the winner once closed. Use \u201cNew auction\u201d or just ask the assistant to start one.",
      },
      {
        icon: "ti-file-text", name: "RFQs",
        shows: "Every request for quotation, with how many vendors have quoted so far.",
        use: "Click into one to see a full quote comparison \u2014 cheapest highlighted, overpriced quotes flagged automatically with a reason.",
      },
      {
        icon: "ti-clipboard-check", name: "Requisitions",
        shows: "Every internal purchase request and its status.",
        use: "Click one to see full detail. If it's still pending, Approve/Reject buttons sit right there \u2014 they go through the same assistant path a typed message would.",
      },
      {
        icon: "ti-building-store", name: "Vendors",
        shows: "The supplier directory: category, rating, total spend, and a computed risk level per vendor.",
        use: "Click a vendor to see the risk breakdown (late deliveries, price volatility, flagged incidents) and full order history before deciding to expand or cut a relationship.",
      },
      {
        icon: "ti-receipt", name: "Purchase orders",
        shows: "Every PO, whether it came from an approved requisition or was created directly, with flagged pricing marked.",
        use: "Click one to see items, status, and payment history. Pay it directly from the detail page.",
      },
      {
        icon: "ti-credit-card", name: "Payments",
        shows: "A flat log of every payment processed against a purchase order.",
        use: "Mostly a reference list \u2014 click a row to jump to the purchase order it belongs to.",
      },
      {
        icon: "ti-file-certificate", name: "Contracts",
        shows: "Stored agreements per vendor: terms, value, and the date range.",
        use: "A contract nearing its end date triggers a proactive alert automatically \u2014 you don't need to check this page to catch that.",
      },
      {
        icon: "ti-message-circle", name: "Assistant",
        shows: "The full-page conversation \u2014 the same thread as the floating bubble on every other page.",
        use: "This (or the bubble) is the only place actions actually happen: create, approve, pay, onboard, import a file. Also where analytical questions get answered with inline charts.",
      },
    ];

    const topBarTools = [
      { icon: "ti-search", name: "Search", desc: "Finds vendors, RFQs, auctions, requisitions, and POs by name or ID." },
      { icon: "ti-focus-2", name: "Focus Mode", desc: "A full-screen, distraction-free way to just talk to the assistant. Escape key exits, no button." },
      { icon: "ti-settings", name: "Configuration", desc: "Tune the thresholds behind flagging, risk scoring, and proactive alerts \u2014 without touching code." },
      { icon: "ti-history", name: "Agent trail", desc: "Every real action any agent has taken, timestamped and expandable \u2014 the audit record." },
      { icon: "ti-bell", name: "Notifications", desc: "Things the system found on its own. \u201cView all\u201d opens the full history, resolved included." },
    ];

    const rowHtml = (icon, name, shows, use) => `
      <div style="display:flex;gap:12px;padding:12px 0;border-bottom:1px solid var(--border);">
        <div style="width:30px;height:30px;border-radius:8px;background:var(--accent-dim);flex-shrink:0;
                    display:flex;align-items:center;justify-content:center;">
          <i class="ti ${icon}" style="color:var(--accent-tint);font-size:15px;"></i>
        </div>
        <div>
          <div style="color:var(--text-primary);font-size:12.5px;font-weight:600;margin-bottom:3px;">${name}</div>
          <div style="color:var(--text-secondary);font-size:11.5px;line-height:1.5;"><span style="color:var(--text-muted);">Shows:</span> ${shows}</div>
          <div style="color:var(--text-secondary);font-size:11.5px;line-height:1.5;margin-top:2px;"><span style="color:var(--text-muted);">Use it to:</span> ${use}</div>
        </div>
      </div>
    `;

    const toolRowHtml = (icon, name, desc) => `
      <div style="display:flex;gap:12px;padding:10px 0;border-bottom:1px solid var(--border);">
        <div style="width:26px;height:26px;border-radius:7px;background:var(--surface-sunken);flex-shrink:0;
                    display:flex;align-items:center;justify-content:center;">
          <i class="ti ${icon}" style="color:var(--text-secondary);font-size:13px;"></i>
        </div>
        <div>
          <div style="color:var(--text-primary);font-size:12px;font-weight:600;margin-bottom:2px;">${name}</div>
          <div style="color:var(--text-muted);font-size:11px;line-height:1.5;">${desc}</div>
        </div>
      </div>
    `;

    document.getElementById("pageGuideList").innerHTML =
      pages.map((p) => rowHtml(p.icon, p.name, p.shows, p.use)).join("") +
      `<div style="color:var(--text-muted);font-size:10.5px;text-transform:uppercase;letter-spacing:0.4px;margin:16px 0 4px;">Top bar tools</div>` +
      topBarTools.map((t) => toolRowHtml(t.icon, t.name, t.desc)).join("");
  },

  renderIntro() {
    document.getElementById("guideIntro").innerHTML = `
      <p style="color:var(--text-secondary);font-size:13px;line-height:1.6;margin:0;">
        ProcureOS is a procurement platform where actions happen two ways: you can browse every
        page like a normal dashboard app, or you can just tell the Assistant what you want and it
        does the real thing \u2014 creates records, approves requests, processes payments. Everything
        the Assistant does is logged in the Agent Trail, and the system also watches things in the
        background and tells you when something needs attention, without you having to ask.
      </p>
      <div id="liveCountsLine" style="margin-top:12px;font-family:var(--font-mono);font-size:11px;color:var(--text-muted);"></div>
    `;
  },

  renderFlow() {
    const steps = [
      { title: "Land on the dashboard", desc: "See what needs attention, spend totals, and open items at a glance." },
      { title: "Browse or ask", desc: "Click into any page to look something up, or type a request to the Assistant to get something done." },
      { title: "The right agent executes it", desc: "The Orchestrator routes your request to one of five specialist agents, which calls a real skill." },
      { title: "The Agent Trail confirms it", desc: "Every action is logged \u2014 what happened, exactly, not just a chat reply claiming it happened." },
      { title: "Signals surface on their own", desc: "Separately, the system watches for problems in the background and tells you \u2014 the bell, the dashboard, or a Pulse Rail marker in Focus Mode." },
    ];
    document.getElementById("guideFlow").innerHTML = steps.map((s, i) => `
      <div style="display:flex;gap:12px;padding:10px 0;${i < steps.length - 1 ? "border-bottom:1px solid var(--border);" : ""}">
        <div style="width:22px;height:22px;border-radius:6px;background:var(--accent-dim);color:var(--accent-tint);
                    display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:600;flex-shrink:0;">${i + 1}</div>
        <div>
          <div style="color:var(--text-primary);font-size:12.5px;font-weight:500;">${s.title}</div>
          <div style="color:var(--text-muted);font-size:11.5px;margin-top:2px;">${s.desc}</div>
        </div>
      </div>
    `).join("");
  },

  /** Renders a vertical flow diagram in the app's own theme (not a copy of
   *  any external design system) — boxes connected by lines, each line
   *  carrying a small animated dot to suggest a request actually moving
   *  through the pipeline, reusing the same subtle-motion language as the
   *  rest of the app (breathing pulse, ticker fade) rather than inventing
   *  a new animation style just for this page. */
  renderDiagram(containerId, nodes) {
    const toneColors = {
      neutral: { bg: "var(--surface-sunken)", border: "var(--border-strong)", text: "var(--text-primary)", sub: "var(--text-muted)" },
      accent: { bg: "var(--accent-dim)", border: "var(--accent)", text: "var(--accent-tint)", sub: "var(--text-secondary)" },
      success: { bg: "var(--success-bg)", border: "var(--success)", text: "var(--success-text)", sub: "var(--text-secondary)" },
      warning: { bg: "var(--warning-bg)", border: "var(--warning)", text: "var(--warning-text)", sub: "var(--text-secondary)" },
    };
    const el = document.getElementById(containerId);
    el.innerHTML = nodes.map((n, i) => {
      const c = toneColors[n.tone] || toneColors.neutral;
      const box = `
        <div style="background:${c.bg};border:1px solid ${c.border};border-radius:10px;padding:10px 14px;">
          <div style="color:${c.text};font-size:12.5px;font-weight:600;">${n.title}</div>
          <div style="color:${c.sub};font-size:11px;margin-top:2px;">${n.subtitle}</div>
        </div>
      `;
      const connector = i < nodes.length - 1
        ? `<div class="guide-connector"><span class="guide-connector-dot"></span></div>`
        : "";
      return box + connector;
    }).join("");
  },

  renderRoles() {
    const roles = [
      {
        icon: "ti-shopping-cart", name: "Buyer",
        starts: "Dashboard's operational strip",
        lives: "Auctions, RFQs, Assistant",
        ask: "\u201cDraft an RFQ for 200 monitors to vendors 3 and 7\u201d",
      },
      {
        icon: "ti-check", name: "Approver",
        starts: "Needs Attention panel",
        lives: "Requisitions, Dashboard headline row",
        ask: "Uses Approve/Reject directly \u2014 the Agent Trail is the record",
      },
      {
        icon: "ti-building-store", name: "Vendor / Category Manager",
        starts: "Vendors page, sorted by risk",
        lives: "Vendors, Contracts, RFQ comparisons",
        ask: "\u201cCompare vendor 2 and vendor 6 by spend\u201d",
      },
      {
        icon: "ti-chart-line", name: "Executive / CPO",
        starts: "Dashboard headline KPIs",
        lives: "Focus Mode",
        ask: "\u201cWhy has spend gone up this quarter?\u201d",
      },
    ];
    document.getElementById("roleCards").innerHTML = roles.map((r) => `
      <div class="stat-card">
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px;">
          <i class="ti ${r.icon}" style="color:var(--accent-tint);font-size:16px;"></i>
          <span style="color:var(--text-primary);font-size:13px;font-weight:600;">${r.name}</span>
        </div>
        <div style="font-size:11.5px;color:var(--text-secondary);line-height:1.7;">
          <div><span style="color:var(--text-muted);">Starts at:</span> ${r.starts}</div>
          <div><span style="color:var(--text-muted);">Lives in:</span> ${r.lives}</div>
          <div style="margin-top:6px;font-style:italic;">${r.ask}</div>
        </div>
      </div>
    `).join("");
  },

  renderGlossary() {
    const terms = [
      ["Agent trail", "A real-time, permanent log of every action any agent has taken \u2014 the audit record that proves the assistant isn't a black box."],
      ["Flagged", "A quote or purchase order priced 30%+ above that vendor/category's historical average, detected automatically."],
      ["Risk score", "A vendor's risk level, computed only from that vendor's own order/quote history in ProcureOS \u2014 not third-party risk data."],
      ["Focus Mode / Living Report", "A full-screen, assistant-only view with no dashboard or sidebar \u2014 answers render as an editorial report, not chat bubbles."],
      ["Pulse Rail", "The Agent Trail and proactive signals, reimagined as an ambient vertical line inside Focus Mode, instead of a list."],
      ["Needs attention / signals", "Things the system noticed on its own \u2014 nothing on this list was asked for."],
      ["Spend Under Management / Maverick spend", "Approximated from whether a purchase order came from an approved requisition, not measured against real contracts or an approved-vendor list."],
    ];
    document.getElementById("glossaryList").innerHTML = terms.map(([term, def], i) => `
      <div style="padding:9px 0;${i < terms.length - 1 ? "border-bottom:1px solid var(--border);" : ""}">
        <div style="color:var(--text-primary);font-size:12.5px;font-weight:500;">${term}</div>
        <div style="color:var(--text-muted);font-size:11.5px;margin-top:2px;">${def}</div>
      </div>
    `).join("");
  },

  async loadLiveCounts() {
    const el = document.getElementById("liveCountsLine");
    if (!el) return;
    try {
      const d = await API.getDashboard();
      el.textContent = `Right now: ${d.vendors} vendors, ${d.purchase_orders} purchase orders, 5 domain agents, 18 skills \u2014 in your own database.`;
    } catch {
      el.textContent = "";
    }
  },
};
