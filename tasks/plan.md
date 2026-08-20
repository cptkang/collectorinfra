# Implementation Plan: 운영 로깅 강화 + 동의어 집합 등록

> **스펙**: `SPEC-ops-logging-and-synonym-set.md` (2026-08-19 승인 대기)
> **태스크 목록**: `tasks/todo.md`
> **상태**: **구현 완료** (2026-08-19). T0~T10 전부 랜딩·검증 완료.
> Q1 확정 = **(i) 앵커 자동 추론**. 결과는 이 문서 말미 「구현 결과」 참조.

---

## Overview

추가 요건 3건을 3개 모듈로 분해해 구현한다. 세 모듈 모두 **기존 구현이 있는 델타 작업**이며,
공통으로 "기존 응답·라우팅 동작을 바꾸지 않는다"(관측 전용)는 제약을 갖는다.

| Module | 실작업 |
|---|---|
| `sql-file-log` | 로거 출력 경로 이전 + `mcp_server` 1곳 편입 + 보존 정리 배선 |
| `failure-trace` | **신규** — 레벨 규약 + 요청 스코프 수집기 + 실패 판정 + JSONL 덤프 + 그래프 배선 |
| `synonym-set` | 결정적 선파서 + `add-synonym-set` 액션 + Redis 대칭 등록 |

---

## Architecture Decisions

### AD-1. 설정은 신규 `ObservabilityConfig` 그룹 1개로 모은다 (T0)

로깅 관련 신규 설정 5개를 기존 그룹에 흩지 않고 `OBS_` prefix 단일 그룹으로 만든다.

- **근거**: D-129 설정 카탈로그는 `AppConfig` 인트로스펙션이 SSOT이고,
  `tests/test_api/test_settings_catalog.py::test_t2_group_and_field_counts`가
  **그룹 18개 / 243필드를 정확히 단언**한다(2026-08-19 실측). 필드를 여러 태스크에
  나눠 추가하면 이 단언을 매번 고쳐야 하므로, **한 태스크에서 한 번에** 추가한다.
- **파급**: 그룹 18→19, 필드 243→248. 카운트 단언과 `.env.example`을 같은 커밋에서 갱신한다
  (T1 커버리지 게이트가 `카탈로그 ⊇ .env ∪ .env.example − 시크릿`을 실파싱 단언 —
  파일에만 키를 넣으면 CI가 적색이 된다).

### AD-2. 트레이스 배선은 `StateGraph` 프록시 1줄로 (T6)

노드 20여 개에 개별 데코레이터를 붙이지 않고, `graph.py`의
`graph = StateGraph(AgentState)`를 얇은 프록시로 감싼다.

```python
graph = _TracedGraph(StateGraph(AgentState))   # ← 변경은 이 1줄
# add_node만 가로채 traced(fn, name)로 감싸고, 나머지 속성은 __getattr__으로 위임
```

- **근거**: 그래프 실행 진입점이 **6곳**(`src/api/routes/query.py`의 `ainvoke` 3 +
  `astream_events` 2, `src/main.py:57`)이고, `add_node` 호출은 **20여 곳**이며 상당수가
  플래그 조건부(`use_deep_agent`, `enable_semantic_routing`, `fault_dx_enabled` 등)다.
  개별 배선은 CLAUDE.md가 반복 원인으로 지목한 "단일/멀티 경로 비대칭"을 그대로 재현한다.
- **효과**: 조건부 노드·신규 노드가 **자동 편입**되고, 노드 파일 수정은 0건이다.
- **검증 부담**: 프록시가 `compile()`·`add_edge()`·`add_conditional_edges()`를 정확히
  위임하는지 계약 테스트로 고정한다.

### AD-3. 수집은 상시, 파일 쓰기는 실패 시에만 (T4·T5)

정상 경로 디스크 비용을 0으로 유지한다. 콘솔 출력 레벨(`AppConfig.log_level`)과
**트레이스 수집 레벨은 독립** — 콘솔이 INFO여도 버퍼는 DEBUG까지 담는다(사후 진단 목적).

### AD-4. 동의어 등록은 결정적 선파서가 1차, LLM은 폴백 (T8)

CLAUDE.md「LLM 자동 등록은 오염 자기강화 루프 위험 — 쓰기(등록) 지점에서 결정적 차단」.
`"A, B, C는 동의어"` 형태는 정규식으로 확정해 **LLM 호출 0회**로 처리한다.

