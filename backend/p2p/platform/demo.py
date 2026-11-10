"""The small demo company, made again from nothing with one command.

    python -m p2p.cli demo reset flow

Drops the company `flow` (if it is there), makes it again, loads the dataset made by datagen/generator/profile_flow.py,
sets who approves what, and gives each of the eight people a login with the same throwaway development password. The logins are
written to `.dev-credentials` (git-ignored). It refuses to run against any database that is not on this machine. Running it twice leaves one company and one set of logins.
"""

import importlib.util
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.config import REPO_ROOT
from p2p.masters.importer import ImportReport, import_masters
from p2p.platform import audit, routing, tenants, users

# A throwaway DEVELOPMENT password for the synthetic demo company only (fake people, fake suppliers, fake data). It is
# the same for all eight logins so a walk-through is quick. The platform needs at least 10 characters, which is why it
# is not shorter. `reset_flow` refuses to run unless the control database is on this machine, so these accounts can
# never be created on a real server.
DEV_PASSWORD = "flow123456"  # noqa: S105 - deliberate: see the comment above
LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")

SLUG = "flow"
NAME = "Flow Engineering Pvt Ltd"
DATASET = REPO_ROOT / "data" / "datasets" / "flow-demo-v1"
CREDENTIALS = REPO_ROOT / ".dev-credentials"
GENERATOR = REPO_ROOT / "datagen" / "generator" / "profile_flow.py"

# The roles an approval chain can name in this company. `manager` is the requester's own manager; `plant_head` is
# whoever holds that designation. Everything else the platform ships with (CFO, director, ...) is not used here.
APPROVER_ROLES = [
    ("manager", "requester_manager", None),
    ("dept_head", "department_head", None),
    ("plant_head", "designation", "Plant Head"),
]
REPLENISHMENT_DEPARTMENT = "MNT"


class DemoError(Exception):
    pass


@dataclass
class FlowResult:
    slug: str
    logins: list[tuple[str, str, str]] = field(default_factory=list)  # email, role, designation (no passwords)
    report: ImportReport | None = None
    credentials_file: Path | None = None


def _generator() -> Any:
    spec = importlib.util.spec_from_file_location("profile_flow", GENERATOR)
    if spec is None or spec.loader is None:
        raise DemoError(f"cannot load {GENERATOR}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def ensure_dataset(dataset: Path = DATASET) -> Path:
    """The dataset folder, made first when it is not there."""
    if not (dataset / "master" / "user.csv").is_file():
        gen = _generator()
        gen.build(dataset)
        problems = gen.check(dataset)
        if problems:
            raise DemoError("the generated dataset is not as designed: " + "; ".join(problems))
    return dataset


# The login role of a person: the most specific job they hold in the organisation's own table.
ROLE_ORDER = (
    ("admin", "Admin"),
    ("finance", "Finance"),
    ("ap", "AP"),
    ("buyer", "Buyer"),
    ("store", "Store"),
    ("approver", "Approver"),
)


def login_role(held: set[str]) -> str:
    return next((login for org, login in ROLE_ORDER if org in held), "Requester")


def _configure(db: Session) -> None:
    """The settings the flow needs that are not master data: who the chain roles are, and who approves planned requests."""
    db.execute(text("delete from approver_role"))
    for role, source, value in APPROVER_ROLES:
        db.execute(
            text("insert into approver_role (role, source, value) values (:r, :s, :v)"),
            {"r": role, "s": source, "v": value},
        )
    audit.record(
        db, audit.SYSTEM, "demo.configure", "approver_role", SLUG, after={"roles": [r for r, _, _ in APPROVER_ROLES]}
    )
    db.execute(
        text(
            "insert into app_setting (key, value) values ('replenishment_department', :d) on conflict (key) do update set value = :d"
        ),
        {"d": REPLENISHMENT_DEPARTMENT},
    )
    audit.record(
        db,
        audit.SYSTEM,
        "demo.configure",
        "app_setting",
        "replenishment_department",
        after={"value": REPLENISHMENT_DEPARTMENT},
    )
    db.commit()


def _write_credentials(path: Path, slug: str, rows: list[tuple[str, str, str, str]]) -> None:
    """Replace the section of this company in the credentials file (other companies' sections stay as they are)."""
    header = f"# local logins for tenant '{slug}' (the small demo company; git-ignored)"
    kept: list[str] = []
    if path.is_file():
        skipping = False
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("# local logins for tenant"):
                skipping = f"tenant '{slug}'" in line
            elif skipping and not line.strip():
                skipping = False
                continue
            if not skipping:
                kept.append(line)
    while kept and not kept[-1].strip():
        kept.pop()
    block = [header] + [
        f"{email}  {password}   # {designation} ({role})" for email, password, role, designation in rows
    ]
    path.write_text("\n".join(kept + ([""] if kept else []) + block) + "\n", encoding="utf-8")


def require_local_database() -> None:
    host = tenants.control_url().host or ""
    if host not in LOCAL_HOSTS:
        raise DemoError(
            f"the demo company has a published development password, so it is only made on this machine; "
            f"the control database is at '{host}'"
        )


def reset_flow(dataset: Path | None = None, *, credentials: Path | None = CREDENTIALS, slug: str = SLUG) -> FlowResult:
    """Make the small demo company from nothing. Raises DemoError if the dataset does not load cleanly."""
    require_local_database()
    folder = ensure_dataset(dataset or DATASET)
    tenants.drop_tenant(slug)
    created = tenants.create_tenant(slug, NAME)
    if not created.ok:
        raise DemoError(f"the company could not be created: {created.error}")
    result = FlowResult(slug)
    with Session(routing.engine_for(slug)) as db:
        result.report = import_masters(db, folder)
        if result.report.rejected:
            first = result.report.rejected[0]
            raise DemoError(
                f"{len(result.report.rejected)} row(s) left out; first: {first.table} line {first.line}: {first.reason}"
            )
        _configure(db)
        people = db.execute(text("select code, name, designation, email from org_user order by code")).all()
        held: dict[str, set[str]] = {}
        for code, role in db.execute(text("select user_code, role from org_user_role")):
            held.setdefault(code, set()).add(role)
    written: list[tuple[str, str, str, str]] = []
    for code, name, designation, email in people:
        role = login_role(held.get(code, set()))
        password = DEV_PASSWORD
        users.create_user(slug, email, password, name, role)
        written.append((email, password, role, designation))
        result.logins.append((email, role, designation))
    if credentials is not None:
        _write_credentials(credentials, slug, written)
        result.credentials_file = credentials
    return result
