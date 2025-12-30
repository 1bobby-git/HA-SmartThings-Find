import asyncio
import base64
import html
import json
import logging
import random
import re
import string
from datetime import datetime, timedelta
from io import BytesIO
from typing import Any, Dict, Optional, Tuple

import aiohttp
import pytz
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry
from homeassistant.helpers.entity import DeviceInfo

from .const import (
    DOMAIN,
    BATTERY_LEVELS,
    CONF_ACTIVE_MODE_SMARTTAGS,
    CONF_ACTIVE_MODE_OTHERS,
)

_LOGGER = logging.getLogger(__name__)

STF_BASE = "https://smartthingsfind.samsung.com"
URL_GET_CSRF = f"{STF_BASE}/chkLogin.do"
URL_DEVICE_LIST = f"{STF_BASE}/device/getDeviceList.do"
URL_REQUEST_LOC_UPDATE = f"{STF_BASE}/dm/addOperation.do"
URL_SET_LAST_DEVICE = f"{STF_BASE}/device/setLastSelect.do"


# ---------------------------
# Cookie helpers
# ---------------------------

_TOKEN_RE = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")


def parse_cookie_header(cookie_header: str) -> Dict[str, str]:
    """Parse a raw Cookie header line into a dict safe for aiohttp CookieJar.

    Accepts:
      - "Cookie: a=b; c=d"
      - "a=b; c=d"
      - Multi-line pasted values (we normalize to ';')
      - Weird "cookie sa_trace=..." (we fix by stripping leading 'cookie ' token)
    """
    if not cookie_header:
        return {}

    s = cookie_header.strip()
    # Remove leading header label if present
    if s.lower().startswith("cookie:"):
        s = s.split(":", 1)[1].strip()

    # Some browsers copy as multiple lines; normalize
    s = s.replace("\r", ";").replace("\n", ";")

    cookies: Dict[str, str] = {}
    for part in s.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        k, v = part.split("=", 1)
        k = k.strip()
        v = v.strip()

        # Fix common bad paste: "cookie sa_trace=...."
        if " " in k and k.lower().startswith("cookie "):
            k = k.split(" ", 1)[1].strip()

        if not k or not _TOKEN_RE.match(k):
            # Skip illegal cookie names to avoid http.cookies.CookieError
            _LOGGER.debug("Skipping illegal cookie key from header: %r", k)
            continue

        cookies[k] = v

    return cookies


def apply_cookies_to_session(session: aiohttp.ClientSession, cookies: Dict[str, str]) -> None:
    """Apply cookies to a session jar scoped for STF_BASE."""
    if not cookies:
        return
    session.cookie_jar.update_cookies(cookies, response_url=STF_BASE)


# ---------------------------
# Auth / request helpers
# ---------------------------

def _looks_like_logout(status: int, body: str) -> bool:
    body_s = (body or "").strip()
    if status in (401, 403):
        return True
    if body_s in ("Logout", "fail"):
        return True
    return False


async def fetch_csrf(hass: HomeAssistant, session: aiohttp.ClientSession, entry_id: str) -> None:
    """Fetch _csrf token header from chkLogin.do.

    Raises ConfigEntryAuthFailed if session/cookies are invalid.
    """
    async with session.get(URL_GET_CSRF) as response:
        text = await response.text()
        if response.status != 200:
            raise ConfigEntryAuthFailed(
                f"SmartThings Find auth failed (chkLogin.do status={response.status}, body={text!r})"
            )

        # Samsung returns 200 with body 'fail' when cookie/session invalid
        if text.strip() == "fail":
            raise ConfigEntryAuthFailed(
                "SmartThings Find session invalid/expired (chkLogin.do returned 200 but body='fail')"
            )
        if text.strip() == "Logout":
            raise ConfigEntryAuthFailed(
                "SmartThings Find session invalid/expired (chkLogin.do returned 200 but body='Logout')"
            )

        csrf_token = response.headers.get("_csrf")
        if not csrf_token:
            raise ConfigEntryAuthFailed(
                f"CSRF token not found in response headers (status={response.status}, body={text!r})"
            )

        hass.data[DOMAIN][entry_id]["_csrf"] = csrf_token
        _LOGGER.debug("Fetched new CSRF token for entry %s", entry_id)


async def _ensure_csrf(hass: HomeAssistant, session: aiohttp.ClientSession, entry_id: str) -> str:
    token = hass.data[DOMAIN][entry_id].get("_csrf")
    if not token:
        await fetch_csrf(hass, session, entry_id)
        token = hass.data[DOMAIN][entry_id].get("_csrf")
    return token


