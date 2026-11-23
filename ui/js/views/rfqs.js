const RfqsView = {
  esc(value) {
    return String(value ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  },

  STATE_BADGE: { invited: "badge-sent", replied: "badge-received", declined: "badge-draft", failed: "badge-rejected", "not yet sent": "badge-waiting" },

  async render(container, params) {
    if (params && params.id) return this.renderDetail(container, params.id);
    return this.renderList(container);
  },

  async renderList(container) {
    container.innerHTML = `
      <div class="page-header" style="display:flex;justify-content:space-between;align-items:flex-end;gap:12px;flex-wrap:wrap;">
        <div><h1>Quotations</h1>
        <p>Prices asked from suppliers for approved requests or for a period, and the answers that came back</p></div>
        <div style="display:flex;gap:8px;">
          <div class="btn btn-sm" id="confirmSelected" style="display:none;"><i class="ti ti-send"></i>Confirm and send selected</div>
          <div class="btn btn-primary btn-sm" id="newPeriodRfq"><i class="ti ti-calendar-plus"></i>New period RFQ</div>
        </div>
      </div>
      <div id="periodForm"></div>
      <div id="confirmNote" style="font-size:12px;color:var(--warning-text);margin-bottom:8px;"></div>
      <div class="table-wrap" id="tableWrap"><div class="empty-state">Loading…</div></div>
    `;
    document.getElementById("newPeriodRfq").addEventListener("click", () => this.periodForm());
    try {
      const rows = await API.listRfqs();
      const wrap = document.getElementById("tableWrap");
      if (!wrap) return; // the person has already gone to another page
      if (!rows.length) {
        wrap.innerHTML = `<div class="empty-next"><strong>No quotations yet.</strong><br>When an approved request has items with no contract price, a request for quotation is made here.<br>Next: <a href="#/requisitions" style="color:var(--accent-tint);">open the approved requests</a>.</div>`;
        return;
      }
      wrap.innerHTML = `
        <table class="data-table">
          <thead><tr><th></th><th>NUMBER</th><th>KIND</th><th>TITLE</th><th>SUPPLIERS</th><th>REPLY BY</th><th>STATUS</th><th>QUOTES</th></tr></thead>
          <tbody>
            ${rows.map((r) => `
              <tr data-id="${this.esc(r.id)}">
                <td>${r.waiting_to_send ? `<input type="checkbox" class="sendPick" value="${this.esc(r.id)}" aria-label="Select ${this.esc(r.id)} to send">` : ""}</td>
                <td class="cell-muted" style="white-space:nowrap;">${this.esc(r.id)}</td>
                <td class="cell-muted">${r.kind === "period" ? "Period" : "Request"}</td>
                <td>${this.esc(r.title)}</td>
                <td class="cell-muted">${this.esc(r.vendor_count)} suppliers</td>
                <td class="cell-muted">${formatDate(r.due_date)}</td>
                <td>${statusBadge(r.status)}${r.waiting_to_send ? `<div class="cell-muted" style="font-size:11px;margin-top:3px;">waiting for a buyer to confirm sending</div>` : ""}</td>
                <td class="cell-muted">${this.esc(r.quote_count)} of ${this.esc(r.vendor_count)}</td>
              </tr>
            `).join("")}
          </tbody>
        </table>
      `;
      wrap.querySelectorAll("tr[data-id]").forEach((tr) => {
        tr.addEventListener("click", (e) => {
          if (e.target.classList.contains("sendPick")) return;
          window.location.hash = `#/rfqs/${tr.dataset.id}`;
        });
      });
      const button = document.getElementById("confirmSelected");
      const picked = () => [...wrap.querySelectorAll(".sendPick:checked")].map((el) => el.value);
      wrap.querySelectorAll(".sendPick").forEach((el) => el.addEventListener("change", () => {
        button.style.display = picked().length ? "" : "none";
      }));
      button.addEventListener("click", async () => {
        const res = await API.postJSON("/api/rfqs/confirm-send", { rfqs: picked() });
        const note = document.getElementById("confirmNote");
        if (!res.ok) { if (note) note.textContent = res.data.detail || "Not sent."; return; }
        const refused = res.data.results.filter((x) => x.status !== "sent");
        await this.renderList(container);
        const after = document.getElementById("confirmNote");
        if (after && refused.length) after.textContent = refused.map((x) => `${x.rfq_no}: ${x.reason}`).join("; ");
      });
    } catch {
      const gone = document.getElementById("tableWrap");
      if (gone) gone.innerHTML = `<div class="empty-state">Couldn't load RFQs.</div>`;
    }
  },

  async periodForm() {
    const box = document.getElementById("periodForm");
    if (!box) return;
    const form = await API.getJSON("/api/rfqs/period/form");
    if (form.error || form.detail) { box.innerHTML = `<div class="panel" style="margin-bottom:12px;">${this.esc(form.detail || form.error)}</div>`; return; }
    const input = "background:var(--surface-sunken);border:1px solid var(--border);color:var(--text-primary);border-radius:6px;padding:6px 8px;font-size:12.5px;";
    const items = form.items.map((i) => `<option value="${this.esc(i.code)}">${this.esc(i.code)} · ${this.esc(i.name)} (${this.esc(i.uom)})</option>`).join("");
    const line = () => `<tr class="pLine"><td><select class="pItem" style="${input}">${items}</select></td>
      <td><input type="number" min="0" step="0.001" class="pQty" style="${input}width:110px;"></td>
      <td><div class="btn btn-sm pSuggest">Suggest</div></td><td class="cell-muted pWords" style="font-size:11.5px;"></td></tr>`;
    box.innerHTML = `
      <div class="panel" style="margin-bottom:12px;">
        <div class="panel-title">New period RFQ</div>
        <div style="font-size:12.5px;color:var(--text-secondary);margin-bottom:8px;">Prices for a period, with no request. The quantities are an estimate, not a commitment; a buyer confirms sending.</div>
        <div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:10px;">
          <label style="font-size:12px;color:var(--text-muted);">Location <select id="pLocation" style="${input}">${form.locations.map((l) => `<option value="${this.esc(l.code)}">${this.esc(l.name)}</option>`).join("")}</select></label>
          <label style="font-size:12px;color:var(--text-muted);">From <input type="date" id="pFrom" style="${input}"></label>
          <label style="font-size:12px;color:var(--text-muted);">To <input type="date" id="pTo" style="${input}"></label>
        </div>
        <table class="data-table"><thead><tr><th>ITEM</th><th>QUANTITY</th><th></th><th>ENGINE'S SUGGESTION</th></tr></thead><tbody id="pLines">${line()}</tbody></table>
        <div style="display:flex;gap:8px;margin-top:10px;">
          <div class="btn btn-sm" id="pAdd"><i class="ti ti-plus"></i>Add an item</div>
          <div class="btn btn-primary btn-sm" id="pSave">Make the period RFQ</div>
          <div class="btn btn-sm" id="pCancel">Cancel</div>
        </div>
        <div id="pNote" style="font-size:12px;color:var(--warning-text);margin-top:8px;"></div>
      </div>`;
    const wireLine = (tr) => tr.querySelector(".pSuggest").addEventListener("click", async () => {
      const q = new URLSearchParams({ item_code: tr.querySelector(".pItem").value, location_code: document.getElementById("pLocation").value,
        period_from: document.getElementById("pFrom").value, period_to: document.getElementById("pTo").value });
      const got = await API.getJSON(`/api/rfqs/period/suggest?${q}`);
      if (got.suggested_qty === undefined) { tr.querySelector(".pWords").textContent = got.detail || "Give the period first."; return; }
      tr.querySelector(".pQty").value = got.suggested_qty;
      tr.querySelector(".pWords").textContent = got.suggestion;
    });
    box.querySelectorAll(".pLine").forEach(wireLine);
    document.getElementById("pAdd").addEventListener("click", () => {
      document.getElementById("pLines").insertAdjacentHTML("beforeend", line());
      wireLine(document.getElementById("pLines").lastElementChild);
    });
    document.getElementById("pCancel").addEventListener("click", () => { box.innerHTML = ""; });
    document.getElementById("pSave").addEventListener("click", async () => {
      const lines = [...box.querySelectorAll(".pLine")].map((tr) => ({ item_code: tr.querySelector(".pItem").value, qty: tr.querySelector(".pQty").value }));
      const res = await API.postJSON("/api/rfqs/period", { location_code: document.getElementById("pLocation").value,
        period_from: document.getElementById("pFrom").value, period_to: document.getElementById("pTo").value, lines });
      if (res.ok) { window.location.hash = `#/rfqs/${res.data.rfq_no}`; return; }
      document.getElementById("pNote").textContent = res.data.detail || "Not made.";
    });
  },

  awardPanel(r) {
    const d = r.award_decision;
    if (!d) return "";
    const money = (v) => formatCurrency(v);
    const tax = Number(d.igst) ? `IGST ${money(d.igst)}` : `CGST ${money(d.cgst)} + SGST ${money(d.sgst)}`;
    const said = { waiting: `Waiting for ${this.esc(d.approver_name)} (${this.esc(d.rule)})`, approved: "Approved", rejected: "Rejected", withdrawn: "Withdrawn by the buyer", stale: "Stale: the buyer selects again" }[d.status] || this.esc(d.status);
    const letters = (d.letters || []).map((l) => `${this.esc(l.kind === "award" ? "Award letter" : "Regret note")} to ${this.esc(l.supplier_code)}`).join(" · ");
    const contracts = (d.contracts || []).map((c) => `<a href="#/contracts/${this.esc(c)}" style="color:var(--accent-tint);">${this.esc(c)}</a>`).join(", ");
    return `<div class="panel" style="margin-bottom:12px;">
      <div class="panel-title">Award, round ${this.esc(d.round)} · ${said}</div>
      <div style="font-size:12.5px;margin-bottom:6px;">${money(d.value_taxable)} before GST, ${tax}: ${money(d.value_total)} with GST · proposed by ${this.esc(d.proposed_by)}</div>
      ${d.note ? `<div style="font-size:12.5px;color:var(--text-secondary);margin-bottom:6px;">${this.esc(d.note)}${d.note_source === "ai" ? ` <span class="cell-muted">(reworded by the assistant from the figures)</span>` : ""}</div>` : ""}
      ${d.decision_note ? `<div class="cell-muted" style="font-size:12px;">${this.esc(d.decision_note)}</div>` : ""}
      ${letters ? `<div class="cell-muted" style="font-size:12px;">${letters}</div>` : ""}
      ${contracts ? `<div style="font-size:12.5px;margin-top:4px;">Draft rate contract: ${contracts} (the supplier accepts it, then a second person activates it)</div>` : ""}
      ${d.can_decide ? `<textarea id="awardDecisionNote" rows="2" placeholder="Note (a reason is needed to reject)" style="width:100%;margin:8px 0;background:var(--surface-sunken);border:1px solid var(--border);color:var(--text-primary);border-radius:8px;padding:8px;font-size:12.5px;"></textarea>
        <div style="display:flex;gap:8px;"><div class="btn btn-primary btn-sm" id="awardApprove"><i class="ti ti-check"></i>Approve award</div><div class="btn btn-sm" id="awardReject">Reject</div></div>` : ""}
      ${d.can_withdraw ? `<div style="display:flex;gap:8px;margin-top:8px;"><input id="awardWithdrawReason" placeholder="Why you withdraw it" style="flex:1;background:var(--surface-sunken);border:1px solid var(--border);color:var(--text-primary);border-radius:6px;padding:6px 8px;font-size:12.5px;"><div class="btn btn-sm" id="awardWithdraw">Withdraw</div></div>` : ""}
      <div id="awardDecisionMsg" style="font-size:12px;color:var(--warning-text);margin-top:8px;"></div>
    </div>`;
  },

  FLAG_NOTE: "does not comply",

  comparisonPanel(r) {
    const c = r.comparison;
    if (!c) return "";
    const head = `<div class="panel-title">Quotes \u00b7 ${c.quotes_received} of ${c.invited} suppliers have replied${c.min_quotes > c.quotes_received ? ` (${c.min_quotes} are wanted)` : ""}</div>
      ${c.quotes_received ? `<div style="font-size:12.5px;margin-bottom:8px;">${this.esc(c.counting)} of ${this.esc(c.quotes_received)} quotes count (${this.esc(c.min_quotes)} wanted)</div>` : ""}
      ${this.invitePanel(r)}`;
    if (!c.quotes_received) {
      return `<div class="panel" style="margin-bottom:12px;">${head}<div class="empty-state">No quotes received yet.</div>
        ${c.can_quote ? `<div class="btn btn-primary btn-sm" id="addQuoteBtn" style="display:inline-flex;"><i class="ti ti-plus"></i>Enter a quote</div>` : ""}</div>`;
    }
    const waiting = c.waiting_for.length ? `<div class="cell-muted" style="font-size:11.5px;margin-bottom:8px;">Still to reply: ${c.waiting_for.map((s) => this.esc(s)).join(", ")}</div>` : "";
    const nameOf = (code) => (c.suppliers.find((s) => s.code === code) || {}).name || code;
    const lines = c.lines.map((l) => {
      const open = c.can_award && !l.awarded;
      const splitRec = l.split && l.split.worthwhile;
      const splitRow = l.split ? `
        <tr style="${splitRec ? "background:var(--success-bg);" : ""}">
          ${open ? `<td><input type="radio" name="pick-${l.rfq_line_no}" value="__split__" data-parts='${this.esc(JSON.stringify(l.split.parts.map((p) => [p.supplier_code, p.qty])))}' ${splitRec ? "checked" : ""}></td>` : ""}
          <td colspan="5" style="font-size:12px;">Split: ${l.split.parts.map((p) => `${this.esc(nameOf(p.supplier_code))} ${this.esc(p.qty)}`).join(" + ")}${splitRec ? ' <span class="badge badge-approved" style="font-size:10px;">recommended</span>' : ""}<div class="cell-muted" style="font-size:11px;">${this.esc(l.split.reason)}</div></td>
        </tr>` : "";
      return `
      <div style="margin-bottom:14px;">
        <div style="font-size:13px;margin-bottom:6px;">${this.esc(l.name)} <span class="cell-muted">\u00b7 ${this.esc(l.qty)} ${this.esc(l.uom)}${l.need_by ? ` \u00b7 needed by ${formatDate(l.need_by)}` : ""} \u00b7 ${this.esc(l.qualifying)} qualifying</span></div>
        ${l.awarded ? `<div style="font-size:12px;color:var(--success-text);margin-bottom:6px;">Supplier selected: ${l.awarded_parts.map((p) => `${this.esc(nameOf(p.supplier_code))}${l.awarded_parts.length > 1 ? ` (${this.esc(p.qty)})` : ""}`).join(", ")}</div>` : ""}
        <table class="data-table">
          <thead><tr>${open ? "<th></th>" : ""}<th>RANK</th><th>SUPPLIER</th><th>PRICE</th><th>TOTAL COST</th><th>DAYS</th><th>NOTES</th></tr></thead>
          <tbody>
            ${l.offers.map((o) => `
              <tr style="${o.supplier_code === l.recommended ? "background:var(--success-bg);" : ""}">
                ${open ? `<td><input type="radio" name="pick-${l.rfq_line_no}" value="${this.esc(o.supplier_code)}" ${o.supplier_code === l.recommended ? "checked" : ""}${o.counts === false ? " disabled" : ""}></td>` : ""}
                <td class="cell-muted">${o.rank ?? (o.flags.length ? "dropped" : "part")}</td>
                <td>${this.esc(o.supplier_name)}${o.supplier_code === l.recommended ? ' <span class="badge badge-approved" style="font-size:10px;">recommended</span>' : ""}${o.counts === false ? ` <span class="badge badge-pending" style="font-size:10px;">${this.esc(o.why_not || "does not count")}</span>` : ""}${o.payment_days ? `<div class="cell-muted" style="font-size:11px;">pays in ${this.esc(o.payment_days)} days</div>` : ""}</td>
                <td>${formatCurrency(o.net_price)}${Number(o.discount_pct) > 0 ? `<div class="cell-muted" style="font-size:11px;">${formatCurrency(o.unit_price)} less ${this.esc(o.discount_pct)}%</div>` : ""}</td>
                <td>${formatCurrency(o.cost)}<div class="cell-muted" style="font-size:11px;">${formatCurrency(o.value)}${Number(o.charges) > 0 ? ` + ${formatCurrency(o.charges)} freight and packing` : ""}; GST ${formatCurrency(o.tax)} (${this.esc(o.gst_rate)}%)${o.cost === o.landed ? " counted" : " shown, claimed back"}</div>${o.score !== null ? `<div class="cell-muted" style="font-size:11px;">score ${this.esc(o.score)} of 100</div>` : ""}</td>
                <td class="cell-muted">${o.delivery_days ?? "\u2014"}</td>
                <td style="font-size:11.5px;color:var(--warning-text);">${o.flags.length ? `Dropped: ${o.flags.map((f) => this.esc(f)).join("; ")}` : ""}${o.qty_offered ? `<div>offers only ${this.esc(o.qty_offered)}</div>` : ""}${o.questions.length ? `<div>Ask the supplier: price is ${o.questions.map((q) => this.esc(q)).join("; ")}</div>` : ""}${o.last_price ? `<div class="cell-muted">last paid ${formatCurrency(o.last_price)}</div>` : ""}</td>
              </tr>`).join("")}
            ${splitRow}
          </tbody>
        </table>
        <div class="cell-muted" style="font-size:11.5px;margin-top:4px;">${this.esc(l.why)}</div>
      </div>`;
    }).join("");
    const actions = c.can_award && c.lines.some((l) => !l.awarded)
      ? `<div class="cell-muted" style="font-size:11.5px;">Compared on ${this.esc(c.basis)} (${this.esc(ruleName(c.rule))})</div>
         <div id="reasonBoxAward" style="margin:8px 0;">
           <textarea id="awardReason" rows="2" placeholder="Reason (needed when you select other than the recommended, cheapest qualifying quote, or when fewer qualifying quotes came in than wanted)" style="width:100%;background:var(--surface-sunken);border:1px solid var(--border);color:var(--text-primary);border-radius:8px;padding:8px;font-size:12.5px;"></textarea>
         </div>
         ${r.kind === "period" ? `<label style="font-size:12px;display:block;margin-bottom:8px;"><input type="checkbox" id="regretNotes" checked> Send regret notes to the others</label>` : ""}
         <div style="display:flex;gap:8px;"><div class="btn btn-primary btn-sm" id="awardBtn"><i class="ti ti-check"></i>${r.kind === "period" ? "Propose award" : "Select these suppliers"}</div>
         ${c.can_quote ? `<div class="btn btn-sm" id="addQuoteBtn"><i class="ti ti-plus"></i>Enter another quote</div>` : ""}</div>`
      : (c.can_quote ? `<div class="btn btn-sm" id="addQuoteBtn" style="display:inline-flex;"><i class="ti ti-plus"></i>Enter another quote</div>` : "");
    return `<div class="panel" style="margin-bottom:12px;">${head}${waiting}${lines}${actions}<div id="awardNote" style="font-size:12px;color:var(--warning-text);margin-top:8px;"></div></div>`;
  },

  // D-03: a prospect of the RFQ's category may be invited; its quote counts once its identity is checked
  invitePanel(r) {
    const c = r.comparison;
    const can = c && c.can_quote && (r.prospects_to_invite || []).length;
    if (!can) return "";
    return `<div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;font-size:12.5px;margin-bottom:10px;">Invite a prospect
      <select id="prospectPick" style="padding:5px;">${r.prospects_to_invite.map((p) => `<option value="${this.esc(p.code)}">${this.esc(p.name)} (${this.esc(p.code)})${p.checked ? "" : ", identity not checked"}</option>`).join("")}</select>
      <div class="btn btn-sm" id="prospectInvite">Invite</div><span id="prospectNote" style="font-size:12px;color:var(--warning-text);"></span></div>`;
  },

  draftsPanel(r) {
    const open = (r.drafts || []).filter((d) => d.status === "pending" || d.status === "needs_person");
    if (!open.length) return "";
    const can = r.comparison && r.comparison.can_quote;
    const input = "background:var(--surface-sunken);border:1px solid var(--border);color:var(--text-primary);border-radius:6px;padding:5px 7px;font-size:12px;width:80px;";
    const cards = open.map((d) => {
      const who = `${this.esc(d.supplier_name || d.from || "Unknown sender")}`;
      const problems = (d.problems || []).map((x) => `<li>${this.esc(x)}</li>`).join("");
      const notes = (d.assumptions || []).map((x) => `<li>${this.esc(x)}</li>`).join("");
      let body = "";
      if (d.status === "needs_person") {
        body = `<div style="font-size:12px;color:var(--warning-text);">This reply could not be read reliably. Enter the quote by hand if it is one.</div>`;
      } else if (d.intent === "quote") {
        body = `
          <table class="data-table" style="margin:6px 0;">
            <thead><tr><th>ITEM</th><th>PRICE</th><th>DISCOUNT %</th><th>GST %</th><th>DAYS</th></tr></thead>
            <tbody>
              ${d.lines.map((l) => `
                <tr data-draft="${d.id}" data-line="${l.rfq_line_no}">
                  <td>${this.esc(l.name)}</td>
                  <td><input class="dPrice" value="${this.esc(l.unit_price)}" style="${input}"></td>
                  <td><input class="dDisc" value="${this.esc(l.discount_pct)}" style="${input}width:60px;"></td>
                  <td><input class="dGst" value="${this.esc(l.gst_rate)}" style="${input}width:60px;"></td>
                  <td><input class="dDays" value="${this.esc(l.delivery_days ?? "")}" style="${input}width:55px;"></td>
                </tr>`).join("")}
            </tbody>
          </table>
          <div class="cell-muted" style="font-size:11.5px;">${d.valid_until ? `valid until ${formatDate(d.valid_until)}` : "no validity date stated"}${Number(d.freight) ? ` \u00b7 freight ${formatCurrency(d.freight)}` : ""}${Number(d.packing_forwarding) ? ` \u00b7 packing ${formatCurrency(d.packing_forwarding)}` : ""}</div>`;
      } else if (d.intent === "decline") {
        body = `<div style="font-size:12.5px;">${who} says it will not quote${d.decline_reason ? `: \u201c${this.esc(d.decline_reason)}\u201d` : "."}</div>`;
      } else if (d.intent === "question") {
        body = `<div style="font-size:12.5px;">${who} asks: \u201c${this.esc(d.question || "")}\u201d <span class="cell-muted">(a task has been made for the buyer)</span></div>`;
      } else {
        body = `<div class="cell-muted" style="font-size:12px;">Nothing to enter from this reply.</div>`;
      }
      const confirmable = d.status === "pending" && (d.intent === "quote" || d.intent === "decline");
      return `
        <div style="padding:10px 0;border-bottom:1px solid var(--border);">
          <div style="display:flex;justify-content:space-between;gap:10px;font-size:12.5px;margin-bottom:4px;">
            <div><b>${who}</b> <span class="cell-muted">\u00b7 ${this.esc(d.subject || "")} \u00b7 read as ${this.esc(d.intent || "unclear")}${d.confidence ? ` (${Math.round(Number(d.confidence) * 100)}% sure)` : ""}</span></div>
          </div>
          ${body}
          ${problems ? `<ul style="margin:6px 0;padding-left:18px;font-size:11.5px;color:var(--danger-text);">${problems}</ul>` : ""}
          ${notes ? `<ul style="margin:6px 0;padding-left:18px;font-size:11.5px;color:var(--warning-text);">${notes}</ul>` : ""}
          ${can ? `<div style="display:flex;gap:8px;margin-top:8px;">
            ${confirmable ? `<div class="btn btn-primary btn-sm draftConfirm" data-id="${d.id}">${d.intent === "decline" ? "Confirm the refusal" : "Confirm this quote"}</div>` : ""}
            <div class="btn btn-sm draftDismiss" data-id="${d.id}">Dismiss</div>
          </div>` : ""}
          <div class="draftNote" data-id="${d.id}" style="font-size:12px;color:var(--warning-text);margin-top:6px;"></div>
        </div>`;
    }).join("");
    return `<div class="panel" style="margin-bottom:12px;"><div class="panel-title">Replies read from email \u00b7 ${open.length} to look at</div>${cards}</div>`;
  },

  quoteForm(r) {
    const c = r.comparison;
    if (!c || !c.can_quote) return "";
    const input = "background:var(--surface-sunken);border:1px solid var(--border);color:var(--text-primary);border-radius:6px;padding:6px 8px;font-size:12.5px;";
    return `
      <div class="panel" id="quoteForm" style="display:none;margin-bottom:12px;">
        <div class="panel-title">Enter a quote</div>
        <div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:10px;">
          <select id="qSupplier" style="${input}">${r.suppliers.map((s) => `<option value="${this.esc(s.code)}">${this.esc(s.name)}</option>`).join("")}</select>
          <label style="font-size:12px;color:var(--text-muted);">Valid until <input type="date" id="qValid" style="${input}"></label>
          <label style="font-size:12px;color:var(--text-muted);">Freight <input type="number" min="0" step="0.01" id="qFreight" style="${input}width:90px;"></label>
          <label style="font-size:12px;color:var(--text-muted);">Packing <input type="number" min="0" step="0.01" id="qPacking" style="${input}width:90px;"></label>
        </div>
        <table class="data-table">
          <thead><tr><th>ITEM</th><th>PRICE</th><th>DISCOUNT %</th><th>GST %</th><th>DAYS</th></tr></thead>
          <tbody>
            ${c.lines.map((l) => `
              <tr data-line="${l.rfq_line_no}">
                <td>${this.esc(l.name)} <span class="cell-muted">${this.esc(l.qty)} ${this.esc(l.uom)}</span></td>
                <td><input type="number" min="0" step="0.0001" class="qPrice" style="${input}width:100px;"></td>
                <td><input type="number" min="0" max="100" step="0.01" class="qDisc" style="${input}width:70px;"></td>
                <td><input type="number" min="0" max="100" step="0.01" class="qGst" style="${input}width:70px;"></td>
                <td><input type="number" min="0" step="1" class="qDays" style="${input}width:60px;"></td>
              </tr>`).join("")}
          </tbody>
        </table>
        <div style="display:flex;gap:8px;margin-top:10px;"><div class="btn btn-primary btn-sm" id="saveQuote">Save the quote</div><div class="btn btn-sm" id="cancelQuote">Cancel</div></div>
        <div id="quoteNote" style="font-size:12px;color:var(--warning-text);margin-top:8px;"></div>
      </div>`;
  },

  wire(r) {
    const on = (id, fn) => { const el = document.getElementById(id); if (el) el.addEventListener("click", fn); };
    on("addQuoteBtn", () => { const f = document.getElementById("quoteForm"); if (f) { f.style.display = "block"; f.scrollIntoView({ behavior: "smooth", block: "center" }); } });
    on("cancelQuote", () => { document.getElementById("quoteForm").style.display = "none"; });
    on("saveQuote", async () => {
      const rows = [...document.querySelectorAll("#quoteForm tbody tr")]
        .map((tr) => ({
          rfq_line_no: Number(tr.dataset.line),
          unit_price: tr.querySelector(".qPrice").value,
          discount_pct: tr.querySelector(".qDisc").value,
          gst_rate: tr.querySelector(".qGst").value,
          delivery_days: tr.querySelector(".qDays").value,
        }))
        .filter((l) => l.unit_price !== "");
      const body = {
        supplier_code: document.getElementById("qSupplier").value,
        valid_until: document.getElementById("qValid").value,
        freight: document.getElementById("qFreight").value,
        packing_forwarding: document.getElementById("qPacking").value,
        lines: rows,
      };
      const res = await API.postJSON(`/api/rfqs/${encodeURIComponent(r.id)}/quotes`, body);
      if (res.ok) return this.renderDetail(document.getElementById("appContent"), r.id);
      const note = document.getElementById("quoteNote");
      if (note) note.textContent = res.data.detail || "That did not work.";
    });
    document.querySelectorAll(".draftConfirm").forEach((btn) => btn.addEventListener("click", async () => {
      const id = btn.dataset.id;
      const edits = {};
      document.querySelectorAll(`tr[data-draft="${id}"]`).forEach((tr) => {
        edits[tr.dataset.line] = {
          unit_price: tr.querySelector(".dPrice").value,
          discount_pct: tr.querySelector(".dDisc").value,
          gst_rate: tr.querySelector(".dGst").value,
          delivery_days: tr.querySelector(".dDays").value,
        };
      });
      const res = await API.postJSON(`/api/quote-drafts/${id}/confirm`, { edits });
      if (res.ok) return this.renderDetail(document.getElementById("appContent"), r.id);
      const note = document.querySelector(`.draftNote[data-id="${id}"]`);
      if (note) note.textContent = res.data.detail || "That did not work.";
    }));
    document.querySelectorAll(".draftDismiss").forEach((btn) => btn.addEventListener("click", async () => {
      const res = await API.postJSON(`/api/quote-drafts/${btn.dataset.id}/dismiss`, {});
      if (res.ok) return this.renderDetail(document.getElementById("appContent"), r.id);
      const note = document.querySelector(`.draftNote[data-id="${btn.dataset.id}"]`);
      if (note) note.textContent = res.data.detail || "That did not work.";
    }));
    document.querySelectorAll(".rfqPdf").forEach((a) => a.addEventListener("click", async (e) => {
      e.preventDefault();
      const res = await Auth.fetch(`${API.base}/api/rfqs/${encodeURIComponent(r.id)}/suppliers/${encodeURIComponent(a.dataset.supplier)}/pdf`);
      if (res.ok) window.open(URL.createObjectURL(await res.blob()), "_blank");
    }));
    on("awardBtn", async () => {
      const choices = {};
      const splits = {};
      document.querySelectorAll('input[type="radio"][name^="pick-"]:checked').forEach((el) => {
        const line = el.name.replace("pick-", "");
        if (el.value === "__split__") splits[line] = JSON.parse(el.dataset.parts);
        else choices[line] = el.value;
      });
      const regret = document.getElementById("regretNotes");
      const res = await API.postJSON(`/api/rfqs/${encodeURIComponent(r.id)}/award`, { choices, splits, reason: document.getElementById("awardReason").value, ...(regret ? { regret_notes: regret.checked } : {}) });
      const note = document.getElementById("awardNote");
      if (res.ok && res.data.status === "waiting") { if (note) note.textContent = `${res.data.message} (proposal ${res.data.proposal_id}).`; return; }
      if (res.ok && (res.data.conditional || []).length) {
        await this.renderDetail(document.getElementById("appContent"), r.id);
        const after = document.getElementById("awardNote");
        if (after) after.textContent = `Conditional award (${res.data.conditional.join(", ")}): no order until the supplier is activated.`;
        return;
      }
      if (res.ok) return this.renderDetail(document.getElementById("appContent"), r.id);
      if (note) note.textContent = res.data.detail || "That did not work.";
    });
    const decide = async (path, body) => {
      const res = await API.postJSON(`/api/rfqs/${encodeURIComponent(r.id)}/award/${path}`, body);
      if (res.ok) return this.renderDetail(document.getElementById("appContent"), r.id);
      const msg = document.getElementById("awardDecisionMsg");
      if (msg) msg.textContent = res.data.detail || "That did not work.";
    };
    on("awardApprove", () => decide("approve", { note: document.getElementById("awardDecisionNote").value }));
    on("awardReject", () => decide("reject", { reason: document.getElementById("awardDecisionNote").value }));
    on("awardWithdraw", () => decide("withdraw", { reason: document.getElementById("awardWithdrawReason").value }));
    on("confirmOne", async () => {
      const res = await API.postJSON("/api/rfqs/confirm-send", { rfqs: [r.id] });
      const one = res.ok ? res.data.results[0] : null;
      if (one && one.status === "sent") return this.renderDetail(document.getElementById("appContent"), r.id);
      const msg = document.getElementById("confirmOneNote");
      if (msg) msg.textContent = (one && one.reason) || res.data.detail || "Not sent.";
    });
    on("prospectInvite", async () => {
      const res = await API.postJSON(`/api/rfqs/${encodeURIComponent(r.id)}/prospects`, { supplier_code: document.getElementById("prospectPick").value });
      if (res.ok) return this.renderDetail(document.getElementById("appContent"), r.id);
      const note = document.getElementById("prospectNote");
      if (note) note.textContent = res.data.detail || "Not invited.";
    });
  },

  async renderDetail(container, id) {
    container.innerHTML = `<div class="page-header"><h1>${this.esc(id)}</h1></div><div class="panel">Loading…</div>`;
    try {
      const r = await API.getRfq(id);
      if (r.error) { container.innerHTML = `<div class="empty-state">${this.esc(r.error)}</div>`; return; }
      container.innerHTML = `
        <div class="page-header">
          <a href="#/rfqs" style="color:var(--text-muted);font-size:12px;text-decoration:none;">← Quotations</a>
          <h1 style="margin-top:6px;">${this.esc(r.title)}</h1>
          <p>${this.esc(r.id)} · ${statusBadge(r.status)} · ${r.kind === "period"
            ? `prices for ${formatDate(r.period_from)} to ${formatDate(r.period_to)} (an estimate, not a commitment)${r.review_task_id ? ` · from <a href="#/contracts" style="color:var(--accent-tint)">strategic review ${this.esc(r.review_task_id)}</a>` : ""}`
            : `for <a href="#/requisitions/${this.esc(r.request_no)}" style="color:var(--accent-tint)">${this.esc(r.request_no)}</a>`}
             · reply by ${formatDate(r.due_date)}${r.sent_at ? ` · sent ${formatDate(r.sent_at)}` : ""}
             ${r.delivery_location_name ? ` · deliver to ${this.esc(r.delivery_location_name)}` : ""}${r.min_quotes ? ` · minimum quotes ${this.esc(r.min_quotes)}${r.min_quotes_rule ? ` (rule: ${this.esc(r.min_quotes_rule)})` : ""}` : ""}</p>
        </div>
        ${FlowStrip.forRfq(r)}
        ${r.status === "draft" && r.proposal_id ? `<div class="panel" style="margin-bottom:12px;font-size:12.5px;color:var(--text-secondary);">Not sent yet: a buyer confirms every new RFQ before it goes out. <span class="btn btn-primary btn-sm" id="confirmOne" style="margin-left:8px;"><i class="ti ti-send"></i>Confirm and send</span><span id="confirmOneNote" style="margin-left:8px;color:var(--warning-text);"></span></div>` : ""}
        ${r.confirmed_at ? `<div class="cell-muted" style="font-size:12px;margin-bottom:8px;">Sending confirmed ${formatDate(r.confirmed_at)}</div>` : ""}
        ${r.note ? `<div class="panel" style="margin-bottom:12px;font-size:12.5px;color:var(--warning-text);">${this.esc(r.note)}</div>` : ""}
        <div class="table-wrap" style="margin-bottom:12px;">
          <table class="data-table">
            <thead><tr><th>SUPPLIER</th><th>EMAIL</th><th>STATE</th><th>PRINT</th></tr></thead>
            <tbody>
              ${r.suppliers.map((s) => `
                <tr>
                  <td>${this.esc(s.name)}<div class="cell-muted" style="font-size:11px;">${this.esc(s.code)}</div></td>
                  <td class="cell-muted">${this.esc(s.email)}</td>
                  <td><span class="badge ${this.STATE_BADGE[s.state] || ""}">${this.esc(s.state)}</span>${s.error ? `<div class="cell-muted" style="font-size:11px;">${this.esc(s.error)}</div>` : ""}${s.reminders ? `<div class="cell-muted" style="font-size:11px;">reminded ${this.esc(plural(s.reminders, "time"))}, last ${formatDate(s.last_reminded_at)}</div>` : ""}</td>
                  <td><a href="#" class="rfqPdf" data-supplier="${this.esc(s.code)}" style="color:var(--accent-tint);font-size:12px;">RFQ (PDF)</a></td>
                </tr>`).join("")}
            </tbody>
          </table>
        </div>
        <div class="table-wrap" style="margin-bottom:12px;">
          <table class="data-table">
            <thead><tr><th>ITEM</th><th>QTY</th><th>NEEDED BY</th></tr></thead>
            <tbody>
              ${r.lines.map((l) => `
                <tr>
                  <td>${this.esc(l.name)}<div class="cell-muted" style="font-size:11px;">${this.esc(l.item_code || "free text")}</div></td>
                  <td>${this.esc(l.qty)} <span class="cell-muted">${this.esc(l.uom)}</span>${l.suggestion ? `<div class="cell-muted" style="font-size:11px;">suggested ${this.esc(l.suggested_qty)}: ${this.esc(l.suggestion)}</div>` : ""}</td>
                  <td class="cell-muted">${formatDate(l.need_by)}</td>
                </tr>`).join("")}
            </tbody>
          </table>
        </div>
        <div id="supplierMessagesBox"></div>
        ${this.draftsPanel(r)}
        ${this.awardPanel(r)}
        ${this.comparisonPanel(r)}
        ${this.quoteForm(r)}
      `;
      this.wire(r);
      SupplierMessages.mount(document.getElementById("supplierMessagesBox"), "rfqs", r.id);
    } catch {
      container.innerHTML = `<div class="empty-state">Couldn't load this RFQ.</div>`;
    }
  },
};
