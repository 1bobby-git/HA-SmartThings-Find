from __future__ import annotations

DOMAIN = "smartthings_find"

# Config
CONF_COOKIE_INPUT = "cookie_input"

# Backward-compat (예전 코드/임포트 오류 방지용)
CONF_JSESSIONID = "jsessionid"  # legacy alias (더이상 UI에선 안 씀)

CONF_UPDATE_INTERVAL = "update_interval"
CONF_UPDATE_INTERVAL_DEFAULT = 60

CONF_ACTIVE_MODE_SMARTTAGS = "active_mode_smarttags"
CONF_ACTIVE_MODE_SMARTTAGS_DEFAULT = True

CONF_ACTIVE_MODE_OTHERS = "active_mode_others"
CONF_ACTIVE_MODE_OTHERS_DEFAULT = False

PLATFORMS = ["device_tracker", "sensor", "button"]

# STF URLs
STF_BASE = "https://smartthingsfind.samsung.com"
URL_GET_CSRF = f"{STF_BASE}/chkLogin.do"
URL_DEVICE_LIST = f"{STF_BASE}/device/getDeviceList.do"
URL_REQUEST_OPERATION = f"{STF_BASE}/dm/addOperation.do"
URL_SET_LAST_DEVICE = f"{STF_BASE}/device/setLastSelect.do"

# Battery mapping (원본 스타일 유지)
BATTERY_LEVELS = {
    "0": 0,
    "1": 5,
    "2": 10,
    "3": 15,
    "4": 20,
    "5": 25,
    "6": 30,
    "7": 35,
    "8": 40,
    "9": 45,
    "10": 50,
    "11": 55,
    "12": 60,
    "13": 65,
    "14": 70,
    "15": 75,
    "16": 80,
    "17": 85,
    "18": 90,
    "19": 95,
    "20": 100,
}

# SmartThings official integration domain (device merge 목적)
SMARTTHINGS_DOMAIN = "smartthings"

# Operations (일부는 기기/계정에 따라 동작 안 할 수 있음)
OP_RING = "RING"
OP_CHECK_CONNECTION_WITH_LOCATION = "CHECK_CONNECTION_WITH_LOCATION"

# 아래는 "사이트 버튼 느낌" 구현용 (실험/불확실)
OP_LOST_MODE = "LOST_MODE"
OP_TRACK_LOCATION = "TRACK_LOCATION"
OP_ERASE_DATA = "ERASE_DATA"
OP_EXTEND_BATTERY = "EXTEND_BATTERY_TIME"
