/** Text from the data (names, messages, reasons) goes into the page as text, never as markup. */
function escapeHtml(value) {
  return String(value ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

/**
 * Every document state has one of eight tones, so a status looks the same on every screen (colours in css/tokens.css).
 * A state not listed here shows as a draft (grey); the word is always shown, so colour is never the only signal.
 */
const STATUS_TONES = {
  draft: "draft", returned: "draft", withdrawn: "draft", cancelled: "draft", closed: "draft", expired: "draft", inactive: "draft",
  pending: "waiting", submitted: "waiting", waiting: "waiting", proposed: "waiting", sourcing: "waiting", processing: "waiting",
  part_received: "waiting", part_paid: "waiting", onboarding: "waiting", rfq_out: "waiting",
  approved: "approved", auto_approved: "approved", active: "approved", awarded: "approved", matched: "approved",
  acknowledged: "approved", ordered: "approved",
  sent: "sent", open: "sent", issued: "sent", file_generated: "sent",
  received: "received",
  blocked: "held", held: "held", on_hold: "held", suspended: "held",
  paid: "paid",
  rejected: "rejected", failed: "rejected", blacklisted: "rejected",
};

function statusTone(status) {
  return STATUS_TONES[String(status || "").toLowerCase().replace(/[\s-]+/g, "_")] || "draft";
}

/** A status as a badge: the tone's colour, a dot and the word. Shared across every list and detail view. */
function statusBadge(status) {
  const key = String(status || "").toLowerCase().replace(/\s+/g, "-");
  const label = (status || "unknown").replace(/_/g, " ");
  return `<span class="badge badge-${escapeHtml(key)} badge-${statusTone(status)}">${escapeHtml(label.charAt(0).toUpperCase() + label.slice(1))}</span>`;
}
/**
 * Currency was previously a free-text symbol with no matching number-
 * grouping logic — someone could set "$" and still get Indian-style digit
 * grouping (1,23,456) underneath, which is silently wrong output, not just
 * a missing feature. Presets bundle a symbol with its real locale so an
 * invalid combination is impossible — narrower than free text on purpose.
 * AppConfig is populated once at startup (app.js) before the first page
 * renders, and falls back to the India preset only if that fetch hasn't
 * completed yet.
 */
const CURRENCY_PRESETS = {
  INR: { symbol: "\u20b9", locale: "en-IN", label: "India (\u20b9)" },
  USD: { symbol: "$", locale: "en-US", label: "United States ($)" },
  EUR: { symbol: "\u20ac", locale: "en-IE", label: "Eurozone (\u20ac)" },
  GBP: { symbol: "\u00a3", locale: "en-GB", label: "United Kingdom (\u00a3)" },
};

const AppConfig = {
  currencySymbol: CURRENCY_PRESETS.INR.symbol,
  currencyLocale: CURRENCY_PRESETS.INR.locale,
  currencyPreset: "INR",
  launchInFocusMode: false,
  async load() {
    try {
      const settings = await API.getSettings();
      const preset = CURRENCY_PRESETS[settings.currency_preset];
      if (preset) {
        this.currencyPreset = settings.currency_preset;
        this.currencySymbol = preset.symbol;
        this.currencyLocale = preset.locale;
      }
      if (settings.launch_in_focus_mode !== undefined) {
        this.launchInFocusMode = String(settings.launch_in_focus_mode).toLowerCase() === "true";
      }
    } catch {
      /* keep the defaults if settings can't be reached yet */
    }
  },
};

function formatCurrency(n) {
  if (n === null || n === undefined) return "\u2014";
  const v = Number(n);
  // whole rupees stay plain; anything with paise always shows both decimals (525.8 would read as 525.08)
  const digits = Number.isInteger(v) ? 0 : 2;
  return AppConfig.currencySymbol + v.toLocaleString(AppConfig.currencyLocale, { minimumFractionDigits: digits, maximumFractionDigits: 2 });
}

function formatDate(iso) {
  if (!iso) return "\u2014";
  return new Date(iso).toLocaleDateString(AppConfig.currencyLocale, { day: "numeric", month: "short", year: "numeric" });
}
