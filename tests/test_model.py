"""Tests for the dependency-free claim domain model."""

import unittest

from custom_components.entity_claim.model import (
    AggregationPolicy,
    ClaimState,
    ReconcileAction,
    Requester,
    aggregate_claims,
    is_conflicting_external_change,
    matches_pending_command,
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


class ExternalChangeTests(unittest.TestCase):
    """Verify the Manual Override gate is separate from aggregation."""

    def test_conflicting_external_change_starts_override(self) -> None:
        self.assertTrue(
            is_conflicting_external_change(
                True,
                False,
                self_induced=False,
                source_recovered=False,
            )
        )

    def test_matching_external_change_does_not_start_override(self) -> None:
        self.assertFalse(
            is_conflicting_external_change(
                False,
                False,
                self_induced=False,
                source_recovered=False,
            )
        )

    def test_self_induced_change_does_not_start_override(self) -> None:
        self.assertFalse(
            is_conflicting_external_change(
                True,
                False,
                self_induced=True,
                source_recovered=False,
            )
        )

    def test_unknown_and_unavailable_do_not_start_override(self) -> None:
        self.assertFalse(
            is_conflicting_external_change(
                True,
                None,
                self_induced=False,
                source_recovered=False,
            )
        )

    def test_first_explicit_state_after_recovery_does_not_start_override(
        self,
    ) -> None:
        self.assertFalse(
            is_conflicting_external_change(
                True,
                False,
                self_induced=False,
                source_recovered=True,
            )
        )


class PendingCommandTests(unittest.TestCase):
    """Verify exact Context correlation and the constrained fallback."""

    def test_exact_context_matches(self) -> None:
        self.assertTrue(
            matches_pending_command(True, True, "command", None, "command")
        )

    def test_child_context_matches_even_when_desired_has_changed(self) -> None:
        self.assertTrue(
            matches_pending_command(False, True, "child", "command", "command")
        )

    def test_expected_state_falls_back_when_context_is_lost(self) -> None:
        self.assertTrue(
            matches_pending_command(True, True, "device", None, "command")
        )

    def test_unrelated_context_and_state_do_not_match(self) -> None:
        self.assertFalse(
            matches_pending_command(False, True, "external", None, "command")
        )

if __name__ == "__main__":
    unittest.main()
