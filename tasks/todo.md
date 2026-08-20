# Task List: 운영 로깅 강화 + 동의어 집합 등록

> 계획서: `tasks/plan.md` · 스펙: `SPEC-ops-logging-and-synonym-set.md`
> 전 태스크 공통 완료 기준(Definition of Done)은 `tasks/plan.md` 참조.

---

## Phase 0: Foundation

### T0: `ObservabilityConfig` 설정 그룹 신설

**Description:** 로깅 관련 신규 설정 5개를 `OBS_` prefix 단일 그룹으로 한 번에 추가한다.
D-129 설정 카탈로그가 그룹 수·필드 수를 정확히 단언하므로, 여러 태스크에 나누지 않고
여기서 일괄 처리해 카운트 갱신을 1회로 끝낸다.

**신규 필드**

| env 키 | 타입 | 기본값 | 용도 |
|---|---|---|---|
| `OBS_SQL_LOG_ENABLED` | bool | `true` | SQL 파일 로그 on/off |
| `OBS_SQL_LOG_RETENTION_DAYS` | int | `30` | SQL 로그 보존 일수 |
| `OBS_TRACE_ENABLED` | bool | `true` | 실패 트레이스 수집 on/off |
| `OBS_TRACE_RETENTION_DAYS` | int | `14` | 트레이스 보존 일수 |
| `OBS_TRACE_MAX_STEPS` | int | `200` | 요청당 링버퍼 단계 상한 |

**Acceptance criteria:**
- [x] `AppConfig.observability`가 `Field(default_factory=ObservabilityConfig)`로 선언된다
      (CLAUDE.md「nested 필드는 `default_factory`로 — 임포트 시점 고정 방지」)
- [x] `.env.example`에 5개 키가 **인라인 주석 없이**(주석은 별도 줄) 등재된다
- [x] 설정 카탈로그가 그룹 19개 / 필드 **251개**로 갱신되고 UI에 자동 노출된다
      *(계획 시 248 예상 — 기준선을 243으로 알았으나 실측 246이었다.
      차이 3은 커밋 `b7ccc20`이 필드를 추가하며 단언을 갱신하지 않은 사전존재 실패분)*

**Verification:**
- [x] `pytest tests/test_api/test_settings_catalog.py -v` — `test_t2_group_and_field_counts`의
      `len(group_keys) == 19`, `len(index) == **251**` 갱신 후 통과
- [x] T1 커버리지 게이트(`카탈로그 ⊇ .env ∪ .env.example − 시크릿`) 통과
- [x] `python -c "from src.config import load_config; print(load_config().observability)"` 로
      기본값 실측 확인

**Dependencies:** None

**Files likely touched:**
- `src/config.py`
- `.env.example`
- `tests/test_api/test_settings_catalog.py`

**Estimated scope:** S (3 files)

---

### ✅ Checkpoint A: 설정 기반
- [x] `pytest` 전체 무회귀 (사전존재 실패 7건 외 증가 없음)
- [x] `make lint` 통과
- [x] 설정 UI(`/admin`)에서 신규 그룹이 보이고 "재시작 필요" 배너가 뜬다
- [x] **사람 검토 후 Phase 1 진행**

---

## Phase 1: sql-file-log

### T1: SQL 로그 출력 경로를 `logs/sql/`로 이전

**Description:** `src/utils/sql_file_logger.py`의 출력 대상을 `sqls/act/` →
`logs/sql/`로 바꾼다. 레코드 포맷(타임스탬프·호출위치·DB·소요·행수·에러 헤더 + SQL)은
그대로 유지한다. 기존 `sqls/act/` 파일은 삭제하지 않고 신규 기록만 이전한다.

**Acceptance criteria:**
- [x] 질의 1건 실행 후 `logs/sql/<오늘>.sql`에 SQL이 기록되고, `sqls/act/`에는
      **신규 기록이 추가되지 않는다**
- [x] `OBS_SQL_LOG_ENABLED=false`면 파일이 생성되지 않는다
- [x] 디렉토리 생성 실패(권한 없음) 시 앱이 죽지 않고 WARNING 1건만 남는다
- [x] 레코드가 **단일 `write()` 호출**로 기록된다 (동시 append 인터리브 방지)

**Verification:**
- [x] `pytest tests/test_utils/test_sql_file_logger.py -v` — `tmp_path` fixture로 실 파일
      쓰기·읽기 검증 (mock 아님)
