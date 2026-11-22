/**
 * Exceptions: one list of everything that is stuck and needs a person (blocked invoices, goods without an order,
 * services not confirmed, approvals past their reminder, decisions waiting, suppliers to chase), oldest first,
 * with the reason, who must act and what they do next. Read only: the link opens the screen where it is worked.
 */
const ExceptionsView = {
  async render(container) {
    this.kind = "";
    this.mine = false;
    container.innerHTML = `
      <div class="page-header page-header-row">
        <div><h1>Exceptions</h1><p>Everything that is stuck and needs a person, oldest first. Age is in working days.</p></div>
        <div style="display:flex;gap:10px;align-items:center;">
          <select id="excKind" style="padding:6px;"><option value="">All kinds</option></select>
          <label style="font-size:13px;"><input type="checkbox" id="excMine"> Only mine</label>
        </div>
      </div>
      <div class="table-wrap" id="excList"><div class="empty-state">Loading…</div></div>`;
    document.getElementById("excKind").addEventListener("change", (e) => { this.kind = e.target.value; this.load(); });
    document.getElementById("excMine").addEventListener("change", (e) => { this.mine = e.target.checked; this.load(); });
    await this.load(true);
  },

  async load(first) {
    const box = document.getElementById("excList");
    const q = new URLSearchParams();
    if (this.kind) q.set("kind", this.kind);
    if (this.mine) q.set("mine", "true");
    let data;
    try {
      data = await API.getJSON(`/api/exceptions${q.toString() ? `?${q}` : ""}`);
    } catch {
      if (box) box.innerHTML = `<div class="empty-state">Couldn't load the exceptions.</div>`;
      return;
    }
    if (!box || !document.body.contains(box)) return; // the person has already gone to another page
    if (first) {
      document.getElementById("excKind").innerHTML = `<option value="">All kinds</option>` +
        data.kinds.map((k) => `<option value="${escapeHtml(k.key)}">${escapeHtml(k.title)}</option>`).join("");
    }
    box.innerHTML = data.rows.length
      ? `<table class="data-table"><thead><tr><th>WHAT</th><th>REASON</th><th>OWNER</th><th>NEXT STEP</th><th>AGE</th><th>AMOUNT</th></tr></thead><tbody>
        ${data.rows.map((r) => `<tr class="exc-row" data-kind="${escapeHtml(r.kind)}" data-ref="${escapeHtml(r.ref)}">
          <td><a href="${escapeHtml(r.link)}" style="color:var(--accent-tint);">${escapeHtml(r.ref)}</a><div class="cell-muted" style="font-size:11px;">${escapeHtml(r.kind_title)}</div></td>
          <td>${escapeHtml(r.reason)}</td>
          <td>${escapeHtml(r.owner)}${r.mine ? ` <span class="badge badge-pending">You</span>` : ""}</td>
          <td>${escapeHtml(r.next_step)}</td>
          <td>${escapeHtml(r.age_days)} days<div class="cell-muted" style="font-size:11px;">since ${formatDate(r.since)}</div></td>
          <td>${r.amount === null ? '<span class="cell-muted">—</span>' : formatCurrency(r.amount)}${r.case && r.mine ? `<div><span class="btn btn-sm" data-close="${escapeHtml(r.case.task_id)}" style="margin-top:4px;">Close</span></div>` : ""}</td></tr>`).join("")}
        </tbody></table>`
      : `<div class="empty-next"><strong>Nothing is stuck${this.mine ? " on you" : ""}.</strong><br>Blocked invoices, goods without an order, services not confirmed and decisions that wait appear here.</div>`;
    box.querySelectorAll("[data-close]").forEach((b) => {
      b.addEventListener("click", () => this.closeForm(b, data.rows.find((r) => r.case && String(r.case.task_id) === b.dataset.close)));
    });
  },

  // A case for a person (a change in the ERP's demand, supply promised too late): say what was done, then it is closed.
  closeForm(button, row) {
    const words = { reduced: "Order reduced", cancelled: "Order cancelled", kept_for_stock: "Kept for stock", expedited: "Supplier asked to deliver earlier", not_possible: "Not possible", returned_to_stock: "Returned to stock", kept_for_production: "Kept for production", accepted_late: "Accepted late", bought_elsewhere: "Bought elsewhere" };
    const tr = button.closest("tr");
    const panel = document.createElement("tr");
    panel.innerHTML = `<td colspan="6"><div class="panel" style="margin:4px 0;">
      <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;">
        <select id="excOutcome" aria-label="What was done" style="padding:6px;">${row.case.outcomes.map((o) => `<option value="${escapeHtml(o)}">${escapeHtml(words[o] || o)}</option>`).join("")}</select>
        <input id="excNote" placeholder="Note (needed for ${escapeHtml(row.case.note_needed.map((o) => (words[o] || o).toLowerCase()).join(" or "))})" style="min-width:260px;padding:6px;">
        <span class="btn btn-primary btn-sm" id="excSave">Close the case</span>
      </div><div id="excErr" style="font-size:12.5px;color:var(--warning-text);margin-top:8px;"></div></div></td>`;
    tr.after(panel);
    button.remove();
    document.getElementById("excSave").addEventListener("click", async () => {
      const res = await API.postJSON(row.case.close_url || `/api/demand/cases/${encodeURIComponent(row.case.task_id)}/close`, {
        outcome: document.getElementById("excOutcome").value, note: document.getElementById("excNote").value,
      });
      if (!res.ok) { document.getElementById("excErr").textContent = res.data.detail || "That did not work."; return; }
      await this.load();
    });
  },
};
