from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = (
    REPO_ROOT
    / "custom_components"
    / "smartthings_find"
    / "auth_input.py"
)


def _load_module():
    name = "smartthings_find_auth_input_test"
    spec = importlib.util.spec_from_file_location(name, MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


auth_input = _load_module()


class AuthInputTests(unittest.TestCase):
    def test_detects_samsung_account_cookie_names(self) -> None:
        self.assertTrue(
            auth_input.has_samsung_account_cookie_markers(
                {
                    "sa_did": "redacted",
                    "USAWSWIPSESSIONID": "redacted",
                    "JSESSIONID": "redacted",
                }
            )
        )

    def test_detection_is_case_insensitive(self) -> None:
        self.assertTrue(
            auth_input.has_samsung_account_cookie_markers(
                {"G_ENABLED_IDPS": "google"}
            )
        )

    def test_typical_find_cookie_names_are_not_classified_as_account(self) -> None:
        self.assertFalse(
            auth_input.has_samsung_account_cookie_markers(
                {
                    "JSESSIONID": "redacted",
                    "WMONID": "redacted",
                    "_csrf": "redacted",
                }
            )
        )

    def test_cookie_values_are_not_inspected(self) -> None:
        self.assertFalse(
            auth_input.has_samsung_account_cookie_markers(
                {"JSESSIONID": "sa_did=not-a-cookie-name"}
            )
        )

    def test_empty_cookie_mapping_is_not_classified(self) -> None:
        self.assertFalse(
            auth_input.has_samsung_account_cookie_markers({})
        )


if __name__ == "__main__":
    unittest.main()
