"""The look of every email the platform sends: one calm, branded HTML layout around the plain-text message.

Each email keeps its plain-text body (what is stored, searched and read by mail programs that show text only); this adds
the HTML version beside it. The colours match the app (ui/css/tokens.css); everything is inline, as mail programs need.
`from_text` turns the plain text into HTML without changing a word: paragraphs, "Label: value" lines, lists, quotes and
links. Nothing in the text is ever treated as markup.
"""

import re
from html import escape

BRAND = "#2453C7"
TEXT = "#141C2B"
MUTED = "#5F6B7A"
BORDER = "#E1E5EB"
CANVAS = "#F4F6F9"
FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
BUTTON_COLOURS = {"primary": BRAND, "danger": "#B42318", "neutral": "#3B4554", "success": "#13683A"}

_URL = re.compile(r"https?://[^\s<>\"']+")
_LABEL = re.compile(r"^([A-Z][\w ,'()/-]{0,30}): (.+)$")


def button(href: str, label: str, kind: str = "primary") -> str:
    """A button that works in every mail program: a link styled as a solid block."""
    return (
        f'<a href="{escape(href)}" style="display:inline-block;padding:11px 20px;margin:0 8px 8px 0;border-radius:6px;'
        f"background:{BUTTON_COLOURS.get(kind, BRAND)};color:#ffffff;text-decoration:none;font-weight:600;"
        f'font-size:14px;font-family:{FONT}">{escape(label)}</a>'
    )


def _inline(text: str) -> str:
    """Escape a line and make its web addresses links."""
    out, last = [], 0
    for m in _URL.finditer(text):
        out.append(escape(text[last : m.start()]))
        url = m.group(0).rstrip(".,)")
        out.append(f'<a href="{escape(url)}" style="color:{BRAND}">{escape(url)}</a>')
        last = m.start() + len(url)
    out.append(escape(text[last:]))
    return "".join(out)


def _block(lines: list[str]) -> str:
    if all(ln.lstrip().startswith(("- ", "* ", "• ")) for ln in lines):
        items = "".join(f'<li style="margin:0 0 4px">{_inline(ln.lstrip()[2:])}</li>' for ln in lines)
        return f'<ul style="margin:0 0 16px;padding-left:20px">{items}</ul>'
    if all(ln.startswith(">") for ln in lines):
        quoted = "<br>".join(_inline(ln[1:].strip()) for ln in lines)
        return (
            f'<blockquote style="margin:0 0 16px;padding:8px 14px;border-left:3px solid {BORDER};color:{MUTED}">'
            f"{quoted}</blockquote>"
        )
    rendered = []
    for ln in lines:
        m = _LABEL.match(ln.strip())
        rendered.append(
            f'<span style="color:{MUTED}">{escape(m.group(1))}:</span> <strong>{_inline(m.group(2))}</strong>'
            if m
            else _inline(ln)
        )
    return f'<p style="margin:0 0 16px">{"<br>".join(rendered)}</p>'


def from_text(body: str) -> str:
    """The plain-text body as HTML paragraphs, lists and quotes, every word kept."""
    blocks: list[list[str]] = [[]]
    for line in body.replace("\r\n", "\n").split("\n"):
        if line.strip():
            blocks[-1].append(line.rstrip())
        elif blocks[-1]:
            blocks.append([])
    return "".join(_block(b) for b in blocks if b)


def wrap(title: str, inner_html: str, *, footer: str = "") -> str:
    """A complete HTML email: the brand bar, the title, the message, and a quiet footer. `inner_html` is already safe."""
    note = (
        footer or "Sent by the purchasing system. You can reply to this email; your reply reaches the purchasing team."
    )
    return (
        f'<!doctype html><html><body style="margin:0;padding:0;background:{CANVAS}">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{CANVAS}">'
        '<tr><td align="center" style="padding:24px 12px">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:620px;background:#ffffff;'
        f'border:1px solid {BORDER};border-radius:8px;font-family:{FONT};font-size:14px;line-height:1.55;color:{TEXT}">'
        f'<tr><td style="padding:16px 28px;border-bottom:1px solid {BORDER}">'
        f'<span style="display:inline-block;width:24px;height:24px;line-height:24px;text-align:center;border-radius:5px;'
        f'background:{BRAND};color:#ffffff;font-weight:700;font-size:13px;vertical-align:middle">P</span>'
        f'<span style="font-weight:600;font-size:14px;margin-left:8px;vertical-align:middle">ProcureOS</span></td></tr>'
        f'<tr><td style="padding:24px 28px 8px"><h1 style="margin:0 0 16px;font-size:18px;line-height:1.35;color:{TEXT}">'
        f"{escape(title)}</h1>{inner_html}</td></tr>"
        f'<tr><td style="padding:14px 28px;border-top:1px solid {BORDER};color:{MUTED};font-size:12px">{escape(note)}</td></tr>'
        "</table></td></tr></table></body></html>"
    )


def text_email(subject: str, body: str, *, footer: str = "") -> str:
    """The HTML version of a plain-text email."""
    return wrap(subject, from_text(body), footer=footer)
