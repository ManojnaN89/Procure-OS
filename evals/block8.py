"""Shared pieces of the block 8 evals (E136-E143): supplier confirmation (flow step 8). The supplier's answer needs the
signed link plus a one-time code to the registered contact (D53), the acknowledgement of the exact version, one
delivery date per line, the supplier's change as the buyer's decision, the supplier agent on versions and lines
(DF-27), chasing per version on the delivery place's calendar, late lines downstream, and the tenant boundary. Written
from the block 8 spec, the plan and the dataset only, never from the implementation.

Two halves, as in `block7`:

- **The oracle** (`Oracle8`, built on `block7.Oracle7`): the amounts of the scenario orders in exact fractions (taxable =
  quantity x price, CGST = SGST = taxable x half the rate, each half up to paise, inside the state), the approval band
  and its person (`block4.Oracle.approver`), working days on the delivery place's own calendar (`location.csv`
  `working_days` and `holiday_scope` over the holiday files: PLT-CKN works Mon-Sat and is closed on 24 Nov and 25 Dec
  2026), the acknowledgement due day, the chaser days and the overdue task day, the delivery reminder days per line, the
  refusal words of a line date (spec 8.5.1), and which versions supplier scoring counts (spec 8.8.7). `SPEC` holds the
  spec's own figures (section 9) as a self-check, and `truth/truth_supplier_confirmation.csv` (the generator's own
  arithmetic, spec 3.1) is compared figure by figure.
- **The platform** (`Co8`, built on `block7.Co7`): a demo company made by `demo reset`, its people signed in with their
  own logins, the supplier's no-login pages (`/supplier/{token}`, `/code`, `/current`), the API section 8 names, the
  worker run in this process, mail caught in this process (the supplier reads its one-time code from the captured
  mail, as a real supplier reads it in its mailbox), incoming supplier mail stored through the mailbox's own
  `emails.ingest` and read by the worker with a scripted model (`aif.Model`, never a live one unless `--live`), the
  daily chaser job run at a chosen business date, and read-only views of the tables section 8 lets an eval read.
  Clock set-up the scenarios need (a send placed on its scenario day when the platform stamped the machine's clock,
  a code made older) is written as the owner and said in the eval's notes.

Where the spec leaves a shape open (a list wrapped in an object or not; a person as code, user id, email or name; a
date as ISO text or words; the reading a scripted model returns) the readers accept every reasonable shape and say so
in a comment.
"""

import html
import json
import re
import uuid
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from email.message import EmailMessage
from email.utils import format_datetime
from fractions import Fraction
from typing import Any

import aif
import block1 as b
import block4 as k
import block7 as t
import lib
from block1 import D, js
from sqlalchemy import text

TODAY = t.TODAY  # Mon 23 Nov 2026, the scenarios' first business date (spec 9)
IST = b.IST
LOC = "PLT-CKN"
SUP = "SUP-001"  # Deccan Castings, CNT-0001
SALES = "sales@deccancastings.example"  # SUP-001's primary contact
ORDERS = "orders@deccancastings.example"  # the second contact spec 3.1 adds
AUTHSERV = "mx.apexpumps.example"
CONTRACT = "CNT-0001"

NOV25, NOV26, NOV27, NOV30 = date(2026, 11, 25), date(2026, 11, 26), date(2026, 11, 27), date(2026, 11, 30)
DEC3, DEC4, DEC7, DEC8, DEC9 = date(2026, 12, 3), date(2026, 12, 4), date(2026, 12, 7), date(2026, 12, 8), date(2026, 12, 9)
DEC10, DEC11, DEC12, DEC14, DEC15 = date(2026, 12, 10), date(2026, 12, 11), date(2026, 12, 12), date(2026, 12, 14), date(2026, 12, 15)
DEC21 = date(2026, 12, 21)

# P (spec 9): production@ asks at PLT-CKN, sourced on CNT-0001, one order to SUP-001 (line number, item, qty, need-by)
P_LINES = ((1, "RM-02", 100, DEC7), (2, "RM-03", 200, DEC7), (3, "RM-05", 150, DEC14))
P_PRICES = {"RM-02": "480.00", "RM-03": "220.00", "RM-05": "180.00"}
# Q (spec 9): one line to SUP-006 for SP-01 20 NOS at PLT-CKN, need-by Thu 3 Dec
Q_ITEM, Q_QTY, Q_NEED, Q_SUP = "SP-01", 20, DEC3, "SUP-006"
REFUSED = (400, 403, 404, 409, 410, 422)

# section 9's figures: the oracle must give the same, else the eval or the spec is wrong
SPEC: dict[str, dict[str, str]] = {
    "P": {"taxable": "119000.00", "cgst": "6095.00", "sgst": "6095.00", "igst": "0.00", "total": "131190.00",
          "rule": "PO-02", "approver": "U08", "line1_taxable": "48000.00", "line1_cgst": "4320.00",
          "line2_taxable": "44000.00", "line2_cgst": "1100.00", "line3_taxable": "27000.00", "line3_cgst": "675.00"},
    "K3": {"v2_taxable": "117200.00", "v2_cgst": "6050.00", "v2_sgst": "6050.00", "v2_total": "129300.00",
           "v2_line3_taxable": "25200.00", "v1_due": "2026-11-26", "v2_due": "2026-11-27"},
    "K4": {"before_line1": "2026-12-04", "before_line2": "2026-12-07", "before_line3": "2026-12-11",
           "overdue_line2_1": "2026-12-10", "overdue_line2_2": "2026-12-14", "overdue_line2_3": "2026-12-17"},
    "K7": {"ack_due": "2026-11-28", "chaser_1": "2026-11-30", "chaser_2": "2026-12-03", "chaser_3": "2026-12-07",
           "overdue_task": "2026-12-10"},
    "K7b": {"v2_due": "2026-12-10", "v2_chaser_1": "2026-12-11"},
}  # fmt: skip


