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


# ─── 레벨 필터 · 찾아보기 ───


def test_level_filter_chips_cover_backend_labels(index_html: str) -> None:
    """칩의 레벨 값은 백엔드 severity_label 정본과 같은 문자열이어야 한다.

    한쪽만 바뀌면 필터가 아무것도 못 거르는데, 화면상으로는 "그 레벨 알람이 없다"와
    구별되지 않는다(noise_gate/application/nodes/alarm_analyzer.py `_SEVERITY_LABELS`).
    """
    from noise_gate.application.nodes.alarm_analyzer import _SEVERITY_LABELS

    for label in list(_SEVERITY_LABELS.values()) + ["해소"]:
        assert f'data-severity="{label}"' in index_html, label
    assert 'data-severity=""' in index_html   # 전체


def test_alarm_filter_controls_exist(index_html: str) -> None:
    for element_id in ("alarmTools", "alarmSearch", "alarmFilterReset"):
        assert f'id="{element_id}"' in index_html, element_id


def test_filter_keys_are_stamped_on_card(app_js: str) -> None:
    """레벨·검색 키는 카드 렌더 시점에 심는다 — 필터가 원본 데이터를 다시 들추지 않는다."""
    render = app_js[app_js.index("function renderAlarmMessage("):]
    render = render[: render.index("\n    function ", 10)]
    assert "el.dataset.severity" in render
    assert "el.dataset.search = alarmSearchText(data)" in render


def test_filter_hides_cards_instead_of_removing(app_js: str) -> None:
    """필터는 감추기만 한다 — 지워 버리면 필터를 풀어도 알람이 돌아오지 않는다."""
    body = app_js[app_js.index("function updateAlarmViewState("):]
    body = body[: body.index("\n    function ", 10)]
    assert 'classList.toggle("alarm-card-hidden"' in body
    assert "alarmList.innerHTML" not in body   # 필터가 목록을 지우면 안 된다


def test_unread_badge_is_independent_of_filter(app_js: str) -> None:
    """미확인 배지는 필터와 무관하다 — 가려진 알람을 못 본 알람으로 만들면 안 된다."""
    render = app_js[app_js.index("function renderAlarmMessage("):]
    render = render[: render.index("\n    function ", 10)]
    unread = render[render.index('if (activeView !== "alarm") {'):]
    assert "alarmFilterSeverity" not in unread
    assert "alarm-card-hidden" not in unread


def test_filter_state_is_not_persisted(app_js: str) -> None:
    """필터를 저장하면 재방문 시 걸린 채로 남아 알람이 없는 것처럼 보인다."""
    state = app_js[app_js.index("var alarmFilterSeverity"):]
    state = state[: state.index("function alarmSearchText(")]
    assert "localStorage" not in state


def test_clear_all_resets_filter(app_js: str) -> None:
    """목록을 비우면 필터도 푼다 — 남겨 두면 다음 알람이 조용히 가려진다."""
    handler = app_js[app_js.index("alarmClearBtn.addEventListener"):]
    handler = handler[: handler.index("if (historyClearBtn)")]
    assert "resetAlarmFilter()" in handler


def test_hidden_card_rule_exists(style_css: str) -> None:
    rule = style_css[style_css.index(".alarm-card-hidden {"):]
    assert "display: none" in rule[: rule.index("}")]


# ─── 알람 → 질의 프롬프트 인계 (Plan 86 · D-192) ───


def test_prompt_axes_cover_mapped_resource_types(app_js: str) -> None:
    """축 매핑은 `resource_type`을 키로 쓴다 — alarm_name은 자유 한국어라 단독 키로 못 쓴다."""
    axes = app_js[app_js.index("var ALARM_PROMPT_AXES = {"):]
    axes = axes[: axes.index("\n    };")]
    for rtype in ("server.Cpus", "server.Memory", "server.Disks",
                  "server.FileSystems", "server.LogMonitor", "server.Server"):
        assert f'"{rtype}"' in axes, rtype


def test_network_axis_stays_out_until_verified(app_js: str) -> None:
    """`server.Network`는 골드셋에 동형이 없다 — 답변 가능성 실측(T7) 전에는 추천하지 않는다.

    검증 안 된 문구를 넣으면 첫 클릭이 LLM 폴백으로 새고, 그 사실이 화면에 드러나지 않는다.
    """
    axes = app_js[app_js.index("var ALARM_PROMPT_AXES = {"):]
    axes = axes[: axes.index("\n    };")]
    assert '"server.Network":' not in axes


def test_spike_prompt_carries_thresholds(app_js: str) -> None:
    """급증형 문구는 임계·차분을 담아야 급증 조립기가 결정적으로 진입한다.

    없으면 `query_generator._try_spike`가 "급증 조립 미진입" 후 LLM으로 폴백한다
    (`src/db_adapters/polestar/spike_sql.py` · `plans/86` §4.3).
    """
    axes = app_js[app_js.index("var ALARM_PROMPT_AXES = {"):]
    axes = axes[: axes.index("\n    };")]
    assert "10%p 이상 상승" in axes
    assert "80% 이상" in axes


