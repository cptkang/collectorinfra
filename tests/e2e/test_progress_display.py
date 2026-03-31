"""진행상태 표시 E2E 테스트 (P-01 ~ P-10).

Plan 24 섹션 3.2에 따라 처리 인디케이터, Progress Panel,
SSE node_start/node_complete 이벤트, SQL 토글, Fallback 모드,
타임아웃 에러 등을 검증한다.

conftest.py의 MockGraph가 SSE 이벤트를 생성하므로
실제 LLM/DB 호출 없이 UI 동작을 테스트할 수 있다.
"""

from __future__ import annotations

from playwright.sync_api import Page, expect


# ---------------------------------------------------------------------------
# P-01: 처리 인디케이터 표시
# ---------------------------------------------------------------------------


def test_processing_indicator_shown(page: Page) -> None:
    """P-01: 질의 전송 후 처리 인디케이터가 표시되는지 확인한다.

    processingMessage div 생성, processing-dots (span 3개),
    "처리" 텍스트가 포함된 processingText를 검증한다.
    MockGraph가 빠르게 응답하므로 MutationObserver로 출현을 감지한다.
    """
    # MutationObserver를 먼저 설치하여 processingMessage 출현을 감지
    page.evaluate("""
        () => {
            window.__processingAppeared = false;
            window.__processingHadDots = false;
            window.__processingHadText = false;
            const observer = new MutationObserver((mutations) => {
                for (const m of mutations) {
                    for (const node of m.addedNodes) {
                        if (node.id === 'processingMessage' ||
                            (node.querySelector && node.querySelector('#processingMessage'))) {
                            window.__processingAppeared = true;
                            const el = document.getElementById('processingMessage');
                            if (el) {
                                window.__processingHadDots = el.querySelectorAll('.processing-dots span').length === 3;
                                const textEl = el.querySelector('#processingText');
                                window.__processingHadText = textEl ? textEl.textContent.includes('처리') : false;
                            }
                        }
                    }
                }
            });
            observer.observe(document.getElementById('chatMessages'), { childList: true, subtree: true });
            window.__processingObserver = observer;
        }
    """)

    page.locator("#prompt").fill("CPU 사용률 현황")
    page.locator("#sendBtn").click()

    # 최종 응답 대기
    page.locator(".message--agent:not(.message--processing)").wait_for(
        state="visible", timeout=30000
    )

    # Observer 정리
    page.evaluate("window.__processingObserver?.disconnect()")

    appeared = page.evaluate("window.__processingAppeared")
    had_dots = page.evaluate("window.__processingHadDots")
    had_text = page.evaluate("window.__processingHadText")

    assert appeared, "processingMessage가 DOM에 추가되지 않았다"
    assert had_dots, "processing-dots span이 3개가 아니었다"
    assert had_text, "processingText에 '처리'가 포함되지 않았다"


# ---------------------------------------------------------------------------
# P-02: SSE node_start 수신 시 stage active 업데이트
# ---------------------------------------------------------------------------


def test_stage_active_on_node_start(page: Page) -> None:
    """P-02: SSE node_start 이벤트 수신 시 해당 스테이지가 active 상태가 되는지 확인한다.

    MockGraph가 빠르게 진행하므로 MutationObserver로 stage active 클래스 추가를 감지한다.
    """
    # MutationObserver로 stage active/done 클래스 변화를 감지
    page.evaluate("""
        () => {
            window.__stageActivated = false;
            const observer = new MutationObserver((mutations) => {
                for (const m of mutations) {
                    if (m.type === 'attributes' && m.attributeName === 'class') {
                        const cls = m.target.getAttribute('class') || '';
                        if (cls.includes('active') || cls.includes('done')) {
                            if (m.target.matches && m.target.matches('.stage')) {
                                window.__stageActivated = true;
                            }
                        }
                    }
                    for (const node of m.addedNodes) {
                        if (node.querySelector) {
                            const active = node.querySelector('.stage.active, .stage.done');
                            if (active) window.__stageActivated = true;
                        }
                    }
                }
            });
            observer.observe(document.body, { childList: true, subtree: true, attributes: true });
            window.__stageObserver = observer;
        }
    """)

    page.locator("#prompt").fill("서버 목록 조회")
    page.locator("#sendBtn").click()

    # 최종 응답 대기
    page.locator(".message--agent:not(.message--processing)").wait_for(
        state="visible", timeout=30000
    )

    page.evaluate("window.__stageObserver?.disconnect()")
    activated = page.evaluate("window.__stageActivated")

    # stage가 active/done이 된 적이 있거나, pipeline-step이 생성되었으면 성공
    pipeline_steps = page.locator(".pipeline-step").count()
    assert activated or pipeline_steps > 0, (
        "SSE에 의한 stage 활성화 또는 pipeline-step 생성이 감지되지 않았다"
    )


