/**
 * The floating Assistant: present on every page. Reads/writes the same
 * Conversation state as the full Assistant page (assistant.js renders
 * both), and shows a live one-line ticker from the Trail feed while
 * collapsed, so there's ambient awareness of agent activity even when
 * the panel is closed.
 */
const FloatingAssistant = {
  init() {
    this.fab = document.getElementById("assistantFab");
    this.panel = document.getElementById("floatingPanel");
    this.body = document.getElementById("floatingPanelBody");
    this.input = document.getElementById("floatingInput");
    this.sendBtn = document.getElementById("floatingSendBtn");
    this.closeBtn = document.getElementById("floatingPanelClose");
    this.tickerPill = document.getElementById("tickerPill");
    this.tickerText = document.getElementById("tickerText");
    this.attachment = ComposerAttachment.makeController(
      document.getElementById("floatingFileInput"),
      document.getElementById("floatingAttachChip"),
    );
    document.getElementById("floatingAttachBtn").addEventListener("click", () => {
      document.getElementById("floatingFileInput").click();
    });
    Voice.wireMicButton(document.getElementById("floatingMicBtn"));

    this.fab.addEventListener("click", () => this.toggle());
    this.closeBtn.addEventListener("click", () => this.close());
    this.sendBtn.addEventListener("click", () => this.submit());
    this.input.addEventListener("keydown", (e) => { if (e.key === "Enter") this.submit(); });

    Conversation.subscribe((messages) => {
      if (this.panel.classList.contains("open")) {
        Assistant.renderThread(this.body, messages, { compact: true });
      }
      this.sendBtn.disabled = false;
    });

    Trail.subscribe((evt) => {
      this.fab.classList.add("active");
      this.showTicker(`${evt.agent} \u2192 ${evt.skill}`, "trail");
      clearTimeout(this._pulseTimer);
      this._pulseTimer = setTimeout(() => this.fab.classList.remove("active"), 3000);
    });

    // Notifications are a second, independent event source — reused here
    // so the ticker gives ambient awareness of BOTH "what I asked for"
    // and "what the system noticed on its own." Amber-tinted and prefixed
    // so the two are never mistaken for each other.
    Notifications.subscribe((evt) => {
      this.fab.classList.add("active");
      this.showTicker(`${Notifications.labelFor(evt.type)} \u2014 ${evt.message}`, "notification");
      clearTimeout(this._pulseTimer);
      this._pulseTimer = setTimeout(() => this.fab.classList.remove("active"), 3000);
    });
  },

  showTicker(text, kind = "trail") {
    this.tickerText.textContent = text;
    this.tickerPill.classList.toggle("ticker-notification", kind === "notification");
    this.tickerPill.style.display = "block";
    this.tickerText.style.animation = "none";
    // restart the CSS animation
    void this.tickerText.offsetWidth;
    this.tickerText.style.animation = "tickerFade 4s ease-in-out";
    clearTimeout(this._tickerTimer);
    this._tickerTimer = setTimeout(() => { this.tickerPill.style.display = "none"; }, 4200);
  },

  toggle() {
    this.panel.classList.contains("open") ? this.close() : this.open();
  },

  open() {
    // mutual exclusion with the trail drawer — avoid two floating overlays at once
    if (typeof TrailDrawer !== "undefined") TrailDrawer.close();
    this.panel.classList.add("open");
    Assistant.renderThread(this.body, Conversation.messages, { compact: true });
    this.input.focus();
  },

  close() {
    this.panel.classList.remove("open");
  },

  submit() {
    const text = this.input.value.trim();
    if (!text && !this.attachment.hasPending()) return;
    this.input.value = "";
    this.sendBtn.disabled = true;
    Conversation.send(this.attachment.buildMessage(text));
    this.attachment.clear();
  },
};
