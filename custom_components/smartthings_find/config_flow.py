"""Config flow for SmartThings Find."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback

from .const import DOMAIN, CONF_JSESSIONID
from .utils import do_login_stage_one, do_login_stage_two, gen_qr_code_base64

_LOGGER = logging.getLogger(__name__)


# 옵션 키(레포 버전에 따라 없을 수 있어 안전하게 fallback)
try:
    from .const import CONF_UPDATE_INTERVAL, CONF_ACTIVE_MODE  # type: ignore
except Exception:  # pragma: no cover
    CONF_UPDATE_INTERVAL = "update_interval"
    CONF_ACTIVE_MODE = "active_mode"


DEFAULT_UPDATE_INTERVAL = 120  # seconds
DEFAULT_ACTIVE_MODE = True


class SmartThingsFindConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for SmartThings Find."""

    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_CLOUD_POLL

    def __init__(self) -> None:
        self._reauth_entry: ConfigEntry | None = None  # ✅ reauth 대상 entry를 직접 저장
        self._task_stage_one: asyncio.Task | None = None
        self._task_stage_two: asyncio.Task | None = None

        self._qr_url: str | None = None
        self._session = None
        self._jsessionid: str | None = None
        self._error: str | None = None

    async def _do_stage_one(self) -> None:
        """Login stage 1: create session + fetch QR url."""
        _LOGGER.debug("Running login stage 1")
        try:
            stage_one_res = await do_login_stage_one(self.hass)
            if stage_one_res is not None:
                self._session, self._qr_url = stage_one_res
            else:
                self._error = "login_stage_one_failed"
                _LOGGER.warning("Login stage 1 failed")
        except Exception as e:  # pragma: no cover
            self._error = "login_stage_one_failed"
            _LOGGER.error("Exception in stage 1: %s", e, exc_info=True)

    async def _do_stage_two(self) -> None:
        """Login stage 2: poll QR + get JSESSIONID."""
        _LOGGER.debug("Running login stage 2")
        try:
            if not self._session:
                self._error = "login_stage_two_failed"
                return

            stage_two_res = await do_login_stage_two(self._session)
            if stage_two_res is not None:
                self._jsessionid = stage_two_res
                _LOGGER.info("Login successful")
            else:
                self._error = "login_stage_two_failed"
                _LOGGER.warning("Login stage 2 failed")
        except Exception as e:  # pragma: no cover
            self._error = "login_stage_two_failed"
            _LOGGER.error("Exception in stage 2: %s", e, exc_info=True)

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        """First step: start stage 1 and show progress."""
        # user_input은 사용하지 않지만, reauth_confirm에서 동일 step으로 진입시키기 위해 유지
        if self._task_stage_one is None:
            self._task_stage_one = self.hass.async_create_task(self._do_stage_one())

        if not self._task_stage_one.done():
            return self.async_show_progress(
                progress_action="task_stage_one",
                progress_task=self._task_stage_one,
            )

        # stage 1 완료
        if self._error:
            # finish에서 에러 표기
            return self.async_show_progress_done(next_step_id="finish")

        return self.async_show_progress_done(next_step_id="auth_stage_two")

    async def async_step_auth_stage_two(self, user_input: dict[str, Any] | None = None):
        """Second step: start stage 2 and show QR / progress."""
        if self._task_stage_two is None:
            self._task_stage_two = self.hass.async_create_task(self._do_stage_two())

        if not self._task_stage_two.done():
            qr_url = self._qr_url or ""
            return self.async_show_progress(
                progress_action="task_stage_two",
                progress_task=self._task_stage_two,
                description_placeholders={
                    "qr_code": gen_qr_code_base64(qr_url) if qr_url else "",
                    "url": qr_url,
                    "code": (qr_url.split("/")[-1] if "/" in qr_url else ""),
                },
            )

        return self.async_show_progress_done(next_step_id="finish")

    async def async_step_finish(self, user_input: dict[str, Any] | None = None):
        """Final step: create entry or update existing entry on reauth."""
        if self._error:
            # strings.json에 정의된 base error key를 쓰는 방식(기존 방식 유지)
            return self.async_show_form(step_id="finish", errors={"base": self._error})

        data = {CONF_JSESSIONID: self._jsessionid}

        # ✅ reauth의 경우: 기존 entry 업데이트 후 reload + abort
        if self._reauth_entry is not None:
            return self.async_update_reload_and_abort(self._reauth_entry, data=data)

        # 최초 설정
        return self.async_create_entry(title="SmartThings Find", data=data)

    async def async_step_reauth(self, user_input: dict[str, Any] | None = None):
        """Reauth step called by Home Assistant when ConfigEntryAuthFailed occurs."""
        entry_id = self.context.get("entry_id")
        if entry_id:
            self._reauth_entry = self.hass.config_entries.async_get_entry(entry_id)

        # reauth에서는 이전 태스크/상태 초기화
        self._task_stage_one = None
        self._task_stage_two = None
        self._qr_url = None
        self._session = None
        self._jsessionid = None
        self._error = None

        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None):
        """Show a confirmation form, then proceed to normal login."""
        if user_input is None:
            return self.async_show_form(
                step_id="reauth_confirm",
                data_schema=vol.Schema({}),
            )
        # 확인 누르면 user step으로 이어서 QR 로그인 진행
        return await self.async_step_user()


class SmartThingsFindOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow (avoid setting read-only properties in newer HA)."""

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry  # ✅ self.config_entry에 대입하지 않음(최신 HA에서 깨질 수 있음)

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        options = dict(self._entry.options)

        if user_input is not None:
            options.update(user_input)
            return self.async_create_entry(title="", data=options)

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_UPDATE_INTERVAL,
                    default=options.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL),
                ): vol.Coerce(int),
                vol.Optional(
                    CONF_ACTIVE_MODE,
                    default=options.get(CONF_ACTIVE_MODE, DEFAULT_ACTIVE_MODE),
                ): bool,
            }
        )

        return self.async_show_form(step_id="init", data_schema=schema)


@callback
def async_get_options_flow(config_entry: ConfigEntry) -> SmartThingsFindOptionsFlowHandler:
    """Return the options flow handler."""
    return SmartThingsFindOptionsFlowHandler(config_entry)


# HA가 이 함수명을 찾는 경우도 있어서(레포/HA 버전 차이 대응) 같이 제공
SmartThingsFindConfigFlow.async_get_options_flow = staticmethod(async_get_options_flow)  # type: ignore
