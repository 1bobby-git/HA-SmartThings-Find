from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file_path = Path(path)
    content = file_path.read_text(encoding="utf-8")
    count = content.count(old)
    if count != 1:
        raise SystemExit(f"Expected one match in {path}, found {count}")
    file_path.write_text(content.replace(old, new, 1), encoding="utf-8")


replace_once(
    "custom_components/smartthings_find/session_store.py",
    '''    legacy_hash = _legacy_source_hash(entry)\n    stored_cookie = _stored_cookie_snapshot(\n        stored,\n        {source_hash, legacy_hash},\n    )\n''',
    '''    legacy_hash = _legacy_source_hash(entry)\n    accepted_hashes: set[str | None] = {source_hash}\n    if legacy_hash is not None:\n        accepted_hashes.add(legacy_hash)\n    stored_cookie = _stored_cookie_snapshot(\n        stored,\n        accepted_hashes,\n    )\n''',
)

AUTH_MANAGER = "custom_components/smartthings_find/auth_manager.py"
for old, new in (
    (
        '''        self._replace_cookies(cookie_line)\n        await self._fetch_csrf_locked(retry_delays=AUTH_RETRY_DELAYS)\n        await self._persist_best_effort_locked()\n''',
        '''        self._replace_cookies(cookie_line)\n        try:\n            await self._fetch_csrf_locked(retry_delays=AUTH_RETRY_DELAYS)\n        except ConfigEntryAuthFailed:\n            await self._persist_best_effort_locked()\n            raise\n        await self._persist_best_effort_locked()\n''',
    ),
    (
        '''                except ConfigEntryAuthFailed:\n                    self._clear_csrf()\n\n            if self._auth_method != AUTH_METHOD_ACCOUNT:\n''',
        '''                except ConfigEntryAuthFailed:\n                    await self._persist_best_effort_locked()\n                    self._clear_csrf()\n\n            if self._auth_method != AUTH_METHOD_ACCOUNT:\n''',
    ),
    (
        '''        except ConfigEntryAuthFailed:\n            self._clear_csrf()\n\n        await self._rebuild_session_locked()\n        return await operation()\n''',
        '''        except ConfigEntryAuthFailed:\n            await self._persist_best_effort_locked()\n            self._clear_csrf()\n\n        await self._rebuild_session_locked()\n        return await operation()\n''',
    ),
    (
        '''            except ConfigEntryAuthFailed:\n                result = await self._repair_csrf_and_retry_locked(operation)\n''',
        '''            except ConfigEntryAuthFailed:\n                await self._persist_best_effort_locked()\n                result = await self._repair_csrf_and_retry_locked(operation)\n''',
    ),
    (
        '''            except ConfigEntryAuthFailed:\n                await self._rebuild_session_locked()\n\n            try:\n                result = await get_device_location(\n''',
        '''            except ConfigEntryAuthFailed:\n                await self._persist_best_effort_locked()\n                await self._rebuild_session_locked()\n\n            try:\n                result = await get_device_location(\n''',
    ),
    (
        '''            except ConfigEntryAuthFailed as err:\n                # Active mode sends CHECK_CONNECTION_WITH_LOCATION before the\n''',
        '''            except ConfigEntryAuthFailed as err:\n                await self._persist_best_effort_locked()\n                # Active mode sends CHECK_CONNECTION_WITH_LOCATION before the\n''',
    ),
    (
        '''            except ConfigEntryAuthFailed:\n                await self._rebuild_session_locked()\n\n            try:\n                await send_operation(\n''',
        '''            except ConfigEntryAuthFailed:\n                await self._persist_best_effort_locked()\n                await self._rebuild_session_locked()\n\n            try:\n                await send_operation(\n''',
    ),
    (
        '''            except ConfigEntryAuthFailed as err:\n                # The server may have accepted an effect before returning an\n''',
        '''            except ConfigEntryAuthFailed as err:\n                await self._persist_best_effort_locked()\n                # The server may have accepted an effect before returning an\n''',
    ),
):
    replace_once(AUTH_MANAGER, old, new)

replace_once(
    "tests/test_session_persistence_v140.py",
    '''    async def test_v13_cookie_snapshot_is_loaded_and_migrated_in_place(self) -> None:\n''',
    '''    async def test_account_mode_rejects_an_unbound_legacy_snapshot(self) -> None:\n        hass = types.SimpleNamespace(data={})\n        entry = _Entry("", session_store.AUTH_METHOD_ACCOUNT)\n        key = f"{session_store.DOMAIN}.{entry.entry_id}.session"\n        _MemoryStore.values[key] = {\n            "source_hash": None,\n            "cookie": "JSESSIONID=foreign-session",\n        }\n\n        loaded = await session_store.async_load_cookie_line(hass, entry)\n\n        self.assertEqual("", loaded)\n\n    async def test_v13_cookie_snapshot_is_loaded_and_migrated_in_place(self) -> None:\n''',
)

AUTH_TEST = "tests/test_auth_manager.py"
replace_once(
    AUTH_TEST,
    '''    async def persist_cookie_to_store(*_args, **_kwargs):\n        return None\n''',
    '''    persisted_cookie_snapshots = []\n\n    async def persist_cookie_to_store(_hass, _entry, session):\n        persisted_cookie_snapshots.append(dict(session.cookie_jar.values))\n''',
)
replace_once(
    AUTH_TEST,
    '''    module._TestAccountAuth = SamsungAccountAuth\n    return module\n''',
    '''    module._TestAccountAuth = SamsungAccountAuth\n    module._persisted_cookie_snapshots = persisted_cookie_snapshots\n    return module\n''',
)
replace_once(
    AUTH_TEST,
    '''    async def test_account_mode_rebuilds_web_session_after_csrf_repair_fails(self) -> None:\n''',
    '''    async def test_auth_failure_persists_server_cookie_deletion_before_repair(self) -> None:\n        hass = _Hass()\n        session = _Session()\n        session.cookie_jar.values["JSESSIONID"] = "old-session"\n        manager = auth_manager.SmartThingsFindAuthManager(\n            hass,\n            _Entry(auth_manager.AUTH_METHOD_COOKIE),\n            session,\n        )\n        auth_manager._persisted_cookie_snapshots.clear()\n\n        async def fetch_csrf(*_args, **_kwargs):\n            raise auth_manager.ConfigEntryAuthFailed("expired")\n\n        auth_manager.fetch_csrf = fetch_csrf\n\n        async def operation():\n            session.cookie_jar.clear()\n            raise auth_manager.ConfigEntryAuthFailed("server deleted cookie")\n\n        with self.assertRaises(auth_manager.ConfigEntryAuthFailed):\n            await manager.async_read(operation)\n\n        self.assertGreaterEqual(len(auth_manager._persisted_cookie_snapshots), 1)\n        self.assertEqual({}, auth_manager._persisted_cookie_snapshots[0])\n\n    async def test_account_mode_rebuilds_web_session_after_csrf_repair_fails(self) -> None:\n''',
)
