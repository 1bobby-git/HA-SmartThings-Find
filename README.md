<!-- project-branding:start -->
<p align="center">
  <img src="custom_components/smartthings_find/brand/logo@2x.png" alt="SmartThings Find 로고" width="520">
</p>
<p align="center">
  <a href="https://github.com/1bobby-git/HA-SmartThings-Find/stargazers"><img src="https://img.shields.io/github/stars/1bobby-git/HA-SmartThings-Find?style=flat-square&logo=github&label=Stars" alt="GitHub Stars"></a>
  <a href="https://github.com/1bobby-git/HA-SmartThings-Find/releases"><img src="https://img.shields.io/github/v/release/1bobby-git/HA-SmartThings-Find?style=flat-square&label=Release" alt="Latest Release"></a>
  <a href="https://github.com/1bobby-git/HA-SmartThings-Find/blob/main/custom_components/smartthings_find/manifest.json"><img src="https://img.shields.io/badge/Architecture-independent-0ea5e9?style=flat-square" alt="Architecture independent"></a>
  <a href="https://github.com/1bobby-git/HA-SmartThings-Find/blob/main/LICENSE"><img src="https://img.shields.io/github/license/1bobby-git/HA-SmartThings-Find?style=flat-square&label=License" alt="License"></a>
  <a href="https://github.com/1bobby-git/HA-SmartThings-Find/commits/main"><img src="https://img.shields.io/github/last-commit/1bobby-git/HA-SmartThings-Find?style=flat-square&label=Updated" alt="Last Commit"></a>
</p>
<!-- project-branding:end -->

# SmartThings Find

> **Compatibility**: Home Assistant **2024.12+** 권장

Samsung **SmartThings Find**의 비공식 웹 기능을 Home Assistant에서 사용하는 커스텀 통합입니다. 기기 위치, 배터리, 마지막 업데이트, 벨 울리기 및 위치 갱신 요청을 Home Assistant 엔티티로 제공합니다.

> ⚠️ 공식 API가 아닌 역공학 기반 통합입니다. 삼성 웹·인증 규격이 변경되면 동작이 달라질 수 있습니다.

## v1.4.0 지속 인증

기존의 “브라우저 쿠키 한 번 복사” 방식은 삼성 서버가 `JSESSIONID`를 폐기하면 자동 복구할 수 없었습니다. v1.4.0부터는 **Samsung Account 지속 인증**을 기본 방식으로 제공합니다.

```text
Samsung Account 로그인 1회
        ↓
장기 master authorization 비공개 저장
        ↓
회전 access / refresh token 관리
        ↓
현재 JSESSIONID 검증
        ↓ 만료 시
새 인증 코드 + 서버 state로 JSESSIONID 자동 재발급
```

추가 보호 조치도 함께 적용됩니다.

- 인증 요청을 config entry별 잠금으로 직렬화해 KeepAlive·폴링·버튼 요청의 충돌 방지
- 인증 실패 시 기존 CSRF 폐기 → 새 CSRF 발급 → 필요 시 전체 웹 세션 재발급
- 서버가 삭제한 쿠키를 이전 저장값에서 되살리지 않고 현재 쿠키 jar로 완전 교체
- 기본 KeepAlive 180초 및 ±12% 시간 분산
- 순수 읽기 요청만 인증 복구 후 제한적으로 재시도
- 액티브 위치 폴링, Ring, 수동 위치 갱신처럼 기기를 깨우거나 동작시키는 요청은 전송 후 자동 재실행하지 않음
- 쿠키·토큰·로그인 콜백 원문을 로그에 남기지 않음

---

## Features

기기마다 아래 엔티티가 생성됩니다. 기기 종류와 삼성 서버 응답에 따라 일부 정보는 제공되지 않을 수 있습니다.

- **Device Tracker**: 기기 위치(GPS)
- **Sensor**
  - 배터리
  - Last update(삼성 서버의 `gps_date` 기준)
- **Button**
  - Ring
  - Stop Ring
  - Update Location

