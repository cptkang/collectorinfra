# Todo 88 — 복합 질의 순차 의존 처리 (1차)

> `tasks/plan-88.md` · 모듈 id는 `CAPABILITY-MAP-88.md` 기준

## `prior-dependency-gate`

- [x] **T1. `assess_prior_dependency` + `DependencyVerdict` + 테스트**
  - Acceptance: `SPEC-prior-dependency-gate.md` 판정 표 전부 · LLM 0회 · utils가 orchestration을 import하지 않음
  - Verify: `pytest tests/test_composite/test_sequential_gate.py` · `arch_check --ci`
  - Files: `src/utils/prior_dependency.py`(신규) · `tests/test_composite/test_sequential_gate.py`(신규)
- [x] **T2. 배선 — 2단 `agent_orchestrator` · 1단 `deepagents_tools` · 플래그 · UI 배지**
  - Acceptance: 플래그 on → 후속 handler 미호출·`status="skipped"`·결과 `error`+`skip_reason` / off → 현행 동일 / 1단·2단 같은 함수
  - Verify: 같은 테스트 파일의 배선 케이스 + `test_orchestrator` · `test_prior_targets_wiring` · `test_deep_agent` 회귀 0
  - Files: `src/config.py` · `src/orchestration/agent_orchestrator.py` · `src/orchestration/deepagents_tools.py` · `src/static/js/app.js`

## `dependency-notes`

- [x] **T3. 채널 · 렌더 · 집계기 · deep_agent · 라우트 · 충족도 병기**
  - Acceptance: `SPEC-dependency-notes.md` 성공 기준 1~5 · 노트 0건이면 응답 바이트 동일 · 충족도 미달 사유가 본문에 나타남
  - Verify: `pytest tests/test_composite/test_dependency_notes.py tests/test_state.py tests/test_api tests/test_orchestration`
  - Files: `src/state.py` · `src/utils/prior_dependency.py` · `src/orchestration/{agent_orchestrator,result_aggregator,deep_agent,deepagents_tools}.py` · `src/api/routes/query.py`

## `scope-postcheck`

- [x] **T4. `assess_scope_conformance` + 2단·1단 배선**
  - Acceptance: `SPEC-scope-postcheck.md` 표 · outside 제거는 `query_results`·`organized_data.rows` 둘 다 · 종류 불일치면 미제거
  - Verify: `pytest tests/test_composite/test_scope_postcheck.py tests/test_orchestration`
  - Files: `src/utils/prior_dependency.py` · `src/config.py` · `src/orchestration/{agent_orchestrator,deepagents_tools}.py`

## `prior-scope-by-db`

- [x] **T5. DB별 분할 + `multi_db_executor` 선택·미조회 + 노트**
  - Acceptance: `SPEC-prior-scope-by-db.md` 표 · `_generate_sql` DB별 인자 분리(mock 단언) · `_source_db` 미노출 골든
  - Verify: `pytest tests/test_nodes/test_prior_scope_by_db.py tests/test_orchestration/test_prior_rows_scope.py tests/test_nodes/test_multi_db_group_loop.py tests/test_composite/test_prior_scope_db_id.py`
  - Files: `src/orchestration/subagents.py` · `src/utils/query_gen_common.py` · `src/nodes/prompt_blocks.py` · `src/nodes/multi_db_executor.py` · `src/config.py`

## 문서

- [x] **T6. 상태 갱신**
  - Acceptance: `plans/88` `-TODO`→`-WIP`(참조 링크 동반) · INDEX 행 · D-203 본문 등재 · 지도 상태 · `overfit_check --ci` 0 · 격리 worktree 전체 스위트
  - Files: `plans/88-*.md` · `plans/INDEX.md` · `docs/02_decision.md` · `CAPABILITY-MAP-88.md`

## 2차 (2026-09-09 재개 · §8 권고안 가정)

- [x] **T7. `validate_plan_dag` + `_enforce_plan_contract` 되먹임 1회** (`plan-dag-validation`)
  - Files: `src/orchestration/schemas.py` · `src/orchestration/intent_planner.py` · `src/config.py` · `tests/test_orchestration/test_plan_dag_validation.py`
- [x] **T8. `has_sequential_marker` + 표지 단일 계획 재분해(예산 공유) + `decompose` 노트** (`sequential-decompose` — 프롬프트 무변경)
  - Files: `src/utils/prior_dependency.py` · `src/orchestration/intent_planner.py`
- [x] **T9. `sequential_runner` 3·4단 노드 + 진입 판정 + 그래프 배선** (`sequential-fallback-runner`)
  - Files: `src/orchestration/sequential_runner.py`(신규) · `src/graph.py` · `src/orchestration/__init__.py` · `tests/test_orchestration/test_sequential_runner.py`
- [x] **T10. 분해 골든셋 + `eval_routing.py --decomposition`** (`sequential-eval` — dry-run만)
  - Files: `testdata/routing_gold/decomposition.yaml` · `scripts/eval_routing.py`
- [x] **T11. 설정 도움말 3건 + 카탈로그 개수(327 실측 · 병행 세션이 반영)**
- [x] **T12. `.env` parity(G-3)** — 2026-09-10 확정·반영: `TEXT2SQL_PATH_PARITY=false` 명시 + 1차 3종 on · 2차 3종 off(§11.1). on 전환은 82 2차 착수 조건으로 이관
- [ ] **T13. 실 LLM 분해 평가**(코드 잔여 아님 · 과금 평가) — D-127 승인 후 `RUN_E2E=1 … --decomposition`(§11.4 단계 7)
