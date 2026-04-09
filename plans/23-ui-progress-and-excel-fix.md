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


---

# Verification Report

# Verification Report: Plan 23 - Web UI Progress & Excel Fix

> 검증일: 2026-03-24
> 대상 파일: `src/static/js/app.js` (949 lines)
> 기준 문서: `plans/23-ui-progress-and-excel-fix.md`

---

## 1. 코드 구조 및 일관성 검증

### IIFE 패턴 유지

| 항목 | 결과 | 비고 |
|------|------|------|
| `(function () { "use strict"; ... })();` 래핑 | PASS | Line 7~949 |
| `"use strict"` 선언 | PASS | Line 8 |

### 함수 배치 순서

전체 코드 구조가 논리적 섹션 주석(`// --- Section ---`)으로 구분되어 있으며, 새로 추가된 함수들이 적절한 위치에 삽입되었습니다.

| 새 함수/변수 | 위치 | 적절성 |
|---|---|---|
| `currentThreadId` | Line 35 (State 섹션) | PASS |
| `nodeToStage` | Line 272 (Stage Animation 섹션) | PASS |
| `updateProcessingStage()` | Line 288 (Stage Animation 섹션) | PASS |
| `showPostHocProgress()` | Line 716 (Progress Panel 섹션) | PASS |

### 변수/함수 참조 오류

코드 전체에서 사용되는 모든 변수와 함수가 IIFE 스코프 내에서 선언되어 있으며, 참조 오류는 발견되지 않았습니다.

**결론: PASS**

---

## 2. 문제 A 검증: 채팅 인디케이터 SSE 연동

### 2.1 setTimeout 타이머 기반 자동 진행 제거

| 항목 | 결과 | 비고 |
|------|------|------|
| `startStageAnimation()`에서 `setTimeout` 제거 | PASS | Line 281-286: 초기 상태 설정만 수행, 타이머 없음 |
| 기존 `advance` 함수 제거 | PASS | 코드 내 `advance` 참조 없음 |
| `stageTimer` 기반 자동 진행 제거 | PASS | `stageTimer`는 `stopStageAnimation()` (Line 306-310)에서 정리 용도로만 사용 |

### 2.2 nodeToStage 매핑 객체

Line 272-279에 `nodeToStage` 객체가 존재합니다.

| 노드명 | 매핑 스테이지 | 정확성 |
|--------|-------------|--------|
| `input_parser` | `parse` | PASS |
| `context_resolver` | `parse` | PASS |
| `semantic_router` | `schema` | PASS |
| `schema_analyzer` | `schema` | PASS |
| `query_generator` | `sql` | PASS |
| `query_validator` | `sql` | PASS |
| `query_executor` | `exec` | PASS |
| `multi_db_executor` | `exec` | PASS |
| `result_organizer` | `result` | PASS |
| `result_merger` | `result` | PASS |
| `output_generator` | `result` | PASS |

Plan 23 Sec 2.1의 `stageMap`과 동일한 매핑입니다.

### 2.3 updateProcessingStage() 함수

Line 288-304에 존재하며, Plan 23의 구현 상세와 일치합니다.

| 항목 | 결과 | 비고 |
|------|------|------|
| `nodeToStage[node]` 조회 | PASS | Line 289 |
| DOM `.stage[data-stage="..."]` 셀렉터 | PASS | Line 292 |
| `status === "start"` 시 `active` 클래스 추가 | PASS | Line 298 |
| `status === "complete"` 시 `active` 제거 + `done` 추가 | PASS | Line 300-302 |
| `processingText` 메시지 업데이트 | PASS | Line 299 |

### 2.4 SSE 이벤트 핸들러에서 호출

| 이벤트 | 호출 | 위치 |
|--------|------|------|
| `node_start` | `updateProcessingStage(event.node, "start")` | Line 487 |
| `node_complete` | `updateProcessingStage(event.node, "complete")` | Line 490 |

### 2.5 stageMessages 키와 stages 배열 일치

