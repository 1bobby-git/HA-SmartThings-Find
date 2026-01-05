# SmartThings Find

Samsung **SmartThings Find** 비공식(역공학) 연동을 Home Assistant에서 쓰기 위한 커스텀 통합입니다.  
원본 프로젝트(`Vedeneb/HA-SmartThings-Find`)를 기반으로, **현재 삼성 로그인 흐름 변경으로 QR 로그인이 사라진 상태**에 맞춰 **브라우저 쿠키(Cookie header) 수동 입력 방식**으로 포크/수정한 버전입니다.

> ⚠️ 주의: 공식 API가 아닙니다. 삼성 웹/백엔드 변경으로 언제든 동작이 깨질 수 있습니다.

---

## Features

기기마다 아래 엔티티가 생성됩니다. (기기 타입/계정 상태에 따라 일부는 미지원일 수 있습니다)

- **Device Tracker**: 기기 위치(GPS)
- **Sensor**
  - 배터리(가능한 기기만 / 특히 이어버드는 안 나오는 경우가 많습니다)
  - Last update(서버가 보고한 gps_date 기반)
- **Button**
  - Ring(벨 울리기) 요청
  - Stop Ring(벨 중지) *(기기/타입에 따라 미지원일 수 있음)*
  - Update Location(위치 업데이트 요청) *(Active 모드가 켜져 있을 때 효과가 큼)*

> ℹ️ 참고: 이 통합은 **SmartTag 물리 버튼(실물 클릭)** 이벤트를 받을 수 없습니다.  
> SmartThings Find **웹사이트가 제공하는 기능 범위**에서만 동작합니다.

---

## Install (HACS)

아래 버튼을 누르면, Home Assistant에서 HACS 커스텀 레포 추가 화면으로 이동합니다.

