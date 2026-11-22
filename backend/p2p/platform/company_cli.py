"""The platform team's commands for a company's state (block 0d): `p2p.cli company ...` and `p2p.cli user repair`.

Exit codes: 0 done, 1 refused by the data (a readiness blocker, rows left out), 2 refused by the state or the move.
"""

import argparse
import json
from pathlib import Path

from sqlalchemy.orm import Session

from p2p.formulas.words import count
from p2p.platform import company_state, readiness, routing, tenants, users
from p2p.platform.company_state import StateError, operator


def add_parsers(sub: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    company = sub.add_parser("company").add_subparsers(dest="cmd", required=True)
    state = company.add_parser("state")
    state.add_argument("slug")
    state.add_argument("to", nargs="?", help="setup: a company in cutover goes back to setup")
    _who(state, required=False)
    ready = company.add_parser("readiness")
    ready.add_argument("slug")
    ready.add_argument("--group", dest="checks", default="all", choices=readiness.GROUPS)
    ready.add_argument("--json", action="store_true")
    cut = company.add_parser("cutover")
    cut.add_argument("slug")
    _who(cut)
    cut.add_argument("--dry-run", action="store_true")
    load = company.add_parser("cutover-import")
    load.add_argument("slug")
    load.add_argument("folder")
    load.add_argument("--as", dest="email", required=True, help="the login of the person who loads the files")
    load.add_argument("--dry-run", action="store_true")
    suspend = company.add_parser("suspend")
    suspend.add_argument("slug")
    suspend.add_argument("--kind", required=True, choices=company_state.SUSPENSION_KINDS)
    _who(suspend)
    resume = company.add_parser("resume")
    resume.add_argument("slug")
    _who(resume)
    archives = company.add_parser("archives")
    archives.add_argument("slug")


def _who(p: argparse.ArgumentParser, *, required: bool = True) -> None:
    p.add_argument("--reason", required=required)
    p.add_argument("--operator", required=required, help="who in the platform team runs this")


def run(args: argparse.Namespace) -> int:
    try:
        return _COMMANDS[args.cmd](args)
    except StateError as exc:
        print(f"refused: {exc}")
        return 2


def _state(args: argparse.Namespace) -> int:
    if args.to:
        if args.reason is None or args.operator is None:
            print("refused: a move needs --reason and --operator")
            return 2
        done = company_state.move(args.slug, args.to, actor=operator(args.operator), reason=args.reason)
        print(f"{args.slug}: {done.from_state} -> {done.to_state}")
        return 0
    print(f"{args.slug} is {company_state.state_of(args.slug)}")
    for h in company_state.history(args.slug):
        extra = f", approved by {h['approved_by']}" if h["approved_by"] else ""
        print(f"  {h['at']}  {h['from']} -> {h['to']}  by {h['by']}{extra}: {h['reason']}")
    return 0


def _readiness(args: argparse.Namespace) -> int:
    with Session(routing.engine_for(args.slug)) as db:
        result = readiness.check(db, args.slug, group=args.checks)
    if args.json:
        print(json.dumps(result.view(), indent=2))
    else:
        for c in result.checks:
            mark = "ok" if c.ok else "FAIL" if c.severity == "blocker" else "warn"
            print(f"{mark:<5} {c.code}  {c.title}")
            for p in c.problems:
                print(f"        {p}")
        print("ready" if result.ready else f"not ready: {', '.join(result.blockers)}")
    return 0 if result.ready else 1


def _cutover(args: argparse.Namespace) -> int:
    from p2p.platform import cutover  # noqa: PLC0415 - only the platform team's command needs it

    try:
        done = cutover.archive_and_rebuild(args.slug, reason=args.reason, operator=args.operator, dry_run=args.dry_run)
    except cutover.NotReady as exc:
        print(f"not ready: {exc}")
        return 1
    except cutover.CutoverFailed as exc:
        print(f"cutover failed: {exc}")
        return 1
    for line in done.lines():
        print(line)
    return 0


def _cutover_import(args: argparse.Namespace) -> int:
    from p2p.platform import cutover_import  # noqa: PLC0415

    uid = users.user_id_by_email(args.slug, args.email)
    if uid is None:
        print(f"refused: {args.email} is no login of {args.slug}")
        return 2
    with Session(routing.engine_for(args.slug)) as db:
        report = cutover_import.import_cutover(db, Path(args.folder), user_id=uid, dry_run=args.dry_run)
    verb = "would load" if args.dry_run else "loaded"
    print(f"{verb}: " + ", ".join(f"{k} {n}" for k, n in report.loaded.items()))
    print("loaded before: " + ", ".join(f"{k} {n}" for k, n in report.already.items()))
    for kind, c in report.control.items():
        mark = "ok" if c["ok"] else "DIFFERS"
        print(
            f"control {kind}: file {c['file_count']} / {c['file_total']}, company {c['loaded_count']} / "
            f"{c['loaded_total']}  {mark}"
        )
    for r in report.rejected[:25]:
        print(f"  left out  {r.file} line {r.line}: {r.reason}")
    if len(report.rejected) > 25:
        print(f"  ... and {len(report.rejected) - 25} more")
    for w in report.warnings[:25]:
        print(f"  warning   {w}")
    print(f"{count(len(report.rejected), 'row')} left out; control {'ok' if report.control_ok else 'FAILS'}")
    return 1 if report.rejected or not report.control_ok else 0


def _suspend(args: argparse.Namespace) -> int:
    done = company_state.move(args.slug, "suspended", actor=operator(args.operator), reason=args.reason, kind=args.kind)
    print(f"{args.slug}: {done.from_state} -> suspended ({args.kind})")
    return 0


def _resume(args: argparse.Namespace) -> int:
    done = company_state.resume(args.slug, actor=operator(args.operator), reason=args.reason)
    print(f"{args.slug}: suspended -> {done.to_state}")
    return 0


def _archives(args: argparse.Namespace) -> int:
    from sqlalchemy import text  # noqa: PLC0415

    with tenants._owner_control_session() as s:  # noqa: SLF001 - the platform team reads the archives
        rows = s.execute(
            text(
                "select a.database_name, a.archived_at, a.operator, a.reason, a.audit_rows from tenant_archive a "
                "join tenant t on t.id = a.tenant_id where t.slug = :s order by a.id"
            ),
            {"s": args.slug},
        ).all()
    for name, at, who, why, n in rows:
        print(f"{name}  {at:%Y-%m-%d %H:%M}  by {who}: {why} ({count(int(n), 'audit row')})")
    print(count(len(rows), "archive"))
    return 0


def repair_users(slug: str) -> int:
    fixes = users.repair(slug)
    for f in fixes:
        print(f"  {f.fixed:<22} user {f.user_id} {f.email}")
    print(f"{count(len(fixes), 'fix', 'fixes')}")
    return 0


_COMMANDS = {
    "state": _state,
    "readiness": _readiness,
    "cutover": _cutover,
    "cutover-import": _cutover_import,
    "suspend": _suspend,
    "resume": _resume,
    "archives": _archives,
}
