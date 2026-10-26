"""Pure domain model for Entity Claim.

This module deliberately has no Home Assistant imports so its behavior can be
tested without installing Home Assistant.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class AggregationPolicy(StrEnum):
    """Supported ordered claim aggregation policies."""

    ANY = "any"
    ALL = "all"


class ClaimKind(StrEnum):
    """Capability whose ordered value is held by each requester."""

    BINARY = "binary"
    BRIGHTNESS = "brightness"
    PERCENTAGE = "percentage"
    POSITION = "position"


@dataclass(frozen=True, slots=True)
class ClaimProfile:
    """Value range and comparison behavior for one Source Entity."""

    kind: ClaimKind
    maximum: int
    tolerance: float = 0
    supports_intermediate: bool = True
    step: float = 1

    @property
    def structured(self) -> bool:
        """Return whether claims expose an ordered value above Boolean state."""

        return self.kind is not ClaimKind.BINARY and self.supports_intermediate

    def normalize(self, value: int | float | bool) -> int:
        """Clamp a claim value to the profile's integer range."""

        return max(0, min(self.maximum, round(float(value))))


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
    claims: dict[str, int | bool], policy: AggregationPolicy
) -> int:
    """Aggregate ordered claims using MAX for ANY and MIN for ALL.

    Boolean claims are the degenerate ``0..1`` case of the same model. An
    empty requester set is explicitly zero for both policies.
    """

    if not claims:
        return 0
    values = (int(value) for value in claims.values())
    return max(values) if policy is AggregationPolicy.ANY else min(values)


def values_match(
    desired: int,
    actual: int | None,
    *,
    tolerance: float = 0,
) -> bool:
    """Return whether an actual value satisfies the current desired value.

    Zero is the explicit inactive endpoint. A tolerance must never make an
    active value equivalent to zero, even for coarse percentage devices.
    """

    if actual is None:
        return False
    if (desired == 0) != (actual == 0):
        return False
    return abs(desired - actual) <= tolerance


def reconcile_action(
    desired: bool, actual: bool | None
) -> ReconcileAction | None:
    """Return the minimum action needed for a Boolean source.

    Structured reconciliation uses :func:`values_match` and a domain adapter,
    while this helper remains the complete Boolean ``0..1`` case.
    """

    if actual is None or desired == actual:
        return None
    return ReconcileAction.TURN_ON if desired else ReconcileAction.TURN_OFF


def is_conflicting_external_change(
    desired: int | bool,
    actual: int | bool | None,
    *,
    self_induced: bool,
    source_recovered: bool,
    tolerance: float = 0,
) -> bool:
    """Return whether a source change should suppress reconciliation."""

    return (
        actual is not None
        and not values_match(int(desired), int(actual), tolerance=tolerance)
        and not self_induced
        and not source_recovered
    )


def matches_pending_command(
    actual: int | bool,
    pending_desired: int | bool,
    event_context_id: str,
    event_parent_context_id: str | None,
    pending_context_id: str,
    *,
    tolerance: float = 0,
) -> bool:
    """Correlate a final state change with a short-lived pending command.

    Exact or child Context correlation is authoritative. An expected result
    match is a constrained fallback for integrations which discard Context;
    its lifetime is enforced by the runtime controller.
    """

    return (
        event_context_id == pending_context_id
        or event_parent_context_id == pending_context_id
        or values_match(
            int(pending_desired), int(actual), tolerance=tolerance
        )
    )


def is_expected_position_progress(
    start: int | None,
    previous: int | None,
    actual: int | None,
    target: int,
    *,
    tolerance: float = 0,
) -> bool:
    """Return whether a Cover position is progressing toward our target.

    Position reports may skip values. They must remain inside the commanded
    interval and may not reverse beyond the configured tolerance.
    """

    if actual is None:
        return True
    origin = previous if previous is not None else start
    if origin is None:
        return 0 - tolerance <= actual <= 100 + tolerance
    lower = min(start if start is not None else origin, target) - tolerance
    upper = max(start if start is not None else origin, target) + tolerance
    if not lower <= actual <= upper:
        return False
    if target > origin:
        return actual >= origin - tolerance
    if target < origin:
        return actual <= origin + tolerance
    return abs(actual - target) <= tolerance


class ClaimState:
    """Mutable ordered claims with one centralized aggregation rule."""

    __slots__ = ("_claims", "_policy", "_profile")

    def __init__(
        self,
        requesters: tuple[Requester, ...],
        policy: AggregationPolicy,
        profile: ClaimProfile | None = None,
    ) -> None:
        self._claims = {requester.id: 0 for requester in requesters}
        self._policy = policy
        self._profile = profile or ClaimProfile(ClaimKind.BINARY, 1)

    @property
    def policy(self) -> AggregationPolicy:
        """Return the configured aggregation policy."""

        return self._policy

    @property
    def profile(self) -> ClaimProfile:
        """Return the Source capability represented by the claims."""

        return self._profile

    @property
    def desired(self) -> int:
        """Return the current aggregated desired value."""

        return aggregate_claims(self._claims, self._policy)

    @property
    def claims(self) -> dict[str, int]:
        """Return a copy of current claims."""

        return self._claims.copy()

    def get(self, requester_id: str) -> int:
        """Return one requester's claim value."""

        return self._claims[requester_id]

    def set(self, requester_id: str, value: int | float | bool) -> bool:
        """Set a claim value and return whether it changed."""

        if requester_id not in self._claims:
            raise KeyError(f"Unknown requester: {requester_id}")
        normalized = self._profile.normalize(value)
        if self._claims[requester_id] == normalized:
            return False
        self._claims[requester_id] = normalized
        return True
