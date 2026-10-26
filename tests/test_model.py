"""Tests for the dependency-free claim domain model."""

import unittest

from custom_components.entity_claim.model import (
    AggregationPolicy,
    ClaimKind,
    ClaimProfile,
    ClaimState,
    ReconcileAction,
    Requester,
    aggregate_claims,
    is_conflicting_external_change,
    is_expected_position_progress,
    matches_pending_command,
    reconcile_action,
    values_match,
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

    def test_any_is_max_for_ordered_claims(self) -> None:
        self.assertEqual(
            aggregate_claims(
                {"reading": 80, "relaxing": 35, "away": 0},
                AggregationPolicy.ANY,
            ),
            80,
        )

    def test_all_is_min_for_ordered_claims(self) -> None:
        self.assertEqual(
            aggregate_claims(
                {"reading": 80, "relaxing": 35, "away": 0},
                AggregationPolicy.ALL,
            ),
            0,
        )


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

    def test_structured_claims_are_normalized_and_aggregated(self) -> None:
        state = ClaimState(
            (Requester("a", "A"), Requester("b", "B")),
            AggregationPolicy.ANY,
            ClaimProfile(ClaimKind.PERCENTAGE, 100),
        )
        self.assertTrue(state.set("a", 40))
        self.assertTrue(state.set("b", 120))
        self.assertEqual(state.get("b"), 100)
        self.assertEqual(state.desired, 100)

    def test_profile_distinguishes_binary_and_structured_capabilities(self) -> None:
        self.assertFalse(ClaimProfile(ClaimKind.BINARY, 1).structured)
        self.assertTrue(ClaimProfile(ClaimKind.BRIGHTNESS, 255).structured)
        self.assertFalse(
            ClaimProfile(
                ClaimKind.POSITION,
                100,
                supports_intermediate=False,
            ).structured
        )


class OrderedValueTests(unittest.TestCase):
    """Verify tolerance and Cover progress stay explicit and predictable."""

    def test_tolerance_never_equates_off_and_on(self) -> None:
        self.assertTrue(values_match(50, 48, tolerance=2))
        self.assertFalse(values_match(0, 1, tolerance=10))

    def test_cover_progress_accepts_commanded_direction(self) -> None:
        self.assertTrue(
            is_expected_position_progress(20, 40, 65, 80, tolerance=2)
        )
        self.assertTrue(
            is_expected_position_progress(80, 60, 42, 20, tolerance=2)
        )

    def test_cover_progress_rejects_reversal_or_interval_escape(self) -> None:
        self.assertFalse(
            is_expected_position_progress(20, 50, 35, 80, tolerance=2)
        )
        self.assertFalse(
            is_expected_position_progress(20, 50, 90, 80, tolerance=2)
        )


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
