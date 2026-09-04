"""Serialized authentication and automatic web-session recovery."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed

from .account_auth import SamsungAccountAuth, SamsungAccountAuthError
from .const import (
    AUTH_METHOD_ACCOUNT,
    AUTH_METHOD_COOKIE,
    AUTH_RETRY_DELAYS,
    CONF_ACTIVE_MODE_OTHERS,
    CONF_ACTIVE_MODE_SMARTTAGS,
    CONF_AUTH_METHOD,
    DOMAIN,
)
from .device_inventory import get_devices
from .session_store import async_load_cookie_line, persist_cookie_to_store
from .utils import (
    apply_cookies_to_session,
    clear_auth_failure,
    fetch_csrf,
    get_device_location,
    keepalive_ping,
    parse_cookie_header,
    send_operation,
)

_LOGGER = logging.getLogger(__name__)
_T = TypeVar("_T")


class SmartThingsFindAuthManager:
    """Own one entry's session and serialize every authenticated request.

    Read operations may be replayed once after CSRF/session repair.  Physical
    effect operations are preflighted but never automatically replayed after
    dispatch, preventing duplicate rings or location requests.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        session: aiohttp.ClientSession,
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.session = session
        self._lock = asyncio.Lock()
        self._auth_method = str(
            entry.data.get(CONF_AUTH_METHOD) or AUTH_METHOD_COOKIE
        )
        self._account_auth = (
            SamsungAccountAuth(hass)
            if self._auth_method == AUTH_METHOD_ACCOUNT
            else None
        )

    @property
    def auth_method(self) -> str:
        return self._auth_method

    def _entry_runtime(self) -> dict[str, Any]:
        return self.hass.data.setdefault(DOMAIN, {}).setdefault(
            self.entry.entry_id,
            {},
        )

    def _clear_csrf(self) -> None:
        self._entry_runtime().pop("_csrf", None)

    def _replace_cookies(self, cookie_line: str) -> None:
        cookies = parse_cookie_header(cookie_line)
        if not cookies:
            raise ConfigEntryAuthFailed("invalid_cookie_snapshot")

        clear = getattr(self.session.cookie_jar, "clear", None)
        if callable(clear):
            clear()
        apply_cookies_to_session(self.session, cookies)

    async def _fetch_csrf_locked(
        self,
        *,
        retry_delays: tuple[int | float, ...] = (),
    ) -> str:
        self._clear_csrf()
        return await fetch_csrf(
            self.hass,
            self.session,
            self.entry.entry_id,
            retry_delays=retry_delays,
        )

    async def _account_cookie_locked(self, *, force_refresh: bool) -> str:
        if self._account_auth is None:
            raise ConfigEntryAuthFailed("automatic_session_rebuild_unavailable")
        try:
            return await self._account_auth.async_cookie(
                force_refresh=force_refresh
            )
        except SamsungAccountAuthError as err:
            raise ConfigEntryAuthFailed(
                "samsung_account_session_rebuild_failed"
            ) from err

    async def _restore_cookie_snapshot_locked(self) -> str:
        cookie_line = await async_load_cookie_line(self.hass, self.entry)
        if not parse_cookie_header(cookie_line):
            raise ConfigEntryAuthFailed("missing_cookie")
        return cookie_line


    async def _persist_best_effort_locked(self) -> None:
        if self._entry_runtime().get("_auth_reconfiguring"):
            return
        try:
            await persist_cookie_to_store(self.hass, self.entry, self.session)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug(
                "SmartThings Find cookie persistence failed (%s)",
                type(err).__name__,
            )

    async def _rebuild_session_locked(self) -> None:
        """Replace the web session and obtain a fresh CSRF token."""
        if self._auth_method == AUTH_METHOD_ACCOUNT:
            cookie_line = await self._account_cookie_locked(force_refresh=True)
        else:
            cookie_line = await self._restore_cookie_snapshot_locked()

        self._replace_cookies(cookie_line)
        try:
            await self._fetch_csrf_locked(retry_delays=AUTH_RETRY_DELAYS)
        except ConfigEntryAuthFailed:
            await self._persist_best_effort_locked()
            raise
        await self._persist_best_effort_locked()
        _LOGGER.info(
            "SmartThings Find web session was rebuilt automatically "
            "(method=%s)",
            self._auth_method,
        )

    async def async_initialize(self) -> None:
        """Restore the best available session and validate it before setup."""
        async with self._lock:
            snapshot = await async_load_cookie_line(self.hass, self.entry)
            if parse_cookie_header(snapshot):
                self._replace_cookies(snapshot)
                try:
                    await self._fetch_csrf_locked(retry_delays=(2, 5))
                    await self._persist_best_effort_locked()
                    clear_auth_failure(self.hass, self.entry.entry_id)
                    return
                except ConfigEntryAuthFailed:
                    await self._persist_best_effort_locked()
                    self._clear_csrf()

            if self._auth_method != AUTH_METHOD_ACCOUNT:
                raise ConfigEntryAuthFailed("saved_cookie_invalid_or_expired")

            await self._rebuild_session_locked()
            clear_auth_failure(self.hass, self.entry.entry_id)

    async def _repair_csrf_and_retry_locked(
        self,
        operation: Callable[[], Awaitable[_T]],
    ) -> _T:
        """Try a fresh CSRF first, then rebuild the complete session."""
        self._clear_csrf()
        try:
            await self._fetch_csrf_locked(retry_delays=(2, 5))
            return await operation()
        except ConfigEntryAuthFailed:
            await self._persist_best_effort_locked()
            self._clear_csrf()

        await self._rebuild_session_locked()
        return await operation()

    async def async_read(
        self,
        operation: Callable[[], Awaitable[_T]],
    ) -> _T:
        """Run an idempotent read with bounded authentication recovery."""
        async with self._lock:
            try:
                result = await operation()
            except ConfigEntryAuthFailed:
                await self._persist_best_effort_locked()
                result = await self._repair_csrf_and_retry_locked(operation)

            await self._persist_best_effort_locked()
            clear_auth_failure(self.hass, self.entry.entry_id)
            return result

    async def async_get_devices(self) -> list[dict[str, Any]]:
        return await self.async_read(
            lambda: get_devices(
                self.hass,
                self.session,
                self.entry.entry_id,
            )
        )

    def _active_location_enabled(self, device_data: dict[str, Any]) -> bool:
        runtime = self._entry_runtime()
        if device_data.get("deviceTypeCode") == "TAG":
            return bool(runtime.get(CONF_ACTIVE_MODE_SMARTTAGS))
        return bool(runtime.get(CONF_ACTIVE_MODE_OTHERS))

    async def _async_effectful_location_read(
        self,
        device_data: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Run an active location cycle once without replaying its wake request."""
        async with self._lock:
            try:
                await self._fetch_csrf_locked(retry_delays=(2, 5))
            except ConfigEntryAuthFailed:
                await self._persist_best_effort_locked()
                await self._rebuild_session_locked()

            try:
                result = await get_device_location(
                    hass=self.hass,
                    session=self.session,
                    dev_data=device_data,
                    entry_id=self.entry.entry_id,
                )
            except ConfigEntryAuthFailed as err:
                await self._persist_best_effort_locked()
                # Active mode sends CHECK_CONNECTION_WITH_LOCATION before the
                # read. The server may already have accepted that wake request,
                # so repair credentials for the next poll but never replay this
                # mixed effect/read cycle automatically.
                self._clear_csrf()
                try:
                    await self._rebuild_session_locked()
                except ConfigEntryAuthFailed:
                    pass
                raise err

            await self._persist_best_effort_locked()
            clear_auth_failure(self.hass, self.entry.entry_id)
            return result

    async def async_get_device_location(
        self,
        device_data: dict[str, Any],
    ) -> dict[str, Any] | None:
        if self._active_location_enabled(device_data):
            return await self._async_effectful_location_read(device_data)

        return await self.async_read(
            lambda: get_device_location(
                hass=self.hass,
                session=self.session,
                dev_data=device_data,
                entry_id=self.entry.entry_id,
            )
        )

    async def async_keepalive(self) -> None:
        await self.async_read(
            lambda: keepalive_ping(
                self.hass,
                self.session,
                self.entry.entry_id,
            )
        )

    async def async_send_operation(self, payload: dict[str, Any]) -> None:
        """Preflight auth and dispatch one effect operation exactly once."""
        async with self._lock:
            try:
                # chkLogin.do obtains a current CSRF and confirms that the
                # session is valid before any physical effect is dispatched.
                await self._fetch_csrf_locked(retry_delays=(2, 5))
            except ConfigEntryAuthFailed:
                await self._persist_best_effort_locked()
                await self._rebuild_session_locked()

            try:
                await send_operation(
                    self.hass,
                    self.session,
                    self.entry.entry_id,
                    payload,
                )
            except ConfigEntryAuthFailed as err:
                await self._persist_best_effort_locked()
                # The server may have accepted an effect before returning an
                # unusable response. Repair credentials for the next command,
                # but never replay the effect automatically.
                self._clear_csrf()
                try:
                    await self._rebuild_session_locked()
                except ConfigEntryAuthFailed:
                    pass
                raise err

            await self._persist_best_effort_locked()
            clear_auth_failure(self.hass, self.entry.entry_id)

    async def async_shutdown(self) -> None:
        """Reserved for future provider resources."""
        return None
