const TrailPageView = {
  async render(container) {
    container.innerHTML = `
      <div class="page-header page-header-row">
        <div>
          <h1>Agent trail \u2014 full history</h1>
          <p>Every real action any agent has taken, filterable and exportable</p>
        </div>
        <div class="btn" id="exportFullBtn"><i class="ti ti-download"></i>Export log</div>
      </div>
      <div class="panel">
        <div style="display:flex;gap:8px;margin-bottom:14px;">
          <select id="agentFilter" style="background:var(--surface-sunken);border:1px solid var(--border);color:var(--text-primary);border-radius:8px;padding:7px 10px;font-size:12px;">
            <option value="">All agents</option>
            <option value="Orchestrator">Orchestrator</option>
            <option value="Auction Agent">Auction Agent</option>
            <option value="RFQ Agent">RFQ Agent</option>
            <option value="Requisition Agent">Requisition Agent</option>
            <option value="Source-to-Pay Agent">Source-to-Pay Agent</option>
          </select>
        </div>
        <div class="trail-list" id="fullTrailList"><div class="empty-state">Loading\u2026</div></div>
      </div>
    `;

    document.getElementById("exportFullBtn").addEventListener("click", () => Trail.exportLog());
    const filterEl = document.getElementById("agentFilter");
    filterEl.addEventListener("change", () => this.load(filterEl.value));

    await this.load();
  },

  async load(agent) {
    const list = document.getElementById("fullTrailList");
    if (!list) return;
    try {
      const params = { session_id: Conversation.sessionId, limit: 200 };
      if (agent) params.agent = agent;
      const rows = await API.getTrail(params);
      list.innerHTML = "";
      if (!rows.length) {
        list.innerHTML = `<div class="empty-state">No agent activity in this session yet.</div>`;
        return;
      }
      rows.forEach((evt) => list.appendChild(Trail.renderEntry(evt)));
    } catch {
      list.innerHTML = `<div class="empty-state">Couldn't load the trail history.</div>`;
    }
  },
};
