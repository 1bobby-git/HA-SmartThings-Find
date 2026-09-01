from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import CONF_KEEPALIVE_INTERVAL_DEFAULT
from .session_store import persist_cookie_to_store
from .utils import (
    auth_failure_is_persistent,
    clear_auth_failure,
    get_device_location,
    keepalive_ping,
    retry_auth_operation,
)

_LOGGER = logging.getLogger(__name__)


class SmartThingsFindCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Fetch locations and battery state for SmartThings Find devices."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        session,
        devices: list[dict[str, Any]],
        update_interval_s: int,
        keepalive_interval_s: int = CONF_KEEPALIVE_INTERVAL_DEFAULT,
    ) -> None:
        super().__init__(
            hass=hass,
            logger=_LOGGER,
            name="smartthings_find",
            config_entry=entry,
            update_interval=timedelta(seconds=max(15, int(update_interval_s))),
        )
        self.entry = entry
        self.session = session
        self.devices = devices
        self._keepalive_interval_s = max(
            60,
            int(keepalive_interval_s or CONF_KEEPALIVE_INTERVAL_DEFAULT),
        )
        self._keepalive_unsub: Callable[[], None] | None = None
        self._last_update_fetch: dict[str, dict[str, Any]] = {}
        self._last_update_fetch_result: dict[str, str] = {}

    async def async_config_entry_first_refresh(self) -> None:
        """Run the initial refresh and then start session keepalive."""
        await super().async_config_entry_first_refresh()
        self._start_keepalive()

    def _start_keepalive(self) -> None:
        """Schedule periodic keepalive to reduce idle session expiry."""
        if self._keepalive_unsub is not None:
            self._keepalive_unsub()
            self._keepalive_unsub = None

        self._keepalive_unsub = async_track_time_interval(
            self.hass,
            self._async_keepalive,
            timedelta(seconds=self._keepalive_interval_s),
        )
        _LOGGER.debug(
            "SmartThings Find keepalive scheduled every %ss",
            self._keepalive_interval_s,
        )

    async def _async_keepalive(self, _now=None) -> None:
        """Ping SmartThings Find without mutating the config entry."""
        try:
            await retry_auth_operation(
                lambda: keepalive_ping(
                    self.hass,
                    self.session,
                    self.entry.entry_id,
                ),
            )
            clear_auth_failure(self.hass, self.entry.entry_id)
            await persist_cookie_to_store(self.hass, self.entry, self.session)
        except ConfigEntryAuthFailed as err:
            if auth_failure_is_persistent(self.hass, self.entry.entry_id):
                _LOGGER.warning(
                    "Authentication failed continuously; starting reauth: %s",
                    err,
                )
                self.entry.async_start_reauth(self.hass)
            else:
                _LOGGER.warning(
                    "Temporary authentication rejection; keeping session for retry: %s",
                    err,
                )
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("KeepAlive failed: %s", err)

    async def async_shutdown(self) -> None:
        """Stop keepalive and coordinator tasks."""
        if self._keepalive_unsub is not None:
            self._keepalive_unsub()
            self._keepalive_unsub = None
        await super().async_shutdown()

    def mark_pending_last_update(self, dvce_id: str, old_gps_date) -> None:
        """Mark one user-requested location refresh as pending."""
        self._last_update_fetch[dvce_id] = {
            "started": datetime.now(tz=timezone.utc),
            "attempts": 0,
            "old": old_gps_date,
        }
        self._last_update_fetch_result[dvce_id] = "fetching"

    def get_pending_last_update(self, dvce_id: str) -> dict[str, Any] | None:
        """Return pending metadata for one device."""
        return self._last_update_fetch.get(dvce_id)

    def mark_last_update_timeout(self, dvce_id: str) -> None:
        """Finish a pending location refresh as timed out."""
        self._last_update_fetch.pop(dvce_id, None)
        self._last_update_fetch_result[dvce_id] = "timeout"

    def mark_last_update_failed(self, dvce_id: str) -> None:
        """Finish a pending location refresh after command failure."""
        self._last_update_fetch.pop(dvce_id, None)
        self._last_update_fetch_result[dvce_id] = "failed"

    def _maybe_clear_pending_if_changed(self, dvce_id: str, new_gps_date) -> None:
        pending = self._last_update_fetch.get(dvce_id)
        if not pending:
            return
        pending["attempts"] = int(pending.get("attempts", 0)) + 1

        old = pending.get("old")
        if old is None:
            if new_gps_date is not None:
                self._last_update_fetch.pop(dvce_id, None)
                self._last_update_fetch_result[dvce_id] = "ok"
            return

        try:
            if new_gps_date and new_gps_date != old and new_gps_date > old:
                self._last_update_fetch.pop(dvce_id, None)
                self._last_update_fetch_result[dvce_id] = "ok"
        except (TypeError, ValueError):
            return

    async def _async_update_data(self) -> dict[str, Any]:
        """Keep a rejected session long enough to distinguish outage from expiry."""
        try:
            result = await retry_auth_operation(self._async_update_data_once)
        except ConfigEntryAuthFailed as err:
            if auth_failure_is_persistent(self.hass, self.entry.entry_id):
                raise
            raise UpdateFailed(
                "SmartThings Find temporarily rejected the saved session; retrying"
            ) from err
        clear_auth_failure(self.hass, self.entry.entry_id)
        return result

    async def _async_update_data_once(self) -> dict[str, Any]:
        """Refresh each device while retaining its last valid payload on a partial failure."""
        try:
            results: dict[str, Any] = {}
            previous = self.data if isinstance(self.data, dict) else {}

            for device in self.devices:
                device_data = device.get("data") or {}
                device_id = device_data.get("dvceID")
                if not device_id:
                    continue
                key = str(device_id)

                result = await get_device_location(
                    hass=self.hass,
                    session=self.session,
                    dev_data=device_data,
                    entry_id=self.entry.entry_id,
                )

                if result is None:
                    previous_value = previous.get(key)
                    results[key] = (
                        previous_value
                        if isinstance(previous_value, dict)
                        else {}
                    )
                    continue

                results[key] = result
                location = result.get("used_loc") or {}
                self._maybe_clear_pending_if_changed(
                    key,
                    location.get("gps_date"),
                )

            try:
                await persist_cookie_to_store(
                    self.hass,
                    self.entry,
                    self.session,
                )
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug(
                    "Cookie persistence after coordinator update failed: %s",
                    err,
                )

            return results

        except ConfigEntryAuthFailed:
            raise
        except Exception as err:
            raise UpdateFailed(
                f"Failed to update SmartThings Find data: {err}"
            ) from err
