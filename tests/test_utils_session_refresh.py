from __future__ import annotations

from datetime import datetime, timedelta, timezone
import types
import unittest

from test_utils_location import _load_utils_module


utils = _load_utils_module()


class _FakeResponse:
    def __init__(
        self,
        body: str,
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._body = body
        self.status = status
        self.headers = headers or {}

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

    def get(self, url, **_kwargs):
        self.get_calls.append(str(url))
        return self.responses.pop(0)

    def post(self, url, **_kwargs):
        return self.responses.pop(0)


class UtilsSessionRefreshTest(unittest.IsolatedAsyncioTestCase):
    async def test_fetch_csrf_retries_a_transient_logout_without_reauth(self) -> None:
        session = _FakeSession(
            [
                _FakeResponse("Logout"),
                _FakeResponse("success", headers={"_csrf": "recovered-csrf"}),
            ]
        )
        hass = types.SimpleNamespace(data={})

        csrf = await utils.fetch_csrf(
            hass,
            session,
            "entry-id",
            retry_delays=(0,),
        )

        self.assertEqual(csrf, "recovered-csrf")
        self.assertEqual(hass.data[utils.DOMAIN]["entry-id"]["_csrf"], "recovered-csrf")
        self.assertEqual(session.get_calls, [str(utils.URL_CHK_LOGIN)] * 2)

    async def test_retry_auth_operation_replays_only_auth_failures(self) -> None:
        calls = 0

        async def operation() -> str:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise utils.ConfigEntryAuthFailed("temporary Logout")
            return "recovered"

        result = await utils.retry_auth_operation(operation, retry_delays=(0,))

        self.assertEqual(result, "recovered")
        self.assertEqual(calls, 2)

    async def test_send_operation_retries_http_200_logout_body(self) -> None:
        session = _FakeSession(
            [
                _FakeResponse("Logout"),
                _FakeResponse("success"),
            ]
        )
        hass = types.SimpleNamespace(
            data={utils.DOMAIN: {"entry-id": {"_csrf": "csrf-token"}}}
        )

        await utils.retry_auth_operation(
            lambda: utils.send_operation(
                hass,
                session,
                "entry-id",
                {"operation": "CHECK_CONNECTION_WITH_LOCATION"},
            ),
            retry_delays=(0,),
        )

        self.assertEqual(session.responses, [])

    def test_auth_failure_requires_continuous_grace_period_before_reauth(self) -> None:
        hass = types.SimpleNamespace(data={})
        started = datetime(2026, 8, 11, 10, 0, tzinfo=timezone.utc)

        self.assertFalse(
            utils.auth_failure_is_persistent(hass, "entry-id", now=started)
        )
        self.assertFalse(
            utils.auth_failure_is_persistent(
                hass,
                "entry-id",
                now=started + timedelta(minutes=29),
            )
        )
        self.assertTrue(
            utils.auth_failure_is_persistent(
                hass,
                "entry-id",
                now=started + timedelta(minutes=30),
            )
        )

        utils.clear_auth_failure(hass, "entry-id")
        self.assertFalse(
            utils.auth_failure_is_persistent(
                hass,
                "entry-id",
                now=started + timedelta(hours=1),
            )
        )


if __name__ == "__main__":
    unittest.main()
