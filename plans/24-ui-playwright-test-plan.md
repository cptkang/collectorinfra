# Plan 24: Playwright UI 테스트 계획

> 작성일: 2026-03-24
> 관련 계획: Plan 23 (UI 진행상태 및 Excel 수정)
> 관련 파일: `src/static/index.html`, `src/static/js/app.js`, `src/api/routes/query.py`

---

## 1. 개요

Web UI의 핵심 사용자 흐름을 Playwright로 자동화 테스트한다.
테스트 대상은 3가지 영역:
1. **기본 UI 인터랙션** — 페이지 로드, 입력, 전송
2. **진행상태 표시** — 처리 인디케이터, Progress Panel, SSE 스트리밍
3. **Excel 파일 업로드/다운로드** — 파일 첨부, 질의 실행, 결과 파일 다운로드

---

## 2. 테스트 환경 구성

### 2.1 디렉토리 구조

```
tests/
  e2e/
    __init__.py
    conftest.py              # Playwright fixtures, 앱 서버 fixture
    test_basic_ui.py         # 기본 UI 테스트
    test_progress_display.py # 진행상태 표시 테스트
    test_excel_workflow.py   # Excel 업로드/다운로드 테스트
    test_streaming.py        # SSE 스트리밍 테스트
    fixtures/
      sample_template.xlsx   # 테스트용 Excel 양식
      sample_template.docx   # 테스트용 Word 양식
```

### 2.2 의존성

```toml
# pyproject.toml [project.optional-dependencies]
e2e = [
    "playwright>=1.40",
    "pytest-playwright>=0.4",
]
```

```bash
pip install -e ".[e2e]"
playwright install chromium
```

### 2.3 conftest.py — 앱 서버 Fixture

테스트용 FastAPI 앱을 별도 포트(예: 18980)에서 실행하고, 테스트 종료 시 셧다운한다.
LLM과 DB 호출은 Mock 처리하여 외부 의존성 없이 테스트한다.

```python
import pytest
import asyncio
import uvicorn
from multiprocessing import Process
from playwright.sync_api import Page

TEST_PORT = 18980
TEST_BASE_URL = f"http://localhost:{TEST_PORT}"


def _run_test_server():
    """테스트용 서버를 실행한다."""
    from src.api.server import create_app
    from src.config import load_config

    config = load_config()
    app = create_app(config)

    # Mock graph 주입 (LLM/DB 호출 없이)
    from unittest.mock import AsyncMock, MagicMock
    mock_graph = _create_mock_graph()
    app.state.graph = mock_graph

    uvicorn.run(app, host="0.0.0.0", port=TEST_PORT, log_level="warning")


def _create_mock_graph():
    """SSE 이벤트를 흉내내는 Mock 그래프를 생성한다."""
    # ainvoke: 정상 응답 반환
    # astream_events: node_start/complete + token 이벤트 생성
    ...


@pytest.fixture(scope="session")
def test_server():
    """테스트 서버를 세션 범위로 실행한다."""
    proc = Process(target=_run_test_server, daemon=True)
    proc.start()

    # 서버 준비 대기
    import time, requests
    for _ in range(30):
        try:
            requests.get(f"{TEST_BASE_URL}/api/v1/health", timeout=1)
            break
        except Exception:
            time.sleep(0.5)

    yield TEST_BASE_URL
    proc.terminate()
    proc.join(timeout=5)


@pytest.fixture
def page(test_server, page: Page):
    """각 테스트 전에 메인 페이지를 로드한다."""
    page.goto(test_server)
    return page
```

---

## 3. 테스트 시나리오

### 3.1 기본 UI 테스트 (`test_basic_ui.py`)

| ID | 시나리오 | 검증 항목 |
|----|----------|-----------|
| B-01 | 페이지 로드 | 타이틀 "인프라 데이터 조회 에이전트", 헤더 "INFRA QUERY AGENT", ONLINE 배지 표시 |
| B-02 | Welcome 화면 표시 | Welcome 메시지, 힌트 버튼 4개 표시 |
| B-03 | 힌트 버튼 클릭 | 클릭 시 textarea에 질의 텍스트 채워짐 |
| B-04 | 빈 입력 전송 차단 | Enter 또는 전송 버튼 시 에러 메시지 "질의를 입력해주세요" |
| B-05 | 텍스트 입력 및 전송 | 사용자 메시지 버블 생성, Welcome 화면 숨김 |
| B-06 | Enter 키 전송 | Enter 시 전송, Shift+Enter 시 개행 |
| B-07 | 처리 중 버튼 비활성화 | 전송 후 sendBtn.disabled === true |
| B-08 | 에러 메시지 자동 숨김 | 에러 표시 후 8초 내 자동 숨김 |
| B-09 | Progress Panel 토글 | 토글 버튼 클릭 시 패널 접기/펼치기 |
| B-10 | 반응형 레이아웃 | 640px 이하에서 Progress Panel 숨김 |

