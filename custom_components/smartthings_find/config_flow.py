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
    CONF_COOKIE_INPUT,
    CONF_UPDATE_INTERVAL,
    CONF_UPDATE_INTERVAL_DEFAULT,
    CONF_ACTIVE_MODE_SMARTTAGS,
    CONF_ACTIVE_MODE_SMARTTAGS_DEFAULT,
    CONF_ACTIVE_MODE_OTHERS,
    CONF_ACTIVE_MODE_OTHERS_DEFAULT,
)
from .utils import make_session_with_cookie, fetch_csrf

_LOGGER = logging.getLogger(__name__)

_COOKIE_SCHEMA = vol.Schema({vol.Required(CONF_COOKIE_INPUT): str})


class SmartThingsFindConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_CLOUD_POLL

    def __init__(self) -> None:
        self._reauth_entry: ConfigEntry | None = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        return await self._async_cookie_step(user_input, reauth=False)

    async def async_step_reauth(self, user_input: dict[str, Any] | None = None):
        entry_id = self.context.get("entry_id")
        if entry_id:
            self._reauth_entry = self.hass.config_entries.async_get_entry(entry_id)
        return await self.async_step_reauth_confirm(user_input)

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None):
        return await self._async_cookie_step(user_input, reauth=True)

    async def _async_cookie_step(self, user_input: dict[str, Any] | None, reauth: bool):
        errors: dict[str, str] = {}

        if user_input is not None:
            cookie_header = user_input.get(CONF_COOKIE_INPUT, "")
            try:
                session = await make_session_with_cookie(self.hass, cookie_header)
                await fetch_csrf(self.hass, session)
                await session.close()

                if reauth and self._reauth_entry:
                    new_data = dict(self._reauth_entry.data)
                    new_data[CONF_COOKIE_INPUT] = cookie_header
                    return self.async_update_reload_and_abort(
                        self._reauth_entry,
                        data=new_data,
                        reason="reauth_successful",
                    )

                return self.async_create_entry(
                    title="SmartThings Find",
                    data={CONF_COOKIE_INPUT: cookie_header},
                )

            except ConfigEntryAuthFailed as e:
                _LOGGER.warning("Cookie auth failed: %s", e)
                errors["base"] = "invalid_auth"
            except Exception as e:
                _LOGGER.exception("Unexpected error validating cookie auth: %s", e)
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="reauth_confirm" if reauth else "user",
            data_schema=_COOKIE_SCHEMA,
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry):
        return SmartThingsFindOptionsFlowHandler(config_entry)


class SmartThingsFindOptionsFlowHandler(config_entries.OptionsFlow):
    def __init__(self, config_entry: ConfigEntry) -> None:
        self.config_entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_UPDATE_INTERVAL,
                    default=self.config_entry.options.get(CONF_UPDATE_INTERVAL, CONF_UPDATE_INTERVAL_DEFAULT),
                ): vol.All(vol.Coerce(int), vol.Clamp(min=30)),
                vol.Optional(
                    CONF_ACTIVE_MODE_SMARTTAGS,
                    default=self.config_entry.options.get(CONF_ACTIVE_MODE_SMARTTAGS, CONF_ACTIVE_MODE_SMARTTAGS_DEFAULT),
                ): bool,
                vol.Optional(
                    CONF_ACTIVE_MODE_OTHERS,
                    default=self.config_entry.options.get(CONF_ACTIVE_MODE_OTHERS, CONF_ACTIVE_MODE_OTHERS_DEFAULT),
                ): bool,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
