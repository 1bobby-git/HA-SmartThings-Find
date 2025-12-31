from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .utils import fetch_csrf

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up SmartThings Find button entities.

    Currently exposes only a "Ring" button per device.
    """
    devices = hass.data[DOMAIN][entry.entry_id]["devices"]
    entities: list[ButtonEntity] = []

    for device in devices:
        entities.append(RingButton(hass, entry.entry_id, device))

    async_add_entities(entities)


class RingButton(ButtonEntity):
    """Button entity to make a SmartThings Find device ring."""

    # ✅ 버튼은 기본 MDI 아이콘 사용 (스싱파인더 아이콘 제거)
    _attr_icon = "mdi:volume-high"

    def __init__(self, hass: HomeAssistant, entry_id: str, device: dict) -> None:
        self.hass = hass
        self._entry_id = entry_id

        data = device["data"]
        self.device = data

        dvce_id = data.get("dvceID")
        model_name = data.get("modelName", "SmartThings Find Device")

        self._attr_unique_id = f"stf_ring_button_{dvce_id}"
        self._attr_name = f"{model_name} Ring"

        # Device picture if available
        icons = data.get("icons") or {}
        colored_icon = icons.get("coloredIcon")
        if colored_icon:
            self._attr_entity_picture = colored_icon

        # DeviceInfo prepared by integration
        self._attr_device_info = device.get("ha_dev_info")

    async def async_press(self) -> None:
        """Handle the button press."""
        entry_data = self.hass.data[DOMAIN].get(self._entry_id, {})
        session = entry_data.get("session")
        csrf_token = entry_data.get("_csrf")

        if session is None:
            _LOGGER.error("No session found for entry_id=%s", self._entry_id)
            return

        if not csrf_token:
            _LOGGER.debug("No CSRF token cached; attempting to fetch a new one.")
            await fetch_csrf(self.hass, session, self._entry_id)
            csrf_token = self.hass.data[DOMAIN][self._entry_id].get("_csrf")

        if not csrf_token:
            _LOGGER.error("Failed to obtain CSRF token for entry_id=%s", self._entry_id)
            return

        ring_payload = {
            "dvceId": self.device.get("dvceID"),
            "operation": "RING",
            "usrId": self.device.get("usrId"),
            "status": "start",
            "lockMessage": "Home Assistant is ringing your device!",
        }

        url = f"https://smartthingsfind.samsung.com/dm/addOperation.do?_csrf={csrf_token}"

        try:
            async with session.post(url, json=ring_payload) as response:
                _LOGGER.debug("Ring request HTTP status: %s", response.status)

                if response.status == 200:
                    _LOGGER.info("Successfully rang device: %s", self.device.get("modelName"))
                    _LOGGER.debug("Ring response: %s", await response.text())
                    return

                _LOGGER.warning(
                    "Failed to ring device %s (HTTP %s). Refreshing CSRF.",
                    self.device.get("modelName"),
                    response.status,
                )
                await fetch_csrf(self.hass, session, self._entry_id)

        except Exception as err:
            _LOGGER.exception(
                "Exception occurred while ringing '%s': %s",
                self.device.get("modelName"),
                err,
            )
