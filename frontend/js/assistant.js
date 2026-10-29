/**
 * Shared rendering logic for the conversation thread. Both the full
 * Assistant page and the floating panel call Assistant.renderThread()
 * against their own container, reading from the same Conversation state —
 * so the two surfaces never show different content.
 *
 * Known simplification: the whole thread re-renders (and any inline chart
 * canvases get rebuilt) on every streamed token, since Conversation.notify()
 * fires per-token. Fine at this scale (short replies, 0-1 charts each); a
 * larger rewrite would diff instead of rebuild if replies got much longer.
 */
const CHART_COLORS = ["#6C5CE7", "#2ECC71", "#4f8dfd", "#F5A623", "#ff6b9d"];
const BUBBLE_SIGNAL_ACTION_LABELS = {
  exception_flagged: "Review",
  vendor_risk: "View vendor",
  stale_requisition: "Review",
  budget_overrun: "Ask about this",
  contract_expiring: "View contract",
};
/**
 * Minimal, safe markdown renderer: escapes ALL raw text first (so nothing
 * the model writes — including literal HTML tags — can ever inject markup),
 * then interprets only **bold**, *italic*, "- "/"* " bullets, and "1. "
 * numbered lists. This is not a full markdown parser on purpose — just
 * enough to make typical LLM-formatted replies readable instead of showing
 * raw asterisks.
 */
function renderMarkdownSafe(text) {
  const escapeHtml = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const inlineFormat = (s) => InteractiveElements.linkifyEntities(escapeHtml(s)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/(?<!\*)\*([^*\n]+?)\*(?!\*)/g, "<em>$1</em>"));

  const lines = text.split("\n");
  let html = "";
  let listType = null; // 'ul' | 'ol' | null
  const closeList = () => { if (listType) { html += listType === "ul" ? "</ul>" : "</ol>"; listType = null; } };

  lines.forEach((line) => {
    const bulletMatch = line.match(/^\s*[-*]\s+(.*)/);
    const numMatch = line.match(/^\s*\d+\.\s+(.*)/);
    if (bulletMatch) {
      if (listType !== "ul") { closeList(); html += "<ul>"; listType = "ul"; }
      html += `<li>${inlineFormat(bulletMatch[1])}</li>`;
    } else if (numMatch) {
      if (listType !== "ol") { closeList(); html += "<ol>"; listType = "ol"; }
      html += `<li>${inlineFormat(numMatch[1])}</li>`;
    } else {
      closeList();
      html += line.trim() === "" ? "<br>" : `<p>${inlineFormat(line)}</p>`;
    }
  });
  closeList();
  return html;
}

