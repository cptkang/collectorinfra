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
