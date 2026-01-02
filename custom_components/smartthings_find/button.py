from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.entity_platform import AddEntitiesCallback

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

    def _start_reauth(self) -> None:
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry:
            entry.async_start_reauth(self.hass)

    async def _get_session_and_csrf(self):
        entry_data = self.hass.data[DOMAIN].get(self._entry_id, {})
        session = entry_data.get(DATA_SESSION) or entry_data.get("session")
        csrf_token = entry_data.get("_csrf")

        if session is None:
            _LOGGER.error("No session found for entry_id=%s", self._entry_id)
            return None, None

        if not csrf_token:
            _LOGGER.debug("No CSRF token cached; attempting to fetch a new one.")
            try:
                await fetch_csrf(self.hass, session, self._entry_id)
            except ConfigEntryAuthFailed:
                _LOGGER.debug("Auth failed while fetching CSRF; starting reauth")
                self._start_reauth()
                return None, None
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
                try:
                    await fetch_csrf(self.hass, session, self._entry_id)
                except ConfigEntryAuthFailed:
                    _LOGGER.debug("Auth failed while refreshing CSRF; starting reauth")
                    self._start_reauth()
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

    def _get_current_server_gps_date(self):
        """Read current server gps_date from coordinator.data (may be None)."""
        coordinator = self.hass.data[DOMAIN][self._entry_id].get(DATA_COORDINATOR)
        if coordinator is None or not coordinator.data:
            return None
        res = coordinator.data.get(self._dvce_id) if coordinator.data else None
        loc = (res or {}).get("used_loc") or {}
        return loc.get("gps_date")

    async def async_press(self) -> None:
        coordinator = self.hass.data[DOMAIN][self._entry_id].get(DATA_COORDINATOR)

        # ✅ pending 시작: '서버 last update(gps_date) 갱신'을 기다린다는 표시
        old_gps_date = self._get_current_server_gps_date()
        if coordinator is not None and hasattr(coordinator, "mark_pending_last_update"):
            try:
                coordinator.mark_pending_last_update(self._dvce_id, old_gps_date)
            except Exception as err:
                _LOGGER.debug("mark_pending_last_update failed: %s", err)

        ok = await self._post_operation(OP_CHECK_CONNECTION_WITH_LOCATION)
        if ok:
            await self._kick_refresh()

            # ✅ 서버 반영이 늦을 수 있어 추가 refresh를 더 걸어줌(너무 공격적이지 않게)
            if coordinator is not None:
                self.hass.async_create_task(self._poll_server_last_update(coordinator))

    async def _poll_server_last_update(self, coordinator) -> None:
        """Poll a few times until server gps_date changes, then stop.
        If not changed within the window, mark timeout so user can see it.
        """
        # _kick_refresh()에서 2s/6s는 이미 돌고 있으니, 여기선 좀 더 긴 구간만
        delays = (15, 30, 45)

        for delay_s in delays:
            try:
                await asyncio.sleep(delay_s)
            except Exception:
                return

            # 이미 성공/해제됐다면 종료
            try:
                if hasattr(coordinator, "get_pending_last_update") and coordinator.get_pending_last_update(self._dvce_id) is None:
                    return
            except Exception:
                # get_pending 실패 시에도 refresh는 시도해봄
                pass

            try:
                await coordinator.async_request_refresh()
            except Exception as err:
                _LOGGER.debug("Poll refresh failed (%ss): %s", delay_s, err)

        # 여기까지 왔으면 아직 pending일 가능성이 있음 → timeout 표기 후 종료
        try:
            if hasattr(coordinator, "mark_last_update_timeout"):
                coordinator.mark_last_update_timeout(self._dvce_id)
        except Exception as err:
            _LOGGER.debug("mark_last_update_timeout failed: %s", err)
