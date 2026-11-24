/**
 * Confirm services: the requester says how much of a service they asked for was done (percent, quantity or amount).
 * The confirmation is the service's receipt; the supplier's invoice is matched against it.
 */
const ServicesView = {
  esc(value) {
    return String(value ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  },

  async render(container) {
    container.innerHTML = `
      <div class="page-header">
        <h1>Confirm services</h1>
        <p>Services you asked for that the supplier has started. Confirm what was done; the supplier is paid only for what you confirm.</p>
      </div>
      <div id="svcWrap"><div class="empty-state">Loading…</div></div>
      <h2 class="section-title" style="margin-top:22px;">Your confirmations</h2>
      <div class="table-wrap" id="svcDone"></div>
    `;
    let data;
    try {
      data = await API.getJSON("/api/services/to-confirm");
    } catch {
      const w = document.getElementById("svcWrap");
      if (w) w.innerHTML = `<div class="empty-state">Couldn't load the services to confirm.</div>`;
      return;
    }
    const w = document.getElementById("svcWrap");
    if (!w) return; // the person has already gone to another page
    w.innerHTML = data.lines.length
      ? data.lines.map((l) => this.card(l)).join("")
      : `<div class="empty-next"><strong>Nothing is waiting for your confirmation.</strong><br>A service appears here once its order has been sent to the supplier.</div>`;
    document.getElementById("svcDone").innerHTML = data.entries.length
      ? `<table class="data-table"><thead><tr><th>ENTRY</th><th>ORDER</th><th>SERVICE</th><th>CONFIRMED</th><th>AMOUNT</th><th>ON</th><th>HOW</th><th></th></tr></thead><tbody>
          ${data.entries.map((e) => `<tr><td class="cell-muted">${this.esc(e.id)}</td><td><a href="#/purchase-orders/${this.esc(e.po_no)}" style="color:var(--accent-tint);">${this.esc(e.po_no)}</a></td><td>${this.esc(e.name)}<div class="cell-muted" style="font-size:11px;">${this.esc(e.supplier)}</div></td><td>${this.esc(e.qty)} <span class="cell-muted">${this.esc(e.uom || "")}</span></td><td>${formatCurrency(e.amount)}</td><td class="cell-muted">${formatDate(e.on)}</td><td class="cell-muted">${e.status === "reversed" ? `reversed on ${formatDate(e.reversed_on)}` : this.esc(e.via === "app" ? "in the app" : "by email")}</td><td>${e.can_reverse ? `<input class="svc-rev-why" placeholder="Why reverse it?" style="width:120px;"><div class="btn btn-secondary btn-sm svc-rev" data-id="${this.esc(e.id)}" style="margin-top:4px;">Reverse</div>` : ""}</td></tr>`).join("")}
        </tbody></table><div id="svcRevNote" style="font-size:12px;color:var(--warning-text);margin-top:6px;"></div>`
      : `<div class="empty-state">You have not confirmed a service yet.</div>`;
    w.querySelectorAll(".svc-send").forEach((btn) => btn.addEventListener("click", () => this.send(btn.dataset.key)));
    document.querySelectorAll(".svc-rev").forEach((btn) => btn.addEventListener("click", async () => {
      const why = btn.parentElement.querySelector(".svc-rev-why").value.trim();
      const res = await API.postJSON(`/api/services/entries/${encodeURIComponent(btn.dataset.id)}/reverse`, { reason: why });
      if (res.ok && !res.data.proposal_id) return this.render(document.getElementById("appContent"));
      const note = document.getElementById("svcRevNote");
      if (note) note.textContent = res.ok ? `Asked the purchase manager (proposal ${res.data.proposal_id}).` : (res.data.detail || "The entry was not reversed.");
    }));
  },

  card(l) {
    const key = `${l.po_no}-${l.line_no}`;
    return `
      <div class="panel" style="margin-bottom:12px;" id="svc-${this.esc(key)}" data-po="${this.esc(l.po_no)}" data-line="${this.esc(l.line_no)}">
        <div style="display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;">
          <div>
            <strong>${this.esc(l.name)}</strong>
            <div class="cell-muted" style="font-size:12px;">${this.esc(l.supplier)} · <a href="#/purchase-orders/${this.esc(l.po_no)}" style="color:var(--accent-tint);">${this.esc(l.po_no)}</a> · ordered ${this.esc(l.ordered)} ${this.esc(l.uom || "")} for ${formatCurrency(l.amount)}</div>
          </div>
          <div class="cell-muted" style="font-size:12px;">confirmed so far ${this.esc(l.confirmed)} (${formatCurrency(l.confirmed_amount)}), ${this.esc(l.left)} left</div>
        </div>
        <div style="margin-top:10px;display:flex;gap:8px;flex-wrap:wrap;align-items:center;">
          <select class="svc-basis" style="padding:6px;">
            <option value="pct">Percent of the order</option>
            <option value="qty">Quantity</option>
            <option value="amount">Amount (lump sum)</option>
          </select>
          <input class="svc-value" type="number" min="0" step="any" placeholder="e.g. 100" style="width:110px;padding:6px;">
          <input class="svc-from" type="date" title="Period from" style="padding:6px;">
          <input class="svc-to" type="date" title="Period to" style="padding:6px;">
          <input class="svc-remarks" placeholder="Remarks" style="padding:6px;flex:1;min-width:140px;">
          <div class="btn btn-primary btn-sm svc-send" data-key="${this.esc(key)}">Confirm</div>
        </div>
        <div class="svc-note" style="font-size:12px;color:var(--warning-text);margin-top:6px;"></div>
      </div>`;
  },

  async send(key) {
    const box = document.getElementById(`svc-${key}`);
    const note = box.querySelector(".svc-note");
    const value = box.querySelector(".svc-value").value;
    if (value === "" || Number(value) <= 0) { note.textContent = "Say how much was done."; return; }
    const res = await API.postJSON("/api/services/confirm", {
      po_no: box.dataset.po,
      line_no: Number(box.dataset.line),
      basis: box.querySelector(".svc-basis").value,
      value,
      period_from: box.querySelector(".svc-from").value || null,
      period_to: box.querySelector(".svc-to").value || null,
      remarks: box.querySelector(".svc-remarks").value,
    });
    if (res.ok) { this.render(document.getElementById("appContent")); return; }
    note.textContent = res.data.detail || "The confirmation was not recorded.";
  },
};
