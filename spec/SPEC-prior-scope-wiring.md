# Spec: prior-scope-wiring

> Module id: `prior-scope-wiring` | 근거: `plans/82` §2 · §7 Wave 3.5 | 예약 결정: **D-176**
> 계층: utils(`prior_targets`) + orchestration(`subagents`·`process_query`)

## ASSUMPTIONS I'M MAKING

1. **행별 출처가 없으면 현행 동작 그대로**다. `_source_db`가 없는 행은 호출부가 준 db_id로 폴백한다
   → `_source_db`를 만드는 경로(멀티 DB 병합)를 타지 않는 모든 기존 케이스는 **비트 동일**.
2. `_source_db`는 `_merge_results`가 붙이는 **내부 태그**다. 대상 해소에만 쓰고 사용자에게 노출하거나
   식별자 컬럼 후보로 취급하지 않는다.
3. `_resolve_db_id`의 새 우선순위는 **`prior_targets` > task.db_ids > previous_db_ids > 위치어**다.
   근거: 이번 턴 선행 결과가 직전 턴 승계보다 강한 신호다(요청 스코프 우선 — `plans/82` §7 Wave 3.5).
   단 **`task.db_ids`가 사용자 확정(존 선택 UI)에서 온 경우**는 여전히 최우선이어야 하므로,
   `prior_targets`는 **`task.db_ids`가 비어 있을 때만** 앞선다 → 실제 순서는
   `task.db_ids > prior_targets > previous_db_ids > 위치어`.
4. `COMPOSITE_PRIOR_TARGETS_ENABLED`가 off면 **아무것도 하지 않는다**(현행 기본값 off — 회귀 0).
5. `TargetRef` 스키마는 **변경하지 않는다**(`db_id` 필드가 이미 있다).

> ASSUMPTION 3은 §2.3의 원안(*"현행 ①②③보다 앞"*)을 **좁힌 것**이다 — 사용자가 존 선택 UI에서
> 확정한 `task.db_ids`를 선행 결과가 덮으면 D-143의 "UI 확정은 어떤 추론보다 우선" 원칙이 깨진다.

## Objective

**선행 조회 결과의 출처(어느 존에서 나온 행인가)를 후속 단계의 조회 대상으로 흘려보낸다.**

`plans/82` §2 실측 — 부품은 다 있고 배관 두 곳이 끊겨 있다:

| 있는 것 | 위치 |
|---|---|
| `TargetRef.db_id` 필드 | `src/utils/prior_targets.py:83-87` |
| 병합 행의 `_source_db` 태그 | `src/nodes/multi_db_executor.py:2126` |

| 끊긴 곳 | 증상 |
|---|---|
| ① `build_prior_targets`가 **행별 `_source_db`를 읽지 않고** 호출부가 준 db_id 하나를 전 대상에 도장 (`prior_targets.py:257-262`) | abd00이 공동존에 있어도 `TargetRef.db_id="polestar_b0"` |
| ② `_resolve_db_id`가 **`prior_targets`를 후보에 넣지 않는다** (`process_query.py:84-143`) | 앞 단계가 존을 정확히 찾아도 프로세스 조회가 그 값을 안 쓴다 |

**성공**: 팬아웃 결과에서 만들어진 대상들이 **행별로 올바른 존**을 갖고, 프로세스 조회가 그 존의
API를 호출한다.

## Tech Stack

기보유만 — 신규 라이브러리 0건. pydantic v2 · pytest.

## Commands

```bash
python -m pytest -q tests/test_composite/test_prior_scope_db_id.py \
                    tests/test_composite/test_target_fanout.py \
                    tests/test_orchestration/test_process_hostname_resolve.py
python scripts/arch_check.py --ci
```

## Project Structure

