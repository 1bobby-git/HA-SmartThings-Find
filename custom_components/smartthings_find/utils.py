from __future__ import annotations

import asyncio
import functools
import json
import logging
import math
import re
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime, timezone
from http.cookies import CookieError, Morsel, SimpleCookie
from typing import Any, TypeVar

import aiohttp
from yarl import URL

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError

from .const import (
    DOMAIN,
    BATTERY_LEVELS,
    CONF_ACTIVE_MODE_SMARTTAGS,
    CONF_ACTIVE_MODE_OTHERS,
    CONF_COOKIE,  # noqa: F401  re-exported; callers read utils.CONF_COOKIE
    DIAG_COOKIE_STORE,
    DIAG_KEEPALIVE,
    DIAG_LAST_CHK_LOGIN,
    OP_CHECK_CONNECTION,
    OP_CHECK_CONNECTION_WITH_LOCATION,
    STF_BASE_URL,
    STF_CHK_LOGIN_PATH,
    STF_DEVICE_LIST_PATH,
    STF_SET_LAST_DEVICE_PATH,
    STF_ADD_OPERATION_PATH,
    AUTH_FAILURE_GRACE_PERIOD,
    AUTH_RETRY_DELAYS,
)

_LOGGER = logging.getLogger(__name__)

_T = TypeVar("_T")

STF_BASE = URL(STF_BASE_URL)
URL_CHK_LOGIN = STF_BASE / STF_CHK_LOGIN_PATH
URL_DEVICE_LIST = STF_BASE / STF_DEVICE_LIST_PATH
URL_SET_LAST_DEVICE = STF_BASE / STF_SET_LAST_DEVICE_PATH
URL_ADD_OPERATION = STF_BASE / STF_ADD_OPERATION_PATH  # requires ?_csrf=


def _utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _entry_runtime(hass: HomeAssistant, entry_id: str) -> dict[str, Any]:
    return hass.data.setdefault(DOMAIN, {}).setdefault(entry_id, {})


def _body_kind(text: str) -> str:
    """Classify a response body without keeping any of its content."""
    if text in ("Logout", "fail"):
        return text
    if not text:
        return "empty"
    if text.lstrip().startswith("<"):
        return "html"
    if text[:1] in ("{", "["):
        return "json"
    return "text"


def record_keepalive(
    hass: HomeAssistant,
    entry_id: str,
    *,
    error: BaseException | None = None,
) -> None:
    """Remember the latest KeepAlive outcome for diagnostics."""
    state = _entry_runtime(hass, entry_id).setdefault(DIAG_KEEPALIVE, {})
    if error is None:
        state["last_success"] = _utc_now_iso()
    else:
        state["last_failure"] = _utc_now_iso()
        state["last_error"] = type(error).__name__


def session_evidence(hass: HomeAssistant, entry_id: str) -> str:
    """Summarize where the cookie came from and what chkLogin.do answered.

    Setup errors carry this line into the Home Assistant log, because the
    diagnostics download is offered only while the entry is loaded. Only labels,
    HTTP status codes and host names are included.
    """
    runtime = hass.data.get(DOMAIN, {}).get(entry_id) or {}
    store = runtime.get(DIAG_COOKIE_STORE) or {}
    parts = [f"cookie source: {store.get('loaded_from') or 'unknown'}"]
    chk = runtime.get(DIAG_LAST_CHK_LOGIN) or {}
    if chk:
        parts.append(f"chkLogin.do: HTTP {chk.get('status')} {chk.get('body')}")
        if chk.get("redirects"):
            parts.append(f"redirected to {chk.get('final_host')}")
    return ", ".join(parts)


COOKIE_NAME_RE = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")

STF_HOST = str(getattr(STF_BASE, "host", None) or "smartthingsfind.samsung.com")

# Runtime key for the cookie values this integration seeded into the jar (the
# pasted or restored line). It holds secrets and is never read by diagnostics.
COOKIE_SEEDS_KEY = "_cookie_seeds"


