from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from homeassistant.const import PERCENTAGE
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


def _battery_icon(level: int | None) -> str:
    if level is None:
        return "mdi:battery-unknown"
    try:
        v = int(level)
    except Exception:
        return "mdi:battery-unknown"

    v = max(0, min(100, v))
    if v <= 5:
        return "mdi:battery-alert"
    if v <= 10:
        return "mdi:battery-10"
    if v <= 20:
        return "mdi:battery-20"
    if v <= 30:
        return "mdi:battery-30"
    if v <= 40:
        return "mdi:battery-40"
    if v <= 50:
        return "mdi:battery-50"
    if v <= 60:
        return "mdi:battery-60"
    if v <= 70:
        return "mdi:battery-70"
    if v <= 80:
        return "mdi:battery-80"
    if v <= 90:
        return "mdi:battery-90"
    return "mdi:battery"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]
    devices = data["devices"]

    entities: list[SensorEntity] = []
    for dev in devices:
        entities.append(SmartThingsFindBatterySensor(coordinator, dev))
        entities.append(SmartThingsFindLastUpdateSensor(coordinator, dev))
    async_add_entities(entities)


class SmartThingsFindBatterySensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, dev: dict[str, Any]) -> None:
        super().__init__(coordinator)
        self.dev = dev
        self._dvce_id = dev["data"]["dvceID"]

        self._attr_unique_id = f"{self._dvce_id}_battery"
        self._attr_name = "Battery"
        self._attr_device_info = dev["ha_dev_info"]

    @property
    def native_value(self) -> int | None:
        res = self.coordinator.data.get(self._dvce_id) if self.coordinator.data else None
        return (res or {}).get("battery_level")

    @property
    def icon(self) -> str:
        # ✅ 요청: 배터리는 STF 방식(entity_picture) 말고 mdi 아이콘으로
        return _battery_icon(self.native_value)


class SmartThingsFindLastUpdateSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:clock-outline"

    def __init__(self, coordinator, dev: dict[str, Any]) -> None:
        super().__init__(coordinator)
        self.dev = dev
        self._dvce_id = dev["data"]["dvceID"]

        self._attr_unique_id = f"{self._dvce_id}_last_update"
        self._attr_name = "Last update"
        self._attr_device_info = dev["ha_dev_info"]

    @property
    def native_value(self) -> datetime | None:
        res = self.coordinator.data.get(self._dvce_id) if self.coordinator.data else None
        if not res:
            return None
        loc = res.get("used_loc") or {}
        return loc.get("gps_date") or res.get("fetched_at")
