from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
COMPONENT_DIR = REPO_ROOT / "custom_components" / "smartthings_find"


def _load_auth_manager_module():
    homeassistant = sys.modules.setdefault(
        "homeassistant",
        types.ModuleType("homeassistant"),
    )
    config_entries = types.ModuleType("homeassistant.config_entries")
    config_entries.ConfigEntry = object
    core = types.ModuleType("homeassistant.core")
    core.HomeAssistant = object
    exceptions = types.ModuleType("homeassistant.exceptions")

    class ConfigEntryAuthFailed(Exception):
        pass

    class HomeAssistantError(Exception):
        pass

    exceptions.ConfigEntryAuthFailed = ConfigEntryAuthFailed
    exceptions.HomeAssistantError = HomeAssistantError
    sys.modules["homeassistant.config_entries"] = config_entries
    sys.modules["homeassistant.core"] = core
    sys.modules["homeassistant.exceptions"] = exceptions
    homeassistant.config_entries = config_entries

    aiohttp = sys.modules.get("aiohttp")
    if aiohttp is None:
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

    account_auth = types.ModuleType("custom_components.smartthings_find.account_auth")

    class SamsungAccountAuthError(Exception):
        pass

    class SamsungAccountAuth:
        instances = []

        def __init__(self, _hass) -> None:
            self.calls = []
            self.__class__.instances.append(self)

        async def async_cookie(self, *, force_refresh=False):
            self.calls.append(force_refresh)
            return "JSESSIONID=fresh-session"

    account_auth.SamsungAccountAuth = SamsungAccountAuth
    account_auth.SamsungAccountAuthError = SamsungAccountAuthError
    sys.modules[account_auth.__name__] = account_auth

    inventory = types.ModuleType("custom_components.smartthings_find.device_inventory")

    async def get_devices(*_args, **_kwargs):
        return []

    inventory.get_devices = get_devices
    sys.modules[inventory.__name__] = inventory

    session_store = types.ModuleType("custom_components.smartthings_find.session_store")

    scoped_snapshot: list = []

    async def async_load_cookie_line(_hass, entry):
        return str(entry.data.get("cookie") or "")

    async def async_load_session_cookies(_hass, entry):
        return str(entry.data.get("cookie") or ""), list(scoped_snapshot)

    persisted_cookie_snapshots = []
    persist_kwargs = []

    async def persist_cookie_to_store(_hass, _entry, session, **kwargs):
        persisted_cookie_snapshots.append(dict(session.cookie_jar.values))
        persist_kwargs.append(kwargs)

    def cookie_source_hash(entry):
        return "source:" + str(entry.data.get("cookie") or "")

    session_store.async_load_cookie_line = async_load_cookie_line
    session_store.async_load_session_cookies = async_load_session_cookies
    session_store.cookie_source_hash = cookie_source_hash
    session_store.persist_cookie_to_store = persist_cookie_to_store
    sys.modules[session_store.__name__] = session_store

    utils = types.ModuleType("custom_components.smartthings_find.utils")

    def parse_cookie_header(line):
        result = {}
        for part in str(line or "").replace("Cookie:", "", 1).split(";"):
            if "=" in part:
                key, value = part.strip().split("=", 1)
                if key:
                    result[key] = value
        return result

    def apply_cookies_to_session(session, cookies):
        session.cookie_jar.values.update(cookies)

    applied_scoped: list = []
    normalize_calls: list = []
    normalize_seeds: list = []

    def apply_scoped_cookies(session, records):
        applied_scoped.append(list(records))

    def normalize_cookie_jar(session, seeds=None):
        normalize_calls.append(dict(session.cookie_jar.values))
        normalize_seeds.append(None if seeds is None else dict(seeds))
        return 0

    async def placeholder(*_args, **_kwargs):
        return None

    utils.COOKIE_SEEDS_KEY = "_cookie_seeds"
    utils.apply_cookies_to_session = apply_cookies_to_session
    utils.apply_scoped_cookies = apply_scoped_cookies
    utils.normalize_cookie_jar = normalize_cookie_jar
    utils.clear_auth_failure = lambda *_args, **_kwargs: None
    utils.fetch_csrf = placeholder
    utils.get_device_location = placeholder
    utils.keepalive_ping = placeholder
    utils.parse_cookie_header = parse_cookie_header
    utils.send_operation = placeholder
    sys.modules[utils.__name__] = utils

    spec = importlib.util.spec_from_file_location(
        "custom_components.smartthings_find.auth_manager",
        COMPONENT_DIR / "auth_manager.py",
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    module._TestAccountAuth = SamsungAccountAuth
    module._persisted_cookie_snapshots = persisted_cookie_snapshots
    module._persist_kwargs = persist_kwargs
    module._scoped_snapshot = scoped_snapshot
    module._applied_scoped = applied_scoped
    module._normalize_calls = normalize_calls
    module._normalize_seeds = normalize_seeds
    return module


auth_manager = _load_auth_manager_module()


class _CookieJar:
    def __init__(self) -> None:
        self.values = {}
        self.clear_calls = 0

    def clear(self) -> None:
        self.clear_calls += 1
        self.values.clear()


class _Session:
    def __init__(self) -> None:
        self.cookie_jar = _CookieJar()


class _Entry:
    entry_id = "entry-id"

    def __init__(self, method: str, cookie: str = "seed=one") -> None:
        self.data = {
            auth_manager.CONF_AUTH_METHOD: method,
            "cookie": cookie,
        }


class _Hass:
    def __init__(self) -> None:
        self.data = {}


class AuthManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_read_retries_once_after_fresh_csrf(self) -> None:
        hass = _Hass()
        session = _Session()
        manager = auth_manager.SmartThingsFindAuthManager(
            hass,
            _Entry(auth_manager.AUTH_METHOD_COOKIE),
            session,
        )

        csrf_calls = 0

        async def fetch_csrf(*_args, **_kwargs):
            nonlocal csrf_calls
            csrf_calls += 1
            hass.data.setdefault(auth_manager.DOMAIN, {}).setdefault(
                "entry-id", {}
            )["_csrf"] = "fresh"
            return "fresh"

        auth_manager.fetch_csrf = fetch_csrf
        operation_calls = 0

        async def operation():
            nonlocal operation_calls
            operation_calls += 1
            if operation_calls == 1:
                raise auth_manager.ConfigEntryAuthFailed("stale csrf")
            return "ok"

        self.assertEqual("ok", await manager.async_read(operation))
        self.assertEqual(2, operation_calls)
        self.assertEqual(1, csrf_calls)

    async def test_auth_failure_persists_server_cookie_deletion_before_repair(self) -> None:
        hass = _Hass()
        session = _Session()
        session.cookie_jar.values["JSESSIONID"] = "old-session"
        manager = auth_manager.SmartThingsFindAuthManager(
            hass,
            _Entry(auth_manager.AUTH_METHOD_COOKIE),
            session,
        )
        auth_manager._persisted_cookie_snapshots.clear()

        async def fetch_csrf(*_args, **_kwargs):
            raise auth_manager.ConfigEntryAuthFailed("expired")

        auth_manager.fetch_csrf = fetch_csrf

        async def operation():
            session.cookie_jar.clear()
            raise auth_manager.ConfigEntryAuthFailed("server deleted cookie")

        with self.assertRaises(auth_manager.ConfigEntryAuthFailed):
            await manager.async_read(operation)

        self.assertGreaterEqual(len(auth_manager._persisted_cookie_snapshots), 1)
        self.assertEqual({}, auth_manager._persisted_cookie_snapshots[0])

    async def test_account_mode_rebuilds_web_session_after_csrf_repair_fails(self) -> None:
        hass = _Hass()
        session = _Session()
        manager = auth_manager.SmartThingsFindAuthManager(
            hass,
            _Entry(auth_manager.AUTH_METHOD_ACCOUNT, cookie=""),
            session,
        )

        csrf_calls = 0

        async def fetch_csrf(*_args, **_kwargs):
            nonlocal csrf_calls
            csrf_calls += 1
            if csrf_calls == 1:
                raise auth_manager.ConfigEntryAuthFailed("expired")
            return "fresh"

        auth_manager.fetch_csrf = fetch_csrf
        operation_calls = 0

        async def operation():
            nonlocal operation_calls
            operation_calls += 1
            if operation_calls == 1:
                raise auth_manager.ConfigEntryAuthFailed("expired")
            return "recovered"

        self.assertEqual("recovered", await manager.async_read(operation))
        self.assertEqual(2, operation_calls)
        self.assertEqual(2, csrf_calls)
        self.assertEqual("fresh-session", session.cookie_jar.values["JSESSIONID"])
        self.assertEqual([True], manager._account_auth.calls)

    async def test_active_location_cycle_is_not_replayed_after_dispatch(self) -> None:
        hass = _Hass()
        hass.data = {
            auth_manager.DOMAIN: {
                "entry-id": {auth_manager.CONF_ACTIVE_MODE_SMARTTAGS: True}
            }
        }
        session = _Session()
        manager = auth_manager.SmartThingsFindAuthManager(
            hass,
            _Entry(auth_manager.AUTH_METHOD_ACCOUNT, cookie=""),
            session,
        )

        async def fetch_csrf(*_args, **_kwargs):
            return "fresh"

        auth_manager.fetch_csrf = fetch_csrf
        location_calls = 0

        async def get_device_location(*_args, **_kwargs):
            nonlocal location_calls
            location_calls += 1
            raise auth_manager.ConfigEntryAuthFailed("ambiguous active cycle")

        auth_manager.get_device_location = get_device_location

        with self.assertRaises(auth_manager.ConfigEntryAuthFailed):
            await manager.async_get_device_location(
                {"deviceTypeCode": "TAG", "dvceID": "tag-1"}
            )

        self.assertEqual(1, location_calls)
        self.assertEqual([True], manager._account_auth.calls)

    async def test_effect_operation_is_never_replayed_after_dispatch(self) -> None:
        hass = _Hass()
        session = _Session()
        manager = auth_manager.SmartThingsFindAuthManager(
            hass,
            _Entry(auth_manager.AUTH_METHOD_ACCOUNT, cookie=""),
            session,
        )

        async def fetch_csrf(*_args, **_kwargs):
            return "fresh"

        auth_manager.fetch_csrf = fetch_csrf
        send_calls = 0

        async def send_operation(*_args, **_kwargs):
            nonlocal send_calls
            send_calls += 1
            raise auth_manager.ConfigEntryAuthFailed("ambiguous response")

        auth_manager.send_operation = send_operation

        with self.assertRaises(auth_manager.ConfigEntryAuthFailed):
            await manager.async_send_operation({"operation": "RING"})

        self.assertEqual(1, send_calls)
        self.assertEqual([True], manager._account_auth.calls)

    async def test_new_manager_clears_a_stale_reconfiguring_guard(self) -> None:
        """Reauth while the entry was not loaded must not disable persistence (#9)."""
        hass = _Hass()
        hass.data = {auth_manager.DOMAIN: {"entry-id": {"_auth_reconfiguring": True}}}
        session = _Session()
        manager = auth_manager.SmartThingsFindAuthManager(
            hass,
            _Entry(auth_manager.AUTH_METHOD_COOKIE),
            session,
        )
        self.assertNotIn(
            "_auth_reconfiguring", hass.data[auth_manager.DOMAIN]["entry-id"]
        )

        auth_manager._persisted_cookie_snapshots.clear()
        session.cookie_jar.values["JSESSIONID"] = "rotated"

        async def operation():
            return "ok"

        self.assertEqual("ok", await manager.async_read(operation))
        self.assertEqual(
            [{"JSESSIONID": "rotated"}], auth_manager._persisted_cookie_snapshots
        )

    async def test_jar_is_reconciled_but_not_saved_while_a_flow_replaces_credentials(
        self,
    ) -> None:
        hass = _Hass()
        session = _Session()
        manager = auth_manager.SmartThingsFindAuthManager(
            hass,
            _Entry(auth_manager.AUTH_METHOD_COOKIE),
            session,
        )
        hass.data[auth_manager.DOMAIN]["entry-id"]["_auth_reconfiguring"] = True
        auth_manager._persisted_cookie_snapshots.clear()
        auth_manager._normalize_calls.clear()

        async def operation():
            return "ok"

        await manager.async_read(operation)

        self.assertEqual([], auth_manager._persisted_cookie_snapshots)
        self.assertGreaterEqual(len(auth_manager._normalize_calls), 1)

    async def test_initialize_restores_domain_and_path_scoped_cookies(self) -> None:
        hass = _Hass()
        session = _Session()
        manager = auth_manager.SmartThingsFindAuthManager(
            hass,
            _Entry(auth_manager.AUTH_METHOD_COOKIE, cookie="JSESSIONID=seed"),
            session,
        )
        record = {"name": "SSO", "value": "v", "domain": "samsung.com", "path": "/"}
        auth_manager._scoped_snapshot[:] = [record]
        auth_manager._applied_scoped.clear()

        async def fetch_csrf(*_args, **_kwargs):
            return "fresh"

        auth_manager.fetch_csrf = fetch_csrf
        try:
            await manager.async_initialize()
        finally:
            auth_manager._scoped_snapshot.clear()

        self.assertEqual({"JSESSIONID": "seed"}, session.cookie_jar.values)
        self.assertEqual([[record]], auth_manager._applied_scoped)

    async def test_cookie_save_failure_is_recorded_for_diagnostics(self) -> None:
        hass = _Hass()
        session = _Session()
        manager = auth_manager.SmartThingsFindAuthManager(
            hass,
            _Entry(auth_manager.AUTH_METHOD_COOKIE),
            session,
        )
        original = auth_manager.persist_cookie_to_store

        async def failing_persist(*_args, **_kwargs):
            raise OSError("storage unavailable")

        async def operation():
            return "ok"

        auth_manager.persist_cookie_to_store = failing_persist
        try:
            self.assertEqual("ok", await manager.async_read(operation))
        finally:
            auth_manager.persist_cookie_to_store = original

        record = hass.data[auth_manager.DOMAIN]["entry-id"][
            auth_manager.DIAG_COOKIE_STORE
        ]
        self.assertEqual("OSError", record["last_save_error"])
        self.assertIn("last_save_error_at", record)

    async def test_saves_stay_bound_to_the_credential_the_jar_was_seeded_from(
        self,
    ) -> None:
        """A flow that replaces the cookie mid-setup must not get the old jar saved."""
        hass = _Hass()
        session = _Session()
        entry = _Entry(auth_manager.AUTH_METHOD_COOKIE, cookie="JSESSIONID=old")
        manager = auth_manager.SmartThingsFindAuthManager(hass, entry, session)

        async def fetch_csrf(*_args, **_kwargs):
            return "fresh"

        auth_manager.fetch_csrf = fetch_csrf
        auth_manager._persist_kwargs.clear()
        await manager.async_initialize()
        entry.data["cookie"] = "JSESSIONID=new"

        async def operation():
            return "ok"

        await manager.async_read(operation)

        self.assertTrue(auth_manager._persist_kwargs)
        self.assertEqual(
            {"seeded_source_hash": "source:JSESSIONID=old"},
            auth_manager._persist_kwargs[-1],
        )

    async def test_seeded_values_are_kept_for_jar_normalization(self) -> None:
        hass = _Hass()
        session = _Session()
        manager = auth_manager.SmartThingsFindAuthManager(
            hass,
            _Entry(auth_manager.AUTH_METHOD_COOKIE, cookie="JSESSIONID=seed; X=pasted"),
            session,
        )

        async def fetch_csrf(*_args, **_kwargs):
            return "fresh"

        auth_manager.fetch_csrf = fetch_csrf
        auth_manager._normalize_seeds.clear()
        await manager.async_initialize()

        seeds = {"JSESSIONID": "seed", "X": "pasted"}
        self.assertEqual(
            seeds, hass.data[auth_manager.DOMAIN]["entry-id"][auth_manager.COOKIE_SEEDS_KEY]
        )
        self.assertTrue(auth_manager._normalize_seeds)
        self.assertTrue(all(call == seeds for call in auth_manager._normalize_seeds))

    async def test_rotated_cookies_are_saved_when_a_read_fails_without_auth_error(
        self,
    ) -> None:
        hass = _Hass()
        session = _Session()
        manager = auth_manager.SmartThingsFindAuthManager(
            hass,
            _Entry(auth_manager.AUTH_METHOD_COOKIE),
            session,
        )
        auth_manager._persisted_cookie_snapshots.clear()

        async def operation():
            session.cookie_jar.values["JSESSIONID"] = "rotated-by-chklogin"
            raise RuntimeError("device list outage")

        with self.assertRaises(RuntimeError):
            await manager.async_read(operation)

        self.assertEqual(
            [{"JSESSIONID": "rotated-by-chklogin"}],
            auth_manager._persisted_cookie_snapshots,
        )


if __name__ == "__main__":
    unittest.main()
