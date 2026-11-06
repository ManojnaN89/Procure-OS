SKILL_NAME = "get_current_settings"
SKILL_DESCRIPTION = (
    "Get the current configuration values that drive exception flagging, risk scoring, "
    "and proactive signals — e.g. what percentage triggers a price flag, how many days "
    "before a requisition counts as stale. Use when the user asks what a threshold is "
    "currently set to, or how the system is configured. Read-only — to change a "
    "setting, tell the user to use the Configuration page, since no skill writes these."
)
SKILL_PARAMETERS = {"type": "object", "properties": {}, "required": []}


def run(db, **kwargs):
    from settings import get_all_settings
    return get_all_settings()