```python
def test_page_load(page):
    """B-01: 페이지가 정상 로드되는지 확인한다."""
    assert page.title() == "인프라 데이터 조회 에이전트"
    assert page.locator("h1").inner_text() == "INFRA QUERY AGENT"
    assert page.locator(".status-badge--online").is_visible()


def test_welcome_screen(page):
    """B-02: Welcome 화면이 표시되는지 확인한다."""
    welcome = page.locator("#chatWelcome")
    assert welcome.is_visible()
    hints = page.locator(".chat-welcome-hint")
    assert hints.count() == 4


def test_hint_button_fills_input(page):
    """B-03: 힌트 버튼 클릭 시 textarea에 텍스트가 채워지는지 확인한다."""
    page.locator(".chat-welcome-hint").first.click()
    textarea = page.locator("#prompt")
    assert textarea.input_value() != ""


def test_empty_submit_shows_error(page):
    """B-04: 빈 입력 전송 시 에러가 표시되는지 확인한다."""
    page.locator("#sendBtn").click()
    error = page.locator("#chatError")
    assert error.is_visible()
    assert "질의를 입력해주세요" in page.locator("#chatErrorText").inner_text()


def test_send_message(page):
    """B-05: 메시지 전송 시 사용자 버블이 생성되는지 확인한다."""
    page.locator("#prompt").fill("테스트 질의")
    page.locator("#sendBtn").click()

    # Welcome 숨김
    assert page.locator("#chatWelcome").is_hidden()
    # 사용자 메시지 버블 존재
    assert page.locator(".message--user").count() >= 1


def test_enter_sends_shift_enter_newline(page):
    """B-06: Enter=전송, Shift+Enter=개행을 확인한다."""
    textarea = page.locator("#prompt")
    textarea.fill("첫 줄")
    textarea.press("Shift+Enter")
    textarea.type("둘째 줄")
    assert "\n" in textarea.input_value()

    textarea.press("Enter")
    # 전송되어 메시지 버블 생성
    assert page.locator(".message--user").count() >= 1


def test_progress_panel_toggle(page):
    """B-09: Progress Panel 토글을 확인한다."""
    page.locator("#panelToggle").click()
    assert page.locator(".chat-layout").get_attribute("class").find("panel-collapsed") != -1

    page.locator("#panelToggle").click()
    assert page.locator(".chat-layout").get_attribute("class").find("panel-collapsed") == -1
```

### 3.2 진행상태 표시 테스트 (`test_progress_display.py`)

| ID | 시나리오 | 검증 항목 |
|----|----------|-----------|
| P-01 | 처리 인디케이터 표시 | 질의 전송 후 processing dots + "처리 중..." 텍스트 |
| P-02 | SSE node_start 수신 시 인디케이터 업데이트 | 해당 스테이지가 active 상태 |
| P-03 | SSE node_complete 수신 시 인디케이터 업데이트 | 해당 스테이지가 done 상태 |
| P-04 | Progress Panel에 노드 표시 | SSE 수신 시 pipeline-step 요소 생성 |
| P-05 | Progress Panel 노드 데이터 표시 | node_complete 시 데이터(SQL, 행 수 등) 표시 |
| P-06 | 완료 후 인디케이터 제거 | SSE done 이벤트 후 processing message 제거 |
| P-07 | 에이전트 응답 메시지 표시 | 응답 텍스트, 메타(ROWS, TIME, ID) 표시 |
| P-08 | SQL 토글 | "실행된 SQL 보기" 클릭 시 SQL 코드 표시/숨김 |
| P-09 | Fallback 모드 | SSE 실패 시 일반 POST 폴백, 응답 정상 표시 |
| P-10 | 타임아웃 에러 | 30초 초과 시 에러 메시지 표시 |

