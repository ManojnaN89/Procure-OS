/**
 * Agent Trail: one WebSocket feed that drives three surfaces — the drawer,
 * the floating assistant's live ticker, and (via API.getTrail) the full
 * history page. This module owns the live connection and a small in-memory
 * ring buffer; each surface renders from it independently.
 */
const AGENT_COLORS = {
  "Orchestrator": "#9B8CFF",
  "Auction Agent": "#f5a35c",
  "RFQ Agent": "#4f8dfd",
  "Requisition Agent": "#2ECC71",
  "Source-to-Pay Agent": "#ff6b9d",
};

const Trail = {
  socket: null,
  recent: [], // ring buffer, newest first
  maxBuffer: 30,
  listeners: [],

  init(sessionId) {
    this.connect(sessionId);
  },

  subscribe(fn) {
    this.listeners.push(fn);
    return () => { this.listeners = this.listeners.filter((l) => l !== fn); };
  },

  notify(event) {
    this.listeners.forEach((fn) => fn(event, this.recent));
  },

  connect(sessionId) {
    const proto = window.location.protocol === "https:" ? "wss" : "ws";
    const url = `${proto}://${window.location.host}/ws/trail/${sessionId}`;
    try {
      this.socket = new WebSocket(url);
    } catch {
      return;
    }

    this.socket.addEventListener("open", () => {
      const dotEl = document.getElementById("trailDot");
      if (dotEl) dotEl.style.display = "none";
    });

    this.socket.addEventListener("message", (msg) => {
      try {
        const evt = JSON.parse(msg.data);
        this.recent.unshift(evt);
        if (this.recent.length > this.maxBuffer) this.recent.pop();
        const dotEl = document.getElementById("trailDot");
        if (dotEl) dotEl.style.display = "block";
        this.notify(evt);
      } catch { /* ignore malformed frames */ }
    });
  },

  colorFor(agent) {
    return AGENT_COLORS[agent] || "#4f8dfd";
  },

  summarize(result) {
    if (!result || typeof result !== "object") return String(result);
    if (result.error) return `\u26a0 ${result.error}`;
    return Object.entries(result).slice(0, 3)
      .map(([k, v]) => `${k}: ${JSON.stringify(v)}`).join("  \u00b7  ");
  },

  /** Builds one trail-entry DOM node — shared markup for drawer, ticker detail, and full page. */
  renderEntry(evt, { expandable = true, isNew = false } = {}) {
    const el = document.createElement("div");
    el.className = "trail-entry" + (isNew ? " new" : "");
    el.style.setProperty("--agent-color", this.colorFor(evt.agent));
    el.style.borderLeftColor = this.colorFor(evt.agent);
    const time = evt.created_at || evt.timestamp
      ? new Date(evt.created_at || evt.timestamp).toLocaleTimeString()
      : "";
    el.innerHTML = `
      <div class="trail-entry-top">
        <span class="trail-agent" style="color:${this.colorFor(evt.agent)}">${evt.agent}</span>
        <span class="trail-time">${time}</span>
      </div>
      <div class="trail-skill">\u2192 ${evt.skill}</div>
      <div class="trail-meta">${this.summarize(evt.result)}</div>
      <div class="trail-detail">Arguments\n${JSON.stringify(evt.args, null, 2)}\n\nResult\n${JSON.stringify(evt.result, null, 2)}</div>
    `;
    if (expandable) {
      el.addEventListener("click", () => el.classList.toggle("expanded"));
    }
    return el;
  },

  /** Exports the persisted trail from Postgres (via /api/trail), not the
   *  live in-memory buffer — the buffer resets on every page refresh even
   *  though the real audit trail survives in the database the whole time. */
  async exportLog() {
    try {
      const rows = await API.getTrail({ session_id: Conversation.sessionId, limit: 500 });
      const blob = new Blob([JSON.stringify(rows, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `procureos-agent-trail-${Date.now()}.json`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      alert("Couldn't export the trail log: " + err.message);
    }
  },
};
