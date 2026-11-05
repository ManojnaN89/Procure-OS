from fastapi import FastAPI
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from p2p.config import get_settings
from p2p.platform.auth import router as auth_router

app = FastAPI(title="P2P platform", version="0.0.1")
app.include_router(auth_router)


@app.get("/api/health")
def health() -> dict[str, str]:
    """Liveness plus a check that the control database answers."""
    db = "ok"
    try:
        engine = create_engine(get_settings().control_database_url, connect_args={"connect_timeout": 2})
        with engine.connect() as conn:
            conn.execute(text("select 1"))
        engine.dispose()
    except SQLAlchemyError:
        db = "unreachable"
    return {"status": "ok", "database": db}
