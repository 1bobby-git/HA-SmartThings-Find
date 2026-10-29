"""Cookie jar behaviour fixed in 1.4.6, verified against the real aiohttp jar."""

from __future__ import annotations

import importlib.util
import socket
import sys
import types
import unittest
from http.cookies import SimpleCookie

from aiohttp_support import real_aiohttp_available

HAS_AIOHTTP = real_aiohttp_available()

from test_utils_location import COMPONENT_DIR, _load_utils_module  # noqa: E402

utils = _load_utils_module()


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


def _load_session_store():
    storage = types.ModuleType("homeassistant.helpers.storage")
    storage.Store = _MemoryStore
    sys.modules["homeassistant.helpers.storage"] = storage
    spec = importlib.util.spec_from_file_location(
        "custom_components.smartthings_find.session_store_v146_test",
        COMPONENT_DIR / "session_store.py",
    )
    module = importlib.util.module_from_spec(spec)
    module.__package__ = "custom_components.smartthings_find"
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


session_store = _load_session_store()


class _Entry:
    entry_id = "entry-id"

    def __init__(self, cookie: str) -> None:
        self.data = {utils.CONF_COOKIE: cookie}


def _entries(jar) -> set[tuple[str, str, str, str]]:
    return {(m["domain"], m["path"], m.key, m.value) for m in jar}


def _set_cookie(session, header: str) -> None:
    parsed = SimpleCookie()
    parsed.load(header)
    session.cookie_jar.update_cookies(parsed, response_url=utils.STF_BASE)


if HAS_AIOHTTP:
    import aiohttp
    from aiohttp import web
    from aiohttp.abc import AbstractResolver

    class _LocalResolver(AbstractResolver):
        """Resolve smartthingsfind.samsung.com to the local test server."""

        async def resolve(self, host, port=0, family=socket.AF_INET):
            return [
                {
                    "hostname": host,
                    "host": "127.0.0.1",
                    "port": port,
                    "family": socket.AF_INET,
                    "proto": 0,
                    "flags": socket.AI_NUMERICHOST,
                }
            ]

        async def close(self) -> None:
            return None


