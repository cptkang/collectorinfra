# 56. LLM 관측성 확보 — Langfuse 통합 (LLM Observability with Langfuse)

> 작성일: 2026-07-08 (갱신: 2026-07-10 — deepagents 트랙 B LLM 호출 실측 검토 반영, §2.6/§4.6.1/§4.9)
> **관련 Plan**: 49(deepagents 오케스트레이션), 50(멀티턴·제어 평면 토큰), 52(알람 노이즈 캔슬링), 55(멀티소스 관측 로드맵)
> **관련 결정**: D-050 (신규 예정 — §9. **등재 직전 `## D-` 헤더와 「변경 이력」 표를 모두 grep하여 실제 최대 번호 재확인** 필수, Known Mistakes 2026-06-29 참조)
> **상태**: 계획 (Phase L1~L4 미착수)

---

## 1. 개요 및 목표

### 1.1 배경 — 현재 LLM 호출은 "블랙박스"

본 시스템은 자연어→SQL 파이프라인(LangGraph 15+ 노드), deepagents 오케스트레이션(vLLM 제어 평면 + FabriX 데이터 평면), 알람 분석 그래프까지 **40여 곳에서 LLM을 호출**하지만, 다음이 전혀 기록되지 않는다:

- **LLM 입출력**: 어떤 프롬프트가 들어가 어떤 응답이 나왔는지 (재현·디버깅 불가)
- **토큰 사용량**: 입력/출력 토큰 수 (Plan 50의 "제어 평면 토큰 예산 압박" 문제를 정량 측정할 수단이 없음)
- **지연시간 분해**: 응답 시간 목표(단순 <10s, 복합 <30s) 미달 시 어느 노드·어느 LLM 호출이 병목인지 알 수 없음
- **재시도 루프 가시성**: query_generator ↔ query_validator/query_executor 재시도(최대 3회)가 실제로 얼마나 발생하는지, 어떤 프롬프트가 재시도를 유발하는지
- **멀티턴 세션 흐름**: thread_id 기반 대화가 세션 단위로 묶여 보이지 않음

기존 로깅 자산은 **SQL 중심**이다:

| 기존 자산 | 위치 | 기록 범위 | 한계 |
|-----------|------|----------|------|
| 감사 로그 | `src/security/audit_logger.py:64-110` → `logs/audit-YYYY-MM-DD.jsonl` | SQL, row_count, execution_time_ms, user_id, thread_id | LLM 호출 자체는 미기록 |
| SQL 파일 로그 | `src/utils/sql_file_logger.py:49-112` → `sqls/act/YYYY-MM-DD.sql` | 실행 SQL + 호출처 + 소요시간 | SQL만 |
| SSE 진행 이벤트 | `src/api/routes/query.py` (`on_chain_start`/`on_chain_end`) | 노드 진입/완료 (실시간 UI용) | 저장 안 됨, LLM 단위 아님 |

즉 "SQL이 무엇이 실행되었나"는 알 수 있으나 **"LLM이 왜 그 SQL을 만들었나"는 알 수 없다**. Langfuse를 도입하여 trace(요청) → span(노드) → generation(LLM 호출) 계층으로 전 과정을 기록·시각화한다.

### 1.2 목표

1. **전 LLM 호출 추적**: 메인 그래프·orchestration·alarm 경로의 모든 LLM 호출(`ainvoke`/`astream`)을 Langfuse generation으로 기록 (프롬프트, 응답, 모델명, 지연시간)
2. **토큰 사용량 계측**: 커스텀 클라이언트(FabriX/KBGenAI/Ollama)에 `usage_metadata` 반환을 구현하여 호출별 입력/출력 토큰을 Langfuse에 집계
3. **세션·사용자 단위 뷰**: thread_id → Langfuse session, 인증 user_id → Langfuse user로 매핑하여 멀티턴 대화 흐름을 세션 단위로 조회
4. **재시도·오류 가시성**: 재시도 루프가 하나의 trace 안에 반복 span으로 나타나 실패 패턴 분석 가능
5. **무회귀·옵트인**: `LANGFUSE_ENABLED=false`(기본)면 기존 경로 완전 무변. SSE 토큰 스트리밍(D-009), HITL 인터럽트, 체크포인트 동작에 영향 0

### 1.3 성공 기준

1. `/query/stream` 1회 호출 시 Langfuse UI에서 **하나의 trace** 안에 context_resolver→…→output_generator 노드 span과 각 LLM generation(입출력 포함)이 계층 구조로 보인다
2. 같은 thread_id로 2턴 대화 시 Langfuse **session 뷰**에 두 trace가 묶여 보인다
3. FabriX(KBGenAIChat) 호출의 generation에 **input/output 토큰 수**가 표시된다 (API가 usage를 반환하는 경우)
4. `LANGFUSE_ENABLED=false` 상태에서 전체 테스트 스위트가 기존과 동일하게 통과하고, SSE 토큰 스트리밍이 회귀 없이 동작한다
5. Langfuse 서버 다운 시에도 질의 처리는 정상 동작한다 (관측 실패가 서비스 실패로 전파되지 않음)
6. 마스킹: 감사 로그와 동일 수준으로 민감 데이터(비밀번호·키 패턴)가 Langfuse 저장 전 마스킹된다

### 1.4 설계 원칙

1. **옵트인 + 안전 폴백**: 기존 기능 플래그 패턴(`enable_semantic_routing` 등)과 동일하게 config 게이트. SDK 미설치·서버 미가용 시 조용히 비활성 (deepagents 폴백 패턴 계승 — `graph.py:_deep_agent_buildable`)
2. **콜백 주입은 진입점에서 1회**: 40여 호출 지점을 개별 수정하지 않는다. LangChain 콜백은 `graph.ainvoke(config={"callbacks":[handler]})`로 주입하면 contextvar를 통해 하위 runnable로 자동 전파된다(Python 3.11 + langchain-core 1.x). **단, 자동 전파는 반드시 실측 검증**(§4.4) — 계획서 의사코드를 실 API 검증 없이 신뢰하지 않는다(Known Mistakes 2026-06-17/06-25)
3. **폐쇄망 우선**: Langfuse는 **self-hosted**(도커) 전제. 외부 egress 불필요. SDK wheel은 `wheels/` 반입 체계를 따른다
4. **관측은 부수 채널**: Langfuse 기록 실패는 warning 로그로만 남기고 질의 처리 경로에 예외를 전파하지 않는다
5. **pydantic-settings 규칙 준수**: Langfuse SDK가 자체적으로 `os.environ`의 `LANGFUSE_*`를 읽지만, 본 프로젝트의 `.env` 로딩(pydantic-settings)은 `os.environ`에 주입하지 않는다(Known Mistakes 2026-06-10). 따라서 **키를 `Langfuse(public_key=..., secret_key=..., host=...)` 생성자에 명시 전달**한다. `.env`에는 인라인 주석 금지(Known Mistakes 2026-07-02/07-03)

