DOMAIN = "smartthings_find"

# Config keys
CONF_COOKIE = "cookie"
CONF_UPDATE_INTERVAL = "update_interval"
CONF_UPDATE_INTERVAL_DEFAULT = 60

CONF_ACTIVE_MODE_SMARTTAGS = "active_mode_smarttags"
CONF_ACTIVE_MODE_SMARTTAGS_DEFAULT = False

CONF_ACTIVE_MODE_OTHERS = "active_mode_others"
CONF_ACTIVE_MODE_OTHERS_DEFAULT = False

# ✅ 0.3.16: SmartThings official device mapping (Options)
# - device_registry id 선택(드롭다운)
CONF_ST_DEVICE_ID = "smartthings_device_id"
# - 실제 병합에 사용할 identifier를 안전한 문자열로 저장 (JSON-safe)
#   format: "smartthings::<identifier_value>"
CONF_ST_IDENTIFIER = "smartthings_identifier"

# Internal keys stored in hass.data[DOMAIN][entry_id]
DATA_SESSION = "session"
DATA_CSRF = "_csrf"
DATA_DEVICES = "devices"
DATA_COORDINATOR = "coordinator"
DATA_KEEPALIVE_CANCEL = "keepalive_cancel"

# Battery mapping (SmartThings Find sometimes returns discrete levels)
BATTERY_LEVELS = {
    "VERY_LOW": 5,
    "LOW": 15,
    "MEDIUM": 50,
    "HIGH": 80,
    "FULL": 100,
}
