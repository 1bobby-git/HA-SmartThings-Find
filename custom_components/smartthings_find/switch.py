from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import (
    DOMAIN,
    NOTIFY_WHEN_FOUND_ON_OPS,
    NOTIFY_WHEN_FOUND_OFF_OPS,
)
from .utils import fetch_csrf

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up 'Notify when found' switches.

    0.3.22.1 policy:
    - Web에 보이는 토글을 HA에도 노출하는 것이 목적
    - 기기별/계정별 op 값이 다를 수 있어 best-effort로 동작
    """
    devices = hass.data[DOMAIN][entry.entry_id]["devices"]
    entities: list[SwitchEntity] = []

    for device in devices:
        entities.append(NotifyWhenFoundSwitch(hass, entry.entry_id, device))

    async_add_entities(entities)


class NotifyWhenFoundSwitch(SwitchEntity, RestoreEntity):
    """'찾으면 알림 받기' (best-effort)."""

    _attr_icon = "mdi:bell-badge-outline"
    _attr_assumed_state = True

    def __init__(self, hass: HomeAssistant, entry_id: str, device: dict[str, Any]) -> None:
        self.hass = hass
        self._entry_id = entry_id

        data = device["data"]
        self.device = data

        self._dvce_id = data.get("dvceID")
        self._usr_id = data.get("usrId")

        model_name = data.get("modelName", "SmartThings Find Device")
        self._attr_unique_id = f"stf_notify_when_found_{self._dvce_id}"
        self._attr_name = f"{model_name} Notify When Found"

        self._attr_device_info = device.get("ha_dev_info")

        icons = data.get("icons") or {}
        colored_icon = icons.get("coloredIcon") or icons.get("icon")
        if colored_icon:
            self._attr_entity_picture = colored_icon

        self._is_on = False

    @property
    def is_on(self) -> bool:
        return self._is_on

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state in ("on", "off"):
            self._is_on = last_state.state == "on"

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

    async def _post_operation(self, operation: str, extra: dict[str, Any] | None = None) -> bool:
        session, csrf_token = await self._get_session_and_csrf()
        if session is None or not csrf_token:
            return False

        payload: dict[str, Any] = {"dvceId": self._dvce_id, "operation": operation, "usrId": self._usr_id}
        if extra:
            payload.update(extra)

        url = f"https://smartthingsfind.samsung.com/dm/addOperation.do?_csrf={csrf_token}"

        try:
            async with session.post(url, json=payload) as resp:
                txt = await resp.text()
                _LOGGER.debug("Notify op=%s http=%s payload=%s resp=%s", operation, resp.status, payload, txt)

                if resp.status == 200:
                    return True

                # csrf refresh (best-effort)
                await fetch_csrf(self.hass, session, self._entry_id)
                return False
        except Exception as err:
            _LOGGER.exception("Notify operation error op=%s: %s", operation, err)
            return False

    async def _try_ops(self, ops: list[str], status: str) -> bool:
        """Try multiple operations until one succeeds."""
        for op in ops:
            ok = await self._post_operation(op, {"status": status})
            if ok:
                _LOGGER.info("Notify When Found success op=%s status=%s device=%s", op, status, self._dvce_id)
                return True
        _LOGGER.warning("Notify When Found failed for all candidates status=%s device=%s", status, self._dvce_id)
        return False

    async def async_turn_on(self, **kwargs: Any) -> None:
        ok = await self._try_ops(NOTIFY_WHEN_FOUND_ON_OPS, "start")
        if ok:
            self._is_on = True
            self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        # 먼저 OFF 후보를 시도하고, 없으면 ON 후보를 stop으로 시도
        ok = await self._try_ops(NOTIFY_WHEN_FOUND_OFF_OPS, "start")
        if not ok:
            ok = await self._try_ops(NOTIFY_WHEN_FOUND_ON_OPS, "stop")

        if ok:
            self._is_on = False
            self.async_write_ha_state()