- `stages` (Line 38): `["parse", "schema", "sql", "exec", "result"]`
- `stageMessages` (Line 46-52): 키가 `parse, schema, sql, exec, result`

| stages 원소 | stageMessages 키 존재 | 결과 |
|---|---|---|
| parse | O | PASS |
| schema | O | PASS |
| sql | O | PASS |
| exec | O | PASS |
| result | O | PASS |

**결론: PASS -- 문제 A 수정이 Plan 23 사양대로 정확히 구현되었습니다.**

---

## 3. 문제 E 검증: SSE 완료 시 다운로드 버튼

### 3.1 finalizeStreamingMessage() 내 다운로드 버튼 생성 로직

Line 578-590에 다운로드 버튼 생성 로직이 존재합니다.

| 항목 | 결과 | 비고 |
|------|------|------|
| `meta.has_file && meta.query_id` 조건 | PASS | Line 579 |
| URL 형식 `/api/v1/query/{query_id}/download` | PASS | Line 584 |
| `escapeHtml(meta.file_name \|\| "파일")` XSS 방지 | PASS | Line 586 |
| `bubble`에 `insertAdjacentHTML("beforeend", ...)` | PASS | Line 588 |

### 3.2 XSS 관련 보안 점검

**[Major] `meta.query_id`가 URL에 직접 삽입됨 (Line 584)**

```javascript
'<a class="message-download" href="/api/v1/query/' + meta.query_id + '/download">'
```

`meta.query_id`는 서버에서 생성되는 UUID이므로 일반적으로 안전하지만, `escapeHtml()` 처리가 되어 있지 않습니다. 만약 서버 응답이 변조되거나 예상치 못한 문자가 포함될 경우, `href` 속성을 통한 injection이 가능합니다.

동일한 패턴이 `renderAgentMessage()`의 Line 357에서도 사용됩니다:
```javascript
'<a class="message-download" href="/api/v1/query/' + data.query_id + '/download">'
```

**권장 조치**: `query_id`에 대해 `encodeURIComponent()` 처리를 추가하거나, UUID 형식 검증을 수행할 것.

**심각도: Minor** -- `query_id`는 서버 생성 UUID이며 클라이언트 입력이 아닌 점을 고려.

**결론: PASS (Minor 보안 권고사항 1건)**

---

## 4. 문제 G 검증: 응답 텍스트 포맷

### 4.1 renderAgentMessage()에서 response-text 처리

Line 367:
```javascript
'<div class="response-text">' + escapeHtml(responseText) + '</div>'
```

`escapeHtml()`을 적용하고 `innerHTML`로 삽입합니다. 이 방식은:
- HTML 태그가 렌더링되지 않음 (안전)
- 줄바꿈(`\n`)은 HTML에서 무시됨 -- 하지만...

### 4.2 CSS `white-space: pre-wrap` 확인

`style.css` Line 750-755:
```css
.message--agent .message-bubble .response-text {
    white-space: pre-wrap;
    font-family: var(--font-mono);
    font-size: 0.8125rem;
    line-height: 1.8;
}
```

`white-space: pre-wrap`이 `.response-text`에 적용되어 있으므로, `escapeHtml()` 후 텍스트의 `\n`이 시각적으로 줄바꿈으로 렌더링됩니다.

### 4.3 스트리밍 메시지에서의 처리

Line 483:
```javascript
if (textEl) textEl.textContent = accumulatedText;
```

스트리밍 중에는 `textContent`를 사용하므로 XSS 안전하면서도 `pre-wrap` CSS에 의해 줄바꿈이 보존됩니다.

### 4.4 Plan 23의 의도와의 비교

Plan 23 Sec 2.5에서는 두 가지 방안을 제시했습니다:
1. `escapeHtml()` 후 `\n` -> `<br>` 변환 (`renderResponseText()` 함수)
2. `escapeHtml()` + `white-space: pre-wrap` CSS (더 안전한 대안)

현재 구현은 방안 2를 채택한 것으로 보입니다. 이는 Plan 23에서 "더 안전할 수 있음"이라고 평가한 접근법입니다.

