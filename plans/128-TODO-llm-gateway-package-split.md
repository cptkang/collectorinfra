# 128. LLM 호출 일원화 — 독립 LLM 게이트웨이 패키지 `llm_gateway/` (FabriX · vLLM · Gemini · MLX · Ollama 전부 · 본체·noise_gate·sre_agent는 HTTP API로 호출)

> **작성일**: 2026-09-30 · **개정**: 2026-09-30 **v2** — 사용자 지시 *"모든 llm을 llm gateway에서 처리하도록 계획을 수정하라."* 범위를 「워커 평면 FabriX ↔ OpenAI 호환」(v1)에서 **저장소의 모든 LLM API 호출**로 넓혔다.
> - 편입 대상: 오케스트레이터 평면(vLLM · deep agents · noise_gate 트랙 B) · Gemini · Ollama · MLX · sre_agent(HolmesGPT · litellm).
> - 확정으로 처리한 게이트: v1 G-6 「오케스트레이터·sre_agent 편입」 = **포함** · v1 G-10 「백엔드 선택 단위」 = **별칭별**(두 평면을 동시에 운용하므로 필수).
> - 신규 설계: tool-calling 통과 계약 · 별칭 가용성 판정 · 별칭별 과금 판정 · Gemini 어댑터(사고 서명 왕복) · 본체 직접 provider 전면 퇴역(P11). 개정 이력은 부록 B.
>
> **상태**: **TODO(계획 · 코드 0)** — 게이트 사용자 확정 대기(§3 — 미결 G-1 · G-5 · G-6 · G-13 · G-14 · G-15 외 권고안)
> **2026-10-08 — `plans/148`과 별개 · 148 성공 시 미사용 예정**(사용자 지시 *"148번 계획은 128번 계획과 다른 계획이다. 이게 성공되면 128번은 사용하지 않을 계획이다."*) — 148(FabriX 도구 호출 프록시 `fabrix_proxy/`) 1단계 PoC 판정 전까지 이 계획은 착수하지 않고 그대로 둔다. 판정 통과 시 상태를 「미사용」으로 바꾼다.
> **2026-10-06 — D-303 회귀 정책 반영**: P0-8 테스트 기준선을 전체 집계 → 실패분만 재대조(전체는 사용자 요청 시)로 바꿨다.
> **요청 원문(v1 · 2026-09-30)**: *"fabrix 호출 부분을 별도의 agent를 통해 패키지를 분리하려고 한다. 설정에 따라 fabrix를 호출하거나 openai api를 호출하는 방식이 이 별도패키지에서 동작되어야 한다. 기존 기능들은 신규로 생성한 패키지를 api형식으로 호출한다. 관련 코드 분리하여 별도의 서버에서 동작할 수 있는 패키지 를 분리할 계획을 파일로 작성하라."*
> **요청 원문(v2 · 2026-09-30)**: *"LLM gateway는 fabrix, gemini, vllm등을 모두 처리하도록 계획이 작성되어 있냐?"* → (v1 범위 보고 뒤) *"모든 llm을 llm gateway에서 처리하도록 계획을 수정하라."*
>
> **해석(추정 — 원문 기준)**
> - "별도의 agent" = 본체 밖 **별도 프로세스·별도 서버에서 도는 LLM 호출 대행 서비스**(이하 "게이트웨이"). 구현 수행 주체(서브에이전트)를 뜻했다면 §4의 Phase가 그대로 병렬 작업 단위다(§4.12).
> - "모든 llm" = 제품 런타임이 **원격 LLM API**를 부르는 모든 경로다.
>   - 해당: 본체 워커 · 본체 오케스트레이터 · in-process noise_gate · sre_agent 조사 LLM.
>   - 해당 없음(LLM API 호출이 아니다): in-process 로컬 임베딩(E5 · 알람 임베딩) · FabriX 리트리벌(RAG — 검색 호출 · G-5) · `agents/`(Claude Agent SDK 빌드 도구 — 제품 런타임이 아니다).
> - "설정에 따라 fabrix 또는 openai" = 게이트웨이 설정의 **별칭 → 백엔드 매핑**만 바꿔서 백엔드를 전환한다(본체·sre_agent 코드는 바꾸지 않는다).
>
> **실측 기준**: 브랜치 `multiintent` HEAD `b5de407` + 미커밋 작업 트리(2026-09-30). 코드는 **읽기만** 했다(수정 0 · LLM 호출 0 · 서버 기동 0 · 과금 0).
> - 루트 `.venv`: langchain-openai 1.3.2 · openai 2.26.0 · langchain-core 1.6.1 · langchain-google-genai 4.2.5 · google-genai 1.68.0 · deepagents 0.6.10 · httpx 0.28.1 · fastapi 0.141.1 · instructor 1.15.4
> - `sre_agent/.venv`: holmesgpt 0.36.0 · litellm 1.89.0 · openai 2.48.0 · google-genai 2.14.0
>
> **근거 표기**: `파일:라인` = 현 작업 트리에서 직접 확인한 코드 · **추정** = 검증하지 않은 추론 · **실측 필요** = §4 P0에서 확인할 항목 · **승인 실측** = 과금 경로라 건별 사용자 승인이 필요한 실측(D-127).
> **관련 결정**: D-139(최상위 패키지 경계) · **D-274**(독립 게이트웨이 패키지 전례 `apm_gateway`) · D-118(경계 불변식 테스트) · **D-174**(워커=FabriX · 제어 평면=vLLM) · D-037·D-042(오케스트레이터 평면 · Qwen no-think) · **D-194**(FabriX `llmConfig` 2단 프로파일) · **D-198**(FabriX 총 소요 상한) · **D-155**(PII 차단 진단) · D-169·D-222(구조화 출력 모드 — 클래스명 판정) · D-009(`USER_RESPONSE_TAG` 스트리밍) · D-060(오케스트레이터 SSL 검증 해제) · D-268(마감 ContextVar 전파 · SDK 재시도 0) · **D-127·D-222 ②③·D-240**(과금 판정 · 외부 차단) · D-120·D-213·D-229·D-230(sre_agent 조사 LLM · 토큰 예산 · 실 조사 게이트) · D-225·D-251(사다리 · `orchestrator_unavailable`) · D-125(정적 Bearer) · D-181(공유 venv) · D-161(경로 폐기 실측 4항) · D-255(매뉴얼 동반)
> **D-번호**: 이 계획서는 예약하지 않는다. 게이트가 확정되면 등재 직전 3곳(`## D-` 헤더 · 「변경 이력」 · 「채번 이력」/안내 라인)을 grep해 부여한다.
> **`docs/02_decision.md` 충돌 검토**: 정면으로 충돌하는 결정은 없다. 게이트 확정 때 같은 D-번호 안에서 부기해야 할 것:
> - D-174 — 평면 정책(워커=FabriX · 제어=vLLM)은 그대로이고 **호출 위치만** 게이트웨이로 바뀐다.
> - D-037·D-042·D-060 — 오케스트레이터 생성 인자(Qwen `enable_thinking` · SSL 검증 해제)의 소재가 게이트웨이 백엔드 설정으로 옮겨진다.
> - D-194 — 프로파일 설정 소재 이동 · D-155 — PII 진단 덤프 위치 이동.
> - D-222 ②③ — 과금 판정 단위가 provider에서 **게이트웨이 별칭의 billing**으로 바뀐다(fail-closed 방향 유지).
> - D-120·D-229·D-230 — sre_agent 조사 LLM이 게이트웨이 별칭으로 바뀐다(설정만).
> - D-225·D-251 — 사다리 `orchestrator_unavailable` 판정 근거가 게이트웨이 별칭 가용성으로 바뀐다.

---

## 0. 요약

### 0.1 결론

- 최상위 패키지 **`llm_gateway/`** 를 새로 만든다. `apm_gateway` 전례를 따른다 — 자체 `pyproject.toml` · 자체 cwd · 루트 venv 공유 · 2단 중첩 · 독립 프로세스.
- 게이트웨이는 **OpenAI Chat Completions 호환 HTTP API**(`/v1/chat/completions` · SSE · tool-calling · `/v1/models` · `/health`)를 노출한다.
  - 호출자는 **별칭**(`worker` · `worker-answer` · `orchestrator` · `investigation`)만 안다.
  - 게이트웨이 설정 `routes.yaml`이 별칭을 **백엔드**에 매핑한다.
- 백엔드 종류는 셋이다.
  - `fabrix` — KBGenAI REST.
  - `openai` — OpenAI 호환 투명 전달. vLLM · MLX · OpenAI · Ollama `/v1` · FabriX OpenAI 호환 모드를 이 하나로 처리한다.
  - `gemini` — G-6.
  - "설정에 따라 fabrix 또는 openai"는 `routes.worker.backend` 한 줄 교체다.
- 소비자 3곳은 코드 대신 **설정으로** 게이트웨이를 가리킨다.

  | 소비자 | 현행 | 전환 |
  |---|---|---|
  | 본체 워커 평면(+ in-process noise_gate) | `create_llm()` → FabriX·Gemini·Ollama·MLX 직접 | `LLM_PROVIDER=gateway` → `GatewayChatOpenAI(model="worker")` |
  | 본체 오케스트레이터 평면(deep agents · noise_gate 트랙 B) | `create_orchestrator_llm()` → vLLM·Gemini·MLX 직접 | `ORCHESTRATOR_PROVIDER=gateway` → `GatewayChatOpenAI(model="orchestrator")`(네이티브 tool-calling) |
  | sre_agent(HolmesGPT · litellm) | `MODEL`·`API_BASE`로 vLLM·Gemini 직접 | `MODEL=openai/investigation` · `API_BASE=<게이트웨이>/v1` · `API_KEY=<게이트웨이 토큰>` — **코드 변경 0**(G-14) |

