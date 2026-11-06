"""
ProcureOS backend — FastAPI app.

- POST /api/chat              : send a user message, get a streamed (SSE) reply
- WS   /ws/trail/{sid}         : Agent Trail event stream for a session (drives the
                                 drawer, the full trail page, and the floating
                                 assistant's live ticker — all subscribe to the same feed)
- WS   /ws/notifications        : global proactive-signal stream (Phase H2/H3) — every
                                 connected client sees the same signals, unlike Trail
- GET  /api/*                    : read-only list/detail/search/dashboard endpoints (reads.py)
- /                               : serves the frontend (static files)
"""
import json
import uuid
import asyncio

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from db import init_db, SessionLocal
from models import AgentTrailLog
from orchestrator import route, propose_suggestions
from trail import trail_manager
from notifications import notification_manager
from reads import router as reads_router
from upload import router as upload_router
from voice import router as voice_router
import scheduler

app = FastAPI(title="ProcureOS")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# simple in-memory session -> chat history store (fine for a local dev build)
SESSIONS: dict[str, list] = {}


app.include_router(reads_router)
app.include_router(upload_router)
app.include_router(voice_router)


@app.on_event("startup")
def startup():
    init_db()
    loop = asyncio.get_event_loop()
    scheduler.init(loop)


@app.on_event("shutdown")
def shutdown():
    scheduler.shutdown()


def _chunk_text(text: str, words_per_chunk: int = 3):
    words = text.split(" ")
    for i in range(0, len(words), words_per_chunk):
        yield " ".join(words[i:i + words_per_chunk]) + " "


@app.post("/api/chat")
async def chat(request: Request):
    payload = await request.json()
    session_id = payload.get("session_id") or str(uuid.uuid4())
    user_message = payload["message"]
    # optional: the frontend already knows which agent owns a Review click
    # (e.g. a flagged quote -> rfq), so it can skip the LLM router guess
    agent_hint = payload.get("agent")

    history = SESSIONS.setdefault(session_id, [])
    history.append({"role": "user", "content": user_message})

    db = SessionLocal()
    try:
        category, reply, events, charts, extras = route(db, history, user_message, agent_hint=agent_hint)
    finally:
        db.close()

    history.append({"role": "assistant", "content": reply})

    # one lightweight follow-up call proposing 0-2 ranked next actions (Option A/B pattern) —
    # never blocks the reply itself; a failure here just means no suggestions are shown
    suggestions = propose_suggestions(reply)

    # persist trail events + broadcast to any connected Agent Trail
    if events:
        db2 = SessionLocal()
        try:
            for e in events:
                db2.add(AgentTrailLog(
                    session_id=session_id, agent=e["agent"], skill=e["skill"],
                    args=e["args"], result=e["result"],
                ))
            db2.commit()
        finally:
            db2.close()

        for e in events:
            await trail_manager.emit(session_id, e["agent"], e["skill"], e["args"], e["result"])

    async def stream():
        yield f"data: {json.dumps({'type': 'session', 'session_id': session_id, 'category': category})}\n\n"
        for chart in charts:
            yield f"data: {json.dumps({'type': 'chart', 'chart': chart})}\n\n"
        if extras:
            yield f"data: {json.dumps({'type': 'extras', 'extras': extras})}\n\n"
        for chunk in _chunk_text(reply):
            yield f"data: {json.dumps({'type': 'token', 'content': chunk})}\n\n"
            await asyncio.sleep(0.02)
        if suggestions:
            yield f"data: {json.dumps({'type': 'suggestions', 'suggestions': suggestions})}\n\n"
        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.websocket("/ws/trail/{session_id}")
async def trail_ws(websocket: WebSocket, session_id: str):
    await trail_manager.connect(session_id, websocket)
    try:
        while True:
            await websocket.receive_text()  # keep-alive; client doesn't need to send anything meaningful
    except WebSocketDisconnect:
        trail_manager.disconnect(session_id, websocket)


@app.websocket("/ws/notifications")
async def notifications_ws(websocket: WebSocket):
    await notification_manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()  # keep-alive
    except WebSocketDisconnect:
        notification_manager.disconnect(websocket)


# (the old inline /api/dashboard handler now lives in reads.py, extended with
#  requisition status breakdown and spend-by-category for the real dashboard)

# Serve the frontend last so API/WS routes above take priority
app.mount("/", StaticFiles(directory="../frontend", html=True), name="frontend")
