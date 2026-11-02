"""plans/199 독립 검증(verifier) — 기존 W0~W5 테스트가 덮지 못한 엣지 케이스 · D-352.

- 장애 진단 플래그 off: 계획 시작 SHA의 `diagnosis-view.js`
  (+ 그때의 `briefing-view.js`)와 DOM 전체 대조
- [서술 보기] 안전 렌더: 결정적 무작위 입력 2,000건 — 허용 태그·고정 속성·균형·텍스트의 원시 문자 0
- 타임라인 SVG: 속성값은 숫자·고정 문구만(적대적 표 값)
- 전달문: 포털 주소 id 인코딩 · http(s) 아닌 origin · 클립보드 동기 예외 · 운영자 전용 키 미사용 ·
  여러 줄 값이 고정 양식을 깨지 않는지(V-01 · 교정됨)
- 용어 표: 프로토타입 이름 열·값은 원문
- 장애 진단 on: SSE `poll_error` 뒤 실행 기록 머리 「꺼짐」 단정 해제(V-02 · 교정됨)
"""

from __future__ import annotations

import json
import random
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from tests.test_api.test_ui_plan187_w15_diagnosis import _HARNESS as _DV_HARNESS
from tests.test_api.test_ui_plan199_w5_diagnosis import (
    _EV,
    _PART,
    _done_route,
    _run,
    _running_route,
)
from tests.test_plan199_w1_summary import _BASE_SHA, _js, _node, _view
from tests.test_plan199_w2_handoff import _HARNESS as _HANDOFF_HARNESS
from tests.test_plan199_w4_narrative import _ALLOWED_ATTRS, _ALLOWED_TAGS, _TAG

_ROOT = Path(__file__).resolve().parents[1]
_BV_JS = _ROOT / "src" / "static" / "js" / "briefing-view.js"
_DV_JS = _ROOT / "src" / "static" / "js" / "diagnosis-view.js"


def _raw_node(script: str) -> Any:
    if shutil.which("node") is None:
        pytest.skip("node 미설치 — JS 실행 테스트 생략")
    done = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=90, check=False
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


