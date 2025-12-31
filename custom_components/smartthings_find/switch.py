from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN, OPERATION_LIST_KEYS
from .utils import fetch_csrf

_LOGGER = logging.getLogger(__name__)


def _extract_supported_operations(dev_data: dict[str, Any]) -> set[str]:
    ops: set[str] = set()
    for key in OPERATION_LIST_KEYS:
        raw = dev_data.get(key)
        if not raw:
            continue
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, dict):
                    t = item.get("oprnType") or item.get("operation") or item.get("type") or item.get("code")
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


def _pick_notify_when_found_ops(supported_ops: set[str]) -> tuple[str | None, str | None]:
    """Try to find ON/OFF operation codes for 'notify when found'."""
    # Heuristic: any op containing both NOTIFY and FOUND
    cands = [op for op in supported_ops if "NOTIFY" in op.upper() and "FOUND" in op.upper()]
    if not cands:
        return None, None

    def score_on(op: str) -> int:
        u = op.upper()
        return (10 if "ON" in u else 0) + (5 if "START" in u else 0) + (1 if "ENABLE" in u else 0)

    def score_off(op: str) -> int:
        u = op.upper()
        return (10 if "OFF" in u else 0) + (8 if "STOP" in u else 0) + (6 if "CANCEL" in u else 0) + (1 if "DISABLE" in u else 0)

    op_on = sorted(cands, key=score_on, reverse=True)[0]
    op_off_candidates = sorted(cands, key=score_off, reverse=True)
    op_off = op_off_candidates[0] if score_off(op_off_candidates[0]) > 0 else None

    return op_on, op_off


class _STFOperationHelper:
    def __init__(self, hass: HomeAssistant, entry_id: str, device: dict[str, Any]) -> None:
        self.hass = hass
        self._entry_id = entry_id
        self._dev_data = device["data"]
        self._dvce_id = self._dev_data.get("dvceID")
        self._usr_id = self._dev_data.get("usrId")

    async def _get_session_and_csrf(self):
        entry_data = self.hass.data[DOMAIN].get(self._entry_id, {})
        session = entry_data.get("session")
        csrf_token = entry_data.get("_csrf")

        if session is None:
            _LOGGER.error("No session found for entry_id=%s", self._entry_id)
            return None, None

        if not csrf_token:
            await fetch_csrf(self.hass, session, self._entry_id)
            csrf_token = self.hass.data[DOMAIN][self._entry_id].get("_csrf")

        return session, csrf_token

    async def post_operation(self, operation: str, extra: dict[str, Any] | None = None) -> bool:
        session, csrf = await self._get_session_and_csrf()
        if session is None or not csrf:
            return False

        payload: dict[str, Any] = {"dvceId": self._dvce_id, "operation": operation, "usrId": self._usr_id}
        if extra:
            payload.update(extra)

        url = f"https://smartthingsfind.samsung.com/dm/addOperation.do?_csrf={csrf}"

        try:
            async with session.post(url, json=payload) as response:
                if response.status == 200:
                    return True
                _LOGGER.warning("Operation %s failed (HTTP %s).", operation, response.status)
                await fetch_csrf(self.hass, session, self._entry_id)
                return False
        except Exception as err:
            _LOGGER.exception("Exception while posting operation %s: %s", operation, err)
            return False


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    devices = hass.data[DOMAIN][entry.entry_id]["devices"]
    entities: list[SwitchEntity] = []

    for device in devices:
        dev_data = device["data"]
        supported_ops = _extract_supported_operations(dev_data)
        op_on, op_off = _pick_notify_when_found_ops(supported_ops)

        # only add if we found at least an ON operation
        if op_on:
            entities.append(NotifyWhenFoundSwitch(hass, entry.entry_id, device, op_on, op_off))

    async_add_entities(entities)


class NotifyWhenFoundSwitch(_STFOperationHelper, SwitchEntity, RestoreEntity):
    """'Notify me when it's found' toggle (best-effort / optimistic)."""

    _attr_icon = "mdi:bell-badge-outline"
    _attr_assumed_state = True

    def __init__(self, hass: HomeAssistant, entry_id: str, device: dict[str, Any], op_on: str, op_off: str | None):
        _STFOperationHelper.__init__(self, hass, entry_id, device)
        self._attr_device_info = device.get("ha_dev_info")

        self._op_on = op_on
        self._op_off = op_off

        model_name = device["data"].get("modelName", "SmartThings Find Device")
        self._attr_unique_id = f"stf_notify_when_found_{self._dvce_id}"
        self._attr_name = f"{model_name} Notify When Found"

        self._is_on = False

    @property
    def is_on(self) -> bool:
        return self._is_on

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state in ("on", "off"):
            self._is_on = last_state.state == "on"

    async def async_turn_on(self, **kwargs: Any) -> None:
        ok = await self.post_operation(self._op_on, {"status": "start"})
        if ok:
            self._is_on = True
            self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        if self._op_off:
            ok = await self.post_operation(self._op_off, {"status": "start"})
        else:
            # fallback: try stopping the same op
            ok = await self.post_operation(self._op_on, {"status": "stop"})

        if ok:
            self._is_on = False
            self.async_write_ha_state()
