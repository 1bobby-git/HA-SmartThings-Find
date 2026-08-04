# HA-SmartThings-Find Stability and v1.2.0 Release Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` to execute this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

## Goal
- Prevent malformed `gpsUtcDt` values from turning a full location refresh result into unusable data.
- Keep auth and reauth behavior unchanged while improving parsing fault tolerance.
- Complete a safe v1.2.0 release path that is installable via HACS.

## Architecture
- Runtime path: Home Assistant config entry setup and polling through DataUpdateCoordinator in `custom_components/smartthings_find/coordinator.py`, then `utils.get_device_location`.
- Failure point: `custom_components/smartthings_find/utils.py` where malformed `gpsUtcDt` currently raises during location parsing.
- Surface impact: `sensor.py`, `device_tracker.py`, and button-triggered refresh flows consume coordinator payload keyed by `dvceID`.

## Tech Stack
- Python 3.12+
- aiohttp for SmartThings endpoints
- Home Assistant helper APIs (Coordinator, entities, config flow)
- Git + GitHub CLI (`gh`) for release governance

---

## Test path convention in this repository
- No existing test suite directory is present in the current tree.
- This plan uses a single normalized path for all new tests:
  - `tests/components/smartthings_find/`

## Task 1 - Red (2-5 minutes): write deterministic failing tests first
- Files: `tests/components/smartthings_find/test_utils.py`
- Estimated time: 2-5 minutes
- [ ] Add `gpsUtcDt` parser success/failure tests.
- [ ] Add one mixed valid+malformed payload test that should not pass yet in red state.
- [ ] Run tests and record expected failure before implementation.

```python
# tests/components/smartthings_find/test_utils.py (complete test code)
from __future__ import annotations

from datetime import datetime, timezone
import json

from custom_components.smartthings_find.utils import parse_stf_date


def test_parse_stf_date_valid():
    assert parse_stf_date("20260804010101") == datetime(
        2026, 8, 4, 1, 1, 1, tzinfo=timezone.utc
    )


def test_parse_stf_date_invalid_returns_none():
    assert parse_stf_date("2026-08-04") is None
    assert parse_stf_date("bad-format") is None
    assert parse_stf_date("") is None
    assert parse_stf_date("2026084") is None


def test_parse_stf_date_rejects_undefined_payload_field():
    payload = json.loads('{"operation":[{"oprnType":"LOCATION","extra":{"gpsUtcDt":"bad"}}]}')
    assert parse_stf_date(payload["operation"][0]["extra"].get("gpsUtcDt")) is None
```

```bash
cd C:\Users\bobby\Documents\Codex\2026-08-04\1bobby-git-ha-ytmusic-url-player\work\repos\HA-SmartThings-Find
pytest tests/components/smartthings_find/test_utils.py -q
```

Expected:
- FAIL (red): parse path is not fault-safe in current implementation and mixed payload support is incomplete.

## Task 2 - Green (2-5 minutes): harden parser and location extraction logic
- Files: `custom_components/smartthings_find/utils.py`
- Estimated time: 2-5 minutes
- [ ] Update `parse_stf_date` to return `None` for invalid dates.
- [ ] Update `get_device_location` to skip only malformed ops and continue processing valid ops.
- [ ] Preserve auth error path (`ConfigEntryAuthFailed`) unchanged for session expiry.
- [ ] Keep existing polling cadence and pending-state behavior unchanged.

```python
# custom_components/smartthings_find/utils.py (complete target functions)
from __future__ import annotations

from datetime import datetime, timezone


def parse_stf_date(datestr: str) -> datetime | None:
    """Parse STF gpsUtcDt safely. Return None for malformed values."""
    if not datestr:
        return None

    try:
        return datetime.strptime(datestr, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    except Exception:
        return None


# inside get_device_location(), location parsing loop section
used_loc = {"latitude": None, "longitude": None, "gps_accuracy": None, "gps_date": None}
used_op = None
for op in ops:
    if op.get("oprnType") in ("LOCATION", "LASTLOC", "OFFLINE_LOC"):
        extra = op.get("extra") or {}
        raw_gps = extra.get("gpsUtcDt") if isinstance(extra, dict) else None
        gps_utc = parse_stf_date(raw_gps or "")

        if gps_utc is None:
            _LOGGER.debug("Skipping malformed gpsUtcDt in operation for dev=%s op=%s value=%r", dev_name, op.get("oprnType"), raw_gps)
            continue

        if used_loc["gps_date"] and used_loc["gps_date"] >= gps_utc:
            continue

        if "latitude" in op:
            used_loc["latitude"] = float(op["latitude"])
        if "longitude" in op:
            used_loc["longitude"] = float(op["longitude"])

        used_loc["gps_accuracy"] = calc_gps_accuracy(op.get("horizontalUncertainty"), op.get("verticalUncertainty"))
        used_loc["gps_date"] = gps_utc
        used_op = op
        res["location_found"] = True
```

