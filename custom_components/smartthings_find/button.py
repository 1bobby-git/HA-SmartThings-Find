from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    OP_RING,
    OP_CHECK_CONNECTION_WITH_LOCATION,
    OP_LOCK,
    OP_TRACK,
    OP_ERASE,
    OP_EXTEND_BATTERY,
    OPERATION_LIST_KEYS,
)
from .utils import fetch_csrf

_LOGGER = logging.getLogger(__name__)


def _extract_supported_operations(dev_data: dict[str, Any]) -> set[str]:
    """Return a set of supported operation codes for a device (best-effort)."""
    ops: set[str] = set()

    for key in OPERATION_LIST_KEYS:
        raw = dev_data.get(key)
        if not raw:
            continue

        # list[str] or list[dict]
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, dict):
                    # Many variants seen in reverse-engineered payloads
                    t = item.get("oprnType") or item.get("operation") or item.get("type") or item.get("code")
                    # Some lists contain support flags
                    support = item.get("supportYn") or item.get("supported") or item.get("support")
                    if support is False or str(support).upper() in ("N", "NO", "FALSE", "0"):
                        continue
                    if t:
                        ops.add(str(t))
                else:
                    ops.add(str(item))
            if ops:
                break

    return ops


def _pick_stop_ring_operation(supported_ops: set[str], dev_type: str | None) -> tuple[str | None, dict[str, Any] | None]:
    """Return (operation, extra_payload) for stop ringing, if supported."""
    # Some devices may expose an explicit stop operation
    stop_candidates = (
        "STOP_RING",
        "RING_STOP",
        "STOP_RINGING",
        "STOP_SOUND",
        "STOP_ALARM",
        "STOP_BUZZER",
    )
    for c in stop_candidates:
        if c in supported_ops:
            return c, None

    # Fallback: for non-TAG devices, some accept RING with status=stop
    if dev_type and dev_type != "TAG" and OP_RING in supported_ops:
        return OP_RING, {"status": "stop"}

    return None, None


class _STFOperationEntity:
    """Shared operation posting helper (no HA Entity base here)."""

    def __init__(self, hass: HomeAssistant, entry_id: str, device: dict[str, Any]) -> None:
        self.hass = hass
        self._entry_id = entry_id
        self._device_wrapper = device
        self._dev_data = device["data"]

        # Common fields
        self._dvce_id = self._dev_data.get("dvceID")
        self._usr_id = self._dev_data.get("usrId")

        # picture if available (doesn't affect icon usage if not shown)
        icons = self._dev_data.get("icons") or {}
        self._entity_picture = icons.get("coloredIcon") or icons.get("icon")

    async def _get_session_and_csrf(self):
        entry_data = self.hass.data[DOMAIN].get(self._entry_id, {})
        session = entry_data.get("session")
        csrf_token = entry_data.get("_csrf")

        if session is None:
            _LOGGER.error("No session found for entry_id=%s", self._entry_id)
            return None, None

        if not csrf_token:
            _LOGGER.debug("No CSRF token cached; attempting to fetch a new one.")
            await fetch_csrf(self.hass, session, self._entry_id)
            csrf_token = self.hass.data[DOMAIN][self._entry_id].get("_csrf")

        if not csrf_token:
            _LOGGER.error("Failed to obtain CSRF token for entry_id=%s", self._entry_id)
            return session, None

        return session, csrf_token

    async def _post_operation(self, operation: str, extra: dict[str, Any] | None = None) -> bool:
        session, csrf = await self._get_session_and_csrf()
        if session is None or not csrf:
            return False

        payload: dict[str, Any] = {
            "dvceId": self._dvce_id,
            "operation": operation,
            "usrId": self._usr_id,
        }
        if extra:
            payload.update(extra)

        url = f"https://smartthingsfind.samsung.com/dm/addOperation.do?_csrf={csrf}"

        try:
            async with session.post(url, json=payload) as response:
                _LOGGER.debug("Operation %s HTTP status: %s", operation, response.status)

                if response.status == 200:
                    _LOGGER.debug("Operation response: %s", await response.text())
                    return True

                # If request failed, refresh CSRF (cookie may be expired / login invalid)
                _LOGGER.warning("Operation %s failed (HTTP %s). Refreshing CSRF.", operation, response.status)
                await fetch_csrf(self.hass, session, self._entry_id)
                return False

        except Exception as err:
            _LOGGER.exception("Exception while posting operation %s: %s", operation, err)
            return False


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    devices = hass.data[DOMAIN][entry.entry_id]["devices"]
    entities: list[ButtonEntity] = []

    for device in devices:
        dev_data = device["data"]
        dev_type = dev_data.get("deviceTypeCode")
        supported_ops = _extract_supported_operations(dev_data)

        _LOGGER.debug("Device %s supported_ops=%s", dev_data.get("modelName"), sorted(supported_ops))

        # Ring (start)
        if OP_RING in supported_ops:
            entities.append(RingStartButton(hass, entry.entry_id, device))

            # Ring stop (supported only)
            stop_op, stop_extra = _pick_stop_ring_operation(supported_ops, dev_type)
            if stop_op:
                entities.append(RingStopButton(hass, entry.entry_id, device, stop_op, stop_extra))

        # Location update (button)
        if OP_CHECK_CONNECTION_WITH_LOCATION in supported_ops:
            entities.append(LocationUpdateButton(hass, entry.entry_id, device))

        # Phone actions (best-effort, only if listed as supported)
        if OP_LOCK in supported_ops:
            entities.append(PhoneActionButton(hass, entry.entry_id, device, OP_LOCK, "Lost Mode", "mdi:lock-alert"))
        if OP_TRACK in supported_ops:
            entities.append(
                PhoneActionButton(hass, entry.entry_id, device, OP_TRACK, "Track Location", "mdi:crosshairs-gps")
            )
        if OP_ERASE in supported_ops:
            entities.append(
                PhoneActionButton(hass, entry.entry_id, device, OP_ERASE, "Erase Data", "mdi:trash-can-outline")
            )
        if OP_EXTEND_BATTERY in supported_ops:
            entities.append(
                PhoneActionButton(
                    hass, entry.entry_id, device, OP_EXTEND_BATTERY, "Extend Battery", "mdi:battery-plus-outline"
                )
            )

    async_add_entities(entities)


