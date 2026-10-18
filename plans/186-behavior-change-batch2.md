# 186. 행동 변경 후보 2차 — 확인 필요 항목 판정 처리 (plans/182 잔여)

> **작성일**: 2026-10-17 · **v1.1**
> **상태**: **완료**(2026-10-17 · 커밋 전) — 고침 6(M7·K5·J4·P3·J7·L4) · 유지 3(S5·M4·K4) · 계획 끝 회귀 실패 1 = 원래 실패(모듈 단위) · security-auditor Critical·High·Medium 0 · verifier Critical·Major 0 · Minor 1 교정 · D-338 신규 · 매뉴얼 user.md·admin.md
> **상위**: `plans/182` §1 「확인 필요」 S5·M4·M7·K4·K5·J4·P3 · 새 후보 L4·J7. 판정은 메인이 실측해 확정했다(사용자 지시 2026-10-17 「확인할 사항은 직접 확인하고 다음 작업을 진행하라」).
> **성격**: 행동 변경(D-337 ⑥ — 구조 리팩터와 분리). 항목마다 바뀐 동작을 단언하는 테스트를 추가하고, 옛 동작을 단언하던 기존 테스트는 그 단언만 고친다(§3 목록).
> **계획 시작 SHA**: `02c6729a`(RCA · 작업 트리 깨끗 — 루트 `Claude outputs/`는 피어 산출물)
> **회귀 대상 파일(누적)**: 제품 18(`src/` 11 · 정적·매뉴얼 산출 7) + 매뉴얼 원천 2 + 테스트 18 = 계획 끝 회귀 `--files` 38개(§4) · 교정 뒤 `tests/test_nodes/test_semantic_router_mapping.py`·`tests/test_xls_plan_integration.py`·`tests/test_plan186_verify.py`
> **관련 결정**: D-338(신규 — L4) · D-337 ⑥ · D-070(사용자/운영자 토큰 · break-glass) · D-066(단일/멀티 대칭) · D-153(예외 원문 축약) · D-255(매뉴얼 동반) · D-303(회귀)
> **범위 밖**: `src/schema_cache/description_generator.py:38`의 전역 `load_config()` 소유 판정(J4와 같은 꼴 — 기록만) · 182 「유지」 항목 · L3

---

## 1. 판정과 수용 기준

### 1.1 고침 6건

| ID | 위치 | 수용 기준 |
|---|---|---|
| M7 | `src/nodes/multi_db_executor.py` 재생성 중단 기록(`_stop_detail` · `_record_regen_stop`) → `src/orchestration/result_aggregator.py` `_failure_disclosure` | regen_stop detail을 「SQL 검증 실패: {원문}」 머리말 꼴로 기록(이미 머리말이면 다시 붙이지 않음) → 고지에 「(마지막 사유: SQL 검증 실패)」만 실림 · 원문 미노출(163 S3+) · kind 판정 불변 |
| K5 | `src/orchestration/replanner.py` `_ReplanCtx.stop` | `plan_validation`이 노트를 남긴 뒤 뒤 필터가 `ctx.stop()`으로 끝나도 `dependency_notes=ctx.replan_notes` 보존 · 호출부가 넘긴 dependency_notes는 그대로 존중 |
| J4 | `src/routing/semantic_router.py` `_ownership_enabled` 호출 4곳 | 정본 `capability_ownership_on(app_config)`(`src/routing/capability_ownership.py`)로 교체 · `_ownership_enabled` 제거 · 운영(설정 1벌) 동작 동일 |
| P3 | `src/nodes/schema_analyzer.py` `_load_manual_profile` | 경로+mtime(+size) 키 캐시 · 반환은 깊은 복사 · 파일이 바뀌면 다시 읽음 · 함수 이름 유지(patch 테스트 무변경) · 테스트 간 누수 방지 장치 |
| J7 | `src/nodes/multi_db_executor.py` `_split_mapping_for_prompt`(`filter_mapping_by_schema`) | 스키마 필터에서 빠진 사용률 통계 매핑 필드를 회수 후보(`select_metric_recovery_fields`)로 넘김 — 단일(185 W1 뒤)과 대칭 · 단일 불변 |
| L4 | `src/api/dependencies.py` payload-only 폴백 3곳(`get_current_user` · `resolve_stream_user` · `require_user`) | AUTH_ENABLED=true + `user_repo` None → 503 고정 문구 + warning 로그(fail-closed) · break-glass(`_try_break_glass_admin`) 경로 유지 · AUTH_ENABLED=false는 ANONYMOUS_USER 그대로 · 프론트 503 처리에 로그인 반복 루프 없음(정적 확인) · security-auditor 필수 |

