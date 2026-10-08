"""Pure domain model for Entity Claim.

This module deliberately has no Home Assistant imports so its behavior can be
tested without installing Home Assistant.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class AggregationPolicy(StrEnum):
    """Supported Boolean claim aggregation policies."""

    ANY = "any"
    ALL = "all"


class ReconcileAction(StrEnum):
    """Action required to bring a Boolean target into sync."""

    TURN_ON = "turn_on"
    TURN_OFF = "turn_off"


@dataclass(frozen=True, slots=True)
class Requester:
    """Stable requester identity and its user-visible name."""

    id: str
    name: str


def aggregate_claims(
    claims: dict[str, bool], policy: AggregationPolicy
) -> bool:
    """Aggregate Boolean claims.

    An empty requester set is explicitly OFF for both policies. This avoids
    Python's mathematically valid but undesirable ``all([]) is True`` result.
    """

    if not claims:
        return False
    if policy is AggregationPolicy.ANY:
        return any(claims.values())
    return all(claims.values())


def reconcile_action(
    desired: bool, actual: bool | None
) -> ReconcileAction | None:
    """Return the minimum action needed to reconcile desired and actual state.

    ``None`` actual state represents unknown or unavailable. There is no useful
    one-shot action to take until the source reports an explicit Boolean state.
    """

    if actual is None or desired == actual:
        return None
    return ReconcileAction.TURN_ON if desired else ReconcileAction.TURN_OFF


def is_conflicting_external_change(
    desired: bool,
    actual: bool | None,
    *,
    self_induced: bool,
    source_recovered: bool,
) -> bool:
    """Return whether a source change should suppress reconciliation.

    Unknown and unavailable states do not express control intent. Likewise,
    the first explicit state after either condition is recovery rather than a
    direct external command and must be reconciled normally.
    """

    return (
        actual is not None
        and actual != desired
        and not self_induced
        and not source_recovered
    )


def matches_pending_command(
    actual: bool,
    pending_desired: bool,
    event_context_id: str,
    event_parent_context_id: str | None,
    pending_context_id: str,
) -> bool:
    """Correlate a state change with a short-lived pending command.

    Exact or child Context correlation is authoritative. The expected Boolean
    result supports device integrations which do not propagate service-call
    Context; its time bound is enforced by the runtime controller.
    """

    return (
        event_context_id == pending_context_id
        or event_parent_context_id == pending_context_id
        or actual == pending_desired
    )


class ClaimState:
    """Mutable set of claims with one centralized aggregation rule."""

    __slots__ = ("_claims", "_policy")

    def __init__(
        self,
        requesters: tuple[Requester, ...],
        policy: AggregationPolicy,
    ) -> None:
        self._claims = {requester.id: False for requester in requesters}
        self._policy = policy

    @property
    def policy(self) -> AggregationPolicy:
        """Return the configured aggregation policy."""

        return self._policy

    @property
    def desired(self) -> bool:
        """Return the current aggregated desired state."""

        return aggregate_claims(self._claims, self._policy)

    @property
    def claims(self) -> dict[str, bool]:
        """Return a copy of current claims."""

        return self._claims.copy()

    def get(self, requester_id: str) -> bool:
        """Return one requester's claim."""

        return self._claims[requester_id]

    def set(self, requester_id: str, state: bool) -> bool:
        """Set a claim and return whether it changed."""

        if requester_id not in self._claims:
            raise KeyError(f"Unknown requester: {requester_id}")
        normalized = bool(state)
        if self._claims[requester_id] == normalized:
            return False
        self._claims[requester_id] = normalized
        return True