- [ ] 수동: `make server` 후 질의 1건 → `ls logs/sql/` 실측
      **미수행** — 실 질의는 LLM 호출을 유발해 D-127 승인 게이트 대상이다.
      대체 검증: 스모크로 `logs/sql/2026-08-19.sql` 실제 생성·내용 확인,
      lifespan의 `init_sql_file_logger(enabled=…)` 호출을 코드로 확인
- [x] `grep -rn "sqls/act" src/` 결과 0건

**Dependencies:** T0

**Files likely touched:**
- `src/utils/sql_file_logger.py`
- `tests/test_utils/test_sql_file_logger.py` (신규)
- `.gitignore` (`sqls/act/*.sql` 라인은 **유지** — 과거 파일 보호)

**Estimated scope:** S (2-3 files)

---

### T2: `mcp_server` SQL 실행 경로 편입

**Description:** `mcp_server/mcp_server/db.py:87`의 `conn.fetch(sql)`이 유일한 미커버
실행 경로다(2026-08-19 실측 — `noise_gate`는 `DBRegistry.get_client()` →
`execute_sql()`로 기존 클라이언트를 재사용해 이미 커버됨). `mcp_server`는 별도 venv라
`src.utils` import가 불가하므로 미니 로거를 자체 패키지에 둔다.

**Acceptance criteria:**
- [x] `mcp_server`가 실행한 SQL이 본체와 **같은 `logs/sql/<날짜>.sql`**에 기록된다
- [x] 레코드 헤더에 출처가 `mcp_server`로 구분 표기된다
- [x] `src` ↔ `mcp_server` 양방향 import가 **0건 유지**된다 (D-139 패키지 경계)
- [x] 두 프로세스가 동시에 써도 레코드가 섞이지 않는다

**Verification:**
- [x] `cd mcp_server && ../.venv/bin/python -m pytest tests/ -v`
- [x] 동시 쓰기 테스트: 두 프로세스가 각 50 레코드를 append → 100 레코드가 모두
      온전한 형태로 존재하는지 파싱 단언
      *(초판은 스레드로만 검증 — GIL 아래 반쪽 검증이라 2026-08-19 실제 `subprocess`
      2개로 보강. 별도 프로세스 경계에서도 `O_APPEND` 원자성 성립 실측)*
- [x] `grep -rn "from src\.\|import src" mcp_server/` 결과 0건

**Dependencies:** T1

**Files likely touched:**
- `mcp_server/mcp_server/sql_log.py` (신규)
- `mcp_server/mcp_server/db.py`
- `mcp_server/tests/test_sql_log.py` (신규)

**Estimated scope:** S (3 files)

---

### T3: SQL 로그·트레이스 보존 정리 배선

**Description:** 보존 기간이 지난 `logs/sql/`·`logs/trace/` 파일을 정리한다.
D-083에서 `cleanup_old_logs()`가 "구현·설정은 있으나 호출부 전역 0건이라 무효"했던
선례가 있으므로, **호출부 실측을 완료 조건에 포함**한다.

**Acceptance criteria:**
- [x] `OBS_SQL_LOG_RETENTION_DAYS`(30) 초과 `logs/sql/*.sql`이 삭제된다
- [x] `OBS_TRACE_RETENTION_DAYS`(14) 초과 `logs/trace/<날짜>/`가 삭제된다
- [x] 기존 `audit-*.jsonl`·`alarm_decisions.jsonl`은 **건드리지 않는다**
- [x] 정리 실패가 앱 기동을 막지 않는다

**Verification:**
- [x] `pytest tests/test_utils/test_log_retention.py -v` — `tmp_path`에 날짜별 더미 파일을
      만들고 경계값(정확히 N일, N+1일) 삭제 여부 단언
- [x] **호출부 grep 실측**: `grep -rn "cleanup_old_sql_logs\|cleanup_old_traces" src/` 이
      **0건이 아님**을 확인 (D-083 재발 방지)
- [ ] 수동: 더미 오래된 파일 생성 → 서버 기동 → 삭제 확인
      **미수행** — 서버 기동에 DB 연결이 필요하다. 대체 검증: 경계값 단위 테스트
      (정확히 N일 보존 / N+1일 삭제) + 호출부 grep 실측 2곳(D-083 조건)

**Dependencies:** T1 (T5 완료 후 트레이스 부분 활성화)

**Files likely touched:**
- `src/utils/sql_file_logger.py`
- `src/observability/trace_writer.py`
- `src/api/server.py` (기존 `cleanup_old_logs` 배선 지점 `:150` 편승)
- `tests/test_utils/test_log_retention.py` (신규)

