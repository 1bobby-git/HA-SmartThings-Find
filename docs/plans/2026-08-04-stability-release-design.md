# HA-SmartThings-Find 안정성/릴리스 통합 설계안

## 핵심 문제
- 현재 구현에서 `gpsUtcDt` 문자열이 누락되거나 형식이 다를 경우 위치 파싱이 예외를 일으켜 `get_device_location()` 처리 전체가 `None`으로 떨어져 해당 디바이스 업데이트가 무효화됨.
- `manifest.json`의 `version`은 `1.2.0`으로 증가했으나 Git tag 및 GitHub release가 `v1.1.20`에 머물러 HACS 사용자에게 배포 경로가 일치하지 않음.
- 회귀 방지 테스트 부재로 파싱/에러 경로 안정화 검증이 불충분함.

## 목표
- `gpsUtcDt` 파싱 실패가 단일 필드의 문제로 처리되도록 하여, 잘못된 타임스탬프가 전체 위치 센서/트래커 갱신을 막지 않도록 함.
- v1.2.0 배포 준비(버전, 태그, release, HACS 검증)을 완료할 수 있는 실행 가능한 계획 수립.
- 최소 범위 변경(`utils.py`, 엔티티/조정기 동작/로그, 테스트)으로 근본 원인만 고침.

## 비목표
- SmartThings API 스키마 전면 재설계.
- UI/번역 전체 리디자인.
- HA 핵심 정책 변경(디바이스/엔티티 생명주기 재설계).
- 장기 관측/알림 인프라 추가 구축.

## 근본 수정(작은 변경)
1. GPS 타임스탬프 파싱을 안전화해 실패를 개별 op 처리로 제한.
   - 대상: `custom_components/smartthings_find/utils.py`
   - 기존:
     - `parse_stf_date(datestr: str) -> datetime`의 직접 `datetime.strptime` 사용.
     - `get_device_location()` 내에서 `gpsUtcDt` 파싱 실패 시 예외가 catch되어 전체 함수가 `None` 반환되는 경로.
   - 제안:
     - `parse_stf_date`를 `Optional[datetime]` 반환으로 확장하거나, 파싱 실패 시 `None`을 반환하는 신규 유틸 함수 추가.
     - `get_device_location()`에서 `gpsUtcDt` 파싱은 `safe` 경로로 분기:
       - 파싱 실패 시 `used_loc`는 해당 op를 스킵하고 계속 처리.
       - 마지막 위치 비교(`gps_date`)도 유효 날짜만 대상으로 갱신.
       - 전체 디바이스가 유효 위치를 갖지 못하더라도 기존 `oprnType`/배터리 처리와 기본 `update_success` 플래그는 유지.
     - 로깅:
       - 실패 op는 `DEBUG`로 남기고, 상태 갱신 실패 시 경고 노출.
2. 잘못된 날짜 문자열이 마지막 업데이트 UI에 미치는 부정적 효과 제거:
   - 대상: `custom_components/smartthings_find/sensor.py:SmartThingsFindLastUpdateSensor.native_value`
     - 기존 로직을 보존하되 `gps_date`가 파싱 실패로 사라진 경우 `None` 처리 동작을 명시적으로 허용.
   - 추가 조건:
   - `fetched_at` 폴백은 임의 즉시 갱신으로 마지막 업데이트 센서 오탐을 방지해야 하므로 기존 정책 유지.

## 대상 파일/심볼
- 핵심 수정:
  - `custom_components/smartthings_find/utils.py`
    - `parse_stf_date`
    - `get_device_location` 내 `gpsUtcDt` 처리 분기
- 검증/보강:
  - `custom_components/smartthings_find/sensor.py`
    - `SmartThingsFindLastUpdateSensor.native_value`
  - `tests/...` (신규)
    - `custom_components/smartthings_find/tests/test_utils.py` (파싱 및 날짜 실패 유연성)
    - `custom_components/smartthings_find/tests/test_get_device_location.py` (혼합 정상/비정상 gpsUtcDt 데이터)

## 구현 단계 (작성형 체크리스트)
- [ ] A. 안전한 날짜 파싱 유틸 정비
  - [ ] `custom_components/smartthings_find/utils.py`에서 `parse_stf_date` 동작 변경 설계 확정
  - [ ] 기대 결과: 잘못된 `gpsUtcDt` 입력에서 예외 미전파, 해당 `op`만 건너뛰기
