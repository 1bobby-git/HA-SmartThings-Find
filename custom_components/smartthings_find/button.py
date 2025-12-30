from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    OP_RING,
    OP_CHECK_CONNECTION_WITH_LOCATION,
    OP_LOST_MODE,
    OP_TRACK_LOCATION,
    OP_ERASE_DATA,
    OP_EXTEND_BATTERY,
)
from .utils import send_operation

@dataclass(frozen=True, kw_only=True)
class STFButtonDescription(ButtonEntityDescription):
    operation: str
    status: str | None = None

BUTTONS: list[STFButtonDescription] = [
    STFButtonDescription(
        key="ring",
        translation_key="ring",
        name="Ring",
        icon="mdi:volume-high",
        operation=OP_RING,
        status="start",
    ),
    STFButtonDescription(
        key="refresh_location",
        translation_key="refresh_location",
        name="Refresh location",
        icon="mdi:crosshairs-gps",
        operation=OP_CHECK_CONNECTION_WITH_LOCATION,
    ),
    # 아래는 "사이트 버튼 느낌" - 계정/기기마다 실패할 수 있음 (실험)
    STFButtonDescription(
        key="lost_mode",
        translation_key="lost_mode",
        name="Lost mode",
        icon="mdi:shield-lock",
        operation=OP_LOST_MODE,
        status="start",
    ),
    STFButtonDescription(
        key="track_location",
        translation_key="track_location",
        name="Track location",
        icon="mdi:map-marker-path",
        operation=OP_TRACK_LOCATION,
        status="start",
    ),
    STFButtonDescription(
        key="erase_data",
        translation_key="erase_data",
        name="Erase data",
        icon="mdi:trash-can-outline",
        operation=OP_ERASE_DATA,
        status="start",
    ),
    STFButtonDescription(
        key="extend_battery",
        translation_key="extend_battery",
        name="Extend battery time",
        icon="mdi:battery-clock-outline",
        operation=OP_EXTEND_BATTERY,
        status="start",
    ),
]

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]
    devices = data["devices"]

    entities: list[ButtonEntity] = []
    for dev in devices:
        for desc in BUTTONS:
            entities.append(SmartThingsFindButton(coordinator, entry, dev, desc))
    async_add_entities(entities)

class SmartThingsFindButton(CoordinatorEntity, ButtonEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, entry: ConfigEntry, dev: dict[str, Any], description: STFButtonDescription) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self.dev = dev
        self.entry = entry

        dev_data = dev["data"]
        self._dvce_id = dev_data["dvceID"]
        self._usr_id = dev_data.get("usrId")

        self._attr_unique_id = f"{self._dvce_id}_{description.key}"
        self._attr_device_info = dev["ha_dev_info"]

    async def async_press(self) -> None:
        data = self.hass.data[DOMAIN][self.entry.entry_id]
        session = data["session"]
        csrf = data["coordinator"].csrf or data.get("csrf")
        if not csrf:
            return

        desc: STFButtonDescription = self.entity_description  # type: ignore[assignment]
        await send_operation(
            session=session,
            csrf=csrf,
            dvce_id=self._dvce_id,
            usr_id=self._usr_id,
            operation=desc.operation,
            status=desc.status,
        )
        # 버튼 누르면 바로 한 번 refresh
        await self.coordinator.async_request_refresh()
