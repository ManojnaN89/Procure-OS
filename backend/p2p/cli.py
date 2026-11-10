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
python -m p2p.cli source <tenant> [--request NO] [--no-send]
        decides how approved requests are bought (contract or quotation request) and sends the quotation requests
python -m p2p.cli remind <tenant> [--dry-run] [--schedule]
        reminds suppliers who have not answered a quotation request; --schedule queues the daily morning run
python -m p2p.cli order <tenant> [--request NO]
        makes purchase orders from the lines that are ready (contract prices and awarded quotations) and sends them for approval
python -m p2p.cli demo reset flow [--dataset DIR]
        drops and makes again the small demo company (8 people, 12 items, 4 suppliers, no history); the logins go to
        .dev-credentials and are not printed
python -m p2p.cli worker      runs the job worker (timers, escalations, email, mailbox polling) until stopped
"""

import argparse
import getpass
import os
import sys
from pathlib import Path

from p2p.formulas.calendar import business_today
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
    src = sub.add_parser("source")
    src.add_argument("tenant")
    src.add_argument("--request")
    src.add_argument("--no-send", action="store_true")
    rem = sub.add_parser("remind")
    rem.add_argument("tenant")
    rem.add_argument("--dry-run", action="store_true")
    rem.add_argument("--schedule", action="store_true")
    ordr = sub.add_parser("order")
    ordr.add_argument("tenant")
    ordr.add_argument("--request")
    demo = sub.add_parser("demo").add_subparsers(dest="cmd", required=True)
    reset = demo.add_parser("reset")
    reset.add_argument("name", choices=["flow"])
    reset.add_argument("--dataset")
    sub.add_parser("worker")
    args = p.parse_args(argv)

    if args.group == "worker":
        print("worker running; stop with Ctrl-C")
        try:
            worker.run_forever()
        except KeyboardInterrupt:
            pass
        return 0
    if args.group == "demo":
        from p2p.platform.demo import DemoError, reset_flow

        try:
            company = reset_flow(Path(args.dataset) if args.dataset else None)
        except DemoError as exc:
            print(f"not made: {exc}")
            return 1
        loaded = company.report.rows_loaded if company.report else 0
        print(f"company '{company.slug}' made: {loaded} master rows, {len(company.logins)} logins")
        for email, role, designation in company.logins:
            print(f"  {email}  {role}  ({designation})")
        print(f"passwords are in {company.credentials_file}")
        return 0
    if args.group == "replenish":
        from sqlalchemy.orm import Session

        from p2p.requests import jobs, replenishment

        if args.schedule:
            jobs.schedule(args.tenant)
            print(f"daily replenishment queued for {args.tenant}; it runs when the worker is running")
            return 0
        with Session(routing.engine_for(args.tenant)) as db:
            result = replenishment.run(
                db, as_of=business_today(), dry_run=args.dry_run, tenant=args.tenant, submit=not args.no_submit
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
    if args.group == "remind":
        from sqlalchemy.orm import Session

        from p2p.sourcing import reminders

        if args.schedule:
            reminders.schedule(args.tenant)
            print(f"daily supplier reminders queued for {args.tenant}; they run when the worker is running")
            return 0
        with Session(routing.engine_for(args.tenant)) as db:
            reminded = reminders.run(db, as_of=business_today(), dry_run=args.dry_run)
        if reminded.stopped:
            print(f"no reminders: {reminded.stopped}")
        verb = "would remind" if args.dry_run else "reminded"
        for rfq_no, supplier in reminded.sent:
            print(f"  {verb} {supplier} about {rfq_no}")
        for rfq_no, supplier in reminded.failed:
            print(f"  could not reach {supplier} about {rfq_no}")
        print(f"{len(reminded.sent)} reminder(s)")
        return 0
    if args.group == "order":
        from sqlalchemy.orm import Session

        from p2p.orders import service as orders

        today = business_today()
        with Session(routing.engine_for(args.tenant)) as db:
            if args.request:
                ordered = [orders.order_request(db, args.tenant, args.request, as_of=today)]
            else:
                ordered = orders.sweep(db, args.tenant, as_of=today)
        for made in ordered:
            print(
                f"{made.request_no}: orders {', '.join(made.pos) or '-'}; {len(made.approved)} approved on the spot, "
                f"{len(made.waiting)} waiting for approvers, {len(made.held)} held for a price variance"
            )
            for note in made.skipped:
                print(f"  note: {note}")
        print(f"{len(ordered)} request(s) ordered")
        return 0
    if args.group == "source":
        from sqlalchemy.orm import Session

        from p2p.sourcing import service

        today = business_today()
        with Session(routing.engine_for(args.tenant)) as db:
            if args.request:
                sourced = [
                    service.source_request(db, args.tenant, args.request, as_of=today, dispatch=not args.no_send)
                ]
            else:
                sourced = service.sweep(db, args.tenant, as_of=today, dispatch=not args.no_send)
        for done in sourced:
            info = service.summary(done)
            print(
                f"{info['request']}: {info['contract']} on contract, {info['rfq']} to quote, {info['manual']} for a buyer; "
                f"RFQs {', '.join(info['rfqs']) or '-'}; {info['emails_sent']} emails sent; "
                f"{len(info['proposals'])} waiting for a buyer to approve sending"
            )
            for note in info["notes"]:
                print(f"  note: {note}")
        print(f"{len(sourced)} request(s) sourced")
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
