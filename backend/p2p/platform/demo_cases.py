"""Fill the main demo company (Apex Pumps) with cases at every stage of the flow, so a walk-through can start in the
middle.

    python -m p2p.cli demo populate apex          (after `demo reset apex`; `--reset` does both)

It works the way the people do: each step is a call to the real API as one of the role logins, and the worker runs the
jobs that follow, so every number, audit row, notification and email is the real thing. Nothing is written straight to
the database. The company must have no requests yet, and the database must be on this machine.

What it leaves behind (dates are relative to today):

    a quotation request from the stock check waiting to be sent,
    a draft, a request waiting for the plant head, one waiting for the director,
    a quotation request sent with quotes in and waiting for an award,
    an order made from an award and waiting for the purchase manager, an approved order waiting to be sent,
    a sent and acknowledged order at version 2 (its delivery terms changed: the versions can be compared), a
    part-received order, a fully received order waiting for an invoice,
    an invoice approved and not due yet, a blocked invoice waiting for release,
    a payment run waiting for the director, an approved run with its bank file made, and one case paid and closed,
    a period RFQ whose award waits for the director, a quotation request with a negotiation brief (block 6), and one
    that closed with too few quotes and is a case for the buyer.

Two spares are used up and left without any request, so "Check stock levels" still has something to suggest.
"""

import hashlib
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

import p2p.platform.demo as demo
from p2p.agents import autonomy
from p2p.formulas.calendar import business_today, pin_today, today_is_pinned
from p2p.main import app
from p2p.platform import audit, routing, tenants, worker
from p2p.requests import replenishment
from p2p.sourcing import closing

REQUESTER, STORE, BUYER = "Production Manager", "Storekeeper", "Buyer"
AP, FINANCE = "AP Executive", "Finance Manager"
PURCHASE_MANAGER, DIRECTOR = "Purchase Manager", "Director"
LOCATION = "PLT-CKN"
GAP = 14  # an order of the same item a fortnight apart is not taken for a repeat

# Stages in the order they are made; the key is the label shown in the report and checked by the test. The first
# group is what a walk-through of the start of the flow needs; the later stages are made after it and a failure there
# leaves the first group in place.
EARLY = (
    "stock check: quotation request waiting to be sent",
    "new request: draft",
    "new request: waiting for the plant head",
    "new request: waiting for the director",
    "quotation request sent, quotes in, waiting for an award",
)
LATER = (
    "order from an award, waiting for the purchase manager",
    "approved order, waiting to be sent",
    "order sent and acknowledged",
    "order part received",
    "order received, waiting for an invoice",
    "invoice approved, not due yet",
    "invoice blocked, waiting for release",
    "payment run waiting for the director",
    "payment run approved, bank file made",
    "paid and closed",
    "period RFQ: award waiting for the director",
    "quotation request with a negotiation brief",
    "quotation request closed short, a case for the buyer",
    "order short-closed, supply done and money open",
)
STAGES = EARLY + LATER