def _raw_morsel(name: str, value: str) -> Morsel | str:
    """Return a Morsel that sends ``value`` exactly as the browser did.

    A plain ``{name: value}`` seed makes aiohttp wrap values containing ``/``,
    ``+``, ``=`` and similar characters in double quotes, which the browser never
    does. A Morsel whose coded value is the raw value, sent through the
    verbatim jar from ``make_session``, keeps the value as pasted, quotes
    included, on every aiohttp release Home Assistant has shipped.
    """
    morsel: Morsel = Morsel()
    try:
        morsel.set(name, value, value)
    except CookieError:
        return value
    return morsel


def cookie_scope(morsel: Any) -> tuple[str, str]:
    """Return the (domain, path) a jar morsel applies to."""
    domain = str(morsel.get("domain") or "").lstrip(".").lower() or STF_HOST
    path = str(morsel.get("path") or "/") or "/"
    return domain, path


def is_stf_cookie_domain(domain: str) -> bool:
    """Return true for SmartThings Find's host and samsung.com above it.

    A bare top-level domain such as ``com`` is never a legitimate cookie scope.
    """
    if domain == STF_HOST:
        return True
    return "." in domain and STF_HOST.endswith("." + domain)


def _jar_morsels(session: Any) -> list[Any] | None:
    jar = getattr(session, "cookie_jar", None)
    if jar is None:
        return None
    try:
        return list(jar)
    except TypeError:
        return None


def normalize_cookie_jar(
    session: Any,
    seeds: Mapping[str, str] | None = None,
) -> int:
    """Drop pasted host-only cookies shadowed by a server-set parent-domain cookie.

    A pasted Cookie header carries no Domain attribute, so every seed becomes a
    host-only cookie for smartthingsfind.samsung.com. When Samsung later rotates
    the same cookie with ``Domain=.samsung.com``, aiohttp keeps both and keeps
    sending the stale host-only value. The parent-domain copy can only come from
    a server response, so it is newer than the seed.

    ``seeds`` holds the values this integration seeded. When given, a host-only
    cookie is removed only while it still has its seeded value, so a host-only
    cookie the server set later is never replaced by an older parent copy.
    Without ``seeds`` every shadowed host-only copy is removed. Returns the
    number removed.
    """
    morsels = _jar_morsels(session)
    if not morsels:
        return 0

    parent_root: set[str] = set()
    host_root: dict[str, str] = {}
    for morsel in morsels:
        domain, path = cookie_scope(morsel)
        if path != "/" or not is_stf_cookie_domain(domain):
            continue
        if domain == STF_HOST:
            host_root[morsel.key] = morsel.value
        else:
            parent_root.add(morsel.key)

    shadowed = {
        name
        for name, value in host_root.items()
        if name in parent_root and (seeds is None or seeds.get(name) == value)
    }
    clear = getattr(session.cookie_jar, "clear", None)
    if not shadowed or not callable(clear):
        return 0

    clear(
        lambda morsel: morsel.key in shadowed
        and cookie_scope(morsel) == (STF_HOST, "/")
    )
    _LOGGER.debug(
        "Dropped %s pasted cookie(s) replaced by Samsung's parent-domain cookie",
        len(shadowed),
    )
    return len(shadowed)


def _normalize_seeded_jar(hass: HomeAssistant, session: Any, entry_id: str) -> None:
    """Reconcile the jar right after a response, before the next request."""
    seeds = hass.data.get(DOMAIN, {}).get(entry_id, {}).get(COOKIE_SEEDS_KEY)
    if seeds is None:
        return
    try:
        normalize_cookie_jar(session, seeds)
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug(
            "SmartThings Find cookie jar normalization failed (%s)",
            type(err).__name__,
        )


def _cookies_from_json(text: str) -> dict[str, str]:
    """Parse browser/extension JSON cookie exports (array or object).

    Supports the common EditThisCookie / DevTools shapes:
      [{"name": "a", "value": "b"}, ...]
      {"a": "b", ...}
    Returns an empty dict when the text is not such a JSON export.
    """
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        return {}

    jar: dict[str, str] = {}
    if isinstance(parsed, list):
        for item in parsed:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            value = item.get("value")
            if isinstance(name, str) and COOKIE_NAME_RE.match(name):
                jar[name] = "" if value is None else str(value)
    elif isinstance(parsed, dict):
        for name, value in parsed.items():
            if isinstance(name, str) and COOKIE_NAME_RE.match(name):
                jar[name] = "" if value is None else str(value)
    return jar


