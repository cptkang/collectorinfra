"""스코프 칩 UI — 마크업·배선 회귀 (plans/90 · D-205 · SPEC-scope-chip-ui).

브라우저 e2e(playwright)는 이 환경에서 돌지 않으므로 **계약이 되는 지점만 정적으로 고정**한다
(`test_ui_scope_select.py` 선례):
  ① 칩은 서버 db_scope의 거울이다 — 응답 4경로 모두에서 renderDbScopeChip을 부른다
  ② 선택은 다음 질의 1건에만 selected_db_ids로 실린다(매 턴 하드 핀 아님 — G-2)
  ③ 해제는 텍스트 2경로에 reset_db_scope로 실린다(파일 턴은 원래 승계하지 않는다 — SPEC Open Q2)
  ④ 존 라벨 리터럴을 프론트에 두지 않는다 · 라디오 규칙(D-143 후속3)은 그대로다
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_STATIC = Path(__file__).resolve().parents[2] / "src" / "static"


@pytest.fixture(scope="module")
def index_html() -> str:
    return (_STATIC / "index.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def app_js() -> str:
    return (_STATIC / "js" / "app.js").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def style_css() -> str:
    return (_STATIC / "css" / "style.css").read_text(encoding="utf-8")


class TestMarkup:
    def test_chip_sits_above_input_row(self, index_html):
        chip = index_html.index('id="dbScopeChip"')
        assert index_html.index('id="promptConfirm"') < chip < index_html.index('<div class="input-row">')

    def test_chip_parts(self, index_html):
        for id_ in ("dbScopePick", "dbScopeText", "dbScopeSol", "dbScopeClear", "dbScopePopover"):
            assert f'id="{id_}"' in index_html

    def test_cache_busting_bumped(self, index_html):
        # 이 변경이 올린 v=10 이상이면 된다 — 뒤의 변경이 또 올려도 깨지지 않게 하한으로 본다(D-248)
        m = re.search(r"app\.js\?v=(\d+)", index_html)
        assert m and int(m.group(1)) >= 10


class TestMirror:
    def test_five_states(self, app_js):
        for s in ('return "pending-reset"', 'return "pending"', 'return "none"',
                  'currentDbScope.source === "selected" ? "selected" : "inherited"'):
            assert s in app_js

    def test_rendered_on_all_four_response_paths(self, app_js):
        assert app_js.count("renderDbScopeChip(metaData.db_scope)") == 2   # 텍스트 SSE · 파일 SSE
        assert app_js.count("renderDbScopeChip(data.db_scope)") == 2       # JSON 폴백 2곳

    def test_clarification_keeps_previous_value(self, app_js):
        assert "if (scope === undefined || scope === null) { updateDbScopeChip(); return; }" in app_js

    def test_labels_come_from_server_only(self, app_js):
        """프론트에 존 라벨 리터럴을 두지 않는다 — 서버 db_scope/scope-options의 라벨만."""
        assert "currentDbScope.zone_groups" in app_js
        assert "공동존 김포" not in app_js.split("// ─── DB Scope Chip")[1].split("// ─── Zone Clarification")[0]


class TestSend:
    def test_pending_is_consumed_once_in_handle_send(self, app_js):
        assert "var sendDbIds = pendingDbIds;" in app_js
        assert "pendingDbIds = null;\n        pendingReset = false;" in app_js
        assert "executeStreamingQuery(query, sendDbIds, undefined, undefined, undefined, sendReset)" in app_js
        assert "executeFileQuery(query, selectedFile, sendDbIds)" in app_js

    def test_reset_rides_on_text_routes_only(self, app_js):
        assert "streamBody.reset_db_scope = true;" in app_js
        assert "queryBody.reset_db_scope = true;" in app_js
        assert 'formData.append("reset_db_scope"' not in app_js

    def test_no_per_turn_hard_pin(self, app_js):
        """G-2: 칩이 켜져 있다고 매 턴 selected_db_ids를 자동 재전송하지 않는다."""
        assert "streamBody.selected_db_ids = currentDbScope" not in app_js


class TestPopover:
    def test_options_from_scope_endpoint(self, app_js):
        assert 'fetch("/api/v1/scope/options"' in app_js

    def test_group_exclusive_radio_kept(self, app_js):
        assert "if (axis.exclusive && c.checked)" in app_js

    def test_reselect_cancels_reset(self, app_js):
        assert "pendingDbIds = ids;\n            pendingReset = false;" in app_js

    def test_clear_sets_pending_reset(self, app_js):
        assert "pendingReset = true;\n            pendingDbIds = null;" in app_js


class TestStyle:
    def test_state_styles(self, style_css):
        for s in ("inherited", "selected", "pending", "pending-reset"):
            assert f'.db-scope-chip[data-state="{s}"]' in style_css
        assert ".db-scope-popover {" in style_css


class TestMixedZones:
    """D-206: 은행존+공동존 동시 스코프의 칩 표기·팝오버 안내·처리현황 순차 경과."""

    def test_chip_joins_group_labels_in_order(self, app_js):
        assert "currentDbScope.zone_groups" in app_js
        assert 'labels.join(" + ")' in app_js

    def test_popover_explains_sequential_query_when_not_exclusive(self, app_js):
        assert "은행존을 먼저 조회한 뒤 공동존을 조회합니다" in app_js
        assert "(axis.exclusive ? \"\" :" in app_js

    def test_task_list_renders_group_progress_in_order(self, app_js):
        assert "t.group_results" in app_js
        assert "실행 그룹(순차): " in app_js
        assert 'groupParts.join(" → ")' in app_js
