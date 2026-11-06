/**
 * Voice input/output. Push-to-talk for this pass, not always-on wake-word
 * listening — that requires running openWakeWord's ONNX models in the
 * browser (onnxruntime-web), a genuinely separate, larger piece of work
 * from wiring up record → transcribe → send → speak. Scoped out of this
 * build deliberately, not silently dropped — see the delivery notes.
 *
 * The transcribed text goes straight into the exact same Conversation.send()
 * every typed message already uses — nothing about the chat pipeline
 * changes for voice input. Same principle for output: this only plays back
 * audio for the assistant's own replies, it doesn't add a second reply path.
 */
const Voice = {
  recording: false,
  mediaRecorder: null,
  audioChunks: [],
  speakingEnabled: false,

  async startRecording() {
    if (this.recording) return;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      this.mediaRecorder = new MediaRecorder(stream);
      this.audioChunks = [];
      this.mediaRecorder.addEventListener("dataavailable", (e) => this.audioChunks.push(e.data));
      this.mediaRecorder.start();
      this.recording = true;
    } catch (err) {
      console.error("Microphone access denied or unavailable:", err);
      throw err;
    }
  },

  /** Stops recording, transcribes what was captured, and sends it through
   *  the same Conversation.send() any typed message uses — voice is a new
   *  way to produce input text, not a parallel system. */
  stopRecordingAndSend() {
    if (!this.recording || !this.mediaRecorder) return;
    return new Promise((resolve, reject) => {
      this.mediaRecorder.addEventListener("stop", async () => {
        this.recording = false;
        const blob = new Blob(this.audioChunks, { type: "audio/webm" });
        try {
          const { text } = await API.transcribeAudio(blob);
          if (text && text.trim()) {
            Conversation.send(text.trim());
          }
          resolve(text);
        } catch (err) {
          console.error("Transcription failed:", err);
          reject(err);
        }
      }, { once: true });
      this.mediaRecorder.stop();
      this.mediaRecorder.stream.getTracks().forEach((t) => t.stop());
    });
  },

  /** Synthesizes and plays the given text, if voice output is enabled.
   *  Called after an assistant reply finishes streaming — see wireAutoSpeak(). */
  async speak(text) {
    if (!text || !text.trim()) return;
    try {
      const audioBlob = await API.speakText(text);
      const url = URL.createObjectURL(audioBlob);
      const audio = new Audio(url);
      audio.play();
      audio.addEventListener("ended", () => URL.revokeObjectURL(url));
    } catch (err) {
      console.error("Speech playback failed:", err);
    }
  },

  toggleSpeaking() {
    this.speakingEnabled = !this.speakingEnabled;
    return this.speakingEnabled;
  },

  /** Wires a mic button: hold-to-record on mousedown/touchstart, stop and
   *  send on mouseup/touchend. Matches the app's existing pattern of one
   *  small helper wired identically into every composer that wants it —
   *  same shape as ComposerAttachment.makeController(). */
  wireMicButton(button) {
    const start = (e) => { e.preventDefault(); this.startRecording().catch(() => {}); button.classList.add("voice-recording"); };
    const stop = (e) => { e.preventDefault(); button.classList.remove("voice-recording"); this.stopRecordingAndSend().catch(() => {}); };
    button.addEventListener("mousedown", start);
    button.addEventListener("mouseup", stop);
    button.addEventListener("mouseleave", stop);
    button.addEventListener("touchstart", start);
    button.addEventListener("touchend", stop);
  },
};
