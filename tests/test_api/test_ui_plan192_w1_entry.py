"""plans/192 W1 — 포털 진입 결함(P1·P4·P8)과 break-glass 관제 버튼(Q-3 A)을 고정한다.

    - P1: 만료·미로그인 리다이렉트가 현재 경로·쿼리·해시를 `next`로 넘기고, 로그인 화면은
      같은 출처 상대경로만 받는다(`/\\…`·제어문자·스킴 거부).
    - P4·P8: 알람 카드·지난 알람의 조사 대화 진입과 「이전 분석」 행 버튼이 클릭 시점의 장애 진단
      판정(openInvestigationEntry)을 따른다 — 공용 렌더는 `onOpenPrevious`가 없으면
      종전과 비트 동일.
    - Q-3 A: break-glass 관제 서랍에는 진단 켜짐·꺼짐과 무관하게 포털 진입 버튼이 없다.

브라우저 e2e 대신 함수 본문을 잘라 가짜 DOM과 함께 node로 실제 실행한다(node 없으면 생략).
이 파일의 하네스(`app_run` 등)는 W2·W4 테스트가 함께 쓴다.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest

from tests.test_api.test_noise_dashboard_api import _js_function_body

_ROOT = Path(__file__).resolve().parents[2]
_STATIC = _ROOT / "src" / "static"
APP_JS = _STATIC / "js" / "app.js"
NOISE_JS = _STATIC / "js" / "noise.js"
BV_JS = _STATIC / "js" / "briefing-view.js"
DV_JS = _STATIC / "js" / "diagnosis-view.js"
LOGIN_HTML = _STATIC / "login.html"

# ─── 하네스 ─────────────────────────────────────────────────────────────

# app.js는 IIFE 한 덩어리다 — 최상위 함수·상수는 들여쓰기 4칸이다.
_TOP_FN_RE = re.compile(r"^    function ([A-Za-z_$][\w$]*)\(", re.M)
_TOP_CONST_RE = re.compile(r"^    var ([A-Z][A-Z0-9_]*) = [/\"'\d][^\n]*;[^\n]*$", re.M)
_IDENT_RE = re.compile(r"[A-Za-z_$][\w$]*")
_PROVIDED_RE = re.compile(r"\b(?:function|var)\s+([A-Za-z_$][\w$]*)")


def need_node() -> None:
    if shutil.which("node") is None:
        pytest.skip("node 미설치 — JS 실행 테스트 생략")


def body_of(src: str, name: str, where: str) -> str:
    """함수 본문 — 없으면 「구현 대기」로 실패한다(수집 오류가 아니라 테스트 실패)."""
    try:
        return _js_function_body(src, name)
    except ValueError:
        pytest.fail(f"{where}에 function {name}( 가 없다 — plans/192 구현 대기")


def app_defs(funcs: tuple[str, ...], harness: str, script: str = "") -> str:
    """요청 함수 + 그 함수(와 테스트 본문)가 이름으로 부르는 app.js 최상위 함수·리터럴 상수.

    전이 폐포다.

    하네스가 같은 이름을 정의하면(가짜) 그것이 우선한다 — 가짜를 두지 않은 함수만 실물로 끌어온다.
    """
    src = APP_JS.read_text(encoding="utf-8")
    unique = {n for n in _TOP_FN_RE.findall(src) if src.count(f"function {n}(") == 1}
    consts: dict[str, str] = {}
    for m in _TOP_CONST_RE.finditer(src):
        consts.setdefault(m.group(1), m.group(0).strip())
    provided = set(_PROVIDED_RE.findall(harness))
    parts: list[str] = []
    used: list[str] = []
    seen: set[str] = set()
    # 테스트 본문·하네스 가짜(예: setActiveView → swapConversation)가 부르는 실물도 끌어온다
    queue = list(funcs) + [
        n for n in dict.fromkeys(_IDENT_RE.findall(script + harness)) if n in unique
    ]
    while queue:
        name = queue.pop(0)
        if name in seen or name in provided:
            continue
        seen.add(name)
        body = body_of(src, name, "app.js")
        parts.append(body)
        for ident in dict.fromkeys(_IDENT_RE.findall(body)):
            if ident in provided:
                continue
            if ident in unique and ident not in seen:
                queue.append(ident)
            elif ident in consts and ident not in used:
                used.append(ident)
    return "\n".join([consts[c] for c in used] + parts)


def node_run(prelude: str, defs: str, body: str) -> Any:
    """prelude(가짜 전역) + defs(실물 함수) + async 본문 → `out`의 JSON."""
    need_node()
    script = "\n".join(
        [
            '"use strict";',
            prelude,
            defs,
            "var out = null;",
            "function tick() { return new Promise(function (r) { setTimeout(r, 5); }); }",
            "(async function () {",
            body,
            "process.stdout.write(JSON.stringify(out));",
            "})().catch(function (e) { console.error((e && e.stack) || e); process.exit(1); });",
        ]
    )
    done = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=30, check=False
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def dv_text() -> dict[str, str]:
    """diagnosis-view.js `TEXT`의 진입 문구(실물 값)."""
    src = DV_JS.read_text(encoding="utf-8")
    out: dict[str, str] = {}
    for key in ("viewInDiagnosis", "openConversation", "startBtn"):
        m = re.search(rf'^\s+{key}: "([^"]*)",', src, re.M)
        assert m, f"diagnosis-view.js TEXT.{key} 없음"
        out[key] = m.group(1)
    return out


# 가짜 DOM — className 매칭 querySelector(innerHTML 또는 자식에서) · 클릭 리스너 · 조각 이동
DOM_PRELUDE = r"""
var window = globalThis;
function El(tag) {
    var self = this;
    this.tagName = String(tag || "div").toUpperCase();
    this.children = []; this.attrs = {}; this.listeners = {}; this.q = {};
    this.hidden = false; this.disabled = false; this.textContent = ""; this.className = "";
    this.innerHTML = ""; this.style = {}; this.parentNode = null; this.type = "";
    this.classList = {
        contains: function (c) { return (" " + self.className + " ").indexOf(" " + c + " ") >= 0; },
        add: function (c) {
            if (!self.classList.contains(c)) self.className = (self.className + " " + c).trim();
        },
        remove: function (c) {
            self.className = self.className.split(" ")
                .filter(function (x) { return x && x !== c; }).join(" ");
        },
        toggle: function (c, on) {
            if (on === undefined) on = !self.classList.contains(c);
            if (on) self.classList.add(c); else self.classList.remove(c);
            return on;
        },
    };
}
El.prototype.appendChild = function (n) {
    if (n.isFrag) {
        var kids = n.children.slice();
        n.children = [];
        kids.forEach(this.appendChild, this);
        return n;
    }
    if (n.parentNode) n.parentNode.children.splice(n.parentNode.children.indexOf(n), 1);
    n.parentNode = this;
    this.children.push(n);
    return n;
};
El.prototype.insertBefore = function (n) {
    if (n.parentNode) n.parentNode.children.splice(n.parentNode.children.indexOf(n), 1);
    n.parentNode = this;
    this.children.unshift(n);
    return n;
};
El.prototype.remove = function () {
    if (!this.parentNode) return;
    this.parentNode.children.splice(this.parentNode.children.indexOf(this), 1);
    this.parentNode = null;
};
El.prototype.setAttribute = function (k, v) { this.attrs[k] = String(v); };
El.prototype.getAttribute = function (k) {
    return Object.prototype.hasOwnProperty.call(this.attrs, k) ? this.attrs[k] : null;
};
El.prototype.removeAttribute = function (k) { delete this.attrs[k]; };
El.prototype.addEventListener = function (t, f) {
    (this.listeners[t] = this.listeners[t] || []).push(f);
};
El.prototype.click = function () {
    var self = this;
    (this.listeners.click || []).forEach(function (f) {
        f({ target: self, preventDefault: function () {} });
    });
};
El.prototype.findByClass = function (cls) {
    for (var i = 0; i < this.children.length; i++) {
        var c = this.children[i];
        if (c.classList.contains(cls)) return c;
        var deep = c.findByClass(cls);
        if (deep) return deep;
    }
    return null;
};
El.prototype.querySelector = function (sel) {
    if (Object.prototype.hasOwnProperty.call(this.q, sel)) return this.q[sel];
    var m = /^\.([\w-]+)$/.exec(sel);
    if (!m) return null;
    var re = new RegExp('<(\\w+)[^>]*class="(?:[^"]* )?' + m[1] + '(?: [^"]*)?"[^>]*>([^<]*)');
    var hit = re.exec(this.innerHTML || "");
    if (hit) {
        var e = new El(hit[1]);
        e.className = m[1];
        e.textContent = hit[2];
        this.q[sel] = e;
        return e;
    }
    return this.findByClass(m[1]);
};
El.prototype.querySelectorAll = function () { return []; };
var document = {
    createElement: function (t) { return new El(t); },
    createDocumentFragment: function () { var f = new El("#fragment"); f.isFrag = true; return f; },
    getElementById: function () { return null; },
    querySelector: function () { return null; },
};
"""

# 장애 진단·조사 대화 목적지 기록 + 공용 렌더 가짜(attach에 넘긴 opts를 붙잡는다)
ENTRY_PRELUDE = r"""
var diagOn = false;
var toDiagnosis = [];
var toConversation = [];
var attached = [];
window.DiagnosisView = {
    TEXT: DV_TEXT,
    isEnabled: function () { return diagOn; },
    openIfEnabled: function (id, o) {
        if (!diagOn) return false;
        toDiagnosis.push([id, (o && o.alarmId) || null]);
        return true;
    },
};
window.BriefingView = {
    attach: function (container, opts) {
        var p = { opts: opts, container: container, opened: 0, closed: 0,
            open: function () { this.opened += 1; }, close: function () { this.closed += 1; } };
        attached.push(p);
        return p;
    },
    hasPlaybook: function () { return false; },
    reasonOf: function () { return ""; },
    playbookScope: function () { return null; },
};
function dest() {
    var d = { diagnosis: toDiagnosis.slice(), conversation: toConversation.slice() };
    toDiagnosis.length = 0;
    toConversation.length = 0;
    return d;
}
function labelOf(l) { return typeof l === "function" ? l() : (l === undefined ? null : l); }
var isProcessing = false;
var alarmCapabilities = null;
var alarmOperator = false;
var investigationAlarmIds = {};
var alarmPastPanels = [];
function getAuthHeaders() { return {}; }
function analyzeOptions(alarmId) { return { alarmId: alarmId }; }
function manualOnlyMode() { return false; }
function alarmQueueDisplay() { return null; }
function rememberAlarmOf() {}
function bvRequest() { return Promise.resolve({}); }
function loadInvestigationView() { return Promise.resolve({}); }
function renderPinTarget() {}
function applyInvestigationScope() {}
function showError() {}
"""


def entry_prelude(extra: str = "") -> str:
    return (
        DOM_PRELUDE
        + f"var DV_TEXT = {json.dumps(dv_text(), ensure_ascii=False)};\n"
        + ENTRY_PRELUDE
        + extra
    )


def app_run(funcs: tuple[str, ...], body: str, prelude: str | None = None) -> Any:
    """app.js 함수(+전이 의존)를 가짜 진입 전역 위에서 실행한다."""
    pre = entry_prelude() if prelude is None else prelude
    return node_run(pre, app_defs(funcs, pre, body), body)


# 가짜 openInvestigationConversation — 목적지만 기록(W4 테스트는 실물을 쓴다)
FAKE_CONV = r"""
function openInvestigationConversation(id) { toConversation.push(String(id)); return true; }
"""

# ─── P1 · 로그인 복귀 경로 ───────────────────────────────────────────────

_LOGIN_PRELUDE = r"""
var store = { user_token: "tok", user_info: "info", keep: "x" };
var localStorage = {
    getItem: function (k) {
        return Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null;
    },
    setItem: function (k, v) { store[k] = String(v); },
    removeItem: function (k) { delete store[k]; },
};
var window = globalThis;
var navigated = [];
window.location = {
    pathname: "/", search: "", hash: "",
    assign: function (u) { navigated.push(u); },
    replace: function (u) { navigated.push(u); },
};
Object.defineProperty(window.location, "href", {
    get: function () { return this.pathname + this.search + this.hash; },
    set: function (u) { navigated.push(u); },
});
"""


def _redirect(pathname: str, search: str, hash_: str) -> dict:
    src = APP_JS.read_text(encoding="utf-8")
    return node_run(
        _LOGIN_PRELUDE,
        body_of(src, "redirectToLogin", "app.js"),
        f"""
