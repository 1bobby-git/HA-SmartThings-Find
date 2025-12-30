from __future__ import annotations

import asyncio
import html
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import aiohttp
import pytz
from yarl import URL

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity import DeviceInfo

from .const import (
    DOMAIN,
    SMARTTHINGS_DOMAIN,
    STF_BASE,
    URL_DEVICE_LIST,
    URL_GET_CSRF,
    URL_REQUEST_OPERATION,
    URL_SET_LAST_DEVICE,
    BATTERY_LEVELS,
    CONF_ACTIVE_MODE_OTHERS,
    CONF_ACTIVE_MODE_SMARTTAGS,
)

_LOGGER = logging.getLogger(__name__)

# --- Simple SVGs (ST Find 스타일 "느낌") ---
# device_tracker / battery 만 커스텀, 나머지는 mdi로 둠
_STF_TRACKER_SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 64 64">
<defs><linearGradient id="g" x1="0" x2="0" y1="0" y2="1">
<stop offset="0" stop-color="#111827"/><stop offset="1" stop-color="#0b1220"/></linearGradient></defs>
<path d="M32 2c-10.5 0-19 8.5-19 19 0 16.5 19 41 19 41s19-24.5 19-41C51 10.5 42.5 2 32 2z" fill="url(#g)"/>
<circle cx="32" cy="21" r="12" fill="#ffffff"/>
<rect x="26.5" y="13" width="11" height="16" rx="2" fill="#60a5fa"/>
<circle cx="32" cy="27" r="1.2" fill="#1f2937"/>
</svg>"""

_STF_BATTERY_SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 64 64">
<rect x="14" y="18" width="34" height="28" rx="6" fill="#111827"/>
<rect x="48" y="26" width="4" height="12" rx="2" fill="#111827"/>
<rect x="18" y="22" width="26" height="20" rx="4" fill="#22c55e"/>
</svg>"""

def svg_data_uri(svg: str) -> str:
    # base64 없이 utf8로도 동작하지만, 환경에 따라 인코딩 이슈가 있어 간단 escape
    svg = svg.replace("\n", "").replace("\r", "")
    return "data:image/svg+xml;utf8," + aiohttp.helpers.quote(svg, safe=":/%#[]@!$&'()*+,;=?")

STF_TRACKER_PICTURE = svg_data_uri(_STF_TRACKER_SVG)
STF_BATTERY_PICTURE = svg_data_uri(_STF_BATTERY_SVG)

# --- Cookie handling ---

_COOKIE_PREFIX_RE = re.compile(r"^\s*cookie\s*:\s*", re.IGNORECASE)

def parse_cookie_header(raw: str) -> dict[str, str]:
    """
    Accepts either:
      - "Cookie: a=b; c=d"
      - "a=b; c=d"
    Returns dict suitable for aiohttp cookie_jar.update_cookies()
    """
    if not raw:
        return {}

    raw = raw.strip()
    raw = _COOKIE_PREFIX_RE.sub("", raw).strip()
    if not raw:
        return {}

    cookies: dict[str, str] = {}
    parts = [p.strip() for p in raw.split(";") if p.strip()]
    for part in parts:
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        k = k.strip()
        v = v.strip()
        # aiohttp/cookies는 key에 공백 등 들어가면 CookieError 발생
        if not k or re.search(r"\s", k):
            continue
        cookies[k] = v
    return cookies

def apply_cookies_to_session(session: aiohttp.ClientSession, cookies: dict[str, str]) -> None:
    # response_url은 반드시 yarl.URL 이어야 함 (raw_host 오류 방지)
    session.cookie_jar.update_cookies(cookies, response_url=URL(STF_BASE))

async def make_session(hass: HomeAssistant, cookie_header: str) -> aiohttp.ClientSession:
    session = async_get_clientsession(hass)
    cookies = parse_cookie_header(cookie_header)
    if not cookies:
        raise ConfigEntryAuthFailed("missing_cookie")
    apply_cookies_to_session(session, cookies)
    return session

# --- Auth / CSRF ---

async def fetch_csrf(hass: HomeAssistant, session: aiohttp.ClientSession) -> str:
    """
    chkLogin.do:
      - success: header "_csrf" 존재
      - invalid: body 'fail' or 'Logout'
    """
    async with session.get(URL_GET_CSRF) as resp:
        txt = (await resp.text()).strip()
        if resp.status != 200:
            raise ConfigEntryAuthFailed(f"SmartThings Find auth failed ({resp.status})")

        csrf = resp.headers.get("_csrf")
        if csrf:
            return csrf

        # 200인데 fail이 오는 케이스가 실제로 있음
        if txt.lower() in ("fail", "logout"):
            raise ConfigEntryAuthFailed(f"SmartThings Find session invalid/expired (chkLogin.do body='{txt}')")

        raise ConfigEntryAuthFailed("CSRF token not found in chkLogin.do response")

