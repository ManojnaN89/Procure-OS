"""
Routes each incoming user message to the right domain agent (or handles it
generally). Also runs the intent-classification call and the post-reply
next-action-suggestion call.
"""
import json
from groq_client import chat_completion
from agents import auction_agent, rfq_agent, requisition_agent, source_to_pay_agent, insights_agent
from skill_loader import load_all_skills
from settings import currency_note

_ALL_SKILLS = load_all_skills()

_AGENTS = {
    "auction": auction_agent.build(_ALL_SKILLS),
    "rfq": rfq_agent.build(_ALL_SKILLS),
    "requisition": requisition_agent.build(_ALL_SKILLS),
    "source_to_pay": source_to_pay_agent.build(_ALL_SKILLS),
    "insights": insights_agent.build(_ALL_SKILLS),
}

_CATEGORIES = ("auction", "rfq", "requisition", "source_to_pay", "insights", "general")

_ROUTER_PROMPT = """You are a router for a procurement platform. Classify the user's latest
message into exactly one category:
- auction: running competitive bidding, forward or reverse auctions
- rfq: requesting quotes from vendors, recording or comparing quotes, or asking about a
  flagged quote or a specific RFQ (e.g. "tell me about the flagged quote on RFQ #1",
  "why was Acme's quote flagged")
- requisition: raising an internal purchase request, approving/rejecting it, converting to a PO
- source_to_pay: onboarding a vendor, creating a purchase order directly, processing a payment,
  importing vendors from a file, storing a contract, renewing a contract, adjusting a budget,
  flagging/deactivating/verifying a vendor
- insights: analytical questions about spend, trends, category breakdowns, or comparing vendors
  by spend (e.g. "why did spending increase", "compare vendor X and Y"), or asking about a
  specific vendor's risk/rating/status/pending bills
- general: greetings, unclear requests, or anything not covered above

Respond with ONLY the category word, nothing else."""

_GENERAL_PROMPT = """You are ProcureOS, a friendly AI procurement assistant. Help the user, or
point them toward auctions, RFQs, requisitions, source-to-pay actions, or spend insights
depending on what they seem to need.

If asked what ProcureOS can do or how it works, answer accurately from what's actually true:
it's a conversational procurement platform covering auctions, RFQs, requisitions, vendor
management, and purchase-to-pay — all real actions executed through this chat, not just
answered. It also runs proactively in the background: a scheduler checks every couple of
minutes for issues (flagged pricing, risky vendors, stuck requisitions, budget overruns,
expiring contracts) and surfaces them without being asked, shown as notifications and in the
"Needs attention" panel. Every action taken through the chat is logged in a full Agent Trail
for audit. There's also a distraction-free "Focus Mode" that replaces the dashboard with a
full-screen conversation, and a Configuration page for tuning the thresholds behind all of
this. Don't invent capabilities beyond what's listed here."""

_SUGGESTION_PROMPT = """You just gave the reply below to a procurement platform user. Propose up
to 2 concise, concrete next actions the user could plausibly want next, phrased as short button
labels plus the full sentence that would be sent to the assistant if clicked.

You may ONLY suggest actions from this exact list of real platform capabilities — never invent a
capability that isn't here (no alerts, notifications, scheduled reports, or anything not listed):
- Auctions: start a forward/reverse auction, submit a bid, close an auction
- RFQs: draft an RFQ, record a vendor's quote, compare quotes on an RFQ
- Requisitions: create a requisition, approve/reject a pending one, convert an approved one to a PO
- Vendors: onboard a vendor, import vendors from a CSV
- Purchase orders: create a PO directly, process a payment on a PO
- Contracts: store a contract record for a vendor, renew an existing contract
- Vendors: flag a vendor for active review, or remove that flag; deactivate or reactivate a vendor; mark a vendor as verified with a note
- Budgets: adjust a category's budgeted amount for a month
- Insights: show the spend trend over time, show spend by category, compare specific vendors by spend, look up a specific vendor's risk/rating/status, check a vendor's pending bills
- Notifications: dismiss or resolve a flagged exception, expiring contract warning, or other proactive notification (does not undo the underlying record, only clears the notification)

Only suggest actions that make sense given the reply above. If nothing on this list clearly
applies, return an empty list. Do not suggest an action that was already just completed.

Respond with ONLY valid JSON in this exact shape, nothing else:
{"suggestions": [{"label": "short button text", "prompt": "full sentence to send"}]}

Assistant's reply:
"""