> 이 통합은 SmartTag의 물리 버튼 클릭 이벤트를 받을 수 없습니다. SmartThings Find 웹사이트가 제공하는 기능 범위에서 동작합니다.

---

## Install (HACS)

[![Open your Home Assistant instance and show the HACS repository.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=1bobby-git&repository=HA-SmartThings-Find&category=integration)

1. HACS → **Integrations** → 우측 상단 ⋮ → **Custom repositories**
2. Repository: `https://github.com/1bobby-git/HA-SmartThings-Find`
3. Category: **Integration**
4. 설치 후 Home Assistant 재시작

---

## Setup (Authentication)

[![Open your Home Assistant instance and start setting up the integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=smartthings_find)

### 방법 1. Samsung Account — 권장

1. 통합 추가 화면에서 **Samsung Account (자동 세션 복구·권장)** 선택
2. 화면에 표시된 Samsung Account 로그인 링크 열기
3. 삼성 로그인 및 2단계 인증 완료
4. 브라우저가 `ms-app://...` 주소로 이동하거나 앱을 열 수 없다는 화면을 표시하면 주소 전체 복사
5. Home Assistant 입력창에 전체 주소 붙여넣기

로그인은 삼성 페이지에서 처리됩니다. 이 통합은 삼성 계정 비밀번호나 2단계 인증 값을 받거나 저장하지 않습니다.

로그인 완료 후 다음 파일들이 Home Assistant `.storage` 아래에 생성됩니다.

```text
smartthings_find_auth/account.master.json
smartthings_find_auth/account.state.json
smartthings_find_auth/account.pending.json   # 로그인 중에만 사용
```

`master.json`은 웹 세션과 서비스 토큰을 다시 발급할 수 있는 중요한 인증정보입니다. Home Assistant 백업에는 포함될 수 있으므로 백업 파일도 계정 비밀번호와 동일한 수준으로 보호해야 합니다. 통합을 삭제하면 해당 인증 상태와 저장된 세션 쿠키도 제거됩니다.

### 방법 2. Cookie header — 호환 모드

Samsung Account 로그인 흐름이 환경에서 동작하지 않을 때 사용할 수 있습니다.

1. 브라우저에서 `https://smartthingsfind.samsung.com/` 로그인
2. 개발자도구(F12) → **Network**
3. 새로고침 후 **`chkLogin.do`** 선택
4. **Request Headers → `Cookie:`** 전체 줄 복사
5. 통합에서 **Cookie header (수동 호환 모드)** 선택 후 붙여넣기

![Cookie header example](media/cookie.png)

이 모드도 쿠키 회전 저장, CSRF 재발급, 요청 직렬화 및 KeepAlive 보호를 사용합니다. 다만 삼성 서버가 세션 자체를 완전히 폐기하면 새 Cookie header를 수동 입력해야 합니다.

> 쿠키, `ms-app://` 콜백 주소, `.storage` 인증 파일을 로그·이슈·채팅에 공유하지 마세요.

---

## 자동 세션 복구 순서

Samsung Account 인증을 사용하는 경우 `Logout`, `fail`, HTTP 401/403이 발생하면 다음 순서로 복구합니다.

```text
1. 캐시된 CSRF 제거
2. 현재 쿠키로 chkLogin.do 재검증 및 새 CSRF 발급
3. 동일 읽기 요청 1회 재시도
4. 계속 실패하면 Samsung master authorization으로 새 JSESSIONID 발급
5. 쿠키 jar 전체 교체 후 새 CSRF 발급
6. 읽기 요청 1회 재시도
7. master authorization까지 거부될 때만 Home Assistant 재인증 시작
```

삼성 계정에서 직접 로그아웃했거나 보안 설정을 변경했거나 삼성 서버가 master authorization을 취소한 경우에는 다시 로그인해야 합니다. 영구 인증을 보장하는 방식은 아니지만, 일반적인 웹 쿠키 만료는 사용자 개입 없이 복구하도록 구성되어 있습니다.

---

## KeepAlive

기본 KeepAlive 간격은 **180초**입니다. 매 요청 시점은 설정값에서 약 ±12% 범위로 분산됩니다.

- `chkLogin.do`로 로그인 상태와 CSRF 확인
- 기기 목록 엔드포인트 호출로 실제 세션 활동 유지
- 응답에서 회전된 쿠키를 즉시 비공개 저장소에 반영
- KeepAlive와 일반 폴링, 버튼 명령이 동시에 인증 상태를 변경하지 않도록 하나의 잠금으로 직렬화

옵션에서 60~86400초 사이로 변경할 수 있습니다. 너무 짧게 설정하면 삼성 서버 요청량이 불필요하게 증가하므로 기본값부터 사용하는 것을 권장합니다.

---

## Active / Passive mode

- **Passive**: 서버에 마지막으로 보고된 위치만 조회합니다. 배터리 영향이 적습니다.
- **Active**: 조회 전에 위치 업데이트 요청을 보내 최신 위치를 유도합니다. 정확도와 즉시성은 높아질 수 있지만 배터리 사용량이 증가할 수 있습니다.

SmartTag와 휴대폰·워치·이어버드는 각각 별도로 설정할 수 있습니다.

---

## Options

Home Assistant → 설정 → 디바이스 및 서비스 → SmartThings Find → **구성**

- **업데이트 간격**: 위치·배터리 폴링 주기, 기본 120초
- **세션 유지 간격**: KeepAlive 기준 주기, 기본 180초
- **SmartTag 모드**: Passive / Active
- **기타 기기 모드**: Passive / Active
- Cookie 호환 모드에서는 새 Cookie header를 선택적으로 교체 가능

인증 방법 자체를 변경하려면 통합의 **재구성**을 실행합니다.

---

## Troubleshooting

### Samsung Account 로그인 후 `ms-app://` 주소를 찾기 어려움

브라우저가 외부 앱 실행 확인창을 표시하거나 “이 링크를 열 수 없음”을 표시할 수 있습니다. 주소 표시줄 또는 개발자도구의 마지막 이동 주소에서 `ms-app://`로 시작하는 전체 값을 복사합니다. 이 주소는 일회성 인증정보를 포함하므로 공유하지 마세요.

### 자동 복구 후에도 계속 Unavailable

1. Home Assistant 로그에서 `automatic session recovery` 또는 `starting reauth` 확인
2. 삼성 계정에서 전체 로그아웃·비밀번호 변경·보안 설정 변경 여부 확인
3. 통합의 재인증 절차로 Samsung Account 로그인 다시 진행
4. 웹사이트에서 동일 계정의 기기 목록이 정상 표시되는지 확인

### 배터리가 Unknown

일부 기기는 웹 응답에서 배터리를 제공하지 않습니다. 특히 이어버드는 계정·모델 상태에 따라 배터리가 누락될 수 있습니다.

### Ring이 동작하지 않음

주변 Galaxy 기기가 SmartTag 명령을 중계할 수 없는 상태이거나 해당 기기 유형에서 웹 Ring을 지원하지 않을 수 있습니다. SmartThings Find 웹사이트에서 먼저 같은 명령을 확인하세요.

---

## Debug Logs

```yaml
logger:
  default: info
  logs:
    custom_components.smartthings_find: debug
```

디버그 로그에도 쿠키, 토큰, 콜백 주소 원문은 기록하지 않도록 구현되어 있습니다. 문제 보고 전 로그에 인증정보가 포함되지 않았는지 다시 확인하세요.

---

## Notes / Limitations

- 삼성의 비공개·비문서화 API를 사용하므로 서버 변경에 영향을 받을 수 있습니다.
- 삼성 서버가 계정의 장기 인증을 취소하면 사용자 재로그인이 필요합니다.
- 위치 갱신 요청 성공이 새 GPS 위치 수신을 보장하지 않습니다.
- 기기가 오프라인이거나 절전 상태이면 위치·배터리·Ring 결과가 지연되거나 실패할 수 있습니다.

---

## Credits / Upstream

- Original upstream: `Vedeneb/HA-SmartThings-Find` (archived / read-only)
- Persistent Samsung Account authorization: `charlesbel/samsung-re-find` (MIT)
