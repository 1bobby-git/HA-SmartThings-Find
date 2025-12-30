from __future__ import annotations

DOMAIN = "smartthings_find"

# Upstream compatibility key name.
# This fork stores FULL browser Cookie header string here.
CONF_JSESSIONID = "jsessionid"

CONF_UPDATE_INTERVAL = "update_interval"
CONF_UPDATE_INTERVAL_DEFAULT = 60  # seconds

CONF_ACTIVE_MODE_SMARTTAGS = "active_mode_smarttags"
CONF_ACTIVE_MODE_SMARTTAGS_DEFAULT = True

CONF_ACTIVE_MODE_OTHERS = "active_mode_others"
CONF_ACTIVE_MODE_OTHERS_DEFAULT = False

BATTERY_LEVELS = {
    "CRITICAL": 5,
    "LOW": 20,
    "MEDIUM": 50,
    "HIGH": 80,
    "FULL": 100,
}
