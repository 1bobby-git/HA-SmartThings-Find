from __future__ import annotations

import json
import logging
import re
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult, OptionsFlowWithConfigEntry
from homeassistant.core import callback

from .const import (
    DOMAIN,
    CONF_COOKIES,
    CONF_COOKIE_HEADER,
    CONF_UPDATE_INTERVAL,
    CONF_UPDATE_INTERVAL_DEFAULT,
    CONF_ACTIVE_MODE_SMARTTAGS,
    CONF_ACTIVE_MODE_SMARTTAGS_DEFAULT,
    CONF_ACTIVE_MODE_OTHERS,
    CONF_ACTIVE_MODE_OTHERS_DEFAULT,
)
from .utils import normalize_cookies, validate_cookies

_LOGGER = logging.getLogger(__name__)

STEP_COOKIE_SCHEMA = vol.Schema({vol.Required("cookie_input"): str})


def _parse_cookie_input(raw: str) -> tuple[dict[str, str], str]:
    s = (raw or "").strip()
    if not s:
        return {}, ""

    for line in s.splitlines():
        if line.lower().startswith("cookie:"):
            s = line.split(":", 1)[1].strip()
            break

    if s.startswith("{"):
        obj = json.loads(s)
        if not isinstance(obj, dict):
            return {}, ""
        cookies = {str(k).strip(): str(v).strip() for k, v in obj.items() if str(k).strip()}
        hdr = "; ".join([f"{k}={v}" for k, v in cookies.items()])
        return cookies, hdr

    m = re.search(r"\bJSESSIONID\b[\s\t]+([A-Za-z0-9._~-]+)", s)
    if m:
        v = m.group(1).strip()
        return {"JSESSIONID": v}, f"JSESSIONID={v}"

    cookies: dict[str, str] = {}
    for part in s.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        k, v = part.split("=", 1)
        cookies[k.strip()] = v.strip()

    hdr = "; ".join([f"{k}={v}" for k, v in cookies.items()])
    return cookies, hdr


class SmartThingsFindConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._stf_reauth_entry_id: str | None = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self.async_step_cookie(user_input)

    async def async_step_reauth(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        self._stf_reauth_entry_id = self.context.get("entry_id")
        return await self.async_step_cookie(user_input)

    async def async_step_cookie(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            raw = user_input.get("cookie_input", "")
            try:
                cookies, hdr = _parse_cookie_input(raw)
            except Exception:
                cookies, hdr = {}, ""
                errors["base"] = "invalid_cookie"

            cookies = normalize_cookies(cookies)

            if "JSESSIONID" not in cookies:
                errors["base"] = "missing_jsessionid"

            if not errors:
                ok = await validate_cookies(self.hass, cookies)
                if not ok:
                    errors["base"] = "auth_failed"
                else:
                    data = {CONF_COOKIES: cookies, CONF_COOKIE_HEADER: hdr}

                    if self._stf_reauth_entry_id:
                        entry = self.hass.config_entries.async_get_entry(self._stf_reauth_entry_id)
                        if entry:
                            self.hass.config_entries.async_update_entry(entry, data={**entry.data, **data})
                            return self.async_abort(reason="reauth_successful")

                    return self.async_create_entry(title="SmartThings Find", data=data)

        return self.async_show_form(step_id="cookie", data_schema=STEP_COOKIE_SCHEMA, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> config_entries.OptionsFlow:
        return SmartThingsFindOptionsFlowHandler(config_entry)


class SmartThingsFindOptionsFlowHandler(OptionsFlowWithConfigEntry):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            res = self.async_create_entry(title="", data=user_input)
            self.hass.config_entries.async_schedule_reload(self.config_entry.entry_id)
            return res

        data_schema = vol.Schema(
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
        return self.async_show_form(step_id="init", data_schema=data_schema)
