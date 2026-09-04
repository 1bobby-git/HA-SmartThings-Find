from __future__ import annotations

import json
from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file_path = Path(path)
    content = file_path.read_text(encoding="utf-8")
    count = content.count(old)
    if count != 1:
        raise SystemExit(f"Expected one match in {path}, found {count}")
    file_path.write_text(content.replace(old, new, 1), encoding="utf-8")


for path in (
    "custom_components/smartthings_find/strings.json",
    "custom_components/smartthings_find/translations/ko.json",
    "custom_components/smartthings_find/translations/en.json",
    "custom_components/smartthings_find/translations/de.json",
):
    file_path = Path(path)
    data = json.loads(file_path.read_text(encoding="utf-8"))
    description = data["config"]["step"]["cookie"]["description"]
    if "https://smartthingsfind.samsung.com" not in description:
        raise SystemExit(f"Cookie URL was not found in {path}")
    data["config"]["step"]["cookie"]["description"] = description.replace(
        "https://smartthingsfind.samsung.com",
        "{stf_url}",
    )
    file_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

replace_once(
    "custom_components/smartthings_find/config_flow.py",
    '''    CONF_UPDATE_INTERVAL_DEFAULT,\n    DOMAIN,\n)\n''',
    '''    CONF_UPDATE_INTERVAL_DEFAULT,\n    DOMAIN,\n    STF_BASE_URL,\n)\n''',
)
replace_once(
    "custom_components/smartthings_find/config_flow.py",
    '''        return self.async_show_form(\n            step_id="cookie",\n            data_schema=vol.Schema(schema_fields),\n            errors=errors,\n        )\n''',
    '''        return self.async_show_form(\n            step_id="cookie",\n            data_schema=vol.Schema(schema_fields),\n            description_placeholders={\n                "stf_url": STF_BASE_URL.rstrip("/"),\n            },\n            errors=errors,\n        )\n''',
)

replace_once(
    "custom_components/smartthings_find/__init__.py",
    '''from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady\n''',
    '''from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady\nfrom homeassistant.helpers import config_validation as cv\n''',
)
replace_once(
    "custom_components/smartthings_find/__init__.py",
    '''PLATFORMS = [Platform.DEVICE_TRACKER, Platform.SENSOR, Platform.BUTTON]\n''',
    '''CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)\n\nPLATFORMS = [Platform.DEVICE_TRACKER, Platform.SENSOR, Platform.BUTTON]\n''',
)
