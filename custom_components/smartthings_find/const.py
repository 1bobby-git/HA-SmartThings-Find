from __future__ import annotations

DOMAIN = "smartthings_find"

# Config keys
CONF_COOKIE_INPUT = "cookie_input"
CONF_JSESSIONID = "jsessionid"  # legacy alias

CONF_UPDATE_INTERVAL = "update_interval"
CONF_UPDATE_INTERVAL_DEFAULT = 60

CONF_ACTIVE_MODE_SMARTTAGS = "active_mode_smarttags"
CONF_ACTIVE_MODE_SMARTTAGS_DEFAULT = True

CONF_ACTIVE_MODE_OTHERS = "active_mode_others"
CONF_ACTIVE_MODE_OTHERS_DEFAULT = False

PLATFORMS = ["device_tracker", "sensor", "button"]

# URLs
STF_BASE = "https://smartthingsfind.samsung.com"
URL_GET_CSRF = f"{STF_BASE}/chkLogin.do"
URL_DEVICE_LIST = f"{STF_BASE}/device/getDeviceList.do"
URL_REQUEST_OPERATION = f"{STF_BASE}/dm/addOperation.do"
URL_SET_LAST_DEVICE = f"{STF_BASE}/device/setLastSelect.do"

# SmartThings official integration domain (device merge purpose)
SMARTTHINGS_DOMAIN = "smartthings"

# Operations
OP_RING = "RING"
OP_CHECK_CONNECTION_WITH_LOCATION = "CHECK_CONNECTION_WITH_LOCATION"

# Battery mapping (fallback: int parsing)
BATTERY_LEVELS = {
    "0": 0, "1": 5, "2": 10, "3": 15, "4": 20, "5": 25, "6": 30, "7": 35, "8": 40, "9": 45,
    "10": 50, "11": 55, "12": 60, "13": 65, "14": 70, "15": 75, "16": 80, "17": 85, "18": 90, "19": 95, "20": 100,
}
