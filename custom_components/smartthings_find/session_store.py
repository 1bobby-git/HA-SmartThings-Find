"""Private runtime storage for rotating SmartThings Find cookies."""

from __future__ import annotations

from hashlib import sha256
import logging
from typing import Any

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import (
    AUTH_METHOD_ACCOUNT,
    AUTH_METHOD_COOKIE,
    CONF_AUTH_METHOD,
    CONF_COOKIE,
    DOMAIN,
)
from .utils import COOKIE_NAME_RE, STF_BASE, parse_cookie_header

_LOGGER = logging.getLogger(__name__)

_STORAGE_VERSION = 1
_STORE_RUNTIME_KEY = "_session_cookie_store"
_COOKIE_CACHE_KEY = "_session_cookie_cache"
_SOURCE_HASH_KEY = "source_hash"
_COOKIE_KEY = "cookie"


def _configured_cookie(entry: Any) -> str:
    return str(entry.data.get(CONF_COOKIE) or "").strip()


def _auth_method(entry: Any) -> str:
    value = str(entry.data.get(CONF_AUTH_METHOD) or "").strip()
    return value or AUTH_METHOD_COOKIE


def _source_identity(entry: Any) -> str:
    """Return a stable boundary preventing credentials crossing auth methods."""
    method = _auth_method(entry)
    if method == AUTH_METHOD_ACCOUNT:
        return f"{AUTH_METHOD_ACCOUNT}:{entry.entry_id}"

    configured = _configured_cookie(entry)
    if not configured:
        return ""
    return f"{AUTH_METHOD_COOKIE}:{configured}"


def _source_hash(source: str) -> str:
    return sha256(source.encode("utf-8")).hexdigest()


def _legacy_source_hash(entry: Any) -> str | None:
    """Return the v1.3.x cookie-store boundary for in-place migration."""
    if _auth_method(entry) != AUTH_METHOD_COOKIE:
        return None
    configured = _configured_cookie(entry)
    return _source_hash(configured) if configured else None


def _runtime_data(hass: HomeAssistant, entry_id: str) -> dict[str, Any]:
    return hass.data.setdefault(DOMAIN, {}).setdefault(entry_id, {})


def _store(hass: HomeAssistant, entry_id: str) -> Store:
    runtime = _runtime_data(hass, entry_id)
    store = runtime.get(_STORE_RUNTIME_KEY)
    if store is None:
        store = Store(
            hass,
            _STORAGE_VERSION,
            f"{DOMAIN}.{entry_id}.session",
            private=True,
            atomic_writes=True,
        )
        runtime[_STORE_RUNTIME_KEY] = store
    return store


def _fallback_cookie(entry: Any) -> str:
    if _auth_method(entry) == AUTH_METHOD_ACCOUNT:
        return ""
    return _configured_cookie(entry)


def _stored_cookie_snapshot(
    payload: Any,
    source_hashes: set[str | None],
) -> str | None:
    """Return a stored cookie snapshot, including an intentional empty marker."""
    if not isinstance(payload, dict):
        return None
    if payload.get(_SOURCE_HASH_KEY) not in source_hashes:
        return None
    cookie_line = payload.get(_COOKIE_KEY)
    if not isinstance(cookie_line, str):
        return None
    if cookie_line and not parse_cookie_header(cookie_line):
        return None
    return cookie_line


async def async_load_cookie_line(hass: HomeAssistant, entry: Any) -> str:
    """Load a rotated cookie only when it belongs to this auth source."""
    source = _source_identity(entry)
    fallback = _fallback_cookie(entry)
    if not source:
        return fallback

    source_hash = _source_hash(source)
    runtime = _runtime_data(hass, entry.entry_id)
    cached = runtime.get(_COOKIE_CACHE_KEY)
    cached_cookie = _stored_cookie_snapshot(cached, {source_hash})
    if cached_cookie is not None:
        return cached_cookie

    try:
        stored = await _store(hass, entry.entry_id).async_load()
    except Exception as err:  # noqa: BLE001
        _LOGGER.warning(
            "Unable to load the private SmartThings Find session store: %s",
            type(err).__name__,
        )
        return fallback

    legacy_hash = _legacy_source_hash(entry)
    accepted_hashes: set[str | None] = {source_hash}
    if legacy_hash is not None:
        accepted_hashes.add(legacy_hash)
    stored_cookie = _stored_cookie_snapshot(
        stored,
        accepted_hashes,
    )
    if stored_cookie is not None:
        payload = {
            _SOURCE_HASH_KEY: source_hash,
            _COOKIE_KEY: stored_cookie,
        }
        runtime[_COOKIE_CACHE_KEY] = payload
        if stored.get(_SOURCE_HASH_KEY) != source_hash:
            try:
                await _store(hass, entry.entry_id).async_save(payload)
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug(
                    "Unable to migrate the legacy session-store boundary (%s)",
                    type(err).__name__,
                )
        return stored_cookie

    runtime[_COOKIE_CACHE_KEY] = {
        _SOURCE_HASH_KEY: source_hash,
        _COOKIE_KEY: fallback,
    }
    return fallback


def _cookie_line_from_session(session: aiohttp.ClientSession) -> str:
    """Serialize the jar's current effective cookies without reviving deletions."""
    current: dict[str, str] = {}
    for name, morsel in session.cookie_jar.filter_cookies(STF_BASE).items():
        if COOKIE_NAME_RE.match(name):
            current[name] = morsel.value

    return "; ".join(
        f"{name}={value}" for name, value in sorted(current.items())
    )


async def persist_cookie_to_store(
    hass: HomeAssistant,
    entry: Any,
    session: aiohttp.ClientSession,
) -> None:
    """Persist the current effective jar in ``.storage``.

    The previous implementation merged the new jar into an older serialized
    cookie line. That could resurrect a cookie Samsung had explicitly removed.
    The dedicated session already contains the seed cookies, so the current jar
    is authoritative and must replace the stored snapshot, including an empty
    jar when the server has removed every cookie.
    """
    source = _source_identity(entry)
    if not source:
        return

    cookie_line = _cookie_line_from_session(session)
    payload = {
        _SOURCE_HASH_KEY: _source_hash(source),
        _COOKIE_KEY: cookie_line,
    }
    runtime = _runtime_data(hass, entry.entry_id)
    cached = runtime.get(_COOKIE_CACHE_KEY)
    if cached == payload:
        return

    await _store(hass, entry.entry_id).async_save(payload)
    runtime[_COOKIE_CACHE_KEY] = payload
    _LOGGER.debug(
        "Persisted current SmartThings Find cookie jar in private storage "
        "(cookie_count=%s)",
        len(parse_cookie_header(cookie_line)),
    )


async def async_remove_session_store(
    hass: HomeAssistant,
    entry_id: str,
) -> None:
    """Remove private cookie state when a config entry is deleted."""
    runtime = _runtime_data(hass, entry_id)
    try:
        await _store(hass, entry_id).async_remove()
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug(
            "Unable to remove SmartThings Find session store (%s)",
            type(err).__name__,
        )
    runtime.pop(_COOKIE_CACHE_KEY, None)
