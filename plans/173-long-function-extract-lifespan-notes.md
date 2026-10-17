# 173. 긴 함수 Extract Function — lifespan(A4) · 노드·검증 긴 함수(N5) · 노트 체인 튜플(N7) · 판정·서비스 긴 함수(I5) (166 단계 6d)

> **작성일**: 2026-10-16 · **v1.0**
> **상태**: **완료**(2026-10-16 · 커밋 전 — 메인이 커밋) — 13함수 Extract · cc>50 5→1 · >200줄 7→0 · 노트 체인 `TEXT_NOTES`·`DOC_NOTES` · 기존 테스트 수정 0 · 계획 끝 회귀 원래 실패 1만(모듈 단위) · verifier 차분 155k건 불일치 0 · Critical·Major 0 · §4·§5
> **상위**: `plans/166` §0.1 단계 6 · **계획 ID 6d**(스멜 A4 · N5 · N7(체인) · I5 — §3 A4·N5·N7·I5 행 · §5.6 W4 표 · §5.4 N7 이동 행). 같은 시간에 6b(O2 · `src/orchestration/apm_query.py` · plans/172)가 병렬로 돈다 — 파일 겹침 0. 6a(`noise_gate/domain/notification_policy.py` · plans/171)는 커밋 전 — 건드리지 않는다.
> **성격**: 동작 불변 구조 리팩터(D-337 · 166 §5.0) — 응답·SQL·프롬프트 바이트·로그 문구·노트 순서 불변 · 기존 테스트 수정 0 · 스냅샷 갱신 금지(166 §0.1 규칙 4)
> **계획 시작 SHA**: `1ad7c161`(RCA)
> **회귀 대상 파일(누적)**: 26 — 제품 13(`src/api/server.py` · `src/nodes/{output_generator,schema_analyzer,realtime_usage,result_organizer,multi_db_executor}.py` · `src/domain/{host_discovery,time_spec}.py` · `src/sql_validation.py` · `src/sql_time_conditions.py` · `src/doc_qa/service.py` · `src/infrastructure/audit_repository.py` · `src/schema_cache/db_registration_service.py`) + 새 특성화 테스트 13(`tests/**/test_plan173_t*.py` · 스냅샷 `tests/snapshots/plan173_t{3,4,5,6,8}/`) *(누적 끝 — 계획 끝 회귀 1회 실행: §4)*
> **관련 결정**: D-337(리팩터 규약 — shim·seam·`_` 별칭·행동 변경 분리) · D-161(플래그 분기 제거 금지) · D-303(회귀) · D-331(게이트) · D-255(매뉴얼 — 사용자 접점 변경 0이라 대상 아님)
> **적용 스킬**: `.claude/skills/refactoring/`

---

## 1. 범위 — 착수 시 실측(`smell_metrics.py` · 1ad7c161)

166 분석(7ba5c96) 이후 W2 파일 분할로 줄 번호가 바뀌어 다시 쟀다.

| 스멜 | 대상 | 위치(착수 시) | 줄 | 근사 cc |
|---|---|---|---:|---:|
| A4 | `lifespan` | `src/api/server.py:308` | 335 | 45 |
| N5 | `schema_analyzer` | `src/nodes/schema_analyzer.py:582` | 438 | 89 |
| N5 | `_run_output_generator` | `src/nodes/output_generator.py:140` | 251 | 44 |
| N5 | `realtime_usage_lookup` | `src/nodes/realtime_usage.py:97` | 223 | 53 |
| N5 | `result_organizer` | `src/nodes/result_organizer.py:46` | 222 | 32 |
| N5 | `validate_sql` | `src/sql_validation.py:172` | 199 | 28 |
| N5 | `_validate_forbidden_joins` | `src/sql_validation.py:1208` | 162 | 37 |
| N5 | `_read_condition` | `src/sql_time_conditions.py:446` | 118 | 62 |
| N5 | `_MultiRun`(필드 32) | `src/nodes/multi_db_executor.py:297` | — | — |
| N7 | 노트 체인(텍스트 14 · 문서 9 + 폼필) | `src/nodes/output_generator.py:225-238 · 284-298` | — | — |
| I5 | `_answer_impl` | `src/doc_qa/service.py:258` | 217 | 60 |
| I5 | `get_stats` | `src/infrastructure/audit_repository.py:229` | 208 | 20 |
| I5 | `decide_systems` | `src/domain/host_discovery.py:454` | 161 | 43 |
| I5 | `resolve` | `src/domain/time_spec.py:679` | 139 | 52 |
| I5 | `config_snippets` | `src/schema_cache/db_registration_service.py:1061` | 101 | 39 |

