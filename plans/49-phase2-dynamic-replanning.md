# 49. Phase 2 — 결과 기반 동적 재계획 (deepagents 실제 패키지 · vLLM 오케스트레이터 + FabriX 응답처리)

> 작성일: 2026-06-16
> **개정: 2026-06-17 — Track B 전환.** 동적 재계획을 자체 구현(Track A, 구현 완료) 대신
> **deepagents 실제 패키지**로 달성한다. tool 호출은 **별도 vLLM**(폐쇄망 내부 호스팅, 네이티브 tool-calling)이
> 담당하고, **실질적인 응답처리(자연어→SQL→DB 조회→결과 정리·최종 응답)는 FabriX(KBGenAIChat)** 가 담당한다.
> 기존 Track-A replanner 구현은 **삭제하지 않고 플래그 게이트로 보존**한다(폴백·회귀 안전).
> 상위 계획: `plans/48-deepagents-intent-orchestration.md` (§5 Phase 8 부활, §5.1 트랙 B 재진입 조건)
> 관련 결정: D-037 — **갱신 완료(2026-06-17)**: 실제 deepagents 도입(vLLM 오케스트레이터 + FabriX 워커) 확정 (§6)
> 트랙: **B (tool-calling 필수)** — deepagents 미들웨어를 vLLM 오케스트레이터로 구동

---

## 1. 개요 및 목표

### 1.1 배경 — 왜 Track B(vLLM)인가

Phase 1은 질의를 **정적으로 1회 분해**(`intent_planner`)하고, Track-A Phase 2(기 구현)는 LangGraph 조건부
엣지로 **결과 기반 동적 재계획**을 자체 구현했다. 이는 deepagents의 _패턴_을 tool-calling 없이 모방한 것이다.

본 개정은 deepagents의 **실제 패키지**를 도입한다. 실제 패키지의 4개 미들웨어
(Planning `write_todos` · SubAgents `task` · Filesystem · Summarization)는 전부 **진짜 tool-calling**으로
동작하므로, 메인 모델이 `bind_tools` + `tool_calls`를 안정적으로 내야 한다.

- FabriX(KBGenAIChat / FabriXAPIClient)는 tool 호출이 불가/불안정하다(D-037, R-08). → 메인 구동 불가.
- Gemini는 네이티브 tool-calling을 지원하나 **외부(Google) egress가 필요** → 폐쇄망에서 불가 가능성.
- ⇒ **폐쇄망 내부에 vLLM을 호스팅**하여 tool-calling 가능한 오픈 모델을 서빙하면 egress 없이 해결된다.
  vLLM은 **OpenAI 호환 API**(`/v1/chat/completions`)를 제공하므로, **이미 wheel에 있는 `langchain-openai`의
  `ChatOpenAI`**(`base_url`=vLLM)로 **네이티브 `bind_tools`** 를 그대로 쓸 수 있다(커스텀 클라이언트 불필요).

### 1.2 역할 분리 (핵심)

| 구성 | 담당 | tool-calling | 모델 |
|------|------|-------------|------|
| **오케스트레이터** (제어/사고) | 의도 분해, **동적 재계획**(`write_todos`), subagent 위임 결정 | **필요** | **vLLM** (`ChatOpenAI`→vLLM 엔드포인트) |
| **워커** (실질 응답처리) | 자연어→SQL 생성, DB 조회, 결과 정리, **최종 자연어 응답** | 불필요 | **FabriX (KBGenAIChat)** |

> deepagents는 "어떤 작업을 어떤 순서로 할지"(제어)를 vLLM tool-calling으로 결정하고,
> "그 작업의 실제 내용"(자연어→SQL→데이터→응답)은 기존 FabriX 파이프라인이 수행한다.
> **사용자에게 나가는 최종 자연어 응답도 FabriX**(`result_aggregator`/`output_generator`)가 생성한다 —
> vLLM은 도구 호출·재계획의 제어 평면에만 관여한다.

### 1.3 성공 기준

1. 폐쇄망 vLLM(OpenAI 호환)에 `ChatOpenAI`로 `bind_tools` → **tool_calls 왕복이 안정 동작**(PoC 게이트).
2. deepagents 실제 패키지가 vLLM 모델로 **`write_todos`(동적 재계획) · `task`(위임)** 를 정상 구동한다.
3. 기존 `SUBAGENT_REGISTRY` 5개 작업이 **deepagents tool로 노출**되고, 그 내부 실행은 **FabriX 파이프라인**이 수행한다.
4. 결과 조건부 질의(패턴 ③)가 deepagents 네이티브 동적 재계획으로 **후속 작업을 추가 실행**한다.
5. 최종 사용자 응답은 **FabriX가 생성**한다(vLLM 자유 서술이 그대로 노출되지 않는다).
6. **vLLM 가용성으로 백엔드를 선택**한다 — 플래그 on + vLLM 서빙 시 Track B, **vLLM 미서빙/off 시
   semantic_router(기존 방식)** 로 무변경 동작한다(회귀 없음, opt-out 보존). Track-A replanner는 별도
   플래그로 명시 선택 시에만 사용(보존).
