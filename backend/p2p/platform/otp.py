"""One-time codes emailed to a person before a sensitive decision (an approval above a limit).

Only a keyed hash of a code is kept. A code expires after ten minutes, allows five wrong tries and works once; asking for a
new one replaces the old. The code is sent straight to the person's email address, never through a stored job, so it is
nowhere at rest in plain text. One instance per purpose: `OneTimeCodes("approval")` for request approvals; an order
approval or a payment run can use its own purpose with the same rules. A code can also go to an address with no login
(issue_to, verify_address: a supplier contact accepting a contract, D53).
"""

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.config import get_settings
from p2p.platform import audit, mail, notifications, settings
from p2p.platform.sod import Actor

DEFAULT_LIMIT = "500000.00"


class CodeError(Exception):
    """The code cannot be issued or does not let the person through (wrong, expired, used, too many tries)."""


def needed(db: Session, amount: Decimal, setting: str | None = None) -> bool:
    """True when the amount is above the limit in the setting (D36: Rs 5,00,000; the limit itself needs no code). The
    setting is approval_otp_above unless another flow names its own."""
    limit = (
        settings.number(db, setting, DEFAULT_LIMIT)
        if setting
        else settings.number(db, "approval_otp_above", DEFAULT_LIMIT)
    )
    return amount > limit


@dataclass(frozen=True)
class _Who:
    """Whom a code is for: a login (`user_id`), or an address with no login (a supplier's contact, D53)."""

    user_id: int | None
    address: str | None

    @property
    def key(self) -> str:
        return str(self.user_id) if self.user_id is not None else str(self.address).lower()

    @property
    def actor(self) -> Actor:
        return Actor("user", str(self.user_id)) if self.user_id is not None else Actor("service", f"contact:{self.key}")

    def where(self) -> str:
        return "user_id = :u" if self.user_id is not None else "lower(address) = :a"

    def params(self) -> dict[str, object]:
        return {"u": self.user_id} if self.user_id is not None else {"a": self.key}


@dataclass(frozen=True)
class OneTimeCodes:
    purpose: str
    ttl: timedelta = timedelta(minutes=10)
    max_attempts: int = 5
    resend_gap: timedelta = timedelta(seconds=30)
    digits: int = 6

    def _hash(self, ref: str, who: str, code: str) -> str:
        secret = get_settings().jwt_secret
        if not secret:
            raise CodeError("one-time codes are not available: no signing secret is set")
        key = hashlib.sha256(b"one-time-code:" + secret.encode()).digest()
        return hmac.new(key, f"{self.purpose}|{ref}|{who}|{code}".encode(), hashlib.sha256).hexdigest()

    def issue(self, db: Session, ref: str, user_id: int, *, subject: str, now: datetime | None = None) -> str:
        """Make a code, email it to the person and keep its hash. Returns the address it went to."""
        address = notifications.email_of(user_id)
        if not address:
            raise CodeError("there is no email address to send the code to")
        return self._issue(db, ref, _Who(user_id, None), address, subject, now)

    def issue_to(self, db: Session, ref: str, address: str, *, subject: str, now: datetime | None = None) -> str:
        """The same for an address with no login (the supplier's contact a letter went to). Returns the address."""
        if not address or "@" not in address:
            raise CodeError("there is no email address to send the code to")
        return self._issue(db, ref, _Who(None, address), address, subject, now)

    def _issue(self, db: Session, ref: str, who: _Who, address: str, subject: str, now: datetime | None) -> str:
        now = now or datetime.now(UTC)
        recent = db.execute(
            text(
                f"select 1 from one_time_code where purpose = :p and ref = :r and {who.where()} "  # noqa: S608 - fixed text
                "and created_at > :since"
            ),
            {"p": self.purpose, "r": ref, "since": now - self.resend_gap, **who.params()},
        ).first()
        if recent:
            raise CodeError("a code was sent a moment ago; check your mail before asking again")
        code = f"{secrets.randbelow(10**self.digits):0{self.digits}d}"
        db.execute(
            text(
                f"update one_time_code set status = 'replaced' where purpose = :p and ref = :r and {who.where()} "  # noqa: S608
                "and status = 'open'"
            ),
            {"p": self.purpose, "r": ref, **who.params()},
        )
        db.execute(
            text(
                "insert into one_time_code (purpose, ref, user_id, address, code_hash, created_at, expires_at) "
                "values (:p, :r, :u, :a, :h, :now, :exp)"
            ),
            {
                "p": self.purpose, "r": ref, "u": who.user_id, "a": who.key if who.user_id is None else None,
                "h": self._hash(ref, who.key, code), "now": now, "exp": now + self.ttl,
            },
        )  # fmt: skip
        minutes = int(self.ttl.total_seconds() // 60)
        try:
            mail.send_email(
                address,
                subject,
                f"Your one-time code is {code}.\n\nIt works once and expires in {minutes} minutes. "
                "Nobody from the company will ask you for it.",
            )
        except OSError:
            db.rollback()
            raise CodeError("the code could not be sent; try again in a moment") from None
        audit.record(db, who.actor, "otp.issue", "one_time_code", f"{self.purpose}:{ref}")
        db.commit()
        return address

    def verify(self, db: Session, ref: str, user_id: int, code: str | None, *, now: datetime | None = None) -> None:
        """Let the person through with the right code, once. The code is marked used in the caller's transaction (it
        commits with the decision); a wrong, expired or locked code is recorded and committed at once, then refused."""
        self._verify(db, ref, _Who(user_id, None), code, now)

    def verify_address(
        self, db: Session, ref: str, address: str, code: str | None, *, now: datetime | None = None
    ) -> None:
        """The same for a code sent to an address with no login."""
        self._verify(db, ref, _Who(None, address), code, now)

    def _verify(self, db: Session, ref: str, who: _Who, code: str | None, now: datetime | None) -> None:
        now = now or datetime.now(UTC)
        row = (
            db.execute(
                text(
                    "select id, code_hash, expires_at, attempts from one_time_code where purpose = :p and ref = :r "  # noqa: S608
                    f"and {who.where()} and status = 'open' order by id desc limit 1 for update"
                ),
                {"p": self.purpose, "r": ref, **who.params()},
            )
            .mappings()
            .first()
        )
        actor, entity = who.actor, f"{self.purpose}:{ref}"
        if row is None:
            raise CodeError("this needs a one-time code: ask for one (a code works once)")
        if now > row["expires_at"]:
            db.execute(text("update one_time_code set status = 'expired' where id = :i"), {"i": row["id"]})
            audit.record(db, actor, "otp.expired", "one_time_code", entity)
            db.commit()
            raise CodeError("the code has expired; ask for a new one")
        given = (code or "").strip()
        if not hmac.compare_digest(row["code_hash"], self._hash(ref, who.key, given)):
            attempts = int(row["attempts"]) + 1
            locked = attempts >= self.max_attempts
            db.execute(
                text("update one_time_code set attempts = :a, status = :s where id = :i"),
                {"a": attempts, "s": "locked" if locked else "open", "i": row["id"]},
            )
            audit.record(
                db, actor, "otp.wrong", "one_time_code", entity, after={"attempts": attempts, "locked": locked}
            )
            db.commit()
            if locked:
                raise CodeError(f"wrong code {self.max_attempts} times: this code no longer works; ask for a new one")
            raise CodeError(f"wrong code; {self.max_attempts - attempts} tries left")
        db.execute(
            text("update one_time_code set status = 'used', used_at = :n where id = :i"), {"n": now, "i": row["id"]}
        )
        audit.record(db, actor, "otp.use", "one_time_code", entity)
