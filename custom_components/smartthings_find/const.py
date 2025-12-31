"""Constants for SmartThings Find integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "smartthings_find"

# 현재 안정 버전: 최소 엔티티만 유지
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
BATTERY_LEVEL_MAP: Final[dict[str, int]] = dict(BATTERY_LEVELS)

# ----------------------------
# SmartThings Find operation codes (minimal)
# ----------------------------
OP_RING: Final = "RING"
OP_CHECK_CONNECTION: Final = "CHECK_CONNECTION"
OP_CHECK_CONNECTION_WITH_LOCATION: Final = "CHECK_CONNECTION_WITH_LOCATION"

# Optional keys where support operation list might appear (kept for future)
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
