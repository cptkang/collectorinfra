# Decision Log

이 문서는 프로젝트의 주요 아키텍처 및 설계 의사결정을 기록합니다.
향후 요건 추가/수정 시 이 문서를 참고하여 의사결정의 방향성과 일관성을 유지합니다.

---

## 목차

1. [아키텍처: LangGraph 상태 머신](#d-001-langgraph-상태-머신-아키텍처)
2. [DB 접근: DBHub (MCP 서버)](#d-002-dbhub-mcp-서버를-통한-db-접근)
3. [보안: 3중 읽기 전용 방어](#d-003-3중-읽기-전용-방어)
4. [시멘틱 라우팅: LLM 전용 분류](#d-004-llm-전용-시멘틱-라우팅)
5. [멀티 DB: 순차 실행 + 부분 실패 허용](#d-005-멀티-db-순차-실행--부분-실패-허용)
6. [설정: 계층화 + 자동 활성화](#d-006-설정-계층화--자동-활성화)
7. [문서 처리: LLM 의미 매핑](#d-007-문서-처리-llm-의미-매핑)
8. [개발 단계: 4-Phase 점진적 구축](#d-008-4-phase-점진적-구축)
9. [사용자 UI: 채팅 인터페이스 + SSE 스트리밍](#d-009-사용자-ui-채팅-인터페이스--sse-스트리밍)
10. [3단계 스키마 캐싱](#d-010-3단계-스키마-캐싱-메모리---파일---db)
11. [Redis 기반 스키마 캐시 + LLM 컬럼 설명/유사 단어](#d-011-redis-기반-스키마-캐시--llm-컬럼-설명유사-단어)
12. [매핑-우선 필드 매핑 + 유사어 등록](#d-012-매핑-우선mapping-first-필드-매핑--유사어-등록)
13. [멀티턴 대화 + Human-in-the-loop](#d-013-멀티턴-대화--human-in-the-loop-phase-3)
14. [자체 MCP 서버 구축 + SSE Transport 전환](#d-014-자체-mcp-서버-구축--sse-transport-전환)
15. [Excel→CSV 변환으로 LLM 컨텍스트 보강](#d-015-excelcsv-변환으로-llm-컨텍스트-보강-plan-19)
16. [EAV 비정규화 테이블 쿼리 지원](#d-016-eav-비정규화-테이블-쿼리-지원-plan-20)
17. [EAV Field Mapper 전체 파이프라인 지원](#d-017-eav-field-mapper-전체-파이프라인-지원-plan-21)
18. [LLM 지능형 필드 매핑 + 매핑 보고서 + 피드백 학습](#d-018-llm-지능형-필드-매핑--매핑-보고서--사용자-피드백-학습-plan-22)
19. [Fingerprint TTL 기반 Redis 캐시 최적화](#d-019-fingerprint-ttl-기반-redis-캐시-최적화-plan-26)
20. [LLM 기반 범용 스키마 구조 분석](#d-020-llm-기반-범용-스키마-구조-분석-plan-27)
21. [Gemini API 프로바이더 추가 + 민감 키 분리](#d-021-gemini-api-프로바이더-추가--민감-키-분리-plan-28)
22. [RESOURCE_CONF_ID JOIN 금지 + hostname 브릿지 조인 필수화](#d-022-resource_conf_id-join-금지--hostname-브릿지-조인-필수화)
23. [데이터 충분성 검사 로직 개선](#d-023-데이터-충분성-검사-로직-개선-plan-36)
24. [Synonym 통합 관리 + EAV 접두사 비교 정규화](#d-024-synonym-통합-관리--eav-접두사-비교-정규화-plan-37)
25. [3계층 하이브리드 필드 매핑 전파 정합성](#d-025-3계층-하이브리드-필드-매핑-전파-정합성-plan-38)

---

## D-001. LangGraph 상태 머신 아키텍처

| 항목 | 내용 |
|------|------|
| **결정일** | 2026-03 (초기 설계) |
| **상태** | 확정 |

### 결정

에이전트 프레임워크로 **LangGraph**를 사용하며, 7개 노드의 순차 파이프라인으로 구성한다.

```
input_parser → schema_analyzer → query_generator → query_validator → query_executor → result_organizer → output_generator
```

### 근거

- **조건부 라우팅**: `query_validator` 실패 → `query_generator` 재시도, `query_executor` 에러 → 회귀 등을 선언적으로 정의 가능
- **체크포인트 통합**: 멀티턴 대화와 중단 복구를 네이티브 지원 (SQLite/PostgreSQL)
- **LLM 교체 용이**: langchain-core 추상화로 Claude ↔ GPT 전환 가능

### 고려한 대안

| 대안 | 제외 이유 |
|------|----------|
| HuggingFace Pipeline | 조건부 분기/재시도 미지원 |
| Airflow/Prefect | 단일 에이전트에는 과도한 오버헤드 |
| 수동 상태 관리 | 유지보수 비용, 버그 발생 위험 |

### 향후 수정 시 고려사항

- 노드 추가/변경 시 `src/graph.py`의 엣지 구성만 수정하면 됨
- 재시도 횟수(현재 3회)는 `QueryConfig.max_retries`로 제어
- 노드 간 데이터는 반드시 `AgentState` TypedDict를 통해 전달

---

## D-002. DBHub (MCP 서버)를 통한 DB 접근

| 항목 | 내용 |
|------|------|
| **결정일** | 2026-03 (초기 설계) |
| **상태** | 확정 |

### 결정

DB 접근은 **DBHub (MCP 서버)**를 단일 게이트웨이로 사용한다.

### 근거

- **다중 DB 타입 지원**: PostgreSQL, MySQL, MariaDB 등을 단일 인터페이스로 접근
- **읽기 전용 강제**: 서버 수준에서 readonly 설정 가능
- **표준 프로토콜**: MCP(Model Context Protocol)로 LLM과 자연스러운 통합
- **스키마 조회 분리**: `search_objects`(스키마)와 `execute_sql`(실행) API 분리

### 고려한 대안

| 대안 | 제외 이유 |
|------|----------|
| 직접 DB 라이브러리 (PyPG, SQLAlchemy) | 다중 DB 보안 설정 산재, 읽기 전용 강제 어려움 |
| ORM 직접 사용 | 동적 스키마 탐색에 부적합 |

### 향후 수정 시 고려사항

- DB 추가 시 `dbhub.toml`에 연결 정보 추가 + `domain_config.py`에 도메인 정의
- 쿼리 타임아웃(30s), max_rows(10,000) 제약은 DBHub 설정에서 관리

---

## D-003. 3중 읽기 전용 방어

| 항목 | 내용 |
|------|------|
| **결정일** | 2026-03 (초기 설계) |
| **상태** | 확정 — 절대 변경 불가 |

### 결정

읽기 전용을 **3개 레이어**에서 동시에 강제한다.

1. **DBHub 설정 레벨**: `dbhub.toml`에 `readonly = true`
2. **SQL 검증 레벨**: `query_validator`에서 DML/DDL 키워드 차단 (INSERT, UPDATE, DELETE, DROP, ALTER, TRUNCATE, CREATE, GRANT, REVOKE)
3. **LLM 프롬프트 레벨**: `query_generator` 프롬프트에 "SELECT 문만 생성" 명시

### 근거

- **다층 방어(Defense in Depth)**: 어느 한 층이 실패해도 다른 층이 보호
- **LLM 신뢰 불가**: LLM은 프롬프트를 무시할 수 있으므로 프로그래밍적 검증 필수
- **인프라 데이터 보호**: 운영 DB를 직접 조회하므로 데이터 변경 방지가 최우선

### 향후 수정 시 고려사항

- **이 결정은 변경하지 않는다.** 어떤 요건이 추가되더라도 쓰기 기능을 허용해서는 안 됨
- 민감 데이터 마스킹(`SecurityConfig.sensitive_columns`)은 Phase 3에서 구현 예정

---

## D-004. LLM 전용 시멘틱 라우팅

| 항목 | 내용 |
|------|------|
| **결정일** | 2026-03 (v2 개정) |
| **상태** | 확정 |
| **이전 결정** | v1: 키워드 1차 + LLM 폴백 2단계 → **폐기** |

### 결정

DB 라우팅은 **LLM 전용**으로 수행한다. 키워드 기반 사전 분류는 사용하지 않는다.

### 근거

**키워드 방식의 한계:**
- "가상화 인프라의 VM 정보" → 키워드 매칭 실패, LLM 폴백 필요
- 동의어, 줄임말, 문맥적 의미를 키워드로 커버 불가
- 멀티 DB 질의("서버 사양과 VM 정보") 판단 불가

**LLM 전용의 장점:**
- 문맥 기반 판단으로 정확도 향상
- 사용자 직접 DB 지정도 LLM이 자연스럽게 감지
- 새로운 DB 추가 시 description/aliases 수정만으로 확장 가능

### 세부 설계

- **사용자 직접 DB 지정**: `aliases` 필드로 DB별 인식 가능 이름 정의 (예: "폴스타", "polestar", "Polestar DB")
- **멀티 DB 분류**: LLM이 각 DB별 `sub_query_context`를 분리하여 반환
- **동적 프롬프트**: 활성 도메인만 포함하여 LLM 혼동 방지 (`_build_router_prompt()`)
- **confidence 기반 필터링**: `relevance_score` 임계값 이하의 DB는 제외

### 도메인 구성 (현재)

| DB ID | 대상 데이터 | 별칭 예시 |
|-------|-----------|----------|
| `polestar` | 서버 사양, 사용량, 호스트 정보, 프로세스 | 폴스타, Polestar |
| `cloud_portal` | VM 정보, 데이터스토어, 영역별 VM 대수 | 클라우드 포탈, Cloud Portal |
| `itsm` | IT 서비스 관리 정보 | ITSM |
| `itam` | IT 자산 관리 정보 | ITAM |

### 향후 수정 시 고려사항

- DB 추가 시: `domain_config.py`에 `DBDomainConfig` 추가 + `.env`에 연결 정보 추가
- 라우팅 정확도 문제 시: `src/prompts/semantic_router.py` 프롬프트 튜닝으로 해결
- **키워드 기반 분류 재도입 금지** — v1에서 폐기한 이유 유지

---

## D-005. 멀티 DB 순차 실행 + 부분 실패 허용

| 항목 | 내용 |
|------|------|
| **결정일** | 2026-03 |
| **상태** | 확정 (병렬화는 향후 검토) |

### 결정

멀티 DB 쿼리 시 **각 DB를 순차적으로 독립 실행**하며, 일부 DB 실패 시에도 **성공한 결과는 반환**한다.

### 그래프 흐름

```
semantic_router → [조건부]
  ├─ 단일 DB: schema_analyzer → ... → output_generator (기존 파이프라인)
  └─ 멀티 DB: multi_db_executor → result_merger → result_organizer → output_generator
```

### 근거

**순차 실행 선택:**
- 각 DB별 에러 격리 (한 DB 실패 ≠ 전체 실패)
- 디버깅 용이 (실행 순서 예측 가능)
- 현재 부하 수준에서 병렬 처리의 이점이 크지 않음

**부분 실패 허용:**
- 사용자에게 부분 결과라도 제공하는 것이 전체 실패보다 유용
- `_source_db` 태깅으로 데이터 출처를 명확히 표시
- `db_result_summary`로 DB별 성공/실패 현황 보고

### 향후 수정 시 고려사항

- 병렬 실행 전환 시: `asyncio.gather(return_exceptions=True)` 패턴 적용
- 결과 병합 로직(`result_merger`)은 실행 방식(순차/병렬)에 독립적으로 설계됨
- 쿼리 간 데이터 의존성(JOIN across DBs)은 현재 미지원 — 향후 고려 필요

---

## D-006. 설정 계층화 + 자동 활성화

| 항목 | 내용 |
|------|------|
| **결정일** | 2026-03 |
| **상태** | 확정 |

### 결정

- 설정은 **pydantic-settings 기반 계층 구조**로 관리한다
- 시멘틱 라우팅은 멀티 DB 연결이 설정되면 **자동 활성화**된다
- `ENABLE_SEMANTIC_ROUTING=false`로 **명시적 비활성화** 가능

### 설정 구조

```python
AppConfig
  ├─ llm: LLMConfig              # LLM provider, model, API key
  ├─ dbhub: DBHubConfig           # DBHub 경로, source_name
  ├─ query: QueryConfig           # 타임아웃(30s), max_rows(10K), 재시도(3회)
  ├─ security: SecurityConfig     # 민감 컬럼, 마스킹 패턴
  ├─ server: ServerConfig         # API 포트, CORS
  ├─ admin: AdminConfig           # 관리자 인증
  ├─ multi_db: MultiDBConfig      # DB별 연결 문자열
  ├─ checkpoint_backend           # sqlite | postgres
  └─ enable_semantic_routing      # bool (자동 감지 또는 명시 설정)
```

### 근거

- **타입 안전**: pydantic으로 설정 검증
- **레거시 호환**: 단일 DB 모드(멀티 DB 미설정)에서도 정상 동작
- **자동 활성화**: 사용자가 멀티 DB를 설정하면 추가 작업 없이 라우팅 활성화

### 향후 수정 시 고려사항

- 새 설정 추가 시 해당 Config 클래스에 필드 추가 + `.env.example` 업데이트
- DB 추가 시 `MultiDBConfig`에 `{db_id}_connection`, `{db_id}_type` 필드 추가
- `get_active_db_ids()`가 연결 문자열이 있는 DB만 활성으로 판단

---

## D-007. 문서 처리: LLM 의미 매핑

| 항목 | 내용 |
|------|------|
| **결정일** | 2026-03 (Phase 2 설계) |
| **상태** | 구현 완료 (2026-03-17) |

### 결정

Excel/Word 양식의 헤더/플레이스홀더를 DB 컬럼에 매핑할 때 **LLM 의미 매핑**을 사용한다.

### 처리 방식

| 형식 | 라이브러리 | 탐지 방식 | 데이터 채우기 |
|------|-----------|----------|-------------|
| Excel | openpyxl | 헤더 행 자동 탐지 | 데이터 행 채우기, 병합 셀/서식/수식 보존 |
| Word | python-docx | `{{placeholder}}` 패턴 | 치환 + 표 행 채우기, 스타일 보존 |

### LLM 매핑 예시

```
양식 필드          →  DB 컬럼
"서버명"           →  servers.hostname
"CPU 사용률"       →  cpu_metrics.usage_pct
"디스크 용량(GB)"  →  disk_metrics.total_gb
```

### 근거

- 양식 필드명은 비정형(한국어, 약어, 조직 고유 용어)이므로 규칙 기반 매핑 불가
- LLM이 DB 스키마와 양식 필드명의 의미를 이해하여 자동 매핑

### 멀티시트 지원 (2026-03-17 추가)

**기본 동작**: Excel 양식의 모든 시트에 데이터를 독립적으로 채움
- 각 시트마다 별도의 LLM 필드 매핑을 수행 (`map_fields_per_sheet()`)
- `AgentState.target_sheets`로 특정 시트만 처리 가능 (None이면 전체)
- `input_parser`가 사용자 프롬프트에서 시트명을 자동 추출
- `OrganizedData.sheet_mappings`에 시트별 매핑 결과 저장
- `fill_excel_template()`이 `sheet_mappings`와 `target_sheets` 파라미터를 수용

**하위 호환성**: 단일 시트 양식이나 `sheet_mappings=None`인 경우 기존 `column_mapping` + `rows` 방식으로 동작

### 향후 수정 시 고려사항

- 매핑 정확도가 낮을 경우: 자주 사용되는 매핑을 캐시/사전 정의하여 LLM 부하 감소
- 시트별 서로 다른 쿼리 결과가 필요한 경우: 시트별 SQL 생성/실행 파이프라인 확장 검토

---

## D-008. 4-Phase 점진적 구축

| 항목 | 내용 |
|------|------|
| **결정일** | 2026-03 (초기 설계) |
| **상태** | 확정 |

### 결정

개발을 4개 Phase로 나누어 점진적으로 구축한다.

| Phase | 범위 | 핵심 가치 |
|-------|------|----------|
| **1** | 자연어 → SQL 파이프라인 | MVP: 질의 → 응답 기본 흐름 |
| **2** | Excel/Word 양식 기반 문서 생성 | Phase 1의 SQL 조회를 재사용하여 문서 출력 |
| **3** | 멀티턴 대화, 감사 로그, 쿼리 승인 | 운영 안정성 강화 |
| **4** | Web UI (사용자/관리자) | 사용자 접근성 확보 |

### 근거

- **Phase 1 우선**: 핵심 가치(자연어 → SQL → 응답)를 먼저 실현
- **Phase 2 분리**: 양식 처리는 Phase 1의 SQL 조회를 그대로 활용, 추가 로직만 필요
- **Phase 3 후순위**: 운영 기능은 기본 기능 안정화 후 추가
- **Phase 4 독립**: Web UI는 백엔드 API만 호출하므로 병렬 개발 가능

### 현재 진행 상태

- Phase 1: **완료** (LangGraph 파이프라인 + 시멘틱 라우팅 + 멀티 DB)
- Phase 2: **완료** (Excel/Word 양식 파싱 + LLM 의미 매핑 + 문서 생성)
- Phase 3: **완료** (멀티턴 대화 + Human-in-the-loop + 유사어 등록 승인)
- Phase 4: 미착수

### 향후 수정 시 고려사항

- Phase 간 의존성 존중: Phase 2는 Phase 1의 SQL 파이프라인에 의존
- Phase 내 요건 추가는 자유롭지만, Phase 순서 변경은 의존성 검토 필요

---

## 의사결정 간 연관 관계

```
D-001 LangGraph ──────────────────────────────────────┐
  │ 조건부 라우팅/재시도 가능                           │
  ├──→ D-003 3중 읽기 전용 방어                        │
  │      (query_validator 노드에서 SQL 검증)            │
  └──→ D-004 LLM 전용 라우팅                           │
         │ semantic_router 노드 추가                    │
         ├──→ D-005 멀티 DB 순차 실행                   │
         │      multi_db_executor/result_merger 노드     │
         └──→ D-006 설정 자동 활성화                    │
                                                        │
D-002 DBHub ────→ D-003 읽기 전용 (DBHub readonly)      │
       │                                                │
       └────→ D-005 멀티 DB (DBHub 멀티 소스)           │
                                                        │
D-007 문서 처리 ←── D-008 Phase 2에서 구현 ─────────────┘
```

---

## D-009. 사용자 UI: 채팅 인터페이스 + SSE 스트리밍

| 항목 | 내용 |
|------|------|
| **결정일** | 2026-03-17 |
| **상태** | 확정 |

### 결정

사용자 Web UI를 **채팅(대화) 인터페이스**로 재설계하며, **SSE(Server-Sent Events) 기반 스트리밍**으로 응답을 실시간 출력한다.

### 핵심 설계

1. **채팅 UI**: 사용자 메시지(오른쪽)와 에이전트 응답(왼쪽)을 대화 형태로 표시, 세션 내 대화 이력 유지
2. **SSE 스트리밍**: `POST /api/v1/query/stream` 엔드포인트에서 `text/event-stream`으로 토큰 단위 응답
3. **폴백 전략**: SSE 미지원 시 기존 `POST /api/v1/query`로 자동 폴백
4. **파일 질의**: SSE 불필요 (결과가 파일이므로 기존 방식 유지)

### SSE 이벤트 형식

```
data: {"type": "token", "content": "..."}\n\n     # 토큰 단위 텍스트
data: {"type": "meta", "executed_sql": "...", ...}\n\n  # 메타 정보
data: {"type": "done", "query_id": "...", ...}\n\n      # 완료
data: {"type": "error", "message": "..."}\n\n           # 에러
```

### 근거

- **UX 개선**: 대화형 인터페이스가 단일 질의/응답 폼보다 자연스러운 인터랙션 제공
- **체감 속도 향상**: SSE 스트리밍으로 첫 토큰까지의 대기 시간(TTFT) 단축
- **기존 API 호환**: 기존 엔드포인트를 그대로 유지하면서 새 스트리밍 엔드포인트 추가

### 향후 수정 시 고려사항

- 멀티턴 대화(Phase 3) 구현 시 `thread_id`를 세션에서 자동 관리
- WebSocket 전환 검토 시 SSE의 단방향 한계와 WebSocket의 양방향 이점 비교 필요
- 기존 `/api/v1/query` 엔드포인트는 CLI/API 클라이언트용으로 유지

---

## D-010. 3단계 스키마 캐싱 (메모리 -> 파일 -> DB)

| 항목 | 내용 |
|------|------|
| **결정일** | 2026-03-17 |
| **상태** | 확정 |

### 결정

DB 스키마 조회에 **3단계 캐시**를 적용한다.

```
요청 → 1차 메모리 캐시(TTL 5분)
  ├─ 히트 → 바로 사용
  └─ 미스 → 2차 파일 캐시 (fingerprint 비교)
       ├─ fingerprint 일치 → 파일에서 로드
       └─ 불일치 또는 미스 → 3차 DB 전체 조회 → 캐시 갱신
```

### 캐시 구조

| 단계 | 저장소 | 유효성 판단 | 범위 |
|------|--------|------------|------|
| 1차 | 메모리 (SchemaCache) | TTL 5분 | 프로세스 수명 |
| 2차 | 파일 (`{cache_dir}/{db_id}_schema.json`) | fingerprint 해시 비교 | 영구 (프로세스 재시작 후에도 유지) |
| 3차 | DB (information_schema + 전체 스키마) | 항상 최신 | - |

### fingerprint 방식

- `information_schema.columns`에서 테이블별 컬럼 수를 조회 (가벼운 쿼리)
- 테이블명+컬럼수를 정렬된 JSON으로 직렬화 후 SHA-256 해시 생성
- 캐시된 해시와 비교하여 변경 감지

### 근거

- **프로세스 재시작 시 비용 절감**: 기존 메모리 캐시(5분 TTL)는 재시작 시 사라짐. 인프라 DB 스키마는 자주 변경되지 않으므로 영구 캐시로 불필요한 전체 조회 방지
- **변경 감지 경량화**: fingerprint 쿼리는 전체 스키마 조회 대비 매우 가벼움 (단일 집계 쿼리)
- **멀티 DB 독립 캐시**: DB별로 독립 파일 관리하여 한 DB 변경이 다른 DB 캐시에 영향 없음
- **Graceful fallback**: 캐시 파일 손상 시 자동으로 전체 조회로 폴백

### 설정

```
SCHEMA_CACHE_DIR=.cache/schema    # 캐시 디렉토리
SCHEMA_CACHE_ENABLED=true          # 캐시 활성화 여부
```

### 향후 수정 시 고려사항

- 캐시 포맷 변경 시 `CACHE_FORMAT_VERSION` 증가 (자동 무효화)
- `.cache/` 디렉토리는 `.gitignore`에 포함
- 스키마 변경이 매우 빈번한 환경에서는 `SCHEMA_CACHE_ENABLED=false`로 비활성화 가능
- fingerprint 쿼리가 실패하면 전체 조회로 폴백 (DB 호환성 보장)

---

## D-011. Redis 기반 스키마 캐시 + LLM 컬럼 설명/유사 단어

| 항목 | 내용 |
|------|------|
| **결정일** | 2026-03-17 |
| **상태** | 구현 완료 |
| **이전 결정** | D-010 확장 |

### 결정

2차 캐시를 **파일 -> Redis**로 업그레이드하고, LLM 기반 **컬럼 설명(description)** + **유사 단어(synonym)** 생성 기능을 추가한다.

### 캐시 구조 (변경 후)

```
요청 -> 1차 메모리 캐시(TTL 5분)
  |- 히트 -> 바로 사용
  +- 미스 -> 2차 Redis 캐시 (fingerprint 비교)
       |- fingerprint 일치 -> Redis에서 로드 + descriptions/synonyms 로드
       +- 불일치 또는 Redis 장애 -> 파일 캐시 폴백
            +- 미스 -> 3차 DB 전체 조회 -> 캐시 갱신
```

### Redis 키 구조

- `schema:{db_id}:meta` - fingerprint, cached_at, table_count 등
- `schema:{db_id}:tables` - 테이블별 스키마 JSON
- `schema:{db_id}:relationships` - FK 관계 JSON 배열
- `schema:{db_id}:descriptions` - 컬럼별 한국어 설명
- `schema:{db_id}:synonyms` - 컬럼별 유사 단어 JSON 배열

### 핵심 설계

- **영구 저장 (TTL 없음)**: fingerprint 변경 시에만 갱신
- **Graceful fallback**: Redis 장애 -> 파일 캐시 -> DB 조회
- **기존 호환**: `SCHEMA_CACHE_BACKEND=file` 시 기존 동작 100% 유지
- **컬럼 설명**: LLM이 테이블 단위로 설명 + 유사 단어를 동시 생성
- **유사 단어**: query_generator 프롬프트에 포함하여 컬럼 선택 정확도 향상
- **시멘틱 라우터 확장**: `cache_management` 의도 분류 추가

### 관련 모듈

| 모듈 | 역할 |
|------|------|
| `src/schema_cache/redis_cache.py` | Redis 기반 CRUD |
| `src/schema_cache/cache_manager.py` | Redis/파일 통합 추상화 |
| `src/schema_cache/description_generator.py` | LLM 설명 + 유사 단어 생성 |
| `src/nodes/cache_management.py` | 프롬프트 기반 캐시 관리 노드 |
| `src/api/routes/schema_cache.py` | 운영자 API |
| `scripts/schema_cache_cli.py` | 독립 실행 CLI |

### 향후 수정 시 고려사항

- Redis 키 구조 변경 시 `CACHE_FORMAT_VERSION` 증가 필요
- 3중 읽기 전용 방어(D-003) 유지: Redis에 저장하는 것은 스키마 메타데이터뿐, DB 쓰기 아님
- 유사 단어 운영자 수동 추가분은 **글로벌 사전(`synonyms:global`)에 보존**. DB별 synonyms는 `invalidate()` 시 삭제되며, 스키마 재생성 시 `load_synonyms_with_global_fallback()`으로 글로벌 사전에서 자동 재구축됨 (Plan 30 정책 변경)

---

## D-012. 매핑-우선(Mapping-First) 필드 매핑 + 유사어 등록

| 항목 | 내용 |
|------|------|
| **결정일** | 2026-03-17 |
| **상태** | 구현 완료 |
| **이전 결정** | D-007 확장 |

### 결정

필드 매핑을 **input_parser 직후** 독립 노드(`field_mapper`)로 수행하고, 그 결과로 대상 DB 선택, SQL 생성, 파일 생성 전체를 주도하는 **Mapping-First** 전략을 도입한다.

### 핵심 설계

1. **3단계 매핑**: 프롬프트 힌트 -> Redis synonyms -> LLM 추론 (순차 적용, 앞 단계에서 매핑 성공하면 다음 단계 스킵)
2. **Single Source of Truth**: field_mapper에서 한 번만 매핑 수행, query_generator와 output_generator가 동일한 매핑 참조
3. **매핑 결과가 DB 선택 주도**: semantic_router는 mapped_db_ids를 우선 참조하여 LLM 라우팅 스킵
4. **LLM 추론 매핑 공개 + 유사어 등록**: LLM 추론 매핑은 사용자에게 표시하고, 사용자가 승인하면 Redis synonyms에 자동 등록
5. **Redis 미존재 시 graceful fallback**: 2단계(synonyms) 스킵 후 LLM 폴백으로 정상 동작
6. **template 없는 경우 스킵**: 텍스트 출력 모드에서는 field_mapper가 아무 작업 없이 통과

### 그래프 변경

```
기존: input_parser -> semantic_router -> schema_analyzer -> ...
개선: input_parser -> field_mapper -> semantic_router -> schema_analyzer -> ...
```

### 향후 수정 시 고려사항

- field_mapper는 template_structure가 없으면 스킵하므로 기존 텍스트 출력 흐름에 영향 없음
- 유사어 등록은 멀티턴 대화에서 pending_synonym_registrations State를 참조
- 새 DB 추가 시 해당 DB의 synonyms/descriptions가 Redis에 존재하면 자동으로 매핑에 활용됨

---

## D-013. 멀티턴 대화 + Human-in-the-loop (Phase 3)

| 항목 | 내용 |
|------|------|
| **결정일** | 2026-03-18 |
| **상태** | 구현 완료 |

### 결정

LangGraph **체크포인트 기반 멀티턴 대화**를 도입하고, **SQL 승인(Human-in-the-loop)**, **유사어 등록 승인** 플로우를 구현한다.

### 핵심 설계

1. **통합 단일 코드 경로**: "단일 턴 모드"와 "멀티턴 모드"를 별도 분기하지 않음. 모든 요청이 동일 그래프를 통과하며, 단일 턴은 멀티턴의 특수한 경우(첫 턴)
2. **context_resolver 노드 신설**: 그래프 첫 노드로 실행, 이전 대화 맥락(SQL, 결과, 테이블, pending 상태) 추출
3. **approval_gate 노드 신설**: `interrupt_before`로 SQL 실행 전 사용자 승인 대기 (approve/reject/modify)
4. **synonym_registrar 노드 신설**: `pending_synonym_registrations`에서 사용자 선택 항목을 Redis에 등록
5. **체크포인트 기반 pending 보존**: `pending_synonym_reuse`, `pending_synonym_registrations` 등은 체크포인트에서 자동 복원 (별도 Redis 저장 불필요)
6. **semantic_router pending 우선 라우팅**: pending 상태가 있으면 LLM 분류 없이 해당 노드로 강제 라우팅

### 그래프 변경

```
[변경 전] START → input_parser → field_mapper → semantic_router → ...
[변경 후] START → context_resolver → input_parser → field_mapper → semantic_router → ...
                                                                       ↓
                                                            synonym_registrar → END (pending 등록 시)
```

SQL 승인 활성화 시:
```
query_validator → approval_gate (interrupt) → query_executor
```

### State 확장

- `messages: Annotated[list[BaseMessage], add_messages]` — 대화 히스토리 (LangGraph reducer)
- `thread_id`, `conversation_context` — 세션/맥락 관리
- `awaiting_approval`, `approval_context`, `approval_action`, `approval_modified_sql` — HITL

### API 변경

- `POST /query`: 체크포인트 기반 첫 턴/후속 턴 자동 분기
- `QueryResponse`: `thread_id`, `awaiting_approval`, `approval_context`, `turn_count` 추가
- `GET /conversation/{thread_id}`: 대화 히스토리 조회

### 향후 수정 시 고려사항

- 체크포인트 크기 관리: `query_results`에 대량 데이터 포함 시 요약본으로 교체 검토
- 동시성: 동일 `thread_id`에 동시 요청 시 LangGraph 직렬화에 의존
- WebSocket 전환 시 SSE interrupt 이벤트 핸들링 수정 필요

---

## D-014. 자체 MCP 서버 구축 + SSE Transport 전환

| 항목 | 내용 |
|------|------|
| **결정일** | 2026-03-19 |
| **상태** | 구현 완료 |
| **이전 결정** | D-002 확장 |

### 결정

외부 npm 패키지 `dbhub`를 자체 Python MCP 서버(`mcp_server/`)로 교체하고, 클라이언트 transport를 stdio에서 SSE로 전환한다.

### 핵심 변경사항

1. **`mcp_server/` 독립 패키지 생성**: FastMCP 기반, 자체 `pyproject.toml`, `src/`에 대한 import 의존성 없음
2. **Transport: stdio -> SSE**: 별도 VM 배포를 위해 네트워크 통신(SSE over HTTP) 사용
3. **다중 DB 타입 지원**: PostgreSQL(asyncpg) + DB2(ibm_db, asyncio.to_thread 래핑)
4. **5개 MCP 도구**: search_objects, execute_sql, get_table_schema(신규), health_check(신규), list_sources(신규)
5. **설정 완전 분리**: DB 연결 정보는 MCP 서버 VM에만 존재, 클라이언트는 서버 URL만 보유
6. **이중 보안**: 서버 자체 SQL 가드(`mcp_server/security.py`) + 클라이언트 SQL 가드(`src/security/sql_guard.py`)

### 설정 변경

| 항목 | 변경 전 | 변경 후 |
|------|---------|---------|
| `DBHubConfig.config_path` | `./dbhub.toml` | 제거 |
| `DBHubConfig.server_url` | (없음) | `http://localhost:9090/sse` |
| `DBHubConfig.mcp_call_timeout` | (없음) | `60`초 |
| `QueryConfig.query_timeout` | `30` | 제거 (서버 관리) |
| `QueryConfig.max_rows` | `10000` | 제거 (서버 관리) |
| `MultiDBConfig` 연결 문자열 | 클라이언트 보유 | 제거 (서버 관리) |
| `MultiDBConfig.active_db_ids_csv` | (없음) | 활성 DB 목록 |

### 근거

- **커스터마이징**: 외부 npm 패키지 수정 불가 -> 자체 패키지로 기능 확장 자유
- **배포 분리**: DB 서버와 에이전트 서버를 별도 VM으로 분리하여 보안 강화
- **Node.js 의존성 제거**: Python 단일 스택으로 통일
- **DB2 지원**: 기존 dbhub는 DB2 미지원, 자체 구현으로 해결

### 향후 수정 시 고려사항

- MCP 서버에 새 도구 추가 시: `mcp_server/tools.py`에 등록 + 클라이언트에서 호출
- DB 타입 추가 시: `mcp_server/db.py`에 드라이버 추가 + `config.toml`에 소스 정의
- `dbhub.toml`은 deprecated 상태로 유지 (롤백 대비)

---

## D-015. Excel→CSV 변환으로 LLM 컨텍스트 보강 (Plan 19)

| 항목 | 내용 |
|------|------|
| **결정일** | 2026-03-23 |
| **상태** | 구현 완료 |
| **이전 결정** | D-007 확장 |

### 결정

Excel 업로드 시 CSV 변환을 통해 **헤더 + 예시 데이터**를 추출하여 LLM 컨텍스트에 전달한다. 기존 파이프라인(field_mapper → SQL → DB 쿼리 → Excel 채우기)은 유지하며, CSV는 LLM 컨텍스트 보강 수단으로만 사용한다.

### 핵심 변경사항

1. **`CsvSheetData` 데이터클래스**: 시트별 헤더, 예시 데이터(최대 50행), CSV 텍스트 구조화
2. **`excel_to_csv()` 함수**: Excel→시트별 CsvSheetData 변환, 기존 `excel_parser` 함수 재활용
3. **폴백 경로**: CSV 변환 실패(복잡 구조) 시 `template_structure` 기반 헤더 추출
4. **시트별 순환 LLM 호출**: `map_fields_per_sheet()` 패턴 재활용, input_parser에서 시트별 개별 파싱
5. **field_mapper 예시 데이터**: 프롬프트에 예시 값 포함하여 매핑 정확도 향상

### 근거

- LLM이 헤더명만 보는 것보다 예시 데이터 패턴을 참고하면 필드 매핑 정확도 향상 (예: "서버명" → `hostname` vs `server_id` 판별)
- 멀티시트 시 시트별 개별 LLM 호출로 컨텍스트 윈도우 관리 유리
- output_generator는 변경 없음 — 기존 `excel_writer`가 DB 결과를 Excel에 채우는 방식 유지

### 향후 수정 시 고려사항

- 예시 데이터 최대 행 수(50행)는 LLM 토큰 사용량에 따라 조정 가능
- 시트별 LLM 호출 병렬화(`asyncio.gather`) 검토 가능

---

## D-016. EAV 비정규화 테이블 쿼리 지원 (Plan 20)

| 항목 | 내용 |
|------|------|
| **결정일** | 2026-03-24 |
| **상태** | 구현 완료 |
| **이전 결정** | D-001 확장, D-014 연계 |

### 결정

Polestar DB의 **EAV(Entity-Attribute-Value) 구조**와 **계층형 리소스 테이블**(CMM_RESOURCE + CORE_CONFIG_PROP)에 대한 쿼리 지원을 추가한다. DB 엔진(DB2/PostgreSQL)에 따른 SQL 문법 분기도 도입한다.

### 핵심 변경사항

1. **`src/prompts/polestar_patterns.py` 신규**: POLESTAR_QUERY_PATTERNS(6개 패턴), POLESTAR_META(메타데이터 상수), POLESTAR_QUERY_GUIDE(프롬프트 가이드)
2. **schema_analyzer 자동 감지**: CMM_RESOURCE + CORE_CONFIG_PROP 테이블 존재 시 `_polestar_meta`를 schema_info에 자동 삽입. EAV 샘플/RESOURCE_TYPE 분포도 수집
3. **query_generator 분기**: `_polestar_meta` 존재 시 EAV 피벗, 계층 탐색, 조인 조건 가이드를 프롬프트에 삽입. 예시 쿼리 3개 포함
4. **DB 엔진 지원**: `DBDomainConfig.db_engine` 필드, `AgentState.active_db_engine` 필드 추가. query_validator가 DB2(`FETCH FIRST N ROWS ONLY`)/PostgreSQL(`LIMIT N`) 문법 자동 대응
5. **query_validator 보강**: LIMIT 검사에 DB2 패턴 인식, 테이블명 대소문자 무시 비교
6. **input_parser 확장**: query_targets에 "파일시스템", "프로세스", "HBA", "에이전트", "서버설정" 추가. filter_conditions에 `is_eav` 플래그 가이드 추가

### 하위 호환성

- `_polestar_meta`가 없으면 기존 로직 그대로 동작 (비-Polestar DB에 영향 없음)
- `db_engine` 기본값은 "postgresql"로 기존 DB 동작 불변
- `polestar_guide` 플레이스홀더는 비-Polestar 시 빈 문자열

### 근거

- Polestar DB는 EAV 패턴과 계층형 self-join이 필수이나, LLM이 이 구조를 자동으로 파악하기 어려움
- 쿼리 패턴 예시와 메타데이터를 프롬프트에 제공하면 LLM의 올바른 SQL 생성 가능성 증가
- DB2와 PostgreSQL의 LIMIT 문법 차이를 validator 수준에서 자동 처리하여 엔진 불문 올바른 SQL 보장

### 향후 수정 시 고려사항

- RESOURCE_TYPE 값이 추가되면 `POLESTAR_META["resource_types"]`에 반영
- EAV known_attributes가 추가되면 `POLESTAR_META["eav"]["known_attributes"]`에 반영
- 새로운 DB 엔진(Oracle 등) 추가 시 `_add_limit_clause`에 분기 추가 필요
- Polestar 이외의 EAV 구조 DB 지원 시 감지 로직을 일반화 검토

---

## D-017. EAV Field Mapper 전체 파이프라인 지원 (Plan 21)

| 항목 | 내용 |
|------|------|
| **결정일** | 2026-03-24 |
| **상태** | 구현 완료 |
| **이전 결정** | D-016 확장, D-012 확장 |

### 결정

Field Mapper의 3단계 매핑에 **2.5단계 EAV synonym 매칭**을 삽입하고, `EAV:속성명` 접두사 규약으로 EAV 속성 매핑을 표현한다. query_generator가 이를 감지하여 CASE WHEN 피벗 쿼리 힌트를 자동 생성한다.

### 핵심 변경사항

1. **`_apply_eav_synonym_mapping()` 신규** (`src/document/field_mapper.py`): Redis `eav_name_synonyms`에서 필드명을 매칭하여 `EAV:속성명` 형식으로 polestar DB에 매핑
2. **`perform_3step_mapping()` 확장**: `eav_name_synonyms` 파라미터 추가, 2단계-3단계 사이에 2.5단계 EAV 매칭 삽입
3. **field_mapper 노드 EAV 로드**: `_load_db_cache_data()`에서 `load_eav_name_synonyms()` 호출하여 `perform_3step_mapping()`에 전달
4. **field_mapper 프롬프트 EAV 가이드**: 단일/멀티 DB 프롬프트에 EAV 매핑 패턴 설명 추가
5. **`_format_schema_columns()` EAV 가상 컬럼**: `_polestar_meta` 감지 시 known_attributes를 `EAV:속성명` 형식으로 스키마에 포함
6. **`_validate_mapping()` EAV 검증**: `EAV:` 접두사 매핑을 known_attributes 기준으로 검증
7. **query_generator EAV 피벗 힌트**: `_build_user_prompt()`에서 `EAV:` 매핑 감지 → CASE WHEN 피벗 쿼리 힌트 + 조인 조건 프롬프트 삽입

### EAV 매핑 규약

- 매핑 결과: `"EAV:속성명"` (예: `"EAV:OSType"`, `"EAV:Vendor"`)
- mapping_sources: `"eav_synonym"` (기존 `"hint"`, `"synonym"`, `"llm_inferred"`에 추가)
- 정규 컬럼 매핑(`table.column`)과 공존 가능

### 하위 호환성

- `eav_name_synonyms`가 None/빈 dict이면 2.5단계 스킵 → 기존 동작 불변
- `EAV:` 접두사가 없는 매핑은 기존 로직 그대로 처리
- 비-Polestar DB에는 영향 없음

### 근거

- Plan 20에서 query_generator만 EAV를 지원했으나, 양식 기반 조회 시 field_mapper도 EAV를 이해해야 올바른 매핑 가능
- `EAV:` 접두사 규약으로 정규/EAV 매핑을 명확히 구분하여 파이프라인 전체에서 투명하게 처리

---

## D-018. LLM 지능형 필드 매핑 + 매핑 보고서 + 사용자 피드백 학습 (Plan 22)

| 항목 | 내용 |
|------|------|
| **결정일** | 2026-03-24 |
| **상태** | 구현 완료 |
| **이전 결정** | D-012 확장, D-017 확장 |

### 결정

Field Mapper의 LLM 추론 단계를 **Redis 유사어 + DB descriptions + EAV names를 결합한 통합 컨텍스트**로 강화하고, LLM 매핑 결과를 **즉시 Redis에 등록**하며, **구조화된 MD 보고서**를 생성하여 사용자가 **MD 수정/업로드**로 매핑을 교정할 수 있도록 한다.

### 핵심 변경사항

1. **`_apply_llm_mapping_with_synonyms()` 신규** (`src/document/field_mapper.py`): Redis synonyms + descriptions + EAV names를 결합한 프롬프트로 전체 필드를 1회 LLM 호출로 매핑. confidence/reason/matched_synonym 포함 응답.
2. **`_register_llm_mappings_to_redis()` 신규**: LLM 매핑 결과를 즉시 Redis synonyms에 등록 (source: `llm_inferred`). EAV 매핑은 eav_name_synonyms에 등록.
3. **`perform_3step_mapping()` 확장**: `cache_manager` 파라미터 추가, 반환 타입 `tuple[MappingResult, list[dict]]`로 변경.
4. **`src/document/mapping_report.py` 신규 모듈**: `generate_mapping_report()` (매핑→MD), `parse_mapping_report()` (MD→매핑 리스트).
5. **`analyze_md_diff()` / `apply_mapping_feedback_to_redis()` 신규**: 원본/수정 MD 비교 → 변경사항 Redis 반영.
6. **API 엔드포인트 2개 추가**: `GET /query/{id}/mapping-report` (다운로드), `POST /query/mapping-feedback` (수정 MD 업로드).
7. **프론트엔드**: 매핑 보고서 다운로드 버튼 + 수정 MD 업로드 버튼 추가.

### 전략: "기본 등록 → 사후 교정"

- 기존: LLM 매핑 결과를 pending 상태로 대기 → 사용자 자연어 승인 필요
- 변경: LLM 매핑 결과를 **즉시 Redis에 등록** → MD 보고서로 현황 제공 → 문제 시 MD 수정/업로드로 교정
- 효과: 사용자 액션 없이도 자기학습, 동일 양식 2차 조회 시 LLM 호출 제거

### 근거

- Redis에 이미 있는 유사어 정보를 LLM 컨텍스트로 활용하면 매핑 정확도 향상
- 즉시 등록 전략으로 반복 양식 조회 비용 대폭 절감
- MD 파일 기반 피드백은 자연어 파싱의 불확실성을 제거하고 구조화된 변경 의도 전달

### 향후 수정 시 고려사항

- source 태그 `llm_inferred`와 `user_corrected`로 자동/수동 등록 구분 가능
- `mapping_history:{template_hash}` Redis 키로 양식별 매핑 이력 추적 가능 (미구현, 필요 시 추가)

---

## D-019. Fingerprint TTL 기반 Redis 캐시 최적화 (Plan 26)

| 항목 | 내용 |
|------|------|
| **결정일** | 2026-03-25 |
| **상태** | 구현 완료 |
| **이전 결정** | D-010 확장, D-011 확장 |

### 결정

메모리 캐시(5분 TTL) 만료 후 Redis 캐시를 조회할 때, **fingerprint 검증 타임스탬프(기본 30분 TTL)**가 유효하면 DB에 fingerprint SQL을 실행하지 않고 Redis 캐시를 그대로 신뢰한다.

### 핵심 변경사항

1. **`SchemaCacheConfig.fingerprint_ttl_seconds: int = 1800`** (`src/config.py`): fingerprint 재검증 주기 설정 (기본 30분)
2. **Redis 키 추가**: `schema:{db_id}:fingerprint_checked_at` — 마지막 fingerprint 검증 시각 (Unix timestamp)
3. **`RedisSchemaCache.is_fingerprint_fresh()` / `refresh_fingerprint_checked_at()`** (`src/schema_cache/redis_cache.py`): TTL 확인 및 갱신
4. **`SchemaCacheManager.is_fingerprint_fresh()` / `refresh_fingerprint_ttl()`** (`src/schema_cache/cache_manager.py`): Redis 위임, 파일 백엔드는 항상 False
5. **캐시 조회 흐름 2단계 분리** (`schema_analyzer.py`, `multi_db_executor.py`):
   - 2차-A: fingerprint TTL 유효 → DB 조회 없이 Redis에서 복원
   - 2차-B: fingerprint TTL 만료 → DB fingerprint SQL 1회 → 불변이면 TTL 갱신 후 Redis에서 복원
6. **`multi_db_executor._analyze_schema()`**: `PersistentSchemaCache` 직접 사용 → `SchemaCacheManager` 통합 사용으로 변경

### 효과

| 시나리오 | 변경 전 DB 조회 | 변경 후 DB 조회 |
|---------|---------------|---------------|
| 5분 이내 재요청 | 없음 (메모리 캐시) | 없음 (메모리 캐시) |
| 5~30분 이내 재요청 | fingerprint SQL 1회 | **없음 (Redis TTL 유효)** |
| 30분 후 재요청 | fingerprint SQL 1회 | fingerprint SQL 1회 (TTL 갱신) |

### 트레이드오프

스키마 변경 반영이 최대 30분 지연될 수 있다. `SCHEMA_CACHE_FINGERPRINT_TTL_SECONDS` 환경변수로 조절 가능.

### 근거

- 인프라 DB 스키마는 빈번하게 변경되지 않으므로 30분 지연은 허용 가능
- Redis 장애 시 `is_fingerprint_fresh()`가 항상 False를 반환하여 기존 경로로 안전하게 폴백
- `multi_db_executor`가 `SchemaCacheManager`를 사용하도록 통합하여 캐시 전략 일관성 확보

---

## D-020. LLM 기반 범용 스키마 구조 분석 (Plan 27)

| 항목 | 내용 |
|------|------|
| **결정일** | 2026-03-25 |
| **상태** | 확정 |

### 결정

`schema_analyzer.py`의 특정 DB(Polestar) 하드코딩 의존성을 전면 제거하고, **LLM 전면 분석 + HITL 검증 + 결과 자동 캐싱** 방식으로 전환한다.

### 주요 변경

1. **`DOMAIN_TABLE_HINTS` 삭제** → LLM 기반 테이블 선택(`_llm_select_relevant_tables`)으로 대체
2. **Polestar 전용 함수 3개 삭제** (`_detect_polestar_structure`, `_enrich_polestar_metadata`, `_collect_polestar_samples`) → 범용 구조 분석(`_analyze_db_structure`, `_collect_structure_samples`)으로 대체
3. **`_polestar_meta` → `_structure_meta`** 키 변경 (다운스트림 4개 파일 포함)
4. **`polestar_patterns.py` 파일 삭제** — `POLESTAR_META`, `POLESTAR_QUERY_PATTERNS`, `POLESTAR_QUERY_GUIDE` 상수 제거
5. **DB 프로필 자동 생성** — LLM 분석 결과를 `config/db_profiles/{db_id}.yaml`에 자동 저장 (수동 작성 없음)
6. **구조 분석 결과 캐싱** — Redis + YAML 이중 저장, 스키마 미변경 시 LLM 호출 생략
7. **HITL 승인 흐름** — `structure_approval_gate` 노드 + `interrupt_before` + `enable_structure_approval` config (기본 활성화)

### 설계 원칙

- YAML/JSON 프로필 파일은 **LLM + HITL의 산출물**이며 수동 편집하지 않는다
- 환각 위험은 HITL(사용자 승인/수정)로 처리한다
- 새 DB 추가 시 `schema_analyzer.py` 코드 변경 없이 동작한다

### 근거

- 기존 방식은 새 DB마다 전용 감지/보강 코드를 추가해야 하는 확장성 문제
- LLM이 EAV, 계층형 등 구조적 패턴을 스키마에서 자동 감지할 수 있음
- 분석 결과를 캐싱하면 LLM 비용/지연 영향이 최초 1회로 제한됨

---

## D-021. Gemini API 프로바이더 추가 + 민감 키 분리 (Plan 28)

| 항목 | 내용 |
|------|------|
| **결정일** | 2026-03-25 |
| **상태** | 구현 완료 |
| **이전 결정** | D-006 확장 (설정 계층화) |

### 결정

Ollama 환각(hallucination) 검증 목적으로 **Google Gemini API**를 3번째 LLM 프로바이더로 추가한다. 동시에 API 키 등 민감 정보를 **`.encenv` 파일로 분리**하여 `.env`와 독립 관리한다.

### 핵심 변경사항

1. **`LLMConfig.provider`에 `"gemini"` 추가** (`src/config.py`): `Literal["ollama", "fabrix", "gemini"]`
2. **`_create_gemini()` 팩토리 함수** (`src/llm.py`): `langchain-google-genai`의 `ChatGoogleGenerativeAI` 사용
3. **`.encenv` 민감 키 파일 도입**: `.gitignore`에 등록, `LLMConfig`/`AdminConfig`/`RedisConfig`의 `env_file`을 `[".env", ".encenv"]`로 확장
4. **`langchain-google-genai>=2.0.0`**: `pyproject.toml` optional dependency (`pip install -e ".[gemini]"`)
5. **Gemini 모델 권장**: `gemini-2.0-flash` (안정, 기본), `gemini-3.1-pro` (최신 추론). `gemini-2.5-*` 시리즈는 2026-06-17 deprecated 예정이므로 사용 금지

### 설계 원칙

- **팩토리 패턴 유지**: 모든 노드는 `create_llm()` 단일 진입점만 사용 → 노드 코드 변경 없음
- **Lazy import**: `langchain_google_genai`는 `_create_gemini()` 내부에서만 import → 미설치 환경에서도 import 에러 없음
- **키 분리**: `.encenv`에 API 키를 격리하여 `.env`가 실수로 커밋되어도 키 유출 방지

### 트레이드오프

- Gemini API는 외부 네트워크 필요 (폐쇄망 불가)
- optional dependency이므로 Gemini 미사용 환경에서는 `pip install -e ".[gemini]"` 불필요

### 근거

- Ollama 로컬 LLM의 환각 현상으로 SQL 생성 정확도 판단이 어려움
- Gemini API로 동일 쿼리 결과를 비교하여 환각 여부를 검증할 수 있음
- `ChatGoogleGenerativeAI`가 `BaseChatModel`을 상속하므로 기존 LangChain/LangGraph 파이프라인과 100% 호환
- `langchain-google-genai`가 `bind_tools()`, `ainvoke()` 등 표준 인터페이스를 지원하므로 커스텀 클라이언트 불필요

### 대안 (미채택)

| 대안 | 미채택 사유 |
|------|-----------|
| OpenAI API | 비용 대비 Gemini 무료 티어가 검증 용도로 충분 |
| Anthropic Claude API | 이미 개발 도구로 사용 중, 별도 검증용 LLM은 다른 벤더가 적절 |
| 커스텀 HTTP 클라이언트 | `langchain-google-genai`가 LangChain 표준 인터페이스를 제공하므로 불필요한 코드 |

---

## D-022. RESOURCE_CONF_ID JOIN 금지 + hostname 브릿지 조인 필수화

| 항목 | 내용 |
|------|------|
| **결정일** | 2026-03-26 |
| **상태** | 확정 |
| **이전 결정** | D-016 수정 (EAV 조인 조건 교정), D-020 보강 |

### 결정

`CMM_RESOURCE.RESOURCE_CONF_ID`는 `CORE_CONFIG_PROP.CONFIGURATION_ID`와의 JOIN 조건으로 **사용할 수 없다**. 두 테이블(CMM_RESOURCE <-> CORE_CONFIG_PROP) 간 조인은 반드시 **hostname 기반 값 브릿지 조인(value_joins)**을 통해서만 수행한다.

### 올바른 조인 패턴

```sql
-- 1단계: hostname 값으로 core_config_prop의 Hostname 속성 행을 찾는다
LEFT JOIN core_config_prop p_host
  ON p_host.name = 'Hostname' AND p_host.stringvalue_short = r.hostname
-- 2단계: 동일 configuration_id를 공유하는 다른 EAV 속성을 조인한다
LEFT JOIN core_config_prop p_ostype
  ON p_ostype.configuration_id = p_host.configuration_id AND p_ostype.name = 'OSType'
```

### 수정된 파일

1. **`sqls/02_polestar_eav_patterns.sql`**: EAV 피벗 쿼리의 JOIN을 `RESOURCE_CONF_ID` 기반에서 hostname 브릿지 패턴으로 교체
2. **`src/prompts/structure_analyzer.py`**: LLM 구조 분석 프롬프트에 `join_condition`을 optional로 변경, `value_joins` 필드 안내 추가
3. **`src/nodes/schema_analyzer.py`**: HITL 승인 요약에서 `join_condition` 없을 때 `value_joins` 정보를 표시하도록 개선
4. **`src/nodes/query_generator.py`**: `value_joins`가 있으면 `join_condition`보다 우선하여 LLM에 브릿지 조인 힌트 제공
5. **`src/nodes/multi_db_executor.py`**: query_generator.py와 동일한 value_joins 우선 로직 적용

### Plan 33 보강 (2026-03-26): 3중 방어 + 사후 감지

D-022의 기존 조치에도 불구하고 LLM이 `resource_conf_id` 기반 JOIN을 생성하는 문제를 근본적으로 차단하기 위해 3중 방어 + 사후 감지를 추가하였다.

**추가/수정된 파일:**
1. **`config/db_profiles/polestar_pg.yaml`**: query_guide 금지 문구에 resource_conf_id 명시, `excluded_join_columns` 필드 신규 추가
2. **`src/utils/schema_utils.py`** (신규): `build_excluded_join_map()` 공용 유틸 함수
3. **`src/prompts/query_generator.py`**: 시스템 프롬프트 규칙 10 추가 (JOIN 금지 컬럼 규칙)
4. **`src/nodes/query_generator.py`**: `_format_schema_for_prompt()`에 "-- JOIN 금지" 주석 추가, `_format_structure_guide()`에 금지 컬럼 경고 섹션 추가
5. **`src/nodes/multi_db_executor.py`**: `_format_schema()`에 "-- JOIN 금지" 주석 추가, `_generate_sql()`에 금지 컬럼 경고 추가
6. **`src/nodes/query_validator.py`**: `_check_excluded_join_columns()` 경고 레벨 감지 추가 (ON 절에서 금지 컬럼 사용 시 warning)
7. **`scripts/arch_check.py`**: `src.utils.schema_utils` MODULE_LAYER_MAP 등록

**방어 체계:**
- 1층 (YAML): query_guide에서 금지 문구 명시 + excluded_join_columns 선언
- 2층 (프롬프트): 시스템 규칙 10 + 스키마 출력에 "-- JOIN 금지" 주석 + 구조 가이드에 금지 컬럼 경고
- 3층 (검증): query_validator에서 ON 절 내 금지 컬럼 사용 감지 (현재 warning, 반복 시 error 승격 검토)

### 근거

- 운영 DB 데이터 분석 결과, `CMM_RESOURCE.RESOURCE_CONF_ID`와 `CORE_CONFIG_PROP.CONFIGURATION_ID`가 직접 매핑되지 않음을 확인
- FK 제약이 존재하지 않으며, `RESOURCE_CONF_ID` 기반 조인은 잘못된 결과를 반환함
- `config/db_profiles/polestar_pg.yaml`은 이미 올바른 `value_joins` 패턴을 사용 중이었으나, SQL 패턴 파일과 소스 코드가 구식 조인 방식을 유지하고 있어 불일치 발생
- LLM이 참조하는 모든 소스에서 일관된 조인 패턴을 제시해야 정확한 SQL 생성 가능

### 향후 수정 시 고려사항

- 새로운 value_joins 대응 관계 발견 시 `config/db_profiles/polestar_pg.yaml`의 `value_joins` 배열에 추가
- `join_condition` 필드는 FK가 존재하는 다른 DB에서는 여전히 유효하므로 코드에서 제거하지 않음 (폴백 경로 유지)
- `plans/` 문서의 `RESOURCE_CONF_ID` 참조는 이력 보존 목적으로 수정하지 않음
- 새로운 JOIN 금지 컬럼 추가 시 `config/db_profiles/` YAML의 `excluded_join_columns` 배열에 항목 추가 (코드 변경 불필요)
- `query_validator`의 `_check_excluded_join_columns()` 경고가 운영 로그에서 3회 이상 반복 발생하면 error 승격을 검토

---

## D-023. 데이터 충분성 검사 로직 개선 (Plan 36)

| 항목 | 내용 |
|------|------|
| **결정일** | 2026-03-30 |
| **상태** | 확정 |

### 결정

`result_organizer` 노드의 `_check_data_sufficiency()` 함수를 개편하여, **하드코딩 50% 임계값**을 제거하고 **매핑 출처별 차등 임계값**(`mapping_sources` 기반)과 **.env 설정 가능한 임계값**을 도입한다.

### 변경 내용

1. **`src/config.py`** (`QueryConfig`): `sufficiency_required_threshold` (기본 0.7), `sufficiency_optional_threshold` (기본 0.5) 필드 추가
2. **`.env.example`**: `QUERY_SUFFICIENCY_REQUIRED_THRESHOLD`, `QUERY_SUFFICIENCY_OPTIONAL_THRESHOLD` 항목 추가
3. **`src/nodes/result_organizer.py`**:
   - `_match_column_in_results()`: 인라인 매칭 로직을 별도 함수로 추출 (정확/컬럼명/EAV/대소문자 무시 4단계 매칭)
   - `_classify_mapped_columns()`: mapping_sources 기반 필수(hint/synonym)/선택(llm_inferred) 분류
   - `_check_data_sufficiency()`: 시그니처에 `mapping_sources`, `app_config` 추가, 4-Case 로직 (빈 결과/column_mapping/레거시 template/text 모드)
   - 호출부에 `mapping_sources`, `app_config` 전달

### 하위 호환성

- `mapping_sources=None` (레거시): 모든 non-None 매핑을 required(70%)로 취급 (기존 50%보다 엄격 -- 의도적 강화)
- 빈 결과 + 집계 쿼리: `True` -> `False` (의도적 변경, 재시도 유도)
- 빈 결과 + 일반 조회: 동일 (`True`)
- text 모드: 거의 동일 (결과 컬럼 0개일 때만 `False`)

### 근거

- hint/synonym 매핑(사용자 지정/유사어 정확 매칭)과 llm_inferred(LLM 추론) 매핑은 확신도가 다르므로 동일 임계값 적용은 부적절
- 50% 하드코딩은 불완전한 Excel/Word 결과물을 사용자에게 전달하는 원인
- 운영 환경별 임계값 조정이 필요하므로 `.env` 설정 가능화

### 향후 수정 시 고려사항

- 임계값 변경 시 `.env`의 `QUERY_SUFFICIENCY_REQUIRED_THRESHOLD`, `QUERY_SUFFICIENCY_OPTIONAL_THRESHOLD`만 수정하면 됨
- EAV 피벗 alias가 예측 불가한 형태로 반환될 경우 `_match_column_in_results`에 fuzzy 매칭 확장 가능

---

## D-024. Synonym 통합 관리 + EAV 접두사 비교 정규화 (Plan 37)

| 항목 | 내용 |
|------|------|
| **결정일** | 2026-03-30 |
| **상태** | 확정 |

### 결정

EAV synonym을 `synonyms:global`에도 등록하여 global 비교 인프라를 공유하고, 필드명 비교에 `normalize_field_name()` 정규화를 도입하며, EAV 접두사(`EAV:`)를 파이프라인 전체에서 일관되게 처리한다. 또한 EAV 쿼리 시 정규 컬럼 과도 필터링을 제거한다.

### 변경 내용

1. **Synonym 통합 관리** (그룹 1):
   - `SynonymLoader._process_synonym_data()`: EAV synonym을 `synonyms:eav_names` + `synonyms:global` 양쪽에 등록
   - `cache_manager.get_schema_or_fetch()`: 캐시 미스 시 `auto_generate_descriptions` 설정 참조하여 descriptions/synonyms 자동 생성
   - LLM 추론 결과(Step 2.8, Step 3): EAV는 `eav_names` + `global` 양쪽, 비-EAV는 `redis_cache.add_global_synonym(bare_name)` 직접 호출
   - `_apply_eav_synonym_mapping()`: `global_synonyms` 파라미터 추가, EAV words와 global words를 병합 비교
   - `_load_db_cache_data()`: `global_synonyms` 별도 로드, 반환값 6-tuple로 확장

2. **비교 로직 정규화** (그룹 2):
   - `src/utils/schema_utils.py`에 `normalize_field_name()` 추가: Unicode NFC, 줄바꿈/탭 -> 공백, 다중 공백 축소, strip
   - `excel_parser._detect_header_row()`: 헤더 추출 시 정규화 적용
   - `field_mapper._synonym_match()`, `_apply_synonym_mapping()`, `_apply_eav_synonym_mapping()`: 정규화 후 비교
   - LLM 응답 매칭(Step 2.8, Step 3): `normalized_lookup` 구축하여 퍼지 매칭

3. **EAV 접두사 처리** (그룹 3):
   - `word_writer._get_value_from_row()`: EAV 접두사 처리 추가
   - `excel_writer._get_value_from_row()`: 폴백 매칭에서 EAV 접두사 제거
   - `query_generator`, `multi_db_executor`: **EAV 쿼리 시 정규 컬럼 필터링 제거** (LLM이 JOIN 판단)
   - `result_organizer._match_column_in_results()`: 폴백에서 EAV 접두사 제거
   - `result_organizer._classify_mapped_columns()`: `eav_synonym` 소스를 `required`로 분류

### 근거

- EAV synonym이 `synonyms:eav_names`에만 격리되면 global의 폴백/비교 인프라를 활용 못함
- `synonyms:global`은 bare column name 기반이므로 EAV 속성명도 동일 체계로 관리 가능
- 스키마 최초 조회 시 descriptions/synonyms가 자동 생성되지 않으면 정상 사용 흐름에서 synonym 매칭이 전적으로 LLM 의존
- 엑셀 헤더의 줄바꿈/다중 공백은 `str.strip()`만으로 처리 불가
- EAV 테이블 필터링은 entity 테이블과 config 테이블이 다를 수 있어 정규 컬럼을 잘못 제외

### 향후 수정 시 고려사항

- `normalize_field_name()`에 새 정규화 규칙 추가 시 기존 매칭에 영향이 없는지 확인
- EAV 접두사 처리 로직이 추가된 모듈에서 새 컬럼명 형식 도입 시 해당 로직도 갱신
- 정규 컬럼 필터링 제거로 LLM이 비-EAV 테이블도 프롬프트에서 볼 수 있으므로, 부적절한 JOIN이 생성되면 프롬프트 튜닝 필요

---

## D-025. 3계층 하이브리드 필드 매핑 전파 정합성 (Plan 38)

| 항목 | 내용 |
|------|------|
| **결정일** | 2026-03-30 |
| **상태** | 구현 완료 |
| **이전 결정** | D-007 확장, D-012 확장, D-024 확장 |

### 결정

field_mapper가 생성한 column_mapping 형식(`"cmm_resource.hostname"`, `"EAV:OSType"`)과 query_generator가 생성한 SQL alias 형식(`"cmm_resource_hostname"`, `"os_type"`)의 불일치를 **3계층 하이브리드 매칭**으로 해결한다.

### 핵심 변경사항

1. **`src/utils/column_matcher.py` 신규**: 규칙 기반 매칭 유틸 (LLM 의존 없음, utils 계층)
   - `resolve_column_key()`: 7단계 매칭 (정확, table.column분리, EAV접두사, 대소문자, dot->underscore, CamelCase<->snake_case, 오타 편집거리1)
   - `build_resolved_mapping()`: column_mapping 전체를 결과 키로 해석, unresolved 필드 목록 반환
   - `camel_to_snake()`, `_is_close_match()`: 정규화/오타 대응 유틸
2. **`src/prompts/column_resolver.py` 신규**: LLM 유사성 판단 프롬프트 (prompts 계층)
3. **`src/state.py` 수정**: `OrganizedData`, `SheetMappingResult`에 `resolved_mapping: Optional[dict]` 추가
4. **`src/nodes/result_organizer.py` 수정**:
   - `_match_column_in_results()` 리팩터: `resolve_column_key` 유틸로 위임 (시그니처 유지)
   - `_resolve_unmatched_via_llm()` 신규: Layer 2 LLM 유사성 판단 (미해결 항목에만 호출)
   - Step 4.5: Layer 1 (규칙) + Layer 2 (LLM) -> `resolved_mapping` 생성
5. **`src/nodes/output_generator.py` 수정**: `resolved_mapping` 우선, `column_mapping` 폴백
6. **`src/document/excel_writer.py` 수정**: `_get_value_from_row`에 Layer 3 폴백 추가 (CamelCase<->snake_case, 오타 대응)
7. **`src/document/word_writer.py` 수정**: 동일 Layer 3 폴백

### 3계층 구조

```
Layer 1 (규칙): build_resolved_mapping()       -> 80%+ 즉시 해결
Layer 2 (LLM):  _resolve_unmatched_via_llm()   -> 축약/창의적 alias 대응
Layer 3 (폴백): _get_value_from_row() 정규화    -> 레거시 경로 대비
```

### 근거

- Layer 1이 대부분의 케이스를 지연 없이 해결하므로 LLM 호출 비용/지연 최소화
- Layer 2는 미해결 항목(축약 alias, 재명명)에만 소규모 컨텍스트로 호출
- Layer 3은 resolved_mapping이 없는 레거시 경로를 커버

### 향후 수정 시 고려사항

- `resolve_column_key`에 새 매칭 단계 추가 시 우선순위(정확 매칭 최우선) 유지
- `_is_close_match` 편집거리를 2 이상으로 확장하면 오탐 위험, 신중히 판단
- Layer 2 LLM 실패 시 graceful 처리(Layer 3 위임)가 유지되는지 확인

---

## 변경 이력

| 날짜 | 결정 ID | 변경 내용 |
|------|---------|----------|
| 2026-03-30 | D-025 | 3계층 하이브리드 필드 매핑 전파 정합성 (Plan 38): column_matcher.py 신규, column_resolver.py 프롬프트 신규, resolved_mapping State 추가, result_organizer Layer 1+2 통합, output_generator resolved_mapping 우선, excel_writer/word_writer Layer 3 폴백 |
| 2026-03-30 | D-024 | Synonym 통합 관리 + EAV 접두사 비교 정규화 (Plan 37): EAV synonym global 통합, normalize_field_name 도입, 스키마 조회 시 synonym 자동 생성, word_writer/excel_writer/result_organizer EAV 접두사 처리, query_generator 정규 컬럼 필터링 제거, eav_synonym 소스 분류 |
| 2026-03-30 | D-023 | 데이터 충분성 검사 로직 개선 (Plan 36): mapping_sources 기반 차등 임계값 도입, _match_column_in_results/_classify_mapped_columns 추출, QueryConfig에 sufficiency 임계값 추가 |
| 2026-03-26 | D-022 | Plan 33 보강: 3중 방어 + 사후 감지. excluded_join_columns YAML 필드, 시스템 프롬프트 규칙 10, 스키마 "-- JOIN 금지" 주석, 구조 가이드 금지 컬럼 경고, query_validator ON 절 감지. src/utils/schema_utils.py 신규 |
| 2026-03-26 | D-022 | RESOURCE_CONF_ID JOIN 금지: hostname 브릿지 조인 필수화. SQL 패턴 파일, 구조 분석 프롬프트, query_generator/multi_db_executor의 조인 힌트 로직을 value_joins 우선으로 변경 |
| 2026-03-26 | D-011 | 캐시 유효성 검증 및 무효화 정합성 개선 (Plan 30): save_schema/descriptions/synonyms 저장 전 유효성 검증 게이트, invalidate 정책 변경 (DB별 synonyms/descriptions도 삭제, 글로벌 사전만 보존), stale entry 자동 정리 (cleanup_stale_entries), 파일 캐시 인메모리 버퍼 |
| 2026-03-25 | D-021 | Gemini API 프로바이더 추가 + .encenv 민감 키 분리 (Plan 28): LLMConfig.provider gemini 추가, _create_gemini() 팩토리, .encenv 도입, langchain-google-genai optional dep |
| 2026-03-25 | D-019 | Fingerprint TTL 기반 Redis 캐시 최적화 (Plan 26): fingerprint_ttl_seconds 설정, fingerprint_checked_at Redis 키, 2차-A/2차-B 캐시 분기, multi_db_executor SchemaCacheManager 통합 |
| 2026-03-24 | D-018 | LLM 지능형 필드 매핑 (Plan 22): LLM 통합 추론 (synonyms+descriptions 컨텍스트), 즉시 Redis 등록, 매핑 보고서 MD 생성/파싱, MD 수정/업로드 피드백, API 2개 신규, 프론트엔드 다운로드/업로드 UI |
| 2026-03-24 | D-009 | Plan 23 UI 수정: SSE 연동 인디케이터, 스트리밍 다운로드 버튼, Fallback Progress Panel, thread_id 전달, URL encodeURIComponent 보안 강화 |
| 2026-03-24 | D-017 | EAV Field Mapper 전체 파이프라인 지원: _apply_eav_synonym_mapping 신규, perform_3step_mapping 2.5단계, EAV: 접두사 규약, field_mapper 프롬프트 EAV 가이드, _validate_mapping EAV 검증, query_generator EAV 피벗 힌트 |
| 2026-03-24 | D-016 | EAV 비정규화 테이블 쿼리 지원: polestar_patterns.py 신규, schema_analyzer 자동 감지, query_generator Polestar 가이드, DB 엔진별 LIMIT 문법, query_validator DB2 대응 |
| 2026-03-23 | D-015 | Excel→CSV 변환 LLM 컨텍스트 보강: CsvSheetData, excel_to_csv(), 시트별 순환 LLM 호출, field_mapper 예시 데이터 프롬프트 |
| 2026-03-19 | D-014 | 자체 MCP 서버 구축: mcp_server/ 독립 패키지, SSE transport, DB2 지원, 설정 분리, DBHubConfig/QueryConfig/MultiDBConfig 재설계 |
| 2026-03-18 | D-013 | Phase 3 멀티턴 대화 + Human-in-the-loop 구현: context_resolver, approval_gate, synonym_registrar 노드 신설, 체크포인트 기반 State 복원, API 멀티턴 지원 |
| 2026-03-17 | D-012 | 매핑-우선(Mapping-First) 전략 도입: field_mapper 노드 신설, 3단계 매핑, 유사어 등록 플로우 |
| 2026-03-17 | D-007 | Phase 2 구현 완료: Excel/Word 파싱, LLM 의미 매핑, 문서 생성 |
| 2026-03-17 | D-008 | Phase 2 진행 상태 업데이트: 완료 |
| 2026-03-17 | D-009 | 사용자 UI 채팅 인터페이스 + SSE 스트리밍 결정 추가 |
| 2026-03-17 | D-007 | 멀티시트 독립 매핑 지원 추가: 시트별 필드 매핑, target_sheets 필터링 |
| 2026-03-17 | D-004 | v1(키워드+LLM) → v2(LLM 전용)로 개정. 키워드 기반 분류 완전 제거 |
| 2026-03-17 | D-004 | 사용자 직접 DB 지정 기능 추가 (aliases 필드) |
| 2026-03-17 | D-005 | 멀티 DB 결과 병합 시 `db_result_summary` 생성 추가 |
| 2026-03-17 | D-010 | 3단계 스키마 캐싱 결정 추가 (메모리->파일->DB, fingerprint 변경감지) |
| 2026-03-17 | D-011 | Redis 기반 스키마 캐시 + LLM 컬럼 설명/유사 단어 구현 |
| 2026-03-18 | D-011 | 유사단어 2계층(DB별+글로벌), source 태깅, invalidate 보존, 프롬프트 기반 synonym CRUD 완성 |
| 2026-03-18 | D-011 | 글로벌 유사단어 description 확장: synonyms:global value를 {words, description} 형태로 확장, update-description action, list-synonyms에 description 표시 |
| 2026-03-18 | D-011 | 프롬프트 기반 글로벌 유사 단어 LLM 생성: generate-global-synonyms action, seed_words 지원, 기존 항목 merge |
| 2026-03-18 | D-011 | Smart Synonym Reuse: 글로벌 사전에 없는 새 필드 추가 시 LLM 유사 컬럼 탐색 및 재활용 제안, pending_synonym_reuse State, reuse/new/merge 모드 |
| 2026-03-25 | D-020 | LLM 기반 범용 스키마 구조 분석: Polestar 하드코딩 제거, LLM+HITL 기반 DB 구조 자동 감지 |
| 2026-03-17 | 전체 | 초기 decision.md 작성 |
