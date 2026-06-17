# 48. deepagents 기반 의도 분해 오케스트레이션 적용 계획서

> 작성일: 2026-06-16
> 관련 결정: D-004(LLM 전용 시멘틱 라우팅) **개정 예정**, 신규 D-037 제안
> 도입 방식: **단계적 하이브리드** (1단계 패턴 자체 구현 → 2단계 격리 PoC)
> 분해 범위: **복합 의도 분해 + 순차/병렬 실행**
> 개정: 2026-06-16 (v2) — deepagents 전체 기능(11개 미들웨어) 충분성 분석 및 Phase 2~9 단계적 로드맵 추가 (§2.5, §5)
> 개정: 2026-06-16 (v3) — 모호성 명료화 인터럽트(Clarification HITL) 추가: 처리 방법 모호 시 사용자에게 선택지 되묻기. Phase 1 훅 + Phase 4 구현, 계획 단계 감지 한정 (§4.11, §5 Phase 4, R-13)
> 개정: 2026-06-17 (v4) — **트랙 B 재진입 확정**(D-037 갱신). FabriX tool 호출 불가 블로커를 **폐쇄망 vLLM 오케스트레이터**(Qwen3.5-9B, `ChatOpenAI` 네이티브 tool-calling) + **FabriX 워커(실질 응답처리)** 분리로 해소. Phase 8(실제 패키지) **부활**, Phase 9 재개. 상세 구현은 **Plan 49** · D-037 참조. **이하 본문(§2.4·§5·R-08 등)의 '트랙 B 제거 / FabriX tool 불가 확정'(2026-06-16) 서술은 본 개정으로 대체됨**(이력 보존 목적 유지).

---

## 1. 개요 및 목표

현재 `semantic_router`는 사용자 질의를 **단일 의도(intent)** 와 **대상 DB**로 한 번에 분류하여
하나의 경로로만 라우팅한다. 이로 인해 다음 두 경우를 처리하지 못한다:

- **복합 의도**: 한 질의에 여러 작업이 섞인 경우 (예: "캐시 갱신하고 나서 서버 목록도 조회해줘")
- **결과 기반 후속 처리**: 한 작업의 **결과를 본 뒤 후속 작업**이 필요한 경우
  (예: "CPU 높은 서버를 찾아 **그 서버들의** 프로세스를 분석", "조회 결과에 **장애가 있으면** 알람 이력도 조회")

본 계획은 LangChain **deepagents**의 핵심 패턴 — **planner(작업 분해) + subagent 위임(task delegation)** —
을 도입하여, 사용자 의도를 **여러 sub-task로 분해**하고 각 작업을 **순차/병렬/결과-의존**으로 실행한 뒤
결과를 통합 응답하는 오케스트레이션 계층을 추가한다.
task 간 관계는 **3패턴**(①독립 병렬 ②데이터 의존 순차 ③결과 조건부 동적 재계획)으로 다룬다(§4.10).

**진행 방식(2026-06-17 갱신 — 트랙 B 재진입)**: 당초(2026-06-16)는 'FabriX tool 호출 불가 → 트랙 B(실제
패키지) 제거, 트랙 A 단일화'로 확정했으나, **폐쇄망 vLLM 오케스트레이터**로 tool-calling 블로커가 해소되어
**트랙 B(deepagents 실제 패키지)를 주 경로로 도입**한다(D-037 갱신, 상세 Plan 49).

- **제어 평면 = vLLM**: 폐쇄망 내부에 tool-calling 지원 오픈모델(**Qwen3.5-9B**)을 서빙하고,
  `langchain-openai`의 `ChatOpenAI`(base_url=vLLM)로 **네이티브 `bind_tools`** 를 구동 → deepagents
  `write_todos`(동적 재계획)·`task`(위임)·filesystem 정상 동작.
- **데이터 평면 = FabriX(KBGenAIChat)**: 자연어→SQL→DB 조회→결과 정리·**최종 자연어 응답**(실질 응답처리).
  기존 `SUBAGENT_REGISTRY` 작업을 `@tool`로 노출하되 **FabriX는 도구 내부에서만 호출**(tool-calling 강요 방지).
- **백엔드 선택은 vLLM 가용성 옵션**: vLLM 서빙 시 트랙 B, **미서빙 시 기존 `semantic_router`** 로 동작.
- **트랙 A**(Phase 1~6, tool-calling 없이 자체 구현)는 **구현 완료되어 폴백으로 보존**한다.

> 비고: 아래 §2.4~§5·§6(R-08)의 '트랙 B 제거'·'FabriX tool 호출 불가 확정' 서술은 2026-06-16 시점 기록이며,
> 본 개정(2026-06-17)으로 **대체**되었다(이력 보존 목적 유지).

> **충분성 분석**: 트랙 A의 Phase 1은 deepagents 11개 핵심 미들웨어 중 Planning(정적 분해)·SubAgent(위임) **2개만 부분 차용**한다(≈18%).
> 누락 기능과 이를 보완하는 **Phase 2~9 단계적 로드맵은 §2.5(커버리지 분석)·§5(로드맵)** 에 정의한다.

### 성공 기준 (1단계)

1. 단일 작업 질의는 기존과 **동일한 결과**를 반환한다 (하위 호환).
2. 복합 작업 질의("A 하고 B 조회해줘")가 2개 이상 sub-task로 분해되어 순차 실행된다.
3. 의존성 없는 독립 작업은 병렬 실행된다.
4. 각 sub-task 결과가 통합되어 단일 응답으로 반환된다.
5. `ENABLE_DEEPAGENT_ORCHESTRATION=false` 시 기존 `semantic_router` 경로가 그대로 동작한다.
6. 각 sub-task의 상태(pending/in_progress/completed/failed)가 추적되고, subagent는 **registry**로 정의되어 **필터된 컨텍스트만** 입력받는다 (Planning·SubAgent 미들웨어 정합 — §2.6).
7. **데이터 의존 후속**(패턴 ②): 선행 task의 결과가 후속 task의 입력으로 주입되어 결과 기반 조건(예: `WHERE hostname IN (…)`)을 생성한다 (§4.10.1).
   - (결과를 **평가**해 후속 task를 동적 생성하는 패턴 ③은 루프가 필요하여 **Phase 2** — §4.10.2.)

---

## 2. deepagents 조사 결과 요약

### 2.1 deepagents란

- LangChain이 공개한 "batteries-included agent harness" (Claude Code에서 영감).
- **LangGraph 위에 구축**되며, `create_deep_agent(...)`가 **컴파일된 LangGraph 그래프**를 반환한다.
- 4개 내장 미들웨어를 자동 장착: **Planning(`write_todos`)**, **가상 Filesystem**, **SubAgents(`task` tool)**, **Summarization(컨텍스트 압축)**.

### 2.2 핵심 패턴 (본 프로젝트에 차용할 부분)

| deepagents 기능 | 동작 | 본 프로젝트 차용 |
|----------------|------|-----------------|
| **Planning (`write_todos`)** | 복잡한 작업을 discrete step으로 분해·추적 | `intent_planner` 노드로 자체 구현 |
| **SubAgents (`task` tool)** | 메인 에이전트가 격리된 컨텍스트의 전문 subagent에 작업 위임. 결과만 `ToolMessage`로 반환 | 기존 작업 경로를 **subagent(서브 파이프라인)** 로 캡슐화 |
| **CompiledSubAgent** | 사전 빌드된 LangGraph 그래프를 subagent로 등록 | 2단계에서 기존 파이프라인 래핑에 활용 |
| Filesystem / Skills / Summarization | 가상 FS, 스킬, 대화 압축 | **본 프로젝트 불필요** — 미차용 |

### 2.3 subagent 정의 스키마 (참고)

```python
{
  "name": "research-agent",            # task() 호출 시 식별자
  "description": "...",                # 메인이 위임 여부 판단에 사용
  "system_prompt": "...",              # 메인에서 상속 안 됨
  "tools": [...],                      # 지정 시 상속 도구 완전 대체
  "model": "openai:gpt-5.5",           # 메인 모델 오버라이드 (문자열 또는 모델 객체)
  "middleware": [...],                 # 선택
}
```

### 2.4 호환성 분석 (★ 결정적)

| 항목 | 현재 배포(`wheels/`) | deepagents 0.6.10 요구 | 영향 |
|------|--------------|----------------------|------|
| langchain-core | **`1.2.30` (1.x)** | `>=1.4.7,<2.0.0` | **마이너 업글**(1.2→1.4) |
| langchain | 미설치(메타패키지) | `>=1.3.9,<2.0.0` | wheel 반입 필요(폐쇄망) |
| langgraph | **`1.1.6` (1.x)** | langchain 1.x와 호환 | **호환**(현행 유지) |
| Python | `>=3.11` | `>=3.11,<4.0` | **호환** |
| tool calling | **vLLM 오케스트레이터**(`ChatOpenAI`→vLLM, 네이티브 tool-calling). FabriX는 워커(tool-calling 불요) | tool calling **필수** | **해소** — vLLM이 제공(R-08, 트랙 B) |

> **주의**: `requirements.txt`는 하한(`>=0.2.0`/`>=0.3.0`)만 명시하나, 실제 배포 wheel(`wheels/{os}/`)은 위와 같이 **1.x**다.

- **결론(2026-06-16 정정)**: 배포 wheel 확인 결과 **현재 운영 스택은 이미 langchain-core 1.2.30 / langgraph 1.1.6 (1.x)** 이다.
  (`requirements.txt`의 `>=0.2.0` 하한만 보고 0.x로 오판했던 것을 정정.) 따라서 deepagents 도입은 **메이저 마이그레이션이 아니라
  마이너 업글**(core 1.2→1.4) + `langchain` 메타패키지·`deepagents` wheel 반입(폐쇄망)이다. 커스텀 LLM 클라이언트도
  이미 1.x에서 동작 중이므로 재작성 리스크는 낮다.
- **tool-calling 블로커(R-08) 해소(2026-06-17)**: FabriX 자체는 tool-calling을 못 하나, **별도 vLLM 오케스트레이터**가
  tool-calling을 담당하고 FabriX는 워커(실질 응답처리)로 분리되어 deepagents 실제 패키지가 작동한다(트랙 B, Plan 49).
  트랙 A(자체 구현)는 폴백으로 보존되며, vLLM 미서빙 시 `semantic_router`로 회귀한다.

