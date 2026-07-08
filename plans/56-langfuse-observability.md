# 56. LLM 관측성 확보 — Langfuse 통합 (LLM Observability with Langfuse)

> 작성일: 2026-07-08
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
| 〃 | `deep_agent.py:212` (`run_deep_agent`) | deepagents `CompiledStateGraph.ainvoke` — 내부에서 vLLM tool-calling + 도구 내 FabriX 워커 호출 | 중첩 그래프 |
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
3. `run_deep_agent`(중첩 CompiledStateGraph)도 동일 검증: `deep_agent.py:212`의 내부 `ainvoke`에 부모 config 전달 여부 확인, 미전파 시 명시 전달

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
   ├─ span: deep_agent               (트랙 B 시)
   │  ├─ generation: ChatOpenAI(vLLM tool-calling — 제어 평면 토큰 측정, Plan 50 D-042 연계)
   │  └─ span: tool:query_executor
   │     └─ generation: KBGenAIChat(워커)
   └─ span: output_generator
      └─ generation: KBGenAIChat (astream — 최종 응답)
```

노드명·태그(USER_RESPONSE_TAG 포함)는 콜백 이벤트에 이미 실려 있으므로 추가 계측 없이 위 계층이 형성된다.

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
| 2 | deep_agent 중첩 전파 (§4.4-3) | 트랙 B 경로 trace에 도구 span 포함 |
| 3 | 마스킹 훅 (§4.7) | 단위 테스트: 민감 패턴 포함 입출력 → 마스킹 단언 (성공 기준 6) |
| 4 | 서버 다운 내성 테스트 | 성공 기준 5 |

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
| 알람 그래프 invoke 지점, `src/orchestration/deep_agent.py` | config 전달 | L3 |
| (조건부) 전파 안 되는 개별 노드 | config 명시 전달 | L1 실측 결과에 따라 |

---

## 6. 테스트 계획

1. **단위 — observability 모듈**: enabled=false → 빈 콜백 반환 / SDK 미설치(import 실패 mock) → 안전 폴백 / init 실패 후 재호출 시 재시도 폭주 없음
2. **단위 — usage_metadata**: 클라이언트별 mock 응답 → 토큰 값 단언. usage 부재 응답 → 필드 없음(예외 없음)
3. **단위 — 마스킹**: 민감 패턴 입출력 → 치환 확인
4. **통합 — 콜백 전파**: fake LLM + 실제 `build_graph`로 `ainvoke(config={"callbacks":[수집 핸들러]})` → 주요 노드의 chat_model 이벤트 수집 단언 (Langfuse 서버 불필요 — 수집용 BaseCallbackHandler 사용)
5. **회귀 — D-009 스트리밍**: callbacks 주입 상태에서 `astream_events`의 `on_chat_model_stream`+USER_RESPONSE_TAG 이벤트가 기존과 동일하게 발생하는지 단언 (기존 스트리밍 테스트에 callbacks 케이스 추가)
6. **회귀 — 전체 스위트**: `LANGFUSE_ENABLED=false` 기본값으로 전체 통과. 테스트 픽스처에서 `LangfuseConfig(enabled=False)` **명시**하여 로컬 `.env` 누수 차단 (Known Mistakes 2026-06-17 — BaseSettings 테스트 누수)
7. **수동 E2E**: 로컬 Langfuse 기동 → `/query/stream` 1회 → UI에서 성공 기준 1·2·3 확인, 스크린샷을 `docs/`에 보관

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