7. 폐쇄망 도입 조건(wheel 반입·버전 업글·vLLM 인프라)이 **문서화·검증**된다.

> 비고: Track-A Phase 2(기 구현)의 성공기준(증분 추가·MAX_REPLAN·보수적 종료)은 **폴백 경로로 유지**된다(부록 A).

---

## 2. 아키텍처

```
사용자 질의
   │
   ▼
┌──────────────────────────────────────────────────────────────────┐
│ deepagents create_deep_agent                                       │
│   model = ChatOpenAI(base_url=<vLLM>/v1)   ← 네이티브 tool-calling   │
│   내장 미들웨어: write_todos(동적 재계획) / task(위임) / filesystem    │
│                                                                    │
│   vLLM 모델이 tool_calls 로 아래 도구들을 호출 (제어 평면)            │
│     ├─ query_infra_db(sub_query)        ┐                          │
│     ├─ query_alarm(sub_query)           │  각 도구 = 기존            │
│     ├─ manage_cache(sub_query)          │  SUBAGENT_REGISTRY        │
│     ├─ register_synonym(sub_query)      │  handler 의 얇은 래퍼      │
│     └─ general_answer(sub_query)        ┘                          │
└───────────────────────────────┬──────────────────────────────────┘
                                 │  (어댑터: tool args ⇄ AgentState,
                                 │   결과 ⇄ ToolMessage 직렬화)
                                 ▼
        ┌───────────────────────────────────────────────┐
        │ 도구 내부 = FabriX(KBGenAIChat) 파이프라인       │  ← 실질 응답처리
        │   run_data_query_pipeline 등:                   │
        │   schema_analyzer → query_generator(FabriX)     │
        │   → query_validator → query_executor(DBHub)     │
        │   → result_organizer(FabriX)                    │
        └───────────────────────────────────────────────┘
                                 │
                                 ▼
        result_aggregator / output_generator (FabriX)  ← 최종 자연어 응답
```

- **제어 평면(vLLM)**: 무엇을·어떤 순서로·결과에 따라 다음에 무엇을 — `write_todos`/`task`/tool_calls.
- **데이터 평면(FabriX)**: 실제 SQL 생성·DB 조회·결과 정리·최종 응답 — 기존 파이프라인 무수정 재사용.

### 2.1 도구 노출 방식 (tool vs SubAgent)

- **채택: `@tool` 래핑** — 기존 5개 handler를 LangChain `@tool`로 감싸 메인 vLLM 에이전트에 직접 노출한다.
  도구 내부는 **결정적 Python 파이프라인 + FabriX 호출**이라 tool-calling이 불필요하다 → FabriX가 절대
  tool 호출을 강요받지 않는다(안정성 핵심).
- **비채택: deepagents SubAgent(`task`)에 FabriX 모델 직결** — SubAgent는 _자체 tool-calling 루프_를 도는
  에이전트라, 모델을 FabriX로 두고 도구를 주면 FabriX가 tool 호출을 강요받아 깨진다. 따라서 FabriX는
  SubAgent의 모델이 아니라 **도구 구현 내부**에서만 호출한다.
- (선택) 상위 그룹핑이 필요하면 SubAgent는 **vLLM 모델**로 두고 그 내부에서 위 도구를 호출하는 형태만 허용.

---

## 3. 폐쇄망 도입 요구 (버전 · wheel · 인프라)

### 3.1 패키지 버전/wheel (실측: `wheels/{windows,linux,mac}/`)

| 패키지 | 현재 반입 | 필요 | 조치 |
|--------|----------|------|------|
| `langchain-core` | **1.2.30** | `>=1.4.7` (deepagents) | ⚠️ **마이너 업글 + wheel 반입** |
| `langchain` (메타) | **없음** | `>=1.3.9` | ❌ **wheel 반입** |
| `deepagents` | **없음** | (반입 시점 최신) | ❌ **wheel 반입** (+ 전이 의존) |
| `langchain-openai` | **1.1.13** | (오케스트레이터 클라이언트) | ✅ 보유 — `ChatOpenAI`로 vLLM 연결 |
| `langgraph` | 1.1.6 | 1.x | ✅ 충족 (deepagents 호환 재확인) |
| `pydantic` | 2.13.1 | 2.x | ✅ 충족 |

- 정확한 deepagents 핀(`langchain-core`/`langchain`/`langgraph` 하한)은 **반입 시점 deepagents 버전의
  메타데이터로 재확정**한다. `langchain-core` 1.2→1.4 업글의 전이 호환성(langgraph 1.1.6, checkpoint-sqlite
  3.0.3 등)을 격리 환경에서 먼저 검증한다.
