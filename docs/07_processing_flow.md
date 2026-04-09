# 03. 처리 프로세스 (Processing Flow)

> 최종 갱신: 2026-04-02

---

## 1. 메인 그래프 흐름도

사용자 요청이 들어오면 LangGraph 상태 머신이 다음 순서로 처리한다.

```
                              ┌──────────────────┐
                              │      START       │
                              └────────┬─────────┘
                                       │
                              ┌────────▼─────────┐
                              │ context_resolver  │ ← 멀티턴 컨텍스트 추출
                              │ (이전 대화 참조)    │   (이전 SQL/테이블/결과)
                              └────────┬─────────┘
                                       │
                              ┌────────▼─────────┐
                              │  input_parser     │ ← LLM: 자연어 → 구조화
                              │ (요구사항 파싱)     │   (Excel/Word 업로드 처리)
                              └────────┬─────────┘
                                       │
                              ┌────────▼─────────┐
                              │  field_mapper     │ ← 3계층 매핑:
                              │ (필드-컬럼 매핑)    │   hint → synonym → LLM
                              └────────┬─────────┘
                                       │
                         ┌─────────────▼─────────────┐
                         │     semantic_router        │ ← LLM: 대상 DB 결정
                         │  (시멘틱 라우팅/의도 분류)    │   (캐시관리/유사어/쿼리)
                         └────┬──────┬──────┬────┬───┘
                              │      │      │    │
              ┌───────────────┘      │      │    └──────────────────┐
              ▼                      ▼      ▼                      ▼
    ┌─────────────────┐   ┌──────────┐  ┌──────────────┐  ┌──────────────┐
    │cache_management │   │synonym   │  │multi_db      │  │schema        │
    │(캐시 관리)       │   │registrar │  │executor      │  │analyzer      │
    └────────┬────────┘   └────┬─────┘  │(멀티DB 실행)  │  │(스키마 분석)  │
             │                 │        └──────┬───────┘  └──────┬───────┘
             ▼                 ▼               │                 │
            END               END              │    ┌────────────▼────────┐
                                               │    │ [structure_approval │
                                               │    │  _gate] (HITL)     │
                                               │    └────────────┬───────┘
                                               │                 │
                                               │    ┌────────────▼───────┐
                                               │    │ query_generator    │◄──────────┐
                                               │    │ (LLM: SQL 생성)    │           │
                                               │    └────────────┬───────┘           │
                                               │                 │                   │
                                               │    ┌────────────▼───────┐           │
                                               │    │ query_validator    │           │
                                               │    │ (규칙 기반 검증)    │           │
                                               │    └──┬──────┬─────┬───┘           │
                                               │       │      │     │               │
                                               │  통과  │ 실패  │ 초과  │               │
                                               │       │ (≤3)  │     │               │
                                               │       │      │     ▼               │
                                               │       │      │  error_response     │
                                               │       │      │     │               │
                                               │       │      └─────┼───► query     │
                                               │       │            │    generator  │
                                               │       ▼            │    (재시도)    │
                                               │  ┌──────────┐     │               │
                                               │  │[approval │     │               │
                                               │  │ _gate]   │     │               │
                                               │  │(SQL 승인) │     │               │
                                               │  └──┬───────┘     │               │
                                               │     │             │               │
                                               │  ┌──▼──────────┐  │               │
                                               │  │query        │  │               │
                                               │  │executor     │  │               │
                                               │  │(SQL 실행)    │  │               │
                                               │  └──┬──────┬───┘  │               │
                                               │     │      │      │               │
                                               │  정상 │  에러 │      │               │
                                               │     │  (≤3) │      │               │
                                               │     │      └──────┼───► query     │
                                               │     │             │    generator  │
                                               │     ▼             │    (재시도)    │
                                               │  ┌──────────────┐ │               │
                                               │  │result        │ │               │
                                        ┌──────┤  │organizer     │ │               │
                                        │      │  │(결과 정리)    │ │               │
                                        │      │  └──┬───────────┘ │               │
                                        │      │     │             │               │
                                        │      │  충분 │ 부족(≤3)    │               │
                                        │      │     │      └──────┘               │
                                        │      │     ▼                             │
                                        │      │  ┌──────────────┐                 │
                                        │      │  │output        │                 │
                                        │      │  │generator     │                 │
                                        │      │  │(응답/문서생성) │                 │
                                        │      │  └──────┬───────┘                 │
                                        │      │         │                         │
                                        │      │         ▼                         │
                                        │      │        END                        │
                                        │      │                                   │
                                        │      │  ┌──────────────┐                 │
                                        └──────┼─►│result_merger │                 │
                                               │  │(결과 병합)    │                 │
                                               │  └──────┬───────┘                 │
                                               │         │                         │
                                               │         └──► result_organizer ────┘
                                               └──────────────────────────────────────
```

