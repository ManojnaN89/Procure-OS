/** Maps a status string to a badge CSS class + label. Shared across every list/detail view. */
function statusBadge(status) {
  const cls = `badge-${(status || "").toLowerCase().replace(/\s+/g, "-")}`;
  const label = (status || "unknown").replace(/_/g, " ");
  return `<span class="badge ${cls}">${label.charAt(0).toUpperCase() + label.slice(1)}</span>`;
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
  launchInFocusMode: true,
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
  return AppConfig.currencySymbol + Number(n).toLocaleString(AppConfig.currencyLocale);
}

function formatDate(iso) {
  if (!iso) return "\u2014";
  return new Date(iso).toLocaleDateString(AppConfig.currencyLocale, { day: "numeric", month: "short", year: "numeric" });
}