---

## 2. 현재 구현 분석

### 2.1 LLM 인스턴스 생성과 주입 구조

- `src/llm.py:create_llm()` — 워커(데이터 평면) LLM 팩토리: ollama(`LLMAPIClient`) / fabrix(`KBGenAIChat` 또는 `FabriXAPIClient`) / gemini(`ChatGoogleGenerativeAI`)
- `src/llm.py:create_orchestrator_llm()` — 제어 평면: vLLM(`ChatOpenAI`) / gemini
- `src/graph.py:build_graph()` — LLM **1회 생성 후 `functools.partial`로 노드에 주입**. 노드 내부에서 `llm.ainvoke(messages)` 형태로 호출(대부분 config 미전달)
- `src/llm.py:astream_text()` — 최종 사용자 응답용 스트리밍 헬퍼. `tags=[USER_RESPONSE_TAG]`로 SSE 토큰 필터링(D-009)

### 2.2 LLM 호출 지점 분포 (전수 조사 결과)

| 영역 | 파일 (주요 라인) | 용도 | 호출 방식 |
|------|------------------|------|----------|
| 메인 노드 | `src/nodes/input_parser.py:57,175,275` | 의도 파싱·필터 추출 | `ainvoke` |
| 〃 | `src/nodes/schema_analyzer.py:170,266,708,1127` | 스키마 분석·컬럼 추론 | `ainvoke` |
| 〃 | `src/nodes/query_generator.py:166,215` | SQL 생성 | `ainvoke` |
| 〃 | `src/nodes/result_organizer.py:219,242,366,393,532,579` | 결과 정제·충분성 판단 | `ainvoke` |
| 〃 | `src/nodes/output_generator.py:144,161` | **최종 응답 생성** | `astream_text` (USER_RESPONSE_TAG) |
| 〃 | `src/nodes/general_inference.py:133,153` | 일반 추론 응답 | `astream_text` |
| 〃 | `src/nodes/field_mapper.py:71`, `cache_management.py:48,144`, `multi_db_executor.py:109,471` | 필드매핑·캐시의도·멀티DB | `ainvoke` |
| 라우팅 | `src/routing/semantic_router.py:62,280` | DB 라우팅 분류 | `ainvoke` |
| orchestration | `intent_planner.py:97,275`, `replanner.py:62,156`, `result_aggregator.py:52`, `agent_orchestrator.py:56` | 의도 계획·재계획·집계 | `ainvoke` |
| 〃 | `deep_agent.py:219` (`run_deep_agent`) | deepagents `CompiledStateGraph.ainvoke` — 내부에서 vLLM tool-calling + 도구 내 FabriX 워커 호출 + 최종 합성 (**상세 §2.6**) | 중첩 그래프 |
| alarm | `src/alarm/application/nodes/alarm_analyzer.py:236,294`, `agentic_enricher.py:92-98`, `src/alarm/infrastructure/noise_signal_tools.py:244-297` | 알람 분석·노이즈 보강 | `ainvoke` |
| 문서/캐시 | `src/document/field_mapper.py:672,951,1224,1298`, `src/schema_cache/cache_manager.py:830,933`, `description_generator.py:84,154` | 템플릿 매핑·스키마 설명 생성 | `ainvoke` |

### 2.3 그래프 실행 진입점 (콜백 주입 대상)

| 진입점 | 위치 | 현재 config 전달 |
|--------|------|------------------|
| `/query` (동기) | `src/api/routes/query.py:365,605` | `{"configurable": {"thread_id": ...}}`만 |
| `/query/stream` (SSE) | `src/api/routes/query.py:750,989` — `graph.astream_events(input_state, thread_config, version="v2")` | 〃 |
| CLI | `src/main.py:57` | `thread_id="cli-session"` |
| 알람 워커 | `src/alarm/` 알람 그래프 invoke 지점 (`alarm_graph.py` 경유) | 별도 |
| 스키마 캐시 갱신 | `src/schema_cache/` (배치성) | 없음 |

### 2.4 커스텀 클라이언트의 관측 관련 현황 (핵심 갭)

| 클라이언트 | `_generate` | `_agenerate` | `_astream` | `usage_metadata` 반환 |
|-----------|:---:|:---:|:---:|:---:|
| `FabriXAPIClient` (`src/clients/fabrix_client.py:195`) | ✓ | ✗ | ✗ | **✗** |
| `LLMAPIClient` (`src/clients/ollama_client.py:260`) | ✓ | ✗ | ✗ | **✗** |
| `KBGenAIChat` (`src/clients/fabrix_kbgenai.py:99,123,194`) | ✓ | ✓ | ✓ | **✗** |

세 클라이언트 모두 `BaseChatModel` 상속이므로 **LangChain 콜백 체계(on_chat_model_start/end)는 기본 동작으로 발화**된다 → Langfuse CallbackHandler가 입출력·지연시간은 자동 수집 가능. 그러나 `ChatResult`에 `usage_metadata`를 싣지 않아 **토큰 수는 0/미표시**가 된다. 이것이 코드 수정이 필요한 유일한 클라이언트 레벨 갭이다.

### 2.5 세션·사용자 식별 자산

- thread_id: 프론트(`static/app.js`) → 요청 body → `thread_config` (Plan 50에서 프론트 누락 수정 완료)
- user_id: `AuthConfig` 기반 인증(`src/api/dependencies.py`) — 감사 로그에 이미 user_id 기록 중 → 같은 값을 Langfuse trace 속성으로 재사용

### 2.6 deepagents(트랙 B) 경로 LLM 호출 상세 (2026-07-10 실측 추기)

