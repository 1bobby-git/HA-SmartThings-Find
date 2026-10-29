"""Redacted diagnostics added in 1.4.6."""

from __future__ import annotations

import importlib.util
import json
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone
from http.cookies import SimpleCookie

from aiohttp_support import real_aiohttp_available

HAS_AIOHTTP = real_aiohttp_available()

from test_utils_location import COMPONENT_DIR, _load_utils_module  # noqa: E402

utils = _load_utils_module()
ConfigEntryAuthFailed = utils.ConfigEntryAuthFailed

SECRETS = (
    "secret-session",
    "secret/alb==",
    "secret-sso",
    "secret-csrf",
    "device-1",
    "device-2",
    "user-123",
    "Bobby",
    "37.5665",
    "126.978",
)


class _MemoryStore:
    values: dict[str, object] = {}
    fail_load = False

    def __init__(self, _hass, _version, key, *args, **kwargs) -> None:
        self.key = key

    async def async_load(self):
        if self.fail_load:
            raise OSError("storage unavailable")
        return self.values.get(self.key)

    async def async_save(self, value) -> None:
        self.values[self.key] = value

    async def async_remove(self) -> None:
        self.values.pop(self.key, None)


def _load_component(name: str):
    spec = importlib.util.spec_from_file_location(
        f"custom_components.smartthings_find.{name}_diagnostics_test",
        COMPONENT_DIR / f"{name}.py",
    )
    module = importlib.util.module_from_spec(spec)
    module.__package__ = "custom_components.smartthings_find"
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_modules():
    config_entries = sys.modules.setdefault(
        "homeassistant.config_entries", types.ModuleType("homeassistant.config_entries")
    )
    if not hasattr(config_entries, "ConfigEntry"):
        config_entries.ConfigEntry = object
    storage = types.ModuleType("homeassistant.helpers.storage")
    storage.Store = _MemoryStore
    sys.modules["homeassistant.helpers.storage"] = storage
    return _load_component("diagnostics"), _load_component("session_store")


diagnostics, session_store = _load_modules()
DIAG_COOKIE_STORE = diagnostics.DIAG_COOKIE_STORE


class _Entry:
    entry_id = "entry-id"

    def __init__(self, options: dict | None = None) -> None:
        self.data = {
            utils.CONF_COOKIE: "JSESSIONID=secret-session",
            "auth_method": "cookie",
        }
        self.options = options or {}
        self.state = types.SimpleNamespace(value="loaded")


class _LoginResponse:
    status = 200
    headers = {"_csrf": "secret-csrf"}
    history = ()
    url = types.SimpleNamespace(host="smartthingsfind.samsung.com")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def text(self) -> str:
        return "success"


class _LoginSession:
    def get(self, _url, **_kwargs):
        return _LoginResponse()


class _Coordinator:
    def __init__(self) -> None:
        self.update_interval = timedelta(seconds=120)
        self.last_update_success = False
        self.last_exception = ConfigEntryAuthFailed("user-123 expired")
        self.data = {
            "device-1": {
                "dev_name": "Bobby Tag",
                "location_found": True,
                "used_loc": {"latitude": 37.5665, "longitude": 126.978},
            },
            "device-2": {},
        }

    def polling_stopped_by_auth_failure(self) -> bool:
        return not self.last_update_success


class _CookieLineJar:
    def __init__(self, values: dict[str, str]) -> None:
        self.values = values

    def filter_cookies(self, _url):
        return {
            name: types.SimpleNamespace(value=value) for name, value in self.values.items()
        }


class DiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    async def test_unloaded_entry_reports_defaults_without_creating_state(self) -> None:
        hass = types.SimpleNamespace(data={})

        result = await diagnostics.async_get_config_entry_diagnostics(hass, _Entry())

        json.dumps(result)
        self.assertEqual({}, hass.data)
        self.assertEqual("loaded", result["entry_state"])
        self.assertEqual("cookie", result["auth_method"])
        self.assertEqual(120, result["options"]["update_interval"])
        self.assertEqual(180, result["options"]["keepalive_interval"])
        self.assertTrue(result["options"]["active_mode_smarttags"])
        self.assertFalse(result["options"]["active_mode_others"])
        self.assertFalse(result["options"]["polling_disabled"])
        self.assertFalse(result["session"]["active"])
        self.assertIsNone(result["session"]["cookies"])
        self.assertIsNone(result["coordinator"])
        self.assertIsNone(result["auth_failure"])
        self.assertEqual({"count": 0, "by_type": {}}, result["devices"])
        self.assertNotIn("secret-session", json.dumps(result))

    @unittest.skipUnless(HAS_AIOHTTP, "requires aiohttp")
    async def test_loaded_entry_reports_session_state_without_secrets(self) -> None:
        hass = types.SimpleNamespace(data={})
        session = utils.make_session(hass)
        try:
            utils.apply_cookies_to_session(
                session, {"JSESSIONID": "secret-session", "AWSALB": "secret/alb=="}
            )
            parent = SimpleCookie()
            parent.load("SSO=secret-sso; Domain=.samsung.com; Path=/")
            session.cookie_jar.update_cookies(parent, response_url=utils.STF_BASE)
            runtime = {
                "session": session,
                "auth_failure_started": datetime.now(tz=timezone.utc) - timedelta(minutes=5),
                "devices": [
                    {"data": {"dvceID": "device-1", "usrId": "user-123",
                              "modelName": "Bobby Tag", "deviceTypeCode": "TAG"}},
                    {"data": {"dvceID": "device-2", "usrId": "user-123",
                              "modelName": "Bobby Phone", "deviceTypeCode": "PHONE"}},
                ],
                "coordinator": _Coordinator(),
            }
            hass.data[utils.DOMAIN] = {"entry-id": runtime}
            await utils.fetch_csrf(hass, _LoginSession(), "entry-id", retry_delays=())
            utils.record_keepalive(hass, "entry-id", error=ConfigEntryAuthFailed("user-123"))
            utils.record_keepalive(hass, "entry-id")

            result = await diagnostics.async_get_config_entry_diagnostics(hass, _Entry())
        finally:
            await session.close()

        dumped = json.dumps(result)
        for secret in SECRETS:
            self.assertNotIn(secret, dumped)

        cookies = result["session"]["cookies"]
        self.assertEqual(3, cookies["count"])
        self.assertEqual(["AWSALB", "JSESSIONID", "SSO"], cookies["sent_to_site"])
        self.assertIn({"name": "SSO", "domain": "samsung.com", "path": "/"}, cookies["scopes"])
        self.assertTrue(result["session"]["active"])
        self.assertTrue(result["session"]["csrf_cached"])
        self.assertFalse(result["session"]["cookie_saving_paused_by_flow"])
        self.assertEqual(200, result["last_chk_login"]["status"])
        self.assertEqual("text", result["last_chk_login"]["body"])
        self.assertIn("last_success", result["keepalive"])
        self.assertEqual("ConfigEntryAuthFailed", result["keepalive"]["last_error"])
        self.assertGreaterEqual(result["auth_failure"]["elapsed_seconds"], 299)
        self.assertEqual(
            {
                "last_update_success": False,
                "last_error": "ConfigEntryAuthFailed",
                "update_interval_seconds": 120.0,
                "polling_stopped_by_auth_failure": True,
                "devices_reported": 2,
                "devices_with_location": 1,
            },
            result["coordinator"],
        )
        self.assertEqual({"count": 2, "by_type": {"PHONE": 1, "TAG": 1}}, result["devices"])


class CookieStoreDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        _MemoryStore.values.clear()
        _MemoryStore.fail_load = False

    def tearDown(self) -> None:
        _MemoryStore.fail_load = False

    async def test_records_whether_restart_used_the_saved_session(self) -> None:
        entry = _Entry()
        hass = types.SimpleNamespace(data={})

        await session_store.async_load_session_cookies(hass, entry)
        first = dict(hass.data[utils.DOMAIN]["entry-id"][DIAG_COOKIE_STORE])
        await session_store.persist_cookie_to_store(
            hass,
            entry,
            types.SimpleNamespace(
                cookie_jar=_CookieLineJar({"JSESSIONID": "secret-session", "WMONID": "w"})
            ),
        )
        saved = dict(hass.data[utils.DOMAIN]["entry-id"][DIAG_COOKIE_STORE])

        restarted = types.SimpleNamespace(data={})
        await session_store.async_load_session_cookies(restarted, entry)
        reloaded = restarted.data[utils.DOMAIN]["entry-id"][DIAG_COOKIE_STORE]

        self.assertEqual("configured_cookie", first["loaded_from"])
        self.assertEqual(2, saved["saved_cookie_count"])
        self.assertEqual(0, saved["saved_scoped_count"])
        self.assertIn("last_saved", saved)
        self.assertEqual("saved_session", reloaded["loaded_from"])
        self.assertIsNone(reloaded["load_error"])
        self.assertNotIn("secret-session", json.dumps([first, saved, reloaded]))

    async def test_records_a_storage_failure_on_load(self) -> None:
        _MemoryStore.fail_load = True
        hass = types.SimpleNamespace(data={})

        line, _scoped = await session_store.async_load_session_cookies(hass, _Entry())

        record = hass.data[utils.DOMAIN]["entry-id"][DIAG_COOKIE_STORE]
        self.assertEqual("JSESSIONID=secret-session", line)
        self.assertEqual("configured_cookie", record["loaded_from"])
        self.assertEqual("OSError", record["load_error"])

    async def test_jar_seeded_from_a_replaced_cookie_is_not_saved(self) -> None:
        entry = _Entry()
        seeded_from = session_store.cookie_source_hash(entry)
        entry.data[utils.CONF_COOKIE] = "JSESSIONID=new-secret"
        hass = types.SimpleNamespace(data={})

        await session_store.persist_cookie_to_store(
            hass,
            entry,
            types.SimpleNamespace(cookie_jar=_CookieLineJar({"JSESSIONID": "secret-session"})),
            seeded_source_hash=seeded_from,
        )

        self.assertEqual({}, _MemoryStore.values)
        line, _scoped = await session_store.async_load_session_cookies(
            types.SimpleNamespace(data={}), entry
        )
        self.assertEqual("JSESSIONID=new-secret", line)

    async def test_matching_seed_source_is_saved(self) -> None:
        entry = _Entry()
        hass = types.SimpleNamespace(data={})

        await session_store.persist_cookie_to_store(
            hass,
            entry,
            types.SimpleNamespace(cookie_jar=_CookieLineJar({"JSESSIONID": "rotated"})),
            seeded_source_hash=session_store.cookie_source_hash(entry),
        )

        self.assertEqual(1, len(_MemoryStore.values))


class SessionEvidenceTests(unittest.TestCase):
    def test_setup_error_summary_names_source_and_chk_login_result(self) -> None:
        hass = types.SimpleNamespace(
            data={
                utils.DOMAIN: {
                    "entry-id": {
                        "_csrf": "secret-csrf",
                        DIAG_COOKIE_STORE: {"loaded_from": "saved_session"},
                        utils.DIAG_LAST_CHK_LOGIN: {
                            "status": 200,
                            "body": "html",
                            "csrf": False,
                            "redirects": 1,
                            "final_host": "account.samsung.com",
                        },
                    }
                }
            }
        )

        evidence = utils.session_evidence(hass, "entry-id")

        self.assertEqual(
            "cookie source: saved_session, chkLogin.do: HTTP 200 html, "
            "redirected to account.samsung.com",
            evidence,
        )

    def test_summary_without_records(self) -> None:
        self.assertEqual(
            "cookie source: unknown",
            utils.session_evidence(types.SimpleNamespace(data={}), "entry-id"),
        )


if __name__ == "__main__":
    unittest.main()
