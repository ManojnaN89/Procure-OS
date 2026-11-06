/**
 * Focus Mode: full-screen, assistant-only "Living Report" view. Reuses the
 * same Conversation and Trail modules as the rest of the app.
 *
 * Entry: top-bar toggle (standard mode only).
 * Exit: Escape key ONLY — no button inside Focus Mode itself, by design.
 * (The chart detail modal is a separate, nested interaction and does get
 * its own close button/backdrop-click — Escape closes the modal first if
 * one is open, and only exits Focus Mode on a second Escape.)
 */
const CHART_SLICE_COLORS = ["#9B8CFF", "#F5A623", "#2ECC71", "#4f8dfd", "#ff6b9d"];
const FocusMode = {
  active: false,
  _unsubConversation: null,
  _unsubTrail: null,
  _pulseCount: 0,
  _modalOpen: false,
  _modalChart: null,

  init() {
    this.overlay = document.getElementById("focusMode");
    this.story = document.getElementById("fmStory");
    this.rail = document.getElementById("fmPulseRail");
    this.input = document.getElementById("fmInput");
    this.sendBtn = document.getElementById("fmSendBtn");
    this.chartModal = document.getElementById("fmChartModal");
    this.chartModalTitle = document.getElementById("fmChartModalTitle");
    this.chartModalCanvas = document.getElementById("fmChartModalCanvas");

    document.getElementById("focusModeToggle").addEventListener("click", () => this.enter());
    document.getElementById("fmChartModalClose").addEventListener("click", () => this.closeChartModal());
    this.chartModal.addEventListener("click", (e) => {
      if (e.target === this.chartModal) this.closeChartModal(); // backdrop click only
    });

    this.sendBtn.addEventListener("click", () => this.submit());
    this.input.addEventListener("keydown", (e) => { if (e.key === "Enter") this.submit(); });

    // Focus Mode's composer was missing both attach and mic entirely — only
    // the floating panel and full Assistant page ever got them. Same
    // controller, same wiring pattern as those two surfaces, just styled
    // to fit Focus Mode's quiet, chrome-free look instead of a boxy button.
    this.attachment = ComposerAttachment.makeController(
      document.getElementById("fmFileInput"),
      document.getElementById("fmAttachChip"),
    );
    document.getElementById("fmAttachBtn").addEventListener("click", () => {
      document.getElementById("fmFileInput").click();
    });
    Voice.wireMicButton(document.getElementById("fmMicBtn"));

    document.addEventListener("keydown", (e) => {
      if (e.key !== "Escape") return;
      if (this._modalOpen) { this.closeChartModal(); return; }
      if (this.active) this.exit();
    });
  },

  enter({ instant = false } = {}) {
    if (this.active) return;
    this.active = true;

    document.body.classList.add("focus-mode-active");
    document.documentElement.classList.remove("fm-boot-hide"); // real classes now govern the visual state
    this.overlay.classList.add("visible");
    if (instant) {
      this.overlay.classList.add("in"); // already visually correct from the boot-hide CSS — no transition fires
    } else {
      requestAnimationFrame(() => this.overlay.classList.add("in"));
    }

    this.rail.innerHTML = "";
    this._pulseCount = 0;
    this.renderStory();
    this._unsubConversation = Conversation.subscribe(() => this.renderStory());
    this._unsubTrail = Trail.subscribe((evt) => this.addPulse(evt));
    [...Trail.recent].reverse().forEach((evt) => this.addPulse(evt, { silent: true }));
    this._unsubNotifications = Notifications.subscribe((evt) => this.addSignalPulse(evt));
    [...Notifications.recent].reverse().forEach((evt) => this.addSignalPulse(evt, { silent: true }));

    setTimeout(() => this.input.focus(), instant ? 0 : 350);
  },

  exit() {
    if (!this.active) return;
    this.active = false;

    this.overlay.classList.remove("in");
    document.body.classList.remove("focus-mode-active");
    setTimeout(() => { this.overlay.classList.remove("visible"); }, 400);

    if (this._unsubConversation) this._unsubConversation();
    if (this._unsubTrail) this._unsubTrail();
    if (this._unsubNotifications) this._unsubNotifications();
    this.closeChartModal();
  },

  submit() {
    const text = this.input.value.trim();
    if (!text && !this.attachment.hasPending()) return;
    this.input.value = "";
    Conversation.send(this.attachment.buildMessage(text));
    this.attachment.clear();
  },

  // ---------------------------------------------------------------------
  // The launch briefing. Composed from a template using real numbers (not
  // a live LLM call, deliberately — instant on every launch, keeps facts
  // deterministic, AI narration only when the person actually asks
  // something). Pushed as a REAL first message into Conversation.messages
  // — not a throwaway view — so it stays visible on scroll-back once a
  // real conversation starts, and shows up consistently across Focus
  // Mode, the floating panel, and the full Assistant page, same as any
  // other message in the shared thread.
  // ---------------------------------------------------------------------
  async loadBriefingIntoConversation() {
    if (Conversation.messages.length > 0 || this._briefingLoading) return;
    this._briefingLoading = true;

    let data;
    try {
      data = await API.getBriefing();
    } catch {
      this._briefingLoading = false;
      // don't persist a "couldn't load" message into the conversation —
      // just show the fallback directly, so a later successful launch
      // isn't stuck with a stale failure message in permanent history
      if (Conversation.messages.length === 0) {
        this.story.innerHTML = `<div class="fm-answer"><p>Ask about spend, an auction, a requisition, a vendor \u2014 anything. This view stays out of the way until you need it.</p></div>`;
      }
      return;
    }

    const hour = new Date().getHours();
    const period = hour < 12 ? "morning" : hour < 17 ? "afternoon" : "evening";
    const total = data.total_unresolved || 0;

    // Content is now just the greeting — "Needs attention" and "Spend
    // snapshot" render as their own labeled sections below (see
    // renderStory's per-message loop), not folded into prose paragraphs.
    // This follows Gemini Daily Brief's confirmed structure: labeled
    // sections, not one undifferentiated block of text.
    const content = `Good ${period}. Here's where things stand.`;

    // Only the exploratory suggestion remains bundled at the end — the
    // action suggestion moved inline, next to the specific item it's
    // about, matching how Gemini attaches next-steps per item rather than
    // bundling one generic action for the whole brief.
    const suggestions = [{ label: "Full dashboard summary", prompt: "Give me a full summary of the dashboard" }];

    // guard again — a fetch this slow could race with the user having
    // already sent their own first message while we were waiting
    if (Conversation.messages.length === 0) {
      Conversation.messages.push({
        role: "assistant", content, pending: false,
        charts: [], suggestions,
        signals: data.signals || [],
        signalTotal: total,
        signalCounts: data.signal_counts || {},
        spendSnapshot: { total_spend: data.total_spend, cost_savings_period: data.cost_savings_period },
      });
      Conversation.notify();
    }
    this._briefingLoading = false;
  },

  // ---------------------------------------------------------------------
  // The Living Report renderer
  // ---------------------------------------------------------------------
  renderStory() {
    const messages = Conversation.messages;
    this.story.innerHTML = "";

    if (messages.length === 0) {
      const intro = document.createElement("div");
      intro.className = "fm-answer";
      intro.innerHTML = `<div class="fm-thinking"><span class="dot"></span><span class="dot" style="animation-delay:.2s"></span><span class="dot" style="animation-delay:.4s"></span>&nbsp;preparing your briefing</div>`;
      this.story.appendChild(intro);
      this.loadBriefingIntoConversation(); // fetch-once; notify() re-triggers this render with the real message
      return;
    }

    for (let i = 0; i < messages.length; i++) {
      const m = messages[i];
      if (m.role !== "assistant") continue;
      const userMsg = messages[i - 1] && messages[i - 1].role === "user" ? messages[i - 1] : null;
      const isLast = i === messages.length - 1;

      const turn = document.createElement("div");
      turn.className = "fm-turn";

      if (userMsg) {
        const uline = document.createElement("div");
        uline.className = "fm-user-line";
        uline.textContent = userMsg.content;
        turn.appendChild(uline);
      }

      const answer = document.createElement("div");
      answer.className = "fm-answer";

      if (m.pending && m.content === "" && (!m.charts || m.charts.length === 0)) {
        answer.innerHTML = `<div class="fm-thinking"><span class="dot"></span><span class="dot" style="animation-delay:.2s"></span><span class="dot" style="animation-delay:.4s"></span>&nbsp;thinking</div>`;
      } else {
        this._renderAnswerContent(answer, m.content || "", m.charts || [], i);
      }
      turn.appendChild(answer);

      // extras (progress bars, expandable detail lists) — permanent part
      // of this message, same as the signal list, not gated to "last only"
      if (m.extras && m.extras.length) {
        InteractiveElements.renderExtras(answer, m.extras, "fm");
      }

      // the launch briefing (and only the briefing) carries the two
      // labeled sections — "Needs attention" (each item with its own
      // inline action, not one generic bundled suggestion) and "Spend
      // snapshot". Appended INTO the .fm-answer container (not the outer
      // .fm-turn), so their <p> tags inherit the same paragraph spacing
      // as everything else in the report — appending to .fm-turn directly
      // would have left them without that styling context. Rendered here
      // as a permanent part of this message, not gated to "last message
      // only", since it's historical content that should stay visible on
      // scroll-back.
      if (m.signalTotal !== undefined) {
        answer.appendChild(this._buildNeedsAttentionSection(m.signals || [], m.signalTotal, m.signalCounts || {}));
      }
      if (m.spendSnapshot) {
        answer.appendChild(this._buildSpendSnapshotSection(m.spendSnapshot));
      }

      if (isLast && !m.pending && m.suggestions && m.suggestions.length) {
        const sline = document.createElement("div");
        sline.className = "fm-suggestions-line";
        sline.appendChild(document.createTextNode("Next: "));
        m.suggestions.forEach((s, si) => {
          if (si > 0) {
            const sep = document.createElement("span");
            sep.className = "fm-suggestion-sep";
            sep.textContent = "\u00b7";
            sline.appendChild(sep);
          }
          const link = document.createElement("span");
          link.className = "fm-suggestion";
          link.textContent = s.label;
          link.addEventListener("click", () => Conversation.send(s.prompt));
          sline.appendChild(link);
        });
        turn.appendChild(sline);
      }

      this.story.appendChild(turn);
    }

    this.story.scrollTop = this.story.scrollHeight;
  },

  /**
   * Builds the answer's content into `container`: parses text into ordered
   * blocks (paragraphs, lists, tables), then interleaves chart sparklines
   * at roughly-even positions through those blocks, rather than dumping
   * every chart after all the text (the original, worse behavior). This
   * is a proportional-spacing heuristic, not true "this chart belongs
   * after this exact sentence" placement — the model doesn't tell us
   * that, so evenly distributing charts through the content is the
   * honest, implementable version of "woven into the report."
   */
  _renderAnswerContent(container, text, charts, msgIndex) {
    const blocks = this._parseBlocks(text);
    const total = blocks.length;

    const chartPositions = new Map(); // block index (1-based, "after this many blocks") -> [chart,...]
    charts.forEach((chart, ci) => {
      const pos = total === 0 ? 0 : Math.min(total, Math.round(((ci + 1) * total) / (charts.length + 1)));
      if (!chartPositions.has(pos)) chartPositions.set(pos, []);
      chartPositions.get(pos).push(chart);
    });

    // charts sharing the same position render as a row (side by side when
    // they fit) instead of always stacking full-width, one per position
    const appendChartsAt = (chartsHere) => {
      if (!chartsHere || !chartsHere.length) return;
      if (chartsHere.length === 1) {
        container.appendChild(this._buildChartBlock(chartsHere[0], msgIndex, 0));
        return;
      }
      const row = document.createElement("div");
      row.className = "fm-chart-row";
      chartsHere.forEach((chart, ci) => row.appendChild(this._buildChartBlock(chart, msgIndex, ci)));
      container.appendChild(row);
    };

    if (total === 0 && charts.length) {
      appendChartsAt(charts);
      InteractiveElements.wireEntityChips(container);
      return;
    }

    blocks.forEach((block, bi) => {
      const wrapper = document.createElement("div");
      wrapper.className = "fm-block";
      wrapper.innerHTML = block.html;
      container.appendChild(wrapper);
      appendChartsAt(chartPositions.get(bi + 1));
    });

    InteractiveElements.wireEntityChips(container);
  },

  /**
   * Parses markdown-ish text into ordered block descriptors: paragraphs,
   * bullet/numbered lists, and GFM-style tables (the raw `| a | b |`
   * pipe syntax the model sometimes writes, which previously rendered
   * as literal pipe characters).
   */
  _parseBlocks(text) {
    const escapeHtml = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    const highlightNumbers = (s) => s.replace(
      // needs a real digit after the symbol and "Rs" as a whole word \u2014 the old
      // [\d,]+ matched a bare comma, so "orders," / "vendors," got highlighted
      /(?:\u20b9|\bRs\.?)\s?\d[\d,]*(?:\.\d+)?(?:\s?(?:M|K|Cr|L|lakhs?|crores?|million|billion)\b)?/gi,
      (match) => `<span class="fm-number">${match}</span>`
    );
    const inlineBasic = (s) => escapeHtml(s)
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/(?<!\*)\*([^*\n]+?)\*(?!\*)/g, "<strong>$1</strong>");
    // full formatting (with number pop + clickable entity references) for
    // prose; table cells stay plain so a row doesn't get thrown out of
    // alignment by oversized numbers or chip padding
    const inlineFormat = (s) => InteractiveElements.linkifyEntities(highlightNumbers(inlineBasic(s)));

    const isTableRow = (line) => /^\s*\|.*\|\s*$/.test(line);
    const isTableSep = (line) => /^\s*\|?[\s:|-]+\|?\s*$/.test(line) && line.includes("-");
    const splitCells = (line) => line.split("|").map((c) => c.trim())
      .filter((c, i, arr) => !(i === 0 && c === "") && !(i === arr.length - 1 && c === ""));

    const lines = text.split("\n");
    const blocks = [];
    let listType = null;
    let listItems = [];
    let tableRows = null;

    const flushList = () => {
      if (listType) { blocks.push({ type: listType, html: `<${listType}>${listItems.join("")}</${listType}>` }); listType = null; listItems = []; }
    };
    const flushTable = () => {
      if (tableRows) {
        const [header, ...rows] = tableRows;
        let html = "<table><thead><tr>" + header.map((c) => `<th>${inlineBasic(c)}</th>`).join("") + "</tr></thead><tbody>";
        rows.forEach((r) => { html += "<tr>" + r.map((c) => `<td>${inlineBasic(c)}</td>`).join("") + "</tr>"; });
        html += "</tbody></table>";
        blocks.push({ type: "table", html });
        tableRows = null;
      }
    };

    for (let idx = 0; idx < lines.length; idx++) {
      const line = lines[idx];

      if (isTableRow(line)) {
        const next = lines[idx + 1];
        if (!tableRows && next !== undefined && isTableSep(next)) {
          flushList();
          tableRows = [splitCells(line)];
          idx += 1; // consume the separator row
          continue;
        } else if (tableRows) {
          tableRows.push(splitCells(line));
          continue;
        }
      } else {
        flushTable();
      }

      const bulletMatch = line.match(/^\s*[-*]\s+(.*)/);
      const numMatch = line.match(/^\s*\d+\.\s+(.*)/);

      if (bulletMatch) {
        if (listType !== "ul") { flushList(); listType = "ul"; }
        listItems.push(`<li>${inlineFormat(bulletMatch[1])}</li>`);
      } else if (numMatch) {
        if (listType !== "ol") { flushList(); listType = "ol"; }
        listItems.push(`<li>${inlineFormat(numMatch[1])}</li>`);
      } else {
        flushList();
        if (line.trim() !== "") blocks.push({ type: "p", html: `<p>${inlineFormat(line)}</p>` });
      }
    }
    flushList();
    flushTable();
    return blocks;
  },

  _signalActionLabel(type) {
    const labels = {
      exception_flagged: "Review",
      vendor_risk: "View vendor",
      stale_requisition: "Review",
      budget_overrun: "Ask about this",
      contract_expiring: "View contract",
    };
    return labels[type] || "Review";
  },

  /** "Needs attention" — one of the briefing's two labeled sections. Each
   *  item carries its own inline action rather than one generic action
   *  bundled at the very end of the message, matching Gemini Daily
   *  Brief's confirmed pattern of attaching next-steps to the specific
   *  item they're about. */
  _buildNeedsAttentionSection(signals, total, counts) {
    const section = document.createElement("div");
    section.className = "fm-block";

    const label = document.createElement("div");
    label.className = "fm-section-label";
    label.textContent = "Needs attention";
    section.appendChild(label);

    if (total === 0) {
      const p = document.createElement("p");
      p.textContent = "Nothing needs your attention right now \u2014 everything is running as expected.";
      section.appendChild(p);
      return section;
    }

    const countParts = [];
    if (counts.high) countParts.push(`${counts.high} high`);
    if (counts.medium) countParts.push(`${counts.medium} medium`);
    if (counts.low) countParts.push(`${counts.low} low`);
    const summary = document.createElement("p");
    summary.textContent = `${total} item${total === 1 ? "" : "s"} need${total === 1 ? "s" : ""} your attention: ${countParts.join(", ")}.`;
    section.appendChild(summary);

    const list = document.createElement("div");
    list.className = "fm-signal-list";
    signals.forEach((n) => {
      const item = document.createElement("div");
      item.className = "fm-signal-item";
      item.innerHTML = `
        <span class="fm-signal-dot" style="background:${Notifications.colorFor(n.severity)}"></span>
        <span class="fm-signal-text">${n.message}</span>
        <span class="fm-signal-action">${this._signalActionLabel(n.type)}</span>
      `;
      item.addEventListener("click", () => Notifications.handleClick(n));
      list.appendChild(item);
    });
    section.appendChild(list);

    return section;
  },

  /** "Spend snapshot" — the briefing's second labeled section, kept
   *  separate from "Needs attention" rather than folded into one
   *  undifferentiated paragraph. */
  _buildSpendSnapshotSection(snapshot) {
    const section = document.createElement("div");
    section.className = "fm-block";

    const label = document.createElement("div");
    label.className = "fm-section-label";
    label.textContent = "Spend snapshot";
    section.appendChild(label);

    const text = `Total spend stands at ${formatCurrency(snapshot.total_spend)} this period, with ${formatCurrency(snapshot.cost_savings_period)} in savings identified so far.`;
    const body = document.createElement("div");
    body.innerHTML = this._parseBlocks(text).map((b) => b.html).join("");
    section.appendChild(body);

    return section;
  },

  /** count-valued charts (like Requisition Status: 19/4/1 requisitions)
   *  must never be run through formatCurrency() — only charts explicitly
   *  marked currency (or unmarked, defaulting to currency for backward
   *  compatibility with every chart-producing skill that predates this
   *  field) get the ₹ treatment. */
  _formatChartValue(val, chart) {
    return chart.value_unit === "count" ? Number(val).toLocaleString() : formatCurrency(val);
  },

  _buildChartBlock(chart, msgIndex, chartIndex) {
    const wrap = document.createElement("div");
    wrap.className = "fm-sparkline-wrap";
    wrap.title = "Click to view full chart";

    const isDoughnut = chart.chart_type === "doughnut";
    const fmtVal = (v) => this._formatChartValue(v, chart);

    const header = document.createElement("div");
    header.className = "fm-sparkline-header";

    if (isDoughnut) {
      // a "first slice -> last slice" trend line makes no sense for a
      // part-of-whole split (there's no before/after relationship between
      // two pie slices) — show a per-slice breakdown instead
      const labels = chart.labels || [];
      const data = (chart.datasets && chart.datasets[0] && chart.datasets[0].data) || [];
      const breakdown = labels.map((l, i) => `${l} ${fmtVal(data[i])}`).join(" \u00b7 ");
      header.innerHTML = `
        <span class="fm-sparkline-title">${chart.title || "Breakdown"}</span>
        <span class="fm-sparkline-values" style="white-space:normal;">${breakdown}</span>
      `;
    } else {
      const data = (chart.datasets && chart.datasets[0] && chart.datasets[0].data) || [];
      const nums = data.filter((v) => v !== null && v !== undefined);
      const first = nums[0];
      const last = nums[nums.length - 1];
      const hasRange = first !== undefined && last !== undefined && nums.length > 1;
      const trendUp = hasRange && last >= first;
      header.innerHTML = `
        <span class="fm-sparkline-title">${chart.title || "Trend"}</span>
        ${hasRange ? `
          <span class="fm-sparkline-values">
            ${fmtVal(first)}
            <i class="ti ti-arrow-right"></i>
            <span class="${trendUp ? "fm-spark-up" : "fm-spark-down"}">${fmtVal(last)}</span>
          </span>
        ` : ""}
      `;
    }
    wrap.appendChild(header);

    const canvasBox = document.createElement("div");
    canvasBox.className = "fm-sparkline-canvas-box";
    const canvas = document.createElement("canvas");
    canvas.id = `fm-spark-${msgIndex}-${chartIndex}-${Math.random().toString(36).slice(2, 7)}`;
    canvasBox.appendChild(canvas);
    wrap.appendChild(canvasBox);

    requestAnimationFrame(() => this._renderSparkline(canvas, chart));
    wrap.addEventListener("click", () => this.openChartModal(chart));
    return wrap;
  },

  _renderSparkline(canvas, chartSpec) {
    if (typeof Chart === "undefined") return;
    const ds = (chartSpec.datasets || [])[0] || { data: [] };
    const isLine = chartSpec.chart_type === "line";
    const isDoughnut = chartSpec.chart_type === "doughnut";

    if (isDoughnut) {
      new Chart(canvas, {
        type: "doughnut",
        data: {
          labels: chartSpec.labels || [],
          datasets: [{ data: ds.data, backgroundColor: CHART_SLICE_COLORS, borderWidth: 0 }],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          cutout: "68%",
          plugins: { legend: { display: false }, tooltip: { enabled: false } },
        },
      });
      return;
    }

    new Chart(canvas, {
      type: isLine ? "line" : "bar",
      data: {
        labels: chartSpec.labels || [],
        datasets: [{
          data: ds.data,
          borderColor: "#9B8CFF",
          backgroundColor: isLine ? "rgba(108,92,231,0.18)" : "rgba(108,92,231,0.6)",
          borderWidth: 2,
          tension: 0.35,
          fill: isLine,
          pointRadius: isLine ? 2.5 : 0,
          pointBackgroundColor: "#9B8CFF",
          pointBorderWidth: 0,
          borderRadius: isLine ? 0 : 4,
          maxBarThickness: 28,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: false }, tooltip: { enabled: false } },
        scales: {
          x: { display: false },
          y: { display: false, beginAtZero: !isLine },
        },
        elements: { line: { borderJoinStyle: "round" } },
      },
    });
  },

  // ---------------------------------------------------------------------
  // Chart detail modal — click an inline sparkline to open a full,
  // interactive (hoverable, tooltipped) version of the same chart data
  // ---------------------------------------------------------------------
  openChartModal(chartSpec) {
    this._modalOpen = true;
    this.chartModalTitle.textContent = chartSpec.title || "Chart";
    this.chartModal.classList.add("open");

    if (this._modalChart) { this._modalChart.destroy(); this._modalChart = null; }
    if (typeof Chart === "undefined") return;

    const ds = (chartSpec.datasets || [])[0] || { data: [], label: "" };
    const isDoughnut = chartSpec.chart_type === "doughnut";

    if (isDoughnut) {
      this._modalChart = new Chart(this.chartModalCanvas, {
        type: "doughnut",
        data: {
          labels: chartSpec.labels || [],
          datasets: [{ data: ds.data, backgroundColor: CHART_SLICE_COLORS, borderWidth: 0 }],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          cutout: "62%",
          plugins: {
            legend: { display: true, position: "bottom", labels: { color: "#B8BECC", font: { size: 11.5 } } },
            tooltip: {
              enabled: true, backgroundColor: "#181B25", titleColor: "#F3F4F8",
              bodyColor: "#B8BECC", borderColor: "#232733", borderWidth: 1, padding: 10,
            },
          },
        },
      });
      return;
    }

    this._modalChart = new Chart(this.chartModalCanvas, {
      type: chartSpec.chart_type === "line" ? "line" : "bar",
      data: {
        labels: chartSpec.labels || [],
        datasets: [{
          label: ds.label || "",
          data: ds.data,
          borderColor: "#9B8CFF",
          backgroundColor: "rgba(108,92,231,0.35)",
          borderWidth: 2,
          tension: 0.3,
          pointRadius: 4,
          pointHoverRadius: 6,
          pointBackgroundColor: "#9B8CFF",
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { display: !!ds.label, labels: { color: "#B8BECC", font: { size: 11.5 } } },
          tooltip: {
            enabled: true, backgroundColor: "#181B25", titleColor: "#F3F4F8",
            bodyColor: "#B8BECC", borderColor: "#232733", borderWidth: 1, padding: 10,
          },
        },
        scales: {
          x: { ticks: { color: "#8B93A7", font: { size: 11 } }, grid: { color: "#232733" } },
          y: { ticks: { color: "#8B93A7", font: { size: 11 } }, grid: { color: "#232733" } },
        },
      },
    });
  },

  closeChartModal() {
    this._modalOpen = false;
    this.chartModal.classList.remove("open");
  },

  // ---------------------------------------------------------------------
  // Pulse Rail — Agent Trail, reimagined as an ambient vertical signal
  // ---------------------------------------------------------------------
  addPulse(evt, { silent = false } = {}) {
    if (!this.rail) return;
    this._pulseCount += 1;
    const dot = document.createElement("div");
    dot.className = "fm-pulse-dot";
    dot.style.top = `${Math.min(4 + this._pulseCount * 7, 94)}%`;
    dot.style.background = Trail.colorFor ? Trail.colorFor(evt.agent) : "#6C5CE7";
    dot.title = `${evt.agent} \u2192 ${evt.skill}`;
    if (!silent) dot.style.animation = "trailSlideIn 0.4s ease";
    this.rail.appendChild(dot);

    if (evt.result && evt.result.flagged) {
      this.overlay.classList.add("fm-alert");
      setTimeout(() => this.overlay.classList.remove("fm-alert"), 2200);
    }
  },

  /**
   * Proactive signals get their own marker — a diamond, not a circle — so
   * they're visually distinguishable from ordinary agent-action dots on
   * the same rail. This is what keeps Focus Mode proactive even though it
   * hides the notification bell entirely (Gap 1 from the UX plan).
   */
  addSignalPulse(evt, { silent = false } = {}) {
    if (!this.rail) return;
    this._pulseCount += 1;
    const dot = document.createElement("div");
    dot.className = "fm-pulse-dot fm-pulse-signal";
    dot.style.top = `${Math.min(4 + this._pulseCount * 7, 94)}%`;
    dot.style.background = Notifications.colorFor(evt.severity);
    dot.title = `${Notifications.labelFor(evt.type)}: ${evt.message}`;
    dot.addEventListener("click", () => Notifications.handleClick(evt));
    if (!silent) dot.style.animation = "trailSlideIn 0.4s ease";
    this.rail.appendChild(dot);

    this.overlay.classList.add("fm-alert");
    setTimeout(() => this.overlay.classList.remove("fm-alert"), 2200);
  },
};
