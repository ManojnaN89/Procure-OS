# UI guide

One look for the main app (`ui/`), the admin app (`frontend/`), the emails and the no-login pages: light, calm, one brand
blue, system fonts. Build a new screen from the pieces below; do not add colours, fonts or one-off styles.

## Stylesheets (`ui/css/`, loaded in this order)

| File | Holds |
|---|---|
| `tokens.css` | every colour, size, space, radius and shadow, as CSS custom properties |
| `base.css` | reset, type, links, focus ring, helpers (`.num`, `.muted`, `.mono`, `.sr-only`) |
| `layout.css` | the shell (side menu, top bar), `.page`, `.page-header`, `.doc-layout` |
| `components.css` | buttons, badges, cards, tables, forms, tabs, flow strip, callouts, My work, timeline, toasts, dialogs |
| `assistant.css` | the assistant, floating panel, agent trail |
| `focus-mode.css`, `auth.css`, `motion.css` | focus mode, sign-in, animations |
| `responsive.css` | tablet and phone (menu drawer below 900 px, list cards and the receive screen below 600 px) |

## Tokens

- Colour roles: `--color-bg`, `--color-surface`, `--color-border`, `--color-text`, `--color-text-secondary`,
  `--color-text-muted`, `--color-primary` (#2453C7), `--color-success | warning | danger` with `-soft` and `-text`.
- Document status, one tone each: `--status-{draft, waiting, approved, sent, received, held, paid, rejected}-{bg, fg, dot}`.
- Type: `--font-sans` (system), `--text-xs` 11.5 to `--text-2xl` 24 px. Space: `--space-1` (4 px) to `--space-10`.
  Radius `--radius-sm|md|lg|xl`, shadow `--shadow-sm|md|lg`, focus `--ring`.
- The older names (`--surface`, `--accent`, `--accent-tint`, `--text-muted`, `--warning-text`, …) still work and map to
  these, so screens written with them get the new look.

All text meets WCAG AA contrast; a status always shows its word, never colour alone.

## Components

| Need | Use |
|---|---|
| Page title, one-line description, actions on the right | `<div class="page-header page-header-row"><div><h1>…</h1><p>…</p></div><div class="page-actions">…</div></div>` |
| Buttons | `.btn` (secondary), `.btn-primary` (one per screen), `.btn-danger`, `.btn-ghost`, `.btn-sm`, `.btn-lg`; group in `.btn-row` |
| Status | `statusBadge(status)` (badges.js) picks the tone; by hand: `<span class="badge badge-held">Blocked</span>` |
| A list | `.table-wrap` > `table.data-table` with `id="tableWrap"` on list pages: sticky header, search box, Export, phone cards |
| Figures in a table | right-aligned by themselves (`UI.enhanceTables`); force with `class="num"` |
| Filters above a list | `.tabs` > `button.tab` (`.active`, `<span class="count">`) |
| Card | `.panel` with `.panel-title` |
| Form | `.form-grid` of `<label>Text<input></label>`; `.help` under a field; `.form-actions` for the buttons |
| A document page | `UI.backLink(href, label)`, `<h1>`, a facts line, `FlowStrip.forX(doc)` (its "Now:" band is the next step), then `.doc-layout` > `.doc-main` (actions first, lines, `UI.totals`) and `.doc-side` (`UI.facts`, `UI.approvalSteps`, history) |
| A hold, an error, a tip | `UI.callout("warning" \| "danger" \| "info" \| "success", title, html)`: say what happened and what to do |
| Empty list | `.empty-next` with `<strong>No … yet.</strong><br>Next: …` |
| Short confirmation | `UI.toast(text, "success")` |
| Home screen work | `.start-here`, `.todo-group` > `.todo-head` + `a.todo-row` (`.sub`, `.go`) |

## A new screen, step by step

1. Add the view in `ui/js/views/`, the route in `router.js`, the menu item in `index.html` (with `data-needs`).
2. Start with the page header; put the one primary action there or in the first card.
3. Lists: `id="tableWrap"` and the route in `ExportList.NAMES` give search, Export and phone cards.
4. Documents: the layout above. What the person must do goes at the top of `.doc-main`.
5. Write errors in words, with the next step. Keep ids, labels and button texts stable: tests and evals use them.
6. Check at 1440 and 390 px wide: no sideways scroll, no raw codes.

## Admin app, emails and no-login pages

- `frontend/src/index.css` holds the same tokens for Tailwind (`bg-primary`, `text-muted-foreground`, …); use the
  components in `frontend/src/components/ui/`.
- Emails: `platform/email_layout.py`. Plain-text emails get the branded HTML version automatically
  (`emails.send_linked_email`, `notifications.notify`); build richer ones with `wrap()` and `button()`.
- Pages opened from an email without signing in: `platform/link_pages.page(title, body)`, with the classes listed in
  its docstring.
