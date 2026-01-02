from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


def _battery_icon(level: int | None) -> str:
    if level is None:
        return "mdi:battery-unknown"

    try:
        v = int(level)
    except (TypeError, ValueError):
        return "mdi:battery-unknown"

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


async def async_setup_entry(
    hass: HomeAssistant,
    entry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]

    entities: list[SensorEntity] = []

    # coordinator.data 구조는 기존 프로젝트에 맞춰 "devices" 형태로 가정
    # (당신 코드가 coordinator.data["devices"]가 아니면 그 부분만 맞춰주세요)
    devices = coordinator.data.get("devices", [])
    for dev in devices:
        # 배터리 없는 기기(이어버드 등)는 스킵
        if dev.get("battery") is None:
            continue
        entities.append(SmartThingsFindBatterySensor(coordinator, dev))

    async_add_entities(entities)


class SmartThingsFindBatterySensor(CoordinatorEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(self, coordinator, device: dict[str, Any]) -> None:
        super().__init__(coordinator)
        self._device = device
        self._device_id = str(device.get("deviceId") or device.get("id") or device.get("devId") or "")
        self._name = device.get("name") or device.get("deviceName") or "SmartThings Find"

        # 고유 ID / 엔티티 ID는 기존 정책을 최대한 보수적으로
        self._attr_unique_id = f"{self._device_id}_battery"

    @property
    def name(self) -> str:
        return f"{self._name} Battery"

    @property
    def native_value(self) -> int | None:
        level = self._device.get("battery")
        try:
            return int(level) if level is not None else None
        except (TypeError, ValueError):
            return None

    @property
    def icon(self) -> str:
        return _battery_icon(self.native_value)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        # 기존에 쓰던 속성이 있으면 여기서 유지하면 되지만,
        # 요청사항이 "아이콘만"이라 최소한만 둠
        return {}

    @property
    def device_info(self) -> dict[str, Any]:
        # device_tracker와 동일 디바이스로 묶이게 identifiers만 유지
        return {
            "identifiers": {(DOMAIN, self._device_id)},
            "name": self._name,
            "manufacturer": "Samsung",
        }
