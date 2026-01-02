"""Constants for SmartThings Find integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "smartthings_find"

# ----------------------------
# Config / Options keys
# ----------------------------
CONF_COOKIE: Final = "cookie"

# Legacy keys (keep for backward compatibility)
CONF_COOKIE_INPUT: Final = "cookie_input"
CONF_JSESSIONID: Final = "jsessionid"

CONF_UPDATE_INTERVAL: Final = "update_interval"
CONF_UPDATE_INTERVAL_DEFAULT: Final = 120  # seconds

# ✅ NEW: keepalive interval (seconds)
CONF_KEEPALIVE_INTERVAL: Final = "keepalive_interval"
CONF_KEEPALIVE_INTERVAL_DEFAULT: Final = 300  # seconds (5 min)

# (저장 구조는 BOOL 유지)
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

# ✅ NEW: keepalive unsubscribe handle key
DATA_KEEPALIVE_UNSUB: Final = "keepalive_unsub"

# ----------------------------
# Battery mapping (서버 응답 문자열 -> 퍼센트)
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
# SmartThings Find operation codes (최소 사용만 유지)
# ----------------------------
OP_RING: Final = "RING"

# check / location update
OP_CHECK_CONNECTION: Final = "CHECK_CONNECTION"
OP_CHECK_CONNECTION_WITH_LOCATION: Final = "CHECK_CONNECTION_WITH_LOCATION"
