"""plans/195 W3 — 「질의응답에서 확인」 화면(P1 장애 진단 단계 카드 · P2 조사 대화 고정 카드).

브라우저 e2e는 돌지 않으므로 D-190 방식으로 고정한다:
    1. `diagnosis-view.js`를 node로 실행(W15 하네스) — 단계 카드에 질의 줄 · 선택 대기 단계 생략 ·
       `requery` 키 없으면 렌더 0 · 범위 밖 비활성+사유 · 진행 중 비활성 · 조회는 주입 인계만 ·
       textContent만.
    2. `briefing-view.js` attach(상세형 · `requery` 옵션) — 「조회한 것」 줄 · 묶음 ·
       구버전 섹션 무변경 · setBusy.
    3. `app.js` `stageInvestigationPrompt` 원문 추출 + 스텁 — 자동 전송 0 · 조사 대화 탭 유지 ·
       확인 바 · 장애 진단 탭이면 그 조사 대화를 연 뒤 채움 · D-183·D-192 두 함수 불변.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from tests.test_api.test_noise_dashboard_api import _js_function_body
from tests.test_api.test_ui_plan187_w15_diagnosis import _node

_ROOT = Path(__file__).resolve().parents[1]
_STATIC = _ROOT / "src" / "static"
_BV_JS = _STATIC / "js" / "briefing-view.js"
_APP_JS = _STATIC / "js" / "app.js"

_HOSTILE = '<img src=x onerror="alert(1)"> web01 "CPU" 사용률'
_WAIT = "폴스타 서버 선택 대기 — 조사 화면에서 후보를 골라 다시 조회"

_REQUERY = [
    {
        "step": "S1",
        "title": "응답시간",
        "prompt": _HOSTILE,
        "path": "apm",
        "source_ref": "apm_bench:x",
        "realtime": True,
        "axis": "apm",
    },
    {
        "step": "S1",
        "title": "응답시간",
        "prompt": "polestar_cm_gp web01 2026-10-27 09:00~10:00 알람 이력",
        "path": "text2sql",
        "source_ref": "gold:y",
        "realtime": False,
        "axis": "polestar",
        "polestar_source": "polestar_cm_gp",
        "disabled_reason": "조회 범위 밖 DB입니다",
    },
    # 선택 대기(후보 블록) 단계 — 서버가 만들지 않지만 와도 그리지 않는다
    {"step": "S4", "title": "호스트 자원", "prompt": "대기 단계 질의", "realtime": False},
]


def _final(requery: list | None) -> dict:
    sections = [
        {"step": "S1", "title": "응답시간", "status": "ok", "finding": "응답 지연"},
        {"step": "S4", "title": "호스트 자원", "status": "skipped", "reason": _WAIT},
    ]
    view: dict[str, Any] = {
        "status": "done",
        "investigation_id": "inv-f",
        "kind": "alarm",
        "briefing_lines": [],
        "briefing": {"playbook": {"id": "fallback", "sections": sections}},
        "activity": {"next_seq": 99, "truncated": False, "events": []},
    }
    if requery is not None:
        view["requery"] = requery
    return view


def _dv_script(final: dict, body: str, stage: bool = True, busy: bool = False) -> str:
    stage_opt = (
        "stagePrompt: function (id, p) { staged.push([id, p]); }, "
        "busy: function () { return busyFlag; },"
        if stage
        else ""
    )
    cands = {"hostname": "web01", "candidates": [], "unchecked": []}
    return f"""
routes[API + "/active"] = {{ status: 200, body: {{ items: [] }} }};
routes[API + "/inv-f"] = {{ status: 200, body: {json.dumps(final, ensure_ascii=False)} }};
routes[API + "/inv-f/host-candidates"] = {{ status: 200, body: {json.dumps(cands)} }};
var staged = []; var busyFlag = {"true" if busy else "false"}; var copied = [];
var clip = {{ writeText: function (t) {{ copied.push(t); return Promise.resolve(); }} }};
var rootEl = new Node("div");
var ctl = DV.create({{ root: rootEl, tabButton: new Node("button"), badge: new Node("span"),
    operator: false, headers: function () {{ return {{}}; }},
    onActivate: function () {{}}, onOpenConversation: function () {{}},
    analyzeFor: function () {{ return null; }},
    linkHost: function () {{ return Promise.resolve({{ ok: true, status: 200, body: {{}} }}); }},
    {stage_opt}
    deps: {{ document: document, fetch: fakeFetch, EventSource: FakeES, setTimeout: setT,
        clearTimeout: clearT, now: function () {{ return clock; }}, storage: storage,
        clipboard: clip }} }});