# ---- words and numbers -------------------------------------------------------------------------------------------------


def long_date(d: date) -> str:
    return k.long_date(d)  # '07 Dec 2026', the spec's <dd Mon yyyy>


def count(n: int, noun: str) -> str:
    """'1 day', '4 days' (the platform's `count(3, 'line')`, written again here)."""
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def norm(x: Any) -> str:
    return t.norm(x)


def page(r: Any) -> str:
    """A page's words: tags out, entities read, spaces as one (so 'Pvt Ltd&#x27;s' reads as "Pvt Ltd's")."""
    raw = str(getattr(r, "text", r) or "")
    raw = re.sub(r"<(style|script)[^>]*>.*?</\1>", " ", raw, flags=re.DOTALL | re.IGNORECASE)
    return norm(html.unescape(re.sub(r"<[^>]+>", " ", raw)))


def says(r: Any, words: str) -> bool:
    return norm(words) in page(r)


def masked(address: str) -> str:
    """Spec 8.2.2: first letter, ****, @domain."""
    local, _, domain = address.partition("@")
    return f"{local[:1]}****@{domain}"


def paise(x: Fraction) -> Decimal:
    """Half up to paise, in exact fractions."""
    cents = x * 100
    whole = int(cents)
    if cents - whole >= Fraction(1, 2):
        whole += 1
    return (Decimal(whole) / 100).quantize(Decimal("0.01"))