def classify_intent(user_message: str) -> str:
    messages = [
        {"role": "system", "content": _ROUTER_PROMPT},
        {"role": "user", "content": user_message},
    ]
    response = chat_completion(messages, stream=False, temperature=0)
    category = (response.choices[0].message.content or "").strip().lower()
    return category if category in _CATEGORIES else "general"


def propose_suggestions(reply_text: str):
    """
    One lightweight follow-up LLM call: proposes 0-2 ranked next actions as
    structured JSON (the Option A/B pattern). Never raises — a parse failure
    or empty reply just means no suggestions are shown, which is always safe.
    """
    if not reply_text or not reply_text.strip():
        return []
    try:
        messages = [
            {"role": "system", "content": _SUGGESTION_PROMPT + reply_text},
            {"role": "user", "content": "Propose the JSON now."},
        ]
        response = chat_completion(messages, stream=False, temperature=0.3)
        raw = (response.choices[0].message.content or "").strip()
        # models sometimes wrap JSON in ```json fences despite instructions — strip if present
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw.lower().startswith("json"):
                raw = raw[4:]
        parsed = json.loads(raw)
        suggestions = parsed.get("suggestions", [])
        cleaned = []
        for s in suggestions[:2]:
            if isinstance(s, dict) and s.get("label") and s.get("prompt"):
                cleaned.append({"label": str(s["label"])[:60], "prompt": str(s["prompt"])[:300]})
        return cleaned
    except Exception:
        return []


def route(db, session_history, user_message, agent_hint=None):
    """
    Returns (category, reply_text, events, charts, extras)
    events: list of {"agent", "skill", "args", "result"} for the Agent Trail
    charts: list of chart-ready dicts, pulled from either a skill's singular
        "chart" key or its plural "charts" list — a single skill can now
        contribute more than one chart (e.g. a broad summary returning both
        a requisition-status donut and a spend-under-management donut)
        without needing a second tool call to get a second visual.
    extras: list of other interactive elements (progress bars, expandable
        detail lists) pulled from any skill result's "extras" list
    """
    events = []
    charts = []
    extras = []

    def trail_cb(agent, skill, args, result):
        events.append({"agent": agent, "skill": skill, "args": args, "result": result})
        if isinstance(result, dict):
            if isinstance(result.get("chart"), dict):
                charts.append(result["chart"])
            if isinstance(result.get("charts"), list):
                charts.extend(c for c in result["charts"] if isinstance(c, dict))
            if isinstance(result.get("extras"), list):
                extras.extend(e for e in result["extras"] if isinstance(e, dict))

    if agent_hint in _AGENTS:
        # the caller already knows the owning agent (a Review click on a
        # specific notification) — no need to guess from the wording
        category = agent_hint
        trail_cb("Orchestrator", "classify_intent", {"message": user_message},
                 {"routed_to": category, "via": "review_action"})
    else:
        category = classify_intent(user_message)
        trail_cb("Orchestrator", "classify_intent", {"message": user_message}, {"routed_to": category})

    if category == "general":
        messages = [{"role": "system", "content": _GENERAL_PROMPT + currency_note()}] + session_history
        response = chat_completion(messages, stream=False)
        return category, response.choices[0].message.content, events, charts, extras

    agent = _AGENTS[category]
    reply = agent.handle(db, session_history, trail_cb=trail_cb)
    return category, reply, events, charts, extras
