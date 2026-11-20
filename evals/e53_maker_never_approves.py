"""E53: whoever shaped a purchase order never approves it or its price, by any route (C1), on the main demo company,
against an oracle from the dataset's approval rules.

The oracle reads the 'po' and 'price_variance' rows of `approval_rule.csv` and the company's approver roles (a role is
the person of that designation in `user.csv`), and applies the plain C1 rule: an order's chain is the band its total
falls in, with the order's makers taken out; a band with no approver (only a touchless order is approved there, by the
rule) or a band left with nobody sends the order up to the next band; a deputy who made the order does not stand in.

The purchase manager is made a maker of orders in each of five ways (the order's buyer, who submitted it, who revised
it, who awarded the quotation its line came from, who changed its buying route), each after a decision is already
waiting for that person, and is then refused on every path: approving the order's step, accepting its price above the
estimate, accepting a supplier's price rise (one within the 2% tolerance, which the buyer, also a maker, could not
accept either), and approving as the director's deputy; every refusal leaves the order and the proposal as they were.
The orders then go up a tier to the director. A non-touchless order of the no-approver band goes to a person; a real
buyer and a real submitter are skipped from the start. Accepting a held price or a supplier's price does not make the
acceptor a maker: the purchase manager accepts and then approves. Three rules are broken on purpose and the eval must
notice each.
"""

from datetime import timedelta
from decimal import Decimal
from typing import Any

import lib
from lib import D, Outcome, rows
from sqlalchemy import text

ID = "E53"
TITLE = "PO makers never approve: not the order, its price, a supplier's price rise, nor as a deputy"
REQUIREMENT = (
    "C1 (v1.1): the people who made a purchase order (its buyer, submitter, reviser, the awarder of its quotation, who "
    "changed its buying route) never approve it, accept its price or a supplier's price rise (even within tolerance), "
    "nor stand in as a deputy; the chain skips them and goes up a tier; in the band with no approver only a touchless "
    "order is approved by the rule, any other goes to a person; accepting a price is not making the order; a refused "
    "person changes nothing"
)
KINDS = ("buyer", "submitter", "reviser", "awarder", "route changer")
SUPPLIER = "SUP-001"
CATEGORY = "RAW"


# ---- the oracle ----------------------------------------------------------------------------------


def person_for(role: str) -> str:
    """A chain role as a person: the company's approver role names a designation, the person holds it."""
    source, value = next((s, v) for r, s, v in lib.demo_company().approver_roles if r == role)
    assert source == "designation", (role, source)
    return next(u["code"] for u in rows("master", "user") if u["designation"] == value)


def bands(document: str) -> list[tuple[Decimal, Decimal, str, str]]:
    return sorted(
        (D(r["min_value"]), D(r["max_value"]), r["approver_chain"], r["rule_code"])
        for r in rows("master", "approval_rule")
        if r["document"] == document
    )


def chain_oracle(total: Decimal, makers: set[str]) -> tuple[str, list[str]]:
    """(rule, people) for an order that is not touchless: its band, else the next band up that has someone left."""
    po = bands("po")
    at = next(i for i, (lo, hi, _, _) in enumerate(po) if lo <= total <= hi)
    for _, _, chain, code in po[at:]:
        if chain == "auto":
            continue  # only a touchless order is approved here, by the rule
        people = [person_for(r.lstrip("+")) for r in chain.split(">")]
        people = [p for p in people if p not in makers]
        if people:
            return code, people
    return "", []


def price_rise_person() -> str:
    _, _, chain, _ = bands("price_variance")[0]
    return person_for(chain.split(">")[0].lstrip("+"))


# ---- the world -----------------------------------------------------------------------------------


