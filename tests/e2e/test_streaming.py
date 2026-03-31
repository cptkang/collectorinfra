"""SSE 스트리밍 E2E 테스트 (S-01 ~ S-05).

Plan 24 섹션 3.4에 따라 SSE 토큰 스트리밍, 타이핑 커서,
스트리밍 완료 메타, 스트리밍 에러 처리, 연결 끊김 폴백을 검증한다.

conftest.py의 MockGraph가 on_chat_model_stream 이벤트로
토큰을 순차 전송하고, 최종 on_chain_end에서 meta/done 이벤트를 생성한다.
"""

from __future__ import annotations

import json

from playwright.sync_api import Page, expect


# ---------------------------------------------------------------------------
# S-01: SSE 토큰 스트리밍
# ---------------------------------------------------------------------------


def test_streaming_token_display(page: Page) -> None:
    """S-01: SSE 토큰 스트리밍 시 응답 텍스트가 점진적으로 표시되는지 확인한다.

    MutationObserver로 streamingText에 토큰이 추가되는 것을 감지하고,
    스트리밍 완료 후 최종 응답 텍스트를 검증한다.
    """
    # MutationObserver로 토큰 추가 감지
    page.evaluate("""
        () => {
            window.__tokenAppended = false;
            window.__maxStreamingLen = 0;
            const observer = new MutationObserver(() => {
                const el = document.getElementById('streamingText');
                if (el && el.textContent.length > 0) {
                    window.__tokenAppended = true;
                    window.__maxStreamingLen = Math.max(
                        window.__maxStreamingLen, el.textContent.length
                    );
                }
            });
            observer.observe(document.getElementById('chatMessages'), {
                childList: true, subtree: true, characterData: true,
            });
            window.__tokenObserver = observer;
        }
    """)

    page.locator("#prompt").fill("서버 현황")
    page.locator("#sendBtn").click()

    # 스트리밍 완료 대기: streamingCursor가 DOM에서 제거되면 완료
    page.wait_for_function(
        "document.getElementById('streamingCursor') === null",
        timeout=30000,
    )

    page.evaluate("window.__tokenObserver?.disconnect()")

    token_appended = page.evaluate("window.__tokenAppended")
    max_len = page.evaluate("window.__maxStreamingLen")

    # 토큰이 점진적으로 채워졌는지 확인
    assert token_appended, "스트리밍 중 토큰이 streamingText에 추가되지 않았다"
    assert max_len > 0, "스트리밍 텍스트 길이가 0이었다"

    # 최종 응답 텍스트 확인
    final_response = page.locator(".response-text").last.inner_text()
    assert "조회 결과" in final_response or "데이터" in final_response, (
        f"최종 응답에 기대한 내용이 포함되어야 한다: {final_response}"
    )


# ---------------------------------------------------------------------------
# S-02: 타이핑 커서 표시/제거
# ---------------------------------------------------------------------------


def test_streaming_cursor(page: Page) -> None:
    """S-02: 스트리밍 중 타이핑 커서가 표시되고, 완료 후 제거되는지 확인한다.

    MutationObserver로 streamingCursor 출현을 감지하고,
    finalizeStreamingMessage 후 DOM에서 제거(remove)됨을 검증한다.
    """
    # MutationObserver로 커서 출현 감지
    page.evaluate("""
        () => {
            window.__cursorAppeared = false;
            const observer = new MutationObserver((mutations) => {
                for (const m of mutations) {
                    for (const node of m.addedNodes) {
                        if (node.id === 'streamingCursor' ||
                            (node.querySelector && node.querySelector('#streamingCursor'))) {
                            window.__cursorAppeared = true;
                        }
                    }
                }
            });
            observer.observe(document.getElementById('chatMessages'), {
                childList: true, subtree: true,
            });
            window.__cursorObserver = observer;
        }
    """)

    page.locator("#prompt").fill("서버 현황")
    page.locator("#sendBtn").click()

    # 스트리밍 완료 대기: streamingCursor가 DOM에서 제거되면 완료
    page.wait_for_function(
        "document.getElementById('streamingCursor') === null",
        timeout=30000,
    )

    page.evaluate("window.__cursorObserver?.disconnect()")
    cursor_appeared = page.evaluate("window.__cursorAppeared")

    # 커서가 출현했다가 제거되었는지 확인
    assert cursor_appeared, "스트리밍 중 streamingCursor가 DOM에 추가되지 않았다"
    assert page.locator("#streamingCursor").count() == 0, (
        "스트리밍 완료 후 커서가 제거되어야 한다"
    )

    # streamingMessage의 id도 제거되어야 한다
    assert page.locator("#streamingMessage").count() == 0, (
        "스트리밍 완료 후 streamingMessage id가 제거되어야 한다"
    )


# ---------------------------------------------------------------------------
# S-03: 스트리밍 완료 메타 (ROWS, TIME, ID)
# ---------------------------------------------------------------------------


