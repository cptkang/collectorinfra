# Capability Map: 스트리밍 응답 진행 상태 표시 (`plans/89` · D-204)

> 작성 2026-09-09 · 근거 `plans/89-streaming-progress-status-line.md` v2. **상태(2026-09-10): 3모듈 + T4 핸들러 마일스톤 구현 완료 · 코드 잔여 0 · 태그 해제**(후속은 D-127 승인 사항 — 실 브라우저 체감·playwright).
> 게이트 G-1~G-5는 사용자 확정 전이며 **권고안을 가정으로 채택**해 진행한다(사용자 지시
> "수립된 계획을 검토하여 구현을 진행하라", 2026-09-09). 가정은 각 SPEC 머리에 명시한다.

| Module id | Responsibility | Depends on |
|---|---|---|
| `sse-progress-contract` | 서버 SSE 계약 — `_known_nodes` 보정(`deep_agent` 등) · 생산자 태스크+큐 · `heartbeat` · `on_tool_*`/`on_custom_event` → `progress` 변환 · 설정 2개 · 두 스트림 라우트 공통 헬퍼 | — |
| `composite-task-progress` | 복합 질의 단계 이벤트 — `emit_task_progress` 공통 헬퍼를 2단 레벨 루프·1단 도구 러너가 호출 · deep_agent 재개/합성 마일스톤 · **T4 핸들러 마일스톤**(`src/utils/progress_events.emit_step` — `schema.sample` k/n · `pipeline.<stage>` 6종) | `sse-progress-contract`(custom event를 `progress{kind:"task"}`로 나른다) |
| `stream-status-ui` | 웹 UI — 커서 아래 상태 영역(활동 문구·경과·stalled) · 단계 칩 이관 · 복합 단계 목록 · 패널 도구 하위 행·`skipped` 렌더 · 라벨 사전 | `sse-progress-contract`, `composite-task-progress` |

Build order: `sse-progress-contract` → `composite-task-progress` → `stream-status-ui`

- 인터페이스는 제공자 SPEC에 둔다: SSE 이벤트 shape는 `SPEC-sse-progress-contract.md` §계약,
  `task` 페이로드는 `SPEC-composite-task-progress.md` §계약.
- 의존 방향은 arch_check 계층과 일치한다: `orchestration/`(custom event 발행) → `api/`(SSE 변환) → `static/`(소비).
- `plans/88`과의 경계: 판정 로직(`DependencyVerdict`)은 88 소관. 89는 **이벤트 자리와 필드명**만 만든다.
