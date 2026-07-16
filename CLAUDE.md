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

에이전트가 작업 중 실수한 항목은 `docs/18_known_mistakes.md`의 표에 기록하여 동일 실수가 반복되지 않도록 한다.

- 실수 발생 시: 원인과 수정 내용을 `docs/18_known_mistakes.md`에 즉시 기록
- 작업 시작 시: 아래 "Known Mistakes 핵심 원칙"을 확인하고, 관련 영역 작업 시 `docs/18_known_mistakes.md`의 상세 이력 참조
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

## Known Mistakes 핵심 원칙

> 전체 실수 이력(50여 건, 원인·방지책 상세)은 `docs/18_known_mistakes.md` 참조. 아래는 반복 실수에서 추출한 예방 원칙 요약.

**실측 우선 (추정 금지)**
- 외부 패키지 API는 `inspect.signature()`로 실제 시그니처 실측 후 사용 — 계획서 의사코드를 신뢰하지 말 것
- 코드/UI "부재"를 단정하기 전 워드 경계 grep(`-w`)·전수 확인으로 실측. 구현·설정이 있어도 호출부 배선까지 grep으로 확인(정의만 있으면 무효)
- 결정적 게이트가 의존하는 데이터는 실 런타임 shape로 검증 — mock 통과 ≠ 프로덕션 동작(로더가 구조를 변형할 수 있음)
- 0건/실패 진단은 안쪽 단계부터 추정 수정하지 말고 진입·게이트별 로그로 끊긴 지점부터 확정(증상보다 라우팅 먼저). 필드 null은 데이터 부재가 아니라 생성 SQL 오류일 수 있음

**pydantic-settings / .env**
- `.env`의 list/dict 필드는 JSON 배열 형식(`["a","b"]`)으로 작성
- `env_file` 로딩은 `os.environ`에 주입되지 않음 → `os.getenv()`로 설정값·설정 유무 판단 금지(pydantic 필드/`AliasChoices`로 판정)
- `.env` 계열 파일에 인라인 주석 금지(주석은 별도 줄, 특히 빈 값 뒤 금지)
- BaseSettings nested 필드는 `Field(default_factory=...)`로 선언(임포트 시점 고정 방지). 테스트 config는 검증 대상 필드를 명시해 `.env` 누수 차단

**LLM 비결정성 대응**
- LLM 분류·매핑·alias·방언 출력에 정합성을 의존하지 말 것 — 결정적 가드로 후처리 교정하고, 스키마·조인이 고정된 쿼리(폼필 피벗 등)는 코드가 runnable SQL을 직접 조립(LLM 우회, 실패 시에만 폴백)
- 프롬프트 강제가 프로필 few-shot 예시와 경쟁해 반복 실패하면 그 쿼리 형태는 결정적 조립 대상
- 금지 규칙(negative instruction)은 범위를 좁게 못 박고 유지해야 할 정상 동작을 명시 재확인
- LLM 자동 등록(유사어 등)은 오염 자기강화 루프 위험 — 출력 교정만으론 부족, 쓰기(등록) 지점에서 결정적 차단

**단일/멀티 경로 대칭 · 멀티 엔진 방언**
- 프롬프트 블록·스키마 메타(`_structure_meta`)·엔진/스키마 규칙은 단일 DB·멀티 DB 경로 **양쪽에 실제 주입됐는지 실측**(한쪽만 고치는 비대칭이 반복 원인)
- PostgreSQL/DB2 방언 분기 필수: LIMIT vs FETCH FIRST, `::numeric` vs `CAST(… AS DECIMAL)`(반드시 집계 **전** 캐스트), DB2 결과 칼럼 라틴 소문자화, 스키마 한정(대문자 POLESTAR)
- 새 DB 편입 체크리스트: ①위치 힌트(`_LOCATION_DB_HINTS`) ②런타임 `.env` base_url ③엔진 방언 ④스키마 한정(db_schema)

**멀티턴 / 상태 관리**
- LangGraph 체크포인터는 델타만 병합 — 요청 스코프 상태(uploaded_file, 매핑 산출물 등)는 라우트에서 명시 초기화하고 노드 스킵 경로는 자기정리
- 승계 신호(hostname/db_id)는 top-level 승격 경로가 대칭인지 + 이번 턴 파싱이 승계값을 덮어쓰지 않는지(우선순위) 확인. 지시어("해당/그 서버")는 식별자가 아님 — previous_entities로 폴백
- 멀티턴 검증은 요청 본문에 thread_id가 실제로 실리는지 프론트까지 확인

**폴백 · 에러 처리**
- 침묵적 폴백/강등 금지 — 산출물 생성 실패는 사유를 구조화해 사용자 응답에 노출, 예외 삼키는 폴백은 실패 SQL·컨텍스트를 로그로 가시화
- 독립 신호 수집은 개별 try/except로 부분 반환 보장(한 try 블록에 묶지 말 것)
- 장시간 실행 경로(SSE 스트리밍 등)는 전체 타임아웃 가드 필수(per-call 타임아웃만으론 무력화됨)
- 데몬류 in-memory dict는 값 bound뿐 아니라 키 만료 sweep도 추가

**보안 · 인가**
- 토큰 서명 검증만으로 끝내지 말고 `type`·role 클레임을 명시 검증(UI 게이트 ≠ 인가). 사용자/운영자 시크릿 분리
- 인증 UX는 "로그인 안 한 첫 방문자" 경로를 실제로 밟아 확인

**테스트 · 작업 절차**
- 대량 테스트 실패는 원인별 분류부터(`--tb=line` 후 유형 카운트) — 한 유형이 지배적이면 단일 오염원 의심. e2e는 `RUN_E2E=1` 옵트인
- 결정적 상수·매트릭스 값 변경 시 그 값을 단언하는 테스트를 repo 전체 grep으로 일괄 갱신. 기존 테스트가 버그를 정답으로 굳혔는지도 점검
- 클린 기준선 검증은 `git stash`가 아니라 `git worktree add <dir> HEAD`(격리 사본)
- 신규 D-번호는 `docs/02_decision.md`의 `## D-` 헤더와 「변경 이력」 표를 모두 grep해 실제 최댓값+1 부여
- 산출물 검증은 미리보기 일부가 아니라 실제 산출 파일의 전 칼럼 확인