async def _request_text(
    hass: HomeAssistant,
    session: aiohttp.ClientSession,
    entry_id: str,
    method: str,
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    json_payload: Any = None,
    data: Any = None,
    retry_auth_once: bool = True,
) -> Tuple[int, str, Dict[str, str]]:
    """Make a request and return (status, text, headers).

    If 401/Logout/fail occurs, refresh CSRF and retry once, then raise ConfigEntryAuthFailed.
    """
    async def _do() -> Tuple[int, str, Dict[str, str]]:
        async with session.request(method, url, headers=headers, json=json_payload, data=data) as resp:
            txt = await resp.text()
            return resp.status, txt, dict(resp.headers)

    status, txt, hdrs = await _do()

    if _looks_like_logout(status, txt):
        if retry_auth_once:
            _LOGGER.warning(
                "Auth looks invalid for %s %s (status=%s, body=%r) -> trying CSRF refresh once",
                method, url, status, (txt or "")[:200],
            )
            # CSRF refresh will also validate session; if invalid it will raise ConfigEntryAuthFailed
            await fetch_csrf(hass, session, entry_id)
            status, txt, hdrs = await _do()

        if _looks_like_logout(status, txt):
            raise ConfigEntryAuthFailed(
                f"Session invalid while fetching data: {status} {txt!r}"
            )

    return status, txt, hdrs


async def _request_json(
    hass: HomeAssistant,
    session: aiohttp.ClientSession,
    entry_id: str,
    method: str,
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    json_payload: Any = None,
    data: Any = None,
    retry_auth_once: bool = True,
) -> Any:
    status, txt, _ = await _request_text(
        hass,
        session,
        entry_id,
        method,
        url,
        headers=headers,
        json_payload=json_payload,
        data=data,
        retry_auth_once=retry_auth_once,
    )

    if status != 200:
        raise aiohttp.ClientResponseError(
            request_info=None, history=(), status=status, message=txt
        )

    try:
        return json.loads(txt)
    except Exception as e:
        raise aiohttp.ClientResponseError(
            request_info=None,
            history=(),
            status=status,
            message=f"JSON decode failed: {e} (body={txt!r})",
        )


# ---------------------------
# API calls
# ---------------------------

async def get_devices(hass: HomeAssistant, session: aiohttp.ClientSession, entry_id: str) -> list:
    """Retrieve list of devices from SmartThings Find."""
    csrf = await _ensure_csrf(hass, session, entry_id)
    url = f"{URL_DEVICE_LIST}?_csrf={csrf}"

    js = await _request_json(
        hass,
        session,
        entry_id,
        "POST",
        url,
        headers={"Accept": "application/json"},
        data={},
        retry_auth_once=True,
    )

    devices_data = js.get("deviceList", []) if isinstance(js, dict) else []
    devices = []
    for device in devices_data:
        device["modelName"] = html.unescape(html.unescape(device.get("modelName", "")))

        identifier = (DOMAIN, device["dvceID"])
        ha_dev = device_registry.async_get(hass).async_get_device({identifier})
        if ha_dev and ha_dev.disabled:
            _LOGGER.debug(
                "Ignoring disabled device: %r (disabled_by=%s)",
                device["modelName"],
                ha_dev.disabled_by,
            )
            continue

        ha_dev_info = DeviceInfo(
            identifiers={identifier},
            manufacturer="Samsung",
            name=device["modelName"],
            model=device.get("modelID"),
            configuration_url=STF_BASE + "/",
        )

        devices.append({"data": device, "ha_dev_info": ha_dev_info})
        _LOGGER.debug("Adding device: %s", device["modelName"])

    return devices