```python
def test_processing_indicator_shown(page):
    """P-01: 질의 전송 후 처리 인디케이터가 표시되는지 확인한다."""
    page.locator("#prompt").fill("CPU 사용률 현황")
    page.locator("#sendBtn").click()

    # 처리 중 메시지 표시
    processing = page.locator("#processingMessage")
    processing.wait_for(state="visible", timeout=3000)
    assert page.locator(".processing-dots").is_visible()
    assert "처리 중" in page.locator("#processingText").inner_text()


def test_progress_panel_nodes(page):
    """P-04: SSE 수신 시 Progress Panel에 노드가 표시되는지 확인한다."""
    page.locator("#prompt").fill("서버 목록 조회")
    page.locator("#sendBtn").click()

    # Progress Panel에 pipeline-step 요소 생성 대기
    page.locator(".pipeline-step").first.wait_for(state="visible", timeout=15000)
    assert page.locator(".pipeline-step").count() >= 1


def test_agent_response_with_meta(page):
    """P-07: 응답 메시지에 메타 정보가 표시되는지 확인한다."""
    page.locator("#prompt").fill("전체 서버 수")
    page.locator("#sendBtn").click()

    # 에이전트 응답 대기
    page.locator(".message--agent:not(.message--processing)").wait_for(
        state="visible", timeout=30000
    )

    # 응답 텍스트 존재
    response_text = page.locator(".response-text").last.inner_text()
    assert len(response_text) > 0


def test_sql_toggle(page):
    """P-08: SQL 토글이 동작하는지 확인한다."""
    page.locator("#prompt").fill("서버 IP 목록")
    page.locator("#sendBtn").click()

    # 응답 대기
    page.locator(".message--agent:not(.message--processing)").wait_for(
        state="visible", timeout=30000
    )

    # SQL 토글 버튼이 있으면 클릭
    sql_toggle = page.locator(".message-sql-toggle").last
    if sql_toggle.is_visible():
        sql_toggle.click()
        sql_code = page.locator(".message-sql-code.open").last
        assert sql_code.is_visible()
```

### 3.3 Excel 업로드/다운로드 테스트 (`test_excel_workflow.py`)

| ID | 시나리오 | 검증 항목 |
|----|----------|-----------|
| E-01 | 파일 첨부 버튼 | 클릭 시 파일 선택 다이얼로그 |
| E-02 | 지원 파일 형식 검증 | .xlsx, .docx만 허용, 다른 형식 에러 |
| E-03 | 파일 크기 제한 | 10MB 초과 시 에러 |
| E-04 | 파일 첨부 프리뷰 | 파일명, 크기 표시 |
| E-05 | 파일 제거 | Remove 버튼 클릭 시 첨부 취소 |
| E-06 | Excel + 질의 전송 | 파일과 텍스트가 함께 전송 |
| E-07 | 전송 후 프리뷰 제거 | 전송 후 파일 프리뷰 숨김 |
| E-08 | 처리 중 표시 | 파일 질의 처리 중 인디케이터 표시 |
| E-09 | 응답에 다운로드 버튼 | has_file=true 시 다운로드 버튼 표시 |
| E-10 | 다운로드 실행 | 다운로드 버튼 클릭 시 파일 다운로드 |
| E-11 | 사용자 메시지에 파일 배지 | 전송된 메시지에 파일명 배지 표시 |

