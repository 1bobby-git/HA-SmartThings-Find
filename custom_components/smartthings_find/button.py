from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    DATA_SESSION,
    DATA_COORDINATOR,
    DATA_DEVICES,
    OP_RING,
    OP_CHECK_CONNECTION_WITH_LOCATION,
)
from .utils import fetch_csrf

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up minimal SmartThings Find buttons (0.3.23).

    Keep only:
    - Ring
    - Stop Ring
    - Update Location
    """
    data = hass.data[DOMAIN][entry.entry_id]
    devices = data[DATA_DEVICES]

    entities: list[ButtonEntity] = []
    for device in devices:
        entities.append(RingStartButton(hass, entry.entry_id, device))
        entities.append(RingStopButton(hass, entry.entry_id, device))
        entities.append(UpdateLocationButton(hass, entry.entry_id, device))

    async_add_entities(entities)


class _STFOperationButton(ButtonEntity):
    """Common helper to call STF addOperation.do with CSRF handling.

    NOTE: Do NOT set entity_picture here.
    Only device_tracker should show STF icon.
    """

    def __init__(self, hass: HomeAssistant, entry_id: str, device: dict[str, Any]) -> None:
        self.hass = hass
        self._entry_id = entry_id

        data = device["data"]
        self.device = data

        self._dvce_id = data.get("dvceID")
        self._usr_id = data.get("usrId")

        self._attr_device_info = device.get("ha_dev_info")

    async def _get_session_and_csrf(self):
        entry_data = self.hass.data[DOMAIN].get(self._entry_id, {})
        session = entry_data.get(DATA_SESSION) or entry_data.get("session")
        csrf_token = entry_data.get("_csrf")

        if session is None:
            _LOGGER.error("No session found for entry_id=%s", self._entry_id)
            return None, None

        if not csrf_token:
            _LOGGER.debug("No CSRF token cached; attempting to fetch a new one.")
            await fetch_csrf(self.hass, session, self._entry_id)
            csrf_token = self.hass.data[DOMAIN][self._entry_id].get("_csrf")

        return session, csrf_token

    async def _post_operation(self, operation: str, extra: dict[str, Any] | None = None) -> bool:
        session, csrf_token = await self._get_session_and_csrf()
        if session is None or not csrf_token:
            _LOGGER.error("Missing session/csrf for entry_id=%s", self._entry_id)
            return False

        payload: dict[str, Any] = {
            "dvceId": self._dvce_id,
            "operation": operation,
            "usrId": self._usr_id,
        }
        if extra:
            payload.update(extra)

        url = f"https://smartthingsfind.samsung.com/dm/addOperation.do?_csrf={csrf_token}"

        try:
            async with session.post(url, json=payload) as response:
                txt = await response.text()
                _LOGGER.debug("Operation=%s HTTP=%s payload=%s resp=%s", operation, response.status, payload, txt)

                if response.status == 200:
                    return True

                _LOGGER.warning("Operation %s failed (HTTP %s). Refreshing CSRF.", operation, response.status)
                await fetch_csrf(self.hass, session, self._entry_id)
                return False

        except Exception as err:
            _LOGGER.exception("Exception while posting operation %s: %s", operation, err)
            return False

    async def _kick_refresh(self) -> None:
        """Force coordinator refresh so sensors (Last update) reflect latest server state."""
        try:
            coordinator = self.hass.data[DOMAIN][self._entry_id].get(DATA_COORDINATOR)
            if coordinator is None:
                return

            # 1) immediately request refresh
            await coordinator.async_request_refresh()

            # 2) and again after a short delay (server updates are async)
            await asyncio.sleep(2)
            await coordinator.async_request_refresh()
        except Exception as err:
            _LOGGER.debug("Coordinator refresh kick failed: %s", err)


class RingStartButton(_STFOperationButton):
    _attr_icon = "mdi:volume-high"

    def __init__(self, hass: HomeAssistant, entry_id: str, device: dict[str, Any]) -> None:
        super().__init__(hass, entry_id, device)
        model_name = self.device.get("modelName", "SmartThings Find Device")
        self._attr_unique_id = f"stf_ring_start_{self._dvce_id}"
        self._attr_name = f"{model_name} Ring"

    async def async_press(self) -> None:
        await self._post_operation(
            OP_RING,
            {
                "status": "start",
                "lockMessage": "Home Assistant is ringing your device!",
            },
        )
        await self._kick_refresh()


class RingStopButton(_STFOperationButton):
    _attr_icon = "mdi:volume-mute"

    def __init__(self, hass: HomeAssistant, entry_id: str, device: dict[str, Any]) -> None:
        super().__init__(hass, entry_id, device)
        model_name = self.device.get("modelName", "SmartThings Find Device")
        self._attr_unique_id = f"stf_ring_stop_{self._dvce_id}"
        self._attr_name = f"{model_name} Stop Ring"

    async def async_press(self) -> None:
        # Best-effort: OP_RING + status=stop
        await self._post_operation(OP_RING, {"status": "stop"})
        await self._kick_refresh()


class UpdateLocationButton(_STFOperationButton):
    _attr_icon = "mdi:refresh"

    def __init__(self, hass: HomeAssistant, entry_id: str, device: dict[str, Any]) -> None:
        super().__init__(hass, entry_id, device)
        model_name = self.device.get("modelName", "SmartThings Find Device")
        self._attr_unique_id = f"stf_update_location_{self._dvce_id}"
        self._attr_name = f"{model_name} Update Location"

    async def async_press(self) -> None:
        await self._post_operation(OP_CHECK_CONNECTION_WITH_LOCATION)
        await self._kick_refresh()
