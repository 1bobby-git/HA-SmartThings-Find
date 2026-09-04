"""Persistent Samsung Account authorization for SmartThings Find."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class SamsungAccountAuthError(Exception):
    """Raised when Samsung Account authorization cannot be completed or reused."""


class SamsungAccountAuth:
    """Run samsung-re-find authentication with HA-owned private state paths.

    The third-party library stores a long-lived Samsung Account master grant and
    rotating derived credentials.  The integration stores no password or second
    factor and only asks the library for a validated/rebuilt web JSESSIONID.
    """

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._storage_dir = Path(
            hass.config.path(".storage", f"{DOMAIN}_auth")
        )
        prefix = self._storage_dir / "account"
        self._state_path = Path(f"{prefix}.state.json")
        self._pending_path = Path(f"{prefix}.pending.json")
        self._master_path = Path(f"{prefix}.master.json")
        self._legacy_state_path = Path(f"{prefix}.legacy.json")
        domain_data = hass.data.setdefault(DOMAIN, {})
        lock = domain_data.get("_account_auth_lock")
        if not isinstance(lock, asyncio.Lock):
            lock = asyncio.Lock()
            domain_data["_account_auth_lock"] = lock
        self._lock = lock

    def _new_client(self):
        # Imported lazily so legacy cookie entries can still load even if an
        # installation temporarily has a dependency problem.
        from samsung_find.auth import SamsungAuth

        return SamsungAuth(
            state_path=self._state_path,
            pending_path=self._pending_path,
            master_path=self._master_path,
            legacy_state_path=self._legacy_state_path,
        )

    async def async_start(self, *, country: str, locale: str) -> str:
        """Create a one-time Samsung Account sign-in URL."""
        async with self._lock:
            try:
                return await self.hass.async_add_executor_job(
                    self._start_sync,
                    country,
                    locale,
                )
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning(
                    "Unable to start Samsung Account authentication (%s)",
                    type(err).__name__,
                )
                raise SamsungAccountAuthError(
                    "Samsung Account login could not be started"
                ) from err

    def _start_sync(self, country: str, locale: str) -> str:
        client = self._new_client()
        try:
            return str(
                client.start(
                    country=(country or "US").lower(),
                    locale=locale or "en-US",
                )
            )
        finally:
            client.close()

    async def async_complete(self, redirect_uri: str) -> str:
        """Finish interactive sign-in and return a validated web cookie line."""
        callback = str(redirect_uri or "").strip()
        if not callback:
            raise SamsungAccountAuthError("Samsung redirect URI is empty")

        async with self._lock:
            try:
                jsessionid = await self.hass.async_add_executor_job(
                    self._complete_sync,
                    callback,
                )
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning(
                    "Samsung Account authentication completion failed (%s)",
                    type(err).__name__,
                )
                raise SamsungAccountAuthError(
                    "Samsung Account login could not be completed"
                ) from err

        return f"JSESSIONID={jsessionid}"

    def _complete_sync(self, redirect_uri: str) -> str:
        client = self._new_client()
        try:
            client.complete(redirect_uri)
            return str(client.web_session_cookie())
        finally:
            client.close()

    async def async_cookie(self, *, force_refresh: bool = False) -> str:
        """Return a valid cookie, rebuilding the web session when required."""
        async with self._lock:
            try:
                jsessionid = await self.hass.async_add_executor_job(
                    self._cookie_sync,
                    force_refresh,
                )
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning(
                    "Unable to obtain a Samsung Find web session (%s)",
                    type(err).__name__,
                )
                raise SamsungAccountAuthError(
                    "Samsung Find web session could not be renewed"
                ) from err

        return f"JSESSIONID={jsessionid}"

    def _cookie_sync(self, force_refresh: bool) -> str:
        client = self._new_client()
        try:
            return str(client.web_session_cookie(force_refresh=force_refresh))
        finally:
            client.close()

    async def async_status(self) -> dict[str, Any]:
        """Return non-secret readiness information for diagnostics."""
        async with self._lock:
            try:
                return await self.hass.async_add_executor_job(self._status_sync)
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug(
                    "Unable to read Samsung Account auth status (%s)",
                    type(err).__name__,
                )
                return {"authenticated": False}

    def _status_sync(self) -> dict[str, Any]:
        client = self._new_client()
        try:
            status = client.public_status()
            return dict(status) if isinstance(status, dict) else {}
        finally:
            client.close()

    async def async_remove(self) -> None:
        """Delete locally stored Samsung credentials when the entry is removed."""
        async with self._lock:
            await self.hass.async_add_executor_job(self._remove_sync)

    def _remove_sync(self) -> None:
        state_paths = (
            self._state_path,
            self._pending_path,
            self._master_path,
            self._legacy_state_path,
        )
        for path in state_paths:
            for candidate in (path, path.with_suffix(path.suffix + ".lock")):
                try:
                    candidate.unlink(missing_ok=True)
                except OSError as err:
                    _LOGGER.warning(
                        "Unable to remove Samsung Account state file %s (%s)",
                        candidate.name,
                        type(err).__name__,
                    )

        try:
            self._storage_dir.rmdir()
        except OSError:
            pass
