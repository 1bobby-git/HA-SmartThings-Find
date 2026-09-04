from __future__ import annotations

from hashlib import sha256
import importlib.util
from pathlib import Path
import re
import sys
import types
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
COMPONENT_DIR = REPO_ROOT / "custom_components" / "smartthings_find"


class _MemoryStore:
    values: dict[str, object] = {}

    def __init__(self, _hass, _version, key, *args, **kwargs) -> None:
        self.key = key

    async def async_load(self):
        return self.values.get(self.key)

    async def async_save(self, value) -> None:
        self.values[self.key] = value

    async def async_remove(self) -> None:
        self.values.pop(self.key, None)


def _parse_cookie_header(cookie_line: str) -> dict[str, str]:
    value = str(cookie_line or "").strip()
    if value.lower().startswith("cookie:"):
        value = value.split(":", 1)[1].strip()
    result: dict[str, str] = {}
    for part in value.split(";"):
        if "=" not in part:
            continue
        name, cookie_value = part.strip().split("=", 1)
        if name:
            result[name] = cookie_value
    return result


def _load_session_store_module():
    homeassistant = sys.modules.setdefault(
        "homeassistant",
        types.ModuleType("homeassistant"),
    )
    core = types.ModuleType("homeassistant.core")
    core.HomeAssistant = object
    helpers = types.ModuleType("homeassistant.helpers")
    storage = types.ModuleType("homeassistant.helpers.storage")
    storage.Store = _MemoryStore
    helpers.storage = storage
    sys.modules["homeassistant.core"] = core
    sys.modules["homeassistant.helpers"] = helpers
    sys.modules["homeassistant.helpers.storage"] = storage
    homeassistant.core = core

    if "aiohttp" not in sys.modules and importlib.util.find_spec("aiohttp") is None:
        aiohttp = types.ModuleType("aiohttp")
        aiohttp.ClientSession = object
        sys.modules["aiohttp"] = aiohttp

    package = sys.modules.setdefault(
        "custom_components.smartthings_find",
        types.ModuleType("custom_components.smartthings_find"),
    )
    package.__path__ = [str(COMPONENT_DIR)]
    sys.modules.setdefault("custom_components", types.ModuleType("custom_components"))

    const_spec = importlib.util.spec_from_file_location(
        "custom_components.smartthings_find.const",
        COMPONENT_DIR / "const.py",
    )
    const_module = importlib.util.module_from_spec(const_spec)
    sys.modules[const_spec.name] = const_module
    const_spec.loader.exec_module(const_module)

    utils = types.ModuleType("custom_components.smartthings_find.utils")
    utils.COOKIE_NAME_RE = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
    utils.STF_BASE = "https://smartthingsfind.samsung.com/"
    utils.parse_cookie_header = _parse_cookie_header
    sys.modules[utils.__name__] = utils

    spec = importlib.util.spec_from_file_location(
        "custom_components.smartthings_find.session_store_v140_test",
        COMPONENT_DIR / "session_store.py",
    )
    module = importlib.util.module_from_spec(spec)
    module.__package__ = "custom_components.smartthings_find"
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


session_store = _load_session_store_module()


class _Entry:
    entry_id = "entry-id"

    def __init__(self, cookie: str, method: str | None = None) -> None:
        self.data = {session_store.CONF_COOKIE: cookie}
        if method is not None:
            self.data[session_store.CONF_AUTH_METHOD] = method


class _CookieJar:
    def __init__(self, values: dict[str, str]) -> None:
        self.values = values

    def filter_cookies(self, _url):
        return {
            name: types.SimpleNamespace(value=value)
            for name, value in self.values.items()
        }


class _Session:
    def __init__(self, cookies: dict[str, str]) -> None:
        self.cookie_jar = _CookieJar(cookies)


class SessionPersistenceV140Tests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        _MemoryStore.values.clear()

    async def test_removed_server_cookie_is_not_resurrected_from_old_snapshot(self) -> None:
        hass = types.SimpleNamespace(data={})
        entry = _Entry("seed=one")

        await session_store.persist_cookie_to_store(
            hass,
            entry,
            _Session({"seed": "one", "removed": "stale"}),
        )
        await session_store.persist_cookie_to_store(
            hass,
            entry,
            _Session({"seed": "one", "fresh": "two"}),
        )

        loaded = await session_store.async_load_cookie_line(hass, entry)
        self.assertEqual(
            {"fresh": "two", "seed": "one"},
            _parse_cookie_header(loaded),
        )
        self.assertNotIn("removed=stale", loaded)

    async def test_empty_authoritative_jar_does_not_restore_configured_seed(self) -> None:
        hass = types.SimpleNamespace(data={})
        entry = _Entry("seed=one")

        await session_store.persist_cookie_to_store(
            hass,
            entry,
            _Session({"seed": "one", "JSESSIONID": "old"}),
        )
        await session_store.persist_cookie_to_store(
            hass,
            entry,
            _Session({}),
        )

        # Use a fresh runtime cache to prove that the empty marker was written
        # to persistent storage rather than held only in memory.
        restarted_hass = types.SimpleNamespace(data={})
        loaded = await session_store.async_load_cookie_line(restarted_hass, entry)

        self.assertEqual("", loaded)

    async def test_account_auth_session_is_persisted_without_manual_cookie_seed(self) -> None:
        hass = types.SimpleNamespace(data={})
        entry = _Entry("", session_store.AUTH_METHOD_ACCOUNT)

        await session_store.persist_cookie_to_store(
            hass,
            entry,
            _Session({"JSESSIONID": "renewed"}),
        )

        loaded = await session_store.async_load_cookie_line(hass, entry)
        self.assertEqual("JSESSIONID=renewed", loaded)

    async def test_v13_cookie_snapshot_is_loaded_and_migrated_in_place(self) -> None:
        hass = types.SimpleNamespace(data={})
        entry = _Entry("seed=one")
        key = f"{session_store.DOMAIN}.{entry.entry_id}.session"
        _MemoryStore.values[key] = {
            "source_hash": sha256(b"seed=one").hexdigest(),
            "cookie": "JSESSIONID=rotated; seed=one",
        }

        loaded = await session_store.async_load_cookie_line(hass, entry)

        self.assertEqual(
            {"JSESSIONID": "rotated", "seed": "one"},
            _parse_cookie_header(loaded),
        )
        expected_source = (
            f"{session_store.AUTH_METHOD_COOKIE}:seed=one".encode("utf-8")
        )
        self.assertEqual(
            sha256(expected_source).hexdigest(),
            _MemoryStore.values[key]["source_hash"],
        )


if __name__ == "__main__":
    unittest.main()
