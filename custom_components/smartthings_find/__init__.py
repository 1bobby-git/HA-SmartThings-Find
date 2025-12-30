from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    DOMAIN,
    CONF_COOKIES,
    CONF_ACTIVE_MODE_SMARTTAGS,
    CONF_ACTIVE_MODE_SMARTTAGS_DEFAULT,
    CONF_ACTIVE_MODE_OTHERS,
    CONF_ACTIVE_MODE_OTHERS_DEFAULT,
    CONF_UPDATE_INTERVAL,
    CONF_UPDATE_INTERVAL_DEFAULT,
)
from .utils import (
    create_stf_session,
    apply_cookies_to_session,
    get_cookies_from_session,
    fetch_csrf,
    get_devices,
    get_device_location,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.DEVICE_TRACKER, Platform.BUTTON, Platform.SENSOR]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data[DOMAIN].setdefault(entry.entry_id, {})

    session = await create_stf_session(hass)

    cookies: dict[str, str] = entry.data.get(CONF_COOKIES) or {}
    if not cookies:
        await session.close()
        raise ConfigEntryAuthFailed("Missing cookies - reauth required")

    cleaned = apply_cookies_to_session(session, cookies)
    if cleaned != cookies:
        _LOGGER.warning("Stored cookies had invalid keys; auto-cleaned and updating entry")
        hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_COOKIES: cleaned})

    active_smarttags = entry.options.get(CONF_ACTIVE_MODE_SMARTTAGS, CONF_ACTIVE_MODE_SMARTTAGS_DEFAULT)
    active_others = entry.options.get(CONF_ACTIVE_MODE_OTHERS, CONF_ACTIVE_MODE_OTHERS_DEFAULT)

    hass.data[DOMAIN][entry.entry_id].update(
        {
            "session": session,
            CONF_ACTIVE_MODE_SMARTTAGS: active_smarttags,
            CONF_ACTIVE_MODE_OTHERS: active_others,
        }
    )

    await fetch_csrf(hass, session, entry.entry_id)
    devices = await get_devices(hass, session, entry.entry_id)

    update_interval = entry.options.get(CONF_UPDATE_INTERVAL, CONF_UPDATE_INTERVAL_DEFAULT)
    coordinator = SmartThingsFindCoordinator(hass, session, devices, update_interval, entry.entry_id)
    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id].update({"devices": devices, "coordinator": coordinator})

    snapshot = get_cookies_from_session(session)
    if snapshot and snapshot != cleaned:
        hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_COOKIES: snapshot})

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_success = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_success:
        data = hass.data[DOMAIN].pop(entry.entry_id, {})
        session: aiohttp.ClientSession | None = data.get("session")
        if session:
            await session.close()
    return unload_success


class SmartThingsFindCoordinator(DataUpdateCoordinator):
    def __init__(
        self,
        hass: HomeAssistant,
        session: aiohttp.ClientSession,
        devices,
        update_interval: int,
        entry_id: str,
    ):
        self.session = session
        self.devices = devices
        self.entry_id = entry_id
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=update_interval),
        )

    async def _async_update_data(self):
        try:
            sem = asyncio.Semaphore(6)

            async def _one(device):
                async with sem:
                    dev_data = device["data"]
                    return dev_data["dvceID"], await get_device_location(
                        self.hass, self.session, dev_data, self.entry_id
                    )

            results = await asyncio.gather(*[_one(d) for d in self.devices], return_exceptions=True)

            tags = {}
            for r in results:
                if isinstance(r, Exception):
                    raise r
                dev_id, tag_data = r
                tags[dev_id] = tag_data

            return tags

        except ConfigEntryAuthFailed:
            raise
        except Exception as err:
            raise UpdateFailed(f"Error fetching data: {err}") from err