def iso(x: Any) -> str | None:
    """A date as ISO text from a date, a timestamp, ISO text or '07 Dec 2026'."""
    if x in (None, ""):
        return None
    if isinstance(x, datetime):
        return x.astimezone(IST).date().isoformat()
    if isinstance(x, date):
        return x.isoformat()
    s_ = str(x).strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}([T ].*)?", s_):
        return s_[:10]
    for fmt in ("%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(s_, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def ist_day(x: Any) -> date | None:
    if isinstance(x, datetime):
        return x.astimezone(IST).date()
    return b.day(x) if x not in (None, "") else None


def as_rows(value: Any, *keys: str) -> list[dict[str, Any]]:
    return t.as_rows(value, *keys)


def blob(x: Any) -> str:
    """Anything as one lower-case text (a payload, a row, a list of rows)."""
    return html.unescape(json.dumps(k.as_json(x), default=str, ensure_ascii=False)).lower()


# ======================================================================================================================
# The oracle
# ======================================================================================================================


class Oracle8(t.Oracle7):
    """Amounts in exact fractions, the delivery place's calendar, due days, chasers, reminders and refusal words."""

    def __init__(self, co: "Co8") -> None:
        super().__init__(co)
        self.loc_rows = {r["code"]: r for r in co.rows("master", "location")}

    # ---- calendars ----
    def cal(self, loc: str = LOC) -> b.Calendar:
        """The delivery place's own calendar (spec 8.6, OP-8): its working days and holiday scope."""
        r = self.loc_rows[loc]
        return b.Calendar(frozenset(b.open_days(r.get("working_days", ""))), frozenset(b.holidays(r.get("holiday_scope", ""))))

    def company_cal(self) -> b.Calendar:
        """The planted mutation: the company default calendar of today, Mon-Fri with the central holidays."""
        return b.Calendar(frozenset(range(5)), frozenset(b.holidays("central")))

    def setting(self, key: str, default: str) -> int:
        return int(D(self.settings.get(key, default)))

    # ---- money (exact fractions) ----
    def line_amounts(self, item: str, qty: Any, price: Any, supplier: str = SUP, loc: str = LOC) -> dict[str, Decimal]:
        taxable_f = Fraction(str(qty)) * Fraction(str(price))
        rate = Fraction(str(self.gst_rate(item)))
        taxable = paise(taxable_f)
        if self.suppliers[supplier]["state_code"] == self.loc_rows[loc]["state_code"]:
            half = paise(taxable_f * rate / 200)
            return {"taxable": taxable, "cgst": half, "sgst": half, "igst": Decimal("0.00")}
        return {"taxable": taxable, "cgst": Decimal("0.00"), "sgst": Decimal("0.00"), "igst": paise(taxable_f * rate / 100)}

    def order(self, lines: list[tuple[int, str, Any, Any]], supplier: str = SUP, loc: str = LOC) -> dict[str, Any]:
        """{lines: {n: amounts}, taxable, cgst, sgst, igst, total} of (line, item, qty, price) rows."""
        per = {n: self.line_amounts(item, qty, price, supplier, loc) for n, item, qty, price in lines}
        out: dict[str, Any] = {"lines": per}
        for x in ("taxable", "cgst", "sgst", "igst"):
            out[x] = sum((a[x] for a in per.values()), Decimal("0.00"))
        out["total"] = out["taxable"] + out["cgst"] + out["sgst"] + out["igst"]
        return out

    def p(self, qty3: int = 150) -> dict[str, Any]:
        """P (or a revision of its line 3) at the contract prices of the dataset (`price_record.csv`, CNT-0001)."""
        rows = []
        for n, item, qty, _need in P_LINES:
            price = self.contract_price(item, TODAY)
            rows.append((n, item, qty3 if n == 3 else qty, price[0] if price else D(P_PRICES[item])))
        return self.order(rows)

    @staticmethod
    def figures(o: dict[str, Any], prefix: str = "") -> dict[str, Any]:
        out = {f"{prefix}{x}": str(o[x]) for x in ("taxable", "cgst", "sgst", "igst", "total")}
        for n, a in o["lines"].items():
            out.update({f"{prefix}line{n}_{x}": str(v) for x, v in a.items()})
        return out

    # ---- days ----
    def ack_due(self, sent: date, cal: b.Calendar | None = None) -> date:
        """Spec 8.8.2: `supplier_ack_due_days` working days after the version was sent, on the ship-to calendar."""
        return (cal or self.cal()).add(sent, self.setting("supplier_ack_due_days", "2"))

    def chasers(self, sent: date, cal: b.Calendar | None = None, *, calendar_days: bool = False,
                most: int | None = None) -> tuple[list[date], date]:  # fmt: skip
        """Spec 8.8.3-4: (the chaser days, the day of the overdue task). The first on the first working day after the due
        day; then every `supplier_chase_days` working days (the mutation: calendar days, the next working day on or
        after); at most `supplier_chase_max`; the task on the day the next would be due."""
        cal = cal or self.cal()
        every = self.setting("supplier_chase_days", "3")
        most = self.setting("supplier_chase_max", "3") if most is None else most
        d = self.ack_due(sent, cal) + timedelta(days=1)
        while not cal.working(d):
            d += timedelta(days=1)
        days = [d]

        def nxt(x: date) -> date:
            if not calendar_days:
                return cal.add(x, every)
            y = x + timedelta(days=every)
            while not cal.working(y):
                y += timedelta(days=1)
            return y

        while len(days) < most:
            days.append(nxt(days[-1]))
        return days, nxt(days[-1])

    def reminders(self, due: date, cal: b.Calendar | None = None) -> list[tuple[date, str, int]]:
        """Delivery reminders about one due date when nothing arrives (the data dictionary's rule, unchanged by block 8
        except for the calendar): one `delivery_reminder_before_days` working days before, then
        `delivery_overdue_after_days` after and every `delivery_overdue_every_days`, at most `delivery_overdue_max`."""
        cal = cal or self.cal()
        before = self.setting("delivery_reminder_before_days", "2")
        after = self.setting("delivery_overdue_after_days", "1")
        every = self.setting("delivery_overdue_every_days", "3")
        most = self.setting("delivery_overdue_max", "3")
        d, n = due, before
        while n:
            d -= timedelta(days=1)
            n -= cal.working(d)
        out = [(d, "before", 1)]
        out += [(cal.add(due, after + i * every), "overdue", i + 1) for i in range(most)]
        return out

    def date_refusal(self, n: int, ordered: date, promised: date, today: date, cal: b.Calendar | None = None,
                     loc: str = LOC) -> str | None:  # fmt: skip
        """Spec 8.5.1 in its order: in the past, not a working day at the delivery place, later than ordered by more
        than `supplier_date_days` (calendar days). None when the date is accepted."""
        cal = cal or self.cal(loc)
        tol = self.date_days()
        if promised < today:
            return f"line {n}: {long_date(promised)} is in the past"
        if not cal.working(promised):
            return f"line {n}: {long_date(promised)} is not a working day at {self.loc_rows[loc]['name']}"
        later = (promised - ordered).days
        if later > tol:
            return (f"line {n}: {long_date(promised)} is {count(later, 'day')} after the date ordered "
                    f"({long_date(ordered)}), more than the {tol} accepted: ask for a change instead")  # fmt: skip
        return None

    def ack_score(self, versions: list[tuple[date, date | None]], as_of: date, cal: b.Calendar | None = None
                  ) -> tuple[int, int]:  # fmt: skip
        """Spec 8.8.7 (OP-21): (counted, on time) over one order's sent versions [(sent, acknowledged or None)], oldest
        first; a version is replaced when the next one is sent. Acknowledged: counted, on time when on or before its due
        day. Unanswered: counted (late) when its due day passed before it was replaced (or before `as_of` while it is
        current); replaced on or before its due day: left out."""
        counted = quick = 0
        for i, (sent, acked) in enumerate(versions):
            due = self.ack_due(sent, cal)
            replaced = versions[i + 1][0] if i + 1 < len(versions) else None
            if acked is not None:
                counted += 1
                quick += acked <= due
            elif (replaced is not None and replaced > due) or (replaced is None and as_of > due):
                counted += 1
        return counted, quick

    # ---- names ----
    def supplier_name(self, code: str) -> str:
        r = self.suppliers[code]
        return r.get("trade_name") or r["legal_name"]

    def item_name(self, item: str) -> str:
        return self.items[item]["name"]


def self_check(check: b.Checker, key: str, mine: dict[str, Any]) -> None:
    """The oracle's figures against section 9's for the keys both name."""
    wrong = []
    for name, want in SPEC[key].items():
        if name not in mine or mine[name] is None:
            continue
        got = mine[name]
        same = D(got) == D(want) if b.dec(want) is not None and b.dec(got) is not None else str(got) == want
        if not same:
            wrong.append(f"{name} {got} (spec {want})")
    check(not wrong, f"oracle self-check {key}: {'; '.join(wrong)}", "medium")


# ---- the truth file -----------------------------------------------------------------------------------------------------

TRUTH = "truth/truth_supplier_confirmation.csv"


def _fig(name: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(name or "").strip().lower()).strip("_")


def _val(x: Any) -> str:
    d = iso(x)
    if d:
        return d
    v = b.dec(str(x).replace(",", "")) if x not in (None, "") else None
    return format(v.normalize(), "f") if v is not None else str(x or "").strip().lower()


def compare_truth(check: b.Checker, co: "Co8", scenario: str, mine: dict[str, Any]) -> int:
    """`truth/truth_supplier_confirmation.csv` (spec 3.1: scenario, figure, value) rows of a scenario against the
    oracle's figures. The spec does not fix the figure names: a row whose name is one of the oracle's is compared by
    name, any other must hold a value the oracle also has. Returns how many rows were compared; the file missing is a
    medium finding (the generator writes it, spec 3.1 item 4)."""
    path = co.file(TRUTH)
    if not check(path is not None, f"the dataset has no {TRUTH} (spec 3.1 item 4: the generator writes it)", "medium"):
        return 0
    rows = [r for r in b.read_csv(path) if r.get("scenario", "").strip().upper() == scenario.upper()]
    if not check(bool(rows), f"truth file: no row of scenario {scenario}", "medium"):
        return 0
    named = {_fig(n): _val(v) for n, v in mine.items()}
    values = set(named.values())
    for r in rows:
        fig, want = _fig(r.get("figure")), _val(r.get("value"))
        if want.upper().startswith("U") and co.code_of(r.get("value")):
            want = _val(co.code_of(r.get("value")))
        if fig in named:
            check(named[fig] == want, f"truth file {scenario} {r.get('figure')}: {r.get('value')}; oracle {named[fig]}",
                  "medium")  # fmt: skip
        else:
            check(want in values, f"truth file {scenario} {r.get('figure')} = {r.get('value')}: no oracle figure of "
                  f"{scenario} has that value ({sorted(values)[:12]} ...)", "medium")  # fmt: skip
    return len(rows)


def data_gaps(check: b.Checker, co: "Co8", *, contacts: bool = False, chase: bool = False) -> None:
    """Spec 3.1 'data first': the generator's second contact and chase settings (each missing one a medium finding)."""
    if contacts:
        rows = co.rows("master", "supplier_contact")
        check(any(r.get("email", "").lower() == ORDERS and r.get("supplier_code") == SUP for r in rows),
              f"the dataset's supplier_contact.csv has no second contact {ORDERS} for {SUP} (spec 3.1 item 1)",
              "medium")  # fmt: skip
    if chase:
        keys = {r["key"] for r in co.rows("master", "setting")}
        missing = [x for x in ("supplier_chase_days", "supplier_chase_max") if x not in keys]
        check(not missing, f"the dataset's setting.csv lacks {missing} (spec 3.1 item 2)", "medium")


# ======================================================================================================================
# The platform
# ======================================================================================================================


def pin(d: date) -> None:
    t.pin(d)


class Co8(t.Co7):
    """A demo company with the block 8 calls."""

    # ---- reading ----
    def lines8(self, no: str) -> list[dict[str, Any]]:
        return self.qd("select * from po_line where po_no = :n order by line_no", n=no)

    def line_dates(self, no: str) -> dict[int, tuple[date | None, date | None]]:
        """{line: (promised_date, need_by)}."""
        return {int(x["line_no"]): (b.day(x.get("promised_date")), b.day(x.get("need_by"))) for x in self.lines8(no)}

    def promised(self, no: str) -> dict[int, date | None]:
        return {n: p for n, (p, _nb) in self.line_dates(no).items()}

    def commitments(self, no: str, after_id: int = 0) -> list[dict[str, Any]]:
        return self.qd("select * from po_commitment where po_no = :n and id > :i order by id", n=no, i=after_id)

    def commit_mark(self) -> int:
        return int(self.one("select coalesce(max(id), 0) from po_commitment") or 0)

    def changes(self, no: str) -> list[dict[str, Any]]:
        return self.qd("select * from po_change where po_no = :n order by id", n=no)

    def codes(self, like: str = "%") -> list[dict[str, Any]]:
        return self.qd("select * from one_time_code where purpose = 'po_answer' and ref like :r order by id", r=like)

    def task_mark(self) -> int:
        return int(self.one("select coalesce(max(id), 0) from task") or 0)

    def new_tasks(self, kind: str, mark: int) -> list[dict[str, Any]]:
        return [x for x in self.tasks(kind) if int(x["id"]) > mark]

    def messages(self, email_id: Any) -> list[dict[str, Any]]:
        return self.qd("select * from supplier_message where email_id = :e order by id", e=int(email_id or 0))

    def fp(self, no: str) -> tuple[Any, ...]:
        """What a refused answer must leave as it was: the order, its lines and dates, its links (used or not), its
        changes, commitments, version rows' acknowledgements and po.acknowledge audits (spec 8.1.1)."""
        p = self.po_row(no)
        return (p.get("version"), p.get("status"), p.get("acknowledged_version"), p.get("acknowledged_at"),
                tuple((x["line_no"], str(x.get("qty")), str(x.get("promised_date")), str(x.get("need_by")), x.get("status"))
                      for x in self.lines8(no)),
                tuple((x["id"], x.get("used_at") is not None) for x in self.links(no)),
                tuple((x["id"], x.get("status")) for x in self.changes(no)), len(self.commitments(no)),
                len(self.audits("po.acknowledge", no)),
                tuple((v, r.get("acknowledged_at") is not None) for v, r in self.po_versions(no).items()))  # fmt: skip

    # ---- mail ----
    def mails_to(self, address: str, since: int, *words: str) -> list[dict[str, Any]]:
        out = []
        for m in self.mails(since):
            if str(m.get("to", "")).lower() != address.lower():
                continue
            words_in = norm(f"{m.get('subject') or ''} {m.get('body') or ''} {m.get('html') or ''}")
            if all(norm(w) in words_in for w in words):
                out.append(m)
        return out

    def code_mails(self, since: int) -> list[dict[str, Any]]:
        """Mails that carry a one-time code: 'code' and six digits, and no link (an order mail carries a pincode)."""
        out = []
        for m in self.mails(since):
            body = f"{m.get('subject') or ''} {m.get('body') or ''}"
            if "code" in body.lower() and re.search(r"\b\d{6}\b", str(m.get("body") or "")) and "/supplier/" not in body:
                out.append(m)
        return out

    @staticmethod
    def code_of_mail(m: dict[str, Any]) -> str | None:
        found = re.search(r"\b(\d{6})\b", str(m.get("body") or ""))
        return found.group(1) if found else None

    def ask_code(self, token: str | None) -> tuple[Any, str | None, list[dict[str, Any]]]:
        """Email me a code: (the page, the code the supplier reads in its mail, the code mails sent)."""
        since = self.mark()
        r = self.link_post(f"/supplier/{token}/code")
        self.work()
        mails = self.code_mails(since)
        return r, (self.code_of_mail(mails[-1]) if mails else None), mails

    def answer(self, token: str | None, action: str, code: str | None = None, **fields: Any) -> Any:
        data = {"action": action, **{x: (v.isoformat() if isinstance(v, date) else str(v)) for x, v in fields.items()}}
        if code is not None:
            data["code"] = code
        r = self.supplier(token or "", **data)
        self.work()
        return r

    @staticmethod
    def date_fields(dates: dict[int, date]) -> dict[str, str]:
        """The accept form's dates (spec 8.5.1: one per open line, filled with the line's date on the order: a browser
        sends the prefilled value of a line the supplier did not touch)."""
        return {f"date_{n}": d.isoformat() for n, d in dates.items()}

    def accept_code(self, check: b.Checker, token: str | None, dates: dict[int, date], label: str,
                    to: str = SALES) -> Any:  # fmt: skip
        """The contact asks for a code, reads it in its mail and accepts with one date per line."""
        r, code, mails = self.ask_code(token)
        check(r.status_code == 200 and code is not None and [str(m.get("to", "")).lower() for m in mails] == [to],
              f"{label}: Email me a code answered {r.status_code}, code mails to "
              f"{[m.get('to') for m in mails]}; expected one to {to}")  # fmt: skip
        return self.answer(token, "accept", code or "000000", note=f"{label}: eval", **self.date_fields(dates))

    def reply_to(self, out_mail: dict[str, Any], no: str) -> str:
        from p2p.platform.emails import plus_address

        return str(out_mail.get("reply_to") or plus_address("po", no))

    # ---- the order's people and acts ----
    def ask_lines(self, check: b.Checker, lines: tuple[tuple[int, str, int, date], ...], label: str, *,
                  by: str | None = None, loc: str = LOC, notes: list[str] | None = None) -> str:  # fmt: skip
        """A request with one need-by per line (`request_line.need_by`), submitted and approved through its chain.
        The request API takes one need-by per request: each line's own date is sent too, and a draft line that did not
        take it is given its date as the owner before submitting (set-up the spec's P needs, said in the notes)."""
        who = by or self.login("production")
        body = {"lines": [{"item_code": item, "qty": str(qty), "need_by": nb.isoformat()} for _n, item, qty, nb in lines],
                "need_by": min(x[3] for x in lines).isoformat(), "location": loc, "reason": f"{label}: eval"}  # fmt: skip
        r = self.call(who, "POST", "/api/requisitions", body)
        no = str(js(r).get("id") or js(r).get("no") or "")
        if not check(r.status_code in (200, 201) and bool(no), f"{label}: the request was not made: {r.status_code} "
                     f"{r.text[:200]}"):  # fmt: skip
            return ""
        want = {n: nb for n, _i, _q, nb in lines}
        got = {int(x["line_no"]): b.day(x.get("need_by")) for x in self.qd(
            "select line_no, need_by from request_line where request_no = :n", n=no)}  # fmt: skip
        if got != want:
            with lib.owner_session(self.slug) as s_:
                for n, nb in want.items():
                    s_.execute(text("update request_line set need_by = :d where request_no = :r and line_no = :n"),
                               {"d": nb, "r": no, "n": n})  # fmt: skip
                s_.commit()
            if notes is not None:
                notes.append(f"{label}: the request API took one need-by for every line; the eval gave the draft's lines "
                             "their own dates as the owner before submitting (spec 9 P has 7 Dec and 14 Dec)")  # fmt: skip
        r = self.call(who, "POST", f"/api/requisitions/{no}/submit", {"override_reason": f"{label}: a separate case"})
        if r.status_code == 409 and "repeat" in r.text.lower():
            self.call(who, "POST", f"/api/requisitions/{no}/duplicate-override", {"reason": f"{label}: a separate case"})
            r = self.call(who, "POST", f"/api/requisitions/{no}/submit", {})
        check(r.status_code == 200, f"{label}: submitting {no} answered {r.status_code} {r.text[:200]}", "medium")
        status = self.approve(no)
        check(status == "approved", f"{label}: request {no} is {status} after its approvers acted, not approved", "medium")
        self.work()
        return no

    def p_order(self, check: b.Checker, label: str, approver: str, notes: list[str] | None = None) -> str:
        """P (spec 9): the request, sourced on CNT-0001 by the worker, one order to SUP-001, approved by `approver` on
        version 1. Its three lines are checked against the spec (a precondition, medium)."""
        req = self.ask_lines(check, P_LINES, label, notes=notes)
        orders = sorted({x["po_no"] for x in self.lines_ordered(req)}) if req else []
        if not check(len(orders) == 1, f"{label}: request {req} gave orders {orders}; the spec's P is one order to {SUP}"):
            return orders[0] if orders else ""
        po = orders[0]
        got = {(int(x["line_no"]), x.get("item_code"), D(x.get("qty")), b.day(x.get("need_by"))) for x in self.lines8(po)}
        want = {(n, item, D(qty), nb) for n, item, qty, nb in P_LINES}
        check(got == want and self.po_row(po).get("supplier_code") == SUP,
              f"{label}: precondition: {po} of {self.po_row(po).get('supplier_code')} has lines {sorted(got, key=str)}; "
              f"the spec's P is {sorted(want, key=str)} on {SUP}", "medium")  # fmt: skip
        k.approve_ok(check, self, approver, po, 1, f"{label} approve version 1")
        return po

    def send8(self, check: b.Checker, no: str, label: str) -> tuple[str | None, dict[str, Any]]:
        token, mail, _lm = self.send_v(check, no, label)
        return token, mail

    def q_order(self, check: b.Checker, label: str) -> tuple[str, str | None, dict[str, Any]]:
        """Q (spec 9): SP-01 20 NOS at PLT-CKN, need-by 3 Dec, to SUP-006, sent on the business date in force. The
        spec does not say how Q is bought: whatever route the platform takes (a safe reorder sent touchless, or a
        quotation the buyer awards to SUP-006 at its 210.00 price on file), the eval follows it to a sent order.
        Returns (order, its respond link, its order mail)."""
        since = self.mark()
        lm = self.link_mark()
        req = self.ask(check, Q_ITEM, Q_QTY, Q_NEED, label)
        orders = sorted({x["po_no"] for x in self.lines_ordered(req)}) if req else []
        if req and not orders:
            rfq = self.rfq_of(req)
            if rfq:
                self.confirm(self.login("buyer"), rfq)
                self.quote6(rfq, Q_SUP, "210.00")
                r = self.award6(rfq, Q_SUP)
                if r.status_code == 409 and "reason" in r.text.lower():
                    self.award6(rfq, Q_SUP, reason=f"{label}: the only spares supplier")
                orders = sorted({x["po_no"] for x in self.lines_ordered(req)})
        if not check(len(orders) == 1, f"{label}: request {req} gave orders {orders}; expected one to {Q_SUP}", "medium"):
            return "", None, {}
        po = orders[0]
        check(self.po_row(po).get("supplier_code") == Q_SUP, f"{label}: {po} is to {self.po_row(po).get('supplier_code')}"
              f"; the spec's Q is to {Q_SUP}", "medium")  # fmt: skip
        status = str(self.po_row(po).get("status"))
        if status == "submitted":
            for step in self.open_steps(po):
                self.approve_po(self.code_of(step.get("approver")) or self.buyer(), po, 1)
                self.work()
        if str(self.po_row(po).get("status")) not in ("sent", "acknowledged"):
            r = self.send(po)
            check(r.status_code == 200, f"{label}: sending {po} answered {r.status_code} {r.text[:200]}", "medium")
        mail = self.last_order_mail(po, since)
        token = self.token_in(mail) if mail else None
        if token is None:
            links = self.respond_links(po, lm)
            check(bool(links), f"{label}: {po} was sent with no respond link", "medium")
        return po, token, mail

    def revise8(self, no: str, line: int, qty: Any = None, need_by: date | None = None, *, version: int,
                reason: str = "plan cut", by: str | None = None) -> Any:  # fmt: skip
        ln: dict[str, Any] = {"line_no": line}
        if qty is not None:
            ln["qty"] = str(qty)
        if need_by is not None:
            ln["need_by"] = need_by.isoformat()
        return self.revise7(no, lines=[ln], version=version, reason=reason, by=by)

    def record_ack(self, no: str, version: Any, lines: dict[int, date] | None, note: str | None, *,
                   by: str | None = None, email_id: Any = None) -> Any:  # fmt: skip
        """Record acknowledgement (spec 8.5.4): version, a date per line, a required note."""
        body: dict[str, Any] = {"version": version}
        if lines is not None:
            body["lines"] = {str(n): d.isoformat() for n, d in lines.items()}
        if note is not None:
            body["note"] = note
        if email_id is not None:
            body["email_id"] = email_id
        r = self.call(by or self.buyer(), "POST", f"/api/purchase-orders/{no}/acknowledge", body)
        self.work()
        return r

    def decide8(self, no: str, change_id: Any, accept: bool, *, by: str | None = None, reason: str | None = None) -> Any:
        """The buyer's decision on one change. Section 8 calls the endpoint 'existing' and names
        /api/purchase-orders/{no}/changes/{id}/decide; the existing one is /api/supplier-changes/{id}/decide (block 0):
        that is tried first, the other when the first is not a route at all."""
        body: dict[str, Any] = {"accept": accept}
        if reason:
            body.update(reason=reason, note=reason)
        who = by or self.buyer()
        r = self.call(who, "POST", f"/api/supplier-changes/{change_id}/decide", body)
        if r.status_code in (404, 405) and r.text.strip() in ('{"detail":"Not Found"}', '{"detail":"Method Not Allowed"}'):
            r = self.call(who, "POST", f"/api/purchase-orders/{no}/changes/{change_id}/decide", body)
        self.work()
        return r

    def resend_link(self, no: str, by: str | None = None) -> Any:
        r = self.call(by or self.buyer(), "POST", f"/api/purchase-orders/{no}/resend-link", {})
        self.work()
        return r

    # ---- clock set-up (the owner) ----
    def place(self, no: str, version: int, column: str, day: date, hour: int = 10) -> bool:
        """Put a version's stamp (`sent_at`, `made_at`, `acknowledged_at`) on the scenario's day when the platform
        stamped the machine's clock instead (a pinned business date is a date, not a clock). po.sent_at follows version
        1's send and po.acknowledged_at the current acknowledgement. Returns whether anything moved."""
        row = self.po_versions(no).get(version, {})
        value = row.get(column)
        if value is None or ist_day(value) == day:
            return False
        at = datetime.combine(day, time(hour), tzinfo=IST)
        with lib.owner_session(self.slug) as s_:
            s_.execute(text(f"update po_version set {column} = :a where po_no = :n and version = :v"),
                       {"a": at, "n": no, "v": version})  # fmt: skip
            if column == "sent_at" and version == 1:
                s_.execute(text("update po set sent_at = :a where no = :n"), {"a": at, "n": no})
            if column == "acknowledged_at":
                s_.execute(text("update po set acknowledged_at = :a where no = :n and acknowledged_version = :v"),
                           {"a": at, "n": no, "v": version})  # fmt: skip
            s_.commit()
        return True

    def age_codes(self, like: str, minutes: int, *, expire: bool = False) -> None:
        """Make the codes of a link older (spec K2: 'the eval moves created_at back'), and optionally expired."""
        with lib.owner_session(self.slug) as s_:
            s_.execute(text("update one_time_code set created_at = created_at - make_interval(mins => :m) "
                            "where purpose = 'po_answer' and ref like :r"), {"m": minutes, "r": like})  # fmt: skip
            if expire:
                s_.execute(text("update one_time_code set expires_at = expires_at - make_interval(mins => :m) "
                                "where purpose = 'po_answer' and ref like :r"), {"m": minutes, "r": like})  # fmt: skip
            s_.commit()

    def ensure_second_contact(self, notes: list[str]) -> None:
        """Spec 3.1 item 1 adds orders@deccancastings.example to SUP-001 (not primary). When the reset did not load it,
        the eval plants it as the owner so the 'code only to the address the link went to' case can tell one contact
        from every contact; the dataset gap itself is a finding (`data_gaps`)."""
        have = self.one("select count(*) from supplier_contact where lower(email) = :e", e=ORDERS)
        if have:
            return
        with lib.owner_session(self.slug) as s_:
            s_.execute(text("insert into supplier_contact (supplier_code, name, role, email, phone, is_primary) values "
                            "(:s, 'Order Desk', 'orders', :e, '+91 00000 90011', false)"), {"s": SUP, "e": ORDERS})  # fmt: skip
            s_.commit()
        notes.append(f"{ORDERS} was not in the reset's contacts: the eval planted it as the owner (spec 3.1 item 1)")

    # ---- jobs at a business date ----
    def chase(self, day: date) -> list[str | None]:
        """The daily chaser job `supplier_chase` (spec 2: 9:45 IST) on this business day, run the way the worker claims
        it; then the outbox. The business date stays pinned to `day`."""
        from p2p.platform import worker

        pin(day)
        at = datetime.combine(day, time(9, 45), tzinfo=IST)
        self.db.commit()
        worker.load_handlers()
        if "supplier_chase" not in worker.HANDLERS:
            return ["no handler for supplier_chase"]
        worker.enqueue(self.slug, "supplier_chase", {}, at)
        errors = aif.run_jobs(self.slug, ["supplier_chase"], now=at + timedelta(minutes=1))
        self.work()
        pin(day)
        self.db.rollback()
        return errors

    def delivery_reminders(self, day: date) -> str | None:
        """Delivery reminders for this business day, called the way E42 calls them (`orders/delivery_reminders.run`,
        the module spec 8.6.1 names); then the outbox."""
        pin(day)
        db = aif.new_session(self)
        error = None
        try:
            from p2p.orders import delivery_reminders

            delivery_reminders.run(db, self.slug, as_of=day)
            db.commit()
        except Exception as exc:  # noqa: BLE001 - recorded as the eval's finding
            db.rollback()
            error = f"{type(exc).__name__}: {str(exc)[:200]}"
        finally:
            db.close()
        self.work()
        pin(day)
        return error

    def alerts_run(self, by: str | None = None) -> Any:
        return self.call(by or self.buyer(), "POST", "/api/alerts/run", {})

    def exception_rows(self, by: str | None = None, query: str = "") -> list[dict[str, Any]]:
        r = self.call(by or self.buyer(), "GET", "/api/exceptions" + query)
        return as_rows(js(r) if r.status_code == 200 else [], "rows", "items")

    def waiting(self, by: str | None = None) -> list[dict[str, Any]]:
        r = self.call(by or self.role("store"), "GET", "/api/receiving/waiting")
        return as_rows(js(r) if r.status_code == 200 else [], "orders", "rows", "items")

    def ack_counts(self, supplier: str, as_of: date) -> dict[str, Any]:
        """Supplier scoring's acknowledgement counts (spec 8.8.7: `orders`, `acks`, `quick_acks`), read from
        `suppliers/scoring.score_all` as E23 reads the scores."""
        from p2p.suppliers import scoring

        db = aif.new_session(self)
        try:
            scores = scoring.score_all(db, as_of)
        finally:
            db.close()
        sc = scores.get(supplier) if isinstance(scores, dict) else None
        hist = getattr(sc, "history", None) if sc is not None else None
        out: dict[str, Any] = {}
        for x in ("orders", "acks", "quick_acks"):
            out[x] = hist.get(x) if isinstance(hist, dict) else getattr(hist, x, None)
        return out

    # ---- mail coming in ----
    def mail_in(self, no: str, out_mail: dict[str, Any], sender: str, body: str, model: aif.Model | None, *,
                auth: tuple[str, ...] = (), thread: bool = True) -> int | None:  # fmt: skip
        """A supplier's reply to an order mail, threaded to it (In-Reply-To and References the order mail's
        Message-ID), stored by the mailbox's own `emails.ingest` and read by the worker (`supplier_message`) with the
        scripted model (`model` None: the real models, `--live`). `auth` are Authentication-Results headers."""
        domain = sender.rpartition("@")[2].strip(">") or "supplier.example"
        msg = EmailMessage()
        for h in auth:
            msg["Authentication-Results"] = h
        msg["From"] = sender
        msg["To"] = self.reply_to(out_mail, no)
        msg["Subject"] = "Re: " + str(out_mail.get("subject") or f"Purchase order {no}")
        msg["Date"] = format_datetime(datetime.now(UTC))
        msg["Message-ID"] = f"<eval-{uuid.uuid4().hex}@{domain}>"
        mid = str(out_mail.get("message_id") or "")
        if thread and mid and mid != "None":
            mid = mid if mid.startswith("<") else f"<{mid}>"
            msg["In-Reply-To"] = mid
            msg["References"] = mid
        msg.set_content(body)
        if model is None:
            email_id = aif.ingest(self, msg.as_bytes())
            self.work()
            aif.run_jobs(self.slug, ["supplier_message"])
            self.work()
            return email_id
        with aif.no_live_models(model):
            email_id = aif.ingest(self, msg.as_bytes())
            self.work()
            aif.run_jobs(self.slug, ["supplier_message"])
            self.work()
        return email_id


def auth_header(result: str, domain: str = "deccancastings.example", server: str = AUTHSERV) -> str:
    """Our own server's Authentication-Results (block 6: trusted only from `mail_authserv_id`)."""
    if result == "pass":
        return f"{server}; spf=pass smtp.mailfrom={domain}; dkim=pass header.d={domain}; dmarc=pass header.from={domain}"
    return f"{server}; spf=pass smtp.mailfrom={domain}; dkim=none; dmarc=fail header.from={domain}"


# ======================================================================================================================
# Checks shared by the evals
# ======================================================================================================================


def amounts_ok(check: b.Checker, co: Co8, no: str, want: dict[str, Any], label: str, sev: str = "high") -> bool:
    """The order's header and per-line amounts against the oracle's."""
    p = co.po_row(no)
    got = {x: D(p.get(x)) for x in ("taxable", "cgst", "sgst", "igst", "total")}
    exp = {x: want[x] for x in got}
    lines = {int(x["line_no"]): {y: D(x.get(y)) for y in ("taxable", "cgst", "sgst", "igst")} for x in co.lines8(no)}
    return check(got == exp and lines == want["lines"], f"{label}: {no} amounts {got}, lines {lines}; oracle {exp}, "
                 f"{want['lines']}", sev)  # fmt: skip


def refused(check: b.Checker, r: Any, codes: int | tuple[int, ...], words: str | None, label: str,
            sev_words: str = "medium") -> bool:  # fmt: skip
    codes = (codes,) if isinstance(codes, int) else codes
    ok = check(r.status_code in codes, f"{label}: answered {r.status_code} {page(r)[:200]}, the spec says {codes}")
    if words is not None:
        check(says(r, words), f"{label}: the answer does not say '{words}': {page(r)[:300]}", sev_words)
    return ok


def unchanged(check: b.Checker, co: Co8, no: str, before: tuple[Any, ...], label: str) -> None:
    after = co.fp(no)
    check(after == before, f"{label}: a refused answer wrote something on {no}: {before} -> {after}")


def ack_ok(check: b.Checker, co: Co8, no: str, version: int, via: str, label: str, *, address: str | None = None,
           code: bool | None = None) -> dict[str, Any]:  # fmt: skip
    """`po.acknowledged_version`, status and the version's row (B7-2, spec 8.3.1, section 7)."""
    p = co.po_row(no)
    row = co.po_versions(no).get(version, {})
    check(p.get("acknowledged_version") == version and p.get("status") == "acknowledged",
          f"{label}: {no} acknowledged_version {p.get('acknowledged_version')}, status {p.get('status')}; expected "
          f"{version}, acknowledged")  # fmt: skip
    check(row.get("acknowledged_at") is not None and row.get("ack_via") == via, f"{label}: po_version {version} "
          f"acknowledged_at {row.get('acknowledged_at')}, ack_via {row.get('ack_via')}; expected filled in, {via}")  # fmt: skip
    if address is not None:
        check(str(row.get("ack_address") or "").lower() == address, f"{label}: po_version {version} ack_address "
              f"{row.get('ack_address', 'missing')}; expected {address} (section 7)", "medium")  # fmt: skip
    if code:
        check(row.get("ack_code_id") is not None and row.get("ack_link_id") is not None, f"{label}: po_version {version} "
              f"ack_code_id {row.get('ack_code_id', 'missing')}, ack_link_id {row.get('ack_link_id', 'missing')}; "
              "expected both (section 7)", "medium")  # fmt: skip
    return row


def not_acked(check: b.Checker, co: Co8, no: str, label: str, sev: str = "high") -> bool:
    p = co.po_row(no)
    return check(p.get("acknowledged_version") is None and p.get("status") != "acknowledged",
                 f"{label}: {no} acknowledged_version {p.get('acknowledged_version')}, status {p.get('status')}; "
                 "expected nothing acknowledged", sev)  # fmt: skip


def chaser_text(po: str, version: int, sent: date) -> str:
    """Spec 8.8.3's fixed words, up to the link."""
    return (f"We sent you purchase order {po} (version {version}) on {long_date(sent)} and have not received your "
            "acceptance. Please accept it, or tell us what you can do, here (the link works once and asks for a code "
            "we email to you):")  # fmt: skip


def stale_text(po: str, now_v: int, revised: date, old_v: int, address: str, sent: date) -> str:
    """Spec 8.3.2's words on a link of an older version."""
    return (f"Order {po} is now version {now_v} (revised on {long_date(revised)}). This link was for version {old_v} and "
            f"no longer works. Version {now_v} was emailed to {masked(address)} on {long_date(sent)}: please use that "
            "email.")  # fmt: skip


def tenant_check8(check: b.Checker, mine: Co8, other: Co8, orders: list[str], tokens: list[str | None], since: int) -> None:
    """Block 7's tenant check (404 for the order and its documents, forged links open nothing, no mail across) plus
    block 8's: the other company holds no code, task or exception row naming these orders."""
    t.tenant_check7(check, mine, other, orders, tokens, since)
    for no in [x for x in orders if x]:
        if other.one("select count(*) from po where no = :n", n=no):
            continue  # the other's own order of the same number
        codes = other.qd("select id from one_time_code where ref like :r", r=f"{no}%")
        check(not codes, f"tenant check: {other.name}'s database holds {len(codes)} code(s) for {mine.name}'s {no}")
        tasks = other.qd("select id from task where payload::text like :p or title like :p", p=f"%{no}%")
        check(not tasks, f"tenant check: {other.name} holds {len(tasks)} task(s) naming {mine.name}'s {no}")
        rows = other.exception_rows(other.buyer())
        check(no not in blob(rows).upper(), f"tenant check: {other.name}'s exceptions screen names {mine.name}'s {no}")


def start(co: Co8) -> None:
    pin(TODAY)
    co.work()
