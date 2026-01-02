from __future__ import annotations

import html
import json
import logging
from http.cookies import SimpleCookie
from typing import Any, Mapping

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers import device_registry

from .const import DOMAIN, CONF_COOKIE, CONF_ACTIVE_MODE_SMARTTAGS, CONF_ACTIVE_MODE_OTHERS

_LOGGER = logging.getLogger(__name__)

SMARTTHINGSFIND_ORIGIN = "https://smartthingsfind.samsung.com"
SMARTTHINGSFIND_HOST = "smartthingsfind.samsung.com"

URL_GET_CSRF = "https://smartthingsfind.samsung.com/chkLogin.do"
URL_DEVICE_LIST = "https://smartthingsfind.samsung.com/device/getDeviceList.do"
URL_REQUEST_LOC_UPDATE = "https://smartthingsfind.samsung.com/dm/addOperation.do"
URL_SET_LAST_DEVICE = "https://smartthingsfind.samsung.com/device/setLastSelect.do"

_AUTH_FAIL_MARKERS = ("logout", "fail", "<html", "<!doctype", "login", "signin", "sign in")


def make_session(hass: HomeAssistant) -> aiohttp.ClientSession:
    """Create a dedicated session with browser-like headers to reduce 'fail' responses."""
    cookie_jar = aiohttp.CookieJar(unsafe=True)
    timeout = aiohttp.ClientTimeout(total=30)

    headers = {
        # SmartThings Find가 비브라우저 UA에 민감하게 동작하는 케이스 방지
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/121.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Origin": SMARTTHINGSFIND_ORIGIN,
        "Referer": SMARTTHINGSFIND_ORIGIN + "/",
    }

    return async_create_clientsession(
        hass,
        timeout=timeout,
        cookie_jar=cookie_jar,
        raise_for_status=False,
        headers=headers,
    )


def parse_cookie_header(cookie_line: str) -> dict[str, str]:
    cookie_line = (cookie_line or "").strip()
    if not cookie_line:
        return {}

    if cookie_line.lower().startswith("cookie:"):
        cookie_line = cookie_line.split(":", 1)[1].strip()

    sc = SimpleCookie()
    try:
        sc.load(cookie_line)
        return {k: morsel.value for k, morsel in sc.items()}
    except Exception:  # noqa: BLE001
        out: dict[str, str] = {}
        for part in cookie_line.split(";"):
            part = part.strip()
            if not part or "=" not in part:
                continue
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
        return out


def apply_cookies_to_session(session: aiohttp.ClientSession, cookies: Mapping[str, str]) -> None:
    if not cookies:
        return
    session.cookie_jar.update_cookies(dict(cookies), response_url=SMARTTHINGSFIND_ORIGIN)


def _looks_like_auth_failure(text: str) -> bool:
    t = (text or "").strip().lower()
    return any(m in t for m in _AUTH_FAIL_MARKERS)


async def _safe_json(resp: aiohttp.ClientResponse, *, context: str) -> Any:
    text = await resp.text(errors="ignore")
    s = (text or "").strip()

    if resp.status in (401, 403) or _looks_like_auth_failure(s):
        raise ConfigEntryAuthFailed(f"Session invalid while {context}: {resp.status} '{s[:200]}'")

    if not s:
        raise RuntimeError(f"Empty response while {context} (status={resp.status})")

    try:
        return json.loads(s)
    except Exception as err:  # noqa: BLE001
        snippet = s[:300].replace("\n", "\\n")
        raise RuntimeError(f"Non-JSON while {context}: status={resp.status} body='{snippet}'") from err


async def fetch_csrf(hass: HomeAssistant, session: aiohttp.ClientSession, entry_id: str) -> str:
    """
    Fetch CSRF token from chkLogin.do.
    - 200이어도 body='fail'이면 세션 만료로 판단
    - redirect(login)로 떨어지는 경우도 만료로 판단
    """
    async with session.get(URL_GET_CSRF, allow_redirects=False) as resp:
        text = await resp.text(errors="ignore")
        csrf_token = resp.headers.get("_csrf")

        stripped = (text or "").strip()

        # 로그인 페이지로 리다이렉트(302 등)되면 사실상 인증 실패
        if resp.status in (301, 302, 303, 307, 308):
            raise ConfigEntryAuthFailed(f"Session invalid/expired (redirected): {resp.status}")

        if resp.status == 200 and csrf_token and not _looks_like_auth_failure(stripped):
            # entry_id가 정상 세팅된 경우에만 저장
            if entry_id and DOMAIN in hass.data and entry_id in hass.data[DOMAIN]:
                hass.data[DOMAIN][entry_id]["_csrf"] = csrf_token
            return csrf_token

        if resp.status == 200 and _looks_like_auth_failure(stripped):
            raise ConfigEntryAuthFailed(
                f"SmartThings Find session invalid/expired (chkLogin.do returned 200 but body='{stripped[:50]}')"
            )

        raise ConfigEntryAuthFailed(
            f"Failed to authenticate with SmartThings Find: [{resp.status}] '{stripped[:200]}'"
        )


async def get_devices(hass: HomeAssistant, session: aiohttp.ClientSession, entry_id: str) -> list[dict[str, Any]]:
    csrf = hass.data[DOMAIN][entry_id]["_csrf"]
    url = f"{URL_DEVICE_LIST}?_csrf={csrf}"

    async with session.post(url, headers={"Accept": "application/json"}, data={}, allow_redirects=False) as resp:
        if resp.status != 200:
            body = (await resp.text(errors="ignore")).strip()
            if resp.status in (401, 403) or _looks_like_auth_failure(body):
                raise ConfigEntryAuthFailed(f"Session invalid while fetching devices: {resp.status} '{body[:200]}'")
            raise RuntimeError(f"Failed to retrieve devices [{resp.status}]: '{body[:200]}'")

        payload = await _safe_json(resp, context="fetching devices")

    devices_data = payload.get("deviceList") or []
    devices: list[dict[str, Any]] = []

    dr = device_registry.async_get(hass)

    for device in devices_data:
        if "modelName" in device:
            device["modelName"] = html.unescape(html.unescape(device["modelName"]))

        dvce_id = device.get("dvceID") or device.get("dvceId")
        if not dvce_id:
            continue

        identifier = (DOMAIN, str(dvce_id))
        ha_dev = dr.async_get_device({identifier})
        if ha_dev and ha_dev.disabled:
            continue

        ha_dev_info = DeviceInfo(
            identifiers={identifier},
            manufacturer="Samsung",
            name=device.get("modelName") or f"Device {dvce_id}",
            model=device.get("modelID") or device.get("modelId") or "Unknown",
            configuration_url=SMARTTHINGSFIND_ORIGIN + "/",
        )
        devices.append({"data": device, "ha_dev_info": ha_dev_info})

    return devices


async def persist_cookie_to_entry(hass: HomeAssistant, entry: ConfigEntry, session: aiohttp.ClientSession) -> None:
    jar = session.cookie_jar.filter_cookies(SMARTTHINGSFIND_ORIGIN)
    if not jar:
        return

    parts: list[str] = []
    for name, morsel in jar.items():
        if name and morsel.value is not None:
            parts.append(f"{name}={morsel.value}")

    if not parts:
        return

    cookie_line = "; ".join(parts).strip()
    old = (entry.data.get(CONF_COOKIE) or "").strip()

    if cookie_line and cookie_line != old:
        new_data = dict(entry.data)
        new_data[CONF_COOKIE] = cookie_line
        hass.config_entries.async_update_entry(entry, data=new_data)