def parse_cookie_header(cookie_header_line: str) -> dict[str, str]:
    """
    Accepts a variety of browser copy formats and normalizes them:
      - "Cookie: a=b; c=d"          (Chrome/Edge/Firefox "Copy value")
      - "a=b; c=d"                  (raw cookie string)
      - a header wrapped in single/double quotes
      - a header copied across several wrapped lines (newlines/tabs)
      - a JSON cookie export (array of {name,value} objects, or an object)
    Duplicate cookie names keep the last occurrence. Returns a dict of cookies
    safe for aiohttp CookieJar. Cookie values are never logged.
    """
    s = (cookie_header_line or "").strip()
    if not s:
        return {}

    # Some browsers/extensions surround "Copy value" output with quotes.
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        s = s[1:-1].strip()

    # A JSON export is unambiguous once the outer quotes are gone.
    if s[:1] in ("[", "{"):
        json_jar = _cookies_from_json(s)
        if json_jar:
            return json_jar

    # Strip a leading "Cookie:" (or "cookie:") request-header label.
    if s.lower().startswith("cookie:"):
        s = s.split(":", 1)[1].strip()

    # Browsers may wrap the header across multiple lines/tabs on copy. The
    # header syntax separates cookies with ";", so any embedded newline or tab
    # is safe to flatten to a single space before splitting.
    s = re.sub(r"[\r\n\t]+", " ", s)

    jar: dict[str, str] = {}
    try:
        sc = SimpleCookie()
        sc.load(s)
        for k, morsel in sc.items():
            if COOKIE_NAME_RE.match(k):
                # The coded value is the token as pasted, quotes included, which
                # is what the browser sends back.
                jar[k] = morsel.coded_value
        if jar:
            return jar
    except CookieError:
        pass

    for part in s.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        k, v = part.split("=", 1)
        k = k.strip()
        v = v.strip()
        if not k or " " in k:
            continue
        if not COOKIE_NAME_RE.match(k):
            continue
        jar[k] = v

    return jar


def apply_cookies_to_session(session: aiohttp.ClientSession, cookies: dict[str, str]) -> None:
    """Seed pasted or restored cookies as host-only cookies, values verbatim.

    aiohttp requires response_url to be yarl.URL, not str.
    """
    if not cookies:
        return
    session.cookie_jar.update_cookies(
        {name: _raw_morsel(name, value) for name, value in cookies.items()},
        response_url=STF_BASE,
    )


def apply_scoped_cookies(
    session: aiohttp.ClientSession,
    records: list[dict[str, str]],
) -> None:
    """Restore cookies that carry a parent Domain or a non-root Path."""
    for record in records or ():
        name = record["name"]
        morsel = _raw_morsel(name, record["value"])
        if not isinstance(morsel, Morsel):
            continue
        if record["domain"] != STF_HOST:
            morsel["domain"] = record["domain"]
        morsel["path"] = record["path"]
        # One record at a time: several records may share a name.
        session.cookie_jar.update_cookies({name: morsel}, response_url=STF_BASE)


@functools.lru_cache(maxsize=1)
def _verbatim_cookie_jar_class() -> type:
    """Build the jar class on first use; tests may run with an aiohttp stub."""

    class VerbatimCookieJar(aiohttp.CookieJar):
        """Send each cookie value exactly as it was pasted or received.

        ``quote_cookie=False`` stops aiohttp from quoting values that contain
        ``/``, ``+`` or ``=``. From aiohttp 3.12 it also drops the quotes of a
        value the server sent quoted, so the received coded value is restored.
        """

        def filter_cookies(self, request_url, *args, **kwargs):
            filtered = super().filter_cookies(request_url, *args, **kwargs)
            received: dict[tuple[str, str], str] = {}
            for morsel in self:
                received.setdefault((morsel.key, morsel.value), morsel.coded_value)
            for name, morsel in list(filtered.items()):
                coded = received.get((morsel.key, morsel.value))
                if coded is None or coded == morsel.coded_value:
                    continue
                restored: Morsel = Morsel()
                try:
                    restored.set(morsel.key, morsel.value, coded)
                except CookieError:
                    continue
                filtered[name] = restored
            return filtered

    return VerbatimCookieJar


