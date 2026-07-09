# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Infrastructure data query agent that converts natural language queries (Korean) into SQL, executes them against infrastructure databases via DBHub (MCP server), and returns results as natural language responses or filled Excel/Word templates.

Full requirements are in `spec.md`.
Architecture decisions and design rationale are documented in `docs/02_decision.md` — **team-lead agent must consult this file before making changes and update it when new decisions are made.**

## Architecture

**LangGraph state machine** with 7 nodes in sequence:

```
input_parser → schema_analyzer → query_generator → query_validator → query_executor → result_organizer → output_generator
```

- `query_validator` loops back to `query_generator` on failure (max 3 retries)
- `query_executor` loops back to `query_generator` on SQL error (with error context)
- `result_organizer` loops back to `query_generator` if data is insufficient

**State** is a `TypedDict` (`AgentState`) tracking user input, parsed requirements, DB schema/results, retry count, and output.

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Agent framework | LangGraph (≥0.2.0) |
| LLM | Claude or GPT via langchain-anthropic / langchain-openai |
| DB access | DBHub (MCP server, readonly) |
| Document processing | openpyxl (Excel), python-docx (Word) |
| API server | FastAPI + uvicorn (optional) |
| Checkpoint store | langgraph-checkpoint-sqlite (dev) / postgres (prod) |

## Key Constraints

- **Read-only DB access only** — agent must never generate INSERT/UPDATE/DELETE/DDL
- Generated SQL must be validated before execution (syntax, safety, referenced tables/columns exist, LIMIT clause for large queries)
- All query executions must be audit-logged
- Sensitive data (passwords, keys) must be masked in responses
- Query timeout: 30s max; max rows: 10,000
- Response time targets: simple queries <10s, complex queries <30s, document generation <60s

## Data Domains

The agent queries 5 infrastructure domains: servers, CPU metrics, memory metrics, disk metrics, network traffic metrics. Schema is discovered dynamically at runtime via DBHub's `search_objects`.

## Document Processing

- **Excel**: auto-detect header rows, fill data rows, preserve merged cells/formulas/formatting
- **Word**: detect `{{placeholder}}` patterns and table structures, fill data while preserving styles
- LLM performs semantic mapping between template field names and DB column names (e.g., "서버명" → `servers.hostname`)

## Development Phases

1. **Phase 1**: Natural language → SQL pipeline (LangGraph graph, DBHub integration, error handling)
2. **Phase 2**: Excel/Word template parsing and filling
3. **Phase 3**: Multi-turn conversation, human-in-the-loop query approval, template management, audit logging

## Multi-Agent Build System

`.claude/agents/` 디렉토리의 `.md` 파일로 에이전트를 정의하고, Claude Agent SDK로 실행합니다.

```
.claude/agents/
├── team-lead.md             # 오케스트레이터 (메인 에이전트)
├── requirements-analyst.md  # 요구사항 분석
├── research-planner.md      # 기술 조사 및 구현 계획
├── implementer.md           # 코드 구현
└── verifier.md              # 검증 및 테스트
agents/
└── run.py                   # 실행 스크립트
```

### 에이전트 구성 및 Phase

| Phase | Agent | 산출물 |
|-------|-------|--------|
| 1 | **requirements-analyst** | `docs/01_requirements.md` |
| 2 | **research-planner** | `plans/*.md` (영역별 계획서) |
| 3 | **implementer** | `src/`, `pyproject.toml` |
| 4 | **verifier** | `tests/`, `docs/verification_report.md` |

**team-lead**가 각 Phase의 산출물을 검토·승인한 후 다음 Phase로 진행합니다.

### 실행 방법

```bash
pip install claude-agent-sdk anyio
python -m agents.run              # 전체 (Phase 1~4)
python -m agents.run --phase 1    # 요구사항 분석만
python -m agents.run --phase 2    # +계획
python -m agents.run --phase 3    # +구현
```

