from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DATA_COORDINATOR,
    DATA_DEVICES,
    DATA_SESSION,
    DOMAIN,
    LOCATION_POLL_DELAYS,
    OP_CHECK_CONNECTION_WITH_LOCATION,
    OP_RING,
    REFRESH_DELAY_IMMEDIATE,
    REFRESH_DELAY_SHORT,
)
from .session_store import persist_cookie_to_store
from .utils import (
    auth_failure_is_persistent,
    clear_auth_failure,
    fetch_csrf,
    retry_auth_operation,
    send_operation,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up ring and location refresh buttons."""
    devices = hass.data[DOMAIN][entry.entry_id][DATA_DEVICES]

    entities: list[ButtonEntity] = []
    for device in devices:
        entities.extend(
            (
                RingStartButton(hass, entry.entry_id, device),
                RingStopButton(hass, entry.entry_id, device),
                UpdateLocationButton(hass, entry.entry_id, device),
            )
        )

    async_add_entities(entities)


class _STFOperationButton(ButtonEntity):
    """Common helper for authenticated SmartThings Find operations."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        device: dict[str, Any],
    ) -> None:
        self.hass = hass
        self._entry_id = entry_id
        self.device = device["data"]
        self._dvce_id = str(self.device.get("dvceID") or "")
        self._usr_id = self.device.get("usrId")
        self._attr_device_info = device.get("ha_dev_info")

    def _entry(self) -> ConfigEntry | None:
        return self.hass.config_entries.async_get_entry(self._entry_id)

    def _start_reauth(self) -> None:
        entry = self._entry()
        if entry:
            entry.async_start_reauth(self.hass)

    def _create_background_task(
        self,
        coroutine: Coroutine[Any, Any, Any],
        name: str,
    ) -> None:
        entry = self._entry()
        create_entry_task = getattr(entry, "async_create_background_task", None)
        if callable(create_entry_task):
            create_entry_task(self.hass, coroutine, name)
            return
        self.hass.async_create_task(coroutine)

    async def _get_session_and_csrf(self):
        entry_data = self.hass.data[DOMAIN].get(self._entry_id, {})
        session = entry_data.get(DATA_SESSION) or entry_data.get("session")
        csrf_token = entry_data.get("_csrf")

        if session is None:
            _LOGGER.error("No session found for entry_id=%s", self._entry_id)
            return None, None

        if not csrf_token:
            try:
                await fetch_csrf(self.hass, session, self._entry_id)
            except ConfigEntryAuthFailed:
                if auth_failure_is_persistent(self.hass, self._entry_id):
                    self._start_reauth()
                return None, None
            clear_auth_failure(self.hass, self._entry_id)
            csrf_token = self.hass.data[DOMAIN][self._entry_id].get("_csrf")

        return session, csrf_token

    async def _post_operation(
        self,
        operation: str,
        extra: dict[str, Any] | None = None,
    ) -> bool:
        session, csrf_token = await self._get_session_and_csrf()
        if session is None or not csrf_token:
            return False

        payload: dict[str, Any] = {
            "dvceId": self._dvce_id,
            "operation": operation,
            "usrId": self._usr_id,
        }
        if extra:
            payload.update(extra)

        try:
            await retry_auth_operation(
                lambda: send_operation(
                    self.hass,
                    session,
                    self._entry_id,
                    payload,
                ),
            )
            clear_auth_failure(self.hass, self._entry_id)
        except ConfigEntryAuthFailed:
            if auth_failure_is_persistent(self.hass, self._entry_id):
                _LOGGER.warning(
                    "Operation %s requires renewed authentication",
                    operation,
                )
                self._start_reauth()
            else:
                _LOGGER.warning(
                    "Operation %s hit a temporary authentication rejection",
                    operation,
                )
            return False
        except Exception as err:  # noqa: BLE001
            _LOGGER.exception("SmartThings Find operation %s failed: %s", operation, err)
            return False

        entry = self._entry()
        if entry:
            try:
                await persist_cookie_to_store(self.hass, entry, session)
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("Cookie persistence after operation failed: %s", err)
        return True

    async def _kick_refresh(self) -> None:
        """Refresh immediately and schedule two bounded follow-up checks."""
        coordinator = self.hass.data[DOMAIN][self._entry_id].get(DATA_COORDINATOR)
        if coordinator is None:
            return

        try:
            await coordinator.async_request_refresh()
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Immediate coordinator refresh failed: %s", err)
            return

        async def _delayed_refresh(delay_s: int) -> None:
            try:
                await asyncio.sleep(delay_s)
                await coordinator.async_request_refresh()
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("Delayed refresh (%ss) failed: %s", delay_s, err)

        for delay in (REFRESH_DELAY_IMMEDIATE, REFRESH_DELAY_SHORT):
            self._create_background_task(
                _delayed_refresh(delay),
                f"smartthings_find_refresh_{self._dvce_id}_{delay}",
            )

    def _raise_command_failed(self, action: str) -> None:
        raise HomeAssistantError(
            f"SmartThings Find {action} failed; check authentication and connectivity"
        )


