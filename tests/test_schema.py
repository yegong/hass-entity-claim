"""Tests for dependency-free config parsing and naming."""

import unittest

from custom_components.entity_claim.model import Requester
from custom_components.entity_claim.schema import (
    RequesterParseError,
    claim_entity_id,
    diagnostic_entity_id,
    parse_requesters,
    requesters_from_config,
    requesters_to_config,
    requesters_to_text,
)


class RequesterParsingTests(unittest.TestCase):
    """Verify stable IDs remain separate from user-visible names."""

    def test_parse_multiple_requesters(self) -> None:
        self.assertEqual(
            parse_requesters("presence: Presence\nschedule: Schedule Logic"),
            (
                Requester("presence", "Presence"),
                Requester("schedule", "Schedule Logic"),
            ),
        )

    def test_empty_input_means_zero_requesters(self) -> None:
        self.assertEqual(parse_requesters("\n  \n"), ())

    def test_name_may_contain_colons(self) -> None:
        self.assertEqual(
            parse_requesters("safety: Safety: critical"),
            (Requester("safety", "Safety: critical"),),
        )

    def test_duplicate_id_is_rejected(self) -> None:
        with self.assertRaisesRegex(RequesterParseError, "duplicate requester"):
            parse_requesters("presence: First\npresence: Second")

    def test_unstable_id_format_is_rejected(self) -> None:
        invalid_values = (
            "Presence: Presence",
            "1presence: Presence",
            "presence-one: Presence",
            "presence one: Presence",
        )
        for value in invalid_values:
            with self.subTest(value=value), self.assertRaises(RequesterParseError):
                parse_requesters(value)

    def test_config_round_trip(self) -> None:
        requesters = (
            Requester("presence", "Presence"),
            Requester("schedule", "Schedule"),
        )
        stored = requesters_to_config(requesters)
        self.assertEqual(requesters_from_config(stored), requesters)
        self.assertEqual(
            requesters_to_text(requesters),
            "presence: Presence\nschedule: Schedule",
        )


class EntityIdTests(unittest.TestCase):
    """Verify IDs are deterministic and use stable requester IDs."""

    def test_claim_entity_id(self) -> None:
        self.assertEqual(
            claim_entity_id("fan.example_target", "requester_a"),
            "fan.example_target_required_by_requester_a",
        )

    def test_requester_name_does_not_affect_entity_id(self) -> None:
        before = Requester("requester_a", "Old name")
        after = Requester("requester_a", "New name")
        self.assertEqual(
            claim_entity_id("light.example_target", before.id),
            claim_entity_id("light.example_target", after.id),
        )

    def test_diagnostic_entity_id(self) -> None:
        self.assertEqual(
            diagnostic_entity_id("switch.example_target"),
            "sensor.example_target_claim_diagnostics",
        )


if __name__ == "__main__":
    unittest.main()
