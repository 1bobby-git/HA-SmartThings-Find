from __future__ import annotations

import html
import json
import logging
import re
from datetime import datetime
from typing import Any

import aiohttp
import pytz
from http.cookies import SimpleCookie, CookieError
from yarl import URL

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers import device_registry
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.entity import DeviceInfo

from .const import (
    DOMAIN,
    BATTERY_LEVELS,
    CONF_COOKIE,
    CONF_ACTIVE_MODE_SMARTTAGS,
    CONF_ACTIVE_MODE_OTHERS,
    CONF_ST_IDENTIFIER,
    OP_RING,
    OP_CHECK_CONNECTION_WITH_LOCATION,
    OP_CHECK_CONNECTION,
)

_LOGGER = logging.getLogger(__name__)

STF_BASE = URL("https://smartthingsfind.samsung.com/")
URL_CHK_LOGIN = STF_BASE / "chkLogin.do"
URL_DEVICE_LIST = STF_BASE / "device/getDeviceList.do"
URL_SET_LAST_DEVICE = STF_BASE / "device/setLastSelect.do"
URL_ADD_OPERATION = STF_BASE / "dm/addOperation.do"  # requires ?_csrf=

COOKIE_NAME_RE = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")

# JSON-safe smartthings identifier encoding
_ST_IDENT_PREFIX = "smartthings::"


def parse_cookie_header(cookie_header_line: str) -> dict[str, str]:
    """
    Accepts:
      - "Cookie: a=b; c=d"
      - "a=b; c=d"
    Returns dict of cookies safe for aiohttp CookieJar.
    """
    s = (cookie_header_line or "").strip()
    if not s:
        return {}

    if s.lower().startswith("cookie:"):
        s = s.split(":", 1)[1].strip()

    jar: dict[str, str] = {}
    try:
        sc = SimpleCookie()
        sc.load(s)
        for k, morsel in sc.items():
            if COOKIE_NAME_RE.match(k):
                jar[k] = morsel.value
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
    """aiohttp requires response_url to be yarl.URL, not str."""
    if not cookies:
        return
    session.cookie_jar.update_cookies(cookies, response_url=STF_BASE)


def make_session(hass: HomeAssistant) -> aiohttp.ClientSession:
    """Dedicated session (avoid polluting HA shared session)."""
    jar = aiohttp.CookieJar(unsafe=True)
    return async_create_clientsession(
        hass,
        cookie_jar=jar,
        raise_for_status=False,
    )


def _mask_set_cookie_headers(set_cookie_values: list[str]) -> list[str]:
    """Return only cookie names, mask values."""
    masked: list[str] = []
    for raw in set_cookie_values:
        first = raw.split(";", 1)[0].strip()
        if "=" in first:
            name = first.split("=", 1)[0].strip()
            masked.append(f"{name}=***")
        else:
            masked.append("***")
    return masked


def _cookiejar_to_cookie_header(session: aiohttp.ClientSession) -> str:
    """Serialize current cookie jar for STF_BASE to a Cookie header string."""
    filtered = session.cookie_jar.filter_cookies(STF_BASE)
    parts: list[str] = []
    for k, morsel in filtered.items():
        parts.append(f"{k}={morsel.value}")
    return "; ".join(parts)


async def fetch_csrf(hass: HomeAssistant, session: aiohttp.ClientSession, entry_id: str | None = None) -> str:
    """
    Calls chkLogin.do and returns CSRF from header "_csrf".
    If entry_id is given, also stores it in hass.data[DOMAIN][entry_id]["_csrf"].
    Additionally, if cookie jar changed and entry exists, persist updated cookie header.
    """
    async with session.get(URL_CHK_LOGIN) as resp:
        text = (await resp.text()).strip()
        csrf = resp.headers.get("_csrf")

        # Debug: Set-Cookie (mask values)
        try:
            sc = resp.headers.getall("Set-Cookie", [])  # type: ignore[attr-defined]
        except Exception:
            sc = []
        if sc:
            _LOGGER.debug("chkLogin.do Set-Cookie: %s", _mask_set_cookie_headers(sc))

        _LOGGER.debug("chkLogin.do status=%s csrf=%s body=%s", resp.status, bool(csrf), text[:200])

        if resp.status == 401 or text in ("fail", "Logout"):
            raise ConfigEntryAuthFailed(
                f"SmartThings Find session invalid/expired (chkLogin.do returned {resp.status} but body='{text}')"
            )

        if resp.status != 200 or not csrf:
            raise ConfigEntryAuthFailed(
                f"CSRF token not found. status={resp.status}, csrf={bool(csrf)}, body='{text[:120]}'"
            )

        if entry_id is not None:
            hass.data.setdefault(DOMAIN, {}).setdefault(entry_id, {})["_csrf"] = csrf

            # Persist refreshed cookies if they changed (best-effort).
            # Only attempt when entry_id looks like a real config entry id.
            entry = hass.config_entries.async_get_entry(entry_id)
            if entry is not None:
                new_cookie_header = _cookiejar_to_cookie_header(session)
                if new_cookie_header:
                    old_cookie_header = (entry.data.get(CONF_COOKIE) or "").strip()
                    if new_cookie_header.strip() != old_cookie_header:
                        _LOGGER.debug("Cookie jar changed -> updating stored cookie header (names only): %s", list(parse_cookie_header(new_cookie_header).keys()))
                        new_data = dict(entry.data)
                        new_data[CONF_COOKIE] = new_cookie_header
                        hass.config_entries.async_update_entry(entry, data=new_data)

        return csrf


