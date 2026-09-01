from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest

from test_utils_location import COMPONENT_DIR, _load_utils_module


utils = _load_utils_module()


class _MemoryStore:
    values: dict[str, object] = {}

    def __init__(self, _hass, _version, key, *args, **kwargs) -> None:
        self.key = key

    async def async_load(self):
        return self.values.get(self.key)

    async def async_save(self, value) -> None:
        self.values[self.key] = value


def _load_component_module(name: str):
    storage = types.ModuleType("homeassistant.helpers.storage")
    storage.Store = _MemoryStore
    sys.modules["homeassistant.helpers.storage"] = storage

    spec = importlib.util.spec_from_file_location(
        f"custom_components.smartthings_find.{name}",
        Path(COMPONENT_DIR) / f"{name}.py",
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


session_store = _load_component_module("session_store")
device_inventory = _load_component_module("device_inventory")


class _Entry:
    entry_id = "entry-id"

    def __init__(self, cookie: str) -> None:
        self.data = {utils.CONF_COOKIE: cookie}


class _CookieJar:
    def __init__(self, values: dict[str, str]) -> None:
        self.values = values

    def filter_cookies(self, _url):
        return {
            name: types.SimpleNamespace(value=value)
            for name, value in self.values.items()
        }


class _Session:
    def __init__(self, response=None, cookies=None) -> None:
        self.response = response
        self.cookie_jar = _CookieJar(cookies or {})

    def post(self, *_args, **_kwargs):
        return self.response


class _Response:
    def __init__(self, payload, status: int = 200) -> None:
        self.status = status
        self.payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def text(self) -> str:
        return json.dumps(self.payload)


class SessionStoreTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        _MemoryStore.values.clear()

    async def test_rotating_cookie_is_saved_without_config_entry_mutation(self) -> None:
        hass = types.SimpleNamespace(data={})
        entry = _Entry("seed=one")
        session = _Session(cookies={"seed": "one", "rotated": "two"})

        await session_store.persist_cookie_to_store(hass, entry, session)
        loaded = await session_store.async_load_cookie_line(hass, entry)

        self.assertEqual("rotated=two; seed=one", loaded)
        self.assertFalse(hasattr(hass, "config_entries"))

    async def test_replaced_configured_cookie_invalidates_old_rotated_cookie(self) -> None:
        hass = types.SimpleNamespace(data={})
        old_entry = _Entry("seed=one")
        await session_store.persist_cookie_to_store(
            hass,
            old_entry,
            _Session(cookies={"seed": "one", "rotated": "old"}),
        )

        new_entry = _Entry("seed=new")
        loaded = await session_store.async_load_cookie_line(hass, new_entry)

        self.assertEqual("seed=new", loaded)


class DeviceInventoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_inventory_uses_only_this_integration_identifier(self) -> None:
        payload = {
            "deviceList": [
                {
                    "dvceID": "tag-1",
                    "modelName": "SmartTag &amp; One",
                    "modelID": "EI-T5600",
                }
            ]
        }
        hass = types.SimpleNamespace(
            data={utils.DOMAIN: {"config_flow": {"_csrf": "csrf"}}},
            config_entries=types.SimpleNamespace(
                async_get_entry=lambda _entry_id: None
            ),
        )

        devices = await device_inventory.get_devices(
            hass,
            _Session(response=_Response(payload)),
            "config_flow",
        )

        self.assertEqual(1, len(devices))
        self.assertEqual("SmartTag & One", devices[0]["data"]["modelName"])
        self.assertEqual(
            {(utils.DOMAIN, "tag-1")},
            devices[0]["ha_dev_info"]["identifiers"],
        )


if __name__ == "__main__":
    unittest.main()
