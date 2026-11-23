const PurchaseOrdersView = {
  esc(value) {
    return String(value ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  },

  async render(container, params) {
    if (params && params.id) return this.renderDetail(container, params.id);
    return this.renderList(container);
  },

  async renderList(container) {
    container.innerHTML = `
      <div class="page-header">
        <h1>Purchase orders</h1>
        <p>What has been ordered from suppliers, and where each order is</p>
      </div>
      <div class="table-wrap" id="tableWrap"><div class="empty-state">Loading…</div></div>
    `;
    try {
      const rows = await API.listPurchaseOrders();
      const wrap = document.getElementById("tableWrap");
      if (!wrap) return; // the person has already gone to another page
      if (!rows.length) {
        wrap.innerHTML = `<div class="empty-next"><strong>No purchase orders yet.</strong><br>An order is made when an approved request has a contract price or an awarded quotation.<br>Next: <a href="#/rfqs" style="color:var(--accent-tint);">see the quotations</a>.</div>`;
        return;
      }
      wrap.innerHTML = `
        <table class="data-table">
          <thead><tr><th>NUMBER</th><th>SUPPLIER</th><th>FOR REQUEST</th><th>DELIVER TO</th><th>AMOUNT</th><th>TIER</th><th>STATUS</th></tr></thead>
          <tbody>
            ${rows.map((r) => `
              <tr data-id="${this.esc(r.id)}">
                <td class="cell-muted" style="white-space:nowrap;">${this.esc(r.id)}</td>
                <td>${this.esc(r.supplier)}<div class="cell-muted" style="font-size:11px;">${this.esc(r.supplier_code)}</div></td>
                <td class="cell-muted" style="white-space:nowrap;">${this.esc(r.request_no)}</td>
                <td class="cell-muted">${this.esc(r.ship_to_name || r.ship_to)}</td>
                <td>${formatCurrency(r.amount)}</td>
                <td>${this.tierCell(r)}</td>
                <td>${statusBadge(r.status)}${r.waiting_for ? `<div class="cell-muted" style="font-size:11px;margin-top:3px;">waiting for ${this.esc(r.waiting_for)}</div>` : ""}${r.held ? `<div style="font-size:11px;margin-top:3px;color:var(--warning-text);">held: price above the estimate</div>` : ""}</td>
              </tr>
            `).join("")}
          </tbody>
        </table>
      `;
      wrap.querySelectorAll("tr[data-id]").forEach((tr) => {
        tr.addEventListener("click", () => { window.location.hash = `#/purchase-orders/${tr.dataset.id}`; });
      });
    } catch {
      const gone = document.getElementById("tableWrap");
      if (gone) gone.innerHTML = `<div class="empty-state">Couldn't load purchase orders.</div>`;
    }
  },

  TIER_LABEL: { buyer: "Touchless", purchase_manager: "Purchase manager", director: "Director", cfo: "CFO" },

  tierLabel(tier) {
    return this.TIER_LABEL[tier] || String(tier || "").replace(/_/g, " ");
  },

  // The tier, who approves at it, and the Touchless badge with its reason on hover
  tierCell(po) {
    if (!po.tier) return "";
    const badge = po.touchless ? ` <span class="badge badge-approved" title="${this.esc(po.touchless_note)}">Touchless</span>` : "";
    return `${this.esc(this.tierLabel(po.tier))}${badge}${po.approver ? `<div class="cell-muted" style="font-size:11px;">${this.esc(po.approver)}</div>` : ""}`;
  },

  touchlessPanel(po) {
    if (!po.touchless_note) return "";
    const checks = (po.touchless_checks || []).map((c) => `
      <div style="font-size:12px;padding:3px 0;"><i class="ti ti-${c.passed ? "check" : "x"}" style="color:var(${c.passed ? "--success-text" : "--warning-text"});"></i> ${this.esc(c.detail)}</div>`).join("");
    return `
      <div class="panel" style="margin-bottom:12px;">
        <div class="panel-title">${po.touchless ? 'Issued without a buyer <span class="badge badge-approved">Touchless</span>' : "Touchless check"}</div>
        <p style="margin:0 0 8px;font-size:12.5px;color:var(--text-secondary);">${this.esc(po.touchless_note)}</p>
        <details class="more"><summary>The facts checked${po.touchless_rule ? ` (${this.esc(ruleName(po.touchless_rule))})` : ""}</summary>${checks}</details>
      </div>`;
  },

  STEP_LABEL: { pending: "Waiting", waiting: "Later", approved: "Approved", rejected: "Rejected", cancelled: "Cancelled" },

  approvalPanel(po) {
    const a = po.approval || {};
    return `
      <div class="panel" style="margin-bottom:12px;">
        <div class="panel-title">Approval${po.tier ? ` · ${this.esc(this.tierLabel(po.tier))} tier` : ""}</div>
        ${po.approver ? `<p style="margin:0 0 10px;font-size:12.5px;color:var(--text-secondary);">Approver: ${this.esc(po.approver)}</p>` : ""}
        ${po.hold_reason ? UI.callout("warning", "On hold until a person accepts the price", `<p>${this.esc(po.hold_reason)}</p>`) : ""}
        ${a.decision ? `<p style="margin:0 0 10px;font-size:12.5px;color:var(--text-secondary);">${this.esc(a.decision)}</p>` : ""}
        ${UI.approvalSteps(a.steps, this.STEP_LABEL)}
        ${a.rule_code ? `<div class="cell-muted" style="font-size:11.5px;margin:10px 0;">Rule ${this.esc(a.rule_code)} version ${this.esc(a.rule_version)}</div>` : ""}
        ${a.can_act ? `<p style="margin:0 0 8px;font-size:12.5px;color:var(--text-secondary);">Approving version ${this.esc(po.version)}</p>` : ""}
        ${a.can_act && a.needs_code ? `<div id="poCodeBox" style="margin:0 0 10px;font-size:12.5px;color:var(--text-secondary);">
            <p style="margin:0 0 6px;">This order is above ${formatCurrency(a.code_above)}: approving needs a one-time code.</p>
            <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;">
              <div class="btn btn-sm" id="poCodeAsk"><i class="ti ti-mail"></i>Email me a code</div>
              <input id="poCode" inputmode="numeric" autocomplete="one-time-code" maxlength="6" placeholder="Six-digit code" style="padding:6px;width:130px;">
              <span id="poCodeSent" class="cell-muted" style="font-size:11.5px;"></span>
            </div></div>` : ""}
        ${a.can_act ? `<div class="btn-row"><div class="btn btn-primary" id="poApprove"><i class="ti ti-check"></i>Approve</div><div class="btn btn-danger" id="poReject"><i class="ti ti-x"></i>Reject</div></div>` : ""}
        <div id="poNote" style="font-size:12px;color:var(--warning-text);margin-top:8px;"></div>
        <div id="poReasonBox" style="display:none;margin-top:10px;">
          <textarea id="poReason" rows="2" placeholder="Reason (required)" style="width:100%;background:var(--surface-sunken);border:1px solid var(--border);color:var(--text-primary);border-radius:8px;padding:8px;font-size:12.5px;"></textarea>
          <div style="display:flex;gap:8px;margin-top:8px;"><div class="btn btn-primary btn-sm" id="poReasonOk">Confirm</div><div class="btn btn-sm" id="poReasonCancel">Cancel</div></div>
        </div>
      </div>`;
  },

  dispatchPanel(po) {
    if (!["approved", "sent", "acknowledged", "part_received", "received", "closed"].includes(po.status) && !po.sent_at) return "";
    const sent = po.sent_at ? `Sent to ${this.esc(po.sent_to)} on ${formatDate(po.sent_at)}.` : "Approved, not sent to the supplier yet.";
    const chasers = (po.chasers || []).filter((c) => c.version === po.version).length;
    const ack = po.acknowledged_at && po.acknowledged_version === po.version
      ? ` Acknowledged version ${this.esc(po.acknowledged_version)}${po.ack ? ` by ${this.esc(po.ack.address || po.ack.by)} (${this.esc(po.ack.via === "app" ? "recorded in the app" : po.ack.via)}${po.ack.with_code ? ", with a code" : ""})` : ""} on ${formatDate(po.acknowledged_at)}${po.promised_date ? `, last line due ${formatDate(po.promised_date)}` : ""}.${po.ack_note ? ` “${this.esc(po.ack_note)}”` : ""}`
      : (po.sent_at && po.status === "sent"
          ? ` Waiting for acknowledgement of version ${this.esc(po.version)}${po.ack_due_on ? `, due ${formatDate(po.ack_due_on)}` : ""}, ${plural(chasers, "reminder")}.`
          : "")
        + (po.ack_overdue ? ' <span style="color:var(--warning-text);">The supplier is overdue.</span>' : "");
    const open = (po.lines || []).filter((l) => ["open", "part_received"].includes(l.status || "open"));
    const dates = open.map((l) => `
      <label>Line ${this.esc(l.line_no)}: ${this.esc(l.name)}<input class="po-ack-date" data-line="${this.esc(l.line_no)}" type="date" value="${this.esc(l.due || "")}"></label>`).join("");
    return `
      <div class="panel" style="margin-bottom:12px;">
        <div class="panel-title">Supplier</div>
        <p style="margin:0 0 10px;font-size:12.5px;color:var(--text-secondary);">${sent}${ack}</p>
        ${po.can_send ? `<div class="btn btn-primary" id="poSend"><i class="ti ti-send"></i>Send to supplier</div>` : ""}
        ${po.can_resend_link ? `<div class="btn btn-sm" id="poResend" style="margin-bottom:8px;"><i class="ti ti-mail-forward"></i>Send the order again</div>` : ""}
        ${po.can_acknowledge ? `<div id="poAckBox" class="form-grid" style="grid-template-columns:repeat(auto-fit,minmax(180px,1fr));">
            <div class="cell-muted" style="grid-column:1/-1;font-size:12px;">Record version ${this.esc(po.version)}'s acknowledgement: the delivery date of each line the supplier confirmed, and how it confirmed.</div>
            ${dates}
            <label style="grid-column:1/-1;">How the supplier confirmed (required)<input id="poAckNote" placeholder="For example: confirmed by phone with Mr Rao"></label>
            <div class="btn btn-primary" id="poAck">Record acknowledgement</div></div>` : ""}
        <div id="poDispatchNote" style="font-size:12px;color:var(--warning-text);margin-top:8px;"></div>
      </div>`;
  },

  wire(po) {
    const act = async (verb, body) => {
      const res = await API.postJSON(`/api/purchase-orders/${encodeURIComponent(po.id)}/${verb}`, body);
      if (res.ok) return this.renderDetail(document.getElementById("appContent"), po.id);
      const note = document.getElementById("poNote");
      if (note) note.textContent = res.data.detail || "That did not work.";
    };
    const on = (id, fn) => { const el = document.getElementById(id); if (el) el.addEventListener("click", fn); };
    // the approver says which version they saw: a revision since then is refused, never approved unseen
    on("poApprove", () => {
      const code = document.getElementById("poCode");
      act("approve", { version: po.version, ...(code && code.value ? { code: code.value.trim() } : {}) });
    });
    on("poCodeAsk", async () => {
      const res = await API.postJSON(`/api/purchase-orders/${encodeURIComponent(po.id)}/approval-code`, {});
      const sent = document.getElementById("poCodeSent");
      if (res.ok && sent) sent.textContent = `Sent to ${res.data.sent_to}. It works once and expires in ${res.data.expires_minutes} minutes.`;
      else { const note = document.getElementById("poNote"); if (note) note.textContent = res.data.detail || "That did not work."; }
    });
    on("poSend", () => act("send", {}));
    on("poAck", async () => {
      const lines = {};
      document.querySelectorAll(".po-ack-date").forEach((el) => { if (el.value) lines[el.dataset.line] = el.value; });
      const res = await API.postJSON(`/api/purchase-orders/${encodeURIComponent(po.id)}/acknowledge`, {
        version: po.version, // the version on screen: a later one is refused, never acknowledged unseen
        lines,
        note: document.getElementById("poAckNote").value,
      });
      if (res.ok && !(res.data.warnings || []).length) return this.renderDetail(document.getElementById("appContent"), po.id);
      const note = document.getElementById("poDispatchNote");
      if (note) note.textContent = res.ok ? `Recorded. Later than ordered: ${res.data.warnings.join("; ")}` : (res.data.detail || "That did not work.");
    });
    on("poResend", async () => {
      const res = await API.postJSON(`/api/purchase-orders/${encodeURIComponent(po.id)}/resend-link`, {});
      const note = document.getElementById("poDispatchNote");
      if (note) note.textContent = res.ok ? `Version ${res.data.version} sent again to ${res.data.sent_to}.` : (res.data.detail || "That did not work.");
    });
    on("poReject", () => { document.getElementById("poReasonBox").style.display = "block"; document.getElementById("poReason").focus(); });
    on("poReasonCancel", () => { document.getElementById("poReasonBox").style.display = "none"; });
    on("poReasonOk", () => act("reject", { reason: document.getElementById("poReason").value, version: po.version }));
  },

  // The order document, its version and history, and what a buyer may change after the order was made.
  async mountChanges(po) {
    const box = document.getElementById("poChangesBox");
    if (!box) return;
    const res = await Auth.fetch(`${API.base}/api/purchase-orders/${encodeURIComponent(po.id)}/controls`);
    if (!res.ok) return;
    const c = await res.json();
    const open = po.lines;
    const history = (c.history || []).map((h) => `
      <div style="padding:6px 0;border-bottom:1px solid var(--border);font-size:12px;">
        <b>Version ${this.esc(h.version)}</b> · ${formatDate(h.at)} · ${this.esc(h.by)}
        ${(h.said || []).length ? `<div>${this.esc(h.said.join("; "))}</div>` : ""}
        <div class="cell-muted">${this.esc(h.reason)} · total ${formatCurrency(h.total_before)} → ${formatCurrency(h.total_after)}${h.outcome ? ` · ${this.esc(h.outcome)}` : ""}</div>
        ${h.acknowledged ? `<div class="cell-muted">${this.esc(h.acknowledged)}</div>` : ""}
      </div>`).join("");
    box.innerHTML = `
      <div class="panel" style="margin-bottom:12px;">
        <div class="panel-title">Order document and changes</div>
        <p style="margin:0 0 10px;font-size:12.5px;color:var(--text-secondary);">Version ${this.esc(c.version)}.
          <a href="#" id="poPdf" style="color:var(--accent-tint);">Open the order document (PDF)</a></p>
        <div style="display:flex;gap:8px;flex-wrap:wrap;">
          ${c.can_revise ? `<div class="btn btn-sm" id="poReviseOpen"><i class="ti ti-edit"></i>Change the order</div>` : ""}
          ${c.can_cancel ? `<div class="btn btn-sm" id="poCancelOpen"><i class="ti ti-x"></i>Cancel this order</div>` : ""}
          ${c.can_close_balance ? `<div class="btn btn-sm" id="poCloseOpen"><i class="ti ti-lock"></i>Close what is still to come</div>` : ""}
        </div>
        <div id="poChangeForm" style="display:none;margin-top:10px;"></div>
        <div id="poChangeNote" style="font-size:12px;color:var(--warning-text);margin-top:8px;"></div>
        ${history ? `<div style="margin-top:12px;"><div class="cell-muted" style="font-size:11.5px;margin-bottom:4px;">HISTORY</div>${history}</div>` : ""}
      </div>`;
    const reload = () => this.renderDetail(document.getElementById("appContent"), po.id);
    const form = document.getElementById("poChangeForm");
    const show = (html) => { form.innerHTML = html; form.style.display = "block"; };
    const reason = `<textarea id="poChangeReason" rows="2" placeholder="Reason (required)" style="width:100%;background:var(--surface-sunken);border:1px solid var(--border);color:var(--text-primary);border-radius:8px;padding:8px;font-size:12.5px;margin-top:8px;"></textarea>`;
    const on = (id, fn) => { const el = document.getElementById(id); if (el) el.addEventListener("click", fn); };
    const send = async (verb, body) => {
      const r = await API.postJSON(`/api/purchase-orders/${encodeURIComponent(po.id)}/${verb}`, { ...body, reason: document.getElementById("poChangeReason").value });
      if (r.ok) return reload();
      document.getElementById("poChangeNote").textContent = r.data.detail || "That did not work.";
    };
    on("poPdf", async (e) => {
      e.preventDefault();
      const r = await Auth.fetch(`${API.base}/api/purchase-orders/${encodeURIComponent(po.id)}/pdf`);
      if (r.ok) window.open(URL.createObjectURL(await r.blob()), "_blank");
    });
    on("poReviseOpen", () => {
      const o = c.options || { locations: [], payment_terms: [] };
      const places = o.locations.filter((l) => l.code !== po.ship_to)
        .map((l) => `<option value="${this.esc(l.code)}">${this.esc(l.name || l.code)} (${this.esc(l.code)}, state ${this.esc(l.state)})</option>`).join("");
      const terms = o.payment_terms.filter((t) => t.code !== po.payment_terms)
        .map((t) => `<option value="${this.esc(t.code)}">${this.esc(t.name || t.code)}${t.advance_pct ? ` (${this.esc(t.advance_pct)}% advance)` : ""}</option>`).join("");
      const rows = open.filter((l) => ["open", "part_received"].includes(l.status || "open")).map((l) => `
        <tr data-line="${this.esc(l.line_no)}">
          <td>${this.esc(l.line_no)}. ${this.esc(l.name)}<div class="cell-muted" style="font-size:11px;">${this.esc(l.qty)} ${this.esc(l.uom || "")}${l.need_by ? ` · needed by ${formatDate(l.need_by)}` : ""}</div></td>
          <td><input class="rev-qty" type="number" min="0" step="any" placeholder="New quantity" aria-label="New quantity for line ${this.esc(l.line_no)}" style="padding:6px;width:120px;"></td>
          <td><input class="rev-date" type="date" aria-label="New date needed for line ${this.esc(l.line_no)}" style="padding:6px;"></td>
          <td>${l.source === "contract" ? `<label style="font-size:12px;"><input type="checkbox" class="rev-reprice"> Use the contract price in force</label>` : ""}</td>
        </tr>`).join("");
      show(`<div class="form-grid" style="grid-template-columns:repeat(auto-fit,minmax(180px,1fr));">
          <label>Delivery place<select id="poHdrPlace"><option value="">Unchanged: ${this.esc(po.ship_to_name || po.ship_to)}</option>${places}</select></label>
          <label>Payment terms<select id="poHdrTerms"><option value="">Unchanged: ${this.esc(po.payment_terms || "none")}</option>${terms}</select></label>
          <label>Delivery terms<input id="poHdrDelivery" maxlength="200" placeholder="${this.esc(String(po.delivery_terms || "").replace(/_/g, " ") || "none")}"></label>
        </div>
        <div class="table-wrap" style="margin-top:8px;"><table class="data-table">
          <thead><tr><th>LINE</th><th>QUANTITY</th><th>DATE NEEDED</th><th>PRICE</th></tr></thead><tbody>${rows}</tbody></table></div>
        ${reason}
        <div class="btn btn-primary btn-sm" id="poRevGo" style="margin-top:8px;display:inline-block;">Make a new version</div>
        <span class="cell-muted" style="font-size:11.5px;"> Delivery place, payment terms, a higher price or quantity, a date beyond the tolerance or a higher total go for approval again; delivery terms and other changes need only the supplier's new acknowledgement.</span>`);
      on("poRevGo", () => {
        const lines = [];
        form.querySelectorAll("tr[data-line]").forEach((tr) => {
          const line = { line_no: Number(tr.dataset.line) };
          const q = tr.querySelector(".rev-qty").value, d = tr.querySelector(".rev-date").value, r = tr.querySelector(".rev-reprice");
          if (q) line.qty = q;
          if (d) line.need_by = d;
          if (r && r.checked) line.reprice = true;
          if (Object.keys(line).length > 1) lines.push(line);
        });
        const header = {};
        const place = document.getElementById("poHdrPlace").value, pay = document.getElementById("poHdrTerms").value;
        const delivery = document.getElementById("poHdrDelivery").value.trim();
        if (place) header.ship_to_location = place;
        if (pay) header.payment_terms_code = pay;
        if (delivery) header.delivery_terms = delivery;
        // the version on screen goes with the change: a later one is refused, never changed unseen
        send("revise", { version: po.version, lines, ...(Object.keys(header).length ? { header } : {}) });
      });
    });
    on("poCancelOpen", () => {
      show(`${reason}
        <label style="font-size:12.5px;display:block;margin-top:8px;"><input type="checkbox" id="poCancelAgain"> Order these lines again (a different supplier or price is wanted)</label>
        <div class="btn btn-primary btn-sm" id="poCancelGo" style="margin-top:8px;display:inline-block;">Cancel the order</div>`);
      on("poCancelGo", () => send("cancel", { reorder: document.getElementById("poCancelAgain").checked }));
    });
    on("poCloseOpen", () => {
      show(`<p style="margin:0;font-size:12.5px;color:var(--text-secondary);">The part the supplier has not delivered is closed. It no longer counts as stock on the way, and the supplier is told.</p>${reason}
        <div class="btn btn-primary btn-sm" id="poCloseGo" style="margin-top:8px;display:inline-block;">Close what is still to come</div>`);
      on("poCloseGo", () => send("close-balance", {}));
    });
  },

  APPROVAL_LABEL: { approved: "Approved", carried: "Approval carried", waiting: "Waiting for approval" },
  ACK_VIA: { link: "by link", app: "in the app", email: "by email" },
  MONEY_FIELDS: ["taxable", "cgst", "sgst", "igst", "total", "unit_price", "amount"],

  // Every version of the order: who made it and why, its approval, when it went to the supplier and the document as
  // sent, how it was acknowledged; and any two versions side by side.
  async mountVersions(po) {
    const box = document.getElementById("poVersionsBox");
    if (!box) return;
    const res = await Auth.fetch(`${API.base}/api/purchase-orders/${encodeURIComponent(po.id)}/versions`);
    if (!res.ok) return;
    const rows = await res.json();
    if (!box.isConnected) return; // the page was drawn again meanwhile; its own call fills the new box
    if (!rows.length) { box.innerHTML = ""; return; }
    const options = (pick) => rows.map((v) => `<option value="${this.esc(v.version)}"${v.version === pick ? " selected" : ""}>Version ${this.esc(v.version)}</option>`).join("");
    // by default the current version against the one last approved (what an approver needs), else the one before it
    const current = rows[0].version;
    const approved = po.approved_version && po.approved_version !== current ? po.approved_version : null;
    const from = approved || (rows[1] ? rows[1].version : current);
    box.innerHTML = `
      <div class="panel" style="margin-bottom:12px;">
        <div class="panel-title">Versions</div>
        ${rows.map((v) => `
          <div style="padding:6px 0;border-bottom:1px solid var(--border);font-size:12px;">
            <b>Version ${this.esc(v.version)}</b>${v.current ? " (current)" : ""} · ${formatDate(v.made_at)} · ${this.esc(v.made_by_name)}
            ${v.approval ? ` · ${this.esc(this.APPROVAL_LABEL[v.approval] || v.approval)}` : ""}
            ${v.document ? ` · <a href="#" class="po-ver-pdf" data-version="${this.esc(v.version)}" style="color:var(--accent-tint);">PDF</a>` : ""}
            <div class="cell-muted">${v.reason ? `${this.esc(v.reason)} · ` : ""}${v.sent_at ? `sent ${formatDate(v.sent_at)} to ${this.esc(v.sent_to)}` : "not sent"} · ${v.acknowledged_at ? `acknowledged ${this.esc(this.ACK_VIA[v.ack_via] || "")}${v.ack_with_code ? " with a code" : ""}${v.ack_address ? ` by ${this.esc(v.ack_address)}` : ""} on ${formatDate(v.acknowledged_at)}${Object.keys(v.ack_lines || {}).length ? ` (${Object.entries(v.ack_lines).map(([n, d]) => `line ${this.esc(n)} ${formatDate(d)}`).join(", ")})` : ""}${v.ack_note ? ` “${this.esc(v.ack_note)}”` : ""}` : "not acknowledged"}${(v.material || []).length ? ` · material: ${this.esc(v.material.join("; "))}` : ""}</div>
          </div>`).join("")}
        <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:8px;font-size:12.5px;">
          <label for="poCmpFrom">Compare</label> <select id="poCmpFrom" style="padding:6px;">${options(from)}</select>
          <label for="poCmpTo">with</label> <select id="poCmpTo" style="padding:6px;">${options(current)}</select>
          <div class="btn btn-sm" id="poCmpGo">Compare</div></div>
        <div id="poCmpOut" style="margin-top:8px;"></div>
      </div>`;
    box.querySelectorAll(".po-ver-pdf").forEach((a) => a.addEventListener("click", async (e) => {
      e.preventDefault();
      const r = await Auth.fetch(`${API.base}/api/purchase-orders/${encodeURIComponent(po.id)}/versions/${a.dataset.version}/pdf`);
      if (r.ok) window.open(URL.createObjectURL(await r.blob()), "_blank");
    }));
    const compare = () => {
      const a = box.querySelector("#poCmpFrom").value, b = box.querySelector("#poCmpTo").value;
      if (a !== b) return this.showComparison(po, a, b, box.querySelector("#poCmpOut"));
      box.querySelector("#poCmpOut").innerHTML = `<div class="cell-muted" style="font-size:12px;">${rows.length > 1 ? "Pick two different versions." : "This is the only version so far: nothing to compare yet."}</div>`;
    };
    box.querySelector("#poCmpGo").addEventListener("click", compare);
    compare();
  },

  cmpValue(field, value) {
    if (value === null || value === undefined || value === "") return '<span class="cell-muted">none</span>';
    if (this.MONEY_FIELDS.includes(field)) return formatCurrency(value);
    if (field === "need_by") return formatDate(value);
    return this.esc(value);
  },

  async showComparison(po, a, b, out) {
    if (!out) return;
    const r = await Auth.fetch(`${API.base}/api/purchase-orders/${encodeURIComponent(po.id)}/versions/compare?from=${encodeURIComponent(a)}&to=${encodeURIComponent(b)}`);
    const data = await r.json();
    if (!out.isConnected) return;
    if (!r.ok) { out.innerHTML = `<div class="cell-muted" style="font-size:12px;">${this.esc(data.detail || "These versions cannot be compared.")}</div>`; return; }
    const changed = "background:var(--warning-bg, rgba(255,196,0,.12));font-weight:600;";
    const row = (what, f) => `<tr><td>${what}</td><td>${this.cmpValue(f.field, f.before)}</td><td style="${changed}">${this.cmpValue(f.field, f.after)}</td></tr>`;
    const body = [
      ...data.header.map((f) => row(this.esc(f.label), f)),
      ...data.lines.flatMap((l) => l.fields.map((f) => row(`Line ${this.esc(l.line_no)}: ${this.esc(f.label)}`, f))),
      ...data.charges.flatMap((c) => c.fields.map((f) => row(`${c.type === "freight" ? "Freight" : "Packing and forwarding"}: ${this.esc(f.label)}`, f))),
    ].join("");
    out.innerHTML = body
      ? `<div class="table-wrap"><table class="data-table"><thead><tr><th>WHAT</th><th>VERSION ${this.esc(data.from)}</th><th>VERSION ${this.esc(data.to)}</th></tr></thead><tbody>${body}</tbody></table></div>`
      : '<div class="cell-muted" style="font-size:12px;">Nothing on the order differs between these versions.</div>';
  },

  CHANGE_STATUS: { proposed: "To decide", waiting_approval: "Waiting", accepted: "Accepted", rejected: "Rejected", withdrawn: "Withdrawn", stale: "Stale: the order changed since" },

  // What the supplier asked for (old value to new, a buyer accepts or rejects) and what it says it has shipped.
  async mountSupplierAnswer(po) {
    const box = document.getElementById("poAnswerBox");
    if (!box) return;
    const res = await Auth.fetch(`${API.base}/api/purchase-orders/${encodeURIComponent(po.id)}/supplier-answer`);
    if (!res.ok) return;
    const a = await res.json();
    if (!a.changes.length && !a.dispatch_notes.length) { box.innerHTML = ""; return; }
    const changes = a.changes.map((c) => `
      <div style="padding:8px 0;border-bottom:1px solid var(--border);font-size:12.5px;" data-change="${this.esc(c.id)}">
        <div style="display:flex;justify-content:space-between;gap:12px;">
          <div><span style="color:var(--text-primary);">${this.esc(c.what)}</span>
            <span class="cell-muted"> · by ${c.source === "email" ? "email" : "the supplier's link"} · ${formatDate(c.at)}</span>
            ${c.kind === "price" && c.rise_pct !== null && c.rise_pct !== undefined ? `<div class="cell-muted" style="font-size:11.5px;">${this.esc(c.rise_pct)}% above the first approved price ${formatCurrency(c.base_value)}</div>` : ""}
            ${c.kind === "price" && c.status === "proposed" ? `<div class="cell-muted" style="font-size:11.5px;">${c.within_tolerance ? "Within the price tolerance: yours to accept" : "Above the price tolerance: accepting sends it to the purchase manager"}</div>` : ""}
            ${c.reason ? `<div class="cell-muted" style="font-size:11.5px;">“${this.esc(c.reason)}”</div>` : ""}
            ${c.waiting_for ? `<div class="cell-muted" style="font-size:11.5px;">Waiting for ${this.esc(c.waiting_for)}</div>` : ""}
            ${c.decision_note ? `<div class="cell-muted" style="font-size:11.5px;">${this.esc(c.decision_note)}</div>` : ""}
            ${c.version_made ? `<div class="cell-muted" style="font-size:11.5px;">Made version ${this.esc(c.version_made)}</div>` : ""}
          </div>
          <span class="badge badge-${c.status === "accepted" ? "approved" : c.status === "rejected" ? "rejected" : "waiting"}" style="white-space:nowrap;align-self:flex-start;">${this.esc(this.CHANGE_STATUS[c.status] || c.status)}</span>
        </div>
        ${c.can_decide ? `<div style="display:flex;gap:8px;margin-top:6px;align-items:center;flex-wrap:wrap;">
          <input class="chg-note" placeholder="Reason (needed to reject)" style="padding:6px;font-size:12.5px;flex:1;min-width:160px;">
          <div class="btn btn-primary btn-sm chg-yes">Accept</div><div class="btn btn-sm chg-no">Reject</div></div>` : ""}
      </div>`).join("");
    const notes = a.dispatch_notes.map((n) => `
      <div style="padding:6px 0;border-bottom:1px solid var(--border);font-size:12.5px;">
        Shipped ${formatDate(n.shipped_on)}: ${n.lines.map((l) => `${this.esc(l.qty)} ${this.esc(l.uom || "")} ${this.esc(l.name)}`).join(", ")}
        <div class="cell-muted" style="font-size:11.5px;">${[n.vehicle && `vehicle ${this.esc(n.vehicle)}`, n.lr_no && `LR/AWB ${this.esc(n.lr_no)}`, n.invoice_no && `invoice ${this.esc(n.invoice_no)}`, n.expected_on && `expected ${formatDate(n.expected_on)}`].filter(Boolean).join(" · ")}</div>
      </div>`).join("");
    box.innerHTML = `
      <div class="panel" style="margin-bottom:12px;">
        <div class="panel-title">Supplier's answer</div>
        ${changes ? `<div class="cell-muted" style="font-size:11.5px;margin-bottom:4px;">CHANGES ASKED FOR</div>${changes}` : ""}
        ${notes ? `<div class="cell-muted" style="font-size:11.5px;margin:10px 0 4px;">DISPATCHED</div>${notes}` : ""}
        <div id="poAnswerNote" style="font-size:12px;color:var(--warning-text);margin-top:8px;"></div>
      </div>`;
    const decide = async (row, accept) => {
      const r = await API.postJSON(`/api/supplier-changes/${row.dataset.change}/decide`, { accept, note: row.querySelector(".chg-note").value });
      if (r.ok) return this.renderDetail(document.getElementById("appContent"), po.id);
      document.getElementById("poAnswerNote").textContent = r.data.detail || "That did not work.";
    };
    box.querySelectorAll("[data-change]").forEach((row) => {
      const yes = row.querySelector(".chg-yes"), no = row.querySelector(".chg-no");
      if (yes) yes.addEventListener("click", () => decide(row, true));
      if (no) no.addEventListener("click", () => decide(row, false));
    });
  },

  async renderDetail(container, id) {
    container.innerHTML = `<div class="page-header"><h1>${this.esc(id)}</h1></div><div class="panel">Loading…</div>`;
    try {
      const po = await API.getPurchaseOrder(id);
      if (po.error) { container.innerHTML = `<div class="empty-state">${this.esc(po.error)}</div>`; return; }
      const taxCols = Number(po.igst) > 0 ? ["IGST"] : (Number(po.cgst) > 0 ? ["CGST", "SGST"] : []);
      const taxCell = (l) => (Number(po.igst) > 0 ? `<td>${formatCurrency(l.igst)}</td>` : (Number(po.cgst) > 0 ? `<td>${formatCurrency(l.cgst)}</td><td>${formatCurrency(l.sgst)}</td>` : ""));
      // what someone has to do on this order comes first; the record of what happened sits at the side
      const approvalActs = (po.approval || {}).can_act || !!po.hold_reason;
      const dispatchActs = po.can_send || po.can_acknowledge;
      container.innerHTML = `
        <div class="page-header">
          ${UI.backLink("#/purchase-orders", "Purchase orders")}
          <h1>${this.esc(po.id)} · ${this.esc(po.supplier)}</h1>
          <p>${statusBadge(po.status)}${po.touchless ? ' <span class="badge badge-approved">Touchless</span>' : ""} · ${formatCurrency(po.total)} · for <a href="#/requisitions/${this.esc(po.request_no)}">${this.esc(po.request_no)}</a>
             ${po.rfq_no ? ` · <a href="#/rfqs/${this.esc(po.rfq_no)}">${this.esc(po.rfq_no)}</a>` : ""}
             ${po.contract_code ? ` · contract ${this.esc(po.contract_code)}` : ""}</p>
        </div>
        ${FlowStrip.forOrder(po)}
        ${po.cancel_reason ? UI.callout("danger", "", `<p><strong>Cancelled:</strong> ${this.esc(po.cancel_reason)}</p>`) : ""}
        <div class="doc-layout">
        <div class="doc-main">
        ${approvalActs ? this.approvalPanel(po) : ""}
        ${dispatchActs ? this.dispatchPanel(po) : ""}
        <div id="poAnswerBox"></div>
        <div class="table-wrap" style="margin-bottom:12px;">
          <table class="data-table">
            <thead><tr><th>ITEM</th><th>QTY</th><th>PRICE</th><th>GST</th><th>TAXABLE</th>${taxCols.map((c) => `<th>${c}</th>`).join("")}</tr></thead>
            <tbody>
              ${po.lines.map((l) => `
                <tr>
                  <td>${this.esc(l.name)}<div class="cell-muted" style="font-size:11px;">${this.esc(l.item_code || "")} · ${l.source === "contract" ? `contract ${this.esc(l.source_ref)}${l.contract ? ` version ${this.esc(l.contract.version)}` : ""}` : l.source === "direct" ? `direct buy (${this.esc(String(l.source_ref || "").replace("_", " "))})` : `quote ${this.esc(l.source_ref)}`}</div></td>
                  <td>${this.esc(l.qty)} <span class="cell-muted">${this.esc(l.uom)}</span>${l.due ? `<div class="cell-muted" style="font-size:11px;">due ${formatDate(l.due)} (${l.due_source === "promised" ? "promised" : "needed"})</div>` : ""}</td>
                  <td>${formatCurrency(l.unit_price)}${l.est_price ? `<div class="cell-muted" style="font-size:11px;">estimate ${formatCurrency(l.est_price)}</div>` : ""}</td>
                  <td class="cell-muted">${this.esc(l.gst_rate)}%</td>
                  <td>${formatCurrency(l.taxable)}</td>
                  ${taxCell(l)}
                </tr>`).join("")}
              ${po.charges.map((c) => `
                <tr>
                  <td>${c.type === "freight" ? "Freight" : "Packing and forwarding"}</td><td></td><td></td>
                  <td class="cell-muted">${this.esc(c.gst_rate)}%</td><td>${formatCurrency(c.amount)}</td>
                  ${Number(po.igst) > 0 ? `<td>${formatCurrency(c.igst)}</td>` : (Number(po.cgst) > 0 ? `<td>${formatCurrency(c.cgst)}</td><td>${formatCurrency(c.sgst)}</td>` : "")}
                </tr>`).join("")}
            </tbody>
          </table>
          <div class="table-foot">${UI.totals([["Taxable", po.taxable], ["IGST", po.igst], ["CGST", po.cgst], ["SGST", po.sgst], ["Total", po.total]])}</div>
        </div>
        <div id="supplierMessagesBox"></div>
        </div>
        <div class="doc-side">
        <div class="panel">
          <div class="panel-title">Order details</div>
          ${UI.facts([
            ["Supplier", `${this.esc(po.supplier)}<div class="cell-muted">GSTIN ${this.esc(po.supplier_gstin || "none")}</div>`],
            ["Deliver to", `${this.esc(po.ship_to_name || po.ship_to)} (${this.esc(po.ship_to)})<div class="cell-muted">our GSTIN ${this.esc(po.buyer_gstin || "none")}</div>`],
            ["Payment terms", this.esc(po.payment_terms || "")],
            ["Delivery terms", this.esc(String(po.delivery_terms || "").replace(/_/g, " "))],
          ])}
        </div>
        ${approvalActs ? "" : this.approvalPanel(po)}
        ${dispatchActs ? "" : this.dispatchPanel(po)}
        ${this.touchlessPanel(po)}
        <div id="poChangesBox"></div>
        <div id="poVersionsBox"></div>
        </div>
        </div>
        <div id="poTraceBox" style="margin-top:12px;"></div>
      `;
      this.wire(po);
      TracePanel.mount(document.getElementById("poTraceBox"), po.id);
      this.mountChanges(po);
      this.mountVersions(po);
      if (po.sent_at) this.mountSupplierAnswer(po);
      if (po.sent_at) SupplierMessages.mount(document.getElementById("supplierMessagesBox"), "orders", po.id);
    } catch {
      container.innerHTML = `<div class="empty-state">Couldn't load this purchase order.</div>`;
    }
  },
};