> 출처: [deepagents overview](https://docs.langchain.com/oss/python/deepagents/overview),
> [subagents](https://docs.langchain.com/oss/python/deepagents/subagents),
> [GitHub langchain-ai/deepagents](https://github.com/langchain-ai/deepagents),
> [PyPI deepagents](https://pypi.org/project/deepagents/)

### 2.5 deepagents 전체 기능 인벤토리 및 1단계 커버리지 (충분성 분석)

심층 재조사 결과, `create_deep_agent()`는 기본 3종(Todo·Filesystem·SubAgent) + 옵션 미들웨어로 스택을 구성한다.
아래는 deepagents 0.6.10 핵심 기능 **전체**와, 본 계획 1단계(§4)의 커버리지다.

| # | deepagents 기능 (미들웨어) | 동작 요약 | 1단계 커버 | 후속 Phase |
|---|---|---|---|---|
| 1 | **TodoListMiddleware** (Planning, `write_todos`) | 작업 todo 분해 + 상태추적(pending/in_progress/completed) + **동적 재계획**(리스트 전체 교체). 모델 턴당 1회 | ◐ 분해 + 상태추적 (§2.6 보완) | 동적 재계획 → **Phase 2** |
| 2 | **SubAgentMiddleware** (`task`) | 격리 컨텍스트 동기 위임, 결과만 회수 | ◐ registry + 부분 격리 (§2.6 보완) | 완전 격리 → **Phase 6** |
| 3 | tool calling 기본화 + **response_format** | 프롬프트+JSON 대신 도구호출/구조화출력으로 신뢰성 확보 | ✗ (수동 JSON 파싱) | **Phase 7** |
| 4 | **FilesystemMiddleware** | 대용량 도구 출력을 파일로 오프로딩, 컨텍스트/state 경량화 | ✗ | **Phase 3** |
| 5 | **SummarizationMiddleware** | 토큰 ≈85% 임계 시 대화 자동 압축 | ✗ (단순 슬라이싱) | **Phase 5** |
| 6 | **HumanInTheLoopMiddleware** (`interrupt_on`) | 도구별 승인/편집/거부 + **`respond`(되묻기)** | △ SQL·구조 2개 고정, 모호성 되묻기 ✗ | **Phase 4** (승인 세분화 + **모호성 명료화** §4.11) |
| 7 | **AsyncSubAgentMiddleware** | 비동기 background 위임(task ID 즉시 반환) | ✗ | Phase 8+ (가치 평가) |
| 8 | **SkillsMiddleware** (`SKILL.md`) | 메타 노출(프롬프트 주입) + 온디맨드 로드(`read_file`) | ✗ | **메타 노출은 트랙 A 가능**, 온디맨드는 코드 기반 대체. 기존 `db_profiles`와 중복 → 선택적 (§5.2) |
| 9 | **MemoryMiddleware** (`AGENTS.md`) | 세션 간 학습 영속 | ✗ | 보류 (가치 평가) |
| 10 | **SummarizationToolMiddleware** (`compact_conversation`) | 작업 경계 수동 압축 | ✗ | Phase 5 (병행) |
| 11 | PatchToolCalls / AnthropicPromptCaching 등 기본 스택 부가 | 도구호출 보정·프롬프트 캐싱 | ✗ | Phase 8~9 (실제 패키지) |

**커버리지 결론**: 1단계는 핵심 11개 중 **2개를 부분(△) 차용**(≈18%). 나머지는 §5 로드맵으로 단계 배치한다.
다만 미차용 기능 다수는 **tool-calling 없이 패턴 자체 구현이 가능**(트랙 A)하며,
일부(async subagent, 기본 스택 부가, skills/memory)는 deepagents 실제 패키지(트랙 B)가 있어야 자연스럽다.
각 기능의 **코드 측 gap 근거(파일·라인)** 는 §5 각 Phase의 "Gap 근거"에 명시한다.

### 2.6 Planning·SubAgent 미들웨어 정밀 비교 및 Phase 1 정합성 (심층 분석)

1단계의 핵심인 planning·subagent가 deepagents의 해당 미들웨어를 **충분히 반영**하는지,
두 미들웨어를 기능 요소로 분해하여 Phase 1 설계(§4)와 1:1 대조한다.

**(a) Planning (TodoListMiddleware) 기능 요소 대조**

| 요소 | deepagents 동작 | Phase 1 (개정 전) | 판정 / 조치 |
|------|----------------|-------------------|------------|
| P1 계획 자료구조 | `Todo{content, status}` 리스트 | TaskSpec에 status 없음 | **Phase 1 보완** — `status` 추가 |
| P2 상태 전이 | pending→in_progress→completed | 없음 | **Phase 1 보완** — orchestrator가 갱신 |
| P3 동적 재계획 | `write_todos`가 리스트 **전체 교체** | 정적 1회 분해 | Phase 2 (Phase 1에 재계획 훅 지점만 마련) |
| P4 계획 가시성 | 모델/사용자에 노출 | 미명시 | **Phase 1 보완** — task_plan 응답·로그 노출 |
| P5 작성 메커니즘 | LLM이 `write_todos` **도구 호출**(agentic) | deterministic 노드 1회 JSON | 트랙 B (Phase 7/8) — 메커니즘 차이 |
| P6 의존성 표현 | 순서 리스트(명시적 의존성 없음) | `depends_on` DAG | Phase 1이 **초과 달성** |

**(b) SubAgent (SubAgentMiddleware) 기능 요소 대조**

| 요소 | deepagents 동작 | Phase 1 (개정 전) | 판정 / 조치 |
|------|----------------|-------------------|------------|
| S1 정의 스키마 | dict{name, description, system_prompt, tools, model, …} | `_run_agent` if-elif 하드코딩 | **Phase 1 보완** — SubAgent **registry** 도입 |
| S2 위임 결정 | 메인 LLM이 `task` 도구로 description 기반 선택 | planner가 agent 지정 | **Phase 1 정합** — planner가 registry description 기반 분류(의미 동형). tool-call 메커니즘은 트랙 B |
| S3 격리 컨텍스트 | subagent 자체 히스토리, 결과만 회수 | 전체 state 전달 | **Phase 1 부분 보완** — handler 입력=필터된 컨텍스트. 완전 격리는 Phase 6 |
| S4 결과 회수 | `ToolMessage` 요약 결과 | `task_results[task_id]` | 반영됨 |
| S5 general-purpose | 자동 fallback subagent | general_inference 유사하나 미명시 | **Phase 1 보완** — fallback agent로 명시 |
| S6 per-agent 커스터마이즈 | model/tools/prompt/response_format 개별 | 없음 | Phase 7 (registry에 슬롯만 Phase 1 예약) |
| S7 CompiledSubAgent | 컴파일 그래프 래핑 | 헬퍼 함수 호출 | Phase 6 |

**결론**: Phase 1(개정 전)은 planning/subagent의 **행동 골격은 갖췄으나 자료구조·정의 추상화가 빈약**했다.
다음을 Phase 1에 보완하여 deepagents 미들웨어와 **구조적으로 정합**시킨다 (§4.2·4.3·4.5 개정 반영):

1. TaskSpec에 `status` 추가 + orchestrator 상태 전이 — **Planning P1·P2**
2. SubAgent **registry**(name/description/handler + model·prompt 슬롯) 도입 — **SubAgent S1·S6 토대**
3. planner가 registry description 기반으로 위임 분류 — **SubAgent S2 의미 정합**
4. subagent handler 입력을 **필터된 컨텍스트**로 한정 — **SubAgent S3 부분 격리**
5. general_inference를 **fallback(general-purpose) agent**로 명시 — **SubAgent S5**
6. task_plan **가시성**(응답·로그 노출) — **Planning P4**

동적 재계획(P3)·완전 컨텍스트 격리(S7)·tool-calling 위임(P5/S2 메커니즘)·per-agent 모델(S6)은
**의도적으로 후속 Phase**에 둔다. 단 위 registry/handler/status 추상화가 그 확장이 얹힐 **토대**가 되도록 Phase 1에서 미리 경계를 잡는다.

---

## 3. 현재 코드 구조 분석 (대체/재사용 대상)

### 3.1 현재 라우팅·실행 흐름

```
context_resolver → input_parser → field_mapper → semantic_router → [조건부 분기]
  ├─ cache_management        → END           (intent=cache_management)
  ├─ synonym_registrar       → END           (intent=synonym_registration)
  ├─ general_inference       → END           (intent=general_inference)
  ├─ multi_db_executor       → result_merger → result_organizer → output_generator → END  (is_multi_db)
  └─ schema_analyzer → ... → output_generator → END                                       (단일 DB)
```

핵심 파일:
- `src/routing/semantic_router.py` — LLM 단일 의도/DB 분류 (`semantic_router`, `_llm_classify`, `_build_router_prompt`)
- `src/graph.py` — `route_after_semantic_router()` + `_INTENT_ROUTE_MAP` 기반 조건부 라우팅
- `src/prompts/semantic_router.py` — 라우팅 LLM 프롬프트 템플릿
- `src/routing/domain_config.py` — `DB_DOMAINS`, `DBDomainConfig`
- `src/state.py` — `AgentState` (routing_intent, target_databases, is_multi_db 등)

### 3.2 "작업(subagent 후보)" 인벤토리

현재 `semantic_router`가 분기시키는 5개 작업 경로 → 5개 subagent로 노출한다.

| agent (신규 명명) | 현재 구현 진입점 | 종류 |
|------------------|-----------------|------|
| `data_query` | `multi_db_executor` / `schema_analyzer→…→output_generator` | DB 조회 (단일/멀티) |
| `cache_management` | `nodes/cache_management.py` | 캐시·유사어 관리 |
| `synonym_registration` | `nodes/synonym_registrar.py` | 유사어 등록 |
| `general_inference` | `nodes/general_inference.py` | DB 미접근 LLM 응답 |
| `alarm_query` | (현재 data_query 경로 흡수) | 알람 조회 — D-029 |

> 참고: 실시간 알람 **수신·발송** 파이프라인(`src/alarm/orchestration/alarm_graph.py`, `alarm_worker`)은
> 소켓 푸시 기반 별도 워커이므로 본 오케스트레이션(사용자 질의 경로) 범위 **밖**이다.
> 사용자 질의의 "알람 조회"(`alarm_query`)만 subagent 대상.

### 3.3 재사용 가능한 선례

`multi_db_executor`는 이미 **"여러 대상(DB)을 순회하며 각각 schema→generate→validate→execute 파이프라인을 실행하고
결과를 누적"** 하는 supervisor 패턴을 구현하고 있다 (D-005). 본 계획의 오케스트레이터는 이를 **"여러 task를 순회하며
각각 해당 agent 파이프라인을 실행"** 으로 일반화한 형태이므로, 동일 설계 원칙(부분 실패 허용, 결과 누적)을 재사용한다.

---

## 4. 1단계 상세 설계 (패턴 자체 구현, 현 스택 유지)

### 4.1 신규 그래프 흐름

```
context_resolver → input_parser → field_mapper → intent_planner → agent_orchestrator → result_aggregator → END
                                                       │                  │
                                                       │                  ├─(task: data_query)         → data_query 서브 파이프라인
                                                       │                  ├─(task: cache_management)   → cache_management 노드
                                                       │                  ├─(task: synonym_registration)→ synonym_registrar 노드
                                                       │                  ├─(task: general_inference)  → general_inference 노드
                                                       │                  └─(task: alarm_query)        → data_query(알람) 파이프라인
                                                       │
                                                  (단일 task면 기존 경로와 동일)
```

- `ENABLE_DEEPAGENT_ORCHESTRATION` **미입력(기본)** 시 **멀티 DB 환경이면 `intent_planner`/`agent_orchestrator`/`result_aggregator` 신규 경로가 기본 동작**(D-037, 2026-06-16 기본값 전환). 단일/레거시면 비활성.
- `=false` 명시 시 기존 `semantic_router` 경로로 회귀 → **하위 호환 opt-out**. `=true`로 강제 활성.

### 4.2 신규 노드 ①: `intent_planner` (deepagents `write_todos` 대응)

**역할**: 사용자 질의를 sub-task 목록(`task_plan`)으로 분해한다. 단일 작업이면 task 1개만 생성.

**위치**: `src/orchestration/intent_planner.py` (신규 패키지)
**프롬프트**: `src/prompts/intent_planner.py` (신규)

**TaskSpec 스키마**:

```python
{
  "task_id": "t1",
  "agent": "data_query" | "cache_management" | "synonym_registration"
           | "general_inference" | "alarm_query",
  "sub_query": "이 작업이 처리할 자연어 지시 (질의에서 해당 작업 부분만 추출)",
  "depends_on": [],          # 선행 task_id 목록(실행 순서). 비어 있으면 병렬 실행 후보
  "input_from": [],          # ★ 선행 task 결과를 입력으로 주입받을 task_id 목록 (데이터 의존 체이닝, §4.10.1)
                             #    보통 input_from ⊆ depends_on. 비어 있으면 데이터 의존 없음
  "order": 1,                # 동일 의존성 그룹 내 표시 순서
  "status": "pending"        # pending → in_progress → completed | failed
                             #   (planner가 pending으로 초기화, orchestrator가 갱신 — Planning 상태추적 P1·P2)
}
```

**LLM 출력 예시** — "polestar 캐시 갱신하고 cpu 사용률 높은 서버 알려줘":

```json
{
  "tasks": [
    {"task_id": "t1", "agent": "cache_management",
     "sub_query": "polestar DB 스키마 캐시를 갱신", "depends_on": [], "order": 1},
    {"task_id": "t2", "agent": "data_query",
     "sub_query": "CPU 사용률이 높은 서버 조회", "depends_on": ["t1"], "order": 2}
  ]
}
```

설계 원칙:
- 기존 `semantic_router` 프롬프트의 **의도 판별 규칙**(cache_management/alarm_query/general_inference 우선순위)을
  planner 프롬프트에 이식한다. 즉 planner는 "분해 + 각 task의 agent 분류"를 동시 수행.
- **DB 라우팅은 task 내부로 위임**한다. planner는 `agent`와 `sub_query`만 결정하고,
  실제 대상 DB 선택은 `data_query` subagent가 (기존 `semantic_router`의 DB 분류 로직을 재사용해) 수행한다.
  → 관심사 분리: planner=무엇을, subagent=어디서·어떻게.
  - ⚠ 단, planner는 질의에 포함된 **DB 식별 신호**(폴스타 위치: 김포/여의도/은행/공동존, DB명: polestar/cloud_portal 등,
    환경: 운영/개발/스테이징)를 **`sub_query`에 그대로 보존**해야 `classify_dbs`가 올바른 DB를 고를 수 있다(§4.9.6).
    이 신호는 DB 선택에만 쓰이고 SQL 조건으로 변환되지 않는다.
- LLM 실패/파싱 실패 시 **단일 `data_query` task로 폴백** (현행 폴백 정책 계승).
- **상태추적(Planning 정합)**: 생성된 모든 task의 `status`를 `pending`으로 초기화한다.
  `agent_orchestrator`가 실행 시작 시 `in_progress`, 완료 시 `completed`/`failed`로 갱신한다.
  `task_plan`은 SSE/로그로 노출하여 진행 가시성을 제공한다(실시간 진행률 스트리밍은 Phase 2).
- **위임 분류(SubAgent 정합)**: planner는 각 agent를 `SUBAGENT_REGISTRY`(§4.3)의 `description`을 근거로 선택한다.
  이는 deepagents에서 메인 LLM이 `task` 도구의 subagent `description`을 보고 위임 대상을 고르는 것과 의미상 동형이다.
- **모호성 훅(Phase 1 예약 슬롯)**: planner 출력에 선택적 `clarification_needed` 필드를 **예약**한다.
  Phase 1은 이를 **방출만** 하고 보수적 기본 선택(단일 task 폴백 등)으로 진행하며, 실제 되묻기 인터럽트는
  **Phase 4에서 처리**한다(§4.11). per-agent model/prompt 슬롯(S6)과 동일한 '슬롯 예약' 방식이다.

### 4.3 신규 노드 ②: `agent_orchestrator` (deepagents `task` 위임 대응)

**역할**: `task_plan`을 의존성 위상정렬하여 **레벨 단위로 실행**한다.
같은 레벨(서로 의존 없음)의 task는 `asyncio.gather`로 **병렬**, 레벨 간에는 **순차**.

**위치**: `src/orchestration/agent_orchestrator.py` (신규)

**SubAgent registry** — deepagents `SubAgent` dict 스키마에 정합하는 정의 테이블.
`_run_agent`의 if-elif 하드코딩을 제거하고, 모든 위임을 이 registry로 일원화한다.

```python
@dataclass(frozen=True)
class SubAgentSpec:                  # deepagents SubAgent dict와 1:1 대응
    name: str
    description: str                 # planner 위임 분류 근거 (SubAgent S2)
    handler: Callable                # 실행 진입점 (기존 노드/파이프라인 래퍼)
    model: BaseChatModel | None = None   # per-agent 모델 슬롯 — Phase 7 예약(현재 None=메인 LLM)
    prompt: str | None = None            # per-agent 프롬프트 슬롯 — Phase 7 예약
    fallback: bool = False               # general-purpose(미분류 시 기본) 여부

SUBAGENT_REGISTRY: dict[str, SubAgentSpec] = {
    "data_query":           SubAgentSpec("data_query", "인프라 DB(서버 사양·사용량·모니터링) 조회", run_data_query_pipeline),
    "alarm_query":          SubAgentSpec("alarm_query", "알람/모니터링 이벤트 조회", run_data_query_pipeline),
    "cache_management":     SubAgentSpec("cache_management", "스키마 캐시·유사어 관리", run_cache_management),
    "synonym_registration": SubAgentSpec("synonym_registration", "유사어 등록", run_synonym_registration),
    "general_inference":    SubAgentSpec("general_inference", "DB 미접근 일반 응답", run_general_inference, fallback=True),
}

async def agent_orchestrator(state, *, llm, app_config):
    tasks = state["task_plan"]
    results: dict[str, dict] = {}

    for level in topological_levels(tasks):          # depends_on 기반 레벨 분할
        for t in level:
            t["status"] = "in_progress"              # Planning 상태 전이 (P2)
        coros = [_run_agent(task, state, llm, app_config, prior=results) for task in level]
        for task, res in zip(level, await asyncio.gather(*coros, return_exceptions=True)):
            norm = _normalize(res)                   # 예외 → 부분 실패 기록 (D-005 정책)
            task["status"] = "failed" if norm.get("error") else "completed"
            results[task["task_id"]] = norm

    return {"task_plan": tasks, "task_results": results, "current_node": "agent_orchestrator"}


async def _run_agent(task, state, llm, app_config, *, prior):
    spec = SUBAGENT_REGISTRY.get(task["agent"]) or _fallback_spec()   # 미지정 → general-purpose (S5)
    isolated = _make_isolated_input(task, state, prior)              # 전체 state 아닌 필터된 컨텍스트 (S3 부분 격리)
    return await spec.handler(task, isolated, llm=spec.model or llm, app_config=app_config)
```

**격리 경계(SubAgent S3)**: `_make_isolated_input(task, state, prior)`은 subagent가 필요로 하는 필드만
추린 **얇은 입력 컨텍스트**를 만든다(예: `sub_query`, 선행 task 결과 일부, user 컨텍스트). 전체 `AgentState`를
그대로 넘기지 않아 컨텍스트 오염을 줄인다. 완전한 컨텍스트 윈도우 격리(독립 messages·서브그래프)는 Phase 6.

**subagent handler** (registry의 `handler`, 시그니처 `(task, isolated, *, llm, app_config) -> dict`):
- 기존 노드/서브그래프를 **그대로 호출**하는 얇은 래퍼로 구현한다. 신규 비즈니스 로직 없음.
- `run_data_query_pipeline`: `multi_db_executor`(또는 단일 DB 파이프라인)를 재사용.
  내부에서 기존 `semantic_router._llm_classify`로 DB를 선택 → 기존 schema→gen→validate→exec 수행.
- `run_cache_management` / `run_synonym_registration` / `run_general_inference`:
  기존 동명 노드 함수를 `sub_query`를 입력으로 호출.
- **새 agent 추가 = registry 항목 1줄 추가**(+handler 래퍼). deepagents `subagents=[...]` 등록과 동형 확장.

**구현 방식 선택** (두 안 제시, **A안 권장**):

| 안 | 방식 | 장점 | 단점 |
|----|------|------|------|
| **A (권장)** | orchestrator를 **단일 supervisor 노드**로 두고 내부에서 서브 파이프라인을 `await` 호출 | `multi_db_executor` 선례와 동일, 격리·병렬 제어가 명시적, 그래프 단순 | 서브 실행이 LangGraph 노드 추적에 분리 표시 안 됨 |
| B | LangGraph `Send` API로 동적 fan-out | 네이티브 그래프 추적/스트리밍 | 상태 reducer 충돌 관리 복잡, 0.2 Send 동작 검증 부담 |

A안은 deepagents의 "task tool이 subagent를 호출하고 최종 결과만 회수"하는 모델과 동형이며, 기존 코드 재사용이 최대다.

### 4.4 신규 노드 ③: `result_aggregator`

**역할**: `task_results`를 통합하여 단일 `final_response`(또는 `output_file`)를 생성한다.

- 단일 task: 해당 결과를 그대로 통과 (기존 `output_generator` 출력과 동일).
- 복합 task: 각 task 결과를 **순서대로 묶어** LLM으로 통합 요약 + 부분 실패 안내(D-005 `db_result_summary` 패턴 계승).
- 문서 생성(Excel/Word) task가 포함되면 해당 `output_file`을 우선 반환.
- 가능한 한 기존 `output_generator`를 재사용/확장한다.

### 4.5 State 확장 (`src/state.py`)

```python
class AgentState(TypedDict):
    # ... 기존 필드 유지 ...
    # === deepagents 오케스트레이션 (신규) ===
    task_plan: list[dict]            # intent_planner 결과 (TaskSpec 목록; 각 항목에 status: pending/in_progress/completed/failed)
    task_results: dict[str, dict]    # {task_id: {response, output_file, source, error}}
    is_composite: bool               # task 2개 이상 여부
```

`create_initial_state()`에 기본값(`task_plan=[]`, `task_results={}`, `is_composite=False`) 추가.

> **상태추적·격리 정합(§2.6)**: `task_plan` 각 항목의 `status`로 Planning 상태추적(P1·P2)을 충족하고,
> `agent_orchestrator`가 `task_plan`을 반환값에 포함해 갱신을 영속화한다. subagent 입력은
> `_make_isolated_input`으로 필터링하여 SubAgent 부분 격리(S3)를 충족한다.

### 4.6 그래프 변경 (`src/graph.py`)

```python
if config.enable_deepagent_orchestration:
    graph.add_node("intent_planner", partial(intent_planner, llm=llm, app_config=config))
    graph.add_node("agent_orchestrator", partial(agent_orchestrator, llm=llm, app_config=config))
    graph.add_node("result_aggregator", partial(result_aggregator, llm=llm, app_config=config))
    graph.add_edge("field_mapper", "intent_planner")
    graph.add_edge("intent_planner", "agent_orchestrator")
    graph.add_edge("agent_orchestrator", "result_aggregator")
    graph.add_edge("result_aggregator", END)
elif config.enable_semantic_routing:
    ...  # 기존 semantic_router 경로 (변경 없음)
else:
    graph.add_edge("field_mapper", "schema_analyzer")  # 레거시
```

- 기존 `semantic_router`/`multi_db_executor`/`result_merger` 등 **노드는 삭제하지 않는다**.
  `data_query` subagent가 내부적으로 재사용하며, 플래그 비활성 시 기존 경로로도 동작해야 하기 때문.

### 4.7 설정 (`src/config.py`)

```python
class AppConfig(BaseSettings):
    enable_deepagent_orchestration: bool | None = None   # 신규 (None=미입력: 멀티 DB면 기본 활성)
```

- 환경변수 `ENABLE_DEEPAGENT_ORCHESTRATION`로 제어. **미입력(None) 시 멀티 DB 환경에서 기본 활성**(신규 경로가 기본 동작 — D-037, 2026-06-16). `=false`로 `semantic_router` 회귀, `=true`로 강제 활성. `semantic_routing`과 **상호 배타**(둘 다 활성이면 orchestration 우선).
- 명시값은 pydantic-settings가 `.env`·OS env에서 필드로 직접 읽음(`model_post_init`에서 `os.getenv` 미사용 — Known Mistakes 2026-06-10 준수). 미입력만 `model_post_init`이 `multi_db` 기준으로 해석.
- HITL(SQL/구조 승인)·체크포인트는 기존 설정 그대로 적용. 단 복합 task 중 HITL 인터럽트 처리 방식은 §6 리스크 참조.

### 4.8 Clean Architecture 계층 배치

- 신규 `src/orchestration/` 패키지: `intent_planner.py`, `agent_orchestrator.py`, `result_aggregator.py`, subagent 헬퍼.
- 계층: **orchestration**(graph와 동급, application 노드를 조합). `prompts`는 prompts 계층.
- ⚠ `scripts/arch_check.py`로 계층 위반 검사 필수 (의존 방향: application → orchestration → interface).
  orchestration이 application 노드를 호출하는 것은 허용(바깥 방향). 역방향 import 금지.

### 4.9 현재 `semantic_router` 코드의 deepagents 치환 매핑 (★ 현재 코드 기준)

본 절은 현재 구현(`src/routing/semantic_router.py`, `src/graph.py`, 각 의도 노드)의 **의도 분석**과
**의도별 실행**을 deepagents 패턴으로 1:1 치환하는 구체 설계다.

#### 4.9.1 의도 분석 치환: `semantic_router` → `intent_planner`

현재 `semantic_router`의 의도 분석은 **2계층**이다(`semantic_router.py:67-205`):

| 계층 | 현재 분기 (우선순위) | 조건 State (생성 출처) | 결과 intent |
|------|--------------------|----------------------|------------|
| **A. deterministic** | ① `pending_synonym_reuse` (router:67) | `cache_management` 노드가 set (`cache_management.py:429`) | cache_management |
| **pre-route** | ② `synonym_registration`+`pending_synonym_registrations` (router:80) | input_parser 파싱 + `field_mapper._build_pending_registrations` (`field_mapper.py:500`) | synonym_registration |
| | ③ `mapped_db_ids` (router:101) | `field_mapper`가 set (`field_mapper.py:140`) | data_query (DB 고정) |
| **B. LLM 분류** | ④⑤ `_llm_classify` (router:159) | LLM 응답 | data_query / cache_management / general_inference / alarm_query |

**치환 규칙**:
- **계층 A는 `intent_planner`의 deterministic pre-check로 그대로 이식**한다(멀티턴 pending 결합 보존 — D-013).
  플래그가 있으면 LLM 분해를 **스킵**하고 단일 task plan을 즉시 반환한다.
- **계층 B만 LLM 복합 분해로 대체**한다. 기존엔 단일 intent였으나, planner는 **복합 의도를 task 목록**으로
  분해하고 각 task에 agent를 할당한다. 의도 판별 규칙(cache/alarm/general 우선순위)은 기존
  `prompts/semantic_router.py`에서 이식한다.

```python
async def intent_planner(state, *, llm, app_config):
    q = state["user_query"]
    # [계층 A] semantic_router 우선순위 ①~③ 이식 — pending 결합 보존, LLM 스킵
    if state.get("pending_synonym_reuse"):
        return _single_task_plan("cache_management", q)
    parsed = state.get("parsed_requirements", {})
    if parsed.get("synonym_registration") and state.get("pending_synonym_registrations"):
        return _single_task_plan("synonym_registration", q)
    if state.get("mapped_db_ids"):
        return _single_task_plan("data_query", q, db_ids=state["mapped_db_ids"])  # DB 고정 메타 전달
    # [계층 B] LLM 복합 분해 — 우선순위 ④⑤ 대체
    tasks = await _llm_decompose(llm, q, SUBAGENT_REGISTRY, app_config)   # registry description 기반 (S2)
    return {"task_plan": tasks, "is_composite": len(tasks) > 1, "current_node": "intent_planner"}
```

#### 4.9.2 의도별 실행 치환: `route_after_semantic_router` + 노드 → `SUBAGENT_REGISTRY` + handler

현재 `_INTENT_ROUTE_MAP`(`graph.py:109`) + 6-way 조건부 엣지(`graph.py:350`)를 registry로 일원화한다.

| 현재 intent / 분기 | 현재 실행 경로 (graph.py) | registry agent | handler → 호출 대상 노드 |
|--------------------|--------------------------|----------------|-------------------------|
| cache_management | `cache_management` → END | `cache_management` | `run_cache_management` → `cache_management(state', llm, app_config)` |
| synonym_registration | `synonym_registrar` → END | `synonym_registration` | `run_synonym_registration` → `synonym_registrar(state', app_config)` |
| general_inference | `general_inference` → END | `general_inference`(fallback) | `run_general_inference` → `general_inference(state', llm, app_config)` |
| data_query (multi) | `multi_db_executor`→`result_merger`→`result_organizer`→`output_generator` | `data_query` | `run_data_query_pipeline` (멀티 분기, §4.9.3) |
| data_query (single) | `schema_analyzer`→…→`output_generator` | `data_query` | `run_data_query_pipeline` (단일 분기, §4.9.3) |
| alarm_query | data_query 경로 | `alarm_query` | `run_data_query_pipeline` |

- handler는 각 노드의 **기존 시그니처를 그대로 호출**한다(`(state, *, llm, app_config)`). 신규 비즈니스 로직 없음.
- `state'` = `_make_isolated_input`이 만든 필터된 컨텍스트 + `user_query := task["sub_query"]` 주입.

#### 4.9.3 `run_data_query_pipeline`: 단일/멀티 DB 통합 (재시도 보존 주의)

현재 DB 라우팅(`semantic_router._llm_classify`의 **DB 분류부만**)을 `data_query` subagent 내부로 흡수한다.

```python
async def run_data_query_pipeline(task, isolated, *, llm, app_config):
    # 1. DB 선택 — 기존 _llm_classify의 DB 분류부 재사용 (intent 분류는 제외)
    targets = task.get("db_ids") or await classify_dbs(llm, task["sub_query"], app_config)
    s = {**isolated, "user_query": task["sub_query"],
         "target_databases": targets, "is_multi_db": len(targets) > 1,
         "active_db_id": targets[0]["db_id"]}
    # 2. 실행 — 단일/멀티 분기
    if s["is_multi_db"]:
        s.update(await multi_db_executor(s, llm=llm, app_config=app_config))
        s.update(await result_merger(s, app_config=app_config))
    else:
        s.update(await _run_single_db_pipeline(s, llm, app_config))   # 풀 검증+재시도 보존
    # 3. 정리
    s.update(await result_organizer(s, llm=llm, app_config=app_config))
    return {"organized_data": s["organized_data"], "source": targets}
```

> ⚠ **재시도 보존(R-09)**: `multi_db_executor`는 DB별 1회 재생성·간이 검증만 한다(`multi_db_executor.py:157`).
> 단일 DB의 풀 검증(`query_validator`)·재시도 루프(max 3회, `graph.py:41-93`)를 잃지 않도록, 단일 분기는
> `_run_single_db_pipeline`(기존 schema_analyzer→query_generator→query_validator→query_executor 재시도 루프를
> 함수로 감싼 헬퍼)로 처리한다. Phase 6에서 이 단일 파이프라인을 **컴파일 서브그래프**로 분리하면 루프가
> 그래프 네이티브로 보존되어 더 깔끔하다.

#### 4.9.4 subagent 결과 이질성 → `result_aggregator` 정규화

| agent | 반환 핵심 | 최종화 주체 |
|-------|----------|------------|
| cache_management / synonym_registration / general_inference | `final_response`(텍스트) **직접 생성** | 그대로 사용 |
| data_query / alarm_query | `organized_data`(구조화) | `output_generator` 호출 필요 |

- `result_aggregator`가 이 이질성을 흡수한다: data_query 계열은 `output_generator(s, llm, app_config)`로
  최종 응답/파일 생성, 텍스트 계열은 `final_response`를 그대로 수집.
- 단일 task: 해당 결과를 그대로 최종화(기존 동작과 동일). 복합 task: 순서대로 묶어 통합(§4.4).

#### 4.9.5 `graph.py` 변경 (AS-IS → TO-BE)

```python
# [AS-IS] semantic_router 모드 — 6-way 조건부 라우팅
graph.add_node("semantic_router", ...); graph.add_node("multi_db_executor", ...)
graph.add_node("result_merger", ...);   graph.add_node("cache_management", ...)
graph.add_node("synonym_registrar", ...); graph.add_node("general_inference", ...)
graph.add_edge("field_mapper", "semantic_router")
graph.add_conditional_edges("semantic_router", route_after_semantic_router, {...})

# [TO-BE] deepagent_orchestration 모드 (§4.6)
graph.add_node("intent_planner", ...); graph.add_node("agent_orchestrator", ...)
graph.add_node("result_aggregator", ...)
graph.add_edge("field_mapper", "intent_planner")
graph.add_edge("intent_planner", "agent_orchestrator")
graph.add_edge("agent_orchestrator", "result_aggregator")
graph.add_edge("result_aggregator", END)
```

- `cache_management`/`synonym_registrar`/`general_inference`/`multi_db_executor`/`result_merger` 및
  단일 DB 파이프라인 노드는 **그래프에 등록하지 않고 handler가 함수로 호출**(A안 supervisor).
- `route_after_semantic_router`/`_INTENT_ROUTE_MAP`은 deepagent 모드에서 **미사용**(registry가 대체).
  단 semantic_routing 모드 하위 호환을 위해 **삭제하지 않는다**.

#### 4.9.6 다양한 라우팅·의도 신호 보존 (현재 구현 점검 — ★ 위치→DB 선택 등)

기존 `semantic_router`는 **의도 분류 + DB 라우팅을 1회 LLM 호출**로 처리하며, 그 과정에서 위치·존·사용자 지정 DB
같은 **라우팅 신호**를 풍부하게 활용한다(`domain_config.aliases`, `prompts/semantic_router.py` 예시).
오케스트레이션은 이를 **2단계로 분리**(planner=agent+sub_query / `classify_dbs`=DB 선택)했으므로,
라우팅 신호가 단계 사이에서 **소실되지 않도록** 명시적으로 보존해야 한다. 아래는 현재 코드 기준 점검표다.

| # | 라우팅·의도 신호 | 현재 코드 근거 | 오케스트레이션 처리 | 상태 |
|---|---|---|---|---|
| 1 | **위치/존 → DB** (김포→`polestar_cm_gp`, 여의도→`polestar_cm_yd`, 은행→`polestar_b0`) | `domain_config.py` aliases(:82,:100,:64) + router 프롬프트 예시 | planner가 `sub_query`에 위치 보존 → `classify_dbs`가 alias로 DB 선택 | **갭(보완 필요)** |
| 2 | **사용자 직접 DB 지정** ("polestar에서") | router 프롬프트 `user_specified`/aliases | planner가 `sub_query`에 DB명 보존 → `classify_dbs`가 `user_specified=1.0` | **갭(보완 필요)** |
| 3 | **멀티 DB sub_query 분리** (서버사양+VM → polestar+cloud_portal) | router 프롬프트 `sub_query_context` 규칙 | `classify_dbs`가 target별 `sub_query_context` 산출 → SQL 생성엔 정제 context 사용 | 부분 |
| 4 | alarm_query vs data_query | router 프롬프트 우선순위 | planner 분류(둘 다 `run_data_query_pipeline`) | 보존 |
| 5 | cache 하위액션(캐시/유사어/컬럼·DB 설명/db-guide) | semantic_router 프롬프트 §"캐시 관리 의도" | planner→`cache_management`(노드가 내부 분기) | 보존 |
| 6 | deterministic pre-route(pending/synonym/mapped_db) | `semantic_router.py:67~126` | `intent_planner` 계층 A pre-check | 보존(R-10) |
| 7 | **위치 정보의 SQL 필터 누출 금지** | router 프롬프트 "sub_query_context에 위치 포함 금지" | `classify_dbs`의 `sub_query_context`(위치 제거)를 SQL 생성에 사용 | **갭(보완 필요)** |

**핵심 보완 — planner→classify_dbs 라우팅 신호 전달** (3건):

1. **planner 프롬프트 규칙 추가**(`prompts/intent_planner.py`): "DB는 선택하지 말되, 질의에 포함된
   **DB 식별 신호(폴스타 위치: 김포/여의도/은행/공동존, DB명: polestar/cloud_portal, 환경: 운영/개발/스테이징)는
   `sub_query`에 그대로 보존**하라. 이 신호는 DB 선택에 쓰이고 SQL 조건으로 변환되지 않는다." + 위치 포함 예시 2건.
2. **`classify_dbs` 충실화**(`subagents.py`):
   - (a) `_llm_classify` 호출 시 **`db_descriptions`(Redis 캐시) 주입을 복원**한다. 원래 `semantic_router`는
     `cache_mgr.get_db_descriptions()`를 전달(`semantic_router.py:150~163`)하나, 현재 `classify_dbs`는 **누락**(`subagents.py:112`).
   - (b) 반환 target의 **`sub_query_context`(위치 제거된 정제 질의)** 를 SQL 생성 입력으로 사용한다.
3. **`run_data_query_pipeline` 보완**(§4.9.3): 단일 DB 분기에서 `user_query`를 raw `sub_query`가 아니라
   **선택된 target의 `sub_query_context`** 로 설정한다(위치의 SQL 누출 방지 — 디멘전 7). 멀티 분기는 이미 target별 context 사용.

**현재 구현 상태(2026-06-16, 보강 완료)**: 위 보완 1~3을 **모두 반영**했다 — planner 프롬프트에 DB 식별 신호
보존 규칙 + 위치 예시(예시 4) 추가, `classify_dbs`에 `db_descriptions`(Redis 캐시) 주입 복원, 단일 DB는
`sub_query_context`(정제 질의)를 SQL 생성 입력으로 사용. 회귀 테스트 3건(`test_routing_signal_preservation`)
통과로 **신호 전달 경로의 구조적 갭은 해소**(R-14 완화). 단, 실제 위치→DB 분류 정확도는 LLM에 의존하므로 라이브
환경 E2E(보완 4)는 별도 검증 과제로 남는다.

**예시 트레이스 — "김포 폴스타에서 CPU 높은 서버 보여줘"**:

```
intent_planner → {agent: data_query, sub_query: "김포 폴스타에서 CPU 사용률 높은 서버 조회"}   ← "김포" 보존(보완 1)
classify_dbs("김포 폴스타…", db_descriptions=캐시)  → polestar_cm_gp 선택, sub_query_context="CPU 사용률 높은 서버 조회"  (보완 2)
run_data_query_pipeline → 단일 DB(polestar_cm_gp), SQL 생성 입력 = "CPU 사용률 높은 서버 조회"  ← 위치 누출 없음(보완 3)
```

### 4.10 결과 기반 후속 처리: task 관계 3패턴 (★ 사용자 요구 직결)

사용자 프롬프트는 (1) 여러 **독립 의도**를 담거나, (2) 한 작업의 **결과를 입력으로** 다음 작업을 하거나,
(3) 한 작업의 **결과를 평가해** 후속 작업을 동적으로 결정할 수 있다. 이를 3패턴으로 구분해 단계 배치한다.

> **후속 처리 주체(2026-06-16 확정)**: **에이전트 자동** — 단일 프롬프트 내에서 시스템이 결과를 평가해 후속 작업을
> 자동 수행한다(패턴 ②③이 핵심, Phase 2 우선). 사용자가 결과를 보고 **직접 다음 프롬프트를 입력**하는 멀티턴
> 후속은 기존 체크포인트 멀티턴(D-013)으로 처리하며 본 절 범위 밖이다(`intent_planner`가 `conversation_context` 참조).

| 패턴 | 정의 | 예시 | 계획 성격 | 배치 |
|------|------|------|----------|------|
| **① 독립 병렬** | task 간 의존 없음 | "캐시 갱신하고 서버 목록 조회" | 정적 | **Phase 1** (병렬) |
| **② 데이터 의존 순차** | task B가 task A **결과를 입력**으로 | "CPU 높은 서버 찾아 **그 서버들** 프로세스 분석" | 정적(계획 고정, 데이터만 흐름) | **Phase 1** (§4.10.1) |
| **③ 결과 조건부 재계획** | task A **결과를 평가**해 후속 task 생성/변경 | "**장애 있으면** 알람 이력도", "**0건이면** 다른 조건 재조회" | **동적**(계획이 결과에 따라 변함) | **Phase 2** (§4.10.2) |

#### 4.10.1 데이터 의존 체이닝 (패턴 ②, Phase 1)

- **planner**: task B에 `depends_on=[A]` + `input_from=[A]`를 부여하고, `sub_query`는 "선행 결과의 서버들에 대해…"처럼
  **결과 참조 의도**를 자연어로 기술한다(구체 값은 실행 시점에 채워짐).
- **orchestrator**: 레벨 실행 시 `_make_isolated_input(task, state, prior)`에서 `task["input_from"]`에 해당하는
  `prior` 결과를 추려 **subagent 입력 컨텍스트에 주입**한다.
- **`run_data_query_pipeline`**: 주입된 선행 결과를 **SQL 생성 컨텍스트**로 전달한다. 즉 `query_generator`(또는 래퍼)에
  "선행 task 결과 행" 컨텍스트를 추가해 `WHERE hostname IN (…)` 같은 **결과 기반 조건**을 생성하게 한다.
  → `query_generator` 입력에 `prior_rows` 옵션 컨텍스트를 더하는 **소규모 확장**만 필요(기존 프롬프트·노드는 유지).
- **결과 크기 제한**: 선행 결과가 대량이면 식별 키 컬럼만 추려 주입(토큰·`IN` 절 폭증 방지, R-12). 상한 초과 시 요약·경고.

```python
# orchestrator: input_from 결과 주입
def _make_isolated_input(task, state, prior):
    base = _filter_context(state, task)              # SubAgent 부분 격리 (S3)
    base["prior_rows"] = {tid: prior[tid].get("rows", []) for tid in task.get("input_from", [])}
    return base
```

#### 4.10.2 결과 기반 동적 재계획 (패턴 ③, Phase 2)

정적 계획으로는 "결과를 보고 **할지 말지 / 무엇을** 결정"하는 경우를 못 다룬다. 이를 위해
**실행 → 평가 → 재계획 루프**를 도입한다(deepagents `write_todos` 동적 재계획 대응).

- **신규 `replanner` 노드**: 현재까지의 `task_results`를 LLM으로 평가해 (a) 종료 또는
  (b) **후속 task 추가/수정**(`task_plan` 갱신)을 결정한다.
- **그래프 루프(Phase 2)** — Phase 1의 선형 흐름에 조건부 루프를 추가:
  ```
  agent_orchestrator → [route_after_orchestrator]
      ├─ 추가 처리 필요 → replanner → agent_orchestrator (재실행, 신규 task만)
      └─ 완료 → result_aggregator → END
  ```
- **안전장치**: `replan_count ≤ MAX_REPLAN`(예: 3) 초과 시 강제 종료(R-11). 각 재계획은 **신규 task만** 추가하고
  기존 완료 task는 보존한다(상태추적 P2 활용).
- 트랙 A에서 **LangGraph 조건부 엣지로 구현 가능**(tool-calling 불필요 — §5.1). 트랙 B에서는 deepagents
  `write_todos`가 네이티브로 제공한다.

> **Phase 1 / Phase 2 경계**: Phase 1은 패턴 ①②(계획이 사전 확정, 데이터만 흐름)까지 — **선형 그래프 유지**.
> 패턴 ③(계획이 실행 중 변함)은 루프가 필요하므로 Phase 2. 사용자 요구의 "결과를 보고 추가 처리"는
> 데이터만 넘기면 되는 경우(②)는 Phase 1에서, 후속 여부·내용을 판단해야 하는 경우(③)는 Phase 2에서 충족된다.

### 4.11 모호성 명료화 인터럽트 (Clarification HITL, Phase 1 훅 + Phase 4 구현)

사용자 질의의 **처리 방법이 모호**할 때(의도 판별 불확실, 대상 DB 미지정, 복수의 유효한 해석 등),
임의로 진행하지 않고 **사용자에게 선택지를 제시해 되묻는** 멀티턴 인터럽트를 도입한다.
deepagents **HumanInTheLoopMiddleware의 `respond`(질의-응답형)** 유형에 대응하며,
기존 `approval_gate`(노드 단위 `interrupt_before`, tool-calling 불필요 — `nodes/approval_gate.py`)와 **동형**으로 구현한다.
세 요소의 융합이다: **① 모호성 판단(intent_planner/Planning) + ② 되묻기 인터럽트(interrupt_before/HITL) + ③ 멀티턴 재개(체크포인터/D-013)**.

**감지 범위(2026-06-16 확정)**: **계획 단계만** — `intent_planner`가 의도/처리방법/대상 DB의 모호성을
감지했을 때만 되묻는다. subagent 실행 중(컬럼·테이블 매핑 모호 등) 되묻기는 복합 task 다중 인터럽트
재개 복잡성(R-03) 때문에 **범위 밖**(향후 과제).

**Phase 1 (훅만)**: `intent_planner` 출력 스키마에 선택적 `clarification_needed` 슬롯을 **예약**한다(§4.2).
Phase 1은 이를 방출만 하고 **보수적 기본 선택**(단일 task 폴백 등)으로 진행한다 — 인터럽트 미발생.

**Phase 4 (구현)**: 신규 `clarification_gate` 노드 + `interrupt_before`로 되묻기·재개를 구현한다.

```
intent_planner → [clarification_needed?]
   ├─ 없음 → agent_orchestrator (기존 흐름)
   └─ 있음 → clarification_gate (interrupt) → 사용자 선택 → intent_planner 재진입(선택 주입) → orchestrator
```

planner 출력(모호 시):

```json
{
  "clarification_needed": {
    "question": "어느 환경의 서버를 조회할까요?",
    "options": ["김포 운영", "여의도 개발", "전체"],
    "reason": "대상 DB가 명시되지 않음"
  },
  "tasks": [ /* 잠정 계획 */ ]
}
```

- **상태 재사용**: 기존 HITL 필드(`awaiting_approval`/`approval_context`/`approval_action`, D-013)에
  `approval_context.type = "clarification"` 변형을 추가한다. 신규 state 최소화.
- **멀티턴 재개**: 체크포인터(D-013)로 ask→사용자 응답→`intent_planner` 재진입. 단일/멀티턴 통합 단일 경로 유지.
- **안전장치**: 되묻기 횟수 상한(`MAX_CLARIFY`, 예: 2) 초과 시 보수적 기본 선택으로 자동 진행(무한 되묻기 방지 — R-13).
- **tool-calling 불필요**: `approval_gate`와 동일한 노드 인터럽트 → FabriX로 정상 동작(트랙 A).

---

## 5. 단계적 구현 로드맵 (Phase 1 ~ Phase 9)

**갱신(2026-06-17)**: 당초 'FabriX tool 호출 불가 → 트랙 A 단일화, 트랙 B 제거'(2026-06-16)였으나,
**폐쇄망 vLLM 오케스트레이터**로 tool-calling 블로커가 해소되어 **트랙 B(deepagents 실제 패키지)를 주 경로로
도입**한다(D-037 갱신, Plan 49). 트랙 A는 폴백으로 보존.

- **트랙 A (구현 완료 · 폴백 보존 · tool-calling 불필요)** — **Phase 1~6**. deepagents 패턴을 자체 LangGraph 노드로 구현. vLLM 미서빙 시 회귀 경로.
- **Phase 7 (tool calling 구조화 출력)** — 트랙 B의 vLLM tool-calling으로 가능.
- **Phase 8 (실제 패키지 도입) / Phase 9 (운영 전환)** — **부활·확정/재개**: 폐쇄망 vLLM 오케스트레이터 + FabriX 워커로 도입(Plan 49).

각 Phase는 독립 착수 가능하다. **Phase 1만 본 문서에서 상세(§4·§8·§9)** 하고,
Phase 2~9는 아래 **계획 개요**로 정의하며, 착수 시 각각 별도 plan 문서(plan 49~)로 상세화한다.
(이는 모든 단계를 지금 과잉 설계하지 않기 위함이다 — 각 Phase 착수 시점에 코드 현황을 재확인하고 설계를 확정한다.)

### Phase 1 — 정적 의도 분해 + subagent 위임 (트랙 A, **본 계획 주 구현**)

- 목표: 복합 의도를 sub-task로 분해(정적)하고 순차/병렬 실행 후 결과 통합.
- 대상 기능: Planning(정적), SubAgent(위임 흉내).
- 상세: §4(설계) · §8(구현 순서) · §9(테스트). 산출물: `src/orchestration/` 3개 노드.

### Phase 2 — 결과 기반 동적 재계획 + 진행상태 추적 (트랙 A, ★ 사용자 요구 직결)

- 목표: 한 작업의 **결과를 평가해 후속 task를 동적 생성/변경**(패턴 ③, §4.10.2) — 실행→평가→재계획 루프. + SSE 진행률 스트리밍.
- 대상 기능: **TodoListMiddleware** (리스트 전체 교체식 동적 재계획).
- Gap 근거: 현재 정적 계획 — **결과 기반 후속/조건부 처리 불가**. `retry_count`(state.py:92)만 존재, 진행 추적·재계획 부재. SSE 진행 이벤트 미구현(api/routes/query.py).
- 접근: 신규 `replanner` 노드 + `agent_orchestrator` **조건부 루프**(`route_after_orchestrator`, `MAX_REPLAN` 안전장치). API `progress` 이벤트. **tool-calling 불필요**(LangGraph 조건부 엣지).
- 산출물: plan 49 / 선행: Phase 1. **우선도 고**(사용자가 명시한 "결과 보고 추가 처리"의 동적 케이스).

### Phase 3 — State/체크포인트 offloading (트랙 A, **D-013 이행**)

- 목표: 대용량 `query_results`/`db_results`를 외부 저장(Redis/파일)에 두고 state엔 **참조키+요약만** 보유 → 체크포인트 경량화·멀티턴 복원 가속.
- 대상 기능: **FilesystemMiddleware** (context offloading).
- Gap 근거: `query_results`(state.py:86)/`db_results`(123)/`organized_data`(89) 전량 누적, 매 노드 체크포인트 저장(graph.py). **D-013 "대량 데이터 요약본 교체" 미구현**. max_rows=10,000(MCP).
- 접근: `result_organizer`/`result_aggregator`에 대용량 페이로드 오프로딩 헬퍼.
- 산출물: plan 50 / 선행: Phase 1 (우선도 **고**).

### Phase 4 — HITL 도구별 세분화 + 모호성 명료화 (트랙 A)

- 목표: (a) task/도구 단위 승인 정책(data_query=SQL 승인, cache_management=확인, synonym_registration=확인 등). 복합 질의에서 task별 차등 승인. (b) **모호성 명료화 인터럽트** — 처리 방법 모호 시 사용자에게 선택지 되묻기(§4.11, 계획 단계 감지 한정).
- 대상 기능: **HumanInTheLoopMiddleware** (`interrupt_on` per-tool + `respond` 질의-응답형).
- Gap 근거: `approval_gate`/`structure_approval_gate` 2종 고정(graph.py:315-321), task별 세분화 불가. `approval_context`에 task 정보 없음. **모호성 되묻기 경로 부재**(planner가 불확실해도 임의 진행).
- 접근: (a) `interrupt_before` 일반화 + `approval_context`에 task_id/agent 부가. 복합 질의 SQL 승인은 순차 분리(R-03 연계). (b) 신규 `clarification_gate` 노드(`approval_gate` 동형) + `intent_planner` 모호성 감지(Phase 1 예약 슬롯 활성화) + `MAX_CLARIFY` 안전장치 + `intent_planner` 재진입.
- 산출물: plan 51 / 선행: Phase 1(`clarification_needed` 슬롯 훅).

### Phase 5 — 멀티턴 컨텍스트 압축 (트랙 A)

- 목표: 대화 길이 임계 초과 시 이전 turn LLM 요약 주입 → 토큰·체크포인트 절감.
- 대상 기능: **SummarizationMiddleware**(자동 ≈85%) + SummarizationToolMiddleware(수동).
- Gap 근거: `messages` 단순 슬라이싱(context_resolver.py `MAX_HISTORY_TURNS=10`), 내용 압축 없음(state.py:130 `add_messages` 누적).
- 접근: `context_resolver` 확장(기존 `results_summary` 로직 발전) + 토큰 임계 트리거.
- 산출물: plan 52 / 선행: Phase 3 권장(offloading과 시너지).

### Phase 6 — subagent 격리 강화 (트랙 A → B 준비)

- 목표: 각 subagent를 독립 컴파일 서브그래프로 분리, 입력 필터링+출력 회수만 → `schema_info` 등 전역 덮어쓰기/오염 제거.
- 대상 기능: **SubAgentMiddleware 격리 / CompiledSubAgent**.
- Gap 근거: `multi_db_executor`가 전체 state 공유, `schema_info`(state.py:136)가 마지막 DB로 덮어써짐, descriptions/synonyms 전역 누적(79-82).
- 접근: 1차 자체(langgraph subgraph compile + 입출력 어댑터), 2차 패키지 `CompiledSubAgent` 직결.
- 산출물: plan 53 / 선행: Phase 1.

### Phase 7 — tool-calling 기반 구조화 출력 (**보류** — FabriX tool 호출 불가)

- 상태: **보류**. FabriX는 tool 호출이 불가하여 `with_structured_output`(tool/provider 기반)·tool calling 전환이 어렵다.
- 결정: **현행 프롬프트 + JSON 파싱(`extract_json_from_response`)을 유지**한다(파싱 신뢰성은 R-07로 관리).
- 재고 조건: tool-calling 지원 LLM으로 교체하거나 FabriX가 안정적 json_mode를 제공할 경우에만 재검토.

### Phase 8 — deepagents 실제 패키지 도입 (**부활·확정** — vLLM 오케스트레이터, 2026-06-17)

- 상태: **부활·확정**(D-037 갱신, 상세 Plan 49). 폐쇄망 **vLLM 오케스트레이터**(Qwen3.5-9B, `ChatOpenAI`
  네이티브 tool-calling)가 tool-calling 블로커를 해소하여 deepagents 실제 패키지를 도입한다. **FabriX는 워커**(실질 응답처리).
- 도입 요건: `langchain-core` 1.2→`>=1.4.7` 업글 + `langchain`/`deepagents` wheel 반입(폐쇄망) + vLLM 인프라.
  기존 `SUBAGENT_REGISTRY`를 `@tool`로 노출(FabriX는 도구 내부 호출). 백엔드는 vLLM 가용성으로 선택(미서빙 시 semantic_router).
- 상세 계획·PoC 게이트(R-B2 tool-calling 신뢰도)·구현 순서는 **Plan 49**. PoC 결과는 `docs/deepagents_poc_report.md`.

### Phase 9 — 운영 스택 전환 (**재개** — 트랙 B 진입)

- 상태: **재개**(2026-06-17). 트랙 B 도입에 따라 `langchain-core` 1.4.7 업글 + `langchain`/`deepagents` wheel 반입
  + vLLM 인프라를 운영 스택에 반영한다.
- 선행: Phase 8 PoC 성공(오픈모델 tool-calling 신뢰도 게이트, Plan 49 §7).

### 향후 재고 항목 (트랙 B 도입 후)

- **MemoryMiddleware(AGENTS.md) / AsyncSubAgentMiddleware**는 deepagents 실제 패키지(tool-calling) 전제다. **vLLM 오케스트레이터 도입(트랙 B, 2026-06-17)으로 이용 가능**해졌으나, Phase 8(코어 도입) 안정화 이후 별도 평가로 **후순위 도입** 검토한다(현 시점 미차용).
- **Skills는 트랙 B 전제가 아니다** — 메타 노출(L1)은 tool-calling 무관이라 트랙 A에서 가능하다. 단 기존 `config/db_profiles/`와 중복되어 **선택적**이며, 별도 검토는 **§5.2** 참조.

### 로드맵 요약

| Phase | 제목 | 트랙 | 대상 기능 | 우선도 |
|-------|------|------|----------|--------|
| 1 | 정적 분해 + 위임 | A | Planning(정적)·SubAgent | 진행 중 |
| 2 | **결과 기반 동적 재계획** + 진행추적 | A | TodoList | **고** (사용자 요구) |
| 3 | State offloading | A | Filesystem | **고** (D-013) |
| 4 | HITL 세분화 + **모호성 명료화** | A | interrupt_on(`respond`) | 중 |
| 5 | 컨텍스트 압축 | A | Summarization | 중 |
| 6 | subagent 격리 강화 | A→B 준비 | SubAgent/CompiledSubAgent | 중 |
| 7 | tool calling / 구조화 출력 | B | response_format | 트랙 B에서 vLLM tool-calling으로 가능 |
| 8 | **실제 패키지 도입** | B | 전체 스택 | **부활·확정** (vLLM 오케스트레이터, Plan 49) |
| 9 | 운영 전환 | B | 전체 스택 | **재개** (Phase 8 성공 시) |

**권장 착수 순서**: 트랙 A는 **1 → (2 ∥ 3) → 4 → 5 → 6**(Phase 1·2 구현 완료). **트랙 B(Phase 8·9)는 2026-06-17
재진입** — 폐쇄망 vLLM 오케스트레이터로 deepagents 실제 패키지 도입(Plan 49). Phase 7(구조화 출력)은 트랙 B의 vLLM tool-calling으로 가능.

### 5.1 tool-calling 의존성 매트릭스 (★ FabriX tool 호출 불가 확정)

> **갱신(2026-06-17)**: FabriX 자체는 tool 호출 불가이나, **별도 vLLM 오케스트레이터**가 tool-calling을 담당하고
> FabriX는 워커로 분리되어 **트랙 B(실제 패키지)를 주 경로로 도입**한다(D-037, Plan 49). 트랙 A(1~6)는 폴백 보존.
> (아래 표의 '8~9 불가'는 2026-06-16 기록 → 본 갱신으로 대체.)

| Phase | tool-calling | 처리 |
|-------|-------------|------|
| **1~6 (트랙 A, 구현 완료·폴백)** | **불필요** | `intent_planner`·`agent_orchestrator`·subagent handler 모두 **프롬프트 + JSON 파싱**(`extract_json_from_response`). 기존 `semantic_router`와 동일 방식. 위임은 코드 dispatch → **FabriX로 정상 동작**. vLLM 미서빙 시 폴백 경로 |
| **7 (구조화 출력)** | 트랙 B에서 가능 | vLLM tool-calling/`with_structured_output` 사용 가능(트랙 B). 트랙 A는 현행 JSON 파싱 유지 |
| **8~9 (트랙 B, 도입)** | **필수 → vLLM이 제공** | deepagents 실제 패키지를 **vLLM 오케스트레이터**(네이티브 tool-calling)로 구동, **FabriX는 워커**(도구 내부 실질 응답처리). 백엔드는 vLLM 가용성으로 선택(미서빙 시 `semantic_router`) |

**핵심 함의**: deepagents의 _패턴_은 트랙 A로 tool-calling 없이 구현된다(Phase 1~6, 구현 완료·폴백 보존).
deepagents _실제 패키지_(트랙 B)는 tool-calling이 필수인데, 이를 **폐쇄망 vLLM 오케스트레이터**로 충족하여 **도입**한다
(2026-06-17, Plan 49). FabriX는 실질 응답처리(워커)를 담당한다.
→ **트랙 B를 주 경로로 도입하되, vLLM 미서빙 시 `semantic_router`로 회귀하는 가용성 옵션을 둔다.**

**트랙 B 진입(2026-06-17 확정)**: 폐쇄망 vLLM 오케스트레이터로 tool-calling 충족 → Phase 8 도입(Plan 49, D-037).

### 5.2 Skills 기능 검토 (트랙 A 선택적 확장)

deepagents skills를 본 계획에 포함할지 검토한 결과를 정리한다.

| skills 계층 | 동작 | tool-calling | 트랙 A 가능? |
|------------|------|-------------|-------------|
| **L1 메타 노출** | `SKILL.md`의 name+description을 시스템 프롬프트에 주입 | **무관** | **가능** |
| **L2 온디맨드 전체 로드** | 에이전트가 `read_file`로 본문 로드 | **의존** | 코드 기반 선택으로 **대체 가능**(planner가 task→스킬 매핑 주입) |

- **기존 메커니즘과 중복**: 본 프로젝트는 이미 `config/db_profiles/*.yaml`(`query_guide`·`known_attributes` 등)로
  도메인·절차 지식을 **코드 기반 온디맨드 주입**한다(plan 32·34). 이는 skills의 핵심 가치(작업별 지식 모듈화 + 필요 시 주입)를 사실상 보유.
- **결론**: deepagents 실제 skills 미들웨어는 온디맨드 로드(`read_file`)가 tool-calling 의존이라 FabriX로는 불가하나,
  **패턴(SKILL.md 형식 + 메타 노출 + 코드 기반 선택 주입)은 트랙 A에서 자체 구현 가능**하다.
- **권장**: 별도 skills 시스템을 **신설하지 않는다**. 필요 시 **SUBAGENT_REGISTRY의 per-agent `prompt` 슬롯**(§4.3, SubAgent S6)에
  작업별 지식을 주입하는 형태로 흡수하며, `db_profiles`와의 중복 정리를 먼저 한 뒤 **선택적**으로 도입한다(우선순위 낮음).

---

## 6. 리스크 및 대응

| # | 리스크 | 심각도 | 대응 |
|---|--------|--------|------|
| R-01 | ~~langchain 1.x 강제 → 대규모 breaking change~~ → **하향(2026-06-16)**: 운영이 **이미 1.x**(core 1.2.30/langgraph 1.1.6) | **Low** | deepagents는 마이너 업글(1.2→1.4)+wheel 반입(폐쇄망)만. 메이저 마이그레이션 아님. 실질 블로커는 R-08(tool-calling) |
| R-02 | planner 분해 오류(과다 분해/누락) | Med | 단일 task 폴백 기본값. 분해 정확도 테스트 케이스로 검증. 보수적 프롬프트(불확실 시 1개 task) |
| R-03 | 복합 task 중 HITL 인터럽트(SQL/구조 승인) 발생 시 흐름 중단·재개 복잡 | Med | 1단계는 **HITL이 필요한 task를 병렬 그룹에서 분리해 순차 처리**. 인터럽트 발생 task만 중단, 나머지 결과 보존. 초기엔 복합 질의에서 SQL 승인 비활성 권장 |
| R-04 | 병렬 task 간 공유 State 경쟁(캐시 무효화 등) | Med | subagent에 **state 스냅샷 복사본** 전달, 결과는 `task_results`로만 수집(reducer 충돌 회피). 쓰기성 작업(cache_management)은 의존성으로 직렬화 |
| R-05 | D-004(LLM 전용 시멘틱 라우팅)와 충돌 | — | §7 의사결정으로 명시 해소. semantic_router 로직은 data_query subagent 내부 DB 분류로 **흡수·재사용**(폐기 아님) |
| R-06 | 응답시간 증가(planner LLM 1회 추가 + 다중 작업) | Low | 단일 task는 planner를 거쳐도 1회 LLM 추가뿐. 독립 작업 병렬화로 상쇄 |
| R-07 | 커스텀 LLM의 JSON 미준수로 task_plan 파싱 실패 | Low | 기존 `extract_json_from_response` 재사용 + 폴백 |
| R-08 | FabriX tool 호출 불가 | **해소(2026-06-17)** | **별도 vLLM 오케스트레이터**(`ChatOpenAI`→vLLM, 네이티브 tool-calling)가 deepagents를 구동, FabriX는 워커(실질 응답처리). 트랙 B 도입(Plan 49, D-037). vLLM 미서빙 시 `semantic_router` 폴백, 트랙 A(1~6) 폴백 보존 (§5.1) |
| R-09 | data_query를 `multi_db_executor`로 통합 시 단일 DB의 풀 검증·재시도(max 3회) 손실 | Med | 단일 분기는 `_run_single_db_pipeline`로 기존 `query_validator`/재시도 루프 보존(§4.9.3). Phase 6에서 컴파일 서브그래프로 분리 |
| R-10 | 멀티턴 pending 분기(synonym reuse/registration)를 planner가 누락 | Med | `intent_planner` 계층 A pre-check로 `semantic_router` 우선순위 ①~③를 **그대로 이식**(§4.9.1). pending E2E 회귀 테스트 필수 |
| R-11 | 결과 기반 동적 재계획(패턴 ③)이 무한 루프 | Med(Phase 2) | `replan_count ≤ MAX_REPLAN`(예: 3) 강제 종료, 재계획은 **신규 task만** 추가·완료 task 보존 (§4.10.2) |
| R-12 | 데이터 의존(패턴 ②) 시 대량 선행 결과 주입 → 토큰·`IN` 절 폭증 | Med | `input_from` 주입 시 **식별 키 컬럼만** 추출 + 행수 상한, 초과 시 요약·경고 (§4.10.1) |
| R-13 | 모호성 되묻기(§4.11)가 반복(무한 되묻기) 또는 복합 task 중 다중 인터럽트로 재개 복잡 | Med(Phase 4) | 감지를 **계획 단계로 한정**(§4.11), `MAX_CLARIFY`(예: 2) 초과 시 보수적 기본 선택으로 자동 진행. 복합 task 다중 인터럽트는 R-03 정책(순차 분리) 적용 |
| R-14 | planner가 `sub_query` 추출 시 **위치/DB 지정 신호를 누락** → `classify_dbs`가 잘못된 DB 선택(위치→DB 라우팅 불안정) | **Med (Phase 1 보강)** | planner 프롬프트에 **라우팅 신호 보존 규칙 + 위치 예시**(§4.9.6), `classify_dbs`에 `db_descriptions` 복원, target `sub_query_context`를 SQL 생성에 사용. 위치 라우팅 E2E 회귀 테스트(김포/여의도/은행/공동존 → 올바른 DB) |

---

## 7. 의사결정 영향 (docs/02_decision.md)

- **D-004 (LLM 전용 시멘틱 라우팅)**: 단일 의도 라우팅 → 복합 의도 분해로 **확장**. 상태 갱신 및
  "향후 수정 시 고려사항"에 D-037 참조 추가 필요. `semantic_router`의 DB 분류 로직은 폐기하지 않고
  `data_query` subagent에 흡수(재사용).
- **신규 D-037 (deepagents 기반 의도 분해 오케스트레이션)**: 본 계획을 의사결정으로 등재.
  단계적 하이브리드 도입, 1단계 자체 구현 / 2단계 격리 PoC 명시.
- **D-005 (멀티 DB 순차 + 부분 실패)**: orchestrator의 부분 실패 허용·결과 누적 정책의 상위 일반화. 정책 계승.

> CLAUDE.md 지침에 따라, 본 계획 승인 시 구현 착수 전 `docs/02_decision.md`에 D-037을 추가하고 D-004를 갱신한다.
> (본 계획서 작성과 함께 D-037 초안을 decision 문서에 "계획 확정/구현 예정" 상태로 등재)

---

## 8. 구현 순서 (1단계)

1. `src/config.py` — `enable_deepagent_orchestration` 플래그 추가 → verify: 설정 로드 테스트
2. `src/state.py` — `task_plan`/`task_results`/`is_composite` 추가 + 초기값 → verify: state 확장 테스트
3. `src/prompts/intent_planner.py` — 분해+분류 프롬프트 작성 (registry `description` 기반 위임 규칙 포함, 기존 `prompts/semantic_router.py` 의도 규칙 이식)
4. `src/orchestration/intent_planner.py` — **계층 A pre-check(pending/synonym/mapped_db 우선순위 ①~③ 이식)** + 계층 B LLM 분해 + 단일 task 폴백 + `status="pending"` 초기화 + **`clarification_needed` 슬롯 예약(Phase 1은 방출만, 인터럽트는 Phase 4 §4.11)** → verify: 분해 단위 테스트(단일/복합/폴백/status/**pending 분기**)
5. `src/orchestration/subagents.py` — `SUBAGENT_REGISTRY` + handler(`run_cache_management`/`run_synonym_registration`/`run_general_inference`/`run_data_query_pipeline`) + `classify_dbs`(기존 `_llm_classify` DB 분류부 재사용) + `_run_single_db_pipeline`(단일 DB 풀 검증·재시도 보존) + `_make_isolated_input` → verify: 각 handler가 기존 경로와 동일 출력, isolated 입력이 전체 state가 아님, 단일 DB 재시도 동작
6. `src/orchestration/agent_orchestrator.py` — 위상정렬·레벨 병렬 실행 + status 전이 → verify: 순차/병렬/부분실패/status 갱신 테스트
7. `src/orchestration/result_aggregator.py` — 결과 통합 → verify: 단일/복합 통합 응답 테스트
8. `src/graph.py` — 플래그 분기·엣지 추가 → verify: `scripts/arch_check.py` 통과 + 그래프 빌드 테스트
9. 통합 테스트: 복합 질의 E2E, 하위 호환(플래그 off) 회귀
10. `docs/02_decision.md` D-037 등재 / D-004 갱신

### 8.1 Phase 1 보강 — 라우팅 신호 보존 (§4.9.6, R-14)

> Phase 1 골격 구현 완료 후 식별된 보완 항목. 위치→DB 등 **다양한 라우팅 의도**의 안정적 처리를 위해 적용한다.
> **상태(2026-06-16): 1~3 구현·단위 테스트 완료**(`test_routing_signal_preservation` 3건). 4(위치 E2E)는 라이브 LLM 필요로 통합 환경 검증 과제.

1. `src/prompts/intent_planner.py` — **DB 식별 신호 보존 규칙 + 위치 포함 예시 2건**(김포/여의도) 추가 → verify: planner가 위치를 `sub_query`에 보존
2. `src/orchestration/subagents.py` `classify_dbs` — (a) `_llm_classify` 호출에 **`db_descriptions` 주입 복원**(Redis 캐시), (b) 반환 target의 **`sub_query_context`(정제 질의)** 노출 → verify: 위치 alias로 올바른 DB 선택, 정제 질의 반환
3. `src/orchestration/subagents.py` `run_data_query_pipeline` — 단일 DB 분기 SQL 생성 입력을 **`sub_query_context`** 로 설정(위치 SQL 누출 방지) → verify: 위치가 SQL `WHERE`에 미포함
4. 위치 라우팅 E2E 회귀: 김포→`polestar_cm_gp`, 여의도→`polestar_cm_yd`, 은행→`polestar_b0`, 사용자 지정 "polestar에서"→`polestar`

---

## 9. 테스트 계획

| 테스트 | 검증 내용 |
|--------|----------|
| `test_intent_planner_single` | 단일 작업 질의 → task 1개 |
| `test_intent_planner_composite` | 복합 질의 → 2개 이상 task, agent 분류·의존성 정확 |
| `test_intent_planner_fallback` | LLM/파싱 실패 → 단일 data_query task 폴백 |
| `test_orchestrator_sequential` | depends_on 체인 → 순서 보장 |
| `test_orchestrator_parallel` | 독립 task → 병렬 실행(gather) |
| `test_orchestrator_partial_failure` | 1개 task 실패 → 나머지 결과 보존(D-005), 해당 task `status="failed"` |
| `test_task_status_transition` | 실행 중 status `pending→in_progress→completed/failed` 전이 (Planning P2 정합) |
| `test_subagent_registry` | registry로 agent 해석, 미지정 agent → fallback(general-purpose) 위임 (SubAgent S1·S5) |
| `test_isolated_input` | subagent handler 입력이 전체 `AgentState`가 아닌 필터된 컨텍스트임 (SubAgent S3) |
| `test_pre_route_pending` | 계층 A pre-check — `pending_synonym_reuse`/`synonym_registration`/`mapped_db_ids` → 해당 단일 task, LLM 분해 스킵 (§4.9.1, R-10) |
| `test_data_query_single_vs_multi` | 단일 DB → `_run_single_db_pipeline`(재시도 보존), 멀티 DB → `multi_db_executor` (§4.9.3, R-09) |
| `test_result_heterogeneity` | 텍스트 agent(`final_response`) vs data_query(`organized_data`) 결과 정규화 (§4.9.4) |
| `test_data_dependent_chaining` | 패턴 ② — task B가 task A 결과 행(`input_from`)을 입력받아 결과 기반 SQL 조건(`WHERE … IN`) 생성 (§4.10.1) |
| `test_input_from_size_limit` | `input_from` 주입 시 식별 키 컬럼만·행수 상한 적용 (R-12) |
| `test_result_aggregator_merge` | 복합 결과 통합 응답 생성 |
| `test_backward_compat_flag_off` | 플래그 off 시 기존 semantic_router 경로 동일 동작 |
| `test_arch_check` | orchestration 계층 위반 없음 |

---

## 10. 변경 범위 요약

### 신규 파일
- `src/orchestration/__init__.py`
- `src/orchestration/intent_planner.py` — 계층 A pre-check + 계층 B LLM 분해 (§4.9.1)
- `src/orchestration/agent_orchestrator.py` — supervisor (위상정렬·병렬·상태전이)
- `src/orchestration/subagents.py` — `SUBAGENT_REGISTRY` + handler + `classify_dbs` + `_run_single_db_pipeline` + `_make_isolated_input` (§4.9.2·4.9.3)
- `src/orchestration/result_aggregator.py` — 결과 이질성 정규화 + 통합 (§4.9.4)
- `src/prompts/intent_planner.py`
- `tests/test_orchestration/*`

### 수정 파일
- `src/config.py` — 플래그 추가
- `src/state.py` — 3개 필드 추가
- `src/graph.py` — 플래그 분기·노드/엣지 등록
- `.env.example` — `ENABLE_DEEPAGENT_ORCHESTRATION` 문서화
- `docs/02_decision.md` — D-037 추가 / D-004 갱신

### 변경하지 않는 파일 (재사용)
- `src/routing/semantic_router.py` — DB 분류 로직을 data_query subagent가 재사용 (삭제 금지)
- `src/nodes/multi_db_executor.py`, `result_merger.py`, `cache_management.py`, `synonym_registrar.py`, `general_inference.py` — subagent가 호출
- 단일 DB 파이프라인 노드 전체 — 재사용

---

## 11. 참고 자료

- deepagents 공식 문서(overview): https://docs.langchain.com/oss/python/deepagents/overview
- deepagents subagents: https://docs.langchain.com/oss/python/deepagents/subagents
- GitHub: https://github.com/langchain-ai/deepagents
- PyPI(0.6.10, 2026-06-13): https://pypi.org/project/deepagents/
- 관련 결정: D-004, D-005 (`docs/02_decision.md`)
- 관련 계획: 09-semantic-routing.md