### 1.2 유지 3건(코드 변경 없음 — 182에 근거 기록)

| ID | 근거 |
|---|---|
| S5 | `cache_management`의 `error_message`는 내부 상태다. 출구(graph error_response `user_error_text` · progress `user_error_text` · stream_failure `_FAILED_DETAIL` · cache_management → END의 final_response 고정 문구)가 모두 거른다. 다른 노드(schema_analyzer · query_executor)도 원문을 error_message에 담는 같은 설계 |
| M4 | `load_config`는 `lru_cache`라 실패는 설정을 적재할 수 없는 상태에서만 반복되고, 그 상태면 기동이 먼저 실패한다. 반복 경고는 그 상태의 관측 신호로 둔다 |
| K4 | 출력 노드가 OutputState 밖에서 읽는 14키는 3·4단 그래프 경로 입력이다. 2단은 집계기가 턴 단위로 붙이거나(scope_narrowed·turn_disclosures·dependency_notes·conversation_context) 다른 키로 싣거나(executed_sqls·selected_db_ids/target_databases) 의도적으로 넘기지 않거나(resolved_limit — W-1 ⑥) 검증 전용(csv_sheet_data)이다 |

## 2. Wave

| Wave | 담당 | 항목 | 파일 |
|---|---|---|---|
| 1-A | implementer | M7 · J7 | `src/nodes/multi_db_executor.py` + 새 테스트 |
| 1-B | implementer | K5 · J4 · P3 | `src/orchestration/replanner.py` · `src/routing/semantic_router.py` · `src/nodes/schema_analyzer.py` + 새 테스트 |
| 1-C | implementer | L4 | `src/api/dependencies.py` (+ 프론트 정적 확인 · 매뉴얼 해당 시) + 새 테스트 |
| 2 | verifier + security-auditor | 전 항목 | 독립 검증 · L4 보안 감사 |

## 3. 처리 결과 · 고친 기존 단언

### 3.1 항목별 새 동작

