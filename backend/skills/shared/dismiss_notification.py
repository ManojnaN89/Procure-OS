SKILL_NAME = "dismiss_notification"
SKILL_DESCRIPTION = (
    "Dismiss, resolve, or acknowledge a proactive notification (a flagged exception, "
    "an expiring contract warning, a budget overrun, etc.), marking it handled so it "
    "stops appearing in Needs Attention. Use when the user says to dismiss, resolve, "
    "clear, or acknowledge a notification. This only changes the notification itself — "
    "it does not undo, cancel, or modify whatever record it was about. "
    "You can identify the notification either by its own numeric ID if you already know "
    "it, OR by the record it's about (related_type + related_id) — for example, to "
    "dismiss the flagged-quote notification for RFQ 1, use related_type='rfq', "
    "related_id=1. Valid related_type values: rfq, purchase_order, vendor, requisition, "
    "budget, contract."
)
SKILL_PARAMETERS = {
    "type": "object",
    "properties": {
        "notification_id": {"type": "integer", "description": "The notification's own ID, if already known"},
        "related_type": {"type": "string", "description": "The type of record the notification is about, e.g. 'rfq', 'purchase_order', 'vendor', 'requisition', 'budget', 'contract' — use this with related_id when the raw notification ID isn't known"},
        "related_id": {"type": "integer", "description": "The ID of the record the notification is about, used together with related_type"},
    },
    "required": [],
}


def run(db, **kwargs):
    from datetime import datetime
    from models import Notification

    n = None
    if kwargs.get("notification_id"):
        n = db.get(Notification, kwargs["notification_id"])
        if not n:
            return {"error": f"Notification {kwargs['notification_id']} not found"}
    elif kwargs.get("related_type") and kwargs.get("related_id") is not None:
        n = db.query(Notification).filter(
            Notification.related_type == kwargs["related_type"],
            Notification.related_id == kwargs["related_id"],
            Notification.resolved == False,  # noqa: E712
        ).order_by(Notification.created_at.desc()).first()
        if not n:
            return {"error": f"No unresolved notification found for {kwargs['related_type']} {kwargs['related_id']}"}
    else:
        return {"error": "Provide either notification_id, or both related_type and related_id"}

    if n.resolved:
        return {"notification_id": n.id, "already_resolved": True}

    n.resolved = True
    n.resolved_at = datetime.utcnow()
    db.commit()
    return {"notification_id": n.id, "resolved": True, "type": n.type, "message": n.message}
