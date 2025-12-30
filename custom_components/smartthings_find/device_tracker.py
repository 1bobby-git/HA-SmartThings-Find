from __future__ import annotations

from typing import Any, Optional

from homeassistant.components.device_tracker import TrackerEntity, SourceType
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from . import SmartThingsFindCoordinator


def _get_colored_icon_url(dev_data: dict) -> Optional[str]:
    icons = dev_data.get("icons")
    if isinstance(icons, dict):
        url = icons.get("coloredIcon") or icons.get("icon") or icons.get("monoIcon")
        if isinstance(url, str) and url.startswith("http"):
            return url
    return None


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: SmartThingsFindCoordinator = data["coordinator"]
    devices = data["devices"]

    entities = []
    for d in devices:
        dev = d["data"]
        entities.append(SmartThingsFindDeviceTracker(coordinator, entry.entry_id, dev["dvceID"]))
    async_add_entities(entities)


class SmartThingsFindDeviceTracker(CoordinatorEntity, TrackerEntity):
    """GPS tracker entity for STF device."""

    def __init__(self, coordinator: SmartThingsFindCoordinator, entry_id: str, dvce_id: str) -> None:
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._dvce_id = dvce_id

        # ✅ 원본처럼 "그림 아이콘" 노출 (device icon URL)
        dev = self.coordinator.devices_by_id.get(self._dvce_id, {}).get("data", {})
        self._attr_entity_picture = _get_colored_icon_url(dev)

        # fallback mdi (그림 URL이 없을 때만)
        self._attr_icon = "mdi:map-search"

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_{self._dvce_id}_tracker"

    @property
    def name(self) -> str:
        d = self.coordinator.devices_by_id.get(self._dvce_id)
        return (d["data"].get("modelName") if d else None) or f"STF {self._dvce_id}"

    @property
    def device_info(self):
        d = self.coordinator.devices_by_id.get(self._dvce_id)
        return d["ha_dev_info"] if d else None

    @property
    def source_type(self) -> SourceType:
        return SourceType.GPS

    @property
    def latitude(self) -> float | None:
        loc = (self.coordinator.data or {}).get(self._dvce_id) or {}
        used = loc.get("used_loc") or {}
        return used.get("latitude")

    @property
    def longitude(self) -> float | None:
        loc = (self.coordinator.data or {}).get(self._dvce_id) or {}
        used = loc.get("used_loc") or {}
        return used.get("longitude")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        loc = (self.coordinator.data or {}).get(self._dvce_id) or {}
        used = loc.get("used_loc") or {}
        op = loc.get("used_op") or {}
        dt = used.get("gps_date")
        return {
            "gps_accuracy": used.get("gps_accuracy"),
            "gps_date": dt.isoformat() if dt else None,
            "location_found": loc.get("location_found"),
            "update_success": loc.get("update_success"),
            "used_oprnType": op.get("oprnType"),
        }
