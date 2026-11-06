"""
Global notification broadcast manager. Unlike the Agent Trail (one channel
per session, since a conversation is personal), notifications are
company-wide findings — everyone connected sees the same signals, so this
broadcasts to every connected client rather than scoping by session_id.
"""
import json
from typing import List
from fastapi import WebSocket


class NotificationManager:
    def __init__(self):
        self.connections: List[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.connections.append(ws)

    def disconnect(self, ws: WebSocket):
        if ws in self.connections:
            self.connections.remove(ws)

    async def broadcast(self, notification: dict):
        for ws in list(self.connections):
            try:
                await ws.send_text(json.dumps(notification))
            except Exception:
                pass


notification_manager = NotificationManager()
