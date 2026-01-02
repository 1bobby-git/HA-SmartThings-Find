from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

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

from .utils import (  # type: ignore
    parse_cookie_header,
    apply_cookies_to_session,
    fetch_csrf,
    make_session,
    get_devices,
)

_LOGGER = logging.getLogger(__name__)

# Options UI 전용(저장 키와 분리)
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
                selector.SelectOptionDict(
                    value=_MODE_PASSIVE,
                    label="패시브 (서버에 마지막으로 보고된 위치만 조회, 배터리 영향 적음)",
                ),
                selector.SelectOptionDict(
                    value=_MODE_ACTIVE,
                    label="액티브 (위치 업데이트 요청 전송, 정확도/즉시성↑ 배터리 영향↑)",
                ),
            ],
        ),
    )


class SmartThingsFindConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            cookie_line = (user_input.get(CONF_COOKIE) or "").strip()
            cookies = parse_cookie_header(cookie_line)
            if not cookie_line or not cookies:
                errors[CONF_COOKIE] = "invalid_cookie"
            else:
                session = make_session(self.hass)
                apply_cookies_to_session(session, cookies)

                try:
                    await fetch_csrf(self.hass, session, "config_flow")
                    devices = await get_devices(self.hass, session, "config_flow")

                    if not devices:
                        errors["base"] = "no_devices"
                    else:
                        return self.async_create_entry(
                            title="SmartThings Find",
                            data={CONF_COOKIE: cookie_line},
                        )
                except Exception as err:  # noqa: BLE001
                    _LOGGER.exception("Config flow setup failed: %s", err)
                    errors["base"] = "cannot_connect"
                finally:
                    try:
                        await session.close()
                    except Exception:  # noqa: BLE001
                        pass

        schema = vol.Schema({vol.Required(CONF_COOKIE): str})
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_reauth(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """
        Home Assistant가 ConfigEntryAuthFailed를 받으면 reauth flow를 시작한다.
        """
        self._stf_reauth_entry_id = self.context.get("entry_id")
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            cookie_line = (user_input.get(CONF_COOKIE) or "").strip()
            cookies = parse_cookie_header(cookie_line)
            if not cookie_line or not cookies:
                errors[CONF_COOKIE] = "invalid_cookie"
            else:
                session = make_session(self.hass)
                apply_cookies_to_session(session, cookies)

                try:
                    await fetch_csrf(self.hass, session, "config_flow")
                    devices = await get_devices(self.hass, session, "config_flow")
                    if not devices:
                        errors["base"] = "no_devices"
                    else:
                        entry_id = getattr(self, "_reauth_entry_id", None)
                        if entry_id:
                            entry = self.hass.config_entries.async_get_entry(entry_id)
                            if entry:
                                new_data = dict(entry.data)
                                new_data[CONF_COOKIE] = cookie_line
                                self.hass.config_entries.async_update_entry(entry, data=new_data)
                                await self.hass.config_entries.async_reload(entry.entry_id)

                        return self.async_abort(reason="reauth_successful")
                except Exception as err:  # noqa: BLE001
                    _LOGGER.exception("Reauth failed: %s", err)
                    errors["base"] = "cannot_connect"
                finally:
                    try:
                        await session.close()
                    except Exception:  # noqa: BLE001
                        pass

        schema = vol.Schema({vol.Required(CONF_COOKIE): str})
        return self.async_show_form(step_id="reauth_confirm", data_schema=schema, errors=errors)

    @staticmethod
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> config_entries.OptionsFlow:
        return SmartThingsFindOptionsFlow(config_entry)


class SmartThingsFindOptionsFlow(config_entries.OptionsFlow):
    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        # ❗ HA 내부에 config_entry property가 있어서 setter로 넣으면 에러남
        self._config_entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            new_options = dict(self._config_entry.options)

            update_interval = user_input.get(CONF_UPDATE_INTERVAL, CONF_UPDATE_INTERVAL_DEFAULT)
            smarttags_mode = user_input.get(_OPT_MODE_SMARTTAGS, _bool_to_mode(CONF_ACTIVE_MODE_SMARTTAGS_DEFAULT))
            others_mode = user_input.get(_OPT_MODE_OTHERS, _bool_to_mode(CONF_ACTIVE_MODE_OTHERS_DEFAULT))

            new_options[CONF_UPDATE_INTERVAL] = int(update_interval)
            new_options[CONF_ACTIVE_MODE_SMARTTAGS] = _mode_to_bool(str(smarttags_mode))
            new_options[CONF_ACTIVE_MODE_OTHERS] = _mode_to_bool(str(others_mode))

            return self.async_create_entry(title="", data=new_options)

        active_smarttags = self._config_entry.options.get(
            CONF_ACTIVE_MODE_SMARTTAGS, CONF_ACTIVE_MODE_SMARTTAGS_DEFAULT
        )
        active_others = self._config_entry.options.get(CONF_ACTIVE_MODE_OTHERS, CONF_ACTIVE_MODE_OTHERS_DEFAULT)

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_UPDATE_INTERVAL,
                    default=self._config_entry.options.get(CONF_UPDATE_INTERVAL, CONF_UPDATE_INTERVAL_DEFAULT),
                ): vol.All(vol.Coerce(int), vol.Clamp(min=15, max=86400)),
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

        return self.async_show_form(step_id="init", data_schema=schema)

