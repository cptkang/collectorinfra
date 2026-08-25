# 76. 실행 관측 로깅 — 실행 SQL 파일 로그 + 실패 요청 단계 트레이스 (설계·구현 정리)

> 작성일: 2026-08-25
> **성격**: **사후 정리 문서**. 신규 계획이 아니라, 이미 랜딩된 기능(2026-08-19)의 **설계와 구현을
> 한 곳에 모은 참조 문서**다. 코드 변경 0건.
> **상태**: **구현 완료 (2026-08-19 랜딩 · D-140/D-141 등재)** — 본 문서는 실행 코드 실측(2026-08-25) 대조본.
> **관련 결정**: **D-140**(실행 SQL 파일 로그 `logs/` 단일 루트 통합), **D-141**(실패 요청 단계 트레이스 +
> 4단 로그 레벨 규약), D-162(사다리 관측 — 트레이스 헤더의 `ladder` 필드), D-139(패키지 경계),
> D-083(로그 로테이션 배선 누락 선례), D-027·D-034(감사 로깅)
> **원천 문서**: `SPEC-ops-logging-and-synonym-set.md`(요건·설계) · `tasks/plan.md`(구현 계획·결과) ·
> `tasks/todo.md`(태스크 T0~T10) · `docs/02_decision.md` D-140~D-142
> **범위**: 같은 스펙에 묶여 있던 **모듈 C(`synonym-set`, D-142)는 로깅과 코드 접점이 0이라 본 문서 범위 밖**이다
> (§8 포인터만 남긴다). 본 문서는 **모듈 A(`sql-file-log`) + 모듈 B(`failure-trace`)**를 다룬다.

---

## 1. 배경 — 무엇이 없었나

### 1.1 요건 원문 (사용자, 2026-08-19)

1. 에이전트에서 동작하는 **모든 SQL은 로그 파일로 생성**하여 `logs` 폴더에 저장하라.
2. 에이전트 동작 시 **정상적인 응답을 제공하지 못할 경우 전체 프로세스 단계별로 로그를 생성**하라.
   이 로그를 기반으로 **원인을 파악할 수 있도록 로그 수준을 정의**하라.

### 1.2 착수 시점 실측 갭

| 요건 | 당시 구현 | 갭 |
|---|---|---|
| ① SQL 파일 로그 | `src/utils/sql_file_logger.py` → `sqls/act/YYYY-MM-DD.sql`. 호출부 8곳(`src/db/client.py` 5 · `src/dbhub/client.py` 3) | 저장 위치가 `logs/`가 아니고, 로그 산출물이 두 루트로 갈림. `mcp_server/`(별도 venv·프로세스) 미커버 |
| ② 단계별 실패 로그 | `setup_logging()`이 structlog **stdout 전용**(`PrintLoggerFactory`). `logs/`에는 `audit-*.jsonl`·`alarm_decisions.jsonl`뿐 | **노드별 단계 트레이스를 파일로 남기는 경로가 0건**. 앱 로그는 프로세스 종료 시 소실. 로그 레벨은 `log_level` 단일 필드뿐이고 진단 목적 규약 없음 |

> 요건 ②는 CLAUDE.md 「0건/실패 진단은 안쪽 단계부터 추정 수정하지 말고 **진입·게이트별 로그로 끊긴
> 지점부터 확정**」의 실행 수단이다. 그때까지 그 원칙을 지킬 **데이터가 없었다**는 것이 착수 근거다.

---

## 2. 설계

### 2.1 설계 원칙 4가지

| # | 원칙 | 이유 |
|---|---|---|
| P1 | **수집은 상시, 파일 쓰기는 실패 시에만** | 정상 경로 디스크 비용 0. 실패는 사후에만 알 수 있으므로 "실패하면 그때 켠다"는 성립하지 않는다 |
| P2 | **배선은 한 점에서** (`StateGraph` 프록시) | 진입점 6곳·`add_node` 20여 곳·상당수 플래그 조건부 → 개별 배선은 「단일/멀티 경로 비대칭」을 그대로 재현한다 |
| P3 | **레벨 4단 + `reason` 강제** | 문자열 메시지로 사후 분류하면 문구 한 줄 바뀔 때 집계가 깨진다 |
| P4 | **관측이 앱을 깨지 않는다** | 수집·기록 경로는 예외를 삼키고 `debug`만 남긴다. `OBS_TRACE_ENABLED=false`면 원본 함수를 그대로 등록해 **비트동일** |