- 반입 절차: 외부에서 `pip download deepagents langchain "langchain-core>=1.4.7" --platform ... --only-binary=:all:`
  로 3개 플랫폼 wheel 수집 → `wheels/{os}/` 추가 → `requirements.txt`/`pyproject.toml` 하한 갱신.

### 3.2 vLLM 인프라

- **서버**: 폐쇄망 내부 vLLM(OpenAI 호환) 기동. `/v1/chat/completions` 노출.
- **모델**: **Qwen3.5-9B** (tool-calling/function calling 지원). vLLM의 `--enable-auto-tool-choice` +
  모델별 `--tool-call-parser` 설정 필요.
  - ⚠️ **세대별 파서 확인**: Qwen3 계열은 **thinking(reasoning) 모드**가 tool-calling과 상호작용하므로,
    정확한 HF 모델 ID·`--tool-call-parser`·`--reasoning-parser`·thinking 비활성 여부를 **PoC에서 확정**한다
    (tool-calling 신뢰도 R-B2와 직결).
- **검증 게이트**: 선정 모델이 **병렬/연속 tool_calls를 안정적으로** 내는지(deepagents ReAct 루프 부하)를
  PoC에서 실측한다(오픈 모델은 상용 대비 tool-calling 신뢰도 편차 큼 — R-B2).

---

## 4. 통합 설계

### 4.1 오케스트레이터 LLM 팩토리 (`src/llm.py`)

```python
def create_orchestrator_llm(config) -> BaseChatModel:
    """deepagents 구동용 tool-calling LLM. provider로 vLLM(운영) / Gemini(테스트, §4.7) 선택."""
    if config.orchestrator.provider == "gemini":     # 테스트/PoC 전용 (egress 필요)
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(
            model=config.orchestrator.model or "gemini-2.5-pro",
            google_api_key=config.orchestrator.api_key or config.llm.gemini_api_key,
            temperature=0.0,
        )
    from langchain_openai import ChatOpenAI           # 기본: vLLM (OpenAI 호환)
    return ChatOpenAI(
        base_url=config.orchestrator.base_url,        # 예: http://vllm-host:8000/v1
        api_key=config.orchestrator.api_key or "EMPTY",
        model=config.orchestrator.model,              # Qwen3.5-9B
        temperature=0.0,
    )
```

- 기존 `create_llm`(FabriX/KBGenAIChat)은 **워커용**으로 유지. 신규 `create_orchestrator_llm`만 추가.
- **provider 교체로 오케스트레이터를 vLLM↔Gemini 전환** — 도구 래퍼·`build_deep_agent`·프롬프트는 **무변경**
  (orchestrator 주입 지점만 바뀜). Gemini는 네이티브 `bind_tools` 지원이라 deepagents가 그대로 구동된다(§4.7).

### 4.2 도구 래퍼 (`src/orchestration/deepagents_tools.py`, 신규)

```python
from langchain_core.tools import tool

def build_tools(llm_worker, app_config):  # llm_worker = FabriX
    @tool
    async def query_infra_db(sub_query: str) -> str:
        """인프라 DB(서버 사양·사용량·모니터링)를 조회한다."""
        task = {"task_id": "t", "agent": "data_query", "sub_query": sub_query, ...}
        isolated = _make_isolated_input(task, _ambient_state(), prior={})
        result = await run_data_query_pipeline(task, isolated, llm=llm_worker, app_config=app_config)
        return _serialize_for_tool(result)   # organized_data/query_results → 요약 텍스트(ToolMessage)
    # query_alarm / manage_cache / register_synonym / general_answer 동형
    return [query_infra_db, ...]
```

- **재사용**: `SUBAGENT_REGISTRY` handler·`_make_isolated_input`·`input_from` 주입(R-12)을 그대로 호출.
- **신규 비즈니스 로직 없음** — 어댑터(상태 변환·결과 직렬화)만 추가.
- `_serialize_for_tool`: 대량 행 → 키 컬럼·행수 상한 요약(토큰 폭증 방지, §부록 R-12 재사용).

### 4.3 deepagents 조립 (`src/orchestration/deep_agent.py`, 신규)

```python
from deepagents import create_deep_agent   # API 표면은 반입 버전으로 재확인

def build_deep_agent(config):
    orchestrator = create_orchestrator_llm(config)     # vLLM
    worker = create_llm(config)                        # FabriX
    tools = build_tools(worker, config)
    return create_deep_agent(
        tools=tools,
        model=orchestrator,
        instructions=ORCHESTRATOR_INSTRUCTIONS,        # 위임·재계획 규칙 + "최종 응답은 도구 결과 기반"
    )
```