class RingStartButton(_STFOperationEntity, ButtonEntity):
    """Ring start."""

    _attr_icon = "mdi:volume-high"

    def __init__(self, hass: HomeAssistant, entry_id: str, device: dict[str, Any]) -> None:
        _STFOperationEntity.__init__(self, hass, entry_id, device)
        self._attr_device_info = device.get("ha_dev_info")

        model_name = self._dev_data.get("modelName", "SmartThings Find Device")
        self._attr_unique_id = f"stf_ring_start_{self._dvce_id}"
        self._attr_name = f"{model_name} Ring"

        if self._entity_picture:
            self._attr_entity_picture = self._entity_picture

    async def async_press(self) -> None:
        await self._post_operation(
            OP_RING,
            {
                "status": "start",
                "lockMessage": "Home Assistant is ringing your device!",
            },
        )


class RingStopButton(_STFOperationEntity, ButtonEntity):
    """Ring stop (only for devices that support it)."""

    _attr_icon = "mdi:volume-mute"

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        device: dict[str, Any],
        stop_operation: str,
        stop_extra: dict[str, Any] | None,
    ) -> None:
        _STFOperationEntity.__init__(self, hass, entry_id, device)
        self._attr_device_info = device.get("ha_dev_info")

        self._stop_operation = stop_operation
        self._stop_extra = stop_extra

        model_name = self._dev_data.get("modelName", "SmartThings Find Device")
        self._attr_unique_id = f"stf_ring_stop_{self._dvce_id}"
        self._attr_name = f"{model_name} Stop Ring"

        if self._entity_picture:
            self._attr_entity_picture = self._entity_picture

    async def async_press(self) -> None:
        extra = self._stop_extra or {}
        # If explicit STOP op doesn't need status, it's okay; for RING fallback, status=stop already set.
        await self._post_operation(self._stop_operation, extra)


class LocationUpdateButton(_STFOperationEntity, ButtonEntity):
    """Trigger 'Update location' on STF website."""

    _attr_icon = "mdi:map-marker-radius"

    def __init__(self, hass: HomeAssistant, entry_id: str, device: dict[str, Any]) -> None:
        _STFOperationEntity.__init__(self, hass, entry_id, device)
        self._attr_device_info = device.get("ha_dev_info")

        model_name = self._dev_data.get("modelName", "SmartThings Find Device")
        self._attr_unique_id = f"stf_location_update_{self._dvce_id}"
        self._attr_name = f"{model_name} Update Location"

        if self._entity_picture:
            self._attr_entity_picture = self._entity_picture

    async def async_press(self) -> None:
        # Observed payloads often don't require status for this operation
        await self._post_operation(OP_CHECK_CONNECTION_WITH_LOCATION)


class PhoneActionButton(_STFOperationEntity, ButtonEntity):
    """Generic phone action button (only if device supports operation)."""

    def __init__(self, hass: HomeAssistant, entry_id: str, device: dict[str, Any], op: str, label: str, icon: str):
        _STFOperationEntity.__init__(self, hass, entry_id, device)
        self._attr_device_info = device.get("ha_dev_info")

        self._op = op
        self._attr_icon = icon

        model_name = self._dev_data.get("modelName", "SmartThings Find Device")
        self._attr_unique_id = f"stf_op_{op.lower()}_{self._dvce_id}"
        self._attr_name = f"{model_name} {label}"

        if self._entity_picture:
            self._attr_entity_picture = self._entity_picture

    async def async_press(self) -> None:
        # Some operations may require "status": "start" (best-effort)
        extra: dict[str, Any] = {"status": "start"}

        # Provide a message if it is lock-ish operation (harmless even if ignored)
        if self._op == OP_LOCK:
            extra["lockMessage"] = "Enabled from Home Assistant"

        await self._post_operation(self._op, extra)
