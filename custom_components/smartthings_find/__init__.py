from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed

from .const import (
    DOMAIN,
    CONF_COOKIE,
    CONF_UPDATE_INTERVAL,
    CONF_UPDATE_INTERVAL_DEFAULT,
    CONF_ACTIVE_MODE_SMARTTAGS,
    CONF_ACTIVE_MODE_SMARTTAGS_DEFAULT,
    CONF_ACTIVE_MODE_OTHERS,
    CONF_ACTIVE_MODE_OTHERS_DEFAULT,
    CONF_ST_IDENTIFIER,
    DATA_SESSION,
    DATA_COORDINATOR,
    DATA_DEVICES,
    DATA_KEEPALIVE_CANCEL,
)
from .coordinator import SmartThingsFindCoordinator
from .utils import (
    parse_cookie_header,
    apply_cookies_to_session,
    make_session,
    fetch_csrf,
    get_devices,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.DEVICE_TRACKER, Platform.SENSOR, Platform.BUTTON]


async def async_setup(hass: HomeAssistant, _config) -> bool:
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN].setdefault(entry.entry_id, {})

    cookie_line = entry.data.get(CONF_COOKIE, "")
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

    hass.data[DOMAIN][entry.entry_id].update(
        {
            CONF_ACTIVE_MODE_SMARTTAGS: active_smarttags,
            CONF_ACTIVE_MODE_OTHERS: active_others,
            # ✅ 0.3.16 mapping option value (string)
            CONF_ST_IDENTIFIER: st_identifier,
        }
    )

    await fetch_csrf(hass, session, entry.entry_id)

    devices = await get_devices(hass, session, entry.entry_id)

    update_interval = entry.options.get(CONF_UPDATE_INTERVAL, CONF_UPDATE_INTERVAL_DEFAULT)

    coordinator = SmartThingsFindCoordinator(
        hass=hass,
        entry=entry,
        session=session,
        devices=devices,
        update_interval_s=update_interval,
    )
    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id].update(
        {
            DATA_SESSION: session,
            DATA_COORDINATOR: coordinator,
            DATA_DEVICES: devices,
        }
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    data = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if data:
        cancel = data.get(DATA_KEEPALIVE_CANCEL)
        if cancel:
            cancel()

        session = data.get(DATA_SESSION)
        if session:
            await session.close()

    return unload_ok