- 분기가 팩토리 두 곳(`create_llm` · `create_orchestrator_llm`)에 모여 있어 **호출처(src 약 25개 · noise_gate 4개 파일)는 바꾸지 않는다.**
- 백엔드 고유 로직은 전부 게이트웨이로 간다.
  - FabriX: 페이로드 · 스트림 파싱 · junk · 총상한 · `llmConfig` · PII 응답 진단.
  - vLLM: Qwen `enable_thinking` · SSL 해제.
  - MLX: `max_tokens` · 절단 경고.
  - Gemini: 메시지·도구 변환 · 사고 서명.
  - 본체에 남는 백엔드 흔적은 KBGenAI 메시지 규약 표지(§2.5)와 응답 본문 기반 PII 판정(§2.7)뿐이다.
- **수용 기준의 핵심은 업스트림 요청 동등성이다.**
  - FabriX로 나가는 바디 = 현행 골든(키 순서까지 동치).
  - vLLM으로 나가는 바디 = 현행 `ChatOpenAI` 골든(JSON 동치 · 차이는 목록화해 무해 판정).
- 전환은 **설정 opt-in**이다. 기본 provider는 그대로라서 게이트웨이를 켜기 전까지 본체 동작은 비트 동일하고, 롤백은 설정 되돌리기다.
  **최종 상태 = 본체·sre_agent에 LLM 직접 클라이언트 0**이며, 폐쇄망 검증 뒤 별도 결정(D-161 4항 실측)으로 도달한다(P11 · G-7).

### 0.2 목표 구조

```
 본체 프로세스 (src/ + in-process noise_gate)                llm_gateway 서버 (별도 호스트 가능)
 ┌───────────────────────────────────────────┐            ┌─────────────────────────────────────────────────┐
 │ 워커:  create_llm(purpose)                  │            │ interface/  FastAPI                              │
 │   └ GatewayChatOpenAI(model=worker|        │  HTTP      │   POST /v1/chat/completions (JSON·SSE·tools)     │
 │                        worker-answer) ─────┼─Bearer───▶ │   GET  /v1/models[/{alias}] · /health[?deep=1]   │
 │ 오케스트레이터: create_orchestrator_llm()     │            │ application/                                     │
 │   └ GatewayChatOpenAI(model=orchestrator) ─┼──────────▶ │   routes.yaml: 별칭 → 백엔드 + 파라미터          │
 │      (deep agents · noise_gate 트랙 B)       │            │   마감 · 감사 · 송신 정책 · billing · 가용성     │
 └───────────────────────────────────────────┘            │ adapters/                                        │
 ┌───────────────────────────────────────────┐            │   fabrix/  ─────────────▶ FabriX KBGenAI         │
 │ sre_agent 프로세스 (HolmesGPT · litellm)      │            │   openai/  ─────────────▶ vLLM · MLX · OpenAI ·  │
 │   MODEL=openai/investigation ──────────────┼──────────▶ │                           Ollama /v1 · FabriX 호환│
 │   API_BASE=http://<gw>:9095/v1              │            │   gemini/  ─────────────▶ Gemini API (G-6)       │
 └───────────────────────────────────────────┘            └─────────────────────────────────────────────────┘
```

### 0.3 범위

| 구분 | 항목 |
|---|---|
| **포함** | 워커 평면(비스트림·스트림 · 목적 프로파일) · 오케스트레이터 평면(tool-calling · 스트림) · sre_agent 조사 LLM · 백엔드 FabriX KBGenAI / vLLM / MLX / Gemini / Ollama / FabriX OpenAI 호환 · 마감 전파 · PII 응답 진단 이전 · 인증 · 송신 정책 · 별칭별 과금 판정 · 별칭 가용성(사다리 · noise_gate 트랙 선택) · 사전 점검(preflight) · 설정 화면·도움말·매뉴얼 · 경계·계약·동등성 테스트 · 본체 직접 provider 퇴역 계획(P11) |
| **게이트로 결정** | FabriX 리트리벌(RAG) 편입(G-5) · Gemini 어댑터 방식(G-6) · Anthropic 백엔드 필요 여부(G-15) |
| **비범위** | in-process 로컬 임베딩(E5 · `noise_gate` 알람 임베딩 — 원격 API 아님) · `agents/`(빌드 도구) · 프롬프트·노드 로직 변경 · 응답 캐시·레이트 리밋·부하 분산·자동 장애 조치 |

---

## 1. 현행 실측 — LLM 호출 전수

### 1.0 전수 표

| 소비자 · 평면 | 진입 | provider → 클라이언트 | 파일 |
|---|---|---|---|
| 본체 워커 | `create_llm(config, provider_override, purpose)` `src/llm.py:156` | `fabrix` → `KBGenAIChat` / `FabriXAPIClient` (`:434`) · `gemini` → `ChatGoogleGenerativeAI` (`:386`) · `ollama` → `LLMAPIClient`(`/api/chat` · `:327`) · `mlx` → `MLXChatOpenAI` (`:345`) | `src/clients/*` |
| 본체 오케스트레이터 | `create_orchestrator_llm(config)` `src/llm.py:195` | `vllm` → `ChatOpenAI`(`extra_body` Qwen 가드 · `verify_ssl` · `max_retries=0` · `:219`) · `mlx` → `MLXChatOpenAI` · `gemini` → `ChatGoogleGenerativeAI` (`:300`) | 소비: `src/orchestration/deep_agent.py:123` · `noise_gate/infrastructure/noise_signal_tools.py:246` |
| in-process noise_gate | `src.llm` 두 팩토리 | 위와 같음 | `alarm_analyzer` · `annotation_classifier` · `agentic_enricher`(트랙 A 워커 · 트랙 B 오케스트레이터) |
| sre_agent 조사 LLM | holmes `Config(model, api_key, api_base)` `sre_agent/sre_agent/diagnosis.py:169-173` | litellm이 `MODEL` 접두(`gemini/` · `openai/` · `anthropic/` …)로 분기. 코드 기본 `anthropic/claude-sonnet-5`(`settings.py:20`) · 개발 `gemini/gemini-3.5-flash`(`:50` · D-120) · 사내 vLLM은 `API_BASE`(`:71`) | 운영 실제값은 **실측 필요**(P0-4) |
| 가용성 판정 | vLLM `/models` GET 2벌 | `deep_agent.vllm_healthy:26` · `orchestrator_available:54`(gemini는 키 유무) · `noise_signal_tools.vllm_healthy:56`(복제) · `agentic_enricher._select_backend:149` | 사다리 1단 확정(`select_orchestration_backend:75` → `src/observability/ladder.py:62` `orchestrator_unavailable`) |
| 사전 점검 | MLX 직접 | `scripts/scenario/preflight.py:199`(`/models`) · `:217-228`(1토큰 생성) · `mlx_run_blockers:244` · 과금 판정 `external_planes:49` | 시나리오 · 벤치 |

### 1.1 워커 평면 FabriX 클라이언트 (v1 실측 유지)

| 파일 | 줄 | 담당 | 처분 |
|---|---:|---|---|
| `fabrix_kbgenai.py` | 382 | 페이로드 `_get_payload:104` · 비스트림 `_agenerate:156`(총상한) · 스트림 `_astream:284`(`data:` 파싱 · `null` 라인 · `STATUS/SYNC/FINISH` · 벽시계 총상한) · junk 제거 · PII 응답 훅 · `verify=False` | 게이트웨이 `adapters/fabrix/` |
| `fabrix_client.py` | 273 | OpenAI 호환 폴백 · few-shot 도구 모사 | 게이트웨이 `openai` 백엔드로 대체(G-9) |
| `fabrix_retrieval.py` | 360 | Retrieval Connector(RAG) | G-5 |

### 1.2 KBGenAI 메시지 규약 — "system 다음 빈 AIMessage"

- **단일 출처 `is_kbgenai`** (`src/utils/llm_compat.py:16` — 클래스명 비교):
  `result_organizer:599,752` · `query_generator:922,1272` · `input_parser:378,480` · `multi_db_executor:1704,1738` · `semantic_compiler:904` · `column_deriver:289,377` · `candidate_selector`·`candidate_generator`(bool 인자) · `instructor_adapter:262`
- **직접 클래스 비교 12곳(7파일)**:
  `nodes/output_generator:417` · `document/field_mapper:1110,1544,1852,1926` · `orchestration/result_aggregator:1487` · `orchestration/replanner:389` · `orchestration/intent_planner:873` · `routing/semantic_router:881,1171,1251` · `routing/intent_confirm:50`
  (+ scripts `eval_routing.py:1073` · `pii_probe.py:81`)
- **삽입하지 않는 경로 4곳**: `general_inference:288`(멀티턴) · `doc_qa/service:390` · `schema_cache/description_generator:112,182` · `cache_management:208`(Human만)
  → 게이트웨이가 일괄 삽입하면 이 4곳의 FabriX 페이로드가 바뀐다(§2.5 근거).

### 1.3 PII 필터 (D-155)

- **응답측 진단**(클라이언트 내부 · `src/security/pii_filter.py:528,686,709`)은 FabriX 원 응답을 봐야 하므로 **게이트웨이로 옮긴다.**
- **본문측 판정**(노드 · `query_generator:936` · `multi_db_executor:1764,2779` · `query_validator:86`) · `diagnose_blocked_prompt` · `scrub_pii`는 **본체에 남는다.** 게이트웨이는 차단 안내문 content를 가공 없이 통과시킨다.
- 비스트림 `status != SUCCESS`는 `ValueError("API returned error status: <STATUS>")`(`fabrix_kbgenai.py:196`)로 올라온다. 이 메시지를 유지한다.

### 1.4 마감·타임아웃

- 요청 마감은 ContextVar(`src/utils/deadline.py:36`)로 묶고, 클라이언트가 `call_timeout(total_timeout)`(`:143`)으로 호출별 상한을 잡는다(D-268). `astream_text`(`src/llm.py:65`)는 마감이 있으면 `asyncio.timeout`으로 감싼다.
- `TimeoutError`를 분기하는 모듈이 13개다(src 7 · noise_gate 6) → 게이트웨이를 거쳐도 시간 상한은 **`TimeoutError`로** 올라와야 한다.
- 오케스트레이터는 `timeout=ORCHESTRATOR_TIMEOUT`(120) · `max_retries=0` · MLX면 `stream_chunk_timeout`(D-222 결함 A — langchain-openai ≥1.2 기본 120초)이다.

