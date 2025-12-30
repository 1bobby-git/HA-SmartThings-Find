from __future__ import annotations

import html
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, Tuple

import aiohttp
from yarl import URL

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.entity import DeviceInfo

from .const import (
    DOMAIN,
    BATTERY_LEVELS,
    CONF_ACTIVE_MODE_SMARTTAGS,
    CONF_ACTIVE_MODE_OTHERS,
)

_LOGGER = logging.getLogger(__name__)

STF_BASE = URL("https://smartthingsfind.samsung.com/")
STF_HOST = "https://smartthingsfind.samsung.com"

URL_GET_CSRF = "https://smartthingsfind.samsung.com/chkLogin.do"
URL_DEVICE_LIST = "https://smartthingsfind.samsung.com/device/getDeviceList.do"
URL_REQUEST_LOC_UPDATE = "https://smartthingsfind.samsung.com/dm/addOperation.do"
URL_SET_LAST_DEVICE = "https://smartthingsfind.samsung.com/device/setLastSelect.do"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept": "*/*",
    "Referer": "https://smartthingsfind.samsung.com/",
    "Origin": "https://smartthingsfind.samsung.com",
    "X-Requested-With": "XMLHttpRequest",
}

# aiohttp/http.cookies rejects whitespace and many chars in cookie names.
_COOKIE_NAME_RE = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")


async def create_stf_session(hass: HomeAssistant) -> aiohttp.ClientSession:
    """Dedicated session for this integration (own cookie jar)."""
    return async_create_clientsession(
        hass,
        cookie_jar=aiohttp.CookieJar(unsafe=True),
        headers=DEFAULT_HEADERS,
    )


def normalize_cookies(raw: Dict[str, str]) -> Dict[str, str]:
    """Sanitize cookie dict to prevent CookieError (illegal key)."""
    cleaned: Dict[str, str] = {}
    for k, v in (raw or {}).items():
        key = "" if k is None else str(k).strip()
        val = "" if v is None else str(v).strip()

        # common bad parse: "cookie sa_trace"
        if key.lower().startswith("cookie "):
            key = key[7:].strip()

        if not key or any(ch.isspace() for ch in key):
            _LOGGER.warning("Dropping invalid cookie key (whitespace): %r", key)
            continue

        if not _COOKIE_NAME_RE.match(key):
            _LOGGER.warning("Dropping invalid cookie key (token rule): %r", key)
            continue

        cleaned[key] = val

    return cleaned


def apply_cookies_to_session(session: aiohttp.ClientSession, cookies: Dict[str, str]) -> Dict[str, str]:
    """Apply sanitized cookies scoped to STF domain."""
    cleaned = normalize_cookies(cookies)
    session.cookie_jar.update_cookies(cleaned, response_url=STF_BASE)
    return cleaned


def get_cookies_from_session(session: aiohttp.ClientSession) -> Dict[str, str]:
    jar = session.cookie_jar.filter_cookies(STF_HOST)
    return {k: v.value for k, v in jar.items()}


async def validate_cookies(hass: HomeAssistant, cookies: Dict[str, str]) -> bool:
    """Validate cookies by calling chkLogin.do."""
    session = await create_stf_session(hass)
    try:
        apply_cookies_to_session(session, cookies)
        async with session.get(URL_GET_CSRF) as resp:
            body = (await resp.text()).strip()
            if resp.status != 200:
                _LOGGER.error("validate_cookies: status=%s body=%s", resp.status, body[:200])
                return False
            if body.lower() == "fail" or "logout" in body.lower():
                _LOGGER.error("validate_cookies: body indicates fail/logout: %s", body[:200])
                return False
            return bool(resp.headers.get("_csrf"))
    finally:
        await session.close()


async def fetch_csrf(hass: HomeAssistant, session: aiohttp.ClientSession, entry_id: str) -> None:
    async with session.get(URL_GET_CSRF) as response:
        body = (await response.text()).strip()

        if response.status == 200 and (body.lower() == "fail" or "logout" in body.lower()):
            raise ConfigEntryAuthFailed(
                f"SmartThings Find session invalid/expired (chkLogin.do returned 200 but body='{body}')"
            )

        if response.status != 200:
            raise ConfigEntryAuthFailed(
                f"Failed to authenticate with SmartThings Find: [{response.status}]: {body[:200]}"
            )

        csrf_token = response.headers.get("_csrf")
        if not csrf_token:
            raise ConfigEntryAuthFailed(
                f"CSRF token not found in response headers. Status={response.status}, Body='{body[:200]}'"
            )

        hass.data[DOMAIN][entry_id]["_csrf"] = csrf_token
        _LOGGER.info("Successfully fetched CSRF Token")


async def get_devices(hass: HomeAssistant, session: aiohttp.ClientSession, entry_id: str) -> list[dict[str, Any]]:
    url = f"{URL_DEVICE_LIST}?_csrf={hass.data[DOMAIN][entry_id]['_csrf']}"
    async with session.post(url, headers={"Accept": "application/json"}, data={}) as response:
        text = await response.text()

        if response.status != 200:
            _LOGGER.error("Failed to retrieve devices [%s]: %s", response.status, text[:250])
            if response.status in (401, 403) or text.strip().lower() in ("logout", "fail"):
                raise ConfigEntryAuthFailed("Auth invalid while fetching device list")
            return []

        response_json = json.loads(text)
        devices_data = response_json.get("deviceList", [])
        devices: list[dict[str, Any]] = []

        for device in devices_data:
            device["modelName"] = html.unescape(html.unescape(device["modelName"]))

            identifier = (DOMAIN, device["dvceID"])
            ha_dev = device_registry.async_get(hass).async_get_device({identifier})
            if ha_dev and ha_dev.disabled:
                continue

            ha_dev_info = DeviceInfo(
                identifiers={identifier},
                manufacturer="Samsung",
                name=device["modelName"],
                model=device["modelID"],
                configuration_url=str(STF_BASE),
            )
            devices.append({"data": device, "ha_dev_info": ha_dev_info})

        return devices


async def get_device_location(
    hass: HomeAssistant,
    session: aiohttp.ClientSession,
    dev_data: dict[str, Any],
    entry_id: str,
) -> dict[str, Any] | None:
    dev_id = dev_data["dvceID"]
    dev_name = dev_data["modelName"]

    set_last_payload = {"dvceId": dev_id, "removeDevice": []}
    update_payload = {
        "dvceId": dev_id,
        "operation": "CHECK_CONNECTION_WITH_LOCATION",
        "usrId": dev_data["usrId"],
    }

    csrf_token = hass.data[DOMAIN][entry_id]["_csrf"]

    try:
        active = (
            (dev_data.get("deviceTypeCode") == "TAG" and hass.data[DOMAIN][entry_id][CONF_ACTIVE_MODE_SMARTTAGS])
            or (dev_data.get("deviceTypeCode") != "TAG" and hass.data[DOMAIN][entry_id][CONF_ACTIVE_MODE_OTHERS])
        )

        if active:
            async with session.post(f"{URL_REQUEST_LOC_UPDATE}?_csrf={csrf_token}", json=update_payload):
                pass

        async with session.post(
            f"{URL_SET_LAST_DEVICE}?_csrf={csrf_token}",
            json=set_last_payload,
            headers={"Accept": "application/json"},
        ) as response:
            if response.status == 200:
                data = await response.json()

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
                if not ops:
                    res["update_success"] = False
                    return res

                res["ops"] = ops
                used_op = None
                used_loc = {"latitude": None, "longitude": None, "gps_accuracy": None, "gps_date": None}

                for op in ops:
                    if op.get("oprnType") not in ("LOCATION", "LASTLOC", "OFFLINE_LOC"):
                        continue

                    if "latitude" in op and "longitude" in op:
                        extra = op.get("extra") or {}
                        if "gpsUtcDt" not in extra:
                            continue
                        utc_date = parse_stf_date(extra["gpsUtcDt"])
                        if used_loc["gps_date"] and used_loc["gps_date"] >= utc_date:
                            continue

                        used_loc["latitude"] = float(op.get("latitude"))
                        used_loc["longitude"] = float(op.get("longitude"))
                        used_loc["gps_accuracy"] = calc_gps_accuracy(
                            op.get("horizontalUncertainty"), op.get("verticalUncertainty")
                        )
                        used_loc["gps_date"] = utc_date
                        used_op = op
                        res["location_found"] = True
                        continue

                    if "encLocation" in op:
                        loc = op["encLocation"]
                        if isinstance(loc, dict) and loc.get("encrypted") is True:
                            continue
                        if not isinstance(loc, dict) or "gpsUtcDt" not in loc:
                            continue

                        utc_date = parse_stf_date(loc["gpsUtcDt"])
                        if used_loc["gps_date"] and used_loc["gps_date"] >= utc_date:
                            continue

                        if "latitude" in loc and "longitude" in loc:
                            used_loc["latitude"] = float(loc["latitude"])
                            used_loc["longitude"] = float(loc["longitude"])
                            used_loc["gps_accuracy"] = calc_gps_accuracy(
                                loc.get("horizontalUncertainty"), loc.get("verticalUncertainty")
                            )
                            used_loc["gps_date"] = utc_date
                            used_op = op
                            res["location_found"] = True

                if used_op:
                    res["used_op"] = used_op
                    res["used_loc"] = used_loc

                return res

            res_text = (await response.text()).strip()
            if response.status in (401, 403) or res_text.lower() in ("logout", "fail"):
                raise ConfigEntryAuthFailed(
                    f"Session invalid while fetching location: {response.status} '{res_text}'"
                )

            _LOGGER.error("[%s] Location request failed: %s / %s", dev_name, response.status, res_text[:250])
            return None

    except ConfigEntryAuthFailed:
        raise
    except Exception as e:
        _LOGGER.error("[%s] Exception fetching location: %s", dev_name, e, exc_info=True)
        return None


def calc_gps_accuracy(hu: Any, vu: Any) -> float | None:
    try:
        return round((float(hu) ** 2 + float(vu) ** 2) ** 0.5, 1)
    except Exception:
        return None


def parse_stf_date(datestr: str) -> datetime:
    return datetime.strptime(datestr, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)


def get_battery_level(dev_name: str, ops: list[dict[str, Any]]) -> int | None:
    for op in ops or []:
        if op.get("oprnType") == "CHECK_CONNECTION" and "battery" in op:
            batt_raw = op["battery"]
            batt = BATTERY_LEVELS.get(batt_raw)
            if batt is None:
                try:
                    batt = int(batt_raw)
                except Exception:
                    _LOGGER.warning("[%s] Invalid battery level: %s", dev_name, batt_raw)
                    return None
            return batt
    return None


def get_sub_location(ops: list[dict[str, Any]], subDeviceName: str) -> Tuple[dict[str, Any], dict[str, Any]]:
    """Return (op, sub_loc) for encLocation[subDeviceName] if present."""
    if not ops or not subDeviceName:
        return {}, {}

    for op in ops:
        enc = op.get("encLocation")
        if not isinstance(enc, dict):
            continue
        loc = enc.get(subDeviceName)
        if not isinstance(loc, dict):
            continue

        try:
            sub_loc = {
                "latitude": float(loc["latitude"]) if "latitude" in loc else None,
                "longitude": float(loc["longitude"]) if "longitude" in loc else None,
                "gps_accuracy": calc_gps_accuracy(loc.get("horizontalUncertainty"), loc.get("verticalUncertainty")),
                "gps_date": parse_stf_date(loc["gpsUtcDt"]) if "gpsUtcDt" in loc else None,
            }
            return op, sub_loc
        except Exception:
            _LOGGER.debug("Failed parsing sub location for %s: %s", subDeviceName, loc, exc_info=True)
            return op, {}

    return {}, {}
