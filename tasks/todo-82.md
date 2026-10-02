# 태스크 목록 — Plan 82 1차 (실행 그룹 기반)

> 계획: `tasks/plan-82.md` · 지도: `CAPABILITY-MAP-execution-groups.md`
> 기준선(2026-08-28):
> - 공유 트리(동시 작업 `plans/81`·`83` 포함) 대상 영역 **1042 passed / 1 skipped / 0 failed**
> - **격리 worktree(순수 HEAD)** 같은 세트 **964 passed** — 차이 78건은 `tests/test_composite/`의
>   `plans/81` 신규 테스트다. **모집단이 달라 두 수치를 직접 비교하지 않는다.**
> - 판정 기준: 격리 **964 → 1006**(내 신규 42건) · 실패 0

## M1 — multi-dialect-guard (`SPEC-multi-dialect-guard.md`)

- [x] **T1** 방언 판정 순수 함수
  - Acceptance: `_check_row_limit_dialect(sql, db_engine)` — DB2+`LIMIT n` → 거부 사유(문자열), DB2+`FETCH FIRST` → None, PG+둘 다 → None, 행 제한 절 부재 → None, 미지 엔진 → None. 서브쿼리 내부 `LIMIT`은 최상위 판정에서 제외(`_strip_parenthesized` 재사용)
  - Verify: `pytest tests/test_nodes/test_multi_db_dialect.py -q` · `arch_check --ci`
  - Files: `src/nodes/multi_db_executor.py`, `tests/test_nodes/test_multi_db_dialect.py`

- [x] **T2** `_validate_sql_simple` 배선
  - Acceptance: `db_engine` 인자를 받아 T1 판정을 수행. **기존 검사 항목 전부 보존**. `db_engine` 미전달 시 현행 동작 동일
  - Verify: `pytest tests/test_nodes/test_multi_db_dialect.py tests/test_nodes/test_multi_db_merge.py tests/test_nodes/test_multi_db_recovery.py -q`
  - Files: `src/nodes/multi_db_executor.py`

- [x] **T3** 실행 오류 후 재생성 1회
  - Acceptance: `_run_single_target`에서 SQL **문법 오류** 계열 실행 실패 시 에러 컨텍스트를 실어 재생성 1회 → 재실행. 연결/타임아웃은 재생성 안 함. 재생성 실패 시 `db_errors`에 원 에러 + 재생성 사실 기록
  - Verify: `pytest tests/test_nodes/test_multi_db_dialect.py -q -k regen`
  - Files: `src/nodes/multi_db_executor.py`, `tests/test_nodes/test_multi_db_dialect.py`

## M2 — group-registry (`SPEC-group-registry.md`)

- [x] **T4** 레지스트리 축 선언
  - Acceptance: `config/db_registry.yaml`에 `solutions`(polestar 1건: order 10 · backend sql · capabilities) · `zone_groups`(bank query_order 10 / common 20) 추가. **기존 키 무변경**
  - Verify: `pytest tests/test_semantic_routing/test_registry_config.py -q` · YAML 로드 스모크
  - Files: `config/db_registry.yaml`

- [x] **T5** 파생 API
  - Acceptance: `registry.solutions()` · `registry.zone_groups()`(**query_order 정렬**) · `registry.zone_group_of(db_id)`. 정본은 YAML, 코드 리터럴 0
  - Verify: `pytest tests/test_semantic_routing/test_registry_config.py tests/test_orchestration/test_execution_groups.py -q` · `overfit_check`
  - Files: `src/routing/registry.py`, `tests/test_orchestration/test_execution_groups.py`

- [x] **T6** `partition_execution_groups` + `ZONE_CLARIFY_OPTIONS` 파생화
  - Acceptance: 분할기가 SPEC 표대로 동작(**입력 순서 불변** · 미등재 무시). `ZONE_CLARIFY_OPTIONS` 값이 현행과 **완전 동일**(골든)
  - Verify: `pytest tests/test_orchestration/test_execution_groups.py tests/test_orchestration/test_zone_group_exclusive.py tests/test_orchestration/test_zone_post_gate.py -q`
  - Files: `src/utils/query_gen_common.py`, `tests/test_orchestration/test_execution_groups.py`