### AD-6. 앵커는 결정적으로 추론하고, 모호하면 등록하지 않는다 (T9)

집합 원소 중 **실제 스키마에 존재하는 것**을 앵커로 채택한다. 후보 소스는 3개(활성 DB
스키마 컬럼명 → 전역 유사어 사전 키 → EAV NAME 값).

```
|후보| == 1  →  앵커 확정, 나머지를 words로 등록
|후보| == 0  →  되묻기 (등록 0건)
|후보| >= 2  →  되묻기 (앵커 모호 — 임의 선택 금지, 등록 0건)
```

- **근거**: 후보가 여럿일 때 임의로 하나를 고르면 LLM 비결정성을 코드로 옮기는 것에 불과하다.
  오등록은 조용히 검색 품질을 갉아먹고 자기강화된다(CLAUDE.md 오염 루프).
  되묻기는 1턴을 더 쓰지만 오등록 0을 보장한다.
- **등록 경로 재사용**: 앵커가 정해지면 기존 `_handle_add_synonym`
  (`src/nodes/cache_management.py:722`)과 동일하게 글로벌 사전 등록 + 활성 DB 동기화.
  신규 로직은 **앵커 추론 함수 하나**뿐이다.

### AD-5. `mcp_server`는 코드 중복을 허용한다 (T2)

`mcp_server`는 별도 venv·별도 프로세스라 `src.utils` import가 불가하다(D-139 패키지 경계).
`mcp_server/mcp_server/sql_log.py`에 미니 로거를 두고 **같은 `logs/sql/`에 append**한다.
동시 append는 `O_APPEND` 원자성에 의존하므로, 레코드를 **한 번의 `write()` 호출**로 기록한다.

---

## Dependency Graph

```
T0 (ObservabilityConfig 설정 그룹)
 │
 ├─── Phase 1: sql-file-log ───────────────┐
 │    T1 경로 이전 → T2 mcp_server → T3 보존정리
 │                                          │
 └─── Phase 2: failure-trace ──────────────┤
      T4 레벨규약+수집기 → T5 판정+덤프 → T6 그래프배선 → T7 계약·성능
                                            │
      Phase 3: synonym-set (T0 불필요·완전 독립) ─── 병렬 가능
      T8 결정적 파서 → T9 액션+Redis저장 → T10 통합검증
```

**병렬화**
- **Phase 3은 Phase 1·2와 완전 독립** — 코드 접점 0. 다른 세션/에이전트가 동시 진행 가능
- Phase 1과 Phase 2는 T0 이후 병렬 가능하나, 둘 다 `logs/` 레이아웃과 보존 정책을 공유하므로
  **T3(보존 정리)에서 합류**한다. 순차 진행을 권장
- **반드시 순차**: T0 → 나머지 전부 (설정 필드가 선행되어야 함)

---

## Task List

### Phase 0: Foundation
- [x] T0: `ObservabilityConfig` 설정 그룹 신설 (5필드)

### Checkpoint A: 설정 기반

### Phase 1: sql-file-log
- [x] T1: SQL 로그 출력 경로를 `logs/sql/`로 이전
- [x] T2: `mcp_server` SQL 실행 경로 편입
- [x] T3: SQL 로그·트레이스 보존 정리 배선

### Checkpoint B: SQL 로그 완결

### Phase 2: failure-trace
- [x] T4: 로그 레벨 규약 + 요청 스코프 수집기
- [x] T5: 실패 판정 술어 + JSONL 덤프
- [x] T6: 그래프 프록시 배선 + 진입점 flush
- [x] T7: JSONL 계약 테스트 + 성능 예산 벤치

### Checkpoint C: 실패 트레이스 완결

### Phase 3: synonym-set (Phase 1·2와 병렬 가능)
- [x] T8: 결정적 동의어 집합 선파서
- [x] T9: `add-synonym-set` 액션 + Redis 대칭 등록
- [x] T10: 등록→질의 매칭 통합 검증

### Checkpoint D: 전체 완료

---

## Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| `_TracedGraph` 프록시가 LangGraph 내부 동작을 깨뜨림 | **High** — 그래프 전체 불능 | T6에서 프록시 계약 테스트를 **먼저** 작성(`compile`/`add_edge`/`add_conditional_edges` 위임 단언). 실패 시 폴백 = `add_node` 호출부 20곳 명시 배선 + 경로별 발동 단언 |
| 설정 카탈로그 카운트 단언 미갱신으로 CI 적색 | Med | T0의 완료 조건에 `test_t2_group_and_field_counts` 갱신을 **명시 포함** |
| 트레이스 수집이 응답시간 목표(단순 <10s) 잠식 | Med | T7에서 5ms 예산을 벤치로 **단언**. 초과 시 수집 항목을 줄이고 스펙은 완화하지 않음 |
| `cleanup_old_logs` 배선 누락 → 디스크 무한 증가 | Med | D-083 선례(구현·설정은 있으나 호출부 0건이라 무효). T3 완료 조건에 **호출부 grep 실측**을 포함 |
| 트레이스에 민감 데이터 유출 | Med | T5에서 기존 마스킹 규약 재사용, 파일 권한 `0600`, SQL 원문 대신 해시+`logs/sql/` 참조 |
| 동의어 오염 자기강화 | Med | T9 쓰기 직전 결정적 검증 + 등록 내역 명시 응답 + 충돌 시 침묵 병합 금지 |
| `mcp_server` 동시 append 인터리브 | Low | 레코드를 단일 `write()` 호출로 기록(현행 구현이 이미 그러함). T2에서 동시 쓰기 테스트로 확인 |
| ~~Q1 미결로 T9 재작업~~ | — | **해소** — (i) 앵커 자동 추론 확정(2026-08-19) |

---

## Definition of Done (전 태스크 공통)

태스크별 acceptance criteria에 더해, 모든 태스크가 아래를 통과해야 완료로 친다.

- [x] `pytest` 전체 통과 (기존 대비 **무회귀** — 실패 건수가 늘지 않음)
- [x] `python scripts/arch_check.py --ci` 위반 0
- [x] `make lint` (ruff + mypy) 통과
- [x] 응답 내용·그래프 라우팅 **비트동일** (관측 전용 원칙)
- [x] 신규 코드 라인 커버리지 85% 이상
- [x] `docs/02_decision.md` 등재 (모듈 완료 시점)

> **사전존재 실패 주의**: `tests/test_api/test_routes.py` 6건(MagicMock config),
> `test_multiturn` capability addendum 1건은 로컬 `.env` 기인으로 D-127 시점부터 존재한다.
> 무회귀 판정 시 **기준선을 먼저 측정**하고 비교한다(CLAUDE.md「클린 기준선은 `git stash`가
> 아니라 `git worktree add <dir> HEAD`」).

---

## Open Questions

**~~Q1~~ — 해소 (2026-08-19 사용자 확정)**
동의어 집합의 Redis 저장 방식 = **(i) 앵커 자동 추론**. T9는 M 규모로 확정되고 재분해가
불필요하다. `add_global_synonym` 재사용, Redis 키 스키마 무변경. 설계는 스펙 §7.3 C-3 참조.

**Q3·Q4 (비블로킹)** — 트레이스 보존 14일, SQL 로그 날짜별 단일 파일. 기본값으로 진행하고
1개월 후 실측해 조정한다.

---

## 구현 결과 (2026-08-19)

### 랜딩한 것

| Module | 신규 파일 | 수정 파일 | 신규 테스트 |
|---|---|---|---|
| `sql-file-log` | `src/utils/log_retention.py`, `mcp_server/mcp_server/sql_log.py` | `src/utils/sql_file_logger.py`, `mcp_server/mcp_server/{db,server}.py`, `src/{main.py,api/server.py}` | 26 |
| `failure-trace` | `src/observability/{__init__,levels,trace_collector,trace_writer,graph_proxy}.py` | `src/graph.py`(1줄), `src/api/middleware/audit_middleware.py`, `src/main.py`, `scripts/arch_check.py`, `tests/test_orchestration/test_deep_agent_wiring.py` | 101 |
| `synonym-set` | `src/utils/synonym_set_parser.py` | `src/nodes/cache_management.py`, `src/prompts/cache_management.py`, `src/routing/semantic_router.py` | 69 |
| 공통 (T0) | — | `src/config.py`, `src/api/settings_catalog.py`, `.env.example`, `tests/test_api/test_settings_catalog.py` | — |

**신규 테스트 합계 196건.** 설정은 19그룹/251필드(종전 18/243 + `ObservabilityConfig` 5 + 사전 누락분 3).

### 계획 대비 달라진 설계 3건

