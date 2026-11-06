"""A small demo toolset for trying agents end to end (acceptance runs, demos). Not part of the product flow.

Load it into the API with EXTRA_MODULES=p2p.agents.demo so that approving a proposal can run the payment tool.
"""

from decimal import Decimal
from typing import Any

from pydantic import BaseModel
from sqlalchemy.orm import Session

from p2p.agents.tools import Tool, ToolRegistry
from p2p.platform.sod import Actor

INVOICES = {
    "INV-9": {"supplier": "Shree Steel Traders", "amount": "125000.00", "status": "approved for payment"},
    "INV-12": {"supplier": "Western Jute Packaging", "amount": "49560.00", "status": "blocked: price above PO"},
}


class LookupInvoice(BaseModel):
    invoice: str


class ReleasePayment(BaseModel):
    invoice: str
    amount: Decimal


def lookup_invoice(db: Session, p: LookupInvoice, actor: Actor) -> dict[str, Any]:
    return INVOICES.get(p.invoice, {"error": f"no invoice called {p.invoice}"})


def release_payment(db: Session, p: ReleasePayment, actor: Actor) -> dict[str, Any]:
    return {"released": p.invoice, "amount": str(p.amount), "by": f"{actor.kind}:{actor.id}"}


def build_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        Tool("lookup_invoice", "Read the status and amount of an invoice by its number", LookupInvoice, lookup_invoice)
    )
    registry.register(
        Tool(
            "release_payment",
            "Release the payment for an invoice that is approved for payment",
            ReleasePayment,
            release_payment,
            writes=True,
            decision_type="payment_release",
            required_permission="payment.approve",
            document="payment_run",
            risk="high",
            value_of=lambda p: p.amount,  # type: ignore[attr-defined]
            summary=lambda p: f"Release payment for {p.invoice}",  # type: ignore[attr-defined]
        )
    )
    return registry


REGISTRY = build_registry()  # importing this module registers the payment executor in the process