## Clean Architecture 계층 규칙

의존성은 안쪽(domain)에서 바깥쪽(entry)으로만 향해야 한다.

```
domain → config/utils → prompts → infrastructure → application → orchestration → interface → entry
```

```bash
python scripts/arch_check.py              # 위반 검사
python scripts/arch_check.py --verbose    # 의존성 매트릭스 포함
python scripts/arch_check.py --ci         # CI 모드 (위반 시 exit 1)
```

Claude Code 스킬: `/arch-check` 로 호출 가능 (`.claude/skills/arch-check.md`)

## 실수 방지 및 의사결정 관리

### 에이전트 실수 이력 관리

에이전트가 작업 중 실수한 항목은 `CLAUDE.md`의 아래 "Known Mistakes" 섹션에 기록하여 동일 실수가 반복되지 않도록 한다.

- 실수 발생 시: 원인과 수정 내용을 즉시 기록
- 작업 시작 시: Known Mistakes 섹션을 확인하여 동일 패턴 재발 방지
- 형식: `[날짜] 실수 내용 — 원인 — 방지책`

### 의사결정 기록 (`docs/02_decision.md`)

프로젝트의 아키텍처·설계 의사결정은 `docs/02_decision.md`에 일원화하여 관리한다.

**작업 전 (필수)**:
1. `docs/02_decision.md`를 읽고 기존 결정 사항을 확인한다.
2. 수행할 작업이 기존 결정과 충돌하는지 검토한다.
3. **충돌이 발견되면 임의로 진행하지 말고 사용자에게 문의**하여 결정을 받는다.

**작업 후 (필수)**:
1. 작업 중 새로운 의사결정이 발생하면 `docs/02_decision.md`에 추가한다.
2. 기존 결정이 변경되었으면 해당 항목의 상태를 갱신한다.
3. 형식: 기존 `D-NNN` 번호 체계를 따른다 (결정일, 상태, 결정 내용, 근거, 대안).

---

## Known Mistakes (에이전트 실수 이력)

> 에이전트가 반복하지 말아야 할 실수 목록. 작업 시작 전 반드시 확인할 것.

