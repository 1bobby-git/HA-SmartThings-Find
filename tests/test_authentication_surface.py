from __future__ import annotations

import ast
import json
from pathlib import Path
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
COMPONENT_DIR = REPO_ROOT / "custom_components" / "smartthings_find"


def _class_node(tree: ast.AST, name: str) -> ast.ClassDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError(f"class not found: {name}")


def _method_node(class_node: ast.ClassDef, name: str) -> ast.AsyncFunctionDef:
    for node in class_node.body:
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return node
    raise AssertionError(f"async method not found: {name}")


def _called_attributes(node: ast.AST) -> set[str]:
    return {
        call.func.attr
        for call in ast.walk(node)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
    }


class AuthenticationSurfaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config_flow_path = COMPONENT_DIR / "config_flow.py"
        self.account_auth_path = COMPONENT_DIR / "account_auth.py"
        self.const_path = COMPONENT_DIR / "const.py"
        self.config_flow_source = self.config_flow_path.read_text(encoding="utf-8")
        self.account_auth_source = self.account_auth_path.read_text(encoding="utf-8")
        self.const_source = self.const_path.read_text(encoding="utf-8")

    def test_setup_reauth_and_reconfigure_all_use_cookie_step(self) -> None:
        tree = ast.parse(self.config_flow_source)
        flow = _class_node(tree, "SmartThingsFindConfigFlow")
        method_names = {
            node.name
            for node in flow.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }

        self.assertNotIn("async_step_account", method_names)
        for name in (
            "async_step_user",
            "async_step_reauth",
            "async_step_reconfigure",
        ):
            method = _method_node(flow, name)
            self.assertIn("async_step_cookie", _called_attributes(method))

    def test_auth_method_selector_and_callback_input_are_removed(self) -> None:
        tree = ast.parse(self.config_flow_source)
        top_level_functions = {
            node.name
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }

        self.assertNotIn("_auth_method_selector", top_level_functions)
        self.assertNotIn("CONF_REDIRECT_URI", self.config_flow_source)
        self.assertNotIn("SamsungAccountCallback", self.config_flow_source)
        self.assertNotIn("async_start(", self.config_flow_source)
        self.assertNotIn("async_complete(", self.config_flow_source)

    def test_legacy_account_adapter_is_runtime_only(self) -> None:
        tree = ast.parse(self.account_auth_source)
        adapter = _class_node(tree, "SamsungAccountAuth")
        method_names = {
            node.name
            for node in adapter.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }

        self.assertIn("async_cookie", method_names)
        self.assertIn("async_remove", method_names)
        self.assertNotIn("async_start", method_names)
        self.assertNotIn("async_complete", method_names)
        self.assertIn("AUTH_METHOD_ACCOUNT", self.const_source)
        self.assertIn("runtime", self.account_auth_source.lower())

    def test_native_callback_module_is_removed(self) -> None:
        self.assertFalse((COMPONENT_DIR / "account_callback.py").exists())
        self.assertFalse((REPO_ROOT / "tests" / "test_account_callback.py").exists())
        self.assertNotIn("SAMSUNG_ACCOUNT_APP_REDIRECT_URI", self.const_source)
        self.assertNotIn("SAMSUNG_ACCOUNT_SIGN_IN_COMPLETE_URL", self.const_source)
        self.assertNotIn("CONF_REDIRECT_URI", self.const_source)

    def test_translations_expose_no_account_enrollment_step(self) -> None:
        paths = [
            COMPONENT_DIR / "strings.json",
            COMPONENT_DIR / "translations" / "ko.json",
            COMPONENT_DIR / "translations" / "en.json",
            COMPONENT_DIR / "translations" / "de.json",
        ]

        for path in paths:
            with self.subTest(path=path.name):
                data = json.loads(path.read_text(encoding="utf-8"))
                steps = data["config"]["step"]
                self.assertEqual({"user", "cookie"}, set(steps))
                self.assertNotIn(
                    "auth_method",
                    steps["user"].get("data", {}),
                )
                self.assertIn(
                    "account_cookie_not_supported",
                    data["config"]["error"],
                )
                self.assertIn(
                    "account_cookie_not_supported",
                    data["options"]["error"],
                )
                self.assertNotIn(
                    "callback_unavailable",
                    data["config"]["error"],
                )
                self.assertNotIn(
                    "invalid_callback",
                    data["config"]["error"],
                )

    def test_release_version_is_1_4_2(self) -> None:
        manifest = json.loads(
            (COMPONENT_DIR / "manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual("1.4.2", manifest["version"])


if __name__ == "__main__":
    unittest.main()
