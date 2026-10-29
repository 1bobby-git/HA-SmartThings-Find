# SmartThings Find 1.4.6 세션 유지·복구 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `docs/superpowers/specs/2026-10-29-session-recovery-1.4.6-design.md`의 F1–F7을 기존 동작을 깨지 않고 구현해 1.4.6으로 발행한다.

**Architecture:** 인증·세션은 `auth_manager.py`(잠금·복구), `utils.py`(HTTP 호출·쿠키 jar), `session_store.py`(비공개 저장), `coordinator.py`(폴링·KeepAlive)로 나뉜다. 각 결함을 해당 파일 안에서 고치고, 새 진단 플랫폼 `diagnostics.py`는 런타임에 기록된 값만 읽는다.

**Tech Stack:** Python 3.12, aiohttp(3.11.18·최신), Home Assistant 스텁 기반 `unittest`(저장소 기존 방식), Linux 컨테이너.

**기준선(수정 전, Linux):** `python -m unittest discover -s tests` 44개 통과(aiohttp 없음/있음 모두).

---

## 파일 구조

| 파일 | 변경 |
|---|---|
| `custom_components/smartthings_find/utils.py` | 원문 쿠키 seed, jar 정규화, 상위 도메인·경로 쿠키 적용, KeepAlive 로그인 확인, chkLogin 진단 기록, 로그 정리, 죽은 코드 삭제 |
| `custom_components/smartthings_find/session_store.py` | `scoped` 저장·복원, `async_load_session_cookies` |
| `custom_components/smartthings_find/auth_manager.py` | 생성 시 `_auth_reconfiguring` 정리, scoped 복원, 응답 후 jar 정규화 |
| `custom_components/smartthings_find/coordinator.py` | KeepAlive 성공 시 폴링 재개, KeepAlive 진단 기록 |
| `custom_components/smartthings_find/diagnostics.py` | 신규 |
| `custom_components/smartthings_find/const.py` | 진단 런타임 키(`DIAG_*`) |
| `custom_components/smartthings_find/manifest.json`, `CHANGELOG.md`, `README.md` | 1.4.6 |
| `.github/workflows/unit-tests.yaml` | aiohttp 설치 |
| `tests/test_v146_cookie_jar.py`, `tests/test_v146_recovery.py`, `tests/test_diagnostics.py` | 신규 |
| `tests/test_auth_manager.py`, `tests/test_authentication_surface.py` | 스텁·버전 갱신 |

---

### Task 1: 쿠키를 원문 그대로 보내고 중복을 정리 (F4, F5 일부)

**Files:** Modify `utils.py`; Create `tests/test_v146_cookie_jar.py`

- [ ] **Step 1: 실패 테스트** — 실제 aiohttp(`unittest.skipUnless(aiohttp 설치)`)로:
  - `make_session` + `apply_cookies_to_session({"AWSALB": "abc/def+ghi=="})` 후 로컬 aiohttp 서버가 받은 Cookie 헤더가 `AWSALB=abc/def+ghi==`
  - host-only `X=seed` 뒤 `Set-Cookie: X=fresh; Domain=.samsung.com; Path=/` 적용 → `normalize_cookie_jar` → `filter_cookies(STF_BASE)["X"].value == "fresh"`, host-only 항목 없음
  - 같은 이름이 없으면 `normalize_cookie_jar`가 아무것도 지우지 않음
- [ ] **Step 2:** `python -m unittest tests.test_v146_cookie_jar` → FAIL(따옴표 전송, `normalize_cookie_jar` 없음)
- [ ] **Step 3: 구현**

```python
STF_HOST = STF_BASE.host or "smartthingsfind.samsung.com"


def _raw_morsel(name: str, value: str) -> Morsel | str:
    """Morsel that sends the value exactly as the browser did (no quoting)."""
    morsel: Morsel = Morsel()
    try:
        morsel.set(name, value, value)
    except CookieError:
        return value
    return morsel


def cookie_scope(morsel: Any) -> tuple[str, str]:
    domain = str(morsel.get("domain") or "").lstrip(".").lower() or STF_HOST
    path = str(morsel.get("path") or "/") or "/"
    return domain, path


def is_stf_cookie_domain(domain: str) -> bool:
    return domain == STF_HOST or STF_HOST.endswith("." + domain)


def normalize_cookie_jar(session) -> int:
    """Drop host-only seeds shadowed by a server-set parent-domain cookie."""
```

`make_session`은 `aiohttp.CookieJar(unsafe=True, quote_cookie=False)`, `apply_cookies_to_session`은 `{name: _raw_morsel(name, value)}`로 넣는다.

- [ ] **Step 4:** 테스트 PASS, 기존 44개 PASS
- [ ] **Step 5: Commit** `fix: send pasted cookies verbatim and drop shadowed host-only copies`

### Task 2: 상위 도메인·경로 쿠키 저장과 복원 (F5)

**Files:** Modify `utils.py`(`apply_scoped_cookies`), `session_store.py`, `auth_manager.py`, `tests/test_auth_manager.py`(스텁); Test `tests/test_v146_cookie_jar.py`

