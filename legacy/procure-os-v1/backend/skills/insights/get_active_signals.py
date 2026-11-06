SKILL_NAME = "get_active_signals"
SKILL_DESCRIPTION = (
    "Get everything currently needing attention right now: unresolved proactive "
    "signals (flagged exceptions, vendor risk, stuck requisitions, budget overruns, "
    "expiring contracts), with counts by severity. Use when the user asks 'anything I "
    "should know', 'what needs attention', or similar — not just at the start of a "
    "conversation, any time. Read-only."
)
SKILL_PARAMETERS = {"type": "object", "properties": {}, "required": []}


def run(db, **kwargs):
    from models import Notification

    unresolved = db.query(Notification).filter(Notification.resolved == False).all()  # noqa: E712
    counts = {"high": 0, "medium": 0, "low": 0}
    for n in unresolved:
        counts[n.severity] = counts.get(n.severity, 0) + 1

    from signals import live_message
    return {
        "total_unresolved": len(unresolved),
        "counts_by_severity": counts,
        "signals": [{
            "type": n.type, "severity": n.severity, "message": live_message(db, n),
            "related_type": n.related_type, "related_id": n.related_id,
        } for n in unresolved],
    }