await ctl.probe();
runTimers(1000);
ctl.open("inv-f");
await flush();
runTimers(1000);
var tab = ctl.tab("inv-f");
await flush();
function attrsOf(n) {{
    var out = [];
    (function walk(x) {{
        Object.keys(x.attrs).forEach(function (k) {{ out.push(x.attrs[k]); }});
        if (x.title) out.push(x.title);
        x.children.forEach(walk);
    }})(n);
    return out;
}}
{body}
"""


# ─── P1 · 장애 진단 단계 카드 ────────────────────────────────────────────


def test_step_card_draws_requery_below_result_and_skips_waiting_step() -> None:
    out = _node(
        _dv_script(
            _final(_REQUERY),
            """
var cards = findAll(tab.dom.flowBody, "dv-card");
var c0 = cards[0];
var kids = c0.children.map(function (n) { return n.className.split(" ")[0]; });
var box = findAll(c0, "dv-requery")[0];
var runs = findAll(box, "rq-run");
out = {
    perCard: cards.map(function (c) { return findAll(c, "dv-requery").length; }),
    hostInWait: findAll(cards[1], "dv-host").length,
    kids: kids,
    head: texts(box, "dv-requery-head"),
    textsAll: texts(box, "rq-text"),
    live: texts(box, "rq-live"),
    disabled: runs.map(function (b) { return b.disabled; }),
    why: texts(box, "rq-why"),
    copies: findAll(box, "rq-copy").length,
    html: htmlWrites.filter(function (n) {
        return /dv-requery|rq-/.test(n.className); }).length,
    attrs: attrsOf(box),
    status: texts(c0, "dv-card-state"),
};
""",
        )
    )
    assert out["perCard"] == [1, 0]  # 선택 대기(후보 블록) 단계에는 그리지 않는다
    assert out["hostInWait"] == 1
    # 결과 줄(dl) 다음에 붙는다
    assert out["kids"].index("dv-card-body") < out["kids"].index("dv-requery")
    assert out["head"] == ["질의응답에서 확인"]
    assert out["textsAll"] == [_HOSTILE, "polestar_cm_gp web01 2026-10-27 09:00~10:00 알람 이력"]
    assert out["live"] == ["지금 값 — 조사 당시와 다를 수 있음"]  # 별도 칸
    assert out["disabled"] == [False, True]
    assert out["why"] == ["조회 범위 밖 DB입니다"]
    assert out["copies"] == 2  # 범위 밖이어도 복사는 허용
    assert out["html"] == 0  # 서버 텍스트는 textContent로만
    assert not any(_HOSTILE in a or "알람 이력" in a or "범위 밖" in a for a in out["attrs"])


def test_run_calls_injected_stage_only_and_copy_uses_clipboard() -> None:
    out = _node(
        _dv_script(
            _final(_REQUERY),
            """
var before = calls.length;
var box = findAll(findAll(tab.dom.flowBody, "dv-card")[0], "dv-requery")[0];
var runs = findAll(box, "rq-run");
runs[0].click();
runs[1].click();   // 범위 밖 — 막힘
findAll(box, "rq-copy")[1].click();
await flush();
out = { staged: staged, copied: copied, msg: texts(box, "rq-msg"),
    newCalls: calls.slice(before) };
""",
        )
    )
    assert out["staged"] == [["inv-f", _HOSTILE]]  # 자동 전송 없이 인계만
    assert out["copied"] == ["polestar_cm_gp web01 2026-10-27 09:00~10:00 알람 이력"]
    assert out["msg"] == ["", "복사했습니다."]
    assert out["newCalls"] == []  # 이 화면은 아무 요청도 보내지 않는다


def test_busy_disables_run_and_set_busy_keeps_blocked() -> None:
    out = _node(
        _dv_script(
            _final(_REQUERY),
            """
