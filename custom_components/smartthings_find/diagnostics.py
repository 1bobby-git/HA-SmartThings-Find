"""Diagnostics for SmartThings Find without credentials or personal data.

The download describes the session and polling state needed to investigate
authentication problems. It is built from an allow-list: cookie values, the
CSRF token, response bodies, account and device identifiers, device names and
coordinates are never read into the result. Home Assistant adds the integration
manifest, including the installed version, to every download.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    AUTH_METHOD_COOKIE,
    CONF_ACTIVE_MODE_OTHERS,
    CONF_ACTIVE_MODE_OTHERS_DEFAULT,
    CONF_ACTIVE_MODE_SMARTTAGS,
    CONF_ACTIVE_MODE_SMARTTAGS_DEFAULT,
    CONF_AUTH_METHOD,
    CONF_KEEPALIVE_INTERVAL,
    CONF_KEEPALIVE_INTERVAL_DEFAULT,
    CONF_UPDATE_INTERVAL,
    CONF_UPDATE_INTERVAL_DEFAULT,
    DATA_COORDINATOR,
    DATA_DEVICES,
    DATA_SESSION,
    DIAG_COOKIE_STORE,
    DIAG_KEEPALIVE,
    DIAG_LAST_CHK_LOGIN,
    DOMAIN,
)
from .utils import COOKIE_NAME_RE, STF_BASE, cookie_scope

_MISSING = object()


def _record(value: Any) -> dict[str, Any] | None:
    """Copy a diagnostics record; its writers store only safe scalar values."""
    return dict(value) if isinstance(value, dict) else None


def _cookie_summary(session: Any) -> dict[str, Any] | None:
    jar = getattr(session, "cookie_jar", None)
    if jar is None:
        return None

    try:
        morsels = list(jar)
    except TypeError:
        morsels = []
    cookies = []
    for morsel in morsels:
        name = getattr(morsel, "key", "")
        if not isinstance(name, str) or not COOKIE_NAME_RE.match(name):
            continue
        domain, path = cookie_scope(morsel)
        cookies.append({"name": name, "domain": domain, "path": path})
    cookies.sort(key=lambda item: (item["domain"], item["path"], item["name"]))

    try:
        sent = sorted(
            name for name in jar.filter_cookies(STF_BASE) if COOKIE_NAME_RE.match(name)
        )
    except Exception:  # noqa: BLE001
        sent = []

    return {"count": len(cookies), "sent_to_site": sent, "scopes": cookies}


def _auth_failure(runtime: dict[str, Any], now: datetime) -> dict[str, Any] | None:
    started = runtime.get("auth_failure_started")
    if not isinstance(started, datetime):
        return None
    return {
        "started": started.isoformat(),
        "elapsed_seconds": int((now - started).total_seconds()),
    }


def _coordinator_summary(coordinator: Any) -> dict[str, Any] | None:
    if coordinator is None:
        return None

    interval = getattr(coordinator, "update_interval", None)
    error = getattr(coordinator, "last_exception", None)
    data = getattr(coordinator, "data", None)
    results = (
        [value for value in data.values() if isinstance(value, dict)]
        if isinstance(data, dict)
        else []
    )
    # Home Assistant keeps the next poll handle here; None means polling stopped.
    unsub_refresh = getattr(coordinator, "_unsub_refresh", _MISSING)

    return {
        "last_update_success": bool(getattr(coordinator, "last_update_success", False)),
        "last_error": type(error).__name__ if error is not None else None,
        "update_interval_seconds": (
            interval.total_seconds() if hasattr(interval, "total_seconds") else None
        ),
        "next_poll_scheduled": (
            None if unsub_refresh is _MISSING else unsub_refresh is not None
        ),
        "devices_reported": len(results),
        "devices_with_location": sum(1 for value in results if value.get("location_found")),
    }


def _device_summary(devices: Any) -> dict[str, Any]:
    if not isinstance(devices, list):
        devices = []
    kinds = Counter(
        str((device.get("data") or {}).get("deviceTypeCode") or "unknown")
        for device in devices
        if isinstance(device, dict)
    )
    return {"count": sum(kinds.values()), "by_type": dict(sorted(kinds.items()))}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> dict[str, Any]:
    """Return redacted diagnostics for one SmartThings Find entry."""
    runtime = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if not isinstance(runtime, dict):
        runtime = {}
    options = entry.options or {}
    session = runtime.get(DATA_SESSION)
    state = getattr(entry, "state", None)

    return {
        "entry_state": getattr(state, "value", None if state is None else str(state)),
        "auth_method": str(entry.data.get(CONF_AUTH_METHOD) or AUTH_METHOD_COOKIE),
        "options": {
            "update_interval": options.get(CONF_UPDATE_INTERVAL, CONF_UPDATE_INTERVAL_DEFAULT),
            "keepalive_interval": options.get(
                CONF_KEEPALIVE_INTERVAL, CONF_KEEPALIVE_INTERVAL_DEFAULT
            ),
            "active_mode_smarttags": bool(
                options.get(CONF_ACTIVE_MODE_SMARTTAGS, CONF_ACTIVE_MODE_SMARTTAGS_DEFAULT)
            ),
            "active_mode_others": bool(
                options.get(CONF_ACTIVE_MODE_OTHERS, CONF_ACTIVE_MODE_OTHERS_DEFAULT)
            ),
        },
        "session": {
            "active": session is not None,
            "csrf_cached": bool(runtime.get("_csrf")),
            "cookie_saving_paused_by_flow": bool(runtime.get("_auth_reconfiguring")),
            "cookies": _cookie_summary(session),
        },
        "cookie_store": _record(runtime.get(DIAG_COOKIE_STORE)),
        "last_chk_login": _record(runtime.get(DIAG_LAST_CHK_LOGIN)),
        "keepalive": _record(runtime.get(DIAG_KEEPALIVE)),
        "auth_failure": _auth_failure(runtime, datetime.now(tz=timezone.utc)),
        "coordinator": _coordinator_summary(runtime.get(DATA_COORDINATOR)),
        "devices": _device_summary(runtime.get(DATA_DEVICES)),
    }
