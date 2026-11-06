"""Command line for platform administration.

python -m p2p.cli control migrate
python -m p2p.cli tenant create <slug> [--name "Display name"]
python -m p2p.cli tenant migrate-all
python -m p2p.cli user create <tenant> <email> --name NAME --role Admin
        asks for the password; for scripts set P2P_NEW_PASSWORD instead
python -m p2p.cli import masters <tenant> <dataset folder> [--dry-run]
        reads the master data of a dataset into a company and reports every row it left out and why
python -m p2p.cli import stock <tenant> <dataset folder> [--dry-run]
        reads the stock movement history of a dataset (after the masters are in)
python -m p2p.cli replenish <tenant> [--dry-run] [--no-submit] [--schedule]
        creates purchase requests for stock at its reorder point and sends them for approval; --schedule queues the daily morning run
python -m p2p.cli worker      runs the job worker (timers, escalations, email, mailbox polling) until stopped
"""

import argparse
import getpass
import os
import sys
from pathlib import Path

from p2p.platform import routing, tenants, users, worker


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="p2p")
    sub = p.add_subparsers(dest="group", required=True)
    control = sub.add_parser("control").add_subparsers(dest="cmd", required=True)
    control.add_parser("migrate")
    tenant = sub.add_parser("tenant").add_subparsers(dest="cmd", required=True)
    create = tenant.add_parser("create")
    create.add_argument("slug")
    create.add_argument("--name")
    tenant.add_parser("migrate-all")
    user = sub.add_parser("user").add_subparsers(dest="cmd", required=True)
    ucreate = user.add_parser("create")
    ucreate.add_argument("tenant")
    ucreate.add_argument("email")
    ucreate.add_argument("--name", required=True)
    ucreate.add_argument("--role", default="Requester", choices=users.ROLES)
    imp = sub.add_parser("import").add_subparsers(dest="cmd", required=True)
    for name in ("masters", "stock"):
        one = imp.add_parser(name)
        one.add_argument("tenant")
        one.add_argument("dataset")
        one.add_argument("--dry-run", action="store_true")
    rep = sub.add_parser("replenish")
    rep.add_argument("tenant")
    rep.add_argument("--dry-run", action="store_true")
    rep.add_argument("--schedule", action="store_true")
    rep.add_argument("--no-submit", action="store_true")
    sub.add_parser("worker")
    args = p.parse_args(argv)

    if args.group == "worker":
        print("worker running; stop with Ctrl-C")
        try:
            worker.run_forever()
        except KeyboardInterrupt:
            pass
        return 0
    if args.group == "replenish":
        from datetime import datetime

        from sqlalchemy.orm import Session

        from p2p.formulas.calendar import IST
        from p2p.requests import jobs, replenishment

        if args.schedule:
            jobs.schedule(args.tenant)
            print(f"daily replenishment queued for {args.tenant}; it runs when the worker is running")
            return 0
        with Session(routing.engine_for(args.tenant)) as db:
            result = replenishment.run(
                db, as_of=datetime.now(IST).date(), dry_run=args.dry_run, tenant=args.tenant, submit=not args.no_submit
            )
        verb = "would create" if args.dry_run else "created"
        print(f"{verb} {len(result.requests)} request(s) with {result.line_count} line(s), total {result.total_value}")
        for req in result.requests:
            fate = req.outcome or (f"left as a draft: {req.submit_error}" if req.submit_error else req.route)
            print(
                f"  {req.no or '(no number yet)'}  {req.location_code}  {req.category_code}  {req.priority}  "
                f"{len(req.lines)} lines  {req.total_value}  {fate}"
            )
        for reason, count in replenishment.summary(result)["skipped"].items():
            print(f"  skipped {count}: {reason}")
        return 0
    if args.group == "import":
        from sqlalchemy.orm import Session

        from p2p.masters.importer import import_masters
        from p2p.receiving.stock import import_movements

        load = import_masters if args.cmd == "masters" else import_movements
        with Session(routing.engine_for(args.tenant)) as db:
            report = load(db, Path(args.dataset), dry_run=args.dry_run)
        verb = "would load" if args.dry_run else "loaded"
        print(
            f"{verb} {report.rows_loaded} rows in {report.seconds} s; {len(report.rejected)} left out, {len(report.warnings)} warnings"
        )
        for problem in report.rejected[:25]:
            print(f"  left out  {problem.table} line {problem.line}: {problem.reason}")
        for problem in report.warnings[:10]:
            print(f"  warning   {problem.table}: {problem.reason}")
        return 1 if report.rejected else 0
    if args.group == "control":
        tenants.migrate_control()
        print("control database is up to date")
        return 0
    if args.group == "user":
        password = os.environ.get("P2P_NEW_PASSWORD") or getpass.getpass("Password: ")
        try:
            uid = users.create_user(args.tenant, args.email, password, args.name, args.role)
        except ValueError as exc:
            print(f"not created: {exc}")
            return 1
        print(f"user {uid} created in tenant {args.tenant}")
        return 0
    if args.cmd == "create":
        try:
            res = tenants.create_tenant(args.slug, args.name)
        except ValueError as exc:
            print(f"not created: {exc}")
            return 1
        outcome = f"created, revision {res.revision}" if res.ok else f"FAILED {res.error}"
        print(f"tenant {res.slug}: {outcome}")
        return 0 if res.ok else 1
    results = tenants.migrate_all()
    for r in results:
        print(f"{r.slug}: {'ok, revision ' + str(r.revision) if r.ok else 'FAILED ' + str(r.error)}")
    return 1 if any(not r.ok for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