트랙 B는 메인 그래프의 `deep_agent` 단일 노드(`graph.py:358` — `partial(run_deep_agent, app_config=config, worker_llm=llm)`) 안에서 **3계층의 LLM 호출**이 중첩된다:

| # | 계층 | 호출 지점 | LLM | config 전달 현황 |
|---|------|----------|-----|------------------|
| B-1 | 제어 평면 (tool-calling 루프) | `deep_agent.py:219` `agent.ainvoke({"messages": ...})` — deepagents `CompiledStateGraph` 내부에서 vLLM 호출 N회 반복 | `ChatOpenAI`(vLLM) 또는 Gemini | **config 미전달**. `run_deep_agent`는 파라미터명 `config`를 의도적으로 회피(`deep_agent.py:193` 주석 — LangGraph 가로채기 방지)하므로 **RunnableConfig를 받지도, 내부로 넘기지도 않음** |
| B-2 | 데이터 평면 (도구 내부) | `deepagents_tools.py:153` `spec.handler(task, isolated, llm=spec.model or worker_llm, ...)` — handler는 기존 노드 함수를 직접 호출: `run_data_query_pipeline`(`classify_dbs`→단일/멀티 파이프라인→`result_organizer`), `run_general_inference`, `run_cache_management` 등 (`subagents.py:580~`) | FabriX(`KBGenAIChat`) 워커 | 노드 내부 `llm.ainvoke`/`astream_text` 모두 config 미전달 — contextvar 전파에 의존. 도구 코루틴은 deepagents 그래프의 도구 실행 노드 안에서 돌므로 **asyncio 컨텍스트 복사로 전파될 것으로 예상되나 실측 필요**(§4.4) |
| B-3 | 최종 응답 합성 (D-062) | `deep_agent.py:263` `result_aggregator(agg_state, synthesize=True)` → per-task `_finalize_task`(복합 task 시 스트리밍 억제) + `_synthesize_finalized` → `astream_text(llm, messages, tags=[USER_RESPONSE_TAG])` (`result_aggregator.py:429`) | FabriX 워커 | `astream_text`(`src/llm.py`)가 **자체 config(`{"tags": tags}`)를 만들어 전달** — 부모 contextvar config(callbacks)와 병합되는지가 이 generation의 trace 귀속을 좌우(langchain-core `ensure_config`의 contextvar merge 동작 실측 필요) |

부가 사실:

- **orchestrator LLM은 요청마다 생성**: `run_deep_agent` → `build_deep_agent`(`deep_agent.py:121` `create_orchestrator_llm`) — 요청 스코프이므로 평면 태그 부착(§4.9)·핸들러 수명 관리에 유리. 기동 시 `_deep_agent_buildable`(`graph.py:271`)은 조립 시험만 하고 호출하지 않으므로 계측 무관
- **`spec.model` per-agent 모델 슬롯**(`subagents.py:90`)은 현재 전부 None(Phase 7 예약) — 워커 단일. 추후 채워지면 generation의 모델명 구분이 자동으로 따라오는지 확인 필요
- `vllm_healthy`(`deep_agent.py:24`)는 requests 기반 health check — LLM 아님, 계측 대상 아님
- deepagents 조립 실패 폴백(`deep_agent.py:208~`)은 LLM 무호출 — trace에는 deep_agent span만 남고 generation 없음(정상)
- **트랙 A**(`agent_orchestrator.py`, `enable_deepagent_orchestration` + 트랙 B 비활성 시)도 동일 handler(`SUBAGENT_REGISTRY`)를 그래프 노드에서 직접 디스패치하므로 B-2와 동일 검증으로 커버됨

---

## 3. 기술 선택

### 3.1 채택: Langfuse self-hosted + Python SDK v4 (LangChain CallbackHandler)

