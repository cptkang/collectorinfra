# 174. 사다리 배선 SSOT(O3) · replanner 규칙 표(O7) · semantic_router 분할(O8) · registry 파서·`_coerce_arg` dispatch(O9) (166 단계 6c)

> **작성일**: 2026-10-16 · **v1.0**
> **상태**: **완료**(2026-10-16 · 커밋 전 — 메인이 커밋) — `build_graph` cc43→17 · `replanner` 66→7 · `semantic_router` 51→5 · `_plan_turn` 36→5 · `parse_registry` 46→5 · `_coerce_arg` 50→2 · 계획 끝 회귀 실패 0(모듈 단위) · verifier 차분 0(알려진 1건 제외) · Critical·Major 0 · §4
> **상위**: `plans/166` §0.1 단계 6 · **166 단계 6c · 스멜 O3(배선)·O7·O8·O9**(§3.3 O3·O7·O8·O9 행 · §5.6 W4 표). 같은 시간에 6d(`plans/173`)가 병렬로 돈다 — 파일 겹침 0. 6a(`plans/171`)·6b(`plans/172`)는 완료·커밋 전.
> **성격**: 동작 불변 구조 리팩터(D-337 · 166 §5.0) — 그래프 노드·엣지 집합 · 라우팅 결정 · 응답 · 프롬프트 바이트 · 로그 문구 · 거부 코드 불변 · 기존 테스트 수정 0 · 스냅샷 갱신 금지(166 §0.1 규칙 4)
> **계획 시작 SHA**: `1ad7c161`(RCA). 단, `src/orchestration/apm_query.py`의 기준은 **작업 트리의 6b 결과**(`1ad7c161` + plans/172 변경 · 커밋 전)다 — 이 계획은 그 위에서 `_coerce_arg` 한 함수만 바꾼다.
> **회귀 대상 파일(누적)**: W1 — `src/graph.py` · `src/observability/ladder.py` · `src/orchestration/replanner.py` · `src/orchestration/result_aggregator.py` · `src/orchestration/agent_spec.py` · `src/routing/semantic_router.py` · `src/orchestration/intent_planner.py` · `src/routing/registry.py` · `src/orchestration/apm_query.py`(`_coerce_arg` 구간만) · 문서 `docs/21_orchestration_ladder.md` *(누적 끝 — 계획 끝 회귀 1회: §4)*
> **관련 결정**: D-337(shim·seam·`_` 별칭·행동 변경 분리) · D-161(플래그·단 분기 제거 금지) · D-251(2단 기준 · 3단 비교 arm) · D-303(회귀) · D-331(게이트) · D-255(매뉴얼 — 사용자 접점 변경 0이라 대상 아님)
> **적용 스킬**: `.claude/skills/refactoring/`

---

## 1. 범위 (166 §3.3 행 · `1ad7c161` 재실측 2026-10-16)

| 스멜 | 대상(재실측) | 처방 | 성공 기준 |
|---|---|---|---|
| **O3** | `src/graph.py` `build_graph` 517-1000(484줄 cc43) — `use_deep_agent`·`single_db_tail`·`plan_loop_tier`·`sequential_tier`·`probe_tier`가 `not use_deep_agent and not config.enable_intent_orchestration`를 반복 · `resolve_ladder_tier`(`src/observability/ladder.py:72`)는 985에서 로그용으로만 호출 · docstring 「build_graph 순서와 일치해야 한다」(수동 동기화 계약) | 선두에서 `resolve_ladder_tier()` 1회 → 배선은 그 결과만 읽음 · `_wire_shared_front`·`_wire_tier1/2/3/legacy`·`_wire_single_db_tail` · 낡은 「기준 3단」 주석 D-251로 정정 · **단·플래그 분기 제거 0**(D-161·D-251) · 기동 로그 1줄 불변 | W0 그래프 위상 스냅샷(53조합) 동일 · 기동 로그 불변 · 사다리 테스트 통과 |
| **O7** | `src/orchestration/replanner.py` `replanner` 85(301줄 cc66 — 조기 종료 가드 + 신규 task 필터) · `result_aggregator.py` `_merge_task_results_by_identity` 349(cc45) · `_finalize_task` 1317(153줄) · `_build_output_state` 1562(107줄) | 순서 있는 `GUARDS`·`FILTERS` 함수 튜플 · `TypedDict HandlerResult` 선언(런타임 불변 · pydantic 미도입) · result_aggregator 3함수 Extract Function | replanner·aggregator 테스트 · mypy |
| **O8** | `src/routing/semantic_router.py` `semantic_router` 96(415줄 cc51) · `intent_planner._plan_turn` 639(232줄 cc36 — 저우선) | `_deterministic_gates`·`_route_by_llm`·`_finalize_targets` · `_plan_turn` `[(pred, builder)]` 표는 여력 시 · 2·3단 게이트 표 공유 미도입 | 라우팅 테스트 · 프롬프트 바이트 불변 |
| **O9** | `src/routing/registry.py` `parse_registry` 851(163줄 cc46) · `apm_query._coerce_arg` 387(51줄 cc50) | `_parse_*`·`_warn_dangling_refs` 추출 · `{kind: coercer}` dispatch | registry 테스트 · 경고 문구 caplog 불변 |

