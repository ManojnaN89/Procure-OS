"""Fill the small demo company with cases at every stage of the flow, so a walk-through can start in the middle.

    python -m p2p.cli demo populate flow          (after `demo reset flow`; `--reset` does both)

It works the way the people do: each step is a call to the real API as one of the eight role logins, and the worker runs
the jobs that follow, so every number, audit row, notification and email is the real thing. Nothing is written straight
to the database. The company must have no requests yet, and the database must be on this machine.

What it leaves behind (dates are relative to today):

    a draft, a request waiting for the maintenance manager, one waiting for the plant head,
    a quotation request waiting to be sent, one sent with quotes in and waiting for an award,
    an order made from an award and waiting for the plant head, an approved order waiting to be sent,
    a sent and acknowledged order, a part-received order, a fully received order waiting for an invoice,
    an invoice approved and not due yet, a blocked invoice waiting for release,
    a payment run waiting for approval, an approved run with its bank file made, and one case paid and closed.

Two low items are left without any request, so "Check stock levels" still has something to suggest.
"""

import hashlib
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

import p2p.platform.demo as flow_demo
from p2p.formulas.calendar import business_today
from p2p.main import app
from p2p.platform import routing, tenants, worker
from p2p.requests import replenishment

PLANT_HEAD, MAINT_MGR, ENGINEER = "Plant Head", "Maintenance Manager", "Maintenance Engineer"
STORE, BUYER, AP, FINANCE = "Storekeeper", "Buyer", "Accounts Payable Executive", "Finance Manager"
LOCATION = "PLT-CKN"
GAP = 14  # an order of the same item a fortnight apart is not taken for a repeat

