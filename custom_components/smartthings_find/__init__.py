from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady

from .account_auth import SamsungAccountAuth
from .auth_manager import SmartThingsFindAuthManager
from .const import (
    AUTH_METHOD_ACCOUNT,
    CONF_ACTIVE_MODE_OTHERS,
    CONF_ACTIVE_MODE_OTHERS_DEFAULT,
    CONF_ACTIVE_MODE_SMARTTAGS,
    CONF_ACTIVE_MODE_SMARTTAGS_DEFAULT,
    CONF_AUTH_METHOD,
    CONF_KEEPALIVE_INTERVAL,
    CONF_KEEPALIVE_INTERVAL_DEFAULT,
    CONF_UPDATE_INTERVAL,
    CONF_UPDATE_INTERVAL_DEFAULT,
    DATA_AUTH_MANAGER,
    DATA_COORDINATOR,
    DATA_DEVICES,
    DATA_SESSION,
    DOMAIN,
)
from .coordinator import SmartThingsFindCoordinator
from .device_inventory import migrate_registered_identifiers
from .session_store import async_remove_session_store
from .utils import (
    auth_failure_is_persistent,
    clear_auth_failure,
    make_session,
)

PLATFORMS = [Platform.DEVICE_TRACKER, Platform.SENSOR, Platform.BUTTON]


async def async_setup(hass: HomeAssistant, _config) -> bool:
    hass.data.setdefault(DOMAIN, {})
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload after a user changes options or authentication settings."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up one SmartThings Find account."""
    hass.data.setdefault(DOMAIN, {})
    runtime = hass.data[DOMAIN].setdefault(entry.entry_id, {})

    active_smarttags = entry.options.get(
        CONF_ACTIVE_MODE_SMARTTAGS,
        CONF_ACTIVE_MODE_SMARTTAGS_DEFAULT,
    )
    active_others = entry.options.get(
        CONF_ACTIVE_MODE_OTHERS,
        CONF_ACTIVE_MODE_OTHERS_DEFAULT,
    )

    try:
        update_interval_s = int(
            entry.options.get(
                CONF_UPDATE_INTERVAL,
                CONF_UPDATE_INTERVAL_DEFAULT,
            )
        )
    except (TypeError, ValueError):
        update_interval_s = int(CONF_UPDATE_INTERVAL_DEFAULT)

    try:
        keepalive_interval_s = int(
            entry.options.get(
                CONF_KEEPALIVE_INTERVAL,
                CONF_KEEPALIVE_INTERVAL_DEFAULT,
            )
        )
    except (TypeError, ValueError):
        keepalive_interval_s = int(CONF_KEEPALIVE_INTERVAL_DEFAULT)

    runtime.update(
        {
            CONF_ACTIVE_MODE_SMARTTAGS: bool(active_smarttags),
            CONF_ACTIVE_MODE_OTHERS: bool(active_others),
        }
    )

    session = make_session(hass)
    auth_manager = SmartThingsFindAuthManager(hass, entry, session)
    coordinator = None

    try:
        await auth_manager.async_initialize()
        devices = await auth_manager.async_get_devices()

        coordinator = SmartThingsFindCoordinator(
            hass=hass,
            entry=entry,
            session=session,
            auth_manager=auth_manager,
            devices=devices,
            update_interval_s=update_interval_s,
            keepalive_interval_s=keepalive_interval_s,
        )
        await coordinator.async_config_entry_first_refresh()

        runtime.update(
            {
                DATA_SESSION: session,
                DATA_AUTH_MANAGER: auth_manager,
                DATA_COORDINATOR: coordinator,
                DATA_DEVICES: devices,
            }
        )

        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        migrate_registered_identifiers(hass, entry.entry_id, devices)
        entry.async_on_unload(entry.add_update_listener(_async_update_listener))
        clear_auth_failure(hass, entry.entry_id)
        return True

    except ConfigEntryAuthFailed as err:
        if coordinator is not None:
            try:
                await coordinator.async_shutdown()
            except Exception:  # noqa: BLE001
                pass
        try:
            await auth_manager.async_shutdown()
        except Exception:  # noqa: BLE001
            pass
        try:
            await session.close()
        except Exception:  # noqa: BLE001
            pass

        for key in (DATA_SESSION, DATA_AUTH_MANAGER, DATA_COORDINATOR, DATA_DEVICES):
            runtime.pop(key, None)

        if not auth_failure_is_persistent(hass, entry.entry_id):
            raise ConfigEntryNotReady(
                "SmartThings Find authentication is temporarily unavailable; "
                "automatic session recovery will retry before reauthentication"
            ) from err
        raise

    except Exception as err:
        if coordinator is not None:
            try:
                await coordinator.async_shutdown()
            except Exception:  # noqa: BLE001
                pass
        try:
            await auth_manager.async_shutdown()
        except Exception:  # noqa: BLE001
            pass
        try:
            await session.close()
        except Exception:  # noqa: BLE001
            pass
        for key in (DATA_SESSION, DATA_AUTH_MANAGER, DATA_COORDINATOR, DATA_DEVICES):
            runtime.pop(key, None)
        raise ConfigEntryNotReady(f"SmartThings Find setup failed: {err}") from err


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload one SmartThings Find account."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    data = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if data:
        coordinator = data.get(DATA_COORDINATOR)
        if coordinator:
            await coordinator.async_shutdown()

        auth_manager = data.get(DATA_AUTH_MANAGER)
        if auth_manager:
            await auth_manager.async_shutdown()

        session = data.get(DATA_SESSION)
        if session:
            await session.close()

    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove private credentials when the user deletes the integration."""
    await async_remove_session_store(hass, entry.entry_id)
    if entry.data.get(CONF_AUTH_METHOD) == AUTH_METHOD_ACCOUNT:
        await SamsungAccountAuth(hass).async_remove()
    hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