## 2. 실측 제약 (팀 리드 grep — 구현자가 다시 확인)

- **seam(테스트가 바꿔 끼우는 모듈 속성)**: `src.graph` — `select_orchestration_backend`(21) · `_deep_agent_buildable`(7) · `create_llm` · `build_graph` · 노드 함수 이름들(`intent_planner`·`replanner`·`field_mapper`·`semantic_router`·`context_resolver`·`input_parser`·`query_*`·`result_organizer`) · `record_ladder_resolution` · `_create_checkpointer_async`. `src.routing.semantic_router` — `_llm_classify`(5) · `_ownership_enabled`(3) · `_zone_clarification_or_none_router`(2) · `enforce_target_ownership` · `_ownership_state_fields`. `src.routing.registry` — `get_registry`. `src.orchestration.intent_planner` — `_decompose_once`(`scripts/eval_routing.py`가 모듈 속성 직접 대입). `src.orchestration.apm_query` — `_SESSION_FACTORY`·`link_business_names`·`get_registry`·`link_hostnames`(`_coerce_arg`와 무관). `result_aggregator.result_aggregator`·`tier3_plan.replanner`는 소비처 모듈에서 patch. → **추출 함수·표는 이 이름들을 호출 시점에 모듈 전역으로 조회**(import 시점 캡처 금지).
- **소스 읽는 가드**: `inspect.getsource(graph_module.build_graph)`에 `"log_adapter_ownership_startup("`(test_db_adapters.py:1577 — 이 호출은 `build_graph` 본문에 남긴다) · `src/graph.py`에 `log_investigation_startup` 호출 · `"ladder_resolution"` · `"TracedGraph"` · `StateGraph(AgentState)` 1회 · `retry_count…3` 리터럴 금지 · `ladder.py`에 3개 외 `enable_` 문자열 금지 · `semantic_router.py`에서 `"logprob"` 상수 분기는 `_intent_confidence`만 · `_validate_db_entries` 사용 함수 집합 · `inspect.getsource(router._pin_turn_location_hints)` · `MIN_RELEVANCE_SCORE = 0.3` 직전 주석 블록 · `intent_planner.py`에 `src.utils.prior_targets` import 금지 · 플래너 로그 접두어 문자열·`_decompose_once(`.

## 3. Wave

| Wave | 내용 | 상태 |
|---|---|---|
| W1 | 4트랙 병렬(파일 겹침 0) — T1 O3(`graph.py`·`ladder.py` docstring·`docs/21` 해당 줄) · T2 O7(`replanner.py`·`result_aggregator.py`·`HandlerResult` 선언 위치) · T3 O8(`semantic_router.py`·`intent_planner.py`) · T4 O9(`registry.py`·`apm_query._coerce_arg`) | 완료 |
| W2 | 계획 끝 회귀 1회 · verifier 독립 검증(변경 전 코드와 나란히) · 기록 | 완료 |

## 4. 결과

| 스멜 | 파일(+/−) | 대상 함수 줄·cc 전→후 | 구조 |
|---|---|---|---|
| O3 | `graph.py` +407/−356 · `ladder.py` +3/−2(docstring) · `docs/21` +18/−13 | `build_graph` 484줄 cc43 → 131줄 cc17 | 선두 `resolve_ladder_tier` 1회 → `_wire_shared_front`·`_wire_tier1/2/3`·`_wire_legacy`·`_wire_single_db_tail`·`_add_sequential_runner_node` · 단 내부 플래그 분기 전부 보존 · `record_ladder_resolution` 위치·인자 불변 · 수동 동기화 계약 문구 제거 · 낡은 「기준 3단」 주석·docs/21 §2 코드 블록·§4 「3단(기준)」 정정(D-251) |
| O7 | `replanner.py` +423/−221 · `result_aggregator.py` +242/−102 · `agent_spec.py` +53/−1 | `replanner` cc66→7 · `_merge_task_results_by_identity` 45→10 · `_finalize_task` 30→10 · `_build_output_state` 14→5 | `GUARDS` 9 → LLM 평가 → `DECISION_GUARDS` 2 → `FILTERS` 8 · 공유 상태 `_ReplanCtx` · `HandlerResult` TypedDict(total=False · 32키 · `agent_spec.py`) — 어노테이션은 replanner 2곳(aggregator는 7e N6로) |
| O8 | `semantic_router.py` +92/−10 · `intent_planner.py` +131/−26 | `semantic_router` cc51→5 · `_plan_turn` 36→5 | `_deterministic_gates`→`_route_by_llm`→`_finalize_targets`(`_RouteContext`·`_LlmRouting`) · `_PRECHECK_STEPS` 14단계 + `_llm_plan` |
| O9 | `registry.py` +123/−55 · `apm_query.py` 6b 대비 `_coerce_arg` 구간만 | `parse_registry` cc46→5 · `_coerce_arg` 50→2 | `_parse_*` 8 + `_warn_dangling_refs`·`_warn_undeclared_db_zones`(capabilities TypeError 시 경고 순서 보존 위해 2개) · `_coerce_*` 9 + `_COERCERS` |

