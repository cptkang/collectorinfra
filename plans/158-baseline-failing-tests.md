# 158. 기준 SHA에서도 실패하는 「원래 실패」 테스트 73건 — 원인 분류와 격리 교정

> **작성일**: 2026-10-13 · **v2.0**(2026-10-19 — 범위 18 → 73건 확장 · 구현·검증 완료)
> **상태**: **완료(커밋 전)** — 73건 = (a) 제품 결함 0 · (b) 테스트 부패 2 · (c) 환경 의존 70 · (d) 플래키 1. 테스트 8파일 + 벤치 러너 1파일(`sre_agent/scripts/investigation_bench/runner.py` — 제품 코드 아님) 교정. `.env` 유·무 두 조건 결과 동일.
> **요청 원문(2026-10-13)**: *"결함은 후속 계획을 만들고 …"* (계획 작성까지만) · **v2.0 지시(2026-10-19 메인)**: v1.0 18건에 2026-10-17 전체 회귀의 원래 실패(본체 3 · sre_agent 52)를 더해 끝낸다. G-1·G-2는 권고안 (a)·(a)로 확정.
> **발견 경위**: `plans/152`(기준 `ee13b47`)·`plans/159`(기준 `6810336`)의 계획 끝 모듈 단위 회귀(`scripts/regress.py`)에서 기준 SHA에서도 실패하는 `[원래 실패]`가 반복해 나왔다(152 §10.2). v2.0 추가분은 2026-10-17 전체 회귀(`logs/regress/20261017-225255-9917/` · 기준 `02c6729a` · `failed_ids.txt` 55건).
> **성격**: 실측 분류 + 테스트 격리 교정(제품 코드 변경 0).
> **상위·인접 계획**: `plans/152`(발견 §10.2) · `plans/159`(재발견) · `plans/136`(회귀 도구 · 실패 귀속 판정) · `plans/154`(sre_agent 조사 품질 벤치 — 러너 소유)
> **관련 결정**: D-303(회귀 정책 — 병렬 불가 테스트는 원인을 먼저 고친다) · D-251(2단 기준 · 미입력 플래그 경고) · D-143(존 역질문) · D-232(조회 권한으로 존 선택지 거름) · D-127(과금 게이트) · Known Mistakes 「테스트 config는 검증 대상 필드를 명시해 `.env` 누수를 막는다」
> **실측 기준(v2.0)**: 2026-10-19 `RCA` HEAD `3eaa104a`(작업 트리 깨끗). 루트 `.venv` python 3.13.1 · FastAPI 0.141.1 · `instructor` **설치됨** · `sre_agent/.venv` python 3.13.1 · **sre_agent 패키지 editable 미설치** · xdist 미설치. `.env` 없는 조건 = `git worktree add --detach <scratchpad>/wt158 3eaa104a`(`.env` 미복사), 있는 조건 = 메인 트리(`ACTIVE_DB_IDS=polestar,itam` · 사다리 플래그 1·2·3단 true). v1.0 실측(2026-10-13 `39c5a0f` · `.env`·`instructor` 없음)의 `file:line`은 query.py 분할(179)·config 패키지화로 낡아 §2에서 갱신했다.
> **계획 시작 SHA**: `3eaa104a`(v2.0 — v1.0은 `39c5a0f`이었으나 코드 0건이라 v2.0 시작점으로 다시 잡음) · **회귀 대상 파일**: `tests/test_api/test_plan122_t4_time_resolution_route.py` · `tests/test_orchestration/test_plan146_demonstrative_zero_rows.py` · `tests/test_orchestration/test_plan150_sample_antecedent.py` · `tests/test_routing_eval/test_eval_routing_modes.py` · `tests/test_structured_output/test_intent_extraction_typing.py` · `tests/test_api/test_plan160_chart_api.py` · `tests/test_scripts/test_query_history_seed.py` · `sre_agent/scripts/investigation_bench/runner.py` · `sre_agent/tests/test_cross_source_investigation.py`

---

## 0. 요약