---

## 2. 노드별 상세 처리

### 2.1 context_resolver (멀티턴 컨텍스트 해석)

```
입력: thread_id, messages (LangGraph checkpoint)
처리:
  1. checkpoint에서 이전 대화 히스토리 로드
  2. 이전 SQL, 결과, 접근 테이블 추출
  3. 대기 중인 유사어 등록/승인 상태 확인
  4. 메시지 히스토리 트리밍 (최대 10턴)
출력: conversation_context = {previous_sql, previous_tables, previous_results, ...}
```

### 2.2 input_parser (자연어 입력 파싱)

```
입력: user_query, uploaded_file, csv_sheet_data
처리:
  1. Excel/Word 파일 업로드 시 → 양식 구조 파싱
  2. Excel CSV 데이터를 LLM 컨텍스트에 추가
  3. LLM 호출: 자연어 → 구조화된 요구사항 JSON
     - 조회 대상, 조건, 기간, 정렬, 집계 등
     - field_mapping_hints (필드→컬럼 힌트)
     - target_db_hints (대상 DB 힌트)
     - synonym_registration (유사어 등록 의도)
출력: parsed_requirements, template_structure, target_sheets
```

### 2.3 field_mapper (3계층 필드 매핑)

```
입력: parsed_requirements, csv_sheet_data
처리: 3단계 매핑 수행

  ┌────────────────────────────────────────────────┐
  │ Layer 1: Prompt Hints (규칙 기반)               │
  │   input_parser가 추출한 field_mapping_hints     │
  │   확실한 매핑만 적용                             │
  ├────────────────────────────────────────────────┤
  │ Layer 2: Redis Synonyms (사전 기반)             │
  │   Redis에 저장된 유사어 사전 조회                 │
  │   "서버명" → hostname 등                        │
  ├────────────────────────────────────────────────┤
  │ Layer 3: LLM Inference (AI 추론)               │
  │   매핑되지 않은 필드에 대해 LLM 호출              │
  │   컬럼 설명/유사어 기반 유사도 매칭               │
  │   → pending_synonym_registrations 생성          │
  └────────────────────────────────────────────────┘

출력: column_mapping, db_column_mapping, mapping_sources, mapped_db_ids
```

### 2.4 semantic_router (시멘틱 라우팅)

```
입력: user_query, parsed_requirements, mapped_db_ids
처리:
  우선순위 판단:
    1. pending_synonym_reuse → cache_management 강제 라우팅
    2. synonym_registration 요청 → synonym_registrar 라우팅
    3. field_mapper 매핑 결과 → 매핑된 DB 사용 (LLM 스킵)
    4. LLM 기반 분류:
       - 활성 도메인 목록 + Redis DB 설명 → 프롬프트 구성
       - LLM이 관련 DB와 관련도 점수(0~1) 반환
       - 최소 관련도 0.3 이상만 사용
       - intent 분류: data_query | cache_management

출력: target_databases, is_multi_db, active_db_id, routing_intent, user_specified_db
```

### 2.5 schema_analyzer (스키마 분석)

