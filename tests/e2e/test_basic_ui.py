"""기본 UI 테스트 (B-01 ~ B-10).

Plan 24 섹션 3.1에 정의된 기본 UI 인터랙션 시나리오를 검증한다.
conftest.py의 page fixture를 사용하여 테스트 서버에 접속한다.
"""

from __future__ import annotations

import re

from playwright.sync_api import Page, expect


# ---------------------------------------------------------------------------
# B-01: 페이지 로드
# ---------------------------------------------------------------------------


def test_page_load(page: Page) -> None:
    """B-01: 페이지가 정상 로드되는지 확인한다.

    타이틀, 헤더 텍스트, ONLINE 상태 배지를 검증한다.
    """
    # 타이틀 확인
    assert page.title() == "인프라 데이터 조회 에이전트"

    # 헤더 h1 텍스트 확인
    h1 = page.locator("h1")
    expect(h1).to_have_text("INFRA QUERY AGENT")

    # ONLINE 배지 표시 확인
    badge = page.locator(".status-badge.status-badge--online")
    expect(badge).to_be_visible()
    expect(badge).to_have_text("ONLINE")


# ---------------------------------------------------------------------------
# B-02: Welcome 화면 표시
# ---------------------------------------------------------------------------


def test_welcome_screen(page: Page) -> None:
    """B-02: Welcome 화면이 표시되는지 확인한다.

    chatWelcome 영역이 보이고, 힌트 버튼이 4개 존재하는지 검증한다.
    """
    welcome = page.locator("#chatWelcome")
    expect(welcome).to_be_visible()

    # hidden 클래스가 없어야 한다
    assert "hidden" not in (welcome.get_attribute("class") or "")

    # 힌트 버튼 4개
    hints = page.locator(".chat-welcome-hint")
    expect(hints).to_have_count(4)


# ---------------------------------------------------------------------------
# B-03: 힌트 버튼 클릭
# ---------------------------------------------------------------------------


def test_hint_button_fills_input(page: Page) -> None:
    """B-03: 힌트 버튼 클릭 시 textarea에 질의 텍스트가 채워지는지 확인한다."""
    first_hint = page.locator(".chat-welcome-hint").first
    expected_query = first_hint.get_attribute("data-query")

    first_hint.click()

    textarea = page.locator("#prompt")
    assert textarea.input_value() == expected_query
    assert textarea.input_value() != ""


# ---------------------------------------------------------------------------
# B-04: 빈 입력 전송 차단
# ---------------------------------------------------------------------------


def test_empty_submit_shows_error(page: Page) -> None:
    """B-04: 빈 입력 전송 시 에러 메시지가 표시되는지 확인한다.

    sendBtn 클릭 후 chatError에 active 클래스가 추가되고,
    에러 텍스트에 "질의를 입력해주세요"가 포함되는지 검증한다.
    """
    # textarea가 비어 있는 상태에서 전송
    page.locator("#prompt").fill("")
    page.locator("#sendBtn").click()

    # chatError에 active 클래스가 추가되었는지 확인
    chat_error = page.locator("#chatError")
    expect(chat_error).to_have_class(re.compile(r"active"))

    # 에러 메시지 텍스트 확인
    error_text = page.locator("#chatErrorText")
    expect(error_text).to_contain_text("질의를 입력해주세요")


# ---------------------------------------------------------------------------
# B-05: 텍스트 입력 및 전송
# ---------------------------------------------------------------------------


def test_send_message(page: Page) -> None:
    """B-05: 메시지 전송 시 사용자 버블이 생성되고 Welcome이 숨겨지는지 확인한다."""
    page.locator("#prompt").fill("테스트 질의")
    page.locator("#sendBtn").click()

    # 사용자 메시지 버블이 생성되었는지 확인
    user_message = page.locator(".message.message--user")
    expect(user_message.first).to_be_visible()

    # Welcome에 hidden 클래스가 추가되었는지 확인
    welcome = page.locator("#chatWelcome")
    page.wait_for_function(
        "document.getElementById('chatWelcome').classList.contains('hidden')",
        timeout=3000,
    )
    assert "hidden" in (welcome.get_attribute("class") or "")


# ---------------------------------------------------------------------------
# B-06: Enter 키 전송 / Shift+Enter 개행
# ---------------------------------------------------------------------------


def test_enter_sends_shift_enter_newline(page: Page) -> None:
    """B-06: Enter=전송, Shift+Enter=개행을 확인한다."""
    textarea = page.locator("#prompt")

    # Shift+Enter로 개행 입력
    textarea.fill("첫 줄")
    textarea.press("Shift+Enter")
    textarea.type("둘째 줄")

    # textarea에 개행 문자가 포함되어야 한다
    value = textarea.input_value()
    assert "\n" in value, f"개행 문자가 없습니다: {value!r}"

    # Enter로 전송
    textarea.press("Enter")

    # 전송되어 사용자 메시지 버블이 생성되어야 한다
    user_message = page.locator(".message.message--user")
    expect(user_message.first).to_be_visible()

    # 전송 후 textarea가 비워져야 한다
    assert textarea.input_value() == ""


