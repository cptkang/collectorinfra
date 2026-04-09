# 웹 UI 처리 현황(Progress) 표시 명세

> 최종 갱신: 2026-03-26
> 관련 파일: `src/static/index.html`, `src/static/js/app.js`, `src/static/css/style.css`

---

## 1. 개요

웹 UI는 사용자가 자연어 질의를 입력한 후 에이전트 파이프라인이 처리되는 과정을 **두 가지 영역**에서 실시간으로 보여준다.

| 영역 | 위치 | 목적 |
|------|------|------|
| **채팅 인라인 진행 표시** | 채팅 메시지 영역 내부 (에이전트 말풍선) | 현재 어느 대분류 단계인지 간략히 표시 |
| **우측 처리 현황 패널** | 화면 오른쪽 사이드 패널 (`#progressPanel`) | 파이프라인 각 노드별 상세 데이터를 접기/펼치기로 표시 |

데이터 수신 방식은 **SSE(Server-Sent Events) 스트리밍**을 우선 사용하고, SSE를 사용할 수 없는 경우 일반 REST 응답에서 사후(post-hoc)로 단계를 재구성하여 표시한다.

---

## 2. 채팅 인라인 진행 표시 (Processing Stages)

사용자가 질의를 전송하면 채팅 영역에 에이전트 처리 중 메시지가 나타나며, 5단계 요약 진행 표시를 보여준다.

### 2.1 단계 정의

```javascript
var stages = ["parse", "schema", "sql", "exec", "result"];
```

| 단계 ID | 라벨 | 진행 중 메시지 | 설명 |
|---------|------|---------------|------|
| `parse` | 입력 분석 | "입력 분석 중..." | 사용자의 자연어 질의를 파싱하여 요구사항을 추출하는 단계 |
| `schema` | 스키마 탐색 | "데이터베이스 스키마 탐색 중..." | DB 라우팅 및 관련 테이블/컬럼 스키마를 탐색하는 단계 |
| `sql` | SQL 생성 | "SQL 쿼리 생성 중..." | LLM이 SQL을 생성하고 검증하는 단계 |
| `exec` | 쿼리 실행 | "쿼리 실행 중..." | 검증된 SQL을 DBHub를 통해 실행하는 단계 |
| `result` | 결과 정리 | "결과 정리 중..." | 쿼리 결과를 정리·요약하고 응답을 생성하는 단계 |

### 2.2 노드-to-단계 매핑

파이프라인 그래프의 세부 노드가 위 5단계 중 어디에 해당하는지는 다음 매핑으로 결정된다.

```javascript
var nodeToStage = {
    input_parser:      "parse",
    context_resolver:  "parse",
    semantic_router:   "schema",
    schema_analyzer:   "schema",
    query_generator:   "sql",
    query_validator:   "sql",
    query_executor:    "exec",
    multi_db_executor: "exec",
    result_organizer:  "result",
    result_merger:     "result",
    output_generator:  "result",
};
```

### 2.3 시각 상태

각 단계 표시 요소(`.stage`)는 세 가지 상태를 가진다:

| CSS 클래스 | 의미 | 시각 표현 |
|-----------|------|----------|
| (없음) | 대기 중 | 회색 점, 흐린 텍스트 |
| `.active` | 현재 진행 중 | teal 점 + 펄스 애니메이션, 밝은 텍스트 |
| `.done` | 완료 | 녹색 점, 일반 텍스트 |

### 2.4 동작 흐름

1. `handleSend()` 호출 시 `renderProcessingMessage()` 가 호출되어 처리 중 메시지 + 5단계 표시가 채팅 영역에 추가됨
2. SSE 이벤트 `node_start` 수신 시 → `updateProcessingStage(node, "start")` → 해당 stage를 `.active`로 전환
3. SSE 이벤트 `node_complete` 수신 시 → `updateProcessingStage(node, "complete")` → 해당 stage를 `.done`으로 전환
4. 모든 처리 완료 후 `removeProcessingMessage()` 로 처리 중 메시지 제거

---

## 3. 우측 처리 현황 패널 (Progress Panel)

