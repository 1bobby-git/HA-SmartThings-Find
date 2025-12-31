from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from homeassistant.const import PERCENTAGE
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


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

        # 1) STF 기준(ops 전체에서 가장 최신 timestamp)
        last_update = res.get("last_update")
        if isinstance(last_update, datetime):
            return last_update

        # 2) 위치 timestamp
        loc = res.get("used_loc") or {}
        gps_date = loc.get("gps_date")
        if isinstance(gps_date, datetime):
            return gps_date

        # 3) 폴백: fetch 시각
        fetched_at = res.get("fetched_at")
        return fetched_at if isinstance(fetched_at, datetime) else None