```
입력: user_query, active_db_id
처리:
  ┌──────────────────────────────────────────────┐
  │ 4계층 캐시 조회                               │
  │                                              │
  │  1차: 메모리 캐시 (TTL 5분)                    │
  │       ↓ miss                                 │
  │  2차: Redis 캐시 (fingerprint 기반)           │
  │       ↓ miss/장애                             │
  │  2차-fb: 파일 캐시 (JSON 파일)                 │
  │       ↓ miss                                 │
  │  3차: DB 전체 조회 (MCP/Direct)               │
  │       → 결과를 모든 캐시에 저장                 │
  └──────────────────────────────────────────────┘

  스키마 획득 후:
  1. LLM으로 관련 테이블 선택
  2. 구조 분석 (EAV, 계층구조, JOIN 패턴 감지)
  3. YAML 프로파일 확인 (config/db_profiles/ 수동 설정 우선)
  4. HITL 구조 승인 대기 (enable_structure_approval=true 시)

출력: relevant_tables, schema_info, column_descriptions, column_synonyms
```

### 2.6 query_generator (SQL 생성)

```
입력: parsed_requirements, schema_info, column_mapping, conversation_context
처리:
  1. 시스템 프롬프트 구성:
     - 범용 프롬프트 또는 Polestar 전용 프롬프트 (polestar_db_id 매칭 시)
     - 스키마 정보 (테이블, 컬럼, FK, EAV 패턴)
     - 구조 가이드 (금지 JOIN 컬럼, value-based join 규칙)
     - 컬럼 매핑 정보 (column_mapping)
  2. 사용자 프롬프트 구성:
     - 자연어 질의
     - 이전 에러 메시지 (재시도 시, error_message)
     - 멀티턴 컨텍스트 (이전 SQL/결과)
  3. LLM 호출 → SELECT SQL 생성

출력: generated_sql
```

### 2.7 query_validator (SQL 검증)

```
입력: generated_sql, schema_info
처리 (규칙 기반, LLM 미사용):
  ┌───────────────────────────────────────────┐
  │ 1. 구문 파싱 (sqlparse)                    │
  │ 2. SELECT-only 강제                       │
  │    (INSERT/UPDATE/DELETE/DROP/ALTER/       │
  │     TRUNCATE/CREATE 차단)                 │
  │ 3. SQL 주입 패턴 감지                      │
  │ 4. 테이블/컬럼 존재 확인                    │
  │ 5. 금지 JOIN 컬럼 검사                     │
  │ 6. EAV 금지 JOIN 패턴 검사                 │
  │ 7. LIMIT 절 자동 추가 (없으면 default_limit)│
  │ 8. 성능 위험 경고                          │
  └───────────────────────────────────────────┘
  실패 시: reason과 함께 → query_generator 재시도 (최대 3회)
  auto_fixed_sql: LIMIT 추가 등 자동 수정된 SQL

출력: validation_result = {passed, reason, auto_fixed_sql}
```

### 2.8 query_executor (SQL 실행)

```
입력: generated_sql (또는 auto_fixed_sql)
처리:
  1. DBClient 선택 (MCP DBHub 또는 Direct asyncpg)
  2. SQL 실행 (타임아웃 30초)
  3. QueryAttempt 기록:
     {sql, success, error, row_count, execution_time_ms}
  4. 감사 로그 기록 (접근 테이블, 행 수)
  에러 시: error_message 설정 → query_generator 재시도

출력: query_results, query_attempts, accessed_tables
```

### 2.9 result_organizer (결과 정리)

```
입력: query_results, column_mapping
처리:
  1. 민감 데이터 마스킹 (password, token, api_key 등)
  2. 숫자 포맷팅
  3. LLM 기반 데이터 충분성 검사:
     - column_mapping 대비 실제 데이터 커버리지 확인
     - hint/synonym 매핑: 0.7 이상 필요
     - llm_inferred 매핑: 0.5 이상 필요
     - 부족 시 → query_generator 재시도
  4. 필드 매핑 적용:
     - resolved_mapping (3계층: 규칙+LLM+폴백)
     - 시트별 매핑 (멀티시트 Excel)
  5. 요약 생성 (LLM)

출력: organized_data = {summary, rows, column_mapping, resolved_mapping,
                        is_sufficient, sheet_mappings}
```

### 2.10 output_generator (출력 생성)

