"""Runtime compatibility for previously enrolled Samsung Account entries."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class SamsungAccountAuthError(Exception):
    """Raised when stored legacy Samsung Account authorization cannot be reused."""


class SamsungAccountAuth:
    """Reuse previously stored samsung-re-find credentials.

    New Samsung Account enrollment is no longer exposed by this integration.
    This adapter remains only so entries that completed the former native-app
    callback flow can renew a SmartThings Find web JSESSIONID until they are
    migrated to the supported Cookie header method.
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
        # Imported lazily so ordinary Cookie entries still load if the legacy
        # compatibility dependency is temporarily unavailable.
        from samsung_find.auth import SamsungAuth

        return SamsungAuth(
            state_path=self._state_path,
            pending_path=self._pending_path,
            master_path=self._master_path,
            legacy_state_path=self._legacy_state_path,
        )

    async def async_cookie(self, *, force_refresh: bool = False) -> str:
        """Return a valid web cookie from previously stored authorization."""
        async with self._lock:
            try:
                jsessionid = await self.hass.async_add_executor_job(
                    self._cookie_sync,
                    force_refresh,
                )
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning(
                    "Unable to obtain a legacy Samsung Find web session (%s)",
                    type(err).__name__,
                )
                raise SamsungAccountAuthError(
                    "Stored Samsung Account authorization could not renew "
                    "the Samsung Find web session"
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
                    "Unable to read legacy Samsung Account auth status (%s)",
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
        """Delete stored legacy credentials when removed or migrated."""
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
