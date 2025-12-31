from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry

from .const import (
    DOMAIN,
    DATA_COORDINATOR,
    DATA_DEVICES,
    OP_LOCK,
    OP_ERASE,
    OP_TRACK,
    OP_EXTEND_BATTERY,
)
from .utils import ring_device, phone_action


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data[DATA_COORDINATOR]
    devices = data[DATA_DEVICES]

    ents = []
    for d in devices:
        dev_data = d["data"]
        ents.append(STFRingButton(coordinator, entry.entry_id, d))

        # non-tag extra actions (best-effort)
        if dev_data.get("deviceTypeCode") != "TAG":
            ents.append(STFPhoneActionButton(coordinator, entry.entry_id, d, OP_LOCK, "Lost Mode", "mdi:lock-alert"))
            ents.append(STFPhoneActionButton(coordinator, entry.entry_id, d, OP_TRACK, "Track Location", "mdi:crosshairs-gps"))
            ents.append(STFPhoneActionButton(coordinator, entry.entry_id, d, OP_ERASE, "Erase Data", "mdi:trash-can-outline"))
            ents.append(STFPhoneActionButton(coordinator, entry.entry_id, d, OP_EXTEND_BATTERY, "Extend Battery", "mdi:battery-plus-outline"))

    async_add_entities(ents)


class STFRingButton(ButtonEntity):
    def __init__(self, coordinator, entry_id: str, dev: dict):
        self.coordinator = coordinator
        self.entry_id = entry_id
        self.dev = dev
        self.dev_data = dev["data"]
        self._attr_device_info = dev["ha_dev_info"]

        dvce_id = str(self.dev_data.get("dvceID"))
        self._attr_unique_id = f"{dvce_id}_ring"
        self._attr_name = f"{self.dev_data.get('modelName')} Ring"
        self._attr_icon = "mdi:volume-high"

    async def async_press(self):
        await ring_device(self.coordinator.hass, self.coordinator.session, self.entry_id, self.dev_data, True)


class STFPhoneActionButton(ButtonEntity):
    def __init__(self, coordinator, entry_id: str, dev: dict, op: str, label: str, icon: str):
        self.coordinator = coordinator
        self.entry_id = entry_id
        self.dev = dev
        self.dev_data = dev["data"]
        self.op = op
        self._attr_device_info = dev["ha_dev_info"]

        dvce_id = str(self.dev_data.get("dvceID"))
        self._attr_unique_id = f"{dvce_id}_op_{op.lower()}"
        self._attr_name = f"{self.dev_data.get('modelName')} {label}"
        self._attr_icon = icon

    async def async_press(self):
        await phone_action(self.coordinator.hass, self.coordinator.session, self.entry_id, self.dev_data, self.op)
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

    NOTE:
    - This integration currently exposes only a "Ring" button.
    - Previous code attempted to import OP_LOCK/OP_UNLOCK, but those constants
      do not exist in const.py, causing ImportError and setup failure.
    """
    devices = hass.data[DOMAIN][entry.entry_id]["devices"]
    entities: list[ButtonEntity] = []

    for device in devices:
        entities.append(RingButton(hass, entry.entry_id, device))

    async_add_entities(entities)


class RingButton(ButtonEntity):
    """Button entity to make a SmartThings Find device ring."""

    _attr_icon = "mdi:nfc-search-variant"

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

                # If request failed, refresh CSRF (cookie may be expired / login invalid)
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
