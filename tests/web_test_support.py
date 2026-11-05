"""Loaded by the API process in the browser tests (EXTRA_MODULES) so it can run the test action."""

from typing import Any

from sqlalchemy.orm import Session

from p2p.platform import proposals
from p2p.platform.sod import Actor


@proposals.executor("test.web_release")
def release(db: Session, payload: dict[str, Any], approver: Actor) -> dict[str, Any]:
    return {"released": payload["invoice"]}