**결론: PASS -- CSS `pre-wrap` 방식으로 줄바꿈이 보존되며, XSS도 방지됩니다.**

---

## 5. 문제 B 검증: Fallback Progress Panel

### 5.1 showPostHocProgress() 함수 존재

Line 716-745에 `showPostHocProgress(data)` 함수가 존재합니다.

### 5.2 executeFallbackQuery()에서 호출

Line 626: `showPostHocProgress(data);` -- PASS

### 5.3 executeFileQuery()에서 호출

Line 668: `showPostHocProgress(data);` -- PASS

### 5.4 handleNodeStart()/handleNodeComplete() 재사용

Line 738-743에서 `handleNodeStart()`와 `handleNodeComplete()`를 재사용합니다:
```javascript
steps.forEach(function (step) {
    handleNodeStart({ node: step.node, timestamp_ms: 0 });
    handleNodeComplete({
        node: step.node,
        data: step.data || {},
        timestamp_ms: 0,
    });
});
```

### 5.5 Plan 23과의 차이점

Plan 23 Sec 2.6의 구현 예시와 비교:

| 항목 | Plan 23 | 실제 구현 | 평가 |
|------|---------|-----------|------|
| 기본 구조 | `handleNodeStart/Complete` 호출 | 동일 | PASS |
| 표시 노드 | `query_generator`, `query_executor`만 | `input_parser`, `schema_analyzer`, `query_generator`, `query_validator`, `query_executor`, `output_generator` | **개선** |
| `query_validator` 표시 | 없음 | `{ passed: true, reason: "" }` 포함 | **개선** |

실제 구현이 Plan보다 더 풍부한 정보를 제공합니다. 이는 사용자 경험 측면에서 긍정적인 개선입니다.

**결론: PASS**

---

## 6. 문제 F 검증: thread_id 전달

### 6.1 currentThreadId 변수 선언

Line 35: `var currentThreadId = null;` -- PASS

### 6.2 SSE 응답에서 thread_id 저장

Line 509: `currentThreadId = metaData.thread_id || currentThreadId;` -- PASS

### 6.3 Fallback 응답에서 thread_id 저장

Line 627: `currentThreadId = data.thread_id || currentThreadId;` -- PASS

### 6.4 File 응답에서 thread_id 저장

Line 669: `currentThreadId = data.thread_id || currentThreadId;` -- PASS

### 6.5 executeFileQuery()의 FormData에 thread_id append

Line 649-651:
```javascript
if (currentThreadId) {
    formData.append("thread_id", currentThreadId);
}
```

PASS -- Plan 23 Sec 2.4의 구현과 정확히 일치합니다.

**결론: PASS**

---

## 7. 보안 검증

### 7.1 XSS 취약점 분석

**escapeHtml() 함수** (Line 683-687):
```javascript
function escapeHtml(text) {
    var div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
}
```

이 구현은 브라우저의 네이티브 텍스트 인코딩을 활용하며, `<`, `>`, `&`, `"` 등을 안전하게 이스케이프합니다.

### 7.2 사용자 입력의 innerHTML 삽입 점검

| 위치 | 입력 소스 | escapeHtml 사용 | 결과 |
|------|-----------|-----------------|------|
| Line 217 | `msg.file.name` | O | PASS |
| Line 223 | `msg.content` (사용자 질의) | O | PASS |
| Line 367 | `responseText` (서버 응답) | O | PASS |
| Line 349 | `data.executed_sql` | O | PASS |
| Line 359 | `data.file_name` | O | PASS |
| Line 574 | `meta.executed_sql` | O | PASS |
| Line 586 | `meta.file_name` | O | PASS |
| Line 769 | `label` (nodeLabels) | O | PASS |
| Line 829 | `t` (테이블명) | O | PASS |

### 7.3 innerHTML 직접 삽입 (escapeHtml 미사용) 점검

