from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import selector

from .const import (
    DOMAIN,
    CONF_COOKIE,
    CONF_UPDATE_INTERVAL,
    CONF_UPDATE_INTERVAL_DEFAULT,
    CONF_KEEPALIVE_INTERVAL,
    CONF_KEEPALIVE_INTERVAL_DEFAULT,
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
                selector.SelectOptionDict(value=_MODE_PASSIVE, label="패시브"),
                selector.SelectOptionDict(value=_MODE_ACTIVE, label="액티브"),
            ],
        ),
    )


class SmartThingsFindConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    # ✅ HA Core에서 _reauth_entry_id는 setter 없는 property일 수 있어 충돌함
    # 통합 전용 변수명으로 보관
    _stf_reauth_entry_id: str | None = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """처음 설정과 재인증을 통합한 단일 설정 화면 (쿠키 + 옵션)."""
        errors: dict[str, str] = {}
        is_reauth = self._stf_reauth_entry_id is not None

        # 재인증 시 기존 옵션 기본값으로 사용
        existing_options: dict[str, Any] = {}
        if is_reauth:
            entry = self.hass.config_entries.async_get_entry(self._stf_reauth_entry_id)
            if entry:
                existing_options = dict(entry.options)

        if user_input is not None:
            cookie_line = (user_input.get(CONF_COOKIE) or "").strip()
            cookies = parse_cookie_header(cookie_line)

            # 쿠키 입력/형식 문제는 invalid_auth로 통일(사용자 안내 일관성)
            if not cookie_line or not cookies:
                errors["base"] = "invalid_auth"
            else:
                session = make_session(self.hass)
                apply_cookies_to_session(session, cookies)

                try:
                    await fetch_csrf(self.hass, session, "config_flow")
                    devices = await get_devices(self.hass, session, "config_flow")

                    if not devices:
                        errors["base"] = "no_devices"
                    else:
                        # 옵션 값 추출
                        update_interval = user_input.get(CONF_UPDATE_INTERVAL, CONF_UPDATE_INTERVAL_DEFAULT)
                        keepalive_interval = user_input.get(CONF_KEEPALIVE_INTERVAL, CONF_KEEPALIVE_INTERVAL_DEFAULT)
                        smarttags_mode = user_input.get(_OPT_MODE_SMARTTAGS, _bool_to_mode(CONF_ACTIVE_MODE_SMARTTAGS_DEFAULT))
                        others_mode = user_input.get(_OPT_MODE_OTHERS, _bool_to_mode(CONF_ACTIVE_MODE_OTHERS_DEFAULT))

                        options_data = {
                            CONF_UPDATE_INTERVAL: int(update_interval),
                            CONF_KEEPALIVE_INTERVAL: int(keepalive_interval),
                            CONF_ACTIVE_MODE_SMARTTAGS: _mode_to_bool(str(smarttags_mode)),
                            CONF_ACTIVE_MODE_OTHERS: _mode_to_bool(str(others_mode)),
                        }

                        if is_reauth:
                            # 재인증: 기존 entry 업데이트 (data + options 모두)
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
                        else:
                            # 처음 설정: 새 entry 생성 (data에 쿠키, options에 설정)
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

        # 통합 스키마: 쿠키 + 옵션
        schema = vol.Schema(
            {
                vol.Required(CONF_COOKIE): str,
                vol.Required(
                    CONF_UPDATE_INTERVAL,
                    default=existing_options.get(CONF_UPDATE_INTERVAL, CONF_UPDATE_INTERVAL_DEFAULT),
                ): vol.All(vol.Coerce(int), vol.Clamp(min=15, max=86400)),
                vol.Required(
                    CONF_KEEPALIVE_INTERVAL,
                    default=existing_options.get(CONF_KEEPALIVE_INTERVAL, CONF_KEEPALIVE_INTERVAL_DEFAULT),
                ): vol.All(vol.Coerce(int), vol.Clamp(min=60, max=86400)),
                vol.Required(
                    _OPT_MODE_SMARTTAGS,
                    default=_bool_to_mode(existing_options.get(CONF_ACTIVE_MODE_SMARTTAGS, CONF_ACTIVE_MODE_SMARTTAGS_DEFAULT)),
                ): _mode_selector(),
                vol.Required(
                    _OPT_MODE_OTHERS,
                    default=_bool_to_mode(existing_options.get(CONF_ACTIVE_MODE_OTHERS, CONF_ACTIVE_MODE_OTHERS_DEFAULT)),
                ): _mode_selector(),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_reauth(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Home Assistant가 ConfigEntryAuthFailed를 받으면 reauth flow를 시작한다."""
        # ✅ 기존 self._reauth_entry_id = ... 는 HA 코어에서 setter가 없어 크래시날 수 있음
        self._stf_reauth_entry_id = self.context.get("entry_id")
        # 통합된 user step으로 리다이렉트
        return await self.async_step_user()

    @staticmethod
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> config_entries.OptionsFlow:
        return SmartThingsFindOptionsFlow(config_entry)


class SmartThingsFindOptionsFlow(config_entries.OptionsFlow):
    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        # ❗ HA 내부에 config_entry property가 있어서 setter로 넣으면 에러남
        self._config_entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """통합 옵션 화면: 쿠키 + 모든 설정."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # 쿠키 변경 여부 확인
            new_cookie = (user_input.get(CONF_COOKIE) or "").strip()
            current_cookie = self._config_entry.data.get(CONF_COOKIE, "")
            cookie_changed = new_cookie and new_cookie != current_cookie

            # 쿠키가 변경되었으면 유효성 검증
            if cookie_changed:
                cookies = parse_cookie_header(new_cookie)
                if not cookies:
                    errors["base"] = "invalid_auth"
                else:
                    session = make_session(self.hass)
                    apply_cookies_to_session(session, cookies)
                    try:
                        await fetch_csrf(self.hass, session, "options_flow")
                        devices = await get_devices(self.hass, session, "options_flow")
                        if not devices:
                            errors["base"] = "no_devices"
                    except ConfigEntryAuthFailed:
                        errors["base"] = "invalid_auth"
                    except Exception as err:  # noqa: BLE001
                        _LOGGER.exception("Options flow cookie validation failed: %s", err)
                        errors["base"] = "cannot_connect"
                    finally:
                        try:
                            await session.close()
                        except Exception:  # noqa: BLE001
                            pass

            if not errors:
                # 옵션 값 추출
                update_interval = user_input.get(CONF_UPDATE_INTERVAL, CONF_UPDATE_INTERVAL_DEFAULT)
                keepalive_interval = user_input.get(CONF_KEEPALIVE_INTERVAL, CONF_KEEPALIVE_INTERVAL_DEFAULT)
                smarttags_mode = user_input.get(_OPT_MODE_SMARTTAGS, _bool_to_mode(CONF_ACTIVE_MODE_SMARTTAGS_DEFAULT))
                others_mode = user_input.get(_OPT_MODE_OTHERS, _bool_to_mode(CONF_ACTIVE_MODE_OTHERS_DEFAULT))

                new_options = {
                    CONF_UPDATE_INTERVAL: int(update_interval),
                    CONF_KEEPALIVE_INTERVAL: int(keepalive_interval),
                    CONF_ACTIVE_MODE_SMARTTAGS: _mode_to_bool(str(smarttags_mode)),
                    CONF_ACTIVE_MODE_OTHERS: _mode_to_bool(str(others_mode)),
                }

                # 쿠키가 변경되었으면 data도 업데이트
                if cookie_changed:
                    self.hass.config_entries.async_update_entry(
                        self._config_entry,
                        data={CONF_COOKIE: new_cookie},
                    )

                return self.async_create_entry(title="", data=new_options)

        # 현재 값을 기본값으로 사용
        active_smarttags = self._config_entry.options.get(
            CONF_ACTIVE_MODE_SMARTTAGS, CONF_ACTIVE_MODE_SMARTTAGS_DEFAULT
        )
        active_others = self._config_entry.options.get(CONF_ACTIVE_MODE_OTHERS, CONF_ACTIVE_MODE_OTHERS_DEFAULT)

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_COOKIE,
                    description={"suggested_value": ""},
                ): str,
                vol.Required(
                    CONF_UPDATE_INTERVAL,
                    default=self._config_entry.options.get(CONF_UPDATE_INTERVAL, CONF_UPDATE_INTERVAL_DEFAULT),
                ): vol.All(vol.Coerce(int), vol.Clamp(min=15, max=86400)),
                vol.Required(
                    CONF_KEEPALIVE_INTERVAL,
                    default=self._config_entry.options.get(CONF_KEEPALIVE_INTERVAL, CONF_KEEPALIVE_INTERVAL_DEFAULT),
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

        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