| ID | 새 동작 | 바뀐 제품 파일 |
|---|---|---|
| M7 | 재생성 중단 detail을 「SQL 검증 실패: …」 머리말 꼴로 기록(`user_error.with_validation_head` — 멀티 `_generate_validated_sql` · 단일 `subagents._stop_regen` NON_SQL·VALIDATION_BUDGET). 출구는 `safe_stop_detail_text`(2단 집계 detail 「db_id: 사유 / …」를 조각마다 `safe_error_text` · 통과한 머리말만 중복 없이 「 · 」 이음 · 나머지 버림)를 `result_aggregator._failure_disclosure`·`replanner._regen_stop_notice`가 쓴다 → 「(마지막 사유: SQL 검증 실패)」 · 원문 미노출. 멀티 실행 오류 뒤 시간 게이트 detail은 「SQL 실행 에러: …」 | `src/utils/user_error.py` · `src/nodes/multi_db_executor.py` · `src/orchestration/subagents.py` · `src/orchestration/result_aggregator.py` · `src/orchestration/replanner.py` |
| K5 | `_ReplanCtx.stop`이 `plan_notes`가 있고 호출부가 `dependency_notes`를 넘기지 않으면 `replan_notes`를 싣는다 — 뒤 필터 종료에도 계획 검증 노트 보존 | `src/orchestration/replanner.py` |
| J4 | `_ownership_enabled` 제거 → `capability_ownership_on(app_config)`. 비공개 4함수(`_llm_classify`·`_llm_classify_two_stage`·`_build_router_prompt`·`_ownership_prompt_slots`)에 `app_config=None` 키워드(None이면 `load_config()`) · 2단 `subagents.classify_dbs`도 app_config 전달(실측으로 범위 확대 — 메인 판정) | `src/routing/semantic_router.py` · `src/orchestration/subagents.py` |
| P3 | `_load_manual_profile`이 (절대 경로, `st_mtime_ns`, `st_size`) 키로 파싱 결과 캐시 · deepcopy 반환 · 실패 미캐시. polestar 25.7ms → 0.2ms | `src/nodes/schema_analyzer.py` |
| J7 | 멀티 `_split_mapping_for_prompt`가 스키마 필터에서 빠진 성능지표 매핑 필드명을 4번째로 반환 → `_multi_resource_pivot_result`의 `select_metric_recovery_fields` 후보(단일과 대칭 · 단일 불변) | `src/nodes/multi_db_executor.py` |
| L4 | 인증 활성 + `user_repo` None → 사용자 토큰 3곳 503 「인증 서비스를 사용할 수 없습니다. 관리자에게 문의하세요.」(로그인 503 문구와 통일 — 지시 예시 문구에서 이탈 · 메인 수용) + warning(경로만). 순서: 비활성 ANONYMOUS → 토큰 없음 종전 → 무효 401 → 유효+저장소 부재 503. break-glass 유지. `_init_auth_db` 풀 생성 전 실패 + 인증 활성 경고 문구 정정. 프론트: app.js 503이면 리다이렉트·알림 구독 없이 문구(v26) · admin.js 503이면 토큰 유지(v12) | `src/api/dependencies.py` · `src/api/server.py` · `src/static/js/app.js` · `src/static/js/admin.js` · `src/static/index.html` · `src/static/admin/dashboard.html` |

### 3.2 고친 기존 단언(행동 변경분만)

- M7: `tests/test_nodes/test_plan139_table_selection.py`(멀티 토큰 한도 detail에 머리말) · `tests/test_orchestration/test_plan119_sql_loop.py::TestMultiDeadline::test_regeneration_skipped_when_last_generation_does_not_fit` · `::TestSingleNonSqlBudget::test_prose_chain_stops_after_two_generations`(detail 머리말)
- J4: `tests/test_semantic_routing/test_router_hint_pinning.py`(`_config`·`_route` 설정 주입 · `test_reused_entry_is_not_marked_user_specified_for_ownership`) · `tests/test_orchestration/test_plan146_demonstrative_zero_rows.py::_route_zone` · verifier Minor 교정: `tests/test_nodes/test_semantic_router_mapping.py::_make_config`·`tests/test_xls_plan_integration.py::TestPipelineConnection::test_semantic_router_falls_back_without_mapping`(MagicMock 설정이면 소유가 on으로 잡혀 off 검증이 사라짐 → `router.capability_ownership_enabled = False` 명시)
- L4(인증 활성 + 저장소 없는 앱으로 payload 통과에 기대던 픽스처 → 가짜 `user_repo` 주입 · 단언 약화 없음): `tests/test_api/` `test_alarm_zone_rbac` · `test_noise_dashboard_api` · `test_plan104_admin_permissions_ui` · `test_plan104_db_structure_api` · `test_plan125_source_permissions` · `test_plan139_table_definitions_api` · `test_plan160_chart_api` · `test_plan160_explain_api` · `test_plan160_verify` · `test_plan163_s2_user_token_type` · `test_query_result_access` · 캐시 버전 `test_plan160_static_assets`(app.js v26) · 경고 문구 `test_plan184_security_lifecycle::test_auth_db_pool_failure_keeps_old_warning[True]`
- 스냅샷 갱신 0

### 3.3 새 테스트

`tests/test_nodes/test_plan186_multi_regen_metric.py`(32 · M7·J7) · `tests/test_plan186_k5_j4_p3.py`(21) · `tests/test_api/test_plan186_auth_fail_closed.py`(33) · verifier `tests/test_plan186_verify.py`(31)

