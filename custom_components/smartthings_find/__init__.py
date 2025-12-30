from __future__ import annotations

from datetime import timedelta
import logging
import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.const import Platform
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.exceptions import ConfigEntryAuthFailed

from .const import (
    DOMAIN,
    CONF_JSESSIONID,  # legacy key name (kept)
    CONF_ACTIVE_MODE_OTHERS,
    CONF_ACTIVE_MODE_OTHERS_DEFAULT,
    CONF_ACTIVE_MODE_SMARTTAGS,
    CONF_ACTIVE_MODE_SMARTTAGS_DEFAULT,
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

PLATFORMS: list[Platform] = [Platform.DEVICE_TRACKER, Platform.BUTTON, Platform.SENSOR]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN].setdefault(entry.entry_id, {})

    # We store cookie header in CONF_JSESSIONID (legacy key) to avoid breaking existing installs
    cookie_header = entry.data.get(CONF_JSESSIONID, "")
    cookies = parse_cookie_header(cookie_header)

    # Per-entry session so we can close it cleanly on unload (prevents unclosed session warnings)
    session = async_create_clientsession(
        hass,
        cookie_jar=aiohttp.CookieJar(unsafe=True),
    )
    apply_cookies_to_session(session, cookies)

    active_smarttags = entry.options.get(
        CONF_ACTIVE_MODE_SMARTTAGS, CONF_ACTIVE_MODE_SMARTTAGS_DEFAULT
    )
    active_others = entry.options.get(
        CONF_ACTIVE_MODE_OTHERS, CONF_ACTIVE_MODE_OTHERS_DEFAULT
    )

    hass.data[DOMAIN][entry.entry_id].update(
        {
            CONF_ACTIVE_MODE_SMARTTAGS: active_smarttags,
            CONF_ACTIVE_MODE_OTHERS: active_others,
            "session": session,
        }
    )

    # Validate auth and fetch csrf (this will raise ConfigEntryAuthFailed if invalid)
    await fetch_csrf(hass, session, entry.entry_id)

    devices = await get_devices(hass, session, entry.entry_id)

    update_interval = entry.options.get(CONF_UPDATE_INTERVAL, CONF_UPDATE_INTERVAL_DEFAULT)
    coordinator = SmartThingsFindCoordinator(hass, entry, session, devices, update_interval)

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

    # Close per-entry session
    if data and (sess := data.get("session")):
        try:
            await sess.close()
        except Exception:
            pass

    return unload_ok


class SmartThingsFindCoordinator(DataUpdateCoordinator[dict]):
    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        session: aiohttp.ClientSession,
        devices: list,
        update_interval_s: int,
    ) -> None:
        self.session = session
        self.devices = devices
        self.entry = entry
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=update_interval_s),
        )

    async def _async_update_data(self) -> dict:
        try:
            tags: dict = {}
            for device in self.devices:
                dev_data = device["data"]
                tags[dev_data["dvceID"]] = await get_device_location(
                    self.hass, self.session, dev_data, self.entry.entry_id
                )
            return tags

        except ConfigEntryAuthFailed:
            # This triggers Home Assistant reauth flow
            raise

        except Exception as err:
            raise UpdateFailed(f"Error fetching SmartThings Find data: {err}") from err
