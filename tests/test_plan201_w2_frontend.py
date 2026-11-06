"""plans/201 W2 — 첫 화면 상황 안내 · 담당별 예시 질의 프론트(`app.js` · `index.html`).

app.js의 W2 구역(`// ─── plans/201 W2 첫 화면 시작 ───` ~ `끝`)을 잘라 작은 가짜 DOM 위에서 node로 실제 실행한다
(plans/201 W3 `test_plan201_w3_frontend.py` 방식). `#chatWelcome`·`<template>`은 **실제 index.html 마크업**을
파싱해 만든다 — 사본을 두지 않는다.

  1. 플래그 off(응답에 키 없음) → `#chatWelcome`을 전혀 건드리지 않는다(직렬화 비트 동일).
  2. 상황 안내 — 3줄 · 예시 버튼 줄 위 · 알람 탭이 없는 사용자는 줄을 숨기지 않고 비활성 + 사유.
  3. 예시 질의 — 현행 버튼 교체 · 「❓ 사용법」 끝 유지 · 제목에 영역 이름 · 클릭은 현행 처리기 · 빈 목록은 현행 유지.
  4. XSS — 서버 문구는 textContent로만(HTML 문자열 삽입 0).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_APP_JS = (_ROOT / "src" / "static" / "js" / "app.js").read_text(encoding="utf-8")
_INDEX = (_ROOT / "src" / "static" / "index.html").read_text(encoding="utf-8")
_START = "// ─── plans/201 W2 첫 화면 시작 ───"
_END = "// ─── plans/201 W2 첫 화면 끝 ───"


def _region() -> str:
    return _APP_JS[_APP_JS.index(_START):_APP_JS.index(_END)]


class _Tree(HTMLParser):
    """HTML 조각 → `{tag, attrs, text, children}` 트리(공백 텍스트 제외 · 주석 무시)."""

    _VOID = {"br", "img", "input", "hr", "meta", "link", "polyline", "line", "path"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root: dict = {"tag": "root", "attrs": {}, "text": "", "children": []}
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = {"tag": tag, "attrs": {k: (v if v is not None else "") for k, v in attrs},
                "text": "", "children": []}
        self.stack[-1]["children"].append(node)
        if tag not in self._VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self._VOID:
            self.stack.pop()

    def handle_endtag(self, tag):
        if tag in self._VOID:
            return
        while len(self.stack) > 1:
            node = self.stack.pop()
            if node["tag"] == tag:
                break

    def handle_data(self, data):
        if data.strip():
            self.stack[-1]["children"].append({"text_node": re.sub(r"\s+", " ", data.strip())})


def _parse(fragment: str) -> dict:
    t = _Tree()
    t.feed(fragment)
    return t.root["children"][0]


def _chat_welcome_html() -> str:
    start = _INDEX.index('<div class="chat-welcome" id="chatWelcome">')
    end = _INDEX.index("<!-- DB connection warning banner -->")
    block = _INDEX[start:end]
    return block[:block.index("<template")] if "<template" in block else block


def _template_html() -> str:
    m = re.search(r'<template id="welcomeGuideTemplate">(.*?)</template>', _INDEX, re.S)
    assert m, "index.html에 welcomeGuideTemplate이 없다"
    return m.group(1)


_FAKE_DOM = r"""
var LOG = { innerHTML: 0, sent: 0, views: [], focus: 0, fileClicks: 0 };
function El(tag) {
    var self = this;
    this.tagName = String(tag).toUpperCase();
    this.attrs = {}; this.children = []; this.parentNode = null; this.listeners = {};
    this.disabled = false; this.type = ""; this.title = ""; this._text = "";
    this.dataset = {};
    this.classList = {
        contains: function (c) { return (self.attrs["class"] || "").split(/\s+/).indexOf(c) >= 0; },
        add: function (c) { if (!self.classList.contains(c)) self.attrs["class"] = ((self.attrs["class"] || "") + " " + c).trim(); },
        remove: function (c) { self.attrs["class"] = (self.attrs["class"] || "").split(/\s+/).filter(function (x) { return x && x !== c; }).join(" "); },
        toggle: function (c, on) { if (on === undefined) on = !self.classList.contains(c); if (on) self.classList.add(c); else self.classList.remove(c); return on; },
    };
}
Object.defineProperty(El.prototype, "className", {
    get: function () { return this.attrs["class"] || ""; }, set: function (v) { this.attrs["class"] = String(v); } });
Object.defineProperty(El.prototype, "id", { get: function () { return this.attrs.id || ""; } });
Object.defineProperty(El.prototype, "innerHTML", {
    get: function () { return ""; }, set: function () { LOG.innerHTML += 1; } });
