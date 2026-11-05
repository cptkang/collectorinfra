"""D-352 부기 5 — 장애 진단 화면의 마크다운 기호(`**`)·JSON 원문 노출 정돈.

- 공용 렌더(`briefing-view.js`): `parseLooseJson`(잘린 JSON 복구) · `excerptHtml`(JSON → 항목·표
  · 마크다운 · 원문) · `plainText`(textContent용 기호 제거)
  · `inlineHtml`(escape 먼저 → 굵게·코드만).
- `renderBody`는 `summaryView` 켜짐에서만 한 줄 문장의 `**`를 굵게로 바꾼다
  (꺼짐 비트 동일은 plans/199 가드가 본다).
- 장애 진단(`diagnosis-view.js`): 켜짐이면 도구 발췌·AI 메모·실행 기록 한 줄이 정돈된다
  · 꺼짐이면 종전 그대로.
- XSS: 출력 태그는 허용 목록 · 속성은 고정 class뿐(원문은 escape된 글자로만 남는다).
"""

from __future__ import annotations

import json
import re
from typing import Any

from tests.test_api.test_ui_plan187_w15_diagnosis import _node as _dv_node
from tests.test_plan199_w4_narrative import _node as _bv_node

_ALLOWED_TAGS = {
    "div", "span", "strong", "code", "pre", "ul", "ol", "li", "dl", "dt", "dd",
    "table", "thead", "tbody", "tr", "th", "td",
}
_TAG = re.compile(r"<(/?)([A-Za-z][A-Za-z0-9]*)([^>]*)>")
_ATTR = re.compile(r'^( class="[a-z0-9 -]+")?$')

_XSS = '<img src=x onerror=alert(1)>'
_ROWS_JSON = json.dumps(
    {"host": "web01", "items": [{"name": "cpu", "value": 96.5}, {"name": "mem", "value": 71}],
     "note": _XSS},
    ensure_ascii=False,
)


_GRID = json.dumps({"columns": ["a", "b"], "rows": [[1, 2], [3, None]]})


def _assert_safe(html: str) -> None:
    for _slash, tag, attrs in _TAG.findall(html):
        assert tag in _ALLOWED_TAGS, tag
        assert _ATTR.match(attrs), attrs
    assert "<img" not in html


