/**
 * Renders chat messages and streams tokens into the active assistant bubble.
 */
const Chat = {
  logEl: null,

  init() {
    this.logEl = document.getElementById("chatLog");
  },

  scrollToBottom() {
    this.logEl.scrollTop = this.logEl.scrollHeight;
  },

  addUserMessage(text) {
    const row = document.createElement("div");
    row.className = "msg msg-user";
    row.innerHTML = `
      <div class="msg-avatar">U</div>
      <div class="msg-bubble"></div>
    `;
    row.querySelector(".msg-bubble").textContent = text;
    this.logEl.appendChild(row);
    this.scrollToBottom();
  },

  startAssistantMessage() {
    const row = document.createElement("div");
    row.className = "msg msg-assistant";
    row.innerHTML = `
      <div class="msg-avatar">P</div>
      <div class="msg-bubble"></div>
    `;
    this.logEl.appendChild(row);
    this.scrollToBottom();
    return row.querySelector(".msg-bubble");
  },

  appendToken(bubbleEl, token) {
    bubbleEl.textContent += token;
    this.scrollToBottom();
  },
};