@dataclass
class Populated:
    slug: str
    cases: dict[str, str] = field(default_factory=dict)  # stage -> document number
    low_items_left: list[str] = field(default_factory=list)
    stopped: str | None = None  # why the later stages were not all made (the early ones are in place)


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class _Run:
    def __init__(self, slug: str) -> None:
        self.slug = slug
        self.client = TestClient(app)
        self.db = Session(routing.engine_for(slug))
        self.today = business_today()
        self._tokens: dict[str, dict[str, str]] = {}
        people = self.db.execute(text("select code, designation, email from org_user")).all()
        self._email: dict[str, str] = {r[1]: r[2] for r in people}
        self._designation: dict[str, str] = {r[0]: r[1] for r in people}
        self._invoice_seq = 0
        self._used: dict[str, int] = {}
        self.out = Populated(slug)

    # ---- plumbing -------------------------------------------------------------------------------------

    def who(self, designation: str) -> dict[str, str]:
        if designation not in self._tokens:
            email = self._email.get(designation)
            if email is None:
                raise demo.DemoError(f"the company has nobody with the designation '{designation}'")
            r = self.client.post("/api/auth/login", json={"email": email, "password": demo.DEV_PASSWORD})
            if r.status_code != 200:
                raise demo.DemoError(f"{email} cannot sign in with the demo password (run `demo reset` first)")
            self._tokens[designation] = {"Authorization": "Bearer " + r.json()["access_token"]}
        return self._tokens[designation]

    def call(
        self, designation: str, method: str, path: str, body: dict[str, Any] | None = None, *, expect: int = 200
    ) -> Any:
        self.db.rollback()
        r = self.client.request(method, path, json=body, headers=self.who(designation))
        self.db.rollback()
        if r.status_code != expect:
            raise demo.DemoError(f"{designation}: {method} {path} gave {r.status_code}: {r.text[:200]}")
        return r.json()

    def q(self, sql: str, **params: Any) -> list[Any]:
        self.db.rollback()
        return list(self.db.execute(text(sql), params).all())

    def work(self) -> None:
        """Let the worker run this company's jobs until none are left (sourcing, ordering, emails)."""
        self.db.commit()
        for _ in range(80):
            if not worker.run_once(tenant=self.slug):
                break
        self.db.rollback()

    def day(self, plus: int = 0) -> str:
        return (self.today + timedelta(days=plus)).isoformat()

    # ---- requests -------------------------------------------------------------------------------------

    def request(self, lines: list[tuple[str, int]], why: str, *, submit: bool = True, gap: int = 0) -> str:
        made = self.call(
            REQUESTER,
            "POST",
            "/api/requisitions",
            {
                "lines": [{"item_code": code, "qty": str(qty)} for code, qty in lines],
                "need_by": self.day(7 + gap * GAP),
                "location": LOCATION,
                "reason": why,
            },
        )
        no = str(made["id"])
        if submit:
            self.call(REQUESTER, "POST", f"/api/requisitions/{no}/submit", {"override_reason": "A separate job"})
        return no

    def waiting_on(self, no: str) -> str | None:
        """The designation of whoever the request waits for, or None."""
        waiting = self.q(
            "select approver from approval_step where request_no = :n and status = 'pending' order by step limit 1",
            n=no,
        )
        return self._designation[waiting[0][0]] if waiting else None

    def approve_request(self, no: str) -> None:
        """Whoever the rules name for it approves (nothing to do when it approved itself)."""
        while (who := self.waiting_on(no)) is not None:
            self.call(who, "POST", f"/api/requisitions/{no}/approve", {})

    def orders_of(self, request_no: str) -> list[str]:
        return [
            r[0] for r in self.q("select distinct po_no from po_line where request_no = :r order by 1", r=request_no)
        ]

    def contract_order(self, item: str, qty: int, why: str) -> tuple[str, str]:
        """A request for a contract item, approved, and the order the worker makes from the contract price. A later
        request for an item already used is dated a fortnight further on, so it is not taken for a repeat."""
        self._used[item] = self._used.get(item, -1) + 1
        no = self.request([(item, qty)], why, gap=self._used[item])
        self.approve_request(no)
        self.work()
        orders = self.orders_of(no)
        if len(orders) != 1:
            raise demo.DemoError(f"request {no} for {item} should give one order from the contract, gave {orders}")
        return no, orders[0]

    # ---- quotation requests ---------------------------------------------------------------------------

    def rfq_of(self, request_no: str) -> tuple[str, int | None]:
        rows = self.q("select no, proposal_id from rfq where request_no = :r order by no", r=request_no)
        if len(rows) != 1:
            raise demo.DemoError(f"request {request_no} should have one quotation request, has {len(rows)}")
        return str(rows[0][0]), rows[0][1]

    def send_rfq(self, rfq: str, proposal: int | None) -> None:
        if proposal is None:
            raise demo.DemoError(f"{rfq} has no 'send' decision waiting")
        self.call(BUYER, "POST", f"/api/admin/proposals/{proposal}/approve", {})
        self.work()

    def invited(self, rfq: str) -> list[str]:
        return [r[0] for r in self.q("select supplier_code from rfq_supplier where rfq_no = :n order by 1", n=rfq)]

    def quote(self, rfq: str, supplier: str, prices: dict[int, str], gst: int) -> None:
        self.call(
            BUYER,
            "POST",
            f"/api/rfqs/{rfq}/quotes",
            {
                "supplier_code": supplier,
                "valid_until": self.day(30),
                "freight": "0",
                "lines": [
                    {
                        "rfq_line_no": n,
                        "unit_price": price,
                        "discount_pct": "0",
                        "gst_rate": str(gst),
                        "delivery_days": 7,
                    }
                    for n, price in prices.items()
                ],
            },
        )

    # ---- orders ---------------------------------------------------------------------------------------

    def approve_order(self, po: str) -> None:
        """The purchase manager approves an order that waits; one within the limit for no approval is approved already."""
        if self.q("select status from po where no = :n", n=po)[0][0] == "submitted":
            self.call(PURCHASE_MANAGER, "POST", f"/api/purchase-orders/{po}/approve", {"comment": "Approved"})

    def send_order(self, po: str, *, acknowledge: bool = True) -> None:
        self.call(BUYER, "POST", f"/api/purchase-orders/{po}/send")
        if acknowledge:
            self.acknowledge(po)

    def acknowledge(self, po: str) -> None:
        """The buyer records the supplier's acknowledgement of the version it was sent, by phone: each line on the date
        ordered (a date per line, block 8)."""
        version = int(self.q("select version from po where no = :n", n=po)[0][0])
        lines = {
            str(n): (promised or need_by).isoformat()
            for n, promised, need_by in self.q(
                "select line_no, promised_date, need_by from po_line where po_no = :n and status = 'open' "
                "and coalesce(promised_date, need_by) is not null order by line_no",
                n=po,
            )
        }
        self.call(
            BUYER, "POST", f"/api/purchase-orders/{po}/acknowledge",
            {"version": version, "lines": lines, "note": "Confirmed by phone with the supplier's sales desk"},
        )  # fmt: skip

    def receive(self, po: str, fraction: Decimal = Decimal(1)) -> None:
        lines = self.q("select line_no, qty from po_line where po_no = :n order by line_no", n=po)
        self.call(
            STORE,
            "POST",
            f"/api/purchase-orders/{po}/receive",
            {
                "challan": f"DC-{po[-5:]}",
                "vehicle": "MH12AB1234",
                "lines": [
                    {"line_no": ln[0], "qty_received": str((Decimal(ln[1]) * fraction).to_integral_value())}
                    for ln in lines
                ],
            },
        )

    def received_order(self, item: str, qty: int, why: str) -> str:
        _, po = self.contract_order(item, qty, why)
        self.approve_order(po)
        self.send_order(po)
        self.receive(po)
        return po

    # ---- invoices and payment ---------------------------------------------------------------------------

    def invoice(self, po: str, *, price_factor: Decimal = Decimal(1)) -> dict[str, Any]:
        head = self.q("select supplier_code from po where no = :n", n=po)[0]
        supplier = self.q("select gstin, e_invoice_applicable from supplier where code = :c", c=head[0])[0]
        line = self.q("select line_no, qty, unit_price from po_line where po_no = :n order by line_no limit 1", n=po)[0]
        buyer_gstin = self.q("select gstin from gst_registration limit 1")[0][0]
        self._invoice_seq += 1
        number = f"INV-{self.today:%y%m}-{self._invoice_seq:03d}"
        body: dict[str, Any] = {
            "supplier_code": head[0],
            "number": number,
            "date": self.today.isoformat(),
            "supplier_gstin": supplier[0],
            "buyer_gstin": buyer_gstin,
            "lines": [
                {
                    "po_no": po,
                    "po_line_no": line[0],
                    "qty": str(line[1]),
                    "unit_price": str(_money(Decimal(line[2]) * price_factor)),
                }
            ],
        }
        if supplier[1]:
            body["irn"] = hashlib.sha256(f"{head[0]}|{number}".encode()).hexdigest().upper()
        return dict(self.call(AP, "POST", "/api/invoices", body))

    def payment_run(self) -> str:
        """Finance plans a run of what is due, in the window that takes the one invoice waiting."""
        made = self.call(FINANCE, "POST", "/api/payment-runs", {"run_date": self.day(), "window_days": 120})
        return str(made.get("run") or made.get("id") or made.get("no"))

    def approve_run(self, run: str) -> None:
        proposal = self.q("select proposal_id from payment_run where no = :r", r=run)[0][0]
        self.call(DIRECTOR, "POST", f"/api/admin/proposals/{proposal}/approve", {})


