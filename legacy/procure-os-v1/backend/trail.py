"""
Live Agent Trail: broadcasts real-time events (agent -> skill -> result)
to any connected frontend via WebSocket, per chat session.
"""
import json
from datetime import datetime
from typing import Dict, List
from fastapi import WebSocket


class TrailManager:
    def __init__(self):
        self.connections: Dict[str, List[WebSocket]] = {}

    async def connect(self, session_id: str, ws: WebSocket):
        await ws.accept()
        self.connections.setdefault(session_id, []).append(ws)

    def disconnect(self, session_id: str, ws: WebSocket):
        conns = self.connections.get(session_id, [])
        if ws in conns:
            conns.remove(ws)

    async def emit(self, session_id: str, agent: str, skill: str, args: dict, result: dict):
        payload = {
            "agent": agent,
            "skill": skill,
            "args": args,
            "result": result,
            "timestamp": datetime.utcnow().isoformat(),
        }
        for ws in list(self.connections.get(session_id, [])):
            try:
                await ws.send_text(json.dumps(payload))
            except Exception:
                pass
        return payload


trail_manager = TrailManager()