- [ ] B. 위치 파싱 루프 분기 보강
  - [ ] `custom_components/smartthings_find/utils.py:get_device_location`에서 `gpsUtcDt` 파싱 실패 시 `continue` 또는 다음 op 진행
  - [ ] 기대 결과: 디바이스 위치 데이터(`used_loc`)가 완전 소거되지 않고, 유효한 op 데이터는 계속 반영
- [ ] C. 경보/상태 규칙 보강
  - [ ] `custom_components/smartthings_find/utils.py`에서 실패 op 로그 레벨/메시지 표준화
  - [ ] 기대 결과: 장애 디버깅이 용이하며 재시도/재인증 로직은 기존 동작 유지
- [ ] D. 테스트 추가 (TDD Red-Green)
  - [ ] Red: 날짜 포맷 불일치 케이스 실패 테스트 작성
  - [ ] Green: 구현 반영 후 동일 테스트 통과
  - [ ] 회귀 테스트: 혼합 데이터(정상 op + malformed 날짜)에서 센서 상태/배터리 상태의 유효성 검증
- [ ] E. 릴리스 정합성 정리
  - [ ] `custom_components/smartthings_find/manifest.json` 버전이 `1.2.0` 유지됨 확인
  - [ ] `v1.2.0` 태그 및 GitHub release 생성(변경 내역, 설치/업데이트 노트 포함)
  - [ ] HACS 메타 경로(manifest, repository content, assets)가 설치 가능한 상태인지 확인

## 명령형 검증 계획
- 파싱 회귀:
  - `pytest tests/test_utils.py -q`
  - `pytest tests/test_get_device_location.py -q`
- 패키지/정적:
  - `python -m pip install -r` 기반의 로컬 체크는 생략(환경 의존), 대신 `.github/workflows`의 기존 validate/hacs 단계에서 결과 사용
- HACS/릴리스 정합성:
  - `git tag --list`
  - `git tag -a v1.2.0 -m "feat: harden gpsUtcDt parsing"` (릴리스 후)
  - `gh release view v1.2.0 --repo 1bobby-git/HA-SmartThings-Find`

## 오류 처리/검증 정책
- `ConfigEntryAuthFailed` 경로는 기존 동작 유지:
  - 인증 만료/실패 시 `reauth` 트리거.
- `gpsUtcDt` 파싱 실패는 인증 오류로 오인하지 않음:
  - 실패 항목은 단일 op 스킵, 전체 위치 갱신 중단 X.
- 비정상 응답(`Logout`, `fail`) 기존 경고 경로 유지.
- 에러 로그는 재시도 유도/triage 가능하도록 `DEBUG`와 `WARNING`의 경계 명확화.

## TDD(적용 순서)
- Red: 신규 테스트가 `utils.parse_stf_date`의 비정상 입력에서 실패를 유발하거나 `get_device_location` 전체 None 반환을 기대하도록 먼저 작성.
- Green: 파싱 유틸과 위치 파싱 루프를 최소 변경으로 수정하여 테스트 통과.
- Refactor: 테스트가 가리키지 않은 경로의 중복 로직 정리.

## 선택적 커밋 전략
- 1차 커밋: `utils.py` 파싱 방어 로직 + 최소 로그 조정.
- 2차 커밋: 센서/테스트 보강.
- 3차 커밋: 릴리스 메타 검증 결과 반영 및 release 노트 정합성 문서.
- 각 커밋은 독립 롤백 가능 범위로 분할.

## Lore 형식 커밋 메시지 예시
- `Harden malformed gpsUtcDt handling to keep last-location updates from being dropped`
- Body:
  - `Constraint: Preserve existing auth/reauth and polling semantics while isolating parse failures`
  - `Rejected: full get_device_location rewrite | rejected to minimize risk`
  - `Confidence: high`
  - `Scope-risk: moderate`
  - `Directive: Keep malformed payload handling opt-in-safe by skipping invalid op items only`
  - `Tested: planned`
  - `Not-tested: full HA integration runtime integration matrix`

## 완료 기준 (DoD)
- [ ] 잘못된 `gpsUtcDt` 하나로 인해 단일 디바이스 위치 전체가 `None`으로 무효화되지 않음.
- [ ] 테스트에서 malformed 날짜 케이스 통과.
- [ ] `v1.2.0` 태그+release가 존재하고 HACS에서 매니페스트로 설치 가능 상태 확인.
- [ ] `manifest.json`, 태그, release 간 버전/문자열 정합성 유지.