var box = findAll(findAll(tab.dom.flowBody, "dv-card")[0], "dv-requery")[0];
var runs = findAll(box, "rq-run");
var whileBusy = runs.map(function (b) { return b.disabled; });
runs[0].click();
busyFlag = false;
// 답변이 끝나면 setBusy(document, false)가 범위 밖 버튼은 그대로 막아 둔다
var fakeRoot = { querySelectorAll: function (sel) { return sel === ".rq-run" ? runs : []; } };
BV.setBusy(fakeRoot, false);
var after = runs.map(function (b) { return b.disabled; });
BV.setBusy(fakeRoot, true);
var again = runs.map(function (b) { return b.disabled; });
out = { whileBusy: whileBusy, staged: staged.length, after: after, again: again };
""",
            busy=True,
        )
    )
    assert out["whileBusy"] == [True, True]
    assert out["staged"] == 0
    assert out["after"] == [False, True]
    assert out["again"] == [True, True]


def test_no_requery_key_or_no_stage_injection() -> None:
    absent = _node(
        _dv_script(
            _final(None),
            'out = { n: findAll(tab.dom.flowBody, "dv-requery").length,'
            ' rq: findAll(tab.dom.flowBody, "rq-item").length };',
        )
    )
    assert absent == {"n": 0, "rq": 0}  # 플래그 off(키 없음) — 종전과 같다
    empty = _node(_dv_script(_final([]), 'out = findAll(tab.dom.flowBody, "dv-requery").length;'))
    assert empty == 0
    nostage = _node(
        _dv_script(
            _final(_REQUERY),
            'var box = findAll(tab.dom.flowBody, "dv-requery")[0];'
            ' out = { run: findAll(box, "rq-run").length, copy: findAll(box, "rq-copy").length };',
            stage=False,
        )
    )
    assert nostage == {"run": 0, "copy": 2}


def test_requery_by_step_pure() -> None:
    out = _node(
        """
out = {
    none: DV.requeryByStep({ status: "done" }),
    bad: DV.requeryByStep({ requery: "x" }),
    by: DV.requeryByStep({ requery: [
        { step: "S1", prompt: "a" }, { step: "S1", prompt: " " },
        { step: "S2", prompt: "b" }, 7 ] }),
};
"""
    )
    assert out["none"] is None and out["bad"] is None
    assert {k: [i["prompt"] for i in v] for k, v in out["by"].items()} == {
        "S1": ["a"],
        "S2": ["b"],
    }


# ─── P2 · 조사 대화 고정 카드(briefing-view attach) ─────────────────────

_BV_HARNESS = r"""
function Node(tag) {
    this.tagName = tag; this.children = []; this._text = ""; this.className = ""; this.attrs = {};
    this.listeners = {}; this.disabled = false; this.type = ""; this.title = "";
    this.isConnected = true; this._html = null;
}
Object.defineProperty(Node.prototype, "textContent", {
    get: function () {
        return this._text + this.children.map(function (c) { return c.textContent; }).join("");
    },
    set: function (v) { this.children = []; this._text = String(v); },
});
Object.defineProperty(Node.prototype, "innerHTML", {
    get: function () { return this._html || ""; },
    set: function (v) { this.children = []; this._text = ""; this._html = String(v); },
});
Node.prototype.appendChild = function (c) { this.children.push(c); return c; };
Node.prototype.setAttribute = function (k, v) { this.attrs[k] = String(v); };
Node.prototype.getAttribute = function (k) { return k in this.attrs ? this.attrs[k] : null; };
Node.prototype.removeAttribute = function (k) { delete this.attrs[k]; };
Node.prototype.addEventListener = function (t, f) {
    (this.listeners[t] = this.listeners[t] || []).push(f); };
Node.prototype.click = function () { var me = this;
    (this.listeners.click || []).forEach(function (f) { f({ target: me }); }); };
