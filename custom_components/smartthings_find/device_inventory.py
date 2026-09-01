"""SmartThings Find inventory adapted to Home Assistant's single-entry devices."""

from __future__ import annotations

import html
import json
import logging
from typing import Any

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN
from .utils import STF_BASE, URL_DEVICE_LIST, fetch_csrf

_LOGGER = logging.getLogger(__name__)


def _existing_device(
    hass: HomeAssistant,
    entry_id: str,
    identifier: tuple[str, str],
):
    """Return this config entry's device on new HA, with an old-HA fallback."""
    config_entries = getattr(hass, "config_entries", None)
    get_entry = getattr(config_entries, "async_get_entry", None)
    if not callable(get_entry) or get_entry(entry_id) is None:
        return None

    registry = device_registry.async_get(hass)
    scoped_get = getattr(registry, "async_get_device_by_identifier", None)
    if callable(scoped_get):
        return scoped_get(identifier, entry_id)

    legacy_get = getattr(registry, "async_get_device", None)
    if callable(legacy_get):
        return legacy_get({identifier})
    return None


def migrate_registered_identifiers(
    hass: HomeAssistant,
    entry_id: str,
    devices: list[dict[str, Any]],
) -> None:
    """Remove identifiers borrowed from other integrations in older releases."""
    registry = device_registry.async_get(hass)
    update_device = getattr(registry, "async_update_device", None)
    if not callable(update_device):
        return

    for device in devices:
        raw = device.get("data") or {}
        device_id = raw.get("dvceID")
        if device_id is None:
            continue
        identifier = (DOMAIN, str(device_id))
        existing = _existing_device(hass, entry_id, identifier)
        if existing is None or set(existing.identifiers) == {identifier}:
            continue
        update_device(existing.id, new_identifiers={identifier})
        _LOGGER.debug(
            "Migrated SmartThings Find device identifiers for %s",
            str(device_id),
        )


async def get_devices(
    hass: HomeAssistant,
    session: aiohttp.ClientSession,
    entry_id: str,
) -> list[dict[str, Any]]:
    """Fetch devices without borrowing identifiers from another integration."""
    hass.data.setdefault(DOMAIN, {}).setdefault(entry_id, {})
    csrf = hass.data[DOMAIN][entry_id].get("_csrf")
    if not csrf:
        csrf = await fetch_csrf(hass, session, entry_id)

    url = URL_DEVICE_LIST.update_query({"_csrf": csrf})
    async with session.post(
        url,
        headers={"Accept": "application/json"},
        data={},
    ) as response:
        body = await response.text()
        stripped = body.strip()
        if stripped in {"Logout", "fail"}:
            raise ConfigEntryAuthFailed(
                f"Session expired while fetching devices: body='{stripped}'"
            )
        if response.status != 200:
            if response.status in {401, 403}:
                raise ConfigEntryAuthFailed(
                    f"Session invalid while fetching devices: {response.status}"
                )
            raise RuntimeError(
                f"SmartThings Find device inventory failed: HTTP {response.status}"
            )

    try:
        payload = json.loads(body) if body else {}
    except json.JSONDecodeError as err:
        raise RuntimeError("SmartThings Find returned invalid device JSON") from err

    raw_devices = payload.get("deviceList", [])
    if not isinstance(raw_devices, list):
        raise RuntimeError("SmartThings Find device inventory has an invalid shape")

    devices: list[dict[str, Any]] = []
    for raw_device in raw_devices:
        if not isinstance(raw_device, dict):
            continue
        device_id = raw_device.get("dvceID")
        if device_id is None or str(device_id).strip() == "":
            continue

        data = dict(raw_device)
        data["modelName"] = html.unescape(
            html.unescape(str(data.get("modelName") or ""))
        )
        model_name = data["modelName"] or str(device_id)
        identifier = (DOMAIN, str(device_id))
        existing = _existing_device(hass, entry_id, identifier)
        if existing is not None and existing.disabled:
            _LOGGER.debug("Ignoring disabled SmartThings Find device: %s", model_name)
            continue

        devices.append(
            {
                "data": data,
                "ha_dev_info": DeviceInfo(
                    identifiers={identifier},
                    manufacturer="Samsung",
                    name=model_name,
                    model=str(data.get("modelID") or ""),
                    configuration_url=str(STF_BASE),
                ),
            }
        )

    return devices
