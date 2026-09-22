"""존 그룹 진행 이벤트 — SSE 변환 · 실패 경위 · UI 정적 계약 (plans/82 v7 R-2·R-3 · D-249).

실행기(`multi_db_executor`)가 내는 custom event ``group``이 SSE ``progress{kind:"group"}``으로
바뀌고, 스트림 실패 경위에 존 그룹 단계가 남으며, 화면이 부분 결과 카드를 그렸다가 완료되면
접고 **중단·오류여도 남긴다**는 것을 고정한다. 브라우저 e2e는 `RUN_E2E=1` 옵트인이라 여기서는
계약이 되는 지점만 정적으로 본다(`test_ui_stream_status.py` 선례).
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from src.api.routes.query import _progress_sse_payload
from src.api.stream_failure import StreamTrace

_STATIC = Path(__file__).resolve().parents[2] / "src" / "static"


def _event(phase, **group):
    return {"event": "on_custom_event", "name": "group", "data": {"phase": phase, "group": group}}


class TestSsePayload:
    def test_group_event_becomes_group_progress(self):
        preview = {"columns": ["host"], "rows": [["h1"]], "truncated": 0}
        out = _progress_sse_payload(
            _event("end", group_key="polestar:bank", label="은행존", row_count=1, preview=preview),
            "multi_db_executor", time.time(),
        )
        assert out["type"] == "progress" and out["kind"] == "group" and out["phase"] == "end"
        assert out["node"] == "multi_db_executor"
        assert out["group"]["label"] == "은행존" and out["group"]["preview"] == preview

    def test_malformed_group_payload_is_emptied_not_crashing(self):
        out = _progress_sse_payload(
            {"event": "on_custom_event", "name": "group", "data": {"phase": "start", "group": "x"}},
            None, time.time(),
        )
        assert out["kind"] == "group" and out["group"] == {}

    def test_other_custom_events_unchanged(self):
        out = _progress_sse_payload(
            {"event": "on_custom_event", "name": "pipeline.execute", "data": {"phase": "start"}},
            None, time.time(),
        )
        assert out["kind"] == "step"


class TestFailureTrace:
    def test_timeout_during_common_zone_names_the_stage(self):
        """공동존 조회 중 시간 초과 — 은행존 완료·공동존 진행 중이 경위에 남는다."""
        trace = StreamTrace()
        trace.observe_progress({"kind": "group", "phase": "start", "timestamp_ms": 0,
                                "group": {"group_key": "polestar:bank", "label": "은행존"}})
        trace.observe_progress({"kind": "group", "phase": "end", "timestamp_ms": 3000,
                                "group": {"group_key": "polestar:bank", "label": "은행존", "row_count": 12}})
        trace.observe_progress({"kind": "group", "phase": "start", "timestamp_ms": 3000,
                                "group": {"group_key": "polestar:common", "label": "공동존"}})
        fields = trace.failure_fields(code="timeout", elapsed_ms=120000, limit_sec=120)
        assert [(s["label"], s["status"]) for s in fields["steps"]] == [("은행존", "done"), ("공동존", "running")]
        assert fields["stage"]["label"] == "공동존"

    def test_group_with_errors_and_no_rows_is_failed(self):
        trace = StreamTrace()
        trace.observe_progress({"kind": "group", "phase": "end", "timestamp_ms": 10,
                                "group": {"group_key": "polestar:common", "label": "공동존",
                                          "row_count": 0, "error_dbs": ["공동존 김포"]}})
        assert trace.failure_fields(code="error", elapsed_ms=10, limit_sec=None)["steps"][0]["status"] == "failed"


@pytest.fixture(scope="module")
def app_js() -> str:
    return (_STATIC / "js" / "app.js").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def style_css() -> str:
    return (_STATIC / "css" / "style.css").read_text(encoding="utf-8")


class TestUiContract:
    def test_group_kind_is_dispatched(self, app_js):
        assert 'event.kind === "group" && event.group' in app_js
        assert "function handleGroupProgress(event)" in app_js
        assert "function renderGroupPartial(g)" in app_js

    def test_partial_cells_are_escaped(self, app_js):
        """행 값은 DB에서 왔다 — innerHTML에 넣기 전에 반드시 이스케이프한다."""
        body = app_js[app_js.index("function renderGroupPartial(g)"):]
        body = body[:body.index("scrollToBottomIfSticky();")]
        assert 'escapeHtml(v == null ? "" : String(v))' in body
        assert "escapeHtml(String(c))" in body
        assert "escapeHtml(groupLabel(g))" in body

    def test_partials_fold_on_done_and_survive_errors(self, app_js):
        body = app_js[app_js.index("function endStreamStatus(outcome, meta)"):]
        body = body[:body.index("function appendPipelineSubStep")]
        assert 'box.querySelector(".stream-partials")' in body
        # 중단·오류: 영역은 지우되 부분 결과는 말풍선에 남긴다(Online Aggregation의 이득)
        assert "box.parentNode.insertBefore(partials, box)" in body
        # 완료: 단계 목록과 함께 요약 버튼 아래로 접는다(append-only — 종합 응답을 대신하지 않는다)
        assert "var folded = [tasks, partials].filter(Boolean);" in body

    def test_right_panel_keys_groups_separately(self, app_js):
        """그룹 이벤트는 이름이 모두 'group'이라 group_key로 행을 구분해야 한다."""
        assert "isGroup ? (event.group.group_key" in app_js

    def test_partials_css_does_not_override_hidden(self, style_css):
        """완료 후 hidden 속성으로 접힌다 — display를 지정하면 hidden이 무시된다."""
        rule = style_css[style_css.index(".stream-partials {"):]
        rule = rule[:rule.index("}")]
        assert "display" not in rule
