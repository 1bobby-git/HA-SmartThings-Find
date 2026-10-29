"""Private runtime storage for rotating SmartThings Find cookies."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
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
    DIAG_COOKIE_STORE,
    DOMAIN,
)
from .utils import (
    COOKIE_NAME_RE,
    STF_BASE,
    STF_HOST,
    cookie_scope,
    is_stf_cookie_domain,
    parse_cookie_header,
)

_LOGGER = logging.getLogger(__name__)

_STORAGE_VERSION = 1
_STORE_RUNTIME_KEY = "_session_cookie_store"
_COOKIE_CACHE_KEY = "_session_cookie_cache"
_SOURCE_HASH_KEY = "source_hash"
_COOKIE_KEY = "cookie"
# Cookies the Cookie-header line cannot describe: a parent Domain or a non-root
# Path. Older releases ignore this key and keep reading the line.
_SCOPED_KEY = "scoped"
MAX_SCOPED_COOKIES = 50
_SCOPED_FIELDS = ("name", "value", "domain", "path")


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


def _record_store_diag(hass: HomeAssistant, entry_id: str, **values: Any) -> None:
    """Remember cookie store activity for diagnostics: sources, counts, times."""
    record = _runtime_data(hass, entry_id).setdefault(DIAG_COOKIE_STORE, {})
    record.update(values)


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


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


def _valid_scoped_record(record: Any) -> dict[str, str] | None:
    if not isinstance(record, dict):
        return None
    values = {field: record.get(field) for field in _SCOPED_FIELDS}
    if not all(isinstance(value, str) for value in values.values()):
        return None
    if not COOKIE_NAME_RE.match(values["name"]):
        return None
    if any(char in values["value"] for char in ";\r\n"):
        return None
    if not values["path"].startswith("/") or not is_stf_cookie_domain(values["domain"]):
        return None
    return values


def _stored_scoped_cookies(payload: Any) -> list[dict[str, str]]:
    raw = payload.get(_SCOPED_KEY) if isinstance(payload, dict) else None
    if not isinstance(raw, list):
        return []
    records: list[dict[str, str]] = []
    for item in raw:
        record = _valid_scoped_record(item)
        if record is not None:
            records.append(record)
        if len(records) >= MAX_SCOPED_COOKIES:
            break
    return records


async def async_load_cookie_line(hass: HomeAssistant, entry: Any) -> str:
    """Load a rotated cookie only when it belongs to this auth source."""
    cookie_line, _scoped = await async_load_session_cookies(hass, entry)
    return cookie_line


async def async_load_session_cookies(
    hass: HomeAssistant,
    entry: Any,
) -> tuple[str, list[dict[str, str]]]:
    """Load the rotated cookie line and its domain/path-scoped cookies."""
    source = _source_identity(entry)
    fallback = _fallback_cookie(entry)
    if not source:
        return fallback, []

    source_hash = _source_hash(source)
    runtime = _runtime_data(hass, entry.entry_id)
    cached = runtime.get(_COOKIE_CACHE_KEY)
    cached_cookie = _stored_cookie_snapshot(cached, {source_hash})
    if cached_cookie is not None:
        return cached_cookie, _stored_scoped_cookies(cached)

    try:
        stored = await _store(hass, entry.entry_id).async_load()
    except Exception as err:  # noqa: BLE001
        _LOGGER.warning(
            "Unable to load the private SmartThings Find session store: %s",
            type(err).__name__,
        )
        _record_store_diag(
            hass,
            entry.entry_id,
            loaded_from="configured_cookie" if fallback else "none",
            load_error=type(err).__name__,
            loaded_at=_now_iso(),
        )
        return fallback, []

    legacy_hash = _legacy_source_hash(entry)
    accepted_hashes: set[str | None] = {source_hash}
    if legacy_hash is not None:
        accepted_hashes.add(legacy_hash)
    stored_cookie = _stored_cookie_snapshot(
        stored,
        accepted_hashes,
    )
    if stored_cookie is not None:
        scoped = _stored_scoped_cookies(stored)
        payload: dict[str, Any] = {
            _SOURCE_HASH_KEY: source_hash,
            _COOKIE_KEY: stored_cookie,
        }
        if scoped:
            payload[_SCOPED_KEY] = scoped
        runtime[_COOKIE_CACHE_KEY] = payload
        if stored.get(_SOURCE_HASH_KEY) != source_hash:
            try:
                await _store(hass, entry.entry_id).async_save(payload)
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug(
                    "Unable to migrate the legacy session-store boundary (%s)",
                    type(err).__name__,
                )
        _record_store_diag(
            hass,
            entry.entry_id,
            loaded_from="saved_session",
            load_error=None,
            loaded_at=_now_iso(),
        )
        return stored_cookie, scoped

    runtime[_COOKIE_CACHE_KEY] = {
        _SOURCE_HASH_KEY: source_hash,
        _COOKIE_KEY: fallback,
    }
    _record_store_diag(
        hass,
        entry.entry_id,
        loaded_from="configured_cookie" if fallback else "none",
        load_error=None,
        loaded_at=_now_iso(),
    )
    return fallback, []


def _cookie_line_from_session(session: aiohttp.ClientSession) -> str:
    """Serialize the jar's current effective cookies without reviving deletions."""
    current: dict[str, str] = {}
    for name, morsel in session.cookie_jar.filter_cookies(STF_BASE).items():
        if COOKIE_NAME_RE.match(name):
            current[name] = morsel.value

    return "; ".join(
        f"{name}={value}" for name, value in sorted(current.items())
    )


