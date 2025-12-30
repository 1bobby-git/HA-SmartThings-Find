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
