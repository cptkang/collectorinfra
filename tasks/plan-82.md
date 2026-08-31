# 구현 계획 — Plan 82 1차 (실행 그룹 기반)

> 지도: `CAPABILITY-MAP-execution-groups.md` (승인 2026-08-28) · 계획: `plans/82` v4 · 예약 결정 **D-176**
> SPEC: `SPEC-multi-dialect-guard.md` · `SPEC-group-registry.md` · `SPEC-prior-scope-wiring.md` · `SPEC-group-runner.md`
> 태스크: `tasks/todo-82.md`

## 범위 (사용자 승인)

**1차 4개 모듈만.** `group-artifacts`·`host-discovery`·`group-ui`·`scope-select`·`solution-pipeline`은
`plans/82` §13의 U1~U13 확정 후 별도 진행한다.

## 기준선 (착수 전 실측 2026-08-28)

| 대상 | 값 |
|---|---|
| 전체 스위트 (`--ignore=tests/e2e`) | **35 failed · 4960 passed · 54 skipped · 24 errors** (공유 트리 — 동시 작업 포함) |
| **1차 모듈 영역** (`test_orchestration`·`test_semantic_routing`·`test_composite`·`test_utils`·`test_routing`·멀티DB 3종) | 공유 트리 **1042 passed · 1 skipped · 0 failed** / **격리 순수 HEAD 964 passed** ← **회귀 판정은 격리 수치로** |
| `arch_check --ci` | exit 0 |

**전체 스위트의 35 failed는 이 작업 착수 전부터 존재**하며 동시 작업(plans/81·83 미커밋)을 포함한
공유 트리의 상태다.

> **⚠ 착수 후 실측으로 드러난 함정**: 위 1042는 **공유 트리** 수치이고, 그 안에는
> `tests/test_composite/`에 있는 `plans/81`의 신규 테스트 **78건**이 섞여 있다. 격리 worktree
> (순수 HEAD)에서 같은 세트를 돌리면 **964**다. **두 수치를 직접 비교하면 내 변경이 테스트를
> 줄인 것처럼 보인다** — 모집단이 다르기 때문이다.
> **판정 기준은 격리 worktree 964 → 964 + 내 신규분**이며, 공유 트리는 "실패 수가 늘지 않음"만 본다.

## 의존 순서와 병렬 가능성

```
독립 (병렬 가능)
  M1 multi-dialect-guard   ─┐
  M3 prior-scope-wiring    ─┤
  M2 group-registry        ─┴─→ M4 group-runner   (M2의 partition_execution_groups를 소비)
```

- **M1·M2·M3는 서로 파일이 겹치지 않는다** → 순서 무관.
- **M4는 M2 이후**여야 한다 — 그룹 축 파생 API를 쓴다.
- 실제 착수는 **M1 → M2 → M3 → M4** 순(위험이 낮은 것부터, 각 단계 회귀 확인).

## 파일 소유권 (모듈 간 충돌 방지)

| 파일 | 소유 모듈 | 비고 |
|---|---|---|
| `src/nodes/multi_db_executor.py` | **M1**(검증·재생성) · **M4**(그룹 루프) | **두 모듈이 같은 파일** — M1을 먼저 끝내고 M4 착수 |
| `src/utils/prior_targets.py` | M3 | |
| `src/orchestration/subagents.py` · `process_query.py` | M3 | `process_query`는 동시 작업 hunk 미겹침 실측 완료 |
| `config/db_registry.yaml` · `src/routing/registry.py` | M2 | |
| `src/utils/query_gen_common.py` | M2 | |
| `src/state.py` · `src/nodes/result_merger.py` · `src/observability/group_metrics.py` | M4 | |
| `src/config.py` | **아무도 안 건드림** | 1차 범위에 신규 플래그 없음 → 동시 작업 `CompositeConfig` 충돌 원천 회피 |

## 위험과 완화

| 위험 | 완화 |
|---|---|
| **동시 작업과의 회귀 오귀속** | 검증을 **격리 worktree**에서 자기 파일만 얹어 수행(`git worktree add … HEAD`) |
| `multi_db_executor.py`를 M1·M4가 공유 | M1 완료·검증 후 M4 착수(순차). 두 변경 지점이 다름(`_validate_sql_simple` vs `multi_db_executor` 본체) |
| 그룹 루프가 기존 동작을 바꿈 | **골든 테스트** — 그룹 미설정 시 반환 키·호출 순서 동일 단언 |
| 방언 검사 위양성 | PostgreSQL의 `FETCH FIRST`도 **통과**시킨다(표준 문법). 행 제한 절 **부재는 강제하지 않는다** |
| 계층 위반 | `arch_check --ci`를 매 태스크 검증에 포함. 부분 결과는 state 적재(방출은 라우트 — 2차) |

## 검증 체크포인트

| 시점 | 확인 |
|---|---|
| 각 태스크 완료 | 해당 스위트 통과 + `arch_check --ci` |
| 각 모듈 완료 | 1차 모듈 영역 전체 재실행 → 공유 트리 **실패 0** |
| 1차 전체 완료 | **격리 worktree(순수 HEAD + 자기 파일만) 964 → 1006 · 실패 0** · `overfit_check` · 전체 스위트 실패 수 **≤35** |

## 산출물

- 코드: 위 파일 소유권 표
- 테스트: 신규 4개 파일
- 문서: `docs/02_decision.md`에 **D-176 등재**(1차 범위 반영) · `plans/82` 상태 갱신
