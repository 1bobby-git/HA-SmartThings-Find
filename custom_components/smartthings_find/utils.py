from __future__ import annotations

import asyncio
import html
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from http.cookies import SimpleCookie
from typing import Any, Mapping

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers import device_registry

from .const import (
    DOMAIN,
    CONF_COOKIE,
    CONF_ACTIVE_MODE_SMARTTAGS,
    CONF_ACTIVE_MODE_OTHERS,
)

_LOGGER = logging.getLogger(__name__)

URL_GET_CSRF = "https://smartthingsfind.samsung.com/chkLogin.do"
URL_DEVICE_LIST = "https://smartthingsfind.samsung.com/device/getDeviceList.do"
URL_REQUEST_LOC_UPDATE = "https://smartthingsfind.samsung.com/dm/addOperation.do"
URL_SET_LAST_DEVICE = "https://smartthingsfind.samsung.com/device/setLastSelect.do"

SMARTTHINGSFIND_ORIGIN = "https://smartthingsfind.samsung.com"
SMARTTHINGSFIND_HOST = "smartthingsfind.samsung.com"

# 응답이 JSON이 아닌 로그인/HTML 페이지로 떨어질 때 나타나는 흔한 패턴들
_AUTH_FAIL_MARKERS = (
    "logout",
    "fail",
    "login",
    "signin",
    "sign in",
    "<!doctype",
    "<html",
)


@dataclass(frozen=True)
class SafeJsonResult:
    data: Any
    text: str
    content_type: str
    status: int


def make_session(hass: HomeAssistant) -> aiohttp.ClientSession:
    """Create a dedicated session for this config entry (with its own cookie jar)."""
    # cookie_jar unsafe=True : 일부 도메인 쿠키 처리에서 필요할 수 있음
    cookie_jar = aiohttp.CookieJar(unsafe=True)
    timeout = aiohttp.ClientTimeout(total=30)
    return async_create_clientsession(
        hass,
        timeout=timeout,
        cookie_jar=cookie_jar,
        raise_for_status=False,
    )


def parse_cookie_header(cookie_line: str) -> dict[str, str]:
    """
    Parse a Cookie header string like:
      "name=value; name2=value2"
    Also accepts "Cookie: name=value; ..."
    """
    cookie_line = (cookie_line or "").strip()
    if not cookie_line:
        return {}

    if cookie_line.lower().startswith("cookie:"):
        cookie_line = cookie_line.split(":", 1)[1].strip()

    sc = SimpleCookie()
    try:
        sc.load(cookie_line)
    except Exception:  # noqa: BLE001
        # fallback: naive split
        out: dict[str, str] = {}
        for part in cookie_line.split(";"):
            part = part.strip()
            if not part or "=" not in part:
                continue
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
        return out

    return {k: morsel.value for k, morsel in sc.items()}


def apply_cookies_to_session(session: aiohttp.ClientSession, cookies: Mapping[str, str]) -> None:
    """Apply cookies to the session for SmartThings Find domain."""
    if not cookies:
        return
    session.cookie_jar.update_cookies(dict(cookies), response_url=SMARTTHINGSFIND_ORIGIN)


def _looks_like_auth_failure(text: str) -> bool:
    t = (text or "").strip().lower()
    if not t:
        return False
    return any(marker in t for marker in _AUTH_FAIL_MARKERS)


async def _safe_json(response: aiohttp.ClientResponse, *, context: str) -> SafeJsonResult:
    """
    Read response text first, then parse JSON safely.
    If the body is empty or not JSON, raise a clear exception.
    """
    status = response.status
    content_type = response.headers.get("Content-Type", "")

    text = await response.text(errors="ignore")
    stripped = (text or "").strip()

    # 인증 문제(서버가 HTML/Logout/fail)로 떨어지는 케이스
    if status in (401, 403):
        raise ConfigEntryAuthFailed(f"Session invalid while {context}: {status} '{stripped[:200]}'")

    if _looks_like_auth_failure(stripped):
        raise ConfigEntryAuthFailed(f"Session invalid/expired while {context}: {status} '{stripped[:200]}'")

    if not stripped:
        # 빈 바디 -> JSON 파싱 불가
        raise RuntimeError(f"Empty response while {context} (status={status}, ct='{content_type}')")

    try:
        data = json.loads(stripped)
    except Exception as err:  # noqa: BLE001
        # HTML/텍스트로 떨어진 경우 등
        snippet = stripped[:300].replace("\n", "\\n")
        raise RuntimeError(
            f"Non-JSON response while {context} (status={status}, ct='{content_type}'): '{snippet}'"
        ) from err

    return SafeJsonResult(data=data, text=text, content_type=content_type, status=status)