window.location.pathname = {json.dumps(pathname)};
window.location.search = {json.dumps(search)};
window.location.hash = {json.dumps(hash_)};
redirectToLogin();
out = {{ navigated: navigated, store: store }};
""",
    )


def test_redirect_to_login_from_bare_root_is_unchanged() -> None:
    # 루트 · 쿼리·해시 없음 — 종전처럼 `/login`(비트 동일) · 토큰 2키만 지운다
    out = _redirect("/", "", "")
    assert out["navigated"] == ["/login"]
    assert out["store"] == {"keep": "x"}


@pytest.mark.parametrize(
    ("pathname", "search", "hash_", "expected"),
    [
        ("/", "?diagnosis=inv-1", "", "/login?next=%2F%3Fdiagnosis%3Dinv-1"),
        ("/", "?investigation=inv-2", "", "/login?next=" + quote("/?investigation=inv-2", safe="")),
        (
            "/",
            "?conversation=a&x=1",
            "#h",
            "/login?next=" + quote("/?conversation=a&x=1#h", safe=""),
        ),
        ("/", "", "#frag", "/login?next=" + quote("/#frag", safe="")),
        ("/noise", "", "", "/login?next=%2Fnoise"),
    ],
)
def test_redirect_to_login_carries_current_location_as_next(
    pathname: str, search: str, hash_: str, expected: str
) -> None:
    out = _redirect(pathname, search, hash_)
    assert out["navigated"] == [expected]
    assert out["store"] == {"keep": "x"}


@pytest.mark.parametrize("pathname", ["//evil.com", "/\\evil.com"])
def test_redirect_to_login_drops_non_relative_next(pathname: str) -> None:
    # 둘째 글자가 `/`·`\`면 다른 출처로 해석될 수 있다 — next 없이 `/login`
    out = _redirect(pathname, "?diagnosis=inv-1", "")
    assert out["navigated"] == ["/login"]


def test_all_reauth_paths_use_redirect_to_login_and_logout_has_no_next() -> None:
    src = APP_JS.read_text(encoding="utf-8")
    redirect = body_of(src, "redirectToLogin", "app.js")
    # 401·미인증 6곳(checkAuthOnLoad 2 · 조회 401 4)은 모두 공용 함수를 부른다
    assert src.count("redirectToLogin();") >= 6
    rest = src.replace(redirect, "")
    direct = [m.start() for m in re.finditer(r"[\"']/login", rest)]
    # 직접 `/login` 대입은 명시 로그아웃 1곳뿐이고 그 경로는 next를 붙이지 않는다
    assert len(direct) == 1, "redirectToLogin 밖의 /login 직접 이동이 로그아웃 말고도 있다"
    logout = rest[rest.index('logoutBtn.addEventListener("click"') : direct[0] + 40]
    assert 'window.location.href = "/login";' in logout


def _safe_next(search: str) -> str:
    html = LOGIN_HTML.read_text(encoding="utf-8")
    return node_run(
        "var window = globalThis;\nwindow.location = { search: " + json.dumps(search) + " };",
        body_of(html, "safeNext", "login.html"),
        "out = safeNext();",
    )


@pytest.mark.parametrize("value", ["/?investigation=x", "/noise", "/?diagnosis=inv-1#h", "/"])
def test_login_safe_next_accepts_same_origin_relative(value: str) -> None:
    assert _safe_next("?next=" + quote(value, safe="")) == value


@pytest.mark.parametrize(
    "value",
    [
        "//evil.com",
        "/\\evil.com",
        "/\t/evil.com",
        "/\n/x",
        "/ok\x7f",
        "/a\\b",
        "https://evil.com",
        "javascript:alert(1)",
        "",
    ],
)
def test_login_safe_next_rejects_other_origin_or_control_chars(value: str) -> None:
    # `\`는 브라우저가 `/`로 바꾸고 탭·개행은 URL 파서가 지운다 — 둘 다 `//evil.com`이 된다
    assert _safe_next("?next=" + quote(value, safe="")) == "/"


def test_login_safe_next_without_param_is_root() -> None:
    assert _safe_next("") == "/"


@pytest.mark.parametrize("search", ["?diagnosis=inv-1", "?conversation=a&x=1"])
def test_redirect_next_round_trips_through_login(search: str) -> None:
    # 리다이렉트가 만든 next를 로그인 화면이 그대로 돌려준다(수용 기준 1의 단위 판독)
    target = _redirect("/", search, "")["navigated"][0]
    assert target.startswith("/login?")
    assert _safe_next(target[len("/login") :]) == "/" + search


# ─── P4 · 알람 카드·지난 알람의 조사 대화 진입 ─────────────────────────────

_ALARM_EL = r"""
function alarmEl() {
    var el = new El("div");
    var section = new El("section");
    section.className = "alarm-briefing-section alarm-briefing-section--collapsed";
    section.q[".alarm-briefing-toggle"] = new El("button");
    section.q[".alarm-briefing-body"] = new El("div");
    el.q[".alarm-briefing-section"] = section;
    return el;
}
"""


def _alarm_opts(script: str) -> Any:
    return app_run(("bindAlarmBriefing",), script, entry_prelude(FAKE_CONV + _ALARM_EL))


def _past_opts(script: str) -> Any:
    return app_run(("buildPastAlarmCard",), script, entry_prelude(FAKE_CONV))


def test_alarm_card_open_conversation_follows_diagnosis_at_click() -> None:
    # 진단 판정 전에 그려져 「조사 대화로 열기」가 남은 카드 — 클릭 시점에 켜져 있으면 진단 하위 탭
    out = _alarm_opts(
        r"""
