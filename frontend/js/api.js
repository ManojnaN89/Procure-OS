/**
 * All backend calls. Read endpoints are plain fetch/JSON; chat streams
 * over SSE. Nothing here writes data directly except sendMessage, which
 * is the one path into the agent/skill layer.
 */
const API = {
  base: "",

  async getJSON(path) {
    const res = await fetch(`${this.base}${path}`);
    if (!res.ok) throw new Error(`Request failed: ${path} (${res.status})`);
    return res.json();
  },

  getDashboard() { return this.getJSON("/api/dashboard"); },

  getSettings() { return this.getJSON("/api/settings"); },
  async updateSettings(payload) {
    const res = await fetch(`${this.base}/api/settings`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    return res.json();
  },

  getBriefing() { return this.getJSON("/api/briefing"); },

  async transcribeAudio(audioBlob) {
    const formData = new FormData();
    formData.append("file", audioBlob, "recording.wav");
    const res = await fetch(`${this.base}/api/voice/transcribe`, { method: "POST", body: formData });
    if (!res.ok) throw new Error(`Transcription failed (${res.status})`);
    return res.json();
  },

  async speakText(text) {
    const formData = new FormData();
    formData.append("text", text);
    const res = await fetch(`${this.base}/api/voice/speak`, { method: "POST", body: formData });
    if (!res.ok) throw new Error(`Speech synthesis failed (${res.status})`);
    return res.blob();
  },

  listAuctions() { return this.getJSON("/api/auctions"); },
  getAuction(id) { return this.getJSON(`/api/auctions/${id}`); },

  listRfqs() { return this.getJSON("/api/rfqs"); },
  getRfq(id) { return this.getJSON(`/api/rfqs/${id}`); },

  listRequisitions() { return this.getJSON("/api/requisitions"); },
  getRequisition(id) { return this.getJSON(`/api/requisitions/${id}`); },

  listVendors() { return this.getJSON("/api/vendors"); },
  getVendor(id) { return this.getJSON(`/api/vendors/${id}`); },

  listPurchaseOrders() { return this.getJSON("/api/purchase-orders"); },
  getPurchaseOrder(id) { return this.getJSON(`/api/purchase-orders/${id}`); },

  listPayments() { return this.getJSON("/api/payments"); },

  listContracts() { return this.getJSON("/api/contracts"); },
  getContract(id) { return this.getJSON(`/api/contracts/${id}`); },

  listNotifications(params = {}) {
    const qs = new URLSearchParams(params).toString();
    return this.getJSON(`/api/notifications${qs ? "?" + qs : ""}`);
  },  getUnreadNotificationCount() { return this.getJSON("/api/notifications/unread-count"); },
  async markNotificationRead(id) {
    const res = await fetch(`${this.base}/api/notifications/${id}/read`, { method: "POST" });
    return res.json();
  },
  async resolveNotification(id) {
    const res = await fetch(`${this.base}/api/notifications/${id}/resolve`, { method: "POST" });
    return res.json();
  },

  search(q) { return this.getJSON(`/api/search?q=${encodeURIComponent(q)}`); },

  getTrail(params = {}) {
    const qs = new URLSearchParams(params).toString();
    return this.getJSON(`/api/trail${qs ? "?" + qs : ""}`);
  },

  /**
   * Streams an assistant reply over SSE. Callbacks fire as chunks arrive.
   * This is the single write path: every domain action goes through here.
   */
  async sendMessage(sessionId, message, { onSession, onToken, onChart, onExtras, onSuggestions, onDone, onError }, agent = null) {
    try {
      const res = await fetch(`${this.base}/api/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(agent ? { session_id: sessionId, message, agent } : { session_id: sessionId, message }),
      });
      if (!res.ok || !res.body) throw new Error(`Server returned ${res.status}`);

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const parts = buffer.split("\n\n");
        buffer = parts.pop();

        for (const part of parts) {
          const line = part.trim();
          if (!line.startsWith("data:")) continue;
          const jsonStr = line.slice(5).trim();
          if (!jsonStr) continue;
          let evt;
          try { evt = JSON.parse(jsonStr); } catch { continue; }

          if (evt.type === "session") onSession && onSession(evt.session_id, evt.category);
          if (evt.type === "token") onToken && onToken(evt.content);
          if (evt.type === "chart") onChart && onChart(evt.chart);
          if (evt.type === "extras") onExtras && onExtras(evt.extras);
          if (evt.type === "suggestions") onSuggestions && onSuggestions(evt.suggestions);
          if (evt.type === "done") onDone && onDone();
        }
      }
    } catch (err) {
      onError && onError(err);
    }
  },
};
