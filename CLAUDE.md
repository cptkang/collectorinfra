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
