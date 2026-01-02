from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from homeassistant.const import PERCENTAGE
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, DATA_DEVICES, DATA_COORDINATOR


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
    coordinator = data[DATA_COORDINATOR]
    devices = data[DATA_DEVICES]

    # 버튼에서 기록, 센서에서 읽는 저장소
    data.setdefault("last_update_requests", {})

    entities: list[SensorEntity] = []
    for dev in devices:
        entities.append(SmartThingsFindBatterySensor(coordinator, entry.entry_id, dev))
        entities.append(SmartThingsFindLastUpdateSensor(coordinator, entry.entry_id, dev))
    async_add_entities(entities)


class SmartThingsFindBatterySensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, entry_id: str, dev: dict[str, Any]) -> None:
        super().__init__(coordinator)
        self._entry_id = entry_id
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
        return _battery_icon(self.native_value)


class SmartThingsFindLastUpdateSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:clock-outline"

    def __init__(self, coordinator, entry_id: str, dev: dict[str, Any]) -> None:
        super().__init__(coordinator)
        self._entry_id = entry_id
        self.dev = dev
        self._dvce_id = dev["data"]["dvceID"]

        self._attr_unique_id = f"{self._dvce_id}_last_update"
        self._attr_name = "Last update"
        self._attr_device_info = dev["ha_dev_info"]

    def _reqs(self) -> dict[str, Any]:
        entry_data = self.coordinator.hass.data.get(DOMAIN, {}).get(self._entry_id, {})
        reqs = entry_data.get("last_update_requests", {})
        return reqs if isinstance(reqs, dict) else {}

    def _clear_req(self) -> None:
        entry_data = self.coordinator.hass.data.get(DOMAIN, {}).get(self._entry_id, {})
        reqs = entry_data.get("last_update_requests", {})
        if isinstance(reqs, dict):
            reqs.pop(self._dvce_id, None)

    @property
    def native_value(self) -> datetime | None:
        # ✅ 서버의 마지막 업데이트 시간만 state로 사용
        res = self.coordinator.data.get(self._dvce_id) if self.coordinator.data else None
        if not res:
            return None
        loc = res.get("used_loc") or {}
        return loc.get("gps_date")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        req = self._reqs().get(self._dvce_id)

        res = self.coordinator.data.get(self._dvce_id) if self.coordinator.data else None
        loc = (res or {}).get("used_loc") or {}
        gps_date = loc.get("gps_date")
        fetched_at = (res or {}).get("fetched_at")

        attrs: dict[str, Any] = {
            "server_last_update": gps_date,
            "last_polled_at": fetched_at,
        }

        if isinstance(req, dict):
            requested_at = req.get("requested_at")
            prev_gps_date = req.get("prev_gps_date")

            if gps_date and prev_gps_date and gps_date != prev_gps_date:
                self._clear_req()
                attrs["update_location_status"] = "done"
            else:
                attrs["update_location_status"] = "waiting_server_timestamp"
                attrs["update_location_requested_at"] = requested_at
        else:
            attrs["update_location_status"] = "idle"

        return attrs