def test_builder_is_pure(app_js: str) -> None:
    """생성기는 순수 함수다 — DOM·네트워크·경로별로 비는 필드에 기대지 않는다."""
    fn = app_js[app_js.index("function buildAlarmPrompts(data) {"):]
    fn = fn[: fn.index("\n    }")]
    for forbidden in ("document.", "fetch(", "history_stats", "process_snapshot"):
        assert forbidden not in fn, forbidden


def test_prompt_never_auto_sends(app_js: str) -> None:
    """★ G-2 확정: 확인 후 전송이다. 인계 함수가 스스로 전송하면 안 된다."""
    fn = app_js[app_js.index("function stageAlarmPrompt(text) {"):]
    fn = fn[: fn.index("\n    }")]
    assert "handleSend" not in fn
    assert "showPromptConfirm(text)" in fn


def test_send_goes_through_single_entry_point(app_js: str) -> None:
    """확인 바의 조회 버튼도 handleSend 단일 진입점을 탄다(D-183이 확인한 계약)."""
    handler = app_js[app_js.index("promptConfirmRun.addEventListener"):]
    handler = handler[: handler.index("if (promptConfirmEdit)")]
    assert "handleSend()" in handler
    assert "if (isProcessing) return;" in handler   # 진행 중 중복 전송 차단


def test_history_reuse_still_does_not_send(app_js: str) -> None:
    """D-183 회귀 방지 — 질의 이력 재사용은 여전히 채우기만 한다(확인 바도 붙지 않는다)."""
    fn = app_js[app_js.index("function reuseHistoryQuery(query) {"):]
    fn = fn[: fn.index("\n    }")]
    assert "handleSend" not in fn
    assert "showPromptConfirm" not in fn


def test_prompt_section_sits_above_feedback(app_js: str) -> None:
    """"조회할까" 다음에 "유용했나"가 온다 — 순서가 뒤집히면 피드백이 먼저 눈에 띈다."""
    render = app_js[app_js.index("function renderAlarmMessage("):]
    render = render[: render.index("\n    function ", 10)]
    assert render.index("promptHtml +") < render.index("feedbackHtml +")


def test_chip_text_not_in_html_attribute(app_js: str) -> None:
    """프롬프트 전문은 속성이 아니라 DOM으로 다룬다.

    전문에는 서버명(외부 데이터)이 들어가는데 `escapeHtml`은 따옴표를 이스케이프하지 않아
    속성값에 넣으면 마크업이 깨진다.
    """
    fn = app_js[app_js.index("function renderAlarmPromptSection(prompts, collapsed) {"):]
    fn = fn[: fn.index("\n    }")]
    assert "data-prompt" not in fn
    bind = app_js[app_js.index("function bindAlarmPrompts(el, prompts) {"):]
    bind = bind[: bind.index("\n    }\n\n    // 추천 질의를")]
    assert "chip.textContent = p.label" in bind
    assert "chip.title = p.text" in bind


def test_llm_suggest_called_only_when_deterministic_is_empty(app_js: str) -> None:
    """결정적 추천이 있으면 네트워크 요청 자체가 나가지 않는다 — 과금 경로를 좁힌다."""
    assert "if (!alarmPrompts.length) requestLlmPrompt(el, data);" in app_js


def test_llm_suggest_respects_capability_gate(app_js: str) -> None:
    """플래그가 꺼져 있으면 부르지 않는다 — 알람마다 503을 때리지 않게 한다."""
    fn = app_js[app_js.index("function requestLlmPrompt(el, data) {"):]
    fn = fn[: fn.index("\n    }")]
    assert "alarmCapabilities.prompt_suggest_enabled" in fn
    assert ".catch(" in fn   # 실패는 조용히 — 카드가 깨지지 않는다


def test_capabilities_expose_prompt_flag() -> None:
    """프론트 게이트가 읽는 필드가 실제 응답 모델에 있어야 한다(배선 실측)."""
    from src.api.routes.alarm import AlarmCapabilitiesResponse

    assert "prompt_suggest_enabled" in AlarmCapabilitiesResponse.model_fields


def test_prompt_confirm_markup(index_html: str) -> None:
    for element_id in ("promptConfirm", "promptConfirmRun", "promptConfirmEdit"):
        assert f'id="{element_id}"' in index_html, element_id


def test_prompt_styles_exist(style_css: str) -> None:
    for selector in (".alarm-prompt-chip {", ".prompt-confirm {", "button.alarm-prompt-trigger {"):
        assert selector in style_css, selector


# ─── 서빙 ───


def test_page_serves_with_alarm_view() -> None:
    client = TestClient(create_app())
    body = client.get("/").text
    assert 'id="alarmView"' in body
    assert 'data-view="alarm"' in body
