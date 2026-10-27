"""plans/160 D(표 넓게 보기) · A(그래프 보기) · B·C(용어 사전·값 해석 팝오버).

정적 자산·배선 계약을 고정한다.

화면 동작(넘침 판정·오버레이·포커스·차트·팝오버)은 실 브라우저 하네스로 확인했다. 여기서는
조용히 깨지는 지점만 정적으로 고정한다 — 스크립트가 빠지거나 순서·캐시 버전이 어긋나거나,
호출 2곳 중 하나가 사라지거나, 복원 턴이 query_id를 넘겨 CSV 링크가 되살아나거나, 넓히는 규칙이
좁은 화면 구간(1024px 이하)으로 새거나, 버튼에 id가 붙어 매뉴얼 역방향 가드를 깨거나,
서버·LLM 문자열이 innerHTML로 들어가는 경우.
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
def viz_js() -> str:
    return (_STATIC / "js" / "result-viz.js").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def style_css() -> str:
    return (_STATIC / "css" / "style.css").read_text(encoding="utf-8")


def test_result_viz_script_loaded_before_app(index_html: str) -> None:
    """result-viz.js는 app.js보다 먼저 실린다(app.js가 window.ResultViz를 찾는다)."""
    viz = index_html.find('src="/static/js/result-viz.js')
    app = index_html.find('src="/static/js/app.js')
    assert viz != -1 and app != -1
    assert viz < app


def test_app_calls_enhance_tables_at_two_render_points(app_js: str) -> None:
    """렌더 완료 지점 2곳(정적 응답 · 스트림 완료)에서만 부른다."""
    assert app_js.count("ResultViz.enhanceTables(") == 2
    begin = app_js.index("function renderAgentMessage(")
    static_fn = app_js[begin : app_js.index("function createStreamingMessage(")]
    assert "ResultViz.enhanceTables(el)" in static_fn
    final_fn = app_js[app_js.index("function finalizeStreamingMessage(") :]
    final_fn = final_fn[: final_fn.index("\n    function ")]
    assert "ResultViz.enhanceTables(streamingMsg)" in final_fn


def _js_function(src: str, name: str) -> str:
    """`function name(` 부터 다음 최상위(4칸 들여쓰기) 함수 선언 직전까지."""
    begin = src.index(f"    function {name}(")
    end = src.find("\n    function ", begin + 1)
    return src[begin : end if end != -1 else len(src)]


def test_result_viz_public_api_and_server_calls(viz_js: str) -> None:
    """공개 객체는 enhanceTables·attach 둘 · 넓게 보기는 서버를 부르지 않음.

    서버 호출은 계약 3종뿐 · 버튼에 id 없음.
    """
    assert "window.ResultViz" in viz_js
    assert "enhanceTables: enhanceTables" in viz_js
    assert "attach: attach" in viz_js
    # D(넓게 보기)는 표시만 — 서버 호출은 A·B·C의 requestJson 한 곳에서만 한다
    assert "fetch(" not in _js_function(viz_js, "enhanceTables")
    assert "fetch(" not in _js_function(viz_js, "openTableOverlay")
    assert viz_js.count("fetch(") == 1 and "XMLHttpRequest" not in viz_js
    assert '"/chart"' in viz_js
    assert '"/interpret", "POST"' in viz_js
    assert '"/glossary?table="' in viz_js
    assert not re.search(r"\.id\s*=", viz_js)
    assert 'setAttribute("role", "dialog")' in viz_js
    assert 'setAttribute("aria-modal", "true")' in viz_js


def test_widening_rules_only_above_1024px(style_css: str) -> None:
    """넓히는 규칙은 `@media (min-width: 1025px)` 블록 안에만 있다(주석은 제외하고 본다)."""
    css = re.sub(r"/\*.*?\*/", "", style_css, flags=re.S)
    start = css.index("@media (min-width: 1025px)")
    block = css[start : css.index("\n}\n", start)]
    assert ".chat-layout.history-collapsed .chat-messages" in block
    assert ".chat-layout.history-collapsed.panel-collapsed .chat-messages" in block
    assert ".message--agent .message-content.has-wide-content" in block
    assert ":has(table, .result-viz-panel)" in block
    outside = css[:start] + css[start + len(block) :]
    assert ".has-wide-content" not in outside
    assert ".history-collapsed .chat-messages" not in outside


def test_chartjs_and_cache_busting_versions(index_html: str) -> None:
    """Chart.js는 result-viz.js보다 먼저 · 바꾼 정적 자산은 버전을 올린다(D-187 캐시 실수 이력)."""
    chart = index_html.find('src="/static/vendor/chartjs/chart.umd.min.js?v=1"')
    viz = index_html.find('src="/static/js/result-viz.js?v=4"')
    assert chart != -1 and viz != -1 and chart < viz
    assert 'href="/static/css/style.css?v=42"' in index_html
    assert 'src="/static/js/app.js?v=42"' in index_html


def test_app_calls_attach_at_two_render_points(app_js: str) -> None:
    """그래프 버튼은 enhanceTables와 같은 렌더 완료 2곳에서 붙인다 — 인증 헤더 공급자도 넘긴다."""
    assert app_js.count("ResultViz.attach(") == 2
    begin = app_js.index("function renderAgentMessage(")
    static_fn = app_js[begin : app_js.index("function createStreamingMessage(")]
    headers = "{ headers: getAuthHeaders })"
    assert f'ResultViz.attach(el.querySelector(".message-bubble"), data, {headers}' in static_fn
    final_fn = app_js[app_js.index("function finalizeStreamingMessage(") :]
    final_fn = final_fn[: final_fn.index("\n    function ")]
    stream_bubble = 'streamingMsg.querySelector(".message-bubble")'
    assert f"ResultViz.attach({stream_bubble}, meta, {headers}" in final_fn
    # 스트림 완료 기록에도 chart 신호를 싣는다(2개 스트림 경로)
    assert app_js.count("chart: metaData.chart") == 2


def test_restored_turn_passes_chart_without_query_id(app_js: str) -> None:
    """대화 복원 턴은 query_id 대신 result_query_id로 넘긴다 — CSV 링크가 되살아나지 않게."""
    begin = app_js.index("function showThreadTurns(")
    fn = app_js[begin : app_js.index("\n    function ", begin + 1)]
    assert "chart: t.chart" in fn
    assert "result_query_id: t.query_id" in fn
    assert not re.search(r"[^_]query_id:", fn)


def test_result_viz_untrusted_text_only_via_textcontent(viz_js: str) -> None:
    """서버·LLM 문자열은 textContent로만 — innerHTML은 고정 아이콘 1곳 · 시간 서식은 UTC 필드만."""
    assert viz_js.count(".innerHTML") == 1
    assert "btn.innerHTML = CHART_ICON;" in viz_js
    assert "insertAdjacentHTML" not in viz_js and "outerHTML" not in viz_js
    assert "textContent" in viz_js
    assert "getUTCHours()" in viz_js
    assert not re.search(r"\.get(Hours|Minutes|Seconds|Date|Month|FullYear)\(", viz_js)


def test_abc_css_block_uses_theme_tokens_only(style_css: str) -> None:
    """A·B·C 블록은 D 블록 뒤에 있고 색은 테마 토큰만 쓴다(하드코딩 색 금지)."""
    d_at = style_css.index("/* plans/160 D")
    abc_at = style_css.index("/* plans/160 A·B·C")
    assert d_at < abc_at
    block = re.sub(r"/\*.*?\*/", "", style_css[abc_at:], flags=re.S)
    for sel in (
        ".result-viz-panel",
        ".result-viz-chart",
        "table.result-viz-grid",
        ".result-viz-link",
        ".result-viz-pop",
        ".result-viz-badge--glossary",
        ".result-viz-badge--llm",
    ):
        assert sel in block
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(", block)


def test_overlay_close_also_closes_popover_and_rows_notice(viz_js: str) -> None:
    """오버레이를 닫으면 그 안 팝오버도 닫는다 · 격자 행 상한 고지는 rows_total이 있을 때만."""
    assert "closePopoverWithin(o.root)" in _js_function(viz_js, "closeTableOverlay")
    show = _js_function(viz_js, "showTable")
    assert 'typeof t.rows_total === "number" && t.rows_total > shown' in show