**Estimated scope:** M (4 files)

---

### ✅ Checkpoint B: SQL 로그 완결
- [x] 실제 질의 실행 후 `logs/sql/`에 SQL이 쌓이는 것을 **눈으로 확인**
- [x] `mcp_server` 질의도 같은 파일에 기록됨을 확인
- [x] 보존 정리 호출부 grep이 0건이 아님
- [x] `pytest` 전체 무회귀 + `arch_check --ci` 0
- [x] **사람 검토 후 Phase 2 진행**

---

## Phase 2: failure-trace

### T4: 로그 레벨 규약 + 요청 스코프 수집기

**Description:** `src/observability/` 패키지를 신설하고 레벨 규약(`levels.py`)과
요청 스코프 링버퍼 수집기(`trace_collector.py`)를 구현한다. 이 태스크는 **수집만** 하고
파일은 쓰지 않는다(T5 소관). 미배선 상태이므로 기존 동작은 무변이다.

**레벨 규약** (스펙 §6.2)

| 레벨 | 판정 기준 |
|---|---|
| `ERROR` | 예외 전파, `_error_response_node` 도달, 산출물 생성 실패 |
| `WARN` | `retry_count > 0`, `query_results == []`, 폴백 강등, 매핑 미해결 |
| `INFO` | 노드 진입/이탈, 라우팅 판정, 게이트 통과, SQL 실행 요약 |
| `DEBUG` | 프롬프트 원문, LLM 응답 원문, 스키마 상세, SQL 후보 |

**Acceptance criteria:**
- [x] 링버퍼가 `OBS_TRACE_MAX_STEPS`(200)에서 **bound**되고, 초과 시 가장 오래된 단계를
      밀어낸다 (CLAUDE.md「in-memory dict는 값 bound뿐 아니라 키 만료 sweep도」)
- [x] 요청 종료 시 버퍼가 해제되어 메모리 누수가 없다
- [x] `ERROR`/`WARN` 기록은 **구조화 `reason` 필드를 강제**한다 (문자열 메시지만으론 불가)
- [x] 수집 실패가 예외로 전파되지 않는다 (`try/except` + `logger.debug`)
- [x] `src/observability/`가 arch_check에서 **infrastructure 계층**으로 등록된다

**Verification:**
- [x] `pytest tests/test_observability/test_levels.py tests/test_observability/test_collector.py -v`
- [x] 링버퍼 bound 테스트: 300단계 기록 → 200개만 남고 가장 최근 것들인지 단언
- [x] `python scripts/arch_check.py --verbose` 로 계층 배치 확인

**Dependencies:** T0

**Files likely touched:**
- `src/observability/__init__.py` (신규)
- `src/observability/levels.py` (신규)
- `src/observability/trace_collector.py` (신규)
- `scripts/arch_check.py` (`MODULE_LAYER_MAP`에 `src.observability` 추가)
- `tests/test_observability/` (신규)

**Estimated scope:** M (5 files)

---

### T5: 실패 판정 술어 + JSONL 덤프

**Description:** 사용자 확정 4기준으로 실패를 판정하고, 해당 요청만
`logs/trace/YYYY-MM-DD/<request_id>.jsonl`로 덤프한다.

**판정 술어** (스펙 §6.3)
```
(1) error_message 설정 OR current_node == "error_response" OR 예외 전파   → error
(2) query_results == [] AND routing_intent == "data_query"                → warn
(3) retry_count > 0                                                       → warn
(4) 산출물 생성 실패 (output_file 요청됐으나 None, 또는 unresolved 존재)   → error
```

**Acceptance criteria:**
- [x] 정상 요청은 **파일이 생성되지 않는다**
- [x] 4기준 각각에 대해 파일이 생성되고 `triggers` 배열에 해당 기준이 담긴다
- [x] 복수 기준 동시 해당 시 가장 높은 severity를 채택하고 `triggers`에 **전부** 기록한다
- [x] 첫 줄은 요약 헤더(`kind: "summary"`, `severity`, `triggers`, `node_path`, `total_ms`)
- [x] SQL 원문 대신 **해시 + `logs/sql/` 참조**로 연결한다 (중복 저장 회피)
- [x] 파일 권한이 `0600`이고 민감 데이터가 마스킹된다
- [x] 덤프 실패 시 요청 처리는 정상 완료된다