@unittest.skipUnless(HAS_AIOHTTP, "requires aiohttp")
class CookieWireFormatTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.seen: list[str] = []

        async def handler(request):
            self.seen.append(request.headers.get("Cookie", ""))
            return web.Response(text="ok")

        async def rotate(request):
            self.seen.append(request.headers.get("Cookie", ""))
            response = web.Response(text="ok")
            response.headers.add("Set-Cookie", "AWSALB=srv/rot+x==; Path=/")
            response.headers.add("Set-Cookie", 'R="abc/def"; Path=/')
            return response

        app = web.Application()
        app.router.add_get("/", handler)
        app.router.add_get("/rotate", rotate)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]

    async def asyncTearDown(self) -> None:
        await self.runner.cleanup()

    async def _send_with(self, jar, *paths: str) -> str:
        connector = aiohttp.TCPConnector(resolver=_LocalResolver())
        async with aiohttp.ClientSession(cookie_jar=jar, connector=connector) as client:
            for path in paths or ("/",):
                url = f"http://{utils.STF_HOST}:{self.port}{path}"
                async with client.get(url) as response:
                    await response.text()
        return self.seen[-1]

    async def test_pasted_cookie_values_are_sent_verbatim(self) -> None:
        session = utils.make_session(types.SimpleNamespace(data={}))
        try:
            utils.apply_cookies_to_session(
                session,
                {"AWSALB": "abc/def+ghi==", "JSESSIONID": "plain.value"},
            )
            header = await self._send_with(session.cookie_jar)
        finally:
            await session.close()

        self.assertIn("AWSALB=abc/def+ghi==", header)
        self.assertIn("JSESSIONID=plain.value", header)
        self.assertNotIn('"', header)

    async def test_pasted_quoted_value_keeps_its_quotes(self) -> None:
        session = utils.make_session(types.SimpleNamespace(data={}))
        try:
            utils.apply_cookies_to_session(
                session, utils.parse_cookie_header('Cookie: Q="q/v"; P=abc/def+g==')
            )
            header = await self._send_with(session.cookie_jar)
        finally:
            await session.close()

        self.assertIn('Q="q/v"', header)
        self.assertIn("P=abc/def+g==", header)

    async def test_server_rotated_values_are_sent_as_received(self) -> None:
        """aiohttp 3.12+ quoted AWSALB-style values and dropped server quotes."""
        session = utils.make_session(types.SimpleNamespace(data={}))
        try:
            utils.apply_cookies_to_session(session, {"JSESSIONID": "seed"})
            header = await self._send_with(session.cookie_jar, "/rotate", "/")
        finally:
            await session.close()

        self.assertIn("AWSALB=srv/rot+x==", header)
        self.assertIn('R="abc/def"', header)
        self.assertIn("JSESSIONID=seed", header)

    async def test_received_values_survive_a_restart_unchanged(self) -> None:
        _MemoryStore.values.clear()
        hass = types.SimpleNamespace(data={})
        entry = _Entry("JSESSIONID=seed")
        session = utils.make_session(hass)
        try:
            utils.apply_cookies_to_session(
                session, utils.parse_cookie_header('JSESSIONID=seed; Q="q/v"')
            )
            before = await self._send_with(session.cookie_jar, "/rotate", "/")
            await session_store.persist_cookie_to_store(hass, entry, session)
        finally:
            await session.close()

        restarted = types.SimpleNamespace(data={})
        line, scoped = await session_store.async_load_session_cookies(restarted, entry)
        session = utils.make_session(restarted)
        try:
            utils.apply_cookies_to_session(session, utils.parse_cookie_header(line))
            utils.apply_scoped_cookies(session, scoped)
            after = await self._send_with(session.cookie_jar)
        finally:
            await session.close()

        def tokens(header: str) -> set[str]:
            return {part.strip() for part in header.split(";")}

        self.assertEqual(tokens(before), tokens(after))
        self.assertIn('R="abc/def"', tokens(after))
        self.assertIn('Q="q/v"', tokens(after))


