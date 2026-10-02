# 구현 계획 — Plan 82 Wave 8·9 (0건 진단 · 급증 조건)

> 지도: `CAPABILITY-MAP-execution-groups.md` v3 · 계획: `plans/82` v6 §6 · 예약 결정 **D-176 후속1·후속2**
> SPEC: `SPEC-empty-answer-diagnosis.md` · `SPEC-spike-condition.md`
> 태스크: `tasks/todo-82-wave89.md`

## 범위 (사용자 확정 2026-08-28)

**Wave 8 → Wave 9 연속.** 사용자 인터뷰 결과:

| 항목 | 확정 |
|---|---|
| 구현 범위 | **Wave 8 → 9 연속** |
| U14 (Wave 8 즉시 착수) | **예** (지시로 확정) |
| U15 (급증 구현) | **(b) 월 단위 한정** |
| U16 (자동 완화 재조회) | **(a) 제안까지만** (계획 권고 유지) |
| U17 (보존기간 실측) | **승인** — 단, **실행 불가**(DBHub `localhost:9099` 연결 거부 · `ACTIVE_DB_IDS=polestar` 샌드박스). 주 단위는 차단 경로로 구현 |
| U18 (기본 임계) | **+20%p** · 응답에 항상 노출 |
| U19 (집계 축) | **파일시스템 단위 행 유지** |

**U1~U13은 2차 모듈 소관으로 미확정 유지** — 이번 범위와 무관하다(§9 독립 레인).

## 기준선 (착수 전 실측 2026-08-28)

| 대상 | 값 |
|---|---|
| 대상 영역 (`test_nodes`·`test_utils`·`test_db_adapters`·`test_middleware`·`test_orchestration`·`test_semantic_routing`) | **6 failed · 1523 passed · 1 skipped** |
| 잔여 6건 | **순수 HEAD와 동일한 기존 실패** — `test_cache_management_synonyms` 3 · `test_output_generator::TestBuildResponsePrompt` 2 · `test_query_generator_mapping` 1 |
| `arch_check --ci` | exit 0 |

> **★ 착수 전 발견·수정한 자기 회귀 8건**: 1차(D-176)가 `_validate_sql_simple`에 `db_engine`
> 키워드를 추가했는데 기존 몽키패치 스텁 2곳이 구 시그니처라 `TypeError` → 넓은 except가
> "DB 실행 에러"로 강등 → 8건 연쇄 실패. **1차 검증 세트에 수정 대상 모듈의 본체 테스트가
> 빠져 있어 놓쳤다.** 격리 worktree(순수 HEAD + 자기 파일 10건)에서 재현해 책임을 확정하고
> 스텁을 `**_kw` 전방 호환으로 수정했다. `docs/18_known_mistakes.md` 등재 · D-176 검증 정정 부기.
>
> **판정 기준**: 이번 작업은 **6 failed를 늘리지 않고** 신규 테스트를 더한다.
> `test_output_generator`의 기존 실패 2건은 `TestBuildResponsePrompt`(내 대상은
> `_generate_empty_result_response`)이므로 **내 변경과 무관함을 유지**해야 한다.

## 의존 순서

```
W8-1 change_terms(선언+로더) ─┬─→ W8-2 empty_answer(순수 진단)  ─┐
                              │                                  ├─→ W8-4 배선
                              └─→ W8-3 condition_probe(SQL 수술) ─┘
                              │
                              └─→ W9-3 급증 의도·임계 ─┬─→ W9-4 배선
                                  W9-1 기간 쌍 해석 ───┤
                                  W9-2 비교 SQL 조립 ──┘
```

- **W8-1이 양 Wave의 공통 선행**이다(선언 파일 공유 — 지도 v3의 의존 방향 근거).
- W8-2·W8-3은 서로 독립(순수 로직 vs SQL 문자열 수술) → 순서 무관.
- W9-1·W9-2는 서로 독립. W9-2는 순수 문자열 조립이라 DB 없이 검증된다.
- 배선(W8-4·W9-4)은 각 Wave 마지막 — 플래그 off 기본이라 회귀 0.

## 파일 소유권

| 파일 | 소유 | 동시 작업 |
|---|---|---|
| `config/change_terms.yaml` (신규) | W8-1 | — |
| `src/domain/change_terms.py` (신규) | W8-1 · W9-3 확장 | — |
| `src/domain/empty_answer.py` (신규) | W8-2 | — |
| `src/nodes/condition_probe.py` (신규) | W8-3 | — |
| `src/nodes/output_generator.py` | W8-4 | clean |
| `src/nodes/result_organizer.py` | W8-4 | clean |
| `src/state.py` | W8-4 | **M**(1차 내 변경) — 추가만 |
| `src/config.py` | W8-4 · W9-4 | **M** — `Text2SQLConfig`(242~338)에 **동시 hunk 없음** 실측. `CompositeConfig`·`NoiseGateConfig`·`AppConfig`는 건드리지 않는다 |
| `src/utils/query_gen_common.py` | W9-1 | **M**(1차 내 변경) — 추가만 |
| `src/db_adapters/polestar/spike_sql.py` (신규) | W9-2 | — |
| `src/nodes/query_generator.py` | W9-4 | clean |

## 위험과 완화

| 위험 | 완화 |
|---|---|
| **프로브 SQL 수술 오류** | 사용자 조건 **화이트리스트**(지표 값 컬럼 수치 비교만) · 식별 0개면 프로브 미생성 · 예외는 사유와 함께 강등 |
| 배관 conjunct 오제거 → 무의미한 수 | `BETWEEN`·`IS NULL`·문자열 등호는 **사용자 조건 아님**으로 판정. 최상위만 보고 서브쿼리 불변 |
| 0건 응답 회귀 | 플래그 off면 `_generate_empty_result_response` **바이트 동일** 골든 테스트 |
| 급증 조립 SQL이 방언 위반 | Wave 1 방언 그물이 잡지만 **애초에 맞게 낸다** — 엔진별 문자열 단언 테스트 |
| `build_stat_month_block` 기간 규칙 충돌 | 비교 모드에서 **미주입** 단언 테스트 |
| 시그니처 변경 재발 | 신규 함수만 추가하고 기존 시그니처는 **키워드 기본값**으로만 확장. 변경 시 함수명 repo 전체 grep(Known Mistakes 신규 항목) |

## 검증 체크포인트

| 시점 | 확인 |
|---|---|
| 각 태스크 | 해당 스위트 통과 + `arch_check --ci` |
| 각 Wave 완료 | 대상 영역 전체 재실행 → **failed ≤ 6**(기존분만) |
| 전체 완료 | 대상 영역 **6 failed 유지 + 신규분 전량 통과** · `overfit_check` 0 · 플래그 off 골든 |

## 산출물

- 코드: 위 파일 소유권 표 (신규 4 · 수정 6)
- 테스트: `tests/test_empty_answer/` · `tests/test_spike/` (선례 = `tests/test_middleware/`)
- 문서: `docs/02_decision.md` **D-176 후속1·후속2 등재** · `plans/82` 상태 갱신 · `plans/INDEX.md`
