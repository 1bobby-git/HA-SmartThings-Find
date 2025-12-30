from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult, OptionsFlowWithConfigEntry
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
    CONF_ST_DEVICE_ID,
    CONF_ST_IDENTIFIER,
)
from .utils import (
    parse_cookie_header,
    apply_cookies_to_session,
    fetch_csrf,
    make_session,
    list_smartthings_devices_for_ui,
    get_smartthings_identifier_value_by_device_id,
)

_LOGGER = logging.getLogger(__name__)

STF_URL = "https://smartthingsfind.samsung.com"


class SmartThingsFindConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for SmartThings Find."""

    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_CLOUD_POLL

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            cookie_line = (user_input.get(CONF_COOKIE) or "").strip()
            cookies = parse_cookie_header(cookie_line)

            if not cookie_line:
                errors["base"] = "missing_cookie"
            elif not cookies:
                errors["base"] = "invalid_cookie"
            else:
                session = make_session(self.hass)
                try:
                    apply_cookies_to_session(session, cookies)
                    await fetch_csrf(self.hass, session, None)
                    return self.async_create_entry(
                        title="SmartThings Find",
                        data={CONF_COOKIE: cookie_line},
                    )
                except ConfigEntryAuthFailed as e:
                    _LOGGER.warning("Cookie auth failed: %s", e)
                    errors["base"] = "auth_failed"
                except Exception as e:
                    _LOGGER.exception("Unexpected error validating cookie auth: %s", e)
                    errors["base"] = "unknown"
                finally:
                    await session.close()

        schema = vol.Schema({vol.Required(CONF_COOKIE): str})

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
            description_placeholders={"stf_url": STF_URL},
        )

    async def async_step_reauth(self, _data: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self.async_step_user()

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> config_entries.OptionsFlow:
        return SmartThingsFindOptionsFlowHandler(config_entry)


class SmartThingsFindOptionsFlowHandler(OptionsFlowWithConfigEntry):
    """Options flow (0.3.15 setter-crash fix + 0.3.16 device mapping)."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            # ✅ device_registry id -> store identifier string (JSON safe)
            st_device_id = (user_input.get(CONF_ST_DEVICE_ID) or "").strip()
            if st_device_id:
                st_ident_value = get_smartthings_identifier_value_by_device_id(self.hass, st_device_id)
                if st_ident_value:
                    user_input[CONF_ST_IDENTIFIER] = st_ident_value
                else:
                    user_input.pop(CONF_ST_IDENTIFIER, None)
            else:
                user_input.pop(CONF_ST_IDENTIFIER, None)

            return self.async_create_entry(title="", data=user_input)

        # Build SmartThings device dropdown
        st_devices = list_smartthings_devices_for_ui(self.hass)
        st_map = {"": "-- (선택 안 함) --"}
        for dev_id, label in st_devices:
            st_map[dev_id] = label

        schema = vol.Schema(
            {
                # ✅ 0.3.16: 공식 SmartThings 기기 병합 선택
                vol.Optional(
                    CONF_ST_DEVICE_ID,
                    default=self.options.get(CONF_ST_DEVICE_ID, ""),
                ): vol.In(st_map),

                vol.Optional(
                    CONF_UPDATE_INTERVAL,
                    default=self.options.get(CONF_UPDATE_INTERVAL, CONF_UPDATE_INTERVAL_DEFAULT),
                ): vol.All(vol.Coerce(int), vol.Clamp(min=30)),

                vol.Optional(
                    CONF_ACTIVE_MODE_SMARTTAGS,
                    default=self.options.get(CONF_ACTIVE_MODE_SMARTTAGS, CONF_ACTIVE_MODE_SMARTTAGS_DEFAULT),
                ): bool,

                vol.Optional(
                    CONF_ACTIVE_MODE_OTHERS,
                    default=self.options.get(CONF_ACTIVE_MODE_OTHERS, CONF_ACTIVE_MODE_OTHERS_DEFAULT),
                ): bool,
            }
        )

        return self.async_show_form(step_id="init", data_schema=schema)
