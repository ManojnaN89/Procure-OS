"""
Central place for reading/writing configurable thresholds. Everything here
has a hardcoded default matching what ProcureOS has always used, so a
fresh install with nobody ever touching the Configuration page behaves
exactly as before — settings only change behavior once someone actually
sets them.

Deliberately exposes thresholds and cutoffs, not the underlying scoring
formulas (e.g. the risk score's point-weighting stays fixed in
intelligence.py) — same principle Suplari's own materials describe:
configure definitions, not the math itself.
"""
from db import SessionLocal
from models import Setting

DEFAULTS = {
    "exception_flag_threshold_pct": "30",       # intelligence.py — % above category average
    "stale_requisition_days": "3",              # signals.py
    "contract_expiring_days": "30",             # signals.py
    "signal_check_interval_minutes": "2",       # scheduler.py
    "risk_score_medium_cutoff": "25",           # intelligence.py — score >= this is "medium"
    "risk_score_high_cutoff": "55",             # intelligence.py — score >= this is "high"
    "currency_preset": "INR",                   # frontend formatCurrency() — symbol + number grouping bundled together
    "launch_in_focus_mode": "true",             # app.js — default landing experience
}

# A free-text currency symbol let someone set "$" while the number grouping
# underneath stayed Indian-style — a real, silent correctness bug. Presets
# bundle a symbol with its matching locale/grouping so an invalid
# combination becomes impossible. Narrower than free text, deliberately —
# correct output matters more than letting someone type an arbitrary symbol.
CURRENCY_PRESETS = ["INR", "USD", "EUR", "GBP"]


def get_setting(key: str) -> str:
    """Returns the stored value, or the hardcoded default if never set."""
    db = SessionLocal()
    try:
        row = db.get(Setting, key)
        if row:
            return row.value
        return DEFAULTS.get(key, "")
    finally:
        db.close()


def get_setting_float(key: str) -> float:
    return float(get_setting(key))


def get_setting_int(key: str) -> int:
    return int(get_setting(key))


def get_all_settings() -> dict:
    """Merges stored overrides with defaults, so the Configuration page
    always sees every known key even before anything's been changed."""
    db = SessionLocal()
    try:
        stored = {row.key: row.value for row in db.query(Setting).all()}
    finally:
        db.close()
    return {**DEFAULTS, **stored}


def set_setting(key: str, value: str):
    if key not in DEFAULTS:
        raise ValueError(f"Unknown setting: {key}")
    if key == "currency_preset" and value not in CURRENCY_PRESETS:
        raise ValueError(f"Invalid currency preset: {value}. Must be one of {CURRENCY_PRESETS}")
    db = SessionLocal()
    try:
        row = db.get(Setting, key)
        if row:
            row.value = value
        else:
            row = Setting(key=key, value=value)
            db.add(row)
        db.commit()
    finally:
        db.close()


def currency_note() -> str:
    """Tells the model which currency the platform is set to. Tools return bare
    numbers, so without this the model guessed dollars ("$1.74 M") while the
    rest of the UI showed rupees."""
    code = get_setting("currency_preset") or "INR"
    if code == "INR":
        return ("\n\nAll money amounts from tools are in Indian Rupees (INR). Always write "
                "them with the \u20b9 symbol and Indian digit grouping (e.g. \u20b917,40,000 or "
                "\u20b917.4 L) \u2014 never use $.")
    symbols = {"USD": "$", "EUR": "\u20ac", "GBP": "\u00a3"}
    return (f"\n\nAll money amounts from tools are in {code}. Always write them with the "
            f"{symbols.get(code, code)} symbol.")