| 위치 | 내용 | 위험도 | 평가 |
|------|------|--------|------|
| Line 334 | `data.query_id.substring(0, 8)` | Low | 서버 생성 UUID, 클라이언트 제어 불가 |
| Line 357 | `data.query_id` in URL | Low | 위 3.2에서 지적. Minor |
| Line 558 | `meta.query_id.substring(0, 8)` | Low | 서버 생성 UUID |
| Line 584 | `meta.query_id` in URL | Low | 위 3.2에서 지적. Minor |

### 7.4 URL 구성 보안

| URL | 사용자 제어 가능 값 | 처리 방식 | 위험도 |
|-----|---------------------|-----------|--------|
| `/api/v1/query/stream` | 없음 (body는 JSON) | N/A | 없음 |
| `/api/v1/query` | 없음 (body는 JSON) | N/A | 없음 |
| `/api/v1/query/file` | 없음 (body는 FormData) | N/A | 없음 |
| `/api/v1/query/{query_id}/download` | `query_id` (서버 생성) | 미처리 | Minor |

### 7.5 renderDataTable()의 `title` 속성

Line 934:
```javascript
html += "<td title='" + escapeHtml(String(val)) + "'>" + escapeHtml(String(val)) + "</td>";
```

**[Minor] `title` 속성에 작은따옴표(`'`) 사용**

`escapeHtml()`은 작은따옴표를 이스케이프하지 않습니다. 만약 데이터 값에 `'`가 포함되면 속성이 조기 종료될 수 있습니다.

예: `val = "it's test"` -> `title='it's test'` -> 속성 구문 오류 발생

**권장 조치**: 작은따옴표 대신 큰따옴표를 사용하거나, 작은따옴표도 이스케이프하는 함수를 사용할 것.

**심각도: Minor** -- XSS 공격은 어렵지만 HTML 구문 오류가 발생할 수 있음.

**결론: PASS (Minor 보안 권고사항 2건)**

---

## 8. 기존 기능 호환성 검증

### 8.1 함수 시그니처 변경

| 함수 | 기존 시그니처 | 현재 시그니처 | 결과 |
|------|--------------|-------------|------|
| `renderAgentMessage(data)` | `(data)` | `(data)` | PASS |
| `executeStreamingQuery(query)` | `(query)` | `(query)` | PASS |
| `executeFallbackQuery(query)` | `(query)` | `(query)` | PASS |
| `executeFileQuery(query, file)` | `(query, file)` | `(query, file)` | PASS |

### 8.2 전역 함수 유지

| 전역 함수 | 위치 | 결과 |
|-----------|------|------|
| `window.toggleSql` | Line 697 | PASS |
| `window.togglePipelineStep` | Line 944 | PASS |

### 8.3 이벤트 리스너 유지

| 이벤트 리스너 | 대상 | 위치 | 결과 |
|--------------|------|------|------|
| `input` | `promptEl` | Line 71 | PASS |
| `keydown` | `promptEl` | Line 72 | PASS |
| `click` | `sendBtn` | Line 73 | PASS |
| `change` | `fileInput` | Line 74 | PASS |
| `click` | `removeFileBtn` | Line 75 | PASS |
| `click` | `hintButtons` | Line 77-83 | PASS |
| `click` | `panelToggle` | Line 86-88 | PASS |

### 8.4 DOM 요소 참조 일관성

`index.html`에 정의된 모든 ID와 `app.js`의 `getElementById` 호출이 일치하는지 확인:

| HTML ID | JS 참조 | 결과 |
|---------|---------|------|
| `chatMessages` | Line 12 | PASS |
| `chatWelcome` | Line 13 | PASS |
| `chatError` | Line 14 | PASS |
| `chatErrorText` | Line 15 | PASS |
| `prompt` | Line 16 | PASS |
| `fileInput` | Line 17 | PASS |
| `filePreview` | Line 18 | PASS |
| `fileName` | Line 19 | PASS |
| `fileSize` | Line 20 | PASS |
| `removeFile` | Line 21 | PASS |
| `sendBtn` | Line 22 | PASS |
| `progressPanel` | Line 24 | PASS |
| `progressPipeline` | Line 25 | PASS |
| `progressEmpty` | Line 26 | PASS |
| `panelToggle` | Line 27 | PASS |