bindAlarmBriefing(alarmEl(), { alarm_id: "a-1", investigation_id: "inv-1" });
var o = attached[0].opts;
o.onOpenConversation("inv-1");
var off = dest();
diagOn = true;
o.onOpenConversation("inv-1");
out = { off: off, on: dest() };
"""
    )
    assert out["off"] == {"diagnosis": [], "conversation": ["inv-1"]}
    assert out["on"] == {"diagnosis": [["inv-1", "a-1"]], "conversation": []}


def test_past_alarm_card_open_conversation_follows_diagnosis_at_click() -> None:
    out = _past_opts(
        r"""
buildPastAlarmCard(new El("div"), { alarm_id: "a-3", tier: "T1" });
var o = attached[0].opts;
o.onOpenConversation("inv-3");
var off = dest();
diagOn = true;
o.onOpenConversation("inv-3");
out = { off: off, on: dest() };
"""
    )
    assert out["off"] == {"diagnosis": [], "conversation": ["inv-3"]}
    assert out["on"] == {"diagnosis": [["inv-3", "a-3"]], "conversation": []}


def test_alarm_entry_sources_do_not_wire_conversation_directly() -> None:
    src = APP_JS.read_text(encoding="utf-8")
    for name in ("bindAlarmBriefing", "buildPastAlarmCard"):
        assert "onOpenConversation: openInvestigationConversation" not in body_of(
            src, name, "app.js"
        ), name
    # 조사 대화 고정 카드(분석 칸 「새 조사로 조사 대화 열기」)와 진단 종결 화면(E7)은
    # 조사 대화 그대로
    assert "onOpenConversation: openInvestigationConversation" in body_of(
        src, "renderInvestigationPin", "app.js"
    )
    assert "onOpenConversation: openInvestigationConversation" in body_of(
        src, "initDiagnosisView", "app.js"
    )


# ─── P8 · 「이전 분석」 행 버튼 ─────────────────────────────────────────────

_PREV = [
    {
        "investigation_id": "inv-old",
        "kind": "alarm",
        "status": "done",
        "submitted_at": "2026-10-18T00:00:00+00:00",
    },
    {"investigation_id": "inv-older", "kind": "manual", "status": "failed"},
]
_PLAIN_VIEW = {
    "status": "done",
    "investigation_id": "inv-cur",
    "briefing_lines": ["요약"],
    "previous": _PREV,
}
_PB_VIEW = {
    "status": "done",
    "investigation_id": "inv-cur",
    "kind": "alarm",
    "briefing": {"playbook": {"sections": []}},
    "previous": _PREV,
}

_BV_HARNESS = r"""
var handler = null;
var container = { innerHTML: "", isConnected: true,
    addEventListener: function (t, f) { handler = f; },
    contains: function () { return true; }, querySelectorAll: function () { return []; },
    querySelector: function () { return null; } };
