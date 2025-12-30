from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN
from .utils import fetch_csrf, get_device_location

_LOGGER = logging.getLogger(__name__)

class SmartThingsFindCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    def __init__(
        self,
        hass: HomeAssistant,
        session: aiohttp.ClientSession,
        devices: list[dict[str, Any]],
        update_interval_s: int,
        active_tags: bool,
        active_others: bool,
    ) -> None:
        self.hass = hass
        self.session = session
        self.devices = devices
        self.active_tags = active_tags
        self.active_others = active_others
        self.csrf: str | None = None

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=update_interval_s),
        )

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            if not self.csrf:
                self.csrf = await fetch_csrf(self.hass, self.session)

            results: dict[str, Any] = {}
            for dev in self.devices:
                dev_data = dev["data"]
                res = await get_device_location(
                    self.hass,
                    self.session,
                    self.csrf,
                    dev_data,
                    self.active_tags,
                    self.active_others,
                )
                if res is not None:
                    results[dev_data["dvceID"]] = res
            return results

        except ConfigEntryAuthFailed:
            # HA가 reauth 유도하도록 그대로 올림
            raise
        except Exception as err:
            raise UpdateFailed(f"Error fetching {DOMAIN} data: {err}") from err
