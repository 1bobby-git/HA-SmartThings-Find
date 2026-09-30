from __future__ import annotations

import ast
import json
from pathlib import Path
import unittest

from test_utils_location import _load_utils_module


REPO_ROOT = Path(__file__).resolve().parents[1]
COMPONENT_DIR = REPO_ROOT / "custom_components" / "smartthings_find"

utils = _load_utils_module()


class CookieHeaderNormalizationTests(unittest.TestCase):
    """Robust cookie parsing for issue #6 (authentication failure)."""

    def test_plain_header_and_cookie_prefix_are_equivalent(self) -> None:
        expected = {"JSESSIONID": "abc", "WMONID": "xyz"}
        self.assertEqual(
            utils.parse_cookie_header("JSESSIONID=abc; WMONID=xyz"),
            expected,
        )
        self.assertEqual(
            utils.parse_cookie_header("Cookie: JSESSIONID=abc; WMONID=xyz"),
            expected,
        )
        self.assertEqual(
            utils.parse_cookie_header("cookie: JSESSIONID=abc; WMONID=xyz"),
            expected,
        )

    def test_surrounding_quotes_are_stripped(self) -> None:
        self.assertEqual(
            utils.parse_cookie_header('"JSESSIONID=abc; WMONID=xyz"'),
            {"JSESSIONID": "abc", "WMONID": "xyz"},
        )
        self.assertEqual(
            utils.parse_cookie_header("'Cookie: JSESSIONID=abc'"),
            {"JSESSIONID": "abc"},
        )

    def test_wrapped_newlines_and_tabs_are_flattened(self) -> None:
        self.assertEqual(
            utils.parse_cookie_header("JSESSIONID=abc;\r\n\tWMONID=xyz"),
            {"JSESSIONID": "abc", "WMONID": "xyz"},
        )

    def test_duplicate_names_keep_last_value(self) -> None:
        self.assertEqual(
            utils.parse_cookie_header("JSESSIONID=old; JSESSIONID=new"),
            {"JSESSIONID": "new"},
        )

    def test_json_array_export_is_parsed(self) -> None:
        exported = json.dumps(
            [
                {"name": "JSESSIONID", "value": "abc", "domain": "x"},
                {"name": "WMONID", "value": "xyz"},
            ]
        )
        self.assertEqual(
            utils.parse_cookie_header(exported),
            {"JSESSIONID": "abc", "WMONID": "xyz"},
        )

    def test_json_object_export_is_parsed(self) -> None:
        exported = json.dumps({"JSESSIONID": "abc", "WMONID": "xyz"})
        self.assertEqual(
            utils.parse_cookie_header(exported),
            {"JSESSIONID": "abc", "WMONID": "xyz"},
        )

    def test_empty_input_returns_empty(self) -> None:
        self.assertEqual(utils.parse_cookie_header(""), {})
        self.assertEqual(utils.parse_cookie_header("   "), {})


class ModeSelectorTranslationTests(unittest.TestCase):
    """Mode selector must go through translations (issue #8)."""

    def test_mode_selector_uses_translation_key_not_hardcoded_labels(self) -> None:
        source = (COMPONENT_DIR / "config_flow.py").read_text(encoding="utf-8")
        self.assertIn("translation_key=_MODE_SELECTOR_TRANSLATION_KEY", source)
        # The hardcoded Korean labels must be gone.
        self.assertNotIn("패시브", source)
        self.assertNotIn("액티브", source)
        self.assertNotIn("SelectOptionDict", source)

    def test_every_translation_file_has_device_mode_selector(self) -> None:
        paths = [
            COMPONENT_DIR / "strings.json",
            COMPONENT_DIR / "translations" / "en.json",
            COMPONENT_DIR / "translations" / "ko.json",
            COMPONENT_DIR / "translations" / "de.json",
        ]
        for path in paths:
            with self.subTest(path=path.name):
                data = json.loads(path.read_text(encoding="utf-8"))
                options = data["selector"]["device_mode"]["options"]
                self.assertEqual({"passive", "active"}, set(options))
                self.assertTrue(options["passive"])
                self.assertTrue(options["active"])


class WearableBatterySkipTests(unittest.TestCase):
    """WEARABLE battery entity is not created (issue #7)."""

    def test_sensor_setup_skips_battery_for_wearable(self) -> None:
        source = (COMPONENT_DIR / "sensor.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        setup = next(
            node
            for node in tree.body
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name == "async_setup_entry"
        )
        # WEARABLE must gate the battery sensor construction.
        self.assertIn("WEARABLE", ast.get_source_segment(source, setup))
        self.assertIn("deviceTypeCode", ast.get_source_segment(source, setup))

    def test_update_location_button_skips_poll_for_wearable(self) -> None:
        source = (COMPONENT_DIR / "button.py").read_text(encoding="utf-8")
        self.assertIn("_is_wearable", source)
        self.assertIn("WEARABLE", source)


if __name__ == "__main__":
    unittest.main()
