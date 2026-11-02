"""plans/199 W4 — 엔진 비교 [서술 보기] · D-352 ⑤.

`BriefingView.renderMarkdownSafe`(escape 먼저 · 화이트리스트 변환 나중)와 summaryView 켜짐의
HolmesGPT 칸 4줄 요약 · 원문 접힘 · 플레이북 칸 안내를 node로 공용 렌더에 넣어 검사한다.
XSS 회귀는 출력의 태그가 허용 목록 · 고정 class 속성뿐인지로 단언한다
(escape된 글자로는 남아도 된다).
W0 픽스처(`tests/fixtures/plan199/`)는 읽기만 하고 변형은 테스트 안 사본으로 만든다. 플래그 off 비트
동일은 `tests/test_plan199_w1_summary.py`가 기준 SHA 렌더와 대조한다.
"""

from __future__ import annotations

import ast
import copy
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from noise_gate.infrastructure.investigation_store import DEEP_NARRATIVE_CLIPPED_NOTE

_ROOT = Path(__file__).resolve().parents[1]
_BV_JS = _ROOT / "src" / "static" / "js" / "briefing-view.js"
_STYLE = _ROOT / "src" / "static" / "css" / "style.css"
_FIX = _ROOT / "tests" / "fixtures" / "plan199"
_DISPATCHER = _ROOT / "sre_agent" / "sre_agent" / "application" / "investigation_dispatcher.py"

#: 렌더러가 만들 수 있는 태그 전부 — 이 밖의 태그가 하나라도 나오면 실패
_ALLOWED_TAGS = {
    "div", "strong", "code", "ul", "ol", "li", "table", "thead", "tbody", "tr", "th", "td",
}
#: 렌더러가 만드는 속성 전부(고정 class만 — 원문이 속성값에 들어가지 않는다)
_ALLOWED_ATTRS = {
    "",
    ' class="bv-md"',
    ' class="bv-md-h"',
    ' class="bv-md-tbl"',
    ' class="bv-md-toc"',
    ' class="bv-label"',
}
_TAG = re.compile(r"<(/?)([A-Za-z][A-Za-z0-9]*)([^>]*)>")