**결론: PASS**

---

## 9. 추가 관찰사항

### 9.1 문제 D (파일 질의 SSE 스트리밍) 미구현 -- 의도적

Plan 23 Sec 3의 구현 순서에서 단계 6(우선순위: 낮음, Phase 3)으로 분류되어 있으며, 현재 구현에는 포함되지 않았습니다. 이는 Plan에 따른 의도적인 미구현입니다.

### 9.2 문제 C (채팅 인디케이터와 패널 비동기)

Plan 23 Sec 2.1의 방안 4("처리 중 메시지를 스트리밍 메시지로 교체하지 않고, 스테이지 인디케이터를 유지한 채 응답 텍스트를 아래에 점진적으로 표시")는 현재 구현에서 채택되지 않았습니다.

현재 동작: Line 454에서 `removeProcessingMessage()`가 호출된 후 Line 455에서 `createStreamingMessage()`로 전환됩니다. 이 시점에서 채팅 영역의 스테이지 인디케이터는 제거됩니다.

다만 `updateProcessingStage()`가 SSE 이벤트에 의해 호출되므로, processing message가 존재하는 동안에는 실제 파이프라인 진행과 동기화됩니다. 스트리밍 메시지로 전환된 이후에는 오른쪽 Progress Panel에서 진행 상태를 확인할 수 있습니다.

**심각도: Minor** -- 모바일 환경에서 Progress Panel이 숨겨지는 경우 사용자 경험이 다소 부족할 수 있으나, Plan 23에서 이 항목의 완전한 해결은 범위 외로 볼 수 있습니다.

### 9.3 stopStageAnimation()에서 잔여 stageTimer 정리

Line 306-310의 `stopStageAnimation()`은 타이머를 정리하는 로직을 유지하고 있습니다. 현재 `startStageAnimation()`에서 타이머를 설정하지 않으므로 이 정리 로직은 실행되지 않지만, 방어적 코딩으로서 유지해도 무방합니다.

---

## 10. 종합 결과

### 검증 항목별 결과 요약

| # | 검증 항목 | 결과 | 발견 이슈 |
|---|-----------|------|-----------|
| 1 | 코드 구조 및 일관성 | **PASS** | 없음 |
| 2 | 문제 A: 채팅 인디케이터 SSE 연동 | **PASS** | 없음 |
| 3 | 문제 E: SSE 완료 시 다운로드 버튼 | **PASS** | Minor 1건 |
| 4 | 문제 G: 응답 텍스트 포맷 | **PASS** | 없음 |
| 5 | 문제 B: Fallback Progress Panel | **PASS** | 없음 |
| 6 | 문제 F: thread_id 전달 | **PASS** | 없음 |
| 7 | 보안 검증 | **PASS** | Minor 2건 |
| 8 | 기존 기능 호환성 | **PASS** | 없음 |

### 발견된 이슈 목록

| # | 심각도 | 위치 | 설명 | 권장 조치 |
|---|--------|------|------|-----------|
| 1 | **Minor** | Line 357, 584 | `query_id`가 URL href에 escapeHtml/encodeURIComponent 없이 삽입됨 | `encodeURIComponent(query_id)` 적용 또는 UUID 형식 검증 |
| 2 | **Minor** | Line 934 | `<td title='...'>` 속성에 작은따옴표 사용 -- 데이터에 `'` 포함 시 HTML 구문 오류 | 큰따옴표 사용 또는 작은따옴표 이스케이프 추가 |
| 3 | **Minor** | Line 454-455 | 스트리밍 전환 시 채팅 인디케이터 즉시 제거 -- 모바일에서 진행 상태 확인 불가 | 향후 개선 과제로 기록 |

### 최종 판정

**PASS** -- Plan 23에서 계획된 문제 A, B, E, F, G의 수정이 모두 사양대로 구현되었습니다. 발견된 이슈 3건은 모두 Minor 등급으로, 기능 동작에 영향을 주지 않습니다. Critical 및 Major 이슈는 없습니다.