# --- Devices ---

def _build_device_info(hass: HomeAssistant, dev: dict[str, Any]) -> DeviceInfo:
    dvce_id = dev["dvceID"]
    identifier_ours = (DOMAIN, dvce_id)
    identifier_st = (SMARTTHINGS_DOMAIN, dvce_id)

    dr = device_registry.async_get(hass)
    st_dev = dr.async_get_device({identifier_st})

    identifiers = {identifier_ours}
    # SmartThings 공식 통합이 같은 device_id를 쓰고 있으면, 같은 Device로 merge되도록 추가
    if st_dev is not None:
        identifiers.add(identifier_st)

    model_name = html.unescape(html.unescape(dev.get("modelName") or "Unknown"))
    model_id = dev.get("modelID") or "Unknown"

    return DeviceInfo(
        identifiers=identifiers,
        manufacturer="Samsung",
        name=model_name,
        model=model_id,
        configuration_url=STF_BASE,
    )

async def get_devices(hass: HomeAssistant, session: aiohttp.ClientSession, csrf: str) -> list[dict[str, Any]]:
    url = f"{URL_DEVICE_LIST}?_csrf={csrf}"
    async with session.post(url, headers={"Accept": "application/json"}, data={}) as resp:
        if resp.status != 200:
            _LOGGER.error("Failed to retrieve devices [%s]: %s", resp.status, await resp.text())
            if resp.status in (401, 403):
                raise ConfigEntryAuthFailed("auth_failed_get_devices")
            return []

        payload = await resp.json()
        devices_data = payload.get("deviceList", []) or []
        results: list[dict[str, Any]] = []

        dr = device_registry.async_get(hass)

        for dev in devices_data:
            # disabled device skip
            identifier = (DOMAIN, dev["dvceID"])
            ha_dev = dr.async_get_device({identifier})
            if ha_dev and ha_dev.disabled:
                continue

            dev["modelName"] = html.unescape(html.unescape(dev.get("modelName") or "Unknown"))
            results.append(
                {"data": dev, "ha_dev_info": _build_device_info(hass, dev)}
            )

        return results

# --- Location & operations ---

def parse_stf_date(datestr: str) -> datetime:
    return datetime.strptime(datestr, "%Y%m%d%H%M%S").replace(tzinfo=pytz.UTC)

def calc_gps_accuracy(hu: float | None, vu: float | None) -> float | None:
    try:
        if hu is None and vu is None:
            return None
        hu_f = float(hu or 0)
        vu_f = float(vu or 0)
        return round((hu_f**2 + vu_f**2) ** 0.5, 1)
    except Exception:
        return None

def get_battery_level(ops: list[dict[str, Any]]) -> int | None:
    for op in ops or []:
        if op.get("oprnType") == "CHECK_CONNECTION" and "battery" in op:
            batt_raw = str(op.get("battery"))
            mapped = BATTERY_LEVELS.get(batt_raw)
            if mapped is not None:
                return mapped
            try:
                return int(batt_raw)
            except Exception:
                return None
    return None

def get_sub_location(ops: list[dict[str, Any]], sub_device_name: str) -> tuple[dict[str, Any], dict[str, Any]]:
    if not ops or not sub_device_name:
        return {}, {}
    for op in ops:
        enc = op.get("encLocation") or {}
        if sub_device_name in enc:
            loc = enc[sub_device_name]
            return op, {
                "latitude": float(loc["latitude"]),
                "longitude": float(loc["longitude"]),
                "gps_accuracy": calc_gps_accuracy(loc.get("horizontalUncertainty"), loc.get("verticalUncertainty")),
                "gps_date": parse_stf_date(loc["gpsUtcDt"]),
            }
    return {}, {}

async def send_operation(
    session: aiohttp.ClientSession,
    csrf: str,
    dvce_id: str,
    usr_id: str | None,
    operation: str,
    status: str | None = None,
) -> None:
    url = f"{URL_REQUEST_OPERATION}?_csrf={csrf}"
    payload: dict[str, Any] = {"dvceId": dvce_id, "operation": operation}
    if usr_id:
        payload["usrId"] = usr_id
    if status:
        payload["status"] = status

    async with session.post(url, json=payload) as resp:
        # 사이트도 성공/실패를 엄격히 다루지 않는 경우가 있어 로그만 남김
        txt = (await resp.text()).strip()
        if resp.status >= 400:
            _LOGGER.warning("Operation %s failed for %s (%s): %s", operation, dvce_id, resp.status, txt[:200])