def make_session(hass: HomeAssistant) -> aiohttp.ClientSession:
    """Dedicated session (not Home Assistant managed session)."""
    _ = hass  # keep signature for compatibility (unused)

    jar = _verbatim_cookie_jar_class()(unsafe=True, quote_cookie=False)

    # 너무 공격적이지 않게 기본 타임아웃만 설정
    timeout = aiohttp.ClientTimeout(total=30)

    # STF가 UA에 민감할 가능성 대비(필수는 아니지만 안전)
    headers = {
        "User-Agent": "HomeAssistant-SmartThingsFind/1.2.0",
        "Accept": "*/*",
    }

    return aiohttp.ClientSession(
        cookie_jar=jar,
        timeout=timeout,
        raise_for_status=False,
        headers=headers,
    )


async def _fetch_csrf_once(
    hass: HomeAssistant,
    session: aiohttp.ClientSession,
    entry_id: str | None = None,
) -> str:
    """Call chkLogin.do once without attempting session renewal."""
    hass.data.setdefault(DOMAIN, {})
    if entry_id is not None:
        hass.data[DOMAIN].setdefault(entry_id, {})

    async with session.get(URL_CHK_LOGIN) as resp:
        text = (await resp.text()).strip()
        csrf = resp.headers.get("_csrf")
        status = resp.status
        history = getattr(resp, "history", None) or ()
        final_host = getattr(getattr(resp, "url", None), "host", None)

        _LOGGER.debug("chkLogin.do status=%s csrf=%s", status, bool(csrf))
        if entry_id is not None:
            # chkLogin.do may rotate a seeded cookie with a parent Domain; the
            # request that follows must already send the rotated value.
            _normalize_seeded_jar(hass, session, entry_id)
            hass.data[DOMAIN][entry_id][DIAG_LAST_CHK_LOGIN] = {
                "status": status,
                "body": _body_kind(text),
                "csrf": bool(csrf),
                "redirects": len(history),
                "final_host": str(final_host) if final_host else None,
                "at": _utc_now_iso(),
            }

        # A rate limit or upstream outage says nothing about cookie validity.
        # Preserve the connection-error path instead of triggering auth retries
        # and eventually requesting replacement credentials.
        if resp.status == 429 or resp.status >= 500:
            raise HomeAssistantError(
                f"SmartThings Find temporarily unavailable (HTTP {resp.status})"
            )

        # STF는 200 + body 'fail' 로도 만료를 표현함
        if resp.status == 401 or text in ("fail", "Logout"):
            raise ConfigEntryAuthFailed(
                f"SmartThings Find session invalid/expired (HTTP {resp.status})"
            )

        if resp.status != 200 or not csrf:
            raise ConfigEntryAuthFailed(
                f"CSRF token not found. status={resp.status}, csrf={bool(csrf)}"
            )

        if entry_id is not None:
            hass.data[DOMAIN][entry_id]["_csrf"] = csrf

        return csrf


async def fetch_csrf(
    hass: HomeAssistant,
    session: aiohttp.ClientSession,
    entry_id: str | None = None,
    *,
    retry_delays: tuple[int | float, ...] = AUTH_RETRY_DELAYS,
) -> str:
    """Fetch CSRF, tolerating short-lived false Logout responses."""
    return await retry_auth_operation(
        lambda: _fetch_csrf_once(hass, session, entry_id),
        retry_delays=retry_delays,
    )