function click(attrs) {
    var el = { disabled: false,
        getAttribute: function (k) { return attrs[k] === undefined ? null : attrs[k]; } };
    handler({ target: { closest: function () { return el; } }, preventDefault: function () {} });
}
function prevBlock(html) {
    var at = html.indexOf('data-bv-key="analyze-prev"');
    return at < 0 ? "" : html.slice(at, html.indexOf("</details>", at));
}
function count(html, text) { return html.split(text).length - 1; }
function buttons(html) { return html.match(/<button[^>]*>[^<]*<\/button>/g) || []; }
var opened = { previous: [], conversation: [] };
function analyzeOpts() {
    return { follow: false, submit: function () { return {}; },
        loadById: function () { return view; } };
}
"""


def _bv(script: str, **values: Any) -> Any:
    need_node()
    head = "".join(f"var {k} = {json.dumps(v, ensure_ascii=False)};\n" for k, v in values.items())
    return node_run(
        f"var BV = require({json.dumps(str(BV_JS))});\n" + head + _BV_HARNESS, "", script
    )


@pytest.mark.parametrize(
    ("view", "render"),
    [
        (_PB_VIEW, {"mode": "summary", "openConversation": True}),
        (_PB_VIEW, {"mode": "summary", "openConversation": False}),
        (_PB_VIEW, {"mode": "detail"}),
        (_PLAIN_VIEW, {"mode": "summary", "openConversation": False}),
    ],
    ids=["summary-open", "summary-closed", "detail", "plain"],
)
def test_previous_rows_get_open_previous_regardless_of_mode(view: dict, render: dict) -> None:
    out = _bv(
        r"""