### 2.2 전체 흐름

```
HTTP 요청
   │
   ├─ AuditMiddleware.dispatch                       (src/api/middleware/audit_middleware.py:56)
   │     start_request(request_id, max_steps=…)      → 요청 스코프 링버퍼 생성
   │
   ├─ build_graph()  graph = TracedGraph(StateGraph(AgentState), enabled=trace_enabled)
   │                                                  (src/graph.py:358-361)
   │     └─ add_node(name, fn) → add_node(name, traced(fn, name=name))   ← 20여 노드 자동 편입
   │
   ├─ 노드 실행 (context_resolver → … → output_generator)
   │     traced 래퍼가 노드마다:
   │        observe_state(진입 state)   ← 실패 신호 축약 관찰
   │        record_step(node.enter, INFO)
   │        [원본 노드 실행]
   │        record_step(node.exit, INFO, payload=델타 요약)
   │        예외 시 record_step(node.exception, ERROR, reason=예외타입) 후 그대로 re-raise
   │
   └─ finally: flush_if_failed(request_id)            (audit_middleware.py:65-67)
         failure_triggers(관찰 신호) → severity
             None  → 파일 없음 · 버퍼만 해제      ← 정상 경로
             warn/error → logs/trace/YYYY-MM-DD/<request_id>.jsonl (0600)
```

CLI(`python -m src.main`)는 미들웨어를 지나지 않으므로 `src/main.py:62-75`에서 같은 수명주기를 직접 관리한다.

### 2.3 로그 레벨 규약 (요건 ②의 "로그 수준 정의")

`src/observability/levels.py` — `TraceLevel` enum. `rank`로 비교하고, `requires_reason`이 True인
레벨은 `TraceStep.__post_init__`이 **생성 시점에 `ValueError`로 막는다**.

| 레벨 | 의미 | 판정 기준 | 예시 이벤트 |
|---|---|---|---|
| `ERROR` | 요청이 최종 실패. 사용자에게 결과를 못 줌 | 예외 전파, `error_response` 도달, 산출물 생성 실패 | `node.exception`, `graph.error_response`, `output.generation_failed` |
| `WARN` | 응답은 나갔으나 **열화**됨 | `retry_count > 0`, 결과 0건, 폴백 강등, 매핑 미해결 | `query.zero_rows`, `generator.retry`, `mapping.unresolved` |
| `INFO` | 정상 경로의 의사결정 지점 | 노드 진입/이탈, 라우팅 판정, 게이트 통과 | `node.enter`, `node.exit`, `router.intent`, `sql.executed` |
| `DEBUG` | 진단 시에만 필요한 상세 | 프롬프트·LLM 응답 원문, 스키마 상세 | `llm.prompt`, `llm.raw_response`, `schema.detail` |

- **수집 레벨은 콘솔 출력 레벨(`AppConfig.log_level`)과 독립**이다. 콘솔이 INFO여도 버퍼는 DEBUG까지 담는다.
- `ERROR`·`WARN`은 구조화 `reason`을 **반드시** 동반한다(P3).

### 2.4 실패 판정 — 4기준 (사용자 확정)

`failure_triggers(state)` → `list[FailureTrigger]`, `severity_for(triggers)` → `"error" | "warn" | None`.
복수 해당 시 **전부 `triggers`에 기록하고 최고 severity를 채택**한다.

| 트리거 | 실제 판정 조건 (`levels.py`) | severity |
|---|---|---|
| `exception` | `error_message` 설정됨 **또는** `current_node == "error_response"` | `error` |
| `output_failed` | `file_type` 요청됐는데 `output_file` 없음 **또는** `smq_derivation[*].unresolved` 존재 | `error` |
| `zero_rows` | `routing_intent == "data_query"` **이고** `query_results == []` | `warn` |
| `retry` | `retry_count > 0` (최종 성공했어도 원인 추적 가치가 있다) | `warn` |

`zero_rows`를 `data_query`로 한정한 이유: 캐시 관리·일반 추론 의도는 원래 결과 행이 없다 — 한정하지
않으면 정상 요청이 전부 실패로 덤프된다.

### 2.5 배선 — `TracedGraph` 프록시 (핵심 설계 판단)