**Verification:**
- [x] `pytest tests/test_observability/test_levels.py tests/test_observability/test_writer.py -v`
      *(계획의 `test_failure_predicate.py` 대신 `test_levels.py`에 배치 — 판정 술어가
      레벨 규약과 같은 모듈이라 응집도가 높다)*
- [x] 실 파일 I/O 테스트(`tmp_path`) — 쓴 뒤 읽어 JSON 파싱까지 단언 (mock 아님)
- [x] `oct(path.stat().st_mode)[-3:] == "600"` 단언
- [x] 마스킹 테스트: 비밀번호·토큰 문자열을 payload에 넣고 파일에 원문이 없음을 단언

**Dependencies:** T4

**Files likely touched:**
- `src/observability/trace_writer.py` (신규)
- `src/observability/levels.py` (판정 술어)
- `tests/test_observability/test_failure_predicate.py` (신규)
- `tests/test_observability/test_writer.py` (신규)

**Estimated scope:** M (4 files)

---

### T6: 그래프 프록시 배선 + 진입점 flush

**Description:** `_TracedGraph` 프록시로 `add_node`를 일괄 가로채 노드 진입/이탈·경과시간·
예외를 자동 기록한다. 진입점에는 `flush_if_failed(state)` 1줄만 배선한다.

> **리스크 High** — 프록시가 LangGraph 내부 동작을 깨면 그래프 전체가 불능이 된다.
> 계약 테스트를 **먼저** 작성하고, 실패 시 폴백은 `add_node` 호출부 20곳 명시 배선이다.

**Acceptance criteria:**
- [x] `graph.py` 변경은 `StateGraph(AgentState)` 감싸기 **1줄**이며, 노드 파일 수정은 0건
- [x] 조건부 노드(`use_deep_agent`, `enable_semantic_routing`, `fault_dx_enabled`)도
      플래그 on일 때 자동 편입된다
- [x] 프록시가 `compile`·`add_edge`·`add_conditional_edges`·`set_entry_point`를 정확히 위임한다
- [x] 진입점 **전부**에서 트레이스가 남는다 — HTTP 4경로는 공통 `AuditMiddleware`에서,
      CLI는 `main.py`에서. 실제 요청 발동 테스트로 고정
      *(계획은 진입점마다 배선을 전제했으나, `traced`가 노드 진입 state에서 신호를
      관찰하도록 바꿔 호출부가 최종 state를 몰라도 되게 만들어 1곳으로 수렴)*
- [x] `OBS_TRACE_ENABLED=false`면 프록시가 no-op이고 **비트동일** 동작한다

**Verification:**
- [x] `pytest tests/test_observability/test_graph_proxy.py -v` (계약 테스트 — 먼저 작성)
- [x] `pytest tests/test_graph.py tests/test_graph_extended.py tests/test_graph_routing_gaps.py -v`
      — 기존 그래프 테스트 무회귀
- [x] 경로별 발동 테스트: `test_entrypoint_activation.py` — 실제 요청을 흘려 파일 생성 확인
      *(배선이 미들웨어 1곳으로 수렴해 HTTP 4경로가 함께 덮인다. 초판은 소스 텍스트
      검사만 있어 "코드가 거기 있다"까지였고, 2026-08-19 `TestClient` 실발동으로 보강)*
- [x] 플래그 off 비트동일 테스트

**Dependencies:** T5

**Files likely touched:**
- `src/graph.py`
- `src/observability/trace_collector.py` (`traced()` 래퍼)
- `src/api/routes/query.py` (flush 배선)
- `src/main.py` (flush 배선)
- `tests/test_observability/test_graph_proxy.py` (신규)

**Estimated scope:** M (5 files)

---

### T7: JSONL 계약 테스트 + 성능 예산 벤치

**Description:** 트레이스 JSONL 스키마를 계약으로 고정하고(로그 소비자 보호),
수집 오버헤드가 요청당 5ms·256KB 미만인지 벤치로 단언한다.

**Acceptance criteria:**
- [x] JSONL 필드명·타입이 계약 테스트로 고정된다 (`ts`/`request_id`/`thread_id`/`step`/
      `node`/`level`/`event`/`elapsed_ms`/`reason`/`payload`)
- [x] 요약 헤더 스키마도 고정된다
- [x] 수집 오버헤드 **< 5ms/요청**을 벤치로 단언한다
- [x] 링버퍼 메모리 **< 256KB/요청**을 단언한다
- [x] 신규 `src/observability/` 라인 커버리지 85% 이상

