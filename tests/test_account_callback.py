from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types
import unittest
from urllib.parse import urlsplit


REPO_ROOT = Path(__file__).resolve().parents[1]
COMPONENT_DIR = REPO_ROOT / "custom_components" / "smartthings_find"


def _load_modules():
    sys.modules.setdefault("custom_components", types.ModuleType("custom_components"))
    package = sys.modules.setdefault(
        "custom_components.smartthings_find",
        types.ModuleType("custom_components.smartthings_find"),
    )
    package.__path__ = [str(COMPONENT_DIR)]

    const_name = "custom_components.smartthings_find.const"
    const_spec = importlib.util.spec_from_file_location(
        const_name,
        COMPONENT_DIR / "const.py",
    )
    const_module = importlib.util.module_from_spec(const_spec)
    sys.modules[const_name] = const_module
    const_spec.loader.exec_module(const_module)

    callback_name = "custom_components.smartthings_find.account_callback"
    callback_spec = importlib.util.spec_from_file_location(
        callback_name,
        COMPONENT_DIR / "account_callback.py",
    )
    callback_module = importlib.util.module_from_spec(callback_spec)
    sys.modules[callback_name] = callback_module
    callback_spec.loader.exec_module(callback_module)
    return const_module, callback_module


const, callback = _load_modules()


FIELDS = "state=s&auth_server_url=a&code=c&retValue=r"


class AccountCallbackTests(unittest.TestCase):
    def test_general_pc_default_is_cookie_header(self) -> None:
        self.assertEqual(const.AUTH_METHOD_COOKIE, const.DEFAULT_AUTH_METHOD)

    def test_bare_sign_in_complete_is_explicitly_unavailable(self) -> None:
        with self.assertRaises(callback.SamsungAccountCallbackUnavailable):
            callback.normalize_samsung_account_callback(
                const.SAMSUNG_ACCOUNT_SIGN_IN_COMPLETE_URL
            )

    def test_parameterized_sign_in_complete_is_normalized(self) -> None:
        value = f"{const.SAMSUNG_ACCOUNT_SIGN_IN_COMPLETE_URL}?{FIELDS}"

        normalized = callback.normalize_samsung_account_callback(value)
        parsed = urlsplit(normalized)

        self.assertTrue(normalized.startswith(const.SAMSUNG_ACCOUNT_APP_REDIRECT_URI))
        self.assertEqual(FIELDS, parsed.query)

    def test_fragment_parameters_are_preserved(self) -> None:
        value = f"{const.SAMSUNG_ACCOUNT_SIGN_IN_COMPLETE_URL}#{FIELDS}"

        normalized = callback.normalize_samsung_account_callback(value)

        self.assertEqual(FIELDS, urlsplit(normalized).fragment)

    def test_default_https_port_is_accepted_and_canonicalized(self) -> None:
        value = (
            "https://account.samsung.com:443/accounts/ANDROIDSDK/"
            f"signInComplete?{FIELDS}"
        )

        normalized = callback.normalize_samsung_account_callback(value)

        self.assertEqual(
            f"{const.SAMSUNG_ACCOUNT_APP_REDIRECT_URI}?{FIELDS}",
            normalized,
        )

    def test_complete_native_callback_is_accepted(self) -> None:
        value = f"{const.SAMSUNG_ACCOUNT_APP_REDIRECT_URI}?{FIELDS}"

        self.assertEqual(
            value,
            callback.normalize_samsung_account_callback(value),
        )

    def test_json_escaped_native_callback_is_accepted(self) -> None:
        escaped = const.SAMSUNG_ACCOUNT_APP_REDIRECT_URI.replace(
            "://",
            r":\/\/",
        )

        normalized = callback.normalize_samsung_account_callback(
            f"{escaped}?{FIELDS}"
        )

        self.assertEqual(
            f"{const.SAMSUNG_ACCOUNT_APP_REDIRECT_URI}?{FIELDS}",
            normalized,
        )

    def test_native_callback_can_be_extracted_from_browser_error_text(self) -> None:
        value = (
            "앱을 열 수 없습니다: `"
            f"{const.SAMSUNG_ACCOUNT_APP_REDIRECT_URI}?"
            "state=s&amp;auth_server_url=a&amp;code=c&amp;retValue=r`."
        )

        normalized = callback.normalize_samsung_account_callback(value)

        self.assertEqual(
            f"{const.SAMSUNG_ACCOUNT_APP_REDIRECT_URI}?{FIELDS}",
            normalized,
        )

    def test_raw_callback_field_block_is_accepted(self) -> None:
        normalized = callback.normalize_samsung_account_callback(FIELDS)

        self.assertEqual(
            f"{const.SAMSUNG_ACCOUNT_APP_REDIRECT_URI}?{FIELDS}",
            normalized,
        )

    def test_missing_callback_field_is_rejected(self) -> None:
        value = (
            f"{const.SAMSUNG_ACCOUNT_SIGN_IN_COMPLETE_URL}?"
            "state=s&auth_server_url=a&code=c"
        )

        with self.assertRaises(callback.SamsungAccountCallbackUnavailable):
            callback.normalize_samsung_account_callback(value)

    def test_wrong_https_target_is_rejected(self) -> None:
        value = f"https://example.com/accounts/ANDROIDSDK/signInComplete?{FIELDS}"

        with self.assertRaises(callback.SamsungAccountCallbackError):
            callback.normalize_samsung_account_callback(value)

    def test_lookalike_samsung_host_is_rejected(self) -> None:
        value = (
            "https://account.samsung.com.example.org/accounts/ANDROIDSDK/"
            f"signInComplete?{FIELDS}"
        )

        with self.assertRaises(callback.SamsungAccountCallbackError):
            callback.normalize_samsung_account_callback(value)


if __name__ == "__main__":
    unittest.main()
