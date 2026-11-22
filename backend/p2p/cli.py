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
python -m p2p.cli import demand <tenant> <dataset folder or file> [--dry-run] [--kind KIND]
        reads the ERP's demand (production orders and their materials, MRP results, sales orders, demand plan,
        joiner kits and the joiner plan);
        rows loaded before are skipped; a changed or cancelled row is applied (requests cut, stock released, cases for
        the buyer or the store) and listed with what it did
python -m p2p.cli material make <tenant>
        makes material requests for imported production orders that have none (the import does this by itself)
python -m p2p.cli material check <tenant> [--no-submit] [--schedule]
        the store check: sets free stock aside for open material requests, earliest need-by first, and sends what is
        short for approval as purchase requests; prints what is at risk (supply promised too late) and the cases;
        --schedule queues the daily 05:30 run (which leaves its requests as drafts unless store_check_submit is on)
python -m p2p.cli material explain <tenant> <MR-no>
        the planning capability explains the request's short and at-risk lines (only when switched on)
python -m p2p.cli replenish <tenant> [--dry-run] [--no-submit] [--schedule]
        creates purchase requests for stock at its reorder point and sends them for approval; --schedule queues the daily morning run
python -m p2p.cli source <tenant> [--request NO] [--no-send]
        decides how approved requests are bought (contract or quotation request) and sends the quotation requests
python -m p2p.cli remind <tenant> [--dry-run] [--schedule]
        reminds suppliers who have not answered a quotation request; --schedule queues the daily morning run
python -m p2p.cli order <tenant> [--request NO]
        makes purchase orders from the lines that are ready (contract prices and awarded quotations) and sends them for approval
python -m p2p.cli confirm-services <tenant> [--dry-run]
        at the end of a period: emails each person who asked for a service still to confirm, one signed link per service
