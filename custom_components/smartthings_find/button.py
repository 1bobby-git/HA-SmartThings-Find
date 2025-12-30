from __future__ import annotations

from dataclasses import dataclass
import re
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


BASE_BUTTONS: list[STFButtonDescription] = [
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

_OPERATION_META: dict[str, dict[str, str]] = {
    "RING": {"name": "Ring", "icon": "mdi:volume-high", "status": "start"},
    "CHECK_CONNECTION_WITH_LOCATION": {"name": "Refresh location", "icon": "mdi:crosshairs-gps"},
    "LOCATE": {"name": "Locate", "icon": "mdi:crosshairs"},
    "TRACK_LOCATION": {"name": "Track location", "icon": "mdi:map-marker-path"},
    "REMOTE_LOCK": {"name": "Remote lock", "icon": "mdi:lock"},
    "LOCK": {"name": "Lock", "icon": "mdi:lock"},
    "ERASE": {"name": "Erase data", "icon": "mdi:delete-forever"},
    "DELETE_DATA": {"name": "Erase data", "icon": "mdi:delete-forever"},
    "BACKUP": {"name": "Backup", "icon": "mdi:backup-restore"},
    "EXTEND_BATTERY": {"name": "Extend battery life", "icon": "mdi:battery-plus"},
    "POWER_OFF": {"name": "Power off", "icon": "mdi:power"},
    "SIREN": {"name": "Siren", "icon": "mdi:alarm-bell"},
}

_OPERATION_LIST_KEYS = (
    "supportOperations",
    "supportOperationList",
    "operationList",
    "operations",
    "oprnList",
    "oprnTypeList",
    "funcList",
    "functionList",
)


def _normalize_key(value: str) -> str:
    key = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip().lower()).strip("_")
    return key or "operation"


def _parse_operation_entry(entry: Any) -> tuple[str, str | None] | None:
    if isinstance(entry, str):
        return entry, None
    if not isinstance(entry, dict):
        return None

    op = (
        entry.get("operation")
        or entry.get("oprnType")
        or entry.get("oprnCd")
        or entry.get("code")
        or entry.get("type")
        or entry.get("id")
        or entry.get("key")
    )
    if not isinstance(op, str) or not op:
        return None

    status = entry.get("status") or entry.get("oprnStatus")
    return op, status if isinstance(status, str) else None


def _extract_supported_operations(dev_data: dict[str, Any]) -> list[tuple[str, str | None]]:
    for key in _OPERATION_LIST_KEYS:
        raw = dev_data.get(key)
        if isinstance(raw, list):
            ops: list[tuple[str, str | None]] = []
            for entry in raw:
                parsed = _parse_operation_entry(entry)
                if parsed:
                    ops.append(parsed)
            if ops:
                return ops
        if isinstance(raw, dict):
            ops = []
            for op_key, op_val in raw.items():
                if isinstance(op_key, str):
                    status = None
                    if isinstance(op_val, dict):
                        status = op_val.get("status") or op_val.get("oprnStatus")
                    ops.append((op_key, status if isinstance(status, str) else None))
            if ops:
                return ops
    return []


def _build_button_descriptions(dev_data: dict[str, Any]) -> list[STFButtonDescription]:
    is_tag = dev_data.get("deviceTypeCode") == "TAG"
    descriptions = list(BASE_BUTTONS)
    if is_tag:
        return descriptions

    supported_ops = _extract_supported_operations(dev_data)
    if not supported_ops:
        return descriptions

    seen_ops = {desc.operation for desc in descriptions}
    for op, status in supported_ops:
        if op in seen_ops:
            continue
        meta = _OPERATION_META.get(op, {})
        descriptions.append(
            STFButtonDescription(
                key=_normalize_key(op),
                name=meta.get("name", op.replace("_", " ").title()),
                icon=meta.get("icon", "mdi:gesture-tap-button"),
                operation=op,
                status=meta.get("status", status),
            )
        )
        seen_ops.add(op)

    return descriptions


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]
    devices = data["devices"]

    entities: list[ButtonEntity] = []
    for dev in devices:
        for desc in _build_button_descriptions(dev["data"]):
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
