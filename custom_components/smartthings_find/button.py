from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from . import SmartThingsFindCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: SmartThingsFindCoordinator = data["coordinator"]
    devices = data["devices"]

    entities = []
    for d in devices:
        dev = d["data"]
        entities.append(SmartThingsFindRefreshButton(coordinator, entry.entry_id, dev["dvceID"]))
    async_add_entities(entities)


class SmartThingsFindRefreshButton(CoordinatorEntity, ButtonEntity):
    """Refresh just this device now."""

    def __init__(self, coordinator: SmartThingsFindCoordinator, entry_id: str, dvce_id: str) -> None:
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._dvce_id = dvce_id

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_{self._dvce_id}_refresh"

    @property
    def name(self) -> str:
        d = self.coordinator.devices_by_id.get(self._dvce_id)
        dev_name = (d["data"].get("modelName") if d else None) or f"STF {self._dvce_id}"
        return f"{dev_name} Refresh"

    @property
    def device_info(self):
        d = self.coordinator.devices_by_id.get(self._dvce_id)
        return d["ha_dev_info"] if d else None

    async def async_press(self) -> None:
        await self.coordinator.async_refresh_device(self._dvce_id)
