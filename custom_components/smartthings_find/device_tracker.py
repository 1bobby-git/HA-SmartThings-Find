from __future__ import annotations

from typing import Any

from homeassistant.components.device_tracker.config_entry import TrackerEntity
from homeassistant.components.device_tracker.const import SourceType
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


def _normalize_icon_url(url: str | None) -> str | None:
    if not url:
        return None
    u = url.strip()
    if not u:
        return None

    # STF가 //cdn... 형태로 주는 경우
    if u.startswith("//"):
        return f"https:{u}"

    # STF가 /img/... 상대경로로 주는 경우
    if u.startswith("/"):
        return f"https://smartthingsfind.samsung.com{u}"

    return u


def _pick_device_icon(dev_data: dict[str, Any]) -> str | None:
    """STF 기기 아이콘(폰 포함) URL을 최대한 안전하게 추출."""
    icons = dev_data.get("icons") or {}

    # 1) 기존에 잘 되던 케이스(태그 등)
    cand = (
        icons.get("coloredIcon")
        or icons.get("icon")
        or icons.get("iconUrl")
        or icons.get("coloredIconUrl")
        or icons.get("imageUrl")
        or dev_data.get("iconUrl")
        or dev_data.get("imageUrl")
    )

    # 2) 그래도 없으면, icons 내부에서 URL스러운 값 하나라도 잡기
    if not cand and isinstance(icons, dict):
        for v in icons.values():
            if isinstance(v, str) and (v.startswith("/") or v.startswith("//") or v.startswith("http")):
                cand = v
                break

    return _normalize_icon_url(cand if isinstance(cand, str) else None)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]
    devices = data["devices"]

    entities: list[SmartThingsFindTracker] = [SmartThingsFindTracker(coordinator, dev) for dev in devices]
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

        # ✅ STF 기기그림은 device_tracker에만 적용 (폰 포함 fallback 강화)
        self._entity_picture_url = _pick_device_icon(dev.get("data") or {})

    @property
    def entity_picture(self) -> str | None:
        return self._entity_picture_url

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
