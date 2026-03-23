# 인프라 데이터 조회 에이전트 - 구현 계획서 목차

> 작성일: 2026-03-13
> 기준: `spec.md` + `docs/requirements.md` + 기존 `src/` 코드 분석

---

## 계획서 목록

| # | 파일 | 영역 | 현재 상태 |
|---|------|------|----------|
| 01 | [01-project-structure.md](./01-project-structure.md) | 디렉토리 구조, 설정 파일, pyproject.toml | 기존 코드 있음 (보완 필요) |
| 02 | [02-state-schema.md](./02-state-schema.md) | AgentState 상세 스키마, 노드 간 데이터 흐름 | 기존 코드 있음 (보완 필요) |
| 03 | [03-graph-design.md](./03-graph-design.md) | LangGraph 그래프 설계 (노드, 엣지, 라우팅) | 기존 코드 있음 (개선 필요) |
| 04 | [04-nodes.md](./04-nodes.md) | 각 노드의 입출력, LLM 프롬프트 전략, 에러 핸들링 | 기존 코드 있음 (Phase 2 확장 필요) |
| 05 | [05-dbhub-integration.md](./05-dbhub-integration.md) | DBHub MCP 클라이언트 설계, 연결 관리, 헬스체크 | 기존 코드 있음 (개선 필요) |
| 06 | [06-api-server.md](./06-api-server.md) | FastAPI 엔드포인트 설계, 요청/응답 스키마 | 기존 코드 있음 (확장 필요) |
| 07 | [07-security.md](./07-security.md) | SQL 검증, 민감 데이터 마스킹, 감사 로그 | 기존 코드 있음 (강화 필요) |
| 08 | [08-ui-screens.md](./08-ui-screens.md) | 사용자/운영자 Web UI, 운영자 인증, 환경변수/DB 설정 관리 | 신규 |
| 09 | [09-semantic-routing.md](./09-semantic-routing.md) | 시멘틱 라우팅, 멀티 DB 레지스트리, 멀티 DB 파이프라인 오케스트레이션 | 신규 |
| 10 | [10-document-processing.md](./10-document-processing.md) | Excel/Word 양식 파싱 및 생성 (Phase 2) | 구현 완료 |
| 15 | [15-mcp-server.md](./15-mcp-server.md) | DBHub MCP 서버 구축 및 클라이언트 리팩토링 | 완료 |
| 16 | [16-field-cache-test-plan.md](./16-field-cache-test-plan.md) | 필드 캐시 테스트 계획 | - |
| 17 | [17-testenv-and-synonym-dict.md](./17-testenv-and-synonym-dict.md) | 테스트 환경 및 동의어 사전 | - |
| 18 | [18-claude-skills-plugins.md](./18-claude-skills-plugins.md) | Claude Code 스킬/플러그인 활용 계획 | 신규 |

---

## 의존 관계 그래프

```
01-project-structure (기반)
    │
    ├── 02-state-schema ← 모든 노드/그래프의 데이터 계약
    │       │
    │       ├── 03-graph-design ← 노드 조합 및 제어 흐름
    │       │       │
    │       │       └── 04-nodes ← 개별 노드 구현
    │       │
    │       └── 05-dbhub-integration ← DB 연결 계층 (노드에서 사용)
    │
    ├── 06-api-server ← 그래프를 HTTP로 노출
    │
    └── 07-security ← 전 영역 횡단 관심사
```

---

## 권장 구현 순서

### Phase 1: 기본 자연어 -> SQL 조회 파이프라인

| 순서 | 계획서 | 구현 범위 | 선행 조건 |
|------|--------|----------|----------|
| 1 | 01-project-structure | pyproject.toml 전환, .env 정비, 디렉토리 정리 | 없음 |
| 2 | 02-state-schema | AgentState 필드 보강 (query_attempts 등) | 01 완료 |
| 3 | 05-dbhub-integration | DB 클라이언트 추상화 인터페이스 통합, 헬스체크 강화 | 01 완료 |
| 4 | 07-security | sql_guard 주석 패턴 강화, data_masker 정규식 보강 | 01 완료 |
| 5 | 04-nodes | 7개 노드 구현 완성 (자연어 파싱 + SQL 파이프라인) | 02, 05, 07 완료 |
| 6 | 03-graph-design | 그래프 빌드 함수 개선, 체크포인트 연동 검증 | 04 완료 |
| 7 | 06-api-server | 기본 REST 엔드포인트 (/query, /health) | 03 완료 |