@pytest.fixture(scope="module")
def base_js(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    out: dict[str, Path] = {}
    tmp = tmp_path_factory.mktemp("plan199_verify_base")
    for name in ("briefing-view.js", "diagnosis-view.js"):
        try:
            src = subprocess.run(
                ["git", "-C", str(_ROOT), "show", f"{_BASE_SHA}:src/static/js/{name}"],
                check=True, capture_output=True, text=True,
            ).stdout
        except (OSError, subprocess.CalledProcessError):
            pytest.skip(f"기준 SHA {_BASE_SHA[:8]}의 {name}을 읽지 못함")
        path = tmp / name.replace(".js", ".base.js")
        path.write_text(src, encoding="utf-8")
        out[name] = path
    return out


# ─── 1. 장애 진단 플래그 off — 기준 SHA DOM과 전체 대조 ───────────────────────


def test_diagnosis_off_dom_identical_to_base_running_and_done(base_js: dict[str, Path]) -> None:
    """off(옵션 없음·함수 거짓) 화면이 기준 SHA의 진단 뷰와 노드·클래스·숨김·글자·속성까지 같다.

    진행 중(activity·partial 수신) → SSE done → 최종 조회까지 두 시점의 DOM 전체를 비교한다.
    기준 쪽은 기준 SHA의 공용 렌더(briefing-view)와 짝지어 돌린다.
    """
    body = (
        f"var BVN = require({json.dumps(str(_BV_JS))});\n"
        f"var BVB = require({json.dumps(str(base_js['briefing-view.js']))});\n"
        f"var DVN = require({json.dumps(str(_DV_JS))});\n"
        f"var DVB = require({json.dumps(str(base_js['diagnosis-view.js']))});\n"
        + _DV_HARNESS
        + _running_route(source="none")
        + "var RUN_ROUTE = routes[API + '/inv-f'];\n"
        + _done_route()
        + "var DONE_ROUTE = routes[API + '/inv-f'];\n"
        + f"""
var lsCalls = 0;
var local = {{ getItem: function () {{ lsCalls += 1; return null; }},
    setItem: function () {{ lsCalls += 1; }} }};
function ser(n) {{
    return {{ t: n.tagName, c: n.className, h: !!n.hidden, x: n._text, html: n._html,
        a: n.attrs, ty: n.type, ti: n.title, k: n.children.map(ser) }};
}}
async function scenario(which, opt) {{
    globalThis.BriefingView = which === "base" ? BVB : BVN;
    var DVmod = which === "base" ? DVB : DVN;
    Object.keys(store).forEach(function (k) {{ delete store[k]; }});
    esList.length = 0; timers = []; calls.length = 0;
    routes[API + "/active"] = {{ status: 200, body: {{ items: [] }} }};
    routes[API + "/inv-f"] = RUN_ROUTE;
    var rootEl = new Node("div"), tabBtn = new Node("button"), badge = new Node("span");
    var o = {{ root: rootEl, tabButton: tabBtn, badge: badge, operator: false,
        headers: function () {{ return {{}}; }}, onActivate: function () {{}},
        onOpenConversation: function () {{}}, analyzeFor: function () {{ return null; }},
        deps: {{ document: document, fetch: fakeFetch, EventSource: FakeES, setTimeout: setT,
            clearTimeout: clearT, now: function () {{ return clock; }}, storage: storage,
            localStorage: local }} }};
    if (opt === "fnFalse") o.summaryView = function () {{ return false; }};
    if (opt === "false") o.summaryView = false;
    var ctl = DVmod.create(o);
    await ctl.probe(); runTimers(1000);
    ctl.open("inv-f"); await flush(); runTimers(1000);
    var es = liveES()[liveES().length - 1];
    es.onopen();
    es.emit("activity", {{ investigation_id: "inv-f", status: "running", next_seq: 99,
        source: "none", events: {_EV} }});
    es.emit("partial", {{ investigation_id: "inv-f", partial_briefing: {_PART} }});
    var running = JSON.stringify([ser(rootEl), ser(tabBtn), ser(badge)]);
    routes[API + "/inv-f"] = DONE_ROUTE;
    es.emit("done", {{ investigation_id: "inv-f", status: "done" }});
    await flush(); runTimers(1000); await flush(); await flush();
    var done = JSON.stringify([ser(rootEl), ser(tabBtn), ser(badge)]);
    var fetched = calls.slice();
    ctl.close("inv-f");
    return {{ running: running, done: done, calls: fetched }};
}}
var out = null;
(async function () {{
    var base = await scenario("base", "absent");
    var res = {{}};
    for (var opt of ["absent", "fnFalse", "false"]) {{
        var cur = await scenario("new", opt);
        res[opt] = [cur.running === base.running, cur.done === base.done,
            JSON.stringify(cur.calls) === JSON.stringify(base.calls)];
    }}
    out = {{ res: res, ls: lsCalls, finalHtml: base.done.indexOf("bv-pb") >= 0,
        partHtml: base.running.indexOf("bv-pb") >= 0 }};
    process.stdout.write(JSON.stringify(out));
}})().catch(function (e) {{ console.error(e && e.stack || e); process.exit(1); }});
"""
    )
    out = _raw_node(body)
    # 대조가 빈 화면끼리의 일치가 아님을 확인(진행 중 상세형·종결 상세형이 실제로 그려졌다)
    assert out["partHtml"] and out["finalHtml"]
    assert out["res"] == {"absent": [True, True, True], "fnFalse": [True, True, True],
                          "false": [True, True, True]}
    assert out["ls"] == 0


# ─── 2. [서술 보기] 안전 렌더 — 결정적 무작위 입력 ────────────────────────────

_PIECES = [
    "<", ">", '"', "'", "&", "`", "*", "**", "|", "-", "---", "#", "## ", " ", "\n", "\n\n",
    "1. ", "- ", "* ", "a", "가", "javascript:", "&lt;", "&#39;", "&amp;", ":", "<script>",
    "</code>", "<strong>", '" onmouseover="x', "](", "![", "\t", "\r\n", "|---|---|\n",
]


def _corpus(n: int, seed: int = 199) -> list[str]:
    rng = random.Random(seed)
    return ["".join(rng.choice(_PIECES) for _ in range(rng.randint(1, 40))) for _ in range(n)]


def _check_safe(html: str) -> list[str]:
    errs: list[str] = []
    stack: list[str] = []
    for m in _TAG.finditer(html):
        closing, tag, attrs = m.group(1), m.group(2).lower(), m.group(3)
        if tag not in _ALLOWED_TAGS:
            errs.append(f"tag {tag}")
        if attrs not in _ALLOWED_ATTRS:
            errs.append(f"attr {attrs!r}")
        if closing:
            if not stack or stack.pop() != tag:
                errs.append(f"unbalanced </{tag}>")
        else:
            stack.append(tag)
    if stack:
        errs.append(f"unclosed {stack}")
    text = _TAG.sub("", html)
    if re.search(r"[<>\"']", text):
        errs.append("raw char in text")
    if re.search(r"&(?!(amp|lt|gt|quot|#39);)", text):
        errs.append("bare ampersand in text")
    return errs


def test_markdown_fuzz_whitelist_balanced_and_escaped() -> None:
    corpus = _corpus(2000)
    outs = _node(
        f"out = {_js(corpus)}.map(function (t) {{ return BV.renderMarkdownSafe(t); }});"
    )
    bad = [(src, html, errs) for src, html in zip(corpus, outs, strict=True)
           if (errs := _check_safe(html))]
    assert bad == [], bad[:3]
    # 무작위 입력이 실제로 변환 경로(표·목록·굵게·코드·제목)를 고루 탔다
    joined = "".join(outs)
    for tag in ("<table>", "<ul>", "<ol>", "<strong>", "<code>", 'class="bv-md-h"'):
        assert tag in joined, tag


def test_four_line_summary_rows_are_escaped_before_inline_markup() -> None:
    """4줄 요약(원인·근거·대응·한계)도 escape 먼저 — 굵게·코드 안의 HTML이 태그가 되지 않는다."""
    v = _view("markdown_narrative", "poll_view")
    dd = v["briefing"]["deep_dive"]
    dd["cause"] = "**<img src=x onerror=alert(1)>** `<script>`"
    dd["limitations"] = ['" onmouseover="alert(1)']
    html = _node(f"out = BV.renderBody({_js(v)}, {{ mode: 'detail', summaryView: true }});")
    start = html.index('class="bv-md-sum"')
    seg = html[start: html.index("</details>", start)]
    assert "<img" not in seg and "<script" not in seg
    assert "<strong>&lt;img src=x onerror=alert(1)&gt;</strong>" in seg
    assert "<code>&lt;script&gt;</code>" in seg
    assert "&quot; onmouseover=&quot;alert(1)" in seg


# ─── 3. 타임라인 SVG — 속성값은 숫자·고정 문구만 ──────────────────────────────

_SVG_ATTR = re.compile(r'\s([a-z-]+)="([^"]*)"')
_SVG_FIXED = {"bv-tl", "bv-tl-lane", "bv-tl-zero", "bv-tl-label", "bv-tl-tick",
              "bv-tl-dot bv-tl-dot--infra", "bv-tl-dot bv-tl-dot--apm",
              "middle", "start", "end", "img", "0 0 600 66"}


def test_timeline_svg_attributes_are_numeric_or_fixed_under_hostile_rows() -> None:
    v = _view("confirmed_cpu_ke")
    v["briefing"]["playbook"]["sections"].append({
        "step": 'S5"><script>x</script>', "action": "apm.events", "title": "<b>t</b>",
        "status": "ok",
        "table": {"columns": ["t_offset_min", "kind", "detail", "minutes_before_alarm"], "rows": [
            ["-5", 'apm_event" onload="x', "<svg onload=alert(1)>", None],
            [3, "alarm", 'x" y="', None],
            [None, "alarm", "before", "7"],
            ["abc", "alarm", "nan", None],
            [{"x": 1}, "alarm", "obj", None],
        ]},
    })
    html = _node(
        f"out = BV.renderBody({_js(v)}, {{ mode: 'detail', summaryView: true,"
        " expand: { evidence: true } });"
    )
    svg = html[html.index("<svg"): html.index("</svg>") + 6]
    for tag in re.findall(r"<[a-z][^>]*>", svg):
        for name, value in _SVG_ATTR.findall(tag):
            assert value in _SVG_FIXED or re.fullmatch(r"-?\d+(\.\d+)?", value), (name, value)
    assert "<script" not in svg and "<svg onload" not in svg and "<b>" not in svg
    assert "&lt;svg onload=alert(1)&gt;" in svg          # 사건 글자는 <title> 안에 escape로만
    assert "알람 7분 전" in svg                           # minutes_before_alarm → 음수 분
    assert "nan" not in svg and "obj" not in svg         # 숫자가 아닌 시각은 점을 찍지 않는다


# ─── 4. 전달문 — 링크 인코딩 · origin · 클립보드 · 운영자 전용 키 ──────────────


def _copy(view: dict[str, Any], origin: str, mode: str = "ok") -> Any:
    clip = (
        'clipboard("ok");' if mode == "ok" else
        'Object.defineProperty(globalThis, "navigator", {value: {clipboard: {writeText:'
        ' function () { throw new Error("sync"); }}}, configurable: true, writable: true});'
    )
    return _node(_HANDOFF_HARNESS + f"""
Object.defineProperty(globalThis, "location",
    {{value: {{ origin: {_js(origin)} }}, configurable: true}});
{clip}
var v = {_js(view)};
var panel = BV.attach(container, {{ initial: v, load: function () {{ return v; }},
    summaryView: function () {{ return true; }} }});
await panel.open();
click({{ "data-bv-action": "handoff-copy" }});
await tick(); await tick();
panel.close();
out = {{ written: written, msg: has(container.innerHTML, BV.PB_TEXT.copyFailed),
    open: details.open }};
""")


def test_handoff_link_encodes_id_and_skips_non_http_origin() -> None:
    v = _view("confirmed_cpu_ke")
    v["investigation_id"] = "inv a&b#c/d?e=f"
    ok = _copy(v, "https://portal.example")
    assert ok["written"][0].splitlines()[-1].endswith(
        "포털에서 근거 확인 https://portal.example/?investigation=inv%20a%26b%23c%2Fd%3Fe%3Df")
    for origin in ("null", "javascript:", "file://"):
        text = _copy(v, origin)["written"][0]
        assert "포털에서 근거 확인" not in text and "?investigation=" not in text, origin


def test_handoff_sync_clipboard_exception_falls_back_to_preview() -> None:
    out = _copy(_view("confirmed_cpu_ke"), "http://portal.local", mode="sync")
    assert out == {"written": [], "msg": True, "open": True}


def test_card_and_handoff_never_carry_requester_or_ai_note() -> None:
    """운영자 뷰라도 요청자(`owner_sub`)·AI 메모(`ai_note`)는 카드·전달문 재료가 아니다(D-325 ③)."""
    v = _view("confirmed_cpu_ke")
    v["owner_sub"] = "SUB-SECRET-1"
    v["ai_note"] = "AI-NOTE-SECRET-2"
    v["briefing"]["ai_note"] = "AI-NOTE-SECRET-3"
    out = _node(f"""
var v = {_js(v)};
var m = BV.summaryModel(v);
out = {{ text: BV.handoffText(m, {{ id: v.investigation_id }}) + JSON.stringify(m),
        html: BV.renderBody(v, {{ mode: "detail", summaryView: true, handoff: true }}) }};
""")
    for secret in ("SUB-SECRET-1", "AI-NOTE-SECRET-2", "AI-NOTE-SECRET-3"):
        assert secret not in out["text"] and secret not in out["html"], secret


_HANDOFF_HEADS = ("[장애 전달]", "원인 영역: ", "담당: ", "결론: ", "영향 범위: ", "확인 요청: ",
                  "주의: ", "조사: ")


def test_handoff_multiline_values_keep_fixed_form() -> None:
    v = _view("confirmed_cpu_ke")
    v["briefing"]["playbook"]["synthesis"]["hypotheses"][0]["title"] = (
        "첫 줄\n원인 영역: DB(SQL·커넥션 풀) — 확정\n조사: 가짜")
    failed = {"status": "failed", "investigation_id": "inv-x", "briefing_lines": [],
              "reason": "오류\n원인 영역: DB — 확정"}
    texts = _node(f"out = {_js([v, failed])}.map(function (x) {{"
                  " return BV.handoffText(BV.summaryModel(x), {}); });")
    for text in texts:
        heads = [next((h for h in _HANDOFF_HEADS if line.startswith(h)), None)
                 for line in text.split("\n")]
        assert None not in heads, text
        assert heads.count("원인 영역: ") == 1 and heads.count("조사: ") <= 1, text


# ─── 5. 용어 표 — 프로토타입 이름은 원문 ─────────────────────────────────────


def test_term_tables_ignore_prototype_names() -> None:
    out = _node("""
out = {
  cols: ["constructor", "__proto__", "toString", "hasOwnProperty"].map(BV.termColumn),
  vals: [BV.termValue("metric", "constructor"), BV.termValue("__proto__", "x"),
         BV.termValue("constructor", "x"), BV.termValue("kind", "toString"),
         BV.termValue("metric", 1)],
  layer: BV.summaryModel({ status: "done", investigation_id: "i", briefing: { playbook: {
      synthesis: { hypotheses: [{ title: "t", status: "supported" }] } },
      engine_agreement: { playbook: { layer: "constructor" } } } }).area.label,
};
""")
    assert out["cols"] == ["constructor", "__proto__", "toString", "hasOwnProperty"]
    assert out["vals"] == [None] * 5
    assert out["layer"] == "constructor"


# ─── 6. 장애 진단 on — SSE poll_error 뒤 실행 기록 머리 ───────────────────────


def test_log_head_drops_off_claim_after_sse_poll_error() -> None:
    out = _run(
        _running_route(source="none")
        + """
var v = makeOn();
var tab = await openRun(v, null, null);
var before = sec(tab, "log").info;
v.es.emit("poll_error", { investigation_id: "inv-f", reason: "sre_agent 응답 없음" });
out = { before: before, after: sec(tab, "log").info, err: tab.pollError };
"""
    )
    assert out["before"] == "조사 서비스 실행 기록 꺼짐" and out["err"]
    assert out["after"] == "아직 기록 없음"