# =========================
# 0.3.16+ SmartThings mapping helpers
# =========================

def list_smartthings_devices_for_ui(hass: HomeAssistant) -> list[tuple[str, str]]:
    """
    Returns list of (device_registry_id, label) for SmartThings official devices.
    """
    dr = device_registry.async_get(hass)
    items: list[tuple[str, str]] = []

    for dev in dr.devices.values():
        if not dev.identifiers:
            continue
        if not any(i[0] == "smartthings" for i in dev.identifiers):
            continue

        name = dev.name_by_user or dev.name or dev.model or dev.id
        label = name
        if dev.model:
            label = f"{name} ({dev.model})"
        items.append((dev.id, label))

    items.sort(key=lambda x: x[1].lower())
    return items


def _encode_smartthings_identifier(ident: tuple[str, str]) -> str:
    if not ident or len(ident) != 2:
        return ""
    if ident[0] != "smartthings":
        return ""
    return f"{_ST_IDENT_PREFIX}{ident[1]}"


def _decode_smartthings_identifier(value: Any) -> tuple[str, str] | None:
    """
    Accepts stored option value:
      - "smartthings::<id>"
      - "<id>" (tolerate)
      - ("smartthings", "<id>") legacy
    """
    if isinstance(value, (list, tuple)) and len(value) == 2:
        if value[0] == "smartthings" and isinstance(value[1], str):
            return ("smartthings", value[1])
        return None

    if isinstance(value, str):
        if value.startswith(_ST_IDENT_PREFIX):
            return ("smartthings", value[len(_ST_IDENT_PREFIX):])
        if value and "::" not in value:
            return ("smartthings", value)
    return None


def get_smartthings_identifier_value_by_device_id(hass: HomeAssistant, device_id: str) -> str:
    """
    device_registry DeviceEntry.id -> first smartthings identifier -> encoded string
    """
    dr = device_registry.async_get(hass)
    dev = dr.devices.get(device_id)
    if not dev or not dev.identifiers:
        return ""
    st_idents = [i for i in dev.identifiers if i[0] == "smartthings"]
    if not st_idents:
        return ""
    return _encode_smartthings_identifier(st_idents[0])


def _find_matching_smartthings_identifiers_by_name(hass: HomeAssistant, name: str) -> set[tuple[str, str]]:
    """Best-effort fallback if user didn't pick mapping option."""
    dr = device_registry.async_get(hass)
    name_norm = (name or "").strip().lower()

    for dev in dr.devices.values():
        if not dev.name:
            continue
        if dev.name.strip().lower() != name_norm:
            continue
        for ident in dev.identifiers:
            if ident and len(ident) == 2 and ident[0] == "smartthings":
                return set(dev.identifiers)

    return set()


async def get_devices(hass: HomeAssistant, session: aiohttp.ClientSession, entry_id: str) -> list[dict[str, Any]]:
    """device/getDeviceList.do requires csrf in query string."""
    csrf = hass.data[DOMAIN][entry_id]["_csrf"]
    url = URL_DEVICE_LIST.update_query({"_csrf": csrf})

    async with session.post(url, headers={"Accept": "application/json"}, data={}) as resp:
        if resp.status != 200:
            body = (await resp.text()).strip()
            _LOGGER.error("Failed to retrieve devices [%s]: %s", resp.status, body[:200])
            if resp.status in (401, 403) or body in ("Logout", "fail"):
                raise ConfigEntryAuthFailed("Session invalid while fetching devices")
            return []

        data = await resp.json()
        devices_data = data.get("deviceList", [])
        devices: list[dict[str, Any]] = []

        dr = device_registry.async_get(hass)

        opt_ident_raw = hass.data.get(DOMAIN, {}).get(entry_id, {}).get(CONF_ST_IDENTIFIER)
        opt_ident = _decode_smartthings_identifier(opt_ident_raw)

        for d in devices_data:
            d["modelName"] = html.unescape(html.unescape(d.get("modelName", "")))

            dvce_id = d.get("dvceID")
            model_name = d.get("modelName") or str(dvce_id) or "SmartThings Find device"

            our_identifier = (DOMAIN, str(dvce_id))
            identifiers: set[tuple[str, str]] = {our_identifier}

            if opt_ident:
                identifiers.add(opt_ident)
            else:
                identifiers |= _find_matching_smartthings_identifiers_by_name(hass, model_name)

            ha_dev = dr.async_get_device({our_identifier})
            if ha_dev and ha_dev.disabled:
                _LOGGER.debug("Ignoring disabled device: %s", model_name)
                continue

            ha_dev_info = DeviceInfo(
                identifiers=identifiers,
                manufacturer="Samsung",
                name=model_name,
                model=str(d.get("modelID") or ""),
                configuration_url=str(STF_BASE),
            )

            devices.append({"data": d, "ha_dev_info": ha_dev_info})

        return devices


