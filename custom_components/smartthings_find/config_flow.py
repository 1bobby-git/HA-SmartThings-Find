from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import selector

from .account_auth import SamsungAccountAuth, SamsungAccountAuthError
from .const import (
    AUTH_METHOD_ACCOUNT,
    AUTH_METHOD_COOKIE,
    CONF_ACTIVE_MODE_OTHERS,
    CONF_ACTIVE_MODE_OTHERS_DEFAULT,
    CONF_ACTIVE_MODE_SMARTTAGS,
    CONF_ACTIVE_MODE_SMARTTAGS_DEFAULT,
    CONF_AUTH_METHOD,
    CONF_COOKIE,
    CONF_KEEPALIVE_INTERVAL,
    CONF_KEEPALIVE_INTERVAL_DEFAULT,
    CONF_REDIRECT_URI,
    CONF_UPDATE_INTERVAL,
    CONF_UPDATE_INTERVAL_DEFAULT,
    DOMAIN,
    STF_BASE_URL,
)
from .device_inventory import get_devices
from .session_store import async_remove_session_store
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


def _auth_method_selector() -> selector.SelectSelector:
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            mode=selector.SelectSelectorMode.DROPDOWN,
            options=[
                selector.SelectOptionDict(
                    value=AUTH_METHOD_ACCOUNT,
                    label="Samsung Account (자동 세션 복구·권장)",
                ),
                selector.SelectOptionDict(
                    value=AUTH_METHOD_COOKIE,
                    label="Cookie header (수동 호환 모드)",
                ),
            ],
        ),
    )


def _options_from_input(user_input: dict[str, Any]) -> dict[str, Any]:
    return {
        CONF_UPDATE_INTERVAL: int(
            user_input.get(
                CONF_UPDATE_INTERVAL,
                CONF_UPDATE_INTERVAL_DEFAULT,
            )
        ),
        CONF_KEEPALIVE_INTERVAL: int(
            user_input.get(
                CONF_KEEPALIVE_INTERVAL,
                CONF_KEEPALIVE_INTERVAL_DEFAULT,
            )
        ),
        CONF_ACTIVE_MODE_SMARTTAGS: _mode_to_bool(
            str(
                user_input.get(
                    _OPT_MODE_SMARTTAGS,
                    _bool_to_mode(CONF_ACTIVE_MODE_SMARTTAGS_DEFAULT),
                )
            )
        ),
        CONF_ACTIVE_MODE_OTHERS: _mode_to_bool(
            str(
                user_input.get(
                    _OPT_MODE_OTHERS,
                    _bool_to_mode(CONF_ACTIVE_MODE_OTHERS_DEFAULT),
                )
            )
        ),
    }


def _settings_fields(existing: dict[str, Any]) -> dict[Any, Any]:
    return {
        vol.Required(
            CONF_UPDATE_INTERVAL,
            default=existing.get(
                CONF_UPDATE_INTERVAL,
                CONF_UPDATE_INTERVAL_DEFAULT,
            ),
        ): vol.All(vol.Coerce(int), vol.Clamp(min=15, max=86400)),
        vol.Required(
            CONF_KEEPALIVE_INTERVAL,
            default=existing.get(
                CONF_KEEPALIVE_INTERVAL,
                CONF_KEEPALIVE_INTERVAL_DEFAULT,
            ),
        ): vol.All(vol.Coerce(int), vol.Clamp(min=60, max=86400)),
        vol.Required(
            _OPT_MODE_SMARTTAGS,
            default=_bool_to_mode(
                bool(
                    existing.get(
                        CONF_ACTIVE_MODE_SMARTTAGS,
                        CONF_ACTIVE_MODE_SMARTTAGS_DEFAULT,
                    )
                )
            ),
        ): _mode_selector(),
        vol.Required(
            _OPT_MODE_OTHERS,
            default=_bool_to_mode(
                bool(
                    existing.get(
                        CONF_ACTIVE_MODE_OTHERS,
                        CONF_ACTIVE_MODE_OTHERS_DEFAULT,
                    )
                )
            ),
        ): _mode_selector(),
    }


async def _async_validate_cookie(
    hass,
    cookie_line: str,
    context_id: str,
) -> list[dict[str, Any]]:
    cookies = parse_cookie_header(cookie_line)
    if not cookie_line or not cookies:
        raise ConfigEntryAuthFailed("invalid_cookie")

    session = make_session(hass)
    apply_cookies_to_session(session, cookies)
    try:
        await fetch_csrf(hass, session, context_id)
        return await get_devices(hass, session, context_id)
    finally:
        try:
            await session.close()
        except Exception:  # noqa: BLE001
            pass
        hass.data.get(DOMAIN, {}).pop(context_id, None)


class SmartThingsFindConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Configure SmartThings Find with persistent or legacy authentication."""

    VERSION = 1
    _stf_entry_id: str | None = None
    _account_login_url: str | None = None
    _is_reconfigure = False

    def _entry(self):
        if self._stf_entry_id is None:
            return None
        return self.hass.config_entries.async_get_entry(self._stf_entry_id)

    def _existing_options(self) -> dict[str, Any]:
        entry = self._entry()
        return dict(entry.options) if entry else {}

    async def _finish(
        self,
        *,
        data: dict[str, Any],
        options: dict[str, Any],
    ) -> ConfigFlowResult:
        entry = self._entry()
        if entry is not None:
            self.hass.data.setdefault(DOMAIN, {}).setdefault(
                entry.entry_id, {}
            )["_auth_reconfiguring"] = True
            await async_remove_session_store(self.hass, entry.entry_id)
            old_method = str(
                entry.data.get(CONF_AUTH_METHOD) or AUTH_METHOD_COOKIE
            )
            self.hass.config_entries.async_update_entry(
                entry,
                data=data,
                options=options,
            )
            if (
                old_method == AUTH_METHOD_ACCOUNT
                and data.get(CONF_AUTH_METHOD) != AUTH_METHOD_ACCOUNT
            ):
                await SamsungAccountAuth(self.hass).async_remove()
            await self.hass.config_entries.async_reload(entry.entry_id)
            return self.async_abort(
                reason=(
                    "reconfigure_successful"
                    if self._is_reconfigure
                    else "reauth_successful"
                )
            )

        await self.async_set_unique_id("smartthings_find")
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title="SmartThings Find",
            data=data,
            options=options,
        )

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Choose the authentication method."""
        if self._stf_entry_id is None:
            await self.async_set_unique_id("smartthings_find")
            self._abort_if_unique_id_configured()

        if self._stf_entry_id is not None and not self._is_reconfigure:
            entry = self._entry()
            method = (
                str(entry.data.get(CONF_AUTH_METHOD) or AUTH_METHOD_COOKIE)
                if entry
                else AUTH_METHOD_COOKIE
            )
            if method == AUTH_METHOD_ACCOUNT:
                return await self.async_step_account()
            return await self.async_step_cookie()

        if user_input is not None:
            method = str(
                user_input.get(CONF_AUTH_METHOD) or AUTH_METHOD_ACCOUNT
            )
            if method == AUTH_METHOD_ACCOUNT:
                return await self.async_step_account()
            return await self.async_step_cookie()

        entry = self._entry()
        default_method = (
            str(entry.data.get(CONF_AUTH_METHOD) or AUTH_METHOD_COOKIE)
            if entry
            else AUTH_METHOD_ACCOUNT
        )
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_AUTH_METHOD,
                        default=default_method,
                    ): _auth_method_selector(),
                }
            ),
        )

    async def async_step_account(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Complete Samsung Account login and store renewable credentials."""
        errors: dict[str, str] = {}
        account_auth = SamsungAccountAuth(self.hass)

        if user_input is not None:
            try:
                cookie_line = await account_auth.async_complete(
                    str(user_input.get(CONF_REDIRECT_URI) or "")
                )
                devices = await _async_validate_cookie(
                    self.hass,
                    cookie_line,
                    "config_flow_account",
                )
                if not devices:
                    errors["base"] = "no_devices"
                else:
                    return await self._finish(
                        data={CONF_AUTH_METHOD: AUTH_METHOD_ACCOUNT},
                        options=_options_from_input(user_input),
                    )
            except (SamsungAccountAuthError, ConfigEntryAuthFailed):
                errors["base"] = "invalid_auth"
            except Exception as err:  # noqa: BLE001
                _LOGGER.error(
                    "Samsung Account config flow failed (%s)",
                    type(err).__name__,
                )
                errors["base"] = "cannot_connect"

            # A callback attempt can consume or invalidate pending state. Always
            # issue a new one-time URL before the form is shown again.
            self._account_login_url = None

        if self._account_login_url is None:
            country = str(getattr(self.hass.config, "country", None) or "US")
            language = str(getattr(self.hass.config, "language", None) or "en")
            locale = (
                language if "-" in language else f"{language}-{country}"
            )
            try:
                self._account_login_url = await account_auth.async_start(
                    country=country,
                    locale=locale,
                )
            except SamsungAccountAuthError:
                errors["base"] = "cannot_connect"
                self._account_login_url = None

        schema_fields: dict[Any, Any] = {
            vol.Required(CONF_REDIRECT_URI): selector.TextSelector(
                selector.TextSelectorConfig(
                    multiline=True,
                    type=selector.TextSelectorType.TEXT,
                )
            )
        }
        schema_fields.update(_settings_fields(self._existing_options()))
        return self.async_show_form(
            step_id="account",
            data_schema=vol.Schema(schema_fields),
            description_placeholders={
                "login_url": self._account_login_url or "",
            },
            errors=errors,
        )

    async def async_step_cookie(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Configure the legacy manually copied Cookie header mode."""
        errors: dict[str, str] = {}

        if user_input is not None:
            cookie_line = str(user_input.get(CONF_COOKIE) or "").strip()
            try:
                devices = await _async_validate_cookie(
                    self.hass,
                    cookie_line,
                    "config_flow_cookie",
                )
                if not devices:
                    errors["base"] = "no_devices"
                else:
                    return await self._finish(
                        data={
                            CONF_AUTH_METHOD: AUTH_METHOD_COOKIE,
                            CONF_COOKIE: cookie_line,
                        },
                        options=_options_from_input(user_input),
                    )
            except ConfigEntryAuthFailed:
                errors["base"] = "invalid_auth"
            except Exception as err:  # noqa: BLE001
                _LOGGER.error(
                    "Cookie config flow failed (%s)",
                    type(err).__name__,
                )
                errors["base"] = "cannot_connect"

        schema_fields: dict[Any, Any] = {
            vol.Required(CONF_COOKIE): selector.TextSelector(
                selector.TextSelectorConfig(
                    multiline=True,
                    type=selector.TextSelectorType.TEXT,
                )
            )
        }
        schema_fields.update(_settings_fields(self._existing_options()))
        return self.async_show_form(
            step_id="cookie",
            data_schema=vol.Schema(schema_fields),
            description_placeholders={
                "stf_url": STF_BASE_URL.rstrip("/"),
            },
            errors=errors,
        )

    async def async_step_reauth(
        self,
        entry_data: dict[str, Any],
    ) -> ConfigFlowResult:
        """Renew the configured authentication method."""
        self._stf_entry_id = self.context.get("entry_id")
        self._is_reconfigure = False
        self._account_login_url = None
        return await self.async_step_user()

    async def async_step_reconfigure(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Allow switching between renewable and legacy authentication."""
        self._stf_entry_id = self.context.get("entry_id")
        self._is_reconfigure = True
        if user_input is not None:
            return await self.async_step_user(user_input)
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
        errors: dict[str, str] = {}
        entry = self.config_entry
        auth_method = str(
            entry.data.get(CONF_AUTH_METHOD) or AUTH_METHOD_COOKIE
        )

        if user_input is not None:
            new_cookie = str(user_input.get(CONF_COOKIE) or "").strip()
            current_cookie = str(entry.data.get(CONF_COOKIE) or "")
            cookie_changed = bool(
                auth_method == AUTH_METHOD_COOKIE
                and new_cookie
                and new_cookie != current_cookie
            )

            if cookie_changed:
                try:
                    devices = await _async_validate_cookie(
                        self.hass,
                        new_cookie,
                        "options_flow_cookie",
                    )
                    if not devices:
                        errors["base"] = "no_devices"
                except ConfigEntryAuthFailed:
                    errors["base"] = "invalid_auth"
                except Exception as err:  # noqa: BLE001
                    _LOGGER.error(
                        "Options cookie validation failed (%s)",
                        type(err).__name__,
                    )
                    errors["base"] = "cannot_connect"

            if not errors:
                if cookie_changed:
                    self.hass.data.setdefault(DOMAIN, {}).setdefault(
                        entry.entry_id, {}
                    )["_auth_reconfiguring"] = True
                    await async_remove_session_store(
                        self.hass,
                        entry.entry_id,
                    )
                    new_data = dict(entry.data)
                    new_data[CONF_COOKIE] = new_cookie
                    new_data[CONF_AUTH_METHOD] = AUTH_METHOD_COOKIE
                    self.hass.config_entries.async_update_entry(
                        entry,
                        data=new_data,
                    )
                return self.async_create_entry(
                    title="",
                    data=_options_from_input(user_input),
                )

        schema_fields: dict[Any, Any] = {}
        if auth_method == AUTH_METHOD_COOKIE:
            schema_fields[
                vol.Optional(
                    CONF_COOKIE,
                    description={"suggested_value": ""},
                )
            ] = selector.TextSelector(
                selector.TextSelectorConfig(
                    multiline=True,
                    type=selector.TextSelectorType.TEXT,
                )
            )
        schema_fields.update(_settings_fields(dict(entry.options)))

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(schema_fields),
            description_placeholders={"auth_method": auth_method},
            errors=errors,
        )