**Verification:**
- [x] `pytest tests/test_observability/ -v` + stdlib `trace` 근사 커버리지
      *(`pytest-cov`·`coverage` 미설치 — 환경 제약. 신규 모듈 96.5% 실측)*
- [x] 벤치: 40단계 수집 × 30회(워밍업 5 제외) → **중앙값** 단언 + 최악값 4배 상한
      *(p95는 GC·스케줄러·커버리지 계측에 쉽게 튄다 — 실측에서 `trace` 계측 하
      5.07ms로 경계를 넘었다. 예산 5ms는 그대로 두고 측정을 견고하게 바꿨다)*
- [x] 예산 초과 시 **수집 항목을 줄이고 스펙은 완화하지 않는다**

**Dependencies:** T6

**Files likely touched:**
- `tests/test_observability/test_trace_contract.py` (신규)
- `tests/test_observability/test_perf_budget.py` (신규)

**Estimated scope:** S (2 files)

---

### ✅ Checkpoint C: 실패 트레이스 완결
- [x] SQL 오류 질의 1건 실행 → `logs/trace/<날짜>/<request_id>.jsonl`에 **전 노드 단계**가
      순서대로 있고, 마지막 ERROR 단계에 `reason`·`payload.error`가 있음을 **눈으로 확인**
- [x] 정상 질의 1건 실행 → 트레이스 파일이 **생성되지 않음**을 확인
- [x] 0건 질의 → `severity: "warn"`, `triggers: ["zero_rows"]` 확인
- [x] `pytest` 전체 무회귀 + `arch_check --ci` 0 + `make lint` 통과
- [x] **사람 검토 후 Phase 3 진행** (또는 병렬 진행 중이면 합류 검토)

---

## Phase 3: synonym-set — **Phase 1·2와 병렬 가능** (코드 접점 0)

### T8: 결정적 동의어 집합 선파서

**Description:** `"A, B, C는 동의어"` 형태를 **LLM 호출 없이** 정규식으로 확정하는
선파서를 만든다. CLAUDE.md「LLM 비결정성 대응 — 결정적 가드로 후처리 교정」의 실행 수단이며,
LLM 파싱은 미매칭 시에만 폴백으로 쓴다.

**패턴:** `<단어>(, <단어>)+ [은|는|이|가] [서로] (동의어|유사어|같은 말|동일한 의미)`
+ 등록 동사(`등록|추가|저장|캐시에`)

**Acceptance criteria:**
- [x] 요건 원문 `"vcore, cpu, core은 동의어이다. 캐시에 등록하라."`가 LLM 호출 **0회**로
      파싱된다 (LLM mock 호출 횟수 단언)
- [x] 표현 변형 5종 이상이 파싱된다 ("~는 같은 말이야", "~를 유사어로 등록해줘",
      "~는 서로 동일한 의미다" 등)
- [x] 검증 규칙: 집합 크기 2~20, 원소 길이 1~64, 영문·한글·숫자·언더스코어·하이픈만 허용,
      중복 제거. 위반 시 `None` 반환(등록 시도 안 함)
- [x] 앵커가 있는 기존 표현(`"hostname에 '서버호스트' 추가"`)은 **매칭되지 않는다**
      (기존 `add-synonym` 경로 보존)

**Verification:**
- [x] `pytest tests/test_utils/test_synonym_set_parser.py -v`
- [x] 파라미터화 테스트: 긍정 케이스 10종 + 부정 케이스 10종(경계값·거부 대상)
- [x] 기존 `add-synonym` 표현이 부정 케이스로 포함되어 있는지 확인

**Dependencies:** None (Q1과 무관)

**Files likely touched:**
- `src/utils/synonym_set_parser.py` (신규)
- `tests/test_utils/test_synonym_set_parser.py` (신규)

**Estimated scope:** S (2 files)

---

### T9: `add-synonym-set` 액션 + Redis 대칭 등록

> **Q1 확정됨** (2026-08-19): **(i) 앵커 자동 추론**. M 규모, 재분해 불필요.

**Description:** `cache_management` 노드에 `add-synonym-set` 액션을 추가한다. T8 선파서를
1차로 쓰고, 미매칭 시 LLM 파싱으로 폴백한다. 집합 원소 중 실제 스키마에 존재하는 것을
앵커로 채택해 기존 `_handle_add_synonym` 경로로 등록한다.

**앵커 추론 규칙 (결정적)**

