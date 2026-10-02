# SPEC: 운영 로깅 강화 + 동의어 집합 등록

> **위치**: 이 문서는 기존 `SPEC.md`(전체 시스템 요건 정의서, 895줄)를 대체하지 않는다.
> 2026-08-19 추가 요건 3건만 다루는 **증분 스펙**이며, 승인 후 `docs/02_decision.md`에
> D-141~D-143으로 등재한다.
>
> **상태**: **승인됨** (2026-08-19). Q1 확정 → §7.3 안 (i) 앵커 자동 추론.
> 계획서 `tasks/plan.md`, 태스크 `tasks/todo.md`.

---

## 0. 요청 원문

1. 에이전트에서 동작하는 모든 SQL은 로그 파일로 생성하여 `logs` 폴더에 저장하라.
2. 에이전트 동작 시 정상적인 응답을 제공하지 못할 경우 전체 프로세스 단계별로 로그를
   생성하라. 이 로그를 기반으로 원인을 파악할 수 있도록 로그 수준을 정의하라.
3. `"vcore, cpu, core은 동의어이다. 캐시에 등록하라."` 프롬프트가 들어오면 시멘틱 라우팅
   또는 deep agents를 통해 Redis에 동의어를 등록할 수 있는 기능을 추가하라.

---

## 1. 실측 기준선 (2026-08-19, 코드 확인)

세 요건 모두 **부분 구현이 이미 존재**한다. 이 스펙은 신규 구축이 아니라 **델타**를 정의한다.

| 요건 | 현행 구현 | 실측 갭 |
|---|---|---|
| 1. SQL 파일 로그 | `src/utils/sql_file_logger.py` — `sqls/act/YYYY-MM-DD.sql`에 append. 호출부 8곳(`src/dbhub/client.py` 3, `src/db/client.py` 5). 초기화 `src/main.py:41`, `src/api/server.py:243` | ① 저장 위치가 `logs/`가 아님 ② `mcp_server/`(별도 프로세스·별도 venv)·`noise_gate/` 실행 경로 미커버 |
| 2. 단계별 실패 로그 | `setup_logging()`(`src/security/audit_logger.py:185`) = structlog + **stdout 전용**(`PrintLoggerFactory`). `logs/`에는 `audit-DATE.jsonl`·`alarm_decisions.jsonl`만 존재 | **노드별 단계 트레이스를 파일로 남기는 경로가 0건.** 앱 로그는 프로세스 종료 시 소실. 로그 레벨은 `AppConfig.log_level` 단일 필드뿐, 진단 목적 레벨 규약 없음 |
| 3. 동의어 등록 | `src/nodes/cache_management.py`의 `add-synonym` 액션 + `RedisCache.add_global_synonym(column_name, words)`. `src/nodes/synonym_registrar.py`는 pending 후보 **승인 전용** | `add-synonym`은 **앵커 컬럼(`target_column`) 필수**. "vcore, cpu, core은 동의어"는 **앵커 없는 대칭 집합** — 파싱 프롬프트에 개념 자체가 없어 미지원 |

**재사용 가능한 자산**
- State 추적 필드: `request_id`, `thread_id`, `current_node`, `query_attempts`, `retry_count`, `error_message`, `smq_derivation` (`src/state.py`)
- `AuditMiddleware`(`src/api/middleware/audit_middleware.py`)가 요청마다 8자리 `request_id`를 생성해 `structlog.contextvars`에 바인딩
- `logs/`는 이미 `.gitignore` 등재 → 신규 하위 폴더도 자동 커버
- `sqls/act` 참조는 문서(`plans/`·`docs/`·`testdata/`)의 **서술적 언급뿐**, 코드 참조 0건 → 이전 시 코드 파손 없음

---

## 2. 명시적 가정 (ASSUMPTIONS)

구현 전 정정하지 않으면 아래 전제로 진행한다.

1. **"에이전트에서 동작하는 모든 SQL"의 범위** = 사용자 질의 처리 경로에서 실행되는 SQL
   + 관측 DB 질의(`mcp_server`). 앱 자체 운영 SQL(`users`/`audit_logs` DDL·CRUD,
   `src/api/server.py`의 부팅 DDL, `src/infrastructure/*_repository.py`)은 **대상 외** —
   에이전트 질의가 아니라 앱 내부 저장이며, 감사 로그에 이미 기록된다.
