// What suppliers wrote about an order or a quotation request, what the system took it to be, and what it did.
// Shown to buyers on the order and the RFQ page. A person without the right sees nothing (the server refuses).
const SupplierMessages = {
  STATUS: {
    applied: ["Done by the system", "badge-approved"],
    task: ["A task was made for a person", "badge-pending"],
    needs_person: ["Needs a person to read it", "badge-rejected"],
    noted: ["Noted", ""],
    ignored: ["Nothing to do", ""],
    handled_elsewhere: ["Handled on another screen", ""],
  },

  esc(value) {
    return String(value ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  },

  /** Fills `box` with the panel for an order ("orders") or a quotation request ("rfqs"). Leaves it empty when there is nothing to show. */
  async mount(box, kind, ref) {
    if (!box || !Auth.can("po.send")) return; // only a buyer may read the supplier's mail: nobody else is even asked
    let d;
    try {
      const res = await Auth.fetch(`/api/${kind}/${encodeURIComponent(ref)}/supplier-messages`);
      if (!res.ok || !document.body.contains(box)) return; // not allowed to see it, or the person has gone to another page
      d = await res.json();
    } catch {
      return; // the panel is an extra: the page works without it
    }
    const mine = d.messages.length || d.commitments.length || d.chasers.length;
    if (!mine && kind !== "orders") return;
    const rows = d.messages.map((m) => {
      const [label, tone] = this.STATUS[m.status] || [m.status, ""];
      return `<div style="padding:8px 0;border-top:1px solid var(--border);font-size:12.5px;">
        <div style="display:flex;justify-content:space-between;gap:10px;">
          <div><b>${this.esc(m.subject || "No subject")}</b> <span class="cell-muted">· ${this.esc(m.from_addr)} · read as ${this.esc((m.kind || "unclear").replace(/_/g, " "))}${m.confidence ? ` (${Math.round(Number(m.confidence) * 100)}% sure)` : ""}</span></div>
          <span class="badge ${tone}">${this.esc(label)}</span>
        </div>
        ${m.action ? `<div class="cell-muted" style="margin-top:3px;">${this.esc(m.action)}</div>` : ""}
        ${m.reply_draft ? `<div style="margin-top:6px;"><span class="cell-muted">Suggested reply (not sent):</span><div style="white-space:pre-wrap;margin-top:2px;">${this.esc(m.reply_draft)}</div></div>` : ""}
      </div>`;
    }).join("");
    const promises = d.commitments.map((c) => `<div style="padding:4px 0;font-size:12.5px;">${c.promised_date ? `Promised ${formatDate(c.promised_date)}` : "Promise"}${c.promised_qty ? ` · ${this.esc(c.promised_qty)} units` : ""}${c.po_line_no ? ` · line ${this.esc(c.po_line_no)}` : ""}${c.note ? ` <span class="cell-muted">“${this.esc(c.note)}”</span>` : ""}</div>`).join("");
    const chases = d.chasers.map((c) => `<div class="cell-muted" style="font-size:12px;padding:2px 0;">${this.esc(String(c.kind).replace(/_/g, " "))} · ${c.how === "task" ? "left for a person" : "emailed"} · ${formatDate(c.sent_at)}</div>`).join("");
    box.innerHTML = `
      <div class="panel" style="margin-bottom:12px;" id="supplierMessagesPanel">
        <div class="panel-title">Supplier messages</div>
        ${rows || `<div class="cell-muted" style="font-size:12.5px;">The supplier has not written about this yet.</div>`}
        ${promises ? `<div class="cell-muted" style="font-size:11px;text-transform:uppercase;margin-top:10px;">What the supplier promised</div>${promises}` : ""}
        ${chases ? `<div class="cell-muted" style="font-size:11px;text-transform:uppercase;margin-top:10px;">Reminders</div>${chases}` : ""}
        ${kind === "orders" ? `<div class="btn btn-secondary btn-sm" id="chaseNow" style="display:inline-block;margin-top:10px;">Chase suppliers now</div><div id="chaseNote" style="font-size:12px;color:var(--text-secondary);margin-top:6px;"></div>` : ""}
      </div>`;
    const chase = document.getElementById("chaseNow");
    if (chase) chase.addEventListener("click", async () => {
      const out = await API.postJSON("/api/supplier-agent/chase", {});
      if (out.ok) await this.mount(box, kind, ref);
      const note = document.getElementById("chaseNote");
      if (note) note.textContent = out.ok
        ? `Done: ${plural(out.data.emailed.length, "reminder")} emailed, ${out.data.tasks.length} left for a person.`
        : (out.data.detail || "That did not work.");
    });
  },
};