| 경로 | 이 모듈에서 |
|---|---|
| `src/utils/prior_targets.py` | 수정 — `build_prior_targets`가 행별 `_source_db` 우선 사용 |
| `src/orchestration/subagents.py` | 수정 — `_build_prior_targets_for_task`의 `db_ids[0]` 도장을 폴백으로 강등 |
| `src/orchestration/process_query.py` | 수정 — `_resolve_db_id` 우선순위에 `prior_targets` 추가 |
| `tests/test_composite/test_prior_scope_db_id.py` | **신규** |

> `process_query.py`는 동시 작업(plans/81)이 수정 중이다. **hunk 미겹침 실측 완료**
> (동시 hunk = `_resolve_hostname`·`_collect_one_target`·`_fanout`; 82 대상 = `_resolve_db_id` 84~143).

## Code Style

기존 `prior_targets.py`의 스타일 — 탈락 사유를 구조화해 남기고(`_drop`), 결정적 판정에 근거 결정
번호를 주석으로 단다.

```python
    # 행별 출처 우선(D-176 · plans/82 §2.2): 팬아웃 결과는 행마다 _source_db를 갖는다
    # (_merge_results가 부착). 호출부가 준 db_id 하나를 전 대상에 찍으면 abd00이 공동존에
    # 있어도 은행존으로 표기돼 후속 단계가 엉뚱한 존의 API를 친다.
    # 태그가 없으면 종전대로 호출부 db_id로 폴백한다 — 단일 DB 경로는 비트 동일.
    row_db = row.get(SOURCE_DB_KEY)
    ref_db = str(row_db) if row_db else db_id
```

## Testing Strategy

pytest · 전부 mock · LLM·네트워크 0.

| 케이스 | 기대 |
|---|---|
| 행에 `_source_db`가 b0/gp 혼재 | `TargetRef.db_id`가 **행별로 다르게** 매겨진다 |
| 행에 `_source_db` 없음 | 호출부 db_id 사용 — **현행과 동일** |
| `_source_db` 값이 빈 문자열/None | 폴백 — 빈 db_id를 만들지 않는다 |
| `_source_db`가 식별자 컬럼으로 오인되는가 | **아니오** — `_pick_identifier_column` 후보에서 제외 |
| `prior_targets_enabled=False` | `build_prior_targets` 동작 **비트 동일** |
| `_resolve_db_id`: task.db_ids 있음 + prior_targets 있음 | **task.db_ids 승**(UI 확정 우선 — D-143) |
| `_resolve_db_id`: task.db_ids 없음 + prior_targets 있음 | **prior_targets 승** |
| `_resolve_db_id`: prior_targets 없음 | 현행 3단 우선순위 **그대로** |
| `prior_targets`의 db_id가 base_url 미매핑 | base_url 있는 후보 우선 규칙 유지 |

## Boundaries

**Always** — 플래그 off면 비트 동일 · `TargetRef` 스키마 불변 · UI 확정(`task.db_ids`) 최우선 유지
**Ask first** — `_resolve_db_id` 우선순위를 ASSUMPTION 3과 다르게 · `TargetRef` 필드 추가
**Never** — `_source_db`를 사용자 응답·CSV에 새로 노출 · `_collect_one_target`/`_fanout` 수정(동시 작업 영역)

## Success Criteria

1. `build_prior_targets`가 행의 `_source_db`를 **행별 db_id 정본**으로 쓰고, 없으면 호출부 값으로 폴백한다.
2. `_source_db`가 **식별자 컬럼 후보에서 배제**된다(대상 값으로 오인되지 않는다).
3. `_build_prior_targets_for_task`의 `db_ids[0]` 도장이 **폴백으로 강등**된다.
4. `_resolve_db_id`가 `task.db_ids > prior_targets > previous_db_ids > 위치어` 순으로 판정한다.
5. `COMPOSITE_PRIOR_TARGETS_ENABLED=false`에서 **모든 동작이 비트 동일**하다.
6. 신규 테스트 통과 · 기존 `test_target_fanout`·`test_process_hostname_resolve` **회귀 0**.

## Open Questions

없음 — ASSUMPTION 3(우선순위 좁힘)이 확정되면 진행.
