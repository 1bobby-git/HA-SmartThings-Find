from __future__ import annotations

from typing import Any

from homeassistant.components.device_tracker.config_entry import TrackerEntity
from homeassistant.components.device_tracker.const import SourceType
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

_BASE = "https://smartthingsfind.samsung.com"


def _normalize_picture_url(url: str | None) -> str | None:
    if not url:
        return None

    u = str(url).strip()
    if not u:
        return None

    if u.startswith("//"):
        return "https:" + u
    if u.startswith("/"):
        return _BASE + u
    return u


def _extract_url(v: Any) -> str | None:
    # coloredIcon이 dict로 오는 케이스 대응: {"url": "..."} / {"path": "..."} 등
    if isinstance(v, str):
        return v
    if isinstance(v, dict):
        for k in ("url", "href", "src", "path"):
            if isinstance(v.get(k), str) and v.get(k).strip():
                return v.get(k)
    return None


def _pick_entity_picture(dev: dict[str, Any]) -> str | None:
    data = dev.get("data") or {}

    icons = data.get("icons") or {}
    if isinstance(icons, dict):
        # 1) 기본(정상 동작하던) 키들
        for k in ("coloredIcon", "coloredIconUrl", "coloredIconURL", "icon", "iconUrl", "iconURL"):
            raw = _extract_url(icons.get(k))
            pic = _normalize_picture_url(raw)
            if pic:
                return pic

    # 2) 폰 등에서 data 레벨로 내려오는 fallback
    for k in (
        "coloredIcon",
        "coloredIconUrl",
        "coloredIconURL",
        "icon",
        "iconUrl",
        "iconURL",
        "imageUrl",
        "imageURL",
        "imgUrl",
        "imgURL",
        "pictureUrl",
        "pictureURL",
        "thumbnailUrl",
        "thumbnailURL",
        "deviceIconUrl",
        "deviceImageUrl",
    ):
        raw = _extract_url(data.get(k))
        pic = _normalize_picture_url(raw)
        if pic:
            return pic

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
    _attr_icon = "mdi:nfc-search-variant"

    def __init__(self, coordinator, dev: dict[str, Any]) -> None:
        super().__init__(coordinator)
        self.dev = dev
        self._dvce_id = dev["data"]["dvceID"]

        self._attr_unique_id = f"{self._dvce_id}_tracker"
        self._attr_name = None
        self._attr_device_info = dev["ha_dev_info"]

    @property
    def entity_picture(self) -> str | None:
        # ✅ 폰 포함: STF 기기 아이콘은 device_tracker에서만 표시
        return _pick_entity_picture(self.dev)

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