Object.defineProperty(El.prototype, "textContent", {
    get: function () {
        if (this.tagName === "#TEXT") return this._text;
        return this.children.map(function (c) { return c.textContent; }).join("");
    },
    set: function (v) { this.children = []; var t = new El("#text"); t._text = String(v); t.parentNode = this; this.children.push(t); } });
El.prototype.getAttribute = function (k) { return Object.prototype.hasOwnProperty.call(this.attrs, k) ? this.attrs[k] : null; };
El.prototype.setAttribute = function (k, v) { this.attrs[k] = String(v); };
El.prototype.appendChild = function (n) { return this.insertBefore(n, null); };
El.prototype.insertBefore = function (n, ref) {
    if (n.isFrag) { var self = this; n.children.slice().forEach(function (c) { self.insertBefore(c, ref); }); n.children = []; return n; }
    if (n.parentNode) n.parentNode.children.splice(n.parentNode.children.indexOf(n), 1);
    n.parentNode = this;
    var at = ref ? this.children.indexOf(ref) : -1;
    if (at < 0) this.children.push(n); else this.children.splice(at, 0, n);
    return n;
};
El.prototype.remove = function () { if (this.parentNode) { this.parentNode.children.splice(this.parentNode.children.indexOf(this), 1); this.parentNode = null; } };
El.prototype.addEventListener = function (t, f) { (this.listeners[t] = this.listeners[t] || []).push(f); };
El.prototype.click = function () { if (this.disabled) return; var s = this; (this.listeners.click || []).forEach(function (f) { f({ target: s }); }); };
function matches(el, sel) {
    if (el.tagName === "#TEXT") return false;
    if (sel[0] === ".") return el.classList.contains(sel.slice(1));
    if (sel[0] === "#") return el.attrs.id === sel.slice(1);
    var m = /^\[([\w-]+)\]$/.exec(sel); if (m) return Object.prototype.hasOwnProperty.call(el.attrs, m[1]);
    return el.tagName === sel.toUpperCase();
}
El.prototype.querySelectorAll = function (sel) {
    var out = [];
    (function walk(n) { n.children.forEach(function (c) { if (matches(c, sel)) out.push(c); walk(c); }); })(this);
    return out;
};
El.prototype.querySelector = function (sel) { return this.querySelectorAll(sel)[0] || null; };
El.prototype.cloneNode = function (deep) {
    var c = new El(this.tagName === "#TEXT" ? "#text" : this.tagName);
    c.attrs = JSON.parse(JSON.stringify(this.attrs)); c._text = this._text; c.isFrag = this.isFrag;
    if (Object.prototype.hasOwnProperty.call(this.attrs, "disabled")) c.disabled = true;
    if (deep) this.children.forEach(function (k) { var kk = k.cloneNode(true); kk.parentNode = c; c.children.push(kk); });
    return c;
};
function build(spec) {
    if (spec.text_node !== undefined) { var t = new El("#text"); t._text = spec.text_node; return t; }
    var el = new El(spec.tag);
    Object.keys(spec.attrs).forEach(function (k) { el.attrs[k] = spec.attrs[k]; });
    if (Object.prototype.hasOwnProperty.call(spec.attrs, "disabled")) el.disabled = true;
    Object.keys(spec.attrs).forEach(function (k) {
        if (k.indexOf("data-") === 0) el.dataset[k.slice(5).replace(/-([a-z])/g, function (_, x) { return x.toUpperCase(); })] = spec.attrs[k];
    });
    spec.children.forEach(function (c) { var k = build(c); k.parentNode = el; el.children.push(k); });
    return el;
}
function serialize(n) {
    if (n.tagName === "#TEXT") return n._text;
    var a = Object.keys(n.attrs).sort().map(function (k) { return k + "=" + JSON.stringify(n.attrs[k]); }).join(" ");
    return "<" + n.tagName + (a ? " " + a : "") + (n.disabled ? " [disabled]" : "") + ">" +
        n.children.map(serialize).join("") + "</" + n.tagName + ">";
}
var BODY = new El("body");
var chatWelcome = build(WELCOME_SPEC);
BODY.appendChild(chatWelcome);
var TEMPLATE = new El("template");
TEMPLATE.attrs.id = "welcomeGuideTemplate";
TEMPLATE.content = build(TEMPLATE_SPEC_ROOT); TEMPLATE.content.isFrag = true;
var document = {
    getElementById: function (id) {
        if (id === "welcomeGuideTemplate") return TEMPLATE;
        return BODY.querySelector("#" + id);
    },
    createElement: function (tag) { return new El(tag); },
};
var promptEl = { value: "", focus: function () { LOG.focus += 1; } };
function autoResizeTextarea() {}
function handleSend() { LOG.sent += 1; }
function setActiveView(v) { LOG.views.push(v); }
var fileInput = { click: function () { LOG.fileClicks += 1; } };
var alarmCanReceive = ALARM_CAN_RECEIVE;
var ME = null;
function fetchAuthMeOnce() { return { then: function (f) { f(ME); return this; } }; }
// 현행 첫 화면 버튼은 app.js 초기화가 bindHintButton으로 묶는다(hintButtons.forEach)
"""


def _run(body: str, *, alarm: bool = True) -> object:
    if shutil.which("node") is None:
        pytest.skip("node 미설치")
    welcome = _parse(_chat_welcome_html())
    tpl_root = {"tag": "frag", "attrs": {}, "text": "", "children": [_parse(_template_html())]}
    script = "\n".join([
        f"var WELCOME_SPEC = {json.dumps(welcome, ensure_ascii=False)};",
        f"var TEMPLATE_SPEC_ROOT = {json.dumps(tpl_root, ensure_ascii=False)};",
        f"var ALARM_CAN_RECEIVE = {'true' if alarm else 'false'};",
        _FAKE_DOM,
        _region(),
        "chatWelcome.querySelectorAll('.chat-welcome-hint').forEach(bindHintButton);",
        "var BEFORE = serialize(chatWelcome);",
        body,
    ])
    # 명령줄(-e) 대신 stdin으로 넘긴다 — Windows 명령줄 한도(32K · WinError 206) 회피
    done = subprocess.run(["node", "-"], input=script.encode("utf-8"), capture_output=True, timeout=30, check=False)
    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
    return json.loads(done.stdout.decode("utf-8"))


_HINTS = [
    {"area": "server_os", "area_label": "서버/OS", "label": "알람 많은 서버", "query": "ㅇㅇ존에서 알람 질의"},
    {"area": "server_os", "area_label": "서버/OS", "label": "CPU 상위", "query": "ㅇㅇ존 CPU 질의"},
    {"area": "was_mw", "area_label": "WAS/미들웨어", "label": "WAS 상태", "query": "WAS 질의"},
]

_STATE = r"""
function hintsState() {
    var box = chatWelcome.querySelector(".chat-welcome-hints");
    return box.querySelectorAll(".chat-welcome-hint").map(function (b) {
        return { text: b.textContent.trim(), cls: b.className, query: b.dataset.query || null, help: !!b.dataset.help };
    });
}
function topOrder() {
    return chatWelcome.children.filter(function (c) { return c.tagName !== "#TEXT"; })
        .map(function (c) { return c.className || c.tagName; });
}
"""


# ─── 1. 플래그 off = 비트 동일 ─────────────────────────────────────────────────


@pytest.mark.parametrize("me", [
    None,
    {"user_id": "u", "role": "user"},
    {"user_id": "u", "duty_areas": ["server_os"], "duty_area_options": []},   # W1만 켬
    {"welcome_guide": False},
    {"welcome_guide": "true"},                                                # 정확히 true만
    {"duty_welcome_queries": []},                                             # 미지정 사용자
    {"duty_welcome_queries": [{"label": "", "query": "x"}, {"label": 3, "query": "y"}, "z"]},
    {"duty_welcome_queries": "nope"},
])
def test_off_or_empty_leaves_chat_welcome_untouched(me) -> None:
    out = _run("ME = " + json.dumps(me, ensure_ascii=False) + ";\n" + r"""
