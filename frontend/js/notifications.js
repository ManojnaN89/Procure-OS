/**
 * Proactive signals — the one truly new event source in the app (everything
 * else is a renderer of something the user asked for; this is the opposite).
 * One global WebSocket (not session-scoped, since signals are company-wide,
 * not personal), fanning out to: the bell dropdown, the Dashboard's "Needs
 * attention" panel, the floating assistant's ticker, and Focus Mode's
 * Pulse Rail — all reading from this one module, never recomputing separately.
 */
const SEVERITY_COLORS = {
  high: "#EF4444",
  medium: "#F5A623",
  low: "#6C5CE7",
};

const TYPE_LABELS = {
  exception_flagged: "EXCEPTION",
  vendor_risk: "HIGH RISK",
  stale_requisition: "STUCK",
  budget_overrun: "BUDGET",
  contract_expiring: "CONTRACT",
};

const Notifications = {
  socket: null,
  recent: [], // ring buffer, newest first — mirrors Trail's pattern
  maxBuffer: 30,
  listeners: [],
  unreadCount: 0,

  init() {
    this.connect();
    this.refreshUnreadCount();

    this.bellBtn = document.getElementById("notificationBellBtn");
    this.dropdown = document.getElementById("notifDropdown");
    this.dot = document.getElementById("notificationDot");

    this.bellBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      this.toggleDropdown();
    });
    document.addEventListener("click", (e) => {
      if (!document.querySelector(".notif-anchor").contains(e.target)) this.closeDropdown();
    });
  },

  subscribe(fn) {
    this.listeners.push(fn);
    return () => { this.listeners = this.listeners.filter((l) => l !== fn); };
  },

  notify(evt) {
    this.listeners.forEach((fn) => fn(evt, this.recent));
  },

  connect() {
    const proto = window.location.protocol === "https:" ? "wss" : "ws";
    const url = `${proto}://${window.location.host}/ws/notifications`;
    try {
      this.socket = new WebSocket(url);
    } catch {
      return;
    }
    this.socket.addEventListener("message", (msg) => {
      try {
        const evt = JSON.parse(msg.data);
        this.recent.unshift(evt);
        if (this.recent.length > this.maxBuffer) this.recent.pop();
        this.unreadCount += 1;
        this.updateBadge();
        this.notify(evt);
      } catch { /* ignore malformed frames */ }
    });
  },

  colorFor(severity) { return SEVERITY_COLORS[severity] || "#8B93A7"; },
  labelFor(type) { return TYPE_LABELS[type] || type.replace(/_/g, " ").toUpperCase(); },

  async refreshUnreadCount() {
    try {
      const r = await API.getUnreadNotificationCount();
      this.unreadCount = r.count || 0;
      this.updateBadge();
    } catch { /* backend may not be up yet on first load */ }
  },

  updateBadge() {
    if (this.dot) this.dot.style.display = this.unreadCount > 0 ? "block" : "none";
  },

  async toggleDropdown() {
    if (this.dropdown.classList.contains("open")) { this.closeDropdown(); return; }
    this.dropdown.classList.add("open");
    await this.renderDropdown();
  },

  closeDropdown() {
    this.dropdown.classList.remove("open");
  },

  async renderDropdown() {
    this.dropdown.innerHTML = `<div style="padding:16px;text-align:center;color:var(--text-muted);font-size:11.5px;">Loading\u2026</div>`;
    let rows;
    try {
      rows = await API.listNotifications({ unresolved_only: true, limit: 20 });
    } catch {
      this.dropdown.innerHTML = `<div style="padding:16px;text-align:center;color:var(--text-muted);font-size:11.5px;">Couldn't load notifications.</div>`;
      return;
    }

    const header = `
      <div class="notif-dropdown-header">
        <span>Needs attention</span>
        <span>${rows.length} active</span>
      </div>
    `;
    const body = rows.length
      ? rows.map((n) => `
          <div class="notif-row" data-id="${n.id}" style="border-left-color:${this.colorFor(n.severity)}">
            <div class="notif-row-top">
              <span class="notif-row-type" style="color:${this.colorFor(n.severity)}">${this.labelFor(n.type)}</span>
              <span class="notif-row-time">${this.timeAgo(n.created_at)}</span>
            </div>
            <div class="notif-row-msg">${n.message}</div>
          </div>
        `).join("")
      : `<div style="padding:20px 16px;text-align:center;color:var(--text-muted);font-size:11.5px;">Nothing needs attention right now.</div>`;

    this.dropdown.innerHTML = header + body + `
      <div class="notif-dropdown-footer" id="notifViewAllFooter">
        <span>View all</span>
      </div>
    `;
    this.dropdown.querySelectorAll(".notif-row").forEach((row) => {
      row.addEventListener("click", () => {
        const n = rows.find((r) => String(r.id) === row.dataset.id);
        if (n) this.handleClick(n);
      });
    });
    document.getElementById("notifViewAllFooter").addEventListener("click", () => {
      this.closeDropdown();
      window.location.hash = "#/notification-history";
    });

    // opening the dropdown marks everything currently shown as read
    if (rows.length) {
      rows.forEach((n) => API.markNotificationRead(n.id));
      this.unreadCount = 0;
      this.updateBadge();
    }
  },

  /**
   * Routes each signal type to whichever surface fits it. When Focus Mode
   * is active, review happens entirely in conversation instead — the
   * assistant explains the item and offers real next-step suggestions
   * (including dismiss/accept, now that both exist as real capabilities),
   * so the whole review-and-resolve loop stays inside Focus Mode without
   * ever navigating out. Outside Focus Mode, behavior is unchanged: read-
   * oriented signals (risk, contract, exception) go straight to the
   * record's detail page, and the one signal with an unambiguous write
   * action (a stuck requisition) opens the Assistant with a decision prompt.
   */
  handleClick(n) {
    this.closeDropdown();
    if (typeof FloatingAssistant !== "undefined") FloatingAssistant.close();
    if (typeof TrailDrawer !== "undefined") TrailDrawer.close();

    if (typeof FocusMode !== "undefined" && FocusMode.active) {
      const { text, agent } = this._focusModePrompt(n);
      Conversation.send(text, { agent });
      return;
    }

    // routed through InteractiveElements.navigateTo() rather than setting
    // window.location.hash directly — a plain hash change while Focus Mode
    // is active re-renders the target page correctly, but that page stays
    // invisible behind Focus Mode's overlay; navigateTo() exits Focus Mode
    // first (only if it's actually on) so the navigation is visible
    switch (n.type) {
      case "vendor_risk":
        InteractiveElements.navigateTo(`#/vendors/${n.related_id}`);
        break;
      case "contract_expiring":
        InteractiveElements.navigateTo(`#/contracts/${n.related_id}`);
        break;
      case "exception_flagged":
        // related_type is either "rfq" or "purchase_order" — routing both
        // to /rfqs previously sent flagged-PO clicks to the wrong page,
        // using a PO id as if it were an RFQ id
        InteractiveElements.navigateTo(
          n.related_type === "purchase_order" ? `#/purchase-orders/${n.related_id}` : `#/rfqs/${n.related_id}`
        );
        break;
      case "stale_requisition":
        InteractiveElements.navigateTo(`#/requisitions/${n.related_id}`);
        break;
      case "budget_overrun":
        Prefill.set(`Why is spend over budget in this category, and what should I do about it? `);
        InteractiveElements.navigateTo("#/assistant");
        break;
      default:
        InteractiveElements.navigateTo("#/dashboard");
    }
  },

  /** The conversational prompt sent when a signal is clicked from inside
   *  Focus Mode — one per type, matching what a real, useful question
   *  about that specific item would look like. */
  _focusModePrompt(n) {
    // Each prompt names the exact record (RFQ #, PO #, vendor #...) and
    // carries the agent that owns it, so the backend doesn't have to guess
    // from the wording. Without this, a flagged-quote Review was routed to
    // the Source-to-Pay Agent, which has no quote tools.
    const id = n.related_id;
    switch (n.type) {
      case "exception_flagged":
        if (n.related_type === "purchase_order") {
          return {
            agent: "source_to_pay",
            text: `Review the flagged purchase order PO #${id}: explain why it was flagged and what I should do. (${n.message})`,
          };
        }
        return {
          agent: "rfq",
          text: `Review the flagged quote on RFQ #${id}: explain why it was flagged, compare it with the other quotes on this RFQ, and recommend what I should do. (${n.message})`,
        };
      case "vendor_risk":
        return {
          agent: "insights",
          text: `Why is vendor #${id} high risk, and what should I do about it? (${n.message})`,
        };
      case "stale_requisition":
        return {
          agent: "requisition",
          text: `Show me requisition #${id}: what it's for, how long it's been pending, and what I should do next.`,
        };
      case "budget_overrun":
        return {
          agent: "insights",
          text: `Why is spend over budget here, which vendors are driving it, and what should I do about it? (${n.message})`,
        };
      case "contract_expiring":
        return {
          agent: "source_to_pay",
          text: `Tell me about contract #${id} that's expiring soon and what my options are. (${n.message})`,
        };
      default:
        return { agent: null, text: `Tell me more about this: ${n.message}` };
    }
  },

  timeAgo(iso) {
    if (!iso) return "";
    const diffMs = Date.now() - new Date(iso).getTime();
    const mins = Math.floor(diffMs / 60000);
    if (mins < 1) return "just now";
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    return `${Math.floor(hrs / 24)}d ago`;
  },
};