```
입력: organized_data, uploaded_file, file_type
처리:
  ┌────────────────────────────────────────────────┐
  │ Case 1: 텍스트 응답 (file_type=None)            │
  │   LLM 호출: 쿼리 결과 → 한국어 자연어 응답        │
  │   마크다운 테이블 포함                            │
  ├────────────────────────────────────────────────┤
  │ Case 2: Excel 출력 (file_type="xlsx")           │
  │   1. 양식 파싱 (헤더 행, 데이터 영역 감지)         │
  │   2. resolved_mapping으로 필드-컬럼 매핑          │
  │   3. 데이터 행 채움 (서식/병합셀/수식 보존)        │
  │   4. 바이너리 반환                              │
  ├────────────────────────────────────────────────┤
  │ Case 3: Word 출력 (file_type="docx")            │
  │   1. {{placeholder}} 패턴 감지                   │
  │   2. 테이블 구조 감지                            │
  │   3. 데이터 채움 (스타일 보존)                    │
  │   4. 바이너리 반환                              │
  └────────────────────────────────────────────────┘
  + LLM 추론 매핑 시 유사어 등록 안내 메시지 추가

출력: final_response, output_file, output_file_name
```

---

## 3. 재시도 및 에러 복구 흐름

시스템은 3가지 지점에서 `query_generator`로 루프백하여 재시도한다. 모든 재시도는 `retry_count`로 추적되며, 최대 3회까지 시도한다.

```
┌──────────────────────────────────────────────────────────────────┐
│                   재시도 루프 (최대 3회, retry_count)               │
│                                                                  │
│  ① 검증 실패 (query_validator → query_generator)                  │
│     - validation_result.passed = false                           │
│     - reason(실패 사유)을 query_generator에 전달                   │
│     - LLM이 이전 에러를 참조하여 SQL 수정                          │
│                                                                  │
│  ② 실행 에러 (query_executor → query_generator)                   │
│     - error_message(DB 에러 메시지)를 query_generator에 전달       │
│     - 테이블/컬럼 없음, 문법 에러, 타임아웃 등                      │
│                                                                  │
│  ③ 데이터 부족 (result_organizer → query_generator)               │
│     - organized_data.is_sufficient = false                       │
│     - 부족한 필드 정보를 query_generator에 전달                    │
│     - LLM이 추가 컬럼/JOIN을 포함하여 SQL 재생성                   │
│                                                                  │
│  retry_count ≥ 3 → error_response → END                          │
│  (③의 경우 부족해도 있는 데이터로 output_generator 진행)             │
└──────────────────────────────────────────────────────────────────┘
```

```
query_generator ──► query_validator ──► query_executor ──► result_organizer
       ▲                   │                  │                   │
       │              검증 실패            실행 에러           데이터 부족
       │              (reason)         (error_message)    (is_sufficient=false)
       └───────────────────┴──────────────────┴───────────────────┘
                                 (retry_count < 3)
```

---

## 4. 멀티 DB 처리 흐름

`semantic_router`에서 `is_multi_db=true`로 판정되면, `multi_db_executor`가 각 DB별로 독립적인 쿼리 파이프라인을 실행한다.

```
semantic_router (is_multi_db=true)
    │
    ▼
┌──────────────────────────────────────────────────────────────┐
│                    multi_db_executor                          │
│                                                              │
│  for each target_db in target_databases:                     │
│    ┌──────────────────────────────────────────────────────┐  │
│    │ 1. schema_analyzer (해당 DB 스키마 조회/캐시)          │  │
│    │ 2. query_generator (해당 DB용 SQL 생성)              │  │
│    │ 3. query_validator (SQL 검증)                        │  │
│    │ 4. query_executor  (SQL 실행)                        │  │
│    │ 5. column_mapping 기반 결과 컬럼 필터링               │  │
│    └──────────────────────────────────────────────────────┘  │
│                                                              │
│  결과: db_results = {db_id: rows, ...}                       │
│        db_errors  = {db_id: error, ...}                      │
│        db_schemas = {db_id: schema_info, ...}                │
└───────────────────────┬──────────────────────────────────────┘
                        │
                        ▼
                 result_merger
                    │
                    │ DB별 결과 병합 → query_results
                    │ 에러 요약 생성
                    ▼
              result_organizer → output_generator → END
```

---

## 5. SSE 스트리밍 프로세스

