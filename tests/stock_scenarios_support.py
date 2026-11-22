"""Drives the stock scenarios of the datasets (truth/stock_scenarios.json, block 2) through the real services, step by
step on their pinned business dates, and reads the state the way the truth files count it."""

import csv
import json
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.approvals import workflow
from p2p.formulas.calendar import business_today, pin_today
from p2p.orders import changes, dispatch
from p2p.orders import service as orders
from p2p.orders import workflow as po_workflow
from p2p.platform import (
    demo,
    notifications,
    proposals,
    routing,
    setting_changes,
    setup_api,  # noqa: F401 - registers the setup change handlers
    users,
)
from p2p.platform.sod import user
from p2p.receiving import goods, quality, safety
from p2p.receiving.stock import StockError, post_count, post_issue
from p2p.requests import jobs, material, replenishment, reservation, shortfall
from p2p.sourcing import service as sourcing

ROOT = Path(__file__).resolve().parents[1]
DATASETS = {"apex": ROOT / "data" / "datasets" / "apex-v1", "brightwave": ROOT / "data" / "datasets" / "brightwave-v1"}
D = Decimal


def scenarios(name: str) -> list[dict[str, Any]]:
    return list(json.loads((DATASETS[name] / "truth" / "stock_scenarios.json").read_text(encoding="utf-8")))


def truth(name: str, kind: str) -> list[dict[str, str]]:
    with (DATASETS[name] / "truth" / f"truth_stock_{kind}.csv").open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def q(value: Any) -> str:
    text_ = f"{D(value).normalize():f}"
    return "0" if text_ in ("-0", "0E-4") else text_


