from __future__ import annotations

from typing import Any

from homeassistant.components.device_tracker.config_entry import TrackerEntity
from homeassistant.components.device_tracker.const import SourceType
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

STF_BASE_URL = "https://smartthingsfind.samsung.com"

# deviceTypeCode → 아이콘 파일명 매핑
DEVICE_TYPE_ICON_MAP: dict[str, str] = {
    "PHONE": "phone",
    "PHONE DEVICE": "phone",
    "TAB": "tablet",
    "TAB DEVICE": "tablet",
    "PC": "laptop",
    "PC DEVICE": "laptop",
    "SPEN": "spen_pro",
    "VR": "vr",
    "AR": "ar",
}

# BUDS subType → 아이콘 파일명 매핑
BUDS_SUBTYPE_ICON_MAP: dict[str, str] = {
    "CANAL": "buds_pair",
    "CANAL2": "attic_pair",
    "CANAL3": "buds3_pair",
    "CANAL4": "buds4_pair",
    "OPEN": "bean_pair",
    "TWS_3RD_PARTY": "tws_pair",
}

# WATCH/WEARABLE subType → 아이콘 파일명 매핑
WATCH_SUBTYPE_ICON_MAP: dict[str, str] = {
    "WATCH": "watch",
    "FIT": "band",
    "RING": "ring",
}


def _get_device_icon_url(device_data: dict[str, Any]) -> str | None:
    """deviceTypeCode와 subType을 기반으로 STF 아이콘 URL 반환"""
    device_type = (device_data.get("deviceTypeCode") or "").upper()
    sub_type = (device_data.get("subType") or "").upper()
    icons = device_data.get("icons") or {}
    colored_icon = icons.get("coloredIcon")

    # TAG: icons.coloredIcon 사용 (SmartTag, SmartTag2 - API에서 전체 URL 제공)
    if device_type == "TAG":
        if colored_icon:
            if colored_icon.startswith("http"):
                return colored_icon
            elif colored_icon.startswith("/"):
                return f"{STF_BASE_URL}{colored_icon}"
        return None

    # BUDS: subType 기반 매핑
    if device_type == "BUDS":
        icon_name = BUDS_SUBTYPE_ICON_MAP.get(sub_type, "buds_pair")
        return f"{STF_BASE_URL}/img/device_icon/{icon_name}.svg"

    # WATCH: subType 기반 매핑
    if device_type == "WATCH":
        icon_name = WATCH_SUBTYPE_ICON_MAP.get(sub_type, "watch")
        return f"{STF_BASE_URL}/img/device_icon/{icon_name}.svg"

    # WEARABLE: subType 기반 매핑
    if device_type == "WEARABLE":
        icon_name = WATCH_SUBTYPE_ICON_MAP.get(sub_type, "ring")
        return f"{STF_BASE_URL}/img/device_icon/{icon_name}.svg"

    # PHONE, TAB, PC, SPEN, VR, AR: deviceType 기반 매핑
    if device_type in DEVICE_TYPE_ICON_MAP:
        icon_name = DEVICE_TYPE_ICON_MAP[device_type]
        return f"{STF_BASE_URL}/img/device_icon/{icon_name}.svg"

    return None


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

    def __init__(self, coordinator, dev: dict[str, Any]) -> None:
        super().__init__(coordinator)
        self.dev = dev
        self._dvce_id = dev["data"]["dvceID"]

        self._attr_unique_id = f"{self._dvce_id}_tracker"
        self._attr_name = None
        self._attr_device_info = dev["ha_dev_info"]

        # STF 아이콘 적용
        device_data = dev.get("data") or {}
        icon_url = _get_device_icon_url(device_data)
        if icon_url:
            self._attr_entity_picture = icon_url

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
