"""질의 프롬프트 이력 UI — 마크업·배선 회귀 (D-183, 사이드바 개정).

이력이 조용히 깨지는 방식은 셋이다: 사이드바가 빠지거나, 기록 지점이 흩어지거나,
목록 클릭이 즉시 전송으로 바뀌거나. 브라우저 e2e가 이 환경에서 돌지 않으므로
(playwright 미설치) 계약이 되는 지점만 정적으로 고정한다 — D-182의
`test_ui_alarm_view.py`와 같은 방식이다.
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


# ─── 마크업 ───


def test_history_sidebar_exists(index_html: str) -> None:
    assert 'id="historyPanel"' in index_html
    assert 'id="historyToggle"' in index_html
    assert 'id="historyList"' in index_html
    assert 'id="historySearch"' in index_html


def test_history_lives_inside_chat_layout(index_html: str) -> None:
    """사이드바는 채팅 레이아웃의 한 열이다 — 밖으로 나가면 grid가 깨진다.

    인증 게이트(`body.auth-pending > .chat-layout`)도 여기에 들어 있어야 걸린다.
    """
    layout = index_html.split('<div class="chat-layout" id="chatView">')[1]
    layout = layout.split('<!-- 이벤트 알람 뷰')[0]
    assert 'id="historyPanel"' in layout


def test_exactly_two_view_tabs(index_html: str) -> None:
    """이력이 사이드바가 되면서 탭은 둘로 돌아왔다."""
    views = re.findall(r'class="view-tab[^"]*"\s+data-view="(\w+)"', index_html)
    assert sorted(views) == ["alarm", "chat"], views


def test_sidebar_collapse_states_cover_both_panels(style_css: str) -> None:
    """왼쪽·오른쪽 패널은 독립적으로 접힌다 — 조합 넷이 모두 정의돼야 한다.

    하나라도 빠지면 그 조합에서 grid 열 수가 어긋나 레이아웃이 무너진다.
    """
    for selector in (
        ".chat-layout.panel-collapsed {",
        ".chat-layout.history-collapsed {",
        ".chat-layout.history-collapsed.panel-collapsed {",
    ):
        assert selector in style_css, selector


# ─── 저장소 계약 ───


def test_history_storage_constants(app_js: str) -> None:
    assert 'var HISTORY_KEY = "query_prompt_history"' in app_js
    match = re.search(r"var HISTORY_MAX = (\d+)", app_js)
    assert match, "HISTORY_MAX 상한이 없다 — 상한 없는 누적은 금지"
    assert 0 < int(match.group(1)) <= 1000


def test_storage_access_is_guarded(app_js: str) -> None:
    """localStorage는 사생활 모드·용량 초과에서 던진다.

    목록이 비는 것은 허용해도 그 때문에 질의 전송이 막히는 것은 허용하지 않는다.
    """
    for fn in ("function loadHistory()", "function saveHistory(items)"):
        body = app_js.split(fn)[1].split("\n    }")[0]
        assert "try {" in body and "catch" in body, fn


def test_history_recorded_at_single_send_site(app_js: str) -> None:
    """기록 지점은 handleSend 하나다 — 흩어지면 경로마다 빠뜨린다."""
    # 세미콜론까지 봐야 정의(`function pushHistory(query) {`)와 호출이 구분된다
    assert app_js.count("pushHistory(query);") == 1
    send_body = app_js.split("function handleSend()")[1].split("\n    function ")[0]
    assert "pushHistory(query);" in send_body


def test_reuse_does_not_send(app_js: str) -> None:
    """목록에서 고른 질의는 입력창에 채우기만 한다.

    옛 질의가 지금도 유효하다는 보장이 없고(존 개명·서버 폐기), 오전송은 되돌릴 수 없다.
    """
    body = app_js.split("function reuseHistoryQuery(query)")[1].split("\n    }")[0]
    assert "promptEl.value = query" in body
    for forbidden in ("handleSend(", "fetch(", "sendBtn.click"):
        assert forbidden not in body, f"재사용 경로에 전송 호출({forbidden})이 있다"


# ─── 뷰 전환 ───


def test_set_active_view_uses_registry(app_js: str) -> None:
    """뷰를 하드코딩 toggle하면 뷰가 늘 때마다 하나씩 안 숨는 회귀가 난다."""
    body = app_js.split("function setActiveView(view)")[1].split("\n    }\n")[0]
    assert "viewRegistry()" in body
    assert "isAlarm" not in body


def test_history_is_not_a_view(app_js: str) -> None:
    """사이드바는 본문을 교체하지 않는다 — 등록표에 들어가면 채팅과 배타가 된다."""
    body = app_js.split("function viewRegistry()")[1].split("\n    }")[0]
    assert "history" not in body


def test_panel_state_is_remembered_and_defaults_closed(app_js: str) -> None:
    """기본 접힘이라 첫 방문 화면이 종전과 같고, 편 사람은 계속 펼쳐진 채로 온다."""
    assert 'var HISTORY_PANEL_KEY = "query_history_panel_open"' in app_js
    body = app_js.split("function isHistoryPanelOpen()")[1].split("\n    }")[0]
    assert "try {" in body and "catch" in body
    assert "return false" in body, "저장소가 막힌 환경의 기본값은 접힘이어야 한다"


def test_scroll_restore_only_on_chat(app_js: str) -> None:
    """복원 분기가 else면 이력 탭으로 *갈* 때도 돌아 엉뚱한 곳을 만진다."""
    body = app_js.split("function setActiveView(view)")[1].split("\n    }\n")[0]
    assert 'view === "chat" && stickToBottom' in body


# ─── 서빙 ───
#
# "index.html이 실제로 서빙되는가"는 test_ui_alarm_view.py::test_page_serves_with_alarm_view가
# 이미 검증한다(같은 파일이다). 여기에 하나 더 두면 한 세션에서 앱 lifespan이 한 번 더 뜨고,
# 종료 정리(풀 close·백그라운드 태스크 취소) 경합으로 간헐 실패한다 — 실패 지점이 단언이 아니라
# TestClient.__exit__이라 신호도 아니다. 마크업 존재는 위 test_history_tab_and_view_exist가 본다.