class Shop:
    """The demo company with logins for the people the eval acts as, and a way to make orders at a chosen value."""

    def __init__(self) -> None:
        from p2p.agents import autonomy
        from p2p.platform import sod, users

        lib.capture_mail()
        self.world = lib.build_world("e53", with_stock=False)
        self.db, self.slug = self.world.db, self.world.slug
        self.pm, self.director = person_for("purchase_manager"), person_for("director")
        self.buyer = next(r["buyer_user"] for r in rows("master", "category") if r["code"] == CATEGORY)
        own = f"buyer.{self.slug}@eval.example"  # the category's buyer, as a person with a buyer's login
        self.db.execute(text("update org_user set email = :e where code = :c"), {"e": own, "c": self.buyer})
        self.db.commit()
        self.login = dict(self.world.org_logins)
        self.login[self.buyer] = users.create_user(self.slug, own, lib.PASSWORD, "The buyer", "Buyer")
        autonomy.configure(self.db, sod.user(self.world.logins["Admin"]), "po.issue", level="propose")
        self.n = 0

    def order(self, price: str, est: str | None = None) -> str:
        """One line of a free-of-GST item bought on the supplier's contract (so the total is the price), ordered."""
        from p2p.orders import service as orders
        from p2p.sourcing import service as sourcing

        db, self.n = self.db, self.n + 1
        item, req, contract = f"E53-I{self.n}", f"E53-REQ-{self.n}", f"E53-K{self.n}"
        db.execute(
            text(
                "insert into item (code, name, category_code, base_uom, purchase_uom, active, stocked, standard_price, "
                "gst_rate_before, gst_rate_after) values (:i, :n, :c, 'NOS', 'NOS', true, true, :p, 0, 0)"
            ),
            {"i": item, "n": f"E53 part {self.n}", "c": CATEGORY, "p": est or price},
        )
        db.execute(
            text(
                "insert into contract (code, supplier_code, type, valid_from, valid_to, status) "
                "values (:k, :s, 'rate_contract', '2026-04-01', '2027-03-31', 'active')"
            ),
            {"k": contract, "s": SUPPLIER},
        )
        db.execute(
            text(
                "insert into price_record (supplier_code, item_code, contract_code, uom, price, min_qty, valid_from, "
                "valid_to, lead_days) values (:s, :i, :k, 'NOS', :p, 1, '2026-04-01', '2027-03-31', 5)"
            ),
            {"s": SUPPLIER, "i": item, "k": contract, "p": price},
        )
        db.execute(
            text(
                "insert into request (no, ship_to_location, type, channel, status, total_value) "
                "values (:n, :loc, 'goods', 'planning', 'approved', 1)"
            ),
            {"n": req, "loc": lib.location()},
        )
        db.execute(
            text(
                "insert into request_line (request_no, line_no, item_code, qty, uom, est_price, category_code, "
                "need_by, status) values (:r, 1, :i, 1, 'NOS', :e, :c, :nb, 'open')"
            ),
            {"r": req, "i": item, "e": est or price, "c": CATEGORY, "nb": lib.TODAY + timedelta(days=20)},
        )
        db.commit()
        sourcing.source_request(db, self.slug, req, as_of=lib.TODAY, dispatch=False)
        (po,) = orders.order_request(db, self.slug, req, as_of=lib.TODAY).pos
        return str(po)

    def po(self, no: str) -> dict[str, Any]:
        self.db.rollback()
        return dict(self.db.execute(text("select * from po where no = :n"), {"n": no}).mappings().one())

    def steps(self, no: str) -> list[tuple[str, str | None]]:
        self.db.rollback()
        return [
            (r[0], r[1])
            for r in self.db.execute(
                text(
                    "select s.approver, s.delegate from po_approval_step s join po p on p.no = s.po_no "
                    "and p.approval_round = s.round where s.po_no = :n order by s.step"
                ),
                {"n": no},
            )
        ]

    def proposal(self, pid: int) -> str:
        self.db.rollback()
        return str(self.db.execute(text("select status from proposal where id = :i"), {"i": pid}).scalar_one())

    def attempt(self, fn: Any) -> str:
        """'done' or 'refused' (a refusal is one of the platform's own refusals, never a crash)."""
        from p2p.approvals.workflow import ApprovalError
        from p2p.orders.confirmation import ConfirmationError
        from p2p.platform.permissions import PermissionDenied
        from p2p.platform.proposals import ProposalError

        try:
            fn()
        except (ApprovalError, ConfirmationError, PermissionDenied, ProposalError):
            self.db.rollback()
            return "refused"
        return "done"

    def mark(self, no: str, kind: str, person: str) -> None:
        """Leave the mark of one way of shaping the order, done by this person's login."""
        from p2p.platform import audit, sod

        db, uid = self.db, self.login[person]
        req, line = db.execute(
            text("select request_no, request_line_no from po_line where po_no = :n"), {"n": no}
        ).one()
        if kind == "buyer":
            db.execute(text("update po set buyer = :b where no = :n"), {"b": person, "n": no})
        elif kind == "submitter":
            audit.record(db, sod.user(uid), "po.submit", "po", no, after={"eval": "E53"})
        elif kind == "reviser":
            version = int(db.execute(text("select version from po where no = :n"), {"n": no}).scalar_one()) + 1
            db.execute(
                text(
                    "insert into po_revision (po_no, version, changed_by, reason, changes, total_before, total_after, "
                    "reapproval) values (:n, :v, :u, 'E53: a quantity revised', '[]', 1, 1, true)"
                ),
                {"n": no, "v": version, "u": str(uid)},
            )
        elif kind == "awarder":
            rfq = f"RFQ-E53-{no[-6:]}"
            db.execute(
                text("insert into rfq (no, due_date, type, status) values (:r, :d, 'commercial', 'awarded')"),
                {"r": rfq, "d": lib.TODAY},
            )
            db.execute(
                text(
                    "insert into rfq_line (rfq_no, line_no, request_no, request_line_no, qty) values (:r, 1, :q, :l, 1)"
                ),
                {"r": rfq, "q": req, "l": line},
            )
            audit.record(db, sod.user(uid), "rfq.award", "rfq", rfq)
        else:
            audit.record(
                db,
                sod.user(uid),
                "sourcing.route_change_proposed",
                "request",
                f"{req}/{line}",
                after={"route": "direct"},
            )
        db.commit()

    def resubmit(self, no: str, actor: Any = None) -> None:
        """The order back to draft and submitted again (by the system unless an actor is given)."""
        from p2p.orders import service as orders
        from p2p.orders import workflow

        self.db.execute(text("delete from po_approval_step where po_no = :n"), {"n": no})
        self.db.execute(text("update po set status = 'draft' where no = :n"), {"n": no})
        self.db.commit()
        workflow.submit(self.db, self.slug, no, actor or orders.SERVICE, today=lib.TODAY)

    def send_with_price_rise(self, no: str, pct: str) -> int:
        """Approved order sent; the supplier asks for a price `pct`% up; the buyer accepts. Returns the change id."""
        from p2p.orders import confirmation, dispatch
        from p2p.platform import sod

        dispatch.send_po(self.db, no, sod.user(self.login[self.buyer]), tenant=self.slug)
        old = D(self.db.execute(text("select unit_price from po_line where po_no = :n"), {"n": no}).scalar_one())
        new = (old * (1 + D(pct) / 100)).quantize(D("0.01"))
        (cid,) = confirmation.propose(
            self.db, self.slug, no, [confirmation.Proposed("price", 1, str(new))], source="link", reason="E53",
            actor=confirmation.supplier_actor(SUPPLIER),
        )  # fmt: skip
        self.last_buyer_answer = confirmation.decide(self.db, self.slug, cid, self.login[self.buyer], accept=True)
        return int(cid)

    def change(self, cid: int) -> dict[str, Any]:
        self.db.rollback()
        return dict(self.db.execute(text("select * from po_change where id = :i"), {"i": cid}).mappings().one())


