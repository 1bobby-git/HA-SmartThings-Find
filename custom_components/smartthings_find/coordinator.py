from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN
from .utils import (
    fetch_csrf,
    get_device_location,
    keepalive_ping,
    persist_cookie_to_entry,
)

_LOGGER = logging.getLogger(__name__)


class SmartThingsFindCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """
    Coordinator that supports BOTH signatures:

    New style:
        SmartThingsFindCoordinator(hass=hass, entry=entry, session=session, devices=devices, update_interval_s=60)

    Old style:
        SmartThingsFindCoordinator(hass, session, devices, 60)
        SmartThingsFindCoordinator(hass, entry, session, devices, 60)
    """

    def __init__(  # noqa: PLR0913
        self,
        hass: HomeAssistant,
        *args: Any,
        entry: ConfigEntry | None = None,
        session: aiohttp.ClientSession | None = None,
        devices: list[dict[str, Any]] | None = None,
        update_interval_s: int | None = None,
        keepalive_interval_s: int = 240,
        **kwargs: Any,
    ) -> None:
        # --- Parse legacy positional args for compatibility ---
        if args:
            if isinstance(args[0], ConfigEntry):
                entry = args[0]
                if len(args) > 1:
                    session = args[1]
                if len(args) > 2:
                    devices = args[2]
                if len(args) > 3 and update_interval_s is None:
                    update_interval_s = int(args[3])
            else:
                session = args[0]
                if len(args) > 1:
                    devices = args[1]
                if len(args) > 2 and update_interval_s is None:
                    update_interval_s = int(args[2])

        if entry is None:
            entry = kwargs.get("config_entry") or kwargs.get("entry")  # type: ignore[assignment]
        if session is None:
            session = kwargs.get("session")
        if devices is None:
            devices = kwargs.get("devices")

        if update_interval_s is None:
            update_interval_s = 60

        # ✅ 브라우저 idle 5~10분 로그아웃 대응:
        # keepalive는 240초 이하로 강제 (너무 짧으면 서버에 부담이니 120~240 권장)
        keepalive_interval_s = min(240, max(90, int(keepalive_interval_s)))

        self.hass = hass
        self.entry = entry
        self.entry_id = entry.entry_id if entry else None

        if session is None:
            raise ValueError("SmartThingsFindCoordinator requires an aiohttp session")
        self.session = session

        self.devices = devices or []

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=max(30, int(update_interval_s))),
        )

        self._keepalive_cancel = async_track_time_interval(
            hass,
            self._async_keepalive,
            timedelta(seconds=keepalive_interval_s),
        )

    async def async_shutdown(self) -> None:
        """Cancel keepalive timer."""
        if self._keepalive_cancel:
            self._keepalive_cancel()
            self._keepalive_cancel = None

    async def _async_keepalive(self, _now) -> None:
        """
        Periodic CSRF refresh + '활동' ping + cookie persist.

        - chkLogin만으로 idle 연장이 안될 수 있어 deviceList ping 추가
        - cookie_jar 갱신분을 entry.data에 저장(재부팅/재로드 시 재입력 확률 감소)
        """
        try:
            await fetch_csrf(self.hass, self.session, self.entry_id)
            if self.entry_id:
                await keepalive_ping(self.hass, self.session, self.entry_id)
            if self.entry is not None:
                await persist_cookie_to_entry(self.hass, self.entry, self.session)

            _LOGGER.debug("keepalive: csrf refreshed + ping ok (+ cookie persisted)")
        except ConfigEntryAuthFailed as e:
            _LOGGER.warning("keepalive failed (reauth likely needed): %s", e)
        except Exception as e:
            _LOGGER.debug("keepalive unexpected error: %s", e)

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data for all devices."""
        try:
            results: dict[str, Any] = {}
            for dev in self.devices:
                dev_data = dev["data"]

                try:
                    tag_data = await get_device_location(self.hass, self.session, dev_data, self.entry_id or "")
                except ConfigEntryAuthFailed:
                    # 1회 CSRF 재발급 후 재시도
                    await fetch_csrf(self.hass, self.session, self.entry_id)
                    tag_data = await get_device_location(self.hass, self.session, dev_data, self.entry_id or "")

                results[str(dev_data.get("dvceID"))] = tag_data

            return results

        except ConfigEntryAuthFailed:
            raise
        except Exception as err:
            raise UpdateFailed(f"Error fetching SmartThings Find data: {err}") from err