## M3 — prior-scope-wiring (`SPEC-prior-scope-wiring.md`)

- [x] **T7** 행별 출처 → `TargetRef.db_id`
  - Acceptance: `build_prior_targets`가 행의 `_source_db`를 우선 사용, 없으면 호출부 db_id 폴백. `_source_db`는 **식별자 컬럼 후보에서 배제**. 빈 값은 폴백
  - Verify: `pytest tests/test_composite/test_prior_scope_db_id.py tests/test_composite/test_target_fanout.py -q`
  - Files: `src/utils/prior_targets.py`, `tests/test_composite/test_prior_scope_db_id.py`

- [x] **T8** 호출부 도장 강등
  - Acceptance: `_build_prior_targets_for_task`의 `db_ids[0]`가 **폴백**으로만 쓰인다. `prior_targets_enabled=False`면 비트 동일
  - Verify: `pytest tests/test_composite -q`
  - Files: `src/orchestration/subagents.py`

- [x] **T9** `_resolve_db_id` 우선순위
  - Acceptance: `task.db_ids > prior_targets > previous_db_ids > 위치어`. `prior_targets` 없으면 현행 3단 그대로. base_url 우선 규칙 유지
  - Verify: `pytest tests/test_composite/test_prior_scope_db_id.py tests/test_orchestration/test_process_hostname_resolve.py -q`
  - Files: `src/orchestration/process_query.py`, `tests/test_composite/test_prior_scope_db_id.py`

## M4 — group-runner (`SPEC-group-runner.md`) — **M1·M2 완료 후**

- [x] **T10** State 필드
  - Acceptance: `execution_groups`·`group_results`·`group_packets` 추가(전부 Optional, 기본 None). `create_initial_state`에 초기화
  - Verify: `pytest tests/test_nodes/test_multi_db_group_loop.py -q` · `arch_check --ci`
  - Files: `src/state.py`

- [x] **T11** 그룹 루프 + 소급 복구 이동
  - Acceptance: `execution_groups` 미설정 시 **반환 키·`_run_single_target` 호출 순서 현행 동일**(골든). 2개면 `query_order` 순차. 실패 격리. D-153 복구가 그룹 내부에서 발동
  - Verify: `pytest tests/test_nodes/test_multi_db_group_loop.py tests/test_nodes/test_multi_db_merge.py tests/test_nodes/test_multi_db_recovery.py -q`
  - Files: `src/nodes/multi_db_executor.py`, `tests/test_nodes/test_multi_db_group_loop.py`

- [x] **T12** 그룹 결과·부분 결과 수집
  - Acceptance: `group_results[key]`에 `row_count`·`elapsed_ms`·`errors`·`sqls`. `group_packets`는 **peer만** 적재
  - Verify: `pytest tests/test_nodes/test_multi_db_group_loop.py -q -k packet`
  - Files: `src/nodes/multi_db_executor.py`

- [x] **T13** `result_merger` 요약 승격
  - Acceptance: 종전 생성 즉시 버려지던 `db_result_summary`가 반환 dict에 실린다. 기존 키 무변경
  - Verify: `pytest tests/test_nodes/test_multi_db_merge.py -q`
  - Files: `src/nodes/result_merger.py`

- [x] **T14** 그룹 계측 (O 계층)
  - Acceptance: `src/observability/group_metrics.py` — `(solution, zone_group, kind, backend)`별 p50/p90 + **표본 수**. 표본 <20이면 호출부가 시간 문구를 생략할 수 있게 `sample_size` 노출
  - Verify: `pytest tests/test_observability/test_group_metrics.py -q` · `arch_check --ci`
  - Files: `src/observability/group_metrics.py`, `tests/test_observability/test_group_metrics.py`

## 마감

- [x] **T15** 격리 검증 + 문서
  - Acceptance: 격리 worktree에서 **자기 파일만** 얹어 1차 모듈 영역 재실행 → 1042 + 신규분, 실패 0. `overfit_check` 0. `docs/02_decision.md`에 **D-176 등재**(1차 범위). `plans/82` 상태·`plans/INDEX.md` 갱신
  - Verify: 위 명령 전부 + `arch_check --ci`
  - Files: `docs/02_decision.md`, `plans/82-*.md`, `plans/INDEX.md`
