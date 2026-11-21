"""E65: a moved start date, and new lines for a closed material request (task 6.3, OP-11, DF-19), written without the
implementation.

Independence: written from the block 1 spec (sections 3.1 rule 8, 4.1, 4.3, 5.3, 6.4 B4, 8), the plan and the dataset
only. A joiner's start date is part of the ERP key, so the ERP moves admin's two December joiners to 30 November by
cancelling the old row and sending a new one in the same file. The oracle (`block1.Plant`) replays the base state B0 and
the file row by row: the cancel releases admin's kit stock and cuts its licence request to nothing; the new row's lines
belong to a plan and department whose material request is now cancelled, so they get a NEW material request (not
skipped, DF-19); at the end of the import the released stock goes at once to the open lines earliest need-by first, so
the new request (30 November) takes all of it; engineering (1 December) is unchanged; the next store check buys the
two licences, needed by 30 November.

The same scenario is then loaded again on a fresh company from the scenario folder with `p2p.cli import demand` (a dry
run first): a folder import and the file-by-file upload end in the same state. The other company sees none of it.
Mutations: new lines for a cancelled material request skipped (DF-19); the moved row loaded as extra demand without
the cancel.
"""

import re
from datetime import date
from decimal import Decimal
from typing import Any

import block1 as b
import env
import lib
from lib import Outcome

ID = "E65"
TITLE = "A moved start date cancels the old request and makes a new one that takes the released stock first"
REQUIREMENT = (
    "Task 6.3, OP-11, DF-19 (block 1 spec 3.1 rule 8, 4.1-4.3, 5.3, 6.4 B4): a joiner row cancelled and re-sent with a "
    "new start date cancels the old material request and cuts its open purchase request (emptied: cancelled); the "
    "new row's lines get a new material request (listed in the import's report), which takes the released stock at "
    "once, earliest need-by first; the next store check buys what is still short by the new date; a folder import and "
    "file-by-file uploads end the same; a second import changes nothing; tenant isolation"
)
PREFIX = "e65"
# a dry run says "would load" (the wording before block 1; spec 5.3 is silent on the dry run): review of 21 Nov
SUMMARY = re.compile(r"(?:loaded|would load) (\d+) rows?; (\d+) changed; (\d+) loaded before and unchanged; (\d+) left out")


def run(ctx: Any) -> Outcome:
    env.forget_tenants(PREFIX)
    try:
        return _run(ctx)
    finally:
        env.forget_tenants(PREFIX)


def state(co: b.Company) -> dict[str, Any]:
    """What a company holds, by material request label and item (numbers that differ between companies left out)."""
    obs = {k: {f: str(v.get(f)) for f in ("need", "reserved", "issued", "requested", "open", "status", "mr_status")}
           for k, v in co.observe().items()}  # fmt: skip
    requests = sorted((str(s), str(t)) for s, t in co.q("select status, total_value from request"))
    lines = sorted(
        (x["item"], str(b.D(x["qty"])), x["status"], str(x["need_by"]))
        for label, _, _ in co.mr_labels()
        for x in b.lines_buying(co, label)
    )
    return {"lines": obs, "requests": requests, "request_lines": lines}