@dataclass
class Run:
    """One company made fresh, and what the steps leave behind (the last proposal, a refused issue)."""

    name: str
    slug: str = field(default_factory=lambda: "sc" + uuid.uuid4().hex[:8])
    db: Session | None = None
    proposal: int | None = None
    refused: StockError | None = None

    @property
    def s(self) -> Session:
        assert self.db is not None
        return self.db

    def uid(self, code: str) -> int:
        email = self.s.execute(text("select email from org_user where code = :c"), {"c": code}).scalar()
        found = users.user_id_by_email(self.slug, str(email))
        assert found is not None, code
        return found

    def mr_of(self, order: str) -> str:
        return str(
            self.s.execute(
                text("select no from material_request where order_no = :o order by created_at desc limit 1"),
                {"o": order},
            ).scalar_one()
        )

    def request_of(self, order: str) -> str:
        return str(
            self.s.execute(
                text("select no from request where source_ref = :m order by created_at desc, no desc limit 1"),
                {"m": self.mr_of(order)},
            ).scalar_one()
        )

    def po_of(self, item: str) -> tuple[str, int]:
        row = self.s.execute(
            text(
                "select pl.po_no, pl.line_no from po_line pl join po on po.no = pl.po_no where pl.item_code = :i "
                "and po.status <> 'cancelled' order by po.created_at desc limit 1"
            ),
            {"i": item},
        ).one()
        return str(row[0]), int(row[1])

    # -- the steps ----------------------------------------------------------------------------------------------

    def step(self, st: dict[str, Any]) -> None:
        pin_today(date.fromisoformat(st["on"]))
        if self.db is not None:
            self.s.rollback()
        getattr(self, "_" + st["do"])(st["who"], st["args"])

    def _reset(self, who: str, args: dict[str, Any]) -> None:
        demo.reset_company(demo.company(self.name), DATASETS[self.name], credentials=None, slug=self.slug)
        self.db = Session(routing.engine_for(self.slug))

    def _store_check(self, who: str, args: dict[str, Any]) -> None:
        material.store_check(self.s, user(self.uid(who)), tenant=self.slug, submit=bool(args.get("submit", True)))

    def _job(self, who: str, args: dict[str, Any]) -> None:
        jobs.run_check(self.s, self.slug)

    def _approve_request(self, who: str, args: dict[str, Any]) -> None:
        no = self.request_of(args["mr_order_no"])
        for _ in range(6):
            self.s.rollback()
            step = self.s.execute(
                text(
                    "select coalesce(s.delegate, s.approver) from approval_step s join request r on r.no = s.request_no "
                    "where s.request_no = :n and s.status = 'pending' and s.round = r.approval_round"
                ),
                {"n": no},
            ).scalar()
            if step is None:
                break
            login = notifications.login_of(self.s, self.slug, step)
            assert login is not None
            workflow.approve(self.s, self.slug, no, login, "approved for the scenario")
        assert self.s.execute(text("select status from request where no = :n"), {"n": no}).scalar() == "approved"

    def _reject_request(self, who: str, args: dict[str, Any]) -> None:
        workflow.reject(self.s, self.request_of(args["mr_order_no"]), self.uid(who), args["reason"], tenant=self.slug)

    def _source(self, who: str, args: dict[str, Any]) -> None:
        today = _today()
        sourcing.sweep(self.s, self.slug, as_of=today, dispatch=False)
        orders.sweep(self.s, self.slug, as_of=today)
        self._send_approved()

    def _send_approved(self) -> None:
        for (po_no,) in self.s.execute(text("select no from po where status = 'approved' order by no")).all():
            dispatch.send_po(self.s, po_no, tenant=self.slug)

    def _issue_po(self, who: str, args: dict[str, Any]) -> None:
        po_no, _ = self.po_of(args["item_code"])
        for _ in range(6):
            self.s.rollback()
            status = self.s.execute(text("select status from po where no = :n"), {"n": po_no}).scalar()
            if status == "draft":
                po_workflow.submit(self.s, self.slug, po_no, user(self.uid(who)))
                continue
            if status != "submitted":
                break
            step = self.s.execute(
                text(
                    "select approver from po_approval_step where po_no = :n and status = 'pending' order by step limit 1"
                ),
                {"n": po_no},
            ).scalar()
            assert step is not None, po_no
            login = notifications.login_of(self.s, self.slug, step)
            assert login is not None
            po_workflow.approve(self.s, self.slug, po_no, login)
        self._send_approved()

    def _acknowledge(self, who: str, args: dict[str, Any]) -> None:
        po_no, _ = self.po_of(args["item_code"])
        dispatch.acknowledge(
            self.s, po_no, user(self.uid(who)), promised=date.fromisoformat(args["promised"]), note=None,
            tenant=self.slug,
        )  # fmt: skip

    def _count(self, who: str, args: dict[str, Any]) -> None:
        place = (args["item_code"], args["location"])
        post_count(self.s, self.uid(who), *place, D(args["counted"]), args.get("reason") or "count")
        reservation.stock_rose(self.s, self.slug, [place], why="count")

    def _receive(self, who: str, args: dict[str, Any]) -> None:
        po_no, line_no = self.po_of(args["item_code"])
        posted = goods.post_receipt(
            self.s, po_no, [goods.LineIn(line_no, D(args["qty"]))], goods.Header(challan="DC-1"), self.uid(who),
            as_of=_today(),
        )  # fmt: skip
        goods.reserve_arrived(self.s, self.slug, posted.no)

    def _close_balance(self, who: str, args: dict[str, Any]) -> None:
        po_no, _ = self.po_of(args["item_code"])
        changes.close_balance(self.s, self.slug, po_no, self.uid(who), args["reason"])

    def _propose_change(self, who: str, kind: str, args: dict[str, Any], reason: str) -> int:
        made = setting_changes.submit(self.s, user(self.uid(who)), kind, args, reason, tenant=None)
        return int(made["proposal_id"])

    def _set_setting(self, who: str, args: dict[str, Any]) -> None:
        pid = self._propose_change(who, "policy", {"key": args["key"], "value": args["value"]}, "scenario")
        proposals.approve(self.s, pid, self.uid(args["approved_by"]))

    def _set_item_planning(self, who: str, args: dict[str, Any]) -> None:
        keep = {k: v for k, v in args.items() if k not in ("reason", "approved_by")}
        pid = self._propose_change(who, "item_planning", keep, args["reason"])
        proposals.approve(self.s, pid, self.uid(args["approved_by"]))

    def _issue(self, who: str, args: dict[str, Any]) -> None:
        try:
            post_issue(
                self.s,
                self.uid(who),
                args["item_code"],
                args["location"],
                D(args["qty"]),
                reference=args.get("reference"),
            )
        except StockError as exc:
            self.refused = exc

    def _safety_override(self, who: str, args: dict[str, Any]) -> None:
        payload = dict(args)
        if args["kind"] == "material":
            mr = self.mr_of(args["mr_order_no"])
            line = self.s.execute(
                text("select line_no from material_request_line where mr_no = :m and item_code = :i"),
                {"m": mr, "i": args["item_code"]},
            ).scalar_one()
            payload = {"kind": "material", "mr_no": mr, "line_no": line, "qty": args["qty"], "reason": args["reason"]}
        self.proposal = safety.ask(self.s, self.slug, self.uid(who), payload).proposal_id

    def _approve_proposal(self, who: str, args: dict[str, Any]) -> None:
        assert self.proposal is not None
        proposals.approve(self.s, self.proposal, self.uid(who))

    def _replenish(self, who: str, args: dict[str, Any]) -> None:
        replenishment.run(self.s, as_of=_today(), tenant=self.slug, submit=True)

    def _decide_hold(self, who: str, args: dict[str, Any]) -> None:
        hold_id = self.s.execute(
            text("select id from stock_hold where import_id = :i"), {"i": args["hold_id"]}
        ).scalar_one()
        quality.decide(
            self.s, self.slug, self.uid(who), int(hold_id), D(args["to_usable"]), D(args["to_blocked"]), args["reason"]
        )

    def _hold(self, who: str, args: dict[str, Any]) -> None:
        quality.hold(
            self.s, self.slug, self.uid(who), args["item_code"], args["location"], D(args["qty"]), args["quality"],
            args["reason"],
        )  # fmt: skip

    # -- reading the state ---------------------------------------------------------------------------------------

    def lines(self) -> dict[tuple[str, str, str], dict[str, str]]:
        """(order, item, place) -> the figures of its open material request line, as the truth counts them."""
        self.s.rollback()
        rows = (
            self.s.execute(
                text(
                    "select c.id, m.order_no, c.mr_no, c.item_code, c.location_code, c.need_qty, c.issued_qty, c.reserved_qty, "
                    "c.requested_qty from material_request_cover c join material_request m on m.no = c.mr_no "
                    "where c.status = 'open'"
                )
            )
            .mappings()
            .all()
        )
        figured = shortfall.figures(self.s, sorted({r["mr_no"] for r in rows}))
        out = {}
        for r in rows:
            f = figured[r["id"]]
            c = f["cover"]
            out[(r["order_no"], r["item_code"], r["location_code"])] = {
                "need": q(r["need_qty"]), "issued": q(r["issued_qty"]), "reserved": q(r["reserved_qty"]),
                "timely": q(c.timely), "late": q(c.late), "at_risk": q(c.at_risk), "short": q(c.short),
                "open_request_qty": q(r["requested_qty"]), "causes": ";".join(f["causes"]),
            }  # fmt: skip
        return out

    def positions(self, places: list[tuple[str, str]]) -> dict[tuple[str, str], dict[str, str]]:
        self.s.rollback()
        found = shortfall.positions(self.s, places)
        return {
            p: {"usable": q(v["usable"]), "quarantine": q(v["quarantine"]), "blocked": q(v["blocked"]),
                "reserved": q(v["reserved"]), "safety_stock": q(v["safety"]),
                "available": q(v["usable"] - v["reserved"] - v["safety"])}
            for p, v in found.items()
        }  # fmt: skip

    def requests(self) -> dict[str, dict[str, Any]]:
        """Live requests: number -> order (or '' for a restock), lines item:base qty, value, priority."""
        self.s.rollback()
        out: dict[str, dict[str, Any]] = {}
        for r in self.s.execute(
            text(
                "select r.no, coalesce(m.order_no, '') as order_no, r.total_value, r.priority, r.status from request r "
                "left join material_request m on m.no = r.source_ref where r.status not in ('cancelled', 'rejected') "
                "order by r.created_at, r.no"
            )
        ).mappings():
            out[r["no"]] = dict(r)
        lines: dict[str, list[str]] = defaultdict(list)
        for no, item, qty in self.s.execute(
            text(
                "select request_no, item_code, coalesce(base_qty, qty) from request_line where status <> 'cancelled' "
                "order by request_no, line_no"
            )
        ):
            if no in out:
                lines[no].append(f"{item}:{q(qty)}")
        for no, r in out.items():
            r["lines"] = sorted(lines[no])
        return out


def _today() -> date:
    return business_today()


def drop(run: Run) -> None:
    from p2p.platform import tenants

    if run.db is not None:
        run.db.close()
    routing.clear_engine_cache()
    tenants.drop_tenant(run.slug)


__all__ = ["DATASETS", "Run", "drop", "q", "scenarios", "truth"]
