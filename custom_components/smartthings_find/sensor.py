from __future__ import annotations

from datetime import datetime, timezone
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
        # 기본 아이콘은 device_class가 처리

    @property
    def native_value(self) -> int | None:
        res = self.coordinator.data.get(self._dvce_id) if self.coordinator.data else None
        return (res or {}).get("battery_level")


def _as_datetime(value: Any) -> datetime | None:
    """Convert various timestamp formats to datetime (UTC-aware).

    Accepts:
    - datetime
    - epoch seconds (int/float)
    - epoch milliseconds (int/float)
    - ISO8601 string (best-effort)
    """
    if value is None:
        return None

    if isinstance(value, datetime):
        # ensure tz-aware
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

    # epoch seconds / ms
    if isinstance(value, (int, float)):
        # heuristic: ms if very large
        ts = float(value)
        if ts > 10_000_000_000:  # ~2286-11-20 in seconds, so likely ms
            ts = ts / 1000.0
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except Exception:
            return None

    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None

        # numeric string epoch
        if s.isdigit():
            try:
                n = int(s)
                return _as_datetime(n)
            except Exception:
                return None

        # ISO8601 best-effort (Python 3.11+ supports many variants)
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:
            return None

    return None


class SmartThingsFindLastUpdateSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:clock-outline"

    # STF/Backend에서 자주 보이는 후보 키들 (used_loc -> res 순으로 탐색)
    _LOC_KEYS = (
        "gps_date",
        "gpsDate",
        "gpsDttm",
        "gps_dt",
        "lastUpdate",
        "last_update",
        "lastUpdateDttm",
        "updDttm",
        "updateDttm",
        "dttm",
    )
    _RES_KEYS = (
        "last_update",
        "lastUpdate",
        "lastUpdateDttm",
        "gps_date",
        "gpsDate",
        "updDttm",
        "updateDttm",
        "fetched_at",
    )

    def __init__(self, coordinator, dev: dict[str, Any]) -> None:
        super().__init__(coordinator)
        self.dev = dev
        self._dvce_id = dev["data"]["dvceID"]

        self._attr_unique_id = f"{self._dvce_id}_last_update"
        self._attr_name = "Last update"
        self._attr_device_info = dev["ha_dev_info"]
        # ✅ 아이콘은 기본 mdi 유지 (요구사항)

    @property
    def native_value(self) -> datetime | None:
        res = self.coordinator.data.get(self._dvce_id) if self.coordinator.data else None
        if not res:
            # 최후 fallback: coordinator refresh 성공 시각
            return getattr(self.coordinator, "last_update_success_time", None)

        loc = res.get("used_loc") or {}

        # 1) location 안의 “마지막 업데이트” 후보
        for k in self._LOC_KEYS:
            dt = _as_datetime(loc.get(k))
            if dt:
                return dt

        # 2) res 루트의 후보
        for k in self._RES_KEYS:
            dt = _as_datetime(res.get(k))
            if dt:
                return dt

        # 3) 최후 fallback: coordinator refresh 성공 시각 (Update Location 버튼 refresh 반영)
        return getattr(self.coordinator, "last_update_success_time", None)