- 동적 재계획은 deepagents **`write_todos`(TodoListMiddleware)** 가 네이티브 제공 → Track-A `replanner` 대체.
- **최종 응답 처리**: deepagents 종료 후 도구가 반환한 `organized_data`를 모아 **FabriX `result_aggregator`/
  `output_generator`로 최종 자연어 응답을 생성**한다(vLLM 자유 서술을 그대로 노출하지 않음 — 성공기준 5).

### 4.4 상태 어댑터

- **입력**: deepagents 메시지/state → 도구 `sub_query`(문자열). ambient 컨텍스트(thread_id·user_id·
  allowed_db_ids 등)는 클로저/контextvar로 주입(도구 시그니처는 `sub_query`만 노출해 vLLM 호출 단순화).
- **출력**: handler 반환 dict(`organized_data`/`query_results`/`source`) → ToolMessage용 요약 텍스트 +
  원본은 별도 수집기에 보관(최종 FabriX 응답 생성용).
- AgentState TypedDict ⇄ deepagents state 완전 통합은 Phase 6(`CompiledSubAgent`) 영역 — 본 단계는
  **도구 경계의 얇은 어댑터**까지만(범위 최소화).

### 4.5 플래그 / 설정 (`src/config.py`)

```python
class OrchestratorConfig(BaseSettings):       # 신규, env_prefix="ORCHESTRATOR_"
    provider: Literal["vllm", "gemini"] = "vllm"  # gemini=테스트/PoC 전용 (§4.7)
    base_url: str = ""          # vLLM /v1 엔드포인트
    model: str = "Qwen3.5-9B"   # gemini 사용 시 gemini 모델명으로 설정(예: gemini-2.5-pro)
    api_key: str = ""           # vLLM: 보통 미사용("EMPTY") / gemini: 미설정 시 LLM_GEMINI_API_KEY 폴백
    timeout: int = 120
    health_timeout: int = 3     # 가용성 health check 타임아웃(vLLM)

enable_deepagents_package: bool = False  # Track B 게이트 (명시적 opt-in)
```

> 구현 메모(2026-06-17): tri-state(`bool|None`) 대신 **명시적 opt-in(`bool=False`)** 채택 — Track B는
> vLLM 인프라가 필수라 멀티 DB 자동 활성화(enable_deepagent_orchestration의 tri-state)가 부적절. 런타임
> 게이팅은 vLLM health check(§4.6)가 담당.

- 분기 우선순위 (§4.6 가용성 분기와 연동):
  - `enable_deepagents_package=on` **AND vLLM 가용** → **Track B (deepagents+vLLM)**.
  - 그 외(플래그 off **또는 vLLM 미서빙**) → **semantic_router (기존 방식)**.
  - Track-A(`enable_deepagent_orchestration`)는 **자동 폴백이 아니라** 명시 선택 시에만 사용(보존, §5).
  - **상호 배타**, 기존 경로 무변경(opt-out 보존).
- `.env.example`에 `ORCHESTRATOR_BASE_URL`/`ORCHESTRATOR_MODEL`/`ORCHESTRATOR_API_KEY` 추가.
  FabriX(`FABRIX_*`) 설정은 워커용으로 그대로 유지.

### 4.6 그래프/진입 — vLLM 가용성 기반 옵션 선택 (A+B 결합)

핵심: **vLLM 서빙 여부를 옵션으로 선택**한다. vLLM 가용 시 deepagents(Track B), 불가 시 기존
`semantic_router` 경로를 사용한다. 통합 방식 (A) 노드 래핑과 (B) 진입 분기를 **배타 선택이 아니라 함께**
적용하여, 두 경우를 **하나의 진입 분기로 모두 수용**한다.

- **선택 신호 = 플래그 + 오케스트레이터 가용성**:
  - `enable_deepagents_package=on` **그리고** 오케스트레이터 가용(vLLM=`/v1/models` health check 통과 /
    Gemini=api_key 존재 — §4.7) → **`deep_agent` 노드 (Track B)**.
  - 그 외(플래그 off **또는** 오케스트레이터 미가용) → **`semantic_router` 경로 (기존 방식)**.
- **그래프 구성**:
  - deepagents `create_deep_agent`(자체 컴파일 LangGraph)를 **단일 노드로 래핑**(옵션 A)하여 그래프에 편입.
  - 진입 라우터 `route_orchestration_backend`가 가용성 신호로 `deep_agent` / `semantic_router`를
    선택(옵션 B의 진입 분기 성격) → **A와 B를 함께** 적용.

