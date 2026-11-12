"""The look of the small pages people open from an email without signing in (the approval link page, and pages like it for
suppliers): the app's colours and type, a brand bar, one card, and buttons that are easy to press on a phone.

`page(title, body)` returns the whole page; `body` is markup the caller has already escaped. Classes for the body:
`ok` / `no` / `back` / `plain` on buttons, `muted` and `note` on paragraphs, `facts` on a table of label and value.
"""

from html import escape

from fastapi.responses import HTMLResponse

STYLE = (
    ":root{--brand:#2453C7;--text:#141C2B;--muted:#5F6B7A;--border:#E1E5EB;--canvas:#F4F6F9}"
    "*{box-sizing:border-box}"
    "body{margin:0;background:var(--canvas);color:var(--text);"
    "font:15px/1.55 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;"
    "-webkit-font-smoothing:antialiased}"
    "header{display:flex;align-items:center;gap:10px;max-width:600px;margin:0 auto;padding:20px 16px 12px;font-weight:600}"
    "header b{display:inline-flex;width:28px;height:28px;align-items:center;justify-content:center;border-radius:6px;"
    "background:var(--brand);color:#fff;font-size:14px}"
    "main{max-width:600px;margin:0 auto 32px;background:#fff;border:1px solid var(--border);border-radius:12px;"
    "padding:24px;box-shadow:0 1px 2px rgba(16,24,40,.06)}"
    "h1{font-size:21px;line-height:1.3;margin:0 0 12px}p{margin:0 0 12px}"
    "table{width:100%;border-collapse:collapse;margin:8px 0 16px;font-size:14px;font-variant-numeric:tabular-nums}"
    "th{background:#FAFBFC;color:var(--muted);font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:.04em}"
    "td,th{padding:9px 10px;border-bottom:1px solid var(--border);text-align:left}"
    "td:last-child,th:last-child{text-align:right}"
    "table.facts td{border:0;padding:3px 12px 3px 0;text-align:left;vertical-align:top}"
    "table.facts td:first-child{color:var(--muted);width:40%}"
    "label{display:block;font-weight:500;margin:12px 0 6px}"
    "textarea,input{width:100%;background:#fff;color:inherit;border:1px solid #CBD2DB;border-radius:6px;padding:10px 12px;"
    "font:inherit;margin-bottom:12px}"
    "textarea:focus,input:focus{outline:none;border-color:var(--brand);box-shadow:0 0 0 3px rgba(47,107,255,.35)}"
    "button{min-height:44px;padding:10px 18px;border-radius:6px;border:1px solid transparent;font:inherit;font-weight:600;"
    "cursor:pointer;margin:0 8px 8px 0}"
    "button:focus-visible{outline:2px solid #2F6BFF;outline-offset:2px}"
    ".ok{background:#13683A;color:#fff}.no{background:#B42318;color:#fff}.back{background:#fff;color:var(--text);"
    "border-color:#CBD2DB}.plain{background:#EAF0FC;color:#1C45AB}"
    ".muted{color:var(--muted);font-size:13px}"
    ".note{background:#FFF4DE;border:1px solid #F3D79C;border-left:4px solid #C27803;border-radius:8px;padding:10px 12px;"
    "color:#5C3300}"
    ".done{background:#E5F4EB;border:1px solid #BFE3CD;border-left:4px solid #1E8A4C;border-radius:8px;padding:12px 14px}"
    "@media (max-width:480px){main{border-radius:0;border-left:0;border-right:0;padding:20px 16px}"
    "button{width:100%;margin-right:0}}"
)
HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Robots-Tag": "noindex"}


def page(title: str, body: str, status: int = 200) -> HTMLResponse:
    html = (
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>{escape(title)}</title><style>{STYLE}</style></head><body>"
        "<header><b aria-hidden=true>P</b>ProcureOS</header>"
        f"<main><h1>{escape(title)}</h1>{body}</main></body></html>"
    )
    return HTMLResponse(html, status_code=status, headers=HEADERS)
