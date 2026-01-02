from __future__ import annotations

import logging
from datetime import datetime, timedelta
from time import monotonic
from typing import Any, Callable

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN
from .utils import fetch_csrf, get_device_location

_LOGGER = logging.getLogger(__name__)

# 인증 실패 시 스팸 방지를 위해 업데이트 간격을 길게 늘림
AUTH_FAILED_UPDATE_INTERVAL = timedelta(hours=6)

# 같은 인증 실패 로그를 너무 자주 찍지 않도록(초 단위)
AUTH_LOG_THROTTLE_SEC = 60 * 30  # 30분

# keepalive 주기: 너무 자주 할 필요 없음 (세션 감지 + csrf 갱신 목적)
KEEPALIVE_INTERVAL = timedelta(minutes=15)


class SmartThingsFindCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """SmartThings Find data coordinator."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        session: aiohttp.ClientSession,
        devices: list[dict[str, Any]],
        update_interval_s: int,
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.entry_id = entry.entry_id
        self.session = session
        self.devices = devices

        self._normal_update_interval = timedelta(seconds=int(update_interval_s))
        self._auth_failed = False
        self._reauth_triggered = False
        self._last_auth_log_mono = 0.0

        self._unsub_keepalive: Callable[[], None] | None = None
        self._keepalive_running = False

        super().__init__(
            hass=hass,
            logger=_LOGGER,
            name=DOMAIN,
            update_interval=self._normal_update_interval,
        )

    # ---------- lifecycle ----------

    def start_keepalive(self) -> None:
        """Start periodic keepalive."""
        if self._unsub_keepalive is not None:
            return
        # interval callback은 datetime 인자를 받으므로 wrapper 사용
        self._unsub_keepalive = async_track_time_interval(
            self.hass,
            self._async_keepalive_tick,
            KEEPALIVE_INTERVAL,
        )
        _LOGGER.debug("Keepalive scheduled every %s", KEEPALIVE_INTERVAL)

    async def async_shutdown(self) -> None:
        """Stop keepalive and cleanup."""
        if self._unsub_keepalive is not None:
            self._unsub_keepalive()
            self._unsub_keepalive = None
        self._keepalive_running = False

    # ---------- auth/log helpers ----------

    def _should_log_auth(self) -> bool:
        now = monotonic()
        if now - self._last_auth_log_mono >= AUTH_LOG_THROTTLE_SEC:
            self._last_auth_log_mono = now
            return True
        return False

    def _mark_auth_failed(self, err: Exception) -> None:
        """Mark auth as failed and slow down polling to avoid log spam."""
        if not self._auth_failed:
            self._auth_failed = True
            # 폴링 간격을 크게 늘려 스팸 방지
            self.async_set_update_interval(AUTH_FAILED_UPDATE_INTERVAL)

        if self._should_log_auth():
            _LOGGER.warning(
                "keepalive failed (reauth likely needed): %s",
                err,
            )
        else:
            _LOGGER.debug("keepalive/auth still failing: %s", err)

    def _mark_auth_ok(self) -> None:
        """Reset auth-failed state and restore normal polling interval."""
        if self._auth_failed:
            _LOGGER.info("Authentication recovered; restoring normal polling interval.")
            self._auth_failed = False
            self._reauth_triggered = False
            self.async_set_update_interval(self._normal_update_interval)

    # ---------- keepalive ----------

    async def _async_keepalive_tick(self, _now: datetime) -> None:
        """Periodic tick wrapper for keepalive."""
        # 동시에 여러 tick이 겹치지 않도록 가드
        if self._keepalive_running:
            return
        self._keepalive_running = True
        try:
            await self.async_keepalive()
        finally:
            self._keepalive_running = False

    async def async_keepalive(self) -> None:
        """
        Keep the session alive by refreshing csrf.

        - 여기서 ConfigEntryAuthFailed를 raise하면 스케줄러 쪽에서 "Task exception" 류가 늘 수 있어
          상태만 마킹하고, reauth 트리거는 coordinator refresh로 유도합니다.
        """
        try:
            await fetch_csrf(self.hass, self.session, self.entry_id)
            self._mark_auth_ok()
        except ConfigEntryAuthFailed as err:
            self._mark_auth_failed(err)

            # reauth를 트리거하기 위해 refresh 요청(예외는 coordinator 내부에서 처리됨)
            if not self._reauth_triggered:
                self._reauth_triggered = True
                self.hass.async_create_task(self.async_request_refresh())
        except Exception as err:  # noqa: BLE001
            # keepalive에서 일반 네트워크 오류는 debug 정도로만(스팸 방지)
            _LOGGER.debug("keepalive transient error: %s", err)

    # ---------- main update ----------

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from SmartThings Find."""
        try:
            tags: dict[str, Any] = {}
            _LOGGER.debug("Updating SmartThings Find locations for %d devices", len(self.devices))

            for device in self.devices:
                dev_data = device.get("data") or device
                tag_data = await get_device_location(
                    self.hass,
                    self.session,
                    dev_data,
                    self.entry_id,
                )

                dvce_id = (
                    dev_data.get("dvceID")
                    or dev_data.get("dvceId")
                    or dev_data.get("dvceid")
                )
                if dvce_id:
                    tags[str(dvce_id)] = tag_data

            # 정상 수집 성공 => auth 상태 복구
            self._mark_auth_ok()
            return tags

        except ConfigEntryAuthFailed as err:
            # 401 Logout / chkLogin fail 등 인증 이슈
            self._mark_auth_failed(err)

            # ✅ reauth는 한 번만 트리거
            if not self._reauth_triggered:
                self._reauth_triggered = True
                raise

            # ✅ reauth 이미 트리거 됐으면 스팸 줄이기 위해 UpdateFailed로 변경
            raise UpdateFailed("Authentication required (reauth in progress)") from err

        except Exception as err:
            raise UpdateFailed(f"Error fetching {DOMAIN} data: {err}") from err
