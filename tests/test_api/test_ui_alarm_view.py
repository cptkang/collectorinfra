"""이벤트 알람 뷰 분리 — 마크업·배선 회귀 (D-182).

알람이 질의응답 스트림으로 되돌아가는 회귀는 조용히 일어난다(삽입 대상 한 줄).
브라우저 e2e(playwright)는 이 환경에서 돌지 않으므로, 계약이 되는 지점만
정적으로 고정한다: ①탭이 둘 있다 ②알람은 알람 목록에만 들어간다
③알람 수신(SSE)은 탭과 무관하게 유지된다.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api.server import create_app

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


# ─── 마크업 ───


def test_two_view_tabs_exist(index_html: str) -> None:
    assert 'data-view="chat"' in index_html
    assert 'data-view="alarm"' in index_html
    assert 'id="alarmTabBadge"' in index_html


def test_alarm_view_container_exists(index_html: str) -> None:
    """알람 카드가 쌓일 목록과 빈 상태가 모두 있어야 한다."""
    for element_id in ("chatView", "alarmView", "alarmList", "alarmEmpty"):
        assert f'id="{element_id}"' in index_html, element_id


def test_alarm_view_hidden_until_authenticated(style_css: str) -> None:
    """인증 확정 전에는 뷰 탭·알람 뷰도 앱 셸과 함께 숨는다(FOUC 방지)."""
    gate = style_css[style_css.index("body.auth-pending > header"):]
    gate = gate[: gate.index("}")]
    assert ".view-tabs" in gate
    assert ".alarm-view" in gate


# ─── 배선 ───


def test_alarm_card_goes_to_alarm_list_not_chat(app_js: str) -> None:
    """알람은 알람 목록에만 들어간다 — 질의응답 스트림에 append하면 회귀."""
    render = app_js[app_js.index("function renderAlarmMessage("):]
    render = render[: render.index("\n    function ", 10)]
    assert "alarmList.insertBefore" in render          # 최신순(맨 위)
    assert "chatMessages.appendChild" not in render    # 채팅 스트림 오염 금지
    assert "chatWelcome" not in render                 # 알람이 대화 시작처럼 보이면 안 된다


def test_alarm_stream_is_not_gated_on_active_view(app_js: str) -> None:
    """수신은 어느 탭에 있든 유지된다 — 탭 전환이 SSE 연결을 끊으면 알람을 놓친다."""
    connect = app_js[app_js.index("function connectAlarmStream("):]
    connect = connect[: connect.index("\n    function ", 10)]
    assert "activeView" not in connect


def test_unread_badge_counts_only_while_away(app_js: str) -> None:
    """알람 뷰를 보고 있는 동안 도착한 알람은 미확인으로 세지 않는다."""
    assert 'if (activeView !== "alarm") {' in app_js
    assert "alarmUnreadCount = 0;" in app_js   # 알람 뷰 진입 시 리셋


def test_alarm_tab_hidden_without_permission(app_js: str) -> None:
    """수신 권한이 없으면 영영 비어 있을 탭을 노출하지 않는다."""
    assert "setAlarmTabVisible(alarmCanReceive)" in app_js


# ─── 레이아웃 ───


def test_body_height_accounts_for_tab_bar(style_css: str) -> None:
    """탭 바가 생긴 만큼 본문 높이에서 빼지 않으면 입력바가 화면 밖으로 밀린다."""
    expected = "calc(100vh - var(--header-h) - var(--view-tabs-h))"
    assert style_css.count(expected) == 2   # .chat-layout · .alarm-view
    assert "--header-h: 64px;" in style_css
    assert "--view-tabs-h: 44px;" in style_css


# ─── 서빙 ───


def test_page_serves_with_alarm_view() -> None:
    client = TestClient(create_app())
    body = client.get("/").text
    assert 'id="alarmView"' in body
    assert 'data-view="alarm"' in body