```python
def orchestrator_available(config) -> bool:
    if config.orchestrator.provider == "gemini":   # 테스트: api_key 유무 (§4.7)
        return bool(config.orchestrator.api_key or config.llm.gemini_api_key)
    return vllm_healthy(config.orchestrator.base_url, config.orchestrator.health_timeout)

def route_orchestration_backend(state, config) -> str:
    # 오케스트레이터 가용 + 플래그 on → Track B, 아니면 기존 semantic_router
    if config.enable_deepagents_package and orchestrator_available(config):
        return "deep_agent"        # Track B (vLLM 또는 Gemini 오케스트레이터)
    return "semantic_router"       # 미가용/off → 기존 방식
```

- **가용성 판정 시점**: 그래프 빌드/기동 시 1회 health check로 백엔드 확정(결정적·비용 0). 추가로 런타임에
  vLLM 연결 오류 발생 시 **graceful fallback**(로그 + `semantic_router` 경로로 best-effort 회귀) — 선택 구현(R-B10).
- 체크포인터(SQLite/Postgres) 호환성·`astream_events` 진행 이벤트 노출을 PoC에서 확인.

### 4.7 테스트 모드: Gemini 오케스트레이터 (vLLM 대체 · PoC 전용)

vLLM 인프라 구축(R-B1) 전에 deepagents tool-calling 파이프라인을 검증하기 위해, **오케스트레이터만 Gemini로
교체**하여 테스트한다. Gemini(`ChatGoogleGenerativeAI`, langchain-google-genai)는 네이티브 `bind_tools`·병렬
`tool_calls`를 지원하므로, **도구 래퍼·`build_deep_agent`·프롬프트는 무변경**이고 `ORCHESTRATOR_PROVIDER=gemini`
설정만으로 전환된다(orchestrator 주입 지점만 바뀜 — §4.1).

- **목적**: deepagents 설치(§7-1)만 되면 **vLLM 없이** 위임·동적 재계획·도구 왕복(§7 3~6단계)을 조기 검증한다.
  오픈모델 tool-calling 신뢰도 게이트(R-B2)와 별개로 **파이프라인 정합성(도구 스키마·ToolMessage·최종 FabriX 응답)**
  을 먼저 확정 → 이후 vLLM에서는 모델 신뢰도만 비교 검증하면 된다.
- **역할 분리는 동일**: Gemini는 제어 평면(tool-calling)만, **실질 응답처리는 FabriX(KBGenAIChat) 워커**가 담당.
- **설정**: `ORCHESTRATOR_PROVIDER=gemini`, `ORCHESTRATOR_MODEL=gemini-2.5-pro`(또는 `gemini-2.0-flash`).
  API 키는 기존 `LLM_GEMINI_API_KEY`/`GOOGLE_API_KEY` 재사용(별도 `ORCHESTRATOR_API_KEY`로 분리도 가능).
  가용성 판정은 health check 대신 **api_key 유무**(§4.6 `orchestrator_available`).
- **deepagents wheel은 양 모드 공통 필요**: Gemini 모드가 절감하는 건 **§7-2의 vLLM 인프라**이며, §7-1(wheel
  반입·`langchain-core` 1.4.7 업글)은 **동일하게 선행**이다. (단, `langchain-google-genai`는 **이미 프로젝트
  의존성**(pyproject/`wheels/` 4.2.2)이라 deepagents와 달리 추가 반입 불요 — 기존 `_create_gemini` 경로 재사용.)
- **⚠️ 한계 — 테스트 전용**: Gemini는 **외부(Google) egress가 필요**하여 **폐쇄망 운영에는 부적합**(D-037이
  vLLM을 택한 이유). 운영 기본값은 `provider=vllm`로 유지하고, **Gemini는 개발/PoC 환경에서만** 사용한다(R-B11).

---

## 5. 동적 재계획: 네이티브 vs 보존

| 항목 | Track A (기 구현, 보존) | Track B (본 계획) |
|------|------------------------|-------------------|
| 동적 재계획 | 자체 `replanner` 노드 + 조건부 엣지 루프 | deepagents **`write_todos`** 네이티브 |
| 제어 | LangGraph 조건부 엣지(코드) | vLLM **tool_calls** |
| 상한/안전 | `MAX_REPLAN`·보수적 종료 | deepagents 루프 가드 + recursion limit |
| 활성 | `enable_deepagent_orchestration` | `enable_deepagents_package` |

- 기존 `replanner.py`/`prompts/replanner.py`/관련 테스트는 **삭제하지 않는다**(폴백·회귀 기준).
- 두 경로는 플래그로 상호 배타. Track B 미설정/실패 시 Track A로 안전하게 회귀 가능.

---

## 6. 의사결정 영향 (`docs/02_decision.md`)

- **D-037 갱신 완료(2026-06-17)**: D-037이 "FabriX tool 호출 불가 확정 → 트랙 B(deepagents 실제 패키지)
  제거"에서 **"실제 deepagents 패키지 도입 — vLLM 오케스트레이터 + FabriX 워커, 트랙 B 재진입"** 으로
  갱신됨. **상태 필드 · 세부 설계 항목 · 변경 이력**에 반영(append-only, 2026-06-16 기록 보존).