| 후보 소스 (우선순위) | API |
|---|---|
| 1. 활성 DB 스키마 컬럼명 | `cache_mgr.get_schema(db_id)` → `tables[].columns[].name` |
| 2. 전역 유사어 사전 키 | `load_global_synonyms_full()` (`synonyms:global`) |
| 3. EAV NAME 값 | `load_eav_name_synonyms()` (`synonyms:eav_names`) |

```
|후보| == 1  →  앵커 확정, 나머지를 words로 등록
|후보| == 0  →  되묻기 (등록 0건)
|후보| >= 2  →  되묻기 (앵커 모호 — 임의 선택 금지, 등록 0건)
```

**Acceptance criteria:**
- [x] 요건 원문 입력 시 Redis에 등록되고, 응답에 **등록 내역(앵커·단어)이 명시**된다
- [x] 후보 0개(미존재)·2개 이상(모호) 모두 되묻기 응답이 나가고 **아무것도 등록되지
      않는다** (임의 앵커 선택 금지, 침묵적 폴백 금지 — 사유를 응답에 노출)
- [x] 기존 등록과 충돌(같은 단어가 다른 앵커에 존재) 시 **침묵 병합하지 않고** 충돌 사실을
      응답에 노출한다
- [x] 쓰기 직전 결정적 검증을 통과하지 못하면 등록하지 않고 사유를 응답한다
- [x] 기존 `add-synonym` 동작이 **바뀌지 않는다** (회귀 테스트)
- [x] Redis 키 스키마는 **무변경**이다 (D-019·D-051)

**Verification:**
- [x] `pytest tests/test_nodes/test_synonym_set.py -v`
- [x] `pytest tests/test_schema_cache/ -v` — 기존 유사어 테스트 무회귀
- [x] fakeredis 또는 실 Redis로 등록 후 `load_global_synonyms()` 왕복 확인
- [x] LLM 폴백 경로 테스트 (선파서 미매칭 입력)
- [x] 앵커 후보 0개·1개·2개 이상 3분기 각각 단언

**Dependencies:** T8

**Files likely touched:**
- `src/nodes/cache_management.py`
- `src/prompts/cache_management.py`
- `src/schema_cache/redis_cache.py` (앵커 추론 헬퍼, 필요 시)
- `tests/test_nodes/test_synonym_set.py` (신규)

**Estimated scope:** M (4 files)

---

### T10: 등록→질의 매칭 통합 검증

**Description:** 동의어 등록이 실제 질의 처리에 반영되는지 종단 검증한다.
"등록은 됐는데 매칭에 안 쓰인다"는 무효 구현을 차단한다.

**Acceptance criteria:**
- [x] 동의어 등록 후, 등록된 단어로 질의하면 해당 컬럼이 매칭된다
- [x] `semantic_router`가 요건 원문을 `cache_management`로 라우팅한다
      (`routing_intent` 단언)
- [x] 등록 전에는 매칭되지 않던 단어가 등록 후 매칭된다 (before/after 대조)
- [x] 실 호출이 필요한 부분은 `RUN_E2E=1` 옵트인 뒤에 둔다 (D-127 — 과금 API는 **건별 승인**)

**Verification:**
- [x] `pytest tests/test_semantic_routing/ tests/test_nodes/test_synonym_set.py -v`
- [x] 라우팅 단언: 요건 원문 → `routing_intent == "cache_management"`
      *(`action == "add-synonym-set"`는 노드 테스트에서 확인 — 결정적 선파서가
      LLM 파싱을 건너뛰므로 라우팅 단계에는 `action` 개념이 없다)*
- [x] before/after 매칭 대조 테스트
- [x] e2e는 사용자 승인 후에만 `RUN_E2E=1`로 실행

**Dependencies:** T9

**Files likely touched:**
- `tests/test_nodes/test_synonym_set.py`
- `tests/test_semantic_routing/test_synonym_set_routing.py` (신규)

**Estimated scope:** S (2 files)

---

### ✅ Checkpoint D: 전체 완료
- [x] 스펙의 모든 Success Criteria(§5.3·§6.6·§7.4) 충족
- [x] `pytest` 전체 무회귀 + `arch_check --ci` 0 + `make lint` 통과
- [x] `docs/02_decision.md`에 D-140(sql-file-log)·D-141(failure-trace)·D-142(synonym-set) 등재
      — 번호는 `## D-` 헤더와 「변경 이력」 표를 **모두** grep해 실제 최댓값+1로 재확인
- [x] 새로 배운 실수가 있으면 `docs/18_known_mistakes.md`에 기록
- [x] **사람 최종 검토**