- **계획 끝 회귀**(`regress.py --base 1ad7c161 --files` 9파일): 본체 통과 6,062 · 실패 0 · 에러 0 · 건너뜀 26 · arch·overfit·config_fallback 통과 · ruff·mypy 이번 diff 줄 위반 0 · `범위: 모듈 단위 — 전체 미실행` · 권고 블록 없음. 뒤이은 주석 정정 1건은 `--no-tests` 재확인(rc 0).
- **verifier**(기준 worktree `1ad7c161`+6b와 나란히): O3 2,070 · replanner 16,400 · aggregator 12,000 · router 24,000 · `_plan_turn` 18,000 · `parse_registry` 5,001 조합 차분 0 · `_coerce_arg` 8,400 중 672 차분 = 알려진 1건(아래 이탈) · 소스 가드 변이 7건 모두 검출 · seam(`_decompose_once`·`_ownership_enabled`) 실효 · Critical 0 · Major 0 · Minor 3(docs/21 「3단(기준)」 · `_PRECHECK_STEPS` 주석 오기 — 둘 다 팀 리드 정정 / 순서 표 공개·비공개 명명 불일치 — 기록만).
- **이탈**: `_coerce_arg`에 해시 불가 `spec.type`이 오면 종전 `(False, None)` → 현 `TypeError`. `ViewArgSpec` 생성 지점이 `registry.py` `_parse_view_args` 1곳이고 `str()` 변환 뒤 `VIEW_ARG_TYPES`로 검증하므로 운영 경로에서 닿지 않는다(verifier 반례 없음). 되돌리는 비용: `_coerce_arg`에 `isinstance(spec.type, str)` 가드 1줄. → **메인이 되돌림(2026-10-16)**: 동작 불변 원칙(D-337)에 맞춰 가드 1줄을 넣어 종전 `(False, None)`을 복원했다 — 해시 불가·비문자열·미지 형식 모두 `(False, None)` 확인, `apm_query` 관련 테스트 1,343 passed, 정적 게이트 이번 diff 줄 위반 0.
- 기존 테스트·스냅샷 수정 0 · 새 테스트 0(특성화는 W0 위상 스냅샷 + 구현자·verifier 대조 스크립트 — scratchpad `t174_*`·`t2_*`·`t3/`·`t4/`·`v174/`).

## 5. 후속

- **행동 변경 후보(미수정)**: ① `build_graph` 경고 「deepagents 조립 불가 → semantic_router 경로로 폴백」은 2단 on이면 실제로 intent_orchestration으로 간다(문구 부정확 — 로그 문구 변경이라 별건) ② replanner `plan_validation`이 남긴 `replan_task_invalid` 노트는 뒤 필터 6종이 루프를 끝내면 반환에서 사라진다(종전 동작) ③ getattr 폴백 잔존(D-331 ② 유형) — `graph.py` `cross_system_probe_enabled`·fault_diagnosis 체인 · `semantic_router.py` `_plan_signal_enabled`·`_ownership_enabled`·`_router_prompt_context`.
- **남긴 대상**: `_finalize_targets` cc23 · `_parse_view_args` cc31 · `result_aggregator` 본체 cc28·`_apply_empty_antecedent` 27 · `ladder.py` 모듈 독스트링의 「and not use_deep_agent」 서술 · 순서 표 명명 불일치(replanner 공개 `GUARDS`/`FILTERS` vs `_PRECHECK_STEPS`·`_COERCERS` 비공개).
- **주의(seam)**: `GUARDS`·`DECISION_GUARDS`·`FILTERS`·`_PRECHECK_STEPS`·`_COERCERS`는 단계 함수를 import 시점에 담는다 — 단계 함수 자체를 바꿔 끼우려면 표를 patch해야 한다(현재 그런 테스트 없음).
- **7e N6**: `HandlerResult`(agent_spec.py) 재사용 · aggregator 읽기 지점 어노테이션.
- 매뉴얼 미반영: 사용자 접점 변경 없음(D-255).