- **본 결정은 D-037 하위로 등재**(신규 D 번호 미부여 — 사용자 결정 2026-06-17). 핵심:
  - vLLM(OpenAI 호환, 폐쇄망) 오케스트레이터 tool-calling + **FabriX 워커(실질 응답처리)** 분리. Gemini egress
    회피 위해 vLLM 채택. 기존 `SUBAGENT_REGISTRY`를 `@tool`로 노출(FabriX는 도구 내부 호출 → tool-calling 미강요).
  - 백엔드는 vLLM 가용성 옵션(미서빙 시 `semantic_router`). Track-A `replanner`는 폴백 보존.
  - 대안 기각: Gemini(egress 불가)·KBGenAIChat 에뮬레이션(다중 tool call·긴 루프에서 불안정).
- **Plan 48 갱신 완료(v4, 2026-06-17)**: §5 Phase 8("제거")→**"부활·확정"**, Phase 9(운영 전환)→**"재개"**,
  §5.1 매트릭스·R-08 리스크를 트랙 B 도입으로 갱신.

---

## 7. 구현 순서 (PoC 우선 · 게이트형)

> **구현 현황(2026-06-17)**: 인프라 비의존 스캐폴딩 **완료·검증**(arch_check error 0, 신규 단위 16건 통과,
> orchestration 회귀 68건 통과) — `OrchestratorConfig`+플래그, `create_orchestrator_llm`(vLLM/ChatOpenAI),
> `deepagents_tools`(@tool 래퍼+직렬화), `deep_agent`(vllm_healthy/select_orchestration_backend/build_deep_agent
> lazy-import), `prompts/orchestrator`, `.env.example`.
> **그래프 실제 배선(§4.6 / step 7) 완료(2026-06-17)**: `build_graph`가 빌드 시 `select_orchestration_backend`로
> 백엔드를 확정하여 `deep_agent` 노드를 등록·연결(`field_mapper → deep_agent → END`). 신규
> `deep_agent.run_deep_agent`(노드)가 `build_deep_agent` 조립 에이전트를 `ainvoke`하고 최종 응답을 추출하며,
> deepagents 미설치(RuntimeError) 시 빌드 시점 `_deep_agent_buildable` 점검 + 런타임 양쪽에서 `semantic_router`로
> 안전 폴백한다(회귀 없음).
> **deepagents 0.6.10 실제 설치 + step 6(최종 FabriX 응답) 완료(2026-06-17)**: 폐쇄망 wheel을 기다리지 않고 현
> 환경에 deepagents 0.6.10 설치(→`langchain-core 1.4.7`/`langchain 1.3.9`/`langgraph 1.2.5` 업글, `langchain-openai
> 1.3.2` 추가; R-B3 전이 비호환 실증 해소 — 신규 실패 0건). **실측 결과**: `create_deep_agent` 인자는 `instructions`가
> 아니라 **`system_prompt`**(코드 수정), 반환 state top-level 키는 `['files','messages']`로 **도구 결과 전용 키 없음** —
> 도구 결과는 `messages`의 `ToolMessage`로만 존재. step 6 구현: `build_tools`/`_run_subagent_tool`에 **원본 결과
> 수집기(collector)** 추가 → `run_deep_agent`가 종료 후 collector 원본을 `task_plan`/`task_results`로 재구성해
> **FabriX `result_aggregator`로 최종 응답 생성**(오케스트레이터 자유 서술 미노출). 실제 `create_deep_agent` 런타임으로
> 도구 호출 → collector → FabriX 재정리 E2E 통과, 실제 vLLM `ChatOpenAI` 오케스트레이터로 조립 성공.
> `tests/test_orchestration/test_deep_agent_wiring.py`(step6·실패키지 통합 포함) 통과.
> **잔여(라이브 인프라 의존)**: 아래 2(실 vLLM HTTP 왕복·tool_calls 신뢰도 R-B2), 5(라이브 동적 재계획 E2E),
> 폐쇄망 3플랫폼 wheel 반입(현재는 개발 환경 단일 설치).

1. **wheel 반입 + 설치 검증** — `deepagents`·`langchain`·`langchain-core>=1.4.7`(+전이) 3플랫폼 wheel 수집,
   격리 venv에서 설치 → verify: import·기존 회귀(langgraph 1.1.6 호환) 통과.
2. **vLLM 기동 + tool-calling PoC** — 선정 모델 서빙, `ChatOpenAI(base_url=vLLM).bind_tools([...])` 단순
   왕복 → verify: tool_calls 정상 생성/파싱(병렬·연속 포함). **불안정 시 모델/파서 교체 또는 중단(R-B2)**.
   - **(테스트 대안 · §4.7) Gemini 오케스트레이터 PoC** — vLLM 인프라 전, `ORCHESTRATOR_PROVIDER=gemini`로
     3~6단계(위임·재계획·도구 왕복·최종 FabriX 응답)를 먼저 검증. 파이프라인 정합성 확정 후 vLLM으로 전환.