loadWelcomeEnhancements();
process.stdout.write(JSON.stringify({ same: serialize(chatWelcome) === BEFORE,
    guide: !!document.getElementById("chatWelcomeGuide"), innerHTML: LOG.innerHTML }));
""")
    assert out == {"same": True, "guide": False, "innerHTML": 0}


# ─── 2. 상황 안내 ─────────────────────────────────────────────────────────────


def test_guide_three_lines_above_hints_and_click_targets() -> None:
    out = _run(_STATE + r"""
ME = { welcome_guide: true };
loadWelcomeEnhancements();
var g = document.getElementById("chatWelcomeGuide");
var items = g.querySelectorAll("[data-guide]");
var texts = items.map(function (b) { return b.textContent; });
items.forEach(function (b) { b.click(); });
loadWelcomeEnhancements();   // 두 번 불러도 한 번만 붙는다
process.stdout.write(JSON.stringify({ order: topOrder(), targets: items.map(function (b) { return b.getAttribute("data-guide"); }),
    title: g.querySelector(".chat-welcome-guide-title").textContent, texts: texts,
    disabled: items.map(function (b) { return b.disabled; }), views: LOG.views, focus: LOG.focus,
    files: LOG.fileClicks, guides: chatWelcome.querySelectorAll(".chat-welcome-guide").length,
    hints: hintsState().length, innerHTML: LOG.innerHTML }));
