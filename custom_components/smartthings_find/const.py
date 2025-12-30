DOMAIN = "smartthings_find"

# Config keys
CONF_COOKIE = "cookie"
CONF_UPDATE_INTERVAL = "update_interval"
CONF_UPDATE_INTERVAL_DEFAULT = 60

CONF_ACTIVE_MODE_SMARTTAGS = "active_mode_smarttags"
CONF_ACTIVE_MODE_SMARTTAGS_DEFAULT = False

CONF_ACTIVE_MODE_OTHERS = "active_mode_others"
CONF_ACTIVE_MODE_OTHERS_DEFAULT = False

# SmartThings official device mapping (Options)
CONF_ST_DEVICE_ID = "smartthings_device_id"
# JSON-safe string: "smartthings::<identifier_value>"
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

# =========================
# ✅ SmartThings Find Operations (single source of truth)
# =========================
OP_RING = "RING"
OP_CHECK_CONNECTION_WITH_LOCATION = "CHECK_CONNECTION_WITH_LOCATION"
OP_CHECK_CONNECTION = "CHECK_CONNECTION"

# Phone / non-tag actions (best-effort; Samsung can change backend anytime)
OP_LOCK = "LOCK"
OP_ERASE = "ERASE"
OP_TRACK = "TRACKING"
OP_EXTEND_BATTERY = "EXTEND_BATTERY"
