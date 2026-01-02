from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.event import async_track_time_interval

from .const import (
    DOMAIN,
    CONF_COOKIE,
    CONF_UPDATE_INTERVAL,
    CONF_UPDATE_INTERVAL_DEFAULT,
    CONF_KEEPALIVE_INTERVAL,
    CONF_KEEPALIVE_INTERVAL_DEFAULT,
    CONF_ACTIVE_MODE_SMARTTAGS,
    CONF_ACTIVE_MODE_SMARTTAGS_DEFAULT,
    CONF_ACTIVE_MODE_OTHERS,
    CONF_ACTIVE_MODE_OTHERS_DEFAULT,
    CONF_ST_IDENTIFIER,
    DATA_SESSION,
    DATA_COORDINATOR,
    DATA_DEVICES,
    DATA_KEEPALIVE_UNSUB,
)
from .coordinator import SmartThingsFindCoordinator
from .utils import (
    parse_cookie_header,
    apply_cookies_to_session,
    make_session,
    fetch_csrf,
    get_devices,
    persist_cookie_to_entry,
    keepalive_ping,  # ✅ 추가
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.DEVICE_TRACKER, Platform.SENSOR, Platform.BUTTON]


async def async_setup(hass: HomeAssistant, _config) -> bool:
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN].setdefault(entry.entry_id, {})

    cookie_line = (entry.data.get(CONF_COOKIE) or "").strip()
    if not cookie_line:
        raise ConfigEntryAuthFailed("missing_cookie")

    cookies = parse_cookie_header(cookie_line)
    if not cookies:
        raise ConfigEntryAuthFailed("invalid_cookie")

    session = make_session(hass)
    apply_cookies_to_session(session, cookies)

    # options
    active_smarttags = entry.options.get(CONF_ACTIVE_MODE_SMARTTAGS, CONF_ACTIVE_MODE_SMARTTAGS_DEFAULT)
    active_others = entry.options.get(CONF_ACTIVE_MODE_OTHERS, CONF_ACTIVE_MODE_OTHERS_DEFAULT)
    st_identifier = entry.options.get(CONF_ST_IDENTIFIER)

    # ✅ FIX: 반드시 먼저 정의 (UnboundLocalError 방지)
    update_interval_s = entry.options.get(CONF_UPDATE_INTERVAL, CONF_UPDATE_INTERVAL_DEFAULT)
    try:
        update_interval_s = int(update_interval_s)
    except Exception:  # noqa: BLE001
        update_interval_s = int(CONF_UPDATE_INTERVAL_DEFAULT)

    # ✅ NEW: keepalive interval
    keepalive_interval_s = entry.options.get(CONF_KEEPALIVE_INTERVAL, CONF_KEEPALIVE_INTERVAL_DEFAULT)
    try:
        keepalive_interval_s = int(keepalive_interval_s)
    except Exception:  # noqa: BLE001
        keepalive_interval_s = int(CONF_KEEPALIVE_INTERVAL_DEFAULT)
    keepalive_interval_s = max(60, keepalive_interval_s)

    hass.data[DOMAIN][entry.entry_id].update(
        {
            CONF_ACTIVE_MODE_SMARTTAGS: bool(active_smarttags),
            CONF_ACTIVE_MODE_OTHERS: bool(active_others),
            CONF_ST_IDENTIFIER: st_identifier,
        }
    )

    try:
        # Validate session + store csrf
        await fetch_csrf(hass, session, entry.entry_id)

        # ✅ 추가(최소 변경): fetch_csrf 과정에서 쿠키가 갱신/회전되었을 수 있으므로 즉시 저장
        try:
            await persist_cookie_to_entry(hass, entry, session)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("cookie persist after fetch_csrf failed: %s", err)

        # Load devices
        devices = await get_devices(hass, session, entry.entry_id)

        # ✅ 추가(최소 변경): devices 조회에서도 Set-Cookie가 올 수 있어 한 번 더 저장
        try:
            await persist_cookie_to_entry(hass, entry, session)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("cookie persist after get_devices failed: %s", err)

        coordinator = SmartThingsFindCoordinator(
            hass=hass,
            entry=entry,
            session=session,
            devices=devices,
            update_interval_s=update_interval_s,
        )

        await coordinator.async_config_entry_first_refresh()

        hass.data[DOMAIN][entry.entry_id].update(
            {
                DATA_SESSION: session,
                DATA_COORDINATOR: coordinator,
                DATA_DEVICES: devices,
            }
        )

        # ✅ NEW: KeepAlive timer (세션/쿠키 유지)
        async def _keepalive(_now) -> None:
            try:
                await keepalive_ping(hass, session, entry.entry_id)
                # keepalive 중에도 쿠키 회전될 수 있으니 저장
                try:
                    await persist_cookie_to_entry(hass, entry, session)
                except Exception as err:  # noqa: BLE001
                    _LOGGER.debug("cookie persist after keepalive failed: %s", err)
            except ConfigEntryAuthFailed:
                # ✅ coordinator 밖에서 발생하는 auth fail은 entry.async_start_reauth(hass)로 유도 :contentReference[oaicite:3]{index=3}
                _LOGGER.debug("Keepalive detected expired session; starting reauth")
                entry.async_start_reauth(hass)
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("Keepalive failed (non-fatal): %s", err)

        unsub = async_track_time_interval(hass, _keepalive, timedelta(seconds=keepalive_interval_s))
        hass.data[DOMAIN][entry.entry_id][DATA_KEEPALIVE_UNSUB] = unsub

        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        return True

    except Exception:
        try:
            await session.close()
        except Exception:  # noqa: BLE001
            pass
        raise


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    data = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if data:
        # ✅ keepalive 해제
        unsub = data.get(DATA_KEEPALIVE_UNSUB)
        if unsub:
            try:
                unsub()
            except Exception:  # noqa: BLE001
                pass

        coordinator = data.get(DATA_COORDINATOR)
        if coordinator:
            await coordinator.async_shutdown()

        session = data.get(DATA_SESSION)
        if session:
            await session.close()

    return unload_ok
