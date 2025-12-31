from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult

from .const import (
    DOMAIN,
    CONF_COOKIE,
    CONF_UPDATE_INTERVAL,
    CONF_UPDATE_INTERVAL_DEFAULT,
    CONF_ACTIVE_MODE_SMARTTAGS,
    CONF_ACTIVE_MODE_SMARTTAGS_DEFAULT,
    CONF_ACTIVE_MODE_OTHERS,
    CONF_ACTIVE_MODE_OTHERS_DEFAULT,
)

# utils는 레포에 이미 있는 함수 시그니처를 최대한 존중해서 호출함
# (0.3.16 이후 fetch_csrf/get_devices가 entry_id를 받는 형태로 바뀐 것으로 보임)
from .utils import (  # type: ignore
    parse_cookie_header,
    apply_cookies_to_session,
    fetch_csrf,
    make_session,
    get_devices,
)

_LOGGER = logging.getLogger(__name__)


STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_COOKIE): str,
    }
)

STEP_OPTIONS_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_UPDATE_INTERVAL, default=CONF_UPDATE_INTERVAL_DEFAULT): vol.Coerce(int),
        vol.Optional(CONF_ACTIVE_MODE_SMARTTAGS, default=CONF_ACTIVE_MODE_SMARTTAGS_DEFAULT): bool,
        vol.Optional(CONF_ACTIVE_MODE_OTHERS, default=CONF_ACTIVE_MODE_OTHERS_DEFAULT): bool,
    }
)


async def _validate_cookie(hass: HomeAssistant, cookie_line: str, flow_id: str) -> None:
    """Validate cookie by performing a minimal auth-required flow."""
    cookies = parse_cookie_header(cookie_line)
    if not cookies:
        raise ValueError("invalid_cookie")

    session = make_session(hass)
    try:
        apply_cookies_to_session(session, cookies)

        # 0.3.16+ 기준: fetch_csrf(hass, session, entry_id) 형태로 보임
        await fetch_csrf(hass, session, flow_id)

        # devices fetch로 실제 로그인/권한 확인
        await get_devices(hass, session, flow_id)

    finally:
        await session.close()


class SmartThingsFindConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for SmartThings Find."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            cookie_line = (user_input.get(CONF_COOKIE) or "").strip()

            if not cookie_line:
                errors[CONF_COOKIE] = "required"
            else:
                try:
                    # flow_id를 entry_id처럼 써서 csrf/devices 호출 (시그니처 호환 목적)
                    await _validate_cookie(self.hass, cookie_line, getattr(self, "flow_id", "config_flow"))
                except ValueError as e:
                    if str(e) == "invalid_cookie":
                        errors["base"] = "invalid_cookie"
                    else:
                        errors["base"] = "unknown"
                except Exception:  # pylint: disable=broad-except
                    _LOGGER.exception("Cookie validation failed")
                    errors["base"] = "auth_failed"

            if not errors:
                # ✅ SmartThings ID 선택 단계 제거: 바로 엔트리 생성
                return self.async_create_entry(
                    title="SmartThings Find",
                    data={
                        CONF_COOKIE: cookie_line,
                    },
                    options={
                        CONF_UPDATE_INTERVAL: CONF_UPDATE_INTERVAL_DEFAULT,
                        CONF_ACTIVE_MODE_SMARTTAGS: CONF_ACTIVE_MODE_SMARTTAGS_DEFAULT,
                        CONF_ACTIVE_MODE_OTHERS: CONF_ACTIVE_MODE_OTHERS_DEFAULT,
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    @staticmethod
    @config_entries.callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> config_entries.OptionsFlow:
        return SmartThingsFindOptionsFlowHandler(config_entry)


class SmartThingsFindOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow for SmartThings Find."""

    def __init__(self, entry: config_entries.ConfigEntry) -> None:
        self._entry = entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            # 업데이트 간격 sanity check
            interval = user_input.get(CONF_UPDATE_INTERVAL, CONF_UPDATE_INTERVAL_DEFAULT)
            try:
                interval = int(interval)
                if interval < 10:
                    errors["base"] = "interval_too_small"
            except Exception:  # pylint: disable=broad-except
                errors["base"] = "invalid_interval"

            if not errors:
                return self.async_create_entry(
                    title="",
                    data={
                        CONF_UPDATE_INTERVAL: interval,
                        CONF_ACTIVE_MODE_SMARTTAGS: bool(user_input.get(CONF_ACTIVE_MODE_SMARTTAGS)),
                        CONF_ACTIVE_MODE_OTHERS: bool(user_input.get(CONF_ACTIVE_MODE_OTHERS)),
                    },
                )

        # defaults from existing entry
        options = dict(self._entry.options)
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_UPDATE_INTERVAL,
                    default=options.get(CONF_UPDATE_INTERVAL, CONF_UPDATE_INTERVAL_DEFAULT),
                ): vol.Coerce(int),
                vol.Optional(
                    CONF_ACTIVE_MODE_SMARTTAGS,
                    default=options.get(CONF_ACTIVE_MODE_SMARTTAGS, CONF_ACTIVE_MODE_SMARTTAGS_DEFAULT),
                ): bool,
                vol.Optional(
                    CONF_ACTIVE_MODE_OTHERS,
                    default=options.get(CONF_ACTIVE_MODE_OTHERS, CONF_ACTIVE_MODE_OTHERS_DEFAULT),
                ): bool,
            }
        )

        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
