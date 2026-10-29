# SmartThings Find 1.4.6: 세션 유지·복구 결함 수정 설계

- 작성일: 2026-10-29
- 기준: `main` 1.4.5 (`24fb651`)
- 범위: 사용자가 승인한 A안. 실측 없이 코드와 aiohttp 동작으로 확인한 결함만 고쳐 1.4.6으로 발행하고, smartthingsfind.samsung.com 세션 실측(REA)은 별도로 진행해 후속 버전에 반영합니다.
- 관련 이슈: #6(약 80분 뒤 인증 실패), #9(매일 쿠키 재입력), #7(워치 배터리, 이번 범위 아님)

## 1. 목표와 제약

목표: 쿠키 한 번 입력으로 서버가 세션을 유지하는 동안은 계속 동작하고, 일시적 장애에서 스스로 회복하며, 문제가 생기면 사용자가 원인을 공유할 수 있게 합니다.

제약: 기존 동작을 깨지 않습니다. 설정·재인증·옵션 화면, 엔티티와 unique_id, 기본 옵션(폴링 120초, KeepAlive 180초, 모드 기본값), 물리 명령(링·위치 갱신)을 재전송하지 않는 정책, 레거시 Samsung Account 항목 호환을 유지합니다.

## 2. 확인된 결함