Node.prototype.querySelector = function () { return null; };
Node.prototype.querySelectorAll = function () { return []; };
Node.prototype.contains = function () { return true; };
globalThis.document = { createElement: function (t) { return new Node(t); } };
function findAll(node, cls) {
    var out = [];
    (function walk(n) {
        if (n.className && n.className.split(" ").indexOf(cls) >= 0) out.push(n);
        n.children.forEach(walk);
    })(node);
    return out;
}
function texts(node, cls) { return findAll(node, cls).map(function (n) { return n.textContent; }); }
function flush() { return new Promise(function (r) { setTimeout(r, 0); }); }
// 상세형 단계 섹션 <details class="bv-step"> 대역 — 마크업을 해석하지 않으므로 섹션 수만큼 만든다
function panel(view, opts) {
    var bodies = [];
    var steps = [];
    // 실제 마크업 순서 — 런북(details.bv-step.bv-runbook · data-bv-step 없음)이 단계보다 먼저
    var rbBody = new Node("div"); rbBody.className = "bv-step-body";
    var rb = new Node("details"); rb.className = "bv-step bv-runbook";
    rb.querySelector = function (sel) { return sel === ".bv-step-body" ? rbBody : null; };
    steps.push(rb);
    var secs = (view.briefing.playbook.sections || []);
    secs.forEach(function (sec) {
        var body = new Node("div"); body.className = "bv-step-body"; bodies.push(body);
        var d = new Node("details"); d.className = "bv-step";
        d.setAttribute("data-bv-step", sec.step || "");
        d.querySelector = function (sel) { return sel === ".bv-step-body" ? body : null; };
        steps.push(d);
    });
    var box = new Node("div");
    box.ownerDocument = globalThis.document;
    box.querySelectorAll = function (sel) { return sel === "details.bv-step" ? steps : []; };
    var p = BV.attach(box, Object.assign({ initial: view,
        load: function () { return Promise.resolve(view); },
        render: { mode: "detail" } }, opts || {}));
    p.open();
    return { box: box, bodies: bodies, runbook: rbBody, panel: p };
}
"""


def _bv(body: str) -> Any:
    if shutil.which("node") is None:
        pytest.skip("node 미설치 — JS 실행 테스트 생략")
    script = "\n".join(
        [
            f"var BV = require({json.dumps(str(_BV_JS))});",
            _BV_HARNESS,
            "var out = null;",
            "(async function () {",
            body,
            "process.stdout.write(JSON.stringify(out));",
            "})().catch(function (e) { console.error(e && e.stack || e); process.exit(1); });",
        ]
    )
    done = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=30, check=False
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def _pin_view(requery: list | None, queries: bool = True) -> dict:
    sec1: dict[str, Any] = {"step": "S1", "title": "응답시간", "status": "ok", "tools": ["apm_x"]}
    sec2: dict[str, Any] = {"step": "S2", "title": "알람 이력", "status": "ok"}
    if queries:
        sec1["queries"] = [{"tool": "apm_x", "phrase": _HOSTILE}, {"tool": "apm_y"}]
    view: dict[str, Any] = {
        "status": "done",
        "investigation_id": "inv-p",
        "briefing": {"playbook": {"id": "fallback", "sections": [sec1, sec2]}},
    }
    if requery is not None:
        view["requery"] = requery
    return view


def test_pin_draws_queried_lines_and_requery_group() -> None:
    view = _pin_view(_REQUERY[:2])
    out = _bv(
        f"""
var staged = []; var busy = false;
var r = panel({json.dumps(view, ensure_ascii=False)},
    {{ requery: {{ stage: function (p) {{ staged.push(p); }},
        busy: function () {{ return busy; }} }} }});
await flush(); await flush();
var group = findAll(r.box, "bv-requery")[0];
var runs = findAll(group, "rq-run");
runs[0].click(); runs[1].click();
out = {{
    queried: r.bodies.map(function (b) {{ return texts(b, "bv-queried"); }}),
    runbook: r.runbook.children.length,
    head: texts(group, "bv-label"),
    steps: texts(group, "rq-step"),
    prompts: texts(group, "rq-text"),
    live: texts(group, "rq-live"),
    why: texts(group, "rq-why"),
    disabled: runs.map(function (b) {{ return b.disabled; }}),
    staged: staged,
    last: r.box.children[r.box.children.length - 1] === group,
    html: r.box.innerHTML.indexOf("rq-") >= 0 || r.box.innerHTML.indexOf("bv-queried") >= 0,
}};
""",
    )
    assert out["queried"] == [["조회한 것: " + _HOSTILE + " · apm_y"], []]
    assert out["runbook"] == 0  # 런북(data-bv-step 없음)에는 붙지 않는다 — 단계로 매칭
    assert out["head"] == ["질의응답에서 확인"]
    assert out["steps"] == ["S1응답시간", "S1응답시간"]  # 단계 번호·제목으로 위 섹션과 이어 준다
    assert out["prompts"] == [_HOSTILE, _REQUERY[1]["prompt"]]
    assert out["live"] == ["지금 값 — 조사 당시와 다를 수 있음"]
    assert out["why"] == ["조회 범위 밖 DB입니다"]
    assert out["disabled"] == [False, True]
    assert out["staged"] == [_HOSTILE]
    assert out["last"] is True  # 카드 아래
    assert out["html"] is False  # 공용 렌더 문자열(innerHTML)에는 싣지 않는다


def test_pin_without_requery_or_queries_is_unchanged() -> None:
    out = _bv(
        f"""