### 1.5 과금·외부 송신 판정

- 판정 단일 정의는 `scripts/scenario/preflight.py:49` `external_planes`다. 평면별 집합: 워커 `{fabrix, ollama, mlx}` · 오케스트레이터 `{vllm, mlx}`(`:45-46`). 모르는 값은 외부로 본다.
- pytest 외부 차단 가드(`tests/conftest.py:94`)는 **pytest 프로세스만** 막는다. 게이트웨이 프로세스의 업스트림 호출은 가드 밖이다(§2.9).
- sre_agent는 자체 실 조사 게이트(`investigation_llm_stub_reason` — `INVESTIGATION_LLM_ENABLED` tri-state · D-230)를 갖는다.

### 1.6 도구 바인딩

- 오케스트레이터: deepagents(`create_deep_agent`) · noise_gate 트랙 B(`noise_signal_tools.py:247` `llm.bind_tools`)가 **네이티브 tool-calling**을 쓴다.
- 워커: `column_deriver:274`(옵트인)가 `bind_tools`를 건다. FabriX는 도구 자리가 없어 무효이고, 게이트웨이 fabrix 어댑터도 버려야 현행과 같다.
- sre_agent: holmes `ToolCallingLLM`이 **매 호출에 `tools`/`tool_choice`를 싣고 폴백이 없다**(`settings.py:66-69` 주석).
- instructor TOOLS 모드는 클래스명 `ChatOpenAI`/`AzureChatOpenAI`만 해당한다(`instructor_adapter.py:41`). 서브클래스는 MD_JSON이다(D-222 실측).

### 1.7 설정·화면 표면

- `LLMConfig`(`src/config.py:21`) — provider 4종 + 각 provider 필드 · `OrchestratorConfig`(`:109`) — `provider` · `base_url` · `model` · `api_key` · `timeout` · `health_timeout` · `verify_ssl` · `enable_thinking` + deep agents 동작값(`recursion_limit` · `max_tool_result_tokens`).
- `worker_provider_override`(`src/config.py:1559` — 테스트 전용 · `graph.py:471`).
- 관리자 설정 화면: 리로드 대상 `LLM_*` · `ORCHESTRATOR_*`(`src/api/settings_catalog.py:295-320`) · 시크릿(`:230`) · 도움말 `config/settings_help/llm.yaml` · `orchestrator.yaml` · `general.yaml:444` · 관리자 매뉴얼 `scripts/manual/content/admin.md:3358`.
- 문서: `docs/21_orchestration_ladder.md`(오케스트레이터 가용성) · `docs/03_setup_guide.md` §7.2(MLX).

---

## 2. 설계

### 2.1 API 계약 — OpenAI Chat Completions 부분집합

| 메서드·경로 | 용도 | 비고 |
|---|---|---|
| `POST /v1/chat/completions` | 채팅 호출. `stream:true`면 SSE(`chat.completion.chunk` … `[DONE]`) | 필드: `model`(별칭) · `messages`(system/user/assistant(+`tool_calls`)/tool) · `stream` · `tools` · `tool_choice` · `parallel_tool_calls` · `max_tokens`/`max_completion_tokens` · `temperature` 등. **`openai` 백엔드는 모르는 필드도 그대로 전달**, `fabrix`·`gemini`는 아는 필드만 쓴다 |
| `GET /v1/models` | 별칭 전체 + 메타 | 메타: `backend_kind`(`fabrix`/`openai`/`gemini`) · `billing`(`internal`/`external`) · `capabilities`(`tools` · `stream`) · `upstream_host` |
| `GET /v1/models/{alias}` | 별칭 1개 + **가용성** `ready` | 싼 판정(§2.11). 사다리·noise_gate 트랙 선택이 읽는다 |
| `GET /health` | 프로세스 생존(업스트림 호출 0) | `?deep=1&alias=<별칭>`이면 1토큰 생성(D-222 결함 B 교훈) |

- **요청 헤더**: `Authorization: Bearer <LLM_GATEWAY_TOKEN>`(D-125 전례) · `X-Gateway-Timeout: <초>`(이번 호출 상한) · `X-Request-Id`(선택).
- **응답 헤더**: `X-Gateway-Backend` · `X-Gateway-Tools-Dropped: 1`(fabrix가 tools를 버렸을 때).
- **오류 봉투**: OpenAI 형식 `{"error": {"type", "code", "message"}}`.

| 상황 | HTTP | `code` | 본체 `GatewayChatOpenAI`가 올리는 예외 |
|---|---:|---|---|
| 토큰 없음·불일치 | 401 | `unauthorized` | 설정 오류(명시 메시지) |
| 송신 정책 위반 | 403 | `egress_denied` | 설정 오류 |
| 모르는 별칭 · 능력 없는 별칭에 tools 요구 · 형식 오류 | 400 | `invalid_request` | `ValueError` |
| FabriX `status != SUCCESS` | 502 | `upstream_status` · message = **종전 문자열** `API returned error status: <STATUS>` | `ValueError`(종전 메시지) |
| 업스트림 HTTP 오류(vLLM 400 포함) | 502 | `upstream_http` · 업스트림 상태 · 본문 앞부분 | `ValueError`(업스트림 메시지 포함 — sre_agent D-213 `System message must be at the beginning.` 같은 진단 문자열 보존) |
| 총 소요 상한·요청 마감 초과 | 504 | `timeout` | **`TimeoutError`** |

- sre_agent(litellm)는 OpenAI 형식 오류를 litellm 예외로 받는다. 종전 vLLM 직결과 같은 계열(`BadRequestError` 등)이 되도록 **업스트림 4xx는 가능한 한 같은 상태 코드로 되돌린다**(502로 뭉개지 않는다)는 규칙을 `openai` 백엔드에 적용한다. P8에서 holmes 동작 동등성으로 확인한다.

### 2.2 별칭과 라우팅 설정

- **별칭**(호출자가 아는 유일한 이름):

  | 별칭 | 호출자 | 요구 능력 |
  |---|---|---|
  | `worker` | 본체 워커(deterministic) · noise_gate 트랙 A·분석·주석 분류 | 텍스트 · 스트림 |
  | `worker-answer` | 본체 최종 응답 합성(D-194 answer — `USER_RESPONSE_TAG` 3곳) | 텍스트 · 스트림 |
  | `orchestrator` | deep agents · noise_gate 트랙 B | **tools** · 스트림 |
  | `investigation` | sre_agent HolmesGPT | **tools** |

- **라우팅 설정** `llm_gateway/config/routes.yaml`(비밀 없음 · git 추적). 비밀·접속 정보는 `.env`/`.encenv`의 환경 변수 이름으로만 참조한다.

  ```yaml
  backends:
    fabrix:
      kind: fabrix
      base_url_env: FABRIX_BASE_URL
      api_key_env: FABRIX_API_KEY
      client_key_env: FABRIX_CLIENT_KEY
      model_env: FABRIX_CHAT_MODEL
      verify_ssl: false
      total_timeout: 300
      profiles:
        deterministic_env: FABRIX_LLM_CONFIG
        answer_env: FABRIX_ANSWER_LLM_CONFIG
    vllm:
      kind: openai
      base_url_env: VLLM_BASE_URL
      api_key_env: VLLM_API_KEY
      model: Qwen3.5-9B
      verify_ssl: false
      timeout: 120
      params:
        temperature: 0.0
        extra_body: {chat_template_kwargs: {enable_thinking: false}}
    mlx:
      kind: openai
      base_url: http://127.0.0.1:8080/v1
      model: default_model
      timeout: 600
      params:
        temperature: 0.0
        max_tokens: 4096
        extra_body: {chat_template_kwargs: {enable_thinking: false}}
      warn_on_length: true
    gemini:
      kind: gemini
      api_key_env: GEMINI_API_KEY
      model: gemini-3.5-flash
      params: {temperature: 0.0}
  routes:
    worker:        {backend: fabrix, profile: deterministic}
    worker-answer: {backend: fabrix, profile: answer}
    orchestrator:  {backend: vllm}
    investigation: {backend: vllm}
  ```

- **기동 시 검증**(조용히 떠 있지 않게 실패시킨다):
  - ① 별칭의 요구 능력을 백엔드가 못 주면 거부한다(예: `orchestrator: {backend: fabrix}` → tools 불가).
  - ② 참조한 환경 변수가 비었으면 거부한다.
  - ③ 송신 정책(§2.9)을 위반하면 거부한다.
  - ④ 프로파일 JSON 문자열이 유효하지 않으면 거부한다(D-194 규칙 이식).
- 요청 원문의 "설정에 따라 fabrix 또는 openai"는 `routes.worker.backend: fabrix` ↔ `vllm`(또는 다른 `openai` 백엔드) 교체다. 본체 코드는 바꾸지 않는다.
- 파라미터 우선순위: **라우트/백엔드 `params`가 기본값**이고, 호출자가 보낸 값은 `openai` 백엔드에서만 덮어쓴다(예: 요약 `max_tokens` — `src/llm.py:39` `output_token_limit_kwargs`). `fabrix`는 `llmConfig` 프로파일만 쓴다(v1 규칙).

### 2.3 백엔드 어댑터 동작

