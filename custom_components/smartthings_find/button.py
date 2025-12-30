import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .utils import fetch_csrf

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    devices = hass.data[DOMAIN][entry.entry_id]["devices"]
    entities = [RingButton(hass, device) for device in devices]
    async_add_entities(entities)


class RingButton(ButtonEntity):
    def __init__(self, hass: HomeAssistant, device):
        self._attr_unique_id = f"stf_ring_button_{device['data']['dvceID']}"
        self._attr_name = f"{device['data']['modelName']} Ring"
        self._attr_icon = "mdi:nfc-search-variant"
        if device["data"].get("icons", {}).get("coloredIcon"):
            self._attr_entity_picture = device["data"]["icons"]["coloredIcon"]
        self.device = device["data"]
        self._attr_device_info = device["ha_dev_info"]

    async def async_press(self) -> None:
        entry_id = self.registry_entry.config_entry_id
        session = self.hass.data[DOMAIN][entry_id]["session"]
        csrf_token = self.hass.data[DOMAIN][entry_id]["_csrf"]

        ring_payload = {
            "dvceId": self.device["dvceID"],
            "operation": "RING",
            "usrId": self.device["usrId"],
            "status": "start",
            "lockMessage": "Home Assistant is ringing your device!",
        }
        url = f"https://smartthingsfind.samsung.com/dm/addOperation.do?_csrf={csrf_token}"

        try:
            async with session.post(url, json=ring_payload) as response:
                if response.status == 200:
                    _LOGGER.info("Successfully rang device %s", self.device["modelName"])
                else:
                    await fetch_csrf(self.hass, session, entry_id)
        except Exception as e:
            _LOGGER.error("Exception occurred while ringing '%s': %s", self.device["modelName"], e)