화면 오른쪽에 고정된 사이드 패널로, 파이프라인의 각 노드를 개별 카드로 표시하며 노드별 상세 데이터를 제공한다.

### 3.1 패널 구조

```
┌─────────────────────────┐
│ 처리 현황           [▶] │  ← 헤더 (접기/펼치기 토글)
├─────────────────────────┤
│ ● 입력 분석       0.3s  │  ← pipeline-step (done)
│ ● 필드 매핑       1.2s  │  ← pipeline-step (done, expanded)
│   ├ 매핑 결과: 8/10     │     ← step-body (세부 데이터)
│   └ 매핑 출처: LLM: 3  │
│ ◉ 스키마 탐색           │  ← pipeline-step (active, 펄스)
│ ○ SQL 생성              │  ← pipeline-step (대기)
│ ...                     │
└─────────────────────────┘
```

### 3.2 파이프라인 노드 목록

총 12개 노드가 등록되어 있으며, 질의 유형에 따라 실제 실행되는 노드 조합이 달라진다.

| 노드 ID | 표시 라벨 | 역할 설명 |
|---------|----------|----------|
| `input_parser` | 입력 분석 | 사용자의 자연어 질의를 파싱하여 요구사항(필요한 데이터, 조건, 정렬 등)을 구조화된 형태로 추출한다. 첨부된 Excel/Word 템플릿이 있으면 템플릿 구조도 함께 분석한다. |
| `field_mapper` | 필드 매핑 | Excel 템플릿의 필드명(예: "서버명", "CPU사용률")을 DB 컬럼명(예: `servers.hostname`, `cpu_metrics.usage_pct`)에 매핑한다. 힌트, 유사어 사전, EAV 유사어, LLM 추론 등 다양한 소스를 활용한다. |
| `semantic_router` | DB 라우팅 | 질의 내용을 분석하여 어떤 데이터베이스/도메인(서버, CPU, 메모리, 디스크, 네트워크)에 접근해야 하는지 결정한다. |
| `schema_analyzer` | 스키마 탐색 | DBHub를 통해 대상 데이터베이스의 테이블/컬럼 스키마를 동적으로 탐색한다. 관련 테이블을 식별하고 컬럼 정보를 수집하여 SQL 생성에 필요한 스키마 컨텍스트를 구성한다. |
| `query_generator` | SQL 생성 | 파싱된 요구사항과 스키마 정보를 기반으로 LLM이 SELECT SQL 쿼리를 생성한다. 읽기 전용 쿼리만 생성하며 LIMIT 절을 포함한다. |
| `query_validator` | SQL 검증 | 생성된 SQL의 문법, 안전성(DML/DDL 차단), 참조 테이블/컬럼 존재 여부를 검증한다. 검증 실패 시 `query_generator`로 루프백한다(최대 3회). |
| `query_executor` | 쿼리 실행 | 검증을 통과한 SQL을 DBHub를 통해 실제 데이터베이스에서 실행한다. 타임아웃 30초, 최대 10,000행 제한이 적용된다. SQL 에러 발생 시 에러 컨텍스트와 함께 `query_generator`로 루프백한다. |
| `result_organizer` | 결과 정리 | 쿼리 실행 결과를 정리·요약한다. 데이터 충분성을 판단하고, 부족할 경우 `query_generator`로 루프백한다. 컬럼명을 사용자 친화적으로 매핑하고 행 수를 집계한다. |
| `output_generator` | 응답 생성 | 정리된 결과를 바탕으로 최종 자연어 응답을 생성하거나, Excel/Word 템플릿에 데이터를 채워 파일을 생성한다. |
| `multi_db_executor` | 멀티 DB 실행 | 여러 데이터베이스에 걸친 질의를 병렬로 실행한다. `semantic_router`가 복수 DB를 지정한 경우 활성화된다. |
| `result_merger` | 결과 병합 | `multi_db_executor`에서 반환된 복수 DB의 결과를 하나로 병합한다. |
| `error_response` | 에러 처리 | 파이프라인 중 복구 불가능한 에러가 발생했을 때 사용자에게 에러 메시지를 전달한다. |

