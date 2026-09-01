from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import selector

from .const import (
    CONF_ACTIVE_MODE_OTHERS,
    CONF_ACTIVE_MODE_OTHERS_DEFAULT,
    CONF_ACTIVE_MODE_SMARTTAGS,
    CONF_ACTIVE_MODE_SMARTTAGS_DEFAULT,
    CONF_COOKIE,
    CONF_KEEPALIVE_INTERVAL,
    CONF_KEEPALIVE_INTERVAL_DEFAULT,
    CONF_UPDATE_INTERVAL,
    CONF_UPDATE_INTERVAL_DEFAULT,
    DOMAIN,
)
from .device_inventory import get_devices
from .utils import (
    apply_cookies_to_session,
    fetch_csrf,
    make_session,
    parse_cookie_header,
)

_LOGGER = logging.getLogger(__name__)

_OPT_MODE_SMARTTAGS = "mode_smarttags"
_OPT_MODE_OTHERS = "mode_others"
_MODE_PASSIVE = "passive"
_MODE_ACTIVE = "active"


def _bool_to_mode(value: bool) -> str:
    return _MODE_ACTIVE if value else _MODE_PASSIVE


def _mode_to_bool(value: str) -> bool:
    return value == _MODE_ACTIVE


def _mode_selector() -> selector.SelectSelector:
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            mode=selector.SelectSelectorMode.DROPDOWN,
            options=[
                selector.SelectOptionDict(value=_MODE_PASSIVE, label="패시브"),
                selector.SelectOptionDict(value=_MODE_ACTIVE, label="액티브"),
            ],
        ),
    )


class SmartThingsFindConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Configure SmartThings Find from a browser session cookie."""

    VERSION = 1
    _stf_reauth_entry_id: str | None = None

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Handle initial setup and cookie reauthentication."""
        errors: dict[str, str] = {}
        is_reauth = self._stf_reauth_entry_id is not None

        existing_options: dict[str, Any] = {}
        if is_reauth:
            entry = self.hass.config_entries.async_get_entry(
                self._stf_reauth_entry_id
            )
            if entry:
                existing_options = dict(entry.options)

        if user_input is not None:
            cookie_line = str(user_input.get(CONF_COOKIE) or "").strip()
            cookies = parse_cookie_header(cookie_line)

            if not cookie_line or not cookies:
                errors["base"] = "invalid_auth"
            else:
                session = make_session(self.hass)
                apply_cookies_to_session(session, cookies)

                try:
                    await fetch_csrf(self.hass, session, "config_flow")
                    devices = await get_devices(
                        self.hass,
                        session,
                        "config_flow",
                    )

                    if not devices:
                        errors["base"] = "no_devices"
                    else:
                        update_interval = user_input.get(
                            CONF_UPDATE_INTERVAL,
                            CONF_UPDATE_INTERVAL_DEFAULT,
                        )
                        keepalive_interval = user_input.get(
                            CONF_KEEPALIVE_INTERVAL,
                            CONF_KEEPALIVE_INTERVAL_DEFAULT,
                        )
                        smarttags_mode = user_input.get(
                            _OPT_MODE_SMARTTAGS,
                            _bool_to_mode(CONF_ACTIVE_MODE_SMARTTAGS_DEFAULT),
                        )
                        others_mode = user_input.get(
                            _OPT_MODE_OTHERS,
                            _bool_to_mode(CONF_ACTIVE_MODE_OTHERS_DEFAULT),
                        )

                        options_data = {
                            CONF_UPDATE_INTERVAL: int(update_interval),
                            CONF_KEEPALIVE_INTERVAL: int(keepalive_interval),
                            CONF_ACTIVE_MODE_SMARTTAGS: _mode_to_bool(
                                str(smarttags_mode)
                            ),
                            CONF_ACTIVE_MODE_OTHERS: _mode_to_bool(
                                str(others_mode)
                            ),
                        }

                        if is_reauth:
                            entry = self.hass.config_entries.async_get_entry(
                                self._stf_reauth_entry_id
                            )
                            if entry:
                                self.hass.config_entries.async_update_entry(
                                    entry,
                                    data={CONF_COOKIE: cookie_line},
                                    options=options_data,
                                )
                                await self.hass.config_entries.async_reload(
                                    entry.entry_id
                                )
                            return self.async_abort(reason="reauth_successful")

                        await self.async_set_unique_id("smartthings_find")
                        self._abort_if_unique_id_configured()
                        return self.async_create_entry(
                            title="SmartThings Find",
                            data={CONF_COOKIE: cookie_line},
                            options=options_data,
                        )

                except ConfigEntryAuthFailed:
                    errors["base"] = "invalid_auth"
                except Exception as err:  # noqa: BLE001
                    _LOGGER.exception("Config flow setup failed: %s", err)
                    errors["base"] = "cannot_connect"
                finally:
                    try:
                        await session.close()
                    except Exception:  # noqa: BLE001
                        pass

        schema = vol.Schema(
            {
                vol.Required(CONF_COOKIE): selector.TextSelector(
                    selector.TextSelectorConfig(
                        multiline=True,
                        type=selector.TextSelectorType.TEXT,
                    )
                ),
                vol.Required(
                    CONF_UPDATE_INTERVAL,
                    default=existing_options.get(
                        CONF_UPDATE_INTERVAL,
                        CONF_UPDATE_INTERVAL_DEFAULT,
                    ),
                ): vol.All(vol.Coerce(int), vol.Clamp(min=15, max=86400)),
                vol.Required(
                    CONF_KEEPALIVE_INTERVAL,
                    default=existing_options.get(
                        CONF_KEEPALIVE_INTERVAL,
                        CONF_KEEPALIVE_INTERVAL_DEFAULT,
                    ),
                ): vol.All(vol.Coerce(int), vol.Clamp(min=60, max=86400)),
                vol.Required(
                    _OPT_MODE_SMARTTAGS,
                    default=_bool_to_mode(
                        existing_options.get(
                            CONF_ACTIVE_MODE_SMARTTAGS,
                            CONF_ACTIVE_MODE_SMARTTAGS_DEFAULT,
                        )
                    ),
                ): _mode_selector(),
                vol.Required(
                    _OPT_MODE_OTHERS,
                    default=_bool_to_mode(
                        existing_options.get(
                            CONF_ACTIVE_MODE_OTHERS,
                            CONF_ACTIVE_MODE_OTHERS_DEFAULT,
                        )
                    ),
                ): _mode_selector(),
            }
        )
        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_reauth(
        self,
        entry_data: dict[str, Any],
    ) -> ConfigFlowResult:
        """Start cookie reauthentication after a persistent auth failure."""
        self._stf_reauth_entry_id = self.context.get("entry_id")
        return await self.async_step_user()

    @staticmethod
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        return SmartThingsFindOptionsFlow()