def _js(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


# ── 공용 렌더 순수 함수 ──────────────────────────────────────────────


def test_parse_loose_json_complete_truncated_and_non_json() -> None:
    truncated = _ROWS_JSON[: _ROWS_JSON.index('"mem"') + 8] + "…"
    out = _bv_node(
        f"""
out = {{
    whole: BV.parseLooseJson({_js(_ROWS_JSON)}),
    cut: BV.parseLooseJson({_js(truncated)}),
    text: BV.parseLooseJson("pool=20 active=3"),
    scalar: BV.parseLooseJson("42"),
    broken: BV.parseLooseJson('{{"msg": "아주 긴 문장이 잘려'),
}};
"""
    )
    assert out["whole"]["cut"] is False and out["whole"]["value"]["host"] == "web01"
    # 잘린 원문은 마지막 완결 값까지 닫아 읽는다
    assert out["cut"]["cut"] is True
    assert out["cut"]["value"] == {
        "host": "web01", "items": [{"name": "cpu", "value": 96.5}, {"name": "mem"}],
    }
    assert out["text"] is None and out["scalar"] is None and out["broken"] is None


def test_excerpt_html_renders_json_as_items_and_table_without_braces() -> None:
    truncated = _ROWS_JSON[: _ROWS_JSON.index('"mem"') + 8] + "…"
    out = _bv_node(
        f"""
out = {{
    json: BV.excerptHtml({_js(_ROWS_JSON)}),
    cut: BV.excerptHtml({_js(truncated)}),
    grid: BV.excerptHtml({_js(_GRID)}),
    md: BV.excerptHtml("## 결론\\n- **커넥션 풀** 고갈"),
    raw: BV.excerptHtml({_js(_XSS + " pool=20")}),
    empty: BV.excerptHtml("  "),
}};
"""
    )
    html = out["json"]
    assert '<dl class="bv-data">' in html and "<dt>host</dt><dd>web01</dd>" in html
    assert '<table class="bv-data-tbl">' in html and "<th>name</th><th>value</th>" in html
    assert "<td>96.5</td>" in html
    assert '{"' not in html and '":' not in html          # JSON 기호가 화면에 남지 않는다
    assert "&lt;img src=x onerror=alert(1)&gt;" in html    # 값은 escape된 글자로
    assert "이하 잘림" in out["cut"] and "이하 잘림" not in html
    assert "<th>a</th><th>b</th>" in out["grid"] and "<td>3</td>" in out["grid"]
    assert '<div class="bv-md-h">결론</div>' in out["md"]
    assert "<strong>커넥션 풀</strong>" in out["md"]
    assert out["raw"].startswith('<pre class="bv-data-raw">') and "&lt;img" in out["raw"]
    assert out["empty"] == ""
    for key in ("json", "cut", "grid", "md", "raw"):
        _assert_safe(out[key])


def test_excerpt_html_caps_rows_and_columns() -> None:
    rows = [{f"c{j}": j for j in range(10)} for _ in range(25)]
    out = _bv_node(f"out = BV.excerptHtml({_js(json.dumps(rows))});")
    assert out.count("<tr>") == 1 + 20
    assert "외 5건" in out and "열 2개 생략" in out


def test_plain_text_and_inline_html() -> None:
    out = _bv_node(
        """
out = {
    plain: BV.plainText("### 판단\\n**DB 커넥션** 고갈 — `pool=20` · password=***"),
    inline: BV.inlineHtml("**굵게** <b>x</b> `코드`"),
    compact: BV.compactText({ a: 1, b: [1, 2], c: { d: null } }),
};
"""
    )
    assert out["plain"] == "판단\nDB 커넥션 고갈 — pool=20 · password=***"
    assert out["inline"] == "<strong>굵게</strong> &lt;b&gt;x&lt;/b&gt; <code>코드</code>"
    assert out["compact"] == "a: 1 · b: 1, 2 · c: d: —"


def test_render_body_bold_only_when_summary_view_on() -> None:
    view = {"status": "done", "investigation_id": "inv-md",
            "briefing_lines": [{"label": "원인 가설", "text": "[가설] **DB 커넥션 풀** 고갈"}]}
    out = _bv_node(
        f"""
var v = {_js(view)};
out = {{ on: BV.renderBody(v, {{ mode: "detail", summaryView: true }}),
        off: BV.renderBody(v, {{ mode: "detail" }}),
        after: BV.inlineHtml("x") && BV.renderBody(v, {{ mode: "detail" }}) }};
"""
    )
    assert "<strong>DB 커넥션 풀</strong>" in out["on"] and "**" not in out["on"]
    assert "**DB 커넥션 풀**" in out["off"] and "<strong>" not in out["off"]
    assert out["after"] == out["off"]   # 켜짐 렌더 뒤에도 정돈 플래그가 새지 않는다


# ── 장애 진단 ─────────────────────────────────────────────────────────


def test_tool_label_text() -> None:
    out = _dv_node(
        """
out = [
    DV.toolLabelText('polestar_metric {"host": "web01", "metrics": ["cpu", "mem"], "w": {"m": 5}}'),
    DV.toolLabelText("apm_resource_pool {A1}"),
    DV.toolLabelText("polestar_alarm {}"),
    DV.toolLabelText("도구"),
];
"""
    )
    assert out == [
        "polestar_metric — host: web01 · metrics: cpu, mem · w: m=5",
        "apm_resource_pool {A1}",
        "polestar_alarm",
        "도구",
    ]


_EVENTS = [
    {"seq": 1, "at": 1792395150.1, "engine": "playbook", "kind": "tool_start", "step": "S2",
     "tool": "polestar_metric", "label": 'polestar_metric {"host": "web01"}'},
    {"seq": 2, "at": 1792395151.3, "engine": "playbook", "kind": "tool_end", "step": "S2",
     "tool": "polestar_metric", "label": 'polestar_metric {"host": "web01"}', "status": "success",
     "elapsed_ms": 1200, "rows": 2, "excerpt": _ROWS_JSON},
    {"seq": 3, "at": 1792395152.0, "engine": "holmes", "kind": "ai_note",
     "label": "HolmesGPT 1회차 메모", "note": "**커넥션 대기** 의심\n- 풀 `20` 소진"},
]


def _diag_script(summary: bool) -> str:
    return f"""
routes[API + "/active"] = {{ status: 200, body: {{ items: [] }} }};
routes[API + "/inv-1"] = {{ status: 200, body: running("inv-1") }};
var rootEl = new Node("div");
var ctl = DV.create({{
    root: rootEl, tabButton: new Node("button"), badge: new Node("span"), operator: true,
    summaryView: {str(summary).lower()},
    headers: function () {{ return {{}}; }}, onActivate: function () {{}},
    onOpenConversation: function () {{}}, analyzeFor: function () {{ return null; }},
    deps: {{ document: document, fetch: fakeFetch, EventSource: FakeES, setTimeout: setT,
        clearTimeout: clearT, now: function () {{ return clock; }}, storage: storage }},
}});
await ctl.probe();
runTimers(1000);
ctl.open("inv-1");
await flush();
runTimers(1000);
var es = liveES()[0];
es.onopen();
es.emit("activity", {{ investigation_id: "inv-1", status: "running", next_seq: 3, truncated: false,
    source: "live", events: {_js(_EVENTS)}, phase: "tools" }});
var tab = ctl.tab("inv-1");
var rich = findAll(tab.dom.panel, "dv-excerpt--rich").map(function (n) {{ return n.innerHTML; }});
var pres = findAll(tab.dom.panel, "dv-excerpt")
    .filter(function (n) {{ return n.tagName === "pre"; }})
    .map(function (n) {{ return n.textContent; }});
out = {{ rich: rich, pres: pres, texts: texts(tab.dom.panel, "dv-text"),
        qtexts: texts(tab.dom.panel, "dv-q-text"),
        opinions: findAll(tab.dom.panel, "dv-opinion-text")
            .map(function (n) {{ return n.innerHTML || n.textContent; }}) }};
"""


def test_diagnosis_screen_cleans_excerpt_note_and_label_when_on() -> None:
    out = _dv_node(_diag_script(True))
    rich = "".join(out["rich"])
    assert '<dl class="bv-data">' in rich and '{"' not in rich
    assert "&lt;img src=x onerror=alert(1)&gt;" in rich
    assert "<strong>커넥션 대기</strong>" in rich           # AI 메모 펼침
    assert out["pres"] == []                                # 고정폭 원문 블록 없음
    assert "polestar_metric — host: web01" in out["texts"]
    assert any(t.startswith("HolmesGPT 1회차 메모 — 커넥션 대기 의심") for t in out["texts"])
    assert not any("**" in t or '{"' in t for t in out["texts"] + out["qtexts"])
    assert any("<strong>커넥션 대기</strong>" in o for o in out["opinions"])
    for html in out["rich"] + out["opinions"]:
        _assert_safe(html)


def test_diagnosis_screen_unchanged_when_off() -> None:
    out = _dv_node(_diag_script(False))
    assert out["rich"] == []
    assert _ROWS_JSON in out["pres"]
    assert 'polestar_metric {"host": "web01"}' in out["texts"]
    assert any("**커넥션 대기**" in t for t in out["texts"])