73건 중 **제품이 틀린 것은 없다.** 테스트(와 벤치 러너)가 개발자 로컬 상태 — `.env`의 `ACTIVE_DB_IDS`·사다리 플래그, 선택 extra `instructor`, 특정 FastAPI 버전의 `app.routes` 표현, 운영 정본 프로필의 예시 개수, venv의 sre_agent editable 설치, 실시간 시계 — 에 암묵적으로 기대고 있었다.

| 원인 | 건수 | 분류 | 파일 | 교정 |
|---|---:|---|---|---|
| C-1 `ACTIVE_DB_IDS` 미설정 → 파일 경로 존 역질문이 가로챔 | 14 | (c) | t4 route 12 · plan146 1 · plan150 1 | fixture에 `multi_db=MultiDBConfig(active_db_ids_csv="polestar")` 명시 |
| C-2 `.env` 부재 → 미입력 플래그 경고·활성 DB 0 | 2 | (c) | eval_routing_modes 2 | 서브프로세스 `_ENV`에 두 플래그 명시 · `--active-dbs` 인자 주입 |
| C-3 선택 extra `instructor` 미설치 | 2 | (c) | intent_extraction_typing 2 | `structured_backend_available()` skipif(사유에 설치 명령) — G-1 (a) |
| C-4 FastAPI 0.141 `app.routes` 비평탄화 | 1 | (c) | plan160_chart_api 1 | 등록 경로를 `app.openapi()["paths"]`로 판독 |
| C-5 운영 정본 프로필 예시 수 증가 | 2 | (b) | query_history_seed 2 | `tmp_path` 고정 원천으로 로직만 검증 |
| C-6 sre_agent venv에 패키지 editable 미설치 → 벤치 자식 프로세스 `import sre_agent` 실패 | 51 | (c) | bench_worlds_selfcheck 44 · bench_e2e_fake_llm 6 · plan154_verify_security 1 | 러너 자식 `PYTHONPATH`에 sre_agent 프로젝트 루트 추가 |
| C-7 감사 `elapsed_s` 실시간 시계 | 1 | (d) | cross_source_investigation 1 | 디스패처에 고정 `clock` 주입 |

---

## 1. 문제

- `regress.py`가 `[원래 실패]`를 가르는 기준 SHA(워크트리)에는 `.env`가 없어, 실패가 「이번 계획의 신규 실패」인지 「원래 실패」인지 매번 사람이 확인해야 했다.
- 상시 빨간색 73건이 회귀 신호의 분별력을 떨어뜨렸다. 2026-10-17 전체 회귀에서는 플래키(C-7)가 `[이번 변경 탓]`으로 잘못 귀속됐다.
- **테스트는 `.env` 유무와 무관하게 같은 결과여야 한다**(Known Mistakes — 테스트 config `.env` 누수).

---

## 2. 실측 — 테스트 × 실패 메시지 × 분류 × 근거 × 조치

재현(v2.0, 2026-10-19): 본체 7개 파일 — `.env` 없는 워크트리 **19 failed**(C-1 14 · C-2 2 · C-4 1 · C-5 2), `.env` 있는 메인 **3 failed**(C-4 1 · C-5 2). C-3 2건은 이 맥에 `instructor`가 설치돼 있어 두 조건 모두 통과(v1.0 체크아웃에서만 재현). sre_agent 52건은 `.env` 무관하게 재현(C-6 51은 결정적, C-7은 전체 병렬에서만).

### 2.1 C-1 — 존 역질문 가로채기 (14건)

공통 원인: `_file_zone_clarification_or_none`(`src/api/query/gates.py:242`)·`_zone_clarification_or_none`(`:296`)이 위치어 없는 질의에 존 역질문을 낸다. 선택지는 `build_zone_clarification`(`src/utils/query_gen_common.py:1151`)이 `:1171 options = [o for o in ZONE_CLARIFY_OPTIONS if not active or o["db_id"] in active]`로 거르는데, 활성 DB가 `polestar,itam`이면 폴스타 존 3종이 전부 걸러져 역질문이 생략되고, 활성 DB가 비면 3종 전부 남아 역질문이 뜬다. fixture(t4 `:105~110` 모듈 스코프 · 146 `:634~638` · 150 `:543~547`)가 `multi_db`를 명시하지 않아 `.env`를 그대로 읽었다.

