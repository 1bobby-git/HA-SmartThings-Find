from __future__ import annotations

from typing import Any
import logging
import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.exceptions import ConfigEntryAuthFailed

from .const import (
    DOMAIN,
    CONF_JSESSIONID,
    CONF_UPDATE_INTERVAL,
    CONF_UPDATE_INTERVAL_DEFAULT,
    CONF_ACTIVE_MODE_SMARTTAGS,
    CONF_ACTIVE_MODE_SMARTTAGS_DEFAULT,
    CONF_ACTIVE_MODE_OTHERS,
    CONF_ACTIVE_MODE_OTHERS_DEFAULT,
)
from .utils import parse_cookie_header, apply_cookies_to_session, fetch_csrf

_LOGGER = logging.getLogger(__name__)


class SmartThingsFindConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_CLOUD_POLLING

    def __init__(self) -> None:
        self._reauth_entry: ConfigEntry | None = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}

        if user_input is not None:
            cookie_header = (user_input.get(CONF_JSESSIONID) or "").strip()
            cookies = parse_cookie_header(cookie_header)

            if not cookies:
                errors["base"] = "invalid_cookie"
            else:
                session = async_create_clientsession(
                    self.hass,
                    cookie_jar=aiohttp.CookieJar(unsafe=True),
                )
                try:
                    apply_cookies_to_session(session, cookies)
                    self.hass.data.setdefault(DOMAIN, {})
                    self.hass.data[DOMAIN].setdefault("config_flow_tmp", {})
                    await fetch_csrf(self.hass, session, "config_flow_tmp")

                    data = {CONF_JSESSIONID: cookie_header}

                    if self._reauth_entry:
                        return self.async_update_reload_and_abort(self._reauth_entry, data=data)

                    return self.async_create_entry(title="SmartThings Find", data=data)

                except ConfigEntryAuthFailed as e:
                    _LOGGER.warning("Auth validation failed: %s", e)
                    errors["base"] = "auth_failed"
                except Exception as e:
                    _LOGGER.exception("Unexpected error validating cookie auth: %s", e)
                    errors["base"] = "unknown"
                finally:
                    try:
                        await session.close()
                    except Exception:
                        pass

        schema = vol.Schema({vol.Required(CONF_JSESSIONID): str})

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_reauth(self, user_input: dict[str, Any] | None = None):
        self._reauth_entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        return await self.async_step_reauth_confirm(user_input)

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None):
        return await self.async_step_user(user_input)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
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
                    default=self.config_entry.options.get(
                        CONF_UPDATE_INTERVAL, CONF_UPDATE_INTERVAL_DEFAULT
                    ),
                ): vol.All(vol.Coerce(int), vol.Clamp(min=30)),
                vol.Optional(
                    CONF_ACTIVE_MODE_SMARTTAGS,
                    default=self.config_entry.options.get(
                        CONF_ACTIVE_MODE_SMARTTAGS, CONF_ACTIVE_MODE_SMARTTAGS_DEFAULT
                    ),
                ): bool,
                vol.Optional(
                    CONF_ACTIVE_MODE_OTHERS,
                    default=self.config_entry.options.get(
                        CONF_ACTIVE_MODE_OTHERS, CONF_ACTIVE_MODE_OTHERS_DEFAULT
                    ),
                ): bool,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
