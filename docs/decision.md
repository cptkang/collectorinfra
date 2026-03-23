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
- 유사 단어 운영자 수동 추가분은 LLM 재생성 시에도 보존해야 함

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

## 변경 이력

| 날짜 | 결정 ID | 변경 내용 |
|------|---------|----------|
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
| 2026-03-17 | 전체 | 초기 decision.md 작성 |