| # | 테스트 | 실패 메시지(요지) | 분류 | 근거 | 조치 |
|---|---|---|---|---|---|
| 1 | `test_plan122_t4_…::test_four_entry_points_carry_state_value[file]` | `out["time_resolution"]` = None (응답이 zone_select 역질문) | (c) | gates.py:242 · fixture t4:105 | fixture `multi_db` |
| 2 | 〃 `[file_stream]` | `KeyError: 'time_resolution'` | (c) | 〃 | 〃 |
| 3 | `test_stream_fallback_ainvoke_carries_state_value[file_stream]` | `KeyError: 'time_resolution'` | (c) | 〃 | 〃 |
| 4 | `test_clarify_end_is_clarification_response[file]` | 응답이 「양식을 채울 대상 존이…」(기대 「[조회 기간] 2월 30일은…」) | (c) | 〃 | 〃 |
| 5 | 〃 `[file_stream]` | 같음 | (c) | 〃 | 〃 |
| 6 | `test_clarify_end_fallback_ainvoke[file_stream]` | `clarification.kind` = `zone_select`(기대 `time_period`) | (c) | 〃 | 〃 |
| 7 | `test_file_answer_without_clarify_keeps_completed_shape[file]` | `status` = `clarification`(기대 `completed`) | (c) | 〃 | 〃 |
| 8 | 〃 `[file_stream]` | `clarification` 키가 응답에 존재 | (c) | 〃 | 〃 |
| 9 | `test_followup_clarify_turn_drops_prior_turn_outputs[astream-file]` | `kind` = `zone_select` | (c) | 〃 | 〃 |
| 10 | 〃 `[astream-file_stream]` | 같음 | (c) | 〃 | 〃 |
| 11 | 〃 `[ainvoke-file_stream]` | 같음 | (c) | 〃 | 〃 |
| 12 | `test_followup_clarify_fallback_meta_event_has_no_prior_sql[file_stream]` | meta 이벤트 없음(`assert ([])`) | (c) | 〃 | 〃 |
| 13 | `test_plan146_demonstrative_zero_rows::test_widened_rows_reach_stream_meta_and_csv_with_notice_first` | 응답이 「조회할 존이 지정되지 않았습니다…」(기대 `[안내] 앞 질문…`) | (c) | fixture 146:634 | 〃 |
| 14 | `test_plan150_sample_antecedent::test_partial_rows_reach_stream_meta_and_csv_with_notice_first` | 같음 | (c) | fixture 150:543 | 〃 |

t4의 텍스트 경로 파라미터가 통과하는 이유: `_zone_clarification_or_none`은 전수 조회이면서 「서버」를 포함할 때만 발동하는데 「테스트 질의」는 해당 없다. 파일 경로는 위치어 미해소면 무조건 발동이라 12건이 전부 `[file]`·`[file_stream]` 계열이다.

### 2.2 C-2 — `eval_routing` 모드 테스트 (2건)

| # | 테스트 | 실패 메시지 | 분류 | 근거 | 조치 |
|---|---|---|---|---|---|
| 15 | `test_eval_routing_modes::TestLegacyModesUnchanged::test_static_mock_output_is_byte_identical` | stderr에 `enable_semantic_routing 미입력 …`·`enable_intent_orchestration 미입력 …` 두 줄이 추가돼 바이트 불일치 | (c) | `_ENV`(:140, `:151 env = {**os.environ, **self._ENV}`)에 두 플래그 없음 · 경고 출처 `src/config/__init__.py`(D-251) | `_ENV`에 두 플래그 `true` 명시(값 조합 4종 모두 출력 동일 실측) — 기대값 불변 |
| 16 | `TestCli::test_real_calls_stay_behind_optin_gate[argv1]` (`--runtime-prompt-conditions`) | `DID NOT RAISE SystemExit` — `scripts/eval_routing.py:2024~2026` 「활성 DB가 없다」 조기 종료로 옵트인 가드(`_require_optin` :120)에 닿지 않음 | (c) | 가드 도달 전제가 `ACTIVE_DB_IDS` 존재 | `--active-dbs` 인자 주입 · `SystemExit(2)` 단언 유지 + 「가드가 실제로 호출됐다」 단언 추가(D-127) |

### 2.3 C-3 — 선택 의존 패키지 (2건)

| # | 테스트 | 실패 메시지 | 분류 | 근거 | 조치 |
|---|---|---|---|---|---|
| 17 | `test_intent_extraction_typing::TestDecomposeStructured::test_bad_agent_is_repaired_by_reask` | `['general_inference'] != ['process_query']` — 되먹임 재질의 없음 | (c) | `src/clients/instructor_adapter.py` 「instructor 사용 불가 — 기존 파싱 경로로 강등」 · `structured` extra | `structured_backend_available()`(어댑터와 같은 판정) skipif · 사유 `pip install -e '.[structured]'` |
| 18 | `test_exhaustion_reports_reason_and_keeps_fallback` | `out.get("degraded")` = None — 침묵 폴백 | (c) | 같음 | 같음 |

### 2.4 C-4 — FastAPI 버전 의존 (1건, v2.0 추가)

| # | 테스트 | 실패 메시지 | 분류 | 근거 | 조치 |
|---|---|---|---|---|---|
| 19 | `test_plan160_chart_api::test_create_app_registers_viz_routes_only_when_chart_enabled[True]` | `assert set() == {'/api/v1/query/{query_id}/chart', …glossary, …interpret}` | (c) | FastAPI 0.141.1(`pyproject.toml` `fastapi>=0.110.0`)은 `include_router`한 라우터를 `app.routes`에 `_IncludedRouter`(path `""`)로 남겨 평탄화하지 않는다. 등록 자체(`src/api/server.py:869`)는 정상, `result_viz.router.routes`에 4경로 존재. 테스트(`:624~632`, 도입 `b6306e38` 2026-10-13)가 프레임워크 내부 표현에 기댔다 — 이 venv의 FastAPI(2026-08-28 설치)에서는 도입 첫날부터 실패 | 등록 경로를 `app.openapi()["paths"]`로 판독. src에 `app.routes` 평탄화에 기대는 코드 없음(grep) |

### 2.5 C-5 — 운영 정본 데이터 증가 (2건, v2.0 추가)

| # | 테스트 | 실패 메시지 | 분류 | 근거 | 조치 |
|---|---|---|---|---|---|
| 20 | `test_query_history_seed::test_profile_entries_use_filename_stem_as_db_id` | 수집 집합에 `itam`이 추가 | (b) | 테스트(도입 `21f1b1e7` 2026-07-30)가 운영 정본 `config/db_profiles/`를 직접 읽어 개수를 단언. `aa69985f`(2026-10-08 plans/145·146)에서 `itam.yaml`에 `query_examples`가 의도적으로 추가됨 | `tmp_path` 고정 원천(골드 3 · 프로필 3 · 예시 없는 프로필 1)으로 로직(파일명 stem = db_id · 예시 없는 프로필 제외 · 병합)만 검증 — 스크립트의 `profile_dir`·`gold_dir` 인자 사용 |
| 21 | `test_collect_entries_merges_both_sources` | `assert 75 == 70` | (b) | 같음 | 같음 |

### 2.6 C-6 — sre_agent 패키지 editable 미설치 (51건, v2.0 추가)

공통 원인: 벤치 러너가 자식 프로세스(실험군 `python -m investigation_bench._serve` · 세계 서버 · 가짜 LLM) env를 처음부터 구성하며 `PYTHONPATH = str(SCRIPTS_DIR)`(`runner.py:280`·`:1117`)만 줬다. `_serve.py:27`의 `from sre_agent.interface.mcp_service import …`는 venv에 sre_agent가 editable 설치돼 있어야만 풀린다(문서 셋업 `plans/154` §9.3.1 `pip install -e ".[dev]"`). 이 맥의 `sre_agent/.venv`에는 설치가 없어 실험군 stderr.log가 `ModuleNotFoundError: No module named 'sre_agent'`. pytest 본 프로세스는 `python -m pytest`의 cwd가 sys.path에 들어가 import가 됐기 때문에 단위 테스트는 통과했다. 도입 `13e2dabe`(2026-10-13 plans/151→154 W0~W5).

| # | 테스트 | 실패 메시지 | 분류 | 근거 | 조치 |
|---|---|---|---|---|---|
| 22~65 | `test_bench_worlds_selfcheck::test_script_scores_expected_code[*]` 44건(D01~D16 · H01~H08의 model·neg) | setup ERROR `RunnerError: sre_agent가 기동 중 종료됐다(… rc=1) — stderr.log 확인` | (c) | `tests/test_bench_worlds_selfcheck.py:89` → `runner.py:623` | 러너 `CHILD_PYTHONPATH = SCRIPTS_DIR + TOP`(sre_agent 프로젝트 루트) — 두 설정 지점 공통 상수 |
| 66~71 | `test_bench_e2e_fake_llm` 6건(completes_within_budget · export_bundle · isolated_from_service · raw_carries_w1_instrumentation · scores_model_scripts_pass · world_calls_are_tagged) | `중단: sre_agent가 기동 중 종료됐다` · `assert 1 == 0` | (c) | `tests/test_bench_e2e_fake_llm.py:133` | 같음 |
| 72 | `test_plan154_verify_security::test_arm_process_environment_is_only_runner_env` | `CalledProcessError: … -m verify_env_probe … exit status 1` | (c) | 테스트 `:126~146`이 `_arm_env`의 PYTHONPATH로 `import sre_agent.interface.mcp_service`를 수행 | 같음(테스트 무변경 — 러너 env를 그대로 씀) |

설치된 환경에서는 같은 경로라 동작이 같다. plans/154 보안 계약(실험군 env는 러너 env만 · 비밀 키 비상속 · `_serve.py`만 sre_agent import)은 경로 추가와 무관하게 유지되고, `test_bench_runner.py`·`test_plan154_verify_security.py`의 env 단언도 통과한다.

### 2.7 C-7 — 감사 계측 시각 플래키 (1건, v2.0 추가)

| # | 테스트 | 실패 메시지 | 분류 | 근거 | 조치 |
|---|---|---|---|---|---|
| 73 | `test_cross_source_investigation::TestDispatcher::test_flag_off_bit_identical_briefing_and_audit` | `audit_a == audit_b` 불일치 — `elapsed_s` 0.001 vs 다른 값 | (d) | `_dispatch`(`:450~471`)가 두 실행의 감사 레코드를 `ts`만 빼고 비교. `elapsed_s`(`investigation_dispatcher.py:528`)·`prefetch_s`(`:441~443`)가 실시간 monotonic이라 전체 병렬 부하에서만 갈림(단독 3회·파일 단위 통과). 테스트 도입 `1686f3b2`(2026-10-12)보다 뒤인 `13e2dabe`(plans/154 W1)가 `done` 감사에 계측 키를 더하면서 플래키가 됐다. 2026-10-17 회귀는 `[이번 변경 탓]`으로 잘못 귀속 | 디스패처 주입 인자 `clock=lambda: 0.0`(`:248`)로 입력 고정 — 비교 단언 불변. 디스패처를 매번 새로 만들어 dedup 상태(`:268`)는 비어 있다 |

---

## 3. 언제부터·무엇이 도입했나

| 테스트 | 도입 커밋 | 비고 |
|---|---|---|
| `test_plan122_t4_time_resolution_route.py` | `9cc8c6b`(2026-10-06) | `.env` 없는 워크트리에서 도입 시점부터 12건 실패 |
| `test_plan146_demonstrative_zero_rows.py` | `aa69985`(2026-10-08) | 도입 직후부터 |
| `test_plan150_sample_antecedent.py` | `39220a5`(2026-10-12) | 146 패턴 복제 |
| `test_eval_routing_modes.py` | `f51cd49`(2026-09-29) | 미입력 플래그 경고(`284137a`·`d957fc0` D-251)가 먼저 존재 |
| `test_intent_extraction_typing.py` | `c7d47e8`(2026-08-27) | `instructor` 설치 환경에서만 통과 |
| `test_plan160_chart_api.py` | `b6306e38`(2026-10-13) | FastAPI 0.141.1이 먼저 설치(2026-08-28) — 이 venv에선 통과한 적 없음 |
| `test_query_history_seed.py` | `21f1b1e7`(2026-07-30) | 정본 증가 `aa69985f`(2026-10-08)부터 실패 — 유일한 「깨진」 사례(테스트 부패) |
| sre_agent 벤치 51건 | `13e2dabe`(2026-10-13) | editable 설치된 venv(당시 `collectorinfra_second` 트리)에서만 통과 |
| `test_cross_source_investigation.py` | `1686f3b2`(2026-10-12) | 계측 키 추가 `13e2dabe` 이후 병렬에서 플래키 |

**결론**: 제품 회귀를 만든 커밋은 없다. C-5만 정본 데이터 변경에 테스트가 끌려간 부패이고 나머지는 「이 환경에서 통과한 적이 없다」 또는 「시계에 의존」이다.

---

## 4. 사용자 확정 게이트

| # | 질문 | 확정 |
|---|---|---|
| G-1 | `instructor` 필요 2건 | **(a) 미설치 시 사유 명시 skip**(2026-10-19 메인 확정) |
| G-2 | 활성 DB 고정 위치 | **(a) 파일별 fixture에서 `multi_db` 명시**(전역 conftest autouse 기각) |

---

## 5. 설계

§2 각 표의 「조치」 칸이 구현 그대로다. 원칙:
- 기대값을 바꿔 통과시키지 않는다 — #15 바이트 동일 기대 · #16 `SystemExit(2)` 단언 유지(오히려 「가드가 호출됐다」 단언을 추가해 조기 종료 오인을 막음).
- (b) C-5는 정본 데이터 추가가 의도된 커밋(`aa69985f`)임을 확인한 뒤, 개수 갱신 대신 고정 입력으로 바꿔 재부패를 막았다. 정본을 읽는 다른 테스트(원천 무변경 등)는 그대로 둔다.
- (d) C-7은 `serial` 마커 없이 주입 시계로 원인을 제거했다(D-303). `addopts`에 `-n`을 넣지 않는다.

**이탈(지시 대비)**: C-6은 테스트가 아니라 벤치 러너(`sre_agent/scripts/investigation_bench/runner.py`, 상수 1개 + 설정 2곳)를 고쳤다. 근거: 자식 프로세스 import 경로를 결정하는 주체가 러너라, 테스트에서 고칠 수 없다(venv에 설치하는 것은 환경을 바꿔 통과시키는 것이라 택하지 않음). 되돌리는 비용: 3줄 원복 — 그 경우 sre_agent venv마다 `pip install -e .`가 다시 필수다.

---

## 6. 작업 분해(실행 결과)

| Wave | 내용 | 결과 |
|---|---|---|
| W0 | 재현 고정 · `.env` 유무 2조건 · 2026-10-17 회귀 로그 판독 | 본체 19/3 failed 재현 · sre_agent 원인 확정(stderr.log) |
| W1 | 본체 7파일(C-1~C-5) | implementer — `.env` 무 235 passed · 유 235 passed · instructor 스텁 2 skip(사유 출력) |
| W2 | sre_agent 2파일(C-6·C-7) | implementer — 대상 5파일 170 passed(plan154 env 테스트 실행됨) |
| W3 | 계획 끝 모듈 단위 회귀 · 검증 · 기록 | 아래 §7 |

---

