# Capability Map: Plan 83 — 노이즈 캔슬링 피드백 루프 · 알람 표시 레벨 · 설정 UI 커버리지

> 요구 근거의 정본은 **`plans/83`**, 문제 정의 출처는 **`docs/28`**이다. 여기에 배경을 복사하지 않는다.
> **승인**: 2026-08-28 사용자 — "제안대로 3모듈".
> 결정 예약: **D-177**(feedback-loop) · **D-178**(view-level) · **D-179**(settings-ui-coverage) —
> `docs/02_decision.md` 「채번 이력」 등재 완료.

## 모듈

| Module id | 책임 | 의존 |
|---|---|---|
| `alarm-feedback-loop` | 피드백·ack 루프의 보안(존 RBAC)·정확성·감사·성능·철회 (plans/83 트랙 A) | — |
| `settings-ui-coverage` | 설정 카탈로그 섹션 분류 완결 + 경계 밖 설정 안내 (트랙 C) | — |
| `alarm-view-level` | 알람 UI 표시 레벨 선택 + SUPPRESS 스트리밍 옵트인 (트랙 B) | `alarm-feedback-loop`, `settings-ui-coverage` |

**빌드 순서**: `alarm-feedback-loop` ∥ `settings-ui-coverage` → `alarm-view-level`

## 경계가 이 자리인 이유

- **독립 출하 가능**: 세 모듈은 각각 단독으로 배포·검증된다. 피드백 RBAC은 표시 레벨 없이도
  유효하고, 섹션 분류는 나머지 둘과 무관하게 완결된다.
- **의존 방향 단방향**: `alarm-view-level`이 두 모듈에 의존하되 역방향은 없다.
  - → `alarm-feedback-loop`: 레벨 셀렉트가 `GET /alarm/capabilities`(A7 산출물)로 게이트 상태를 읽는다.
  - → `settings-ui-coverage`: B5가 신설하는 `NOISE_SSE_SUPPRESSED_ENABLED`는 C1의 **섹션 누락 감지
    테스트**에 걸려야 한다. 테스트가 먼저 있어야 같은 누락이 재발하지 않는다.
- **한 모듈을 잘라도 나머지 요구가 재작성되지 않는다**: `alarm-view-level`을 통째로 빼도
  트랙 A·C의 수용 기준은 그대로 성립한다.

## 인터페이스는 제공자 쪽 스펙에 있다

`GET /api/v1/alarm/capabilities`의 응답 계약은 **제공자인 `alarm-feedback-loop` 스펙**이 소유한다
(`SPEC-alarm-feedback-loop.md` §계약). 소비자인 `alarm-view-level`은 필드를 읽기만 하고 정의하지 않는다.

## 모듈 스펙

- `SPEC-alarm-feedback-loop.md`
- `SPEC-settings-ui-coverage.md`
- `SPEC-alarm-view-level.md`