2. **트레이스 파일은 개인정보·자격증명을 담을 수 있다** → 기존 마스킹 규약(`src/security/`)을
   그대로 적용하고, 파일 권한은 `0600`으로 생성한다.
3. **로그 보존 기간**은 감사 로그와 동일 정책(`retention_days`)을 따르되, 트레이스는
   기본 14일로 별도 상한을 둔다(실패 건만 쌓이므로 감사보다 짧게).
4. **성능 예산**: 트레이스 상시 수집의 오버헤드는 요청당 **5ms 미만·메모리 256KB 미만**.
   초과 시 수집 항목을 줄이지 스펙을 완화하지 않는다.
5. **기존 응답 동작은 비트동일**하게 유지한다. 로깅 추가가 `final_response` 내용이나
   그래프 라우팅을 바꾸면 안 된다.
6. **동의어 집합 등록은 결정적 경로가 1차**, LLM 파싱은 폴백이다
   (CLAUDE.md「LLM 비결정성 대응」 준수).

---

## 3. Phase 0 — Capability Map

요청 3건은 각각 독립적으로 테스트·출시 가능한 역량이다. 모듈 단위로 분해한다.

| Module id | 책임 | 의존 |
|---|---|---|
| `sql-file-log` | 실행 SQL 전량을 `logs/sql/`에 파일 기록 | — |
| `failure-trace` | 실패 요청의 노드별 단계 트레이스 + 로그 레벨 규약 | `sql-file-log` (로그 루트 레이아웃·정리 정책 공유) |
| `synonym-set` | 앵커 없는 동의어 집합의 Redis 캐시 등록 | — |

**빌드 순서**: `sql-file-log` → `failure-trace`  /  `synonym-set` (병렬 가능)

`synonym-set`은 로깅 두 모듈과 코드 접점이 없다. `failure-trace`가 `sql-file-log`에 의존하는
것은 `logs/` 하위 레이아웃·보존 정책·마스킹 헬퍼를 공유하기 때문이며, 역방향 의존은 없다.

---

## 4. 공통 규약 (3 모듈 공통)

### 4.1 Tech Stack (기존 유지 — 신규 의존성 0)

| 구분 | 기술 |
|---|---|
| 로깅 | `logging`(stdlib) + `structlog`(기존 의존) |
| 상태 머신 | LangGraph ≥0.2.0 |
| 캐시 | Redis (`src/schema_cache/redis_cache.py`) |
| 설정 | pydantic-settings (`src/config.py`) |
| 테스트 | pytest + pytest-asyncio (`asyncio_mode = "auto"`) |

> **신규 서드파티 패키지는 추가하지 않는다.** 파일 로테이션은 stdlib
> `logging.handlers`로 충분하며, OpenTelemetry 등 도입은 이 스펙 범위 밖이다.

### 4.2 Commands

```bash
# 전체 테스트 (본체 + noise_gate 자동 수집)
pytest

# 이 스펙 관련 테스트만
pytest tests/test_utils/test_sql_file_logger.py tests/test_observability/ tests/test_nodes/test_synonym_set.py -v

# 커버리지
make test                                  # pytest tests/ -v --cov=src --cov-report=term-missing

# 아키텍처 계층 검사 (필수 게이트)
python scripts/arch_check.py --ci

# 린트·타입
make lint                                  # ruff check src/ tests/ && mypy src/
make format                                # ruff format src/ tests/

# 로컬 실행
make server                                # python -m src.main --server
make run Q="CPU 사용률이 가장 높은 서버"
```

### 4.3 Project Structure (신규·변경 파일)