var panel = BV.attach(container, { initial: view, load: function () { return view; },
    render: render, analyze: analyzeOpts(),
    onOpenConversation: function (id) { opened.conversation.push(id); },
    onOpenPrevious: function (id) { opened.previous.push(id); },
    openPreviousLabel: function () { return "장애 진단에서 보기"; } });
await panel.open();
var block = prevBlock(container.innerHTML);
click({ "data-bv-action": "open-previous", "data-investigation-id": "inv-old" });
out = { listed: block.indexOf("inv-older") >= 0,
        prev: count(block, 'data-bv-action="open-previous"'),
        row: buttons(block).some(function (b) {
            return b.indexOf('data-bv-action="open-previous"') >= 0
                && b.indexOf('data-investigation-id="inv-old"') >= 0
                && />장애 진단에서 보기<\/button>$/.test(b);
        }),
        conv: count(block, 'data-bv-action="open-conversation"'),
        opened: opened };
panel.close();
""",
        view=view,
        render=render,
    )
    assert out["listed"]
    assert out["prev"] == 2 and out["row"]
    assert out["conv"] == 0  # 행 버튼은 open-previous 하나로 대체된다
    assert out["opened"] == {"previous": ["inv-old"], "conversation": []}


def test_previous_label_defaults_to_open_conversation_and_is_escaped() -> None:
    out = _bv(
        r"""
