/**
 * Configuration page: the frontend half of settings.py. Exposes the
 * thresholds/cutoffs that drive proactive monitoring — deliberately not
 * the underlying risk-score formula itself (see settings.py's docstring
 * for why). Reachable via the gear icon in the top bar, not a sidebar
 * item, since this is app configuration, not a working page.
 */
const SettingsView = {
  async render(container) {
    container.innerHTML = `
      <div class="page-header">
        <h1>Configuration</h1>
        <p>Thresholds that drive exception flagging, risk scoring, and proactive signals \u2014 not the scoring formulas themselves.</p>
      </div>
      <div class="panel" id="settingsForm"><div class="empty-state">Loading\u2026</div></div>
    `;
    await this.load();
  },

  FIELDS: [
    { key: "exception_flag_threshold_pct", label: "Exception flag threshold", suffix: "% above category average", group: "Flagging" },
    { key: "stale_requisition_days", label: "Stale requisition after", suffix: "days pending", group: "Signals" },
    { key: "contract_expiring_days", label: "Contract expiring warning", suffix: "days before end date", group: "Signals" },
    { key: "signal_check_interval_minutes", label: "Background check interval", suffix: "minutes", group: "Signals" },
    { key: "risk_score_medium_cutoff", label: "Risk score \u2014 medium starts at", suffix: "points (of 100)", group: "Risk scoring" },
    { key: "risk_score_high_cutoff", label: "Risk score \u2014 high starts at", suffix: "points (of 100)", group: "Risk scoring" },
    { key: "currency_preset", label: "Currency", suffix: "symbol and number formatting, bundled together", group: "Display", isSelect: true, options: [
      { value: "INR", label: "India (\u20b9)" },
      { value: "USD", label: "United States ($)" },
      { value: "EUR", label: "Eurozone (\u20ac)" },
      { value: "GBP", label: "United Kingdom (\u00a3)" },
    ] },
    { key: "launch_in_focus_mode", label: "Launch in Focus Mode", suffix: "on app open, instead of the dashboard", group: "Display", isBoolean: true },
  ],

  async load() {
    const el = document.getElementById("settingsForm");
    let values;
    try {
      values = await API.getSettings();
    } catch {
      el.innerHTML = `<div class="empty-state">Couldn't load settings.</div>`;
      return;
    }

    const groups = {};
    this.FIELDS.forEach((f) => { (groups[f.group] = groups[f.group] || []).push(f); });

    el.innerHTML = Object.entries(groups).map(([group, fields]) => `
      <div style="margin-bottom:22px;">
        <div class="panel-title" style="margin-bottom:10px;">${group}</div>
        ${fields.map((f) => `
          <div style="display:flex;align-items:center;justify-content:space-between;padding:8px 0;border-bottom:1px solid var(--border);">
            <span style="color:var(--text-secondary);font-size:12.5px;">${f.label}</span>
            <div style="display:flex;align-items:center;gap:8px;">
              ${f.isBoolean ? `
                <select data-key="${f.key}" style="background:var(--surface-sunken);border:1px solid var(--border);
                        border-radius:6px;padding:6px 8px;color:var(--text-primary);font-size:12.5px;">
                  <option value="true" ${String(values[f.key]).toLowerCase() === "true" ? "selected" : ""}>Enabled</option>
                  <option value="false" ${String(values[f.key]).toLowerCase() === "false" ? "selected" : ""}>Disabled</option>
                </select>
              ` : f.isSelect ? `
                <select data-key="${f.key}" style="background:var(--surface-sunken);border:1px solid var(--border);
                        border-radius:6px;padding:6px 8px;color:var(--text-primary);font-size:12.5px;">
                  ${f.options.map((o) => `<option value="${o.value}" ${values[f.key] === o.value ? "selected" : ""}>${o.label}</option>`).join("")}
                </select>
              ` : `
                <input type="${f.isText ? "text" : "number"}" data-key="${f.key}" value="${values[f.key]}"
                       style="width:${f.isText ? 60 : 80}px;background:var(--surface-sunken);border:1px solid var(--border);
                              border-radius:6px;padding:6px 8px;color:var(--text-primary);font-size:12.5px;text-align:right;" />
              `}
              <span style="color:var(--text-muted);font-size:11px;min-width:150px;">${f.suffix}</span>
            </div>
          </div>
        `).join("")}
      </div>
    `).join("") + `
      <div style="display:flex;align-items:center;gap:10px;">
        <div class="btn btn-primary" id="saveSettingsBtn"><i class="ti ti-check"></i>Save changes</div>
        <span id="settingsSavedMsg" style="font-size:11.5px;color:var(--success-text);display:none;">Saved</span>
      </div>
    `;

    document.getElementById("saveSettingsBtn").addEventListener("click", () => this.save());
  },

  async save() {
    const payload = {};
    document.querySelectorAll("#settingsForm input[data-key], #settingsForm select[data-key]").forEach((el) => {
      payload[el.dataset.key] = el.value;
    });
    const btn = document.getElementById("saveSettingsBtn");
    btn.style.opacity = "0.6";
    try {
      const result = await API.updateSettings(payload);
      if (result.error) {
        alert(result.error);
        return;
      }
      if (payload.currency_preset && CURRENCY_PRESETS[payload.currency_preset]) {
        const preset = CURRENCY_PRESETS[payload.currency_preset];
        AppConfig.currencyPreset = payload.currency_preset;
        AppConfig.currencySymbol = preset.symbol;
        AppConfig.currencyLocale = preset.locale;
      }
      if (payload.launch_in_focus_mode !== undefined) {
        AppConfig.launchInFocusMode = payload.launch_in_focus_mode === "true";
        try { localStorage.setItem("procureos_launch_focus_mode", payload.launch_in_focus_mode); } catch {}
      }
      const msg = document.getElementById("settingsSavedMsg");
      msg.style.display = "inline";
      setTimeout(() => { msg.style.display = "none"; }, 2000);
    } catch {
      alert("Couldn't save settings \u2014 is the backend running?");
    } finally {
      btn.style.opacity = "1";
    }
  },
};