### 1.1 착수 전 실측으로 바뀐 처방

- **A4 — 취소 블록은 `lifespan` 본문에 남긴다.** `tests/test_api/test_lifespan_shutdown_cancel.py`가 `inspect.getsource(server.lifespan)`의 AST에서 `alarm_worker_task`·`sse_bridge_task`·`incident_sub_task`의 `.cancel()`과 그 `await`를 감싼 `try`의 `except`가 `CancelledError`를 잡는지 센다. `_cancel_quietly(task)`로 뽑으면 이름이 사라져 이 테스트가 실패한다(테스트 수정 금지). 같은 이유로 `AsyncExitStack` 콜백 등록도 취소 블록에는 쓰지 않는다. `tests/test_api/test_plan147_w4_admin_startup.py`는 `lifespan` 소스에 `app.state.apm_source_report = await _check_apm_sources(config)` 문자열이 있어야 한다 — 이 줄도 본문에 남긴다. 기동 `_start_*` 헬퍼와 redis·pool `_close_quietly`만 뽑는다.
- **N5 `_MultiRun` — 평면 생성자·평면 속성 접근이 테스트에 고정돼 있다.** `tests/test_orchestration/test_plan119_sql_loop.py`가 `mdb._MultiRun(state=…, …)` 키워드 생성을 하고, 여러 테스트가 `SimpleNamespace`·`MagicMock` 대역 run에 평면 속성을 단다. 하위 객체(`run.inputs.x`)로 나누면 테스트가 깨진다. 허용 범위는 「평면 생성자·속성은 그대로 두고 선언만 입력/누적으로 구분」(기반 dataclass 2개 + `kw_only`, 또는 주석 구획)까지다.
- **N7 — 튜플에 함수 객체를 담으면 이후 모듈 속성 patch가 체인에 닿지 않는다.** 착수 시 노트 함수 patch·직접 대입은 0건(T2 재확인)이라 함수 객체 튜플로 했다. 166 W0 노트 순서 테스트는 `sys.setprofile` 호출 관찰이라 영향이 없다. 이 성질은 튜플 선언 주석에 적었다(verifier Minor 반영).

## 2. Wave

파일이 겹치지 않는 트랙으로 나눠 Wave마다 4트랙을 병렬로 돌린다. 특성화 테스트는 **각 트랙 안에서 제품 코드보다 먼저** 쓰고 1ad7c161 코드에서 통과를 확인한다(verifier가 기준선 worktree에서 다시 확인).

| Wave | 트랙 | 스멜 · 대상 | 파일 |
|---|---|---|---|
| W1 | T1 | A4 `lifespan` | `src/api/server.py` |
| W1 | T2 | N7 노트 체인 튜플 + N5 `_run_output_generator` | `src/nodes/output_generator.py` |
| W1 | T3 | I5 판정 `decide_systems` · `resolve`(특성화 먼저) | `src/domain/host_discovery.py` · `src/domain/time_spec.py` |
| W1 | T4 | N5 `validate_sql` · `_validate_forbidden_joins` · `_read_condition` | `src/sql_validation.py` · `src/sql_time_conditions.py` |
| W2 | T5 | N5 `schema_analyzer` | `src/nodes/schema_analyzer.py` |
| W2 | T6 | N5 `realtime_usage_lookup` · `result_organizer` | `src/nodes/realtime_usage.py` · `src/nodes/result_organizer.py` |
| W2 | T7 | N5 `_MultiRun`(선언 구분까지) | `src/nodes/multi_db_executor.py` |
| W2 | T8 | I5 `_answer_impl` · `get_stats` · `config_snippets` | `src/doc_qa/service.py` · `src/infrastructure/audit_repository.py` · `src/schema_cache/db_registration_service.py` |

## 3. 성공 기준