```python
graph = TracedGraph(                                   # src/graph.py:358
    StateGraph(AgentState),
    enabled=bool(getattr(getattr(config, "observability", None), "trace_enabled", False)),
)
```

- `add_node`만 가로채 `traced(action, name=…)`으로 감싸고, **나머지 전부(`add_edge`·
  `add_conditional_edges`·`set_entry_point`·`compile`…)는 `__getattr__`로 원본에 위임**한다.
- 효과: `use_deep_agent`·`enable_semantic_routing`·`fault_diagnosis_enabled` 등 **조건부 노드와 신규
  노드가 자동 편입**되고, 노드 파일 수정은 0건.
- `add_node(action)`(함수명이 노드명) 단일 인자 형태도 분기 처리한다.
- 래핑 실패 시 경고 1건 남기고 **원본을 등록**한다 — 관측을 포기할지언정 그래프 빌드를 막지 않는다.
- **유일한 High 리스크**(프록시가 LangGraph 내부를 깨면 그래프 전체 불능)는 위임 계약 테스트로 고정
  (`tests/test_observability/test_graph_proxy.py`).

### 2.6 flush 배선이 5곳 → 1곳으로 수렴한 경위 (계획 대비 설계 정정)

초안은 그래프 실행 진입점마다 `flush_if_failed(state)`를 배선하는 것이었다. 그러나 진입점 4곳의 종료
구조가 제각각(SSE generator는 `return`이 여럿)이라 **누락 위험이 컸다**.

→ `traced`가 **노드 진입 state와 반환 델타에서 실패 신호를 관찰**(`observe_state`)하도록 바꾸니
호출부가 최종 state를 몰라도 되어, HTTP 경로는 **모든 요청이 지나는 `AuditMiddleware` 한 곳**으로
수렴했다(CLI만 별도 1곳). 계획의 "대칭 확보" 목적을 더 강하게 달성한다.

관찰 신호는 **축약해서** 담는다(`_shrink_signals`) — `query_results`는 "비었는가"만, `output_file`은
존재 여부(bool)만 남겨, 큰 결과셋·바이너리가 버퍼로 복사되지 않는다.

### 2.7 출력 포맷 — `logs/trace/YYYY-MM-DD/<request_id>.jsonl`

**첫 줄 = 요약 헤더**

```json
{"kind":"summary","ts":"2026-08-19T14:03:11.482+09:00","request_id":"a1b2c3d4","thread_id":"sess-88",
 "user_query":"…","severity":"error","triggers":["exception","retry"],"total_ms":12043.0,
 "node_path":["context_resolver","input_parser","query_generator","query_validator","query_generator","query_executor"],
 "step_count":11,"ladder":{…}}
```

**이후 한 줄 = 한 단계**

```json
{"ts":"…","request_id":"a1b2c3d4","thread_id":"sess-88","step":5,"node":"query_executor",
 "level":"ERROR","event":"node.exception","elapsed_ms":842.3,"reason":"Db2SqlError",
 "payload":{"db_id":"polestar_b0","sql_hash":"9f3c…","error":"SQLCODE=-206"}}
```

- `node_path`는 **연속 중복만 접는다** — 재시도로 같은 노드에 다시 오면 사이에 다른 노드가 끼므로
  그대로 남아, **루프 횟수가 눈에 보인다**(`query_generator → query_validator → query_generator`).
- `ladder`(D-162): 어느 폴백 사다리 단에서 난 실패인지. 단이 다르면 노드 구성이 달라, 이 값 없이는
  `node_path`를 해석할 기준이 없다.
- SQL 원문은 담지 않는다 — `sql`·`generated_sql` 키는 **값을 sha256 앞 16자로 바꾸고 키 이름도
  `sql_hash`로 바꾼다**(값만 해시로 두면 `sql` 키가 남아 원문처럼 읽힌다). 원문은 `logs/sql/`에서 찾는다.

### 2.8 안전장치

