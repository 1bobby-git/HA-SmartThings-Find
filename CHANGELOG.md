# 변경 이력

## 1.3.4 - 2026-09-02

- 마지막으로 확정한 SmartThings 기본 아이콘을 다시 적용했습니다.
- Home Assistant 로컬 Brands Proxy API가 사용할 256px 및 512px 투명 PNG를 컴포넌트 패키지에 포함했습니다.
- 위치 조회, 로그인 및 기기 명령 동작은 변경하지 않았습니다.

## 1.3.3 - 2026-09-02

- SmartThings Web과 동일한 최종 SmartThings 기본 아이콘을 적용했습니다.
- Home Assistant 로컬 Brands Proxy API용 256px 및 512px 투명 PNG를 갱신했습니다.
- 위치 조회, 로그인 및 기기 명령 동작은 변경하지 않았습니다.

## 1.3.1 - 2026-09-01

- 서버가 회전시킨 세션 쿠키를 config entry 대신 Home Assistant 비공개 저장소에 보관해 불필요한 통합 재로드를 제거했습니다.
- 사용자가 새 쿠키를 입력하면 이전 회전 세션은 자동으로 무효화됩니다.
- Home Assistant의 config-entry 단일 기기 모델에 맞춰 SmartThings Find 고유 식별자만 사용합니다.
- 이전 버전에서 추가된 외부 `smartthings` 식별자를 자동 정리합니다.
- 일부 기기 조회가 실패해도 마지막 정상 위치와 배터리 데이터를 유지합니다.
- 위치 갱신·벨 울리기 명령 실패를 사용자에게 오류로 전달하고 pending 상태를 즉시 종료합니다.
- 위치 갱신 후 지연 확인 작업을 config entry 수명주기에 연결했습니다.
