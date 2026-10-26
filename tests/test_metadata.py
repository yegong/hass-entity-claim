"""Tests for integration metadata that do not require Home Assistant."""

import ast
import json
from pathlib import Path
import re
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

    def test_base_and_english_strings_match(self) -> None:
        with (INTEGRATION_DIR / "strings.json").open(encoding="utf-8") as file:
            base = json.load(file)
        with (INTEGRATION_DIR / "translations" / "en.json").open(
            encoding="utf-8"
        ) as file:
            english = json.load(file)
        self.assertEqual(base, english)
        self.assertIsNone(
            re.search(
                r"[\u3400-\u4dbf\u4e00-\u9fff]",
                json.dumps(english, ensure_ascii=False),
            )
        )

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
        for platform in ("cover", "fan", "light", "switch"):
            self.assertIn("name", strings["entity"][platform]["claim"])
        self.assertIn("name", strings["entity"]["sensor"]["diagnostics"])

    def test_fan_claim_declares_percentage_capability(self) -> None:
        source = (INTEGRATION_DIR / "fan.py").read_text(encoding="utf-8")
        self.assertIn("FanEntityFeature.TURN_ON", source)
        self.assertIn("FanEntityFeature.TURN_OFF", source)
        self.assertIn("FanEntityFeature.SET_SPEED", source)

    def test_cover_claim_excludes_tilt_and_stop(self) -> None:
        source = (INTEGRATION_DIR / "cover.py").read_text(encoding="utf-8")
        self.assertIn("CoverEntityFeature.SET_POSITION", source)
        self.assertNotIn("CoverEntityFeature.SET_TILT_POSITION", source)
        self.assertNotIn("CoverEntityFeature.STOP", source)

        controller = (INTEGRATION_DIR / "controller.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("SERVICE_STOP_COVER", controller)
        self.assertIn("CoverEntityFeature.STOP", controller)

    def test_diagnostics_use_unified_control_states(self) -> None:
        source = (INTEGRATION_DIR / "sensor.py").read_text(encoding="utf-8")
        self.assertIn("control_state.value", source)
        self.assertNotIn('return "in_sync"', source)
        self.assertNotIn('return "manual_override"', source)

    def test_config_entry_migrates_legacy_override_toggle(self) -> None:
        setup = (INTEGRATION_DIR / "__init__.py").read_text(encoding="utf-8")
        flow = (INTEGRATION_DIR / "config_flow.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("async def async_migrate_entry(", setup)
        self.assertIn("CONF_RESPECT_SOURCE_CHANGES", setup)
        self.assertIn("VERSION = 2", flow)

    def test_helpers_ui_has_an_options_flow(self) -> None:
        source = (INTEGRATION_DIR / "config_flow.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("async_get_options_flow", source)
        self.assertIn("OptionsFlowWithReload", source)

    def test_control_configuration_is_translated_in_every_flow(self) -> None:
        """Ensure Manual Override uses a conditional translated step."""

        for filename in (
            "strings.json",
            "translations/en.json",
            "translations/zh-Hans.json",
        ):
            with (INTEGRATION_DIR / filename).open(encoding="utf-8") as file:
                strings = json.load(file)
            for flow, step in (
                ("config", "claims"),
                ("config", "reconfigure"),
                ("options", "init"),
            ):
                data = strings[flow]["step"][step]["data"]
                self.assertIn("control_policy", data)
                self.assertNotIn("override_duration_minutes", data)
                self.assertEqual(
                    list(data)[-1],
                    "diagnostic_sensor_enabled",
                    "Diagnostics should remain the final, optional setting",
                )

            for flow in ("config", "options"):
                override = strings[flow]["step"]["override"]
                self.assertEqual(
                    set(override["data"]), {"override_duration_minutes"}
                )
                self.assertIn("description", override)

        source = (INTEGRATION_DIR / "config_flow.py").read_text(
            encoding="utf-8"
        )
        classes = {
            node.name: {
                child.name
                for child in node.body
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            for node in ast.parse(source).body
            if isinstance(node, ast.ClassDef)
        }
        for class_name in (
            "EntityClaimConfigFlow",
            "EntityClaimOptionsFlow",
        ):
            self.assertIn("async_step_override", classes[class_name])

    def test_control_policy_has_three_translated_choices(self) -> None:
        for filename in (
            "strings.json",
            "translations/en.json",
            "translations/zh-Hans.json",
        ):
            with (INTEGRATION_DIR / filename).open(encoding="utf-8") as file:
                options = json.load(file)["selector"]["control_policy"][
                    "options"
                ]
            self.assertEqual(
                set(options), {"authoritative", "override", "scoped"}
            )

    def test_each_capability_has_aggregation_copy(self) -> None:
        for filename in (
            "strings.json",
            "translations/en.json",
            "translations/zh-Hans.json",
        ):
            with (INTEGRATION_DIR / filename).open(encoding="utf-8") as file:
                selectors = json.load(file)["selector"]
            for capability in (
                "binary",
                "brightness",
                "percentage",
                "position",
            ):
                options = selectors[f"aggregation_policy_{capability}"][
                    "options"
                ]
                self.assertEqual(set(options), {"any", "all"})

    def test_generated_entities_inherit_source_location(self) -> None:
        setup_source = (INTEGRATION_DIR / "__init__.py").read_text(
            encoding="utf-8"
        )
        claim_source = (INTEGRATION_DIR / "entity.py").read_text(
            encoding="utf-8"
        )
        sensor_source = (INTEGRATION_DIR / "sensor.py").read_text(
            encoding="utf-8"
        )
        registry_source = (INTEGRATION_DIR / "registry.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("async_entity_id_to_device", setup_source)
        self.assertIn("source_device=", setup_source)
        self.assertIn("self.device_entry = source_device", claim_source)
        self.assertIn("self.device_entry = source_device", sensor_source)
        self.assertIn("def inherit_source_entity_area(", registry_source)
        self.assertIn("entry.area_id is None", registry_source)

    def test_empty_reconfigure_offers_only_removal(self) -> None:
        source = (INTEGRATION_DIR / "config_flow.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('menu_options=["remove_entry"]', source)
        self.assertNotIn("keep_empty", source)

        classes = {
            node.name: {
                child.name
                for child in node.body
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            for node in ast.parse(source).body
            if isinstance(node, ast.ClassDef)
        }
        for class_name in (
            "EntityClaimConfigFlow",
            "EntityClaimOptionsFlow",
        ):
            self.assertIn("async_step_remove_confirm", classes[class_name])
            self.assertIn("async_step_remove_entry", classes[class_name])

        with (INTEGRATION_DIR / "strings.json").open(encoding="utf-8") as file:
            strings = json.load(file)
        for flow in ("config", "options"):
            remove_step = strings[flow]["step"]["remove_confirm"]
            self.assertEqual(
                remove_step["menu_options"],
                {"remove_entry": "Remove Entity Claim"},
            )


if __name__ == "__main__":
    unittest.main()