| 위험 | 조치 | 위치 |
|---|---|---|
| 민감정보 유출 | 3단 마스킹 — ①URL 자격증명(`scheme://user:pw@host`의 pw만) ②문장 내 토큰(OpenAI `sk-`·JWT·AWS `AKIA`·GitHub `ghp_`·GitLab `glpat-`·bcrypt) ③값 전체가 시크릿 형태면 통째로. 키 이름 기반(`password|token|secret|api_key…`) 마스킹 별도 | `trace_writer._mask_text`·`_sanitize` |
| 중첩 payload 비대칭 | `_sanitize` ↔ `_sanitize_payload` 상호 재귀 — **깊이와 무관하게 동일 규칙** | `trace_writer` |
| ReDoS (외부 입력 `user_query`가 직접 닿음) | 모든 수량자에 상한 + `"://"` 사전 검사 + **마스킹 전 길이 절단**(`_MAX_TEXT_LEN=2000`, 절단 표시 남김) | `trace_writer` |
| 경로 조작 (`request_id`가 파일명) | 화이트리스트 `^[A-Za-z0-9._-]{1,128}$` + `.`/`..` 배제. 위반 시 원본을 로그에 싣지 않고 길이만 남긴다 | `trace_writer._is_safe_request_id` |
| 파일 권한 창(race) | `os.open(..., 0o600)`으로 **생성 시점에** 권한 부여(open 후 chmod 금지) | `trace_writer._write_atomic` |
| 메모리 무한 증가 | **2방향 bound** — 요청당 단계 `maxlen=trace_max_steps`(기본 200) + 동시 요청 키 `_MAX_ACTIVE_REQUESTS=64`(초과 시 오래된 것부터 축출). 단계 번호는 밀려나도 단조 증가 | `trace_collector` |
| 관측이 요청을 깨뜨림 | 수집·기록 전 경로 `try/except` + `debug`. 덤프 실패만 `warning`(침묵 금지). `finally`에서 항상 `end_request` | 전 모듈 |
| 추적 대상 아닌 호출 | `request_id` 없음/버퍼 없음이면 **계측 없이 원본 실행**(오버헤드 0) | `trace_collector.traced` |

### 2.9 모듈 A — 실행 SQL 파일 로그 (D-140)

| 항목 | 내용 |
|---|---|
| 경로 이전 | `sqls/act/YYYY-MM-DD.sql` → **`logs/sql/YYYY-MM-DD.sql`**. 레코드 포맷(타임스탬프·호출위치·DB·소요·행수·에러 헤더 + SQL) **불변**, 과거 파일은 삭제하지 않고 신규 기록만 이전 |
| 범위 | 사용자 질의 처리 경로 + 관측 DB 질의. 앱 자체 운영 SQL(users/audit_logs DDL·CRUD, 부팅 DDL, `*_repository.py`)은 **대상 외**(감사 로그에 이미 기록됨) |
| `mcp_server` 편입 | 별도 venv·프로세스라 `src.utils` import 불가(D-139) → `mcp_server/mcp_server/sql_log.py` 미니 로거 신설, **같은 `logs/sql/`에 append**. 코드 중복은 경계 유지의 대가로 의도적 수용. 레코드는 **한 번의 `write()`**로 기록(`O_APPEND` 원자성) + `_SOURCE_TAG="mcp_server"`로 출처 구분 |
| `noise_gate` | 조치 불필요 — 자체 실행기 없이 `DBRegistry.get_client(db_id).execute_sql()`로 기존 클라이언트를 재사용해 **이미 커버** |
| 보존 정리 | `src/utils/log_retention.py`(신규) — `logs/sql/`·`logs/trace/`를 날짜 기반 삭제. 이름이 `YYYY-MM-DD` **정확 일치**가 아니면 건드리지 않는다(오삭제 방지) |
| 감사 정리와 **분리**한 이유 | 감사 정리는 `audit_repo`(DB)가 있을 때만 도는데 파일 로그는 DB 없이도 쌓인다 → 같은 루프에 얹으면 **무인증 구성에서 영영 정리되지 않는다** |

### 2.10 설정 — `ObservabilityConfig` (`src/config.py:539`, `env_prefix="OBS_"`)

| env 키 | 타입 | 기본값 | 용도 |
|---|---|---|---|
| `OBS_SQL_LOG_ENABLED` | bool | `true` | 실행 SQL 파일 기록 on/off |
| `OBS_SQL_LOG_RETENTION_DAYS` | int | `30` | SQL 로그 보존 일수 (0 이하면 정리 비활성) |
| `OBS_TRACE_ENABLED` | bool | `true` | 실패 트레이스 수집·덤프 on/off (off면 프록시 no-op — 비트동일) |
| `OBS_TRACE_RETENTION_DAYS` | int | `14` | 트레이스 보존 일수 (실패 건만 쌓이므로 SQL보다 짧게) |
| `OBS_TRACE_MAX_STEPS` | int | `200` | 요청당 링버퍼 단계 상한 (노드 20 × 재시도 3 + 여유) |

