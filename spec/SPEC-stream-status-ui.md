# Spec: stream-status-ui — 커서 아래 진행 상태 영역

> 모듈 id `stream-status-ui` (`CAPABILITY-MAP-89.md`) · `plans/89` §3.3·§3.4.
> **가정(게이트 권고안 채택)**: G-2 완료 후 **한 줄 요약으로 접음** · G-3 정지 판정 **15s** · G-4 패널 도구 하위 행
> **추가** · G-5 복합 단계 목록 완료 후 **접음**.

## Objective

스트리밍 말풍선의 커서 아래에 "지금 무엇을 하는지 · 얼마나 지났는지 · 살아 있는지"를 보인다. 복합 질의는
단계 목록을 실행 중에 보인다. 오른쪽 패널이 숨는 화면(≤1024px)에서도 동작한다.

## Tech Stack

바닐라 JS(`src/static/js/app.js`, IIFE·`var`) · CSS 변수(`style.css`) · SSE 수동 파서.

## Commands

```
pytest tests/test_api/test_ui_stream_status.py -q
pytest tests/test_api -q
```

## Project Structure

```
src/static/js/app.js     createStreamingMessage · SSE 루프 · updateProcessingStage · finalize/interrupt · toolLabels/agentLabels · renderTaskList · 패널 하위 행
src/static/css/style.css .stream-status* 규칙(기존 .processing-* 토큰 재사용)
tests/test_api/test_ui_stream_status.py   정적 계약 테스트(test_ui_scope_select.py 선례)
tests/e2e/test_progress_display.py        (RUN_E2E 옵트인) 기존 단언 유지 확인
```

## 계약 (DOM)

```
#streamingMessage .message-bubble
  #streamingText / #streamingCursor            (기존)
  #streamingStatus[role=status][aria-live=polite]
     .stream-status-line   .stream-status-spinner · #streamingStatusText · #streamingStatusElapsed
     .processing-stages    (#processingMessage에서 이관한 단계 칩 · agent 칩은 지연 생성)
     .stream-status-tasks  (kind:"task" 이벤트가 있을 때만 생성)
  #streamingMeta / #streamingSql               (기존)
```

상태 기계: `waiting` → `active`(node/tool/task) → `streaming`(첫 token) → `done` | `stalled`(마지막 이벤트 후 15s,
하트비트 수신 시 복귀) | `interrupted`/`error`(기존 경로 + 상태 영역 제거). 경과 시간은 클라이언트 1s 타이머,
`finally`에서 해제.

문구 우선순위: `task(in_progress)` > `tool` > `node`. 라벨 사전: `toolLabels`(도구 7종 + deepagents 내장), `agentLabels` 보강
(`process_query`·`host_inspect`·`fault_diagnosis`). 미지 이름 → "도구 실행: {name}".

완료(G-2/G-5): 상태 영역을 `.stream-status-done` 한 줄("완료 · N단계 · 12.3s")로 접고 단계 목록은 `hidden`. 클릭으로 펼침.

패널(G-4): `progress{kind:"tool"|"task"}`를 현재 활성 `pipeline-step` 본문의 하위 행으로 추가, `end`에서 완료·경과 표기.
`renderTaskList`: `skipped` 배지 "건너뜀" + `reason` 문구 + `truncated_count` "N대 절단"; `reason`이 있으면 상태값과 무관하게 "건너뜀" 우선.

## Code Style

기존 파일 관례 — `var`, 문자열 연결 HTML, `escapeHtml`, 주석에 계획·D-번호 인용. 새 색 토큰 금지(`--accent`·`--warning`·`--success` 재사용).

## Testing Strategy

정적 계약 테스트(문자열 존재·구조 단언) — 브라우저 e2e는 `RUN_E2E=1` 옵트인이라 코드만 갱신한다. `prefers-reduced-motion`에서
스피너 정지 규칙 존재 단언.

## Boundaries

- Always: 폴백(`ainvoke`·비SSE) 경로의 기존 `#processingMessage` 동작 유지 · 타이머 해제 · 미지 이벤트 무시
- Ask first: 토큰 렌더(`scheduleStreamingRender`) 변경 · 말풍선 레이아웃(폭·여백) 변경
- Never: LLM 사고 과정 텍스트 노출 · 상태 문구를 LLM이 생성

## Success Criteria

1. `createStreamingMessage`가 `#streamingStatus`를 만들고 `role="status"`를 갖는다.
2. SSE 루프에 `progress`·`heartbeat` 분기가 있고, `heartbeat`가 `lastEventAt`을 갱신한다.
3. 정지 판정 상수 15000ms·하트비트 부재 시 `stalled` 클래스 부여 코드가 있다.
4. `updateProcessingStage`가 `#streamingStatus` 안의 칩을 찾는다(기존 `#processingMessage`도 계속).
5. `renderTaskList`가 `skipped`/`reason`/`truncated_count`를 렌더한다.
6. `finally`에서 경과 타이머·정지 타이머를 해제한다.
7. `.stream-status-tasks`는 task 이벤트 없이는 생성되지 않는다(단일 DB 경로 불변).

## Open Questions

- 실 브라우저 체감 확인은 과금 경로(D-127) — 사용자 승인 후 1회.
