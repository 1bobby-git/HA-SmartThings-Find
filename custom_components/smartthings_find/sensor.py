from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
from homeassistant.const import PERCENTAGE, UnitOfLength
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .utils import get_battery_level
from . import SmartThingsFindCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: SmartThingsFindCoordinator = data["coordinator"]
    devices = data["devices"]

    entities = []
    for d in devices:
        dev = d["data"]
        dvce_id = dev["dvceID"]
        entities.append(SmartThingsFindBatterySensor(coordinator, entry.entry_id, dvce_id))
        entities.append(SmartThingsFindAccuracySensor(coordinator, entry.entry_id, dvce_id))
        entities.append(SmartThingsFindLastSeenSensor(coordinator, entry.entry_id, dvce_id))
    async_add_entities(entities)


class _Base(CoordinatorEntity, SensorEntity):
    def __init__(self, coordinator: SmartThingsFindCoordinator, entry_id: str, dvce_id: str) -> None:
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._dvce_id = dvce_id

    @property
    def device_info(self):
        d = self.coordinator.devices_by_id.get(self._dvce_id)
        return d["ha_dev_info"] if d else None

    def _dev_name(self) -> str:
        d = self.coordinator.devices_by_id.get(self._dvce_id)
        return (d["data"].get("modelName") if d else None) or f"STF {self._dvce_id}"

    def _loc(self) -> dict:
        return (self.coordinator.data or {}).get(self._dvce_id) or {}


class SmartThingsFindBatterySensor(_Base):
    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_{self._dvce_id}_battery"

    @property
    def name(self) -> str:
        return f"{self._dev_name()} Battery"

    @property
    def native_unit_of_measurement(self) -> str:
        return PERCENTAGE

    @property
    def native_value(self) -> int | None:
        loc = self._loc()
        ops = loc.get("ops") or []
        return get_battery_level(self._dev_name(), ops)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        loc = self._loc()
        return {
            "update_success": loc.get("update_success"),
            "location_found": loc.get("location_found"),
        }


class SmartThingsFindAccuracySensor(_Base):
    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_{self._dvce_id}_accuracy"

    @property
    def name(self) -> str:
        return f"{self._dev_name()} GPS Accuracy"

    @property
    def native_unit_of_measurement(self) -> str:
        return UnitOfLength.METERS

    @property
    def native_value(self) -> float | None:
        loc = self._loc()
        used = loc.get("used_loc") or {}
        return used.get("gps_accuracy")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        loc = self._loc()
        used = loc.get("used_loc") or {}
        dt = used.get("gps_date")
        return {
            "gps_date": dt.isoformat() if dt else None,
            "update_success": loc.get("update_success"),
        }


class SmartThingsFindLastSeenSensor(_Base):
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_{self._dvce_id}_last_seen"

    @property
    def name(self) -> str:
        return f"{self._dev_name()} Last Seen"

    @property
    def native_value(self):
        loc = self._loc()
        used = loc.get("used_loc") or {}
        return used.get("gps_date")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        loc = self._loc()
        used = loc.get("used_loc") or {}
        return {
            "latitude": used.get("latitude"),
            "longitude": used.get("longitude"),
            "gps_accuracy": used.get("gps_accuracy"),
            "update_success": loc.get("update_success"),
            "location_found": loc.get("location_found"),
        }