var old = {json.dumps(_pin_view(None, queries=False), ensure_ascii=False)};
var neu = {json.dumps(_pin_view(None, queries=True), ensure_ascii=False)};
var r = panel(old, {{ requery: {{ stage: function () {{}},
    busy: function () {{ return false; }} }} }});
await flush(); await flush();
// requery 옵션 없음(장애 진단 종결 패널·관제) — 응답에 requery가 있어도 덧붙이지 않는다
var withKey = Object.assign({{}}, neu,
    {{ requery: {json.dumps(_REQUERY[:1], ensure_ascii=False)} }});
var n = panel(withKey, null);
await flush(); await flush();
out = {{
    sameHtml: BV.renderBody(old, {{ mode: "detail" }}) === BV.renderBody(neu, {{ mode: "detail" }}),
    kids: r.box.children.length,
    queried: r.bodies.map(function (b) {{ return b.children.length; }}),
    noOptKids: n.box.children.length,
    noOptQueried: n.bodies.map(function (b) {{ return b.children.length; }}),
}};
""",
    )
    assert out["sameHtml"] is True  # 문자열 렌더는 queries를 읽지 않는다(구버전과 비트 동일)
    assert out["kids"] == 0 and out["queried"] == [0, 0]
    assert out["noOptKids"] == 0 and out["noOptQueried"] == [0, 0]


def test_queried_phrases_pure() -> None:
    out = _bv(
        """
out = [
    BV.queriedPhrases({ step: "S1" }),
    BV.queriedPhrases({ queries: [{ tool: "a" }, { tool: "b", phrase: "B 조회" },
        { tool: "" }, 3] }),
    BV.requeryItems({}),
];
"""
    )
    assert out == [[], ["a", "B 조회"], None]


# ─── app.js · 인계 함수 ─────────────────────────────────────────────────


def _stage(prelude: str, call: str) -> Any:
    if shutil.which("node") is None:
        pytest.skip("node 미설치 — JS 실행 테스트 생략")
    fn = _js_function_body(_APP_JS.read_text(encoding="utf-8"), "stageInvestigationPrompt")
    script = f"""
var log = [];
var INVESTIGATION_ID_RE = /^[A-Za-z0-9._:-]{{1,128}}$/;
var promptEl = {{ value: "", focus: function () {{ log.push("focus"); }} }};
var activeView = "investigation"; var conversationSlot = "investigation";
var parkedConversation = null;
var stored = null; var openOk = true;
function storedInvestigationConversation() {{ return stored; }}
function setActiveView(v) {{ log.push("view:" + v); activeView = v; }}
function openInvestigationConversation(id) {{
    log.push("open:" + id);
    if (openOk) {{ activeView = "investigation"; conversationSlot = "investigation"; }}
    return openOk;
}}
function autoResizeTextarea() {{ log.push("resize:" + (this === promptEl)); }}
function showPromptConfirm(t) {{ log.push("confirm:" + t + ":" + promptEl.value); }}
function handleSend() {{ log.push("SEND"); }}
function stageAlarmPrompt() {{ log.push("ALARM"); }}
{fn}
{prelude}
var ret = {call};
process.stdout.write(JSON.stringify(
    {{ ret: ret, log: log, value: promptEl.value, view: activeView }}));