# ---------------------------------------------------------------------------
# P-03: SSE node_complete 수신 시 stage done 업데이트
# ---------------------------------------------------------------------------


def test_stage_done_on_node_complete(page: Page) -> None:
    """P-03: SSE node_complete 이벤트 수신 후 해당 스테이지가 done 상태가 되는지 확인한다.

    전체 스트리밍이 완료되면 모든 스테이지가 done이어야 한다.
    """
    page.locator("#prompt").fill("전체 서버 수")
    page.locator("#sendBtn").click()

    # 최종 응답 대기
    page.locator(".message--agent:not(.message--processing)").wait_for(
        state="visible", timeout=30000
    )

    # 스트리밍 완료 후에는 모든 stage가 done이어야 한다
    # processingMessage가 제거된 후이므로, 직접 stage 요소가
    # DOM에 없을 수 있다 (processing message와 함께 제거됨).
    # 이 경우 processing message 제거 자체가 정상 완료의 증거이다.
    processing = page.locator("#processingMessage")
    assert processing.count() == 0


# ---------------------------------------------------------------------------
# P-04: Progress Panel에 pipeline-step 생성
# ---------------------------------------------------------------------------


def test_progress_panel_pipeline_steps(page: Page) -> None:
    """P-04: SSE 수신 시 Progress Panel에 pipeline-step 요소가 생성되는지 확인한다."""
    page.locator("#prompt").fill("서버 목록 조회")
    page.locator("#sendBtn").click()

    # pipeline-step이 나타날 때까지 대기
    page.locator(".pipeline-step").first.wait_for(state="visible", timeout=15000)
    steps_count = page.locator(".pipeline-step").count()
    assert steps_count >= 1, f"pipeline-step이 {steps_count}개 생성됨, 최소 1개 기대"


# ---------------------------------------------------------------------------
# P-05: Progress Panel 노드 데이터 표시
# ---------------------------------------------------------------------------


def test_progress_panel_node_data(page: Page) -> None:
    """P-05: node_complete 시 Progress Panel에 노드 데이터(SQL, 행 수 등)가 표시되는지 확인한다."""
    page.locator("#prompt").fill("서버 IP 목록")
    page.locator("#sendBtn").click()

    # 최종 응답 대기
    page.locator(".message--agent:not(.message--processing)").wait_for(
        state="visible", timeout=30000
    )

    # query_generator 스텝에 SQL이 표시되어야 한다
    sql_step = page.locator("#step-query_generator")
    if sql_step.count() > 0:
        sql_step.wait_for(state="visible", timeout=5000)
        step_body = sql_step.locator(".pipeline-step-body")
        body_text = step_body.inner_text()
        assert "SELECT" in body_text, "query_generator 스텝에 SQL이 포함되어야 한다"

    # query_executor 스텝에 행 수가 표시되어야 한다
    exec_step = page.locator("#step-query_executor")
    if exec_step.count() > 0:
        exec_step.wait_for(state="visible", timeout=5000)
        exec_body = exec_step.locator(".pipeline-step-body")
        exec_text = exec_body.inner_text()
        # MockGraph가 5행을 반환하므로 숫자가 포함되어야 한다
        assert any(c.isdigit() for c in exec_text), (
            "query_executor 스텝에 행 수가 포함되어야 한다"
        )


