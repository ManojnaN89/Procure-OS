/**
 * Small shared pieces of the interface, used by every screen:
 *   UI.toast(text, kind)        a short message in the corner that goes away by itself
 *   UI.backLink(href, label)    "← Purchase orders" above a document's title
 *   UI.callout(kind, title, html)  a hold, an error or a tip explained in words (kind: info, warning, danger, success)
 *   UI.enhanceTables(root)      right-aligns columns of amounts and quantities, and labels cells for the phone layout
 *   ListSearch.attach(route)    a search box over a list page's table (called by the router)
 * Every table drawn into the page is enhanced, so new screens get it without doing anything.
 */
const UI = {
  toast(message, kind = "info") {
    let host = document.getElementById("toastHost");
    if (!host) {
      host = document.createElement("div");
      host.id = "toastHost";
      host.className = "toast-host";
      host.setAttribute("role", "status");
      host.setAttribute("aria-live", "polite");
      document.body.appendChild(host);
    }
    const el = document.createElement("div");
    el.className = `toast toast-${kind}`;
    el.textContent = message;
    host.appendChild(el);
    setTimeout(() => el.remove(), 4500);
  },

  backLink(href, label) {
    return `<a class="back-link" href="${escapeHtml(href)}"><i class="ti ti-arrow-left" aria-hidden="true"></i>${escapeHtml(label)}</a>`;
  },

  CALLOUT_ICONS: { info: "ti-info-circle", warning: "ti-alert-triangle", danger: "ti-alert-octagon", success: "ti-circle-check" },

  /** `html` is markup the caller has already escaped. */
  callout(kind, title, html) {
    return `<div class="callout callout-${kind}" role="${kind === "danger" ? "alert" : "note"}"><i class="ti ${this.CALLOUT_ICONS[kind] || this.CALLOUT_ICONS.info}" aria-hidden="true"></i>
      <div>${title ? `<div class="callout-title">${escapeHtml(title)}</div>` : ""}${html || ""}</div></div>`;
  },

  /** The approval steps of a request or an order as a timeline: who, in what role, their decision and when. */
  approvalSteps(steps, labels) {
    if (!steps || !steps.length) return "";
    return `<ol class="timeline">${steps.map((s) => {
      const tone = s.status === "waiting" ? "draft" : statusTone(s.status);
      return `<li class="tl-${tone}">
        <div class="tl-head"><span class="tl-who">${escapeHtml(s.name)}</span><span class="badge badge-${tone}">${escapeHtml(labels[s.status] || s.status)}</span></div>
        <div class="tl-meta">${escapeHtml(s.designation || s.role)}${s.stand_in ? ` · ${escapeHtml(s.stand_in)} standing in` : ""}${s.acted_at ? ` · ${formatDate(s.acted_at)}` : ""}</div>
        ${s.comment ? `<div class="tl-note">“${escapeHtml(s.comment)}”</div>` : ""}
        ${s.why ? `<div class="tl-meta">Why: ${escapeHtml(s.why)}</div>` : ""}
      </li>`;
    }).join("")}</ol>`;
  },

  /** Label and value pairs for the side of a document page. Values are markup the caller has escaped; empty ones are left out. */
  facts(pairs) {
    const rows = pairs.filter(([, v]) => v !== "" && v !== null && v !== undefined);
    return `<dl class="facts facts-stack">${rows.map(([k, v]) => `<div><dt>${escapeHtml(k)}</dt><dd>${v}</dd></div>`).join("")}</dl>`;
  },

  /** The totals under a document's lines: [label, amount] pairs; the last one is the grand total. Zero tax lines are left out. */
  totals(pairs) {
    const rows = pairs.filter(([label, v], i) => i === pairs.length - 1 || !/GST|cess/i.test(label) || Number(v) !== 0);
    return `<dl class="totals">${rows.map(([label, v], i) => {
      const grand = i === rows.length - 1 ? " grand" : "";
      return `<dt class="${grand}">${escapeHtml(label)}</dt><dd class="${grand}">${formatCurrency(v)}</dd>`;
    }).join("")}</dl>`;
  },

  // An amount, a quantity with its unit, or a percentage: "₹1,890", "10 NOS", "18%", "1,274.40"
  FIGURE: /^[-−]?(?:[₹$€£]\s?)?\d[\d,]*(?:\.\d+)?(?:\s?%|\s[A-Za-z]{1,6})?$/,

  /** Right-aligns the columns of figures, and labels each cell with its column (a list stacks into cards on a phone). */
  enhanceTables(root) {
    (root || document).querySelectorAll("table.data-table:not([data-aligned])").forEach((table) => {
      const heads = table.querySelectorAll("thead th");
      const rows = [...table.querySelectorAll("tbody tr")].filter((tr) => tr.children.length === heads.length);
      if (!heads.length || !rows.length) return;
      table.dataset.aligned = "1";
      heads.forEach((th, i) => {
        const label = th.textContent.trim();
        rows.forEach((tr) => { if (label && !tr.children[i].dataset.label) tr.children[i].dataset.label = label; });
        const firstLines = rows.map((tr) => tr.children[i].innerText.trim().split("\n")[0].trim()).filter((t) => t && t !== "—");
        if (!firstLines.length || !firstLines.every((t) => this.FIGURE.test(t))) return;
        th.classList.add("num");
        rows.forEach((tr) => tr.children[i].classList.add("num"));
      });
    });
  },
};

const ListSearch = {
  /** A search box above the table of a list page; it hides the rows that do not contain the words typed. */
  attach(route, id) {
    const wrap = document.getElementById("tableWrap");
    if (id || !wrap || !ExportList.NAMES[route] || document.getElementById("listSearch")) return;
    const bar = document.createElement("div");
    bar.className = "table-toolbar";
    bar.innerHTML = `<label class="table-search"><i class="ti ti-search" aria-hidden="true"></i><span class="sr-only">Search this list</span>
      <input id="listSearch" type="search" placeholder="Search this list" autocomplete="off"></label>`;
    const tabs = wrap.previousElementSibling && wrap.previousElementSibling.classList.contains("tabs") ? wrap.previousElementSibling : null;
    (tabs || wrap).before(bar);
    const input = bar.querySelector("input");
    const apply = () => {
      const words = input.value.trim().toLowerCase();
      let shown = 0;
      const rows = wrap.querySelectorAll("tbody tr");
      rows.forEach((tr) => {
        const hit = !words || tr.textContent.toLowerCase().includes(words);
        tr.hidden = !hit;
        if (hit) shown += 1;
      });
      let none = wrap.querySelector(".table-search-empty");
      if (words && rows.length && !shown) {
        if (!none) {
          none = document.createElement("div");
          none.className = "table-search-empty";
          wrap.appendChild(none);
        }
        none.textContent = `Nothing in this list matches "${input.value.trim()}".`;
      } else if (none) none.remove();
    };
    input.addEventListener("input", apply);
    new MutationObserver(() => { if (input.value) apply(); }).observe(wrap, { childList: true });
  },
};

(function () {
  let queued = false;
  const run = () => { queued = false; UI.enhanceTables(document.getElementById("appContent")); };
  const start = () => new MutationObserver(() => {
    if (!queued) { queued = true; requestAnimationFrame(run); }
  }).observe(document.getElementById("appContent") || document.body, { childList: true, subtree: true });
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
