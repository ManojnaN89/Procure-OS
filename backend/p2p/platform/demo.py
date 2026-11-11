"""The demo companies, made again from nothing with one command.

    python -m p2p.cli demo reset apex          # the main demo company, described by datagen/catalog/apex/company.yaml
    python -m p2p.cli demo reset brightwave    # the second one

Drops the company (if it is there), makes it again, loads its generated dataset (the master data, then the stock history
and the ERP's demand files when the dataset has them: each production order then has its material request, waiting for
the store check), sets who approves what, and gives each
person a login with the same throwaway development password. The logins are written to `.dev-credentials`
(git-ignored). It refuses to run against any database that is not on this machine. Running it twice leaves one company
and one set of logins.
"""

import importlib.util
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.config import REPO_ROOT
from p2p.masters.importer import ImportReport, import_masters
from p2p.platform import audit, routing, tenants, users
from p2p.receiving import stock
from p2p.requests.demand import DemandReport, import_demand

# A throwaway DEVELOPMENT password for the synthetic demo company only (fake people, fake suppliers, fake data). It is
# the same for every login so a walk-through is quick. The platform needs at least 10 characters, which is why it
# is not shorter. `reset_company` refuses to run unless the control database is on this machine, so these accounts can
# never be created on a real server.
DEV_PASSWORD = "flow123456"  # noqa: S105 - deliberate: see the comment above
LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")

MAIN = "apex"  # the main demo company; `brightwave` is the second
CREDENTIALS = REPO_ROOT / ".dev-credentials"
GENERATORS = REPO_ROOT / "datagen" / "generator"
CATALOG = REPO_ROOT / "datagen" / "catalog"


class DemoError(Exception):
    pass


@dataclass
class DemoResult:
    slug: str
    logins: list[tuple[str, str, str]] = field(default_factory=list)  # email, role, designation (no passwords)
    report: ImportReport | None = None
    stock: ImportReport | None = None  # the stock history, when the dataset has one
    demand: DemandReport | None = None  # the ERP's demand (production orders), when the dataset has it
    credentials_file: Path | None = None


@dataclass(frozen=True)
class DemoCompany:
    """A demo company: its tenant, its dataset and how to write it, and the settings that are not master data."""

    slug: str
    name: str
    dataset: Path
    approver_roles: list[tuple[str, str, str | None]]
    replenishment_department: str
    build: Callable[[Path], list[str]]  # writes the dataset into a folder and returns what is wrong with it


def _generator(path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:
        raise DemoError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def profiled_companies() -> list[str]:
    """The companies described by a profile file in the catalogue."""
    return sorted(p.parent.name for p in CATALOG.glob("*/company.yaml"))


def company(name: str) -> DemoCompany:
    """A company with a profile file datagen/catalog/<name>/company.yaml."""
    if name not in profiled_companies():
        raise DemoError(f"no demo company '{name}'; there are {', '.join(profiled_companies())}")
    gen = _generator(GENERATORS / "company_profile.py")
    profile = gen.CompanyProfile.named(name)

    def build(folder: Path) -> list[str]:
        gen.build(profile, folder)
        problems: list[str] = gen.check(folder, profile)
        return problems

    return DemoCompany(
        slug=profile.tenant,
        name=profile["company"]["legal_name"],
        dataset=REPO_ROOT / "data" / "datasets" / profile.dataset,
        approver_roles=profile.approver_roles,
        replenishment_department=profile["replenishment_department"],
        build=build,
    )


def ensure_dataset(dataset: Path, demo: DemoCompany) -> Path:
    """The dataset folder, made first when it is not there."""
    if not (dataset / "master" / "user.csv").is_file():
        problems = demo.build(dataset)
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


def configure(db: Session, demo: DemoCompany, slug: str) -> None:
    """The settings the flow needs that are not master data: who the chain roles are, and who approves planned requests."""
    db.execute(text("delete from approver_role"))
    for role, source, value in demo.approver_roles:
        db.execute(
            text("insert into approver_role (role, source, value) values (:r, :s, :v)"),
            {"r": role, "s": source, "v": value},
        )
    audit.record(
        db,
        audit.SYSTEM,
        "demo.configure",
        "approver_role",
        slug,
        after={"roles": [r for r, _, _ in demo.approver_roles]},
    )
    db.execute(
        text(
            "insert into app_setting (key, value) values ('replenishment_department', :d) on conflict (key) do update set value = :d"
        ),
        {"d": demo.replenishment_department},
    )
    audit.record(
        db,
        audit.SYSTEM,
        "demo.configure",
        "app_setting",
        "replenishment_department",
        after={"value": demo.replenishment_department},
    )
    db.commit()


def _write_credentials(path: Path, slug: str, rows: list[tuple[str, str, str, str]]) -> None:
    """Replace the section of this company in the credentials file (other companies' sections stay as they are)."""
    header = f"# local logins for tenant '{slug}' (a demo company; git-ignored)"
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


def reset_company(
    demo: DemoCompany, dataset: Path | None = None, *, credentials: Path | None = CREDENTIALS, slug: str | None = None
) -> DemoResult:
    """Make a demo company from nothing. Raises DemoError if the dataset does not load cleanly."""
    require_local_database()
    slug = slug or demo.slug
    folder = ensure_dataset(dataset or demo.dataset, demo)
    tenants.drop_tenant(slug)
    created = tenants.create_tenant(slug, demo.name)
    if not created.ok:
        raise DemoError(f"the company could not be created: {created.error}")
    result = DemoResult(slug)
    with Session(routing.engine_for(slug)) as db:
        result.report = import_masters(db, folder)
        if result.report.rejected:
            first = result.report.rejected[0]
            raise DemoError(
                f"{len(result.report.rejected)} row(s) left out; first: {first.table} line {first.line}: {first.reason}"
            )
        if (folder / "history" / "stock_movement.csv").is_file():
            result.stock = stock.import_movements(db, folder)
            if result.stock.rejected:
                first = result.stock.rejected[0]
                raise DemoError(
                    f"{len(result.stock.rejected)} stock movement(s) left out; first: line {first.line}: {first.reason}"
                )
        if (folder / "inputs" / "production_order.csv").is_file():
            result.demand = import_demand(db, folder)
            if result.demand.rejected:
                first = result.demand.rejected[0]
                raise DemoError(
                    f"{len(result.demand.rejected)} demand row(s) left out; first: {first.table} line {first.line}: "
                    f"{first.reason}"
                )
        configure(db, demo, slug)
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