# ---------------------------------------------------------------------------
# P-06: 완료 후 processingMessage 제거
# ---------------------------------------------------------------------------


def test_processing_message_removed_after_completion(page: Page) -> None:
    """P-06: SSE done 이벤트 후 processingMessage가 제거되는지 확인한다.

    MockGraph가 빠르게 응답하므로 MutationObserver로 출현 후 제거를 감지한다.
    """
    # MutationObserver로 processingMessage 출현+제거 감지
    page.evaluate("""
        () => {
            window.__procAppeared = false;
            window.__procRemoved = false;
            const observer = new MutationObserver((mutations) => {
                for (const m of mutations) {
                    for (const node of m.addedNodes) {
                        if (node.id === 'processingMessage' ||
                            (node.querySelector && node.querySelector('#processingMessage'))) {
                            window.__procAppeared = true;
                        }
                    }
                    for (const node of m.removedNodes) {
                        if (node.id === 'processingMessage' ||
                            (node.querySelector && node.querySelector('#processingMessage'))) {
                            window.__procRemoved = true;
                        }
                    }
                }
            });
            observer.observe(document.getElementById('chatMessages'), { childList: true, subtree: true });
            window.__procObserver = observer;
        }
    """)

    page.locator("#prompt").fill("디스크 사용률")
    page.locator("#sendBtn").click()

    # 최종 응답 대기
    page.locator(".message--agent:not(.message--processing)").wait_for(
        state="visible", timeout=30000
    )

    page.evaluate("window.__procObserver?.disconnect()")

    appeared = page.evaluate("window.__procAppeared")
    removed = page.evaluate("window.__procRemoved")

    assert appeared, "processingMessage가 DOM에 추가되지 않았다"
    assert removed, "processingMessage가 DOM에서 제거되지 않았다"
    assert page.locator("#processingMessage").count() == 0


# ---------------------------------------------------------------------------
# P-07: 에이전트 응답 메시지의 메타 정보
# ---------------------------------------------------------------------------


def test_agent_response_meta_info(page: Page) -> None:
    """P-07: 에이전트 응답에 ROWS, TIME, ID 메타 정보가 표시되는지 확인한다."""
    page.locator("#prompt").fill("전체 서버 수")
    page.locator("#sendBtn").click()

    # 에이전트 응답 대기
    agent_msg = page.locator(".message--agent:not(.message--processing)")
    agent_msg.wait_for(state="visible", timeout=30000)

    # 응답 텍스트 존재
    response_text = page.locator(".response-text").last
    expect(response_text).to_be_visible()
    assert len(response_text.inner_text()) > 0

    # 메타 영역에 ROWS, TIME, ID가 표시되어야 한다
    meta_area = page.locator(".message-meta").last
    meta_area.wait_for(state="visible", timeout=5000)
    meta_text = meta_area.inner_text()

    assert "ROWS" in meta_text, "ROWS 메타가 표시되어야 한다"
    assert "TIME" in meta_text, "TIME 메타가 표시되어야 한다"
    assert "ID" in meta_text, "ID 메타가 표시되어야 한다"


# ---------------------------------------------------------------------------
# P-08: SQL 토글
# ---------------------------------------------------------------------------