async def retry_auth_operation(
    operation: Callable[[], Awaitable[_T]],
    *,
    retry_delays: tuple[int | float, ...] = AUTH_RETRY_DELAYS,
) -> _T:
    """Retry only authentication failures, preserving all other error paths."""
    for attempt, delay in enumerate((0, *retry_delays)):
        if delay:
            await asyncio.sleep(delay)
        try:
            return await operation()
        except ConfigEntryAuthFailed:
            if attempt >= len(retry_delays):
                raise
            _LOGGER.warning(
                "SmartThings Find temporarily rejected authentication; retrying in %ss",
                retry_delays[attempt],
            )
    raise RuntimeError("unreachable")


def auth_failure_is_persistent(
    hass: HomeAssistant,
    entry_id: str,
    *,
    now: datetime | None = None,
) -> bool:
    """Return true only after continuous auth failures exceed the grace period."""
    now = now or datetime.now(tz=timezone.utc)
    entry_data = hass.data.setdefault(DOMAIN, {}).setdefault(entry_id, {})
    started = entry_data.get("auth_failure_started")
    if not isinstance(started, datetime):
        entry_data["auth_failure_started"] = now
        return False
    return (now - started).total_seconds() >= AUTH_FAILURE_GRACE_PERIOD


def clear_auth_failure(hass: HomeAssistant, entry_id: str) -> None:
    """Clear the continuous-auth-failure timer after any successful request."""
    entry_data = hass.data.get(DOMAIN, {}).get(entry_id)
    if entry_data is not None:
        entry_data.pop("auth_failure_started", None)


def normalize_location_accuracy(value: Any) -> int:
    """Return the numeric accuracy HA requires for GPS tracker zone checks."""
    try:
        accuracy = float(value)
    except (TypeError, ValueError):
        return 0
    if not math.isfinite(accuracy) or accuracy < 0:
        return 0
    return int(round(accuracy))


def _is_device_list_payload(body: str) -> bool:
    try:
        payload = json.loads(body)
    except (TypeError, ValueError):
        return False
    return isinstance(payload, dict) and isinstance(payload.get("deviceList"), list)


async def _device_list_ping(session: aiohttp.ClientSession, csrf: str) -> None:
    """Request the device list like the web client; only a JSON list succeeds."""
    url = URL_DEVICE_LIST.update_query({"_csrf": csrf})
    async with session.post(url, headers={"Accept": "application/json"}, data={}) as resp:
        body = (await resp.text()).strip()
        status = resp.status

    if body in ("Logout", "fail"):
        _LOGGER.warning("KeepAlive found an expired SmartThings Find session (%s)", body)
        raise ConfigEntryAuthFailed(f"Session expired while keepalive ping: body='{body}'")
    if status in (401, 403):
        raise ConfigEntryAuthFailed(f"Session invalid while keepalive ping (HTTP {status})")
    if status != 200:
        raise HomeAssistantError(f"SmartThings Find keepalive failed (HTTP {status})")
    if not _is_device_list_payload(body):
        _LOGGER.debug(
            "KeepAlive device list was not JSON (%s, %d bytes)",
            _body_kind(body),
            len(body),
        )
        raise HomeAssistantError("SmartThings Find keepalive returned an unexpected response")


async def keepalive_ping(hass: HomeAssistant, session: aiohttp.ClientSession, entry_id: str) -> None:
    """Confirm the login with chkLogin.do, then keep the web session active.

    chkLogin.do runs on every KeepAlive, also in passive mode where polling never
    calls it, so the login state is checked as documented and a fresh CSRF token
    is cached. The device list request is the same activity the web client makes.
    Only a JSON device list counts as success; a sign-in page or an outage is an
    error without being treated as an authentication failure.

    When chkLogin.do fails while a CSRF token is cached, the device list decides,
    as it did before 1.4.6: a session that still lists devices is alive, and an
    expired one fails that request too.
    """
    cached_csrf = _entry_runtime(hass, entry_id).get("_csrf")
    try:
        csrf = await fetch_csrf(hass, session, entry_id, retry_delays=())
    except Exception as chk_error:
        if not cached_csrf:
            raise
        try:
            await _device_list_ping(session, cached_csrf)
        except ConfigEntryAuthFailed:
            raise
        except Exception:  # noqa: BLE001
            raise chk_error from None
        _LOGGER.debug(
            "chkLogin.do failed (%s) but the session still lists devices",
            type(chk_error).__name__,
        )
        return

    await _device_list_ping(session, csrf)


