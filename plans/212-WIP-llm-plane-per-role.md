# 212. 용도별 LLM 평면 분리 — 질의 응답·시맨틱 라우팅·의도 계획·최종 응답·딥 에이전트의 모델·엔드포인트를 각자 지정

> **작성일**: 2026-11-04 · **v1.1**(2026-11-04 · 승계 원칙 사용자 확정 — §4.1 항목 단위 승계) · 원 plans/208·D-359 → 2026-11-04 main 병합 시 plans/212·D-365로 재부여(main이 208·D-359를 먼저 사용)
> **상태**: **WIP** — W0~W5 완료(2026-11-04 · 작업 트리 · 미커밋) · 잔여 **W6만**(폐쇄망 운영 전환 — Q5 사용자 결정 뒤 · D-174 ②③ 동반) · D-365 확정 · D-174 부기 1 · 모듈 단위 회귀 rc=0(원래 실패 60 · 범위: 모듈 단위 — 전체 미실행) · verifier Critical 0(Major 1 교정 — `WORKER_PROVIDER_OVERRIDE` 과금 판정) · MLX 2서버(8080·8081) 대표 2단·3단 1회씩 — 로그 평면과 요청 포트 일치
> **계획 시작 SHA**: `58e34f8f`(main 병합 후 재기준 — 원 `2b43f658`)
> **회귀 대상 파일**: `.encenv.example` · `.env.example` · `CLAUDE.md` · `config/settings_help/llm.yaml` · `docs/03_setup_guide.md` · `docs/21_orchestration_ladder.md` · `scripts/apm_bench/driver.py` · `scripts/apm_bench/loop.py` · `scripts/bench/__main__.py` · `scripts/demo/__main__.py` · `scripts/demo/client.py` · `scripts/eval_routing.py` · `scripts/itam_bench/__main__.py` · `scripts/itam_bench/build_assets.py` · `scripts/manual/content/admin.md` · `scripts/manual/features.yaml` · `scripts/scenario/__main__.py` · `scripts/scenario/preflight.py` · `src/api/settings_catalog.py` · `src/config/llm.py` · `src/graph.py` · `src/llm.py` · `src/orchestration/intent_planner.py` · `src/orchestration/replanner.py` · `src/routing/semantic_router.py` · `src/static/admin/dashboard.html` · `src/static/js/admin.js` · `src/static/manual/admin.html` · `src/static/manual/help_index.json` · `src/static/manual/user.html` · `tests/test_api/test_settings_catalog.py` · `tests/test_api/test_settings_llm_roles.py` · `tests/test_api/test_ui_plan194_w5_noise_admin.py` · `tests/test_graph_llm_roles.py` · `tests/test_llm_gemini.py` · `tests/test_llm_roles.py` · `tests/test_plan201_w1_duty_areas.py` · `tests/test_plan201_w3_frontend.py` · `tests/test_plan212_verifier.py` · `tests/test_preflight_role_planes.py` · `tests/test_scripts/test_bench_sweep.py`
> **성격**: 기능 추가 · **기본 off = 현행 비트 동일** — 따로 설정하지 않으면 현재 설정된 LLM을 쓴다(사용자 확정 · §4.1). 역할 설정을 하나도 넣지 않으면 모든 호출이 지금과 같은 LLM 인스턴스를 쓴다. 구조 리팩터와 섞지 않는다(D-337 ⑥).
> **관련 결정**: **D-365**(이 계획의 정본 · 확정 2026-11-04) · **D-174**(LLM 평면 운영 정책 — 「라우터 평면 이동」은 별도 결정으로 예고됨 · 이 계획이 그 기반) · D-037(오케스트레이터 평면) · D-222·D-340(평면별 비과금 집합) · D-194(FabriX 하이퍼파라미터 프로파일 `purpose`) · D-169(라우터 구조화 출력) · D-004(LLM 전용 시맨틱 라우팅) · D-251(사다리 기준 2단) · D-127·D-240(과금 승인·로컬 MLX)
> **패키지**: 본체 `src/`(config·llm·graph 배선) · `scripts/scenario`·`scripts/bench`(과금 판정) · 문서·매뉴얼

---

## 0. 요약

지금 LLM은 **평면 2개**뿐이다 — 워커(`LLM_*` · `create_llm`)와 오케스트레이터(`ORCHESTRATOR_*` · `create_orchestrator_llm`, 딥 에이전트·noise_gate 트랙 B 전용). 질의 응답·시맨틱 라우팅·의도 계획·최종 응답 합성은 모두 **같은 워커 인스턴스 1개**를 쓴다(`src/graph.py:969`에서 1회 생성 후 `partial(llm=llm)`로 주입).

이 계획은 워커 평면 안에 **역할(role)** 을 두어, 역할마다 provider·엔드포인트·모델·키·타임아웃을 따로 지정하게 한다. **따로 설정하지 않으면 현재 설정된 LLM(워커 `LLM_*`)을 쓴다**(사용자 확정 2026-11-04). 승계는 항목 단위다 — 역할에 적은 항목만 바뀌고 나머지는 현재 워커 값을 가져온다. 하나도 적지 않으면 워커와 같은 객체를 쓴다(현행 비트 동일). 기존 provider 팩토리(`_create_fabrix`·`_create_vllm`·`_create_mlx`…)는 고치지 않고, **역할 값으로 덮어쓴 설정 사본을 기존 팩토리에 넘기는 방식**으로 provider별 처리(D-042 thinking 가드·D-060 SSL·D-222 스트림 청크 타임아웃·D-194 프로파일)를 전부 재사용한다.

---

## 1. 요구

사용자 지시(2026-11-04): *"질의 응답과 시멘틱라우팅, 딥에이전트 등의 LLM모델과 엔드포인트를 각자 다르게 하려고 한다."*

해석(§7 Q1에서 확정):

| 요구 표현 | 대응 역할 | 현행 |
|---|---|---|
| 질의 응답 | `worker`(기본 평면 — NL→SQL·결과 정리 등 나머지 전부) | `LLM_*` |
| 시멘틱 라우팅 | `router` — 3단 `semantic_router` | 워커 공유 |
| (「등」 — 2단 기준 경로의 라우팅 상당) | `planner` — 2단 `intent_planner`·`replanner` · 3단 계획 루프 `plan`·`replan` | 워커 공유 |
| (「등」 — 사용자에게 보이는 최종 응답) | `answer` — `purpose="answer"` 호출 지점 전부 | 워커 공유(FabriX만 하이퍼파라미터 프로파일 분리 · D-194) |
| 딥 에이전트 | `orchestrator` | **이미 분리됨**(`ORCHESTRATOR_*`) — 키 이름·동작 그대로 두고 기동 로그·과금 판정·문서의 평면 표에만 편입 |

