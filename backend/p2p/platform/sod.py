"""Segregation of duties (FR32).

For one case (one purchase) the requester, approver, receiver, invoice approver and payer must be
different. An agent counts as the actor it is configured to act as, so an agent working for a user
does not get around the rule.
"""

from collections.abc import Mapping
from dataclasses import dataclass

DUTIES = ("requester", "approver", "receiver", "invoice_approver", "payer")


class SegregationError(Exception):
    pass


@dataclass(frozen=True)
class Actor:
    kind: str  # user, agent, service or rule
    id: str
    acts_as: str | None = None  # identity of the person or role this actor stands in for, e.g. "user:5"

    @property
    def identity(self) -> str:
        return self.acts_as or f"{self.kind}:{self.id}"


def user(user_id: int) -> Actor:
    return Actor("user", str(user_id))


def check_segregation(case_actors: Mapping[str, Actor], duty: str, actor: Actor) -> None:
    """Raise SegregationError if the actor already holds a different duty in this case."""
    if duty not in DUTIES:
        raise ValueError(f"unknown duty '{duty}'")
    for other, held_by in case_actors.items():
        if other != duty and held_by.identity == actor.identity:
            raise SegregationError(
                f"{actor.identity} is already the {other} on this case and cannot be the {duty}"
            )