| 항목 | `fabrix` (KBGenAI) | `openai` (vLLM · MLX · OpenAI · Ollama `/v1` · FabriX 호환) | `gemini` (G-6) |
|---|---|---|---|
| 메시지 | 현행 `_convert_messages_to_prompts`·`_get_payload`와 같은 규칙(첫 system → `systemPrompt` · 모든 system 제외 · user/assistant content 순서대로 · 빈 assistant 포함) | **투명 전달**. 단 도구 호출 없는 빈 assistant 턴은 제거(§2.5) | 변환(system → system instruction · user/assistant → user/model · tool_calls ↔ function call · tool → function response). 빈 assistant 턴 제거 |
| `tools`/`tool_choice` | **버림** + 응답 헤더 표지(워커 전용 — 요구 능력 검증으로 오케스트레이터 매핑 불가) | 그대로 | 변환(function declarations) |
| 응답 `tool_calls` | 없음 | 그대로(스트림 `delta.tool_calls` 조각 포함) | 변환 + **사고 서명 왕복**(G-6) |
| `stream` | `isStream` · 비스트림 `strip()` · 스트림 청크 strip 없음 | 업스트림 SSE를 **라인 단위로 중계**(`model` 필드만 별칭으로 되돌림) | 스트림 → OpenAI chunk 변환 · 사고(thinking) 파트는 content에 싣지 않음 |
| `max_tokens` | 버림(llmConfig 규약 미확인) | 호출자 값 > 백엔드 `params` > 미전송 | `max_output_tokens`로 변환(호출자 값만 · v1 `src/llm.py:53` 주석의 사고 토큰 예산 문제는 승인 실측 후) |
| 백엔드 고유 처리 | junk 토큰 제거 · PII 진단(§2.7) | `extra_body` 병합 · `warn_on_length`(`finish_reason=length` WARNING — MLX 종전 동작 이전 · D-222 부기) | — |
| 총 소요 상한 | `min(total_timeout, X-Gateway-Timeout)` 벽시계(D-198 F1) | 같은 규칙 + httpx read 상한 | 같은 규칙 |
| TLS | `verify_ssl` 기본 false(현행 `verify=False`) | 백엔드별 `verify_ssl`(vLLM 폐쇄망 false — D-060) | SDK 기본 |
| 연결 | 호출마다 새 `AsyncClient`(현행 동일) | 백엔드별 공유 클라이언트 | SDK 클라이언트 |
| 재시도 | 없음 | 없음(`max_retries=0` 등가 — D-268 부기) | SDK 재시도 끔 |

- **Ollama**: `/api/chat` 전용 클라이언트(`LLMAPIClient`)를 옮기지 않고 Ollama의 OpenAI 호환 `/v1`을 `openai` 백엔드로 붙인다(G-13 — 호환 범위는 **실측 필요**). 개발 전용이라 페이로드 동일성이 아니라 동작 확인으로 수용한다.
- **FabriX OpenAI 호환 모드**(`FabriXAPIClient` · `client_key` 없을 때): `openai` 백엔드로 대체한다. few-shot 도구 모사는 폐기한다(G-9).

### 2.4 tool-calling 통과 계약 (오케스트레이터 · sre_agent)

- `openai` 백엔드는 **요청·응답을 변형하지 않는다.** 바꾸는 것은 `model`(별칭 → 업스트림 모델) · 백엔드 `params` 병합 · 빈 assistant 턴 제거뿐이다.
  deepagents·holmes가 보내는 `tools` · `tool_choice` · `parallel_tool_calls` · assistant `tool_calls` · `tool` 메시지(`tool_call_id`)가 그대로 vLLM에 닿는다.
- SSE는 업스트림 라인을 그대로 중계한다(`delta.tool_calls[i].function.arguments` 조각 포함). 게이트웨이가 조각을 재조립하지 않으므로 조립 규칙이 바뀌지 않는다.
- 본체 쪽 `GatewayChatOpenAI`는 `ChatOpenAI` 서브클래스이므로 `bind_tools`·스트리밍 tool_call 조립이 **현행 vLLM 경로와 같은 코드**다.
- **요구 능력 검증**: `tools`가 실린 요청이 `tools` 능력 없는 별칭으로 오면 400이다(fabrix 워커 별칭은 예외 — `column_deriver` 현행 호환을 위해 버리고 표지만 단다).

### 2.5 KBGenAI 메시지 규약의 위치 (G-4 권고안 · v1 보강)

- **본체 워커가 지금처럼 빈 assistant 턴을 삽입**하고, 게이트웨이 `fabrix` 어댑터는 받은 그대로 옮긴다. `openai`·`gemini` 어댑터는 도구 호출 없는 빈 assistant 턴을 제거한다.
  → 워커를 vLLM·MLX·Gemini로 매핑해도 종전(삽입 안 함)과 같은 입력이 된다.
- 판정은 클래스명이 아니라 **인스턴스 표지**로 한다(v2 변경 — 워커와 오케스트레이터가 같은 클래스라서).
  - `GatewayChatOpenAI.kbgenai_message_order: bool` — 워커 평면 인스턴스만 `True`.
  - `is_kbgenai(llm)` = `type(llm).__name__ == "KBGenAIChat"` **또는** `getattr(llm, "kbgenai_message_order", None) is True`.
  - `is True` 비교는 테스트의 `MagicMock` LLM이 참으로 판정되는 것을 막는다.
- 직접 클래스 비교 12곳을 `is_kbgenai(llm)`으로 치환한다(Plan 69 P2 단일 출처화 완결).
- 일괄 정규화를 택하지 않는 근거는 v1과 같다 — 삽입하지 않는 4경로(§1.2)의 FabriX 페이로드가 바뀐다.

### 2.6 마감·타임아웃 전파 — 3중 상한

1. **호출자 벽시계**: `GatewayChatOpenAI._agenerate`/`_astream`이 호출 시작에 `call_timeout(total_timeout)`을 1회 계산해 상한을 건다 → `TimeoutError`. `astream_text`의 `asyncio.timeout`은 그대로 둔다.
   평면별 `total_timeout`: 워커 = `LLM_GATEWAY_TOTAL_TIMEOUT`(기본 300 = FabriX 총상한) · 오케스트레이터 = `ORCHESTRATOR_TIMEOUT`(기본 120 · 현행 의미 유지).
2. **헤더 전파**: 같은 값을 `X-Gateway-Timeout`으로 보낸다. 게이트웨이는 업스트림 상한을 `min(백엔드 총상한, 헤더 값)`으로 건다 → 게이트웨이 쪽 유령 호출의 수명이 유한하다(D-198 F1의 홉 연장).
   sre_agent(litellm)는 헤더를 보내지 않으므로 백엔드 총상한만 적용되고, 조사 전체 상한(`investigation_timeout_seconds` 300)은 sre_agent가 종전대로 건다.
3. **연결 끊김 취소**: 스트림은 연결이 닫히면 생성기가 취소되고 업스트림도 닫힌다. 비스트림은 2번 상한이 수명을 보장한다(필요하면 `is_disconnected` 감시 — 측정 후).

- `openai` SDK 설정: `max_retries=0` · `timeout` = read 상한 · `stream_chunk_timeout` = 총상한(`src/clients/mlx_client.py:39` `stream_chunk_timeout_kwargs` 재사용 — D-222 결함 A).
- 헤더 싣는 방법은 P0-3에서 고른다: ⓐ 호출 kwargs `extra_headers` ⓑ httpx `event_hooks` + ContextVar.

### 2.7 PII 진단의 이전 (v1 유지)

- 게이트웨이 `adapters/fabrix/pii.py`에 응답측 진단(`is_filter_blocked` · `log_filter_block_if_any` · `scan_pii` · `dump_blocked_payload`)과 규칙표를 **복제**한다(경계상 import 불가 — D-274 복제 전례). 덤프는 게이트웨이 호스트 `logs/pii_block/`에 남는다.
- 루트 `tests/`에서 두 규칙표의 **동등성**을 단언한다. 원천은 `docs/pii_filtering_rules.md` 하나다.
- 본체 잔류: `scrub_pii` · `is_filter_blocked(raw_text=…)` · `diagnose_blocked_prompt`.

### 2.8 보안

- **인증**: 정적 Bearer(`LLM_GATEWAY_TOKEN`) · 상수 시간 비교. 토큰이 없으면 기동을 거부한다(G-11). 호출자는 본체·sre_agent 둘이다.
  1차는 토큰 하나를 공유하고, 호출자별 토큰(별칭 접근 제한 — 예: sre_agent는 `investigation`만)은 G-12에서 정한다.
- **바인딩**: 기본 `127.0.0.1`. 별도 서버면 `LLM_GATEWAY_HOST`로 명시한다. TLS는 사내 리버스 프록시 또는 uvicorn TLS.
- **로그**: 프롬프트·응답 본문을 싣지 않는다(PII 덤프 제외). 감사 1줄 = 시각 · request id · 호출자(토큰 식별자) · 별칭 · 백엔드 · stream · tools 개수 · 상태 · 소요 · 입출력 문자 수.
- **자격증명 격리**: FabriX · vLLM · Gemini · OpenAI 키는 **게이트웨이에만** 둔다. 본체·sre_agent의 `.encenv`에서 LLM 키를 지우는 것은 P11.

### 2.9 과금·외부 송신 판정 — 별칭 단위

- **게이트웨이 송신 정책**: `openai`·`gemini` 백엔드의 업스트림 호스트가 루프백·사설 대역이 아니면 `LLM_GATEWAY_ALLOW_EXTERNAL=true` 없이는 **기동 거부**. Gemini는 항상 공인이므로 이 허용이 필수다.
- **billing 메타**: 별칭마다 `/v1/models`에 싣는다 — fabrix → `internal` · openai + 사설/루프백 → `internal` · 그 밖(공인 OpenAI · Gemini) → `external`.
- **본체 판정(`external_planes` 개정)**: 평면 provider가 `gateway`면 **해당 별칭들의 billing**을 조회해 판정한다.
  - 워커 = `worker` · `worker-answer` / 오케스트레이터 = `orchestrator`.
  - 하나라도 `external`이거나 조회에 실패하면 외부다(fail-closed · D-222 ③ 방향 유지).
  - 소비처 3곳(시나리오 기본 · `--run` · 벤치 `approval_policy`)은 판정 함수 하나를 계속 공유한다.