async def fetch_csrf(hass: HomeAssistant, session: aiohttp.ClientSession, entry_id: str) -> None:
    """
    Retrieve _csrf token from chkLogin.do.
    NOTE: chkLogin.do가 200이라도 body가 'fail'일 수 있음 -> 반드시 검사.
    """
    async with session.get(URL_GET_CSRF) as resp:
        text = await resp.text(errors="ignore")
        csrf_token = resp.headers.get("_csrf")

        if resp.status == 200 and csrf_token:
            hass.data[DOMAIN][entry_id]["_csrf"] = csrf_token
            _LOGGER.debug("Fetched CSRF token (_csrf header present)")
            return

        stripped = (text or "").strip()
        if resp.status == 200 and stripped and _looks_like_auth_failure(stripped):
            raise ConfigEntryAuthFailed(
                f"SmartThings Find session invalid/expired (chkLogin.do returned {resp.status} but body='{stripped}')"
            )

        raise ConfigEntryAuthFailed(
            f"Failed to authenticate with SmartThings Find: [{resp.status}] '{stripped[:200]}' (csrf_header={bool(csrf_token)})"
        )


async def get_devices(hass: HomeAssistant, session: aiohttp.ClientSession, entry_id: str) -> list[dict[str, Any]]:
    """Fetch device list."""
    csrf = hass.data[DOMAIN][entry_id]["_csrf"]
    url = f"{URL_DEVICE_LIST}?_csrf={csrf}"

    async with session.post(url, headers={"Accept": "application/json"}, data={}) as resp:
        if resp.status != 200:
            body = (await resp.text(errors="ignore")).strip()
            if resp.status in (401, 403) or _looks_like_auth_failure(body):
                raise ConfigEntryAuthFailed(f"Session invalid while fetching devices: {resp.status} '{body[:200]}'")
            raise RuntimeError(f"Failed to retrieve devices [{resp.status}]: '{body[:200]}'")

        js = await _safe_json(resp, context="fetching devices")
        payload = js.data

    devices_data = payload.get("deviceList") or []
    devices: list[dict[str, Any]] = []

    dr = device_registry.async_get(hass)

    for device in devices_data:
        # Double unescaping required in original upstream
        if "modelName" in device:
            device["modelName"] = html.unescape(html.unescape(device["modelName"]))

        dvce_id = device.get("dvceID") or device.get("dvceId")
        if not dvce_id:
            continue

        identifier = (DOMAIN, str(dvce_id))
        ha_dev = dr.async_get_device({identifier})
        if ha_dev and ha_dev.disabled:
            _LOGGER.debug(
                "Ignoring disabled device: '%s' (disabled_by=%s)",
                device.get("modelName"),
                ha_dev.disabled_by,
            )
            continue

        ha_dev_info = DeviceInfo(
            identifiers={identifier},
            manufacturer="Samsung",
            name=device.get("modelName") or f"Device {dvce_id}",
            model=device.get("modelID") or device.get("modelId") or "Unknown",
            configuration_url="https://smartthingsfind.samsung.com/",
        )

        devices.append({"data": device, "ha_dev_info": ha_dev_info})

    return devices