def parse_stf_date(datestr: str) -> datetime:
    return datetime.strptime(datestr, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)


def _parse_operation_gps_date(dev_name: str, gps_utc_dt: Any) -> datetime | None:
    try:
        return parse_stf_date(gps_utc_dt)
    except (TypeError, ValueError):
        _LOGGER.debug("[%s] Ignoring operation with malformed gpsUtcDt", dev_name)
        return None


def calc_gps_accuracy(hu: Any, vu: Any) -> float | None:
    try:
        return round((float(hu) ** 2 + float(vu) ** 2) ** 0.5, 1)
    except Exception:
        return None


def get_battery_level(_dev_name: str, ops: list[dict[str, Any]]) -> int | None:
    for op in ops or []:
        if op.get("oprnType") == OP_CHECK_CONNECTION and "battery" in op:
            batt_raw = op.get("battery")
            if batt_raw is None:
                return None
            batt = BATTERY_LEVELS.get(str(batt_raw), None)
            if batt is not None:
                return batt
            try:
                return int(batt_raw)
            except Exception:
                return None
    return None


async def _post_json(session: aiohttp.ClientSession, url: URL, payload: dict[str, Any]) -> tuple[int, str]:
    async with session.post(url, json=payload, headers={"Accept": "application/json"}) as resp:
        text = await resp.text()
        # ✅ 쿠키 만료 시 200 OK + body "fail"/"Logout" 반환 케이스 체크
        text_stripped = text.strip()
        if text_stripped in ("fail", "Logout"):
            raise ConfigEntryAuthFailed(f"Session expired: body='{text_stripped}'")
        return resp.status, text


async def send_operation(
    hass: HomeAssistant,
    session: aiohttp.ClientSession,
    entry_id: str,
    payload: dict[str, Any],
) -> None:
    hass.data.setdefault(DOMAIN, {}).setdefault(entry_id, {})
    csrf = hass.data[DOMAIN][entry_id].get("_csrf")
    if not csrf:
        csrf = await fetch_csrf(hass, session, entry_id)

    url = URL_ADD_OPERATION.update_query({"_csrf": csrf})

    status, text = await _post_json(session, url, payload)
    if status != 200:
        # Never log the payload (account and device identifiers) or the body.
        _LOGGER.error(
            "SmartThings Find operation %s failed (HTTP %s, %d response bytes)",
            payload.get("operation"),
            status,
            len(text),
        )
        if status in (401, 403) or text.strip() in ("Logout", "fail"):
            raise ConfigEntryAuthFailed(
                f"Session invalid while sending operation (HTTP {status})"
            )
        raise HomeAssistantError(f"SmartThings Find operation failed: {status}")


