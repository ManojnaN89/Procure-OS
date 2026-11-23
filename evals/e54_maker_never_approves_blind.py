"""E54: a maker of a purchase order never approves it, by any route (C1, D43), written without the implementation.

Independence: this eval was written from the rule as the product owner states it, the plan (revision s5, C1, D43; the
baseline's DF-06, DF-08, DF-34, AT-03), the role guides, the public API and the dataset of the main demo company only. It
never reads the platform's code or tests; the expected outcome of every case is computed here from `approval_rule.csv`,
`user.csv`, `item.csv`, `supplier.csv` and the company profile.

The rule: the makers of an order are its buyer, whoever submitted it, revised it, awarded the quotation it came from or
changed the buying route of its lines. None of them approves the order, accepts its price above the estimate or accepts
a supplier's price rise on it: not on the screen, not through a proposal, not through an emailed link, not as someone's
deputy. When the person the band names is a maker, the order goes to the next band up; when nobody is left it is refused
with a reason. Only the touchless policy approves an order with no person. Someone who only accepted a price (a checker)
is not a maker. A refused attempt leaves the decision waiting for the right person.

The cases are built through the API as the demo logins; three people get a second job through the two-administrator
role change: the purchase manager and the director also buy, the buyer is also an approver (so the buyer's refusal is
the maker rule and not a missing permission).
"""

import re
import tempfile
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

import env
import lib
import yaml
from lib import Outcome

ID = "E54"
TITLE = "A maker of a purchase order never approves it, by any route (C1), written without the implementation"
REQUIREMENT = (
    "C1, D43, AT-03: the buyer, submitter, reviser, awarder and route changer of an order never approve it, accept its "
    "price above the estimate or accept a supplier's price rise on it (screen, proposal, link, deputy); all makers named "
    "means the next band; no band left means refused with a reason; only the touchless policy approves with no person; "
    "a checker is not a maker; a refused attempt leaves it waiting for the right person"
)

TODAY = date(2026, 11, 6)
NEED_BY = "2026-12-15"
CENT = Decimal("0.01")
REFUSED = "refused"  # nobody may approve: the order is refused with a reason
POLICY = "policy"  # the touchless policy approves it, no person


def money(value: Decimal | int | str) -> Decimal:
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


# ---- the oracle: written from the rule and the raw files only ---------------------------------------------------


class Oracle:
    """Who may approve an order, from the dataset's rule rows, people and the company profile."""

    def __init__(self, dataset: Path, profile: dict[str, Any]) -> None:
        self.rules = lib.rows("master", "approval_rule", dataset)
        self.people = {u["code"]: u for u in lib.rows("master", "user", dataset)}
        self.items = {r["code"]: r for r in lib.rows("master", "item", dataset)}
        self.suppliers = {r["code"]: r for r in lib.rows("master", "supplier", dataset)}
        self.plant_state = lib.rows("master", "gst_registration", dataset)[0]["gstin"][
            :2
        ]
        self.location_head = profile["location"]["head"]
        self.role_map = {r[0]: (r[1], r[2]) for r in profile["approver_roles"]}
        self.touchless_on = (profile.get("autonomy") or {}).get("po.issue") == "auto"

    def bands(self, document: str) -> list[dict[str, str]]:
        found = [
            r
            for r in self.rules
            if r["document"] == document and not r["approver_chain"].startswith("+")
        ]
        return sorted(found, key=lambda r: lib.D(r["min_value"]))

    def person(self, role: str) -> str:
        kind, value = self.role_map[role]
        if kind == "location_head":
            return str(self.location_head)
        return next(
            c
            for c, u in self.people.items()
            if u["designation"] == value and u["active"] == "true"
        )

    def gst_rate(self, item: str) -> Decimal:
        it = self.items[item]
        changed = (
            it["gst_rate_change_date"]
            and date.fromisoformat(it["gst_rate_change_date"]) <= TODAY
        )
        return Decimal(it["gst_rate_after"] if changed else it["gst_rate_before"])

    def total(self, item: str, qty: Decimal, price: Decimal, supplier: str) -> Decimal:
        taxable = money(qty * price)
        rate = self.gst_rate(item)
        if self.suppliers[supplier]["state_code"] != self.plant_state:
            return taxable + money(taxable * rate / 100)
        return taxable + 2 * money(taxable * rate / 200)

    def po_approver(
        self, total: Decimal, makers: set[str], *, touchless: bool = False
    ) -> tuple[str, list[str]]:
        """The person who must approve, POLICY or REFUSED, with the bands walked (strict C1)."""
        if touchless:
            return POLICY, ["touchless policy"]
        trail: list[str] = []
        started = False
        for band in self.bands("po"):
            if not started and not (
                lib.D(band["min_value"]) <= total <= lib.D(band["max_value"])
            ):
                continue
            started = True
            chain = band["approver_chain"]
            if (
                chain == "auto"
            ):  # only touchless orders are approved here; any other order goes up
                trail.append(f"{band['rule_code']} auto: not touchless, up")
                continue
            who = self.person(chain)
            if who in makers:
                trail.append(f"{band['rule_code']} {chain} {who}: a maker, up")
                continue
            trail.append(f"{band['rule_code']} {chain} {who}")
            return who, trail
        trail.append("no band left")
        return REFUSED, trail

    def price_decider(self, makers: set[str]) -> str:
        """Who accepts a price above the estimate or a supplier's price rise: the price_variance row, never a maker."""
        for band in self.bands("price_variance"):
            who = self.person(band["approver_chain"])
            if who not in makers:
                return who
        return REFUSED


@dataclass
class Case:
    name: str
    path: str  # screen, deputy, proposal (variance), proposal (supplier price), policy
    makers: set[str]
    expected: str  # a person's code, POLICY or REFUSED
    naive: str  # what a broken rule that ignores makers would say
    trail: list[str] = field(default_factory=list)
    actual: str = "-"
    verdict: str = "-"
    po: str = ""
    mail_from: int = (
        0  # emails from this index on were sent once every maker was a maker
    )


