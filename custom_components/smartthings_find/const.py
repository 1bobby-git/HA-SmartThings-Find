"""Constants for the SmartThings Find integration."""

from __future__ import annotations

DOMAIN = "smartthings_find"

# ===== Config keys =====
# Current (0.3.x) cookie input key
CONF_COOKIE = "cookie"

# Legacy keys kept for backwards compatibility / migrations
CONF_COOKIE_INPUT = "cookie_input"
CONF_JSESSIONID = "jsessionid"

CONF_UPDATE_INTERVAL = "update_interval"
CONF_UPDATE_INTERVAL_DEFAULT = 120

CONF_ACTIVE_MODE_SMARTTAGS = "active_mode_smarttags"
CONF_ACTIVE_MODE_SMARTTAGS_DEFAULT = True

CONF_ACTIVE_MODE_OTHERS = "active_mode_others"
CONF_ACTIVE_MODE_OTHERS_DEFAULT = False

# (0.3.16에서 들어온 “SmartThings 공식 통합 디바이스 매핑” 관련 옵션 키)
# 너 요구사항대로 config_flow에서 더 이상 강제 선택은 하지 않지만,
# 기존 설치/옵션 값이 남아있을 수 있어서 키는 유지 (하위호환)
CONF_ST_DEVICE_ID = "st_device_id"
CONF_ST_IDENTIFIER = "st_identifier"

# ===== hass.data keys =====
DATA_SESSION = "session"
DATA_COORDINATOR = "coordinator"
DATA_DEVICES = "devices"
DATA_KEEPALIVE_CANCEL = "keepalive_cancel"

# ===== SmartThings Find operation codes =====
# NOTE: STF 웹(Find)에서 사용하는 operation 문자열들.
# 버튼 플랫폼이 import하는 OP_*는 반드시 여기에 존재해야 함.
OP_RING = "RING"
OP_CHECK_CONNECTION_WITH_LOCATION = "CHECK_CONNECTION_WITH_LOCATION"

# 아래 4개가 지금 너 로그에서 핵심 (특히 OP_LOCK)
OP_LOCK = "LOCK"
OP_ERASE = "ERASE"
OP_TRACK = "TRACK_LOCATION"
OP_EXTEND_BATTERY = "EXTEND_BATTERY"

# STF 응답에서 operation list가 들어오는 키 후보들(서버가 케이스/키를 바꿀 때 대비)
OPERATION_LIST_KEYS: tuple[str, ...] = (
    "supportOperations",
    "supportOperationList",
    "operationList",
    "operations",
    "oprnList",
    "oprnTypeList",
    "funcList",
    "functionList",
)