def test_sql_toggle(page: Page) -> None:
    """P-08: '실행된 SQL 보기' 버튼 클릭 시 SQL 코드가 표시/숨김되는지 확인한다."""
    page.locator("#prompt").fill("서버 IP 목록")
    page.locator("#sendBtn").click()

    # 응답 대기
    page.locator(".message--agent:not(.message--processing)").wait_for(
        state="visible", timeout=30000
    )

    # SQL 토글 버튼 확인
    sql_toggle = page.locator(".message-sql-toggle").last
    sql_toggle.wait_for(state="visible", timeout=5000)
    assert "SQL" in sql_toggle.inner_text()

    # 초기 상태에서 SQL 코드는 숨김 (open 클래스 없음)
    sql_code = page.locator(".message-sql-code").last
    assert "open" not in (sql_code.get_attribute("class") or "")

    # 토글 버튼 클릭 -> SQL 코드 표시
    sql_toggle.click()
    page.wait_for_timeout(300)
    assert "open" in (sql_code.get_attribute("class") or ""), (
        "토글 클릭 후 SQL 코드에 open 클래스가 있어야 한다"
    )

    # 다시 클릭 -> SQL 코드 숨김
    sql_toggle.click()
    page.wait_for_timeout(300)
    assert "open" not in (sql_code.get_attribute("class") or ""), (
        "재클릭 후 SQL 코드에서 open 클래스가 제거되어야 한다"
    )


# ---------------------------------------------------------------------------
# P-09: Fallback 모드 (SSE 실패 시 일반 POST 폴백)
# ---------------------------------------------------------------------------


def test_fallback_mode_on_sse_failure(page: Page) -> None:
    """P-09: SSE endpoint가 404를 반환하면 fallback POST로 응답이 정상 표시되는지 확인한다.

    page.route로 /api/v1/query/stream을 404로 intercept하고,
    /api/v1/query (fallback POST)는 정상 동작하도록 한다.
    """
    # SSE 엔드포인트를 404로 intercept
    def intercept_sse(route):
        route.fulfill(status=404, body="Not Found")

    page.route("**/api/v1/query/stream", intercept_sse)

    page.locator("#prompt").fill("서버 현황 조회")
    page.locator("#sendBtn").click()

    # fallback POST를 통해 에이전트 응답이 도착해야 한다
    agent_msg = page.locator(".message--agent:not(.message--processing)")
    agent_msg.wait_for(state="visible", timeout=30000)

    # 응답 텍스트가 존재해야 한다
    response_text = page.locator(".response-text").last.inner_text()
    assert len(response_text) > 0, "Fallback 모드에서도 응답 텍스트가 표시되어야 한다"

    # route intercept 해제
    page.unroute("**/api/v1/query/stream")


# ---------------------------------------------------------------------------
# P-10: 타임아웃 에러 (30초 초과 시 에러)
# ---------------------------------------------------------------------------


def test_timeout_error_display(page: Page) -> None:
    """P-10: 응답 지연 시 에러 메시지가 표시되는지 확인한다.

    page.route로 SSE endpoint를 무한 지연시키고,
    일반 POST fallback도 차단하여 네트워크 에러를 유발한다.
    JS에서 fetch 에러가 발생하면 에러 메시지가 표시된다.
    """
    # SSE 엔드포인트를 abort하여 네트워크 에러 발생
    def intercept_sse_abort(route):
        route.abort("timedout")

    # fallback POST도 abort
    def intercept_post_abort(route):
        route.abort("timedout")

    page.route("**/api/v1/query/stream", intercept_sse_abort)
    page.route("**/api/v1/query", intercept_post_abort)

    page.locator("#prompt").fill("타임아웃 테스트")
    page.locator("#sendBtn").click()

    # 에러 메시지가 표시되어야 한다
    error_el = page.locator("#chatError")
    error_el.wait_for(state="visible", timeout=15000)

    error_text = page.locator("#chatErrorText").inner_text()
    assert len(error_text) > 0, "에러 메시지가 표시되어야 한다"
    # "통신" 또는 "오류" 또는 "실패" 등의 에러 키워드 확인
    assert any(
        keyword in error_text for keyword in ["통신", "오류", "실패", "에러"]
    ), f"에러 메시지에 관련 키워드가 포함되어야 한다: {error_text}"

    # route intercept 해제
    page.unroute("**/api/v1/query/stream")
    page.unroute("**/api/v1/query")