설정 5개를 여러 태스크에 흩지 않고 **한 태스크에서 그룹 1개로** 추가했다 — D-129 설정 카탈로그가
그룹 수·필드 수를 정확히 단언하므로(`tests/test_api/test_settings_catalog.py`), 나눠 넣으면 그 단언을
매번 고쳐야 한다. 결과: 그룹 18→19, 필드 243→251(신규 5 + 사전 누락분 3 보정).

---

## 3. 구현

### 3.1 파일

| 구분 | 파일 | 역할 |
|---|---|---|
| 신규 | `src/observability/levels.py` | `TraceLevel`(4단·`requires_reason`) · `FailureTrigger`(4기준) · `failure_triggers()` · `severity_for()` |
| 신규 | `src/observability/trace_collector.py` | 요청 스코프 링버퍼 · `start_request`/`record_step`/`end_request` · `observe_state`(신호 축약) · **`traced` 래퍼** |
| 신규 | `src/observability/trace_writer.py` | 실패 판정 → JSONL 덤프 · 마스킹 3단 · SQL 해시 · 0600 원자 생성 · `flush_if_failed()` |
| 신규 | `src/observability/graph_proxy.py` | `TracedGraph` — `add_node`만 가로채고 나머지 위임 |
| 신규 | `src/utils/log_retention.py` | `logs/sql/`·`logs/trace/` 날짜 기반 보존 정리 (`cleanup_file_logs`) |
| 신규 | `mcp_server/mcp_server/sql_log.py` | 별도 프로세스용 미니 SQL 로거 (동일 `logs/sql/`에 append) |
| 변경 | `src/graph.py` | **1줄** — `StateGraph`를 `TracedGraph`로 감쌈 (358-361) |
| 변경 | `src/api/middleware/audit_middleware.py` | `start_request` / `finally: flush_if_failed` (56-67) |
| 변경 | `src/main.py` | CLI 트레이스 수명주기 + SQL 로거 초기화 (41-45, 62-75) |
| 변경 | `src/api/server.py` | SQL 로거 초기화(276) · 기동 시 정리 1회(281) · 주기 정리 루프(185-201) |
| 변경 | `src/utils/sql_file_logger.py` | `_SQL_ACT_DIR` → `_SQL_LOG_DIR`(`logs/sql/`) + `enabled` 게이트 |
| 변경 | `src/config.py` · `src/api/settings_catalog.py` · `.env.example` | `ObservabilityConfig` 5필드 (§2.10) |
| 변경 | `scripts/arch_check.py` | `src/observability`를 **infrastructure** 계층으로 등록 |

### 3.2 배선 지점 (실측 grep — D-083 재발 방지)

| 배선 | 위치 | 확인 |
|---|---|---|
| 노드 트레이싱 | `src/graph.py:358` | `TracedGraph(StateGraph(AgentState), enabled=…)` 1곳 — 이후 `add_node` 전부 자동 |
| HTTP 수집 시작·덤프 | `src/api/middleware/audit_middleware.py:56-67` | 4개 그래프 진입점이 모두 이 미들웨어를 지남 |
| CLI 수집 시작·덤프 | `src/main.py:62-75` | `try/finally` |
| SQL 로거 초기화 | `src/main.py:41-42` · `src/api/server.py:276-277` | 두 프로세스 대칭 |
| 보존 정리 호출부 | `src/api/server.py:201`(주기 루프) · `:281`(기동 1회) | **2곳 grep 실측** — D-083("구현·설정은 있으나 호출부 0건이라 무효") 재발 방지 조건 충족 |

### 3.3 테스트 (2026-08-25 실측)

