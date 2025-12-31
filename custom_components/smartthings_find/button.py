from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    OP_RING,
    OP_CHECK_CONNECTION_WITH_LOCATION,
    OP_LOCK,
    OP_TRACK,
    OP_ERASE,
    OP_EXTEND_BATTERY,
)
from .utils import fetch_csrf

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up SmartThings Find buttons.

    0.3.22.1 policy:
    - Always expose Ring (never disappear).
    - Expose other website actions as best-effort (even if some devices reject them).
    - Non-tag devices also get phone actions (best-effort).
    """
    devices = hass.data[DOMAIN][entry.entry_id]["devices"]
    entities: list[ButtonEntity] = []

    for device in devices:
        dev_data = device["data"]
        dev_type = dev_data.get("deviceTypeCode")  # "TAG" or others

        # ✅ Always add Ring start (stable)
        entities.append(RingStartButton(hass, entry.entry_id, device))

        # ✅ Stop ring (best-effort)
        entities.append(RingStopButton(hass, entry.entry_id, device))

        # ✅ Update location (best-effort)
        entities.append(UpdateLocationButton(hass, entry.entry_id, device))

        # ✅ Phone actions only for non-tags (best-effort)
        if dev_type != "TAG":
            entities.append(PhoneActionButton(hass, entry.entry_id, device, OP_LOCK, "Lost Mode", "mdi:lock-alert"))
            entities.append(
                PhoneActionButton(hass, entry.entry_id, device, OP_TRACK, "Track Location", "mdi:crosshairs-gps")
            )
            entities.append(
                PhoneActionButton(hass, entry.entry_id, device, OP_ERASE, "Erase Data", "mdi:trash-can-outline")
            )
            entities.append(
                PhoneActionButton(
                    hass, entry.entry_id, device, OP_EXTEND_BATTERY, "Extend Battery", "mdi:battery-plus-outline"
                )
            )

    async_add_entities(entities)


class _STFOperationButton(ButtonEntity):
    """Common helper to call STF addOperation.do with CSRF handling."""

    def __init__(self, hass: HomeAssistant, entry_id: str, device: dict[str, Any]) -> None:
        self.hass = hass
        self._entry_id = entry_id

        data = device["data"]
        self.device = data

        self._dvce_id = data.get("dvceID")
        self._usr_id = data.get("usrId")

        # DeviceInfo prepared by integration
        self._attr_device_info = device.get("ha_dev_info")

        # Picture if available (more reliable than icon in some HA views)
        icons = data.get("icons") or {}
        colored_icon = icons.get("coloredIcon") or icons.get("icon")
        if colored_icon:
            self._attr_entity_picture = colored_icon

    async def _get_session_and_csrf(self):
        entry_data = self.hass.data[DOMAIN].get(self._entry_id, {})
        session = entry_data.get("session")
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

                # refresh CSRF if failed (cookie/CSRF invalid may happen)
                _LOGGER.warning("Operation %s failed (HTTP %s). Refreshing CSRF.", operation, response.status)
                await fetch_csrf(self.hass, session, self._entry_id)
                return False

        except Exception as err:
            _LOGGER.exception("Exception while posting operation %s: %s", operation, err)
            return False


class RingStartButton(_STFOperationButton):
    """Ring start."""

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


class RingStopButton(_STFOperationButton):
    """Ring stop (best-effort)."""

    _attr_icon = "mdi:volume-mute"

    def __init__(self, hass: HomeAssistant, entry_id: str, device: dict[str, Any]) -> None:
        super().__init__(hass, entry_id, device)
        model_name = self.device.get("modelName", "SmartThings Find Device")

        self._attr_unique_id = f"stf_ring_stop_{self._dvce_id}"
        self._attr_name = f"{model_name} Stop Ring"

    async def async_press(self) -> None:
        # Best-effort: many devices accept RING + status=stop
        await self._post_operation(OP_RING, {"status": "stop"})


class UpdateLocationButton(_STFOperationButton):
    """Website action: Update location (best-effort)."""

    _attr_icon = "mdi:map-marker-radius"

    def __init__(self, hass: HomeAssistant, entry_id: str, device: dict[str, Any]) -> None:
        super().__init__(hass, entry_id, device)
        model_name = self.device.get("modelName", "SmartThings Find Device")

        self._attr_unique_id = f"stf_update_location_{self._dvce_id}"
        self._attr_name = f"{model_name} Update Location"

    async def async_press(self) -> None:
        await self._post_operation(OP_CHECK_CONNECTION_WITH_LOCATION)


class PhoneActionButton(_STFOperationButton):
    """Phone actions (best-effort)."""

    def __init__(self, hass: HomeAssistant, entry_id: str, device: dict[str, Any], op: str, label: str, icon: str):
        super().__init__(hass, entry_id, device)
        self._op = op
        self._attr_icon = icon

        model_name = self.device.get("modelName", "SmartThings Find Device")
        self._attr_unique_id = f"stf_op_{op.lower()}_{self._dvce_id}"
        self._attr_name = f"{model_name} {label}"

    async def async_press(self) -> None:
        extra: dict[str, Any] = {"status": "start"}
        if self._op == OP_LOCK:
            extra["lockMessage"] = "Enabled from Home Assistant"
        await self._post_operation(self._op, extra)