```
logs/                                  ← 모든 로그 산출물의 단일 루트
├── sql/YYYY-MM-DD.sql                 ← [신규] 모듈 A: 실행 SQL 전량
├── trace/YYYY-MM-DD/<request_id>.jsonl ← [신규] 모듈 B: 실패 요청 단계 트레이스
├── audit-YYYY-MM-DD.jsonl             ← [기존] 감사 로그
└── alarm_decisions.jsonl              ← [기존] 알람 판정

src/
├── utils/sql_file_logger.py           ← [변경] 출력 경로 sqls/act/ → logs/sql/
├── observability/                     ← [신규 패키지] 모듈 B
│   ├── __init__.py
│   ├── levels.py                      ← 로그 레벨 규약 + 이벤트 상수
│   ├── trace_collector.py             ← 요청 스코프 링버퍼 수집기
│   └── trace_writer.py                ← 실패 판정 시 JSONL 덤프
├── graph.py                           ← [변경] 노드 등록 시 트레이스 데코레이터 일괄 적용
├── nodes/cache_management.py          ← [변경] add-synonym-set 액션 추가
├── prompts/cache_management.py        ← [변경] 대칭 동의어 집합 개념 추가
├── utils/synonym_set_parser.py        ← [신규] 결정적 동의어 집합 선파서
├── schema_cache/redis_cache.py        ← [변경] 동의어 집합 저장 API
└── config.py                          ← [변경] 로깅·트레이스 설정 필드

mcp_server/
└── sql_log.py                         ← [신규] 별도 프로세스용 미니 SQL 로거 (동일 logs/sql/에 append)

tests/
├── test_utils/test_sql_file_logger.py ← [신규]
├── test_observability/                ← [신규] 레벨 규약·수집기·덤프 트리거
└── test_nodes/test_synonym_set.py     ← [신규]
```

### 4.4 Code Style

기존 코드베이스 관례를 그대로 따른다. 실제 스타일 예시:

```python
"""단계 트레이스 수집기.

요청 스코프로 노드별 단계 기록을 링버퍼에 누적하고, 실패 판정 시에만
logs/trace/에 덤프한다. 정상 경로는 디스크 쓰기 0건이다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# 요청당 보관 상한. 노드 20개 × 재시도 3회 + 여유를 감안한 값으로,
# 초과 시 가장 오래된 단계부터 밀어낸다(메모리 bound 필수 — CLAUDE.md 데몬 dict 교훈).
_MAX_STEPS_PER_REQUEST = 200


@dataclass
class TraceStep:
    """단일 노드 실행 기록."""

    step: int
    node: str
    level: str
    event: str
    elapsed_ms: float
    payload: dict[str, Any] = field(default_factory=dict)


def record_step(request_id: str, step: TraceStep) -> None:
    """단계 기록을 누적한다.

    Args:
        request_id: 요청 추적 ID
        step: 기록할 단계

    Note:
        수집 실패가 메인 로직에 영향을 주면 안 되므로 예외를 삼키고 debug 로그만 남긴다.
    """
```

**규약**
- 모듈 docstring·함수 docstring 한국어, `Args:`/`Returns:` 섹션 유지
- `from __future__ import annotations` 상단 고정
- 타입 힌트 필수, `Optional[X]`보다 `X | None` (기존 혼재 — 신규 코드는 `X | None`)
- 상수는 모듈 상단 `_UPPER_SNAKE`, **왜 그 값인지 주석**
- 로깅 실패·트레이스 실패는 **절대 메인 로직을 깨지 않는다**(`try/except` + `logger.debug`)
- 단, **침묵적 폴백 금지**(CLAUDE.md): 강등·실패는 사유를 구조화해 남긴다

### 4.5 Testing Strategy

| 레벨 | 위치 | 대상 |
|---|---|---|
| 단위 | `tests/test_utils/`, `tests/test_observability/` | 경로 계산, 레벨 매핑, 실패 판정 술어, 결정적 파서 |
| 통합 | `tests/test_nodes/`, `tests/test_graph*.py` | 노드 데코레이터 배선, cache_management 액션 라우팅 |
| 계약 | `tests/test_observability/test_trace_contract.py` | JSONL 스키마 고정 (필드명·타입) — 로그 소비자 보호 |
| 회귀 | 기존 전체 스위트 | 로깅 추가로 응답·라우팅이 바뀌지 않음 |
| e2e | `RUN_E2E=1` 옵트인 | 실 Redis·실 LLM 경로 (D-127 승인 게이트) |

- **커버리지 기준**: 신규 모듈 3개는 라인 커버리지 **85% 이상**
- **비결정성 배제**: 타임스탬프·경과시간은 주입 가능한 clock으로 테스트
- **실 파일 I/O**: `tmp_path` fixture 사용, 프로젝트 `logs/`를 오염시키지 않음
- **금지**: mock만으로 통과시키지 않는다. 트레이스 덤프는 실제 파일을 써서 읽어 검증
  (CLAUDE.md「mock 통과 ≠ 프로덕션 동작」)

### 4.6 Boundaries