### 3.4 잔여(기록만)

- 단일 BACKEND_LIMIT detail은 `LLMBackendError.summary`(머리말 아님)라 고지 사유가 생략된다(현행) — 멀티는 「SQL 검증 실패: LLM 백엔드 …」라 비대칭. 맞추려면 summary를 고정 사유로 허용할지 판정 필요
- `semantic_router`의 two_stage·unknown 플래그 전역 읽기(약 :927·:1506) · `src/schema_cache/description_generator.py:38` 소유 판정 전역 읽기 — J4와 같은 꼴, 범위 밖
- 「SQL 검증 실패 · SQL 실행 에러」처럼 DB마다 머리말이 다르면 복수 머리말이 이어진다(의도 · 원문 없음)
- regen_stop이 없는 멀티 실패 요약은 `db_error_text` 경유로 검증 사유(SQL 조각 포함)를 싣는다 — D-153 후속2(폐쇄망 자가 진단) 의도된 설계 · 재검토는 별도 결정(182 §3.1 메모)
- `_first_error`가 이미 「SQL 실행 에러」 머리말이면 detail에 머리말이 겹친다(화면은 머리말 1개 · 무해)
- P3: 같은 `st_mtime_ns`·같은 크기로 다시 쓰면 캐시가 낡을 수 있다(나노초 해상도라 실무 영향 없음)
- J7: 회수 후보 순서가 미매핑 → 통계 매핑 → 스키마 탈락 통계 매핑 → llm_inferred라 단일과 SELECT 순서가 다를 수 있다(값 동일)
- security-auditor Low: 사용자 알림 SSE(`src/api/routes/alarm.py`)는 break-glass 미수용(기존 의도) · app.js 503 전용 처리는 기동 인증 확인에만(이후 503은 일반 오류 경로 · 토큰 유지)

## 4. 검증

- **계획 끝 회귀(1회)**: `python scripts/regress.py --base 02c6729a --files <38파일>` → 본체 통과 7,225 · 실패 1 · 건너뜀 21. 실패 귀속: 원래 실패 1(`tests/test_api/test_plan160_chart_api.py::test_create_app_registers_viz_routes_only_when_chart_enabled[True]`). arch·overfit·config_fallback 통과 · mypy 이번 diff 0 · ruff 이번 diff 1(server.py E501 → 고침 · `--no-tests` 재확인 통과). `[전체 회귀 권고]` 블록 없음. **범위: 모듈 단위 — 전체 미실행**. 산출물 `logs/regress/20261017-221149-37040`
- 교정(verifier Minor) 뒤: 바뀐 2파일 + `tests/test_plan186_verify.py` 70 passed · `--no-tests` 이번 diff 위반 0
- security-auditor(L4): Critical·High·Medium 0 · Low 2 · Info 3(§3.4)
- verifier: Critical·Major 0 · Minor 1(J4 MagicMock 소유 on → 교정) · Info 6(§3.4) · P3 캐시 누수 정순·역순 없음
- 매뉴얼(D-255): L4 문구 → `scripts/manual/content/user.md`(「인증 서비스를 쓸 수 없을 때」) · `admin.md`(break-glass 안내) · 빌드 산출 `src/static/manual/{user,admin}.html`·`help_index.json` · `pytest tests/test_manual` 통과(implementer). M7 고지 문구는 user.md가 이미 「SQL 검증 실패」·「SQL 실행 에러」 종류까지만 붙는다고 서술 — 반영 없음

---

## 변경 이력

- 2026-10-17 v1.1 — 완료: W1-A(M7·J7 · M7은 이탈 판정 (b) 소비처 헬퍼로 교정)·W1-B(K5·J4·P3)·W1-C(L4) · auditor·verifier 라운드 · Minor 교정 · D-338 · 파일명 WIP 태그 제거.
- 2026-10-17 v1.0 — 최초 작성 · 메인 판정(고침 6 · 유지 3) 기록.