async def get_device_location(
    hass: HomeAssistant,
    session: aiohttp.ClientSession,
    dev_data: dict[str, Any],
    entry_id: str,
) -> dict[str, Any]:
    """
    Optionally request location update, then get latest location info (setLastSelect).
    """
    dev_id = dev_data.get("dvceID") or dev_data.get("dvceId")
    dev_name = dev_data.get("modelName") or str(dev_id)
    if not dev_id:
        raise RuntimeError("Device missing dvceID")

    csrf = hass.data[DOMAIN][entry_id]["_csrf"]

    # active/passive mode
    device_type = dev_data.get("deviceTypeCode")
    active = (
        (device_type == "TAG" and hass.data[DOMAIN][entry_id].get(CONF_ACTIVE_MODE_SMARTTAGS))
        or (device_type != "TAG" and hass.data[DOMAIN][entry_id].get(CONF_ACTIVE_MODE_OTHERS))
    )

    update_payload = {
        "dvceId": dev_id,
        "operation": "CHECK_CONNECTION_WITH_LOCATION",
        "usrId": dev_data.get("usrId"),
    }
    set_last_payload = {"dvceId": dev_id, "removeDevice": []}

    # 1) request update (optional)
    if active:
        try:
            async with session.post(f"{URL_REQUEST_LOC_UPDATE}?_csrf={csrf}", json=update_payload) as resp:
                # 응답 파싱은 필요 없지만, 인증 실패/HTML 떨어짐은 잡아야 함
                if resp.status in (401, 403):
                    text = (await resp.text(errors="ignore")).strip()
                    raise ConfigEntryAuthFailed(f"Session invalid while requesting update: {resp.status} '{text[:200]}'")
        except ConfigEntryAuthFailed:
            raise
        except Exception as err:  # noqa: BLE001
            # 업데이트 요청은 실패해도 다음 setLastSelect로 넘어가며 위치를 가져올 수 있음
            _LOGGER.debug("[%s] update request failed (continuing): %s", dev_name, err)

        # 서버가 갱신을 반영할 시간을 아주 조금 줌
        await asyncio.sleep(0.2)

    # 2) get last select (location payload)
    async with session.post(
        f"{URL_SET_LAST_DEVICE}?_csrf={csrf}",
        json=set_last_payload,
        headers={"Accept": "application/json"},
    ) as resp:
        if resp.status != 200:
            body = (await resp.text(errors="ignore")).strip()
            if resp.status in (401, 403) or _looks_like_auth_failure(body):
                raise ConfigEntryAuthFailed(f"Session invalid while fetching location: {resp.status} '{body[:200]}'")
            raise RuntimeError(f"[{dev_name}] Location response error ({resp.status}): '{body[:200]}'")

        js = await _safe_json(resp, context=f"fetching location for {dev_name}")
        data = js.data

    # 원본 스타일의 결과 구조 유지
    res: dict[str, Any] = {
        "dev_name": dev_name,
        "dev_id": dev_id,
        "update_success": True,
        "location_found": False,
        "used_op": None,
        "used_loc": None,
        "ops": [],
    }

    ops = data.get("operation") or []
    if isinstance(ops, list):
        res["ops"] = ops

    used_loc: dict[str, Any] | None = None
    used_op: dict[str, Any] | None = None

    # 가장 “쓸만한 최신” 위치를 고르는 로직 (기존 방식 보존 + 방어 강화)
    for op in ops:
        loc = op.get("loc") if isinstance(op, dict) else None
        if not isinstance(loc, dict):
            continue

        # OFFLINE_LOC 같은 암호화/무의미 값은 패스
        loc_type = (loc.get("type") or "").upper()
        if "OFFLINE" in loc_type:
            continue

        lat = loc.get("lat") or loc.get("latitude")
        lon = loc.get("lon") or loc.get("longitude")
        if lat is None or lon is None:
            continue

        # 시간 비교: gpsDate / dt 등 다양한 키 대응
        gps_dt = None
        for k in ("gpsDate", "dt", "gpsDtm", "time"):
            if k in loc:
                gps_dt = loc.get(k)
                break

        # 문자열이면 그대로 비교 어렵지만, 최신/우선순위만 사용
        if used_loc is None:
            used_loc = loc
            used_op = op
        else:
            # gps_dt 비교 가능하면 비교
            try:
                prev = used_loc.get("gpsDate") or used_loc.get("dt") or used_loc.get("time")
                if prev and gps_dt and str(gps_dt) > str(prev):
                    used_loc = loc
                    used_op = op
            except Exception:  # noqa: BLE001
                pass

    if used_loc:
        res["location_found"] = True
        res["used_op"] = used_op
        res["used_loc"] = {
            "latitude": used_loc.get("lat") or used_loc.get("latitude"),
            "longitude": used_loc.get("lon") or used_loc.get("longitude"),
            "gps_accuracy": used_loc.get("acc") or used_loc.get("accuracy"),
            "gps_date": used_loc.get("gpsDate") or used_loc.get("dt") or used_loc.get("time"),
        }

    return res


async def persist_cookie_to_entry(hass: HomeAssistant, entry: ConfigEntry, session: aiohttp.ClientSession) -> None:
    """
    Persist current cookies back into config entry data (best effort).
    This helps when Samsung rotates/updates cookies.
    """
    # cookie_jar 내부를 Cookie 헤더 문자열로 직렬화
    jar = session.cookie_jar.filter_cookies(SMARTTHINGSFIND_ORIGIN)
    if not jar:
        return

    parts: list[str] = []
    for name, morsel in jar.items():
        val = morsel.value
        if not name or val is None:
            continue
        parts.append(f"{name}={val}")

    if not parts:
        return

    cookie_line = "; ".join(parts)
    old = (entry.data.get(CONF_COOKIE) or "").strip()
    if cookie_line.strip() and cookie_line.strip() != old:
        new_data = dict(entry.data)
        new_data[CONF_COOKIE] = cookie_line.strip()
        hass.config_entries.async_update_entry(entry, data=new_data)
        _LOGGER.debug("Persisted updated cookies into config entry")


def utc_now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"