**Always do**
- `python scripts/arch_check.py --ci` 통과 (신규 `src/observability/`는 infrastructure 계층)
- 변경 전 `docs/02_decision.md` 확인, 변경 후 D-번호 등재
   (신규 번호는 `## D-` 헤더와 「변경 이력」 표를 **모두** grep해 최댓값+1)
- 로그 기록 실패가 메인 로직을 깨지 않도록 예외 격리
- 민감 데이터(비밀번호·토큰·키) 마스킹 후 기록
- 기존 테스트 전량 통과 확인 후 완료 보고

**Ask first**
- 로그 파일 포맷 변경(기존 `audit-*.jsonl` 소비자 존재)
- Redis 키 스키마 신설·변경 (D-019·D-051 캐시 구조 불변 원칙)
- 신규 설정 필드 추가 — D-129 설정 카탈로그는 **현재 18그룹 / 243필드**(2026-08-19 실측)를
   `tests/test_api/test_settings_catalog.py::test_t2_group_and_field_counts`가 정확히 단언하므로
   같은 커밋에서 갱신해야 한다. `.env.example`에만 키를 넣으면 T1 커버리지 게이트가 CI를 적색으로 만든다
- 성능 예산(§2.4) 초과가 불가피할 때
- 과금 외부 API 실호출 (D-127 — 건별 승인, 포괄 승인 없음)

**Never do**
- `logs/`의 기존 파일(`audit-*.jsonl`, `alarm_decisions.jsonl`) 포맷·경로 변경
- 응답 내용·그래프 라우팅 변경 (이 스펙은 관측 전용)
- LLM 출력만 믿고 동의어를 Redis에 쓰기 — 결정적 가드 필수
   (CLAUDE.md「LLM 자동 등록은 오염 자기강화 루프 위험, 쓰기 지점에서 결정적 차단」)
- 실패 사유를 삼키는 폴백
- `sqls/act/` 기존 파일 삭제 (이전만 하고 과거 이력은 보존)

---

## 5. 모듈 A — `sql-file-log`

### 5.1 Objective

에이전트가 실행하는 **모든 SQL**을 `logs/sql/`에 파일로 남겨, 사후에 "무엇이 실행됐는가"를
코드 없이 확인할 수 있게 한다. 사용자는 운영자·개발자다.

### 5.2 설계

**A-1. 출력 경로 이전** (`src/utils/sql_file_logger.py`)

```
sqls/act/YYYY-MM-DD.sql  →  logs/sql/YYYY-MM-DD.sql
```

- `_SQL_ACT_DIR` → `_SQL_LOG_DIR`, `project_root / "logs" / "sql"`
- 기존 레코드 포맷은 **그대로 유지** (타임스탬프·호출위치·DB·소요·행수·에러 헤더 + SQL)
- `sqls/act/`의 과거 파일은 삭제하지 않음. 신규 기록만 이전
- `.gitignore`의 `sqls/act/*.sql` 라인은 유지 (과거 파일 보호)

**A-2. 미커버 실행 경로 편입**

| 경로 | 현행 | 조치 |
|---|---|---|
| `src/db/client.py` | 커버 (5곳) | 유지 |
| `src/dbhub/client.py` | 커버 (3곳) | 유지 |
| `mcp_server/` | **미커버 확정** — `mcp_server/mcp_server/db.py:87` `conn.fetch(sql)` (별도 venv·별도 프로세스) | `mcp_server/sql_log.py` 미니 로거 신설 → 동일 `logs/sql/`에 append. `src.utils` import 불가하므로 코드 중복 허용(패키지 경계 D-139 준수) |
| `noise_gate/` | **커버됨** (2026-08-19 실측) | 조치 불필요. `polestar_noise_context.py:525` 등이 `DBRegistry.get_client(db_id)` → `client.execute_sql()`로 기존 클라이언트를 재사용 |
| `src/infrastructure/*_repository.py`, `src/api/server.py` DDL | 미커버 | **대상 외** (§2.1 가정) |

> 동시 append는 `O_APPEND` 원자성에 의존한다. 레코드 단위가 4KB를 넘으면 인터리브
> 가능성이 있으므로, 로거는 **레코드를 한 번의 `write()` 호출로** 기록한다(현행 구현이 이미 그러함).

**A-3. 보존 정책**