python -m p2p.cli demo reset apex|brightwave [--dataset DIR]
        drops and makes again a demo company from its profile in datagen/catalog (master data, stock history and the
        ERP's production orders, each with its material request);
        the logins go to .dev-credentials
python -m p2p.cli demo populate [apex] [--reset]
        fills the demo company with cases at every stage of the flow (needs a company with no requests, or --reset)
python -m p2p.cli demo reset apex --state setup
        the same, left in setup (test data, archived at cutover) for a go-live rehearsal; a plain reset ends live
python -m p2p.cli company state <slug> [setup --reason R --operator N]
        the company's state and its history; or a company in cutover goes back to setup
python -m p2p.cli company readiness <slug> [--group all|setup|cutover] [--json]
        the readiness checks (exit 1 when a blocker fails)
python -m p2p.cli company cutover <slug> --reason R --operator N [--dry-run]
        setup to cutover: the setup database is kept as an archive and a clean one made with the configuration
python -m p2p.cli company cutover-import <slug> <folder> --as EMAIL [--dry-run]
        loads the open documents of the old system (cutover only); exit 1 when rows are left out or control fails
python -m p2p.cli company suspend <slug> --kind commercial|security --reason R --operator N
python -m p2p.cli company resume <slug> --reason R --operator N
python -m p2p.cli company archives <slug>
python -m p2p.cli user repair <tenant>
        finishes logins a user creation left half made (DF-36)
python -m p2p.cli worker      runs the job worker (timers, escalations, email, mailbox polling) until stopped
"""

import argparse
import getpass
import json
import os
import sys
from pathlib import Path

from p2p.formulas.calendar import business_today
from p2p.formulas.money import format_inr
from p2p.formulas.words import count
from p2p.platform import company_cli, db_logins, routing, tenants, users, worker
from p2p.platform.company_state import StateError


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
    urepair = user.add_parser("repair")
    urepair.add_argument("tenant")
    company_cli.add_parsers(sub)
    imp = sub.add_parser("import").add_subparsers(dest="cmd", required=True)
    for name in ("masters", "stock", "demand"):
        one = imp.add_parser(name)
        one.add_argument("tenant")
        one.add_argument("dataset")
        one.add_argument("--dry-run", action="store_true")
        if name == "demand":
            one.add_argument("--kind", help="what the file holds, when its name does not say")
    mat = sub.add_parser("material").add_subparsers(dest="cmd", required=True)
    for name in ("make", "check"):
        one = mat.add_parser(name)
        one.add_argument("tenant")
        if name == "check":
            one.add_argument("--no-submit", action="store_true")
            one.add_argument("--schedule", action="store_true")
    explain = mat.add_parser("explain")
    explain.add_argument("tenant")
    explain.add_argument("mr_no")
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
    rev = sub.add_parser("review")
    rev.add_argument("tenant")
    rev.add_argument("--dry-run", action="store_true")
    ordr = sub.add_parser("order")
    ordr.add_argument("tenant")
    ordr.add_argument("--request")
    ask = sub.add_parser("confirm-services")
    ask.add_argument("tenant")
    ask.add_argument("--dry-run", action="store_true")
    demo = sub.add_parser("demo").add_subparsers(dest="cmd", required=True)
    reset = demo.add_parser("reset")
    reset.add_argument("name", help="a company with a profile in datagen/catalog (apex, brightwave)")
    reset.add_argument("--dataset")
    reset.add_argument("--state", choices=("setup", "live"), default="live", help="setup: for a go-live rehearsal")
    fill = demo.add_parser("populate")
    fill.add_argument("name", nargs="?", default="apex")
    fill.add_argument("--reset", action="store_true")
    sub.add_parser("worker")
    args = p.parse_args(argv)

    if args.group == "company":
        return company_cli.run(args)
    if args.group == "worker":
        print("worker running; stop with Ctrl-C")
        try:
            worker.run_forever()
        except KeyboardInterrupt:
            pass
        return 0
    if args.group == "demo":
        from p2p.platform.demo import DemoError, company, reset_company

        if args.cmd == "populate":
            from p2p.platform.demo_cases import populate

            try:
                if args.reset:
                    reset_company(company(args.name))
                filled = populate(args.name)
            except DemoError as exc:
                print(f"not made: {exc}")
                return 1
            print(f"company '{filled.slug}' now has cases at {len(filled.cases)} stages:")
            for stage, number in filled.cases.items():
                print(f"  {number:<14} {stage}")
            print(f"still low with nothing on the way: {', '.join(filled.low_items_left) or 'none'}")
            if filled.stopped:
                print(f"the later stages stopped here (the earlier ones are in place): {filled.stopped}")
                return 1
            return 0
        try:
            built = reset_company(company(args.name), Path(args.dataset) if args.dataset else None, state=args.state)
        except DemoError as exc:
            print(f"not built: {exc}")
            return 1
        loaded = built.report.rows_loaded if built.report else 0
        moves = f", {built.stock.rows_loaded} stock movements" if built.stock else ""
        if built.demand:
            moves += (
                f", {built.demand.rows_loaded} demand rows ({len(built.demand.material_requests)} material requests)"
            )
        print(f"company '{built.slug}' made ({args.state}): {loaded} master rows{moves}, {len(built.logins)} logins")
        for email, role, designation in built.logins:
            print(f"  {email}  {role}  ({designation})")
        print(f"passwords are in {built.credentials_file}")
        return 0
    if args.group == "material":
        from sqlalchemy.orm import Session

        from p2p.requests import jobs as request_jobs
        from p2p.requests import material

        if args.cmd == "check" and args.schedule:
            request_jobs.schedule_check(args.tenant)
            print(f"daily store check queued for {args.tenant}; it runs when the worker is running")
            return 0
        with Session(routing.engine_for(args.tenant)) as db:
            if args.cmd == "make":
                numbers = material.make_requests(db)
                print(f"{count(len(numbers), 'material request')} made or added to: {', '.join(numbers) or '-'}")
                return 0
            if args.cmd == "explain":
                from p2p.agents import planning
                from p2p.ai_steps.gateway import Gateway

                got = planning.explain(db, args.tenant, None, args.mr_no, Gateway())
                print(f"{args.mr_no}: {got.status}" + (f" ({'; '.join(got.reasons)})" if got.reasons else ""))
                if got.summary:
                    print(got.summary)
                for ln in got.lines or got.facts:
                    print(f"  {json.dumps(ln, default=str)}")
                return 0
            checked = material.store_check(db, tenant=args.tenant, submit=not args.no_submit)
        for c in checked.requests:
            short = ", ".join(f"{ln.item_code} {ln.short}" for ln in c.lines if ln.short) or "nothing"
            risk = ", ".join(f"{ln.item_code} {ln.at_risk}" for ln in c.lines if ln.at_risk)
            if risk:
                short += f"; at risk {risk}"
            req = c.request
            bought = f"; {req.no} {req.total_value} {req.outcome or req.submit_error or 'draft'}" if req else ""
            print(f"  {c.mr_no} ({c.order_no}): short {short}{bought}")
            if c.unpriced:
                print(f"    no price to estimate: {', '.join(c.unpriced)}")
        for case in checked.cases:
            print(f"  late-supply case {case['task_id']} for {case['assignee']}: {case['po_no']} ({case['mr_no']})")
        print(
            f"{count(len(checked.requests), 'material request')} checked, {count(len(checked.purchase_requests), 'purchase request')}"
        )
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
        print(
            f"{verb} {count(len(result.requests), 'request')} with {count(result.line_count, 'line')}, total {format_inr(result.total_value)}"
        )
        for req in result.requests:
            fate = req.outcome or (f"left as a draft: {req.submit_error}" if req.submit_error else req.route)
            print(
                f"  {req.no or '(no number yet)'}  {req.location_code}  {req.category_code}  {req.priority}  "
                f"{len(req.lines)} lines  {req.total_value}  {fate}"
            )
        for reason, n in replenishment.summary(result)["skipped"].items():
            print(f"  skipped {n}: {reason}")
        return 0
    if args.group == "review":
        from sqlalchemy.orm import Session

        from p2p.sourcing import price_review

        with Session(routing.engine_for(args.tenant)) as db:
            found = price_review.run(db, args.tenant, business_today(), dry_run=args.dry_run)
        for label, rows in (("opened", found.opened), ("joined", found.joined), ("below the thresholds", found.below)):
            for row in rows:
                print(f"{label}: {row}")
        print(f"{len(found.opened)} opened, {len(found.joined)} joined, {len(found.below)} below the thresholds")
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
        print(count(len(reminded.sent), "reminder"))
        return 0
    if args.group == "confirm-services":
        from sqlalchemy.orm import Session

        from p2p.receiving import service_links

        with Session(routing.engine_for(args.tenant)) as db:
            told = service_links.ask_requesters(db, args.tenant, dry_run=args.dry_run)
        for code, services in told:
            print(f"  {'would ask' if args.dry_run else 'asked'} {code} to confirm {count(services, 'service')}")
        print(count(len(told), "person", "people"))
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
        print(f"{count(len(ordered), 'request')} ordered")
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
        print(f"{count(len(sourced), 'request')} sourced")
        return 0
    if args.group == "import":
        from sqlalchemy.orm import Session

        from p2p.masters.importer import ImportReport, import_masters
        from p2p.masters.report import update_lines
        from p2p.receiving.stock import import_movements
        from p2p.requests import demand

        report: ImportReport
        try:
            with Session(routing.engine_for(args.tenant)) as db:
                if args.cmd == "demand":
                    report = demand.import_demand(
                        db, Path(args.dataset), dry_run=args.dry_run, kind=args.kind, tenant=args.tenant
                    )
                else:
                    load = import_masters if args.cmd == "masters" else import_movements
                    report = load(db, Path(args.dataset), dry_run=args.dry_run)
        except StateError as exc:  # the company's state does not allow this import (D-02)
            print(f"refused: {exc}")
            return 2
        if isinstance(report, demand.DemandReport):
            print(f"{demand.summary(report)} ({report.seconds} s)")
            for kind, n in report.loaded.items():
                print(f"  {kind}: {n} new, {report.changed.get(kind, 0)} changed, {report.already[kind]} loaded before")
            for change in report.changes:
                print(f"  change    {demand.change_line(change.view())}")
        elif report.mode == "update":  # a later masters import: matched by key
            for line in update_lines(report):
                print(line)
            return 1 if report.rejected else 0
        else:
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
        print(f"control database is up to date; app login {db_logins.app_role_name()} is ready")
        return 0
    if args.group == "user" and args.cmd == "repair":
        return company_cli.repair_users(args.tenant)
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
        if res.ok:
            from sqlalchemy.orm import Session

            from p2p.requests import jobs as request_jobs

            with Session(routing.engine_for(res.slug)) as db:
                request_jobs.queue_daily_check(db, res.slug)
        outcome = f"created, revision {res.revision}" if res.ok else f"FAILED {res.error}"
        print(f"tenant {res.slug}: {outcome}")
        return 0 if res.ok else 1
    results = tenants.migrate_all()
    for r in results:
        print(f"{r.slug}: {'ok, revision ' + str(r.revision) if r.ok else 'FAILED ' + str(r.error)}")
    return 1 if any(not r.ok for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