@unittest.skipUnless(HAS_AIOHTTP, "requires aiohttp")
class CookieShadowTests(unittest.IsolatedAsyncioTestCase):
    async def test_server_parent_domain_cookie_replaces_pasted_seed(self) -> None:
        session = utils.make_session(types.SimpleNamespace(data={}))
        try:
            utils.apply_cookies_to_session(session, {"X": "seed", "Y": "keep"})
            _set_cookie(session, "X=fresh; Domain=.samsung.com; Path=/")

            removed = utils.normalize_cookie_jar(session)

            sent = session.cookie_jar.filter_cookies(utils.STF_BASE)
            self.assertEqual(1, removed)
            self.assertEqual("fresh", sent["X"].value)
            self.assertEqual("keep", sent["Y"].value)
            self.assertEqual(
                {
                    ("samsung.com", "/", "X", "fresh"),
                    (utils.STF_HOST, "/", "Y", "keep"),
                },
                _entries(session.cookie_jar),
            )
        finally:
            await session.close()

    async def test_normalize_keeps_jar_without_conflicts(self) -> None:
        session = utils.make_session(types.SimpleNamespace(data={}))
        try:
            utils.apply_cookies_to_session(session, {"X": "seed"})
            _set_cookie(session, "Z=other; Domain=.samsung.com; Path=/")
            _set_cookie(session, "X=path-only; Path=/device")

            self.assertEqual(0, utils.normalize_cookie_jar(session))
            self.assertEqual(3, len(_entries(session.cookie_jar)))
        finally:
            await session.close()

    def test_normalize_ignores_jars_without_iteration(self) -> None:
        jar = types.SimpleNamespace(filter_cookies=lambda _url: {})
        self.assertEqual(0, utils.normalize_cookie_jar(types.SimpleNamespace(cookie_jar=jar)))

    async def test_seeded_normalize_keeps_a_host_only_cookie_the_server_set(self) -> None:
        session = utils.make_session(types.SimpleNamespace(data={}))
        try:
            seeds = {"X": "seed"}
            utils.apply_cookies_to_session(session, seeds)
            _set_cookie(session, "X=parent-old; Domain=.samsung.com; Path=/")
            _set_cookie(session, "X=host-new; Path=/")

            self.assertEqual(0, utils.normalize_cookie_jar(session, seeds))
            self.assertIn((utils.STF_HOST, "/", "X", "host-new"), _entries(session.cookie_jar))
        finally:
            await session.close()

    async def test_seeded_normalize_still_drops_the_untouched_seed(self) -> None:
        session = utils.make_session(types.SimpleNamespace(data={}))
        try:
            seeds = {"X": "seed"}
            utils.apply_cookies_to_session(session, seeds)
            _set_cookie(session, "X=fresh; Domain=.samsung.com; Path=/")

            self.assertEqual(1, utils.normalize_cookie_jar(session, seeds))
            self.assertEqual(
                "fresh", session.cookie_jar.filter_cookies(utils.STF_BASE)["X"].value
            )
        finally:
            await session.close()

    async def test_chk_login_response_reconciles_the_jar_before_the_next_request(
        self,
    ) -> None:
        session = utils.make_session(types.SimpleNamespace(data={}))
        try:
            utils.apply_cookies_to_session(session, {"X": "seed"})
            hass = types.SimpleNamespace(
                data={utils.DOMAIN: {"entry-id": {utils.COOKIE_SEEDS_KEY: {"X": "seed"}}}}
            )

            class _Response:
                status = 200
                headers = {"_csrf": "token"}
                history = ()
                url = types.SimpleNamespace(host=utils.STF_HOST)

                async def __aenter__(self):
                    return self

                async def __aexit__(self, *_exc):
                    return False

                async def text(self) -> str:
                    return "success"

            class _Client:
                cookie_jar = session.cookie_jar

                def get(self, _url, **_kwargs):
                    # aiohttp stores Set-Cookie before the caller reads the body.
                    _set_cookie(session, "X=fresh; Domain=.samsung.com; Path=/")
                    return _Response()

            await utils.fetch_csrf(hass, _Client(), "entry-id", retry_delays=())

            self.assertEqual(
                "fresh", session.cookie_jar.filter_cookies(utils.STF_BASE)["X"].value
            )
        finally:
            await session.close()

    def test_bare_top_level_domain_is_not_a_smartthings_find_scope(self) -> None:
        self.assertTrue(utils.is_stf_cookie_domain(utils.STF_HOST))
        self.assertTrue(utils.is_stf_cookie_domain("samsung.com"))
        self.assertFalse(utils.is_stf_cookie_domain("com"))
        self.assertFalse(utils.is_stf_cookie_domain("account.samsung.com"))