async def get_device_location(
    hass: HomeAssistant,
    session: aiohttp.ClientSession,
    csrf: str,
    dev_data: dict[str, Any],
    active_tags: bool,
    active_others: bool,
    retry_on_unauth: bool = True,
) -> dict[str, Any] | None:
    dev_id = dev_data["dvceID"]
    dev_name = dev_data.get("modelName") or dev_id
    usr_id = dev_data.get("usrId")

    try:
        is_tag = dev_data.get("deviceTypeCode") == "TAG"
        active = (is_tag and active_tags) or ((not is_tag) and active_others)

        # Active mode: request location refresh
        if active:
            await send_operation(session, csrf, dev_id, usr_id, "CHECK_CONNECTION_WITH_LOCATION")

        # setLastSelect -> returns operations + locations
        url = f"{URL_SET_LAST_DEVICE}?_csrf={csrf}"
        set_last_payload = {"dvceId": dev_id, "removeDevice": []}

        async with session.post(url, json=set_last_payload, headers={"Accept": "application/json"}) as resp:
            txt = (await resp.text()).strip()

            # 세션 만료 패턴
            if resp.status in (401, 403) or txt == "Logout":
                if retry_on_unauth:
                    # 1회: csrf 재획득 후 재시도
                    _LOGGER.warning("[%s] Session invalid, retrying once with new CSRF...", dev_name)
                    new_csrf = await fetch_csrf(hass, session)
                    return await get_device_location(
                        hass, session, new_csrf, dev_data, active_tags, active_others, retry_on_unauth=False
                    )
                raise ConfigEntryAuthFailed(f"Session invalid while fetching location: {resp.status} '{txt}'")

            if resp.status != 200:
                _LOGGER.error("[%s] Failed to fetch device data (%s): %s", dev_name, resp.status, txt[:200])
                return None

            data = json.loads(txt) if txt and txt[0] in "{[" else await resp.json()

        ops = data.get("operation") or []
        used_op = None
        used_loc = {"latitude": None, "longitude": None, "gps_accuracy": None, "gps_date": None}

        for op in ops:
            t = op.get("oprnType")
            if t not in ("LOCATION", "LASTLOC", "OFFLINE_LOC"):
                continue

            # latitude/longitude directly
            if "latitude" in op or "longitude" in op:
                extra = op.get("extra") or {}
                if "gpsUtcDt" in extra:
                    utc_date = parse_stf_date(extra["gpsUtcDt"])
                else:
                    continue

                # choose newest
                if used_loc["gps_date"] and used_loc["gps_date"] >= utc_date:
                    continue

                used_loc["latitude"] = float(op.get("latitude")) if op.get("latitude") is not None else None
                used_loc["longitude"] = float(op.get("longitude")) if op.get("longitude") is not None else None
                used_loc["gps_accuracy"] = calc_gps_accuracy(op.get("horizontalUncertainty"), op.get("verticalUncertainty"))
                used_loc["gps_date"] = utc_date
                used_op = op
                continue

            # encLocation block
            if "encLocation" in op:
                loc = op["encLocation"]
                # encrypted -> skip
                if isinstance(loc, dict) and loc.get("encrypted") is True:
                    continue
                if "gpsUtcDt" not in loc:
                    continue

                utc_date = parse_stf_date(loc["gpsUtcDt"])
                if used_loc["gps_date"] and used_loc["gps_date"] >= utc_date:
                    continue

                used_loc["latitude"] = float(loc.get("latitude")) if loc.get("latitude") is not None else None
                used_loc["longitude"] = float(loc.get("longitude")) if loc.get("longitude") is not None else None
                used_loc["gps_accuracy"] = calc_gps_accuracy(loc.get("horizontalUncertainty"), loc.get("verticalUncertainty"))
                used_loc["gps_date"] = utc_date
                used_op = op

        result = {
            "dev_id": dev_id,
            "dev_name": dev_name,
            "usr_id": usr_id,
            "ops": ops,
            "used_op": used_op,
            "used_loc": used_loc,
            "location_found": bool(used_loc["latitude"] is not None and used_loc["longitude"] is not None),
            "battery_level": get_battery_level(ops),
            "fetched_at": datetime.now(tz=pytz.UTC),
        }
        return result

    except ConfigEntryAuthFailed:
        raise
    except Exception as e:
        _LOGGER.exception("[%s] Exception while fetching location: %s", dev_name, e)
        return None

@dataclass
class STFDevice:
    data: dict[str, Any]
    ha_dev_info: DeviceInfo