""")
    assert out["targets"] == ["alarm", "investigate", "report"]
    assert out["title"] == "이 화면에서 할 수 있는 일"
    assert "알람을 받았다면" in out["texts"][0] and "이벤트 알람 탭" in out["texts"][0]
    assert "원인을 찾고 있다면" in out["texts"][1] and "입력창" in out["texts"][1]
    assert "보고 자료가 필요하다면" in out["texts"][2] and "양식" in out["texts"][2]
    order = out["order"]
    assert order.index("chat-welcome-guide") == order.index("chat-welcome-hints") - 1   # 예시 버튼 줄 바로 위
    assert out["disabled"] == [False, False, False]
    assert out["views"] == ["alarm"] and out["focus"] == 1 and out["files"] == 1
    assert out["guides"] == 1 and out["hints"] == 5 and out["innerHTML"] == 0          # 예시 버튼은 현행 그대로


def test_guide_alarm_line_disabled_with_reason_when_no_alarm_tab() -> None:
    out = _run(r"""
ME = { welcome_guide: true };
loadWelcomeEnhancements();
var b = document.getElementById("chatWelcomeGuide").querySelector("[data-guide]");
b.click();
process.stdout.write(JSON.stringify({ shown: !!b.parentNode, disabled: b.disabled, aria: b.getAttribute("aria-disabled"),
    cls: b.className, reason: b.querySelector(".chat-welcome-guide-reason").textContent, views: LOG.views }));
""", alarm=False)
    assert out["shown"] is True and out["disabled"] is True and out["aria"] == "true"   # 숨기지 않는다
    assert "chat-welcome-guide-item--disabled" in out["cls"]
    assert out["reason"].startswith("알람 수신 지역이 지정되지 않았습니다")
    assert out["views"] == []


def test_line_state_is_pure() -> None:
    out = _run(r"""
process.stdout.write(JSON.stringify([welcomeGuideLineState("alarm", { alarm: true }),
    welcomeGuideLineState("alarm", { alarm: false }), welcomeGuideLineState("alarm", null),
    welcomeGuideLineState("investigate", { alarm: false }), welcomeGuideLineState("report", null)]));
""")
    assert [s["disabled"] for s in out] == [False, True, True, False, False]


# ─── 3. 담당별 예시 질의 ──────────────────────────────────────────────────────


def test_duty_hints_replace_buttons_keep_help_last_and_title() -> None:
    out = _run(_STATE + "ME = { duty_welcome_queries: " + json.dumps(_HINTS, ensure_ascii=False) + " };\n" + r"""
loadWelcomeEnhancements();
loadWelcomeEnhancements();   // 재적용 없음
var title = chatWelcome.querySelectorAll(".chat-welcome-hints-title");
var st = hintsState();
var btns = chatWelcome.querySelector(".chat-welcome-hints").querySelectorAll(".chat-welcome-hint");
btns[0].click();
var afterFirst = { value: promptEl.value, sent: LOG.sent };
btns[btns.length - 1].click();
process.stdout.write(JSON.stringify({ titles: title.map(function (t) { return t.textContent; }), order: topOrder(), st: st,
    afterFirst: afterFirst, sentAfterHelp: LOG.sent, titleAttr: btns[0].title, innerHTML: LOG.innerHTML }));
""")
    assert out["titles"] == ["이렇게 물어보세요 · 서버/OS · WAS/미들웨어 담당 추천"]
    order = out["order"]
    assert order.index("chat-welcome-hints-title") == order.index("chat-welcome-hints") - 1
    st = out["st"]
    assert [b["text"] for b in st] == ["알람 많은 서버", "CPU 상위", "WAS 상태", "❓ 사용법"]
    assert all("chat-welcome-hint--duty" in b["cls"] for b in st[:3])
    assert st[-1]["help"] is True                                     # 사용법은 늘 끝
    assert [b["query"] for b in st[:3]] == ["ㅇㅇ존에서 알람 질의", "ㅇㅇ존 CPU 질의", "WAS 질의"]
    # 클릭 = 현행 처리기: 입력창에 채우기만(전송 없음 · 「ㅇㅇ존」은 사용자가 고친다) · 사용법만 바로 전송
    assert out["afterFirst"] == {"value": "ㅇㅇ존에서 알람 질의", "sent": 0}
    assert out["sentAfterHelp"] == 1
    assert out["titleAttr"] == "서버/OS 담당 · ㅇㅇ존에서 알람 질의"
    assert out["innerHTML"] == 0


def test_single_area_title() -> None:
    out = _run("ME = { duty_welcome_queries: " + json.dumps(_HINTS[:2], ensure_ascii=False) + " };\n" + r"""
