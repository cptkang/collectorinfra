"""커서 아래 진행 상태 영역 — 마크업·배선 정적 계약 (plans/89 T5 · D-204 · `SPEC-stream-status-ui.md`).

브라우저 e2e(playwright)는 `RUN_E2E=1` 옵트인이라 이 환경에서 돌지 않는다. `test_ui_scope_select.py`
선례대로 **계약이 되는 지점만 정적으로 고정**한다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_STATIC = Path(__file__).resolve().parents[2] / "src" / "static"


@pytest.fixture(scope="module")
def app_js() -> str:
    return (_STATIC / "js" / "app.js").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def style_css() -> str:
    return (_STATIC / "css" / "style.css").read_text(encoding="utf-8")


class TestStatusArea:
    def test_streaming_bubble_has_status_slot_below_cursor(self, app_js):
        """★ 커서(#streamingCursor) 바로 아래에 상태 영역이 있다 — 요청의 핵심."""
        i_cursor = app_js.index('id="streamingCursor"')
        i_status = app_js.index('id="streamingStatus"')
        i_meta = app_js.index('id="streamingMeta"')
        assert i_cursor < i_status < i_meta
        assert 'role="status" aria-live="polite"' in app_js

    def test_status_line_parts(self, app_js):
        for needle in ('id="streamingStatusText"', 'id="streamingStatusElapsed"', 'class="stream-status-spinner"',
                       'id="streamingStages"'):
            assert needle in app_js, needle

    def test_processing_chips_migrate_instead_of_vanish(self, app_js):
        """예전에는 SSE 연결 직후 칩을 지웠다(plans/89 §0 ③). 이제 생성 → 이관 → 제거 순서다."""
        assert app_js.count("createStreamingMessage();   // 칩 상태 이관을 위해 처리 말풍선보다 먼저 만든다(plans/89)\n            removeProcessingMessage();") == 2
        assert 'document.querySelectorAll("#processingStages .stage")' in app_js
        assert "function ensureStageChip(stage)" in app_js

    def test_update_processing_stage_targets_streaming_chips_first(self, app_js):
        body = app_js[app_js.index("function updateProcessingStage(node, status)"):]
        body = body[:body.index("function stopStageAnimation")]
        assert "ensureStageChip(stage)" in body
        assert "setStreamStatusText(msg)" in body
        # 폴백 경로(#processingMessage)의 문구 갱신도 유지
        assert 'getElementById("processingText")' in body

    def test_agent_stage_is_lazy_not_default(self, app_js):
        """3·4단 UI 불변: 기본 칩 5개는 그대로, agent 칩은 stageOrder에만 있고 지연 생성된다."""
        assert 'var stages = ["parse", "schema", "sql", "exec", "result"];' in app_js
        assert 'var stageOrder = ["parse", "agent", "schema", "sql", "exec", "result"];' in app_js
        assert 'deep_agent: "agent", intent_planner: "agent", agent_orchestrator: "agent",' in app_js


class TestStateMachine:
    def test_sse_loops_handle_progress_and_heartbeat_symmetrically(self, app_js):
        """두 SSE 루프(텍스트·파일)에 같은 분기가 있다 — 경로 대칭."""
        assert app_js.count('} else if (event.type === "progress") {') == 2
        assert app_js.count('} else if (event.type === "heartbeat") {') == 2
        assert app_js.count("noteStreamActivity();   // plans/89") == 2
        assert app_js.count("markStreamTokens();") == 2

    def test_stall_threshold_and_recovery(self, app_js):
        assert "var STREAM_STALL_MS = 15000;" in app_js
        assert 'box.classList.add("stalled")' in app_js
        assert 'box.classList.remove("stalled")' in app_js
        assert "서버 신호 대기 중 · 마지막 신호" in app_js

    def test_elapsed_timer_is_client_side_and_cleared_in_finally(self, app_js):
        assert "setInterval(tickStreamStatus, 1000)" in app_js
        assert app_js.count("stopStreamStatusTimers();   // plans/89") == 2
        assert "clearInterval(_streamStatus.timer)" in app_js

    def test_terminal_transitions(self, app_js):
        fin = app_js[app_js.index("function finalizeStreamingMessage(text, meta)"):][:200]
        assert 'endStreamStatus("done", meta)' in fin
        intr = app_js[app_js.index("function markStreamInterrupted()"):][:200]
        assert 'endStreamStatus("interrupted")' in intr
        # 후속 스트림과의 ID 충돌 방지 목록에 상태 영역 ID가 포함된다(두 곳)
        assert app_js.count('"streamingStatus", "streamingStatusText", "streamingStatusElapsed", "streamingStages"]') == 2

    def test_done_folds_into_one_line_summary(self, app_js):
        """G-2/G-5: 완료 후 한 줄 요약 + 단계 목록은 hidden(클릭 펼침)."""
        body = app_js[app_js.index("function endStreamStatus(outcome, meta)"):]
        body = body[:body.index("function appendPipelineSubStep")]
        assert 'doneEl.className = "stream-status-done"' in body
        assert "tasks.hidden = true" in body
        assert 'doneEl.setAttribute("aria-expanded"' in body
        assert 'if (outcome !== "done" || !st) { box.remove(); return; }' in body


class TestLabels:
    def test_tool_labels_cover_server_tool_names(self, app_js):
        """서버 `_TOOL_NAMES` 7종 전부 + 미지 이름 폴백."""
        from src.orchestration.deepagents_tools import _TOOL_NAMES

        for tool_name in _TOOL_NAMES.values():
            assert re.search(rf"\b{tool_name}: \"", app_js), tool_name
        assert '("도구 실행: " + name)' in app_js

    def test_agent_labels_extended(self, app_js):
        for needle in ('process_query: "프로세스 조회"', 'host_inspect: "호스트 점검"', 'fault_diagnosis: "장애 진단"'):
            assert needle in app_js, needle

    def test_node_labels_cover_ladder_whitelist_additions(self, app_js):
        """plans/89가 화이트리스트에 더한 노드는 라벨도 함께 들어간다(서버·클라 대칭).
        기존 누락(context_resolver 등)은 이 계획 소관이 아니라 검사하지 않는다."""
        from src.api.routes.query import _STREAM_KNOWN_NODES

        labels = app_js[app_js.index("var nodeLabels = {"):]
        labels = labels[:labels.index("};")]
        for node in ("deep_agent", "fault_diagnosis", "cache_management"):
            assert node in _STREAM_KNOWN_NODES
            assert re.search(rf"\b{node}:\s", labels), f"nodeLabels 누락: {node}"


class TestCompositeTasks:
    def test_task_list_uses_server_field_names(self, app_js):
        """plans/88 verdict 필드명 그대로 — 변환 계층 없음."""
        body = app_js[app_js.index("function renderStreamTasks()"):]
        body = body[:body.index("function endStreamStatus")]
        for f in ("t.row_count", "t.scope_size", "t.scope_col", "t.truncated_count", "t.status", "t.reason", "t.error"):
            assert f in body, f
        # reason이 있으면 상태값과 무관하게 건너뜀(88 R-A 대비)
        assert '(t.status === "skipped" || t.reason) ? "skipped"' in body

    def test_task_list_dom_only_on_task_events(self, app_js):
        """단일 DB 경로에는 task 이벤트가 없으므로 목록 DOM이 생기지 않는다."""
        assert app_js.count('list.className = "stream-status-tasks"') == 1
        assert '"stream-status-tasks"' not in app_js[app_js.index("function createStreamingMessage()"):app_js.index("function beginStreamStatus()")]

    def test_task_text_has_priority_over_tool_text(self, app_js):
        body = app_js[app_js.index("function handleProgressEvent(event)"):][:800]
        assert "!_streamStatus.activeTaskId" in body

    def test_panel_task_list_renders_skipped_reason_and_truncation(self, app_js):
        body = app_js[app_js.index("function renderTaskList(tasks)"):]
        body = body[:body.index("function renderSection") if "function renderSection" in body else 4000]
        assert 't.status === "skipped" || t.reason' in body
        assert "대 절단" in body and "t.scope_size" in body

    def test_panel_substeps_preserved_on_node_complete(self, app_js):
        body = app_js[app_js.index("function handleNodeComplete(event)"):][:1600]
        assert 'querySelector(".pipeline-substeps")' in body
        assert "if (subSteps) bodyEl.appendChild(subSteps);" in body


class TestCss:
    def test_rules_exist_and_reuse_tokens(self, style_css):
        for sel in (".stream-status {", ".stream-status.stalled .stream-status-text", ".stream-status-tasks {",
                    ".stream-status-done {", ".pipeline-substeps li"):
            assert sel in style_css, sel
        block = style_css[style_css.index("/* ─── Stream status"):]
        assert "#" not in re.sub(r"/\*.*?\*/", "", block, flags=re.S).replace("\\25B8", "").replace("\\25BE", ""), "신규 색 리터럴 금지 — 기존 토큰만"

    def test_reduced_motion_stops_spinner(self, style_css):
        block = style_css[style_css.index("/* ─── Stream status"):]
        assert "@media (prefers-reduced-motion: reduce)" in block
        assert ".stream-status-spinner" in block[block.index("prefers-reduced-motion"):]