**왜 `planner`가 필요한가**: 기준 경로는 2단 `intent_orchestration`이다(D-251). 3단 `semantic_router`는 비교 arm이라, 라우터만 분리하면 기준 경로에서는 아무것도 바뀌지 않는다. 2단의 대상 DB·도구 결정은 `intent_planner`가 한다.

---

## 2. 현황 실측 (2026-11-04)

### 2.1 팩토리

| 함수 | 평면 | provider | 비고 |
|---|---|---|---|
| `create_llm(config, *, provider_override, purpose)` (`src/llm.py:156`) | 워커 | `ollama`·`fabrix`·`gemini`·`mlx`·`vllm` | `purpose`는 FabriX 프로파일만 바꾼다 · `provider_override`는 테스트 전용(`worker_provider_override`) |
| `create_orchestrator_llm(config)` (`src/llm.py:197`) | 오케스트레이터 | `vllm`·`gemini`·`mlx` | 소비처: `src/orchestration/deep_agent.py:123` · `noise_gate/infrastructure/noise_signal_tools.py:271` |

### 2.2 워커 소비처 — W0 실측 확정(2026-11-04 · 기준 `58e34f8f`)

**(가) `grep -rn "create_llm(" src noise_gate --include='*.py'` · tests 제외 = 39줄** — 호출 34 · 비호출 5(정의 1 · 독스트링/주석 4). 경로 어휘: **상시** = 주입 인자 없이 늘 생성 · **내부** = `llm is None`일 때만 생성(괄호 안이 실제 주입처) · **비호출** = 정의·주석.

| # | 지점 | 경로 | purpose | 역할 | 비고 |
|---:|---|---|---|---|---|
| 1 | `src/graph.py:969` | 상시(그래프 1회) | det | worker | **워커 생성 유일 지점 · `worker_provider_override` 적용 유일 지점**(`:969-974`). 역할 인스턴스 해석 위치(W2) |
| 2 | `src/llm.py:156` | 비호출(정의) | — | — | `create_llm` 정의 |
| 3 | `src/pipeline/turn/approval.py:144` | 상시 | det | worker | SQL 승인 의도 분류 |
| 4 | `src/nodes/synonym_registrar.py:280` | 내부(그래프 미주입 — `graph.py:698`은 `llm` 없음 → 늘 이 경로 · `intent_llm_assist` on일 때만) | det | worker | |
| 5 | `src/nodes/field_mapper.py:139` | 내부(`graph.py:548`) | det | worker | |
| 6 | `src/nodes/result_organizer.py:681` | 내부(`graph.py:270·877` · 2단 서브에이전트) | det | worker | |
| 7 | `src/nodes/result_organizer.py:830` | 내부(같음) | det | worker | |
| 8 | `src/nodes/result_organizer.py:996` | 내부(같음) | det | worker | |
| 9 | `src/nodes/result_organizer.py:1043` | 내부(같음) | det | worker | |
| 10 | `src/nodes/cache_management.py:53` | 내부(`graph.py:693`) | det | worker | |
| 11 | `src/nodes/general_inference.py:341` | 내부(`graph.py:703` · 2단 `subagents.py:973`) | answer | answer(자동) | **그래프 경로에서는 도달하지 않는다** — 주입 llm이 늘 있다(아래 ⚠) |
| 12 | `src/nodes/query_generator.py:527` | 내부(`graph.py:265·860`) | det | worker | |
| 13 | `src/nodes/multi_db_executor.py:473` | 내부(`graph.py:268·685`) | det | worker | |
| 14 | `src/nodes/schema_analyzer.py:1069` | 내부(`graph.py:264·856`) | det | worker | |
| 15 | `src/nodes/input_parser.py:230` | 내부(`graph.py:543`) | det | worker | |
| 16 | `src/nodes/output_generator.py:540` | 내부(`graph.py:881` · `result_aggregator.py:869·1485`) | answer / det(`stream_user_response=False` 중간 합성) | answer(자동) / worker | **그래프 경로에서는 도달하지 않는다**(⚠) |
| 17 | `src/schema_cache/admin_base.py:91` | 비호출(독스트링) | — | — | |
| 18 | `src/schema_cache/admin_base.py:116` | 상시(`_default_llm` = `llm_factory` 기본 — DB 구조·자산 생성·등록 서비스) | det | worker | |
| 19 | `src/semantic/result_explainer.py:55` | 비호출(주석) | — | — | 실제 주입 람다는 #34 |
| 20 | `src/orchestration/doc_query.py:134` | 비호출(독스트링) | — | — | |
| 21 | `src/orchestration/doc_query.py:138` | 상시(`_answer_llm` · 실패 시 주입 llm 폴백) | answer | answer(자동) | |
| 22 | `src/orchestration/replanner.py:115` | 내부(`graph.py:607` · 3단 `tier3_plan.py:510` ← `graph.py:727`) | det | **planner** | `_llm_evaluate`만 사용 |
| 23 | `src/orchestration/result_aggregator.py:122` | 내부(`graph.py:600·732` · `deep_agent.py:492` · `sequential_runner.py:100`) | det | ⚠ answer 후보(W2) | 이 llm은 `output_generator`(`:869·1485`)·`_synthesize_finalized`(`:228`)로만 간다 = 응답 서술 전용 |
| 24 | `src/orchestration/result_aggregator.py:2055` | 내부 — **실질 미도달**(`:122`가 먼저 채운다 · 호출처는 `:228` 1곳) | answer | answer(자동) | |
| 25 | `src/orchestration/deep_agent.py:124` | 주입(`graph.py:570` `worker_llm=llm` · 조립 시도 `graph.py:520`) · 없으면 생성 | det | worker(Q6) | 같은 `worker_llm`이 `deep_agent.py:492` 최종 합성에도 간다(⚠) |
| 26 | `src/orchestration/agent_orchestrator.py:84` | 내부(`graph.py:590` · `sequential_runner.py:96`) | det | worker | 합성 없음 — 서브에이전트 핸들러에 그대로 전달(`agent_orchestrator.py:463` `spec.model or llm`) |
| 27 | `src/orchestration/intent_planner.py:666`(`_plan_turn`) | 내부(`graph.py:586` · 3단 `tier3_plan.py:154` ← `graph.py:716`) | det | **planner** | `_llm_decompose`만 사용 |
| 28 | `src/orchestration/sequential_runner.py:64` | 내부(`graph.py:651`) | det | worker | 한 llm을 분해(`:66` `_llm_decompose`)·실행(`:96`)·집계(`:100` `synthesize=False`)에 함께 쓴다 → planner로 바꾸면 실행·응답도 planner가 된다. **worker 유지**(분해만 분리는 범위 밖) |
| 29 | `src/orchestration/portal_help.py:122` | 상시(`_answer_llm` · 실패 시 주입 llm 폴백) | answer | answer(자동) | |
| 30 | `src/api/routes/alarm.py:923` | 상시(`suggest_alarm_prompt`) | det | worker | |
| 31 | `src/api/routes/schema_cache.py:502` | 상시(설명 생성) | det | worker | |
| 32 | `src/api/routes/schema_cache.py:781` | 상시 | det | worker | |
| 33 | `src/api/routes/doc_search.py:118` | 상시(관리자 문서 검색 시험) | answer | answer(자동) | |
| 34 | `src/api/routes/result_viz.py:200` | 상시(`_llm_factory` 람다 → `semantic/result_explainer` 주입 · `:261·292`) | answer | answer(자동) | |
| 35 | `src/routing/semantic_router.py:155` | 내부(`graph.py:680` `authorized_router(inner=…)`) | det | **router** | |
| 36 | `noise_gate/application/annotation_classifier.py:105` | 비호출(독스트링) | — | — | |
| 37 | `noise_gate/application/annotation_classifier.py:194` | 상시(지연 1회) | det | worker | Q3 범위 밖 |
| 38 | `noise_gate/application/nodes/agentic_enricher.py:95` | 상시 | det | worker | Q3 범위 밖 |
| 39 | `noise_gate/application/nodes/alarm_analyzer.py:297` | 상시 | det | worker | Q3 범위 밖 |

