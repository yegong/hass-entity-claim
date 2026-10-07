"""Tests for dependency-free config parsing and naming."""

import unittest

from custom_components.entity_claim.model import Requester
from custom_components.entity_claim.schema import (
    RequesterParseError,
    claim_entity_id,
    diagnostic_entity_id,
    parse_requesters,
    requester_id_from_name,
    requesters_from_config,
    requesters_to_config,
    requesters_to_names,
)


class RequesterParsingTests(unittest.TestCase):
    """Verify names produce stable machine IDs without exposing ID syntax."""

    def test_parse_multiple_requesters(self) -> None:
        self.assertEqual(
            parse_requesters(["motion", "Automation", "SCHEDULE"]),
            (
                Requester("motion", "motion"),
                Requester("automation", "Automation"),
                Requester("schedule", "SCHEDULE"),
            ),
        )

    def test_empty_input_means_zero_requesters(self) -> None:
        self.assertEqual(parse_requesters([]), ())
        self.assertEqual(parse_requesters(["", "  "]), ())

    def test_name_is_slugified_for_machine_id(self) -> None:
        self.assertEqual(
            parse_requesters(["Window Contact"]),
            (Requester("window_contact", "Window Contact"),),
        )

    def test_case_variants_are_accepted(self) -> None:
        for name in ("motion", "Motion", "MOTION"):
            with self.subTest(name=name):
                self.assertEqual(
                    parse_requesters([name]),
                    (Requester("motion", name),),
                )

    def test_duplicate_generated_id_is_rejected(self) -> None:
        with self.assertRaisesRegex(RequesterParseError, "same ID"):
            parse_requesters(["motion", "MOTION"])

    def test_non_ascii_name_has_deterministic_fallback_id(self) -> None:
        requester_id = requester_id_from_name("移动侦测")
        self.assertRegex(requester_id, r"^requester_[a-f0-9]{10}$")
        self.assertEqual(requester_id_from_name("移动侦测"), requester_id)

    def test_rename_reuses_previous_id(self) -> None:
        previous = (Requester("motion", "Motion"),)
        self.assertEqual(
            parse_requesters(["Occupancy"], previous),
            (Requester("motion", "Occupancy"),),
        )

    def test_removing_item_preserves_remaining_ids(self) -> None:
        previous = (
            Requester("motion", "Motion"),
            Requester("schedule", "Schedule"),
        )
        self.assertEqual(
            parse_requesters(["Schedule"], previous),
            (Requester("schedule", "Schedule"),),
        )

    def test_added_item_gets_generated_id(self) -> None:
        previous = (Requester("motion", "Motion"),)
        self.assertEqual(
            parse_requesters(["Motion", "Schedule"], previous),
            (
                Requester("motion", "Motion"),
                Requester("schedule", "Schedule"),
            ),
        )

    def test_config_round_trip(self) -> None:
        requesters = (
            Requester("presence", "Presence"),
            Requester("schedule", "Schedule"),
        )
        stored = requesters_to_config(requesters)
        self.assertEqual(requesters_from_config(stored), requesters)
        self.assertEqual(
            requesters_to_names(requesters),
            ["Presence", "Schedule"],
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
