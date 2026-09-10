# Plan 89 구현 계획 — 스트리밍 진행 상태 (D-204)

근거: `plans/89-WIP-streaming-progress-status-line.md` v2 · `CAPABILITY-MAP-89.md` · SPEC 3건.
게이트 G-1~G-5는 권고안을 가정으로 채택(사용자 지시 2026-09-09 "구현을 진행하라").

## 순서와 검증 체크포인트

| 단계 | 모듈 | 내용 | 체크포인트 |
|---|---|---|---|
| T0 | contract | 중첩 CompiledGraph의 `on_tool_start`·`on_custom_event`가 바깥 `astream_events`에 전파되는지 실측 | `test_stream_nested_events.py` 통과 → T3/T9 전제 성립. 실패 시 deep_agent `ainvoke(config=)`에 콜백 전달로 대체 |
| T1 | contract | `_known_nodes` + `deep_agent` 진행 데이터(두 라우트) | MockGraph에서 두 라우트 모두 `deep_agent` 이벤트 |
| T2 | contract | 생산자 태스크+큐+하트비트 공통 헬퍼 · 설정 2개 · settings_help · .env.example | 하트비트/타임아웃/취소/플래그 off 바이트 동일 |
| T3 | contract | `on_tool_*`·`on_custom_event` → `progress` | mock 이벤트 변환 단언 |
| T9 | composite | `emit_task_progress` + 2단·1단 호출 + deep_agent 마일스톤 | 헬퍼 테스트·대칭 grep |
| T5 | ui | 상태 영역·상태 기계·타이머·라벨·복합 단계 목록·패널 하위 행·`skipped` 렌더 | 정적 계약 테스트 |
| T6 | ui | e2e 단언 점검(코드만) | 기존 P-01~P-10 전제 유지 확인 |
| T8 | docs | D-204 본문 등재 · INDEX/plans 상태(`-TODO`→`-WIP` 또는 해제) · 개정 이력 | grep 3표 |

병렬 가능: T9는 T1~T3과 파일이 겹치지 않는다(orchestration vs api). T5는 T3의 shape 확정 후.

## 리스크
- `wait_for(__anext__)` 재호출 금지 → 큐 구조(§3.2-④).
- 두 제너레이터 복사본 → 루프 머리만 공통 헬퍼로 교체, 나머지 분기 불변.
- 88과 호출 지점 공유(`agent_orchestrator.py:76-92`, `deepagents_tools.py:255-280`) → 2줄만.