| # | 결함 | 근거 |
|---|---|---|
| F1 | 30분 유예 뒤 `ConfigEntryAuthFailed`가 나면 HA 코디네이터가 다음 폴링을 예약하지 않습니다(HA `update_coordinator.py`: `auth_failed`이면 `_schedule_refresh()` 생략). 이후 KeepAlive가 성공해도 폴링이 다시 시작되지 않아 엔티티가 계속 unavailable입니다. | `coordinator.py` `_async_keepalive`, HA 소스 |
| F2 | 재인증·옵션 흐름이 런타임 dict에 `_auth_reconfiguring=True`를 넣고, 이 값은 언로드 때만 지워집니다. 항목이 로드되지 않은 상태(설정 재시도·오류)에서 재인증하면 언로드가 호출되지 않아 플래그가 남고, 새로 설정된 항목은 회전된 쿠키를 계속 저장하지 않습니다. 재시작하면 처음 붙여 넣은 오래된 쿠키로 돌아가 다시 인증이 필요해집니다(#9와 일치). | `config_flow.py` `_finish`·옵션 흐름, `auth_manager.py` `_persist_best_effort_locked`, `__init__.py` |
| F3 | KeepAlive는 CSRF가 캐시되어 있으면 `chkLogin.do`를 호출하지 않습니다. README는 KeepAlive가 `chkLogin.do`로 로그인 상태를 확인한다고 안내합니다. 또 기기 목록 응답이 200 HTML이어도 성공으로 처리합니다. passive 모드에서는 설정 이후 `chkLogin.do`가 다시 호출되지 않습니다(#6 보고 환경과 일치). | `utils.py` `keepalive_ping`, README |
| F4 | 붙여 넣은 쿠키를 dict로 jar에 넣으면 `/`, `+`, `=` 등이 든 값을 aiohttp가 큰따옴표로 감싸 보냅니다. 브라우저는 그대로 보냅니다. aiohttp 3.11.18·3.14.5에서 재현했습니다. | `utils.py` `apply_cookies_to_session`, `make_session` |
| F5 | 붙여 넣은 쿠키는 모두 `smartthingsfind.samsung.com` 전용(host-only)으로 들어갑니다. 서버가 같은 이름을 `Domain=.samsung.com`으로 회전하면 jar에 둘 다 남고 aiohttp는 오래된 host-only 값을 보냅니다(두 버전에서 재현). 저장도 `name=value`만 남겨 상위 도메인·하위 경로 쿠키의 속성이 사라집니다. | `session_store.py` `_cookie_line_from_session` |
| F6 | 오류 로그에 명령 payload(`usrId`, `dvceId`)와 응답 본문 일부가 남고, 위치 파싱 예외 메시지에 좌표 문자열이 들어갈 수 있습니다. | `utils.py` `send_operation`, `get_device_location`, `keepalive_ping` |
| F7 | 문제를 보고할 때 공유할 진단 정보가 없습니다(#6에서 요청). | 진단 플랫폼 없음 |

## 3. 설계

### 3.1 폴링 재개 (F1)

`_async_keepalive`가 성공했을 때 코디네이터가 인증 실패로 멈춘 상태(`last_update_success`가 False이고 `last_exception`이 `ConfigEntryAuthFailed`)이면 `async_refresh()`를 한 번 호출합니다. 새로 고침이 성공하면 HA가 폴링 예약을 다시 시작합니다. 진행 중인 재인증 흐름은 건드리지 않습니다.

### 3.2 재인증 플래그 정리 (F2)

`SmartThingsFindAuthManager`를 만들 때(= 항목 설정을 시작할 때) `_auth_reconfiguring`을 지웁니다. 설정은 항상 이미 저장된 새 자격 증명으로 시작하므로 이 시점에는 차단이 필요 없습니다. 흐름 도중의 경합은 기존 `source_hash` 경계(설정된 쿠키가 바뀌면 이전 스냅샷을 무시)가 계속 막습니다.

### 3.3 KeepAlive 로그인 확인 (F3)

`keepalive_ping`은 매번 `chkLogin.do`로 로그인 상태를 확인하고 새 CSRF를 받은 뒤 기기 목록을 요청합니다.

- 본문 `Logout`/`fail`, HTTP 401/403: 인증 실패(기존과 같음)
- HTTP 200이지만 `deviceList`가 있는 JSON이 아님: 인증 실패가 아닌 일시 오류로 처리하고 성공으로 세지 않음
- 그 밖의 HTTP 오류: 일시 오류

재시도·세션 복구는 기존 `async_read` 경로를 그대로 씁니다.

### 3.4 쿠키를 브라우저처럼 보내기 (F4, F5)

- 세션 jar를 `CookieJar(unsafe=True, quote_cookie=False)`로 만들고, 붙여 넣은 쿠키는 원문 값을 그대로 가진 Morsel로 넣습니다(두 aiohttp 버전 모두 원문 전송 확인).
- 같은 이름의 쿠키가 host-only(`smartthingsfind.samsung.com`, 경로 `/`)와 상위 도메인(경로 `/`)에 함께 있으면 host-only 쪽을 jar에서 지웁니다. 상위 도메인 쿠키는 서버 응답으로만 생기므로 더 최신입니다. 응답을 받을 때마다(저장 직전) 정리합니다.
- 저장 형식에 `scoped` 목록을 추가해 상위 도메인 또는 `/`가 아닌 경로의 쿠키를 `name`·`value`·`domain`·`path`와 함께 저장하고 복원합니다. 일반 host-only `/` 쿠키는 지금처럼 `cookie` 문자열에 둡니다. 이런 쿠키가 없으면 저장 내용은 1.4.5와 같습니다. 1.4.5로 되돌려도 `cookie` 문자열은 그대로 읽힙니다.

### 3.5 로그 정리 (F6)

명령 실패 로그는 명령 종류와 HTTP 상태만 남깁니다. 응답 본문은 길이만 남깁니다. 위치 처리 예외는 예외 종류만 오류 로그에 남기고 상세는 debug 로그로 내립니다.

### 3.6 진단 (F7)

`diagnostics.py`를 추가합니다. 포함: 인증 방식, 옵션(주기·모드), 쿠키 이름 목록과 개수(값 없음), 쿠키별 도메인·경로, CSRF 보유 여부, 마지막 `chkLogin.do` 결과(HTTP 상태, `Logout`/`fail` 여부, 리디렉트 여부와 최종 호스트, 시각), 연속 인증 실패 시작 시각과 경과 시간, 마지막 KeepAlive 성공·실패 시각과 오류 종류, 시작할 때 쓴 쿠키 출처(저장된 세션 또는 설정한 쿠키)와 저장소 오류 종류, 마지막 쿠키 저장 시각·개수와 저장 오류 종류, 코디네이터 상태(다음 폴링 예약 여부 포함), 기기 유형별 개수. 통합 버전은 HA가 진단 파일에 manifest를 함께 넣으므로 따로 넣지 않습니다. 제외: 쿠키 값, CSRF 값, `usrId`, `dvceID`, 좌표, 기기 이름.

### 3.7 정리

`utils.py`에서 어디서도 쓰지 않는 예전 함수(`persist_cookie_to_entry`, 이전 `get_devices`, SmartThings 식별자 차용 함수들, `ring_device`)를 지웁니다. `persist_cookie_to_entry`는 다시 쓰면 설정 항목을 계속 갱신해 재로드 반복을 일으킬 수 있습니다.

## 4. 바꾸지 않는 것

기본 옵션 값(SmartTag active 기본값 포함), 30분 유예와 재시도 간격, 물리 명령 비재전송 정책, 엔티티·unique_id, 설정 화면, 레거시 Samsung Account 런타임, `samsung-re-find` 의존성.

## 5. 테스트

기존 `unittest` + HA 스텁 방식을 따릅니다. aiohttp 동작은 실제 aiohttp로 검증하고, CI에 aiohttp 설치를 추가합니다.

- F1: 인증 실패로 멈춘 코디네이터에서 KeepAlive 성공 시 새로 고침 1회, 정상 상태에서는 추가 새로 고침 없음
- F2: 플래그가 남은 런타임으로 매니저를 만들면 플래그가 지워지고 저장이 동작
- F3: KeepAlive가 매번 `chkLogin.do`를 호출, HTML 200은 성공으로 세지 않음, `Logout`은 인증 실패, 로그에 본문 없음
- F4·F5(실제 aiohttp): 원문 값 전송, host-only 중복 제거 후 상위 도메인 값 전송, `scoped` 저장·복원 후 회전 시 교체, 1.4.5 형식 스냅샷 호환
- F6: 명령 실패 로그에 `usrId`·payload·본문 없음
- F7: 진단에 쿠키 값·CSRF·`usrId`·좌표 없음, 쿠키 이름 포함

실행: Linux 컨테이너(python 3.12)에서 `python -m compileall`, `python -m unittest discover -s tests`(aiohttp 3.11.18과 최신 각각). GitHub에서 Unit tests·HACS Validate·hassfest 통과 확인.

## 6. 배포

작업 브랜치 `fix/1.4.6-session-recovery`를 push해 CI를 확인한 뒤 `main`에 병합·push합니다. `main`의 Unit tests가 성공하면 Release 워크플로가 HACS Validate를 기다려 `v1.4.6` Release를 자동 생성합니다. 이슈 #6·#9에 변경 내용과 진단 다운로드 방법을 남깁니다(닫지 않음: 실측 결과 반영 전).

## 7. 위험과 대응

- KeepAlive가 `chkLogin.do`를 매번 호출: active 모드는 이미 기기마다 폴링 때 호출하고 있어 서버 부담 증가는 passive 모드의 3분당 1회 수준입니다.
- HTML 200을 성공으로 세지 않음: 인증 실패로 보지 않으므로 잘못된 재인증 요청은 늘지 않습니다.
- host-only 중복 제거: 상위 도메인 쿠키가 서버 응답으로 생긴 경우에만 동작합니다.
