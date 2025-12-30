import logging

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .utils import get_battery_level

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    devices = hass.data[DOMAIN][entry.entry_id]["devices"]
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    entities = [DeviceBatterySensor(hass, coordinator, device) for device in devices]
    async_add_entities(entities)


class DeviceBatterySensor(SensorEntity):
    def __init__(self, hass: HomeAssistant, coordinator, device):
        self.coordinator = coordinator
        self._attr_unique_id = f"stf_device_battery_{device['data']['dvceID']}"
        self._attr_name = f"{device['data']['modelName']} Battery"
        self.hass = hass
        self.device = device["data"]
        self.device_id = device["data"]["dvceID"]
        self._attr_device_info = device["ha_dev_info"]
        self._attr_device_class = SensorDeviceClass.BATTERY
        self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def available(self) -> bool:
        data = self.coordinator.data.get(self.device_id, {}) or {}
        return bool(data) and bool(data.get("update_success"))

    @property
    def unit_of_measurement(self) -> str:
        return "%"

    @property
    def native_value(self):
        ops = (self.coordinator.data.get(self.device_id, {}) or {}).get("ops", [])
        return get_battery_level(self.name, ops)