1. 기존 테스트 수정 0 · W0(166) 스냅샷 무갱신 · 새 특성화 테스트는 1ad7c161에서도 통과.
2. 대상 함수 cc·줄 수 감소(§1 표 대비 — 결과는 §4).
3. 노트 순서: `tests/test_nodes/test_plan166_w0_output_note_order.py` 무수정 통과 · `TEXT_NOTES`·`DOC_NOTES`는 현행 구성·순서 그대로(U-8 — 문서 경로 4종 누락은 고치지 않음).
4. 소스를 읽는 가드(`inspect.getsource`·`__file__` 읽기)의 리터럴 출현 수가 전후 같다.
5. `arch_check --ci` error 0 · overfit 자기 델타 0 · ruff·mypy(바꾼 파일) 통과 · `prompt_render_diff.py --ci` 0.
6. 계획 끝 `regress.py --base 1ad7c161 --files <누적>` 종료 코드 0(원래 실패만).
7. verifier 차분(1ad7c161 worktree와 나란히) 동일 · Critical 0.

## 4. 결과

### 4.1 대상 함수 cc·줄 전후(`smell_metrics.py`)

| 스멜 | 트랙 | 함수 | 전 cc · 줄 | 후 cc · 줄 | 뽑은 헬퍼 |
|---|---|---|---|---|---|
| A4 | T1 | `lifespan` | 45 · 335 | 21 · 124 | `_init_file_logs` · `_build_agent_graph` · `_init_auth_db` · `_start_audit_retention` · `_start_file_log_retention` · `_connect_schema_cache_redis` · `_start_alarm_worker` · `_start_sse_bridge` · `_start_incident_tracking` · `_close_quietly` |
| N5·N7 | T2 | `_run_output_generator` | 44 · 251 | 21 · 150 | `_demote_templateless_file_request` · `_text_succession` · `_form_fill_memory_notice` · `_attach_form_fill_hitl` · `TEXT_NOTES`(13)·`DOC_NOTES`(9) + `_apply_note_chain` |
| I5 | T3 | `decide_systems` | 43 · 161 | 3 · 35 | `_DecisionContext` · `_decide_single`·`_both`·`_ambiguous` |
| I5 | T3 | `time_spec.resolve` | 52 · 139 | 20 · 49 | `_resolve_{none,last,this,next,ago,between,since,until}` |
| N5 | T4 | `validate_sql` | 28 · 199 | 5 · 94 | `_check_statement_safety` · `_check_surface_tokens` · `_check_references` · `_check_join_patterns` · `_apply_row_limit` |
| N5 | T4 | `_validate_forbidden_joins` | 37 · 162 | 5 · 35 | `_JoinEq` · `_on_clause_equalities` · `_eav_pattern_join_errors` · `_direct_join_errors` · `_excluded_column_join_errors` |
| N5 | T4 | `_read_condition` | 62 · 118 | 21 · 46 | `_Pred` · `_widen_operand` · `_read_{compare,between,in_list,negated_or_like,flipped}` |
| N5 | T5 | `schema_analyzer` | 89 · 438 | 12 · 160 | `_AnalyzeContext` · `_preselect_tables` · `_apply_allowed_tables` · `_apply_alarm_tables` · `_build_schema_dict` · `_load_structure_meta` · `_attach_structure_meta` · `_load_lookup_synonyms` · `_load_column_value_index` |
| N5 | T6 | `realtime_usage_lookup` | 53 · 223 | 9 · 93 | 단계 8(`_fetch_zone_servers` → `_build_summary`) |
| N5 | T6 | `result_organizer` | 32 · 222 | 12 · 131 | `_judge_sufficiency` · `_empty_diagnosis` · `_template_column_mapping` · `_resolve_mapping_to_result_keys` · `_attach_merge_context` |
| N5 | T7 | `_MultiRun` | 필드 32 | `_MultiRunInputs`(16) + `_MultiRunAccum`(16) 다중 상속 · `kw_only` | 평면 생성자·속성 유지 |
| I5 | T8 | `_answer_impl` | 60 · 217 | 15 · 86 | `_select_collections` · `_search_with_cache` · `_stale_notes` · `_search_diagnostics` · `_no_evidence_answer` · `_search_only_answer` · `_narrate` |
| I5 | T8 | `get_stats` | 20 · 208 | 3 · 43 | `_stats_filter` · `_fetch_*` 4 · `_count_with` · `_stats_result` · `_empty_stats` |
| I5 | T8 | `config_snippets` | 39 · 101 | 13 · 44 | `_snippet_{schema_candidate,item,body,header,notes}` |

