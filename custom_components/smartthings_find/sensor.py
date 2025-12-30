from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from homeassistant.const import PERCENTAGE
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .utils import STF_BATTERY_PICTURE

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]
    devices = data["devices"]

    entities: list[SensorEntity] = []
    for dev in devices:
        entities.append(SmartThingsFindBatterySensor(coordinator, entry, dev))
        entities.append(SmartThingsFindLastUpdateSensor(coordinator, entry, dev))
    async_add_entities(entities)

class SmartThingsFindBatterySensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_picture = STF_BATTERY_PICTURE

    def __init__(self, coordinator, entry: ConfigEntry, dev: dict[str, Any]) -> None:
        super().__init__(coordinator)
        self.dev = dev
        dvce_id = dev["data"]["dvceID"]
        self._dvce_id = dvce_id

        self._attr_unique_id = f"{dvce_id}_battery"
        self._attr_name = "Battery"
        self._attr_device_info = dev["ha_dev_info"]

    @property
    def native_value(self) -> int | None:
        res = self.coordinator.data.get(self._dvce_id) if self.coordinator.data else None
        if not res:
            return None
        return res.get("battery_level")

class SmartThingsFindLastUpdateSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator, entry: ConfigEntry, dev: dict[str, Any]) -> None:
        super().__init__(coordinator)
        self.dev = dev
        dvce_id = dev["data"]["dvceID"]
        self._dvce_id = dvce_id

        self._attr_unique_id = f"{dvce_id}_last_update"
        self._attr_name = "Last update"
        self._attr_device_info = dev["ha_dev_info"]
        self._attr_icon = "mdi:clock-outline"

    @property
    def native_value(self) -> datetime | None:
        res = self.coordinator.data.get(self._dvce_id) if self.coordinator.data else None
        if not res:
            return None
        # gps_date가 있으면 그걸, 없으면 fetched_at
        loc = res.get("used_loc") or {}
        return loc.get("gps_date") or res.get("fetched_at")