# Stages in the order they are made; the key is the label shown in the report and checked by the test. The first
# group is what a walk-through of the start of the flow needs; the later stages are made after it and a failure there
# leaves the first group in place.
EARLY = (
    "stock check: quotation request waiting to be sent",
    "stock check: requests waiting for the maintenance manager",
    "new request: draft",
    "new request: waiting for the maintenance manager",
    "new request: waiting for the plant head",
    "quotation request sent, quotes in, waiting for an award",
)
LATER = (
    "order from an award, waiting for the plant head",
    "approved order, waiting to be sent",
    "order sent and acknowledged",
    "order part received",
    "order received, waiting for an invoice",
    "invoice approved, not due yet",
    "invoice blocked, waiting for release",
    "payment run waiting for approval",
    "payment run approved, bank file made",
    "paid and closed",
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
        self._email: dict[str, str] = {
            r[0]: r[1] for r in self.db.execute(text("select designation, email from org_user"))
        }
        self._invoice_seq = 0
        self._used: dict[str, int] = {}
        self.out = Populated(slug)

    # ---- plumbing -------------------------------------------------------------------------------------

    def who(self, designation: str) -> dict[str, str]:
        if designation not in self._tokens:
            email = self._email.get(designation)
            if email is None:
                raise flow_demo.DemoError(f"the company has nobody with the designation '{designation}'")
            r = self.client.post("/api/auth/login", json={"email": email, "password": flow_demo.DEV_PASSWORD})
            if r.status_code != 200:
                raise flow_demo.DemoError(
                    f"{email} cannot sign in with the demo password (run `demo reset flow` first)"
                )
            self._tokens[designation] = {"Authorization": "Bearer " + r.json()["access_token"]}
        return self._tokens[designation]

    def call(self, designation: str, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        self.db.rollback()
        r = self.client.request(method, path, json=body, headers=self.who(designation))
        self.db.rollback()
        if r.status_code != 200:
            raise flow_demo.DemoError(f"{designation}: {method} {path} gave {r.status_code}: {r.text[:200]}")
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
            ENGINEER,
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
            self.call(ENGINEER, "POST", f"/api/requisitions/{no}/submit", {"override_reason": "A separate job"})
        return no

    def approve_request(self, no: str) -> None:
        """Whoever the rules name for it approves (nothing to do when it approved itself)."""
        waiting = self.q(
            "select approver from approval_step where request_no = :n and status = 'pending' order by step limit 1",
            n=no,
        )
        while waiting:
            who = MAINT_MGR if waiting[0][0] == "U02" else PLANT_HEAD
            self.call(who, "POST", f"/api/requisitions/{no}/approve", {})
            waiting = self.q(
                "select approver from approval_step where request_no = :n and status = 'pending' order by step limit 1",
                n=no,
            )

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
            raise flow_demo.DemoError(f"request {no} for {item} should give one order from the contract, gave {orders}")
        return no, orders[0]

    # ---- quotation requests ---------------------------------------------------------------------------

    def rfq_of(self, request_no: str) -> tuple[str, int | None]:
        rows = self.q("select no, proposal_id from rfq where request_no = :r order by no", r=request_no)
        if len(rows) != 1:
            raise flow_demo.DemoError(f"request {request_no} should have one quotation request, has {len(rows)}")
        return str(rows[0][0]), rows[0][1]

    def send_rfq(self, rfq: str, proposal: int | None) -> None:
        if proposal is None:
            raise flow_demo.DemoError(f"{rfq} has no 'send' decision waiting")
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
        self.call(PLANT_HEAD, "POST", f"/api/purchase-orders/{po}/approve", {"comment": "Approved"})

    def send_order(self, po: str, *, acknowledge: bool = True) -> None:
        self.call(BUYER, "POST", f"/api/purchase-orders/{po}/send")
        if acknowledge:
            self.call(BUYER, "POST", f"/api/purchase-orders/{po}/acknowledge", {"promised_date": self.day(3)})

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
        self.call(PLANT_HEAD, "POST", f"/api/admin/proposals/{proposal}/approve", {})


def _stage(run: _Run, label: str, number: str) -> None:
    run.out.cases[label] = number


def populate(slug: str = flow_demo.SLUG) -> Populated:
    """Make the cases. Raises DemoError when it is not safe to, or when the first group of stages cannot be made; a
    failure in the later stages is reported in `stopped` and leaves everything made so far."""
    flow_demo.require_local_database()
    with tenants.control_session() as control:
        if control.execute(text("select 1 from tenant where slug = :s"), {"s": slug}).first() is None:
            raise flow_demo.DemoError(f"there is no company '{slug}'; make it with `demo reset flow` first")
    with Session(routing.engine_for(slug)) as check:
        if check.execute(text("select count(*) from request")).scalar_one():
            raise flow_demo.DemoError(
                f"'{slug}' already has requests; use `demo populate {slug} --reset` to start over"
            )

    run = _Run(slug)
    try:
        _early(run)
        try:
            _later(run)
        except Exception as exc:  # noqa: BLE001 - the early stages stand; the report says where the rest stopped
            run.out.stopped = f"{type(exc).__name__}: {exc}"
        run.work()
        run.out.low_items_left = _still_low(run)
    finally:
        run.db.close()
    return run.out


def _early(run: _Run) -> None:
    """Stock check, new requests, approvals and quotation requests."""
    # "Check stock levels": the buyer's click. Four items are low; the shoes request is small and approves itself,
    # the other two wait for the maintenance manager.
    made = run.call(BUYER, "POST", "/api/replenishment/run", {"dry_run": False})["requests"]
    by_category = {r["category"]: r for r in made}
    if sorted(by_category) != ["ELE", "MRO", "PPE"]:
        raise flow_demo.DemoError(
            f"the stock check should make requests for ELE, MRO and PPE, made {sorted(by_category)}"
        )
    run.work()  # the approved shoes request is sourced: a quotation request that waits for a buyer to send it
    shoes_rfq, _ = run.rfq_of(by_category["PPE"]["no"])
    _stage(run, EARLY[0], shoes_rfq)
    _stage(run, EARLY[1], ", ".join(by_category[c]["no"] for c in ("MRO", "ELE")))

    # Two items people have used up since, left alone so "Check stock levels" still has something to suggest.
    for item in ("ITM-002", "ITM-010"):
        run.call(
            STORE,
            "POST",
            "/api/inventory/issue",
            {"item_code": item, "location": LOCATION, "qty": "22", "reference": "Used on the line"},
        )

    # New requests by the engineer: one kept as a draft, one for each approver
    _stage(run, EARLY[2], run.request([("ITM-007", 40)], "Breakers for the new line", submit=False))
    _stage(run, EARLY[3], run.request([("ITM-003", 50)], "Hose for the press line"))
    _stage(run, EARLY[4], run.request([("ITM-005", 30)], "Grease for the annual service"))

    # A small request approves itself; its quotation request is sent, two suppliers answer, nobody has awarded it yet
    no = run.request([("ITM-009", 20)], "Push buttons for the new panel")
    run.work()
    rfq, proposal = run.rfq_of(no)
    run.send_rfq(rfq, proposal)
    for supplier, price in zip(run.invited(rfq), ("176", "171", "174"), strict=False):
        run.quote(rfq, supplier, {1: price}, 18)
    _stage(run, EARLY[5], rfq)


def _later(run: _Run) -> None:
    # Gloves: a small request, a quotation request sent, quotes from the suppliers asked, the cheapest awarded; the order
    # waits for the plant head
    no = run.request([("ITM-012", 10)], "Gloves for the shift")
    run.work()
    rfq, proposal = run.rfq_of(no)
    run.send_rfq(rfq, proposal)
    asked = run.invited(rfq)
    for supplier, price in zip(asked, ("216", "209", "213"), strict=False):
        run.quote(rfq, supplier, {1: price}, 5)
    run.call(BUYER, "POST", f"/api/rfqs/{rfq}/award", {"choices": {"1": asked[1]}})
    run.work()
    orders = run.orders_of(no)
    if len(orders) != 1:
        raise flow_demo.DemoError(f"the awarded quotation request should give one order, gave {orders}")
    _stage(run, LATER[0], orders[0])

    # Orders on the contract price, each one step further along
    _, po = run.contract_order("ITM-008", 2, "Cable for the new motor")
    run.approve_order(po)
    _stage(run, LATER[1], po)

    _, po = run.contract_order("ITM-007", 20, "Breakers for the spare panels")
    run.approve_order(po)
    run.send_order(po)
    _stage(run, LATER[2], po)

    _, po = run.contract_order("ITM-006", 4, "Contactors for the compressor")
    run.approve_order(po)
    run.send_order(po)
    run.receive(po, Decimal("0.5"))
    _stage(run, LATER[3], po)

    po = run.received_order("ITM-008", 2, "Cable for the control room")
    _stage(run, LATER[4], po)

    # Three invoices, each planned into its own payment run straight away, so each run holds one
    po = run.received_order("ITM-007", 6, "Breakers for the packing line")
    first = run.invoice(po)
    _expect(first, "approved", "the first invoice")
    paid_run = run.payment_run()
    run.approve_run(paid_run)
    run.call(FINANCE, "POST", f"/api/payment-runs/{paid_run}/file")
    run.call(FINANCE, "POST", f"/api/payment-runs/{paid_run}/paid", {"paid_on": run.day()})
    run.work()
    _stage(run, LATER[9], first["invoice"])

    po = run.received_order("ITM-006", 2, "Contactors for the lift")
    _expect(run.invoice(po), "approved", "the invoice of the second run")
    filed_run = run.payment_run()
    run.approve_run(filed_run)
    run.call(FINANCE, "POST", f"/api/payment-runs/{filed_run}/file")
    _stage(run, LATER[8], filed_run)

    po = run.received_order("ITM-008", 2, "Cable for the workshop")
    _expect(run.invoice(po), "approved", "the invoice of the third run")
    _stage(run, LATER[7], run.payment_run())

    # An invoice that is fine and not due yet, and one billed 12% above the order, which is blocked
    po = run.received_order("ITM-007", 6, "Breakers for the stores")
    good = run.invoice(po)
    _expect(good, "approved", "the invoice that is not due yet")
    _stage(run, LATER[5], good["invoice"])

    po = run.received_order("ITM-006", 2, "Contactors for the crane")
    blocked = run.invoice(po, price_factor=Decimal("1.12"))
    if blocked.get("status") != "blocked":
        raise flow_demo.DemoError(f"the invoice billed above the order should be blocked, is {blocked.get('status')}")
    reason = "The supplier's price rose and the stock is needed"
    run.call(AP, "POST", f"/api/invoices/{blocked['invoice']}/release", {"reason": reason})
    _stage(run, LATER[6], blocked["invoice"])


def _expect(result: dict[str, Any], status: str, what: str) -> None:
    if result.get("status") != status:
        raise flow_demo.DemoError(f"{what} should be {status}, is {result.get('status')}: {result}")


def _still_low(run: _Run) -> list[str]:
    """Items at or below their reorder point with nothing already on the way: what 'Check stock levels' would raise."""
    run.db.rollback()
    planned, _ = replenishment.plan(run.db, run.today)
    return sorted({line.item_code for req in planned for line in req.lines if line.item_code})
