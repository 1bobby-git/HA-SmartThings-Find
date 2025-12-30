# HA-SmartThings-Find

Samsung **SmartThings Find**(비공식/역공학) 기능을 Home Assistant에서 사용하기 위한 커스텀 통합입니다.  
원본 프로젝트 **Vedeneb/HA-SmartThings-Find**를 기반으로 하며, **삼성 로그인 흐름 변경으로 QR 로그인 엔드포인트가 사라진(또는 404로 깨진) 환경**에 맞춰 **브라우저 쿠키(Cookie header) 수동 입력 방식**으로 인증하도록 포크/수정한 버전입니다.

> ⚠️ 경고: 공식 API가 아닙니다. 삼성 웹/백엔드 변경에 따라 언제든 동작이 깨질 수 있습니다.

---

## Features

기기마다 아래 엔티티가 생성됩니다.

- **Device Tracker**: 위치(GPS) — 지도에서 확인 가능
- **Sensor**
  - 배터리(가능한 기기만)
  - GPS 정확도(m)
  - 마지막 위치 갱신 시각(Last Seen)
- **Button**
  - **Refresh**: 해당 기기만 즉시 위치 갱신 시도(폴링 주기 기다리지 않음)

---

## Install (HACS)

1. HACS → **Integrations** → 우측 상단 ⋮ → **Custom repositories**
2. Repository: `https://github.com/1bobby-git/HA-SmartThings-Find`
3. Category: **Integration**
4. 설치 후 Home Assistant 재시작

> HACS에서 README/버전이 갱신되지 않으면: 레포에서 **Release(태그)** 를 발행한 뒤 HACS에서 **Update information** / **Redownload**를 해주세요.

---

## Setup (Authentication)

현재는 QR 로그인 방식이 동작하지 않는 경우가 많아, **브라우저에서 로그인한 세션 쿠키를 그대로 붙여넣는 방식**으로 인증합니다.

### 1) SmartThings Find 열기
- [smartthingsfind.samsung.com](https://smartthingsfind.samsung.com/)

### 2) 쿠키 추출 (권장: `chkLogin.do`)
1. 브라우저에서 `https://smartthingsfind.samsung.com/` 접속 후 로그인
2. 개발자 도구(F12) → **Network**
3. 페이지 새로고침(Refresh)
4. 요청 목록에서 **`chkLogin.do`** 선택
5. **Request Headers → `Cookie:`** 라인을 **통째로 복사**
6. Home Assistant → 설정 → 디바이스 및 서비스 → 통합 추가 → **SmartThings Find**  
   → 입력창에 **Cookie 줄 전체** 붙여넣기

![Cookie header example](media/cookie.png)

✅ 팁  
- `JSESSIONID`만 넣으면 실패할 수 있습니다.  
- 가능하면 `chkLogin.do` 요청의 **Cookie 헤더 전체**를 넣어주세요.

---

## Active / Passive Mode

- **Passive mode**: 서버에 “마지막으로 보고된 위치”를 읽기만 함 (배터리 영향 적음)
- **Active mode**: 위치 업데이트 요청을 보내 최신 위치를 유도 (정확도/즉시성 ↑, 배터리 영향 ↑)

옵션에서 기기 유형별 Active mode를 켜고 끌 수 있습니다.

---

## Options

Home Assistant → 통합 → SmartThings Find → **구성(Configure)**

- `update_interval` : 업데이트 간격(초) (최소 30초)
- `active_mode_smarttags` : SmartTag류 Active mode
- `active_mode_others` : 기타 기기(폰/워치/이어버드 등) Active mode

---

## Troubleshooting

### 1) `chkLogin.do returned 200 but body='fail'`
- 쿠키가 만료되었거나 잘못 복사된 경우입니다.
- 다시 로그인 후 `chkLogin.do`의 **Cookie 헤더 전체**를 재복사해 넣어주세요.

### 2) `401 'Logout'`
- 세션이 만료된 상태입니다.
- 통합이 1회 재시도 후에도 실패하면 **Reauth(재인증)** 를 요청합니다.
- 재인증에서 쿠키를 새로 입력하세요.

### 3) `cannot import name 'CONF_JSESSIONID'...`
- 설치 파일이 “버전 믹스”로 꼬였을 때 발생합니다(구버전 const.py + 신버전 init.py).
- 해결(권장):
  1) `/config/custom_components/smartthings_find/` 폴더를 통째로 삭제  
  2) Home Assistant 재시작  
  3) HACS에서 재설치(또는 Redownload)

### 4) `Unexpected error validating cookie auth: 'str' object has no attribute 'raw_host'`
- aiohttp 버전에 따라 `cookie_jar.update_cookies(..., response_url=...)`가 **URL 객체**를 요구합니다.
- 해결: `utils.py`의 `apply_cookies_to_session()`에서 `yarl.URL("https://smartthingsfind.samsung.com")`를 사용하도록 수정하세요. (아래 “Fix” 섹션 참고)

---

## Debug (Logs)

아래 YAML을 `configuration.yaml`에 추가 후 재시작:

```yaml
logger:
  default: info
  logs:
    custom_components.smartthings_find: debug
```

---

## Credits

- Original project: **Vedeneb/HA-SmartThings-Find**
- This fork: **Cookie header auth** 기반으로 수정/유지보수

---

## License

원본 프로젝트의 라이선스를 따릅니다. (레포의 LICENSE 파일을 참고하세요)