3. **도구 1종 래핑** — `query_infra_db`(= `run_data_query_pipeline`) `@tool` + 어댑터 → verify: vLLM이 도구
   호출, FabriX가 내부 실행, 결과 ToolMessage 반환 E2E.
4. **deepagents 조립** — `create_deep_agent(tools, model=vLLM)` + 나머지 4개 도구 → verify: 위임·`write_todos`
   동작.
5. **동적 재계획 E2E(패턴 ③)** — "결과에 장애 서버 있으면 알람 조회" / "0건이면 재조회" → verify: 후속 도구
   호출이 결과 기반으로 발생.
6. **최종 응답 FabriX화** — 수집된 도구 결과 → `result_aggregator`/`output_generator`(FabriX) → verify:
   사용자 응답이 FabriX 생성(성공기준 5).
7. **백엔드 선택(가용성 분기) + 회귀** — 플래그 + vLLM health check로 `deep_agent`/`semantic_router`
   선택, **vLLM 미서빙 시 semantic_router 무변 동작** → verify: `arch_check --ci` exit 0, health check
   on/off 양쪽 경로, 기존 orchestration·그래프 회귀 통과.
8. **PoC 보고** — 결과·한계·운영 권고를 `docs/deepagents_poc_report.md`에 기록(D-037 갱신 완료, §6).

---

## 8. 테스트 계획

| 테스트 | 검증 내용 |
|--------|----------|
| `test_orchestrator_llm_factory` | `create_orchestrator_llm`가 `ChatOpenAI`(base_url=vLLM) 생성 |
| `test_vllm_bind_tools_roundtrip` | (통합) vLLM `bind_tools` → tool_calls 생성·파싱 |
| `test_tool_wrapper_invokes_fabrix` | `@tool` 내부가 FabriX 파이프라인 실행, 결과 직렬화 |
| `test_tool_result_serialization_limit` | 대량 행 → 키 컬럼·행수 상한 요약(R-12) |
| `test_deep_agent_delegation` | (통합) 위임 → 도구 호출 → ToolMessage 회수 |
| `test_dynamic_replan_followup` | (통합) 결과 조건부 후속 도구 호출(패턴 ③) |
| `test_final_response_by_fabrix` | 최종 응답이 FabriX(result_aggregator) 생성 |
| `test_flag_gate_track_b` | `enable_deepagents_package` on/off 분기, off 시 기존 경로 무변 |
| `test_backend_selection_by_vllm_health` | vLLM health check 통과 → `deep_agent`, 실패/off → `semantic_router` |
| `test_track_a_fallback_preserved` | Track-A replanner 경로·테스트 회귀 무변(보존) |
| `test_arch_check` | orchestration 계층 위반 없음 |

> (통합) 표시는 vLLM·deepagents 패키지 의존 → CI 스킵/마커 분리(폐쇄망 자원 가용 시 실행).

---

## 9. 변경 범위 요약

### 신규 파일
- `src/orchestration/deepagents_tools.py` — `SUBAGENT_REGISTRY` handler → `@tool` 래퍼 + 어댑터
- `src/orchestration/deep_agent.py` — `create_deep_agent` 조립 + 최종 FabriX 응답
- `src/prompts/orchestrator.py` — vLLM 위임·재계획 instructions
- `tests/test_orchestration/test_deep_agent.py`
- `docs/deepagents_poc_report.md` — PoC 결과(8단계)

### 수정 파일
- `src/llm.py` — `create_orchestrator_llm`(vLLM/`ChatOpenAI` + **Gemini provider 분기**, §4.7) 추가(기존 `create_llm` 유지)
- `src/config.py` — `OrchestratorConfig`(**provider vllm|gemini**, vLLM 설정) + `enable_deepagents_package` 플래그
- `src/graph.py` — `deep_agent` 노드 래핑 + `route_orchestration_backend`(vLLM 가용성 분기, vLLM 불가 시 semantic_router) + `vllm_healthy` health check
- `.env.example` — `ORCHESTRATOR_*` 추가
- `requirements.txt`/`pyproject.toml` — `deepagents`·`langchain`·`langchain-core>=1.4.7` 하한
- `wheels/{windows,linux,mac}/` — 신규 wheel 반입
- `docs/02_decision.md` — **D-037 갱신 완료(2026-06-17, 트랙 B 재진입)**

