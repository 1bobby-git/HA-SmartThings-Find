"""Constants for SmartThings Find integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "smartthings_find"

# Home Assistant platforms
PLATFORMS: Final[list[str]] = ["device_tracker", "sensor", "button"]

# ----------------------------
# Config / Options keys
# ----------------------------

# Current auth input (Cookie header line)
CONF_COOKIE: Final = "cookie"

# Legacy keys (backward compatibility)
CONF_COOKIE_INPUT: Final = "cookie_input"
CONF_JSESSIONID: Final = "jsessionid"

# Options
CONF_UPDATE_INTERVAL: Final = "update_interval"
CONF_UPDATE_INTERVAL_DEFAULT: Final = 120  # seconds

CONF_ACTIVE_MODE_SMARTTAGS: Final = "active_mode_smarttags"
CONF_ACTIVE_MODE_SMARTTAGS_DEFAULT: Final = True

CONF_ACTIVE_MODE_OTHERS: Final = "active_mode_others"
CONF_ACTIVE_MODE_OTHERS_DEFAULT: Final = False

# (If still present in older config/options flows, keep for compatibility)
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
# Battery level mapping
# ----------------------------
# ✅ 반드시 이름이 BATTERY_LEVELS 여야 함 (현재 네 config_flow가 이 이름을 import 중)
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

# Some code may refer to BATTERY_LEVEL_MAP (keep alias to be safe)
BATTERY_LEVEL_MAP: Final[dict[str, int]] = dict(BATTERY_LEVELS)

# ----------------------------
# SmartThings Find operation codes
# ----------------------------
# Button / actions import safety: these MUST exist if referenced by button.py etc.
OP_RING: Final = "RING"
OP_CHECK_CONNECTION_WITH_LOCATION: Final = "CHECK_CONNECTION_WITH_LOCATION"

# ✅ 이전 로그의 ImportError 원인 해결
OP_LOCK: Final = "LOCK"

# Other operations (best-effort; backend can vary)
OP_ERASE: Final = "ERASE"
OP_TRACK: Final = "TRACK_LOCATION"
OP_EXTEND_BATTERY: Final = "EXTEND_BATTERY"

# STF responses can use different keys for operation lists; used by parsers.
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
