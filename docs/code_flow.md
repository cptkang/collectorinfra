# 코드 흐름 상세 가이드

> 프롬프트(사용자 입력)에 따라 코드가 어떤 경로로 실행되는지를 설명한다.
> 작성일: 2026-03-24

---

## 목차

1. [전체 아키텍처 개요](#1-전체-아키텍처-개요)
2. [API 진입점](#2-api-진입점)
3. [그래프 구조](#3-그래프-구조)
4. [공통 전처리 노드](#4-공통-전처리-노드)
5. [시멘틱 라우팅 분기](#5-시멘틱-라우팅-분기)
6. [프롬프트별 실행 경로](#6-프롬프트별-실행-경로)
7. [재시도 및 에러 처리](#7-재시도-및-에러-처리)
8. [설정 플래그에 따른 그래프 변형](#8-설정-플래그에-따른-그래프-변형)

---

## 1. 전체 아키텍처 개요

```
┌─────────────────────────────────────────────────────────────────┐
│  FastAPI (src/api/server.py)                                    │
│                                                                 │
│  POST /api/v1/query          ─┐                                │
│  POST /api/v1/query/stream   ─┼─→ graph.ainvoke(state, config) │
│  POST /api/v1/query/file     ─┘                                │
└────────────────────────────────┬────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────┐
│  LangGraph StateGraph (src/graph.py)                            │
│                                                                 │
│  AgentState를 읽고/쓰며 노드를 순차 실행한다.                    │
│  각 노드는 state dict의 일부 필드만 업데이트하여 반환한다.        │
└─────────────────────────────────────────────────────────────────┘
```

**핵심 원칙**: 모든 노드는 `AgentState`(TypedDict)를 입력으로 받고, 변경할 필드만 담은 `dict`를 반환한다. LangGraph가 기존 state에 merge한다.

---

## 2. API 진입점

### 2.1 `POST /api/v1/query` — 텍스트 질의 (`src/api/routes/query.py:207`)

| 항목 | 값 |
|------|----|
| 입력 | `QueryRequest(query, thread_id?)` |
| State 생성 | `create_initial_state(user_query=query, thread_id=...)` |
| 그래프 호출 | `graph.ainvoke(state, thread_config)` |
| 타임아웃 | `config.server.query_timeout` (기본 30초) |

**멀티턴 대화 처리**:
- `thread_id`가 없으면 새 UUID 발급 (첫 턴)
- `thread_id`가 있으면 체크포인트에서 이전 State 복원
  - `awaiting_approval=True`이면: 승인 응답으로 파싱하여 `approval_action` 설정
  - 그 외: `user_query`와 `messages`만 delta로 전달

### 2.2 `POST /api/v1/query/stream` — SSE 스트리밍 (`src/api/routes/query.py:299`)

State 구성은 `/query`와 동일. `graph.astream_events()`로 노드별 진행상황과 LLM 토큰을 실시간 스트리밍한다.

SSE 이벤트 타입:
- `node_start` — 노드 실행 시작
- `node_complete` — 노드 완료 + 진행 데이터
- `token` — LLM 출력 토큰
- `meta` — 실행된 SQL, 행 수
- `done` — 전체 완료
- `error` — 에러

### 2.3 `POST /api/v1/query/file` — 파일 업로드 질의 (`src/api/routes/query.py:534`)

| 항목 | 값 |
|------|----|
| 입력 | `query(Form)` + `file(UploadFile, .xlsx/.docx)` |
| 추가 처리 | xlsx → CSV 변환 (`excel_to_csv`) |
| State 생성 | `create_initial_state(user_query, uploaded_file, file_type, csv_sheet_data)` |
| 타임아웃 | `config.server.file_query_timeout` (기본 60초) |

`uploaded_file`과 `file_type`이 설정되면 `input_parser`가 양식 구조를 분석하고 `template_structure`를 생성한다.

---

## 3. 그래프 구조

### 3.1 노드 목록 (`src/graph.py:197` `build_graph()`)

| 노드명 | 파일 | LLM 사용 | 역할 |
|--------|------|----------|------|
| `context_resolver` | `src/nodes/context_resolver.py` | X | 멀티턴 대화 맥락 추출 |
| `input_parser` | `src/nodes/input_parser.py` | O | 자연어/양식 파싱 → `parsed_requirements` |
| `field_mapper` | `src/nodes/field_mapper.py` | O | 양식 필드 ↔ DB 컬럼 매핑 (양식 없으면 스킵) |
| `semantic_router` | `src/routing/semantic_router.py` | O | 대상 DB 결정 + 의도 분류 (조건부 등록) |
| `schema_analyzer` | `src/nodes/schema_analyzer.py` | O | DB 스키마 조회 + 관련 테이블 식별 |
| `query_generator` | `src/nodes/query_generator.py` | O | SQL SELECT 생성 |
| `query_validator` | `src/nodes/query_validator.py` | X | SQL 문법/안전성/성능 검증 (규칙 기반) |
| `approval_gate` | `src/nodes/approval_gate.py` | X | SQL 실행 전 사용자 승인 대기 (조건부 등록) |
| `query_executor` | `src/nodes/query_executor.py` | X | SQL 실행 + 결과 수집 |
| `result_organizer` | `src/nodes/result_organizer.py` | O | 결과 정리 + 마스킹 + 충분성 판단 |
| `output_generator` | `src/nodes/output_generator.py` | O | 최종 자연어 응답 또는 파일 생성 |
| `multi_db_executor` | `src/nodes/multi_db_executor.py` | O | 멀티 DB 병렬 실행 (조건부 등록) |
| `result_merger` | `src/nodes/result_merger.py` | X | 멀티 DB 결과 병합 (조건부 등록) |
| `cache_management` | `src/nodes/cache_management.py` | O | 캐시 생성/조회/삭제 (조건부 등록) |
| `synonym_registrar` | `src/nodes/synonym_registrar.py` | X | 유사어 Redis 등록 (조건부 등록) |
| `error_response` | `src/graph.py:125` | X | 재시도 초과 시 에러 응답 |

### 3.2 엣지 연결 (시멘틱 라우팅 ON 기준)

```
START
  │
  ▼
context_resolver ──→ input_parser ──→ field_mapper ──→ semantic_router
                                                           │
                                          ┌────────────────┼────────────────┬──────────────┐
                                          ▼                ▼                ▼              ▼
                                   schema_analyzer   multi_db_executor  cache_management  synonym_registrar
                                          │                │                │              │
                                          ▼                ▼                ▼              ▼
                                   query_generator   result_merger        END            END
                                          │                │
                                          ▼                ▼
                                   query_validator   result_organizer
                                      │  │  │              │
                              ┌───────┘  │  └──────┐       ▼
                              ▼          ▼         ▼   output_generator
                       query_generator  query_executor  error_response     │
                       (재시도)         │                  │               ▼
                                       ▼                  ▼              END
                                 result_organizer        END
                                    │     │
                                    ▼     ▼
                             output_generator  query_generator
                                    │          (재시도)
                                    ▼
                                   END
```

---

## 4. 공통 전처리 노드

모든 프롬프트는 반드시 다음 3개 노드를 순서대로 통과한다:

### 4.1 `context_resolver` (첫 번째 노드)

```
입력: messages (대화 히스토리)
출력: conversation_context (이전 SQL, 결과 요약, 테이블 등)
```

- **첫 턴**: `conversation_context = None`, 그대로 통과
- **후속 턴**: 이전 대화에서 `previous_sql`, `previous_tables`, `previous_db_id` 등을 추출
- 대화 히스토리가 MAX_HISTORY_TURNS(10)을 초과하면 트리밍

### 4.2 `input_parser` (두 번째 노드)

```
입력: user_query, uploaded_file, file_type, conversation_context
출력: parsed_requirements, template_structure (양식 업로드 시)
```

- **텍스트 질의**: LLM으로 자연어를 파싱하여 `query_targets`, `conditions`, `output_format` 등 추출
- **파일 업로드**: 양식 구조를 분석하여 `template_structure` (시트별 헤더, 필드명 목록) 생성
- **CSV 시트 데이터**: `csv_sheet_data`가 있으면 CSV 변환 결과를 함께 분석
- **멀티턴**: `conversation_context`가 있으면 이전 맥락을 프롬프트에 포함

**LLM 호출 위치**: `src/nodes/input_parser.py:156` (최대 2회 시도)

### 4.3 `field_mapper` (세 번째 노드)

```
입력: template_structure, parsed_requirements
출력: column_mapping, db_column_mapping, mapped_db_ids, pending_synonym_registrations
```

- **`template_structure`가 없으면 (텍스트 모드)**: 즉시 스킵, 빈 결과 반환
- **양식 업로드 시**: 3단계 매핑 수행
  1. **프롬프트 힌트 매핑**: `parsed_requirements.field_mapping_hints`로 직접 매핑
  2. **Redis synonym 매핑**: Redis 캐시의 유사 단어로 매핑
  3. **LLM 추론 매핑**: 나머지 미매핑 필드를 LLM으로 추론 (`src/document/field_mapper.py:503`)
- `mapped_db_ids`: 매핑에서 식별된 DB 목록 → `semantic_router`에서 LLM 라우팅 스킵에 활용

---

## 5. 시멘틱 라우팅 분기

> **전제**: `config.enable_semantic_routing = True` 일 때만 `semantic_router` 노드가 존재한다.
> `False`이면 `field_mapper → schema_analyzer`로 직행한다.

### 5.1 `semantic_router` 내부 우선순위 (`src/routing/semantic_router.py:34`)

`semantic_router`는 다음 우선순위로 라우팅을 결정한다:

```
[우선순위 1] pending_synonym_reuse 존재?
    → YES: routing_intent = "cache_management"

[우선순위 2] pending_synonym_registrations 존재?
    → YES: routing_intent = "synonym_registration"

[우선순위 3] mapped_db_ids 존재? (field_mapper가 이미 DB를 결정)
    → YES: routing_intent = "data_query", LLM 라우팅 스킵

[우선순위 4] 활성 DB가 없음?
    → YES: 레거시 단일 DB 모드

[우선순위 5] LLM 분류 실행
    → LLM이 프롬프트를 분석하여 intent + 대상 DB를 결정
```

### 5.2 LLM 분류 (`_llm_classify`)

프롬프트: `src/prompts/semantic_router.py`

LLM에게 사용자 질의와 활성 DB 목록을 주고 JSON으로 응답받는다:

```json
{
    "intent": "data_query" | "cache_management",
    "databases": [
        {
            "db_id": "polestar",
            "relevance_score": 0.95,
            "sub_query_context": "서버 CPU 사용률 조회",
            "user_specified": false
        }
    ]
}
```

**캐시 관리 의도로 분류되는 키워드** (프롬프트에 명시):
- "캐시 생성", "캐시 갱신", "캐시 삭제", "캐시 상태", "스키마 캐시"
- "유사 단어 생성", "유사 단어 추가", "유사 단어 삭제"
- "컬럼 설명 생성", "컬럼 설명 변경"
- "DB 설명 생성", "DB 설명 설정"
- "어떤 DB가 있어?", "DB 목록 보여줘"
- "재활용", "새로 생성", "병합" (이전 턴의 재활용 제안에 대한 응답)

### 5.3 `route_after_semantic_router` 분기 (`src/graph.py:107`)

```python
routing_intent == "cache_management"    → cache_management 노드
routing_intent == "synonym_registration" → synonym_registrar 노드
is_multi_db == True                     → multi_db_executor 노드
그 외                                   → schema_analyzer 노드 (단일 DB 쿼리)
```

---

## 6. 프롬프트별 실행 경로

### 6.1 일반 데이터 조회 (단일 DB)

**프롬프트 예시**: `"서버 CPU 사용률이 80% 이상인 목록을 보여줘"`

```
context_resolver ──→ input_parser ──→ field_mapper (스킵)
    │                     │                │
    │              LLM: 질의 파싱      template 없음
    │              parsed_requirements  → 즉시 반환
    │
    ──→ semantic_router ──→ schema_analyzer ──→ query_generator
              │                   │                   │
        LLM: DB 분류        DB 스키마 조회       LLM: SQL 생성
        intent=data_query   relevant_tables      generated_sql
        active_db_id=polestar

    ──→ query_validator ──→ query_executor ──→ result_organizer ──→ output_generator ──→ END
              │                   │                   │                    │
        규칙 기반 검증        SQL 실행           결과 정리            LLM: 응답 생성
        passed=True         query_results       organized_data       final_response
```

**LLM 호출 횟수**: 최소 4회 (input_parser, semantic_router, query_generator, output_generator)
+ schema_analyzer에서 LLM 테이블 선택 시 +1회

### 6.2 멀티 DB 조회

**프롬프트 예시**: `"서버 사양과 해당 서버의 VM 정보를 알려줘"`

```
context_resolver ──→ input_parser ──→ field_mapper (스킵)
    ──→ semantic_router ──→ multi_db_executor ──→ result_merger ──→ result_organizer ──→ output_generator ──→ END
              │                      │                  │
        LLM: 2개 DB 분류      DB별 독립 파이프라인    결과 병합
        polestar + cloud_portal  (스키마→SQL→검증→실행)
        is_multi_db=True         db_results에 저장
```

`multi_db_executor` 내부에서 각 DB별로 스키마 분석 → SQL 생성 → 검증 → 실행을 독립 수행한다. 부분 실패 시 성공한 결과와 에러를 모두 반환한다.

### 6.3 캐시 관리

**프롬프트 예시**: `"스키마 캐시를 생성해줘"`

```
context_resolver ──→ input_parser ──→ field_mapper (스킵)
    ──→ semantic_router ──→ cache_management ──→ END
              │                      │
        LLM: intent 분류        LLM: action 파싱
        intent=cache_management  action=generate
                                 → _handle_generate()
                                 → DB 연결 → 스키마 수집 → Redis 저장
```

**cache_management 노드의 action 종류** (`src/nodes/cache_management.py:166`):

| action | 프롬프트 예시 | 동작 |
|--------|-------------|------|
| `generate` | "캐시를 생성해줘" | DB 스키마 수집 → Redis 캐시 저장 |
| `generate-descriptions` | "컬럼 설명을 생성해줘" | LLM으로 컬럼 설명 + 유사 단어 생성 |
| `generate-synonyms` | "polestar의 유사 단어를 생성해줘" | 특정 DB의 유사 단어 생성 |
| `generate-global-synonyms` | "hostname의 유사 단어를 생성해줘" | 글로벌 유사 단어 LLM 생성 |
| `generate-db-description` | "DB 설명을 생성해줘" | LLM으로 DB 설명 자동 생성 |
| `set-db-description` | "polestar DB 설명을 '모니터링 DB'로 설정해" | DB 설명 수동 설정 |
| `status` | "캐시 상태를 보여줘" | 캐시 상태 조회 |
| `invalidate` | "캐시를 삭제해줘" | 캐시 삭제 (유사 단어 보존) |
| `db-guide` | "어떤 DB가 있어?" | DB 목록 + 설명 안내 |
| `list-synonyms` | "hostname의 유사 단어를 보여줘" | 유사 단어 목록 조회 |
| `add-synonym` | "hostname에 '서버호스트' 추가해줘" | 유사 단어 추가 |
| `remove-synonym` | "hostname에서 '호스트네임' 삭제해줘" | 유사 단어 삭제 |
| `update-synonym` | "유사 단어를 '서버명, 호스트'로 변경해줘" | 유사 단어 교체 |
| `update-description` | "hostname 설명을 '서버 호스트명'으로 변경해줘" | 글로벌 컬럼 설명 수정 |
| `reuse-synonym` | "재활용" / "새로 생성" / "병합" | 유사 필드 재활용 응답 처리 |

**캐시 초기 구축 순서**:
```
1. "스키마 캐시를 생성해줘"    → action: generate (스키마 수집 + Redis 저장)
2. "컬럼 설명을 생성해줘"      → action: generate-descriptions (descriptions + synonyms)
3. "DB 설명을 생성해줘"        → action: generate-db-description
```

### 6.4 파일 업로드 + 데이터 조회

**프롬프트 예시**: `"이 양식에 서버 목록을 채워줘"` + `서버목록.xlsx`

```
context_resolver ──→ input_parser ──→ field_mapper ──→ semantic_router
                          │                │                │
                    LLM: 양식 구조 분석  3단계 매핑 수행    mapped_db_ids 사용
                    template_structure   column_mapping     LLM 라우팅 스킵
                    생성                 mapped_db_ids

    ──→ schema_analyzer ──→ query_generator ──→ ... ──→ output_generator ──→ END
                                                              │
                                                        양식 파일 생성
                                                        output_file (bytes)
                                                        output_file_name
```

**field_mapper 3단계 매핑** (`src/document/field_mapper.py`):
1. **프롬프트 힌트**: `parsed_requirements.field_mapping_hints` (사용자가 명시한 매핑)
2. **Redis synonym**: 캐시된 유사 단어로 자동 매핑 (예: "서버명" → `servers.hostname`)
3. **LLM 추론**: 나머지 미매핑 필드를 descriptions 기반으로 LLM이 매핑

> **주의**: 2, 3단계는 Redis 캐시에 descriptions/synonyms가 있어야 동작한다.
> 없으면 `"DB descriptions가 없어 LLM 매핑을 수행할 수 없습니다."` 경고 발생.

### 6.5 유사어 등록 (멀티턴)

파일 업로드 후 `field_mapper`가 LLM으로 추론한 매핑이 있으면 `pending_synonym_registrations`에 등록 대기 항목을 생성한다.

**1턴째 응답**: `"다음 유사어를 등록하시겠습니까? 1. 서버명 → hostname ..."`

**2턴째 프롬프트**: `"전체 등록"` 또는 `"1, 3 등록"` 또는 `"건너뛰기"`

```
context_resolver ──→ input_parser ──→ field_mapper
    ──→ semantic_router ──→ synonym_registrar ──→ END
              │                      │
    pending_synonym_registrations    사용자 입력 파싱
    존재 → synonym_registration      → Redis에 유사어 등록
```

### 6.6 SQL 승인 (Human-in-the-loop, `enable_sql_approval=True`)

**1턴째 프롬프트**: `"서버 목록을 보여줘"`

```
... → query_validator ──→ approval_gate ──→ [INTERRUPT]
                                │
                          awaiting_approval=True
                          approval_context에 SQL 정보 저장
                          → 클라이언트에 승인 요청 응답
```

**2턴째 프롬프트**: `"실행"` / `"취소"` / `"SELECT ... (수정된 SQL)"`

```
graph.ainvoke({approval_action: "approve"}, thread_config)
    → approval_gate 재개
        → "approve": query_executor로 진행
        → "reject": END
        → "modify": query_validator로 재검증
```

### 6.7 레거시 모드 (`enable_semantic_routing=False`)

```
context_resolver ──→ input_parser ──→ field_mapper ──→ schema_analyzer ──→ ...
                                                  │
                                           semantic_router 없음
                                           직접 schema_analyzer로
```

`semantic_router`, `multi_db_executor`, `result_merger`, `cache_management`, `synonym_registrar` 노드가 그래프에 등록되지 않는다.
**캐시 관리 프롬프트를 입력해도 데이터 조회 파이프라인으로 흘러간다.**

---

## 7. 재시도 및 에러 처리

그래프에는 3개의 재시도 루프가 있다 (모두 최대 3회):

### 7.1 `query_validator` → `query_generator` 루프

```python
# src/graph.py:39 route_after_validation
validation_result.passed == True  → query_executor
validation_result.passed == False AND retry_count < 3  → query_generator (재시도)
validation_result.passed == False AND retry_count >= 3 → error_response
```

검증 실패 사유(`error_message`)가 `query_generator`에 전달되어 수정된 SQL을 생성한다.

### 7.2 `query_executor` → `query_generator` 루프

```python
# src/graph.py:80 route_after_execution
error_message 없음  → result_organizer
error_message 있음 AND retry_count < 3  → query_generator (재시도)
error_message 있음 AND retry_count >= 3 → error_response
```

SQL 실행 에러(문법 오류, 테이블 미존재 등)를 에러 컨텍스트로 전달하여 SQL을 재생성한다.

### 7.3 `result_organizer` → `query_generator` 루프

```python
# src/graph.py:94 route_after_organization
organized_data.is_sufficient == True  → output_generator
organized_data.is_sufficient == False AND retry_count < 3  → query_generator (재시도)
organized_data.is_sufficient == False AND retry_count >= 3 → output_generator (있는 데이터로 출력)
```

결과가 부족하면 SQL을 재생성한다. 3회 초과 시에도 `error_response`가 아닌 `output_generator`로 가서 있는 데이터로 응답을 생성한다.

---

## 8. 설정 플래그에 따른 그래프 변형

| 설정 | 기본값 | 활성화 시 추가되는 노드/엣지 |
|------|--------|----------------------------|
| `enable_semantic_routing` | `False` (.env) | `semantic_router`, `multi_db_executor`, `result_merger`, `cache_management`, `synonym_registrar` + 조건부 엣지 |
| `enable_sql_approval` | `False` | `approval_gate` + `interrupt_before` 설정 |

### `enable_semantic_routing` 자동 활성화 조건 (`src/config.py:230`):

```python
# .env에서 명시적으로 설정하지 않아도:
if multi_db.get_active_db_ids():  # 멀티 DB 연결이 하나라도 있으면
    enable_semantic_routing = True  # 자동 활성화
```

---

## 부록: State 주요 필드와 담당 노드

| State 필드 | 쓰기 담당 노드 | 설명 |
|-----------|---------------|------|
| `conversation_context` | context_resolver | 이전 대화 맥락 |
| `parsed_requirements` | input_parser | 파싱된 요구사항 |
| `template_structure` | input_parser | 양식 구조 (파일 업로드 시) |
| `column_mapping` | field_mapper | 필드 ↔ DB 컬럼 매핑 |
| `mapped_db_ids` | field_mapper | 매핑에서 식별된 DB 목록 |
| `pending_synonym_registrations` | field_mapper | 유사어 등록 대기 항목 |
| `routing_intent` | semantic_router | 라우팅 의도 |
| `target_databases` | semantic_router | 대상 DB 목록 |
| `is_multi_db` | semantic_router | 멀티 DB 여부 |
| `active_db_id` | semantic_router | 현재 DB 식별자 |
| `relevant_tables` | schema_analyzer | 관련 테이블 목록 |
| `schema_info` | schema_analyzer | DB 스키마 상세 |
| `column_descriptions` | schema_analyzer | 컬럼 설명 |
| `column_synonyms` | schema_analyzer | 유사 단어 |
| `generated_sql` | query_generator | 생성된 SQL |
| `validation_result` | query_validator | SQL 검증 결과 |
| `query_results` | query_executor | 쿼리 실행 결과 |
| `query_attempts` | query_executor | SQL 시도 이력 |
| `db_results` | multi_db_executor | DB별 쿼리 결과 |
| `db_errors` | multi_db_executor | DB별 에러 |
| `organized_data` | result_organizer | 정리된 결과 데이터 |
| `final_response` | output_generator / cache_management / error_response | 최종 응답 |
| `output_file` | output_generator | 생성된 파일 바이너리 |
| `awaiting_approval` | approval_gate | 승인 대기 여부 |
| `approval_action` | API 계층 (delta input) | 사용자 승인 응답 |

---

## 부록: LLM 호출 위치 전체 목록

| 파일:라인 | 노드 | 용도 |
|----------|------|------|
| `src/nodes/input_parser.py:156` | input_parser | 질의 파싱 (최대 2회) |
| `src/nodes/input_parser.py:255` | input_parser | CSV 컨텍스트 질의 파싱 (최대 2회) |
| `src/nodes/schema_analyzer.py:425` | schema_analyzer | LLM 기반 테이블 선택 |
| `src/nodes/query_generator.py:87` | query_generator | SQL 생성 |
| `src/nodes/output_generator.py:143` | output_generator | 자연어 응답 생성 |
| `src/routing/semantic_router.py:262` | semantic_router | DB 라우팅 분류 |
| `src/nodes/multi_db_executor.py:328` | multi_db_executor | 멀티 DB SQL 생성 |
| `src/nodes/cache_management.py:144` | cache_management | 캐시 의도 파싱 |
| `src/document/field_mapper.py:503` | field_mapper | 필드 매핑 LLM 추론 |
| `src/document/field_mapper.py:575` | field_mapper | 매핑 보정 |
| `src/schema_cache/description_generator.py:84` | (보조) | DB 설명 생성 |
| `src/schema_cache/description_generator.py:154` | (보조) | 스키마 설명 생성 |
| `src/schema_cache/cache_manager.py:552` | (보조) | 캐시 관련 LLM 판단 |
| `src/schema_cache/cache_manager.py:655` | (보조) | 캐시 관련 LLM 판단 |