def _scoped_cookies_from_session(session: aiohttp.ClientSession) -> list[dict[str, str]]:
    """Describe cookies whose Domain or Path the Cookie-header line would lose.

    Parent-domain cookies on ``/`` stay in the line as well, so a downgrade keeps
    sending them; restoring both is reconciled by ``normalize_cookie_jar``.
    """
    try:
        morsels = list(session.cookie_jar)
    except TypeError:
        return []

    records: dict[tuple[str, str, str], dict[str, str]] = {}
    for morsel in morsels:
        name = getattr(morsel, "key", "")
        if not isinstance(name, str) or not COOKIE_NAME_RE.match(name):
            continue
        domain, path = cookie_scope(morsel)
        if not is_stf_cookie_domain(domain) or (domain, path) == (STF_HOST, "/"):
            continue
        record = _valid_scoped_record(
            {"name": name, "value": str(morsel.value), "domain": domain, "path": path}
        )
        if record is not None:
            records[(domain, path, name)] = record
    return [records[key] for key in sorted(records)][:MAX_SCOPED_COOKIES]


def _cookie_store_lock(hass: HomeAssistant, entry_id: str) -> asyncio.Lock:
    """Serialize snapshots per entry without blocking other accounts."""
    runtime = _runtime_data(hass, entry_id)
    lock = runtime.get("_session_cookie_store_lock")
    if lock is None:
        lock = asyncio.Lock()
        runtime["_session_cookie_store_lock"] = lock
    return lock


async def persist_cookie_to_store(
    hass: HomeAssistant,
    entry: Any,
    session: aiohttp.ClientSession,
) -> None:
    """Take the latest jar snapshot only after earlier writes have finished."""
    async with _cookie_store_lock(hass, entry.entry_id):
        await _persist_cookie_to_store_locked(hass, entry, session)


async def _persist_cookie_to_store_locked(
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
    payload: dict[str, Any] = {
        _SOURCE_HASH_KEY: _source_hash(source),
        _COOKIE_KEY: cookie_line,
    }
    scoped = _scoped_cookies_from_session(session)
    if scoped:
        payload[_SCOPED_KEY] = scoped
    runtime = _runtime_data(hass, entry.entry_id)
    cached = runtime.get(_COOKIE_CACHE_KEY)
    if cached == payload:
        return

    await _store(hass, entry.entry_id).async_save(payload)
    runtime[_COOKIE_CACHE_KEY] = payload
    cookie_count = len(parse_cookie_header(cookie_line))
    _record_store_diag(
        hass,
        entry.entry_id,
        last_saved=_now_iso(),
        saved_cookie_count=cookie_count,
        saved_scoped_count=len(scoped),
    )
    _LOGGER.debug(
        "Persisted current SmartThings Find cookie jar in private storage "
        "(cookie_count=%s, scoped_count=%s)",
        cookie_count,
        len(scoped),
    )


async def async_remove_session_store(
    hass: HomeAssistant,
    entry_id: str,
) -> None:
    """Wait for in-flight writes before deleting a session snapshot."""
    async with _cookie_store_lock(hass, entry_id):
        await _async_remove_session_store_locked(hass, entry_id)


async def _async_remove_session_store_locked(
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
