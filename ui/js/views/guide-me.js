/**
 * Help: the short guides in docs/guides/, one per role. The person's own guide opens first.
 * The text comes from the server as markdown and is drawn here as text: everything is escaped before the few
 * markers we understand (headings, lists, tables, **bold**, links to another guide) are turned into elements.
 */
const GuideMeView = {
  esc(value) {
    return String(value ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  },

  // inline: **bold** and [text](slug.md); the input is escaped first, so nothing else can become markup
  inline(raw) {
    let out = this.esc(raw);
    out = out.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    out = out.replace(/\[([^\]]+)\]\(([a-z0-9-]+)\.md\)/g, '<a href="#/help/$2" style="color:var(--accent-tint);">$1</a>');
    return out;
  },

  markdown(md) {
    const lines = md.replace(/\r\n/g, "\n").split("\n");
    const html = [];
    let i = 0;
    const cell = (c) => `<td style="padding:6px 10px;border-top:1px solid var(--border);vertical-align:top;">${this.inline(c.trim())}</td>`;
    while (i < lines.length) {
      const line = lines[i];
      if (/^# /.test(line)) { html.push(`<h2 style="margin:0 0 10px;font-size:18px;">${this.inline(line.slice(2))}</h2>`); i++; continue; }
      if (/^## /.test(line)) { html.push(`<h3 style="margin:18px 0 6px;font-size:14px;">${this.inline(line.slice(3))}</h3>`); i++; continue; }
      if (/^\|/.test(line)) {
        const rows = [];
        while (i < lines.length && /^\|/.test(lines[i])) { rows.push(lines[i]); i++; }
        const split = (r) => r.replace(/^\||\|$/g, "").split("|");
        const head = split(rows[0]);
        const body = rows.slice(2).map(split);
        html.push(`<div class="table-wrap"><table class="data-table"><thead><tr>${head.map((h) => `<th>${this.inline(h.trim())}</th>`).join("")}</tr></thead><tbody>${body.map((r) => `<tr>${r.map(cell).join("")}</tr>`).join("")}</tbody></table></div>`);
        continue;
      }
      if (/^(- |\d+\. )/.test(line)) {
        const ordered = /^\d+\. /.test(line);
        const items = [];
        while (i < lines.length && /^(- |\d+\. )/.test(lines[i])) { items.push(lines[i].replace(/^(- |\d+\. )/, "")); i++; }
        const tag = ordered ? "ol" : "ul";
        html.push(`<${tag} style="margin:6px 0;padding-left:22px;font-size:13px;line-height:1.7;">${items.map((t) => `<li>${this.inline(t)}</li>`).join("")}</${tag}>`);
        continue;
      }
      if (line.trim() === "") { i++; continue; }
      const para = [];
      while (i < lines.length && lines[i].trim() !== "" && !/^(#|\||- |\d+\. )/.test(lines[i])) { para.push(lines[i]); i++; }
      html.push(`<p style="margin:6px 0;font-size:13px;line-height:1.7;color:var(--text-secondary);">${this.inline(para.join(" "))}</p>`);
    }
    return html.join("");
  },

  async render(container, params) {
    container.innerHTML = `
      <div class="page-header">
        <h1>Help</h1>
        <p>A short guide for each role. Yours opens first.</p>
      </div>
      <div id="guideTabs" style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px;"></div>
      <div class="panel" id="guideBody"><div class="empty-state">Loading…</div></div>`;
    let list;
    try {
      list = await API.getJSON("/api/guides");
    } catch {
      document.getElementById("guideBody").innerHTML = `<div class="empty-state">Couldn't load the guides.</div>`;
      return;
    }
    const wanted = params && params.id && list.guides.some((g) => g.slug === params.id) ? params.id : list.start;
    const tabs = document.getElementById("guideTabs");
    if (!tabs) return; // the person has gone to another page
    tabs.innerHTML = list.guides.map((g) => `<a class="btn btn-sm ${g.slug === wanted ? "btn-primary" : ""}" href="#/help/${this.esc(g.slug)}" data-guide="${this.esc(g.slug)}">${this.esc(g.title)}${g.mine ? " (yours)" : ""}</a>`).join("");
    await this.show(wanted);
  },

  async show(slug) {
    const body = document.getElementById("guideBody");
    if (!body || !slug) { if (body) body.innerHTML = `<div class="empty-state">There are no guides yet.</div>`; return; }
    try {
      const g = await API.getJSON(`/api/guides/${encodeURIComponent(slug)}`);
      if (document.getElementById("guideBody")) document.getElementById("guideBody").innerHTML = this.markdown(g.markdown);
    } catch {
      body.innerHTML = `<div class="empty-state">Couldn't load this guide.</div>`;
    }
  },
};