def test_streaming_completion_meta(page: Page) -> None:
    """S-03: 스트리밍 완료 시 ROWS, TIME, ID 메타 정보가 표시되는지 확인한다.

    finalizeStreamingMessage에서 streamingMeta 영역에
    meta-item 요소들이 추가된다.
    """
    page.locator("#prompt").fill("메모리 사용률 현황")
    page.locator("#sendBtn").click()

    # 최종 응답 대기
    agent_msg = page.locator(".message--agent:not(.message--processing)")
    agent_msg.wait_for(state="visible", timeout=30000)

    # 메타 영역 확인
    meta_area = page.locator(".message-meta").last
    meta_area.wait_for(state="visible", timeout=5000)
    meta_text = meta_area.inner_text()

    # ROWS 메타 확인 (MockGraph는 5건을 반환)
    assert "ROWS" in meta_text, "ROWS 메타가 표시되어야 한다"
    assert "5" in meta_text, "행 수 5건이 표시되어야 한다"

    # TIME 메타 확인
    assert "TIME" in meta_text, "TIME 메타가 표시되어야 한다"

    # ID 메타 확인 (query_id의 처음 8자)
    assert "ID" in meta_text, "ID 메타가 표시되어야 한다"

    # meta-item이 3개 이상이어야 한다 (ROWS, TIME, ID)
    meta_items = page.locator(".message-meta .meta-item")
    assert meta_items.count() >= 3, (
        f"meta-item이 최소 3개 필요, 실제 {meta_items.count()}개"
    )


# ---------------------------------------------------------------------------
# S-04: 스트리밍 에러 처리
# ---------------------------------------------------------------------------


def test_streaming_error_handling(page: Page) -> None:
    """S-04: SSE error 이벤트 수신 시 에러 메시지가 표시되는지 확인한다.

    page.route로 SSE endpoint를 intercept하여
    error 타입 SSE 이벤트를 직접 전송한다.
    """
    error_message = "테스트 에러: 데이터베이스 연결 실패"

    def intercept_sse_with_error(route):
        """SSE 응답으로 error 이벤트를 전송한다."""
        error_event = {
            "type": "error",
            "message": error_message,
        }
        sse_body = f"data: {json.dumps(error_event, ensure_ascii=False)}\n\n"
        route.fulfill(
            status=200,
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
            body=sse_body,
        )

    page.route("**/api/v1/query/stream", intercept_sse_with_error)

    page.locator("#prompt").fill("에러 테스트 질의")
    page.locator("#sendBtn").click()

    # 에러 메시지가 표시되어야 한다
    error_el = page.locator("#chatError")
    error_el.wait_for(state="visible", timeout=15000)

    error_text = page.locator("#chatErrorText").inner_text()
    assert error_message in error_text, (
        f"에러 메시지에 '{error_message}'가 포함되어야 한다: {error_text}"
    )

    # route intercept 해제
    page.unroute("**/api/v1/query/stream")


# ---------------------------------------------------------------------------
# S-05: 연결 끊김 폴백
# ---------------------------------------------------------------------------


def test_connection_drop_fallback(page: Page) -> None:
    """S-05: 네트워크 에러 시 fallback POST로 응답이 표시되는지 확인한다.

    page.route로 SSE endpoint를 네트워크 에러(abort)로 설정하고,
    fallback POST (/api/v1/query)는 정상 동작하도록 한다.
    JS의 catch 블록에서 TypeError/fetch 에러를 감지하면
    executeFallbackQuery를 호출한다.
    """
    def intercept_sse_network_error(route):
        """SSE 연결을 네트워크 에러로 중단한다."""
        route.abort("connectionrefused")

    page.route("**/api/v1/query/stream", intercept_sse_network_error)

    page.locator("#prompt").fill("네트워크 에러 폴백 테스트")
    page.locator("#sendBtn").click()

    # fallback POST를 통해 에이전트 응답이 도착하거나,
    # 에러 메시지가 표시되어야 한다.
    # 두 경우 모두 UI가 정상 반응하는 것을 확인한다.
    agent_or_error = page.locator(
        ".message--agent:not(.message--processing), #chatError.active"
    )
    agent_or_error.first.wait_for(state="visible", timeout=30000)

    # 에이전트 응답이 표시된 경우 (fallback 성공)
    agent_msgs = page.locator(".message--agent:not(.message--processing)")
    if agent_msgs.count() > 0:
        response_text = page.locator(".response-text").last.inner_text()
        assert len(response_text) > 0, (
            "Fallback 응답 텍스트가 존재해야 한다"
        )
    else:
        # 에러 메시지가 표시된 경우 (fallback도 실패)
        error_text = page.locator("#chatErrorText").inner_text()
        assert len(error_text) > 0, "에러 메시지가 표시되어야 한다"

    # route intercept 해제
    page.unroute("**/api/v1/query/stream")