클라이언트는 `POST /api/v1/query/stream`을 호출하고, 서버는 각 노드의 시작/완료를 실시간으로 SSE 이벤트로 전송한다.

```
Client                                    Server
  │                                         │
  │  POST /api/v1/query/stream              │
  │  {query, thread_id, file}               │
  │ ─────────────────────────────────────►  │
  │                                         │
  │  ◄─── event: node_start                 │  ← context_resolver 시작
  │       data: {node: "context_resolver"}  │
  │                                         │
  │  ◄─── event: node_complete              │  ← context_resolver 완료
  │       data: {node: "context_resolver"}  │
  │                                         │
  │  ◄─── event: node_start                 │  ← input_parser 시작
  │  ◄─── event: node_complete              │
  │                                         │
  │  ◄─── event: node_start                 │  ← field_mapper
  │  ◄─── event: node_complete              │
  │                                         │
  │  ... (각 노드별 start/complete)          │
  │                                         │
  │  ◄─── event: approval_required          │  ← (HITL 활성화 시)
  │       data: {sql, context}              │     interrupt_before에서 중단
  │                                         │
  │  POST /api/v1/query/stream              │  ← 승인/거절/수정 응답
  │  {thread_id, approval_action,           │     (같은 thread_id로 resume)
  │   modified_sql?}                        │
  │ ─────────────────────────────────────►  │
  │                                         │
  │  ◄─── event: node_start                 │  ← 승인 후 처리 계속
  │  ...                                    │
  │                                         │
  │  ◄─── event: result                     │  ← 최종 결과
  │       data: {response, has_file,        │
  │              file_url, mapping_report}  │
  │                                         │
  │  ◄─── event: synonym_suggestion         │  ← (LLM 추론 매핑 시)
  │       data: {pending_registrations}     │     유사어 등록 안내
  │                                         │
  │  ◄─── event: done                       │  ← 스트림 종료
  │                                         │
```

---

## 6. End-to-End 시나리오

### 6.1 일반 자연어 질의

```
사용자: "Polestar DB에서 CPU 사용률이 80% 이상인 서버 목록을 보여줘"

 1. [API] POST /api/v1/query/stream
    ├── AuditMiddleware: request_id 생성, client_ip 추출
    └── JWT 인증 (AUTH_ENABLED 시)

 2. [context_resolver] 이전 대화 없음 → 스킵

 3. [input_parser] LLM 호출
    └── parsed_requirements = {
          "target": "서버 목록",
          "conditions": ["CPU 사용률 > 80%"],
          "target_db_hints": ["polestar"]
        }

 4. [field_mapper] 매핑 불필요 (파일 업로드 없음)

 5. [semantic_router] LLM 분류
    └── target_databases = [{db_id: "polestar", relevance: 0.95}]
        is_multi_db = false, active_db_id = "polestar"

 6. [schema_analyzer]
    ├── Redis 캐시에서 polestar 스키마 로드
    ├── LLM: 관련 테이블 선택 → [CMM_RESOURCE, CORE_CONFIG_PROP]
    └── EAV 구조 감지 → value-based join 가이드 생성

 7. [query_generator] LLM: SQL 생성
    └── SELECT r.NAME as hostname, p.VALUE as cpu_usage
        FROM CMM_RESOURCE r
        JOIN CORE_CONFIG_PROP p ON r.ID = p.RESOURCE_ID
        WHERE p.PROP_KEY = 'cpu_usage_avg'
          AND CAST(p.VALUE AS DECIMAL) > 80
        LIMIT 1000

 8. [query_validator] 규칙 기반 검증
    ├── SELECT-only ✓
    ├── 테이블 존재 ✓
    ├── 금지 JOIN 없음 ✓
    └── LIMIT 있음 ✓ → passed=true

 9. [query_executor] MCP 서버로 SQL 실행
    └── query_results = [{hostname: "srv001", cpu_usage: "85.2"}, ...]

10. [result_organizer]
    ├── 민감 데이터 검사 → 해당 없음
    ├── 데이터 충분성 ✓
    └── 요약: "CPU 사용률 80% 이상 서버 15건"

11. [output_generator] LLM: 자연어 응답 생성
    └── "CPU 사용률이 80%를 초과하는 서버 15건을 조회했습니다.
         | 서버명 | CPU 사용률 |
         |--------|-----------|
         | srv001 | 85.2%     | ..."

12. [API] SSE 이벤트로 클라이언트에 전송
    ├── event: result {response, ...}
    └── event: done
```