### Phase 2: 양식 기반 문서 생성

| 순서 | 계획서 | 구현 범위 |
|------|--------|----------|
| 8 | 04-nodes | input_parser 양식 파싱, output_generator 파일 생성 |
| 9 | 06-api-server | /query/file, /query/{id}/download 엔드포인트 |

### Phase 3: 안정화 및 부가 기능

| 순서 | 계획서 | 구현 범위 |
|------|--------|----------|
| 10 | 03-graph-design | 멀티턴 대화, Human-in-the-loop |
| 11 | 06-api-server | 인증/인가, 히스토리, 템플릿 관리 |
| 12 | 07-security | DB 기반 감사 로그, RBAC |

### Phase 4: UI 화면 (사용자/운영자)

| 순서 | 계획서 | 구현 범위 |
|------|--------|----------|
| 13 | 08-ui-screens | 사용자 Web UI (프롬프트 + 파일 첨부), 운영자 인증/설정/DB UI |

---

## 기존 코드 현황 요약

| 모듈 | 파일 | 구현 수준 | 비고 |
|------|------|----------|------|
| State | `src/state.py` | 90% | ValidationResult, OrganizedData TypedDict 포함. query_attempts 필드 부재 |
| Graph | `src/graph.py` | 85% | 7개 노드 + error_response 등록, 조건부 라우팅 구현. 체크포인트 리소스 관리 개선 필요 |
| Config | `src/config.py` | 80% | pydantic-settings 기반. db_backend "direct" 추가됨. pyproject.toml 미전환 |
| LLM | `src/llm.py` | 90% | Anthropic/OpenAI 팩토리. temperature, max_tokens 파라미터 미지원 |
| DBHub Client | `src/dbhub/client.py` | 75% | MCP 연결/해제, search_objects, execute_sql 구현. 재연결 로직 부재 |
| Direct DB | `src/db/client.py` | 80% | asyncpg 기반 PostgresClient. DBHubClient와 동일 인터페이스 |
| input_parser | `src/nodes/input_parser.py` | 70% | 자연어 파싱 완료. 양식 파싱은 Phase 2 스텁 |
| schema_analyzer | `src/nodes/schema_analyzer.py` | 80% | 캐시, 도메인 힌트 매핑 구현. LLM 기반 매핑 미구현 |
| query_generator | `src/nodes/query_generator.py` | 85% | 프롬프트 기반 SQL 생성. 재시도 컨텍스트 반영 |
| query_validator | `src/nodes/query_validator.py` | 90% | sqlparse + SQLGuard 이중 검증. 컬럼 검증, LIMIT 자동 추가 |
| query_executor | `src/nodes/query_executor.py` | 85% | 실행 + 감사 로그 기록. 타임아웃/에러 분기 처리 |
| result_organizer | `src/nodes/result_organizer.py` | 70% | 마스킹, 숫자 포맷팅 구현. 양식 매핑 미구현 |
| output_generator | `src/nodes/output_generator.py` | 60% | 자연어 응답만 구현. Excel/Word 생성은 Phase 2 스텁 |
| SQL Guard | `src/security/sql_guard.py` | 85% | 금지 키워드, 인젝션 패턴, 다중 SQL 차단 |
| Data Masker | `src/security/data_masker.py` | 80% | 컬럼명 + 값 패턴 기반 마스킹 |
| Audit Logger | `src/security/audit_logger.py` | 70% | JSONL 파일 기반. structlog 사용. DB 기반은 Phase 3 |
| API Server | `src/api/server.py` | 75% | FastAPI + CORS + lifespan. 라우트 등록 |
| API Routes | `src/api/routes/query.py` | 70% | /query, /query/{id}/result, /query/{id}/download. 파일 업로드 미구현 |
| Prompts | `src/prompts/*.py` | 80% | input_parser, query_generator, output_generator 프롬프트 정의 |
