from __future__ import annotations

import html
import json
import logging
import re
from datetime import datetime
from typing import Any

import aiohttp
import pytz
from yarl import URL

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry
from homeassistant.helpers.aiohttp_client import async_create_clientsession
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
)

_LOGGER = logging.getLogger(__name__)

_COOKIE_PREFIX_RE = re.compile(r"^\s*cookie\s*:\s*", re.IGNORECASE)

# 브라우저와 비슷한 헤더로 세션 수명 짧아지는 케이스 완화
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.7,en;q=0.6",
    "Referer": STF_BASE + "/",
    "Origin": STF_BASE,
}


def parse_cookie_header(raw: str) -> dict[str, str]:
    """
    Accepts:
      - "Cookie: a=b; c=d"
      - "a=b; c=d"
    Returns dict for aiohttp cookie_jar.update_cookies()
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

        # aiohttp/http.cookies는 공백 들어간 키에서 CookieError
        if not k or re.search(r"\s", k):
            continue

        cookies[k] = v
    return cookies


def apply_cookies_to_session(session: aiohttp.ClientSession, cookies: dict[str, str]) -> None:
    """response_url must be yarl.URL (prevents raw_host error)."""
    session.cookie_jar.update_cookies(cookies, response_url=URL(STF_BASE))


async def make_isolated_session(hass: HomeAssistant) -> aiohttp.ClientSession:
    """
    Create integration-dedicated aiohttp session (isolated cookie jar).
    Prevents cookie contamination from HA global session.
    """
    jar = aiohttp.CookieJar()
    session = async_create_clientsession(
        hass,
        headers=DEFAULT_HEADERS,
        cookie_jar=jar,
    )
    return session


async def make_session_with_cookie(hass: HomeAssistant, cookie_header: str) -> aiohttp.ClientSession:
    session = await make_isolated_session(hass)
    cookies = parse_cookie_header(cookie_header)
    if not cookies:
        await session.close()
        raise ConfigEntryAuthFailed("missing_cookie")
    apply_cookies_to_session(session, cookies)
    return session


async def fetch_csrf(hass: HomeAssistant, session: aiohttp.ClientSession, *_args: Any) -> str:
    """
    Keep-alive + CSRF refresh.
    ✅ Backward compatible: old code may call fetch_csrf(hass, session, entry_id)
    """
    async with session.get(URL_GET_CSRF) as resp:
        body = (await resp.text()).strip()

        if resp.status != 200:
            raise ConfigEntryAuthFailed(f"SmartThings Find auth failed (status={resp.status})")

        csrf = resp.headers.get("_csrf")
        if csrf:
            return csrf

        if body.lower() in ("fail", "logout"):
            raise ConfigEntryAuthFailed(
                f"SmartThings Find session invalid/expired (chkLogin.do body='{body}')"
            )

        raise ConfigEntryAuthFailed("CSRF token not found in chkLogin.do response")


def _build_device_info(hass: HomeAssistant, dev: dict[str, Any]) -> DeviceInfo:
    dvce_id = dev["dvceID"]
    identifier_ours = (DOMAIN, dvce_id)
    identifier_st = (SMARTTHINGS_DOMAIN, dvce_id)

    dr = device_registry.async_get(hass)
    st_dev = dr.async_get_device({identifier_st})

    identifiers = {identifier_ours}
    # SmartThings official device가 존재하면 같은 디바이스로 merge되게 추가
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
            identifier = (DOMAIN, dev["dvceID"])
            ha_dev = dr.async_get_device({identifier})
            if ha_dev and ha_dev.disabled:
                continue

            dev["modelName"] = html.unescape(html.unescape(dev.get("modelName") or "Unknown"))
            results.append({"data": dev, "ha_dev_info": _build_device_info(hass, dev)})

        return results


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

        # Active mode: trigger refresh
        if active:
            await send_operation(session, csrf, dev_id, usr_id, "CHECK_CONNECTION_WITH_LOCATION")

        url = f"{URL_SET_LAST_DEVICE}?_csrf={csrf}"
        set_last_payload = {"dvceId": dev_id, "removeDevice": []}

        async with session.post(url, json=set_last_payload, headers={"Accept": "application/json"}) as resp:
            txt = (await resp.text()).strip()

            if resp.status in (401, 403) or txt == "Logout":
                if retry_on_unauth:
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

            if "latitude" in op or "longitude" in op:
                extra = op.get("extra") or {}
                if "gpsUtcDt" not in extra:
                    continue

                utc_date = parse_stf_date(extra["gpsUtcDt"])
                if used_loc["gps_date"] and used_loc["gps_date"] >= utc_date:
                    continue

                used_loc["latitude"] = float(op.get("latitude")) if op.get("latitude") is not None else None
                used_loc["longitude"] = float(op.get("longitude")) if op.get("longitude") is not None else None
                used_loc["gps_accuracy"] = calc_gps_accuracy(
                    op.get("horizontalUncertainty"), op.get("verticalUncertainty")
                )
                used_loc["gps_date"] = utc_date
                used_op = op
                continue

            if "encLocation" in op:
                loc = op["encLocation"]
                if isinstance(loc, dict) and loc.get("encrypted") is True:
                    continue
                if "gpsUtcDt" not in loc:
                    continue

                utc_date = parse_stf_date(loc["gpsUtcDt"])
                if used_loc["gps_date"] and used_loc["gps_date"] >= utc_date:
                    continue

                used_loc["latitude"] = float(loc.get("latitude")) if loc.get("latitude") is not None else None
                used_loc["longitude"] = float(loc.get("longitude")) if loc.get("longitude") is not None else None
                used_loc["gps_accuracy"] = calc_gps_accuracy(
                    loc.get("horizontalUncertainty"), loc.get("verticalUncertainty")
                )
                used_loc["gps_date"] = utc_date
                used_op = op

        return {
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

    except ConfigEntryAuthFailed:
        raise
    except Exception as e:
        _LOGGER.exception("[%s] Exception while fetching location: %s", dev_name, e)
        return None