def _stage(run: _Run, label: str, number: str) -> None:
    run.out.cases[label] = number


def populate(slug: str = demo.MAIN) -> Populated:
    """Make the cases. Raises DemoError when it is not safe to, or when the first group of stages cannot be made; a
    failure in the later stages is reported in `stopped` and leaves everything made so far."""
    demo.require_local_database()
    with tenants.control_session() as control:
        if control.execute(text("select 1 from tenant where slug = :s"), {"s": slug}).first() is None:
            raise demo.DemoError(f"there is no company '{slug}'; make it with `demo reset` first")
    with Session(routing.engine_for(slug)) as check:
        if check.execute(text("select count(*) from request")).scalar_one():
            raise demo.DemoError(f"'{slug}' already has requests; use `demo populate {slug} --reset` to start over")

    run = _Run(slug)
    # Touchless orders (po.issue) would send the contract orders before they reach the stages named above, so they
    # are off while the cases are made and set back as the company had them afterwards.
    level = run.db.execute(text("select level from autonomy_setting where decision_type = 'po.issue'")).scalar()
    autonomy.configure(run.db, audit.SYSTEM, "po.issue", level="propose")
    try:
        _early(run)
        try:
            _later(run)
        except Exception as exc:  # noqa: BLE001 - the early stages stand; the report says where the rest stopped
            run.out.stopped = f"{type(exc).__name__}: {exc}"
        run.work()
        from p2p.invoices import price_measures  # noqa: PLC0415 - block 13's measures for the cases just made

        price_measures.record(run.db, business_today())
        run.out.low_items_left = _still_low(run)
    finally:
        run.db.rollback()
        autonomy.configure(run.db, audit.SYSTEM, "po.issue", level=str(level or "propose"))
        run.db.close()
    return run.out