### 변경하지 않는 파일 (재사용·보존)
- `subagents.py`(handler·`_make_isolated_input`·`input_from` 그대로), `intent_planner.py`,
  `result_aggregator.py`, **`replanner.py`/`prompts/replanner.py`(Track-A 폴백 보존)**,
  FabriX 클라이언트(`fabrix_kbgenai.py`)·기존 7노드 파이프라인.

---

## 10. 리스크 및 대응

| # | 리스크 | 심각도 | 대응 |
|---|--------|--------|------|
| R-B1 | 폐쇄망 vLLM 인프라(GPU·모델 서빙) 구축/운영 부담 | High | 인프라 선행 요건으로 명시. 모델 크기·동시성 사이징 PoC에서 산정 |
| R-B2 | 오픈 모델 tool-calling 신뢰도 편차(위임·재계획 오작동) | High | PoC 2단계 게이트(병렬/연속 tool_calls 실측). 미달 시 모델/파서 교체 또는 Track-A 유지 |
| R-B3 | `langchain-core` 1.2→1.4 업글 전이 비호환(langgraph/checkpoint) | Med | 격리 venv 선검증, 기존 회귀 전수 통과 후 반입 |
| R-B4 | FabriX가 SubAgent 모델로 들어가 tool 호출 강요받아 깨짐 | Med | **도구 내부 호출만** 허용(§2.1). SubAgent 모델은 vLLM만 |
| R-B5 | deepagents state ⇄ AgentState 어댑터 복잡도·누수 | Med | 도구 경계 얇은 어댑터로 범위 한정. 완전 통합은 Phase 6 |
| R-B6 | 도구 결과 대량 주입 → 토큰·`IN` 절 폭증 | Med | 키 컬럼·행수 상한(R-12) 직렬화 재사용 |
| R-B7 | 최종 응답을 vLLM 자유 서술이 점유(FabriX 우회) | Med | 종료 후 FabriX `result_aggregator`로 응답 생성 강제(성공기준 5) |
| R-B8 | D-037(Track B 제거)와 충돌 | — | **해소**: D-037 갱신 완료(2026-06-17) — 트랙 B 재진입 확정, §6 |
| R-B9 | 두 LLM 경로(vLLM+FabriX)로 지연·디버깅 복잡도 증가 | Low | 제어/데이터 평면 로깅 분리, 진행 이벤트(astream_events) 노출 |
| R-B10 | vLLM 미서빙/일시 장애 시 동작 불가 | Med | **가용성 옵션 선택**(§4.6): 기동 health check로 백엔드 확정, vLLM 불가 시 **semantic_router 자동 사용**. 런타임 연결 오류 시 graceful fallback(best-effort) |
| R-B11 | Gemini 테스트 모드를 운영에 사용 → egress·폐쇄망 위반 | Med | Gemini는 **개발/PoC 전용** 명시(§4.7), 운영 기본값 `provider=vllm`. 가용성 판정·`.env.example`에 경고. deepagents wheel은 양 모드 공통 필요 |

---

## 부록 A. Track-A 동적 재계획 (기 구현 — 보존)

> 아래는 2026-06-16 구현 완료된 자체 구현(tool-calling 미사용) 설계이며, **삭제하지 않고 폴백으로 유지**한다.
> `enable_deepagent_orchestration` 경로에서 동작한다. 상세는 D-037(2026-06-16, Plan 49) 변경 이력 참조.

- **그래프**: `agent_orchestrator → route_after_orchestrator → replanner → route_after_replanner →
  {agent_orchestrator | result_aggregator}` 조건부 루프.
- **`replanner`**(`src/orchestration/replanner.py`): `task_results`를 LLM 평가 → 후속 task **증분 추가**
  (전체 교체 아님, R-A1) 또는 종료. `replan_count ≤ max_replan`(기본 3) 가드, 파싱 실패 시 보수적 종료.
- **State**: `replan_count`/`needs_replan`. **설정**: `max_replan: int = 3`.
- **원칙**: 완료 task·감사 로그·`task_results` 보존을 위해 신규 task만 append, 종료 시 `task_plan`/
  `replan_count` 미반환으로 reducer 충돌 방지. `input_from` 키 컬럼·행수 상한(R-12).
- **테스트**: `tests/test_orchestration/test_replanner.py`(종료/추가/보존/상한/파싱실패/완료스킵/주입/회귀/루프형/arch).

---

## 11. 참고

- 상위 설계: `plans/48-deepagents-intent-orchestration.md` §5(Phase 8 부활), §5.1(트랙 B 재진입 조건)
- 관련 결정: `docs/02_decision.md` D-037 (→ 본 계획으로 재활성, §6)
- deepagents: https://docs.langchain.com/oss/python/deepagents/overview
- vLLM tool calling: https://docs.vllm.ai/en/latest/features/tool_calling.html
- `ChatOpenAI`(OpenAI 호환 엔드포인트) — langchain-openai (wheel 보유)
