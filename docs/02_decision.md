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
26. [사용자 로그인 및 인증 시스템](#d-026-사용자-로그인-및-인증-시스템-plan-39)
27. [사용자 행위 감사 로깅 강화](#d-027-사용자-행위-감사-로깅-강화-plan-40)
28. [Polestar 불필요 lookup 테이블 JOIN 차단](#d-028-polestar-불필요-lookup-테이블-join-차단)
29. [알람 조회 의도 분리 + 알람 전용 쿼리 템플릿 주입](#d-029-알람-조회-의도-분리--알람-전용-쿼리-템플릿-주입-plan-44)
30. [ALARMSEVERITY=0 해소 상태 이력 쿼리 포함](#d-030-alarmseverity0-해소-상태-이력-쿼리-포함-plan-45)
31. [알람 소켓 수신 → LLM 분석 → worKB 발송](#d-031-알람-소켓-수신--llm-분석--workb-발송-plan-46)
32. [폴스타 알람 메시지 포맷 확정](#d-032-폴스타-알람-메시지-포맷-확정--단일행-json--alarmevent-필드-재설계-plan-46-개정)
33. [처리 현황에 유사어 매핑 표시 — SQL 기반 역조회](#d-033-처리-현황에-유사어-매핑-표시--생성된-sql-기반-역조회)
34. [알람 이력 기반 패턴 분석 — 폴스타 DB 직접 조회 (Plan 47)](#d-035-알람-이력-기반-패턴-분석--폴스타-db-직접-조회-plan-47)
35. [알람 영향 프로세스 보강 — 폴스타 실시간 프로세스 API (Plan 47-1)](#d-036-알람-영향-프로세스-보강--폴스타-실시간-프로세스-api-plan-47-1)
36. [deepagents 기반 의도 분해 오케스트레이션 (Plan 48)](#d-037-deepagents-기반-의도-분해-오케스트레이션-plan-48)

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
| **개정일** | 2026-06-10 |
| **상태** | 확정 (D-037로 복합 의도 분해 오케스트레이션 확장 계획 — Plan 48) |
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

### routing_intent 값 목록

| intent | 설명 | 라우팅 대상 노드 |
|--------|------|----------------|
| `data_query` | 일반 인프라 데이터 조회 | `schema_analyzer` 또는 `multi_db_executor` |
| `alarm_query` | 알람/모니터링 이벤트 조회 | `schema_analyzer` 또는 `multi_db_executor` |
| `cache_management` | 스키마 캐시 관리, 유사어 관리, 컬럼 설명 변경 | `cache_management` |
| `synonym_registration` | 유사어 등록 (pending 상태에서 사용자 확인 후) | `synonym_registrar` |
| `general_inference` | DB 조회 불필요 (IT 개념 설명, 에이전트 능력 문의, 범위 외 요청, 인사말 등) | `general_inference` |

### route_after_semantic_router() 설계

**변경 전 (if-chain 방식)**: 새 intent 추가 시마다 함수 내부 if 분기 수정 필요.

**변경 후 (레지스트리 방식, 2026-06-10 적용)**:

```python
_INTENT_ROUTE_MAP: dict[str, str] = {
    "cache_management": "cache_management",
    "synonym_registration": "synonym_registrar",
    "general_inference": "general_inference",
}

def route_after_semantic_router(state: AgentState) -> str:
    intent = state.get("routing_intent")
    if intent in _INTENT_ROUTE_MAP:
        return _INTENT_ROUTE_MAP[intent]
    if state.get("is_multi_db"):
        return "multi_db_executor"
    return "schema_analyzer"
```

`_INTENT_ROUTE_MAP`에 등재된 intent는 `is_multi_db` 여부와 무관하게 고정 노드로 라우팅된다.
`data_query` / `alarm_query`는 map에 없으므로 기존 multi_db 분기 로직을 그대로 탄다.

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
- **새 intent 추가 시 3곳 수정**: (1) `_INTENT_ROUTE_MAP`에 `{intent: node_name}` 추가, (2) `build_graph()`에 노드 등록, (3) `conditional_edges` dict에 항목 추가
- **복합 의도 분해 확장(D-037, Plan 48)**: 단일 의도 라우팅을 planner 기반 다중 task 분해로 확장 예정. `semantic_router`의 DB 분류 로직은 폐기하지 않고 `data_query` subagent가 재사용. `ENABLE_DEEPAGENT_ORCHESTRATION` 플래그로 제어

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

### 토큰 스트리밍 구현 (2026-06-24 보강)

토큰 단위 스트리밍이 실제로 동작하려면 노드가 LLM을 `.astream()`으로 호출해야 한다.
`ainvoke()`는 `_agenerate`(단일 호출) 경로를 타므로 `astream_events`가
`on_chat_model_stream` 토큰 이벤트를 내보내지 않아, 응답이 한 번에 출력된다.

- 최종 사용자 응답 생성 노드(`output_generator`, `general_inference`)는 공용
  헬퍼 `src/llm.py::astream_text(llm, messages, tags=[USER_RESPONSE_TAG])`로 호출한다.
- SSE 핸들러는 **노드명이 아닌 `USER_RESPONSE_TAG` 태그**로 토큰을 거른다.
  orchestration 경로(`agent_orchestrator`)에서는 SQL 생성·DB 분류·최종 응답이 모두
  같은 노드에서 일어나므로, 노드명 필터로는 SQL/분류 토큰이 채팅으로 새어 나간다.
- 복합(composite) 질의는 같은 레벨 task가 `asyncio.gather`로 병렬 실행되어 토큰이
  뒤섞일 수 있다. `done` 이벤트에 권위 있는 `response`(최종 `final_response`)를 실어
  보내고, 프론트엔드가 마무리 시점에 누적 토큰 대신 이 값으로 보정한다.
- `_astream` 미구현 클라이언트(FabriX OpenAI 호환/Ollama)는 `.astream()`이 단일
  청크로 폴백되므로 회귀가 없다(KBGenAIChat은 `_astream` 구현).

### 향후 수정 시 고려사항

- 멀티턴 대화(Phase 3) 구현 시 `thread_id`를 세션에서 자동 관리
- WebSocket 전환 검토 시 SSE의 단방향 한계와 WebSocket의 양방향 이점 비교 필요
- 기존 `/api/v1/query` 엔드포인트는 CLI/API 클라이언트용으로 유지
- 신규 "최종 사용자 응답" LLM 호출을 추가하면 반드시 `astream_text`+`USER_RESPONSE_TAG`를
  사용해야 스트리밍된다(중간 LLM 호출에는 태그를 붙이지 말 것)

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
| `DBHubConfig.server_url` | (없음) | `http://localhost:9099/sse` |
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

## D-026. 사용자 로그인 및 인증 시스템 (Plan 39)

| 항목 | 내용 |
|------|------|
| **결정일** | 2026-04-01 |
| **상태** | 구현 완료 |
| **이전 결정** | D-006 확장 (설정 계층화) |

### 결정

사용자 인증 시스템을 **AUTH_ENABLED=false 기본** (개발단계 무인증) 방식으로 구현한다. DB 기반 사용자 저장, 자유 가입 + 관리자 권한 부여, SAML SSO 확장 기반(AuthProvider 추상화)을 적용한다.

### 핵심 변경사항

1. **`src/domain/auth.py` 신규**: AuthProvider ABC, AuthMethod 열거형 (domain 계층)
2. **`src/domain/user.py` 신규**: User 엔터티, UserRole/UserStatus 열거형, UserRepository/AuditRepository ABC (domain 계층)
3. **`src/utils/password.py` 신규**: bcrypt 해싱/검증 유틸리티 (utils 계층)
4. **`src/infrastructure/auth_provider.py` 신규**: LocalAuthProvider (infrastructure 계층)
5. **`src/infrastructure/user_repository.py` 신규**: PostgresUserRepository (infrastructure 계층)
6. **`src/infrastructure/audit_repository.py` 신규**: PostgresAuditRepository (infrastructure 계층)
7. **`src/api/dependencies.py` 신규**: require_user, get_current_user 의존성, ANONYMOUS_USER (interface 계층)
8. **`src/api/routes/user_auth.py` 신규**: 가입/로그인/로그아웃/비밀번호변경/인증상태 API (interface 계층)
9. **`src/config.py` 수정**: AuthConfig 추가 (AUTH_ENABLED, auth_db_url, jwt_expire_hours 등)
10. **`src/state.py` 수정**: user_id, user_department, allowed_db_ids 필드 추가
11. **`src/api/routes/query.py` 수정**: Depends(require_user) 적용, 사용자 컨텍스트 주입
12. **`src/api/routes/conversation.py` 수정**: Depends(require_user) 적용
13. **`src/api/routes/admin.py` 수정**: 사용자 관리/권한/감사로그 API 추가
14. **`src/api/server.py` 수정**: 인증 DB 초기화, DDL 자동 실행, user_auth 라우터 등록
15. **`src/api/schemas.py` 수정**: 사용자 인증 관련 Pydantic 모델 추가
16. **`scripts/arch_check.py` 수정**: domain, infrastructure, utils.password 모듈 매핑 추가
17. **`ddl/auth_tables.sql` 신규**: auth_users, audit_logs 테이블 DDL
18. **UI**: login.html, register.html 신규, index.html/app.js에 인증 헤더 주입, 관리자 대시보드에 사용자 관리/감사로그 탭 추가
19. **`pyproject.toml` 수정**: bcrypt>=4.0.0 의존성 추가

### 설계 원칙

| 원칙 | 설명 |
|------|------|
| 인증 비활성화 기본 | `AUTH_ENABLED=false` 기본값. 개발 환경에서 인증 없이 모든 기능 동작 |
| DB 기반 저장 | PostgreSQL에 저장, 향후 DB2 전환 가능 (raw SQL + asyncpg, ORM 미사용) |
| 자유 가입 + 관리자 권한 부여 | 사용자 직접 가입 (승인 불필요), 관리자가 역할/권한 부여 |
| SAML SSO 확장 기반 | AuthProvider 추상화로 ID/PW 외 SAML SSO 연동 가능 구조 |
| JWT 시크릿 공유 | AdminConfig.jwt_secret을 사용자 토큰에도 공유, `type` 클레임으로 구분 |
| 실시간 권한 반영 | 토큰에 최소 정보만 포함, 매 요청마다 DB에서 최신 사용자 정보 조회 |

### 근거

- 개발 초기에 인증 없이 기능 개발/테스트 가능 (AUTH_ENABLED=false)
- Clean Architecture 계층 분리: domain(인터페이스) -> infrastructure(구현체) -> interface(API)
- 기존 admin_auth.py와 구조를 공유하되 사용자 인증은 별도 라우터로 분리
- bcrypt 비밀번호 해싱, 로그인 시도 제한(5회), 계정 잠금(30분) 등 보안 강화

### 향후 수정 시 고려사항

- SAML SSO 연동 시 `SamlAuthProvider` 구현체만 추가하면 됨
- DB2 전환 시 `Db2UserRepository`, `Db2AuditRepository` 구현체만 교체
- Redis 토큰 블랙리스트 추가 시 `require_user`에 블랙리스트 검사 추가
- `allowed_db_ids`는 Plan 41 접근 제어의 전제 조건

---

## D-027. 사용자 행위 감사 로깅 강화 (Plan 40)

| 항목 | 내용 |
|------|------|
| **결정일** | 2026-04-02 |
| **상태** | 확정 |

### 결정

1. **이중 기록 유지**: 기존 JSONL 파일 + PostgreSQL DB 이중 기록 구조를 유지한다.
2. **SQLite 대신 PostgreSQL 사용**: Plan 40 원안은 조회/통계용으로 SQLite를 제안했으나, Plan 39에서 이미 PostgreSQL 기반 `audit_logs` 테이블을 구축했으므로 PostgreSQL을 확장하여 사용한다.
3. **통합 AuditService**: `src/security/audit_service.py`에 통합 서비스를 구현하여 JSONL과 DB 기록을 단일 인터페이스로 통합한다.
4. **AuditMiddleware**: `src/api/middleware/audit_middleware.py`에서 요청별 request_id와 client_ip를 자동 수집한다.
5. **10개 이벤트 유형**: 기존 2개(user_request, query_execution)에서 10개로 확장 (user_login, user_logout, login_fail, register, password_change, data_access, file_download, security_alert, admin_action, cache_operation).
6. **보안 경고 자동 감지**: 금지 SQL 시도, SQL 인젝션 패턴, 대량 데이터 조회, 로그인 실패 반복 시 security_alert 이벤트를 자동 생성한다.

### 근거

- PostgreSQL은 이미 인증 시스템에서 사용 중이며, 별도 SQLite 파일을 추가하면 관리 포인트가 증가한다.
- JSONL은 빠른 쓰기와 운영 디버깅에 유리하고, PostgreSQL은 복잡한 조회/통계에 유리하므로 이중 기록이 최적이다.
- AuditService로 통합하면 각 노드/라우트에서 개별 저장소를 직접 호출하지 않아 의존성이 단순해진다.

### 고려한 대안

| 대안 | 제외 이유 |
|------|----------|
| SQLite 병행 (Plan 40 원안) | PostgreSQL이 이미 구축되어 있어 중복 관리 비용 |
| JSONL만 확장 | 복잡한 조회/통계에 부적합 (파일 스캔 필요) |
| PostgreSQL만 사용 (JSONL 제거) | 운영 환경에서 빠른 파일 로그 확인이 불가 |

### 향후 수정 시 고려사항

- 감사 로그 테이블이 대량화되면 파티셔닝(월별) 적용 검토
- `AuditConfig.retention_days`로 보관 기간 관리 (현재 90일)
- 실시간 보안 경고 알림은 별도 알림 시스템(이메일, 슬랙) 연동 필요

---

## D-028. Polestar 불필요 lookup 테이블 JOIN 차단

| 항목 | 내용 |
|------|------|
| **결정일** | 2026-04-02 |
| **상태** | 확정 |
| **이전 결정** | D-022 보강 (3중 방어 체계를 vendor_id/os_id/os_param_id로 확장) |

### 결정

Polestar DB의 `cmm_vendor`, `cmm_os`, `cmm_os_param` 테이블은 쿼리 대상에서 제외한다. 해당 테이블의 데이터는 `core_config_prop` EAV에 속성(`Vendor`, `OSType`, `OSParameter`)으로 존재하므로 직접 JOIN이 불필요하다.

### 조치

1. **YAML 프로필** (`config/db_profiles/polestar.yaml`, `polestar_pg.yaml`): EAV 패턴에 `excluded_join_columns` 추가 (vendor_id, os_id, os_param_id) — 기존 3중 방어 체계(프롬프트 "-- JOIN 금지" 주석 + 구조 가이드 경고 + validator 감지)가 자동으로 작동
2. **YAML 프로필**: `allowed_tables` 필드 신규 추가 (cmm_resource, core_config_prop만 허용) — 근본적으로 불필요 테이블 차단
3. **`src/nodes/schema_analyzer.py`**: `_load_manual_profile()`에서 `allowed_tables`를 읽어 `relevant_tables`를 필터링
4. **`src/nodes/query_validator.py`**: `_validate_forbidden_joins()` 패턴 3 추가 — excluded_join_columns 컬럼이 config_table 외 임의 테이블과의 JOIN에 사용되어도 에러로 감지

### 근거

- `cmm_vendor`, `cmm_os`, `cmm_os_param`은 레거시 lookup 테이블로, 실제 운영 데이터 조회에 사용하지 않음
- LLM이 DB 스키마에서 FK-like 컬럼명(vendor_id, os_id, os_param_id)을 보고 불필요한 JOIN을 생성
- 기존 D-022의 3중 방어 체계는 `resource_conf_id ↔ configuration_id` 패턴만 커버하여 이 문제를 차단하지 못했음

### 향후 수정 시 고려사항

- `allowed_tables`는 선택적 필드이므로 미설정 DB는 기존 동작 유지 (하위 호환성 보장)
- Polestar에 새 테이블이 추가되면 `allowed_tables`에 명시적 등록 필요
- 다른 DB에서 유사 문제 발생 시 해당 DB의 YAML에 `allowed_tables` + `excluded_join_columns` 추가

---

## D-029. 알람 조회 의도 분리 + 알람 전용 쿼리 템플릿 주입 (Plan 44)

| 항목 | 내용 |
|------|------|
| **결정일** | 2026-05-29 |
| **상태** | 확정 |
| **이전 결정** | D-004 확장 (시멘틱 라우팅), D-016 확장 (Polestar 쿼리) |

### 결정

알람/모니터링 관련 질의에 대해 `routing_intent = "alarm_query"`를 독립 의도로 분류하고,
이를 `query_generator`까지 전파하여 알람 전용 프롬프트 템플릿(`POLESTAR_ALARM_QUERY_GENERATOR_SYSTEM_TEMPLATE`)을 주입한다.

### 핵심 변경사항

1. **`src/routing/domain_config.py`**: polestar 4개 도메인 description에 모니터링/알람 테이블(CMM_ALARM, CMM_ALARM_DEF 등) 및 컬럼 정보 추가 — LLM이 알람 질의 시 polestar DB를 높은 관련도로 평가하도록 유도
2. **`src/prompts/semantic_router.py`**: 출력 JSON의 `intent` 필드에 `"alarm_query"` 추가, `## 알람 조회 판단` 섹션 + 예시 7건 추가
3. **`src/nodes/query_generator.py`**: `_build_system_prompt()` 호출부 + 시그니처에 `routing_intent` 파라미터 추가, 템플릿 선택 분기에 `alarm_query` 조건 추가
4. **`src/prompts/query_generator.py`**: `POLESTAR_ALARM_QUERY_GENERATOR_SYSTEM_TEMPLATE` 상수 추가 (Template C-1~C-5)

### 설계 원칙 (방안 2 채택)

`routing_intent`를 semantic_router → State → query_generator로 결정론적으로 전달하여,
LLM의 자율 선택이 아닌 **의도 기반 강제 주입**으로 올바른 쿼리 패턴을 보장한다.

### backward compatibility

- `routing_intent`가 None이거나 "data_query"이면 기존 `POLESTAR_QUERY_GENERATOR_SYSTEM_TEMPLATE` 그대로 사용 (변경 없음)
- 비-Polestar DB에는 영향 없음 (polestar_db_ids 조건 유지)

### 알람 Template 구조

| Template | 용도 |
|---------|------|
| C-1 | 현재 활성 알람 목록 (CMM_ALARM_ACTIVE JOIN 포함) |
| C-2 | 서버 알람 기간 이력 (RESOURCE_TYPE 필터 + 기간 조건) |
| C-3 | 서버 CPU/메모리 알람 기간 이력 (CONDITIONLOGTEXT LIKE 필터 추가) |
| C-4 | 알람 집계 (GROUP BY, GROUP_PATH 불필요) |
| C-5 | 전체 장비 알람 기간 이력 (RESOURCE_TYPE 무관) |

### 주의사항

- polestar/polestar_b0는 DB2 엔진이나 Template C는 PostgreSQL 문법으로 작성됨. PostgreSQL 대상(polestar_cm_gp, polestar_cm_yd)에서 우선 검증 필요
- 알람 테이블(CMM_ALARM 등)이 Redis 스키마 캐시에 없으면 해당 polestar DB 캐시 갱신 필요

---

## D-030. ALARMSEVERITY=0 해소 상태 이력 쿼리 포함 (Plan 45)

| 항목 | 내용 |
|------|------|
| **결정일** | 2026-06-01 |
| **상태** | 확정 |
| **이전 결정** | D-029 확장 (알람 쿼리 템플릿) |

### 결정

ALARMSEVERITY=0은 알람 해소 상태를 나타내며, 이력 조회 쿼리에서 기본 포함 대상으로 처리한다.
활성 알람 조회(CMM_ALARM_ACTIVE JOIN)는 JOIN 구조상 0이 자연 배제되므로 별도 필터 불필요.

### 핵심 변경사항

1. **`src/routing/domain_config.py`**: polestar 4개 도메인 description의 알람 심각도 설명을 `"알람 심각도(1=주의/2=경고/3=심각)"` → `"알람 심각도(0=해소/1=주의/2=경고/3=심각)"` 으로 변경 — LLM이 0=해소를 인식하도록 보강
2. **`src/prompts/query_generator.py`**: POLESTAR_ALARM_QUERY_GENERATOR_SYSTEM_TEMPLATE 수정
   - [필수 WHERE 조건] 섹션: 활성/이력 쿼리 분리 — C-1은 IN (1,2,3), C-2~C-5는 IN (0,1,2,3)
   - [심각도 매핑] 섹션: `해소/해제/resolved/cleared/normal → ALARMSEVERITY = 0` 추가
   - [심각도 0(해소)과 활성/이력 분기] 섹션 신규 추가
   - Template C-1~C-5 CASE WHEN: `WHEN CA.ALARMSEVERITY = 0 THEN '해소'` 추가
   - Template C-2~C-5 WHERE: `IN (1, 2, 3)` → `IN (0, 1, 2, 3)` 변경
   - Template C-4 집계 컬럼: `"해소_수"` 컬럼 추가
3. **`plans/44-polestar-monitoring-alert-routing.md`**: 심각도 코드 정의 2곳에 `ALARMSEVERITY = 0 → "해소" (Resolved/Cleared)` 추가

### 설계 원칙

| 쿼리 유형 | JOIN 구조 | ALARMSEVERITY 조건 | 이유 |
|---|---|---|---|
| 현재 활성 알람 (C-1) | CMM_ALARM_ACTIVE JOIN 포함 | IN (1, 2, 3) | ALARM_ACTIVE가 해소 레코드를 이미 배제 |
| 이력 조회 (C-2~C-5) | CMM_ALARM_ACTIVE JOIN 없음 | IN (0, 1, 2, 3) | 발생→해소 전체 이력 반환 필요 |
| 해소만 조회 | CMM_ALARM_ACTIVE JOIN 없음 | = 0 단독 | 사용자 명시 요청 시 |

### 근거

- ALARMSEVERITY=0 레코드가 이력 쿼리에서 배제되면 "지난달 알람 이력" 등의 요청에서 해소된 알람이 누락됨
- CASE WHEN에 0이 없으면 해소 레코드의 `등급` 컬럼이 공백('')으로 출력됨
- 활성 알람 조회는 CMM_ALARM_ACTIVE가 INNER JOIN으로 해소 레코드를 구조적으로 배제하므로 추가 필터 불필요

### 향후 수정 시 고려사항

- 사용자가 "해소된 알람만 조회" 요청 시: ALARMSEVERITY = 0 단독 + CMM_ALARM_ACTIVE JOIN 제외
- polestar_b0(DB2 엔진) 에서 동일 템플릿 적용 시 DB2 문법 호환성 확인 필요

---

## D-031. 알람 소켓 수신 → LLM 분석 → worKB 발송 (Plan 46)

| 항목 | 내용 |
|------|------|
| **결정일** | 2026-06-04 |
| **상태** | 구현 완료 |
| **이전 결정** | D-014 확장 (독립 프로세스 분리 원칙), D-029 연계 (알람 기능) |

### 결정

알람 소켓 수신을 `alarm_server/`(독립 프로세스)로 분리하고, 에이전트 서버(`src/alarm/`)와 Redis Stream(`alarm:raw`)으로 연결한다. 알람 분석·발송은 2-노드 LangGraph 서브그래프(`AlarmAnalysisGraph`)로 구현한다.

### 구현 내용

| 파일 | 역할 | 계층 |
|------|------|------|
| `alarm_server/__init__.py` | 패키지 설명 | alarm_server (독립) |
| `alarm_server/config.py` | AlarmServerConfig (ALARM_SERVER_ 접두사) | alarm_server (독립) |
| `alarm_server/base_receiver.py` | BaseReceiver 추상 클래스 (Redis 발행 공통) | alarm_server (독립) |
| `alarm_server/tcp_receiver.py` | TcpReceiver (asyncio TCP, 포트 9100) | alarm_server (독립) |
| `alarm_server/__main__.py` | python -m alarm_server 진입점 | alarm_server (독립) |
| `alarm_server/alarm_server.env` | 소켓 서버 전용 env (.gitignore) | 설정 |
| `src/config.py` | AlarmConfig, WorkbConfig 추가 + AppConfig.alarm/workb 필드 | config |
| `src/alarm/domain/alarm.py` | AlarmEvent, AlarmAnalysisResult dataclass | domain |
| `src/alarm/prompts/alarm_analyzer.py` | 시스템 프롬프트 + 유저 템플릿 | prompts |
| `src/alarm/infrastructure/redis_queue.py` | Redis Stream XREAD 헬퍼 | infrastructure |
| `src/alarm/application/nodes/alarm_analyzer.py` | LLM 분석 노드 | application |
| `src/alarm/application/nodes/alarm_notifier.py` | worKB/webhook 발송 노드 | application |
| `src/alarm/orchestration/alarm_graph.py` | 2-노드 LangGraph 서브그래프 | orchestration |
| `src/alarm/application/alarm_worker.py` | Redis Stream 소비 + dedup + 그래프 호출 | orchestration |
| `src/api/server.py` | lifespan에 AlarmWorker 백그라운드 태스크 추가 | interface |

### 핵심 설계 결정

| 항목 | 결정 |
|------|------|
| 발송 채널 | 현재 worKB 단일 지원. Generic Webhook 분기 구조 포함. Slack 제외 (외부망 불가) |
| TCP 수신 포트 | 9100 (ALARM_SERVER_SOCKET_PORT) |
| Redis Stream 키 | alarm:raw |
| 중복 제거 | in-memory dedup dict (alarm_id TTL 기반, dedup_ttl_seconds=300) |
| 심각도 필터 | min_severity=2 기본 (경고 이상만 처리) |
| worKB 토큰 | .encenv 저장 (기존 AdminConfig/LLMConfig 패턴 동일) |
| arch_check 계층 | AlarmWorker: orchestration (그래프 조합 역할), nodes: application |

### 근거

- `mcp_server/`로 DB 접근을 분리한 것과 동일한 원칙 — 라이프사이클 독립, 설정 분리, 독립 배포
- Redis Stream으로 alarm_server 재시작 시에도 알람 유실 방지
- 현재 내부망 환경에서 worKB가 유일하게 사용 가능한 채널 (Slack 외부망 불가)

### 대안 (미채택)

| 대안 | 미채택 이유 |
|------|-----------|
| 에이전트 서버 내 asyncio 태스크로 통합 | 에이전트 재시작 시 소켓 끊김, 로그 혼재 |
| FastAPI WebSocket 수신 | 폴스타가 TCP 소켓 방식으로 전송, 프로토콜 불일치 |

---

## D-032. 폴스타 알람 메시지 포맷 확정 — 단일행 JSON + AlarmEvent 필드 재설계 (Plan 46 개정)

| 항목 | 내용 |
|------|------|
| **결정일** | 2026-06-09 |
| **상태** | 확정 |
| **이전 결정** | D-031 개정 (AlarmEvent 필드 체계 변경) |

### 결정

폴스타 알람 메시지 포맷을 **단일행 JSON**으로 확정하고, `AlarmEvent` 필드 구조를 실제 폴스타 템플릿 변수와 정확히 1:1 대응하도록 전면 재설계한다.

### 폴스타 등록 템플릿 (최종 확정)

```
{"dbId":"<인스턴스_DB_ID>","serverName":"${platformName}","hostname":"${hostname}","ipAddress":"${ipAddress}","resourceAncestry":"${resourceAncestry}","alarmId":"${alarmId}","severity":${severity},"alarmStatus":"${alarmStatus}","resourceType":"${resourceType}","alarmName":"${alarmName}","alarmTime":"${formatAlarmDate('yyyyMMddHHmmss')}","conditions":"${conditions}","conditionLog":"${conditionLog}"}
```

- `dbId`: 폴스타 인스턴스마다 **상수로 직접 기입** (템플릿 변수 아님)
- `serverName`: `${platformName}` 렌더링 결과 — DB의 `server_name` 컬럼과 매핑

### AlarmEvent 필드 변경 요약

> **정정 (2026-06-11, D-035)**: 아래 표의 `alarmStatus` '발생'/'해소' 기술은 실측과 다르다.
> `${alarmStatus}`는 발생/해소 구분이 아니라 **폴스타 UI의 인지(ACK) 버튼 클릭 여부**(`NOT_ACK` 등)이며
> 해소 여부와 무관하다. 해소 판정(is_clear)은 `severity == 0` 단독 기준이 옳다 — D-035 참조.

| 구 필드 | 신 필드 | 변경 이유 |
|--------|--------|----------|
| `source_db_id` | `db_id` | 폴스타 JSON 키 `dbId`와 직접 대응 |
| (없음) | `server_name` | `${platformName}` — DB `server_name` 매핑 |
| (없음) | `ip_address` | `${ipAddress}` 추가 |
| (없음) | `resource_ancestry` | `${resourceAncestry}` 추가 |
| `alarm_state` | `alarm_status` | `${alarmStatus}` '발생'/'해소' (→ 정정: ACK 상태값, 위 정정 주석 참조) |
| `alarm_conditions` | `conditions` | `${conditions}` 임계 조건 정의 |
| `alarm_description` | (제거) | 폴스타 템플릿에 없는 필드 |
| `alarm_definition` | (제거) | 폴스타 템플릿에 없는 필드 |
| `resource_name` | (제거) | 폴스타 템플릿에 없는 필드 |
| `resource_description` | (제거) | 폴스타 템플릿에 없는 필드 |
| `raw_text` | `raw_payload` | 원본 JSON dict으로 타입 변경 |
| (없음) | `alarm_time` | `${formatAlarmDate('yyyyMMddHHmmss')}` 파싱 → datetime |

### 변경된 파일

| 파일 | 변경 내용 |
|------|----------|
| `src/alarm/domain/alarm.py` | AlarmEvent 필드 전면 재설계 |
| `src/alarm/application/alarm_worker.py` | `_process()` 필드 매핑 + datetime 파싱 + `is_clear` 파생 |
| `src/alarm/prompts/alarm_analyzer.py` | 시스템/유저 프롬프트 새 필드 기반으로 교체 |
| `src/alarm/application/nodes/alarm_analyzer.py` | 템플릿 변수 호출 수정, `_SEVERITY_LABELS` 수정 |
| `src/alarm/application/nodes/alarm_notifier.py` | `build_workb_body` + `_send_webhook` 새 필드 반영 |
| `src/api/routes/alarm.py` | `AlarmTestRequest` 모델 + AlarmEvent 생성 코드 새 필드 반영 |
| `alarm_server/base_receiver.py` | 로그 필드명 `alarmState` → `alarmStatus` |

### 근거

- 실제 폴스타 관리자가 등록한 템플릿을 직접 확인한 결과, 기존 `AlarmEvent`가 실제 전송 필드와 불일치
- `${platformName}`(→`serverName`)은 DB의 `server_name`과 직접 매핑되어 알람 발신 서버 식별에 핵심
- `${conditions}`는 임계 조건 정의, `${conditionLog}`는 실제 측정값으로 LLM 원인 분석의 핵심 근거
- `_parse()`는 단일행 JSON이므로 `json.loads()` 4줄로 완전히 처리됨 (기존 복잡한 텍스트 파싱 불필요)

---

## D-033. 처리 현황에 유사어 매핑 표시 — 생성된 SQL 기반 역조회

| 항목 | 내용 |
|------|------|
| **결정일** | 2026-06-11 |
| **상태** | 확정 |
| **관련 결정** | D-009 (SSE 스트리밍 UI), D-011/D-024 (유사어 사전) |

### 결정

처리 현황 UI의 "SQL 생성" 단계에 **사용자 용어 → 유사어 → 선택된 컬럼/속성** 매핑 과정을 표시한다. 매핑 정보는 LLM에게 자기 보고시키지 않고, **생성된 SQL에 등장한 리터럴/컬럼을 유사어 사전 key와 대조하는 결정적(deterministic) 역조회**로 추출한다.

### 핵심 설계

- `src/utils/synonym_usage.py` 신규 — `extract_synonym_usage(sql, ...)`:
  - EAV 속성명/RESOURCE_TYPE: 따옴표로 감싼 리터럴(`'TotalSize'`, `'server.Memory'`)을 사전 key와 정확 일치 검색 (따옴표 경계 덕분에 `server.Memory` vs `server.VirtualMemory` 오인 없음)
  - 일반 컬럼: 리터럴 제거 후 컬럼명 단어 경계 검색. **단, column_synonyms가 DB 전체 테이블×컬럼 규모(수백 키)이고 `name`/`id` 등 공통 컬럼명이 테이블마다 중복되므로, bare 컬럼명 기준으로 그룹화·중복 제거하고 `matched_user_terms`가 있는 항목만 포함** (2026-06-11 보강 — 대량 출력 방지). 전체 매핑은 최대 15건으로 제한
  - `matched_user_terms`: 사전 유사어와 `query_targets` 표현을 정규화(공백 제거·소문자) 후 포함 관계로 대조하여 어떤 사용자 용어가 매핑을 유발했는지 표시
  - **사전 미등록 감지**: EAV 속성 컬럼(`_structure_meta`의 `attribute_column`, 기본 `NAME`)과 `RESOURCE_TYPE`의 비교 리터럴 중 사전에 없는 값을 `unregistered`로 보고 → UI에 "사전 미등록 (LLM 직접 추론)" 경고 배지 표시, 유사어 등록 후보 안내 용도
- `query_generator` 노드가 SQL 생성 직후 역조회를 수행해 `synonym_usage` State 필드로 반환 (실패해도 SQL 생성에 영향 없도록 try/except)
- `_extract_node_progress`(query.py) → SSE `node_complete` → `renderNodeData`(app.js) "유사어 매핑 (생성된 SQL 기준)" / "사전 미등록 항목" 섹션 렌더링

### 근거

- 실제 유사어→컬럼 매핑은 query_generator LLM 내부에서 일어나 직접 관찰 불가. SQL은 LLM 결정의 산출물이므로 SQL 기반 역조회가 "LLM이 실제 결정한 매핑"을 가장 정직하게 반영
- LLM 자기 보고 방식(프롬프트에 매핑 JSON 출력 요구)은 환각 위험과 프롬프트 변경 부담이 있어 배제
- 재시도 루프 시 매 `node_complete`마다 갱신되므로 최종 실행 SQL 기준 매핑이 자연히 표시됨

### 변경된 파일

| 파일 | 변경 내용 |
|------|----------|
| `src/utils/synonym_usage.py` | 신규 — SQL 기반 유사어 역조회 + 사전 미등록 리터럴 감지 |
| `src/nodes/query_generator.py` | SQL 생성 후 `extract_synonym_usage` 호출, `synonym_usage` 반환 |
| `src/state.py` | `AgentState.synonym_usage` 필드 추가 |
| `src/api/routes/query.py` | `_extract_node_progress` query_generator 분기에 `synonym_usage` 전달 |
| `src/static/js/app.js` | query_generator 렌더링에 유사어 매핑/미등록 섹션 추가 |
| `tests/test_synonym_usage.py` | 신규 — 역조회 단위 테스트 11건 |

---

## D-034. 주기적 헬스체크 로그 노이즈 감소 — 성공 경로 로그 전역 강등

| 항목 | 내용 |
|------|------|
| **결정일** | 2026-06-11 |
| **상태** | 확정 |
| **관련 결정** | D-027 (감사 로깅 — 영향 없음) |

### 결정

`/health` API 1회 호출 시 약 19줄(활성 DB 3개 기준: DB당 httpx 4줄 + 연결 성공/종료 2줄, + uvicorn 액세스 로그 1줄)의 INFO 로그가 발생하고 프런트엔드가 30초마다 폴링하여 로그 노이즈가 컸다. 이를 해결하기 위해 **성공 경로 로그를 전역으로 강등**한다:

1. `setup_logging()`에서 `httpx` 로거를 WARNING으로 상향 — 성공한 모든 HTTP 요청 INFO 로그 억제 (MCP, LLM API 호출 포함)
2. DB 클라이언트(`DBHubClient`, `PostgresClient`)의 연결 성공/종료 로그를 INFO → DEBUG로 강등

### 근거

- 강등 대상은 **성공 경로 로그뿐**. 연결 실패는 `DBConnectionError` 예외와 호출부 WARNING 로그(`헬스체크 실패 (source=...)` 등)로 여전히 드러나므로 연결성 이슈 진단 능력은 유지됨
- 질의 실행 이력은 `sql_file_logger`와 감사 로깅(D-027)이 별도 기록하므로 추적성 손실 없음
- /health 한정 필터 방식(contextvar 기반)도 검토했으나, 평소 연결 수명 로그를 모니터링하지 않으므로 전역 강등의 단순함을 선택 (사용자 확인 완료)

### 변경된 파일

| 파일 | 변경 내용 |
|------|----------|
| `src/security/audit_logger.py` | `setup_logging()`에 httpx 로거 WARNING 설정 추가 |
| `src/dbhub/client.py` | connect/disconnect 성공 로그 INFO → DEBUG |
| `src/db/client.py` | connect/disconnect 성공 로그 INFO → DEBUG |

---

## D-035. 알람 이력 기반 패턴 분석 — 폴스타 DB 직접 조회 (Plan 47)

| 항목 | 내용 |
|------|------|
| **결정일** | 2026-06-11 |
| **상태** | 구현 완료 |
| **관련 결정** | D-022 (조인 규칙 준수), D-030 (해소 이력 포함), D-031/D-032 (알람 파이프라인) — **D-032 alarmStatus 기술 정정 포함** |

### 결정

알람 패턴 분석의 이력 소스를 **폴스타 DB 직접 조회**(고정 SQL, DBHub 경유, **기본 lookback 90일**)로 구현. 통계 계산·1차 분류는 Python 결정적 수행, LLM은 해석만 담당. 그래프를 3-노드(`alarm_context_enricher` 추가)로 확장. 알람 폭주 대비 조회 결과 단기 Redis 캐시(TTL 5분) 적용.

### 근거

폴스타 DB에 전체 알람 이력이 이미 존재(단일 진실 원천) — 별도 저장소 신설은 중복 저장·도입 초기 이력 공백·정합성 관리 부담만 추가. DB 의존 리스크는 타임아웃 + graceful degradation + 단기 캐시로 완화. lookback 90일은 일·주·월 주기를 각 3회 이상 관측할 수 있는 최소 기간(월 주기 3회 = 약 3개월), 180일은 과거 패턴 희석·조회량 2배로 기본값에서 제외하고 설정 확장으로 제공.

### 대안 (미채택)

| 대안 | 미채택 이유 |
|------|-----------|
| ① 자체 Redis 이력 적재 (Plan 47 초안) | 배포 시점 이후 데이터만 보유, 중복 저장, 사용자 결정으로 기각 |
| ② LLM에 원시 이력 직접 주입 | 토큰 비용·계산 환각으로 기각 |

### 핵심 설계 결정

| 항목 | 결정 |
|------|------|
| 시간 윈도우 기준 | 모든 통계 윈도우(24h/7일/30일/90일)와 SQL lookback_start는 `event.alarm_time` 기준 (처리 시점 now 기준 아님) |
| 서버 매칭 | Template C-6 패턴 (`SVR.ID = COALESCE(CR.PLATFORM_RESOURCE_ID, CR.ID)`) + `SVR.NAME = server_name(=${platformName})`. 공동존(gp/yd)은 r.name 매칭 — hostname 금지 (Known Mistakes 2026-06-10). db_id별 매칭 식 분기 가능 (`_SERVER_MATCH_BY_DB_ID`) |
| 1차 분류 | 첫 발생(이력 0건) → 급증(count_24h ≥ 5 이고 30일 일평균 3배 이상) → 주기적(≥3건 + 간격 CV<0.5, 일/주/월 라벨) → 산발적 |
| graceful degradation | 이력 조회 실패/타임아웃(5초)/db_id 미등록/Redis 장애 시 history_stats=None — 알람 분석·발송 절대 차단 금지. LLM 패턴 필드 누락도 `parsed.get()` 기본값 처리 |
| 패턴 = 부가 정보 | 알림 발송 억제에 사용하지 않음. 심각도 3은 is_routine과 무관하게 권고 조치 유지 (프롬프트 규칙) |
| 캐시 | `alarm:histcache:{db_id}:{server_name}:{alarm_name}` 키에 조회 행 원본 SETEX (TTL 300초, 0이면 비활성). 캐시 실패 무시 |
| 상한 | history_max_rows=2,000 (truncated 플래그 연동 — 프롬프트에 "이력 일부만 반영" 명시) |
| **is_clear 정정** | `severity == 0` 단독 기준으로 통일. `alarmStatus`는 폴스타 UI 인지(ACK) 상태(`NOT_ACK` 등)로 해소 여부와 무관 — **D-032에 기술된 alarmStatus='발생'/'해소'는 실측과 다르므로 정정** (worker·API의 `alarm_status == "해소"` 조건 제거, 프롬프트 "alarmStatus=해소" 규칙을 severity=0 기준으로 수정, AlarmEvent 주석 갱신) |
| 미등록 db_id | enricher가 조회 전 차단하여 history_stats=None 유지 — 빈 이력으로 통계 계산 시 "첫 발생" 오판 방지 |
| ALARM_HISTORY_ENABLED=false | enricher 노드 자체를 그래프에서 제외 — 기존 2-노드 동작과 완전 동일 |

### 변경된 파일

| 파일 | 변경 내용 | 계층 |
|------|----------|------|
| `src/alarm/domain/alarm.py` | `AlarmHistoryEntry`/`AlarmHistoryStats` 신규, `AlarmAnalysisResult` 패턴 필드 3개(pattern_type/is_routine/pattern_analysis), AlarmEvent alarm_status·is_clear 주석 정정 | domain |
| `src/alarm/domain/alarm_pattern.py` | 신규 — `compute_history_stats()` 순수 함수 (1차 분류 + 주기 라벨), 캐시 직렬화 헬퍼 | domain |
| `src/alarm/infrastructure/polestar_history.py` | 신규 — `PolestarAlarmHistoryRepository` (고정 SQL, 리터럴 이스케이프, LIMIT 상한, 미등록 db_id 처리) | infrastructure |
| `src/alarm/application/nodes/alarm_context_enricher.py` | 신규 — enricher 노드 + `enrich_history()` (캐시→DB→통계, 타임아웃, graceful degradation, 해소 알람 스킵) | application |
| `src/alarm/orchestration/alarm_graph.py` | `AlarmState.history_stats` 추가, history_enabled에 따른 3-노드/2-노드 분기 | orchestration |
| `src/alarm/application/alarm_worker.py` | is_clear severity=0 단독 기준, DBRegistry 기반 리포지토리 생성·graph config 주입(history_repo/history_redis) | orchestration |
| `src/alarm/prompts/alarm_analyzer.py` | 응답 스키마 패턴 필드 3개, 패턴 판단 규칙 7개 추가, "alarmStatus=해소"→"severity=0" 정정, `{history_section}` 추가 | prompts |
| `src/alarm/application/nodes/alarm_analyzer.py` | `_render_history_section()` 신규, 패턴 필드 `parsed.get()` 기본값 파싱 | application |
| `src/alarm/application/nodes/alarm_notifier.py` | workb 본문 "패턴 분석" 섹션(배지+해석), webhook payload 패턴 필드 3개 | application |
| `src/api/routes/alarm.py` | is_clear 정정 2곳, UI push 2곳 패턴 필드, `AlarmAnalysisOutput`/`AlarmTestResponse` 확장, `query_history`/`simulated_history` 파라미터, `_resolve_history_stats()`/`_stats_to_dict()`/`_simulated_entries()` 헬퍼 | interface |
| `src/static/js/app.js` | 알람 말풍선 패턴 배지 (is_routine=true 회색 "일상 알람", false 강조색 "확인 필요") | static |
| `src/config.py` | `AlarmConfig`에 Plan 47 필드 6개 (history_enabled/lookback_days/max_rows/cache_ttl/enrich_timeout/burst_threshold) | config |
| `.env.example` | `ALARM_HISTORY_*` 등 6개 항목 추가 (모두 스칼라 — list 필드 아님) | 설정 |
| `tests/test_alarm_pattern.py` | 신규 — 분류 4종·주기 라벨 3종·윈도우 기준·truncated·현재 이벤트 제외·직렬화 (21건) | 테스트 |
| `tests/test_alarm_history_repo.py` | 신규 — SQL 조립(D-022/D-030 준수, 이스케이프, gp 서버 매칭)·행 변환·미등록 db_id (8건) | 테스트 |
| `tests/test_alarm_enricher.py` | 신규 — graceful degradation 7종·캐시·그래프 분기·is_clear 판정·notifier/webhook 패턴 출력·analyzer 이력 주입 (23건) | 테스트 |

### 향후 수정 시 고려사항

- `CMM_ALARM.CTIME` 인덱스 부재로 90일 범위 조회가 느리면 `ALARM_HISTORY_LOOKBACK_DAYS` 축소(30) 또는 인덱스 협의 후 확대 — enricher 타임아웃(5초)이 1차 방어선
- 월 주기 작업이 많은 환경은 `ALARM_HISTORY_LOOKBACK_DAYS=180` 확장 (분기 주기는 판정 범위 외 — 프롬프트에 전제 명시됨)
- 새 폴스타 인스턴스의 서버 매칭 컬럼이 다르면 `polestar_history.py`의 `_SERVER_MATCH_BY_DB_ID`에 db_id별 식 추가
- 패턴 기반 알림 발송 억제는 별도 계획으로 분리 (현재 패턴은 부가 정보로만 사용)

---

## D-036. 알람 영향 프로세스 보강 — 폴스타 실시간 프로세스 API (Plan 47-1)

| 항목 | 내용 |
|------|------|
| **결정일** | 2026-06-16 |
| **상태** | 구현 완료 |
| **관련 결정** | D-035(Plan 47 enricher 확장), D-032(AlarmEvent 필드), D-022(조인 규칙 — 본 계획은 DB 미사용) |

### 결정

CPU/메모리 **발생** 알람에 한해 폴스타 실시간 프로세스 API(`GET {base_url}/rest/server/process/listByhostname?hostname=`)를 **hostname으로 조회**하여, 그 시점 자원을 점유 중인 상위 프로세스를 결정적으로 선별·마스킹해 패턴 근거에 더해 제공한다. 별도 노드를 만들지 않고 `alarm_context_enricher`에 프로세스 조회 단계를 추가(그래프 노드 수 3개 불변)하고, 이력 조회(폴스타 DB)와 `asyncio.gather`로 **동시 실행**하며 각자 독립 graceful degradation한다.

### 근거

"왜 자원이 높은가"의 직접 근거가 프로세스 점유율 — DB 이력(패턴)과 보완 관계. 별도 노드 없이 enricher 확장으로 응집. 외부 HTTP 의존은 짧은 타임아웃(기본 3초)+graceful degradation으로 격리. 정렬·선별·마스킹은 Python 순수 함수로 결정적 처리하고 LLM은 상위 프로세스(이름·pid) 인용만 — 토큰·환각·민감정보 노출 회피.

### 대안 (미채택)

| 대안 | 미채택 이유 |
|------|-----------|
| ① 별도 노드 분리 | 그래프 복잡도만 증가 — enricher 확장으로 응집 |
| ② 모든 알람에 프로세스 조회 | 디스크/네트워크엔 무의미, 외부 호출 낭비 |
| ③ 프로세스 원시 데이터를 LLM에 그대로 주입 | 토큰·환각·민감정보 노출 — 결정적 선별+마스킹 채택 |

### 핵심 설계 결정

| 항목 | 결정 |
|------|------|
| **조회 키 (키 차이 주의)** | 프로세스 API는 **`event.hostname`** 사용. Plan 47 DB 이력 조회는 `r.name`(=serverName)을 쓰는 것과 **정반대 키** — 동일 알람에서 서로 다른 식별자(실측: serverName="cop0-aisapd02" vs hostname="saisvd01") |
| 인증·scheme | 인증 불필요(내부 시스템, 비로그인 조회 확인) · `http://`만 사용(TLS 없음). 인증 헤더/토큰/verify_ssl 설정 두지 않음 |
| 게이팅 | CPU/메모리(`classify_alarm_kind`) + 발생 알람(is_clear=False) + base_url 매핑 존재 + process_enrich_enabled + client 주입 시에만 조회. 디스크/네트워크/해소/미매핑 db_id 스킵 |
| graceful degradation | API 타임아웃/비200/네트워크오류/미주입 어느 경우에도 `process_snapshot=None` — 분석·발송 차단 금지. 노드는 항상 `{history_stats, process_snapshot}` 두 키 반환 |
| 동시 실행 | history와 process를 `asyncio.gather`로 동시 실행, 각자 try/except 독립 degradation, 노드 전체 `enrich_timeout_seconds` 상한 |
| 결정적 선별 + LLM 인용 | 정렬(CPU=p100cpu→pcpu 폴백, 메모리=pmem)·상위 N 선별·마스킹은 Python 순수함수(`process_rank.py`). LLM은 probable_cause/recommended_action에 상위 프로세스 인용만, 미조회 시 추측 금지 |
| **민감정보 마스킹 (필수)** | args의 password/passwd/pwd/secret/token/api_key/access_key/credential 값과 접속문자열(scheme://user:pass@host) 비밀번호를 `mask_args()`로 마스킹한 값만 LLM·UI·workb·webhook에 노출. 마스킹 회귀를 단위 테스트로 고정 |
| SSRF/인젝션 방지 | base_url은 설정 고정값(사용자 입력 아님), hostname만 `urllib.parse.quote(safe='')`로 인코딩하여 쿼리에 부착 — 경로/호스트 조작 불가 |
| `.env` 신규 필드 | 스칼라 + `=`구분 CSV (`ALARM_PROCESS_API_BASE_URLS_CSV`) — JSON dict 회피 (Known Mistakes 2026-03-23 비해당) |
| API 응답 시각 | 응답 최상위 `date`를 `ProcessApiResult.captured_at`으로 파싱(표시용). `list_by_hostname` 반환을 (captured_at, processes) 튜플로 확장 (Plan 47-1 §5.3의 list 반환을 시각 포함으로 보강) |
| ALARM_PROCESS_ENRICH_ENABLED=false | process_section 미주입 — 기존 Plan 47 동작과 완전 동일 |

### 변경된 파일

| 파일 | 변경 내용 | 계층 |
|------|----------|------|
| `src/alarm/domain/alarm.py` | `ProcessInfo`/`ProcessSnapshot` 신규 | domain |
| `src/alarm/domain/process_rank.py` | 신규 — `classify_alarm_kind`/`select_top_processes`/`mask_args` 순수 함수 | domain |
| `src/alarm/infrastructure/polestar_process_api.py` | 신규 — `PolestarProcessApiClient`(httpx GET, hostname URL 인코딩, 타임아웃, None degradation), `ProcessApiResult`(captured_at+processes) | infrastructure |
| `src/alarm/application/nodes/alarm_context_enricher.py` | `enrich_processes()` 신규, 노드를 history∥process `asyncio.gather` 동시 실행+독립 degradation으로 재구성 (항상 두 키 반환) | application |
| `src/alarm/application/nodes/alarm_analyzer.py` | `_render_process_section()` 신규, `{process_section}` 주입 | application |
| `src/alarm/application/nodes/alarm_notifier.py` | workb `영향 프로세스` 텍스트 표(`_process_table_html`), webhook `process_snapshot` 필드(`_process_payload`), `_send_workb`/`_send_webhook` 시그니처 확장 | application |
| `src/alarm/application/alarm_worker.py` | `_build_process_client()` 신규, graph config에 `process_client` 주입, 초기 state에 `process_snapshot` | application |
| `src/alarm/orchestration/alarm_graph.py` | `AlarmState.process_snapshot` 추가 | orchestration |
| `src/alarm/prompts/alarm_analyzer.py` | `{process_section}` + 프로세스 인용 규칙 4개(인용/수치 비계산/미조회 추측 금지/마스킹 복원 금지) | prompts |
| `src/api/routes/alarm.py` | `query_process`/`simulated_processes` 파라미터(2 요청), `process_snapshot` 응답 필드, `_process_to_dict`/`_resolve_process_snapshot` 헬퍼, UI push 2곳·미리보기·실발송에 스냅샷 연동 | interface |
| `src/static/js/app.js` | `renderProcessEvidence()` 영향 프로세스 표(CPU/MEM 컬럼, 알람 지표 강조 정렬), 알람 말풍선 주입 | static |
| `src/static/css/style.css` | `.alarm-proc-table`/`.alarm-proc-num`/`.is-primary` (`.alarm-evidence` 재사용+컬럼 헤더 조정) | static |
| `src/config.py` | `AlarmConfig`에 `process_enrich_enabled`/`process_api_base_urls_csv`/`process_api_timeout_seconds`/`process_top_n` + `get_process_api_base_url()` | config |
| `.env.example` | `ALARM_PROCESS_*` 4개 항목 추가 (스칼라+CSV) | 설정 |
| `tests/test_alarm_process_rank.py` | 신규 — 알람 종류 판정·CPU/메모리 정렬·마스킹 회귀·누락 필드·base_url 매핑 (31건) | 테스트 |
| `tests/test_alarm_process_enrich.py` | 신규 — 게이팅·hostname 사용·동시 실행·독립 degradation·analyzer 주입·API 클라이언트·notifier/webhook·API 헬퍼 (33건) | 테스트 |
| `tests/test_alarm_enricher.py` | 노드 반환 계약 변경(항상 `process_snapshot` 키 포함) 반영 6건 | 테스트 |

### 향후 수정 시 고려사항

- 폴스타 프로세스 API에 향후 인증이 도입되면 그때 헤더/토큰 처리를 추가 (현재 비인증 확인됨)
- 새 폴스타 인스턴스 추가 시 `ALARM_PROCESS_API_BASE_URLS_CSV`에 `db_id=http://host` 항목 추가
- `mask_args()` 마스킹 패턴은 신규 민감 키워드(예: bearer, jwt) 발견 시 `_SENSITIVE_KEY`에 보강 — 마스킹 회귀 테스트로 고정

---

## D-037. deepagents 기반 의도 분해 오케스트레이션 (Plan 48)

| 항목 | 내용 |
|------|------|
| **결정일** | 2026-06-16 |
| **상태** | **Phase 1·2 구현 완료 (2026-06-16)** / **실제 deepagents 패키지 도입 결정 (2026-06-17, Plan 49 — vLLM 오케스트레이터 + FabriX 워커, 트랙 B 재진입)** / **트랙 B 런타임 그래프 배선 완료 (2026-06-17, Plan 49 §7 step 7 — deepagents 미반입 시 안전 폴백)** / **deepagents 0.6.10 실제 설치 + step6(도구 결과→FabriX 재정리) 실측 구현 완료 (2026-06-17, Plan 49 §4.3 step6/§7 step 6)** / Phase 3~6 예정 |
| **관련 결정** | D-004 확장, D-005 일반화 |

### 결정

`semantic_router`의 **단일 의도 라우팅**을 LangChain **deepagents 패턴**(planner + subagent 위임)으로 확장하여,
사용자 질의를 **여러 sub-task로 분해**하고 **순차/병렬 실행** 후 결과를 통합 응답한다.

**도입 방식은 단계적 하이브리드로 확정**한다.
- **1단계 (자체 구현)**: 현재 스택(langchain-core 0.3 / langgraph 0.2)을 유지한 채 deepagents 패턴을 자체 LangGraph 노드(`intent_planner` → `agent_orchestrator` → `result_aggregator`)로 구현한다.
- **2단계 (격리 PoC)**: 별도 모듈(`experiments/`)에서 deepagents 실제 패키지를 검증한 뒤 점진 전환 여부를 결정한다.

### 근거

- **복합 의도 미지원 한계**: 현 `semantic_router`는 한 질의=한 작업으로만 분기하여 "A 하고 B 조회" 같은 복합 질의를 처리 못 함.
- **검증된 패턴 차용**: deepagents의 planning(`write_todos`) + task 위임은 작업 분해·격리 실행의 표준 패턴.
- **메이저 버전 리스크 회피**: deepagents 0.6.10은 `langchain-core>=1.4.7` / `langchain>=1.3.9`(1.x)를 요구. 현 프로젝트는 0.x이며 사내 커스텀 LLM(`KBGenAIChat`/`FabriXAPIClient`/`LLMAPIClient`)을 `BaseChatModel`로 직접 구현. 직접 도입 시 전 스택 breaking change → 1단계는 패턴만 자체 구현하여 리스크 격리.

### 세부 설계

- **TaskSpec**: `{task_id, agent, sub_query, depends_on, order}`. `agent` ∈ {data_query, cache_management, synonym_registration, general_inference, alarm_query}.
- **intent_planner**: 질의 → task 목록 분해 + agent 분류. DB 선택은 `data_query` subagent로 위임(관심사 분리). 실패 시 단일 data_query task 폴백.
- **agent_orchestrator**: `depends_on` 위상정렬 → 같은 레벨 병렬(`asyncio.gather`), 레벨 간 순차. 부분 실패 허용(D-005 계승). 기존 노드/`multi_db_executor`를 subagent 헬퍼로 재사용.
- **result_aggregator**: task 결과 통합 응답(단일 task는 통과). `output_generator` 재사용.
- **하위 호환 / 기본값 전환(2026-06-16)**: `ENABLE_DEEPAGENT_ORCHESTRATION` 미입력 시 **멀티 DB 환경이면 신규 경로가 기본 활성**(`bool|None` tri-state, `model_post_init`이 `multi_db.get_active_db_ids()` 기준 해석). `=false` 명시 시 기존 `semantic_router` 경로로 회귀(opt-out 보존 — 성공기준 5). `semantic_router` 로직은 **삭제하지 않고 재사용**. 명시값은 pydantic-settings가 `.env`·OS env에서 필드로 직접 읽어 `os.getenv` 미사용(Known Mistakes 2026-06-10 준수).
- **계층**: 신규 `src/orchestration/` 패키지(application 노드 조합). `arch_check.py` 위반 검사 필수.
- **단계적 로드맵(Plan 48 §5)**: deepagents 11개 미들웨어 대비 1단계는 Planning(정적)·SubAgent 2개만 부분 차용(≈18%). 누락 기능을 Phase 2(동적 재계획)→3(state offloading, D-013 이행)→4(HITL 세분화)→5(컨텍스트 압축)→6(subagent 격리)→7(tool calling/구조화 출력)→8(실제 패키지 PoC)→9(운영 전환)로 단계 배치. **트랙 A(자체구현, Phase 1~7) / 트랙 B(실제 패키지·langchain 1.x, Phase 8~9)** 분리. skills/memory/async subagent는 Phase 8 PoC에서 가치 평가 후 결정.
- **tool-calling 전제(Plan 48 §5.1, R-08)**: FabriX(KBGenAIChat)는 네이티브 tool-calling이 불안정. **트랙 A(Phase 1~6)는 프롬프트+JSON 방식으로 tool-calling 미사용 → 제약 무관**. Phase 7 구조화 출력은 json_mode/프롬프트로 한정(tool-calling 강제 안 함). **트랙 B(deepagents 실제 패키지)는 tool-calling 필수** → FabriX 개선 / `LLMToolEmulator` / 오케스트레이터 LLM 분리 중 1을 Phase 8 PoC에서 검증, 모두 불가 시 트랙 A 영구 운영도 유효.
- **버전 정정 + 트랙 A 단일 확정(2026-06-16)**: 배포 wheel(`wheels/{os}/`) 확인 결과 운영 스택은 **이미 1.x**(langchain-core 1.2.30 / langgraph 1.1.6) — `requirements.txt` 하한(`>=0.2.0`)만 보고 0.x로 오판했던 것을 정정(R-01 **High→Low** 하향). **단, FabriX는 tool 호출이 불가한 것으로 확정** → deepagents 실제 패키지(전 기능 tool-calling 기반)는 작동 불가하므로 **트랙 B(실제 패키지)·검증 PoC(Phase 8) 제거**. 본 계획은 **트랙 A — deepagents 패턴을 tool-calling 없이(프롬프트+JSON) 자체 구현 — 으로 단일 확정**(Phase 1~6, 착수 순서 1→(2∥3)→4→5→6). Phase 7(구조화 출력)·8·9는 보류/제거, **tool-calling 지원 LLM 교체 시에만 재고**. 사용자 요구(복합 의도·결과 기반 후속)는 트랙 A로 전부 충족.
- **Skills 검토(Plan 48 §5.2)**: deepagents skills는 혼합형 — L1 메타 노출(프롬프트 주입, **tool-calling 무관**) + L2 온디맨드 로드(`read_file`, tool-calling 의존). 실제 미들웨어는 FabriX 불가하나 **패턴은 트랙 A 자체 구현 가능**(트랙 B 전제 아님). 단 기존 `config/db_profiles/`(query_guide·known_attributes)가 동일 가치(작업별 지식 모듈화+온디맨드 주입)를 이미 제공 → **신규 skills 시스템 미신설**, 필요 시 SUBAGENT_REGISTRY per-agent `prompt` 슬롯으로 흡수(db_profiles 중복 정리 후, 선택적·우선순위 낮음).
- **현재 코드 치환 매핑(Plan 48 §4.9)**: `semantic_router` 의도분석은 2계층 — (A) deterministic pre-route(`pending_synonym_reuse`①/`synonym_registration`②/`mapped_db_ids`③), (B) `_llm_classify` 의도 분류(④⑤). 계층 A는 `intent_planner` pre-check로 **그대로 이식**(멀티턴 pending 결합 보존), 계층 B만 복합 task 분해로 대체. 의도별 노드(cache_management/synonym_registrar/general_inference/multi_db_executor/단일 DB 파이프라인)는 그래프에 등록하지 않고 `SUBAGENT_REGISTRY` handler가 **함수로 호출**. `_llm_classify`의 DB 분류부는 `classify_dbs`로 재사용, 단일 DB 풀 재시도는 `_run_single_db_pipeline`로 보존(R-09). `route_after_semantic_router`/`_INTENT_ROUTE_MAP`은 deepagent 모드 미사용(하위 호환 위해 삭제 금지).
- **결과 기반 후속 처리(Plan 48 §4.10)**: 단일 의도만 처리하던 한계를 task 관계 **3패턴**으로 확장 — ① 독립 병렬(Phase 1) ② **데이터 의존 순차**(Phase 1; `input_from`으로 선행 task 결과 행을 후속 task SQL 생성 컨텍스트에 주입, 키 컬럼·행수 상한 R-12) ③ **결과 조건부 동적 재계획**(Phase 2; `replanner` 노드 + `agent_orchestrator` 조건부 루프, `MAX_REPLAN` 상한 R-11, tool-calling 불필요). 사용자가 복합 의도 + 결과 기반 후속을 한 프롬프트에 담는 경우를 지원. Phase 1=계획 사전 확정(①②, 선형 그래프), Phase 2=계획이 실행 중 변함(③, 루프). **후속 처리 주체는 '에이전트 자동'으로 확정(2026-06-16)** — 단일 프롬프트 내 시스템 자동 후속(②③)이 핵심이며, 사용자 직접 멀티턴 후속은 D-013으로 처리(본 결정 범위 밖).
- **모호성 명료화 인터럽트(Plan 48 §4.11, 2026-06-16 확정)**: 처리 **방법이 모호**할 때(의도/처리방법/대상 DB 불확실, 복수 유효 해석) 임의 진행하지 않고 **사용자에게 선택지를 되묻는** 멀티턴 인터럽트를 도입. deepagents **HumanInTheLoopMiddleware `respond`(질의-응답형)** 대응, 기존 `approval_gate`(노드 `interrupt_before`, **tool-calling 불필요**)와 동형 → 트랙 A. 세 요소 융합: 모호성 판단(intent_planner)+되묻기 인터럽트(interrupt_before)+멀티턴 재개(체크포인터/D-013). **감지 범위는 계획 단계 한정**(subagent 실행 중 되묻기는 복합 task 다중 인터럽트 복잡성 R-03으로 범위 밖). **배치: Phase 1은 `intent_planner` 출력에 `clarification_needed` 슬롯 예약(방출만, 인터럽트 미발생) / Phase 4에서 `clarification_gate` 노드 + 인터럽트·재진입 구현**. 상태는 기존 HITL 필드(`approval_context.type="clarification"`) 재사용, `MAX_CLARIFY`(예: 2)로 무한 되묻기 방지(R-13).
- **실제 deepagents 패키지 도입 확정(2026-06-17, Plan 49 개정 — 트랙 B 재진입)**: 위 "트랙 B(실제 패키지) 제거"(2026-06-16) 입장을 **번복**한다. tool-calling 블로커(R-08)를 **폐쇄망 내부 vLLM 오케스트레이터**로 해소 — vLLM(OpenAI 호환 `/v1`)에 tool-calling 지원 오픈모델(**Qwen3.5-9B**)을 서빙하고, **이미 보유한 `langchain-openai`의 `ChatOpenAI`(base_url=vLLM)** 로 네이티브 `bind_tools`를 구동한다(커스텀 클라이언트 불필요, Gemini egress 회피). **역할 분리**: vLLM=제어 평면(`write_todos` 동적 재계획·`task` 위임·tool_calls), **FabriX(KBGenAIChat)=데이터 평면(자연어→SQL→DB 조회→결과 정리 및 최종 자연어 응답 = 실질 응답처리)**. 기존 `SUBAGENT_REGISTRY` 5개 작업은 `@tool`로 노출하되 **FabriX는 도구 구현 내부에서만 호출**(deepagents SubAgent 모델로 직결 금지 — tool-calling 강요 방지, R-B4). **백엔드 선택은 vLLM 가용성 옵션**: `enable_deepagents_package=on` + vLLM health check 통과 시 트랙 B, **vLLM 미서빙/off 시 기존 `semantic_router` 사용**(Track-A `replanner`는 자동 폴백 아님 — 명시 선택 시에만 보존). **폐쇄망 요건**: `langchain-core` 1.2.30→`>=1.4.7` 업글 + `langchain`·`deepagents` wheel 반입 + vLLM 인프라(`--enable-auto-tool-choice`, 세대별 tool-call·reasoning 파서 PoC 확정). Plan 48 §5 **Phase 8("제거")→부활**, Phase 9(운영 전환) 재개. PoC 게이트(R-B2: 오픈모델 tool-calling 신뢰도) 후 결과를 `docs/deepagents_poc_report.md`에 기록. **근거**: 트랙 B 재진입 조건("tool-calling 지원 LLM") 충족. **대안 기각**: Gemini(egress 불가)·KBGenAIChat 프롬프트 에뮬레이션(다중 tool call·긴 ReAct 루프에서 불안정).

### 고려한 대안

| 대안 | 제외 이유 |
|------|----------|
| deepagents 패키지 즉시 도입 | langchain 1.x 메이저 마이그레이션 강제, 커스텀 LLM 재작성 리스크 → 2단계 PoC로 연기 |
| 단일 의도 라우팅 고도화만 | 복합 의도 분해(사용자 요구) 미충족 |
| LangGraph Send fan-out | 0.2 reducer 충돌 관리 복잡 → supervisor 노드 방식(A안) 채택 |

### 향후 수정 시 고려사항

- 새 agent 추가 시: planner 프롬프트 분류 규칙 + `agent_orchestrator._run_agent` 분기 + subagent 헬퍼 추가.
- 복합 task 중 HITL(SQL/구조 승인) 인터럽트 재개 처리는 1단계에서 순차 분리로 제한. 전면 지원은 후속 과제.
- 모호성 명료화 인터럽트(§4.11)는 Phase 4 구현이나, **Phase 1에서 `intent_planner` 출력에 `clarification_needed` 슬롯을 반드시 예약**할 것(미예약 시 Phase 4에서 planner 스키마·프롬프트를 재손질해야 함). 감지 확장(subagent 실행 단계 되묻기)은 R-03 정리 후 별도 과제.
- 2단계 PoC 결과는 `docs/deepagents_poc_report.md`에 기록하고, 전환 시 langchain 1.x 업그레이드를 별도 계획으로 분리.

---

## D-038. 사용법/지원 소스 안내 — general_inference 그라운딩 (도움말 디스커버리)

| 항목 | 내용 |
|------|------|
| **결정일** | 2026-06-23 |
| **상태** | **구현 완료 (2026-06-23)** |
| **관련 결정** | D-026(allowed_db_ids 접근제어), D-037(deep_agent/semantic_router 이원 백엔드) |

### 결정

"이 에이전트로 무엇을 할 수 있는가 / 현재 지원 소스는 무엇인가"를 묻는 **사용법·능력 문의**에 대해,
실제 활성·허용 소스에 근거한 안내를 채팅으로 제공한다. 안내문 **생성 위치는 `general_inference` 노드 한 곳**으로 일원화하고,
메인 화면에 **도움말 버튼**을 추가해 같은 핸들러로 질의를 주입한다.

- **백엔드 무관 단일 수렴점**: deep_agent 트랙(`general_answer` 도구)·semantic_router 트랙·intent_planner 그래프 트랙이
  모두 `src/nodes/general_inference.py` 한 함수로 수렴 → 그라운딩을 이 노드에만 주입하면 전 백엔드 자동 커버.
- **그라운딩 = 코드 조립 + LLM 문장화**: 소스 목록(사실)은 `active_db_ids ∩ allowed_db_ids` + `DB_DOMAINS`의
  display_name/description으로 노드 안에서 조립하고, 자연어 안내문은 LLM이 그 사실에 근거해 생성(소스/메트릭이
  늘어도 자동 반영). 멀티턴 마무리 문장("어떤 것을 확인해 드릴까요?")으로 끝맺도록 지시.
- **접근제어 반영**: `allowed_db_ids`(D-026)가 지정된 사용자에게는 교집합 소스만 안내(못 쓰는 소스 광고 금지).
- **도움말 버튼**: 예시 칩(쿼리 실행)과 시각적으로 구분(점선·메타 액션)하고, 클릭 즉시 실행(`data-help`).

### 근거

- **디스커버리 가치**: 신규 사용자가 지원 소스(김포/여의도/은행존 폴스타 등)·조회 가능 메트릭을 몰라 빈 질의→실패→이탈하는 흐름을 완화.
- **환각 차단이 핵심**: 사실(온라인 소스·지원 메트릭)은 시스템 상태이므로 LLM에 맡기면 꺼진 소스/없는 기능을 지어냄 → **사실은 코드가 조립, 문장만 LLM**.
- **노드 계약 유지**: `general_inference`는 "DB 미접근" 노드 → 라이브 health 체크(연결 오픈) 미수행. 활성 소스는 설정(`get_active_db_ids`), 설명은 `DB_DOMAINS`(도메인 정의)만 참조하여 계층 위반·부작용 없음.
- **분류는 기존 자원 재사용**: `semantic_router`/`intent_planner`가 이미 "뭘 할 수 있어?"류를 general_inference로 분류. deep_agent 트랙만 오케스트레이터가 직접 답해 그라운딩을 우회할 수 있어 `ORCHESTRATOR_INSTRUCTIONS`에 "사용법·능력 문의 → general_answer 위임" 한 줄 추가.

### 세부 변경

- `src/nodes/general_inference.py`: `_build_source_catalog`(active ∩ allowed + DB_DOMAINS), `_build_system_prompt`(카탈로그·지원 메트릭·멀티턴 마무리 안내 규칙 그라운딩) 추가, 시스템 프롬프트 주입.
- `src/prompts/orchestrator.py`: 사용법·능력 문의 → `general_answer` 도구 위임 규칙 추가(deep_agent 트랙 그라운딩 보장).
- `src/static/index.html`·`css/style.css`·`js/app.js`: `❓ 사용법` 버튼(점선 메타 스타일) + 클릭 즉시 실행 배선.
- **result_aggregator 무변**: deep_agent 트랙도 행 없는 텍스트 결과의 `final_response`를 verbatim 통과(실측 확인) → 통과 분기 추가 불필요.

### 고려한 대안

| 대안 | 제외 이유 |
|------|----------|
| 전용 `/api/v1/help` + 정적 템플릿 | 결정적·저비용이나 자유 타이핑("여기 뭐 돼?") 미대응, 안내 경로 이원화. 사용자 의도는 "채팅으로 안내" |
| 도움말 쿼리를 SQL 파이프라인에 태움 | 도움말은 시스템 메타데이터 — SQL 생성 시 실패/환각. 파이프라인 부적합 |
| 도움말 버튼만(타이핑 미지원) | 자유 입력 능력 문의를 놓침. general_inference 그라운딩이 양쪽을 한 번에 해결 |

### 향후 수정 시 고려사항

- 새 소스/메트릭 추가 시 안내문은 자동 반영되나, 소스 비의존 메트릭 요약(`_SUPPORTED_CAPABILITIES`)은 수기 목록이므로 신규 도메인(예: 신규 DB 유형) 추가 시 동기화 필요.
- 향후 "현재 연결(healthy) 소스만" 정밀 표기가 필요하면, general_inference 계약(DB 미접근)을 깨지 말고 프론트의 `/health` `db_status_map`(배너가 이미 사용)을 별도 표기로 결합할 것.

---

## D-041. 멀티턴 컨텍스트 전파 및 엔티티 보존 (Plan 50)

| 항목 | 내용 |
|------|------|
| **결정일** | 2026-06-25 |
| **상태** | 구현 완료 (2026-06-25) |
| **관련 결정** | D-013(멀티턴) 확장, D-037(orchestration), D-009(SSE) — 충돌 없음 |

> **번호 주의**: Plan 50 §6은 이 결정을 "D-039"로 적었으나, 변경 이력 표에서 D-039/D-040이 이미
> 다른 결정(orchestration 처리 현황 관찰성·replanner 과재계획 수정)에 선점되어 있었다. 번호 충돌을
> 피하기 위해 **다음 빈 번호 D-041**을 부여한다(team-lead 판단, 사용자 확인 필요 항목으로 보고).

### 결정

후속 턴 분해(`intent_planner`)에 **직전 턴의 해소된 DB/위치/대상 엔티티를 압축 주입**하고,
`data_query`가 **이전 턴 DB를 승계**하며, "현재/실시간 프로세스" 류는 **`process_query` 1급 의도**로
실시간 폴스타 프로세스 API에 라우팅한다. D-013(멀티턴) 확장이며 되돌리지 않는다.

### 세부 변경

- **M3 — 엔티티 보존(`context_resolver`)**: `conversation_context`에 `previous_db_ids`
  (target_databases∪active_db_id∪mapped_db_ids), `previous_entities`(filter_conditions 식별 키 +
  결과 식별 컬럼 값, **행수 상한** `_MAX_ENTITY_ROWS=20`), `previous_location`(김포/여의도/운영 등
  표면 추출) 추가. 대량 보존 금지(Known Mistakes 2026-06-11 사전 순회 상한 원칙).
- **M1 — 맥락 주입(`intent_planner`)**: `_llm_decompose(..., conversation_context=)` 추가,
  후속 턴이면 `_build_context_block`이 **압축 1블록**(원시 메시지 히스토리 금지)을 HumanMessage 앞에
  주입. `prompts/intent_planner.py`에 지시어 해소·DB 승계 규칙 + 예시 5(후속 턴) 추가.
- **M2 — DB 승계(`subagents.run_data_query_pipeline`)**: 우선순위 ① 이번 턴 명시 위치/DB >
  ② mapped_db_ids > ③ `previous_db_ids`(멀티턴) > ④ 전체 fan-out. 이번 턴에 새 위치/DB 신호가
  있으면 승계하지 않음(`_has_new_location_db_signal`). 승계 시 처리현황에 `db_succession` 노출(투명성).
- **M4 — `process_query` 신규 의도/subagent**: `src/orchestration/process_query.py` 신규.
  조회는 `alarm` 모듈의 `polestar_process_api.py`(infrastructure)·`process_rank.py`(domain)를 **재사용**
  (마스킹·상위 N 선별은 결정적 — LLM 원시 주입 금지, D-036 정합). db_id는 승계로, hostname은
  `previous_entities`로 해소. 대상 미식별/미연결 시 graceful 안내(없는 테이블 조회·`SQL0204N` 방지).
  계층: orchestration → {infrastructure, domain} 정합(arch_check 통과).

### 근거

- 검증 시나리오(턴2 "해당 서버 프로세스" → "데이터 없음")의 구조적 단절점 M1~M4를 직접 해소.
- 회귀 없음: 첫 턴/단일 의도/맥락 없음 경로는 무변경(테스트로 고정).

### 향후 수정 시 고려사항

- `process_query`는 base_url 매핑(`AlarmConfig.process_api_base_urls_csv`)이 있는 폴스타(김포/여의도)에서만
  실동작. 신규 폴스타 추가 시 매핑 추가 필요.
- 엔티티/맥락 블록은 압축 1블록·상한 유지(토큰 재증가 방지) — 확장 시 상한을 함께 검토.

---

## D-042. 제어 평면 컨텍스트 예산 · 평면 분리 강제 · Qwen no-think (Plan 50)

| 항목 | 내용 |
|------|------|
| **결정일** | 2026-06-25 |
| **상태** | 구현 완료 (2026-06-25, 클라이언트 코드) / 서버 기동 파라미터(B8)는 인프라 진행 중 |
| **관련 결정** | D-037(deepagents 이원 백엔드) 운영 보강 — 충돌 없음 |

> **번호 주의**: Plan 50 §6은 "D-040"으로 적었으나 D-040이 선점되어 **D-042**를 부여한다(D-041과 동일 사유).

### 결정

tool-calling 제어 평면(vLLM **Qwen3.5-9B**, 소용량)에는 **계획 신호만**, 대용량 작업(SQL/데이터/응답)은
**FabriX 평면**으로 강제한다. 원시 도구 결과는 collector에만 보관하고 오케스트레이터 컨텍스트에는
요약본만 노출한다. **상한값은 하드코딩하지 않고 `OrchestratorConfig`(env `ORCHESTRATOR_*`) 노브로 노출**
하여 모델 교체 시 `.env`만으로 예산을 확장한다. 또한 **Qwen 계열은 no-think를 기본 적용**한다.

### 세부 변경

- **B6 — 예산 노브(`OrchestratorConfig`)**: `max_input_tokens`(기본 **12000**), `context_budget_ratio`(0.8),
  `max_tool_result_tokens`(2000), `max_history_turns`(6). 단순 int/float이라 `.env` JSON 이슈 없음
  (2026-03-23 정합). `os.getenv` 미사용(pydantic-settings 필드 — 2026-06-10 정합).
- **서버값 정합(중요)**: 인프라에서 서버 `max_model_len=16384`, `gpu_memory_utilization=0.85`로 상향
  진행 중. 따라서 클라이언트 입력 예산 = 16384 − 출력 여유(~4000) = **12000**으로 확정(계획서의 32768
  가정·24000은 무효 — Plan 50 B6 표/§3.7 갱신 반영).
- **B7 — Qwen no-think(`_create_orchestrator_vllm`)**: `enable_thinking: bool=False` 노브 추가. 모델이
  Qwen 계열일 때만 `model_kwargs.extra_body.chat_template_kwargs.enable_thinking`를 부착(계열 가드 —
  비-Qwen/미지원 서버 오류 회피). `ORCHESTRATOR_ENABLE_THINKING`로 모델 교체 시 전환.
- **B1/B2 — 도구 반환 축소(`deepagents_tools`)**: vLLM 반환 텍스트는 `max_tool_result_tokens`(chars≈tokens×4
  근사) 상한으로 요약·축소. **원본은 collector에만 적재**(최종 FabriX 응답 생성용 — 기존 동작 보존).
- **B8 — 서버 기동 파라미터**: 레포 코드 변경 아님. 인프라에서 `max_model_len=16384`/`gpu_memory_utilization=0.85`
  상향 진행 중(클라이언트 B6 예산과 정합 유지가 배포 체크리스트).

### 근거

- 관찰 오류 `Input tokens must be <=95232. Given: 197986`은 제어 평면(vLLM Qwen) 컨텍스트 폭증.
  멀티턴 압축(D-041)·도구 결과 축소·예산 노브·no-think의 다중 방어로 상한 내 유지.
- 모델 교체 시 코드 수정 없이 `.env`만으로 예산 확장(one-knob scaling).

### 향후 수정 시 고려사항

- 대형 모델 교체 시: 서버 `max_model_len`↑ + 클라이언트 `ORCHESTRATOR_MAX_INPUT_TOKENS`↑ + 필요 시
  `ORCHESTRATOR_ENABLE_THINKING=true`를 **한 세트로** 변경.
- `extra_body`는 langchain_openai에서 top-level 인자로도 전달 가능(향후 model_kwargs 경고 회피 시 검토).

---

## D-043. 재조회(대체) 후속 task의 1차 시도 결과 본문 숨김 (supersedes)

| 항목 | 내용 |
|------|------|
| **결정일** | 2026-06-26 |
| **상태** | 확정 |

### 배경 / 문제

단일 의도 질의(예: "김포 ### 서버의 호스트네임·IP·OS·CPU·메모리")에서 1차 task가
빈/누락 결과("조회된 1000건 중 존재하지 않음, 값 모두 null")를 내자, `replanner`가
프롬프트 예시 1("0건 → 재조회") 패턴에 따라 **같은 의도를 다시 묻는 후속 task(t2)** 를
추가했다. t2가 1건을 찾아 성공했으나, `result_aggregator._merge_finalized`가
**t1(실패 서술)과 t2(성공 서술)를 둘 다 본문에 이어붙여** 출력 →
"없다고 했다가 있다고 하는" 모순된 이중 답변으로 사용자가 혼란스러웠다.

### 결정

후속 task에 **`supersedes`(대체하는 선행 task_id 목록)** 필드를 도입한다.

- **대체(재조회)형 후속**: 같은 질문을 다른 방식으로 재시도 → `supersedes: ["t1"]`.
  → `result_aggregator`가 최종 답변 본문에서 대체된 선행 task 서술을 **숨기고 후속 결과만 노출**.
- **추가(보강)형 후속**(예: 장애 발견 → 알람 이력): `supersedes: []` → 두 결과 모두 노출(D-005 유지).

안전장치: 대체 후속이 **성공(에러 없음)했을 때만** 선행을 숨긴다. 재조회 자체가 실패하면
1차 결과를 그대로 유지하여 빈 답변만 보이는 상황을 방지한다. 숨김은 **최종 답변 본문에만**
적용하며, 처리 현황(SSE) 패널에는 두 task가 모두 투명하게 남는다(관찰성 보존, D-039).

### 구현

- `prompts/replanner.py`: `supersedes` 필드 규칙·예시(예시 1=대체, 예시 2=추가) 추가.
- `orchestration/replanner.py::_assign_ids`: `supersedes` 보존 + 신규 task 간 임시 id 재매핑
  (depends_on/input_from과 동일 처리). 누락 시 빈 배열로 보정.
- `orchestration/result_aggregator.py`: `_collect_superseded`로 숨김 대상 task_id 집합을
  계산(후속 성공 시에만)하여 본문 조립 대상에서 제외. 전부 제외되는 비정상 시 전체 사용(방어).

### 근거 / 대안

- 대안(기각): "특정 엔티티 단일 조회는 0건이어도 replanner 재조회 차단" — 1차 쿼리가 틀려서
  못 찾은 경우 교정 기회를 잃어 사용자에게 '없음'만 답하게 됨. 사용자 결정으로 supersedes 채택.
- 관련: D-005(부분 실패 병합), D-037/D-039(orchestration·처리 현황), D-040(replanner 중복 가드).
- 검증: arch_check --ci exit 0, result_aggregator·replanner 단위 테스트 신규 6건 포함 전체 통과.

---

## D-044. 스트리밍 응답 조건부 자동 스크롤 (stick-to-bottom) + 맨 아래 이동 플로팅 버튼

| 항목 | 내용 |
|------|------|
| **결정일** | 2026-06-26 |
| **상태** | 확정 |

### 배경 / 문제

D-009로 최종 사용자 응답이 SSE `token` 이벤트로 토큰 단위 스트리밍되는데, 프론트(`app.js`)가
토큰마다 **무조건** `scrollToBottom()`을 호출하고 `.chat-messages`에 `scroll-behavior: smooth`가
걸려 있어, (1) 토큰마다 부드러운 스크롤이 재트리거되어 화면이 "튀어" 응답을 즉시 읽기 어렵고,
(2) 사용자가 위로 스크롤해 과거를 읽으려 해도 다음 토큰에 강제로 맨 아래로 끌려 내려갔다.
또한 (3) 위로 올린 뒤 최신 응답으로 되돌아갈 빠른 수단(버튼)이 없었다.

### 결정

ChatGPT/Claude류 **stick-to-bottom 모델**을 도입한다(프론트엔드 전용, 백엔드/SSE 무변경).

- **맨 아래 고정 상태**(`stickToBottom`)를 추적: 맨 아래(임계값 `BOTTOM_THRESHOLD_PX=24` 이내)면 고정, 위로 스크롤하면 해제.
- **토큰/에이전트 출력 추종은 조건부**: 고정 상태일 때만 즉시(비smooth) 따라 내려가고, 해제 상태면 스크롤 위치를 건드리지 않는다(면역). **사용자 본인 질의 메시지 추가만 무조건** 맨 아래로 이동.
- **플로팅 "맨 아래로" 버튼**(우측 하단): 고정 해제 상태에서만 표시, 클릭 시 부드럽게 맨 아래로 이동·고정 복귀.
- **신규 내용 강조**: 고정 해제 상태에서 새 토큰/응답이 도착하면 위치는 유지하되 버튼에 점(`has-new`)을 띄워 새 답변 도착을 인지시킨다.

### 구현

- `static/js/app.js`: `stickToBottom`/`hasNewContent`/`BOTTOM_THRESHOLD_PX` 상태, `isNearBottom()`,
  `scrollToBottom(smooth)`(무조건), `scrollToBottomIfSticky()`(조건부), `updateScrollToBottomBtn()` 추가.
  `chatMessages` scroll 리스너로 상태/버튼 갱신, 버튼 클릭 핸들러. 토큰 루프 및 에이전트 측 출력
  (스트리밍 컨테이너 생성·finalize·에이전트/시스템 메시지)은 `scrollToBottomIfSticky()`로 교체,
  사용자 질의(`renderUserMessage`)만 `scrollToBottom()` 유지.
- `static/index.html`: `.chat-main` 내 `.chat-messages` 형제로 `#scrollToBottomBtn`(+ 강조 점) 추가.
- `static/css/style.css`: `.chat-main`에 `position: relative`, `.chat-messages`의 `scroll-behavior: smooth` 제거,
  `.scroll-to-bottom-btn`(+`.is-visible`/`.has-new`/`.scroll-to-bottom-dot`) 스타일, 반응형 `bottom` 조정.

### 근거 / 대안

- 사용자 결정: 신규 메시지 정책은 (B) 조건부 + 신규 강조 채택(무조건 끌어내리기 기각 — 읽기 흐름 방해).
  임계값은 80px로 두고 테스트 후 조정. 버튼 강조는 적용.
- 관련: D-009(SSE 토큰 스트리밍). 계획서: `plans/51-streaming-scroll-ux.md`.
- 주의: 계획서 초안이 가정한 결정 번호 D-043은 같은 날 다른 결정(supersedes)이 선점하여 **D-044**로 부여
  (변경 이력 표까지 grep하여 확정 — 2026-06-25 번호 충돌 교훈 반영).

---

## D-045. 스트리밍 마크다운 비파괴 렌더 (DOM 모핑) — 표 가로 스크롤·텍스트 선택 보존

| 항목 | 내용 |
|------|------|
| **결정일** | 2026-06-26 |
| **상태** | 확정 |

### 배경 / 문제

D-009 스트리밍에서 토큰마다 `#streamingText.innerHTML = marked.parse(누적텍스트 전체)`로 **서브트리 전체를
파괴·재생성**한다. 표는 `.response-text table { display:block; overflow-x:auto }`로 **테이블 요소 자체가
가로 스크롤 컨테이너**이고 `scrollLeft`은 어트리뷰트가 아닌 라이브 프로퍼티라, 매 토큰 새 테이블이 생성되면
사용자가 우측으로 스크롤한 표가 **좌측으로 초기화**된다(동일 원인으로 스트리밍 중 텍스트 선택도 끊김).
스트리밍 구간 한정 문제(finalize 후 DOM 고정 시 정상).

### 결정

토큰마다 `innerHTML` 전체 교체 대신 **DOM 모핑(B-1)** 으로 전환한다. full `marked.parse` 결과를 기존 DOM에
diff 적용하여 **동일 위치·태그 요소를 재사용** → 표 `scrollLeft`·텍스트 선택 보존. 출력 HTML은 full-parse와
**동일**하므로 출력 정확성 영향 없음(블록 증분 파싱 B-2의 맥락 오판 위험과 대비). 추가로 토큰 버스트를
**rAF로 코얼레싱**(프레임당 1회 렌더)하여 전체 재파싱 O(L²)의 상수를 줄인다.

**구현 방식 — 자체 morph(외부 의존성 없음)**: 계획서 초안은 `morphdom`(UMD) 로컬 벤더를 제안했으나,
폐쇄망이라 CDN 취득이 불가하고 라이브러리 재현 정확성 리스크가 있어 **동등 동작의 경량 자체 구현**으로 결정.
`morphChildren`(인덱스+nodeName 기준 노드 재사용, `isEqualNode`로 무변경 서브트리 스킵) + `syncAttributes`.
실패 시 **폴백(방안 A: 표 `scrollLeft`만 스냅샷·복원하며 전체 교체)** 으로 강등.

### 구현

- `static/js/app.js`:
  - `morphChildren`/`syncAttributes`: 비파괴 DOM 모프(자체 구현).
  - `renderStreamingMarkdown(el, md)`: morph 적용, 예외 시 폴백 A.
  - `scheduleStreamingRender()`: rAF 코얼레싱(`_streamAccumulated`/`_streamRafQueued`), 렌더 후 `scrollToBottomIfSticky()`.
  - 두 스트리밍 토큰 루프: `textEl.innerHTML = renderMarkdown(...)` → `scheduleStreamingRender()`로 교체.
  - `createStreamingMessage()`에서 `_streamAccumulated=""` 초기화(이전 스트림 잔여 rAF 누수 차단).
- 외부 의존성/`index.html` 스크립트 추가 **없음**(morphdom 미사용).

### 근거 / 대안

- 대안(기각) **morphdom 벤더**: 폐쇄망 CDN 불가 + 재현 리스크. 자체 구현이 의존성 0으로 더 견고.
- 대안(보류) **B-2 블록 증분 파싱**: 파싱을 O(L)에 근접시키나 맥락 의존 마크다운 블록 경계 오판 시 출력 정확성
  회귀 위험 → 실사용 응답 크기 분포로 정당화된 뒤 검토(계획서 §13).
- 검증: `morphChildren` 단위 검증(표 2→3행 성장 시 `<table>` 인스턴스 재사용·`scrollLeft=120` 보존·무변경 노드 재사용 PASS), `node --check` 통과. 수동(브라우저) 검증은 잔여.
- 관련: D-009(SSE 토큰 스트리밍), D-044(Part 1 스크롤 UX). 계획서: `plans/51-streaming-scroll-ux.md` Part 2.

---

## D-046. 프로세스 조회 시 서버명 → 호스트명 해소 (process_query)

| 항목 | 내용 |
|------|------|
| **결정일** | 2026-06-26 |
| **상태** | 확정 |
| **관련 결정** | D-041(M4 process_query 신설), D-036(프로세스 API 재사용) 보강 — 충돌 없음 |

### 배경 / 문제

폴스타 실시간 프로세스 API(`polestar_process_api.py`)의 조회 키는 **hostname**(=`cmm_resource.hostname`,
OS 호스트명)이다. 그러나 사용자는 보통 **서버명**(=`cmm_resource.name`, 폴스타 등록 장비명)으로 질의한다.
공동존 폴스타(gp/yd)는 `name ≠ hostname`(Known Mistakes 2026-06-10 실측)이므로, `process_query`가
서버명을 그대로 `hostname=서버명`으로 API에 전달하면 **0건**이 반환된다. 이때 오케스트레이터가
프로세스 의도를 DB 조회로 폴백하여 **리소스명이 '프로세스'인 행을 가져오는 환각**이 발생했다.

### 결정

`process_query`가 프로세스 API를 호출하기 **전에** 입력 식별자(서버명 또는 호스트명)를
**정규 hostname으로 해소**한다. D-041(M4) 보강이며 되돌리지 않는다.

- 신규 `PolestarHostnameResolver`(infrastructure)가 폴스타 DB(`cmm_resource`)에 **고정 SELECT 단일문**
  (LLM 미사용, 읽기 전용)을 실행하여 hostname을 얻는다 — `polestar_history.py`와 동일 DBHub(MCP) 경로 재사용.
- 매칭: 입력 값을 `name`(서버명) **또는** `hostname`(호스트명) 양쪽과 비교, `name` 일치 우선
  (서버명 질의가 일반적). 입력이 이미 hostname이면 idempotent하게 같은 값 반환.
- **graceful 폴백**: 미등록 db_id / 조회 실패 / 0건 / 빈 hostname이면 해소 None → 원시 입력 값을 그대로
  사용한다(이미 hostname이거나 DB 미연결이어도 회귀 없음).
- 관찰성: 결과 `process_query`에 `server_name`(원본 입력)·`hostname`(해소값) 동시 보존, 요약에는
  서버명을 표기하되 hostname이 다르면 `서버명(호스트명 ...)` 형태로 병기.

### 세부 변경

- `src/alarm/infrastructure/polestar_hostname_resolver.py` **신규**: `build_hostname_sql`(고정 SELECT,
  `server.Server`·`DTIME IS NULL`·name/hostname OR·name 우선 ORDER BY)·`PolestarHostnameResolver.resolve`.
  D-022(RESOURCE_CONF_ID JOIN 금지)·2026-06-10(`is_lob` 조건 금지) 정합.
- `src/orchestration/process_query.py`: `_resolve_hostname` 결과를 `identifier`로 받아 `_resolve_canonical_hostname`
  (DBRegistry+resolver, 예외 시 None)로 hostname 해소 후 API 호출. 미식별 안내문을 "hostname"→"서버명"으로 수정.

### 근거

- 환각의 구조적 원인(서버명≠hostname을 무해소로 API 전달)을 호출 직전에 차단.
- 회귀 없음: 해소 실패·DB 미연결 경로는 기존 원시 값 사용 동작 유지(테스트로 고정).

### 향후 수정 시 고려사항

- 폴스타 인스턴스별 스키마가 다르면 `polestar_hostname_resolver._SCHEMA_BY_DB_ID`에 db_id별 스키마를 등록
  (`polestar_history._SCHEMA_BY_DB_ID`와 동일 패턴).
- 해소 쿼리는 매 프로세스 조회마다 1회 실행(LIMIT 1, 가벼움). 빈도 급증 시 캐시 검토.

---

## D-047. 프로세스 조회 대상 서버 식별자 추출 (input_parser 규칙 보강)

| 항목 | 내용 |
|------|------|
| **결정일** | 2026-06-29 |
| **상태** | 확정 |
| **관련 결정** | D-046(서버명→hostname 해소), D-041(M4 process_query) 보강 — 충돌 없음 |

### 배경 / 문제

"김포 ###서버에 대한 프로세스 리스트 조회" 질의가 **"프로세스 조회 대상 서버(서버명)를 식별하지 못했습니다"**
로 응답되었다. `process_query._resolve_hostname`는 이번 턴 `filter_conditions` 중 field가
`_HOST_FIELDS`(`hostname/host_name/server_name/name`)인 조건의 value를 서버 식별자로 쓰는데,
**input_parser 프롬프트에 지목된 서버 이름을 hostname filter로 추출하는 규칙이 없었다.** LLM이
서버명을 filter_condition으로 만들지 않거나 한글 field명(`서버명`/`장비명`)으로 만들어 `_HOST_FIELDS`
매칭에 실패 → `identifier=None`. D-046(서버명→hostname 해소)는 `identifier`를 **얻은 뒤** 동작하므로
이 공백은 D-046 이전 단계의 누락이었다.

### 결정

특정 서버를 지목하는 질의(특히 프로세스 조회)에서 **서버 식별자를 항상 filter_conditions로 추출**하고,
`_resolve_hostname`은 LLM 출력 비결정성을 고려해 **한글 field 변형까지 방어적으로 인정**한다.
추가로 **시간성(이력/추세) 신호가 없는 프로세스 조회는 `process_query`(실시간 API)로 결정적 교정**한다.
되돌리지 않는다.

- `input_parser` 프롬프트 규칙 14 신설: "XXX 서버", "XXX에 대한 프로세스/CPU", "XXX의 프로세스" 등 단일
  서버 지목 시 `{"field": "hostname", "op": "=", "value": "<서버식별자>"}`로 추출. 위치/DB 수식어
  (김포/여의도/폴스타)는 value에서 분리해 target_db_hints로(규칙 10 정합). 프로세스 리스트 질의는 대상
  식별 필수임을 명시. 예시 1건 추가.
- `process_query._HOST_FIELDS` 확장: 영문(`host`/`device_name`)·한글(`서버명`/`서버이름`/`장비명`/
  `호스트명`/`서버`/`장비`) 변형 수용. 매칭 성공 후의 정규화는 기존 D-046 해소가 그대로 처리.
- **라우팅 교정(2차 원인)**: "현재/실시간/리스트" 같은 시간성 수식어가 없는 "프로세스 조회"를 LLM이
  보수적으로 `data_query`로 분류 → `cmm_resource`의 `resource_type='process'` 행을 가져오는 환각(D-046이
  경고한 폴백)이 발생했다. (a) `prompts/intent_planner.py` 규칙 3을 "프로세스 조회는 기본 process_query,
  명시적 과거/이력 신호가 있을 때만 data_query"로 강화. (b) `intent_planner._coerce_process_intent`:
  LLM 분해(폴백 포함) 결과에서 `agent=="data_query"`이고 sub_query에 "프로세스"가 있으나 이력 신호
  (이력/추세/추이/지난/과거/기간/동안/시점 등)가 없으면 `process_query`로 결정적 교정.

### 근거

- 식별자 추출은 자연어 파싱 단계의 책임 — 프롬프트 규칙으로 결정적 유도가 정석. field 변형 수용은
  LLM 비결정성에 대한 저비용 안전망(회귀 없음: 기존 영문 field 동작 유지).
- D-046의 graceful 폴백과 합쳐, 식별자가 잡히면 잘못된 값이어도 0건 안내로 수렴(환각 폴백 없음).

### 프로세스 결과 표시/다운로드 (채팅 상위 N + CSV 전체)

| 항목 | 내용 |
|------|------|
| **결정일** | 2026-06-29 |
| **상태** | 확정 (사용자 결정) |

- **문제**: process_query가 `select_top_processes`로 상위 N(기본 `process_top_n=5`)만 남기고 **전체를 폐기**해,
  채팅·CSV 모두 5건만 노출됐다. 또한 orchestration 경로는 `query_results`를 top-level state로 승격하지 않아
  (result_aggregator가 `final_response`만 반환) **CSV 다운로드 버튼 자체가 뜨지 않았다**(`row_count=0`).
- **결정(사용자)**: 채팅 말풍선은 상위 N만 표시하되, **CSV 다운로드는 전체 프로세스**를 받도록 한다.
- **변경**:
  - `process_query.py`: 전체를 1회 정렬·마스킹(`select_top_processes(.., len(processes))`)한 뒤
    `organized_data.rows`=상위 N(채팅 표 — output_generator가 이 rows로 생성), `query_results`=전체(CSV·row_count).
    요약에 "전체 X건은 CSV 다운로드" 안내, `process_query.shown_count` 추가. `process_top_n`은 **표시 전용**.
  - `result_aggregator.py`: `_finalize_task` 결과(base)에 `query_results`를 보존하고, 단일/복합(`_merge_finalized`,
    행 이어붙임) 모두 **top-level `query_results`로 승격** → orchestration 결과(데이터/프로세스 공통)에서
    CSV 버튼·row_count가 동작. (스트리밍 `done`/비스트리밍 응답 모두 result_aggregator 출력의 query_results 사용.)
  - `api/routes/query.py::download_csv`: 행마다 키가 달라도(복합 병합) 깨지지 않도록 컬럼명을 **등장 순서 합집합**으로
    만들고 `restval=""`/`extrasaction="ignore"` 적용.

### 향후 수정 시 고려사항

- 한 질의에서 복수 서버를 지목하는 경우는 현재 첫 번째 host filter만 사용. 다중 서버 프로세스 조회
  요구가 생기면 `_resolve_hostname`을 리스트 반환으로 확장 검토.
- 전체 프로세스가 매우 많으면(수백+) CSV는 전부 받지만 채팅 표는 상위 N만 — 표시 N 조정은
  `ALARM_PROCESS_TOP_N`. 채팅에서도 더 보고 싶다는 요구가 잦으면 표시 N 기본값 상향 검토.

---

## D-048. 알람 노이즈 캔슬링 — 4-티어 발송 게이트 (Plan 52 E1)

| 항목 | 내용 |
|------|------|
| **결정일** | 2026-06-29 |
| **상태** | 구현 완료 (Phase E1 MVP, 2026-06-29) |
| **관련 결정** | **D-035 확장**(패턴을 부가정보→결정적 게이트의 보조 입력으로, 심각도3 보존 유지), D-003(읽기전용 준수), D-029~D-032(알람 파이프라인 재사용), D-037(트랙 B vLLM — D-048.7 전제) |
| **번호 정정** | 최초 계획(Plan 52/53 §13)은 **D-040** 명시 → D-039/D-040이 2026-06-23 변경이력에 선점되어 **D-041**로 1차 등재했으나, 병렬 multiintent 작업이 **D-041(멀티턴, Plan 50)~D-047**을 점유(2026-06-25)하여 재충돌. 사용자 승인(2026-06-29)으로 노이즈 게이트를 **D-048**로 최종 리넘버. **Plan 50의 D-041~D-047은 보존**. |

### 결정

폴스타 알람을 수신 시 **결정적 규칙 파이프라인**으로 **PAGE / TICKET / DASHBOARD / SUPPRESS** 4-티어로 라우팅하는 발송 게이트를 추가한다(전 기능 옵트인 `enable_noise_gate=False` 기본). LLM(`is_routine`)은 보조 입력 1개일 뿐 판단은 결정적이며, **심각도 3은 어떤 억제 단계도 거치지 않고 항상 PAGE**한다(D-035 계승). 억제·강등 결정도 감사 기록한다(억제 ≠ 삭제). 신호는 폴스타 읽기전용 DB(중요도·유지보수·알림정책)에서 고정 SQL로 수집하며 실패 시 보수적 PAGE(재현율 우선).

### 하위 결정 (Plan 52 §13 — 원안 D-040.x를 D-048.x로 등재)

- **D-048.1** 4-티어(PAGE/TICKET/DASHBOARD/SUPPRESS), 결정적 규칙=판단·LLM=보조, 결정 근거(reason) 기록.
- **D-048.2** 중요도×심각도 매트릭스 + 유지보수/자가복구 억제(E1). 의존성/인히비션/플래핑은 E2. **심각도3 절대 PAGE**.
- **D-048.3** 신호는 폴스타 읽기전용 DB(IMPORTANCE_ID/IS_MAINTENANCE/`cmm_alarm_def_noti*` 앵커). `IMPORTANCE_ID` 값코드 미확정 시 **미매핑=보통(보수적)**(`importance_value_map_csv` 기본 빈값). `cmm_alarm_def_noti*` 통보대상 존재→`notify`, 불확실→`None`(절대 단정적 `suppress` 미반환).
- **D-048.4** 재현율 우선·억제≠삭제(`decision_store` JSONL 감사)·억제기 메타모니터링(억제율 집계). 전 기능 옵트인.
- **D-048.5** (E3, **구현 완료 2026-06-30**) AI 분석은 **LLM 인컨텍스트, ML 모델 미사용**. 메시지 심각도 보강은 **상향 전용(monotonic) `max()`** + 결정적 시그니처(Plan51 부록A.1) 우선. 폴스타 심각도가 베이스라인(SSOT). 적용 범위는 **메시지형 알람(`condition_log` 보유, 임계형 수치 알람 제외)** 한정(사용자 확정 §13.1#7: LogMonitor+보안+앱 로그). `ai_severity_escalate_only=True` 안전 고정, 하향/강등 경로 부재(R-10). `alarm_analyzer`가 기존 단일 LLM 응답을 재파싱(추가 호출 없음)하여 `ai_message_severity`를 산출, `decide_notification` step1이 `max(severity, ai_severity)`로 결합. 심각도3은 AI와 무관하게 항상 PAGE.
- **D-048.6** (향후·미구현) **Plan 55 멀티소스 확장 대비** — `noise_context`는 소스 무관 확장형 dict(`parent_avail_status` 등 자리예약), step 8 보조 축, 영향 신호 **승격 비대칭**(§6.4·§8.6). 지금은 설계 예약만.
- **D-048.7** (E5, **구현 완료 2026-07-02** — 사용자 확정 §13.1#8: 3경로 전체 구현·메시지형 한정) **deepagents Advisory Enricher** — agentic 분석은 **보조(`signals` 승격 전용)** 만, 판단은 결정적. 신규 `agentic_enricher_node`(application)를 `alarm_analyzer → agentic_enricher → notification_gate` 사이에 삽입. **3경로 자동 선택**(`_select_backend`): (트랙 B) vLLM health OK → 읽기전용 `@tool` bind_tools ReAct(`run_signal_react_loop`, `max_tool_calls` 상한) / (트랙 A) vLLM 미가용+`fallback="semantic_routing"` → FabriX 1회 JSON 분류(`needed_signals`)→결정적 컬렉터 고정 SQL·시그니처 스캔 / (no-op) `deterministic_only` 또는 LLM 부재 → 결정적 게이트만. **승격 전용 비대칭**(R-10/R-12): 후보가 `event.severity` **초과** && 기존 `ai_message_severity` 초과일 때만 상향, **하향/강등 경로 부재**. **심각도3은 step0 PAGE 단락으로 enricher 미개입**(PAGE 불변·비용절감). **메시지형 한정**(`message_alarms_only=True` — 임계형 CPU/메모리 수치 알람 제외). **계층 배치(치명적)**: 도구·health check·ReAct 루프를 **infrastructure(`noise_signal_tools.py`)** 에 두어 application→orchestration 역방향(arch error) 회피 — 계획서 §14의 "orchestration/deepagents_tools 재사용" 표기는 참고이며 실제는 infra 배치가 정답(arch_check로 확정). **collector 패턴**(Known Mistakes 2026-06-17): 도구는 truncate 전 원본을 collector에 적재, enricher는 ToolMessage 재파싱 없이 원본 소비. **graceful**: 타임아웃(`agentic_enricher_timeout_seconds`)·예외·no-op은 발송 무차단(빈 dict 반환). **전 기능 옵트인**(`enable_agentic_enricher=False`면 E1~E4 배선·판단 무변경 — 게이트 off면 enricher_enabled=`gate and enricher`로 강제 False, 회귀 0). 환각→오억제는 R-12로 통제(강등 경로 부재). 신규 `noise_signal_tools.py`(infra)·`prompts/agentic_enricher.py`·`nodes/agentic_enricher.py`·`test_agentic_enricher.py`(36) + `config.py` 설정 5종·`alarm_graph.py` 조건 배선. **트랙 B Gemini 테스트 경로 추가(2026-07-02, 사용자 요청)**: `_select_backend`가 `orchestrator.provider=="gemini"` + api_key 존재 시 **vLLM 없이 트랙 B 선택**(Gemini 네이티브 tool-calling — `create_orchestrator_llm` gemini 분기가 실행 코드 공용이라 라우팅만 개방). provider="vllm"(운영)은 종전대로 `vllm_healthy`로만 track_b(회귀 0). 키 없으면 폴백(gemini에서도 vllm_healthy 미호출). 이는 D-037/§4.7의 "gemini=테스트/PoC 오케스트레이터" 패턴 계승 — agentic 경로를 vLLM 인프라 없이 실검증하기 위함. 신규 `test_agentic_enricher_gemini_live.py`(실 Gemini bind_tools ReAct·노드 end-to-end·승격전용 불변, gemini 키 없으면 모듈 skip). 검증(§11.1 baseline): `tests/test_alarm` **398 passed**(기존 359 + enricher 단위 36 + gemini live 3, 실패 0 — live는 이 환경 실 키로 실제 Gemini 호출 통과), `arch_check --ci` exit 0. **운영 주의**: 트랙 B의 vLLM 실서빙 라이브 검증은 여전히 vLLM 준비 후 별도 필요(gemini는 테스트/PoC 경로).
- **D-048.8** (E3, **구현 완료 2026-06-30** — 사용자 확정 §13.1#2) **억제 적극성 = DASHBOARD 강등**. 저중요도 자산의 **severity 1(주의)×중요도 낮음 매트릭스 셀을 SUPPRESS → DASHBOARD**로 확정(묵살이 아니라 대시보드 가시화). §3.2 매트릭스 외 셀은 불변. 메타경보 억제율 임계(`meta_alert_suppress_ratio`)는 보수적(기본 0.9).
- **D-048.9** (E3, **구현 완료 2026-06-30** — 사용자 확정 §13.1#3) **TICKET 티어 = 감사 + DASHBOARD(SSE) + 일배치 요약 큐 조합**(실시간 별도 채널 없음). 신규 `ticket_queue.py`(JSONL, graceful) 적재 + bus 가용 시 SSE 푸시. 워커 경로는 cross-process라 `alarm_bus` 미주입 → SSE는 로그 폴백·일배치 큐만 동작, **API 경로(`analyze_alarm_*`)는 bus 주입으로 SSE 실동작**(이 워커 경로 한계는 **D-048.10에서 해소**). 운영지표(`/alarm/metrics`)는 `decision_store` 집계(티어별·액션가능 비율·억제율·무수신)로 노출하되, **MTTA/MTTR/사건전환율은 ack·incident 계측 부재로 `null`+`unavailable_metrics` 사유 명시**(환각 수치 금지) — 후속 계측 필요.
- **D-048.10** (E3 후속, **구현 완료 2026-06-30** — 사용자 확정) **워커→UI 실시간 SSE Redis pub/sub 브리지**로 D-048.9의 워커 경로 한계 해소. 신규 `infrastructure/sse_bridge.py`: 워커측 `RedisSseBridgePublisher`(`publish(event)`→`redis.publish("alarm:sse", json)`, graceful) + API측 `run_sse_bridge_subscriber`(채널 구독→기존 `app.state.alarm_bus.publish`로 재팬아웃→`/alarm/notifications/stream`→프론트 무변경). **pub/sub 선택 근거**: SSE는 fire-and-forget(at-most-once 허용) — TICKET은 큐+감사로 영속, DASHBOARD는 정보성이라 소비그룹/ACK 불요(과설계 회피). **단방향**: `_publish_tier_sse`가 `alarm_bus` 우선, 없을 때(워커 경로)만 주입된 `sse_publisher`로 Redis 발행 → API 직접 publish·주입 bus와 **이중 발행 방지**. **티어 분기 불변**: TICKET/DASHBOARD만 브리지(SUPPRESS=로그, PAGE=workb), 감사·큐 중복 적재 없음(SSE 표시만). **계층**: notifier(application)는 redis 직접 import 없이 주입된 `sse_publisher` 덕타이핑만 호출. **옵트인**(`enable_noise_gate` AND `sse_bridge_enabled`, 기본 False) + graceful(Redis 미가용/실패→로그 폴백, 서버 기동 무차단) → 회귀 0. 인프로세스(워커 task가 같은 API 프로세스)·standalone 워커 양 배포 모두 Redis 채널 경유로 동작. 운영 주석: 브리지 on인데 Redis 다운 시 인프로세스에서도 티어-SSE가 조용히 미도달(graceful이나 비가시) — 메타모니터링으로 보완.
- **D-048.11** (E4, **구현 완료 2026-07-01** — 사용자 확정 §13.1) **LLM 액션가능성 판단(피드백 few-shot, ML 모델 미사용)**. 운영자 피드백(유효/노이즈)을 `feedback_store`(신규 JSONL, decision_store 미러·graceful)에 적재 → 유사 과거 알람을 **alarm_name·resource·pattern 키**(§3.9, fingerprint 아님)로 few-shot 검색 → `alarm_analyzer`가 **기존 단일 LLM 응답을 재파싱**(추가 호출 없음·D-048.5 패턴)하여 `llm_actionability`(actionable|noise|null) 자문 산출. **판단은 결정적 규칙(step 9)**: `actionable`→promote(승격은 항상 안전), `noise`→demote(단 `effective_severity ≤ suppress_max_severity` 가드 — is_routine demote와 동일), **승격우선 기계**가 promote 공존 시 noise demote를 무시, 1단계 이내 이동·SUPPRESS 하한 불변. **심각도3 절대 PAGE 불변**(step3 단락, E4는 step9만 관여), 불확실→`null`(재현율 우선·승격 비대칭). `signals["llm_actionability"]` 키 추가(감사·설명가능성·Plan 54 드로어 정합 — §8.2 스키마 가드 2곳 갱신). **전 기능 옵트인**(`enable_llm_actionability=False`면 E3 무변경·회귀 0 — policy는 값 미독·None, analyzer는 few-shot 미조회·재파싱 스킵). 캡처: `POST /alarm/feedback`(require_user, 게이트/액션가능성 off면 503·label 검증) + `app.js` 알람 카드 유효/노이즈 버튼(closure 바인딩·503 처리·`textContent` XSS 안전). 구현상 주의: `llm_actionability` 추출을 첫 `_decision` **이전으로 hoist**(sev3 조기 반환 경로의 `_signals()` 클로저 참조 시 NameError 방지 — `test_severity3_always_page_regardless`로 고정). 신규 `feedback_store.py`·`POST /alarm/feedback`·테스트 22. 검증(§11.1 baseline·팀리드 독립 재실행): `tests/test_alarm` **359 passed**(E3/D-049 337 + 신규 22, 실패 0), `arch_check --ci` exit 0(feedback_store=infra·stdlib, notification_policy=stdlib 유지), `AppConfig()` OK, `node --check app.js` OK, 게이트오프 회귀 0(3계층 테스트 고정). E5(deepagents Advisory Enricher, D-048.7)는 vLLM 가용성 확인 후 별도 착수(§13.1#8).

### 핵심 설계 결정 (E1 구현)

| 항목 | 결정 |
|------|------|
| 결정 파이프라인 | `notification_policy.decide_notification`(순수함수, domain) 8단계: 실효심각도→수집실패 보수화→심각도3 단락 PAGE→해소/자가복구→유지보수 SUPPRESS→매트릭스→보조 조정(앵커·is_routine, 1단계·승격 우선)→확정 |
| `min_severity` 역할 분리(§4.8) | 게이트 활성 시 워커는 `1 ≤ severity < min_severity`만 드롭. **severity 0(해소)은 자가복구용 전달, severity 3은 절대 드롭 금지**. 권장 운영값 `ALARM_MIN_SEVERITY=1`. 게이트 off면 기존 `severity < min_severity` 유지 |
| 핑거프린트 dedup(§6.1) | 게이트 활성 시 dedup 키를 `alarm_id`→`compute_fingerprint(db_id,server\|hostname,alarm_name,resource)`로 교정. 해소 이벤트는 dedup 제외(자가복구 상관). TTL=`repeat_interval_seconds`(기본 4h, Alertmanager 패턴). **재발생 재통보 억제는 모든 심각도에 적용** — 단 최초 발생 severity 3은 항상 게이트 도달 PAGE(설계 관찰: §6.1 재통보 억제 ≠ §4.8 임계 드롭) |
| 자가복구 상관(§3.7) | 워커 인메모리 `_firing_registry`로 발생(severity ≤ `suppress_max_severity`, 기본2)을 기록, `self_heal_window_seconds`(기본300) 내 해소(severity 0) 매칭 시 SUPPRESS. **심각도3 제외**(해소가 와도 발생 PAGE 보존) |
| 독립 해소(§6.3) | 매칭 발생 없는 severity 0 → 기본 SUPPRESS(감사), `resolved_to_dashboard=true`면 DASHBOARD |
| 4-티어 라우팅(§7) | PAGE→기존 `_send_workb`/`_send_webhook`(무변경), TICKET/DASHBOARD/SUPPRESS→발송 안 함·로그(감사는 게이트가 적재). DASHBOARD SSE 푸시는 워커 경로에 `alarm_bus` 미주입이라 **E3 후속** |
| 설정 구조(§8.5) | `NoiseGateConfig`를 `AppConfig`의 **형제 필드** `cfg.noise_gate.*`로 신설(env_prefix `NOISE_`). AlarmConfig 이중 중첩 회피. 전 필드 스칼라/CSV(JSON list 회피) |
| graceful degradation | 노이즈 신호 수집 실패/미등록 db_id/타임아웃 → `source="unavailable"` → 보수적 PAGE. 발송 절대 차단 금지(Plan 47 enricher 패턴 계승) |
| 회귀 0(옵트인) | `enable_noise_gate=False`(기본)면 그래프에 게이트 노드 미포함, 워커 dedup/필터 기존 경로, notifier `notification_decision=None` 분기로 기존 발송 무변경 |

### 변경된 파일

| 파일 | 변경 | 계층 |
|------|------|------|
| `src/alarm/domain/notification_policy.py` | 신규 — `decide_notification`/`compute_fingerprint`/`map_importance`/`NotificationDecision`(signals 12키 §8.2) | domain |
| `src/alarm/infrastructure/polestar_noise_context.py` | 신규 — 중요도/유지보수/알림정책 고정 SQL(읽기전용, `_sql_literal`, Template C-6 서버매칭, graceful) | infrastructure |
| `src/alarm/infrastructure/decision_store.py` | 신규 — `DecisionStore` JSONL 결정 감사 + 집계(억제율) | infrastructure |
| `src/alarm/application/nodes/notification_gate.py` | 신규 — 게이트 노드(결정 산출 + decision_store 적재) | application |
| `src/alarm/orchestration/alarm_graph.py` | `AlarmState`에 noise_context/notification_decision/self_heal, 배선 `history_enabled or enable_noise_gate`(§8.1) | orchestration |
| `src/alarm/application/nodes/alarm_context_enricher.py` | noise_context 동시 수집(gather, 게이트 활성 시) | application |
| `src/alarm/application/nodes/alarm_notifier.py` | 4-티어 라우팅 분기(decision None=기존 경로) | application |
| `src/alarm/application/alarm_worker.py` | 핑거프린트 dedup(§6.1)+min_severity(§4.8)+self_heal 시드+noise_repo/decision_store 주입(게이트 활성 시) | orchestration |
| `src/config.py` | `NoiseGateConfig` 신규(형제 필드 §8.5) | config |
| `.env.example` | `NOISE_*` 추가(기본 비활성) | 설정 |
| `tests/test_alarm/` (신규 패키지) | 정책/스토어/컬렉터 단위(57) + 통합(배선/게이트오프/티어/감사/min_severity 35) = **92 통과** | 테스트 |

### 검증 (2026-06-29 실측, §11.1 baseline 정책)

- `tests/test_alarm/` 92 passed(0 fail), 기존 알람 회귀 116 passed, graph 30 passed — 노이즈 모듈 import 파일 13개 신규 실패 0.
- `python scripts/arch_check.py --ci` exit 0(error 0, 기존 orchestration→prompts warning 3건만 — 무관).
- `AppConfig()` 정상 생성, `NOISE_` env prefix 로드 확인, 게이트오프 회귀 0.
- 전체 스위트의 기타 실패는 **52와 무관한 multiintent/인증 부채 + 라이브 DB/LLM/Redis 부재**로 baseline 차감(별도 트랙).

### 향후 수정 시 고려사항

- **E2 착수**: 의존성 억제(`AVAIL_DEPEND_RESOURCE_ID`+부모 `AVAIL_STATUS`)·인히비션·스톰 그룹핑·플래핑(Nagios). `noise_context.parent_avail_status` 자리예약 활용.
- **심각도3 재발생 재통보**: 현재 `repeat_interval_seconds`(4h)가 모든 심각도에 적용 → 운영에서 심각도3 재통보가 잦아야 하면 severity별 `repeat_interval` 분리 검토(현 동작은 Alertmanager 표준 계승, 최초 발생 PAGE는 보장).
- **`IMPORTANCE_ID` 값코드**: 인스턴스별 표본 확인 후 `NOISE_IMPORTANCE_VALUE_MAP_CSV` 설정(미설정=전부 보통).
- **DASHBOARD SSE**(E3 + 후속 완료): API 경로는 `alarm_bus` 직접 주입으로 동작하고, **워커 경로는 D-048.10 Redis pub/sub 브리지로 해소**(`sse_bridge_enabled` 옵트인). 남은 후속은 없음(브리지 기본 off — 운영 활성화 시 Redis 가용 필요).
- **운영지표 ack/incident 계측**(E3 후속): `/alarm/metrics`의 MTTA/MTTR/사건전환율은 ack·해소상관 데이터가 decision_store에 없어 미산출(null). 향후 ack/incident 이벤트 계측 추가 시 활성화.
- **AI 심각도 보강 적용 확대**(E3 후속): 현재 메시지형(`condition_log`) 한정. 시그니처 치트시트(Plan51 A.1) 확장·신규 로그 패턴 추가 시 `severity_signatures.py` 보강.

---

## D-049. ack/incident 라이프사이클 계측 — PostgreSQL 단일 저장소 (Plan 52 §9.1 · §13.1 #9)

| 항목 | 내용 |
|------|------|
| **결정일** | 2026-06-30 |
| **상태** | 확정 (사용자 결정) — **구현 완료** (백엔드 + UI 2 surface: 채팅 알람카드 확인버튼 + admin "열린 사건" 패널) |
| **관련 결정** | D-048.9(MTTA/MTTR/사건전환율을 `null`+`unavailable_metrics`로 유보 — 본 결정이 그 후속 계측), D-048.10(워커→Redis→API 브리지 패턴 재사용), D-048.4(억제≠삭제 감사), D-026/D-027(앱 운영데이터 쓰기 PG `audit_logs`/`auth_users` 선례), D-003(읽기전용은 폴스타 한정 — 앱 운영데이터 쓰기와 무관) |

### 배경 / 문제

E3(`GET /alarm/metrics`)는 `decision_store`(발송 판단 JSONL) 집계로 산출 가능한 지표(티어별 건수·억제율·
액션가능 비율·무수신 메타경보)만 노출하고, **MTTA·MTTR·사건전환율은 `null`+`unavailable_metrics`**(환각 금지)
로 두었다(D-048.9). 이 세 지표는 **incident 라이프사이클(firing→ack→resolved) 상태 전이**와 **시간창 집계**가
필요한데, 다음 두 가지 구조적 제약 때문에 현행 JSONL `decision_store`로는 불가능하다:

- **프로세스 경계**: ack(확인)은 웹 UI "확인" 버튼 → **API 프로세스**에서 발생하고, incident 생성(PAGE)·해소
  (clear/self-heal)는 **워커 프로세스**(`alarm_worker`)에서 발생한다. 같은 incident 레코드를 두 프로세스가
  갱신해야 한다.
- **가변 상태 + 집계**: JSONL은 append-only라 상태 전이 UPDATE 불가, 로컬 파일이라 cross-process 갱신 비안전,
  열린 incident 조회·시간창 집계가 약하다.

### 결정

incident 라이프사이클 저장소를 **PostgreSQL 단일 저장소**로 한다(Redis 단독·혼합 기각). 앱은 이미
`audit_repository`(`audit_logs`)·`user_repository`(`auth_users`)로 **쓰기 가능한 PG**를 운영하므로 신규 의존이
아니며, 동일 repo 패턴을 재사용한다.

- **계층(arch_check clean)**: port `IncidentStore`(domain) + 어댑터 `src/alarm/infrastructure/incident_repository.py`
  (`audit_repository` 미러). application(notifier/worker subscriber)은 주입된 port만 호출.
- **테이블 `alarm_incidents`**: `id`(PK), `fingerprint`, `alarm_id`, `db_id`/`server`, `severity`, `priority`,
  `tier`, **`status`(open|ack|resolved)**, `created_at`(PAGE/firing), `acked_at`, `acked_by`, `resolved_at`,
  `resolution`(self_heal|clear|manual). 인덱스: `status`, `fingerprint`(부분 `WHERE status='open'`), `created_at`.
  DDL은 기존 `ddl/auth_tables.sql` + 기동시 `_ensure_*_tables()` 자동 생성 패턴을 따른다(신규 `ddl/` SQL 파일 +
  lifespan auto-create).
- **쓰기 단일화(D-048.10 브리지 패턴 재사용)**: 워커는 PG 쓰기 풀을 신설하지 않는다. incident 이벤트를
  **Redis 채널 `alarm:incident`로 발행 → API 프로세스의 단일 PG 라이터(subscriber)가 영속**한다.
  - **open**: notifier가 **PAGE 결정 시** open 이벤트 발행 → subscriber가 open 행 INSERT.
  - **resolved**: 워커가 **clear 이벤트(is_clear)** 수신 시 fingerprint 키로 resolved 이벤트 발행 →
    subscriber가 매칭 open 행을 `resolved`로 UPDATE(`resolution='clear'`). self-heal 매칭은 `resolution='self_heal'`.
  - **ack**: 웹 UI "확인" 버튼 → `POST /alarm/incidents/{id}/ack`(API가 PG 직접 UPDATE: `status=ack`,
    `acked_at=now`, `acked_by`). **식별키 = incident id**.
- **PG 풀(④ 확정)**: `incident_tracking_enabled` 플래그 기준 **전용 풀**(auth_pool 종속 회피 — `AUTH_ENABLED`와
  독립). DSN은 기존 앱 쓰기 PG(`db_connection_string`) 재사용. 풀 수명주기는 lifespan에서 관리.
- **지표 `/alarm/metrics`**: `mtta_seconds=AVG(acked_at-created_at)`, `incident_mttr_seconds=AVG(resolved_at-created_at)`,
  `incident_conversion_rate=incidents/page_count` — 모두 window SQL. 해당 항목의 `null`/`unavailable_metrics` 제거.
- **MTTR 라벨 분리(정직성 — 환각 금지)**: self-heal 상관(`_update_firing_registry`)은 발생↔해소 소요시간을
  **이미 in-memory 계산**하므로 `decision_store` JSONL에 resolved 레코드 append로 `auto_recovery_mttr_seconds`를
  **저위험·선행** 산출한다. 단 self-heal은 **sev1..suppress_max만 대상(sev3 제외)** 이라 **편향(부분)** 지표이며,
  paged incident의 운영자 MTTR은 PG 기반 `incident_mttr_seconds`로 별도 노출한다. **두 지표는 라벨로 명확히 구분**한다.
- **옵트인**: `incident_tracking_enabled`(기본 False) + `incident_event_channel`(기본 `alarm:incident`).
  기본 off → 트래커 미기동, `/alarm/metrics`는 기존 null 동작 유지(**회귀 0**). graceful: Redis/PG 미가용 시 warning
  후 폴백, 알람 발송·서버 기동 무차단.

### 근거

- incident 요건의 지배 비용은 **상태 전이 UPDATE + 이력 시간창 집계**이고 둘 다 PostgreSQL 강점. "열린 incident"는
  상시 소수라 Redis의 속도 이점이 무의미(인덱스 조회로 충분).
- `audit_repository`(쓰기 repo)·`sse_bridge`(워커→Redis→API)·startup DDL auto-create — **기존 자산 3종을 그대로
  재사용** → 신규 표면 최소·arch_check clean·회귀 리스크 낮음.
- MTTA/MTTR/전환율이 순수 SQL(`AVG(ts차)`, count 비율)로 산출됨.

### 대안 (기각)

- **Redis 단독**: 활성 incident 상태(HASH/ZSET)는 빠르나 **감사급 이력의 시간창 집계가 약하고** 내구성이
  persistence 설정 의존 → MTTA/MTTR 산출 부적합.
- **혼합(Redis 활성 + PG 이력)**: 활성셋이 작아 이중 저장·dual-write 일관성 부담이 이득을 초과 → 과설계
  (Simplicity First 위배).
- **현행 JSONL 확장**: append-only·로컬 파일·cross-process 갱신 비안전 → 3요건(상태 전이·열린 incident 조회·집계)
  모두 미충족.

### 향후 수정 시 고려사항

- 복수 open incident가 같은 fingerprint를 가질 수 있는 경합(재발생) — resolved UPDATE는 가장 최근 open 1건 대상
  또는 전체 정책 확정 필요(구현 시 `ORDER BY created_at DESC LIMIT 1` 기본).
- ITSM 연동(외부 incident 시스템) 확장 시 `resolution='manual'`/외부 incident id 컬럼 추가 검토(현재 자기완결).
- ack 권한(운영자 전용) — `acked_by`는 인증 사용자 id, AUTH 비활성 환경에서는 익명/UI 입력 허용 여부 확정 필요.

---

## D-050. 단일 서버 필터 + EAV 피벗(CPU·메모리) 조회 SQL 교정 (HAVING 패턴)

| 항목 | 내용 |
|------|------|
| **결정일** | 2026-06-29 |
| **상태** | 확정 |

### 배경 / 문제

"김포 ### 서버의 OS·IP·호스트명·CPU·메모리" 질의에서 **서버는 실재하고 CPU·메모리 데이터도
존재하는데** 조회 결과 CPU·메모리만 null로 나왔다(OS/IP/호스트명은 정상). 사용자가 "데이터를
제대로 못 가져오는 게 문제"라고 지적 → 조사 결과 **조회 SQL 생성 오류**로 확인.

폴스타(공동존)는 EAV 모델: CPU는 `resource_type='server.Cpus'`, 메모리는 `'server.Memory'`
**행**에 저장되어 server.Server 행과는 `platform_resource_id`로만 묶인다. 한 행으로 보려면
`GROUP BY COALESCE(platform_resource_id, id)` 피벗(Template A)이 필요하다. 그런데:
- 피벗 예시(query_example #4, Template A)에는 **단일 서버 필터가 없고**,
- 서버명 필터 예시(#5/#6, 재매핑 규칙)는 **server.Server 단독 조회**(CPU/메모리 없음)뿐.
- **둘을 결합한 예시·지침이 전무**했다.

그래서 LLM이 피벗에 `WHERE c.name = '###'`를 그대로 붙이면, 그 술어가 **server.Server 행에만
참**이라 `GROUP BY` 전에 server.Cpus/server.Memory 행이 제거되어 **CPU·메모리 CASE 집계가
NULL**이 된다(OS/IP/호스트명은 살아남은 server.Server 행에서 정상). 관측 증상과 정확히 일치.

avail_status 필터는 이미 **HAVING(집계 후 server.Server 행 기준)** 기법을 가르치는데
(`query_generator.py`), **서버명/호스트명 필터에는 그 기법이 없던 것**이 구멍이었다.

### 결정

서버 식별 필터를 EAV 피벗과 함께 쓸 때는 **WHERE가 아니라 HAVING**(집계 후 server.Server 행
기준)으로 적용한다(WHERE는 resource_type/dtime 등 행집합 한정용으로만). avail_status와 동일 기법.

```sql
WHERE c.resource_type IN ('server.Server','server.Cpus','server.Memory') AND c.dtime IS NULL
GROUP BY COALESCE(c.platform_resource_id, c.id)
HAVING MAX(CASE WHEN c.resource_type = 'server.Server' THEN c.name END) = '조회할_장비명'
-- "호스트명이 XXX인"으로 명시한 경우에만 c.name → c.hostname
```

### 구현

- `prompts/query_generator.py`: filter_conditions 섹션에 "서버명/호스트명 필터 + GROUP BY 피벗
  동시 적용 시 반드시 HAVING" 규칙·예시 추가(WHERE에 두면 CPU/메모리 NULL이 되는 이유 명시).
- `config/db_profiles/polestar_cm_gp.yaml`·`polestar_cm_yd.yaml`: 단일 서버 + OS/IP/호스트명 +
  CPU/메모리를 한 번에 조회하는 query_example 신규(HAVING 패턴, 잘못된 WHERE 주의 주석 포함).

### 근거 / 대안

- HAVING 채택: 이 코드베이스가 이미 avail_status에 동일 기법을 쓰므로 일관적이고, 구체
  query_example이 LLM에 가장 강한 신호. 대안 — `platform_resource_id` IN (서버 식별 서브쿼리):
  동등하나 예시가 길고 기존 패턴과 덜 일관적이라 보조로만 언급.
- 한계: 쿼리 생성은 LLM 의존이라 100% 보장은 아니다. 구체 예시 + 명시 규칙 + 오류 사유 설명으로
  신뢰도를 높였고, query_validator/result 단계가 후속 안전망. 필드가 정말 다른 DB에 있는 경우는
  본 결정 범위 밖(field_mapper/DB 선택 영역).
- 관련: D-063(이 사례를 "속성 부재"로 오판했던 가드 — 본 결정이 진짜 원인), D-046(폴스타 EAV/
  hostname 해소), CLAUDE.md Known Mistakes(OSParameter LOB — 그건 LOB 값 컬럼 이슈, 본 건은
  교차 행 피벗 이슈로 별개).
- 검증: 두 YAML safe_load OK, arch_check --ci exit 0, 전체 회귀 통과(사전 실패 3건은 본 변경과
  무관 — git stash 대조 확인).

---

## D-051. allowed_tables 유사어 동적 보완을 질의 매칭분으로 게이트 (b0 토큰 폭증 차단)

| 항목 | 내용 |
|------|------|
| **결정일** | 2026-06-30 |
| **상태** | 확정 (1차 구현 완료 — schema_analyzer 게이트) |

### 배경 / 문제

"은행 폴스타(polestar_b0)의 ### 서버 IP/OS/CPU/메모리" 단일 조회가 4개 task 전부
`Input tokens must be <= 95232. Given: 197951`로 실패. 이 오류는 vLLM 제어 평면이 아니라
**FabriX(KBGenAI) 데이터 평면 백엔드(GptOss, 입력 ~95K 한도)** 가 던진 것으로(D-042의
"데이터 평면=대용량" 전제는 실측과 다름), SQL 생성 프롬프트가 한도의 2배로 팽창했다.

VS Code 디버거 단계별 실측으로 원인을 확정:
- `schema_analyzer.py`에서 LLM 테이블 선택 직후 `relevant`=4 (전체-폴백 미발동), query_targets 정상.
- 그러나 **최종 relevant=400, `_allowed`=407** (b0 프로필 `allowed_tables`는 5개뿐).
- `query_generator` 직전 `system_prompt`=104,632 토큰(>95K), `column_synonyms`=72K·`schema_info`=134K 토큰.

근본 원인: `schema_analyzer`의 allowed_tables 동적 보완이 **`cache_mgr.get_synonyms(db_id)`의
모든 col_key 테이블을 무조건 `_allowed`에 추가**(`:756-762`)하고 Step2가 이를 전량 relevant로
보충(`:781-789`). b0는 거의 전 테이블에 컬럼 유사어가 누적돼 있어 5개 화이트리스트가 407개
(전체 409 거의 전부)로 부풀고, 그 스키마가 통째로 프롬프트에 덤프됐다. **DB2라서가 아니라
유사어가 가장 많이 누적된 DB**여서이며, 유사어가 늘수록 악화되는 시한폭탄형 버그.
(Known Mistakes 2026-06-11 "사전류 전 테이블 순회 시 매칭+상한 필수"의 재발.)

### 결정

allowed_tables 유사어 동적 보완을 **이번 질의(원질의 + query_targets)에 실제 등장한 유사어의
테이블만**으로 게이트하고 보완 수 상한(`_MAX_SYNONYM_SUPPLEMENT_TABLES=15`)을 적용한다.
`schema_analyzer._synonym_tables_matching_query()` 헬퍼 신설(빈 문자열·1글자 유사어 제외로
전체 매칭 함정 차단). 이로써 b0 relevant이 5개 수준으로 수렴해 프롬프트가 ~5-10K로 떨어진다.

**보조 방어선**(Plan 57, 후속): FabriX 데이터 평면 토큰 가드(`WORKER_MAX_INPUT_TOKENS` +
노이즈 우선 제거, D-042 전제 정정), schema_analyzer 전체-테이블 폴백 제거(P1),
DB2 introspection 앱 스키마 스코프(부차), replanner 인프라성 에러 가드(D-052 예정).

### 근거 / 대안

- **게이트 채택 이유**: 유사어 매핑 보존이라는 원 취지를 살리되 질의 관련분만 포함 → 누적과
  무관하게 relevant 경계 유지. 회귀 안전(다른 DB도 질의 매칭분만 추가).
- **대안(전면 제거)**: 프로필 화이트리스트만 권위. 더 단순하나, 비-화이트리스트 테이블을
  유사어로 매핑해 쓰는 DB가 있으면 깨질 수 있어 보류.

### 검증

- `tests/test_nodes/test_schema_analyzer_synonym_gate.py`(8) + `test_schema_analyzer_eav_supplement.py`
  통합 양성/음성(유사어 미매칭 시 테이블 미보완) — 15 passed. arch_check 영향 없음(WARN 2건은
  기존 orchestration→prompts, 본 변경 무관). 상세: `plans/57-polestar-b0-token-overflow-and-replan-misdiagnosis.md` §1.5/§4.★.

---

## D-053. hostname 해소 SQL 엔진 인지(은행 레거시 b0 DB2 프로세스 조회)

| 항목 | 내용 |
|------|------|
| **결정일** | 2026-06-30 |
| **상태** | 확정 |
| **관련 결정** | D-046(서버명→hostname 해소) 보강 — 충돌 없음 |

### 배경 / 문제

은행 레거시 폴스타(`polestar_b0`)의 실시간 프로세스 조회가 0건으로 나오고, 빈 결과가
replanner를 깨워 후속 회차에서 일반 DB 조회로 폴백됐다. 김포/여의도(gp/yd)는 정상.
근본 원인은 D-046의 `build_hostname_sql`이 **엔진 무관하게 PostgreSQL 방언으로 고정**돼 있던 것:
(1) `LIMIT 1` — DB2(b0)는 미지원, (2) `polestar.cmm_resource` 스키마 prefix 고정 — b0(DB2)는
연결 계정 CURRENT SCHEMA를 쓰며 b0 프로필도 무스키마 `cmm_resource`를 참조. b0에서 이 SQL이
실패하면 `resolve()`가 예외를 삼키고 None을 반환(D-046 graceful 폴백) → 원시 **서버명**이
hostname으로 프로세스 API에 전달 → b0 API 0건. gp/yd는 PostgreSQL이라 동일 SQL이 성공해
정규 hostname을 얻으므로 정상 동작했다.

### 결정

`build_hostname_sql`을 **엔진 인지**로 만든다. `resolve()`가 `get_domain_by_id(db_id).db_engine`로
대상 엔진을 조회해 전달:
- **db2**(b0/polestar): `FETCH FIRST 1 ROWS ONLY` + 무스키마 `cmm_resource`(CURRENT SCHEMA 해소).
- **postgresql**(gp/yd, 기본): 기존 `LIMIT 1` + `polestar.` 스키마 유지(회귀 없음).

진단성: 해소 실패 시 WARNING 로그에 `engine`·실패 SQL을 함께 남겨 방언/스키마 미스매치를
테스트 중 식별 가능하게 한다(폴백 동작 자체는 불변).

### 세부 변경

- `src/alarm/infrastructure/polestar_hostname_resolver.py`: `_table(db_id, name, db_engine)` /
  `build_hostname_sql(db_id, value, db_engine)` 엔진 분기, `resolve()`가 엔진 조회·전달,
  실패 로그에 engine·sql 추가. `get_domain_by_id` import(same-layer: routing↔alarm.infrastructure).
- `src/orchestration/process_query.py`: **(라이브 진단으로 드러난 실제 1차 게이트)** `_resolve_db_id`의
  `_LOCATION_DB_HINTS`에 김포/여의도만 있고 **은행(b0)이 누락**돼 "은행 폴스타 … 프로세스" 질의의
  db_id가 None으로 빠짐(첫 턴은 task.db_ids/previous 공백 → ③ 위치 힌트로 와야 하는데 b0 힌트 부재).
  → b0 힌트(`"은행"/"레거시"/"은행존"`) 추가. 위치 신호가 명확하면 base_url 미매핑이어도 db_id를
  반환하도록 ③ 보강(잘못된 "위치 미식별" 대신 정확한 "API 미연결" 안내). 진입/게이트 진단 로그 추가.
- `src/alarm/infrastructure/polestar_process_api.py`: 200이지만 `data.list` 경로가 없을 때 응답
  envelope(top_keys·body 일부)를 WARNING 로깅(인스턴스별 형식 차이 진단). b0 응답 형식은 gp/yd와
  동일함이 라이브 확인됨(`{date, data.list, id}`) — 형식 mismatch 아님.
- `src/orchestration/result_aggregator.py`: **(멀티턴 후속 질의 펑 — 추가 근본원인)** orchestration
  경로의 `agent_orchestrator`는 최종 state로 `{task_plan, task_results, current_node}`만 반환하고
  subagent가 세팅한 `active_db_id`/`target_databases`를 top-level로 올리지 않는다. 그래서 다음 턴
  `context_resolver._extract_previous_db_ids`가 읽을 값이 없어 **previous_db_ids가 비고**, "해당 서버
  …" 후속 process_query가 db_id=None으로 펑(위치어가 없어 ③ 힌트로도 못 잡음). query_results는
  이미 승격돼 previous_entities(hostname)는 살아남던 비대칭이 단서. → `_collect_db_promotion`이
  실행 task들의 `target_db_ids`를 `active_db_id`(첫 DB)·`target_databases`([{db_id}])로 승격(3개 반환
  경로 모두). 1·2턴 모두에서 b0 DB 승계 복원.
- `tests/test_orchestration/test_process_hostname_resolve.py`: b0(db2) FETCH FIRST·무스키마,
  postgres 기본 회귀, resolve가 b0에서 db2 방언 실행, b0 위치 힌트(은행/레거시/base_url 무관)·김포 회귀 — 7건 신규.

### 향후 수정 시 고려사항

- b0 CURRENT SCHEMA가 cmm_resource를 해소하지 못하면(WARNING 로그 SQLCODE -204) `_SCHEMA_BY_DB_ID`에
  b0 실제 스키마를 등록한다.
- `polestar_history.py`도 동일한 `_DEFAULT_SCHEMA='polestar'`+`LIMIT` 패턴이라 b0 알람 이력 보강에서
  같은 방언 불일치 잠재. 본 결정은 프로세스 조회 경로에 한정했으며 history는 후속 과제로 남긴다.

### 검증

- `tests/test_orchestration/test_process_hostname_resolve.py` 14 passed,
  `test_alarm_process_enrich.py`·`test_alarm_process_rank.py` 63 passed, arch_check --ci exit 0.

---

## D-054. 레거시 단일 `polestar` 도메인 폐기 + db_profiles 정리 + 면책 문구 환각 차단

| 항목 | 내용 |
|------|------|
| **결정일** | 2026-06-30 |
| **상태** | 확정 |
| **관련 결정** | D-004(도메인 구성), D-053(b0 엔진 인지) 후속 — 충돌 없음 |

### 배경 / 문제

(1) `config/db_profiles/`에 운영 3종(`polestar_b0`/`polestar_cm_gp`/`polestar_cm_yd`) 외에
불필요 파일이 잔존했다: `test_db.yaml`(빈 auto 스텁), `unknown.yaml`(auto, db_id 미사용),
`polestar_pg.yaml`(도메인 미등록·테스트 전용), `polestar.yaml`(레거시 단일 폴스타 — b0로 대체됨).
(2) `output_generator` LLM이 조회 결과 말미에 "현재 조회된 데이터에 avail_status 컬럼이 없으므로
정상·비정상 여부를 판단할 수 없습니다 …" 같은 **묻지 않은 면책 문구를 자발적으로 환각**했다
(코드에 해당 문자열 없음 — 순수 LLM 생성).

### 결정

- **레거시 `polestar` 도메인 폐기**: `DB_DOMAINS`에서 `db_id="polestar"` 엔트리 제거(7→6).
  b0가 "은행 레거시"를 승계하므로 기능 손실 없음. 바 별칭("폴스타"/"polestar")은 이제 특정
  DB로 직결되지 않고 LLM 시멘틱 라우팅이 b0/gp/yd 중 선택(3 인스턴스 환경에서 "폴스타"는 본질적 모호).
- **db_profiles 정리**: 위 4개 파일 삭제. `_load_manual_profile`은 파일명=db_id 규칙이라 미등록
  db_id 파일은 어차피 미로드(영향 없음). `polestar_pg` 참조 테스트는 모두 `pytest.skip` graceful 처리.
- **면책 문구 차단**: `OUTPUT_GENERATOR_SYSTEM_PROMPT`에 규칙 6 추가 — 사용자가 가용성/상태를
  묻지 않았다면 특정 컬럼(avail_status 등) 부재 언급·면책·안내 문구를 추가하지 말 것.

### 세부 변경

- `config/db_profiles/{test_db,unknown,polestar_pg,polestar}.yaml` 삭제.
- `src/routing/domain_config.py`: `polestar` DBDomainConfig 엔트리 제거.
- `src/prompts/output_generator.py`: 면책 문구 금지 규칙 6 추가.
- `src/api/routes/alarm.py`: `AlarmTestRequest.db_id` 기본값 `"polestar"`→`"polestar_b0"`.
- `src/prompts/semantic_router.py`·`cache_management.py`: few-shot 예시 db_id `"polestar"`→`"polestar_b0"`,
  사용자 직접지정 예시 입력을 "은행 폴스타"로 정합화(존재하지 않는 db_id 예시 제거).
- `.env.example`: `POLESTAR_DB_IDS`·`ACTIVE_DB_IDS` 예시를 b0/gp/yd로 갱신.
- 테스트: `test_domain_config.py`(6개·polestar_b0 기준), `test_structure_analysis.py`
  (`test_polestar_db_engine`→polestar_b0) 갱신.

### 남겨둔 항목 (의도)

- `src/nodes/field_mapper.py`의 `if db_id_lower == "polestar"` 우선순위 분기: active_db_ids 루프
  내부라 polestar 미활성 시 자연히 비활성(무해 dead branch). 라우팅 우선순위 로직 변경 리스크를
  피하기 위해 그대로 둠.
- `tests/test_document/*`의 `db_id="polestar"` 픽스처: 도메인 등록과 무관한 단순 예시 키 문자열.
- `redis/export/*.json`(polestar_schema 등): 마이그레이션 스냅샷 산출물(런타임 프로필 아님), 범위 외.

### 향후 수정 시 고려사항

- 레거시 DB2 단일 `polestar`를 다시 붙일 일이 생기면 도메인 엔트리·프로필을 재등록한다
  (b0와 별도 인스턴스인 경우에 한함).

### 검증

- `test_semantic_routing/test_domain_config.py`·`test_structure_analysis.py`·
  `test_plan32_manual_profile.py`: 93 passed, 7 skipped. (기존 실패 1건
  `TestSaveStructureProfile::test_yaml_file_created`은 본 변경과 무관 — HEAD에서도 실패).

---

## D-055. 후속 턴 "해당 서버" 지시어 hostname 오추출 차단 + 결과 요약 유지 정정

| 항목 | 내용 |
|------|------|
| **결정일** | 2026-07-01 |
| **상태** | 확정 |
| **관련 결정** | D-046(서버명→hostname 해소), D-047(멀티턴 승계), D-053(멀티턴 db_id 승격), D-054(면책 문구 차단) 후속 — 충돌 없음 |

### 배경 / 문제

(1) **멀티턴 hostname 오추출**: 후속 턴 "해당 서버의 프로세스 리스트를 확인해줘"에서 프로세스
조회 서버가 `previous_server`(영문 플레이스홀더)로 들어가 0건. 원인은 `input_parser` 규칙 14가
"특정 서버 지목 시 hostname 추출"을 강제하는데, **지시어("해당 서버")도 서버 지목으로 보고 hostname
필터를 만들며 LLM이 값으로 플레이스홀더를 지어냄**. 이 이번-턴 필터가 `process_query._resolve_hostname`
①(이번 턴 filter)에서 ②(previous_entities, 직전 실제 서버)보다 **우선순위가 높아** 잘못된 값이 채택됨.
D-053이 db_id 승계를 복원했어도 hostname 신호가 이 지점에서 오염되어 0건 재발.

(2) **결과 요약 소실**: D-054 규칙 6(면책 문구 차단)을 LLM이 과잉 적용해, 특정 서버 정보 조회 시
avail_status 환각 문구뿐 아니라 **정상적인 1~2문장 요약·이상 징후 분석까지 통째로 생략**함.

### 결정

- **결정적 지시어 가드**(주 방어): `process_query._is_demonstrative_value()`로 이번 턴 filter 값이
  지시어(해당/그/이/저/위/직전 등 + 서버/장비 등)·영문 플레이스홀더(previous/prev + server/host)이면
  hostname으로 인정하지 않고 `previous_entities`로 폴백. LLM 비결정성에 의존하지 않는 결정적 교정
  (Known Mistakes 원칙: LLM 분류 의존 의도는 결정적 가드로 후처리). ①·② 양쪽에 적용.
- **input_parser 규칙 14 보강**(부 방어): 지시어만으로 지목한 경우 hostname filter를 만들지 말고
  filter_conditions를 비워 두도록 명시(+플레이스홀더 임의 생성 금지). 예시 추가.
- **output_generator 규칙 6 정정**: 규칙 6은 "데이터에 없는 컬럼에 대한 면책·안내"만 금지하며,
  조회된 데이터 자체의 요약·이상 징후 분석(규칙 2·5)은 생략하지 말 것을 명시.

### 세부 변경

- `src/orchestration/process_query.py`: `_is_demonstrative_value()` 신규, `_resolve_hostname` ①·②에 가드 적용,
  `_DEMONSTRATIVE_PREFIXES`/`_DEMONSTRATIVE_NOUNS` 상수 추가.
- `src/prompts/input_parser.py`: 규칙 14에 지시어 미추출 지침·예시 추가.
- `src/prompts/output_generator.py`: 규칙 6에 "요약·분석은 유지" 단서 추가.
- 테스트: `test_process_hostname_resolve.py`에 `TestResolveHostnameDemonstrative`(가드 단위 3건).

### 검증

- `test_orchestration/` 141 passed/4 skipped, `arch_check --ci` exit 0.

---

## D-056. 멀티턴 후속 판단형 질의에 직전 턴 답변 전파 (Approach A)

| 항목 | 내용 |
|------|------|
| **결정일** | 2026-07-01 |
| **상태** | 확정 (1단계 A — ③ rows 보존/intent_planner 분석 라우팅은 2단계 유보) |
| **관련 결정** | D-041(M3 압축 맥락 보존), D-047(query_results 승격), D-062(합성), D-053(db_id 승격), D-055(지시어 해소) 후속 — 충돌 없음 |

### 배경 / 문제

멀티턴 3턴 시나리오에서 판단형 후속 질의가 직전 턴 데이터를 활용하지 못하고 거부:
- 턴1 "은행 ### 서버 스펙"(정상) → 턴2 "해당 서버 프로세스 리스트/분석"(정상) →
  턴3 "위와 같은 상태면 rightsizing 해도 되는가?" → **"구체적 모니터링 데이터가 확보되지
  않아 판단할 수 없다"** 며 거부(환각).

코드 기반 진단 결과 근본 원인(활성 경로 = orchestration, Track A):
1. **어시스턴트 답변이 `messages`에 누적되지 않음** — `final_response`를 AIMessage로
   messages에 append하는 노드가 `synonym_registrar` 하나뿐. 대화 이력엔 HumanMessage(질문)만 쌓임.
2. **격리 입력이 이력을 통째로 삭제** — `subagents._make_isolated_input`이 `"messages": []`로
   설정 → subagent(특히 general_inference)가 직전 답변을 못 봄. general_inference의
   `prior_messages[-10:]` 멀티턴 코드가 orchestration에서 무력화.
3. 판단형 질의는 intent_planner에서 `general_inference`(fallback)로 분류되는데, 이 노드가
   `conversation_context`(압축 신호)를 읽지도 않음.

### 결정 (Approach A = ①②④, 최소 침습·결정적)

- **② 답변 이력 누적**: 경로별 **단일 종료 노드**에서만 최종 답변을 AIMessage로 messages에
  append. orchestration=`result_aggregator._with_answer_history`(유일 top-level finalizer),
  직접/레거시 경로=`output_generator`(래퍼)·`general_inference`·`error_response`.
  subagent 반환 messages는 task_results에만 담겨 top-level로 승격 안 됨(D-053 비대칭) → 이중 누적 없음.
- **① 이력 선별 전달**: `SubAgentSpec.needs_history` 플래그 신설, `general_inference`만 True.
  `_make_isolated_input`이 needs_history agent에만 트리밍 이력(`_history_for_agent`, 상한
  `_MAX_HISTORY_MESSAGES=10`, 말미 현재 턴 질의 제외) 전달. 데이터 조회 agent는 `[]` 유지(격리 —
  이력이 SQL 생성에 불필요·토큰 부담·오염 위험).
- **④ 판단 그라운딩**: general_inference가 후속 턴(turn_count>1)에 `conversation_context`를
  판단 근거 블록으로 그라운딩 + `_JUDGMENT_GUIDANCE`(긍정 지시) 부착 — "대화에 이미 제공된
  데이터가 있으면 확보 안 됐다고 단정해 거부하지 말고, 확보 데이터로 판단 가능 범위를 답한 뒤
  추가 필요 데이터만 짚으라". D-055 교훈(부정 지시엔 유지할 정상 동작 명시)과 정합.

### 유보 (2단계, 필요 시 증설)

- **③ 이전 턴 rows 소량 보존**: 정성 판단은 A로 충분한지 관찰 후, 정량 근거가 부족하면
  rows를 planner엔 넣지 않고 general_inference에만 상한(상위 N행×핵심 컬럼)으로 전달 +
  "직전 조회 시점" 라벨 + 본 결정 개정. (95K 토큰 민감 구간·D-041 "대량 보존 금지" 완화라 신중.)
- **intent_planner 분석/판단 후속 라우팅 규칙**: LLM 라우팅 변경은 오분류 리스크 대비 이득이
  작아 후순위. general_inference 그라운딩(④)으로 대부분 해결.

### 세부 변경

- `src/orchestration/result_aggregator.py`: `_with_answer_history()` 신규, 3개 반환 분기 래핑.
- `src/orchestration/subagents.py`: `SubAgentSpec.needs_history` 필드, general_inference=True,
  `_history_for_agent()` 신규, `_make_isolated_input`의 `"messages"` 선별 주입, `_MAX_HISTORY_MESSAGES`.
- `src/nodes/general_inference.py`: `_JUDGMENT_GUIDANCE` 상수, `_build_context_grounding()` 신규,
  `_build_system_prompt` 후속 턴 addendum 부착, 반환에 AIMessage messages 누적.
- `src/nodes/output_generator.py`: `output_generator`를 얇은 래퍼로 분리(`_run_output_generator`),
  final_response를 messages로 누적.
- `src/graph.py`: `_error_response_node`가 답변을 messages로 누적(AIMessage import 추가).
- 테스트: `tests/test_multiturn/test_answer_history_propagation.py`(①②④ 11건).

### 후속 보강 (같은 날, 엔티티 승계 sticky)

**증상**: A 적용 후 턴3(판단, general_inference)은 정상이나, 다음 턴4 "해당 서버의 최근 1개월
CPU/메모리 사용률…"이 특정 서버가 아닌 **은행 폴스타 전체 서버**를 조회. **원인**: 판단 턴은 DB
조회를 안 해 `result_aggregator`가 top-level `query_results=[]`로 덮어씀(리듀서 없는 plain 필드)
→ 다음 턴 `context_resolver._extract_previous_entities`가 빈 결과·빈 filter에서 추출 →
`previous_entities=[]`로 hostname(=###) 신호 소실 → "해당 서버" 미해소로 필터 누락. **수정**:
`context_resolver`가 이번 턴 신규 추출이 비면 **직전 `conversation_context`의
`previous_entities`/`previous_db_ids`/`previous_location`을 승계(sticky)**. 이번 턴에 새 대상이
잡히면 그 값이 우선(오염 없음) — 조회 없는 분석 턴을 건너뛰어도 지시어가 유지된다. 회귀:
`test_context_resolver.py::TestContextResolverEntityStickiness` 2건.

**미해결(별건)**: b0(은행, DB2) 월간 사용률 통계가 소수점 없이 정수로 표시됨. gp/yd는
`ROUND(AVG(...)::numeric, 2)` 캐스트가 있으나 b0 query_guide는 `ROUND(AVG(...), 2)`로 캐스트
부재. 다운스트림(result_organizer/query_executor)엔 값 정수화 없음 확인 → SQL/DB2 레벨 이슈로
추정. DB2 캐스트 문법은 `cmm_metric_stat_m.{min,avg,max}_val` 실제 타입 확인 후 적용해야
SQL 에러(Known Mistakes 방언 교훈)를 피함 — 검증 전 미수정.

### 후속 보강 (같은 날, 미지원 역량 광고 차단 — 프롬프트 하드 제약)

**증상**: general_inference가 판단/분석 마무리에서 후속 조회를 제안할 때 **지원하지 않는 기능**
(예: "MySQL InnoDB Buffer Pool 설정 확인", DB 엔진 내부·앱 튜닝값)을 예시로 지어내 광고 →
사용자가 그걸 실제 요청하면 data_query로 라우팅돼 존재하지 않는 속성을 찾느라 재시도 낭비 후
"없음" 결론. **원인**: ④의 `_JUDGMENT_GUIDANCE`("추가로 필요한 데이터를 짚어라")가 제안을
지원 역량 목록(`_SUPPORTED_CAPABILITIES`)으로 **바운딩하지 않아** LLM이 목록 밖 항목을 자유
생성. 목록은 프롬프트에 있으나 "이 목록 안에서만 제안" 강제가 없어 그라운딩이 샜다(같은 턴에서
어떤 때는 정확, 어떤 때는 환각 — 일관성 부재가 증거). **수정(사용자 선택: 프롬프트 하드 제약)**:
`_JUDGMENT_GUIDANCE`와 general_inference 카탈로그 분기 [안내 규칙]에 "후속 제안·예시는 반드시
[지원 가능한 조회 유형] 목록 안에서만, 목록 밖 항목(DB 엔진 내부 설정·MySQL/InnoDB/버퍼풀·앱
파라미터·임의 IT 개념)은 예시로도 금지, 없는 기능을 있는 것처럼 안내 금지"를 명시. 소스 카탈로그가
없는 후속 턴에도 지원 목록을 함께 실어 제약의 참조 기준을 보장. 회귀: `test_answer_history_propagation.py::TestCapabilityScopeConstraint` 3건. **유보**: 미지원 요청의
data_query 재시도 낭비(2차 증상)는 스코프 게이트 필요 — 라우팅 변경 리스크로 후순위(사용자 결정).

### 후속 보강 3 (같은 날, 프로세스 결과 행의 엔티티 오수집 차단)

**증상**: 멀티턴 "프로세스 리스트 조회" 후 "해당 서버의 알람을 분석해줘"가 실제 서버(###)가
아닌 **프로세스명들**(name=mysql pid=30176, name=node_exporter …)을 "서버"로 삼아 알람을 조회.
**원인**: `_extract_previous_entities`가 결과 행의 식별 컬럼을 harvesting하는데, 프로세스 조회
결과 행(`_process_to_dict`: {name, pid, user, cpu_pct, …})의 `name`(프로세스명)이 `name` 힌트에,
`pid`가 `id` 힌트에 **부분매칭**되어 서버 엔티티로 오수집됨. "해당 서버 프로세스"는 지시어라
input_parser가 hostname filter를 안 만들어(D-055) filter 경유 서버 식별자도 없어, 그 턴의
previous_entities가 통째로 프로세스명으로 오염 → sticky 승계로 다음 턴까지 전파. **수정**:
`_looks_like_process_rows()`(첫 행에 `pid` 키 존재)로 프로세스 결과 행을 감지해 **결과 harvesting
에서 제외**(서버 식별자는 filter_conditions에서만 수집). 프로세스 행에는 hostname이 없어 손실
없음. 이로써 그 턴 fresh 추출이 비면 후속 보강(sticky)이 직전 ctx의 hostname(###)을 승계 →
"해당 서버"가 서버로 올바로 해소. 회귀: `test_context_resolver.py::TestProcessRowEntityExclusion` 5건.

### 후속 보강 4 (같은 날, data/alarm_query 지시어 서버 필터 결정적 주입 — 근본)

**증상**: "해당 서버는 특이사항이 없는가?"(→alarm_query)가 특정 서버가 아닌 **전체 알람**을 조회.
data_query "해당 서버 CPU/메모리"의 전체-서버 조회도 동일 뿌리(앞선 sticky 수정은 필요조건일 뿐
불충분이었음). **근본 원인**: `query_generator._build_user_prompt`가 SQL 생성 입력으로
`parsed_requirements["original_query"]`(원본 질의)+`filter_conditions`만 사용하고, **intent_planner가
확장한 sub_query(`state["user_query"]`)는 프롬프트에 쓰지 않는다**. 지시어 후속은 (1)filter_conditions가
비어 있고(D-055, 지시어 hostname 미추출) (2)original_query엔 구체 서버명이 없어 → query_generator에
**hostname이 전혀 도달하지 않아** 서버 필터 없이 전체 조회(alarm은 Template C-2, data는 전체 서버).
process_query만 `_resolve_hostname` 결정적 해소를 갖고 data/alarm_query엔 없었음. **수정**:
`subagents._inject_demonstrative_hostname()` — data/alarm 파이프라인(run_data_query_pipeline)에서
original_query가 지시어로 특정 서버를 지목하고(`_refers_to_specific_server`, "전체/모든" 제외)
filter에 서버 식별자가 없으면, 직전 서버 hostname(`_resolve_hostname`: previous_entities, 지시어 가드)을
`filter_conditions`에 주입. concrete "webdb01 서버 알람"과 **동일한 filter_conditions.hostname 경로**
(이미 동작)로 서버 필터를 강제 → alarm C-6(SVR.NAME)·data 서버 필터가 걸린다. 전역 조회("전체 서버")·
concrete 질의는 미주입(스코프 오확대 없음). 회귀: `test_multiturn_propagation.py::TestDemonstrativeHostnameInjection` 6건.
※ 남은 가정: query_generator 알람 템플릿이 filter_conditions.hostname을 C-6(SVR.NAME)으로 매핑
(concrete 알람 경로와 동일) — 라이브 미검증 시 프롬프트 보강 여지.

### 검증

- 신규 11건 + `test_multiturn/`·`test_orchestration/{result_aggregator,subagents}` 123 passed.
  엔티티 승계·역량 제약·프로세스 행 제외·지시어 hostname 주입 후속 포함
  `test_multiturn/`·`test_orchestration/` 246 passed/4 skipped.
- `arch_check --ci` exit 0. 기존 무관 실패: `test_output_generator.py::TestBuildResponsePrompt`
  2건(코드에 없는 `sql` kwarg 기대 — 본 변경 전부터 실패, 범위 외).

---

## D-057. 폼필(멀티DB) SQL 생성의 엔진·스키마 인지 — b0(DB2) POLESTAR 스키마 한정

| 항목 | 내용 |
|------|------|
| **결정일** | 2026-07-02 |
| **상태** | 확정 |
| **관련 결정** | D-053(hostname 해소 SQL 엔진 인지) 확장 — 충돌 없음 |

### 배경 / 문제

파일 업로드 양식 채우기(멀티DB 경로 `multi_db_executor`)에서 은행존(`polestar_b0`, DB2)이
`SQL0204N "SDQ000.CMM_RESOURCE" is an undefined name`으로 실패. 엔진·스키마 인지 SQL 조립이
`polestar_hostname_resolver.build_hostname_sql`(D-053)에만 있고, **LLM 기반 일반 SQL 생성 경로
(`multi_db_executor`/`query_generator`)에는 이식되지 않아** b0 폼필이 무스키마 `cmm_resource`를
생성 → DB2가 연결 계정 CURRENT SCHEMA(`SDQ000`)로 해소 → 미존재. D-053이 "향후 고려사항"으로
예측한 시나리오(SQLCODE -204 → b0 실제 스키마 등록)가 폼필 경로에서 현실화. 실측(2026-07-02):
`SYSCAT.TABLES.TABSCHEMA='POLESTAR'`, `CURRENT SCHEMA=SDQ000`.

### 결정

테이블 스키마 한정을 **domain_config `db_schema`를 단일 출처**로 결정적으로 적용한다.
- `DBDomainConfig.db_schema` 필드 신설: gp/yd=`polestar`, **b0=`POLESTAR`**(DB2 대문자 식별자), 그 외 미설정.
- `src/routing/db_schema.py`(`get_schema_prefix`/`qualify_table`) — 접두사 계산을 한 곳에 집약.
- `multi_db_executor._generate_sql(db_id=...)`가 스키마 한정 규칙을 프롬프트에 **결정적 주입**
  (설정 시 `POLESTAR.테이블`, 미설정 시 무스키마 지침 + DB2면 FETCH FIRST 방언 명시).
- `polestar_hostname_resolver._table`도 `db_schema`를 우선 사용(하위호환 폴백 유지) → b0 hostname
  해소도 동시에 스키마 한정되어 D-053의 잠재 -204를 함께 해소.
- b0 프로필 `query_examples`를 `POLESTAR.` 한정으로 정합화(PostgreSQL식 `polestar.` 접두사 제거),
  query_guide에 DB2 스키마 규칙 명시.

### 세부 변경

- `src/routing/domain_config.py`: `db_schema` 필드 + gp/yd/b0 값.
- `src/routing/db_schema.py`(신규), `src/nodes/multi_db_executor.py`(`db_id` 인자·규칙 주입),
  `src/alarm/infrastructure/polestar_hostname_resolver.py`(`_table` db_schema 우선),
  `config/db_profiles/polestar_b0.yaml`(예시 POLESTAR 한정·가이드).
- `tests/test_routing/test_db_schema.py`(신규), `test_process_hostname_resolve.py`(b0 POLESTAR 한정으로 갱신).

### 방향성 정정 (2026-07-02 사용자 검토)

본 결정의 앱-레벨 명시 한정(db_schema=POLESTAR)은 **인-리포 안전망**으로 유지하되, **최선(운영)**은
mcp_server `.env`의 `POLESTAR_B0_CONNECTION`에 **`CURRENTSCHEMA=POLESTAR`를 추가**하는 것이다
(`...;UID=SDQ000;PWD=###;CURRENTSCHEMA=POLESTAR;`). 연결 시 CURRENT SCHEMA를 POLESTAR로 고정하면
무스키마 참조가 **전 경로**(LLM SQL·리졸버·`polestar_history`·alarm 등)에서 자동 해소되어, 본 결정이 배선하지
못한 경로까지 커버한다. 앱 `SET CURRENT SCHEMA` 앞단(플랜 B)은 DBHub 읽기전용(`_validate_sql_simple`이
비-SELECT 차단)이라 execute_sql 경로로는 불가 — 연결 초기화에 넣어야 하므로 사실상 연결 문자열과 수렴.
우선순위/액션 아이템: `plans/58-...md` §0.

### 검증

- 신규 helper/규칙 주입 테스트 + 갱신 resolver 테스트 통과. arch_check exit 0.
- **운영 확인 필요**: (권고) 연결 문자열 `CURRENTSCHEMA=POLESTAR` 반영 후 b0 폼필 재실행하여 `SQL0204N` 소멸 확인.

---

## D-058. 공동존(gp/yd) 서버 식별자 NULL 폴백 — COALESCE(name, hostname)

| 항목 | 내용 |
|------|------|
| **결정일** | 2026-07-02 |
| **상태** | **잠정(graceful degradation)** — H1/H2 확정은 D-057 해소 후 진단(사용자 검토: "057 먼저") |
| **관련 결정** | D-050(EAV 피벗 HAVING·null은 SQL 오류일 수 있음), D-057(선행), 2026-06-10 name≠hostname |

### 순서 조정 (2026-07-02 사용자 검토)

`COALESCE(name, hostname)`는 H1(피벗 SQL 오류)·H2(데이터 미채움) **양쪽에서 안전**하므로 임시 유지하되
**확정 수정 아님**. yd `name` NULL의 원인 판별은 파이프라인이 정상 동작(D-057 해소)해 실제 생성 SQL·데이터를
관찰할 수 있어야 정확하다(D-050 원칙). 057 해소 후 처리현황(D-039) 생성 SQL + `COUNT(*) FILTER(WHERE name
IS NOT NULL)`로 H1/H2 확정 → H1이면 피벗 SQL 교정(COALESCE로 덮지 말 것), H2면 COALESCE 확정.

### 배경 / 문제

여의도(`polestar_cm_yd`) 양식 채우기에서 데이터는 조회되나 `cmm_resource.name`(서버명)이 전부
NULL이라 개별 서버 식별 불가. 공동존 프로필은 "서버명=name, 기본 식별자"를 강제하는데(2026-06-10),
개발/스테이징 환경은 name이 미채워졌을 수 있음(H2) 또는 피벗 SQL 구성 오류일 수 있음(H1, D-050류).

### 결정

서버 식별 **출력 컬럼**을 항상 `COALESCE(cmm_resource.name, cmm_resource.hostname)`로 생성해
name이 비어도 식별 불가(전부 NULL)를 방지한다(gp/yd 공통). 값 필터도 `(r.name = 'X' OR r.hostname = 'X')`로
매칭 권장. name/hostname을 별도 컬럼으로 함께 요청받으면 각각 그대로 출력하되 식별 대표 컬럼은 COALESCE.
(H1/H2 판별을 위해 실행된 생성 SQL·`name IS NOT NULL` 카운트 확인을 권장 — D-050 원칙.)

### 세부 변경

- `config/db_profiles/polestar_cm_yd.yaml`·`polestar_cm_gp.yaml`: query_guide에 `[★ 서버 식별자 NULL 폴백]`
  절 신설, 대표 query_examples(서버 종합/목록/비정상/장비명 필터)의 server_name 출력을 COALESCE로 갱신.

### 검증

- YAML 로드·기존 문서/매핑 테스트 통과. **라이브 확인 권장**: 생성 SQL·데이터 카운트로 H1/H2 확정 후 폴백 효과 검증.

---

## D-059. 폼필 실패 시 침묵적 CSV 강등 금지 — 사유 노출

| 항목 | 내용 |
|------|------|
| **결정일** | 2026-07-02 |
| **상태** | 확정 |
| **관련 결정** | D-047/2026-06-26(결정적 진단 메시지를 일반 문구로 덮지 말 것) 정합 |

### 배경 / 문제

양식 채우기 불가 시 `output_generator._generate_document_file`이 `None`을 반환 → `output_file=None` →
API `has_file=False` → 프론트가 Excel 링크를 감추고 CSV만 노출. 사용자는 **왜 Excel이 안 나오는지**
알 수 없이 조용히 CSV로 강등됨(실패 원인 은닉 안티패턴).

### 결정

`_generate_document_file`이 실패 시 `{"reason": ...}`(매핑 없음/양식·파일 없음/채우기 오류)를 반환하고,
`output_generator`가 사유를 최종 응답에 **결정적으로 노출**("업로드하신 XLSX 양식을 채우지 못했습니다. 사유: …,
아래 CSV로 확인"). 성공 판별은 `file_bytes` 키 존재로. 데이터·매핑이 있으면 일부 NULL이어도 Excel은 생성
유지(`total_filled==0`도 bytes 반환) — `None` 반환은 실제 불가 시로 국한.

### 세부 변경

- `src/nodes/output_generator.py`: `_generate_document_file` 반환 규약 변경(성공=`file_bytes`, 실패=`reason`),
  호출부 성공 판별·사유 노출 메시지.
- `tests/test_nodes/test_output_generator.py`: 폴백 테스트를 사유 노출 검증으로 갱신.

### 검증

- 갱신 테스트 통과. arch_check exit 0.

---

## D-061. 은행존(b0) 서버명(등록명)·호스트명·IP 구분 — 직접 컬럼 사용

| 항목 | 내용 |
|------|------|
| **결정일** | 2026-07-08 |
| **상태** | 확정(라이브 실측 기반) |
| **관련 결정** | D-058(공동존 서버 식별자), 2026-06-10(공동존 name≠hostname) |

### 배경 / 문제

은행존(`polestar_b0`) 양식 채우기에서 "서버 이름"과 "호스트네임" 칼럼이 **둘 다 등록명(cmm_resource.name)**
으로 출력됨. 원인: b0 프로필에 gp/yd 같은 name/hostname 구분(`column_synonyms`)이 없고, EAV `Hostname`
속성의 synonyms가 **"서버명"을 포함**해 서버명↔호스트명을 혼동.

### 라이브 실측 (2026-07-08, `b0_query.py` 진단 스크립트)

- `cmm_resource.name` = 폴스타 등록명. 대개 `"<호스트명> (<설명>)"` 구조(**호스트명과 괄호 사이 공백은 있을
  수도/없을 수도** 있음, 예: `fccsttso065(콜센터 시각화 #3)`, `sicwso01 (이미지 업무 WAS#1)`).
  **단, 설명 없이 `name == hostname`으로 등록된 서버도 상당수** 존재.
- `cmm_resource.hostname` 직접 컬럼 = **클린 호스트명**(예: `sicwso01`).
- `cmm_resource.ipaddress` 직접 컬럼 = **클린 IP**(EAV IPaddress와 동일 값 확인).
- (gp/yd는 EAV Hostname/IPaddress가 비어 있으나, b0는 EAV에도 값이 있음 — 그래도 직접 컬럼이 단순.)

### 결정

b0도 서버명(name) ≠ 호스트명(hostname)을 구분한다.
- `column_synonyms` 추가: `서버명/서버 이름/장비명 → cmm_resource.name`, `호스트명/호스트네임 → cmm_resource.hostname`.
- EAV `Hostname` synonyms에서 **"서버명" 제거**(혼동 차단).
- **호스트명·IP 출력은 직접 컬럼(`r.hostname`/`r.ipaddress`)** 사용. **name에서 호스트명을 파싱하지 말 것** —
  `name == hostname` 서버가 있고 `"<host> (<desc>)"` 구조도 불규칙(공백 유무)이라 파싱은 취약하며, 직접
  컬럼이 항상 클린이다.
- **주의(회귀 오판 금지)**: `name == hostname`으로 등록된 서버는 "서버 이름"과 "호스트네임" 칼럼이 **같은 값**
  으로 나오는 것이 정상이다(버그 아님).

### 경로 독립성

`column_synonyms`(서버명→name, 호스트명→hostname)는 field_mapper 단계에서 적용되므로 **LLM 생성 경로와
D-038 결정적 빌더(`_ALLOWED_DIRECT_COLUMNS={name,hostname,ipaddress,...}`) 양쪽 모두** 올바르게 분리된다.
query_guide/예시는 LLM 경로 보강용.

### 세부 변경

- `config/db_profiles/polestar_b0.yaml`: EAV Hostname synonyms 정리, `column_synonyms` 신설,
  query_guide `[★ 은행존 전용 — 서버명(등록명)과 호스트명 구분]` 절, 종합 조회 예시에 `server_name`(name)·
  `hostname`(직접)·`ipaddress`(직접) 구분 시연. (코드 변경 없음 — 프로필 단일 파일.)

---

## D-062. 딥 에이전트 경로 복합 결과의 단일 LLM 합성 (모순 이중 답변 제거)

| 항목 | 내용 |
|------|------|
| **결정일** | 2026-06-29 |
| **상태** | 확정 |

### 배경 / 문제

딥 에이전트(트랙 B) 경로에서 오케스트레이터(vLLM/Gemini)가 동일 질문을 **재시도**(도구
재호출)하면, `deepagents_tools._run_subagent_tool`이 매 호출마다 새 고유 `task_id`로
collector에 `(task, result)`를 append한다. 1차가 "부분 성공"(예: 서버는 찾았으나 CPU/메모리
컬럼 null — `error` 없음 → `status="completed"`)이고 2차가 완전 성공이면, **두 결과 모두
`completed`** 상태로 남는다. `_aggregate_with_fabrix`가 collector 전체를 `task_plan`/
`task_results`로 재구성 → `result_aggregator._merge_finalized`가 1·2차 텍스트를 `"\n\n".join`
으로 이어붙여 **"CPU 없음" 서술과 "CPU 8코어" 서술이 한 말풍선에 혼재**, 사용자 혼란 유발.

D-043(supersedes)은 이 모순을 막기 위한 기존 장치이나, (a) `_run_subagent_tool`이 task에
`supersedes`를 설정하지 않고 (b) 이를 설정하는 replanner는 딥 에이전트 경로에 배선되지
않으며 (c) **1차가 error가 아닌 "부분 성공"**이라 failed→success 매칭으로도 못 잡는다.

### 결정

딥 에이전트 경로의 복합 결과는 deterministic 이어붙이기 대신 **LLM 1회 호출로 단일 일관
답변을 합성**한다(사용자 결정: "단일 LLM 합성"). 재시도 모순(없음→있음)과 보강 결과를
모두 하나의 답변으로 통합하므로, "같은 질문 재시도 vs 서로 다른 질문(멀티의도)"을 휴리스틱
으로 구분할 필요가 없다(동일 agent로 서로 다른 대상을 조회하는 멀티의도 회귀 위험 회피).

- `result_aggregator(..., synthesize=True)`: 복합 task일 때 `_synthesize_finalized`로 합성.
  단일 task는 기존대로 통과(스트리밍 유지).
- **적용 경로 — 둘 다**: (1) 딥 에이전트(트랙 B) `_aggregate_with_fabrix`, (2) **다중 의도
  오케스트레이션(트랙 A, Plan 48) `result_aggregator` 그래프 노드**. 폐쇄망(deepagents
  미설치)에서 실제 활성 경로는 (2)이므로 `graph.py`에서 이 노드를 `synthesize=True`로
  바인딩해야 합성이 실제로 동작한다. (1)만 적용하면 사용자 런타임에서 미발동(최초 누락 원인).
  이로써 트랙 A의 복합 task는 기존 deterministic 병합(_merge_finalized)을 LLM 합성으로
  대체한다(D-005/D-043은 supersedes 사전 제외 단계로 여전히 유효, 병합 방식만 변경).
- **스트리밍 정합(D-009)**: 합성 모드 + 복합 task일 때 per-task `output_generator`는
  `stream_user_response=False`로 호출해 USER_RESPONSE_TAG를 떼고(중간 토큰 누출 방지),
  **최종 합성 1회만** USER_RESPONSE_TAG로 토큰 스트리밍. 딥 에이전트 그래프 노드는
  `deep_agent`라 SSE 핸들러가 태그로만 토큰을 거르므로(노드명 매칭 아님) 단일 스트림이 된다.
- **안전 폴백**: 합성 LLM 호출이 예외/빈 결과면 `_merge_finalized`(이어붙이기)로 폴백.
- output_file은 첫 산출 task의 것을 채택, `query_results`는 전체 누적(CSV/row_count, D-047).

### 구현

- `prompts/result_synthesizer.py` 신규: `RESULT_SYNTHESIZER_SYSTEM_PROMPT`(모순은 구체적
  값으로 해소, 내부 라벨 비노출, 구체 데이터 보존, 미확인 항목만 "확인되지 않음").
- `nodes/output_generator.py`: `stream_user_response: bool = True` 파라미터 추가 →
  `_generate_text_response`가 태그 부여 여부 결정.
- `orchestration/result_aggregator.py`: `synthesize` 파라미터, `suppress_stream` 계산,
  `_finalize_task(stream_user_response=...)`, `_synthesize_finalized` 신규.
- `orchestration/deep_agent.py::_aggregate_with_fabrix`: `synthesize=True` 전달(트랙 B).
- `graph.py`: 트랙 A(`enable_deepagent_orchestration and not use_deep_agent`)의
  `result_aggregator` 노드를 `partial(..., synthesize=True)`로 바인딩(실제 활성 경로 — 필수).

### 근거 / 대안

- 대안(기각) supersedes 휴리스틱: 1차가 error가 아닌 "부분 성공"이라 failed→success 매칭이
  안 되고, "동일 agent+둘 다 completed"를 합치면 멀티의도(같은 agent·다른 대상)에서 앞
  답변이 사라지는 더 나쁜 회귀. 대안(기각) 오케스트레이터 명시 태깅: 도구 스키마 변경 +
  LLM 신뢰 의존. 사용자 결정으로 단일 합성 채택.
- 관련: D-005(부분 실패 병합), D-009(SSE 토큰 스트리밍), D-043(supersedes — replanner 경로
  한정), D-047(query_results 승격).
- 검증: arch_check --ci exit 0(pre-existing WARN orchestration→prompts만), result_aggregator
  합성 신규 6건 + deep_agent wiring synthesize 검증 포함 통과.

---

## D-063. 엔티티 확보 후 무의미 재시도 차단 (replanner 필드-null 재조회 가드)

| 항목 | 내용 |
|------|------|
| **결정일** | 2026-06-29 |
| **상태** | 확정(가드). **단, 이 사례의 진짜 원인은 조회 SQL 오류 — 근본 수정은 D-050**. 본 가드는 "필드 null=속성 부재"를 단정하지 않으며, 동일 쿼리 반복 재시도(무익) 방지용 효율 가드로만 유효 |

### 배경 / 문제

"김포 ### 서버의 OS·IP·호스트명·CPU·메모리" 단일 질의에서, 1차 task(t1)가 **서버는 1건
찾았으나 CPU·메모리 컬럼이 null**이었다. (당초 "속성 미수집"으로 추정했으나 — **오판**.
실제로는 데이터가 존재하는데 EAV 피벗+서버명 필터 SQL이 잘못 생성되어 못 가져온 것이었다.
근본 원인·수정은 **D-050**.) `replanner`의 LLM이 이를 프롬프트의 "0건 → 재조회" 예시에
잘못 적용해 "불충분 → 재조회"로 판단, DB 범위를 바꿔가며(gp→전체 소스→gp→전체)
**`max_replan`(3)까지 헛 재시도**했다(t1~t4). 결과:
(1) 불필요한 DB 왕복 4회, (2) 각 시도가 부분/빈 답변을 내고 `_merge_finalized`가 모두
이어붙여 "1건 확인" + "데이터 없음"의 **모순 이중 답변**(D-062 합성으로 사후 통합은 되나
근원 낭비는 잔존). 또한 replanner LLM이 `supersedes`(D-043)를 신뢰성 있게 설정하지 않아
(원인 B) D-043 숨김도 작동하지 않았다.

### 결정

`replanner`에 **결정적 가드** `_filter_futile_retries`를 추가한다. 엔티티가 이미 확보된
(선행이 >0행 반환) 조회를 같은 방식으로 다시 묻는 후속을 제거하고, 전부 제거되면 루프를
종료한다. **`supersedes` 필드 존재에만 의존하지 않도록 행수 + 독립성으로 판정**(원인 B에
견고):

- 제거 (a): 신규 task의 `supersedes`가 **>0행을 반환한** 선행 task_id를 가리킴(명시적 재조회).
- 제거 (b): 신규 task가 **독립**(`depends_on` 비어있음)이면서 **같은 agent**의 선행이 이미
  >0행을 반환함(LLM이 supersedes를 빠뜨린 암묵적 재조회).
- 보존: 선행이 0건이면(엔티티 못 찾음) "0건 → 재조회" 허용 / 데이터 의존 보강(`depends_on`
  존재, 장애→알람) 허용 / 다른 agent 보강 허용.

보조로 `prompts/replanner.py`에 판단 원칙 7 추가: "행은 찾았으나 일부 요청 필드가 null인
경우는 속성 부재이므로 재조회 금지(0건=대상 못 찾음 vs 필드 null=속성 부재 구분)". 가드가
안전망, 프롬프트는 LLM이 애초에 덜 제안하도록 보조.

### 근거 / 대안

- 채택: **옵션 ① 엔티티 찾으면 즉시 중단**(사용자 분석 요청에 대한 권장). data_query agent가
  내부적으로 DB를 선택하므로 1차 task가 이미 관련 DB를 고려하고, 실측상 "전체 소스" 확장도
  CPU/메모리를 못 찾아 교차 DB 회수 가치가 낮음. 엔티티 확보 후 종료 시 단일 task → 합성조차
  불필요한 정직한 단일 답변("CPU·메모리는 데이터에 없어 확인 불가").
- 대안(기각) 옵션 ② 1회 확장 후 중단: 재시도 이력 추적 필요(복잡)하고 모순 답변 표면이 잔존.
  대안(기각) 옵션 ③ 프롬프트만: 원인 B와 동일하게 LLM 준수에 의존해 신뢰성 낮음.
- 한계: 필드가 정말 다른 DB에 있는데 1차가 그 DB를 놓친 경우 회수 못 함 — 그 경우의 옳은
  수정 지점은 field_mapper/data_query DB 선택이지 replanner 재시도가 아님. honest "확인 불가"가
  4회 모순 답변보다 낫다.
- 관련: D-043(supersedes — 원인 B 보완), D-062(합성 안전망), D-040(replanner 중복 가드).
- 검증: arch_check --ci exit 0, replanner 신규 6건 포함 orchestration 130 passed/4 skipped.

---

## D-064. 텍스트 후속 턴의 폼필 요청-스코프 상태 누수 차단

| 항목 | 내용 |
|------|------|
| **결정일** | 2026-07-09 |
| **상태** | 확정 (옵션 B — 경로에서 트리거만 초기화 + field_mapper 스킵 자기정리, 진입 경로 무관) |
| **관련 결정** | D-053(db_id 승격), D-055/D-056(멀티턴 지시어·엔티티 sticky) — 충돌 없음(세션 승계 신호 불변) |

### 배경 / 문제

멀티턴 테스트에서: 폼업로드 턴(은행 b0 양식 채우기) 다음의 **텍스트 질의**(파일 없이 "공동존
전체 서버 메트릭 조회")가 (1) "입력 분석"에 **템플릿 구조가 재출력**되고 (2) "DB 조회" 대상이
**polestar_b0로 고정**되며 (3) 직전 양식을 그대로 재활용해 채워 넣었다.

근본 원인 — LangGraph 체크포인터의 **델타 병합**: 텍스트 경로(`/query`, `/query/stream`)는
후속 턴에 `{user_query, messages}`만 입력으로 준다([query.py]). 체크포인터는 나머지 필드를
직전 턴 값으로 유지하므로, 직전 폼업로드 턴의 `uploaded_file`/`file_type`/`csv_sheet_data`/
`template_structure`/`mapped_db_ids`가 **잔존**한다(실측: MemorySaver 2턴 재현으로 확인).
그 결과 `input_parser`가 옛 파일을 재파싱해 `template_structure`를 되살리고([input_parser.py]
`if state.get("uploaded_file")`), `intent_planner`가 잔존 `mapped_db_ids`로 DB를 고정한다
([intent_planner.py] mapped_db_ids 단축 — LLM 분해·위치 라우팅을 전부 우회). 파일 업로드
경로는 `create_initial_state`가 전 필드를 리셋하므로 무관 — **텍스트 경로 전용 누수**.

### 결정 (옵션 B)

폼필 상태는 **요청-스코프**(세션-스코프 아님)로 취급하여, 이번 턴에 파일이 없으면 존재해선 안 된다.

- **경로**: 텍스트 후속 턴 입력을 `state.create_followup_input()`로 생성 — 폼필 **트리거**
  (`uploaded_file`/`file_type`/`csv_sheet_data`)를 `None`으로 명시 초기화(input_parser 재파싱 차단).
  파일이 없음을 아는 곳은 라우트뿐이라 트리거 초기화는 라우트에 둔다.
- **노드(단일 출처)**: `field_mapper`가 template 없이 스킵할 때 매핑 산출물
  (`mapped_db_ids`/`column_mapping`/`db_column_mapping`/`mapping_sources`/`llm_inference_details`/
  `mapping_report_md`)을 `None`으로 정리(`_CLEARED_MAPPING_FIELDS`). "template 없음 → 매핑 없음"
  불변식을 **진입 경로와 무관하게** 보장(경로별 목록 중복 방지).
- **보존(승계 신호)**: `messages`/`conversation_context`/`pending_synonym_reuse`/
  **`pending_synonym_registrations`**(멀티턴 유사어 등록 흐름)/승인 컨텍스트는 건드리지 않는다.
  특히 `pending_synonym_registrations`를 지우면 "N턴 폼필 → N+1턴 등록" 흐름이 깨지므로 제외.

### 근거 / 대안

- 채택: 옵션 B(노드 자기정리) — 진입 경로가 늘어도 매핑 누수가 자동 봉인. "template 없음=매핑
  없음"은 의미적으로 옳아 부작용이 낮다. 트리거만 라우트에서 지우면 됨(2곳, 승인 분기는 폼필
  approval이 드물어 제외 — 보수적).
- 대안(기각) 옵션 A(라우트에서 전 필드 명시 초기화): 라우트 2곳에 긴 목록 중복·누락 위험.
- 동작 변화(1건): 텍스트 후속 턴이 직전 양식을 **자동 상속하지 않음**("방금 올린 양식으로 이번엔
  공동존 채워줘"는 재업로드 필요). 현재는 그게 조용히 틀린 DB로 채워지는 버그라 상속 차단이 안전.
  사용자 확인 완료(2026-07-09): 기본 동작 정합 우선, 상속 재사용은 후속 기능으로 유보.
- 수정: `src/state.py`(`create_followup_input`), `src/api/routes/query.py`(텍스트 경로 2곳),
  `src/nodes/field_mapper.py`(`_CLEARED_MAPPING_FIELDS`, 스킵 2경로).
- 검증: `tests/test_multiturn/test_form_state_reset.py`(6건), `test_field_mapper_node.py` 갱신.

---

## D-065. 바(bare) "공동존" 위치의 DB 라우팅 (gp+yd)

| 항목 | 내용 |
|------|------|
| **결정일** | 2026-07-09 |
| **상태** | 확정 (aliases 보강 + input_parser 결정적 가드 — `_resolve_priority_db_ids` 전용 분기 미사용) |
| **관련 결정** | D-053(위치→db_id 힌트), D-057/D-058(공동존 gp/yd 폼필) |

### 배경 / 문제

"공동존 전체 서버" 폼필(3턴)이 gp/yd가 아닌 **polestar_b0**로 라우팅됐다(D-064의 누수와 별개 —
파일 경로라 `create_initial_state`가 리셋하므로 잔존이 아님, 실측 확인). "공동존"은 김포+여의도
K리전 공동존 **두 DB**를 포괄하는데:
- gp/yd `aliases`가 `"공동존 김포 폴스타"`처럼 **복합어만** 있고 단독 `"공동존"`이 없었다.
- `input_parser` 규칙 10 예시에 여의도/김포만 있어 LLM이 "공동존"을 `target_db_hints`로 뽑는
  것이 **비결정적**이었다.

→ 힌트가 비면 `field_mapper._resolve_priority_db_ids`의 priority가 비고, 폼필 DB 선택이
`active_db_ids` 순서(b0가 첫 항목, [domain_config.py])대로 **b0로 잘못 확정**됐다.

### 결정

- **aliases 보강**: gp/yd `aliases`에 단독 `"공동존"`/`"공동존 폴스타"` 추가. 기존 지역-배제
  로직상 `"공동존"`→`[gp, yd]`, `"공동존 김포"`→`[gp]`(복합어 세분화 유지), `"은행"`→`[b0]`
  으로 정확히 해소된다(실측).
- **input_parser 결정적 가드**: `_ensure_location_hints`로 원문의 위치 표면어
  (`공동존`/`김포`/`여의도`/`은행`/`레거시`/`은행존`)를 `target_db_hints`에 보강(부분문자열
  중복 방지). LLM 프롬프트(규칙 10 예시에 "공동존" 추가)만으론 비결정적이라 결정적 후처리 병행
  (Known Mistakes 2026-06-29 "LLM 분류는 결정적 가드로 교정" 원칙).

### 근거 / 대안

- **`_resolve_priority_db_ids` 전용 분기 미채택**(사용자 검토 요청): 기존 alias-substring +
  지역-배제 for-루프가 이미 `"공동존"`→`[gp,yd]`와 `"공동존 김포"`→`[gp]`를 올바르게 낸다.
  `if "공동존" in hint: return [gp,yd]` 같은 무조건 분기를 넣으면 `"공동존 김포"`의 세분화를
  **덮어써** yd가 잘못 포함된다 → 분기는 오히려 회귀 위험. **aliases만이 안전**.
- process_query `_LOCATION_DB_HINTS`(실시간 프로세스/알람 API 경로)는 이번 폼필 버그와 무관이라
  변경하지 않음(폼필 DB는 field_mapper가 결정). 향후 공동존 프로세스 조회 편입 시 별도 등재.
- 수정: `src/routing/domain_config.py`(gp/yd aliases), `src/nodes/input_parser.py`
  (`_ensure_location_hints`), `src/prompts/input_parser.py`(규칙 10 예시).
- 검증: `tests/test_routing/test_gongdongjon_routing.py`.

### 후속 (실측 재테스트 — 제품명 단독 "폴스타"가 b0를 끌어들임)

"공동존 폴스타의 모든 서버" 폼필이 여전히 b0(은행존) 참조. 원인: LLM이 `target_db_hints`에 bare
"폴스타"를 넣으면, `_resolve_priority_db_ids`의 부분매칭이 "폴스타"를 b0 alias "은행 폴스타"에
매칭 → b0가 priority에 포함되고 active 순서(b0 첫째)상 **b0가 우선 선택**됐다. (`_ensure_location_hints`가
"공동존"을 넣어도, "폴스타"가 별도 hint로 b0를 끌어옴.) **소수점 소실(사용률 int·0% 표시)은 이
오라우팅의 결과** — b0(DB2)는 사용률 통계를 정수로 반환하고 gp/yd만 `ROUND(::numeric,2)`를 쓴다
(2026-07-01 기록). 수정: `_resolve_priority_db_ids`가 **지역 토큰(공동존/김포/여의도/은행 등)이
있으면 제품명 단독 토큰(폴스타/polestar)을 제거**(`_is_generic_only_hint`) → 지역이 우선. 지역 없는
순수 "폴스타"는 종전대로 전체 폴스타 후보 유지. b0 자체의 소수점(DB2 컬럼 타입) 수정은 별도 유보.
회귀: `test_gongdongjon_routing.py::TestGenericProductTokenDoesNotPullB0`. 교훈: **제품명 공통 토큰은
지역 변별력이 없다 — 지역 명시 시 무시. 잘못된 DB 참조가 다운스트림 증상(소수점)으로 위장**.

### 후속 2 (라우팅 정상화 후 — 공동존 다중 DB 조회 + b0 소수점)

라우팅 수정 후 공동존이 gp로 정상 라우팅됐으나 두 잔여:

- **공동존인데 gp만 조회(yd 누락)**: `_resolve_priority_db_ids(["공동존"])=[gp,yd]`지만, field_mapper가
  각 필드를 첫 priority DB(gp)에만 매핑해 `mapped_db_ids=[gp]`가 됐다. gp/yd는 스키마 동일이나
  **데이터(김포/여의도 서버)는 달라 둘 다 조회 필요**. 수정: `_replicate_mapping_for_multi_location` —
  priority가 다중이면 매핑(union)을 전 priority DB에 복제하고 mapped_db_ids를 priority 전체로 확장
  (스키마 다른 DB로 잘못 복제돼도 multi_db_executor 테이블 필터가 제거). 회귀:
  `test_gongdongjon_routing.py::TestMultiLocationReplication`.
- **은행존(b0) 사용률 정수 표시(소수점 소실)**: 원인은 데이터 저장 방식이 아니라 **DB2의 `AVG()`가
  정수 컬럼을 정수로 집계**하는 것(PostgreSQL AVG는 numeric 반환). gp/yd는 `::numeric` 캐스트로
  회피하지만 b0 예시는 캐스트 없어 truncate. 수정: b0 예시·가이드의 집계 값을 **집계 전 DECIMAL
  캐스트**(`AVG(CAST(s.avg_val AS DECIMAL(15,4)))`). `::numeric`은 DB2 문법 오류라 CAST 사용. 2026-07-01
  "b0 정수 표시(SQL 레벨 추정)" 유보 항목 해소. 저장은 동형 솔루션이라 동일(사용자 지적) — SQL 방언 차이.

### 후속 3 (서로 다른 존을 지목하는 다중 hint가 한쪽만 조회 — 지역 배제의 전역 평가 결함)

버그(2026-07-13): "은행 폴스타와 공동존 김포 폴스타의 모든 서버" 폼필에서 `input_parser`가
`target_db_hints=["은행 폴스타", "공동존 김포 폴스타"]`로 **정확히 파싱**했으나 결과에 은행존(b0)만
포함되고 공동존 김포(gp)가 누락. 원인: `_resolve_priority_db_ids`의 지역 배제 로직이 db_id별로
**전체 hint 목록**을 훑어 "경쟁 지역이 하나라도 있으면 배제"했다. 그래서 gp는 "은행" hint(은행존 지목)에
걸려 배제되고, b0는 "김포" hint(공동존 지목)에 걸려 배제 → priority가 **빔**. 빈 priority에서 폼필 DB가
active 순서 폴백으로 b0만 선택됨. 단일 존 의도만 가정한 배제 로직이 **다중 존 다중 hint**에서 양쪽을 서로
지워버린 것.

수정: 지역 배제를 **hint 단위**로 평가(`_hint_excludes_db(hint, db_id)`). 각 db_id는 자신을 배제하지
않는(=다른 존을 지목하지 않는) hint만 매칭 후보로 사용 → 각 hint가 지목한 DB만 선택되고 union.
`["은행 폴스타", "공동존 김포 폴스타"]` → `[b0, gp]`. 배제 규칙 자체는 db_id→경쟁지역 테이블
(`_DB_EXCLUDING_REGIONS`)로 집약(기존 if/elif 분기와 동일 의미). 단일 존 hint·제품명 단독 필터 동작은
불변(기존 회귀 전부 유지). 이후 `_replicate_mapping_for_multi_location`이 [b0, gp]에 매핑 복제하여 둘 다 조회.
회귀: `test_gongdongjon_routing.py::TestCrossZoneMultiHint`. 교훈: **"~와/과 …"로 여러 존을 나열하는 hint는
서로를 배제하면 안 된다 — 배제는 전역이 아니라 hint 단위.** 파싱은 맞아도 라우팅 후처리가 상호 소거할 수 있으니
다중 hint 케이스를 결과로 실측.

---

## D-066. 단일/멀티 DB SQL 생성 경로 동등화 (few-shot 예시·전체조회 LIMIT 공유)

| 항목 | 내용 |
|------|------|
| **결정일** | 2026-07-09 |
| **상태** | 확정 (RC1+RC4 우선 — 공용 헬퍼로 동등화. RC2/RC3는 결과 관찰 후 판단) |
| **관련 결정** | D-057(multi_db_executor 스키마·엔진 인지), D-050(EAV 피벗), D-058(gp/yd COALESCE) — 충돌 없음(parity 연장) |

### 배경 / 문제

"공동존의 전체 서버 + 지난 1개월 CPU/메모리 사용률" 폼필이 gp/yd로 정상 매핑됐으나
`data_insufficient` 실패·NULL 난무·`LIMIT 1000`. 생성 SQL이 실존하지 않는 조인
(`cmm_metric_stat_h`에 서버행 직접 조인 + `definition_name='CPUUtilization'` 환각)을 만듦.
반면 **은행존(b0, 단일 DB)은 동일 양식으로 정상 동작**.

근본 원인 — **DB 개수가 갈라놓은 두 SQL 생성 경로의 품질 비대칭**([subagents.py]
`run_data_query_pipeline`: `is_multi_db = len(targets) > 1`):
- 단일 DB(b0) → `_run_single_db_pipeline` → `schema_analyzer→query_generator→query_validator`
  풀 파이프라인. **query_generator가 프로필 few-shot `query_examples` 주입 + "전체/모든"
  LIMIT 상향(100000) + 풀 검증·재시도**를 수행 → 올바른 `cmm_metric_stat_m` 피벗 SQL.
- 멀티 DB(gp+yd) → `multi_db_executor` → query_generator를 **덜 갖춘 형태로 재구현한 별도
  경로**: `query_examples` 미주입(query_guide 텍스트만)·`default_limit`(1000) 고정·간이 검증만.
  프로필엔 정답 예시("서버들의 CPU/메모리 사용률")가 **있는데도** 주입 안 돼 LLM이 field_mapper의
  가짜 컬럼 매핑(`cmm_metric_stat_h.cpu_avg_val` 등)을 그대로 따라 환각 SQL 생성.

즉 b0가 동작한 건 스키마 차이가 아니라 **few-shot 예시가 field_mapper 가짜 매핑을 이겼기**
때문. 멀티 경로엔 그 예시가 없어 가짜 매핑이 이김.

### 결정 (RC1 + RC4)

두 경로가 **동일 출처**를 쓰도록 공용 헬퍼 `src/utils/query_gen_common.py` 신설:
- **`build_query_examples_block(structure_meta)`** — 프로필 `query_examples`를 few-shot 블록으로.
  `query_generator._format_structure_guide`의 인라인 로직을 이 함수로 대체(동작 동일),
  `multi_db_executor._generate_sql`이 `structure_guide`에 동일 블록을 append(RC1).
- **`resolve_query_limit(user_query, default_limit)`** — "전체/모든/모두" → 100000 상향.
  query_generator의 인라인 규칙을 이 함수로 대체(동작 동일), `multi_db_executor`가
  `state["user_query"]` 기준으로 동일 규칙 적용(RC4, 전체 서버 1000건 절단 방지).

### 근거 / 대안

- RC1+RC4 우선: b0 선례가 "예시 주입만으로 가짜 매핑을 이긴다"는 증거. RC2(field_mapper가
  사용률 지표를 가짜 컬럼으로 매핑)·RC3(시간단위 _h/_d/_m 미반영)은 **RC1 적용 후 실제 결과를
  보고** 필요 시에만(과수정 회피). query_examples가 `_m` 기준이라 RC3도 상당 완화 기대.
- 공용 헬퍼 단일화: multi_db_executor는 query_generator의 축소 재구현이라 기능이 계속 어긋남
  (D-057에서도 스키마 인지 이식 필요했음). 예시·LIMIT을 단일 출처화해 **경로 간 재발 방지**
  (`query_generator.py와 동일 로직 적용` 선례 — 변경이력 2026-03 EAV value_joins 참조).
- 대안(기각): multi_db_executor를 폐기하고 멀티 DB도 query_generator 루프를 N회 호출 — 대규모
  리팩터·회귀 위험 큼. 동등화가 최소 침습.
- 수정: `src/utils/query_gen_common.py`(신규), `src/nodes/query_generator.py`(헬퍼 사용),
  `src/nodes/multi_db_executor.py`(예시 주입·effective_limit).
- 검증: `tests/test_nodes/test_query_gen_parity.py`, query_generator 회귀 동일(behavior).
- 잔여(관찰 후): RC2 field_mapper 사용률-지표 매핑 완화, RC3 시간단위→metric 테이블 힌트.

### 후속 (실측 재테스트 — 진짜 근본원인: 멀티 DB 경로에 structure_meta 미부착)

RC1(예시 주입) 적용 후 공동존 재테스트에서도 **동일 환각 SQL**(`cmm_metric_stat_h`,
`definition_name='CPU'`, 서버행 직접 조인). RC4(LIMIT 100000)는 적용됨 → 새 코드는 도는데
RC1만 무효. 원인: `build_query_examples_block(structure_meta)`의 **structure_meta가 멀티 DB
경로에선 항상 None**이었다.

- `multi_db_executor._analyze_schema`는 `cache_mgr.get_schema_or_fetch`만 호출 — 이는 **테이블
  스키마만** 반환하고 `_structure_meta`(query_guide/query_examples/patterns)는 **별도 캐시 키**라
  부착하지 않는다. `_structure_meta`를 붙이는 건 단일 DB 경로의 **`schema_analyzer` 노드**뿐
  (line 1037, get_schema_or_fetch가 저장한 **이후** 인메모리로 추가 → 캐시 블롭엔 미포함).
- 결과: 멀티 DB 경로는 **query_guide·query_examples·EAV 힌트가 프롬프트에 전혀 안 들어감**.
  LLM은 원시 테이블 컬럼(cmm_metric_stat_h.resource_id/definition_name/avg_val)만 보고 조인
  의미(resource_type 피벗·`_m` 월별·`'Utilization'`)를 몰라 환각. RC1은 no-op였던 것.
- **수정**: `_analyze_schema`가 `_load_manual_profile(db_id)`(수동 프로필 우선) → 실패 시
  `cache_mgr.get_structure_meta(db_id)` 폴백으로 `_structure_meta`를 **명시적으로 부착**(단일 DB
  schema_analyzer 노드와 동등). 이걸로 query_guide + query_examples + EAV 힌트가 비로소 프롬프트에
  실려 RC1이 실효를 갖는다.
- 회귀: `test_query_gen_parity.py::TestAnalyzeSchemaAttachesStructureMeta`(수동 프로필에서
  사용률 예시·guide 부착 확인). 교훈: **"주입 코드 추가"만으론 부족 — 주입 대상 데이터가 그
  경로에 실제로 존재하는지 실측**(단일/멀티 경로의 schema 조립 차이).

### 후속 2 (실측 재테스트 — 예시는 실렸으나 강제 매핑·프로필 synonym이 방해)

structure_meta 부착 후 SQL이 CTE/예시 패턴으로 바뀌었으나 잔여 3증상: (1) 여전히
`cmm_metric_stat_h` 참조, (2) 서버 이름에 등록명(name) 대신 OS hostname, (3) 부분 채움·
data_insufficient. 각 원인·수정:

- **RC2b (서버이름=hostname)**: gp/yd 프로필의 EAV `Hostname` synonyms가 아직 `["EAV호스트명",
  "서버명", "호스트네임"]` — **b0가 D-061에서 제거한 바로 그 버그가 gp/yd엔 미적용**. "서버 이름"이
  EAV Hostname(→hostname)으로 매핑됨. 수정: gp/yd의 EAV Hostname synonyms를 `["EAV호스트명"]`으로
  축소(D-061 미러). column_synonyms(name←서버명/서버 이름, hostname←호스트명)는 이미 존재.
- **RC2 (강제 매핑이 예시를 이김)**: `## 양식-DB 매핑 (반드시 SELECT에 포함할 컬럼)`이 field_mapper가
  지어낸 `cmm_metric_stat_h.cpu_avg_val` 같은 컬럼을 강제 → LLM이 _h·잘못된 조인 생성.
  사용률 지표는 resource_type+definition_name 피벗이라 단일 field→column 매핑 불가.
  수정: `_generate_sql`이 `cmm_metric_stat` 참조 매핑을 강제 블록에서 **제외**하고, 대신 "쿼리
  예시의 `cmm_metric_stat_m` 피벗을 따르라 + `_h/_d` 금지 + 지어낸 컬럼명 대신 avg_val/max_val를
  resource_type로 구분"하라는 안내로 전환(RC3 월별 강제 포함). 단순 컬럼(name/hostname/ip/OS/코어수/
  메모리크기)은 계속 강제.
- 회귀: `test_query_gen_parity.py::TestMetricMappingDeferredToExamples`(지어낸 metric 컬럼 미강제·
  안내 전환·단순 컬럼 강제 유지). 교훈: **EAV/피벗형 지표는 "field→table.column" 강제 매핑으로
  표현 불가 — 강제하면 few-shot 예시를 이겨 환각**. 프로필 synonym 수정(D-061)은 **DB별로 전파
  확인**(b0만 고치고 gp/yd 누락).

### 후속 3 (실측 재테스트 — 요청 무한 hang + field_mapper 완결)

재테스트에서 **요청이 영영 안 끝남**(로그 마지막이 field_mapper step 2.8 제외 3줄, 이후
healthcheck만). 두 원인:

- **트리거**: field_mapper가 사용률 필드(CPU/메모리 평균·최고)를 계속 매핑 시도 → step 2.8 배치
  LLM은 응답했으나(malformed `matched_key="db_id:polestar_cm_yd:..."` 파싱으로 제외), 이어지는
  **step 3(`_apply_llm_mapping_with_synonyms`) LLM 호출이 응답 없이 멈춤**(closed-net, 더 큰 프롬프트).
  수정(Fix A): `perform_3step_mapping`이 사용률 필드(`_is_metric_usage_field`: metric 명사+집계어)를
  `remaining`에서 **제외** → step 2.8/3 LLM을 아예 안 돌림, 미매핑(None)으로 두어 SQL 예시 피벗에
  위임(RC2의 field_mapper 쪽 완성). '메모리 용량'·'CPU 코어 수'(사양 EAV)는 집계어 없어 정상 매핑 유지.
  **스킵은 cmm_metric_stat 피벗 스키마일 때만**(`_schema_uses_metric_stat_pivot`) — 사용률이 직접
  컬럼(cpu_metrics.usage_pct 등)인 스키마에서는 정상 매핑 대상이라 스킵하지 않음(과대적용 방지).
- **증폭**: SSE 스트리밍 경로(`/query/stream`·`/query/file/stream`)의 `astream_events` 루프에 **전체
  타임아웃 부재** — `file_query_timeout`은 폴백 ainvoke에만 적용. 노드 내부 LLM이 멈추면 다음 이벤트가
  영영 안 나와 무한 hang. 수정(Fix B): `async for`를 수동 iterator + `asyncio.wait_for(anext,
  timeout=query_timeout|file_query_timeout)`로 바꿔 stuck fetch를 끊고 error 이벤트 방출 후 종료.
- 회귀: `test_query_gen_parity.py::TestMetricUsageFieldSkip`(사용률 필드 판별 12케이스 + metric-only는
  LLM 미호출·미매핑). 교훈: **(1)매핑 불가한 파생 필드를 매핑 시도하면 환각·hang 유발 → 조기 제외.
  (2)장시간 실행 경로(SSE)는 반드시 전체 타임아웃 가드** — per-call 타임아웃(FabriX 300s)만으론
  스트리밍/재시도로 무력화됨.
- 수정 파일: `src/document/field_mapper.py`(`_is_metric_usage_field`·remaining 제외),
  `src/api/routes/query.py`(두 스트리밍 루프 wait_for).

### 후속 4 (실측 재테스트 — 공동존 폼 metric 칼럼 공백 + b0 정수)

Fix A(metric 필드 미매핑)가 **폼필 채우기의 헤더→결과칼럼 연결을 끊음**. excel_writer는 미매핑 헤더에
대해 **한글 헤더명 자체를 결과 칼럼 키로 fuzzy 매칭**하는데(`excel_writer._get_value_from_row`),

- **단일 DB(query_generator)**: "자동 매핑 실패 필드" 블록이 미매핑 필드를 **한글 헤더 그대로
  `AS "CPU 평균"`** 로 alias하라 지시 → 결과 키=한글 → 매칭·채움 성공(은행존 정상).
- **멀티 DB(multi_db_executor)**: 그 블록이 **없어** metric 필드가 프롬프트에서 누락 → LLM이 영문
  alias(`cpu_util_avg`) → 한글 헤더 매칭 실패 → **공동존 Excel metric 칼럼 공백**(또 다른 경로 비대칭).

수정: query_generator의 미매핑-필드 블록을 `multi_db_executor._generate_sql`로 이식(통합 column_mapping의
None 필드를 `unmapped_fields`로 전달). 더불어 블록의 소수 캐스트 예시가 `::numeric`(PostgreSQL) 고정이라
b0(DB2)에서 정수 표시·문법 위험 → 공용 `decimal_cast_example(db_engine)`로 **엔진별 캐스트**(PostgreSQL
`::numeric`, DB2 `CAST(... AS DECIMAL(15,4))`) 주입. 두 경로 모두 적용. 회귀:
`test_query_gen_parity.py`(unmapped alias 블록·엔진별 캐스트·경로 패리티). 교훈: **필드를 "미매핑"으로
두면 SQL 정확성은 얻지만 폼필이 결과칼럼 이름에 의존하게 된다 — 미매핑 필드는 반드시 헤더명으로 alias
지시. 이것도 단일/멀티 경로 비대칭(D-066 계열 반복).**

### 후속 5 (실측 — 공동존 매핑 필드 alias 불일치 + 엑셀 소수 표시)

metric은 채워졌으나 **식별 필드(서버 이름/호스트네임/IP)가 gp/yd 간 제각각**(gp=한글 임의명, yd=영문
Server Name/Hostname)이라 폼 헤더와 안 맞아 공백. 원인: 멀티 DB는 **DB별 독립 LLM 호출**로 SQL을
따로 생성 → alias가 비결정적. 기존 forcing 블록은 `"table.column" alias`를 지시했으나 LLM이 안 따름.
수정: `multi_db_executor`의 regular/EAV 블록을 **"결과 alias는 반드시 양식 필드명(한글 헤더) 그대로,
임의 영문 alias 금지"** 로 변경 → gp/yd 모두 동일 헤더 alias → `excel_writer._get_value_from_row`의
reverse_mapping(step4)이 매핑 필드를, 헤더-키 fallback이 미매핑 필드를 일관 매칭. (단일 DB는 정상이라
미변경 — excel의 dual 매칭이 양쪽 흡수.) **엑셀 소수 표시**(은행존 1.55000000000): 값은 정상(CSV=1.55),
템플릿 셀 number_format zero-fill이 원인. `excel_writer._normalize_cell_value`로 Decimal→float 정규화
(스케일 trailing zero 제거·정수는 int). 잔존 시 템플릿 셀 서식 문제라 서식 override는 유보. 회귀:
`test_query_gen_parity.py::TestMetricMappingDeferredToExamples`(헤더 alias 지시), normalize 단위 확인.
교훈: **DB별 독립 SQL 생성은 결과 컬럼명이 비결정적 — 폼필처럼 컬럼명 일치가 필요하면 결정적 alias
규칙(양식 헤더)을 강제**.

### 후속 6 (실측 — 프롬프트 튜닝 한계 → 결정적 구조 수정 3종)

후속5의 프롬프트(헤더 alias 강제) 후에도 재발: gp="서버 이름"/yd="서버명"(alias 여전히 흔들림),
b0 소수↔정수 왕복, "IP주소"↔"ip 주소"(대소문자·공백). 근본은 **LLM alias/cast 비결정성** — 프롬프트로는
한계. 결정적 구조 수정 3종:

- **① 멀티 DB 동일 스키마 SQL 재사용**(`multi_db_executor`): 공동존(gp/yd)은 스키마·`db_schema`(polestar)
  동일 → 첫 DB의 **검증된 SQL을 나머지 동일 스키마 DB에 재사용**(`_sql_by_schema` 캐시, 키=(engine,schema)).
  두 DB가 **동일 컬럼명** → 병합·폼필 정합. DB별 독립 LLM 호출의 alias 비결정성 원천 제거(서버명 중복·
  여의도 빈칸 해소).
- **② active_db_engine 실제 설정**(`subagents.run_data_query_pipeline`): 지금껏 항상 None →
  query_generator/validator가 b0(DB2)를 PostgreSQL로 취급(`::numeric`·LIMIT) → 문법 오류·정수·
  data_insufficient. `get_domain_by_id(db_id).db_engine`로 실제 엔진 주입 → DB2는 `FETCH FIRST`·
  `CAST DECIMAL`·`decimal_cast_example('db2')` 적용.
- **③ excel 매칭 표기 정규화**(`_get_value_from_row` 3.7): 소문자+공백/언더스코어 제거 비교로
  "IP주소" vs "ip 주소" 등 alias 표기 흔들림 흡수(reverse_mapping 필드명도 정규화).

교훈: **LLM 출력(alias/cast/방언)에 정합성을 의존하지 말 것 — (a)동일 스키마는 SQL 재사용으로
결정화, (b)엔진 메타는 config에서 결정적 주입, (c)이름 매칭은 표기 정규화로 흡수**. 잔여(관찰):
b0 metric cast는 재사용 대상 아니라 여전히 LLM 의존(단일 DB) — 반복 시 결정적 SELECT 구성 검토.

---

## D-067. 재발 방지 드리프트 가드 (프로필·경로 중복 감시 테스트)

| 항목 | 내용 |
|------|------|
| **결정일** | 2026-07-09 |
| **상태** | 확정 (B+C 가드레일 도입. 근본 리팩터 A(프로필 상속)는 부담이 커 보류) |
| **관련 결정** | D-061(서버명), D-066(경로 동등화) — 이들의 재발을 자동 감지 |

### 배경 / 문제

같은 결함이 반복 "재발"하는 근본 이유는 regression이 아니라 **중복**: (1) gp/yd/b0가 동일
Polestar 스키마를 기술하는 near-duplicate 프로필 3벌(**gp/yd는 567줄 중 실질 1줄만 차이**),
(2) 단일 DB(`query_generator`)/멀티 DB(`multi_db_executor`) SQL 생성 로직 2벌. 한 복사본만
패치하면 나머지에 결함이 남아 나중에 표면화한다(D-061=b0만 고쳐 gp/yd 누락, D-066=단일 경로만
갖춰 멀티 경로 누락). Known Mistakes에 같은 메타-교훈이 3회 등장("한 엔진/경로만 고치지 말라").

근본 해소는 **A. 프로필 상속(공통 base + delta override)** 이나 구조 변경 부담이 커 보류하고,
저비용 **가드레일**로 "전파 누락·경로 비대칭"을 CI에서 즉시 실패시킨다.

### 결정 (B + C, 테스트 전용 — 프로덕션 코드 미변경)

- **B. 프로필 드리프트 가드** (`tests/test_routing/test_polestar_profile_consistency.py`):
  - **B-1 불변식**: 3개 Polestar 프로필 각각이 과거 버그를 규칙화한 불변식을 만족(EAV Hostname
    synonyms에 식별어 없음=D-061, name/hostname column_synonyms 분리, 월별 metric 예시 존재=D-066,
    예시 SQL에 환각 패턴(`definition_name='CPU'`·`cpu_avg_val`) 없음, source=manual).
  - **B-2 gp/yd 등가성**: 주석 제외 실질 라인 차이가 allowlist(OSParameter synonym 1건)뿐. 한쪽만
    수정하면 즉시 실패(RC2b 재발 차단).
- **C. 경로 패리티 가드** (`tests/test_nodes/test_query_gen_parity.py::TestCrossPathParity`):
  두 SQL 생성 경로가 **동일 공유 헬퍼 객체**(`build_query_examples_block`/`resolve_query_limit`)를
  참조(복붙 분기 차단), 각 프롬프트 빌더가 예시를 실제 주입, LIMIT 상향 대칭.

### 근거 / 한계

- 채택 이유: 저비용(LLM/DB 불필요, 수 ms)·즉효. 부정검증으로 실제 재발 시 실패함을 확인(누출·
  divergence 주입 → 감지). B=데이터(프로필) 중복, C=코드(경로) 중복을 각각 커버(상보적).
- **한계(명시)**: 둘 다 **"이미 아는 결함의 재발"만** 잡는 가드레일 — 신규 버그 유형은 그에 대한
  불변식을 한 줄 추가하기 전까지 못 잡는다. 새 지뢰를 밟을 때마다 B-1에 assert 한 줄 추가로 성장시킨다.
- 유지: 공통 속성을 의도적으로 바꾸거나 gp/yd를 다르게 할 때만 불변식/allowlist 갱신(드묾).
- 보류(A): 프로필 상속은 loader 변경·마이그레이션 부담이 커 후속 과제로 남김(가드가 있으면
  A 없이도 재발은 CI에서 드러남).
- 검증: B 16건 + C 3건 통과, 부정검증(누출·divergence 주입 시 실패) 확인, arch_check exit 0.

---

## D-068. 폼필 EAV 강제 SELECT의 resource_type 인지 다중 리소스 피벗

| 항목 | 내용 |
|------|------|
| **결정일** | 2026-07-13 |
| **상태** | 확정 (강제 블록 결정화 + 단일/멀티 경로 공유 헬퍼) |
| **관련 결정** | D-050(EAV 피벗은 HAVING), D-066(경로 동등화), D-067(중복 가드) |

### 배경 / 문제

양식 채우기에서 `CPU 코어 수`·`메모리 용량` 칼럼이 **전부 빈칸**(공동존 멀티·여의도 단일 공통).
생성 SQL은 `MAX(CASE WHEN cc.name='LOGICALCORE' THEN cc.stringvalue_short END)`처럼 피벗 형태는
있으나 값이 NULL. 근본 원인은 EAV 강제 블록(`query_generator`/`multi_db_executor` 양쪽 복제)이:

1. **resource_type 구분이 없음** — `cc.name='LOGICALCORE'`만 생성. 그러나 LOGICALCORE는
   `server.Cpus`, TotalSize는 `server.Memory` **자식 리소스 행**의 속성이다.
2. **조인 힌트가 `value_joins`(Hostname/IPaddress = server.Server만) 기반** — config를 서버 행의
   `resource_conf_id`에만 붙임. 자식 리소스 속성은 그 config 그룹에 없어 영영 NULL.

프로필 query_example엔 올바른 멀티 리소스 피벗(`GROUP BY platform_resource_id` +
`MAX(CASE WHEN c.resource_type='server.Cpus' AND cc.name='LOGICALCORE' ...)`)이 있으나, **강제
블록의 (틀린) 명시 지시가 예시를 이긴다**. 사용률(cmm_metric_stat) 필드가 단일 field→컬럼
매핑 불가라 강제에서 제외됐던 것(D-066 후속3)과 **동일 계열** — 정적 EAV 자식 속성도 동일.

### 결정 (1차: config 피벗 결정화)

- **`eav_attr_resource_types(schema_info)`**: known_attributes 설명의 `[resource_type: …]`를
  파싱해 `속성명→resource_type` 맵 구성(복수 표기는 첫 매치). 프로필에 이미 있는 메타를 활용.
- **`build_multi_resource_pivot_block(...)`**: 자식 리소스 EAV 속성이 하나라도 있으면, 직접 컬럼
  (`c.name`/`c.hostname`)·server.Server EAV·자식 EAV를 **resource_type 구분 CASE WHEN + config를
  `cc.configuration_id = c.resource_conf_id`로 조인 + `GROUP BY COALESCE(platform_resource_id, id)`**
  형태로 **결정적 생성**. Hostname 브릿지 조인 금지 명시. 두 경로가 이 공유 헬퍼를 호출(D-067 정신).
- **순수 server.Server EAV(OS-only 등)·비폼필 경로는 기존 브릿지 블록 유지** → 회귀 없음.
- **부수 발견/수정**: `query_generator._build_user_prompt`가 column_mapping 필터링 루프에서 누적
  리스트 `parts`를 `parts = col.split('.')`로 **덮어써**, schema_info+정규컬럼이 있는 폼필 프롬프트에서
  `## 사용자 질의`·`## 파싱된 요구사항` 섹션이 **유실**되던 기존 버그를 `col_parts`로 격리 수정.
  (멀티 경로는 누적 변수명이 `user_parts`라 무해했음.)

### 결정 (2차 정정: metric 통합 — GROUP BY 충돌 회귀 대응)

1차(config 전용 피벗 스켈레톤)를 넣자 공동존 재테스트에서 **`column "r.name" must appear in the
GROUP BY clause`** 에러 + 전체 빈칸으로 **회귀**. 원인: 사용률 필드(`CPU 평균/최고`, `메모리 평균/최고`)는
field_mapper가 **미매핑(None)** 으로 넘겨 `unmapped_fields` 블록(alias `r`/`s`, cmm_metric_stat_m
피벗)이 **별도로** 붙는데, 이게 내 config 피벗(alias `c`, GROUP BY) 스켈레톤과 **구조·alias 충돌** →
LLM이 둘을 섞다 `r.name`을 집계 밖에 둠. 프로필 정식 예시는 metric을 서브쿼리로 분리한 2-scope지만,
**단일 스코프로도 합칠 수 있다**(config·metric 이중 LEFT JOIN은 AVG/MAX/MIN 값에 불변 — 중복행이
같은 값이라 집계 결과 동일).

- **정정**: `build_multi_resource_pivot_block`에 `metric_fields`·`db_engine` 인자를 추가하여 사용률
  필드를 **같은 GROUP BY 스켈레톤에 접어 넣음**(`ROUND(AVG/MAX(CASE WHEN c.resource_type='server.Cpus'
  AND s.definition_name='Utilization' THEN s.avg_val END)…, 2)` + `LEFT JOIN cmm_metric_stat_m s
  ON s.resource_id=c.id`). 엔진별 소수 보존(PG `::numeric` / DB2 `CAST DECIMAL`).
- **`classify_metric_field(field)`**: metric 명사(cpu/메모리…)+집계어(평균/최고…)로 (resource_type,
  집계함수, 값컬럼) 분류. 두 경로가 unmapped(+metric_entries)에서 사용률 필드를 골라 피벗에 접고,
  **미매핑/metric 별도 블록에서 제외**해 충돌 원천 제거.
- 결과: identity+config+metric이 **하나의 GROUP-BY-valid 쿼리**(모든 SELECT식이 집계, 맨 컬럼 없음).
  기간(지난달)은 `s.stat_date`에 적용하도록 지시로 위임.

### 결정 (3차: 결정적 SQL 조립 — 프롬프트 유도 포기)

2차(통합 스켈레톤을 프롬프트로 강제)에서도 공동존 재테스트가 **서버 2~3중 중복(2만건)+config 빈칸**으로
실패. 원인: 스켈레톤을 프롬프트에 "제안"으로 넣어도 **프로필 few-shot 예시**("사용률 리스트" 예시는
`GROUP BY … TO_DATE(s.stat_date…)` 월별 + config를 PHYSICALCORE만 담는 `hi` 서브쿼리)와 **경쟁**해
LLM이 예시를 택함 → 월별 분해로 서버 중복 + LOGICALCORE/TotalSize 누락. 프롬프트 텍스트를 더 넣는
접근은 매 라운드 새 실패를 낳음(경쟁하는 지시가 늘어날 뿐).

- **결정**: 이 well-defined 폼필 쿼리(자식 리소스 EAV 포함)는 **코드가 runnable SQL을 직접 조립**하고
  **LLM을 우회**한다. `build_multi_resource_pivot_sql(...)`가 프로필의 검증된 조인 패턴 그대로(단, 사용률까지
  단일 GROUP BY로 합침) 완성 SQL을 생성: 스키마 한정(`db_schema`, DB2 대문자)·엔진별 소수캐스트·엔진별
  LIMIT/FETCH·`resolve_stat_month`로 기간(`s.stat_date`) 필터까지 결정적. 멀티(`_generate_sql`)는 자식
  EAV 감지 시 즉시 이 SQL을 return(프롬프트 미구성), 단일(`query_generator._try_build_form_fill_pivot_sql`)은
  LLM 호출 전 단락. **재시도(에러 컨텍스트) 시에만 LLM 폴백**(결정적 SQL이 실행 실패했을 때 에러 반영).
- 자식 EAV가 없는 일반 질의·비폼필은 종전 LLM 경로 유지(회귀 0). 2차의 프롬프트 블록
  (`build_multi_resource_pivot_block`)은 단일 경로 **재시도 폴백**으로만 잔존.
- **date 처리**: "지난달"/"전월"/"이번달" 등만 결정(연말↔연초 롤오버 포함), 그 외 표현은 월 필터 없이
  전체 월 평균(서버당 1행은 유지 — GROUP BY가 월을 포함하지 않으므로 중복 불가).

### 근거 / 한계

- LLM 비결정성·조각 블록 충돌 대신 **강제 지시 자체를 하나의 정답 쿼리로** 결정화(최근 방향과 일치).
- 한계: regular_entries가 entity 테이블(cmm_resource) 컬럼, 사용률 통계 테이블=`cmm_metric_stat_m`,
  키=`resource_id`/`definition_name='Utilization'`/`avg_val|max_val|min_val`라는 폴스타 규약 가정
  (gp/yd/b0 동형이라 성립). 기간 필터는 여전히 LLM이 `s.stat_date`에 채움.
- **교훈**: 조각난 강제 블록에 **또 다른 구조 강제(GROUP BY 피벗)를 얹으면 alias·집계 스코프가
  충돌**한다 — 폼필처럼 여러 필드류(식별/EAV/사용률)가 한 쿼리에 필요하면 **하나의 스켈레톤으로 통합**해
  생성해야 하며, 부분 블록을 켠 채로 두면 안 된다(반드시 상호배타 게이팅).
- 검증: 신규 단위(분류·피벗·metric 접힘·DB2 캐스트·GROUP BY 라인 전수 집계 + **결정적 SQL**: 단일
  GROUP BY·월 미포함·resource_type 구분·스키마 한정·엔진별 LIMIT/캐스트·stat_month 롤오버) + 단일
  `_try_build_form_fill_pivot_sql`·멀티 `_generate_sql` 실측(자식 EAV 감지 시 완성 SQL 반환), 회귀
  스위트 신규 실패 0, arch_check exit 0.
- **한계/교훈(3차)**: **프롬프트로 LLM을 유도하는 접근은 경쟁 지시(프로필 few-shot 등)가 있으면
  비결정적으로 실패**한다 — 스키마·조인이 고정된 well-defined 쿼리는 프롬프트 강제가 아니라 **코드가
  직접 조립**해야 안정적. 기간처럼 런타임 값이 필요한 부분만 결정적 파서로 처리하고, 파싱 불가 시에도
  구조(서버당 1행)는 깨지지 않게 설계.

### 결정 (4차 정정: 3차 결정적 빌더가 실 런타임에서 발동조차 안 됐음 — attr_rt 항상 빔)

**증상**: "은행 폴스타와 공동존 김포 폴스타" 폼필에서 라우팅 수정(멀티 DB 활성화, D-065 후속2·후속3) 후
b0(DB2)=`SQL0245N MAX 모호`, gp(PostgreSQL)=`aggregate functions are not allowed in GROUP BY`로 **둘 다** 실패.
두 에러 모두 `build_multi_resource_pivot_sql`가 내는 형태가 아니라(그 SQL은 두 엔진 모두 유효 — 실측 검증)
**LLM 폴백 SQL의 전형적 실수** → 3차 결정적 빌더가 발동하지 않았다는 뜻.

**근본 원인**: 결정적 빌더 게이트 `use_multi_resource_pivot = bool(child_eav)`의 `child_eav`는
`eav_attr_resource_types(schema_info)`(→`attr_rt`)에 의존하는데, 이 헬퍼가 `pattern["known_attributes"]`만
읽고 각 항목을 `isinstance(attr, dict)`로 검사했다. 그러나 **실 런타임 로더 `_load_manual_profile`은
known_attributes를 문자열 리스트로 평탄화**하고 원본 객체(`name`/`description`/resource_type 태그)를
`known_attributes_detail`에 보존한다(schema_analyzer.py:501). → 런타임에서 `attr_rt`가 **항상 빔** →
`child_eav=[]` → 결정적 빌더 **한 번도 발동 안 됨**(단일 `_try_build_form_fill_pivot_sql`·멀티 `_generate_sql`
공통) → 항상 LLM 폴백. **단일 경로(schema_analyzer 노드)도 같은 로더를 쓰므로** 단일 b0 성공은 결정적
빌더가 아니라 **LLM 폴백이 우연히 유효 SQL을 낸 것**. 멀티는 두 DB 모두 LLM이 잘못된 SQL 생성.

**왜 테스트가 못 잡았나**: `test_multi_resource_pivot.py`가 `known_attributes`를 **dict shape로 직접** 먹여
헬퍼가 통과했다. 실 런타임의 평탄화 shape(문자열+detail)를 재현하지 않아, 3차 이후 결정적 빌더가
프로덕션에서 죽어 있는데도 스위트는 초록. (D-066 계열의 "mock 통과·런타임 실패" 재발.)

- **수정**: `eav_attr_resource_types`가 `known_attributes_detail`을 **우선** 읽고, 없으면
  `known_attributes`로 폴백(원시 dict 구조 메타 호환). 단일 출처 헬퍼라 단일·멀티 경로가 동시에 살아남.
  이제 gp/b0/yd 모두 `attr_rt` 27건(LOGICALCORE→server.Cpus, TotalSize→server.Memory) → 결정적 빌더가
  각 DB의 스키마·엔진 방언으로 완성 SQL을 조립 → 이종 엔진 [b0, gp]도 각자 유효 SQL 반환.
- **회귀 테스트**: 실 런타임 shape(문자열+detail)와 실제 프로필 로드(`_load_manual_profile`) end-to-end
  단언을 추가(`test_resource_type_map_reads_flattened_known_attributes_detail`,
  `test_resource_type_map_from_real_manual_profiles`).
- **교훈**: 결정적 게이트가 **의존하는 데이터의 실 런타임 shape를 반드시 실측**하라 — 로더가 구조를 변형
  (평탄화)하면 mock 테스트가 통과해도 프로덕션에선 게이트가 죽는다. 부가 필드(`_detail`)를 만드는 변형
  로더가 있으면, 그 필드를 읽는 소비자와 **양쪽 shape 호환**을 테스트로 고정.

### 결정 (5차: 서버명/서버이름이 EAV Hostname으로 오매핑 — 결정적 교정 가드)

4차로 결정적 빌더가 발동하자, 이제 `column_mapping`이 **글자 그대로** 렌더된다. 그런데 폼 `서버 이름`이
`column_mapping["서버 이름"]="EAV:Hostname"`으로 매핑돼(생성 SQL:
`MAX(CASE WHEN cc.name='Hostname' THEN cc.stringvalue_short END) AS "서버 이름"`), `서버 이름`·`호스트네임`
**둘 다 hostname 값**으로 채워졌다. 프로필은 "'서버명/서버 이름'은 EAV Hostname이 아니라 등록명
컬럼(cmm_resource.name)"이라 명시하고 EAV Hostname synonyms도 `["EAV호스트명"]`로 축소돼 있으나,
**전역 유사어 `Hostname:[…,서버명,…]`가 미끼**가 되어 유사어 발견/LLM 추론 단계가 "서버 이름"을 Hostname에
붙였다(2026-06-10 기록의 `서버명→HOSTNAME` 전역 오염과 동일 계열, 그때는 filter_conditions만 우회).

- **인과**: 이 오매핑은 field_mapper 유사어/LLM 해소의 **기존·지속** 문제로, 4차(attr_rt 복원) 변경이 만든
  것이 아니다. 다만 결정적 빌더 활성화로 **잠재 오류가 일관되게 노출**됐다(LLM 경로는 프로필 가이드로 가끔
  마스킹). 올바른 방향은 LLM 마스킹 복원이 아니라 매핑을 결정적으로 교정.
- **수정**: 공유 헬퍼 `correct_servername_hostname_mapping(column_mapping, entity_table)` — 필드가
  서버명/서버이름/장비명/리소스명/등록명류(공백제거·소문자 정규화)인데 `EAV:Hostname`에 붙었으면
  `<entity>.name`(예: cmm_resource.name)으로 교정. 단일(`_try_build_form_fill_pivot_sql`)·멀티
  (`_generate_sql`) 두 경로가 분류 직전 호출(D-067 정신). `호스트네임` 등 호스트명 표면어는 name-term이
  아니라 불변. 유사어/Redis 상태·LLM 변동과 무관한 결정적 가드(비결정 매핑을 결정적으로 교정 — D-055 계열).
- **권장 후속(코드 아님)**: 전역 `global_synonyms.yaml`의 `HOSTNAME`/EAV `Hostname` words에서 `서버명`·`서버`
  제거(per-DB D-061/D-066후속2를 전역에 미러) + Redis 유사어 재동기화. 가드가 있으므로 non-blocking.
- 검증: `correct_servername_hostname_mapping` 단위(교정·호스트네임 불변·정규화·비Hostname 불변·entity 없음
  no-op) + 피벗 SQL 렌더 단언(`서버 이름`→`c.name`, `호스트네임`→`c.hostname`, `cc.name='Hostname'` 부재),
  회귀 신규 실패 0, arch exit 0.
- **교훈**: **결정성은 잠재 매핑 오류를 노출**한다(LLM의 우발적 교정을 제거) — 노출된 오류는 되돌리지 말고
  결정적 가드로 정면 교정. 프로필이 명시한 규칙(서버명=name)은 유사어 튜닝(Redis 의존·LLM 퍼지)보다
  **코드 가드로 못박는 것**이 반복 실패를 끝낸다.

### 결정 (6차: 재오염 자기강화 루프 차단 — 등록 가드 + 직접 컬럼 교정 확장)

5차 가드는 폼필 **출력**만 교정했고, Redis 유사어 오염 자체는 남았다. 조사 결과 **자기강화(self-reinforcing)
루프**가 확인됐다: (1) 전역/EAV 유사어에 `서버명→hostname`이 있음 → (2) field_mapper의 LLM 매핑이 "서버 이름"을
hostname으로 매핑 → (3) `_register_llm_mappings_to_redis`/`_register_llm_synonym_discoveries_to_redis`/
`apply_mapping_feedback_to_redis`가 그 매핑을 **사용자 확인 없이 Redis(전역+EAV+per-DB)에 자동 재등록** →
(4) 오염 강화 → 다음 턴 더 확실히 오매핑. 그래서 per-DB만 청소해도 씨앗에서 재번진다. 또한 5차 교정은
`EAV:Hostname`만 봐서 **직접 `*.hostname` 컬럼** 매핑(전역/per-DB 컬럼 유사어 경로)은 사각지대였다.

- **수정 A (등록 가드, 재오염 원천 차단)**: 공용 판정 `is_servername_to_hostname(field, column)`으로,
  서버명/서버이름류 → hostname(직접 컬럼 or EAV) 자동 등록을 **세 등록 함수 전부**에서 거부(skip+log).
  잘못된 연관이 애초에 Redis에 안 쌓여 루프가 끊긴다. 정상 필드(호스트명→hostname, IP주소 등)는 그대로 등록.
- **수정 B (교정 확장)**: `correct_servername_hostname_mapping`이 `EAV:Hostname`뿐 아니라 **bare 컬럼명이
  hostname인 직접 컬럼**(`is_hostname_target`)도 `<entity>.name`으로 교정 → 폼필 출력이 EAV·직접 컬럼 양쪽 안전.
- 판정 헬퍼(`is_servername_field`/`is_hostname_target`/`is_servername_to_hostname`)를 `query_gen_common`에
  단일 출처화하여 등록 가드·교정이 동일 규칙을 공유(D-067 정신).
- **효과**: ① Redis 재오염 안 됨(루프 원천 차단) ② 설령 어디서 오염돼도 폼필 출력은 교정 ③ Redis 일회성 청소로 끝.
- **주의**: 기존 테스트 `test_register_llm_mappings_to_redis_normal`이 **오염 동작(서버명→HOSTNAME 등록)을
  정답으로 단언**하고 있었다 → 정상 필드(호스트명)로 교체하고, 차단 회귀(`test_register_blocks_servername_to_hostname`)
  신설. 검증: 등록 가드 mock 테스트 + 판정/교정 단위(직접 컬럼 포함) 신규, 회귀 신규 실패 0, arch exit 0.
- **교훈**: LLM 자동등록이 **오염된 입력을 학습해 되쓰면 자기강화 루프**가 된다 — 출력 교정만으론 부족하고
  **등록(쓰기) 지점에서 결정적으로 차단**해야 근본 종결. 테스트가 버그 동작을 정답으로 굳혀두지 않았는지도 점검.

### 결정 (7차: 이종 엔진 CSV 칼럼 중복 + DB2 통계 스케일 제로필)

이종 엔진 폼필(공동존 gp/yd + 은행존 b0) **CSV 다운로드**에서 두 잔여 관찰:

1. **CSV 칼럼 중복(라틴 대소문자만 다름)**: `IP주소`/`ip주소`·`OS 종류`/`os 종류`·`CPU 평균`/`cpu 평균`처럼
   중복 칼럼이 생기고 각 존이 자기 표기 칼럼에만 채워짐. **Excel은 정상**(자체 정규화 매칭으로 흡수).
   원인: **DB2가 결과 칼럼의 라틴 문자를 소문자로 반환** → gp="IP주소" vs b0="ip주소". 순수 한글 칼럼
   (서버 이름/메모리 용량 등)은 소문자화 대상이 없어 중복 안 됨(관찰과 정확히 일치). `_merge_results`가
   각 DB 행을 키 그대로 append해 원본 병합(query_results)·그 CSV가 분리됐다. 수정: `_merge_results`가
   칼럼명을 정규화(소문자·공백/언더스코어 제거) 기준 **canonical 이름(양식 필드 우선, 없으면 첫 등장 키)**으로
   통일. Excel writer의 정규화 매칭 로직과 동형. 회귀: `test_multi_db_merge.py`.
2. **DB2 통계 엑셀 제로필(6.51→6.51000000000000000000, CSV는 정상)**: gp는 같은 칼럼에서 정상이므로
   템플릿 셀 포맷이 아니라 **값 타입** 문제. **DB2 `AVG(DECIMAL)`은 스케일을 크게 확장**(scale~18)해
   `ROUND(x,2)`로 값은 2자리로 반올림돼도 **타입 스케일이 남아** trailing zero로 직렬화된다(D-066 후속5의
   `_normalize_cell_value` Decimal→float로도 고스케일 Decimal은 float 변환돼도 원천은 SQL). 수정:
   `_metric_select_line` DB2 분기를 `CAST(ROUND(AVG/MAX(...), 2) AS DECIMAL(15,2))`로 **스케일 2 고정**
   (PostgreSQL은 `::numeric` 유지). `decimal_cast_example`도 동일 미러. 회귀: `test_db2_metric_scale_fixed_to_two`.
- **교훈**: 멀티 엔진 결과 병합은 **엔진별 칼럼명 표기 차이(DB2 소문자화)**를 정규화해 통일해야 CSV/원본이
  안 깨진다(Excel만 보고 정상으로 오판 금지). DB2 집계는 **스케일 확장**을 최종 CAST로 고정.

---

## D-069. 어드민 접근 통합 RBAC (Plan 59 Part A · 방향 A)

| 항목 | 내용 |
|------|------|
| **결정일** | 2026-07-14 |
| **상태** | 확정 (사용자 승인: 방향 A 1안) |
| **관련 결정** | D-026(사용자 인증·시크릿 공유·type 구분 원의도), D-070(시크릿 분리), D-071(하드닝), D-082(존 RBAC가 재사용) |

### 배경 / 문제

두 개의 독립 인증 체계가 하나의 멘탈 모델로 봉합되지 않은 채 병존했다. (A) 어드민 진입에 별도
운영자 계정 로그인이 또 필요했고, (B) 회원 리스트의 "어드민 권한 부여"가 무효했다 —
`UserRole.ADMIN`이 정의만 있고 어떤 게이트도 열지 않는 **죽은 필드**였기 때문(소비처 0건).

### 결정

어드민 접근을 **DB `user.role == ADMIN`**으로 판정한다(통합 RBAC). 고정 운영자 계정은
**break-glass seed 1개**로 축소한다.

- 신규 `dependencies.require_admin_user` 통합 가드: ① 개발 모드(AUTH_ENABLED=false) 우회
  (Plan 39 원칙) ② break-glass 운영자 토큰(type="admin", admin 시크릿) 허용(DB 장애 대비)
  ③ 사용자 토큰 + DB 실시간 role==admin. `admin.py`(15개)·`schema_cache.py`(14개) 엔드포인트를
  `require_admin`→`require_admin_user`로 교체.
- 기동 시 활성 관리자 0명이면 env 크레덴셜로 seed admin을 멱등 생성(`server._seed_admin_user`).
- **최소-1-admin 가드**: 마지막 활성 관리자의 강등·비활성화·삭제 차단(`_ensure_not_last_active_admin`).
- 프론트: `role==admin`(또는 개발 모드)일 때만 어드민 진입 링크 노출, `admin.js`가 사용자 토큰으로
  진입 허용(별도 admin_token 없을 때 폴백).

### 근거 / 대안

방향 B(운영자 계정 분리 유지)는 "권한 부여로 어드민 접근"이라는 사용자 기대를 충족 못 함. 방향 A는
죽은 필드를 부활시켜 사용자 멘탈 모델과 일치. break-glass seed로 DB 장애 시 진입성도 보존(하이브리드).

### 번호

Plan 59는 작성 시점(2026-07-01, 최대 D-056) 기준 D-064를 제안했으나, 이후 폼필 작업이 D-060~D-068을
소비(2026-07-09~13) → 충돌. 등재 직전 `## D-` 헤더+변경 이력 표+CLAUDE.md grep으로 최대 D-068 확인,
**D-069** 부여(Known Mistakes 2026-06-25·2026-06-29 번호 규칙 준수).

---

## D-070. 운영자 토큰 type 검증 + 사용자/운영자 시크릿 분리 (권한 상승 차단)

| 항목 | 내용 |
|------|------|
| **결정일** | 2026-07-14 |
| **상태** | 확정 (보안 즉시 수정, 방향 무관) |
| **관련 결정** | D-026(시크릿 공유+type 구분 원의도가 미구현이었음), D-069 |

### 배경 / 문제 [보안 결함]

`verify_admin_token`이 토큰의 `sub`만 확인하고 **`type`·`role`을 검증하지 않았다.** 그런데 사용자
토큰도 **동일 시크릿**(`config.admin.jwt_secret`)으로 서명되어(D-026이 "type으로 구분"한다고 결정했으나
검증이 누락됨), **임의의 일반 사용자 JWT로 모든 `/admin/*`·`schema_cache` 운영자 API를 통과**할 수 있었다
(`.env` 수정, DB 접속정보 변경, 사용자 삭제, 비밀번호 리셋 등). 프론트에 별도 로그인 폼이 있을 뿐 API
계층이 이미 뚫려 있었다.

### 결정

- **① `type` 검증**: `verify_admin_token`이 `payload.get("type") != "admin"`이면 401(D-026 원의도 강제).
  이 한 줄로 사용자 토큰 통과가 즉시 차단된다.
- **② 시크릿 분리**: `AuthConfig.jwt_secret`(env `AUTH_JWT_SECRET`) 신설. 사용자 토큰은
  `config.auth.jwt_secret`으로 서명(`_create_user_token`)·검증(`dependencies._verify_user_token`),
  운영자 토큰은 `config.admin.jwt_secret` 유지 → 교차 서명 자체가 불가.
- **마이그레이션**: 배포 시 기존 사용자 토큰 무효화(재로그인). 사용자 확인 결과 배포마다 재로그인 수용 →
  한시적 이중 검증 미도입.

### 근거

①은 방향 선택과 무관한 P0 핫픽스. ②는 심층 방어(시크릿 분리로 교차 서명 원천 차단). 회귀 테스트
`tests/test_api/test_admin_rbac.py`(사용자 토큰 401 / 운영자 토큰 200 / 시크릿 분리).

---

## D-071. 기본 크레덴셜·시크릿 하드닝 (운영 모드 기동 거부)

| 항목 | 내용 |
|------|------|
| **결정일** | 2026-07-14 |
| **상태** | 확정 (사용자 승인: 강한 거부, JWT 시크릿 사전 기입 권장) |
| **관련 결정** | D-070, Known Mistakes 2026-06-10(os.getenv가 .env/.encenv 미반영) |

### 결정

- `AdminConfig`의 기본 크레덴셜 `admin`/`admin123`을 **제거**(빈 문자열 기본값).
- 운영 모드(AUTH_ENABLED=true)에서 크레덴셜·시크릿 미설정 시 **기동 거부**
  (`server._validate_production_secrets`): `ADMIN_USERNAME`/`ADMIN_PASSWORD`,
  `ADMIN_JWT_SECRET`, `AUTH_JWT_SECRET`가 모두 명시 설정되어야 한다.
- 개발 모드는 현행 유지(jwt_secret 미설정 시 임시 랜덤 생성).

### 구현 주의 (Known Mistakes 반영)

`os.getenv`는 pydantic-settings의 `.env`/`.encenv` 로딩 값을 못 본다(2026-06-10). 따라서 "명시 설정
여부"를 `AdminConfig`/`AuthConfig`의 **`PrivateAttr _jwt_secret_explicit`** 플래그(model_post_init에서
자동 생성 전에 기록)와 pydantic 필드로 판정한다. `_validate_production_secrets`는 이 플래그·필드만 읽어
파일 기반 설정도 정확히 인식한다.

---

## D-082. 알림 지역 스코프 RBAC + 쿠키 SSE 인증 + 존 필터 (Plan 59 Part C)

| 항목 | 내용 |
|------|------|
| **결정일** | 2026-07-14 |
| **상태** | 확정 (사용자 승인: 데이터 모델 1, SSE 인증 쿠키(B)) |
| **관련 결정** | D-069(통합 RBAC 재사용), D-026(인증 불변 · 인가만 추가), D-053(존↔db_id 라우팅 교훈) |

### 배경 / 문제

알림 SSE 스트림(`/alarm/notifications/stream`)이 **인증 의존성 없이** 웹 접속자 전원에게 무차별
브로드캐스트되고 있었다. 요구: 지역 스코프 역할(관리자=전 존, 공동존/은행존 운영자=해당 존만,
일반=수신 안 함, **중복 할당 가능**).

### 결정

- **데이터 모델 1**: `UserRole`(어드민 게이트, D-069) 유지 + 별도 **`User.alarm_zones: list[str]`**
  신규 필드(중복 할당=리스트, 빈 목록=일반). 모델 2(역할 리스트 일반화)는 Part A 소비처 광범위 변경으로
  회귀 위험↑라 기각.
- **존↔db_id 단일 출처**: `src/routing/zones.py`(`ZONE_GONGJON=[polestar_cm_gp, polestar_cm_yd]`,
  `ZONE_BANKJON=[polestar_b0]`, `zone_to_db_ids`/`db_id_to_zone`/`normalize_zones`). 신규 존/DB는 여기만 갱신.
- **쿠키 기반 SSE 인증**(사용자 선택 B): 로그인 시 JWT를 `HttpOnly` 쿠키(`user_token`)로도 세팅
  (EventSource가 Authorization 헤더를 못 실음). `dependencies.resolve_stream_user`가 쿠키→헤더→쿼리
  토큰 순으로 해소. `alarm_zones_for_user`가 존 집합 산출(admin·개발=전 존, 운영자=해당 존, 일반=∅).
  존 집합이 비면 **403**, 아니면 `event_generator`가 `db_id_to_zone(event.db_id)`로 구독자 존만 통과.
- **프론트**: 존 권한이 있는 사용자만 EventSource를 열고(권한 없으면 미구독=403 재연결 루프 차단),
  헤더에 개인 **알림 수신 토글**(localStorage) 제공.
- **SSO 보존**(필수 제약): 로그인/인증 흐름 불변, 역할/존은 인증 성공 후 인가 계층에만 추가.

### 근거

기존 `allowed_db_ids` 인프라(토큰 노출·DDL)와 동형이나, 조회 권한과 알림 존은 별개 축이라 전용 필드로
분리. 구독자측 필터로 충분(트래픽 소규모)하며 alarm_bus 존 토픽화는 향후 옵션.

### 번호

최초 등재(2026-07-14) 시 grep 최대 D-071 확인 → D-072 부여(Plan 59 §20의 "후보 D-069~"를 따름).
**2026-07-15 재배정: D-072 → D-082** — multiintent 병합 준비 중 팀장 브랜치(origin/multiintent)가
D-072~076(Plan 61)·D-077~081(Plan 60)을 선점·예약한 것을 확인, 팀장 우선 원칙에 따라 다음 빈 번호 D-082로 이동.

---

## D-083. 보호 root 계정 + 감사 로그 로테이션 배선 + 어드민 진입 규약 정정 (Plan 59-a)

| 항목 | 내용 |
|------|------|
| **결정일** | 2026-07-15 |
| **상태** | 확정 (사용자 승인) |
| **관련 결정** | D-069(통합 RBAC·최소-1-admin 가드 보완), D-070(진입 규약), D-082(알림그룹 UI 완성) |

### 배경 / 문제

Plan 59-a 실테스트에서 세 가지가 드러났다. (1) **어드민 재로그인**: 미인증 상태로 `/admin`에 오면 admin.js가
`/admin/login`(env 운영자 전용 break-glass)으로 유도 → DB 사용자(role==admin 포함)가 그 창구에서 로그인
실패. (2) **잠김 위험**: 유일한 관리자가 자신을 강등·삭제하면 접속 가능한 관리자가 0명이 될 수 있음(최소-1-admin
가드는 있으나 "상시 불변 root"라는 명시적 보장 부재). (3) **감사 로그 로테이션 미작동**: `AuditConfig.retention_days`
설정과 `cleanup_old_logs()` 구현은 있으나 **호출부가 전역 0건** → 오래된 로그가 영영 삭제되지 않음.

### 결정

- **어드민 진입 규약(개선 1, D-069/D-070 완성)**: 정상 진입은 `/login` 단일 창구 → role==admin이면 ADMIN 버튼
  → `/admin`. admin.js의 미인증 리다이렉트를 `/admin/login`→**`/login?next=/admin`**으로 교정, 403(비관리자)은
  `/`로. `/admin/login`은 **링크 없는 break-glass 전용**(DB 장애 시 env 계정)으로만 유지. login.html은 `next`
  복귀(오픈 리다이렉트 방지 — 같은 출처 상대경로만).
- **보호 root 계정(개선 3)**: `User.is_protected: bool`(신규) + DDL `ALTER TABLE auth_users ADD COLUMN IF NOT
  EXISTS is_protected BOOLEAN NOT NULL DEFAULT FALSE`(멱등). seed admin(`_seed_admin_user`)을 `is_protected=True`로
  생성 → env 운영자 계정이 **일반 로그인 가능한 상시 root**가 된다. 백엔드 가드: 보호 계정의 **역할·상태 변경·
  삭제·PW초기화 거부(403)**, **부서·알림그룹은 허용**(정책 교정 목적, 사용자 확정). 프론트: 해당 행 컨트롤 disabled
  + 자물쇠 표시. (표식은 컬럼 방식 — username 변경에도 보호가 계정에 고정.)
- **알림그룹 UI(개선 2, D-082 완성)**: 사용자 관리 테이블에 K리전(공동존)·K리전(은행존) 체크박스. 중복 가능,
  둘 다 해제 시 `alarm_zones=[]` 명시 전송(=일반). 관리자 행은 전 존 수신이라 비활성.
- **부서 편집(개선 4)**: 사용자 관리 부서 셀을 인라인 편집으로(백엔드는 기존 `UpdateUserRequest.department` 재사용).
- **감사 로그 로테이션(개선 5)**: `retention_days`(기본 90, `AUDIT_RETENTION_DAYS`)를 기준으로 (A) 기동 시 1회
  + (B) 하루 1회 주기 백그라운드 태스크 + (C) 수동 엔드포인트 `POST /admin/audit/cleanup`(어드민 버튼). `<=0`이면
  비활성. DB 감사 로그 대상(JSONL 파일 로테이션은 추후).

### 근거 / 대안

보호 표식은 `is_protected` 컬럼(권장) vs `user_id==ADMIN_USERNAME` 매칭 중 **컬럼 방식** 채택 — 운영자 ID를
바꿔도 보호가 계정에 남아 안정적. 진입 창구 이원화(`/admin/login`을 정상 경로로 노출)는 혼동·로그인 실패의
원인이므로 단일 창구(`/login`)로 정리하고 break-glass는 비상용으로만 격리.

### 번호

최초 등재(2026-07-15) 시 `## D-` 헤더 + 변경 이력 표 + CLAUDE.md grep으로 최대 D-072 확인 → D-073 부여.
**2026-07-15 재배정: D-073 → D-083** — multiintent 병합 준비 중 팀장 브랜치(origin/multiintent)가
D-072~076(Plan 61)·D-077~081(Plan 60)을 선점·예약한 것을 확인, 팀장 우선 원칙에 따라 다음 빈 번호 D-083으로 이동.

---

## 변경 이력

| 날짜 | 결정 ID | 변경 내용 |
|------|---------|----------|
| 2026-07-15 | D-083 | **보호 root 계정 + 감사 로그 로테이션 + 어드민 진입 규약 정정 (Plan 59-a)**: 실테스트로 3결함 확정·수정. (1)**어드민 재로그인**: 미인증 `/admin` 진입이 `/admin/login`(env break-glass 전용, DB 사용자 인증 안 함)으로 유도돼 role==admin 계정도 로그인 실패 → admin.js 리다이렉트를 `/login?next=/admin`으로, 403은 `/`로 교정. `/admin/login`은 링크 없는 break-glass로만 유지. login.html `next` 복귀(오픈 리다이렉트 방지). (2)**보호 root**: `User.is_protected` 컬럼(+멱등 DDL) 신설, seed admin을 `is_protected=True`로 생성(env 계정=상시 일반 로그인 root). 역할/상태 변경·삭제·PW초기화 **403 차단**, 부서·알림그룹은 허용. 프론트 해당 행 disabled+자물쇠. (3)**감사 로테이션**: `cleanup_old_logs`가 호출부 0건이라 무효였음 → 기동 1회 + 하루 1회 태스크 + 수동 `POST /admin/audit/cleanup`(버튼), `AUDIT_RETENTION_DAYS`(기본90). +알림그룹 체크박스 UI(D-082 완성)·부서 인라인 편집. 수정 `domain/user.py`·`user_repository.py`·`api/server.py`·`schemas.py`·`routes/admin.py`·`routes/user_auth.py` 없음·`static/js/admin.js`·`static/login.html`·`admin/dashboard.html`·`.env.example`. 검증 `test_plan59a.py` 10건(보호 가드·seed·로테이션), 회귀 신규 실패 0(기존 6 test_routes=MagicMock 픽스처), arch exit 0. 상세 `## D-083`(구 D-073 — 팀장 브랜치 Plan 61 예약과 충돌해 재배정). |
| 2026-07-14 | D-082 | **알림 지역 스코프 RBAC + 쿠키 SSE 인증 + 존 필터 (Plan 59 Part C)**: 무인증 브로드캐스트하던 `/alarm/notifications/stream`을 지역 스코프로 인가(관리자=전존/공동존·은행존 운영자=해당존/일반=403, 중복 할당 가능). 데이터 모델 1: `User.alarm_zones: list[str]` 신규 필드(+DDL `ALTER TABLE ADD COLUMN IF NOT EXISTS alarm_zones TEXT[]` 멱등·repo·to_auth_dict·schemas·admin update). 존↔db_id 단일 출처 `src/routing/zones.py`(gongjon=[gp,yd]/bankjon=[b0]). 쿠키 인증(사용자 선택 B): 로그인 시 HttpOnly `user_token` 쿠키 세팅(EventSource 헤더 제약), `resolve_stream_user`(쿠키→헤더→쿼리)+`alarm_zones_for_user`(admin·dev=전존)로 존 산출→빈 집합 403, `event_generator`가 `db_id_to_zone`로 구독자 존만 통과. 프론트: 권한자만 EventSource open(403 루프 차단)+수신 토글. SSO 보존(인증 불변·인가만 추가). 수정 `routing/zones.py`(신규)·`domain/user.py`·`user_repository.py`·`api/server.py`(DDL)·`dependencies.py`·`routes/alarm.py`·`routes/user_auth.py`(쿠키)·`routes/admin.py`·`schemas.py`·`static/js/app.js`·`index.html`. 검증 `test_alarm_zone_rbac.py` 16건(존 격리·중복=전존·일반 403·쿠키 인증), 회귀 신규 실패 0, arch exit 0. 상세 `## D-082`(구 D-072 — 팀장 브랜치 Plan 61 예약과 충돌해 재배정). |
| 2026-07-14 | D-069~071 | **어드민 접근 통합 RBAC + 권한 상승 차단 + 크레덴셜 하드닝 (Plan 59 Part A)**: **D-070①(P0 보안)** `verify_admin_token`이 `type`·`role` 미검증 + 사용자/운영자 토큰 동일 시크릿 서명 → 일반 사용자 JWT로 모든 `/admin/*`·schema_cache 통과 가능(권한 상승). `type=="admin"` 검증 추가로 즉시 차단(D-026 원의도 강제). **D-070②** 사용자 토큰을 `AuthConfig.jwt_secret`(env `AUTH_JWT_SECRET`)로 분리 서명·검증(교차 서명 불가, 배포 시 재로그인 수용). **D-069(통합 RBAC·방향 A)** 어드민 접근=DB `role==admin` 판정, 신규 `require_admin_user` 가드(개발 우회+break-glass 운영자 토큰+role==admin)로 admin.py 15개·schema_cache.py 14개 교체, 죽은 `UserRole.ADMIN` 부활(증상 B 해소), 기동 시 seed admin 멱등 부트스트랩, 최소-1-admin 가드. 프론트 어드민 링크 role 게이팅+admin.js 사용자 토큰 폴백. **D-071(하드닝)** 기본 크레덴셜 admin/admin123 제거, 운영 모드 미설정 시 기동 거부(`_validate_production_secrets`, `os.getenv` 대신 `PrivateAttr _jwt_secret_explicit`로 판정 — 2026-06-10 교훈). 수정 `admin_auth.py`·`dependencies.py`·`config.py`·`routes/admin.py`·`schema_cache.py`·`routes/user_auth.py`·`server.py`·`static/js/{app,admin}.js`·`index.html`. 검증 `test_admin_rbac.py` 13건, 회귀 신규 실패 0(기존 6 test_routes 실패는 MagicMock 픽스처 pre-existing), arch exit 0. 번호: Plan 제안 D-064~066이 폼필 D-060~068에 선점됨 → 최대 grep 후 D-069~071 재부여. 상세 `## D-069`·`## D-070`·`## D-071`. |
| 2026-07-13 | D-068 7차 | **이종 엔진 CSV 칼럼 중복 + DB2 통계 스케일 제로필**: 공동존+은행존 폼필 CSV에서 (1) `IP주소`/`ip주소` 등 라틴 대소문자만 다른 **중복 칼럼**(각 존이 자기 표기에만 채움, Excel은 정상)—원인 **DB2가 결과 칼럼 라틴 문자를 소문자로 반환**, 순수 한글 칼럼은 무영향. 수정 `_merge_results`가 정규화 기준 canonical(양식 필드) 이름으로 통일. (2) **DB2 통계 엑셀 제로필**(6.51→6.51000…, CSV 정상, gp 같은 칼럼 정상=값 타입 문제)—원인 **DB2 `AVG(DECIMAL)` 스케일 확장**으로 ROUND(x,2)해도 타입 스케일 잔존. 수정 `_metric_select_line` DB2를 `CAST(ROUND(...,2) AS DECIMAL(15,2))`로 스케일 고정(PG는 ::numeric 유지). 교훈: 멀티 엔진 병합은 엔진별 칼럼 표기차(DB2 소문자화) 정규화 필수(Excel만 보고 오판 금지), DB2 집계 스케일은 최종 CAST 고정. 수정 `nodes/multi_db_executor.py`·`utils/query_gen_common.py`. 검증 `test_multi_db_merge.py`+`test_db2_metric_scale_fixed_to_two`, 회귀 신규 실패 0, arch exit 0. 상세 `## D-068` 7차. |
| 2026-07-13 | D-068 6차 | **재오염 자기강화 루프 차단(등록 가드) + 직접 컬럼 교정 확장**: 5차 가드는 폼필 출력만 교정, Redis 유사어 오염은 잔존. 루프 확인: 전역/EAV에 `서버명→hostname` 존재 → LLM이 "서버 이름"을 hostname 매핑 → `_register_llm_mappings_to_redis`/`_register_llm_synonym_discoveries_to_redis`/`apply_mapping_feedback_to_redis`가 **사용자 확인 없이 자동 재등록**(전역+EAV+per-DB) → 오염 강화 → 재오매핑(per-DB만 청소해도 씨앗에서 재번짐). 또 5차 교정은 `EAV:Hostname`만 봐 **직접 `*.hostname` 컬럼**은 사각지대. 수정: (A) 공용 판정 `is_servername_to_hostname`로 서버명류→hostname(컬럼/EAV) 자동 등록을 **세 등록 함수 전부**에서 거부(재오염 원천 차단), (B) `correct_servername_hostname_mapping`이 직접 hostname 컬럼(`is_hostname_target`)도 `<entity>.name`으로 교정. 판정 헬퍼는 `query_gen_common` 단일 출처. 기존 테스트가 오염 동작(서버명→HOSTNAME 등록)을 정답으로 단언 → 정상 필드로 교체+차단 회귀 신설. 교훈: LLM 자동등록이 오염 입력을 학습해 되쓰면 자기강화 루프 — 출력 교정만으론 부족, **쓰기 지점 결정적 차단** 필요. 수정 `utils/query_gen_common.py`·`document/field_mapper.py`. 검증 신규 단위+mock 등록 가드, 회귀 신규 실패 0, arch exit 0. 상세 `## D-068` 6차. |
| 2026-07-13 | D-068 5차 | **서버명/서버이름이 EAV Hostname으로 오매핑 → 서버 이름·호스트네임 둘 다 hostname**: 4차로 결정적 빌더가 발동하자 `column_mapping["서버 이름"]="EAV:Hostname"`이 글자 그대로 렌더(`cc.name='Hostname'`)돼 두 칼럼 모두 hostname 값. 원인: 프로필은 서버명=cmm_resource.name으로 명시하나 **전역 유사어 `Hostname:[…,서버명,…]`가 미끼**가 되어 유사어/LLM 해소가 "서버 이름"을 Hostname에 붙임(2026-06-10 `서버명→HOSTNAME` 전역 오염 동일 계열). 이 오매핑은 field_mapper의 기존·지속 문제(4차 변경이 만든 게 아님)이나 결정성이 잠재 오류를 노출. 수정: 결정적 가드 `correct_servername_hostname_mapping`(서버명/서버이름/장비명/리소스명/등록명류가 EAV:Hostname이면 `<entity>.name`으로 교정)을 단일·멀티 두 경로가 분류 직전 호출, 호스트명 표면어는 불변. 권장 후속: 전역 유사어 `HOSTNAME`/EAV `Hostname`에서 `서버명`·`서버` 제거+Redis 재동기화(가드로 non-blocking). 교훈: 결정성은 잠재 매핑 오류를 노출하니 되돌리지 말고 **프로필 명시 규칙을 코드 가드로 못박아** 유사어 튜닝(Redis/LLM 의존)의 반복 실패를 끝냄. 수정 `utils/query_gen_common.py`·`nodes/query_generator.py`·`nodes/multi_db_executor.py`. 검증 신규 단위 6+피벗 렌더 단언, 회귀 신규 실패 0, arch exit 0. 상세 `## D-068` 5차. |
| 2026-07-13 | D-068 4차 | **3차 결정적 빌더가 실 런타임에서 발동조차 안 됐음**: "은행+공동존 김포" 폼필(라우팅 수정으로 멀티 활성화 후) b0=`MAX 모호`·gp=`GROUP BY에 집계`로 둘 다 실패 — 둘 다 `build_multi_resource_pivot_sql`가 아니라 **LLM 폴백 SQL**의 실수 → 결정적 빌더 미발동. 원인: 게이트 `child_eav`가 의존하는 `eav_attr_resource_types`가 `known_attributes`(항목=dict 가정)만 읽는데, 실 로더 `_load_manual_profile`은 이를 **문자열로 평탄화**하고 원본을 `known_attributes_detail`에 보존 → 런타임 `attr_rt` **항상 빔** → child_eav=[] → 결정적 빌더 **한 번도 안 돎**(단일·멀티 공통, 단일 b0 성공은 LLM 우연). 테스트는 dict shape만 먹여 통과(mock 통과·런타임 실패). 수정: `eav_attr_resource_types`가 `known_attributes_detail` 우선 읽고 없으면 known_attributes 폴백 → gp/b0/yd attr_rt 27건 복원, 결정적 빌더가 각 DB 방언으로 조립(이종 엔진 각자 유효 SQL). 회귀: 평탄화 shape+실 프로필 로드 end-to-end 단언 추가. 교훈: 결정적 게이트가 의존하는 데이터의 **실 런타임 shape 실측** 필수 — 변형 로더가 있으면 소비자와 양쪽 shape 호환을 테스트로 고정. 수정 `utils/query_gen_common.py`. 상세 `## D-068` 4차. |
| 2026-07-13 | D-068 | **폼필 EAV 강제 SELECT의 resource_type 인지 다중 리소스 피벗**: 양식 `CPU 코어 수`(LOGICALCORE=server.Cpus)·`메모리 용량`(TotalSize=server.Memory)이 빈칸(공동존 멀티·여의도 단일 공통). 생성 SQL에 `MAX(CASE WHEN cc.name='LOGICALCORE' ...)` 피벗은 있으나 NULL. 원인: EAV 강제 블록(query_generator/multi_db_executor 복제)이 (1) resource_type 구분 없이 `cc.name='...'`만 생성, (2) 조인 힌트가 `value_joins`(Hostname/IPaddress=server.Server만) 기반이라 config를 **서버 행 resource_conf_id에만** 붙임 → 자식 리소스(server.Cpus/Memory) 속성이 영영 NULL. 프로필 query_example엔 올바른 멀티리소스 피벗이 있으나 강제 블록의 틀린 명시 지시가 예시를 이김(사용률 필드 강제 제외 D-066 후속3과 동일 계열). 수정: 공유 헬퍼 `eav_attr_resource_types`(설명 `[resource_type: …]` 파싱)+`build_multi_resource_pivot_block`(자식 EAV 있으면 resource_type 구분 CASE WHEN + `cc.configuration_id=c.resource_conf_id` 조인 + `GROUP BY COALESCE(platform_resource_id,id)` 결정적 생성, 브릿지 조인 금지 명시)을 두 경로가 호출(D-067 정신). 순수 server.Server EAV·비폼필은 기존 블록 유지(회귀 0). **2차 정정(GROUP BY 회귀 대응)**: 1차 config-전용 피벗을 넣자 공동존 재테스트에서 `column "r.name" must appear in the GROUP BY clause`+전체 빈칸 회귀 — 사용률 필드가 미매핑 경로로 **별도 metric 블록**(alias r/s)이 붙어 config 피벗(alias c, GROUP BY)과 alias·스코프 충돌. 프로필 정식 예시는 2-scope(metric 서브쿼리)지만 **단일 스코프로 합쳐도 config·metric 이중 조인이 AVG/MAX에 불변**이라 유효 → `build_multi_resource_pivot_block(metric_fields, db_engine)`로 사용률을 **같은 GROUP BY에 접어 넣고**(`ROUND(AVG/MAX(CASE WHEN c.resource_type=... AND s.definition_name='Utilization' THEN s.avg_val END)…)`+`LEFT JOIN cmm_metric_stat_m s ON s.resource_id=c.id`, 엔진별 소수캐스트), `classify_metric_field`로 골라 미매핑/metric 별도 블록에서 제외(상호배타 게이팅). 결과=identity+config+metric 하나의 GROUP-BY-valid 쿼리. **3차 정정(결정적 SQL 조립)**: 2차(통합 스켈레톤을 프롬프트 강제)도 공동존이 **서버 2~3중 중복(2만건)+config 빈칸** 재발 — 프롬프트 "제안"이 프로필 few-shot("사용률 리스트" 예시=월별 `GROUP BY stat_date`+config PHYSICALCORE만 담는 서브쿼리)과 경쟁해 LLM이 예시를 택함. → 폼필 다중리소스 쿼리(자식 EAV 포함)를 **코드가 runnable SQL로 직접 조립·LLM 우회**: `build_multi_resource_pivot_sql`(스키마 한정·엔진별 캐스트/LIMIT·`resolve_stat_month`로 s.stat_date 기간필터, 단일 GROUP BY=서버당 1행). 멀티 `_generate_sql`은 자식 EAV 감지 시 즉시 return, 단일 `_try_build_form_fill_pivot_sql`은 LLM 전 단락, 재시도 시만 LLM 폴백. **부수 수정**: `_build_user_prompt` 필터링 루프가 누적리스트 `parts`를 `parts=col.split('.')`로 덮어써 `## 사용자 질의`·`## 파싱된 요구사항`이 유실되던 기존 버그를 `col_parts`로 격리(멀티는 `user_parts`라 무해). 미해결: 증상 1(b0 OS 미SELECT)은 매핑 실패 가설 — field_mapper 로그 확인 대기. 수정 `utils/query_gen_common.py`·`nodes/query_generator.py`·`nodes/multi_db_executor.py`. 검증 `test_multi_resource_pivot.py` 18건+단일/멀티 실측(서버당 1행·월 미포함·엔진별 SQL), 회귀 신규 실패 0, arch exit 0. 상세 `## D-068`. |
| 2026-07-09 | D-067 | **재발 방지 드리프트 가드(테스트 전용)**: 같은 결함 반복은 regression이 아니라 **중복**(gp/yd/b0 near-duplicate 프로필 3벌=gp/yd는 567줄 중 실질 1줄 차이 + query_generator/multi_db_executor SQL생성 2벌) → 한 복사본만 패치해 나머지에 잔존(D-061 gp/yd 누락, D-066 경로 비대칭). 근본해소 A(프로필 상속)는 부담 커 보류, 저비용 가드 도입. **B**(`test_polestar_profile_consistency.py`): B-1 3프로필 공통 불변식(EAV Hostname 식별어 없음·name/hostname 분리·월별 metric 예시·환각패턴 없음·source=manual), B-2 gp/yd 등가성(주석 제외 실질차이 allowlist뿐). **C**(`test_query_gen_parity.py::TestCrossPathParity`): 두 경로가 동일 공유헬퍼 참조·예시 주입·LIMIT 대칭. 한계: "아는 결함 재발"만 감지(신규 유형은 불변식 추가 필요). 부정검증(누출·divergence 주입시 실패) 확인. 프로덕션 코드 미변경. 검증 B 16+C 3 통과, arch exit 0. 상세 `## D-067`. |
| 2026-07-09 | D-066 | **단일/멀티 DB SQL 생성 경로 동등화(few-shot 예시·전체조회 LIMIT 공유)**: "공동존 전체 서버+월간 CPU/메모리 사용률" 폼필이 gp/yd로 매핑됐으나 환각 SQL(`cmm_metric_stat_h` 서버행 직접조인+`definition_name='CPUUtilization'`)로 data_insufficient·NULL·LIMIT 1000. 은행존(b0)은 정상. 근본원인=DB 개수가 경로를 가름(`is_multi_db=len(targets)>1`): 단일(b0)→`_run_single_db_pipeline`→query_generator(프로필 `query_examples` few-shot 주입+"전체" LIMIT 상향+풀 검증)로 올바른 `cmm_metric_stat_m` 피벗 생성; 멀티(gp+yd)→`multi_db_executor`(예시 미주입·default_limit 1000 고정·간이검증)라 field_mapper 가짜 컬럼 매핑을 그대로 따름. b0가 동작한 건 few-shot 예시가 가짜 매핑을 이겼기 때문(스키마 차이 아님). 수정(RC1+RC4): 공용 `src/utils/query_gen_common.py`(`build_query_examples_block`·`resolve_query_limit`)로 두 경로 단일 출처화 — query_generator 인라인 로직 대체(동작 동일), multi_db_executor `_generate_sql`이 예시 append+`effective_limit` 사용. RC2(field_mapper 사용률→가짜컬럼)·RC3(시간단위 _h/_d/_m)는 결과 관찰 후 판단(예시가 _m 기준이라 완화 기대). 수정 `utils/query_gen_common.py`(신규)·`nodes/query_generator.py`·`nodes/multi_db_executor.py`. 검증 `test_query_gen_parity.py` 10건+query_generator 회귀 동일. 상세 `## D-066`. |
| 2026-07-09 | D-064 | **텍스트 후속 턴의 폼필 요청-스코프 상태 누수 차단**: 폼업로드 턴 다음 텍스트 질의가 옛 template_structure 재출력·옛 mapped_db_ids(b0)로 DB 고정·양식 재활용. 근본원인=LangGraph 체크포인터 델타 병합(텍스트 경로는 `{user_query,messages}`만 전달→직전 폼업로드의 uploaded_file/template_structure/mapped_db_ids 잔존, MemorySaver 2턴 재현 실측). 옵션 B: `state.create_followup_input()`가 폼필 트리거(uploaded_file/file_type/csv_sheet_data) None 초기화(라우트 2곳), `field_mapper` 스킵 경로가 매핑 산출물(`_CLEARED_MAPPING_FIELDS`) None 정리("template 없음→매핑 없음" 불변식, 진입 경로 무관). 승계 신호(messages/conversation_context/**pending_synonym_registrations**/승인)는 보존(등록 흐름 유지). 동작변화 1건: 텍스트 후속이 직전 양식 자동 상속 안 함(사용자 확정, 기본 동작 정합 우선). 수정 `state.py`·`api/routes/query.py`·`nodes/field_mapper.py`. 검증 `test_form_state_reset.py` 6건+`test_field_mapper_node.py` 갱신. 상세 `## D-064`. |
| 2026-07-09 | D-065 | **바 "공동존" 위치 DB 라우팅(gp+yd)**: "공동존 전체 서버" 폼필이 b0로 오라우팅(D-064 누수와 별개 — 파일 경로라 create_initial_state 리셋, 잔존 아님). 원인: gp/yd aliases에 복합어("공동존 김포 폴스타")만 있고 단독 "공동존" 부재 + input_parser 규칙10 예시에 공동존 없어 target_db_hints 추출 비결정적 → priority 비면 active_db_ids 순서(b0 첫 항목)로 오확정. 수정: (1) gp/yd aliases에 "공동존"/"공동존 폴스타" 추가(→"공동존"=[gp,yd], "공동존 김포"=[gp] 세분화 유지 실측), (2) `_ensure_location_hints` 결정적 가드로 위치 표면어 target_db_hints 보강, (3) 규칙10 예시 보강. **`_resolve_priority_db_ids` 전용 분기 미채택**(사용자 검토): 무조건 분기는 "공동존 김포" 세분화를 덮어써 회귀 위험 — aliases만 안전. process_query `_LOCATION_DB_HINTS`(프로세스/알람 경로)는 폼필과 무관이라 미변경. 수정 `domain_config.py`·`nodes/input_parser.py`·`prompts/input_parser.py`. 검증 `test_gongdongjon_routing.py` 10건. 상세 `## D-065`. |
| 2026-07-01 | D-048 | **Phase E4 — LLM 액션가능성 판단(피드백 few-shot, ML 모델 미사용)** (D-048.11 등재, 사용자 확정 §13.1, 전부 옵트인·기본 off→E3/D-049 무변경). 잔여작업 E4/E5 중 **E4 우선** 착수(E5는 vLLM 가용성 확인 후 별도). 운영자 피드백(유효/노이즈)을 신규 `feedback_store.py`(JSONL·decision_store 미러·graceful, stdlib만)에 적재→유사 과거 알람을 **alarm_name·resource·pattern 키**(§3.9, fingerprint 아님)로 few-shot 검색→`alarm_analyzer`가 **기존 단일 LLM 응답 재파싱**(추가 호출 없음·D-048.5 패턴)하여 `llm_actionability`(actionable\|noise\|null) 자문 산출. **판단은 결정적 `notification_policy` step 9**: actionable→promote(항상 안전), noise→demote(단 `effective_severity≤suppress_max_severity` 가드 — is_routine과 동일), **승격우선 기계**가 promote 공존 시 noise 무시, 1단계·SUPPRESS 하한 불변. **심각도3 절대 PAGE 불변**(step3 단락, E4는 step9만), 불확실→null(재현율 우선·승격 비대칭). `signals["llm_actionability"]` 키 추가(§8.2 스키마 가드 2곳 `test_notification_policy`·`test_polestar_noise_context_integration` 갱신). 캡처: `POST /alarm/feedback`(require_user, 게이트/액션가능성 off면 503·label 검증 400) + `app.js` 알람 카드 유효/노이즈 버튼(closure 바인딩·503 처리·`textContent` XSS 안전). 게이트오프 회귀 0: policy는 값 미독·None, analyzer는 few-shot 미조회·재파싱 스킵(3계층 테스트 고정). **구현 주의**: `llm_actionability` 추출을 첫 `_decision` **이전으로 hoist**(sev3 조기 반환 경로 `_signals()` 클로저 free-variable NameError 방지 — 회귀 테스트 고정). 수정 `alarm.py`(필드2)·`notification_policy.py`(step9)·`prompts/alarm_analyzer.py`·`alarm_analyzer.py`(`_render_feedback_section`+재파싱)·`config.py`(3필드+주석 E3→E4)·`alarm_worker.py`(`_build_feedback_store`+주입)·`api/routes/alarm.py`(`_alarm_extra_configurable`+엔드포인트)·`app.js`/`style.css`·`.env.example`. 검증(§11.1 baseline·**팀리드 독립 재실행**): `tests/test_alarm` **359 passed**(337+신규 22, 실패 0), `arch_check --ci` exit 0(feedback_store=infra·stdlib, policy=stdlib 유지), `AppConfig()` OK, `node --check app.js` OK. 상세: `## D-048` §D-048.11. 번호: `## D-` 헤더+변경이력 표 둘 다 grep, E4는 D-048 하위(.10→**.11**)로 등재(top-level 신번호 아님·D-049 보존). |
| 2026-06-30 | D-049 | **ack/incident 라이프사이클 계측 — PostgreSQL 단일 저장소 (Plan 52 §9.1·§13.1 #9, 사용자 확정)**: D-048.9가 `null`+`unavailable_metrics`로 유보한 MTTA/MTTR/사건전환율을 산출하기 위한 후속 계측 결정(이 changelog는 결정 등재 — 구현은 후속 커밋). **핵심 진단**: ack(웹 UI "확인")는 **API 프로세스**, incident 생성(PAGE)·해소(clear/self-heal)는 **워커 프로세스**에서 발생 → 같은 incident 레코드를 두 프로세스가 갱신해야 하고, 상태 전이(firing→ack→resolved) UPDATE·열린 incident 조회·시간창 집계가 필요 → 현행 JSONL `decision_store`(append-only·로컬·집계 약함) 부적합. **결정**: incident 저장소=**PostgreSQL 단일**(Redis 단독·혼합 기각 — 활성셋 소수라 Redis 속도 무의미, 감사급 이력 집계는 PG 강점). 신규 의존 아님(`audit_repository`/`user_repository`가 쓰기 PG 선례). port `IncidentStore`(domain)+`incident_repository.py`(infra, audit 미러)·테이블 `alarm_incidents`(status open\|ack\|resolved, created/acked/resolved_at, fingerprint·alarm_id·tier·resolution; 인덱스 status/fingerprint(부분)/created_at; DDL은 `ddl/` SQL+기동시 `_ensure_*_tables` auto-create 패턴). **쓰기 단일화(D-048.10 브리지 재사용)**: 워커는 PG 풀 신설 없이 incident 이벤트를 **Redis `alarm:incident` 발행→API 단일 PG 라이터(subscriber)** 영속(open=notifier PAGE 시, resolved=워커 clear/self-heal 시 fingerprint 매칭 UPDATE). **ack**=`POST /alarm/incidents/{id}/ack`(API 직접 UPDATE, **식별키=incident id**)+`app.js` SSE 카드 "확인" 버튼. **PG 풀**=`incident_tracking_enabled` 기준 전용 풀(auth 독립, DSN 재사용). **지표**: MTTA=AVG(acked-created)·`incident_mttr`=AVG(resolved-created)·전환율=incidents/page_count(window SQL, null/unavailable 제거). **MTTR 라벨 분리(환각 금지)**: self-heal 소요시간을 JSONL로 **저위험 선행** 산출하되 sev3 제외 **편향**이라 `auto_recovery_mttr_seconds`로, paged 운영자 MTTR은 PG `incident_mttr_seconds`로 **명확히 구분**. **옵트인**(기본 False)→트래커 미기동·기존 null 동작 유지(회귀 0), graceful(Redis/PG 미가용 폴백·서버 무차단). 상세: `## D-049`. 번호: 등재 직전 `## D-` 헤더+변경이력 표 둘 다 grep, 최대 D-048→**D-049** 부여(2026-06-29 충돌 교훈 준수). **구현 완료(백엔드, 동일 effort)**: 신규 `src/alarm/domain/incident_store.py`(port ABC)·`src/alarm/infrastructure/incident_repository.py`(PostgresIncidentStore, audit_repository 미러)·`src/alarm/infrastructure/incident_events.py`(RedisIncidentPublisher+run_incident_event_subscriber, sse_bridge 미러)·`ddl/alarm_incidents.sql`. 수정 `config.py`(NoiseGateConfig `incident_tracking_enabled`/`incident_event_channel`)·`decision_store.py`(`record_resolution`+aggregate `auto_recovery_mttr_seconds`, resolution 줄 by_tier/total 제외)·`alarm_notifier.py`(PAGE 시 open 발행, 미주입 스킵)·`alarm_worker.py`(`_build_incident_publisher`·is_clear 시 resolved 발행·self-heal duration→record_resolution·graph configurable 주입)·`api/server.py`(`_ensure_incident_tables`+lifespan 전용 PG풀/store/subscriber 기동·종료, 전체 try/except 무차단)·`api/routes/alarm.py`(`POST /alarm/incidents/{id}/ack`·`GET /alarm/incidents`·/alarm/metrics에 incident_store 주입·`AlarmMetricsResponse` incident_mttr_seconds/auto_recovery_mttr_seconds/open_incident_count)·`.env.example`. **쓰기 단일화**: 워커는 PG풀 미신설(Redis 발행만)·API 단일 라이터가 영속. **라벨 분리**: `incident_mttr_seconds`(PG, paged 운영자) vs `auto_recovery_mttr_seconds`(JSONL self-heal, sev3 제외 편향 — 명시). 검증(verifier 독립 PASS·7항목): fingerprint 대칭성(open=decision.fingerprint↔resolved=compute_fingerprint 동일식 — resolve 매칭 성립)·aggregate resolution 제외(기존 비율지표 비오염)·옵트인 게이팅·graceful·계층경계(domain asyncpg/redis 0, notifier/worker redis 직접 0)·metrics 0division None안전·resolve 경합(최근1건)·ack 멱등. tests/test_alarm **336 passed**(baseline 333+verifier 신규 fingerprint 대칭성 3), arch_check --ci exit 0(error 0), 광역 스위트 회귀 0(git stash pristine=working 631 failed 동일 — 환경 baseline). **UI(후속 완료, 사용자 확정=둘 다)**: (1) 채팅 알람카드(`app.js renderAlarmMessage`) — `data.incident_id` 있을 때만 "확인" 버튼(closure 캡처·인라인 onclick 없음), 클릭→`POST /alarm/incidents/{id}/ack`→확인됨/이미확인됨/재시도 상태 갱신; (2) admin 대시보드(`dashboard.html`+`admin.js`) "열린 사건" 탭 — `GET /alarm/incidents?status=open` 목록(서버/알람명/심각도/tier/생성시각/경과)+행별 확인 버튼. **카드/목록 표시용 백엔드 delta(소규모)**: `_incident_open_payload`를 `_tier_sse_payload` 전체 표시필드+식별필드로 보강(리치 카드), `alarm_incidents`에 `alarm_name` 컬럼 추가(`ALTER ... ADD COLUMN IF NOT EXISTS` 멱등·port/repo/list_open 전파). 전부 옵트인(off→빈 목록·버튼 미표시·회귀 0). 검증(verifier 독립 UI 7항목 PASS): payload 보강·alarm_name INSERT $오프셋 정확(컬럼순서 가드 테스트 추가)·테스트 약화 0(강화만)·ack 버튼 incident_id 게이팅·admin 인증조회/빈목록·플래그off 회귀·escapeHtml/textContent XSS 안전. tests/test_alarm **337 passed**, arch_check --ci exit 0, node --check app.js·admin.js OK. 라이브 브라우저 E2E는 인프라 부재로 미수행(정적·단위 단언 대체). `docs/verification_report.md` D-049 + D-049 UI 섹션. |
| 2026-06-30 | D-048 | **E3 후속 — 워커→UI 실시간 SSE Redis pub/sub 브리지 (D-048.10, D-048.9 한계 해소)**: 워커는 cross-process(또는 API 내 asyncio task지만 bus 미주입)라 DASHBOARD/TICKET 티어 결정이 UI에 실시간 표시 안 되던 D-048.9 한계를 해소. 신규 `infrastructure/sse_bridge.py` — 워커측 `RedisSseBridgePublisher`(`publish(event)`→`redis.publish("alarm:sse",json)`, graceful) + API측 `run_sse_bridge_subscriber`(채널 구독→기존 `app.state.alarm_bus.publish` 재팬아웃→`/alarm/notifications/stream`→프론트 무변경). 수정 `alarm_notifier._publish_tier_sse`(alarm_bus 우선·없으면 주입 `sse_publisher`로만 — **이중 발행 방지**, notifier는 redis 직접 import 없이 덕타이핑 호출)·`alarm_worker._build_sse_publisher`(게이트+브리지 플래그+redis 가용 시만 주입)·`api/server.py` lifespan(플래그 on 시 구독 task 기동·종료 cancel/close, Redis 실패에도 서버 기동 무차단)·`config.py`(`sse_bridge_enabled`/`sse_bridge_channel`)·`.env.example`. **pub/sub 선택**(Stream 아님): SSE fire-and-forget·TICKET은 큐+감사로 영속·DASHBOARD 정보성→ACK/소비그룹 불요(과설계 회피). **티어 분기 불변**(TICKET/DASHBOARD만, SUPPRESS=로그·PAGE=workb, 감사·큐 중복 적재 없음). **옵트인**(`enable_noise_gate` AND `sse_bridge_enabled`, 기본 False)+graceful→**회귀 0**. 검증(verifier 독립 PASS·§11.1): tests/test_alarm **287 passed**(E3 271→+16: test_sse_bridge 12+notifier 보강), arch_check --ci exit 0(error 0, notifier→infra redis 직접의존 0), AppConfig OK, 플래그오프 회귀 0(로그 폴백·E3 동일). 인프로세스/standalone 워커 양 배포 동작. (#11) `plans/52` §9.1+§13.1#9에 ack/incident 계측 후속(미구현) 명시 — MTTA/MTTR/사건전환율 null 사유. |
| 2026-06-30 | D-048 | **Phase E3 — AI 메시지 심각도 보강(상향 전용) + 억제 매트릭스 강등 + TICKET 일배치 큐 + 메타모니터링/운영지표** (사용자 확정 §13.1 #2/#3/#7, 전부 옵트인·기본 off→E2 무변경). **(AI 심각도 §3.11·D-048.5)** 신규 domain `severity_signatures.py`(Plan51 A.1 결정적 시그니처 스캔, stdlib만)·`notification_policy.py` step1 `effective_severity=max(severity, ai_severity)`(보강 off면 ai 무시·이중안전)·`alarm.py` `AlarmAnalysisResult.ai_message_severity/ai_severity_reason`·`alarm_analyzer.py`(기존 단일 LLM 응답 재파싱, **추가 호출 없음**; `is_message_alarm`=condition_log 보유∧메트릭류 제외; `cand>event.severity`일 때만 채움)·`prompts/alarm_analyzer.py`. **상향 전용 불변식**: max로만 결합→AI 하향 불가, 강등/SUPPRESS 경로 부재(R-10), `ai_severity_escalate_only=True` 고정. 심각도3은 effective 기준 단락 PAGE 불변. **(매트릭스 강등 §3.2·D-048.8)** sev1×낮음 SUPPRESS→**DASHBOARD**(타 셀 불변). **(TICKET §7·D-048.9)** 신규 `ticket_queue.py`(JSONL graceful)·`alarm_notifier.py`(TICKET→큐+SSE, DASHBOARD→SSE, SUPPRESS→로그)·`alarm_worker.py`(`_build_ticket_queue` 주입, 워커는 bus 미주입→큐만)·`api/routes/alarm.py`(analyze 경로 ticket_queue/decision_store/alarm_bus 주입→API경로 SSE 실동작). **(메타모니터링/지표 §9)** `decision_store.aggregate` 확장(page/ticket/dashboard/suppress count·actionable_ratio·last_event_age, 기존 키 하위호환)+신규 `meta_alerts`(억제율>임계→high_suppress_ratio·무수신→no_events)·`GET /alarm/metrics`. **MTTA/MTTR/사건전환율은 ack/incident 계측 부재로 null+`unavailable_metrics` 사유 명시(환각 금지)**. config E3 필드(meta_alert_window/min_events·ticket_batch_queue_*)는 팀리드 추가. 검증(§11.1 baseline): tests/test_alarm **271 passed**(E2 196→+75: severity_signatures 37·escalate_only 6·ticket_queue·meta_monitoring·metrics·notifier_tier 등), arch_check --ci exit 0(error 0, severity_signatures stdlib만), AppConfig OK, 보강오프/게이트오프 회귀 0. 미구현: 워커→UI 실시간 SSE(Redis 브리지)·ack/incident 계측·E4(LLM 액션가능성)·E5(Advisory Enricher). |
| 2026-06-29 | D-048 | **Phase E2 — 연쇄/인히비션/플래핑/스톰 억제 (의존성→인히비션→스톰→플래핑)**: 결정 파이프라인 step4~7을 결정적 규칙으로 구현(전부 옵트인, 기본 off→E1 무변경). (step4 의존성 §3.6) `polestar_noise_context.build_dependency_sql`+`fetch(collect_dependency=)`로 부모 `AVAIL_STATUS` 수집(독립 try/except — graceful 교훈 적용), parent≠0→자식 SUPPRESS·None(stale)→보수적 미억제. (step5 인히비션 §3.4) worker `_detect_inhibition`(scope=db_id\|server, 상위 심각도 활성+self-inhibition 금지)→SUPPRESS. (step6 플래핑 §3.7) 신규 domain `flapping.py`(Nagios 가중 %-state-change 1.0→1.5+히스테리시스 20/5%) + worker `_detect_flapping`(deque21+직전상태)→저심각도 SUPPRESS. (step7 스톰 §3.8) worker `_detect_storm`(사건창 deque, 대표 후 억제)→SUPPRESS. (§6.1) `_is_duplicate_fingerprint` severity별 `sev3_repeat_interval_seconds` 분리(기본 공통 4h). signals 12키의 parent_avail_status/flapping/storm 실채움. config 8플래그 추가(dependency_suppression/inhibition_enabled/+window/flapping_enabled/storm_grouping_enabled/+window/threshold/sev3_repeat). **심각도3 모든 단계 단락·PAGE 불변**(4기능 각각 테스트 고정). `AVAIL_DEPEND_RESOURCE_ID_2`는 향후 OR 확장(주석). 후속 교정: worker `_flap_states`/`_storm_window` 키 만료 sweep 추가(기존 `_firing_registry` 패턴 일관 — 단조 메모리 증가 차단). 검증(§11.1 baseline): tests/test_alarm **184 passed**(E1 92→+92: 단위 flapping21/dependency13 + 통합 dependency/inhibition/flapping/storm/sev3/회귀 41 + 기타), 기존 알람/graph 회귀 129 passed, arch_check --ci exit 0(error 0). 실 DB(도커 폴스타): 의존성 SQL 라이브 실행 OK(testdata avail_depend 전부 NULL→parent None 확인), parent≠0 SUPPRESS는 데이터 제약으로 mock 단위 검증. 미구현: E3(AI심각도/메타모니터링/DASHBOARD SSE)·E4·E5 |
| 2026-06-29 | D-048 | **noise_context graceful degradation 부분 반환 교정**(실 폴스타 도커 DB 검증으로 발견): `polestar_noise_context.fetch()`가 resource SQL과 noti SQL을 한 try 블록에 묶어, 실 폴스타에 `cmm_alarm_def` 부재로 noti 조회만 실패해도 실재하는 중요도/유지보수까지 전부 `unavailable`로 손실하던 결함을 **독립 try/except 분리**로 교정(연결 실패만 전체 unavailable, 한쪽 조회 실패는 타 신호 보존, 양쪽 실패 시 unavailable). 회귀 테스트 4종 추가(`TestPartialFailureGracefulDegradation`: noti만 실패→resource 보존·resource만 실패→noti 보존·양쪽 실패→unavailable·연결 실패→unavailable). 기존 mock(항상 성공)이 못 잡던 공백 보완. 검증: tests/test_alarm/test_polestar_noise_context 20 passed, arch_check --ci exit 0. 교훈은 CLAUDE.md Known Mistakes 등재 |
| 2026-06-29 | D-048 | **알람 노이즈 캔슬링 4-티어 발송 게이트 (Plan 52 E1 MVP)**: 폴스타 알람을 결정적 규칙으로 PAGE/TICKET/DASHBOARD/SUPPRESS 라우팅(옵트인 `enable_noise_gate`, 기본 off). 신규 `notification_policy.py`(순수함수 결정 파이프라인+`NotificationDecision` signals 12키)·`polestar_noise_context.py`(중요도/유지보수/알림정책 고정SQL, graceful)·`decision_store.py`(JSONL 결정 감사+억제율)·`notification_gate.py`(노드). 수정 `alarm_graph.py`(AlarmState+배선 `history_enabled or enable_noise_gate`)·`alarm_context_enricher.py`(noise_context gather)·`alarm_notifier.py`(4-티어, decision=None→기존 경로 무변경)·`alarm_worker.py`(핑거프린트 dedup §6.1+min_severity 역할분리 §4.8: sev0·sev3 비드롭+self_heal 시드)·`config.py`(`NoiseGateConfig` 형제 필드 `cfg.noise_gate.*`, env `NOISE_`)·`.env.example`. **심각도3 절대 PAGE(억제 단락)·재현율 우선(수집실패→보수적 PAGE)·억제≠삭제(감사)·전 기능 옵트인**(D-035 확장, D-003 준수). **번호 정정**: Plan §13의 D-040은 2026-06-23 changelog가 D-039/D-040을 선점하여 충돌 → 다음 빈 번호 D-041→최종 D-048로 등재(상세 ## D-048 §번호 정정). 하위 D-048.1~.7(E2~E5/Plan55/Advisory Enricher 예약). 검증(§11.1 baseline): tests/test_alarm 92 passed(단위57+통합35)·기존 알람회귀 116·graph 30 passed, 노이즈 모듈 신규실패 0, arch_check --ci exit 0(error 0), 게이트오프 회귀 0. 미구현: E2(의존성/스톰/플래핑)·E3(AI심각도/메타모니터링/DASHBOARD SSE)·E4(LLM 액션가능성)·E5(Advisory Enricher) |
| 2026-07-08 | D-061 | **은행존(b0) 서버명(등록명)·호스트명·IP 구분**: 은행존 폼필에서 "서버 이름"·"호스트네임"이 둘 다 등록명(name)으로 출력 — b0에 name/hostname 구분 부재 + EAV `Hostname` synonyms가 "서버명" 포함(혼동). 라이브 실측(`b0_query.py`): name=등록명(대개 `"<host> (<desc>)"`, 공백 유무 무관, **일부 서버는 name==hostname**), hostname/ipaddress 직접 컬럼=클린 값. → column_synonyms(서버명→name, 호스트명→hostname) 추가, EAV Hostname synonyms에서 "서버명" 제거, 호스트명·IP는 **직접 컬럼** 사용(name 파싱 금지 — name==hostname 서버 존재+구조 불규칙). name==hostname 서버는 두 칼럼 동일값이 정상(회귀 아님). column_synonyms는 field_mapper 단계라 LLM·D-038 빌더 경로 모두 적용. 수정: `polestar_b0.yaml`(단일 파일). 관련: D-058, 2026-06-10. 계획 `plans/58-...md` |
| 2026-07-07 | D-060 | **오케스트레이터 vLLM SSL 검증 토글 — 인증서 없는 443 엔드포인트 대응**: 목적지 vLLM이 443을 listen하되 유효 SSL 인증서를 쓰지 않는 폐쇄망에서 `deep_agent`가 `semantic_router`로 잘못 폴백하던 문제. 근본 원인: `deep_agent.vllm_healthy`의 health check(`requests.get(base_url/models)`)가 `verify` 미지정(기본 True)이라 SSL 검증 실패→예외→`orchestrator_available=False`→`select_orchestration_backend`가 `semantic_router` 반환. health check만 고쳐도 실제 tool-calling 요청(`ChatOpenAI`)이 같은 이유로 실패하므로 **두 경로 모두** 처리. **결정**: `OrchestratorConfig.verify_ssl: bool = True`(안전 기본값) 노브 추가(`.env`의 `ORCHESTRATOR_VERIFY_SSL=false`로 비활성). (a) `vllm_healthy(..., verify_ssl=)` 파라미터 추가, `orchestrator_available`이 `config.orchestrator.verify_ssl` 전파. (b) `_create_orchestrator_vllm`이 `verify_ssl=False`면 `httpx.Client(verify=False)`/`httpx.AsyncClient(verify=False)`를 `http_client`/`http_async_client`로 주입(async ainvoke 경로 필수, sync도 대칭). 수정: `config.py`·`orchestration/deep_agent.py`·`llm.py`·`.env.example`. FabriX 워커(`fabrix_kbgenai.py`)는 이미 `verify=False`라 무관. 관련: D-037(트랙 B), D-053(엔진별 방언). |
| 2026-07-02 | D-059 | **폼필 실패 시 침묵적 CSV 강등 금지 — 사유 노출**: 양식 채우기 불가 시 `_generate_document_file`이 None 반환→`output_file=None`→프론트가 Excel 링크 감추고 CSV만 노출(원인 은닉). → 실패 시 `{"reason":...}`(매핑 없음/양식·파일 없음/채우기 오류) 반환, `output_generator`가 사유를 최종 응답에 결정적 노출. 성공 판별=`file_bytes` 키. 수정: `output_generator.py`, `test_output_generator.py`. 관련: D-047(2026-06-26 진단 은닉 금지). 계획 `plans/58-...md` |
| 2026-07-02 | D-058 | **공동존(gp/yd) 서버 식별자 NULL 폴백**: yd 양식 채우기에서 `cmm_resource.name`(서버명)이 전부 NULL이라 개별 서버 식별 불가(개발/스테이징 name 미채움 H2 또는 피벗 SQL 오류 H1). → 서버 식별 출력 컬럼을 `COALESCE(name, hostname)`로, 값 필터도 `(name OR hostname)` 매칭. 수정: `polestar_cm_yd.yaml`·`polestar_cm_gp.yaml`(가이드 절+대표 예시 COALESCE). 라이브에서 생성 SQL·카운트로 H1/H2 확정 권장(D-050 원칙). 관련: D-050, 2026-06-10(name≠hostname). 계획 `plans/58-...md` |
| 2026-07-02 | D-057 | **폼필(멀티DB) SQL 생성의 엔진·스키마 인지 — b0(DB2) POLESTAR 한정**: 양식 채우기(`multi_db_executor`)에서 b0가 `SQL0204N "SDQ000.CMM_RESOURCE" undefined`로 실패. 엔진·스키마 인지가 hostname resolver(D-053)에만 있고 LLM SQL 생성 경로엔 미이식 → 무스키마 생성→CURRENT SCHEMA(SDQ000) 오해소. 실측: cmm_resource 소유 스키마=POLESTAR. → `DBDomainConfig.db_schema`(gp/yd=polestar, b0=POLESTAR) 단일 출처, `routing/db_schema.py` 헬퍼, `_generate_sql(db_id)`가 스키마 규칙+DB2 방언 결정적 주입, resolver `_table`도 db_schema 우선(b0 hostname 해소 -204 동시 해소), b0 프로필 예시 POLESTAR 한정. 수정: `domain_config.py`·`db_schema.py`(신규)·`multi_db_executor.py`·`polestar_hostname_resolver.py`·`polestar_b0.yaml`·`test_db_schema.py`(신규)·`test_process_hostname_resolve.py`. D-053이 예측한 -204 시나리오 현실화(폼필 경로). 관련: D-053. 계획 `plans/58-...md` |
| 2026-07-01 | D-056 | **멀티턴 후속 판단형 질의에 직전 턴 답변 전파(Approach A, ①②④)**: 3턴 "rightsizing 해도 되나?"가 "데이터 확보 안 됨"으로 거부되던 환각 수정. 근본원인(orchestration): (1)어시스턴트 답변이 `messages`에 누적 안 됨(append 노드가 synonym_registrar뿐), (2)`_make_isolated_input`이 `messages:[]`로 이력 삭제 → general_inference 멀티턴 코드 무력화, (3)general_inference가 conversation_context 미참조. → **②** 경로별 단일 종료 노드(result_aggregator `_with_answer_history`/output_generator 래퍼/general_inference/error_response)에서만 AIMessage 누적(subagent 반환은 task_results에만 담겨 이중 누적 없음). **①** `SubAgentSpec.needs_history`(general_inference만 True)로 추론 agent에만 트리밍 이력 전달(`_history_for_agent`, 상한 10, 현재 턴 질의 제외), 조회 agent는 [] 격리. **④** general_inference 후속 턴 conversation_context 그라운딩 + `_JUDGMENT_GUIDANCE`(확보 데이터로 판단 가능 범위 답하고 추가 필요분만 지목, 거부 금지). ③rows 보존·intent_planner 분석 라우팅은 2단계 유보(95K/D-041 신중). **후속 보강(같은 날)**: A 적용 후 조회 없는 판단 턴(general_inference)이 top-level `query_results=[]`로 덮어써 다음 턴 `previous_entities`가 소실 → "해당 서버"가 전체 서버로 조회되던 문제 → `context_resolver`가 이번 턴 추출이 비면 직전 conversation_context의 entities/db_ids/location을 **sticky 승계**(이번 턴 신규값 우선). 별건 미해결: b0(DB2) 월간 사용률 통계 정수 표시(gp/yd만 `::numeric` 캐스트, b0 부재 — SQL 레벨 추정, DB2 컬럼 타입 검증 후 수정 예정). **후속 보강 2(같은 날)**: general_inference가 판단/분석 마무리에서 **미지원 기능**(MySQL InnoDB 버퍼풀·DB 엔진 내부 설정 등)을 후속 조회 예시로 지어내 광고(→ 실제 요청 시 data_query 재시도 낭비 후 "없음") → `_JUDGMENT_GUIDANCE`가 제안을 `_SUPPORTED_CAPABILITIES`로 바운딩 안 한 탓. 프롬프트 하드 제약(사용자 선택): `_JUDGMENT_GUIDANCE`·general_inference [안내 규칙]에 "제안·예시는 [지원 가능한 조회 유형] 목록 안에서만, 목록 밖(엔진 내부 설정·앱 파라미터·임의 IT 개념) 예시 금지" 명시 + 카탈로그 없는 후속 턴에도 지원 목록 주입. 미지원 요청 재시도 낭비(2차 증상)는 스코프 게이트 유보. **후속 보강3(같은 날)**: 프로세스 리스트 조회 후 "해당 서버 알람"이 프로세스명(name=mysql,pid…)을 서버로 오조회 → `_extract_previous_entities`가 프로세스 결과 행의 `name`/`pid`를 서버 식별자로 오수집(name/id 힌트 부분매칭)한 탓 → `_looks_like_process_rows`(pid 키 감지)로 프로세스 행을 결과 harvesting에서 제외(서버 식별자는 filter만), sticky 승계로 hostname 복원. **후속 보강4(같은 날, 근본)**: "해당 서버 알람/CPU"가 전체 조회로 빠지던 근본 — `query_generator`가 SQL 생성에 `parsed_requirements.original_query`+filter만 쓰고 intent_planner 확장 sub_query는 안 씀 → 지시어 후속은 filter 비고(D-055) original_query에 구체 서버명 없어 hostname 미도달. `subagents._inject_demonstrative_hostname`으로 data/alarm 파이프라인에서 지시어 특정-서버 질의(전체 조회 제외)에 직전 hostname을 filter_conditions에 결정적 주입(concrete 경로와 동일). 회귀: 신규 11건 + 승계 2건 + 역량 제약 3건 + 프로세스 제외 5건 + hostname 주입 6건, test_multiturn·test_orchestration 246 passed/4 skipped. 관련: D-041, D-047, D-062, D-053, D-055. 검증: arch_check exit 0 |
| 2026-07-01 | D-055 | **후속 턴 "해당 서버" 지시어 hostname 오추출 차단 + 결과 요약 유지 정정**: (1) 멀티턴 후속 "해당 서버 프로세스"에서 서버가 `previous_server`로 들어가 0건 — `input_parser` 규칙 14가 지시어도 서버 지목으로 보고 hostname 필터를 만들며 LLM이 플레이스홀더를 지어냄 → 이번-턴 필터가 `_resolve_hostname` ①에서 previous_entities ②보다 우선순위가 높아 오염. → `process_query._is_demonstrative_value()` 결정적 가드로 지시어(해당/그/이/위 + 서버/장비)·영문 플레이스홀더(previous/prev+server/host)를 hostname에서 배제하고 previous_entities로 폴백(①·② 모두). input_parser 규칙 14에 지시어 미추출 지침·예시 보강. (2) D-054 규칙 6을 LLM이 과잉 적용해 정상 요약·이상 징후 분석까지 소실 → 규칙 6에 "데이터에 없는 컬럼 면책만 금지, 조회 데이터 요약·분석은 유지" 단서 추가. 회귀: 지시어 가드 단위 3건. 관련: D-046, D-047, D-053, D-054. 검증: orchestration 141 passed/4 skipped, arch_check exit 0 |
| 2026-06-30 | D-054 | **레거시 `polestar` 도메인 폐기 + db_profiles 정리 + 면책 문구 환각 차단**: (1) `DB_DOMAINS`에서 레거시 단일 `polestar` 엔트리 제거(7→6, b0가 은행 레거시 승계). (2) `config/db_profiles/{test_db,unknown,polestar_pg,polestar}.yaml` 삭제(빈 auto 스텁·미등록 db_id·b0로 대체). (3) `output_generator` 프롬프트 규칙 6 추가 — 사용자가 묻지 않은 컬럼(avail_status 등) 부재·면책·안내 문구 자발 추가 금지(코드에 없던 순수 LLM 환각). 부수: `alarm.py` db_id 기본값·`semantic_router`/`cache_management` few-shot 예시·`.env.example` POLESTAR_DB_IDS/ACTIVE_DB_IDS·테스트(domain_config 6개, structure_analysis polestar_b0) 갱신. field_mapper polestar 분기·test_document 픽스처·redis/export 스냅샷은 의도적 보존. 검증: 영향 테스트 93 passed/7 skipped(기존 실패 1건 무관). 관련: D-004, D-053 |
| 2026-06-30 | D-053 | **은행 b0 실시간 프로세스 조회 0건 수정(2건)**: (1·실제 1차 게이트) `_resolve_db_id._LOCATION_DB_HINTS`에 은행(b0)이 없어 "은행 폴스타 … 프로세스" 질의 db_id=None → 조기 0건(라이브 진입 로그로 확인). → b0 힌트(은행/레거시/은행존) 추가, 위치 신호 명확 시 base_url 무관 db_id 반환. (2·후속 게이트) `build_hostname_sql`이 엔진 무관 PostgreSQL 방언(`LIMIT 1`+`polestar.` 스키마) 고정 → DB2 b0에서 SQL 실패 → D-046 graceful 폴백이 서버명을 그대로 API에 전달 → 0건. → `resolve()`가 `get_domain_by_id(db_id).db_engine` 조회, db2는 `FETCH FIRST 1 ROWS ONLY`+무스키마(CURRENT SCHEMA). 진입/게이트/응답-envelope 진단 로그 추가. b0 API 형식은 gp/yd와 동일 확인(형식 mismatch 아님). (3·멀티턴) `agent_orchestrator`가 `active_db_id`/`target_databases`를 top-level로 안 올려 다음 턴 previous_db_ids 공백 → "해당 서버 …" 후속 process_query db_id=None 펑. → `result_aggregator._collect_db_promotion`이 실행 task target_db_ids를 승격(멀티턴 DB 승계 복원). 런타임 `.env`에 `ALARM_PROCESS_API_BASE_URLS_CSV`의 b0 매핑 필요(코드 기본값엔 gp/yd만). 관련: D-046, D-047. 검증: orchestration 201 passed/4 skipped, 알람 프로세스 63 passed, arch_check exit 0 |
| 2026-06-30 | D-051 | **allowed_tables 유사어 동적 보완을 질의 매칭분으로 게이트**: `schema_analyzer`가 `get_synonyms`의 모든 테이블을 무조건 `_allowed`에 추가하던 것(b0 실측 5→407, relevant 400, system_prompt 104K>95K FabriX 한도)을, 이번 질의 용어와 매칭된 유사어의 테이블만(상한 15) 보완하도록 `_synonym_tables_matching_query()` 게이트 신설. FabriX 데이터 평면도 ~95K 입력 한도임을 확인(D-042 전제 정정). 회귀: synonym_gate 8 + eav_supplement 통합 양성/음성. 계획 `plans/57-...md` |
| 2026-06-29 | D-050 | **단일 서버 필터 + EAV 피벗(CPU·메모리) 조회 SQL 교정(HAVING 패턴)**: 실재하는 김포 서버의 CPU·메모리가 조회 시 null로 나오던 문제 — "데이터를 못 가져오는 게 문제"라는 사용자 지적이 정확. 근본 원인: 폴스타 EAV에서 CPU(`server.Cpus`)·메모리(`server.Memory`)는 server.Server와 다른 행이라 `GROUP BY platform_resource_id` 피벗이 필요한데, **단일 서버 필터+피벗을 결합한 예시·지침이 없어** LLM이 피벗에 `WHERE c.name='###'`를 붙임 → 그 술어가 server.Server 행에만 참이라 GROUP BY 전에 Cpus/Memory 행이 제거 → CPU·메모리 NULL(OS/IP/호스트명만 정상). avail_status는 이미 HAVING 기법을 쓰는데 서버명 필터엔 없던 게 구멍. **결정**: 서버 식별 필터는 WHERE가 아니라 **HAVING(집계 후 server.Server 행 기준)**으로 적용. **수정**: `prompts/query_generator.py`(서버명+피벗 HAVING 규칙·예시), `config/db_profiles/polestar_cm_gp.yaml`·`polestar_cm_yd.yaml`(단일 서버 OS/IP/호스트명+CPU/메모리 query_example 신규). 관련: D-063(이 사례를 "속성 부재"로 오판한 가드 — 본 결정이 진짜 원인), D-046. 검증: YAML safe_load OK, arch_check exit 0, 회귀 통과(사전 실패 3건 무관) |
| 2026-06-29 | D-063 | **엔티티 확보 후 무의미 재시도 차단(replanner 필드-null 재조회 가드)**: 단일 서버 단일 의도 질의에서 1차가 서버는 찾았으나 CPU·메모리 컬럼이 null(속성 미수집)이자, replanner LLM이 "0건→재조회" 예시를 오적용해 DB 범위를 바꿔가며 max_replan(3)까지 헛 재시도(t1~t4) → 불필요 DB 왕복 4회 + 부분/빈 답변 모순 이중 출력. **결정**: `replanner._filter_futile_retries` 결정적 가드 — 엔티티 확보(선행 >0행) 후 같은 의도 재조회를 제거(전부 제거 시 종료). supersedes 필드에만 의존하지 않도록 **행수+독립성**으로 판정(원인 B=LLM이 supersedes 미설정에 견고): (a) supersedes가 >0행 선행을 가리키거나, (b) 독립(depends_on 빈)+같은 agent 선행이 >0행이면 제거. 보존: 선행 0건 재조회/데이터 의존 보강(장애→알람)/다른 agent 보강. 보조로 `prompts/replanner.py` 원칙 7(행 찾음+필드 null=속성 부재→재조회 금지) 추가. **수정**: `orchestration/replanner.py`(`_filter_futile_retries`+호출), `prompts/replanner.py`. 채택=옵션①(즉시 중단; data_query가 내부 DB 선택하므로 교차 DB 회수 가치 낮음, 종료 시 단일 task→정직한 단일 답변). 관련: D-043, D-062, D-040. 검증: arch_check --ci exit 0, replanner 신규 6건 포함 orchestration 130 passed/4 skipped |
| 2026-06-29 | D-062 | **딥 에이전트 경로 복합 결과의 단일 LLM 합성(모순 이중 답변 제거)**: 딥 에이전트(트랙 B)에서 오케스트레이터가 동일 질문을 재시도하면 collector에 1·2차 결과가 모두 쌓이는데, 1차가 error가 아닌 "부분 성공"(서버는 찾았으나 CPU/메모리 null → `completed`)이라 D-043(supersedes)이 못 잡고 `_merge_finalized`가 "CPU 없음"·"CPU 8코어" 서술을 한 말풍선에 이어붙여 모순 이중 답변을 내던 문제 해결. **결정(사용자): 단일 LLM 합성** — `result_aggregator(synthesize=True)`(딥 경로 전용, replanner 경로는 기존 deterministic 병합 유지)가 복합 task를 `_synthesize_finalized`로 LLM 1회 합성(재시도 모순은 구체적 값으로 해소, 멀티의도는 통합). **스트리밍 정합(D-009)**: 합성 시 per-task `output_generator`는 `stream_user_response=False`로 태그를 떼고 최종 합성 1회만 USER_RESPONSE_TAG로 스트리밍(딥 노드는 `deep_agent`라 태그로만 토큰을 거름) → 단일 스트림. 합성 예외/빈 결과는 `_merge_finalized`로 안전 폴백, output_file 첫 task 채택·query_results 누적(D-047). **적용 경로 — 둘 다**: 딥 에이전트(트랙 B) `_aggregate_with_fabrix`와 **다중 의도 오케스트레이션(트랙 A, Plan 48) `result_aggregator` 그래프 노드**. 폐쇄망(deepagents 미설치)에서 실제 활성 경로는 트랙 A이므로 `graph.py`에서 이 노드를 `synthesize=True`로 바인딩해야 발동(트랙 B만 적용 시 사용자 런타임 미발동 — 최초 누락 원인). **수정**: `prompts/result_synthesizer.py` 신규, `output_generator.py`(`stream_user_response` 파라미터), `result_aggregator.py`(`synthesize`/`_synthesize_finalized`), `deep_agent.py`(트랙 B `synthesize=True`), `graph.py`(트랙 A result_aggregator 노드 `synthesize=True` 바인딩). 관련: D-005, D-009, D-043, D-047. 검증: arch_check --ci exit 0, result_aggregator 합성 신규 6건+wiring synthesize 검증(트랙 A/B) 통과 |
| 2026-06-29 | D-047 | **프로세스 결과: 채팅 상위 N + CSV 전체 다운로드(사용자 결정)**: process_query가 상위 N(`process_top_n=5`)만 남기고 전체를 폐기 → 채팅·CSV 모두 5건만 노출. 또 orchestration 경로는 `query_results`를 top-level state로 승격하지 않아(result_aggregator가 final_response만 반환) CSV 버튼 자체가 미노출(row_count=0). **수정**: (a) `process_query.py` 전체 1회 정렬·마스킹 후 `organized_data.rows`=상위 N(채팅), `query_results`=전체(CSV/row_count), 요약에 CSV 안내+`shown_count`. (b) `result_aggregator.py` `_finalize_task`/단일·복합 경로에서 `query_results`를 top-level로 승격(데이터/프로세스 공통으로 CSV·row_count 동작). (c) `download_csv` 컬럼명 합집합+`restval`/`extrasaction`로 이종 행 견고화. `process_top_n`은 표시 전용으로 의미 변경. 검증: orchestration+multiturn 신규 2건 포함 통과, arch_check exit 0 |
| 2026-06-29 | D-047 | **프로세스 조회 대상 서버 식별자 추출 + 실시간 라우팅 결정적 교정**: "김포 ### 서버에 대한 프로세스 조회"가 (1차) "서버명을 식별하지 못했습니다"로, 이어 (2차) DB에서 `resource_type='process'` 행을 가져오는 환각으로 응답되던 문제 해결. **원인1**: `process_query._resolve_hostname`은 filter_conditions 중 field가 `_HOST_FIELDS`인 조건을 서버 식별자로 쓰는데 input_parser에 지목 서버명을 hostname filter로 추출하는 규칙이 없어 `identifier=None`(D-046 해소는 identifier 확보 후 동작이라 공백을 못 메움). **원인2**: 시간성 수식어 없는 "프로세스 조회"를 intent_planner LLM이 보수적으로 `data_query`로 분류 → D-046이 경고한 DB 폴백 환각. **수정**: (a) `prompts/input_parser.py` 규칙 14 신설 — 단일 서버 지목 시 `{"field":"hostname","op":"=","value":"<서버식별자>"}`로 추출, 위치/DB 수식어는 target_db_hints로 분리, 예시 1건. (b) `process_query._HOST_FIELDS`를 영문(host/device_name)·한글(서버명/장비명/호스트명/서버/장비) 변형까지 확장. (c) `prompts/intent_planner.py` 규칙 3을 "프로세스 조회 기본=process_query, 명시적 과거/이력 신호만 data_query"로 강화. (d) `intent_planner._coerce_process_intent` 결정적 가드 — data_query+프로세스+이력신호 없음 → process_query 교정(폴백 포함). 회귀 없음(영문 field·이력 프로세스 data_query 동작 유지). 관련: D-046, D-041. 검증: arch_check --ci exit 0, orchestration+multiturn 194 passed/4 skipped(신규: 한글 field 1, 라우팅 교정 3) |
| 2026-06-26 | D-046 | **프로세스 조회 시 서버명 → 호스트명 해소 (process_query)**: 폴스타 실시간 프로세스 API의 조회 키는 hostname(`cmm_resource.hostname`)이지만 사용자는 서버명(`cmm_resource.name`)으로 질의한다. 공동존 폴스타(gp/yd)는 name≠hostname이라 `hostname=서버명`으로 보내면 0건 → 오케스트레이터가 DB 조회로 폴백해 **리소스명이 '프로세스'인 행을 가져오는 환각** 발생. **수정**: 프로세스 API 호출 전 입력을 정규 hostname으로 해소. (a) `src/alarm/infrastructure/polestar_hostname_resolver.py` 신규 — DBHub(MCP) 고정 SELECT(`server.Server`·`DTIME IS NULL`·name/hostname OR·name 우선)로 hostname 조회(`polestar_history.py` 경로 재사용, D-022/`is_lob` 금지 정합). (b) `process_query.py`가 `_resolve_canonical_hostname`(예외/0건 시 None→원시 값 폴백)로 해소 후 API 호출, 결과에 `server_name`·`hostname` 동시 보존·요약 병기. 회귀 없음(해소 실패·DB 미연결 경로 기존 동작 유지). 관련: D-041(M4 process_query), D-036(프로세스 API 재사용). 검증: arch_check --ci exit 0, 신규 11건 포함 orchestration/multiturn/alarm 260 passed/4 skipped |
| 2026-06-26 | D-045 | **스트리밍 마크다운 비파괴 렌더(DOM 모핑) — 표 가로 스크롤·텍스트 선택 보존**: 토큰마다 `#streamingText.innerHTML = marked.parse(전체)`가 서브트리를 파괴·재생성 → 표(자체가 `overflow-x:auto` 스크롤 컨테이너)의 `scrollLeft`이 매 토큰 0으로 초기화되어 가로 스크롤이 좌측으로 리셋되던 문제 해결(텍스트 선택 끊김도 동반). **B-1 DOM 모핑**: full-parse 결과를 기존 DOM에 diff 적용해 동일 위치·태그 요소 재사용 → 스크롤·선택 보존, 출력 HTML은 full-parse와 동일(정확성 영향 0). 폐쇄망이라 `morphdom` 벤더 대신 **자체 경량 morph 구현**(`morphChildren`+`syncAttributes`, `isEqualNode`로 무변경 스킵), 실패 시 폴백 A(표 `scrollLeft`만 보존하며 전체 교체). 추가로 **rAF 렌더 코얼레싱**(`scheduleStreamingRender`, 프레임당 1회)으로 재파싱 O(L²) 상수 절감. 수정: `app.js`(두 토큰 루프→`scheduleStreamingRender`, `createStreamingMessage` 누적 초기화). 외부 의존성/`index.html` 변경 없음. 검증: morphChildren 단위(표 2→3행 성장 시 인스턴스 재사용·scrollLeft=120 보존 PASS)+`node --check`. 관련: D-009, D-044. 계획서 `plans/51-streaming-scroll-ux.md` Part 2. ※ 초안 가정 morphdom 벤더는 폐쇄망 사유로 자체 구현으로 변경 |
| 2026-06-26 | D-044 | **스트리밍 응답 조건부 자동 스크롤(stick-to-bottom) + 맨 아래 이동 플로팅 버튼**: SSE 토큰마다 무조건 `scrollToBottom()` 호출 + `.chat-messages`의 `scroll-behavior: smooth`가 겹쳐 화면이 "튀고"(즉시 읽기 어려움), 위로 스크롤해도 다음 토큰에 강제로 끌려 내려가던(과거 읽기 불가) 문제 해결. **stick-to-bottom 모델**(프론트 전용, 백엔드/SSE 무변경): `stickToBottom` 상태 추적(임계값 80px), 토큰·에이전트 출력은 **고정 상태일 때만** 즉시 추종(`scrollToBottomIfSticky`)하고 해제 시 면역, **사용자 본인 질의만 무조건**(`scrollToBottom`) 이동. 고정 해제 시 표시되는 **플로팅 "맨 아래로" 버튼** + 신규 토큰 도착 시 점(`has-new`) 강조로 새 답변 인지. `scroll-behavior: smooth`는 제거하고 버튼 클릭 이동에만 국소 적용. 결정(사용자): 신규 메시지 (B)조건부+강조 채택, 임계값 24px(초안 80px이 과해 축소), 버튼 우측 하단 배치. 수정: `app.js`/`index.html`/`style.css`. 관련: D-009(SSE 스트리밍). 계획서 `plans/51-streaming-scroll-ux.md`. ※ 계획 초안 가정 D-043은 같은 날 supersedes가 선점→**D-044** 부여(변경 이력 표까지 grep, 2026-06-25 충돌 교훈) |
| 2026-06-26 | D-043 | **재조회(대체) 후속 task의 1차 시도 결과 본문 숨김 (supersedes)**: 단일 의도 질의에서 1차 task가 빈/누락 결과를 내자 replanner가 같은 의도 재조회 후속(t2)을 추가→성공했는데, `result_aggregator._merge_finalized`가 t1(실패 서술)·t2(성공 서술)를 **둘 다 본문에 이어붙여** "없음→있음" 모순 이중 답변이 출력되던 문제 해결. **수정 3중**: (a) `prompts/replanner.py`에 `supersedes`(대체 대상 선행 task_id) 필드 규칙·예시 추가(예시1=대체/예시2=추가 구분). (b) `replanner._assign_ids`가 `supersedes` 보존·신규 task 간 임시 id 재매핑(depends_on/input_from과 동일), 누락 시 `[]` 보정. (c) `result_aggregator._collect_superseded`로 **후속이 성공(에러 없음)했을 때만** 대체된 선행 task_id를 본문 조립 대상에서 제외(재조회 실패 시 1차 유지=안전, 전부 제외 시 전체 사용=방어). 숨김은 **최종 답변 본문 한정** — 처리 현황(SSE) 패널은 두 task 모두 투명 유지(D-039). 관련: D-005(부분 실패 병합), D-037/D-039(orchestration·현황), D-040(replanner 중복 가드). 검증: arch_check --ci exit 0, result_aggregator·replanner 신규 6건 포함 25건 통과 |
| 2026-06-24 | D-039 | **orchestration 처리 현황에 생성 SQL·대상 DB·DB 에러 노출 (관찰성 보강)**: deepagent orchestration 경로에서는 `schema_analyzer`/`query_generator`/`query_executor`가 그래프 노드가 아니라 `agent_orchestrator` 내부 함수 호출이라, 생성 SQL이 SSE `node_complete`로 노출되지 않아 **어떤 쿼리가 어느 DB로 실행됐는지 처리 현황에서 볼 수 없던 문제** 해결(자원 조회 성능/품질 저하 진단의 1차 장애물). **수정 3중**: (a) `subagents.run_data_query_pipeline` 결과에 `generated_sql`(단일 DB는 state, 멀티 DB는 `query_attempts`에서 수집)·`target_db_ids`·`db_errors` 추가 → `agent_orchestrator`가 `task_results`에 보존. (b) `query.py::_summarize_tasks(tasks, results=...)`가 task별 생성 SQL·대상 DB·행수·DB 에러를 포함하도록 확장하고 `_extract_node_progress`의 `agent_orchestrator` 분기가 `task_results` 전달(node_complete가 재계획 회차마다 재방출되므로 회차별 SQL도 누적 노출). intent_planner는 results 미전달(계획 시점엔 결과 없음). (c) 프론트 `renderTaskList`에 대상 DB·생성 SQL(`<pre>`)·행수·DB별 에러 렌더링 추가 — `polestar_b0`(은행) 오선택·SQL0204N 같은 잘못된 DB 실행이 즉시 가시화. **목적**: null 값이 실제 null인지 EAV 조인 누락/DB 오선택의 결과인지 SQL로 판별 가능하게 함(후속 DB 핀 고정·replanner null 가드 교정의 전제). 관련: D-039(처리 현황), D-037(orchestration). 검증: arch_check --ci exit 0, python/js 구문 OK, orchestration 85 passed/4 skipped |
| 2026-06-23 | D-040 | **replanner 과(過)재계획으로 인한 일반 안내 답변 중복 출력 수정**: 동일 안내 질의("사용법+지원 소스+조회 가능 데이터")에서 1차 `general_inference` 답변이 3가지를 모두 담았는데도 replanner가 후속 `general_inference`를 추가해 "지원 소스/조회 가능 데이터"를 **중복 재출력**하던 버그 해결. **원인 2가지**: (1) [결정적] `replanner._summarize_result`가 텍스트 결과를 `text[:300]`로 절단 → 긴 안내 답변의 앞부분(사용법)만 평가 컨텍스트에 노출 → replanner가 "뒷부분 누락"으로 오판. (2) [개념적] `general_inference`(자체 완결적 안내)에 또 `general_inference` 후속을 붙이는 것은 데이터 의존 후속(replanner 본래 목적: 0건→재조회, 장애→알람조회)이 아니라 "같은 주제 재서술"임. **수정 3중**: (a) `_summarize_result` 텍스트 상한 `300→1500자`(`_MAX_SUMMARY_TEXT_CHARS`) — 완결성 판단이 잘린 답변에 기반하지 않도록. (b) **결정적 가드**(`replanner`): `_assign_ids` 후 신규 task가 모두 `general_inference`이면 추가하지 않고 `needs_replan=False` 종료(데이터 의존 후속만 허용, data_query→data_query 등 정당한 재계획은 영향 없음). (c) **프롬프트 규칙 6**(`prompts/replanner.py`): 안내성 답변은 완결로 간주, general_inference 후속 생성 금지 명시. **범위**: 과분해(intent_planner)·다중 general_inference 인사 중복은 본 건과 별개(미해결). 관련: D-037(replanner), D-039(처리 현황/라벨). 검증: arch_check --ci exit 0, replanner 12건(신규 가드 테스트 1건 포함)·orchestration 85 passed/4 skipped 통과 |
| 2026-06-23 | D-039 | **다중 의도 처리 현황 표시 + 본문 작업 라벨 제거 (Plan 49 §3.6/§5 step 7 "SSE progress 보류" 완성)**: 다중 의도 경로(`intent_planner → agent_orchestrator → replanner 루프 → result_aggregator`)의 4개 노드가 SSE 화이트리스트(`_known_nodes`, query.py 양쪽 스트림 핸들러)에 없어 **처리 현황 패널에 미표시**되던 문제 해결. (1) **백엔드**(`query.py`): 4개 노드를 화이트리스트에 추가 + `_extract_node_progress` 분기 추가(`intent_planner`=task_count/tasks, `agent_orchestrator`=tasks 상태, `replanner`=replan_history/needs_replan, `result_aggregator`=status), `_summarize_tasks` 헬퍼(order 정렬·표시 필드만). node_complete는 루프 재진입 시 매 회차 재방출되므로 status 갱신은 자연 동작. (2) **재계획 사유 보존**: replanner 종료 회차의 node_complete가 추가 회차 데이터를 덮어써 사유가 사라지던 문제를 `state.replan_history`(신규 필드, 루프 누적; replanner가 추가/종료 양 경로에서 carry forward)로 해결 — 종료 후에도 회차별 추가 작업 수·사유 표시. (3) **본문 라벨 제거**(`result_aggregator._merge_finalized`): `### 작업 N (general_inference)` 헤딩·내부 agent명을 본문에서 제거하고 각 결과 텍스트만 순서대로 연결, 작업 구성/개수/재계획 이력은 처리 현황으로 이전(사용자 요청). 부분 실패 안내는 내부 agent명 없이 `작업 N` 순번만 유지(D-005). (4) **프론트**(`app.js`): nodeLabels/nodeTooltips 4개 + `agentLabels`(agent→사용자 라벨) + `renderTaskList` 헬퍼 + renderNodeData 4분기. **범위 한정(사용자 결정)**: 이번엔 처리 현황/라벨만 — 과분해·인사 중복 등 근본 원인(planner 분해 규칙·general_inference 인사 반복)은 후속. 관련 결정: D-033(처리 현황 추가 패턴 `_extract_node_progress→node_complete→renderNodeData` 재사용), D-037(replanner), D-038(result_aggregator 단일 task verbatim 통과 — 본 변경은 복합 task merge만 수정해 무충돌). 검증: arch_check --ci exit 0(pre-existing WARN orchestration→prompts만), orchestration 84 passed/4 skipped, graph 30건 회귀 통과, result_aggregator 테스트 2건 신 동작 갱신. 문서: `docs/11_web_ui_progress_specification.md` §3.2/§4.13 갱신 |
| 2026-06-23 | D-038 | **사용법/지원 소스 안내 — general_inference 그라운딩 + 도움말 버튼**: "뭘 할 수 있어?/지원 소스?" 능력 문의에 실제 `active_db_ids ∩ allowed_db_ids` + `DB_DOMAINS` 설명을 코드로 조립해 시스템 프롬프트에 그라운딩(사실은 코드, 문장만 LLM, 멀티턴 마무리). deep_agent·semantic_router·intent_planner 3 백엔드가 모두 수렴하는 `general_inference` 1노드만 수정 → 전 백엔드 자동 커버. deep_agent 트랙 우회 방지로 `ORCHESTRATOR_INSTRUCTIONS`에 general_answer 위임 1줄 추가. `allowed_db_ids`(D-026) 교집합으로 못 쓰는 소스 광고 차단. `general_inference`의 "DB 미접근" 계약 유지(라이브 health 미수행, 설정·도메인 정의만 참조). UI: `❓ 사용법` 버튼(점선 메타 스타일, 클릭 즉시 실행). result_aggregator 무변(텍스트 결과 verbatim 통과 실측 확인). 검증: arch_check --ci exit 0(신규 error/warning 없음), orchestration 27건 통과, 카탈로그 교집합·폴백·멀티턴 마무리 동작 확인 |
| 2026-06-17 | D-037 | **테스트용 워커 provider override 추가 — deepagent 경로 전체 gemini 검증 (Plan 49 §4.7)**: 데이터 평면(워커=FabriX, "실질 응답처리")을 **운영은 FabriX 유지, 테스트 환경에서는 gemini로 deepagent 경로 전체를 검증**할 수 있도록 토글 추가. 갭 규명 결과: 워커 파이프라인(`run_data_query_pipeline`→schema_analyzer/query_generator/result_organizer)은 일반 경로와 동일 노드에 주입된 `llm`을 쓰며 gemini는 `create_llm`에서 이미 지원 — **deepagent 고유 버그 없음**. 진짜 갭은 "운영(FabriX)과 분리된 테스트 토글 부재"(전역 `LLM_PROVIDER`를 바꿔야만 gemini 테스트 가능). **구현**: (1) `create_llm(config, *, provider_override=None)` — 지정 시 `config.llm.provider` 대신 사용(기본 None=운영 무변, keyword-only로 하위호환). (2) `AppConfig.worker_provider_override: Literal["ollama","fabrix","gemini"]|None=None`(env `WORKER_PROVIDER_OVERRIDE`). (3) `build_graph`가 워커 LLM을 `create_llm(config, provider_override=config.worker_provider_override)`로 생성 → deepagent 경로 전체(input_parser/field_mapper + deep_agent 워커)가 override provider로 동작. (4) `.env`에 운영/테스트 전환 주석. **사용자 결정(범위)**: "테스트 시 deepagent 경로 전체 gemini"(워커 일부만이 아닌 전체). 운영은 override 미설정으로 FabriX 무변. **부수 수정**: 사용자가 `.env`에 `ENABLE_DEEPAGENTS_PACKAGE=true`를 켜자 `enable_deepagents_package`가 `_build_config`/`_build_orchestration_config` 픽스처에 누수되어 deep_agent 경로가 선택→orchestration/replanner 테스트 3건 오탐 → 픽스처에 `enable_deepagents_package=False` 명시로 차단(D-037 Decision 2 패턴). 검증: `arch_check --ci` exit 0, override 단위 8건 통과, orchestration 87 passed/1 skipped(0 failed), graph 30건 회귀 통과. (사전 존재 실패 2건 `test_gemini_api_key/model_default_empty`은 로컬 `.env`/`.encenv`의 `LLM_GEMINI_*`를 BaseSettings가 파일에서 읽어 기본값 `""` 단언이 깨지는 것 — 본 작업·`.env` 변경과 무관) |
| 2026-06-17 | D-037 | **deepagents 0.6.10 실제 설치 + step6(도구 결과→FabriX 재정리) 실측 구현 (Plan 49 §4.3 step6/§7 step 6)**: 폐쇄망 wheel을 기다리지 않고 **현 개발 환경에 deepagents 0.6.10을 실제 설치**하여 런타임 표면을 실측 후 step6를 추측이 아닌 실제 구현으로 완성. **설치 영향**: deepagents 0.6.10이 `langchain-core>=1.4.7`/`langchain>=1.3.9`를 요구 → `langchain-core 1.2.18→1.4.7`, `langchain 1.2.12→1.3.9`, `langgraph 1.1.1→1.2.5` 업글(+`langchain-openai 1.3.2`·`langchain-google-genai 4.2.5` 등). **R-B3(1.2→1.4 전이 비호환) 실증 해소** — 업글 전후 전체 스위트 동일(업글로 인한 신규 실패 0건, 모듈 단위 격리 실행 시 회귀 없음). **실측한 런타임 표면**: `create_deep_agent`의 실제 인자는 `instructions`가 **아니라 `system_prompt`**(기존 코드의 `instructions=`는 0.6.10에서 TypeError였음 → 수정). 반환은 `CompiledStateGraph`이며 `ainvoke` 결과 top-level 키는 `['files','messages']`로 **도구 결과 전용 state 키 없음** — 도구 결과는 `messages` 내 `ToolMessage`(name=도구명, content=직렬화 JSON)로만 존재. **step6 구현**: 토큰 폭증 방지 직렬화로 인한 손실을 피하려 `build_tools`/`_run_subagent_tool`에 **원본 결과 수집기(collector)** 추가 → `run_deep_agent`가 에이전트 종료 후 collector의 **원본 결과**를 `task_plan`/`task_results`로 재구성해 **FabriX `result_aggregator`로 최종 응답 생성**(오케스트레이터 자유 서술 미노출 — 성공기준 5). 도구 미호출 시에만 마지막 메시지 폴백. **실증 범위**: 실제 `create_deep_agent` 런타임으로 fake tool-calling LLM이 도구 호출 → collector 원본 적재 → FabriX 재정리까지 E2E 통과(`test_real_deepagents_collector_and_fabrix_step6`), 실제 vLLM `ChatOpenAI` 오케스트레이터로 `build_deep_agent` 조립 성공(HTTP 왕복만 라이브 vLLM 필요). **사전 존재 테스트 3건 수정**(Decision 2): `.env`의 `ORCHESTRATOR_PROVIDER=gemini`가 `OrchestratorConfig(BaseSettings)`에 누수되던 것을 테스트 픽스처에 `provider="vllm"` 명시로 차단(surgical, 타 모듈 무영향). 의존성: `requirements.txt`/`pyproject.toml`에 deepagents(opt-in 그룹) 반영. 검증: `arch_check --ci` exit 0, orchestration 87 passed/1 skipped(0 failed) |
| 2026-06-17 | D-037 | **트랙 B 런타임 그래프 배선 완료 (Plan 49 §7 step 7 / §9)**: 그동안 정의·테스트만 되고 어떤 진입점에도 연결되지 않았던 `build_deep_agent`(→`deepagents.create_deep_agent`)를 **실제 실행 경로에 배선**. `src/graph.py`의 `build_graph`가 빌드 시 `select_orchestration_backend(config)`로 백엔드를 확정하여 `"deep_agent"`면 신규 `deep_agent` 노드를 등록하고 `field_mapper → deep_agent → END`로 연결(트랙 A·semantic_router보다 최우선, 상호 배타로 해당 노드 미등록). 신규 `src/orchestration/deep_agent.py:run_deep_agent` 노드가 `build_deep_agent`로 조립한 에이전트를 `ainvoke`하고 최종 메시지를 `final_response`로 추출(ambient 컨텍스트 = thread_id/user_id/allowed_db_ids/양식 등 화이트리스트 주입, §4.4). **안전 폴백 2중**: (1) 빌드 시 `_deep_agent_buildable`가 조립을 시도해 deepagents 미설치(RuntimeError)면 `semantic_router`로 폴백(그래프 크래시 방지) (2) 런타임 `run_deep_agent`도 RuntimeError를 잡아 안내 응답 반환. 기본값(`enable_deepagents_package=False`)에서는 `semantic_router` 선택으로 기존 경로 무변(회귀 없음). 검증: `arch_check --ci` exit 0(error 0, 신규 warning 없음), 신규 `tests/test_orchestration/test_deep_agent_wiring.py` 10건 통과, orchestration 회귀 동일(전·후 모두 기존 2건 실패 — 로컬 `.env`의 `ORCHESTRATOR_PROVIDER=gemini` 누수로 인한 사전 존재 실패, 본 작업과 무관). **실제 deepagents 패키지 동작 확인은 폐쇄망 wheel 반입(§3.1) + vLLM 기동(§3.2) 후 가능** — 본 배선은 그 인프라가 갖춰지면 즉시 동작하도록 완성됨 |
| 2026-06-17 | D-037 | **실제 deepagents 패키지 도입 결정 (Plan 49 개정 — 트랙 B 재진입)**: 2026-06-16 "트랙 B 제거"를 **번복**. 폐쇄망 vLLM 오케스트레이터(OpenAI 호환, **Qwen3.5-9B**, `langchain-openai`의 `ChatOpenAI`로 네이티브 tool-calling)가 deepagents(`write_todos`/`task`)를 구동하고, **FabriX(KBGenAIChat)가 실질 응답처리(자연어→SQL→DB 조회→결과·최종응답)** 를 담당. `SUBAGENT_REGISTRY`→`@tool`(FabriX는 도구 **내부**에서만 호출, SubAgent 모델 직결 금지). **백엔드 선택=vLLM 가용성 옵션**(미서빙 시 `semantic_router`, Track-A replanner는 폴백 보존). 폐쇄망 요건: `langchain-core` 1.4.7 업글 + `langchain`/`deepagents` wheel 반입 + vLLM 인프라. Plan 48 Phase 8 **부활**. PoC 결과는 `docs/deepagents_poc_report.md`. 근거: 트랙 B 재진입 조건(tool-calling 지원 LLM=vLLM) 충족. 대안 기각: Gemini(egress)·KBGenAIChat 에뮬레이션(불안정) |
| 2026-06-16 | D-037 | **Phase 2 구현 완료 (Plan 49 — 결과 기반 동적 재계획 + 진행 추적)**: 신규 `src/orchestration/replanner.py`(결과 평가·후속 task 증분 추가·상한 가드·보수적 종료)·`src/prompts/replanner.py`(REPLANNER_SYSTEM_TEMPLATE). `src/config.py`(`max_replan: int = 3`, R-A3/R-11)·`src/state.py`(`replan_count`/`needs_replan` + 초기값 0/False)·`src/orchestration/agent_orchestrator.py`(**pending task만 실행** + `task_results` 시드 누적, 재진입 시 완료 task 미재실행 R-A2)·`src/graph.py`(`replanner` 노드 + 조건부 루프 엣지 `agent_orchestrator→route_after_orchestrator→replanner→route_after_replanner→{agent_orchestrator\|result_aggregator}`, Phase 1 선형 엣지를 루프로 교체)·`src/orchestration/__init__.py`(replanner export). **핵심 설계 — 증분 추가 채택(R-A1)**: deepagents TodoListMiddleware의 "리스트 전체 교체"와 달리, 완료 task·감사 로그·task_results 보존을 위해 **신규 task만 append**(전체 교체 금지). 종료 시 `task_plan`/`replan_count`를 반환하지 않아 reducer 충돌 방지. 무한 루프 방지: `replan_count ≤ max_replan`(기본 3) 가드 + LLM/JSON 파싱 실패 시 보수적 종료(`needs_replan=False`). 트랙 A(tool-calling 미사용, 프롬프트+JSON `extract_json_from_response`). 하위 호환: 재계획 미발생(needs_replan=False) 시 패턴①② Phase 1과 동일 결과, 플래그 off 시 replanner 노드 미등록·기존 경로 무변. **SSE progress(Plan 49 §3.6/§5 step 7)는 보류** — 핵심 루프 우선, best-effort 미구현. 검증: arch_check --ci exit 0(error 0), `tests/test_orchestration/test_replanner.py` 11종(§6 표 10종 + 예외 종료 변형 1종) 통과, orchestration 52건·그래프 회귀 30건 통과. OUT: clarification_gate(Phase 4)·state offloading(Phase 3)·컨텍스트 압축(Phase 5)·subagent 격리(Phase 6)·deepagents 실제 패키지(트랙 B)는 후속 |
| 2026-06-16 | D-037 | **Phase 1 보강 — 라우팅 신호 보존 구현 (Plan 48 §4.9.6/§8.1, R-14)**: planner→`classify_dbs` 2단계 분리로 **위치→DB 신호가 소실**되던 갭 해소. (1) `prompts/intent_planner.py`에 DB 식별 신호(위치 김포/여의도/은행/공동존, DB명, 환경) 보존 규칙 + 위치 예시 추가. (2) `subagents.classify_dbs`에 `db_descriptions`(Redis 캐시) 주입 복원(기존 semantic_router 대비 누락분). (3) `run_data_query_pipeline` 단일 DB는 `sub_query_context`(정제 질의)를 SQL 생성 입력으로 사용(위치 SQL 누출 방지). 회귀 3건(`test_routing_signal_preservation`) 통과, orchestration 41건·arch_check exit 0. E2E(라이브 LLM) 위치 라우팅 정확도는 통합 검증 과제로 잔존 |
| 2026-06-16 | D-037 | **기본값 전환 + Phase 2 계획 착수**: (1) `enable_deepagent_orchestration`를 `bool|None=None` tri-state로 변경 — **미입력 시 멀티 DB 환경에서 신규 경로 기본 동작**(`model_post_init` `multi_db` 기준 해석, `os.getenv` 제거로 Known Mistakes 2026-06-10 준수). `=false` opt-out 보존. `.env.example`/Plan48 §4.1·§4.7 갱신. (2) **Plan 49**(Phase 2 — 결과 기반 동적 재계획 `replanner` + 조건부 루프 + 진행 SSE) 상세 계획서 작성 |
| 2026-06-16 | D-037 | **Phase 1 구현 완료 (Plan 48 §8)**: 신규 `src/orchestration/`(intent_planner·agent_orchestrator·result_aggregator·subagents) + `src/prompts/intent_planner.py`. `src/config.py`(`enable_deepagent_orchestration`, 기본 False)·`src/state.py`(task_plan/task_results/is_composite)·`src/graph.py`(플래그 분기, semantic_routing보다 우선·상호 배타)·`scripts/arch_check.py`(`src.orchestration` 계층 매핑)·`.env.example` 수정. 패턴 ①(독립 병렬)·②(데이터 의존 순차 `input_from`) 지원, status 추적(pending→in_progress→completed/failed), SUBAGENT_REGISTRY, 계층A pre-check 이식(R-10), 단일 DB 풀 검증·재시도 보존(`_run_single_db_pipeline`, R-09), `_make_isolated_input` 부분 격리(S3), input_from 키 컬럼·100행 상한(R-12). `clarification_needed` 슬롯은 예약만(방출, 인터럽트 미발생 — Phase 4). 트랙 A(tool-calling 미사용, 프롬프트+JSON). 기존 semantic_router/노드 무수정 재사용(하위 호환). 검증: arch_check --ci exit 0(error 0), tests/test_orchestration 38건 통과, 그래프 회귀 30건 통과. OUT: 패턴 ③ 동적 재계획·clarification_gate·state offloading·압축·완전 격리는 후속 Phase |
| 2026-06-16 | D-037 | **모호성 명료화 인터럽트(Clarification HITL) 추가 (Plan 48 §4.11)**: 처리 방법 모호 시 사용자에게 선택지 되묻는 멀티턴 인터럽트. deepagents HumanInTheLoopMiddleware `respond` 대응, 기존 `approval_gate`(노드 `interrupt_before`)와 동형이라 **tool-calling 불필요(트랙 A)**. 감지는 **계획 단계 한정**(intent_planner), **Phase 1=`clarification_needed` 슬롯 예약 / Phase 4=`clarification_gate` 구현**. 기존 HITL 상태필드(D-013) 재사용, `MAX_CLARIFY` 무한 되묻기 방지(R-13) |
| 2026-06-16 | D-037 | deepagents 기반 의도 분해 오케스트레이션 계획 확정 (Plan 48): 단계적 하이브리드 도입(1단계 패턴 자체 구현 / 2단계 격리 PoC), 복합 의도 분해 + 순차·병렬 실행. `intent_planner`→`agent_orchestrator`→`result_aggregator` 신규 orchestration 계층, 5개 작업(data_query/cache_management/synonym_registration/general_inference/alarm_query)을 subagent로 노출, `ENABLE_DEEPAGENT_ORCHESTRATION` 플래그 하위 호환. **deepagents 0.6.10은 langchain 1.x 요구 → 직접 도입 회피, 패턴만 자체 구현.** D-004 확장 / D-005 일반화 |
| 2026-06-16 | D-036 | 알람 영향 프로세스 보강 (Plan 47-1): CPU/메모리 발생 알람에 한해 폴스타 실시간 프로세스 API를 **hostname으로 조회**(Plan 47 DB 이력의 serverName과 정반대 키), 상위 N을 결정적 선별·마스킹하여 패턴 근거에 추가. `alarm_context_enricher`에 프로세스 조회 단계 추가(노드 수 3개 불변, history와 `asyncio.gather` 동시 실행·독립 degradation). process_rank.py(순수 함수)·polestar_process_api.py(httpx GET, 비인증 http, URL 인코딩) 신규, AlarmState.process_snapshot, 프롬프트 `{process_section}`+인용 규칙, notifier workb 표·webhook 필드, app.js/style.css 영향 프로세스 표, AlarmConfig 4필드+`get_process_api_base_url()`, 테스트 API query_process/simulated_processes. **args 민감정보(password/token/접속문자열) mask_args() 마스킹 필수 — 회귀 테스트 고정** |
| 2026-06-11 | D-035 | 알람 이력 기반 패턴 분석 (Plan 47): 폴스타 DB 직접 조회(고정 SQL, lookback 90일, max_rows 2,000), alarm_pattern.py 통계·1차 분류 순수 함수, polestar_history.py 리포지토리(C-6 패턴 서버 매칭, gp/yd r.name), alarm_context_enricher 노드(+Redis 단기 캐시 TTL 300초, 타임아웃 5초, graceful degradation), 3-노드 그래프 전환, 프롬프트/notifier/UI 패턴 필드 확장, 테스트 API query_history/simulated_history. **is_clear를 severity=0 단독 기준으로 정정 — D-032의 alarmStatus='발생'/'해소' 기술은 실측과 다름(폴스타 UI ACK 상태값)** |
| 2026-06-11 | D-034 | 주기적 헬스체크 로그 노이즈 감소: httpx 로거 WARNING 상향, DBHubClient/PostgresClient 연결 성공·종료 로그 INFO→DEBUG 전역 강등. 실패 경로(WARNING/예외) 로그는 유지. 부수: tests/test_dbhub_integration.py 인코딩 깨짐으로 잘못된 단정문 6건 복원 |
| 2026-06-11 | D-033 | 처리 현황 유사어 매핑 표시: src/utils/synonym_usage.py 신규(SQL 리터럴 역조회 + 사전 미등록 감지), query_generator synonym_usage 반환, AgentState 필드 추가, SSE/UI 렌더링 추가 |
| 2026-06-11 | D-033 | 일반 컬럼 매핑 대량 출력 수정: bare 컬럼명 그룹화·중복 제거, 사용자 용어 매칭 항목만 포함, 매핑 상한 15건 (_MAX_MAPPINGS) |
| 2026-06-11 | D-009 | 처리 현황 schema_analyzer "스키마 요약"(schema_summary) 제거: schema_info 중첩 구조를 잘못 읽어 정상 출력된 적 없던 버그성 표시. 관련 테이블 목록과 중복 정보로 판단해 백엔드/프론트/명세(11_web_ui_progress_specification.md) 일괄 제거 |
| 2026-06-09 | D-032 | 폴스타 알람 메시지 포맷 확정 + AlarmEvent 필드 재설계 (Plan 46 개정): 단일행 JSON 템플릿 확정, AlarmEvent 구 필드 제거(alarm_description/alarm_definition/resource_name/resource_description/alarm_state/alarm_conditions/source_db_id/raw_text), 신 필드 추가(db_id/server_name/ip_address/resource_ancestry/alarm_status/conditions/alarm_time/raw_payload), alarm_worker._process() 재작성, 프롬프트/노드/API 라우터 전면 업데이트 |
| 2026-06-04 | D-031 | 알람 소켓 수신 → LLM 분석 → worKB 발송 (Plan 46): alarm_server/ 독립 프로세스, AlarmConfig/WorkbConfig 추가, src/alarm/ 서브패키지 신규, AlarmWorker Redis Stream 소비, 2-노드 AlarmAnalysisGraph, FastAPI lifespan AlarmWorker 등록, arch_check MODULE_LAYER_MAP alarm 계층 추가 |
| 2026-06-01 | D-030 | ALARMSEVERITY=0 해소 상태 이력 쿼리 포함 (Plan 45): domain_config.py 4개 도메인 description 0=해소 추가, query_generator.py 필수 WHERE/심각도 매핑/분기 섹션 수정, Template C-1~C-5 CASE WHEN 0 추가, C-2~C-5 WHERE IN(0,1,2,3) 변경, C-4 해소_수 집계 컬럼 추가, plan 44 심각도 코드표 갱신 |
| 2026-05-29 | D-029 | 알람 조회 의도 분리 (Plan 44): routing_intent="alarm_query" 의도 추가, domain_config.py 4개 도메인 description 보강, semantic_router.py alarm_query 규칙+예시 7건, query_generator.py routing_intent 파라미터 전파, prompts/query_generator.py Template C-1~C-5 신규 상수 추가 |
| 2026-04-02 | D-028 | Polestar 불필요 lookup 테이블 JOIN 차단 (Plan 42): excluded_join_columns에 vendor_id/os_id/os_param_id 추가, allowed_tables 필드 신규, schema_analyzer 테이블 필터링, query_validator 패턴 3 추가 |
| 2026-04-02 | D-027 | 사용자 행위 감사 로깅 강화 (Plan 40): JSONL+PostgreSQL 이중 기록, SQLite 대신 PostgreSQL 확장, 통합 AuditService, AuditMiddleware, 10개 이벤트 유형, 보안 경고 자동 감지 |
| 2026-04-01 | D-026 | 사용자 로그인 및 인증 시스템 (Plan 39): domain/auth.py, domain/user.py, utils/password.py, infrastructure/auth_provider.py, infrastructure/user_repository.py, infrastructure/audit_repository.py, api/dependencies.py, api/routes/user_auth.py 신규. config.py AuthConfig, state.py 사용자 컨텍스트, query.py/conversation.py/admin.py/server.py/schemas.py 수정, arch_check.py 모듈 매핑, ddl/auth_tables.sql, UI login/register/admin 사용자 관리 |
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
