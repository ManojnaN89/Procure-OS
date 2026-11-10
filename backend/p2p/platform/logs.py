"""Structured logs: one JSON object per line, with the request id, tenant and user where known."""

import json
import logging
import time
import uuid
from contextvars import ContextVar
from typing import Any

from fastapi import FastAPI, Request, Response

request_id: ContextVar[str] = ContextVar("request_id", default="")
tenant_ctx: ContextVar[str] = ContextVar("tenant", default="")
user_ctx: ContextVar[str] = ContextVar("user", default="")

log = logging.getLogger("p2p")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, var in (("request_id", request_id), ("tenant", tenant_ctx), ("user", user_ctx)):
            if value := var.get():
                data[key] = value
        data.update(getattr(record, "extra_fields", {}))
        if record.exc_info:
            data["exc"] = self.formatException(record.exc_info)
        return json.dumps(data, default=str)


def configure(level: int = logging.INFO) -> None:
    root = logging.getLogger("p2p")
    if any(getattr(h, "_p2p", False) for h in root.handlers):
        return
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    handler._p2p = True  # type: ignore[attr-defined]
    root.addHandler(handler)
    root.setLevel(level)
    root.propagate = False


def install(app: FastAPI) -> None:
    """Give every request an id (also returned in X-Request-ID) and log one line when it finishes."""
    configure()

    @app.middleware("http")
    async def _log_requests(request: Request, call_next: Any) -> Response:
        rid = (
            "".join(c for c in request.headers.get("x-request-id", "")[:64] if c.isalnum() or c in "-_.")
            or uuid.uuid4().hex[:12]
        )
        request_id.set(rid)
        started = time.monotonic()
        response: Response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        log.info(
            "request",
            extra={
                "extra_fields": {
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "ms": int((time.monotonic() - started) * 1000),
                }
            },
        )
        return response