### 6.2 Excel 양식 업로드 질의

```
사용자: Excel 양식 업로드 + "이 양식에 서버 정보를 채워줘"

 1. [API] POST /api/v1/query/file
    └── Excel 바이너리 수신, CSV 변환 (excel_csv_converter)

 2. [input_parser]
    ├── Excel 파싱: 시트 목록, 헤더 행, 데이터 영역 감지
    ├── CSV 데이터를 LLM 컨텍스트에 추가
    └── template_structure = {
          sheets: [{name: "Sheet1", headers: ["서버명", "IP", "CPU", ...]}]
        }

 3. [field_mapper] 3계층 매핑
    ├── Layer 1: hints (input_parser 추출) → "서버명" → hostname
    ├── Layer 2: Redis synonyms 조회 → "IP" → ip_address
    ├── Layer 3: LLM 추론 → "CPU코어수" ↔ core_count (유사도 매칭)
    └── column_mapping = {
          "서버명": "CMM_RESOURCE.NAME",
          "IP": "CORE_CONFIG_PROP.VALUE",
          "CPU코어수": "CORE_CONFIG_PROP.VALUE"
        }
        mapping_sources = {"서버명": "hint", "IP": "synonym", "CPU코어수": "llm_inferred"}

 4. [semantic_router] mapped_db_ids=[polestar] → LLM 스킵

 5~9. (일반 질의와 동일한 파이프라인)

10. [output_generator]
    ├── Excel 양식 로드 (원본 보존)
    ├── resolved_mapping으로 필드-컬럼 매핑
    ├── 데이터 행 채움 (서식/병합셀/수식 보존)
    ├── output_file = (Excel 바이너리)
    └── LLM 추론 매핑 안내:
        "다음 매핑은 AI가 추론했습니다. 유사어로 등록하시겠습니까?
         - CPU코어수 → core_count"

11. [API]
    ├── event: result {response, has_file: true, file_url: "/api/v1/query/{id}/download"}
    └── GET /api/v1/query/{id}/download → Excel 파일 다운로드
```

### 6.3 멀티턴 대화 + HITL

```
[Turn 1] "서버 목록을 보여줘"
  → 일반 처리 → 결과 반환
  → LangGraph checkpoint 저장 (thread_id="abc-123")

[Turn 2] "그 중에서 메모리가 16GB 이상인 것만"
  → context_resolver:
    - checkpoint에서 이전 상태 복원
    - conversation_context = {
        previous_sql: "SELECT ... FROM CMM_RESOURCE ...",
        previous_tables: ["CMM_RESOURCE", "CORE_CONFIG_PROP"],
        previous_results: [{hostname: "srv001", ...}]
      }
  → input_parser: "메모리 >= 16GB" 조건 추가 인식
  → query_generator: 이전 SQL을 참조하여 WHERE 조건 추가
  → 결과 반환

[Turn 3] (SQL 승인 활성화 시, enable_sql_approval=true)
  "디스크 사용량도 같이 보여줘"
  → query_validator 통과 후 approval_gate에서 interrupt
  → SSE: event: approval_required
    data: {
      sql: "SELECT ... JOIN ... WHERE ...",
      tables: ["CMM_RESOURCE", "CORE_CONFIG_PROP"],
      type: "sql_approval"
    }

  사용자 응답 3가지:
  ┌─────────────────────────────────────────────────────────┐
  │ "approve" → query_executor로 진행                       │
  │ "reject"  → END (응답 없이 종료)                        │
  │ "modify"  + modified_sql → query_validator로 재검증     │
  └─────────────────────────────────────────────────────────┘

[Turn 4] (유사어 등록 안내)
  → output_generator 응답에 유사어 등록 안내 포함:
    "LLM이 '서버명' → hostname 매핑을 추론했습니다.
     유사어로 등록하시겠습니까?"
     ① 전체등록  ② 선택등록  ③ 건너뛰기

  사용자: "전체등록"
  → context_resolver: synonym_registration 의도 감지
  → input_parser: synonym_registration 파싱
  → semantic_router → synonym_registrar 라우팅
  → synonym_registrar: Redis에 유사어 저장
  → END
```