def run(ctx: lib.Ctx) -> Outcome:  # noqa: C901 - one scenario, step by step
    from p2p.orders import confirmation, workflow
    from p2p.platform import proposals

    out = Outcome(True)
    shop = Shop()
    db, pm, director, buyer = shop.db, shop.pm, shop.director, shop.buyer
    approve = lambda no, who: shop.attempt(lambda: workflow.approve(db, shop.slug, no, shop.login[who]))  # noqa: E731
    mid, held_price, high, small = D("200000.00"), D("250000.00"), D("700000.00"), D("50000.00")
    rise_person = price_rise_person()
    tolerance = D(next(r["value"] for r in rows("master", "tolerance") if r["key"] == "supplier_price_pct"))
    out.metrics["oracle: band people (PO-02 / PO-03 / price rise)"] = (
        f"{chain_oracle(mid, set())[1]} / {chain_oracle(high, set())[1]} / {rise_person}"
    )

    # the director is away the whole time; the purchase manager is the deputy
    db.execute(
        text(
            "insert into delegation (from_user, to_user, start_date, reason) values (:f, :t, '2026-01-01', 'E53 leave')"
        ),
        {"f": director, "t": pm},
    )
    db.commit()

    # 1. every way of making an order, every path of approving it
    bad: list[str] = []

    def one_kind(kind: str) -> tuple[int, int]:
        """The purchase manager makes four orders in one way; (paths refused, paths tried)."""
        refused = tried = 0
        held, plain, sent, deputy = (
            shop.order(str(held_price), str(mid)),
            shop.order(str(mid)),
            shop.order(str(mid)),
            shop.order(str(high)),
        )
        hold_pid = int(shop.po(held)["hold_proposal_id"] or 0)
        before = {
            "plain": shop.steps(plain),
            "deputy": shop.steps(deputy),
        }
        want_plain = [(p, None) for p in chain_oracle(mid, {buyer})[1]]
        want_deputy = [(p, pm if p == director else None) for p in chain_oracle(high, {buyer})[1]]
        if before["plain"] != want_plain or before["deputy"] != want_deputy or not hold_pid:
            bad.append(
                f"{kind}: before the mark, chains {before}, oracle {want_plain} / {want_deputy}, hold {hold_pid}"
            )
        if approve(sent, pm) != "done":
            bad.append(f"{kind}: {pm} could not approve {sent} before making it")
        cid = shop.send_with_price_rise(sent, "1")  # within the tolerance: the buyer, a maker, cannot accept it
        within = shop.change(cid)
        rise_pid = int(within["proposal_id"] or 0)
        named = db.execute(text("select payload ->> 'approver' from proposal where id = :i"), {"i": rise_pid}).scalar()
        if (
            shop.last_buyer_answer.get("status") != "waiting_approval" or within["status"] != "waiting_approval"
            or named != rise_person or D(1) > tolerance
        ):  # fmt: skip
            bad.append(f"{kind}: a 1% rise accepted by the buyer {buyer}: {shop.last_buyer_answer}, named {named}")

        for no in (held, plain, sent, deputy):
            shop.mark(no, kind, pm)

        price_before = D(db.execute(text("select unit_price from po_line where po_no = :n"), {"n": sent}).scalar_one())
        paths = {
            "approve the order": (approve(plain, pm), lambda: shop.po(plain)["status"] == "submitted"
                                  and shop.steps(plain) == want_plain),
            "accept the price above the estimate": (
                shop.attempt(lambda: proposals.approve(db, hold_pid, shop.login[pm])),
                lambda: shop.proposal(hold_pid) == "pending" and shop.po(held)["status"] == "draft",
            ),
            "accept the supplier's price rise": (
                shop.attempt(lambda: confirmation.decide(db, shop.slug, cid, shop.login[pm], accept=True)),
                lambda: shop.proposal(rise_pid) == "pending" and shop.change(cid)["status"] == "waiting_approval"
                and D(db.execute(text("select unit_price from po_line where po_no = :n"), {"n": sent}).scalar_one())
                == price_before,
            ),
            "approve as the director's deputy": (approve(deputy, pm), lambda: shop.po(deputy)["status"] == "submitted"),
        }  # fmt: skip
        for path, (verdict, unchanged) in paths.items():
            tried += 1
            if verdict == "refused" and unchanged():
                refused += 1
            else:
                bad.append(f"{pm} as {kind}, '{path}': {verdict}; left as it was: {unchanged()}")

        # the chain skips the maker and goes up a tier; the deputy who made it does not stand in
        shop.resubmit(plain)
        want_up = [(p, None) for p in chain_oracle(mid, {buyer, pm})[1]]
        if shop.steps(plain) != want_up:
            bad.append(f"{kind}: {plain} submitted again goes to {shop.steps(plain)}, the oracle {want_up}")
        shop.resubmit(deputy)
        if shop.steps(deputy) != [(p, None) for p in chain_oracle(high, {buyer, pm})[1]]:
            bad.append(f"{kind}: {deputy} submitted again: {shop.steps(deputy)}; the deputy {pm} made it, so none")
        accepted = shop.attempt(lambda: proposals.approve(db, hold_pid, shop.login[director]))
        want_held = [(p, None) for p in chain_oracle(held_price, {buyer, pm})[1]]
        if accepted != "done" or shop.po(held)["status"] != "submitted" or shop.steps(held) != want_held:
            bad.append(
                f"{kind}: the director accepting {held}'s price: {accepted}, then {shop.steps(held)}, oracle {want_held}"
            )
        return refused, tried

    counts = [one_kind(kind) for kind in KINDS]
    refused, tried = sum(c[0] for c in counts), sum(c[1] for c in counts)
    out.metrics["maker refused on every path (kinds x paths)"] = f"{refused} of {tried}"
    out.metrics["ways of making an order checked"] = ", ".join(KINDS)

    # 2. the band with no approver: an order that is not touchless goes to a person
    low = shop.order(str(small))
    got = shop.po(low)
    want_rule, want_people = chain_oracle(small, {buyer})
    out.metrics["no-approver band, not touchless: status / rule / approvers"] = (
        f"{got['status']} / {got['rule_code']} / {[a for a, _ in shop.steps(low)]}"
    )
    if (
        got["status"] != "submitted"
        or got["touchless"]
        or got["rule_code"] != want_rule
        or [a for a, _ in shop.steps(low)] != want_people
    ):
        bad.append(
            f"{low} ({small}): {got['status']}, {got['rule_code']}, {shop.steps(low)}; oracle {want_rule} {want_people}"
        )

    # 3. real makers, from the start: the category's buyer is the purchase manager; the purchase manager submits
    db.execute(text("update item_category set buyer_user = :p where code = :c"), {"p": pm, "c": CATEGORY})
    db.commit()
    own_low, own_mid = shop.order(str(small)), shop.order(str(mid))
    db.execute(text("update item_category set buyer_user = :b where code = :c"), {"b": buyer, "c": CATEGORY})
    db.commit()
    for no, value in ((own_low, small), (own_mid, mid)):
        want = [(p, None) for p in chain_oracle(value, {pm})[1]]
        if shop.po(no)["buyer"] != pm or shop.steps(no) != want:
            bad.append(f"{no} bought by {pm}: buyer {shop.po(no)['buyer']}, chain {shop.steps(no)}, oracle {want}")
    submitted_by_pm = shop.order(str(mid))
    from p2p.platform import sod

    shop.resubmit(submitted_by_pm, sod.user(shop.login[pm]))
    want = [(p, None) for p in chain_oracle(mid, {buyer, pm})[1]]
    got_chain = shop.steps(submitted_by_pm)
    if got_chain != want:
        bad.append(
            f"{submitted_by_pm} submitted by {pm} ({pm}'s login calling workflow.submit): the chain is {got_chain}, "
            f"the oracle {want}; {pm} approving it: {approve(submitted_by_pm, pm)}"
        )
    out.metrics["real buyer / real submitter skipped"] = (
        f"{shop.steps(own_mid) == [(p, None) for p in chain_oracle(mid, {pm})[1]]} / {got_chain == want}"
    )

    # 4. accepting a price is not making the order: the purchase manager accepts, then approves
    control: dict[str, str] = {}
    held = shop.order(str(held_price), str(mid))
    pid = int(shop.po(held)["hold_proposal_id"])
    control["accepts the held price"] = shop.attempt(lambda: proposals.approve(db, pid, shop.login[pm]))
    control["then is the order's approver"] = (
        "done" if shop.steps(held) == [(p, None) for p in chain_oracle(held_price, {buyer})[1]] else "no"
    )
    control["then approves it"] = approve(held, pm)
    cid = shop.send_with_price_rise(held, str(tolerance + 1))  # above the tolerance: the price rise person decides
    control["accepts the supplier's price rise"] = shop.attempt(
        lambda: confirmation.decide(db, shop.slug, cid, shop.login[pm], accept=True)
    )
    after = shop.po(held)
    new_total = D(after["total"])
    control["the order is back for approval (version 2)"] = (
        "done" if (after["status"], after["version"]) == ("submitted", 2) else f"{after['status']} v{after['version']}"
    )
    chain_now, makers_now = shop.steps(held), sorted(workflow.makers(db, shop.po(held)))
    verdict = approve(held, pm)
    want_now = [(p, None) for p in chain_oracle(new_total, {buyer})[1]]
    control["and the acceptor approves it"] = (
        "done" if verdict == "done" and chain_now == want_now
        else f"chain {chain_now} (oracle {want_now}), approving: {verdict}"
    )  # fmt: skip
    control["the acceptor is not a maker"] = "done" if pm not in makers_now else f"makers {makers_now}"
    deputy = shop.order(str(high))
    control["a deputy who did not make the order stands in"] = approve(deputy, pm)
    out.metrics["accepting a price leaves the acceptor free to approve"] = (
        f"{sum(v == 'done' for v in control.values())} of {len(control)}"
    )
    for name, verdict in control.items():
        if verdict != "done":
            bad.append(f"control: {pm} {name}: {verdict}")

    for b in bad:
        out.find("high", b)
    out.metrics["mutation checks (rule broken on purpose: caught?)"] = _mutations(shop, approve, out)
    return out