- 기본 30일 (`sql_log_retention_days`, 감사 로그와 동일)
- 정리 훅은 기존 `cleanup_old_logs` 배선 지점(`src/api/server.py:150`)에 편승
- **주의**: D-083에서 `cleanup_old_logs()`가 "구현·설정은 있으나 호출부 0건이라 무효"였던
  선례가 있다 → 배선 후 **호출부를 grep으로 실측 확인**

### 5.3 Success Criteria

- [ ] 사용자 질의 1건 실행 후 `logs/sql/<오늘>.sql`에 해당 SQL이 기록된다 (실 파일 확인)
- [ ] `sqls/act/`에는 신규 기록이 추가되지 않는다
- [ ] `mcp_server` 프로세스가 실행한 SQL도 같은 파일에 기록된다
- [ ] 로거 초기화 실패(디렉토리 권한 없음) 시 앱이 죽지 않고 WARNING 1건만 남는다
- [ ] 30일 초과 파일이 정리 훅 실행 후 삭제되며, **호출부 grep 결과가 0건이 아니다**
- [ ] 전체 테스트 스위트 무회귀

---

## 6. 모듈 B — `failure-trace`

### 6.1 Objective

에이전트가 정상 응답을 내지 못했을 때, **어느 단계에서 무엇 때문에 끊겼는지**를
로그만 보고 확정할 수 있게 한다. CLAUDE.md「0건/실패 진단은 안쪽 단계부터 추정 수정하지
말고 진입·게이트별 로그로 끊긴 지점부터 확정」의 실행 수단이다.

### 6.2 로그 레벨 규약 (요건 2의 "로그 수준 정의")

| 레벨 | 의미 | 판정 기준 | 예시 이벤트 |
|---|---|---|---|
| `ERROR` | 요청이 최종 실패. 사용자에게 결과를 못 줌 | 예외 전파, `_error_response_node` 도달, 산출물 생성 실패 | `node.exception`, `graph.error_response`, `output.generation_failed` |
| `WARN` | 응답은 나갔으나 **열화**됨. 원인 추적 가치 있음 | `retry_count > 0`, `query_results == []`, 폴백 강등, 필드 매핑 미해결(`unresolved`) | `query.zero_rows`, `generator.retry`, `mapping.unresolved`, `fallback.degraded` |
| `INFO` | 정상 경로의 의사결정 지점 | 노드 진입/이탈, 라우팅 판정, 게이트 통과, SQL 생성·실행 요약 | `node.enter`, `node.exit`, `router.intent`, `sql.executed` |
| `DEBUG` | 진단 시에만 필요한 상세 | 프롬프트 원문, LLM 응답 원문, 스키마 상세, SQL 후보 전량 | `llm.prompt`, `llm.raw_response`, `schema.detail` |

**핵심 원칙**
- **수집은 항상 INFO+DEBUG까지**, 파일 덤프는 실패 시에만 → 정상 경로 디스크 비용 0
- 콘솔 출력 레벨(`AppConfig.log_level`)과 **트레이스 수집 레벨은 독립**.
  콘솔이 INFO여도 트레이스 버퍼는 DEBUG까지 담는다 (사후 진단이 목적)
- `ERROR`/`WARN`은 **반드시 구조화 사유**(`reason` 필드)를 동반한다. 메시지 문자열만으로
  분류하지 않는다

### 6.3 실패 판정 술어 (사용자 확정 4기준)

```
is_failure(state) :=
    (1) error_message 설정됨 OR current_node == "error_response" OR 예외 전파
 OR (2) query_results == [] AND routing_intent == "data_query"
 OR (3) retry_count > 0
 OR (4) 산출물 생성 실패 (output_file 요청됐으나 None, 또는 unresolved 필드 존재)
```

- (1)·(4) → 트레이스 최상위 `severity: "error"`
- (2)·(3) → `severity: "warn"`
- **여러 기준에 동시 해당** 시 가장 높은 severity를 채택하고 `triggers: [...]`에 전부 기록

### 6.4 배선 지점 — 단일 데코레이터 (대칭 보장)

**실측된 위험**: 그래프 실행 진입점이 6곳이다
(`src/api/routes/query.py`의 `ainvoke` 3 + `astream_events` 2, `src/main.py:57`).
여기에 개별 배선하면 **비대칭이 반복 원인**이 된다(CLAUDE.md「단일/멀티 경로 대칭」).

→ **`src/graph.py`의 `add_node` 호출을 감싸는 단일 헬퍼**로 일괄 적용한다.

