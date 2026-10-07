"""Tests for the dependency-free claim domain model."""

import unittest

from custom_components.entity_claim.model import (
    AggregationPolicy,
    ClaimState,
    ReconcileAction,
    Requester,
    aggregate_claims,
    reconcile_action,
)


class AggregateClaimsTests(unittest.TestCase):
    """Verify the explicitly defined ANY and ALL semantics."""

    def test_any(self) -> None:
        self.assertFalse(
            aggregate_claims(
                {"a": False, "b": False}, AggregationPolicy.ANY
            )
        )
        self.assertTrue(
            aggregate_claims({"a": False, "b": True}, AggregationPolicy.ANY)
        )

    def test_all(self) -> None:
        self.assertFalse(
            aggregate_claims({"a": True, "b": False}, AggregationPolicy.ALL)
        )
        self.assertTrue(
            aggregate_claims({"a": True, "b": True}, AggregationPolicy.ALL)
        )

    def test_empty_is_off_for_both_policies(self) -> None:
        self.assertFalse(aggregate_claims({}, AggregationPolicy.ANY))
        self.assertFalse(aggregate_claims({}, AggregationPolicy.ALL))


class ClaimStateTests(unittest.TestCase):
    """Verify requester isolation and aggregation ownership."""

    def test_claims_change_independently(self) -> None:
        state = ClaimState(
            (Requester("presence", "Presence"), Requester("schedule", "Schedule")),
            AggregationPolicy.ANY,
        )
        self.assertFalse(state.desired)
        self.assertTrue(state.set("presence", True))
        self.assertTrue(state.desired)
        self.assertFalse(state.get("schedule"))
        self.assertFalse(state.set("presence", True))

    def test_unknown_requester_is_rejected(self) -> None:
        state = ClaimState((), AggregationPolicy.ANY)
        with self.assertRaises(KeyError):
            state.set("missing", True)

    def test_claims_property_is_a_copy(self) -> None:
        state = ClaimState((Requester("a", "A"),), AggregationPolicy.ANY)
        snapshot = state.claims
        snapshot["a"] = True
        self.assertFalse(state.get("a"))


class ReconcileActionTests(unittest.TestCase):
    """Verify duplicate calls are avoided and unknown actual is deferred."""

    def test_no_action_when_in_sync(self) -> None:
        self.assertIsNone(reconcile_action(True, True))
        self.assertIsNone(reconcile_action(False, False))

    def test_opposite_explicit_state_is_reconciled(self) -> None:
        self.assertEqual(
            reconcile_action(True, False), ReconcileAction.TURN_ON
        )
        self.assertEqual(
            reconcile_action(False, True), ReconcileAction.TURN_OFF
        )

    def test_unknown_or_unavailable_actual_is_deferred(self) -> None:
        self.assertIsNone(reconcile_action(True, None))
        self.assertIsNone(reconcile_action(False, None))


if __name__ == "__main__":
    unittest.main()

