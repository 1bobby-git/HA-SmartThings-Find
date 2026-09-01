"""Private runtime storage for rotating SmartThings Find cookies."""

from __future__ import annotations

from hashlib import sha256
import logging
from typing import Any

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import CONF_COOKIE, DOMAIN
from .utils import COOKIE_NAME_RE, STF_BASE, parse_cookie_header

_LOGGER = logging.getLogger(__name__)

_STORAGE_VERSION = 1
_STORE_RUNTIME_KEY = "_session_cookie_store"
_COOKIE_CACHE_KEY = "_session_cookie_cache"
_SOURCE_HASH_KEY = "source_hash"
_COOKIE_KEY = "cookie"


def _configured_cookie(entry: Any) -> str:
    return str(entry.data.get(CONF_COOKIE) or "").strip()


def _source_hash(cookie_line: str) -> str:
    return sha256(cookie_line.encode("utf-8")).hexdigest()


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


async def async_load_cookie_line(hass: HomeAssistant, entry: Any) -> str:
    """Load a rotated cookie only when it belongs to the configured cookie seed."""
    configured = _configured_cookie(entry)
    if not configured:
        return ""

    source_hash = _source_hash(configured)
    runtime = _runtime_data(hass, entry.entry_id)
    cached = runtime.get(_COOKIE_CACHE_KEY)
    if (
        isinstance(cached, dict)
        and cached.get(_SOURCE_HASH_KEY) == source_hash
        and isinstance(cached.get(_COOKIE_KEY), str)
        and parse_cookie_header(cached[_COOKIE_KEY])
    ):
        return cached[_COOKIE_KEY]

    try:
        stored = await _store(hass, entry.entry_id).async_load()
    except Exception as err:  # noqa: BLE001
        _LOGGER.warning(
            "Unable to load the private SmartThings Find session store: %s",
            type(err).__name__,
        )
        return configured

    if (
        isinstance(stored, dict)
        and stored.get(_SOURCE_HASH_KEY) == source_hash
        and isinstance(stored.get(_COOKIE_KEY), str)
        and parse_cookie_header(stored[_COOKIE_KEY])
    ):
        runtime[_COOKIE_CACHE_KEY] = stored
        return stored[_COOKIE_KEY]

    runtime[_COOKIE_CACHE_KEY] = {
        _SOURCE_HASH_KEY: source_hash,
        _COOKIE_KEY: configured,
    }
    return configured


async def persist_cookie_to_store(
    hass: HomeAssistant,
    entry: Any,
    session: aiohttp.ClientSession,
) -> None:
    """Persist cookie rotation in ``.storage`` without mutating the config entry."""
    configured = _configured_cookie(entry)
    if not configured:
        return

    existing_line = await async_load_cookie_line(hass, entry)
    existing = parse_cookie_header(existing_line)
    current: dict[str, str] = {}
    for name, morsel in session.cookie_jar.filter_cookies(STF_BASE).items():
        if COOKIE_NAME_RE.match(name):
            current[name] = morsel.value

    if not current:
        return

    merged = dict(existing)
    merged.update(current)
    cookie_line = "; ".join(
        f"{name}={value}" for name, value in sorted(merged.items())
    )
    if not cookie_line:
        return

    source_hash = _source_hash(configured)
    payload = {
        _SOURCE_HASH_KEY: source_hash,
        _COOKIE_KEY: cookie_line,
    }
    runtime = _runtime_data(hass, entry.entry_id)
    cached = runtime.get(_COOKIE_CACHE_KEY)
    if cached == payload:
        return

    await _store(hass, entry.entry_id).async_save(payload)
    runtime[_COOKIE_CACHE_KEY] = payload
    _LOGGER.debug(
        "Persisted rotated SmartThings Find cookies in private storage (length=%s)",
        len(cookie_line),
    )