[![Open your Home Assistant instance and show the HACS repository.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=1bobby-git&repository=HA-SmartThings-Find&category=integration)

1. HACS → **Integrations** → 우측 상단 ⋮ → **Custom repositories**
2. Repository: `https://github.com/1bobby-git/HA-SmartThings-Find`
3. Category: **Integration**
4. 설치 후 Home Assistant 재시작

---

## Setup (Authentication)

통합 추가 화면으로 바로 이동하는 버튼입니다.

[![Open your Home Assistant instance and start setting up the integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=smartthings_find)

현재는 QR 로그인 엔드포인트가 동작하지 않는 경우가 많아, **브라우저에서 로그인한 세션 쿠키를 그대로 붙여넣는 방식**으로 인증합니다.

### 쿠키 추출 방법 (권장: `chkLogin.do`)
1. 브라우저에서 `https://smartthingsfind.samsung.com/` 로그인
2. 개발자도구(F12) → **Network**
3. 페이지 새로고침(Refresh)
4. 요청 목록에서 **`chkLogin.do`** 클릭
5. **Request Headers → `Cookie:`** 라인을 **통째로 복사**
6. Home Assistant → 설정 → 디바이스 및 서비스 → 통합 추가 → **SmartThings Find**  
   → 입력창에 **Cookie 줄 전체** 붙여넣기

![Cookie header example](media/cookie.png)

> ✅ 팁: `JSESSIONID`만 넣으면 실패할 수 있습니다.  
> 가능하면 `chkLogin.do` 요청의 **Cookie 헤더 전체**를 넣어주세요.

### 쿠키/세션 만료 & 재인증(reauth)

SmartThings Find 웹 세션은 **시간이 지나면 만료**될 수 있습니다.  
만료되면 엔티티가 **[사용 불가]/Unavailable**로 바뀌거나, 로그에 `chkLogin.do ... body='fail'` 같은 인증 실패가 나타날 수 있습니다.

- 세션이 만료되면 Home Assistant가 통합을 **재인증 필요** 상태로 전환할 수 있습니다.
- 이 경우 통합 화면에서 **재구성/재인증**을 통해 새 쿠키를 다시 넣어주세요.

> 🔐 보안: 쿠키는 계정 인증 정보입니다. 로그/이슈에 그대로 올리지 마세요.

---

## KeepAlive (세션 유지)

일부 환경에서는 **몇 시간 후 세션이 만료되어** 기기가 Unavailable이 되는 경우가 있습니다.  
이를 완화하기 위해, 통합은 **KeepAlive**를 통해 주기적으로 SmartThings Find 웹 엔드포인트를 호출해 세션을 유지하려고 시도합니다.

- KeepAlive는 완전한 보장을 하지는 않습니다(삼성 정책/변경에 따라 달라질 수 있음).
- 그래도 일반적으로 “idle 로그아웃” 유형에는 도움이 됩니다.
- KeepAlive 동작 중 **쿠키가 회전(Set-Cookie)** 될 수 있어, 가능하면 최신 쿠키를 entry에 저장해 유지합니다.

---

## Active / Passive mode

이 통합에는 위치 갱신 방식이 두 가지가 있습니다.

- **Passive mode**: 서버에 “마지막으로 보고된 위치”를 읽기만 함 (배터리 영향 적음)
- **Active mode**: 위치 업데이트 요청을 보내 최신 위치를 유도 (정확도/즉시성 ↑, 배터리 영향 ↑)

### 모드는 어떻게 설정하나요?
옵션에서 기기 유형별로 **Active / Passive를 선택**합니다.

- 옵션이 **켜져 있으면(ON) = Active mode**
- 옵션이 **꺼져 있으면(OFF) = Passive mode**

기기 타입별로 나누는 이유:
- **SmartTag**는 Active로 얻는 이점이 큰 편이고,
- **기타 기기(폰/워치/이어버드 등)** 는 Active 요청이 배터리/상태에 영향을 줄 수 있어 분리해 둡니다.

---

## Options

Home Assistant → 통합 → SmartThings Find → **구성(Configure)**

- `update_interval` : 업데이트 간격(초)
- `keepalive_interval` : 세션 유지(KeepAlive) 간격(초)  
  - 추천 시작값: **300초(5분)**  
  - 여전히 만료되면: 120초(2분)로 낮춰 테스트
- `active_mode_smarttags` : SmartTag Active mode
- `active_mode_others` : 기타 기기(폰/워치/이어버드 등) Active mode

---

## Notes / Limitations

- “Ring”은 주변의 갤럭시 기기(폰/태블릿)가 BLE로 태그에 전달하는 구조라, 주변에 연결 기기가 없으면 실패할 수 있습니다.
- 기기 종류에 따라 위치/배터리 정보가 항상 오지 않을 수 있습니다.
- SmartThings **모바일 앱**과 **웹사이트** 동작이 100% 동일하지 않을 수 있습니다. 가능하면 웹사이트에서 먼저 테스트하세요.
- 비공식 통합이라 삼성 측 변경에 취약합니다.

---

## Troubleshooting

### 1) 몇 시간 후 Unavailable로 바뀜
- 쿠키 만료 가능성이 큽니다 → 새 쿠키로 재인증(reauth)
- KeepAlive 간격을 줄여보세요(예: 300 → 120)
- SmartThings Find 웹사이트에서 같은 계정/기기로 정상 조회되는지 확인

### 2) 배터리가 Unknown/없음
- 일부 기기는 웹에서 배터리를 제공하지 않습니다(특히 이어버드)

### 3) Ring이 안 울림
- 주변에 중계할 Galaxy 기기가 없으면 실패할 수 있습니다
- 웹사이트에서 Ring이 되는지 먼저 확인

---

## Debug (Logs)

`configuration.yaml`에 추가 후 재시작:

```yaml
logger:
  default: info
  logs:
    custom_components.smartthings_find: debug
```

---

## Credits / Upstream

- Original upstream: `Vedeneb/HA-SmartThings-Find` (archived / read-only)
