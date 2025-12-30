import logging

from homeassistant.components.device_tracker.config_entry import TrackerEntity as DeviceTrackerEntity
from homeassistant.components.device_tracker.const import SourceType
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .utils import get_battery_level, get_sub_location

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    devices = hass.data[DOMAIN][entry.entry_id]["devices"]
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    entities = []
    for device in devices:
        if device["data"].get("subType") == "CANAL2":
            entities.append(SmartThingsDeviceTracker(hass, coordinator, device, "left"))
            entities.append(SmartThingsDeviceTracker(hass, coordinator, device, "right"))
        entities.append(SmartThingsDeviceTracker(hass, coordinator, device))

    async_add_entities(entities)


class SmartThingsDeviceTracker(DeviceTrackerEntity):
    def __init__(self, hass: HomeAssistant, coordinator, device, subDeviceName=None):
        self.coordinator = coordinator
        self.hass = hass
        self.device = device["data"]
        self.device_id = self.device["dvceID"]
        self.subDeviceName = subDeviceName

        suffix = f"_{subDeviceName}" if subDeviceName else ""
        self._attr_unique_id = f"stf_device_tracker_{self.device_id}{suffix}"
        self._attr_name = self.device["modelName"] + (
            f" {subDeviceName.capitalize()}" if subDeviceName else ""
        )
        self._attr_device_info = device["ha_dev_info"]

        if self.device.get("icons", {}).get("coloredIcon"):
            self._attr_entity_picture = self.device["icons"]["coloredIcon"]

        self.async_update = coordinator.async_add_listener(self.async_write_ha_state)

    def async_write_ha_state(self):
        if not self.enabled:
            return
        return super().async_write_ha_state()

    @property
    def available(self) -> bool:
        data = self.coordinator.data.get(self.device_id, {}) or {}
        return bool(data) and bool(data.get("update_success"))

    @property
    def source_type(self) -> str:
        return SourceType.GPS

    @property
    def latitude(self):
        data = self.coordinator.data.get(self.device_id, {}) or {}
        if not data:
            return None
        if not self.subDeviceName:
            return (data.get("used_loc") or {}).get("latitude") if data.get("location_found") else None
        _, loc = get_sub_location(data.get("ops", []), self.subDeviceName)
        return loc.get("latitude")

    @property
    def longitude(self):
        data = self.coordinator.data.get(self.device_id, {}) or {}
        if not data:
            return None
        if not self.subDeviceName:
            return (data.get("used_loc") or {}).get("longitude") if data.get("location_found") else None
        _, loc = get_sub_location(data.get("ops", []), self.subDeviceName)
        return loc.get("longitude")

    @property
    def location_accuracy(self):
        data = self.coordinator.data.get(self.device_id, {}) or {}
        if not data:
            return None
        if not self.subDeviceName:
            return (data.get("used_loc") or {}).get("gps_accuracy") if data.get("location_found") else None
        _, loc = get_sub_location(data.get("ops", []), self.subDeviceName)
        return loc.get("gps_accuracy")

    @property
    def battery_level(self):
        data = self.coordinator.data.get(self.device_id, {}) or {}
        if self.subDeviceName:
            return None
        return get_battery_level(self.name, data.get("ops", []))

    @property
    def extra_state_attributes(self):
        tag_data = self.coordinator.data.get(self.device_id, {}) or {}
        used_loc = tag_data.get("used_loc") or {}

        # keep attributes small to avoid recorder 16KB warning
        attrs = {
            "device_id": self.device_id,
            "device_type": self.device.get("deviceTypeCode"),
            "last_seen": used_loc.get("gps_date"),
            "gps_accuracy": used_loc.get("gps_accuracy"),
        }

        if self.subDeviceName:
            _, loc = get_sub_location(tag_data.get("ops", []), self.subDeviceName)
            attrs["subdevice"] = self.subDeviceName
            attrs["last_seen"] = loc.get("gps_date")
            attrs["gps_accuracy"] = loc.get("gps_accuracy")

        return attrs