- **sre_agent**: 게이트웨이 경유 시 `INVESTIGATION_LLM_ENABLED=true`(키 추론 경로 대신 명시 · D-230)로 둔다. 실 조사 e2e는 `investigation` 별칭이 `internal`일 때만 무승인이고, `external`이면 D-127 건별 승인이다.
- **pytest**: `RUN_LOCAL_LLM=1` 실 LLM 테스트가 게이트웨이를 쓰면 픽스처가 먼저 필요한 별칭의 `billing=internal`을 확인하고, 아니면 skip한다. `RUN_E2E=1`(승인 후)은 게이트웨이의 `ALLOW_EXTERNAL`도 함께 켜야 외부가 열린다 — **두 곳 모두 켜야 외부 호출**이 된다.
- **D-127 "키 존재만으로 실행 금지"**: 키가 게이트웨이에 있어도, 별칭이 Gemini로 매핑되고 `ALLOW_EXTERNAL=true`로 명시돼야 호출이 나간다. 게이트웨이 기동 로그 1줄에 `external` 별칭 목록을 남긴다.

### 2.10 Gemini 백엔드 (G-6)

- 현행 Gemini 경로는 `ChatGoogleGenerativeAI`(langchain-google-genai 4.2.5)이며, 개발 `.env`는 두 평면 모두 Gemini다(CLAUDE.md 운영 실측 행). 운영 폐쇄망은 egress가 없어 쓰지 않는다 — **테스트·PoC 전용**.
- **핵심 난점 — 사고 서명(thought signature) 왕복(추정 · 승인 실측 필요)**: Gemini 3 계열 사고 모델은 다중 턴 function calling에서 직전 응답의 서명을 다음 요청에 되돌려 받기를 요구한다.
  본체 `ChatOpenAI`·holmes(litellm)는 OpenAI 규약에 없는 필드를 다음 요청에 싣지 않는다. 그래서 게이트웨이가 서명을 **OpenAI 규약 안에서 살아남는 자리**에 실어야 한다.
  - 권고 안: 서명을 `tool_calls[].id`에 부호화한다. id는 클라이언트가 assistant `tool_calls`와 `tool.tool_call_id`로 되돌려 보내므로 서버 상태 없이 복원된다.
- 선택지(G-6):
  - (a) Gemini **OpenAI 호환 엔드포인트**를 `openai` 백엔드로 호출하고, 서명(`extra_content`) ↔ id 보정 shim만 추가 — 코드가 가장 적다. 호환 범위는 승인 실측.
  - (b) `google-genai` SDK(루트 venv 1.68.0) **네이티브 어댑터** — 변환 약 300줄 + 테스트 · 동작 통제가 가장 확실하다.
  - (c) 게이트웨이 안에서 litellm 사용 — sre_agent가 이미 Gemini tool-calling 왕복을 검증한 경로(D-120)지만, 루트 venv 신규 의존이 크고 텔레메트리 등 부수 동작 점검이 필요하다.

### 2.11 가용성 판정 — 사다리와 noise_gate 트랙 선택

- `GET /v1/models/{alias}`의 `ready`는 **비용 0에 가까운 판정**이다(현행 `/models` GET과 같은 등급).
  - `openai` = 업스트림 `/models` 200(게이트웨이가 5초 캐시).
  - `gemini` = 키 존재 + `ALLOW_EXTERNAL`(현행 `orchestrator_available`의 키 유무 판정과 같은 의미).
  - `fabrix` = 설정 완비(FabriX는 싼 헬스 엔드포인트가 없다 — 현행도 사전 판정 없음).
- 본체: `src/llm.py`에 `gateway_alias_ready(config, alias) -> bool`을 두고(noise_gate가 import할 수 있는 모듈 — D-139 ② 허용 목록 `src.llm`), provider가 `gateway`일 때 두 판정 지점이 쓴다.
  - `deep_agent.orchestrator_available`(`:54`) · `agentic_enricher._select_backend`(`:149`).
  - 타임아웃은 `ORCHESTRATOR_HEALTH_TIMEOUT` 그대로다. 게이트웨이 미도달은 미가용(현행 vLLM 미도달과 같은 강등).
- 사다리 사유 어휘는 바꾸지 않는다 — `orchestrator_unavailable` 그대로이고, 안내 문구(`preflight.py:437`)와 `docs/21`에 게이트웨이 경우를 추가한다.
- 1토큰 생성 점검(`?deep=1&alias=`)은 사전 점검(preflight)에서만 쓴다(MLX 좀비 서버 판정 — D-222 결함 B 이전).

### 2.12 패키지 구조 (`apm_gateway` 전례)

```
llm_gateway/
├── pyproject.toml          # fastapi · uvicorn · httpx · pydantic-settings · pyyaml (+ G-6 (b)면 google-genai)
├── .env.example
├── config/routes.yaml      # 별칭 → 백엔드 · 파라미터 (비밀 없음)
├── README.md               # 기동 · 설정 · 별도 서버 배포(venv · wheel 목록) · 롤백
├── llm_gateway/
│   ├── __main__.py         # python -m llm_gateway
│   ├── config.py           # GatewaySettings(.env) + routes.yaml 로더 · 기동 검증
│   ├── domain/             # 별칭 · 능력 · 오류 어휘 · billing 판정(순수 함수)
│   ├── adapters/
│   │   ├── fabrix/         # kbgenai.py · pii.py(복제)
│   │   ├── openai/         # client.py(투명 전달 · SSE 중계 · 빈 assistant 턴 제거 · length 경고)
│   │   └── gemini/         # G-6
│   ├── application/        # router(별칭 → 어댑터) · deadline · audit · egress · readiness 캐시
│   └── interface/          # server.py(FastAPI · Bearer · SSE · 오류 봉투)
├── tests/                  # test_boundary · 어댑터 · 서버 · 골든 대조
└── testdata/               # fabrix/golden_requests.json · vllm/golden_requests.json · 스트림 녹화
```

- 게이트웨이는 **LangChain에 의존하지 않는다**(순수 httpx + 선택 SDK). 메시지 규칙은 골든으로 고정한다.
- 경계 불변식: `llm_gateway` ↔ `src`·`noise_gate`·`sre_agent`·`mcp_server`·`apm_gateway` **양방향 import 0**. `sre_agent/tests/test_boundary.py` 방식이며, 기존 패키지들의 금지 목록에 `llm_gateway`를 추가한다.
- 실행: `cd llm_gateway && ../.venv/bin/python -m llm_gateway` · 테스트 `cd llm_gateway && ../.venv/bin/python -m pytest`(mcp_server 전례).
- 포트: **9095** 제안(9096 apm · 9097·9098 sre · 9099 mcp · 9100 alarm 옆자리 · 점유 실측 후 확정 — G-12).

### 2.13 호출자 측 변경

**본체 (`src/` · `scripts/`)**

| 파일 | 변경 |
|---|---|
| `src/config.py` | `LLMConfig.provider`·`OrchestratorConfig.provider` Literal에 `gateway` 추가. 공통 필드 `LLMConfig.gateway_base_url` · `gateway_token`(시크릿) · `gateway_timeout`(read) · `gateway_total_timeout`(300) · `gateway_verify_ssl` · 별칭 `gateway_worker_model`(`worker`) · `gateway_answer_model`(`worker-answer`) · `OrchestratorConfig.gateway_model`(`orchestrator`). 오케스트레이터는 접속 정보를 `LLMConfig.gateway_*`에서 공유하고 `timeout`·`health_timeout`·deep agents 동작값은 기존 필드를 쓴다. provider가 `gateway`가 아니면 읽지 않는다 |
| `src/clients/gateway_client.py` (신규) | `GatewayChatOpenAI(ChatOpenAI)` — `_llm_type` · `kbgenai_message_order` · `total_timeout` · 마감 · `X-Gateway-Timeout` · 504 → `TimeoutError` · 502/400 → `ValueError`(원 메시지) · `max_retries=0` · `stream_chunk_timeout` · `gateway_alias_ready()` |
| `src/llm.py` | `create_llm`·`create_orchestrator_llm`에 `gateway` 분기 · `gateway_alias_ready` 재노출 · 독스트링 provider 목록 · `output_token_limit_kwargs` 주석 |
| `src/utils/llm_compat.py` | `is_kbgenai` 인스턴스 표지 판정(§2.5) |
| 직접 비교 12곳(7파일) + scripts 2곳 | `is_kbgenai(llm)` 치환 · 쓰지 않게 된 `KBGenAIChat` import 제거 |
| `src/orchestration/deep_agent.py:54` | `orchestrator_available`에 `gateway` 분기 |
| `src/schema_cache/db_structure_service.py:454` | `provider_info`에 `gateway`(모델 = 별칭 + `/v1/models`의 업스트림 모델은 기록용) |
| `src/observability/ladder.py:55` 주석 · `docs/21` | 오케스트레이터 미가용 사유에 게이트웨이 포함 |
| `scripts/scenario/preflight.py` | `external_planes` 별칭 billing 판정 · `_check_mlx`/`mlx_run_blockers` → 평면이 `gateway`면 `/health?deep=1&alias=` 점검 · 오케스트레이터 안내 문구 |
| `scripts/scenario/__main__.py` · `scripts/bench` | `llm_providers()`·`approval_policy`가 개정 판정을 쓰는지 확인(정의 1곳 · 소비 3곳 유지) |
| `src/api/settings_catalog.py` · `config/settings_help/{llm,orchestrator,general}.yaml` · `.env.example` · `.encenv.example` | 신규 키 · `gateway` 값 설명 · 리로드 대상 · 시크릿 |
| `src/api/server.py`(lifespan) | provider가 `gateway`면 기동 시 `/health` 1회(실패는 WARNING — 현행처럼 LLM 미도달이 기동을 막지 않는다) + `/v1/models` 별칭·백엔드·billing 로그 1줄 |

**noise_gate** — `agentic_enricher._select_backend`(`:149`)에 `gateway` 분기(`gateway_alias_ready(cfg, "orchestrator")`)만 추가한다. 나머지는 팩토리 경유라 바꾸지 않는다. `noise_signal_tools.vllm_healthy` 복제본은 provider가 `vllm`일 때만 쓰이므로 그대로 둔다(P11에서 정리).

