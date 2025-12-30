from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, OP_RING, OP_CHECK_CONNECTION_WITH_LOCATION
from .utils import send_operation


@dataclass(frozen=True, kw_only=True)
class STFButtonDescription(ButtonEntityDescription):
    operation: str
    status: str | None = None


BUTTONS: list[STFButtonDescription] = [
    STFButtonDescription(
        key="ring",
        name="Ring",
        icon="mdi:volume-high",
        operation=OP_RING,
        status="start",
    ),
    STFButtonDescription(
        key="refresh_location",
        name="Refresh location",
        icon="mdi:crosshairs-gps",
        operation=OP_CHECK_CONNECTION_WITH_LOCATION,
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
        csrf = data["coordinator"].csrf
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
        await self.coordinator.async_request_refresh()