def _early(run: _Run) -> None:
    """Stock check, new requests, approvals and quotation requests."""
    # "Check stock levels": the buyer's click. The V-belts are below their reorder point; the request is small and
    # approves itself, and its quotation request waits for a buyer to send it.
    made = run.call(BUYER, "POST", "/api/replenishment/run", {"dry_run": False})["requests"]
    if [r["category"] for r in made] != ["MRO"]:
        raise demo.DemoError(f"the stock check should make one request for spares (MRO), made {made}")
    run.work()
    belts_rfq, _ = run.rfq_of(made[0]["no"])
    _stage(run, EARLY[0], belts_rfq)

    # Two spares people have used up since, left alone so "Check stock levels" still has something to suggest.
    for item in ("SP-02", "SP-03"):
        run.call(
            STORE,
            "POST",
            "/api/inventory/issue",
            {"item_code": item, "location": LOCATION, "qty": "22", "reference": "Used on the line"},
        )

    # New requests by the production manager: one kept as a draft, one for each approver
    _stage(run, EARLY[1], run.request([("RM-06", 100)], "Bearings for the 1 HP pumps", submit=False))
    _stage(run, EARLY[2], run.request([("RM-08", 20)], "Winding wire for the rewinding job"))
    _stage(run, EARLY[3], run.request([("RM-01", 200)], "Motor bodies for the next quarter"))

    # A small request approves itself; its quotation request is sent, suppliers answer, nobody has awarded it yet
    no = run.request([("RM-09", 50)], "Capacitors for the 1 HP pumps")
    run.work()
    rfq, proposal = run.rfq_of(no)
    run.send_rfq(rfq, proposal)
    for supplier, price in zip(run.invited(rfq), ("86", "84", "88"), strict=False):
        run.quote(rfq, supplier, {1: price}, 18)
    _stage(run, EARLY[4], rfq)