```python
import os

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


def test_file_attach_preview(page):
    """E-04: 파일 첨부 시 프리뷰가 표시되는지 확인한다."""
    file_input = page.locator("#fileInput")
    file_input.set_input_files(os.path.join(FIXTURE_DIR, "sample_template.xlsx"))

    preview = page.locator("#filePreview")
    assert preview.is_visible()
    assert "sample_template.xlsx" in page.locator("#fileName").inner_text()


def test_unsupported_file_rejected(page):
    """E-02: 지원하지 않는 파일 형식이 거부되는지 확인한다."""
    # .txt 파일 시도 (accept 속성에 의해 필터링)
    # Playwright에서는 accept를 우회할 수 있으므로 에러 메시지 확인
    file_input = page.locator("#fileInput")

    # 임시 .txt 파일 생성
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
        f.write(b"test")
        txt_path = f.name

    try:
        file_input.set_input_files(txt_path)
        error = page.locator("#chatError")
        error.wait_for(state="visible", timeout=3000)
        assert "지원하지 않는 파일 형식" in page.locator("#chatErrorText").inner_text()
    finally:
        os.unlink(txt_path)


def test_file_remove(page):
    """E-05: 파일 제거가 동작하는지 확인한다."""
    file_input = page.locator("#fileInput")
    file_input.set_input_files(os.path.join(FIXTURE_DIR, "sample_template.xlsx"))

    assert page.locator("#filePreview").is_visible()

    page.locator("#removeFile").click()
    assert page.locator("#filePreview").is_hidden()


def test_excel_query_send(page):
    """E-06: Excel 파일과 질의가 함께 전송되는지 확인한다."""
    # 파일 첨부
    file_input = page.locator("#fileInput")
    file_input.set_input_files(os.path.join(FIXTURE_DIR, "sample_template.xlsx"))

    # 질의 입력
    page.locator("#prompt").fill("서버 현황 데이터를 양식에 채워줘")

    # 전송
    page.locator("#sendBtn").click()

    # 사용자 메시지에 파일 배지 표시
    file_badge = page.locator(".message-file-badge")
    file_badge.wait_for(state="visible", timeout=3000)
    assert "sample_template.xlsx" in file_badge.inner_text()

    # 프리뷰 숨김
    assert page.locator("#filePreview").is_hidden()


def test_download_button_shown(page):
    """E-09: has_file=true 시 다운로드 버튼이 표시되는지 확인한다."""
    # 파일 첨부 + 질의
    file_input = page.locator("#fileInput")
    file_input.set_input_files(os.path.join(FIXTURE_DIR, "sample_template.xlsx"))
    page.locator("#prompt").fill("양식에 데이터 채워줘")
    page.locator("#sendBtn").click()

    # 응답 대기 (최대 60초 — 문서 생성 시간 고려)
    page.locator(".message--agent:not(.message--processing)").wait_for(
        state="visible", timeout=60000
    )

    # 다운로드 버튼 확인
    download_btn = page.locator(".message-download").last
    assert download_btn.is_visible()
    href = download_btn.get_attribute("href")
    assert "/api/v1/query/" in href
    assert "/download" in href


def test_file_download(page):
    """E-10: 다운로드 버튼 클릭 시 파일이 다운로드되는지 확인한다."""
    # 파일 첨부 + 질의
    file_input = page.locator("#fileInput")
    file_input.set_input_files(os.path.join(FIXTURE_DIR, "sample_template.xlsx"))
    page.locator("#prompt").fill("양식에 데이터 채워줘")
    page.locator("#sendBtn").click()

    # 응답 대기
    page.locator(".message-download").last.wait_for(
        state="visible", timeout=60000
    )

    # 다운로드 실행
    with page.expect_download() as download_info:
        page.locator(".message-download").last.click()
    download = download_info.value

    assert download.suggested_filename.endswith(".xlsx")
    # 파일 크기 확인 (빈 파일이 아닌지)
    path = download.path()
    assert os.path.getsize(path) > 0
```

### 3.4 SSE 스트리밍 테스트 (`test_streaming.py`)

| ID | 시나리오 | 검증 항목 |
|----|----------|-----------|
| S-01 | SSE 토큰 스트리밍 | 응답 텍스트가 점진적으로 표시 |
| S-02 | 타이핑 커서 표시 | 스트리밍 중 커서 깜빡임, 완료 후 제거 |
| S-03 | 스트리밍 완료 메타 표시 | 완료 시 ROWS, TIME, ID 메타 표시 |
| S-04 | 스트리밍 에러 처리 | SSE error 이벤트 시 에러 메시지 표시 |
| S-05 | 연결 끊김 폴백 | 네트워크 에러 시 fallback POST 시도 |

```python
def test_streaming_cursor(page):
    """S-02: 스트리밍 중 타이핑 커서가 표시되는지 확인한다."""
    page.locator("#prompt").fill("서버 현황")
    page.locator("#sendBtn").click()

    # 스트리밍 메시지 대기
    streaming = page.locator("#streamingMessage")
    try:
        streaming.wait_for(state="visible", timeout=10000)
        cursor = page.locator("#streamingCursor")
        assert cursor.is_visible()
    except Exception:
        # SSE 미지원 환경에서는 스킵
        pass

    # 최종 응답 대기
    page.locator(".message--agent:not(.message--processing)").wait_for(
        state="visible", timeout=30000
    )

    # 커서 제거 확인
    assert page.locator("#streamingCursor").count() == 0
```

---

## 4. Mock 전략

### 4.1 Mock Graph

테스트 서버에 주입할 Mock Graph는 다음 동작을 시뮬레이션한다:

```python
class MockGraph:
    """UI 테스트용 Mock LangGraph 그래프."""

    async def ainvoke(self, input_state, config):
        """일반 POST 용 Mock 응답."""
        query = input_state.get("user_query", "")
        has_file = input_state.get("uploaded_file") is not None

        result = {
            "final_response": f"'{query}'에 대한 조회 결과입니다.\n\n총 5건의 데이터가 조회되었습니다.",
            "generated_sql": "SELECT * FROM servers LIMIT 5",
            "query_results": [{"id": i, "hostname": f"srv-{i}"} for i in range(5)],
            "messages": [HumanMessage(content=query)],
        }

        if has_file:
            # Excel 결과 파일 생성 (openpyxl로 간단한 xlsx)
            result["output_file"] = _create_sample_xlsx()
            result["output_file_name"] = "result_20260324.xlsx"

        return result

    async def astream_events(self, input_state, config, version="v2"):
        """SSE 스트리밍 Mock 이벤트."""
        import asyncio

        nodes = ["input_parser", "schema_analyzer", "query_generator",
                 "query_validator", "query_executor", "result_organizer",
                 "output_generator"]

        for node in nodes:
            yield {"event": "on_chain_start", "name": node, "data": {}}
            await asyncio.sleep(0.1)
            yield {"event": "on_chain_end", "name": node, "data": {"output": _mock_node_output(node)}}

        # 최종 출력
        yield {
            "event": "on_chain_end",
            "name": "output_generator",
            "data": {"output": {
                "final_response": "Mock 응답입니다.",
                "generated_sql": "SELECT * FROM servers",
                "query_results": [{"id": 1}],
                "messages": [],
            }},
        }

    def get_state(self, config):
        return None
```

### 4.2 테스트 Fixture 파일

- `sample_template.xlsx`: 3열(서버명, IP주소, CPU사용률) 헤더가 있는 빈 양식
- `sample_template.docx`: `{{서버명}}`, `{{IP주소}}` 플레이스홀더 + 테이블이 있는 양식

이 파일들은 `tests/e2e/fixtures/`에 미리 생성하여 커밋한다.

---

## 5. 실행 방법

```bash
# 전체 E2E 테스트 실행
pytest tests/e2e/ -v

# 특정 시나리오만 실행
pytest tests/e2e/test_basic_ui.py -v
pytest tests/e2e/test_excel_workflow.py -v

# headed 모드 (브라우저 표시)
pytest tests/e2e/ -v --headed

# 특정 브라우저
pytest tests/e2e/ -v --browser chromium
pytest tests/e2e/ -v --browser firefox

# 스크린샷 캡처 (실패 시)
pytest tests/e2e/ -v --screenshot on --output tests/e2e/results/

# 비디오 녹화
pytest tests/e2e/ -v --video on --output tests/e2e/results/
```

---

## 6. CI 통합

```yaml
# .github/workflows/e2e.yml (향후)
- name: E2E Tests
  run: |
    pip install -e ".[e2e]"
    playwright install chromium --with-deps
    pytest tests/e2e/ -v --screenshot only-on-failure --output e2e-results/
```

---

## 7. 구현 순서

| 단계 | 작업 | 비고 |
|------|------|------|
| 1 | `tests/e2e/` 디렉토리 및 conftest.py 작성 | Mock 서버 포함 |
| 2 | 테스트 fixture 파일 생성 | sample_template.xlsx/docx |
| 3 | `test_basic_ui.py` 작성 (B-01 ~ B-10) | 외부 의존성 없음 |
| 4 | Plan 23 UI 수정 적용 | 수정 후 테스트 |
| 5 | `test_progress_display.py` 작성 (P-01 ~ P-10) | SSE Mock 필요 |
| 6 | `test_excel_workflow.py` 작성 (E-01 ~ E-11) | 파일 업로드/다운로드 |
| 7 | `test_streaming.py` 작성 (S-01 ~ S-05) | SSE 스트리밍 |
| 8 | CI 통합 | GitHub Actions |

---

## 8. 주의사항

- Playwright 테스트는 실제 서버를 로컬에서 실행하므로, 포트 충돌 방지를 위해 테스트 전용 포트(18980) 사용
- Mock Graph를 사용하므로 LLM API 키나 DB 연결 불필요
- 테스트 타임아웃은 각 시나리오별로 적절히 설정 (기본 30초, 파일 처리 60초)
- `--headed` 모드로 디버깅하면 UI 동작을 시각적으로 확인 가능
- 테스트 실패 시 스크린샷을 `tests/e2e/results/`에 저장하여 원인 분석에 활용
