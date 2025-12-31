"""Constants for SmartThings Find integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "smartthings_find"

# Home Assistant platforms
PLATFORMS: Final[list[str]] = ["device_tracker", "sensor", "button"]

# ----------------------------
# Config / Options keys
# ----------------------------
CONF_COOKIE: Final = "cookie"

# Legacy keys (keep for backward compatibility)
CONF_COOKIE_INPUT: Final = "cookie_input"
CONF_JSESSIONID: Final = "jsessionid"

CONF_UPDATE_INTERVAL: Final = "update_interval"
CONF_UPDATE_INTERVAL_DEFAULT: Final = 120  # seconds

CONF_ACTIVE_MODE_SMARTTAGS: Final = "active_mode_smarttags"
CONF_ACTIVE_MODE_SMARTTAGS_DEFAULT: Final = True

CONF_ACTIVE_MODE_OTHERS: Final = "active_mode_others"
CONF_ACTIVE_MODE_OTHERS_DEFAULT: Final = False

# Older flows/options might still reference these (keep for compatibility)
CONF_ST_DEVICE_ID: Final = "st_device_id"
CONF_ST_IDENTIFIER: Final = "st_identifier"

# ----------------------------
# hass.data keys
# ----------------------------
DATA_SESSION: Final = "session"
DATA_COORDINATOR: Final = "coordinator"
DATA_DEVICES: Final = "devices"
DATA_KEEPALIVE_CANCEL: Final = "keepalive_cancel"

# ----------------------------
# Battery mapping
# ----------------------------
# ✅ Some modules import BATTERY_LEVELS (must exist)
BATTERY_LEVELS: Final[dict[str, int]] = {
    "FULL": 100,
    "HIGH": 80,
    "NORMAL": 50,
    "MEDIUM": 50,
    "LOW": 15,
    "VERY_LOW": 5,
    "EMPTY": 0,
    "NONE": 0,
}

# Alias for older code that expects BATTERY_LEVEL_MAP
BATTERY_LEVEL_MAP: Final[dict[str, int]] = dict(BATTERY_LEVELS)

# ----------------------------
# SmartThings Find operation codes
# ----------------------------
OP_RING: Final = "RING"

# ✅ 이번 에러 원인: OP_CHECK_CONNECTION missing
OP_CHECK_CONNECTION: Final = "CHECK_CONNECTION"

# Variant used by some device types / endpoints
OP_CHECK_CONNECTION_WITH_LOCATION: Final = "CHECK_CONNECTION_WITH_LOCATION"

# ✅ 이전 에러 원인: OP_LOCK missing
OP_LOCK: Final = "LOCK"

# Other operations (availability depends on device/account)
OP_ERASE: Final = "ERASE"
OP_TRACK: Final = "TRACK_LOCATION"
OP_EXTEND_BATTERY: Final = "EXTEND_BATTERY"

# STF responses can use different keys for operation lists
OPERATION_LIST_KEYS: Final[tuple[str, ...]] = (
    "supportOperations",
    "supportOperationList",
    "operationList",
    "operations",
    "oprnList",
    "oprnTypeList",
    "funcList",
    "functionList",
)