def _node(body: str) -> Any:
    if shutil.which("node") is None:
        pytest.skip("node 미설치 — JS 순수 함수 실행 테스트 생략")
    script = "\n".join([
        f"var BV = require({json.dumps(str(_BV_JS))});",
        "var out = null;",
        body,
        "process.stdout.write(JSON.stringify(out));",
    ])
    done = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=60, check=False
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def _js(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _md(text: Any) -> str:
    return _node(f"out = BV.renderMarkdownSafe({_js(text)});")


def _mds(texts: list[Any]) -> list[str]:
    return _node(f"out = {_js(texts)}.map(function (t) {{ return BV.renderMarkdownSafe(t); }});")


def _render(view: dict[str, Any], extra: dict[str, Any]) -> str:
    return _node(f"out = BV.renderBody({_js(view)}, {_js(extra)});")


def _detail(view: dict[str, Any], on: bool = True) -> str:
    extra: dict[str, Any] = {"mode": "detail"}
    if on:
        extra["summaryView"] = True
    return _render(view, extra)


def _view(name: str, key: str = "view") -> dict[str, Any]:
    data = json.loads((_FIX / f"{name}.json").read_text(encoding="utf-8"))
    return copy.deepcopy(data[key])


def _narr_text() -> dict[str, Any]:
    return _node("out = BV.NARR_TEXT;")


def _assert_safe(html: str) -> None:
    """태그는 허용 목록 · 속성은 고정 class뿐 · 태그 밖 글자에는 날것의 `<>"'`가 없다."""
    for m in _TAG.finditer(html):
        assert m.group(2).lower() in _ALLOWED_TAGS, m.group(0)
        assert m.group(3) in _ALLOWED_ATTRS or (m.group(1) and not m.group(3)), m.group(0)
    text = _TAG.sub("", html)
    assert not re.search(r"[<>\"']", text), text[:200]
    low = html.lower()
    for bad in ("<script", "<img", "<a ", "<svg", "<iframe", "<style"):
        assert bad not in low, bad
    for m in _TAG.finditer(html):  # javascript:·이벤트 속성은 태그 안에 없다(글자로는 있어도 된다)
        assert "javascript:" not in m.group(0).lower()
        assert not re.search(r"\son[a-z]+\s*=", m.group(0), re.I)


def _block(html: str, marker: str) -> str:
    """`marker`가 든 여는 `<details`부터 짝이 맞는 `</details>`까지."""
    at = html.rindex("<details", 0, html.index(marker))
    depth = 0
    for m in re.finditer(r"<details\b|</details>", html[at:]):
        depth += 1 if m.group(0) == "<details" else -1
        if depth == 0:
            return html[at : at + m.end()]
    raise AssertionError("details 짝 없음")


def _hg_narr(html: str) -> str:
    return _block(html, 'data-bv-key="narr-hg"')


def _pb_column(html: str) -> str:
    grid = html[html.index('<div class="bv-engine-grid">') :]
    cols = [m.start() for m in re.finditer(r'<div class="bv-engine-col">', grid)]
    return grid[cols[0] : cols[1]]


def _sum_labels(narr: str) -> list[str]:
    if 'class="bv-md-sum"' not in narr:
        return []
    part = narr[narr.index('class="bv-md-sum"') :]
    part = part[: part.index("<details") if "<details" in part else len(part)]
    return re.findall(r'<div class="bv-label">([^<]*)</div>', part)


def _sum_row(narr: str, label: str) -> str:
    pat = r'<div class="bv-label">' + re.escape(label)
    pat += r'</div><div class="bv-text">(.*?)</div></div>'
    m = re.search(pat, narr)
    return m.group(1) if m else ""


# ─── 1. XSS 회귀 ─────────────────────────────────────────────────────────────

_XSS = [
    "<script>alert(1)</script>",
    "[x](javascript:alert(1))",
    "![i](javascript:alert(1))",
    "<img src=x onerror=alert(1)>",
    '" onmouseover="alert(1)',
    "' onfocus='alert(1)",
    "<a href=\"javascript:alert(1)\">x</a>",
    "| <img src=x onerror=1> | a |\n|---|---|\n| <script>x</script> | `<svg onload=1>` |",
    "### <b onclick=1>t</b>",
    "- <iframe src=javascript:alert(1)>",
    "1. **<svg onload=1>**",
    "`<script>`",
    "**<svg onload=1>**",
    "<style>body{display:none}</style>",
    "https://evil.example/x <https://evil.example>",
    "a `b **c",
    "**unclosed ` tick",
    "&lt;script&gt; 이미 escape된 글자",
    "<SCRIPT>alert(1)</SCRIPT>",
    "x" * 20000 + "<img src=x onerror=1>",
]


def test_xss_payloads_render_as_text_only() -> None:
    outs = _mds([*_XSS, "\n".join(_XSS)])
    for src, html in zip([*_XSS, "joined"], outs, strict=True):
        assert html, src[:40]
        _assert_safe(html)


def test_xss_payload_text_is_kept_escaped_not_dropped() -> None:
    script, link, img, attr, cell = _mds([
        "<script>alert(1)</script>",
        "[x](javascript:alert(1))",
        "![i](javascript:alert(1))",
        '" onmouseover="alert(1)',
        "| h | i |\n|---|---|\n| <img src=x onerror=1> | a |",
    ])
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in script
    assert "[x](javascript:alert(1))" in link  # 링크는 변환하지 않는다(글자 그대로)
    assert "![i](javascript:alert(1))" in img
    assert "&quot; onmouseover=&quot;alert(1)" in attr
    assert "<td>&lt;img src=x onerror=1&gt;</td>" in cell


def test_markup_inside_heading_list_bold_code_is_escaped() -> None:
    head, code, bold, item = _mds(
        ["### <b onclick=1>t</b>", "`<script>`", "**<svg onload=1>**", "- <i>x</i>"]
    )
    assert head == (
        '<div class="bv-md"><div class="bv-md-h">&lt;b onclick=1&gt;t&lt;/b&gt;</div></div>'
    )
    assert "<code>&lt;script&gt;</code>" in code
    assert "<strong>&lt;svg onload=1&gt;</strong>" in bold
    assert "<li>&lt;i&gt;x&lt;/i&gt;</li>" in item


def test_unbalanced_backtick_and_stars_stay_literal() -> None:
    tick, star, mixed, mask = _mds(["a `b", "**c", "**a `b` c**", "Bearer *** password=***"])
    assert tick == '<div class="bv-md"><div>a `b</div></div>'
    assert star == '<div class="bv-md"><div>**c</div></div>'
    assert "<code>b</code>" in mixed and "<strong>" not in mixed
    assert mask == '<div class="bv-md"><div>Bearer *** password=***</div></div>'  # 마스킹 표지 불변


def test_long_lines_render_in_linear_time() -> None:
    out = _node(
        "var t0 = Date.now();"
        "var a = BV.renderMarkdownSafe('| h |\\n|' + new Array(60001).join('-') + 'x');"
        "var b = BV.renderMarkdownSafe(new Array(30001).join('**a'));"
        "var c = BV.renderMarkdownSafe(new Array(30001).join('`') + new Array(30001).join('- '));"
        # 닫히지 않는 굵게 120K자(역추적 회귀)
        "var d = BV.renderMarkdownSafe(new Array(30001).join('**a '));"
        "out = { ms: Date.now() - t0, a: a.length, b: b.length, c: c.length, d: d.length };"
    )
    assert out["ms"] < 2000, out
    assert min(out["a"], out["b"], out["c"], out["d"]) > 0


# ─── 2. 렌더 단위 ────────────────────────────────────────────────────────────


def test_headings_become_bold_subtitles_up_to_four_hashes() -> None:
    html = _md("# h1\n## h2\n### h3\n#### h4\n##### h5\n#nospace")
    assert re.findall(r'<div class="bv-md-h">([^<]*)</div>', html) == ["h1", "h2", "h3", "h4"]
    assert "<div>##### h5</div>" in html and "<div>#nospace</div>" in html


def test_lists_three_kinds() -> None:
    html = _md("- a\n* b\n\n1. c\n2. **d**\n\n3. e")
    assert "<ul><li>a</li><li>b</li></ul>" in html
    assert "<ol><li>1. c</li><li>2. <strong>d</strong></li></ol>" in html
    assert "<ol><li>3. e</li></ol>" in html  # 끊긴 번호 목록도 원문 번호를 유지


def test_bold_and_inline_code() -> None:
    html = _md("앞 **굵게** 중간 `x**y**` 끝")
    assert html == (
        '<div class="bv-md"><div>앞 <strong>굵게</strong> 중간 <code>x**y**</code> 끝</div></div>'
    )


def test_pipe_table_head_rows_cells() -> None:
    html = _md("| a | b | c |\n|---|:---:|---|\n| 1 | 2 | 3 |\n| 4 | `5` | 6 |\n뒤 문장")
    assert html.count("<table>") == 1
    assert html.count("<th>") == 3 and html.count("<tr>") == 3 and html.count("<td>") == 6
    assert "<td><code>5</code></td>" in html
    assert html.endswith("<div>뒤 문장</div></div>")
    no_sep = _md("| a | b |\n| 1 | 2 |")
    assert "<table>" not in no_sep and "<div>| a | b |</div>" in no_sep


def test_toc_only_with_three_or_more_headings() -> None:
    toc = _narr_text()["toc"]
    two, three = _mds(["# a\n## b\n본문", "# a\n## b\n### c"])
    assert "bv-md-toc" not in two
    m = re.search(
        r'<div class="bv-md-toc"><div class="bv-label">([^<]*)</div><ul>(.*?)</ul></div>', three
    )
    assert m and m.group(1) == toc
    assert re.findall(r"<li>([^<]*)</li>", m.group(2)) == ["a", "b", "c"]
    assert three.index("bv-md-toc") < three.index('class="bv-md-h"')


def _sre_clip_marker() -> str:
    tree = ast.parse(_DISPATCHER.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "DEEP_NARRATIVE_CLIPPED" for t in node.targets
        ):
            return str(ast.literal_eval(node.value))
    raise AssertionError("DEEP_NARRATIVE_CLIPPED 없음")


def test_clip_markers_both_kinds_survive() -> None:
    sre_note = _sre_clip_marker().format(n=11042, keep=8000)
    sre, store = _mds([
        "### 제목\n| a | b |\n|---|---|\n| 1 | 2" + sre_note,
        "- 앞부분 `미완" + DEEP_NARRATIVE_CLIPPED_NOTE,
    ])
    assert f"<div>{sre_note.strip()}</div>" in sre
    assert f"<div>{DEEP_NARRATIVE_CLIPPED_NOTE.strip()}</div>" in store
    # W0 픽스처의 실제 8,000자 원문 — 제목 3개 이상(목차) · 표 · 끝 생략 표지
    narr = _view("markdown_narrative", "poll_view")["briefing"]["deep_dive"]["narrative"]
    html = _md(narr)
    _assert_safe(html)
    assert "bv-md-toc" in html and "<table>" in html and "<strong>" in html and "<code>" in html
    assert html.endswith("<div>" + narr.splitlines()[-1] + "</div></div>")
    assert "이하 생략 — 원문" in narr.splitlines()[-1]


def test_empty_input_renders_nothing() -> None:
    assert _mds(["", None, "\n\n   \n", "\r\n"]) == ["", "", "", ""]
    assert _node("out = BV.renderMarkdownSafe();") == ""


# ─── 3. HolmesGPT 칸 — 4줄 요약 · 원문 접힘 ──────────────────────────────────


def _poll_with_action() -> dict[str, Any]:
    view = _view("markdown_narrative", "poll_view")
    dd = view["briefing"]["deep_dive"]
    marker = dd["recommendation"]["items"][0]
    dd["recommendation"]["items"] = [marker, "커넥션 풀 점유 트랜잭션 확인", "DB 담당 확인 요청"]
    return view


def test_four_line_summary_then_folded_raw() -> None:
    narr = _hg_narr(_detail(_poll_with_action()))
    rows = _narr_text()["rows"]
    assert _sum_labels(narr) == [rows["cause"], rows["basis"], rows["action"], rows["limits"]]
    assert _sum_row(narr, rows["action"]) == "커넥션 풀 점유 트랜잭션 확인 · DB 담당 확인 요청"
    assert _sum_row(narr, rows["basis"]) == "tool:apm_resource_pool"
    assert "<strong>커넥션 풀 점유 트랜잭션 확인</strong>" in _sum_row(narr, rows["cause"])
    assert "조치 권고 없음(" not in narr[: narr.index("bv-md-raw")]
    # 원문은 요약 아래 다시 접힘(open 없음) · 「AI 소견」 배지 · 안전 렌더
    assert narr.index("bv-md-sum") < narr.index("bv-md-raw")
    assert re.search(
        r'<details class="bv-md-raw" data-bv-key="narr-hg-raw">'
        r'<summary>원문 <span class="bv-ai">AI 소견</span>',
        narr,
    )
    assert not re.search(r"<details[^>]*\sopen", narr)
    assert "bv-md-toc" in narr and "<table>" in narr
    assert 'class="bv-opinion"' not in narr  # 종전 평문 블록은 쓰지 않는다


def test_summary_skips_missing_lines_and_no_action_marker() -> None:
    rows = _narr_text()["rows"]
    # 대응 = 「권고 없음」 표지뿐
    narr = _hg_narr(_detail(_view("markdown_narrative", "poll_view")))
    assert _sum_labels(narr) == [rows["cause"], rows["basis"], rows["limits"]]
    view = _view("markdown_narrative", "poll_view")
    dd = view["briefing"]["deep_dive"]
    dd.pop("cause")
    dd["limitations"] = []
    view["briefing"]["engine_agreement"]["holmesgpt"]["basis"] = []
    narr = _hg_narr(_detail(view))
    # 줄이 하나도 없으면 요약 칸 없이 원문만
    assert "bv-md-sum" not in narr and "bv-md-raw" in narr


def test_narrative_absent_shows_summary_with_neutral_note() -> None:
    text = _narr_text()
    view = _view("markdown_narrative", "view")   # 저장 32KB 통과본 — narrative 키 없음
    assert "narrative" not in view["briefing"]["deep_dive"]
    narr = _hg_narr(_detail(view))
    assert "bv-md-raw" not in narr
    assert "[가설] ### 장애 분석 결과 보고서" in narr
    assert f'<div class="bv-meta">{text["noRaw"]}</div>' in narr
    for word in ("32KB", "저장 상한", "옛 레코드", "삭제"):   # 원문이 없는 이유는 단정하지 않는다
        assert word not in text["noRaw"]
    assert _sum_labels(narr) == [text["rows"]["cause"], text["rows"]["basis"]]


def test_store_clipped_narrative_keeps_marker_in_raw() -> None:
    view = _view("markdown_narrative", "poll_view")
    dd = view["briefing"]["deep_dive"]
    dd["narrative"] = dd["narrative"][:2000] + DEEP_NARRATIVE_CLIPPED_NOTE
    narr = _hg_narr(_detail(view))
    raw = narr[narr.index("bv-md-raw") :]
    assert f"<div>{DEEP_NARRATIVE_CLIPPED_NOTE.strip()}</div>" in raw


# ─── 4. 플레이북 칸 — 원문 대신 안내 ──────────────────────────────────────────


def test_playbook_column_points_to_synthesis_when_on() -> None:
    view = _view("markdown_narrative", "poll_view")
    syn_narr = view["briefing"]["playbook"]["synthesis"]["narrative"]
    head = _node("out = BV.PB_TEXT.summaryHead;")
    on = _detail(view)
    col = _pb_column(on)
    assert 'data-bv-key="narr-pb"' not in on
    assert syn_narr not in col
    assert _narr_text()["seeSynthesis"].replace("{head}", head) in col
    # 링크가 아니라 텍스트 안내(종합 블록이 접힘 영역으로 옮겨져도 깨지지 않게)
    assert "<a " not in col
    off = _detail(view, on=False)
    pb_off = _block(off, 'data-bv-key="narr-pb"')
    assert syn_narr in pb_off and 'class="bv-opinion"' in pb_off
    assert "bv-md" not in off   # off는 새 렌더를 전혀 쓰지 않는다
    view["briefing"]["playbook"]["synthesis"].pop("narrative")
    assert "AI 소견을 보세요" not in _pb_column(_detail(view))


# ─── 4b. CSS — 긴 원문에서도 표가 접히지 않는다 ───────────────────────────────


def test_md_children_do_not_shrink_inside_scroll_box() -> None:
    """`.bv-md`(세로 flex · max-height · overflow auto) 자식이 flex 축소로 높이 0이 되지 않는다."""
    css = re.sub(r"/\*.*?\*/", "", _STYLE.read_text(encoding="utf-8"), flags=re.S)
    box = re.search(r"\.bv-md\s*\{([^}]*)\}", css)
    assert box and "max-height" in box.group(1) and "overflow: auto" in box.group(1)
    if "flex" in box.group(1):
        assert re.search(r"\.bv-md\s*>\s*\*\s*\{[^}]*flex-shrink:\s*0", css)


# ─── 5. 사용자 뷰 — 가린 값 불변 ─────────────────────────────────────────────


def test_user_view_render_keeps_masks_and_no_secret() -> None:
    op = _view("user_view_secret", "view")
    user = _view("user_view_secret", "user_view")
    html = _detail(user)
    narr = _hg_narr(html)
    assert "bv-md-raw" in narr
    for secret in ("abc123", "eyJhbGciOiJIUzI1NiJ9", "c2lnbmF0dXJl"):
        assert secret in json.dumps(op, ensure_ascii=False)   # 운영자 원문에는 있다(픽스처 전제)
        assert secret not in html
    assert "<li>로그 발췌: 연계 호출 헤더 Authorization: Bearer ***</li>" in narr
    assert "<li>설정 발췌: password=***</li>" in narr