| 파일 | 건수 | 대상 |
|---|---|---|
| `tests/test_observability/test_levels.py` | 20 | 레벨 순위·`reason` 강제·4기준 판정·severity 최댓값 |
| `tests/test_observability/test_collector.py` | 16 | 링버퍼 bound(단계·키)·신호 축약·단조 증가 단계 번호 |
| `tests/test_observability/test_writer.py` | 34 | 마스킹 5종·중첩 payload·SQL 해시·0600·경로 조작 차단 |
| `tests/test_observability/test_graph_proxy.py` | 11 | **위임 계약**(`compile`/`add_edge`/`add_conditional_edges`/`set_entry_point`) |
| `tests/test_observability/test_trace_contract.py` | 14 | JSONL 스키마 고정 (필드명·타입) — 로그 소비자 보호 |
| `tests/test_observability/test_wiring_parity.py` | 8 | 조건부·신규 노드 자동 편입(경로 대칭) |
| `tests/test_observability/test_entrypoint_activation.py` | 7 | HTTP·CLI 진입점에서 실제로 발동하는지 |
| `tests/test_observability/test_perf_budget.py` | 5 | 요청당 **5ms·256KB 미만** 예산 단언 |
| `tests/test_utils/test_log_retention.py` | 14 | 날짜 정확 일치 삭제·`_as_days` 타입 화이트리스트 |
| `tests/test_utils/test_sql_file_logger.py` | 10 | 경로·레코드 포맷·`enabled` 게이트 |
| `mcp_server/tests/test_sql_log.py` | 8 | 별도 프로세스 append·동시 쓰기 |
| **합계** | **147** | (같은 디렉토리의 `test_ladder_*`·`test_tristate_warning` 34건은 **D-162 별건**) |

> 랜딩 당시 기록은 `docs/02_decision.md` D-141(신규 98건 → TDD 보강 후 → 코드리뷰 보강 156건) 참조.
> 위 표는 **현재 코드 기준 재실측치**이며, 이후 보강분이 반영돼 있다.

---

## 4. 운영 — 실패한 요청 원인 추적 절차

```bash
# 1. 오늘 실패한 요청 목록 (요약 헤더만 훑기)
for f in logs/trace/$(date +%F)/*.jsonl; do head -1 "$f" | \
  python -c 'import json,sys; d=json.load(sys.stdin); print(d["severity"], d["triggers"], d["request_id"], d["user_query"][:40])'; done

# 2. 특정 요청의 단계 흐름 — 어디서 끊겼는지
head -1 logs/trace/2026-08-19/a1b2c3d4.jsonl | python -m json.tool   # node_path·total_ms·ladder
grep '"level":"ERROR"' logs/trace/2026-08-19/a1b2c3d4.jsonl          # 끊긴 지점 + reason + payload.error

# 3. 그 단계가 실행한 SQL 원문 — 트레이스의 sql_hash로 대조
grep -n "<sql_hash 앞자리>" logs/sql/2026-08-19.sql   # 없으면 헤더 타임스탬프로 근방 탐색
```

판정 순서: `severity`/`triggers`로 **성격**을 정하고 → `node_path`로 **어디까지 갔는지** 보고 →
마지막 `ERROR`/`WARN` 단계의 `reason`·`payload`로 **원인**을 확정한다. `retry`가 섞여 있으면
`node_path`에 `query_generator`가 반복 등장하므로 **루프 횟수**가 그대로 읽힌다.

**정상 요청은 파일이 생기지 않는다** — `logs/trace/`가 비어 있으면 "로그가 안 남았다"가 아니라
"실패로 판정된 요청이 없었다"는 뜻이다.

---

## 5. 검증 결과 (2026-08-19 랜딩 시점, 동일 조건 대조)

| 항목 | 기준선 (`804b447` + `.env`/`.encenv` 복사) | HEAD |
|---|---|---|
| 집계 | 40 failed · 3840 passed · 29 skipped · 5 errors | **38 failed · 4033 passed · 29 skipped · 5 errors** |
| 실패 집합 diff | — | **HEAD에만 있는 실패 0건 (회귀 0)** |
| 개선 | — | 기준선에만 2건 = `test_settings_catalog` 사전존재 실패 해소 |

- `python scripts/arch_check.py --ci` → **exit 0**
- `cd mcp_server && pytest` → 182 passed · 2 skipped
- 커버리지 `src/observability` **96.5%**(코드리뷰 보강 후, `pytest-cov` 미설치로 stdlib `trace` 근사)
- 성능 예산 실측 통과(중앙값 기준·워밍업 제외). ReDoS 수정으로 최악 케이스 **1405ms → 0.06ms**
- **환경 제약**: `ruff`·`mypy` 미설치 → `make lint` 실행 불가. `playwright` 미설치 → `tests/e2e` 수집 불가(기준선 동일 조건)

