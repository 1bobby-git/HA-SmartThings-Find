from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed

from .const import (
    DOMAIN,
    PLATFORMS,
    CONF_COOKIE_INPUT,
    CONF_JSESSIONID,
    CONF_UPDATE_INTERVAL,
    CONF_UPDATE_INTERVAL_DEFAULT,
    CONF_ACTIVE_MODE_SMARTTAGS,
    CONF_ACTIVE_MODE_SMARTTAGS_DEFAULT,
    CONF_ACTIVE_MODE_OTHERS,
    CONF_ACTIVE_MODE_OTHERS_DEFAULT,
)
from .coordinator import SmartThingsFindCoordinator
from .utils import fetch_csrf, get_devices, make_session_with_cookie

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    hass.data.setdefault(DOMAIN, {})
    return True


def _get_cookie_from_entry(entry: ConfigEntry) -> str | None:
    cookie = entry.data.get(CONF_COOKIE_INPUT)
    if cookie:
        return cookie

    # legacy support
    cookie = entry.data.get(CONF_JSESSIONID) or entry.data.get("jsessionid")
    if cookie:
        return cookie

    return None


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate old data keys to new key."""
    data = dict(entry.data)

    if CONF_COOKIE_INPUT not in data:
        legacy = data.get(CONF_JSESSIONID) or data.get("jsessionid")
        if legacy:
            data[CONF_COOKIE_INPUT] = legacy

    if data != dict(entry.data):
        hass.config_entries.async_update_entry(entry, data=data)

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})

    cookie_header = _get_cookie_from_entry(entry)
    if not cookie_header:
        raise ConfigEntryAuthFailed("missing_cookie")

    session = await make_session_with_cookie(hass, cookie_header)

    active_tags = entry.options.get(CONF_ACTIVE_MODE_SMARTTAGS, CONF_ACTIVE_MODE_SMARTTAGS_DEFAULT)
    active_others = entry.options.get(CONF_ACTIVE_MODE_OTHERS, CONF_ACTIVE_MODE_OTHERS_DEFAULT)
    update_interval = entry.options.get(CONF_UPDATE_INTERVAL, CONF_UPDATE_INTERVAL_DEFAULT)

    csrf = await fetch_csrf(hass, session)
    devices = await get_devices(hass, session, csrf)

    coordinator = SmartThingsFindCoordinator(
        hass=hass,
        session=session,
        devices=devices,
        update_interval_s=update_interval,
        active_tags=active_tags,
        active_others=active_others,
    )
    coordinator.csrf = csrf

    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = {
        "session": session,
        "devices": devices,
        "coordinator": coordinator,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    data = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if data and "session" in data:
        try:
            await data["session"].close()
        except Exception:
            _LOGGER.debug("Session close failed", exc_info=True)

    return unload_ok