- [ ] **Step 1: 실패 테스트**
  - 실제 jar에 host-only `/`, 상위 도메인 `/`, host-only `/device` 쿠키를 만든 뒤 `persist_cookie_to_store` → 저장 payload `cookie`에 루트 쿠키(상위 도메인 포함), `scoped`에 상위 도메인·`/device` 쿠키
  - 새 jar에 `async_load_session_cookies` 결과를 `_replace_cookies`와 같은 순서로 적용 → (domain, path, name, value) 집합이 원래와 같고, 이후 `Domain=.samsung.com` 회전 값이 전송됨
  - 1.4.5 형식 payload(`scoped` 없음) → `scoped == []`, 줄 그대로
  - 비정상 scoped 항목(다른 도메인, 경로 없음, `;` 포함 값, 51번째 이후)은 무시
- [ ] **Step 2:** FAIL 확인
- [ ] **Step 3: 구현** — `session_store._scoped_cookies_from_session`, payload에 비어 있지 않을 때만 `scoped`, `async_load_session_cookies(hass, entry) -> tuple[str, list[dict[str, str]]]`(기존 `async_load_cookie_line`은 이를 감싼다). `utils.apply_scoped_cookies(session, records)`는 기록마다 domain(상위 도메인일 때만)·path를 지정한 원문 Morsel로 `update_cookies`. `auth_manager._replace_cookies(line, scoped=())`가 줄 → scoped → `normalize_cookie_jar` 순으로 적용, `async_initialize`·`_restore_cookie_snapshot_locked`가 scoped를 함께 사용.
- [ ] **Step 4:** PASS
- [ ] **Step 5: Commit** `fix: persist parent-domain and path-scoped SmartThings Find cookies`

### Task 3: 재인증 플래그 정리와 응답 후 정규화 (F2)

**Files:** Modify `auth_manager.py`; Test `tests/test_auth_manager.py`

- [ ] **Step 1: 실패 테스트** — 런타임에 `_auth_reconfiguring=True`를 넣고 매니저 생성 → 플래그 없음, `async_read` 뒤 저장 스냅샷 1건 이상. 정규화 스텁 호출이 저장 전에 일어나고 플래그가 있어도 호출됨.
- [ ] **Step 2:** FAIL
- [ ] **Step 3: 구현** — `__init__` 끝에 `self._entry_runtime().pop("_auth_reconfiguring", None)`; `_persist_best_effort_locked` 시작에서 `normalize_cookie_jar(self.session)`(예외는 debug 로그) 후 기존 로직.
- [ ] **Step 4:** PASS
- [ ] **Step 5: Commit** `fix: keep persisting rotated cookies after reauth from a failed setup`

### Task 4: KeepAlive 로그인 확인과 진단 기록 (F3, F7 일부)

**Files:** Modify `utils.py`; Test `tests/test_v146_recovery.py`

- [ ] **Step 1: 실패 테스트**(가짜 세션)
  - CSRF가 캐시되어 있어도 `keepalive_ping`이 `chkLogin.do` GET 후 기기 목록 POST
  - POST가 200 HTML → `HomeAssistantError`(인증 실패 아님), 로그에 본문 없음
  - POST `Logout` → `ConfigEntryAuthFailed`
  - 정상 JSON → 예외 없음, 진단 `_diag_chk_login`에 status·body 분류·리디렉트 수·최종 호스트·시각, 본문·CSRF 값 없음
- [ ] **Step 2:** FAIL
- [ ] **Step 3: 구현** — `keepalive_ping`이 `fetch_csrf(..., retry_delays=())`로 매번 CSRF를 받고, 응답 분류 후 `deviceList` JSON만 성공. `_fetch_csrf_once`가 `record_chk_login(...)` 호출.
- [ ] **Step 4:** PASS
- [ ] **Step 5: Commit** `fix: confirm the login on every SmartThings Find keepalive`

### Task 5: 폴링 재개 (F1)

**Files:** Modify `coordinator.py`; Test `tests/test_v146_recovery.py`

- [ ] **Step 1: 실패 테스트**(코디네이터 베이스 스텁) — `last_update_success=False`, `last_exception=ConfigEntryAuthFailed()`에서 `_async_keepalive` 성공 → `async_refresh` 1회. 정상 상태 → 0회. KeepAlive 실패 → 0회, 진단 `last_failure`·`last_error` 기록.
- [ ] **Step 2:** FAIL
- [ ] **Step 3: 구현** — 성공 시 `_polling_stopped_by_auth_failure()`이면 info 로그 후 `await self.async_refresh()`; 성공·실패 시각과 오류 종류를 런타임 `_diag_keepalive`에 기록.
- [ ] **Step 4:** PASS
- [ ] **Step 5: Commit** `fix: resume polling when the SmartThings Find session recovers`

### Task 6: 로그 정리와 죽은 코드 삭제 (F6, 3.7)

