from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed

from .const import (
    DOMAIN,
    PLATFORMS,
    CONF_COOKIE_INPUT,
    CONF_UPDATE_INTERVAL,
    CONF_UPDATE_INTERVAL_DEFAULT,
    CONF_ACTIVE_MODE_SMARTTAGS,
    CONF_ACTIVE_MODE_SMARTTAGS_DEFAULT,
    CONF_ACTIVE_MODE_OTHERS,
    CONF_ACTIVE_MODE_OTHERS_DEFAULT,
)
from .coordinator import SmartThingsFindCoordinator
from .utils import fetch_csrf, get_devices, make_session

_LOGGER = logging.getLogger(__name__)

async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    hass.data.setdefault(DOMAIN, {})
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})

    cookie_header = entry.data.get(CONF_COOKIE_INPUT)
    if not cookie_header:
        raise ConfigEntryAuthFailed("missing_cookie")

    session = await make_session(hass, cookie_header)
    csrf = await fetch_csrf(hass, session)

    active_tags = entry.options.get(CONF_ACTIVE_MODE_SMARTTAGS, CONF_ACTIVE_MODE_SMARTTAGS_DEFAULT)
    active_others = entry.options.get(CONF_ACTIVE_MODE_OTHERS, CONF_ACTIVE_MODE_OTHERS_DEFAULT)
    update_interval = entry.options.get(CONF_UPDATE_INTERVAL, CONF_UPDATE_INTERVAL_DEFAULT)

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

    # first refresh must succeed
    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = {
        "session": session,
        "csrf": csrf,
        "devices": devices,
        "coordinator": coordinator,
        "active_tags": active_tags,
        "active_others": active_others,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok
