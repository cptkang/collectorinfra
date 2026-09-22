# 103. 사다리 3단 기능 동등성 — LangGraph 네이티브 구성으로 1단(deep_agent)·2단 전 기능을 3단에서 제공

> **작성일**: 2026-09-17
> **상태**: **부분 구현**(2026-09-22 · `plans/111` C-4·C-5 범위 — P0-1·P0-2 · P1-1 · P2-1~P2-3 · 플래그 `TIER3_PLAN_LOOP_ENABLED` **기본 off**) — 사용자 확정 게이트 G-1~G-7은 **미응답이라 §7 기본 가정으로 진행**(§7 채택 기록) · 파일명 `-WIP`(잔여: §4.1)
> **성격**: 구현 계획 · 부분 구현(§4.1 구현 현황)
> **요청 취지(사용자 지시 원문, 2026-09-17)**: *"3단 기능도 langgraph 기능을 이용하면 1단의 모든 기능을 구현할 수 있다. 검토하여 모두 구현하는 방향으로 계획을 작성하라."*
> 선행 기준(같은 날): *"기본은 시멘틱 라우터를 사용한다. 모든 동작은 시멘틱 라우터에서 동작되어야 한다. deepagents는 부가적으로 사용할 예정"* → `plans/102` v2 · D-225 예약
> **상위/연결 계획**: **`plans/102`** v3(G-10 "3단 미도달 기능" → 이 계획으로 확정 · 트랙 L-5 운영 전환의 선행) · **`plans/104`**(구조 승인 HITL을 관리자 페이지로 이동 — 3단 복합 실행을 막던 HITL 차단 해소) ·
> `plans/88`(D-203 순차 의존 · 3·4단 `sequential_runner`) · `plans/48`·`plans/49`(D-037 트랙 A/B) · `plans/70`(D-161·D-162 사다리) · `plans/94`(시나리오 하네스 — 동등성 판정 무대)
> **관련 결정**: D-004(LLM 전용 라우팅) · D-035(결정적=판단) · D-037 · D-053(사본 금지) · D-062(복합 합성) · D-092·D-093(1단 빈 응답 재개) · D-127(과금 승인) · D-143·D-205·D-206(존 역질문·스코프·승계) ·
> D-161(승격-폐기 동반) · D-162(사다리 관측) · D-203 · D-204(진행 이벤트) · D-222(과금 판정 평면) · **D-225**(3단 기준)
> **신규 결정 예약**: **D-226**(§9) — `docs/02_decision.md` 「채번 이력」 표 등재(2026-09-17). 채번 실측: `## D-` 헤더 최댓값 222 · 채번 이력 최댓값 D-225 · 계획서 D-226 사용 0.
> **실측 기준**: 2026-09-17 작업 트리(`multiintent`, HEAD `c64ef98` + 미커밋 — `src/orchestration/deep_agent.py`·`src/config.py`·`src/llm.py`는 병행 세션(`plans/100` MLX)이 수정 중).
> LangGraph API는 **루트 venv 설치본을 토이 그래프로 실행해** 확인했다(저장소 밖 임시 스크립트 · LLM·네트워크 호출 0).

---

## 0. 요약

### 0.1 검토 결론 — **가능하다.** 막는 것은 LangGraph가 아니라 저장소 코드 4곳이다

| 질문 | 답 | 근거 |
|---|---|---|
| LangGraph로 1단 기능을 3단에 다 만들 수 있나 | **예.** 필요한 구성요소(`Send` 팬아웃 · `defer` 합류 · `Command` · 노드 안 `interrupt()` · 컴파일된 서브그래프 노드 · `RetryPolicy`·`TimeoutPolicy`)가 설치본(langgraph **1.2.11**)에 전부 있고, 조합해 돌려 봤다 | §1.1 |
| 1단은 무엇으로 만들어져 있나 | deepagents **0.6.10**도 LangGraph 위에 있다 — `langchain.agents.create_agent`(모델 노드 + `ToolNode`, 병렬 도구 호출은 `Send`) + 미들웨어. **우리가 실제로 쓰는 것은 할 일 목록(`write_todos`)과 1단계 도구 호출 루프뿐**(파일시스템 도구·범용 `task` 서브에이전트는 조립만 되고 작업에 쓰이지 않는다) | §1.1 |
| 그럼 무엇이 막나 | ①노드 추적 프록시가 컴파일된 서브그래프를 부를 수 없게 감싼다 ②`AgentState`에 리듀서가 `messages` 하나뿐이라 병렬 쓰기가 오류 ③SSE 라우트가 **처음 끝난** `final_response` 노드에서 스트림을 닫고, 인터럽트를 감지하지 못한다 ④HITL "재개"가 LangGraph 재개가 아니라 **처음부터 새 실행**이다 | §1.3 |

### 0.2 설계 한 줄

**1단의 "LLM이 도구를 골라 부르는 자율 루프"를 3단에 다시 만들지 않는다.** 같은 기능을 **워크플로**로 옮긴다 —
`semantic_router`(무엇을 할지) → **계획 노드**(하위 작업 DAG) → **`Send` 팬아웃** → **태스크 서브그래프**(에이전트별 실행) → **`defer` 합류** → **결과 기반 재계획 루프(명시 상한)** → **합성**.
데이터 조회 파이프라인은 **태스크 서브그래프 하나**로 두고, 3단 단일·복합과 1·2단 핸들러가 **같은 서브그래프를 호출**한다(D-053 · D-225 ⑥).

근거: 사용자 기준(3단 기본) + 문헌 원칙 — *"workflows offer predictability and consistency for well-defined tasks, whereas agents are the better option when flexibility and model-driven decision-making are needed"*(Anthropic, *Building effective agents* — `plans/102` P10, 2026-09-17 원문 재확인).

### 0.3 동등성의 기준 — "1단과 같은 구조"가 아니라 "같은 질문에 같은(또는 더 나은) 결과"

1. **판정 단위는 시나리오 결과**다(`plans/94` 카탈로그 — 1단 기동과 3단 기동의 판정 비교).
2. **1단의 조용한 결손은 재현하지 않는다** — 예: 1단은 ambient 키 누락으로 존 역질문이 **한 번도 발동하지 않는다**(§1.2 N-1). 기준은 1·2·3단 중 **가장 완전한 동작**이다.
3. **3단에만 있는 기능은 잃지 않는다** — 장애 진단 위임(`fault_diagnosis`), 알람 헤드라인·진행 중 월 고지(`routing_intent` 의존), 양식 채우기 네이티브 경로.

---

## 1. 현황 실측

### 1.1 LangGraph 설치본 — 필요한 것이 전부 있다

| 항목 | 실측 |
|---|---|
| 버전 | `langgraph` 1.2.11 · `langgraph-prebuilt` 1.1.0 · `langgraph-checkpoint` 4.2.0 · `langgraph-checkpoint-sqlite` 3.1.1 · `langchain-core` 1.6.1 · `langchain` 1.3.9 · `deepagents` 0.6.10 (postgres 체크포인터 미설치) |
| `langgraph.types` | `Send(node, arg, *, timeout)` · `Command(*, graph, update, resume, goto)` · `interrupt(value)` · `RetryPolicy(...)` · `CachePolicy` · `TimeoutPolicy` · `Overwrite(value)` |
| `StateGraph.add_node` | `retry_policy`·`cache_policy`·`error_handler`·`destinations`·`timeout`·**`defer`** 인자 지원. 조건부 엣지는 `Send` 목록 반환 가능 |
| 실행 | `astream(stream_mode=[…,"custom"], subgraphs=True)` · `astream_events(version="v2")` · `aget_state(subgraphs=)` · `aupdate_state` |
| 재귀 한도 | 기본 **10007**(`LANGGRAPH_DEFAULT_RECURSION_LIMIT`) — `src/config.py:128-131` 주석의 "기본값 25"는 낡았다. 라우트는 `thread_id`만 넘긴다 → **재계획 루프는 명시 카운터로 끊어야 한다** |
| 토이 그래프 실측 | 계획 → `[Send("run_task", …)]` → 컴파일된 서브그래프 노드 → `join`(`defer=True`) → 조건부 루프 → 합성: **동작**. `Annotated[dict, merge]`·`Annotated[list, operator.add]` 팬인 **동작** |
| 인터럽트 실측 | `Send`된 서브그래프 노드 안 `interrupt()` + `AsyncSqliteSaver`: **동작**. `Command(resume=…)`에서 **중단된 노드만 처음부터 재실행**, 끝난 형제 태스크·앞 노드는 재실행 안 됨. 대기 인터럽트가 2개 이상이면 **`{interrupt_id: 값}`으로 재개해야 한다**(단일 값은 `RuntimeError`) |
| 서브그래프 함정 | `Send` 대상 서브그래프는 **부모와 공유하는 키를 전부 되쓴다**(바꾸지 않았어도) → 공유 일반 키가 `InvalidUpdateError`. ~~`StateGraph(T, output_schema=리듀서 키만)`으로 해결~~ **정정(2026-09-22 실측)**: `output_schema`는 `ainvoke`·`astream(updates/values)`에서만 충분하다 — **`astream_events(v2)`(SSE 라우트의 실행 방식)에서는 `output_schema`를 줘도 같은 오류**가 난다. 컴파일 서브그래프를 `Send` 대상으로 직접 등록하지 않고 **함수 노드 안에서 `ainvoke`**하면 세 방식 모두 동작하고, 서브그래프 안 노드 이벤트도 부모 스트림에 전파된다(`parent_ids` 깊이 ≥2) — 구현 `src/orchestration/tier3_plan.py` `run_task` |
| 진행 이벤트 | `astream_events(v2)`에서 `get_stream_writer()` 출력은 **보이지 않고**, `adispatch_custom_event`는 `Send`된 서브그래프 안에서도 **보인다** — 현행 `emit_task_progress` 채널을 그대로 쓸 수 있다 |
| deepagents 조립 | `deepagents/graph.py:236-866` `create_deep_agent` → `create_agent(...)`(`:844`, `recursion_limit` 9999). 미들웨어: 할 일 목록 · 파일시스템 · 서브에이전트 `task` 도구 · 요약 · 도구 호출 패치 · (조건부) HITL. 저장소 사용: `src/orchestration/deep_agent.py:132-136`(체크포인터·서브에이전트·`interrupt_on` 없이) |
| 저장소 사용 현황 | `Send(`·`Command(`·`interrupt(`·`get_stream_writer`·`RetryPolicy`·서브그래프 노드 — **`src/` 사용 0건.** HITL은 `interrupt_before`(`src/graph.py:696-706`)뿐, 2단 병렬은 `asyncio.gather`(`agent_orchestrator.py:106`) |

### 1.2 기능 격차 — 1·2단에 있고 3단에 없는(또는 부분인) 것

> 상태: ✅ 있음 · ◐ 부분 · ❌ 없음. `file:line`은 조사에서 파일을 읽어 확인했다.

| # | 기능 | 1단 | 2단 | 3단 현재 | 격차 |
|---|---|---|---|---|---|
| **A-1** | **실시간 프로세스 조회** `process_query` | ✅ 도구 `query_live_processes` | ✅ + `_coerce_process_intent`(`intent_planner.py:118-145`) | ❌ | 라우터가 프로세스를 `data_query`(SQL)로 분류(`src/prompts/semantic_router.py:106`) → `plans/50` M4의 없는 테이블 조회(`SQL0204N`) 경로 |
| **A-2** | **호스트 단건 점검** `host_inspect`(OS 구성·자원 현황·지표 추세) | ✅ 도구(핸들러 안에서 `composite.investigation_enabled` 게이트 `host_inspect.py:153-159`, **기본 off** `config.py:1098`) | ◐ 보정으로만(`intent_planner.py:148-208`) | ❌ | 3단 도달 경로 없음 |
| **A-3** | 알람 의도 결정적 보정 | ✅ `deepagents_tools.py:293-297` | ✅ `_coerce_alarm_intent` `intent_planner.py:88-115` | ◐ 라우터 LLM 의도만 | 보정 함수 3종(알람·프로세스·점검)이 3단에 없음 |
| **B-1** | **복합 질의 분해**(`_llm_decompose` · DAG 검증 `validate_plan_dag` · 되먹임 1회) | ◐ LLM 도구 루프 | ✅ `intent_planner.py:668-718,729-803` · `schemas.py:56-108` | ◐ 순차 표지가 있을 때 `sequential_runner` 경유만 | 진입이 원문 표지 문자열 · 구조 승인 HITL 기본 on이면 미진입(`sequential_runner.py:44-46`) |
| **B-2** | **레벨 병렬·순차 실행** · 선행 게이트 · 사후 대조 · 충분성 재시도 | ◐ 도구 호출 순서 | ✅ `agent_orchestrator.py:91-124,179-213,216-258` | ◐ 순차 러너 경유 | 순차 러너는 `data_query`·`alarm_query`만(`sequential_runner.py:35`) · 분해 불성립 시 agent를 **`data_query`로 하드코딩**(`:81-92`)해 알람 의도 소실 · 충분성 재시도는 사실상 미발동 |
| **B-3** | **결과 기반 재계획** | ◐ LLM 루프 | ✅ `replanner.py:40-165`(상한 `max_replan` `config.py:1261` · 결정적 중단 5종) | ❌ | 루프 없음 |
| **B-4** | 1단 빈 응답 재개(D-092·093) · 미완 할 일 고지 | ✅ `deep_agent.py:241-259,386-439` | — | — | 도구 루프 특유 결함 대응 — §2.3 "대체" |
| **C-1** | **복합 결과 합성**(LLM 1회 · 식별자 병합 · 대체 처리) | ✅ `deep_agent.py:442-486` → `result_aggregator` | ✅ `synthesize=True`(`graph.py:426`) | ◐ 순차 러너 `synthesize=False`(`sequential_runner.py:100`) — 단순 이어 붙이기 | 합성·식별자 병합 없음 |
| **C-2** | 알람 헤드라인·진행 중 월 고지 | ❌ `_build_output_state`가 `routing_intent` 미전달(`result_aggregator.py:608-669`) | ❌ | ✅ **3단 전용**(`output_generator.py:704,998`) | 합성 경로로 옮길 때 **잃지 않게** |
| **D-1** | 원문 위치 힌트 고정 — **공용 함수** `routing/location_hints.pin_targets_to_hints`(2026-09-22 · `plans/113` F-1·F-2 · D-246) | ✅ (1단 도구 호출 = 단일 task → 원문 힌트 전체) | ✅ `subagents._apply_turn_hint_pinning`(위임 · **복합 계획은 task 단위 고정** — 종전 통째 해제 교정 · 존 없는 DB 분류 유지) | ✅ **라우터 단계** `semantic_router._pin_turn_location_hints`(위임) · **3단 계획 루프 복합 task**는 라우터 고정 집합 안에서 같은 규칙으로 좁힘(`tier3_plan._narrow_routed_targets_to_task`) | 세 단이 같은 함수·같은 규칙. **3단 계획 루프의 task별 재분류(LLM)는 P1-2 잔여** |
| **D-2** | **직전 턴 DB 승계** `_apply_db_succession`(D-206 `inherit_all`) | ✅ | ✅ `subagents.py:439-507` | ❌ | 스코프 칩(D-205)은 승계를 표시하는데 3단은 실제로 승계하지 않음 — **화면과 동작 불일치** |
| **D-3** | 지시어 호스트명 주입("해당 서버") | ✅ | ✅ `subagents.py:318-359` | ◐ 프롬프트 문맥만 | 결정적 주입 없음 |
| **D-4** | 실시간 사용률(Plan 71 · 기본 off) | ✅ | ✅ `subagents.py:1118-1148` | ❌ | `realtime_usage_lookup`이 핸들러에서만 호출 |
| **D-5** | 호스트 소재 탐색 · 가용성 사전 점검 · N대 팬아웃 · 스냅샷 캐시 | ✅ | ✅ `process_query.py:295-360,429-751` · `host_sweep.py:124-206` | ❌ | A-1과 함께 · `plans/102` §3.4 `entity_locator`와 **같은 판정 함수** 공유 |
| **D-6** | 존 역질문 | ❌ **발동 안 함**(ambient 키 누락 — N-1) | ✅ 태스크 게이트 `subagents.py:212-298` | ✅ 라우터 게이트 `semantic_router.py:400-452` | 복합 계획의 **태스크 단위** 게이트 없음 |
| **E-1** | 2단 결정적 사전 처리 — 양식 기억 조회·삭제 + `last_form_signature` · 파일 없는 양식 요청 안내 · 가동률 안내 | ❌ | ✅ `intent_planner.py:271-353` | ❌ | 3단은 `last_form_signature`를 세우지 않아 **패널 삭제가 거부**됨(`query.py:963-975`) |
| **E-2** | 캐시 관리·유사어·일반 추론의 답변 기록(AIMessage) · 일반 추론 고정 답(`direct_response`) | ✅ | ✅ `result_aggregator.py:409-427` · `subagents.py:1008-1019` | ◐ 노드는 있으나 기록·고정 답 없음 | 멀티턴 문맥 결손 |
| **F-1** | 진행 이벤트(태스크 시작·종료·총수 · 단계) | ✅ | ✅ `agent_orchestrator.py:96,100,119` | ◐ 노드 시작·완료 + 일부 단계만 | `multi_db_executor`·`query_generator`·`fault_diagnosis` 단계 없음 · `_STREAM_KNOWN_NODES`(`query.py:144-158`)에 `sequential_runner`·`structure_approval_gate` 없음 |
| **Y-1** | SQL 승인 HITL(`ENABLE_SQL_APPROVAL`, 운영 off) | ❌ **고지 없이 우회**(`subagents.py:586-603`) | ❌ 우회 | ◐ 단일 DB 경로만 · 멀티 DB는 건너뜀(`graph.py:586-587`) | 전 경로 일관 승인 없음. **구조 승인 HITL은 `plans/104`로 질의 경로에서 제거** |
| **Y-2** | 제어 평면 LLM 분리(오케스트레이터 vLLM·Gemini·MLX ↔ 워커) | ✅ `llm.py:109-130` | — | ❌ 워커 LLM 하나 | 계획·재계획 LLM 평면 선택(G-2) |

**N-1 · 1단을 정답으로 삼으면 안 되는 이유(실측)** — `_AMBIENT_KEYS`(`src/orchestration/deep_agent.py:141-162`)에 `user_query`·`messages`·`uploaded_file`·`form_fill_answers`·`zone_clarification_allowed`·`pending_synonym_*`·`is_composite`·`resolved_limit`이 **없다**.
그래서 1단은 존 역질문·양식 답변 덮어쓰기·원문 LIMIT·일반 추론 대화 이력 일부가 **조용히 꺼져 있다.** 이 계획은 이 결손을 재현하지 않는다(§0.3-2). 1단 쪽 결손은 1단 핸들러가 같은 서브그래프를 부르게 되면(P4-3) 함께 사라진다.

### 1.3 저장소 차단 요인 — LangGraph가 아니라 우리 코드

| # | 차단 | `file:line` | 증상(토이 그래프 실측 포함) |
|---|---|---|---|
| **K-1** | **노드 추적 프록시** | `src/observability/graph_proxy.py:45-64` → `trace_collector.py:248-277` `traced()`의 `async def _wrapper(state, *args, **kwargs)` · `OBS_TRACE_ENABLED` 기본 **True**(`config.py:629`, 운영 `.env` 미설정) | 컴파일된 서브그래프를 넘기면 실행 시 `TypeError: 'CompiledStateGraph' object is not callable`. `config`를 선언한 노드는 주입을 못 받는다(`get_config()`는 동작). `GraphInterrupt`가 `Exception` 하위라 **HITL 일시정지가 매번 ERROR 트레이스**로 남는다. **정정(2026-09-22 HEAD 실측)**: `config` 주입은 이미 동작한다 — 래퍼의 `__wrapped__`로 LangGraph가 원본 시그니처를 읽어 `**kwargs`로 넘긴다. 남은 결함은 서브그래프 등록 불가·인터럽트 ERROR 기록 2건이었고 P0-1에서 해소 |
| **K-2** | **상태 리듀서 부재** | `src/state.py:79-294` — 리듀서는 `messages`(`:216`) 하나. `task_plan`·`task_results`·`db_results`·`dependency_notes`·`prior_rows`·`replan_count` 등 전부 일반 키 | 병렬 `Send` 대상이 같은 키를 쓰면 `InvalidUpdateError` |
| **K-3** | **SSE 종료 판정** | `src/api/routes/query.py:1476-1549`(첫 `on_chain_end` 중 출력에 `final_response`가 있으면 `done` 후 return) · 폴백 `:1552-1559`(`ainvoke` **재실행**) | 서브그래프 노드·서브그래프 종료가 루트보다 먼저 끝난다(루트·서브그래프 기본 이름 모두 `"LangGraph"`) → 태스크가 `final_response`를 내면 **부분 답으로 스트림 종료**. 인터럽트 시 루트 종료 출력에 `final_response`·`__interrupt__`가 없어 폴백 `ainvoke`가 **처음부터 다시 돌고 다시 멈춘다** |
| **K-4** | **HITL 재개 방식** | `query.py:466-475` `_resolve_turn_approval` · `:689-722` `_build_turn_input_state` · `_get_checkpoint_state`(`:288-306`)는 `.values`만 읽음 | 승인 턴이 `Command(resume=…)`가 아니라 **새 dict 입력** → 멈춘 스레드에서 **START부터 새 실행**(토이 실측). 노드가 `approval_action`을 스스로 읽는 방식 |
| **K-5** | 턴 간 누수(2단) | `state.py:326-362` `create_followup_input`이 `task_plan`·`task_results`·`replan_count`·`replan_history`를 초기화하지 않음 | 한 턴이 `max_replan`에 닿으면 **그 스레드의 이후 턴은 재계획하지 않는다**(`replanner.py:64-72`) |

### 1.4 숨은 결합 — 옮길 때 깨지는 것

| # | 결합 | 근거 | 대응 |
|---|---|---|---|
| J-1 | 전역 설정 캐시·임포트 시점 스냅샷 | `load_config()` lru_cache 직접 호출 `subagents.py:659-660,735,899` · `process_query.py:204,416,461,558,837` · 레지스트리 값 임포트 시 캡처 `subagents.py:77` · `process_query.py:73` | 노드화 시 주입 `app_config` 사용으로 정리(테스트 설정 누수 방지) |
| J-2 | 프로세스 전역 가변 상태 | `process_query.py:383`(`_inflight_locks`) · `:409-410`(스냅샷 캐시) · `host_sweep.py:41`(`_CACHE`) | 오늘도 `asyncio.gather` 병렬에서 공유 — `Send` 병렬에서도 **같은 조건**. 신규 경합 없음을 테스트로 고정 |
| J-3 | 진행 이벤트는 부모 실행 문맥 필요 | `src/utils/progress_events.py:24-31`(문맥 없으면 조용히 버림) | 서브그래프 노드 안 호출은 문맥 있음(실측) — 그래프 밖 함수 호출로 남는 경로만 점검 |
| J-4 | 계층 규칙 | `src/routing`(infrastructure)·`src/nodes`(application)는 `src/orchestration`을 import할 수 없음(`scripts/arch_check.py`) | 계획·팬아웃·재계획·에이전트 노드는 **orchestration 계층**에 두고 `graph.py`가 배선 |
| J-5 | 태스크 번호 경합(1단 · 추정 · 미실행) | `deepagents_tools.py:299` `len(collector)+1`을 await 전에 계산 | 3단은 계획 노드가 `task_id`를 확정하므로 해당 없음 |
| J-6 | 라우터 `unknown` 의도 경로 | `semantic_router.py:294-316`이 `unknown` + `final_response`를 내지만 `route_after_semantic_router`(`graph.py:133-151`)는 이를 `schema_analyzer`로 보낸다(그래프 경로 테스트 없음) | P2에서 분기 표에 `unknown → END` 명시 |

---

## 2. 설계 원칙

| # | 원칙 | 이 계획에서 |
|---|---|---|
| **Q1** | **워크플로로 옮긴다**(`plans/102` P10) | 자율 도구 루프 → 계획 · 팬아웃 · 합류 · 재계획(상한) · 합성의 명시 그래프 |
| **Q2** | **하나의 실행 단위** | 데이터 조회 파이프라인은 태스크 서브그래프 하나. 3단 단일·복합, 1·2단 핸들러가 같은 서브그래프 호출(D-053 · D-225 ⑥) |
| **Q3** | **결정은 구조화 출력, 판단은 코드**(D-004 · D-035) | 복합 진입·에이전트 선택은 라우터·계획 LLM의 구조화 출력. 게이트·스코프·상한·합류는 결정적 코드 |
| **Q4** | **병렬 쓰기는 전용 팬인 키로만** | `AgentState` 기존 키에 리듀서를 달지 않는다(2·4단 의미 변경 방지). 3단 팬인 전용 키 1개 + 합류 노드가 기존 키에 **단일 기록** |
| **Q5** | **HITL은 LangGraph 재개로** | `interrupt()`(부작용 없는 작은 게이트 노드) + `Command(resume={id: 값})`. 상태 플래그를 읽고 처음부터 다시 도는 방식은 폐기 |
| **Q6** | **단계적·플래그·비트 동일** | 플래그 off면 현행 3단 그래프 노드 구성·분기·프롬프트 동일. 단계마다 검증 후 다음 단계 |
| **Q7** | **승격-폐기 동반**(D-161 ①) | 새 경로가 기본이 되는 단계에서 대체된 코드(`sequential_runner`·함수 호출형 단일 DB 파이프라인)의 삭제를 같은 결정에 포함 |

### 2.3 이식하지 않는 것(대체)

| 1단 요소 | 이식하지 않는 이유 | 3단 대체 |
|---|---|---|
| 자율 도구 호출 루프 자체 | Q1 | 계획 → 팬아웃 → 재계획 루프 |
| 파일시스템 도구 · 범용 `task` 서브에이전트 | 1단도 실제 작업에 쓰지 않음(§1.1) | — |
| 빈 응답 재개(D-092·093) · 미완 할 일 고지 | 오케스트레이터가 도구 루프 중 빈 응답을 내는 결함 대응 — 3단에는 그 루프가 없다 | 계획 LLM 실패는 기존 `_enforce_plan_contract` 되먹임 1회 → 단일 태스크 폴백 + **사유 노트**. 재계획 상한 도달은 답변에 **고지** |
| 도구 결과 절단(`ORCHESTRATOR_MAX_TOOL_RESULT_TOKENS`) | 3단은 원시 결과를 LLM에 도구 메시지로 보이지 않는다 | 재계획 입력은 기존 `replanner` 요약 규칙 |
| 1단 스코프 휴리스틱 G2(참조어)·G3(순위어) | 명시 DAG가 없는 1단의 보완책 — 키워드 판정 | 계획의 `input_from` DAG + 기존 D-203 게이트(G-4 확인) |

---

## 3. 설계

### 3.1 목표 그래프(3단 · 플래그 전부 on)

```
START → context_resolver → input_parser → field_mapper → semantic_router
semantic_router ─┬─ 캐시 관리 · 유사어 · 일반 추론 · 장애 진단 · 존 역질문/unknown(END)      (현행 유지)
                 ├─ 사전 안내(양식 기억 조회·삭제 · 파일 없는 양식 요청 · 가동률)     → END   (E-1)
                 ├─ [plans/102 entity_locator]                                               (102 플래그)
                 └─ plan  ─→ dispatch ─(Send × 준비된 레벨)→ task_run[서브그래프] ─→ join(defer)
                                ↑                                                    │
                                └──────── replan (상한·결정적 중단) ←───────────────┤
                                                                                     └→ finalize → END
task_run 서브그래프(에이전트별 분기):
  resolve_targets(D-1 고정 · D-2 승계 · D-3 지시어 · D-6 태스크 존 게이트 · D-4 실시간 사용률)
    ├ data_query/alarm_query: 단일 DB[schema_analyzer → query_generator → query_validator → (sql_approval: interrupt) → query_executor → result_organizer]
    │                         | 멀티 DB[multi_db_executor → result_merger → result_organizer]
    ├ process_query: 소재 탐색 → 가용성 점검 → 실시간 API (기존 핸들러 함수)
    ├ host_inspect:  기존 핸들러 함수(플래그 게이트 유지)
    └ pack_outcome → task_outcomes(팬인 키)
```

- **단일 의도도 같은 길**(G-5): 라우터가 계획 불필요로 판단하면 `plan`이 **태스크 1개**를 결정적으로 만들고 같은 서브그래프를 탄다.
  최종 답은 태스크 1개면 기존 `output_generator`(양식 채우기·알람 헤드라인 등 3단 고유 기능 보존), 2개 이상이면 `result_aggregator(synthesize=True)` — 이때 `routing_intent`를 출력 상태에 **전달**한다(C-2).
- **태스크 서브그래프 안에는 `output_generator`를 두지 않는다** — 태스크가 `final_response`를 내지 않게 해 SSE 조기 종료(K-3)를 구조적으로 막는다.

### 3.2 라우터 — 계획 진입과 새 의도

| 변경 | 내용 |
|---|---|
| 구조화 출력 | `RouterDecision`에 **`needs_plan: bool`**("하위 조회가 둘 이상 필요하거나 앞 결과가 뒤 조회를 한정하는가"). `plans/102`의 `chain`(교차 시스템 순서)이 비어 있지 않으면 `needs_plan`으로 간주 |
| 의도 추가 | `process_query`(실시간 프로세스) · `host_inspect`(조사 플래그 `COMPOSITE_INVESTIGATION_ENABLED` on일 때만 프롬프트 노출 — `fault_diagnosis` 옵트인과 같은 방식) |
| 프롬프트 | `_S_INTENT_CLASSES`의 `data_query` 정의에서 "프로세스" 제거 → `process_query`로(플래그 on일 때만 — off면 프롬프트 바이트 동일) |
| 분기 | `unknown` → END 명시(J-6) · 사전 안내 → END · 그 외 데이터 의도 → `plan` |
| 순차 표지 | `has_sequential_marker`는 **전환기 동안 OR 신호로만** 유지하고 동등성 검증(P5) 뒤 삭제(G-1 · D-161 ①) |

### 3.3 계획·실행·재계획 — 2단 함수를 노드로

| 노드 | 계층·위치 | 재사용 함수 | 새로 하는 일 |
|---|---|---|---|
| `plan` | orchestration · `src/orchestration/tier3_plan.py` | `intent_planner`의 사전 처리(①~③.7) · `_llm_decompose`(DAG 검증·되먹임 포함) · `_coerce_*` 보정 3종(G-3) | 단일 의도면 결정적 1태스크 · `task_id` 확정 · `replan_count`·팬인 키 **턴 초기화** |
| `dispatch` | orchestration · 조건부 엣지 함수 | `topological_levels` · `_gate_level` · `assess_prior_dependency` · `_make_isolated_input` | 준비된 레벨의 태스크마다 `Send("task_run", 격리 입력)`. 선행 게이트에 걸린 태스크는 `Send` 없이 `skip_result`를 팬인 키에 직접 기록 |
| `task_run` | 컴파일된 서브그래프 · `StateGraph(AgentState, output_schema=TaskOutcomeState)` | 기존 노드 함수들 · `run_process_query` · `run_host_inspect` · `realtime_usage_lookup` | `pack_outcome`이 결과를 `task_outcomes`(리듀서 `operator.add`)에만 기록 |
| `join` | orchestration · `defer=True` | `apply_scope_postcheck` · 충분성 판정·재시도 1회 · `_extract_identity_rows` | 팬인 키 → `task_results`·`prior_rows`·`dependency_notes` **단일 기록** · 다음 레벨 있으면 `dispatch`, 없으면 `replan` |
| `replan` | orchestration | `replanner`(결정적 중단 5종) | **명시 상한**(`max_replan`) · 상한 도달·무익 재시도 중단 사유를 노트로 |
| `finalize` | orchestration | `result_aggregator(synthesize=True)` · `output_generator` | 태스크 1개 → `output_generator`, 2개 이상 → 합성 + `routing_intent` 전달 |

**상태 추가(Q4)** — `AgentState`에 `task_outcomes: Annotated[list[dict], operator.add]` **하나만** 추가. 턴 시작 입력(`create_initial_state`·`create_followup_input`)에서 `Overwrite([])`로 비우고,
같은 자리에서 `task_plan`·`task_results`·`replan_count`·`replan_history`도 초기화한다(K-5 — 2단 누수도 함께 해소).

> **구현(2026-09-22) — 초기화 자리를 바꿨다.** 턴 초기화는 입력 델타가 아니라 **루프 입구 `plan` 노드**가 `Overwrite([])`로 한다 — 이 키를 읽는 것은 같은 루프의 `join`뿐이고, 입력 델타는 평범한 값(JSON 직렬화 가능)으로 둔다. `task_plan`·`task_results`·`replan_count`·`replan_history`도 `plan`이 비운다(**루프 한정**). 입력 델타(`create_followup_input`)에는 라우터 신호 `needs_plan: None`만 더했다(사전 처리 분기가 이 값을 쓰지 않아 직전 턴 값이 남으면 양식·존 선택 턴이 루프로 샌다). **2단 누수 K-5는 해소하지 않았다** — `create_followup_input`에서 2단 키를 비우면 2단 동작이 바뀌는데, 2단 전용 결함은 `plans/108` G-2 · `plans/111` G-5(사용자 확정)가 보류한다.

### 3.3.1 `plans/111` 델타 편입 (2026-09-21 · 111 G-2 확정 — 노드 구성 정본은 이 계획 하나)

> 근거는 `plans/111` §2(run `20260918-182507` 복합 계획 45턴 재집계)이고 여기에는 **설계 변경만** 적는다(D-053).
> 111 G-1·G-3·G-4 확정(2026-09-21 · 권고안)을 이 계획의 노드에 옮긴 것이다.

| 노드 | 더하는 것 | 막는 실측(111) | 이미 있는 부품 |
|---|---|---|---|
| **`plan`** | 분해 출력 계약을 **자유문 `sub_query` → 원문 조각 `spans`**로(111 G-1). `sub_query`는 조각을 원문 순서로 이어 만든다. 나눌지 말지 규칙(에이전트·산출물·실행 그룹·조건 분기일 때만 나눔)을 계획 프롬프트에 둔다 | §2.5 발명·SQL 주입 44건 · §2.2 과·미분해 | `COMPOSITE_TASK_FRAME_ENABLED` 경로(`intent_planner._apply_task_frames` · `SpanDecomposedPlan` · 프롬프트 계약 절) — **2단 `_llm_decompose`에 랜딩**(기본 off). `plan` 노드는 이 함수를 그대로 부른다 |
| **`normalize`**(신규 · `plan`의 **단일 출구**) | 사전 처리 ①~③.7의 조기 반환을 포함한 **모든 분기**가 지난다 — G-3 교정 3종 · `verify_task_frames`(발명 금지·숫자 합집합 커버·SQL 금지) · 불합격이면 원문 단일 task + 사유. **G-3의 "계획 경로에만"을 "계획 노드의 출구 전부에"로 좁혀 명시**한다 | §2.4 존 선택 재진입 교정 우회 27건 | `_normalize_plan_exit`(2단 · 플래그) · `src/domain/task_frame.py` |
| **`task_run` 첫 노드 `task_prompt`**(신규) | task 텍스트를 107 렌더러로 찍는다. 선행 결과 스코프는 **값 목록이 아니라 참조 블록**으로 싣는다(값은 D-086·D-095 결정적 주입이 SQL에 넣는다) | §2.5 직전 엔티티 전량 주입 | `render_task_query` · 107 `render_canonical_block` |
| **`join`**(변경) | **0행 선행 → 의존 task 미실행 + 사유**를 **기본 on**(111 G-4) | E-06 · R1-08 · SYN-I-06 | `_gate_level` · `assess_prior_dependency`(D-203 게이트) |
| **`replan`**(변경) | **task 실패(산문·검증 소진·0행)를 재계획 입력에서 뺀다**(111 G-3) — task 서브그래프가 사유와 함께 종결한다(108 CU-A2 경로). 재계획은 ①선행 결과가 후속 조건을 바꾼 경우 ②요구 산출물 누락만. 새 task 텍스트에 SQL 금지 | §2.3 재계획 28턴(복구 0) | `replanner._filter_futile_retries` 위에 입력 범위 규칙 |

### 3.4 HITL — `interrupt()` + `Command(resume)` (G-6)

- **SQL 승인**: 태스크 서브그래프의 `sql_approval` 게이트 노드(부작용 없음)에서 `interrupt({task_id, db_id, sql, 요약})`. 병렬 태스크가 동시에 멈추면 **대기 목록**을 한 번에 보인다.
- **라우트**: 스트림 종료 후 `aget_state(config).interrupts`로 대기를 감지해 `awaiting_approval` 이벤트(인터럽트 id별 페이로드)를 보낸다.
  승인 턴은 `_build_turn_input_state`가 새 dict 대신 **`Command(resume={interrupt_id: {"action": …, "sql": …}})`**를 만든다. 결정적 승인어 판정(`query.py:310-363`)은 그대로 쓴다.
- **구조 승인**: 질의 경로에서 **제거**(`plans/104`). 따라서 `sequential_entry`의 HITL 차단(`sequential_runner.py:44-46`)이 막던 문제는 이 계획의 새 경로에 존재하지 않는다.
- **기존 `interrupt_before` 방식 승인**(`graph.py:696-706` · `approval_gate`)은 플래그 on에서 새 게이트로 대체되고, P4에서 삭제한다.

### 3.5 기반 계약 — 차단 요인 해소(P0)

| # | 대상 | 변경 | 검증 |
|---|---|---|---|
| K-1 | `graph_proxy.py` · `trace_collector.py` | Runnable(컴파일된 그래프 포함)은 **감싸지 않고 원본 등록** · 래퍼는 `config`를 받는 노드에 `config`를 전달 · `GraphBubbleUp`(인터럽트)은 오류가 아닌 `node.interrupt` 단계로 기록 | 토이 그래프를 프록시 경유로 재실행 — 서브그래프·`config` 주입·인터럽트 트레이스 |
| K-2 | `src/state.py` | §3.3 팬인 키 1개 + 턴 초기화 | 병렬 `Send` 3개에서 `InvalidUpdateError` 0 · 두 번째 턴에 앞 턴 결과 0 |
| K-3 | `query.py` SSE | **루트 실행 종료만** `done`으로 판정(루트 그래프에 고유 `name` 부여 + 이벤트의 `parent_ids` 빈 목록 — 착수 시 실측으로 확정) · 폴백 `ainvoke` 재실행 제거(대기 인터럽트면 재실행 금지) · `_STREAM_KNOWN_NODES`에 신규 노드 · 토큰 필터는 `finalize` 계열만 | 서브그래프 종료로 스트림이 닫히지 않음 · `done` 정확히 1회 · 인터럽트 시 `awaiting_approval` 1회 · 재실행 0 |
| K-4 | `query.py` 승인 턴 | §3.4 | 병렬 인터럽트 2건 각각 승인·거절 · 끝난 형제 태스크 재실행 0 |

### 3.6 1·2단과의 관계

- **1단(부가)**: P4에서 `run_data_query_pipeline`의 단일 DB·멀티 DB 실행부가 **같은 `task_run` 서브그래프를 `ainvoke`**하도록 바꾼다 — 1단 도구도 같은 구현을 탄다(D-225 ⑥). ambient 결손(H-1)은 서브그래프 입력을 `_make_isolated_input` 정본으로 만들며 함께 정리한다.
- **2단 배선**: 3단이 계획·재계획 루프를 네이티브로 갖게 되면 2단 배선은 **중복**이다. **이 계획에서 폐기하지 않는다** — P5 완료 후 D-161 ② 4항 실측(운영 설정·가용성·최근 활동·역방향 의존)으로 별도 판단한다. 2단 **모듈**은 3단 노드가 재사용한다.

### 3.7 플래그 — 기본 off · 기동 시 1회 해석 · 만료일은 D-226 등재 시 확정(D-161 ①)

| 플래그 | 범위 | off일 때 |
|---|---|---|
| `TIER3_TASK_GRAPH_ENABLED` | P1 — 태스크 서브그래프 · `process_query`·`host_inspect` 의도·노드 · 사전 안내 | 현행 3단 그래프 |
| `TIER3_PLAN_LOOP_ENABLED` | P2 — `needs_plan` · plan/dispatch/join/replan/finalize (P1 필요) | 현행 분기(순차 러너 포함) |
| `TIER3_INTERRUPT_HITL_ENABLED` | P3 — `interrupt()` 승인 · 라우트 재개 (P1 필요) | 현행 `interrupt_before` |

P0(기반 계약)은 플래그 없이 랜딩한다 — 프록시·SSE·턴 초기화는 **현행 동작을 바꾸지 않는 교정**이어야 하고, 그 사실을 기존 테스트 전건 통과로 보인다.

> **구현(2026-09-22)**: 만든 플래그는 **`TIER3_PLAN_LOOP_ENABLED` 하나**다(`AppConfig.tier3_plan_loop_enabled` · 기본 off · `config/settings_help/general.yaml` 등재). task 서브그래프(P1-1)는 이 플래그로 등록된다 — 단독으로 켤 대상(P1-3 의도 2종 · P1-4 사전 안내)이 아직 없어 `TIER3_TASK_GRAPH_ENABLED`를 만들면 효과 없는 설정이 된다(P1-3·P1-4 착수 때 만든다). `TIER3_INTERRUPT_HITL_ENABLED`는 P3와 함께.

---

## 4. 작업 분해

| WU | 단계 | 작업 | 선행 | verify |
|---|---|---|---|---|
| **P-0** | — | G-1~G-7 확정 | — | §7 기록 |
| **P0-1** | 기반 | K-1 추적 프록시 서브그래프·`config`·인터럽트 호환 | — | 토이 그래프 프록시 경유 통과 · 기존 트레이스 테스트 전건 |
| **P0-2** | 기반 | K-2 팬인 키 · 턴 초기화(2단 누수 K-5 포함) | — | 병렬 쓰기 오류 0 · 턴 격리 테스트 · 2단 기존 테스트 전건 |
| **P0-3** | 기반 | K-3 SSE 루트 종료 판정 · 폴백 재실행 제거 · 인터럽트 감지 이벤트 | — | `tests/test_api/test_stream_nested_events.py` 확장 — 중첩 종료·인터럽트 시나리오 |
| **P1-1** | 태스크 | `task_run` 서브그래프 — 데이터·알람 파이프라인(기존 노드 조립) · `pack_outcome` · `output_schema` · **첫 노드 `task_prompt`(§3.3.1 · 111 C-4)** | P0 | 단일 DB·멀티 DB 결과가 현행 3단 노드 체인과 동일(골든 비교) |
| **P1-2** | 태스크 | `resolve_targets` — D-1 고정 · D-2 승계 · D-3 지시어 · D-6 태스크 존 게이트 · D-4 실시간 사용률 | P1-1 | `tests/test_orchestration/test_turn_hint_pinning.py`·`test_zone_post_gate.py`·`tests/test_multiturn/*` 계열을 3단 경로로 재실행 |
| **P1-3** | 태스크 | `process_query`·`host_inspect` 노드 · 라우터 의도 2종(플래그 종속 노출) · 프롬프트 | P1-1 | 프로세스 질의가 SQL로 가지 않음 · `host_inspect`는 `COMPOSITE_INVESTIGATION_ENABLED` off면 노출 0 · 플래그 off 라우터 골든 무변화 |
| **P1-4** | 태스크 | E-1 사전 안내 3종 · E-2 답변 기록·고정 답 | — | 양식 패널 삭제가 3단에서 동작 · 멀티턴 이력 테스트 |
| **P2-1** | 루프 | 라우터 `needs_plan` · `plan` 노드(사전 처리·분해·보정·1태스크 결정적) · **`normalize` 단일 출구 + 원문 조각 계약(§3.3.1 · 111 C-3·C-4)** | P1 · G-1·G-2·G-3 | 표지 없는 복합 질의 진입 · 단일 의도 1태스크 · D-004 리뷰 체크(원문 문자열 판정 추가 0) |
| **P2-2** | 루프 | `dispatch`(`Send`) · `join`(`defer`) · 게이트·사후 대조·충분성 재시도 · **0행 의존 게이트 기본 on(111 G-4)** | P2-1 | 레벨 병렬 실행 · 선행 실패·0건·식별자 없음 게이트 · `tests/test_composite/*` 3단 재실행 |
| **P2-3** | 루프 | `replan`(명시 상한·결정적 중단 · **task 실패 입력 제외 111 G-3**) · `finalize`(합성 + `routing_intent` 전달) | P2-2 | `tests/test_orchestration/test_replanner.py`·`test_result_aggregator.py` 3단 재실행 · 알람 헤드라인 유지(C-2) |
| **P2-4** | 루프 | F-1 진행 이벤트 — 태스크 총수·단계(`multi_db_executor`·`query_generator`·`fault_diagnosis`) | P2-2 | 이벤트 순서·필드가 D-204 페이로드와 동일 |
| **P3-1** | HITL | `sql_approval` `interrupt()` 게이트 · 라우트 `Command(resume)` · 다중 대기 목록 | P0-3 · P1-1 · G-6 | 병렬 인터럽트 2건 · 재실행 0 · 거절 시 사유 · 승인어 판정 재사용 |
| **P4-1** | 통합 | 단일 의도 데이터 조회를 태스크 서브그래프로 일원화(G-5) · 기존 3단 직결 체인 배선 제거 | P2 · P3 | 기존 3단 골든·시나리오 무회귀 |
| **P4-2** | 통합 | **삭제** — `sequential_runner`(D-203 3·4단 러너) · 순차 표지 진입 · `interrupt_before` 승인 배선(D-161 ①) | P4-1 · P5-1 | 참조 0 grep · 관련 테스트 이관 |
| **P4-3** | 통합 | 1단 핸들러 `run_data_query_pipeline`이 `task_run` 서브그래프 호출 · 함수 호출형 `_run_single_db_pipeline` 삭제 | P4-1 | 1단 기동 시나리오 판정이 3단과 일치(`plans/102` X-14와 합류) |
| **P5-1** | 검증 | **동등성 매트릭스** — §1.2 격차 표 행마다 3단 테스트 1건 이상 · `plans/94` 시나리오를 1단·3단 기동으로 각각 실행해 판정 비교 | P2 · P3 | §5 |
| **P5-2** | 검증 | 응답 시간 — 단순 <10s · 복합 <30s 목표 대비 1단 기준선과 비교 | P5-1 | 시나리오 리포트 |
| **P5-3** | 문서 | D-226 등재 · `docs/21`(사다리 · `plans/102` L-4와 합류) · `CLAUDE.md` LangGraph 노드 절 · INDEX | 전건 | 링크·번호 실존 |

**순서 불변식**
- **P0은 플래그 없이 먼저**, 기존 테스트 전건 통과로 "동작 무변경"을 보인 뒤 P1에 들어간다.
- **P4(삭제·일원화)는 P5-1 동등성 판정 통과 뒤**다 — 판정 전에 대체 경로를 지우지 않는다.
- **`plans/102` L-5(운영 `.env` 3단 전환)는 이 계획 P5 완료 뒤**다.
- 실 LLM 실행: 현재 로컬 `.env`는 워커·오케스트레이터 모두 `mlx`(D-222 비과금)다. **실행 직전 `scripts/scenario --preflight`로 과금 평면을 확인**하고, 과금 provider면 D-127 건별 승인 + `RUN_E2E=1` 뒤에만 돌린다.
- 병행 세션: `deep_agent.py`·`config.py`·`llm.py`는 `plans/100` 세션이 미커밋 수정 중 — P0·P4-3 착수 전 `git status`·`ListAgents` 확인.

### 4.1 구현 현황 (2026-09-22 · `plans/111` C-4·C-5 범위 — 111 델타를 실현하는 데 필요한 WU까지)

> 상태: ✅ 랜딩 · ◐ 부분 · ❌ 미착수. 코드는 전부 **`TIER3_PLAN_LOOP_ENABLED`(기본 off) 뒤**다 — P0만 플래그 없이 랜딩했다.
> 새 판단 로직은 없다 — 2단 함수를 노드로 옮겼다(D-053): `_plan_turn` · `_normalize_plan_exit` · `_coerce_host_inspect_intent` · `_apply_task_frames` · `_gate_level`(+ 추출 `_task_verdict`) · `_postcheck_result`(추출) · `_make_isolated_input` · `_pack_pipeline_result`(추출) · `replanner` · `result_aggregator`.

| WU | 상태 | 산출물 | 남긴 것과 이유 |
|---|---|---|---|
| **P0-1** K-1 | ✅ | `graph_proxy.TracedGraph.add_node` — Runnable(컴파일 서브그래프)은 감싸지 않음 · `trace_collector.traced` — `GraphBubbleUp`을 `node.interrupt`(INFO)로 기록 | `config` 주입은 이미 동작해 손대지 않았다(§1.3 정정) |
| **P0-2** K-2 | ✅(루프 한정) | `AgentState.task_outcomes`(`operator.add` — 유일한 팬인 키) · `needs_plan` · 턴 초기화는 `plan` 노드(§3.3 구현 주) | **K-5(2단 누수) 미해소** — 2단 전용 결함 보류(108 G-2 · 111 G-5) |
| **P0-3** K-3 | ◐ | SSE 두 라우트(`/query/stream` · `/query/file/stream`)가 `parent_ids` 깊이 ≥2(서브그래프 안) 종료를 `done` 판정에서 뺀다(`_is_subgraph_event`) · `_STREAM_KNOWN_NODES`에 루프 노드 7종 · 진행 요약 매핑 | **인터럽트 감지(`awaiting_approval`)·폴백 `ainvoke` 재실행 제거 미착수** — 인터럽트를 내는 경로가 P3(SQL 승인 `interrupt()`)뿐이고, 루프는 SQL 승인이 켜지면 진입하지 않는다. 현행 `interrupt_before` 승인 흐름을 바꾸면 P0 "무변경" 원칙에 걸린다 |
| **P1-1** | ✅ | `task_run` 서브그래프(`graph._build_task_run_graph`): `task_prompt` → 데이터·알람은 3단 직결 체인과 **같은 노드·같은 분기 함수**(검증 회귀 · 산문 조기 종결 · 실행 회귀 · 데이터 부족 회귀) · 멀티 DB는 `multi_db_executor → result_merger → result_organizer` · 그 외 담당은 2단 레지스트리 핸들러(`task_handler`) → `pack_outcome`. 종결은 `final_response`를 쓰지 않는 `task_error`·`pack_outcome` | 서브그래프는 **함수 노드 안 `ainvoke`**(§1.1 정정). SQL 승인 게이트 없음(루프 미진입으로 대신). **골든 비교(현행 3단 체인과 결과 동일) 미실시** — 대역 노드로 배선만 검증했다 |
| P1-2 | ❌ | — | task는 이번 턴 라우터의 조회 대상을 이어 받는다(`_ROUTING_KEYS`) · 사전 처리가 DB를 고정한 task(`db_ids`)만 2단과 같은 정규화. task별 재분류·위치 힌트 고정·승계·존 게이트·실시간 사용률은 없다. **(2026-09-22 부기)** 라우터 단계 D-1 고정은 `plans/113` F-1로 랜딩 — task가 고정된 대상을 상속하므로 "공동존" 류 턴 전역 힌트는 task에도 닿는다(113 F-3 단위 확인). **(v3)** task별 위치가 다른 복합 계획의 고정도 `plans/113` F-2로 랜딩(`_narrow_routed_targets_to_task` — 라우터 고정 집합 안에서만 좁힘). task별 재분류·승계·지시어·실시간 사용률·태스크 존 게이트는 여기 잔여 |
| P1-3 · P1-4 | ❌ | — | 111 델타와 무관 |
| **P2-1** | ✅ | 라우터 `needs_plan`(플래그 on일 때만 프롬프트 말미 절 + 구조화 서브클래스 `PlanRouterDecision`·`OwnershipPlanRouterDecision` · `chain` 비면 안 됨 → 계획 필요) · 진입 `plan_loop_entry`(데이터·알람 의도 + `needs_plan` 또는 순차 표지 · SQL 승인·양식 제외) · `plan`(= `_plan_turn`) · **`normalize` 단일 출구**(111 D-1) | 2단 분리 라우터(`ROUTER_TWO_STAGE_ENABLED`) 경로는 `needs_plan`을 내지 않는다 — 순차 표지 OR만 남는다 |
| **P2-2** | ◐ | `dispatch`(다음 레벨 · `_gate_level` · `in_progress` 표시) → 분기 함수 `route_dispatch`가 `Send(task_run)` · `join`(`defer=True` · 사후 대조) · **0행 의존 게이트 = D-203 게이트 기본 on 그대로**(111 G-4) | **충족도 재시도(78 W5) 미이식** — 대상 주입(`prior_targets`)은 `process_query`류에만 생기고 111 델타와 무관 |
| **P2-3** | ◐ | `replan`(111 D-3 — 입력 = 행을 돌려준 task · 행 있는 task가 없으면 LLM 0회 · 계획에 있는 담당의 독립 후속·SQL 문장 후속 제거 · 전체 계획 기준 재채번 · 상한 도달 노트) · `finalize` = `result_aggregator(synthesize=True)` | **C-2(`routing_intent` 전달 — 알람 헤드라인) 미착수** — 단일 의도는 루프에 들어오지 않아(G-5) 3단 고유 출력이 유지되지만, 복합 계획의 합성 경로에는 아직 없다 |
| P2-4 | ◐ | `dispatch`·`join`이 task 시작·종료 이벤트(`emit_task_progress` — 2단과 같은 페이로드) | 단계 이벤트(`multi_db_executor`·`query_generator`) 없음 |
| P3 · P4 · P5 | ❌ | — | 범위 밖 |

**검증** — `tests/test_orchestration/test_plan103_tier3_plan_loop.py` 22건(배선 · 진입 · 라우터 off 바이트 동일/on 절 추가 · normalize · 0행 게이트 · task_prompt · replan 입력 범위 · 그래프 실행 **`ainvoke`·`astream_events` 두 방식** · 턴 격리) · `tests/test_api/test_stream_subgraph_done.py` 2건 · `tests/test_observability/test_graph_proxy.py` +3건 · 전체 스위트 회귀 0(구조 단언 1건 갱신 — `test_two_stage.py`: 라우터 응답 검증부가 `_classify_parsed`로 추출됨).
**실 노드 통합 스모크**(저장소 밖 스크립트 · 2026-09-22): 계획·라우터·입력 파서만 대역, `schema_analyzer`~`result_organizer`는 **실제 노드**, DB는 로컬 샌드박스(MCP 9099 · 읽기 전용), LLM은 스크립트 대역(네트워크·과금 0), 실행은 `astream_events` — t1 알람 2행 → t2 SQL 프롬프트에 선행 스코프 블록 주입 · 2행 → 합성(`result_aggregator` 식별자 병합) · 경과 노트 1건 · SSE 종료 판정 `finalize` 1회 · 루트 노드 순서 `plan→normalize→dispatch→task_run→join→dispatch→task_run→join→replan→finalize`. **실 LLM 실행 0회**(M-4 미측정).

---

## 5. 성공 기준

1. **격차 0** — §1.2 표의 모든 행이 3단에서 ✅ 이거나, §2.3 "대체"로 사유가 확정됐다. 행마다 3단 경로 테스트가 1건 이상 있다.
2. **시나리오 동등성** — `plans/94` 카탈로그를 1단 기동·3단 기동으로 각각 실행했을 때, 1단이 통과한 시나리오는 3단도 통과한다. 1단 결손(H-1)으로 1단이 실패한 시나리오는 3단 통과를 목표로 별도 표기한다.
3. **3단 고유 기능 보존** — 장애 진단 위임 · 알람 헤드라인·진행 중 월 고지 · 양식 채우기 기존 시나리오 무회귀.
4. **병렬·재개** — 병렬 `Send` 태스크에서 `InvalidUpdateError` 0 · 인터럽트 재개 시 끝난 태스크 재실행 0 · 다중 대기 인터럽트 개별 재개.
5. **스트림** — `done` 정확히 1회(루트 종료) · 서브그래프 종료로 스트림이 닫히지 않음 · 인터럽트 시 `awaiting_approval` 이벤트 · 폴백 재실행 0.
6. **턴 격리** — 새 턴에서 앞 턴의 `task_outcomes`·`task_results`·`replan_count` 잔존 0.
7. **비트 동일** — 플래그 3종 off에서 3단 그래프 노드 구성·분기 함수·라우터 프롬프트 골든 무변화. P0 랜딩 후 기존 테스트 전건 통과.
8. **게이트** — `arch_check --ci`·`overfit_check --ci` 신규 위반 0 · 원문 문자열 기반 의도 판정 신규 0(D-004 리뷰 체크).
9. **성능** — 시나리오 리포트 기준 3단 p50·p95가 1단 기준선보다 나쁘지 않고, 응답 시간 목표(단순 <10s · 복합 <30s)를 지킨다.
10. **폐기** — P4 후 `sequential_runner`·순차 표지 진입·`interrupt_before` 승인 배선·`_run_single_db_pipeline` 참조 0.

---

## 6. 위험 · 비범위

| 위험 | 완화 |
|---|---|
| 한 번에 큰 전환 | 단계별 플래그(P1·P2·P3) · P0은 무변경 교정 · 단계마다 검증 후 진행 |
| 기존 3단 노드가 서브그래프 안에서 다르게 동작(상태 키 가정) | P1-1 골든 비교 · 서브그래프 상태는 `AgentState` 그대로 · 출력만 `output_schema`로 좁힘 |
| D-203 순차 의존 회귀 | 게이트·사후 대조·경과 노트는 **같은 함수** 재사용 · `tests/test_composite/*` 3단 재실행 |
| 체크포인트 호환 — 기존 스레드에 새 키 없음 | 새 키는 선택적 · 없으면 빈 목록으로 해석 · 인터럽트 방식 전환은 플래그 on 이후 새 스레드부터 |
| 계획 LLM 호출 추가로 지연 증가 | 단일 의도는 결정적 1태스크(LLM 0) · 계획 LLM은 `needs_plan`일 때만 · P5-2 측정 |
| 병렬 태스크 간 전역 캐시·락 경합(J-2) | 오늘 2단 `asyncio.gather`와 같은 조건 — 테스트로 고정 |
| 진행 이벤트 유실(J-3) | 서브그래프 안 `adispatch_custom_event` 동작 실측 · 그래프 밖 호출 경로 점검 |
| 병행 세션 충돌 | 착수 전 확인(§4 불변식) |

**비범위**
- 2단 배선 폐기(§3.6 — P5 후 별도 판단).
- deepagents를 3단 라우터의 요청 단위 분기 대상으로 편입(`plans/102` G-9 (b)).
- 구조 분석·구조 승인 — `plans/104`.
- 폴스타·자산관리 교차 시스템 계약 — `plans/102`(이 계획의 `plan`·`dispatch`·`join`이 102의 소유 검증·키 브리지·소재 프로브를 부르는 자리가 된다).
- 새 체크포인터 백엔드(postgres) 도입.

---

## 7. 사용자 확정 게이트

| # | 질문 | 왜 막히는가 | 기본 가정(답 없으면 이걸로 진행) |
|---|---|---|---|
| **G-1** | **복합 질의 진입 신호** — 라우터 구조화 출력 `needs_plan`으로 판정할 것인가? 현행 원문 순차 표지(16종)는 어떻게 하나 | P2-1 · D-004 | **`needs_plan` 정본** · 순차 표지는 전환기 OR 신호로 두고 P4-2에서 삭제 |
| **G-2** | **계획·재계획 LLM 평면** — 워커 LLM(운영 FabriX · 2단·순차 러너와 동일)인가, 1단처럼 오케스트레이터 LLM(vLLM·Gemini·MLX)인가 | 품질·비용·과금 판정(D-222) | **워커 LLM** · 품질이 부족하면 오케스트레이터 평면으로 바꾸는 설정 1개 |
| **G-3** | **결정적 의도 보정**(`_coerce_alarm_intent`·`_coerce_process_intent`·`_coerce_host_inspect_intent`) 이식 범위 | 원문 키워드 기반 보정이라 D-004 경계 | **계획 경로에만 2단과 같은 함수로** — 라우터 단일 의도에는 추가하지 않는다(신규 키워드 판정 0) |
| **G-4** | 1단 스코프 휴리스틱 G2(참조어)·G3(순위어)를 이식할 것인가 | 1단 고유 · 키워드 기반 | **이식하지 않고 `input_from` DAG로 대체** — 시나리오 동등성(P5-1)에서 차이가 나면 재검토 |
| **G-5** | **단일 의도 데이터 조회도 태스크 서브그래프로 일원화**할 것인가 | P4-1 · 사본 제거 | **일원화**(P5-1 통과 뒤) |
| **G-6** | **SQL 승인 HITL**(운영 off)을 3단 전 경로(단일·멀티·복합)에서 `interrupt()`로 제공할 것인가 | P3 범위 | **제공** — 운영 기본값은 off 유지 |
| **G-7** | `host_inspect` 운영 노출 — 기존 플래그 `COMPOSITE_INVESTIGATION_ENABLED`(기본 off)를 그대로 따를 것인가 | P1-3 | **기존 플래그를 따른다**(3단 추가 플래그 없음) |

> **2026-09-22 착수 시 채택한 기본 가정**(게이트 답 없음 · teammate 지시 "기본 가정으로 진행하되 보고서에 명시") — **확정이 아니다.**
> G-1 `needs_plan` 정본 + 순차 표지 OR(구현) · G-2 워커 LLM(`plan`·`replan`이 워커 `llm`) · G-3 교정은 계획 경로에만 — `normalize` 출구 전부에(111 D-1) · 라우터 단일 의도에는 없음(구현) · G-4 G2/G3 미이식 — `input_from` DAG + D-203 게이트(구현) · G-5 단일 의도 일원화는 P5-1 뒤 — 이번에는 **단일 의도가 루프에 들어오지 않는다**(현행 직결 체인) · G-6·G-7 해당 WU 미착수.

---

## 8. 다른 계획과의 관계

| 계획 | 관계 |
|---|---|
| `plans/102` | G-10(3단 미도달 기능) → **이 계획으로 확정**. 102의 소유 검증(§3.1)은 `plan`의 태스크 대상 결정에서, 키 브리지(§3.3)는 `join`의 `prior_rows` 기록에서, 소재 프로브(§3.4)는 `resolve_targets`·`entity_locator`에서 호출된다. 102 X-13(순차 진입)은 **이 계획의 `needs_plan` 진입**으로 대체. 102 L-5(운영 전환)는 이 계획 P5 뒤 |
| `plans/104` | 구조 승인 HITL을 질의 경로에서 제거 — 3단 복합 실행의 HITL 차단(102 X-T8)을 원천 해소. 이 계획 P3은 **SQL 승인만** 다룬다 |
| `plans/88`(D-203) | 게이트·사후 대조·경과 노트 함수를 그대로 재사용. `sequential_runner`는 P4-2에서 대체·삭제 |
| `plans/94` | 동등성 판정 무대(P5-1) |
| `plans/111` | **94 run 실측 기반 델타**(2026-09-21 · 111 게이트 확정 → §3.3.1에 편입) — `plan`의 모든 분기가 지나는 결정적 `normalize` 단일 출구(G-3 교정 3종 · task 프레임 검증) · `task_run` 첫 노드 `task_prompt`(107 렌더) · `replan` 입력에서 task 실패 제외 · 0행 의존 게이트 · 재진입 재파싱 생략. 분해 출력 계약을 `sub_query` 자유문에서 task 프레임으로(111 §5). 111 G-2 권고는 **이 계획 P1·P2에 편입**(정본 단일화) |

---

## 9. 신규 결정 예약 — D-226

**제목(예정)**: 사다리 3단 기능 동등성 — LangGraph 네이티브 계획·팬아웃·재계획 워크플로 · 태스크 서브그래프 단일화 · `interrupt()` HITL (D-203 3·4단 러너 대체 · D-225 후속)

**담을 내용**
1. **3단이 1·2단 전 기능을 제공**한다 — 계획 노드 → `Send` 팬아웃 → 태스크 서브그래프 → `defer` 합류 → 결과 기반 재계획(명시 상한) → 합성. 1단 자율 도구 루프는 이식하지 않고 워크플로로 대체한다.
2. **동등성 기준은 시나리오 결과**다. 1단의 조용한 결손은 재현하지 않고, 3단 고유 기능은 보존한다.
3. **데이터 조회 파이프라인은 태스크 서브그래프 하나** — 3단 단일·복합, 1·2단 핸들러가 같은 서브그래프를 호출한다.
4. **복합 진입은 라우터 구조화 출력**(`needs_plan`)으로 판정한다(D-004). 원문 순차 표지는 전환기 후 삭제.
5. **HITL은 `interrupt()` + `Command(resume)`** — 상태 플래그 재실행 방식 폐기. 구조 승인은 질의 경로에서 제거(D-227).
6. **기반 계약**: 추적 프록시의 서브그래프·인터럽트 호환 · 팬인 전용 리듀서 키 + 턴 초기화 · SSE 루트 종료 판정 · 인터럽트 이벤트.
7. **승격-폐기 동반**(D-161 ①): `sequential_runner`·순차 표지 진입·`interrupt_before` 승인 배선·함수 호출형 단일 DB 파이프라인 삭제. 2단 배선은 P5 후 D-161 ② 실측으로 별도 판단. 플래그 3종 만료일은 등재 시 확정.

등재 시 `docs/02_decision.md`의 `## D-` 헤더·「변경 이력」·「채번 이력」 표를 재확인하고 최댓값+1을 재부여한다(예약 소진 가능).

---

## 10. 참고

| 자료 | 반영 |
|---|---|
| 루트 venv 설치본 — `langgraph` 1.2.11 · `deepagents` 0.6.10 소스(`.venv/lib/python3.12/site-packages/`) · 토이 그래프 실행(2026-09-17) | §1.1 전 항목 — **계획서 의사코드가 아니라 설치본 시그니처·동작**으로 설계했다(Known Mistakes "외부 패키지 API 실측") |
| Anthropic, *Building effective agents*(2024-12-19) — 워크플로·에이전트 정의와 선택 기준, *"find the simplest solution possible, and only increasing complexity when needed"* | Q1 · §0.2 (`plans/102` P10과 같은 근거 · 2026-09-17 원문 재확인) |
| `docs/21_orchestration_ladder.md` · `plans/102` v2 §1.6 | 사다리 배선·모듈 의존 |

---

## 11. 변경 이력

| 버전 | 날짜 | 내용 |
|---|---|---|
| v1.2 | 2026-09-22 | **부분 구현**(`plans/111` C-4·C-5 범위 · teammate 지시 *"111번 계획을 구현하라"*) — §4.1 구현 현황: P0-1 ✅ · P0-2 ✅(루프 한정 · K-5 미해소) · P0-3 ◐ · P1-1 ✅ · P2-1 ✅ · P2-2 ◐ · P2-3 ◐ · P2-4 ◐. 플래그 `TIER3_PLAN_LOOP_ENABLED` 1개(기본 off · §3.7 구현 주). **실측 정정 2건**: §1.1 서브그래프 함정 — `output_schema`는 `astream_events`에서 불충분(함수 노드 안 `ainvoke`로 해결) · §1.3 K-1 — `config` 주입은 이미 동작. §3.3 턴 초기화 자리를 `plan` 노드로. §7 게이트는 **미응답 — 기본 가정 채택 기록**만 더했다(확정 아님). 파일명 `-TODO` → `-WIP` · 신규 D-번호 0 |
| v1.1 | 2026-09-21 | **`plans/111` 델타 편입**(111 G-2 확정 · 사용자 *"권고에 맞게 진행하라"*) — §3.3.1 신설(`plan` 원문 조각 계약 · `normalize` 단일 출구 · `task_prompt` · 0행 의존 게이트 기본 on · 재계획 입력 범위) · P1-1·P2-1·P2-2·P2-3 작업 항목 확장. **이 계획의 게이트 G-1~G-7 상태는 바꾸지 않았다**(111 게이트만 확정) · 신규 D-번호 0 |
| v1 | 2026-09-17 | 최초 작성(사용자 지시 *"3단 기능도 langgraph 기능을 이용하면 1단의 모든 기능을 구현할 수 있다. 검토하여 모두 구현하는 방향으로"*). 검토 결론: 가능 — LangGraph 1.2.11 설치본에 `Send`·`defer`·`Command`·`interrupt()`·서브그래프가 있고 토이 그래프로 계획→팬아웃→합류→루프→합성·서브그래프 안 인터럽트 재개를 확인. deepagents 0.6.10도 LangGraph 기반이며 실사용은 할 일 목록+1단계 도구 루프뿐. 기능 격차 20행(§1.2 · ★프로세스·호스트 점검 3단 미도달 · 재계획 없음 · 승계 없음 · 1단 ambient 결손으로 존 역질문 미발동) · 저장소 차단 4종(★추적 프록시·리듀서 부재·SSE 조기 종료·HITL 재개가 새 실행) + 2단 턴 누수 · 설계(태스크 서브그래프 단일화 · plan/dispatch/join/replan/finalize · 팬인 키 1개 · `interrupt()` HITL) · WU P-0~P5-3 · 게이트 G-1~G-7 · D-226 예약 |