### 3.3 노드 상태와 시각 표현

각 `pipeline-step` 요소는 다음 상태를 가진다:

| CSS 클래스 | 상태 | 점(dot) 색상 | 테두리 | 추가 효과 |
|-----------|------|-------------|--------|----------|
| (기본) | 대기 | `--text-muted` (회색) | `--border` (기본) | 없음 |
| `.active` | 진행 중 | `--accent` (teal) | `--accent-dim` (teal) | `pulse-dot` 애니메이션, box-shadow glow |
| `.done` | 완료 | `--success` (녹색) | `--border` (기본) | 없음 |
| `.error` | 에러 | `--error` (빨간색) | `--error` (빨간색) | 없음 |

### 3.4 접기/펼치기 동작

- 각 노드 헤더 클릭 시 `.expanded` 클래스가 토글되며, 세부 데이터 영역(`pipeline-step-body`)이 표시/숨김 처리된다.
- 노드 처리가 완료되고 세부 데이터가 있을 경우 **자동으로 펼쳐진다**.
- 펼쳐진 상태에서 헤더 오른쪽의 화살표(▶)가 90도 회전(▼)하여 펼침 상태를 시각적으로 표시한다.

### 3.5 경과 시간 표시

- 각 노드가 시작될 때 `timestamp_ms`를 `data-start` 속성에 저장한다.
- 노드 완료 시 `(완료 timestamp - 시작 timestamp) / 1000`을 계산하여 `0.0s` 형식으로 표시한다.
- 경과 시간이 0 이하면 표시하지 않는다 (폴백 모드에서는 타임스탬프가 없으므로 미표시).

---

## 4. 노드별 세부 표시 데이터

각 노드가 완료될 때 SSE `node_complete` 이벤트의 `data` 필드를 파싱하여 노드별로 다른 세부 정보를 렌더링한다.

### 4.1 `input_parser` — 입력 분석

| 필드 | 표시 라벨 | 형태 | 설명 |
|------|----------|------|------|
| `parsed_requirements` | 파싱된 요구사항 | JSON 미리보기 (`<pre>`) | LLM이 추출한 구조화된 요구사항 객체. 필요한 데이터, 조건, 정렬, 그룹핑 등을 포함. |
| `template_structure` | 템플릿 구조 | JSON 미리보기 (`<pre>`) | 첨부된 Excel/Word 파일의 헤더, 필드, 테이블 구조 정보. 파일 미첨부 시 미표시. |

### 4.2 `field_mapper` — 필드 매핑

| 필드 | 표시 라벨 | 형태 | 설명 |
|------|----------|------|------|
| `mapped_count` / `total_count` | 매핑 결과 | 뱃지 (`N/M (P%)`) | 전체 필드 중 성공적으로 매핑된 필드 수와 비율. 예: `8/10 (80%)` |
| `sources` | 매핑 출처 | 텍스트 | 매핑에 사용된 소스별 건수. 가능한 소스: |
| | | | - `hint`: 사용자가 제공한 힌트에 의한 매핑 |
| | | | - `synonym`: 유사어 사전에 의한 매핑 |
| | | | - `eav_synonym`: EAV(Entity-Attribute-Value) 유사어에 의한 매핑 |
| | | | - `llm_inferred`: LLM이 추론한 매핑 |
| `has_mapping_report` | 보고서 | 뱃지 (`생성됨`) | 매핑 보고서가 생성되었는지 여부 |

### 4.3 `semantic_router` — DB 라우팅

전용 렌더링 로직 없음. **generic fallback**으로 `data` 객체 전체를 JSON 미리보기로 표시한다.

### 4.4 `schema_analyzer` — 스키마 탐색

| 필드 | 표시 라벨 | 형태 | 설명 |
|------|----------|------|------|
| `relevant_tables` | 관련 테이블 | 리스트 (`<ul>`) | 질의와 관련된 것으로 식별된 테이블명 목록. 예: `servers`, `cpu_metrics` |
| `schema_summary` | 스키마 요약 | 테이블별 컬럼 목록 | 각 관련 테이블의 컬럼명을 쉼표 구분으로 표시. 테이블명은 teal 색상의 소제목으로 표시. |