var a = BV.attach(container, { initial: view, load: function () { return view; },
    render: { mode: "detail" }, analyze: analyzeOpts(),
    onOpenPrevious: function () {} });
await a.open();
var dflt = prevBlock(container.innerHTML);
a.close();
var b = BV.attach(container, { initial: view, load: function () { return view; },
    render: { mode: "detail" }, analyze: analyzeOpts(),
    onOpenPrevious: function () {}, openPreviousLabel: "<b>열기</b>" });
await b.open();
var esc = prevBlock(container.innerHTML);
b.close();
out = { dflt: count(dflt, ">" + BV.PB_TEXT.openConversation + "</button>"),
        esc: esc.indexOf("&lt;b&gt;열기&lt;/b&gt;") >= 0 && esc.indexOf("<b>열기</b>") < 0 };
""",
        view=_PB_VIEW,
    )
    assert out == {"dflt": 2, "esc": True}


@pytest.mark.parametrize(
    ("view", "render"),
    [
        (_PB_VIEW, {"mode": "summary", "openConversation": True}),
        (_PB_VIEW, {"mode": "summary", "openConversation": False}),
        (_PB_VIEW, {"mode": "detail"}),
        (_PLAIN_VIEW, {"mode": "summary", "openConversation": True}),
    ],
    ids=["summary-open", "summary-closed", "detail", "plain"],
)
def test_without_on_open_previous_markup_is_unchanged(view: dict, render: dict) -> None:
    # 관제(noise.js)·진단 뷰는 새 키를 넘기지 않는다 — 렌더가 종전 renderBody와 같아야 한다
    out = _bv(
        r"""
var panel = BV.attach(container, { initial: view, load: function () { return view; },
    render: render, analyze: analyzeOpts(), onOpenConversation: function () {} });
await panel.open();
var got = container.innerHTML;
var extra = {};
Object.keys(render).forEach(function (k) { extra[k] = render[k]; });
extra.engineChoice = false;
extra.ui = { choice: null, choiceMsg: "", choiceOff: "", pending: false,
             analyze: { enabled: true, previous: view.previous } };
extra.analyze = true;
var want = BV.renderBody(view, extra);
var block = prevBlock(got);
out = { same: got === want, prev: count(got, "open-previous"),
        conv: count(block, 'data-bv-action="open-conversation"') };
panel.close();
""",
        view=view,
        render=render,
    )
    assert out["same"] and out["prev"] == 0
    assert out["conv"] == (2 if render.get("openConversation") else 0)


def test_noise_and_diagnosis_view_do_not_pass_on_open_previous() -> None:
    for path in (NOISE_JS, DV_JS):
        assert "onOpenPrevious" not in path.read_text(encoding="utf-8"), path.name


@pytest.mark.parametrize(
    ("runner", "script"),
    [
        ("alarm", 'bindAlarmBriefing(alarmEl(), { alarm_id: "a-1", investigation_id: "inv-1" });'),
        ("past", 'buildPastAlarmCard(new El("div"), { alarm_id: "a-1", tier: "T1" });'),
    ],
)
def test_app_alarm_hosts_pass_on_open_previous_to_entry(runner: str, script: str) -> None:
    body = (
        script
        + r"""
