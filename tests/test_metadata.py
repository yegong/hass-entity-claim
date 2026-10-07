"""Tests for integration metadata that do not require Home Assistant."""

import json
from pathlib import Path
import unittest


INTEGRATION_DIR = Path(__file__).parents[1] / "custom_components" / "entity_claim"


class MetadataTests(unittest.TestCase):
    """Validate shipped JSON and essential manifest fields."""

    def test_all_json_files_are_valid(self) -> None:
        paths = [
            INTEGRATION_DIR / "manifest.json",
            INTEGRATION_DIR / "strings.json",
            *(INTEGRATION_DIR / "translations").glob("*.json"),
        ]
        for path in paths:
            with self.subTest(path=path.name):
                with path.open(encoding="utf-8") as file:
                    self.assertIsInstance(json.load(file), dict)

    def test_manifest_declares_config_flow_and_no_dependencies(self) -> None:
        with (INTEGRATION_DIR / "manifest.json").open(encoding="utf-8") as file:
            manifest = json.load(file)
        self.assertEqual(manifest["domain"], "entity_claim")
        self.assertTrue(manifest["config_flow"])
        self.assertEqual(manifest["requirements"], [])
        self.assertRegex(manifest["version"], r"^\d+\.\d+\.\d+$")

    def test_every_platform_has_an_entity_name_translation(self) -> None:
        with (INTEGRATION_DIR / "strings.json").open(encoding="utf-8") as file:
            strings = json.load(file)
        for platform in ("fan", "light", "switch"):
            self.assertIn("name", strings["entity"][platform]["claim"])
        self.assertIn("name", strings["entity"]["sensor"]["diagnostics"])

    def test_fan_claim_declares_boolean_actions(self) -> None:
        source = (INTEGRATION_DIR / "fan.py").read_text(encoding="utf-8")
        self.assertIn("FanEntityFeature.TURN_ON", source)
        self.assertIn("FanEntityFeature.TURN_OFF", source)

    def test_helpers_ui_has_an_options_flow(self) -> None:
        source = (INTEGRATION_DIR / "config_flow.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("async_get_options_flow", source)
        self.assertIn("OptionsFlowWithReload", source)


if __name__ == "__main__":
    unittest.main()