```
graph.add_node("query_generator", partial(query_generator, ...))
                    ↓
graph.add_node("query_generator", traced(partial(query_generator, ...), name="query_generator"))
```

- 데코레이터가 노드 진입/이탈·경과시간·반환 델타 요약·예외를 자동 기록
- 노드 개별 수정 0건 → 신규 노드도 자동 편입
- 진입점에서는 **덤프 트리거 1줄만** 배선(요청 종료 시 `flush_if_failed(state)`)

### 6.5 출력 포맷

`logs/trace/YYYY-MM-DD/<request_id>.jsonl` — 한 줄 = 한 단계

```json
{"ts":"2026-08-19T14:03:11.482+09:00","request_id":"a1b2c3d4","thread_id":"sess-88",
 "step":5,"node":"query_executor","level":"ERROR","event":"node.exception",
 "elapsed_ms":842.3,"reason":"db2_sql_error",
 "payload":{"db_id":"polestar_b0","sql_hash":"9f3c...","error":"SQLCODE=-206"}}
```

- 첫 줄은 요약 헤더: `{"kind":"summary","severity":"error","triggers":["exception","retry"],
  "user_query":"...","total_ms":12043,"node_path":["context_resolver","input_parser",...]}`
- SQL 원문은 담지 않고 **해시 + `logs/sql/`의 타임스탬프 참조**로 연결 (중복 저장 회피)
- 파일 권한 `0600`, 마스킹 적용 후 기록

### 6.6 Success Criteria

- [ ] 정상 요청 처리 후 `logs/trace/`에 **파일이 생성되지 않는다**
- [ ] SQL 오류로 실패한 요청 1건 후, 해당 `request_id.jsonl`에 **모든 노드**의 단계가
      순서대로 존재하고, 마지막 ERROR 단계에 `reason`·`payload.error`가 있다
- [ ] 쿼리 0건 요청 후 `severity: "warn"`, `triggers: ["zero_rows"]` 파일이 생성된다
- [ ] 재시도 후 성공한 요청도 `warn` 트레이스가 남는다 (원인 추적용)
- [ ] 트레이스 수집 오버헤드가 요청당 5ms 미만 (벤치 테스트로 측정·단언)
- [ ] 링버퍼가 200단계에서 bound된다 (무한 증가 없음)
- [ ] 트레이스 기록 실패 시 요청 처리는 정상 완료된다
- [ ] JSONL 스키마 계약 테스트가 필드명·타입을 고정한다
- [ ] 전체 테스트 스위트 무회귀 + `arch_check --ci` 0

---

## 7. 모듈 C — `synonym-set`

### 7.1 Objective

`"vcore, cpu, core은 동의어이다. 캐시에 등록하라."` 같은 **앵커 없는 대칭 동의어 집합**을
사용자가 자연어로 Redis에 등록할 수 있게 한다. 사용자는 운영자다.

### 7.2 왜 기존 `add-synonym`으로 안 되는가 (실측)

기존 액션은 `target_column` 앵커를 요구한다:
```
"hostname에 '서버호스트' 유사 단어를 추가해줘"  →  anchor=hostname, words=["서버호스트"]
```
요청 프롬프트에는 앵커가 없다. `vcore`·`cpu`·`core` 셋이 **대등**하다.
`src/prompts/cache_management.py`의 파싱 프롬프트에 대칭 집합 개념이 아예 없어
LLM이 임의로 하나를 앵커로 골라 나머지를 종속시킨다(비결정적).

### 7.3 설계 — 라우팅 경로 (사용자 확정: `cache_management` 확장)

```
semantic_router  →  routing_intent: "cache_management"
                 →  nodes/cache_management.py
                      action: "add-synonym-set"
                      words: ["vcore","cpu","core"]
                      anchor: null
                 →  Redis 대칭 등록
```

**deep agents는 사용하지 않는다.** 근거: deepagents 경로는 옵트인
(`enable_deepagent_orchestration`)이라 기본 경로에서 미작동한다. D-128에서도 같은 이유로
deepagents 대신 노드 내 자체 루프를 채택한 선례가 있다.

**C-1. 결정적 선파서** (`src/utils/synonym_set_parser.py`) — **1차 경로**