## 7. 수용 기준 — 결과

1. 대상 73건이 `.env` 없는 환경에서 통과(C-3 2건은 미설치 시 사유 skip) — **충족**(본체 워크트리 235 passed · sre_agent `.env` 무관).
2. 같은 건이 `.env` 있는 환경에서도 통과 — **충족**(두 조건 결과 동일).
3. 회귀: `python scripts/regress.py --base 3eaa104a --files <9파일>` rc=0 · 실패 0 · 본체 통과 379 · sre_agent 패키지 전체 통과 1,169(실패 0) · arch_check·overfit_check·config_fallback_check 통과 · ruff 이번 diff 줄 위반 0. 산출물 `logs/regress/20261019-075909-86817`. **범위: 모듈 단위 — 전체 미실행.**
4. 존 역질문을 발동시키는 기존 테스트 무영향 — fixture 범위가 각 파일 안에 닫힘.
5. 매뉴얼 동반(D-255): 해당 없음 — 사용자·관리자 기능 변경 0.
6. verifier(diff 리뷰 · 재실행 없음): Critical 0 · Major 0 · Minor 0 — 기대값 불변·옵트인 가드 강화·중복 제거 축 잔존(:142)·chart True 실검증·러너 보호 키에 `PYTHONPATH` 포함·clock 고정은 계측·dedup TTL·sweep만(타임아웃은 실시간)·C-1 fixture 파일 내 폐쇄 확인.

---

## 8. 위험 · 잔여

| 항목 | 내용 |
|---|---|
| 미발견 같은 패턴 | `grep -rn "AppConfig(" tests`에서 `multi_db` 미명시 fixture는 후보만 — 전수 스캔은 하지 않음(전체 회귀 금지). 다음 `.env` 없는 기준 워크트리 회귀에서 `[원래 실패]`로 드러나면 같은 방식으로 고친다 |
| C-7 병렬 재현 | sre_agent venv에 xdist가 없어 병렬 재현은 못 했다. 고정 시계로 계측 값이 구성상 0.0이라 부하 무관 |
| sre_agent venv | editable 미설치 상태는 그대로다(문서 셋업과 다름). 러너 교정으로 벤치는 무관해졌지만, sre_agent를 다른 cwd에서 import하는 다른 도구가 생기면 같은 문제가 난다 |

---

## 9. 의사결정 정합

- 신규 결정 불필요(테스트 격리 · 제품 동작 불변). Known Mistakes 1건 추가(테스트·벤치가 개발자 로컬 상태에 기대는 패턴).
- D-303 정합 — 플래키는 원인 제거, `serial` 미사용. D-127 정합 — 과금 옵트인 가드 단언 유지·강화. D-232·D-143(존 역질문 동작) 무변경.

---

## 10. 진행 기록

- 2026-10-13 — v1.0 작성. 18건 실행·분류 완료, 워크트리 3개(`9cc8c6b`·`42deda2`·`3c4adab`)로 도입 시점 확인 후 제거. 제품 코드·테스트·INDEX·결정 변경 0.
- 2026-10-19 — v2.0. 메인 지시로 2026-10-17 전체 회귀의 원래 실패 55건(본체 3 · sre_agent 52)을 범위에 더함. W0 재현(`.env` 없는 워크트리 `3eaa104a`) → implementer 2명 병렬(본체 7파일 · sre_agent 2파일) → 팀 리드가 세 조건(`.env` 무/유 · instructor 스텁) 재확인 → 계획 끝 회귀 1회 rc=0 → verifier diff 리뷰 Critical 0 · Major 0 · Minor 0(보고는 메인 경유로 회수). 완료(커밋 전).

## 변경 이력

- v2.0 (2026-10-19) — 범위 18 → 73건(C-4~C-7 추가) · `file:line` 갱신(query.py 분할 179 · config 패키지화) · G-1·G-2 확정 · 구현·검증 결과 · 계획 시작 SHA `3eaa104a`.
- v1.0 (2026-10-13) — 최초(번호 158 예약 스텁 대체).