class RingStartButton(_STFOperationButton):
    _attr_icon = "mdi:volume-high"

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        device: dict[str, Any],
    ) -> None:
        super().__init__(hass, entry_id, device)
        model_name = self.device.get("modelName", "SmartThings Find Device")
        self._attr_unique_id = f"stf_ring_start_{self._dvce_id}"
        self._attr_name = f"{model_name} Ring"

    async def async_press(self) -> None:
        if not await self._post_operation(
            OP_RING,
            {
                "status": "start",
                "lockMessage": "Home Assistant is ringing your device!",
            },
        ):
            self._raise_command_failed("ring start")
        await self._kick_refresh()


class RingStopButton(_STFOperationButton):
    _attr_icon = "mdi:volume-mute"

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        device: dict[str, Any],
    ) -> None:
        super().__init__(hass, entry_id, device)
        model_name = self.device.get("modelName", "SmartThings Find Device")
        self._attr_unique_id = f"stf_ring_stop_{self._dvce_id}"
        self._attr_name = f"{model_name} Stop Ring"

    async def async_press(self) -> None:
        if not await self._post_operation(OP_RING, {"status": "stop"}):
            self._raise_command_failed("ring stop")
        await self._kick_refresh()


class UpdateLocationButton(_STFOperationButton):
    _attr_icon = "mdi:refresh"

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        device: dict[str, Any],
    ) -> None:
        super().__init__(hass, entry_id, device)
        model_name = self.device.get("modelName", "SmartThings Find Device")
        self._attr_unique_id = f"stf_update_location_{self._dvce_id}"
        self._attr_name = f"{model_name} Update Location"

    def _get_current_server_gps_date(self):
        coordinator = self.hass.data[DOMAIN][self._entry_id].get(DATA_COORDINATOR)
        if coordinator is None or not coordinator.data:
            return None
        result = coordinator.data.get(self._dvce_id)
        location = (result or {}).get("used_loc") or {}
        return location.get("gps_date")

    async def async_press(self) -> None:
        coordinator = self.hass.data[DOMAIN][self._entry_id].get(DATA_COORDINATOR)
        old_gps_date = self._get_current_server_gps_date()
        if coordinator is not None:
            coordinator.mark_pending_last_update(self._dvce_id, old_gps_date)

        if not await self._post_operation(OP_CHECK_CONNECTION_WITH_LOCATION):
            if coordinator is not None:
                mark_failed = getattr(coordinator, "mark_last_update_failed", None)
                if callable(mark_failed):
                    mark_failed(self._dvce_id)
                else:
                    coordinator.mark_last_update_timeout(self._dvce_id)
            self._raise_command_failed("location update")

        await self._kick_refresh()
        if coordinator is not None:
            self._create_background_task(
                self._poll_server_last_update(coordinator),
                f"smartthings_find_location_poll_{self._dvce_id}",
            )

    async def _poll_server_last_update(self, coordinator) -> None:
        """Poll until the authoritative server timestamp changes or times out."""
        for delay_s in LOCATION_POLL_DELAYS:
            try:
                await asyncio.sleep(delay_s)
            except asyncio.CancelledError:
                raise

            try:
                if coordinator.get_pending_last_update(self._dvce_id) is None:
                    return
            except Exception:  # noqa: BLE001
                pass

            try:
                await coordinator.async_request_refresh()
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("Poll refresh failed (%ss): %s", delay_s, err)

        try:
            coordinator.mark_last_update_timeout(self._dvce_id)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("mark_last_update_timeout failed: %s", err)
