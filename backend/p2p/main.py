import importlib
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from p2p.config import REPO_ROOT, get_settings
from p2p.platform import logs, tenants
from p2p.platform.admin_api import router as admin_router
from p2p.platform.agent_api import router as agent_router
from p2p.platform.approval_link_api import router as approval_link_router
from p2p.platform.auth import router as auth_router
from p2p.platform.buying_rules_api import router as buying_rules_router
from p2p.platform.demand_api import router as demand_router
from p2p.platform.export_api import router as export_router
from p2p.platform.files import router as files_router
from p2p.platform.guides_api import router as guides_router
from p2p.platform.invoice_api import router as invoice_router
from p2p.platform.invoice_intake_api import router as invoice_intake_router
from p2p.platform.master_api import router as master_router
from p2p.platform.material_api import router as material_router
from p2p.platform.order_api import router as order_router
from p2p.platform.payment_api import router as payment_router
from p2p.platform.price_api import router as price_router
from p2p.platform.prospect_api import router as prospect_router
from p2p.platform.reconciliation_api import router as reconciliation_router
from p2p.platform.service_entry_api import router as service_entry_router
from p2p.platform.setup_api import router as setup_router
from p2p.platform.supplier_agent_api import router as supplier_agent_router
from p2p.platform.supplier_link_api import router as supplier_link_router
from p2p.platform.ui_api import router as ui_router
from p2p.platform.ui_api import sockets as ui_sockets
from p2p.platform.workspace_api import router as workspace_router

app = FastAPI(title="P2P platform", version="0.0.1")
logs.install(app)
for _module in filter(None, (m.strip() for m in get_settings().extra_modules.split(","))):
    importlib.import_module(_module)
app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(files_router)
app.include_router(export_router)
app.include_router(demand_router)
app.include_router(material_router)
app.include_router(setup_router)
app.include_router(buying_rules_router)
app.include_router(ui_router)
app.include_router(workspace_router)
app.include_router(guides_router)
app.include_router(invoice_router)
app.include_router(invoice_intake_router)
app.include_router(master_router)
app.include_router(prospect_router)
app.include_router(price_router)
app.include_router(order_router)
app.include_router(agent_router)
app.include_router(approval_link_router)
app.include_router(supplier_link_router)
app.include_router(payment_router)
app.include_router(reconciliation_router)
app.include_router(supplier_agent_router)
app.include_router(service_entry_router)
app.include_router(ui_sockets)


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


def mount_frontend(admin_dist: Path | None = None, ui_dir: Path | None = None) -> None:
    """Serve the two web front ends from the API's own address.

    /admin  is the React admin app (built into frontend/dist); /  is the main interface (ui/), which is plain files.
    """
    admin_dist = admin_dist or REPO_ROOT / "frontend" / "dist"
    ui_dir = ui_dir or REPO_ROOT / "ui"
    admin_index = admin_dist / "index.html"
    if admin_index.is_file():
        app.mount("/admin/assets", StaticFiles(directory=admin_dist / "assets"), name="admin-assets")

        @app.get("/admin", include_in_schema=False)
        @app.get("/admin/{path:path}", include_in_schema=False)
        def admin_app(path: str = "") -> FileResponse:
            candidate = (admin_dist / path).resolve()
            if path and candidate.is_file() and candidate.is_relative_to(admin_dist.resolve()):
                return FileResponse(candidate)
            return FileResponse(admin_index)

    if (ui_dir / "index.html").is_file():
        app.mount("/", StaticFiles(directory=ui_dir, html=True), name="ui")


mount_frontend()