@unittest.skipUnless(HAS_AIOHTTP, "requires aiohttp")
class ScopedCookiePersistenceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        _MemoryStore.values.clear()

    async def test_parent_domain_and_path_cookies_survive_restart(self) -> None:
        hass = types.SimpleNamespace(data={})
        entry = _Entry("JSESSIONID=seed")
        session = utils.make_session(hass)
        try:
            utils.apply_cookies_to_session(session, {"JSESSIONID": "seed"})
            _set_cookie(session, "SSO=domain-value; Domain=.samsung.com; Path=/")
            _set_cookie(session, "DEV=path-value; Path=/device")
            await session_store.persist_cookie_to_store(hass, entry, session)
            before = _entries(session.cookie_jar)
        finally:
            await session.close()

        stored = next(iter(_MemoryStore.values.values()))
        self.assertEqual(
            {"JSESSIONID": "seed", "SSO": "domain-value"},
            utils.parse_cookie_header(stored["cookie"]),
        )
        self.assertEqual(
            [
                {"name": "SSO", "value": "domain-value", "domain": "samsung.com", "path": "/"},
                {"name": "DEV", "value": "path-value", "domain": utils.STF_HOST, "path": "/device"},
            ],
            stored["scoped"],
        )

        restarted = types.SimpleNamespace(data={})
        line, scoped = await session_store.async_load_session_cookies(restarted, entry)
        session = utils.make_session(restarted)
        try:
            utils.apply_cookies_to_session(session, utils.parse_cookie_header(line))
            utils.apply_scoped_cookies(session, scoped)
            utils.normalize_cookie_jar(session)
            self.assertEqual(before, _entries(session.cookie_jar))

            _set_cookie(session, "SSO=rotated; Domain=.samsung.com; Path=/")
            utils.normalize_cookie_jar(session)
            sent = session.cookie_jar.filter_cookies(utils.STF_BASE)
            self.assertEqual("rotated", sent["SSO"].value)
            self.assertEqual("seed", sent["JSESSIONID"].value)
        finally:
            await session.close()

    async def test_snapshot_without_scoped_cookies_matches_previous_format(self) -> None:
        hass = types.SimpleNamespace(data={})
        entry = _Entry("JSESSIONID=seed")
        session = utils.make_session(hass)
        try:
            utils.apply_cookies_to_session(session, {"JSESSIONID": "seed", "WMONID": "w"})
            await session_store.persist_cookie_to_store(hass, entry, session)
        finally:
            await session.close()

        stored = next(iter(_MemoryStore.values.values()))
        self.assertEqual({"source_hash", "cookie"}, set(stored))
        self.assertEqual("JSESSIONID=seed; WMONID=w", stored["cookie"])

    async def test_v145_snapshot_loads_without_scoped_cookies(self) -> None:
        entry = _Entry("JSESSIONID=seed")
        key = f"{utils.DOMAIN}.{entry.entry_id}.session"
        source = f"{session_store.AUTH_METHOD_COOKIE}:JSESSIONID=seed"
        from hashlib import sha256

        _MemoryStore.values[key] = {
            "source_hash": sha256(source.encode("utf-8")).hexdigest(),
            "cookie": "JSESSIONID=rotated",
        }
        line, scoped = await session_store.async_load_session_cookies(
            types.SimpleNamespace(data={}), entry
        )
        self.assertEqual("JSESSIONID=rotated", line)
        self.assertEqual([], scoped)

    async def test_invalid_scoped_records_are_ignored(self) -> None:
        entry = _Entry("JSESSIONID=seed")
        key = f"{utils.DOMAIN}.{entry.entry_id}.session"
        source = f"{session_store.AUTH_METHOD_COOKIE}:JSESSIONID=seed"
        from hashlib import sha256

        valid = {"name": "SSO", "value": "v", "domain": "samsung.com", "path": "/"}
        _MemoryStore.values[key] = {
            "source_hash": sha256(source.encode("utf-8")).hexdigest(),
            "cookie": "JSESSIONID=rotated",
            "scoped": [
                valid,
                {"name": "OTHER", "value": "v", "domain": "example.com", "path": "/"},
                {"name": "NOPATH", "value": "v", "domain": "samsung.com", "path": "relative"},
                {"name": "BAD", "value": "a;b", "domain": "samsung.com", "path": "/"},
                {"name": "bad name", "value": "v", "domain": "samsung.com", "path": "/"},
                "not-a-record",
            ]
            + [
                {"name": f"N{i}", "value": "v", "domain": "samsung.com", "path": "/"}
                for i in range(60)
            ],
        }
        _line, scoped = await session_store.async_load_session_cookies(
            types.SimpleNamespace(data={}), entry
        )
        self.assertEqual(valid, scoped[0])
        self.assertEqual(session_store.MAX_SCOPED_COOKIES, len(scoped))
        self.assertTrue(all(record["domain"] == "samsung.com" for record in scoped))


if __name__ == "__main__":
    unittest.main()
