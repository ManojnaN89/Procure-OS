const AssistantPageView = {
  unsubscribe: null,
  unsubscribeTrail: null,

  async render(container) {
    container.innerHTML = `
      <div class="page-header page-header-row">
        <div>
          <h1>Assistant</h1>
          <p>Routed automatically to the right specialist agent</p>
        </div>
        <span class="status-pill" id="statusPill"><span class="dot"></span>Assistant ready</span>
      </div>
      <div class="two-col" style="align-items:start;">
        <div class="panel" style="display:flex;flex-direction:column;min-height:420px;">
          <div class="chat-thread" id="assistantThread" style="flex:1;overflow-y:auto;max-height:440px;"></div>
          <div class="attach-chip-row" id="assistantAttachChip"></div>
          <div class="chat-composer">
            <input type="file" id="assistantFileInput" accept=".pdf,.xlsx,.xls,.csv" style="display:none;" />
            <i class="ti ti-paperclip composer-attach-btn" id="assistantAttachBtn" title="Attach a quote or CSV"></i>
            <input type="text" id="assistantInput" placeholder="Ask ProcureOS to do something..." autocomplete="off" />
            <button class="send-btn" id="assistantSendBtn"><i class="ti ti-arrow-right"></i></button>
          </div>
        </div>
        <div class="panel">
          <div class="panel-title">Agent trail</div>
          <div class="trail-list" id="miniTrailList"></div>
        </div>
      </div>
    `;

    const thread = document.getElementById("assistantThread");
    const input = document.getElementById("assistantInput");
    const sendBtn = document.getElementById("assistantSendBtn");
    const statusPill = document.getElementById("statusPill");
    const miniTrail = document.getElementById("miniTrailList");

    const prefill = Prefill.consume();
    if (prefill) input.value = prefill;

    const attachment = ComposerAttachment.makeController(
      document.getElementById("assistantFileInput"),
      document.getElementById("assistantAttachChip"),
    );
    document.getElementById("assistantAttachBtn").addEventListener("click", () => {
      document.getElementById("assistantFileInput").click();
    });

    const submit = () => {
      const text = input.value.trim();
      if (!text && !attachment.hasPending()) return;
      input.value = "";
      sendBtn.disabled = true;
      Conversation.send(attachment.buildMessage(text));
      attachment.clear();
    };
    sendBtn.addEventListener("click", submit);
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") submit(); });
    input.focus();

    Assistant.renderThread(thread, Conversation.messages);

    this.unsubscribe = Conversation.subscribe((messages, category) => {
      Assistant.renderThread(thread, messages);
      statusPill.innerHTML = `<span class="dot"></span>${Assistant.statusLabel(category)}`;
      sendBtn.disabled = false;
    });

    const renderMiniTrail = () => {
      miniTrail.innerHTML = "";
      if (!Trail.recent.length) {
        miniTrail.innerHTML = `<div class="empty-state">No activity yet.</div>`;
        return;
      }
      Trail.recent.slice(0, 6).forEach((evt, i) => {
        miniTrail.appendChild(Trail.renderEntry(evt, { isNew: i === 0 }));
      });
    };
    renderMiniTrail();
    this.unsubscribeTrail = Trail.subscribe(renderMiniTrail);
  },

  unmount() {
    if (this.unsubscribe) this.unsubscribe();
    if (this.unsubscribeTrail) this.unsubscribeTrail();
  },
};