# ---------------------------------------------------------------------------
# B-07: 처리 중 버튼 비활성화
# ---------------------------------------------------------------------------


def test_send_disables_button(page: Page) -> None:
    """B-07: 전송 후 sendBtn이 비활성화되는지 확인한다.

    handleSend 진입 직후 isProcessing=true, sendBtn.disabled=true가
    되는 것을 확인한다.
    """
    textarea = page.locator("#prompt")
    textarea.fill("처리 중 테스트 질의")

    send_btn = page.locator("#sendBtn")

    # 전송 전에는 활성화 상태
    assert send_btn.is_enabled()

    # 전송 버튼 클릭
    send_btn.click()

    # 전송 직후 disabled 상태 확인
    # executeStreamingQuery에서 isProcessing=true, sendBtn.disabled=true가 된다.
    page.wait_for_function(
        "document.getElementById('sendBtn').disabled === true",
        timeout=3000,
    )
    assert send_btn.is_disabled()


# ---------------------------------------------------------------------------
# B-08: 에러 메시지 자동 숨김
# ---------------------------------------------------------------------------


def test_error_auto_dismiss(page: Page) -> None:
    """B-08: 에러 표시 후 8초 이내에 active 클래스가 자동 제거되는지 확인한다.

    빈 입력으로 에러를 발생시킨 후, setTimeout(8000)에 의해
    chatError의 active 클래스가 제거되는 것을 wait_for_function으로 대기한다.
    """
    # 빈 입력으로 에러 발생
    page.locator("#prompt").fill("")
    page.locator("#sendBtn").click()

    # active 클래스가 추가되었는지 확인
    chat_error = page.locator("#chatError")
    expect(chat_error).to_have_class(re.compile(r"active"))

    # 8초 후 active 클래스가 제거될 때까지 대기 (여유분 포함 10초)
    page.wait_for_function(
        "!document.getElementById('chatError').classList.contains('active')",
        timeout=10000,
    )

    # active 클래스가 제거되었는지 최종 확인
    classes = chat_error.get_attribute("class") or ""
    assert "active" not in classes


# ---------------------------------------------------------------------------
# B-09: Progress Panel 토글
# ---------------------------------------------------------------------------


def test_progress_panel_toggle(page: Page) -> None:
    """B-09: Progress Panel 토글 버튼으로 패널을 접고 펼 수 있는지 확인한다.

    panelToggle 클릭 시 chat-layout에 panel-collapsed 클래스가 토글된다.
    """
    layout = page.locator(".chat-layout")
    toggle_btn = page.locator("#panelToggle")

    # 초기 상태: panel-collapsed 없음
    classes_before = layout.get_attribute("class") or ""
    assert "panel-collapsed" not in classes_before

    # 첫 번째 클릭: 패널 접기
    toggle_btn.click()
    page.wait_for_function(
        "document.querySelector('.chat-layout').classList.contains('panel-collapsed')",
        timeout=3000,
    )
    classes_collapsed = layout.get_attribute("class") or ""
    assert "panel-collapsed" in classes_collapsed

    # 두 번째 클릭: 패널 펼치기
    toggle_btn.click()
    page.wait_for_function(
        "!document.querySelector('.chat-layout').classList.contains('panel-collapsed')",
        timeout=3000,
    )
    classes_expanded = layout.get_attribute("class") or ""
    assert "panel-collapsed" not in classes_expanded


# ---------------------------------------------------------------------------
# B-10: 반응형 레이아웃
# ---------------------------------------------------------------------------


def test_responsive_hides_panel(page: Page) -> None:
    """B-10: 640px 이하 viewport에서 Progress Panel이 숨겨지는지 확인한다.

    CSS 미디어 쿼리 @media (max-width: 640px)에 의해
    .progress-panel { display: none } 규칙이 적용되는 것을 확인한다.
    """
    # 넓은 화면에서는 패널이 보여야 한다
    page.set_viewport_size({"width": 1280, "height": 720})
    # viewport 변경 후 레이아웃 재계산 대기
    page.wait_for_timeout(300)

    panel = page.locator("#progressPanel")
    expect(panel).to_be_visible()

    # 640px 이하로 줄이면 패널이 숨겨져야 한다
    page.set_viewport_size({"width": 640, "height": 480})
    page.wait_for_timeout(300)

    expect(panel).to_be_hidden()

    # 원래 크기로 복원
    page.set_viewport_size({"width": 1280, "height": 720})
    page.wait_for_timeout(300)

    expect(panel).to_be_visible()