def parse_stf_date(datestr: str) -> datetime:
    return datetime.strptime(datestr, "%Y%m%d%H%M%S").replace(tzinfo=pytz.UTC)


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
        return resp.status, text


async def send_operation(
    hass: HomeAssistant,
    session: aiohttp.ClientSession,
    entry_id: str,
    payload: dict[str, Any],
) -> None:
    csrf = hass.data[DOMAIN][entry_id]["_csrf"]
    url = URL_ADD_OPERATION.update_query({"_csrf": csrf})

    status, text = await _post_json(session, url, payload)
    if status != 200:
        _LOGGER.error("Operation failed status=%s body=%s payload=%s", status, text[:200], payload)
        if status in (401, 403) or text.strip() in ("Logout", "fail"):
            raise ConfigEntryAuthFailed(f"Session invalid while sending operation: {status} '{text.strip()}'")
        raise HomeAssistantError(f"SmartThings Find operation failed: {status}")


async def get_device_location(
    hass: HomeAssistant,
    session: aiohttp.ClientSession,
    dev_data: dict[str, Any],
    entry_id: str,
) -> dict[str, Any] | None:
    dev_id = dev_data.get("dvceID")
    dev_name = dev_data.get("modelName", dev_id)

    csrf = hass.data[DOMAIN][entry_id]["_csrf"]

    set_last_payload = {"dvceId": dev_id, "removeDevice": []}
    update_payload = {"dvceId": dev_id, "operation": OP_CHECK_CONNECTION_WITH_LOCATION, "usrId": dev_data.get("usrId")}

    try:
        active = (
            (dev_data.get("deviceTypeCode") == "TAG" and hass.data[DOMAIN][entry_id].get(CONF_ACTIVE_MODE_SMARTTAGS))
            or (dev_data.get("deviceTypeCode") != "TAG" and hass.data[DOMAIN][entry_id].get(CONF_ACTIVE_MODE_OTHERS))
        )

        if active:
            await _post_json(session, URL_ADD_OPERATION.update_query({"_csrf": csrf}), update_payload)

        async with session.post(
            URL_SET_LAST_DEVICE.update_query({"_csrf": csrf}),
            json=set_last_payload,
            headers={"Accept": "application/json"},
        ) as resp:
            text = await resp.text()

            if resp.status != 200:
                _LOGGER.error("[%s] Failed to fetch device data (%s): %s", dev_name, resp.status, text[:200])
                if resp.status in (401, 403) or text.strip() in ("Logout", "fail"):
                    raise ConfigEntryAuthFailed(f"Session invalid while fetching location: {resp.status} '{text.strip()}'")
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
            }

            ops = data.get("operation") or []
            if not ops:
                res["update_success"] = False
                return res

            res["ops"] = ops

            used_op = None
            used_loc = {"latitude": None, "longitude": None, "gps_accuracy": None, "gps_date": None}

            for op in ops:
                if op.get("oprnType") in ("LOCATION", "LASTLOC", "OFFLINE_LOC"):
                    if "latitude" in op or "longitude" in op:
                        extra = op.get("extra") or {}
                        if "gpsUtcDt" not in extra:
                            continue
                        utc_date = parse_stf_date(extra["gpsUtcDt"])

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
                            utc_date = parse_stf_date(loc["gpsUtcDt"])
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
        _LOGGER.error("[%s] Exception in get_device_location: %s", dev_name, e, exc_info=True)
        return None


async def ring_device(hass: HomeAssistant, session: aiohttp.ClientSession, entry_id: str, dev_data: dict[str, Any], start: bool) -> None:
    payload = {
        "dvceId": dev_data.get("dvceID"),
        "operation": OP_RING,
        "usrId": dev_data.get("usrId"),
        "status": "start" if start else "stop",
        "lockMessage": "SmartThings Find is trying to find this device.",
    }
    await send_operation(hass, session, entry_id, payload)
