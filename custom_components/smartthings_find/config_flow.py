"""Config flow for SmartThings Find."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.exceptions import ConfigEntryAuthFailed

from .const import (
    DOMAIN,
    CONF_COOKIE,
    CONF_UPDATE_INTERVAL,
    CONF_UPDATE_INTERVAL_DEFAULT,
    CONF_ACTIVE_MODE_SMARTTAGS,
    CONF_ACTIVE_MODE_SMARTTAGS_DEFAULT,
    CONF_ACTIVE_MODE_OTHERS,
    CONF_ACTIVE_MODE_OTHERS_DEFAULT,
    CONF_ST_IDENTIFIER,
)
from .utils import (
    parse_cookie_header,
    apply_cookies_to_session,
    make_session,
    fetch_csrf,
)

_LOGGER = logging.getLogger(__name__)


STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_COOKIE): str,
    }
)

OPTIONS_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_UPDATE_INTERVAL, default=CONF_UPDATE_INTERVAL_DEFAULT): vol.Coerce(int),
        vol.Optional(CONF_ACTIVE_MODE_SMARTTAGS, default=CONF_ACTIVE_MODE_SMARTTAGS_DEFAULT): bool,
        vol.Optional(CONF_ACTIVE_MODE_OTHERS, default=CONF_ACTIVE_MODE_OTHERS_DEFAULT): bool,
        vol.Optional(CONF_ST_IDENTIFIER): str,
    }
)


class SmartThingsFindConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for SmartThings Find."""

    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_CLOUD_POLL

    def __init__(self) -> None:
        self._reauth_entry: ConfigEntry | None = None

    async def _validate_cookie(self, cookie_line: str) -> str | None:
        """Validate cookie by fetching csrf. Return error key or None."""
        cookie_line = (cookie_line or "").strip()
        if not cookie_line:
            return "missing_cookie"

        cookies = parse_cookie_header(cookie_line)
        if not cookies:
            return "invalid_cookie"

        session = make_session(self.hass)
        try:
            apply_cookies_to_session(session, cookies)
            await fetch_csrf(self.hass, session, "")  # entry_id 없어도 체크 가능하도록 빈 값
            return None
        except ConfigEntryAuthFailed:
            return "invalid_cookie"
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Cookie validation failed: %s", err, exc_info=True)
            return "cannot_connect"
        finally:
            try:
                await session.close()
            except Exception:  # noqa: BLE001
                pass

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        """Initial setup."""
        errors: dict[str, str] = {}

        if user_input is not None:
            cookie_line = user_input.get(CONF_COOKIE, "")
            err = await self._validate_cookie(cookie_line)
            if err is None:
                # ✅ 최초 설정: cookie만 entry.data에 저장 (options는 options flow에서)
                return self.async_create_entry(
                    title="SmartThings Find",
                    data={CONF_COOKIE: cookie_line.strip()},
                )
            errors["base"] = err

        return self.async_show_form(step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors)

    async def async_step_reauth(self, user_input: dict[str, Any] | None = None):
        """Handle reauth initiated by ConfigEntryAuthFailed."""
        entry_id = self.context.get("entry_id")
        if entry_id:
            self._reauth_entry = self.hass.config_entries.async_get_entry(entry_id)

        return await self.async_step_reauth_confirm(user_input)

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None):
        """Confirm reauth and ask for a new cookie."""
        errors: dict[str, str] = {}

        if user_input is not None:
            cookie_line = user_input.get(CONF_COOKIE, "")
            err = await self._validate_cookie(cookie_line)

            if err is None and self._reauth_entry is not None:
                new_data = dict(self._reauth_entry.data)
                new_data[CONF_COOKIE] = cookie_line.strip()

                # ✅ 최신 HA: async_update_reload_and_abort 사용 가능
                if hasattr(self, "async_update_reload_and_abort"):
                    return self.async_update_reload_and_abort(
                        self._reauth_entry,
                        data=new_data,
                        reason="reauth_successful",
                    )

                # ✅ 구버전 호환: 직접 업데이트 + reload + abort
                self.hass.config_entries.async_update_entry(self._reauth_entry, data=new_data)
                await self.hass.config_entries.async_reload(self._reauth_entry.entry_id)
                return self.async_abort(reason="reauth_successful")

            errors["base"] = err or "unknown"

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=STEP_USER_SCHEMA,
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry):
        return SmartThingsFindOptionsFlow(config_entry)


class SmartThingsFindOptionsFlow(config_entries.OptionsFlow):
    """Handle options flow."""

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry  # ✅ self.config_entry에 대입하지 않음(HA 버전에 따라 read-only일 수 있음)

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        options = dict(self._entry.options)

        if user_input is not None:
            options.update(user_input)
            return self.async_create_entry(title="", data=options)

        # 현재 옵션값 반영해서 스키마 default 채움
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
                vol.Optional(
                    CONF_ST_IDENTIFIER,
                    default=options.get(CONF_ST_IDENTIFIER, ""),
                ): str,
            }
        )

        return self.async_show_form(step_id="init", data_schema=schema)
