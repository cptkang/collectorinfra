# Todo 90 — 채팅창 폴스타(존) 스코프 표시·선택·해제

> `tasks/plan-90.md` · 모듈 id는 `CAPABILITY-MAP-90.md` 기준

## `deep-agent-selected-db`

- [x] **T0. 1단 결손 고정 테스트** — `_extract_ambient_state`에 `selected_db_ids`가 통과하는지(현행 실패)
  - Acceptance: 실패하는 테스트 1건 → T2 후 통과
  - Verify: `pytest tests/test_orchestration/test_deep_agent_wiring.py -q`
  - Files: `tests/test_orchestration/test_deep_agent_wiring.py`
- [x] **T2. `_AMBIENT_KEYS += "selected_db_ids"`**
  - Files: `src/orchestration/deep_agent.py`

## `db-scope-resolve`

- [x] **T1. `src/routing/db_scope.py` 신설 + `context_resolver` 위임**
  - Acceptance: SPEC 성공 기준 1·2 · `_extract_previous_db_ids` 동작 동일(기존 테스트 무변화)
  - Verify: `pytest tests/test_routing/test_db_scope.py tests/test_multiturn/test_context_resolver.py -q`
  - Files: `src/routing/db_scope.py` · `src/nodes/context_resolver.py` · `tests/test_routing/test_db_scope.py`

## `db-scope-source`

- [x] **T1b. `db_scope_source` 신호** — state 필드 · semantic_router 확정 지점 · subagents `db_origin` · `_collect_db_promotion` 승격
  - Acceptance: SPEC 성공 기준 3 · `target_databases` shape 불변
  - Verify: `pytest tests/test_orchestration/test_result_aggregator.py tests/test_routing/test_db_scope.py -q`
  - Files: `src/state.py` · `src/routing/semantic_router.py` · `src/orchestration/subagents.py` · `src/orchestration/result_aggregator.py`

## `db-scope-reset`

- [x] **T4. `reset_db_scope` 3지점** — `create_followup_input` · `context_resolver` sticky 차단 · pre-gate(reset 턴 한정)
  - Acceptance: SPEC 성공 기준 4 · 기본값에서 델타 바이트 동일
  - Verify: `pytest tests/test_multiturn/test_db_scope_reset.py tests/test_orchestration/test_zone_selection.py -q`
  - Files: `src/state.py` · `src/nodes/context_resolver.py` · `src/api/routes/query.py` · `tests/test_multiturn/test_db_scope_reset.py`

## `db-scope-contract`

- [x] **T3. 스키마 · 4경로 `db_scope` · `GET /scope/options`**
  - Acceptance: SPEC 성공 기준 6 · `QueryResponse(**response_data)` 통과
  - Verify: `pytest tests/test_api/test_db_scope_contract.py -q`
  - Files: `src/api/schemas.py` · `src/api/routes/query.py` · `src/api/routes/scope.py` · `src/api/server.py` · `tests/test_api/test_db_scope_contract.py`

## `scope-chip-ui`

- [x] **T5. 칩 마크업·CSS·JS 배선**
  - Acceptance: `SPEC-scope-chip-ui.md` 성공 기준 1~7
  - Verify: `node --check src/static/js/app.js` · `pytest tests/test_api/test_ui_zone_scope.py tests/test_api/test_ui_scope_select.py -q`
  - Files: `src/static/index.html` · `src/static/js/app.js` · `src/static/css/style.css` · `tests/test_api/test_ui_zone_scope.py`

## 문서

- [x] **T6. D-205 본문 등재 · D-143 후속4 부기 · INDEX 90행 갱신·`-TODO` 해제 · 계획서 상태 · 채번 안내 라인**
- [ ] **T7. 실 브라우저·실 파이프라인 3턴 시나리오(과금 — 승인 대기)**

## v3 — 존 동시 선택·순차 조회 개방 (D-206)

- [x] **T8. `.env` 옵트아웃 + 실행기 자동 그룹 분할 + 혼합 승계 + `zone_groups` + 문구 + 처리현황 그룹 경과**
  - Verify: `pytest tests/test_nodes/test_multi_db_auto_groups.py tests/test_multiturn/test_mixed_zone_succession.py -q`
- [ ] **T9. 실 브라우저·실 파이프라인 — 은행존+공동존 동시 선택 → 순차 실행 확인(과금 — 승인 대기)**
