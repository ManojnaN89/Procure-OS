/**
 * Shared conversation state. Both the full Assistant page and the floating
 * panel read/write this same object, so switching between them never loses
 * context — there is exactly one message list and one session id.
 */
const Conversation = {
  sessionId: null,
  messages: [], // { role: 'user'|'assistant', content: string, pending?: bool }
  category: null, // last agent that responded, e.g. "rfq"
  listeners: [],

  init() {
    let id = localStorage.getItem("procureos_session_id");
    if (!id) {
      id = crypto.randomUUID();
      localStorage.setItem("procureos_session_id", id);
    }
    this.sessionId = id;
  },

  subscribe(fn) {
    this.listeners.push(fn);
    return () => { this.listeners = this.listeners.filter((l) => l !== fn); };
  },

  notify() {
    this.listeners.forEach((fn) => fn(this.messages, this.category));
  },

  /** Sends a message and streams the reply into shared state; every
   *  subscriber (full page + floating panel) re-renders as tokens arrive. */
  send(text, { agent = null } = {}) {
    if (!text || !text.trim()) return;
    this.messages.push({ role: "user", content: text });
    const assistantMsg = { role: "assistant", content: "", pending: true, charts: [], extras: [], suggestions: [] };
    this.messages.push(assistantMsg);
    this.notify();

    API.sendMessage(this.sessionId, text, {
      onSession: (sid, category) => {
        this.sessionId = sid;
        this.category = category;
        this.notify();
      },
      onToken: (token) => {
        assistantMsg.content += token;
        this.notify();
      },
      onChart: (chart) => {
        assistantMsg.charts.push(chart);
        this.notify();
      },
      onExtras: (extras) => {
        assistantMsg.extras = extras || [];
        this.notify();
      },
      onSuggestions: (suggestions) => {
        assistantMsg.suggestions = suggestions || [];
        this.notify();
      },
      onDone: () => {
        assistantMsg.pending = false;
        this.notify();
        if (typeof Dashboard !== "undefined" && Dashboard.refreshIfMounted) {
          Dashboard.refreshIfMounted();
        }
        if (typeof Voice !== "undefined" && Voice.speakingEnabled) {
          Voice.speak(assistantMsg.content);
        }
      },
      onError: (err) => {
        assistantMsg.content += `\n\u26a0 ${err.message}`;
        assistantMsg.pending = false;
        this.notify();
      },
    }, agent);
  },
};

Conversation.init();

/**
 * Shared prefill queue: "New RFQ" / "New auction" etc. buttons on list pages
 * navigate to the Assistant page and pre-fill its composer with a starter
 * prompt, rather than opening a separate write path. Approve/reject buttons
 * (which have all the info they need already) send directly instead.
 */
const Prefill = {
  text: null,
  set(text) { this.text = text; },
  consume() { const t = this.text; this.text = null; return t; },
};
