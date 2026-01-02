from __future__ import annotations

import logging
from datetime import datetime, timedelta
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

        # ✅ Update Location UX: 서버 last update(gps_date) "가져오는 중" 상태 저장
        # - _last_update_fetch: dvce_id -> {"old": iso/None, "started": iso, "attempts": int}
        # - _last_update_fetch_result: dvce_id -> "fetching"|"ok"|"timeout"
        self._last_update_fetch: dict[str, dict[str, Any]] = {}
        self._last_update_fetch_result: dict[str, str] = {}

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

    def mark_pending_last_update(self, dvce_id: str, old_gps_date: Any | None) -> None:
        """Mark that we are waiting for server gps_date to change for this device."""
        old_iso = old_gps_date.isoformat() if hasattr(old_gps_date, "isoformat") and old_gps_date else None
        self._last_update_fetch[str(dvce_id)] = {
            "old": old_iso,
            "started": datetime.utcnow().isoformat(),
            "attempts": 0,
        }
        self._last_update_fetch_result[str(dvce_id)] = "fetching"
        self.async_update_listeners()

    def get_pending_last_update(self, dvce_id: str) -> dict[str, Any] | None:
        return self._last_update_fetch.get(str(dvce_id))

    def mark_last_update_timeout(self, dvce_id: str) -> None:
        """Stop fetching indicator with timeout result."""
        dvce_id = str(dvce_id)
        if dvce_id in self._last_update_fetch:
            self._last_update_fetch.pop(dvce_id, None)
            self._last_update_fetch_result[dvce_id] = "timeout"
            self.async_update_listeners()

    @staticmethod
    def _extract_server_gps_date(tag_data: dict[str, Any] | None):
        if not tag_data:
            return None
        loc = tag_data.get("used_loc") or {}
        return loc.get("gps_date")

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data for all devices."""
        try:
            results: dict[str, Any] = {}
            prev_data_all = self.data or {}

            for dev in self.devices:
                dev_data = dev["data"]
                dev_id = str(dev_data.get("dvceID"))

                try:
                    tag_data = await get_device_location(self.hass, self.session, dev_data, self.entry_id or "")
                except ConfigEntryAuthFailed:
                    # 1회 CSRF 재발급 후 재시도
                    await fetch_csrf(self.hass, self.session, self.entry_id)
                    tag_data = await get_device_location(self.hass, self.session, dev_data, self.entry_id or "")

                # ✅ 안정화: 간헐적으로 gps_date가 빠져도 이전 값을 유지
                prev_tag = prev_data_all.get(dev_id) if prev_data_all else None
                prev_loc = (prev_tag or {}).get("used_loc") or {}
                prev_gps_date = prev_loc.get("gps_date")

                new_loc = (tag_data or {}).get("used_loc") or {}
                if prev_gps_date is not None and "gps_date" not in new_loc:
                    new_loc = dict(new_loc)
                    new_loc["gps_date"] = prev_gps_date
                    tag_data = dict(tag_data or {})
                    tag_data["used_loc"] = new_loc

                # ✅ pending 처리: 서버 gps_date가 "변했을 때만" fetching 해제
                pending = self._last_update_fetch.get(dev_id)
                if pending is not None:
                    try:
                        pending["attempts"] = int(pending.get("attempts", 0)) + 1
                    except Exception:
                        pending["attempts"] = 1

                    old_iso = pending.get("old")
                    old_dt = None
                    if old_iso:
                        try:
                            old_dt = datetime.fromisoformat(old_iso)
                        except Exception:
                            old_dt = None

                    new_dt = self._extract_server_gps_date(tag_data)

                    changed = (old_dt is not None and new_dt is not None and new_dt != old_dt) or (
                        old_dt is None and new_dt is not None
                    )
                    if changed:
                        self._last_update_fetch.pop(dev_id, None)
                        self._last_update_fetch_result[dev_id] = "ok"
                        self.async_update_listeners()

                results[dev_id] = tag_data

            # ✅ 추가(최소 변경): STF가 Set-Cookie로 세션을 회전시키는 경우를 대비해
            # refresh 성공 시점마다 최신 쿠키를 entry에 저장(재부팅 후 '바로 fail' 방지에 효과적)
            if self.entry is not None:
                try:
                    await persist_cookie_to_entry(self.hass, self.entry, self.session)
                except Exception as err:
                    _LOGGER.debug("cookie persist after update failed: %s", err)

            return results

        except ConfigEntryAuthFailed:
            raise
        except Exception as err:
            raise UpdateFailed(f"Error fetching SmartThings Find data: {err}") from err