loadWelcomeEnhancements();
process.stdout.write(JSON.stringify(chatWelcome.querySelector(".chat-welcome-hints-title").textContent));
""")
    assert out == "이렇게 물어보세요 · 서버/OS 담당 추천"


def test_guide_and_hints_together_layout() -> None:
    out = _run(_STATE + "ME = { welcome_guide: true, duty_welcome_queries: " + json.dumps(_HINTS, ensure_ascii=False)
               + " };\n" + r"""
loadWelcomeEnhancements();
process.stdout.write(JSON.stringify(topOrder()));
""")
    tail = out[-3:]
    assert tail == ["chat-welcome-guide", "chat-welcome-hints-title", "chat-welcome-hints"]


def test_xss_server_text_goes_to_text_nodes_only() -> None:
    evil = [{"area": "x", "area_label": "<img src=x onerror=alert(1)>", "label": "<b>굵게</b>",
             "query": "\"><script>alert(1)</script>"}]
    out = _run(_STATE + "ME = { duty_welcome_queries: " + json.dumps(evil, ensure_ascii=False) + " };\n" + r"""
loadWelcomeEnhancements();
var b = hintsState()[0];
process.stdout.write(JSON.stringify({ b: b, innerHTML: LOG.innerHTML,
    title: chatWelcome.querySelector(".chat-welcome-hints-title").textContent }));
""")
    assert out["innerHTML"] == 0
    assert out["b"]["text"] == "<b>굵게</b>" and out["b"]["query"] == "\"><script>alert(1)</script>"
    assert "<img src=x onerror=alert(1)>" in out["title"]


# ─── 4. 정적 계약 ─────────────────────────────────────────────────────────────


def test_region_has_no_html_string_injection() -> None:
    region = _region()
    for bad in ("innerHTML", "insertAdjacentHTML", "outerHTML", "document.write"):
        assert bad not in region, bad


def test_no_area_vocabulary_copy_in_frontend_region() -> None:
    import yaml

    body = yaml.safe_load((_ROOT / "config" / "duty_areas.yaml").read_text(encoding="utf-8"))
    region = _region()
    for area in body["areas"]:
        assert area["label"] not in region and f'"{area["code"]}"' not in region, area["code"]
    for entries in body["welcome_queries"].values():
        for e in entries:
            assert e["query"] not in region and e["query"] not in _INDEX


def test_template_outside_chat_welcome_and_not_rendered() -> None:
    welcome = _chat_welcome_html()
    assert "chat-welcome-guide" not in welcome and "chat-welcome-hints-title" not in welcome
    assert "<template" not in welcome
    # 템플릿은 hidden 속성이 아니라 <template> — CSS display 지정에 덮이지 않는다
    assert '<template id="welcomeGuideTemplate">' in _INDEX
    assert 'class="chat-welcome-guide" id="chatWelcomeGuide"' in _template_html()
    # 탭 이름은 화면 용어 그대로(이벤트 알람 탭 버튼 문구)
    assert re.search(r'data-view="alarm"[^>]*>\s*이벤트 알람', _INDEX)


def test_loader_wired_after_alarm_subscription_and_auth_gated() -> None:
    body = _APP_JS[_APP_JS.index("function checkAuthOnLoad()"):]
    body = body[:body.index("// 로그아웃 버튼")]
    assert body.index("initAlarmSubscription(userInfo, data.auth_enabled)") < body.index("loadWelcomeEnhancements()")
    assert "if (data.auth_enabled && userInfo && userInfo.user_id) loadWelcomeEnhancements();" in body
    assert "hintButtons.forEach(bindHintButton);" in _APP_JS


def test_cache_versions() -> None:
    assert 'src="/static/js/app.js?v=56"' in _INDEX
    assert 'href="/static/css/style.css?v=51"' in _INDEX
    css = (_ROOT / "src" / "static" / "css" / "style.css").read_text(encoding="utf-8")
    for cls in (".chat-welcome-guide", ".chat-welcome-guide-item--disabled", ".chat-welcome-guide-reason",
                ".chat-welcome-hints-title"):
        assert cls in css, cls
