# Plan 23: Web UI 진행상태 표시 및 Excel 업로드/다운로드 수정 계획

> 작성일: 2026-03-24
> 관련 파일: `src/static/js/app.js`, `src/static/index.html`, `src/static/css/style.css`, `src/api/routes/query.py`

---

## 1. 현황 분석 및 문제점

### 1.1 진행상태 표시 문제

#### 문제 A: 채팅 영역 처리 인디케이터가 가짜 애니메이션

`app.js:270-298` — `startStageAnimation()`이 실제 파이프라인 진행과 무관하게 2~4초 간격 타이머로 단계를 전환한다.

```javascript
// 현재: 하드코딩된 타이머 기반
stageTimer = setTimeout(advance, 2000 + Math.random() * 2000);
```

- 실제 파이프라인이 10초 걸려도 3초 만에 모든 스테이지가 "done"으로 표시
- 파이프라인이 1초에 끝나도 애니메이션은 계속 진행 중으로 표시
- SSE 이벤트(`node_start`, `node_complete`)가 이 인디케이터에 반영되지 않음

#### 문제 B: 오른쪽 Progress Panel이 비스트리밍 모드에서 비어있음

- `executeFallbackQuery()` (line 575): `resetProgressPanel()`을 호출하지만 이후 노드 이벤트를 전달하지 않음
- `executeFileQuery()` (line 606): 일반 POST를 사용하여 SSE 이벤트 없이 진행됨 -> 오른쪽 패널 항상 비어있음
- SSE 스트리밍 실패 시 폴백되면 패널에 아무 정보도 표시되지 않음

#### 문제 C: SSE 스트리밍 성공 시에도 채팅 인디케이터와 패널이 비동기적으로 동작

- `removeProcessingMessage()`가 호출되면 채팅 내 스테이지 인디케이터가 즉시 제거됨 (line 441)
- 이후 `createStreamingMessage()`로 전환되나, 이때 채팅 영역에는 스테이지 정보가 없음
- 오른쪽 패널만 노드 진행을 표시하지만, 모바일(640px 이하)에서는 패널이 숨겨져 진행상태를 전혀 볼 수 없음

### 1.2 Excel 파일 업로드/다운로드 문제

#### 문제 D: 파일 질의에 SSE 스트리밍 미지원

- `executeFileQuery()` (line 606)는 `/api/v1/query/file` POST만 사용
- 처리 시간이 30~60초까지 걸릴 수 있으나, 사용자에게 가짜 타이머 애니메이션만 표시
- 실제 어느 노드에서 처리 중인지 알 수 없음

#### 문제 E: 다운로드 버튼 렌더링 조건 불완전

- `renderAgentMessage()` (line 342): `data.has_file && data.query_id` 조건으로 다운로드 버튼 표시
- SSE 스트리밍 완료 시 `finalizeStreamingMessage()` (line 522)에서는 다운로드 버튼이 생성되지 않음
- `metaData`에 `has_file`과 `file_name`이 포함되어도 다운로드 링크가 누락됨

#### 문제 F: 파일 질의의 thread_id 미전달

- `executeFileQuery()`에서 FormData에 `thread_id`를 포함하지 않음
- 멀티턴 대화에서 파일 질의 시 세션 연속성이 깨짐

#### 문제 G: 응답 텍스트가 escapeHtml 처리되어 포맷 손실

- `renderAgentMessage()` (line 354): `escapeHtml(responseText)` — LLM 응답의 마크다운, 줄바꿈 등이 전부 일반 텍스트로 출력
- 표 형식이나 목록 등이 의미 없는 평문으로 표시됨

---

## 2. 수정 계획

### 2.1 채팅 인디케이터를 SSE 이벤트에 연동 (문제 A, C 해결)

**변경 파일:** `src/static/js/app.js`

**방안:**
1. `startStageAnimation()` 타이머 기반 자동 진행 제거
2. SSE `node_start` 이벤트 수신 시 채팅 인디케이터의 해당 스테이지를 `active`로 전환
3. SSE `node_complete` 이벤트 수신 시 해당 스테이지를 `done`으로 전환
4. 처리 중 메시지를 스트리밍 메시지로 교체하지 않고, 스테이지 인디케이터를 유지한 채 응답 텍스트를 아래에 점진적으로 표시

**구현 상세:**
```javascript
// node_start/complete를 처리하는 함수 추가
function updateProcessingStage(node, status) {
    // nodeLabels에서 stages 매핑으로 변환
    var stageMap = {
        input_parser: "parse", context_resolver: "parse",
        semantic_router: "schema", schema_analyzer: "schema",
        query_generator: "sql", query_validator: "sql",
        query_executor: "exec", multi_db_executor: "exec",
        result_organizer: "result", result_merger: "result",
        output_generator: "result",
    };
    var stage = stageMap[node];
    if (!stage) return;

    var stageEl = document.querySelector('.stage[data-stage="' + stage + '"]');
    if (!stageEl) return;

    if (status === "start") {
        stageEl.classList.add("active");
    } else if (status === "complete") {
        stageEl.classList.remove("active");
        stageEl.classList.add("done");
    }
}
```