| 항목 | 내용 |
|------|------|
| 서버 | **Langfuse OSS v3 self-hosted** (docker compose) — 폐쇄망 내부 배포 가능, MIT 코어 |
| SDK | `langfuse>=4.13,<5` (2026-07 기준 최신 4.13.1, OTel 기반) |
| 통합 방식 | `from langfuse.langchain import CallbackHandler` — LangChain/LangGraph 콜백 1급 지원 |
| langchain 호환 | LangChain v1 지원 공식화(SDK ≥3.8.0, [changelog 2025-10-26](https://langfuse.com/changelog/2025-10-26-langchain-v1-support)). 본 프로젝트는 `langchain>=1.3.9`/`langchain-core>=1.4.7`로 충족. CallbackHandler가 버전 감지용으로 `langchain` 우산 패키지를 요구하는 알려진 이슈([#13651](https://github.com/langfuse/langfuse/issues/13651))가 있으나 본 프로젝트는 이미 설치되어 있어 무영향 |

### 3.2 대안 비교 (기각 사유)

| 대안 | 기각 사유 |
|------|----------|
| LangSmith | SaaS 중심 — 폐쇄망 self-host는 Enterprise 유료. egress 불가 환경 부적합 |
| Arize Phoenix | self-host 가능하나 세션/사용자 뷰·프롬프트 diff 등 LLM 앱 운영 기능이 Langfuse 대비 약함. 사용자가 Langfuse를 명시 지정 |
| OpenTelemetry 직접 계측 | 콜백 자동 수집 없이 40여 지점 수동 span 작성 필요 — 유지비 과다. Langfuse v4 자체가 OTel 기반이므로 추후 OTLP 연계 여지는 남음 |
| 자체 로거 확장 (audit_logger에 LLM 기록 추가) | 수집은 가능하나 시각화(trace 트리·세션 뷰·토큰 집계 대시보드)를 전부 자체 구현해야 함 |

### 3.3 서버 구성 요소 (self-host v3 스택)

```
langfuse-web (UI/API) ─┬─ PostgreSQL  (트랜잭션 데이터)
langfuse-worker        ├─ ClickHouse  (trace/observation 저장)
                       ├─ Redis       (큐/캐시)
                       └─ S3 호환(MinIO) (이벤트/미디어 blob)
```

- 개발: 공식 `docker-compose.yml` 기반으로 `langfuse/docker-compose.yml`을 프로젝트에 추가 (기존 `db/`, `redis/` 디렉토리의 compose 패턴 계승)
- 폐쇄망 운영: 이미지 6종(langfuse/langfuse, langfuse/langfuse-worker, postgres, clickhouse/clickhouse-server, redis, minio/minio)을 `docker save`/`load`로 반입. 기존 Redis(`redis/docker-compose.yml`)와는 **분리 운용**(포트 충돌 회피, 관측 스택 독립성)

### 3.4 SDK wheel 반입 (폐쇄망)

`wheels/README.md` 절차를 따른다. langfuse v4의 전이 의존성(opentelemetry-api/sdk/exporter-otlp-proto-http, httpx, backoff, packaging, wrapt 등)을 포함하여 플랫폼별로 수집:

```bash
pip download "langfuse>=4.13,<5" -d wheels/linux --platform manylinux2014_x86_64 --only-binary=:all: --python-version 3.11
# mac/windows 동일 패턴, wheels/requirements_all.txt 갱신
```

`pyproject.toml`에는 deepagents와 동일하게 **optional-dependencies 그룹**으로 추가:

```toml
[project.optional-dependencies]
observability = [
    "langfuse>=4.13,<5",
]
```

---

## 4. 통합 설계

### 4.1 신규 모듈: `src/infrastructure/observability.py`

관측 클라이언트 생성·핸들러 팩토리를 한 곳에 모은다 (infrastructure 계층 — LLM 클라이언트와 동급. **배치 후 `python scripts/arch_check.py`로 계층 위반 검사** 필수, Known Mistakes 2026-03-23).

```python
"""Langfuse 관측성 어댑터 (Plan 56).

LANGFUSE_ENABLED=false(기본) 또는 SDK 미설치·초기화 실패 시
모든 함수가 None을 반환하여 호출부는 콜백 없이 기존 경로로 동작한다.
"""
_client = None          # 프로세스 싱글턴
_init_failed = False    # 재시도 폭주 방지

def init_langfuse(config: AppConfig) -> None:
    """앱 기동 시 1회 호출. 실패해도 예외를 전파하지 않는다."""
    global _client, _init_failed
    if not config.langfuse.enabled or _init_failed:
        return
    try:
        from langfuse import Langfuse
        _client = Langfuse(
            public_key=config.langfuse.public_key,      # 생성자 명시 전달 —
            secret_key=config.langfuse.secret_key,      # os.environ 의존 금지 (§1.4-5)
            host=config.langfuse.host,
            sample_rate=config.langfuse.sample_rate,
            mask=_mask_sensitive,                        # §4.7
        )
    except Exception as e:
        _init_failed = True
        logger.warning("Langfuse 초기화 실패 — 관측 없이 계속: %s", e)

def create_trace_callbacks(*, trace_name, thread_id, user_id=None, tags=None):
    """요청당 CallbackHandler 목록(0 또는 1개)과 trace 메타데이터를 반환한다."""
    if _client is None:
        return [], {}
    from langfuse.langchain import CallbackHandler
    handler = CallbackHandler()
    metadata = {
        "langfuse_session_id": thread_id,     # 세션 = thread_id
        **({"langfuse_user_id": user_id} if user_id else {}),
        **({"langfuse_tags": tags} if tags else {}),
    }
    return [handler], metadata

def shutdown_langfuse() -> None:
    """서버 lifespan 종료/CLI 종료 시 flush."""
    if _client is not None:
        _client.flush()
        _client.shutdown()
```

주의(실측 원칙): 위는 의사코드다. **구현 시 `inspect.signature(Langfuse.__init__)`로 v4 실제 인자명(`host` vs `base_url`, `sample_rate`, `mask`)을 실측 후 확정**한다 (Known Mistakes 2026-06-17/06-25 — 계획서 의사코드를 실 API 검증 없이 구현 금지).

### 4.2 설정: `LangfuseConfig` (`src/config.py`)

기존 config 클래스 패턴을 그대로 따른다:

```python
class LangfuseConfig(BaseSettings):
    """Langfuse LLM 관측성 설정 (Plan 56 / D-050)."""
    enabled: bool = False
    host: str = "http://localhost:3000"
    public_key: str = ""
    secret_key: str = ""
    sample_rate: float = 1.0          # 0.0~1.0 (성능 이슈 시 하향)
    environment: str = "development"  # Langfuse environment 라벨

    model_config = {"env_prefix": "LANGFUSE_", "env_file": [".env", ".encenv"], "extra": "ignore"}
```

- `AppConfig`에 `langfuse: LangfuseConfig = Field(default_factory=LangfuseConfig)` 추가
- secret_key는 Admin/Auth와 동일하게 `.encenv` 지원
- `.env.example` 추가분 — **주석은 반드시 별도 줄** (Known Mistakes 2026-07-02/07-03):

```
# --- Langfuse LLM 관측성 (Plan 56) ---
# 활성화 여부 (기본 false — 옵트인)
LANGFUSE_ENABLED=false
# self-hosted Langfuse URL
LANGFUSE_HOST=http://localhost:3000
# 프로젝트 API 키 (Langfuse UI에서 발급)
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=
# 트레이스 샘플링 비율 (1.0=전량)
LANGFUSE_SAMPLE_RATE=1.0
```

추가 후 `AppConfig(_env_file='.env.example')` 파싱값 실측 검증(빈 값 키 뒤 주석 오염 여부 확인).

### 4.3 콜백 주입 지점 (호출부 수정 — 핵심)

40여 LLM 호출 지점은 **수정하지 않는다**. 그래프 진입점 4곳에서 config에 callbacks+metadata를 병합한다:

**(1) `/query` 동기 경로** — `src/api/routes/query.py` (`graph.ainvoke` 2곳):

```python
callbacks, lf_meta = create_trace_callbacks(
    trace_name="query", thread_id=thread_id, user_id=current_user_id,
    tags=["api", "sync"],
)
thread_config = {
    "configurable": {"thread_id": thread_id},
    "callbacks": callbacks,
    "metadata": lf_meta,
}
```

**(2) `/query/stream` SSE 경로** — `graph.astream_events(...)` 2곳: 동일 병합. `astream_events`는 config의 callbacks를 그대로 수용하므로 SSE 이벤트 소비 로직(`on_chain_start`/`on_chat_model_stream` 필터)은 무변. **D-009 회귀 검증 필수**(§6).

**(3) CLI** — `src/main.py:run_query()`: `trace_name="cli"`, 종료 전 `shutdown_langfuse()` (flush 미수행 시 단명 프로세스에서 이벤트 유실).

**(4) 알람 워커** — 알람 그래프 invoke 지점: `trace_name="alarm"`, session=알람 배치/사건 ID, `tags=["alarm"]`. 알람은 데몬이므로 handler를 요청(알람)당 생성.

부가 경로(스키마 캐시 설명 생성, 문서 field_mapper 단독 사용)는 그래프 밖 직접 `llm.ainvoke`가 있는 경우에만 L4에서 선별 적용.

**FastAPI lifespan**: `src/api/server.py` lifespan startup에서 `init_langfuse(config)`, shutdown에서 `shutdown_langfuse()`.

### 4.4 콜백 자동 전파 검증 (L1 게이트 — 실측 필수)

노드 내부의 `llm.ainvoke(messages)`는 config를 전달하지 않지만, langchain-core 1.x는 async contextvar(`var_child_runnable_config`)로 부모 config(callbacks 포함)를 자동 전파한다(Python 3.11 asyncio 기준). 이 전제가 깨지면 generation이 trace에 붙지 않는다. **L1 착수 시 최소 재현으로 실측**:

1. 2노드 toy StateGraph + fake `BaseChatModel`로 `ainvoke(config={"callbacks":[수집용 핸들러]})` 실행 → 노드 내부 config 미전달 `llm.ainvoke`에서 `on_chat_model_start`가 핸들러에 도달하는지 확인
2. 도달하지 않는 케이스(동기 `_generate`만 있는 클라이언트를 스레드 실행하는 경로 등)가 발견되면: 해당 노드 시그니처에 `config: RunnableConfig` 파라미터를 추가하고 `llm.ainvoke(messages, config=config)`로 명시 전달 (LangGraph는 노드 함수에 config를 주입해준다) — 이 경우에만 해당 노드 선별 수정
3. **deepagents(트랙 B) 3계층 검증** (§2.6 매핑, 실측 대상을 세분화):
   - **(3a) B-1 제어 평면**: `deep_agent.py:219`의 `agent.ainvoke`(config 미전달)에서 deepagents 내부 vLLM 호출의 `on_chat_model_start`가 부모 핸들러에 도달하는지 — 중첩 CompiledStateGraph 경계의 contextvar 전파 확인
   - **(3b) B-2 도구 내부**: deepagents 도구 실행 노드가 `StructuredTool` 코루틴을 실행할 때(별도 asyncio Task 생성 여부 포함) handler 내부 워커 LLM 호출 이벤트가 전파되는지
   - **(3c) B-3 합성 경로**: `astream_text`가 만드는 부분 config(`{"tags": [...]}`)가 부모 contextvar config의 callbacks와 **병합**되는지 — langchain-core `ensure_config`가 contextvar를 base로 merge하는지 실측. 병합 안 되면 USER_RESPONSE_TAG generation들이 trace에서 전부 누락됨(deep_agent뿐 아니라 output_generator/general_inference도 동일 영향 — **전 경로 공통 게이트**)
   - 미전파 확인 시 대응은 §4.9 참조

검증 결과(전파 O/X 매트릭스)는 본 계획서에 추기하고 D-050 등재 시 근거로 첨부한다.

### 4.5 커스텀 클라이언트 `usage_metadata` 보강 (토큰 계측)

Langfuse는 `AIMessage.usage_metadata`(`{"input_tokens","output_tokens","total_tokens"}`)를 자동 집계한다. 세 클라이언트에 응답 API의 usage 필드를 매핑:

| 클라이언트 | 원천 usage 필드 (실측 필요) | 수정 위치 |
|-----------|---------------------------|----------|
| `FabriXAPIClient` | OpenAI 호환 `response["usage"]["prompt_tokens"/"completion_tokens"]` | `_generate`의 `ChatResult` 조립부 |
| `KBGenAIChat` | KBGenAI 응답 payload의 토큰 필드 — **실 응답 JSON을 덤프해 실측 후 매핑** (없으면 생략, generation은 토큰 없이 기록됨) | `_generate`/`_agenerate`/`_astream` (스트리밍은 마지막 청크에 usage 부착) |
| `LLMAPIClient` | Ollama `prompt_eval_count`/`eval_count` | `_generate` |

구현 형태: `AIMessage(content=..., usage_metadata=UsageMetadata(...))` — langchain-core 1.x의 `usage_metadata` 표준 필드 사용. usage 부재 시 필드 생략(0 하드코딩 금지). vLLM(`ChatOpenAI`)·Gemini는 이미 표준 반환하므로 무수정.

### 4.6 trace 구조 설계 (Langfuse에 보이는 모양)

```
trace: "query" (session=thread_id, user=user_id, tags=[api,sync|stream])
└─ span: LangGraph (그래프 전체)
   ├─ span: context_resolver
   ├─ span: input_parser
   │  └─ generation: KBGenAIChat (의도 파싱 프롬프트/응답/토큰)
   ├─ span: query_generator          ← 재시도 시 반복 등장 = 루프 가시화
   │  └─ generation: ...
   └─ span: output_generator
      └─ generation: KBGenAIChat (astream — 최종 응답)

trace: "query" — 트랙 B(deep_agent) 경로 (§2.6 실측 반영)
└─ span: LangGraph
   ├─ span: context_resolver / input_parser / field_mapper (전처리 공통)
   └─ span: deep_agent
      └─ span: deepagents CompiledStateGraph (중첩 그래프)
         ├─ generation: ChatOpenAI(vLLM) [tags: control-plane]   ← tool-calling 루프 1회차
         ├─ span: tool:query_infra_db (B-2)
         │  ├─ generation: KBGenAIChat — classify_dbs [data-plane]
         │  └─ span: 데이터 평면 파이프라인 (schema_analyzer→query_generator→…)
         │     └─ generation: KBGenAIChat × 노드별 [data-plane]
         ├─ generation: ChatOpenAI(vLLM) [control-plane]         ← 루프 2회차(재계획)
         └─ …
      └─ generation: KBGenAIChat — result_aggregator 합성 (B-3, USER_RESPONSE_TAG, D-062)
```

노드명·태그(USER_RESPONSE_TAG 포함)는 콜백 이벤트에 이미 실려 있으므로 추가 계측 없이 위 계층이 형성된다. 트랙 B에서 vLLM generation의 tool-calling 루프 반복 횟수와 control-plane 토큰 합계가 그대로 보이므로 **Plan 50 D-042(제어 평면 토큰 예산)의 정량 측정 수단**이 된다.

### 4.6.1 제어/데이터 평면 태그 분리 (§4.9와 연동)

deepagents 경로는 제어 평면(vLLM)과 데이터 평면(FabriX)의 토큰을 **분리 집계**해야 Plan 50의 예산 압박을 정량화할 수 있다. 모델명만으로도 구분 가능하나(vLLM 모델 vs FabriX 모델), 조회 편의를 위해 LLM 인스턴스 레벨 태그를 부착한다:

- `create_orchestrator_llm()` 반환 인스턴스에 `tags=["control-plane"]`
- `create_llm()` 반환 인스턴스에 `tags=["data-plane"]`
- 방식: `BaseChatModel`(Runnable)의 `tags` 필드 또는 `.with_config(tags=[...])` — **어느 쪽이 Langfuse generation의 tags로 실리는지 실측 후 확정**(둘 다 안 실리면 metadata로 대체). `.with_config` 래핑 시 `type(llm) is KBGenAIChat` 같은 기존 타입 체크(`result_aggregator.py:424`)가 깨지지 않는지 확인 — 깨지면 생성자/필드 방식만 사용

### 4.7 민감 데이터 마스킹

Key Constraints("민감 데이터 마스킹") 준수를 위해 클라이언트 레벨 `mask` 훅을 사용한다:

- `Langfuse(mask=_mask_sensitive)` — 모든 input/output이 저장 전 이 함수를 통과
- `_mask_sensitive`: 기존 `SecurityConfig`/감사 로그의 마스킹 규칙(비밀번호·키·토큰 패턴)을 재사용. 재귀적으로 dict/list/str을 순회하며 패턴 치환
- `.encenv`의 secret 값 자체(FABRIX_API_KEY 등)가 프롬프트에 포함될 일은 없으나, DB 조회 결과 중 masked_columns 대상이 프롬프트에 실리는 경로(result_organizer)가 있으므로 마스킹 훅은 필수
- v4의 `mask` 인자 시그니처는 구현 시 실측 (§4.1 주의와 동일)

### 4.8 성능·안정성

- SDK는 백그라운드 배치 전송(비동기 큐) — 요청 경로 블로킹 없음. 단 `flush()`는 lifespan shutdown에서만
- 서버 미가용: SDK가 내부 재시도 후 drop — 질의 처리 무영향 (성공 기준 5). L1에서 Langfuse 컨테이너 내린 상태로 `/query` 정상 동작 확인
- 과부하 우려 시 `LANGFUSE_SAMPLE_RATE`로 표본화 (trace 단위 일관 샘플링)
- 알람 워커는 상시 데몬 — handler/trace 객체를 알람 처리 단위로 생성·폐기하여 누적 방지 (Known Mistakes 2026-06-29 데몬 메모리 교훈: 장기 보관 dict/객체 금지)

### 4.9 deepagents(트랙 B) 경로 계측 설계 (2026-07-10 추기)

§2.6의 3계층(B-1/B-2/B-3)을 하나의 trace에 귀속시키기 위한 설계. §4.4-3 실측 결과에 따라 (2)는 조건부다.

**(1) `run_deep_agent`에 RunnableConfig 수용 + 명시 전달 (기본 적용)**

contextvar 전파가 실측에서 확인되더라도, 중첩 CompiledStateGraph 경계는 deepagents 내부 구현(0.6.10)에 의존하므로 **명시 전달을 기본으로 한다** (전파 실패 시 generation 전체가 조용히 누락되는 위험 대비):

```python
async def run_deep_agent(
    state: dict,
    *,
    app_config: AppConfig,
    worker_llm: Optional[BaseChatModel] = None,
    config: Optional[RunnableConfig] = None,   # LangGraph가 주입 (키워드 전용이라 partial과 충돌 없음)
) -> dict:
    ...
    result = await agent.ainvoke(
        {"messages": [...]}, config=config      # 중첩 그래프에 부모 callbacks/metadata 전달
    )
```

- 주의: 기존 주석(`deep_agent.py:193`)은 "파라미터명 config를 피한다"였으나 이는 **app_config를 config로 명명하지 않는다**는 뜻 — 계측을 위해서는 반대로 LangGraph의 config 주입을 **활용**한다. `partial(run_deep_agent, app_config=..., worker_llm=...)`(graph.py:358)은 키워드 바인딩이므로 config 파라미터 추가와 충돌하지 않음. **toy 그래프로 partial+config 주입 동작을 실측 후 적용**
- `_aggregate_with_fabrix` → `result_aggregator`는 같은 노드 컨텍스트 안(await 체인)이므로 contextvar로 커버되나, (3c) 실측에서 병합이 안 되면 `result_aggregator(..., runnable_config=config)` 파라미터를 추가해 `astream_text`까지 내려보낸다 (트랙 A `agent_orchestrator`도 동일 패턴 적용)

**(2) `astream_text` config 병합 보강 (조건부 — §4.4-3c 실측 결과에 따라)**

`ensure_config`가 contextvar를 병합해주면 무수정. 병합이 안 되면 `astream_text(llm, messages, *, tags=None, config=None)`으로 확장하여 호출부가 부모 config를 넘길 수 있게 한다 — tags는 기존처럼 config에 merge (D-009 태그 필터 회귀 금지).

**(3) trace 크기·비용 관리**

- 도구 1회 호출마다 데이터 평면 파이프라인 전체(15+ 노드 span)가 붙는다 — 복합 질의(도구 3~4회 + 재계획 루프)는 trace 1건이 수십 span에 달함. self-hosted ClickHouse라 저장은 감당 가능하나, `LANGFUSE_SAMPLE_RATE` 하향의 1순위 후보 경로임을 운영 문서에 명시
- deepagents 내부 span 이름은 deepagents가 정하는 노드명(agent/tools 등)을 그대로 쓴다 — 커스텀 rename은 하지 않음(유지비)

**(4) 도구 결과 collector와의 관계**

collector(원본 결과 수집, `deepagents_tools.py:159`)는 관측과 무관한 기존 응답 생성 경로 — Langfuse 도입으로 변경하지 않는다. 단 도구 span의 output에는 `_serialize_for_tool`의 **truncate된 텍스트**(제어 평면 노출본)가 실리므로, "vLLM이 실제로 본 것"이 그대로 기록된다는 점이 오히려 디버깅에 정확하다(원본은 최종 generation의 input으로 간접 확인 가능).

---

## 5. 구현 로드맵

### Phase L1 — 코어 경로 계측 (그래프 + API)

| # | 작업 | 산출물 | 검증 |
|---|------|--------|------|
| 1 | 콜백 자동 전파 실측 (§4.4) | 전파 매트릭스 (본 문서 추기) | toy 그래프 테스트 통과 |
| 2 | `langfuse/docker-compose.yml` 작성, 로컬 기동, 프로젝트/키 발급 | compose 파일 + README | UI 접속, 키 발급 |
| 3 | `LangfuseConfig` + `.env.example` (§4.2) | `src/config.py` | `.env.example` 파싱 실측 |
| 4 | `src/infrastructure/observability.py` (§4.1) | 신규 모듈 | `arch_check --ci` exit 0 |
| 5 | 진입점 주입: query.py 4곳 + main.py + server.py lifespan (§4.3) | 수정 diff | E2E: trace 계층 확인 (성공 기준 1) |
| 6 | 멀티턴 session 매핑 확인 | — | 성공 기준 2 |
| 7 | 무회귀 검증 | — | `LANGFUSE_ENABLED=false`로 전체 스위트 + SSE 스트리밍 수동 확인 (성공 기준 4) |

### Phase L2 — 토큰 계측 (커스텀 클라이언트)

| # | 작업 | 검증 |
|---|------|------|
| 1 | FabriX/KBGenAI/Ollama 실 응답의 usage 필드 실측 (JSON 덤프) | 필드 존재 여부 표 |
| 2 | 3개 클라이언트 `usage_metadata` 매핑 (§4.5) | 단위 테스트: mock 응답 → `AIMessage.usage_metadata` 값 단언 |
| 3 | Langfuse generation 토큰 표시 확인 | 성공 기준 3 |
| 4 | (선택) FabriX 모델 단가를 Langfuse Models 설정에 등록 → 비용 집계 | UI 비용 표시 |

### Phase L3 — 부가 경로 + 마스킹

| # | 작업 | 검증 |
|---|------|------|
| 1 | 알람 워커 trace (§4.3-4) | 알람 1건 처리 → trace 확인, 데몬 메모리 안정 |
| 2 | deep_agent 계측 (§4.9): `run_deep_agent` RunnableConfig 수용 + `agent.ainvoke(config=...)` 명시 전달, (조건부) `astream_text`/`result_aggregator` config 파라미터 확장 | 트랙 B trace에 B-1(vLLM 루프 generation)·B-2(도구 내부 워커 generation)·B-3(합성 generation)가 모두 한 trace로 귀속. D-062 합성 경로 USER_RESPONSE_TAG 스트리밍 회귀 없음 |
| 3 | 제어/데이터 평면 태그 분리 (§4.6.1) | Langfuse에서 control-plane vs data-plane 토큰 분리 집계 확인 (Plan 50 D-042 정량화) |
| 4 | 마스킹 훅 (§4.7) | 단위 테스트: 민감 패턴 포함 입출력 → 마스킹 단언 (성공 기준 6) |
| 5 | 서버 다운 내성 테스트 | 성공 기준 5 |

### Phase L4 — 운영 정착

| # | 작업 |
|---|------|
| 1 | 폐쇄망 반입: SDK wheel(`wheels/`) + 도커 이미지 6종 절차 문서화 |
| 2 | 대시보드 구성: 노드별 p95 지연, 일별 토큰, 오류율, 재시도율 |
| 3 | 스키마 캐시·문서 경로 선별 계측 |
| 4 | (선택) 감사 로그에 trace_id 상호 참조 기록 — audit JSONL ↔ Langfuse trace 연결 |
| 5 | (선택) Plan 55 멀티소스 관측 로드맵과의 통합 지점 정리 |

### 예상 수정 파일 총괄

| 파일 | 변경 | Phase |
|------|------|-------|
| `src/infrastructure/observability.py` | **신규** | L1 |
| `src/config.py` | LangfuseConfig 추가 | L1 |
| `src/api/routes/query.py` | 진입점 4곳 config 병합 | L1 |
| `src/api/server.py` | lifespan init/shutdown | L1 |
| `src/main.py` | CLI init/shutdown + config 병합 | L1 |
| `langfuse/docker-compose.yml` | **신규** | L1 |
| `.env.example`, `pyproject.toml`, `requirements.txt` | 설정·의존성 | L1 |
| `src/clients/fabrix_client.py`, `fabrix_kbgenai.py`, `ollama_client.py` | usage_metadata | L2 |
| `src/orchestration/deep_agent.py` | `run_deep_agent` RunnableConfig 수용 + 중첩 ainvoke 명시 전달 (§4.9-1) | L3 |
| `src/llm.py` | 평면 태그 부착 (§4.6.1) + (조건부) `astream_text` config 파라미터 (§4.9-2) | L3 |
| 알람 그래프 invoke 지점 | config 전달 | L3 |
| (조건부) `src/orchestration/result_aggregator.py`, `agent_orchestrator.py` | config 하향 전달 — §4.4-3c 실측 결과에 따라 | L3 |
| (조건부) 전파 안 되는 개별 노드 | config 명시 전달 | L1 실측 결과에 따라 |

---

## 6. 테스트 계획

1. **단위 — observability 모듈**: enabled=false → 빈 콜백 반환 / SDK 미설치(import 실패 mock) → 안전 폴백 / init 실패 후 재호출 시 재시도 폭주 없음
2. **단위 — usage_metadata**: 클라이언트별 mock 응답 → 토큰 값 단언. usage 부재 응답 → 필드 없음(예외 없음)
3. **단위 — 마스킹**: 민감 패턴 입출력 → 치환 확인
4. **통합 — 콜백 전파**: fake LLM + 실제 `build_graph`로 `ainvoke(config={"callbacks":[수집 핸들러]})` → 주요 노드의 chat_model 이벤트 수집 단언 (Langfuse 서버 불필요 — 수집용 BaseCallbackHandler 사용)
5. **회귀 — D-009 스트리밍**: callbacks 주입 상태에서 `astream_events`의 `on_chat_model_stream`+USER_RESPONSE_TAG 이벤트가 기존과 동일하게 발생하는지 단언 (기존 스트리밍 테스트에 callbacks 케이스 추가). **deep_agent 경로 포함**: `result_aggregator(synthesize=True)`의 합성 generation 태그(D-062)도 동일 단언
6. **통합 — deepagents 3계층 귀속 (§2.6/§4.9)**: fake 오케스트레이터(tool-calling 1회 후 종료하는 fake `BaseChatModel`) + fake 워커로 `run_deep_agent`를 callbacks 포함 config로 실행 → 수집 핸들러에 (a) 오케스트레이터 chat_model 이벤트 (b) 도구 내부 워커 이벤트 (c) 합성 경로 이벤트가 모두 도달하는지 단언 (deepagents 미설치 환경에서는 skip 마킹 — 기존 트랙 B 테스트의 skip 패턴 계승)
7. **회귀 — 전체 스위트**: `LANGFUSE_ENABLED=false` 기본값으로 전체 통과. 테스트 픽스처에서 `LangfuseConfig(enabled=False)` **명시**하여 로컬 `.env` 누수 차단 (Known Mistakes 2026-06-17 — BaseSettings 테스트 누수)
8. **수동 E2E**: 로컬 Langfuse 기동 → `/query/stream` 1회 → UI에서 성공 기준 1·2·3 확인, 스크린샷을 `docs/`에 보관. 트랙 B 활성 환경에서는 §4.6 deep_agent trace 계층도 함께 확인

---

## 7. 운영 및 모니터링

- **핵심 지표** (Langfuse 대시보드): ① trace 수/일 ② 노드별 generation p50/p95 지연 ③ 일별 입력/출력 토큰(제어 평면 vs 데이터 평면 분리 — vLLM/FabriX 모델별) ④ 오류율(generation error) ⑤ 재시도율(query_generator span 반복 횟수)
- **응답 시간 목표 추적**: 단순<10s/복합<30s 위반 trace를 tags/duration 필터로 조회
- **보존 정책**: ClickHouse 디스크 사용량 모니터링, 필요 시 Langfuse retention 설정(프로젝트 단위)으로 오래된 trace 정리
- **감사 로그와의 관계**: 감사 로그(JSONL)는 규제·보안 목적의 **권위 기록**으로 유지. Langfuse는 운영·디버깅 뷰 — 대체가 아니라 보완 (L4-4의 trace_id 상호 참조로 연결)

## 8. 리스크 및 완화

| 리스크 | 영향 | 완화 |
|--------|------|------|
| 콜백 자동 전파가 일부 경로에서 안 됨 | generation 누락 | L1 게이트 실측(§4.4) 후 해당 노드만 config 명시 전달 |
| KBGenAI API가 usage를 반환하지 않음 | FabriX 토큰 미표시 | 필드 생략(부분 성공). 필요 시 추후 tokenizer 근사치는 별도 결정으로 |
| CallbackHandler 오버헤드로 SSE 지연 | UX 저하 | 배치 비동기 전송 + sample_rate 하향. L1에서 스트리밍 체감·소요 비교 |
| Langfuse 서버 리소스(ClickHouse) 부담 | 인프라 비용 | sample_rate·retention 조정. 관측 스택은 서비스와 별도 호스트 배치 가능 |
| langfuse v4 API 표면이 문서와 상이 | 구현 지연 | 모든 SDK 호출부는 `inspect.signature` 실측 후 확정(§4.1) — Known Mistakes 원칙 |
| deepagents 중첩 그래프 경계에서 콜백 미전파 (0.6.10 내부 구현 의존) | 트랙 B generation 전체 누락 | §4.9-1 명시 전달을 기본 적용 — contextvar 전파에만 의존하지 않음. deepagents 버전 업 시 §4.4-3 재실측 |
| `astream_text`의 부분 config가 부모 callbacks를 병합하지 못함 | USER_RESPONSE_TAG generation 누락 (deep_agent 합성 + output_generator 공통) | §4.4-3c 실측 게이트 — 미병합 시 §4.9-2 config 파라미터 확장 |
| 트랙 B 복합 질의 trace가 과대 (도구별 파이프라인 전체 span) | 저장·UI 부담 | §4.9-3 — sample_rate 하향 1순위 경로로 운영 문서에 명시 |
| 프롬프트 내 민감 데이터 저장 | 보안 | mask 훅 필수(L3), self-hosted라 데이터 외부 반출 없음 |

## 9. 의사결정 등재 (예정)

구현 착수 승인 시 `docs/02_decision.md`에 신규 결정 등재:

- **D-050(예정) — Langfuse 기반 LLM 관측성 도입**: self-hosted Langfuse v3 + Python SDK v4 CallbackHandler, 진입점 1회 주입 + contextvar 자동 전파, 옵트인(`LANGFUSE_ENABLED`), 커스텀 클라이언트 usage_metadata 보강. 대안 기각: LangSmith(폐쇄망 self-host 유료), Phoenix(운영 기능 열세), 수동 OTel(유지비)
- 등재 직전 **`grep -oE "D-0[0-9]{2}" docs/02_decision.md`로 본문 헤더+변경 이력 표 전체에서 실제 최대 번호를 재확인**하고 충돌 시 다음 빈 번호 사용 (Known Mistakes 2026-06-25/06-29)

## 10. 참고 자료

- Langfuse LangChain 통합: https://langfuse.com/integrations/frameworks/langchain
- LangChain v1 지원 공지: https://langfuse.com/changelog/2025-10-26-langchain-v1-support
- Python SDK v3→v4 마이그레이션: https://langfuse.com/docs/observability/sdk/upgrade-path/python-v3-to-v4
- Self-hosting 가이드: https://langfuse.com/self-hosting
- 사전 조사 원본: 본 계획 §2 (2026-07-08 코드베이스 전수 조사)
