"""Constants for SmartThings Find integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "smartthings_find"

# ✅ 0.3.22.1: switch 추가 (찾으면 알림 받기)
PLATFORMS: Final[list[str]] = ["device_tracker", "sensor", "button", "switch"]

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
# SmartThings Find operation codes (best-effort)
# ----------------------------
OP_RING: Final = "RING"

# check / location update
OP_CHECK_CONNECTION: Final = "CHECK_CONNECTION"
OP_CHECK_CONNECTION_WITH_LOCATION: Final = "CHECK_CONNECTION_WITH_LOCATION"

# phone actions (best-effort)
OP_LOCK: Final = "LOCK"
OP_ERASE: Final = "ERASE"
OP_TRACK: Final = "TRACK_LOCATION"
OP_EXTEND_BATTERY: Final = "EXTEND_BATTERY"

# ✅ 0.3.22.1: "찾으면 알림 받기" (Notify when found) best-effort candidates
# 실제 op 값은 계정/지역/기기별로 다를 수 있어 후보를 순차 시도한다.
NOTIFY_WHEN_FOUND_ON_OPS: Final[list[str]] = [
    "NOTIFY_WHEN_FOUND",
    "NOTIFY_FOUND",
    "NOTIFY_WHEN_FOUND_ON",
    "NOTIFY_WHEN_FOUND_START",
    "FOUND_NOTIFY_ON",
]
NOTIFY_WHEN_FOUND_OFF_OPS: Final[list[str]] = [
    "NOTIFY_WHEN_FOUND_OFF",
    "NOTIFY_WHEN_FOUND_STOP",
    "FOUND_NOTIFY_OFF",
    "CANCEL_NOTIFY_WHEN_FOUND",
]

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