**sre_agent** — **코드 변경 0 · 설정만**(G-14 권고):

```
MODEL=openai/investigation
API_BASE=http://<게이트웨이 호스트>:9095/v1
API_KEY=<LLM_GATEWAY_TOKEN>
INVESTIGATION_LLM_ENABLED=true
OVERRIDE_MAX_CONTENT_SIZE=<업스트림 실제 컨텍스트>
OVERRIDE_MAX_OUTPUT_TOKEN=<업스트림 출력 상한>
```

- litellm 모델표에 `openai/investigation`이 없으므로 holmes는 출력 상한 64000·컨텍스트 200000으로 가정한다. **토큰 예산 오버라이드는 필수**다(D-213 · `settings.py:73-84`). 값은 게이트웨이 `/v1/models`의 업스트림 정보로 운영 문서에 적는다.
- 비선두 system 강등 가드(`llm_message_guard` · D-213 후속)는 holmes 쪽 경계라 **그대로 둔다**. 게이트웨이는 메시지를 바꾸지 않는다.
- `sre_agent/tests/test_boundary.py` 금지 목록에 `llm_gateway`를 추가한다.

### 2.14 게이트웨이 설정 예 (`llm_gateway/.env.example`)

`.env` 계열에는 인라인 주석을 쓰지 않는다(Known Mistakes). 설명은 별도 줄에 둔다.

```
LLM_GATEWAY_HOST=127.0.0.1
LLM_GATEWAY_PORT=9095
# 본체 .encenv의 LLM_GATEWAY_TOKEN · sre_agent API_KEY와 같은 값
LLM_GATEWAY_TOKEN=
LLM_GATEWAY_ROUTES_FILE=config/routes.yaml
LLM_GATEWAY_ALLOW_EXTERNAL=false

FABRIX_BASE_URL=
FABRIX_API_KEY=
FABRIX_CLIENT_KEY=
FABRIX_CHAT_MODEL=
FABRIX_LLM_CONFIG=
FABRIX_ANSWER_LLM_CONFIG=

VLLM_BASE_URL=
VLLM_API_KEY=

# Gemini는 ALLOW_EXTERNAL=true일 때만 기동한다(D-127 — 호출은 건별 승인)
GEMINI_API_KEY=

SECURITY_PII_FILTER_LOG_ENABLED=true
SECURITY_PII_FILTER_LOG_UNMASK=false
SECURITY_PII_BLOCK_DUMP_ENABLED=true
```

---

## 3. 결정 게이트

**사용자 지시로 확정된 것(2026-09-30)**:
- v1 G-6 → **오케스트레이터 평면 · sre_agent 포함**.
- v1 G-10 → **별칭별 백엔드**(§2.2).
- 최종 상태 = **본체·sre_agent의 LLM 직접 클라이언트 0**(P11 — 시점은 G-7).

| G | 질문 | 선택지 | 권고 | 근거 |
|---|---|---|---|---|
| **G-1** | 분리 동기 | (a) 자격증명 격리·백엔드 전환 (b) 네트워크 — LLM 백엔드에 닿을 수 있는 호스트가 따로 있다 (c) 공용 LLM 서비스 | **사용자 답 필요** | (b)면 RAG 리트리벌도 게이트웨이로 가야 하고(G-5=(b)), 직접 경로 제거(G-7)를 서둘러야 한다 |
| **G-2** | 패키지 이름 | (a) `llm_gateway` (b) `llm_agent` | **(a)** | 모든 LLM을 받는 경계라는 역할이 이름에 드러나고 `apm_gateway`와 짝이 맞다. 계획·도구 호출 주체가 아니므로 agent가 아니다 |
| **G-3** | API 형태 | (a) OpenAI 호환 (b) 자체 REST (c) MCP | **(a)** — v2에서 근거 강화 | 소비자 3곳 모두 코드 없이 붙는다 — 본체는 `ChatOpenAI` 서브클래스, deepagents는 네이티브 tool-calling, sre_agent는 litellm `openai/` 접두 |
| **G-4** | KBGenAI 메시지 규약 | (a) 본체 삽입 유지 + 비-fabrix 어댑터가 빈 턴 제거 (b) 게이트웨이 일괄 정규화 | **(a)** | FabriX 페이로드 동일 · 인스턴스 표지로 평면 구분(§2.5) |
| **G-5** | FabriX 리트리벌(RAG · `plans/126`) 편입 | (a) 1차 비범위 (b) `POST /v1/retrieval` 추가 + 접속 정보·관리자 편집 표면 이동 | **(a)** (G-1≠(b)일 때) | LLM 채팅 호출이 아니고, 컬렉션별 접속 정보를 관리자 화면에서 편집한다(126 G-14). `plans/127`(병행 작성 — 문서 RAG 라우팅 편입)도 호출 위치에 의존하므로 (b)면 127과 함께 정한다 |
| **G-6** | Gemini 어댑터 방식 | (a) OpenAI 호환 엔드포인트 + 서명 shim (b) `google-genai` 네이티브 (c) 게이트웨이 내부 litellm | **(a)**, 승인 실측에서 호환 결함이 나오면 (b) | 테스트·PoC 전용 경로라 코드가 가장 적은 안부터 잰다. (c)는 루트 venv 신규 대형 의존 |
| **G-7** | 본체·sre_agent 직접 경로 제거 시점 | (a) 전환 기간 유지(롤백 수단) → 폐쇄망 검증 뒤 별도 결정 (b) 이번에 함께 제거 | **(a)** | D-161 — 경로 제거에는 운영 실측 4항이 앞서야 한다. (b)는 롤백 수단이 없다 |
| **G-8** | 게이트웨이 설정 편집 | (a) `routes.yaml`·`.env` + 재기동 (b) 본체 관리자 화면에서 원격 편집 | **(a)** | 단순하다. 관리자 화면에서 옮겨 간 LLM 키를 바꿔도 효과가 없으므로 도움말·매뉴얼에 명시하고, 게이트웨이로 옮긴 키는 화면에서 「게이트웨이 설정」 안내로 바꾼다(P9) |
| **G-9** | FabriX OpenAI 호환 폴백 | (a) `openai` 백엔드로 대체 (b) 그대로 이식 | **(a)** | 워커 `bind_tools` 소비처는 옵트인 `column_deriver`뿐 · 폐쇄망 사용 여부는 P0-4 |
| **G-11** | 토큰 없는 기동 | (a) 금지 (b) 루프백이면 허용 | **(a)** | 설정 누락이 운영에 새는 위험이 더 크다 |
| **G-12** | 배포·의존·토큰 분리 | 포트 · `langchain-openai` core 편입 · 호출자별 토큰 | 포트 **9095** · core **편입** · 1차 **단일 토큰**(호출자별 토큰은 운영 요구 시) | 두 평면이 모두 `GatewayChatOpenAI`를 쓰므로 langchain-openai가 필수 경로가 된다(운영 venv에는 이미 설치 · 하한 `>=1.1.13` 유지) |
| **G-13** | Ollama 처리 | (a) Ollama `/v1`을 `openai` 백엔드로 (b) `/api/chat` 네이티브 어댑터 이식 | **(a)** | 개발 전용 · 어댑터 0줄. 호환 결함이 있으면 (b) |
| **G-14** | sre_agent 전환 방식 | (a) 설정만(`MODEL=openai/<별칭>` · `API_BASE` · `API_KEY`) (b) sre_agent에 게이트웨이 전용 설정 필드 추가 | **(a)** | holmes `Config`가 `api_base`를 받는다(`diagnosis.py:173` · 0.36.0 실측 주석). 코드 변경 0 |
| **G-15** | Anthropic 백엔드 | sre_agent 코드 기본 `MODEL=anthropic/claude-sonnet-5`(`settings.py:20`) — 운영·개발이 실제로 Anthropic을 쓰는가 | **사용자 답 필요** | 쓰면 `anthropic` 어댑터 종류가 추가된다(Messages API 변환 · 과금 외부 · D-127). 안 쓰면 비범위 |

---

## 4. 작업 분해

각 단계는 앞 단계의 verify가 통과해야 시작한다. **P1~P4(게이트웨이)와 P5~P7(호출자)은 §2.1·§2.4 계약이 고정되면 서로 독립이다.**

### P0. 실측·기준선 (코드 0)

| ID | 작업 | verify |
|---|---|---|
| P0-1 | **FabriX 골든** — 현 `KBGenAIChat`을 `tests/mocks/fabrix_kbgenai_mock.py` transport로 호출해 요청 바디 녹화(패턴: System+빈 AI+Human · System+Human · 멀티턴 · Human만 · `llmConfig` 유/무 × deterministic/answer · stream 유/무) | 2회 녹화 diff 0 |
| P0-2 | **vLLM 골든** — 현 `_create_orchestrator_vllm`의 `ChatOpenAI`로 가짜 OpenAI 서버에 요청(도구 2종 `bind_tools` · 도구 결과 턴 · 스트림) → 요청 바디 녹화. MLX 설정 조합(`max_tokens` · `extra_body`)도 녹화 | 녹화 결정성 |
| P0-3 | **sre_agent 요청 형태** — `sre_agent/.venv`에서 holmes `DefaultLLM`을 가짜 서버(`API_BASE`)로 1회 호출해 litellm 요청 바디·헤더 녹화(`MODEL=openai/<이름>`) | 녹화 기록 |
| P0-4 | **운영 실제값 확인(사용자)** — 본체 `.env`의 `LLM_PROVIDER`·`ORCHESTRATOR_PROVIDER`·`FABRIX_CLIENT_KEY` 유무 · sre_agent `MODEL`·`API_BASE`(G-15) · LLM 백엔드 도달 가능 호스트(G-1) · 게이트웨이 배치 호스트·포트 | 답변 기록 |
| P0-5 | langchain-openai 1.3.2 직렬화 실측 — `AIMessage("")` · 리스트 content · 헤더 주입 방식 ⓐ/ⓑ | 채택안 기록 |
| P0-6 | Ollama `/v1` 호환 범위(로컬 Ollama가 있을 때만 · 비과금) | 결과 기록(G-13) |
| P0-7 | **Gemini 승인 실측(G-6 · D-127 건별 승인)** — OpenAI 호환 엔드포인트로 다중 턴 tool-calling 3왕복 · 스트림 · 사고 서명 필드 형태 | 승인 받은 뒤에만. 결과로 G-6 확정 |
| P0-8 | 테스트 기준선(D-303) — 모듈 단위 회귀(`python scripts/regress.py`)에서 실패가 나온 것만 **세션 시작 커밋 SHA**의 `git worktree add` 격리 사본에서 재대조한다. 본체 `pytest` · `sre_agent` · `mcp_server` 전체 집계는 사용자 요청 시 | 실패분의 대조 결과 저장 |