### 4.5 `query_generator` — SQL 생성

| 필드 | 표시 라벨 | 형태 | 설명 |
|------|----------|------|------|
| `generated_sql` | 생성된 SQL | 코드 블록 (`<pre>`) | LLM이 생성한 SELECT SQL 쿼리. 모노스페이스 폰트로 코드 블록 형태로 표시. |

### 4.6 `query_validator` — SQL 검증

| 필드 | 표시 라벨 | 형태 | 설명 |
|------|----------|------|------|
| `passed` | 검증 결과 | 뱃지 (PASS/FAIL) | `true`이면 녹색 `PASS` 뱃지, `false`이면 빨간색 `FAIL` 뱃지 |
| `reason` | 사유 | 텍스트 | 검증 실패 시 실패 사유. 통과 시에는 미표시. |

### 4.7 `query_executor` — 쿼리 실행

| 필드 | 표시 라벨 | 형태 | 설명 |
|------|----------|------|------|
| `error` | 에러 | 뱃지 (빨간색) | SQL 실행 에러 메시지. 에러가 없으면 미표시. |
| `row_count` | 조회 건수 | 뱃지 (파란색) | 쿼리 결과 행 수. 예: `245건` |
| `preview_rows` | 미리보기 (최대 10행) | 테이블 (`<table>`) | 결과의 처음 최대 10행을 HTML 테이블로 표시. 데이터가 없으면 "데이터 없음" 표시. |

### 4.8 `result_organizer` — 결과 정리

| 필드 | 표시 라벨 | 형태 | 설명 |
|------|----------|------|------|
| `summary` | 요약 | 텍스트 | 결과에 대한 자연어 요약 |
| `is_sufficient` | 데이터 충분성 | 뱃지 | `true`이면 녹색 `충분`, `false`이면 빨간색 `부족`. 부족 시 `query_generator`로 루프백 트리거 가능. |
| `row_count` | 정리된 행 수 | 뱃지 (파란색) | 정리 후 최종 행 수. 예: `100건` |
| `column_mapping` | 컬럼 매핑 | JSON 미리보기 | DB 컬럼명 → 사용자 친화적 컬럼명 매핑. 예: `{"hostname": "서버명"}` |

### 4.9 `output_generator` — 응답 생성

| 필드 | 표시 라벨 | 형태 | 설명 |
|------|----------|------|------|
| `status` | 상태 | 뱃지 (녹색) | 처리 완료 상태. 기본값 `"완료"` |

### 4.10 `multi_db_executor` — 멀티 DB 실행

전용 렌더링 로직 없음. **generic fallback**으로 `data` 객체 전체를 JSON 미리보기로 표시한다.

### 4.11 `result_merger` — 결과 병합

전용 렌더링 로직 없음. **generic fallback**으로 `data` 객체 전체를 JSON 미리보기로 표시한다.

### 4.12 `error_response` — 에러 처리

| 필드 | 표시 라벨 | 형태 | 설명 |
|------|----------|------|------|
| `error` | 에러 | 텍스트 (빨간색) | 에러 메시지. `--error` 색상으로 강조 표시. |

---

## 5. 채팅 응답 메시지의 메타 정보

파이프라인 처리가 완료된 후 에이전트 응답 메시지(말풍선) 하단에 다음 메타 정보가 표시된다.

| 항목 | 라벨 | 형태 | 설명 |
|------|------|------|------|
| `row_count` | ROWS | 뱃지 | 최종 쿼리 결과 행 수 (예: `245건`) |
| `processing_time_ms` | TIME | 뱃지 | 전체 처리 소요 시간 (예: `3.2s`) |
| `query_id` | ID | 뱃지 | 쿼리 고유 ID 앞 8자리 (감사 추적용) |

또한 응답 메시지에는 다음 액션 요소가 조건부로 표시된다:

| 조건 | 표시 요소 | 설명 |
|------|----------|------|
| `executed_sql` 존재 | **실행된 SQL 보기** 토글 버튼 | 클릭 시 SQL 코드 블록 펼치기/접기 |
| `has_file && query_id` | **파일 다운로드** 링크 | 생성된 Excel/Word 파일 다운로드 |
| `has_mapping_report && query_id` | **매핑 보고서 다운로드** 링크 | 필드 매핑 보고서(Markdown) 다운로드 |
| `has_mapping_report && query_id` | **수정된 보고서 업로드** 버튼 | 사용자가 수정한 매핑 보고서(.md)를 업로드하여 피드백 반영 |

---

## 6. SSE 이벤트 타입

SSE 스트리밍(`/api/v1/query/stream`) 중 수신하는 이벤트 타입:

| 이벤트 타입 | 역할 | 주요 필드 | UI 동작 |
|------------|------|----------|---------|
| `token` | 응답 텍스트 토큰 | `content` | 채팅 메시지에 점진적으로 텍스트 추가 (타이핑 효과) |
| `node_start` | 노드 처리 시작 | `node`, `timestamp_ms` | 인라인 stage 활성화 + 패널에 새 pipeline-step 추가 |
| `node_complete` | 노드 처리 완료 | `node`, `data`, `timestamp_ms` | 인라인 stage 완료 + 패널 step에 세부 데이터 채움 |
| `meta` | 처리 메타데이터 | `query_id`, `executed_sql`, `row_count` 등 | 메타 데이터 축적 (응답 완료 시 활용) |
| `done` | 전체 처리 완료 | `query_id`, `has_file`, `file_name` 등 | 스트리밍 메시지 확정, 다운로드 버튼 생성 |
| `error` | 에러 발생 | `message` | 에러 알림 표시, 스트리밍 종료 |

---

## 7. 폴백(Fallback) 모드

SSE 엔드포인트를 사용할 수 없는 경우 (404/405 응답 또는 `text/event-stream`이 아닌 Content-Type), 일반 REST POST(`/api/v1/query`)로 폴백한다.

폴백 모드에서는 응답 수신 후 `showPostHocProgress(data)` 함수로 처리 단계를 **사후 재구성**한다:

```
1. input_parser             ← 항상 표시
2. schema_analyzer          ← executed_sql이 있으면 표시
3. query_generator          ← executed_sql이 있으면 표시 (SQL 포함)
4. query_validator          ← executed_sql이 있으면 표시 (PASS로 표시)
5. query_executor           ← row_count가 있으면 표시
6. output_generator         ← 항상 표시 ("완료")
```

폴백 모드에서는 경과 시간이 표시되지 않으며(`timestamp_ms`가 0), `field_mapper`, `semantic_router` 등 일부 노드는 표시되지 않는다.

---

## 8. 패널 접기/펼치기

- 패널 헤더의 토글 버튼(`#panelToggle`)을 클릭하면 `.chat-layout` 요소에 `.panel-collapsed` 클래스가 토글된다.
- 접힌 상태에서는 패널 너비가 42px로 축소되고, 헤더 타이틀과 본문이 숨겨진다.
- 토글 버튼의 화살표 아이콘이 180도 회전하여 방향을 표시한다.

---

## 9. 파일 첨부 질의 흐름

파일(.xlsx, .docx)이 첨부된 경우:

1. `/api/v1/query/file`로 `FormData` POST (SSE 미사용)
2. 채팅 인라인 처리 중 표시 + 우측 패널 초기화
3. 응답 수신 후 `showPostHocProgress(data)`로 사후 단계 표시
4. 응답 메시지에 파일 다운로드 링크 + 매핑 보고서 액션 추가

---

## 10. 뱃지(Badge) 색상 체계

세부 데이터 영역에서 사용되는 뱃지의 색상:

| CSS 클래스 | 색상 | 용도 |
|-----------|------|------|
| `step-data-badge--success` | `--success` (녹색) | 성공, PASS, 충분, 생성됨 |
| `step-data-badge--error` | `--error` (빨간색) | 실패, FAIL, 부족, 에러 |
| `step-data-badge--info` | `--info` (파란색) | 수치 정보 (건수, 비율 등) |