var o = attached[0].opts;
if (typeof o.onOpenPrevious !== "function") throw new Error("onOpenPrevious 미배선");
var offLabel = labelOf(o.openPreviousLabel);
o.onOpenPrevious("inv-old");
var off = dest();
diagOn = true;
var onLabel = labelOf(o.openPreviousLabel);
o.onOpenPrevious("inv-old");
out = { offLabel: offLabel, off: off, onLabel: onLabel, on: dest() };
"""
    )
    out = _alarm_opts(body) if runner == "alarm" else _past_opts(body)
    text = dv_text()
    assert out["offLabel"] == "조사 대화로 열기"
    assert out["off"] == {"diagnosis": [], "conversation": ["inv-old"]}
    assert out["onLabel"] == text["viewInDiagnosis"]
    assert out["on"] == {"diagnosis": [["inv-old", "a-1"]], "conversation": []}


# ─── 조사 대화 고정 카드(상세형) ─────────────────────────────────────────

PIN_PRELUDE = r"""
var chatMessages = new El("div");
var chatWelcome = new El("div");
var investigationPinPanel = null;
function pinOf(id, opts) {
    renderInvestigationPin(id, opts);
    return { el: chatMessages.children[0], opts: attached[attached.length - 1].opts };
}
"""


def pin_run(script: str, extra: str = "") -> Any:
    pre = entry_prelude(FAKE_CONV + PIN_PRELUDE + extra)
    return app_run(("renderInvestigationPin",), script, pre)


def test_pin_previous_rows_follow_diagnosis_and_conversation_stays() -> None:
    out = pin_run(
        r"""
investigationAlarmIds["inv-1"] = "a-9";
var p = pinOf("inv-1", { restored: false });
if (typeof p.opts.onOpenPrevious !== "function") throw new Error("onOpenPrevious 미배선");
var offLabel = labelOf(p.opts.openPreviousLabel);
p.opts.onOpenPrevious("inv-old");
var off = dest();
diagOn = true;
var onLabel = labelOf(p.opts.openPreviousLabel);
p.opts.onOpenPrevious("inv-old");
var on = dest();
p.opts.onOpenConversation("inv-new");   // 분석 칸 「새 조사로 조사 대화 열기」 — 켜짐에도 조사 대화
out = { mode: p.opts.render.mode, offLabel: offLabel, off: off, onLabel: onLabel, on: on,
        conv: dest() };
"""
    )
    assert out["mode"] == "detail"
    assert out["offLabel"] == "조사 대화로 열기"
    assert out["off"] == {"diagnosis": [], "conversation": ["inv-old"]}
    assert out["onLabel"] == dv_text()["viewInDiagnosis"]
    assert out["on"] == {"diagnosis": [["inv-old", "a-9"]], "conversation": []}
    assert out["conv"] == {"diagnosis": [], "conversation": ["inv-new"]}


# ─── Q-3 A · break-glass 관제 서랍 ─────────────────────────────────────────

_NOISE_PRELUDE = r"""
var document = { createElement: function (t) {
    return { tag: t, className: "", textContent: "", type: "", attrs: {},
        setAttribute: function (k, v) { this.attrs[k] = String(v); },
        getAttribute: function (k) { return this.attrs[k] === undefined ? null : this.attrs[k]; },
        addEventListener: function () {} };
} };
var IS_BREAK_GLASS = false;
var liveViewOn = false;
var investigationHead = { kids: [],
    querySelector: function () { return this.kids[0] || null; },
    appendChild: function (n) { this.kids.push(n); return n; } };
function openInPortal() {}
"""


@pytest.mark.parametrize(
    ("break_glass", "live", "expected"),
    [
        (True, True, None),
        (True, False, None),
        (False, True, "장애 진단에서 보기"),
        (False, False, "조사 대화로 열기"),
    ],
    ids=["bg-on", "bg-off", "user-on", "user-off"],
)
def test_portal_entry_button_hidden_for_break_glass(
    break_glass: bool, live: bool, expected: str | None
) -> None:
    src = NOISE_JS.read_text(encoding="utf-8")
    defs = body_of(src, "el", "noise.js") + "\n" + body_of(src, "portalEntryButton", "noise.js")
    out = node_run(
        _NOISE_PRELUDE,
        defs,
        f"""
IS_BREAK_GLASS = {json.dumps(break_glass)};
liveViewOn = {json.dumps(live)};
portalEntryButton({{ investigation_id: "inv-1", status: "done" }});
var b = investigationHead.kids[0];
out = b ? {{ text: b.textContent, id: b.getAttribute("data-investigation-id"),
             n: investigationHead.kids.length }} : null;