def run(ctx: Any) -> Outcome:
    env.forget_tenants("e54")
    try:
        return _run(ctx)
    finally:
        env.forget_tenants("e54")


def _run(ctx: Any) -> Outcome:
    from fastapi.testclient import TestClient
    from p2p.main import app
    from p2p.platform import demo, routing, worker
    from sqlalchemy.orm import Session

    out = Outcome(True)
    box = lib.capture_mail()
    client = TestClient(app)
    folder = Path(tempfile.mkdtemp(prefix="e54_"))
    dataset, credentials = folder / "dataset", folder / "credentials"
    slug = env.slug("e54")
    demo.reset_company(
        demo.company("apex"), dataset, credentials=credentials, slug=slug
    )
    db = Session(routing.engine_for(slug))
    profile = yaml.safe_load(
        (lib.ROOT / "datagen" / "catalog" / "apex" / "company.yaml").read_text(
            encoding="utf-8"
        )
    )
    oracle = Oracle(dataset, profile)
    cases: list[Case] = []
    checks = 0

    def check(ok: bool, message: str, severity: str = "high") -> bool:
        nonlocal checks
        checks += 1
        if not ok:
            out.find(severity, message)
        return ok

    # ---- logins by person code ---------------------------------------------------------------------------------
    passwords: dict[str, str] = {}
    for line in credentials.read_text(encoding="utf-8").splitlines():
        found = re.match(r"^(\S+@\S+)\s+(\S+)\s+#", line)
        if found:
            passwords[found.group(1)] = found.group(2)
    email_of = {c: u["email"] for c, u in oracle.people.items()}
    name_of = {c: u["name"] for c, u in oracle.people.items()}
    code_of_name = {u["name"]: c for c, u in oracle.people.items()}
    tokens: dict[str, dict[str, str]] = {}
    PROD, BUYER, ADMIN, ADMIN2 = "U01", "U03", "U06", "U10"
    PLANT_HEAD = oracle.person("plant_head")
    PM, DIRECTOR = oracle.person("purchase_manager"), oracle.person("director")

    def who(code: str) -> dict[str, str]:
        if code not in tokens:
            r = client.post(
                "/api/auth/login",
                json={"email": email_of[code], "password": passwords[email_of[code]]},
            )
            check(r.status_code == 200, f"{code} cannot sign in: {r.text[:120]}")
            tokens[code] = {
                "Authorization": "Bearer " + r.json().get("access_token", "")
            }
        return tokens[code]

    def call(
        code: str, method: str, path: str, body: Any = None, *, expect: int | None = 200
    ) -> Any:
        db.rollback()
        r = client.request(method, path, json=body, headers=who(code))
        db.rollback()
        if expect is not None and r.status_code != expect:
            check(
                False,
                f"{code}: {method} {path} gave {r.status_code} (wanted {expect}): {r.text[:200]}",
                "medium",
            )
        return r

    def work() -> None:
        db.commit()
        for _ in range(60):
            if not worker.run_once():
                break
        db.rollback()

    def pending(action: str) -> list[dict[str, Any]]:
        seen: dict[int, dict[str, Any]] = {}
        for code in (
            BUYER,
            PM,
            DIRECTOR,
            ADMIN,
        ):  # each sees what its permissions allow
            for p in call(code, "GET", "/api/admin/proposals?status=pending").json():
                if p["action"] == action:
                    seen[p["id"]] = p
        return list(seen.values())

    # ---- second jobs, each through two administrators ----------------------------------------------------------------
    people = {
        p["person"]["code"]: p
        for p in call(ADMIN, "GET", "/api/setup/people").json()["people"]
        if p["person"]
    }

    def grant(code: str, role: str) -> None:
        r = call(
            ADMIN,
            "POST",
            f"/api/setup/people/{people[code]['user_id']}/roles",
            {"role": role, "reason": "E54"},
            expect=None,
        )
        proposal = r.json().get("proposal_id")
        if proposal:
            call(
                ADMIN2,
                "POST",
                f"/api/admin/proposals/{proposal}/approve",
                {"note": "second job for E54"},
            )
        tokens.pop(code, None)

    grant(PM, "Buyer")
    grant(DIRECTOR, "Buyer")
    grant(BUYER, "Approver")
    roles_now = {
        p["person"]["code"]: [r["role"] for r in p["roles"]]
        for p in call(ADMIN, "GET", "/api/setup/people").json()["people"]
        if p["person"]
    }
    check(
        "Buyer" in roles_now[PM]
        and "Buyer" in roles_now[DIRECTOR]
        and "Approver" in roles_now[BUYER],
        f"the second jobs were not given: {roles_now[PM]}, {roles_now[DIRECTOR]}, {roles_now[BUYER]}",
    )

    # ---- building blocks ----------------------------------------------------------------------------------------------
    def request(item: str, qty: str) -> str:
        r = call(
            PROD,
            "POST",
            "/api/requisitions",
            {
                "lines": [{"item_code": item, "qty": qty}],
                "need_by": NEED_BY,
                "location": "PLT-CKN",
                "reason": "E54",
            },
        )
        no = str(r.json().get("id"))
        r = call(PROD, "POST", f"/api/requisitions/{no}/submit", {}, expect=None)
        if (
            r.status_code == 409 and "repeat" in r.text
        ):  # same item as an earlier case: a separate need
            call(
                PROD,
                "POST",
                f"/api/requisitions/{no}/duplicate-override",
                {"reason": "a separate case of E54"},
            )
            r = call(PROD, "POST", f"/api/requisitions/{no}/submit", {})
        for step in call(PROD, "GET", f"/api/requisitions/{no}").json()["approval"][
            "steps"
        ]:
            if step["status"] == "pending":
                call(
                    code_of_name[step["name"]],
                    "POST",
                    f"/api/requisitions/{no}/approve",
                    {},
                )
        return no

    def line_source(no: str) -> dict[str, Any]:
        return (
            call(BUYER, "GET", f"/api/requisitions/{no}")
            .json()["lines"][0]
            .get("source")
            or {}
        )

    def rfq_of(no: str) -> str:
        work()
        rfq = str(line_source(no).get("rfq_no"))
        for p in pending("rfq.send"):
            if p["payload"].get("rfq_no") == rfq:
                call(BUYER, "POST", f"/api/admin/proposals/{p['id']}/approve", {})
        work()
        return rfq

    def quotes(rfq: str, item: str, prices: list[str]) -> list[str]:
        """The buyer enters one quote per price from the suppliers asked, the cheapest first; returns the suppliers."""
        asked = sorted(
            s["code"]
            for s in call(BUYER, "GET", f"/api/rfqs/{rfq}").json()["suppliers"]
        )
        for supplier, price in zip(asked, prices, strict=False):
            body = {
                "supplier_code": supplier,
                "valid_until": "2026-12-31",
                "freight": "0",
                "lines": [
                    {
                        "rfq_line_no": 1,
                        "unit_price": price,
                        "discount_pct": "0",
                        "gst_rate": str(oracle.gst_rate(item)),
                        "delivery_days": 7,
                    }
                ],
            }
            call(BUYER, "POST", f"/api/rfqs/{rfq}/quotes", body)
        return asked[: len(prices)]

    def award(rfq: str, supplier: str, by: str, no: str) -> str:
        r = call(
            by,
            "POST",
            f"/api/rfqs/{rfq}/award",
            {"choices": {"1": supplier}},
            expect=None,
        )
        if (
            r.status_code == 409 and "reason" in r.text
        ):  # fewer quotes than the route wants: the awarder says why
            body = {
                "choices": {"1": supplier},
                "reason": "E54: two quotes are enough here",
            }
            r = call(by, "POST", f"/api/rfqs/{rfq}/award", body)
        check(
            r.status_code == 200,
            f"award of {rfq} by {by}: {r.status_code} {r.text[:150]}",
            "medium",
        )
        # Block 0b (task 4.4, D-03 R9 and OP-6, approved by the owner on 20 Nov 2026): too few counting quotes on a line
        # above the buying route's agree_above also needs an agreement under `sourcing.route_approve` before the
        # selection is made. The plant head, who takes no part in these orders, agrees, so the makers stay the same.
        answer = r.json() if r.status_code == 200 else {}
        if answer.get("status") == "waiting" and answer.get("proposal_id"):
            call(PLANT_HEAD, "POST", f"/api/admin/proposals/{answer['proposal_id']}/approve", {})
        work()
        return str(line_source(no).get("po_no") or "")

    def po(no: str) -> dict[str, Any]:
        return dict(call(ADMIN, "GET", f"/api/purchase-orders/{no}").json())

    def state(no: str) -> tuple[Any, ...]:
        d = po(no)
        steps = tuple((s["name"], s["status"]) for s in d["approval"]["steps"])
        return d["status"], d.get("waiting_for"), steps, d["total"]

    def name_deputy(code: str, deputy: str) -> Any:
        """From today (the evals' pinned day, else the clock's) to the end of the year."""
        r = None
        for start in (TODAY, date.today()):
            body = {"deputy": deputy, "start": start.isoformat(), "end": "2026-12-31"}
            r = call(code, "POST", "/api/me/deputies", body, expect=None)
            if r.status_code < 300:
                out.notes.append(f"{code} named {deputy} deputy from {start}")
                break
        return r

    def try_approve(code: str, no: str) -> Any:
        return call(
            code,
            "POST",
            f"/api/purchase-orders/{no}/approve",
            {"comment": "E54"},
            expect=None,
        )

    def refused_and_waiting(case: Case, code: str, no: str, how: str) -> None:
        before = state(no)
        r = try_approve(code, no)
        after = state(no)
        out.notes.append(
            f"{case.name}: {code} refused on {no} {how}: {r.status_code} {r.text[:140]}"
        )
        check(
            r.status_code in (403, 409, 422),
            f"{case.name}: {code} ({name_of[code]}), a maker, approved {no} {how} ({r.status_code} {r.text[:150]}); "
            f"expected refused",
        )
        check(
            after == before,
            f"{case.name}: a refused attempt by {code} changed {no}: {before} -> {after}; it must stay waiting",
            "high" if after[0] != before[0] else "medium",
        )

    def expect_waiting_for(case: Case, no: str) -> None:
        d = po(no)
        if case.expected in (POLICY, REFUSED):
            return
        check(
            d.get("waiting_for") == name_of[case.expected],
            f"{case.name}: {no} waits for {d.get('waiting_for')!r}; the rule gives {name_of[case.expected]} "
            f"({' / '.join(case.trail)})",
            "medium",
        )

    def approve_by_right_person(case: Case, no: str) -> None:
        r = try_approve(case.expected, no)
        d = po(no)
        ok = r.status_code == 200 and d["status"] in (
            "approved",
            "sent",
            "acknowledged",
        )
        case.actual = (
            name_of[case.expected]
            if ok
            else f"refused for {case.expected} ({r.status_code})"
        )
        check(
            ok,
            f"{case.name}: {case.expected} ({name_of[case.expected]}) is not a maker and the rule names them "
            f"({' / '.join(case.trail)}), but approving gave {r.status_code} {r.text[:150]}; order {d['status']}",
            "medium",
        )
        approved_by = {
            s["name"] for s in d["approval"]["steps"] if s["status"] == "approved"
        }
        check(
            not (approved_by & {name_of[m] for m in case.makers}),
            f"{case.name}: {no} carries an approval by a maker: {approved_by}",
        )

    def po_case(
        name: str,
        path: str,
        no: str,
        makers: set[str],
        item: str,
        qty: Decimal,
        price: Decimal,
        supplier: str,
    ) -> Case:
        want_total = oracle.total(item, qty, price, supplier)
        got = po(no) if no else {}
        check(bool(no), f"{name}: no order was made")
        if got:
            check(
                abs(lib.D(got["total"]) - want_total) <= 2 * CENT,
                f"{name}: {no} total {got['total']}, expected {want_total} ({qty} x {price} + GST)",
                "medium",
            )
        expected, trail = oracle.po_approver(want_total, makers)
        naive, _ = oracle.po_approver(want_total, set())
        case = Case(name, path, makers, expected, naive, trail, po=no)
        cases.append(case)
        return case

    # ---- C1: the buyer awards; Rs 1.6 lakh is the purchase manager's band ----------------------------------------
    req1 = request("RM-08", "150")
    rfq1 = rfq_of(req1)
    cheap = quotes(rfq1, "RM-08", ["905", "920"])[0]
    po1 = award(rfq1, cheap, BUYER, req1)
    c1 = po_case(
        "C1 buyer awards, PO-02",
        "screen",
        po1,
        {BUYER},
        "RM-08",
        Decimal(150),
        Decimal(905),
        cheap,
    )
    if po1:
        expect_waiting_for(c1, po1)
        refused_and_waiting(c1, BUYER, po1, "on the screen")
        approve_by_right_person(c1, po1)

    # ---- C2: the buyer awards below Rs 1 lakh: the 'auto' band is for touchless orders only -------------------------
    req2 = request("RM-07", "500")
    rfq2 = rfq_of(req2)
    cheap = quotes(rfq2, "RM-07", ["94", "97"])[0]
    po2 = award(rfq2, cheap, BUYER, req2)
    c2 = po_case(
        "C2 buyer awards, PO-01 not touchless",
        "screen",
        po2,
        {BUYER},
        "RM-07",
        Decimal(500),
        Decimal(94),
        cheap,
    )
    if po2:
        d = po(po2)
        check(
            d["status"] not in ("approved", "sent", "acknowledged")
            and not d["touchless"],
            f"C2: {po2} from a quotation was {d['status']} (touchless {d['touchless']}) with no person: only the "
            f"touchless policy approves in the 'auto' band (DF-34)",
        )
        r = call(BUYER, "POST", f"/api/purchase-orders/{po2}/send", expect=None)
        check(
            r.status_code in (403, 409, 422),
            f"C2: the buyer sent {po2} before anyone approved it ({r.status_code})",
        )
        expect_waiting_for(c2, po2)
        refused_and_waiting(c2, BUYER, po2, "on the screen")
        approve_by_right_person(c2, po2)

    # ---- C3: the purchase manager awards (second job): PO-02 names him, so it goes to the director -------------------
    req3 = request("RM-11", "400")
    rfq3 = rfq_of(req3)
    cheap = quotes(rfq3, "RM-11", ["345", "352"])[0]
    po3 = award(rfq3, cheap, PM, req3)
    c3 = po_case(
        "C3 purchase manager awards, PO-02",
        "screen",
        po3,
        {BUYER, PM},
        "RM-11",
        Decimal(400),
        Decimal(345),
        cheap,
    )
    if po3:
        expect_waiting_for(c3, po3)
        refused_and_waiting(c3, PM, po3, "on the screen (he awarded the quotation)")
        refused_and_waiting(c3, BUYER, po3, "on the screen")
        approve_by_right_person(c3, po3)

    # ---- C4: the purchase manager awards below Rs 1 lakh: auto band -> his band -> the director ----------------------
    req4 = request("RM-06", "1000")
    rfq4 = rfq_of(req4)
    cheap = quotes(rfq4, "RM-06", ["37.50", "38.50"])[0]
    po4 = award(rfq4, cheap, PM, req4)
    c4 = po_case(
        "C4 purchase manager awards, PO-01",
        "screen",
        po4,
        {BUYER, PM},
        "RM-06",
        Decimal(1000),
        Decimal("37.50"),
        cheap,
    )
    if po4:
        expect_waiting_for(c4, po4)
        refused_and_waiting(
            c4, PM, po4, "on the screen (he awarded it; the 'auto' band went up to him)"
        )
        approve_by_right_person(c4, po4)

    # ---- C5: the buyer awards, the purchase manager revises the order (a lower quantity): he is now a maker ----------
    req5 = request("RM-09", "1500")
    rfq5 = rfq_of(req5)
    cheap = quotes(rfq5, "RM-09", ["84", "86"])[0]
    po5 = award(rfq5, cheap, BUYER, req5)
    revised_at = len(box.sent)
    if po5:
        r = call(
            PM,
            "POST",
            f"/api/purchase-orders/{po5}/revise",
            {"lines": [{"line_no": 1, "qty": "1400"}], "reason": "E54: a smaller lot"},
        )
        out.notes.append(
            f"C5 revision by the purchase manager: {r.status_code} {r.text[:160]}"
        )
        revised_at = len(box.sent)
    c5 = po_case(
        "C5 purchase manager revises, PO-02",
        "screen",
        po5,
        {BUYER, PM},
        "RM-09",
        Decimal(1400),
        Decimal(84),
        cheap,
    )
    c5.mail_from = revised_at
    if po5:
        expect_waiting_for(c5, po5)
        refused_and_waiting(c5, PM, po5, "on the screen (he revised it)")
        approve_by_right_person(c5, po5)

    # ---- C6: the purchase manager changes the buying route to a direct buy; the plant head agrees (a checker) --------
    req6 = request("RM-10", "2000")
    work()
    r = call(
        PM,
        "POST",
        f"/api/requisitions/{req6}/lines/1/route",
        {
            "route": "direct",
            "supplier_code": "SUP-003",
            "price": "60",
            "reason": "E54: the usual supplier",
        },
    )
    proposal6 = r.json().get("proposal_id")
    if proposal6:
        own = call(
            PM, "POST", f"/api/admin/proposals/{proposal6}/approve", {}, expect=None
        )
        check(
            own.status_code in (403, 409, 422),
            f"C6: the purchase manager agreed to his own route change ({own.status_code})",
            "medium",
        )
        agreed = call(
            PLANT_HEAD,
            "POST",
            f"/api/admin/proposals/{proposal6}/approve",
            {},
            expect=None,
        )
        out.notes.append(
            f"C6 route change agreed by the plant head: {agreed.status_code} {agreed.text[:120]}"
        )
    work()
    po6 = str(line_source(req6).get("po_no") or "")
    c6 = po_case(
        "C6 purchase manager changes the route, PO-02",
        "screen",
        po6,
        {BUYER, PM},
        "RM-10",
        Decimal(2000),
        Decimal(60),
        "SUP-003",
    )
    if po6:
        expect_waiting_for(c6, po6)
        refused_and_waiting(
            c6, PM, po6, "on the screen (he changed the route of its line)"
        )
        approve_by_right_person(c6, po6)

    # ---- C7: the purchase manager is away; his deputy is the buyer, who awards the quotation --------------------------
    r = name_deputy(PM, BUYER)
    deputy7 = r.json().get("id") if r.status_code < 300 else None
    check(
        deputy7 is not None,
        f"C7: the purchase manager could not name the buyer deputy: {r.status_code} {r.text[:120]}",
        "medium",
    )
    req7 = request("RM-07", "1300")
    rfq7 = rfq_of(req7)
    cheap = quotes(rfq7, "RM-07", ["94", "96"])[0]
    po7 = award(rfq7, cheap, BUYER, req7)
    c7 = po_case(
        "C7 buyer awards and is the approver's deputy",
        "deputy",
        po7,
        {BUYER},
        "RM-07",
        Decimal(1300),
        Decimal(94),
        cheap,
    )
    if po7:
        d = po(po7)
        check(
            d.get("waiting_for") != name_of[BUYER],
            f"C7: {po7} waits for the buyer as the purchase manager's deputy, but the buyer awarded it",
            "medium",
        )
        steps = [
            (x["name"], x["status"], x.get("stand_in")) for x in d["approval"]["steps"]
        ]
        out.notes.append(f"C7: {po7} waits for {d.get('waiting_for')}; steps {steps}")
        refused_and_waiting(c7, BUYER, po7, "as the purchase manager's deputy")
        approve_by_right_person(c7, po7)
    if deputy7:
        call(PM, "DELETE", f"/api/me/deputies/{deputy7}", expect=None)

    # ---- C8: the director is away; her deputy is the purchase manager, who awarded the order that went up to her ------
    r = name_deputy(DIRECTOR, PM)
    deputy8 = r.json().get("id") if r.status_code < 300 else None
    check(
        deputy8 is not None,
        f"C8: the director could not name the purchase manager deputy: {r.status_code} {r.text[:120]}",
        "medium",
    )
    req8 = request("RM-06", "3000")
    rfq8 = rfq_of(req8)
    cheap = quotes(rfq8, "RM-06", ["37.50", "38.50"])[0]
    po8 = award(rfq8, cheap, PM, req8)
    c8 = po_case(
        "C8 purchase manager awards and is the director's deputy",
        "deputy",
        po8,
        {BUYER, PM},
        "RM-06",
        Decimal(3000),
        Decimal("37.50"),
        cheap,
    )
    if po8:
        d = po(po8)
        check(
            d.get("waiting_for") != name_of[PM],
            f"C8: {po8} waits for the purchase manager (as the director's deputy), but he awarded it",
            "medium",
        )
        steps = [
            (x["name"], x["status"], x.get("stand_in")) for x in d["approval"]["steps"]
        ]
        out.notes.append(f"C8: {po8} waits for {d.get('waiting_for')}; steps {steps}")
        refused_and_waiting(c8, PM, po8, "as the director's deputy")
        approve_by_right_person(c8, po8)
    # control for C7 and C8: with the director still away, an order the purchase manager did not make goes to him as
    # her deputy; without this the deputy refusals above could only be "not your turn"
    reqd = request("RM-08", "560")
    rfqd = rfq_of(reqd)
    cheap = quotes(rfqd, "RM-08", ["905", "915"])[0]
    pod = award(rfqd, cheap, BUYER, reqd)
    deputy_used = False
    if pod and deputy8:
        d = po(pod)
        steps = [
            (x["name"], x["status"], x.get("stand_in")) for x in d["approval"]["steps"]
        ]
        deputy_used = d.get("waiting_for") == name_of[PM] or any(x[2] for x in steps)
        out.notes.append(
            f"deputy control: {pod} ({d['total']}) waits for {d.get('waiting_for')}; steps {steps}"
        )
    out.metrics["deputy routing observed for orders (control)"] = deputy_used
    if not deputy_used:
        out.find(
            "low",
            "an order above Rs 5 lakh made by the buyer while the director is away did not go to her deputy (the "
            "purchase manager), so C7 and C8 show only that the maker is refused, not that the deputy route checks "
            "makers: either deputies do not stand in for order approvals or the control did not reach them",
        )
    if deputy8:
        call(DIRECTOR, "DELETE", f"/api/me/deputies/{deputy8}", expect=None)

    # ---- C9: the director awards an order above Rs 5 lakh: her band is the last; nobody is left ------------------------
    req9 = request("RM-08", "600")
    rfq9 = rfq_of(req9)
    cheap = quotes(rfq9, "RM-08", ["905", "915"])[0]
    po9 = award(rfq9, cheap, DIRECTOR, req9)
    c9 = po_case(
        "C9 director awards, PO-03 (no band left)",
        "screen",
        po9,
        {BUYER, DIRECTOR},
        "RM-08",
        Decimal(600),
        Decimal(905),
        cheap,
    )
    if po9:
        before = state(po9)
        r = try_approve(DIRECTOR, po9)
        check(
            r.status_code in (403, 409, 422),
            f"C9: the director approved {po9} that she awarded ({r.status_code}); no band is above hers, so it must be "
            f"refused (a broken rule that ignores makers would approve it here)",
        )
        reason = (
            r.json().get("detail")
            if r.headers.get("content-type", "").startswith("application/json")
            else ""
        ) or ""
        check(
            bool(str(reason).strip()),
            f"C9: the director's refusal on {po9} gave no reason",
            "medium",
        )
        for code in (PM, BUYER):  # below the band or makers: never a way round
            r2 = try_approve(code, po9)
            check(
                r2.status_code in (403, 409, 422),
                f"C9: {code} approved {po9} above Rs 5 lakh ({r2.status_code})",
            )
        after = po(po9)
        check(
            after["status"] not in ("approved", "sent", "acknowledged"),
            f"C9: {po9} became {after['status']}",
        )
        check(
            state(po9)[:3] == before[:3],
            f"C9: refused attempts changed {po9}: {before} -> {state(po9)}",
            "medium",
        )
        check(
            after.get("waiting_for") != name_of[DIRECTOR],
            f"C9: {po9} says it waits for {after.get('waiting_for')}, a maker; with no band left it must be refused "
            f"with a reason, not wait for a maker (shown: {after['approval'].get('decision')!r}, hold {after.get('hold_reason')!r})",
            "medium",
        )
        shown = [
            after.get("hold_reason"),
            after["approval"].get("decision"),
            after.get("touchless_note"),
        ]
        elsewhere = [
            path
            for code, path in (
                (ADMIN, "/api/exceptions"),
                (BUYER, "/api/exceptions"),
                (BUYER, "/api/notifications"),
            )
            if po9 in call(code, "GET", path, expect=None).text
        ]
        out.notes.append(
            f"C9: {po9} is {after['status']}; the order shows {shown}; named in {elsewhere}"
        )
        check(
            any(
                x and ("approv" in str(x).lower() or "maker" in str(x).lower())
                for x in shown[:2]
            ),
            f"C9: {po9} cannot be approved by anyone, but the order does not say why (hold {shown[0]!r}, decision "
            f"{shown[1]!r}); refused attempts only say {str(reason)[:80]!r}",
            "medium",
        )
        c9.actual = (
            f"refused ({r.status_code}): {str(reason)[:90]}"
            if r.status_code != 200
            else "approved by the director"
        )

    # ---- C10: the purchase manager awards above the estimate; the price is his to accept, but he made the order --------
    req10 = request("RM-11", "100")
    est10 = lib.D(
        call(BUYER, "GET", f"/api/requisitions/{req10}").json()["lines"][0]["est_price"]
    )
    rfq10 = rfq_of(req10)
    high10 = money(est10 * Decimal("1.25"))
    cheap = quotes(rfq10, "RM-11", [str(high10), str(money(high10 + 10))])[0]
    po10 = award(rfq10, cheap, PM, req10)
    c10 = Case(
        "C10 purchase manager awards above the estimate",
        "proposal (price above estimate)",
        {BUYER, PM},
        oracle.price_decider({BUYER, PM}),
        oracle.price_decider(set()),
        ["VAR-01 purchase_manager: a maker; no other row"],
        po=po10,
    )
    cases.append(c10)
    if po10:
        held = [
            p
            for p in pending("po.accept_variance")
            if p["payload"].get("po_no") == po10
        ]
        check(
            bool(held) and po(po10)["held"],
            f"C10: {po10} at 25% above the estimate should be held for its price",
            "medium",
        )
        for p in held:
            r = call(
                PM,
                "POST",
                f"/api/admin/proposals/{p['id']}/approve",
                {"note": "E54"},
                expect=None,
            )
            check(
                r.status_code in (403, 409, 422),
                f"C10: the purchase manager accepted the price on {po10} that he awarded ({r.status_code})",
            )
            r = call(
                BUYER,
                "POST",
                f"/api/admin/proposals/{p['id']}/approve",
                {"note": "E54"},
                expect=None,
            )
            check(
                r.status_code in (403, 409, 422),
                f"C10: the buyer accepted the price on {po10} ({r.status_code})",
            )
            still = [x for x in pending("po.accept_variance") if x["id"] == p["id"]]
            check(
                bool(still) and po(po10)["held"],
                f"C10: after the refused attempts the price on {po10} must still wait",
                "medium",
            )
        c10.actual = (
            "refused; still waiting" if held and po(po10)["held"] else "not held"
        )

    # ---- C11: the buyer awards above the estimate; the purchase manager accepts the price (a checker) and approves -----
    req11 = request("RM-08", "150")
    est11 = lib.D(
        call(BUYER, "GET", f"/api/requisitions/{req11}").json()["lines"][0]["est_price"]
    )
    rfq11 = rfq_of(req11)
    high11 = money(est11 * Decimal("1.25"))
    cheap = quotes(rfq11, "RM-08", [str(high11), str(money(high11 + 10))])[0]
    po11 = award(rfq11, cheap, BUYER, req11)
    if po11:
        held = [
            p
            for p in pending("po.accept_variance")
            if p["payload"].get("po_no") == po11
        ]
        check(
            bool(held),
            f"C11: {po11} at 25% above the estimate should wait for the price to be accepted",
            "medium",
        )
        for p in held:
            r = call(
                BUYER,
                "POST",
                f"/api/admin/proposals/{p['id']}/approve",
                {"note": "E54"},
                expect=None,
            )
            check(
                r.status_code in (403, 409, 422),
                f"C11: the buyer accepted the price on {po11} that she awarded ({r.status_code})",
            )
            call(
                PM,
                "POST",
                f"/api/admin/proposals/{p['id']}/approve",
                {"note": "E54: the market moved"},
            )
        work()
    c11 = po_case(
        "C11 purchase manager accepted the price (checker)",
        "screen",
        po11,
        {BUYER},
        "RM-08",
        Decimal(150),
        high11,
        cheap,
    )
    if po11:
        expect_waiting_for(c11, po11)
        refused_and_waiting(c11, BUYER, po11, "on the screen")
        approve_by_right_person(c11, po11)

    # ---- the supplier's price rise on orders that are out ---------------------------------------------------------------
    decided: dict[str, Any] = {}

    def supplier_rise(no: str, new_price: str) -> int | None:
        sent_before = len(box.sent)
        call(BUYER, "POST", f"/api/purchase-orders/{no}/send")
        links = [
            m
            for mail in box.sent[sent_before:]
            for m in re.findall(r"/supplier/[A-Za-z0-9_.\-]+", mail["body"])
        ]
        if not check(
            bool(links), f"{no}: the order email carries no supplier link", "medium"
        ):
            return None
        mailed = len(box.sent)
        client.post(f"{links[0]}/code")  # D53: the supplier asks for its one-time code and reads it from its mail
        coded = [m for m in box.sent[mailed:] if "one-time code is " in m["body"]]
        client.post(
            links[0],
            data={
                "action": "change",
                "price_1": new_price,
                "reason": "E54: metal prices",
                "code": coded[-1]["body"].split("one-time code is ")[1][:6] if coded else "",
            },
        )
        changes = (
            call(BUYER, "GET", f"/api/purchase-orders/{no}/supplier-answer")
            .json()
            .get("changes")
            or []
        )
        if not check(
            bool(changes),
            f"{no}: the supplier's price change was not recorded",
            "medium",
        ):
            return None
        r = call(
            BUYER,
            "POST",
            f"/api/supplier-changes/{changes[0]['id']}/decide",
            {"accept": True, "note": "E54"},
            expect=None,
        )
        decided[no] = r
        out.notes.append(
            f"{no}: the buyer accepted the supplier's price {new_price}: {r.status_code} {r.text[:160]}"
        )
        return int(changes[0]["id"])

    def price_of(no: str) -> str:
        return str(po(no)["lines"][0]["unit_price"])

    # C12: C3's order (the purchase manager awarded it, the director approved it); the supplier asks for 5% more
    c12 = Case(
        "C12 supplier price rise on an order the purchase manager made",
        "proposal (supplier price)",
        {BUYER, PM},
        oracle.price_decider({BUYER, PM}),
        oracle.price_decider(set()),
        ["VAR-01 purchase_manager: a maker"],
        po=po3,
    )
    cases.append(c12)
    if po3 and po(po3)["status"] == "approved":
        old = price_of(po3)
        change = supplier_rise(po3, str(money(Decimal(old) * Decimal("1.05"))))
        answer = decided.get(po3)
        if answer is not None and answer.status_code != 200:
            open_changes = (
                call(BUYER, "GET", f"/api/purchase-orders/{po3}/supplier-answer")
                .json()
                .get("changes")
                or []
            )
            check(
                any(
                    c["id"] == change
                    and c["status"] in ("proposed", "waiting_approval")
                    for c in open_changes
                ),
                f"C12: after the refusal the supplier's change on {po3} is no longer waiting: {open_changes}",
                "medium",
            )
            if "requester" in answer.text:
                out.find(
                    "low",
                    f"C12: the refusal on {po3} says {answer.json().get('detail')!r}; the purchase manager awarded "
                    "the order, he did not request it: the reason names the wrong duty",
                )
        waits = [
            p
            for p in pending("po.supplier_price")
            if p["payload"].get("change_id") == change
        ]
        for p in waits:
            check(
                p["payload"].get("approver") != PM,
                f"C12: the price rise on {po3} was put to the purchase manager, who awarded the order",
                "medium",
            )
            r = call(
                PM,
                "POST",
                f"/api/admin/proposals/{p['id']}/approve",
                {"note": "E54"},
                expect=None,
            )
            check(
                r.status_code in (403, 409, 422),
                f"C12: the purchase manager accepted the supplier's price rise on {po3} that he awarded ({r.status_code})",
            )
            r = call(
                BUYER,
                "POST",
                f"/api/admin/proposals/{p['id']}/approve",
                {"note": "E54"},
                expect=None,
            )
            check(
                r.status_code in (403, 409, 422),
                f"C12: the buyer accepted the supplier's price rise on {po3} ({r.status_code})",
            )
        check(
            price_of(po3) == old,
            f"C12: the price on {po3} moved from {old} to {price_of(po3)} without a non-maker",
        )
        moved = price_of(po3) != old
        c12.actual = ("accepted: " if moved else "refused; ") + (
            f"price {price_of(po3)} (was {old}); {len(waits)} proposal(s) waiting for "
            f"{[p['payload'].get('approver') for p in waits]}"
        )
    else:
        c12.actual = "not reached: C3's order was not approved"

    # C13: C1's order (the buyer awarded it, the purchase manager approved it: a checker); the supplier asks for 1% more
    c13 = Case(
        "C13 supplier price rise, the approver is not a maker",
        "proposal (supplier price)",
        {BUYER},
        oracle.price_decider({BUYER}),
        oracle.price_decider(set()),
        ["VAR-01 purchase_manager"],
        po=po1,
    )
    cases.append(c13)
    if po1 and po(po1)["status"] == "approved":
        old = price_of(po1)
        new = str(money(Decimal(old) * Decimal("1.01")))
        change = supplier_rise(po1, new)
        check(
            price_of(po1) == old,
            f"C13: the buyer's acceptance alone changed the price on {po1} to {price_of(po1)}; a maker never accepts a supplier's price rise",
            "high",
        )
        waits = [
            p
            for p in pending("po.supplier_price")
            if p["payload"].get("change_id") == change
        ]
        check(
            len(waits) == 1,
            f"C13: the price rise the buyer accepted on {po1} should wait for the purchase manager: {len(waits)} proposals",
            "medium",
        )
        for p in waits:
            r = call(
                BUYER,
                "POST",
                f"/api/admin/proposals/{p['id']}/approve",
                {"note": "E54"},
                expect=None,
            )
            check(
                r.status_code in (403, 409, 422),
                f"C13: the buyer accepted the supplier's price rise on {po1} herself ({r.status_code})",
            )
            r = call(
                PM,
                "POST",
                f"/api/admin/proposals/{p['id']}/approve",
                {"note": "E54"},
                expect=None,
            )
            check(
                r.status_code == 200,
                f"C13: the purchase manager (not a maker) could not accept the rise: {r.status_code} {r.text[:150]}",
                "medium",
            )
        check(
            money(price_of(po1)) == money(new),
            f"C13: after the purchase manager accepted, the price is {price_of(po1)}, expected {new}",
            "medium",
        )
        c13.actual = (
            name_of[PM]
            if money(price_of(po1)) == money(new)
            else f"price {price_of(po1)} (was {old})"
        )
    else:
        c13.actual = "not reached: C1's order was not approved"

    # ---- C14: a contract line within the touchless limits: the policy approves, no person -----------------------------
    req14 = request("RM-03", "100")
    work()
    po14 = str(line_source(req14).get("po_no") or "")
    contract_price = next(
        lib.D(p["price"])
        for p in lib.rows("master", "price_record", dataset)
        if p["item_code"] == "RM-03" and p["contract_code"]
    )
    total14 = oracle.total("RM-03", Decimal(100), contract_price, "SUP-001")
    band14 = next(
        b
        for b in oracle.bands("po")
        if lib.D(b["min_value"]) <= total14 <= lib.D(b["max_value"])
    )
    touchless14 = oracle.touchless_on and band14["approver_chain"] == "auto"
    want14 = oracle.po_approver(total14, set(), touchless=touchless14)[0]
    c14 = Case(
        "C14 contract line within the touchless policy",
        "policy",
        set(),
        want14,
        want14,
        po=po14,
    )
    cases.append(c14)
    if check(bool(po14), "C14: the contract line made no order", "medium"):
        d = po(po14)
        people_on_it = [
            s["name"] for s in d["approval"]["steps"] if s["status"] == "approved"
        ]
        ok = d["touchless"] and d["status"] in ("approved", "sent") and not people_on_it
        check(
            ok == (c14.expected == POLICY),
            f"C14: {po14} of {d['total']}: touchless {d['touchless']}, {d['status']}, approved by {people_on_it}; expected {c14.expected}",
            "medium",
        )
        c14.actual = POLICY if ok else f"{d['status']} touchless {d['touchless']}"

    # ---- verdict per case ------------------------------------------------------------------------------------------------
    for case in cases:
        if case.expected == REFUSED:
            case.verdict = "PASS" if case.actual.startswith("refused") else "FAIL"
        elif case.expected == POLICY:
            case.verdict = "PASS" if case.actual == POLICY else "FAIL"
        else:
            case.verdict = (
                "PASS" if case.actual == name_of.get(case.expected) else "FAIL"
            )

    # ---- the emailed path: an approval task or link for an order never goes to its maker -------------------------------
    makers_of = {c.po: (c.makers, c.mail_from) for c in cases if c.po}
    emailed = 0
    for index, mail in enumerate(box.sent):
        text = f"{mail.get('subject', '')} {mail.get('body', '')}"
        for no, (makers, since) in makers_of.items():
            if (
                no in text
                and index >= since
                and ("approve" in text.lower() or "/approve/" in text)
            ):
                to = mail.get("to", "")
                if any(to == email_of[m] for m in makers):
                    check(
                        False,
                        f"an email asking to approve {no} went to its maker {to}: {mail.get('subject')}",
                    )
                emailed += 1
    po_links = sum(
        1
        for m in box.sent
        if "/approve/" in m.get("body", "") and "PO-" in m.get("body", "")
    )
    out.metrics["order approval emails checked / with a signed approval link"] = (
        f"{emailed} / {po_links}"
    )
    if po_links == 0:
        out.notes.append(
            "No order approval email carries a signed /approve/ link (they are task notices), so the link path for orders "
            "is checked only as: no approval email for an order ever goes to one of its makers"
        )

    # ---- mutation: the cases must tell a broken rule (makers ignored) from the right one --------------------------------
    discriminating = [c.name for c in cases if c.naive != c.expected]
    out.metrics["cases where a rule ignoring makers gives another answer"] = (
        f"{len(discriminating)} of {len(cases)}"
    )
    check(
        len(discriminating) >= 7,
        f"too few cases tell a broken rule from the right one: {discriminating}",
        "medium",
    )
    only_maker = [c for c in cases if c.naive in c.makers and c.expected == REFUSED]
    out.metrics["cases where a maker would be the only approver (must be refused)"] = [
        c.name for c in only_maker
    ]
    check(
        bool(only_maker),
        "no case where a maker would be the only approver: the mutation check is missing",
        "medium",
    )

    out.metrics["cases"] = {
        c.name: f"{c.path}; makers {sorted(c.makers)}; expected {name_of.get(c.expected, c.expected)}; "
        f"broken rule {name_of.get(c.naive, c.naive)}; actual {c.actual}; {c.verdict}"
        for c in cases
    }
    out.metrics["checks"] = checks
    out.passed = out.passed and not any(
        f.severity in ("high", "medium") for f in out.findings
    )
    db.close()
    return out
