from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

import aiohttp
import pytz

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN
from .utils import fetch_csrf, get_device_location

_LOGGER = logging.getLogger(__name__)


class SmartThingsFindCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """
    Coordinator with backward compatible signatures.

    Preferred (current):
        SmartThingsFindCoordinator(hass=hass, entry=entry, session=session, devices=devices, update_interval_s=120)

    Legacy:
        SmartThingsFindCoordinator(hass, session, devices, 60)
        SmartThingsFindCoordinator(hass, entry, session, devices, 60)
    """

    def __init__(  # noqa: PLR0913
        self,
        hass: HomeAssistant,
        *args: Any,
        entry: ConfigEntry | None = None,
        session: aiohttp.ClientSession | None = None,
        devices: list[dict[str, Any]] | None = None,
        update_interval_s: int | None = None,
        keepalive_interval_s: int = 300,
        **kwargs: Any,
    ) -> None:
        # --- Parse legacy positional args for compatibility ---
        # Possible legacy patterns:
        #   (session, devices, update_interval)
        #   (entry, session, devices, update_interval)
        if args:
            if isinstance(args[0], ConfigEntry):
                entry = args[0]
                if len(args) > 1:
                    session = args[1]
                if len(args) > 2:
                    devices = args[2]
                if len(args) > 3 and update_interval_s is None:
                    update_interval_s = int(args[3])
            else:
                # session first
                session = args[0]
                if len(args) > 1:
                    devices = args[1]
                if len(args) > 2 and update_interval_s is None:
                    update_interval_s = int(args[2])

        # kwargs fallbacks
        if entry is None:
            entry = kwargs.get("config_entry") or kwargs.get("entry")  # type: ignore[assignment]
        if session is None:
            session = kwargs.get("session")
        if devices is None:
            devices = kwargs.get("devices")

        if update_interval_s is None:
            update_interval_s = 60

        if session is None:
            raise ValueError("SmartThingsFindCoordinator requires an aiohttp session")

        self.hass = hass
        self.entry = entry
        self.entry_id: str | None = entry.entry_id if entry else None
        self.session = session
        self.devices = devices or []

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=max(30, int(update_interval_s))),
        )

        self._keepalive_cancel = async_track_time_interval(
            hass,
            self._async_keepalive,
            timedelta(seconds=max(120, int(keepalive_interval_s))),
        )

    async def async_shutdown(self) -> None:
        """Cancel keepalive timer."""
        if self._keepalive_cancel:
            self._keepalive_cancel()
            self._keepalive_cancel = None

    async def _async_keepalive(self, _now) -> None:
        """Periodic CSRF refresh; never raise here."""
        try:
            # entry_id can be None in some edge cases; fetch_csrf supports it (won't store)
            await fetch_csrf(self.hass, self.session, self.entry_id)
            _LOGGER.debug("keepalive: csrf refreshed")
        except ConfigEntryAuthFailed as e:
            _LOGGER.warning("keepalive failed (reauth likely needed): %s", e)
        except Exception as e:  # noqa: BLE001
            _LOGGER.debug("keepalive unexpected error: %s", e)

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data for all devices.

        Stability policy:
        - If one device fails, do NOT fail the whole coordinator.
        - Keep previous data for that device if available.
        - Only raise if authentication truly failed (ConfigEntryAuthFailed).
        """
        if not self.entry_id:
            raise UpdateFailed("Missing entry_id for SmartThings Find coordinator")

        results: dict[str, Any] = {}
        previous: dict[str, Any] = self.data or {}

        for dev in self.devices:
            dev_data = dev.get("data") or {}
            dvce_id = str(dev_data.get("dvceID"))
            dev_name = dev_data.get("modelName") or dvce_id

            try:
                tag_data = await get_device_location(self.hass, self.session, dev_data, self.entry_id)
                if tag_data is None:
                    raise RuntimeError("get_device_location returned None")

                results[dvce_id] = tag_data

            except ConfigEntryAuthFailed:
                raise

            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("Device update failed (%s / %s): %s", dev_name, dvce_id, err, exc_info=True)

                prev = previous.get(dvce_id)
                if isinstance(prev, dict):
                    results[dvce_id] = prev
                    continue

                now = datetime.now(tz=pytz.UTC)
                results[dvce_id] = {
                    "dev_name": dev_name,
                    "dev_id": dvce_id,
                    "update_success": False,
                    "location_found": False,
                    "used_op": None,
                    "used_loc": {"latitude": None, "longitude": None, "gps_accuracy": None, "gps_date": None},
                    "ops": [],
                    "battery_level": None,
                    "fetched_at": now,
                    "last_update": now,
                }

        return results
