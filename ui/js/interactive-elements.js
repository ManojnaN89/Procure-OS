/**
 * Shared logic for the "beyond charts" interactive elements — clickable
 * entity references, inline progress bars, expandable detail lists — used
 * by both Focus Mode and the regular chat bubble renderer. Each surface
 * uses its own CSS class prefix ("fm" or "bubble") to match its own visual
 * scale, but the underlying logic and data shape are identical.
 */
const InteractiveElements = {
  /**
   * Centralized navigation — every click-to-navigate element in this
   * module (and Notifications.handleClick) routes through this instead of
   * setting window.location.hash directly. Reason: the router re-renders
   * the target page correctly even while Focus Mode is active, but that
   * page lives inside #app, which stays hidden (opacity: 0) for as long
   * as Focus Mode's overlay is showing — so a plain hash change was
   * navigating invisibly, with nothing visibly happening on screen.
   * Exiting Focus Mode first (only if it's actually active) makes the
   * navigation visible; this is a no-op guard when Focus Mode isn't on,
   * so it's always safe to call.
   */
  navigateTo(route) {
    if (typeof FocusMode !== "undefined" && FocusMode.active) {
      FocusMode.exit();
    }
    window.location.hash = route;
  },

  /**
   * Wraps recognized entity references (Requisition #13, PO #23, RFQ #1,
   * Contract #5, Auction #2) in clickable chips. Runs on already-escaped
   * HTML (safe: none of these word patterns can appear inside the tags our
   * own renderers produce), same safety model as the existing number
   * highlighting. Deliberately scoped to numbered-ID patterns only — vendor
   * names are free text with no reliable pattern to match against safely,
   * so they're not linkified here.
   */
  linkifyEntities(html) {
    const patterns = [
      { re: /\bRequisitions?\s*#?(\d+)/gi, route: "requisitions" },
      { re: /\b(?:Purchase Order|PO)s?\s*#?(\d+)/gi, route: "purchase-orders" },
      { re: /\bRFQs?\s*#?(\d+)/gi, route: "rfqs" },
      { re: /\bContracts?\s*#?(\d+)/gi, route: "contracts" },
      { re: /\bAuctions?\s*#?(\d+)/gi, route: "auctions" },
    ];
    let out = html;
    patterns.forEach(({ re, route }) => {
      out = out.replace(re, (match, id) => `<span class="entity-chip" data-route="#/${route}/${id}">${match}</span>`);
    });
    return out;
  },

  /** Call once after setting innerHTML somewhere containing .entity-chip
   *  elements, to wire up navigation clicks (can't attach listeners to a
   *  raw HTML string, only to real DOM nodes). */
  wireEntityChips(container) {
    container.querySelectorAll(".entity-chip[data-route]").forEach((el) => {
      el.addEventListener("click", (e) => {
        e.stopPropagation();
        this.navigateTo(el.dataset.route);
      });
    });
  },

  progressBarHtml(extra, prefix) {
    const pct = Math.max(0, Math.min(100, extra.value_pct || 0));
    return `
      <div class="${prefix}-progress-card">
        <div class="${prefix}-progress-card-top">
          <span>${extra.label || "Progress"}</span>
          <span class="value">${pct}%</span>
        </div>
        <div class="${prefix}-progress-bar-track">
          <div class="${prefix}-progress-bar-fill" style="width:${pct}%"></div>
          <div class="${prefix}-progress-bar-remainder" style="width:${100 - pct}%"></div>
        </div>
        ${extra.secondary_label ? `<div class="${prefix}-progress-card-sub">${extra.secondary_label}</div>` : ""}
      </div>
    `;
  },

  /** Builds a live DOM element (not an HTML string) since it needs its own
   *  toggle + item-click listeners, unlike the progress bar. Reuses the
   *  existing signal-list item classes for its rows — same visual language
   *  as the launch briefing's clickable signal list, not a new pattern. */
  buildExpandable(extra, prefix) {
    const wrap = document.createElement("div");
    wrap.className = `${prefix}-expandable`;

    const toggle = document.createElement("div");
    toggle.className = `${prefix}-expandable-toggle`;
    toggle.innerHTML = `<i class="ti ti-chevron-right"></i><span>Show ${extra.label || "details"}</span>`;
    toggle.addEventListener("click", () => wrap.classList.toggle("open"));
    wrap.appendChild(toggle);

    const body = document.createElement("div");
    body.className = `${prefix}-expandable-body`;
    const itemClass = prefix === "fm" ? "fm-signal-item" : "bubble-signal-item";
    const dotClass = prefix === "fm" ? "fm-signal-dot" : "bubble-signal-dot";
    (extra.items || []).forEach((item) => {
      const row = document.createElement("div");
      row.className = itemClass;
      row.innerHTML = `<span class="${dotClass}" style="background:var(--accent)"></span><span>${item.text}</span>`;
      if (item.route) {
        row.addEventListener("click", () => this.navigateTo(item.route));
      }
      body.appendChild(row);
    });
    wrap.appendChild(body);
    return wrap;
  },

  /** Renders whichever extras a message carries into `container`, in
   *  order — the same small set of extra types either surface supports. */
  renderExtras(container, extras, prefix) {
    (extras || []).forEach((extra) => {
      if (extra.type === "progress_bar") {
        const div = document.createElement("div");
        div.innerHTML = this.progressBarHtml(extra, prefix);
        container.appendChild(div.firstElementChild);
      } else if (extra.type === "expandable_list") {
        container.appendChild(this.buildExpandable(extra, prefix));
      }
    });
  },
};
