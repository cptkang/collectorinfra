# 구현 계획 — Plan 81 호스트 가용성 사전 판정

> SPEC: `SPEC-host-availability-precheck.md` · 요구 근거: `plans/81` · 결정: D-175(예약)
> 게이트 확정(2026-08-28): G-1 **기본 on** · G-2 **거부+사실 브리핑** · G-3 **DOWN 알람 예외** ·
> G-4 **해소(양 엔진 대칭)** · G-5 **경고 후 진행**

## 의존 그래프

```
T1 판정 도메인 (src/domain/host_availability.py)          ← 의존 없음
T2 조회 확장 (polestar_hostname_resolver)                  ← 의존 없음 (T1과 병렬 가능)
T3 설정 플래그 (CompositeConfig)                           ← 의존 없음
        │
        ├─→ T4 process_query 단일 경로       ← T1·T2·T3
        │      └─→ T5 process_query fan-out  ← T4 (같은 파일·대칭 요구)
        │             └─→ T6 결과 문구·메타  ← T5
        ├─→ T7 fault_diagnosis 게이트        ← T1·T2·T3
        │      └─→ T8 sre_agent 계약 확장    ← T7 (본체가 실어 보내야 소비처가 성립)
        │             └─→ T9 sre_agent 가드  ← T8
        └─→ T10 investigation_trigger        ← T8 (payload 계약 선행)
T11 문서·결정 등재                                          ← 전부
```

**수직 슬라이스**: T1~T4가 첫 완결 슬라이스다(판정 → 프로세스 단일 조회에서 사용자가 사유를 받는다).
T5~T6은 그 슬라이스의 확장, T7~T10은 조사 경로 슬라이스.

## 순서와 근거

| 순서 | 태스크 | 왜 이 순서인가 |
|---|---|---|
| 1 | T1 판정 도메인 | 나머지 전부가 이 타입을 소비한다. 순수 함수라 테스트가 가장 싸다 |
| 2 | T2 조회 확장 | T1과 독립. **기존 `resolve()` 불변**이 수용 기준이라 먼저 고정 |
| 3 | T3 설정 | 배선 전에 있어야 배선이 플래그를 참조할 수 있다 |
| 4 | T4 → T5 | 단일 → fan-out. **같은 파일이라 순차**(병렬 편집 충돌 방지) |
| 5 | T6 문구 | 수집 동작이 확정된 뒤 표현을 붙인다 |
| 6 | T7 → T8 → T9 | 본체 게이트 → 계약 → 소비처. 계약이 먼저 실려야 `sre_agent` 가드가 검증 가능 |
| 7 | T10 | 알람 경로는 payload 계약(T8) 확정 후 |
| 8 | T11 | 구현 확정 후 문서화(추정 기록 금지) |

## 위험과 완화

| 위험 | 완화 |
|---|---|
| **기본 on이 정상 조회를 차단** (최대 위험) | `unavailable`만 차단·`unknown`은 진행. 조회 실패 시 판정 생략하고 종전 경로로 진행. T4에 "판정 실패 → 종전 동작" 테스트 필수 |
| 단일/fan-out 비대칭 (저장소 반복 실수) | T5를 T4와 **분리된 태스크**로 두고 각각 테스트. 문구 규약을 T6에서 한 번 더 대조 |
| `resolve()` 호출부 회귀 | 반환형 불변 + 기존 13건 무수정 통과를 T2 수용 기준으로 명시 |
| DB2 컬럼 대소문자 편차 | 기존 `_row_value` 재사용(사본 금지). T2에 대문자 응답 테스트 |
| `sre_agent` 계약 후방 호환 깨짐 | 추가 키·선택 인자만. "필드 없으면 통과" 테스트를 T9에 고정 |
| DOWN 알람 자기차단 | T10에서 알람 종류 예외를 **결정적**으로 판정하고 테스트 |

## 검증 체크포인트

- **CP1 (T3 완료)**: `pytest tests/test_domain tests/test_dbhub -q` + `arch_check --ci` — 배선 전 계층 확인
- **CP2 (T6 완료)**: `pytest tests/test_orchestration -q` — 프로세스 슬라이스 완결
- **CP3 (T10 완료)**: `pytest -q --ignore=tests/e2e` + `cd sre_agent && pytest -q` — 전체 회귀
- **CP4 (T11 완료)**: 기준선 대조(회귀 0)·`arch_check --ci` exit 0
