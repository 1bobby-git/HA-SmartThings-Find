"""Config flow for SmartThings Find (Cookie-based)."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback

from .const import DOMAIN, CONF_COOKIE
from .utils import parse_cookie_header

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema({vol.Required(CONF_COOKIE): str})


class SmartThingsFindConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for SmartThings Find."""

    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_CLOUD_POLL

    def __init__(self) -> None:
        self._reauth_entry: ConfigEntry | None = None

    def _basic_cookie_check(self, cookie_line: str) -> str | None:
        """Only do basic/syntax checks here. Real auth is done in async_setup_entry."""
        cookie_line = (cookie_line or "").strip()
        if not cookie_line:
            return "missing_cookie"

        cookies = parse_cookie_header(cookie_line)
        if not cookies:
            return "invalid_cookie"

        # 여기서는 통과(실제 유효성은 setup_entry에서 판별)
        return None

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        """Initial setup step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            cookie_line = user_input.get(CONF_COOKIE, "")
            err = self._basic_cookie_check(cookie_line)
            if err is None:
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
        """Ask for cookie again when session expired."""
        errors: dict[str, str] = {}

        if user_input is not None:
            cookie_line = user_input.get(CONF_COOKIE, "")
            err = self._basic_cookie_check(cookie_line)
            if err is None and self._reauth_entry is not None:
                new_data = dict(self._reauth_entry.data)
                new_data[CONF_COOKIE] = cookie_line.strip()

                if hasattr(self, "async_update_reload_and_abort"):
                    return self.async_update_reload_and_abort(
                        self._reauth_entry,
                        data=new_data,
                        reason="reauth_successful",
                    )

                # 구버전 호환
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
        # 기존 OptionsFlow가 별도 파일/구현에 있다면 그걸 그대로 쓰세요.
        # 이 레포 구조에 따라 OptionsFlow가 여기 없을 수도 있으니,
        # 현재 프로젝트에 OptionsFlow가 있다면 그 구현을 유지하세요.
        return None
