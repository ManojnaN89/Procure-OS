// The price types of an item at a location, each in its own section and never merged (D41). Evidence only for benchmarks.
const PriceView = {
  async open(box, item, location) {
    if (!box) return;
    box.innerHTML = '<div class="panel" style="margin:10px 0;">Loading\u2026</div>';
    let p;
    try { p = await API.getJSON(`/api/items/${encodeURIComponent(item)}/prices${location ? `?location=${encodeURIComponent(location)}` : ""}`); } catch {
      box.innerHTML = '<div class="panel" style="margin:10px 0;">The prices of this item could not be loaded.</div>';
      return;
    }
    const e = escapeHtml;
    const none = (t) => `<p class="cell-muted" style="font-size:12px;margin:0;">${t}</p>`;
    const section = (title, body) => `<div style="margin-top:10px;"><div style="font-size:12px;font-weight:600;margin-bottom:4px;">${title}</div>${body}</div>`;
    const table = (head, rows) => `<table class="data-table"><thead><tr>${head.map((h) => `<th>${h}</th>`).join("")}</tr></thead><tbody>${rows.join("")}</tbody></table>`;
    box.innerHTML = `<div class="panel" style="margin:10px 0;">
      <div class="panel-title">Prices of ${e(p.item.name)} (${e(p.item.code)})${p.location ? ` at ${e(p.location)}` : ""} <span class="btn btn-sm" id="priceViewClose" style="float:right;">Close</span></div>
      ${section("Contract prices in force here", p.contract_prices.length ? table(["SUPPLIER", "CONTRACT", "VERSION", "WHERE", "PRICE", "MARKET GAP, EVIDENCE ONLY"], p.contract_prices.map((c) => `<tr><td>${e(c.supplier)}</td><td>${e(c.contract)}</td><td>${e(c.version)}</td><td>${e(c.where)}</td><td>${formatCurrency(c.price)}</td><td class="cell-muted" style="font-size:11.5px;">${c.market_gap.gap_inr !== null ? `${formatCurrency(c.market_gap.gap_inr)} per unit (${e(c.market_gap.gap_pct)}%), ${c.market_gap.above_thresholds ? "above" : "below"} the thresholds` : e(c.market_gap.why)} \u00b7 ${e(c.market_gap.rule)}</td></tr>`)) : none("No contract price applies here."))}
      ${section("Confirmed quotes still valid", p.quotes.length ? table(["RFQ", "SUPPLIER", "PRICE", "VALID UNTIL"], p.quotes.map((q) => `<tr><td>${e(q.rfq)}</td><td>${e(q.supplier)}</td><td>${formatCurrency(q.price)}</td><td>${q.valid_until ? formatDate(q.valid_until) : "\u2014"}</td></tr>`)) : none("No valid quote."))}
      ${section("Last price paid", p.last_paid ? `<p style="font-size:12.5px;margin:0;">${formatCurrency(p.last_paid.price)} on ${e(p.last_paid.order)}${p.last_paid.date ? `, ${formatDate(p.last_paid.date)}` : ""}</p>` : none("Never ordered."))}
      ${section("Prices paid, last twelve months", p.price_history.length ? table(["MONTH", "ORDERS", "QTY", "AVERAGE", "LOW", "HIGH"], p.price_history.map((h) => `<tr><td>${e(h.month)}</td><td>${e(h.orders)}</td><td>${e(h.qty)}</td><td>${formatCurrency(h.avg_price)}</td><td>${formatCurrency(h.low)}</td><td>${formatCurrency(h.high)}</td></tr>`)) : none("No orders in the last twelve months."))}
      ${section("Standard price", `<p style="font-size:12.5px;margin:0;">${p.standard_price ? formatCurrency(p.standard_price) : "not set"}</p>`)}
      ${section("Budget or target price", `<p class="cell-muted" style="font-size:12px;margin:0;">${e(p.budget_price)}</p>`)}
      ${section("Market benchmarks (evidence only)", p.benchmarks.length ? table(["DATE", "PRICE", "UNIT", "REGION", "SOURCE", "STATUS", "COUNTS"], p.benchmarks.map((b) => `<tr><td>${formatDate(b.effective_date)}</td><td>${formatCurrency(b.price)}</td><td>${e(b.uom)}</td><td>${e(b.region_name)}</td><td>${e(b.source_name)}</td><td>${e(b.status)}</td><td>${b.counts ? "yes" : `no: ${e(b.why)}`}</td></tr>`)) : none("No benchmark on file."))}
    </div>`;
    box.querySelector("#priceViewClose").addEventListener("click", () => { box.innerHTML = ""; });
  },
};

