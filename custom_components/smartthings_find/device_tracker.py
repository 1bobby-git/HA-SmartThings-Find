from __future__ import annotations

from typing import Any

from homeassistant.components.device_tracker import TrackerEntity, SourceType
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

_BASE = "https://smartthingsfind.samsung.com"


def _normalize_picture_url(url: str | None) -> str | None:
    if not url:
        return None

    u = str(url).strip()
    if not u:
        return None

    # //cdn... 형태
    if u.startswith("//"):
        return "https:" + u

    # /images/... 같은 상대경로
    if u.startswith("/"):
        return _BASE + u

    return u


def _pick_device_picture(device: dict[str, Any]) -> str | None:
    # 폰/태그/워치/이어버드 등 케이스별로 키가 달라지는 경우가 있어서 후보를 넓게 잡음
    candidates = [
        "entity_picture",
        "entityPicture",
        "picture",
        "pictureUrl",
        "pictureURL",
        "image",
        "imageUrl",
        "imageURL",
        "img",
        "imgUrl",
        "imgURL",
        "icon",
        "iconUrl",
        "iconURL",
        "thumbnail",
        "thumbnailUrl",
        "thumbnailURL",
        "deviceImage",
        "deviceImageUrl",
    ]

    for k in candidates:
        if k in device and device.get(k):
            return _normalize_picture_url(device.get(k))

    # 혹시 nested 형태면(예: device["model"]["imageUrl"])도 최소 대응
    model = device.get("model") if isinstance(device.get("model"), dict) else None
    if model:
        for k in ["imageUrl", "iconUrl", "thumbnailUrl"]:
            if model.get(k):
                return _normalize_picture_url(model.get(k))

    return None


async def async_setup_entry(
    hass: HomeAssistant,
    entry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]

    entities: list[TrackerEntity] = []

    devices = coordinator.data.get("devices", [])
    for dev in devices:
        entities.append(SmartThingsFindDeviceTracker(coordinator, dev))

    async_add_entities(entities)


class SmartThingsFindDeviceTracker(CoordinatorEntity, TrackerEntity):
    _attr_source_type = SourceType.GPS

    def __init__(self, coordinator, device: dict[str, Any]) -> None:
        super().__init__(coordinator)
        self._device = device
        self._device_id = str(device.get("deviceId") or device.get("id") or device.get("devId") or "")
        self._name = device.get("name") or device.get("deviceName") or "SmartThings Find"

        self._attr_unique_id = f"{self._device_id}_tracker"

    @property
    def name(self) -> str:
        return self._name

    @property
    def latitude(self) -> float | None:
        loc = self._device.get("location") if isinstance(self._device.get("location"), dict) else self._device
        return loc.get("latitude") or loc.get("lat")

    @property
    def longitude(self) -> float | None:
        loc = self._device.get("location") if isinstance(self._device.get("location"), dict) else self._device
        return loc.get("longitude") or loc.get("lon") or loc.get("lng")

    @property
    def location_accuracy(self) -> int | None:
        loc = self._device.get("location") if isinstance(self._device.get("location"), dict) else self._device
        v = loc.get("accuracy")
        try:
            return int(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        # 기존 속성 유지가 목적이 아니라 최소만
        return {}

    @property
    def entity_picture(self) -> str | None:
        # ✅ 요청사항: STF 아이콘/그림은 device_tracker에만 적용 유지
        return _pick_device_picture(self._device)

    @property
    def device_info(self) -> dict[str, Any]:
        return {
            "identifiers": {(DOMAIN, self._device_id)},
            "name": self._name,
            "manufacturer": "Samsung",
        }