### 2.2 파일 질의에 SSE 스트리밍 엔드포인트 추가 (문제 D 해결)

**변경 파일:** `src/api/routes/query.py`, `src/static/js/app.js`

**백엔드 방안:**
- `/api/v1/query/file/stream` POST 엔드포인트 추가 (multipart/form-data + SSE)
- 기존 `/query/stream`의 `event_generator()` 로직을 재사용하되, 초기 State에 파일 정보 포함

**프론트엔드 방안:**
- `executeFileQuery()`를 SSE 방식으로 변경
- FormData를 POST로 전송하고 SSE 응답을 수신
- 실패 시 기존 `/api/v1/query/file` POST로 폴백

### 2.3 SSE 스트리밍 완료 시 다운로드 버튼 표시 (문제 E 해결)

**변경 파일:** `src/static/js/app.js`

**방안:** `finalizeStreamingMessage()`에 다운로드 버튼 생성 로직 추가

```javascript
// finalizeStreamingMessage 내에 추가
if (meta.has_file && meta.query_id) {
    var downloadContainer = document.getElementById("streamingDownload")
        || document.createElement("div");
    downloadContainer.innerHTML =
        '<a class="message-download" href="/api/v1/query/' + meta.query_id + '/download">' +
            '<svg ...>...</svg>' +
            escapeHtml(meta.file_name || "파일") + ' 다운로드' +
        '</a>';
    var bubble = streamingMsg.querySelector(".message-bubble");
    if (bubble) bubble.appendChild(downloadContainer);
}
```

### 2.4 파일 질의에 thread_id 전달 (문제 F 해결)

**변경 파일:** `src/static/js/app.js`

**방안:** `executeFileQuery()`에서 현재 세션의 thread_id를 FormData에 포함

```javascript
// executeFileQuery 내
var formData = new FormData();
formData.append("query", query);
formData.append("file", file);
if (currentThreadId) {
    formData.append("thread_id", currentThreadId);
}
```

### 2.5 응답 텍스트 포맷 개선 (문제 G 해결)

**변경 파일:** `src/static/js/app.js`

**방안:** LLM 응답의 줄바꿈을 `<br>`로 변환하고, 기본적인 마크다운 렌더링 적용

```javascript
function renderResponseText(text) {
    // XSS 방지: 먼저 escapeHtml 적용
    var escaped = escapeHtml(text);
    // 줄바꿈 -> <br>
    escaped = escaped.replace(/\n/g, "<br>");
    return escaped;
}
```

- 또는 경량 마크다운 라이브러리(marked.js ~28KB) 도입 검토
- `white-space: pre-wrap`이 이미 CSS에 설정되어 있으므로, escapeHtml만 적용하고 innerHTML 대신 textContent 유지하는 것이 더 안전할 수 있음

### 2.6 Fallback 모드에서 Progress Panel 정보 표시 (문제 B 해결)

**변경 파일:** `src/static/js/app.js`

**방안:** 비스트리밍(fallback/file) 응답 완료 시, 응답 데이터에서 추출 가능한 정보를 Progress Panel에 사후 표시

```javascript
function showPostHocProgress(data) {
    resetProgressPanel();
    progressEmpty.style.display = "none";

    // 실행된 SQL이 있으면 query_generator 완료로 표시
    if (data.executed_sql) {
        handleNodeStart({ node: "query_generator", timestamp_ms: 0 });
        handleNodeComplete({
            node: "query_generator",
            data: { generated_sql: data.executed_sql },
            timestamp_ms: 0,
        });
    }
    // row_count가 있으면 query_executor 완료로 표시
    if (data.row_count != null) {
        handleNodeStart({ node: "query_executor", timestamp_ms: 0 });
        handleNodeComplete({
            node: "query_executor",
            data: { row_count: data.row_count },
            timestamp_ms: 0,
        });
    }
}
```

---

## 3. 구현 순서

| 단계 | 작업 | 파일 | 우선순위 |
|------|------|------|----------|
| 1 | 채팅 인디케이터 SSE 연동 | `app.js` | 높음 |
| 2 | SSE 스트리밍 완료 시 다운로드 버튼 | `app.js` | 높음 |
| 3 | 응답 텍스트 줄바꿈 보존 | `app.js` | 높음 |
| 4 | Fallback 모드 Progress Panel | `app.js` | 중간 |
| 5 | 파일 질의 thread_id 전달 | `app.js` | 중간 |
| 6 | 파일 질의 SSE 스트리밍 | `query.py`, `app.js` | 낮음 (Phase 3) |

---

## 4. 영향 범위

- **변경 파일**: `src/static/js/app.js` (주), `src/api/routes/query.py` (단계 6만)
- **CSS 변경 없음**: 기존 스타일이 충분
- **백엔드 API 호환성**: 기존 엔드포인트 변경 없음, 새 엔드포인트만 추가
- **테스트**: Plan 24 (Playwright UI 테스트)에서 검증
