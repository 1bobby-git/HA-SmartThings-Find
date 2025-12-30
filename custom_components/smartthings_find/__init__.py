from __future__ import annotations

import logging
from datetime import timedelta

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    DOMAIN,
    CONF_JSESSIONID,
    CONF_ACTIVE_MODE_SMARTTAGS,
    CONF_ACTIVE_MODE_SMARTTAGS_DEFAULT,
    CONF_ACTIVE_MODE_OTHERS,
    CONF_ACTIVE_MODE_OTHERS_DEFAULT,
    CONF_UPDATE_INTERVAL,
    CONF_UPDATE_INTERVAL_DEFAULT,
)
from .utils import (
    parse_cookie_header,
    apply_cookies_to_session,
    fetch_csrf,
    get_devices,
    get_device_location,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.DEVICE_TRACKER, Platform.SENSOR, Platform.BUTTON]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN].setdefault(entry.entry_id, {})

    cookie_header = entry.data.get(CONF_JSESSIONID, "") or ""
    cookies = parse_cookie_header(cookie_header)

    # per-entry session (prevents Unclosed session warnings)
    session = async_create_clientsession(
        hass,
        cookie_jar=aiohttp.CookieJar(unsafe=True),
    )
    apply_cookies_to_session(session, cookies)

    hass.data[DOMAIN][entry.entry_id].update(
        {
            "session": session,
            CONF_ACTIVE_MODE_SMARTTAGS: entry.options.get(
                CONF_ACTIVE_MODE_SMARTTAGS, CONF_ACTIVE_MODE_SMARTTAGS_DEFAULT
            ),
            CONF_ACTIVE_MODE_OTHERS: entry.options.get(
                CONF_ACTIVE_MODE_OTHERS, CONF_ACTIVE_MODE_OTHERS_DEFAULT
            ),
        }
    )

    # validate cookie session + store csrf
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
            "coordinator": coordinator,
            "devices": devices,
        }
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    data = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if data:
        sess = data.get("session")
        if sess:
            try:
                await sess.close()
            except Exception:
                pass

    return unload_ok


class SmartThingsFindCoordinator(DataUpdateCoordinator[dict]):
    """Fetch and store SmartThings Find data for all devices."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        session: aiohttp.ClientSession,
        devices: list,
        update_interval_s: int,
    ) -> None:
        self.entry = entry
        self.session = session

        # devices list elements: {"data": <dev>, "ha_dev_info": DeviceInfo}
        self.devices = devices
        self.devices_by_id = {d["data"]["dvceID"]: d for d in devices}

        super().__init__(
            hass=hass,
            logger=_LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=update_interval_s),
        )

    async def async_refresh_device(self, dvce_id: str) -> None:
        """Refresh just one device (used by button)."""
        if dvce_id not in self.devices_by_id:
            return

        dev_data = self.devices_by_id[dvce_id]["data"]
        loc = await get_device_location(self.hass, self.session, dev_data, self.entry.entry_id)

        new = dict(self.data or {})
        new[dvce_id] = loc
        self.async_set_updated_data(new)

    async def _async_update_data(self) -> dict:
        try:
            # Limit concurrency to avoid hammering STF
            sem = aiohttp.Semaphore(4)  # type: ignore[attr-defined]
        except Exception:
            sem = None

        async def _fetch_one(d: dict):
            dev = d["data"]
            dvce_id = dev["dvceID"]

            if sem:
                async with sem:
                    return dvce_id, await get_device_location(
                        self.hass, self.session, dev, self.entry.entry_id
                    )
            return dvce_id, await get_device_location(
                self.hass, self.session, dev, self.entry.entry_id
            )

        try:
            results = await aiohttp.helpers.asyncio.gather(  # type: ignore[attr-defined]
                *[_fetch_one(d) for d in self.devices], return_exceptions=True
            )
        except Exception:
            # fallback (no aiohttp helper)
            import asyncio
            results = await asyncio.gather(*[_fetch_one(d) for d in self.devices], return_exceptions=True)

        data: dict = {}
        for item in results:
            if isinstance(item, Exception):
                # keep going; errors are logged inside utils
                continue
            dvce_id, loc = item
            data[dvce_id] = loc

        return data
