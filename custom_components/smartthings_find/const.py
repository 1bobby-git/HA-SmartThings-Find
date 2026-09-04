"""Constants for SmartThings Find integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "smartthings_find"

# ----------------------------
# SmartThings Find URLs
# ----------------------------
STF_BASE_URL: Final = "https://smartthingsfind.samsung.com/"
STF_CHK_LOGIN_PATH: Final = "chkLogin.do"
STF_DEVICE_LIST_PATH: Final = "device/getDeviceList.do"
STF_SET_LAST_DEVICE_PATH: Final = "device/setLastSelect.do"
STF_ADD_OPERATION_PATH: Final = "dm/addOperation.do"

# Samsung's native-app account flow is pinned to this callback. A desktop
# browser often exposes only the HTTPS completion page, which is not enough
# unless the encrypted callback fields are present in its query or fragment.
SAMSUNG_ACCOUNT_SIGN_IN_COMPLETE_URL: Final = (
    "https://account.samsung.com/accounts/ANDROIDSDK/signInComplete"
)
SAMSUNG_ACCOUNT_APP_REDIRECT_URI: Final = (
    "ms-app://s-1-15-2-4027708247-2189610-1983755848-2937435718-"
    "1578786913-2158692839-1974417358"
)

# ----------------------------
# Timing constants (seconds)
# ----------------------------
# Button refresh delays after operation
REFRESH_DELAY_IMMEDIATE: Final = 2
REFRESH_DELAY_SHORT: Final = 6

# Location polling delays for server sync
LOCATION_POLL_DELAYS: Final[tuple[int, ...]] = (15, 30, 45)

# ----------------------------
# Config / Options keys
# ----------------------------
CONF_AUTH_METHOD: Final = "auth_method"
AUTH_METHOD_ACCOUNT: Final = "samsung_account"
AUTH_METHOD_COOKIE: Final = "cookie"
DEFAULT_AUTH_METHOD: Final = AUTH_METHOD_COOKIE
CONF_REDIRECT_URI: Final = "redirect_uri"
CONF_COOKIE: Final = "cookie"

# Legacy keys (keep for backward compatibility)
CONF_COOKIE_INPUT: Final = "cookie_input"
CONF_JSESSIONID: Final = "jsessionid"

CONF_UPDATE_INTERVAL: Final = "update_interval"
CONF_UPDATE_INTERVAL_DEFAULT: Final = 120  # seconds

# A shorter interval helps idle expiry while the randomized scheduler avoids
# repeatedly hitting Samsung at an exact machine-like cadence.
CONF_KEEPALIVE_INTERVAL: Final = "keepalive_interval"
CONF_KEEPALIVE_INTERVAL_DEFAULT: Final = 180  # seconds (3 min)
KEEPALIVE_JITTER_RATIO: Final = 0.12

# A single STF "Logout" body can be transient. The auth manager first refreshes
# CSRF and then rebuilds the web session when Samsung Account auth is available.
AUTH_RETRY_DELAYS: Final[tuple[int, ...]] = (2, 5, 15)
AUTH_FAILURE_GRACE_PERIOD: Final = 30 * 60

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
DATA_AUTH_MANAGER: Final = "auth_manager"
DATA_COORDINATOR: Final = "coordinator"
DATA_DEVICES: Final = "devices"

# Legacy key retained for compatibility with older runtime data.
DATA_KEEPALIVE_UNSUB: Final = "keepalive_unsub"

# ----------------------------
# Battery mapping (server response string -> percent)
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
# SmartThings Find operation codes
# ----------------------------
OP_RING: Final = "RING"
OP_CHECK_CONNECTION: Final = "CHECK_CONNECTION"
OP_CHECK_CONNECTION_WITH_LOCATION: Final = "CHECK_CONNECTION_WITH_LOCATION"