1. **트레이스 flush 배선이 5곳 → 1곳으로 축소** (계획 T6). 초안은 진입점마다 `flush_if_failed(state)`를
   배선하는 것이었으나, 진입점 4곳의 종료 지점 구조가 제각각(SSE generator는 return이 여럿)이라
   누락 위험이 컸다. `traced`가 노드 진입 state에서 실패 신호를 관찰하도록 바꾸니
   호출부가 최종 state를 몰라도 되어, **모든 HTTP 요청이 지나는 `AuditMiddleware` 한 곳**으로
   수렴했다(CLI만 별도 1곳). 계획의 "대칭 확보" 목적을 더 강하게 달성한다.
2. **보존 정리를 `log_retention.py` 공용 유틸로 분리** (계획 T3). SQL·트레이스가 같은 성격이라
   한 모듈에서 처리하면 T5에서 정리 로직을 다시 만들 필요가 없다.
3. **실패 판정 술어를 `levels.py`에 배치** (계획은 T5). 레벨 규약과 같은 파일에 두는 편이
   응집도가 높고, 순수 함수라 T4에서 함께 테스트하는 것이 자연스러웠다.

### 계획에 없던 발견 (전부 수정 완료)

- **사전존재 실패 2건 해소**: 커밋 `b7ccc20`이 config 필드 3개를 추가하며 카탈로그 카운트 단언을
  갱신하지 않아 2주간 방치돼 있었다. 내 변경이 같은 단언을 건드려야 해서 함께 해소.
- **자기 회귀 1건 발생·수정**: 로그 정리의 `retention_days <= 0` 비교가 MagicMock config와
  `TypeError`를 내 기동 경로를 깨뜨렸다(test_routes 7 passed → 10 errors). 클린 worktree 대조로
  확정 후 `_as_days` 화이트리스트로 수정.
- **잠복 결함 1건 발견·수정**: `log_sql`의 `inspect.stack()[caller_depth]`가 얕은 스택에서
  `IndexError`를 던져 SQL 기록이 통째로 유실됐다. 단위 테스트 9건 전부 그린이었고 **스모크로
  실제 파일을 열어보다 발견**.
- **선파서 부분 수용 1건 차단**: `cpu;drop, memory`가 `drop, memory`로 잘려 매칭됐다(부정 케이스
  테스트가 검출, shipped 아님). 시작 경계 lookbehind로 해소.
- **자기 회귀 1건 추가 발견·수정**: `traced` 래퍼가 `functools.partial`을 감싸면서
  `test_deep_agent_wiring.py`의 배선 검증(D-062 `synthesize=True` 강제)을 **조용히 무력화**했다.
  개별 테스트로는 안 보였고 **`.env`를 맞춘 기준선 실패 집합 diff에서만** 드러났다.
  `__wrapped__` 노출 + 테스트 헬퍼 `inspect.unwrap()` 투과로 해소하고, 인트로스펙션 투과를
  계약 테스트 3건으로 고정.
- **검증 절차 오류 1건**: worktree 기준선에 `.env`/`.encenv`를 복사하지 않아 첫 대조가
  무효였다(기준선 822초 vs HEAD 115초 — 7배 격차가 신호였다). 이 실수는 `docs/18_known_mistakes.md`에
  이미 두 번 등재된 것으로 **3회째 재발**이라, 다음에는 문서가 아니라 worktree 생성 자동화로
  막으라는 권고를 함께 남겼다.

상세는 `docs/18_known_mistakes.md` 2026-08-19 4건 참조.

### 최종 검증 (2026-08-19, 동일 조건 대조)

| | 기준선 (`804b447` + `.env` 복사) | HEAD |
|---|---|---|
| 집계 | 40 failed · 3840 passed · 29 skipped · 5 errors (110s) | **38 failed · 4033 passed · 29 skipped · 5 errors** (113s) |
| 실패 집합 diff | — | **HEAD에만 있는 실패 0건** (회귀 0) |
| 개선 | — | 기준선에만 있는 실패 2건 = `test_settings_catalog` 사전존재 실패 해소 |

- `python scripts/arch_check.py --ci` → **exit 0**
- `cd mcp_server && pytest` → **182 passed, 2 skipped**
- passed 증가분 +193 = 신규 테스트

### 환경 제약 (측정 불가 항목)

- `ruff`·`mypy` 미설치 → `make lint` 실행 불가
- `pytest-cov`·`coverage` 미설치 → stdlib `trace`로 근사 측정 (`src/observability` **87.3%**, 기준 85% 충족)
- `playwright` 미설치 → `tests/e2e` 수집 불가(기준선도 동일, `--ignore=tests/e2e`로 대조)
