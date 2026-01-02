from __future__ import annotations

import logging
from datetime import timedelta
from time import monotonic
from typing import Any

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN
from .utils import fetch_csrf, get_device_location

_LOGGER = logging.getLogger(__name__)

DEFAULT_UPDATE_INTERVAL = timedelta(minutes=2)
AUTH_FAILED_UPDATE_INTERVAL = timedelta(hours=6)
AUTH_LOG_THROTTLE_SEC = 60 * 30  # 30분


class SmartThingsFindCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """SmartThings Find data coordinator."""

    def __init__(
        self,
        hass: HomeAssistant,
        session: aiohttp.ClientSession,
        devices: list[dict[str, Any]],
        entry_id: str | None = None,
        update_interval: timedelta = DEFAULT_UPDATE_INTERVAL,
    ) -> None:
        self.hass = hass
        self.session = session
        self.devices = devices
        self.entry_id = entry_id or ""

        self._normal_update_interval = update_interval
        self._auth_failed = False
        self._reauth_triggered = False
        self._last_auth_log_mono = 0.0

        super().__init__(
            hass=hass,
            logger=_LOGGER,
            name=DOMAIN,
            update_interval=update_interval,
        )

    def _should_log_auth(self) -> bool:
        now = monotonic()
        if now - self._last_auth_log_mono >= AUTH_LOG_THROTTLE_SEC:
            self._last_auth_log_mono = now
            return True
        return False

    def _mark_auth_failed(self, err: Exception) -> None:
        if not self._auth_failed:
            self._auth_failed = True
            self.async_set_update_interval(AUTH_FAILED_UPDATE_INTERVAL)

        if self._should_log_auth():
            _LOGGER.warning("keepalive failed (reauth likely needed): %s", err)
        else:
            _LOGGER.debug("keepalive/auth still failing: %s", err)

    def _mark_auth_ok(self) -> None:
        if self._auth_failed:
            _LOGGER.info("Authentication recovered; restoring normal polling interval.")
            self._auth_failed = False
            self._reauth_triggered = False
            self.async_set_update_interval(self._normal_update_interval)

    async def async_keepalive(self) -> None:
        try:
            await fetch_csrf(self.hass, self.session, self.entry_id)
            self._mark_auth_ok()
        except ConfigEntryAuthFailed as err:
            self._mark_auth_failed(err)
            if not self._reauth_triggered:
                self._reauth_triggered = True
                self.hass.async_create_task(self.async_request_refresh())
        except Exception as err:
            _LOGGER.debug("keepalive transient error: %s", err)

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            tags: dict[str, Any] = {}
            _LOGGER.debug("Updating SmartThings Find locations for %d devices", len(self.devices))

            for device in self.devices:
                dev_data = device.get("data") or device
                tag_data = await get_device_location(self.hass, self.session, dev_data, self.entry_id)
                dvce_id = dev_data.get("dvceID") or dev_data.get("dvceId")
                if dvce_id:
                    tags[str(dvce_id)] = tag_data

            self._mark_auth_ok()
            return tags

        except ConfigEntryAuthFailed as err:
            self._mark_auth_failed(err)

            if not self._reauth_triggered:
                self._reauth_triggered = True
                raise

            raise UpdateFailed("Authentication required (reauth in progress)") from err

        except Exception as err:
            raise UpdateFailed(f"Error fetching {DOMAIN} data: {err}") from err