```bash
pytest tests/components/smartthings_find/test_utils.py -q
pytest tests/components/smartthings_find/test_get_device_location.py -q
```

Expected PASS:
- `parse_stf_date` returns `None` on malformed input.
- `get_device_location` continues across malformed ops and still fills valid `used_loc` when available.

## Task 3 - Refine (3-5 minutes): finalize state safety and logs
- Files: `custom_components/smartthings_find/sensor.py`, `custom_components/smartthings_find/utils.py`
- Estimated time: 3-5 minutes
- [ ] Confirm `SmartThingsFindLastUpdateSensor.native_value` already returns only `gps_date` and supports `None`.
- [ ] Add/keep explicit debug logs for skipped malformed payloads.
- [ ] Confirm `battery_level` extraction remains independent of `gpsUtcDt` issues.

```python
# custom_components/smartthings_find/sensor.py (complete target function)
@property
def native_value(self) -> datetime | None:
    res = self.coordinator.data.get(self._dvce_id) if self.coordinator.data else None
    if not res:
        return None
    loc = res.get("used_loc") or {}
    return loc.get("gps_date")


# custom_components/smartthings_find/utils.py (complete logging line in loop)
if gps_utc is None:
    _LOGGER.debug(
        "Skipping malformed gpsUtcDt in operation for dev=%s op=%s value=%r",
        dev_name,
        op.get("oprnType"),
        raw_gps,
    )
    continue
```

```bash
pytest tests/components/smartthings_find/test_utils.py -q
pytest tests/components/smartthings_find/test_sensors.py -q
```

Expected PASS:
- No crash in sensor state property when `gps_date` is missing.
- Debug log appears for malformed GPS timestamp operations.

## Task 4 - Release gate (3-5 minutes): verify tag absence, push main, tag, release, API checks
- Files: `custom_components/smartthings_find/manifest.json`, `.github/workflows/hacs.yaml`, `.github/workflows/hassfest.yaml`
- Estimated time: 3-5 minutes
- [ ] Verify branch and manifest version before tag creation.
- [ ] Check whether `v1.2.0` tag already exists; abort if exists.
- [ ] Push `main`, create annotated tag `v1.2.0`, push tag.
- [ ] Create release using GitHub API and verify result.

```bash
git rev-parse --abbrev-ref HEAD
git fetch --all --prune
git tag --list | findstr "v1.2.0"
python - <<"PY"
import json
with open("custom_components/smartthings_find/manifest.json", encoding="utf-8") as f:
    print(json.load(f).get("version"))
PY
```

Expected:
- FAIL if branch is not `main`.
- FAIL if command outputs malformed encoding/path error.
- PASS when branch is `main` and manifest output equals `1.2.0`.
- PASS when local tag list does not include `v1.2.0`.

```bash
git add custom_components/smartthings_find/manifest.json
git commit -m "Prepare v1.2.0 release metadata"
git push origin main
git tag -a v1.2.0 -m "Release: HA-SmartThings-Find v1.2.0 stability"
git push origin v1.2.0
gh release create v1.2.0 --repo 1bobby-git/HA-SmartThings-Find --title "v1.2.0" --generate-notes
gh release view v1.2.0 --repo 1bobby-git/HA-SmartThings-Find --json tagName,name,url,isDraft,isPrerelease
gh api repos/1bobby-git/HA-SmartThings-Find/releases/tags/v1.2.0 --jq ".tag_name"
gh api repos/1bobby-git/HA-SmartThings-Find/actions/workflows/hacs.yaml/runs?per_page=1
gh api repos/1bobby-git/HA-SmartThings-Find/actions/workflows/hassfest.yaml/runs?per_page=1
```

Expected:
- PASS when `gh release view` returns tag `v1.2.0`.
- PASS when API endpoints return matching release and workflow runs.

## Selective commit plan
- Commit 1: `custom_components/smartthings_find/utils.py` only.
- Commit 2: `custom_components/smartthings_find/sensor.py` only.
- Commit 3: tests under `tests/components/smartthings_find/` only.
- Commit 4: `custom_components/smartthings_find/manifest.json` plus release note file.

Lore message template:
```
Harden STF gpsUtcDt parsing without changing auth/reauth contract

Constraint: keep coordinator polling cadence and reauth flow unchanged
Rejected: full rewrite of get_device_location | rejected due to higher regression risk
Confidence: high
Scope-risk: moderate
Directive: keep release operations to manifest version, annotated tag, and one release body
Tested: planned pytest gates and gh release validation commands
Not-tested: long-running real-device validation on production SmartThings sessions
```

## Completion checks
- [ ] Malformed `gpsUtcDt` no longer makes one bad op invalidate full device location update.
- [ ] Tests in `tests/components/smartthings_find/` pass in CI-style run.
- [ ] `git tag --list` shows `v1.2.0` once created.
- [ ] `gh release view v1.2.0` succeeds.
- [ ] `gh api .../actions/workflows/hacs.yaml/runs` and `.../hassfest.yaml/runs` return accessible latest runs.