const Assistant = {
  renderThread(container, messages, { compact = false } = {}) {
    container.innerHTML = "";
    if (messages.length === 0) {
      const empty = document.createElement("div");
      empty.className = "empty-state";
      empty.textContent = compact
        ? "Ask the assistant to do something."
        : "Try: \"Onboard a vendor called Acme Supplies, email acme@supplies.com, category electronics\"";
      container.appendChild(empty);
      return;
    }

    messages.forEach((m, i) => {
      const row = document.createElement("div");
      row.className = `chat-msg ${m.role}`;

      if (m.role === "assistant") {
        const avatar = document.createElement("div");
        avatar.className = "chat-avatar";
        avatar.textContent = "P";
        row.appendChild(avatar);
      }

      const bubble = document.createElement("div");
      bubble.className = "chat-bubble";

      if (m.pending && m.content === "" && (!m.charts || m.charts.length === 0)) {
        bubble.innerHTML = `<div class="typing-dots"><span></span><span></span><span></span></div>`;
      } else {
        if (m.charts && m.charts.length) {
          const chartHost = m.charts.length > 1 ? document.createElement("div") : bubble;
          if (m.charts.length > 1) {
            chartHost.className = "chart-row";
            bubble.appendChild(chartHost);
          }
          m.charts.forEach((chart, ci) => {
            const canvasWrap = document.createElement("div");
            canvasWrap.className = "chart-wrap";
            const canvas = document.createElement("canvas");
            canvas.id = `chart-${i}-${ci}-${Math.random().toString(36).slice(2, 8)}`;
            canvasWrap.appendChild(canvas);
            chartHost.appendChild(canvasWrap);
            // deferred so the canvas exists in the DOM before Chart.js measures it
            requestAnimationFrame(() => this._renderChart(canvas, chart));
          });
        }
        if (m.content) {
          const textEl = document.createElement("div");
          textEl.className = "chat-bubble-text";
          textEl.innerHTML = renderMarkdownSafe(m.content);
          bubble.appendChild(textEl);
          InteractiveElements.wireEntityChips(textEl);
        }
        if (m.extras && m.extras.length) {
          InteractiveElements.renderExtras(bubble, m.extras, "bubble");
        }
        // the launch briefing carries the same two labeled sections as
        // Focus Mode's version — "Needs attention" (per-item inline
        // action) and "Spend snapshot" — styled to fit a chat bubble
        // instead of the Living Report's typography. Permanent part of
        // this message, not gated to "last message only" like the
        // suggestion chips below.
        if (m.signalTotal !== undefined) {
          const section = document.createElement("div");
          section.className = "bubble-section";
          const label = document.createElement("div");
          label.className = "bubble-section-label";
          label.textContent = "Needs attention";
          section.appendChild(label);

          if (m.signalTotal === 0) {
            const p = document.createElement("div");
            p.className = "chat-bubble-text";
            p.textContent = "Nothing needs your attention right now \u2014 everything is running as expected.";
            section.appendChild(p);
          } else {
            const counts = m.signalCounts || {};
            const parts = [];
            if (counts.high) parts.push(`${counts.high} high`);
            if (counts.medium) parts.push(`${counts.medium} medium`);
            if (counts.low) parts.push(`${counts.low} low`);
            const summary = document.createElement("div");
            summary.className = "chat-bubble-text";
            summary.textContent = `${m.signalTotal} item${m.signalTotal === 1 ? "" : "s"} need${m.signalTotal === 1 ? "s" : ""} your attention: ${parts.join(", ")}.`;
            section.appendChild(summary);

            const list = document.createElement("div");
            list.className = "bubble-signal-list";
            (m.signals || []).forEach((n) => {
              const item = document.createElement("div");
              item.className = "bubble-signal-item";
              item.innerHTML = `
                <span class="bubble-signal-dot" style="background:${Notifications.colorFor(n.severity)}"></span>
                <span class="bubble-signal-text">${n.message}</span>
                <span class="bubble-signal-action">${BUBBLE_SIGNAL_ACTION_LABELS[n.type] || "Review"}</span>
              `;
              item.addEventListener("click", () => { if (typeof Notifications !== "undefined") Notifications.handleClick(n); });
              list.appendChild(item);
            });
            section.appendChild(list);
          }
          bubble.appendChild(section);
        }
        if (m.spendSnapshot) {
          const section = document.createElement("div");
          section.className = "bubble-section";
          const label = document.createElement("div");
          label.className = "bubble-section-label";
          label.textContent = "Spend snapshot";
          section.appendChild(label);
          const text = document.createElement("div");
          text.className = "chat-bubble-text";
          text.innerHTML = renderMarkdownSafe(
            `Total spend stands at ${formatCurrency(m.spendSnapshot.total_spend)} this period, with ${formatCurrency(m.spendSnapshot.cost_savings_period)} in savings identified so far.`
          );
          section.appendChild(text);
          bubble.appendChild(section);
        }
      }
      row.appendChild(bubble);
      container.appendChild(row);

      // suggestion chips only under the most recent assistant message
      const isLast = i === messages.length - 1;
      if (isLast && m.role === "assistant" && !m.pending && m.suggestions && m.suggestions.length) {
        const chipRow = document.createElement("div");
        chipRow.className = "suggestion-row";
        m.suggestions.forEach((s) => {
          const chip = document.createElement("button");
          chip.className = "suggestion-chip";
          chip.textContent = s.label;
          chip.addEventListener("click", () => Conversation.send(s.prompt));
          chipRow.appendChild(chip);
        });
        container.appendChild(chipRow);
      }
    });

    container.scrollTop = container.scrollHeight;
  },

  _renderChart(canvas, chartSpec) {
    if (typeof Chart === "undefined") return; // Chart.js failed to load (offline) — fail quietly

    if (chartSpec.chart_type === "doughnut") {
      const ds = (chartSpec.datasets || [])[0] || { data: [] };
      new Chart(canvas, {
        type: "doughnut",
        data: {
          labels: chartSpec.labels || [],
          datasets: [{ data: ds.data, backgroundColor: CHART_COLORS, borderWidth: 0 }],
        },
        options: {
          responsive: true,
          cutout: "62%",
          plugins: {
            legend: { display: true, position: "bottom", labels: { color: "#B8BECC", font: { size: 10.5 } } },
            title: {
              display: !!chartSpec.title, text: chartSpec.title || "",
              color: "#F3F4F8", font: { size: 12, weight: "600" }, padding: { bottom: 10 },
            },
          },
        },
      });
      return;
    }

    const datasets = (chartSpec.datasets || []).map((ds, i) => ({
      label: ds.label,
      data: ds.data,
      backgroundColor: chartSpec.chart_type === "line" ? "transparent" : CHART_COLORS[i % CHART_COLORS.length],
      borderColor: CHART_COLORS[i % CHART_COLORS.length],
      borderWidth: 2,
      tension: 0.3,
      pointBackgroundColor: CHART_COLORS[i % CHART_COLORS.length],
    }));

    new Chart(canvas, {
      type: chartSpec.chart_type === "line" ? "line" : "bar",
      data: { labels: chartSpec.labels || [], datasets },
      options: {
        responsive: true,
        plugins: {
          legend: { display: datasets.length > 1, labels: { color: "#B8BECC", font: { size: 10.5 } } },
          title: {
            display: !!chartSpec.title, text: chartSpec.title || "",
            color: "#F3F4F8", font: { size: 12, weight: "600" }, padding: { bottom: 10 },
          },
        },
        scales: {
          x: { ticks: { color: "#8B93A7", font: { size: 10 } }, grid: { color: "#232733" } },
          y: { ticks: { color: "#8B93A7", font: { size: 10 } }, grid: { color: "#232733" } },
        },
      },
    });
  },

  statusLabel(category) {
    const map = {
      auction: "Auction Agent active",
      rfq: "RFQ Agent active",
      requisition: "Requisition Agent active",
      source_to_pay: "Source-to-Pay Agent active",
      insights: "Insights Agent active",
      general: "Assistant ready",
    };
    return map[category] || "Assistant ready";
  },
};