미배정 0. 역할별: router 1(#35) · planner 2(#22·#27) · answer(자동) 7(#11·16·21·24·29·33·34 — #16의 det 분기는 worker) · answer 후보(W2 배선) 1(#23) · worker 나머지 · 비호출 5.

**(나) `src/graph.py` 주입 지점(`llm=llm` · `worker_llm=llm`) — 전부 #1 워커 1개**

| graph.py 줄 | 노드(배선 함수) | 역할 |
|---|---|---|
| `:263`·`:264`·`:265`·`:268`·`:270` | 3단 task 서브그래프 `task_handler`·`schema_analyzer`·`query_generator`·`multi_db_executor`·`result_organizer`(`_build_task_run_graph` ← `:723`) | worker |
| `:520` | `_deep_agent_buildable` 조립 시도 `build_deep_agent(worker_llm=)` | worker |
| `:543`·`:548` | `input_parser`·`field_mapper`(`_wire_shared_front`) | worker |
| `:570` | 1단 `run_deep_agent(worker_llm=)` | worker(Q6) — ⚠ `deep_agent.py:492` 최종 합성 포함 |
| `:586` | 2단 `intent_planner` | **planner** |
| `:590` | 2단 `agent_orchestrator` | worker |
| `:600` | 2단 `result_aggregator`(`synthesize=True`·`composite_answer="steps"`) | ⚠ **answer 후보(W2)** |
| `:607` | 2단 `replanner` | **planner** |
| `:651` | `sequential_runner`(3·4단 · `_add_sequential_runner_node`) | worker(#28) |
| `:680` | 3단 `semantic_router` | **router** |
| `:685`·`:693` | 3단 `multi_db_executor`·`cache_management` | worker |
| `:703` | 3단 `general_inference` | ⚠ **answer 후보(W2)** — llm은 최종 응답 1회(`astream_text(…, USER_RESPONSE_TAG)`)뿐 |
| `:716` | 3단 계획 루프 `plan`(`tier3_plan.plan` → `_plan_turn`) | **planner** |
| `:727` | 3단 계획 루프 `replan`(`tier3_plan.replan` → `replanner`) | **planner** |
| `:732` | 3단 계획 루프 `finalize`(`result_aggregator` `synthesize=True`) | ⚠ **answer 후보(W2)** |
| `:856`·`:860`·`:877` | 3·4단 꼬리 `schema_analyzer`·`query_generator`·`result_organizer` | worker |
| `:881` | 3·4단 꼬리 `output_generator` | ⚠ **answer 후보(W2)** — llm은 요약 서술(`_summarize`)뿐 |

> **⚠ 전제 교정(W0)**: §4.3 「`answer`는 대부분 내부 생성 경로라 자동 적용으로 끝난다」는 **채팅 본 경로에 대해 성립하지 않는다.** 그래프는 응답 노드에 워커를 늘 주입하므로(`:600·703·732·881` · `:570`→`deep_agent.py:492` · 2단 서브에이전트 `general_inference` ← `:590`) `purpose="answer"` 분기(#11·#16·#24)는 그래프 경로에서 도달하지 않는다 — **현행 채팅 최종 응답에는 D-194 answer 프로파일도 적용되지 않고 있다**(plans/138 §2가 문서 처리기에 대해 같은 사실을 기록). 자동 적용으로 끝나는 것은 상시 생성 4곳(#21·29·33·34)과, 주입 없이 단독 호출될 때의 #11·#16뿐이다.
> W2 판단 항목: ① 응답 노드 4곳(`:600·703·732·881`)에 answer 인스턴스를 주입할지. **answer 미설정이면 워커 객체 그대로(`is`)여야 현행 비트 동일**이다(미설정 시 「워커 + answer 프로파일」을 주입하면 `LLM_FABRIX_ANSWER_LLM_CONFIG`가 설정된 운영에서 채팅 응답 페이로드가 바뀐다). ② `result_aggregator`의 llm은 D-062 중간 합성(`:1485` `stream_user_response=False`)에도 쓰인다 — 한 인스턴스 주입으로는 §4.1 「중간 합성은 worker」를 지킬 수 없다(answer로 같이 가거나, 인자 분리가 필요). ③ 1단 `deep_agent.py:492`·2단 서브에이전트 `general_inference`(`:590` 경유)는 worker(Q6·현행)로 두는 것이 최소 변경.

### 2.3 같이 움직여야 하는 곳

| 대상 | 현행 | 영향 |
|---|---|---|
| 과금 판정 `scripts/scenario/preflight.py` `external_planes(worker, orchestrator)` · 집합 `WORKER_INTERNAL_PROVIDERS`·`ORCHESTRATOR_INTERNAL_PROVIDERS` | 평면 2개만 본다 | **역할 provider를 빼면 D-127 우회가 생긴다**(예: `LLM_ROUTER_PROVIDER=gemini`가 승인 없이 실행). 소비처 3곳(시나리오 기본·`--run`·벤치 `approval_policy`) |
| MLX 실행 전 점검 `preflight.mlx_run_blockers` | 워커·오케스트레이터의 mlx 서버만 | 역할이 다른 mlx 포트를 쓰면 그 서버도 점검 |
| 설정 화면 `src/api/settings_catalog.py` | `RELOADABLE_KEYS`·시크릿 목록·필드 수 단언 테스트 | 신규 필드 등재 · 도움말 커버리지 게이트(`config/settings_help/*.yaml`) |
| 리로드 `POST /admin/settings/reload` | `build_graph` 재조립 후 원자 교체 | 역할 인스턴스도 재조립에 포함되면 리로드로 반영 가능 |
| 관리자 표시 `src/schema_cache/admin_base.py` `provider_info` | 워커 모델만 | 역할별 표시(W4) |
| 기동 로그 | 사다리 1줄(`record_ladder_resolution`) · 딥 에이전트 조립 1줄 | 평면 해석 1줄 추가 |

### 2.4 W0 실측(2026-11-04)

| 항목 | 결론 |
|---|---|
| ② 구조화 출력 모드 | `src/clients/instructor_adapter.py` `select_mode(llm)` — **`type(llm).__name__`이 `_NATIVE_TOOL_CALLING_CLASSES = {"ChatOpenAI","AzureChatOpenAI"}`면 `MODE_TOOLS`, 그 밖은 `MODE_MD_JSON`**(정확한 클래스명 일치 · 하위 클래스 불인정). provider → 클래스(`src/llm.py`): vllm `ChatOpenAI` → TOOLS · mlx `MLXChatOpenAI`(ChatOpenAI 하위) → MD_JSON · fabrix `KBGenAIChat`/`FabriXAPIClient` → MD_JSON · gemini `ChatGoogleGenerativeAI` → MD_JSON · ollama `LLMAPIClient` → MD_JSON. 역할 provider가 vllm이면 라우터 구조화 출력이 TOOLS로 바뀐다(D-169 · W1 매트릭스 테스트 대상) |
| ③ LLM 호출 계측 | `src/observability/`에 LLM 호출 메트릭·트레이스 없음(`metrics.py`는 HTTP 카운터·히스토그램뿐 · `trace_collector`·`trace_writer`·`run_capture`에 provider/model 필드 0) — **없음 · 추가 안 함** |
| ④ 설정 카탈로그 단언 테스트 | `tests/test_api/test_settings_catalog.py` — `test_t1_schema_endpoint_returns_catalog`(`:241` 항목 484) · `test_t2_group_and_field_counts`(`:435` 484 · `:438` general 22) · `test_t2_type_detection`(`:477-478` `LLM_PROVIDER` enum 5종) · `test_t2_secret_detection`(`:495` 시크릿 집합 정확 일치 — 역할 `*_API_KEY` 3종 추가 시 갱신) · `test_t6_requires_restart_matches_immediate_list`(`:829`) · `test_t6_unconsumed_fields_flagged`(`:839` 19) · `test_t6_apply_mode_partitions_catalog`(`:924` reload 집합 = `RELOADABLE_KEYS` · 리로드 키에 시크릿 금지) · `test_t1_catalog_covers_all_env_file_keys`(`:81`) · `tests/test_api/test_settings_help.py::test_t2_curated_groups_have_full_coverage`(`:79` 큐레이션 100%)·`test_t1_curated_keys_all_exist_in_catalog`(`:59`)·`test_t3_every_catalog_key_yields_help`(`:142`) · `tests/test_api/test_settings_catalog_sections.py::test_env_files_fully_covered_by_catalog`(`:79`) · `tests/test_scenario/test_platform_gates.py:45`(프로파일 키 ⊂ 카탈로그) · `tests/test_scenario/test_measurement_trust.py:637`(`RELOADABLE_KEYS` 멤버십) |
| ⑤ 역할 키 이름 충돌 | `LLM_ROUTER_*`·`LLM_PLANNER_*`·`LLM_ANSWER_*` — 기준 SHA의 `src`·`noise_gate`·`scripts`·`config`·`docs`·`.env.example`·`.encenv.example`, 로컬 `.env`(키 이름만)에 **0건**(이 계획서 제외) · `os.getenv` 사용 0 · 기준 `LLMConfig`에 `router_`/`planner_`/`answer_` 접두 필드 0(유사 이름 `fabrix_answer_llm_config` → `LLM_FABRIX_ANSWER_LLM_CONFIG`는 접두가 달라 무충돌) · `RouterConfig`는 `ROUTER_` 접두라 무충돌 |
| ⑥ 리로드 | `src/api/routes/admin_settings_reload.py:72`(`load_config.cache_clear`) → `:74`(새 config) → `:118-119`(`run_in_threadpool(build_graph, fresh, …)`) → `:152-153`(graph·config 원자 교체). `build_graph`가 `src/graph.py:969`에서 워커를 **새로 만든다** — 역할 인스턴스도 `build_graph` 안에서 해석하면 리로드로 반영된다. 라우트 상시 생성(#3·30~34)은 요청마다 `app.state.config`로 생성 → 리로드 후 새 값 · noise_gate(#37~39)는 알람 워커 기동 캡처 config(D-194 주의 ② 비대칭 그대로) |
| 부기 | ① 내부 생성 경로(#4~16·22~28·35)는 `worker_provider_override`를 적용하지 않는다(현행) — 그래프에서는 주입 llm이 있어 도달하지 않으므로 영향 없음 ② `AgentSpec.model`(`agent_spec.py:111`) 서브에이전트별 모델 슬롯이 예약돼 있으나 전부 None(`agent_orchestrator.py:463`·`tier3_plan.py:378`·`deepagents_tools.py:387`이 `spec.model or llm`) — 이 계획은 쓰지 않는다 |

---

## 3. 기존 결정과의 충돌 검토

| 결정 | 내용 | 판정 |
|---|---|---|
| **D-174** | 운영 정책 「vLLM은 deep agents 전용 · 나머지는 FabriX」 · *"향후 시멘틱 라우팅 등을 vLLM으로 분리할 예정"* · ③ 라우터 평면 이동 결정이 서면 트랙 C·트랙 B 발효·`pydantic-ai` **3건을 한 번에 재평가** · ② `MIN_RELEVANCE_SCORE` 정산은 **모델·시점과 함께 기록**하고 라우터 평면이 바뀌면 재정산 | **충돌 아님(기반 마련)** — 이 계획은 기본 off 메커니즘이다. **운영에서 라우터를 실제로 옮기는 것은 별도 사용자 결정(Q5)** 이고, 그 결정이 서면 D-174 ②·③을 같은 자리에서 처리한다(§6 W6). D-174에 부기 1을 단다 |
| D-037 | 딥 에이전트 = 오케스트레이터(vLLM) + 워커(FabriX) | 유지 — 오케스트레이터 키·동작 무변경 |
| D-222 ③ · D-340 ④ | 과금 판정은 평면별 집합 · 정의 1곳(`preflight`) | **확장** — 역할 평면도 같은 집합으로 판정(워커 집합 적용). 정의 위치 1곳 유지 |
| D-194 | `purpose`는 FabriX 하이퍼파라미터만 고른다 | 유지 — `answer` 역할은 엔드포인트를, `purpose`는 프로파일을 고른다(직교). `answer` 역할이 fabrix면 `LLM_FABRIX_ANSWER_LLM_CONFIG`가 그대로 적용된다 |
| D-169 | 라우터 구조화 출력(instructor 모드는 클라이언트 클래스로 결정) | 유지 — 역할 provider가 바뀌면 모드도 클래스에 따라 바뀐다. W1에서 provider별 모드 매트릭스 테스트 |
| D-340 ③ | vllm 필수값 누락은 기동 실패 · 조용한 폴백 금지 | **동일 규칙 적용** — 역할 provider를 지정했는데 엔드포인트·모델이 없으면 `ValueError`(워커로 침묵 폴백하지 않는다) |
| D-251 | 기준 경로 2단 | `planner` 역할 신설의 근거 |

---

## 4. 설계

### 4.1 승계 원칙 — 따로 설정하지 않으면 현재 설정된 LLM을 쓴다 (사용자 확정 2026-11-04)

사용자 지시: *"LLM을 별도로 각각 설정하지 않으면 현재 설정된 LLM을 사용하는 방식으로 계획을 수립하라."*

**「현재 설정된 LLM」 = 지금의 워커 설정(`LLM_*`)이다.** 승계는 역할 단위가 아니라 **항목 단위**로 한다. 역할에 적지 않은 항목은 하나하나 현재 워커 설정에서 가져온다. 그래서 역할마다 바꾸고 싶은 항목만 적으면 된다.

```
create_llm(config, role="router")
  ① 역할 항목이 하나도 없다     → 현재 워커와 같은 인스턴스(그래프) / 같은 인자(직접 생성)   ← 현행 비트 동일
  ② 역할 항목이 일부 있다       → 항목별로 해석한 뒤 기존 팩토리 호출
        provider    = LLM_ROUTER_PROVIDER  없으면 → 현재 워커 provider(LLM_PROVIDER)
        base_url    = LLM_ROUTER_BASE_URL  없으면 → 현재 워커의 그 provider 엔드포인트(예: LLM_VLLM_BASE_URL)
        model       = LLM_ROUTER_MODEL     없으면 → 현재 워커의 그 provider 모델(예: LLM_FABRIX_CHAT_MODEL)
        api_key     = LLM_ROUTER_API_KEY   없으면 → 현재 워커의 그 provider 키
        timeout     = LLM_ROUTER_TIMEOUT   없으면(0) → 현재 워커의 그 provider 타임아웃
        provider 고유 노브(FabriX client_key·llm_config · vllm verify_ssl·thinking · mlx max_tokens 등) → 항상 현재 워커 값
  ③ 해석 뒤 필수값(엔드포인트·모델)이 비었다 → ValueError (그래프 조립에서 기동 실패 · 워커로 몰래 바꾸지 않는다)
```

**예시** (현재 워커 = `LLM_PROVIDER=fabrix` · `LLM_FABRIX_BASE_URL=A` · `LLM_FABRIX_CHAT_MODEL=M1` · `LLM_VLLM_*` 비어 있음)

| 역할 설정 | 해석 결과 | 비고 |
|---|---|---|
| (없음) | 워커 그대로 — fabrix · A · M1 | ① 같은 객체 |
| `LLM_ROUTER_MODEL=M2` | fabrix · A · **M2** | 같은 엔드포인트·키, 모델만 다르다 |
| `LLM_ROUTER_BASE_URL=B` | fabrix · **B** · M1 | 같은 provider·모델, 엔드포인트만 다르다 |
| `LLM_ROUTER_PROVIDER=vllm` · `…_BASE_URL=V` · `…_MODEL=Q` | **vllm · V · Q** | provider를 바꾸면 엔드포인트·모델도 적어야 한다 |
| `LLM_ROUTER_PROVIDER=vllm`만 | vllm · (빈 값) · (빈 값) → **ValueError** | ③ — 현재 워커 값은 fabrix용이라 vllm에 쓸 수 없다. `LLM_VLLM_*`가 채워져 있으면 그 값을 쓴다 |

- 승계는 **그래프를 조립할 때마다** 다시 해석한다 — 설정 화면에서 워커를 바꾸고 리로드하면, 따로 설정하지 않은 역할도 바뀐 워커를 따라간다(값을 복사해 두지 않는다).
- `purpose="answer"` 호출은 **`role="answer"`를 자동 적용**한다. 호출부 8곳은 고치지 않는다(Q2). `answer` 역할을 설정하지 않으면 지금처럼 워커에 answer 프로파일(D-194)을 얹어 생성한다. `output_generator`의 중간 합성(`purpose="deterministic"`)은 `worker` 그대로다(D-194 주석의 구분 유지).
- `worker_provider_override`(테스트 전용)는 「현재 워커」를 바꾼다. 따라서 설정하지 않은 역할 항목도 그 값을 승계한다(지금 deepagent 경로 전체를 gemini로 돌리는 테스트 동작 유지).
- **딥 에이전트 오케스트레이터(`ORCHESTRATOR_*`)는 이 승계 대상이 아니다**(Q8). 지금도 따로 설정하는 평면이고, 설정하지 않으면 1단을 세우지 않고 2단으로 확정하는 현행 동작을 유지한다.

### 4.2 설정 형식 (Q4)

역할별로 **공통 5필드**만 두고, **모든 필드의 기본값은 빈 값(= 현재 워커 승계)**이다. 이름은 워커 접두(`LLM_`) 아래로 묶는다. `ROUTER_*`(라우터 신뢰도 설정 `RouterConfig`)와 섞이지 않게 하려는 것이다.

| 키 | 의미 · 기본값 | 비었을 때 가져오는 현재 워커 필드(해석된 provider 기준) |
|---|---|---|
| `LLM_<ROLE>_PROVIDER` | `ollama`·`fabrix`·`gemini`·`mlx`·`vllm` · 기본 빈 값 | `LLM_PROVIDER` |
| `LLM_<ROLE>_BASE_URL` | 엔드포인트 · 기본 빈 값 | `ollama_base_url`·`fabrix_base_url`·`mlx_base_url`·`vllm_base_url` (gemini 무시) |
| `LLM_<ROLE>_MODEL` | 모델 · 기본 빈 값 | `model`(ollama)·`fabrix_chat_model`·`mlx_model`·`vllm_model`·`gemini_model` |
| `LLM_<ROLE>_API_KEY` | 키(`.encenv` · 시크릿) · 기본 빈 값 | `ollama_api_key`·`fabrix_api_key`·`vllm_api_key`·`gemini_api_key` |
| `LLM_<ROLE>_TIMEOUT` | 초 · 기본 0 | `ollama_timeout`·`fabrix_total_timeout`·`mlx_timeout`·`vllm_timeout` |

`<ROLE>` = `ROUTER`·`PLANNER`·`ANSWER`. 대응표(provider → 필드명)는 `src/llm.py` 한 곳의 상수로 둔다. 설정 클래스는 기존 nested config 패턴(`Field(default_factory=...)`)을 따른다.

**provider 고유 노브는 항상 현재 워커 값**을 쓴다(FabriX `client_key`·`llm_config` · vllm `verify_ssl`·`enable_thinking` · mlx `max_tokens`·`enable_thinking`). 역할마다 다르게 둘 필요가 확인되면 그때 필드를 더한다(Q4 대안).

**설정 화면 표시**: 비어 있는 역할 항목에는 「현재 워커 값 사용 — `<해석된 값>`」을 회색으로 보여준다(키는 마스킹). 사용자는 무엇을 승계하는지 화면에서 바로 확인할 수 있다.

### 4.3 배선

- `src/graph.py`: 워커 1개 생성 뒤 `router`·`planner`·`answer` 역할 인스턴스를 해석한다. 역할 미설정이면 **워커 객체 그대로**(`is` 동일). 노드 주입은 §2.2 배정표대로 바꾼다 — `semantic_router`←router · `intent_planner`·`replanner`·`plan`·`replan`←planner · 나머지 무변경.
- 노드 내부 생성 경로(`llm is None`일 때): `create_llm(app_config, role=...)`로 바꾼다(라우터·계획 노드 4곳 정도).
- ~~`answer`는 대부분 내부 생성 경로라 §4.1 자동 적용으로 끝난다.~~ **W0 실측으로 정정(팀 리드 2026-11-04)**: 그래프가 응답 노드에 워커를 늘 주입하므로 `purpose="answer"` 내부 생성 분기는 채팅 그래프 경로에서 실행되지 않는다(자동 적용은 문서 QA·포털 도움말·문서 검색·결과 시각화 설명 등 그래프 밖 4곳에만 걸린다). 그래서 W2는 **응답 합성 노드 4곳**(`graph.py:600` 2단 `result_aggregator` · `:703` `general_inference` · `:732` 3단 `finalize` · `:881` `output_generator`)에 `answer` 역할 인스턴스를 주입한다. 배정은 **노드 단위**다 — 그 노드 안의 중간 합성(D-062 task별 `output_generator` 호출)도 answer 평면을 쓴다(노드 시그니처를 나누지 않는다). answer 미설정이면 워커와 `is` 동일이라 현행 비트 동일이고, 채팅 경로에 D-194 answer 프로파일이 걸리지 않는 현행 사실도 그대로다(이 계획 범위 밖 · 잔여로 보고). 1단 `deep_agent.py:492` 최종 합성은 도구 워커와 같은 `worker_llm`이라 worker 그대로(Q6).
- 딥 에이전트: 오케스트레이터 무변경 · 도구 워커는 `worker`(Q6).

### 4.4 과금 판정 (D-127 우회 차단 — 필수)

- `external_planes`를 평면→provider 매핑을 받게 일반화한다. 역할 평면은 워커 비과금 집합으로 판정한다. 하나라도 집합 밖이면 외부.
- `--show-env`·`--preflight` 출력에 역할 평면을 행으로 더한다(미설정 역할은 「워커 승계」로 표시).
- `mlx_run_blockers`: 해석된 평면들 중 mlx인 것의 **서로 다른 base_url마다** 도달·1토큰 생성 점검.
- 시그니처 변경 소비처(시나리오·벤치) 전수 grep 갱신(D-222 주의 ⑤ 선례).

### 4.5 관측·보안

- 기동 로그 1줄: `LLM 평면: worker=fabrix(M1@A) router=vllm(Q@V) planner=워커 승계 answer=fabrix(M2@A · 모델만 지정) orchestrator=vllm(<model>)` — 역할마다 「워커 승계」인지, 어떤 항목을 직접 지정했는지 보인다. 키는 출력하지 않고 호스트만 적는다.
- `LLM_<ROLE>_API_KEY`는 설정 화면 시크릿 목록·로그 비밀값 가림(D-339) 대상에 등재. `.encenv.example`에만 예시.
- LLM 호출 계측(있다면)의 provider/model 라벨에 역할을 더할지는 W0 실측 후 결정한다(없으면 추가하지 않는다).

---

## 5. 범위

| 포함 | 범위 밖 |
|---|---|
| 역할 `router`·`planner`·`answer` 설정·해석·배선 · 과금 판정 확장 · 기동 로그 · 설정 화면·도움말·리로드 · 문서·관리자 매뉴얼 | `ORCHESTRATOR_*` 키 개명·통합 · noise_gate 알람 분석 역할(Q3) · `sre_agent` LLM(별도 venv·자체 설정) · `mcp_server`·`apm_gateway`(LLM 없음) · 역할별 provider 고유 노브 · 역할별 폴백 체인(장애 시 다른 엔드포인트로 전환) · 운영 `.env` 실제 전환(Q5) · D-174 ③ 3건 재평가 자체(Q5 후 별도) |

---

## 6. Wave

| # | 내용 | 담당 · 대상 | verify |
|---|---|---|---|
| **W0** | 실측 — ① §2.2 배정표 확정(36곳 전수 · 주입 경로/내부 생성 경로 구분) ② provider별 구조화 출력 모드(`src/clients/instructor_adapter.py`) ③ LLM 호출 계측의 라벨 유무 ④ `settings_catalog` 필드 수·enum·시크릿을 단언하는 테스트 위치 ⑤ 역할 키가 `LLMConfig`(`extra="ignore"`)에 새어 드는지 `inspect`/실 로드로 확인 | implementer · 이 계획서 §2 갱신 | 배정표 행 수 = grep 결과 수 · 미배정 0 |
| **W1** | 설정·팩토리 — 역할 설정 클래스 · `create_llm(..., role=)` · provider→필드 대응표 · `purpose="answer"` 자동 적용 · 필수값 누락 `ValueError` · 기동 로그 | implementer · `src/config/llm.py`(+`__init__.py` nested 등재) · `src/llm.py` · `tests/test_llm_roles.py`(신규) | ① 역할 미설정 시 5 provider 모두 생성 인자가 현행과 동일 ② §4.1 예시 표 5행을 그대로 테스트(항목 단위 승계 — 모델만·엔드포인트만·provider 교체·provider만 → `ValueError`) ③ 역할별 요청이 자기 base_url·model로 간다(httpx MockTransport · vllm·mlx·fabrix OpenAI 호환) ③-2 워커 설정을 바꿔 다시 해석하면 미설정 역할 항목이 따라 바뀐다(리로드 승계) ④ 로그에 키 없음 ⑤ `regress.py --no-tests` · **`[웨이브 회귀 필요]`(`src/config/`) → W1 끝 모듈 단위 회귀** |
| **W2** | 배선 — `graph.py` 역할 인스턴스 해석·주입 · 라우터·계획 노드 내부 생성 경로 `role=` | implementer · `src/graph.py` · `src/routing/semantic_router.py` · `src/orchestration/intent_planner.py`·`replanner.py` · 3단 계획 루프 노드 · `tests/test_graph_llm_roles.py`(신규) | ① 역할 미설정 `build_graph` → 모든 노드 주입 객체가 워커와 `is` 동일 ② `LLM_ROUTER_*` 설정 → `semantic_router`만 다른 객체 · 2단 `LLM_PLANNER_*` → `intent_planner`·`replanner`만 ③ `prompt_render_diff.py` 무변경 ④ `arch_check`·`overfit_check` 0 |
| **W3** | 과금·하네스 — `external_planes` 일반화 · `--show-env`/`--preflight` 역할 행 · `mlx_run_blockers` 서버별 · 벤치 `approval_policy` | implementer · `scripts/scenario/preflight.py`·`__main__.py` · `scripts/bench/__main__.py` · 해당 테스트 | ① 워커 mlx + `LLM_ROUTER_PROVIDER=gemini` → 외부 판정(승인 요구) ② 역할 미설정 시 판정·출력이 현행과 동일 ③ mlx 서버 2개(포트 다름) → 점검 2회 |
| **W4** | 설정 화면·문서 — `settings_catalog`(`RELOADABLE_KEYS`·시크릿) · `config/settings_help/llm.yaml` · `admin_base.provider_info` 역할 표시 · `.env.example`·`.encenv.example` · `docs/03_setup_guide.md` §7(역할 평면 절) · `docs/21_orchestration_ladder.md` §3(평면 표) · `CLAUDE.md` Tech Stack 1줄 · **관리자 매뉴얼**(D-255 — 설정 항목 신설) | implementer · 위 파일 · `tests/test_settings_catalog.py` · `tests/test_manual` | ① 도움말 커버리지 게이트 통과 ② 리로드 후 역할 변경이 새 그래프에 반영(가짜 LLM) ③ `pytest tests/test_manual` 통과 |
| **W5** | 독립 검증·회귀·기록 — verifier 리뷰(침묵 폴백·과금 우회·시크릿 노출 중점) · `regress.py --base 58e34f8f --files …` · **MLX 대표 1회**(아래) · D-365 본문·색인·CHANGELOG · D-174 부기 1 · INDEX | verifier · team-lead | 모듈 단위 회귀 통과 · 범위 줄 보고에 그대로 옮김 · MLX 1회에서 기동 로그의 역할 평면과 실제 요청 포트가 일치 |
| **W6**(폐쇄망 · Q5 후) | 운영 전환 — 폐쇄망 `.env`에 역할 지정(예: router·planner → 사내 vLLM) · 대표 질의 응답·지연 비교 · **D-174 ② `MIN_RELEVANCE_SCORE` 재정산(모델·시점 기록)** · **D-174 ③ 3건 일괄 재평가** | 사용자·내부망 | 응답시간 목표(단순 <10s · 복합 <30s) 유지 · 라우팅 골드셋(`eval_routing`) 비열화 |

**MLX 검증(W5 · 최소 1회)**: 두 평면 모두 루프백 MLX일 때만. `scripts/mlx_server.sh`로 워커 서버(8080)를 띄우고, 같은 캐시 모델로 두 번째 서버를 다른 포트(예: 8081)에 띄워 `LLM_ROUTER_*`·`LLM_PLANNER_*`를 8081로 지정 → 대표 질의 1건(2단)과 3단 비교 arm 1건. 메모리(M1 Max 32GB · 9B 4bit 2개)는 W0에서 여유 확인 후 진행하고, 부족하면 가짜 OpenAI 서버 2개로 대체한다. 지연 수치는 결론에 쓰지 않는다(D-174 부기).

---

## 7. 사용자 확인 — **전 항목 권고안 확정**(2026-11-04 · Q4는 승계 원칙 지시 · 나머지는 *"권고안대로 진행하라."*)

| # | 질문 | 권고 | 대안 |
|---|---|---|---|
| **Q1** | 분리할 역할 범위 | **`router`·`planner`·`answer` 3종 + 기존 `orchestrator`** — 기준 경로(2단)에 효과가 있으려면 `planner`가 필요하다(§1) | (b) `router` 1개에 3단 라우터와 2단 계획 노드를 묶는다 — 키는 줄지만 2단·3단 비교 arm을 다른 모델로 대조할 수 없다 · (c) 요청 문구 그대로 `router`만 |
| **Q2** | `answer` 역할을 `purpose="answer"`에 자동 연결 | **예** — 호출부 8곳 무변경, 최종 응답 정의가 D-194와 한 곳으로 맞는다 | 호출부마다 `role="answer"` 명시(변경 8곳+) |
| **Q3** | noise_gate 알람 분석(`alarm_analyzer`·`agentic_enricher`·`annotation_classifier`)도 역할로 분리 | **이번 범위 밖** — 요청에 없고, 필요 시 역할 1행 추가로 끝나는 구조다 | `alarm` 역할 추가(W2에 noise_gate 배선 3곳) |
| **Q4** ✅ | 역할 설정 형식·승계 | **확정(사용자 2026-11-04)** — 따로 설정하지 않으면 현재 설정된 LLM을 쓴다 · 항목 단위 승계 · 공통 5필드 + provider 고유 노브는 현재 워커 값(§4.1·§4.2) | 역할마다 provider 전체 필드 복제(필드 수 3배 · 화면·도움말 부담) · 이름 붙인 엔드포인트 묶음을 JSON으로 정의하고 역할이 이름 참조(키가 JSON 안에 들어가 `.encenv` 시크릿 처리와 맞지 않음) |
| **Q5** | 운영(폐쇄망)에서 실제로 라우터·계획을 vLLM 등으로 옮길지 | **이 계획은 메커니즘까지** · 운영 전환은 W5 뒤 사용자가 정한다. 정하면 D-174 정책 개정 + ②·③ 처리(W6) | 계획 안에서 운영 전환까지 확정 |
| **Q6** | 딥 에이전트 도구 실행 워커(`deep_agent.py:124`)도 별도 역할로 | **아니오 — `worker` 그대로**(도구 내부가 SQL 생성·결과 정리 등 질의 응답 경로 자체) | `deep_worker` 역할 추가 |
| **Q7** | 역할 설정 변경의 반영 시점 | **설정 화면 리로드로 반영**(리로드가 그래프를 재조립하므로 · 승계 항목도 리로드 때 다시 해석) | 재시작 필요(보수적) |
| **Q8** | 딥 에이전트 오케스트레이터(`ORCHESTRATOR_*`)를 설정하지 않았을 때 워커를 쓸지 | **아니오 — 현행 유지**. 오케스트레이터에게 「현재 설정된 LLM」은 `ORCHESTRATOR_*` 자신이다. 워커로 승계하면 FabriX가 tool-calling 제어 평면을 맡게 되고(D-037 · D-174 ①), 오케스트레이터를 설정하지 않은 환경에서도 1단이 서서 사다리 확정이 바뀐다(D-251) | 미설정 시 워커 승계(행동 변경 — 사다리 확정 조건 개정 필요) |

---

## 8. 리스크

| 리스크 | 대응 |
|---|---|
| 역할 provider를 과금 판정이 못 보면 Gemini가 승인 없이 불린다(D-127) | W3을 W1·W2와 같은 계획 안에서 끝낸다 · 판정 정의 1곳 유지 · 테스트 ① |
| 역할 엔드포인트 누락이 워커로 조용히 폴백 | 금지 — `ValueError` 기동 실패(D-340 ③ 규칙) |
| 구조화 출력 모드가 역할 provider에 따라 달라져 라우터 출력 계약(D-169)이 깨짐 | W1 provider별 모드 테스트 · 실패 시 기존 폴백 경로(구조화 출력 `None` → 기존 파싱) 확인 |
| 서로 다른 모델이 같은 프롬프트에 다르게 반응(라우팅 정확도·관련도 점수 스케일) | 운영 전환(W6) 전 `eval_routing` 비교 · `MIN_RELEVANCE_SCORE` 재정산(D-174 ②) |
| 엔드포인트가 늘어 지연·가용성 지점이 늘어남 | 역할별 타임아웃 · 기동 로그로 판독 · 응답시간 목표 비교(W6) |
| 단일 MLX 서버에 다른 모델을 번갈아 요청하면 재적재(D-222 ⑦) | 로컬은 포트를 나눠 띄우거나 같은 모델로 경로만 검증 |
| `settings_catalog` 필드 수 단언 등 상수 단언 테스트 | 변경 시 저장소 전체 grep으로 갱신 |

---

## 9. 완료 기준

1. 역할 설정 0개 상태에서 그래프의 모든 노드가 현행과 같은 LLM 객체를 받는다(테스트로 고정).
1-2. 일부만 설정한 역할은 적은 항목만 바뀌고 나머지는 현재 워커 값을 쓴다(§4.1 예시 표 전 행 테스트). 해석 뒤 필수값이 비면 기동이 실패하고 워커로 몰래 바뀌지 않는다.
2. 역할마다 지정한 엔드포인트·모델로만 요청이 간다(가짜 서버 테스트 + MLX 1회).
3. 역할 provider가 과금 평면이면 하네스가 승인을 요구한다.
4. 기동 로그 1줄로 역할별 평면을 판독할 수 있고, 키는 어디에도 출력되지 않는다.
5. 설정 화면·도움말·`.env.example`·`docs/03`·`docs/21`·관리자 매뉴얼이 새 키를 설명한다.
6. D-365 등재 · D-174 부기 1 · INDEX 갱신.

---

## 10. 실행 기록 (2026-11-04 · W0~W5)

| Wave | 결과 |
|---|---|
| W0 | §2.2 배정표 39행(grep 39줄 · 미배정 0) · §2.4 실측 · **§4.3 정정** — 응답 노드는 워커를 주입받아 `purpose="answer"` 내부 분기가 그래프 경로에서 실행되지 않음 → 응답 합성 노드 4곳에 answer 주입 |
| W1 | `LLMConfig` 역할 필드 15 · `src/llm.py` 해석·생성·기동 로그 · 카탈로그 시크릿 3/리로드 12(API_KEY는 시크릿이라 재시작 반영) · `tests/test_llm_roles.py` 57 |
| W2 | `graph.py` router 1·planner 4·answer 4 주입 · 내부 생성 `role=` 3곳 · `tests/test_graph_llm_roles.py` 13 · 프롬프트 렌더 변경 0 |
| W3 | `external_planes(…, roles=)` · `role_providers` · 역할 행 · `_mlx_planes` 역할 포함 · 소비처 전수 · `tests/test_preflight_role_planes.py` · 교정: `WORKER_PROVIDER_OVERRIDE`도 과금 판정(verifier Major · 기존 결함) |
| W4 | 설정 화면 `inherited_hint`(「현재 워커 값 사용 — …」) · 리로드 반영 테스트 · `docs/03` §7.6 · `docs/21` §5 · CLAUDE.md 1줄 · 관리자 매뉴얼 A-91(캡처 미갱신 — 역할 항목이 캡처 화면 밖) · `admin_base.provider_info`는 바꾸지 않음(자산 생성 기록 라벨 = 워커 평면) |
| W5 | verifier Critical 0 · 회귀 `regress.py --base 58e34f8f --files <위 목록>` rc=0(원래 실패 60) · MLX 루프백 2서버(9B 4bit · 8080 워커 / 8081 router·planner): 2단 planner 요청 8081 1건, 3단(`ACTIVE_DB_IDS=polestar`) router 요청 8081 1건 · 기동 로그 평면과 일치 · 지연 수치는 결론에 쓰지 않음 |

**잔여**: W6(운영 전환 · Q5) · 채팅 경로 D-194 answer 프로파일 미적용(현행 · answer 역할 미설정 시 그대로) · 카탈로그 `*_BASE_URL` `effective_value`에 URL 자격증명 노출 가능(기존) · `settings_catalog`가 `src/llm.py` 비공개 이름 지연 import(Minor) · E1(apm_bench)은 역할 base_url이 `.env`에 있으면 그 서버로 감(과금 아님).
