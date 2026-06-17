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
36. [양식 채우기 SQL 정합성 — server.Server 메트릭 조인 분리 + EAV 오탈자 결정적 치환](#d-037-양식-채우기-sql-정합성--serverserver-메트릭-조인-분리--알려진-eav-오탈자-결정적-치환)
37. [양식 채우기 결정적 SQL 빌더 — 메트릭 메타데이터 + 분류기 + 빌더](#d-038-양식-채우기-결정적-sql-빌더--메트릭-메타데이터--분류기--빌더)

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

## D-039. 특정 자원 실시간 프로세스 리스트 조회 + 현황 분석 (Plan 48)

| 항목 | 내용 |
|------|------|
| **결정일** | 2026-06-16 |
| **상태** | 구현 완료 |
| **관련 결정** | D-036(Plan 47-1 프로세스 API 클라이언트/도메인 재사용), D-004(LLM 전용 라우팅), D-013(멀티턴/HITL), D-020(LLM 범용 스키마) |
| **번호 비고** | Plan 48 §8은 "D-037 예정"으로 예약했으나, 그 사이 D-037·D-038이 양식 채우기 SQL 작업에 선점됨. CLAUDE.md 번호 규약(마지막 번호+1)에 따라 **D-039**로 부여 (설계 충돌 아님 — 번호 충돌만 해소) |

### 결정

사용자가 특정 자원의 프로세스 목록/현황을 질의하면, 신규 `process_query` 라우팅 의도로 **전용 노드**(`process_query_node`)를 분기시켜 SQL 파이프라인을 우회하고, 폴스타 실시간 프로세스 API(Plan 47-1 `PolestarProcessApiClient` 재사용)를 **hostname으로 조회**한 뒤 결정적 선별·집계·마스킹(`build_process_overview`) 후 LLM이 현황만 해석한다. 프로세스 선별 원시 함수(`ProcessInfo`/`mask_args`/`select_top_processes`)를 `src/domain/process.py`로 **승격**하여 알람·메인이 공유하고, 알람 도메인은 re-export로 무회귀 보존한다. 서버명/hostname을 문자열로 구분하려 시도하지 않고 `cmm_resource`의 `name`·`hostname` **동시 매칭**(read-only SELECT)으로 정규 hostname을 해석하며, 모호 시 HITL 되물음·미해석 시 직접 hostname 폴백·라우터 db 오선택 대비 타 폴스타 db 재해석 폴백을 둔다.

### 근거

프로세스는 DB(EAV/메트릭)가 아닌 실시간 API에만 존재 → SQL 파이프라인으로 처리 불가. Plan 47-1 자산(API 클라이언트·선별·마스킹) 재사용으로 중복 제거. 결정적 표 + LLM 해석 분리로 환각·토큰·민감정보 위험 차단(47-1 §3.3 계승). 서버명/hostname은 둘 다 임의 문자열이라 패턴 판별이 오판을 부르므로 `name OR hostname` 동시 매칭이 입력 종류와 무관하게 정규 hostname을 흡수한다.

### 대안 (미채택)

| 대안 | 미채택 이유 |
|------|-----------|
| ① SQL 파이프라인 내 특수 분기 | 외부 HTTP를 SQL 노드에 섞어 응집 저하 — 전용 노드 분기 채택 |
| ② 메인이 `src/alarm`을 직접 import | 서브시스템 결합 — 공유 도메인(`src/domain/process.py`) 승격 채택 |
| ③ 원시 프로세스를 LLM에 그대로 주입 | 토큰·환각·민감정보 노출 — 결정적 선별+마스킹 채택 |
| ④ 서버명/hostname을 정규식으로 구분 | 둘 다 임의 문자열이라 오판 — `name OR hostname` 동시 매칭 채택 |

### 핵심 설계 결정

| 항목 | 결정 |
|------|------|
| **조회 키 (키 차이 주의)** | 프로세스 API는 **hostname**, DB(`cmm_resource`)는 `name`(서버명)/`hostname` 둘 다 보유 → 노드가 name→hostname 변환·모호성 해소 책임 (47-1 §9·D-036 연계) |
| **hostname 해석 순서** | ① 활성 db DB해석(name OR hostname 동시 매칭, 정확 hostname 우선 `ORDER BY CASE`, 2건까지 조회) → 1건 확정 / 모호(2건↑) HITL 후보 안내(임의 선택 금지) / 0건 → ② identifier를 직접 hostname으로 API 시도 → ③ 타 폴스타 db 재해석(매핑 db 수 상한) → 끝내 미해석 시 안내 종료(추측 금지) |
| **user_specified_db 시 ③ 스킵** | "김포 폴스타의 ### 서버"처럼 db 명시 시 `semantic_router`가 `domain_config.py` alias로 `active_db_id`·`user_specified_db` 확정 → ③ 다중-db 폴백 미발동(해당 db에 없으면 "그 폴스타에 없음" 안내). `state.user_specified_db` truthy 시 ③ 루프 스킵 |
| **라우팅 오버라이드** | `semantic_router`가 일반 data_query처럼 LLM 분류로 `target_databases`/`active_db_id`/`user_specified_db`를 정상 결정한 뒤, `parsed_requirements.process_query` 신호(또는 LLM 직접 intent)가 있으면 `routing_intent`만 `"process_query"`로 오버라이드 — 무-DB 분기(cache/general)를 타지 않으면서 `active_db_id`가 확정된 채 노드로 분기 |
| **read-only / 인젝션 방지** | hostname 해석은 단일 SELECT + `_sql_literal()` 이스케이프(작은따옴표 `''`+`\x00` 제거, DBHub 바인딩 미지원 대응) + `polestar.cmm_resource` 스키마 한정 + db_engine별 LIMIT(db2=`FETCH FIRST 2 ROWS ONLY`, postgresql=`LIMIT 2`). INSERT/UPDATE/DDL 생성 금지 |
| **결정적 선별 + LLM 해석** | 정렬(cpu=p100cpu→pcpu 폴백, memory=pmem)·상위 N·집계(상위 점유 합/고유 계정 수/최다 계정)·마스킹은 순수함수. LLM은 마스킹된 결정적 요약만 인용·해석, 수치 재계산·`***` 복원·0건 추측 금지 |
| **민감정보 마스킹 (필수)** | 프로세스 `args`는 `mask_args()` 후에만 LLM·UI·엑셀에 노출 — 평문 비밀번호/토큰/접속문자열 비노출을 단위 테스트로 회귀 고정 |
| **graceful degradation** | DB 해석/API 호출 각각 try/except + 타임아웃(`process_query_resolve_timeout_seconds`/`process_api_timeout_seconds`). 미주입·미설정·비폴스타·미매핑 db·0건 모두 안내 텍스트로 종료(추측 없음) |
| **설정 분리** | 신규 `ProcessQueryConfig`(env_prefix `PROCESS_QUERY_`) — `process_query_enabled`/`process_query_top_n`(10)/`process_query_resolve_timeout_seconds`(3). base_url 매핑·API 타임아웃은 `AlarmConfig.get_process_api_base_url`/`process_api_timeout_seconds` 재사용(동일 엔드포인트). AlarmConfig에 사용자 조회 설정을 섞지 않음 |
| **도메인 승격 무회귀** | `ProcessInfo`/`mask_args`/`select_top_processes`를 `src/domain/process.py`로 이동, `src/alarm/domain/{alarm,process_rank}.py`는 re-export — 47-1 테스트(63건) 전수 통과로 무회귀 확인 후 진행. `classify_alarm_kind`만 `AlarmEvent` 의존이라 알람 도메인 잔류 |
| **출력 채널** | `output_format=xlsx/docx`면 `process_query_node`가 `organized_data.rows` 구성 → `output_generator` 위임(문서 생성 재사용). 텍스트면 END. UI는 기존 `.alarm-proc-table`/`renderProcessEvidence` 재사용 |
| **`.env` 신규 필드** | 스칼라만 (`PROCESS_QUERY_ENABLED`/`_TOP_N`/`_RESOLVE_TIMEOUT_SECONDS`) — list/dict JSON 없음 (Known Mistakes 2026-03-23 비해당) |

### 변경된 파일

| 파일 | 변경 내용 | 계층 |
|------|----------|------|
| `src/domain/process.py` | 신규 — 47-1에서 `ProcessInfo`/`mask_args`/`select_top_processes` 승격 + `ProcessOverview`/`build_process_overview`/`resolve_metric_from_text` | domain |
| `src/alarm/domain/alarm.py` | `ProcessInfo` 정의 삭제 → `src.domain.process`에서 re-export (`ProcessSnapshot` 잔류) | domain |
| `src/alarm/domain/process_rank.py` | `mask_args`/`select_top_processes`/헬퍼·정규식 상수 re-export (`classify_alarm_kind` 잔류) | domain |
| `src/infrastructure/polestar_host_resolver.py` | 신규 — `HostResolution`, `PolestarHostResolver`(read-only SELECT, `_sql_literal` 이스케이프, db_engine별 LIMIT, name/hostname 동시 매칭, 모호 2건 감지) | infrastructure |
| `src/nodes/process_query_node.py` | 신규 — 게이팅 + §3.4 해석 순서 + API 재사용 + overview + LLM 분석 + degradation + organized_data 위임 | application |
| `src/prompts/process_query.py` | 신규 — 현황 분석 프롬프트(결정적 수치만 인용·재계산/마스킹 복원/0건 추측 금지) | prompts |
| `src/nodes/input_parser.py` | `_build_process_query_target()` + 조건부 `process_query_target` 반환 | application |
| `src/prompts/input_parser.py` | 규칙 14 — process_query 신호 추출(identifier/metric/top_n) | prompts |
| `src/routing/semantic_router.py` | process_query intent 오버라이드(DB 결정 보존 후 routing_intent만 변경) | orchestration |
| `src/prompts/semantic_router.py` | intent 목록에 process_query 설명 추가 | prompts |
| `src/graph.py` | `_INTENT_ROUTE_MAP`·노드 등록(`PolestarProcessApiClient`/`PolestarHostResolver`/`get_db_client` 주입)·조건부 엣지·`route_after_process_query` | orchestration |
| `src/api/routes/query.py` | SSE `process_query` 노드 블록(`process_overview` 전달, 마스킹 dict) | interface |
| `src/static/js/app.js` | intentMap 라벨, `process_overview` 프로세스 표 렌더(`.alarm-proc-table` 재사용) | static |
| `src/config.py` | `ProcessQueryConfig` + `AppConfig.process_query` | config |
| `src/state.py` | `AgentState.process_query_target`/`process_overview` + 초기화 | orchestration |
| `.env.example` | `PROCESS_QUERY_*` 3개 (스칼라) | 설정 |
| `tests/test_process_overview.py` | 신규 — build_process_overview 정렬·집계·마스킹 회귀·엣지 (17건) | 테스트 |
| `tests/test_polestar_host_resolver.py` | 신규 — read-only SQL·이스케이프·db_engine별 LIMIT·1/2건/0건/예외 | 테스트 |
| `tests/test_process_query_node.py` | 신규 — 해석 순서·폴백·모호·user_specified 스킵·게이팅·마스킹 비노출 | 테스트 |
| `tests/test_process_query_integration.py` | 신규 — 시나리오·라우터 오버라이드·input_parser·graph 배선 (15건) | 테스트 |

### 향후 수정 시 고려사항

- 실 폴스타 프로세스 API end-to-end, 실 `cmm_resource` 서버명→hostname 해석 정확도, base_url 매핑 도달성, xlsx/docx 실제 생성, 외부 호출 audit 로깅 부합은 **운영 환경 수동 확인** 필요 (§7 일부)
- 새 폴스타 인스턴스 추가 시 `ALARM_PROCESS_API_BASE_URLS_CSV`에 항목 추가하면 프로세스 조회도 자동 지원
- 향후 base_url/타임아웃을 `ProcessApiConfig`로 추출해 AlarmConfig와 분리 가능(현재는 동일 엔드포인트라 재사용)
- 사전 존재 실패: `tests/test_semantic_routing/test_semantic_router.py::TestLLMClassify` 5건은 `_llm_classify`가 intent 도입(D-029) 이후 dict를 반환하는데 테스트가 list를 가정해 발생 — Plan 48과 무관(stash 후에도 동일 실패), 별도 테스트 갱신 권장

---

## D-040. process_query 출력 채널 보강 — 스트리밍 / args 전달 / CSV (Plan 48 §10)

| 항목 | 내용 |
|------|------|
| **결정일** | 2026-06-17 |
| **상태** | 구현 완료 |
| **관련 결정** | D-039(process_query 도입 — 핵심 파이프라인 불변) |

### 배경 / 문제

D-039 구현 후 사용자 피드백 3건: (1) `process_query` 응답이 토큰 스트리밍 없이 한 번에 출력됨, (2) LLM이 `args` 등 정보 부족을 이유로 "데이터 부족/추가 확인 필요"를 출력, (3) 프로세스 조회 결과를 CSV로 받을 수 없음.

### 결정

핵심 결정(D-039)과 파이프라인은 **불변**으로 두고 **출력 채널 배선만** 보강한다.

| 사안 | 원인 | 조치 |
|------|------|------|
| **스트리밍** | SSE 스트리밍(`query.py`)이 `process_query`를 화이트리스트에서 누락 — `_known_nodes`(2곳)에 없어 진행 패널·프로세스 표 미렌더, `on_chat_model_stream` 노드 필터가 `output_generator`/`general_inference`만이라 노드 LLM 토큰 미스트리밍 → `on_chain_end` 폴백이 전체 응답을 단일 청크로 전송 | `/query/stream`·`/query/file/stream` 두 제너레이터의 `_known_nodes`(2곳)와 `on_chat_model_stream` 노드 필터(2곳)에 `"process_query"` 추가. 노드는 기존 `llm.ainvoke` 유지 — `output_generator`와 동일하게 `astream_events`가 토큰 포착 |
| **args 전달 (버그 아님)** | API 필드 `args`(47-1 §2.2)는 `_to_process_info`가 `mask_args()`로 마스킹해 보관하고 `_build_summary_text`가 상위 프로세스마다 포함 — 이미 LLM에 전달됨. "데이터 부족" 출력은 프롬프트가 "상위 N 요약"을 불완전 데이터로 오해한 프레이밍 문제 | `process_query` 프롬프트에 "상위 N + 전체 건수 + 집계 = 현황 분석에 충분. `total_count>0`·상위 목록 존재 시 '데이터 부족'이라 하지 말 것. '데이터 부족/추가 확인'은 0건·hostname 미해석 등 실제 데이터 없을 때만" 규칙 추가. 요약의 빈 args는 `(없음)`으로 명시, args는 서비스 식별 근거로 해석하되 `***` 복원 금지 |
| **CSV** | `download-csv`는 저장소 `query_results`를 CSV화하고 UI 버튼은 `row_count>0`일 때 노출 — `process_query`가 `query_results` 미설정이라 404·버튼 미표시 | `process_query_node`가 성공 시 **전체 프로세스(마스킹 args 포함)**를 기본 지표 내림차순으로 정렬해 `query_results`(평면 dict 행, 상한 10,000)로 반환 → 기존 CSV 엔드포인트·UI 버튼 무변경 재사용. `_text_only` 조기 종료(게이팅/모호/미해석/0건)는 `query_results` 미설정 → CSV 미표시(정상) |

### 근거

기존 스트리밍·CSV 인프라가 노드 화이트리스트와 `query_results` 키에만 의존하므로, 엔드포인트·프런트엔드 변경 없이 배선만으로 3건을 해소. args는 이미 전달되고 있었고(보안 마스킹 유지), 문제는 LLM 프레이밍이라 프롬프트로 교정. CSV는 LLM 주입 요약(top N)과 분리된 결정적 전체 행으로 제공해 "전체 목록" 요구를 충족하면서 max rows 10,000·마스킹 제약 준수.

### 변경된 파일

| 파일 | 변경 내용 | 계층 |
|------|----------|------|
| `src/api/routes/query.py` | `_known_nodes`(2곳)·`on_chat_model_stream` 노드 필터(2곳)에 `"process_query"` 추가 | interface |
| `src/nodes/process_query_node.py` | `_full_process_rows()`(전체 마스킹 행, 상한 `_MAX_CSV_ROWS=10000`) + 성공 시 `query_results` 반환, `_build_summary_text`에 "전체 N건 중 상위 K개"·CSV 안내·빈 args `(없음)` 표기 | application |
| `src/prompts/process_query.py` | "데이터 부족" 오프레이밍 교정 규칙(5)·args 해석 규칙(6) 추가, 분석 항목 4 조건화 | prompts |

### 향후 수정 시 고려사항

- 스트리밍 토큰·진행 패널·프로세스 표 렌더, CSV 전체 목록 다운로드는 **운영 환경에서 수동 확인** 필요(실 API 연동)
- 프로세스 수가 비정상적으로 많은 서버(>10,000)는 CSV가 상위 10,000행으로 절단됨 — 현재 현실 범위 밖

---

## D-037. 양식 채우기 SQL 정합성 — server.Server 메트릭 조인 분리 + 알려진 EAV 오탈자 결정적 치환

| 항목 | 내용 |
|------|------|
| **결정일** | 2026-06-16 |
| **상태** | 확정 |
| **관련** | D-016(EAV 쿼리), D-022(조인 규칙), D-033(유사어 매핑 표시) |

### 배경 / 문제

여의도 공동존 폴스타 양식 채우기에서 **서버명/호스트명/IP/OS 컬럼이 전부 NULL**(컬럼 자체는 존재, 값만 빔)인 반면 CPU/메모리 용량·사용률은 정상 출력되는 현상. 두 가지 독립 원인이 동시에 작용:

1. **메트릭 INNER JOIN으로 server.Server 행 탈락**: 생성된 SQL이 server.Server EAV 피벗에 성능 통계(`cmm_metric_stat_*`)를 단일 평면 조인(`JOIN ... ON r.id = s.resource_id`)으로 묶음. 메트릭은 server.Cpus/server.Memory 등 하위 리소스에만 존재하므로 server.Server 행이 INNER JOIN으로 전부 탈락 → server.Server에서 뽑는 모든 CASE 컬럼만 NULL.
2. **EAV 속성명 오탈자 자동 교정**: LLM이 DB 실제 속성명 `'OSVerson'`(폴스타 제품 오탈자)을 정상 철자 `'OSVersion'`으로 자동 교정해 생성 → EAV NAME 매칭 0건. 프롬프트에 정확한 값을 제공해도 재발.

### 결정

1. **프로필 가이드/예시 (버그1)**: 5개 polestar 프로필(`polestar.yaml`, `polestar_pg.yaml`, `polestar_cm_gp.yaml`, `polestar_cm_yd.yaml`, `polestar_b0.yaml`) query_guide에 `[★ 양식 채우기 / 성능 통계 조인 시 server.Server 행 탈락 주의]` 규칙 추가. 식별/OS는 (a) 서버 행 `svr`(`svr.id = r.platform_resource_id`)의 직접 컬럼/EAV, 또는 (b) 메트릭과 분리된 서브쿼리에서 `platform_resource_id`로 조인해 조회. `polestar_cm_yd.yaml`에 식별+OS+설정+사용률 통합 few-shot 예시 추가.
2. **결정적 치환 (버그2)**: `query_generator`에서 생성 SQL의 따옴표 리터럴 `'OSVersion'` → `'OSVerson'`을 결정적으로 치환(`_fix_known_attribute_typos()` / `_KNOWN_ATTRIBUTE_TYPO_FIXES`). SQL 추출 직후·`extract_synonym_usage` 이전에 적용하여 처리 현황 표시·실행 모두 보정값 사용. 프로필 query_guide에도 정정 금지 경고 명시(보조).

### 근거

- **D-003 정신 계승(LLM 신뢰 불가)**: 오탈자 자동 교정은 프롬프트로 막기 어려움이 실증됨 → 좁은 범위의 결정적 후처리를 1차 방어로 둔다.
- INNER JOIN grain 문제는 가이드 텍스트만으로 재발 위험 → 올바른 구조의 few-shot 예시로 고정.

### 적용 범위 / 부작용

- 치환은 **따옴표로 감싼 리터럴만** 대상 → alias(`AS os_version` 등)·snake_case 식별자 영향 없음.
- `'OSVersion'` 리터럴은 폴스타 EAV NAME 비교에만 의미가 있어 타 DB 부작용 없음.

### 향후 수정 시 고려사항

- 새 오탈자 속성명 발견 시 `_KNOWN_ATTRIBUTE_TYPO_FIXES`에 한 줄 등록(코드 단일 지점).
- 정적 속성 + 메트릭을 함께 조회할 때는 항상 식별/속성 경로와 메트릭 경로를 분리.

### 변경 파일

| 파일 | 변경 | 계층 |
|------|------|------|
| `src/nodes/query_generator.py` | `_fix_known_attribute_typos()`/`_KNOWN_ATTRIBUTE_TYPO_FIXES` 신규, SQL 추출 직후 호출 | application |
| `config/db_profiles/polestar*.yaml` (5개) | query_guide 규칙 추가; yd에 통합 few-shot 예시 | 설정 |
| `CLAUDE.md` | Known Mistakes 2건 추가 | 문서 |

---

## D-038. 양식 채우기 결정적 SQL 빌더 — 메트릭 메타데이터 + 분류기 + 빌더

| 항목 | 내용 |
|------|------|
| **결정일** | 2026-06-16 |
| **상태** | 진행 중 (Phase 1~2 완료, Phase 3 예정) |
| **관련** | D-007(문서처리), D-012(Mapping-First), D-016(EAV), D-037 |

### 배경 / 문제

양식 채우기(서버 인벤토리+사용량) 리포트가 **실행마다 결과가 달라짐**(예: 1651건 설정만 출력 / 389건 사용률만 출력). 원인은 LLM이 매 실행 SQL을 새로 생성하며 "설정 피벗 ↔ 메트릭 조인" 두 패턴을 비결정적으로 오가기 때문(temperature는 이미 0). 모양이 고정된 리포트는 LLM 재생성만으로는 일관성을 보장할 수 없다.

### 결정 (전체 방향)

양식 채우기처럼 모양이 고정된 폴스타 리포트는 field_mapper 매핑을 입력으로 **코드가 결정적으로 SQL을 생성**한다(Mapping-First 확장, D-012). 단 **분류-아니면-폴백** 원칙: 모든 필드가 분류 가능할 때만 결정적 생성하고, 하나라도 미분류면 기존 LLM 경로로 폴백한다. 알람/프로세스/임의 분석 등 모양이 불특정한 질의는 계속 LLM이 담당한다.

### 단계화

- **Phase 1 (완료)**: 메트릭 메타데이터 + 분류기 토대 마련 (라이브 동작 무변경).
- **Phase 2 (완료)**: 결정적 SQL 빌더 + query_generator 게이팅 + **재시도 시 LLM 폴백** + alias 규약 준수.
- **Phase 3 (예정)**: 디스크/파일시스템/네트워크/특정서버 필터 등 도메인·조건 확장.

### Phase 1 구현

1. **구조화 메타데이터 `metric_patterns` 신설** (5개 polestar 프로필). cmm_metric_stat_[h,d,m] 테이블·값컬럼(min/avg/max_val)·시간단위→테이블 매핑·집계어 인식·지표(resource_type + definition_name + 단위 + 도메인어/동의어). 기존 로더(`_load_manual_profile` → `schema_analyzer`)가 `source` 제외 전 키를 structure_meta로 복사하므로 **자동 surface**(schema_analyzer 수정 불필요).
2. **순수 유틸 `src/utils/metric_classifier.py`**: `classify_metric_field()`(양식 필드 → `MetricFieldSpec`), `detect_aggregation()`, `resolve_stat_table()`, `load_metric_patterns()`. 판정 규칙: (1) 완전 동의어 매칭, (2) 도메인어+집계어 조합. "CPU 코어 수"·"메모리 용량"은 메트릭 아님(None)으로 EAV/직접컬럼에 양보.

### Phase 2 구현

1. **결정적 빌더 `src/utils/report_sql_builder.py`** (순수 함수). 각 양식 필드를 (ⓐ직접컬럼 / ⓑEAV / ⓒ메트릭)으로 분류 후 폴스타 인벤토리+사용량 SQL을 생성. **하나라도 미분류면 None 반환 → LLM 폴백**(분류-아니면-폴백).
   - 구조: **단일 피벗 + 메트릭 LEFT JOIN(ON절 stat_date 필터)** → server.Server 행이 메트릭 INNER JOIN으로 탈락하지 않음(D-037 내장). EAV는 소유 resource_type별 `MAX(CASE …)`로 분리.
   - **value_joins로 직접컬럼 등가가 있는 식별 속성(Hostname↔hostname, IPaddress↔ipaddress)은 직접컬럼 사용** → 공동존처럼 식별 EAV가 비어도 안전.
   - EAV 소유 resource_type은 `known_attributes_detail` description의 `[resource_type: X]`에서 해석, 모호(예: TotalSize=Memory/Disks)하면 필드 도메인어로 해소, 그래도 모호하면 미분류→폴백.
   - definition_name·EAV 속성명은 메타데이터 상수 → 오타(OSVerson 등) 불가.
2. **query_generator 게이팅** (`_try_build_deterministic_sql`): 폴스타 양식 채우기(xlsx/docx) + column_mapping 존재 + **필터/멀티DB 없음** + 전 필드 분류 성공 시에만 결정적 SQL 사용. 그 외/예외는 기존 LLM 경로.
3. **재시도 LLM 폴백**: `is_retry`(validator/executor/충분성 회귀)면 빌더를 건너뛰고 LLM이 생성 → 결정적 SQL이 어떤 이유로 실패해도 무한 동일에러 루프 없이 LLM으로 복구.
4. **alias 규약**: SELECT alias를 column_mapping 값과 동일하게(EAV는 `EAV:attr`, 직접은 `table.column`, 메트릭은 `metric_<name>_<agg>`) 부여하고, 빌더가 산출한 `field_aliases`로 `column_mapping`을 갱신 → 다운스트림(result_organizer/excel_writer) 셀 매핑이 정확히 일치.
5. **킬 스위치 (3-f)**: `QueryConfig.enable_deterministic_report_sql`(env `QUERY_ENABLE_DETERMINISTIC_REPORT_SQL`, 기본 true). false면 빌더를 건너뛰고 항상 LLM 생성 → 운영 중 문제 시 즉시 LLM 전용으로 회귀하는 안전장치.

### 근거

- 매핑은 이미 확보된 정보 → 기계적 SQL 조립은 코드가 결정적·완전·테스트 가능. LLM이 가장 흔들리는 지점.
- 메트릭은 매핑이 표현할 수단이 없던 영역(definition_name/집계/시간단위) → 구조화 메타데이터로 정형화해야 결정적 생성이 가능.

### 영향 범위 / 부작용 (Phase 1)

- **라이브 동작 무변경**: metric_patterns가 structure_meta로 흐르지만 `_format_structure_guide()`는 이 키를 읽지 않는다(특정 키만 `.get()`). 분류기는 Phase 2 배선 전까지 휴면이며, metric_patterns가 없는 DB에서는 분류기가 항상 None → 무영향.
- arch_check 0 위반, metric_classifier 단위/통합 테스트 26건(사용자 양식 컬럼 분류 검증 포함).

### 향후 수정 시 고려사항

- 새 지표/도메인은 프로필 `metric_patterns.metrics`에 항목 추가만으로 확장(코드 변경 불필요).
- 결정적 빌더가 의심되는 동작을 보이면 `QUERY_ENABLE_DETERMINISTIC_REPORT_SQL=false`로 즉시 LLM 전용 회귀.

### Phase 3 후보 (백로그 — 미반영, 추후 반영 여부 결정)

현재 아래 유형은 모두 **LLM 폴백**으로 처리된다(동작은 정상, 다만 결정적이지 않음). 수요가 확인되면 "분류-아니면-폴백" 원칙을 유지한 채 결정적 경로로 점진 편입한다.

| 후보 | 현재 | 작업 내용 | 우선순위/난이도 | 비고 |
|------|------|----------|----------------|------|
| **3-a 행 필터** | 필터 있으면 LLM | filter_conditions를 직접컬럼 WHERE/HAVING로 결정적 생성(서버명→`r.name`, avail_status 0=정상 등). 모든 필터가 알려진 컬럼일 때만 결정적, 아니면 폴백 | 높음 / 중 | 가장 흔한 케이스(특정 서버·비정상 서버). EAV 필터(OS=Linux)는 HAVING/서브쿼리 필요 → 후속 |
| **3-b 비메트릭 EAV 도메인 확장** | 대부분 이미 동작 | DiskCount/SwapTotalSize/CPU 모델 등 1:1 속성 — `[resource_type]` 태그 검증·보강 | 낮음 / 하 | 메트릭 도메인(파일시스템/디스크IO)은 메타데이터 기반이라 이미 커버됨(검증만) |
| **3-c 1:N 리소스(네트워크 인터페이스)** | LLM 폴백 | 서버당 NIC 다중 → "한 서버=한 행" 피벗으로 표현 불가. 서버×NIC 다중행 별도 셰이프/빌더 필요 | 낮음 / 상 | 구조적으로 다름 → 폴백 유지 권장 |
| **3-d 멀티시트 양식** | 통합 매핑만 | 시트별 매핑(`sheet_mappings`)이 query_generator 시점엔 없음 → 시트별 빌드 위해 파이프라인 손질 필요 | 낮음 / 상 | |
| **3-e 시간 범위 정교화** | 최신 1개 기간(MAX stat_date) | "지난 N개월 평균", 특정 월/기간 `BETWEEN` 지원 | 중 / 중 | 현재도 "지난 1개월/현재"는 정확(기간 집계 통계) |
| **3-f 스키마 접두사 메타데이터화** | `polestar.` 하드코딩 | DB별 스키마가 다를 때 대비해 메타데이터로 분리 | 낮음 / 하 | 현재 모든 프로필 예시가 `polestar.`로 일치, 불일치 시 실행에러→LLM 폴백 |

추천 진행 순서(수요 발생 시): **3-a 행 필터 → 3-e 시간 범위 → 3-b 검증**. 3-c/3-d는 별도 수요 확인 후.

### 변경 파일 (Phase 1)

| 파일 | 변경 | 계층 |
|------|------|------|
| `config/db_profiles/polestar*.yaml` (5개) | `metric_patterns` 메타데이터 추가 | 설정 |
| `src/utils/metric_classifier.py` | 신규 — 메트릭 분류 순수 유틸 | utils |
| `tests/test_utils/test_metric_classifier.py` | 신규 — 26건 | 테스트 |

### 변경 파일 (Phase 2)

| 파일 | 변경 | 계층 |
|------|------|------|
| `src/utils/report_sql_builder.py` | 신규 — 결정적 SQL 빌더 순수 유틸 | utils |
| `src/nodes/query_generator.py` | `_try_build_deterministic_sql`/`_infer_resolution`/`_safe_extract_synonym_usage` 추가, 빌더 게이팅 early-return(재시도 시 LLM 폴백), 킬 스위치 연동, 유사어 역조회 리팩터 | application |
| `src/config.py` | `QueryConfig.enable_deterministic_report_sql`(킬 스위치, 기본 true) | config |
| `.env.example` | `QUERY_ENABLE_DETERMINISTIC_REPORT_SQL` 항목 추가 | 설정 |
| `tests/test_utils/test_report_sql_builder.py` | 신규 — 빌더 단위/통합 | 테스트 |
| `tests/test_nodes/test_query_generator.py` | 게이팅·폴백·킬 스위치 테스트 추가 | 테스트 |

---

## 변경 이력

| 날짜 | 결정 ID | 변경 내용 |
|------|---------|----------|
| 2026-06-17 | D-040 | process_query 출력 채널 보강 (Plan 48 §10) — 핵심 파이프라인(D-039) 불변, 배선만 변경: (1) **스트리밍** — SSE `query.py` 두 제너레이터의 `_known_nodes`(2곳)·`on_chat_model_stream` 노드 필터(2곳)에 `"process_query"` 추가 → 토큰 스트리밍·진행 패널·프로세스 표 렌더(노드는 `ainvoke` 유지, `astream_events`가 토큰 포착). (2) **args(버그 아님)** — args는 이미 마스킹돼 LLM에 전달 중이었고, "데이터 부족" 출력은 프롬프트 프레이밍 문제 → `process_query` 프롬프트에 "상위 N+전체 건수+집계=현황 분석 충분, total_count>0·상위목록 존재 시 '데이터 부족' 금지, 0건/미해석에서만 사용" 규칙·args 해석 규칙 추가, 빈 args `(없음)` 표기. (3) **CSV** — `process_query_node`가 성공 시 전체 프로세스(마스킹 args, 상한 10,000)를 `query_results`로 반환 → 기존 download-csv·UI 버튼 무변경 재사용, 조기 종료엔 미설정(미표시). 변경 3파일(query.py/process_query_node.py/process_query.py) |
| 2026-06-16 | D-039 | 특정 자원 실시간 프로세스 리스트 조회 + 현황 분석 (Plan 48): 신규 `process_query` 라우팅 의도로 전용 노드(`process_query_node`) 분기, 폴스타 실시간 프로세스 API(47-1 `PolestarProcessApiClient` 재사용)를 **hostname으로 조회** 후 결정적 선별·집계·마스킹(`build_process_overview`)·LLM 현황 해석. `ProcessInfo`/`mask_args`/`select_top_processes`를 `src/domain/process.py`로 승격(알람 도메인 re-export 무회귀, 63건 통과). 서버명/hostname 구분 시도 없이 `cmm_resource` `name OR hostname` 동시 매칭(read-only SELECT, `_sql_literal` 이스케이프, db_engine별 LIMIT, 정확 hostname 우선)으로 정규 hostname 해석, 모호 2건↑ HITL 후보 안내·미해석 직접 hostname 폴백·라우터 db 오선택 대비 타 폴스타 db 재해석(**user_specified_db 시 ③ 스킵**). `semantic_router`는 DB 결정 보존 후 routing_intent만 오버라이드. `ProcessQueryConfig`(PROCESS_QUERY_*, base_url은 AlarmConfig 재사용), input_parser 규칙 14, graph 배선(노드 주입·조건부 엣지·`route_after_process_query`), SSE/app.js 출력. **args 민감정보 mask_args() 마스킹 필수 — LLM·UI·엑셀 평문 비노출 회귀 고정**. 테스트 신규 4파일(120건 전수 통과), arch_check 위반 0. **번호 비고: Plan 48 §8은 D-037 예약했으나 D-037/D-038이 선점되어 규약대로 D-039 부여** |
| 2026-06-16 | D-038 | 양식 채우기 결정적 SQL 빌더 Phase 2: `src/utils/report_sql_builder.py` 신규 — 양식 필드를 직접컬럼/EAV/메트릭으로 분류 후 **단일 피벗 + 메트릭 LEFT JOIN(ON절 필터)** 구조로 결정적 생성(server.Server 탈락 방지), value_joins로 Hostname/IPaddress 직접컬럼 대체(공동존 안전), TotalSize 등 모호 resource_type은 도메인어로 해소·실패 시 폴백. query_generator에 `_try_build_deterministic_sql` 게이팅(폴스타 양식채우기+필터/멀티DB 없음+전필드 분류 성공 시만) + **재시도 시 LLM 폴백** + alias 규약(field_aliases로 column_mapping 갱신). 미분류/비대상/예외는 기존 LLM 경로. **킬 스위치 `QUERY_ENABLE_DETERMINISTIC_REPORT_SQL`(QueryConfig, 기본 true) 추가 — false 시 즉시 LLM 전용 회귀(3-f)**. Phase 3 후보(행 필터/시간범위/네트워크/멀티시트 등)는 D-038 백로그로 기록. 테스트(빌더 단위·통합 + 게이팅·폴백·킬스위치) 추가 |
| 2026-06-16 | D-038 | 양식 채우기 결정적 SQL 빌더 Phase 1: 5개 polestar 프로필에 구조화 메타데이터 `metric_patterns`(stat_tables h/d/m, value_columns min/avg/max_val, aggregations 인식어, metrics=resource_type+definition_name+단위+도메인어/동의어) 신설. 순수 유틸 `src/utils/metric_classifier.py` 신규(classify_metric_field/detect_aggregation/resolve_stat_table/load_metric_patterns) — "CPU 평균"·"메모리 최대"를 메트릭으로 결정적 분류, "CPU 코어수"·"메모리 용량"은 None(EAV 양보). 라이브 동작 무변경(분류기 Phase 2 배선 전 휴면, metric_patterns는 _format_structure_guide가 읽지 않음). 테스트 26건. **Phase 2(빌더+게이팅+재시도 LLM 폴백), Phase 3(도메인 확장) 예정** |
| 2026-06-16 | D-037 | 양식 채우기 SQL 정합성: (1) 5개 polestar 프로필 query_guide에 `[★ 양식 채우기 / 성능 통계 조인 시 server.Server 행 탈락 주의]` 추가 + yd 통합 few-shot 예시 — 메트릭(cmm_metric_stat_*) 단일 평면 INNER JOIN 시 server.Server 행 탈락으로 식별/OS 컬럼 전체 NULL 방지. (2) query_generator `_fix_known_attribute_typos()`로 생성 SQL 리터럴 `'OSVersion'`→`'OSVerson'`(폴스타 오탈자 실제값) 결정적 치환 — LLM 자동 교정 무력화, 따옴표 리터럴만 대상이라 alias 무영향. CLAUDE.md Known Mistakes 2건 |
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
