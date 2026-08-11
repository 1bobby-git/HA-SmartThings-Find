from __future__ import annotations

import importlib.util
import json
import sys
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
COMPONENT_DIR = REPO_ROOT / "custom_components" / "smartthings_find"


def _install_homeassistant_stubs() -> None:
    homeassistant = types.ModuleType("homeassistant")

    core = types.ModuleType("homeassistant.core")
    core.HomeAssistant = object

    exceptions = types.ModuleType("homeassistant.exceptions")

    class ConfigEntryAuthFailed(Exception):
        pass

    class HomeAssistantError(Exception):
        pass

    exceptions.ConfigEntryAuthFailed = ConfigEntryAuthFailed
    exceptions.HomeAssistantError = HomeAssistantError

    helpers = types.ModuleType("homeassistant.helpers")
    device_registry = types.ModuleType("homeassistant.helpers.device_registry")

    class DeviceInfo(dict):
        pass

    device_registry.DeviceInfo = DeviceInfo
    device_registry.async_get = lambda hass: types.SimpleNamespace(devices={})
    helpers.device_registry = device_registry

    sys.modules.setdefault("homeassistant", homeassistant)
    sys.modules.setdefault("homeassistant.core", core)
    sys.modules.setdefault("homeassistant.exceptions", exceptions)
    sys.modules.setdefault("homeassistant.helpers", helpers)
    sys.modules.setdefault("homeassistant.helpers.device_registry", device_registry)


def _install_optional_dependency_stubs() -> None:
    if importlib.util.find_spec("aiohttp") is None:
        aiohttp = types.ModuleType("aiohttp")

        class ClientSession:
            pass

        class CookieJar:
            def __init__(self, *args, **kwargs) -> None:
                pass

        class ClientTimeout:
            def __init__(self, *args, **kwargs) -> None:
                pass

        aiohttp.ClientSession = ClientSession
        aiohttp.CookieJar = CookieJar
        aiohttp.ClientTimeout = ClientTimeout
        sys.modules["aiohttp"] = aiohttp

    if importlib.util.find_spec("yarl") is None:
        yarl = types.ModuleType("yarl")

        class URL:
            def __init__(self, value: str) -> None:
                self.value = value

            def __truediv__(self, path: str):
                return URL(f"{self.value.rstrip('/')}/{path.lstrip('/')}")

            def update_query(self, _query: dict[str, Any]):
                return self

            def __str__(self) -> str:
                return self.value

        yarl.URL = URL
        sys.modules["yarl"] = yarl


def _load_utils_module():
    _install_homeassistant_stubs()
    _install_optional_dependency_stubs()

    package = types.ModuleType("custom_components.smartthings_find")
    package.__path__ = [str(COMPONENT_DIR)]
    sys.modules.setdefault("custom_components", types.ModuleType("custom_components"))
    sys.modules.setdefault("custom_components.smartthings_find", package)

    const_spec = importlib.util.spec_from_file_location(
        "custom_components.smartthings_find.const",
        COMPONENT_DIR / "const.py",
    )
    const_module = importlib.util.module_from_spec(const_spec)
    sys.modules[const_spec.name] = const_module
    const_spec.loader.exec_module(const_module)

    utils_spec = importlib.util.spec_from_file_location(
        "custom_components.smartthings_find.utils",
        COMPONENT_DIR / "utils.py",
    )
    utils_module = importlib.util.module_from_spec(utils_spec)
    sys.modules[utils_spec.name] = utils_module
    utils_spec.loader.exec_module(utils_module)
    return utils_module


utils = _load_utils_module()


class _FakeResponse:
    status = 200

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def text(self) -> str:
        return json.dumps(self._payload)


class _FakeSession:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def post(self, *_args, **_kwargs):
        return _FakeResponse(self.payload)


class UtilsLocationTest(unittest.IsolatedAsyncioTestCase):
    def test_normalize_location_accuracy_never_returns_none(self) -> None:
        self.assertEqual(utils.normalize_location_accuracy(None), 0)
        self.assertEqual(utils.normalize_location_accuracy("invalid"), 0)
        self.assertEqual(utils.normalize_location_accuracy(float("nan")), 0)
        self.assertEqual(utils.normalize_location_accuracy(-1), 0)
        self.assertEqual(utils.normalize_location_accuracy(5.6), 6)

    def test_parse_stf_date_preserves_utc_timezone(self) -> None:
        self.assertEqual(
            utils.parse_stf_date("20260804123456"),
            datetime(2026, 8, 4, 12, 34, 56, tzinfo=timezone.utc),
        )

    async def test_bad_gps_utc_date_operations_do_not_discard_valid_location_or_battery(self) -> None:
        bad_cases = [
            {"extra": {"gpsUtcDt": "2026-08-04T12:34:56Z"}},
            {"extra": {"gpsUtcDt": ""}},
            {"extra": {}},
        ]

        for bad_fields in bad_cases:
            with self.subTest(bad_fields=bad_fields):
                payload = {
                    "operation": [
                        {
                            "oprnType": "LOCATION",
                            "latitude": "1.25",
                            "longitude": "2.5",
                            **bad_fields,
                        },
                        {"oprnType": utils.OP_CHECK_CONNECTION, "battery": "HIGH"},
                        {
                            "oprnType": "LASTLOC",
                            "latitude": "37.5665",
                            "longitude": "126.9780",
                            "horizontalUncertainty": "3",
                            "verticalUncertainty": "4",
                            "extra": {"gpsUtcDt": "20260804101010"},
                        },
                    ]
                }

                hass = types.SimpleNamespace(
                    data={
                        utils.DOMAIN: {
                            "entry-id": {
                                "_csrf": "csrf-token",
                                utils.CONF_ACTIVE_MODE_SMARTTAGS: False,
                                utils.CONF_ACTIVE_MODE_OTHERS: False,
                            }
                        }
                    }
                )

                result = await utils.get_device_location(
                    hass,
                    _FakeSession(payload),
                    {"dvceID": "device-id", "modelName": "Tracker", "deviceTypeCode": "TAG"},
                    "entry-id",
                )

                self.assertIsNotNone(result)
                self.assertTrue(result["location_found"])
                self.assertEqual(result["battery_level"], 80)
                self.assertEqual(result["used_loc"]["latitude"], 37.5665)
                self.assertEqual(result["used_loc"]["longitude"], 126.978)
                self.assertEqual(result["used_loc"]["gps_accuracy"], 5.0)
                self.assertEqual(result["used_loc"]["gps_date"], utils.parse_stf_date("20260804101010"))


if __name__ == "__main__":
    unittest.main()
