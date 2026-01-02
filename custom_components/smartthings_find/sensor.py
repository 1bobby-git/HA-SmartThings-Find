from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from homeassistant.const import PERCENTAGE
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

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


def _to_dt(v: Any) -> datetime | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else dt_util.as_local(v)
    if isinstance(v, str):
        d = dt_util.parse_datetime(v)
        if d:
            return d if d.tzinfo else dt_util.as_local(d)
    return None


def _dt_key(v: Any) -> str | None:
    d = _to_dt(v)
    if d:
        try:
            return dt_util.as_utc(d).isoformat()
        except Exception:
            return d.isoformat()
    if isinstance(v, str) and v.strip():
        return v.strip()
    return None


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data[DATA_COORDINATOR]
    devices = data[DATA_DEVICES]

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
        # ✅ 서버 gps_date만 state로. 문자열이면 datetime으로 변환해서 UI 갱신 보장.
        res = self.coordinator.data.get(self._dvce_id) if self.coordinator.data else None
        if not res:
            return None
        loc = res.get("used_loc") or {}
        return _to_dt(loc.get("gps_date"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        req = self._reqs().get(self._dvce_id)

        res = self.coordinator.data.get(self._dvce_id) if self.coordinator.data else None
        loc = (res or {}).get("used_loc") or {}
        gps_date_raw = loc.get("gps_date")
        gps_key = _dt_key(gps_date_raw)
        fetched_at = (res or {}).get("fetched_at")

        attrs: dict[str, Any] = {
            "server_last_update_raw": gps_date_raw,
            "server_last_update_key": gps_key,
            "last_polled_at": fetched_at,
        }

        if isinstance(req, dict):
            requested_at = req.get("requested_at")
            prev_key = req.get("prev_gps_key")
            timeout_at = req.get("timeout_at")

            now = dt_util.utcnow()
            if isinstance(timeout_at, datetime) and now >= timeout_at:
                attrs["update_location_status"] = "timeout"
                attrs["update_location_requested_at"] = requested_at
                return attrs

            # gps_date가 실제로 변했으면 done
            if gps_key and prev_key and gps_key != prev_key:
                self._clear_req()
                attrs["update_location_status"] = "done"
            else:
                attrs["update_location_status"] = "waiting_server_timestamp"
                attrs["update_location_requested_at"] = requested_at
                if isinstance(requested_at, datetime):
                    attrs["update_location_wait_s"] = int((now - requested_at).total_seconds())
        else:
            attrs["update_location_status"] = "idle"

        return attrs