def _mutations(shop: Shop, approve: Any, out: Outcome) -> dict[str, str]:
    from p2p.orders import confirmation, workflow
    from p2p.orders import service as orders
    from p2p.platform import proposals

    db, pm = shop.db, shop.pm
    results: dict[str, str] = {}

    # who awarded the quotation is not counted as a maker (only the buyer on the order is)
    saved = workflow.makers
    workflow.makers = lambda db_, po: frozenset({str(po["buyer"])} if po["buyer"] else set())
    try:
        no = shop.order("200000.00")
        shop.mark(no, "awarder", pm)
        results["the awarder approves"] = "caught" if approve(no, pm) == "done" else "NOT caught"
    finally:
        workflow.makers = saved

    # a buyer may accept a price rise within the tolerance on their own order
    saved_rise = confirmation._maker_raises_price
    confirmation._maker_raises_price = lambda *a: False
    try:
        no = shop.order("200000.00")
        approve(no, pm)
        shop.send_with_price_rise(no, "1")
        caught = shop.last_buyer_answer.get("status") == "accepted"
        results["the buyer accepts a rise within the tolerance"] = "caught" if caught else "NOT caught"
    finally:
        confirmation._maker_raises_price = saved_rise

    # accepting the price above the estimate has no guard
    saved_guard, saved_check = proposals.GUARDS["po.accept_variance"], orders._may_accept_variance
    proposals.GUARDS["po.accept_variance"] = lambda *a: None
    orders._may_accept_variance = lambda *a: None
    try:
        no = shop.order("250000.00", "200000.00")
        pid = int(shop.po(no)["hold_proposal_id"])
        shop.mark(no, "route changer", pm)
        done = shop.attempt(lambda: proposals.approve(db, pid, shop.login[pm]))
        results["a maker accepts the price above the estimate"] = "caught" if done == "done" else "NOT caught"
    finally:
        proposals.GUARDS["po.accept_variance"], orders._may_accept_variance = saved_guard, saved_check
    for name, verdict in results.items():
        if verdict != "caught":
            out.find("high", f"mutation '{name}' was not caught: the eval cannot tell this rule is broken")
    return results