def _later(run: _Run) -> None:
    # Copper wire: a large request the director approves, a quotation request sent, quotes from the suppliers asked,
    # one awarded; the order is above the purchase manager's threshold and waits for her
    no = run.request([("RM-08", 120)], "Winding wire for the December production")
    run.approve_request(no)
    run.work()
    rfq, proposal = run.rfq_of(no)
    run.send_rfq(rfq, proposal)
    asked = run.invited(rfq)
    for supplier, price in zip(asked, ("925", "910", "918"), strict=False):
        run.quote(rfq, supplier, {1: price}, 18)
    run.call(BUYER, "POST", f"/api/rfqs/{rfq}/award", {"choices": {"1": asked[1]}})
    run.work()
    orders = run.orders_of(no)
    if len(orders) != 1:
        raise demo.DemoError(f"the awarded quotation request should give one order, gave {orders}")
    _stage(run, LATER[0], orders[0])

    # Orders on the castings rate contract, each one step further along (small enough to need no approval)
    _, po = run.contract_order("RM-05", 10, "Shafts for the 0.5 HP pumps")
    run.approve_order(po)
    _stage(run, LATER[1], po)

    _, po = run.contract_order("RM-03", 20, "Impellers for the 1 HP pumps")
    run.approve_order(po)
    run.send_order(po)
    # the plant collects from the foundry: a minor change, so the approval carries and the supplier answers version 2
    run.call(
        BUYER, "POST", f"/api/purchase-orders/{po}/revise",
        {"reason": "We collect from the foundry this time", "version": 1, "header": {"delivery_terms": "ex_works"}},
    )  # fmt: skip
    run.acknowledge(po)  # version 2
    _stage(run, LATER[2], po)

    _, po = run.contract_order("RM-04", 20, "Impellers for the 0.5 HP pumps")
    run.approve_order(po)
    run.send_order(po)
    run.receive(po, Decimal("0.5"))
    _stage(run, LATER[3], po)

    po = run.received_order("RM-05", 10, "Shafts for the spares stock")
    _stage(run, LATER[4], po)

    # Three invoices, each planned into its own payment run straight away, so each run holds one
    po = run.received_order("RM-03", 10, "Impellers for a customer's spares")
    first = run.invoice(po)
    _expect(first, "approved", "the first invoice")
    paid_run = run.payment_run()
    run.approve_run(paid_run)
    run.call(FINANCE, "POST", f"/api/payment-runs/{paid_run}/file")
    run.call(FINANCE, "POST", f"/api/payment-runs/{paid_run}/paid", {"paid_on": run.day()})
    run.work()
    _stage(run, LATER[9], first["invoice"])

    # Block 13: a part-delivered contract order whose balance the buyer closes below the limit; supply is complete and
    # the money is still open, so the order waits for its invoice and settlement
    _, po = run.contract_order("RM-05", 10, "Shafts for the January service kits")
    run.approve_order(po)
    run.send_order(po)
    run.receive(po, Decimal("0.5"))
    run.call(BUYER, "POST", f"/api/purchase-orders/{po}/close-balance",
             {"reason": "The supplier cannot make the rest this quarter"})  # fmt: skip
    _stage(run, LATER[13], po)

    po = run.received_order("RM-04", 10, "Impellers for the service centre")
    _expect(run.invoice(po), "approved", "the invoice of the second run")
    filed_run = run.payment_run()
    run.approve_run(filed_run)
    run.call(FINANCE, "POST", f"/api/payment-runs/{filed_run}/file")
    _stage(run, LATER[8], filed_run)

    po = run.received_order("RM-05", 5, "Shafts for the test bench")
    _expect(run.invoice(po), "approved", "the invoice of the third run")
    _stage(run, LATER[7], run.payment_run())

    # An invoice that is fine and not due yet, and one billed 12% above the order (beyond the tolerances), which is blocked
    po = run.received_order("RM-03", 5, "Impellers for the stores")
    good = run.invoice(po)
    _expect(good, "approved", "the invoice that is not due yet")
    _stage(run, LATER[5], good["invoice"])

    po = run.received_order("RM-04", 50, "Impellers for the rework")
    blocked = run.invoice(po, price_factor=Decimal("1.12"))
    if blocked.get("status") != "blocked":
        raise demo.DemoError(f"the invoice billed above the order should be blocked, is {blocked.get('status')}")
    reason = "The supplier's price rose and the stock is needed"
    run.call(AP, "POST", f"/api/invoices/{blocked['invoice']}/release", {"reason": reason})
    _stage(run, LATER[6], blocked["invoice"])
    _period(run)
    _quotes(run)