### P1. 게이트웨이 골격 + 라우팅 설정 + fabrix 어댑터

- `pyproject.toml` · `config.py`(`.env` + `routes.yaml` · 기동 검증 4종) · `domain/` · `adapters/fabrix/`(kbgenai · pii).
- 이식 테스트: `test_fabrix_kbgenai_stream.py` · `test_fabrix_total_timeout.py`(D-198) · `test_fabrix_llm_config.py` · `test_pii_filter.py`(응답측).
- **verify**: FabriX 골든과 요청 바디 동치(키 순서 포함 · P0-1 전 패턴) · 기동 검증 거부 사례 4종 · `test_boundary`.

### P2. openai 어댑터 + 송신 정책 + billing + 가용성

- 투명 전달 · SSE 라인 중계 · 빈 assistant 턴 제거 · `params` 병합 · `warn_on_length` · 업스트림 4xx 상태 보존 · readiness 캐시.
- **verify**:
  - vLLM 골든과 JSON 동치(P0-2 — 차이가 있으면 목록화해 무해 판정).
  - tool_call 스트림 조각이 그대로 중계되는지(바이트 비교).
  - 공인 호스트 + `ALLOW_EXTERNAL=false` → 기동 거부.
  - billing 판정 표 단위 테스트 · `/v1/models/{alias}` ready 캐시.

### P3. gemini 어댑터 (G-6 결과에 따라)

- (a)면 `openai` 어댑터에 서명 shim(응답 `extra_content` → id 부호화 · 요청 id → `extra_content` 복원). (b)면 네이티브 변환.
- **verify**: 녹화 응답(P0-7) 재생 테스트 · 서명 왕복 단위 테스트. 실 호출 재검증은 건별 승인.

### P4. 인터페이스

- FastAPI 앱 · Bearer · SSE 인코더 · 오류 봉투 · `X-Gateway-Timeout` · 감사 로그 · `/v1/models[/{alias}]` · `/health`(`?deep=1&alias=`) · `__main__`.
- **verify**:
  - httpx `ASGITransport`로 전 경로 검사 · 401/400/403/502/504 매핑.
  - 스트림 중 클라이언트 취소 → 업스트림 스트림 닫힘.
  - 마감 헤더 2초 → 2.5초 안에 504 · 감사 로그에 본문 0.

### P5. 본체 워커 평면 (`LLM_PROVIDER=gateway`)

- §2.13 표의 워커 항목 · `is_kbgenai` 표지 · 직접 비교 12곳 치환 · 설정 화면·도움말. **기본값은 바꾸지 않는다.**
- **verify**:
  - 기본값에서 본체 테스트 집계가 P0-8 기준선과 동일(신규 실패 0).
  - `grep -rn "KBGenAIChat" src --include=*.py`가 `src/clients/` · `src/llm.py` · `src/utils/llm_compat.py` 밖에서 0.
  - `test_settings_catalog` 카운터 · `arch_check --ci` · `overfit_check --ci` · ruff/mypy(`uvx --offline`).

### P6. 본체 오케스트레이터 평면 (`ORCHESTRATOR_PROVIDER=gateway`)

- `create_orchestrator_llm` 분기 · `orchestrator_available`·`agentic_enricher._select_backend` 게이트웨이 분기 · 사다리 안내 · preflight(별칭 billing · deep 점검).
- **verify**:
  - 게이트웨이 `ready=false` → `select_orchestration_backend`가 `semantic_router`(사다리 `orchestrator_unavailable`) · noise_gate 트랙 A/noop 강등(현행 미가용과 같은 결과).
  - `ready=true` → 1단 조립·트랙 B 선택.
  - 기존 `test_deep_agent.py`·`test_agentic_enricher.py`의 provider 분기 테스트에 `gateway` 사례 추가.

### P7. sre_agent 전환 (설정만)

- `sre_agent/.env.example`에 게이트웨이 예시 · README · `test_boundary` 금지 목록 · 운영 문서에 토큰 예산 오버라이드 값.
- **verify**:
  - sre_agent venv에서 holmes → 게이트웨이(ASGI 또는 로컬 기동) → 가짜 OpenAI 업스트림: 업스트림이 받은 바디 = P0-3 직결 바디와 JSON 동치(`model`만 다름).
  - 조사 1건 tool-calling 루프 완주(가짜 업스트림 대본).
  - 업스트림 400 `System message must be at the beginning.` → holmes가 종전과 같은 예외 계열을 받는지.

### P8. 계약·동등성·실 LLM 검증

1. **교차 계약**(루트 `tests/test_llm_gateway_contract/` — 2단 중첩 해석 때문에 conftest가 `llm_gateway/`를 `sys.path`에 넣는다 · D-139 ③):
   - 워커: `GatewayChatOpenAI` → ASGI 게이트웨이 → fabrix mock에서 FabriX 수신 바디 = P0-1 골든 · 토큰 순서·누적 텍스트 · `USER_RESPONSE_TAG` 토큰 이벤트(D-009) · `TimeoutError`/`ValueError` 계약 · instructor MD_JSON.
   - 오케스트레이터: → 가짜 vLLM에서 수신 바디 = P0-2 골든(JSON 동치) · 스트리밍 tool_call 조립 결과 동일.
2. **경로 동등성**: 라우팅 평가 mock 하네스(`scripts/eval_routing.py` mock 모드) 또는 파이프라인 mock 1세트를 provider `fabrix`와 `gateway`로 각각 돌려 FabriX 수신 바디 전량 diff 0.
   deepagents 1단은 가짜 tool-calling 업스트림 대본으로 직결 vs 게이트웨이 경유의 도구 호출 순서·최종 응답 동일.
3. **로컬 실 LLM**(승인 불요 · D-240): 게이트웨이의 `worker`·`orchestrator`·`investigation`을 모두 `mlx`(127.0.0.1:8080) 백엔드로 매핑한다.
   - 실행 전 preflight로 세 별칭 billing `internal` 확인.
   - `RUN_LOCAL_LLM=1 pytest -m live_llm` 워커 표본 · 본체 서버 단일 DB 질의 1건 · 1단(deep_agent) 도구 질의 1건 · noise_gate 트랙 B 1건 · sre_agent 조사 1건.
   - MLX 결과는 로직 확인용이다(D-240) — 9B 품질 한계는 D-222 부기대로 인용하지 않는다.
4. **Gemini**: 두 평면 Gemini 매핑 스모크는 **건별 승인 후**에만 한다(D-127).
5. **홉 오버헤드**: 로컬 게이트웨이 경유 vs 직접(고정 지연 mock) — 비스트림 p50/p95 · 첫 토큰 · 오케스트레이터 tool 턴당 지연. 목표 p95 +20ms 이내는 **추정 목표치**이며 측정 후 확정한다. 성능 결론은 내부망 결과로만 낸다.

### P9. 문서·매뉴얼·결정 기록

- `llm_gateway/README.md` · `docs/03_setup_guide.md` 게이트웨이 절(별도 서버 배치 · venv · 기동 · 헬스체크 · 롤백 · MLX를 게이트웨이 백엔드로 쓰는 법 — §7.2 개정) · `docs/21_orchestration_ladder.md`(가용성 판정) · `sre_agent/README.md`.
- `CLAUDE.md` 「저장소 지도」 · 「패키지 경계」 표 · Tech Stack의 LLM provider 행 · 개발 명령(게이트웨이 기동) · 「과금 외부 API 승인 게이트」 항목(두 곳을 모두 켜야 외부 — §2.9).
- `docs/02_decision.md`: D-번호 등재(G 확정 + 서두의 부기 대상 D-174 · D-037·D-042·D-060 · D-194 · D-155 · D-222 · D-120·D-229·D-230 · D-225·D-251) · `plans/INDEX.md` 상태.
- **매뉴얼(D-255)**: 관리자 설정 화면의 LLM·오케스트레이터 키 구성이 바뀐다. `scripts/manual/content/admin.md`(설정 절 · 3358행 운영 평면 설명)를 갱신한다
  → 화면이 바뀌면 `captures.yaml` 갱신 → `python -m scripts.manual.run_capture --only <캡처ID>` → `python -m scripts.manual.build` → `pytest tests/test_manual`.

### P10. 폐쇄망 전환 (사용자 수행)

1. 게이트웨이 서버 배치(`routes.yaml` 운영값 — `worker`/`worker-answer` → fabrix · `orchestrator`/`investigation` → vLLM · `.env`에 자격증명) → `/health?deep=1&alias=` 별칭별 확인.
2. **워커 먼저**: 본체 `LLM_PROVIDER=gateway` → 대표 질의 3종(일반 · 인프라 · 폼필 — D-194 실측 표본) → 감사 로그·PII 덤프 위치 확인.
3. **오케스트레이터**: `ORCHESTRATOR_PROVIDER=gateway` → 기동 로그의 사다리 확정 단 · noise_gate 트랙 B 1건.
4. **sre_agent**: §2.13 설정 → 조사 1건.
5. **롤백**: 평면별로 provider를 되돌린다(G-7 기간 동안 본체에 자격증명이 남아 있다).

### P11. (별도 결정) 직접 경로 퇴역 — 최종 상태 "모든 LLM = 게이트웨이"