class SmartThingsFindOptionsFlow(config_entries.OptionsFlow):
    """Configure polling, keepalive and active location behavior."""

    async def async_step_init(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Handle integration options and an optional replacement cookie."""
        errors: dict[str, str] = {}
        entry = self.config_entry

        if user_input is not None:
            new_cookie = str(user_input.get(CONF_COOKIE) or "").strip()
            current_cookie = str(entry.data.get(CONF_COOKIE) or "")
            cookie_changed = bool(new_cookie and new_cookie != current_cookie)

            if cookie_changed:
                cookies = parse_cookie_header(new_cookie)
                if not cookies:
                    errors["base"] = "invalid_auth"
                else:
                    session = make_session(self.hass)
                    apply_cookies_to_session(session, cookies)
                    try:
                        await fetch_csrf(self.hass, session, "options_flow")
                        devices = await get_devices(
                            self.hass,
                            session,
                            "options_flow",
                        )
                        if not devices:
                            errors["base"] = "no_devices"
                    except ConfigEntryAuthFailed:
                        errors["base"] = "invalid_auth"
                    except Exception as err:  # noqa: BLE001
                        _LOGGER.exception(
                            "Options flow cookie validation failed: %s",
                            err,
                        )
                        errors["base"] = "cannot_connect"
                    finally:
                        try:
                            await session.close()
                        except Exception:  # noqa: BLE001
                            pass

            if not errors:
                update_interval = user_input.get(
                    CONF_UPDATE_INTERVAL,
                    CONF_UPDATE_INTERVAL_DEFAULT,
                )
                keepalive_interval = user_input.get(
                    CONF_KEEPALIVE_INTERVAL,
                    CONF_KEEPALIVE_INTERVAL_DEFAULT,
                )
                smarttags_mode = user_input.get(
                    _OPT_MODE_SMARTTAGS,
                    _bool_to_mode(CONF_ACTIVE_MODE_SMARTTAGS_DEFAULT),
                )
                others_mode = user_input.get(
                    _OPT_MODE_OTHERS,
                    _bool_to_mode(CONF_ACTIVE_MODE_OTHERS_DEFAULT),
                )

                new_options = {
                    CONF_UPDATE_INTERVAL: int(update_interval),
                    CONF_KEEPALIVE_INTERVAL: int(keepalive_interval),
                    CONF_ACTIVE_MODE_SMARTTAGS: _mode_to_bool(
                        str(smarttags_mode)
                    ),
                    CONF_ACTIVE_MODE_OTHERS: _mode_to_bool(str(others_mode)),
                }

                if cookie_changed:
                    self.hass.config_entries.async_update_entry(
                        entry,
                        data={CONF_COOKIE: new_cookie},
                    )

                return self.async_create_entry(title="", data=new_options)

        active_smarttags = entry.options.get(
            CONF_ACTIVE_MODE_SMARTTAGS,
            CONF_ACTIVE_MODE_SMARTTAGS_DEFAULT,
        )
        active_others = entry.options.get(
            CONF_ACTIVE_MODE_OTHERS,
            CONF_ACTIVE_MODE_OTHERS_DEFAULT,
        )

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_COOKIE,
                    description={"suggested_value": ""},
                ): selector.TextSelector(
                    selector.TextSelectorConfig(
                        multiline=True,
                        type=selector.TextSelectorType.TEXT,
                    )
                ),
                vol.Required(
                    CONF_UPDATE_INTERVAL,
                    default=entry.options.get(
                        CONF_UPDATE_INTERVAL,
                        CONF_UPDATE_INTERVAL_DEFAULT,
                    ),
                ): vol.All(vol.Coerce(int), vol.Clamp(min=15, max=86400)),
                vol.Required(
                    CONF_KEEPALIVE_INTERVAL,
                    default=entry.options.get(
                        CONF_KEEPALIVE_INTERVAL,
                        CONF_KEEPALIVE_INTERVAL_DEFAULT,
                    ),
                ): vol.All(vol.Coerce(int), vol.Clamp(min=60, max=86400)),
                vol.Required(
                    _OPT_MODE_SMARTTAGS,
                    default=_bool_to_mode(bool(active_smarttags)),
                ): _mode_selector(),
                vol.Required(
                    _OPT_MODE_OTHERS,
                    default=_bool_to_mode(bool(active_others)),
                ): _mode_selector(),
            }
        )

        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            errors=errors,
        )