```
패턴: <단어>(, <단어>)+ [은|는|이|가] [서로] (동의어|유사어|같은 말|동일한 의미)
    + 등록 동사(등록|추가|저장|캐시에)
```
- 단어는 영문·한글·숫자·언더스코어·하이픈 허용, 각 1~64자, 집합 크기 2~20
- 매칭되면 **LLM 호출 없이** 확정 → 비결정성 원천 차단
- 미매칭 시에만 LLM 파싱으로 폴백

**C-2. 파싱 프롬프트 확장** (`src/prompts/cache_management.py`) — **폴백 경로**

`add-synonym-set` 액션과 판별 기준을 추가하고, 출력 스키마에 `anchor: null` 허용을 명시.
기존 `add-synonym`(앵커 있음)과의 구분 기준을 예시로 못 박는다.

**C-3. 저장 — 앵커 자동 추론** (2026-08-19 사용자 확정: 안 (i))

`RedisCache.add_global_synonym(column_name, words)`는 앵커를 요구한다. 집합 원소 중
**실제 스키마에 존재하는 것**을 앵커로 채택하고 나머지를 유사어로 등록한다.

**앵커 후보 소스** (우선순위 순, 실측 확인)

| 순위 | 소스 | API |
|---|---|---|
| 1 | 활성 DB 스키마의 실제 컬럼명 | `cache_mgr.get_schema(db_id)` → `tables[].columns[].name` |
| 2 | 전역 유사어 사전의 키(= 이미 앵커로 쓰이는 컬럼명) | `load_global_synonyms_full()` (`synonyms:global`) |
| 3 | EAV NAME 값 | `load_eav_name_synonyms()` (`synonyms:eav_names`) |

**판정 규칙 (결정적)**

```
후보 = {원소 ∈ 집합 | 원소가 위 3개 소스 중 하나에 존재}
|후보| == 1  →  앵커 확정, 나머지 원소를 words로 등록
|후보| == 0  →  등록하지 않고 되묻기 ("어느 컬럼의 유사어인가요?")
|후보| >= 2  →  등록하지 않고 되묻기 (앵커 모호 — 임의 선택 금지)
```

예: `{vcore, cpu, core}`에서 `cpu`가 EAV NAME으로 존재하면
→ anchor=`cpu`, words=`[vcore, core]`

**등록 경로는 기존 `_handle_add_synonym`(`src/nodes/cache_management.py:722`)과 동일**하다
— 글로벌 사전 등록 + 활성 DB 중 해당 컬럼을 가진 DB의 synonyms 동기화. 즉 신규 저장 로직은
**앵커 추론 함수 하나**뿐이고, Redis 키 스키마는 무변경이다(D-019·D-051 준수).

> **기각한 대안 (ii) 대칭 집합 저장소 신설**: Redis `synonym:sets` 키를 신설해 집합을 그대로
> 저장하는 안. 요건 문구에는 더 충실하지만 `schema_analyzer`·`field_mapper`·`query_generator`
> 매칭 경로 전반에 새 단을 **대칭 주입**해야 하고(CLAUDE.md 단일/멀티 경로 대칭), Redis 키
> 스키마 신설이 D-019·D-051 캐시 구조 불변 원칙과 충돌한다. 작업량·회귀 위험이 수 배 크다.

**C-4. 오염 방지 가드** (CLAUDE.md「LLM 자동 등록은 오염 자기강화 루프 위험」)

- 쓰기 직전 결정적 검증: 집합 크기 2~20, 원소 길이 1~64, 중복 제거, 공백·특수문자 거부
- **등록 결과를 사용자에게 명시 응답**: 어떤 앵커에 어떤 단어가 붙었는지 전문 출력
- 기존 등록과 충돌(같은 단어가 다른 앵커에 이미 존재) 시 **침묵 병합 금지** — 충돌 사실을
  응답에 노출하고 사용자 확인을 받는다

### 7.4 Success Criteria

- [ ] `"vcore, cpu, core은 동의어이다. 캐시에 등록하라."` 입력 시 Redis에 등록되고,
      응답에 등록 내역(앵커·단어)이 명시된다
- [ ] 결정적 선파서가 **LLM 호출 없이** 위 문장을 파싱한다 (LLM mock 호출 0회 단언)
- [ ] 표현 변형 5종("~는 같은 말이야", "~를 유사어로 등록해줘" 등)이 모두 파싱된다
- [ ] 앵커 후보가 0개(미존재) 또는 2개 이상(모호)이면 되묻기 응답이 나가고,
      **아무것도 등록되지 않는다** (임의 앵커 선택 금지)
