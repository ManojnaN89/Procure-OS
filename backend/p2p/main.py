import importlib
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from p2p.config import REPO_ROOT, get_settings
from p2p.platform import logs, tenants
from p2p.platform.admin_api import router as admin_router
from p2p.platform.auth import router as auth_router
from p2p.platform.files import router as files_router

app = FastAPI(title="P2P platform", version="0.0.1")
logs.install(app)
for _module in filter(None, (m.strip() for m in get_settings().extra_modules.split(","))):
    importlib.import_module(_module)
app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(files_router)


@app.get("/api/health")
def health() -> dict[str, str]:
    """Liveness plus a check that the control database answers."""
    db = "ok"
    try:
        with tenants.control_engine().connect() as conn:
            conn.execute(text("select 1"))
    except SQLAlchemyError:
        db = "unreachable"
    return {"status": "ok", "database": db}


def mount_frontend(dist: Path | None = None) -> None:
    """Serve the built web app (frontend/dist) from the same address as the API, with a fallback for page reloads."""
    dist = dist or REPO_ROOT / "frontend" / "dist"
    index = dist / "index.html"
    if not index.is_file():
        return
    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        if path.startswith("api/"):
            raise HTTPException(404)
        candidate = (dist / path).resolve()
        if path and candidate.is_file() and candidate.is_relative_to(dist.resolve()):
            return FileResponse(candidate)
        return FileResponse(index)


mount_frontend()