""",
    )
    if expected is None:
        assert out is None
    else:
        assert out == {"text": expected, "id": "inv-1", "n": 1}


# break-glass 서랍 — 조사 기록이 있는 판단(renderInvestigation)과 없는 판단(renderAnalyzeOnly) 모두
# 실물 공용 렌더로 그린다. 진행 중 조사(in_flight)·합류 버튼은
# attach `onOpenInvestigation`이 있어야 생긴다.
_DRAWER_PRELUDE = r"""
var window = globalThis;
var BVreal = require(BV_PATH);
var attaches = [];
function container() {
    return { innerHTML: "", isConnected: true, kids: [], h: null,
        appendChild: function (n) { this.kids.push(n); return n; },
        addEventListener: function (t, f) { this.h = f; }, contains: function () { return true; },
        querySelectorAll: function () { return []; }, querySelector: function () { return null; } };
}
var document = { createElement: function (t) {
    var n = container();
    n.tag = t; n.className = ""; n.textContent = "";
    return n;
} };
window.BriefingView = Object.assign({}, BVreal, {
    attach: function (c, o) { attaches.push({ c: c, o: o }); return BVreal.attach(c, o); },
});
var IS_BREAK_GLASS = false;
var liveViewOn = false;
var manualOnlyMode = Promise.resolve(false);
var investigationPanel = null;
var investigationSeq = 0;
var analyzeAsked = false;
var analyzeAvailable = false;
var investigationHead = null;
var drawerCause = container();
var portal = [];
function openInPortal(id) { portal.push(id); }
function stopInvestigation() {
    if (investigationPanel) investigationPanel.close();
    investigationPanel = null;
}
function onInvestigationView() {}
function scrollToCause() {}
var BLOCK = { status: "running", investigation_id: "inv-1", kind: "alarm",
    briefing: { playbook: { sections: [] } } };
var NONE = { status: "none", previous: [] };
function decisionInvestigation() { return Promise.resolve(recordBlock); }
var recordBlock = null;
function reloadInvestigation() { return Promise.resolve(recordBlock || NONE); }
var postRes = null;
function alarmCall(path, method) {
    if (method === "POST") return Promise.resolve(postRes);
    return Promise.resolve({ ok: true, status: 200, body: NONE });
}
"""

_DRAWER_FUNCS = (
    "el",
    "probeAnalyze",
    "analyzeOptions",
    "renderAnalyzeOnly",
    "renderInvestigation",
    "investigationBox",
)


@pytest.mark.parametrize("has_record", [True, False], ids=["record", "analyze-only"])
@pytest.mark.parametrize("live", [True, False], ids=["diag-on", "diag-off"])
def test_break_glass_drawer_has_no_in_flight_or_joined_button(has_record: bool, live: bool) -> None:
    # (사용자 결정 2026-10-26) break-glass 관제는 진행 중 조사·합류로 가는 버튼도 숨긴다 —
    # 서랍의 어떤 attach에도 onOpenInvestigation이 없고 접수(analyze)·수동 모드도 없다
    src = NOISE_JS.read_text(encoding="utf-8")
    defs = "\n".join(body_of(src, n, "noise.js") for n in _DRAWER_FUNCS)

    def run(break_glass: bool) -> Any:
        return node_run(
            f"var BV_PATH = {json.dumps(str(BV_JS))};\n" + _DRAWER_PRELUDE,
            defs,
            f"""
IS_BREAK_GLASS = {json.dumps(break_glass)};
liveViewOn = {json.dumps(live)};
recordBlock = {"BLOCK" if has_record else "null"};
renderInvestigation({{ alarm_id: "a-1" }});
for (var i = 0; i < 4; i++) await tick();
out = attaches.map(function (a) {{
    var html = a.c.innerHTML;
    return {{ go: typeof a.o.onOpenInvestigation === "function", analyze: !!a.o.analyze,
        manual: !!a.o.manualMode, openInv: html.indexOf("open-investigation") >= 0,
        analyzeBtn: html.indexOf('data-bv-action="analyze"') >= 0 }};
}});
if (investigationPanel) investigationPanel.close();
""",
        )

    bg = run(True)
    # 조사 기록이 없으면 break-glass 서랍에는 칸 자체가 없다(접수 칸은 사용자 토큰 전용)
    assert len(bg) == (1 if has_record else 0)
    for a in bg:
        assert a == {
            "go": False,
            "analyze": False,
            "manual": False,
            "openInv": False,
            "analyzeBtn": False,
        }
    # 대조 — 사용자 관제의 접수 칸에는 진행 중 조사·합류 버튼 경로가 있다
    if not has_record:
        user = run(False)
        assert len(user) == 1 and user[0]["go"] and user[0]["analyze"]
