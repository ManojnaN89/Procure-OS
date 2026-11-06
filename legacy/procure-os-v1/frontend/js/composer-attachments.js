/**
 * Shared file-attach logic for both composers (full Assistant page and the
 * floating panel). Uploads via the existing /api/upload endpoint (working
 * since Phase B, previously unreachable through the UI), shows a removable
 * chip, and folds the extracted content into the outgoing message only
 * when Send is actually pressed — nothing uploads-and-acts automatically.
 */
const ComposerAttachment = {
  makeController(fileInput, chipContainer) {
    let pending = null; // { filename, content }

    fileInput.addEventListener("change", async () => {
      const file = fileInput.files[0];
      if (!file) return;

      chipContainer.innerHTML = `<span class="attach-chip attach-chip-loading">Reading ${file.name}\u2026</span>`;

      const formData = new FormData();
      formData.append("file", file);
      try {
        const res = await fetch("/api/upload", { method: "POST", body: formData });
        const data = await res.json();

        if (data.error) {
          chipContainer.innerHTML = `<span class="attach-chip attach-chip-error">${data.error}</span>`;
          pending = null;
          return;
        }

        const content = data.extracted_text || JSON.stringify(data.rows || []);
        pending = { filename: data.filename, content };

        chipContainer.innerHTML = `
          <span class="attach-chip">
            <i class="ti ti-paperclip"></i> ${data.filename}
            <i class="ti ti-x attach-chip-remove"></i>
          </span>
        `;
        chipContainer.querySelector(".attach-chip-remove").addEventListener("click", () => this.clear(fileInput, chipContainer, () => { pending = null; }));
      } catch {
        chipContainer.innerHTML = `<span class="attach-chip attach-chip-error">Upload failed \u2014 is the backend running?</span>`;
        pending = null;
      }
    });

    return {
      hasPending: () => !!pending,
      /** Bundles the extracted file content ahead of whatever the user typed,
       *  so the agent's LLM call reads it in context (same design as Phase B:
       *  no separate "parsing skill", the model maps fields itself). */
      buildMessage(userText) {
        if (!pending) return userText;
        const prefix = `Here is the content of an uploaded file (${pending.filename}):\n\n${pending.content}\n\n`;
        return prefix + (userText && userText.trim() ? userText : "Please process this file.");
      },
      clear: () => this.clear(fileInput, chipContainer, () => { pending = null; }),
    };
  },

  clear(fileInput, chipContainer, onClear) {
    onClear();
    chipContainer.innerHTML = "";
    fileInput.value = "";
  },
};
