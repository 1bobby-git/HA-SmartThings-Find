from __future__ import annotations

import logging
from datetime import timedelta, datetime
from typing import Any

import pytz

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.exceptions import ConfigEntryAuthFailed

from .utils import get_device_location, persist_cookie_to_entry

_LOGGER = logging.getLogger(__name__)


class SmartThingsFindCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Fetch locations/battery for SmartThings Find devices."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        session,
        devices: list[dict[str, Any]],
        update_interval_s: int,
    ) -> None:
        super().__init__(
            hass=hass,
            logger=_LOGGER,
            name="smartthings_find",
            update_interval=timedelta(seconds=max(15, int(update_interval_s))),
        )
        self.entry = entry
        self.session = session
        self.devices = devices

        # 버튼/센서에서 참조하는 pending 구조 유지
        self._last_update_fetch: dict[str, dict[str, Any]] = {}
        self._last_update_fetch_result: dict[str, str] = {}

    async def async_shutdown(self) -> None:
        """Called from __init__.py unload."""
        # 현재는 별도 타이머 없음(keepalive는 __init__.py에서 관리)
        return

    # -------- pending helpers (버튼/센서 호환) --------

    def mark_pending_last_update(self, dvce_id: str, old_gps_date) -> None:
        self._last_update_fetch[dvce_id] = {
            "started": datetime.now(tz=pytz.UTC),
            "attempts": 0,
            "old": old_gps_date,
        }
        # 결과는 fetching으로 초기화
        self._last_update_fetch_result[dvce_id] = "fetching"

    def get_pending_last_update(self, dvce_id: str) -> dict[str, Any] | None:
        return self._last_update_fetch.get(dvce_id)

    def mark_last_update_timeout(self, dvce_id: str) -> None:
        # pending 제거 + timeout 결과 남김
        self._last_update_fetch.pop(dvce_id, None)
        self._last_update_fetch_result[dvce_id] = "timeout"

    def _maybe_clear_pending_if_changed(self, dvce_id: str, new_gps_date) -> None:
        pending = self._last_update_fetch.get(dvce_id)
        if not pending:
            return
        pending["attempts"] = int(pending.get("attempts", 0)) + 1

        old = pending.get("old")
        if old is None:
            # old가 없으면 new가 생기는 순간 ok
            if new_gps_date is not None:
                self._last_update_fetch.pop(dvce_id, None)
                self._last_update_fetch_result[dvce_id] = "ok"
            return

        # old/new 비교
        try:
            if new_gps_date and new_gps_date != old and new_gps_date > old:
                self._last_update_fetch.pop(dvce_id, None)
                self._last_update_fetch_result[dvce_id] = "ok"
        except Exception:
            # 비교 실패 시에는 pending 유지
            return

    # -------- DataUpdateCoordinator --------

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            results: dict[str, Any] = {}

            for dev in self.devices:
                dev_data = dev.get("data") or {}
                dvce_id = dev_data.get("dvceID")
                if not dvce_id:
                    continue

                res = await get_device_location(
                    hass=self.hass,
                    session=self.session,
                    dev_data=dev_data,
                    entry_id=self.entry.entry_id,
                )

                if res is None:
                    # 기존 안정화 성격 유지: 개별 실패는 None으로 두되 전체 실패로 만들지 않음
                    results[str(dvce_id)] = {}
                    continue

                results[str(dvce_id)] = res

                # pending last update 처리(서버 gps_date 변화 감지)
                loc = (res or {}).get("used_loc") or {}
                self._maybe_clear_pending_if_changed(str(dvce_id), loc.get("gps_date"))

            # ✅ 쿠키 회전/갱신을 entry에 지속 저장
            try:
                await persist_cookie_to_entry(self.hass, self.entry, self.session)
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("cookie persist after coordinator update failed: %s", err)

            return results

        except ConfigEntryAuthFailed:
            # ✅ 이 예외는 HA가 reauth를 자동 시작할 수 있는 “정석 경로”
            raise
        except Exception as err:
            raise UpdateFailed(f"Failed to update SmartThings Find data: {err}") from err