async def get_device_location(
    hass: HomeAssistant,
    session: aiohttp.ClientSession,
    dev_data: dict[str, Any],
    entry_id: str,
) -> dict[str, Any] | None:
    dev_id = dev_data.get("dvceID")
    dev_name = dev_data.get("modelName", dev_id)

    hass.data.setdefault(DOMAIN, {}).setdefault(entry_id, {})
    csrf = hass.data[DOMAIN][entry_id].get("_csrf")
    if not csrf:
        csrf = await fetch_csrf(hass, session, entry_id)

    set_last_payload = {"dvceId": dev_id, "removeDevice": []}
    update_payload = {"dvceId": dev_id, "operation": OP_CHECK_CONNECTION_WITH_LOCATION, "usrId": dev_data.get("usrId")}

    try:
        active = (
            (dev_data.get("deviceTypeCode") == "TAG" and hass.data[DOMAIN][entry_id].get(CONF_ACTIVE_MODE_SMARTTAGS))
            or (dev_data.get("deviceTypeCode") != "TAG" and hass.data[DOMAIN][entry_id].get(CONF_ACTIVE_MODE_OTHERS))
        )

        # Active 모드일 때만 "위치 업데이트 요청"을 먼저 날림
        if active:
            await _post_json(session, URL_ADD_OPERATION.update_query({"_csrf": csrf}), update_payload)

        async with session.post(
            URL_SET_LAST_DEVICE.update_query({"_csrf": csrf}),
            json=set_last_payload,
            headers={"Accept": "application/json"},
        ) as resp:
            text = await resp.text()

            # ✅ 쿠키 만료 시 200 OK + body "fail"/"Logout" 반환하는 케이스 처리
            text_stripped = text.strip()
            if text_stripped in ("fail", "Logout"):
                _LOGGER.warning("[%s] Session expired (body=%s), triggering reauth", dev_name, text_stripped)
                raise ConfigEntryAuthFailed(f"Session expired while fetching location: body='{text_stripped}'")

            if resp.status != 200:
                _LOGGER.error(
                    "[%s] Failed to fetch device data (HTTP %s, %d response bytes)",
                    dev_name,
                    resp.status,
                    len(text),
                )
                if resp.status in (401, 403):
                    raise ConfigEntryAuthFailed(
                        f"Session invalid while fetching location (HTTP {resp.status})"
                    )
                return None

            data = json.loads(text) if text else {}
            res: dict[str, Any] = {
                "dev_name": dev_name,
                "dev_id": dev_id,
                "update_success": True,
                "location_found": False,
                "used_op": None,
                "used_loc": None,
                "ops": [],
                # 폴링 시점 (Last update fallback 용)
                "fetched_at": datetime.now(tz=timezone.utc),
            }

            ops = data.get("operation") or []
            if not ops:
                res["update_success"] = False
                return res

            res["ops"] = ops

            # battery best-effort
            res["battery_level"] = get_battery_level(dev_name, ops)

            used_op = None
            used_loc = {"latitude": None, "longitude": None, "gps_accuracy": None, "gps_date": None}

            for op in ops:
                if op.get("oprnType") in ("LOCATION", "LASTLOC", "OFFLINE_LOC"):
                    if "latitude" in op or "longitude" in op:
                        extra = op.get("extra") or {}
                        if "gpsUtcDt" not in extra:
                            continue
                        utc_date = _parse_operation_gps_date(dev_name, extra["gpsUtcDt"])
                        if utc_date is None:
                            continue

                        if used_loc["gps_date"] and used_loc["gps_date"] >= utc_date:
                            continue

                        if "latitude" in op:
                            used_loc["latitude"] = float(op["latitude"])
                        if "longitude" in op:
                            used_loc["longitude"] = float(op["longitude"])

                        used_loc["gps_accuracy"] = calc_gps_accuracy(
                            op.get("horizontalUncertainty"), op.get("verticalUncertainty")
                        )
                        used_loc["gps_date"] = utc_date
                        used_op = op
                        res["location_found"] = True

                    elif "encLocation" in op:
                        loc = op["encLocation"]
                        if isinstance(loc, dict) and loc.get("encrypted") is True:
                            continue
                        if isinstance(loc, dict) and "gpsUtcDt" in loc:
                            utc_date = _parse_operation_gps_date(dev_name, loc["gpsUtcDt"])
                            if utc_date is None:
                                continue
                            if used_loc["gps_date"] and used_loc["gps_date"] >= utc_date:
                                continue
                            if "latitude" in loc:
                                used_loc["latitude"] = float(loc["latitude"])
                            if "longitude" in loc:
                                used_loc["longitude"] = float(loc["longitude"])
                            used_loc["gps_accuracy"] = calc_gps_accuracy(
                                loc.get("horizontalUncertainty"), loc.get("verticalUncertainty")
                            )
                            used_loc["gps_date"] = utc_date
                            used_op = op
                            res["location_found"] = True

            res["used_op"] = used_op
            res["used_loc"] = used_loc
            return res

    except ConfigEntryAuthFailed:
        raise
    except Exception as e:
        # The exception text can quote coordinates or response content, so
        # neither the error log nor a debug traceback carries it.
        _LOGGER.error(
            "[%s] Unexpected SmartThings Find location response (%s)",
            dev_name,
            type(e).__name__,
        )
        return None
