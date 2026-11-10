"""E08: a decision stays reproducible against the rule version it used, even after the rule table changes."""

from datetime import date

import lib
import pipeline
from lib import Outcome, pct
from sqlalchemy import text

ID = "E08"
TITLE = "Decisions stay reproducible when the rules change"
REQUIREMENT = "Rule 8: rules and rates are versioned data; a decision stays reproducible against the version it used"


def run(ctx) -> Outcome:
    from p2p.approvals import rules, workflow
    from p2p.platform import sod

    out = Outcome(True)
    state = pipeline.get_state(ctx)
    world, db = state.world, state.world.db

    decided = db.execute(
        text(
            "select no, total_value, rule_code, rule_version, status from request where approval_round >= 1 order by no"
        )
    ).all()
    missing = [no for no, _, code, ver, _ in decided if code is None or ver is None]
    if missing:
        out.find(
            "high",
            f"{len(missing)} submitted request(s) do not record the rule and version they were decided under, e.g. {missing[0]}",
        )
    po_missing = db.execute(
        text(
            "select count(*) from po where approval_round >= 1 and (rule_code is null or rule_version is null)"
        )
    ).scalar_one()
    if po_missing:
        out.find(
            "high", f"{po_missing} submitted order(s) do not record their rule version"
        )

    def band_holds() -> tuple[int, int]:
        ok = bad = 0
        for no, value, code, ver, _ in decided:
            row = db.execute(
                text(
                    "select min_value, max_value from approval_rule where rule_code = :c and version = :v"
                ),
                {"c": code, "v": ver},
            ).first()
            if row and row[0] <= value <= row[1]:
                ok += 1
            else:
                bad += 1
        return ok, bad

    before_ok, before_bad = band_holds()
    snapshot = db.execute(
        text(
            "select rule_code, version, min_value, max_value, approver_chain, effective_from, effective_to from approval_rule order by rule_code, version"
        )
    ).all()

    # the company changes its rules: version 3 of every request rule starts tomorrow, with a different chain and band
    switch = date(2026, 11, 7)
    for r in (
        db.execute(
            text(
                "select * from approval_rule where document = 'request' and effective_to is null"
            )
        )
        .mappings()
        .all()
    ):
        db.execute(
            text("update approval_rule set effective_to = :d where id = :i"),
            {"d": date(2026, 11, 6), "i": r["id"]},
        )
        db.execute(
            text(
                "insert into approval_rule (rule_code, document, min_value, max_value, category_code, department_code, spend_type, approver_chain, version, effective_from) "
                "values (:c, 'request', :lo, :hi, 'ANY', 'ANY', :s, 'director', :v, :f)"
            ),
            {
                "c": r["rule_code"],
                "lo": r["min_value"],
                "hi": r["max_value"],
                "s": r["spend_type"],
                "v": r["version"] + 1,
                "f": switch,
            },
        )
    db.commit()
    after_ok, after_bad = band_holds()
    unchanged = all(
        db.execute(
            text(
                "select min_value, max_value, approver_chain from approval_rule where rule_code = :c and version = :v"
            ),
            {"c": s[0], "v": s[1]},
        ).one()
        == (s[2], s[3], s[4])
        for s in snapshot
    )
    same_requests = db.execute(
        text(
            "select count(*) from request where approval_round >= 1 and rule_version is not null"
        )
    ).scalar_one() == len(decided)

    # evaluating a past date still finds the old version; a new submission finds the new one
    reproduced = total = 0
    for no, value, code, ver, _ in decided:
        total += 1
        try:
            again = rules.find_rule(
                db,
                "request",
                value,
                lib.TODAY,
                spend_type="opex",
                category=None,
                department="LOG",
            )
        except rules.ApprovalRuleError:
            continue
        reproduced += (again.code, again.version) == (code, ver)
    db.execute(
        text(
            "insert into request (no, department_code, ship_to_location, type, channel, status, total_value) values ('EV-NEW', 'LOG', 'WH-BLR', 'goods', 'planning', 'draft', 20000)"
        )
    )
    db.execute(
        text(
            "insert into request_line (request_no, line_no, free_text, qty, status) values ('EV-NEW', 1, 'x', 1, 'open')"
        )
    )
    db.commit()
    try:
        result = workflow.submit(db, world.slug, "EV-NEW", sod.user(1), today=switch)
        new_rule = (result.rule_code, result.rule_version)
    except Exception as exc:  # noqa: BLE001
        new_rule = ("error", str(exc)[:80])
    audit_versions = db.execute(
        text(
            "select count(*) from audit_event where action in ('request.approve', 'request.auto_approve') and rule_version is null"
        )
    ).scalar_one()

    out.metrics = {
        "decisions": len(decided),
        "band holds before / after the change": f"{before_ok}/{before_bad} and {after_ok}/{after_bad}",
        "old rule rows untouched": unchanged,
        "past date reproduces the old version %": pct(reproduced, total),
        "a new submission uses": new_rule,
        "approval audit rows without a rule version": audit_versions,
    }
    if before_bad or after_bad:
        out.find(
            "high",
            f"{before_bad + after_bad} decision(s) no longer sit inside the band of the rule version they recorded",
        )
    if not unchanged:
        out.find(
            "high",
            "changing the rule table altered a version that decisions were made under",
        )
    if not same_requests:
        out.find(
            "high", "changing the rule table altered what decided requests recorded"
        )
    if reproduced != total:
        out.find(
            "high",
            f"{total - reproduced} decision(s) are not reproduced by evaluating the rules for the date they were made",
        )
    if new_rule[1] != 3 and new_rule[0] != "REQ-02":
        out.find(
            "medium",
            f"a request submitted after the change did not use the new rule version: {new_rule}",
        )
    if audit_versions:
        out.find(
            "medium",
            f"{audit_versions} approval audit row(s) do not say which rule version they used",
        )
    return out