| 날짜 | 실수 | 원인 | 방지책 |
|------|------|------|--------|
| 2026-03-23 | `.env`의 `list[str]` 필드를 쉼표 구분 문자열로 설정하여 pydantic-settings 파싱 에러 발생 | pydantic-settings는 복합 타입(list, dict)을 JSON으로 파싱함 | `.env`에서 `list[str]` 필드는 반드시 JSON 배열 형식(`["a","b"]`)으로 작성 |
| 2026-03-23 | `_schema_to_dict` 유틸 함수를 application 계층(nodes/)에 배치하여 infrastructure→application 역방향 의존 발생 | 함수의 계층 소속을 고려하지 않음 | 새 함수 작성 시 `scripts/arch_check.py` 로 계층 위반 검사 후 배치. 데이터 모델 변환 함수는 해당 모델이 있는 계층에 위치 |
| 2026-06-10 | `model_post_init`에서 `os.getenv()`로 환경변수를 읽어 systemd 서비스(EnvironmentFile 미설정)에서 값이 로드되지 않음 | pydantic-settings의 `env_file` 로딩은 `os.environ`에 주입하지 않아 `os.getenv()`로 접근 불가 | `model_post_init`에서 `os.getenv()` 대신 pydantic-settings `AliasChoices`를 사용하여 `.env` 파일에서 직접 읽도록 구현 |
| 2026-06-10 | "가용성 상태가 비정상인" 질의에서 value를 숫자로 변환하지 않거나 특정 값(1)으로만 매핑 | avail_status 값 매핑 규칙 부재. 알 수 없음도 비정상임 | `input_parser.py` 규칙 13: 정상→`= 0`, 비정상/정상이 아닌 모든 표현→`!= 0`. 각 polestar DB 프로필 query_guide에도 동일 규칙 명시 |
| 2026-06-10 | 공동존 폴스타(김포/여의도)에서 "장비명", "서버명" 필터를 hostname으로 매핑하여 잘못된 결과 반환 | 글로벌 유사어에 서버명→HOSTNAME 매핑 있어 `filter_conditions.field = "hostname"`으로 생성됨. query_generator가 이를 그대로 사용 | `polestar_cm_gp.yaml`, `polestar_cm_yd.yaml` query_guide에 `[filter_conditions 필드명 재매핑]` 섹션 추가: field="hostname"이더라도 원문이 서버이름/장비명이면 `r.name` 컬럼 사용 |
| 2026-06-10 | "OS파라미터", "커널 파라미터" 등 표현이 OSParameter EAV 항목으로 인식되지 않거나, 조회 시 빈 값 반환 | (1) known_attributes에 "커널 파라미터"(띄어쓰기 포함), "sysctl" 등 동의어 부재 (2) OSParameter는 LOB 컬럼이라 stringvalue_short 사용 시 빈 값 반환됨 | 모든 polestar DB 프로필 known_attributes OSParameter synonyms에 커널/시스템 파라미터 동의어 추가. query_guide에 `[OSParameter LOB 값 조회 주의]` 섹션과 stringvalue 사용 예시 쿼리 추가. **주의: query_guide 예시 SQL에 `AND cc.is_lob = 1` 조건 절대 포함 금지 — 실제 DB에서 is_lob 값이 다를 수 있어 0건 반환됨** |
| 2026-06-10 | avail_status 필터가 GROUP BY 쿼리에 적용되지 않거나, output_generator가 "1=정상"으로 환각 | (1) POLESTAR_QUERY_GENERATOR_SYSTEM_TEMPLATE에 filter_conditions 적용 방법 미비 (2) output_generator 프롬프트에 avail_status 값 의미 없음 | query_generator.py에 `[filter_conditions 적용]` 섹션 추가(HAVING 패턴 포함). output_generator.py에 "avail_status: 0=정상, 0이 아닌 값=비정상" 도메인 지식 추가 |
| 2026-06-11 | 처리 현황 유사어 매핑(D-033)에서 일반 컬럼 매칭이 대량 출력 발생 | `column_synonyms`는 DB 전체 테이블×컬럼(수백 키) 규모인데 bare 컬럼명만으로 SQL을 검색하여 `name`/`id` 등 공통 컬럼명이 모든 테이블 키와 중복 매칭됨 | UI 표시용 사전 역조회는 (1) bare 컬럼명 기준 중복 제거 (2) 사용자 용어와 매칭된 항목만 포함 (3) 항목 수 상한 적용. 사전류 데이터를 순회하는 로직 설계 전 사전의 실제 규모(전 테이블 자동 생성 여부)를 먼저 확인할 것 |
| 2026-06-17 | `build_deep_agent`가 `create_deep_agent(..., instructions=...)`로 호출 — 실제 deepagents 0.6.10 시그니처에는 `instructions` 인자가 없어(올바른 인자는 `system_prompt`) 런타임 TypeError 발생 | 계획서(Plan 49 §4.3) 의사코드의 `instructions=`를 검증 없이 그대로 구현. 외부 패키지 API를 실측하지 않고 문서/계획의 표기를 신뢰 | 외부 패키지 함수 호출 시 `inspect.signature()`로 **실제 시그니처를 실측**한 뒤 인자명을 확정. 계획서 의사코드는 참고용이며 실 API와 다를 수 있음. deepagents `create_deep_agent`는 `system_prompt`(=instructions 아님) 사용 |
| 2026-06-17 | deepagents 도구 결과를 `messages`의 ToolMessage(요약·상한 적용)에서 재파싱하려다 데이터 손실 위험 | `create_deep_agent` 반환 state에 도구 결과 전용 키가 없고(`['files','messages']`만), ToolMessage content는 토큰 폭증 방지로 truncate된 직렬화본임을 모르고 설계 | 도구 래퍼에 **원본 결과 수집기(collector)** 를 두어 truncate 전 결과를 별도 보관 후 최종 응답(FabriX result_aggregator) 생성에 사용. 외부 에이전트 프레임워크의 state 표면은 최소 구동으로 **실측**한 뒤 의존 |
| 2026-06-17 | `OrchestratorConfig(base_url=...)`로 만든 테스트 config가 로컬 `.env`의 `ORCHESTRATOR_PROVIDER=gemini`를 상속해 provider 의도와 불일치(테스트 실패) | `OrchestratorConfig(BaseSettings)`는 `env_file=['.env','.encenv']`(env_prefix=`ORCHESTRATOR_`)를 읽으므로 명시하지 않은 필드는 `.env` 값을 상속 | `BaseSettings` 서브클래스를 테스트에서 생성할 때 **검증 대상 필드(provider 등)를 명시**하여 `.env` 누수 차단. 환경 비의존 단위 테스트는 OS env뿐 아니라 `.env` 파일 누수도 고려 |
| 2026-06-24 | 도움말 등 LLM 응답이 SSE 토큰 스트리밍되지 않고 한 번에 출력됨 | 노드가 `llm.ainvoke()` 호출 — `_agenerate`(단일 호출) 경로라 `astream_events`가 `on_chat_model_stream` 토큰 이벤트를 안 냄. 또한 orchestration 경로는 SQL 생성·최종 응답이 같은 노드(`agent_orchestrator`)에서 일어나 노드명 필터로는 구분 불가 | 최종 사용자 응답 노드는 `src/llm.py::astream_text(..., tags=[USER_RESPONSE_TAG])`로 `.astream()` 호출. SSE는 **노드명이 아닌 태그**로 토큰을 거름. 복합 질의 토큰 인터리빙은 `done` 이벤트의 권위 `response`로 프론트에서 보정. 상세: `docs/02_decision.md` D-009 |
| 2026-06-25 | Plan 50 B7 의사코드대로 `ChatOpenAI(model_kwargs={"extra_body": ...})`로 Qwen no-think를 부착하면 `UserWarning("Parameters {'extra_body'} should be specified explicitly")` 발생 + langchain_openai가 extra_body를 model_kwargs에서 끌어내 우회 처리 | `ChatOpenAI`는 `extra_body`를 **전용 생성자 인자**로 가지며 model_kwargs 경유 전달을 비권장(경고). 계획서 의사코드(model_kwargs.extra_body)가 실제 API와 다름 | `inspect.signature`로 인자 실측 후 vLLM 확장 필드(extra_body)는 **전용 인자로 직접 전달**. 회귀 방지 `-W error::UserWarning` 단위 테스트 고정(test_control_plane_budget.py). 계획서 의사코드는 참고용 — 실 API 우선(2026-06-17과 동일 교훈) |
| 2026-06-25 | Plan 50 §6이 신규 결정을 D-039/D-040으로 적었으나 변경 이력 표에서 두 ID가 이미 다른 결정(orchestration 관찰성·replanner 수정)에 선점됨 → 번호 충돌 | 결정 ID가 형식 섹션 헤더가 아닌 변경 이력 표에서만 소비되어 추적 어긋남. 계획서 작성 시점 최신 ID 미확인 | 신규 결정 ID는 **변경 이력 표까지 grep**(`grep -oE "D-0[0-9]{2}"`)하여 실제 최댓값+1 부여. Plan 50 결정은 D-041(멀티턴 전파)·D-042(제어 평면 예산)로 부여, 계획 의도 라벨을 주석 명시. 사용자 확인 필요 |
| 2026-06-26 | `process_query`가 빈 결과(rows=[])일 때 진단 summary(서버 식별 실패·API 미응답·0건 등)가 `result_aggregator`→`output_generator`의 일반 "조건에 해당하는 …데이터가 없습니다" 문구로 덮어써져 실제 원인이 사라짐 | `_finalize_task`가 organized_data가 있으면 **무조건** output_generator로 최종화 — output_generator는 rows 비면 전역 query_targets 기반 일반 문구를 반환하여 결정적 subagent의 원인 메시지를 폐기 | `_finalize_task`에서 agent=="process_query" + rows 비어있음 + summary 존재 시 organized_data.summary를 그대로 노출(output_generator 우회). 결정적 subagent의 진단 메시지는 일반 빈-결과 문구로 덮지 말 것. 회귀 테스트: `test_result_aggregator.py::test_finalize_process_query_empty_keeps_diagnostic_summary` |
| 2026-06-26 | 멀티턴 단절을 백엔드(context_resolver/intent_planner)에서만 진단·수정(Plan 50 M1~M3)했으나, 실제 후속 턴이 항상 "1턴"으로 처리됨 — `turn_count>1` 게이트가 영영 안 켜져 수정이 전부 무효 | 진짜 단절은 **프론트엔드**: `app.js`의 텍스트 질의(`/query`, `/query/stream`) fetch body가 `{query}`만 보내고 `thread_id`를 누락(파일 질의만 전송). 백엔드 체크포인트·`add_messages` 리듀서는 정상이었음. 백엔드 turn 감지만 검증하고 **요청 경로 끝(프론트 전송)**을 안 봄 | 멀티턴/세션 기능 검증은 **요청 본문에 세션 식별자(thread_id)가 실제로 실리는지 프론트까지** 확인. `app.js` 두 fetch에 `if(currentThreadId) body.thread_id=currentThreadId` 추가. 단위 검증: AsyncSqliteSaver+add_messages 2턴 재현으로 백엔드 정상 확인 후 프론트 누락 격리 |
| 2026-06-29 | 조회 결과 일부 필드(CPU·메모리)가 null인 것을 "데이터에 속성 부재"로 단정하고 replanner 재시도를 막는 가드(D-063)로 대응 — 실제로는 데이터가 존재하는데 **SQL이 잘못 생성되어 못 가져온 것**이었음(증상 호도) | EAV 피벗(`GROUP BY platform_resource_id`)에 단일 서버 필터를 `WHERE c.name='...'`로 붙이면 그 술어가 server.Server 행에만 참이라 server.Cpus/server.Memory 행이 GROUP BY 전에 제거→CPU/메모리 NULL. null의 원인(생성 SQL)을 보지 않고 "데이터 없음"으로 가정 | **필드 null은 데이터 부재가 아니라 조회 SQL 오류일 수 있다** — null 진단 시 먼저 **생성된 SQL(D-039 처리현황에 노출)**을 확인. EAV 다중 resource_type 피벗+엔티티 필터는 WHERE가 아니라 **HAVING(집계 후 server.Server 행 기준)**으로. 수정: `prompts/query_generator.py`, `polestar_cm_gp/yd.yaml` query_example. 상세: `docs/02_decision.md` D-050(근본), D-063(가드는 효율 목적으로만 유효) |
| 2026-06-29 | "김포 ### 서버에 대한 프로세스 조회"가 실시간 프로세스 API가 아닌 DB 조회로 처리돼 `resource_type='process'` 행 환각 반환. "프로세스 리스트 조회"는 process_query로 가는데 "프로세스 조회"는 data_query로 감 | (1) input_parser에 지목 서버명을 hostname filter로 추출하는 규칙 부재 → `_resolve_hostname` 식별 실패 (2) intent_planner LLM이 "현재/실시간/리스트" 시간성 신호 부재 시 보수적으로 data_query 분류 — 프롬프트의 "애매하면 process_query" 규칙을 LLM이 안 따름 | LLM 분류 의존 의도는 **결정적 가드로 후처리 교정**. (a) input_parser 규칙 14: 단일 서버 지목 시 hostname filter 추출. (b) `intent_planner._coerce_process_intent`: data_query+프로세스 키워드+이력 신호 없음 → process_query 강제(폴백 포함). 라우팅이 키워드 유무("리스트")에 민감하면 프롬프트 강화만으론 부족 — 결정적 교정 병행. 상세: D-047 |
| 2026-06-30 | 은행 레거시 폴스타(b0, DB2)의 실시간 프로세스 조회가 0건 → 3번째 회차에서 DB 조회로 폴백. gp/yd(PostgreSQL)는 정상 | `build_hostname_sql`(서버명→hostname 해소)이 엔진 무관하게 PostgreSQL 방언(`LIMIT 1`+`polestar.` 스키마) 고정. DB2는 `LIMIT` 미지원·CURRENT SCHEMA 사용이라 b0에서 SQL 실패 → `resolve()`가 예외를 삼키고 None(D-046 graceful 폴백) → 원시 **서버명**이 hostname으로 API에 전달 → 0건. 예외를 삼키는 폴백이 진짜 원인을 가림 | **고정 SQL은 대상 DB 엔진별 방언 차이(LIMIT vs FETCH FIRST, 스키마 한정 방식)를 반드시 분기**. `resolve()`가 `get_domain_by_id(db_id).db_engine`로 엔진 조회 후 SQL 생성. 멀티 엔진(PostgreSQL+DB2) 환경에서 한 엔진만 검증하면 다른 엔진 회귀를 놓침 — 엔진별 테스트 고정. 예외 삼키는 폴백은 실패 SQL·engine을 로그에 남겨 원인 가시화. 상세: D-053 |
| 2026-06-30 | 위 b0 0건의 **실제 1차 원인은 방언이 아니라 db_id 라우팅 누락**이었음 — 코드만 보고 DB2 방언을 먼저 고쳤으나, 라이브 진입 로그를 찍어보니 `db_id=None`(=base_url·hostname 해소 단계 도달조차 못함) | `_resolve_db_id._LOCATION_DB_HINTS`에 김포/여의도만 있고 **은행(b0) 신호 누락** → "은행 폴스타 …" 첫 턴 질의가 위치 힌트 폴백에서 db_id 미식별 → 조기 0건. 방언 버그는 그 **뒤 단계**라 실행될 기회조차 없었음 | **0건/실패 진단은 추정으로 안쪽 단계부터 고치지 말고, 진입·게이트별 로그로 어디서 끊기는지부터 확정**. 결정적 라우팅 보조표(`_LOCATION_DB_HINTS`)에 신규 DB(b0) 추가 시 **위치 신호도 함께 등록**(domain_config alias만으론 첫 턴 폴백 경로를 못 메움). 새 DB를 프로세스 조회에 편입할 때 체크리스트: ①위치 힌트 ②런타임 `.env` base_url ③엔진 방언. 상세: D-053 |
| 2026-06-30 | 멀티턴 후속 "해당 서버의 프로세스 리스트"가 db_id=None으로 펑 — hostname(previous_entities)은 승계됐는데 db_id(previous_db_ids)만 소실 | `agent_orchestrator`가 최종 state로 `{task_plan, task_results, current_node}`만 반환 → subagent가 isolated에 쓴 `active_db_id`/`target_databases`가 top-level state로 안 올라감 → 다음 턴 `context_resolver._extract_previous_db_ids`가 빈 값 읽음. 반면 query_results는 result_aggregator가 승격(D-047)해 previous_entities는 살아남는 **비대칭**이 단서 | 멀티턴 승계가 일부 신호(hostname)만 되고 일부(db_id)는 안 되면, **각 신호의 top-level state 승격 경로가 대칭인지** 확인. orchestration 경로는 subagent isolated 결과를 자동 머지하지 않으므로, 다음 턴에 필요한 필드는 **finalizer(result_aggregator)에서 명시적으로 top-level 승격**해야 함(`_collect_db_promotion`). 상세: D-053 |
| 2026-07-01 | 후속 턴 "해당 서버의 프로세스"에서 hostname이 `previous_server`(플레이스홀더)로 들어가 0건 | `input_parser` 규칙 14가 "특정 서버 지목 시 hostname 추출"을 강제 → **지시어("해당 서버")도 서버 지목으로 보고** LLM이 hostname 값으로 플레이스홀더를 지어냄. 이 이번-턴 filter가 `_resolve_hostname` ①에서 previous_entities ②(직전 실제 서버)보다 우선순위가 높아 오염. db_id 승계(D-053)를 고쳐도 hostname 신호가 이 지점에서 오염됨 | 지시어("해당/그/이/위 서버")는 실제 식별자가 아니므로 **hostname으로 인정하지 말고 previous_entities로 폴백**. LLM 프롬프트(규칙 14)만으론 부족 — `_is_demonstrative_value()` **결정적 가드**로 지시어·영문 플레이스홀더(previous/prev)를 배제(①·② 모두). 멀티턴 신호는 "승계가 되는가"뿐 아니라 **"이번 턴 파싱이 승계값을 덮어쓰지 않는가"(우선순위)**까지 확인. 상세: D-055 |
| 2026-07-01 | 면책 문구 환각 차단(D-054 규칙 6) 후 정상 결과 요약·이상 징후 분석까지 소실 | 규칙 6("묻지 않은 내용 면책 금지")을 LLM이 과잉 적용 → avail_status 환각 문구뿐 아니라 규칙 2·5의 정당한 1~2문장 요약·분석도 통째로 생략 | 금지 규칙(negative instruction)은 **범위를 좁게 못 박고, 유지해야 할 정상 동작을 명시적으로 재확인**할 것. 규칙 6에 "데이터에 없는 컬럼 면책만 금지, 조회 데이터 요약·분석은 유지" 단서 추가. 상세: D-055 |
| 2026-07-02 | 파일 업로드 양식 채우기에서 b0(DB2)가 `SQL0204N "SDQ000.CMM_RESOURCE" undefined`로 실패 | 엔진·스키마 인지 SQL 조립이 `polestar_hostname_resolver.build_hostname_sql`(D-053)에만 있고 **LLM SQL 생성 경로(`multi_db_executor`/`query_generator`)엔 미이식** → 무스키마 `cmm_resource` 생성 → DB2가 CURRENT SCHEMA(SDQ000)로 해소(실 스키마는 POLESTAR). D-053이 "향후 고려사항"으로 예측했으나 프로세스 경로만 고치고 폼필 경로를 안 봄 | **한 경로(프로세스/알람)만 엔진·스키마 인지를 넣지 말고, LLM SQL 생성 경로까지 동일 규칙을 공유**하도록 단일 출처화. `DBDomainConfig.db_schema` + `routing/db_schema.py` 헬퍼로 집약하고 `multi_db_executor._generate_sql(db_id)`가 스키마 규칙을 결정적 주입. DB2는 미인용 식별자를 대문자 저장 → 스키마명 대문자(POLESTAR). 새 DB 편입 체크리스트에 ④스키마 한정(db_schema) 추가. 상세: D-057 |
| 2026-07-02 | 양식 채우기 실패 시 사용자가 이유 모른 채 CSV만 받게 됨(침묵적 강등) | `output_generator._generate_document_file`이 실패를 `None`으로만 반환 → `output_file=None` → 프론트가 Excel 링크를 감추고 CSV만 노출. 로그엔 warning이 있으나 사용자 채널로 전달 안 됨 | 결정적 산출물 생성 실패는 **사유를 구조화(reason)해 사용자 응답에 노출**할 것(침묵적 폴백 금지, 2026-06-26과 동일 원칙). `None`→`{"reason":...}` 반환, 성공 판별은 `file_bytes` 키. 상세: D-059 |