- [ ] 집합 크기 1 또는 21 이상, 빈 문자열, 64자 초과 원소는 거부되고 사유가 응답된다
- [ ] 기존 `add-synonym`(앵커 있는 요청) 동작이 바뀌지 않는다 (회귀 테스트)
- [ ] 등록 후 실제 질의에서 해당 동의어로 컬럼이 매칭된다 (통합 테스트)
- [ ] 충돌 시 침묵 병합하지 않는다
- [ ] 전체 테스트 스위트 무회귀 + `arch_check --ci` 0

---

## 8. 리스크와 완화

| 리스크 | 영향 | 완화 |
|---|---|---|
| 트레이스 수집이 지연을 늘림 | 응답시간 목표(단순 <10s) 위협 | 링버퍼 append만, 직렬화는 덤프 시점에만. 5ms 예산을 벤치 테스트로 단언 |
| 진입점 6곳 중 일부에만 배선 | 특정 경로에서 트레이스 누락 (반복 실수 유형) | `add_node` 데코레이터로 일괄 적용 + 경로별 발동 단언 테스트 |
| 트레이스에 민감 데이터 유출 | 보안 | 기존 마스킹 규약 재사용, 파일 권한 0600, SQL 원문 대신 해시 |
| 동의어 오염 자기강화 | 검색 품질 저하 | 쓰기 지점 결정적 차단 + 등록 내역 명시 응답 + 충돌 노출 |
| `cleanup_old_logs` 배선 누락 | 디스크 무한 증가 (D-083 선례) | 배선 후 호출부 grep 실측을 완료 조건에 포함 |
| 설정 필드 추가로 카탈로그 카운트 테스트 실패 | CI 적색 | 신규 `ObservabilityConfig` 5필드로 18그룹/243필드 → 19그룹/248필드. 카운트 단언을 같은 커밋에서 갱신(계획서 T0) |

---

## 9. Open Questions

**~~Q1~~ (2026-08-19 사용자 확정으로 해소)**
동의어 집합의 Redis 저장 방식 → **(i) 앵커 자동 추론**으로 확정. §7.3 C-3에 설계를 반영했고,
(ii) 대칭 집합 저장소 신설은 기각 근거와 함께 같은 절에 기록했다.

**~~Q2~~ (2026-08-19 실측으로 해소)**
~~`noise_gate/`의 SQL 실행 경로가 모듈 A 범위인지~~ → **해소**: `noise_gate`는 자체 실행기를
갖지 않고 `DBRegistry.get_client(db_id)` → `client.execute_sql()`로 기존 클라이언트를
재사용한다(`polestar_noise_context.py:525·534·551`, `topology_loader.py:18` 주석 확인).
따라서 `sql_file_logger`가 **이미 커버**하며 추가 작업이 없다. 실제 미커버는 `mcp_server`
단독이다.

**Q3 (비블로킹)**
트레이스 보존 14일이 적절한지. 실패 건만 쌓이므로 용량은 작을 것으로 예상하나, 실사용
빈도를 모른다. **기본**: 14일로 시작하고 1개월 후 실측해 조정.

**Q4 (비블로킹)**
`logs/sql/`을 날짜별 단일 파일로 유지할지, DB별로 분리할지. **기본**: 날짜별 단일 유지
(현행과 동일, 레코드 헤더에 `-- DB:` 라인이 이미 있어 grep으로 분리 가능).

---

## 10. 다음 단계

1. ~~`planning-and-task-breakdown` → `tasks/plan.md` + `tasks/todo.md` 생성~~
   → **완료 (2026-08-19)**. 11개 태스크(T0~T10) + 체크포인트 4개. 계획서 `tasks/plan.md` 참조
2. Q1 결정 반영 → 이 문서 §7.3 확정 + `tasks/todo.md` T9 확정 (**미해결 — T9 착수 전 필요**)
3. `docs/02_decision.md`에 D-140(sql-file-log)·D-141(failure-trace)·D-142(synonym-set) 등재
   — 번호는 `## D-` 헤더와 「변경 이력」 표를 모두 grep해 실제 최댓값+1로 재확인
4. 빌드 순서대로 `incremental-implementation` + `test-driven-development`
   (T0 → Phase 1 → Phase 2, Phase 3은 병렬 가능)
