from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from homeassistant.const import PERCENTAGE
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


def _battery_svg_name(level: int) -> str:
    """SmartThings Find 웹 UI 배터리 SVG 파일명 추정 매핑."""
    if level >= 96:
        return "96_100.svg"
    if level >= 76:
        return "76_95.svg"
    if level >= 56:
        return "56_75.svg"
    if level >= 26:
        return "26_55.svg"
    if level >= 6:
        return "6_25.svg"
    return "0_5.svg"


def _battery_entity_picture(level: int | None) -> str | None:
    if level is None:
        return None
    try:
        lvl = int(level)
    except Exception:
        return None
    lvl = max(0, min(100, lvl))
    svg = _battery_svg_name(lvl)
    return f"https://smartthingsfind.samsung.com/img/device_card/battery/{svg}"


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
    def entity_picture(self) -> str | None:
        """배터리 상태에 따라 STF 배터리 SVG를 동적으로 적용."""
        return _battery_entity_picture(self.native_value)


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
