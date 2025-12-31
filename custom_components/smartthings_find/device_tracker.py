from __future__ import annotations

from typing import Any

from homeassistant.components.device_tracker.config_entry import TrackerEntity
from homeassistant.components.device_tracker.const import SourceType
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]
    devices = data["devices"]

    entities: list[SmartThingsFindTracker] = []
    for dev in devices:
        entities.append(SmartThingsFindTracker(coordinator, dev))
    async_add_entities(entities)


class SmartThingsFindTracker(CoordinatorEntity, TrackerEntity):
    _attr_should_poll = False
    _attr_source_type = SourceType.GPS
    _attr_has_entity_name = True

    # ✅ 스싱파인더 아이콘은 device_tracker에 적용
    _attr_icon = "mdi:nfc-search-variant"

    def __init__(self, coordinator, dev: dict[str, Any]) -> None:
        super().__init__(coordinator)
        self.dev = dev
        self._dvce_id = dev["data"]["dvceID"]

        self._attr_unique_id = f"{self._dvce_id}_tracker"
        self._attr_name = None
        self._attr_device_info = dev["ha_dev_info"]

    @property
    def latitude(self) -> float | None:
        res = self.coordinator.data.get(self._dvce_id) if self.coordinator.data else None
        loc = (res or {}).get("used_loc") or {}
        return loc.get("latitude")

    @property
    def longitude(self) -> float | None:
        res = self.coordinator.data.get(self._dvce_id) if self.coordinator.data else None
        loc = (res or {}).get("used_loc") or {}
        return loc.get("longitude")

    @property
    def location_accuracy(self) -> int | None:
        res = self.coordinator.data.get(self._dvce_id) if self.coordinator.data else None
        loc = (res or {}).get("used_loc") or {}
        acc = loc.get("gps_accuracy")
        return int(acc) if acc is not None else None
