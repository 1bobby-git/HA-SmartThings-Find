"""KeepAlive through the real auth manager, async_read and the coordinator."""

from __future__ import annotations

import importlib.util
import json
import sys
import types
import unittest
from datetime import datetime, timezone
from unittest import mock

from aiohttp_support import real_aiohttp_available

HAS_AIOHTTP = real_aiohttp_available()

from test_utils_location import COMPONENT_DIR, _load_utils_module  # noqa: E402
from test_v146_recovery import coordinator_module  # noqa: E402

utils = _load_utils_module()
ConfigEntryAuthFailed = utils.ConfigEntryAuthFailed
HomeAssistantError = utils.HomeAssistantError
PACKAGE = "custom_components.smartthings_find"


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


def _load(name: str):
    spec = importlib.util.spec_from_file_location(
        f"{PACKAGE}.{name}_flow_test", COMPONENT_DIR / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    module.__package__ = PACKAGE
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_real_auth_manager():
    """Load auth_manager.py against the real utils, store, inventory and account auth.

    Other test modules install stubs under the canonical module names; they are
    swapped out only while these modules import and restored afterwards.
    """
    config_entries = sys.modules.setdefault(
        "homeassistant.config_entries", types.ModuleType("homeassistant.config_entries")
    )
    if not hasattr(config_entries, "ConfigEntry"):
        config_entries.ConfigEntry = object
    storage = types.ModuleType("homeassistant.helpers.storage")
    storage.Store = _MemoryStore

    names = ("utils", "session_store", "device_inventory", "account_auth")
    canonical = [f"{PACKAGE}.{name}" for name in names] + ["homeassistant.helpers.storage"]
    previous = {name: sys.modules.get(name) for name in canonical}
    try:
        sys.modules["homeassistant.helpers.storage"] = storage
        sys.modules[f"{PACKAGE}.utils"] = utils
        for name in names[1:]:
            sys.modules[f"{PACKAGE}.{name}"] = _load(name)
        return _load("auth_manager")
    finally:
        for name, module in previous.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


if HAS_AIOHTTP:
    import aiohttp

    auth_manager_module = _load_real_auth_manager()


class _Response:
    def __init__(self, body: str, *, status: int = 200, csrf: str | None = None) -> None:
        self._body = body
        self.status = status
        self.headers = {"_csrf": csrf} if csrf else {}
        self.url = types.SimpleNamespace(host=utils.STF_HOST)
        self.history = ()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def text(self, **_kwargs) -> str:
        return self._body


class _Session:
    """Real aiohttp cookie jar with scripted chkLogin.do and device list replies."""

    def __init__(
        self,
        chk_login: list[_Response],
        device_list: list[_Response],
        *,
        chk_login_after: str | None = None,
    ) -> None:
        self.cookie_jar = aiohttp.CookieJar(unsafe=True, quote_cookie=False)
        self.chk_login = list(chk_login)
        self.device_list = list(device_list)
        self.chk_login_after = chk_login_after
        self.get_calls = 0
        self.post_calls = 0

    def get(self, _url, **_kwargs):
        self.get_calls += 1
        if not self.chk_login and self.chk_login_after is not None:
            return _Response(self.chk_login_after)
        return self.chk_login.pop(0)

    def post(self, _url, **_kwargs):
        self.post_calls += 1
        return self.device_list.pop(0)


class _Entry:
    entry_id = "entry-id"

    def __init__(self) -> None:
        self.data = {"auth_method": "cookie", utils.CONF_COOKIE: "JSESSIONID=seed"}
        self.options = {}
        self.reauth_calls = 0

    def async_start_reauth(self, _hass) -> None:
        self.reauth_calls += 1


async def _no_sleep(_delay) -> None:
    return None


@unittest.skipUnless(HAS_AIOHTTP, "requires aiohttp")
class KeepaliveFlowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        _MemoryStore.values.clear()

    async def _loaded(self, chk_login, device_list, **session_kwargs):
        """Return a coordinator whose manager was initialized with a valid session."""
        hass = types.SimpleNamespace(data={})
        entry = _Entry()
        session = _Session(
            [_Response("success", csrf="setup-csrf"), *chk_login], device_list, **session_kwargs
        )
        manager = auth_manager_module.SmartThingsFindAuthManager(hass, entry, session)
        await manager.async_initialize()
        coordinator = coordinator_module.SmartThingsFindCoordinator(
            hass=hass,
            entry=entry,
            session=session,
            auth_manager=manager,
            devices=[],
            update_interval_s=120,
        )
        return coordinator, hass, entry, session

    async def test_working_device_list_outweighs_a_rejected_chk_login(self) -> None:
        coordinator, hass, entry, session = await self._loaded(
            [_Response("fail")], [_Response(json.dumps({"deviceList": []}))]
        )
        runtime = hass.data[utils.DOMAIN]["entry-id"]
        runtime["auth_failure_started"] = datetime.now(tz=timezone.utc)

        await coordinator._async_keepalive()

        self.assertEqual(2, session.get_calls)
        self.assertEqual("setup-csrf", runtime["_csrf"])
        self.assertNotIn("auth_failure_started", runtime)
        self.assertIn("last_success", runtime[utils.DIAG_KEEPALIVE])
        self.assertEqual(0, entry.reauth_calls)

    async def test_maintenance_pages_leave_the_session_and_the_timer_alone(self) -> None:
        coordinator, hass, entry, session = await self._loaded(
            [_Response("<html>maintenance</html>")], [_Response("<html>maintenance</html>")]
        )
        runtime = hass.data[utils.DOMAIN]["entry-id"]

        await coordinator._async_keepalive()

        self.assertEqual(2, session.get_calls)
        self.assertEqual("setup-csrf", runtime["_csrf"])
        self.assertNotIn("auth_failure_started", runtime)
        self.assertEqual("HomeAssistantError", runtime[utils.DIAG_KEEPALIVE]["last_error"])
        self.assertEqual(0, entry.reauth_calls)

    async def test_expired_session_is_repaired_then_counted_toward_reauth(self) -> None:
        coordinator, hass, entry, session = await self._loaded(
            [], [_Response("Logout")], chk_login_after="fail"
        )
        runtime = hass.data[utils.DOMAIN]["entry-id"]

        with mock.patch.object(utils.asyncio, "sleep", _no_sleep):
            await coordinator._async_keepalive()

        # KeepAlive, the CSRF repair (3 tries) and the session rebuild (4 tries).
        self.assertEqual(1 + 1 + 3 + 4, session.get_calls)
        self.assertNotIn("_csrf", runtime)
        self.assertIn("auth_failure_started", runtime)
        self.assertEqual("ConfigEntryAuthFailed", runtime[utils.DIAG_KEEPALIVE]["last_error"])
        self.assertEqual(0, entry.reauth_calls)
        self.assertEqual(1, session.post_calls)


if __name__ == "__main__":
    unittest.main()