const ContractsView = {
  async render(container, params) {
    if (params && params.id) return this.renderDetail(container, params.id);
    return this.renderList(container);
  },

  async renderList(container) {
    container.innerHTML = `
      <div class="page-header page-header-row">
        <div>
          <h1>Contracts</h1>
          <p>Stored terms, value, and date range per vendor</p>
        </div>
        <div class="btn btn-primary" id="newContractBtn"><i class="ti ti-plus"></i>Store contract</div>
      </div>
      <div class="tabs" id="contractTabs">
        <button class="tab${this.tab === "market" ? "" : " active"}" data-t="">Contracts</button>
        <button class="tab${this.tab === "market" ? " active" : ""}" data-t="market">Market prices</button>
      </div>
      <div id="contractForm"></div>
      <div class="table-wrap" id="tableWrap"><div class="empty-state">Loading\u2026</div></div>
    `;
    document.querySelectorAll("#contractTabs .tab").forEach((b) => b.addEventListener("click", () => { this.tab = b.dataset.t; this.renderList(container); }));
    if (this.tab === "market") {
      document.getElementById("newContractBtn").style.display = "none";
      return this.renderMarket(document.getElementById("tableWrap"));
    }
    document.getElementById("newContractBtn").addEventListener("click", async () => {
      const box = document.getElementById("contractForm");
      if (box.innerHTML) { box.innerHTML = ""; return; }
      let vendors = [];
      try { vendors = await API.listVendors(); } catch { box.textContent = "Couldn't load vendors."; return; }
      box.innerHTML = `<div class="panel" style="margin-bottom:14px;"><div class="panel-title">Store a contract (saved as a draft; an approver activates it)</div>
        <div style="display:flex;gap:8px;flex-wrap:wrap;">
          <select id="cSupplier" style="padding:6px;">${vendors.filter((v) => v.active).map((v) => `<option value="${escapeHtml(v.id)}">${escapeHtml(v.name)}</option>`).join("")}</select>
          <select id="cType" style="padding:6px;"><option value="rate_contract">Rate contract</option><option value="framework">Framework agreement</option><option value="service_contract">Service contract</option></select>
          <input id="cFrom" type="date" title="Valid from" style="padding:6px;"><input id="cTo" type="date" title="Valid to" style="padding:6px;">
          <input id="cMax" type="number" min="0" step="any" placeholder="Ceiling before tax (optional)" style="padding:6px;width:220px;">
          <input id="cTerms" placeholder="Payment terms code, e.g. NET30" style="padding:6px;"><input id="cDelivery" placeholder="Delivery terms" style="padding:6px;">
          <div class="btn btn-primary btn-sm" id="cSave">Store the contract</div></div>
        <div id="cNote" style="font-size:12.5px;color:var(--warning-text);margin-top:6px;"></div></div>`;
      document.getElementById("cSave").addEventListener("click", async () => {
        const res = await API.postJSON("/api/contracts", {
          supplier_code: document.getElementById("cSupplier").value, type: document.getElementById("cType").value,
          valid_from: document.getElementById("cFrom").value, valid_to: document.getElementById("cTo").value,
          max_value: document.getElementById("cMax").value, payment_terms_code: document.getElementById("cTerms").value,
          delivery_terms: document.getElementById("cDelivery").value });
        if (res.ok) { window.location.hash = `#/contracts/${res.data.id}`; return; }
        document.getElementById("cNote").textContent = res.data.detail || "The contract was not stored.";
      });
    });

    try {
      const rows = await API.listContracts();
      const wrap = document.getElementById("tableWrap");
      if (!wrap) return; // the person has already gone to another page
      if (!rows.length) {
        wrap.innerHTML = `<div class="empty-state">No contracts on file yet.</div>`;
        return;
      }
      const now = new Date();
      wrap.innerHTML = `
        <table class="data-table">
          <thead><tr><th>CONTRACT</th><th>VENDOR</th><th>CEILING</th><th>USED</th><th>ENDS</th></tr></thead>
          <tbody>
            ${rows.map((c) => {
              const endsSoon = c.status === "active" && c.end_date && (new Date(c.end_date) - now) / 86400000 <= 30;
              const high = c.pct_used !== null && Number(c.pct_used) >= 80;
              return `
                <tr data-id="${escapeHtml(c.id)}">
                  <td>${escapeHtml(c.title)}${c.status !== "active" ? ` <span class="badge badge-pending">${escapeHtml(c.status)}</span>` : ""}</td>
                  <td>${c.vendor ? escapeHtml(c.vendor.name) : "—"}</td>
                  <td>${c.value ? formatCurrency(c.value) : "—"}</td>
                  <td>${formatCurrency(c.used)}${c.pct_used !== null ? ` <span class="badge ${high ? "badge-rejected" : "badge-approved"}">${escapeHtml(c.pct_used)}%</span>` : ""}</td>
                  <td>${c.end_date ? formatDate(c.end_date) : "—"} ${endsSoon ? '<span class="badge badge-pending">expiring soon</span>' : ""}</td>
                </tr>
              `;
            }).join("")}
          </tbody>
        </table>
      `;
      wrap.querySelectorAll("tr[data-id]").forEach((tr) => {
        tr.addEventListener("click", () => { window.location.hash = `#/contracts/${tr.dataset.id}`; });
      });
    } catch {
      const gone = document.getElementById("tableWrap");
      if (gone) gone.innerHTML = `<div class="empty-state">Couldn't load contracts.</div>`;
    }
  },

  // D52: market benchmarks, entered or uploaded by one person and verified by another; evidence only
  async renderMarket(wrap) {
    let d;
    try { d = await API.getJSON("/api/benchmarks"); } catch { wrap.innerHTML = '<div class="empty-state">Couldn\'t load the market prices.</div>'; return; }
    const e = escapeHtml;
    const badge = { verified: "badge-approved", unverified: "badge-pending", rejected: "badge-rejected" };
    const kinds = [["index", "Index"], ["price_list", "Price list"], ["market_survey", "Market survey"], ["other", "Other"]];
    wrap.innerHTML = `
      ${d.can_enter ? `<div class="panel" style="margin-bottom:12px;"><div class="panel-title">Add a benchmark</div>
        <div style="display:flex;gap:6px;flex-wrap:wrap;">
          <input id="bItem" placeholder="Item code" style="padding:5px;width:100px;"><input id="bPrice" type="number" min="0" step="any" placeholder="Price before GST" style="padding:5px;width:130px;">
          <input id="bUom" placeholder="Unit" style="padding:5px;width:70px;"><select id="bBasis" style="padding:5px;"><option value="delivered">Delivered</option><option value="ex_works">Ex works</option></select>
          <input id="bDate" type="date" title="Effective date" style="padding:5px;"><input id="bRegion" placeholder="Region (empty: national)" style="padding:5px;width:170px;">
          <select id="bKind" style="padding:5px;">${kinds.map(([v, l]) => `<option value="${v}">${l}</option>`).join("")}</select>
          <input id="bSource" placeholder="Source name" style="padding:5px;min-width:200px;"><input id="bRef" placeholder="Source reference (URL or document)" style="padding:5px;min-width:200px;">
          <input id="bNote" placeholder="Note" style="padding:5px;"><div class="btn btn-primary btn-sm" id="bAdd">Add</div></div>
        <div class="cell-muted" style="font-size:11px;margin-top:4px;">A benchmark is evidence only: it never sets an estimate, a contract price or an order price. Someone other than you verifies it.</div></div>
      <div class="panel" style="margin-bottom:12px;"><div class="panel-title">Upload a sheet</div>
        <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;font-size:12.5px;"><a href="#" id="bTemplate" style="color:var(--accent-tint);">Download the template</a>
          <input type="file" id="bFile" accept=".csv,.xlsx"><div class="btn btn-secondary btn-sm" id="bUpload">Upload</div></div>
        <div id="bUploadResult" style="font-size:12px;margin-top:6px;"></div></div>` : ""}
      <div id="bNote2" style="font-size:12.5px;color:var(--warning-text);margin-bottom:6px;"></div>
      ${d.benchmarks.length ? `<table class="data-table"><thead><tr><th>ITEM</th><th>PRICE</th><th>UNIT</th><th>EFFECTIVE</th><th>REGION</th><th>SOURCE</th><th>STATUS</th><th>AGE</th><th>COUNTS</th></tr></thead><tbody>
        ${d.benchmarks.map((b) => `<tr><td>${e(b.item_name || b.item_code)}<div class="cell-muted" style="font-size:11px;">${e(b.item_code)}</div></td><td>${formatCurrency(b.price)}</td><td>${e(b.uom)}</td><td>${formatDate(b.effective_date)}</td><td>${e(b.region_name)}</td>
          <td>${e(b.source_name)}<div class="cell-muted" style="font-size:11px;">${e(b.source_kind.replace(/_/g, " "))}${b.source_ref ? ` \u00b7 ${e(b.source_ref)}` : ""}</div></td>
          <td><span class="badge ${badge[b.status] || ""}">${e(b.status)}</span>${b.rejected_reason ? `<div class="cell-muted" style="font-size:11px;">${e(b.rejected_reason)}</div>` : ""}${b.verified_by && b.status === "verified" ? `<div class="cell-muted" style="font-size:11px;">by ${e(b.verified_by)}</div>` : ""}
            ${d.can_verify && b.status === "unverified" ? `<div style="margin-top:4px;"><span class="btn btn-secondary btn-sm b-verify" data-id="${b.id}" style="padding:2px 8px;font-size:11px;">Verify</span> <span class="btn btn-secondary btn-sm b-reject" data-id="${b.id}" style="padding:2px 8px;font-size:11px;">Reject</span></div>` : ""}</td>
          <td>${e(plural(b.age_days, "day"))}</td><td>${b.counts ? "yes" : `no: ${e(b.why)}`}</td></tr>`).join("")}
      </tbody></table>` : '<div class="empty-state">No market prices on file.</div>'}`;
    const note = (t) => { wrap.querySelector("#bNote2").textContent = t; };
    const val = (id) => { const el = wrap.querySelector(`#${id}`); return el ? el.value.trim() : ""; };
    const add = wrap.querySelector("#bAdd");
    if (add) add.addEventListener("click", async () => {
      const res = await API.postJSON("/api/benchmarks", { item_code: val("bItem"), price: val("bPrice"), uom: val("bUom"), basis: val("bBasis"), effective_date: val("bDate"), region_code: val("bRegion"), source_kind: val("bKind"), source_name: val("bSource"), source_ref: val("bRef"), note: val("bNote") });
      if (res.ok) return this.renderMarket(wrap);
      note(res.data.detail || "The benchmark was not added.");
    });
    const tpl = wrap.querySelector("#bTemplate");
    if (tpl) tpl.addEventListener("click", async (ev) => {
      ev.preventDefault();
      const res = await Auth.fetch(`${API.base}/api/benchmarks/template`);
      if (!res.ok) return;
      const a = document.createElement("a");
      a.href = URL.createObjectURL(await res.blob()); a.download = "market_benchmark.csv"; a.click();
    });
    const up = wrap.querySelector("#bUpload");
    if (up) up.addEventListener("click", async () => {
      const file = wrap.querySelector("#bFile");
      const out = wrap.querySelector("#bUploadResult");
      if (!file.files.length) { out.textContent = "Choose a sheet first."; return; }
      const form = new FormData();
      form.append("file", file.files[0]);
      const res = await Auth.fetch(`${API.base}/api/benchmarks/upload`, { method: "POST", body: form });
      let data = {};
      try { data = await res.json(); } catch {}
      if (!res.ok) { out.textContent = data.detail || "The sheet was not loaded."; return; }
      await this.renderMarket(wrap);
      const again = wrap.querySelector("#bUploadResult");
      if (again) again.innerHTML = `Loaded ${e(data.loaded)} unverified, ${e(data.skipped)} loaded before${data.changed.length ? `, ${e(plural(data.changed.length, "row"))} already loaded with another price (not applied: lines ${data.changed.map((c) => e(c.line)).join(", ")})` : ""}.${data.rejected.length ? `<ul style="margin:4px 0;padding-left:18px;color:var(--warning-text);">${data.rejected.map((r) => `<li>line ${e(r.line)}: ${e(r.reason)}</li>`).join("")}</ul>` : ""}`;
    });
    wrap.querySelectorAll(".b-verify").forEach((b) => b.addEventListener("click", async () => {
      const res = await API.postJSON(`/api/benchmarks/${b.dataset.id}/verify`, { note: "" });
      if (res.ok) return this.renderMarket(wrap);
      note(res.data.detail || "Not verified.");
    }));
    wrap.querySelectorAll(".b-reject").forEach((b) => b.addEventListener("click", async () => {
      const reason = window.prompt("Why is this benchmark rejected?");
      if (!reason) return;
      const res = await API.postJSON(`/api/benchmarks/${b.dataset.id}/reject`, { reason });
      if (res.ok) return this.renderMarket(wrap);
      note(res.data.detail || "Not rejected.");
    }));
  },

  async marketPanel(box, id) {
    if (!box) return;
    let m;
    try { m = await API.getJSON(`/api/contracts/${encodeURIComponent(id)}/market`); } catch { box.remove(); return; }
    const e = escapeHtml;
    box.innerHTML = `<div class="panel-title">Market (${e(m.label)})</div>
      ${m.prices.length ? `<table class="data-table"><thead><tr><th>ITEM</th><th>WHERE</th><th>CONTRACT</th><th>LATEST COUNTING BENCHMARK</th><th>GAP</th><th>THRESHOLDS</th><th>REVIEW</th></tr></thead><tbody>
        ${m.prices.map((p) => `<tr><td>${e(p.item_code)}</td><td>${e(p.where)}</td><td>${formatCurrency(p.contract_price)} / ${e(p.uom)}</td>
          <td>${p.latest_benchmark ? `${formatCurrency(p.latest_benchmark.price)} / ${e(p.latest_benchmark.uom)}, ${formatDate(p.latest_benchmark.effective_date)}<div class="cell-muted" style="font-size:11px;">${e(p.latest_benchmark.source_name)}</div>` : '<span class="cell-muted">none counts</span>'}</td>
          <td>${p.gap_inr !== null ? `${formatCurrency(p.gap_inr)} (${e(p.gap_pct)}%)${p.saving !== null ? `<div class="cell-muted" style="font-size:11px;">saving ${formatCurrency(p.saving)} on ${e(p.relevant_qty)}</div>` : ""}` : "\u2014"}</td>
          <td>${p.above_thresholds ? '<span class="badge badge-pending">above</span>' : '<span class="badge badge-approved">below</span>'}<div class="cell-muted" style="font-size:11px;">${e(p.why)} \u00b7 ${e(p.rule)}</div></td>
          <td>${p.review ? `open (${e(p.review.trigger.replace(/_/g, " "))})` : "\u2014"}</td></tr>`).join("")}
      </tbody></table>` : '<p class="cell-muted" style="font-size:12px;margin:0;">No price in force today.</p>'}
      ${(m.reviews || []).length ? `<p style="font-size:12.5px;margin:8px 0 0;">Open reviews: ${m.reviews.map((r) => e(r.title)).join("; ")}. The contract stays valid and is still used for ordering.</p>` : ""}
      <div style="margin-top:8px;display:flex;gap:6px;align-items:center;flex-wrap:wrap;"><input id="reviewReason" placeholder="Why a review? (needed)" style="padding:5px;min-width:240px;"><span class="btn btn-secondary btn-sm" id="reviewAsk">Ask for a review</span><span id="reviewNote" style="font-size:12px;color:var(--warning-text);"></span></div>`;
    box.querySelector("#reviewAsk").addEventListener("click", async () => {
      const res = await API.postJSON("/api/reviews", { trigger: "buyer", contract_code: id, reason: box.querySelector("#reviewReason").value.trim() });
      box.querySelector("#reviewNote").textContent = res.ok ? (res.data.joined ? `Added to the open review (task ${res.data.task_id}).` : `Review opened (task ${res.data.task_id}).`) : (res.data.detail || "No review opened.");
    });
  },

  async renderDetail(container, id) {
    container.innerHTML = `<div class="page-header"><h1>Contract ${escapeHtml(id)}</h1></div><div class="panel">Loading\u2026</div>`;
    try {
      const c = await API.getContract(id);
      if (c.error) { container.innerHTML = `<div class="empty-state">${escapeHtml(c.error)}</div>`; return; }
      container.innerHTML = `
        <div class="page-header">
          <a href="#/contracts" style="color:var(--text-muted);font-size:12px;text-decoration:none;">\u2190 Contracts</a>
          <h1 style="margin-top:6px;">${escapeHtml(c.title)}</h1>
          <p>${c.vendor ? c.vendor.name : "\u2014"} \u00b7 ${formatCurrency(c.value)}</p>
        </div>
        <div class="panel" style="margin-bottom:14px;">
          <div class="panel-title">Ordered against this contract</div>
          <p style="color:var(--text-secondary);font-size:12.5px;margin:0 0 10px;">
            ${formatCurrency(c.used)} before tax${c.value ? ` of ${formatCurrency(c.value)} (${escapeHtml(c.pct_used)}%), ${formatCurrency(c.remaining)} left` : ""}
          </p>
          ${(c.orders || []).length ? `<table class="data-table"><thead><tr><th>ORDER</th><th>STATUS</th><th>BEFORE TAX</th></tr></thead><tbody>${c.orders.map((o) => `<tr data-po="${escapeHtml(o.id)}"><td>${escapeHtml(o.id)}</td><td>${statusBadge(o.status)}</td><td>${formatCurrency(o.amount)}</td></tr>`).join("")}</tbody></table>` : `<p class="cell-muted" style="font-size:12px;margin:0;">No orders yet.</p>`}
        </div>
        <div class="panel" style="margin-bottom:14px;">
          <div class="panel-title">Term</div>
          <p style="color:var(--text-secondary);font-size:12.5px;margin:0 0 10px;">
            ${c.start_date ? formatDate(c.start_date) : "\u2014"} \u2192 ${c.end_date ? formatDate(c.end_date) : "\u2014"}
          </p>
          <div class="btn btn-secondary" id="renewContractBtn" style="font-size:11.5px;padding:6px 12px;display:inline-block;">Renew contract</div>
        </div>
        <div class="panel" style="margin-bottom:14px;">
          <div class="panel-title">Price list ${c.status !== "active" ? `<span class="badge badge-pending">${escapeHtml(c.status)}</span>` : ""}</div>
          ${(c.prices || []).length ? `<table class="data-table"><thead><tr><th>ITEM</th><th>WHERE</th><th>UNIT</th><th>FROM QTY</th><th>PRICE</th><th>VALID</th><th>VERSION</th></tr></thead><tbody>${c.prices.map((p) => `<tr><td>${escapeHtml(p.name || p.item_code)}<div class="cell-muted" style="font-size:11px;">${escapeHtml(p.item_code)} \u00b7 <a href="#" class="price-view" data-item="${escapeHtml(p.item_code)}" data-loc="${escapeHtml(p.location_code || "")}" style="color:var(--accent-tint);">prices</a></div></td><td>${escapeHtml(p.where || "everywhere")}</td><td>${escapeHtml(p.uom || "")}</td><td>${escapeHtml(p.min_qty)}</td><td>${formatCurrency(p.price)}</td><td class="cell-muted">${p.valid_from ? formatDate(p.valid_from) : "—"} → ${p.valid_to ? formatDate(p.valid_to) : "—"}</td><td>${escapeHtml(p.version)}${p.status === "draft" ? ' <span class="badge badge-pending">draft</span>' : ""}</td></tr>`).join("")}</tbody></table>` : `<p class="cell-muted" style="font-size:12px;margin:0;">No prices yet.</p>`}
          ${c.can_manage ? `<div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:10px;"><input id="pItem" placeholder="Item code" style="padding:5px;"><input id="pUom" placeholder="Unit" style="padding:5px;width:70px;"><input id="pPrice" type="number" min="0" step="any" placeholder="Price" style="padding:5px;width:110px;"><input id="pMin" type="number" min="0" step="any" placeholder="From qty" style="padding:5px;width:90px;"><input id="pFrom" type="date" title="Valid from (empty: the contract's start)" style="padding:5px;"><input id="pTo" type="date" title="Valid to (empty: the contract's end)" style="padding:5px;"><select id="pWhere" title="Where the price applies" style="padding:5px;"><option value="">Everywhere</option>${(c.regions || []).map((r) => `<option value="r:${escapeHtml(r.code)}">${escapeHtml(r.name)} (region)</option>${r.locations.map((l) => `<option value="l:${escapeHtml(l.code)}">${escapeHtml(l.name)}</option>`).join("")}`).join("")}</select><div class="btn btn-secondary btn-sm" id="pAdd">${c.status === "active" ? "Add a draft price" : "Add a price"}</div></div>${c.status === "active" ? `<p class="cell-muted" style="font-size:11.5px;margin:6px 0 0;">A new price is a draft of the next version. It is used only after someone else activates the version; from its first day it replaces the price in force.</p>` : ""}` : ""}
          ${c.version_waiting ? `<p style="font-size:12.5px;margin:10px 0 0;">Version ${escapeHtml(c.draft_version)} is waiting to be activated by someone other than who entered it.</p>` : ""}
          ${c.can_submit_version ? `<div class="btn btn-primary btn-sm" id="vSubmit" style="margin-top:10px;display:inline-block;">Send version ${escapeHtml(c.draft_version)} for activation</div>` : ""}
          ${c.can_activate_version ? `<div class="btn btn-primary btn-sm" id="vActivate" style="margin-top:10px;display:inline-block;">Activate version ${escapeHtml(c.draft_version)}</div> <div class="btn btn-secondary btn-sm" id="vReject" style="margin-top:10px;display:inline-block;">Reject</div>` : ""}
          ${c.can_manage && c.status === "active" && c.draft_version ? `<div class="btn btn-secondary btn-sm" id="vWithdraw" style="margin-top:10px;display:inline-block;">Withdraw the draft</div>` : ""}
          ${c.can_activate ? `<div class="btn btn-primary btn-sm" id="cActivate" style="margin-top:10px;display:inline-block;">Activate this contract</div>` : ""}
          <div id="cMsg" style="font-size:12.5px;color:var(--warning-text);margin-top:6px;"></div>
          <div id="priceViewBox"></div>
        </div>
        ${c.status === "active" ? '<div class="panel" id="marketPanel" style="margin-bottom:14px;"></div>' : ""}
        <div class="panel">
          <div class="panel-title">Terms</div>
          <p style="color:var(--text-secondary);font-size:12.5px;margin:0;white-space:pre-wrap;">${escapeHtml(c.terms || "No terms recorded.")}</p>
        </div>
      `;

      const msg = (t) => { const el = document.getElementById("cMsg"); if (el) el.textContent = t; };
      const pAdd = document.getElementById("pAdd");
      if (pAdd) pAdd.addEventListener("click", async () => {
        const where = document.getElementById("pWhere").value;
        const res = await API.postJSON(`/api/contracts/${encodeURIComponent(id)}/prices`, { item_code: document.getElementById("pItem").value, uom: document.getElementById("pUom").value, price: document.getElementById("pPrice").value, min_qty: document.getElementById("pMin").value, valid_from: document.getElementById("pFrom").value, valid_to: document.getElementById("pTo").value, location_code: where.startsWith("l:") ? where.slice(2) : null, region_code: where.startsWith("r:") ? where.slice(2) : null });
        if (res.ok) return this.renderDetail(container, id);
        msg(res.data.detail || "The price was not added.");
      });
      container.querySelectorAll(".price-view").forEach((a) => a.addEventListener("click", (ev) => { ev.preventDefault(); PriceView.open(document.getElementById("priceViewBox"), a.dataset.item, a.dataset.loc || null); }));
      this.marketPanel(document.getElementById("marketPanel"), id);
      const act = document.getElementById("cActivate");
      if (act) act.addEventListener("click", async () => {
        const res = await API.postJSON(`/api/contracts/${encodeURIComponent(id)}/activate`, {});
        if (res.ok) return this.renderDetail(container, id);
        msg(res.data.detail || "The contract was not activated.");
      });
      const versionAction = (elId, path, ask) => {
        const el = document.getElementById(elId);
        if (!el) return;
        el.addEventListener("click", async () => {
          const body = {};
          if (ask) {
            const reason = window.prompt(ask);
            if (!reason) return;
            body.reason = reason;
          }
          const res = await API.postJSON(`/api/contracts/${encodeURIComponent(id)}/versions/${path}`, body);
          if (res.ok) return this.renderDetail(container, id);
          msg(res.data.detail || "That did not work.");
        });
      };
      versionAction("vSubmit", "submit");
      versionAction("vActivate", "activate");
      versionAction("vReject", "reject", "Why is this version rejected?");
      versionAction("vWithdraw", "withdraw", "Why is the draft withdrawn?");

      // Prefill, not auto-send — renewal needs a new end date the user
      // has to actually supply, unlike a one-click action like Dismiss
      document.getElementById("renewContractBtn").addEventListener("click", () => {
        Prefill.set(`Renew contract ${id} with a new end date of [YYYY-MM-DD]`);
        window.location.hash = "#/assistant";
      });
    } catch {
      container.innerHTML = `<div class="empty-state">Couldn't load this contract.</div>`;
    }
  },
};