---

## 6. 랜딩 과정에서 드러난 것 (재발 방지용)

| # | 사건 | 교훈 |
|---|---|---|
| 1 | `traced` 래퍼가 `functools.partial`을 감싸 `test_deep_agent_wiring.py`의 배선 검증(D-062)이 **조용히 무력화** | **관측 기능의 무회귀를 "응답 비트동일"만으로 주장하면 안 된다.** 인트로스펙션에 의존하는 안전망은 래핑만으로 꺼진다 → `__wrapped__` 노출 + `inspect.unwrap()` 투과, 계약 테스트 3건으로 고정 |
| 2 | `log_sql`의 `inspect.stack()[caller_depth]`가 얕은 스택에서 `IndexError` → **SQL 기록이 통째로 유실** | 단위 테스트 9건은 전부 그린이었다. **스모크로 실제 파일을 열어보다** 발견 |
| 3 | `_cutoff`의 `int(value)`가 `MagicMock`을 **조용히 1로 변환** → 의도치 않은 삭제 위험 + 기동 경로 파손 | 보존 일수 같은 파괴적 파라미터는 **허용 타입 화이트리스트**(`_as_days`) |
| 4 | 마스킹이 값 **전체 일치**만 검사해 `"auth failed with sk-…"` 형태 키가 그대로 기록(5종 실측) | 트레이스는 실패 시 반드시 기록되므로 그대로 두면 **상시 노출 경로**. 전체 일치 + 문장 내 검색 2단 |
| 5 | worktree 기준선에 `.env`/`.encenv`를 복사하지 않아 첫 대조가 무효(822초 vs 115초) | 같은 실수 **3회째**. 문서가 아니라 **worktree 생성 자동화**로 막으라는 권고를 남김 |

상세: `docs/18_known_mistakes.md` 2026-08-19 항목.

---

## 7. 알려진 한계 · 후속 판단 대기

| # | 항목 | 현황 |
|---|---|---|
| 1 | 보존 기간 기본값(SQL 30일 · 트레이스 14일) | **비블로킹 Open Question**으로 기본값 진행. 1개월 실측 후 조정하기로 했으므로 **디스크 사용량 실측이 남아 있다** |
| 2 | `logs/sql/` 파일 분할 정책 | 날짜별 단일 파일 유지. DB별 분리는 미채택 |
| 3 | 커버되지 않는 실행 경로 | 앱 자체 운영 SQL(users/audit_logs·부팅 DDL·`*_repository.py`)은 **의도적 대상 외**(§2.9) |
| 4 | LLM 호출 자체는 미기록 | "왜 그 SQL을 만들었나"(프롬프트·토큰·지연 분해)는 본 기능 범위 밖 — `plans/56-langfuse-observability.md`(미착수)의 영역이다. 본 기능은 **노드 단위**까지만 본다 |
| 5 | `lint` 게이트 | `ruff`·`mypy` 미설치로 미검증 상태 |

---

## 8. 관련 문서

| 문서 | 관계 |
|---|---|
| `SPEC-ops-logging-and-synonym-set.md` | 요건 원문·실측 기준선·모듈별 Success Criteria (§5 모듈 A · §6 모듈 B) |
| `tasks/plan.md` · `tasks/todo.md` | 구현 계획(AD-1~AD-6 · 의존 그래프) · 태스크 T0~T10 · 구현 결과 |
| `docs/02_decision.md` D-140 · D-141 | 확정 결정 원본(근거·기각 대안·보강 이력) |
| `docs/02_decision.md` D-142 | **모듈 C `synonym-set`** — 같은 스펙에 묶였으나 로깅과 코드 접점 0. 본 문서 범위 밖 |
| `plans/70-codebase-scale-and-path-debt.md` | 폴백 사다리 관측(D-162) — 트레이스 헤더 `ladder` 필드의 출처 |
| `plans/56-langfuse-observability.md` | LLM 호출 단위 관측(미착수). 본 기능과 계층이 다르다(노드 vs LLM 호출) |
| `plans/40-audit-logging-enhancement.md` | 감사 로깅 — 목적이 다르다("누가 무엇을 했는가" vs "왜 실패했는가") |
| `docs/18_known_mistakes.md` (2026-08-19) | §6 사건 5건의 상세 |