- D-161 ② 4항 실측을 첨부한다 — 운영 `.env` 실제값 · 게이트웨이 운영 상태 · 대상 파일 `git log`(`--all`이면 `merge-base --is-ancestor`) · 역방향 import.
- 정리 대상:
  - 본체 `src/clients/{fabrix_kbgenai,fabrix_client,ollama_client,mlx_client}.py` · `src/llm.py` 직접 분기(`_create_fabrix`·`_create_ollama`·`_create_gemini`·`_create_mlx`·`_create_orchestrator_vllm`·`_create_orchestrator_gemini`) · `LLMConfig`/`OrchestratorConfig` provider 필드.
  - `vllm_healthy` 2벌 · `worker_provider_override`(→ 별칭 선택 `LLM_GATEWAY_WORKER_MODEL`) · preflight MLX 직접 점검 · 본체·sre_agent `.encenv` LLM 키.
  - 테스트 mock을 게이트웨이 ASGI + 업스트림 mock으로 이전(`tests/mocks/fabrix_kbgenai_mock.py` · `scripts/eval_routing.py` mock 모드).
- `is_kbgenai`는 표지 판정만 남긴다.
- 게이트웨이가 유일 경로가 되므로 **게이트웨이 가용성 = 전 LLM 기능 가용성**이다. 퇴역 전에 운영 감시(헬스 · 감사 로그 경보)가 서 있어야 한다(R-2).

### 4.12 병렬 수행 단위 (서브에이전트 분담 시)

- 계약(§2.1 · §2.2 별칭 표 · §2.4 · §2.6 헤더)을 먼저 `spec/SPEC-llm-gateway.md`로 고정한다(D-244).
- 그러면 **게이트웨이(P1~P4) · 본체 워커(P5) · 본체 오케스트레이터(P6) · sre_agent(P7)** 는 서로 독립이다. P5와 P6은 `src/config.py`·`src/llm.py`를 함께 고치므로 **순서대로** 하거나 한 작업자에게 맡긴다.
- `src/config.py` · `settings_catalog` · `docs/02_decision.md` · `plans/INDEX.md`는 병행 세션이 자주 편집한다. 착수 직전 `git status`와 병행 세션을 확인하고, 자기 델타만 넣는다.

---

## 5. 수용 기준

1. FabriX 수신 요청 바디가 현행 골든과 동치다(키 순서 포함 · P0-1 전 패턴 · 비스트림/스트림).
2. vLLM 수신 요청 바디가 현행 `ChatOpenAI` 골든과 JSON 동치다. 차이가 있으면 목록화하고 무해 판정 근거를 기록한다.
3. provider 기본값에서 본체·sre_agent 테스트 집계가 기준선과 같다(신규 실패 0).
4. provider `gateway`에서 다음이 유지된다(계약 테스트):
   - 워커: 스트리밍 토큰 SSE · 목적 프로파일 · `TimeoutError` 계약 · PII 차단 본문 판정 · instructor MD_JSON.
   - 오케스트레이터: 네이티브 tool-calling(스트림 조각 포함) · 가용성 강등.
   - sre_agent: tool-calling 루프 · 오류 계열.
5. 경계 불변식 import 0(양방향 · 5개 패키지).
6. 외부(공인) 백엔드는 게이트웨이 `ALLOW_EXTERNAL`과 호출 측 승인 게이트를 **둘 다** 열어야 호출된다. 과금 판정은 별칭 billing을 반영한다(조회 실패 = 외부).
7. 게이트웨이 로그에 프롬프트·응답 본문이 없다(감사 1줄 형식 단언).
8. 로컬 MLX 종단이 성공한다 — 워커 1질의 · 1단 도구 질의 1건 · noise_gate 트랙 B 1건 · sre_agent 조사 1건(비과금 확인 후).
9. 문서·매뉴얼·결정 등재를 마치고 `plans/INDEX.md` 상태를 갱신한다.

---

## 6. 위험과 완화

| ID | 위험 | 영향 | 완화 |
|---|---|---|---|
| R-1 | 페이로드 표류(FabriX · vLLM) | 응답 품질·PII 차단·tool-calling 동작 변화 | 골든 대조(P1 · P2 · P8) |
| R-2 | **단일 장애점** — 모든 LLM 기능(질의 · 알람 분석 · 조사)이 게이트웨이 하나에 의존 | 전면 중단 | 별칭별 `/health?deep=1` 감시 · 호출자는 사유를 명시 노출(**직접 경로로 조용히 폴백하지 않는다**) · 전환 기간 롤백은 설정(G-7) · P11 전 운영 감시 필수 · 다중 인스턴스는 후속 |
| R-3 | 홉 너머 무한대기(D-198 재발) | SSE 무한 대기 | 3중 상한(§2.6) · 하트비트 mock 단언(게이트웨이·본체 각각) |
| R-4 | 과금 가드 우회 — 게이트웨이는 pytest 가드 밖 · Gemini 키가 게이트웨이에 상주 | 무승인 과금(D-127 위반) | `ALLOW_EXTERNAL` 기동 거부 + 별칭 billing + fail-closed + 두 곳 모두 켜야 외부(§2.9) · 기동 로그에 external 별칭 명시 |
| R-5 | 예외 형태 변화 — `TimeoutError` 분기 13모듈 · litellm 예외 계열 | 타임아웃·오류 처리 오동작 | 본체 클라이언트 변환 + 업스트림 4xx 상태 보존 + 계약 테스트(P7 · P8) |
| R-6 | Gemini 사고 서명 왕복 실패(추정) | 개발·PoC의 Gemini 다중 턴 tool-calling 400 | G-6 서명 shim · 승인 실측(P0-7) · 실패 시 네이티브 어댑터 |
| R-7 | sre_agent 토큰 예산 오판 — 별칭 이름이 litellm 모델표에 없음 | 매 호출 `max_tokens=64000` · 컨텍스트 초과(D-213 유형) | `OVERRIDE_MAX_*` 필수화(운영 문서 · sre_agent 기동 점검 항목) |
| R-8 | 첫 청크 120초 절단(D-222 결함 A) | 긴 프롬프트 스트림 조기 실패 | `stream_chunk_timeout` = 총상한 |
| R-9 | 설정 리로드 비대칭 · 관리자 화면 키 이동 | 운영자 혼란 | 도움말·매뉴얼 명시 · 화면 안내 문구(G-8) |
| R-10 | PII 규칙 이중화 드리프트 | 진단 누락 | 규칙 동등성 테스트 |
| R-11 | 가용성 판정 의미 변화 — 사다리 1단 확정이 게이트웨이 `ready`에 의존 | 기동 시 단 확정 흔들림 | `ready`는 업스트림 `/models`와 같은 등급 · 5초 캐시 · 미도달 = 미가용(현행 강등과 같음) · 기동 로그 1줄 |
| R-12 | 병행 세션과 편집 충돌 | 병합 사고 | 착수 직전 `git status` · 자기 델타만 |
| R-13 | langchain-openai core 편입이 공유 venv에 영향(D-181) | 기존 패키지 파손 | 설치 버전 하한 유지 · 추가 설치 0 확인 |

---

## 7. 비범위 · 후속

- 응답 캐시 · 레이트 리밋 · 다중 게이트웨이 인스턴스 · 백엔드 자동 장애 조치 · 토큰 사용량 집계 · FabriX 연결 재사용.
- G-5 · G-15에서 (b)/사용을 고르면 별도 절 또는 계획서로 다룬다.

## 부록 A. 실측에 쓴 명령

```bash
# 원격 LLM 클라이언트 전수
grep -rn "ChatOpenAI\|ChatGoogleGenerativeAI\|from litellm\|from openai\|google.genai\|ChatAnthropic" \
  --include='*.py' src noise_gate scripts sre_agent/sre_agent mcp_server/mcp_server apm_gateway/apm_gateway tools agents
# 팩토리 소비처
grep -rln "create_llm\|create_orchestrator_llm" --include='*.py' src noise_gate scripts
# 가용성 판정
grep -rn "vllm_healthy\|orchestrator_available\|select_orchestration_backend" --include='*.py' src noise_gate scripts
# 직접 HTTP 호출(헬스·1토큰 점검)
grep -rn "chat/completions\|/models\"\|/api/chat" --include='*.py' src noise_gate scripts
# KBGenAI 규약 — 직접 클래스 비교 12곳
grep -rn "isinstance(llm, KBGenAIChat)\|type(llm) is KBGenAIChat" src
# sre_agent holmes 배선
grep -n "api_base\|model=" sre_agent/sre_agent/diagnosis.py
# 설치 버전
.venv/bin/python -c "import importlib.metadata as m; print(m.version('langchain-google-genai'), m.version('google-genai'), m.version('deepagents'))"
ls sre_agent/.venv/lib/*/site-packages | grep -iE "^litellm|^holmesgpt|^openai-|^google_genai"
```

## 부록 B. 개정 이력

| 판 | 일자 | 내용 |
|---|---|---|
| v1 | 2026-09-30 | 워커 평면 FabriX ↔ OpenAI 호환 분리. 오케스트레이터·sre_agent·Gemini·Ollama는 비범위(v1 G-6 (a) · §0.3). 번호는 병행 세션의 127 선점으로 128 재부여 |
| v2 | 2026-09-30 | 사용자 지시 *"모든 llm을 llm gateway에서 처리하도록 계획을 수정하라."* — 범위를 전 LLM 호출로 확대. 별칭 4종·`routes.yaml`(§2.2) · 어댑터 3종(§2.3) · tool-calling 통과 계약(§2.4) · KBGenAI 표지를 인스턴스 필드로(§2.5) · 별칭 billing(§2.9) · Gemini 사고 서명(§2.10) · 별칭 가용성(§2.11) · sre_agent 설정 전환(§2.13) · 게이트 재편(v1 G-6·G-10 확정, G-13~G-15 신설) · 단계 P0~P11로 재구성(오케스트레이터 P6 · sre_agent P7 · 직접 경로 퇴역 P11) |