async def get_device_location(
    hass: HomeAssistant,
    session: aiohttp.ClientSession,
    dev_data: dict,
    entry_id: str,
) -> Optional[dict]:
    """Request update (optional) and fetch current device location."""
    dev_id = dev_data["dvceID"]
    dev_name = dev_data.get("modelName", dev_id)

    csrf = await _ensure_csrf(hass, session, entry_id)

    set_last_payload = {"dvceId": dev_id, "removeDevice": []}
    update_payload = {
        "dvceId": dev_id,
        "operation": "CHECK_CONNECTION_WITH_LOCATION",
        "usrId": dev_data["usrId"],
    }

    active = (
        (dev_data.get("deviceTypeCode") == "TAG" and hass.data[DOMAIN][entry_id][CONF_ACTIVE_MODE_SMARTTAGS])
        or (dev_data.get("deviceTypeCode") != "TAG" and hass.data[DOMAIN][entry_id][CONF_ACTIVE_MODE_OTHERS])
    )

    # 1) Optional active update request
    if active:
        await _request_text(
            hass,
            session,
            entry_id,
            "POST",
            f"{URL_REQUEST_LOC_UPDATE}?_csrf={csrf}",
            json_payload=update_payload,
            retry_auth_once=True,
        )

    # 2) Fetch operations/location
    status, txt, _ = await _request_text(
        hass,
        session,
        entry_id,
        "POST",
        f"{URL_SET_LAST_DEVICE}?_csrf={csrf}",
        headers={"Accept": "application/json"},
        json_payload=set_last_payload,
        retry_auth_once=True,
    )

    if status != 200:
        _LOGGER.error("[%s] Failed to fetch device data (%s): %r", dev_name, status, txt[:250])
        return None

    try:
        data = json.loads(txt)
    except Exception:
        _LOGGER.error("[%s] Non-JSON response: %r", dev_name, txt[:250])
        return None

    res = {
        "dev_name": dev_name,
        "dev_id": dev_id,
        "update_success": True,
        "location_found": False,
        "used_op": None,
        "used_loc": None,
        "ops": [],
    }

    if "operation" not in data or not data["operation"]:
        res["update_success"] = False
        return res

    res["ops"] = data["operation"]

    used_op = None
    used_loc = {"latitude": None, "longitude": None, "gps_accuracy": None, "gps_date": None}

    for op in data["operation"]:
        if op.get("oprnType") not in ["LOCATION", "LASTLOC", "OFFLINE_LOC"]:
            continue

        # Plain lat/lon
        if "latitude" in op:
            utc_date = None
            if "extra" in op and "gpsUtcDt" in op["extra"]:
                utc_date = parse_stf_date(op["extra"]["gpsUtcDt"])
            else:
                continue

            if used_loc["gps_date"] and used_loc["gps_date"] >= utc_date:
                continue

            loc_found = False
            if "latitude" in op:
                used_loc["latitude"] = float(op["latitude"])
                loc_found = True
            if "longitude" in op:
                used_loc["longitude"] = float(op["longitude"])
                loc_found = True

            if loc_found:
                res["location_found"] = True

            used_loc["gps_accuracy"] = calc_gps_accuracy(
                op.get("horizontalUncertainty"), op.get("verticalUncertainty")
            )
            used_loc["gps_date"] = utc_date
            used_op = op
            continue

        # Enc location (may be encrypted)
        if "encLocation" in op:
            loc = op["encLocation"]
            if isinstance(loc, dict) and loc.get("encrypted"):
                continue
            if not isinstance(loc, dict) or "gpsUtcDt" not in loc:
                continue

            utc_date = parse_stf_date(loc["gpsUtcDt"])
            if used_loc["gps_date"] and used_loc["gps_date"] >= utc_date:
                continue

            loc_found = False
            if "latitude" in loc:
                used_loc["latitude"] = float(loc["latitude"])
                loc_found = True
            if "longitude" in loc:
                used_loc["longitude"] = float(loc["longitude"])
                loc_found = True

            if loc_found:
                res["location_found"] = True

            used_loc["gps_accuracy"] = calc_gps_accuracy(
                loc.get("horizontalUncertainty"), loc.get("verticalUncertainty")
            )
            used_loc["gps_date"] = utc_date
            used_op = op

    if used_op:
        res["used_op"] = used_op
        res["used_loc"] = used_loc

    return res


# ---------------------------
# Utility functions
# ---------------------------

def calc_gps_accuracy(hu: float, vu: float) -> Optional[float]:
    try:
        return round((float(hu) ** 2 + float(vu) ** 2) ** 0.5, 1)
    except Exception:
        return None


def get_sub_location(ops: list, subDeviceName: str) -> tuple:
    if not ops or not subDeviceName or len(ops) < 1:
        return {}, {}
    for op in ops:
        if subDeviceName in op.get("encLocation", {}):
            loc = op["encLocation"][subDeviceName]
            sub_loc = {
                "latitude": float(loc["latitude"]),
                "longitude": float(loc["longitude"]),
                "gps_accuracy": calc_gps_accuracy(
                    loc.get("horizontalUncertainty"), loc.get("verticalUncertainty")
                ),
                "gps_date": parse_stf_date(loc["gpsUtcDt"]),
            }
            return op, sub_loc
    return {}, {}


def parse_stf_date(datestr: str) -> datetime:
    return datetime.strptime(datestr, "%Y%m%d%H%M%S").replace(tzinfo=pytz.UTC)


def get_battery_level(dev_name: str, ops: list) -> Optional[int]:
    for op in ops:
        if op.get("oprnType") == "CHECK_CONNECTION" and "battery" in op:
            batt_raw = op["battery"]
            batt = BATTERY_LEVELS.get(batt_raw, None)
            if batt is None:
                try:
                    batt = int(batt_raw)
                except ValueError:
                    _LOGGER.warning("[%s]: invalid battery level: %r", dev_name, batt_raw)
                    batt = None
            return batt
    return None


def gen_qr_code_base64(data: str) -> str:
    # Kept for backward compatibility; QR auth is deprecated in this fork
    try:
        import qrcode  # type: ignore
    except Exception:
        return ""
    qr = qrcode.QRCode()
    qr.add_data(data)
    img = qr.make_image(fill_color="black", back_color="white")
    buffer = BytesIO()
    img.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("utf-8")