13파일 합계: 함수 >200줄 7→0 · 근사 cc>50 5→1(남은 1은 `_build_mapping_user_parts` — §5) · >100줄 29→21 · cc>20 27→23.

### 4.2 게이트

- **Wave 운영**: W1(T1~T4) · W2(T5~T8) 각 4트랙 병렬 — 파일 겹침 0 · 공유 작업 트리. 각 트랙 특성화를 제품 코드보다 먼저 쓰고 1ad7c161에서 통과 확인 · 자기 테스트 + `regress.py --no-tests` 종료 코드 0 · `[웨이브 회귀 필요]` 최종 상태 0건(T1이 중간에 반환 주석을 바꿔 1회 발생 → 원복).
- **계획 끝 회귀 1회**(`regress.py --base 1ad7c161 --files <26>`): 본체 통과 6,167 · 실패 1 · 건너뜀 17 — 실패 1건은 `tests/test_api/test_plan160_chart_api.py::test_create_app_registers_viz_routes_only_when_chart_enabled[True]` **[원래 실패]**(기준선에서도 실패 · `create_app` 쪽). arch·overfit·config_fallback 통과 · ruff·mypy 이번 diff 줄 0. 범위 줄: `범위: 모듈 단위 — 전체 미실행`. `[전체 회귀 권고]` 블록 없음.
- **기존 테스트 수정 0**(`git diff 1ad7c161 --stat -- tests noise_gate/tests` 빈 출력) · 166 W0 스냅샷 무수정 · overfit 기준선 무수정 · `prompt_render_diff.py --ci` 차이 0.
- **verifier**(독립 검증 + 보안 한 라운드): 특성화 13파일 149건이 1ad7c161 worktree에서도 통과(해시 시드 4종) · 독립 차분 하네스 7함수 약 155,000 입력 불일치 0(`validate_sql` 27,015 · `_validate_forbidden_joins` 36,015 · `extract_column_conditions` 71,609 · `resolve` 12,000 · `decide_systems` 8,000 · `get_stats` 192(발행 SQL·인자 포함) · `config_snippets` 80) · `validate_sql` 검사 순서 보존(D-003) · Critical 0 · Major 0 · Minor 3(노트 튜플 seam 성질 — 주석 반영 / `_MultiRun` 필드 순서 변화 — 위치 생성 0이라 정보 / `test_log_retention` 소스 가드 공허화 — §5).

### 4.3 이탈(근거 · 되돌리는 비용)

- T1: `typing.Any` 추가 → 그 줄의 기존 UP035로 `AsyncGenerator`를 `collections.abc`에서 import(런타임 동일 · 1줄). 주석 1줄 E501 분할.
- T2: `collections.abc.Callable` 추가(노트 튜플 타입 별칭 · 되돌리면 `tuple[Any, ...]` 2줄).
- T5: `dataclasses.dataclass` import 추가(`_AnalyzeContext`·`_TableChoice` — 위임 프롬프트가 허용). 옮긴 줄의 `Optional[dict]` → `dict[str, Any] | None` 표기(ruff UP045·mypy가 이동 줄을 diff 줄로 잡음 · 런타임 무관).
- T7: 옮긴 두 필드 타입 주석 구체화(`dict` → `dict[str, str]` 등 · state.py와 정합 · 런타임 무관).
- A4 `AsyncExitStack` 미도입 · 취소 블록 본문 잔류(§1.1 — 소스 가드와 종료 순서).
- I5 규칙표 평탄화 미실시: `resolve` 분기 커버리지 88/102(미커버 = assert 실패 쪽 11 + 도달 불가 3) · `decide_systems`는 100%이나 규칙 3개·지연 평가 중간값이라 평탄화 이득 없음 · `_no_evidence_answer` 5갈래도 이득 작음.

## 5. 후속

### 5.1 남긴 대상(이 계획 범위 밖으로 남김)

