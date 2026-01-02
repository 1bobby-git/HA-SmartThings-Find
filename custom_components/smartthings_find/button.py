from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    DATA_SESSION,
    DATA_COORDINATOR,
    DATA_DEVICES,
    OP_RING,
    OP_CHECK_CONNECTION_WITH_LOCATION,
)
from .utils import fetch_csrf

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up minimal SmartThings Find buttons.

    Keep only:
    - Ring
    - Stop Ring
    - Update Location
    """
    data = hass.data[DOMAIN][entry.entry_id]
    devices = data[DATA_DEVICES]

    # ✅ Update Location 대기 상태 저장소(센서에서 읽음)
    data.setdefault("last_update_requests", {})

    entities: list[ButtonEntity] = []
    for device in devices:
        entities.append(RingStartButton(hass, entry.entry_id, device))
        entities.append(RingStopButton(hass, entry.entry_id, device))
        entities.append(UpdateLocationButton(hass, entry.entry_id, device))

    async_add_entities(entities)


class _STFOperationButton(ButtonEntity):
    """Common helper to call STF addOperation.do with CSRF handling.

    NOTE: Do NOT set entity_picture here.
    Only device_tracker should show STF icon.
    """

    def __init__(self, hass: HomeAssistant, entry_id: str, device: dict[str, Any]) -> None:
        self.hass = hass
        self._entry_id = entry_id

        data = device["data"]
        self.device = data

        self._dvce_id = data.get("dvceID")
        self._usr_id = data.get("usrId")

        self._attr_device_info = device.get("ha_dev_info")

    async def _get_session_and_csrf(self):
        entry_data = self.hass.data[DOMAIN].get(self._entry_id, {})
        session = entry_data.get(DATA_SESSION) or entry_data.get("session")
        csrf_token = entry_data.get("_csrf")

        if session is None:
            _LOGGER.error("No session found for entry_id=%s", self._entry_id)
            return None, None

        if not csrf_token:
            _LOGGER.debug("No CSRF token cached; attempting to fetch a new one.")
            await fetch_csrf(self.hass, session, self._entry_id)
            csrf_token = self.hass.data[DOMAIN][self._entry_id].get("_csrf")

        return session, csrf_token

    async def _post_operation(self, operation: str, extra: dict[str, Any] | None = None) -> bool:
        session, csrf_token = await self._get_session_and_csrf()
        if session is None or not csrf_token:
            _LOGGER.error("Missing session/csrf for entry_id=%s", self._entry_id)
            return False

        payload: dict[str, Any] = {
            "dvceId": self._dvce_id,
            "operation": operation,
            "usrId": self._usr_id,
        }
        if extra:
            payload.update(extra)

        url = f"https://smartthingsfind.samsung.com/dm/addOperation.do?_csrf={csrf_token}"

        try:
            async with session.post(url, json=payload) as response:
                txt = await response.text()
                _LOGGER.debug("Operation=%s HTTP=%s payload=%s resp=%s", operation, response.status, payload, txt)

                if response.status == 200:
                    return True

                _LOGGER.warning("Operation %s failed (HTTP %s). Refreshing CSRF.", operation, response.status)
                await fetch_csrf(self.hass, session, self._entry_id)
                return False

        except Exception as err:
            _LOGGER.exception("Exception while posting operation %s: %s", operation, err)
            return False

    async def _kick_refresh(self) -> None:
        """Force coordinator refresh so sensors reflect latest server state.

        Do one immediate refresh, then schedule delayed refreshes in background.
        """
        coordinator = self.hass.data[DOMAIN][self._entry_id].get(DATA_COORDINATOR)
        if coordinator is None:
            return

        try:
            await coordinator.async_request_refresh()
        except Exception as err:
            _LOGGER.debug("Immediate coordinator refresh failed: %s", err)
            return

        async def _delayed_refresh(delay_s: int) -> None:
            try:
                await asyncio.sleep(delay_s)
                await coordinator.async_request_refresh()
            except Exception as err:
                _LOGGER.debug("Delayed refresh (%ss) failed: %s", delay_s, err)

        # STF 서버 반영 지연 대비(버튼 응답은 빠르게 종료)
        self.hass.async_create_task(_delayed_refresh(2))
        self.hass.async_create_task(_delayed_refresh(6))


class RingStartButton(_STFOperationButton):
    _attr_icon = "mdi:volume-high"

    def __init__(self, hass: HomeAssistant, entry_id: str, device: dict[str, Any]) -> None:
        super().__init__(hass, entry_id, device)
        model_name = self.device.get("modelName", "SmartThings Find Device")
        self._attr_unique_id = f"stf_ring_start_{self._dvce_id}"
        self._attr_name = f"{model_name} Ring"

    async def async_press(self) -> None:
        ok = await self._post_operation(
            OP_RING,
            {
                "status": "start",
                "lockMessage": "Home Assistant is ringing your device!",
            },
        )
        if ok:
            await self._kick_refresh()


class RingStopButton(_STFOperationButton):
    _attr_icon = "mdi:volume-mute"

    def __init__(self, hass: HomeAssistant, entry_id: str, device: dict[str, Any]) -> None:
        super().__init__(hass, entry_id, device)
        model_name = self.device.get("modelName", "SmartThings Find Device")
        self._attr_unique_id = f"stf_ring_stop_{self._dvce_id}"
        self._attr_name = f"{model_name} Stop Ring"

    async def async_press(self) -> None:
        ok = await self._post_operation(OP_RING, {"status": "stop"})
        if ok:
            await self._kick_refresh()


class UpdateLocationButton(_STFOperationButton):
    _attr_icon = "mdi:refresh"

    def __init__(self, hass: HomeAssistant, entry_id: str, device: dict[str, Any]) -> None:
        super().__init__(hass, entry_id, device)
        model_name = self.device.get("modelName", "SmartThings Find Device")
        self._attr_unique_id = f"stf_update_location_{self._dvce_id}"
        self._attr_name = f"{model_name} Update Location"

    def _mark_waiting_server_timestamp(self) -> None:
        """Mark that user requested server gps_date update, so Last update sensor can show waiting state."""
        entry_data = self.hass.data[DOMAIN].get(self._entry_id, {})
        coordinator = entry_data.get(DATA_COORDINATOR)
        reqs = entry_data.setdefault("last_update_requests", {})

        res = None
        if coordinator and getattr(coordinator, "data", None):
            res = coordinator.data.get(self._dvce_id)

        prev_gps_date = (((res or {}).get("used_loc") or {}).get("gps_date")) if res else None
        reqs[self._dvce_id] = {
            "requested_at": dt_util.utcnow(),
            "prev_gps_date": prev_gps_date,
        }

    def _clear_waiting(self) -> None:
        entry_data = self.hass.data[DOMAIN].get(self._entry_id, {})
        reqs = entry_data.get("last_update_requests", {})
        if isinstance(reqs, dict):
            reqs.pop(self._dvce_id, None)

    async def async_press(self) -> None:
        # ✅ 누른 즉시 “서버 업데이트 시간 받아오는 중” 상태를 Last update 센서에 노출
        self._mark_waiting_server_timestamp()

        ok = await self._post_operation(OP_CHECK_CONNECTION_WITH_LOCATION)
        if ok:
            await self._kick_refresh()
            return

        # 실패면 대기 상태 제거(사용자에게 계속 대기 표시는 불필요)
        self._clear_waiting()