"""
    done = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=30, check=False
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_stage_in_investigation_tab_keeps_tab_and_shows_confirm() -> None:
    out = _stage("", 'stageInvestigationPrompt("inv-1", "web01 CPU 사용률")')
    assert out["ret"] is True
    assert out["view"] == "investigation"
    assert out["log"] == ["resize:true", "confirm:web01 CPU 사용률:web01 CPU 사용률", "focus"]
    assert "SEND" not in out["log"] and "ALARM" not in out["log"]


def test_stage_from_diagnosis_opens_conversation_then_fills() -> None:
    out = _stage(
        'activeView = "diagnosis"; conversationSlot = "chat";',
        'stageInvestigationPrompt("inv-1", "q")',
    )
    assert out["ret"] is True and out["view"] == "investigation"
    assert out["log"][0] == "open:inv-1"  # 대화를 연 뒤에 채운다
    assert out["log"][1:] == ["resize:true", "confirm:q:q", "focus"]
    # 같은 조사의 조사 대화가 이미 열려 있으면(내려 둔 슬롯) 새로 열지 않고 그 탭으로 옮긴다
    same = _stage(
        'activeView = "diagnosis"; conversationSlot = "chat"; parkedConversation = {};'
        ' stored = { id: "inv-1", thread: "t-1" };',
        'stageInvestigationPrompt("inv-1", "q")',
    )
    assert same["log"][0] == "view:investigation" and not any(
        e.startswith("open:") for e in same["log"]
    )
    # 다른 조사의 대화가 열려 있으면 이 조사로 새로 연다
    other = _stage(
        'activeView = "diagnosis"; conversationSlot = "investigation"; stored = { id: "inv-9" };',
        'stageInvestigationPrompt("inv-1", "q")',
    )
    assert other["log"][0] == "open:inv-1"


def test_stage_refuses_when_open_fails_or_bad_input() -> None:
    busy = _stage(
        'activeView = "diagnosis"; openOk = false;', 'stageInvestigationPrompt("inv-1", "q")'
    )
    assert busy["ret"] is False and busy["value"] == "" and busy["log"] == ["open:inv-1"]
    bad = _stage("", 'stageInvestigationPrompt("bad id!", "q")')
    assert bad["ret"] is False and bad["log"] == []
    empty = _stage("", 'stageInvestigationPrompt("inv-1", "")')
    assert empty["ret"] is False and empty["log"] == []


def test_wiring_and_d183_d192_contracts_unchanged() -> None:
    src = _APP_JS.read_text(encoding="utf-8")
    fn = _js_function_body(src, "stageInvestigationPrompt")
    assert "handleSend" not in fn and "showPromptConfirm(text)" in fn
    assert 'setActiveView("chat")' not in fn  # 질의응답 탭으로 넘어가지 않는다
    # D-192 · D-183 두 함수 본문 불변(계획 시작 SHA 원문과 같다)
    alarm = _js_function_body(src, "stageAlarmPrompt")
    assert alarm == (
        "function stageAlarmPrompt(text) {\n"
        "        if (!text || !promptEl) return;\n"
        '        setActiveView("chat");\n'
        "        promptEl.value = text;\n"
        "        autoResizeTextarea.call(promptEl);   "
        "// 인자 없이 부르면 TypeError 로 확인 바가 뜨지 "
        "않았다(plans/116)\n"
        "        showPromptConfirm(text);\n"
        "        promptEl.focus();\n"
        "    }"
    )
    reuse = _js_function_body(src, "reuseHistoryQuery")
    assert "showPromptConfirm" not in reuse and "handleSend" not in reuse
    # 세 함수 주석이 서로를 가리킨다
    head = src[: src.index("function stageAlarmPrompt(text)")]
    assert "stageInvestigationPrompt" in head[-800:]
    head = src[: src.index("function reuseHistoryQuery(query)")]
    assert "stageInvestigationPrompt" in head[-400:] and "stageAlarmPrompt" in head[-400:]
    head = src[: src.index("function stageInvestigationPrompt(")]
    assert "stageAlarmPrompt" in head[-1200:] and "reuseHistoryQuery" in head[-1200:]
    # 진입 배선 — 고정 카드 · 장애 진단(쓰기·전송 경로는 app.js가 주입)
    pin = _js_function_body(src, "renderInvestigationPin")
    assert "stage: function (prompt) { stageInvestigationPrompt(investigationId, prompt); }" in pin
    assert "busy: function () { return isProcessing; }" in pin
    init = _js_function_body(src, "initDiagnosisView")
    assert "stagePrompt: stageInvestigationPrompt" in init
    # 고친 주석(D-341 부기 24)
    assert "추천 칩은 내지 않는다(W0" not in src
    assert "질의응답에서 확인" in src[src.index("// ─── 조사 대화 (plans/187 W5b") :][:900]