def _run(ctx: Any) -> Outcome:  # noqa: C901 - one scenario, step by step
    from fastapi.testclient import TestClient
    from p2p.main import app

    out = Outcome(True)
    check = b.Checker(out)
    lib.capture_mail()
    client = TestClient(app)
    apex = b.Company("apex", PREFIX, client)
    b.start(apex, check)
    bw = b.Company("brightwave", PREFIX, client)
    plant, rank = b.start(bw, check)
    b.check_base(check, bw, plant, b.SPEC_B0, b.SPEC_B0_VALUES)
    plant.mark_buyer(bw.with_buyer())
    raw, parsed = b.scenario_files(bw, check, "B4")
    if not raw:
        return out
    folder = bw.scenario("B4")
    apex_before = apex.snapshot()

    # ---- 1. the file uploaded on the Material requests screen's API --------------------------------------------
    before = plant.observe()
    eng_before = sorted(bw.q("select item_code, reserved_qty::text, need_qty::text from material_request_line l join "
                             "material_request m on m.no = l.mr_no where m.order_no = :o", o=b.ENG))  # fmt: skip
    adm_requests = bw.request_of(b.ADM)
    audit0, tasks0 = b.audit_counts(bw), b.task_ids(bw)
    answers = bw.import_files(raw)
    summary = b.summary_of(answers)
    eff = plant.apply(parsed)
    b.check_import(check, bw, "B4", plant, eff, summary, audit0, tasks0)
    b.agree(check, "B4", plant, bw)
    after_import = plant.observe()
    labels = {label: (no, status) for label, no, status in bw.mr_labels()}
    old, new = labels.get(b.ADM), labels.get(b.ADM + "#2")
    check(old is not None and old[1] == "cancelled", f"B4: admin's first material request is {old}, expected cancelled")
    if check(new is not None, "B4: no new material request for admin's moved joiners (the new lines were skipped)"):
        head = bw.q("select need_by, status, department_code from material_request where no = :n", n=new[0])
        check(
            b.day(head[0][0]) == date(2026, 11, 30) and head[0][1] == "open" and head[0][2] == "ADM",
            f"B4: the new material request is {head}, expected open, ADM, needed by 30 Nov",
        )
        listed = [str(m) for a in answers for m in a.get("material_requests") or []]
        check(any(new[0] in m for m in listed), f"B4: the import's report lists material requests {listed}, not the "
              f"new {new[0]} (spec 3.1 rule 8)")  # fmt: skip
    took = {i: v["reserved"] for (m, i), v in after_import.items() if m == b.ADM + "#2"}
    spec_took = {i: Decimal(2) for i in ("LP-01", "MN-01", "DK-01", "HS-01", "KB-01")} | {"SW-01": b.Z}
    check(took == spec_took, f"B4: the oracle's new request takes {took} (self-check: 2 of each kit item)", "medium")
    eng_after = sorted(bw.q("select item_code, reserved_qty::text, need_qty::text from material_request_line l join "
                            "material_request m on m.no = l.mr_no where m.order_no = :o", o=b.ENG))  # fmt: skip
    check(eng_after == eng_before, "B4: engineering's lines changed (needed later, they keep what they had)")
    check(
        all(bw.request(n).get("status") == "cancelled" for n in adm_requests),
        f"B4: admin's licence request {adm_requests} should be cut to nothing and cancelled",
    )
    # ---- 2. the next store check buys the licences by the new date ------------------------------------------------
    check(bw.store_check().status_code == 200, "B4: Run store check failed")
    made = plant.store_check()
    b.agree(check, "B4 after the next store check", plant, bw)
    check(made == {(b.ADM + "#2", "SW-01"): Decimal(2)}, f"B4: the oracle's next check buys {made} (self-check)",
          "medium")  # fmt: skip
    sw = [x for x in b.lines_buying(bw, b.ADM + "#2", "SW-01") if x["status"] != "cancelled"]
    check(
        len(sw) == 1 and b.D(sw[0]["buys"]) == 2 and b.day(sw[0]["need_by"]) == date(2026, 11, 30),
        f"B4: the next store check buys {sw} for the new request's licences, expected 2 needed by 30 Nov",
    )
    for name, rules in (
        ("new lines for a cancelled material request skipped (DF-19)", b.Rules(skip_closed=True)),
        ("the moved row loaded as extra demand without the cancel", b.Rules(ignore_cancel=True)),
    ):
        wrong = b.Plant(bw.dataset, rules, rank)
        wrong.store_check(tag="first")
        wrong.mark_buyer(bw.with_buyer())
        wrong.apply(parsed)
        wrong.store_check()
        b.mutation(check, out, name, plant.observe(), wrong.observe())
    b.compare_truth(check, bw, "B4", before, [after_import, plant.observe()], eff)
    b.check_again(check, bw, "B4", raw, eff)
    b.isolation(check, bw, apex, {"JP-2026-12", b.ADM, b.ENG}, apex_before)
    by_upload = state(bw)

    # ---- 3. the same scenario from its folder through the CLI, on a fresh company ------------------------------
    bw.reset()
    plant2, _ = b.start(bw, check)
    snap = bw.snapshot()
    code, printed = b.cli("import", "demand", bw.slug, str(folder), "--dry-run")
    check(code == 0, f"CLI dry run exited {code}: {printed[-300:]}")
    check(SUMMARY.search(printed) is not None, f"the CLI dry run printed no summary line: {printed[-300:]}", "medium")
    check(bw.snapshot() == snap, "the CLI dry run changed something")
    code, printed = b.cli("import", "demand", bw.slug, str(folder))
    m = SUMMARY.search(printed)
    want = (0, len(eff.changes), 0, len(eff.refused))
    rows_in = sum(len(v) for v in parsed.values())
    got = tuple(int(x) for x in m.groups()) if m else None
    check(code == 0 and got is not None and got[1:] == want[1:] and got[0] in (0, rows_in - want[1]), f"the CLI folder "
          f"import exited {code} and printed {got}, expected changed {want[1]} and nothing left out")  # fmt: skip
    check("JP-2026-12" in printed, "the CLI prints no line for the change", "medium")
    check(bw.store_check().status_code == 200, "Run store check after the CLI import failed")
    by_folder = state(bw)
    differ = [k for k in by_upload if by_upload[k] != by_folder[k]]
    check(not differ, f"a folder import and file-by-file uploads end differently in {differ}: "
          f"{[(k, by_upload[k], by_folder[k]) for k in differ][:1]}")  # fmt: skip

    out.metrics.update({"checks": check.done, "failed": len(check.failed)})
    out.notes += [
        "Spec gap: whether the CLI's 'loaded N rows' counts a row that only cancels (and the new row that replaces it) "
        "is not said; the new row is accepted as loaded 0 or 1 there, the changed and left-out counts exactly.",
        "Not exercised: new lines for an issued material request (the same DF-19 rule; no spec scenario issues a "
        "whole request before a new line arrives).",
    ]
    return out
