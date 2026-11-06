SKILL_NAME = "get_recent_activity"
SKILL_DESCRIPTION = (
    "Get a log of recent real actions taken by any agent — what was done, by which "
    "agent, and roughly what happened. Use when the user asks what the assistant has "
    "done recently, what changed, or wants an audit-style answer. This is the same "
    "data the Agent Trail shows, just askable in conversation. Read-only."
)
SKILL_PARAMETERS = {
    "type": "object",
    "properties": {
        "limit": {"type": "integer", "description": "How many recent entries to return, default 10"},
    },
    "required": [],
}


def run(db, **kwargs):
    from models import AgentTrailLog

    limit = kwargs.get("limit") or 10
    rows = db.query(AgentTrailLog).order_by(AgentTrailLog.created_at.desc()).limit(limit).all()

    return {"count": len(rows), "activity": [{
        "agent": r.agent, "skill": r.skill,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    } for r in rows]}