def _period(run: _Run) -> None:
    """Strategic sourcing (block 5): copper wire for next quarter priced with no request, three quotes in, the award
    proposed by the buyer and waiting for the director (it is above Rs 5 lakh with GST)."""
    start = (run.today.replace(day=1) + timedelta(days=62)).replace(day=1)  # the first day of the month after next
    end = (start + timedelta(days=92)).replace(day=1) - timedelta(days=1)  # three calendar months
    made = run.call(
        BUYER, "POST", "/api/rfqs/period",
        {"location_code": LOCATION, "period_from": start.isoformat(), "period_to": end.isoformat(),
         "lines": [{"item_code": "RM-08", "qty": "900"}], "note": "Copper wire for the next quarter"},
        expect=201,
    )  # fmt: skip
    rfq = str(made["rfq_no"])
    run.call(BUYER, "POST", "/api/rfqs/confirm-send", {"rfqs": [rfq]})
    for supplier, price in zip(made["invited"], ("895", "880", "910"), strict=False):
        run.quote(rfq, supplier, {1: price}, 18)
    run.call(BUYER, "POST", f"/api/rfqs/{rfq}/award", {"choices": {"1": made["invited"][1]}}, expect=202)
    _stage(run, LATER[10], rfq)


def _quotes(run: _Run) -> None:
    """Block 6: copper wire quoted above the last price paid (a negotiation brief for the buyer), and a quotation
    request sent a fortnight ago that closed with one quote of three (the morning job's case for the buyer)."""
    no = run.request([("RM-08", 500)], "Winding wire for the January production", gap=2)
    run.approve_request(no)
    run.work()
    rfq, proposal = run.rfq_of(no)
    run.send_rfq(rfq, proposal)
    prices = {"SUP-005": "990", "SUP-003": "1005", "SUP-001": "1020"}  # above the 910 paid earlier in this run
    for supplier in run.invited(rfq):
        run.quote(rfq, supplier, {1: prices.get(supplier, "1025")}, 18)
    if not run.q("select 1 from recommendation where kind = 'negotiation_brief' and business_key = :k "
                 "and status = 'proposed'", k=f"{rfq}/1"):  # fmt: skip
        raise demo.DemoError(f"{rfq} should have a negotiation brief")
    _stage(run, LATER[11], rfq)

    # A request sourced and sent two weeks ago (the business date set back while it is made), one quote in by its date
    was = business_today() if today_is_pinned() else None
    pin_today(run.today - timedelta(days=14))
    try:
        short = run.request([("RM-08", 100)], "Winding wire for the export order", gap=3)
        run.approve_request(short)
        run.work()
        rfq, proposal = run.rfq_of(short)
        run.send_rfq(rfq, proposal)
        run.quote(rfq, run.invited(rfq)[0], {1: "915"}, 18)
    finally:
        pin_today(was)
    with Session(routing.engine_for(run.slug)) as db:
        closing.run(db, run.slug, business_today())  # what the morning reminder job does
    if not run.q("select 1 from task where kind = 'rfq_no_response' and payload->>'rfq_no' = :n", n=rfq):
        raise demo.DemoError(f"{rfq} should have closed short with a case for the buyer")
    _stage(run, LATER[12], rfq)


def _expect(result: dict[str, Any], status: str, what: str) -> None:
    if result.get("status") != status:
        raise demo.DemoError(f"{what} should be {status}, is {result.get('status')}: {result}")


def _still_low(run: _Run) -> list[str]:
    """Items at or below their reorder point with nothing already on the way: what 'Check stock levels' would raise."""
    run.db.rollback()
    planned, _ = replenishment.plan(run.db, run.today)
    return sorted({line.item_code for req in planned for line in req.lines if line.item_code})
