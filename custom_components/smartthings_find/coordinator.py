from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .auth_manager import SmartThingsFindAuthManager
from .const import CONF_KEEPALIVE_INTERVAL_DEFAULT, KEEPALIVE_JITTER_RATIO
from .utils import auth_failure_is_persistent, clear_auth_failure

_LOGGER = logging.getLogger(__name__)


class SmartThingsFindCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Fetch locations and battery state for SmartThings Find devices."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        session,
        auth_manager: SmartThingsFindAuthManager,
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
        self.auth_manager = auth_manager
        self.devices = devices
        self._keepalive_interval_s = max(
            60,
            int(keepalive_interval_s or CONF_KEEPALIVE_INTERVAL_DEFAULT),
        )
        self._keepalive_task: asyncio.Task | None = None
        self._last_update_fetch: dict[str, dict[str, Any]] = {}
        self._last_update_fetch_result: dict[str, str] = {}

    async def async_config_entry_first_refresh(self) -> None:
        """Run the initial refresh and then start session keepalive."""
        await super().async_config_entry_first_refresh()
        self._start_keepalive()

    def _start_keepalive(self) -> None:
        """Run keepalive with bounded jitter to avoid an exact request cadence."""
        if self._keepalive_task is not None:
            self._keepalive_task.cancel()
            self._keepalive_task = None

        coroutine = self._keepalive_loop()
        create_entry_task = getattr(
            self.entry,
            "async_create_background_task",
            None,
        )
        if callable(create_entry_task):
            self._keepalive_task = create_entry_task(
                self.hass,
                coroutine,
                "smartthings_find_session_keepalive",
            )
        else:
            self._keepalive_task = self.hass.async_create_task(coroutine)

        _LOGGER.debug(
            "SmartThings Find keepalive scheduled around every %ss",
            self._keepalive_interval_s,
        )

    async def _keepalive_loop(self) -> None:
        while True:
            jitter = random.uniform(
                1.0 - KEEPALIVE_JITTER_RATIO,
                1.0 + KEEPALIVE_JITTER_RATIO,
            )
            delay = max(60.0, self._keepalive_interval_s * jitter)
            try:
                await asyncio.sleep(delay)
                await self._async_keepalive()
            except asyncio.CancelledError:
                raise

    async def _async_keepalive(self) -> None:
        """Ping SmartThings Find and rebuild an expired web session when possible."""
        try:
            await self.auth_manager.async_keepalive()
            clear_auth_failure(self.hass, self.entry.entry_id)
        except ConfigEntryAuthFailed as err:
            if auth_failure_is_persistent(self.hass, self.entry.entry_id):
                _LOGGER.warning(
                    "Authentication recovery failed continuously; starting reauth: %s",
                    type(err).__name__,
                )
                self.entry.async_start_reauth(self.hass)
            else:
                _LOGGER.warning(
                    "Authentication recovery is temporarily unavailable; keeping "
                    "the entry loaded for another retry"
                )
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("KeepAlive failed: %s", type(err).__name__)

    async def async_shutdown(self) -> None:
        """Stop keepalive and coordinator tasks."""
        task = self._keepalive_task
        self._keepalive_task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
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
            result = await self._async_update_data_once()
        except ConfigEntryAuthFailed as err:
            if auth_failure_is_persistent(self.hass, self.entry.entry_id):
                raise
            raise UpdateFailed(
                "SmartThings Find authentication recovery is temporarily unavailable"
            ) from err
        clear_auth_failure(self.hass, self.entry.entry_id)
        return result

    async def _async_update_data_once(self) -> dict[str, Any]:
        """Refresh devices while retaining last valid data on a partial failure."""
        try:
            results: dict[str, Any] = {}
            previous = self.data if isinstance(self.data, dict) else {}

            for device in self.devices:
                device_data = device.get("data") or {}
                device_id = device_data.get("dvceID")
                if not device_id:
                    continue
                key = str(device_id)

                result = await self.auth_manager.async_get_device_location(
                    device_data
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

            return results

        except ConfigEntryAuthFailed:
            raise
        except Exception as err:
            raise UpdateFailed(
                f"Failed to update SmartThings Find data: {err}"
            ) from err