**Files:** Modify `utils.py`; Test `tests/test_v146_recovery.py`

- [ ] **Step 1: 실패 테스트** — `send_operation` 403 실패 로그에 `usrId` 값·payload·본문이 없음. `get_device_location` 비정상 응답 로그에 본문 없음, 좌표 파싱 예외 오류 로그에 좌표 문자열 없음.
- [ ] **Step 2:** FAIL
- [ ] **Step 3: 구현** — 상태·길이·예외 종류만 기록, 상세 traceback은 debug. `persist_cookie_to_entry`, `utils.get_devices`, SmartThings 식별자 차용 함수, `ring_device`와 쓰지 않게 된 import 삭제.
- [ ] **Step 4:** PASS, `python -m compileall`
- [ ] **Step 5: Commit** `fix: keep identifiers and response bodies out of SmartThings Find logs`

### Task 7: 진단 플랫폼 (F7)

**Files:** Create `diagnostics.py`, `tests/test_diagnostics.py`

- [ ] **Step 1: 실패 테스트** — 런타임(실제 aiohttp jar, `_csrf`, 진단 기록, 기기 2개, 코디네이터 상태)으로 결과 생성 → 쿠키 이름·개수·도메인·경로 포함, 쿠키 값·CSRF·`usrId`·`dvceID`·좌표·기기 이름 없음. 런타임이 비어 있어도 동작하고 런타임을 만들지 않음. 쿠키 저장소가 시작 시 쿠키 출처·저장 시각·개수·저장소 오류를 기록(`session_store`), 저장 실패 종류를 기록(`auth_manager`).
- [ ] **Step 2:** FAIL
- [ ] **Step 3: 구현** — `async_get_config_entry_diagnostics(hass, entry)`, 진단 키는 `const.py`의 `DIAG_*`
- [ ] **Step 4:** PASS
- [ ] **Step 5: Commit** `feat: add redacted SmartThings Find diagnostics`

### Task 8: 버전·문서·CI

- [ ] manifest `1.4.6`, `test_release_version_is_1_4_6`, CHANGELOG `## 1.4.6 — 2026-10-29`, README(세션 보호·KeepAlive·진단 다운로드·문제 해결), `unit-tests.yaml`에 `python -m pip install aiohttp`
- [ ] Commit `chore: release SmartThings Find 1.4.6`

### Task 9: 검증과 발행

- [ ] Linux: aiohttp 없음 / 3.11.18 / 최신에서 `compileall`과 `unittest discover` 통과
- [ ] `semantic_reviewer` 리뷰 반영
- [ ] 브랜치 push → GitHub Unit tests·HACS Validate·hassfest 성공 확인
- [ ] `main` 병합·push → Release `v1.4.6` 생성과 첨부 파일 확인
- [ ] #6·#9에 변경 요약과 진단 다운로드 안내 댓글(닫지 않음)

### Task 10: 리뷰 반영 (설계 8절)

- [ ] 실패 테스트: 폴링 재개 요청 예외·루프 반복 실패에도 KeepAlive 유지, 흐름 도중 바뀐 Cookie로 이전 jar 저장 안 함, 서버가 회전한 `AWSALB`·따옴표 값과 붙여 넣은 따옴표 값의 원문 전송과 재시작 후 유지, 서버가 설정한 host-only 쿠키 유지, `chkLogin.do` 직후 정규화, KeepAlive 대체 판단 6가지, 인증 외 실패 후 저장, `com` 범위 제외, 진단 폴링 필드와 설정 오류 요약
- [ ] 구현: `coordinator.py`, `auth_manager.py`, `session_store.py`, `utils.py`, `diagnostics.py`, `__init__.py`
- [ ] CI 매트릭스(Python 3.12 + aiohttp 3.11.18, Python 3.13 + 최신 aiohttp), CHANGELOG·README 문구
- [ ] Linux: aiohttp 없음 / 3.11.18 / 최신(3.12), 최신(3.13)에서 전체 통과, 새 테스트가 리뷰 전 코드에서 실패
- [ ] Commit `fix: address review of the SmartThings Find 1.4.6 session fixes`

### Task 11: 2차 리뷰 반영 (설계 8.1절)

- [ ] 실패 테스트: 재시작 뒤 서버 host-only 쌍 유지와 상위 도메인 회전 우선, 대체 판단 경계(점검 페이지·404는 일시 오류, 로그인 리디렉트는 인증 실패, 403 + 정상 기기 목록은 성공), 캐시 재사용 시 쿠키 출처, 실제 auth manager·`async_read`·코디네이터를 거친 KeepAlive 3가지
- [ ] 구현: `seed_session_cookies`, `scoped` host-only 쌍 기록, `_chk_login_signals_expiry`, 설정 실패 경고, 캐시 출처 기록, 루프 경고, CI 3조합
- [ ] Linux 전체 통과, 새 테스트가 1차 반영 코드(73d57d8)에서 실패
- [ ] Commit `fix: keep server-set cookies across restarts and classify ambiguous KeepAlive pages as temporary`
