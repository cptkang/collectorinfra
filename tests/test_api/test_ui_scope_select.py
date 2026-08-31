"""범위 사전 선택 UI — 마크업·배선 회귀 (Plan 82 W65-T7 · D-176 후속4).

브라우저 e2e(playwright)는 이 환경에서 돌지 않으므로, **계약이 되는 지점만 정적으로
고정**한다(`test_ui_alarm_view.py` 선례):
  ① `default: true` 옵션이 미리 선택된다 — 답하지 않아도 진행 가능해야 한다(U10)
  ② `skippable`이면 **건너뛰기 버튼**이 렌더된다
  ③ 기존 `zone_select`(모호성 해소) 동작은 **바뀌지 않는다**
"""

from __future__ import annotations

from pathlib import Path

import pytest

_STATIC = Path(__file__).resolve().parents[2] / "src" / "static"


@pytest.fixture(scope="module")
def app_js() -> str:
    return (_STATIC / "js" / "app.js").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def style_css() -> str:
    return (_STATIC / "css" / "style.css").read_text(encoding="utf-8")


class TestDefaultSelection:
    def test_default_option_is_prechecked(self, app_js):
        """★ "전체 조회"가 미리 선택돼 있어야 그냥 확인만 눌러도 진행된다."""
        assert '(o.default ? " checked" : "")' in app_js

    def test_confirm_enabled_when_a_default_exists(self, app_js):
        assert "var anyDefault = options.some(" in app_js
        assert '(anyDefault ? "" : " disabled")' in app_js


class TestSkip:
    def test_skip_button_rendered_when_skippable(self, app_js):
        assert "clar.skippable" in app_js
        assert "건너뛰고 전체 조회" in app_js

    def test_skip_sends_every_db_id(self, app_js):
        """건너뛰기는 **전체 조회**다 — 아무것도 안 보내면 질문이 진행을 막은 것이 된다."""
        assert 'c.getAttribute("data-key") === "__all__"' in app_js
        assert "executeStreamingQuery(clar.original_query" in app_js

    def test_skip_button_has_weaker_visual_weight(self, style_css):
        assert ".zone-clarify-skip {" in style_css
        assert ".zone-clarify-skip:disabled {" in style_css


class TestScopeShape:
    def test_group_db_ids_are_expanded_on_confirm(self, app_js):
        """scope_select 옵션은 그룹의 db_ids CSV라 확인 시 펼쳐야 한다."""
        assert 'c.value ? c.value.split(",") : []' in app_js

    def test_all_option_is_exclusive_with_groups(self, app_js):
        """"전체 조회"와 개별 그룹을 함께 고르면 무엇을 고른 건지 알 수 없다."""
        assert "if (isScope && c.checked)" in app_js

    def test_confirm_label_differs_by_kind(self, app_js):
        assert '"선택한 범위로 조회"' in app_js
        assert '"선택한 존으로 조회"' in app_js


class TestZoneSelectUnchanged:
    def test_group_exclusive_radio_behaviour_kept(self, app_js):
        """D-143 후속3 — 은행존/공동존 라디오 동작은 그대로다."""
        assert "if (clar.group_exclusive && c.checked)" in app_js
        assert 'x.getAttribute("data-group") !== g' in app_js

    def test_file_path_resend_kept(self, app_js):
        """폼필 경로 재전송(Plan 75 §4)은 건드리지 않았다."""
        assert "if (clar.has_file && lastUploadedFile)" in app_js
        assert "executeFileQuery(clar.original_query" in app_js

    def test_single_db_id_option_still_supported(self, app_js):
        """zone_select는 db_id 단일 값이다 — 두 형태를 함께 받는다."""
        assert "(o.db_ids && o.db_ids.length) ? o.db_ids.join(\",\") : (o.db_id || \"\")" in app_js


class TestReexpandPanelRendered:
    def test_scope_reexpand_is_attached_on_every_result_path(self, app_js):
        """★ 4개 응답 경로(SSE meta·JSON, 텍스트·파일) 전부에 붙어야 한다.

        한 경로만 빠지면 그 경로에서는 좁힌 사실만 남고 되돌릴 길이 사라진다 —
        경로 비대칭은 이 저장소에서 반복된 회귀 형태다(D-066).
        """
        assert app_js.count("appendZoneClarificationToLastBubble(metaData.scope_reexpand)") == 2
        assert app_js.count("appendZoneClarificationToLastBubble(data.scope_reexpand)") == 2

    def test_reexpand_reuses_the_existing_renderer(self, app_js):
        """새 렌더러를 만들지 않는다 — 재확장도 '고르고 재전송'이라 같은 상호작용이다."""
        assert "function appendZoneClarificationToLastBubble(clar)" in app_js