### 6.4 캐시 관리 요청

```
사용자: "polestar DB의 스키마 캐시를 갱신해줘"

 1. [context_resolver] → [input_parser]

 2. [semantic_router]
    └── LLM: intent = "cache_management" 감지
        → routing_intent = "cache_management"

 3. [cache_management]
    ├── LLM: 의도 파싱 → "schema cache invalidate for polestar"
    ├── Redis 캐시 무효화 (fingerprint 삭제)
    ├── 메모리 캐시 삭제
    ├── DB에서 스키마 재조회
    ├── LLM: 컬럼 설명/유사어 재생성
    └── Redis에 새 캐시 저장

 4. → END (final_response: "polestar DB 스키마 캐시를 갱신했습니다.")
```

---

## 7. 문서 처리 상세 흐름

### 7.1 Excel 처리 파이프라인

```
Excel 양식 업로드
    │
    ▼
┌──────────────────────────────────────────────────────────────┐
│  excel_csv_converter (src/document/excel_csv_converter.py)    │
│                                                              │
│  Excel → CSV 변환 (LLM 컨텍스트 생성용)                       │
│  - 시트별 CSV 텍스트 생성                                     │
│  - 헤더 행 자동 감지                                          │
│  - 빈 행/열 스킵                                             │
│  → csv_sheet_data: {sheet_name: {headers, csv_text, ...}}    │
└──────────────────────────┬───────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────────┐
│  field_mapper (src/nodes/field_mapper.py)                     │
│                                                              │
│  CSV 헤더를 DB 컬럼에 매핑                                    │
│  3계층: hint → Redis synonym → LLM inference                 │
│  → column_mapping, mapping_sources                           │
└──────────────────────────┬───────────────────────────────────┘
                           │
                           ▼
          (query_generator → ... → result_organizer)
                           │
                           ▼
┌──────────────────────────────────────────────────────────────┐
│  output_generator (src/nodes/output_generator.py)            │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐  │
│  │  excel_parser (src/document/excel_parser.py)           │  │
│  │  - 헤더 행 위치 감지                                    │  │
│  │  - 데이터 시작 행 결정                                   │  │
│  │  - 병합셀 영역 파악                                     │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐  │
│  │  excel_writer (src/document/excel_writer.py)           │  │
│  │  - resolved_mapping으로 컬럼 대응                       │  │
│  │  - 데이터 행 채움                                       │  │
│  │  - 원본 서식/병합셀/수식 보존                            │  │
│  │  - 시트별 독립 처리 (멀티시트)                           │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
│  → output_file (Excel 바이너리)                              │
└──────────────────────────────────────────────────────────────┘
```

### 7.2 Word 처리 파이프라인

```
Word 양식 업로드
    │
    ▼
┌──────────────────────────────────────────────────────────────┐
│  word_parser (src/document/word_parser.py)                    │
│                                                              │
│  - {{placeholder}} 패턴 감지                                  │
│  - 테이블 구조 파싱 (헤더, 데이터 행)                          │
│  - 스타일 정보 보존                                           │
│  → template_structure                                        │
└──────────────────────────┬───────────────────────────────────┘
                           │
          (field_mapper → ... → result_organizer)
                           │
                           ▼
┌──────────────────────────────────────────────────────────────┐
│  word_writer (src/document/word_writer.py)                    │
│                                                              │
│  - {{placeholder}} → 실제 데이터 치환                         │
│  - 테이블 행 추가 (데이터 건수만큼)                            │
│  - 원본 스타일/폰트/서식 보존                                  │
│  → output_file (Word 바이너리)                               │
└──────────────────────────────────────────────────────────────┘
```

---

> 관련 문서: [01. 시스템 아키텍처](01_system_architecture.md) | [02. 소프트웨어 아키텍처](02_software_architecture.md)