- `multi_db_executor.py` 긴 함수 — `_build_mapping_user_parts`(cc 54 · 194줄 · 프롬프트 조립 — 바이트 불변 증명 필요) · `multi_db_executor`(cc 36) · `_run_single_target`(cc 34 · 187줄) · `_multi_resource_pivot_result`(cc 33) · `_generate_validated_sql`(198줄). 166 N5는 `_MultiRun`만 지정했다. 3,295줄 공유 파일이라 단독 계획 권고.
- `time_spec.py` `_resolve_clock`(cc 34) · `TimeSpec.__post_init__`(cc 32) · `schema_analyzer._synonym_tables_matching_query`(cc 25) · `output_generator` 나머지(`_generate_document_file` cc 21 등).
- I5 규칙표 평탄화(§4.3).
- `_MultiRun` 헬퍼 시그니처를 `_MultiRunInputs`로 좁히는 일.
- `tests/test_utils/test_log_retention.py::test_cleanup_is_actually_wired` 가드가 공허해졌다 — `server.py` 텍스트의 `cleanup_file_logs(` 출현 ≥2만 보는데 호출이 `_init_file_logs` 정의 안으로 옮겨져 `lifespan`의 `_init_file_logs(config)` 호출을 지워도 통과한다. 실효 공백은 없다(새 `test_plan173_t1_lifespan_order.py`가 같은 변이를 6건 실패로 잡음). 가드를 `_init_file_logs(` 호출 검사로 좁히는 것은 기존 테스트 수정이라 별 작업.

### 5.2 행동 변경 후보(고치지 않음 — verifier가 기준선과 같은 기존 동작임을 확인)

1. `server._init_auth_db` — DDL 등 중간 실패 시 이미 대입한 `app.state.auth_pool`·`audit_repo` 등이 남아 풀이 열린 채 감사 로테이션 태스크가 시작된다(로그는 「인증 기능 비활성」).
2. `lifespan` 종료부 `cache_mgr.disconnect()`만 예외를 삼키지 않는다 — 실패 시 「서버 종료」 로그 생략·예외 전파(다른 정리 단계와 비대칭).
3. `_read_condition` — `CURRENT_TIMESTAMP`·`CURRENT_DATE`·`SYSDATE`(괄호 없는 현재시각 낱말)를 `reason=column`으로 분류해 `db_adapters/polestar/validators._judge_groups`가 그 경계를 구간 계산에서 뺀다(`NOW()`·DB2 `CURRENT TIMESTAMP`는 `expression`).
4. `validate_sql` 참조 테이블 단계 — 미존재 테이블이 2개 이상이면 오류 문구 나열이 set 순회 순서라 프로세스마다 달라질 수 있다.
5. `schema_analyzer` — `_supplement_bare`·`_alarm_core_set` set 순회로 보충 테이블 순서가 해시 시드마다 달라져 `relevant_tables`·프롬프트 접두가 재기동마다 바뀔 수 있다(KV 캐시 안정화와 같은 종류).
6. `schema_analyzer` — `_load_manual_profile`이 요청당 최대 5회 파일을 읽는다(성능 후보 · 테스트가 호출 횟수를 patch로 봄).
7. `result_organizer` — data_insufficient 반환의 `OrganizedData`에 `sheet_mappings` 키가 없다(소비처 `.get`이라 영향 미미).
8. `host_discovery.decide_systems` ②′ 문구가 `found[0].weak_label`만 쓴다 — 보조 컬럼만 일치한 시스템이 둘 이상이고 표기가 다르면 첫 표기만 나온다.
9. `doc_qa._answer_impl` 독스트링이 `audit_context`를 「주면 감사 로그를 남긴다」고 하나 실제 감사는 래퍼가 하고 인자는 쓰이지 않는다(문서 정정 후보).
10. `time_spec.resolve` 도달 불가 분기 3(last `rolling` 참 쪽 · between `start > now` 참 쪽 · since `precision=="hour"` 참 쪽) — 정리 후보(D-161 — 제거는 별건).
11. U-8(문서 경로 노트 4종 누락)은 166 §8 후속 그대로.

### 5.3 남긴 shim·별칭

없음(같은 모듈 안 Extract · 이름 변경 0).
