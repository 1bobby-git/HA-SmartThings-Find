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
)
from .utils import parse_cookie_header, apply_cookies_to_session, fetch_csrf, make_session

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

                    # ✅ validate by fetching csrf
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
        """Reauth just re-runs user step."""
        return await self.async_step_user()

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> config_entries.OptionsFlow:
        # ✅ OptionsFlowWithConfigEntry는 내부에서 config_entry를 관리함
        # ❌ 절대로 self.config_entry = ... 같은 대입을 하면 안 됨
        return SmartThingsFindOptionsFlowHandler(config_entry)


class SmartThingsFindOptionsFlowHandler(OptionsFlowWithConfigEntry):
    """Handle an options flow for SmartThings Find."""

    # ✅ 중요: __init__을 오버라이드하지 마세요.
    # OptionsFlowWithConfigEntry가 config_entry를 안전하게 보관합니다.

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        schema = vol.Schema(
            {
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
