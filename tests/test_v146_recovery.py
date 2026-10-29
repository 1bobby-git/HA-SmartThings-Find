"""Keepalive, polling recovery and log hygiene fixed in 1.4.6."""

from __future__ import annotations

import importlib.util
import json
import logging
import sys
import types
import unittest

from test_utils_location import COMPONENT_DIR, _load_utils_module

utils = _load_utils_module()
ConfigEntryAuthFailed = utils.ConfigEntryAuthFailed
HomeAssistantError = utils.HomeAssistantError


class _Recorder(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    def text(self, level: int = logging.DEBUG) -> str:
        return "\n".join(
            record.getMessage() for record in self.records if record.levelno >= level
        )


class _LogCapture:
    """Capture every SmartThings Find log line, including debug."""

    def __enter__(self) -> _Recorder:
        self.logger = logging.getLogger("custom_components.smartthings_find")
        self.previous = self.logger.level
        self.recorder = _Recorder()
        self.logger.addHandler(self.recorder)
        self.logger.setLevel(logging.DEBUG)
        return self.recorder

    def __exit__(self, *exc) -> None:
        self.logger.removeHandler(self.recorder)
        self.logger.setLevel(self.previous)


class _FakeResponse:
    def __init__(
        self,
        body: str,
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
        host: str = "smartthingsfind.samsung.com",
        history: tuple = (),
    ) -> None:
        self._body = body
        self.status = status
        self.headers = headers or {}
        self.url = types.SimpleNamespace(host=host)
        self.history = history

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def text(self, **_kwargs) -> str:
        return self._body


class _FakeSession:
    def __init__(self, responses: list[_FakeResponse]) -> None:
        self.responses = list(responses)
        self.get_calls: list[str] = []
        self.post_calls: list[str] = []

    def get(self, url, **_kwargs):
        self.get_calls.append(str(url))
        return self.responses.pop(0)

    def post(self, url, **_kwargs):
        self.post_calls.append(str(url))
        return self.responses.pop(0)


def _login_ok(csrf: str = "fresh-csrf") -> _FakeResponse:
    return _FakeResponse("success", headers={"_csrf": csrf})


def _hass_with_cached_csrf() -> types.SimpleNamespace:
    return types.SimpleNamespace(data={utils.DOMAIN: {"entry-id": {"_csrf": "cached"}}})


class KeepaliveTests(unittest.IsolatedAsyncioTestCase):
    async def test_keepalive_confirms_login_even_with_a_cached_csrf(self) -> None:
        session = _FakeSession([_login_ok(), _FakeResponse(json.dumps({"deviceList": []}))])
        hass = _hass_with_cached_csrf()

        await utils.keepalive_ping(hass, session, "entry-id")

        self.assertEqual([str(utils.URL_CHK_LOGIN)], session.get_calls)
        self.assertEqual(1, len(session.post_calls))
        self.assertEqual("fresh-csrf", hass.data[utils.DOMAIN]["entry-id"]["_csrf"])

    async def test_html_device_list_is_not_counted_as_success(self) -> None:
        session = _FakeSession(
            [_login_ok(), _FakeResponse("<html><body>private sign-in page</body></html>")]
        )
        with _LogCapture() as logs:
            with self.assertRaises(HomeAssistantError) as caught:
                await utils.keepalive_ping(_hass_with_cached_csrf(), session, "entry-id")
        self.assertNotIsInstance(caught.exception, ConfigEntryAuthFailed)
        self.assertNotIn("private", str(caught.exception))
        self.assertNotIn("private", logs.text())

    async def test_logout_body_is_an_authentication_failure(self) -> None:
        session = _FakeSession([_login_ok(), _FakeResponse("Logout")])
        with self.assertRaises(ConfigEntryAuthFailed):
            await utils.keepalive_ping(_hass_with_cached_csrf(), session, "entry-id")

    async def test_server_error_is_not_counted_as_success(self) -> None:
        session = _FakeSession([_login_ok(), _FakeResponse("busy", status=503)])
        with self.assertRaises(HomeAssistantError) as caught:
            await utils.keepalive_ping(_hass_with_cached_csrf(), session, "entry-id")
        self.assertNotIsInstance(caught.exception, ConfigEntryAuthFailed)

    async def test_chk_login_outcome_is_recorded_without_secrets(self) -> None:
        hass = types.SimpleNamespace(data={})
        redirected = _FakeResponse(
            "<html>private</html>",
            host="account.samsung.com",
            history=(object(),),
        )
        session = _FakeSession([_login_ok("secret-csrf"), redirected])

        await utils.fetch_csrf(hass, session, "entry-id", retry_delays=())
        first = dict(hass.data[utils.DOMAIN]["entry-id"][utils.DIAG_LAST_CHK_LOGIN])
        with self.assertRaises(ConfigEntryAuthFailed):
            await utils.fetch_csrf(hass, session, "entry-id", retry_delays=())
        second = hass.data[utils.DOMAIN]["entry-id"][utils.DIAG_LAST_CHK_LOGIN]

        self.assertEqual(200, first["status"])
        self.assertEqual("text", first["body"])
        self.assertTrue(first["csrf"])
        self.assertEqual(0, first["redirects"])
        self.assertEqual("smartthingsfind.samsung.com", first["final_host"])
        self.assertIn("at", first)
        self.assertEqual("html", second["body"])
        self.assertFalse(second["csrf"])
        self.assertEqual(1, second["redirects"])
        self.assertEqual("account.samsung.com", second["final_host"])
        self.assertNotIn("secret-csrf", json.dumps([first, second]))
        self.assertNotIn("private", json.dumps([first, second]))


class LogHygieneTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_operation_log_omits_identifiers_and_body(self) -> None:
        session = _FakeSession([_FakeResponse("secret-body", status=403)])
        hass = _hass_with_cached_csrf()
        payload = {"dvceId": "device-9", "usrId": "user-123", "operation": "RING"}
        with _LogCapture() as logs:
            with self.assertRaises(ConfigEntryAuthFailed) as caught:
                await utils.send_operation(hass, session, "entry-id", payload)
        for secret in ("device-9", "user-123", "secret-body"):
            self.assertNotIn(secret, logs.text())
            self.assertNotIn(secret, str(caught.exception))
        self.assertIn("RING", logs.text())

    async def test_failed_location_read_log_omits_response_body(self) -> None:
        session = _FakeSession([_FakeResponse("<html>private-page</html>", status=500)])
        hass = _hass_with_cached_csrf()
        with _LogCapture() as logs:
            result = await utils.get_device_location(
                hass, session, {"dvceID": "d1", "modelName": "Tag"}, "entry-id"
            )
        self.assertIsNone(result)
        self.assertNotIn("private-page", logs.text())

    async def test_malformed_coordinates_stay_out_of_error_logs(self) -> None:
        payload = {
            "operation": [
                {
                    "oprnType": "LOCATION",
                    "latitude": "37.5665xyz",
                    "longitude": "126.978",
                    "extra": {"gpsUtcDt": "20260804101010"},
                }
            ]
        }
        session = _FakeSession([_FakeResponse(json.dumps(payload))])
        hass = _hass_with_cached_csrf()
        with _LogCapture() as logs:
            result = await utils.get_device_location(
                hass, session, {"dvceID": "d1", "modelName": "Tag"}, "entry-id"
            )
        self.assertIsNone(result)
        self.assertNotIn("37.5665xyz", logs.text(logging.INFO))

    def test_unused_legacy_helpers_are_removed(self) -> None:
        for name in (
            "persist_cookie_to_entry",
            "get_devices",
            "ring_device",
            "list_smartthings_devices_for_ui",
        ):
            self.assertFalse(hasattr(utils, name), name)


def _load_coordinator_module():
    config_entries = sys.modules.setdefault(
        "homeassistant.config_entries", types.ModuleType("homeassistant.config_entries")
    )
    if not hasattr(config_entries, "ConfigEntry"):
        config_entries.ConfigEntry = object

    update_coordinator = types.ModuleType("homeassistant.helpers.update_coordinator")

    class UpdateFailed(Exception):
        pass

    class DataUpdateCoordinator:
        def __class_getitem__(cls, _item):
            return cls

        def __init__(self, hass=None, logger=None, name=None, config_entry=None,
                     update_interval=None, **_kwargs) -> None:
            self.hass = hass
            self.logger = logger
            self.name = name
            self.config_entry = config_entry
            self.update_interval = update_interval
            self.data = None
            self.last_update_success = True
            self.last_exception = None
            self.refresh_calls = 0

        async def async_refresh(self) -> None:
            self.refresh_calls += 1

        async def async_shutdown(self) -> None:
            return None

    update_coordinator.DataUpdateCoordinator = DataUpdateCoordinator
    update_coordinator.UpdateFailed = UpdateFailed
    sys.modules[update_coordinator.__name__] = update_coordinator

    auth_manager_name = "custom_components.smartthings_find.auth_manager"
    if auth_manager_name not in sys.modules:
        stub = types.ModuleType(auth_manager_name)
        stub.SmartThingsFindAuthManager = object
        sys.modules[auth_manager_name] = stub

    spec = importlib.util.spec_from_file_location(
        "custom_components.smartthings_find.coordinator_v146_test",
        COMPONENT_DIR / "coordinator.py",
    )
    module = importlib.util.module_from_spec(spec)
    module.__package__ = "custom_components.smartthings_find"
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    module._UpdateFailed = UpdateFailed
    return module


coordinator_module = _load_coordinator_module()


class _Entry:
    entry_id = "entry-id"

    def __init__(self) -> None:
        self.reauth_calls = 0

    def async_start_reauth(self, _hass) -> None:
        self.reauth_calls += 1


class _AuthManager:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls = 0

    async def async_keepalive(self) -> None:
        self.calls += 1
        if self.error is not None:
            raise self.error


def _coordinator(auth_manager: _AuthManager):
    hass = types.SimpleNamespace(data={})
    entry = _Entry()
    coordinator = coordinator_module.SmartThingsFindCoordinator(
        hass=hass,
        entry=entry,
        session=None,
        auth_manager=auth_manager,
        devices=[],
        update_interval_s=120,
    )
    return coordinator, hass, entry


class PollingRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_keepalive_success_resumes_polling_stopped_by_auth_failure(self) -> None:
        coordinator, hass, _entry = _coordinator(_AuthManager())
        coordinator.last_update_success = False
        coordinator.last_exception = coordinator_module.ConfigEntryAuthFailed("expired")

        await coordinator._async_keepalive()

        self.assertEqual(1, coordinator.refresh_calls)
        keepalive = hass.data[utils.DOMAIN]["entry-id"][utils.DIAG_KEEPALIVE]
        self.assertIn("last_success", keepalive)

    async def test_keepalive_success_leaves_healthy_polling_alone(self) -> None:
        coordinator, _hass, _entry = _coordinator(_AuthManager())

        await coordinator._async_keepalive()

        self.assertEqual(0, coordinator.refresh_calls)

    async def test_keepalive_success_ignores_non_auth_update_errors(self) -> None:
        coordinator, _hass, _entry = _coordinator(_AuthManager())
        coordinator.last_update_success = False
        coordinator.last_exception = coordinator_module._UpdateFailed("outage")

        await coordinator._async_keepalive()

        self.assertEqual(0, coordinator.refresh_calls)

    async def test_keepalive_failure_is_recorded_without_refresh(self) -> None:
        coordinator, hass, _entry = _coordinator(_AuthManager(RuntimeError("boom")))
        coordinator.last_update_success = False
        coordinator.last_exception = coordinator_module.ConfigEntryAuthFailed("expired")

        await coordinator._async_keepalive()

        self.assertEqual(0, coordinator.refresh_calls)
        keepalive = hass.data[utils.DOMAIN]["entry-id"][utils.DIAG_KEEPALIVE]
        self.assertEqual("RuntimeError", keepalive["last_error"])
        self.assertIn("last_failure", keepalive)


if __name__ == "__main__":
    unittest.main()
