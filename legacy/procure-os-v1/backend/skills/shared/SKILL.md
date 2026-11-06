# Skill Domain: Shared

Cross-cutting skills usable by any agent, not tied to one domain. Placed
in their own folder rather than duplicated across domain folders, and
registered directly in every agent's SKILLS list so whichever agent the
orchestrator routes a conversation to always has it available.

## dismiss_notification
Use when the user wants to dismiss, resolve, or acknowledge a proactive
notification/signal (a flagged exception, an expiring contract, etc.) —
marking it handled so it stops showing up in "Needs attention." Does not
change any underlying record, only the notification itself.
Requires: notification_id
